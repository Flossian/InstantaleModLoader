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
| 設定値 | `app.text_speed`。ティックのたびに読む（版5までは1秒ごとの見張りでも読んでいた） |
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

版6: 見張りは残し、常駐の手間とログを絞った。

- 1秒ごとの見張り（Clock）をやめた。
  版5は変化が無くても 30秒ごとに `frame fps=…` を1行書き、fps が 3 動くたびにも書いていた。
  遊んでいない間も書き続けて、1か月でこのログの約65%（9MB あまり）がこの行だった。
  fps と本文のテクスチャの大きさは `tick` の要約に載っている。
  `text_speed` の変化はティックのたびに読んでいるので、次のティックで1行出る
- `chars/tick` の平均の分母を最大と揃えた。
  版5は、本文が伸びた回の文字数の合計を、アイドル明けの1回を除いたティック数で割っていた。
  平均が最大を上回る行があった（12,189行中 195行）。
  いまは本文が伸びた回の数で割る
- 同じ瞬間に続けて来るティック（間隔 1ms 未満。打ち切りが残りを一度に流す塊）は数えない。
  要約の末尾に `burst xN` として件数だけ出す。
  版5では、間隔 0.0ms の要約が 699行あった
- `Label.texture_update` の包みは safe=True をやめ、素の包みにした。
  全部の Label が通る場所で、safe=True の層は呼び出しのたびに器と閉包を作っていた。
  本文のラベル以外は判定1つで素通しにし、記録は try の中に置く
- 最初の `DETAIL_TICKS` 回の1行ずつの記録は1プロセスに1回にした（版5は注入のたびに出ていた）。
  `get_default_text_speed_for_language` は同じ答えを1プロセスに1回だけ書く

