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

NAME 以外に VERSION / DESCRIPTION / AUTHOR も書ける（全て任意）。
書いてあればログと status() に出るだけで、動作は変わらない。

ctx に何があるかは下の ModContext を参照。パッチの当て方は patch.py に
詳しく書いてある。

インジェクタを実行し直すとこのパッケージも mod も全て読み込み直されるので、
「mod を編集する → もう一度注入する」が開発時のループになる。
"""

from __future__ import annotations

import datetime
import importlib.util
import json
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
    # mod ファイル名 -> マニフェスト（NAME / VERSION / DESCRIPTION / AUTHOR）。
    "manifests": {},
    # この boot で ctx.on_ready() に積まれた処理。mod の適用が全部済んでから流す。
    "ready": [],
    # 見張りが mod を当て直した回数。boot() では数え直さない（上限の判定に使う）。
    # 手で注入し直すとローダごと読み直されるので、そこで 0 に戻る。
    "deferred_boots": 0,
}

# on_ready の「もう実行した」印を置く場所。sys に生やしているのは、
# **注入し直すとこのモジュール自体が読み込み直される**ため。
# ここをモジュール変数にすると印ごと消えて、再注入や遅延再適用のたびに
# 1回きりのはずの処理（溜まった状態の掃除など）が走り直してしまう。
# sys はプロセスに1つしか無く、誰かが読み直すこともない。
_ONCE_ATTR = "__instantale_ready_once__"


def _once_store() -> set:
    store = getattr(sys, _ONCE_ATTR, None)
    if not isinstance(store, set):
        store = set()
        setattr(sys, _ONCE_ATTR, store)
    return store


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

    それに加えて、1回だけ実行したい処理を預けられる:

        ctx.on_ready(関数)  プロセスにつき1回だけ、メインスレッドで実行する
    """

    def __init__(self, out_dir: str, runtime_dir: str):
        self.out_dir = out_dir
        self.runtime_dir = runtime_dir
        self.log = log
        self.log_exc = log_exc
        # 今 apply() を実行中の mod ファイル名。boot() が出し入れする。
        # on_ready の既定のキーに使う。
        self._mod: str | None = None

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

    def patches(self) -> dict:
        """対象 -> その対象に当てた mod の一覧。

        自分より前に読み込まれた mod が同じ関数を触っているかを、apply() の中から
        確かめられる。ファイル名順の適用なので、見えるのは自分より前の分だけ。
        """
        from . import patch_registry as _registry
        return _registry.by_target()

    # -- 1回きりの処理 -------------------------------------------------------
    def on_ready(self, fn, *, key: str | None = None, delay: float = 0.0) -> bool:
        """`fn` を「このプロセスで1回だけ」「メインスレッドで」実行する。

        apply() は1プロセスの中で**何度も呼ばれる**。手で注入し直したときと、
        未 import のモジュールが現れて当て直したとき（`_arm_deferred`）で、
        後者は最大 MAX_DEFERRED_BOOTS 回まで起こりうる。

        パッチを当てる分にはこれで問題ない。patch.py の世代管理が前の層を
        置き換えるので、何度当てても結果は1回分になる。困るのは**副作用のある
        初期化**で、apply() の中で直接やると回数ぶん繰り返される:

            溜まった「迷子の曲」の掃除、状態ファイルの初期化、スレッドの起動

        そういう処理をここへ預ける。同じキーで2回目以降に積まれたものは黙って
        捨てられる（戻り値 False）。

        実行は Kivy の Clock 経由にしてある。ゲームの状態を触ってよいのは
        メインスレッドだけで、`boot()` 自体は注入したリモートスレッドの上で
        走っているため。Clock が無い環境（オフライン検証など）ではその場で呼ぶ。

        キーの既定は「mod ファイル名 + 関数名」。同じ mod の中で複数の
        on_ready を使い分けたい場合や、mod をまたいで1回にしたい場合は
        `key` を明示する。
        """
        name = key or "{}:{}".format(
            self._mod or "<loader>",
            getattr(fn, "__qualname__", None) or getattr(fn, "__name__", repr(fn)))
        store = _once_store()
        # 印を「積んだ時点」で付ける。実行時に付けると、実行前にもう一度
        # boot() が走ったときに二重に積まれる（Clock はまだ流していない）。
        if name in store:
            log("on_ready: {} already done in this process; skipped".format(name))
            return False
        store.add(name)
        _state["ready"].append((name, fn, delay))
        return True

    def out_path(self, *parts: str) -> str:
        """out/ 以下のパスを返す。親ディレクトリは先に作っておく。"""
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    @property
    def mod_dir(self) -> str | None:
        """いま apply() が走っている mod のフォルダ。同梱データを読むとき用。

            table = json.load(open(os.path.join(ctx.mod_dir, "data", "x.json")))

        **読む専用**。書き込みは `ctx.out_path()`（out/ 以下）へ。mods/ は
        配布物そのもので、遊ぶ側が書き換わることを想定していない。

        apply() の外（`on_ready` の中など）では None になるので、フォルダを
        後で使うなら apply() の中で控えておく。
        """
        if self._mod is None:
            return None
        return os.path.join(self.runtime_dir, "mods", self._mod)

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


