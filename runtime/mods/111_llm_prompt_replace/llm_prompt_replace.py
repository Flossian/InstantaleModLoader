# -*- coding: utf-8 -*-
"""LLM へ送る文章を、利用者が書いた置換ルールで書き換える。

外部プロキシ（InstantaleLLMProxy）の置換機能をプロセス内で同じ書式のまま動かす。
プロキシ用に書いた `llm_replacements.txt` をこの MOD のフォルダへ置けばそのまま動く。

    置換前=>置換後            そのまま置き換える
    置換前=>置換後=>60        60% の確率で置き換える（0-100・省略時は 100）
    regex:正規表現=>$1…       置換前を正規表現として解釈する
    #tab:名前 / #offtab:名前  タブの区切り。`#offtab:` の中は全部無視
    #off:… や行頭 #           コメント（`#memo:…` も読み飛ばす）

同じ「置換前」の行はグループとして**1回だけ抽選する**（プロキシと同じ。確率の合計が
100 を超えるなら `値/合計` の割合で必ずどれかに置き換わる）。

## プロキシとの違い（同じルールでも扱いを変えている3点）

プロキシは JSON エンコード後の HTTP ボディを見ていたが、ここで見えるのは復号済みの
Python 文字列。そのままでは当たらないので:

1. **エスケープは復号してから使う。** 置換後は必ず復号し、置換前は素の形と復号形の
   両方を登録する（`\\n` 入りの既存ルールを生かすため）。本物の `\\` は `\\\\`
2. **置換後の `$1` / `${name}` / `$&` / `$$`（.NET）は Python の後方参照へ読み替える。**
   存在しない番号は警告して文字列扱い。**パターン側は読み替えない**（改行は素直に `\\n`）
3. **.NET の 1 秒タイムアウトの代わり**に、照合へ 1 秒以上かかった正規表現ルールを
   以後捨てる（`SLOW_REGEX_SECONDS`。1回目の暴走だけは止められない）

## どこに仕掛けるか（経路の実測と経緯は GAME.md §2.12 / VERIFICATION.md §2.24）

  * ローカル（llama.cpp）: `LlamaCppClient` の chat / _apply_chat_template /
    _post_with_model_loading_retry の3点。どれが通るかはビルドと経路で変わる
  * クラウド（APIキー）: 送信モジュールがプロバイダごとに違うので名指しせず、
    どの経路でも import される **`llm_manager` の別名 `send_request*`** を包む
    （ローダの alias_scan が、同じ関数を持つ全モジュールを張り替える）。
    ログの site はラップした元関数の `__module__` から採る＝プロバイダ名が残る

はまりどころ3つ（詳細は各実装コメント）:

  * **1回の推論で抽選は1回だけ。** 複数地点で抽選すると確率の分母が壊れる。
    スレッドごとの印で内側の地点を素通しし、印が届かない別スレッド経路は
    「自分が作った文章」のハッシュで二度目を止める（`306_` と同じ手口）
  * **ローカル実行では `llm_manager` 境界に触らない。** send_request は内部で
    別スレッドに降りてから chat を呼ぶため印が届かず、二重抽選になる。
    llama.cpp の送信モジュールが import されているかで見分ける
  * **`llm_manager` の別名は初期化時に後から生える。** ローダの保留はモジュール
    単位で、属性の後生えは拾わない。無かったぶんは自前の見張りで当てる
    （`ALIAS_POLL_SECONDS` ごと・`ALIAS_WATCH_SECONDS` で諦める・注入し直しで降りる）

クラウド境界で見えるのは呼び出し側が渡した `message` だけで、send_request の中で
足される部分（Gemini のスキーマ文など）には当たらない（GAME.md §1.8）。

## 適用順（`mod.json` の `after` / `before`）

`105_`（スキーマ圧縮）より後＝外側で、置換は**圧縮前**の本文を見る（「JSON安定化」
タブがスキーマの repr を狙うため）。`305_`（ミニクエスト）より前＝内側で、あちらには
書き換え前の本文を見せる（完全一致前提のため）。置換ルールが `103_` の目印を
書き換えるとあちらが止まるのは、ルールを書く側の責任。

## ルールファイル

この MOD のフォルダの中だけ。**探索はしない**（TECH.md §3.1.1）:

    llm_replacements.txt          利用者のルール。あればこちらを読む
    llm_replacements.default.txt  同梱の既定。MOD 更新で上書きされるのはこちらだけ
                                  （利用者のルールは配布物に無いので生き残る）

変更は**リクエストのたびに反映**（更新時刻と大きさで読み直す。読めない間は前回の
ルールで続け、無ければ何もしない）。`out\\prompt_bloat.log` に `[RULES]`（読込）・
`[REPLACE]`（置換）・`[SKIP]`（確率で見送り）が出る。`102_` / `103_` / `105_` と
同じファイルなので、置換と圧縮のどちらが先に効いたかを時系列で読める。
"""

