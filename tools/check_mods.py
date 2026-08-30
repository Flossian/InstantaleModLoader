# -*- coding: utf-8 -*-
"""mod の静的検査。ゲーム不要。注入する前に必ず通すこと。

    python tools/check_mods.py
    python tools/check_mods.py --only 404     名前に 404 を含む mod だけを見る

`python -m compileall` は**構文しか見ない**。
実際にゲームを落としたのはどれも構文としては正しいコードだった:

  * `@ctx.wrap("...InstantaleApp.process_choice")` が、コード移動の巻き添えで
    **別の関数（`snapshot()`）に付く**。`process_choice` が `snapshot` に
    差し替わり、ボタンを押すたびに
    `TypeError: snapshot() got an unexpected keyword argument 'function'`
  * `@ctx.wrap` が飾る関数の第1引数は `orig`、メソッド対象なら第2引数は `self`。
    ここがずれると引数が1つずつ食い違ったまま本体が呼ばれる

どちらも「デコレータの対象名」と「関数の引数の並び」を突き合わせれば静的に捕まる。
ソースが読めない環境では、こういう機械的な検査ほど効く。

同じ考えで、**宣言と実体のずれ**も注入前に捕まえる。

  * `mod.json` の `"entry"` が指すファイルが無い（mod が黙って読み込まれない）
  * `"api"` がこのローダで扱えない
  * `load_order.json` と実体の食い違い、`"after"` / `"before"` の循環
  * `"settings"` の宣言と、コード側の定数のずれ（**既定値が2箇所にある**ため）
  * 提供を受けた MOD の作者が `NOTICE` に載っていない（**配ると表示が欠けたまま
    再配布される**。MIT は著作権表示が複製に付いて回ることを要求する）
  * MOD どうしで名前がぶつかっている（ボタンの印・ウィジェットの属性・`state/` の
    フォルダ名。**例外は出ず、片方の機能が黙って効かなくなる**）

探索と適用順の判定は**ローダ本体の `discover()` を呼ぶ**。
以前はここに同じ規則を書き写していたので、片方だけ直すと検査と実際の適用順がずれた。
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

import mods_meta                              # noqa: E402

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


# ゲームに埋まっている CPython の版。
# MOD はこのインタプリタの中で動くので、
# ここより新しい構文を書くと**注入した瞬間に SyntaxError で落ちる**。
# 手元の python は 3.13 しか無く、`compileall` は 3.13 として通してしまうため、
# パーサに版を指定して 3.11 以降の構文を弾く。
# 捕まるのは**構文だけ**。
# 3.11 以降で追加された標準ライブラリの関数を使った場合はここでは分からない（CI で実際に
# 3.10 を動かす方でしか捕まらない）。
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
                # 第1引数は必ず元の関数。
                # ここが違えば別の関数に付いている。
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
    `@ctx.wrap` は入口以外のファイルにも書けるので、
    そこだけ検査から漏れるとこの検査の意味（デコレータの対象と引数の食い違いを注入前に捕まえる）が無くなる。
    `data/` のようなサブフォルダも辿る。
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
    リテラルとして読めない代入（式・関数呼び出し）は入れない
    ― そこは「宣言された既定値と比べられない」であって、間違いではない。
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

    フォルダ名も入口のファイル名も自由なので、**入口がどれかは
    mod.json だけが知っている**。
    ここが食い違うと mod が黙って読み込まれないので、注入前に静的に捕まえる。

    名乗り（name / description / version / author）は**仕様では任意**なので、
    欠けていても「問題」にはしない（外部の
    mod が任意の項目で検査に落ちるのは筋が通らない）。
    同梱 mod は全部揃えておきたいので、`--strict` のときだけ見落としとして数える。
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

    # ローダ API。
    # ここで撥ねられる mod は注入しても読み込まれない。
    verdict, reason = ml.api_status(manifest)
    if verdict:
        problems.append((path, MANIFEST_NAME,
                         "{}: {}（このローダは API {}）".format(verdict, reason, ml.API)))
    if "api" not in data:
        notes.append((path, MANIFEST_NAME,
                      '"api" が無い（{} として扱う）'.format(ml.DEFAULT_API)))

    # 表示用の項目。
    # 無くても動くので、報告はするが問題として数えない。
    for key in ("name", "description", "version", "author"):
        if not data.get(key):
            notes.append((path, MANIFEST_NAME, "{!r} が空（表示だけの項目）".format(key)))

    # 種別の名乗り（"kind"）。GUI の「種別」列がこれを読む。
    # 無いのは他の表示項目と同じく note（外部の mod が任意の項目で落ちない）。
    # **書いてあるのに語彙の外**は問題 ― ローダが無指定に均すので、
    # 書いた本人の意図と違って「-」が出続けることになる。
    kind = data.get("kind")
    if kind is None:
        notes.append((path, MANIFEST_NAME,
                      '"kind" が無い（種別の列に「-」が出る）'))
    elif str(kind).strip().lower() not in ml.KINDS:
        problems.append((path, MANIFEST_NAME,
                         '"kind" が語彙に無い: {!r}（使えるのは {}）'
                         .format(kind, " / ".join(ml.KINDS))))

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

    **既定値が2箇所に書かれている**のがこの検査の存在理由。
    実際に使われるのはコードの定数で、GUI が表示に使うのは `mod.json` の `"default"`（GUI は mod のコードを
    import しない決まりなので定数を読めない）。
    ずれると「GUI では既定 3 と出るのに実際は 5 で動く」という、
    最も気付きにくい形になる。
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
    ここはその報告を検査の書式に移し替えるだけ。
    適用順は動作の前提なので、**宣言と実体のずれは黙って通さない**。
    """
    path = ml.order_path(MODS_DIR)
    name = os.path.basename(path)
    problems = [(path, name, line) for line in found["problems"]]
    # `notes` は「直すべきずれではない知らせ」（手元用の順序ファイルを使っている、
    # など）。
    # **問題として数えない** ― 数えると、未公開の MOD を手元で動かしている間ずっと赤が出ることになり、
    # 本当のずれが埋もれる。
    # `--strict` では呼び出し側が問題に格上げする。
    notes = [(path, name, line) for line in found.get("notes", [])]
    return problems, notes


