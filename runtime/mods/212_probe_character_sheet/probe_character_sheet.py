# -*- coding: utf-8 -*-
"""計測: プレイヤーの人物欄（プロフィール画面）を写し取る（読み取り専用）。

人物欄を広げて手配度・スキル・特性を載せたい。そのために先に決めておくことが
3つあり、どれも推測してはいけない。

1. **画面の組み立て**: 人物欄は誰が持っているのか（`InstanTaleHUD` の属性か、
   開くたびに作られるのか）。枠・立ち絵・出自・健康状態がそれぞれどのウィジェットで、
   `size_hint` / `pos_hint` のどちらで置かれているのか。立ち絵の位置を動かさずに
   左の欄だけ広げられるかは、この形で決まる。
2. **文字の入り方**: 出自や健康状態は `text` の差し替えなのか、開くたびに作り直しなのか。
   `update_status_texts` / `toggle_character_sheet_window` のどちらが中身を入れているのか。
   作り直しなら、足したウィジェットは毎回消える前提で書く必要がある。
3. **載せる値の在り処**: 手配度は `player.area_history[エリアid]['lawfulness']`
   （`309_office_pardon` が実セーブで確認済み）。**エリアの名前と `size`** も要る
   （`size` が `dungeon` のエリアは出さない）。スキルと特性は `player.skills` /
   `player.traits` に何の形で入っているのか（文字列か、辞書か、オブジェクトか）。

この mod は**観測しかしない**。値は変えず、記録に失敗しても本体は必ず呼ぶ。
"""

import datetime
import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "character_sheet.log"

# 何回ぶん書き出すか。開閉のたびに1件出るので、開く→閉じるを数回で足りる。
MAX_DUMPS = 6

# ウィジェットを何段まで潜るか。人物欄は箱の入れ子が深いので item 欄より多く取る。
MAX_DEPTH = 8

WIDGET_ATTRS = ("size", "size_hint", "pos", "pos_hint", "height", "width",
                "padding", "spacing", "orientation", "opacity", "cols", "rows")
LABEL_ATTRS = ("font_size", "text_size", "texture_size", "halign", "valign",
               "max_lines", "line_height", "markup", "color")

# hud / app の属性のうち、人物欄に関係しそうな名前。**推測で1つに絞らない**
# ので、引っ掛かった名前を全部出して本物をこちらで選ぶ。
NAME_HINTS = ("sheet", "profile", "status", "character", "player", "portrait")

# プレイヤーから読む値。形が分からないので repr_value に任せる。
PLAYER_ATTRS = ("name", "age", "gender", "experience_level", "skills", "traits",
                "ability_scores", "original_ability_scores", "profile", "state",
                "status", "area_history", "current_area", "story_achievements")


