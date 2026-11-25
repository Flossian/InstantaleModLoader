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
送り口を包むのはローダの窓口 `llm.on_request` で、この MOD は送る messages の書き換えだけを渡す
（後生え・別名の対策も窓口が持つ。TECH.md §3.3.21・§3.4）。

戦闘中の判定は `107_fix_battle_flag_stuck` が正常化した `in_battle` だけ。
ボス戦の `in_boss_battle` も闘技場の `in_colosseum_battle` も `in_battle` と一緒に立つ（GAME.md §2.11「戦闘BGM」の表）。

## 何を載せるか

名前と HP、装備（weapon / wearable）、人物（profile・personality・speech_style・
job・tactics・traits・status）の順。
装備は本体がプレイヤーの装備を書くときの `名前(説明)` の形に寄せ、
内部属性（attributes）は別の行に分ける。`equipments` の値が id なら持ち物から実体を引く。

装備欄の MOD（`333_`）を入れていれば、ローダの窓口 `equipment.gear` に身に着けている品を聞き、
weapon / wearable の代わりに部位ごと（`right_hand` / `head` / `accessory1` など）の行で書く。
装備欄の品は持ち物の辞書に居ないので、`equipments` の id からは引けない（id だけの行になった）。
主人公も装備欄を使っていれば、装備の行だけの `- player:` の枠を足す（本体が渡すのは weapon / wearable の
2 つだけで、残りの部位は審判に見えていなかった）。窓口が None なら今までどおり。

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

from instantale_modloader import equipment, frames, items, llm, loads, ui
from instantale_modloader.state import process_store

#: 控えの置き場（`sys` の属性。注入をまたぐ）。
STATE_ATTR = "_instantale_battle_character_context"


#: 追記する塊の見出し。同じ message に2度足さないための印でもある（`append_block`）。
MARKER = "【パーティーメンバー戦闘情報】"
#: `out\` に置くこの MOD のログ。何人ぶん・何文字を足したかが1手ごとに1行残る。
LOG_BASENAME = "battle_character_context.log"

