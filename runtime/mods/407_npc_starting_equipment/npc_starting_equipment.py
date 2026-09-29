# -*- coding: utf-8 -*-
"""407_npc_starting_equipment: パーティ加入NPCへ初期装備を一度だけ与える。

このMODの役割は3つに分けている。

1. ``InstantaleApp.add_party_member`` は本体へそのまま通す。加入が済んだNPCについて、
   人物情報のコピーを背景ワーカーでLLMへ送り、武器と防具の候補を得る。
2. メインスレッドでゲーム本来の ``generate_item_from_item_data`` を呼んでNPCの通常inventoryへ入れ、
   ローダの窓口 ``equipment.equip`` で装備欄へ入れてもらい、ゲームの ``save_game`` を呼ぶ。
3. ``save_game`` が通った後で、407のstateへ「このNPCに渡した」印を書く。

装備させるのは装備欄の MOD（333）の仕事で、407 は窓口に頼むだけ。本体は仲間の ``equipments`` を
保存しないので、407 が自分で書いてもロードで外れる（GAME.md §2.13.3）。装備欄の MOD が居なければ、
品は持ち物に渡すだけにする（DOC.md「装備させる」）。

加入を装備の完成まで止めないのは、本体の呼び出し側が ``add_party_member`` の直後に
パーティーが変わった前提で先へ進み、そのまま保存するため（DOC.md「加入は止めない」）。

アイテム本体はMOD独自の入れ物へ移さない。ゲームのNPC inventoryへ置くため、通常のセーブ処理が
そのまま扱う（装備欄へ入った品は装備欄の MOD がセーブへ足す）。407のstateへ残すのは、
「このNPCにどの種類を渡したか」という印だけである。
"""

from __future__ import annotations

import json
import sys
import threading
import typing

from instantale_modloader import equipment, frames, ids, items, jobs, llm, npcs, state, ui


# ---- MOD固有の設定 ---------------------------------------------------------
STATE_DIRNAME = "npc_starting_equipment"
LOG_BASENAME = "npc_starting_equipment.log"
MANAGER_NAME = "mod_npc_starting_equipment"
LLM_TIMEOUT = 120
LLM_MAX_TOKENS = 1200
MAX_TEXT = 600
MAX_ITEM_RETRIES = 2

# ``WorldStore`` は注入し直しを跨いで同じものを使う。
STATE_STORE = "__instantale_407_npc_starting_equipment_store__"
STATE_KEY_OVERRIDE = "__instantale_407_npc_starting_equipment_key__"
RUNTIME_STORE = "__instantale_407_npc_starting_equipment_runtime__"
WORLDS = state.SysWorldStore(STATE_STORE, STATE_DIRNAME, STATE_KEY_OVERRIDE)

RARITIES = ("common", "rare", "magical", "epic", "legendary", "mythic")

# generate_item_from_item_data へ渡す細分。ゲーム自身の品揃え生成のスキーマと同じ語彙で、
# 312 と同じ組。武器はゲームが ``_weapon`` を足して item_detail にするので、
# ``medium_weapon`` を渡すと ``medium_weapon_weapon`` になって生成が落ちる
# （VERIFICATION.md §3.76）。
ITEM_TYPES = {
    "weapon": ("small", "medium", "long", "large", "throwable"),
    "wearable": ("headgear", "body_armor", "legwear", "gauntlets", "shield",
                 "accessory", "clothing"),
}
EQUIP_TYPES = ("weapon", "wearable")
FALLBACK_SUBTYPE = {"weapon": "medium", "wearable": "body_armor"}
TYPE_LABELS = {"weapon": "武器", "wearable": "防具"}


def _text(value, limit=MAX_TEXT):
    """LLMへ渡す文章を短くする。ゲーム側のオブジェクトは渡さない。"""
    if not isinstance(value, str):
        return ""
    return frames.short(value.strip(), limit)


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _npc_data(app, npc_id):
    """加入直後のNPCから、LLMに渡す必要最小限のコピーを作る。"""
    try:
        data = npcs.save_npcs(app).get(str(npc_id))
    except Exception:
        data = None
    if not isinstance(data, dict):
        # save_npcs 側が先に更新される版と、実行時 Character だけが
        # 先に更新される版がある。どちらも受ける。
        character = ui.character_of(app, npc_id)
        if character is None:
            return None
        data = {
            name: frames.attr(character, name, None)
            for name in ("name", "category", "job", "profile",
                         "personality", "look_description",
                         "experience_level", "config")
        }

    config = data.get("config")
    if not isinstance(config, dict):
        config = {}
    # ここは完全なNPCデータではなく、装備を考えるための必要最小限だけを写す。
    return {
        "npc_id": str(npc_id),
        "name": _text(data.get("name"), 80),
        "category": _text(data.get("category"), 80),
        "job": _text(data.get("job"), 80),
        "profile": _text(data.get("profile")),
        "personality": _text(data.get("personality")),
        "look_description": _text(data.get("look_description")),
        "experience_level": _int(data.get("experience_level")),
        "difficulty_level": _int(config.get("difficulty_level")),
    }


