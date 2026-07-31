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

同じ考えで、**宣言と実体のずれ**も注入前に捕まえる。

  * `mod.json` の `"entry"` が指すファイルが無い（mod が黙って読み込まれない）
  * `"api"` がこのローダで扱えない
  * `load_order.json` と実体の食い違い、`"after"` / `"before"` の循環
  * `"settings"` の宣言と、コード側の定数のずれ（**既定値が2箇所にある**ため）

探索と適用順の判定は**ローダ本体の `discover()` を呼ぶ**。以前はここに同じ規則を
書き写していたので、片方だけ直すと検査と実際の適用順がずれた。
"""

import ast
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader as ml            # noqa: E402
from instantale_modloader import config as C  # noqa: E402

MODS_DIR = os.path.join(_ROOT, "runtime", "mods")
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


# ゲームに埋まっている CPython の版。MOD はこのインタプリタの中で動くので、
# ここより新しい構文を書くと**注入した瞬間に SyntaxError で落ちる**。
# 手元の python は 3.13 しか無く、`compileall` は 3.13 として通してしまうため、
# パーサに版を指定して 3.11 以降の構文を弾く。
#
# 捕まるのは**構文だけ**。3.11 以降で追加された標準ライブラリの関数を使った場合は
# ここでは分からない（CI で実際に 3.10 を動かす方でしか捕まらない）。
GAME_PYTHON = (3, 10)


def _parse(path):
    """AST を返す。読めなければ `(None, 問題)`。"""
    source = None
    try:
        source = io.open(path, encoding="utf-8").read()
        return ast.parse(source, feature_version=GAME_PYTHON), None
    except SyntaxError as exc:
        # 手元の版では通るのに 3.10 では通らない、を見分けて言い分ける。
        # 「動いているのに怒られた」と受け取られると、この検査ごと無視されるため。
        if source is not None:
            try:
                ast.parse(source)
            except SyntaxError:
                pass
            else:
                return None, (path, "<file>",
                              "ゲームの Python {}.{} には無い構文: {}".format(
                                  GAME_PYTHON[0], GAME_PYTHON[1], exc.msg))
        return None, (path, "<file>", "syntax error: {}".format(exc))
    except Exception as exc:
        return None, (path, "<file>", "読めない: {}".format(exc))


def check_file(path):
    problems = []
    tree, broken = _parse(path)
    if tree is None:
        return [broken]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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

    ローダは入口しか呼ばないが、検査は**フォルダの中身を全部**見る。
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


def _module_constants(path):
    """モジュール直下の `NAME = 定数` を `{名前: 値}` で返す。

    設定の宣言（`mod.json`）と実体（コードの定数）を突き合わせるために使う。
    リテラルとして読めない代入（式・関数呼び出し）は入れない ― そこは
    「宣言された既定値と比べられない」であって、間違いではない。
    """
    tree, _broken = _parse(path)
    if tree is None:
        return {}, set()
    values, names = {}, set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            names.add(target.id)
            try:
                values[target.id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return values, names


def check_manifest(name, manifest):
    """`mod.json` の中身を見る。

    フォルダ名も入口のファイル名も自由なので、**入口がどれかは mod.json だけが
    知っている**。ここが食い違うと mod が黙って読み込まれないので、注入前に
    静的に捕まえる。

    名乗り（name / description / version / author）は**仕様では任意**なので、
    欠けていても「問題」にはしない（外部の mod が任意の項目で検査に落ちるのは
    筋が通らない）。同梱 mod は全部揃えておきたいので、`--strict` のときだけ
    見落としとして数える。
    """
    problems, notes = [], []
    path = os.path.join(MODS_DIR, name, MANIFEST_NAME)
    try:
        data = json.load(io.open(path, encoding="utf-8"))
    except Exception as exc:
        return [(path, MANIFEST_NAME, "読めない: {}".format(exc))], []
    if not isinstance(data, dict):
        return [(path, MANIFEST_NAME, "オブジェクトではない")], []

    entry = data.get("entry")
    entry_path = os.path.join(MODS_DIR, name, entry) if entry else None
    if not entry:
        problems.append((path, MANIFEST_NAME, '"entry" が無い'))
    elif not os.path.isfile(entry_path):
        problems.append((path, MANIFEST_NAME,
                         '"entry" が指す {!r} が無い'.format(entry)))

    # ローダ API。ここで撥ねられる mod は注入しても読み込まれない。
    verdict, reason = ml.api_status(manifest)
    if verdict:
        problems.append((path, MANIFEST_NAME,
                         "{}: {}（このローダは API {}）".format(verdict, reason, ml.API)))
    if "api" not in data:
        notes.append((path, MANIFEST_NAME,
                      '"api" が無い（{} として扱う）'.format(ml.DEFAULT_API)))

    # 表示用の項目。無くても動くので、報告はするが問題として数えない。
    for key in ("name", "description", "version", "author"):
        if not data.get(key):
            notes.append((path, MANIFEST_NAME, "{!r} が空（表示だけの項目）".format(key)))

    # 依存の宣言が自分自身を指していないか（循環は discover() が見る）
    for key in ("after", "before", "conflicts"):
        for other in manifest.get(key) or []:
            if other == name:
                problems.append((path, MANIFEST_NAME,
                                 '"{}" が自分自身を指している'.format(key)))

    problems += check_settings(name, data, manifest, entry_path)
    return problems, notes


def check_settings(name, raw, manifest, entry_path):
    """`"settings"` の宣言を見る。

    **既定値が2箇所に書かれている**のがこの検査の存在理由。実際に使われるのは
    コードの定数で、GUI が表示に使うのは `mod.json` の `"default"`（GUI は mod の
    コードを import しない決まりなので定数を読めない）。ずれると「GUI では
    既定 3 と出るのに実際は 5 で動く」という、最も気付きにくい形になる。
    """
    problems = []
    path = os.path.join(MODS_DIR, name, MANIFEST_NAME)
    declared_raw = raw.get("settings")
    if declared_raw is None:
        return problems
    if not isinstance(declared_raw, dict):
        return [(path, MANIFEST_NAME, '"settings" がオブジェクトではない')]

    decls = manifest.get("settings") or {}
    # normalize_decls が落とした宣言＝書き方が壊れているもの。
    for key in declared_raw:
        if key not in decls:
            problems.append((path, MANIFEST_NAME,
                             '設定 {!r} の宣言が読めない（type / values を確認）'.format(key)))

    if not entry_path or not os.path.isfile(entry_path):
        return problems      # 入口が無いことは既に報告済み

    constants, names = _module_constants(entry_path)
    for key, decl in decls.items():
        # 宣言した既定値そのものが自分の宣言を満たしているか
        ok, _value, why = C.coerce(decl, decl["default"])
        if not ok:
            problems.append((path, MANIFEST_NAME,
                             '設定 {!r} の "default" が宣言に合わない（{}）'.format(key, why)))
        if key not in names:
            # ローダは「宣言だけあってコードに無い名前」を作らない。
            # つまりこの設定は GUI に出るが**何も効かない**。
            problems.append((path, MANIFEST_NAME,
                             '設定 {!r} に対応する定数が {} に無い'
                             .format(key, os.path.basename(entry_path))))
            continue
        if key in constants and constants[key] != decl["default"]:
            problems.append((path, MANIFEST_NAME,
                             '設定 {!r} の既定値がコードとずれている'
                             '（コード {!r} / mod.json {!r}）'
                             .format(key, constants[key], decl["default"])))
    return problems


def check_order(found):
    """`load_order.json` と実体、`after` / `before` の突き合わせ。

    判定そのものはローダの `discover()` が持っている（同じ規則を2箇所に書かない）。
    ここはその報告を検査の書式に移し替えるだけ。適用順は動作の前提なので、
    **宣言と実体のずれは黙って通さない**。
    """
    path = os.path.join(MODS_DIR, ORDER_NAME)
    return [(path, ORDER_NAME, line) for line in found["problems"]]


def main():
    strict = "--strict" in sys.argv
    found = ml.discover(MODS_DIR)
    mods = found["installed"]

    problems, notes = [], []
    for name in mods:
        mod_problems, mod_notes = check_manifest(name, found["manifests"][name])
        problems += mod_problems
        notes += mod_notes
        for path in _mod_files(name):
            problems += check_file(path)
    problems += check_order(found)

    if strict:
        problems += notes
        notes = []

    def show(entries, head):
        for path, func, message in entries:
            # フォルダ名を残す。ファイル名だけだと、どの mod の話か分からなくなる。
            label = os.path.relpath(path, MODS_DIR).replace(os.sep, "/")
            print("{} {} :: {}\n    {}".format(head, label, func, message))

    show(problems, "MISMATCH")
    show(notes, "note    ")
    print("checked {} mod(s); problems: {}{}".format(
        len(mods), len(problems),
        "; notes: {}".format(len(notes)) if notes else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