MANIFEST_NAME = "mod.json"
ORDER_NAME = "load_order.json"


def _read_json(path: str):
    """JSON を読む。読めなければ None（呼び出し側が既定へ倒す）。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception:
        log_exc("cannot read {}".format(path))
        return None


def _installed(mods_dir: str) -> list[str]:
    """mods/ に入っている mod のフォルダ名。並びは名前順。

    **1つの mod は1つのフォルダ**で、`mod.json` を持つものだけを mod とみなす:

        mods/mini_quest/mod.json      名乗りと入口ファイルの宣言
        mods/mini_quest/quest.py      入口（mod.json の "entry"）
        mods/mini_quest/prompts.py    分割した中身（from . import prompts）
        mods/mini_quest/data/...      同梱データ（ctx.mod_dir から読む）

    フォルダ名にもファイル名にも決まりは無い。入口は `mod.json` が名指しする。

    探索は**この1階層だけ**で、再帰しない。深く潜ると mod の中の補助モジュール
    （上の `prompts.py`）まで mod として拾ってしまい、「何が mod なのか」の規則が
    増える。

    先頭が "_" のフォルダは読み込まない（手で切るときの逃げ道。GUI から切った
    ものは `load_order.json` の "disabled" に載る ― `_discover` を参照）。
    """
    found = []
    for name in sorted(os.listdir(mods_dir)):
        if name.startswith("_") or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(mods_dir, name, MANIFEST_NAME)):
            found.append(name)
    return found


def _discover(mods_dir: str) -> list[str]:
    """mod を**適用順に**並べて返す。

    このローダでは適用順が動作の前提になっている（TECH.md §3.2 — 計測は修正より
    外側に置く、など）。順序はフォルダ名から決めるのをやめ、`load_order.json` が
    明示的に持つ:

        {"order": ["recon", "crash_recorder", "fix_kivy_shutdown", ...],
         "disabled": ["recon"]}

    `disabled` に載っている mod は**読み込まない**。GUI のチェックボックスの実体で、
    フォルダ名を変えずに切れるようにしてある（`_` を付ける方式だと、無効にした
    瞬間に `order` の中の名前と食い違う）。

    こうしたのは、フォルダ名を自由に付けられるようにしたため。名前で順序を表すと
    「名前は自由」と「順序は名前で決まる」が両立しない。

    **順序ファイルに無い mod は捨てずに末尾へ回す**（フォルダ名順）。新しい mod を
    フォルダごと置いただけで動くようにするため。逆に、順序ファイルに載っているが
    実体が無いものは黙って飛ばす（消した mod の記述が残っていても壊れない）。

    順序ファイルが読めなければフォルダ名順。**必ず何らかの決まった順で動く**ように
    しておく ― ここで例外にすると、順序ファイルが壊れた瞬間に mod が全滅する。
    """
    found = _installed(mods_dir)
    data = _read_json(os.path.join(mods_dir, ORDER_NAME))

    order = []
    if isinstance(data, dict):
        order = data.get("order") or []
    elif isinstance(data, list):        # 素の配列で書かれていても読む
        order = data
    if not isinstance(order, list):
        log("{}: \"order\" is not a list; falling back to name order".format(ORDER_NAME),
            level="WARN")
        order = []

    disabled = data.get("disabled") if isinstance(data, dict) else None
    if not isinstance(disabled, list):
        disabled = []
    off = {name for name in disabled if isinstance(name, str)}
    if off:
        # 「入れたのに効かない」を黙って起こさない。切ったことは必ず残す。
        skipped = sorted(off & set(found))
        if skipped:
            log("disabled in {}; not loaded: {}".format(ORDER_NAME, ", ".join(skipped)))

    known = set(found) - off
    ordered = []
    for name in order:
        if name in known and name not in ordered:
            ordered.append(name)

    extra = [name for name in found if name in known and name not in ordered]
    if extra and order:
        # 順序ファイルに無い mod。落とさずに末尾へ回したことを残す
        # （「入れたのに効かない」を黙って起こさないため）。
        log("not in {}; applied last: {}".format(ORDER_NAME, ", ".join(extra)))
    return ordered + extra


def _load_mod_file(path: str):
    """mod をパス指定で読み込む。ゲームのモジュール名とは隔離する。

    `path` は `mod.json` の "entry" が指すファイル。**パッケージとして**読み込むので、
    入口の名前が何であれ、mod の中から `from . import prompts` で隣を引ける。
    """
    # 専用の接頭辞を付けた名前で sys.modules に登録する。
    # ゲーム側のモジュール名とぶつかると、どちらかが壊れるため。
    mod_dir = os.path.dirname(path)
    name = "instantale_mod_" + os.path.basename(mod_dir)
    # submodule_search_locations を渡すとパッケージ扱いになる。
    # これが無いと mod の中の相対 import が
    # 「親パッケージが無い」で落ちる（単一ファイルの頃は不要だった）。
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[mod_dir])
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


def _manifest(mods_dir: str, name: str) -> dict:
    """mod.json を読んで、扱いやすい形に均して返す。GUI の一覧に出す用。

        {"entry": "quest.py",
         "name":        {"en": "Non-combat mini quests", "ja": "戦闘なしミニクエスト"},
         "description": {"en": "...", "ja": "..."},
         "version": "1", "author": "R01/Flossian"}

    **mod のコードを import せずに読める**ことがこの形の要点。GUI は無効化中の
    mod も、壊れている mod も、一覧に出すだけならコードを一切走らせずに済む。
    名乗りを Python のモジュール変数に置いていると、一覧を作るだけのために
    他人の mod を import することになる（import した時点でトップレベルは走る）。

    `entry` 以外は全て任意。`name` は文字列でも `{"en":..., "ja":...}` でも書ける。
    **片方の言語しか無ければもう片方で埋める**ので、`name["ja"]` は必ず何かを返す
    （GUI の行が空にならない）。
    """
    data = _read_json(os.path.join(mods_dir, name, MANIFEST_NAME))
    if not isinstance(data, dict):
        data = {}

    def text(value) -> str:
        if value is None:
            return ""
        try:
            return str(value).strip()
        except Exception:
            return ""

    def localized(key: str, default: str = "") -> dict:
        value = data.get(key)
        if isinstance(value, dict):
            en, ja = text(value.get("en")), text(value.get("ja"))
        else:
            en, ja = text(value), ""     # 文字列1つなら英語として扱う
        en = en or ja or default
        return {"en": en, "ja": ja or en}

    return {
        "dir": name,
        # 入口の既定。mod.json が無い/壊れている場合の受け皿で、
        # 実際に読めるかどうかは boot() が確かめる。
        "entry": text(data.get("entry")) or "mod.py",
        # 名前が無ければフォルダ名。GUI の行が名無しにならないように。
        "name": localized("name", name),
        "description": localized("description"),
        "version": text(data.get("version")),
        "author": text(data.get("author")),
    }


# --------------------------------------------------------------------------
# 1回きりの処理の実行
# --------------------------------------------------------------------------
def _dispatch_ready() -> None:
    """この boot で積まれた on_ready を流す。

    Clock に載せるのは、`boot()` が注入したリモートスレッドの上で走っていて、
    ゲームの状態（Kivy のウィジェット、pygame の音）をそこから触れないため。
    `schedule_once` は次のフレーム＝メインスレッドで呼ばれる。
    """
    entries = _state["ready"]
    _state["ready"] = []
    if not entries:
        return

    try:
        from kivy.clock import Clock
    except Exception:
        Clock = None      # ゲームの外（オフライン検証など）

    log("on_ready: {} task(s){}".format(
        len(entries), "" if Clock else " (no kivy Clock; running inline)"))

    for name, fn, delay in entries:
        # 既定引数で束縛する。ループ変数のまま閉じ込めると、Clock が呼ぶ頃には
        # 最後の1件を全員が指している。
        def call(_dt=None, name=name, fn=fn):
            try:
                fn()
                log("on_ready: {} done".format(name))
            except BaseException:
                # ここは Clock（メインスレッド）の中なので、投げるとゲームが落ちる。
                log_exc("on_ready failed: {}".format(name))

        if Clock is None:
            call()
            continue
        try:
            Clock.schedule_once(call, delay)
        except BaseException:
            # 載せられなかった＝一度も走っていない。印を外して、次の boot
            # （再注入や遅延当て直し）で積み直せるようにする。
            # 印は「実行した」ではなく「実行したか、Clock に渡ってもう走る」の意。
            # ここで残すと、走らないまま二度と積まれない一件が生まれる。
            _once_store().discard(name)
            log_exc("on_ready: could not schedule {} (will retry next boot)".format(name))


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

    from . import patch_registry as _registry

    mods_dir = _mods_dir()
    results: dict[str, str] = {}
    manifests: dict[str, dict] = {}
    _state["ready"] = []      # 前回の boot で流し残した分は持ち越さない

    if not os.path.isdir(mods_dir):
        log("no mods dir at {}".format(mods_dir), level="WARN")
    else:
        # 適用順は load_order.json が決める（_discover を参照）。
        names = _discover(mods_dir)
        log("{} mod(s) in {}".format(len(names), mods_dir))
        for fname in names:
            # 名乗りは**コードを読み込む前**に集める。こうしておくと、
            # 読み込みに失敗した mod も GUI の一覧に名前付きで出せる。
            manifest = _manifest(mods_dir, fname)
            manifests[fname] = manifest
            path = os.path.join(mods_dir, fname, manifest["entry"])

            # 読み込みでも apply() でも、失敗したらログに残して次の mod へ進む。
            # 1つの mod が壊れているせいで残り全部が動かないのを防ぐため。
            if not os.path.isfile(path):
                log("{}: entry {!r} not found".format(fname, manifest["entry"]),
                    level="WARN")
                results[fname] = "no-entry"
                continue
            try:
                module = _load_mod_file(path)
            except BaseException:
                log_exc("load failed: {}".format(fname))
                results[fname] = "load-error"
                continue

            apply_fn = getattr(module, "apply", None)
            if apply_fn is None:
                log("{} has no apply(), skipped".format(fname), level="WARN")
                results[fname] = "no-apply"
                continue

            # 台帳とコンテキストに「今どの mod を実行中か」を教える。
            # patch.py はこれを見てパッチの帰属を決め、ctx.on_ready は
            # 既定のキーに使う。apply が例外で抜けても必ず戻すこと（finally）。
            # 戻し忘れると、次に記録されたパッチが前の mod のものとして残る。
            _registry.begin_mod(fname)
            ctx._mod = fname
            try:
                apply_fn(ctx)
            except BaseException:
                log_exc("apply failed: {}".format(fname))
                results[fname] = "apply-error"
                continue
            finally:
                _registry.end_mod()
                ctx._mod = None
            log("applied: {}".format(fname))
            results[fname] = "ok"

    _state["mods"] = results
    _state["manifests"] = manifests
    _state["booted"] = True

    ok = sum(1 for v in results.values() if v == "ok")
    log("-" * 70)
    log("boot complete: {}/{} mod(s) applied".format(ok, len(results)))

    # 適用に失敗した mod を先に名指しする。トレースバックは上に出ているが、
    # 何本読み込んだか分からないログの中では埋もれるため。
    broken = {f: v for f, v in results.items() if v != "ok"}
    if broken:
        log("{} mod(s) not applied:".format(len(broken)), level="WARN")
        for fname in sorted(broken):
            log("  {} [{}]".format(fname, broken[fname]), level="WARN")

    # どの mod がどこへ当てたか、重なりはどこか、解決できなかった対象は何か。
    # ゲーム更新で関数が消えた場合はここの UNRESOLVED に出る。
    for line in _registry.format_report():
        log(line)
    log("-" * 70)

    # 1回きりの処理を流す。mod の適用が全部済んでから（適用中に Clock が
    # 回り始めると、まだ当たっていないパッチを前提にした処理が走りうる）。
    _dispatch_ready()

    # まだ import されていないモジュール宛てのフックがあれば、現れるまで見張る。
    # mod の適用が全部終わってから立てること（適用中に当て直しが走らないように）。
    _arm_deferred(out_dir, generation)
    return dict(results)


def status() -> dict:
    """今のローダの状態を返す。動いているプロセスに問い合わせて調べるとき用。

    これ1回で GUI が要るものが揃うようにしてある:

        ["mods"]       ファイル名 -> "ok" / "load-error" / "apply-error" / "no-apply"
        ["manifests"]  ファイル名 -> 名乗り（name/description は {"en","ja"}）
        ["patches"]    台帳（by_target / by_mod / conflicts / unresolved / counts）

    `_state` を浅く写してから台帳を足している。`_state` をそのまま返すと
    呼び出し側の書き換えがローダに届いてしまうため。
    """
    from . import patch_registry as _registry
    snapshot = dict(_state)
    snapshot["patches"] = _registry.summary()
    return snapshot


def patches() -> dict:
    """対象 -> その対象に当てた mod の一覧。動いているプロセスへの問い合わせ用。"""
    from . import patch_registry as _registry
    return _registry.by_target()


def mod_patches() -> dict:
    """mod -> その mod が当てた対象の一覧（`patches()` の逆引き）。"""
    from . import patch_registry as _registry
    return _registry.by_mod()


def conflicts() -> dict:
    """2つ以上の mod が触っている対象だけ。"""
    from . import patch_registry as _registry
    return _registry.conflicts()
