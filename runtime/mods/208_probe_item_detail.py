# -*- coding: utf-8 -*-
"""計測: アイテム説明欄（ItemDetailBox）の実寸と中身を写し取る（読み取り専用）。

症状（ユーザー報告・2026-07-27、`Pictures/Screenshots/inventory.png`）:
説明文が途中で切れる。`item_0` の説明に半角 test を 105 文字入れたのに、
画面には **24文字 × 3行 = 72文字** しか出ていない。名前・種別・攻撃力・売価は
出ているので、**箱の大きさが固定で、説明のラベルがそこに収まる分しか描かれない**
という読みになる。

直すには次の3つが要る。どれも推測せず実測で決める（`108_` と同じ立場 ―
寸法を発明すればレイアウトの仕様をこちらで勝手に決めることになる）:

1. **箱と中のラベルの持ち方** ― `ItemDetailBox` が何のレイアウトで、子が何個で、
   説明は専用のラベルなのか名前と同じラベルに繋げて入れているのか。
   `update_content` が毎回作り直すのか、既にあるラベルの `text` を差し替えるのか。
2. **なぜ72文字で止まるのか** ― 候補は3つあり、対策が別々になる。
   `text_size` の高さが固定（超過分は描かれない）／`max_lines` が 3 ／
   ラベル自体の `size` が固定でクリップされている。`texture_size` と `size` を
   並べて記録すれば、**文字が要求している高さ**と**与えられている高さ**の差で分かる。
3. **箱を伸ばしてよい余地** ― 箱は誰が位置を決めているのか（親、それとも
   マウス位置から直接）。画面のどこに出ていて、下や右にどれだけ空きがあるか。
   伸ばした結果が画面外にはみ出すなら、伸ばすだけでは足りない。

`update_content` はマウス移動（`InventoryItem.on_mouse_pos`）から呼ばれるので
**毎フレーム走りうる**。同じ内容を何度も書くとログが読めなくなるうえ描画も重く
なるので、`(item_id, 説明文の長さ)` が変わったときだけ1回書く。

この mod は**観測しかしない**。値は変えず、記録に失敗しても本体は必ず呼ぶ。
"""

import datetime
import sys

from instantale_modloader import frames

NAME = "probe: item detail box geometry (why the description is cut off)"

LOG_BASENAME = "item_detail.log"

# 書き出す組み合わせの上限。1つのアイテムにつき1件なので少しでいい。
MAX_SAMPLES = 40

# 子ウィジェットを何段まで潜るか。BoxLayout の中に BoxLayout があっても届く程度。
MAX_DEPTH = 4

# ラベルの折り返しに効く property。名前が違って空振りしても、
# 下の vars() ダンプが本当の属性名を教えてくれる。
WIDGET_ATTRS = ("size", "size_hint", "pos", "pos_hint", "height", "width",
                "padding", "spacing", "orientation", "opacity")
LABEL_ATTRS = ("font_size", "font_name", "text_size", "texture_size", "halign",
               "valign", "shorten", "shorten_from", "max_lines", "line_height",
               "markup", "strip", "bold", "color")

# `opacity` の上げ下げを何件まで残すか。所持品を開くたびに数件出るので、
# 「閉じたのに残った」1回分を含む前後が読めれば十分。
MAX_OPACITY_EVENTS = 200

# アイテム側から読む値。説明文の**本当の長さ**が要る（画面に出た分ではなく）。
ITEM_ATTRS = ("item_id", "text", "rarity", "width_slots", "height_slots")