def apply(ctx):
    new_hud = sys.modules.get("scripts.hud.new_hud")
    if new_hud is None:
        ctx.log("scripts.hud.new_hud not loaded; skipping", level="WARN")
        return

    state = {"dumps": 0}

    write = ctx.logger(LOG_BASENAME, stamp=False)

    def props(widget, names):
        out = []
        for name in names:
            value = frames.attr(widget, name)
            if value is frames.MISSING:
                continue
            if name in ("pos_hint", "size_hint", "pos", "size"):
                # 置き方そのものを写す欄なので、要約ではなく生の値を出す
                # （`pos_hint` はキーだけ出ても寸法が決められない）。
                out.append("{}={}".format(name, _plain(value)))
                continue
            out.append("{}={}".format(name, frames.repr_value(value)))
        return out

    def _plain(value):
        """Kivy の Observable* を素の repr にする。長さは高々数個なので切らない。"""
        try:
            if isinstance(value, dict):
                return repr({k: v for k, v in value.items()})
            return repr(list(value))
        except Exception:
            return frames.repr_value(value)

    def canvas_of(widget):
        """枠線や背景が canvas に描かれているかどうか（寸法追従の可否）。"""
        lines = []
        for name in ("canvas.before", "canvas", "canvas.after"):
            holder = frames.attr(widget, name.split(".")[0])
            if name.endswith("before"):
                holder = frames.attr(holder, "before")
            elif name.endswith("after"):
                holder = frames.attr(holder, "after")
            children = frames.attr(holder, "children")
            if not isinstance(children, (list, tuple)) or not children:
                continue
            lines.append("    {:<14} = {}".format(
                name, ", ".join(type(c).__name__ for c in children[:12])))
        return lines or ["    <no canvas instructions>"]

    def describe(widget, depth, label=""):
        indent = "  " * (depth + 1)
        head = "{}{}#{:x}".format(label and label + ": ",
                                  type(widget).__name__, id(widget))
        bits = [head] + props(widget, WIDGET_ATTRS)

        text = frames.attr(widget, "text")
        if text is not frames.MISSING:
            bits.append("text=" + frames.repr_value(text))
            bits += props(widget, LABEL_ATTRS)
        source = frames.attr(widget, "source")
        if source is not frames.MISSING:
            bits.append("source=" + frames.repr_value(source))

        lines = [indent + " ".join(bits)]
        if depth < MAX_DEPTH:
            children = frames.attr(widget, "children")
            if isinstance(children, (list, tuple)):
                # Kivy の children は後ろが先頭。見た目の並び順に直して出す。
                for child in reversed(list(children)):
                    lines += describe(child, depth + 1)
        return lines

    def hinted(obj, what):
        """名前に手掛かりを含む属性を並べる。本物はこの一覧から人が選ぶ。"""
        try:
            own = dict(vars(obj))
        except Exception:
            return ["  {}: <no __dict__>".format(what)]
        names = sorted(n for n in own
                       if any(h in n.lower() for h in NAME_HINTS))
        if not names:
            return ["  {}: <no hinted names>  all={}".format(what, sorted(own))]
        lines = ["  {} ({} hinted of {}):".format(what, len(names), len(own))]
        for name in names:
            lines.append("    {:<34} = {}".format(name, frames.repr_value(own[name])))
        return lines

    def player_values(app):
        """載せたい値の在り処。手配度はエリアごとなのでエリア表と突き合わせる。"""
        lines = ["  player:"]
        player = frames.attr(app, "player")
        if player in (None, frames.MISSING):
            return lines + ["    <no player>"]
        for name in PLAYER_ATTRS:
            lines.append("    {:<26} = {}".format(
                name, frames.repr_value(frames.attr(player, name))))

        # スキルと特性は「1件が何の形か」が要る。中身を1件だけ開いて見る。
        for name in ("skills", "traits"):
            value = frames.attr(player, name)
            items = None
            if isinstance(value, dict):
                items = list(value.items())[:3]
            elif isinstance(value, (list, tuple)):
                items = list(enumerate(value))[:3]
            if not items:
                continue
            for key, item in items:
                own = None
                try:
                    own = dict(vars(item))
                except Exception:
                    pass
                lines.append("    {}[{!r}] = {}".format(
                    name, key, frames.repr_value(item)))
                if own:
                    lines.append("      vars: " + ", ".join(
                        "{}={}".format(k, frames.repr_value(v))
                        for k, v in sorted(own.items())))

        # 手配度の対象は「size が dungeon 以外」のエリア。ところが実行中の
        # Area には `size` が無かった（`getattr` が `<missing>`）。セーブの
        # `areas[id]["size"]` に当たるものが実行時に何という名前なのかを、
        # **推測せず**エリアの持ち物を全部並べて決める。
        lines.append("  areas: vars() of the first few")
        try:
            for area_id, area in list((ui.world_areas(app) or {}).items())[:3]:
                own = {}
                try:
                    own = dict(vars(area))
                except Exception:
                    pass
                lines.append("    area[{!r}] {}".format(area_id, type(area).__name__))
                for key in sorted(own):
                    lines.append("      {:<24} = {}".format(
                        key, frames.repr_value(own[key])))
                # 辞書のように引けるビルドもありうる。両方試して結果を残す。
                try:
                    lines.append("      area['size'] -> {!r}".format(area["size"]))
                except Exception as exc:
                    lines.append("      area['size'] -> {}".format(type(exc).__name__))
        except Exception:
            ctx.log_exc("character sheet probe: area dump failed")

        # 実行中の Area は `size` を持っていなかった（上のダンプで確定）。
        # セーブでは `areas[id]["size"]` に在るので、**生の辞書がどこかに
        # 残っていないか**を app / world の持ち物から探す。見つからなければ
        # bgm のパス（`musics/<size>/<mood>/曲`）から逆算するしかない。
        lines.append("  where is the raw world dict?")
        world = frames.attr(app, "world")
        for holder, what in ((app, "app"), (world, "world")):
            try:
                own = dict(vars(holder))
            except Exception:
                lines.append("    vars({}): <unreadable>".format(what))
                continue
            lines.append("    vars({}) = {}".format(what, sorted(own)))
            for key, value in sorted(own.items()):
                if not isinstance(value, dict) or not value:
                    continue
                sample = next(iter(value.values()))
                if isinstance(sample, dict) and "size" in sample:
                    lines.append("      {}.{} looks like the raw areas: [{!r}] "
                                 "size={!r} name={!r}".format(
                                     what, key, next(iter(value)),
                                     sample.get("size"), sample.get("name")))

        lines.append("  areas (id / size / name / bgm / lawfulness):")
        try:
            areas = ui.world_areas(app)
        except Exception:
            areas = None
        history = frames.attr(player, "area_history")
        if isinstance(areas, dict):
            for area_id, area in list(areas.items())[:40]:
                entry = None
                if isinstance(history, dict):
                    entry = history.get(area_id, history.get(str(area_id)))
                lawful = entry.get("lawfulness") if isinstance(entry, dict) else None
                lines.append("    {:<5} size={:<10} name={:<24} law={!s:<6} bgm={}".format(
                    str(area_id),
                    str(frames.attr(area, "size")),
                    str(frames.attr(area, "name"))[:24],
                    lawful,
                    str(frames.attr(area, "bgm"))[-70:]))
        else:
            lines.append("    <areas not a dict: {}>".format(frames.repr_value(areas)))
        return lines

    def dump(tag, hud):
        if state["dumps"] >= MAX_DUMPS:
            return
        state["dumps"] += 1
        app = ui.find_app()
        lines = ["", "=" * 78,
                 "[{}] {}  dump #{}".format(
                     datetime.datetime.now().isoformat(timespec="milliseconds"),
                     tag, state["dumps"])]
        try:
            from kivy.core.window import Window
            lines.append("  window: {}x{}".format(Window.width, Window.height))
        except Exception:
            pass

        if hud is not None:
            lines.append("  visible_character_sheet_data = " + frames.repr_value(
                frames.attr(hud, "visible_character_sheet_data")))
            # 人物欄の根と、動かしてはいけない立ち絵。名前は dump #1 で確定済み。
            for name in ("character_sheet_layout", "character_image",
                         "character_image_right"):
                widget = frames.attr(hud, name)
                if widget is frames.MISSING:
                    continue
                lines.append("  {}:".format(name))
                lines += describe(widget, 0, label=name)
                lines += canvas_of(widget)
        if app is not None and state["dumps"] == 1:
            # 値の在り処は1回取れば足りる。開閉のたびに繰り返さない。
            lines += player_values(app)
        write("\n".join(lines))

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility",
              safe=True)
    def toggle_visibility(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            # 直後の `pos` はまだ1つ前のレイアウトの値（`size` だけ新しい）。
            # 落ち着いた寸法が要るので、次のフレームでもう1回写す。
            dump("toggle_character_sheet_visibility", self)
            from kivy.clock import Clock
            Clock.schedule_once(
                lambda _dt: dump("settled (1 frame later)", self), 0)
        except Exception:
            ctx.log_exc("character sheet probe: dump failed")
        return result

    @ctx.wrap("__main__:InstantaleApp.toggle_character_sheet_window", safe=True)
    def toggle_window(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            dump("toggle_character_sheet_window", ui.find_hud(self))
        except Exception:
            ctx.log_exc("character sheet probe: dump failed")
        return result

    ctx.log("watching the character sheet; geometry goes to out/{}".format(
        LOG_BASENAME))
