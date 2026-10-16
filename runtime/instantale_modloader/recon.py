# -*- coding: utf-8 -*-
"""動いているゲームの中身を調べて、パッチ対象の一覧を書き出す。

Nuitka でビルドされているのでソースは存在しない。
関数の正確な名前を知る方法は、動いているインタプリタに
sys.modules を通して直接聞くことだけになる。
ここでは以下を出力する。

    recon/summary.txt        実行環境とモジュールの概況
    recon/modules.json       全モジュールの一覧（機械可読）
    recon/game_modules.txt   ゲーム自身のモジュールの全属性。擬似的なソース一覧
    recon/targets.txt        "モジュール名:関数名" の形。@ctx.wrap にそのまま貼れる
    recon/bug_sites.txt      クラッシュログに出ていた箇所まわりの調査結果

ゲームには一切変更を加えない。
読み取って書き出すだけ。
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import inspect
import json
import os
import re
import shutil
import sys
import types
import zipfile

# ゲーム自身のトップレベルモジュールの表（GAME_TOPLEVEL）はパッケージ側にある。
# patch.py もエイリアス張り替えの範囲を決めるのに同じ表を見るので、
# 2箇所に書き写さないため。
# 名前はここからも引けるように再公開しておく。
from . import GAME_TOPLEVEL, is_game_module, log, log_exc, write_json  # noqa: F401

MAX_REPR = 300
MAX_CONST_ITEMS = 40

#: 毎回書き出すファイル。
#: 退避（zip）に詰めるのもこの一式。
OUTPUT_FILES = ("summary.txt", "modules.json", "game_modules.txt",
                "targets.txt", "bug_sites.txt", "signatures.json")

#: 関数の変化を見るための控え。`{"module:qualname": "(引数)"}`。ゲーム自身の関数だけ。
SIGNATURES_NAME = "signatures.json"

#: MOD とローダが定義した関数の `__module__` の頭。`signatures.json` から除く。
MOD_ORIGINS = ("instantale_mod", "instantale_modloader")

#: ローダと MOD がゲームのモジュールやクラスに置く名前の頭。関数の比べと重複の判定から除く。
#: 注入のときに `__main__` へ置く `_instantale_modloader_bootstrap` は、ある回と無い回がある
#: （2026-10-15 の実物。比べに入れると、版が同じでも「関数が消えた」と見える）。
MOD_NAME_PREFIXES = ("_instantale", "__instantale")

#: 退避先の中の、今のビルドの基準のダンプ（そのビルドで見た中でいちばん多くのモジュールを読み込んだ回）。
#: 版が変わった・関数が変わった・特異な変化があったときは、これを zip にして退避する。
CURRENT_DIR_NAME = "current"

#: 退避した理由を zip に添えるファイル。
REASON_NAME = "archive_reason.txt"

#: その回のリコンが「どのビルドを見たものか」を書き添える控え。
#: `out/recon/` の中に置くので、退避したときは zip の中にそのまま入る（zip 自身が何の版のものか、
#: 開ければ分かる）。
BUILD_NAME = "build.json"

#: 退避先。配布フォルダ直下（`out/` と並ぶ位置）に `recon_history/<版>_YYYYMMDD.zip` の形で溜まる。
#: 版ごとの履歴で取り直せないので、「消してよい」置き場の `out/` には置かない（GAME.md §1.1）。
SNAPSHOT_DIR_NAME = "recon_history"

#: 基準（`current/`）に添える、前の版からの関数の変化（`build_changes`）。
CHANGES_NAME = "changes.json"

#: `recon_history/` の下で recon が触らないフォルダ（`drift` の控え）。刈り込みは zip しか見ない。
DRIFT_DIR_NAME = "drift"

#: 前の退避先（`out/recon_snapshots/`）。中身は `dump()` が新しい退避先へ移す。
OLD_SNAPSHOT_DIR_NAME = "recon_snapshots"

#: 退避を残す本数の上限。古いものから消す。
#: 版の判定は**安全側に倒してある**（読めない版は「別のビルド」。
#: `_same_build` と tools/tests/test_recon_archive.py を参照）ので、
#: 素性が読めるようになる前後で1回の起動から2つ以上できることがある。
#: 実測では同じ版のまま 109 個 / 23MB まで溜まっていた。
#: `tools/logrotate.py` は `out/` 直下の `*.log` と `*.jsonl` しか回さないので、ここで刈る。
#: **版ごとの1本（`_keepers`）は数に入れず、消さない。** 上限で数えるのは同じ版の2本目以降だけ。
#: 前は版を問わず古い順に消したので、同じ版の退避が溜まると前の版の控えが押し出された
#: （main_024 の控えはこれで失われた。VERIFICATION.md §3.100）。
SNAPSHOT_KEEP = 20


# --------------------------------------------------------------------------
# 分類
# --------------------------------------------------------------------------
def is_compiled(module: types.ModuleType) -> bool:
    """Nuitka がコンパイルしたモジュールには __compiled__ が入っている。"""
    try:
        return "__compiled__" in vars(module)
    except Exception:
        return False


def safe_repr(value: object) -> str:
    """repr が失敗しても長すぎても、リコン全体を止めないようにする。"""
    try:
        text = repr(value)
    except Exception as exc:
        return f"<repr failed: {type(exc).__name__}>"
    # 改行を潰さないと、1行1エントリの出力形式が崩れる。
    text = text.replace("\n", "\\n")
    return text if len(text) <= MAX_REPR else text[:MAX_REPR] + "...<truncated>"


def safe_signature(obj: object) -> str:
    """引数の並びを文字列にする。

    Nuitka のコンパイル済み関数でも inspect.signature はだいたい通る。
    通らない場合は __code__ から引数名だけを取り出す。
    """
    try:
        return str(inspect.signature(obj))
    except Exception:
        pass
    try:
        # こちらの経路では既定値やキーワード専用かどうかは分からないので、
        # 見分けが付くようコメントを付けておく。
        code = getattr(obj, "__code__", None)
        if code is not None:
            names = list(code.co_varnames[:code.co_argcount])
            return "(" + ", ".join(names) + ")  # from __code__"
    except Exception:
        pass
    return "(?)"


def kind_of(value: object) -> str:
    """出力の形を切り替えるための、おおまかな種類判定。"""
    if inspect.ismodule(value):
        return "module"
    if inspect.isclass(value):
        return "class"
    if inspect.isroutine(value):
        return "function"
    if isinstance(value, property):
        return "property"
    if isinstance(value, (staticmethod, classmethod)):
        return "descriptor"
    return "value"


# --------------------------------------------------------------------------
# ダンプ
# --------------------------------------------------------------------------
def describe_class(cls: type) -> dict:
    """クラス1つを、基底クラス・MRO・メンバ一覧として記録する。"""
    info: dict = {
        "bases": [],
        "mro": [],
        "members": {},
    }
    try:
        info["bases"] = [getattr(b, "__qualname__", str(b)) for b in cls.__bases__]
        info["mro"] = [getattr(c, "__module__", "?") + "." + getattr(c, "__qualname__", "?")
                       for c in inspect.getmro(cls)]
    except Exception:
        pass

    try:
        members = list(vars(cls).items())
    except Exception:
        return info

    for name, value in members:
        # 特殊メソッドは基本的に飛ばすが、__init__ と
        # __call__ はパッチ対象になり得るので残す。
        if name.startswith("__") and name.endswith("__") and name not in ("__init__", "__call__"):
            continue
        entry: dict = {"kind": kind_of(value)}
        if entry["kind"] in ("function", "descriptor"):
            target = value
            # staticmethod / classmethod は包み物なので、中身を取り出して調べる。
            if isinstance(value, (staticmethod, classmethod)):
                target = value.__func__
            entry["signature"] = safe_signature(target)
            doc = getattr(target, "__doc__", None)
            # 空白だけ・文字列でない docstring で、モジュール1本ぶんの記述ごと落とさない
            lines = doc.strip().splitlines() if isinstance(doc, str) else []
            if lines:
                entry["doc"] = lines[0][:200]
        elif entry["kind"] == "property":
            entry["signature"] = "<property>"
        elif entry["kind"] == "value":
            entry["repr"] = safe_repr(value)
        info["members"][name] = entry
    return info


def describe_module(name: str, module: types.ModuleType, *, deep: bool) -> dict:
    """モジュール1つを記録する。deep=False なら見出しの情報だけ。

    ゲーム以外の約 4200 モジュールまで中身を展開すると出力が使い物にならないので、
    深く見るのはゲームのモジュールだけにしている（呼び出し側が deep で指定する）。
    """
    info: dict = {
        "name": name,
        "file": getattr(module, "__file__", None),
        "package": getattr(module, "__package__", None),
        "compiled": is_compiled(module),
        "game": is_game_module(name),
    }
    if not deep:
        return info

    info["attributes"] = {}
    try:
        namespace = list(vars(module).items())
    except Exception:
        info["error"] = "vars() failed"
        return info

    for attr, value in namespace:
        if attr.startswith("__") and attr.endswith("__"):
            continue
        kind = kind_of(value)
        entry: dict = {"kind": kind}
        if kind == "function":
            entry["signature"] = safe_signature(value)
            # __module__ は「元々どこで定義されたか」。
            # from ... import でコピーされてきた名前を見分ける手がかりになる。
            entry["module"] = getattr(value, "__module__", None)
            doc = getattr(value, "__doc__", None)
            lines = doc.strip().splitlines() if isinstance(doc, str) else []
            if lines:
                entry["doc"] = lines[0][:200]
        elif kind == "class":
            entry["module"] = getattr(value, "__module__", None)
            entry["detail"] = describe_class(value)
        elif kind == "module":
            entry["target"] = getattr(value, "__name__", None)
        else:
            entry["type"] = type(value).__name__
            if isinstance(value, (list, tuple, set, dict)):
                try:
                    entry["len"] = len(value)
                except Exception:
                    pass
                if isinstance(value, dict):
                    # 参照テーブルは中身が巨大なので、キーだけを控えめに記録する。
                    try:
                        entry["keys"] = [safe_repr(k) for k in list(value)[:MAX_CONST_ITEMS]]
                    except Exception:
                        pass
                else:
                    entry["repr"] = safe_repr(value)
            else:
                entry["repr"] = safe_repr(value)
        info["attributes"][attr] = entry
    return info


# --------------------------------------------------------------------------
# クラッシュログに出ていた箇所の調査
# --------------------------------------------------------------------------
BUG_PROBES = [
    ("kivy shutdown ArgumentError (47 crashes)",
     ["kivy.input.providers.wm_common", "kivy.input.providers.wm_pen",
      "kivy.input.providers.wm_touch"]),
    ("llama-server boot failure (35 crashes)",
     ["llama_cpp_runtime_completion", "__main__"]),
    ("llm request path (timings / response_format / resets)",
     ["scripts.llm.request_llm_inference_llama_cpp_completion",
      "scripts.llm.request_llm_inference_any_server"]),
]

# トレースバックやバグ報告に出てきた単語。
# 名前の部分一致で、関係のありそうな関数を洗い出す。
KEYWORDS = ["timings", "response_format", "facility_move_to", "sidecar",
            "send_request", "SetWindowLong", "crash", "llama", "structure",
            "retry", "json"]


def probe_bug_sites() -> list[str]:
    """クラッシュ地点まわりのモジュールを列挙し、キーワード検索の結果を添える。"""
    lines: list[str] = []
    for title, module_names in BUG_PROBES:
        lines.append("=" * 72)
        lines.append(title)
        lines.append("=" * 72)
        for mod_name in module_names:
            module = sys.modules.get(mod_name)
            if module is None:
                # ロードされていないこと自体が情報になる。
                # そのクラッシュは今のバージョンでは起こり得ない可能性がある。
                lines.append(f"  [not loaded] {mod_name}")
                continue
            lines.append(f"  [loaded] {mod_name}  file={getattr(module, '__file__', '?')} "
                         f"compiled={is_compiled(module)}")
            try:
                for attr, value in sorted(vars(module).items()):
                    if attr.startswith("__"):
                        continue
                    if inspect.isroutine(value):
                        lines.append(f"      def {attr}{safe_signature(value)}")
                    # そのモジュールで定義されたクラスだけを出す。
                    # import してきただけのクラスを並べても対象にはならない。
                    elif inspect.isclass(value) and getattr(value, "__module__", "") == mod_name:
                        lines.append(f"      class {attr}")
            except Exception:
                lines.append("      <vars() failed>")
        lines.append("")

    lines.append("=" * 72)
    lines.append("keyword sweep (game modules + kivy only)")
    lines.append("=" * 72)
    for keyword in KEYWORDS:
        hits: list[str] = []
        for mod_name, module in list(sys.modules.items()):
            if module is None:
                continue
            # 約 4200 モジュール全部を対象にすると結果が埋もれる（試したところ
            # "Literal" は標準ライブラリや sympy に 486 件当たった）。
            # パッチする可能性のある範囲だけに絞る。
            if not (is_game_module(mod_name) or mod_name.startswith("kivy.")):
                continue
            try:
                names = list(vars(module))
            except Exception:
                continue
            for attr in names:
                if keyword.lower() in attr.lower():
                    hits.append(f"{mod_name}:{attr}")
        lines.append(f"  {keyword} -> {len(hits)} hit(s)")
        for hit in hits[:25]:
            lines.append(f"      {hit}")
        if len(hits) > 25:
            lines.append(f"      ... {len(hits) - 25} more")
    return lines


# --------------------------------------------------------------------------
# 退避（ゲームが更新されたら、前回のリコンを zip で残す）
# --------------------------------------------------------------------------
# `out/recon/` は注入のたびに同じ名前で書き直される。素直な作りだが、**ゲームが
# 更新された瞬間に前の版のリコンが消える**ので、「何が増えて何が消えたか」を
# 機械的に出せなくなる。GAME.md §1.5 に、退避してあった main_023 → main_024 では
# 68 ターゲット増を出せて、退避が無かった main_024 → main_025 では出せなかった、
# という記録がそのまま残っている。ここはその退避を自動でやる。
#
# **退避の引き金は「ビルドが変わったこと」で、出力の中身の差ではない。**
# 中身は同じビルドでも走らせるたびに変わる。リコンは `sys.modules` を見るので、
# 起動直後なら 3452 モジュール、長時間プレイ後なら 4235 モジュールになる
# （同 §1.5）。中身の差を引き金にすると、同じ版のまま zip が毎回増えていき、
# 肝心の「版が変わった1回」が埋もれる。
#
# ビルドの見分けには次の3つを使う。
#
#     app_version    Epic の AppVersionString（main_025）。更新で必ず上がる
#     game_version   __main__.get_game_version()（014）。据え置かれることが多い
#     exe_size       instantale.exe の大きさ。Epic 以外の入手経路の保険
#
# `instantale.exe` の mtime は**使わない**。実測すると起動した時刻と同じ分に
# なっていることがあり、更新で動いたのか起動で動いたのか区別が付かない。
# 判定に混ぜると毎回退避が走りかねないので、控えとして `build.json` には
# 書くが比較には入れない。
#
# `exe_sha256` は exe の中身のハッシュ。版の名前が取れない入手経路（Steam など）で、
# 大きさの変わらない更新（中身だけの直し）も見分けるために足した（2026-10-16）。
IDENTITY_KEYS = ("app_version", "game_version", "exe_size", "python", "kivy", "llama_builds", "exe_sha256")

#: IDENTITY_KEYS のうち、取れないことがある鍵。どちらかが None なら比べない。
#: `game_version` はゲームの中に問い合わせて取るので、注入の時機によっては取れない。
#: `exe_size` は起動直後の注入で実行ファイルの場所が読めず None になったことがある（`_exe_path`）。
#: 版の変化は `app_version` が見る（こちらは読めなければ別のビルド扱いのまま）。
#: `python` / `kivy` / `llama_builds` は 2026-10-14 に足した鍵で、それより前の控えには無い
#: （「特異な変化」を拾うため。同梱の llama-server が替わって 127_ が落ちた。VERIFICATION.md §3.100）。
#: `exe_sha256` は 2026-10-16 に足した鍵で、それより前の控えには無い。
LATE_KEYS = ("game_version", "exe_size", "python", "kivy", "llama_builds", "exe_sha256")

#: exe のハッシュの控え（`recon_history\exe_digest.json`）。exe は 700MB ほどあり、ハッシュに1秒ほどかかるので、
#: 場所・大きさ・更新時刻が控えと同じなら控えの値を使う。
EXE_DIGEST_NAME = "exe_digest.json"
_DIGEST_ATTR = "__instantale_exe_digest__"

#: Epic Games Launcher が置いているマニフェスト。
#: `AppVersionString` がここにある。
#: ゲームを起動しなくても読める素性なので、ゲーム側の API に依存しない。
EPIC_MANIFEST_GLOB = os.path.join(
    os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
    "Epic", "EpicGamesLauncher", "Data", "Manifests", "*.item")


def _norm(path: str) -> str:
    """比較用にパスを均す。8.3 形式の短い名前を実体に戻す。

    `sys.executable` は `C:\\PROGRA~1\\EPICGA~1\\INSTAN~1\\instantale.exe` の形で返ってくることがあり（実測）、
    Epic のマニフェストにある
    `InstallLocation`（`C:\\Program Files\\Epic Games\\Instantaleq6Ve7`）と字面が合わない。
    """
    try:
        return os.path.normcase(os.path.realpath(path))
    except Exception:
        return os.path.normcase(path)


def epic_app_version(exe_path: str) -> str | None:
    """Epic の `AppVersionString`（`main_025`）を返す。読めなければ None。

    複数のゲームが入っていることがあるので、
    `InstallLocation` が**今動いている実行ファイルの置き場所と一致するもの**だけを採る。
    表示名で当てにいくと、別のタイトルの版を自分の版として書き残す事故になる。
    """
    game_dir = _norm(os.path.dirname(exe_path))
    for item in glob.glob(EPIC_MANIFEST_GLOB):
        try:
            with open(item, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue        # 別タイトルのマニフェストが1つ壊れていても続ける
        location = data.get("InstallLocation")
        if not location or _norm(str(location)) != game_dir:
            continue
        version = data.get("AppVersionString")
        return str(version) if version else None
    return None


def game_version() -> str | None:
    """ゲーム自身が名乗るバージョン（`014`）。取れなければ None。

    Epic の `AppVersion` とは別系統で、更新しても据え置かれることがある（GAME.md
    §1.4）。
    だから**どちらか一方では足りず、両方を控える**。
    """
    main = sys.modules.get("__main__")
    getter = getattr(main, "get_game_version", None)
    if getter is None:
        return None
    try:
        return str(getter())
    except Exception:
        return None


def build_identity(snap_dir: str | None = None) -> dict:
    """今動いているビルドの素性。`build.json` に書き、退避の判定にも使う。

    どの項目も取れなくてよい（取れなければ None が入る）。
    ここで例外を投げると、素性が分からないというだけでリコン全体が止まってしまう。
    `snap_dir` を渡すと、exe のハッシュの控えをそこに置いて次の起動で使う。
    """
    exe = _exe_path()
    size = mtime = None
    stat = None
    try:
        stat = os.stat(exe)
        size = stat.st_size
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    except Exception:
        pass
    try:
        app_version = epic_app_version(exe)
    except Exception:
        log_exc("recon: cannot read the Epic manifest")
        app_version = None
    return {
        "app_version": app_version,
        "game_version": game_version(),
        "executable": exe,
        "exe_size": size,
        "exe_mtime": mtime,        # 控えるだけ。IDENTITY_KEYS には入れない
        "python": sys.version.split()[0],
        "kivy": _kivy_version(),
        "llama_builds": _llama_builds(exe),
        "exe_sha256": exe_digest(exe, stat, snap_dir) if stat is not None else None,
    }


def exe_digest(exe: str, stat, snap_dir: str | None = None) -> str | None:
    """exe の sha256。同じプロセスでは1回だけ数える。`snap_dir` の控えと場所・大きさ・更新時刻が同じなら控えを使う。"""
    key = [os.path.normcase(exe), stat.st_size, stat.st_mtime_ns]
    cache = getattr(sys, _DIGEST_ATTR, None)
    if isinstance(cache, dict) and cache.get("key") == key:
        return cache.get("sha256")
    path = os.path.join(snap_dir, EXE_DIGEST_NAME) if snap_dir else None
    stored = _read_json_file(path) if path else None
    if isinstance(stored, dict) and stored.get("key") == key and stored.get("sha256"):
        digest = stored["sha256"]
    else:
        digest = _digest(exe)
        if digest and path:
            try:
                os.makedirs(snap_dir, exist_ok=True)
                write_json(path, {"key": key, "sha256": digest}, indent=1)
            except Exception:
                log_exc("recon: cannot keep the exe digest")
    if digest:
        setattr(sys, _DIGEST_ATTR, {"key": key, "sha256": digest})
    return digest


#: 版の鍵（`build_key`）に使う素性。注入の時点でいつも取れるものだけ
#: （`game_version` はゲームの組み上がりの前だと取れず、Kivy は読み込みの前だと取れない）。
#: Python と Kivy は exe に入っているので、変われば `exe_size` と `exe_sha256` も変わる。
#: `exe_sha256` は取れたときだけ頭の12文字を末尾に足す（足す前の控えの鍵と形を変えないため）。
BUILD_KEY_KEYS = ("app_version", "exe_size", "llama_builds")


def build_key(identity: dict | None = None, snap_dir: str | None = None) -> str | None:
    """版を表す短い鍵。ほかの部品（`drift`）が版ごとの控えの見出しに使う。どれも取れなければ None。

    「同じビルドか」の判定（`_same_build`）と食い違わないよう、ここに1つだけ置く。
    """
    identity = identity if identity is not None else build_identity(snap_dir)
    values = [identity.get(key) for key in BUILD_KEY_KEYS]
    digest = identity.get("exe_sha256")
    if all(value is None for value in values) and not digest:
        return None
    key = "|".join("?" if value is None else str(value) for value in values)
    return "{}|{}".format(key, str(digest)[:12]) if digest else key


def _exe_path() -> str:
    """今動いているプロセスの実行ファイル。

    起動直後の注入では `sys.executable` が在りもしない `<ゲームのフォルダ>\\python.exe` を指していて、
    同じダンプの途中で `instantale.exe` に変わった（2026-10-15 の実機。Nuitka の起動処理の途中）。
    そのまま使うと `exe_size` が取れず、同じ版を別のビルドと見なして退避した。
    Windows ではプロセスの実体を `GetModuleFileNameW` で聞く。聞けなければ `sys.executable`。
    """
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
        if length and os.path.isfile(buffer.value):
            return buffer.value
    except Exception:
        pass
    return sys.executable or ""


def _kivy_version() -> str | None:
    """ゲームに同梱の Kivy の版。読み込まれていなければ None（ゲームの外）。"""
    kivy = sys.modules.get("kivy")
    version = getattr(kivy, "__version__", None) if kivy is not None else None
    return str(version) if version else None


def _llama_builds(exe: str) -> str | None:
    """ゲームに同梱の llama-server の版（`bin\\llama-b10642-bin-win-cuda-12.4-x64` などのフォルダ名を並べたもの）。

    ゲームの外・`bin\\` が無いときは None。
    """
    if not exe or not os.path.basename(exe).lower().startswith("instantale"):
        return None
    try:
        names = sorted(name for name in os.listdir(os.path.join(os.path.dirname(exe), "bin"))
                       if name.lower().startswith("llama-"))
    except Exception:
        return None
    return ",".join(names) if names else None


def _same_build(old: dict, new: dict) -> bool:
    """同じビルドを見たリコンか。

    **控えが無い（`old` が空）ときは False。**
    「前回と同じだと確かめられない」のであって「同じ」ではない。
    ここを True に倒すと、この仕組みが入る前から `out/recon/` に残っていた最後の1回が、
    確かめないまま上書きされる。
    """
    if not old:
        return False
    for key in IDENTITY_KEYS:
        before, now = old.get(key), new.get(key)
        # ゲームに問い合わせる鍵は、注入が `__main__` の組み上がりより早いと取れない（None）。
        # 取れなかった側があるときは比べない。比べると同じ版でも注入のたびに退避が2本ずつ増え、
        # 上限で古い版の控えが押し出される（GAME.md §1.1）。
        if key in LATE_KEYS and (before is None or now is None):
            continue
        if before != now:
            return False
    return True


def _digest(path: str) -> str | None:
    """ファイルの sha256。退避を開かずに「中身が動いたか」を見るための控え。"""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def _snapshot_name(recon_dir: str, old: dict) -> str:
    """退避の名前。`<版>_YYYYMMDD` の形。

    日付は**退避するリコンを取った日**で、今日ではない。
    更新前に取ったダンプを更新後に退避するなら、
    名前に入るのは取った日のほうでなければならない。
    そうしないと、
    後から並べたときに「いつのゲームを見たダンプなのか」が分からなくなる。

    版が読めない（この仕組みが入る前のダンプ）ときは `unknown` にする。
    当て推量の版を付けるくらいなら、分からないと書いてあるほうが後で困らない。
    """
    version = str(old.get("app_version") or old.get("game_version") or "unknown")
    stamp = str(old.get("written") or "")[:10].replace("-", "")
    if len(stamp) != 8 or not stamp.isdigit():
        # 控えが無いときは、書き出したファイルの更新時刻から日付を拾う。
        times = []
        for name in OUTPUT_FILES:
            try:
                times.append(os.path.getmtime(os.path.join(recon_dir, name)))
            except OSError:
                pass
        when = datetime.datetime.fromtimestamp(max(times)) if times else datetime.datetime.now()
        stamp = when.strftime("%Y%m%d")
    # 版に区切りや空白が混ざってもファイル名にできるようにしておく。
    version = "".join(c if (c.isalnum() or c in "._-") else "_" for c in version)
    return "{}_{}".format(version, stamp)


def _unique_zip_path(snap_dir: str, base: str) -> str:
    """空いている `<base>.zip` を返す。埋まっていれば `_2`, `_3` … と足す。

    同じ版・同じ日に2回退避が要ることは滅多に無いが、
    起きたときに**黙って上書きするのが一番まずい**（残すために作った仕組みが消す側に回る）。
    """
    path = os.path.join(snap_dir, base + ".zip")
    index = 2
    while os.path.exists(path):
        path = os.path.join(snap_dir, "{}_{}.zip".format(base, index))
        index += 1
    return path


def snapshot_dir(out_dir: str) -> str:
    """退避先。`out/` の1つ上（配布フォルダ直下）の `recon_history/`。"""
    return os.path.join(os.path.dirname(os.path.abspath(out_dir)), SNAPSHOT_DIR_NAME)


def migrate_old_snapshots(out_dir: str, snap_dir: str) -> int:
    """前の退避先 `out/recon_snapshots/` の中身を `snap_dir` へ移す。移した数を返す。**例外は投げない。**

    名前が埋まっていれば `_unique_zip_path` と同じく `_2` … を足して、どちらも残す。
    空になったら前の退避先を消す。
    """
    old_dir = os.path.join(out_dir, OLD_SNAPSHOT_DIR_NAME)
    if not os.path.isdir(old_dir):
        return 0
    moved = 0
    try:
        os.makedirs(snap_dir, exist_ok=True)
        for name in sorted(os.listdir(old_dir)):
            source = os.path.join(old_dir, name)
            if name.endswith(".zip"):
                target = _unique_zip_path(snap_dir, name[:-len(".zip")])
            else:
                target = os.path.join(snap_dir, name)
                index = 2
                while os.path.exists(target):
                    target = os.path.join(snap_dir, "{}_{}".format(name, index))
                    index += 1
            try:
                os.replace(source, target)
                moved += 1
            except Exception:
                log_exc("recon: cannot move {} out of {}".format(name, old_dir))
        if not os.listdir(old_dir):
            os.rmdir(old_dir)
        if moved:
            log("recon: moved {} snapshot(s) from {} to {}".format(moved, old_dir, snap_dir))
    except Exception:
        log_exc("recon: cannot move the old snapshots")
    return moved


def archive_previous(recon_dir: str, identity: dict, snap_dir: str = None) -> str | None:
    """`out/recon/` に残っている前回のダンプを、別ビルドのものなら zip で退避する。

    **上書きする前に呼ぶこと。**
    退避したら zip のパスを返し、退避しなかった（初回・同じビルド・退避に失敗）なら
    None を返す。

    失敗しても例外は投げない。
    退避はリコンの付随物で、
    これが原因で調査そのものが止まるほうが損害が大きい（ローダ全体の作法と同じ）。
    """
    present = [name for name in OUTPUT_FILES
               if os.path.isfile(os.path.join(recon_dir, name))]
    if not present:
        return None          # 初回。残すものが無い

    old: dict = {}
    try:
        with open(os.path.join(recon_dir, BUILD_NAME), "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            old = loaded
    except FileNotFoundError:
        pass                 # この仕組みが入る前のダンプ。_same_build が False に倒す
    except Exception:
        log_exc("recon: cannot read {}".format(BUILD_NAME))

    if _same_build(old, identity):
        log("recon: same build as the previous dump ({}); overwriting in place".format(
            old.get("app_version") or old.get("game_version") or "?"))
        return None

    if snap_dir is None:
        snap_dir = snapshot_dir(os.path.dirname(recon_dir))
    kept = _duplicate_of(recon_dir, snap_dir)
    if kept:
        log("recon: the previous dump is already kept as {}; not archiving again".format(
            os.path.basename(kept)))
        return None
    tmp = None
    try:
        os.makedirs(snap_dir, exist_ok=True)
        path = _unique_zip_path(snap_dir, _snapshot_name(recon_dir, old))
        # 隣に書いてから差し替える。
        # 書いている途中で落ちても、壊れた
        # zip が「退避済み」の顔で残らない（`write_text()` と同じ作法）。
        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in present + [BUILD_NAME]:
                source = os.path.join(recon_dir, name)
                if os.path.isfile(source):
                    archive.write(source, name)
        os.replace(tmp, path)
        _prune_snapshots(snap_dir)
    except Exception:
        log_exc("recon: cannot archive the previous dump")
        if tmp:
            try:
                os.remove(tmp)      # 書きかけを残さない
            except Exception:
                pass
        return None
    return path


def identity_changes(old: dict, new: dict) -> list:
    """素性の違い（`app_version: main_025 -> main_026` の形の行）。`_same_build` と同じく、取れなかった鍵は比べない。"""
    lines = []
    for key in IDENTITY_KEYS:
        before, now = old.get(key), new.get(key)
        if key in LATE_KEYS and (before is None or now is None):
            continue
        if before != now:
            if key == "exe_sha256":
                before, now = str(before)[:12], str(now)[:12]
            lines.append("{}: {} -> {}".format(key, before, now))
    return lines


def _load_signatures(dump_dir: str) -> dict | None:
    """`signatures.json` を読む。無い・読めなければ None（2026-10-14 より前のダンプ）。"""
    try:
        with open(os.path.join(dump_dir, SIGNATURES_NAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def function_changes(old: dict, new: dict) -> list:
    """関数の変化（`removed` / `added` / `changed` の行）。`function_diff` を行にしたもの。"""
    lines = []
    for item in function_diff(old, new):
        if item["kind"] == "changed":
            lines.append("changed {}{} -> {}".format(item["key"], item["before"], item["now"]))
        elif item["kind"] == "removed":
            lines.append("removed {}{}".format(item["key"], item["before"]))
        else:
            lines.append("added {}{}".format(item["key"], item["now"]))
    return lines


def function_diff(old: dict, new: dict) -> list:
    """関数の変化を `{"kind": removed/added/changed, "key", "before", "now"}` の並びで返す。

    比べるのは `old` に在ったモジュールの中だけ。
    LLM や画像生成のモジュールは遊び始めるまで読み込まれないので、`new` にだけ在るモジュールは
    「増えた」のではなく「まだ読んでいなかった」ことがある。
    """
    def ours(key):
        return key.rsplit(":", 1)[-1].rsplit(".", 1)[-1].startswith(MOD_NAME_PREFIXES)

    # 2026-10-15 より前に書いた `signatures.json` には、ローダの置いた名前が入っていることがある。
    old = {key: value for key, value in old.items() if not ours(key)}
    new = {key: value for key, value in new.items() if not ours(key)}
    modules = {key.split(":", 1)[0] for key in old}
    loaded = {key.split(":", 1)[0] for key in new}
    items = []
    for key in sorted(old):
        # 今回読み込まれていないモジュールの関数は「消えた」と数えない。LLM の接続先で読み込むモジュールが違う
        # （ローカルの回だけ `llama_cpp_runtime_completion` が載る）。2026-10-15 にクラウドへ替えた回で40件を誤って数えた。
        if key not in new:
            if key.split(":", 1)[0] in loaded:
                items.append({"kind": "removed", "key": key, "before": old[key], "now": None})
        elif new[key] != old[key]:
            items.append({"kind": "changed", "key": key, "before": old[key], "now": new[key]})
    for key in sorted(new):
        if key not in old and key.split(":", 1)[0] in modules:
            items.append({"kind": "added", "key": key, "before": None, "now": new[key]})
    return items


def _read_build(dump_dir: str) -> dict:
    try:
        with open(os.path.join(dump_dir, BUILD_NAME), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _zip_dump(dump_dir: str, snap_dir: str, build: dict, reasons: list) -> str | None:
    """`dump_dir` の一式を `<版>_<日付>.zip` に固めて `snap_dir` へ置く。理由を `REASON_NAME` に添える。"""
    tmp = None
    try:
        os.makedirs(snap_dir, exist_ok=True)
        path = _unique_zip_path(snap_dir, _snapshot_name(dump_dir, build))
        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in list(OUTPUT_FILES) + [BUILD_NAME]:
                source = os.path.join(dump_dir, name)
                if os.path.isfile(source):
                    archive.write(source, name)
            archive.writestr(REASON_NAME, "\n".join(reasons) + "\n")
        os.replace(tmp, path)
        _prune_snapshots(snap_dir)
        return path
    except Exception:
        log_exc("recon: cannot archive {}".format(dump_dir))
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
        return None


def update_current(recon_dir: str, snap_dir: str) -> str | None:
    """書き終えたダンプを、今のビルドの基準（`snap_dir/current/`）と比べる。退避したら zip のパスを返す。**例外は投げない。**

    - 基準が無い: 今回のダンプを基準にする
    - 素性が違う（版・同梱の llama-server・Python・Kivy などが替わった）: 基準を退避し、今回のダンプを基準にする
    - 素性が同じで、今回のほうが読み込んだモジュールが同じか多い: 関数を比べ、変わっていれば基準を退避する。今回のダンプを基準にする
    - 今回のほうが少ない（起動直後で読み込みの途中）: 比べない。基準はそのまま

    退避するのは基準のほう（そのビルドでいちばん多く読み込んだ回）で、`out/recon/` の起動直後の少ないダンプではない。
    """
    current = os.path.join(snap_dir, CURRENT_DIR_NAME)
    new = _read_build(recon_dir)
    old = _read_build(current)
    reasons: list = []
    replace = True
    if old:
        if not _same_build(old, new):
            reasons = ["build changed"] + identity_changes(old, new)
        elif (new.get("modules") or 0) >= (old.get("modules") or 0):
            before, after = _load_signatures(current), _load_signatures(recon_dir)
            if before is not None and after is not None:
                changes = function_changes(before, after)
                # 今回のほうが多く読み込んでいて「増えた」だけなら、読み込みの途中だった基準と比べただけ
                # （起動直後は `__main__` を組み立てている途中）。消えた・変わったが1つでもあれば退避する。
                more_loaded = (new.get("modules") or 0) > (old.get("modules") or 0)
                if changes and not (more_loaded and all(line.startswith("added ") for line in changes)):
                    reasons = ["functions changed ({})".format(len(changes))] + changes
        else:
            replace = False
            # 置き換えない回でも、ハッシュを持たない基準（2026-10-16 より前に作った基準）には書き足す。
            # 基準は長く遊んだ回のもので、起動直後の走査では置き換わらないので、書き足さないと次の版で
            # ハッシュを比べられない（大きさの変わらない更新を見落とす）。
            if old.get("exe_sha256") is None and new.get("exe_sha256"):
                try:
                    write_json(os.path.join(current, BUILD_NAME), dict(old, exe_sha256=new["exe_sha256"]), indent=1)
                except Exception:
                    log_exc("recon: cannot add the exe digest to the reference dump")
    archived = None
    if reasons:
        # 同じビルドで中身を全部含む記録が既にあれば、もう1本は作らない。
        kept = _duplicate_of(current, snap_dir)
        if kept:
            log("recon: the reference dump is already kept as {} ({}); not archiving again".format(
                os.path.basename(kept), reasons[0]))
        else:
            archived = _zip_dump(current, snap_dir, old, reasons)
            if archived:
                log("recon: archived the reference dump to {} ({})".format(archived, reasons[0]))
    detected = None
    if replace:
        previous_changes = _read_json_file(os.path.join(current, CHANGES_NAME))
        detected = previous_changes if isinstance(previous_changes, dict) else None
        try:
            staging = current + ".tmp"
            if os.path.isdir(staging):
                shutil.rmtree(staging)
            os.makedirs(staging)
            for name in list(OUTPUT_FILES) + [BUILD_NAME]:
                source = os.path.join(recon_dir, name)
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(staging, name))
            if os.path.isdir(current):
                shutil.rmtree(current)
            os.replace(staging, current)
        except Exception:
            log_exc("recon: cannot update the reference dump")
    # 前の版との比べは、基準を置き換えたときと、まだ比べていないとき（この仕組みより前に作った基準）に行う。
    # 置き換えない回（起動直後で読み込みが少ない）でも、`changes.json` が無ければ今の基準で比べる。
    if replace or not os.path.isfile(os.path.join(current, CHANGES_NAME)):
        try:
            write_build_changes(snap_dir, detected)
        except Exception:
            log_exc("recon: cannot compare with the previous build")
    return archived


def _read_json_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def previous_build(snap_dir: str, build: dict):
    """`build` と別のビルドの記録のうち、いちばん新しく退避された版の、関数をいちばん多く含む記録。

    `(zip のパス, build.json, 関数の一覧)` か None。古い形の記録（フォルダ）は見ない。
    """
    group = _previous_group(snap_dir, build)
    if not group:
        return None
    path, (old_build, functions) = max(group, key=lambda item: len(item[1][1]))
    return path, old_build, functions


def _previous_group(snap_dir: str, build: dict) -> list | None:
    """`previous_build` の版の記録すべて（`[(zip のパス, (build.json, 関数の一覧)), ...]`）。"""
    try:
        names = [name for name in os.listdir(snap_dir) if name.endswith(".zip")]
    except OSError:
        return None
    groups: dict = {}
    for name in names:
        path = os.path.join(snap_dir, name)
        summary = _dump_summary(path)
        if not summary or not summary[0] or _same_build(summary[0], build):
            continue
        key = build_key(summary[0]) or name
        groups.setdefault(key, []).append((path, summary))
    if not groups:
        return None

    def when(path):
        # 名前の日付（`<版>_<日付>[_N].zip`）を先に見る。更新時刻だけだと、同じ秒に作った記録の順が揺れる
        found = re.search(r"_(\d{8})(?:_\d+)?\.zip$", os.path.basename(path))
        return (found.group(1) if found else "", os.path.getmtime(path))

    return max(groups.values(), key=lambda group: max(when(p) for p, _s in group))


def build_changes(snap_dir: str) -> dict | None:
    """今の基準（`current/`）と前の版の記録の関数を比べる。前の版の記録が無ければ None。

    比べるのは `targets.txt`（どの記録にもある。`signatures.json` は 2026-10-14 より前の記録に無い）。
    """
    current = os.path.join(snap_dir, CURRENT_DIR_NAME)
    mine = _dump_summary(current)
    if not mine or not mine[0]:
        return None
    group = _previous_group(snap_dir, mine[0])
    if not group:
        return None
    path, (old_build, functions) = max(group, key=lambda item: len(item[1][1]))
    # 前の版の記録のうち最後に作ったもの（版が替わった最初の起動で退避した記録）の時刻
    archived = max(os.path.getmtime(p) for p, _s in group)
    return {"since": os.path.basename(path),
            "since_build": {key: old_build.get(key) for key in IDENTITY_KEYS},
            "build": build_key(mine[0]),
            "identity": identity_changes(old_build, mine[0]),
            "functions": function_diff(functions, mine[1]),
            "archived_at": datetime.datetime.fromtimestamp(archived).isoformat(timespec="seconds")}


def write_build_changes(snap_dir: str, before: dict | None = None) -> dict | None:
    """`build_changes` を `current/changes.json` に書く。

    見つけた日時（`detected_at`）は、前と同じ版との比べなら引き継ぐ。初めて書くときは前の版の記録を退避した時刻
    （版が替わった最初の起動）にする。`changes.json` より前のローダで更新をまたいだときに、
    ローダを入れ替えた日を「更新された日」と取り違えないため。
    """
    changes = build_changes(snap_dir)
    if changes is None:
        return None
    if before and before.get("since") == changes["since"] and before.get("detected_at"):
        changes["detected_at"] = before["detected_at"]
    else:
        changes["detected_at"] = changes.get("archived_at") or datetime.datetime.now().isoformat(timespec="seconds")
    write_json(os.path.join(snap_dir, CURRENT_DIR_NAME, CHANGES_NAME), changes, indent=1)
    counts = {}
    for item in changes["functions"]:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    log("recon: compared with {}: {}".format(
        changes["since"], ", ".join("{} {}".format(n, kind) for kind, n in sorted(counts.items()))
        or "no function changed"))
    return changes


def _snapshot_version(name: str) -> str:
    """退避の名前（`<版>_YYYYMMDD[_N].zip`）から版を取り出す。形が違えば名前そのもの（それだけで1つの版と数える）。"""
    match = re.match(r"^(.+)_\d{8}(?:_\d+)?\.zip$", name)
    return match.group(1) if match else name


def _parse_targets(text: str) -> dict:
    """`targets.txt` の中身を `{"module:qualname": "(引数)"}` にする。"""
    functions = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cut = line.find("(")
        if cut > 0:
            name = line[:cut]
            if name.rsplit(":", 1)[-1].rsplit(".", 1)[-1].startswith(MOD_NAME_PREFIXES):
                continue                        # ローダ・MOD が置いた名前（MOD_NAME_PREFIXES）
            functions[name] = line[cut:]
    return functions


def _dump_summary(source: str):
    """記録（zip かフォルダ）の (build.json, 関数の一覧)。読めなければ None。

    関数は `targets.txt` で比べる。`signatures.json` は 2026-10-14 より前の記録に無いので、
    古い記録と並べて比べられない。
    """
    try:
        if source.endswith(".zip"):
            with zipfile.ZipFile(source) as archive:
                names = set(archive.namelist())
                build = json.loads(archive.read(BUILD_NAME).decode("utf-8")) if BUILD_NAME in names else {}
                text = archive.read("targets.txt").decode("utf-8")
        else:
            build = _read_build(source)
            with open(os.path.join(source, "targets.txt"), "r", encoding="utf-8") as fh:
                text = fh.read()
    except Exception:
        return None
    return (build if isinstance(build, dict) else {}), _parse_targets(text)


def _covers(bigger: dict, smaller: dict) -> bool:
    """`smaller` の関数が全部、同じ引数で `bigger` にあるか。"""
    return all(bigger.get(key) == value for key, value in smaller.items())


def _duplicate_of(source: str, snap_dir: str, summary=None):
    """`source` と同じビルドで、関数を全部（同じ引数で）含む記録が `snap_dir` に既にあれば、その zip を返す。"""
    mine = summary or _dump_summary(source)
    if not mine or not mine[0]:
        return None
    try:
        names = sorted(os.listdir(snap_dir))
    except OSError:
        return None
    for name in names:
        path = os.path.join(snap_dir, name)
        if not name.endswith(".zip") or os.path.abspath(path) == os.path.abspath(source):
            continue
        theirs = _dump_summary(path)
        if theirs and theirs[0] and _same_build(theirs[0], mine[0]) and _covers(theirs[1], mine[1]):
            return path
    return None


def _redundant(paths: list) -> set:
    """重複している記録。同じビルドの別の記録に、関数が全部（同じ引数で）含まれているもの。

    起動直後の読み込み途中の記録（全部読み込んだ記録に含まれる）と、中身の同じ記録が当たる。
    関数が消えた・変わった記録はどちらにも含まれないので残る。
    中身が同じ記録どうしは、大きいほう（同じなら新しいほう）を残す。
    build.json の無い記録は比べない（残す）。
    """
    infos = {path: _dump_summary(path) for path in paths}

    def rank(path):
        return (os.path.getsize(path), os.path.getmtime(path), path)

    drop = set()
    for a in paths:
        info_a = infos[a]
        if not info_a or not info_a[0]:
            continue
        for b in paths:
            info_b = infos[b]
            if b == a or not info_b or not info_b[0]:
                continue
            if not (_same_build(info_a[0], info_b[0]) and _covers(info_b[1], info_a[1])):
                continue
            if info_a[1] != info_b[1] or rank(b) > rank(a):
                drop.add(a)
                break
    return drop


def _keepers(paths: list) -> set:
    """版ごとに消さない1本。いちばん大きい zip（＝いちばん多くのモジュールを見たダンプ）、同じ大きさなら新しいほう。

    同じ版でも、起動直後の注入で取ったダンプはゲームの読み込みの途中で中身が少ない（main_026 で 65 KB と 350 KB）。
    新しい順で選ぶと、少ないほうが残ることがある。
    """
    best = {}
    for path in paths:
        version = _snapshot_version(os.path.basename(path))
        key = (os.path.getsize(path), os.path.getmtime(path), path)
        if version not in best or key > best[version][0]:
            best[version] = (key, path)
    return {path for _key, path in best.values()}


def _prune_snapshots(snap_dir: str, keep: int = None) -> int:
    """古い退避を落とす。落とした数を返す。**例外は投げない。**

    先に重複（`_redundant`）を落とす。
    残りのうち版ごとの1本（`_keepers`）は消さない。上限 `keep` で数えて落とすのは、それ以外（同じ版の2本目以降）の古いものから。
    退避は調査のための控えなので、消せなくても支障は無い。
    ここで投げると、書けたばかりの退避を「失敗」に見せてしまう。
    """
    keep = SNAPSHOT_KEEP if keep is None else keep
    try:
        if keep <= 0:
            return 0
        entries = [os.path.join(snap_dir, name)
                   for name in os.listdir(snap_dir)
                   if name.endswith(".zip")]
        dropped = 0
        duplicates = _redundant(entries)
        for path in sorted(duplicates):
            try:
                os.remove(path)
                dropped += 1
            except Exception:
                pass
        if duplicates:
            log("recon: dropped {} duplicate snapshot(s)".format(len(duplicates)))
        entries = [path for path in entries if path not in duplicates]
        protected = _keepers(entries)
        spare = [path for path in entries if path not in protected]
        if len(spare) <= keep:
            return dropped
        spare.sort(key=lambda path: (os.path.getmtime(path), path))
        for path in spare[:len(spare) - keep]:
            try:
                os.remove(path)
                dropped += 1
            except Exception:
                pass
        log("recon: pruned old snapshot(s); keeping {}".format(keep))
        return dropped
    except Exception:
        log_exc("recon: cannot prune the snapshots")
        return 0


def _write_build(recon_dir: str, identity: dict, counts: dict) -> None:
    """`build.json` を書く。**全部書き終えてから**呼ぶこと。

    中身の sha256 まで控えるのは、次に見るときに
    zip を開かなくても「本当に中身が動いたのか」を確かめられるようにするため（退避の判定には使わない。
    理由はこの節の頭にある）。
    """
    record = dict(identity)
    record["written"] = datetime.datetime.now().isoformat(timespec="seconds")
    record.update(counts)
    record["files"] = {name: _digest(os.path.join(recon_dir, name))
                       for name in OUTPUT_FILES
                       if os.path.isfile(os.path.join(recon_dir, name))}
    # 壊れない書き方で書く。途中で落ちて読めない控えが残ると、次の回は
    # 「別のビルド」と判定して要らない退避を1つ増やす。
    write_json(os.path.join(recon_dir, BUILD_NAME), record, indent=1)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def dump(out_dir: str, *, backup: bool = True) -> str:
    """out_dir/recon/ に成果物一式を書き出し、そのパスを返す。

    `backup=True` なら、前回のダンプが**別のビルドを見たものだった場合に限り** 上書きする前に
    `recon_history/<版>_<日付>.zip`（`out/` の1つ上）へ退避する（ビルドの見分け方と、
    中身の差を引き金にしない理由は「退避」の節にある）。
    """
    recon_dir = os.path.join(out_dir, "recon")
    os.makedirs(recon_dir, exist_ok=True)

    # 素性は書き出す前に取る。
    # 退避の判定に使うので、**上書きより先**でなければ前回の
    # `build.json` がもう残っていない。
    snap_dir = snapshot_dir(out_dir)
    identity = build_identity(snap_dir)
    if backup:
        migrate_old_snapshots(out_dir, snap_dir)
        # 基準（`update_current`）がまだ無い頃は、前回のダンプを素性だけで見て退避する。
        if not os.path.isfile(os.path.join(snap_dir, CURRENT_DIR_NAME, BUILD_NAME)):
            archived = archive_previous(recon_dir, identity, snap_dir)
            if archived:
                log("recon: archived the previous dump to {}".format(archived))

    # 走査の途中で sys.modules が変化しても壊れないよう、先にコピーを取る。
    # 以降は `sys.modules` を直に引かず、この写し（`modules_by_name`）から引く。
    # 走査中にモジュールが消えると KeyError で dump 全体が止まるため。
    snapshot = [(name, mod) for name, mod in list(sys.modules.items()) if mod is not None]
    modules_by_name = dict(snapshot)
    game_names = sorted(name for name, _ in snapshot if is_game_module(name))

    log("recon: {} modules loaded, {} classified as game code".format(
        len(snapshot), len(game_names)))

    # -- modules.json ------------------------------------------------------
    inventory = []
    for name, module in sorted(snapshot):
        # 1つのモジュールで失敗しても全体を止めない。
        try:
            inventory.append(describe_module(name, module, deep=is_game_module(name)))
        except Exception:
            log_exc("recon: describe_module failed for {}".format(name))
    with open(os.path.join(recon_dir, "modules.json"), "w", encoding="utf-8") as fh:
        # default=safe_repr にしておくと、JSON にできない値も捨てずに文字列で残る。
        json.dump(inventory, fh, ensure_ascii=False, indent=1, default=safe_repr)

    # -- summary.txt -------------------------------------------------------
    compiled = sum(1 for _n, m in snapshot if is_compiled(m))
    summary = [
        "Instantale recon",
        "=" * 72,
        # どのビルドを見たダンプなのかを先頭に出す。
        # 退避した zip を後から開いたときに、
        # 機械可読の `build.json` を見なくても分かるように。
        f"app version   : {identity.get('app_version') or '?'}  (Epic manifest)",
        f"game version  : {identity.get('game_version') or '?'}  (get_game_version)",
        f"python        : {sys.version}",
        f"executable    : {sys.executable}",
        f"prefix        : {sys.prefix}",
        f"cwd           : {os.getcwd()}",
        f"pid           : {os.getpid()}",
        f"modules       : {len(snapshot)} ({compiled} Nuitka-compiled)",
        f"game modules  : {len(game_names)}",
        "",
        "sys.path",
        "-" * 72,
    ]
    summary += [f"  {p}" for p in sys.path]
    summary += ["", "game modules", "-" * 72]
    for name in game_names:
        module = modules_by_name[name]
        summary.append("  {:<58} {}".format(
            name, os.path.basename(str(getattr(module, "__file__", "?")))))
    summary += ["", "top-level non-game packages", "-" * 72]
    # ゲーム以外は名前だけを6個ずつ横に並べる。
    # 一覧として眺めやすくするため。
    tops = sorted({n.split(".")[0] for n, _ in snapshot if not is_game_module(n)})
    summary += ["  " + ", ".join(tops[i:i + 6]) for i in range(0, len(tops), 6)]
    _write(os.path.join(recon_dir, "summary.txt"), summary)

    # -- game_modules.txt --------------------------------------------------
    # 擬似的なソース一覧。
    # 本物のソースが無い以上、これが一番コードに近い出力になる。
    detail: list[str] = []
    for name in game_names:
        module = modules_by_name[name]
        detail.append("=" * 72)
        detail.append(f"{name}   (file={getattr(module, '__file__', '?')}, "
                      f"compiled={is_compiled(module)})")
        detail.append("=" * 72)
        try:
            entries = sorted(vars(module).items())
        except Exception:
            detail.append("  <vars() failed>")
            continue
        for attr, value in entries:
            if attr.startswith("__") and attr.endswith("__"):
                continue
            kind = kind_of(value)
            if kind == "function":
                detail.append(f"  def {attr}{safe_signature(value)}")
            elif kind == "class":
                detail.append(f"  class {attr}:")
                try:
                    for mname, mvalue in sorted(vars(value).items()):
                        if mname.startswith("__") and mname not in ("__init__", "__call__"):
                            continue
                        if inspect.isroutine(mvalue):
                            detail.append(f"      def {mname}{safe_signature(mvalue)}")
                        elif isinstance(mvalue, (staticmethod, classmethod)):
                            detail.append(f"      def {mname}{safe_signature(mvalue.__func__)}"
                                          f"  # {type(mvalue).__name__}")
                        elif isinstance(mvalue, property):
                            detail.append(f"      {mname}  # property")
                except Exception:
                    detail.append("      <vars() failed>")
            elif kind == "module":
                detail.append(f"  import {attr} -> {getattr(value, '__name__', '?')}")
            else:
                detail.append(f"  {attr} = {safe_repr(value)}")
        detail.append("")
    _write(os.path.join(recon_dir, "game_modules.txt"), detail)

    # -- targets.txt -------------------------------------------------------
    targets: list[str] = [
        "# Pasteable patch targets. Use with:",
        "#     @ctx.wrap(\"module:qualname\")",
        "#     @ctx.patch(\"module:qualname\")",
        "",
    ]
    # 関数の変化を見るための控え（`signatures.json`）。MOD とローダが足した関数は入れない
    # （MOD を入れ替えただけで「関数が変わった」と見なさないため）。
    signatures: dict[str, str] = {}

    def own(value, expected: str) -> bool:
        """比べる対象か。MOD・ローダが足した関数と、その場所で定義されていない関数（後から代入された関数、
        よそから import した関数）は外す。ゲームは遊び始めてから `Item.set_callback` で `Item` に関数を
        代入するので、読み込みの量で「増えた」に見えた（2026-10-14 の実物）。
        ローダの包みは `functools.wraps` で元の名前を写すので、包まれた関数は外れない。
        """
        if str(getattr(value, "__module__", "") or "").startswith(MOD_ORIGINS):
            return False
        if expected.rsplit(".", 1)[-1].startswith(MOD_NAME_PREFIXES):
            return False
        qualname = getattr(value, "__qualname__", None) or getattr(
            getattr(value, "__func__", None), "__qualname__", None)
        return qualname is None or qualname == expected or qualname.endswith("." + expected)

    for name in game_names:
        module = modules_by_name[name]
        try:
            entries = sorted(vars(module).items())
        except Exception:
            continue
        for attr, value in entries:
            if attr.startswith("__"):
                continue
            if inspect.isroutine(value):
                signature = safe_signature(value)
                targets.append(f"{name}:{attr}{signature}")
                if own(value, attr) and getattr(value, "__module__", name) in (name, None):
                    signatures[f"{name}:{attr}"] = signature
            # __module__ が None のクラスも拾う。
            # Nuitka では欠けていることがある。
            elif inspect.isclass(value) and getattr(value, "__module__", "") in (name, None):
                try:
                    for mname, mvalue in sorted(vars(value).items()):
                        # 特殊メソッドは対象にしないが、__init__ だけは残す。
                        if inspect.isroutine(mvalue) and not (
                                mname.startswith("__") and mname != "__init__"):
                            signature = safe_signature(mvalue)
                            targets.append(f"{name}:{attr}.{mname}{signature}")
                            if own(mvalue, f"{attr}.{mname}"):
                                signatures[f"{name}:{attr}.{mname}"] = signature
                except Exception:
                    pass
    _write(os.path.join(recon_dir, "targets.txt"), targets)
    write_json(os.path.join(recon_dir, SIGNATURES_NAME), signatures, indent=0)

    # -- bug_sites.txt -----------------------------------------------------
    try:
        _write(os.path.join(recon_dir, "bug_sites.txt"), probe_bug_sites())
    except Exception:
        log_exc("recon: bug site probe failed")

    # -- build.json --------------------------------------------------------
    # 最後に書く。
    # この控えが次の回の退避の判定材料になるので、
    # 中身が揃う前に書くと「揃っている顔の控え」が残る。
    _write_build(recon_dir, identity,
                 {"modules": len(snapshot), "game_modules": len(game_names)})

    log("recon: wrote {} file(s) to {}".format(len(OUTPUT_FILES) + 1, recon_dir))
    if backup:
        update_current(recon_dir, snap_dir)
    return recon_dir


def _write(path: str, lines: list[str]) -> None:
    """行のリストを1つのファイルに書く。失敗してもリコン全体は止めない。"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        log_exc("recon: write failed for {}".format(path))
