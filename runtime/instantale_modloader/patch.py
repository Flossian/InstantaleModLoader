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

共通のキーワード引数:

    required=True   対象が見つからないとき例外にする（既定）。False なら警告だけ
    safe=False      True にすると、フックの例外をゲームへ流さず元の動作に落とす
    alias_scan=True 張り替える範囲。True＝関係するモジュールだけ / "all"＝全部 /
                    False＝張り替えない（下の _alias_scope を参照）
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable

from . import GAME_TOPLEVEL, log, log_exc
from . import patch_registry as _registry

# 元に戻すための記録。(ラベル, 持ち主, 属性名, 元の値, 元々存在したか)
#
# sys に置いているのは on_ready の印と同じ理由で、**注入し直すとこのモジュール自体が
# 読み込み直される**ため。モジュール変数に持つと、再注入した瞬間に「元の値」の記録だけが
# 消えて revert_all() が何も戻せなくなる（＝前回の注入で当てたパッチが永久に残る）。
# sys はプロセスに1つしか無く、誰かが読み直すこともない。
_UNDO_ATTR = "__instantale_undo__"


def _undo_log() -> list[tuple[str, Any, str, Any, bool]]:
    entries = getattr(sys, _UNDO_ATTR, None)
    if not isinstance(entries, list):
        entries = []
        setattr(sys, _UNDO_ATTR, entries)
    return entries

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
    # 台帳も同じ寿命。世代が変われば前回の帰属は無効になる。
    _registry.reset()


def pending_modules() -> list[str]:
    """この boot で「まだ import されていない」として見送った対象のモジュール名。"""
    return sorted(_pending_modules)


def _defer_if_not_imported(target: str, kind: str) -> bool:
    """対象のモジュールが未 import なら保留に積んで True を返す。

    `required=True` でもここでは例外にしない。呼び出し側が悪いのではなく、
    単に順番の問題だからで、モジュールが現れた時点で当て直せば済む。
    「モジュールは在るが属性が無い」は本物の問題なので、こちらは
    `required` の指定に従う。
    """
    try:
        mod_name, _qual = split_target(target)
    except LookupError:
        # ":" を省いた書き方で、どこまでがモジュール名か特定できなかった場合。
        # 推測で保留にはしない（通常のエラー経路に流す）。
        return False
    if sys.modules.get(mod_name) is not None:
        return False
    _pending_modules.add(mod_name)
    _registry.record(_registry.DEFERRED, target, detail=mod_name)
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


def unwrap(func, *, limit: int = 32):
    """包む前の**素の関数**まで `__original__` をたどる。

        raw, depth, still_ours = unwrap(functions.get_npc_employ_price)

    | 戻り値 | |
    |---|---|
    | `raw` | 最下層の関数 |
    | `depth` | 剥がした段数（0 なら最初から素） |
    | `still_ours` | **まだローダの印が残っているか。** 真なら底に着いていない |

    MOD が「本体がまだ壊れているか」を測るときに要る。包まれた関数を総当たり
    しても確かめられるのは「自分の修正が効いていること」だけで、**修正が
    もう要らないかは分からない**（`101_` / `123_` / `214_` が同じ理由で剥がす）。

    **1段だけ剥がして済ませないこと。** 同じ対象を2つ以上の MOD が包むのは
    このローダでは正常な使い方（TECH.md §3.7）なので、層は1枚とは限らない。
    `still_ours` を見るのは、底に着いたかを推論で決めないため ― ローダは
    自分が被せたものに必ず印を付ける。

    `limit` は連鎖が壊れていたときに止まるための上限で、剥がし切れなければ
    `still_ours` が真のまま返る（黙って底だと言わない）。
    """
    depth = 0
    for _ in range(limit):
        deeper = getattr(func, "__original__", None)
        if deeper is None:
            break
        func, depth = deeper, depth + 1
    still_ours = any(getattr(func, mark, None) is not None
                     for mark in _LEGACY_MARKS)
    return func, depth, still_ours


