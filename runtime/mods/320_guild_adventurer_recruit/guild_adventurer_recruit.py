# -*- coding: utf-8 -*-
"""ギルドの「冒険者達と話す」で冒険者が減っていたら、補充のボタンを出す。

素のゲームは冒険者をエリアの初回生成で3人作るきりで、補充しない
（GAME.md §2.23。実測でも自然な補充は観測されていない）。
雇う・死ぬで減った町のギルドは、以後ずっと閑散としたまま。
このMODは一覧に「冒険者を募集する」を足し、
押されたら新しい冒険者をLLMで書いてギルドに入れる。

##### 画面の見分け（armed の旗）

冒険者一覧には固有の目印が無い。
並ぶのは `ConversationStartManager` × 人数 + `JustSetButtonToNormalPhase`
（やめる）だけで、これは「会話する」（`DisplayTalkChoice`）の一覧と同じ形
（実測: `out/bounty_hunter.jsonl` と `out/city_case.log` の画面ダンプ）。
spec だけでは区別できないので、`DisplayAdventurerTalkChoice.execute` を
包んで旗を立て、`DisplayTalkChoice.execute` と
「別の画面の目印が見えたとき」に下ろす。
旗と形の両方が揃ったときだけ一覧とみなす（GAME.md §2.5 の
「既知以外を既定側に倒さない」）。

##### 数えるのは画面に並んだ人数

`Area.adventurer_npcs` は刈り込まれない（死亡もパーティ加入中も
入ったまま。実測）。素の `len()` は「いま会話できる人数」ではないので、
死亡とパーティを自分で引き直す代わりに、
**ゲーム自身が並べたボタンの数**を数える。
表示の判断はプレイヤーが見ているものと必ず一致する。

##### 生成と登録

中身（名前・人物像）は `llm.ask` の構造化出力で書く。
強さは土地の水準に合わせる: その土地の冒険者の
`config['difficulty_level']`（死んでいても記録は残っているので拾える）と、
土地の依頼の難易度（`318_` が育てた値もここに現れる）の最大値。
`get_npc_employ_price` の定義域が 0..76 なので上限で刈る
（`101_` が踏んだ `KeyError: 80` を作らないため）。

世界への入れ方はローダの `npcs.make_npc`（素データ・組み立て・施設への
配置まで。`902_` が実測で確立した手順）。
`level_of_detail` は 1 にして、HP・スキルは最初の会話の直前に
ゲーム自身の `ensure_npc_detail_generated` に埋めさせる。
そのうえで `Area.adventurer_npcs`（実行時とセーブ側の両方）へ id を足す。
一覧がどちらの名簿から組まれるかは未確定なので、両方に書いて
ログに残す（確認手順は VERIFICATION.md §3.39）。

##### 押された後の流れ

`process_choice` に自前のフェーズを載せ、`execute`（ゲームの
ワーカースレッド）の中でLLMを同期で待ちきる。
別スレッドへ投げないのは `301_` と同じ理由 ― 即座に戻ると
ゲームは行動が終わったと判断し、生成中にプレイヤーが動けてしまう。
終わったら一覧をゲーム自身の `DisplayAdventurerTalkChoice` で開き直す。
新しい冒険者もゲームが正しい spec で並べてくれる。

作った冒険者はゲーム自身の `npcs` 項目としてセーブに残る。
MODを外しても消えない（DOC.md に明記）。
増えすぎない根拠はボタンの出る条件そのもの ―
一覧が RECRUIT_MIN 人以下のときしか募集できない。
"""

import sys

from instantale_modloader import frames, llm, ui
from instantale_modloader.npcs import make_npc

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
RECRUIT_MIN = 1     # 一覧がこの人数以下なら募集ボタンを出す
RECRUIT_COUNT = 1   # 1回の募集で作る人数（1人ごとにLLM生成1回）