版7: 包み3本が引数を名前で受けて位置で渡し直していたのをやめ、受け取った形のまま本体へ渡す。
`probe installed` の行は、積み順が前回と変わったときだけ書く（版6までは注入のたびに出ていた）。
"""

import sys
import time

from instantale_modloader import frames

LOG_BASENAME = "text_speed.log"

# 最初の何ティックを1行ずつ出すか。
# **要約を待たずに間隔が読める**ようにするため（要約だけにすると、
# 短いメッセージでは1行も出ないまま終わる）。
# 版6で1プロセスに1回にした（数えるのは `sys` の DETAIL_MARK）。
DETAIL_TICKS = 40

# その後、ティック何回ごとに要約を1行書くか。
SUMMARY_EVERY = 60

# これ以上ティックが空いたら「別のメッセージ」として要約を締める（秒）。
# 打ち終わりで必ず1行出るので、短いメッセージでも数字が残る。
IDLE_GAP = 1.0

# これより短い間隔で続いたティックは数えない（秒。版6）。
# 打ち切りが残りを一度に流すと、同じ瞬間に数十回まとめて来る。
# 間隔の平均を 0 へ引っぱるだけで、表示の速さの計測にならない。
BURST_GAP = 0.001

# 1文字あたりこのミリ秒を超えた塗り直しは、
# 要約とは別に残す（何が重いかの手がかり）。
SLOW_MS = 20.0

# 遅い回を残す上限。
MAX_SLOW = 40

# 1プロセスに1回だけにするための印（`sys` に置く。TECH.md §3.6。版6）。
# `DETAIL_MARK` は1行ずつ出したティックの数、`DEFAULTS_MARK` は書いた既定値の組、
# `INSTALLED_MARK` は最後に書いた `update_display_text` の積み順（版7）。
DETAIL_MARK = "_instantale_probe_textspeed_detail"
DEFAULTS_MARK = "_instantale_probe_textspeed_defaults"
INSTALLED_MARK = "_instantale_probe_textspeed_installed"

# 要約のカウンタ。flush() が 0 に戻す。
COUNTERS = ("ticks", "gap_sum", "gap_max", "dt_sum", "dt_max",
            "paint_sum", "paint_max", "paints", "chars", "grows", "char_max",
            "render_n", "render_sum", "render_max", "burst")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 計測の途中経過。
    # 要約系のカウンタ（COUNTERS）は flush() が 0 に戻す。
    state = {
        "speed": None,        # 最後に見た app.text_speed
        "prev": None,         # 直前のティックの時刻
        "ticks": 0, "gap_sum": 0.0, "gap_max": 0.0,
        "dt_sum": 0.0, "dt_max": 0.0,
        "paints": 0, "paint_sum": 0.0, "paint_max": 0.0,
        # `grows` は本文が伸びた回の数。`chars` と `char_max` はその回だけを数えるので、
        # 平均の分母もこれにする（版6。版5はティック数で割っていて、平均が最大を上回った）。
        "chars": 0, "grows": 0, "char_max": 0, "len_prev": None,
        "render_n": 0, "render_sum": 0.0, "render_max": 0.0,
        "burst": 0,           # 数えなかった塊のティック数（BURST_GAP）
        "slow": 0,            # 遅い塗り直しを何回書いたか（MAX_SLOW まで）
        "label": None,        # 計測対象の本文ラベル
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

    def reset():
        for key in COUNTERS:
            state[key] = 0 if isinstance(state[key], int) else 0.0

    def flush(reason=""):
        """要約を1行。平均と最大の両方を出す（頭打ちは最大に出る）。

        数えたティックが無ければ書かずにカウンタだけ戻す（版6）。
        版5は戻さずに抜けていたので、塊だけの回の塗り直しが次のメッセージの要約に混ざっていた。
        """
        if not state["ticks"]:
            reset()
            return
        ticks = state["ticks"]
        paints = state["paints"] or 1
        fps = fps_now()
        label = state["label"]
        texture = frames.attr(label, "texture_size") if label is not None else None
        write("tick x{}{}  text_speed={}  interval avg={:.1f}ms max={:.1f}ms  "
              "clock dt avg={:.1f}ms max={:.1f}ms  repaint avg={:.1f}ms max={:.1f}ms  "
              "chars/tick avg={:.2f} max={}  fps={}  "
              "render x{:.2f}/tick avg={:.1f}ms max={:.1f}ms  texture={}{}"
              .format(ticks, " ({})".format(reason) if reason else "", speed_text(),
                      state["gap_sum"] / ticks * 1000.0, state["gap_max"] * 1000.0,
                      state["dt_sum"] / ticks * 1000.0, state["dt_max"] * 1000.0,
                      state["paint_sum"] / paints * 1000.0, state["paint_max"] * 1000.0,
                      state["chars"] / float(state["grows"] or 1), state["char_max"],
                      "{:.1f}".format(fps) if fps is not None else "?",
                      state["render_n"] / float(ticks),
                      state["render_sum"] / (state["render_n"] or 1) * 1000.0,
                      state["render_max"] * 1000.0,
                      frames.repr_value(texture),
                      "  burst x{}".format(state["burst"]) if state["burst"] else ""))
        # 判定に使う材料はここまで。
        # 結論はログを読む側が出す（推測を書かない）。
        reset()

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

    # -- 設定の見張り ----------------------------------------------------------
    # 版5までは 1秒ごとの見張り（Clock）が `app.text_speed` とフレームの様子を読み、
    # 変化が無くても 30秒ごとに1行書いていた。
    # 版6でやめた（docstring の版6）。
    # `text_speed` の変化は次のティックの `note_speed` が拾う。
    # 注入し直したとき、版5の見張りは自分から降りる（あちらの `ctx.superseded()`）。

    # -- ゲームの打ち出し（1ティック） ---------------------------------------
    # 引数は受け取った形のまま本体へ渡す（版7）。
    # 名前で受けて位置で渡し直すと、欠けた `context` に None を補ってしまい、
    # 内側の厳密な受け手（`118_` / `122_`）が出すはずの TypeError を計測中だけ隠す。
    @ctx.wrap("__main__:InstantaleApp.add_text_display", required=False, safe=True)
    def add_text_display(orig, self, *args, **kwargs):
        now = time.perf_counter()
        try:
            dt = frames.arg(args, kwargs, "dt", 0)
            note_speed(self, "tick")
            gap = None
            if state["prev"] is not None:
                gap = now - state["prev"]
                # 間が空いた ＝ 別のメッセージ。
                # 前のぶんはここで締める（短いメッセージでも要約が残るように）。
                if gap > IDLE_GAP:
                    flush("idle")
                elif gap < BURST_GAP:
                    # 同じ瞬間の塊。件数だけ数える（版6）。
                    state["burst"] += 1
                else:
                    state["gap_sum"] += gap
                    state["gap_max"] = max(state["gap_max"], gap)
                    state["ticks"] += 1
                    # Kivy が渡す dt は「前回の予約から実際に経った時間」。
                    # 設定どおりに呼べているかは、
                    # こちらと間隔の両方を並べたほうが確かめやすい。
                    # 分母は ticks なので、数えたティックの dt だけを足す（版6）。
                    if isinstance(dt, float):
                        state["dt_sum"] += dt
                        state["dt_max"] = max(state["dt_max"], dt)
            state["prev"] = now
            # 最初の数十回は1行ずつ（1プロセスに1回。版6）。
            # 要約を待たずに間隔が読める。
            seen = getattr(sys, DETAIL_MARK, 0)
            if seen < DETAIL_TICKS:
                setattr(sys, DETAIL_MARK, seen + 1)
                write("tick #{}  gap={}  dt={}  text_speed={}".format(
                    seen + 1,
                    "-" if gap is None else "{:.1f}ms".format(gap * 1000.0),
                    "{:.1f}ms".format(dt * 1000.0) if isinstance(dt, float) else repr(dt),
                    speed_text()))
            elif state["ticks"] >= SUMMARY_EVERY:
                flush()
        except Exception:
            ctx.log_exc("text speed probe: tick bookkeeping failed")
        return orig(self, *args, **kwargs)

    # -- ラベルのテクスチャの作り直し（フレーム側の代金） -----------------
    # ここが `repaint` に出てこない残りの仕事。
    # Kivy はテキストが変わると次のフレームでテクスチャを作り直すので、
    # `update_display_text` の中の計測には出ず、フレーム時間のほうに乗る。
    # 1文字ごとに 1340x2800 を作り直していればそれだけでフレームが落ちる。
    # **1ティックあたり何回・何ミリ秒**かを数える。
    #
    # 全部の Label がここを通るので、中は最小にしてある（版6）。
    # safe=True の層は呼び出しのたびに器と閉包を作るのでやめ、素の包みにした。
    # 本文のラベル以外は判定1つで素通し。記録は本体の後の try の中だけ。
    @ctx.wrap("kivy.uix.label:Label.texture_update", required=False)
    def texture_update(orig, self, *args, **kwargs):
        if self is not state["label"]:
            # 本文のラベル以外（ボタン・状態表示）は数えない。
            # 計測の対象は「1文字ごとに作り直している大きいラベル」だけ。
            return orig(self, *args, **kwargs)
        start = time.perf_counter()
        result = orig(self, *args, **kwargs)
        try:
            spent = time.perf_counter() - start
            state["render_n"] += 1
            state["render_sum"] += spent
            state["render_max"] = max(state["render_max"], spent)
        except Exception:
            ctx.log_exc("text speed probe: render bookkeeping failed")
        return result

    # -- 1文字ぶんの塗り直し（ゲーム＋内側の MOD 全部） -----------------------
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, *args, **kwargs):
        # テクスチャの計測対象を覚える（属性を1回読むだけ）。
        label = frames.attr(self, "text_display")
        if label is not frames.MISSING and label is not None:
            state["label"] = label
        start = time.perf_counter()
        result = orig(self, *args, **kwargs)
        spent = time.perf_counter() - start
        try:
            value = frames.arg(args, kwargs, "value", 1)
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
                    state["grows"] += 1
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
    def get_default_text_speed_for_language(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        language = frames.arg(args, kwargs, "language", 0)
        # 同じ答えは1プロセスに1回（版6。版5は呼ばれるたびに書いていた）。
        seen = getattr(sys, DEFAULTS_MARK, None)
        if not isinstance(seen, set):
            seen = set()
            setattr(sys, DEFAULTS_MARK, seen)
        key = (repr(language), repr(result))
        if key not in seen:
            seen.add(key)
            write("get_default_text_speed_for_language({!r}) -> {!r}".format(
                language, result))
        return result

    # 誰が同じ場所に乗っているかを残す。
    # 犯人を絞るときの出発点になる。
    # 1回の起動で apply は何度も走るので、積み順が前回と同じなら書かない（版7）。
    stacked = (ctx.patches() or {}).get(
        "scripts.hud.new_hud:InstanTaleHUD.update_display_text") or []
    stacked_line = ", ".join(stacked) or "(nothing else)"
    if getattr(sys, INSTALLED_MARK, None) != stacked_line:
        setattr(sys, INSTALLED_MARK, stacked_line)
        write("probe installed; update_display_text is wrapped by: {}".format(
            stacked_line))
    ctx.log("text speed probe: measuring the typewriter; results go to out/{}".format(
        LOG_BASENAME))
