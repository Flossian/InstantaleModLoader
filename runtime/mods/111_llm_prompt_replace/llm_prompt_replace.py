# -*- coding: utf-8 -*-
"""LLM へ送る文章を、手で書いた置換ルールで書き換える。

外部プロキシ（InstantaleLLMProxy）の置換機能をプロセス内で同じ書式のまま動かす。
プロキシ用に書いた `llm_replacements.txt` をこの
MOD のフォルダへ置けばそのまま動く。

    置換前=>置換後            そのまま置き換える
    置換前=>置換後=>60        60% の確率で置き換える（0-100・省略時は 100）
    regex:正規表現=>$1…       置換前を正規表現として解釈する
    #tab:名前 / #offtab:名前  タブの区切り。`#offtab:` の中は全部無視
    #off:… や行頭 #           コメント（`#memo:…` も読み飛ばす）

同じ「置換前」の行はグループとして1回だけ抽選する（プロキシと同じ。
確率の合計が 100 を超えるなら `値/合計` の割合で必ずどれかに置き換わる）。

## プロキシとの違い（同じルールでも扱いを変えている3点）

プロキシは JSON エンコード後の HTTP ボディを見ていたが、
ここで見えるのは復号済みの Python 文字列。
そのままでは当たらないので:

1. エスケープは復号してから使う。
   置換後は必ず復号し、
   置換前は素の形と復号形の両方を登録する（`\\n` 入りの既存ルールを生かすため）。
   本物の `\\` は `\\\\`
2. 置換後の `$1` / `${name}` / `$&` / `$$`（.NET）は Python の後方参照へ読み替える。
   存在しない番号は警告して文字列扱い。
   パターン側は読み替えない（改行は素直に `\\n`）
3. **.NET の 1 秒タイムアウトの代わり**に、
   照合へ 1 秒以上かかった正規表現ルールを以後捨てる（`SLOW_REGEX_SECONDS`。
   1回目の暴走だけは止められない）

## どこに仕掛けるか

`instantale_modloader.llm.wrap_outgoing` に渡すだけ。
次の4つはすべてあちらの担当。

* ローカル（llama.cpp）の 3点
* クラウド（APIキー）の `llm_manager` 別名包み
* 別名の後生えの見張り
* 入れ子で通る地点を素通しする印

（v5 まではこのファイルが持っていた。`119_` にも同じものが要ると分かった時点でローダへ移した。TECH.md
§3.2.3「写して回るものが出たら、それはローダの語彙」）。
経路の実測と経緯は GAME.md §2.12 / VERIFICATION_LOG.md §2.24。

こちらに残るのは**この MOD 固有の歯止め**1つだけ:

  * 1回の推論で抽選は1回だけ。複数地点で抽選すると確率の分母が壊れる。
    入れ子の地点はローダの印で素通しになるが、印が届かない別スレッド経路
    （`chat` が返った後に別のスレッドが送る場合）は止められないので、
    「自分が作った文章」のハッシュで二度目を止める（`306_` と同じ手口）。
    `119_` は書き換えが冪等なので、あちらにこの受け皿は要らない

クラウド境界で見えるのは呼び出し側が渡した `message` だけで、
send_request の中で足される部分（Gemini のスキーマ文など）には当たらない（GAME.md
§1.8）。

## 適用順（`mod.json` の `after` / `before`）

宣言するのは**同じ地点を包む MOD だけ**。
`102_`（重複除去）と `105_`（スキーマ圧縮）より後＝外側に置き、
置換は圧縮前の本文を見る（ルールが素のスキーマ repr を狙えるようにするため。
同梱の既定は狙っていないが、手元のルールは狙える）。
完全一致を前提に書き換える
MOD は逆にこちらより外側へ置いて素の本文を見せる（その宣言はあちら側が持つ）。

`103_`（イベントログの切り詰め）とは順序の関係が無い。
あちらは `llm_manager:quest_referee_event_*`、こちらは送信の入口で、
呼ぶ側と呼ばれる側の関係だから、どちらを先に当てても見えるものは変わらない。
置換ルールが `103_` の目印を書き換えるとあちらが止まるのは、ルールを書く側の責任。

## ルールファイル

手元のルールは `state\\` に置く（遊びの続きと同じ寿命。TECH.md §3.11）。
読む先は上から順に、**在るものが1つ**:

    <設定 RULES_PATH>                            指定が在ればこれが最優先
    state\\llm_prompt_replace\\llm_replacements.txt   手元のルール（保存先もここ）
    <MOD>\\llm_replacements.txt                   旧い置き場。読むだけ（移行のため残す）
    <MOD>\\llm_replacements.default.txt           同梱の既定。MOD 更新で上書きされるのはこれだけ

`state\\` へ移したのは、MOD のフォルダが**更新のたびに上書きされる置き場**だから。
以前は「MOD 単体の部品は MOD のフォルダで完結させる」を理由に MOD の中だけを読んでいたが、
手で書いたルールは消耗品ではなく遊びの続き（§3.11 の `state\\` の定義そのもの）で、
`state\\` なら MOD を入れ替えても、フォルダごと消しても巻き添えにならない。
旧い置き場を読む候補に残してあるので、前の版で書いたルールは黙って無効にならない
（設定画面で保存すると `state\\` へ移る）。

`RULES_PATH` は別名・別フォルダを使いたいときの逃げ道（外部プロキシと同じファイルを共有する、
ルールの束を切り替える）。
相対パスは `state\\llm_prompt_replace\\` から辿り、フォルダを指したらその中の
`llm_replacements.txt` を読む。

変更はリクエストのたびに反映（更新時刻と大きさで読み直す。
読めない間は前回のルールで続け、無ければ何もしない）。
`out\\prompt_bloat.log` に
`[RULES]`（読込）・`[REPLACE]`（置換）・`[SKIP]`（確率で見送り）が出る。
`102_` / `103_` / `105_` と同じファイルなので、
置換と圧縮のどちらが先に効いたかを時系列で読める。

## 同じ文面の行は1プロセスで1回（版8）

版7までは LLM を呼ぶたびに、当たった規則ごとに `[REPLACE]` を1行書いていた。
同じ規則が毎回同じ文面で当たるので、約40日で `[REPLACE]` が12,806行・約2.5MB になり、
中身は91通りしか無かった（上位3通りで4,701行）。
`[RULES] 読込` も apply のたびに出ていた（遅れて当て直す boot を含めて1,120回・約200KB）。

版8から:

    [REPLACE] / [SKIP]  同じ文面はこのプロセスで最初の1回だけ書き、以後は規則ごとに数える
    [TALLY]             数えた回数を1行にまとめる。規則ごとに「この起動の累計(+前の [TALLY] から)」。
                        書くのは、数えるだけにした推論が TALLY_CALLS 回たまったとき・
                        最初に数えてから TALLY_SECONDS 秒たった後の推論・apply の頭
                        （遅れて当て直す boot と注入し直し）
    [RULES] 読込        同じファイル（場所・更新時刻・大きさ）を読み直しただけなら書かない

控えは `sys` に置く（`LOG_STORE_ATTR`）。
MOD のモジュールは boot のたびに読み直されるので、モジュール変数では boot ごとに消える。
注入のときにログが世代送りされたら（`prompt_bloat.log` が入れ替わったら）、書いた文面の控えを捨て、
新しいログにもう一度書く。数えた回数（累計）はプロセスで持ち続ける。

最後の `[TALLY]` の後に数えた分は、閉じ方ごとに次のように書く（回数を落とさないため）:

    閉じたとき          Kivy の `on_stop` と `atexit` で `[TALLY]` を書く（先に走った方だけ）
    落ちた・止められた  まだ書いていない回数を推論のたびに `out/prompt_replace_tally.json` へ
                        上書きしておき、次の起動の最初の apply で `[TALLY]` として書き出して消す
"""

