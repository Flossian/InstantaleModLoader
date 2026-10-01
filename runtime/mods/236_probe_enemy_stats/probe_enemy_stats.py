# -*- coding: utf-8 -*-
r"""計測: 敵と味方の強さの出どころ（HP・能力値・防御・素点）。ゲームは変えない。

`319_battle_tactics` の式を「HP・攻撃・防御を全員が持つ形」に組み直すための下調べ。
319 の `hit:` の記録からは、同じレベルの敵でも素点（`get_instant_damage` に渡る攻撃）・
HP・防御が別々に散っていることまでは読めた（Lv77 で素点 343〜791、HP 720〜1376、防御 200〜328）。
読めていないのは、その差が**何から来るか**。

録るもの:

  * 敵の能力値の生まれ方: `scripts.functions` の
    `get_enemy_attributes_base_point(段, 難易度)` / `get_character_attributes(基準点, 型)` /
    `get_npc_attributes(値)` の引数と戻り（`kind: gen`）
  * 戦闘の開始（`BattleStartManager.start_battle` の戻りの後）: 敵全員と味方全員の値を写す
    （名前・レベル・HP・能力値・`get_npc_defense()`・型や大きさらしい項目・スキルの強度・装備・
    戦闘の種類・ボスの旗。`kind: start`）。項目名が分からない値もあるので、数と短い文字列の項目は全部写す
  * 1手の素点: `BattlePhaseManager.calculate_battle_effect(battle_action)` の中で、
    その手の裁き（`instant_damage` の `power` / `multiplier` / `category`）と、
    その間に呼ばれた `get_base_damage_value` / `get_instant_damage` の引数と戻り（`kind: act`）。
    `resolve_battle_effect` の中の `get_instant_damage` も同じ手に束ねる
  * 版2: `calculate_battle_effect` については、渡された `battle_action` の全体・戻り値・呼んだ後の
    `battle_action`・前後で変わった全員の HP と状態異常（`state_changed`）も録る。
    版1の実機で、この段の中では `get_base_damage_value` しか呼ばれず、HP に当たる
    `get_instant_damage` は `resolve_battle_effect` にしか無かった。
    この段だけをゲームに試し打ちさせて素点を測れるか（状態を動かさないか、素点がどこに出るか）の判断材料
  * 版3: 設定「素点の試し打ち」（`DRY_RUN`。既定 OFF）が ON のとき、戦闘の開始で
    敵1体・味方1人ごとに、`instant_damage` を1本だけ持つ行動を作ってゲームの
    `calculate_battle_effect` に渡し、戻った素点を録る（`kind: dry`）。
    裁きは weak〜extreme の5段 × 物理・魔法、倍率 1。weak の物理だけ倍率 2 と 0.67 も足す。
    ゲームの素点には揺らぎがあるので、1通りにつき `DRY_REPEAT` 回呼ぶ。
    版2の実機で、この段は HP も状態異常も `battle_action` も動かさず（18手）、
    素点を戻り値の1つ目の枠 `{相手: 素点}` に返すと分かった。
    それでも呼ぶ前後で全員の HP と状態異常を比べ、動いていたらその場で止めて以後は試さない（`kind: dry_abort`）。
    319 が1手の値を控えるのはゲームの `handle_battle_situation` の間だけなので、戦闘の開始なら控えは空で上書きしない。
    試し打ちの間は、この probe 自身の `act` の記録を止める
  * 版3の続き（リリース前）: 試し打ちを「副作用なし・最小限」に絞った（版3の実機で問いは決着した。GAME.md §2.10.4）。
    - ゲームの素点には乱数の揺らぎがあるので、試し打ちがゲームの乱数を引いて乱数の列をずらしていた
      （戦闘の開始ごとに「人数 × 12通り × 回数」、5人で 180 回）。呼ぶ前に `random` の全体の状態と、
      numpy の全体の状態（import 済みのときだけ）と、`__main__` / `scripts.functions` が持つ
      `random.Random` の状態を控え、終わったら戻す（`rng_saved`）
    - 同じ相手（名前とレベル）は1プロセス1回だけ試す（控えは `sys` の `DRY_SEEN_MARK`。注入し直しても出直さない）
    - 倍率の2通り（weak の物理の 2 と 0.67）は外した。倍率がこの段に入らないことは版3で決着している
    - 試し打ちの間は `sys` の `DRY_THREAD_MARK` に自分のスレッドの id を置く。
      同じ `calculate_battle_effect` を包む他の probe（`222_`）が、これを見て記録を控えられるようにするため
      （版3の実機では、222 が試し打ちの呼び出しを「手の外の calculate」として約 1,400 行録っていた）
    - `start_battle` の包みで、try の中で作る値を try の外の試し打ちが使っていたので、先に None で用意した
  * 版4: 戦闘の開始の写しで `get_npc_defense()` を呼ぶ間も `DRY_THREAD_MARK` を置く。
    置いていなかったので、`222_` がこの呼び出しを「手の外の計算」として録り、件数の枠も食っていた

この probe は読み込み順で `319_` / `333_` より外側に入る。
`get_instant_damage` の引数（素点と防御）は誰も書き換えないので素の値が録れるが、戻りは 319 が置き換えた後の値。
ゲーム自身の結果は引数から引き算で出る（GAME.md §2.10.2）。
`get_base_damage_value` の引数は 333 が武器を合算に差し替える前の値。

読む用は `out\enemy_stats.log`、数える用は `out\enemy_stats.jsonl`。
"""

