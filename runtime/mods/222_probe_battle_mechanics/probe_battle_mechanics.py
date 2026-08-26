# -*- coding: utf-8 -*-
"""計測: 戦闘の数の作られ方を録る。ゲームは変えない。

戦闘は「殴るだけで、実質死ぬまで殴り合い」に見えるが、
審判 LLM の語彙には防御・バフ・デバフ・状態異常が既にある
（`PowerModification` / `AttributeEffect` / `TextStatusEffect`。GAME.md §2.10.1）。
つまり大味さの原因は語彙の不足ではなく、
**語彙が数へ変換されるところ**のどこかにある。
戦闘を組み直す MOD を書く前に、その変換部を測る。

分かっていること:

  * 1手 = `handle_battle_situation` 1回。中で
    `calculate_battle_effect` → `resolve_battle_effect` → `process_battle_text`
    が回ると読める（GAME.md §2.10）
  * 実測のダメージは味方の一撃 800〜2200、同じ場の敵の HP は 60〜850
    （`out/battle_damage.log`。過剰打倒が常態）
  * 能力値は普通に作ると各 9〜16、才能点を積んで 30 が上端（GAME.md §2.17）
  * 敵はクエストの難易度1つから作られ、レベル＝難易度+1（GAME.md §2.20）

読めていないこと（この MOD が録る5つ）:

  1. **ダメージ式の形**。`get_instant_damage(attack, defense)` の入出力
  2. **attack の出どころ**。`get_base_damage_value(character_attack, weapon_attack)`
     の実引数と呼び出し元の連鎖
  3. **防御は効いているか**。`Character.get_npc_defense` の戻りと、
     `defense` に渡る実値（防具の`防御力`がここに来るのか）
  4. **語彙 → 数**。1手の `battle_action`（power / effect の列挙）と、
     その手の中で走った計算の対応
  5. **バフ・デバフの実体**。`AttributeEffect` / `TextStatusEffect` が
     キャラクタのどの属性へ書かれ、次の手の数を動かすか

## 測り方

**1手を文脈にして、その中で走った計算を全部ぶら下げる。**
`handle_battle_situation` の入りで手を開き、
`scripts.functions` の2関数と `get_npc_defense` は開いている手に
自分の入出力と呼び出し元（`frames.caller`）を積む。
手が閉じるとき、`battle_action`・計算の列・キャラクタの変化を1行にして書く。

キャラクタの変化は属性の総当たりで取る（`vars()` の浅い差分）。
バフ・デバフがどの属性へ書かれるかを推測しないためで、
属性名を先に決めて見張ると、正解が候補に無いとき何も見えない
（HUD の描画先を `texts` で探して外した実例。GAME.md §1.3）。
騒がしい属性（ログ・記憶・画像）だけ除外してある。

## 式は能動でも測る（グリッド）

引数が数値だけの2関数
（`get_instant_damage` / `get_base_damage_value`）に限り、
注入時に代表値の格子で直接呼んで対応表を書く。
受け身の記録だけだと、いま遊んでいる帯の値しか流れてこないので、
「レベルが上がると一撃になる」の曲線の形が出るまで何十戦も要る。
同じ点を `GRID_REPEATS` 回ずつ呼ぶのは乱数の幅を見るため
（1回では「たまたまの値」と「決まった値」の区別がつかない。
memory: 区別のつく状態を作ってから測る）。
純関数かどうかは確かめていないので、
1点目で例外が出たらグリッドごと諦めて記録に残す。`GRID_REPEATS=0` で切れる。

## 200番台の約束どおり読み取りだけ

書くのは `out/` の記録だけ。ゲームの状態・セーブ・画面には触らない。
能動で呼ぶのも上記の数値2関数と、戦闘開始時の `get_npc_defense`
（引数なしの読み取り）だけ。

出力は `out/battle_mechanics.log`（読む用）と
`out/battle_mechanics.jsonl`（1件1行）。
"""

import datetime
import json

from instantale_modloader import frames, ui

LOG_BASENAME = "battle_mechanics.log"
RECORD_BASENAME = "battle_mechanics.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
ACTION_SAMPLES = 300
GRID_REPEATS = 3

# 引数が数値だけの計算関数。1手の文脈に入出力をぶら下げ、グリッドでも呼ぶ。
PURE_TARGETS = ("get_base_damage_value", "get_instant_damage")