import atexit
import collections
import hashlib
import json
import os
import random
import re
import sys
import threading
import time

from instantale_modloader import ui
from instantale_modloader.llm import wrap_outgoing

# --------------------------------------------------------------------------
# 設定（既定値。`mod.json` の "settings" が同じ値を宣言している）
# --------------------------------------------------------------------------
RULES_PATH = ""              # ルールファイルの置き場（空なら state\ の既定の場所）
LOG_REPLACE = True           # [REPLACE] / [SKIP] を残すか
LOG_RULES = True             # [RULES]（読込）を残すか

# 手元のルールは `state\` に置き、同梱の既定だけが MOD のフォルダに在る（`rules_path`）。
RULES_FILE_NAME = "llm_replacements.txt"                  # 手元のルール（保存先）
DEFAULT_RULES_FILE_NAME = "llm_replacements.default.txt"  # 同梱の既定

#: `state\` の下のフォルダ名。番号を落とした形（TECH.md §3.11）。
#: 番号を振り直しても手元のルールが行方不明にならないよう、`111_` は付けない。
STATE_DIRNAME = "llm_prompt_replace"

# 書式（プロキシと同じ）
SEPARATOR = "=>"
REGEX_PREFIX = "regex:"
TAB_PREFIX = "#tab:"
OFFTAB_PREFIX = "#offtab:"

SNIP_CHARS = 40              # ログに出す断片の長さ（プロキシの Snip と同じ）
SLOW_REGEX_SECONDS = 1.0     # 照合にこれ以上かかった正規表現は以後使わない
SEEN_TEXTS = 64              # 「自分が作った文章」を覚えておく件数

# 同じ文面の [REPLACE] / [SKIP] を数えるだけにした後、[TALLY] を書く間隔（版8）。
TALLY_CALLS = 50             # 数えるだけにした推論の数
TALLY_SECONDS = 600          # 最初に数えてからの秒数（次の推論で書く）
TALLY_SNIP = 16              # [TALLY] に出す断片の長さ。[REPLACE] の行の頭と突き合わせる
# まだ書いていない回数の控え（`out\` の中）。落ちた・止められた起動の分を次の起動で書く。
PENDING_NAME = "prompt_replace_tally.json"

#: 書いた行の文面と数えた回数の控え。1プロセスに1組（`log_store`）。
LOG_STORE_ATTR = "_instantale_llm_prompt_replace_log"

_RNG = random.Random()
_RNG_LOCK = threading.Lock()


def _roll(denom):
    """0 以上 denom 未満の整数。抽選はここだけで行う（検証では差し替える）。"""
    with _RNG_LOCK:
        return _RNG.randrange(denom)


# --------------------------------------------------------------------------
# エスケープの復号
# --------------------------------------------------------------------------
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                   "\\": "\\", '"': '"', "'": "'", "/": "/", "0": "\0"}


