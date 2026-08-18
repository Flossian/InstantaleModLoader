# -*- coding: utf-8 -*-
"""修正: 本文（LLM の応答・システムメッセージ）の行間が広すぎて読みづらいのを詰める。

## 症状（採寸の値は VERIFICATION_LOG.md §2.25）

画面を実寸で測ると、行の間隔 70px に対して段落の間隔がちょうど2倍の 138px。
ここから2つ読める。

* **段落の間隔が行の間隔のちょうど2倍** ＝ 本文には段落の区切りとして空行が1行入っている（`\\n\\n`）。
  「会話などの場合は一部間隔が狭まる」のは空行を挟まない行が続いているところで、
  狭いのが本来の行間。
* その本来の行間そのものが広い。
  Kivy のラベルは既定（`line_height=1.0`）なら文字の 1.3〜1.4 倍で1行を送るので、
  42px 前後になるはずが 70px 出ている。

本文を描いているのは **`hud.text_display`**（`Label`）で、
`font_size=27` / **`line_height=1.8`** ＝ 行間はゲームが意図的に広げている（GAME.md
§2.3）。

どちらか片方だけ直しても足りない。
空行だけ詰めても本文全体が間延びしたままで、
`line_height` だけ下げても段落の空きは2倍のまま残る。

## 直し方

**書き換えるのはラベル1枚だけ**にする。
`InstanTaleHUD.display_text`（StringProperty）はゲームが本文を組み立てて持っている値そのもので、
ここを書き換えると本文の中身を変えることになる。
実際に困っているのは見た目なので、`update_display_text` が塗り終わった後に、
そのラベルの `line_height` と `text` だけを整える。
ゲームが持っている本文・会話履歴・セーブに入る文字列はどれも元のまま（`to_display_texts`
/ `to_save_texts`）。

1. **`line_height` は元の値に対する倍率で決める**（`LINE_SCALE`）。
   ゲームがいくつを入れているかはビルドによって変わりうるので、
   こちらで絶対値を決めない。
   最初に見たときの値を「設計値」として控え、以後はそれに掛ける。
   控えた値はラベル自身に持たせる（`_instantale_line_height`）。
   注入し直すと MOD 側の変数は作り直されるが、ラベルはゲームのプロセスに残るので、
   ここを見ないと**注入のたびに倍率が掛かって縮み続ける**。
2. **空行は連続改行の上限で詰める**（`BLANK_LINES`。既定 0 ＝ 空行なし）。
   段落の区切りは改行1つで十分読み取れる。
   `1` にすればゲームのままになる。

## ラベルの見つけ方

まず名前で引く（`hud.text_display`）。
リコンのダンプには出ない（インスタンス属性なので）が、
実測で確定した名前が GAME.md §2.3 に載っている。
**一度分かった名前は推測ではない**。

名前で引くことには、本文を書き換える他の MOD と共存できるという実利がある。
「`value` と同じ文字列を持つラベル」を探す作りだと、
`117_message_text_integrity` が長い本文を「前置き + ［…省略］ + 末尾
1000 文字」に載せ替えた時点で（`112_` より内側で走る）ラベルの text は
value と一致も包含もしなくなり、探索が空振りする（VERIFICATION_LOG.md §2.32）。

本文を手がかりにラベルを探す作りは、本文を触る MOD が増えた時点で壊れる。
名前で引けばこの依存が消える（`117_` / `118_` / `113_` も同じく名前で引いている）。

名前で引けなかったときのために、本文を手がかりにする探索は予備として残してある（ゲーム更新で属性名が変わった場合。
`value` を手がかりに `vars(hud)` → ウィジェット木の順で探す）。
予備の一致は完全一致に限らない。
実機で当たったのは「末尾が一致する」（`match=2`）で、
完全一致だけを条件にすると1回も見つからない。
短い文字列で「含まれる」を許すと選択肢のボタン（あれも `Label`）を掴みうるので、
完全一致以外は本文と同じくらい長いラベルに限る（`MIN_MATCH`）。

見つけたラベルは弱参照で覚える。
画面を作り直されたら消えるので、次の本文で探し直しになる。
見つけた経路・設計値・実測の行送りは `out/text_spacing.log` に1回だけ書く。
**倍率を決め直すときの根拠がそこにしか無い**ため。
"""

