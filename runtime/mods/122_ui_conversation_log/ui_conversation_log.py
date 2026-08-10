# -*- coding: utf-8 -*-
"""追加: 画面に流れた本文を貯めて、本のアイコンから読み返す。

## 何が困っているか

本文（情景描写・LLM の応答・システムメッセージ）は1文字ずつ打ち出され、
次の本文が来ると**前のものは画面から消える**。ゲームはそれをどこにも残さない ―
`app.current_conversation_history` はいま開いている会話のやり取りだけで、会話を
閉じると捨てられる（GAME.md §2.5）。「さっき何と言われたか」を確かめる手段が無い。

そこで、打ち出される本文をこちらで流れた順に控え、いつでも読み返せる窓を出す。
ゲームの本文・会話履歴・セーブには**一切触らない**。読み取って控えるだけ。

## どこで拾うか

本文の打ち出しは `InstantaleApp.add_text_display(self, dt, context, index=-1)`
で始まる（GAME.md §2.3）。1文字ごとに呼ばれるのは `index >= 0` の続きで、
**`index == -1` が「新しい本文が始まった」の合図**、`context` はその本文の全文
（`118_batch_message_render` が一括表示に使っているのと同じ性質）。だから
`index == -1` のときだけ控えれば、1つの本文につき1件で済む。

塗り直し（`update_display_text`）の側から拾わない。あちらは1文字ごとに来るので、
どこまでが「同じ本文」かをこちらで判定することになる。合図がある以上、そちらに乗る。

## どこへ残すか

`state/conversation_log/<世界名>.jsonl` に1行1件で**追記**する。

* 置き場が `state/` なのは、遊びの続きに要るものだから（TECH.md §3.11）。
  `out/` は消してよい場所で、掃除のつもりで消した人のログまで飛ぶ
* 追記専用（JSON Lines）にしてあるのは、1件足すたびに全体を書き直さないため。
  途中で落ちても壊れるのは最後の1行だけで、読む側はその行を捨てれば続けられる
* 世界ごとに分けるのは `311_npc_profile_memory` と同じ規則（`world_key`）。
  別の世界のログが混ざらない

`MAX_ENTRIES` 件を超えたぶんは古いほうから落とす。ファイルは追記なので件数の
上限では減らない ― 上限の `COMPACT_RATIO` 倍まで伸びたら、控えているぶんだけを
隣に書いてから差し替える（`os.replace`。書き途中のファイルと入れ替わらない）。

## ボタン

`113_ui_text_expand` が入っていれば**そのすぐ左**に、入っていなければ 113 と同じ
場所（本文の枠の内側・右上）に置く。並べる相手は 113 が HUD に控えている
ボタン（`_instantale_expand_button`）から引く。高さもその相手に合わせるので、
113 側の大きさの設定を変えても2つの高さが揃う。

置いた後は相手の `pos` に束ねる（`follow`）。113 のボタンは枠が伸び縮みすると
動くので、こちらの塗り直しを待っていると1手ぶん遅れて追いかけることになる。

ボタンそのものの作法は 113 と同じ:

* 足す先は HUD ではなく、HUD が持っている `FloatLayout` の中（`ui.overlay_host`）。
  HUD の子を増やすと「画面の最初の子」を取る側から見える相手が変わり、
  アイテムの移動・装備が効かなくなる（VERIFICATION.md §2.33）
* 絵柄は**画像ファイルを持たず** Kivy の canvas に線で描く。どの解像度でも滲まず、
  配布物にバイナリが増えない
* 線を引き直すのは位置・大きさ・絵柄のどれかが変わったときだけ。本文は1文字ずつ
  増えるので、塗り直しは何十回も来る

## 窓

読む窓は `ModalView` を1枚。ゲームのウィジェットを1つも動かさないので、開いて
いる間もゲームの画面はそのまま。書体は本文のラベルから写す（Kivy の既定には
日本語が無く、写さないと豆腐になる）。開いた直後は**いちばん下**（＝最新）を出す。

中身は **Label 1枚ではなく、縦に並べた複数枚**で持つ。Kivy の Label は中身を
1枚のテクスチャに焼くので、GPU の上限（多くの環境で 16384px）を超えた瞬間に
**何も描かれない** ― 実機で 500 件を1枚に入れて窓が空になった（2026-08-10）。
件数が増えれば必ず踏むので、`VIEW_CHUNK_CHARS` ごとの塊に割ってある。

開いている最中に新しい本文が来たら、その場で足す。下まで読んでいたときだけ
下へ追従する（途中を読んでいる人の位置を動かさない）。

## 触らないもの

`hud.display_text`、`app.current_conversation_history`、`app.buttons`、セーブ。
剥がす（`--unload`）とフックは外れるが、足したボタンはゲームのプロセスに残る
（TECH.md §3.10 の「戻らないもの」）。控えたログは `state/` に残る ―
消したいときはフォルダごと消せばよく、ゲームの側には何も残らない。
"""