#: 名前がぶつかると静かに壊れるもの。値の先頭で見分ける。
#:
#:   mod_          自前ボタンの印（`ui.MARK_PREFIX`）と LLM の `manager_name`
#:   _instantale   ウィジェットや `sys` に付ける属性
NAMESPACE_PREFIXES = ("mod_", "_instantale")

#: 値では見分けられないので**定数の名前**で拾うもの。
#: `state/` のフォルダ名は `"npc_profiles"` のような普通の語なので、
#: 値だけを見ても他の文字列と区別が付かない。
NAMESPACE_SUFFIXES = ("STATE_DIRNAME", "_DIRNAME")

#: 「この名前は自分のものではない」と `mod.json` で断る鍵。
SHARES_KEY = "shares"


def _module_names(path):
    """1ファイルのモジュール直下の文字列定数を `{値: [定数名, ...]}` で返す。

    見るのはモジュール直下だけ。
    関数の中で組み立てる名前（`404_` の読み取り先など）は追わない ―
    **見えるものだけを確かめる**のがこの検査の立場で、
    見えないものまで拾おうとすると誤検知でこの検査自体が信用されなくなる。
    """
    out = {}
    tree, _problem = _parse(path)
    if tree is None:
        return out
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value.value
        wanted = value.startswith(NAMESPACE_PREFIXES) or any(
            n == NAMESPACE_SUFFIXES[0] or n.endswith(NAMESPACE_SUFFIXES[1])
            for n in names)
        if wanted and value:
            out.setdefault(value, []).extend(names)
    return out


