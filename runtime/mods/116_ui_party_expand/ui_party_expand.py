# -*- coding: utf-8 -*-
"""追加: パーティ欄を広げて、4人目以降の仲間を画面に出す。

## 何が困っているか

仲間を3人雇うと4人パーティになるが、画面右のパーティ欄には3人までしか出ない。
名簿から漏れているわけではなく（会話も戦闘も4人目を含めて進む）、**枠が3つしか作られていない**。
実測（`instantale.exe` の定数表、main_023）:

    bottom_info_layout        size_hint=(0.2, 0.88) pos_hint={center_x:0.88, y:0.1}
      party_cells             range(0, 3)  ← ここが 3 で固定されている
        ClickableFloatLayout  size_hint=(1, 0.33) / member_id を持つ
          StencilFloatLayout
            Image             立ち絵
    update_party_display      party_members（DictProperty）の変化で呼ばれる

`update_party_display` は `party_members` を見て枠を塗るが、
枠が3つなので 4人目には塗る先が無い。
足りないのは枠であって、データでも塗り方でもない。

そこで `113_ui_text_expand` と同じ形にする。
ボタンを1枚足し、押すと帯が上へ伸びて足りないぶんの枠が現れる。
もう一度押すと元の3枠に戻る。
名簿・セーブ・戦闘には一切触らない。

## 枠を増やす（型を名指ししない）

枠は今そこに在る枠を複製して作る（`panel.clone`）。
`ClickableFloatLayout` も `StencilFloatLayout` も、こちらからは名前しか分からない。
`type(cell)()` ならそのビルドが持っているクラスがそのまま使われるので、
名指しせずに同じ形が作れる。
中身（立ち絵の `Image` など）も同じやり方で降りて写す。

複製した枠は `hud.party_cells` に追記する。
この並びの添字が `on_member_label_press(label_index)` の相手そのものなので（GAME.md
§2.8）、追記しておけば押したときの経路がゲーム自身のものになる。
並べ替えてはいけない。
添字がずれると、押した相手と話す相手が食い違う。

### 埋めるのはまずゲームに任せる

枠を足したら、先に `hud.update_party_display()` を1回呼ぶ。
`party_cells` をなめて塗るビルドなら、
それだけで4人目が出る（こちらは塗り方を知らずに済む）。

塗られなかったときだけ自分で埋める。
そのときも埋め方は発明しない。
既に埋まっている枠と `party_members` のその相手のデータを突き合わせ、
文字列が一致した場所を「そこに入る値」とみなす（`panel.learn_fields`）。
立ち絵のパスがどのキーから来るのかを、こちらが決め打ちしない。

### 枠線と、中身の見え方

枠線はウィジェットの canvas に描かれているので、
複製には付いてこない（足した枠だけ枠線が無い状態になる）。
線を自分で引くのではなく、`scripts.hud.new_hud` の `add_border(widget)` を借りる。
色も太さも寸法追従もゲームのものになる。

中身の大きさも複製では食い違う（立ち絵が枠の左へはみ出す）。
ゲームの枠は絵を縦横比のまま置いて `StencilFloatLayout` で切り取っているが、
複製ではその切り取りが効いていない。
そこで**見本の枠を測って、
同じ相対位置・同じ大きさを複製に入れ直す**（`panel.sync_geometry`）。
中身を入れた後に呼ぶ。
立ち絵を差し替えると絵の大きさが変わるので、入れる前に合わせても意味が無い。

## 伸びる向き（上だけ）と、元の枠を1 px も動かさないこと

帯の下端は動かさない。
Kivy の `y` は下端なので、`y` を据え置いて
`height` だけ増やすと下端が動かず上へ伸びる。
帯の下には画面の縁しか無く、上に在るのは背景画像なので、下端が動かないことは、
そのまま「他をずらさない」になる。

**枠は比率ではなく実測の座標で置く**（`panel.pin`）。
最初は各枠の `size_hint_y` を `1/行数` に割り直していたが、
ゲームが使っているのは `0.33` という丸めた比率で、
こちらの `1/4`（＝0.25）と食い違う。
伸びた帯に当て直すと、その差が **元から在る枠を数 px 動かす**。
そこで元の枠は測った位置と大きさに釘付けし、
足した枠だけを行の送り幅（隣り合う枠の y の差。`panel.row_pitch`）ぶんずつ上へ積む。
帯の高さも同じだけ足す（倍率ではなく足し算。枠線は帯より数 px 大きいので、
倍率で配ると差まで倍になる）。

窓に入る行数で頭打ちにする（`MAX_ROWS` と窓の高さの小さい方）。

枠線・背景は帯とは別のウィジェットが描いていることがあるので、**今その帯と同じ場所に同じ大きさで置かれているもの**を仲間として一緒に伸ばす（`panel.family_of`。
これが無いと `113_` と同じく「中身だけ広がって枠線が残る」）。

## 重なったところの始末（2通り）

帯を上へ伸ばすと、そこに元から在ったものが帯の下に入る。
相手によって手が違う。

### ゲームのもの: 黒い板を1枚敷く（相手には触らない）

足した枠は背景を持たない（絵と枠線だけ）ので、
下にあるゲームの選択肢ボタンが透けて見え、
押せてしまう（下の「会話する」の文字が枠に重なる）。
そこで、新しく覆った矩形に不透明な板を1枚敷く（`panel.make_blocker`）。

* 見た目。
  板が不透明なので、下の文字は見えない
* 当たり判定。
  Kivy は「無効なウィジェットに触れたら `True` を返す」ので、
  `disabled=True` の板で触りが止まり、下へ抜けない
* 戻すとき。
  板を外すだけ。
  **ゲームの状態は1つも書き換えていない**

板を帯の中に入れてはいけない。
ゲームの `update_party_display` は帯の子を 1つずつ「パーティの枠」として塗るので、
枠の形をしていない板が混じると `cell.children[0]` が `IndexError` で落ちる（GAME.md
§2.8）。

> 他人の入れ物に、他人が数えている物と違う物を混ぜない。

板は HUD 直下の入れ物へ、帯のすぐ後ろに入れる（`panel.behind_index`）。
Kivy は `children` を逆順に描くので、ここは「帯より下・ゲームのボタンより上」になる唯一の位置で、
触りの判定でも帯より後になる。
だから枠の押下は今までどおり枠に届き、その外側は板で止まる。

### MOD が足したもの: 隠して無効にする

`113_ui_text_expand` の切り替えボタンは、既定でまさにこの帯の真上に出る。
これは板より上に描かれるので板では隠れない。
広げている間は **見えなくし、かつ押せなくする**（`opacity=0` と `disabled=True` の両方。
片方だけでは「見えないのに押せる」か「押せないのに見える」が残る）。

ただし、こうしてよいのは MOD が足したボタンだけ。
見るのは HUD 直下の入れ物の直接の子だけにする（`panel.coverable`）。
木を降りて「`disabled` を持つもの」を全部拾う作りにすると、**ゲームの選択肢ボタンまで掴んで、
戻したあとも押せなくなる**（GAME.md §2.8）。
理由は控えの取り方にある。
ゲームは応答待ちの間だけ選択肢を無効にするので、
その瞬間に控えると「無効」を控えることになり、畳んだときにそれを書き戻して、
ゲームが有効に戻した後でも無効に落とす。

> 他人が管理している状態を控えて書き戻してはいけない。
> 控えた瞬間と書き戻す瞬間の間に、持ち主がその値を変えている。

MOD が足すボタンはその入れ物の直接の子になる（`get_current_screen_root` を壊さないため、
こちらも `113_` もそこへ足す決まり）。
ゲームの選択肢はもっと深い入れ物の中に居るので、この網には入らない。
念のため `hud.buttons` / `right_buttons` / `icon_buttons` /
`party_cells` の中身も避ける（`panel.game_buttons`。
名前で引くのが「触る」ためではなく避けるためなので、
引けなくなっても危ない側へ倒れない）。

こちらのボタンは対象外。
隠すと戻す手段が無くなる。

畳むときは、畳めるかどうかに関わらず必ず先に戻し、板も外す。
加えて注入のたびに1回、隠しっぱなしの相手と置き去りの板が無いか木を掃く（`heal`）。
押せないボタンや見えない板が画面に残るのは遊べなくなることなので、
注入し直せば直る状態にしておく。

## 元に戻せること

戻すために覚えるのは、こちらが触る前の寸法一式（帯と各枠の `size_hint` / `size` / `pos` /
`pos_hint`）。
これを**帯のウィジェット自身に持たせる**（`DESIGN_ATTR`）。
MOD 側の変数に持つと、注入し直したときに「広げた後の寸法」を設計値として控えてしまい、押すたびに帯が育つ（`112_` が
`line_height` で踏んだ罠と同じ）。
広がっている帯からは絶対に控えない。

## 仲間が増えた・減ったとき

その場で当て直す。
画面の側（`party_members`）は `InstantaleApp.update_party_member` が
0.1 秒ごとに入れ直すので、放っておいてもいずれ追いつく。
ただし「雇った瞬間に枠が増える」「別れた瞬間に減る」を確かめられるのは名簿が動いた地点だけなので、
`add_party_member` / `remove_party_member` からも直に当て直す。

名簿が動いた直後はまだ画面の側が古い（入れ直しは次の周期）。
だから次のフレームと、周期をまたいだ後（`REFRESH_DELAY`）の2回に分けて当てる。
`upkeep` は何度呼んでも同じ結果になるので重ならない。

中身は**行数が変わらなくても見直す**。
1人抜けて1人入れば人数は同じままだが顔ぶれは変わるので、
行数の変化だけを合図にすると古い顔が残る。
毎回呼ぶかわりに、入っている id の列が同じなら何もしない。

## 窓の大きさが変わったとき

塗り直し（`update_party_display` /
`update_display_text`）は中身が変わったときにしか来ない。
**窓だけ変えられるとこちらは何も気付けず**、控えている寸法は古い窓の値のまま残る。
そこで `Window.on_resize` を直に拾う（`watch_window`）。
拾った後は「畳む → 控えを捨てる → 1フレーム置いて控え直して当て直す」の順。
間に挟まる1フレームで、ゲームが自分の寸法を入れ直す。

## 触らないもの

名簿（`app.party` / `game_variables['party']`）、`party_members` の中身、セーブ、
戦闘。
このMODが変えるのは帯と枠の寸法、複製した枠、自分で足したボタン1枚だけ。
剥がす（`--unload`）とフックは外れるが、
足した枠とボタンはゲームのプロセスに残る（TECH.md §3.10 の「戻らないもの」）。
"""

