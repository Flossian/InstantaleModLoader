# -*- coding: utf-8 -*-
r"""111_llm_prompt_replace の置換ルール画面（tool.py）を窓抜きで通す。

    python tools/tests/test_llm_prompt_replace_tool.py

  往復    … 読んで書き戻すと1行も変わらない。同梱の既定でも、取りこぼしやすい形でも
  取りこぼし … 連続する #memo:・タブ行の直前の #memo:・先頭の説明・空行・書式を誤った行が残る
  同一性  … 往復してもゲームが見るルール（本体の parse_rules の結果）が変わらない
  欄      … 欄とファイルの行き来（\n だけを改行にする。\\n や \t や正規表現のパターンは触らない）
  検査    … 置換前が空・確率の範囲・=> の混入・不正な正規表現・regex: 始まり・タブ名の重複
  置き場  … 読む先は state\\ → 旧い置き場（MOD フォルダ）→ 同梱の既定。指定が最優先
  保存    … 書き先は state\\（指定が在ればそこ）。同梱の既定は触らない。BOM と CRLF が保たれる
  置換    … 置換テストの結果が本体の判定と一致する

画面（tkinter）は開かない。
窓が開くかどうかは `tools/check_tool_screens.py --only 111`（TECH.md §3.12）。
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "111_llm_prompt_replace")

# 本体は `instantale_modloader.llm` を引く（画面では `modtool.add_loader_path` が通す）。
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load("llm_prompt_replace_tool_under_test", os.path.join(MOD_DIR, "tool.py"))
engine = load("llm_prompt_replace_under_test", os.path.join(MOD_DIR, "llm_prompt_replace.py"))

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def rule_keys(lines):
    """ゲームが見るルール（本体の読み方）。往復の前後で一致していること。"""
    rules, _warnings = engine.parse_rules(lines)
    return [(rule.key, rule.to_text, rule.prob) for rule in rules]


# 取りこぼしやすい形を1つに集めたファイル。
# どれも実際の `llm_replacements.txt` に在る形で、移植元の GUI は保存のたびに落としていた。
SAMPLE = "\n".join([
    "# ============================================================",
    "# 先頭の説明。ここは書き換えない",
    "# ============================================================",
    "",
    "#tab:標準",
    "",
    "#memo:1行目のメモ",
    "#memo:2行目のメモ（移植元は1行目を落としていた）",
    "置換前A=>置換後A",
    "#off:止めてあるルール=>置換後B=>60",
    "regex:HPが(\\d+)残っている=>HPが$1ポイント残っている",
    "# ただのコメント",
    "書式を誤った行（矢印が無い）",
    "",
    "#memo:このメモはタブへの覚え書き（直後がタブ行）",
    "#offtab:止めてあるタブ",
    "#off:regex:古い(パターン)=>新しい$1=>30",
    "タブごと止まっているルール=>置換後C",
    "",
])

tmp = tempfile.mkdtemp(prefix="llm_prompt_replace_tool_test_")
try:
    # ------------------------------------------------------------ 往復（取りこぼし）
    doc = tool.Document.parse(SAMPLE)
    rendered = doc.render()
    check("往復: 1行も変わらない", rendered == SAMPLE,
          "\n--- 読んだ ---\n{}\n--- 書き戻し ---\n{}".format(SAMPLE, rendered))
    check("往復: 2回目も同じ", tool.Document.parse(rendered).render() == rendered)
    check("往復: ゲームが見るルールが変わらない",
          rule_keys(SAMPLE.split("\n")) == rule_keys(rendered.split("\n")))

    check("読み: タブは2枚", len(doc.tabs) == 2)
    check("読み: タブの入切", [tab.enabled for tab in doc.tabs] == [True, False])
    first = doc.tabs[0]
    check("読み: 先頭の説明はどのタブにも属さない", len(doc.head) == 4 and doc.head[0].startswith("#"))
    check("読み: 連続する #memo: が2行とも残る", first.rules[0].memo == [
        "1行目のメモ", "2行目のメモ（移植元は1行目を落としていた）"])
    check("読み: タブ行の直前の #memo: はタブの覚え書き",
          doc.tabs[1].memo == ["このメモはタブへの覚え書き（直後がタブ行）"])
    check("読み: #off: のルールは止まっている", first.rules[1].enabled is False)
    check("読み: 確率", [rule.prob for rule in first.rules] == [100, 60, 100])
    check("読み: 正規表現の印", [rule.is_regex for rule in first.rules] == [False, False, True])
    check("読み: #off:regex: の順",
          doc.tabs[1].rules[0].enabled is False and doc.tabs[1].rules[0].is_regex is True)
    check("読み: 書式を誤った行は消さずに残す",
          any(isinstance(item, str) and item.startswith("書式を誤った行") for item in first.items))
    check("数え: 件数と効いている件数", doc.count() == (5, 2))

    # ------------------------------------------------------------ 欄とファイルの行き来
    check(r"欄: \n は改行になる", tool.to_display(r"前\n後") == "前\n後")
    check(r"欄: \\n は改行にしない", tool.to_display(r"前\\n後") == r"前\\n後")
    check(r"欄: \t と \uXXXX は触らない",
          tool.to_display(r"前\t中あ後") == r"前\t中あ後")
    check("欄: 改行はファイルでは 2文字に戻る", tool.from_display("前\n後") == r"前\n後")
    for text in (r"前\n後", r"前\\n後", r"前\t中あ後", r"(\d+)行"):
        check("欄: 往復で変わらない（{}）".format(text),
              tool.from_display(tool.to_display(text)) == text)

    # ------------------------------------------------------------ 確率の読み
    check("確率: 範囲の外は置換後の一部",
          tool.parse_rule_body("前=>後=>200", True).to_text == "後=>200")
    check("確率: 省略は 100", tool.parse_rule_body("前=>後", True).prob == 100)
    check("確率: 100 は書かない", tool.Rule(True, False, 100, "前", "後").line() == "前=>後")
    check("確率: 100 以外は書く", tool.Rule(True, False, 60, "前", "後").line() == "前=>後=>60")
    check("ルールでない行は None", tool.parse_rule_body("矢印が無い", True) is None)

    # ------------------------------------------------------------ 入力の検査
    def why(rule=None, tabs=None):
        document = tool.Document()
        document.tabs = tabs or [tool.Tab("標準", True)]
        if rule is not None:
            document.tabs[0].items.append(rule)
        return tool.validate(document)[2]

    check("検査: 通る", why(tool.Rule(True, False, 100, "前", "後")) == "")
    check("検査: 置換前が空", "置換前が空" in why(tool.Rule(True, False, 100, "", "後")))
    check("検査: 確率の範囲", "0〜100" in why(tool.Rule(True, False, 120, "前", "後")))
    check("検査: => の混入", "=>" in why(tool.Rule(True, False, 100, "前=>x", "後")))
    check("検査: 不正な正規表現", "正規表現" in why(tool.Rule(True, True, 100, "(未閉じ", "後")))
    check("検査: 置換前が regex: 始まり",
          "regex:" in why(tool.Rule(True, False, 100, "regex:前", "後")))
    check("検査: タブ名の重複",
          "同じ名前" in why(tabs=[tool.Tab("同名", True), tool.Tab("同名", True)]))
    check("検査: 正しい正規表現は通る", why(tool.Rule(True, True, 100, r"HPが(\d+)", "HP $1")) == "")

    # ------------------------------------------------------------ 同梱の既定を読んで書き戻す
    default_path = os.path.join(MOD_DIR, tool.DEFAULT_RULES_FILE_NAME)
    text, bom, newline = tool.read_text(default_path)
    # 改行は見ない。`.gitattributes`（`eol=lf`）で checkout は LF、手元の作業ツリーは CRLF になり、
    # どちらで開いても読んだ形のまま書き戻すのがこの画面の約束（下の「往復」がそれを見る）。
    check("既定: BOM 付き", bom is True, (bom, newline))
    check("既定: 改行は LF か CRLF のどちらか", newline in ("\n", "\r\n"), repr(newline))
    shipped = tool.Document.parse(text)
    check("既定: 往復で1行も変わらない", shipped.render() == text)
    check("既定: ゲームが見るルールが変わらない",
          rule_keys(text.split("\n")) == rule_keys(shipped.render().split("\n")))

    # ------------------------------------------------------------ 置き場と保存
    model = tool.Model()
    # 本物のルールファイルを踏まないよう、MOD と state の場所を差し替えて読み直す。
    fake_mod = os.path.join(tmp, "mod")
    fake_state = os.path.join(tmp, "state")
    os.makedirs(fake_mod, exist_ok=True)
    model.mod_dir = fake_mod
    model.state_dir = fake_state
    state_file = os.path.join(fake_state, tool.STATE_DIRNAME, tool.RULES_FILE_NAME)
    tool.write_text(model.default_path, SAMPLE, bom=True, newline="\r\n")
    model.reload()
    check("置き場: 手元のルールが無ければ同梱の既定を読む", model.kind() == "default", model.kind())
    check("置き場: 保存先は state\\", model.target_path == state_file, model.target_path)
    check("置き場: 候補は state → 旧い置き場 → 同梱の既定",
          model.candidates == [state_file, model.legacy_path, model.default_path],
          model.candidates)

    # 旧い置き場（MOD フォルダ）に在れば、同梱の既定より先に読む。
    tool.write_text(model.legacy_path, SAMPLE, bom=True, newline="\r\n")
    model.reload()
    check("置き場: 旧い置き場は同梱の既定に優先する", model.kind() == "legacy", model.source_path)
    check("置き場: 旧い置き場を読んでいるときは保存で移る", model.moving() is True)
    check("保存: 開いただけでは未保存にならない", model.dirty() is False)
    model.doc.tabs[0].rules[0].to_text = "置換後A（変えた）"
    check("保存: 変えると未保存になる", model.dirty() is True)
    model.save()
    check("保存: 書き先は state\\", os.path.isfile(state_file))
    check("保存: 保存したら読む先も state\\ になる", model.kind() == "state", model.source_path)
    check("保存: 移った後はもう動かない", model.moving() is False)
    check("保存: 同梱の既定は変わらない",
          io.open(model.default_path, "rb").read() == (
              b"\xef\xbb\xbf" + SAMPLE.replace("\n", "\r\n").encode("utf-8")))
    check("保存: 旧い置き場のファイルは消さない", os.path.isfile(model.legacy_path))
    written = io.open(state_file, "rb").read()
    check("保存: BOM 付きの CRLF のまま",
          written.startswith(b"\xef\xbb\xbf") and b"\r\n" in written and b"\n\n" not in written)
    check("保存: 保存し終えたら未保存が落ちる", model.dirty() is False)
    model.reload()
    check("保存: 次からは state\\ のルールを読む", model.kind() == "state", model.source_path)
    check("保存: 変えたところが残っている",
          model.doc.tabs[0].rules[0].to_text == "置換後A（変えた）")
    check("保存: 中身は往復したまま（行数が変わらない）",
          len(model.doc.render().split("\n")) == len(SAMPLE.split("\n")))
    check("保存: .tmp を残さない", not os.path.isfile(state_file + ".tmp"))

    # ------------------------------------------------------------ 置き場の指定
    elsewhere = os.path.join(tmp, "別の場所", "別名.txt")
    tool.write_text(elsewhere, SAMPLE, bom=True, newline="\r\n")
    model.settings["RULES_PATH"] = elsewhere
    model.repath()
    check("指定: 指定した先を読む", model.source_path == elsewhere, model.source_path)
    check("指定: 書く先も指定した先", model.target_path == elsewhere, model.target_path)
    check("指定: 素性は「指定した置き場」", model.kind() == "custom", model.kind())
    model.settings["RULES_PATH"] = "別の束.txt"
    model.repath()
    check("指定: 相対は state のこの MOD のフォルダから",
          model.target_path == os.path.join(fake_state, tool.STATE_DIRNAME, "別の束.txt"),
          model.target_path)
    model.settings["RULES_PATH"] = ""

    # ------------------------------------------------------------ 置換テスト
    check("置換テスト: 本体を引けている", model.engine is not None)
    result, events, warnings = model.preview("置換前Aがある文章")
    check("置換テスト: 当たる", result == "置換後A（変えた）がある文章", result)
    check("置換テスト: 何が起きたかを返す",
          bool(events) and events[0].startswith("[REPLACE]"), str(events))
    result, _events, _warnings = model.preview("どのルールにも当たらない")
    check("置換テスト: 当たらなければそのまま", result == "どのルールにも当たらない")
    result, _events, _warnings = model.preview("タブごと止まっているルールを含む文章")
    check("置換テスト: 止めてあるタブは効かない",
          result == "タブごと止まっているルールを含む文章")
    result, _events, _warnings = model.preview("HPが12残っている")
    check("置換テスト: 正規表現と後方参照", result == "HPが12ポイント残っている", result)
    check("置換テスト: 書式の誤りは警告として返る",
          any("書式" in line or "無視" in line for line in model.warnings()),
          str(model.warnings()))

    # ------------------------------------------------------------ 設定
    # 設定の名前と既定値の出所は `mod.json` の "settings" だけ（`modtool.defaults`）。
    check("設定: 宣言の項目が揃っている",
          set(model.settings) == {"RULES_PATH", "LOG_REPLACE", "LOG_RULES"},
          str(model.settings))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("{} failure(s): {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("all ok")