import collections
import datetime
import hashlib
import os
import random
import re
import sys
import threading
import time

# --------------------------------------------------------------------------
# 設定（既定値。`mod.json` の "settings" が同じ値を宣言している）
# --------------------------------------------------------------------------
LOG_REPLACE = True           # [REPLACE] / [SKIP] を残すか
LOG_RULES = True             # [RULES]（読込）を残すか

# ルールファイルは**この MOD のフォルダの中**だけ（探索も設定もしない）。
# 利用者のファイルは配布物に入っていない＝MOD を更新しても残る（`rules_path`）。
RULES_FILE_NAME = "llm_replacements.txt"                  # 利用者のルール（優先）
DEFAULT_RULES_FILE_NAME = "llm_replacements.default.txt"  # 同梱の既定

# 書式（プロキシと同じ）
SEPARATOR = "=>"
REGEX_PREFIX = "regex:"
TAB_PREFIX = "#tab:"
OFFTAB_PREFIX = "#offtab:"

SNIP_CHARS = 40              # ログに出す断片の長さ（プロキシの Snip と同じ）
SLOW_REGEX_SECONDS = 1.0     # 照合にこれ以上かかった正規表現は以後使わない
SEEN_TEXTS = 64              # 「自分が作った文章」を覚えておく件数

# 仕掛ける先。全部 `required=False`（ビルドと経路によって無い可能性がある）。
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"
TEMPLATE_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template"
POST_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry"

# 外部APIキー（クラウド）経路。プロバイダごとに送信モジュールが分かれているので、
# モジュール名ではなく `llm_manager` が持つ別名を包む（docstring「どこに仕掛けるか」）。
# 第2要素は send_request_with_no_structure か（ログの site に "_ns" を付ける）。
MANAGER_SEND_TARGETS = (
    ("scripts.llm.llm_manager:send_request", False),
    ("scripts.llm.llm_manager:send_request_with_no_structure", True),
)

# これが import されていたらローカル実行（クラウドとどちらか片方しか載らない）。
LOCAL_REQUEST_MODULE = "scripts.llm.request_llm_inference_llama_cpp_completion"

# 「別名の後生え」の見張り（docstring「どこに仕掛けるか」）。
ALIAS_POLL_SECONDS = 5.0     # 生えたかを見る間隔
ALIAS_WATCH_SECONDS = 3600.0  # これだけ待って生えなければ諦める

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

    知らないエスケープ（`\\d` など）は**そのまま残す**。ルールに正規表現めいた
    書き方が混じっていても壊さないため。`\\uD83D\\uDE00` のような UTF-16 の
    代理対は1文字に戻す（`ensure_ascii=True` はコード単位ごとに書くため）。
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

    `$1` `${name}` `$&`（一致全体）`$$`（`$` 自身）を読む。`$` の後がそれ以外なら
    文字として扱う（.NET も同じ）。文字の部分は `unescape` を通すので、`\\n` は
    改行として入る。

    Python の `re.sub` のテンプレート文字列にせず自前で組み立てるのは、置換後に
    `\\1` や `\\g` を含む文章が来たときの取り違えを起こさないため。
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

    `disp_from` / `disp_to` はログに出す姿で、復号する前の書き方をそのまま見せる
    （ルールファイルを目で照合できるようにするため）。
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

        # 「置換前=>置換後=>確率」。確率として読めない末尾は置換後の一部とみなす。
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
            # ボディの中の形（`\n` や `\uXXXX`）で書かれたルール。本文は復号済みなので
            # 復号した形でも登録する。当たるのはどちらか一方だけ。
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
    抽選は**グループごとに1回**で、そのグループのどのルールも当たらないことがある
    （確率の合計が 100 未満のとき）。
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
                # 止められないので、次からは使わない。1回目の遅れだけは受け入れる。
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


def snip(text):
    """ログ用に短く切る（プロキシの Snip と同じ長さ）。改行は見える形にする。"""
    text = text.replace("\r", "").replace("\n", "\\n")
    return text if len(text) <= SNIP_CHARS else text[:SNIP_CHARS] + "…"


