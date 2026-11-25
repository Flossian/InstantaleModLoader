# -*- coding: utf-8 -*-
"""役場で手配を解く（罰金を納めて、その土地の手配を帳消しにする）。遊び方は DOC.md「役場で手配を解く」。

`309_office_pardon` を取り込んだもの（309 は取込済）。振る舞いは 309 の版5 のまま。
設定は `env.cfg.PARDON_*` をその場で読む。

手配度（`player.area_history[エリアid]['lawfulness']`）が 0 未満だと、
プレイヤーはその土地では犯罪者として扱われる。
素のゲームにはこれを戻す手段が無いので、**役場（`administrative_office`）の選択肢に「罰金を納める」を1つ足す**。

    [役場]  会話する / 罰金を納めて手配を解く(3,000G) / 出る
                              ↓
            3,000ゴールドを納める / やめておく
                              ↓
            所持金 -3,000 ／ 手配度 -3 → 10

## 扱うのは今いるエリアの手配度だけ

`area_history` はエリア id ごとに1件ある。
他の土地の手配はその土地の役場でしか解けない。
「よその役場に金を積めば全部消える」は世界の作りとして通らないし、
どの土地の分をいくら払ったのかも画面に出せなくなる。

## 値段

    手配度 = 0 - 手配度の実値（手配の重さ。窓口 `wanted.weight_of`）   （lawfulness=-3 なら 3）
    罰金   = PARDON_PRICE_PER_POINT × 手配度              （既定 1000G/点 なので 3,000G）

どこから手配かは、追手・時効・`317_` の悪名と同じ窓口の境目（`wanted.WANTED_BELOW`）で数える。
309 の頃は罰金だけが設定（`WANTED_THRESHOLD`）を持っていて、変えると「罰金を取られるのに追手と時効は手配と見ない」
食い違いが起きたので外した（2026-11-24）。額は土地で変えない（手配度1あたりの固定額）。

払うと手配度は `PARDON_RESTORE_TO`（既定 10 ＝ 素の平常値）に戻る。
**下げる方向には決して動かさない**（設定を触って
戻し先を今の値より低くしても、そのときは今の値のまま）。
戻し先が境目より低いときは境目まで戻す（そうしないと払っても手配が解けない）。

## ボタンの出し方

ボタンは 336 の宣言型の口（`env.on_offer`。窓口 `choices` の `offer`）に預ける（TECH.md §3.3.14）。
「罰金を納める」は組み直しのたびに、いまの手配から作る。
役場の選択肢そのものはゲームが持っているので、こちらは足すだけで組み直さない。
出す条件は全部揃ったときだけ:

    いま居る施設が administrative_office／その土地の手配度が閾値未満／
    その画面に移動（`MovePhaseManager`）のボタンがある＝施設の選択肢である／
    戦闘・会話・ポップアップの最中ではない

確認の画面（納める／やめておく）は MOD だけの画面（`env.on_screen`）で、下の役場の選択肢は窓口が書き換えずに残す。
確認の画面で保存して読み直しても、確認の画面に戻る。
押下は窓口がボタンに持たせた処理を呼ぶ。ボタンはセーブに残らない（窓口が保存の間だけ外す）。
「罰金を納める」には印（`MARK`）を `marks` で付ける（ほかの MOD が見分けるための印。押下の振り分けには使わない）。

## セーブはこちらから書かない

書き換えるのは実行中の `player.gold` と `player.area_history` だけで、
`save_game` は呼ばない。
ゲームが次にセーブしたときに両方まとめて保存される。
セーブされる前に落ちれば**罰金も手配の帳消しも両方が無かったこと**になるので、
片方だけが残ることはない。
"""

from instantale_modloader import choices, ui, wanted

LOG_BASENAME = "office_pardon.log"

#: 役場の `facility_type`（実セーブで確認。GAME.md §2.7）。
OFFICE_FACILITY_TYPE = "administrative_office"

#: 「罰金を納める」に付ける印（ほかの MOD が見分けるため。窓口 `choices` の `marks`）。
#: 309 の頃と同じキー。
MARK = "mod_pardon_action"