def _level_value(snapshot):
    """NPCの強さをゲームのアイテム価値段階1〜70へ決定論的に写す。

    ``difficulty_level`` は地域のNPC生成水準、``experience_level`` は生成後の
    表示レベルである。通常は後者が前者より少し高くなるため、難易度を基準にしつつ
    レベル側の補正が低すぎないようにする。最終的な値はLLMへ任せない。
    """
    difficulty = _int(snapshot.get("difficulty_level")) or 0
    experience = _int(snapshot.get("experience_level")) or 0
    value = max(difficulty, experience - 5, 1)
    return max(1, min(70, value))


def _subtype(item_type, raw):
    """LLMが返した細分をゲームの語彙へ寄せる。寄せられなければ None。

    武器の ``small_weapon`` のような item_detail の綴りで返されても、
    ``_weapon`` を外せば同じ細分なので受ける。
    """
    word = _text(raw, 60).casefold().replace(" ", "_").replace("-", "_")
    if item_type == "weapon" and word.endswith("_weapon"):
        word = word[:-len("_weapon")]
    return word if word in ITEM_TYPES.get(item_type, ()) else None


def _structure(ctx):
    """初期装備の構造化出力。``Literal`` は使わない（空だと pydantic が落ちる。llm.py）。"""
    item = llm.create_structure(
        ctx,
        "NpcStartingEquipmentItem",
        {
            "item_type": (str, ...),
            "item_name": (str, ...),
            "description": (str, ...),
            "item_sub_type": (str, ...),
            "rarity": (str, ...),
            "item_appearance": (str, ...),
        },
        label="npc starting equipment",
    )
    if item is None:
        return None
    return llm.create_structure(
        ctx,
        "NpcStartingEquipment",
        {"items": (typing.List[item], ...)},
        label="npc starting equipment",
    )