import datetime
import json
import os
import sys
import weakref

from instantale_modloader import frames, ui
from instantale_modloader.state import world_filename, world_key

# ---------------------------------------------------------------- 利用者設定
# ここの定数は `mod.json` の "settings" と対で持つ（既定値が2箇所にある。
# `tools/check_mods.py` が AST で突き合わせる）。

# ボタンの置き場所。既定は「113 のボタンのすぐ左」で、113 が入っていない
# ときは 113 と同じ場所（本文の枠の内側・右上）へ落ちる。
BUTTON_CORNER = "113の左"

# ボタンの絵柄。背景なしの白い線で描く（画像ファイルは持たない）。
ICON = "本"

# 線の太さ（px）と濃さ（0〜1）。背景に溶けるなら濃さを上げる。
ICON_WIDTH = 2.0
ICON_ALPHA = 0.85

# ボタンの高さ（px）。**113 のボタンの隣に並ぶときは、その相手の高さに合わせる**
# ので使われない（2つの大きさが揃わないと並びが崩れて見える）。
BUTTON_SIZE = 32

# 113 のボタンとの間隔（px）。
BUTTON_GAP = 6

# 控えておく本文の件数。古いものから落ちる。
MAX_ENTRIES = 500

# 窓に日時を出すか。
SHOW_TIME = True

# 窓の縁に白い枠線を引くか。地の色（`VIEW_BG`）だけでは背景との境目が
# 分かりにくい画面があるので、切り替えられるようにしてある。
VIEW_BORDER = True

# 窓の文字の大きさ（本文の何倍）。既定の 1.0 は**ゲームの本文と同じ大きさ**
# （書体と同じく、寸法もゲームの値をそのまま使う。こちらで数字を発明しない）。
FONT_SCALE = 1.0

# ボタンの文字（絵柄に「文字」を選んだときだけ使う）。
LABEL_OPEN = "記録"

# ---------------------------------------------------------------- 実装
LOG_BASENAME = "conversation_log.log"

# 世界ごとの控えを置くフォルダ（`state/` の下）。`311_` と同じ立場のデータで、
# 消すと巻き戻る（読み返せなくなる）。
STATE_DIRNAME = "conversation_log"

# 1件あたりに控える文字数の上限。本文は実測 750 文字前後（GAME.md §2.3）なので
# 通常は掛からない。壊れた値が来たときにファイルが際限なく育つのを止めるだけ。
ENTRY_CHARS = 8000

# ファイルを畳み直す倍率。件数の上限の何倍まで行を溜めてよいか。
COMPACT_RATIO = 3

# 隅ではない置き場所（こちらが座標を入れるもの）。
NEXT_TO_EXPAND = "113の左"
IN_FRAME = "枠の右上"
PLACEMENTS = (NEXT_TO_EXPAND, IN_FRAME)

# 枠の内側に置くときの余白（px。113 の `FRAME_INSET` と同じ値）。
FRAME_INSET = 8.0

# 隅と `pos_hint` の対応。ボタンの作り方ごとローダに集約してある
# （`113_` / `116_` と共有。TECH.md §3.2.3）。
CORNERS = ui.CORNERS

# 絵柄に「文字」を選んだときの呼び名。
AS_TEXT = ui.AS_TEXT

# ラベルから親を何段まで上へたどるか（ラベル → 入れ物 → ScrollView → HUD）。
MAX_UP = 6

# 窓の大きさが変わってから、もう一度置き直すまでの秒数。113 が枠を組み直すのを
# 待つ（あちらも同じ理由で2回当てている）。
RESETTLE_DELAY = 0.35

# 読む窓の見た目。
VIEW_TITLE = "会話ログ"
VIEW_SIZE_HINT = (0.86, 0.86)
VIEW_PAD = 12
VIEW_BG = (0.06, 0.06, 0.08, 0.96)
VIEW_LINE_HEIGHT = 1.5

# 枠線の太さ（px。ゲームの拡縮に乗せる）と濃さ。太さと濃さまで設定に出すと
# 項目が増えるだけなので、切り替えは有無（`VIEW_BORDER`）だけにしてある。
VIEW_BORDER_WIDTH = 1.5
VIEW_BORDER_ALPHA = 0.8
VIEW_CLOSE = "×"
VIEW_EMPTY = "まだ記録がありません。"

# Label 1枚に入れる文字数の上限と、その間隔（px）。**1枚に全部入れない**
# （`view_blocks` の説明。テクスチャの上限を超えると何も描かれない）。
VIEW_CHUNK_CHARS = 1200
VIEW_GAP = 8

# 窓を開くときに組む文字数の上限。あふれるのは古いほう。
VIEW_MAX_CHARS = 200000

# 記録の上限（`out/conversation_log.log` に出す診断の行数）。本文そのものは
# `state/` の側に残るので、こちらは「どこへ置いたか」「拾えたか」だけ。
MAX_LOG = 40

