# -*- coding: utf-8 -*-
"""修正: 宿屋の選択肢を 宿泊する／出る／会話する の順にする。

##### 何が起きているか

施設の選択肢は「その施設の操作 → 出る → 会話する」の順で出る
（店は `売買する / 出る / 会話する`、鍛冶屋は `装備の強化 / 出る / 会話する`、
役場は `労働の募集をみる / 市民権の発行 / 出る / 会話する`）。
**宿屋だけ `出る` が先頭に来る**（`出る / 宿泊する(Nヵ月) / 会話する`）。
MOD を1つも当てない状態でも同じで、原因はゲームの側にある（GAME.md §2.2）。
`Facility.choices` を触っている MOD は無く、宿屋の画面で生まれる `PhaseSpec` は
`DisplayVacationChoice`（宿泊する）と `DisplayTalkChoice`（会話する）の2つだけ。
`出る` は施設が元から持っている選択肢で、並びを決めているのは施設に入る経路のほう。

原因は `232_probe_facility_choices` で測った（2026-09-20）。
施設が持つ `choices` は挿入順の dict で、**宿屋は `出る` の1つだけ**（店は `売買する` → `出る`）。
ゲームは `choices` の並びでボタンを組んでから、宿屋の `宿泊する`
（期間の引数を持つ `DisplayVacationChoice` なので静的な `choices` に入っていない）を後ろに足し、
最後に `会話する` を足す。宿屋だけ操作が `choices` の外に居る。ハッシュは無関係。

セーブの `game_variables["buttons"]` を手で並べ替えるとその1画面は直るが、
一度出て入り直すと `出る` が先頭に戻る（同 §2.2）。
入る経路そのものは手で直せないので、描く直前に並べ直す。

##### 直し方

`InstantaleApp.refresh_choice_buttons` を包み、**描く前に** `app.buttons` を並べ直す。
`出る`（`MovePhaseManager`）より前に `宿泊する`（`DisplayVacationChoice`）が無いときだけ、
`宿泊する` を `出る` の前へ動かす。
それ以外の画面（部屋選び・活動の選択肢・よその施設）は spec の辞書引きだけで素通りする。

見分けは文言ではなく spec のクラス名で行う（`315_vacation_custom` が文言を書き換えるため。
GAME.md §2.2）。
描く前に動かすので `to_display_buttons` と `display_button_map` は動かした後の並びで組まれ、
押した添字が別のボタンを指すことは無い。

##### 他の MOD との噛み合い

- `315_vacation_custom` は同じ場所で文言だけ書き換える。並びは見ないので順序の縛りは無い
- `331_facility_investment` の自分の宿はゲームの `宿泊する` を伏せて `無料で泊まる` を
  その場所に出す（ローダの `modfacility`。TECH.md §5.8）。伏せる前にこちらが動かすので、
  `無料で泊まる` も先頭に出る
- 並び替えた後の `app.buttons` はゲームがそのままセーブに焼く（GAME.md §2.3）。
  この MOD を外しても、焼かれた並びは次に施設へ入り直すまで残るだけで害は無い
"""

from instantale_modloader import ui

LOG_BASENAME = "inn_button_order.log"

#: 宿屋の入口（`宿泊する(Nヵ月)`。部屋選びへ進む）。
STAY_CLS = "DisplayVacationChoice"

#: 施設の `出る`（ゲームの移動）。
MOVE_CLS = "MovePhaseManager"

#: ログの上限。塗り直しのたびに動かすが、同じ並びを2度は書かない。
MAX_LINES = 300


def reorder(buttons):
    """`出る` より前に `宿泊する` が無ければ、`宿泊する` を `出る` の前へ動かす。

    動かしたら真。触らなかったら偽。
    `宿泊する` が複数並ぶ画面は無い（実測）が、在れば元の順のまま全部を前へ出す。
    """
    if not isinstance(buttons, list):
        return False
    first_move = None
    stays = []
    for index, entry in enumerate(buttons):
        cls = ui.spec_cls_name(entry)
        if cls == MOVE_CLS and first_move is None:
            first_move = index
        elif cls == STAY_CLS:
            stays.append(index)
    if first_move is None or not stays or stays[0] < first_move:
        return False
    moved = [buttons[i] for i in stays]
    for index in reversed(stays):
        del buttons[index]
    buttons[first_move:first_move] = moved
    return True


def apply(ctx):
    append = ctx.logger(LOG_BASENAME)
    state = {"lines": 0, "last": None}

    def write(text):
        if state["lines"] >= MAX_LINES:
            return
        state["lines"] += 1
        append(text)

    def texts(buttons):
        return [entry.get("text") if isinstance(entry, dict) else str(entry)
                for entry in buttons]

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False,
              safe=True)
    def refresh_buttons(orig, self, *args, **kwargs):
        """描かれる直前に、宿屋の `宿泊する` を `出る` の前へ動かす。"""
        try:
            buttons = getattr(self, "buttons", None)
            before = texts(buttons) if isinstance(buttons, list) else None
            if reorder(buttons):
                after = texts(buttons)
                if state["last"] != (before, after):
                    state["last"] = (before, after)
                    write("reordered: {} -> {}".format(before, after))
        except Exception:
            ctx.log_exc("inn button order: cannot reorder the buttons")
        return orig(self, *args, **kwargs)

    ctx.log("inn button order: {} goes before {} at inns".format(STAY_CLS, MOVE_CLS))
