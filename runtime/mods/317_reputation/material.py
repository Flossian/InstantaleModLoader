# -*- coding: utf-8 -*-
"""評判の素材がゲームのどこに在るか。**この MOD の方針は持たない**（TECH.md §3.1.1.1）。

ここに書いてあるのは読み方だけで、
「何件から評判が立つか」「手配がどれだけ重ければ悪名か」は入口（`reputation.py`）が決める。
分けているのは、素材の在り処はゲームの更新で動き、方針は遊びの好みで動くから。

素材は4つとも**ゲーム自身が既に書いている**（GAME.md §2.20 / §2.9）。
この MOD が新しく記録を始めるものは1つも無い。

    player.area_history[エリアid]["achievements"]   その土地で成した事の文章
    player.area_history[エリアid]["lawfulness"]     手配度（平常 10、0 未満で犯罪者）
    player.area_history[エリアid]["residency"]      滞在日数の累計と最後に発った日
    world.quests / world_dict["quests"]             完了した依頼

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


def survey(app):
    """全土地の質的な立ち位置を1回の走査で採る。読めなければ空の辞書。

    返すのは `{エリアid(str): {"deeds": 件数, "wanted": bool}}`。
    件数は成した事＋完了した依頼（`gather` の数え方と同じ）で、
    wanted はゲーム自身の線（`lawfulness < 0`）。
    **いくつから評判が立つかはここでは決めない**（方針は入口の仕事）。

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
        stats[str(area_id)] = {
            "deeds": len(achievements_of(record)),
            "wanted": isinstance(value, int) and value < 0,
        }
    for quest_id in ui.quest_ids(app):
        quest = ui.quest_of(app, quest_id)
        if quest is None:
            continue
        config = ui.quest_value(quest, "config")
        status = config.get("status") if isinstance(config, dict) else None
        if status != COMPLETED_STATUS:
            continue
        # 1件の依頼が両方の鍵で別の土地を指していたら両方に数える
        # （`completed_quests` がどちらの鍵でも拾うのと同じ読み方）。
        seen = set()
        for key in QUEST_AREA_KEYS:
            area_id = ui.quest_value(quest, key, "")
            area_id = str(area_id) if area_id not in (None, "") else ""
            if not area_id or area_id in seen:
                continue
            seen.add(area_id)
            entry = stats.setdefault(area_id, {"deeds": 0, "wanted": False})
            entry["deeds"] += 1
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
    return {
        "area_id": str(area_id),
        "area_name": area_name(app, area_id),
        "achievements": achievements_of(record),
        "lawfulness": ui.lawfulness_of(record),
        "residency": residency_of(record),
        "quests": completed_quests(app, area_id),
    }


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
            if hasattr(player, "__dict__") else "vars() が引けない")
    record = ui.area_record(player, area_id)
    return ("area_history: {}件のエリア / いま {!r} / 記録は {} / "
            "achievements {}件 / lawfulness {!r} / residency {!r}".format(
                len(history), area_id,
                "無い" if record is None else type(record).__name__,
                len(achievements_of(record)), ui.lawfulness_of(record),
                residency_of(record)))