def original_of(func, *, limit: int = 32):
    """`unwrap()` の関数だけを返す短い形。"""
    return unwrap(func, limit=limit)[0]


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
def _alias_scope(target: str, mode: Any) -> set[str] | None:
    """張り替えを探しに行くトップレベルパッケージ。None なら全モジュール。

    既定（`alias_scan=True`）は**ゲーム自身のモジュール＋対象と同じトップレベル
    パッケージ**に絞る。配布物には約 4200 のモジュールが入っているので、全件なめると
    次の2つが起きる。

      * コスト: パッチ1本ごとに全モジュールの全グローバルを見る。当て直しは
        最大 8 回あるので、これが積み上がる
      * 巻き添え: 同じオブジェクトを指しているだけの無関係な名前まで張り替わる。
        `tools/` の検証スクリプトが握っている変数が書き換わるのがその実例で、
        「テストで identity 比較が使えない」制約はここから来ていた

    対象のトップレベルを足しているのは、ゲーム以外を狙うパッチのため。
    `kivy.input.providers.wm_common:SetWindowLong_WndProc_wrapper` の複製束縛は
    kivy の中にあるので、ゲームのモジュールだけに絞ると届かない。

    全部なめてほしい場合は `alias_scan="all"` を明示する。
    """
    if mode == "all":
        return None
    top = target.split(":")[0].split(".")[0]
    return set(GAME_TOPLEVEL) | {top}