#: 確認の画面の名前（336 の MOD だけの画面。ほかの機能の画面と分ける）。
CONFIRM_SCREEN = "pardon_confirm"

# ---------------------------------------------------------------- 画面に出す文言
BUTTON_LABEL = "罰金を納めて手配を解く({price}G)"
BUTTON_LABEL_FREE = "手配を解いてもらう"
ANNOUNCE_TEXT = "窓口の帳面に、{area}で手配された者として名が載っている。"
CONFIRM_TEXT = ("{area}での手配について、役場の窓口に問う。"
                "「{price}ゴールド。それで帳消しです」")
CONFIRM_DETAIL = "（手配度 {wanted} ／ 手持ち {gold}ゴールド）"
PAY_LABEL = "{price}ゴールドを納める"
CANCEL_LABEL = "やめておく"
CANCEL_TEXT = "窓口を離れた。"
PAID_TEXT = "役場に{price}ゴールドを納めた。"
CLEARED_TEXT = "{area}での手配は取り消された。"
BROKE_TEXT = "「その額を持たない者に、書き換える帳面はありません」"
BROKE_DETAIL = "（罰金 {price}ゴールド ／ 手持ち {gold}ゴールド）"

# 309 の版3までがセーブに焼いた自前のボタンの文言（窓口 `choices` の `labels`。無害な spec で印の無いものを外す）。
# 版3までは自前のボタンを一覧に物として置いていたので、セーブに文言と spec が残った。
# 金額つきのラベルは `{price}` の手前までを見る（照合は前方一致）。
# `PAY_LABEL` / `CANCEL_LABEL` は入れない。
# 「やめておく」は `302_` も出す文言で、こちらのものだと言い切れない。
OUR_LABELS = (BUTTON_LABEL.split("(")[0], BUTTON_LABEL_FREE)
GONE_TEXT = "（手配はもう残っていない）"
FAILED_TEXT = "（帳面は書き換えられなかった）"

#: ここが真の間はボタンを出さない。
#: 戦闘中・会話中など。
#: 「手が空いているか」（テキストの流し込み中・ポップアップが開いている）は
#: `ui.busy_signals` 側。
#: 表はローダが持つ。`in_shopping` は入っていない（店に寄った後の役場で
#: ボタンが出なかった。`ui.BUSY_FLAGS` の註）。
BUSY_FLAGS = ui.BUSY_FLAGS

#: 施設の選択肢だと見なす目印。
#: 文字列ではなく spec のクラス名で見る。
#: 施設には必ず出口（`MovePhaseManager`）があるので、
#: これが1つも無い画面は施設の選択肢ではない（会話中・確認画面・掲示板など）。
FACILITY_MARK = "MovePhaseManager"


money = ui.money          # 金額の表示（`902_` と共有）
coins = ui.rewrite_coins  # 通貨の表記を今の表記へ（`130_`。`902_` と共有）

# 上の文言は素のゲームの言い方（`ゴールド` / `G`）で書いてある。
# 画面へ出す直前に `coins()` を通すので、`130_` が表記を差し替えていれば
# そちらに揃う。入っていなければ何も変わらない。


def price_for(weight, per_point):
    """手配度から罰金を出す。手配されていなければ 0。"""
    if weight <= 0:
        return 0
    return max(0, int(per_point)) * int(weight)


