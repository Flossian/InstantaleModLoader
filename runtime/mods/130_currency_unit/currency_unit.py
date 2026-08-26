# -*- coding: utf-8 -*-
"""通貨の呼び名と、画面上部の所持金の表示を差し替える。

通貨まわりの文字は**2つの経路**に分かれている。
どちらもゲームの中に文字列として書かれていて、片方だけ直すと食い違う。

    1. 文中の通貨    `scripts.languages:tr` を通る（画面 ＋ LLM への指示文）
    2. 画面上部の欄  `Gold:` の見出し。`tr` を通らない素の英語

## 1. 文中の通貨（`tr`）

素のゲームの通貨は「ゴールド」で、表記は2つある:

    長い形   `1000ゴールドを支払った。快適な旅だ...`
    短い形   `馬車(1000G)` / `簡易寝台(10G)` / `貴族権(1,000,000G)`

どちらも `scripts.languages:tr(text)` を通る。
多言語化の入口で、日本語の文を今の言語の文に置き換えている
（`translate_dict` が完全一致、`pattern_dict` が正規表現）。
ゲームの文言はここを通ってから画面にも指示文にも回るので、
**戻ってきた文字列を直せば両方が同時に変わる**（GAME.md §2.29）。

仕掛けるのはそこ1点だけ。
画面の部品やプロンプトを個別に追いかけてはいない。

### LLM に「認識させる」ために何をしているか

ゲームが LLM へ送る指示文にも通貨は書かれている（実文言）:

    必ず前払いで(数)ゴールドの雇用費を提示する。
    治療依頼の場合: ... 前払いで(数)Gの費用を提示する。絶対に値引きはしない。
    前払いで簡易寝台10G、個室100G、高級個室1000Gが必要な事を説明する。
    市民権を10000G, 貴族権を100000Gで購入できる。

これらも `tr` を通るので、差し替えた表記のまま LLM へ届く。
NPC はその表記で値段を言い、こちらの入力もその表記の世界として読まれる。

届かないのは**表記を変える前に書かれた文**で、
人生ログ・会話の記憶・世界観は「ゴールド」のままセーブに残っている。
`tr` を通らないので、送る直前に同じ置換を当てる
（`REWRITE_PROMPTS`。`instantale_modloader.llm.wrap_outgoing`）。
置換は何度当てても結果が変わらないため、
`111_` のような「1回の推論で抽選は1回」の受け皿は要らない（`119_` と同じ）。

## 2. 画面上部の所持金（`update_status_texts`）

画面上部の能力欄は1本の文字列で、`InstanTaleHUD.status_texts`（StringProperty）に入る:

    Atk:432(+500)\\nDef:0(+500)\\nExp:1675/3237\\nGold:1116472\\nAge:31\\n...

`Gold:` `Age:` `Atk` `Def` `Exp` `Sta` `Location` は**素の英語のまま**で、
`translate_dict` にも `pattern_dict` にも無い。
だから言語を日本語にしても `Gold:` と出る（GAME.md §2.29）。

ここは `tr` を通らないので、property の見張り
（`InstanTaleHUD.update_status_texts(self, instance, value)`）を包み、
`Gold:` の行を `HUD_GOLD_FORMAT` で組み直す:

    Gold:{amount}          素のゲームの見え方（既定）
    所持金:{money}{short}  → `所持金:13,184Slv`
    {money} {long}         → `13,184 シルバー`
    ${money}               → `$13,184`

**見出しの `Gold` は呼び名を変えただけで付け替わる。**
`Gold` は文中の `ゴールド` や英語表示の ` gold` と同じ「通貨の呼び名」で、
綴りが違うだけだから。
呼び名を `シルバー` にすれば、書式を触っていなくても
既定の `Gold:{amount}` が `シルバー:{amount}` として使われる
（`effective_template`）。
呼び名も書式も素のままのときだけ、ここには何も仕掛けない。

**見張りが `value` を見るとは限らない。**
`self.status_texts` を読み直して塗るビルドだと、
渡した文字列を直しても画面は変わらない。
だから塗り終えた後にラベル（`status_label`）を1度見て、
見出しがまだ残っていたらそこで直す（`repaint`）。
どちらで効いたかは `out\\currency_unit.log` に残る。

## 表記そのものはローダが持つ

`309_`（罰金）・`314_`（馬車代）・`315_`（宿代）・`902_` は
自分で書いた「〜ゴールド」を画面に出す。
`tr` を通らないので、この MOD が横から直すことはできない。
表記と置換の形はローダの語彙に置いてあり（`ui.set_currency` /
`ui.rewrite_coins` / `ui.parse_coin`。TECH.md §5.1）、各 MOD がそれを通す。
この MOD が入っていなければ表記は素のまま（`ゴールド` / `G`）で、
どの MOD の見た目も変わらない。

`ui.parse_coin` があるのは、`314_` と `315_` が**画面のラベルから**
素の運賃・宿代を読み取っているため（`馬車(1000G)` → 1000）。
表記を差し替えると `馬車(1000円)` になるので、
読む側も新しい表記を知っている必要がある。

## 変えないこと

  * **額そのもの**。値段も所持金も1つも動かない
  * **数の前に付ける記号**（`$1000`）。文中の短い形は数の後ろにしか置けない
    （画面上部の欄は `HUD_GOLD_FORMAT` で前にも置ける）
  * **キャラクタ作成画面の `所持金`**。あちらは `tr` を通る日本語の見出しで、
    通貨の語を含まない
  * **画面上部の他の見出し**（`Atk` / `Def` / `Exp` / `Age` / `Sta` / `Location`）。
    通貨の話ではないので触らない

セーブには跡が残る。
`<行動>1000Gを手に入れた。` のような行動ログは `tr` を通ってから書かれるので、
表記を変えた後に貯まるぶんは新しい表記で残る。
古い行と混ざるが増えるものは無く、外せば以後は素の表記に戻る
（既に書かれた行はそのまま）。
"""