import re
import weakref

from instantale_modloader import frames

LOG_BASENAME = "text_spacing.log"

# 本文のラベルが入っている HUD の属性名（実測。GAME.md §2.3）。
# ここが引ければ本文の中身に一切依存しない ＝ 本文を書き換える他の
# MOD と共存できる。
# 引けなければ下の探索（`value` を手がかりにする予備）に落ちる。
LABEL_ATTR = "text_display"

# 行間。
# ゲームが入れている `line_height` に対する倍率。
# 絶対値ではなく倍率なのは、ゲーム側の値を実測で控えてから掛けるため（上の説明）。
# 空欄（null）にすると行間には触らない。
LINE_SCALE = 0.8

# 段落の区切りに残す空行の数。
# 0 ＝ 空行なし（改行1つ）、1 ＝ ゲームのまま。
# 空欄（null）にすると本文の改行には触らない。
BLANK_LINES = 0

# 記録の上限。
# 本文は1文字ずつ増えていくので（`InstantaleApp.add_text_display`）、
# 毎回書くとログが埋まる。
# 書くのは「新しいラベルを見つけたとき」だけ。
MAX_LOG = 40

# ウィジェット木を何段まで降りるか。
# HUD → レイアウト → ScrollView → ラベル。
MAX_DEPTH = 6

# この長さ以上の本文でだけラベルを探す。
# 短い文字列は他のラベル（ボタン・状態表示）とも一致しうるので、
# 探す手がかりにしない。
MIN_MATCH = 8

# 木を降りる探索を何回に1回やるか。
# 属性の走査は安いが、木を全部降りるのは 1文字ごとに走らせたくない。
WALK_EVERY = 20

# 見つからないまま何回過ぎたら警告するか（1回だけ出す）。
WARN_AFTER = 200

# 控えた設計値をラベル自身に持たせるときの名前。
# 注入をまたいで残る。
DESIGN_ATTR = "_instantale_line_height"


