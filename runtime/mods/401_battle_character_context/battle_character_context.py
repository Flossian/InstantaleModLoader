# -*- coding: utf-8 -*-
"""戦闘の審判 LLM に、同行している仲間の人物と装備を本体の表記に寄せて添える。

素の戦闘の審判（`referee_*`）が読むのはプレイヤーの情報だけで、
仲間が居ても、どんな人物で何を装備しているかは審判が知らないまま手が裁かれる。
この MOD は審判への送り口を見張り、戦闘中だけ user message の末尾に
「【パーティーメンバー戦闘情報】」の塊を足す。
本体が組んだ prompt は消さず、プレイヤーの情報も本体に任せる。
戦闘の計算そのもの（攻撃力・防御力の加算）には触らない。

## どこに差すか

referee は `scripts.llm.llm_manager_battle` の `send_request` と
`send_request_with_no_structure` のどちらかを通る。
どの referee がどちらを通るかは manager_name では決まらないので両方を見張り、
足すかどうかは manager_name（`BATTLE_MANAGERS`）と `app.in_battle` で決める。
`send_request` はプロバイダ初期化後に生えるので、後生え・別名の対策は
ローダの `llm.watch_aliases` に任せる（TECH.md §3.4）。

戦闘中の判定は `107_fix_battle_flag_stuck` が正常化した `in_battle` だけ。
`in_boss_battle` / `in_colosseum_battle` は、`in_battle` が落ちているのに
立っていたら1度だけ記録する。判定を広げるかはその記録が出てから決める
（ボス戦では出ておらず、`in_battle` で拾えている）。

## 何を載せるか

名前と HP、装備（weapon / wearable）、人物（profile・personality・speech_style・
job・tactics・traits・status）の順。
装備は本体がプレイヤーの装備を書くときの `名前(説明)` の形に寄せ、
内部属性（attributes）は別の行に分ける。`equipments` の値が id なら持ち物から実体を引く。

referee は1手ごとに呼ばれるので、項目ごとの上限（`FIELD_CHARS`）と
同行者全員ぶんの合計（`BLOCK_TOTAL_CHARS`）を置く。
合計は人数で割って1人ぶんの予算にし、溢れた分は末尾の項目から落とす。
効きは追記時のログの文字数で数える。

## 保存辞書の控え

NPC の runtime の Character には口調などが載らないことがある。
ロード時に `World.generate_character(character_id, character_value)` へ渡される
保存辞書を NPC ごとに控え、runtime 側に欠ける項目だけそこから補う。
装備・持ち物の有無では絞らない。
タイトルへ戻るときに控えを捨て、別ワールド・別セーブへの持ち越しを防ぐ。
"""

from instantale_modloader import frames, llm, ui


