# -*- coding: utf-8 -*-
"""動いているゲームの中身を調べて、パッチ対象の一覧を書き出す。

Nuitka でビルドされているのでソースは存在しない。関数の正確な名前を知る方法は、
動いているインタプリタに sys.modules を通して直接聞くことだけになる。
ここでは以下を出力する。

    recon/summary.txt        実行環境とモジュールの概況
    recon/modules.json       全モジュールの一覧（機械可読）
    recon/game_modules.txt   ゲーム自身のモジュールの全属性。擬似的なソース一覧
    recon/targets.txt        "モジュール名:関数名" の形。@ctx.wrap にそのまま貼れる
    recon/bug_sites.txt      クラッシュログに出ていた箇所まわりの調査結果

ゲームには一切変更を加えない。読み取って書き出すだけ。
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import inspect
import json
import os
import sys
import types
import zipfile

# ゲーム自身のトップレベルモジュールの表（GAME_TOPLEVEL）はパッケージ側にある。
# patch.py もエイリアス張り替えの範囲を決めるのに同じ表を見るので、2箇所に
# 書き写さないため。名前はここからも引けるように再公開しておく。
from . import GAME_TOPLEVEL, is_game_module, log, log_exc  # noqa: F401

MAX_REPR = 300
MAX_CONST_ITEMS = 40

#: 毎回書き出すファイル。退避（zip）に詰めるのもこの一式。
OUTPUT_FILES = ("summary.txt", "modules.json", "game_modules.txt",
                "targets.txt", "bug_sites.txt")

#: その回のリコンが「どのビルドを見たものか」を書き添える控え。
#: `out/recon/` の中に置くので、退避したときは zip の中にそのまま入る
#: （zip 自身が何の版のものか、開ければ分かる）。
BUILD_NAME = "build.json"

#: 退避先。`out/recon_snapshots/main_025_20260809.zip` の形で溜まる。
SNAPSHOT_DIR_NAME = "recon_snapshots"


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
        # 特殊メソッドは基本的に飛ばすが、__init__ と __call__ は
        # パッチ対象になり得るので残す。
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
            if doc:
                entry["doc"] = doc.strip().splitlines()[0][:200]
        elif entry["kind"] == "property":
            entry["signature"] = "<property>"
        elif entry["kind"] == "value":
            entry["repr"] = safe_repr(value)
        info["members"][name] = entry
    return info


def describe_module(name: str, module: types.ModuleType, *, deep: bool) -> dict:
    """モジュール1つを記録する。deep=False なら見出しの情報だけ。

    ゲーム以外の約 4200 モジュールまで中身を展開すると出力が使い物に
    ならないので、深く見るのはゲームのモジュールだけにしている
    （呼び出し側が deep で指定する）。
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
            if doc:
                entry["doc"] = doc.strip().splitlines()[0][:200]
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
            # 約 4200 モジュール全部を対象にすると結果が埋もれる
            # （試したところ "Literal" は標準ライブラリや sympy に 486 件当たった）。
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
# 中身は同じビルドでも走らせるたびに変わる ― リコンは `sys.modules` を見るので、
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
IDENTITY_KEYS = ("app_version", "game_version", "exe_size")

#: Epic Games Launcher が置いているマニフェスト。`AppVersionString` がここにある。
#: ゲームを起動しなくても読める素性なので、ゲーム側の API に依存しない。
EPIC_MANIFEST_GLOB = os.path.join(
    os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
    "Epic", "EpicGamesLauncher", "Data", "Manifests", "*.item")


def _norm(path: str) -> str:
    """比較用にパスを均す。8.3 形式の短い名前を実体に戻す。

    `sys.executable` は `C:\\PROGRA~1\\EPICGA~1\\INSTAN~1\\instantale.exe` の形で
    返ってくることがあり（実測）、Epic のマニフェストにある `InstallLocation`
    （`C:\\Program Files\\Epic Games\\Instantaleq6Ve7`）と字面が合わない。
    """
    try:
        return os.path.normcase(os.path.realpath(path))
    except Exception:
        return os.path.normcase(path)


