# -*- coding: utf-8 -*-
"""装備構成の変化を立ち絵の一時的な追加プロンプトへ反映する。

この MOD はキャラクター本体、セーブ、ゲーム本来の画像プロンプトを書き換えない。
画像生成関数へ渡るその1回の引数だけを差し替える。

装備の取得は 333_equipment_slots の内部を直接読まず、共有窓口
``equipment.gear(app, holder)`` を使う。333 が無い環境では本体の
``equipments`` に入っている weapon / wearable だけへ静かに戻る。

SD1.5 へ渡すものは英語の短い語だけにする（DOC.md「SD1.5 へ渡すもの」）。
主人公の立ち絵の描き直しは 335_player_portrait_regenerate が持つ。
"""

import hashlib
import json
import re
import sys
import typing

from instantale_modloader import equipment, frames, imagegen, items, llm, state, ui


# GUI 設定と同じ名前・既定値。ローダが apply() 前に上書きする。
EQUIPMENT_PROMPT_COUNT = 1

# 設定の上限。推定で元のプロンプトが 59〜69 トークンあり、
# CLIP の1塊（75 トークン）に残る余地は 6〜16 トークンしかない。
MAX_PROMPT_COUNT = 3

# 人物の絵を描く関数は画像生成の方式ごとに別のモジュールに居るが、どの方式かはローダの
# `imagegen` が吸収する（GAME.md §2.33）。
FAMILIES = imagegen.FAMILIES
IMAGE_FUNC = "generate_character_image"
STORE_ATTR = "_instantale_408_equipment_visual_store"
STORE_DIRNAME = "equipment_visuals"
LOG_BASENAME = "408_equipment_visuals.log"
LLM_MANAGER = "mod_408_equipment_visual_prompt"
LLM_TIMEOUT = 60

# SD1.5 へ渡す1要素の形。英小文字・数字・空白・ハイフン・アポストロフィだけ。
# 括弧は SD の強調記法になるので通さない。
SD_ITEM = re.compile(r"^[a-z0-9][a-z0-9 '-]*$")
MAX_ITEM_WORDS = 4
MAX_ITEM_CHARS = 40
# 装備から足す分の上限（推定で約14トークン）。
MAX_ADDED_CHARS = 60

def _store(ctx, write):
    """注入し直しを越えて同じ WorldStore を使う。"""
    found = getattr(sys, STORE_ATTR, None)
    if found is None:
        found = state.WorldStore(ctx, STORE_DIRNAME, write=write)
        setattr(sys, STORE_ATTR, found)
    else:
        found.rebind(ctx, write=write)
    return found


#: ゲームが選んでいる画像生成の方式。読めなければ None（ローダの `imagegen.backend`）。
config_backend = imagegen.backend


def image_targets(backend):
    """包む対象。方式が分からなければ4つとも（入っていない方式は待つだけ）。"""
    return imagegen.creature_targets(IMAGE_FUNC, backend)


def _value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    value = frames.attr(obj, name, default)
    return default if value is frames.MISSING else value


def _text(obj, name):
    value = _value(obj, name, "")
    return value.strip() if isinstance(value, str) else ""


def _find_holder(app, name):
    """画像関数の名前から、実行中の人物を見つける。見つからなければ触らない。"""
    if app is None or not isinstance(name, str) or not name.strip():
        return None
    player = _value(app, "player")
    if _text(player, "name") == name:
        return player

    # 会話中の参照が名簿へ反映されていない版にも備える。
    for attr in ("conversation_character", "current_character",
                 "current_npc", "target_character", "character"):
        candidate = _value(app, attr)
        if _text(candidate, "name") == name:
            return candidate

    world = _value(app, "world")
    characters = _value(world, "characters")
    values = characters.values() if isinstance(characters, dict) else characters
    if isinstance(values, (list, tuple)) or hasattr(values, "__iter__"):
        try:
            for candidate in values:
                if _text(candidate, "name") == name:
                    return candidate
        except Exception:
            pass
    return None


def _resolve_item(owner, value):
    if isinstance(value, str):
        inventory = items.inventory_of(owner) or {}
        return inventory.get(value)
    return value


def _equipment(owner, app):
    """[(部位, 品)]。部位名を使えるのは333の窓口が答えた場合だけ。"""
    if owner is None:
        return []
    offered = equipment.gear(app, owner)
    if offered is not None:
        return [(slot, item) for slot, item in offered if item is not None]

    # 333 が無い場合の本体側の最小フォールバック。
    # 本体の equipments の weapon / wearable は装備品の入れ物であり、
    # 333 の装備部位ではない。したがって部位名としてLLMへ渡さない。
    equipment_map = _value(owner, "equipments", {})
    if not isinstance(equipment_map, dict):
        return []
    result = []
    for slot in ("weapon", "wearable"):
        item = _resolve_item(owner, equipment_map.get(slot))
        if item is not None:
            result.append(("", item))
    return result


