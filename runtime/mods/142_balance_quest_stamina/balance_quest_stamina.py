# -*- coding: utf-8 -*-
"""調整: 依頼をクリアすると、難易度に関係なくスタミナが上限の半分減る。

##### 何が起きているか

依頼のクリアでスタミナ（`physical_integrity`）を引くのは `QuestEndManager.method_1` で、
新しい値は `round(今の値 − 上限/2)`。依頼の難易度も道中の被弾も入らない（GAME.md §2.19）。
依頼を受けられるのはスタミナが上限の半分より多いときだけなので、
満タンから1件クリアすると必ず受けられなくなり、宿で休むまで次の依頼に出られない。
レベルより大幅に易しい依頼でも同じで、遊びの進みを止める。

##### どう変えるか

クリアで引く量を、依頼の難易度と主人公のレベルの差、それに依頼の間の被弾で決め直す。

```
差   = 依頼の難易度 − レベル           （難易度は 133 が「適正Lv」と出している数と同じ）
基本 = 差が −LEVEL_GAP 以下なら EASY_PERCENT、0 なら FAIR_PERCENT、
       +LEVEL_GAP 以上なら HARD_PERCENT。その間は直線でつなぐ
被弾 = 依頼の間に減った HP の合計 ÷ (最大 HP × DAMAGE_FULL_AT / 100)（1 で頭打ち）
引く量 = 上限 × (基本 + DAMAGE_PERCENT × 被弾) / 100   （四捨五入、少なくとも 1）
新しいスタミナ = max(0, クリア前のスタミナ − 引く量)
```

同行者も同じ式で、本人のレベルと本人の被弾で決める（`PARTY_TOO`）。

ゲームの引き算はそのまま走らせ、後から値を置き直す（クリア前のスタミナは窓の入口で控える）。
置き直したら `exhausted` を依頼の受付と同じ基準（上限の半分以下）で立て直し、
最大 HP をゲーム自身の `Character.update_max_hp` で組み直す（スタミナが最大 HP を削るため）。
ゲームがスタミナを引かなかった人（値が動かなかった人）には触らない。

被弾は、依頼の最中にゲームが保存するたびに HP を見て、前に見た値から減った分を足していく
（ゲームは行動のたびに保存する）。依頼の途中でロードし直した場合は、ロードより前の被弾は数えられない。
その代わり、クリアの時点で最大 HP に足りない分のほうが多ければ、そちらを使う。

設定がすべてゲームの値（3つの割合が 50、被弾が 0）なら何もしない。

    out\\quest_stamina.log    1件のクリアごとに、差・基本・被弾・引いた量・前後のスタミナ
"""
import sys
import threading

from instantale_modloader import frames, ui

LOG_BASENAME = "quest_stamina.log"
INSTALLED_MARK = "_instantale_balance_quest_stamina_installed"

#: 適正（難易度＝レベル）の依頼で引く割合（上限に対する %）。
FAIR_PERCENT = 33

#: レベルより LEVEL_GAP 以上易しい依頼で引く割合。
EASY_PERCENT = 10

#: レベルより LEVEL_GAP 以上難しい依頼で引く割合。
HARD_PERCENT = 50

#: 易しい・難しいの割合に届くまでのレベル差。
LEVEL_GAP = 20

#: 被弾が満額（DAMAGE_FULL_AT に届いた）のときに足す割合。
DAMAGE_PERCENT = 20

#: 被弾の上乗せが満額になる、依頼の間に失った HP の合計（最大 HP に対する %）。
#: 最大 HP の 100% を失えば倒れるので、回復を挟まずに届く値にしておく。
DAMAGE_FULL_AT = 50

#: 同行者にも同じ式を使う。
PARTY_TOO = True

#: ゲームの割合（`round(今の値 − 上限/2)`）。設定がこれと同じなら触らない。
GAME_PERCENT = 50

