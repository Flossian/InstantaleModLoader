# -*- coding: utf-8 -*-
"""プレイヤーの人物欄を広げ、手配度・スキル・特性を載せる。

素の人物欄は 810x497（窓 1920x1000 のとき）で、右半分は能力値と空のままの箱、
左半分は出自と健康状態だけ。
キャラクタが持っている手配度・スキル・特性はどこにも出ていない。

やっていることは2つ。

1. 枠を広げる。
   `hud.character_sheet_layout` の四辺を窓に対する割合で置き直す（`PANEL_LEFT` ほか）。
   立ち絵（`character_image_right`）はこのレイアウトの外なので動かない。
   広げるぶんは左・上・下へ出す（右は立ち絵に被る）。
2. 中身を並べ直し、3つ足す。
   手配度・スキル・特性。
   特性はゲームが作って一度も使っていない箱（`character_sheet_empty`）を借り、
   手配度とスキルは Label を2枚足す。
   何を出すかは `sheet.py`。

### なぜ開くたびに書くのか

人物欄はゲーム自身が開くたびに `size` を入れ直す（閉じるときは 0 にする）。
一度きり書いても次に開いた時点で元の寸法へ戻るので、開いた直後に毎回当てる。
窓の大きさが変わったときも同じ理由で当て直す（`watch_window`）。
子は全て `size_hint` / `pos_hint` で置かれているから、親を広げれば付いてくるし、
閉じれば一緒に消える。
足した箱の出し入れをこちらで管理する必要は無い。

### なぜ倍率ではなく割合なのか

最初は「ゲームが決めた寸法の 1.4 倍」で書いたが、
これは**窓を小さくすると下の情報欄や本文に食い込む**。
ゲームの各欄は窓に対する割合で置かれているので、こちらも同じ土俵に乗せる。
四辺を割合で決めれば、どの大きさでも他の欄との位置関係が変わらない。

### 決めつけていないこと

- 書体を発明しない。
  足した Label の書体・文字の大きさ・色・余白は、
  隣の `character_sheet_health_condition` から写す。
  日本語はゲームが読み込んだ書体でないと出ないし、画面の拡縮もゲームの値に付いてくる
- 枠線を自分で引かない。
  `scripts.hud.new_hud:add_border(widget)` を借りる（色も太さも寸法追従もゲームのものになる。
  GAME.md §2.8）
- **誰の人物欄かを決めつけない**。
  開いている相手は `visible_character_sheet_data` の名前で引き当てる。
  プレイヤー以外が出ている場合にプレイヤーの手配度を載せてしまわないようにするため

外したときに枠の寸法は戻らない（ゲームを起動し直せば戻る）。
TECH.md §3.10 の「完全に元通りにはならないもの」に当たる。
"""

import os
import sys

from instantale_modloader import frames, ui
from instantale_modloader.state import world_filename, world_key

from . import sheet

# ---------------------------------------------------------------- 設定
#: 枠の四辺。
#: 窓に対する割合で持ち、四隅を直に決める（左上が原点。`PANEL_TOP` は上から測る）。
#: 倍率ではなく割合にしてあるのは、
#: 窓の大きさが変わっても他の欄との位置関係が崩れないようにするため。
#: 倍率で持つと、小さい窓では下の情報欄や本文に食い込む。
#: 既定値は実測に基づく（窓 1920x1000）。
#: 右端 0.645 は立ち絵の手前で、下端 0.65 は画面下の情報欄（上端 0.69）の手前。
#: 上端 0.03 は素の位置（0.055 相当）より上。
#: 能力値の箱が書体によってはぎりぎりだったので、上へ広げて縦の余裕に充てている。
#: 左端 0.02 は画面下のステータス欄（Atk/Def の箱）の左端。
#: 箱は `size_hint 0.2, center_x 0.12` で置かれていて、左端は 0.12 - 0.2/2 = 0.02
#: （212 のプローブで実測。`out/character_sheet.log` の `status_label^1`）。
#: 広げたぶんは手配度の箱の幅に充てている（`sheet.BOXES`）。
PANEL_LEFT = 0.02
PANEL_RIGHT = 0.645
PANEL_TOP = 0.03
PANEL_BOTTOM = 0.65