def apply(ctx):
    new_hud = sys.modules.get("scripts.hud.new_hud")
    if new_hud is None:
        ctx.log("scripts.hud.new_hud not loaded; skipping", level="WARN")
        return

    log_path = ctx.out_path(LOG_BASENAME)
    seen = set()

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(text.rstrip("\n") + "\n")
        except Exception:
            # 記録のせいでゲームを落とさない。ここは常に握り潰す。
            ctx.log_exc("item detail probe: write failed")

    def props(widget, names):
        out = []
        for name in names:
            value = frames.attr(widget, name)
            if value is frames.MISSING:
                continue
            out.append("{}={}".format(name, frames.repr_value(value)))
        return out

    def describe(widget, depth):
        """1つのウィジェットを1行に。テキストは長さも一緒に出す。"""
        indent = "  " * (depth + 1)
        bits = ["{}#{:x}".format(type(widget).__name__, id(widget))]
        bits += props(widget, WIDGET_ATTRS)

        text = frames.attr(widget, "text")
        if text is not frames.MISSING:
            # 画面に出ている分ではなく、ラベルが持っている文字列そのもの。
            # これと texture_size を突き合わせれば「要求された高さ」が出る。
            bits.append("len(text)={}".format(
                len(text) if isinstance(text, str) else "?"))
            bits.append("text=" + frames.repr_value(text))
            bits += props(widget, LABEL_ATTRS)

        lines = [indent + " ".join(bits)]
        if depth < MAX_DEPTH:
            children = frames.attr(widget, "children")
            if isinstance(children, (list, tuple)):
                # Kivy の children は後ろが先頭。見た目の並び順に直して出す。
                for child in reversed(list(children)):
                    lines += describe(child, depth + 1)
        return lines

    def ancestry(widget):
        """箱の位置を誰が決めているのか。親を辿って寸法だけ並べる。"""
        lines, node, depth = [], frames.attr(widget, "parent"), 0
        while node not in (None, frames.MISSING) and depth < 5:
            lines.append("  parent[{}] {}".format(
                depth, " ".join(["{}#{:x}".format(type(node).__name__, id(node))]
                                + props(node, WIDGET_ATTRS))))
            node = frames.attr(node, "parent")
            depth += 1
        return lines or ["  parent[0] <none: the box is not in the tree>"]

    opacity_log = {"count": 0}

    def window_size():
        try:
            from kivy.core.window import Window
            return "{}x{}".format(Window.width, Window.height)
        except Exception:
            return "?"

    @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content")
    def update_content(orig, self, item, *args, **kwargs):
        result = orig(self, item, *args, **kwargs)
        # 箱はホバーのたびに作り直されるので、見張りもそのたびに掛け直す
        # （同じ箱には二度掛からないよう印を持たせてある）。
        watch_opacity(self)
        try:
            text = frames.attr(item, "text")
            key = (frames.attr(item, "item_id"),
                   len(text) if isinstance(text, str) else -1)
            # マウスが乗っている間ずっと呼ばれる。中身が変わったときだけ書く。
            if key in seen or len(seen) >= MAX_SAMPLES:
                return result
            seen.add(key)

            lines = ["", "=" * 72,
                     "[{}] update_content  window={}".format(
                         datetime.datetime.now().isoformat(timespec="milliseconds"),
                         window_size()),
                     "  item: " + "  ".join(
                         "{}={}".format(name, frames.repr_value(frames.attr(item, name)))
                         for name in ITEM_ATTRS)]

            # 属性名は推測しない。box が自分で持っているものを全部出す
            # （どれが説明のラベルなのかは、ここに出る名前で決める）。
            try:
                own = vars(self)
            except Exception:
                own = {}
            lines.append("  vars(box): " + ", ".join(sorted(own)) or "  vars(box): <none>")
            for name in sorted(own):
                lines.append("    {:<28} = {}".format(name, frames.repr_value(own[name])))

            lines.append("  tree:")
            lines += describe(self, 0)
            lines.append("  ancestry:")
            lines += ancestry(self)
            write("\n".join(lines))
        except Exception:
            ctx.log_exc("item detail probe: dump failed")
        return result

    # ------------------------------------------------------------------
    # 説明欄が閉じたはずなのに残る（ユーザー報告・2026-07-28、
    # `Pictures/Screenshots/floating item.bmp`）。所持品を閉じているのに、
    # 戦闘画面の上に説明の箱が浮いたままになっていた。
    #
    # **表示・非表示は `opacity` で行われている**（上のダンプで確定 ―
    # `update_content` の時点で箱は `opacity=0`、子は 1）。つまり症状は
    # 「1 に上げた誰かが 0 に戻していない」。誰が上げ下げしているのかは
    # コンパイル済みで読めないので、**プロパティの変化そのものを見張る**。
    #
    # 読み取り専用。値は一切変えない。`opacity` は Kivy の property なので
    # `bind` で変化を拾える（`109_` は size/pos しか触らないので、この計測に
    # `109_` の書き込みが混ざることは無い）。
    # ------------------------------------------------------------------
    def watch_opacity(box):
        if getattr(box, "_mod_opacity_watched", False):
            return
        try:
            setattr(box, "_mod_opacity_watched", True)
        except Exception:
            return

        def on_opacity(instance, value):
            if opacity_log["count"] >= MAX_OPACITY_EVENTS:
                return
            opacity_log["count"] += 1
            try:
                # 呼び出し元は段数で数えない（`runtime/` 配下を飛ばす）。
                caller = frames.caller()
                parent = frames.attr(instance, "parent")
                write("[{}] opacity -> {} | box={} pos={} size={} parent={} | from {}"
                      .format(
                          datetime.datetime.now().isoformat(timespec="milliseconds"),
                          value, frames.repr_value(instance),
                          frames.repr_value(frames.attr(instance, "pos")),
                          frames.repr_value(frames.attr(instance, "size")),
                          frames.repr_value(parent), caller))
            except Exception:
                ctx.log_exc("item detail probe: opacity record failed")

        try:
            box.bind(opacity=on_opacity)
        except Exception:
            ctx.log_exc("item detail probe: cannot watch opacity")

    ctx.log("watching ItemDetailBox.update_content; geometry goes to out/{}".format(
        LOG_BASENAME))
