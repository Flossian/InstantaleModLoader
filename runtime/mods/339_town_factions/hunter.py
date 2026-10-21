# -*- coding: utf-8 -*-
"""勢力の刺客。主人公を敵とみなす勢力の街を歩いていると、その勢力の者が呼び止めて勝負を挑む。

仕様は DOC.md「勢力の刺客」、決めたことと根拠は VERIFICATION.md §3.98「勢力の刺客」。

- 現れるのは、見られ方が敵対（設定で緊張も）の勢力が治める街に着いたとき。確率と、次に現れるまでの日数を設定で決める
- 刺客は新しく作る（`336_` の同房の囚人と同じ形。LLM に人物を書かせ、`npcs.make_npc` で世界に入れ、
  `generate_npc_detail` で HP・スキル・立ち絵を埋める。埋めないまま戦闘に出すと落ちる。GAME.md §2.23）。
  役職は勢力の種類と説明から LLM が決める
- 作るのは移動の処理の中で、作る間は待機表示を出す（`336_` の同房の囚人が刑の始まりの中で作るのと同じ）。
  背景の仕事で作ると、ゲームの保存が人物の一覧を舐めている間に人物が増えうる。
  できたら手が空くのを待って呼び止める（`300_` と同じ、ゲーム本来の会話の始め方。到着の取り合いはローダの `arrivals`）。
  勢力の街を出た・会話中・施設の画面でない（衛兵に見つかった）ときは、次にその勢力の街に着いたときに呼び止める
- 会話の「会話を終了する」は文言を「受けて立つ」に変え、ほかのボタンは外す。押すと会話をゲーム自身の経路で閉じてから、
  その刺客との戦闘（`BattleStartManager(app, 'specific', [id])`。ゲームが自由行動で特定の相手と戦うときと同じ形）に入る
- 勝ったら刺客は死ぬ（`config['is_dead']`。GAME.md §2.22）。その勢力からの見られ方と、その街の手配度が下がる。
  逃げたら刺客は生きていて、次に同じ勢力の街に着いたときにまた現れる。負けたらゲームオーバー（ゲームのまま）。
  ゲームは依頼の外の戦闘を衛兵との戦闘として扱うので、手配度と戦闘の締めの語りをここで直す（GAME.md §2.20「特定の人物との戦闘」）
"""
import random
import sys
import time

from instantale_modloader import arrivals, choices, llm, modfacility, npcs, ui

ARRIVAL_PRIORITY = 20           # 300（0）・911（10）より先に呼び止める
FIGHT_LABEL = "受けて立つ"
FIGHT_CHOICE = "{name}の挑戦を受けて立つ"
END_TEXT = "<行動: {name}の挑戦を受けて立ち、戦いになった>"
APPEAR_TEXT = "{faction}の{title}、{name}が行く手を塞いだ。"
WIN_TEXT = "{faction}の{title}を討ったことは、{faction}の者たちにもすぐに伝わるだろう。"
OPENING = ("<状況: {player}が{town}にいると、あなたが行く手を塞いだ。"
           "あなたは{faction}の{title}として、{faction}の敵である{player}を討ちに来た。{close}>")
OPENING_FIRST = "名乗りと共に勝負を挑む第一声を述べよ"
#: 前に戦って逃げられた刺客（同じ人物がまた現れる）。
OPENING_AGAIN = "あなたは以前にも{player}に勝負を挑んだが、逃げられている。再び相まみえたことを告げ、勝負を挑む第一声を述べよ"
#: 戦闘の締めの頼み（`guard_battle_summarizer`）は、街での戦闘を衛兵との戦闘と決め打ちしている（実機の頼み）。
#: 刺客との戦闘の間だけ、決め打ちの2文を差し替える。
SUMMARY_LINES = (
    ("プレイヤーキャラはこのエリアで犯罪者として指名手配されている。", "プレイヤーキャラは{faction}から敵とみなされている。"),
    ("先ほど衛兵に発見されて戦闘が始まり、そして終了した。",
     "先ほど{faction}の{title}である{name}に勝負を挑まれ、受けて立って戦闘が始まり、そして終了した。"))
NOTE = ("【刺客】あなたは{faction}の{title}。{faction}の命を受け、{faction}の敵である{player}を討つために来た。"
        "言葉で引き下がることはなく、話が済めば力ずくで決着をつけるつもりでいる。")