#: 手配度に出す行数の上限。
#: 箱に入り切らない分は描かれないだけなので、数だけの上限をここで持つ。
MAX_WANTED_LINES = 24

# ---------------------------------------------------------------- 実装
#: 足したウィジェットの目印（`ui.added_by_a_mod` が見る接頭辞に合わせる）。
MARK = ui.MOD_WIDGET_PREFIX + "character_sheet"

HUD_MODULE = "scripts.hud.new_hud"


def apply(ctx):
    state = {"reported": False, "hud": None}

    def hud_module():
        return sys.modules.get(HUD_MODULE)

    # ------------------------------------------------------------ 寸法
    def window_size():
        """窓の実寸。取れなければ `None`（画面の無い環境では何もしない）。"""
        try:
            from kivy.core.window import Window
            return float(Window.width), float(Window.height)
        except Exception:
            return None

    def is_open(layout):
        """人物欄が開いているか。閉じているときは触らない。

        閉じるときゲームは寸法に 0 を入れる（実測）。
        こちらが入れた寸法は常に 0 より大きいので、
        0 なら「たった今ゲームが閉じた」で確定する。
        """
        size = frames.attr(layout, "size")
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            return False
        try:
            return float(size[0]) > 0 and float(size[1]) > 0
        except (TypeError, ValueError):
            return False

    def widen(layout):
        """枠を窓に対する割合で置き直す。何度呼んでも同じ結果になる。

        ゲームが入れた寸法を基準にしない。
        倍率で持つと、窓が小さいときに下の情報欄や本文へ食い込む。
        四辺を窓の割合で決めれば、リサイズしても他の欄との位置関係が変わらない。
        """
        if not is_open(layout):
            return False
        window = window_size()
        if window is None:
            return False
        width, height = window
        layout.size = ((PANEL_RIGHT - PANEL_LEFT) * width,
                       (PANEL_BOTTOM - PANEL_TOP) * height)
        layout.pos_hint = {
            "center_x": (PANEL_LEFT + PANEL_RIGHT) / 2.0,
            # Kivy の縦は下が原点。
            # 設定は上から測っているのでひっくり返す。
            "center_y": 1.0 - (PANEL_TOP + PANEL_BOTTOM) / 2.0,
        }
        return True

    def place(widget, spec):
        size_hint, pos_hint = spec
        widget.size_hint = size_hint
        widget.pos_hint = dict(pos_hint)

    # ------------------------------------------------------------ 足す箱
    #: 手本から写す見た目。
    #: 文字の大きさは窓の大きさでゲームが変えるので、
    #: 作るときだけでなく開くたびに写し直す。
    STYLE_ATTRS = ("font_name", "font_size", "color", "padding", "line_height",
                   "max_lines", "shorten")

    def copy_style(label, style):
        """手本の Label から見た目を写し、左上詰めにする。写せない項目は飛ばす。

        書体を写すのは、
        日本語がゲームの読み込んだ書体でしか出ないため（既定の書体では豆腐になる）。

        揃え方だけは手本から写さずここで決める。
        項目が縦に並ぶ欄なので、中央寄せだと1件のときに宙に浮き、
        行が増えるたびに全部動く。
        余白も手本のもの（左右 11）に揃える。
        能力値の箱は左右 46 の余白を持っていて、
        そのまま使うと文字が中央寄せに見える。
        """
        if style is None:
            return
        for name in STYLE_ATTRS:
            value = frames.attr(style, name)
            if value is frames.MISSING:
                continue
            try:
                setattr(label, name, value)
            except Exception:
                pass        # 写せない項目は既定のままでよい
        for name, value in (("halign", "left"), ("valign", "top")):
            try:
                setattr(label, name, value)
            except Exception:
                pass

    def make_label(style):
        """手本から見た目を写した空の Label。"""
        from kivy.uix.label import Label

        label = Label(text="", halign="left", valign="top", markup=False)
        copy_style(label, style)
        # 折り返しは箱の幅に従わせる。
        # `text_size` を寸法に束ねておかないと、
        # 1行が箱を突き抜ける（ゲーム自身の Label も同じ形になっている）。
        label.bind(size=lambda instance, value: setattr(instance, "text_size", value))
        label.text_size = label.size
        setattr(label, MARK, True)
        return label

    def add_border(widget):
        """枠線はゲームの描き方を借りる。引けなければ枠線なしで進む。"""
        module = hud_module()
        for name in ("add_border", "add_border_before"):
            painter = getattr(module, name, None) if module is not None else None
            if not callable(painter):
                continue
            try:
                painter(widget)
                return
            except Exception:
                ctx.log_exc("character sheet: the game's {} failed".format(name))

    def ensure_boxes(layout, style):
        """足した箱を1度だけ作る。同じ親に二度足さない。"""
        boxes = getattr(layout, MARK + "_boxes", None)
        if isinstance(boxes, dict) and all(
                frames.attr(box, "parent") is layout for box in boxes.values()):
            return boxes
        boxes = {}
        for name in sheet.ADDED:
            label = make_label(style)
            place(label, sheet.BOXES[name])
            layout.add_widget(label)
            add_border(label)
            boxes[name] = label
        setattr(layout, MARK + "_boxes", boxes)
        return boxes

    def ensure_reroll(layout):
        """二つ名の引き直しボタンを1度だけ作る。作れない環境では None。

        押すと評判 MOD への頼みのファイルを書く（`request_reroll`）。
        絵柄は円弧＋矢尻。文字ではないので書体は要らない。
        """
        button = getattr(layout, MARK + "_reroll", None)
        if button is not None and frames.attr(button, "parent") is layout:
            return button
        # 大きさは正方形の固定値（ゲームの upx で拡縮）。
        # `place()` で箱の割合を与えない ― 割合だと窓の形に引きずられて横長に潰れ、
        # 絵柄（円弧）が楕円になる（実機で確認）。
        button = ui.make_icon_button(size=24.0,
                                     pos_hint=dict(sheet.REROLL_POS))
        if button is None:
            return None       # kivy Button の無い環境（オフライン検証）ではボタンなし
        setattr(button, MARK, True)
        button.bind(on_release=lambda *_args: request_reroll(button))

        def repaint(*_args):
            ui.paint_icon(button, sheet.REROLL_STROKES, attr=MARK + "_icon",
                          key=("reroll",), log_exc=ctx.log_exc)

        button.bind(pos=repaint, size=repaint)
        repaint()
        layout.add_widget(button)
        setattr(layout, MARK + "_reroll", button)
        return button

    def request_reroll(button):
        """二つ名の引き直しを評判 MOD に頼む。

        MOD どうしは呼び合わない（TECH.md §3.2.3）ので、頼みはファイルで渡す。
        評判 MOD は次の照合（移動・日送り・人物欄の開閉）でファイルを消し、
        いまの名を除いて編み直す。押した合図としてボタンは隠す
        （次に人物欄を開いたとき、名が変わっていればまた出る）。
        """
        app = ui.find_app()
        if app is None:
            return
        path = os.path.join(ctx.state_dir, sheet.REPUTATION_DIRNAME,
                            world_filename(world_key(app), sheet.REROLL_SUFFIX))
        if ctx.write_json(path, {"reroll": True}):
            ctx.log("character sheet: 二つ名の引き直しを頼んだ")
        ui.show_widget(button, False)

    # ------------------------------------------------------------ 中身
    def sheet_character(app, hud):
        """人物欄に出ている相手。プレイヤーと決めつけない。

        名前で引き当てる。
        合う相手が居なければプレイヤーを返す（素の人物欄はプレイヤーのもの）。
        """
        player = frames.attr(app, "player")
        player = None if player is frames.MISSING else player
        data = frames.attr(hud, "visible_character_sheet_data")
        name = data.get("name") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name.strip():
            return player
        if getattr(player, "name", None) == name:
            return player
        world = frames.attr(app, "world")
        characters = frames.attr(world, "characters")
        if isinstance(characters, dict):
            for character in characters.values():
                if getattr(character, "name", None) == name:
                    return character
        return player

    def wanted_text(app, character):
        areas = ui.world_areas(app)
        world = frames.attr(app, "world")
        holders = [app] if world in (frames.MISSING, None) else [app, world]
        raw = sheet.raw_areas(holders)
        sizes = sheet.area_sizes(areas, raw)
        entries = sheet.wanted_entries(
            areas, sizes, frames.attr(character, "area_history"),
            max_lines=MAX_WANTED_LINES)
        if not state["reported"]:
            # どこから規模を読めたのかは、後で食い違ったときに最初に見る所。
            state["reported"] = True
            ctx.log("character sheet: {} area(s), size from {} ({} shown)".format(
                len(areas or {}), "the save's area table" if raw else "the bgm path",
                len(entries)))
        return sheet.wanted_text(entries)

    def epithet_text(app, character):
        """世界の二つ名。評判 MOD の控えがあれば出す。無ければ空。

        MOD どうしは import しない（TECH.md §3.2.3）。控えのファイルを読む。
        置き場所は `os.path.join` で組む。`ctx.state_path()` は親フォルダを作るので、
        評判 MOD を使っていない `state/` に空のフォルダを置いてしまう（TECH.md §3.11）。
        二つ名はプレイヤーのものなので、別人の人物欄には出さない。
        """
        player = frames.attr(app, "player")
        if player is frames.MISSING or player is None or character is not player:
            return ""
        path = os.path.join(ctx.state_dir, sheet.REPUTATION_DIRNAME,
                            world_filename(world_key(app)))
        return sheet.epithet_text(
            sheet.reputation_epithet(ctx.read_json(path, None)))

    def write_epithet(hud, epithet):
        """レベル行の続きに二つ名を書く。無ければ素の行に戻す。

        ゲームはこの欄を開くたびに書き直すが、書き直さないまま
        こちらの塗りだけが2度走ることもある（窓の大きさの変更など）。
        前に足したぶんが残っていたら剥がしてから足す ＝ 何度呼んでも二重にならない。
        """
        info = frames.attr(hud, "character_sheet_basic_info")
        if info is frames.MISSING or info is None:
            return
        base = frames.text_of(info, "text")
        written = getattr(info, MARK + "_written", None)
        if isinstance(written, dict) and base == written.get("full"):
            base = written.get("base", base)
        full = base + "　" + epithet if epithet else base
        try:
            info.text = full
        except Exception:
            return
        setattr(info, MARK + "_written", {"base": base, "full": full})

    def paint(app, hud, boxes, reroll=None):
        character = sheet_character(app, hud)
        if character is None:
            return
        boxes["wanted"].text = wanted_text(app, character)
        epithet = epithet_text(app, character)
        write_epithet(hud, epithet)
        if reroll is not None:
            # 二つ名が出ているときだけ押せる（無い名は引き直せない）。
            ui.show_widget(reroll, bool(epithet))
        boxes["skills"].text = sheet.list_text(
            sheet.SKILLS_HEADING, sheet.names_of(frames.attr(character, "skills")))
        # 既定を None にして「無い」も「None が入っている」も1つの判定で弾く。
        # 番人だけを見ていた版は、ゲームが箱を作る前（`character_sheet_empty` が None）に
        # `None.text = ...` を踏んでいた。
        # `safe=True` がゲームを守るので落ちはしないが、
        # そこで降りるためこの後の塗りが全部飛ぶ。
        # 同じ関数の他の2箇所は最初から両方見ている（下の `place` / `copy_style`）。
        traits_box = frames.attr(hud, sheet.TRAITS_BOX, None)
        if traits_box is not None:
            traits_box.text = sheet.list_text(
                sheet.TRAITS_HEADING, sheet.names_of(frames.attr(character, "traits")))

    # ------------------------------------------------------------ 入口
    def decorate(hud, repaint=True):
        """開いている人物欄を整える。開いていなければ何もしない。

        `repaint=False` は窓の大きさが変わったとき用（寸法と書体だけ合わせ、
        文字は前のまま）。
        リサイズ中は何度も呼ばれるので、手配度の集計まで毎回やり直さない。
        """
        layout = frames.attr(hud, "character_sheet_layout")
        if layout is frames.MISSING or layout is None:
            return
        state["hud"] = hud      # 窓の大きさが変わったときの当て直し先
        if not widen(layout):
            return          # 閉じたところ。中身は親と一緒に消えるので何もしない

        style = frames.attr(hud, sheet.STYLE_SOURCE)
        if style is frames.MISSING:
            style = None
        boxes = ensure_boxes(layout, style)
        reroll = ensure_reroll(layout)

        # ゲーム自身の箱も置き直す。
        # 広げた枠の中で列に並べ替える。
        for name, spec in sheet.BOXES.items():
            if name in boxes:
                continue
            widget = frames.attr(hud, name)
            if widget is not frames.MISSING and widget is not None:
                place(widget, spec)

        # 文字の大きさは窓に合わせてゲームが変える。
        # こちらが中身を書く箱には毎回写し直す。
        # 借りている特性の箱と能力値の箱も同じ扱い（元は中央寄せ・大きな余白なので、
        # そのままでは中央に寄って見え、書体によっては右端が欠ける）。
        for box in (list(boxes.values())
                    + [frames.attr(hud, name) for name in sheet.RESTYLED]):
            if box is not frames.MISSING and box is not None:
                copy_style(box, style)

        # レベル行（basic_info）は素のゲームでは `text_size` を持たず、
        # 文字の塊が箱の中央に置かれる（`halign` は効いていない。212 の実測）。
        # 素の短い行では左に居るように見えるだけで、二つ名を続けて書いて
        # 箱を広げると中央へ寄ってしまう。寸法に束ねて `halign`（ゲーム自身の
        # left）を効かせる。束ねは1度だけ（開くたびに重ねない）。
        info = frames.attr(hud, "character_sheet_basic_info")
        if info is not frames.MISSING and info is not None:
            if not getattr(info, MARK + "_wrap", False):
                try:
                    info.bind(size=lambda instance, value: setattr(
                        instance, "text_size", value))
                    setattr(info, MARK + "_wrap", True)
                except Exception:
                    pass
            try:
                info.text_size = info.size
            except Exception:
                pass

        if not repaint:
            return
        app = ui.find_app()
        if app is not None:
            paint(app, hud, boxes, reroll)

    def watch_window():
        """窓の大きさが変わったら置き直す。掛け直しても重ならない。

        ゲーム自身も窓の大きさに合わせて人物欄の寸法を入れ直すので、
        こちらが後から当て直さないと、リサイズした瞬間だけ素の寸法に戻る。
        束ねた相手は Window に控えておき、注入し直したときは古いものを外してから掛ける（`apply()` は1プロセスで何度も走る。
        §3.4）。
        """
        try:
            from kivy.core.window import Window
        except Exception:
            return          # 画面の無い環境（オフライン検証）
        previous = getattr(Window, MARK + "_resize", None)
        if previous is not None:
            try:
                Window.unbind(size=previous)
            except Exception:
                ctx.log_exc("character sheet: could not unbind the old watcher")

        def on_resize(_instance, _value):
            try:
                # 直前に整えた HUD を覚えておく。
                # 人物欄を一度も開いていない間は探しに行く（探せなくても、
                # 次に開いたときに整う）。
                hud = state.get("hud") or ui.find_hud(ui.find_app())
                if hud is not None:
                    decorate(hud, repaint=False)
            except Exception:
                ctx.log_exc("character sheet: resize failed")

        try:
            Window.bind(size=on_resize)
            setattr(Window, MARK + "_resize", on_resize)
        except Exception:
            ctx.log_exc("character sheet: cannot watch the window size")

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility",
              safe=True)
    def toggle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        decorate(self)      # 壊れてもゲームは orig の結果を受け取る（§3.1.5）
        return result

    watch_window()
    ctx.log("character sheet: panel at ({}, {})-({}, {}) of the window"
            .format(PANEL_LEFT, PANEL_TOP, PANEL_RIGHT, PANEL_BOTTOM))