QUEST_START = "__main__:QuestStartManager.start_quest"
QUEST_END = "__main__:QuestEndManager.execute"
SAVE_GAME = "__main__:InstantaleApp.save_game"


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
    except Exception:
        return None
    return int(number) if number.is_integer() else number


def is_game_rule():
    """設定がゲームの値と同じか（同じなら何もしない）。"""
    return (FAIR_PERCENT == GAME_PERCENT and EASY_PERCENT == GAME_PERCENT
            and HARD_PERCENT == GAME_PERCENT and DAMAGE_PERCENT == 0)


def base_percent(gap):
    """難易度とレベルの差から、基本の割合。"""
    span = max(0, int(LEVEL_GAP))
    if span == 0:
        return EASY_PERCENT if gap < 0 else HARD_PERCENT if gap > 0 else FAIR_PERCENT
    if gap <= -span:
        return float(EASY_PERCENT)
    if gap >= span:
        return float(HARD_PERCENT)
    if gap < 0:
        return EASY_PERCENT + (FAIR_PERCENT - EASY_PERCENT) * (gap + span) / float(span)
    return FAIR_PERCENT + (HARD_PERCENT - FAIR_PERCENT) * gap / float(span)


def damage_scale(loss_share):
    """失った HP の割合（最大 HP に対する比）から、被弾の上乗せの効き（0〜1）。"""
    full = max(1, int(DAMAGE_FULL_AT)) / 100.0
    return min(1.0, max(0.0, loss_share / full))


