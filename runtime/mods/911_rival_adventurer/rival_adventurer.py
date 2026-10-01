# -*- coding: utf-8 -*-
"""ギルドの冒険者が1人ライバルになり、掲示板の依頼を先に片付けていく。

素のゲームの冒険者は、雇われるまで何もしない。
このMODは、プレイヤーが依頼をいくつか片付けたところで冒険者の1人をライバルに据え、
日数が進むたびに掲示板の依頼を1件狙わせる。
期限までにプレイヤーが片付ければプレイヤーの勝ち、
期限が来ればライバルが挑み、成功すればその依頼は掲示板から消える。
仕様と決めた経緯は DOC.md。

##### ゲームのどこに触るか

- **依頼の `status` は書かない。**
  `317_` は `completed` の依頼をプレイヤーの手柄として評判に編む（DOC.md §2.1）ので、
  片付けた依頼は台帳（`state\\rival_adventurer\\`）に控え、掲示板から隠すだけにする。
  MOD を外せば依頼は掲示板に戻る
- 掲示板は「未完了の依頼が2件以上あると『クエストを探す』を出さない」
  （206 の記録94回。DOC.md §2.2）。隠した依頼もゲームは未完了として数えるので、
  `DisplayQuestChoice.get_active_quest_count` の答えからも引く
- ライバルの Lv は勝つたびに上げる（実行時の Character と素データの両方）。
  これだけはセーブに残る。上げた Lv が何に効くかは DOC.md §4 の未確認項目
- 会話には `modnpc` の窓口（`register(owner, ANY, notes=...)`）から文を足す。
  ライバル本人には張り合う理由・態度・勝敗・最近の取り合い・今の狙い、その土地の住人には噂
- 登場の語りは背景の待ち行列で LLM に1回書かせる（`jobs.Worker`）。
  依頼の終わりは要約などで既に長いので、そこで待たせない
"""

import os
import random
import re
import sys

from instantale_modloader import frames, jobs, llm, modnpc, state as loader_state, ui
from instantale_modloader.npcs import npc_stores, save_npcs

from . import rivalry

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
RIVAL_AFTER_QUESTS = 2        # ライバルの抽選を始めるまでに片付ける依頼の数
RIVAL_CHANCE_PERCENT = 50     # 依頼を片付けるたびにライバルが現れる確率（%）
INTRO_USE_LLM = True          # 登場の語りと張り合う理由を LLM に書かせる
FAILURE_ENABLED = True        # ライバルが依頼にしくじることがある
SUCCESS_BASE_PERCENT = 60     # Lv と難易度が同じときの成功率（%）
SUCCESS_SLOPE_PERCENT = 3     # Lv が難易度を1上回るごとに足す成功率（%）
DUE_DAYS_MIN = 20             # 狙いを付けてから挑むまでの日数（下限）
DUE_DAYS_MAX = 40             # 同 上限
COOLDOWN_DAYS = 10            # 片付けてから次に狙うまでの日数
INJURY_DAYS = 30              # しくじったあと休む日数
REACH_LEVELS = 10             # 狙う依頼の難易度の上限（ライバルの Lv + この値）
LEVEL_STEP = 1                # 片付けるたびに上がる Lv
LEVEL_MAX = 99                # ライバルの Lv の上限
WINS_PER_STANCE = 1           # 態度が1段和らぐまでに先に片付ける回数
CLEANUP_SOFTENS = True        # ライバルがしくじった依頼を片付けると態度が1段和らぐ
AFFINITY_PER_STANCE = 30      # 態度が1段和らぐ好感度の幅
RUMOR_DAYS = 90               # 住人が噂にする日数
INTRO_FALLBACK = "ギルドの片隅で、冒険者{name}が{player}を値踏みするように眺めている。{known}を聞きつけ、張り合う気でいるらしい。"
REASON_FALLBACK = "{known}を聞き、格の違いを見せつけてやろうと思っている"
TARGET_SUFFIX = "（{name}が狙っている・あと{days}日）"
TARGET_ANNOUNCE = "冒険者{name}が、掲示板の「{title}」に目を付けたようだ。"
TAKEN_ANNOUNCE = "掲示板から「{title}」の貼り紙が剥がされている。{name}が片付けたらしい。"
FAILED_ANNOUNCE = "「{title}」に挑んだ{name}がしくじって怪我を負ったと、掲示板の前で噂されている。"
WIN_ANNOUNCE = "{name}が狙っていた依頼を、先に片付けた。"
CLEANUP_ANNOUNCE = "{name}がしくじった「{title}」を、代わりに片付けた。"
STANCE_ANNOUNCE_1 = "{name}の態度から、わずかに棘が抜けたようだ。"
STANCE_ANNOUNCE_2 = "{name}は、もう{player}を格下とは見ていないらしい。"
STANCE_ANNOUNCE_3 = "{name}は、すっかり{player}に一目置いているようだ。"
RIVAL_NOTE = ("あなたは同じ冒険者として{player}を張り合う相手と見ている。理由は、{reason}から。"
              "依頼の取り合いはこれまで、あなたが{rival_wins}回、{player}が{player_wins}回先に片付けた。")