def epic_app_version(exe_path: str) -> str | None:
    """Epic の `AppVersionString`（`main_025`）を返す。読めなければ None。

    複数のゲームが入っていることがあるので、`InstallLocation` が**今動いている
    実行ファイルの置き場所と一致するもの**だけを採る。表示名で当てにいくと、
    別のタイトルの版を自分の版として書き残す事故になる。
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

    Epic の `AppVersion` とは別系統で、更新しても据え置かれることがある
    （GAME.md §1.4）。だから**どちらか一方では足りず、両方を控える**。
    """
    main = sys.modules.get("__main__")
    getter = getattr(main, "get_game_version", None)
    if getter is None:
        return None
    try:
        return str(getter())
    except Exception:
        return None


def build_identity() -> dict:
    """今動いているビルドの素性。`build.json` に書き、退避の判定にも使う。

    どの項目も取れなくてよい（取れなければ None が入る）。ここで例外を投げると、
    素性が分からないというだけでリコン全体が止まってしまう。
    """
    exe = sys.executable or ""
    size = mtime = None
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
    }


def _same_build(old: dict, new: dict) -> bool:
    """同じビルドを見たリコンか。

    **控えが無い（`old` が空）ときは False。** 「前回と同じだと確かめられない」
    のであって「同じ」ではない。ここを True に倒すと、この仕組みが入る前から
    `out/recon/` に残っていた最後の1回が、確かめないまま上書きされる。
    """
    if not old:
        return False
    return all(old.get(key) == new.get(key) for key in IDENTITY_KEYS)


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
    """退避の名前。`main_025_20260809` の形。

    日付は**退避するリコンを取った日**で、今日ではない。8/05 に取ったものを
    8/09 の更新で退避するなら `..._20260805` でなければ、後から並べたときに
    「いつのゲームを見たダンプなのか」が分からなくなる。

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

    同じ版・同じ日に2回退避が要ることは滅多に無いが、起きたときに**黙って
    上書きするのが一番まずい**（残すために作った仕組みが消す側に回る）。
    """
    path = os.path.join(snap_dir, base + ".zip")
    index = 2
    while os.path.exists(path):
        path = os.path.join(snap_dir, "{}_{}.zip".format(base, index))
        index += 1
    return path


