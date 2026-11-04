# -*- coding: utf-8 -*-
"""評判の素材がゲームのどこに在るか。**この MOD の方針は持たない**（TECH.md §3.1.1.1）。

ここに書いてあるのは読み方だけで、
「何件から評判が立つか」「手配がどれだけ重ければ悪名か」は入口（`reputation.py`）が決める。
分けているのは、素材の在り処はゲームの更新で動き、方針は遊びの好みで動くから。

素材はどれも**ゲーム自身が既に書いている**（GAME.md §2.20 / §2.9）。
この MOD が新しく記録を始めるものは1つも無い。

    player.area_history[エリアid]["achievements"]   その土地で成した事の文章
    player.area_history[エリアid]["lawfulness"]     手配度（平常 10、0 未満で犯罪者）
    player.area_history[エリアid]["residency"]      滞在日数の累計と最後に発った日
    world.quests / save_data_dict["quests"]         完了した依頼（`ui.quest_stores`）
    world.story_quests / save_data_dict["story_quests"]   果たした物語の依頼
    闘技場の施設の config（current_phase / enemy_data）    闘技場の勝ちと破った相手の格

`area_history` の読み方はローダの語彙（`ui.area_record` ほか）。
ここに写さないのは `309_` / `316_` と同じ表を3枚持つことになるため（TECH.md §3.2.3）。
"""

import hashlib
import json

from instantale_modloader import frames, ui

#: `area_history` の記録の中の鍵（GAME.md §2.20 の実セーブ）。
ACHIEVEMENTS_KEY = "achievements"
RESIDENCY_KEY = "residency"

#: `residency` の中の鍵。
TOTAL_DAYS_KEY = "total_days"
LAST_STAY_KEY = "last_stay_end"

#: 手配度の平常値（GAME.md §2.20。40エリア全てが 10 の実セーブで確認）。
#: **これは素のゲームの値であって閾値ではない**。
#: いくつから悪名とみなすかは入口の判断。
NORMAL_LAWFULNESS = 10

#: 完了した依頼の `config["status"]`（GAME.md §2.9。観測できている値は2つだけ）。
COMPLETED_STATUS = "completed"

#: 依頼がどの土地のものかを持っている鍵。**どちらで持つかを決めつけない**。
#: `neighboring_settlement_id` は受注条件に使われている側（GAME.md §2.9）。
QUEST_AREA_KEYS = ("neighboring_settlement_id", "quest_area_id")

#: 物語の依頼の台帳の名前（`world` の属性とセーブの鍵で同じ。GAME.md §2.9.1）。
STORY_QUESTS_KEY = "story_quests"

#: 闘技場の `facility_type` と、1勝ごとの `current_phase` の進み（GAME.md の闘技場の節）。
ARENA_TYPE = "colosseum"
ARENA_PHASE_STEP = 2

#: 編纂に載せる依頼の上限。
#: 設定にはしない（増やすほど頼み文が伸びるだけで、
#: 評判に効くのは「何件片付けたか」と直近の中身の方）。
QUEST_LIMIT = 20

#: 素材の文字列を控えるときの上限。
#: `achievements` は LLM が書いた文章なので、1件が長いことがある。
ITEM_CHARS = 400


def player_of(app):
    return getattr(app, "player", None)


def player_name(app):
    name = getattr(player_of(app), "name", None)
    return frames.short(name, 40) or "その者"


