# -*- coding: utf-8 -*-
"""mod の静的検査。ゲーム不要。注入する前に必ず通すこと。

    python tools/check_mods.py

`python -m compileall` は**構文しか見ない**。実際にゲームを落としたのは
どれも構文としては正しいコードだった:

  * `@ctx.wrap("...InstantaleApp.process_choice")` が、コード移動の巻き添えで
    **別の関数（`snapshot()`）に付いた**。`process_choice` が `snapshot` に
    差し替わり、ボタンを押すたびに
    `TypeError: snapshot() got an unexpected keyword argument 'function'`
    （2026-07-27）
  * `@ctx.wrap` が飾る関数の第1引数は `orig`、メソッド対象なら第2引数は `self`。
    ここがずれると引数が1つずつ食い違ったまま本体が呼ばれる

どちらも「デコレータの対象名」と「関数の引数の並び」を突き合わせれば静的に
捕まる。ソースが読めない環境では、こういう機械的な検査ほど効く。
"""

import ast
import io
import json
import os
import sys

MODS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "runtime", "mods")
MANIFEST_NAME = "mod.json"
ORDER_NAME = "load_order.json"


def _target_of(call):
    """`ctx.wrap("mod:Cls.meth")` の対象文字列。定数でなければ None。"""
    if not call.args:
        return None
    first = call.args[0]
    return first.value if isinstance(first, ast.Constant) else None


def _decorators(node):
    """この関数に付いている ctx.wrap / ctx.patch を (種類, 対象) で返す。"""
    found = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr in ("wrap", "patch"):
            found.append((func.attr, _target_of(dec)))
    return found


def check_file(path):
    problems = []
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except SyntaxError as exc:
        return [(path, "<file>", "syntax error: {}".format(exc))]

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        args = [a.arg for a in node.args.args]
        for kind, target in _decorators(node):
            if target is None:
                continue                      # 動的な対象は検査しない
            if kind == "wrap":
                # 第1引数は必ず元の関数。ここが違えば別の関数に付いている。
                if not args or args[0] != "orig":
                    problems.append((path, node.name,
                                     "wrap({!r}) の第1引数は 'orig' のはずが {}"
                                     .format(target, args[:3])))
                    continue
                # メソッドを包むなら第2引数は self。
                method = target.rsplit(":", 1)[-1]
                if "." in method and (len(args) < 2 or args[1] != "self"):
                    problems.append((path, node.name,
                                     "wrap({!r}) はメソッドなので (orig, self, ...) "
                                     "が要る。今は {}".format(target, args[:3])))
    return problems


def _mod_files(name):
    """mod フォルダの中の .py を全て返す（入口 + 分割した中身）。

    ローダは `__init__.py` しか呼ばないが、検査は**フォルダの中身を全部**見る。
    `@ctx.wrap` は入口以外のファイルにも書けるので、そこだけ検査から漏れると
    この検査の意味（デコレータの対象と引数の食い違いを注入前に捕まえる）が
    無くなる。`data/` のようなサブフォルダも辿る。
    """
    root = os.path.join(MODS_DIR, name)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        found.extend(os.path.join(dirpath, f)
                     for f in sorted(filenames) if f.endswith(".py"))
    return found


def check_manifest(name):
    """mod.json の中身を見る。

    フォルダ名も入口のファイル名も自由になったぶん、**入口がどれかは mod.json
    だけが知っている**。ここが食い違うと mod が黙って読み込まれないので、
    注入前に静的に捕まえる。
    """
    problems = []
    path = os.path.join(MODS_DIR, name, MANIFEST_NAME)
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        return [(path, MANIFEST_NAME, "読めない: {}".format(exc))]
    if not isinstance(data, dict):
        return [(path, MANIFEST_NAME, "オブジェクトではない")]

    entry = data.get("entry")
    if not entry:
        problems.append((path, MANIFEST_NAME, '"entry" が無い'))
    elif not os.path.isfile(os.path.join(MODS_DIR, name, entry)):
        problems.append((path, MANIFEST_NAME,
                         '"entry" が指す {!r} が無い'.format(entry)))

    # GUI の一覧が空欄にならないよう、表示用の項目も揃っているか見る
    for key in ("name", "description", "version", "author"):
        if not data.get(key):
            problems.append((path, MANIFEST_NAME, "{!r} が空".format(key)))
    return problems


def check_order(mods):
    """load_order.json と実体の突き合わせ。

    適用順は動作の前提（計測は修正より外側、など）なので、
    **順序ファイルと実体のずれは黙って通さない**。
    """
    path = os.path.join(MODS_DIR, ORDER_NAME)
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        order = data.get("order") or []
        disabled = data.get("disabled") or []
    except Exception as exc:
        return [(path, ORDER_NAME, "読めない: {}".format(exc))]

    problems = []
    # 無効な mod は「入れたのに効かない」の原因そのものなので、
    # 検査でも必ず名前を出す（異常ではないので警告ではなく報告）。
    off = [m for m in disabled if m in mods]
    if off:
        problems.append((path, ORDER_NAME,
                         "無効化されている（読み込まれない）: {}".format(", ".join(off))))
    missing = [m for m in mods if m not in order]
    if missing:
        # 落ちはしない（ローダは末尾に回す）が、順序が宣言されていない状態
        problems.append((path, ORDER_NAME,
                         "順序に無い mod（末尾に回る）: {}".format(", ".join(missing))))
    stale = [m for m in order if m not in mods]
    if stale:
        problems.append((path, ORDER_NAME,
                         "実体の無い記述: {}".format(", ".join(stale))))
    dupes = sorted({m for m in order if order.count(m) > 1})
    if dupes:
        problems.append((path, ORDER_NAME, "重複: {}".format(", ".join(dupes))))
    return problems


def main():
    # 1 mod = 1 フォルダ。`mod.json` を持つものだけを mod とみなす
    # （ローダの `_installed` と同じ規則）。
    mods = sorted(name for name in os.listdir(MODS_DIR)
                  if not name.startswith(("_", "."))
                  and os.path.isfile(os.path.join(MODS_DIR, name, MANIFEST_NAME)))
    problems = []
    for name in mods:
        problems.extend(check_manifest(name))
        for path in _mod_files(name):
            problems.extend(check_file(path))
    problems.extend(check_order(mods))

    for path, func, message in problems:
        # フォルダ名を残す。ファイル名だけだと全部 __init__.py になって
        # どの mod の話か分からなくなる。
        label = os.path.relpath(path, MODS_DIR).replace(os.sep, "/")
        print("MISMATCH {} :: def {}()\n    {}".format(label, func, message))
    print("checked {} mod(s); problems: {}".format(len(mods), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
