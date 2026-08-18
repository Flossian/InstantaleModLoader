# -*- coding: utf-8 -*-
"""計測: 本文の表示速度が設定どおりに変わらない原因を測る（読み取り専用）。

発端の症状: テキスト表示速度の設定を変えても速さが変わらない。
原因の候補は 3つあり、対策が別々になるので、どれなのかを測って決める:

1. 設定が `app.text_speed` に届いていない。
   このとき値そのものが動かない。
2. `app.text_speed` は動くが、ゲームがそれを間隔に使っていない。
   （1ティックで進める文字数を変えている、など）
3. 1文字ごとの処理が重くて、設定より遅いところで頭打ちになっている。
   本文が1文字進むたびに `update_display_text` が走り、
   そこに**いまは5本の MOD が積まれている**（`117_` / `112_` / `113_` / `114_` /
   `116_`）。
   1文字あたりの処理時間が設定の間隔を超えると、
   速い設定はどれも「1フレーム1文字」に潰れる。

## 分かっていること（GAME.md §2.3 / VERIFICATION_LOG.md §2.34）

計測は決着済みで、MOD は無関係だった。
設定は届いており、1ティック＝1文字、間隔は `text_speed` どおり。
積まれた MOD の代金は1文字あたり 0.3ms（間隔の 0.6%）。
残った差はフレームそのものの遅れで、Clock は予約時刻を過ぎた最初のフレームで呼ぶため、フレームが落ちれば
`text_speed` どおりには呼べない。

この MOD はその見張りとして残してある。
フレームレート（`Clock.get_fps()`）と、
本文のラベルのテクスチャの作り直し（`kivy.uix.label:Label.texture_update` の回数と時間）まで数えるので、
遅くなったときにフックの中と外を切り分けられる。

    1回/ティック    Kivy 本来の作り直し。重ければ本文の大きさの問題
    2回以上/ティック 誰かが余計に作り直している（`settle()` の `texture_update()` など）

## 測るもの

| 測るもの | 取り方 |
|---|---|
| 設定値 | `app.text_speed`。ティックのたびと、1秒ごとの見張りの両方で読む |
| 実際のティック間隔 | `add_text_display` の呼び出し間隔と、Kivy が渡す `dt` |
| 1ティックで進む文字数 | `update_display_text` に来る `value` の長さの差 |
| 1文字ぶんの塗り直しの重さ | `update_display_text` の中の `orig(...)` の実時間 |

最後が要点。
この MOD は適用順が一番外側なので（計測は修正より外側。TECH.md §3.2）、
`orig(...)` の中に**ゲーム本体と、内側に積まれた MOD 全部**が入る。
つまり「1文字進めるのに、いまの構成で実際に何ミリ秒かかっているか」がそのまま出る。

読み方:

    text_speed が動かない                      → 候補1（設定が届いていない）
    text_speed は動くが interval が動かない     → 候補2か3。chars/tick を見る
      ├ chars/tick が動く                      → 候補2（間隔ではなく文字数で調節）
      └ repaint ≒ interval                     → 候補3（重さで頭打ち）

候補3だったときは、`update_display_text` に積まれた MOD を GUI から1本ずつ切って、
`repaint avg` がどれで下がるかを見れば犯人が決まる。

この MOD は観測しかしない。
値は変えず、記録に失敗しても本体は必ず呼ぶ。
"""

import datetime
import time

from instantale_modloader import frames

LOG_BASENAME = "text_speed.log"

# 最初の何ティックを1行ずつ出すか。
# **要約を待たずに間隔が読める**ようにするため（要約だけにすると、
# 短いメッセージでは1行も出ないまま終わる）。
DETAIL_TICKS = 40

# その後、ティック何回ごとに要約を1行書くか。
SUMMARY_EVERY = 60

# これ以上ティックが空いたら「別のメッセージ」として要約を締める（秒）。
# 打ち終わりで必ず1行出るので、短いメッセージでも数字が残る。
IDLE_GAP = 1.0

# `app.text_speed` を見張る間隔（秒）。
# 本文が出ていなくても設定の変化を拾う。
POLL_SECONDS = 1.0

# フレームレートがこれだけ動いたら1行残す。
FPS_DELTA = 3.0

# 本文の長さがこれだけ動いたら1行残す。
LEN_DELTA = 500

# 何も動かなくても、この秒数ごとには1行残す（そのときの状態が分かるように）。
HEARTBEAT_SECONDS = 30.0