# referee がどちらの送り口を通るかは manager_name では決まらない
# （out/recon/targets.txt にはどちらも在る）。戦闘の部品の2つ（構造あり・なし）を見張り、
# 足すかどうかは BATTLE_MANAGERS と in_battle で決める。
# 送り口の文字列はローダの `llm.send_targets` が選ぶ（窓口が包む対象を MOD に書かない）。
BATTLE_SEND_MODULE = "llm_manager_battle"

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
    owner = ctx.mod_name or "401_battle_character_context"

    # 控え。タイトルへ戻るときに空にする。
    # `sys` に置いて注入をまたぐ（apply() の中に置くと、注入し直したときに次のロードまで補いが効かない。
    # TECH.md §3.4。`403_` と同じ）。置き方はローダの `state.process_store`。
    state = process_store(STATE_ATTR, {
        "saved_character": {},   # character_id -> ロード時の保存辞書の写し（runtime に欠ける項目の拠り所）
    })

    # ------------------------------------------------------------ 安全な読み取り

    # dict / object のどちらでも読む（ローダの `frames.field`。読めない番人は default に戻す）。
    value_of = frames.field

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

    # 持ち物の実体 `{item_id: item}`（ローダの `items.inventory_of`）。
    # 読むのは runtime の Character だけ（保存辞書の持ち物は `equipment_sources` が別に引く）。
    inventory_of = items.inventory_of

    def saved_character_of(character):
        """ロード時にゲーム自身が読んだNPC保存辞書。無ければNone。"""
        character_id = value_of(character, "id")
        if character_id in (None, ""):
            return None
        return state["saved_character"].get(str(character_id))

    def equipment_sources(character):
        """装備の参照元を3つ揃えて返す: (runtime装備, runtime持ち物, 保存持ち物)。

        新しい runtime 状態を優先し、ロード時の保存辞書の持ち物は fallback。
        保存辞書の装備は見ない（仲間の `equipments` はセーブでは常に `{}`。GAME.md §2.13.3）。
        辞書でなかったものは None にして、読む側の isinstance を減らす。
        """
        runtime_eq = value_of(character, "equipments")
        runtime_inv = inventory_of(character)
        saved = saved_character_of(character) or {}
        saved_inv = saved.get("inventory")
        return (
            runtime_eq if isinstance(runtime_eq, dict) else None,
            runtime_inv if isinstance(runtime_inv, dict) else None,
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
          - 333 や 402 が書いた後はアイテム id の文字列
          - Item の辞書がそのまま入っている場合
        オブジェクト／辞書ならそのまま `item_summary` へ。id 文字列なら持ち物から
        実体を引く。runtime の持ち物に無ければ保存辞書の持ち物も見る（そのときは
        どの経路で解決したかをログに1行残す）。どこにも無ければ id だけを載せて、
        「何かを装備している」ことだけは審判へ伝える。
        """
        runtime_eq, runtime_inv, saved_inv = equipment_sources(character)

        ref = runtime_eq.get(slot) if isinstance(runtime_eq, dict) else None
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
                if inv_source == "saved":
                    write("equipment fallback: character={!r} slot={} ref={} via {}+{}".format(
                        text_of(character, "name") or value_of(character, "id"),
                        slot, frames.short(ref, 120), "runtime", inv_source))
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

    def worn_lines(worn):
        """装備欄の答え `[(部位, 品), ...]` を `(項目名, 本文)` の並びにする。属性は本文の直後。"""
        rows = []
        for region, item in worn:
            main, attrs = item_summary(item)
            rows.append((region, main))
            rows.append((region + "_attributes", attrs))
        return rows

    def character_block(character, role, budget=BLOCK_TOTAL_CHARS, worn=None, gear_only=False):
        """1人ぶんを `budget` 文字までで組む。溢れた分は末尾の項目から落とす。

        並びがそのまま優先順位。名前とHPは必ず載せ、
        次に戦闘の材料である装備、その後に人物・口調の順で埋める。
        `worn` は装備欄の答え（`equipment.gear`）。あれば weapon / wearable の代わりに部位ごとに書く。
        `gear_only` は主人公の枠（名前と装備だけ。HP や人物は本体が渡している）。
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
        hp = hp_of(character) if not gear_only else None
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
        if worn is not None:
            rows = worn_lines(worn)
        else:
            weapon, weapon_attributes = equipment_summary(character, "weapon")
            wearable, wearable_attributes = equipment_summary(character, "wearable")
            rows = [("weapon", weapon), ("weapon_attributes", weapon_attributes),
                    ("wearable", wearable), ("wearable_attributes", wearable_attributes)]
        for label, text in rows:
            if not add(label, text):
                break
        if gear_only:
            return "\n".join(lines) if len(lines) > 1 else None

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
        """追記する本文と、載せた人数（主人公の装備の枠は数えない）を返す。

        人数はログ用。ここで返さないと、同行者の解決が1手につき2回走る。
        """
        members = current_party(app)
        # 主人公は装備欄を使っているときだけ、装備の行だけの枠を足す（`equipment.gear`）
        player = getattr(app, "player", None)
        player_worn = equipment.gear(app, player) if player is not None else None
        # 合計上限を人数で割って1人ぶんの予算にする。人数が多くて割った値が
        # MEMBER_MIN_CHARS を切るときは下限を優先する（合計は上限を超えるが、
        # 名前と HP だけの仲間を作るよりよい）。
        count = len(members) + (1 if player_worn else 0)
        budget = (
            max(MEMBER_MIN_CHARS, BLOCK_TOTAL_CHARS // count)
            if count else BLOCK_TOTAL_CHARS
        )

        player_block = (character_block(player, "player", budget, worn=player_worn, gear_only=True)
                        if player_worn else None)
        blocks = []
        for role, character in members:
            rendered = character_block(character, role, budget, worn=equipment.gear(app, character))
            if rendered:
                blocks.append(rendered)

        head = "\n\n" + MARKER + "\n"
        mine = ""
        if player_block:
            mine = ("player は主人公。装備欄に身に着けている品を部位ごとに載せる"
                    "（本体の weapon / wearable はこの中の一部）。\n" + player_block + "\n")

        # 仲間が居なくても見出しだけは足す。「情報が無い」と「MOD が動かなかった」を
        # プロンプトの側で区別できるようにするため。
        if not blocks:
            return head + mine + "パーティーメンバーなし", 0

        return (
            head + mine
            + "パーティーメンバーについては、以下の人物・装備情報を優先して参照してください。\n"
            + "\n".join(blocks)
        ), len(blocks)

    # ------------------------------------------------------------ 送り口の引数
    # 見張る2つの送り口はどちらも先頭が (manager_name, message, ...) で、
    # message は `[{"role": ..., "content": ...}, ...]` の list。
    # 引数を来た側（位置かキーワード）のまま読み書きするのは窓口 `llm.on_request`。

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
    def remember_saved_character(orig, self, *args, **kwargs):
        """ゲームがセーブから NPC を組み立てる入口で、渡された保存辞書を写しておく。

        `World.generate_character(character_id, character_value)` はロード時に
        NPC ごとに呼ばれ、`character_value` がセーブ上のその人物の辞書。
        runtime の Character には載らない項目（口調など）があるので、
        必要な欄だけを `state["saved_character"]` へ控え、`field_with_saved_fallback` /
        `equipment_sources` が runtime に無いときの拠り所にする。
        本体の処理は一切変えない（控えたあと、受け取った引数のまま `orig` を呼ぶ）。
        仲間の `equipments` はセーブでは常に `{}`（GAME.md §2.13.3）なので控えない。
        """
        try:
            character_id = frames.arg(args, kwargs, "character_id", 0)
            character_value = frames.arg(args, kwargs, "character_value", 1)
            if isinstance(character_value, dict) and character_id is not None:
                inventory = character_value.get("inventory")
                # speech_style 等だけを持つNPCもいるため、所持品の有無では絞らない。
                state["saved_character"][str(character_id)] = {
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
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ フック

    def clear_saved_character_cache(call):
        """タイトルへ戻るときに控えを全部捨てる。

        別のワールドや別のセーブをロードすると character_id が同じでも別人になり得る。
        古い保存辞書を fallback に使うと他所の人物像が混ざるので、境目で空にする。
        """
        state["saved_character"].clear()

    # タイトルへ戻る前はローダの窓口 `loads` の TITLE（TECH.md §3.3.21）。
    loads.on(owner, ctx, (loads.TITLE,), before=clear_saved_character_cache, write=write)

    def battle_request(req):
        """戦闘の審判の頼みに仲間の塊を足す（窓口 `llm.on_request` の書き換え）。

        流れ:
          1. manager_name が戦闘の審判でなければ来ない（窓口の `managers` で絞る。会話や生成には触らない）
          2. message の list が読めなければ WARN を残して素通し
          3. `app.in_battle` が立っていなければ素通し
          4. 仲間の塊を組み、最後の user message の末尾へ足した新しい list を返す
        失敗はどの段でも本体の呼び出しを止めない（None を返すと元の引数のまま送られる）。
        """
        manager_name = req.manager_name
        message = req.message
        if not isinstance(message, list):
            write("WARN {} has no readable message list".format(manager_name))
            return None

        app = ui.find_app()
        if app is None:
            write("WARN {}: app not found".format(manager_name))
            return None

        # 107_fix_battle_flag_stuck が終了/ロード時の残骸を片付ける。
        # 401側ではその正常化済みフラグを戦闘中判定としてそのまま使う。
        if not bool(getattr(app, "in_battle", False)):
            return None

        try:
            block, count = build_block(app)
            if not block:
                write("WARN {}: no party-member context built".format(manager_name))
                return None

            replaced = append_block(message, block)
            if replaced is message:
                return None
            write("{}: appended {} character(s), {} chars".format(
                manager_name, count, len(block)))
            return replaced
        except Exception:
            ctx.log_exc("battle character context: cannot build/append context")
            return None

    # send_request はプロバイダ初期化後に生える（apply() の時点では無いことがある）。
    # 包むのはローダの窓口 `llm.on_request` の1か所で、後生え・別名の対策も窓口が持つ。
    llm.on_request(owner, ctx, battle_request, managers=BATTLE_MANAGERS,
                   targets=llm.send_targets(BATTLE_SEND_MODULE), write=write)

    ctx.log("battle character context: installed (total {} chars, {} per member at least); log -> {}".format(
        BLOCK_TOTAL_CHARS, MEMBER_MIN_CHARS, ctx.out_path(LOG_BASENAME)))