def unescape(text):
    """`\\n` や `\\u3042` を実際の文字に直す。

    知らないエスケープ（`\\d` など）はそのまま残す。
    ルールに正規表現めいた書き方が混じっていても壊さないため。
    `\\uD83D\\uDE00` のような
    UTF-16 の代理対は1文字に戻す（`ensure_ascii=True` はコード単位ごとに書くため）。
    """
    if "\\" not in text:
        return text

    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "\\" or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        following = text[index + 1]
        simple = _SIMPLE_ESCAPES.get(following)
        if simple is not None:
            out.append(simple)
            index += 2
            continue
        if following == "u" and index + 6 <= length:
            try:
                out.append(chr(int(text[index + 2:index + 6], 16)))
            except ValueError:
                out.append(char)
                index += 1
                continue
            index += 6
            continue
        out.append(char)
        out.append(following)
        index += 2

    joined = "".join(out)
    try:
        return joined.encode("utf-16", "surrogatepass").decode("utf-16")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return joined              # 片方だけの代理符号。復号せずに渡す


# --------------------------------------------------------------------------
# 置換後（正規表現ルール）の読み替え
# --------------------------------------------------------------------------
def replacement_parts(text):
    """.NET の置換文字列を `[("lit", 文字列) | ("group", 番号 or 名前)]` にする。

    `$1` `${name}` `$&`（一致全体）`$$`（`$` 自身）を読む。
    `$` の後がそれ以外なら文字として扱う（.NET も同じ）。
    文字の部分は `unescape` を通すので、`\\n` は改行として入る。

    Python の `re.sub` のテンプレート文字列にせず自前で組み立てるのは、
    置換後に `\\1` や `\\g` を含む文章が来たときの取り違えを起こさないため。
    """
    parts = []
    buffer = []

    def flush():
        if buffer:
            parts.append(("lit", unescape("".join(buffer))))
            del buffer[:]

    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "$" or index + 1 >= length:
            buffer.append(char)
            index += 1
            continue
        following = text[index + 1]
        if following == "$":
            buffer.append("$")
            index += 2
            continue
        if following == "&":
            flush()
            parts.append(("group", 0))
            index += 2
            continue
        if following == "{":
            end = text.find("}", index + 2)
            if end > 0:
                name = text[index + 2:end]
                flush()
                parts.append(("group", int(name) if name.isdigit() else name))
                index = end + 1
                continue
            buffer.append(char)
            index += 1
            continue
        if following.isdigit():
            end = index + 1
            while end < length and text[end].isdigit():
                end += 1
            flush()
            parts.append(("group", int(text[index + 1:end])))
            index = end
            continue
        buffer.append(char)
        index += 1
    flush()
    return parts


def _validate_parts(parts, rx):
    """存在しない後方参照を文字列に戻す。`(直した並び, 警告)` を返す。"""
    fixed = []
    warnings = []
    for kind, value in parts:
        if kind == "group":
            known = (value in rx.groupindex if isinstance(value, str)
                     else 0 <= value <= rx.groups)
            if not known:
                warnings.append("後方参照 ${} は存在しないので文字として扱う: {}".format(
                    value, rx.pattern))
                fixed.append(("lit", "${}".format(value)))
                continue
        fixed.append((kind, value))
    return fixed, warnings


# --------------------------------------------------------------------------
# ルール
# --------------------------------------------------------------------------
class Rule(object):
    """1行から作られる置換1件。

    `disp_from` / `disp_to` はログに出す姿で、
    復号する前の書き方をそのまま見せる（ルールファイルを目で照合できるようにするため）。
    """

    __slots__ = ("from_text", "to_text", "prob", "is_regex", "rx", "parts",
                 "disp_from", "disp_to", "dropped")

    def __init__(self, from_text, to_text, prob, is_regex=False, rx=None,
                 parts=None, disp_from=None, disp_to=None):
        self.from_text = from_text
        self.to_text = to_text
        self.prob = prob
        self.is_regex = is_regex
        self.rx = rx
        self.parts = parts
        self.disp_from = disp_from if disp_from is not None else from_text
        self.disp_to = disp_to if disp_to is not None else to_text
        self.dropped = None          # 捨てた理由（暴走した正規表現）

    @property
    def key(self):
        """抽選をまとめる単位。正規表現とリテラルは同じ文字列でも別扱い。"""
        return ("R" if self.is_regex else "L", self.from_text)

    def matches(self, text):
        if self.is_regex:
            return self.rx.search(text) is not None
        return self.from_text in text

    def replace(self, text):
        """`(置換後, 件数)`。当たらなければ元の文字列をそのまま返す。"""
        if not self.is_regex:
            count = text.count(self.from_text)
            if not count:
                return text, 0
            return text.replace(self.from_text, self.to_text), count

        parts = self.parts

        def expand(match):
            out = []
            for kind, value in parts:
                if kind == "lit":
                    out.append(value)
                else:
                    got = match.group(value)
                    out.append(got if got is not None else "")
            return "".join(out)

        return self.rx.subn(expand, text)


