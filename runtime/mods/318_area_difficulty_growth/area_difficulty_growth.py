# -*- coding: utf-8 -*-
"""依頼を片付けるたび、その土地の依頼の難易度が上がっていく。

素のゲームでは、土地の難易度は世界を作った時点で決まったまま動かない。
最初の町の依頼は 3,4,5 のまま何十回通っても 3,4,5 で、
店に並ぶ品も、鍛冶で打てるものも、そこで止まる。
土地を移れば強くなるが、**同じ土地に居続けても何も育たない**。

この MOD が動かすのは1つの数だけ:

    world.quests[id].difficulty          その依頼の難易度（0..76）

ゲーム自身の項目なので、セーブに MOD 独自の鍵は増えない（TECH.md §6.4）。
素の値は `state/` に控えるので、いつでも元へ戻せる（`ROLLBACK`）。

## なぜ「依頼の難易度」1つで足りるのか

**その土地の物価と品揃えは、その土地の依頼の難易度そのものだから**。
実セーブ3世界・店23軒を突き合わせた結果（GAME.md §2.13.1）:

    ペルディション エリア2   依頼 27 / 30 / 31（3件とも完了済み）
      general_store          在庫の value = 5,5,27,27,30,30,31,31,31
      medical_facility       在庫の value = 27,27,27,27,27,30,30,31
      specialty_shop         在庫の value = 27,27,27,27,30,30,30,30,31

在庫の `value` は**その土地の依頼の難易度そのもの**（190個中 186個）。
外れた4個は2軒に集まっていて、どれもその土地には無い難易度
（上の 5,5 を含む。プレイヤーが売った品が主の持ち物へ積まれる経路と読める）。
完了済みの依頼も母数に入る。
ゲーム側の入口は `get_area_quest_difficulty_for_tier(area, world, tier)` /
`get_quest_difficulties(area, world, include_completed=True)`。

だからこの MOD は在庫にもクラフトにも手を触れない。
依頼の難易度を上げれば、そこから下流が全部付いてくる:

    依頼の難易度
      ├ 敵         get_enemy_exp_lvl / get_enemy_attributes_base_point（レベル＝難易度+1）
      ├ 報酬・才能 get_quest_reward / get_talent_point_quest_clear / get_labor_reward
      ├ 店の在庫   get_area_quest_difficulty_for_tier → 品の value
      │              （並べ替わるのは `312_shop_restock` の入れ替え日から）
      └ クラフト   在庫と戦利品の value が上がる → 素材の値段が上がる
                     → ItemCraftManager.calculate_modification(item_type, item_price)

いちばん下のクラフトだけ、引数の名前からの読みで実測がまだ無い。
`221_probe_item_level` がその1本を録る（VERIFICATION.md §3.38）。

## 上げ方

土地ごとにクリア回数を数え、段数に直して素の難易度へ乗せる:

    段     = クリア回数 // CLEARS_PER_STEP
    上昇量 = min(MAX_BONUS, 段 * STEP_SIZE)
    難易度 = min(素の難易度 + 上昇量, 上限)

**差分を足していくのではなく、毎回「素の値 + いまの上昇量」を書く。**
何度走っても同じ値に落ち着き、途中で設定を変えても
「そのとき正しい高さ」へ寄る（差分を足す形だと、取りこぼしと二重掛けが
どちらも黙って積み上がる）。

素の値の控え（`base`）は、その依頼を初めて触ったときに取る。
**新しく生まれた依頼は、生まれた時点で既に土地の上昇量を含んでいる**
（ゲームは土地の依頼の帯から新しい依頼の難易度を選ぶ。`206_` の実測で、
帯 [5,4,3] の町から出た新規依頼は 3 と 5）。
だから初見の控えは「いまの難易度 - いま乗っている上昇量」で取る。
ここを素通しにすると、生成のたびに上昇量が二重に乗る。

## 上げる場面

| 場面 | すること |
| --- | --- |
| `QuestEndManager.execute` | その依頼の土地のクリア回数を +1 して、寄せ直す |
| `DisplayQuestChoice.__init__` | 掲示板を開いた土地を寄せ直す（設定を変えた直後もここで揃う） |

依頼がどの土地のものかは `neighboring_settlement_id`
（掲示板の絞り込みにゲーム自身が使っている鍵。GAME.md §2.9）。
いま居る土地ではなく**依頼の土地**を数えるので、
道中で受けた依頼を別の町で終わらせても出所に付く。

## 外し方

`ROLLBACK` を入れると、次にその土地の掲示板を開いた時点で
控えた素の値へ戻して控えごと消す。
MOD を消すだけだと、上がった難易度はセーブに残ったままになる
（ゲーム自身の項目なので、MOD が無くてもそのまま動く。害は無いが元にも戻らない）。
"""

