# -*- coding: utf-8 -*-
"""依頼を片付けるたび、その土地の依頼の難易度が上がっていく。

素のゲームでは、土地の難易度は世界を作った時点で決まったまま動かない。
最初の町の依頼は 3,4,5 のまま何十回通っても 3,4,5 で、
店に並ぶ品も、鍛冶で打てるものも、そこで止まる。
土地を移れば強くなるが、**同じ土地に居続けても何も育たない**。

この MOD が動かすのは1つの数だけ:

    world.quests[id].difficulty          その依頼の難易度（0..76）

ゲーム自身の項目なので、セーブに MOD 独自の鍵は増えない（TECH.md §6.4）。

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

4つとも実機で確かめてある（VERIFICATION_LOG.md §2.67）。
クラフトは `成果物の値段 = 素材の合計値段 × calculate_modification が返す倍率` で、
素材が高いほど成果物が良くなる（GAME.md §2.14.2）。

## 上げ方

**依頼を1つ片付けるたびに、STEP_MIN〜STEP_MAX の乱数を引いて積む:**

    クリア1回ごと   上昇量 += randint(STEP_MIN, STEP_MAX)   （上限 MAX_BONUS）
    難易度          = min(素の難易度 + 上昇量, 上限)

乱数なので**上昇量はクリア回数から計算し直せない**。
だから上昇量そのものを控えに持つ（`bonus`）。
ここが固定値だった頃との唯一の構造の違いで、
控えを失うとその土地の育ちが戻るのはこのため。

**依頼へ書くときは差分を足さず、毎回「素の値 + いまの上昇量」を書く。**
何度走っても同じ値に落ち着き、途中で設定を変えても
「そのとき正しい高さ」へ寄る（依頼の側へ差分を足す形だと、
取りこぼしと二重掛けがどちらも黙って積み上がる）。

素の値の控え（`base`）は、その依頼を初めて触ったときの難易度そのもの。
**新しく生まれた依頼も素の帯で生まれる**ので、引き算は要らない
（VERIFICATION_LOG.md §2.66。土地を +20 まで上げた状態で作らせた依頼が
4 と 3 で生まれた ＝ ゲームは今の難易度を見ていない）。

## 上げる場面

| 場面 | すること |
| --- | --- |
| `World.__init__` | セーブを読み込んだ直後。**世界じゅうの土地をまとめて寄せ直す** |
| `DisplayQuestChoice.generate_random_quest` | 生まれた依頼をその場で上げる。頼み文へ渡す難易度も上げる |
| `QuestChoiceManager.__init__` | 受注の直前 |
| `DisplayQuestChoice.__init__` | 掲示板を開いた土地（設定を変えた直後もここで揃う） |
| `QuestEndManager.execute` | その依頼の土地のクリア回数を +1 して、寄せ直す |

ロードの1回で世界ぜんぶを寄せるのが要。
掲示板を開いた土地だけ直していると、
**掲示板を通らない読み手が素の値を見る**。
筆頭が店の品揃えで、`get_quest_difficulties(area, world)` は生きた一覧を読んでいる
（雛形に無い依頼まで数に入っていた。§2.66）。
一括寄せは**走査1回・控えの書き込み1回**で済ませる。

依頼がどの土地のものかは `neighboring_settlement_id`
（掲示板の絞り込みにゲーム自身が使っている鍵。GAME.md §2.9）。
いま居る土地ではなく**依頼の土地**を数えるので、
道中で受けた依頼を別の町で終わらせても出所に付く。

## 書く先は生きた一覧だけ（`app.world.quests`）

`app.world_dict['quests']` には**書かない**。
あれはセーブの中身ではなく世界の雛形で、
書くと `worlds/<世界名>/world_data.json` に焼かれ、
**その世界で新しく始めた別のキャラクタにまで難易度が乗る**
（実測。雛形は 12件、セーブは 22件だった。GAME.md §2.9.1 / §2.66）。

生きた一覧はセーブに残らないので、ロードすると難易度は素へ戻る。
**それでよい。** 戻ったぶんはロードの1回で書き直すので、
遊んでいる側からは連続して見える。
そのうえで **MOD を外せばセーブは素のまま**になり、後始末が要らない。
`ROLLBACK` はいま動いているゲームを戻すための逃げ道で、
セーブを片付けるためのものではない。

続きを持っているのは `state/area_difficulty/<世界名>.json` のほう
（クリア回数と素の難易度）。ここが消えると進みが戻る ―
`state/` の約束どおり（TECH.md §3.11）。
"""