# ---- 設定にしない定数 ----------------------------------------------------
RECRUIT_LABEL = "冒険者を募集する"
LIST_TEXT = "冒険者達と話す"        # 開き直しの choice_text。判定には使わない
MARK = "mod_recruit_action"         # ボタンの印。MODごとに別の文字列（ui.MARK_PREFIX）
MANAGER_NAME = "mod_adventurer_recruit"   # LLM記録の分かれ目（output_data/）
RECRUIT_TIMEOUT = 90.0              # LLM 1問の待ち。無期限にしない（GAME.md §2.12）
RECRUIT_MAX_TOKENS = 800
DIFFICULTY_FALLBACK = 4             # 土地の水準が読めないときの難易度（902_ と同じ）
DIFFICULTY_MAX = 76                 # get_npc_employ_price の定義域（VERIFICATION_LOG.md §2.2）
NAME_MAX = 24
TAKEN_NAMES_MAX = 15                # プロンプトに並べる既存名の上限

#: 冒険者一覧ではありえない spec。
#: 1つでも見えたら別の画面に移っている（armed を下ろす）。
OTHER_SCREEN_SPECS = ("ConversationEndManager", "MovePhaseManager",
                      "DisplayTalkChoice", "DisplayQuestChoice",
                      "QuestChoiceManager")

#: 実セーブに出てくる `category` の語彙（立ち絵の生成に使われる英語の句）。
#: これ以外を返されたら先頭の候補へ倒す。
CATEGORIES = ("young man", "young woman", "teenage boy", "teenage girl",
              "middle-aged man", "middle-aged woman", "old man", "old woman")

PROMPT = (
    "あなたはRPGの世界に新しく登場する冒険者NPCを1人考える係です。\n"
    "舞台となる土地: {area}\n"
    "土地の様子: {notes}\n"
    "この土地の冒険者の強さの目安: 難易度{difficulty}（0〜76。大きいほど強い）\n"
    "既にいる人物（名前をかぶらせない）: {taken}\n"
    "\n"
    "次の項目を考えてください。\n"
    "- name: 日本語。通り名と名前を合わせた短い呼び名（例の形式:「重装のハンス」）\n"
    "- profile: 日本語。経歴と今ギルドに居る理由。2〜3文\n"
    "- personality: 日本語。性格。1〜2文\n"
    "- speech_style: 日本語。口調の特徴。1文\n"
    "- look_description: 日本語。見た目。1〜2文\n"
    "- category: 英語。次のどれか1つ: {categories}\n"
    "- look: 英語。外見を表す短い句を5つ、カンマ区切り。"
    "1つ目は category と同じ語にする（例: young man, scarred face, "
    "heavy armor, huge axe, battle-worn）\n"
)