import sys
import threading

from instantale_modloader import ui
from instantale_modloader.state import world_filename, world_key

LOG_BASENAME = "area_difficulty.log"
STATE_DIRNAME = "area_difficulty"

#: ゲーム自身の難易度の上限（`scripts.functions.QUEST_DIFFICULTY_VALUE_MAX`）。
#: 引けなかったときの落とし先で、引ければそちらが優先。
GAME_DIFFICULTY_MAX = 76
GAME_DIFFICULTY_MIN = 0

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
CLEARS_PER_STEP = 3
STEP_SIZE = 3
MAX_BONUS = 30
DIFFICULTY_LIMIT = 76
SCOPE = "all"
ROLLBACK = False
ANNOUNCE = "この地に貼り出される依頼は、以前より歯応えのあるものになっている。"


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="area difficulty")

    # 控えはゲームのスレッドと Clock コールバックの両方から触る。
    lock = threading.RLock()
    cache = {"buckets": {}}
    # 上限で頭打ちになった土地は一度だけ知らせる（毎回の掲示板で出さない）。
    capped = set()

    # ------------------------------------------------------------ 設定の読み
    def per_step():
        return max(1, int(CLEARS_PER_STEP))

    def step_size():
        return max(0, int(STEP_SIZE))

    def max_bonus():
        return max(0, int(MAX_BONUS))

    def bonus_for(cleared):
        """クリア回数に見合う上昇量。`ROLLBACK` のときは常に 0。"""
        if ROLLBACK:
            return 0
        return min(max_bonus(), (max(0, int(cleared)) // per_step()) * step_size())

    def limits():
        """難易度の下限・上限。ゲームの値と設定の低いほうを採る。"""
        functions = sys.modules.get("scripts.functions")
        low = getattr(functions, "QUEST_DIFFICULTY_VALUE_MIN", None) if functions else None
        high = getattr(functions, "QUEST_DIFFICULTY_VALUE_MAX", None) if functions else None
        if not isinstance(low, int) or isinstance(low, bool):
            low = GAME_DIFFICULTY_MIN
        if not isinstance(high, int) or isinstance(high, bool):
            high = GAME_DIFFICULTY_MAX
        return low, min(high, max(low, int(DIFFICULTY_LIMIT)))

    # ------------------------------------------------------------ 控え
    def path_for(key):
        # フォルダを作るのはここ（`ctx.state_path` が親を作る）。
        # `apply()` では作らない。一度も上がっていない `state/` に
        # 空のフォルダを置かないため（TECH.md §3.11）。
        return ctx.state_path(STATE_DIRNAME, world_filename(key))

    def bucket_of(key):
        """その世界の控え `{土地id: 記録}`。無ければ読み込む。錠は呼び側が持つ。"""
        found = cache["buckets"].get(key)
        if found is None:
            data = ctx.read_json(path_for(key), {})
            found = data if isinstance(data, dict) else {}
            cache["buckets"][key] = found
        return found

    def ordered(record):
        """並びを固定して書く（差分を読むとき、順が動くと全行が動いて見える）。"""
        out = {}
        for name in ("cleared", "step", "bonus", "day"):
            if record.get(name) is not None:
                out[name] = record[name]
        base = record.get("base")
        if base:
            out["base"] = {qid: base[qid] for qid in sorted(base, key=ui.id_sort_key)}
        return out

    def save(key, area_id, record):
        """1つの土地の記録を書き戻す。空の記録は行ごと落とす。"""
        bucket = bucket_of(key)
        if record is None:
            bucket.pop(str(area_id), None)
        else:
            bucket[str(area_id)] = ordered(record)
        data = {aid: bucket[aid] for aid in sorted(bucket, key=ui.id_sort_key)}
        cache["buckets"][key] = data
        if not ctx.write_json(path_for(key), data):
            write("控えを書けなかった: {}".format(path_for(key)))
            return False
        return True

    def record_of(key, area_id):
        found = bucket_of(key).get(str(area_id))
        record = dict(found) if isinstance(found, dict) else {}
        base = record.get("base")
        record["base"] = dict(base) if isinstance(base, dict) else {}
        record["cleared"] = _int(record.get("cleared"), 0)
        record["bonus"] = _int(record.get("bonus"), 0)
        return record

    # ------------------------------------------------------------ ゲームを読む
    def area_of_quest(quest):
        value = ui.quest_value(quest, "neighboring_settlement_id", None)
        return str(value) if value is not None else None

    def is_incomplete(quest):
        """片付いていない依頼か。`config['status']` はゲーム自身の鍵（GAME.md §2.9）。"""
        config = ui.quest_value(quest, "config", None)
        if not isinstance(config, dict):
            return True
        return config.get("status") != "completed"

    def quests_of(app, area_id):
        """その土地の依頼 `[(id, quest, 難易度)]`。難易度が数でないものは外す。"""
        found = []
        for quest_id in ui.quest_ids(app):
            quest = ui.quest_of(app, quest_id)
            if quest is None or area_of_quest(quest) != str(area_id):
                continue
            difficulty = ui.quest_value(quest, "difficulty", None)
            if not isinstance(difficulty, int) or isinstance(difficulty, bool):
                continue
            found.append((str(quest_id), quest, difficulty))
        return found

    def in_scope(quest):
        return True if SCOPE == "all" else is_incomplete(quest)

    def day_of(app):
        value = getattr(getattr(app, "world", None), "days_elapsed", None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    # ------------------------------------------------------------ 寄せ直し
    def reconcile(app, area_id, why):
        """その土地の依頼の難易度を「素の値 + いまの上昇量」へ寄せる。

        戻り値は `(乗せた上昇量, 書き換えた依頼の数)`。
        何もしなかったときは `(いまの上昇量, 0)`。
        """
        if app is None or area_id is None:
            return 0, 0
        key = world_key(app)
        with lock:
            existed = isinstance(bucket_of(key).get(str(area_id)), dict)
            record = record_of(key, area_id)
            want = bonus_for(record["cleared"])
            entries = quests_of(app, area_id)
            if not entries:
                # 生成前の土地。控えだけ進めて次の機会に寄せる。
                if want != record["bonus"]:
                    write("{}: 土地 {} に依頼がまだ無い（上昇量 {} は次の機会に）"
                          .format(why, area_id, want))
                return record["bonus"], 0

            low, high = limits()
            base = record["base"]
            carried = record["bonus"]
            changed = 0
            hit_cap = False
            for quest_id, quest, difficulty in entries:
                if ROLLBACK:
                    # 戻すのは**自分が触ったもの**だけ。ここでは範囲を見ない。
                    # 上げた後に片付いた依頼は範囲の外へ出るので、
                    # 範囲で絞ると上げっぱなしのまま取り残される。
                    if quest_id not in base:
                        continue
                else:
                    if not in_scope(quest):
                        # 触らない依頼の素の値は控えない。
                        # 控えると、範囲の外で生まれた依頼にまで
                        # 「上昇量を引いた値」を素として与えることになる。
                        continue
                    if quest_id not in base:
                        # 初見の依頼の素の値。
                        # 生まれた時点で土地の上昇量を含んでいるので引く。
                        base[quest_id] = _clip(difficulty - carried, low, high)
                target = _clip(base[quest_id] + want, low, high)
                if target != base[quest_id] + want:
                    hit_cap = True
                if target == difficulty:
                    continue
                written = ui.set_quest_value(
                    app, quest_id, "difficulty", target,
                    on_error=lambda msg: write("WARN " + msg))
                if not written:
                    write("WARN {}: 依頼 {} の難易度を書けなかった".format(why, quest_id))
                    continue
                changed += 1
                write("{}: 土地 {} 依頼 {} 難易度 {} -> {}（素 {} + {}）"
                      .format(why, area_id, quest_id, difficulty, target,
                              base[quest_id], want))

            if ROLLBACK:
                # 戻し切ったら控えごと畳む（残しておくと、次に入れたとき
                # 「素の値」として古い控えが効いてしまう）。
                if existed:
                    write("{}: 土地 {} を素の難易度へ戻した（{}件）"
                          .format(why, area_id, changed))
                    save(key, area_id, None)
                capped.discard((key, str(area_id)))
                return 0, changed

            if not existed and not changed and not record["cleared"] and not want:
                # 素のままの土地。掲示板を開いただけで控えを作らない
                # （`state/` に「何も起きていない」行が増えるだけになる）。
                return 0, 0

            record["step"] = max(0, int(record["cleared"])) // per_step()
            record["bonus"] = want
            record["base"] = base
            day = day_of(app)
            if day is not None:
                record["day"] = day
            save(key, area_id, record)

            if hit_cap and (key, str(area_id)) not in capped:
                capped.add((key, str(area_id)))
                write("{}: 土地 {} は上限 {} に届いた（これ以上は上がらない）"
                      .format(why, area_id, high))
            return want, changed

    # ------------------------------------------------------------ 数える
    def count_clear(app, area_id):
        """クリアを1回数える。段が上がったなら True を返す。"""
        key = world_key(app)
        with lock:
            record = record_of(key, area_id)
            before = bonus_for(record["cleared"])
            record["cleared"] += 1
            record["step"] = record["cleared"] // per_step()
            after = bonus_for(record["cleared"])
            save(key, area_id, record)
            write("clear: 土地 {} は {} 回目（{}回ごとに +{}、いま +{}）"
                  .format(area_id, record["cleared"], per_step(), step_size(), after))
            return after > before

    # ------------------------------------------------------------ フック
    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """依頼を片付けた。その依頼の土地を1回ぶん進める。

        **どの依頼が終わるのかは `orig` の前に読む**
        （終わった後では `current_quest_data` が片付いている。`307_` と同じ）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        area_id = None
        try:
            quest = getattr(app, "current_quest_data", None) if app is not None else None
            if quest is not None:
                area_id = area_of_quest(quest)
        except Exception:
            ctx.log_exc("area difficulty: 終わる依頼を読めなかった")
        result = orig(self, *args, **kwargs)
        try:
            if app is not None and area_id is not None:
                raised = count_clear(app, area_id)
                reconcile(app, area_id, "clear")
                if raised and ANNOUNCE:
                    # 依頼の終了は報酬・才能・要約と出力が続く。
                    # その最中に差し込むと押し流されるので手が空くのを待つ
                    # （`303_` と同じ。既に確定した出来事なので待ちきれなくても出す）。
                    screen.when_idle(app, lambda: screen.say(app, ANNOUNCE),
                                     proceed_on_timeout=True,
                                     tag="area difficulty announce")
            elif app is not None:
                write("clear: 終わった依頼の土地が読めなかった（数えない）")
        except Exception:
            ctx.log_exc("area difficulty: クリアを数えられなかった")
        return result

    @ctx.wrap("__main__:DisplayQuestChoice.__init__", required=False, safe=True)
    def board(orig, self, app, *args, **kwargs):
        """掲示板を開いた。並べる前に、いま居る土地を寄せ直す。

        クリアの時点で既に寄せてあるので普段は何も起きない。
        効くのは設定を変えた直後と、`ROLLBACK` を入れた後。
        """
        try:
            area_id = ui.area_id_of(ui.current_area(app))
            if area_id:
                reconcile(app, area_id, "board")
        except Exception:
            ctx.log_exc("area difficulty: 掲示板で寄せ直せなかった")
        return orig(self, app, *args, **kwargs)

    ctx.log("area difficulty growth: {} clear(s) per +{}, up to +{} (limit {}), "
            "scope={}{}; log goes to out/{}".format(
                per_step(), step_size(), max_bonus(), int(DIFFICULTY_LIMIT),
                SCOPE, ", ROLLBACK" if ROLLBACK else "", LOG_BASENAME))


def _int(value, fallback):
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _clip(value, low, high):
    return max(low, min(high, int(value)))