def spend_of(max_pi, gap, loss_share):
    """引く量と、その内訳の割合。`loss_share` は失った HP ÷ 最大 HP。"""
    share = damage_scale(loss_share)
    percent = base_percent(gap) + DAMAGE_PERCENT * share
    spend = int(max_pi * percent / 100.0 + 0.5)
    return max(1, spend), percent


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    lock = threading.Lock()
    #: 依頼の間の被弾。`{"quest": id, "people": {人: {"last", "loss", "max_hp"}}}`。
    track = {"quest": None, "people": {}}

    if is_game_rule():
        write("settings match the game (all {}%, damage 0); not touching the quest clear".format(
            GAME_PERCENT))
        return

    def participants(app):
        """`[(人, Character)]`。主人公が先頭。同行者は `PARTY_TOO` のときだけ。"""
        out = []
        player = frames.attr(app, "player", None)
        if player is not None:
            out.append((ui.PLAYER_ID, player))
        if PARTY_TOO:
            characters = frames.attr(frames.attr(app, "world", None), "characters", None)
            for member in ui.party_member_ids(app):
                character = characters.get(member) if isinstance(characters, dict) else None
                if character is not None:
                    out.append((str(member), character))
        return out

    def sample(app, quest_id):
        """今の HP を見て、前に見た値から減った分を足す。依頼が変わっていたら数え直す。"""
        with lock:
            if track["quest"] != quest_id:
                track["quest"] = quest_id
                track["people"] = {}
            for person, character in participants(app):
                hp = _number(frames.attr(character, "current_hp", None))
                if hp is None:
                    continue
                row = track["people"].get(person)
                if row is None:
                    track["people"][person] = {
                        "last": hp, "loss": 0,
                        "max_hp": _number(frames.attr(character, "max_hp", None))}
                    continue
                if hp < row["last"]:
                    row["loss"] += row["last"] - hp
                row["last"] = hp

    @ctx.wrap(QUEST_START, required=False, safe=True)
    def quest_started(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = frames.attr(self, "app", None) or ui.find_app()
            quest_id = ui.current_quest_id(app)
            if quest_id is not None:
                with lock:
                    track["quest"] = None       # 同じ依頼を受け直したときも数え直す
                sample(app, quest_id)
        except Exception:
            ctx.log_exc("quest stamina: cannot start counting the damage")
        return result

    @ctx.wrap(SAVE_GAME, required=False, safe=True)
    def saved(orig, self, *args, **kwargs):
        try:
            quest_id = ui.current_quest_id(self)
            if quest_id is not None:
                sample(self, quest_id)
        except Exception:
            ctx.log_exc("quest stamina: cannot count the damage at a save")
        return orig(self, *args, **kwargs)

    @ctx.wrap(QUEST_END, required=False, safe=True)
    def quest_ended(orig, self, *args, **kwargs):
        app, before, quest = None, [], {}
        try:
            app = frames.attr(self, "app", None) or ui.find_app()
            data = frames.attr(app, "current_quest_data", None)
            quest_id = ui.current_quest_id(app)
            if data is not None and quest_id is not None:
                sample(app, quest_id)
                with lock:
                    hits = {k: dict(v) for k, v in track["people"].items()}
                quest = {"id": quest_id,
                         "difficulty": _number(ui.quest_value(data, "difficulty"))}
                for person, character in participants(app):
                    pi = _number(frames.attr(character, "physical_integrity", None))
                    max_pi = _number(frames.attr(character, "max_physical_integrity", None))
                    if pi is None or not max_pi:
                        continue
                    hp = _number(frames.attr(character, "current_hp", None))
                    max_hp = _number(frames.attr(character, "max_hp", None))
                    row = hits.get(person) or {}
                    base_hp = row.get("max_hp") or max_hp
                    loss = row.get("loss") or 0
                    if hp is not None and max_hp:
                        loss = max(loss, max_hp - hp)      # ロードをまたいだ被弾の取りこぼしを補う
                    before.append({
                        "who": person, "character": character, "pi": pi, "max_pi": max_pi,
                        "level": _number(frames.attr(character, "experience_level", None)),
                        "share": (loss / float(base_hp)) if base_hp else 0.0, "loss": loss})
        except Exception:
            ctx.log_exc("quest stamina: cannot read the party before the quest clear")
            before = []
        result = orig(self, *args, **kwargs)
        if before:
            try:
                settle(quest, before)
            except Exception:
                ctx.log_exc("quest stamina: cannot settle the stamina after the quest clear")
        with lock:
            track["quest"] = None
            track["people"] = {}
        return result

    def settle(quest, before):
        difficulty = quest.get("difficulty")
        for row in before:
            character = row["character"]
            now = _number(frames.attr(character, "physical_integrity", None))
            if now is None or now == row["pi"]:
                continue        # ゲームが引かなかった人には触らない
            level = row["level"]
            gap = (difficulty - level) if difficulty is not None and level is not None else 0
            spend, percent = spend_of(row["max_pi"], gap, row["share"])
            value = max(0, int(row["pi"] - spend))
            character.physical_integrity = value
            character.exhausted = value * 2 <= row["max_pi"]
            update = getattr(character, "update_max_hp", None)
            if callable(update):
                try:
                    update()
                except Exception:
                    ctx.log_exc("quest stamina: update_max_hp failed for {}".format(row["who"]))
            write("quest {} (difficulty {}): {} lv={} gap={:+} base={:.0f}% hp lost={} ({:.0%}, "
                  "damage {:.0%}) -> spend {} ({:.0f}% of {}); stamina {} -> {} (game: {})".format(
                      quest.get("id"), difficulty, row["who"], level, gap, base_percent(gap),
                      row["loss"], row["share"], damage_scale(row["share"]), spend, percent,
                      row["max_pi"], row["pi"], value, now))

    if getattr(sys, INSTALLED_MARK, False):
        return
    setattr(sys, INSTALLED_MARK, True)
    write("installed: easy {}% / fair {}% / hard {}% over {} level(s), damage +{}% at {}% hp lost, "
          "party {}".format(EASY_PERCENT, FAIR_PERCENT, HARD_PERCENT, LEVEL_GAP, DAMAGE_PERCENT,
                            DAMAGE_FULL_AT, PARTY_TOO))
