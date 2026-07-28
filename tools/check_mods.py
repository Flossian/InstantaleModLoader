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
import os
import sys

MODS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "runtime", "mods")


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


def main():
    mods = sorted(f for f in os.listdir(MODS_DIR)
                  if f.endswith(".py") and not f.startswith("_"))
    problems = []
    for name in mods:
        problems.extend(check_file(os.path.join(MODS_DIR, name)))

    for path, func, message in problems:
        print("MISMATCH {} :: def {}()\n    {}".format(
            os.path.basename(path), func, message))
    print("checked {} mod(s); problems: {}".format(len(mods), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
