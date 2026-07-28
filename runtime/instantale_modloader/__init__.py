# -*- coding: utf-8 -*-
"""mod ローダ本体。ゲームの Python インタプリタ（CPython 3.10）の中で動く。

injector.py がこのディレクトリを sys.path に追加して boot() を呼ぶことで
読み込まれる。ここから先はゲームと同じプロセス・同じインタプリタなので、
ここで例外を投げるとゲームを巻き込む。そのため mod が失敗した場合は
ログに残して次へ進むだけにしてあり、外へは投げない。

ディレクトリ構成:

    instantale_modloader/   このパッケージ（ローダ + パッチ API + リコン）
    mods/                   1つの修正につき1ファイル。ファイル名順に適用される

mod ファイルの書き方は次の2つを定義するだけ:

    NAME = "何をする mod か（ログに出る）"

    def apply(ctx):
        @ctx.wrap("モジュール名:関数名")
        def 好きな名前(orig, *args, **kwargs):
            ...
            return orig(*args, **kwargs)

ctx に何があるかは下の ModContext を参照。パッチの当て方は patch.py に
詳しく書いてある。

インジェクタを実行し直すとこのパッケージも mod も全て読み込み直されるので、
「mod を編集する → もう一度注入する」が開発時のループになる。
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import sys
import threading
import time
import traceback
import uuid

__version__ = "0.1.0"

# 「まだ import されていないモジュール」を待つ見張りの設定。
# ゲームは LLM 系モジュールを最初のリクエストまで import しないので、
# 起動直後に注入すると llama 系のフックが1つも載らない。詳しくは _arm_deferred。
DEFERRED_POLL = 5.0          # sys.modules を見に行く間隔（秒）
DEFERRED_TIMEOUT = 3600.0    # ここまで来なければ諦める（1時間）
MAX_DEFERRED_BOOTS = 8       # 当て直しの上限。暴走よけ

# ローダの状態。再注入のたびに作り直される。
# ゲーム側に残っているパッチとの対応付けは generation（下の boot を参照）で行う。
_state: dict = {
    "booted": False,
    "boot_count": 0,
    "out_dir": None,
    "log_path": None,
    "mods": {},
    # 見張りが mod を当て直した回数。boot() では数え直さない（上限の判定に使う）。
    # 手で注入し直すとローダごと読み直されるので、そこで 0 に戻る。
    "deferred_boots": 0,
}


# --------------------------------------------------------------------------
# ログ出力
# --------------------------------------------------------------------------
def log(msg: str, *, level: str = "INFO") -> None:
    line = "[{}] {:<5} {}".format(datetime.datetime.now().isoformat(timespec="milliseconds"),
                                  level, msg)
    # ファイルと stderr の両方に出す。
    # どちらも失敗する可能性があるので（出力先が未設定、stderr が閉じている等）、
    # 別々に握り潰す。ログのせいでゲームを落とすのは本末転倒なので。
    path = _state.get("log_path")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    try:
        sys.stderr.write("[modloader] " + line + "\n")
    except Exception:
        pass


def log_exc(msg: str) -> None:
    """今処理している例外を、トレースバック付きで ERROR として記録する。

    except 節の中から呼ぶこと。
    """
    log(msg + "\n" + traceback.format_exc(), level="ERROR")


# --------------------------------------------------------------------------
# 各 mod に渡されるコンテキスト
# --------------------------------------------------------------------------
class ModContext:
    """mod が使える機能をまとめたもの。apply(ctx) の ctx がこれ。

    よく使うのは次の5つ:

        ctx.wrap(対象)      元の関数を第1引数で受け取るラッパを付ける
        ctx.patch(対象)     関数を丸ごと差し替える
        ctx.log(文字列)     modloader.log に出す
        ctx.log_exc(文字列) 例外をトレースバック付きで出す
        ctx.out_path(名前)  out/ 以下のパスを作る（親ディレクトリも作成）
    """

    def __init__(self, out_dir: str, runtime_dir: str):
        self.out_dir = out_dir
        self.runtime_dir = runtime_dir
        self.log = log
        self.log_exc = log_exc

    # -- patch モジュールへの入口 -------------------------------------------
    # ここで遅延 import しているのは循環 import を避けるため。
    # patch.py はこのモジュールから log / log_exc を import している。
    def patch(self, target: str, **kw):
        from . import patch as _patch
        return _patch.patch(target, **kw)

    def wrap(self, target: str, **kw):
        from . import patch as _patch
        return _patch.wrap(target, **kw)

    def resolve(self, target: str):
        """対象を (持ち主, 属性名, 現在の値) として取り出す。調査用。"""
        from . import patch as _patch
        return _patch.resolve(target)

    def out_path(self, *parts: str) -> str:
        """out/ 以下のパスを返す。親ディレクトリは先に作っておく。"""
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # -- 実行環境の情報 -----------------------------------------------------
    @property
    def game_dir(self) -> str:
        return os.path.dirname(os.path.abspath(sys.executable))

    def describe(self) -> str:
        return (
            "python      : {}\n"
            "executable  : {}\n"
            "compiled    : {}\n"
            "modules     : {}\n"
            "out_dir     : {}\n"
        ).format(sys.version.replace("\n", " "),
                 sys.executable,
                 # __compiled__ があれば Nuitka でビルドされたモジュール。
                 # 素の Python で動かしているのか、ゲームの中なのかの区別に使える。
                 "__compiled__" in dir(sys.modules.get("__main__", object())),
                 len(sys.modules),
                 self.out_dir)


# --------------------------------------------------------------------------
# mod の探索と読み込み
# --------------------------------------------------------------------------
def _mods_dir() -> str:
    # このファイルは runtime/instantale_modloader/__init__.py にあるので、
    # 2階層上がって runtime/ に出て、その下の mods/ を指す。
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mods")


def _load_mod_file(path: str):
    """mod ファイルをパス指定で読み込む。ゲームのモジュール名とは隔離する。"""
    # 専用の接頭辞を付けた名前で sys.modules に登録する。
    # ゲーム側のモジュール名とぶつかると、どちらかが壊れるため。
    name = "instantale_mod_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # 読み込みに失敗したモジュールを sys.modules に残さない。
        # 残すと次の注入で「もう読み込み済み」と誤認される可能性がある。
        sys.modules.pop(name, None)
        raise
    return module


# --------------------------------------------------------------------------
# 後から import されるモジュールを待つ
# --------------------------------------------------------------------------
def _superseded(generation: str) -> bool:
    """この見張りが用済みかどうか。

    降りるべき場合が2つある。1つは同じローダで別の boot が走ったとき。
    もう1つは注入し直されてローダ自体が読み込み直されたときで、このスレッドが
    握っているのは古いモジュールなので、そのまま当て直すと二重に適用してしまう。
    後者は sys.modules の中身が別の _state を持っているかどうかで見分ける。
    """
    if _state.get("generation") != generation:
        return True
    current = sys.modules.get(__name__)
    return current is not None and getattr(current, "_state", None) is not _state


def _deferred_loop(out_dir: str, generation: str, pending: list) -> None:
    deadline = time.monotonic() + DEFERRED_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(DEFERRED_POLL)
        if _superseded(generation):
            return
        arrived = [name for name in pending if sys.modules.get(name) is not None]
        if not arrived:
            continue
        # 1つでも来たら当て直す。全部揃うのを待つと、片方が永久に来ない場合
        # （エリアを生成しなければ save_area_json は載らない）に全部が道連れになる。
        # 当て直した後は残りの保留に対して新しい見張りが立つ。
        log("deferred: {} imported; re-applying mods".format(", ".join(arrived)))
        _state["deferred_boots"] += 1
        try:
            boot(out_dir)
        except BaseException:
            # ここで投げるとゲーム側のスレッドを道連れにするので、記録だけして降りる。
            log_exc("deferred re-apply failed")
        return
    log("deferred: gave up after {:.0f}s; still not imported: {}".format(
        DEFERRED_TIMEOUT, ", ".join(n for n in pending if sys.modules.get(n) is None)),
        level="WARN")


def _arm_deferred(out_dir: str, generation: str) -> None:
    """未 import のモジュール宛てのフックがあれば、現れるまで見張る。

    ゲームは `llama_cpp_runtime_completion` と `scripts.llm.llm_manager` を
    最初の LLM リクエストまで import しない。`watcher.py` は「インタプリタ初期化」と
    「ウィンドウ表示」で注入するが、その時点ではまだどちらも載っていないので、
    プロンプト関係の mod（DEDUP / COMPACT / 計測）が1つも設置されないまま
    プレイが進むことになる。

    そこで、モジュールが現れた時点で mod を当て直す。当て直しは手作業での
    再注入と同じ経路で、世代管理（patch.py）が前の層を置き換えるので重ならない。
    """
    from . import patch as _patch
    pending = _patch.pending_modules()
    if not pending:
        return
    if _state["deferred_boots"] >= MAX_DEFERRED_BOOTS:
        log("deferred: already re-applied {} time(s); not watching again for {}".format(
            _state["deferred_boots"], ", ".join(pending)), level="WARN")
        return
    log("deferred: waiting for {} (checking every {:.0f}s)".format(
        ", ".join(pending), DEFERRED_POLL))
    threading.Thread(target=_deferred_loop, args=(out_dir, generation, pending),
                     name="instantale_modloader.deferred", daemon=True).start()


def boot(out_dir: str) -> dict:
    """注入されたブートストラップから呼ばれる入口。"""
    _state["out_dir"] = out_dir
    _state["log_path"] = os.path.join(out_dir, "modloader.log")
    _state["boot_count"] += 1

    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass

    # 今回の注入に固有の ID を振る。
    # 同じプロセスに2回目以降の注入をしたとき、patch.py がこの ID を見て
    # 「前回の注入が残したラッパ」と「今回自分が付けたラッパ」を区別する。
    # これが無いと、注入のたびにラッパが入れ子で積み上がってしまう。
    generation = uuid.uuid4().hex[:12]
    _state["generation"] = generation
    from . import patch as _patch
    _patch.set_generation(generation)

    log("=" * 70)
    log("instantale_modloader {} boot #{} gen={} (pid {})".format(
        __version__, _state["boot_count"], generation, os.getpid()))
    log("python {} | {} modules loaded".format(sys.version.split()[0], len(sys.modules)))

    runtime_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ctx = ModContext(out_dir, runtime_dir)

    mods_dir = _mods_dir()
    results: dict[str, str] = {}

    if not os.path.isdir(mods_dir):
        log("no mods dir at {}".format(mods_dir), level="WARN")
    else:
        # ファイル名順がそのまま適用順になる。
        # 先頭が "_" のファイルは読み込まない（一時的に無効化したいとき用）。
        files = sorted(f for f in os.listdir(mods_dir)
                       if f.endswith(".py") and not f.startswith("_"))
        log("{} mod file(s) in {}".format(len(files), mods_dir))
        for fname in files:
            path = os.path.join(mods_dir, fname)
            # 読み込みでも apply() でも、失敗したらログに残して次の mod へ進む。
            # 1つの mod が壊れているせいで残り全部が動かないのを防ぐため。
            try:
                module = _load_mod_file(path)
            except BaseException:
                log_exc("load failed: {}".format(fname))
                results[fname] = "load-error"
                continue

            label = getattr(module, "NAME", fname)
            apply_fn = getattr(module, "apply", None)
            if apply_fn is None:
                log("{} has no apply(), skipped".format(fname), level="WARN")
                results[fname] = "no-apply"
                continue
            try:
                apply_fn(ctx)
            except BaseException:
                log_exc("apply failed: {} ({})".format(fname, label))
                results[fname] = "apply-error"
                continue
            log("applied: {} ({})".format(fname, label))
            results[fname] = "ok"

    _state["mods"] = results
    _state["booted"] = True

    ok = sum(1 for v in results.values() if v == "ok")
    log("boot complete: {}/{} mod(s) applied".format(ok, len(results)))

    # まだ import されていないモジュール宛てのフックがあれば、現れるまで見張る。
    # mod の適用が全部終わってから立てること（適用中に当て直しが走らないように）。
    _arm_deferred(out_dir, generation)
    return dict(results)


def status() -> dict:
    """今のローダの状態を返す。動いているプロセスに問い合わせて調べるとき用。"""
    return dict(_state)
