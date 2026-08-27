# -*- coding: utf-8 -*-
"""戦闘LLMへ渡す同行NPC情報を、本体の表記に寄せた形式で補う。

方針:
- 本体の referee_* や戦闘計算を再実装しない。
- `instantale_modloader.ui` の共通APIから現在の同行NPCだけを引く。
- `instantale_modloader.llm.watch_aliases` に send_request の後生え・別名対策を任せる。
- 戦闘中判定は 107_fix_battle_flag_stuck が正常化した `app.in_battle` を参照する。
- プレイヤー情報は本体に任せ、本体が作った prompt は消さず、user message の末尾へ同行NPC情報を足すだけ。
- equipments があれば inventory から weapon / wearable を解決し、`weapon: 名前(説明)` の本体寄り表記と attributes を分離して渡す。
- NPCはロード時の `World.generate_character(character_value)` に渡されたゲーム自身の保存辞書も控え、runtime側で人物・口調・装備参照が欠ける場合だけ同じ保存形式をfallbackとして使う。
- 保存辞書は装備/所持品の有無にかかわらずNPCごとに控える。タイトルへ戻る時は控えを破棄し、別ワールド/別セーブへの持ち越しを防ぐ。
- 攻撃力・防御力をゲーム内部のdamageへ直接加算はしない。

v7:
- 1項目ずつの上限を用途で分け、同行者全員ぶんの合計にも蓋をした
  （referee は1手ごとに呼ばれるので、素の 2,400 文字 × 7項目 × 人数がそのまま毎ターン載っていた）。
- 追記したログに文字数を出す。上限の効きは `out/battle_character_context.log` で数える。
- `in_battle` が落ちている時に `in_boss_battle` / `in_colosseum_battle` が
  立っていたら1度だけ記録する。判定を広げるかはその記録が出てから決める。
- `send_request_with_no_structure` も見張る。どの referee がどちらの送り口を通るかは
  manager_name では決まらないため（足す条件は今までどおり BATTLE_MANAGERS と in_battle）。
"""

from instantale_modloader import frames, llm, ui


MARKER = "【パーティーメンバー戦闘情報】"
LOG_BASENAME = "battle_character_context.log"

# referee がどちらの送り口を通るかは manager_name では決まらない
# （out/recon/targets.txt にはどちらも在る）。両方を見張り、
# 足すかどうかは BATTLE_MANAGERS と in_battle で決める。
# 先頭2引数は (manager_name, message) で共通。
BATTLE_SEND_TARGETS = (
    "scripts.llm.llm_manager_battle:send_request",
    "scripts.llm.llm_manager_battle:send_request_with_no_structure",
)

# 戦闘中判定に使うのは in_battle だけ（107 が正常化するのもこれだけ）。
# 他2つは立っているところをまだ観測していないので、立っていたら記録だけ残す。
OTHER_BATTLE_FLAGS = ("in_boss_battle", "in_colosseum_battle")

# referee は1手ごとに呼ばれ、同行者ぶんが毎ターン user message に載る。
# 1項目ずつの上限を用途で分け、その上で全員ぶんの合計にも蓋をする
# （204_probe_prompt_bloat が測っているのと同じ肥大）。
FIELD_CHARS = (
    ("profile", 800),
    ("personality", 500),
    ("speech_style", 400),
    ("job", 200),
    ("tactics", 400),
    ("traits", 300),
    ("status", 300),
)
ITEM_NAME_CHARS = 120
ITEM_DESCRIPTION_CHARS = 400
ITEM_ATTRIBUTES_CHARS = 300
ITEM_FALLBACK_CHARS = 400
#: 同行NPC全員ぶんの合計の目安。1人あたりへ割り、溢れた分は末尾の項目から落とす。
BLOCK_TOTAL_CHARS = 6000
#: 何人居ても1人ぶんはこれだけ確保する（名前とHPだけになるのを避ける）。
MEMBER_MIN_CHARS = 900

