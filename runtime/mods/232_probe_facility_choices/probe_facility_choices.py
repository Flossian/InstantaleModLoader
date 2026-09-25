# -*- coding: utf-8 -*-
"""計測: 施設の選択肢の並び（宿屋だけ `出る` が先頭になる原因を探す）。

宿屋だけゲームが `出る / 宿泊する / 会話する` の順で組む（GAME.md §2.2。MOD 無しでも同じ）。
`135_fix_inn_button_order` は描く前に並べ直す対処で、原因は版1のこの計測で測った。

候補は2つあった。

1. `Facility.choices` が集合で、並びが文字列のハッシュで決まる
2. ゲームがボタンを組むとき、`choices` に無い選択肢を後から足していて、
   宿屋の `宿泊する` がそれに当たる

施設へ入るたびに次を録る。

- 立っている施設の `choices` の型と、そのまま回したときの順（`list(choices)`）
- ゲームが組み終えた `app.buttons` の並び（文言と spec のクラス名）
- `sys.flags.hash_randomization` と `PYTHONHASHSEED`、`出る` / `宿泊する` のハッシュの下3ビット
  （集合の 8 枠のどこに入るか。起動をまたいで同じなら種が固定されている）

##### 結果（起動2回・宿屋2回・店1回・区画3回）

- `choices` は**集合ではなく dict**（挿入順）。宿屋は `['出る']` の**1つだけ**。
  店は `['売買する', '出る']`。区画は繋がる施設の名前の並び
- ハッシュの枠は起動ごとに変わった（`出る` が 0 → 3）のに並びは同じ。ハッシュは無関係
- だから 2 が原因。ゲームは `choices` の並びでボタンを組み、宿屋の `宿泊する`
  （`DisplayVacationChoice(app, period_months)`。期間の引数を持つので静的な `choices` に入っていない）を
  **その後ろに足し**、最後に `会話する` を足す。店の `売買する` は `choices` の中に
  `出る` より前で入っているので操作が先に出る。宿屋だけ操作が `choices` の外に居る
- ここで見える `buttons` は `135_` が並べ直した**後**（135 は `move_phase` の中の
  `refresh_choice_buttons` で動かす。`after` で外側に包んでも中の順は変わらない）。
  素の並びは GAME.md §2.2（MOD 無しの実機）

バイトコードの書き出しは版1で試して外した。ゲームは Nuitka ビルドで `__code__` の
`co_consts` / `co_names` が空（TECH.md §1 のとおり）。読めるのはローダの包みだけだった。

ゲームは変えない（200番台の約束どおり読み取りだけ）。
"""

import os
import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "facility_choices.log"
#: ハッシュの枠を見る文言。
HASH_WORDS = ("出る", "宿泊する", "会話する", "売買する", "利用する")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    state = {"env": False}

    def facility_of(app):
        """立っている施設（`player.location`）。id だけなら街から引く。"""
        player = getattr(app, "player", None)
        location = getattr(player, "location", None)
        if location is None or isinstance(location, (str, int)):
            area = ui.current_area(app)
            found, _node = ui.find_facility(area, location) if area is not None else (None, None)
            return found
        return location

    def env_once():
        if state["env"]:
            return
        state["env"] = True
        flags = getattr(sys, "flags", None)
        write("env: hash_randomization={} PYTHONHASHSEED={!r} python={}".format(
            getattr(flags, "hash_randomization", None),
            os.environ.get("PYTHONHASHSEED"), sys.version.split()[0]))
        write("env: hash slots (hash & 7): {}".format(
            {word: hash(word) & 7 for word in HASH_WORDS}))

    def buttons_brief(app):
        out = []
        for entry in getattr(app, "buttons", None) or ():
            if isinstance(entry, dict):
                out.append((entry.get("text"), ui.spec_cls_name(entry)))
            else:
                out.append((frames.repr_value(entry), None))
        return out

    def record(app, why):
        env_once()
        facility = facility_of(app)
        if facility is None:
            write("{}: no facility under the player".format(why))
            return
        choices = getattr(facility, "choices", None)
        try:
            order = list(choices) if choices is not None else None
        except Exception:
            order = "(not iterable)"
        write("{}: facility={!r} type={} choices<{}>={!r} buttons={!r}".format(
            why, getattr(facility, "name", None), ui.facility_type_of(facility),
            type(choices).__name__, order, buttons_brief(app)))
        # 施設が持つ、選択肢に関わりそうな属性を一度だけ全部並べる（名前だけ）。
        key = "attrs:{}".format(ui.facility_type_of(facility))
        if not state.get(key):
            state[key] = True
            names = sorted(n for n in vars(facility) if not n.startswith("_"))
            write("attrs of a {}: {}".format(ui.facility_type_of(facility), names))
            for name in names:
                if "choice" in name or "button" in name or "menu" in name:
                    write("  {}={!r}".format(name, frames.repr_value(getattr(facility, name, None))))

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            record(getattr(self, "app", None) or ui.find_app(), "move_phase")
        except Exception:
            ctx.log_exc("probe facility choices: cannot record after move_phase")
        return result

    ctx.log("probe facility choices: recording choices and buttons on every move")
