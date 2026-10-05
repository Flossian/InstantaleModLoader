# -*- coding: utf-8 -*-
"""ゲームの衛兵の戦闘を MOD が起こす窓口。強さ（難易度）を決めて起こせる。

手配された者へ衛兵を差し向ける仕掛けはゲームに元からある（GAME.md §2.20）。

    BattleStartManager(app, 'guard', None) -> execute -> start_battle
      -> create_guard_enemies -> guard_npc_generator / generate_enemy_instance_from_quest_dict

MOD がするのは、このマネージャを組んで `process_choice` に乗せることだけ。
強さは、`create_guard_enemies` の中から呼ばれる2か所に届く**難易度の数1つ**を差し替える
（式は発明しない。レベルも能力値もゲームがそこから決める。`316_bounty_hunter` の実機）。

    guards.install(ctx)                         # apply() の中。何本の MOD が呼んでも1つの世代に1回だけ包む
    phase = guards.build(app, difficulty)       # 組む。difficulty が None なら強さはゲームのまま
    screen.start_phase(app, phase, "衛兵")      # 起こすのは MOD（いつ起こすかは MOD が決める）

差し替えるのは、組んだマネージャの `start_battle` の中だけ。
時間で開けておくと、その間にゲーム自身が出した衛兵まで強くしてしまう（`316_` が以前に踏んだ）。
組んだマネージャには弱参照で印を付けるので、起こさずに捨てたマネージャの印は残らない。

`316_bounty_hunter`（追手）と `913_crime_incentive`（脱獄の決行）が使う。
"""
import sys
import weakref

from . import log, log_exc, ui

GUARD_ENEMY_TYPE = "guard"

START_TARGET = "__main__:BattleStartManager.start_battle"
DESCRIPTION_TARGET = "scripts.llm.llm_manager:guard_npc_generator"
ENEMY_TARGET = "__main__:InstantaleApp.generate_enemy_instance_from_quest_dict"

_STATE_ATTR = "_instantale_guards"
_GATE_ATTR = "_instantale_guards_gate"


def _state():
    state = getattr(sys, _STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {"armed": weakref.WeakKeyDictionary(), "inside": None, }
        setattr(sys, _STATE_ATTR, state)
    return state


def _write(text):
    """窓口の記録は `modloader.log` へ（どの MOD が先に包んだかで行き先を変えない）。"""
    try:
        log(text, level="WARN" if text.startswith("WARN") else "INFO")
    except Exception:
        pass


def is_number(value):
    """数として読める値か。`bool` は弾く（`True` を難易度1にしない）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def difficulty_index(args):
    """`generate_enemy_instance_from_quest_dict` の残りの引数のうち難易度の位置。

    実測の並びでは難易度だけが数なので、**数が1つだけならそれを採る**
    （引数が増減しても追随する）。複数あるときだけ実測の位置（5番目）に戻る。
    """
    numbers = [index for index, value in enumerate(args) if is_number(value)]
    if len(numbers) == 1:
        return numbers[0]
    if 4 in numbers:
        return 4
    return None


def arm(phase, difficulty):
    """組んだマネージャの戦闘の強さを決める。None なら外す。"""
    try:
        if difficulty is None:
            _state()["armed"].pop(phase, None)
        else:
            _state()["armed"][phase] = int(difficulty)
    except TypeError:
        _write("WARN guards: cannot mark {} (no weak reference)".format(type(phase).__name__))


def armed(phase):
    """そのマネージャに決めた難易度。決めていなければ None。"""
    try:
        return _state()["armed"].get(phase)
    except TypeError:
        return None


def build(app, difficulty=None):
    """ゲームの衛兵の戦闘のマネージャを組む。組めなければ None。

    `difficulty` を渡すと、このマネージャの戦闘だけ強さを差し替える（`install` が要る）。
    起こすのは呼ぶ側（`ui.Screen.start_phase`）。
    """
    cls = ui.cls_of("BattleStartManager")
    if cls is None:
        _write("WARN guards: BattleStartManager is not available")
        return None
    try:
        phase = cls(app, GUARD_ENEMY_TYPE, None)
    except Exception:
        log_exc("guards: cannot build the guard battle")
        return None
    if difficulty is not None:
        arm(phase, difficulty)
    return phase


def gate():
    return getattr(sys, _GATE_ATTR, None)


def install(ctx):
    """強さを差し替える3か所を包む。包んだ対象の名前を返す。

    何本の MOD が呼んでも、1つの世代につき1回だけ（`durations.install` と同じ形。TECH.md §5.8）。
    """
    generation = getattr(ctx, "generation", None)
    done = gate()
    if done is not None and done.get("generation") == generation:
        return list(done.get("targets") or [])

    def start_battle(orig, self, *args, **kwargs):
        """組んだマネージャの敵を組む間だけ、決めた難易度を開ける。"""
        difficulty = armed(self)
        if difficulty is None:
            return orig(self, *args, **kwargs)
        state = _state()
        state["inside"] = difficulty
        _write("guards: building the enemies with difficulty {}".format(difficulty))
        try:
            return orig(self, *args, **kwargs)
        finally:
            state["inside"] = None
            arm(self, None)

    def guard_npc_generator(orig, area=None, world=None, npc_difficulty_level=None,
                            *args, **kwargs):
        """敵の姿と説明。難易度は文章の強さ（頼み文）に効く。"""
        difficulty = _state()["inside"]
        if difficulty is not None and is_number(npc_difficulty_level):
            _write("guards: difficulty {} -> {} (description)".format(
                npc_difficulty_level, difficulty))
            npc_difficulty_level = difficulty
        return orig(area, world, npc_difficulty_level, *args, **kwargs)

    def enemy_instance(orig, self, enemy_dict=None, *args, **kwargs):
        """敵の実体。難易度からレベルと能力値が決まる。"""
        difficulty = _state()["inside"]
        if difficulty is not None:
            index = difficulty_index(args)
            if index is None:
                _write("WARN guards: cannot find the difficulty among {} argument(s); "
                       "leaving it to the game".format(len(args)))
            else:
                args = args[:index] + (difficulty,) + args[index + 1:]
        return orig(self, enemy_dict, *args, **kwargs)

    ctx.wrap(START_TARGET, required=False, safe=True)(start_battle)
    ctx.wrap(DESCRIPTION_TARGET, required=False, safe=True)(guard_npc_generator)
    ctx.wrap(ENEMY_TARGET, required=False, safe=True)(enemy_instance)
    targets = [START_TARGET, DESCRIPTION_TARGET, ENEMY_TARGET]
    setattr(sys, _GATE_ATTR, {"generation": generation, "targets": targets})
    _write("guards: wrapped {}".format(", ".join(targets)))
    return targets


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, _STATE_ATTR, None)
    setattr(sys, _GATE_ATTR, None)