MANAGER_NAME = "mod_town_factions_hunter"
COMPOSE_TIMEOUT = 90.0
COMPOSE_MAX_TOKENS = 900
NAME_MAX = 24
TITLE_MAX = 16
TAKEN_NAMES_MAX = 20
DIFFICULTY_MAX = 76
OPENING_TTL = 120.0
IDLE_TIMEOUT = 60.0
BATTLE_CLS = "BattleStartManager"
SPECIFIC_ENEMY = "specific"
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle", "in_conversation",
              "in_free_input", "in_action_in_conversation")
CATEGORIES = ("young man", "young woman", "teenage boy", "teenage girl",
              "middle-aged man", "middle-aged woman", "old man", "old woman")

PROMPT = (
    "あなたはゲームの世界で、ある勢力が主人公のもとへ差し向ける刺客の人物を1人考える係です。\n"
    "勢力: {faction}（{kind}）\n"
    "勢力の説明: {note}\n"
    "この勢力は主人公「{player}」を敵とみなしている。{title_line}\n"
    "舞台となる街: {town}\n"
    "街の様子: {notes}\n"
    "この土地の強さの目安: 難易度{difficulty}（0〜76。大きいほど強い）\n"
    "既にいる人物（名前をかぶらせない）: {taken}\n"
    "\n"
    "次の項目を考えてください。\n"
    "- title: 日本語。勢力の中での役職や肩書き。勢力の種類と説明に合わせる（例の形式:「異端審問官」「回収官」「執行人」）\n"
    "- name: 日本語。この人物の名前。役職は含めない\n"
    "- profile: 日本語。経歴と、勢力の命で主人公を討ちに来た経緯。2〜3文\n"
    "- personality: 日本語。性格。1〜2文\n"
    "- speech_style: 日本語。口調の特徴。1文\n"
    "- look_description: 日本語。見た目。1〜2文\n"
    "- category: 英語。次のどれか1つ: {categories}\n"
    "- look: 英語。外見を表す短い句を5つ、カンマ区切り。1つ目は category と同じ語にする\n"
)


def clean_text(value, limit=400):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def clean_name(value, limit=NAME_MAX):
    """名前はそのままファイルパスになる（GAME.md §2.15）。危ない字は落とす（`320_` と同じ）。"""
    name = clean_text(value, limit * 2)
    for bad in "\\/:*?\"<>|":
        name = name.replace(bad, "")
    return name.strip()[:limit]


def clean_category(value):
    text = clean_text(value, 40).lower().strip()
    for known in CATEGORIES:
        if known in text:
            return known
    return CATEGORIES[0]


def clean_look(value, category):
    tokens = [token.strip() for token in clean_text(value, 240).split(",")]
    return [category] + [token for token in tokens if token and token.lower() != category][:7]