# --------------------------------------------------------------------------
# ルールファイル
# --------------------------------------------------------------------------
def rules_path(mod_dir):
    """読むルールファイル。**この MOD のフォルダの中だけ**（探索はしない）。

    利用者のファイル（`llm_replacements.txt`）があればそれを、無ければ同梱の既定
    （`llm_replacements.default.txt`）を返す。**利用者のファイルは配布物に入って
    いない**ので、MOD を新しい版に差し替えても上書きされずに残る。

    配布フォルダの `settings\\` も外部プロキシの置き場所も見ない（MOD 単体の部品は
    MOD のフォルダで完結させる。外に出るのは `out\\` のログだけ）。
    `mod_dir` は `apply()` の外では None になるので、呼ぶ側が控えておくこと。

    既定のファイルが無くても、その場所を返す（呼ぶ側が「無い」を扱う）。
    """
    if not mod_dir:
        return None
    user = os.path.join(mod_dir, RULES_FILE_NAME)
    if os.path.isfile(user):
        return user
    return os.path.join(mod_dir, DEFAULT_RULES_FILE_NAME)


class RuleFile(object):
    """ルールファイル1つ。**リクエストのたびに更新を見て読み直す**。

    ゲームを再起動しなくてもルールの変更が次のリクエストから効く（プロキシの
    `ReloadRulesIfChanged` と同じ）。読めなかったとき（保存の書き込み途中など）は
    前回のルールを使い続け、次のリクエストで再試行する。

    **ファイルが無いのは異常ではない**（ルールを書いていなければ置換しないだけ）。
    後から置いた場合も、次のリクエストで拾われる。利用者のファイルを置いた／消した
    ときは、読む先もその場で切り替わる（`rules_path`）。
    """

    def __init__(self, mod_dir, report):
        self.mod_dir = mod_dir
        self.report = report          # ログを出す関数（行, force）
        self.path = rules_path(mod_dir)
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
        if not self.mod_dir:
            return
        # 利用者のファイルが置かれた／消えたら、読む先を入れ替える。
        # ファイル1つを見るだけなので、リクエストごとに確かめても軽い。
        path = rules_path(self.mod_dir)
        if path != self.path:
            self.report("[RULES] 読む先を切り替えた: {} -> {}".format(self.path, path),
                        force=True)
            self.path = path
            self.stamp = None
        if not os.path.isfile(self.path):
            # 消された。**前のルールを握り続けない**（消したのに効き続ける方が驚かれる）。
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

        # 印は**読めた時点で**進める。書式の解析で落ちた場合に、同じ内容を
        # リクエストごとに読み直して同じ苦情を並べないため（次の保存で再挑戦する）。
        self.stamp = stamp
        try:
            rules, warnings = parse_rules(lines)
        except Exception as exc:
            self.report("[RULES] ルールを読めなかったので前回のまま続ける（{}）".format(exc),
                        force=True)
            return
        self.groups = group_rules(rules)
        self.count = len(rules)
        for line in warnings:
            self.report("[RULES] " + line, force=True)
        self.report("[RULES] 読込: {} {}パターン / {}グループ".format(
            self.path, self.count, len(self.groups)))


# --------------------------------------------------------------------------
# 「1回の推論で1回だけ」の歯止め
# --------------------------------------------------------------------------
_pass_local = threading.local()


def begin_pass():
    """このスレッドで既に置換したかを返し、印を立てる。"""
    already = getattr(_pass_local, "active", False)
    _pass_local.active = True
    return already


def end_pass(previous):
    _pass_local.active = previous