STANCE_NOTE_0 = "{player}のことは格下だと見下していて、口を開けば嫌味や挑発が出る。"
STANCE_NOTE_1 = "口では張り合っているが、{player}の腕は認め始めていて、嫌味にも以前ほどの棘は無い。"
STANCE_NOTE_2 = "{player}を対等な好敵手と認め、敬意を持って競い合っている。"
STANCE_NOTE_3 = "{player}の実力にすっかり感服していて、張り合うより認められたがり、何かと持ち上げる。"
RIVAL_AIM_NOTE = "いまは「{title}」を狙っていて、{days}日ほどのうちに片付けるつもりでいる。"
RIVAL_AWAY_NOTE = "先日「{title}」にしくじって怪我を負い、あと{days}日ほどは依頼を受けずに休んでいる。"
RUMOR_NOTE = "この土地では、冒険者{name}が「{title}」を片付けたと噂になっている（{days}日前）。"

# ---- 設定にしない定数 ----------------------------------------------------
OWNER = "911_rival_adventurer"         # modnpc の層の持ち主（フォルダ名）
LOG_BASENAME = "rival_adventurer.log"
STATE_DIRNAME = "rival_adventurer"
STORE_ATTR = "_instantale_rival_adventurer"
SETTLEMENT_QUEST = "settlement_quest"  # 掲示板の QuestChoiceManager の第1引数（GAME.md §2.9）
CHOICE_SPEC = "QuestChoiceManager"
STORY_QUEST_FIELD = "story_quest"
COMPLETED = "completed"
RUMOR_LIMIT = 2                        # 1回の会話に足す噂の件数
HISTORY_LIMIT = 3                      # ライバル本人に渡す最近の取り合いの件数
#: 取り合いの結末を本人に渡す言い方（`rivalry.RIVAL_WON` など）。
HISTORY_WORDS = {"rival": "「{title}」はあなたが先に片付けた",
                 "player": "「{title}」は{player}に先を越された",
                 "failed": "「{title}」はあなたがしくじった",
                 "cleanup": "「{title}」はあなたがしくじった後、{player}に片付けられた"}
HISTORY_NOTE = "最近の取り合い: {items}。"
#: `301_quest_from_conversation` の控え（世界名 → {依頼id: 依頼人}）。
#: 会話から頼んで作ってもらった依頼は狙わない（読むだけ。DOC.md §1）。
CONVERSATION_QUESTS_FILE = "quest_clients.json"
#: `317_reputation` の控えのフォルダ（世界名ごと）。二つ名を読むだけ。
REPUTATION_DIRNAME = "reputation"
#: 登場の語りの頼み。`output_data\` に別々に残るよう MOD 専用の名前。
MANAGER_INTRO = "mod_rival_adventurer_intro"
INTRO_TIMEOUT = 90                     # 秒。返らなければ文型の語りに降りる
INTRO_CHARS = 300                      # 語りの上限（超えたら文型に降りる）
REASON_CHARS = 80                      # 理由の上限
DEED_CHARS = 80                        # 頼み文に渡す功績1件の上限
PROFILE_CHARS = 300                    # 頼み文に渡す人物の素性の上限
#: 語りを出すのを見送る旗（GAME.md §2.6）。戦闘・会話の最中には割り込まない。
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
              "in_conversation", "in_free_input", "in_action_in_conversation")

LOAD_TARGETS = ("__main__:InstantaleApp.load_game_new",
                "__main__:InstantaleApp.start_game")