def parse_rules(lines):
    """ルールファイルの行を読む。`(ルール, 警告)` を返す。

    タブ行が1つも無い旧い形式のファイルは全行が有効（プロキシと同じ）。
    """
    rules = []
    warnings = []
    tab_enabled = True

    for raw in lines:
        line = raw.strip().lstrip("﻿").strip()      # 先頭行の BOM を落とす
        if not line:
            continue
        if line.startswith(TAB_PREFIX):
            tab_enabled = True
            continue
        if line.startswith(OFFTAB_PREFIX):
            tab_enabled = False
            continue
        if line.startswith("#"):
            continue                        # コメントと `#off:` で切られた行
        if not tab_enabled:
            continue

        is_regex = line.startswith(REGEX_PREFIX)
        if is_regex:
            line = line[len(REGEX_PREFIX):]

        index = line.find(SEPARATOR)
        if index <= 0:
            warnings.append("不正なルール行を無視: " + line)
            continue
        raw_from = line[:index]
        rest = line[index + len(SEPARATOR):]

        # 「置換前=>置換後=>確率」。
        # 確率として読めない末尾は置換後の一部とみなす。
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
        raw_to = rest

        if is_regex:
            try:
                rx = re.compile(raw_from)
            except re.error as exc:
                warnings.append("不正な正規表現ルールを無視: {} ({})".format(raw_from, exc))
                continue
            parts, notes = _validate_parts(replacement_parts(raw_to), rx)
            warnings += notes
            rules.append(Rule(raw_from, raw_to, prob, is_regex=True, rx=rx, parts=parts,
                              disp_from=raw_from + "〔正規表現〕", disp_to=raw_to))
            continue

        # 置換後は必ず復号する（本文は復号済みなので `\n` は改行として入れたい）。
        to_text = unescape(raw_to)
        rules.append(Rule(raw_from, to_text, prob, disp_from=raw_from, disp_to=raw_to))
        decoded = unescape(raw_from)
        if decoded != raw_from:
            # ボディの中の形（`\n` や `\uXXXX`）で書かれたルール。
            # 本文は復号済みなので復号した形でも登録する。
            # 当たるのはどちらか一方だけ。
            rules.append(Rule(decoded, to_text, prob,
                              disp_from=raw_from + "〔復号形式〕", disp_to=raw_to))
    return rules, warnings


def group_rules(rules):
    """同じ「置換前」のルールをまとめる。出現順は保つ（プロキシと同じ）。"""
    groups = []
    index = {}
    for rule in rules:
        group = index.get(rule.key)
        if group is None:
            group = []
            index[rule.key] = group
            groups.append(group)
        group.append(rule)
    return groups


def decide(groups, text, roll=None):
    """当てるルールをグループごとに1回の抽選で決める。

    戻り値は `(発動する [(ルール, 分母)], 見送った [(ルール, 合計, 分母, 理由)])`。
    抽選はグループごとに1回で、そのグループのどのルールも当たらないことがある（確率の合計が
    100 未満のとき）。
    """
    roll = roll or _roll
    chosen = []
    skipped = []

    for group in groups:
        head = group[0]
        if head.dropped:
            continue
        started = time.monotonic()
        try:
            hit = head.matches(text)
        except Exception as exc:
            head.dropped = "照合に失敗した（{}）".format(exc)
            skipped.append((head, 0, 0, head.dropped))
            continue
        if head.is_regex:
            spent = time.monotonic() - started
            if spent >= SLOW_REGEX_SECONDS:
                # 止められないので、次からは使わない。
                # 1回目の遅れだけは受け入れる。
                head.dropped = "照合に {:.1f} 秒かかったので以後使わない".format(spent)
                skipped.append((head, 0, 0, head.dropped))
                continue
        if not hit:
            continue

        total = sum(rule.prob for rule in group)
        if total <= 0:
            skipped.append((head, total, 100, "確率0のため置換せず"))
            continue
        denom = max(total, 100)
        value = roll(denom)
        picked = None
        accumulated = 0
        for rule in group:
            accumulated += rule.prob
            if value < accumulated:
                picked = rule
                break
        if picked is None:
            skipped.append((head, total, denom, "確率判定により置換せず"))
            continue
        chosen.append((picked, denom))
    return chosen, skipped


def apply_chosen(text, chosen):
    """決まったルールを順に当てる。`(置換後, [(ルール, 件数, 分母)])`。"""
    hits = []
    for rule, denom in chosen:
        try:
            new_text, count = rule.replace(text)
        except Exception as exc:
            rule.dropped = "置換に失敗した（{}）".format(exc)
            continue
        if count:
            hits.append((rule, count, denom))
            text = new_text
    return text, hits