def install(env):
    """`env`: ctx, write, worlds, ensure(app), cfg()（いまの世界の設定）, notify(key), rules, owner, rng。

    戻り値は会話に足す文を返す関数（`note(app, 台帳, 相手の id, 主人公の名前)`。`notes` の中から呼ぶ）。
    """
    ctx, write, worlds, rules = env.ctx, env.write, env.worlds, env.rules
    screen = ui.Screen(ctx, write, tag="hunter")
    rng = getattr(env, "rng", None) or random.Random()
    owner = env.owner + ":hunter"
    state = {"opening": None, "fight": None, "waiting": None, "making": False}
    arrivals.install(ctx, write)

    # ------------------------------------------------------------ 読む
    def character(app, npc_id):
        return ui.character_of(app, npc_id)

    def alive(app, npc_id):
        found = character(app, npc_id)
        config = getattr(found, "config", None)
        return found is not None and not (isinstance(config, dict) and config.get("is_dead"))

    def place_of(app):
        """`(土地, 土地の id, 施設の id)`。読めなければ None。"""
        player = getattr(app, "player", None)
        facility = getattr(player, "location", None)
        facility_id = getattr(facility, "id", None)
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        if area is None or area_id in (None, "") or facility_id in (None, ""):
            return None
        return area, str(area_id), str(facility_id)

    def area_difficulty(app, area):
        functions = sys.modules.get("scripts.functions")
        average = getattr(functions, "get_area_average_difficulty", None)
        try:
            value = average(area, getattr(app, "world", None)) if callable(average) else None
        except Exception:
            ctx.log_exc("town factions: get_area_average_difficulty failed")
            value = None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 20
        return min(DIFFICULTY_MAX, max(1, int(round(value))))

    def exp_level_for(difficulty):
        """経験値レベル。ゲーム自身の対応表で引く（呼べなければ近似。`320_` / `336_` と同じ）。"""
        table = getattr(sys.modules.get("scripts.functions"), "get_npc_exp_level", None)
        if callable(table):
            try:
                level = table(difficulty)
                if isinstance(level, int) and not isinstance(level, bool):
                    return min(100, max(1, level))
            except Exception:
                ctx.log_exc("town factions: get_npc_exp_level failed")
        return min(100, max(1, difficulty + 5))

    def faction_here(app, ledger):
        """いまの街の勢力 `(勢力, 段階)`。勢力の街でなければ `(None, None)`。"""
        found = place_of(app)
        if found is None:
            return None, None
        faction_id = rules.faction_of_town(ledger, found[1])
        faction = rules.faction_by_id(ledger, faction_id) if faction_id else None
        if faction is None:
            return None, None
        row = (ledger.get("standing") or {}).get(faction_id) or {}
        return faction, rules.standing_stage(row.get("score", 0))

    def busy(app):
        return [flag for flag in BUSY_FLAGS if getattr(app, flag, False)]

    # ------------------------------------------------------------ 着いたとき
    def arrived(app):
        ledger = env.ensure(app)
        if not ledger or ledger.get("status") != rules.READY:
            return
        faction, stage = faction_here(app, ledger)
        if faction is None:
            return
        cfg = env.cfg()
        rate = rules.hunter_rate(stage, cfg["HUNTER_HOSTILE_RATE"], cfg["HUNTER_WARY_RATE"])
        if rate <= 0:
            return
        key = worlds.playthrough(app)
        day = ui.game_day(app)
        with worlds.lock:
            waiting = rules.waiting_hunter(ledger, faction["id"], lambda npc_id: alive(app, npc_id))
            row = rules.hunter_of(ledger, waiting) if waiting else None
        if row is not None and row.get("status") == rules.HUNTER_READY:
            # 作ってある刺客は、勢力の街に着いたらすぐに呼び止める（抽選は作るときに済んでいる）
            launch(app, waiting)
            return
        if not rules.hunter_due(ledger.get("hunter_day"), day, cfg["HUNTER_GAP_DAYS"]):
            return
        if state["making"]:
            return                  # 前の到着で作っている途中
        roll = rng.randint(1, 100)
        if roll > rate:
            write("hunter: {} roll {} > {}% ({}, {})".format(
                key, roll, rate, faction["name"], rules.STANDING_NAMES[stage]))
            return
        with worlds.lock:
            ledger["hunter_day"] = day
            worlds.save(key)
        if waiting:
            write("hunter: {} {} comes back ({})".format(key, waiting, faction["name"]))
            launch(app, waiting)
            return
        found = place_of(app)
        write("hunter: {} roll {} <= {}%; making a hunter of {} in area {}".format(
            key, roll, rate, faction["name"], found[1]))
        # 作る前に申し出る。作る間に、同じ到着で `300_` が店の主に話しかけさせていた（実機）
        arrivals.offer(app, owner, ARRIVAL_PRIORITY)
        # 作る間は待機表示を出す。移動の処理の中でも、ゲームはもう選択肢を押せる状態に戻しており、
        # 作っている間に次の移動ができて2人目を作り始めた（実機）。人物を足す間に保存が走らないようにもする
        state["making"] = True
        screen.busy_on(app)
        made = False
        try:
            made = make({"app": app, "key": key, "faction": faction["id"], "area": found[1],
                         "facility": found[2]})
        finally:
            state["making"] = False
            screen.busy_off(app)
            if not made:
                arrivals.withdraw(app, owner)

    def make(job):
        """刺客を作る（移動の処理のスレッドで。LLM を待つ間ゲームは待機表示のまま）。できたら呼び止める。"""
        app, key = job["app"], job["key"]
        if worlds.playthrough(app) != key:
            write("hunter: {} skipped (another save was loaded)".format(key))
            return False
        ledger = env.ensure(app)
        faction = rules.faction_by_id(ledger or {}, job["faction"])
        if faction is None:
            return False
        area = ui.world_areas(app).get(job["area"]) if isinstance(ui.world_areas(app), dict) else None
        area = area or ui.current_area(app)
        difficulty = area_difficulty(app, area)
        fields = compose(app, ledger, faction, area, difficulty)
        if fields is None:
            write("hunter: the LLM gave no usable hunter")
            return False
        if worlds.playthrough(app) != key:
            write("hunter: {} dropped (another save was loaded while asking)".format(key))
            return False
        title = fields.pop("title")
        npc_id = npcs.make_npc(app, fields, job["area"], job["facility"],
                               config={"level_of_detail": 1, "difficulty_level": difficulty}, write=write)
        if npc_id is None:
            write("hunter: make_npc failed")
            return False
        made = character(app, npc_id)
        detail = getattr(app, "generate_npc_detail", None)
        if made is not None and callable(detail):
            try:
                detail(made)            # HP・スキル・立ち絵（埋めないまま戦闘に出すと落ちる）
            except Exception:
                ctx.log_exc("town factions: generate_npc_detail failed for the hunter")
        name = ui.character_name(app, npc_id, fallback=fields["name"])
        with worlds.lock:
            ledger = worlds.load(key)
            rules.hunters_of(ledger)[str(npc_id)] = {
                "faction": faction["id"], "name": name, "title": title, "town": job["area"],
                "status": rules.HUNTER_READY, "day": ui.game_day(app)}
            worlds.save(key)
        write("hunter: {} made {} {!r} ({}の{}) in area {} (difficulty {}, Lv {})".format(
            key, npc_id, name, faction["name"], title, job["area"], difficulty, fields["experience_level"]))
        launch(app, str(npc_id))
        return True

    def compose(app, ledger, faction, area, difficulty):
        structure = llm.create_structure(
            ctx, "ModFactionHunter",
            {"title": (str, ...), "name": (str, ...), "profile": (str, ...), "personality": (str, ...),
             "speech_style": (str, ...), "look_description": (str, ...), "category": (str, ...),
             "look": (str, ...)},
            label="faction hunter")
        player = getattr(getattr(app, "player", None), "name", "") or "主人公"
        standing = (ledger.get("standing") or {}).get(faction["id"]) or {}
        title_line = "勢力の者たちは主人公を「{}」と呼んでいる。".format(standing["title"]) \
            if standing.get("title") else ""
        taken = list(npcs_names(app))[-TAKEN_NAMES_MAX:]
        prompt = PROMPT.format(
            faction=faction["name"], kind=faction.get("kind") or "勢力", note=faction.get("note") or "",
            player=player, title_line=title_line, town=getattr(area, "name", "") or "",
            notes=clean_text(area_notes(area), 600), difficulty=difficulty,
            taken="、".join(taken) or "なし", categories=", ".join(CATEGORIES))
        data = llm.ask(ctx, MANAGER_NAME, [{"role": "user", "content": prompt}], timeout=COMPOSE_TIMEOUT,
                       structure=structure, max_tokens=COMPOSE_MAX_TOKENS, label="faction hunter", write=write)
        if not isinstance(data, dict):
            return None
        name = clean_name(data.get("name"))
        title = clean_name(data.get("title"), TITLE_MAX) or "刺客"
        if not name or name in npcs_names(app):
            write("hunter: unusable name {!r}".format(data.get("name")))
            return None
        category = clean_category(data.get("category"))
        return {"name": name, "title": title,
                "profile": "{}の{}。{}".format(faction["name"], title, clean_text(data.get("profile"))),
                "personality": clean_text(data.get("personality")),
                "speech_style": clean_text(data.get("speech_style"), 120),
                "look_description": clean_text(data.get("look_description")),
                "category": category, "look": clean_look(data.get("look"), category),
                "job": "adventure", "age": 30, "experience_level": exp_level_for(difficulty)}

    def npcs_names(app):
        characters = getattr(getattr(app, "world", None), "characters", None)
        return [name for name in (getattr(c, "name", None) for c in (characters or {}).values())
                if isinstance(name, str) and name]

    def area_notes(area):
        notes = getattr(area, "descriptions", None)
        if isinstance(notes, dict):
            for key in ("overview", "area_description", "facilities"):
                if isinstance(notes.get(key), str) and notes[key].strip():
                    return notes[key]
        return ""

    # ------------------------------------------------------------ 呼び止める
    def launch(app, npc_id):
        manager_cls = ui.cls_of("ConversationStartManager")
        if manager_cls is None:
            write("hunter: ConversationStartManager not found")
            return
        arrivals.offer(app, owner, ARRIVAL_PRIORITY)
        state["waiting"] = npc_id

        def gone():
            ledger = env.ensure(app)
            row = rules.hunter_of(ledger or {}, npc_id)
            if row is None or not alive(app, npc_id):
                return "the hunter is gone"
            faction, _stage = faction_here(app, ledger)
            if faction is None or faction["id"] != row.get("faction"):
                return "left the town of the faction"
            buttons = getattr(app, "buttons", None)
            if not modfacility.is_top_screen(buttons):
                # 着いた先で衛兵に見つかった画面（大人しく捕まる／抵抗する！）の上から呼び止めていた（実機。`300_` と同じ判定）
                return "not on the facility screen {}".format([ui.spec_cls_name(entry) for entry in buttons or []])
            return None

        def start():
            state["waiting"] = None
            reason = gone()
            if reason:
                write("hunter: {} not launched ({}); waits for the next arrival".format(npc_id, reason))
                arrivals.withdraw(app, owner)
                return
            doing = busy(app)
            if doing:
                write("hunter: {} not launched (busy {}); waits for the next arrival".format(npc_id, doing))
                arrivals.withdraw(app, owner)
                return
            top = arrivals.winner(app)
            if top not in (None, owner):
                write("hunter: {} yielded to {}; waits for the next arrival".format(npc_id, top))
                return
            key = worlds.playthrough(app)
            with worlds.lock:
                ledger = worlds.load(key)
                row = rules.hunter_of(ledger, npc_id)
                faction = rules.faction_by_id(ledger, row.get("faction")) or {}
                again = row.get("status") == rules.HUNTER_ESCAPED
                row["status"] = rules.HUNTER_MET
                worlds.save(key)
            name = row.get("name") or ui.character_name(app, npc_id)
            area = ui.current_area(app)
            state["opening"] = {"npc": npc_id, "at": _now(), "town": getattr(area, "name", "") or "",
                                "faction": faction.get("name", ""), "title": row.get("title") or "刺客",
                                "again": again}
            screen.say(app, APPEAR_TEXT.format(faction=faction.get("name", ""), title=row.get("title") or "刺客",
                                               name=name))
            write("hunter: {} launch process_choice(ConversationStartManager, {!r})".format(npc_id, name))
            try:
                app.process_choice(manager_cls(app, npc_id), name)
            except Exception:
                ctx.log_exc("town factions: cannot start the hunter's conversation")
                state["opening"] = None
                arrivals.withdraw(app, owner)

        screen.when_idle(app, start, timeout=IDLE_TIMEOUT, cancel_if=gone, tag="hunter")

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, messages, *args, **kwargs):
        """刺客が起こした会話のときだけ、開始の合図を「呼び止めて勝負を挑む」に差し替える（`300_` と同じ形）。"""
        mark, state["opening"] = state["opening"], None
        if mark is None or _now() - mark["at"] > OPENING_TTL or not isinstance(messages, list) \
                or not messages or not isinstance(messages[-1], dict):
            return orig(messages, *args, **kwargs)
        app = ui.find_app()
        player = getattr(getattr(app, "player", None), "name", "") or "旅人"
        replacement = dict(messages[-1])
        close = (OPENING_AGAIN if mark.get("again") else OPENING_FIRST).format(player=player)
        replacement["content"] = OPENING.format(player=player, town=mark["town"] or "この街",
                                                faction=mark["faction"], title=mark["title"], close=close)
        write("hunter: opening for {} -> {!r}".format(mark["npc"], replacement["content"][:60]))
        return orig(messages[:-1] + [replacement], *args, **kwargs)

    def note(app, ledger, npc_id, player):
        """会話に足す文（`notes` の中から呼ぶ）。刺客でなければ None。"""
        row = rules.hunter_of(ledger, npc_id)
        if row is None or row.get("status") not in rules.HUNTER_ALIVE:
            return None
        faction = rules.faction_by_id(ledger, row.get("faction")) or {}
        return NOTE.format(faction=faction.get("name", ""), title=row.get("title") or "刺客", player=player)

    # ------------------------------------------------------------ 「会話を終了する」を「受けて立つ」に
    # ゲームの「会話を終了する」のボタン（spec はそのまま）の文言だけを変え、押下を `intercept` で握る。
    # 自前のボタンに差し替えると、セーブに残るのは文言と spec だけで、ロードの後に印と相手が消え、
    # 押すとゲームが素の spec（JustSetButtonToNormalPhase）を動かして会話が半端に抜けた（実機）。
    # ゲームのボタンなら spec の args[0] が相手なので、ロードの後でも見分けられる
    def hunter_partner(app, entry):
        """そのボタンが生きている刺客との会話の「会話を終了する」なら相手の id。"""
        if ui.spec_cls_name(entry) != "ConversationEndManager":
            return None
        args = ui.spec_args(entry) or []
        partner = str(args[0]) if args else None
        if partner is None or not alive(app, partner):
            return None
        row = rules.hunter_of(env.ensure(app) or {}, partner)
        return partner if row is not None and row.get("status") in rules.HUNTER_ALIVE else None

    def trim(app, _buttons):
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        _partner, end_entry = ui.conversation_partner(buttons)
        if end_entry is None or hunter_partner(app, end_entry) is None:
            return
        end_entry["text"] = FIGHT_LABEL
        # 雇うのような会話のボタンも外す（受けて立つほかに道を残さない）
        buttons[:] = [end_entry]

    def intercept(app, entry, _index):
        npc_id = hunter_partner(app, entry)
        if npc_id is None:
            return False
        name = ui.character_name(app, npc_id)
        write("hunter: {} accepted the challenge".format(npc_id))

        def fight(app):
            cls = ui.cls_of(BATTLE_CLS)
            if cls is None:
                write("hunter: BattleStartManager not found")
                return
            try:
                phase = cls(app, SPECIFIC_ENEMY, [str(npc_id)])
            except Exception:
                ctx.log_exc("town factions: cannot build the hunter's battle")
                return
            write("hunter: {} battle starts".format(npc_id))
            screen.start_phase(app, phase, FIGHT_CHOICE.format(name=name))

        screen.end_conversation(app, entry, fight, end_text=END_TEXT.format(name=name),
                                on_abort=lambda reason: write("hunter: could not close the talk ({})".format(reason)))
        return True

    # 絞るのはほかの MOD がボタンを足し終えた後（`301_` が「依頼を受ける（話を切り上げる）」を足し、戦わずに抜けられた。実機）
    choices.provide(ctx, screen, trim=trim, intercept=intercept, owner=owner)

    # ------------------------------------------------------------ 戦闘
    @ctx.wrap("__main__:BattleStartManager.__init__", required=False, safe=True)
    def battle_start(orig, self, *args, **kwargs):
        """刺客との戦闘を見分ける（受けて立った戦闘も、会話の自由行動から入った戦闘も）。"""
        result = orig(self, *args, **kwargs)
        app = args[0] if args else kwargs.get("app")
        enemy_type = args[1] if len(args) > 1 else kwargs.get("enemy_type")
        content = args[2] if len(args) > 2 else kwargs.get("enemy_content")
        state["fight"] = None             # 前の戦闘の印を持ち越さない（ゲームオーバーの後など）
        if enemy_type != SPECIFIC_ENEMY or not isinstance(content, (list, tuple)) or app is None:
            return result
        ledger = env.ensure(app)
        for npc_id in content:
            row = rules.hunter_of(ledger or {}, npc_id)
            if row is None or row.get("status") not in rules.HUNTER_ALIVE:
                continue
            area = ui.current_area(app)
            area_id = str(ui.area_id_of(area))
            record = ui.area_record(getattr(app, "player", None), area_id)
            faction = rules.faction_by_id(ledger, row.get("faction")) or {}
            state["fight"] = {"npc": str(npc_id), "faction": row.get("faction"), "area": area_id,
                              "wanted": ui.lawfulness_of(record), "faction_name": faction.get("name", ""),
                              "title": row.get("title") or "刺客", "name": row.get("name") or ""}
            key = worlds.playthrough(app)
            with worlds.lock:
                row = rules.hunter_of(worlds.load(key), npc_id)
                row["status"] = rules.HUNTER_FIGHTING
                worlds.save(key)
            write("hunter: {} fight armed in area {} (wanted {})".format(npc_id, area_id, state["fight"]["wanted"]))
            break
        return result

    def battle_end(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        fight, state["fight"] = state["fight"], None
        if fight is not None:
            try:
                settle(getattr(self, "app", None) or ui.find_app(), fight, getattr(self, "end_type", None))
            except Exception:
                ctx.log_exc("town factions: cannot settle the hunter's battle")
        return result

    for target in ("__main__:BattleEndManager.end_phase", "__main__:BattleEndInFreeAction.end_phase"):
        ctx.wrap(target, required=False, safe=True)(battle_end)

    def rewrite_summary(texts, site):
        """刺客との戦闘の締めの頼みを、衛兵との戦闘の決め打ちから刺客との勝負に直す（`SUMMARY_LINES`）。"""
        fight = state["fight"]
        if fight is None:
            return None
        changed, out = False, []
        for text in texts:
            for old, new in SUMMARY_LINES:
                if old in text:
                    text = text.replace(old, new.format(faction=fight["faction_name"], title=fight["title"],
                                                        name=fight["name"]))
                    changed = True
            out.append(text)
        if changed:
            write("hunter: {} the battle summary told as the hunter's challenge ({})".format(fight["npc"], site))
        return out if changed else None

    llm.wrap_outgoing(ctx, rewrite_summary, label="town factions hunter")

    def settle(app, fight, end_type):
        npc_id = fight["npc"]
        found = character(app, npc_id)
        hp = getattr(found, "current_hp", None)
        won = end_type == "won" or (isinstance(hp, (int, float)) and not isinstance(hp, bool) and hp <= 0)
        key = worlds.playthrough(app)
        cfg = env.cfg()
        with worlds.lock:
            ledger = worlds.load(key)
            row = rules.hunter_of(ledger, npc_id) or {}
            faction = rules.faction_by_id(ledger, fight["faction"]) or {}
            if not won:
                row["status"] = rules.HUNTER_ESCAPED
                worlds.save(key)
                # ゲームは街での戦闘を衛兵との戦闘として扱い、逃げても手配度を 10 下げる（実機）。
                # 手配度が動くのは討ったときだけなので、戦闘の前へ戻す
                record = ui.area_record(getattr(app, "player", None), fight["area"])
                current = ui.lawfulness_of(record)
                after = rules.wanted_after_escape(fight["wanted"], current)
                if record is not None and current is not None and after != current:
                    ui.set_lawfulness(record, after)
                write("hunter: {} survived ({}, hp {}); comes back later; wanted {} -> {}".format(
                    npc_id, end_type, hp, current, after))
                return
            row["status"] = rules.HUNTER_DEAD
            config = getattr(found, "config", None)
            if isinstance(config, dict):
                config["is_dead"] = True
            moves = rules.hunter_loss(ledger, fight["faction"], cfg["HUNTER_STANDING_LOSS"])
            rules.apply_moves(ledger, moves)
            _written, changed = rules.record_moves(
                ledger, moves, day=ui.game_day(app), kind=rules.HUNTER,
                incident="{}の{}".format(faction.get("name", ""), row.get("title") or "刺客"))
            worlds.save(key)
        record = ui.area_record(getattr(app, "player", None), fight["area"])
        current = ui.lawfulness_of(record)
        after = rules.wanted_after(fight["wanted"], current, cfg["HUNTER_WANTED_LOSS"])
        if record is not None and current is not None and after != current:
            ui.set_lawfulness(record, after)
        write("hunter: {} defeated ({}); dead, standing {} wanted {} -> {} (before the fight {})".format(
            npc_id, end_type, moves, current, after, fight["wanted"]))
        if changed:
            env.notify(key)
        screen.say(app, WIN_TEXT.format(faction=faction.get("name", ""), title=row.get("title") or "刺客"))

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None:
                arrived(app)
        except Exception:
            ctx.log_exc("town factions: hunter arrival failed")
        return result

    return note


def _now():
    return time.monotonic()