# 現行リコンで確認された battle referee の manager_name。
# この名前の推論にだけ補足する。他のLLM処理には触らない。
BATTLE_MANAGERS = {
    "referee_enemy_new",
    "referee_member_new_new",
    "referee_npc",
    "referee_npc_rewrite",
    "referee_player_any_input_new",
    "referee_player_any_input_new_new",
    "referee_player_any_input_new_new_with_skill",
    "referee_player_attack_new_new",
    "referee_player_skill_new_new",
}


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="battle context:")

    state = {
        "saved_character": {},
        "noted_flags": set(),
    }

    def note_other_battle_flags(app):
        """`in_battle` が立たない戦闘があるかを確かめるための1行。

        107 が正常化するのは `in_battle` だけで、他2つは立っているところを
        まだ観測していない（107 の記録）。判定を広げる前に、
        本当に立つ場面があるのかをここで拾う。立ったフラグごとに1度だけ残す。
        """
        for flag in OTHER_BATTLE_FLAGS:
            if flag in state["noted_flags"]:
                continue
            if bool(getattr(app, flag, False)):
                state["noted_flags"].add(flag)
                write("NOTE {} is set while in_battle is not; "
                      "party-member context stayed off".format(flag))

    # ------------------------------------------------------------ 安全な読み取り

    def value_of(obj, name, default=None):
        """dict / object のどちらでも読む。object はローダ共通 frames.attr を使う。"""
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return frames.attr(obj, name, default)

    def text_of(obj, name):
        """文字列属性。dictにも対応し、object側は共通部品を使う。"""
        if obj is None:
            return None
        if isinstance(obj, dict):
            value = obj.get(name)
            return value if isinstance(value, str) and value.strip() else None
        return frames.text_of(obj, name)

    def present(value):
        return value not in (None, "", [], {}, ())

    def short(value, limit=1800):
        if value is None:
            return ""
        try:
            text = str(value)
        except Exception:
            return ""
        return frames.short(text, limit)

    def inventory_of(character):
        """持ち物の実体 `{item_id: item}`。312_shop_restock と同じ読み方。"""
        if character is None:
            return None
        inventory = value_of(character, "inventory")
        if isinstance(inventory, dict):
            return inventory
        inner = value_of(inventory, "inventory")
        if isinstance(inner, dict):
            return inner
        return None

    def saved_character_of(character):
        """ロード時にゲーム自身が読んだNPC保存辞書。無ければNone。"""
        character_id = value_of(character, "id")
        if character_id in (None, ""):
            return None
        return state["saved_character"].get(str(character_id))

    def equipment_sources(character):
        """新しいruntime状態を優先し、ロード時保存辞書をfallbackにする。"""
        runtime_eq = value_of(character, "equipments")
        runtime_inv = inventory_of(character)
        saved = saved_character_of(character) or {}
        saved_eq = saved.get("equipments")
        saved_inv = saved.get("inventory")
        return (
            runtime_eq if isinstance(runtime_eq, dict) else None,
            runtime_inv if isinstance(runtime_inv, dict) else None,
            saved_eq if isinstance(saved_eq, dict) else None,
            saved_inv if isinstance(saved_inv, dict) else None,
        )

    # ------------------------------------------------------------ 装備

    def item_summary(item):
        """本体の weapon: 名前(説明) に寄せ、内部属性だけ別フィールドへ分ける。"""
        if item is None:
            return None, None

        name = text_of(item, "name") or text_of(item, "item_name")
        description = text_of(item, "description")
        attributes = value_of(item, "attributes")

        if name and description:
            main = "{}({})".format(
                short(name, ITEM_NAME_CHARS), short(description, ITEM_DESCRIPTION_CHARS))
        elif name:
            main = short(name, ITEM_NAME_CHARS)
        elif description:
            main = short(description, ITEM_DESCRIPTION_CHARS)
        else:
            main = short(item, ITEM_FALLBACK_CHARS)

        attrs = short(attributes, ITEM_ATTRIBUTES_CHARS) if present(attributes) else None
        return main, attrs

    def equipment_summary(character, slot):
        runtime_eq, runtime_inv, saved_eq, saved_inv = equipment_sources(character)

        ref = runtime_eq.get(slot) if isinstance(runtime_eq, dict) else None
        source = "runtime"
        if not present(ref) and isinstance(saved_eq, dict):
            ref = saved_eq.get(slot)
            if present(ref):
                source = "saved"

        if not present(ref):
            return None, None

        if isinstance(ref, dict):
            return item_summary(ref)
        if not isinstance(ref, (str, bytes, int, float, bool)):
            if text_of(ref, "name") or text_of(ref, "item_name"):
                return item_summary(ref)

        inventories=[]
        if isinstance(runtime_inv, dict):
            inventories.append(("runtime", runtime_inv))
        if isinstance(saved_inv, dict) and saved_inv is not runtime_inv:
            inventories.append(("saved", saved_inv))

        for inv_source, inventory in inventories:
            item = inventory.get(ref)
            if item is None:
                item = inventory.get(str(ref))
            if item is not None:
                if source == "saved" or inv_source == "saved":
                    write("equipment fallback: character={!r} slot={} ref={} via {}+{}".format(
                        text_of(character, "name") or value_of(character, "id"),
                        slot, short(ref, 120), source, inv_source))
                return item_summary(item)

        return "item_id=" + short(ref, 200), None


    # ------------------------------------------------------------ Character -> 同行NPC戦闘表示

    def hp_of(character):
        current = value_of(character, "current_hp")
        maximum = value_of(character, "max_hp")
        if maximum is None:
            maximum = value_of(character, "original_max_hp")

        # dict化済みデータの "HP": "132/132" も受ける。
        if current is None:
            current = value_of(character, "HP")

        if current is None:
            return None
        if isinstance(current, str) and "/" in current:
            return current
        return "{}/{}".format(current, maximum) if maximum is not None else str(current)

    def field_with_saved_fallback(character, attr):
        """runtimeを優先し、欠けているNPC項目だけロード時保存辞書から補う。"""
        value = value_of(character, attr)
        if present(value):
            return value
        saved = saved_character_of(character)
        if isinstance(saved, dict):
            value = saved.get(attr)
            if present(value):
                return value
        return None

    def character_block(character, role, budget=BLOCK_TOTAL_CHARS):
        """1人ぶんを `budget` 文字までで組む。溢れた分は末尾の項目から落とす。

        並びがそのまま優先順位。名前とHPは必ず載せ、
        次に戦闘の材料である装備、その後に人物・口調の順で埋める。
        """
        name = text_of(character, "name")
        if not name:
            # dict化済みの日本語キーへの保険。
            raw = value_of(character, "名前")
            name = raw if isinstance(raw, str) and raw.strip() else None
        if not name:
            return None

        lines = ["- {}: {}".format(role, short(name, 300))]

        hp = hp_of(character)
        if hp:
            lines.append("  HP: " + short(hp, 100))

        def add(label, text):
            """入るなら足す。入らなければ False（呼び側はそこで打ち切る）。"""
            if not text:
                return True
            line = "  {}: {}".format(label, text)
            used = sum(len(x) + 1 for x in lines)
            if used + len(line) + 1 > budget:
                return False
            lines.append(line)
            return True

        weapon, weapon_attributes = equipment_summary(character, "weapon")
        wearable, wearable_attributes = equipment_summary(character, "wearable")
        for label, text in (
            ("weapon", weapon),
            ("weapon_attributes", weapon_attributes),
            ("wearable", wearable),
            ("wearable_attributes", wearable_attributes),
        ):
            if not add(label, text):
                break

        # 戦闘時に意味があり、Characterが元から持つ情報だけ。
        for attr, limit in FIELD_CHARS:
            value = field_with_saved_fallback(character, attr)
            if not present(value):
                continue
            if not add(attr, short(value, limit)):
                break

        return "\n".join(lines)

    def current_party(app):
        """ローダ共通APIだけで現在の同行NPCを引く。プレイヤーは本体情報に任せる。"""
        if app is None:
            return []

        result = []
        seen = set()

        for member_id in ui.party_member_ids(app):
            member = ui.character_of(app, member_id)
            if member is None:
                write("WARN cannot resolve party member {!r}; {}".format(
                    member_id, ui.describe_stores(app)))
                continue
            if id(member) in seen:
                continue
            seen.add(id(member))
            result.append(("party_member", member))

        return result

    def build_block(app):
        """追記する本文と、載せた人数を返す。

        人数はログ用。ここで返さないと、同行者の解決が1手につき2回走る。
        """
        members = current_party(app)
        budget = (
            max(MEMBER_MIN_CHARS, BLOCK_TOTAL_CHARS // len(members))
            if members else BLOCK_TOTAL_CHARS
        )

        blocks = []
        for role, character in members:
            rendered = character_block(character, role, budget)
            if rendered:
                blocks.append(rendered)

        if not blocks:
            return (
                "\n\n" + MARKER + "\n"
                "パーティーメンバーなし"
            ), 0

        return (
            "\n\n" + MARKER + "\n"
            "パーティーメンバーについては、以下の人物・装備情報を優先して参照してください。\n"
            + "\n".join(blocks)
        ), len(blocks)

    # ------------------------------------------------------------ message

    def manager_name_of(args, kwargs):
        if args:
            return args[0]
        return kwargs.get("manager_name")

    def message_of(args, kwargs):
        if len(args) >= 2 and isinstance(args[1], list):
            return args[1], "args"
        message = kwargs.get("message")
        if isinstance(message, list):
            return message, "kwargs"
        return None, None

    def replace_message(args, kwargs, message, where):
        if where == "args":
            return args[:1] + (message,) + args[2:], kwargs
        if where == "kwargs":
            new_kwargs = dict(kwargs)
            new_kwargs["message"] = message
            return args, new_kwargs
        return args, kwargs

    def append_block(message, block):
        """最後の user message に追記。呼び出し元のlist/dictは壊さない。"""
        if not block:
            return message

        for item in message:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str) and MARKER in content:
                return message

        rewritten = list(message)

        for index in range(len(rewritten) - 1, -1, -1):
            item = rewritten[index]
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = item.get("content")
            if not isinstance(content, str):
                continue

            replacement = dict(item)
            replacement["content"] = content + block
            rewritten[index] = replacement
            return rewritten

        # 通常のbattle refereeにはuserがあるが、無い版でも情報を落とさない。
        rewritten.append({"role": "user", "content": block.lstrip("\n")})
        return rewritten

    # ------------------------------------------------------------ ロード時のNPC保存形
    @ctx.wrap("__main__:World.generate_character", required=False, safe=True)
    def remember_saved_character(orig, self, character_id, character_value, *args, **kwargs):
        try:
            if isinstance(character_value, dict) and character_id is not None:
                equipments = character_value.get("equipments")
                inventory = character_value.get("inventory")
                # speech_style 等だけを持つNPCもいるため、装備/所持品の有無では絞らない。
                state["saved_character"][str(character_id)] = {
                    "equipments": dict(equipments) if isinstance(equipments, dict) else {},
                    "inventory": dict(inventory) if isinstance(inventory, dict) else {},
                    "profile": character_value.get("profile"),
                    "personality": character_value.get("personality"),
                    "speech_style": character_value.get("speech_style"),
                    "job": character_value.get("job"),
                    "tactics": character_value.get("tactics"),
                    "traits": character_value.get("traits"),
                    "status": character_value.get("status"),
                }
        except Exception:
            ctx.log_exc("battle character context: cannot remember saved NPC character data")
        return orig(self, character_id, character_value, *args, **kwargs)

    # ------------------------------------------------------------ フック


    @ctx.wrap("__main__:InstantaleApp.return_to_title", required=False, safe=True)
    def clear_saved_character_cache(orig, self, *args, **kwargs):
        # runtimeを跨いで古いcharacter_idの保存辞書をfallbackしない。
        state["saved_character"].clear()
        state["noted_flags"].clear()
        return orig(self, *args, **kwargs)

    def install_send(target):
        @ctx.wrap(target, required=False, safe=True)
        def battle_send(orig, *args, **kwargs):
            manager_name = manager_name_of(args, kwargs)
            if manager_name not in BATTLE_MANAGERS:
                return orig(*args, **kwargs)

            message, where = message_of(args, kwargs)
            if message is None:
                write("WARN {} has no readable message list".format(manager_name))
                return orig(*args, **kwargs)

            app = ui.find_app()
            if app is None:
                write("WARN {}: app not found".format(manager_name))
                return orig(*args, **kwargs)

            # 107_fix_battle_flag_stuck が終了/ロード時の残骸を片付ける。
            # 401側ではその正常化済みフラグを戦闘中判定としてそのまま使う。
            if not bool(getattr(app, "in_battle", False)):
                note_other_battle_flags(app)
                return orig(*args, **kwargs)

            try:
                block, count = build_block(app)
                if not block:
                    write("WARN {}: no party-member context built".format(manager_name))
                    return orig(*args, **kwargs)

                replaced = append_block(message, block)
                if replaced is not message:
                    args, kwargs = replace_message(args, kwargs, replaced, where)
                    write("{}: appended {} character(s), {} chars".format(
                        manager_name, count, len(block)))
            except Exception:
                ctx.log_exc("battle character context: cannot build/append context")
                return orig(*args, **kwargs)

            return orig(*args, **kwargs)

    # send_request はプロバイダ初期化後に生える。
    # 後生え・別名対策はローダの共有部品へ任せる。
    llm.watch_aliases(
        ctx,
        list(BATTLE_SEND_TARGETS),
        install_send,
        label="battle character context",
    )

    ctx.log("battle character context v7: party-member-only context + base-style equipment text + separated attributes + unconditional saved NPC speech/style/equipment fallback + title cache clear + 107 battle flag gate armed + per-field and total prompt caps; log -> {}".format(
        ctx.out_path(LOG_BASENAME)))