import re

from instantale_modloader import frames, llm, ui

LOG_BASENAME = "currency_unit.log"

# ログに出すプロンプト断片の長さ（`111_` の Snip と同じ考え）。
SNIP_CHARS = 60

# --------------------------------------------------------------------------
# 画面上部の所持金の欄（実機のビルドの文字列。GAME.md §2.29）
# --------------------------------------------------------------------------
#: 見出し。`tr` を通らないので言語を変えても英語のまま。
HUD_GOLD_MARK = "Gold:"

#: 見出しのうち**通貨の呼び名**にあたる部分。
#: `ゴールド`（文中）や ` gold`（英語表示）と同じものが、
#: ここでは `Gold` と綴られている。呼び名を変えたらここも変える。
HUD_GOLD_WORD = "Gold"

#: 見出しからその行の終わりまで。欄は改行で区切られている。
HUD_GOLD_RE = re.compile(re.escape(HUD_GOLD_MARK) + r"([^\n]*)")

#: 塗り終えた文字列を持つラベル（実測。`206_probe_quest_flow` が読んでいる）。
HUD_STATUS_LABEL = "status_label"

#: 見出しに1度も当たらないまま何回来たら「形が違う」と言うか。
#: 1回ごとに言わないのは、能力欄が組み上がる前の空文字列でも呼ばれるため。
HUD_MISS_LIMIT = 50

#: 素のゲームの見え方。
#: **設定がこれと同じなら画面上部には触らない**ための照合値。
GAME_HUD_GOLD = "Gold:{amount}"

# --------------------------------------------------------------------------
# 設定（既定値。`mod.json` の "settings" が同じ値を宣言している）
# --------------------------------------------------------------------------
# 既定は**素のゲームの言い方と見え方**。同じままなら何も仕掛けない。
UNIT_LONG = "ゴールド"            # 文中で使う形
UNIT_SHORT = "G"                 # 数のすぐ後ろに付く形
HUD_GOLD_FORMAT = "Gold:{amount}"  # 画面上部の所持金の欄
REWRITE_PROMPTS = True           # LLM へ出ていく本文にも同じ置換を当てる
LOG_LIMIT = 20                   # 記録に残す置換の件数