import weakref

from instantale_modloader import frames, ui

from . import button as toggle_button
from . import panel as party_panel

LOG_BASENAME = "party_expand.log"

# 広げたときに何行まで増やすか。
# 窓に入る行数と、この値の小さい方で頭打ち。
MAX_ROWS = 8

# 広げている間、帯に重なるボタンを隠して押せなくするか。
HIDE_COVERED = True

# ボタンの置き場所。
# 既定はパーティ欄（画面右の帯）のすぐ上・右寄せ。
# `113_ui_text_expand` のボタンは同じ帯の上の左寄せなので、ぶつからない。
BUTTON_CORNER = "帯の上"

# ボタンの絵柄。
# 線で描くので画像ファイルは要らない（背景なし・白）。
# 「文字」を選ぶと LABEL_EXPAND / LABEL_RESTORE の文字ボタンになる。
ICON = "二重山形"

# 線の太さ（px）と濃さ（0〜1）。
# 背景に溶けるなら濃さを上げる。
ICON_WIDTH = 2.0
ICON_ALPHA = 0.85

# ボタンの高さ（px）。
# アイコンなら正方形、文字ならこの 2 倍の幅。
BUTTON_SIZE = 32

# ボタンの文字（絵柄に「文字」を選んだときだけ使う）。
# 押すと入れ替わる。
LABEL_EXPAND = "仲間"
LABEL_RESTORE = "戻す"

# 仲間が枠に収まっているときもボタンを出すか。
# 既定は出さない（押しても何も起きないボタンを常設しない）。
ALWAYS_SHOW_BUTTON = False

# 起動した直後から広げておくか。
START_EXPANDED = False

# 広げた帯が窓に占めてよい割合。
# 窓の縁まで詰めない。
MAX_FILL = 0.98

