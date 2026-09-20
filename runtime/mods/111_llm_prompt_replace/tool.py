# -*- coding: utf-8 -*-
r"""置換ルールの編集画面。`llm_replacements.txt` を読み書きする。

    python runtime/mods/111_llm_prompt_replace/tool.py           窓を開く
    python runtime/mods/111_llm_prompt_replace/tool.py --dump    窓を開かず、いま読めるルールを標準出力に出す

TECH.md §3.12 の契約で動く（`322_battle_bgm` の道具と同じ）。
ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見てこのファイルを
サブプロセスで起動し、場所は環境変数で渡す。直接起動したときは自分で探す（`modtool.locate`）。

##### 何を移植したか

外部プロキシ（`InstantaleLLMProxy`）の管理 GUI の「置換ルール」タブ
（`src\gui\Gui.RulesTab.cs` と `Gui.RulesData.cs`）。
あちらの画面が持っていた他のもの（適用/解除・プロセス管理・OpenAI 互換・機能設定・
デバッグ設定・ログ表示）は移植していない。
ローダでは注入と `mod.json` の設定と `out\` がそれぞれ引き受けていて、
ここに置くと入口が2つになる。

| 移植元 | ここ |
|---|---|
| 表での編集（有効・正規表現・確率・置換前・置換後・メモ） | 一覧＋下の編集欄。`DataGridView` が無いので、選んだ1件を下で編む。行の複製・移動は右クリックとキー（Insert / Delete / Ctrl+D / Ctrl+↑↓） |
| セル内改行は Shift+Enter、ファイルへは `\n` の2文字 | 置換前／置換後の欄が複数行。ファイルへ書くときに `\n` へ戻す（`from_display`） |
| タブ（追加・名前変更・削除・タブ単位の入切・見出しのドラッグで並び替え） | 同じ。操作はタブ見出しの右クリックと「…」に置き、並び替えはそこの「左へ／右へ」（`ttk.Notebook` の見出しは掴めない） |
| 先頭の「すべて」タブ（横断表示・検索・ダブルクリックで該当行へ） | 同じ |
| 置換テスト | 同じ。ただし判定は**本体（`llm_prompt_replace.py`）をそのまま呼ぶ** |
| 保存・再読込・フォルダを開く | 同じ |

##### 判定は写さない。本体を呼ぶ

移植元の置換テスト（`OnPreview`）は、確率のグループ分けと抽選を GUI 側に**写して**持っていた。
写した時点でずれは予告されている（TECH.md §3.2.3）。
ここは同じ MOD の本体を import して
`parse_rules` → `group_rules` → `decide` → `apply_chosen` を通す。
画面で試した結果と、ゲームが実際に送る文章の書き換えが同じ判定になる。

MOD どうしの import を禁じる規約（§3.2.3）に触れないのは、
**自分の MOD の中**だから。
本体が引くのは `instantale_modloader.llm` だけなので、ゲームの外でも import できる
（`modtool.add_loader_path` でローダを先に引ける状態にしておくこと）。
引けなかったときは置換テストだけを止め、編集と保存は続けられるようにしてある。

##### ファイルの中身を落とさない

本体の `parse_rules` は**読み捨てる**ものがある。
`#memo:`・タブの名前・コメント・空行・書式を誤った行。
ゲームは使わないので正しいが、画面が同じ読み方をすると**保存のたびに消える**。
実際、同梱の `llm_replacements.txt` は次の形を使っている:

- `#memo:` が2行続く（移植元の GUI は最後の1行しか覚えず、前の行を落とす）
- `#memo:` が `#offtab:` の直前に在る（タブへの覚え書き。移植元はタブ行で捨てる）
- 先頭に MOD 固有の説明が 34 行（移植元は保存のたびに自分の説明文へ書き換える）

そこでこの画面は**行の並びをそのまま持つ**（`Document`）。
ルール行だけを組み直し、それ以外の行は読んだ姿のまま書き戻す。
`#memo:` は続く行数ぶんそのまま残し、タブ行の直前に在ったものはタブの覚え書きとして扱う。

##### 置き場の判断は本体に聞く

読む先も書く先も本体（`llm_prompt_replace`）が決める。
この画面は `rules_path` / `rules_target` / `rules_candidates` を呼ぶだけで、
順番を写さない（写すと、ゲームが読むファイルと画面が編むファイルが食い違う）。

    <設定 RULES_PATH>                            指定が在ればこれが最優先
    state\\llm_prompt_replace\\llm_replacements.txt   既定の置き場。**保存先はここ**
    <MOD>\\llm_replacements.txt                   旧い置き場。読むだけ
    <MOD>\\llm_replacements.default.txt           同梱の既定。読むだけ

**同梱の既定には書かない**（配布物なので MOD の更新で消える）。
既定しか無い状態で保存すると、その内容が `state\\` に生まれる。
旧い置き場のルールを読んでいる状態で保存した場合も `state\\` へ移る（元のファイルは消さない
― 消すのは手で書いたものを勝手に片付けることになるので、画面には「もう読まれない」とだけ出す）。

本体を引けないときだけ、順番の縮退版をここで組む（`Model.paths`）。
指定（`RULES_PATH`）の解決は本体にしか無いので、そのときは既定の置き場と同梱の既定だけを見る。

##### 移植元の入力検査のうち、1つは持ってこない

あちらは置換前／置換後が「JSON 文字列の中身として妥当か」を見ていた（`CheckJsonStringSafe`）。
プロキシは JSON にした後のリクエスト本文を生テキストのまま書き換えていたので、
`"` や制御文字を入れるとボディの JSON が壊れたため。
ここが見るのは復号済みの Python 文字列なので、`"` も生の制御文字も壊さない。
持ってくると**正しいルールを弾く**検査になる。

代わりに、保存の直前に本体の `parse_rules` を通して警告（`[RULES]` に出るもの）を拾い、
画面に出す。

##### 設定（`mod.json` の `"settings"`）もここで引き受ける

`"tool"` を宣言した MOD では、ローダの「設定…」がこの画面を開く（§3.12）。
`LOG_REPLACE` と `LOG_RULES` の置き場は他の MOD と同じ
`settings\mod_settings.json`（`modtool.load_settings` / `save_settings`）。
"""

import codecs
import io
import os
import re
import sys
import time

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 自分の隣（`llm_prompt_replace.py`）を import できるようにする。
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)

# 共有の土台（`tools/modtool.py`）を import できるようにする。
# `IML_ROOT` が指す先に `tools/` が無いこと（オフラインの検査）と、
# 環境変数の無い直接起動の両方があるので、3つ上も候補に入れる。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)

import modtool  # noqa: E402

MOD_NAME = modtool.mod_name(MOD_DIR)

#: 窓の題。未保存のときは後ろに付く。
TITLE = "LLM への指示文の置換ルール"

#: 書式。本体を引けなかったときに倒れる先で、値は本体の定数と同じ（`_engine` が上書きする）。
SEPARATOR = "=>"
REGEX_PREFIX = "regex:"
TAB_PREFIX = "#tab:"
OFFTAB_PREFIX = "#offtab:"
OFF_PREFIX = "#off:"
MEMO_PREFIX = "#memo:"
RULES_FILE_NAME = "llm_replacements.txt"
DEFAULT_RULES_FILE_NAME = "llm_replacements.default.txt"
STATE_DIRNAME = "llm_prompt_replace"

#: タブ行の無いファイル（旧い形式）を読んだときのタブ名。移植元と同じ。
DEFAULT_TAB_NAME = "標準"

#: 一覧のセルに出す長さ。超えたぶんは `…` にする。
CELL_CHARS = 70

#: 置換テストの入力欄に最初から入っている文章（移植元と同じ例文）。
SAMPLE_TEXT = "あなたはダークファンタジーRPGのキャラクター生成AIだ。"

#: いま読んでいるファイルの素性（`Model.kind`）の言い方。画面と `--dump` で同じ語を使う。
KIND_LABEL = {
    "custom": "指定した置き場",
    "state": "手元のルール",
    "legacy": "旧い置き場（MOD のフォルダ）",
    "default": "同梱の既定",
    "other": "指定した置き場",
    "missing": "まだ無い",
}