import random
import sys
import threading
import time

from instantale_modloader import ui
from instantale_modloader.state import (UNKNOWN_WORLD, world_filename,
                                        world_key, world_key_of_dict)

LOG_BASENAME = "area_difficulty.log"
STATE_DIRNAME = "area_difficulty"

#: 生成へ渡す上昇量の印の寿命（秒）。`301_` / `307_` と同じ形。
#: 生成が始まらなかった回の印を、次の生成が拾わないための時限。
INJECT_TTL = 300.0

#: ゲーム自身の難易度の上限（`scripts.functions.QUEST_DIFFICULTY_VALUE_MAX`）。
#: 引けなかったときの落とし先で、引ければそちらが優先。
GAME_DIFFICULTY_MAX = 76
GAME_DIFFICULTY_MIN = 0

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
STEP_MIN = 3
STEP_MAX = 10
MAX_BONUS = 60
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
    state = {
        "inject": None,        # 生成へ渡す上昇量（1回で使い切る）
        "inject_at": 0.0,
        "warned_store": False,
    }

    # ------------------------------------------------------------ 設定の読み
    def step_range():
        """1回のクリアで引く幅。上下が逆に設定されても壊さない。"""
        low = max(0, int(STEP_MIN))
        return low, max(low, int(STEP_MAX))

    def draw_step():
        """1回ぶんの上昇量を引く。幅が無ければ乱数を回さない。"""
        low, high = step_range()
        return low if low == high else random.randint(low, high)

    def max_bonus():
        return max(0, int(MAX_BONUS))

    def want_of(record):
        """その土地にいま乗せる上昇量。`ROLLBACK` のときは常に 0。

        **積んだ値を読むだけで、計算し直さない**（乱数なので再現できない）。
        上限だけはここで当てるので、`MAX_BONUS` を下げれば次の寄せ直しで下がる。
        """
        if ROLLBACK:
            return 0
        return max(0, min(max_bonus(), record["bonus"]))

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
        for name in ("cleared", "bonus", "day"):
            if record.get(name) is not None:
                out[name] = record[name]
        base = record.get("base")
        if base:
            out["base"] = {qid: base[qid] for qid in sorted(base, key=ui.id_sort_key)}
        return out

    def save(key, area_id, record, flush=True):
        """1つの土地の記録を控えへ入れる。空の記録は行ごと落とす。

        `flush=False` にすると控えを直すだけでファイルには書かない。
        ロード直後は土地の数だけ寄せ直すので、
        そこで毎回書くと**1回のロードで同じファイルを土地の数だけ書く**ことになる。
        """
        bucket = bucket_of(key)
        if record is None:
            bucket.pop(str(area_id), None)
        else:
            bucket[str(area_id)] = ordered(record)
        cache["buckets"][key] = {aid: bucket[aid]
                                 for aid in sorted(bucket, key=ui.id_sort_key)}
        return flush_bucket(key) if flush else True

    def flush_bucket(key):
        """控えをファイルへ。錠の中で呼ぶ。"""
        if not ctx.write_json(path_for(key), bucket_of(key)):
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

    def live_store(world):
        """難易度を読み書きする先 ＝ **生きた一覧だけ**（`world.quests`）。

        `ui.quest_stores` は雛形（`app.world_dict['quests']`）も返すが、
        そちらへ書くと世界のファイルに焼かれて、
        同じ世界で新しく始めた別のキャラクタにまで乗る（GAME.md §2.9.1）。
        生きた一覧が引けないビルドでは、雛形しか無いので諦めて一度だけ知らせる。

        `app` ではなく `world` を受けるのは、`World.__init__` を包む場面では
        `app.world` がまだ埋まっていないため（自分自身が代入される前）。
        """
        quests = getattr(world, "quests", None)
        if isinstance(quests, dict):
            return quests
        if not state["warned_store"]:
            state["warned_store"] = True
            write("WARN world.quests が引けない。この版では何もしない")
        return None

    def quests_by_area(world):
        """`{土地id: [(依頼id, quest, 難易度)]}`。難易度が数でないものは外す。

        **走査は1回**。土地ごとに呼び直すと、土地の数だけ全依頼を読み直すことになる。
        """
        grouped = {}
        store = live_store(world)
        for quest_id in list(store or {}):
            quest = store[quest_id]
            if quest is None:
                continue
            area_id = area_of_quest(quest)
            if area_id is None:
                continue
            difficulty = ui.quest_value(quest, "difficulty", None)
            if not isinstance(difficulty, int) or isinstance(difficulty, bool):
                continue
            grouped.setdefault(area_id, []).append((str(quest_id), quest, difficulty))
        return grouped

    def quests_of(world, area_id):
        """その土地の依頼だけ。1つの土地しか要らない場面用。"""
        return quests_by_area(world).get(str(area_id), [])

    def set_difficulty(quest, value):
        """生きた一覧の1件へ書く。書けたら True。"""
        try:
            if isinstance(quest, dict):
                quest["difficulty"] = value
            else:
                setattr(quest, "difficulty", value)
            return True
        except Exception:
            ctx.log_exc("area difficulty: 難易度を書けなかった")
            return False

    def in_scope(quest):
        return True if SCOPE == "all" else is_incomplete(quest)

    def day_of(world):
        value = getattr(world, "days_elapsed", None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    # ------------------------------------------------------------ 寄せ直し
    def reconcile(world, key, area_id, why, entries=None, flush=True):
        """その土地の依頼の難易度を「素の値 + いまの上昇量」へ寄せる。

        戻り値は `(乗せた上昇量, 書き換えた依頼の数)`。
        何もしなかったときは `(いまの上昇量, 0)`。
        `entries` は束ねた依頼の使い回し、`flush` は控えの書き出しの先送り。
        どちらもロード直後の一括寄せのため（走査1回・書き込み1回）。
        """
        if world is None or key is None or area_id is None:
            return 0, 0
        with lock:
            existed = isinstance(bucket_of(key).get(str(area_id)), dict)
            record = record_of(key, area_id)
            want = want_of(record)
            if entries is None:
                entries = quests_of(world, area_id)
            if not entries:
                # 生成前の土地。控えだけ進めて次の機会に寄せる。
                if want:
                    write("{}: 土地 {} に依頼がまだ無い（上昇量 {} は次の機会に）"
                          .format(why, area_id, want))
                return want, 0

            low, high = limits()
            base = record["base"]
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
                        # 控えると、範囲の外の依頼にまで素の値を与えることになる。
                        continue
                    if quest_id not in base:
                        # 初見の依頼の素の値。**そのまま控える。**
                        # 新しく生まれた依頼も素の帯で来る（§2.66）ので、
                        # ここで上昇量を引くと素の値が沈む。
                        base[quest_id] = _clip(difficulty, low, high)
                target = _clip(base[quest_id] + want, low, high)
                if target != base[quest_id] + want:
                    hit_cap = True
                if target == difficulty:
                    continue
                if not set_difficulty(quest, target):
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
                    save(key, area_id, None, flush=flush)
                capped.discard((key, str(area_id)))
                return 0, changed

            if not existed and not changed and not record["cleared"] and not want:
                # 素のままの土地。掲示板を開いただけで控えを作らない
                # （`state/` に「何も起きていない」行が増えるだけになる）。
                return 0, 0

            record["base"] = base
            day = day_of(world)
            if day is not None:
                record["day"] = day
            save(key, area_id, record, flush=flush)

            if hit_cap and (key, str(area_id)) not in capped:
                capped.add((key, str(area_id)))
                write("{}: 土地 {} は上限 {} に届いた（これ以上は上がらない）"
                      .format(why, area_id, high))
            return want, changed

    def reconcile_all(world, key, why):
        """世界の土地を全部寄せ直す。ロードした直後の1回に使う。

        依頼の難易度はセーブに残らないので、ロードすると素へ戻る。
        掲示板を開いた土地だけ直していると、
        **掲示板を通らない読み手が素の値を見る**（店の品揃えがその筆頭。
        `get_quest_difficulties(area, world)` は生きた一覧を読んでいる ―
        雛形に無い依頼まで数に入っていた。VERIFICATION_LOG.md §2.66）。

        走査は1回、控えの書き込みも1回。
        """
        grouped = quests_by_area(world)
        touched = 0
        for area_id in sorted(grouped, key=ui.id_sort_key):
            touched += reconcile(world, key, area_id, why,
                                 entries=grouped[area_id], flush=False)[1]
        with lock:
            flush_bucket(key)
        return touched

    def bonus_of(key, area_id):
        """その土地にいま乗せる上昇量。控えは書かない（読むだけ）。"""
        with lock:
            return want_of(record_of(key, area_id))

    # ------------------------------------------------------------ 数える
    def count_clear(key, area_id):
        """クリアを1回数え、乱数を引いて積む。上がったなら True を返す。

        `ROLLBACK` のあいだは数えない
        （戻している最中に積むと、戻し終わらない）。
        """
        if ROLLBACK:
            return False
        with lock:
            record = record_of(key, area_id)
            before = want_of(record)
            drawn = draw_step()
            record["cleared"] += 1
            record["bonus"] = min(max_bonus(), max(0, record["bonus"]) + drawn)
            after = want_of(record)
            save(key, area_id, record)
            low, high = step_range()
            write("clear: 土地 {} は {} 回目（{}〜{} から +{} を引いて、いま +{}）"
                  .format(area_id, record["cleared"], low, high, drawn, after))
            return after > before

    # ------------------------------------------------------------ フック
    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_loaded(orig, self, save_data_dict, app, *args, **kwargs):
        """セーブを読み込んだ。**その場で世界じゅうの土地を寄せ直す。**

        難易度はセーブに残らないので、ここが素へ戻った状態の始点になる。
        掲示板を開いた土地だけ直していると、
        掲示板を通らない読み手（店の品揃えが筆頭）が素の値を見る。

        世界の鍵はセーブの辞書から取る。
        この時点では `app.world` がまだ埋まっていない（いま作っている最中）。
        """
        result = orig(self, save_data_dict, app, *args, **kwargs)
        try:
            key = world_key_of_dict(save_data_dict, None) or world_key(app)
            if key and key != UNKNOWN_WORLD:
                touched = reconcile_all(self, key, "load")
                write("load: 世界 {!r} を寄せ直した（{}件）".format(key, touched))
        except Exception:
            ctx.log_exc("area difficulty: ロード直後に寄せ直せなかった")
        return result

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
                key = world_key(app)
                raised = count_clear(key, area_id)
                reconcile(getattr(app, "world", None), key, area_id, "clear")
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

        普段はロードの時点で寄せてあるので何も起きない。
        効くのは設定を変えた直後と、`ROLLBACK` を入れた後。
        """
        try:
            area_id = ui.area_id_of(ui.current_area(app))
            if area_id:
                reconcile(getattr(app, "world", None), world_key(app),
                          area_id, "board")
        except Exception:
            ctx.log_exc("area difficulty: 掲示板で寄せ直せなかった")
        return orig(self, app, *args, **kwargs)

    @ctx.wrap("__main__:DisplayQuestChoice.generate_random_quest",
              required=False, safe=True)
    def generate_random_quest(orig, self, *args, **kwargs):
        """依頼を作らせた。**生まれたその場で上げる。**

        新しい依頼は素の帯で生まれてくる（§2.66）ので、
        ここで上げないと、いま作らせた依頼だけが素の難易度のまま差し出される。
        頼み文へ渡す難易度も上げておく（下の `random_quest_generator`）ので、
        文章と数が食い違わない。
        """
        app = getattr(self, "app", None) or ui.find_app()
        before = set()
        try:
            before = set(live_store(getattr(app, "world", None)) or {})
            area_id = ui.area_id_of(ui.current_area(app))
            bonus = bonus_of(world_key(app), area_id) if area_id else 0
            if bonus:
                state["inject"] = bonus
                state["inject_at"] = time.monotonic()
        except Exception:
            ctx.log_exc("area difficulty: 生成の前に土地を読めなかった")
        result = orig(self, *args, **kwargs)
        state["inject"] = None
        try:
            world = getattr(app, "world", None)
            key = world_key(app)
            store = live_store(world) or {}
            born = [qid for qid in list(store) if qid not in before]
            areas = []
            for quest_id in born:
                area = area_of_quest(store[quest_id])
                if area is not None and area not in areas:
                    areas.append(area)
            if not born:
                write("search: 新しい依頼が見つからない（何も上げない）")
            for area in areas:
                reconcile(world, key, area, "search")
        except Exception:
            ctx.log_exc("area difficulty: 生まれた依頼を上げられなかった")
        return result

    @ctx.wrap("__main__:QuestChoiceManager.__init__", required=False, safe=True)
    def quest_choice(orig, self, app, quest_type, quest_id, *args, **kwargs):
        """受注の直前。その依頼の土地を寄せ直してから受けさせる。

        掲示板を通らずに受ける経路（`301_` の会話からの受注、
        `307_` の道中）もここを通る。
        """
        try:
            world = getattr(app, "world", None)
            store = live_store(world) or {}
            quest = store.get(str(quest_id))
            area_id = area_of_quest(quest) if quest is not None else None
            if area_id is not None:
                reconcile(world, world_key(app), area_id, "accept")
        except Exception:
            ctx.log_exc("area difficulty: 受注の前に寄せ直せなかった")
        return orig(self, app, quest_type, quest_id, *args, **kwargs)

    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False, safe=True)
    def random_quest_generator(orig, world_overview, settlement_name,
                               settlement_overview, settlement_structure_description,
                               area_description, quest_difficulty, *args, **kwargs):
        """頼み文へ渡す難易度に、その土地の上昇量を乗せる。

        出力スキーマにも呼び出し側にも影響しない（数を1つ差し替えるだけ）。
        印は1回で使い切るので、上昇量ゼロの土地の生成は素通しする。
        `307_` は自分の難易度を持っているので、
        こちらが乗せた値はあちらが上書きする（あちらが内側）。
        """
        bonus = state["inject"]
        state["inject"] = None
        raised = quest_difficulty
        if (bonus and isinstance(quest_difficulty, int)
                and not isinstance(quest_difficulty, bool)
                and time.monotonic() - state["inject_at"] <= INJECT_TTL):
            low, high = limits()
            raised = _clip(quest_difficulty + bonus, low, high)
            if raised != quest_difficulty:
                write("search: 頼み文へ渡す難易度 {} -> {}".format(
                    quest_difficulty, raised))
        return orig(world_overview, settlement_name, settlement_overview,
                    settlement_structure_description, area_description,
                    raised, *args, **kwargs)

    ctx.log("area difficulty growth: +{}..{} per clear, up to +{} (limit {}), "
            "scope={}{}; log goes to out/{}".format(
                step_range()[0], step_range()[1], max_bonus(),
                int(DIFFICULTY_LIMIT), SCOPE,
                ", ROLLBACK" if ROLLBACK else "", LOG_BASENAME))


def _int(value, fallback):
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _clip(value, low, high):
    return max(low, min(high, int(value)))
