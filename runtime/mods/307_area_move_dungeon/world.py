# -*- coding: utf-8 -*-
"""ゲームのデータを読む部品。**この MOD の方針は持たない。**

ここに置いてあるのは「Instantale がどこに何を持っているか」だけで、
どう使うか（難易度の決め方・断る条件・文言）は入口の
`area_move_dungeon.py` にある。設定（`mod.json` から変えられる値）も
入口の定数なので、ここでは読まない。必要な値は引数で受け取る。

根拠は全て実測。詳細は GAME.md（§2.7 世界のデータ / §2.9 クエスト /
§2.18 エリア移動 / §2.19 体力）。
"""

import sys

from instantale_modloader import ui
# 世界の見分け方はローダの語彙。ここで再輸出して `world.world_key(app)` を保つ。
from instantale_modloader.state import world_key


def short(value, limit=60):
    """ログや画面に出す用に切り詰めた文字列。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def id_sort(value):
    """id の並べ替え。数字なら数として、そうでなければ文字列として。"""
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def current_quest_id(app):
    """ゲームがいま進めているクエストの id。無ければ None。

    **これがゲーム自身の答え。** `QuestStartManager` を捕まえられなくても
    （注入し直しをまたいだ場合など）、これを見れば道中のクエストの最中か
    どうかが分かる。`app.current_quest_data` はクエスト中だけ `Quest` が
    入り、それ以外は None（`206_` の記録で確認済み）。
    """
    quest = getattr(app, "current_quest_data", None) if app is not None else None
    if quest is None:
        return None
    value = quest.get("id") if isinstance(quest, dict) else getattr(quest, "id", None)
    return str(value) if value is not None else None


# --------------------------------------------------------------- エリア
def area_of(app, area_id):
    return ui.world_areas(app).get(str(area_id))


def area_name(area, fallback="", limit=40):
    name = getattr(area, "name", None)
    return short(name, limit) if isinstance(name, str) and name else fallback


def area_level(app, area, write, label, minimum=1):
    """その土地の難易度。**ゲーム自身のヘルパに聞く。**

    引けなかったときは依頼の難易度の平均へ、それも無ければ `minimum` へ落ちる。
    落ちたことは必ず残す（黙って 1 になると「妙に楽な道」になるだけで
    原因が分からない）。
    """
    world = getattr(app, "world", None)
    functions = sys.modules.get("scripts.functions")
    average = getattr(functions, "get_area_average_difficulty", None) \
        if functions else None
    if average is not None and area is not None:
        try:
            value = average(area, world)
            if isinstance(value, (int, float)):
                return max(minimum, int(round(value)))
            write("WARN {}: get_area_average_difficulty returned {!r}".format(
                label, value))
        except Exception as exc:
            write("WARN {}: get_area_average_difficulty failed: {}: {}".format(
                label, type(exc).__name__, exc))
    difficulties = getattr(functions, "get_quest_difficulties", None) \
        if functions else None
    if difficulties is not None and area is not None:
        try:
            values = [v for v in difficulties(area, world)
                      if isinstance(v, (int, float))]
            if values:
                write("{}: fell back to get_quest_difficulties {}".format(
                    label, values))
                return max(minimum, int(round(sum(values) / float(len(values)))))
        except Exception as exc:
            write("WARN {}: get_quest_difficulties failed: {}: {}".format(
                label, type(exc).__name__, exc))
    write("WARN {}: no difficulty available; using {}".format(label, minimum))
    return minimum


# --------------------------------------------------------------- クエスト
# クエストは2箇所にある。**書くときは必ず両方**（GAME.md §2.9）。
def quest_stores(app):
    stores = []
    quests = getattr(getattr(app, "world", None), "quests", None)
    if isinstance(quests, dict):
        stores.append(quests)
    world_dict = getattr(app, "world_dict", None)
    if isinstance(world_dict, dict) and isinstance(world_dict.get("quests"), dict):
        stores.append(world_dict["quests"])
    return stores


def quest_ids(app):
    """両方の合併。どちらに登録されるか決め打ちしないため。"""
    seen = []
    for store in quest_stores(app):
        for qid in store:
            if str(qid) not in seen:
                seen.append(str(qid))
    return seen


def quest_of(app, quest_id):
    for store in quest_stores(app):
        if quest_id in store:
            return store[quest_id]
    return None


def quest_value(quest, name, default=None):
    if isinstance(quest, dict):
        return quest.get(name, default)
    return getattr(quest, name, default)


def append_quest_value(app, quest_id, name, suffix, mark=None, on_error=None):
    """各格納先の文字列の**末尾に足す**。書けた数を返す。

    `mark` を含んでいる格納先は飛ばすので、何度呼んでも二重に足さない
    （生成の直後は片方の格納先にしか居ないことがあり、受注の時点でもう一度
    呼ぶため。GAME.md §2.9）。文字列でない値には触らない。
    """
    written = 0
    for store in quest_stores(app):
        target = store.get(quest_id)
        if target is None:
            continue
        current = quest_value(target, name, "")
        if current is None:
            current = ""
        if not isinstance(current, str):
            if on_error is not None:
                on_error("{} on {!r} is {}; not appending".format(
                    name, quest_id, type(current).__name__))
            continue
        if mark and mark in current:
            continue
        try:
            if isinstance(target, dict):
                target[name] = current + suffix
            else:
                setattr(target, name, current + suffix)
            written += 1
        except Exception:
            if on_error is not None:
                on_error("cannot append to {} on {!r}".format(name, quest_id))
    return written


def strip_quest_value(app, quest_id, name, mark, on_error=None):
    """`append_quest_value` で足した末尾を**取り除く**。消した数を返す。

    足すのは必ず末尾なので、目印から後ろを丸ごと落とせば元の文に戻る。
    目印を含まない格納先は飛ばすので、何度呼んでも安全。
    """
    written = 0
    for store in quest_stores(app):
        target = store.get(quest_id)
        if target is None:
            continue
        current = quest_value(target, name, "")
        if not isinstance(current, str) or mark not in current:
            continue
        trimmed = current[:current.rfind(mark)].rstrip()
        try:
            if isinstance(target, dict):
                target[name] = trimmed
            else:
                setattr(target, name, trimmed)
            written += 1
        except Exception:
            if on_error is not None:
                on_error("cannot strip {} on {!r}".format(name, quest_id))
    return written


def set_quest_value(app, quest_id, name, value, on_error=None):
    """両方の格納先に書く。書けた数を返す（`len(quest_stores)` が満額）。"""
    written = 0
    for store in quest_stores(app):
        target = store.get(quest_id)
        if target is None:
            continue
        try:
            if isinstance(target, dict):
                target[name] = value
            else:
                setattr(target, name, value)
            written += 1
        except Exception:
            if on_error is not None:
                on_error("cannot set {} on {!r}".format(name, quest_id))
    return written


# --------------------------------------------------- 移動の確認画面のボタン
def move_options(buttons):
    """確認画面に並ぶ `AreaMoveManager` のボタンを `(entry, args)` で返す。

    **`args` は読むだけ**。`[target_area_id, mode]` という並びは
    `targets.txt` のシグネチャそのもので、値の意味は解釈しない。
    """
    found = []
    if not isinstance(buttons, (list, tuple)):
        return found
    for entry in buttons:
        if ui.spec_cls_name(entry) != "AreaMoveManager":
            continue
        args = ui.spec_args(entry)
        if args and len(args) >= 2:
            found.append((entry, list(args)))
    return found


def pick_option(options, known_modes, words, first):
    """到着に使うボタンを選ぶ。実測値 → 文字列 → 並び順 の順に下がる。

    `known_modes` は `mode` の実測値、`words` はボタンの文字列の手掛かり、
    `first` は並び順で選ぶときに先頭を採るか（徒歩）どうか。
    どれで選んだかを3つ目に返すので、ログに残せる。
    """
    for entry, args in options:
        if str(args[1]) in [str(v) for v in known_modes]:
            return entry, args, "mode"
    for entry, args in options:
        text = entry.get("text") or ""
        if any(word in text for word in words):
            return entry, args, "text"
    index = 0 if first else min(1, len(options) - 1)
    entry, args = options[index]
    return entry, args, "order"


# --------------------------------------------------------------- 体力
# `Character.physical_integrity` / `max_physical_integrity`。**実測で確定**
# （GAME.md §2.19）:
#
#   physical_integrity  100 -> 50 -> 0        道を行くたびに減る
#   max_hp             1560 -> 1365 -> 1170   体力が減ると最大HPが下がる
#   exhausted          False -> True          50 の時点で既に True
#
# 医療施設（`MedicalTreatmentManager` / `get_heal_physical_integrity_barden`）で
# 戻す値でもある。`current_hp` は戦闘のHPで別物なので見ない。
def stamina_of(app):
    """`(体力, 最大体力)`。読めなければ `(None, None)`。"""
    player = getattr(app, "player", None)
    if player is None:
        return None, None
    value = getattr(player, "physical_integrity", None)
    limit = getattr(player, "max_physical_integrity", None)
    if not _number(limit) or limit <= 0:
        limit = getattr(player, "original_max_physical_integrity", None)
    if not _number(value) or not _number(limit) or limit <= 0:
        return None, None
    return value, limit


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)