def snip(text, limit=None):
    """ログ用に短く切る（既定はプロキシの Snip と同じ長さ）。改行は見える形にする。"""
    limit = SNIP_CHARS if limit is None else limit
    text = text.replace("\r", "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------
# 同じ文面の行を繰り返さない（版8）
# --------------------------------------------------------------------------
def log_store():
    """書いた行の文面と数えた回数の控え。1プロセスに1組。

    遅れて当て直す boot と注入し直しをまたいで持つ（`sys` に置く）。
    鍵が欠けているか型が違えば、その鍵だけ作り直す（版の違う器を握らない）。
    """
    store = getattr(sys, LOG_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {}
        setattr(sys, LOG_STORE_ATTR, store)
    for name, kind in (("written", set), ("total", dict), ("pending", dict)):
        if not isinstance(store.get(name), kind):
            store[name] = kind()
    for name in ("calls", "since"):
        if not isinstance(store.get(name), (int, float)):
            store[name] = 0
    # `rules` は最後に `[RULES] 読込` を書いたルールファイル、`file` はログのファイルの身元。
    store.setdefault("rules", None)
    store.setdefault("file", None)
    if not hasattr(store.get("lock"), "acquire"):
        store["lock"] = threading.Lock()
    return store


def file_id(path):
    """ログのファイルの身元。無ければ None（世代送りで名前を変えられた直後など）。"""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def log_replaced(store, path):
    """前の apply の後で `path` が入れ替わった（世代送り・削除）なら True。控えは今の身元へ進める。

    世代送りは注入のとき（apply より前）にしか起きないので、apply の頭で見れば足りる。
    """
    now = file_id(path)
    before = store.get("file")
    store["file"] = now
    return before is not None and before != now


def note_line(store, line, key, now=None):
    """`[REPLACE]` / `[SKIP]` の行を書くなら True。

    このプロセスで書いた文面なら書かずに、規則（`key`）ごとに数えて False。
    """
    now = time.time() if now is None else now
    with store["lock"]:
        store["total"][key] = store["total"].get(key, 0) + 1
        if line not in store["written"]:
            store["written"].add(line)
            return True
        pending = store["pending"]
        if not pending:
            store["since"] = now
        pending[key] = pending.get(key, 0) + 1
        return False


def take_tally(store, quiet_call=False, force=False, now=None):
    """数えた回数を `[TALLY]` の1行にして返す。まだ書かないなら None。

    `quiet_call` は今の推論で数えるだけにした行があったか。
    書くのは、そういう推論が `TALLY_CALLS` 回たまったとき、
    最初に数えてから `TALLY_SECONDS` 秒たったとき、`force`（apply の頭）のとき。
    規則ごとに「この起動の累計(+前の [TALLY] から)」。
    """
    now = time.time() if now is None else now
    with store["lock"]:
        if quiet_call:
            store["calls"] += 1
        pending = store["pending"]
        if not pending:
            return None
        if (not force and store["calls"] < TALLY_CALLS
                and now - store["since"] < TALLY_SECONDS):
            return None
        total = store["total"]
        parts = []
        for key, count in sorted(pending.items(), key=lambda kv: (-kv[1], kv[0])):
            kind, source, target = key
            label = ("\"{}\"->\"{}\"".format(snip(source, TALLY_SNIP), snip(target, TALLY_SNIP))
                     if kind == "REPLACE"
                     else "\"{}\" 見送り".format(snip(source, TALLY_SNIP)))
            parts.append("{} {}(+{})".format(label, total.get(key, count), count))
        quiet = sum(pending.values())
        store["pending"] = {}
        store["calls"] = 0
        store["since"] = now
    return ("[TALLY] 同じ文面の行を書かずに数えた {}回。規則ごとに この起動の累計(+前の [TALLY] から): {}"
            .format(quiet, " / ".join(parts)))


def save_pending(store):
    """まだ書いていない回数を控えのファイルへ上書きする。無ければファイルを消す。

    落ちたときや止められたときは `on_stop` も `atexit` も走らないので、推論のたびに残しておく。
    推論は1回に数秒かかるので、小さな JSON の上書き1回は重さにならない（測ってはいない）。
    """
    path = store.get("pending_path")
    if not path:
        return
    with store["lock"]:
        pending = store["pending"]
        if not pending:
            if store.get("pending_saved"):
                store["pending_saved"] = False
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        rows = [[kind, source, target, count, store["total"].get((kind, source, target), count)]
                for (kind, source, target), count in pending.items()]
        data = {"pid": os.getpid(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "rows": rows}
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, path)
            store["pending_saved"] = True
        except OSError:
            pass


def take_leftover(path):
    """前の起動が書けずに残した回数を `[TALLY]` の1行にして返し、控えを消す。無ければ None。

    今のプロセスが残した控え（遅れて当て直す boot）は、手元の回数と同じなので読まない。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("pid") == os.getpid():
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    parts = []
    quiet = 0
    for row in data.get("rows") or []:
        try:
            kind, source, target, count, total = row
        except (TypeError, ValueError):
            continue
        quiet += int(count)
        label = ("\"{}\"->\"{}\"".format(snip(source, TALLY_SNIP), snip(target, TALLY_SNIP))
                 if kind == "REPLACE" else "\"{}\" 見送り".format(snip(source, TALLY_SNIP)))
        parts.append("{} {}(+{})".format(label, total, count))
    if not parts:
        return None
    return ("[TALLY] 前の起動（pid {}・最後に数えたのは {}）で書けずに残った {}回。"
            "規則ごとに その起動の累計(+前の [TALLY] から): {}".format(
                data.get("pid"), data.get("at"), quiet, " / ".join(parts)))


def flush_at_exit(*_args):
    """閉じるときに、まだ書いていない回数を `[TALLY]` に書く（`on_stop` と `atexit` から）。

    どちらが先に走っても、2回目は書くものが無いので何もしない。
    モジュール関数にして `sys` の控えだけを見るので、boot が変わっても最新の書き手で書く。
    """
    store = getattr(sys, LOG_STORE_ATTR, None)
    if not isinstance(store, dict) or not hasattr(store.get("lock"), "acquire"):
        return
    write = store.get("write")
    try:
        tally = take_tally(store, force=True)
        if tally and write is not None:
            write(tally + "（閉じるとき）")
        save_pending(store)
    except Exception:
        pass


# --------------------------------------------------------------------------
# ルールファイル
# --------------------------------------------------------------------------
def resolve_path(custom, state_dir="", mod_dir=""):
    r"""設定（`RULES_PATH`）が指す1つのファイル。指定が無ければ空文字。

    絶対パスならそのまま。
    相対パスは `state\<この MOD>\` から辿る（手元のルールの置き場と同じ起点）。
    フォルダを指していたら、その中の `llm_replacements.txt`。

    `~` と `%VAR%` は展開する（設定欄に貼られる形として普通なので、
    そのまま繋いで「そんなフォルダは無い」にしない）。
    引用符付きで貼られることがあるので両端の `"` も落とす。
    """
    custom = (custom or "").strip().strip('"').strip()
    if not custom:
        return ""
    path = os.path.expanduser(os.path.expandvars(custom))
    if not os.path.isabs(path):
        base = os.path.join(state_dir, STATE_DIRNAME) if state_dir else (mod_dir or "")
        path = os.path.join(base, path)
    if os.path.isdir(path):
        path = os.path.join(path, RULES_FILE_NAME)
    return os.path.normpath(path)


def rules_candidates(mod_dir, state_dir="", custom=""):
    r"""読む先を優先順に並べる。**在るものの先頭**が使われる（`rules_path`）。

        <RULES_PATH>                      指定が在れば最優先
        state\<この MOD>\<手元のルール>     既定の置き場。設定画面の保存先
        <MOD>\<手元のルール>               旧い置き場。読むだけ（移行のため）
        <MOD>\<同梱の既定>                 配布物。MOD の更新で上書きされる

    `mod_dir` も `state_dir` も `apply()` の外では引けないので、呼ぶ側が控えておくこと。
    """
    paths = []
    picked = resolve_path(custom, state_dir, mod_dir)
    if picked:
        paths.append(picked)
    if state_dir:
        paths.append(os.path.join(state_dir, STATE_DIRNAME, RULES_FILE_NAME))
    if mod_dir:
        # 旧い置き場。前の版で書いたルールを黙って無効にしないために読む
        # （書く側はもう `state\` だけ。設定画面で保存すると向こうへ移る）。
        paths.append(os.path.join(mod_dir, RULES_FILE_NAME))
        paths.append(os.path.join(mod_dir, DEFAULT_RULES_FILE_NAME))
    return paths


def rules_path(mod_dir, state_dir="", custom=""):
    """いま読むルールファイル。

    候補（`rules_candidates`）の先頭から、**在るもの**を返す。
    どれも無ければ最後の候補（同梱の既定）の場所を返す（呼ぶ側が「無い」を扱う）。
    候補そのものが無い（`apply()` の外で場所を控え忘れた）なら `None`。
    """
    paths = rules_candidates(mod_dir, state_dir, custom)
    for path in paths:
        if os.path.isfile(path):
            return path
    return paths[-1] if paths else None


def rules_target(mod_dir="", state_dir="", custom=""):
    r"""書く先。指定が在ればそこ、無ければ `state\<この MOD>\<手元のルール>`。

    **同梱の既定（`*.default.txt`）は返さない。**
    あれは配布物で、書くと MOD の更新で消える。
    `state_dir` が引けなければ `None`（書く先が決められないことを呼ぶ側に伝える）。
    """
    picked = resolve_path(custom, state_dir, mod_dir)
    if picked:
        return picked
    if state_dir:
        return os.path.join(state_dir, STATE_DIRNAME, RULES_FILE_NAME)
    return None


class RuleFile(object):
    """ルールファイル1つ。**リクエストのたびに更新を見て読み直す**。

    ゲームを再起動しなくてもルールの変更が次のリクエストから効く（プロキシの
    `ReloadRulesIfChanged` と同じ）。
    読めなかったとき（保存の書き込み途中など）は前回のルールを使い続け、
    次のリクエストで再試行する。

    **ファイルが無いのは異常ではない**（ルールを書いていなければ置換しないだけ）。
    後から置いた場合も、次のリクエストで拾われる。
    手元のファイルを置いた／消したときは、
    読む先もその場で切り替わる（`rules_path`）。
    """

    def __init__(self, mod_dir, state_dir, report, fresh=None):
        self.mod_dir = mod_dir
        self.state_dir = state_dir
        self.report = report          # ログを出す関数（行, force）
        # 読んだファイル `(場所, 更新時刻と大きさ)` を渡し、読込の行を書くなら真を返す関数。
        # apply ごとにこの器を作り直すので、無いと同じファイルでも `[RULES] 読込` が
        # apply のたびに出ていた（版7まで）。None なら毎回書く。
        self.fresh = fresh
        self.path = rules_path(mod_dir, state_dir, RULES_PATH)
        self.stamp = None
        self.groups = []
        self.count = 0
        self.lock = threading.Lock()

    def current(self):
        """いま効いているグループの一覧。必要なら読み直す。"""
        with self.lock:
            try:
                self._reload_if_changed()
            except Exception:
                pass                  # 読めなければ前回のルールで続ける
            return self.groups

    def _reload_if_changed(self):
        if not self.mod_dir and not self.state_dir:
            return
        # 手元のファイルが置かれた／消えたら、読む先を入れ替える。
        # 候補は4つまでなので、リクエストごとに確かめても軽い。
        path = rules_path(self.mod_dir, self.state_dir, RULES_PATH)
        if path != self.path:
            self.report("[RULES] 読む先を切り替えた: {} -> {}".format(self.path, path),
                        force=True)
            self.path = path
            self.stamp = None
        if not os.path.isfile(self.path):
            # 消された。
            # 前のルールを握り続けない（消したのに効き続ける方が驚かれる）。
            if self.groups:
                self.groups = []
                self.count = 0
                self.report("[RULES] ルールファイルが無くなったので置換を止める",
                            force=True)
            self.stamp = None
            return

        try:
            info = os.stat(self.path)
            stamp = (info.st_mtime, info.st_size)
        except OSError:
            return
        if stamp == self.stamp:
            return

        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return                    # 書き込み途中。次のリクエストで読み直す

        # 印は読めた時点で進める。
        # 書式の解析で落ちた場合に、
        # 同じ内容をリクエストごとに読み直して同じ苦情を並べないため（次の保存で再挑戦する）。
        self.stamp = stamp
        try:
            rules, warnings = parse_rules(lines)
        except Exception as exc:
            self.report("[RULES] ルールを読めなかったので前回のまま続ける（{}）".format(exc),
                        force=True)
            return
        self.groups = group_rules(rules)
        self.count = len(rules)
        # 同じファイルを読み直しただけなら、警告も読込の行も前に書いたものと同じ。
        if self.fresh is not None and not self.fresh((self.path, stamp)):
            return
        for line in warnings:
            self.report("[RULES] " + line, force=True)
        self.report("[RULES] 読込: {} {}パターン / {}グループ".format(
            self.path, self.count, len(self.groups)))


# --------------------------------------------------------------------------
# 「1回の推論で1回だけ」の歯止め
# --------------------------------------------------------------------------
# 入れ子で通る地点を素通しする印はローダ側（`instantale_modloader.llm`）にある。
# こちらが持つのは、その印が届かない場合の受け皿だけ。
class Seen(object):
    """自分が作った文章を覚えておく輪。二度目に来たものは触らない。

    印（スレッド）が届かない経路（`chat` が返した後に別のスレッドが送る場合）でも、
    同じ文字列に確率の抽選をもう一度させないため。
    中身ではなくハッシュを持つ（プロンプトは数万文字になるので、そのまま抱えない）。
    """

    def __init__(self, size=SEEN_TEXTS):
        self.size = size
        self.order = collections.deque()
        self.marks = set()
        self.lock = threading.Lock()

    @staticmethod
    def _mark(text):
        return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()

    def add(self, text):
        mark = self._mark(text)
        with self.lock:
            if mark in self.marks:
                return
            self.marks.add(mark)
            self.order.append(mark)
            while len(self.order) > self.size:
                self.marks.discard(self.order.popleft())

    def __contains__(self, text):
        with self.lock:
            return self._mark(text) in self.marks


# --------------------------------------------------------------------------
# 注入
# --------------------------------------------------------------------------
def apply(ctx):
    log_path = ctx.out_path("prompt_bloat.log")
    # 手元のルールは `state\`、同梱の既定は MOD のフォルダ（`rules_candidates`）。
    # `ctx.mod_dir` も `ctx.state_dir` も apply() の外では引けないので、
    # ここで控えておく（ラッパは後からこの値を使う）。
    mod_dir = ctx.mod_dir
    state_dir = getattr(ctx, "state_dir", "") or ""
    seen = Seen()

    write = ctx.logger("prompt_bloat.log")
    store = log_store()
    if log_replaced(store, log_path):
        # 世代送りされた新しいログ。書いた文面の控えを捨て、読込の行も書き直す。
        with store["lock"]:
            store["written"].clear()
            store["rules"] = None

    def report(line, force=False):
        if force or LOG_RULES:
            write(line)

    def first_read(signature):
        """最後に読込の行を書いたファイルと違えば真（控えも進める）。"""
        if store.get("rules") == signature:
            return False
        store["rules"] = signature
        return True

    # 前の起動が落ちて書けなかった回数（控えのファイル）を先に書き出す。
    store["pending_path"] = ctx.out_path(PENDING_NAME)
    leftover = take_leftover(store["pending_path"])
    if leftover:
        write(leftover)

    # 前の boot（または前の注入）で数えたまま書いていない回数を、ここで書き出す。
    if LOG_REPLACE:
        tally = take_tally(store, force=True)
        if tally:
            write(tally)
        save_pending(store)

    # 閉じるときの書き出し。書き手は boot ごとに新しいものへ差し替え、登録は1プロセス1回。
    store["write"] = write
    if not store.get("exit_hooked"):
        store["exit_hooked"] = True
        atexit.register(flush_at_exit)

    def bind_on_stop():
        app = ui.find_app()
        if app is not None:
            app.bind(on_stop=flush_at_exit)

    # キーを世代で変えないので、boot をまたいで1回だけ走る（TECH.md §3.6）。
    ctx.on_ready(bind_on_stop, key="111_llm_prompt_replace:on_stop")

    rules = RuleFile(mod_dir, state_dir, report, fresh=first_read)

    def run(texts, site):
        """文章の並びにルールを当てる。変わらなければ None。

        ローダ（`instantale_modloader.llm`）から、
        1回の推論で出ていく本文の並びとして呼ばれる。
        引数の順はあちらの約束（`rewrite(texts, site)`）。
        """
        groups = rules.current()
        if not groups:
            return None
        fresh = [text for text in texts if text and text not in seen]
        if not fresh:
            return None                       # 全部が自分の出力。抽選もしない

        # 判定と抽選は**全部を繋いだ文章に対して1回**（プロキシがボディ全体を1回で見ていたのと同じ）。
        # 書き換えは1つずつに当てる。
        chosen, skipped = decide(groups, "\n".join(fresh))
        quiet = []                            # この推論で数えるだけにした行があったか

        def event(line, key):
            # 同じ文面はこのプロセスで1回だけ書き、以後は数える（版8）。
            if note_line(store, line, key):
                write(line)
            else:
                quiet.append(key)

        for rule, total, denom, why in skipped:
            if rule.dropped:
                write("[RULES] 正規表現を捨てた: \"{}\" {}".format(
                    snip(rule.disp_from), why))
            elif LOG_REPLACE:
                event("[SKIP] {} | \"{}\" {}{}".format(
                    site, snip(rule.disp_from), why,
                    "" if not total else " ({}/{})".format(total, denom)),
                    ("SKIP", rule.disp_from, why))

        result = []
        changed = False
        if chosen:
            for text in texts:
                if not text or text in seen:
                    result.append(text)
                    continue
                new_text, hits = apply_chosen(text, chosen)
                for rule, count, denom in hits:
                    if LOG_REPLACE:
                        event("[REPLACE] {} | \"{}\" -> \"{}\" (確率{}/{}) {}箇所".format(
                            site, snip(rule.disp_from), snip(rule.disp_to),
                            rule.prob, denom, count),
                            ("REPLACE", rule.disp_from, rule.disp_to))
                if new_text != text:
                    seen.add(new_text)
                    changed = True
                result.append(new_text)

        # 見送りだけの推論（chosen が空）も数えた分があれば区切りを見る。
        if LOG_REPLACE:
            tally = take_tally(store, quiet_call=bool(quiet))
            if tally:
                write(tally)
            if quiet or tally:
                save_pending(store)
        return result if changed else None

    # 仕掛ける場所はローダの担当（`instantale_modloader.llm`）。
    # ローカルの3点もクラウドの `llm_manager` 別名も、
    # 別名の後生えの見張りも向こうにある。
    # こちらが渡すのは「この並びをどう書き換えるか」だけ。
    hooks = wrap_outgoing(
        ctx, run, label="prompt replace",
        on_arm=lambda target: report(
            "[RULES] 遅れて仕掛けた: {}".format(target), force=True))

    # 注入した時点で、作ったデータで正しさを確かめておく。
    # 実経路はゲームが LLM を呼ぶまで通らず、
    # 起動直後に通る保証が無いため（102_ / 105_ と同じ方針）。
    _verify(ctx)

    # どこに仕掛かったかと、どのルールファイルを読むのかを残す。
    armed = hooks.armed()
    groups = rules.current()
    ctx.log("prompt replace: armed on {} | {} | log {}".format(
        ", ".join(armed) if armed else "nothing (targets missing)",
        "{} rule group(s) from {}".format(len(groups), rules.path) if groups
        else "no rules in {} (nothing will be replaced)".format(rules.path),
        log_path))


# --------------------------------------------------------------------------
# 自己検証
# --------------------------------------------------------------------------
# プロキシの書式を1つずつ含めた見本。
# タブ・無効タブ・コメント・確率・0%・エスケープ・正規表現の後方参照。
_SAMPLE_LINES = [
    "#tab:標準",
    "#off:切った行=>効かない",
    "古い言い方=>新しい言い方",
    "念のため=>必ず替える=>100",
    "絶対に替えない=>替わってしまった=>0",
    "改行を入れる=>入れた\\n次の行",
    "regex:(残り: -?\\d+)  - 効果:=>$1\\n  - 効果:",
    "#offtab:切ったタブ",
    "この行は切ったタブの中=>効かない",
]

_SAMPLE_TEXT = ("古い言い方。念のため。絶対に替えない。改行を入れる。"
                "この行は切ったタブの中。残り: -1  - 効果: なし")

_EXPECTED_TEXT = ("新しい言い方。必ず替える。絶対に替えない。入れた\n次の行。"
                  "この行は切ったタブの中。残り: -1\n  - 効果: なし")


def _verify(ctx):
    rules, warnings = parse_rules(_SAMPLE_LINES)
    if warnings:
        ctx.log("VERIFY FAILED: sample rules produced warnings: {}".format(warnings),
                level="ERROR")
        return
    if len(rules) != 5:
        ctx.log("VERIFY FAILED: expected 5 rules from the sample, got {}".format(
            len(rules)), level="ERROR")
        return

    groups = group_rules(rules)
    chosen, skipped = decide(groups, _SAMPLE_TEXT, roll=lambda denom: 0)
    got, _hits = apply_chosen(_SAMPLE_TEXT, chosen)
    if got != _EXPECTED_TEXT:
        ctx.log("VERIFY FAILED: unexpected replacement\n  got      {!r}\n"
                "  expected {!r}".format(got, _EXPECTED_TEXT), level="ERROR")
        return
    if len(skipped) != 1 or "確率0" not in skipped[0][3]:
        ctx.log("VERIFY FAILED: the 0% rule should have been skipped, got {!r}".format(
            skipped), level="ERROR")
        return

    # 同じ「置換前」が2行あるときは1回の抽選でどちらかに決まる（合計 120 >
    # 100 なので必ずどちらかになる）。
    # 抽選値を固定して両方の枝を通す。
    pair, _warnings = parse_rules(["同じ前=>Aへ=>60", "同じ前=>Bへ=>60"])
    pair_groups = group_rules(pair)
    if len(pair_groups) != 1:
        ctx.log("VERIFY FAILED: same-from rules must share one group", level="ERROR")
        return
    outcomes = []
    for value in (0, 70):
        chosen, _skipped = decide(pair_groups, "同じ前", roll=lambda denom, v=value: v)
        text, _hits = apply_chosen("同じ前", chosen)
        outcomes.append(text)
    if outcomes != ["Aへ", "Bへ"]:
        ctx.log("VERIFY FAILED: probability groups picked {!r}".format(outcomes),
                level="ERROR")
        return

    # 復号（ボディの形で書かれたルールが復号済みの本文に当たること）。
    escaped, _warnings = parse_rules(["\\u3042い=>う\\u3048"])
    if len(escaped) != 2 or escaped[1].from_text != "あい" or escaped[1].to_text != "うえ":
        ctx.log("VERIFY FAILED: escaped rule was not decoded: {!r}".format(
            [(r.from_text, r.to_text) for r in escaped]), level="ERROR")
        return

    ctx.log("verified: rule format (tabs / #off / probability / escapes / regex $1), "
            "one draw per group, decoded forms registered")