# `113_ui_text_expand` が HUD に控えているボタン。**名指しで引くのはここだけ**で、
# 見つからなければ 113 が入っていない画面として扱う（無ければ無いで成立する）。
EXPAND_BUTTON_ATTR = "_instantale_expand_button"

# こちらがウィジェットに付ける控え（`ui.MOD_WIDGET_PREFIX` に揃える）。
BUTTON_ATTR = ui.MOD_WIDGET_PREFIX + "log_button"
CALLBACK_ATTR = ui.MOD_WIDGET_PREFIX + "log_callback"
ICON_ATTR = ui.MOD_WIDGET_PREFIX + "log_icon"
FOLLOW_ATTR = ui.MOD_WIDGET_PREFIX + "log_follow"
WINDOW_ATTR = ui.MOD_WIDGET_PREFIX + "log_on_resize"

# 世代をまたいで1組だけ持つ控え。`apply()` は1プロセスで何度も走る（TECH.md §3.6）
# ので、注入し直すたびに読み直すとログが二重になり、開いている窓も迷子になる。
STATE_STORE_ATTR = "__instantale_conversation_log__"


def short_time(stamp):
    """控えた時刻を窓に出す形（`2026-08-06T12:34:56` → `08-06 12:34`）。"""
    if not isinstance(stamp, str) or len(stamp) < 16:
        return ""
    return stamp[5:16].replace("T", " ")


def entry_text(entry):
    text = entry.get("text") if isinstance(entry, dict) else None
    return text if isinstance(text, str) else ""


def entry_block(entry):
    """1件ぶんの見出しと本文。"""
    text = entry_text(entry)
    if not text:
        return ""
    head = short_time(entry.get("t")) if SHOW_TIME else ""
    return ("── {} ──\n".format(head) if head else "") + text


def view_blocks(entries):
    """窓に出す本文を、**Label 1枚ぶんずつの塊**にして古い順に返す。

    全部を1枚に入れてはいけない。Kivy の Label は中身を1枚のテクスチャに焼くので、
    GPU の上限（多くの環境で 16384px）を超えた瞬間に**何も描かれない** ―
    実機で 500 件を1枚に入れて窓が空になったのがこれ（2026-08-10）。
    件数が増えるほど確実に踏むので、塊に割って複数枚で持つ。

    それでも組む量には上限を置く（`VIEW_MAX_CHARS`）。件数の上限を大きくした人の
    窓が、開くたびに何十万字を組み直すことにならないように ― あふれるのは
    **古いほう**で、新しい本文は必ず出る。
    """
    blocks = [block for block in (entry_block(entry) for entry in entries) if block]
    kept, total = [], 0
    for block in reversed(blocks):
        total += len(block)
        if kept and total > VIEW_MAX_CHARS:
            break
        kept.append(block)
    kept.reverse()
    chunks, current = [], ""
    for block in kept:
        if current and len(current) + len(block) > VIEW_CHUNK_CHARS:
            chunks.append(current)
            current = block
        else:
            current = block if not current else current + "\n\n" + block
    if current:
        chunks.append(current)
    return chunks or [VIEW_EMPTY]