import datetime
import random
import sys
import threading
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "enemy_stats.log"
RECORD_BASENAME = "enemy_stats.jsonl"

#: 写す項目のうち、長さを切る上限（文字）。
TEXT_CHARS = 80

#: 写さない項目（大きいか、強さに関係しない）。
SKIP_FIELDS = frozenset((
    "memory", "life_log", "current_log", "knowledges", "profile", "personality",
    "look_description", "relationship", "area_history", "story_achievements",
    "image_src", "config", "inventory", "description", "speech_style", "tactics",
    "requirements_to_strike_weakness", "weakness", "body_parts", "location",
))

#: 戦闘の旗（`107_` の表と同じ3つ）。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
#: 戦闘の開始で、ゲームに素点の計算を試し打ちさせる（版3）。
DRY_RUN = False
#: 1通りにつき何回呼ぶか（素点の揺らぎを見る）。
DRY_REPEAT = 3

#: 試し打ちの裁き。
DRY_POWERS = ("weak", "normal", "strong", "very_strong", "extreme")
DRY_CATEGORIES = ("physical", "magical")
#: 倍率の掛かり方を見る分。版3では weak の物理に 2 と 0.67 を足していたが、
#: 倍率がこの段に入らないと決着したので、版3の続きで外した。
DRY_EXTRA = ()

#: 試し打ちを済ませた相手（名前とレベル）の控え。1プロセス1回にするため `sys` に置く（版3の続き）。
DRY_SEEN_MARK = "_instantale_probe_236_dry_seen"

#: 試し打ちの間だけ、試し打ちをしているスレッドの id を置く（版3の続き）。
#: 戦闘の開始の写しで `get_npc_defense` を呼ぶ間も置く（版4）。
#: 同じ関数を包む他の probe は、`getattr(sys, DRY_THREAD_MARK, None) == threading.get_ident()`
#: のあいだ記録を控えてよい。
DRY_THREAD_MARK = "_instantale_probe_dry_run_thread"

#: 乱数の控えを探すモジュール（`random.Random` を持っていれば状態を戻す。版3の続き）。
RNG_MODULES = ("__main__", "scripts.functions")


def rng_saved():
    """いまの乱数の状態を控える。`[(戻す関数, 状態, 名前)]`。"""
    saved = [(random.setstate, random.getstate(), "random")]
    numpy = sys.modules.get("numpy")
    legacy = getattr(getattr(numpy, "random", None), "get_state", None)
    if callable(legacy):
        try:
            saved.append((numpy.random.set_state, legacy(), "numpy.random"))
        except Exception:
            pass
    seen = {id(getattr(random, "_inst", None))}
    for name in RNG_MODULES:
        module = sys.modules.get(name)
        try:
            items = list(vars(module).items()) if module is not None else []
        except Exception:
            items = []
        for key, value in items:
            if isinstance(value, random.Random) and id(value) not in seen:
                seen.add(id(value))
                saved.append((value.setstate, value.getstate(), "{}.{}".format(name, key)))
    return saved