# 1手の中に積む計算の上限。全体攻撃＋継続ダメージでも溢れない程度。
CALLS_PER_ACTION = 24

# 戦闘開始の名簿を録る回数の上限（1戦闘1件。設定にするほど動かさない）。
ROSTER_SAMPLES = 40

# 属性の差分から除外する名前。
# 毎手必ず動く記録類と、差分に意味のない大物。
# ここに現れる変化が見たくなったら、その属性を名指しで録る MOD を別に書く。
NOISY_FIELDS = frozenset((
    "current_log", "life_log", "memory", "knowledges", "relationship",
    "image_src", "look", "look_description", "profile", "personality",
    "speech_style", "area_history", "story_achievements", "body_parts",
    "inventory", "config",
))

# グリッドの代表値。
# attack / defense は実測の帯（能力値 9〜30・武器の攻撃力 23〜245・
# 一撃 800〜2200）を跨ぐように置いてある。
GRID_ATTACK = (1, 2, 5, 10, 15, 20, 30, 50, 80, 120, 200, 300, 500, 1000)
GRID_DEFENSE = (0, 1, 2, 5, 10, 15, 20, 30, 50, 100, 200, 500)
# `get_base_damage_value` は中で `statistics.geometric_mean` を呼ぶので
# 0 を渡すと StatisticsError で死ぬ（1回目の実測でグリッドごと落ちた。
# VERIFICATION_LOG.md §2.68）。どちらの列も正の数だけにする。
GRID_CHARACTER_ATTACK = (1, 5, 9, 12, 16, 20, 26, 30, 50)
GRID_WEAPON_ATTACK = (1, 5, 23, 50, 96, 150, 245, 500, 1000, 2000)

ENEMY_SIDE = "enemy"
ALLY_SIDE = "ally"


