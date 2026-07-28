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

import inspect
import json
import os
import sys
import types

from . import log, log_exc

# ゲーム自身のトップレベルモジュール。最初のリコンで確認した。
#
# 「ゲーム以外を除外する」ではなく「ゲームのものを列挙する」形にしている。
# 配布物には約 4200 のモジュールが入っており、除外方式では
# click / joblib / keyring / dill / pygments といった同梱ライブラリが
# 紛れ込んでしまう。さらに悪いことに、"__main__" は
# sys.stdlib_module_names に含まれているため、標準ライブラリを除外すると
# instantale.py 本体（＝最も重要な対象）まで落ちてしまった。
GAME_TOPLEVEL = {
    "__main__",                      # instantale.py。約1万行のメインモジュール
    "scripts",                       # scripts.hud.*, scripts.llm.*, scripts.items ...
    "Embedding",
    "image_generation",
    "llama_cpp_runtime_completion",
    "sidecar_process",
    "save_area_json",
    "save_world_json",
    "api_key_manager",
    "build_type",
    "sdcpp_cuda",                    # 同梱の stable-diffusion.cpp バインディング
}

MAX_REPR = 300
MAX_CONST_ITEMS = 40


# --------------------------------------------------------------------------
# 分類
# --------------------------------------------------------------------------
def is_compiled(module: types.ModuleType) -> bool:
    """Nuitka がコンパイルしたモジュールには __compiled__ が入っている。"""
    try:
        return "__compiled__" in vars(module)
    except Exception:
        return False


def is_game_module(name: str) -> bool:
    return name.split(".")[0] in GAME_TOPLEVEL


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
# 入口
# --------------------------------------------------------------------------
def dump(out_dir: str) -> str:
    """out_dir/recon/ に成果物一式を書き出し、そのパスを返す。"""
    recon_dir = os.path.join(out_dir, "recon")
    os.makedirs(recon_dir, exist_ok=True)

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

    log("recon: wrote {} file(s) to {}".format(5, recon_dir))
    return recon_dir


def _write(path: str, lines: list[str]) -> None:
    """行のリストを1つのファイルに書く。失敗してもリコン全体は止めない。"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        log_exc("recon: write failed for {}".format(path))