def rng_restore(saved):
    for setter, state, _name in saved:
        try:
            setter(state)
        except Exception:
            pass


def dry_seen():
    """試し打ちを済ませた相手の控え（1プロセスに1つ）。"""
    found = getattr(sys, DRY_SEEN_MARK, None)
    if not isinstance(found, set):
        found = set()
        setattr(sys, DRY_SEEN_MARK, found)
    return found


def dry_actions(actor, target):
    """試し打ちの行動の並び。形は版2で録った `battle_action` と同じ8つの鍵。"""
    plans = [(p, c, 1) for p in DRY_POWERS for c in DRY_CATEGORIES] + list(DRY_EXTRA)
    actions = []
    for power, category, multiplier in plans:
        actions.append(((power, category, multiplier), {
            "actor": actor, "narration": "", "vfx": "slash",
            "instant_damage": [{"target": target, "category": category,
                                "power": power, "multiplier": multiplier}],
            "instant_heal": [], "text_status": [], "escape_from_battle": [],
            "other_action": []}))
    return actions


def plain(value, depth=0):
    """JSON に載る形へ縮める。数・真偽・短い文字列・その入れ子だけ残す。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return frames.short(value, TEXT_CHARS)
    if depth >= 3:
        return frames.short(repr(value), TEXT_CHARS)
    if isinstance(value, dict):
        return {str(k): plain(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [plain(v, depth + 1) for v in list(value)[:20]]
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return "<{} {}>".format(type(value).__name__, frames.short(name, 40))
    return frames.short(repr(value), TEXT_CHARS)


def fields_of(character):
    """Character の項目のうち、数と短い値を写す（項目名はゲームのまま）。"""
    out = {}
    try:
        items = vars(character).items()
    except TypeError:
        return out
    for key, value in items:
        if key.startswith("_") or key in SKIP_FIELDS:
            continue
        if key == "skills" and isinstance(value, dict):
            out[key] = {name: [(plain(e.get("type")), plain(e.get("power")),
                                plain(e.get("target_type")))
                               for e in (s.get("effects") or []) if isinstance(e, dict)]
                        + [plain(s.get("current_uses")), plain(s.get("max_uses"))]
                        for name, s in value.items() if isinstance(s, dict)}
            continue
        if isinstance(value, (bool, int, float, str, type(None), dict, list, tuple)):
            out[key] = plain(value)
    return out


def snapshot(character):
    row = {"name": plain(getattr(character, "name", None)), "fields": fields_of(character)}
    for method in ("get_npc_defense", "get_attribute_average"):
        fn = getattr(character, method, None)
        if callable(fn):
            try:
                row[method] = plain(fn())
            except Exception as exc:
                row[method] = "error: {}".format(type(exc).__name__)
    return row


def damage_entries(battle_action):
    """`battle_action` の `instant_damage` を `(target, power, multiplier, category)` の並びに。"""
    found = []
    damages = battle_action.get("instant_damage") if isinstance(battle_action, dict) else None
    for entry in damages or []:
        if isinstance(entry, dict):
            found.append({k: plain(entry.get(k))
                          for k in ("target", "power", "multiplier", "category")})
    return found


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    started = time.monotonic()
    local = threading.local()        # いま中に居る手（calculate / resolve の間だけ）

    def event(kind, **fields):
        row = {"at": datetime.datetime.now().isoformat(timespec="milliseconds"),
               "t": round(time.monotonic() - started, 3), "kind": kind}
        row.update(fields)
        record(row)
        write("{:>9.3f} {} {}".format(row["t"], kind, frames.short(
            " ".join("{}={}".format(k, v) for k, v in fields.items()), 400)))

    # ------------------------------------------------------------ 能力値の生まれ方
    def watch_gen(name):
        def wrapper(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                event("gen", fn=name, args=plain(list(args)), kwargs=plain(kwargs),
                      result=plain(result), caller=frames.caller(4))
            except Exception:
                ctx.log_exc("enemy stats probe: recording {} failed".format(name))
            return result
        return wrapper

    for _name in ("get_enemy_attributes_base_point", "get_character_attributes",
                  "get_npc_attributes"):
        ctx.wrap("scripts.functions:" + _name, required=False, safe=True)(watch_gen(_name))

    # ------------------------------------------------------------ 戦闘の開始
    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        # 版3の続き: 試し打ちは try の外で使うので先に用意する（中で投げると NameError になっていた）。
        app, enemies, allies = None, None, []
        try:
            app = getattr(self, "app", None) or ui.find_app()
            enemies = getattr(app, "current_enemy_dict", None)
            allies = [getattr(app, "player", None)]
            for member_id in ui.party_member_ids(app):
                allies.append(ui.character_of(app, member_id))
            # 版4: 写しで能動に呼ぶ get_npc_defense も、試し打ちと同じ印の下で呼ぶ。
            # 印が無いと、同じ関数を包む 222_ がこれを「手の外の計算」として録り、件数の枠も食う。
            setattr(sys, DRY_THREAD_MARK, threading.get_ident())
            try:
                event("start",
                      enemy_type=plain(getattr(self, "enemy_type", None)),
                      flags=[f for f in BATTLE_FLAGS if getattr(app, f, False)],
                      area=plain(getattr(ui.current_area(app), "name", None)),
                      enemies={str(k): snapshot(v) for k, v in (enemies or {}).items()}
                      if isinstance(enemies, dict) else plain(enemies),
                      allies=[snapshot(c) for c in allies if c is not None])
            finally:
                try:
                    delattr(sys, DRY_THREAD_MARK)
                except AttributeError:
                    pass
        except Exception:
            ctx.log_exc("enemy stats probe: recording the battle start failed")
        if DRY_RUN and not dry_state["stopped"] and app is not None:
            try:
                dry_run(app, enemies, [c for c in allies if c is not None])
            except Exception:
                dry_state["stopped"] = True
                ctx.log_exc("enemy stats probe: the dry run failed; no more dry runs")
            finally:
                local.dry = False
                local.act = None
        return result

    # ------------------------------------------------------------ 素点の試し打ち（版3）
    dry_state = {"stopped": False, "rng_noted": False}

    def dry_run(app, enemies, allies):
        manager_cls = ui.cls_of("BattlePhaseManager")
        if manager_cls is None or not isinstance(enemies, dict) or not enemies:
            event("dry_skip", reason="no BattlePhaseManager or no enemies")
            return
        manager = manager_cls.__new__(manager_cls)
        manager.app = app
        calculate = getattr(manager_cls, "calculate_battle_effect")
        first_enemy = str(next(iter(enemies)))
        player_name = str(getattr(allies[0], "name", "")) if allies else ""
        people = [("enemy", str(k), v, player_name) for k, v in enemies.items()]
        people += [("ally", str(getattr(c, "name", "?")), c, first_enemy) for c in allies]
        # 版3の続き: 同じ相手（名前とレベル）は1プロセス1回だけ。
        seen = dry_seen()
        fresh = []
        for side, name, person, target in people:
            key = (side, name, plain(getattr(person, "experience_level", None)))
            if key not in seen:
                fresh.append((key, side, name, person, target))
        if not fresh:
            return
        before = combatants_state(app)
        # 版3の続き: ゲームの素点には乱数が入るので、呼ぶ前の乱数の状態を控えて最後に戻す。
        rng = rng_saved()
        local.dry = True
        setattr(sys, DRY_THREAD_MARK, threading.get_ident())
        count = 0
        try:
            for key, side, name, person, target in fresh:
                seen.add(key)
                for plan, action in dry_actions(person, target):
                    raws = []
                    calls = []
                    for _ in range(max(1, DRY_REPEAT)):
                        local.act = {"calls": []}
                        got = calculate(manager, action)
                        first = got[0] if isinstance(got, (list, tuple)) and got else None
                        raws.append(first.get(target) if isinstance(first, dict) else plain(got))
                        calls = local.act["calls"]
                    count += 1
                    event("dry", side=side, actor=name,
                          level=plain(getattr(person, "experience_level", None)),
                          attack_power=plain(getattr(person, "attack_power", None)),
                          magic_power=plain(getattr(person, "magic_power", None)),
                          power=plan[0], category=plan[1], multiplier=plan[2],
                          raws=raws, calls=calls)
        finally:
            local.dry = False
            try:
                delattr(sys, DRY_THREAD_MARK)
            except AttributeError:
                pass
            rng_restore(rng)
        if not dry_state["rng_noted"]:
            dry_state["rng_noted"] = True
            write("dry run: restored the random state of {}".format(
                ", ".join(name for _setter, _state, name in rng)))
        people = [(side, name, person, target)
                  for _key, side, name, person, target in fresh]
        after = combatants_state(app)
        if after != before:
            dry_state["stopped"] = True
            event("dry_abort", changed={k: [before.get(k), v] for k, v in after.items()
                                        if before.get(k) != v})
            ctx.log("enemy stats probe: the dry run changed the battle; stopped", level="WARN")
        else:
            write("dry run: {} plan(s) x {} for {} combatant(s)".format(
                count // max(1, len(people)), DRY_REPEAT, len(people)))

    # ------------------------------------------------------------ 1手の素点
    def open_act(stage, self, battle_action):
        act = getattr(local, "act", None)
        if act is None:
            act = {"stage": stage, "actor": plain(battle_action.get("actor"))
                   if isinstance(battle_action, dict) else None,
                   "entries": damage_entries(battle_action), "calls": []}
            local.act = act
            return act, True
        return act, False

    def close_act(act):
        local.act = None
        if act["entries"] or act["calls"]:
            event("act", **act)

    def combatants_state(app):
        """全員の HP と状態異常（`calculate` の前後で比べる。版2）。"""
        state = {}
        people = list((getattr(app, "current_enemy_dict", None) or {}).values())
        people.append(getattr(app, "player", None))
        for member_id in ui.party_member_ids(app):
            people.append(ui.character_of(app, member_id))
        for person in people:
            if person is None:
                continue
            state[str(getattr(person, "name", "?"))] = plain({
                "current_hp": getattr(person, "current_hp", None),
                "max_hp": getattr(person, "max_hp", None),
                "status": getattr(person, "status", None)})
        return state

    def watch_turn(stage):
        def wrapper(orig, self, *args, **kwargs):
            if getattr(local, "dry", False):
                return orig(self, *args, **kwargs)     # 試し打ちは dry の行で録る
            battle_action = args[0] if args else kwargs.get("battle_action")
            before = None
            try:
                act, mine = open_act(stage, self, battle_action)
                if mine and stage == "calculate":
                    # 版2: 素点がどこに出るか（戻りか、battle_action への書き込みか）と、
                    # この段が HP や状態を動かすかを録る。試し打ち（案 A）に使えるかの判断材料
                    act["action"] = plain(battle_action)
                    before = combatants_state(getattr(self, "app", None) or ui.find_app())
            except Exception:
                act, mine = None, False
            result = None
            try:
                result = orig(self, *args, **kwargs)
                return result
            finally:
                if mine:
                    try:
                        if stage == "calculate":
                            act["result"] = plain(result)
                            act["action_after"] = plain(battle_action)
                            after = combatants_state(getattr(self, "app", None) or ui.find_app())
                            act["state_changed"] = {k: [before.get(k), v] for k, v in after.items()
                                                    if before is not None and before.get(k) != v}
                        close_act(act)
                    except Exception:
                        ctx.log_exc("enemy stats probe: recording a turn failed")
        return wrapper

    ctx.wrap("__main__:BattlePhaseManager.calculate_battle_effect", required=False,
             safe=True)(watch_turn("calculate"))
    ctx.wrap("__main__:BattlePhaseManager.resolve_battle_effect", required=False,
             safe=True)(watch_turn("resolve"))

    def watch_math(name):
        def wrapper(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            act = getattr(local, "act", None)
            if act is not None and len(act["calls"]) < 40:
                act["calls"].append({"fn": name, "args": plain(list(args)),
                                     "result": plain(result)})
            return result
        return wrapper

    for _name in ("get_base_damage_value", "get_instant_damage"):
        ctx.wrap("scripts.functions:" + _name, required=False, safe=True)(watch_math(_name))

    ctx.log("enemy stats probe: installed (log -> {})".format(ctx.out_path(LOG_BASENAME)))