def check_namespaces(found):
    """MOD どうしで名前がぶつかっていないか。

    ぶつかると**例外は出ない**。
    ボタンの印を共有すれば相手の `on_button_press` が自分のボタンを握り潰し、
    `state/` のフォルダ名を共有すれば2本が同じ控えを書き合う。
    どちらも「なぜか効かない」としてしか現れないので、静的に捕まえる。

    **共有そのものは禁じない。**
    他の MOD の控えを読む・相手が置いた印を見る、はこのローダの正しい繋がり方で
    （MOD どうしは import しない。TECH.md §3.2.3）、実際に4本がそうしている。
    区別するのは**断ってあるかどうか**だけ:

        "shares": ["npc_profiles"]      この名前は自分のものではない、の意思表示

    同じ名前を使う MOD のうち、断っていないものが**ちょうど1本**なら持ち主が
    決まっていて正常。0本（誰も名乗らない）と2本以上（取り合い）は問題。
    """
    installed = found["installed"]
    owners, users = {}, {}
    for name in installed:
        # ローダの `_manifest()` は知っている鍵だけを写すので、`shares` は
        # 素の `mod.json` から読む。
        declared = mods_meta.manifest(name, MODS_DIR).get(SHARES_KEY)
        declared = set(declared) if isinstance(declared, (list, tuple)) else set()
        for path in _mod_files(name):
            for value, consts in _module_names(path).items():
                users.setdefault(value, {})[name] = consts
                if value not in declared:
                    owners.setdefault(value, set()).add(name)

    problems, notes = [], []
    for value, mods in sorted(users.items()):
        if len(mods) < 2:
            continue
        holders = sorted(owners.get(value, ()))
        if len(holders) == 1:
            continue
        where = ", ".join("{}（{}）".format(m, "/".join(sorted(set(c))))
                          for m, c in sorted(mods.items()))
        if not holders:
            message = ('名前 {!r} を持つ MOD が居ない（{}）。'
                       '全部が "{}" で断っている'.format(value, where, SHARES_KEY))
        else:
            message = ('名前 {!r} を {} 本が取り合っている（{}）。'
                       '持ち主以外は mod.json の "{}" に書くこと'
                       .format(value, len(holders), where, SHARES_KEY))
        entry = (os.path.join(MODS_DIR, holders[0] if holders else
                              sorted(mods)[0], MANIFEST_NAME),
                 MANIFEST_NAME, message)
        # 開発中の mod（9xx）が絡むものは note。理由は `main` の分岐と同じ。
        (notes if any(ml.is_wip(m) for m in mods) else problems).append(entry)
    return problems, notes


def check_notice(found):
    """提供を受けた MOD の作者が `NOTICE` に載っているか。

    `LICENSE` は MIT の原文で、著作権表示は「複製に付いて回る」ことを要求する。
    同梱物の一部が他者の著作物なら、その旨が配布物の中に無ければならない。
    `mod.json` の `author` は名乗りであって権利の表示ではないので、
    そこに書いてあることは根拠にならない。

    ここで見るのは**名前が NOTICE の本文に在るか**だけ。
    文面の良し悪しは機械では見られないが、
    「新しい提供者が増えたのに NOTICE を直し忘れた」は必ず捕まる
    ― 実際 `404_party_talk` が入ったとき、NOTICE は
    「同梱 MOD はすべて本プロジェクトの著作物」と言ったままだった。

    開発中の MOD（9xx）は配布物に入らないので見ない（TECH.md §2.6）。
    """
    path = os.path.join(_ROOT, "NOTICE")
    name = "NOTICE"
    try:
        with io.open(path, encoding="utf-8") as fh:
            body = fh.read()
    except OSError:
        return [(path, name, "NOTICE が読めない")], []

    shipped = [n for n in found["installed"] if not ml.is_wip(n)]
    rows = mods_meta.contributed(shipped, MODS_DIR)
    problems, notes = [], []
    # 雛形の見本の名前（`your name here`）を消し忘れたまま取り込むと、
    # `contributed()` がそれを作者として数えないので、
    # **提供なのに提供として現れない**。名前が要ることだけ知らせる。
    for name in shipped:
        data = mods_meta.manifest(name, MODS_DIR)
        left = [who for who in mods_meta.authors(data)
                if who in mods_meta.PLACEHOLDER_AUTHORS]
        if left:
            notes.append((os.path.join(MODS_DIR, name, "mod.json"), "mod.json",
                          '"author" が雛形のまま（{}）。'
                          "誰の著作物かが決まらない".format(", ".join(left))))
    for who in mods_meta.names_of(rows):
        if who in body:
            continue
        where = [folder for folder, names, _shared in rows if who in names]
        problems.append((path, name,
                         '提供者 "{}" が NOTICE に載っていない（{}）。'
                         "同梱物の一部が他者の著作物であることは"
                         "配布物の中に書いてあること".format(
                             who, ", ".join(where))))
    return problems, notes