def apply(ctx):
    # `scripts.hud.new_hud` が未 import でも自分では降りない。
    # `ctx.wrap` が保留に積み、モジュールが現れた時点でローダが当て直す（TECH.md
    # §3.5）。
    # 設計値の控え（ラベルが捨てられたら一緒に消えるよう弱参照で持つ）。
    # 本命はラベル自身に付ける DESIGN_ATTR で、こちらはそれが書けなかったとき用。
    designs = weakref.WeakKeyDictionary()
    # 見つけた本文のラベル。
    # HUD ごとに覚える。
    # 画面を作り直されたときに古い
    # HUD のラベルを塗り続けないようにするため（親が居るかどうかだけでは「まだ生きているが別の画面のもの」を見分けられない）。
    labels = weakref.WeakKeyDictionary()
    state = {"tries": 0, "logged": 0, "warned": False, "pending": False}

    write = ctx.logger(LOG_BASENAME)

    # -- ラベルかどうか ------------------------------------------------------
    def is_label(widget):
        """本文を描けるウィジェットか。型では見ない（ゲーム側の派生クラスや
        別名の Label がありうる）。
        触る property が3つとも在ることを条件にする。"""
        for name in ("text", "line_height", "texture_update"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return isinstance(frames.attr(widget, "text"), str)

    def rank(text, value):
        """`value` との近さ。0 ＝ 一致しない、大きいほど確からしい。"""
        if not text:
            return 0
        if text == value:
            return 3
        # 一致しないものを「近い」と見なすのは、本文と同じくらい長いときだけ。
        # 選択肢のボタンも Label なので（`hud.buttons[i].text`）、
        # 短い文字列で「含まれる」を許すと、本文に出てくる語と同じボタンを掴みうる。
        if len(text) < MIN_MATCH:
            return 0
        if value.endswith(text) or text.endswith(value):
            return 2      # ゲームが末尾を足している／削っている場合
        if text in value or value in text:
            return 1
        return 0

    def best(hits):
        """候補から1つ選ぶ。近い順、同じ近さなら長い方（本文のラベル）。"""
        hits.sort(key=lambda hit: (hit[0], len(frames.attr(hit[2], "text", ""))),
                  reverse=True)
        return hits[0]

    # -- ラベルを探す --------------------------------------------------------
    def scan_attrs(hud, value):
        """`vars(hud)` から探す。見つかれば属性名も分かるので記録に残せる。"""
        try:
            items = list(vars(hud).items())
        except Exception:
            return None
        hits = []
        for name, widget in items:
            if not is_label(widget):
                continue
            score = rank(frames.attr(widget, "text", ""), value)
            if score:
                hits.append((score, "hud." + name, widget))
        return best(hits) if hits else None

    def scan_tree(hud, value):
        """ウィジェット木を降りて探す。ラベルが属性として持たれていない場合用。"""
        hits = []

        def descend(widget, depth, path):
            if depth and is_label(widget):
                score = rank(frames.attr(widget, "text", ""), value)
                if score:
                    hits.append((score, path, widget))
            if depth >= MAX_DEPTH:
                return
            children = frames.attr(widget, "children")
            if not isinstance(children, (list, tuple)):
                return
            # Kivy の children は後ろが先頭。
            # 見た目の並び順に直して降りる。
            for index, child in enumerate(reversed(list(children))):
                descend(child, depth + 1,
                        "{}/{}[{}]".format(path, type(child).__name__, index))

        try:
            descend(hud, 0, type(hud).__name__)
        except Exception:
            ctx.log_exc("text spacing: walking the widget tree failed")
            return None
        return best(hits) if hits else None

    def by_name(hud):
        """`hud.text_display` をそのまま引く。本文の中身を見ないので、
        本文を載せ替える MOD（`117_` など）が先に走っていても外さない。"""
        label = frames.attr(hud, LABEL_ATTR)
        if label in (None, frames.MISSING) or not is_label(label):
            return None
        return (4, "hud." + LABEL_ATTR, label)

    def find_label(hud, value):
        """本文のラベル。見つからなければ None。"""
        ref = labels.get(hud)
        label = ref() if ref is not None else None
        # 親が外れたラベル（画面を作り直された）は覚え直す。
        if label is not None and frames.attr(label, "parent") not in (None, frames.MISSING):
            return label

        state["tries"] += 1
        hit = by_name(hud)
        # 予備の探索は本文を手がかりにするので、
        # 短すぎる文字列では走らせない（選択肢のボタンなど、
        # 他のラベルとも一致しうる）。
        if hit is None and len(value) >= MIN_MATCH:
            hit = scan_attrs(hud, value)
            if hit is None and state["tries"] % WALK_EVERY == 1:
                hit = scan_tree(hud, value)
        if hit is None:
            if state["tries"] == WARN_AFTER and not state["warned"]:
                state["warned"] = True
                ctx.log("text spacing: hud.{} is not a label and no other label is "
                        "showing display_text; leaving the text as the game drew it"
                        .format(LABEL_ATTR), level="WARN")
            return None

        score, path, label = hit
        try:
            labels[hud] = weakref.ref(label)
        except TypeError:
            pass      # 弱参照を持てない相手なら、毎回探し直す（動作は変わらない）
        if state["logged"] < MAX_LOG:
            state["logged"] += 1
            write("label {} at {} (match={}) line_height={!r} design={!r} "
                  "font_size={!r} texture={!r} text_size={!r} len(text)={}".format(
                      type(label).__name__, path,
                      "name" if score == 4 else score,
                      frames.attr(label, "line_height"), design_of(label),
                      frames.attr(label, "font_size"),
                      frames.attr(label, "texture_size"),
                      frames.attr(label, "text_size"),
                      len(frames.attr(label, "text", ""))))
        return label

    # -- 設計値（ゲームが入れている行間） ------------------------------------
    def design_of(label):
        """このラベルの元の `line_height`。最初に見たときの値を控えて使う。

        控え先はラベル自身。
        MOD 側の変数だけに持つと、注入し直したときに「こちらが縮めた後の値」を設計値として控えてしまい、
        倍率が二重に掛かる。
        """
        for value in (frames.attr(label, DESIGN_ATTR), designs.get(label)):
            if isinstance(value, float):
                return value
        try:
            current = float(frames.attr(label, "line_height", 1.0))
        except (TypeError, ValueError):
            current = 1.0
        designs[label] = current
        try:
            setattr(label, DESIGN_ATTR, current)
        except Exception:
            pass      # 属性を足せないウィジェットなら弱参照の控えだけで動く
        return current

    # -- 本文の空行 ----------------------------------------------------------
    def collapse(text):
        """連続する改行を `BLANK_LINES` 行ぶんの空行までに詰める。"""
        if BLANK_LINES is None or not isinstance(text, str):
            return text
        keep = max(int(BLANK_LINES), 0) + 1          # 許す連続改行の数
        return re.sub(r"\n{%d,}" % (keep + 1), "\n" * keep, text)

    # -- 高さの決め直し ------------------------------------------------------
    def settle(hud, label):
        """行間と本文を変えた後で、ゲームに本文の高さを決め直させる。

        次のフレームに1回だけにする。
        本文は1文字ずつ増えるので（`InstantaleApp.add_text_display`）、
        その場で呼ぶと1文字ごとに仕事が1回増える。
        予約済みなら積まない。

        ここでやるのは高さの計算だけ。
        テクスチャの作り直しは Kivy に任せる（下の `run` を参照。
        ここを自分で呼んでいたのが表示が遅い原因だった）。
        """
        if state.get("pending"):
            return

        def run(_dt=None, hud=hud, label=label):
            state["pending"] = False
            try:
                # `texture_update()` は呼ばない。
                # 「ゲームは `texture_size` を見て高さを出すので、
                # 新しい行間で測り直させる」つもりで呼ぶと、**本文が1文字進むたびにラベルを余計に作り直す**ことになる。
                # 1回 15ms で、ティックの間隔の 3分の2 をここで食う（VERIFICATION_LOG.md §2.34。`112_` と `117_` の 2本を外して 1文字 42.6ms →
                # 8.1ms）。
                # 外しても困らない。
                # Kivy の作り直しはこの予約より先に走る（テキストを変えた時点で予約されるので、
                # こちらより早い）ので、高さを出す時点の
                # `texture_size` は新しい行間のものになっている。
                # 万一順序が入れ替わっても、遅れるのは1フレームぶんだけで、
                # 次の文字で必ず組み直される。
                update_height = frames.attr(hud, "update_label_height")
                if callable(update_height):
                    update_height()
            except Exception:
                # 高さのためにゲームを落とさない。
                # 次の文字で組み直される。
                ctx.log_exc("text spacing: could not settle the label height")

        try:
            from kivy.clock import Clock
        except Exception:
            run()                     # ゲームの外（オフライン検証）ではその場で
            return
        state["pending"] = True
        try:
            Clock.schedule_once(run, 0)
        except Exception:
            state["pending"] = False
            ctx.log_exc("text spacing: could not schedule the height update")

    # -- 当てる --------------------------------------------------------------
    def restyle(hud, value):
        label = find_label(hud, value)
        if label is None:
            return

        changed = False
        if LINE_SCALE is not None:
            wanted = design_of(label) * float(LINE_SCALE)
            try:
                current = float(frames.attr(label, "line_height", 1.0))
            except (TypeError, ValueError):
                current = None
            if current is None or abs(current - wanted) > 1e-6:
                label.line_height = wanted
                changed = True

        text = frames.attr(label, "text", "")
        tightened = collapse(text)
        if tightened != text:
            label.text = tightened
            changed = True

        if changed:
            settle(hud, label)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        # 先にゲームに塗らせる。
        # こちらは塗り終わった後の見た目だけを整えるので、
        # ここから先で壊れても safe=True が orig の結果を返せる。
        result = orig(self, instance, value, *args, **kwargs)
        if isinstance(value, str):
            restyle(self, value)
        return result

    ctx.log("text spacing: LINE_SCALE={!r} BLANK_LINES={!r}; measurements go to out/{}"
            .format(LINE_SCALE, BLANK_LINES, LOG_BASENAME))