def apply(ctx):
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    state = {
        "action": None,      # 開いている1手。閉じるときに1行になる
        "rows": 0,           # 書いた手の数（ACTION_SAMPLES で打ち切り）
        "rosters": 0,        # 書いた名簿の数
        "grid": False,       # グリッド実行中（受け身の記録を黙らせる）
    }

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("battle mechanics: record failed")

    def brief(value, depth=4):
        """値を JSON に写す。battle_action の入れ子（効果のリスト）が要るので深めに。"""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return frames.short(value, 160)
        if depth <= 0:
            return frames.short(frames.repr_value(value), 120)
        if isinstance(value, (list, tuple)):
            return [brief(item, depth - 1) for item in list(value)[:12]]
        if isinstance(value, dict):
            return {str(key): brief(value[key], depth - 1)
                    for key in list(value)[:16]}
        return frames.short(frames.describe_instance(value), 120)

    # ------------------------------------------------------------ 戦闘の面々
    # 敵は `current_enemy_dict`、味方は `app.player` と名簿の同行者（308_ と同じ形）。
    def combatants(app):
        found = []
        enemies = getattr(app, "current_enemy_dict", None)
        if isinstance(enemies, dict):
            for key, holder in list(enemies.items()):
                found.append((ENEMY_SIDE, str(key), holder))
        player = getattr(app, "player", None)
        if player is not None:
            found.append((ALLY_SIDE, ui.PLAYER_ID, player))
        try:
            member_ids = ui.party_member_ids(app)
        except Exception:
            member_ids = []
        for member_id in member_ids:
            member = ui.character_of(app, member_id)
            if member is not None:
                found.append((ALLY_SIDE, str(member_id), member))
        return found

    def fields_of(holder):
        """属性名 → 値。Character でも辞書でも読む（持ち主の型を決めつけない）。"""
        if isinstance(holder, dict):
            return dict(holder)
        try:
            return dict(vars(holder))
        except Exception:
            return {}

    def snapshot(holder):
        """差分用。値は短い repr の文字列にして比べる（同値なら同じ文字列）。"""
        shot = {}
        for name, value in fields_of(holder).items():
            if name in NOISY_FIELDS or name.startswith("_"):
                continue
            shot[name] = frames.short(frames.repr_value(value), 200)
        return shot

    def snapshot_all(app):
        return {(side, key): snapshot(holder)
                for side, key, holder in combatants(app)}

    def diff_all(before, after):
        """{誰: {属性: [前, 後]}}。居なくなった者・現れた者は属性 `<presence>` で出す。"""
        changed = {}
        for who in set(before) | set(after):
            b, a = before.get(who), after.get(who)
            if b is None or a is None:
                changed["/".join(who)] = {"<presence>": [b is not None, a is not None]}
                continue
            fields = {name: [b.get(name), a.get(name)]
                      for name in set(b) | set(a) if b.get(name) != a.get(name)}
            if fields:
                changed["/".join(who)] = fields
        return changed

    # ------------------------------------------------------------ 1手の文脈
    def open_action(kind, actor, side, battle_action, app):
        state["action"] = {
            "at": now(), "phase": kind,
            "actor": frames.short(actor, 60), "side": frames.short(side, 20),
            "battle_action": brief(battle_action),
            "calc": [], "effect": None,
            "_before": snapshot_all(app) if app is not None else {},
        }

    def close_action(app):
        action = state["action"]
        state["action"] = None
        if action is None:
            return
        if ACTION_SAMPLES > 0 and state["rows"] >= ACTION_SAMPLES:
            return
        state["rows"] += 1
        before = action.pop("_before")
        action["changed"] = diff_all(
            before, snapshot_all(app) if app is not None else {})
        record(action)
        write("{}: {} ({}) calc={} changed={}".format(
            action["phase"], action["actor"], action["side"],
            len(action["calc"]),
            list(action["changed"]) if action["changed"] else "-"))

    def push_calc(name, args, kwargs, result):
        action = state["action"]
        entry = {"func": name,
                 "args": [brief(value, 1) for value in args],
                 "result": brief(result, 1),
                 "caller": frames.caller(depth=3)}
        if kwargs:
            entry["kwargs"] = {key: brief(value, 1)
                               for key, value in kwargs.items()}
        if action is not None:
            if len(action["calc"]) < CALLS_PER_ACTION:
                action["calc"].append(entry)
            return
        # 手の外で走った計算（敵の生成時など）。文脈なしの1行で残す。
        if ACTION_SAMPLES > 0 and state["rows"] < ACTION_SAMPLES:
            state["rows"] += 1
            entry.update({"at": now(), "phase": "手の外の計算"})
            record(entry)

    # ------------------------------------------------------------ 計算の見張り
    def watch_pure(name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def pure(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            if not state["grid"]:
                try:
                    push_calc(name, args, kwargs, result)
                except Exception:
                    # 記録に失敗しても戻り値は素通しする（戦闘を止めない）。
                    pass
            return result
        return pure

    for name in PURE_TARGETS:
        watch_pure(name)

    @ctx.wrap("scripts.characters:Character.get_npc_defense",
              required=False, safe=True)
    def get_npc_defense(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            push_calc("get_npc_defense",
                      (frames.attr(self, "name", "?"),), kwargs, result)
        except Exception:
            pass
        return result

    # ================================================================ 1手
    @ctx.wrap("__main__:BattlePhaseManager.handle_battle_situation",
              required=False, safe=True)
    def handle_battle_situation(orig, self, character_key=None,
                                character_side=None, battle_action=None,
                                *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        try:
            open_action("1手", character_key, character_side, battle_action, app)
        except Exception:
            ctx.log_exc("battle mechanics: cannot open the action")
        result = orig(self, character_key, character_side, battle_action,
                      *args, **kwargs)
        try:
            close_action(app)
        except Exception:
            ctx.log_exc("battle mechanics: cannot close the action")
        return result

    # `calculate_battle_effect` の戻り値が「語彙 → 数」の変換結果そのもの。
    # 開いている手にぶら下げる（手の外で呼ばれたらそれ自体を1行にする）。
    @ctx.wrap("__main__:BattlePhaseManager.calculate_battle_effect",
              required=False, safe=True)
    def calculate_battle_effect(orig, self, battle_action=None, *args, **kwargs):
        result = orig(self, battle_action, *args, **kwargs)
        try:
            action = state["action"]
            if action is not None:
                action["battle_action"] = brief(battle_action)
                action["effect"] = brief(result)
            elif ACTION_SAMPLES > 0 and state["rows"] < ACTION_SAMPLES:
                state["rows"] += 1
                record({"at": now(), "phase": "手の外の calculate",
                        "battle_action": brief(battle_action),
                        "effect": brief(result)})
        except Exception:
            pass
        return result

    # 毒などの継続分。1手と同じ形で、キャラクタ1人ぶんの小さな手として録る。
    @ctx.wrap("__main__:BattlePhaseManager.reduce_status_turns_and_log",
              required=False, safe=True)
    def reduce_status_turns_and_log(orig, self, character=None, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        opened = False
        try:
            if state["action"] is None:
                open_action("継続効果", frames.attr(character, "name", "?"),
                            "-", None, app)
                opened = True
        except Exception:
            ctx.log_exc("battle mechanics: cannot open the status turn")
        result = orig(self, character, *args, **kwargs)
        if opened:
            try:
                close_action(app)
            except Exception:
                ctx.log_exc("battle mechanics: cannot close the status turn")
        return result

    # ================================================================ 名簿
    def roster_entry(side, key, holder):
        entry = {"side": side, "key": frames.short(key, 60),
                 "name": frames.short(frames.attr(holder, "name", key)
                                      if not isinstance(holder, dict)
                                      else holder.get("name", key), 60)}
        for field in ("current_hp", "max_hp", "physical_integrity",
                      "max_physical_integrity", "experience_level",
                      "ability_scores", "original_ability_scores",
                      "status", "state", "traits", "skills", "equipments",
                      "weakness", "tactics"):
            value = (holder.get(field) if isinstance(holder, dict)
                     else frames.attr(holder, field, None))
            if value is not None:
                entry[field] = brief(value)
        # 防御の実値。引数なしの読み取りなので戦闘開始時に1回だけ能動で聞く。
        try:
            getter = getattr(holder, "get_npc_defense", None)
            if callable(getter):
                entry["npc_defense"] = brief(getter(), 1)
        except Exception:
            entry["npc_defense"] = "<failed>"
        return entry

    @ctx.wrap("__main__:BattleStartManager.start_battle",
              required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if state["rosters"] < ROSTER_SAMPLES:
                state["rosters"] += 1
                app = getattr(self, "app", None) or ui.find_app()
                roster = [roster_entry(side, key, holder)
                          for side, key, holder in combatants(app)]
                record({"at": now(), "phase": "戦闘開始", "roster": roster})
                write("戦闘開始: {}".format(
                    ", ".join("{}({}) def={}".format(
                        entry["name"], entry.get("current_hp"),
                        entry.get("npc_defense")) for entry in roster)))
        except Exception:
            ctx.log_exc("battle mechanics: cannot record the roster")
        return result

    # ================================================================ グリッド
    def grid_of(name, first_values, second_values):
        try:
            found = ctx.resolve("scripts.functions:{}".format(name))
        except Exception:
            # ゲームの外（オフライン検証）では scripts.functions が無い。
            # 見つからないのは異常ではないので静かに諦める。
            found = None
        func = found[2] if found else None
        if not callable(func):
            write("グリッド: {} が見つからないので諦めた".format(name))
            return
        points = 0
        for a in first_values:
            for b in second_values:
                results = []
                for _ in range(GRID_REPEATS):
                    results.append(func(a, b))
                record({"at": now(), "phase": "式のグリッド", "func": name,
                        "args": [a, b], "results": [brief(r, 1) for r in results]})
                points += 1
        write("グリッド: {} を {}点 × {}回 記録した（例: {}{} -> {}）".format(
            name, points, GRID_REPEATS,
            name, (first_values[0], second_values[0]),
            brief(func(first_values[0], second_values[0]), 1)))

    def run_grid():
        if GRID_REPEATS <= 0:
            return
        state["grid"] = True
        try:
            grid_of("get_instant_damage", GRID_ATTACK, GRID_DEFENSE)
            grid_of("get_base_damage_value",
                    GRID_CHARACTER_ATTACK, GRID_WEAPON_ATTACK)
        except Exception:
            # 純関数かどうかは確かめていない。落ちたらグリッドごと諦めて残す。
            ctx.log_exc("battle mechanics: the grid died; giving it up")
        finally:
            state["grid"] = False

    ctx.on_ready(run_grid)

    ctx.log("battle mechanics probe: log -> {} (actions<={} grid x{})".format(
        ctx.out_path(LOG_BASENAME), ACTION_SAMPLES, GRID_REPEATS))