def install(env):
    ctx, cfg = env.ctx, env.cfg
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    screen = ui.Screen(ctx, write, tag="office pardon", mark=MARK)

    state = {
        # 手配を知らせた場所（エリア id, 施設 id）。
        # 同じ場所で言い直さないため。
        "announced": None,
    }

    # ------------------------------------------------------------ 状況を読む
    def wanted_here(app):
        """今いる土地の手配。`(エリア名, 記録, 手配度, 罰金)`。無ければ `None`。

        読めなかったものは「手配されていない」に倒す。
        値が読めないことを理由に、ありもしない罰金を請求しない。
        """
        player = getattr(app, "player", None)
        if player is None:
            return None
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        if not area_id:
            return None
        entry = ui.area_record(player, area_id)
        lawfulness = ui.lawfulness_of(entry)
        if lawfulness is None:
            return None
        weight = wanted.weight_of(lawfulness)
        if weight <= 0:
            return None
        return (ui.area_name(app, area, fallback="この土地", limit=None),
                entry, weight, price_for(weight, cfg.PARDON_PRICE_PER_POINT))

    def at_office(app):
        """いま役場に立っているか。"""
        facility = ui.current_facility(app)
        return ui.facility_type_of(facility) == OFFICE_FACILITY_TYPE

    def busy(app):
        """ボタンを出す前提が崩れている理由の一覧（無ければ空。`ui.busy_flags`）。"""
        return ui.busy_flags(app, BUSY_FLAGS)

    def facility_key(app):
        facility = ui.current_facility(app)
        return (ui.area_id_of(ui.current_area(app)),
                str(getattr(facility, "id", "")))

    # ------------------------------------------------------------ ボタンを足す
    def offer(app, view):
        """役場の選択肢に「罰金を納める」を1つ出す（窓口 `choices` の `offer`。組み直しのたびに呼ばれる）。

        ゲームが組んだ一覧に足すだけで、組み直さない。
        """
        if not cfg.PARDON_ENABLED:
            return None
        buttons = view.buttons
        if not buttons or not ui.is_facility_screen(buttons):
            return None
        if not at_office(app):
            state["announced"] = None   # 役場を出たら、次に入ったときに知らせ直す
            return None
        if busy(app):
            return None
        found = wanted_here(app)
        if found is None:
            return None
        area_name, _entry, weight, price = found
        label = (coins(BUTTON_LABEL.format(price=money(price))) if price > 0
                 else BUTTON_LABEL_FREE)
        if cfg.PARDON_ANNOUNCE_ON_ARRIVAL:
            key = facility_key(app)
            if state["announced"] != key:
                state["announced"] = key
                write("offer: {!r} wanted={} price={} label={!r}".format(
                    area_name, weight, price, label))
                screen.say(app, ANNOUNCE_TEXT.format(area=area_name))
        # 移動（出口）の手前に置く。
        # 役場でできることを先に、その場を離れる選択肢を後に、
        # という並びはゲーム自身の施設の選択肢と同じ。
        return [choices.Choice(label, place=choices.before(FACILITY_MARK), marks={MARK: "open"},
                               on_press=_press("open", label))]

    # ------------------------------------------------------------ 確認画面
    def open_confirm(app):
        """「罰金を納めて手配を解く」が押されたとき。金額を出して確認を取る（確認の画面を開く）。"""
        found = wanted_here(app)
        if found is None:
            # 押すまでの間に手配が消えている（別経路で戻された等）。
            write("confirm: nothing is pending any more")
            screen.say(app, GONE_TEXT)
            restore(app, "not wanted")
            return
        area_name, _entry, weight, price = found
        gold = ui.gold_of(app)
        screen.say(app, coins(CONFIRM_TEXT.format(
            area=area_name, price=money(price))))
        screen.say(app, coins(CONFIRM_DETAIL.format(
            wanted=weight, gold=money(gold) if gold is not None else "?")))
        write("confirm: {!r} wanted={} price={} gold={}".format(
            area_name, weight, price, gold))
        env.open_screen(app, CONFIRM_SCREEN)

    def confirm_screen(app, view, _data):
        """確認の画面のボタン（組み直しのたびに、いまの手配から組む）。役場に居ない・手配が無いなら閉じる（None）。"""
        found = wanted_here(app) if at_office(app) else None
        if found is None:
            return None
        pay_label = coins(PAY_LABEL.format(price=money(found[3])))
        return [choices.Choice(pay_label, on_press=_press("pay", pay_label)),
                choices.Choice(CANCEL_LABEL, on_press=_press("cancel", CANCEL_LABEL))]

    def restore(app, why):
        """確認の画面を閉じて役場の選択肢に戻す（下の選択肢は窓口が書き換えずに残している）。

        まだ手配が残っていれば窓口の組み直しで `offer` が足し直すので、ここで足さない
        （「今どうなっているか」の判断を1箇所に閉じておく）。
        """
        write("restore: {}".format(why))
        if not env.close_screen(app):
            choices.repaint(app)

    # ------------------------------------------------------------ 支払う
    def pay(app):
        """罰金を納めて手配度を戻す。金と手配度は同じ流れの中で動かす。"""
        found = wanted_here(app)
        if found is None:
            write("pay: nothing is pending any more")
            screen.say(app, GONE_TEXT)
            restore(app, "not wanted")
            return
        area_name, entry, weight, price = found
        gold = ui.gold_of(app)

        if gold is None:
            # 所持金が読めないなら払わせない。
            # 手配だけ消して金を取らない（あるいはその逆）にならないよう、
            # ここで降りる。
            write("WARN pay: cannot read player.gold; refusing")
            screen.say(app, FAILED_TEXT)
            restore(app, "no gold field")
            return
        if gold < price:
            write("pay: refused; price={} gold={}".format(price, gold))
            screen.say(app, BROKE_TEXT)
            screen.say(app, coins(BROKE_DETAIL.format(price=money(price),
                                                      gold=money(gold))))
            restore(app, "not enough gold")
            return

        before = ui.lawfulness_of(entry)
        # 下げる方向には動かさない（設定を触られても手配を重くしない）。
        # 境目より下にも戻さない。そのまま書くと、払っても手配が解けずにボタンが残り、押すたびに罰金だけ取られる。
        after = max(before, int(cfg.PARDON_RESTORE_TO), wanted.WANTED_BELOW)
        if not ui.set_lawfulness(entry, after):
            write("WARN pay: cannot write lawfulness; nothing was charged")
            screen.say(app, FAILED_TEXT)
            restore(app, "cannot write lawfulness")
            return
        if price > 0 and ui.set_gold(app, gold - price, refresh=True) is None:
            # 金を取れなかったので手配度も戻す。
            # 片方だけ通さない。
            ui.set_lawfulness(entry, before)
            write("WARN pay: cannot write gold; lawfulness rolled back")
            screen.say(app, FAILED_TEXT)
            restore(app, "cannot write gold")
            return

        state["announced"] = None
        write("paid: {!r} price={} gold {} -> {} lawfulness {} -> {} (wanted {})"
              .format(area_name, price, gold, gold - price, before, after, weight))
        if price > 0:
            screen.say(app, coins(PAID_TEXT.format(price=money(price))))
        screen.say(app, CLEARED_TEXT.format(area=area_name))
        restore(app, "paid")

    def cancel(app):
        write("cancelled")
        screen.say(app, CANCEL_TEXT)
        restore(app, "cancelled")

    ACTIONS = {"open": open_confirm, "pay": pay, "cancel": cancel}

    def _press(action, text):
        """自前のボタンの押下（窓口がボタンに持たせる処理）。ゲームと同じ経路（`process_choice`）で動かす。

        使い捨てのフェーズはローダの `Screen.run_phase` が作る（`PhaseSpec` には載せない）。
        """
        def on_press(app):
            write("pressed {!r} ({})".format(text, action))
            screen.run_phase(app, text, ACTIONS[action], label=action)
            return None
        return on_press

    # 選択肢と押下は 336 の宣言型の口（入口がローダの窓口 `choices` に1つだけ預ける）。
    env.on_offer(offer)
    env.on_screen(CONFIRM_SCREEN, confirm_screen)
    env.on_labels(lambda _app: OUR_LABELS)

    ctx.log("office pardon ({}): {}G per point, wanted below {}, restore to {}, log={}"
            .format("on" if cfg.PARDON_ENABLED else "off", cfg.PARDON_PRICE_PER_POINT,
                    wanted.WANTED_BELOW, cfg.PARDON_RESTORE_TO, log_path))