def apply(ctx):
    write = ctx.logger("adventurer_recruit.log")
    state = {"armed": False, "recruiting": False}
    screen = ui.Screen(ctx, write, tag="adventurer recruit", mark=MARK)

    # ================================================== 冒険者一覧の見分け
    @ctx.wrap("__main__:DisplayAdventurerTalkChoice.execute",
              required=False, safe=True)
    def adventurer_list_execute(orig, self, choice_text, *args, **kwargs):
        state["armed"] = True
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:DisplayTalkChoice.execute", required=False, safe=True)
    def talk_list_execute(orig, self, choice_text, *args, **kwargs):
        # 「会話する」の一覧は冒険者一覧と同じ形なので、明示的に旗を下ろす。
        state["armed"] = False
        return orig(self, choice_text, *args, **kwargs)

    def back_button_index(buttons):
        """ゲーム側の「やめる」の位置。無ければ None（＝一覧ではない）。

        無害 spec（`JustSetButtonToNormalPhase`）で、どのMODの印も
        付いていないもの。自前のボタンも同じ spec を使うので、印で除く。
        """
        for index, entry in enumerate(buttons):
            if (ui.spec_cls_name(entry) == ui.SAFE_CLS
                    and not screen.marked_by_a_mod(entry)):
                return index
        return None

    def offer(app, buttons):
        """一覧なら、人数が閾値以下のとき募集ボタンを差す。"""
        if not state["armed"]:
            return
        names = [ui.spec_cls_name(entry) for entry in buttons]
        if any(name in OTHER_SCREEN_SPECS for name in names):
            state["armed"] = False
            return
        if any(screen.mark_of(entry) is not None for entry in buttons):
            return                      # もう差してある
        at = back_button_index(buttons)
        if at is None:
            return                      # 「やめる」が無い＝一覧が組み上がっていない
        count = names.count("ConversationStartManager")
        if count > RECRUIT_MIN or state["recruiting"]:
            return
        entry = screen.button(RECRUIT_LABEL, mark="recruit")
        if entry is None:
            return
        buttons.insert(at, entry)
        write("offered {!r} (adventurers listed: {})".format(
            RECRUIT_LABEL, count))

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list):
                # 印を失った残骸（セーブから復元された自前ボタン）を先に落とす。
                screen.prune_stale(buttons, [RECRUIT_LABEL])
                offer(self, buttons)
        except Exception:
            ctx.log_exc("adventurer recruit: cannot offer the button")
        return orig(self, reset_page, *args, **kwargs)

    # ================================================== 押されたら募集する
    class RecruitPhase(object):
        """自前のフェーズ。`process_choice` に渡してゲームと同じ経路を通す。

        `PhaseSpec` には載せない（セーブに焼かれて、MOD無しの次回起動で
        `getattr(__main__, ...)` が失敗する。GAME.md §2.2）。
        """

        def __init__(self, app):
            self.app = app

        def execute(self, choice_text):
            return recruit(self.app)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        if state["recruiting"]:
            return None                 # 連打。生成中は無反応でよい
        write("pressed {!r}".format(entry.get("text")))
        screen.start_phase(self, RecruitPhase(self),
                           entry.get("text") or RECRUIT_LABEL,
                           fallback=lambda: recruit(self))
        return None

    # ================================================== 土地の水準を読む
    def difficulty_for(app, area):
        """新しい冒険者の難易度。土地の水準の最大値、読めなければ既定。

        冒険者の記録は死んでいても `Area.adventurer_npcs` に残っているので
        拾える。依頼の難易度も見るのは、`318_` が育てた土地では
        依頼のほうが今の水準を映しているから。
        """
        found = []
        roster = frames.attr(area, "adventurer_npcs", None)
        for member in (roster if isinstance(roster, (list, tuple)) else []):
            config = frames.attr(
                ui.character_of(app, ui.element_id(member)), "config", None)
            value = config.get("difficulty_level") if isinstance(config, dict) else None
            if isinstance(value, int) and not isinstance(value, bool):
                found.append(value)
        quest_ids = frames.attr(area, "quests", None)
        stores = ui.quest_stores(app)
        for quest_id in (quest_ids if isinstance(quest_ids, (list, tuple)) else []):
            for store in stores:
                quest = store.get(str(quest_id), store.get(quest_id))
                if quest is None:
                    continue
                value = ui.quest_value(quest, "difficulty")
                if isinstance(value, int) and not isinstance(value, bool):
                    found.append(value)
                break
        target = max(found) if found else DIFFICULTY_FALLBACK
        return min(max(target, 0), DIFFICULTY_MAX)

    def guild_of(area):
        """そのエリアのギルド。`(施設id, 施設)`。無ければ `(None, None)`。"""
        for node in ui.nodes_of(area):
            for key, facility in ui.facilities_of(node).items():
                if ui.facility_type_of(facility) == ui.GUILD_FACILITY_TYPE:
                    return str(key), facility
        return None, None

    def taken_names(app, area, made):
        """名前をかぶらせないための既存名。土地の冒険者 + 今回作った分。"""
        names = []
        roster = frames.attr(area, "adventurer_npcs", None)
        for member in (roster if isinstance(roster, (list, tuple)) else []):
            name = getattr(ui.character_of(app, ui.element_id(member)),
                           "name", None)
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        names.extend(name for _npc_id, name in made)
        return names[-TAKEN_NAMES_MAX:]

    # ================================================== LLMに1人書かせる
    def clean_text(value, limit=400):
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())[:limit]

    def clean_name(value):
        """名前はそのままファイルパスになる（GAME.md §2.15）。危ない字は落とす。"""
        name = clean_text(value, NAME_MAX * 2)
        for bad in "\\/:*?\"<>|":
            name = name.replace(bad, "")
        return name.strip()[:NAME_MAX]

    def area_notes(area):
        """土地の説明。`descriptions` は dict（overview 等）にも文字列にもなりうる。"""
        notes = frames.attr(area, "descriptions", None)
        if isinstance(notes, dict):
            for key in ("overview", "area_description", "facilities"):
                value = notes.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return notes if isinstance(notes, str) else ""

    def clean_category(value):
        """語彙の中の category に均す。読めなければ先頭の候補。"""
        text = clean_text(value, 40).lower().strip()
        for known in CATEGORIES:
            if known in text:
                return known
        return CATEGORIES[0]

    def clean_look(value, category):
        """カンマ区切りの英語トークンをリストにする。先頭は category。"""
        tokens = []
        for token in clean_text(value, 240).split(","):
            token = token.strip()
            if token and token.lower() != category:
                tokens.append(token)
        return [category] + tokens[:7]

    def exp_level_for(difficulty):
        """経験値レベル。ゲーム自身の対応表で引く（呼べなければ近似）。

        実セーブの生成直後の個体は experience_level が整数で入っている
        （難易度48 → 51。GAME.md §2.23）。None のままにしない。
        """
        functions = sys.modules.get("scripts.functions")
        table = getattr(functions, "get_npc_exp_level", None)
        if callable(table):
            try:
                level = table(difficulty)
                if isinstance(level, int) and not isinstance(level, bool):
                    return min(100, max(1, level))
            except Exception:
                ctx.log_exc("adventurer recruit: get_npc_exp_level failed")
        # 実測の対（0→7 / 20→24 / 48→51 / 60→69）に沿う近似。
        return min(100, max(1, difficulty + 5))

    def compose(app, area, difficulty, made):
        """冒険者1人ぶんの項目。書けなければ None。"""
        structure = llm.create_structure(
            ctx, "ModNewAdventurer",
            {"name": (str, ...), "profile": (str, ...),
             "personality": (str, ...), "speech_style": (str, ...),
             "look_description": (str, ...), "category": (str, ...),
             "look": (str, ...)},
            label="adventurer recruit")
        if structure is None:
            write("生成の部品が無い（send_request / create_model）。募集できない")
            return None
        taken = taken_names(app, area, made)
        prompt = PROMPT.format(
            area=clean_text(getattr(area, "name", None), 60) or "名も無い土地",
            notes=clean_text(area_notes(area), 240) or "（記録なし）",
            difficulty=difficulty,
            taken="、".join(taken) if taken else "（いない）",
            categories=" / ".join(CATEGORIES))
        data = llm.ask(ctx, MANAGER_NAME,
                       [{"role": "user", "content": prompt}],
                       timeout=RECRUIT_TIMEOUT, structure=structure,
                       max_tokens=RECRUIT_MAX_TOKENS,
                       label="adventurer recruit", write=write)
        if not isinstance(data, dict):
            return None
        name = clean_name(data.get("name"))
        if not name:
            write("compose: no usable name in {!r}".format(data))
            return None
        category = clean_category(data.get("category"))
        return {"name": name,
                "profile": clean_text(data.get("profile")),
                "personality": clean_text(data.get("personality")),
                "speech_style": clean_text(data.get("speech_style"), 120),
                "look_description": clean_text(data.get("look_description")),
                "category": category,
                "look": clean_look(data.get("look"), category),
                "job": "adventure",
                # 実セーブの生成直後の個体に合わせる（GAME.md §2.23）。
                # age は全NPCが 20 の定数。関係値は「初対面」の初期値。
                "age": 20,
                "experience_level": exp_level_for(difficulty),
                "relationship": {"player": {
                    "affinity": 0, "affinity_text": "警戒心がある",
                    "relationship": ["初対面"], "conversation_count": 0}}}

    # ================================================== 名簿へ入れる
    def enroll(app, area, area_id, npc_id):
        """`Area.adventurer_npcs` へ足す。実行時とセーブ側の両方。

        セーブの形＝実行時の形ではない（GAME.md §2.7）ので、
        素データ側の同名リストにも心当たりを全部見て書く。
        """
        wrote = []
        roster = frames.attr(area, "adventurer_npcs", None)
        if isinstance(roster, list) and npc_id not in roster:
            roster.append(npc_id)
            wrote.append("area")
        for label, root in (("world_dict", getattr(app, "world_dict", None)),
                            ("save_data_dict", getattr(app, "save_data_dict", None))):
            if not isinstance(root, dict):
                continue
            holders = [root]
            inner = root.get("world_data")
            if isinstance(inner, dict):
                holders.append(inner)
            for holder in holders:
                areas = holder.get("areas")
                entry = areas.get(area_id) if isinstance(areas, dict) else None
                raw = entry.get("adventurer_npcs") if isinstance(entry, dict) else None
                if isinstance(raw, list) and raw is not roster and npc_id not in raw:
                    raw.append(npc_id)
                    wrote.append(label)
        write("enroll: {} -> adventurer_npcs of area {} via {}".format(
            npc_id, area_id, wrote or "nothing (roster not found)"))
        return bool(wrote)

    # ================================================== 募集の本体
    def recruit_all(app):
        area = ui.current_area(app)
        if area is None:
            write("recruit: current area unknown")
            return []
        area_id = ui.area_id_of(area)
        guild_id, _guild = guild_of(area)
        if guild_id is None:
            write("recruit: no guild in area {}".format(area_id))
            return []
        difficulty = difficulty_for(app, area)
        write("recruit: area={} guild={} difficulty={} count={}".format(
            area_id, guild_id, difficulty, RECRUIT_COUNT))
        made = []
        for _index in range(max(1, RECRUIT_COUNT)):
            fields = compose(app, area, difficulty, made)
            if fields is None:
                break
            npc_id = make_npc(app, fields, area_id, guild_id,
                              config={"level_of_detail": 1,
                                      "difficulty_level": difficulty},
                              write=write)
            if npc_id is None:
                break
            if made and npc_id == made[-1][0]:
                # 同じ id が返った＝前の1人を上書きしている。ここで止める
                # （連続採番が進まない事故は `902_` の実機記録にある）。
                write("recruit: {} was returned twice; stopping".format(npc_id))
                break
            enroll(app, area, area_id, npc_id)
            made.append((npc_id, fields["name"]))
        return made

    def reopen_list(app):
        """一覧をゲーム自身に開き直させる。新しい冒険者も正しい spec で並ぶ。"""
        cls = ui.cls_of("DisplayAdventurerTalkChoice")
        if cls is None:
            write("reopen: DisplayAdventurerTalkChoice not found")
            return
        try:
            manager = cls(app)
        except Exception:
            ctx.log_exc("adventurer recruit: cannot rebuild the list")
            return
        screen.start_phase(app, manager, LIST_TEXT)

    def settle(app, then):
        """LLMを待った後の後始末をメインスレッドで。手が空くのを待つ。"""
        screen.when_idle(app, then, proceed_on_timeout=True,
                         tag="recruit settle")

    def finish(app, made):
        if not made:
            screen.busy_off(app)
            settle(app, lambda: screen.say(
                app, "募集をかけたが、応じる者は現れなかった。"))
            return
        names = "、".join("「{}」".format(name) for _npc_id, name in made)
        write("recruited: {}".format(made))
        screen.busy_off(app, restore=False)

        def announce():
            screen.say(app, "{}がギルドにやって来た。".format(names))
            reopen_list(app)

        settle(app, announce)

    def recruit(app):
        """`execute`（ワーカースレッド）の中で同期でやりきる。"""
        if state["recruiting"]:
            return
        state["recruiting"] = True
        screen.busy_on(app)
        try:
            made = recruit_all(app)
        except Exception:
            ctx.log_exc("adventurer recruit: recruiting failed")
            made = []
        state["recruiting"] = False
        finish(app, made)

    ctx.log("adventurer recruit: installed (min={}, count={})".format(
        RECRUIT_MIN, RECRUIT_COUNT))