def _messages(snapshot, types):
    """頼み文はsystemへ置き、user側には人物6項目だけを渡す。"""
    wanted = "と".join(TYPE_LABELS[t] for t in types)
    subtypes = "\n".join(
        "  - {}: {}".format(t, " / ".join(ITEM_TYPES[t])) for t in types)
    system = (
        "あなたはRPGに登場するNPCの装備品を作る担当だ。\n"
        "【指示】\n"
        "userが示すNPCが、仲間になった時点で身に着けている{wanted}を"
        "1つずつ作り、JSONオブジェクト1個で返せ。\n"
        "品は人物の職業、プロフィール、性格、外見に見合うものにする。\n"
        "【出力要素】（items の各要素）\n"
        "- item_type: {types}。\n"
        "- item_name: 日本語の品名。\n"
        "- description: 日本語で1〜2文の説明。由来や使い込まれ方など、"
        "持ち主らしさが分かるように書く。\n"
        "- item_sub_type: item_type ごとに次から1つ選ぶ。\n{subtypes}\n"
        "- rarity: {rarities} のどれか。レアリティ兼魔法的性質の度合で、"
        "どんな名品も魔法がなければ common か rare、"
        "どんなにしょぼくても魔法があれば最低で magical。\n"
        "- item_appearance: 英語で短い一文。見た目を画像の手掛かりとして書く。"
        "(例: 'A rusted longsword with a chipped blade and worn leather grip.')"
    ).format(wanted=wanted, types=" / ".join(types), subtypes=subtypes,
             rarities=" / ".join(RARITIES))
    source = {
        key: snapshot.get(key, "")
        for key in ("name", "category", "job", "profile",
                    "personality", "look_description")
    }
    user = (
        "以下のNPCの初期装備になる{}を1つずつ作成してください。\n".format(wanted) +
        json.dumps(source, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _normalise(raw, snapshot, types):
    """LLMの返答を、頼んだ種類ごとに1件ずつゲームの生成入口へ渡せる形へ絞る。"""
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, (list, tuple)):
        return []
    value = _level_value(snapshot)
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        # 武器・防具の判定は独自slotではなく、ゲーム本体と同じ
        # item_typeを唯一の軸にする。
        item_type = _text(row.get("item_type"), 40).casefold()
        if item_type not in types or item_type in found:
            continue
        name = _text(row.get("item_name"), 80)
        if not name:
            continue
        rarity = _text(row.get("rarity"), 30).casefold()
        found[item_type] = {
            "name": name,
            "description": _text(row.get("description"), 300) or name,
            "item_type": item_type,
            "item_sub_type": (_subtype(item_type, row.get("item_sub_type"))
                              or FALLBACK_SUBTYPE[item_type]),
            "value": value,
            "item_appearance": _text(row.get("item_appearance"), 240) or name,
            "rarity": rarity if rarity in RARITIES else "common",
        }
    return [found[key] for key in EQUIP_TYPES if key in found]


def _playthrough(app):
    """いまの周回の鍵。世界が読めなければ None。"""
    key = state.playthrough_key(app)
    return key if key and key != state.UNKNOWN_WORLD else None


def _worn_types(app, character):
    """もう身に着けている種類。装備欄の MOD が答えればそちら、居なければ ``equipments`` から読む。"""
    worn = equipment.gear(app, character)
    if worn is not None:
        return {getattr(item, "item_type", None) for _slot, item in worn} & set(EQUIP_TYPES)
    equipments = frames.attr(character, "equipments", None)
    if not isinstance(equipments, dict):
        return set()
    return {key for key in EQUIP_TYPES if equipments.get(key)}


def _make_item(ctx, app, character, spec, write):
    """ゲーム本来のアイテム生成入口を呼び、追加された鍵を返す。"""
    maker = frames.attr(app, "generate_item_from_item_data", None)
    if not callable(maker) or not isinstance(items.inventory_of(character), dict):
        return None
    attempts = [dict(spec)]
    # LLMが選んだ細分で落ちたときは、名称や説明は変えず、
    # その種類の一般的な細分で一度だけ作り直す。
    fallback = FALLBACK_SUBTYPE.get(spec.get("item_type"))
    if fallback and fallback != spec.get("item_sub_type"):
        candidate = dict(spec)
        candidate["item_sub_type"] = fallback
        attempts.append(candidate)
    for candidate in attempts:
        inventory = items.inventory_of(character)
        if not isinstance(inventory, dict):
            return None
        before = set(inventory)
        try:
            item = maker(
                candidate["name"], candidate["description"],
                candidate["item_type"], candidate["item_sub_type"],
                candidate["value"], candidate["item_appearance"],
                candidate["rarity"], character,
            )
        except Exception:
            ctx.log_exc("npc starting equipment: generate_item_from_item_data failed "
                        "({} / {})".format(candidate["item_type"],
                                           candidate["item_sub_type"]))
            continue
        inventory = items.inventory_of(character)
        if not isinstance(inventory, dict):
            return None
        added = [key for key in inventory if key not in before]
        if added:
            return str(added[0])
        if item is None:
            continue
        # ゲームの入口がオブジェクトだけ返す版にも対応する。
        item_key = ids.claim(app, "item", write=write)
        try:
            inventory[item_key] = item
        except Exception:
            continue
        return item_key
    write("item generation failed for {!r}".format(spec.get("name")))
    return None


def _runtime():
    found = getattr(sys, RUNTIME_STORE, None)
    if isinstance(found, dict) and isinstance(found.get("marks"), dict) \
            and all(isinstance(v, dict) for v in found["marks"].values()):
        return found
    found = {
        "pending": set(),
        "generation": 0,
        "suppress": 0,
        # 周回の鍵 -> {npc_id: 渡した種類の集合}。save_game が通ったら state へ書く。
        "marks": {},
        "lock": threading.RLock(),
        "worker": None,
    }
    setattr(sys, RUNTIME_STORE, found)
    return found


def apply(ctx):
    """ローダが呼ぶ入口。ゲーム側の重い処理はすべてsafeな境界で包む。"""
    write = ctx.logger(LOG_BASENAME)
    worlds = WORLDS.bind(ctx, write)
    runtime = _runtime()
    marks = runtime["marks"]
    schedule = ui.scheduler(ctx, "npc starting equipment")
    save_soon = ui.saver(ctx, write, "npc starting equipment")

    def given_types(key, npc_id):
        """もう渡した種類。state の印と、保存を待っている印を合わせる。"""
        with worlds.lock:
            pending = set(marks.get(key, {}).get(npc_id, ()))
            bucket = worlds.load(key)
        records = bucket.get("npcs") if isinstance(bucket, dict) else None
        record = records.get(npc_id) if isinstance(records, dict) else None
        if not isinstance(record, dict):
            return pending
        if record.get("generated"):
            return pending | set(EQUIP_TYPES)
        given = record.get("given")
        return pending | ({str(t) for t in given} if isinstance(given, list) else set())

    def in_party(app, npc_id):
        try:
            return npc_id in {str(member) for member in ui.party_member_ids(app)}
        except Exception:
            return False

    def enqueue(app, npc_id, attempt=0, types=None, snapshot=None):
        """加入が済んだNPCの初期装備をLLMへ頼む。積んだら True。"""
        if app is None or not npc_id:
            return False
        npc_id = str(npc_id)
        if npc_id == ui.PLAYER_ID:
            return False
        with runtime["lock"]:
            if runtime["suppress"]:
                return False
            generation = runtime["generation"]
        if not in_party(app, npc_id):
            # add_party_member が実際には加入させなかった場合は何もしない。
            return False
        key = _playthrough(app)
        if key is None:
            write("NPC {} has no stable world key; starting equipment skipped".format(npc_id))
            return False
        given = given_types(key, npc_id)
        if given >= set(EQUIP_TYPES):
            return False
        character = ui.character_of(app, npc_id)
        if character is None:
            write("NPC {} is not in the runtime roster; starting equipment skipped".format(npc_id))
            return False
        # 渡していない種類のうち、もう身に着けている種類は作らない。
        worn = _worn_types(app, character)
        missing = [t for t in EQUIP_TYPES
                   if t not in given and t not in worn and (types is None or t in types)]
        if not missing:
            return False
        if snapshot is None:
            snapshot = _npc_data(app, npc_id)
        if snapshot is None:
            write("NPC {} had no usable source data".format(npc_id))
            return False
        job = {
            "generation": generation,
            "npc_id": npc_id,
            "key": key,
            "snapshot": snapshot,
            "types": missing,
            "attempt": attempt,
        }
        pending = (generation, npc_id)
        with runtime["lock"]:
            if pending in runtime["pending"]:
                return False
            runtime["pending"].add(pending)
        if worker.enqueue(job):
            write("starting equipment queued: npc={} name={!r} types={} attempt={}".format(
                npc_id, snapshot.get("name", ""), missing, attempt))
            return True
        with runtime["lock"]:
            runtime["pending"].discard(pending)
        return False

    def ask_job(job):
        """背景ワーカー。ゲームオブジェクトには触れず、LLMだけを待つ。"""
        result = []
        try:
            if not ctx.superseded():
                structure = _structure(ctx)
                if structure is not None:
                    raw = llm.ask(
                        ctx, MANAGER_NAME, _messages(job["snapshot"], job["types"]),
                        timeout=LLM_TIMEOUT, structure=structure,
                        max_tokens=LLM_MAX_TOKENS,
                        label="npc starting equipment", write=write,
                    )
                    result = _normalise(raw, job["snapshot"], job["types"])
        except Exception:
            ctx.log_exc("npc starting equipment: LLM job failed")
        schedule(lambda: finish_job(job, result), delay=0)

    def finish_job(job, specs):
        """メインスレッド。アイテムを作って装備欄へ入れてもらい、保存を頼む。"""
        npc_id = job["npc_id"]
        with runtime["lock"]:
            runtime["pending"].discard((job["generation"], npc_id))
            stale = job["generation"] != runtime["generation"]
        if stale:
            write("NPC {}: the world was loaded or restarted; equipment dropped".format(npc_id))
            return
        app = ui.find_app()
        if app is None:
            return
        if _playthrough(app) != job["key"]:
            write("NPC {} moved to another playthrough; equipment skipped".format(npc_id))
            return
        # LLM待ちの間に離脱した場合は、加入時装備を後から渡さない。
        if not in_party(app, npc_id):
            write("NPC {} left the party before equipment was ready".format(npc_id))
            return
        character = ui.character_of(app, npc_id)
        if character is None:
            write("NPC {} is no longer available; starting equipment skipped".format(npc_id))
            return
        made = []
        handed = set()
        for spec in specs:
            item_type = spec["item_type"]
            if item_type in handed:
                continue
            item_key = _make_item(ctx, app, character, spec, write)
            if not item_key:
                continue
            handed.add(item_type)
            item = (items.inventory_of(character) or {}).get(item_key)
            # 装備させるのは装備欄の MOD。None なら居ないので、持ち物に渡すだけで終える。
            answer = equipment.equip(app, character, item) if item is not None else None
            made.append("{}={}({}) -> {}".format(
                item_type, spec["name"], item_key, answer or "inventory"))
        missing = [t for t in job["types"] if t not in handed]
        if handed:
            # 印は save_game が通った後に state へ書く（save_game の包み）。
            with worlds.lock:
                marks.setdefault(job["key"], {}).setdefault(npc_id, set()).update(handed)
            write("starting equipment handed: npc={} {} missing={}".format(
                npc_id, " ".join(made), missing or "-"))
            save_soon(app, "starting equipment")
        if not missing:
            return
        if job["attempt"] < MAX_ITEM_RETRIES and enqueue(
                app, npc_id, attempt=job["attempt"] + 1, types=missing,
                snapshot=job["snapshot"]):
            return
        # 作れなかった種類は、再加入時に補えるようにする（渡した種類だけを印に残す）。
        write("starting equipment incomplete: npc={} missing={}".format(npc_id, missing))

    worker = runtime.get("worker")
    if not isinstance(worker, jobs.Worker):
        worker = jobs.Worker(
            ctx, ask_job, name="npc_starting_equipment",
            label="npc starting equipment", max_pending=8,
        )
        runtime["worker"] = worker
    else:
        worker.rebind(ctx, ask_job, write)

    @ctx.wrap("__main__:InstantaleApp.add_party_member",
               required=False, safe=True)
    def add_party_member(orig, self, character_id=None, *args, **kwargs):
        """本体の加入処理をそのまま通し、加入できたNPCの初期装備を頼む。"""
        result = orig(self, character_id, *args, **kwargs)
        try:
            enqueue(self, character_id)
        except Exception:
            ctx.log_exc("npc starting equipment: party join hook failed")
        return result

    @ctx.wrap("__main__:InstantaleApp.save_game", required=False)
    def save_game(orig, self, *args, **kwargs):
        """ゲームの保存が通ったら、保存を待っている印を state へ書く。

        錠は保存の間ずっと持つ。保存の途中で印が増えると、
        セーブに入らなかった装備の印だけを確定してしまう。
        ``orig`` が投げたら書かない（次の保存でまとめて書く）。
        """
        with worlds.lock:
            result = orig(self, *args, **kwargs)
            try:
                for key in sorted(marks):
                    bucket = worlds.load(key)
                    records = bucket.setdefault("npcs", {})
                    if not isinstance(records, dict):
                        records = bucket["npcs"] = {}
                    for npc_id, handed in sorted(marks[key].items()):
                        old = records.get(npc_id)
                        given = set(handed)
                        if isinstance(old, dict) and isinstance(old.get("given"), list):
                            given.update(str(t) for t in old["given"])
                        records[npc_id] = {
                            "generated": given >= set(EQUIP_TYPES),
                            "given": [t for t in EQUIP_TYPES if t in given],
                        }
                    if worlds.save(key):
                        write("save: marked {} of {}".format(sorted(marks[key]), key))
                        del marks[key]
            except Exception:
                ctx.log_exc("npc starting equipment: cannot write the marks after the save")
        return result

    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_init(orig, self, *args, **kwargs):
        """ロードと新規開始。LLM待ちの仕事と、保存されなかった印を捨てる。"""
        with runtime["lock"]:
            runtime["generation"] += 1
            runtime["pending"].clear()
        with worlds.lock:
            dropped = {key: sorted(held) for key, held in marks.items()}
            marks.clear()
        if dropped:
            write("load: unsaved marks dropped: {}".format(dropped))
        result = orig(self, *args, **kwargs)
        worlds.forget()
        return result

    @ctx.wrap("__main__:InstantaleApp.load_game_new", required=False, safe=True)
    def load_game_new(orig, self, *args, **kwargs):
        # セーブのロードで既存の仲間を加入扱いにしない。
        with runtime["lock"]:
            runtime["suppress"] += 1
        try:
            return orig(self, *args, **kwargs)
        finally:
            with runtime["lock"]:
                runtime["suppress"] = max(0, runtime["suppress"] - 1)

    ctx.log("npc starting equipment: installed; waiting for party joins")