def game_day(app):
    """いまのゲーム内日数。読めなければ `None`（GAME.md §2.16）。"""
    value = getattr(getattr(app, "world", None), "days_elapsed", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def current_area_id(app):
    return ui.area_id_of(ui.current_area(app))


def area_name(app, area_id):
    """エリアの名前。引けなければ空文字（呼び側が「この土地」に倒す）。"""
    area = ui.world_areas(app).get(str(area_id))
    return frames.short(getattr(area, "name", ""), 40) or ""


def achievements_of(record):
    """その土地で成した事の文章。読めなければ空のリスト。

    中身が文字列以外で入っていても落とさずに文字列へ均す。
    ゲームが書いている側なので形を決めつけない（GAME.md §2.7）。
    """
    if not isinstance(record, dict):
        return []
    value = record.get(ACHIEVEMENTS_KEY)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    items = []
    for item in value:
        text = item if isinstance(item, str) else frames.repr_value(item)
        text = frames.short(text.strip(), ITEM_CHARS)
        if text:
            items.append(text)
    return items


def residency_of(record):
    """滞在の記録 `{total_days, last_stay_end}`。読めた項目だけを入れる。"""
    if not isinstance(record, dict):
        return {}
    value = record.get(RESIDENCY_KEY)
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in (TOTAL_DAYS_KEY, LAST_STAY_KEY):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            continue
        out[key] = int(number)
    return out


def completed_quests(app, area_id):
    """その土地で完了した依頼の題名。新しい順（id の数の大きい順）。

    片付いた依頼はセーブから消えず `world.quests` に残る（GAME.md §2.9）ので、
    ここは「これまでに片付けた分」の一覧になる。
    2つの格納先の合併を見るのは、どちらに登録されるかを決めつけないため。
    """
    wanted = str(area_id)
    found = []
    for quest_id in sorted(ui.quest_ids(app), key=ui.id_sort_key, reverse=True):
        quest = ui.quest_of(app, quest_id)
        if quest is None:
            continue
        config = ui.quest_value(quest, "config")
        status = config.get("status") if isinstance(config, dict) else None
        if status != COMPLETED_STATUS:
            continue
        if not any(str(ui.quest_value(quest, key, "")) == wanted
                   for key in QUEST_AREA_KEYS):
            continue
        title = ui.quest_value(quest, "quest_title", "")
        title = frames.short(title.strip(), ITEM_CHARS) if isinstance(title, str) else ""
        if title:
            found.append(title)
        if len(found) >= QUEST_LIMIT:
            break
    return found


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def quest_difficulty(quest):
    """依頼の難易度（GAME.md の初期依頼の表。1〜76、`923_` 併用ならその上も）。読めなければ `None`。"""
    return _number(ui.quest_value(quest, "difficulty"))


def is_completed(quest):
    config = ui.quest_value(quest, "config")
    return isinstance(config, dict) and config.get("status") == COMPLETED_STATUS


def story_quests(app):
    """物語の依頼。`world.quests` とは別の台帳（GAME.md §2.9.1。id は 0〜4 で重なる）。

    生きた世界に無ければセーブの側を読む。どちらも無ければ空のリスト。
    """
    world = getattr(app, "world", None)
    data = getattr(app, "save_data_dict", None)
    for store in (frames.attr(world, STORY_QUESTS_KEY, None),
                  data.get(STORY_QUESTS_KEY) if isinstance(data, dict) else None):
        if isinstance(store, dict) and store:
            return list(store.values())
        if isinstance(store, (list, tuple)) and store:
            return list(store)
    return []


def _field(holder, name):
    if isinstance(holder, dict):
        return holder.get(name)
    return frames.attr(holder, name, None)


def arenas(app):
    """土地ごとの闘技場の戦績 `{エリアid: {"name", "wins", "ranks"}}`（GAME.md の闘技場の節）。

    勝つと `current_phase` が +2 され、試合ごとの相手の格は `enemy_data.<段>.data.rank` に焼かれる。
    勝った試合は段 0, 2, … の `wins` 個。逃げると段は進まないので数えない。
    MOD が建てた闘技場（`331_`）も生きた世界の施設に居るので同じ読み方で拾う。
    """
    found = {}
    for area_id, area in ui.world_areas(app).items():
        for node in ui.nodes_of(area):
            for facility in ui.facilities_of(node).values():
                if ui.facility_type_of(facility) != ARENA_TYPE:
                    continue
                config = _field(facility, "config")
                if not isinstance(config, dict):
                    continue
                phase = _number(config.get("current_phase")) or 0
                wins = max(0, phase // ARENA_PHASE_STEP)
                if not wins:
                    continue
                enemies = config.get("enemy_data")
                ranks = []
                for index in range(wins):
                    entry = (enemies.get(str(index * ARENA_PHASE_STEP))
                             if isinstance(enemies, dict) else None)
                    data = entry.get("data") if isinstance(entry, dict) else None
                    rank = _number(data.get("rank") if isinstance(data, dict) else
                                   (entry.get("rank") if isinstance(entry, dict) else None))
                    if rank is not None:
                        ranks.append(rank)
                entry = found.setdefault(str(area_id), {"name": "", "wins": 0, "ranks": []})
                entry["wins"] += wins
                entry["ranks"].extend(ranks)
                entry["name"] = entry["name"] or frames.short(_field(facility, "name") or "", 40)
    return found


def quest_town(quest):
    """依頼を1つの土地に帰す。`QUEST_AREA_KEYS` の先に読めた方。読めなければ空文字。

    件数（`deeds`）は両方の鍵で数えるが、難易度の合計は1件を1度だけ足す
    （2つの土地に足すと、同じ依頼が名声に2回効く）。
    """
    for key in QUEST_AREA_KEYS:
        area_id = ui.quest_value(quest, key, "")
        if area_id not in (None, ""):
            return str(area_id)
    return ""


def _blank_stats():
    return {"deeds": 0, "wanted": False, "lawfulness": None, "achievements": 0,
            "done": 0, "done_difficulties": [], "difficulties": [],
            "story_difficulties": [], "arena_ranks": []}


def survey(app):
    """全土地の立ち位置を1回の走査で採る。読めなければ空の辞書。

    返すのは `{エリアid(str): {...}}` で、中身は数だけ。
      deeds              成した事＋完了した依頼＋果たした物語の依頼＋闘技場の勝ち
                         （`gather` の数え方と同じ。依頼は両方の鍵で数える）
      wanted             ゲーム自身の線（`lawfulness < 0`）
      lawfulness         手配度そのもの。読めなければ None
      achievements       成した事の件数
      done               その土地に帰した完了した依頼の件数（`quest_town`。1件を1度だけ）
      done_difficulties  その完了した依頼の難易度
      difficulties       その土地に帰した依頼の難易度（片付いていないものも）。土地の格を見るため
      story_difficulties その土地で果たした物語の依頼の難易度
      arena_ranks        その土地の闘技場で破った相手の格
    **いくつから評判が立つか・名声をどう数えるかはここでは決めない**（方針は入口の仕事）。

    `gather` を土地の数だけ呼ばない。
    あちらは依頼の走査を土地ごとにやり直すので、
    40エリアの世界では同じ台帳を40回なめることになる。
    こちらは依頼を1回だけなめて土地別に数える。
    """
    player = player_of(app)
    history = ui.area_history_of(player)
    if history is None:
        return {}
    stats = {}
    for area_id, record in history.items():
        value = ui.lawfulness_of(record)
        entry = stats[str(area_id)] = _blank_stats()
        entry["achievements"] = len(achievements_of(record))
        entry["deeds"] = entry["achievements"]
        entry["wanted"] = isinstance(value, int) and value < 0
        entry["lawfulness"] = value if isinstance(value, int) else None
    for quest_id in ui.quest_ids(app):
        quest = ui.quest_of(app, quest_id)
        if quest is None:
            continue
        difficulty = quest_difficulty(quest)
        town = quest_town(quest)
        if town and difficulty is not None:
            stats.setdefault(town, _blank_stats())["difficulties"].append(difficulty)
        config = ui.quest_value(quest, "config")
        status = config.get("status") if isinstance(config, dict) else None
        if status != COMPLETED_STATUS:
            continue
        if town:
            entry = stats.setdefault(town, _blank_stats())
            entry["done"] += 1
            if difficulty is not None:
                entry["done_difficulties"].append(difficulty)
        # 1件の依頼が両方の鍵で別の土地を指していたら両方に数える
        # （`completed_quests` がどちらの鍵でも拾うのと同じ読み方）。
        seen = set()
        for key in QUEST_AREA_KEYS:
            area_id = ui.quest_value(quest, key, "")
            area_id = str(area_id) if area_id not in (None, "") else ""
            if not area_id or area_id in seen:
                continue
            seen.add(area_id)
            stats.setdefault(area_id, _blank_stats())["deeds"] += 1
    for quest in story_quests(app):
        town = quest_town(quest)
        if not town or not is_completed(quest):
            continue
        entry = stats.setdefault(town, _blank_stats())
        entry["deeds"] += 1
        difficulty = quest_difficulty(quest)
        if difficulty is not None:
            entry["story_difficulties"].append(difficulty)
    for area_id, arena in arenas(app).items():
        entry = stats.setdefault(area_id, _blank_stats())
        entry["deeds"] += arena["wins"]
        entry["arena_ranks"].extend(arena["ranks"])
    return stats


def gather(app, area_id):
    """その土地の素材を1つの辞書にまとめる。一度も訪れていない土地なら `None`。

    `area_record` が `None` を返す（＝ `area_history` にその土地が無い）のは
    「まだ何も無い」であって異常ではない。呼び側は黙って降りる。
    """
    player = player_of(app)
    if player is None or not area_id:
        return None
    record = ui.area_record(player, area_id)
    if record is None:
        return None
    arena = arenas(app).get(str(area_id)) or {}
    return {
        "area_id": str(area_id),
        "area_name": area_name(app, area_id),
        "achievements": achievements_of(record),
        "lawfulness": ui.lawfulness_of(record),
        "residency": residency_of(record),
        "quests": completed_quests(app, area_id),
        "story": completed_story(app, area_id),
        "arena": {"name": arena.get("name") or "", "wins": arena.get("wins") or 0,
                  "top_rank": max(arena.get("ranks") or [0])},
    }


def completed_story(app, area_id):
    """その土地で果たした物語の依頼の題名。"""
    found = []
    for quest in story_quests(app):
        if quest_town(quest) != str(area_id) or not is_completed(quest):
            continue
        title = ui.quest_value(quest, "quest_title", "")
        title = frames.short(title.strip(), ITEM_CHARS) if isinstance(title, str) else ""
        if title:
            found.append(title)
    return found


def fingerprint(item):
    """素材が変わったかを見る印。**同じ素材からは必ず同じ値**。

    `hash()` を使わないのは、プロセスごとに変わるので次に起動したとき
    同じ素材を「変わった」と読んでしまうため（`state.world_filename` と同じ理由）。

    印に入れるのは `achievements` / `lawfulness` / 完了した依頼の3つだけで、
    **滞在日数と土地の名前は入れない**。
    滞在日数はその土地に居るだけで毎日増えるので、入れると評判を毎日編纂し直すことになる。
    評判が変わるのは行いが増えたときであって、日が経ったときではない。
    """
    if not isinstance(item, dict):
        return ""
    core = {
        "achievements": item.get("achievements") or [],
        "lawfulness": item.get("lawfulness"),
        "quests": item.get("quests") or [],
    }
    # 版6で足した素材。空のときは鍵ごと入れない（版5までの印と同じ値を保ち、入れ替えた直後に編み直さない）。
    if item.get("story"):
        core["story"] = item["story"]
    wins = (item.get("arena") or {}).get("wins")
    if wins:
        core["arena_wins"] = wins
    text = json.dumps(core, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def describe_shape(app, area_id):
    """`area_history` が実行時にどう見えているかの1行（注入の確認用）。

    VERIFICATION.md §3.36 で「実行時に読めるか未確認」としていた点をログで押さえるための関数。
    値そのものではなく**形**を出す（`achievements` の本文は長く、ログを埋める）。
    """
    player = player_of(app)
    history = ui.area_history_of(player)
    if history is None:
        return "area_history: {} には無い（{}）".format(
            type(player).__name__, ", ".join(sorted(vars(player))[:12])
            if getattr(player, "__dict__", None) is not None else "vars() が引けない")
    record = ui.area_record(player, area_id)
    return ("area_history: {}件のエリア / いま {!r} / 記録は {} / "
            "achievements {}件 / lawfulness {!r} / residency {!r}".format(
                len(history), area_id,
                "無い" if record is None else type(record).__name__,
                len(achievements_of(record)), ui.lawfulness_of(record),
                residency_of(record)))