class _SafeDict(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。

    `314_` / `315_` と同じ形。
    設定の打ち間違いを画面で見えるようにするため、黙って素へ落とさない。
    """

    def __missing__(self, key):
        return "{" + str(key) + "}"


def snip(text):
    """ログ用に切り詰める。"""
    text = str(text)
    return text if len(text) <= SNIP_CHARS else text[:SNIP_CHARS] + "..."


def number(text):
    """見出しの後ろの数を int にする。読めなければ渡されたものをそのまま返す。

    ゲームが何を書いているか（int か float か、桁区切りが入るか）は
    実機で確かめていないので、3通りとも受ける。
    """
    raw = str(text).strip().replace(",", "")
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return int(float(raw))
    except ValueError:
        return text


def effective_template(template=None):
    """実際に使う書式。**見出しの `Gold` は通貨の長い表記に付け替える。**

    画面上部の `Gold` は、文中の `ゴールド` や英語表示の ` gold` と同じ
    「通貨の呼び名」で、綴りが違うだけ。
    呼び名を変えたのにここだけ英語で残るのは食い違いなので、
    書式を触っていなくても既定の `Gold:{amount}` が
    `シルバー:{amount}` になる。

    自分で見出しを書いた書式（`所持金:{money}`）には `Gold` が無いので、
    何も起きない。
    呼び名が素のままなら書式もそのままで、画面はゲームのとおり。
    """
    template = HUD_GOLD_FORMAT if template is None else template
    long_name, _short_name = ui.currency_names()
    if long_name == ui.COIN_LONG:
        return template
    return str(template).replace(HUD_GOLD_WORD, long_name)


def restate(text, template=None):
    """画面上部の所持金の行をテンプレートで組み直す。当たらなければ `None`。

    テンプレートで使える変数は4つ:

        {amount}  ゲームが書いた数字をそのまま
        {money}   3桁ごとに区切った数字（`ui.money`）
        {long}    通貨の長い表記   {short}  通貨の短い表記
    """
    if not isinstance(text, str) or HUD_GOLD_MARK not in text:
        return None
    template = effective_template(template)
    long_name, short_name = ui.currency_names()

    def one(match):
        amount = match.group(1)
        try:
            return str(template).format_map(_SafeDict(
                amount=amount, money=ui.money(number(amount)),
                long=long_name, short=short_name))
        except Exception:
            return match.group(0)      # 壊れたテンプレートはゲームのまま

    return HUD_GOLD_RE.sub(one, text)


def apply(ctx):
    # 表記はローダが持つ（他の MOD もここから読む）。
    # 素へ戻す指定でも必ず1回通す。
    # 設定を戻して注入し直したとき、前回の表記が残らないようにするため。
    asked = (UNIT_LONG, UNIT_SHORT)
    names = ui.set_currency(*asked)
    if names != asked:
        ctx.log("currency unit: cannot use {!r}; keeping {!r}".format(
            asked, names), level="WARN")

    wording = names != (ui.COIN_LONG, ui.COIN_SHORT)
    # 書式を触っていなくても、呼び名を変えたら見出しの `Gold` は付け替える
    # （`effective_template`）。だから条件は「どちらかが動いていれば」。
    hud = wording or HUD_GOLD_FORMAT != GAME_HUD_GOLD
    if not wording and not hud:
        ctx.log("currency unit: left at the game's own wording "
                "({} / {}) and display; nothing installed".format(*names))
        return

    write = ctx.logger(LOG_BASENAME)
    warn = ctx.warner("currency unit")
    state = {"logged": 0, "once": set(), "hud_hit": False, "hud_miss": 0}

    def note(site, before, after):
        """効いていることを確かめるための例。件数で打ち切る。"""
        if state["logged"] >= LOG_LIMIT:
            return
        state["logged"] += 1
        write("{}: {!r} -> {!r}".format(site, snip(before), snip(after)))

    def note_once(key, site, before, after):
        """毎フレーム来る所からの記録。鍵ごとに1回だけ。

        能力欄は HP や経験値が動くたびに塗り直されるので、
        件数の打ち切りだけだと `tr` や LLM の例が押し出される。
        """
        if key in state["once"]:
            return
        state["once"].add(key)
        note(site, before, after)

    # ---------------------------------------------------------- 文中の通貨
    if wording:
        # 画面と、ゲーム自身が組む指示文の両方がここを通る。
        # 元の関数を先に呼んでから直すので、
        # ここで壊れても safe=True が `orig` の結果をそのまま返せる（TECH.md §3.1.5）。
        @ctx.wrap("scripts.languages:tr", safe=True)
        def tr(orig, text=None, *args, **kwargs):
            result = orig(text, *args, **kwargs)
            new = ui.rewrite_coins(result)
            if new != result:
                note("tr", result, new)
            return new

    hooks = None
    if wording and REWRITE_PROMPTS:
        def rewrite(texts, site):
            """`tr` を通らずに LLM へ出ていく本文（セーブに貯まった文）を直す。"""
            new = [ui.rewrite_coins(text) for text in texts]
            if new == texts:
                return None
            for before, after in zip(texts, new):
                if before != after:
                    note("llm " + str(site), before, after)
            return new

        hooks = llm.wrap_outgoing(ctx, rewrite, label="currency unit")

    # ------------------------------------------------------ 画面上部の欄
    if hud:
        def repaint(widget_owner):
            """見張りが `value` を見ないビルドへの保険。

            塗り終えたラベルに見出しが残っていたら、そこで直す。
            見張りが渡された文字列を使っているなら、ここは毎回素通りする。
            """
            label = frames.attr(widget_owner, HUD_STATUS_LABEL, None)
            if label is None:
                warn("status_label",
                     "no {} on the HUD; the top-of-screen amount is left as the "
                     "game wrote it".format(HUD_STATUS_LABEL))
                return
            text = frames.text_of(label)
            if text is None or HUD_GOLD_MARK not in text:
                return                  # 見張りに渡した文字列で足りていた
            fixed = restate(text)
            if fixed is None or fixed == text:
                return
            label.text = fixed
            note_once("repaint", "hud repaint", text, fixed)

        @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_status_texts",
                  safe=True)
        def update_status_texts(orig, self, instance=None, value=None,
                                *args, **kwargs):
            fixed = restate(value)
            if fixed is None:
                # 組み上がる前は空文字列でも呼ばれる。
                # 1度も当たらないまま続いたときだけ「形が違う」と言う。
                state["hud_miss"] += 1
                if (not state["hud_hit"]
                        and state["hud_miss"] == HUD_MISS_LIMIT):
                    warn("no_mark",
                         "{!r} has not appeared in {} status update(s); the "
                         "top-of-screen amount is left as the game wrote "
                         "it".format(HUD_GOLD_MARK, HUD_MISS_LIMIT))
                return orig(self, instance, value, *args, **kwargs)
            state["hud_hit"] = True
            note_once("hud", "hud", value, fixed)
            result = orig(self, instance, fixed, *args, **kwargs)
            repaint(self)
            return result

    # 別名は起動直後にはまだ生えていない（`llm.wrap_outgoing` が見張って当てる）。
    # 生えていない時点で「off」に見えないよう、待っていることを書き分ける。
    if hooks is None:
        prompts = "off"
    else:
        armed = hooks.armed()
        prompts = "on(" + ",".join(armed) + ")" if armed else "on(waiting)"

    ctx.log("currency unit: {} / {} hud={!r} (prompts={}, log={})".format(
        names[0], names[1],
        effective_template() if hud else "game's own",
        prompts, ctx.out_path(LOG_BASENAME)))