def rebind_aliases(old: Any, new: Any, *, skip: Any = None,
                   scope: set[str] | None = None) -> list[str]:
    """まだ old を指しているモジュール変数を全て new に張り替える。

    `from x import y` でコピーされた名前を拾うための処理。
    属性を1つ書き換えるだけでは、これらは取りこぼす。

    `scope` にトップレベルパッケージ名の集合を渡すと、その配下だけを見る
    （`_alias_scope` が組み立てる）。None なら全モジュール。
    """
    if old is None or old is new:
        return []

    rebound: list[str] = []
    # 走査中に sys.modules が変化することがあるので、list() でコピーしてから回す。
    for mod_name, module in list(sys.modules.items()):
        if module is None or module is skip:
            continue
        if scope is not None and mod_name.split(".")[0] not in scope:
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
def set_attr(target: str, value: Any, *, alias_scan: Any = True,
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
    _undo_log().append((label or target, owner, name, original, existed))
    setattr(owner, name, value)

    if alias_scan:
        # 持ち主がモジュール自身なら、今 setattr したばかりなので走査から除く。
        skip = owner if isinstance(owner, type(sys)) else None
        scope = _alias_scope(target, alias_scan)
        rebound: list[str] = []
        # 他のモジュールが持っているのは、素の関数のことも、前回の注入で
        # 残ったラッパのこともある。取りこぼさないよう両方を張り替える。
        for stale in (current, original):
            if stale is not None and stale is not value:
                rebound += rebind_aliases(stale, value, skip=skip, scope=scope)
        if rebound:
            unique = list(dict.fromkeys(rebound))
            log("  rebound {} alias(es): {}".format(
                len(unique), ", ".join(unique[:8]) + (" ..." if len(unique) > 8 else "")))
    return original


def patch(target: str, *, alias_scan: Any = True, required: bool = True,
          safe: bool = False) -> Callable:
    """対象を丸ごと差し替える。

    差し替えた関数には __original__ が付くので、中から元の実装を呼べる。

        @patch("scripts.llm.foo:parse_timings")
        def parse_timings(resp):
            return resp.get("timings", {})

    required=False にすると、対象が見つからなくても警告だけ出して先へ進む。
    ビルドによって存在しない関数を狙うときに使う。

    safe=True にすると、差し替えた関数が例外を投げてもゲームへ流さず、元の実装を
    呼んだ結果を返す（`_guard` を参照）。
    """
    def decorator(func: Callable) -> Callable:
        if _defer_if_not_imported(target, "patch"):
            return func
        try:
            owner, name, old = resolve(target)
        except (LookupError, AttributeError) as exc:
            # required=True でも記録してから投げる。ここで投げると boot() が
            # この mod を apply-error にして次へ進むので、記録を先にしておかないと
            # 「何が見つからなかったのか」が最後の報告に出ない。
            _registry.record(_registry.UNRESOLVED, target, detail=str(exc))
            if required:
                raise
            log("skip patch {} ({})".format(target, exc), level="WARN")
            return func

        # 属性が無い場合、setattr は黙って**新しい名前を作る**。
        # 対象名を打ち間違えた patch が「当たった」ものとして台帳に載ってしまい、
        # 未解決の報告があてにならなくなるので、ここで撥ねる。
        # 名前を新設したいときは required=False を明示する。
        if not hasattr(owner, name):
            _registry.record(_registry.UNRESOLVED, target, detail="attribute not found")
            if required:
                raise LookupError(f"{target}: no attribute {name!r} to replace")
            log("patch {} creates a new attribute (was not there)".format(target),
                level="WARN")

        old = unwrap_ours(old)
        try:
            # __name__ や __doc__ を引き継いで、トレースバックの見た目を保つ。
            functools.update_wrapper(func, old)
        except Exception:
            pass
        installed = _guard(func, old, target) if safe else func
        # 次の3行は update_wrapper の後で設定すること。
        # update_wrapper は元の関数の __dict__ をコピーするので、
        # 先に設定すると上書きされて消える。
        installed.__original__ = old
        setattr(installed, PATCH_MARK, target)
        setattr(installed, GENERATION_MARK, _generation)
        set_attr(target, installed, alias_scan=alias_scan)
        _registry.record(_registry.APPLIED, target,
                         detail="patch safe" if safe else "patch")
        log("patched {} ({!r} -> {!r}){}".format(
            target, _short(old), _short(func), " [safe]" if safe else ""))
        # ゲームに差し込むのは installed だが、返すのは func 自身。
        # こうしておくと、デコレートした名前で mod の中から直接呼べる。
        return func
    return decorator


def _guard(func: Callable, old: Callable, target: str) -> Callable:
    """`safe=True` の中身。フックの例外をゲームへ流さず、素の動作に落とす。

    「落とす」の意味が2つに分かれるので、元の関数が**もう走ったかどうか**で決める。

        まだ走っていない  → 元の関数を呼んでその結果を返す（素のゲームと同じ挙動）
        もう走った        → その結果をそのまま返す（後処理だけが失敗した）

    2つ目が要点で、単純に「失敗したら元を呼び直す」と書くと、元の関数が持つ副作用
    （テキストの追加・セーブ・状態の更新）が2回起きる。フックが `orig` を呼んだ後に
    壊れるのはよくある形（返り値を加工していて型を間違えた等）なので、ここを
    間違えると `safe=True` が事故の原因になる。

    `wrap` では第1引数として渡す `orig` がこの記録を兼ねる。`patch` では元の関数を
    呼ぶかどうかが差し替え側の自由なので、記録は取らず「まだ走っていない」扱いにする。
    """
    is_wrap = getattr(func, "__instantale_wrap__", False)

    @functools.wraps(old)
    def guarded(*args, **kwargs):
        box: dict = {}

        def orig(*a, **kw):
            box["result"] = old(*a, **kw)
            return box["result"]

        try:
            return func(orig, *args, **kwargs) if is_wrap else func(*args, **kwargs)
        except BaseException:
            log_exc("safe hook failed on {}; falling back to the original".format(target))
            if "result" in box:
                return box["result"]
            if old is None:
                # `required=False` で**属性を新設**した patch。元の実装が無いので
                # 落とす先も無い ― ここで `None(...)` を呼ぶと TypeError が
                # ゲームへ抜けて、safe の約束（例外を流さない）が破れる。
                return None
            return old(*args, **kwargs)

    return guarded


def wrap(target: str, *, alias_scan: Any = True, required: bool = True,
         safe: bool = False) -> Callable:
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

    safe=True にすると、この関数が投げた例外をゲームへ流さず、元の動作に落とす
    （`_guard` を参照）。ゲームを落としたくないだけの `try`/`except` を毎回書く
    代わりに使える。
    """
    def decorator(func: Callable) -> Callable:
        if _defer_if_not_imported(target, "wrap"):
            return func
        try:
            owner, name, old = resolve(target)
        except (LookupError, AttributeError) as exc:
            _registry.record(_registry.UNRESOLVED, target, detail=str(exc))
            if required:
                raise
            log("skip wrap {} ({})".format(target, exc), level="WARN")
            return func

        if old is None:
            # 包む相手が無い。理由は2つあり、報告では区別する。
            # 「名前ごと無い」＝ゲーム更新で関数が消えた可能性が高い。
            # 「名前はあるが None」＝そういう値が入っている（別の話）。
            _registry.record(_registry.UNRESOLVED, target,
                             detail="attribute not found" if not hasattr(owner, name)
                             else "resolved to None")
            if required:
                raise LookupError(f"{target} resolved to None")
            log("skip wrap {} (resolved to None)".format(target), level="WARN")
            return func

        # 委譲先は素の関数にする。前回の注入で残ったラッパを呼ぶと二重実行になる。
        old = unwrap_ours(old)

        if safe:
            # _guard が「orig を呼んだか」を見分けられるように印を付ける。
            try:
                func.__instantale_wrap__ = True
            except Exception:
                pass
            wrapper = _guard(func, old, target)
        else:
            @functools.wraps(old)
            def wrapper(*args, **kwargs):
                # 元の関数を第1引数に差し込む。これがこの API の中心。
                return func(old, *args, **kwargs)

        wrapper.__original__ = old
        wrapper.__wrapper_of__ = target
        setattr(wrapper, PATCH_MARK, target)
        setattr(wrapper, GENERATION_MARK, _generation)
        set_attr(target, wrapper, alias_scan=alias_scan)
        _registry.record(_registry.APPLIED, target,
                         detail="wrap safe" if safe else "wrap")
        log("wrapped {} ({!r}){}".format(target, _short(old), " [safe]" if safe else ""))
        # ゲームに差し込むのは wrapper だが、返すのは func 自身。
        # こうしておくと、デコレートした名前で mod の中から直接呼べる。
        return func
    return decorator


def revert_all() -> int:
    """当てたパッチを全て元に戻す。新しいものから順に。

    記録は sys に置いてあるので（`_undo_log`）、**前回の注入で当てた分も戻せる**。
    ローダを読み込み直しても記録は残るため、`unload()` は「注入 → 遊ぶ → 外す」の
    間にローダが何度読み直されていても成立する。

    同じ対象に複数の世代が層を重ねている場合、各世代が記録しているのはどれも
    「素の元の関数」なので、後入れ先出しで戻せば最後に書き戻されるのは
    一番古い記録＝素の関数になる。
    """
    count = 0
    entries = _undo_log()
    # 必ず後入れ先出しで戻すこと。
    # 同じ対象に複数の層が乗っている場合、順番を逆にすると
    # 古い層が最後に書き戻されて残ってしまう。
    while entries:
        label, owner, name, old, existed = entries.pop()
        try:
            # 属性を戻すだけでは足りない。当てるときに張り替えた複製束縛
            # （`from x import y` でコピーされた名前）はこちらのラッパを指したままで、
            # そこから呼ばれる経路が生き残ってしまう。当てたときと同じ範囲を
            # 逆向きに張り替える。
            current = getattr(owner, name, None)
            if existed:
                setattr(owner, name, old)
            else:
                delattr(owner, name)
            if current is not None and current is not old:
                rebind_aliases(current, old,
                               skip=owner if isinstance(owner, type(sys)) else None,
                               scope=_alias_scope(label, True))
            count += 1
        except Exception:
            log_exc("revert failed: {}".format(label))
    log("reverted {} patch(es)".format(count))
    return count


def active() -> list[str]:
    """今当たっているパッチの一覧。調査用。"""
    return [entry[0] for entry in _undo_log()]


def _short(obj: Any) -> str:
    """ログに出す用の短い名前。Nuitka の関数は repr が長くなるので使わない。"""
    try:
        return getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or type(obj).__name__
    except Exception:
        return "<?>"
