# -*- coding: utf-8 -*-
"""注入で流し込むコードが、ゲームの `__main__` を汚さないことを見る。

    python tools/tests/test_injector_bootstrap.py

`PyRun_SimpleString` は**ゲームの `__main__` の辞書で**流し込んだ文字列を実行する。
モジュール階層に `import` や代入を書くと、
本体がその名前に束縛していたものを黙って上書きする。

実際に踏んだ形（2026-08-21）:

    BOOTSTRAP_TEMPLATE = r'''
    import sys, os, datetime, traceback

本体は `from datetime import datetime`（クラス束縛）で持っていたので、
この1行がモジュールを被せていた。
その結果、本体の `make_crash_log` が
`AttributeError: module 'datetime' has no attribute 'now'` で落ち、
注入したセッションでは `crash_log.txt` も `send_crash_log_to_server` も
丸ごと止まっていた（素のゲームでは書けている）。

見つけるまでに14件のクラッシュを「素の不具合」として数えていた。
同じ数え違いを繰り返さないよう、構造そのものを検査する。

確認するもの:

  素材     … `make_bootstrap()` が boot / unload とも構文の通るコードを返し、
             ASCII だけで出来ている
  汚さない … モジュール階層に残る束縛は包みの関数ひとつだけで、それも
             最後に `del` される。`import` がモジュール階層に1つも無い
  実演     … 偽の `__main__`（`datetime` はクラス束縛）へ流しても、
             束縛が変わらない
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))

if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import injector                                        # noqa: E402

#: 包みの関数の名前。`del` されるので、実行後には残らない。
WRAPPER = "_instantale_modloader_bootstrap"

failures = []
passed = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    (passed if cond else failures).append(name)


def source_for(action):
    """`make_bootstrap()` が実際に流し込むコードを、文字列として取り出す。"""
    payload = injector.make_bootstrap(
        runtime_dir=r"C:\some\runtime",
        out_dir=r"C:\some\out",
        log_path=r"C:\some\out\bootstrap.log",
        action=action)
    check("{}: NUL で終端している".format(action), payload.endswith(b"\0"))
    return payload[:-1].decode("ascii")


def module_level_bindings(tree):
    """モジュール階層で名前を束縛している節を、種類ごとに集める。

    関数の中は見ない（そこが安全地帯なので）。
    """
    imports, assigns, defs = [], [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            assigns.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(node.name)
        elif isinstance(node, (ast.Try, ast.If, ast.For, ast.While, ast.With)):
            # 入れ子の中にも書けるので、同じ規則で潜る。
            inner = module_level_bindings(ast.Module(
                body=(list(getattr(node, "body", []))
                      + list(getattr(node, "orelse", []))
                      + list(getattr(node, "finalbody", []))
                      + [h for handler in getattr(node, "handlers", [])
                         for h in handler.body]),
                type_ignores=[]))
            imports += inner[0]
            assigns += inner[1]
            defs += inner[2]
    return imports, assigns, defs


def deleted_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def main():
    for action in sorted(injector.CALLS):
        print("\n[{}]".format(action))
        src = source_for(action)
        check("{}: ASCII だけ".format(action), src.isascii())

        try:
            tree = ast.parse(src)
            ok = True
        except SyntaxError as exc:
            tree, ok = None, False
            check("{}: 構文が通る".format(action), False, exc)
        if not ok:
            continue
        check("{}: 構文が通る".format(action), True)

        imports, assigns, defs = module_level_bindings(tree)
        check("{}: モジュール階層に import が無い".format(action),
              not imports,
              [ast.unparse(node) for node in imports])
        check("{}: モジュール階層に代入が無い".format(action),
              not assigns,
              [ast.unparse(node) for node in assigns])
        check("{}: モジュール階層の定義は包みひとつだけ".format(action),
              defs == [WRAPPER], defs)
        check("{}: その包みも del される".format(action),
              WRAPPER in deleted_names(tree), deleted_names(tree))

        # 呼び出す中身が入れ替わっていないか（boot と unload の作り分け）。
        check("{}: 目的の呼び出しが入っている".format(action),
              injector.CALLS[action].replace("__OUT__", "") .split("(")[0] in src,
              src[-200:])

    # -- 偽の __main__ へ流してみる ----------------------------------------
    # 本物を流すとローダが起動してしまうので、包みの形だけを同じにした
    # 差し障りの無いコードで、名前の残り方を見る。
    print("\n[偽の __main__ へ流す]")
    import datetime as real_datetime

    def fresh():
        return {"__name__": "__main__", "__builtins__": __builtins__,
                "datetime": real_datetime.datetime}

    flat = fresh()
    exec("import sys, os, datetime, traceback\n", flat)
    check("平らに書くと本体の束縛が消える（踏んだ形の再現）",
          flat["datetime"] is not real_datetime.datetime, flat["datetime"])

    wrapped_src = ast.unparse(ast.parse(source_for("boot")))
    # 中身（sys.path いじり・import・boot 呼び出し）は動かさない。
    # 包みの構造だけを残して、束縛の残り方を見る。
    body = wrapped_src.split("\n")
    check("包んだコードは1文字も module 階層で import しない",
          not [line for line in body
               if line.startswith("import ") or line.startswith("from ")],
          [line for line in body[:5]])

    wrapped = fresh()
    exec("def {0}():\n"
         "    import sys, os, datetime, traceback\n"
         "    return datetime\n"
         "try:\n"
         "    {0}()\n"
         "finally:\n"
         "    del {0}\n".format(WRAPPER), wrapped)
    check("包むと本体の束縛が残る",
          wrapped["datetime"] is real_datetime.datetime, wrapped["datetime"])
    check("包みの名前も残らない",
          sorted(k for k in wrapped if not k.startswith("__")) == ["datetime"],
          sorted(k for k in wrapped if not k.startswith("__")))

    print("\n{} check(s), {} failure(s)".format(
        len(passed) + len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