def _item_row(item):
    """外見に関係する文字列だけを取り出す。

    value / rarity / attributes / upgrade_level など、数値や強化状態は意図的に読まない。
    """
    row = {
        "name": _text(item, "name"),
        "description": _text(item, "description"),
        "item_type": _text(item, "item_type"),
        "sub_type": _text(item, "sub_type"),
    }
    if not any(row[key] for key in ("name", "description", "item_type", "sub_type")):
        return None
    return row


def _rows(owner, app):
    result = []
    for _slot, item in _equipment(owner, app):
        row = _item_row(item)
        if row is not None:
            result.append(row)
    return result


def _prompt_count():
    try:
        count = int(EQUIPMENT_PROMPT_COUNT)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_PROMPT_COUNT, count))


def _fingerprint(rows, count):
    """装備の見た目と要素数。どちらかが変われば LLM に聞き直す。"""
    body = json.dumps({"count": count, "items": rows}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sd_item(value):
    """SD1.5 へ渡せる1要素に均す。渡せなければ空文字。

    日本語などの非 ASCII、長すぎる句、SD の記法に使う記号はここで落ちる。
    """
    if not isinstance(value, str):
        return ""
    value = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", value)
    value = " ".join(value.split()).strip(" .;:\"'").lower()
    if not value or len(value) > MAX_ITEM_CHARS:
        return ""
    if not SD_ITEM.match(value) or len(value.split()) > MAX_ITEM_WORDS:
        return ""
    return value


def _sd_items(values, limit=None, budget=None):
    """要素の並びを SD1.5 へ渡せるものだけにする。

    文字列1つでも受け、カンマと改行で要素に割る。
    `limit` は要素数、`budget` は「, 」で繋いだときの字数の上限。
    """
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    result, used = [], 0
    for value in values:
        if not isinstance(value, str):
            continue
        for piece in re.split(r"[,\n]", value):
            item = _sd_item(piece)
            if not item or item in result:
                continue
            extra = len(item) + (2 if result else 0)
            if budget is not None and used + extra > budget:
                return result
            result.append(item)
            used += extra
            if limit is not None and len(result) >= limit:
                return result
    return result


def _loose_values(raw):
    """構造化出力を使えなかったときの返答を要素の並びにする。"""
    if not isinstance(raw, str):
        return []
    body = llm.strip_fence(raw).strip()
    try:
        parsed = json.loads(body)
    except Exception:
        return body.splitlines()
    if isinstance(parsed, dict):
        parsed = parsed.get("prompts")
    return parsed if isinstance(parsed, list) else []


def _ask_equipment_prompt(ctx, rows, count, write):
    """装備の見た目を英語の短い語で聞く。聞けなければ None。"""
    if count <= 0:
        return []
    instruction = (
        "userが提示するキャラクターの装備品の見た目を、"
        "Stable Diffusion 1.5の画像生成プロンプトの要素として英語で書いてください。\n"
        "各要素は英単語または3語以内の短文。日本語は使わない。\n"
        "装備品が複数ある場合は、見た目として目立つものを優先してください。\n"
        "要素は{}個まで。prompts の配列に入れて返してください。"
    ).format(count)
    message = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(rows, ensure_ascii=False, indent=2)},
    ]
    errors = []
    structure = llm.create_structure(
        ctx, "Mod408EquipmentLook", {"prompts": (typing.List[str], ...)},
        label="408 equipment visual prompt")
    if structure is not None:
        data = llm.ask(
            ctx, LLM_MANAGER, message, timeout=LLM_TIMEOUT, structure=structure,
            max_tokens=max(96, count * 64), label="408 equipment visual prompt",
            errors=errors)
        values = data.get("prompts") if isinstance(data, dict) else None
    else:
        raw = llm.ask(
            ctx, LLM_MANAGER, message, timeout=LLM_TIMEOUT,
            max_tokens=max(96, count * 64), label="408 equipment visual prompt",
            errors=errors)
        values = _loose_values(raw) if raw is not None else None
    if values is None:
        if errors:
            write("LLM failed: {}".format(type(errors[0]).__name__))
        else:
            write("LLM returned no equipment prompt")
        return None
    prompts = _sd_items(values, limit=count, budget=MAX_ADDED_CHARS)
    if not prompts:
        write("LLM returned nothing usable for SD1.5: {!r}".format(repr(values)[:200]))
    return prompts