def archive_previous(recon_dir: str, identity: dict) -> str | None:
    """`out/recon/` に残っている前回のダンプを、別ビルドのものなら zip で退避する。

    **上書きする前に呼ぶこと。** 退避したら zip のパスを返し、退避しなかった
    （初回・同じビルド・退避に失敗）なら None を返す。

    失敗しても例外は投げない。退避はリコンの付随物で、これが原因で調査そのものが
    止まるほうが損害が大きい（ローダ全体の作法と同じ）。
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

    snap_dir = os.path.join(os.path.dirname(recon_dir), SNAPSHOT_DIR_NAME)
    tmp = None
    try:
        os.makedirs(snap_dir, exist_ok=True)
        path = _unique_zip_path(snap_dir, _snapshot_name(recon_dir, old))
        # 隣に書いてから差し替える。書いている途中で落ちても、壊れた zip が
        # 「退避済み」の顔で残らない（`write_text()` と同じ作法）。
        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in present + [BUILD_NAME]:
                source = os.path.join(recon_dir, name)
                if os.path.isfile(source):
                    archive.write(source, name)
        os.replace(tmp, path)
    except Exception:
        log_exc("recon: cannot archive the previous dump")
        if tmp:
            try:
                os.remove(tmp)      # 書きかけを残さない
            except Exception:
                pass
        return None
    return path


def _write_build(recon_dir: str, identity: dict, counts: dict) -> None:
    """`build.json` を書く。**全部書き終えてから**呼ぶこと。

    中身の sha256 まで控えるのは、次に見るときに zip を開かなくても「本当に
    中身が動いたのか」を確かめられるようにするため（退避の判定には使わない。
    理由はこの節の頭にある）。
    """
    record = dict(identity)
    record["written"] = datetime.datetime.now().isoformat(timespec="seconds")
    record.update(counts)
    record["files"] = {name: _digest(os.path.join(recon_dir, name))
                       for name in OUTPUT_FILES
                       if os.path.isfile(os.path.join(recon_dir, name))}
    try:
        with open(os.path.join(recon_dir, BUILD_NAME), "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1, default=str)
    except Exception:
        log_exc("recon: cannot write {}".format(BUILD_NAME))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def dump(out_dir: str, *, backup: bool = True) -> str:
    """out_dir/recon/ に成果物一式を書き出し、そのパスを返す。

    `backup=True` なら、前回のダンプが**別のビルドを見たものだった場合に限り**
    上書きする前に `out/recon_snapshots/<版>_<日付>.zip` へ退避する（ビルドの
    見分け方と、中身の差を引き金にしない理由は「退避」の節にある）。
    """
    recon_dir = os.path.join(out_dir, "recon")
    os.makedirs(recon_dir, exist_ok=True)

    # 素性は書き出す前に取る。退避の判定に使うので、**上書きより先**でなければ
    # 前回の `build.json` がもう残っていない。
    identity = build_identity()
    if backup:
        archived = archive_previous(recon_dir, identity)
        if archived:
            log("recon: archived the previous dump to {}".format(archived))

    # 走査の途中で sys.modules が変化しても壊れないよう、先にコピーを取る。
    snapshot = [(name, mod) for name, mod in list(sys.modules.items()) if mod is not None]
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
        # どのビルドを見たダンプなのかを先頭に出す。退避した zip を後から
        # 開いたときに、機械可読の `build.json` を見なくても分かるように。
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
        module = sys.modules[name]
        summary.append("  {:<58} {}".format(
            name, os.path.basename(str(getattr(module, "__file__", "?")))))
    summary += ["", "top-level non-game packages", "-" * 72]
    # ゲーム以外は名前だけを6個ずつ横に並べる。一覧として眺めやすくするため。
    tops = sorted({n.split(".")[0] for n, _ in snapshot if not is_game_module(n)})
    summary += ["  " + ", ".join(tops[i:i + 6]) for i in range(0, len(tops), 6)]
    _write(os.path.join(recon_dir, "summary.txt"), summary)

    # -- game_modules.txt --------------------------------------------------
    # 擬似的なソース一覧。本物のソースが無い以上、これが一番コードに近い出力になる。
    detail: list[str] = []
    for name in game_names:
        module = sys.modules[name]
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
    for name in game_names:
        module = sys.modules[name]
        try:
            entries = sorted(vars(module).items())
        except Exception:
            continue
        for attr, value in entries:
            if attr.startswith("__"):
                continue
            if inspect.isroutine(value):
                targets.append(f"{name}:{attr}{safe_signature(value)}")
            # __module__ が None のクラスも拾う。Nuitka では欠けていることがある。
            elif inspect.isclass(value) and getattr(value, "__module__", "") in (name, None):
                try:
                    for mname, mvalue in sorted(vars(value).items()):
                        # 特殊メソッドは対象にしないが、__init__ だけは残す。
                        if inspect.isroutine(mvalue) and not (
                                mname.startswith("__") and mname != "__init__"):
                            targets.append(f"{name}:{attr}.{mname}{safe_signature(mvalue)}")
                except Exception:
                    pass
    _write(os.path.join(recon_dir, "targets.txt"), targets)

    # -- bug_sites.txt -----------------------------------------------------
    try:
        _write(os.path.join(recon_dir, "bug_sites.txt"), probe_bug_sites())
    except Exception:
        log_exc("recon: bug site probe failed")

    # -- build.json --------------------------------------------------------
    # 最後に書く。この控えが次の回の退避の判定材料になるので、
    # 中身が揃う前に書くと「揃っている顔の控え」が残る。
    _write_build(recon_dir, identity,
                 {"modules": len(snapshot), "game_modules": len(game_names)})

    log("recon: wrote {} file(s) to {}".format(len(OUTPUT_FILES) + 1, recon_dir))
    return recon_dir


def _write(path: str, lines: list[str]) -> None:
    """行のリストを1つのファイルに書く。失敗してもリコン全体は止めない。"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        log_exc("recon: write failed for {}".format(path))
