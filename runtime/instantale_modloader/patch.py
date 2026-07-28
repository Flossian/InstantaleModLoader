# -*- coding: utf-8 -*-
"""Nuitka でビルドされたゲームに monkey patch を当てるための API。

Nuitka は Python コードをネイティブコードに変換するが、モジュール・クラス・
関数はどれも通常の Python オブジェクトのまま残る。コンパイル済みのコードも、
モジュールレベルの名前はモジュールの辞書を経由して引く。そのため次の書き換えは
そのまま効く。

    mod.func = 新しい関数        # func(...) と呼んでいる箇所に反映される
    Cls.method = 新しい関数      # クラスも普通の type なので差し替えられる

引っかかるのが `from x import y` の形。この場合、y という名前は import した側の
モジュールに「コピー」されている。だから x.y だけを書き換えても、import した側は
元の関数を持ったままになる。「直したはずなのに呼ばれ続ける」の典型がこれ。

対策として alias_scan（既定で有効）を用意してある。sys.modules を全部なめて、
まだ古いオブジェクトを指している変数を残らず張り替える。

それでも届かないものが1つある。Nuitka が1つの関数の中で解決してしまった呼び出しは、
外から差し替える方法が無い。この場合は、その呼び出しを含む関数ごと差し替えるしかない。

対象の書き方:

    "pkg.mod:func"          モジュールと名前を明示する。基本はこの形を使う
    "pkg.mod:Cls.method"    クラスのメソッド
    "pkg.mod.func"          ":" を省いた形。ロード済みのモジュール名として
                            成立する最長の部分を自動で切り分ける
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable

from . import log, log_exc

# 元に戻すための記録。(ラベル, 持ち主, 属性名, 元の値, 元々存在したか)
_undo: list[tuple[str, Any, str, Any, bool]] = []

# こちらが仕掛けたものに付ける目印。
# 再注入したときに「自分が前回付けたもの」を見分けるために使う。
PATCH_MARK = "__instantale_patch__"
GENERATION_MARK = "__instantale_gen__"
_LEGACY_MARKS = (PATCH_MARK, "__wrapper_of__")

# 今回の注入を表す ID（boot() が設定する）。
# 前回以前の注入で付いたラッパは剥がし、今回の注入で付けたものは残す。
# こうしないと、ファイル名順にわざと重ねた mod
# （例: 101_ で修正を入れ、その上に別の mod で計測を重ねる）が壊れる。
_generation: str | None = None

# まだ import されていなかったモジュールの名前。
# ゲームは LLM 系（llama_cpp_runtime_completion / scripts.llm.llm_manager）を
# 最初のリクエストまで import しないので、起動直後に注入するとこれらが空になる。
# 「対象が無い」のではなく「まだ来ていない」だけなので、ローダ側（__init__.py）が
# ここを見て、モジュールが現れたときに mod を当て直す。
_pending_modules: set[str] = set()


def set_generation(generation: str) -> None:
    global _generation
    _generation = generation
    # 保留は boot ごとに数え直す。前回の注入で保留だったものが今回は載っている、
    # という場合に古い名前が残っていると見張りが終わらない。
    _pending_modules.clear()


def pending_modules() -> list[str]:
    """この boot で「まだ import されていない」として見送った対象のモジュール名。"""
    return sorted(_pending_modules)


def _defer_if_not_imported(target: str, kind: str) -> bool:
    """対象のモジュールが未 import なら保留に積んで True を返す。

    `required=True` でもここでは例外にしない。呼び出し側が悪いのではなく、
    単に順番の問題だからで、モジュールが現れた時点で当て直せば済む。
    「モジュールは在るが属性が無い」は本物の問題なので、こちらは従来どおり
    `required` の指定に従う。
    """
    try:
        mod_name, _qual = split_target(target)
    except LookupError:
        # ":" を省いた書き方で、どこまでがモジュール名か特定できなかった場合。
        # 推測で保留にはしない（従来どおりのエラー経路に流す）。
        return False
    if sys.modules.get(mod_name) is not None:
        return False
    _pending_modules.add(mod_name)
    log("defer {} {} ({} is not imported yet)".format(kind, target, mod_name))
    return True


def unwrap_ours(value: Any) -> Any:
    """前回以前の注入で付けたラッパを剥がして、元の関数を取り出す。

    boot() はローダ自身を sys.modules から消して読み直すので、
    上の _undo は毎回空になる。しかしゲーム側に差し込んだ関数はプロセスに残る。
    この処理が無いと、再注入のたびに前回のラッパをさらに包むことになり、
    層が黙って積み重なって、中身が何度も実行されてしまう。

    ポイントは「他の世代のものだけ」を剥がすこと。
    何も剥がさなければ層が積み上がるし、全部剥がすと同じ注入の中で
    直前に当てた修正まで自分で外してしまう。
    """
    # 32 回で打ち切っているのは、__original__ の連鎖が壊れていた場合に
    # 無限ループしないようにするため。
    for _ in range(32):
        if not any(hasattr(value, mark) for mark in _LEGACY_MARKS):
            return value      # こちらが付けたものではない＝素の関数
        if getattr(value, GENERATION_MARK, None) == _generation:
            return value      # 今回の注入で付けたもの＝残す
        original = getattr(value, "__original__", None)
        if original is None:
            return value
        value = original
    return value


# --------------------------------------------------------------------------
# 対象の解決
# --------------------------------------------------------------------------
def split_target(target: str) -> tuple[str, str]:
    """"pkg.mod:Cls.method" を ("pkg.mod", "Cls.method") に分ける。"""
    if ":" in target:
        mod_name, _, qual = target.partition(":")
        if not mod_name or not qual:
            raise ValueError(f"malformed target: {target!r}")
        return mod_name, qual

    # ":" が無い場合は、ロード済みのモジュール名として成立する最長の部分を探す。
    # "a.b.c" なら a.b.c → a.b → a の順に試す。
    parts = target.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in sys.modules:
            return candidate, ".".join(parts[cut:])
    raise LookupError(
        f"no loaded module prefix in {target!r}; use explicit 'module:attr' syntax")


def resolve(target: str) -> tuple[Any, str, Any]:
    """対象を (持ち主, 属性名, 現在の値) として返す。"""
    mod_name, qual = split_target(target)
    module = sys.modules.get(mod_name)
    if module is None:
        raise LookupError(f"module not loaded: {mod_name}")

    # 最後の名前だけ残して手前を順にたどる。
    # 持ち主はモジュールのこともクラスのこともある。
    parts = qual.split(".")
    owner: Any = module
    for part in parts[:-1]:
        owner = getattr(owner, part)
    name = parts[-1]
    return owner, name, getattr(owner, name, None)


# --------------------------------------------------------------------------
# エイリアスの張り替え
# --------------------------------------------------------------------------
def rebind_aliases(old: Any, new: Any, *, skip: Any = None) -> list[str]:
    """まだ old を指しているモジュール変数を全て new に張り替える。

    `from x import y` でコピーされた名前を拾うための処理。
    属性を1つ書き換えるだけでは、これらは取りこぼす。
    """
    if old is None or old is new:
        return []

    rebound: list[str] = []
    # 走査中に sys.modules が変化することがあるので、list() でコピーしてから回す。
    for mod_name, module in list(sys.modules.items()):
        if module is None or module is skip:
            continue
        try:
            namespace = vars(module)
        except Exception:
            continue      # vars() が使えないモジュール（拡張など）は飛ばす
        try:
            # `is` で比べている。同じ値を持つ別の関数まで巻き込まないため。
            hits = [k for k, v in list(namespace.items()) if v is old]
        except Exception:
            continue
        for key in hits:
            try:
                setattr(module, key, new)
                rebound.append(f"{mod_name}.{key}")
            except Exception:
                pass      # 書き換えを拒む名前空間は諦める
    return rebound


# --------------------------------------------------------------------------
# 公開している API
# --------------------------------------------------------------------------
def set_attr(target: str, value: Any, *, alias_scan: bool = True,
             label: str | None = None) -> Any:
    """対象に値を設定し、元に戻すための記録を残す。低レベルの処理。

    戻り値は「素の元の関数」。前回以前の注入で付いた層は先に剥がしてから返すので、
    パッチは積み重なるのではなく置き換わる。
    """
    owner, name, current = resolve(target)
    original = unwrap_ours(current)
    if original is not current:
        log("  replacing a previous patch layer on {}".format(target))

    # 元々その属性が存在したかどうかも記録しておく。
    # 存在しなかったものは、戻すときに delattr する必要があるため
    # （None を入れて残すのとは意味が違う）。
    existed = hasattr(owner, name)
    _undo.append((label or target, owner, name, original, existed))
    setattr(owner, name, value)

    if alias_scan:
        # 持ち主がモジュール自身なら、今 setattr したばかりなので走査から除く。
        skip = owner if isinstance(owner, type(sys)) else None
        rebound: list[str] = []
        # 他のモジュールが持っているのは、素の関数のことも、前回の注入で
        # 残ったラッパのこともある。取りこぼさないよう両方を張り替える。
        for stale in (current, original):
            if stale is not None and stale is not value:
                rebound += rebind_aliases(stale, value, skip=skip)
        if rebound:
            unique = list(dict.fromkeys(rebound))
            log("  rebound {} alias(es): {}".format(
                len(unique), ", ".join(unique[:8]) + (" ..." if len(unique) > 8 else "")))
    return original


def patch(target: str, *, alias_scan: bool = True, required: bool = True) -> Callable:
    """対象を丸ごと差し替える。

    差し替えた関数には __original__ が付くので、中から元の実装を呼べる。

        @patch("scripts.llm.foo:parse_timings")
        def parse_timings(resp):
            return resp.get("timings", {})

    required=False にすると、対象が見つからなくても警告だけ出して先へ進む。
    ビルドによって存在しない関数を狙うときに使う。
    """
    def decorator(func: Callable) -> Callable:
        if _defer_if_not_imported(target, "patch"):
            return func
        try:
            owner, name, old = resolve(target)
        except (LookupError, AttributeError) as exc:
            if required:
                raise
            log("skip patch {} ({})".format(target, exc), level="WARN")
            return func

        old = unwrap_ours(old)
        try:
            # __name__ や __doc__ を引き継いで、トレースバックの見た目を保つ。
            functools.update_wrapper(func, old)
        except Exception:
            pass
        # 次の3行は update_wrapper の後で設定すること。
        # update_wrapper は元の関数の __dict__ をコピーするので、
        # 先に設定すると上書きされて消える。
        func.__original__ = old
        setattr(func, PATCH_MARK, target)
        setattr(func, GENERATION_MARK, _generation)
        set_attr(target, func, alias_scan=alias_scan)
        log("patched {} ({!r} -> {!r})".format(target, _short(old), _short(func)))
        return func
    return decorator


def wrap(target: str, *, alias_scan: bool = True, required: bool = True) -> Callable:
    """対象を包む。元の関数が第1引数として渡ってくる。

        @wrap("llama_cpp_runtime_completion:start")
        def start(orig, self, *a, **kw):
            for attempt in range(3):
                try:
                    return orig(self, *a, **kw)
                except RuntimeError:
                    ...

    メソッドを対象にする場合、self は元の関数の第1引数として自分で受け取る
    （上の例のとおり orig, self, ... の順になる）。
    """
    def decorator(func: Callable) -> Callable:
        if _defer_if_not_imported(target, "wrap"):
            return func
        try:
            _owner, _name, old = resolve(target)
        except (LookupError, AttributeError) as exc:
            if required:
                raise
            log("skip wrap {} ({})".format(target, exc), level="WARN")
            return func

        if old is None:
            # 名前はあるが中身が None。包む相手が無いので何もしない。
            if required:
                raise LookupError(f"{target} resolved to None")
            log("skip wrap {} (resolved to None)".format(target), level="WARN")
            return func

        # 委譲先は素の関数にする。前回の注入で残ったラッパを呼ぶと二重実行になる。
        old = unwrap_ours(old)

        @functools.wraps(old)
        def wrapper(*args, **kwargs):
            # 元の関数を第1引数に差し込む。これがこの API の中心。
            return func(old, *args, **kwargs)

        wrapper.__original__ = old
        wrapper.__wrapper_of__ = target
        setattr(wrapper, PATCH_MARK, target)
        setattr(wrapper, GENERATION_MARK, _generation)
        set_attr(target, wrapper, alias_scan=alias_scan)
        log("wrapped {} ({!r})".format(target, _short(old)))
        # ゲームに差し込むのは wrapper だが、返すのは func 自身。
        # こうしておくと、デコレートした名前で mod の中から直接呼べる。
        return func
    return decorator


def revert_all() -> int:
    """当てたパッチを全て元に戻す。新しいものから順に。"""
    count = 0
    # 必ず後入れ先出しで戻すこと。
    # 同じ対象に複数の層が乗っている場合、順番を逆にすると
    # 古い層が最後に書き戻されて残ってしまう。
    while _undo:
        label, owner, name, old, existed = _undo.pop()
        try:
            if existed:
                setattr(owner, name, old)
            else:
                delattr(owner, name)
            count += 1
        except Exception:
            log_exc("revert failed: {}".format(label))
    log("reverted {} patch(es)".format(count))
    return count


def active() -> list[str]:
    """今当たっているパッチの一覧。調査用。"""
    return [entry[0] for entry in _undo]


def _short(obj: Any) -> str:
    """ログに出す用の短い名前。Nuitka の関数は repr が長くなるので使わない。"""
    try:
        return getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or type(obj).__name__
    except Exception:
        return "<?>"