INTRO_PROMPT = """あなたはファンタジーRPGの語り手です。
冒険者「{name}」が、同じ冒険者「{player}」を張り合う相手と見定めた場面を書いてください。

# 世界
{world}

# {name}（ライバルになる冒険者）
{rival}

# {player}について{name}が耳にしていること
{known}

# 書くこと
- narration: その場面の語り。2〜3文、常体。{name}の台詞を1つ含めてよい。{name}は{player}を格下と見下している。{player}の台詞や行動は書かない
- reason: {name}が{player}と張り合う理由。{name}の立場から1文で、文末は「〜」で止める（「から」は付けない）。例: 噂ばかり先行する新顔に格の違いを見せつけたい
"""


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None, "reputation": None, "worker": None,
                 "reconcile": False, "rng": random.Random()}
        setattr(sys, STORE_ATTR, store)
    for name in ("reputation", "worker"):
        store.setdefault(name, None)
    return store


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def suffix_pattern(template):
    """`TARGET_SUFFIX` で付けた添え字を見つける正規表現。値が何でも剥がせるように。

    値の部分は括弧をまたがせない。またがせると、題名に全角の括弧がある依頼で
    題名の括弧から末尾までを1つの添え字と読んで、題名を削る。
    """
    parts = re.split(r"\{[^{}]*\}", template or "")
    if not any(parts):
        return None
    return re.compile("[^（）()]*?".join(re.escape(part) for part in parts) + r"$")


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(
            ctx, STATE_DIRNAME, default=rivalry.new_bucket,
            order=rivalry.order_bucket, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    if store["reputation"] is None:
        store["reputation"] = loader_state.WorldStore(ctx, REPUTATION_DIRNAME, own=False)
    reputation = store["reputation"].rebind(ctx)
    rng = store["rng"]
    screen = ui.Screen(ctx, write, tag="rival adventurer")
    stale_suffix = suffix_pattern(TARGET_SUFFIX)
    stance_notes = (STANCE_NOTE_0, STANCE_NOTE_1, STANCE_NOTE_2, STANCE_NOTE_3)
    stance_announces = (None, STANCE_ANNOUNCE_1, STANCE_ANNOUNCE_2, STANCE_ANNOUNCE_3)

    def ledger(app):
        key = worlds.playthrough(app)
        return key, worlds.load(key)

    # ------------------------------------------------------------ 世界を読む
    def live_quests(app):
        """生きた一覧（`world.quests`）。掲示板が読むのはこちら（GAME.md §2.9.1）。"""
        quests = getattr(getattr(app, "world", None), "quests", None)
        return quests if isinstance(quests, dict) else {}

    def quest_title(quest, fallback=""):
        title = ui.quest_value(quest, "quest_title", "")
        return title.strip() if isinstance(title, str) and title.strip() else fallback

    def is_completed(quest):
        config = ui.quest_value(quest, "config", None)
        return isinstance(config, dict) and config.get("status") == COMPLETED

    def cleared_count(app):
        """プレイヤーが片付けた依頼の数（`world.quests` の `completed`）。"""
        return sum(1 for quest in live_quests(app).values()
                   if quest is not None and is_completed(quest))

    def conversation_quests(app):
        """`301_` が会話から作った依頼の id。読めなければ空。"""
        try:
            path = os.path.join(ctx.state_dir, CONVERSATION_QUESTS_FILE)
            if not os.path.isfile(path):
                return set()
            data = ctx.read_json(path, {})
            world = (data or {}).get(loader_state.world_key(app))
            return {str(qid) for qid in world} if isinstance(world, dict) else set()
        except Exception:
            ctx.log_exc("rival adventurer: cannot read {}".format(CONVERSATION_QUESTS_FILE))
            return set()

    def open_quests(app, area_id, bucket):
        """その土地でライバルが狙える依頼 `[(id, 難易度)]`。

        掲示板に出る条件（その土地の依頼で未完了）から、物語の依頼・隠した依頼・
        プレイヤーが受けている依頼・会話から作った依頼を除く。
        """
        taken = rivalry.taken_of(bucket)
        current = ui.current_quest_id(app)
        skip = conversation_quests(app)
        rows = []
        for quest_id, quest in live_quests(app).items():
            quest_id = str(quest_id)
            if quest is None or quest_id in taken or quest_id == current or quest_id in skip:
                continue
            if str(ui.quest_value(quest, "neighboring_settlement_id", "")) != str(area_id):
                continue
            if ui.quest_value(quest, "quest_type", None) == STORY_QUEST_FIELD:
                continue
            if is_completed(quest):
                continue
            rows.append((quest_id, _int(ui.quest_value(quest, "difficulty", None))))
        return rows

    def level_of(app, npc_id):
        for value in (getattr(ui.character_of(app, npc_id), "experience_level", None),
                      (save_npcs(app).get(str(npc_id)) or {}).get("experience_level")):
            if _int(value) is not None:
                return value
        return None

    def raw_of(app, npc_id, name):
        """人物の項目。実行時の Character を先に、無ければ素データ。"""
        value = getattr(ui.character_of(app, npc_id), name, None)
        if value is None:
            value = (save_npcs(app).get(str(npc_id)) or {}).get(name)
        return value

    def is_dead(app, npc_id):
        config = raw_of(app, npc_id, "config")
        return bool(config.get("is_dead")) if isinstance(config, dict) else False

    def exists(app, npc_id):
        return ui.character_of(app, npc_id) is not None or str(npc_id) in save_npcs(app)

    def in_party(app, npc_id):
        return str(npc_id) in {str(member) for member in ui.party_member_ids(app)}

    def affinity_of(app, npc_id):
        relationship = raw_of(app, npc_id, "relationship")
        row = relationship.get("player") if isinstance(relationship, dict) else None
        value = row.get("affinity") if isinstance(row, dict) else None
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def adventurers(app):
        """名簿（`Area.adventurer_npcs`）に載っている冒険者の id。土地の順。"""
        found = []
        for _area_id, area in sorted((ui.world_areas(app) or {}).items(),
                                     key=lambda item: ui.id_sort_key(item[0])):
            for raw in getattr(area, "adventurer_npcs", None) or []:
                if str(raw) not in found:
                    found.append(str(raw))
        return found

    def home_area(app, npc_id):
        """その冒険者が名簿に載っている土地。無ければ ""。"""
        for area_id, area in (ui.world_areas(app) or {}).items():
            if str(npc_id) in [str(raw) for raw in getattr(area, "adventurer_npcs", None) or []]:
                return str(area_id)
        return ""

    def player_level(app):
        return _int(getattr(getattr(app, "player", None), "experience_level", None))

    def player_name(app):
        name = getattr(getattr(app, "player", None), "name", None)
        return name.strip() if isinstance(name, str) and name.strip() else "プレイヤー"

    def rival_name(app, bucket):
        rival = bucket.get("rival") or {}
        return ui.character_name(app, rival.get("id"), fallback=rival.get("name") or "")

    def epithet_of(app):
        """`317_` の二つ名。入っていない・まだ編まれていなければ ""。"""
        try:
            _key, bucket = reputation.of(app, fresh=True)
        except Exception:
            ctx.log_exc("rival adventurer: cannot read the epithet")
            return ""
        record = bucket.get("epithet") if isinstance(bucket, dict) else None
        text = record.get("epithet") if isinstance(record, dict) else None
        return text.strip() if isinstance(text, str) and text.strip() else ""

    def deeds_of(app, limit=2):
        """プレイヤーの功績の文（`area_history` の `achievements`）。土地ごとの最新を `limit` 件。"""
        history = getattr(getattr(app, "player", None), "area_history", None)
        if not isinstance(history, dict):
            data = getattr(app, "save_data_dict", None)
            player = data.get("player_data") if isinstance(data, dict) else None
            history = player.get("area_history") if isinstance(player, dict) else None
        found = []
        for _area_id, row in (history or {}).items() if isinstance(history, dict) else ():
            deeds = row.get("achievements") if isinstance(row, dict) else None
            if isinstance(deeds, list) and deeds and isinstance(deeds[-1], str):
                found.append(frames.short(deeds[-1].strip(), DEED_CHARS))
        return found[-limit:]

    def known_text(app):
        """ライバルが耳にしているプレイヤーの評判の一言（文型の `{known}`）。"""
        epithet = epithet_of(app)
        if epithet:
            return "「{}」の二つ名".format(epithet)
        return "{}の活躍の噂".format(player_name(app))

    # ------------------------------------------------------------ ライバルを選ぶ
    def rival_alive(app, bucket):
        """ライバルがまだ居るか。居なくなっていれば（死亡・消去）台帳を空にして False。"""
        rival = bucket.get("rival")
        if not isinstance(rival, dict) or not rival.get("id"):
            return False
        npc_id = str(rival["id"])
        if exists(app, npc_id) and not is_dead(app, npc_id):
            return True
        write("rival: {} ({}) is gone; the next rival is drawn on a quest clear".format(
            rival.get("name"), npc_id))
        bucket.update(rivalry.new_bucket())
        return False

    def draw_rival(app, bucket, day):
        """依頼を片付けたときの抽選。当たってライバルを選べたら True。"""
        clears = cleared_count(app)
        if not rivalry.may_draw(clears, RIVAL_AFTER_QUESTS):
            write("draw: {} quest(s) cleared, waiting for {}".format(clears, RIVAL_AFTER_QUESTS))
            return False
        roll = rng.random()
        if roll >= RIVAL_CHANCE_PERCENT / 100.0:
            write("draw: missed ({:.2f} >= {}%), {} quest(s) cleared".format(
                roll, RIVAL_CHANCE_PERCENT, clears))
            return False
        party = {str(member) for member in ui.party_member_ids(app)}
        candidates = [(npc_id, level_of(app, npc_id)) for npc_id in adventurers(app)
                      if npc_id not in party and exists(app, npc_id)
                      and not is_dead(app, npc_id)]
        chosen = rivalry.pick_rival(candidates, player_level(app), rng)
        if chosen is None:
            write("draw: hit but no adventurer to choose")
            return False
        bucket.update(rivalry.new_bucket())
        bucket["rival"] = {"id": chosen, "name": ui.character_name(app, chosen),
                           "chosen_day": day, "stance": 0, "stance_told": True,
                           "reason": "", "intro": "", "intro_told": False}
        write("draw: chose {} ({}) Lv{} for player Lv{} among {} adventurer(s)".format(
            bucket["rival"]["name"], chosen, level_of(app, chosen), player_level(app),
            len(candidates)))
        return True

    # ------------------------------------------------------------ 登場の語り
    def intro_messages(app, rival_id):
        rival_lines = []
        for label, name in (("経歴", "profile"), ("性格", "personality"),
                            ("話し方", "speech_style")):
            value = raw_of(app, rival_id, name)
            if isinstance(value, str) and value.strip():
                rival_lines.append("{}: {}".format(label, frames.short(value.strip(),
                                                                       PROFILE_CHARS)))
        rival_lines.append("Lv: {}".format(level_of(app, rival_id)))
        known = []
        epithet = epithet_of(app)
        if epithet:
            known.append("「{}」の二つ名で知られている".format(epithet))
        known.extend(deeds_of(app))
        if not known:
            known.append("最近名を上げ始めた冒険者だという噂")
        prompt = INTRO_PROMPT.format(
            name=ui.character_name(app, rival_id), player=player_name(app),
            world=ui.world_overview(app) or "（不明）",
            rival="\n".join(rival_lines), known="\n".join("- " + line for line in known))
        return [{"role": "user", "content": prompt}]

    def write_intro(job):
        """背景で語りと理由を作り、台帳へ入れて出す。LLM が使えなければ文型。"""
        app = job["app"]
        rival_id = job["rival"]
        narration, reason = "", ""
        if INTRO_USE_LLM:
            structure = llm.create_structure(ctx, "RivalIntro", {
                "narration": (str, ...), "reason": (str, ...)}, label="rival adventurer")
            raw = None
            if structure is not None:
                raw = llm.ask(ctx, MANAGER_INTRO, intro_messages(app, rival_id),
                              timeout=INTRO_TIMEOUT, structure=structure,
                              label="rival adventurer", write=write)
            if isinstance(raw, dict):
                narration = str(raw.get("narration") or "").strip()
                reason = str(raw.get("reason") or "").strip().rstrip("。").strip()
                if reason.endswith("から"):
                    reason = reason[:-2].rstrip()
            if not narration or len(narration) > INTRO_CHARS:
                if narration:
                    write("intro: narration too long ({} chars); using the template".format(
                        len(narration)))
                narration = ""
            if not reason or len(reason) > REASON_CHARS:
                reason = ""
        fields = {"name": ui.character_name(app, rival_id), "player": player_name(app),
                  "known": known_text(app)}
        narration = narration or rivalry.format_text(INTRO_FALLBACK, **fields)
        reason = reason or rivalry.format_text(REASON_FALLBACK, **fields)
        with worlds.lock:
            bucket = worlds.load(job["key"])
            rival = bucket.get("rival")
            if not isinstance(rival, dict) or str(rival.get("id")) != str(rival_id):
                write("intro: the rival changed while writing; dropped")
                return
            rival["intro"] = narration
            rival["reason"] = reason
            worlds.save(job["key"])
        write("intro: {!r} / reason {!r}".format(narration[:80], reason))
        screen.when_idle(app, lambda: tell_intro(app), cancel_if=lambda: busy_reason(app),
                         tag="rival adventurer intro")

    worker = store["worker"] = jobs.rebind(
        store["worker"]
        or jobs.Worker(ctx, write_intro, name="rival_adventurer",
                       label="rival adventurer", key=lambda job: job["key"]),
        ctx, write_intro, write)

    def ask_intro(app, key, rival_id):
        worker.enqueue({"app": app, "key": key, "rival": str(rival_id)})

    def busy_reason(app):
        """語りを出すのを見送る理由。出してよければ None。"""
        for flag in BUSY_FLAGS:
            if getattr(app, flag, None):
                return flag
        if ui.current_quest_id(app) is not None:
            return "on a quest"
        return None

    def tell_intro(app):
        """まだ出していない登場の語りを出す。出したら True。"""
        with worlds.lock:
            key, bucket = ledger(app)
            rival = bucket.get("rival")
            if not isinstance(rival, dict) or rival.get("intro_told") or not rival.get("intro"):
                return False
            rival["intro_told"] = True
            worlds.save(key)
            text = rival["intro"]
        screen.say(app, text)
        return True

    # ------------------------------------------------------------ 態度
    def soften(app, bucket, floor, why):
        """態度を `floor` 段まで和らげる。和らいだら知らせる文（無ければ ""）。"""
        stance = rivalry.raise_stance(bucket, floor)
        if stance is None:
            return ""
        write("stance: {} -> {} ({})".format(rival_name(app, bucket), stance, why))
        return rivalry.format_text(stance_announces[stance], name=rival_name(app, bucket),
                                   player=player_name(app))

    def stance_line(app, bucket):
        """まだ知らせていない態度の変化の文。出すと印を付ける（錠は呼び側）。"""
        rival = bucket.get("rival")
        if not isinstance(rival, dict) or rival.get("stance_told", True):
            return ""
        rival["stance_told"] = True
        stance = rivalry.stance_of(bucket)
        return rivalry.format_text(stance_announces[stance], name=rival_name(app, bucket),
                                   player=player_name(app)) if stance else ""

    def raise_level(app, npc_id):
        """Lv を `LEVEL_STEP` 上げる。実行時の Character と素データの両方へ。上げた後の Lv を返す。"""
        before = level_of(app, npc_id)
        if before is None:
            return None
        after = min(int(LEVEL_MAX), before + max(0, int(LEVEL_STEP)))
        if after <= before:
            return before
        character = ui.character_of(app, npc_id)
        if character is not None:
            character.experience_level = after
        for where, holder in npc_stores(app):
            if "characters" in where.rsplit(".", 1)[-1]:
                continue                # 実行時の名簿。素データではない
            data = holder.get(str(npc_id))
            if isinstance(data, dict):
                data["experience_level"] = after
        return after

    # ------------------------------------------------------------ 狙う・挑む
    def aim(app, bucket, day, area_id, why):
        """その土地の依頼に狙いを付ける。付けたら True。"""
        if not area_id or not rivalry.may_aim(bucket, day):
            return False
        rival_id = str((bucket.get("rival") or {}).get("id") or "")
        if not rival_id or in_party(app, rival_id):
            return False
        quests = open_quests(app, area_id, bucket)
        quest_id = rivalry.pick_target(quests, level_of(app, rival_id), REACH_LEVELS, rng)
        if quest_id is None:
            return False
        quest = live_quests(app).get(quest_id)
        due = rivalry.due_day(day, rng, DUE_DAYS_MIN, DUE_DAYS_MAX)
        bucket["target"] = {"quest": quest_id, "area": str(area_id),
                            "title": quest_title(quest, quest_id),
                            "set_day": day, "due_day": due, "told": False}
        write("aim[{}]: {} -> quest {} {!r} in area {} (difficulty {}), due day {}".format(
            why, rival_name(app, bucket), quest_id, bucket["target"]["title"], area_id,
            ui.quest_value(quest, "difficulty", None), due))
        return True

    def settle(app, bucket, day):
        """狙いの決着。変えたら True。"""
        target = bucket.get("target")
        if not isinstance(target, dict):
            return False
        rival_id = str((bucket.get("rival") or {}).get("id") or "")
        quest_id = str(target.get("quest"))
        quest = live_quests(app).get(quest_id)
        if quest is None:
            bucket["target"] = None
            write("settle: quest {} is gone; target dropped".format(quest_id))
            return True
        if is_completed(quest):
            player_won(app, bucket, day, "found completed")
            return True
        if in_party(app, rival_id):
            # 同行中は張り合えない。狙いは取り下げる。
            bucket["target"] = None
            write("settle: {} joined the party; target {} dropped".format(
                rival_name(app, bucket), quest_id))
            return True
        if not rivalry.is_due(target, day):
            return False
        if ui.current_quest_id(app) == quest_id:
            write("settle: player is on quest {}; {} waits".format(
                quest_id, rival_name(app, bucket)))
            return False
        level = level_of(app, rival_id)
        difficulty = _int(ui.quest_value(quest, "difficulty", None))
        chance = (rivalry.success_chance(level, difficulty, SUCCESS_BASE_PERCENT,
                                         SUCCESS_SLOPE_PERCENT)
                  if FAILURE_ENABLED else 1.0)
        roll = rng.random()
        score = rivalry.score_of(bucket)
        row = {"area": target.get("area"), "title": target.get("title"),
               "day": day, "told": False}
        bucket["target"] = None
        if roll < chance:
            rivalry.taken_of(bucket)[quest_id] = row
            score["rival"] += 1
            rivalry.push_history(bucket, row["title"], rivalry.RIVAL_WON, day, HISTORY_LIMIT)
            after = raise_level(app, rival_id)
            bucket["next_day"] = day + max(0, int(COOLDOWN_DAYS))
            write("settle: {} cleared quest {} {!r} (Lv{} vs difficulty {}, chance {:.2f}, "
                  "roll {:.2f}) -> Lv{}".format(rival_name(app, bucket), quest_id,
                                                 row["title"], level, difficulty, chance,
                                                 roll, after))
        else:
            row["quest"] = quest_id
            bucket["failure"] = row
            score["failed"] += 1
            rivalry.push_history(bucket, row["title"], rivalry.RIVAL_FAILED, day,
                                 HISTORY_LIMIT)
            bucket["away_until"] = day + max(0, int(INJURY_DAYS))
            bucket["next_day"] = bucket["away_until"]
            write("settle: {} failed quest {} {!r} (Lv{} vs difficulty {}, chance {:.2f}, "
                  "roll {:.2f}); away until day {}".format(
                      rival_name(app, bucket), quest_id, row["title"], level, difficulty,
                      chance, roll, bucket["away_until"]))
        return True

    def player_won(app, bucket, day, why):
        """狙われた依頼をプレイヤーが先に片付けた。態度が和らいだら知らせる文を返す。"""
        target = bucket.get("target") or {}
        score = rivalry.score_of(bucket)
        score["player"] += 1
        rivalry.push_history(bucket, target.get("title"), rivalry.PLAYER_WON, day,
                             HISTORY_LIMIT)
        bucket["target"] = None
        bucket["next_day"] = day + max(0, int(COOLDOWN_DAYS)) if day is not None else None
        write("settle[{}]: player cleared quest {} {!r} before {}".format(
            why, target.get("quest"), target.get("title"), rival_name(app, bucket)))
        line = soften(app, bucket, rivalry.stance_from_wins(score["player"], WINS_PER_STANCE),
                      "player won")
        if line:
            bucket["rival"]["stance_told"] = True
        return line

    def cleaned_up(app, bucket, failure, day):
        """ライバルがしくじった依頼をプレイヤーが片付けた（尻拭い）。出す文の並びを返す。

        1つのしくじりにつき1度だけ（`cleaned` の印）。
        """
        failure["cleaned"] = True
        rivalry.push_history(bucket, failure.get("title"), rivalry.PLAYER_CLEANED, day,
                             HISTORY_LIMIT)
        name = rival_name(app, bucket)
        write("cleanup: player cleared quest {} {!r} that {} failed".format(
            failure.get("quest"), failure.get("title"), name))
        lines = [rivalry.format_text(CLEANUP_ANNOUNCE, name=name, title=failure.get("title"))]
        if CLEANUP_SOFTENS:
            line = soften(app, bucket, rivalry.stance_of(bucket) + 1, "cleanup")
            if line:
                bucket["rival"]["stance_told"] = True
                lines.append(line)
        return lines

    def tick(app, why):
        """日が進んだ後の見回り。決着を付け、狙いが無ければ付ける。"""
        if app is None or getattr(app, "world", None) is None:
            return
        day = ui.game_day(app)
        if day is None:
            return
        with worlds.lock:
            key, bucket = ledger(app)
            had_rival = isinstance(bucket.get("rival"), dict)
            if not rival_alive(app, bucket):
                if had_rival:
                    worlds.save(key)
                return
            changed = settle(app, bucket, day)
            if rivalry.may_aim(bucket, day):
                here = ui.area_id_of(ui.current_area(app))
                rival_id = (bucket.get("rival") or {}).get("id")
                for area_id in (here, home_area(app, rival_id)):
                    if aim(app, bucket, day, area_id, why):
                        changed = True
                        break
            if changed:
                worlds.save(key)

    # ------------------------------------------------------------ 掲示板
    def hidden_here(app, area_id):
        """その土地で隠している未完了の依頼の id。"""
        if not area_id:
            return set()
        _key, bucket = ledger(app)
        quests = live_quests(app)
        found = set()
        for quest_id, row in rivalry.taken_of(bucket).items():
            quest = quests.get(str(quest_id))
            if quest is None or is_completed(quest):
                continue
            if str(ui.quest_value(quest, "neighboring_settlement_id", "")) == str(area_id):
                found.add(str(quest_id))
        return found

    def board_buttons(buttons):
        """掲示板の依頼のボタン `[(位置, 依頼id)]`。無ければ空（掲示板ではない）。"""
        found = []
        for index, entry in enumerate(buttons):
            if ui.spec_cls_name(entry) != CHOICE_SPEC:
                continue
            args = ui.spec_args(entry) or []
            if len(args) >= 2 and args[0] == SETTLEMENT_QUEST:
                found.append((index, str(args[1])))
        return found

    def fix_board(app, buttons):
        """隠した依頼のボタンを外し、狙われている依頼に添え字を付ける。"""
        rows = board_buttons(buttons)
        if not rows:
            return
        area_id = ui.area_id_of(ui.current_area(app))
        hidden = hidden_here(app, area_id)
        _key, bucket = ledger(app)
        target = bucket.get("target") if isinstance(bucket.get("target"), dict) else {}
        day = ui.game_day(app)
        removed = []
        for index, quest_id in reversed(rows):
            if quest_id in hidden:
                removed.append(quest_id)
                del buttons[index]
                continue
            entry = buttons[index]
            if not isinstance(entry, dict):
                continue
            text = entry.get("text") if isinstance(entry.get("text"), str) else ""
            base = stale_suffix.sub("", text) if stale_suffix is not None else text
            if quest_id == str(target.get("quest")):
                suffix = rivalry.format_text(TARGET_SUFFIX, name=rival_name(app, bucket),
                                             days=rivalry.days_left(target, day))
                base = base + suffix
            if base != text:
                entry["text"] = base
        if removed:
            write("board: hid quest(s) {} in area {}".format(sorted(removed, key=ui.id_sort_key),
                                                             area_id))

    def tell_board(app):
        """掲示板を開いた土地の、まだ知らせていない出来事を本文に出す。

        登場の語りがまだ出ていなければ先に出す（出し損ねた分の受け皿）。
        態度の変化も、会話の好感度で和らいだ分はここで知らせる。
        """
        tell_intro(app)
        area_id = ui.area_id_of(ui.current_area(app))
        if not area_id:
            return
        lines = []
        with worlds.lock:
            key, bucket = ledger(app)
            if not isinstance(bucket.get("rival"), dict):
                return
            name = rival_name(app, bucket)
            for kind, _quest_id, row in rivalry.untold_in(bucket, area_id):
                template = TAKEN_ANNOUNCE if kind == "taken" else FAILED_ANNOUNCE
                text = rivalry.format_text(template, name=name, title=row.get("title"))
                if text:
                    lines.append(text)
                row["told"] = True
            target = bucket.get("target")
            if isinstance(target, dict) and not target.get("told") \
                    and str(target.get("area")) == str(area_id):
                text = rivalry.format_text(TARGET_ANNOUNCE, name=name,
                                           title=target.get("title"))
                if text:
                    lines.append(text)
                target["told"] = True
            line = stance_line(app, bucket)
            if line:
                lines.append(line)
            if lines:
                worlds.save(key)
        for line in lines:
            screen.say(app, line)

    # ------------------------------------------------------------ 会話の文
    def rival_note(app, bucket, day):
        """ライバル本人に渡す文。理由・態度・勝敗・最近の取り合い・今の狙い。"""
        rival = bucket["rival"]
        player = player_name(app)
        score = rivalry.score_of(bucket)
        parts = [rivalry.format_text(RIVAL_NOTE, player=player,
                                     reason=rival.get("reason") or rivalry.format_text(
                                         REASON_FALLBACK, known=known_text(app)),
                                     rival_wins=score["rival"], player_wins=score["player"]),
                 rivalry.format_text(stance_notes[rivalry.stance_of(bucket)], player=player)]
        items = [rivalry.format_text(HISTORY_WORDS.get(row.get("outcome"), ""),
                                     title=row.get("title"), player=player)
                 for row in rivalry.history_of(bucket)]
        items = [item for item in items if item]
        if items:
            parts.append(rivalry.format_text(HISTORY_NOTE, items="、".join(items)))
        target = bucket.get("target")
        failure = bucket.get("failure")
        if isinstance(target, dict):
            parts.append(rivalry.format_text(RIVAL_AIM_NOTE, title=target.get("title"),
                                             days=rivalry.days_left(target, day)))
        elif rivalry.is_away(bucket, day) and isinstance(failure, dict):
            parts.append(rivalry.format_text(
                RIVAL_AWAY_NOTE, title=failure.get("title"),
                days=max(0, int(bucket["away_until"]) - int(day))))
        return "".join(part for part in parts if part) or None

    def notes(info):
        """会話相手の素性に足す文。ライバル本人には `rival_note`、ほかの人には噂。

        ライバル本人と話すたびに好感度を見て、態度の段を上げる（下げない）。
        知らせる文は次に掲示板を開いたときに出す（会話の最中には割り込まない）。
        """
        app = info.get("app") or ui.find_app()
        if app is None:
            return None
        day = ui.game_day(app)
        rival = None
        with worlds.lock:
            key, bucket = ledger(app)
            rival = bucket.get("rival")
            if not isinstance(rival, dict):
                return None
            if str(info.get("npc_id")) == str(rival.get("id")):
                floor = rivalry.stance_from_affinity(affinity_of(app, rival.get("id")),
                                                     AFFINITY_PER_STANCE)
                if soften(app, bucket, floor, "affinity"):
                    worlds.save(key)
                return rival_note(app, bucket, day)
        if in_party(app, rival.get("id")):
            return None
        name = rival_name(app, bucket)
        area_id = ui.area_id_of(ui.current_area(app))
        rumors = rivalry.rumors_in(bucket, area_id, day, RUMOR_DAYS)[:RUMOR_LIMIT]
        lines = [rivalry.format_text(RUMOR_NOTE, name=name, title=row.get("title"), days=ago)
                 for _quest_id, row, ago in rumors]
        return "\n".join(line for line in lines if line) or None

    modnpc.install(ctx, write=write)
    modnpc.register(OWNER, modnpc.ANY, notes=notes, write=write)

    # ------------------------------------------------------------ ロードの突き合わせ
    def reconcile(app):
        day = ui.game_day(app)
        with worlds.lock:
            key, bucket = ledger(app)
            if rivalry.rewind(bucket, day):
                write("reconcile: forgot what happened after day {} (older save)".format(day))
                worlds.save(key)
            rival = bucket.get("rival")
            if isinstance(rival, dict) and not rival.get("intro"):
                # 語りを書き終える前に閉じた。もう一度頼む。
                ask_intro(app, key, rival.get("id"))

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        """日が進んだ後に見回る。日数の進め方には触らない。"""
        result = orig(self, *args, **kwargs)
        try:
            tick(self, "days")
        except Exception:
            ctx.log_exc("rival adventurer: tick after elapse_days failed")
        return result

    @ctx.wrap("__main__:DisplayQuestChoice.__init__", required=False, safe=True)
    def board(orig, self, app, *args, **kwargs):
        """掲示板を開いた。ライバルが狙いを持っていなければ、この土地で付けさせる。"""
        try:
            day = ui.game_day(app)
            area_id = ui.area_id_of(ui.current_area(app))
            with worlds.lock:
                key, bucket = ledger(app)
                had_rival = isinstance(bucket.get("rival"), dict)
                if day is not None and rival_alive(app, bucket):
                    if settle(app, bucket, day) | aim(app, bucket, day, area_id, "board"):
                        worlds.save(key)
                elif had_rival:
                    worlds.save(key)
        except Exception:
            ctx.log_exc("rival adventurer: cannot aim at the board")
        result = orig(self, app, *args, **kwargs)
        try:
            screen.when_idle(app, lambda: tell_board(app), proceed_on_timeout=True,
                             tag="rival adventurer board")
        except Exception:
            ctx.log_exc("rival adventurer: cannot tell the board news")
        return result

    @ctx.wrap("__main__:DisplayQuestChoice.get_active_quest_count",
              required=False, safe=True)
    def active_quest_count(orig, self, *args, **kwargs):
        """隠した依頼を未完了の数から引く（引かないと『クエストを探す』が出ない）。"""
        result = orig(self, *args, **kwargs)
        if _int(result) is None:
            return result
        app = getattr(self, "app", None) or ui.find_app()
        hidden = hidden_here(app, ui.area_id_of(ui.current_area(app)))
        if not hidden:
            return result
        fixed = max(0, result - len(hidden))
        write("board: active quests {} -> {} (hidden {})".format(result, fixed, len(hidden)))
        return fixed

    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """依頼を片付けた。ライバルの狙っていた依頼ならプレイヤーの勝ち。
        ライバルがまだ居なければ抽選する。

        どの依頼が終わるのかは `orig` の前に読む（終わった後は片付いている。`318_` と同じ）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        quest_id = ui.current_quest_id(app) if app is not None else None
        result = orig(self, *args, **kwargs)
        try:
            if app is None:
                return result
            lines = []
            with worlds.lock:
                key, bucket = ledger(app)
                day = ui.game_day(app)
                had_rival = isinstance(bucket.get("rival"), dict)
                if rival_alive(app, bucket):
                    target = bucket.get("target")
                    failure = bucket.get("failure")
                    if quest_id is not None and isinstance(target, dict) \
                            and str(target.get("quest")) == quest_id:
                        name = rival_name(app, bucket)
                        lines.append(rivalry.format_text(WIN_ANNOUNCE, name=name))
                        lines.append(player_won(app, bucket, day, "quest end"))
                        worlds.save(key)
                    elif quest_id is not None and isinstance(failure, dict) \
                            and str(failure.get("quest")) == quest_id \
                            and not failure.get("cleaned"):
                        lines.extend(cleaned_up(app, bucket, failure, day))
                        worlds.save(key)
                elif day is not None and draw_rival(app, bucket, day):
                    worlds.save(key)
                    ask_intro(app, key, bucket["rival"]["id"])
                elif had_rival:
                    worlds.save(key)        # ライバルが居なくなった（台帳を空にした）
            lines = [line for line in lines if line]
            if lines:
                screen.when_idle(app, lambda: [screen.say(app, line) for line in lines],
                                 proceed_on_timeout=True, tag="rival adventurer win")
        except Exception:
            ctx.log_exc("rival adventurer: cannot score the quest end")
        return result

    def make_load(target):
        label = target.rsplit(".", 1)[-1]

        @ctx.wrap(target, required=False, safe=True)
        def on_load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            worlds.forget()
            store["reconcile"] = True
            write("{}: reconcile armed".format(label))
            return result

        return on_load

    for target in LOAD_TARGETS:
        make_load(target)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        """ロードの後の突き合わせと、掲示板の手当て。"""
        if store["reconcile"]:
            characters = getattr(getattr(self, "world", None), "characters", None)
            if isinstance(characters, dict) and len(characters) > 1:
                store["reconcile"] = False
                try:
                    reconcile(self)
                except Exception:
                    ctx.log_exc("rival adventurer: reconcile failed")
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list):
                fix_board(self, buttons)
        except Exception:
            ctx.log_exc("rival adventurer: cannot fix the quest board")
        return orig(self, *args, **kwargs)

    ctx.log("rival adventurer: installed (rival after {} clears at {}%, intro {}, "
            "failure {}, success {}%+{}%/Lv, due {}..{} days, state {})".format(
                RIVAL_AFTER_QUESTS, RIVAL_CHANCE_PERCENT,
                "llm" if INTRO_USE_LLM else "template",
                "on" if FAILURE_ENABLED else "off", SUCCESS_BASE_PERCENT,
                SUCCESS_SLOPE_PERCENT, DUE_DAYS_MIN, DUE_DAYS_MAX, worlds.dir_path()))