# 1文字あたりこのミリ秒を超えた塗り直しは、
# 要約とは別に残す（何が重いかの手がかり）。
SLOW_MS = 20.0

# 遅い回を残す上限。
MAX_SLOW = 40


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 計測の途中経過。
    # 要約系のカウンタは flush() が 0 に戻す。
    state = {
        "speed": None,        # 最後に見た app.text_speed
        "prev": None,         # 直前のティックの時刻
        "seen": 0,            # 通算ティック数（DETAIL_TICKS の判定用）
        "ticks": 0, "gap_sum": 0.0, "gap_max": 0.0,
        "dt_sum": 0.0, "dt_max": 0.0,
        "paints": 0, "paint_sum": 0.0, "paint_max": 0.0,
        "chars": 0, "char_max": 0, "len_prev": None,
        "render_n": 0, "render_sum": 0.0, "render_max": 0.0,
        "slow": 0,            # 遅い塗り直しを何回書いたか（MAX_SLOW まで）
        "label": None,        # 計測対象の本文ラベル
        "fps": None, "label_len": None, "beat": 0.0,
    }

    def speed_text():
        speed = state["speed"]
        return "{:.4f}".format(speed) if isinstance(speed, float) else repr(speed)

    def fps_now():
        """Kivy が数えているフレームレート。読めなければ None。

        ティックの間隔はこれに縛られる。
        Clock は予約した時刻を過ぎた最初のフレームで呼ぶので、
        フレームが遅ければ `text_speed` どおりには呼べない。
        """
        try:
            from kivy.clock import Clock
            return float(Clock.get_fps())
        except Exception:
            return None

    def flush(reason=""):
        """要約を1行。平均と最大の両方を出す（頭打ちは最大に出る）。"""
        if not state["ticks"]:
            return
        ticks = state["ticks"]
        paints = state["paints"] or 1
        fps = fps_now()
        label = state["label"]
        texture = frames.attr(label, "texture_size") if label is not None else None
        write("tick x{}{}  text_speed={}  interval avg={:.1f}ms max={:.1f}ms  "
              "clock dt avg={:.1f}ms max={:.1f}ms  repaint avg={:.1f}ms max={:.1f}ms  "
              "chars/tick avg={:.2f} max={}  fps={}  "
              "render x{:.2f}/tick avg={:.1f}ms max={:.1f}ms  texture={}"
              .format(ticks, " ({})".format(reason) if reason else "", speed_text(),
                      state["gap_sum"] / ticks * 1000.0, state["gap_max"] * 1000.0,
                      state["dt_sum"] / ticks * 1000.0, state["dt_max"] * 1000.0,
                      state["paint_sum"] / paints * 1000.0, state["paint_max"] * 1000.0,
                      state["chars"] / float(ticks), state["char_max"],
                      "{:.1f}".format(fps) if fps is not None else "?",
                      state["render_n"] / float(ticks),
                      state["render_sum"] / (state["render_n"] or 1) * 1000.0,
                      state["render_max"] * 1000.0,
                      frames.repr_value(texture)))
        # 判定に使う材料はここまで。
        # 結論はログを読む側が出す（推測を書かない）。
        for key in ("ticks", "gap_sum", "gap_max", "dt_sum", "dt_max",
                    "paint_sum", "paint_max", "paints", "chars", "char_max",
                    "render_n", "render_sum", "render_max"):
            state[key] = 0 if isinstance(state[key], int) else 0.0

    def note_speed(app, source):
        """`app.text_speed` を読む。変わっていたら1行残す（設定が届いた証拠）。"""
        speed = frames.attr(app, "text_speed")
        if speed is frames.MISSING:
            speed = None
        if speed == state["speed"]:
            return
        # 締めるのが先。
        # 後にすると、
        # 前の設定で測った分に新しい設定値のラベルが付いてしまう（設定と速さの対応を読むためのログなので致命的）。
        flush("speed changed")
        write("text_speed {!r} -> {!r} ({})".format(state["speed"], speed, source))
        state["speed"] = speed

    # -- フレームと本文の大きさ ----------------------------------------------
    def note_frame(app):
        """フレームレートと、いま描いている本文の大きさを残す。

        **ワールドによって速さが違う**（同じ `text_speed` でも 46ms と 73ms。
        VERIFICATION_LOG.md §2.34）ので、
        その差が「ゲーム全体が遅い」のか「本文が大きい」のかを分ける。

        本文の大きさを見るのは、
        ラベルの**テクスチャの作り直しは次のフレームで走る**ため。
        `update_display_text` の中の計測（`repaint`）には出てこず、
        フレーム時間のほうに乗る。
        長い本文ほど1文字ごとの作り直しが重くなる。

        変化があったときだけ書く（1秒ごとに同じ行を出しても読めない）。
        """
        fps = fps_now()
        hud = frames.attr(app, "hud")
        label = frames.attr(hud, "text_display") if hud not in (None, frames.MISSING) else None
        if label not in (None, frames.MISSING):
            state["label"] = label      # 本文が出ていなくても計測対象を掴んでおく
        text = frames.attr(label, "text", "") if label not in (None, frames.MISSING) else ""
        length = len(text) if isinstance(text, str) else 0
        texture = frames.attr(label, "texture_size") if label not in (None, frames.MISSING) else None

        now = time.perf_counter()
        moved = (
            (fps is not None and state["fps"] is not None and abs(fps - state["fps"]) >= FPS_DELTA)
            or (state["label_len"] is None or abs(length - state["label_len"]) >= LEN_DELTA)
            or (now - state["beat"] >= HEARTBEAT_SECONDS)
        )
        if not moved:
            return
        state["fps"], state["label_len"], state["beat"] = fps, length, now
        write("frame fps={}  label len={}  texture={}  text_speed={}".format(
            "{:.1f}".format(fps) if fps is not None else "?",
            length, frames.repr_value(texture), speed_text()))

    # -- 設定の見張り（本文が出ていなくても拾う） ----------------------------
    # 注入し直したときに新しい見張りへ入れ替わるようにする。
    # `ctx.on_ready(start_poll)` だけでは、注入し直しても見張りが立ち上がらない。
    # `on_ready` の印はプロセスに残るので2回目は黙って捨てられ、**古い版の見張りが回り続ける**（TECH.md
    # §3.6）。
    # ログが1行も増えず、「ゲームが何も出していない」と読み違えることになる。
    # 対策は2つ組。
    # 印を毎回別のキーにして必ず積み、古い見張りには `ctx.superseded()` で自分から降りてもらう（`revert_all` は
    # Clock の予約を取り消せない。TECH.md の `unload()` の但し書き）。
    # 世代の持ち回りはローダの仕事なので、
    # `sys` に合言葉を置くのはやめた（TECH.md §3.6.1）。

    def start_poll():
        """1秒ごとに `app.text_speed` とフレームの様子を読む。

        メインスレッドで動かすため
        `on_ready` 経由で仕掛ける（`boot()` 自体は注入したリモートスレッドの上で走っている）。
        """
        try:
            from kivy.app import App
            from kivy.clock import Clock
        except Exception:
            ctx.log("text speed probe: no kivy Clock; polling disabled", level="WARN")
            return

        def poll(_dt):
            # 自分より新しい注入が来ていたら降りる（False で Clock から外れる）。
            if ctx.superseded():
                write("polling stopped (a newer injection took over)")
                return False
            try:
                app = App.get_running_app()
                if app is None:
                    return True
                note_speed(app, "poll")
                note_frame(app)
            except Exception:
                ctx.log_exc("text speed probe: polling failed")
            return True

        Clock.schedule_interval(poll, POLL_SECONDS)
        write("polling app.text_speed every {}s (gen {})".format(
            POLL_SECONDS, ctx.generation))

    # キーに世代を混ぜる。
    # 混ぜないと2回目以降の注入で積まれない（上の説明）。
    ctx.on_ready(start_poll,
                 key="211_probe_text_speed:poll:{}".format(ctx.generation))

    # -- ゲームの打ち出し（1ティック） ---------------------------------------
    @ctx.wrap("__main__:InstantaleApp.add_text_display", required=False, safe=True)
    def add_text_display(orig, self, dt=None, context=None, *args, **kwargs):
        now = time.perf_counter()
        try:
            note_speed(self, "tick")
            gap = None
            if state["prev"] is not None:
                gap = now - state["prev"]
                # 間が空いた ＝ 別のメッセージ。
                # 前のぶんはここで締める（短いメッセージでも要約が残るように）。
                if gap > IDLE_GAP:
                    flush("idle")
                else:
                    state["gap_sum"] += gap
                    state["gap_max"] = max(state["gap_max"], gap)
                    state["ticks"] += 1
            state["prev"] = now
            # Kivy が渡す dt は「前回の予約から実際に経った時間」。
            # 設定どおりに呼べているかは、
            # こちらと間隔の両方を並べたほうが確かめやすい。
            if isinstance(dt, float):
                state["dt_sum"] += dt
                state["dt_max"] = max(state["dt_max"], dt)
            state["seen"] += 1
            # 最初の数十回は1行ずつ。
            # 要約を待たずに間隔が読める。
            if state["seen"] <= DETAIL_TICKS:
                write("tick #{}  gap={}  dt={}  text_speed={}".format(
                    state["seen"],
                    "-" if gap is None else "{:.1f}ms".format(gap * 1000.0),
                    "{:.1f}ms".format(dt * 1000.0) if isinstance(dt, float) else repr(dt),
                    speed_text()))
            elif state["ticks"] >= SUMMARY_EVERY:
                flush()
        except Exception:
            ctx.log_exc("text speed probe: tick bookkeeping failed")
        return orig(self, dt, context, *args, **kwargs)

    # -- ラベルのテクスチャの作り直し（フレーム側の代金） -----------------
    # ここが `repaint` に出てこない残りの仕事。
    # Kivy はテキストが変わると次のフレームでテクスチャを作り直すので、
    # `update_display_text` の中の計測には出ず、フレーム時間のほうに乗る。
    # 1文字ごとに 1340x2800 を作り直していればそれだけでフレームが落ちる。
    # **1ティックあたり何回・何ミリ秒**かを数える。
    @ctx.wrap("kivy.uix.label:Label.texture_update", required=False, safe=True)
    def texture_update(orig, self, *args, **kwargs):
        if self is not state["label"]:
            # 本文のラベル以外（ボタン・状態表示）は数えない。
            # 計測の対象は「1文字ごとに作り直している大きいラベル」だけ。
            return orig(self, *args, **kwargs)
        start = time.perf_counter()
        result = orig(self, *args, **kwargs)
        spent = time.perf_counter() - start
        state["render_n"] += 1
        state["render_sum"] += spent
        state["render_max"] = max(state["render_max"], spent)
        return result

    # -- 1文字ぶんの塗り直し（ゲーム＋内側の MOD 全部） -----------------------
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        # テクスチャの計測対象を覚える（属性を1回読むだけ）。
        label = frames.attr(self, "text_display")
        if label is not frames.MISSING and label is not None:
            state["label"] = label
        start = time.perf_counter()
        result = orig(self, instance, value, *args, **kwargs)
        spent = time.perf_counter() - start
        try:
            state["paint_sum"] += spent
            state["paint_max"] = max(state["paint_max"], spent)
            state["paints"] += 1
            # 1回でどれだけ本文が伸びたか。
            # **間隔ではなく文字数で速さを調節している**場合、
            # 設定を変えるとここが動く（間隔は動かない）。
            if isinstance(value, str):
                grown = len(value) - (state["len_prev"] or 0)
                if 0 < grown < 10000:      # 本文の入れ替わり（減る・飛ぶ）は数えない
                    state["chars"] += grown
                    state["char_max"] = max(state["char_max"], grown)
                state["len_prev"] = len(value)
            if spent * 1000.0 >= SLOW_MS and state["slow"] < MAX_SLOW:
                state["slow"] += 1
                write("slow repaint {:.1f}ms  len(text)={}  text_speed={}".format(
                    spent * 1000.0,
                    len(value) if isinstance(value, str) else "?", speed_text()))
        except Exception:
            ctx.log_exc("text speed probe: repaint bookkeeping failed")
        return result

    # -- ゲームが既定として計算している速さ ------------------------------------
    @ctx.wrap("scripts.functions:get_default_text_speed_for_language",
              required=False, safe=True)
    def get_default_text_speed_for_language(orig, language=None, *args, **kwargs):
        result = orig(language, *args, **kwargs)
        write("get_default_text_speed_for_language({!r}) -> {!r}".format(language, result))
        return result

    # 誰が同じ場所に乗っているかを1回だけ残す。
    # 犯人を絞るときの出発点になる。
    stacked = (ctx.patches() or {}).get(
        "scripts.hud.new_hud:InstanTaleHUD.update_display_text") or []
    write("probe installed; update_display_text is wrapped by: {}".format(
        ", ".join(stacked) or "(nothing else)"))
    ctx.log("text speed probe: measuring the typewriter; results go to out/{}".format(
        LOG_BASENAME))