def _only(argv):
    """`--only <文字列>` / `--only=<文字列>`。フォルダ名の部分一致。

    提供を受けた1本を見るときに、87本ぶんの出力を読まずに済ませるためのもの。
    **全体の検査（並び・NOTICE・名前の取り合い）は同時に外れる** ―
    どれも「他の mod との関係」を見るもので、1本だけ見ても答えが出ないため。
    """
    for n, arg in enumerate(argv):
        if arg.startswith("--only="):
            return arg.split("=", 1)[1]
        if arg == "--only" and n + 1 < len(argv):
            return argv[n + 1]
    return None


def main():
    strict = "--strict" in sys.argv
    only = _only(sys.argv[1:])
    # デバッグモードの設定に関わらず、入っている mod を全部見る。
    # 切っている間だけ計測 mod の `after` が誰にも確かめられない、
    # という穴を作らないため。
    found = ml.discover(MODS_DIR, debug=True)
    # `local/` の mod は検査しない（TECH.md §2.6）。
    # 配布物にも CI にも入らないもので、この検査が見ているのは
    # 「配る形になっているか」だから、当てる意味が無い。
    # `installed` から先に落とすので、以降の全体検査（NOTICE・名前空間・順序）
    # にも現れない。
    local = found.get("local") or set()
    if local:
        found = dict(found, installed=[n for n in found["installed"]
                                       if n not in local])
    mods = found["installed"]
    if only:
        mods = [name for name in mods if only in name]
        if not mods:
            print("--only {!r} に当たる mod が無い。".format(only))
            return 1

    problems, notes = [], []
    for name in mods:
        mod_problems, mod_notes = check_manifest(name, found["manifests"][name])
        for path in _mod_files(name):
            mod_problems += check_file(path)
        # 開発中の mod（9xx。TECH.md §2.6）は**見るが、赤にはしない**。
        # 配布物にも `load_order.json` にも入らないものなので、
        # 書きかけの一本で CI が止まると、リリースする側の検査が使えなくなる。
        # 検査そのものを飛ばさないのは、直しどきに手掛かりが無くなるため ―
        # `note` として出す。
        # `--strict` は今までどおり note も問題に格上げする。
        if ml.is_wip(name):
            notes += mod_problems + mod_notes
        else:
            problems += mod_problems
            notes += mod_notes
    if not only:
        # ここから下は**全体を見る検査**。`--only` のときは走らせない
        # （1本だけ見ても答えが出ない。`_only` の説明を参照）。
        for check in (check_order, check_notice, check_namespaces):
            got_problems, got_notes = check(found)
            problems += got_problems
            notes += got_notes

    if strict:
        problems += notes
        notes = []

    def show(entries, head):
        for path, func, message in entries:
            # フォルダ名を残す。
            # ファイル名だけだと、どの mod の話か分からなくなる。
            label = os.path.relpath(path, MODS_DIR).replace(os.sep, "/")
            print("{} {} :: {}\n    {}".format(head, label, func, message))

    show(problems, "MISMATCH")
    show(notes, "note    ")
    print("checked {} mod(s){}; problems: {}{}".format(
        len(mods), "（--only {}。全体の検査は外れている）".format(only) if only else "",
        len(problems),
        "; notes: {}".format(len(notes)) if notes else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