def _is_regeneration():
    """本体の再生成の経路（会話相手の画像の再生成）から呼ばれたか。"""
    chain = frames.caller(depth=8)
    return "regenerate_current_image" in chain or "_regenerate_" in chain


def _is_player(app, name):
    """描いているのが主人公か。

    主人公の絵が描かれるのは作成時と 335 の描き直しだけで、335 からの呼び出しは
    本体の再生成の経路を通らない。主人公は経路を問わず足す対象にする
    （作成時はふつう装備が無く、何も足さない）。
    """
    return bool(name) and _text(_value(app, "player"), "name") == name


def _prepare(ctx, store, write, app, holder, rows):
    """装備から足す要素。控えの fingerprint が合えば LLM に聞かない。"""
    name = _text(holder, "name")
    if not name:
        return []
    playthrough = store.playthrough(app)
    bucket = store.load(playthrough)
    # 主人公の英語の外見は 335 へ移した。前の版が控えたものが残っていれば片付ける。
    if bucket.pop("player_look", None) is not None:
        store.save(playthrough)
    records = bucket.setdefault("characters", {})
    count = _prompt_count()
    if not rows or count <= 0:
        if records.pop(name, None) is not None:
            store.save(playthrough)
        return []

    fingerprint = _fingerprint(rows, count)
    record = records.get(name)
    if isinstance(record, dict) and record.get("fingerprint") == fingerprint:
        return _sd_items(record.get("prompts"), limit=count, budget=MAX_ADDED_CHARS)

    prompts = _ask_equipment_prompt(ctx, rows, count, write)
    if not prompts:
        return []        # 聞けなかった・使える語が無かった回は控えず、次の再生成で聞き直す
    records[name] = {"fingerprint": fingerprint, "prompts": list(prompts)}
    store.save(playthrough)
    write("cached {} prompt(s) for {}".format(len(prompts), name))
    return prompts


def _append_prompt(original, prompts):
    """元のプロンプトの後ろへ足す。元に在る要素は足さない。

    本体の prompt が文字列か並びかは測っていないので、どちらでも受ける。
    """
    if not prompts:
        return original
    if isinstance(original, str):
        have = {part.strip().lower() for part in original.split(",")}
        added = [p for p in prompts if p not in have]
        if not added:
            return original
        base = original.rstrip().rstrip(",").rstrip()
        return base + (", " if base else "") + ", ".join(added)
    if isinstance(original, (list, tuple)):
        have = {str(part).strip().lower() for part in original}
        added = [p for p in prompts if p not in have]
        if not added:
            return original
        return type(original)(list(original) + added)
    return original




def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="408 equipment visual:")
    store = _store(ctx, write)

    def rewrite(orig, args, kwargs, app, name, prompt):
        holder = _find_holder(app, name)
        if holder is None:
            return orig(*args, **kwargs)
        rows = _rows(holder, app)
        prompts = _prepare(ctx, store, write, app, holder, rows)
        # 最後の関所。どの経路から来ても非 ASCII は SD1.5 へ渡さない。
        if not all(isinstance(p, str) and p.isascii() for p in prompts):
            write("non-ASCII equipment prompt dropped for {}".format(name))
            return orig(*args, **kwargs)
        rewritten = _append_prompt(prompt, prompts)
        if rewritten == prompt:
            return orig(*args, **kwargs)
        new_args, new_kwargs, replaced = frames.replace_arg(
            args, kwargs, "prompt", 3, rewritten)
        if not replaced:
            return orig(*args, **kwargs)
        write("temporary equipment prompt appended for {} ({} item(s))".format(
            name, len(prompts)))
        return orig(*new_args, **new_kwargs)

    def install(target):
        @ctx.wrap(target, required=False, safe=True)
        def generate_character_image(orig, *args, **kwargs):
            """NPC の再生成と主人公の絵（335 の描き直し）の入口。

            描き終わった後の表示の読み直しは 138_fix_character_image_refresh が持つ。
            """
            app = ui.find_app()
            name = frames.arg(args, kwargs, "name", 1, None)
            if not _is_regeneration() and not _is_player(app, name):
                return orig(*args, **kwargs)
            prompt = frames.arg(args, kwargs, "prompt", 3, None)
            return rewrite(orig, args, kwargs, app, name, prompt)

    backend = config_backend()
    targets = image_targets(backend)
    for target in targets:
        install(target)

    ctx.log("408 equipment visual installed (prompt_count={}, backend={})".format(
        _prompt_count(), backend))