def apply(ctx):
    # 控えの置き場。**ここで1回だけ引く**（`ctx.state_path` は `out/` に同じ名前が
    # 在ればフォルダごと移してくる。1ファイルずつ引くと、まだ触っていない世界の
    # 控えが `out/` に残る）。
    state_dir = ctx.state_path(STATE_DIRNAME)

    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "buckets": {},    # 世界名 -> 控えた本文（新しいものが後ろ）
            "lines": {},      # 世界名 -> ファイルに在る行数（畳み直しの判断に使う）
            "view": None,     # 開いている窓（Kivy のウィジェット一式）
            "world": None,    # 直前に本文を控えた世界（窓に出す相手）
            "logged": 0,
            "hud": None,
            "watching": False,
            "anchor": None,
        }
        setattr(sys, STATE_STORE_ATTR, store)
    warned = set()

    write = ctx.logger(LOG_BASENAME)

    def note(text):
        if store["logged"] < MAX_LOG:
            store["logged"] += 1
            write(text)

    def warn_once(key, message):
        if key in warned:
            return
        warned.add(key)
        ctx.log("conversation log: " + message, level="WARN")

    def guarded(fn):
        """ボタン・フックから呼ばれる処理。ここで投げるとゲームを巻き込む。"""
        try:
            return fn()
        except Exception:
            ctx.log_exc("conversation log: failed")
            return None

    def schedule(fn, delay=0.0):
        try:
            from kivy.clock import Clock
        except Exception:
            fn()              # ゲームの外（オフライン検証）ではその場で
            return
        try:
            Clock.schedule_once(lambda _dt: fn(), delay)
        except Exception:
            ctx.log_exc("conversation log: could not schedule")

    # -- 世界と控えの読み書き ------------------------------------------------

    def path_for(key):
        # フォルダを作るのはここ（`ctx.state_path` が親を作る）。
        return ctx.state_path(STATE_DIRNAME, world_filename(key, ".jsonl"))

    def load_bucket(key):
        """1世界分の控え。**一度読んだら覚えておく**（書くのはこの MOD だけ）。

        壊れた行（書き込み途中で落ちた最後の1行など）は**その行だけ捨てる**。
        丸ごと諦めると、1行のために全部の記録が消える。
        """
        bucket = store["buckets"].get(key)
        if bucket is not None:
            return bucket
        bucket, lines, broken = [], 0, 0
        path = path_for(key)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    lines += 1
                    try:
                        entry = json.loads(line)
                    except Exception:
                        broken += 1
                        continue
                    if isinstance(entry, dict) and entry_text(entry):
                        bucket.append(entry)
        except FileNotFoundError:
            pass
        except Exception:
            ctx.log_exc("conversation log: cannot read " + path)
        if len(bucket) > MAX_ENTRIES:
            del bucket[:-MAX_ENTRIES]
        store["buckets"][key] = bucket
        store["lines"][key] = lines
        note("loaded {} entr(y/ies) for {!r} from {} ({} line(s), {} broken)".format(
            len(bucket), key, path, lines, broken))
        return bucket

    def append_entry(key, entry):
        """1件を追記する。**全体を書き直さない。**"""
        path = path_for(key)
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            ctx.log_exc("conversation log: cannot append to " + path)
            return
        store["lines"][key] = store["lines"].get(key, 0) + 1
        if store["lines"][key] > MAX_ENTRIES * COMPACT_RATIO:
            compact(key)

    def compact(key):
        """溜まった行を控えているぶんだけに畳み直す。

        素朴に `open(path, "w")` で書くと、書いている最中に落ちた瞬間に記録が
        消える。隣に書いてから差し替える作法は `ctx.write_text()` にある
        （こちらは JSON 文書1つではなく1行1レコードなので `write_json` ではない）。
        """
        path = path_for(key)
        bucket = store["buckets"].get(key) or []
        text = "".join(json.dumps(entry, ensure_ascii=False) + "\n"
                       for entry in bucket)
        if not ctx.write_text(path, text):
            return
        store["lines"][key] = len(bucket)
        note("compacted {} to {} line(s)".format(path, len(bucket)))

    def record(app, text):
        """流れた本文を1件控える。**同じ本文が続けて来たら足さない。**

        打ち出しが途中で作り直される版があっても、同じ文章が2件並ばないように
        しておく（読み返す側にとっては同じ本文が2回出るのは事故に見える）。
        """
        if not isinstance(text, str):
            return
        text = text.strip()
        if not text:
            return
        if len(text) > ENTRY_CHARS:
            text = text[:ENTRY_CHARS]
        key = world_key(app)
        store["world"] = key      # 窓に出すのはこの世界（`entries_for`）
        bucket = load_bucket(key)
        if bucket and entry_text(bucket[-1]) == text:
            return
        entry = {"t": datetime.datetime.now().isoformat(timespec="seconds"),
                 "text": text}
        bucket.append(entry)
        if len(bucket) > MAX_ENTRIES:
            del bucket[:-MAX_ENTRIES]
        append_entry(key, entry)
        refresh_view(key)

    # -- 画面の手がかり ------------------------------------------------------
    def is_label(widget):
        """本文を描けるウィジェットか。**型では見ない**（GAME.md §1.3）。"""
        for name in ("text", "texture_update", "text_size"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return isinstance(frames.attr(widget, "text"), str)

    def label_of(hud):
        """本文のラベル。書体を写す相手であり、枠を探す起点。"""
        named = frames.attr(hud, "text_display")
        return named if named is not frames.MISSING and is_label(named) else None

    def is_scroller(widget):
        for name in ("scroll_y", "do_scroll_y"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return True

    def frame_rect(hud):
        """本文の枠の矩形。見つからなければ None（隅へ落とす）。

        探し方は 113 と同じで、ラベルから親を上へたどって `scroll_y` を持つ最初の
        相手を枠とみなす。**HUD 自身は使わない**（画面全体の矩形になってしまう）。
        """
        label = label_of(hud)
        if label is None:
            return None
        widget = frames.attr(label, "parent")
        box = None
        for _step in range(MAX_UP):
            if widget in (None, frames.MISSING) or widget is hud:
                break
            box = widget          # 見つからなければ HUD の1つ下を枠とみなす
            if is_scroller(widget):
                break
            widget = frames.attr(widget, "parent")
        if box is None:
            return None
        size = frames.attr(box, "size")
        pos = frames.attr(box, "pos")
        try:
            return (float(pos[0]), float(pos[1]), float(size[0]), float(size[1]))
        except (TypeError, ValueError, IndexError):
            return None

    def expand_button(hud):
        """`113_ui_text_expand` が置いたボタン。入っていなければ None。

        画面に載っている（親が居る）ものだけを相手にする。剥がされた後の控えが
        残っていることがあるので、控えの有無だけでは決めない。
        """
        button = frames.attr(hud, EXPAND_BUTTON_ATTR)
        if button in (None, frames.MISSING):
            return None
        if frames.attr(button, "parent") in (None, frames.MISSING):
            return None
        return button

    window_size = ui.window_size
    upx = ui.upx

    def clamp(widget):
        """窓の内側へ寄せる。`pos_hint` を持たない相手にだけ効く。"""
        ui.clamp_into_window(widget)

    # -- 絵柄 ----------------------------------------------------------------
    def strokes():
        """アイコンの線。**0〜1 の座標**で返す（ボタンの大きさに依らない形）。

        画像ファイルは持たない。線で描けば、どの解像度でも滲まず、色も透過も
        こちらで決められる。
        """
        if ICON == "本":
            # 開いた本。中央の綴じ目から左右へページが開く。
            return [[(0.50, 0.30), (0.30, 0.24), (0.10, 0.28),
                     (0.10, 0.70), (0.30, 0.66), (0.50, 0.72)],
                    [(0.50, 0.30), (0.70, 0.24), (0.90, 0.28),
                     (0.90, 0.70), (0.70, 0.66), (0.50, 0.72)],
                    [(0.50, 0.30), (0.50, 0.72)]]
        if ICON == "本（閉じた）":
            # 閉じた本。左端の背表紙だけ内側に線を1本引く。
            return [[(0.26, 0.16), (0.78, 0.16), (0.78, 0.84),
                     (0.26, 0.84), (0.26, 0.16)],
                    [(0.36, 0.16), (0.36, 0.84)]]
        if ICON == "一覧":
            return [[(0.20, 0.72), (0.80, 0.72)],
                    [(0.20, 0.50), (0.80, 0.50)],
                    [(0.20, 0.28), (0.80, 0.28)]]
        if ICON == "吹き出し":
            return [[(0.16, 0.72), (0.84, 0.72), (0.84, 0.36),
                     (0.44, 0.36), (0.28, 0.18), (0.28, 0.36),
                     (0.16, 0.36), (0.16, 0.72)]]
        return []

    def paint_icon(button):
        """ボタンにアイコンを描き直す。**変わったときだけ**（毎フレーム描かない）。

        引き直すかの判定はローダ側（`ui.paint_icon`）に集約してある
        （`113_` / `116_` と共有）。**向きは持たない** ― この MOD の絵柄は
        押しても反転しないので、控えに `expanded` を混ぜない。
        """
        if ICON == AS_TEXT:
            return
        ui.paint_icon(button, strokes(), attr=ICON_ATTR, key=(ICON,),
                      width=ICON_WIDTH, alpha=ICON_ALPHA,
                      log_exc=lambda msg: ctx.log_exc("conversation log: " + msg))

    # -- ボタン --------------------------------------------------------------
    def make_button(hud, label):
        """ボタンを1枚作る（背景消し・フォント写し・大きさはローダの作法）。"""
        button = ui.make_icon_button(
            text="" if ICON != AS_TEXT else LABEL_OPEN,
            size=BUTTON_SIZE, square=(ICON != AS_TEXT),
            font_name=frames.text_of(label, "font_name"),
            pos_hint=(dict(CORNERS.get(BUTTON_CORNER, {}))
                      if BUTTON_CORNER not in PLACEMENTS else {}))
        if button is None:
            warn_once("button",
                      "kivy Button unavailable; no log button will be shown")
        return button

    def match_size(button, other):
        """並べる相手と高さを揃える。**相手の設定に付いていく。**

        113 のボタンの大きさは向こうの設定で決まる。こちらが自分の設定で描くと、
        利用者が向こうを大きくした瞬間に2つの高さが食い違う。
        """
        size = frames.attr(other, "size")
        try:
            height = float(size[1])
        except (TypeError, ValueError, IndexError):
            return
        if height <= 0:
            return
        try:
            button.height = height
            button.width = height if ICON != AS_TEXT else height * 2.0
            button.font_size = height * 0.45
        except Exception:
            ctx.log_exc("conversation log: could not match the neighbour's size")

    def place_beside(button, other):
        """相手のすぐ左へ。下端を揃える（並んで見えるのは下端が同じとき）。"""
        try:
            button.pos_hint = {}
            button.x = float(other.x) - float(button.width) - upx(BUTTON_GAP)
            button.y = float(other.y)
        except Exception:
            ctx.log_exc("conversation log: could not place the button")
            return False
        clamp(button)
        return True

    def follow(button, other):
        """相手が動いたら付いていく。**塗り直しを待たない。**

        113 のボタンは本文の枠が伸び縮みすると動く（枠の内側に置く設定のとき）。
        こちらの塗り直しを待つと1手ぶん遅れて追いかけることになるので、相手の
        `pos` / `size` に直に束ねる。注入し直したときは前の手を外してから結ぶ。
        """
        previous = frames.attr(other, FOLLOW_ATTR, None)
        if previous is not None:
            try:
                other.unbind(pos=previous, size=previous)
            except Exception:
                pass          # 外せなくても、次に結ぶ手が同じことをする

        def moved(*_args):
            guarded(lambda: (match_size(button, other),
                             place_beside(button, other),
                             paint_icon(button)))

        try:
            other.bind(pos=moved, size=moved)
            setattr(other, FOLLOW_ATTR, moved)
        except Exception:
            pass              # 束ねられない相手なら塗り直しのたびに置き直す

    def place_button(hud, button):
        """置き直す。塗り直しのたびに呼ぶ（113 のボタンも枠も動くことがある）。"""
        if BUTTON_CORNER not in PLACEMENTS:
            paint_icon(button)
            return
        if BUTTON_CORNER == NEXT_TO_EXPAND:
            other = expand_button(hud)
            if other is not None:
                match_size(button, other)
                if place_beside(button, other):
                    follow(button, other)
                    paint_icon(button)
                    remember("beside 113", frames.attr(button, "pos", ()))
                    return
        # 113 が入っていない（または「枠の右上」を選んだ）。113 と同じ場所 ＝
        # 本文の枠の内側・右上へ。枠が伸びれば一緒に上がる。
        rect = frame_rect(hud)
        if rect is not None:
            inset = upx(FRAME_INSET)
            try:
                button.pos_hint = {}
                button.x = rect[0] + rect[2] - button.width - inset
                button.y = rect[1] + rect[3] - button.height - inset
            except Exception:
                ctx.log_exc("conversation log: could not place the button in the frame")
                return
            clamp(button)
            paint_icon(button)
            remember("frame", frames.attr(button, "pos", ()))
            return
        # 手がかりが無い画面（会話前・戦闘中など）では隅に落とす。
        if not frames.attr(button, "pos_hint", None):
            button.pos_hint = dict(CORNERS["右上"])
        paint_icon(button)
        remember("corner", ())

    def remember(how, pos):
        """置き場所が変わったときだけ記録する（毎フレームは書かない）。"""
        try:
            where = (how, tuple(round(float(value)) for value in pos))
        except (TypeError, ValueError):
            where = (how, ())
        if store.get("anchor") != where:
            store["anchor"] = where
            note("button placed by the {} at {}".format(how, where[1]))

    def ensure_button(hud):
        """ボタンを1枚だけ足す。既にあれば押下先だけ今の注入へ付け替える。"""
        label = label_of(hud)
        if label is None:
            warn_once("label", "no label is showing the narration; "
                               "the log button has nothing to sit next to")
            return
        host = ui.overlay_host(hud)
        button = frames.attr(hud, BUTTON_ATTR)
        if button in (None, frames.MISSING):
            button = make_button(hud, label)
            if button is None:
                return
            setattr(hud, BUTTON_ATTR, button)
        parent = frames.attr(button, "parent")
        if parent is not host:
            if parent not in (None, frames.MISSING):
                try:
                    parent.remove_widget(button)
                except Exception:
                    ctx.log_exc("conversation log: could not detach the button")
            try:
                host.add_widget(button)       # 既定は先頭挿入＝一番上に描かれる
            except Exception:
                ctx.log_exc("conversation log: could not add the button")
                return
            note("button added to {} at {}".format(type(host).__name__, BUTTON_CORNER))

        # 押下先の付け替え。注入し直したとき、古い注入の窓を開き続けないため。
        previous = frames.attr(button, CALLBACK_ATTR)
        if previous is not frames.MISSING and previous is not None:
            try:
                button.unbind(on_release=previous)
            except Exception:
                pass

        def callback(_instance=None, _hud=hud):
            return guarded(lambda: toggle_view(_hud))

        try:
            button.bind(on_release=callback)
            setattr(button, CALLBACK_ATTR, callback)
        except Exception:
            ctx.log_exc("conversation log: could not bind the button")
            return
        place_button(hud, button)

    # -- 読む窓 --------------------------------------------------------------
    def soft_set(widget, name, value):
        """持っていないプロパティは飛ばす（Kivy の版差でここが割れる）。"""
        try:
            setattr(widget, name, value)
        except Exception:
            pass

    def entries_for(hud):
        """窓に出す控えと、その世界の名前。

        **直前に控えた世界を先に見る。** 走っているアプリを探し直しても同じ答えに
        なるはずだが、こちらは「いま書いている相手」そのものなので取り違えない
        （`ui.find_app` はゲームの外では相手を見つけられない）。
        """
        key = store.get("world")
        if not key:
            app = ui.find_app()
            key = world_key(app) if app is not None else "_"
        return key, load_bucket(key)

    def toggle_view(hud):
        if store.get("view") is not None:
            close_view()
            return
        open_view(hud)

    def close_view():
        view = store.get("view")
        store["view"] = None
        if not isinstance(view, dict):
            return
        try:
            view["view"].dismiss()
        except Exception:
            ctx.log_exc("conversation log: could not close the window")

    def open_view(hud):
        """読む窓を1枚開く。**ゲームのウィジェットは1つも動かさない。**"""
        try:
            from kivy.uix.modalview import ModalView
            from kivy.uix.boxlayout import BoxLayout
            from kivy.uix.button import Button
            from kivy.uix.label import Label
            from kivy.uix.scrollview import ScrollView
            from kivy.graphics import Color, Line, Rectangle
        except Exception:
            warn_once("view", "kivy is not available; the log window cannot open")
            return
        key, entries = entries_for(hud)
        label = label_of(hud)
        font_name = frames.attr(label, "font_name") if label is not None else None
        font_size = frames.attr(label, "font_size") if label is not None else None
        try:
            font_size = float(font_size) * float(FONT_SCALE)
        except (TypeError, ValueError):
            font_size = None

        view = ModalView(size_hint=VIEW_SIZE_HINT, auto_dismiss=True)
        # 後ろを暗くする色の名前は版によって違う（`background_color` /
        # `overlay_color`）。持っているほうにだけ効かせる。**`background`
        # （画像）は触らない** ― 空にすると読み込みに失敗する版がある。
        # 窓そのものの地の色は中の箱で描くので、これは後ろの暗さの話。
        soft_set(view, "background_color", (0, 0, 0, 0.5))
        soft_set(view, "overlay_color", (0, 0, 0, 0.5))

        root = BoxLayout(orientation="vertical", padding=upx(VIEW_PAD),
                         spacing=upx(VIEW_PAD))
        with root.canvas.before:
            Color(*VIEW_BG)
            panel = Rectangle(pos=root.pos, size=root.size)
        # 枠線は**地の色の後**（`canvas.after`）に引く。`before` に混ぜると、
        # 中身より先に描かれて内側の線が本文に隠れる。ゲームの `add_border` は
        # 借りない ― あちらの色はゲームの意匠のもので、白とは限らない。
        frame = None
        if VIEW_BORDER:
            with root.canvas.after:
                Color(1, 1, 1, float(VIEW_BORDER_ALPHA))
                frame = Line(rectangle=(root.x, root.y, root.width, root.height),
                             width=upx(VIEW_BORDER_WIDTH))

        def redraw(*_args):
            panel.pos, panel.size = root.pos, root.size
            if frame is not None:
                frame.rectangle = (root.x, root.y, root.width, root.height)

        root.bind(pos=redraw, size=redraw)

        head_height = upx(BUTTON_SIZE)
        header = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=head_height, spacing=upx(VIEW_PAD))
        title = Label(text=VIEW_TITLE, halign="left", valign="middle")
        close = Button(text=VIEW_CLOSE, size_hint=(None, None),
                       size=(head_height, head_height))
        for widget in (title, close):
            if isinstance(font_name, str) and font_name:
                soft_set(widget, "font_name", font_name)
        if font_size:
            soft_set(title, "font_size", font_size)
        # 閉じるボタンの字だけは**枠の高さから決める**。本文と同じ大きさにすると
        # （既定はゲームの本文そのままなので）字が枠に収まらないことがある。
        soft_set(close, "font_size", head_height * 0.5)
        title.bind(size=lambda instance, value: setattr(instance, "text_size", value))
        close.bind(on_release=lambda _instance: guarded(close_view))
        header.add_widget(title)
        header.add_widget(close)

        # 本文は**Label 1枚ではなく縦に並べた複数枚**で持つ（`view_blocks`）。
        # 縦の BoxLayout は先に足したものが上に来るので、古い順に足せばそのまま
        # 上から古い順に並ぶ。高さは中身（`minimum_height`）が決める。
        scroll = ScrollView(do_scroll_x=False)
        column = BoxLayout(orientation="vertical", size_hint_y=None,
                           padding=upx(VIEW_PAD), spacing=upx(VIEW_GAP))
        column.bind(minimum_height=lambda instance, value: setattr(
            instance, "height", value))
        scroll.add_widget(column)

        root.add_widget(header)
        root.add_widget(scroll)
        view.add_widget(root)
        view.bind(on_dismiss=lambda *_args: store.update({"view": None}))
        opened = {"view": view, "column": column, "scroll": scroll, "key": key,
                  "font_name": font_name, "font_size": font_size, "chunks": [],
                  "empty": not entries}
        store["view"] = opened
        chunks = view_blocks(entries)
        for chunk in chunks:
            add_chunk(opened, chunk)
        try:
            view.open()
        except Exception:
            store["view"] = None
            ctx.log_exc("conversation log: could not open the window")
            return
        # 開いた直後はいちばん下（＝最新）を出す。中身の高さが決まるのは
        # 次のフレームなので、そこで寄せる。
        schedule(lambda: soft_set(scroll, "scroll_y", 0))
        note("opened the window with {} entr(y/ies) for {!r} in {} label(s)".format(
            len(entries), key, len(chunks)))

    def add_chunk(opened, text):
        """本文の塊を1枚の Label にして窓の下へ足す。

        折り返し幅は**自分の幅**から決める（親の幅に `size_hint_x=1` で従うので、
        入れ物の寸法を別途たどらなくてよい）。高さは中身が決める。
        """
        try:
            from kivy.uix.label import Label
        except Exception:
            return None
        label = Label(text=text, markup=False, halign="left", valign="top",
                      size_hint_y=None)
        font_name, font_size = opened.get("font_name"), opened.get("font_size")
        if isinstance(font_name, str) and font_name:
            soft_set(label, "font_name", font_name)
        if font_size:
            soft_set(label, "font_size", font_size)
        soft_set(label, "line_height", VIEW_LINE_HEIGHT)
        label.bind(width=lambda instance, value: setattr(
            instance, "text_size", (value, None)))
        label.bind(texture_size=lambda instance, value: setattr(
            instance, "height", value[1]))
        column = opened["column"]
        label.text_size = (column.width, None)
        column.add_widget(label)
        opened["chunks"].append(label)
        return label

    def refresh_view(key):
        """開いている窓に**来たぶんだけ**足す。読んでいる位置は動かさない。

        全部を組み直さないのは、窓を開いたまま遊べる限り本文は何度も来るから。
        最後の塊に入るならそこへ継ぎ足し、あふれるなら次の1枚にする。

        下まで読んでいた（`scroll_y` が 0 付近）ときだけ下へ追う。途中を読んで
        いる人の位置を動かすと、本文が来るたびに読みかけの行が飛ぶ。
        """
        opened = store.get("view")
        if not isinstance(opened, dict) or opened.get("key") != key:
            return
        entries = store["buckets"].get(key) or []
        block = entry_block(entries[-1]) if entries else ""
        if not block:
            return
        scroll = opened["scroll"]
        try:
            at_bottom = float(frames.attr(scroll, "scroll_y", 0.0)) <= 0.01
            if opened.get("empty"):
                # 「まだ記録がありません」を出していた窓。1件目が来たので外す。
                for label in opened["chunks"]:
                    opened["column"].remove_widget(label)
                del opened["chunks"][:]
                opened["empty"] = False
            last = opened["chunks"][-1] if opened["chunks"] else None
            if last is not None and len(last.text) + len(block) <= VIEW_CHUNK_CHARS:
                last.text = last.text + "\n\n" + block
            else:
                add_chunk(opened, block)
        except Exception:
            ctx.log_exc("conversation log: could not refresh the window")
            return
        if at_bottom:
            schedule(lambda: soft_set(scroll, "scroll_y", 0))

    # -- 窓の大きさが変わったとき --------------------------------------------
    def on_window_resize(*_args):
        reference = store.get("hud")
        hud = reference() if reference is not None else None
        if hud is None:
            return
        # 113 は窓が変わると枠を組み直し、**次のフレームで**ボタンを置き直す。
        # その場で並べると古い座標の隣に付くので、こちらも次のフレームに回し、
        # レイアウトが1フレームで終わらないビルドのためにもう一度当てる。
        schedule(lambda: guarded(lambda: upkeep(hud)))
        schedule(lambda: guarded(lambda: upkeep(hud)), RESETTLE_DELAY)

    def watch_window():
        if store["watching"]:
            return
        try:
            from kivy.core.window import Window
        except Exception:
            return            # ゲームの外（オフライン検証）では窓が無い
        store["watching"] = True
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
            store["watching"] = False
            ctx.log_exc("conversation log: could not watch the window size")

    def upkeep(hud):
        store["hud"] = weakref.ref(hud)
        watch_window()
        ensure_button(hud)

    # -- フック --------------------------------------------------------------
    # 本文の打ち出しが始まったところ。`index == -1` が「新しい本文」の合図で、
    # `context` がその全文（GAME.md §2.3）。**控えてからゲームに渡す** ―
    # 打ち出しの側で何が起きても、流れた本文は残る。
    @ctx.wrap("__main__:InstantaleApp.add_text_display", required=False, safe=True)
    def add_text_display(orig, self, dt, context, index=-1):
        if index == -1:
            guarded(lambda: record(self, context))
        return orig(self, dt, context, index)

    # ボタンを保つ。どちらも塗り直しの合図で、ゲームに先に仕事をさせてから触る。
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

    ctx.log("conversation log: keeping the last {} message(s) in {} ({} button, "
            "{} icon); notes go to out/{}".format(
                MAX_ENTRIES, state_dir, BUTTON_CORNER, ICON, LOG_BASENAME))