# 隠す相手の大きさの上限（窓の面積に対する割合）。
# これより大きいものはボタンではなく入れ物なので触らない。
MAX_HIDE_AREA = 0.2

# 足した枠の立ち絵の入れ方。
# ゲームの枠は絵の縦横比のまま枠からはみ出させ、
# `StencilFloatLayout` で切り取っているが、
# 複製ではその切り取りが効かない（そのままだと絵が枠の左へはみ出す）。
# どちらに寄せるかを選べるようにする。
FIT_INSIDE = "枠に収める"      # 縦横比のまま枠の中へ収める（はみ出さない）
FIT_FILL = "枠いっぱい"        # 縦横比を無視して枠を埋める（切り取りに近い）
PORTRAIT_FIT = FIT_INSIDE

# 隅ではない置き場所（こちらが座標を入れるもの）と、その余白（px）。
ON_PANEL = "帯の上"
IN_PANEL = "帯の右上"
PLACEMENTS = (ON_PANEL, IN_PANEL)
PANEL_GAP = 4.0
PANEL_INSET = 8.0

# 窓の大きさが変わってから、もう一度当て直すまでの秒数。
RESETTLE_DELAY = 0.3

# 仲間が増減してから、もう一度当て直すまでの秒数。
# 名簿が動いた直後はまだ画面の側（`party_members`）が古く、
# `InstantaleApp.update_party_member` が 0.1 秒ごとに入れ直している。
# その周期をまたぐだけの間を置く。
REFRESH_DELAY = 0.2

# 塗り直しは何度も来るので、書くのは節目だけ。

# ウィジェット自身に持たせる控え。
# 注入し直しても残るので、
# 広げた後の寸法を設計値として控え直してしまう事故が起きない。
DESIGN_ATTR = "_instantale_party_design"
EXPANDED_ATTR = "_instantale_party_rows"
EXTRA_ATTR = "_instantale_party_extras"
COVERED_ATTR = "_instantale_party_covered"
BLOCKER_ATTR = "_instantale_party_blocker"
HIDDEN_ATTR = "_instantale_party_was"
MINE_ATTR = "_instantale_party_mine"
BUTTON_ATTR = "_instantale_party_button"
CALLBACK_ATTR = "_instantale_party_callback"
WINDOW_ATTR = "_instantale_party_on_resize"