class Seen(object):
    """自分が作った文章を覚えておく輪。二度目に来たものは触らない。

    印（スレッド）が届かない経路（`chat` が返した後に別のスレッドが送る場合）
    でも、同じ文字列に確率の抽選をもう一度させないため。中身ではなく
    ハッシュを持つ（プロンプトは数万文字になるので、そのまま抱えない）。
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


def content_of(message):
    """メッセージの本文。dict でなければ None（＝触らない）。"""
    try:
        content = message.get("content")
    except Exception:
        return None
    return content if isinstance(content, str) else None


# --------------------------------------------------------------------------
# 注入
# --------------------------------------------------------------------------
def apply(ctx):
    log_path = ctx.out_path("prompt_bloat.log")
    # ルールは MOD のフォルダの中。`ctx.mod_dir` は apply() の外では None になるので、
    # ここで控えておく（ラッパは後からこの値を使う）。
    mod_dir = ctx.mod_dir
    seen = Seen()

    def write(text):
        # DEDUP / EVENTLOG / COMPACT と同じファイルに出す。置換と圧縮のどちらが
        # 先に効いたのかを時系列で読めるようにするため。
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("replace: write failed")

    def report(line, force=False):
        if force or LOG_RULES:
            write(line)

    rules = RuleFile(mod_dir, report)

    def run(site, texts):
        """文章の並びにルールを当てる。変わらなければ None。"""
        groups = rules.current()
        if not groups:
            return None
        fresh = [text for text in texts if text and text not in seen]
        if not fresh:
            return None                       # 全部が自分の出力。抽選もしない

        # 判定と抽選は**全部を繋いだ文章に対して1回**（プロキシがボディ全体を1回で
        # 見ていたのと同じ）。書き換えは1つずつに当てる。
        chosen, skipped = decide(groups, "\n".join(fresh))
        for rule, total, denom, why in skipped:
            if rule.dropped:
                write("[RULES] 正規表現を捨てた: \"{}\" {}".format(
                    snip(rule.disp_from), why))
            elif LOG_REPLACE:
                write("[SKIP] {} | \"{}\" {}{}".format(
                    site, snip(rule.disp_from), why,
                    "" if not total else " ({}/{})".format(total, denom)))
        if not chosen:
            return None

        result = []
        changed = False
        for text in texts:
            if not text or text in seen:
                result.append(text)
                continue
            new_text, hits = apply_chosen(text, chosen)
            for rule, count, denom in hits:
                if LOG_REPLACE:
                    write("[REPLACE] {} | \"{}\" -> \"{}\" (確率{}/{}) {}箇所".format(
                        site, snip(rule.disp_from), snip(rule.disp_to),
                        rule.prob, denom, count))
            if new_text != text:
                seen.add(new_text)
                changed = True
            result.append(new_text)
        return result if changed else None

    def replace_messages(messages, site):
        """messages の本文にルールを当てる。**元のリストは書き換えない**。

        会話履歴としてゲーム側が同じ dict を持ち続けている可能性があるため、
        浅い写しを作って差し替える（`105_` と同じ理由）。
        """
        if not isinstance(messages, list) or not messages:
            return messages
        spots = []
        for index, message in enumerate(messages):
            content = content_of(message)
            if content:
                spots.append((index, content))
        if not spots:
            return messages

        result = run(site, [content for _index, content in spots])
        if result is None:
            return messages

        new_messages = list(messages)
        for (index, before), after in zip(spots, result):
            if after == before:
                continue
            replacement = dict(new_messages[index])
            replacement["content"] = after
            new_messages[index] = replacement
        return new_messages

    def safely(what, fn, fallback):
        try:
            return fn()
        except Exception:
            # 置換に失敗しても文章はそのまま送る。ここで止める方が損害が大きい。
            ctx.log_exc("replace: {} pass failed; sending the text untouched".format(what))
            return fallback

    # ------------------------------------------------------- chat（実際の経路）
    @ctx.wrap(CHAT_TARGET, required=False)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        previous = begin_pass()
        try:
            if not previous:
                messages = safely("chat", lambda: replace_messages(messages, "chat"),
                                  messages)
            return orig(self, model, messages, format, *args, **kwargs)
        finally:
            end_pass(previous)

    # ------------------------------------------ messages（102_ と同じ地点・保険）
    @ctx.wrap(TEMPLATE_TARGET, required=False)
    def apply_chat_template(orig, self, model, messages, timeout=None, *args, **kwargs):
        previous = begin_pass()
        try:
            if not previous:
                messages = safely("template",
                                  lambda: replace_messages(messages, "template"),
                                  messages)
            return orig(self, model, messages, timeout, *args, **kwargs)
        finally:
            end_pass(previous)

    # ------------------------------------------- payload（プロキシと同位置・保険）
    @ctx.wrap(POST_TARGET, required=False)
    def post_with_retry(orig, self, url, payload, timeout=None, *args, **kwargs):
        previous = begin_pass()
        try:
            if not previous and isinstance(payload, dict):
                prompt = payload.get("prompt")
                if isinstance(prompt, str) and prompt:
                    result = safely("payload", lambda: run("payload", [prompt]), None)
                    if result and result[0] != prompt:
                        # 呼び出し元の dict は変えず、浅い写しを渡す。
                        payload = dict(payload)
                        payload["prompt"] = result[0]
            return orig(self, url, payload, timeout, *args, **kwargs)
        finally:
            end_pass(previous)

    # ---------------------------------------------- 外部APIキー（クラウド）経路
    # クラウドは LlamaCppClient を通らないので、上の3つだけでは素通りになる。
    # 送信モジュールはプロバイダごとに違ううえコンパイル済みなので、モジュール名に
    # 依存しない `llm_manager` の別名を包む（docstring「どこに仕掛けるか」）。
    # 本文は第2位置引数（または message=）のリスト。並びが
    # `send_request(manager_name, message, structure, ...)` でないプロバイダも
    # ありうるので、位置に決め打ちせず両方を見る。
    def replace_message_arg(site, args, kwargs):
        """呼び出しの (args, kwargs) の中の message にルールを当てる。"""
        if len(args) >= 2 and isinstance(args[1], list):
            replaced = replace_messages(args[1], site)
            if replaced is not args[1]:
                args = args[:1] + (replaced,) + args[2:]
        elif isinstance(kwargs.get("message"), list):
            replaced = replace_messages(kwargs["message"], site)
            if replaced is not kwargs["message"]:
                kwargs = dict(kwargs, message=replaced)
        return args, kwargs

    def cloud_site(orig, ns):
        """ログの site。ラップした元関数の持ち主からプロバイダ名を採る。"""
        module = (getattr(orig, "__module__", "") or "").rpartition(".")[2]
        if module.startswith("request_llm_inference_"):
            module = module[len("request_llm_inference_"):]
        return (module or "cloud") + ("_ns" if ns else "")

    def hook_manager_send(target, ns):
        @ctx.wrap(target, required=False)
        def manager_send(orig, *args, **kwargs):
            # ローカル実行ではここでは触らない。send_request は内部で別スレッドに
            # 降りるため印が届かず、LlamaCppClient 側の3点と二重に抽選してしまう。
            if sys.modules.get(LOCAL_REQUEST_MODULE) is not None:
                return orig(*args, **kwargs)
            previous = begin_pass()
            try:
                if not previous:
                    site = cloud_site(orig, ns)
                    args, kwargs = safely(
                        site, lambda: replace_message_arg(site, args, kwargs),
                        (args, kwargs))
                return orig(*args, **kwargs)
            finally:
                end_pass(previous)

    def resolvable(target):
        try:
            return ctx.resolve(target)[2] is not None
        except Exception:
            return False

    # 居る別名は今すぐ包む。まだ生えていない別名は見張りに回す（起動直後の
    # 注入では llm_manager に send_request がまだ無い。docstring のとおり）。
    unarmed = []
    for _target, _ns in MANAGER_SEND_TARGETS:
        if resolvable(_target):
            hook_manager_send(_target, _ns)
        else:
            unarmed.append((_target, _ns))

    if unarmed:
        def watch_manager_alias():
            deadline = time.monotonic() + ALIAS_WATCH_SECONDS
            remaining = list(unarmed)
            while remaining and not ctx.superseded():
                for item in list(remaining):
                    target, ns = item
                    if not resolvable(target):
                        continue
                    hook_manager_send(target, ns)
                    remaining.remove(item)
                    ctx.log("prompt replace: late-armed on {} "
                            "(the alias appeared)".format(target))
                    report("[RULES] 遅れて仕掛けた: {}".format(target), force=True)
                if not remaining:
                    return
                if time.monotonic() > deadline:
                    ctx.log("prompt replace: gave up waiting for {} "
                            "({}s)".format(
                                ", ".join(t for t, _n in remaining),
                                int(ALIAS_WATCH_SECONDS)), level="WARN")
                    return
                time.sleep(ALIAS_POLL_SECONDS)

        threading.Thread(target=watch_manager_alias,
                         name="111_replace_alias_watch", daemon=True).start()

    # 注入した時点で、作ったデータで正しさを確かめておく。実経路はゲームが LLM を
    # 呼ぶまで通らず、起動直後に通る保証が無いため（102_ / 105_ と同じ方針）。
    _verify(ctx)

    # どこに仕掛かったかと、どのルールファイルを読むのかを残す。
    armed = []
    for target in ((CHAT_TARGET, TEMPLATE_TARGET, POST_TARGET)
                   + tuple(t for t, _ns in MANAGER_SEND_TARGETS)):
        try:
            _owner, _name, value = ctx.resolve(target)
        except Exception:
            value = None
        if value is not None:
            armed.append(target.rpartition(".")[2])
    groups = rules.current()
    ctx.log("prompt replace: armed on {} | {} | log {}".format(
        ", ".join(armed) if armed else "nothing (targets missing)",
        "{} rule group(s) from {}".format(len(groups), rules.path) if groups
        else "no rules in {} (nothing will be replaced)".format(rules.path),
        log_path))


# --------------------------------------------------------------------------
# 自己検証
# --------------------------------------------------------------------------
# プロキシの書式を1つずつ含めた見本。タブ・無効タブ・コメント・確率・0%・
# エスケープ・正規表現の後方参照。
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

    # 同じ「置換前」が2行あるときは1回の抽選でどちらかに決まる（合計 120 > 100 なので
    # 必ずどちらかになる）。抽選値を固定して両方の枝を通す。
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