#: 追記する塊の見出し。同じ message に2度足さないための印でもある（`append_block`）。
MARKER = "【パーティーメンバー戦闘情報】"
#: `out\` に置くこの MOD のログ。何人ぶん・何文字を足したかが1手ごとに1行残る。
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
# 装備品の名前。
ITEM_NAME_CHARS = 120
# 装備品の説明（`名前(説明)` の括弧の中）。
ITEM_DESCRIPTION_CHARS = 400
# 装備品の内部属性（`weapon_attributes` の行）。
ITEM_ATTRIBUTES_CHARS = 300
# 名前も説明も無い品を文字列表現のまま載せるときの上限。
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

    # apply() の外へ持ち出さない控え。どちらもタイトルへ戻るときに空にする。
    state = {
        "saved_character": {},   # character_id -> ロード時の保存辞書の写し（runtime に欠ける項目の拠り所）
        "noted_flags": set(),    # `in_battle` 以外の戦闘フラグを記録済みか（同じ NOTE を毎手書かない）
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
        """「値が入っている」の判定。None と空の入れ物だけを不在とみなす（0 や False は在る）。"""
        return value not in (None, "", [], {}, ())

    def inventory_of(character):
        """持ち物の実体 `{item_id: item}`。312_shop_restock と同じ読み方。

        runtime の Character では `inventory` が Inventory オブジェクトで、
        その中の `.inventory` が辞書。保存辞書（ロード時の写し）では
        `inventory` がそのまま辞書。どちらの形でも辞書を返す。
        """
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
        """装備の参照元を4つ揃えて返す: (runtime装備, runtime持ち物, 保存装備, 保存持ち物)。

        新しい runtime 状態を優先し、ロード時の保存辞書は fallback。
        辞書でなかったものは None にして、読む側の isinstance を減らす。
        """
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
        """アイテム1つを (本文, 属性) の2本に分ける。

        本文は本体がプレイヤーの装備を書くときの `名前(説明)` の形に寄せる
        （審判 LLM が既に読み慣れている表記）。`attributes`（攻撃力などの内部値）は
        本文に混ぜず `weapon_attributes` として別行にする。名前も説明も無い品は
        オブジェクトの文字列表現をそのまま短く載せる（何も出さないよりは材料になる）。
        `item` は runtime の Item でも保存辞書でもよい（`text_of` / `value_of` が吸収）。
        """
        if item is None:
            return None, None

        name = text_of(item, "name") or text_of(item, "item_name")
        description = text_of(item, "description")
        attributes = value_of(item, "attributes")

        if name and description:
            main = "{}({})".format(frames.short(name, ITEM_NAME_CHARS),
                                   frames.short(description, ITEM_DESCRIPTION_CHARS))
        elif name:
            main = frames.short(name, ITEM_NAME_CHARS)
        elif description:
            main = frames.short(description, ITEM_DESCRIPTION_CHARS)
        else:
            main = frames.short(item, ITEM_FALLBACK_CHARS)

        attrs = frames.short(attributes, ITEM_ATTRIBUTES_CHARS) if present(attributes) else None
        return main, attrs

    def equipment_summary(character, slot):
        """`slot`（"weapon" / "wearable"）に装備している品を (本文, 属性) で返す。無ければ (None, None)。

        `equipments[slot]` の値は場面で形が違う:
          - runtime では Item オブジェクトそのもの（402 が装備を書き換えた直後など）
          - セーブから読んだ直後や保存辞書ではアイテム id の文字列
          - 保存辞書の中に Item の辞書がそのまま入っている場合
        オブジェクト／辞書ならそのまま `item_summary` へ。id 文字列なら持ち物から
        実体を引く。runtime の持ち物に無ければ保存辞書の持ち物も見る（そのときは
        どの経路で解決したかをログに1行残す）。どこにも無ければ id だけを載せて、
        「何かを装備している」ことだけは審判へ伝える。
        """
        runtime_eq, runtime_inv, saved_eq, saved_inv = equipment_sources(character)

        # 参照はまず runtime。空なら保存辞書。どちらから取ったかは
        # fallback のログのために覚えておく。
        ref = runtime_eq.get(slot) if isinstance(runtime_eq, dict) else None
        source = "runtime"
        if not present(ref) and isinstance(saved_eq, dict):
            ref = saved_eq.get(slot)
            if present(ref):
                source = "saved"

        if not present(ref):
            return None, None

        # 参照が実体（辞書 or 名前を持つオブジェクト）ならそのまま要約できる。
        if isinstance(ref, dict):
            return item_summary(ref)
        if not isinstance(ref, (str, bytes, int, float, bool)):
            if text_of(ref, "name") or text_of(ref, "item_name"):
                return item_summary(ref)

        # ここへ来るのは参照が id（文字列など）のとき。持ち物から実体を探す。
        inventories = []
        if isinstance(runtime_inv, dict):
            inventories.append(("runtime", runtime_inv))
        if isinstance(saved_inv, dict) and saved_inv is not runtime_inv:
            inventories.append(("saved", saved_inv))

        for inv_source, inventory in inventories:
            # 鍵の型が揃っていない場合に備え、そのまま と str() の両方で引く。
            item = inventory.get(ref)
            if item is None:
                item = inventory.get(str(ref))
            if item is not None:
                if source == "saved" or inv_source == "saved":
                    write("equipment fallback: character={!r} slot={} ref={} via {}+{}".format(
                        text_of(character, "name") or value_of(character, "id"),
                        slot, frames.short(ref, 120), source, inv_source))
                return item_summary(item)

        return "item_id=" + frames.short(ref, 200), None


    # ------------------------------------------------------------ 同行NPC1人ぶんの本文

    def hp_of(character):
        """HP を `現在/最大` の文字列で返す。読める項目が無ければ None。

        runtime の Character は `current_hp` / `max_hp`（無ければ `original_max_hp`）、
        dict 化済みのデータは `"HP": "132/132"` の形で持つことがあるので両方を受ける。
        """
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

        # 先頭行は「- party_member: 名前」。以降は2字下げの「  項目: 値」が続く。
        lines = ["- {}: {}".format(role, frames.short(name, 300))]

        # 名前と HP は上限に関係なく載せる。
        hp = hp_of(character)
        if hp:
            lines.append("  HP: " + frames.short(hp, 100))

        def add(label, text):
            """入るなら足す。入らなければ False（呼び側はそこで打ち切る）。

            `used` は改行ぶんも含めた今の文字数。1項目でも溢れたら以後の項目は
            見ない（並びが優先順位なので、後ろの項目を詰めて入れる意味は無い）。
            """
            if not text:
                return True
            line = "  {}: {}".format(label, text)
            used = sum(len(x) + 1 for x in lines)
            if used + len(line) + 1 > budget:
                return False
            lines.append(line)
            return True

        # 装備は戦闘の材料なので人物より先。属性は本文の直後に置く。
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
        # runtime に無い項目は保存辞書から補う（NPC は speech_style などが
        # Character に載らないことがある。方針の fallback がこれ）。
        for attr, limit in FIELD_CHARS:
            value = field_with_saved_fallback(character, attr)
            if not present(value):
                continue
            if not add(attr, frames.short(value, limit)):
                break

        return "\n".join(lines)

    def current_party(app):
        """ローダ共通APIだけで現在の同行NPCを引く。プレイヤーは本体情報に任せる。

        返すのは `[("party_member", Character), ...]`。役割名は今のところ1種だけで、
        `character_block` の先頭行の見出しになる。同じ実体が2つの id から引けても
        1度しか載せない（`seen` は id() で見る。Character は hash を持つとは限らない）。
        id から実体を引けなかった仲間は WARN を残して飛ばす。
        """
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
        # 合計上限を人数で割って1人ぶんの予算にする。人数が多くて割った値が
        # MEMBER_MIN_CHARS を切るときは下限を優先する（合計は上限を超えるが、
        # 名前と HP だけの仲間を作るよりよい）。
        budget = (
            max(MEMBER_MIN_CHARS, BLOCK_TOTAL_CHARS // len(members))
            if members else BLOCK_TOTAL_CHARS
        )

        blocks = []
        for role, character in members:
            rendered = character_block(character, role, budget)
            if rendered:
                blocks.append(rendered)

        # 仲間が居なくても見出しだけは足す。「情報が無い」と「MOD が動かなかった」を
        # プロンプトの側で区別できるようにするため。
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

    # ------------------------------------------------------------ 送り口の引数
    # 見張る2つの送り口はどちらも先頭が (manager_name, message, ...) で、
    # message は `[{"role": ..., "content": ...}, ...]` の list。
    # 呼び側が位置引数で渡すか keyword で渡すかは決め打ちできないので、
    # 読むときにどちらだったかを控え、書き戻すときに同じ形へ戻す。

    def manager_name_of(args, kwargs):
        """第1引数の manager_name。位置でも keyword でも読む。"""
        if args:
            return args[0]
        return kwargs.get("manager_name")

    def message_of(args, kwargs):
        """message の list と、それが "args" / "kwargs" のどちらに在ったかを返す。"""
        if len(args) >= 2 and isinstance(args[1], list):
            return args[1], "args"
        message = kwargs.get("message")
        if isinstance(message, list):
            return message, "kwargs"
        return None, None

    def replace_message(args, kwargs, message, where):
        """`message_of` が見つけた位置へ差し替え後の message を戻す。元の tuple / dict は触らない。"""
        if where == "args":
            return args[:1] + (message,) + args[2:], kwargs
        if where == "kwargs":
            new_kwargs = dict(kwargs)
            new_kwargs["message"] = message
            return args, new_kwargs
        return args, kwargs

    def append_block(message, block):
        """最後の user message に追記。呼び出し元のlist/dictは壊さない。

        戻り値が引数の `message` と同一なら「足さなかった」の意味
        （呼び側は `is` で見て、書き戻しとログを省く）。
        """
        if not block:
            return message

        # 既に MARKER が入っていれば2度足さない。同じ message が
        # 再送（リトライ）で通る場合や、2つの送り口が同じ list を回す場合の保険。
        for item in message:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str) and MARKER in content:
                return message

        rewritten = list(message)

        # 末尾に近い user message ほど「今の手」の指示。そこへ足すと
        # 審判がプレイヤー情報の直後に仲間の情報を読む。
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
        """ゲームがセーブから NPC を組み立てる入口で、渡された保存辞書を写しておく。

        `World.generate_character(character_id, character_value)` はロード時に
        NPC ごとに呼ばれ、`character_value` がセーブ上のその人物の辞書。
        runtime の Character には載らない項目（口調など）があるので、
        必要な欄だけを `state["saved_character"]` へ控え、`field_with_saved_fallback` /
        `equipment_sources` が runtime に無いときの拠り所にする。
        本体の処理は一切変えない（控えたあと `orig` をそのまま呼ぶ）。
        """
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
        """タイトルへ戻るときに控えを全部捨てる。

        別のワールドや別のセーブをロードすると character_id が同じでも別人になり得る。
        古い保存辞書を fallback に使うと他所の人物像が混ざるので、境目で空にする。
        NOTE の印も一緒に戻し、次の遊びでまた1度だけ記録できるようにする。
        """
        state["saved_character"].clear()
        state["noted_flags"].clear()
        return orig(self, *args, **kwargs)

    def install_send(target):
        """送り口1つに包みを掛ける。`llm.watch_aliases` が対象ごとに呼ぶ。

        包みの中の流れ:
          1. manager_name が戦闘の審判でなければ素通し（会話や生成には触らない）
          2. message の list が読めなければ WARN を残して素通し
          3. `app.in_battle` が立っていなければ素通し
             （他の戦闘フラグだけ立っていたら NOTE を1度だけ残す）
          4. 仲間の塊を組み、最後の user message の末尾へ足して本体へ渡す
        失敗はどの段でも本体の呼び出しを止めない（元の引数で `orig` を呼ぶ）。
        """
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

    # send_request はプロバイダ初期化後に生える（apply() の時点では無いことがある）。
    # 後生え・別名（他 MOD が包んだ後の関数名違い）の対策はローダの共有部品へ任せる。
    # 対象が現れた時点で `install_send(target)` が呼ばれ、包みが掛かる。
    llm.watch_aliases(
        ctx,
        list(BATTLE_SEND_TARGETS),
        install_send,
        label="battle character context",
    )

    ctx.log("battle character context: installed (total {} chars, {} per member at least); log -> {}".format(
        BLOCK_TOTAL_CHARS, MEMBER_MIN_CHARS, ctx.out_path(LOG_BASENAME)))