def apply(ctx):
    # 設定の意思（広げたいか）。
    # 画面を作り直されても引き継ぐので、帯ではなくこちらに持つ。
    # 帯側にあるのは「今その帯が何行か」。
    state = {"expanded": bool(START_EXPANDED), "synced": False,
             "ask_game": True, "busy": False, "stale": False, "watching": False,
             "healed": False, "game_paints": False,
             "hud": None, "fields": None, "anchor": None}

    note = ctx.logger(LOG_BASENAME)
    warn_once = ctx.warner("party expand")

    def guarded(fn):
        """ボタンから呼ばれる処理。ここで投げるとゲームを巻き込む。"""
        try:
            return fn()
        except Exception:
            ctx.log_exc("party expand: toggle failed")
            return None

    schedule = ui.scheduler(ctx, "party expand")

    def close_enough(value, wanted):
        try:
            return abs(float(value) - float(wanted)) < 0.5
        except (TypeError, ValueError):
            return False

    def rows_now(box):
        """今その帯が何行に広がっているか。触っていない帯では None。

        既定値を必ず渡す。
        `frames.attr` の既定は文字列（`"<missing>"`）なので、
        そのまま真偽で見ると「属性を持たない帯」が全部「広がっている」側に倒れる（`113_` が立ち絵探しで踏んだのと同じ罠。
        VERIFICATION_LOG.md §2.26）。
        """
        value = frames.attr(box, EXPANDED_ATTR, None)
        return value if isinstance(value, int) else None

    # -- 画面のどこを触るか ---------------------------------------------------
    def base_cells(hud):
        """ゲームが作った枠だけ（こちらが足した複製を数えない）。"""
        cells = party_panel.cells_of(hud)
        return [cell for cell in cells if frames.attr(cell, MINE_ATTR) is not True]

    def design_of(hud, box, cells):
        """この帯の元の寸法。最初に見たとき（＝まだ広げていないとき）の値。"""
        saved = frames.attr(box, DESIGN_ATTR)
        if isinstance(saved, dict) and saved.get("family"):
            return saved
        if rows_now(box) is not None:
            # 広がっている帯からは控えない。
            # 今の寸法はこちらが入れた値なので、
            # それを設計値にすると二度と元に戻せない（押すたびに育つ）。
            warn_once("orphan", "the party panel is expanded but its original size "
                                "is gone; leaving it as it is")
            return None
        fresh = party_panel.capture(hud, box, cells)
        if fresh is None:
            return None
        try:
            setattr(box, DESIGN_ATTR, fresh)
        except Exception:
            pass          # 属性を足せない相手でも、この場の1回は動く
        note("design: " + party_panel.describe(fresh))
        return fresh

    def rows_for(hud, design):
        """出したい行数。仲間の人数と、窓に入る行数と、設定の小さい方。"""
        base = design["rows"]
        wanted = len(party_panel.member_ids(hud))
        if wanted <= base:
            return base
        rows = min(wanted, max(int(MAX_ROWS), base))
        pitch = party_panel.row_pitch(design)
        _width, height = toggle_button.window_size()
        if height and pitch > 0:
            # 伸ばせるのは「窓に入る高さ」まで。
            # 帯の下端は動かないので、使えるのは元の高さぶんを差し引いた残り。
            room = height * MAX_FILL - float(design["size"][1])
            rows = min(rows, base + max(int(room // pitch), 0))
        return rows

    # -- 枠を足す -------------------------------------------------------------
    def running_app():
        try:
            from kivy.app import App
            return App.get_running_app()
        except Exception:
            return None

    def press_member(hud, cell):
        """複製した枠が押されたとき。ゲーム自身の経路に流す。

        `on_member_label_press(label_index)` は
        `hud.party_cells[index].member_id` から相手を決める（GAME.md §2.8）。
        複製をその並びに追記してあるので、添字を渡すだけで、
        相手の決め方も効果音も会話の入り方もゲームのままになる。
        """
        app = running_app()
        roster = party_panel.roster_of(hud)
        if app is not None and isinstance(roster, list) and cell in roster:
            press = frames.attr(app, "on_member_label_press")
            if callable(press):
                note("pressed member cell #{}".format(roster.index(cell)))
                return press(roster.index(cell))
        member_id = frames.attr(cell, "member_id", None)
        choose = frames.attr(app, "process_party_member_choice") if app else None
        if callable(choose) and member_id not in (None, frames.MISSING):
            note("pressed member {} (direct)".format(member_id))
            return choose(member_id)
        warn_once("press", "cannot route a press on the added party cells; "
                           "they are display-only in this build")
        return None

    def bind_cell(hud, cell, template):
        """複製した枠に押下先を付ける。入れ先は見本の枠から知る。"""
        def pressed(*_args, **_kwargs):
            return guarded(lambda: press_member(hud, cell))

        names = party_panel.callback_names(template)
        for name in names:
            try:
                setattr(cell, name, pressed)
            except Exception:
                pass
        binder = frames.attr(cell, "bind")
        if callable(binder):
            try:
                cell.bind(on_release=pressed)
            except Exception:
                pass      # 押下のイベントを持たない入れ物（既定はこちら）
        if not names:
            note("no callback slot on the party cell; copies may be display-only")

    def drop_extra(hud, box, cell):
        roster = party_panel.roster_of(hud)
        if isinstance(roster, list) and cell in roster:
            try:
                roster.remove(cell)
            except ValueError:
                pass
        try:
            box.remove_widget(cell)
        except Exception:
            ctx.log_exc("party expand: could not remove an added party cell")

    def extras_of(box):
        kept = frames.attr(box, EXTRA_ATTR)
        if not isinstance(kept, list):
            return []
        return [cell for cell in kept
                if frames.attr(cell, "parent") not in (None, frames.MISSING)]

    def ensure_extras(hud, box, design, count):
        """足りない枠を複製して用意する（多すぎれば減らす）。"""
        extras = extras_of(box)
        template = design["cells"][design["order"][0]]["widget"]
        while len(extras) > count:
            drop_extra(hud, box, extras.pop())
        while len(extras) < count:
            fresh = party_panel.clone(template, ctx.log_exc)
            if fresh is None:
                warn_once("clone", "cannot copy a party cell in this build; "
                                   "the panel is left as the game built it")
                break
            try:
                setattr(fresh, MINE_ATTR, True)
                setattr(fresh, "member_id", None)
            except Exception:
                pass
            try:
                box.add_widget(fresh)
            except Exception:
                ctx.log_exc("party expand: could not add a party cell")
                break
            roster = party_panel.roster_of(hud)
            if isinstance(roster, list):
                roster.append(fresh)
            bind_cell(hud, fresh, template)
            # 枠線は canvas に描かれていて複製には付いてこない。
            # ゲーム自身の描き方を借りて引き直す（実機で「足した枠だけ枠線が無い」）。
            drawn = party_panel.add_border(fresh, ctx.log_exc)
            extras.append(fresh)
            note("added party cell #{} ({}, border by {})".format(
                len(extras), type(fresh).__name__, drawn or "nothing"))
        try:
            setattr(box, EXTRA_ATTR, list(extras))
        except Exception:
            pass
        return extras

    def learn(hud, cells):
        """「枠のどこに何が入るか」を、埋まっている枠から知る。"""
        if state["fields"]:
            return state["fields"]
        for cell in cells:
            member_id = frames.attr(cell, "member_id", None)
            if member_id in (None, frames.MISSING):
                continue
            data = party_panel.member_data(hud, member_id)
            if data is None:
                continue
            fields = party_panel.learn_fields(cell, data)
            if fields:
                state["fields"] = fields
                note("cell fields: {}".format(
                    ", ".join("{}.{} <- {}".format(path, name, key)
                              for path, name, key in fields)))
                return fields
        return []

    def ask_game(hud, extras):
        """先にゲーム自身に塗らせる。塗られたなら、こちらは埋め方を知らずに済む。"""
        if not state["ask_game"]:
            return False
        painter = frames.attr(hud, "update_party_display")
        if not callable(painter):
            state["ask_game"] = False
            return False
        state["busy"] = True          # 自分のフックへ戻ってこないように
        try:
            painter()
        except Exception:
            ctx.log_exc("party expand: the game's own painter failed")
        finally:
            state["busy"] = False
        painted = all(frames.attr(cell, "member_id", None)
                      not in (None, frames.MISSING) for cell in extras)
        if painted:
            state["game_paints"] = True
            note("the game paints the added cells itself")
            return True
        state["ask_game"] = False
        note("the game leaves the added cells empty; filling them here")
        return False

    def fill_extras(hud, cells, extras):
        """足した枠に、元の枠に入り切らない相手を入れる。塗り直しのたびに呼ぶ。

        人数が同じままでも中身は入れ替わる（1人抜けて1人入る、隊列が変わる）。
        行数の変化だけを合図にすると、そのとき古い顔が残る。
        毎回呼ぶかわりに、**変わっていなければ何もしない**（比べるのは
        id の列だけ）。
        """
        if not extras:
            return
        empty = [cell for cell in extras
                 if frames.attr(cell, "member_id", None) in (None, frames.MISSING)]
        if state["game_paints"]:
            # 塗るのはゲーム自身。
            # こちらが割り込むと取り合いになる。
            # ただし **枠を増やした直後は空のまま**なので、
            # そのときだけ塗り直しを頼む（ゲームは
            # `party_members` が動いたときしか塗らない。
            # 雇い直しで枠が1つ増えた場面がこれ）。
            if empty:
                ask_game(hud, extras)
            return
        if ask_game(hud, extras):
            return
        shown = party_panel.shown_ids(cells)
        remaining = [member_id for member_id in party_panel.member_ids(hud)
                     if member_id not in shown]
        wanted = remaining[:len(extras)]
        if party_panel.shown_ids(extras) == wanted and len(wanted) == len(extras):
            return            # 同じ顔ぶれが同じ順で入っている
        fields = learn(hud, cells)
        for cell, member_id in zip(extras, remaining):
            data = party_panel.member_data(hud, member_id)
            try:
                setattr(cell, "member_id", member_id)
            except Exception:
                pass
            if data is not None and fields:
                party_panel.fill(cell, data, fields, ctx.log_exc)
        note("filled {} added cell(s) with {}".format(len(extras), wanted))

    # -- 重なるボタンを隠す ---------------------------------------------------
    def gained_rect(box, design):
        """帯が新しく覆った矩形（広げる前の上端から、伸びた先まで）。"""
        rect = party_panel.rect_of(box)
        if rect is None or not design["size"]:
            return None
        top = rect[1] + float(design["size"][1])       # 広げる前の上端
        gained = (rect[0], top, rect[2], max(rect[1] + rect[3] - top, 0.0))
        return gained if gained[3] > 0 else None

    def cover_gap(hud, box, design):
        """新しく覆った場所に黒い板を敷く（ゲームのボタンには触らない）。

        足した枠は背景を持たないので、下にあるゲームの選択肢が透けて見え、
        押せてしまう。
        ゲームのボタンを無効にするのは禁じ手（応答待ちの値を控えて書き戻すと選択肢が死ぬ。
        GAME.md §2.8）なので、こちらが1枚敷く。

        板は帯の中に入れてはいけない。
        ゲームの `update_party_display` は帯の子を1つずつ「パーティの枠」として塗るので、枠の形をしていない板が混じると
        `cell.children[0]` が `IndexError` で落ちる（GAME.md §2.8）。
        板は HUD 直下の入れ物へ、帯のすぐ後ろに入れる。
        描画は帯より下、触りの判定は帯より後になるので、
        枠の押下は今までどおり枠に届き、その外側は板で止まる。
        """
        if not HIDE_COVERED:
            return
        gained = gained_rect(box, design)
        if gained is None:
            return
        blocker = frames.attr(box, BLOCKER_ATTR, None)
        if blocker is None or frames.attr(blocker, "parent") in (None, frames.MISSING):
            blocker = party_panel.make_blocker(ctx.log_exc)
            if blocker is None:
                return        # 板が作れない環境では敷かない（何も壊さない）
            host = host_of(hud)
            index = party_panel.behind_index(host, box)
            if index is None:
                return        # 帯がその入れ物の中に居ない画面では敷かない
            try:
                host.add_widget(blocker, index=index)
            except Exception:
                ctx.log_exc("party expand: could not add the blocker")
                return
            try:
                setattr(box, BLOCKER_ATTR, blocker)
            except Exception:
                pass
            note("covered the new area with a blocker at {} (behind the panel)"
                 .format(tuple(round(value) for value in gained)))
        party_panel.place_blocker(blocker, gained, ctx.log_exc)

    def uncover_gap(box):
        blocker = frames.attr(box, BLOCKER_ATTR, None)
        if blocker is None:
            return
        parent = frames.attr(blocker, "parent")
        if parent not in (None, frames.MISSING):
            try:
                parent.remove_widget(blocker)
            except Exception:
                ctx.log_exc("party expand: could not remove the blocker")
        try:
            setattr(box, BLOCKER_ATTR, None)
        except Exception:
            pass

    def hide_covered(hud, box, design, cells, extras):
        if not HIDE_COVERED:
            return
        gained = gained_rect(box, design)
        if gained is None:
            return
        keep = set(id(shot["widget"]) for shot in design["family"])
        keep.update(id(cell) for cell in list(cells) + list(extras))
        keep.update(party_panel.game_buttons(hud))   # ゲームの選択肢は触らない
        own = frames.attr(hud, BUTTON_ATTR)
        if own not in (None, frames.MISSING):
            keep.add(id(own))         # こちらのボタンを隠すと戻す手段が消える
        covered = []
        for widget in party_panel.coverable(host_of(hud), gained, keep,
                                            toggle_button.window_size(), MAX_HIDE_AREA):
            if frames.attr(widget, HIDDEN_ATTR) is not frames.MISSING:
                covered.append(widget)
                continue              # もう隠してある（控えを上書きしない）
            try:
                setattr(widget, HIDDEN_ATTR,
                        (frames.attr(widget, "opacity", 1.0),
                         frames.attr(widget, "disabled", False)))
                widget.opacity = 0.0
                widget.disabled = True
            except Exception:
                ctx.log_exc("party expand: could not hide a covered widget")
                continue
            covered.append(widget)
            note("hid {} at {} (covered by the panel)".format(
                type(widget).__name__, party_panel.rect_of(widget)))
        try:
            setattr(box, COVERED_ATTR, covered)
        except Exception:
            pass

    def unhide(box):
        covered = frames.attr(box, COVERED_ATTR)
        for widget in covered if isinstance(covered, list) else []:
            was = frames.attr(widget, HIDDEN_ATTR)
            if not isinstance(was, tuple):
                continue
            try:
                widget.opacity, widget.disabled = was
            except Exception:
                ctx.log_exc("party expand: could not show a covered widget again")
            try:
                delattr(widget, HIDDEN_ATTR)
            except Exception:
                # 消せない相手には番人（`frames.MISSING`）を書き戻す。
                # 次に広げたときの「もう隠してある」判定はこれを見て控え直す側に倒れるので、
                # 戻した後の値がそのまま次の控えになる。
                try:
                    setattr(widget, HIDDEN_ATTR, frames.MISSING)
                except Exception:
                    pass
        try:
            setattr(box, COVERED_ATTR, [])
        except Exception:
            pass

    # -- 広げる / 戻す --------------------------------------------------------
    def arrange(design, extras):
        """枠を並べ直す。**元から在る枠は1 px も動かさない**。

        元の枠は実測どおりの座標に釘付けし（`pin`）、足した枠だけを、
        その上へ行の送り幅ぶんずつ積む。
        比率（`size_hint` / `pos_hint`）で置き直すと、
        ゲームが使っている 0.33 のような丸めた比率と、
        こちらが割り直した 1/4・1/5 が食い違って元の枠が数 px 動く。
        """
        pitch = party_panel.row_pitch(design)
        top = design["cells"][design["order"][0]]
        for shot in design["cells"]:
            party_panel.pin(shot, shot["widget"], shot["pos"][0], shot["pos"][1])
        for step, cell in enumerate(extras):
            party_panel.pin(top, cell, top["pos"][0],
                            float(top["pos"][1]) + (step + 1) * pitch)
        return pitch

    def align(design, extras):
        """足した枠の中身を、見本の枠と同じ見え方に合わせる。

        中身を入れた後に呼ぶ。
        立ち絵を差し替えると絵の大きさが変わるので、
        入れる前に合わせても意味が無い（実機で立ち絵が枠の左へはみ出した）。
        """
        top = design["cells"][design["order"][0]]["widget"]
        for cell in extras:
            party_panel.sync_geometry(top, cell, PORTRAIT_FIT == FIT_FILL,
                                      ctx.log_exc)

    def expand(hud, box, cells, design):
        rows = rows_for(hud, design)
        if rows <= design["rows"]:
            return collapse(hud, box)      # 全員が元の枠に収まっている
        pitch = party_panel.row_pitch(design)
        delta = pitch * (rows - design["rows"])
        height = float(design["size"][1]) + delta
        if (rows_now(box) == rows
                and close_enough(frames.attr(box, "height"), height)):
            # もう広がっている。
            # 寸法には触らないが、
            # 中身の見え方だけは毎回合わせ直す（ゲームが枠を塗り替えると絵の大きさが変わる）。
            # 寸法には触らないが、中身は毎回見直す。
            # 人数が同じままでも顔ぶれは入れ替わる（1人抜けて1人入る）。
            extras = extras_of(box)
            fill_extras(hud, cells, extras)
            align(design, extras)
            cover_gap(hud, box, design)   # 板の寸法も毎回合わせ直す
            return False
        for shot in design["family"]:
            party_panel.grow(shot, delta)
        extras = ensure_extras(hud, box, design, rows - design["rows"])
        if not extras:
            for shot in design["family"]:
                party_panel.restore(shot, move=False)
            return False          # 枠を足せないビルドでは帯も伸ばさない
        arrange(design, extras)
        fill_extras(hud, cells, extras)
        align(design, extras)         # 中身を入れた後に見え方を合わせる
        cover_gap(hud, box, design)   # 新しく覆った場所を黒い板で塞ぐ
        hide_covered(hud, box, design, cells, extras)
        try:
            setattr(box, EXPANDED_ATTR, rows)
        except Exception:
            pass
        schedule(lambda: guarded(lambda: clamp_all(design)))
        note("expanded to {} row(s), height {:.0f} (design {:.0f}, pitch {:.0f})"
             .format(rows, height, design["size"][1], pitch))
        return True

    def collapse(hud, box):
        # 畳めるかどうかに関わらず、隠したものは必ず先に戻す。
        # 途中で降りる道のどれかが unhide を飛ばすと、
        # 隠したボタンが押せないまま残る。
        unhide(box)
        uncover_gap(box)
        design = frames.attr(box, DESIGN_ATTR)
        if not isinstance(design, dict) or rows_now(box) is None:
            return False          # 触っていない帯は戻す必要もない
        for cell in extras_of(box):
            drop_extra(hud, box, cell)
        try:
            setattr(box, EXTRA_ATTR, [])
        except Exception:
            pass
        try:
            for shot in design["cells"]:
                party_panel.restore(shot, move=True)
            for shot in design["family"]:
                party_panel.restore(shot, move=True)
        except Exception:
            ctx.log_exc("party expand: could not restore the panel")
            return False
        try:
            setattr(box, EXPANDED_ATTR, None)
        except Exception:
            pass
        note("restored to {} row(s)".format(design["rows"]))
        return True

    def clamp_all(design):
        """伸びた帯を窓の内側へ。仲間は同じ量だけ動かす。

        位置を決めているのは `pos_hint` を持たない相手なので、
        その最初の1つを基準にする。
        `pos_hint` を持つ相手は親の中で寄せ直されるので触らない。
        """
        movable = [shot for shot in design["family"] if not shot["pos_hint"]]
        if not movable:
            return
        box = movable[0]["widget"]
        before = party_panel.numbers(frames.attr(box, "pos"), 2)
        toggle_button.clamp(box)
        after = party_panel.numbers(frames.attr(box, "pos"), 2)
        if not before or not after:
            return
        shift_x, shift_y = after[0] - before[0], after[1] - before[1]
        if not shift_x and not shift_y:
            return
        for shot in movable[1:]:
            widget = shot["widget"]
            try:
                widget.x, widget.y = widget.x + shift_x, widget.y + shift_y
            except Exception:
                ctx.log_exc("party expand: could not move a panel widget")

    def heal(hud):
        """注入のたびに1回だけ、**隠しっぱなしのものを全部戻す**。

        画面を作り直された・注入をまたいだ・こちらが途中で落ちた。
        どの道でも、隠したまま行方不明になった相手が残りうる。
        押せないボタンが画面に居座るのは遊べなくなることなので、
        控え（`HIDDEN_ATTR`）を持つ相手を木から拾い直して戻す。
        注入し直せば直る状態にしておく。
        """
        restored = 0

        def descend(widget, depth):
            if frames.attr(widget, party_panel.BLOCK_RECT_ATTR, None) is not None:
                # 置き去りになった黒い板。
                # そこの触りを止め続けるので必ず外す。
                parent = frames.attr(widget, "parent")
                if parent not in (None, frames.MISSING):
                    try:
                        parent.remove_widget(widget)
                    except Exception:
                        ctx.log_exc("party expand: could not remove a stray blocker")
                return 1
            was = frames.attr(widget, HIDDEN_ATTR)
            if isinstance(was, tuple):
                try:
                    widget.opacity, widget.disabled = was
                except Exception:
                    ctx.log_exc("party expand: could not restore a stranded widget")
                try:
                    delattr(widget, HIDDEN_ATTR)
                except Exception:
                    pass
                return 1
            found = 0
            if depth < party_panel.MAX_DEPTH:
                for child in party_panel.children_of(widget):
                    found += descend(child, depth + 1)
            return found

        try:
            restored = descend(hud, 0)
        except Exception:
            ctx.log_exc("party expand: could not sweep for stranded widgets")
        if restored:
            note("restored {} widget(s) that were left hidden".format(restored))

    def adopt(box):
        """注入し直したときに、画面の今の姿を意思として引き継ぐ。

        `apply()` は何度でも呼ばれる（TECH.md §3.6）。
        MOD 側の変数は作り直されるので、ここで読み直さないと「広げて遊んでいたのに、
        注入し直した拍子に戻る」。
        """
        if state["synced"]:
            return
        state["synced"] = True
        raw = frames.attr(box, EXPANDED_ATTR)
        if raw is frames.MISSING:
            return        # まだ触ったことのない帯。設定（START_EXPANDED）に従う
        state["expanded"] = isinstance(raw, int)

    def apply_state(hud):
        """今の意思（広げたいか）を、今の帯に当てる。"""
        if state["stale"] or state["busy"]:
            return            # 窓の組み直しの最中／ゲームの塗り直しの最中
        cells = base_cells(hud)
        if not cells:
            warn_once("cells", "no party cells on this screen; "
                               "leaving the panel as the game built it")
            return
        box = party_panel.panel_of(cells)
        if box is None:
            warn_once("panel", "the party cells have no container to grow")
            return
        adopt(box)
        design = design_of(hud, box, cells)
        if design is None:
            return
        if state["expanded"]:
            expand(hud, box, cells, design)
        else:
            collapse(hud, box)

    def overflowing(hud):
        """枠に収まらない仲間が居るか（ボタンを出すかどうかの判断）。"""
        cells = base_cells(hud)
        return bool(cells) and len(party_panel.member_ids(hud)) > len(cells)

    # -- 窓の大きさが変わったとき --------------------------------------------
    def rebuild(hud):
        """窓が変わった後に、控えを取り直して当て直す。

        1. 先に元の寸法へ戻す（広げていたなら畳む）。
           ここで戻さずに控えだけ捨てると、次に控えるのは「広げた後の寸法」になる
        2. 畳めたときだけ控えを捨てる
        3. さらに次のフレームで当て直す。
           畳んだ直後はまだ古い寸法が入っているので、
           その場で控え直すと古い窓の値を新しい設計値にしてしまう
        """
        cells = base_cells(hud)
        box = party_panel.panel_of(cells) if cells else None
        if box is not None:
            collapse(hud, box)
            if rows_now(box) is None:
                try:
                    setattr(box, DESIGN_ATTR, None)
                except Exception:
                    pass      # 捨てられなくても、次の当て直しで寸法は合う

        def resettled():
            state["stale"] = False
            upkeep(hud)

        schedule(lambda: guarded(resettled))

    def on_window_resize(*_args):
        reference = state.get("hud")
        hud = reference() if reference is not None else None
        if hud is None:
            return
        # 組み直しが済むまで寸法は測らない。
        # ここを止めておかないと、
        # 途中の（古い窓のままの）寸法を新しい設計値として控えてしまう。
        state["stale"] = True
        schedule(lambda: guarded(lambda: rebuild(hud)))
        schedule(lambda: guarded(lambda: upkeep(hud)), RESETTLE_DELAY)

    def watch_window():
        if state["watching"]:
            return
        try:
            from kivy.core.window import Window
        except Exception:
            return            # ゲームの外（オフライン検証）では窓が無い
        state["watching"] = True
        # 注入し直したときに古い版の手が残らないよう、前のものを外してから結ぶ。
        previous = frames.attr(Window, WINDOW_ATTR, None)
        if previous is not None:
            try:
                Window.unbind(on_resize=previous)
            except Exception:
                pass
        try:
            Window.bind(on_resize=on_window_resize)
            setattr(Window, WINDOW_ATTR, on_window_resize)
        except Exception:
            state["watching"] = False
            ctx.log_exc("party expand: could not watch the window size")

    # -- ボタン --------------------------------------------------------------
    def button_text():
        if ICON != toggle_button.AS_TEXT:
            return ""         # アイコンで示すので文字は出さない
        return LABEL_RESTORE if state["expanded"] else LABEL_EXPAND

    def toggle(hud):
        state["expanded"] = not state["expanded"]
        note("pressed: {}".format("expand" if state["expanded"] else "restore"))
        apply_state(hud)
        widget = frames.attr(hud, BUTTON_ATTR)
        if widget not in (None, frames.MISSING):
            try:
                widget.text = button_text()
            except Exception:
                ctx.log_exc("party expand: could not relabel the button")
            place_button(hud, widget)     # 帯が動いたぶんを追い、向きも描き直す

    def host_of(hud):
        """ボタンを載せる相手。HUD 自身の子の並びは変えない。

        規則は `ui.overlay_host` に集約してある（`113_` と共通。
        同じ発見を 2箇所に書かない。TECH.md §6.1）。
        素の HUD の子は `FloatLayout` 1枚だけで、そこへ直接足すと子が2つになり、
        「画面の最初の子」を取る側から見える相手が変わる（`scripts.hud.new_hud:get_current_screen_root`）。
        ここを外すとアイテムの移動・装備が効かなくなる（VERIFICATION_LOG.md
        §2.33）。

        選ぶのはいちばん古い子。
        先頭（＝いちばん新しい子）を採ると、
        他の MOD が HUD 直下に残したウィジェットの中へ入り込みうる。
        HUD へウィジェットを足す MOD が2本あれば成立してしまう。
        """
        return ui.overlay_host(hud)

    def place_button(hud, widget):
        """置き直す。塗り直しのたびに呼ぶ（帯は伸び縮みする）。"""
        if BUTTON_CORNER not in PLACEMENTS:
            toggle_button.paint(widget, ICON, state["expanded"],
                                ICON_WIDTH, ICON_ALPHA, ctx.log_exc)
            return
        cells = base_cells(hud)
        box = party_panel.panel_of(cells) if cells else None
        rect = party_panel.rect_of(box) if box is not None else None
        if rect is not None:
            try:
                widget.pos_hint = {}          # 位置はこちらが入れる
                if BUTTON_CORNER == IN_PANEL:
                    inset = toggle_button.upx(PANEL_INSET)
                    widget.x = rect[0] + rect[2] - widget.width - inset
                    widget.y = rect[1] + rect[3] - widget.height - inset
                else:
                    # 帯のすぐ上・右寄せ。
                    # `113_` のボタン（左寄せ）と並ばない。
                    widget.x = rect[0] + rect[2] - widget.width
                    widget.y = rect[1] + rect[3] + toggle_button.upx(PANEL_GAP)
            except Exception:
                ctx.log_exc("party expand: could not place the button")
                return
            toggle_button.clamp(widget)
            toggle_button.paint(widget, ICON, state["expanded"],
                                ICON_WIDTH, ICON_ALPHA, ctx.log_exc)
            remember(rect)
            return
        # 帯が見つからない画面（会話前・戦闘中など）では隅に落とす。
        if not widget.pos_hint:
            widget.pos_hint = dict(toggle_button.CORNERS["右上"])
        toggle_button.paint(widget, ICON, state["expanded"],
                            ICON_WIDTH, ICON_ALPHA, ctx.log_exc)

    def remember(rect):
        """置き場所が変わったときだけ記録する（毎フレームは書かない）。"""
        where = tuple(round(value) for value in rect)
        if state.get("anchor") != where:
            state["anchor"] = where
            note("button placed by the panel at {}".format(where))

    def font_of(hud):
        """ボタンの文字に使うフォント。本文のラベルから写す（豆腐にしない）。

        `frames.text_of` で受けるのは、`hud.text_display` が None のとき
        `frames.attr(None, "font_name")` が文字列の番人（`"<missing>"`）を返し、
        `isinstance(str)` を素通りして**フォント名として代入されてしまう**ため（TECH.md
        §5.2）。
        取れなければ Kivy の既定に任せる。
        """
        name = frames.text_of(frames.attr(hud, "text_display", None), "font_name")
        return name or None

    def ensure_button(hud):
        """ボタンを1枚だけ足す。既にあれば押下先だけ今の注入へ付け替える。"""
        widget = frames.attr(hud, BUTTON_ATTR)
        if widget in (None, frames.MISSING):
            if not base_cells(hud):
                return    # パーティ欄が無い画面（会話前・戦闘中）には足さない
            widget = toggle_button.make(font_of(hud), button_text(), BUTTON_SIZE, ICON,
                                        {} if BUTTON_CORNER in PLACEMENTS
                                        else toggle_button.CORNERS.get(
                                            BUTTON_CORNER,
                                            toggle_button.CORNERS["右上"]))
            if widget is None:
                warn_once("button", "kivy Button unavailable; no toggle will be shown")
                return
            setattr(hud, BUTTON_ATTR, widget)
        host = host_of(hud)
        parent = frames.attr(widget, "parent")
        if parent is not host:
            if parent not in (None, frames.MISSING):
                try:
                    parent.remove_widget(widget)
                except Exception:
                    ctx.log_exc("party expand: could not detach the toggle button")
            try:
                host.add_widget(widget)     # 既定は先頭挿入＝一番上に描かれる
            except Exception:
                ctx.log_exc("party expand: could not add the toggle button")
                return
            note("button added to {} at {}".format(type(host).__name__, BUTTON_CORNER))

        # 押下先の付け替え。
        # 注入し直したとき、古い注入の切り替えを呼び続けないため。
        previous = frames.attr(widget, CALLBACK_ATTR)
        if previous not in (None, frames.MISSING):
            try:
                widget.unbind(on_release=previous)
            except Exception:
                pass

        def callback(_instance=None, _hud=hud):
            return guarded(lambda: toggle(_hud))

        try:
            widget.bind(on_release=callback)
            setattr(widget, CALLBACK_ATTR, callback)
        except Exception:
            ctx.log_exc("party expand: could not bind the toggle button")
            return
        try:
            widget.text = button_text()
        except Exception:
            pass
        # 収まっているときは出さない（押しても何も起きないボタンを常設しない）。
        toggle_button.show(widget, bool(ALWAYS_SHOW_BUTTON) or overflowing(hud)
                           or state["expanded"])
        place_button(hud, widget)

    def upkeep(hud):
        """塗り直しのたびに、ボタンが在ることと、広げたままであることを保つ。"""
        if state["busy"]:
            return            # ゲームの塗り直しをこちらから呼んでいる最中
        if not state["healed"]:
            state["healed"] = True
            heal(hud)         # 隠しっぱなしのものを、注入のたびに1回だけ戻す
        state["hud"] = weakref.ref(hud)
        watch_window()
        apply_state(hud)
        ensure_button(hud)

    # -- フック --------------------------------------------------------------
    # パーティが変わったとき（雇用・離脱・HP）と、画面が塗り直されたとき。
    # どれもゲームに先に仕事をさせてから、こちらの後始末をする。
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_party_display", safe=True)
    def update_party_display(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        upkeep(self)
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        upkeep(self)
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts", safe=True)
    def update_button_texts(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        upkeep(self)
        return result

    # 仲間が増えた・減ったその場で当て直す。
    # 塗り直しを待たない。
    # 画面の側（`party_members`）は `InstantaleApp.update_party_member` が
    # 0.1 秒ごとに入れ直しているので、放っておいてもその周期で追いつく。
    # だが「雇った瞬間に枠が増える」「別れた瞬間に減る」を確かめられるのはここだけなので、
    # 名簿が動いた地点から直に当て直す。
    # 名簿が動いた直後はまだ画面の側が古い（入れ直しは次の周期）。
    # だから次のフレームと、周期をまたいだ後の2回に分けて当て直す。
    # `upkeep` は何度呼んでも同じ結果になる（変わっていなければ何もしない）ので重ならない。
    def party_changed(app, what):
        hud = frames.attr(app, "hud", None)
        if hud in (None, frames.MISSING):
            reference = state.get("hud")
            hud = reference() if reference is not None else None
        if hud is None:
            return
        note("party {}: refreshing the panel".format(what))
        schedule(lambda: guarded(lambda: upkeep(hud)))
        schedule(lambda: guarded(lambda: upkeep(hud)), REFRESH_DELAY)

    @ctx.wrap("__main__:InstantaleApp.add_party_member", safe=True, required=False)
    def add_party_member(orig, self, character_id, *args, **kwargs):
        result = orig(self, character_id, *args, **kwargs)
        party_changed(self, "joined")
        return result

    @ctx.wrap("__main__:InstantaleApp.remove_party_member", safe=True, required=False)
    def remove_party_member(orig, self, member_id, *args, **kwargs):
        result = orig(self, member_id, *args, **kwargs)
        party_changed(self, "left")
        return result

    ctx.log("party expand: a button grows the party panel to at most {} row(s) "
            "so the 4th member onwards is shown ({} corner); details go to out/{}"
            .format(MAX_ROWS, BUTTON_CORNER, LOG_BASENAME))