def load_engine(root):
    """本体（`llm_prompt_replace.py`）。引けなければ `None`。

    本体は `instantale_modloader.llm` を import するので、先にローダへの道を作る。
    引けない状況（ローダの無い場所へ MOD だけ置いた）でも画面は開けたほうがよいので、
    ここで落とさず呼ぶ側に `None` を渡す。
    """
    try:
        modtool.add_loader_path(root, MOD_DIR)
        import llm_prompt_replace
        return llm_prompt_replace
    except Exception:
        return None


def adopt_format(engine):
    """書式の定数を本体のものに合わせる（写しが食い違わないように）。"""
    if engine is None:
        return
    for name in ("SEPARATOR", "REGEX_PREFIX", "TAB_PREFIX", "OFFTAB_PREFIX",
                 "RULES_FILE_NAME", "DEFAULT_RULES_FILE_NAME", "STATE_DIRNAME"):
        value = getattr(engine, name, None)
        if isinstance(value, str):
            globals()[name] = value


# ----------------------------------------------------------------- 欄とファイルの行き来
def to_display(text):
    r"""ファイルの形 → 欄に出す形。`\n` だけを実際の改行にする。

    他のエスケープ（`\t` `\uXXXX` `\\`）は触らない。
    触ると正規表現のパターンが意味ごと変わる（`\d` が壊れる）。
    `\\n`（本物の `\` と `n`）を改行と読み違えないよう、`\` は2文字ずつ進める。
    """
    if "\\" not in text:
        return text
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            following = text[index + 1]
            if following == "n":
                out.append("\n")
            else:
                out.append(char)
                out.append(following)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def from_display(text):
    r"""欄の形 → ファイルの形。実際の改行を `\n` の2文字へ戻す。

    ルールは1行1件なので、改行を含んだまま書くと次の行が別のルールになる。
    `to_display` の逆で、改行以外は何も変えない。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def cell(text, width=CELL_CHARS):
    """一覧のセルに出す1行。改行は見える形にして、長ければ切る。"""
    text = text.replace("\r", "").replace("\n", "\\n").replace("\t", "\\t")
    return text if len(text) <= width else text[:width] + "…"


# ----------------------------------------------------------------- ルールファイルの持ち方
class Rule(object):
    """一覧の1行。**ファイルに書いてある形のまま**持つ（復号しない）。

    復号（`\\n` → 改行、`\\uXXXX` → 文字）をするのは本体の仕事で、
    ここでするとルールファイルと画面が1対1でなくなる。
    """

    __slots__ = ("enabled", "is_regex", "prob", "from_text", "to_text", "memo")

    def __init__(self, enabled=True, is_regex=False, prob=100,
                 from_text="", to_text="", memo=None):
        self.enabled = enabled
        self.is_regex = is_regex
        self.prob = prob
        self.from_text = from_text
        self.to_text = to_text
        self.memo = list(memo or [])      # `#memo:` 1行につき1つ

    def copy(self):
        return Rule(self.enabled, self.is_regex, self.prob,
                    self.from_text, self.to_text, list(self.memo))

    def line(self):
        """ファイルに書く1行。"""
        head = ("" if self.enabled else OFF_PREFIX) + (REGEX_PREFIX if self.is_regex else "")
        tail = "" if self.prob == 100 else SEPARATOR + str(self.prob)
        return head + self.from_text + SEPARATOR + self.to_text + tail


class Tab(object):
    """タブ1枚。ルールと、ルールでない行（コメント・空行）を**読んだ順のまま**持つ。"""

    __slots__ = ("name", "enabled", "memo", "items")

    def __init__(self, name, enabled=True, memo=None):
        self.name = name
        self.enabled = enabled
        self.memo = list(memo or [])      # タブ行の直前に在った `#memo:`
        self.items = []                   # `Rule` か、そのままの行（`str`）

    @property
    def rules(self):
        return [item for item in self.items if isinstance(item, Rule)]

    def index_of(self, rule):
        return self.items.index(rule)


class Document(object):
    r"""ルールファイル1つ。**読んだ行を落とさずに書き戻せる**形で持つ。

    先頭の説明（`head`）・コメント・空行・書式を誤った行は、読んだ姿のまま `items` に残る。
    組み直すのはルール行だけ。
    """

    def __init__(self):
        self.head = []                    # 最初のタブ行／ルール行より前に在った行
        self.tabs = []

    # -- 読む ---------------------------------------------------------------
    @classmethod
    def parse(cls, text):
        r"""ファイルの本文から作る。読み方は本体の `parse_rules` と同じ順序で見る。

        `#memo:` は続く行に付ける（次がタブ行ならそのタブに、ルール行ならそのルールに）。
        間に別の行が挟まったら覚え書きではなくただのコメントとして扱い、その場に残す。
        """
        doc = cls()
        current = None            # いま読んでいるタブ
        raw_lines = []            # まだどこにも入れていない行（コメント・空行）
        memo_lines = []           # 直前から続いている `#memo:`

        def park(line):
            """ルールでない行を、いまの場所に置く。"""
            if current is None:
                doc.head.append(line)
            else:
                current.items.append(line)

        def flush_memo():
            """覚え書きとして使わなかった `#memo:` を、ただの行として戻す。"""
            for memo in memo_lines:
                raw_lines.append(MEMO_PREFIX + memo)
            del memo_lines[:]

        def flush_raw():
            for line in raw_lines:
                park(line)
            del raw_lines[:]

        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()             # 末尾の改行。`render` が最後に1つ足すので持たない
        for raw in lines:
            line = raw.strip().lstrip("\ufeff").strip()      # 先頭行の BOM を落とす
            if not line:
                flush_memo()
                raw_lines.append(raw.rstrip("\r"))
                continue
            if line.startswith(TAB_PREFIX) or line.startswith(OFFTAB_PREFIX):
                enabled = line.startswith(TAB_PREFIX)
                prefix = TAB_PREFIX if enabled else OFFTAB_PREFIX
                flush_raw()                                   # タブ行より前の行はいまの場所へ
                tab = Tab(line[len(prefix):].strip(), enabled, memo_lines)
                del memo_lines[:]
                doc.tabs.append(tab)
                current = tab
                continue
            if line.startswith(MEMO_PREFIX):
                flush_raw()
                memo_lines.append(line[len(MEMO_PREFIX):])
                continue

            enabled = True
            body = line
            if body.startswith(OFF_PREFIX):
                enabled = False
                body = body[len(OFF_PREFIX):].strip()
            elif body.startswith("#"):
                flush_memo()
                raw_lines.append(raw.rstrip("\r"))             # ただのコメント
                continue

            rule = parse_rule_body(body, enabled)
            if rule is None:
                # 書式を誤った行。本体は `[RULES]` に警告を出して無視する。
                # ここでも読まずに、そのまま残す（消すと直しようがなくなる）。
                flush_memo()
                raw_lines.append(raw.rstrip("\r"))
                continue
            rule.memo = list(memo_lines)
            del memo_lines[:]
            if current is None:
                # タブ行より前のルール（旧い形式のファイル）。移植元と同じく「標準」に入れる。
                current = Tab(DEFAULT_TAB_NAME, True)
                doc.tabs.append(current)
            flush_raw()
            current.items.append(rule)

        flush_memo()
        flush_raw()
        if not doc.tabs:
            doc.tabs.append(Tab(DEFAULT_TAB_NAME, True))
        return doc

    # -- 書く ---------------------------------------------------------------
    def render_lines(self):
        """ファイルに書く行の並び。"""
        lines = list(self.head)
        for tab in self.tabs:
            for memo in tab.memo:
                lines.append(MEMO_PREFIX + memo)
            lines.append((TAB_PREFIX if tab.enabled else OFFTAB_PREFIX) + tab.name)
            for item in tab.items:
                if isinstance(item, Rule):
                    for memo in item.memo:
                        lines.append(MEMO_PREFIX + memo)
                    lines.append(item.line())
                else:
                    lines.append(item)
        return lines

    def render(self):
        r"""ファイルに書く本文。行は `\n` で繋ぎ、CRLF への変換は `write_text` が1回だけ行う。"""
        return "\n".join(self.render_lines()) + "\n"

    def count(self):
        """`(ルールの件数, 効いている件数)`。効いている＝タブもルールも有効。"""
        total = 0
        active = 0
        for tab in self.tabs:
            for rule in tab.rules:
                total += 1
                if tab.enabled and rule.enabled:
                    active += 1
        return total, active


def parse_rule_body(body, enabled):
    """`regex:` と `置換前=>置換後=>確率` を読む。ルールでなければ `None`。

    確率の読み方は本体（`parse_rules`）と同じ。
    末尾の `=>…` が 0〜100 の整数として読めないときは置換後の一部として残す。
    """
    is_regex = body.startswith(REGEX_PREFIX)
    if is_regex:
        body = body[len(REGEX_PREFIX):]
    index = body.find(SEPARATOR)
    if index <= 0:
        return None
    from_text = body[:index]
    rest = body[index + len(SEPARATOR):]
    prob = 100
    tail_at = rest.rfind(SEPARATOR)
    if tail_at >= 0:
        tail = rest[tail_at + len(SEPARATOR):].strip()
        try:
            value = int(tail)
        except ValueError:
            value = None
        if value is not None and 0 <= value <= 100:
            prob = value
            rest = rest[:tail_at]
    return Rule(enabled, is_regex, prob, from_text, rest)


# ----------------------------------------------------------------- 入力の検査
def validate(doc):
    """最初の不備を `(タブ, ルール, 文言)` で返す。無ければ `(None, None, "")`。

    移植元の `CollectRules` の検査のうち、**JSON 文字列としての妥当性は見ない**
    （このファイルの docstring の「移植元の入力検査のうち、1つは持ってこない」）。
    """
    seen = set()
    for tab in doc.tabs:
        if not tab.name:
            return tab, None, "タブの名前が空です。"
        if "\n" in tab.name or tab.name.startswith("#"):
            return tab, None, "タブの名前に使えない文字があります（改行と行頭の # は使えません）。"
        if tab.name in seen:
            return tab, None, "同じ名前のタブがあります: {}".format(tab.name)
        seen.add(tab.name)
        for rule in tab.rules:
            if not rule.from_text:
                return tab, rule, "置換前が空です。"
            if not 0 <= rule.prob <= 100:
                return tab, rule, "置換確率は 0〜100 の整数で入力してください。"
            if SEPARATOR in rule.from_text or SEPARATOR in rule.to_text:
                return tab, rule, "「{}」は置換前にも置換後にも使えません（区切りに読まれます）。".format(
                    SEPARATOR)
            if rule.is_regex:
                try:
                    re.compile(rule.from_text)
                except Exception as exc:
                    return tab, rule, "正規表現が不正です: {}".format(exc)
            elif rule.from_text.startswith(REGEX_PREFIX):
                return tab, rule, (
                    "置換前を「{}」で始めることはできません"
                    "（ファイルの上で正規表現ルールと区別できなくなります）。".format(REGEX_PREFIX))
    return None, None, ""


# ----------------------------------------------------------------- ファイルの読み書き
def read_text(path):
    r"""`(本文, BOM が在ったか, 改行)`。読めなければ空の本文と既定（BOM 付き・CRLF）。

    BOM と改行を覚えておいて書くときに揃えるのは、同梱のファイルも移植元の GUI が
    書いたファイルも BOM 付きの CRLF だから。
    揃えないと、1行も変えずに保存しただけで全行が差分になる。
    """
    try:
        with io.open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return "", True, "\r\n"
    bom = data.startswith(codecs.BOM_UTF8)
    if bom:
        data = data[len(codecs.BOM_UTF8):]
    text = data.decode("utf-8", "replace")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), bom, newline


def write_text(path, text, bom=True, newline="\r\n"):
    """隣に書いてから差し替える（`modtool.write_json` と同じ手順）。

    `write_json` は JSON 専用なので、テキストの口はここに持つ。
    途中で電源が落ちても半分書けたルールファイルを残さないため（TECH.md §3.11.1）。
    """
    data = text.replace("\n", newline) if newline != "\n" else text
    raw = data.encode("utf-8")
    if bom:
        raw = codecs.BOM_UTF8 + raw
    tmp = path + ".tmp"
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with io.open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ----------------------------------------------------------------- 中身
class Model(object):
    """画面が見せるもの。tkinter を知らない（`--dump` からも使う）。"""

    def __init__(self):
        self.root, self.state_dir, self.game_dir = modtool.locate(MOD_DIR)
        self.engine = load_engine(self.root)
        adopt_format(self.engine)
        # 場所は**属性で持つ**（`MOD_DIR` を直に読まない）。
        # オフラインの検査が本物のフォルダを踏まずに済む（`tools/tests/` が差し替える）。
        self.mod_dir = MOD_DIR
        self.source_path = ""     # いま読んでいるファイル
        self.target_path = ""     # 保存する先（`rules_target`）
        self.candidates = []      # 優先順（`rules_candidates`）
        self.doc = Document()
        self.settings = {}
        self.saved_settings = {}
        self.saved_text = ""
        self.bom = True
        self.newline = "\r\n"
        self.reload()

    # -- 置き場 -------------------------------------------------------------
    @property
    def default_path(self):
        """同梱の既定。**読むだけ**（配布物なので MOD の更新で上書きされる）。"""
        return os.path.join(self.mod_dir, DEFAULT_RULES_FILE_NAME)

    @property
    def legacy_path(self):
        """旧い置き場（MOD のフォルダの手元のルール）。読むだけ。"""
        return os.path.join(self.mod_dir, RULES_FILE_NAME)

    @property
    def custom(self):
        """設定（`RULES_PATH`）に書かれた置き場。空なら既定の置き場を使う。"""
        return str(self.settings.get("RULES_PATH") or "")

    def state_path(self):
        r"""既定の置き場（`state\<この MOD>\llm_replacements.txt`）。引けなければ空。"""
        if not self.state_dir:
            return ""
        return os.path.join(self.state_dir, STATE_DIRNAME, RULES_FILE_NAME)

    def paths(self):
        """`(読む先, 書く先, 候補)`。判断は本体に聞く（順番を写さない）。

        本体を引けないときだけ縮退する。
        指定（`RULES_PATH`）の解決は本体にしか無いので、そのときは見ない。
        """
        if self.engine is not None:
            return (self.engine.rules_path(self.mod_dir, self.state_dir, self.custom),
                    self.engine.rules_target(self.mod_dir, self.state_dir, self.custom),
                    self.engine.rules_candidates(self.mod_dir, self.state_dir, self.custom))
        found = [path for path in (self.state_path(), self.legacy_path, self.default_path)
                 if path]
        for path in found:
            if os.path.isfile(path):
                return path, self.state_path(), found
        return self.default_path, self.state_path(), found

    def repath(self):
        """置き場を引き直す（指定を打ち替えたとき）。**読み直しはしない。**

        読み直すと編みかけが消えるので、変えた直後に動くのは
        「どこへ保存するか」と画面の案内だけ。
        新しい置き場の中身を読むのは「再読込」を押したとき（`reload`）。
        """
        self.source_path, self.target_path, self.candidates = self.paths()

    def kind(self):
        """いま読んでいるファイルの素性。画面の言い回しを1か所にまとめるため。"""
        if not self.source_path or not os.path.isfile(self.source_path):
            return "missing"
        if self.custom and self.candidates and self.source_path == self.candidates[0]:
            return "custom"
        if self.source_path == self.state_path():
            return "state"
        if self.source_path == self.legacy_path:
            return "legacy"
        if self.source_path == self.default_path:
            return "default"
        return "other"

    # -- 読み書き -----------------------------------------------------------
    def reload(self):
        """ディスクから読み直す。読む先の決め方は本体（`rules_path`）と同じ。"""
        self.settings = modtool.load_settings(self.root, MOD_DIR)
        self.saved_settings = dict(self.settings)
        self.repath()
        text, self.bom, self.newline = read_text(self.source_path)
        self.doc = Document.parse(text)
        # 「保存済みの姿」は読んだ本文そのものではなく**組み直した姿**。
        # ルール行は組み直すので（`=>100` を落とすなど）、
        # 生の本文と比べると開いただけで「未保存」になる。
        self.saved_text = self.doc.render()

    def from_default(self):
        """同梱の既定を読んでいる（手元のルールがまだ無い）か。"""
        return self.kind() == "default"

    def moving(self):
        """保存すると読む先が動くか（いまのファイルと保存先が違う）。"""
        return bool(self.target_path) and self.source_path != self.target_path

    def dirty(self):
        return (self.doc.render() != self.saved_text
                or self.settings != self.saved_settings)

    def save(self):
        r"""保存先（既定は `state\`）に書く。同梱の既定には**書かない**。"""
        if not self.target_path:
            raise OSError("保存先が決められません（state\\ の場所が引けませんでした）")
        text = self.doc.render()
        write_text(self.target_path, text, self.bom, self.newline)
        self.source_path = self.target_path
        self.saved_text = text
        if self.settings != self.saved_settings:
            modtool.save_settings(self.root, MOD_DIR, self.settings, strict=True)
            self.saved_settings = dict(self.settings)
        # 書いた先が今度は読む先になる（指定を変えていた場合も含めて引き直す）。
        self.repath()

    # -- 本体に聞く ---------------------------------------------------------
    def warnings(self):
        """本体が読んだときに `[RULES]` へ出す警告。引けなければ空。"""
        if self.engine is None:
            return []
        try:
            _rules, warnings = self.engine.parse_rules(self.doc.render_lines())
            return warnings
        except Exception as exc:
            return ["ルールを読めませんでした（{}）".format(exc)]

    def preview(self, text):
        """置換テスト。`(置換後, 起きたことの行, 警告)`。

        判定は本体そのもの。
        抽選は押すたびに引き直すので、確率 100 でないルールは結果が変わりうる。
        """
        if self.engine is None:
            return text, [], ["本体（llm_prompt_replace.py）を読み込めないので試せません。"]
        engine = self.engine
        try:
            rules, warnings = engine.parse_rules(self.doc.render_lines())
            groups = engine.group_rules(rules)
            chosen, skipped = engine.decide(groups, text)
            result, hits = engine.apply_chosen(text, chosen)
        except Exception as exc:
            return text, [], ["置換に失敗しました（{}: {}）".format(type(exc).__name__, exc)]
        events = []
        for rule, count, denom in hits:
            events.append("[REPLACE] {} → {}（{}か所・確率 {}/{}）".format(
                engine.snip(rule.disp_from), engine.snip(rule.disp_to),
                count, rule.prob, denom))
        for rule, _total, _denom, why in skipped:
            events.append("[SKIP] {}（{}）".format(engine.snip(rule.disp_from), why))
        return result, events, warnings


# ----------------------------------------------------------------- 画面
#
# 一覧が主役。常に見えるのは「どのファイルか」「タブ」「一覧」「選んだ1件の欄」だけ。
#
#   毎回触る   一覧・編集欄                          → 常に出す
#   ときどき   置換テスト                            → 下に畳んでおく（押すと開く）
#   稀         タブの追加や並び替え・行の複製や移動   → 右クリックと「…」の中
#   初回だけ   置き場・記録の設定                    → 「設定…」の小窓
#
# 説明文は画面に置かない。
# 最初に組んだ版は案内を7行常設していて、一覧が4行しか見えなかった。
# 意味が要るものはツールチップ（`tip`）に、置き場の説明は設定の小窓に置く。
def build_window(model):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(TITLE)
    root.minsize(900, 600)
    # 大きさと位置は前回のもの（`settings/gui.json` の `tool_window`）。
    modtool.restore_window(model.root, MOD_DIR, root, fallback="1180x800")
    # 配色と書体は設定画面のものを借りる。無ければ素の Tk。
    modtool.setup_theme(root, model.root)

    outer = ttk.Frame(root, padding=10)
    outer.pack(fill="both", expand=True)

    # 画面を組む途中の代入で「変えた」と数えないための旗と、画面の控え。
    state = {"quiet": False, "pages": [], "all": None, "test_open": False}

    def update_title():
        root.title(TITLE + ("（未保存）" if model.dirty() else ""))

    # -- ツールチップ ------------------------------------------------------
    def tip(widget, text):
        """マウスを乗せたときだけ出る1行。説明文を常設しないための受け皿。"""
        box = {"win": None}

        def show(_event=None):
            if box["win"] is not None:
                return
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry("+{}+{}".format(widget.winfo_rootx() + 12,
                                            widget.winfo_rooty() + widget.winfo_height() + 4))
            ttk.Label(win, text=text, style="TLabel",
                      padding=(6, 3), relief="solid", borderwidth=1).pack()
            box["win"] = win

        def hide(_event=None):
            if box["win"] is not None:
                box["win"].destroy()
                box["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)
        widget.bind("<ButtonPress>", hide)

    # -- 1行目: 題・いま読んでいるファイル・開く・設定… ---------------------
    head = ttk.Frame(outer)
    head.pack(fill="x")
    ttk.Label(head, text=TITLE, style="Title.TLabel").pack(side="left")
    ttk.Button(head, text="設定…", command=lambda: open_settings()).pack(side="right")
    open_link = ttk.Label(head, text="開く", style="Sub.TLabel", cursor="hand2")
    open_link.pack(side="right", padx=(10, 12))
    file_label = ttk.Label(head, text="", style="Sub.TLabel")
    file_label.pack(side="right")

    def open_folder(_event=None):
        """いま読んでいるファイルのフォルダ。無ければ保存先、それも無ければ MOD。"""
        for path in (model.source_path, model.target_path):
            folder = os.path.dirname(path or "")
            if folder and os.path.isdir(folder):
                os.startfile(folder)
                return
        os.startfile(MOD_DIR)

    open_link.bind("<Button-1>", open_folder)

    def update_file_line():
        kind = model.kind()
        text = "{}（{}）".format(os.path.basename(model.source_path or "") or "（無い）",
                                 KIND_LABEL.get(kind, kind))
        if model.moving() and kind != "missing":
            text += "　保存すると state\\ へ移る"
        file_label.configure(
            text=text, style="Warn.TLabel" if kind in ("legacy", "default", "missing") else "Sub.TLabel")

    tip(file_label, "いま読んでいるファイルの場所は「設定…」に出る")

    # -- 下から詰める（窓が低いときに一覧へ押し出されないように。`322_` と同じ） ---
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x", pady=(6, 0))

    # -- 置換テスト（畳んである） -----------------------------------------
    test_toggle = ttk.Label(outer, text="▸ 置換テスト", style="Group.TLabel", cursor="hand2")
    test_toggle.pack(side="bottom", anchor="w", pady=(6, 0))

    # 一覧・編集欄・置換テストは縦の仕切りで分け、境目をドラッグして大きさを変えられる。
    # 余った高さは一覧が取る（weight）。編集欄は自然な高さから始まり、引けば伸びる。
    paned = ttk.PanedWindow(outer, orient="vertical")
    paned.pack(fill="both", expand=True, pady=(6, 0))
    test = ttk.Frame(paned)
    # 開いている間の見出し。閉じているときは下の `test_toggle` が同じ役をする。
    test_head = ttk.Label(test, text="▾ 置換テスト", style="Group.TLabel", cursor="hand2")
    test_head.pack(anchor="w", pady=(0, 2))
    test_row = ttk.Frame(test)
    test_row.pack(fill="both", expand=True)
    test_in = tk.Text(test_row, height=4, wrap="word", undo=True)
    test_in.pack(side="left", fill="both", expand=True)
    test_in.insert("1.0", SAMPLE_TEXT)
    test_mid = ttk.Frame(test_row)
    test_mid.pack(side="left", padx=8)
    test_button = ttk.Button(test_mid, text="置換 →", command=lambda: run_preview())
    test_button.pack()
    test_out = tk.Text(test_row, height=4, wrap="word")
    test_out.pack(side="left", fill="both", expand=True)
    test_out.configure(state="disabled")
    test_note = ttk.Label(test, style="Faint.TLabel", text="")
    test_note.pack(anchor="w", pady=(2, 0))
    tip(test_button, "いまのルールを左の文章に当てる。押すたびに抽選し直す")

    def toggle_test(_event=None):
        state["test_open"] = not state["test_open"]
        if state["test_open"]:
            paned.add(test, weight=1)          # 3段目の区画として足す（境目は引ける）
            test_toggle.pack_forget()
        else:
            paned.forget(test)
            test_toggle.pack(side="bottom", anchor="w", pady=(6, 0), before=paned)

    test_toggle.bind("<Button-1>", toggle_test)
    test_head.bind("<Button-1>", toggle_test)

    def run_preview():
        text = test_in.get("1.0", "end-1c")
        result, events, warnings = model.preview(text)
        test_out.configure(state="normal")
        test_out.delete("1.0", "end")
        test_out.insert("1.0", result)
        test_out.configure(state="disabled")
        if events or warnings:
            test_note.configure(text="　/　".join(events + warnings)[:400])
        else:
            test_note.configure(text="当たるルールはありませんでした")

    if model.engine is None:
        test_button.state(["disabled"])
        test_note.configure(text="本体（llm_prompt_replace.py）を読み込めないので置換テストは使えない")

    # -- 選んだルールの編集欄 ---------------------------------------------
    editor = ttk.LabelFrame(paned, text="選んだルール", padding=(8, 4, 8, 8))

    flags = ttk.Frame(editor)
    flags.pack(fill="x")
    rule_on = tk.BooleanVar(value=True)
    rule_regex = tk.BooleanVar(value=False)
    rule_prob = tk.StringVar(value="100")
    on_check = ttk.Checkbutton(flags, text="有効", variable=rule_on)
    on_check.pack(side="left")
    regex_check = ttk.Checkbutton(flags, text="正規表現", variable=rule_regex)
    regex_check.pack(side="left", padx=(12, 0))
    ttk.Label(flags, text="確率(%)").pack(side="left", padx=(16, 4))
    prob_spin = ttk.Spinbox(flags, from_=0, to=100, width=5, textvariable=rule_prob)
    prob_spin.pack(side="left")
    tip(regex_check, "置換前を正規表現として読む（置換後で $1 が使える。改行に当てるなら \\n と書く）")
    tip(prob_spin, "同じ置換前のルールでまとめて1回だけ抽選する。合計が 100 を超えると必ずどれかに置き換わる")

    body = ttk.Frame(editor)
    body.pack(fill="both", expand=True, pady=(4, 0))
    for column, weight in ((0, 3), (1, 3), (2, 2)):
        body.columnconfigure(column, weight=weight)
    body.rowconfigure(1, weight=1)          # 区画を広げたぶんは欄が取る（4行は最小）
    ttk.Label(body, text="置換前", style="Group.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(body, text="置換後", style="Group.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))
    ttk.Label(body, text="メモ", style="Group.TLabel").grid(row=0, column=2, sticky="w", padx=(8, 0))
    from_box = tk.Text(body, height=4, wrap="word", undo=True)
    from_box.grid(row=1, column=0, sticky="nsew")
    to_box = tk.Text(body, height=4, wrap="word", undo=True)
    to_box.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
    memo_box = tk.Text(body, height=4, wrap="word", undo=True)
    memo_box.grid(row=1, column=2, sticky="nsew", padx=(8, 0))

    # -- ルールの一覧（タブごと） -----------------------------------------
    notebook = ttk.Notebook(paned)
    paned.add(notebook, weight=3)
    paned.add(editor, weight=0)

    COLUMNS = (("on", "有効", 40, "center"), ("rx", "正規", 40, "center"),
               ("prob", "確率", 48, "e"), ("from", "置換前", 300, "w"),
               ("to", "置換後", 300, "w"), ("memo", "メモ", 160, "w"))

    def make_tree(parent, columns, stretch_from=3):
        box = ttk.Frame(parent)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings",
                            selectmode="browse")
        for index, (key, label, width, anchor) in enumerate(columns):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor=anchor, stretch=index >= stretch_from)
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        # 効いていない行（タブが OFF かルールが OFF）は灰色（移植元と同じ）。
        tree.tag_configure("off", foreground="#888888")
        return box, tree

    # -- 編集欄と一覧の行き来 ----------------------------------------------
    def current_page():
        """いま開いているタブの控え。「すべて」タブなら `None`。"""
        try:
            index = notebook.index("current")
        except Exception:
            return None
        if index <= 0:
            return None
        return state["pages"][index - 1]

    def selected_rule():
        page = current_page()
        if page is None:
            return None, None
        item = page["tree"].focus()
        if not item:
            return page["tab"], None
        return page["tab"], page["rules"].get(item)

    def row_values(tab, rule, with_tab=False):
        values = ["✓" if rule.enabled else "", "✓" if rule.is_regex else "",
                  str(rule.prob), cell(rule.from_text), cell(rule.to_text),
                  cell(" ".join(rule.memo), 40)]
        return ([tab.name] + values) if with_tab else values

    def refresh_row(page, item, rule):
        page["tree"].item(item, values=row_values(page["tab"], rule),
                          tags=() if (rule.enabled and page["tab"].enabled) else ("off",))

    def load_editor(rule):
        """選んだ1件を下の欄へ。ここでの代入で「変えた」と数えない。"""
        state["quiet"] = True
        try:
            for box in (from_box, to_box, memo_box):
                box.configure(state="normal")   # disabled のままでは書き換えが通らない
            rule_on.set(bool(rule.enabled) if rule else True)
            rule_regex.set(bool(rule.is_regex) if rule else False)
            rule_prob.set(str(rule.prob) if rule else "100")
            for box, text in ((from_box, rule.from_text if rule else ""),
                              (to_box, rule.to_text if rule else ""),
                              (memo_box, "\n".join(rule.memo) if rule else "")):
                box.delete("1.0", "end")
                # 正規表現のパターンは復号しない（`\n` はパターンの一部）。
                if box is from_box and rule is not None and rule.is_regex:
                    box.insert("1.0", text)
                else:
                    box.insert("1.0", to_display(text))
            enabled = "normal" if rule is not None else "disabled"
            for box in (from_box, to_box, memo_box):
                box.configure(state=enabled)
            for widget in (on_check, regex_check, prob_spin):
                widget.state(["!disabled"] if rule is not None else ["disabled"])
        finally:
            state["quiet"] = False

    def apply_editor(*_args):
        """欄の内容をルールへ。1文字打つたびに当て、その行だけ描き直す。"""
        if state["quiet"]:
            return
        page = current_page()
        if page is None:
            return
        item = page["tree"].focus()
        rule = page["rules"].get(item)
        if rule is None:
            return
        rule.enabled = bool(rule_on.get())
        was_regex = rule.is_regex
        rule.is_regex = bool(rule_regex.get())
        try:
            value = int(str(rule_prob.get()).strip() or "100")
        except ValueError:
            value = None                      # 打ちかけ。最後に打てた値のままにする
        if value is not None and 0 <= value <= 100:
            rule.prob = value
        from_text = from_box.get("1.0", "end-1c")
        rule.from_text = from_text if rule.is_regex else from_display(from_text)
        rule.to_text = from_display(to_box.get("1.0", "end-1c"))
        memo = memo_box.get("1.0", "end-1c")
        rule.memo = [line for line in memo.split("\n") if line.strip()]
        if was_regex != rule.is_regex:
            # 正規表現に変える／やめると置換前の見せ方が変わる（復号するかどうか）。
            load_editor(rule)
        refresh_row(page, item, rule)
        update_title()

    def on_prob_blur(_event=None):
        """打ちかけの確率を、いまルールが持っている値に戻す。"""
        _tab, rule = selected_rule()
        if rule is not None and not state["quiet"]:
            state["quiet"] = True
            rule_prob.set(str(rule.prob))
            state["quiet"] = False

    rule_on.trace_add("write", apply_editor)
    rule_regex.trace_add("write", apply_editor)
    rule_prob.trace_add("write", apply_editor)
    for box in (from_box, to_box, memo_box):
        box.bind("<KeyRelease>", apply_editor)
        box.bind("<<Paste>>", lambda _e, box=box: box.after_idle(apply_editor))
        box.bind("<FocusOut>", apply_editor)
    prob_spin.bind("<FocusOut>", on_prob_blur)

    # -- 一覧を作り直す ----------------------------------------------------
    def fill_tree(page):
        tree = page["tree"]
        tree.delete(*tree.get_children())
        page["rules"] = {}
        for rule in page["tab"].rules:
            item = tree.insert("", "end", values=row_values(page["tab"], rule),
                               tags=() if (rule.enabled and page["tab"].enabled) else ("off",))
            page["rules"][item] = rule

    def refresh_all():
        """「すべて」タブ。検索語（タブ名・置換前・置換後・メモ）で絞る。"""
        view = state["all"]
        if view is None:
            return
        tree = view["tree"]
        tree.delete(*tree.get_children())
        view["rows"] = {}
        query = view["search"].get().strip().lower()
        shown = 0
        for tab in model.doc.tabs:
            for rule in tab.rules:
                if query and query not in " ".join(
                        (tab.name, rule.from_text, rule.to_text,
                         " ".join(rule.memo))).lower():
                    continue
                item = tree.insert("", "end", values=row_values(tab, rule, with_tab=True),
                                   tags=() if (rule.enabled and tab.enabled) else ("off",))
                view["rows"][item] = (tab, rule)
                shown += 1
        total, active = model.doc.count()
        view["count"].configure(text="{} / {} 件（効いているのは {} 件）".format(shown, total, active))

    def tab_text(tab):
        return "  {}{}  ".format("" if tab.enabled else "[OFF] ", tab.name)

    def rebuild(select=0, keep_rule=None):
        """タブの構成が変わったら丸ごと組み直す（枚数は多くて数枚）。"""
        for page in state["pages"]:
            notebook.forget(page["frame"])
            page["frame"].destroy()
        state["pages"] = []
        for tab in model.doc.tabs:
            frame = ttk.Frame(notebook, padding=(8, 6, 8, 8))
            notebook.add(frame, text=tab_text(tab))
            on_var = tk.BooleanVar(value=tab.enabled)
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=(0, 4))
            ttk.Checkbutton(row, text="このタブを有効にする", variable=on_var).pack(side="left")
            page = {"tab": tab, "frame": frame, "tree": None, "rules": {}, "on": on_var}
            state["pages"].append(page)
            # 右側: 追加・削除だけを見せ、残りは「…」の中（右クリックでも同じものが出る）。
            more = ttk.Menubutton(row, text="…", width=3)
            more.pack(side="right")
            more.configure(menu=make_menu(more, page))
            ttk.Button(row, text="削除", width=6,
                       command=lambda page=page: del_rule(page)).pack(side="right", padx=(6, 6))
            ttk.Button(row, text="追加", width=6,
                       command=lambda page=page: add_rule(page)).pack(side="right")
            if tab.memo:
                memo_note = ttk.Label(row, style="Faint.TLabel",
                                      text="メモ: " + cell(" ".join(tab.memo), 60))
                memo_note.pack(side="left", padx=(12, 0))
                tip(memo_note, "\n".join(tab.memo))
            box, tree = make_tree(frame, COLUMNS)
            box.pack(fill="both", expand=True)
            page["tree"] = tree

            def on_toggle(_a=None, _b=None, _c=None, page=page):
                if state["quiet"]:
                    return
                page["tab"].enabled = bool(page["on"].get())
                notebook.tab(state["pages"].index(page) + 1, text=tab_text(page["tab"]))
                fill_tree(page)
                update_title()

            on_var.trace_add("write", on_toggle)
            tree.bind("<<TreeviewSelect>>",
                      lambda _e, page=page: load_editor(page["rules"].get(page["tree"].focus())))
            tree.bind("<Button-1>", lambda e, page=page: on_click(e, page))
            tree.bind("<Button-3>", lambda e, page=page: on_row_menu(e, page))
            tree.bind("<Insert>", lambda _e, page=page: add_rule(page))
            tree.bind("<Delete>", lambda _e, page=page: del_rule(page))
            tree.bind("<Control-d>", lambda _e, page=page: dup_rule(page))
            tree.bind("<Control-Up>", lambda _e, page=page: (move_rule(page, -1), "break")[1])
            tree.bind("<Control-Down>", lambda _e, page=page: (move_rule(page, 1), "break")[1])
            fill_tree(page)
        try:
            notebook.select(max(0, min(select, len(state["pages"]))))
        except Exception:
            notebook.select(0)
        page = current_page()
        if page is not None:
            items = page["tree"].get_children()
            target = None
            if keep_rule is not None:
                for item in items:
                    if page["rules"].get(item) is keep_rule:
                        target = item
                        break
            target = target or (items[0] if items else None)
            if target:
                page["tree"].selection_set(target)
                page["tree"].focus(target)
                load_editor(page["rules"][target])
            else:
                load_editor(None)
        else:
            load_editor(None)
        refresh_all()
        update_file_line()
        update_title()

    def on_click(event, page):
        """「有効」「正規」の列は押したら入切する（移植元のチェック列と同じ手触り）。"""
        if page["tree"].identify("region", event.x, event.y) != "cell":
            return
        column = page["tree"].identify_column(event.x)
        if column not in ("#1", "#2"):
            return
        item = page["tree"].identify_row(event.y)
        rule = page["rules"].get(item)
        if rule is None:
            return
        if column == "#1":
            rule.enabled = not rule.enabled
        else:
            rule.is_regex = not rule.is_regex
        refresh_row(page, item, rule)
        page["tree"].selection_set(item)
        page["tree"].focus(item)
        load_editor(rule)
        update_title()
        return "break"

    # -- 右クリックと「…」 --------------------------------------------------
    def make_menu(parent, page):
        """行の操作とタブの操作を1つの menu に。右クリックも「…」も同じもの。"""
        menu = tk.Menu(parent, tearoff=0)
        menu.add_command(label="ルールを追加\tInsert", command=lambda: add_rule(page))
        menu.add_command(label="ルールを複製\tCtrl+D", command=lambda: dup_rule(page))
        menu.add_command(label="ルールを削除\tDelete", command=lambda: del_rule(page))
        menu.add_command(label="上へ\tCtrl+↑", command=lambda: move_rule(page, -1))
        menu.add_command(label="下へ\tCtrl+↓", command=lambda: move_rule(page, 1))
        menu.add_separator()
        menu.add_command(label="タブ追加", command=add_tab)
        menu.add_command(label="タブ名変更", command=rename_tab)
        menu.add_command(label="タブ削除", command=delete_tab)
        menu.add_command(label="タブを左へ", command=lambda: move_tab(-1))
        menu.add_command(label="タブを右へ", command=lambda: move_tab(1))
        return menu

    def on_row_menu(event, page):
        item = page["tree"].identify_row(event.y)
        if item:
            page["tree"].selection_set(item)
            page["tree"].focus(item)
            load_editor(page["rules"].get(item))
        menu = make_menu(root, page)
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    tab_menu = tk.Menu(root, tearoff=0)
    tab_menu.add_command(label="タブ追加", command=lambda: add_tab())
    tab_menu.add_command(label="タブ名変更", command=lambda: rename_tab())
    tab_menu.add_command(label="タブ削除", command=lambda: delete_tab())
    tab_menu.add_command(label="タブを左へ", command=lambda: move_tab(-1))
    tab_menu.add_command(label="タブを右へ", command=lambda: move_tab(1))

    def on_tab_menu(event):
        """タブ見出しの右クリック。押した見出しを選んでからメニューを出す。"""
        try:
            index = notebook.index("@{},{}".format(event.x, event.y))
        except Exception:
            return
        notebook.select(index)
        is_real = index >= 1
        for entry in ("タブ名変更", "タブ削除", "タブを左へ", "タブを右へ"):
            tab_menu.entryconfigure(entry, state="normal" if is_real else "disabled")
        tab_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    notebook.bind("<Button-3>", on_tab_menu)

    # -- ルールの出し入れ --------------------------------------------------
    def select_rule(page, rule):
        for item, found in page["rules"].items():
            if found is rule:
                page["tree"].selection_set(item)
                page["tree"].focus(item)
                page["tree"].see(item)
                load_editor(rule)
                return

    def add_rule(page):
        rule = Rule()
        _tab, current = selected_rule()
        items = page["tab"].items
        at = (items.index(current) + 1) if current in items else len(items)
        items.insert(at, rule)
        fill_tree(page)
        select_rule(page, rule)
        refresh_all()
        update_title()
        from_box.focus_set()

    def dup_rule(page):
        _tab, rule = selected_rule()
        if rule is None:
            return
        copy = rule.copy()
        page["tab"].items.insert(page["tab"].index_of(rule) + 1, copy)
        fill_tree(page)
        select_rule(page, copy)
        refresh_all()
        update_title()

    def del_rule(page):
        _tab, rule = selected_rule()
        if rule is None:
            return
        if not messagebox.askyesno(
                "ルールの削除",
                "このルールを削除します。よろしいですか？\n\n{}\n→ {}".format(
                    cell(rule.from_text), cell(rule.to_text)), parent=root):
            return
        page["tab"].items.remove(rule)
        fill_tree(page)
        items = page["tree"].get_children()
        if items:
            page["tree"].selection_set(items[0])
            page["tree"].focus(items[0])
            load_editor(page["rules"][items[0]])
        else:
            load_editor(None)
        refresh_all()
        update_title()

    def move_rule(page, step):
        """並びを1つ動かす。間に挟まっているコメント行は動かさない。"""
        _tab, rule = selected_rule()
        if rule is None:
            return
        rules = page["tab"].rules
        at = rules.index(rule)
        if not 0 <= at + step < len(rules):
            return
        other = rules[at + step]
        items = page["tab"].items
        here, there = items.index(rule), items.index(other)
        items[here], items[there] = items[there], items[here]
        fill_tree(page)
        select_rule(page, rule)
        refresh_all()
        update_title()

    # -- タブの出し入れ ----------------------------------------------------
    def ask_name(title, initial=""):
        dialog = tk.Toplevel(root)
        dialog.title(title)
        dialog.transient(root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="タブの名前").pack(anchor="w")
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(frame, textvariable=var, width=32)
        entry.pack(fill="x", pady=(4, 10))
        entry.focus_set()
        entry.selection_range(0, "end")
        answer = {"value": None}

        def ok(_event=None):
            answer["value"] = var.get().strip()
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", style="Accent.TButton",
                   command=ok).pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", ok)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        root.wait_window(dialog)
        return answer["value"]

    def find_tab(name):
        for tab in model.doc.tabs:
            if tab.name == name:
                return tab
        return None

    def add_tab():
        name = ask_name("タブ追加")
        if not name:
            return
        if find_tab(name):
            messagebox.showwarning("タブ追加", "同じ名前のタブがあります: " + name, parent=root)
            return
        model.doc.tabs.append(Tab(name, True))
        rebuild(select=len(model.doc.tabs))

    def rename_tab():
        page = current_page()
        if page is None:
            messagebox.showinfo("タブ名変更", "「すべて」タブの名前は変えられません。", parent=root)
            return
        name = ask_name("タブ名変更", page["tab"].name)
        if not name or name == page["tab"].name:
            return
        if find_tab(name):
            messagebox.showwarning("タブ名変更", "同じ名前のタブがあります: " + name, parent=root)
            return
        page["tab"].name = name
        rebuild(select=state["pages"].index(page) + 1)

    def delete_tab():
        page = current_page()
        if page is None:
            messagebox.showinfo("タブ削除", "「すべて」タブは削除できません。", parent=root)
            return
        if len(model.doc.tabs) <= 1:
            messagebox.showwarning("タブ削除", "最後のタブは削除できません。", parent=root)
            return
        count = len(page["tab"].rules)
        if not messagebox.askyesno(
                "タブ削除",
                "タブ「{}」とその中のルール {} 件を削除します。よろしいですか？".format(
                    page["tab"].name, count), parent=root):
            return
        at = model.doc.tabs.index(page["tab"])
        model.doc.tabs.pop(at)
        rebuild(select=max(1, at))

    def move_tab(step):
        page = current_page()
        if page is None:
            return
        tabs = model.doc.tabs
        at = tabs.index(page["tab"])
        if not 0 <= at + step < len(tabs):
            return
        tabs[at], tabs[at + step] = tabs[at + step], tabs[at]
        rebuild(select=at + step + 1)

    # -- 「すべて」タブ ----------------------------------------------------
    all_frame = ttk.Frame(notebook, padding=(8, 6, 8, 8))
    notebook.add(all_frame, text="  すべて  ")
    all_bar = ttk.Frame(all_frame)
    all_bar.pack(fill="x", pady=(0, 4))
    ttk.Label(all_bar, text="検索").pack(side="left")
    search_var = tk.StringVar()
    search_entry = ttk.Entry(all_bar, textvariable=search_var, width=28)
    search_entry.pack(side="left", padx=(6, 6))
    ttk.Button(all_bar, text="クリア", width=8,
               command=lambda: search_var.set("")).pack(side="left")
    count_label = ttk.Label(all_bar, style="Faint.TLabel", text="")
    count_label.pack(side="right")
    tip(search_entry, "タブ名・置換前・置換後・メモで絞る。行をダブルクリックするとそのタブへ移る")
    all_box, all_tree = make_tree(
        all_frame, (("tab", "タブ", 130, "w"),) + COLUMNS, stretch_from=4)
    all_box.pack(fill="both", expand=True)
    state["all"] = {"tree": all_tree, "search": search_var, "rows": {}, "count": count_label}
    search_var.trace_add("write", lambda *_a: refresh_all())

    def jump(_event=None):
        found = state["all"]["rows"].get(all_tree.focus())
        if not found:
            return
        tab, rule = found
        notebook.select(model.doc.tabs.index(tab) + 1)
        page = current_page()
        if page is not None:
            select_rule(page, rule)
            page["tree"].focus_set()

    all_tree.bind("<Double-1>", jump)

    def opening_tab():
        """開いたときに見せるタブ。**中身のある最初のタブ**。

        ファイルの先頭のタブが空のことがある（同梱のルールの `#tab:標準` がそう）。
        そこを開くと、読めているのに「何も入っていない画面」に見える。
        """
        for index, tab in enumerate(model.doc.tabs):
            if tab.rules:
                return index + 1
        return 1

    def on_tab_changed(_event=None):
        """「すべて」タブへ移ったら横断表示を作り直し、編集欄は空にする。"""
        page = current_page()
        if page is None:
            refresh_all()
            load_editor(None)
        else:
            load_editor(page["rules"].get(page["tree"].focus()))

    notebook.bind("<<NotebookTabChanged>>", on_tab_changed)

    # -- 設定の小窓（置き場と記録） ----------------------------------------
    def open_settings():
        dialog = tk.Toplevel(root)
        dialog.title("設定")
        dialog.transient(root)
        dialog.resizable(True, False)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="ルールファイルの置き場", style="Group.TLabel").pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(4, 0))
        grid.columnconfigure(1, weight=1)
        kind = model.kind()
        ttk.Label(grid, text="読んでいる").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="{}（{}）".format(model.source_path, KIND_LABEL.get(kind, kind)),
                  style="Warn.TLabel" if kind in ("legacy", "default", "missing") else "TLabel").grid(
                      row=0, column=1, columnspan=2, sticky="w", padx=(8, 0))
        ttk.Label(grid, text="保存先").grid(row=1, column=0, sticky="w")
        target_label = ttk.Label(grid, text=model.target_path or "（決められない）")
        target_label.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8, 0))
        ttk.Label(grid, text="指定").grid(row=2, column=0, sticky="w", pady=(6, 0))
        custom_var = tk.StringVar(value=str(model.settings.get("RULES_PATH") or ""))
        ttk.Entry(grid, textvariable=custom_var, width=60).grid(
            row=2, column=1, sticky="ew", padx=(8, 6), pady=(6, 0))

        def browse():
            start = os.path.dirname(model.source_path or "") or MOD_DIR
            chosen = filedialog.askopenfilename(
                parent=dialog, title="ルールファイルを選ぶ", initialdir=start,
                filetypes=[("ルールファイル", "*.txt"), ("すべてのファイル", "*.*")])
            if chosen:
                chosen = os.path.normpath(chosen)
                # 既定の置き場そのものを選んだら指定を空に戻す（同じ場所を2通りで持たない）。
                custom_var.set("" if chosen == os.path.normpath(model.state_path() or "")
                               else chosen)

        ttk.Button(grid, text="参照…", width=8, command=browse).grid(
            row=2, column=2, pady=(6, 0))
        ttk.Label(frame, style="Faint.TLabel", wraplength=880, justify="left",
                  text="空なら state\\{}\\{} を読み書きする。"
                       "別のファイルかフォルダを書くとそちらを先に読み、保存先もそこになる"
                       "（相対は state のこの MOD のフォルダから。"
                       "無ければ順に、state → MOD のフォルダの旧いルール → 同梱の既定）。".format(
                           STATE_DIRNAME, RULES_FILE_NAME)).pack(anchor="w", pady=(4, 0))

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="記録（out\\prompt_bloat.log）", style="Group.TLabel").pack(anchor="w")
        log_replace = tk.BooleanVar(value=bool(model.settings.get("LOG_REPLACE", True)))
        log_rules = tk.BooleanVar(value=bool(model.settings.get("LOG_RULES", True)))
        ttk.Checkbutton(frame, text="置換したことを記録する（[REPLACE] / [SKIP]）",
                        variable=log_replace).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(frame, text="ルールの読込を記録する（[RULES]。切っても書式の誤りは残る）",
                        variable=log_rules).pack(anchor="w")

        def preview_target(*_args):
            model.settings["RULES_PATH"] = custom_var.get().strip()
            model.repath()
            target_label.configure(text=model.target_path or "（決められない）")

        custom_var.trace_add("write", preview_target)
        before = dict(model.settings)
        was_source = model.source_path

        def ok(_event=None):
            model.settings["RULES_PATH"] = custom_var.get().strip()
            model.settings["LOG_REPLACE"] = bool(log_replace.get())
            model.settings["LOG_RULES"] = bool(log_rules.get())
            model.repath()
            dialog.destroy()
            update_file_line()
            update_title()
            if model.source_path != was_source:
                # 指定で読む先が変わった。編みかけが無ければその場で読み直す。
                if model.doc.render() == model.saved_text:
                    reload(confirm=False, keep_settings=True)
                else:
                    status.configure(style="Warn.TLabel",
                                     text="読む先が変わった。「再読込」で読み直す（未保存の変更は消える）")

        def cancel(_event=None):
            model.settings.clear()
            model.settings.update(before)
            model.repath()
            dialog.destroy()
            update_file_line()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="キャンセル", command=cancel).pack(side="right")
        ttk.Button(buttons, text="OK", style="Accent.TButton", command=ok).pack(
            side="right", padx=(0, 6))
        dialog.bind("<Return>", ok)
        dialog.bind("<Escape>", cancel)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.grab_set()
        root.wait_window(dialog)

    # -- 下段 --------------------------------------------------------------
    status = ttk.Label(footer, style="Faint.TLabel", text="")
    status.pack(side="left", fill="x", expand=True)

    def show_warnings(prefix=""):
        warnings = model.warnings()
        if warnings:
            status.configure(text="{}書式の誤り {} 件: {}".format(
                prefix, len(warnings), warnings[0]), style="Warn.TLabel")
        return warnings

    def summary():
        total, active = model.doc.count()
        return "{} 件（効いているのは {} 件）".format(total, active)

    def save():
        tab, rule, why = validate(model.doc)
        if why:
            if tab is not None and tab in model.doc.tabs:
                notebook.select(model.doc.tabs.index(tab) + 1)
                page = current_page()
                if page is not None and rule is not None:
                    select_rule(page, rule)
            messagebox.showwarning("入力の不備", why, parent=root)
            return
        moving = model.moving()
        was = model.source_path
        try:
            model.save()
        except Exception as exc:
            messagebox.showerror(
                "保存に失敗しました",
                "{} に書けませんでした。\n\n{}: {}".format(
                    model.target_path, type(exc).__name__, exc), parent=root)
            return
        update_file_line()
        update_title()
        status.configure(style="Faint.TLabel", text="保存しました {}  {}  {}".format(
            time.strftime("%H:%M:%S"), model.source_path, summary()))
        if moving:
            messagebox.showinfo(
                "保存しました",
                "{}\n\nに保存しました。これから読まれるのはこのファイルです。\n\n"
                "元のファイル（{}）は消していませんが、もう読まれません。".format(
                    model.source_path, was), parent=root)
        show_warnings("保存しました。")

    def reload(confirm=True, keep_settings=False):
        if confirm and model.dirty() and not messagebox.askyesno(
                "再読込", "未保存の変更があります。破棄して読み直しますか？", parent=root):
            return
        kept = dict(model.settings)
        model.reload()
        if keep_settings:
            # 設定の小窓で変えた直後の読み直し。まだ保存していない指定を保つ。
            model.settings.update(kept)
            model.repath()
            text, model.bom, model.newline = read_text(model.source_path)
            model.doc = Document.parse(text)
            model.saved_text = model.doc.render()
        rebuild(select=opening_tab())
        status.configure(style="Faint.TLabel", text="読み直しました  {}  {}".format(
            model.source_path, summary()))
        show_warnings()

    def close():
        modtool.save_window(model.root, MOD_DIR, root)
        if model.dirty():
            answer = messagebox.askyesnocancel(
                "未保存の変更", "変更を保存してから閉じますか？", parent=root)
            if answer is None:
                return
            if answer:
                save()
                if model.dirty():
                    return                 # 保存に失敗した。閉じない
        root.destroy()

    ttk.Button(footer, text="閉じる", command=close).pack(side="right")
    ttk.Button(footer, text="保存", style="Accent.TButton",
               command=save).pack(side="right", padx=(0, 6))
    ttk.Button(footer, text="再読込", command=lambda: reload(True)).pack(side="right", padx=(0, 6))
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Control-s>", lambda _e: save())

    rebuild(select=opening_tab())
    status.configure(text=summary() + "。保存すると次の応答から効く")
    show_warnings()
    return root


# ----------------------------------------------------------------- 窓を開かずに見る
def dump(model):
    print("rules   : {} ({})".format(model.source_path, KIND_LABEL.get(
        model.kind(), model.kind())))
    print("save to : {}".format(model.target_path or "（決められない）"))
    for index, path in enumerate(model.candidates):
        print("  {} {} {}".format("*" if path == model.source_path else " ",
                                  index + 1, path))
    print("settings: {}".format(", ".join(
        "{}={}".format(key, modtool.shown(value))
        for key, value in sorted(model.settings.items())) or "（宣言なし）"))
    print("engine  : {}".format("本体を読めた" if model.engine else "本体を読めない"))
    total, active = model.doc.count()
    print("tabs    : {} 枚 / rules: {} 件（効いているのは {} 件）".format(
        len(model.doc.tabs), total, active))
    print()
    for tab in model.doc.tabs:
        print("[{}] {}{}".format("ON " if tab.enabled else "OFF", tab.name,
                                 ("  memo: " + cell(" ".join(tab.memo), 60)) if tab.memo else ""))
        for rule in tab.rules:
            print("  {} {} {:>3}%  {}  ->  {}".format(
                "✓" if rule.enabled else "×", "re" if rule.is_regex else "  ",
                rule.prob, cell(rule.from_text, 46), cell(rule.to_text, 46)))
    warnings = model.warnings()
    if warnings:
        print()
        for line in warnings:
            print("[RULES] {}".format(line))


def main(argv):
    model = Model()
    if "--dump" in argv:
        # ルールにも世界の文言にも日本語と記号（✓）が入るので、
        # cp932 のコンソールで止まらないようにしておく（`tools/rebalance_saved_bgm.py` と同じ）。
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass
        dump(model)
        return 0
    build_window(model).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
