# -*- coding: utf-8 -*-
"""処刑場からの脱出。死刑の判決の後、好感度の高い人物の手引きでダンジョンに挑む。仕様は DOC.md「処刑場からの脱出」。

決めた仕様（2026-10-05）:
- 起きるのは、プレイヤーへの好感度が高い人物（仲間・知人）がいるときだけ
- 方法はダンジョン（ゲーム自身の依頼の生成で作る）。難易度は「土地の難易度とパーティの平均レベルの高いほう ＋ 10」で、
  クリアを前提にしない。上限はゲームの難易度の上限（`QUEST_DIFFICULTY_VALUE_MAX`）
- 同行するのは手引きした人物だけ（捕まった時点で仲間は解散している。`custody`）。手引きした人物をパーティに入れてから作る
- 撤退すれば処刑（ゲームオーバー）。戦闘で全滅すれば、ゲーム自身のゲームオーバー

流れ:
    死刑の判決の画面（「嫌だ！」＝ `ExecutionPhaseManager`）に「〈名前〉の手引きに賭ける」を足す
    → 押すと、手引きした人物をパーティに入れ、依頼の生成に脱出の指示と難易度を差し込んで1件作り
      （`office` と同じ口。`env.quest_inject`）、受注画面を通さず `QuestStartManager(app, 'settlement_quest', id)` で始める
      （GAME.md §2.9 の進行ループ）。生成に渡した難易度は LLM への頼みの数字にしか効かず、依頼の記録はゲームが決めた値になる
      （2026-10-21 の実機。82 を渡して記録は 4、敵は HP 160）。だから作った直後と始める直前に、依頼の記録へ書き直す（`307_` と同じ形）
    → 帰還（`QuestEndManager`）: 外へ逃れた。報酬は出さず（本文の行を止め、入った額を引く）、捕まった土地の手配度を下げる
    → 撤退（`QuestRetireManager`）: 処刑（`ExecutionPhaseManager`）
控えは `state\\crime_overhaul\\<世界×主人公>.json` の `rescue`（依頼の id・土地・手引きした人）。終われば消す。
"""
import sys
import time

from instantale_modloader import choices, npcs, ui

from . import common, rules, underworld

DEATH_SPEC = "ExecutionPhaseManager"
#: MOD の人物の id の頭。パーティに入れられないので手引き役に選ばない。
MOD_NPC_PREFIX = "mod:"
#: ゲームの依頼の難易度の上限（`scripts.functions.QUEST_DIFFICULTY_VALUE_MAX`）が引けないときの値（`318_` と同じ）。
DIFFICULTY_MAX = 76
JOIN_TEXT = "{name}が駆け付け、行動を共にする。"
QUEST_START_SPEC = "QuestStartManager"
QUEST_TYPE = "settlement_quest"
LABEL = "{name}の手引きに賭ける"
LABEL_HEAD = "の手引きに賭ける"
OFFER_TEXT = "処刑の前夜、{name}が牢番を眠らせ、鍵を差し入れてきた。\n「今しかない。処刑場を抜ければ外だ」"
LOOKING_TEXT = "{name}が手引きの段取りを囁いている……"
FAILED_TEXT = "手引きは間に合わなかった。"
FREE_TEXT = "{name}の手引きで処刑場の囲みを破り、外へ逃れた。"
LAW_TEXT = "{town}の官憲が、逃亡した死刑囚を追い始めた。（手配度 {before} → {after}）"
RETIRE_TEXT = "追い詰められ、再び捕らえられた。"
NO_REWARD_TEXT = "（逃亡者に報酬を払う者はいない）"

BRIEF_TEXT = (
    "\n\n【処刑場からの脱出】この依頼は、死刑を言い渡されたプレイヤーが処刑の前夜に牢を抜け出し、"
    "処刑場と周りの監獄を突破して外へ逃れる脱出行として作ること。"
    "手引きしたのは{name}。依頼人（client_name）は{name}とし、依頼人の言葉は逃亡を急かす短い囁きにすること。"
    "舞台は{town}の監獄と処刑場。enemies には看守・処刑場の兵・追手の衛兵などを、boss には処刑人か典獄長を立てること。"
    "quest_title は脱出行らしい名前にすること。報酬の話はしないこと。"
)


affinity_of = rules.affinity_of


def rescuer_of(app, threshold):
    """好感度が `threshold` 以上でいちばん高い人物 `(id, 名前, 好感度)`。いなければ None。"""
    characters = getattr(getattr(app, "world", None), "characters", None)
    best = None
    if not isinstance(characters, dict):
        return None
    for npc_id, character in list(characters.items()):
        # 死の印はゲームの人物では `config['is_dead']`（GAME.md §2.22）。`is_dead` の属性は modnpc の人物だけが持つ。
        config = getattr(character, "config", None)
        if str(npc_id) == "player" or str(npc_id).startswith(MOD_NPC_PREFIX) \
                or getattr(character, "is_dead", False) or (isinstance(config, dict) and config.get("is_dead")):
            continue
        value = affinity_of(character)
        name = getattr(character, "name", None)
        if value is None or value < threshold or not name:
            continue
        if best is None or value > best[2]:
            best = (str(npc_id), name, value)
    return best


def party_level(app):
    """主人公と仲間のレベルの平均。読めなければ None。"""
    levels = []
    player = getattr(app, "player", None)
    for character in [player] + [ui.character_of(app, member) for member in ui.party_member_ids(app)]:
        level = getattr(character, "experience_level", None)
        if isinstance(level, (int, float)) and not isinstance(level, bool):
            levels.append(level)
    return int(round(sum(levels) / len(levels))) if levels else None


def difficulty_max():
    """ゲームの依頼の難易度の上限。引けなければ `DIFFICULTY_MAX`。"""
    value = getattr(sys.modules.get("scripts.functions"), "QUEST_DIFFICULTY_VALUE_MAX", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else DIFFICULTY_MAX


def rescue_difficulty(area_difficulty, level, plus, ceiling=DIFFICULTY_MAX):
    """「土地の難易度とパーティの平均レベルの高いほう ＋ plus」を `ceiling` で止めた値。どちらも読めなければ None。"""
    known = [value for value in (area_difficulty, level) if isinstance(value, (int, float))]
    return min(int(ceiling), int(max(known)) + int(plus)) if known else None


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg
    #: 帰還の報酬を止める窓（`QuestEndManager.execute` の間だけ）。
    ending = {"on": False}

    def load_rescue(app):
        with worlds.lock:
            value = worlds.load(worlds.playthrough(app)).get("rescue")
        return dict(value) if isinstance(value, dict) else None

    def is_ours(app, rescue):
        """いま進めている依頼がこの脱出か。

        依頼の id はゲームオーバーの後に同じ名前で作り直した周回で使い回されうるので、題も見る
        （全滅して控えが残ったまま、別の依頼に脱出の帰還が当たらないように）。
        """
        if rescue is None or str(ui.current_quest_id(app)) != str(rescue.get("quest")):
            return False
        title = rescue.get("title")
        return not title or ui.quest_value(
            ui.quest_of(app, str(rescue.get("quest"))), "quest_title", "") == title

    def save_rescue(app, value):
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            if value:
                bucket["rescue"] = value
            else:
                bucket.pop("rescue", None)
            worlds.save(playthrough)

    #: 撤退の後の処刑を予約したか（同じプロセスで二重に予約しない）。
    pending = {"execution": False}

    def execute_retired(app):
        """撤退した脱出の処刑を起こす。控えの印は処刑を起こす直前に消す。

        印は撤退からここまで控えに残す。撤退の語りの間にゲームを閉じて読み直しても、
        読み直した後の最初の組み直しで、ここをもう一度通す（`on_refresh`）。
        """
        pending["execution"] = False
        rescue = load_rescue(app)
        if not rescue or not rescue.get("retired"):
            return
        cls = ui.cls_of(DEATH_SPEC)
        if cls is None:
            write("WARN rescue: ExecutionPhaseManager is not available")
            return
        save_rescue(app, None)
        screen.say(app, RETIRE_TEXT)
        screen.start_phase(app, cls(app), "嫌だ！")

    def schedule_execution(app):
        if pending["execution"]:
            return
        pending["execution"] = True
        screen.when_idle(app, lambda: execute_retired(app), proceed_on_timeout=True, tag="rescue execution")

    # ---------------------------------------------------- 死刑の画面に手を足す
    def on_refresh(app, buttons):
        if not cfg.RESCUE_ENABLED:
            return
        rescue = load_rescue(app)
        if rescue and rescue.get("retired"):
            # 撤退の後、処刑の前にゲームを閉じて読み直した。処刑を起こし直す
            write("rescue: the execution after the retreat from quest {} is still due".format(rescue.get("quest")))
            schedule_execution(app)
            return
        if not any(ui.spec_cls_name(entry) == DEATH_SPEC for entry in buttons or []):
            return
        # 前の版がセーブに焼いた印の無い自前のボタン（文言の末尾で見分ける。頭は手引きする人の名前で変わる）を外す。
        buttons[:] = [entry for entry in buttons
                      if not (isinstance(entry, dict) and not screen.marked_by_a_mod(entry)
                              and ui.spec_cls_name(entry) == ui.SAFE_CLS
                              and str(entry.get("text") or "").endswith(LABEL_HEAD))]

    def offer(app, view):
        """死刑の判決の画面に「〈名前〉の手引きに賭ける」を足す（好感度の高い人物が居るときだけ）。"""
        if not cfg.RESCUE_ENABLED or not any(ui.spec_cls_name(entry) == DEATH_SPEC for entry in view.buttons):
            return None
        rescue = load_rescue(app)
        if rescue and rescue.get("retired"):
            return None
        found = rescuer_of(app, cfg.RESCUE_AFFINITY)
        if found is None:
            return None
        return [choices.Choice(LABEL.format(name=found[1]), on_press=press)]

    class RescuePhase(object):
        """自前のフェーズ（依頼の生成は重いので、ゲームと同じく別スレッドで最後までやる）。"""

        def __init__(self, app):
            self.app = app

        def execute(self, choice_text):
            try:
                make_rescue(self.app)
            except Exception:
                ctx.log_exc("crime incentive: the rescue failed")
                if screen.is_busy():
                    screen.busy_off(self.app)

    def make_rescue(app):
        found = rescuer_of(app, cfg.RESCUE_AFFINITY)
        display_cls = ui.cls_of("DisplayQuestChoice")
        start_cls = ui.cls_of(QUEST_START_SPEC)
        if found is None or display_cls is None or start_cls is None:
            write("WARN rescue: cannot start (rescuer {} generator {} start {})".format(
                found, display_cls is not None, start_cls is not None))
            choices.repaint(app)
            return
        npc_id, name, affinity = found
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        town = getattr(area, "name", None) or "この街"
        screen.busy_on(app)
        screen.say(app, OFFER_TEXT.format(name=name))
        joined = join(app, npc_id, area_id)
        if joined:
            screen.say(app, JOIN_TEXT.format(name=name))
        was_in_custody = env.in_custody(app)
        env.set_custody(app, True, "rescue")        # 外へ逃れるまで牢の中（`custody`）

        def undo(why):
            """依頼を作れなかった・始められなかったとき、入れた手引き役と牢の印を戻す。"""
            if joined and env.send_back(app, npc_id) is not None:
                screen.schedule(lambda: screen.paint_party(app))
            if not was_in_custody:
                env.set_custody(app, False, why)

        difficulty = rescue_difficulty(env.area_difficulty(app), party_level(app),
                                       cfg.RESCUE_DIFFICULTY_PLUS, difficulty_max())
        env.quest_inject.update(brief=BRIEF_TEXT.format(name=name, town=town),
                                difficulty=difficulty, at=time.monotonic(), tag="rescue")
        screen.say(app, LOOKING_TEXT.format(name=name))
        before = set(ui.quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("crime incentive: generating the rescue failed")
        finally:
            env.quest_inject.update(brief=None, difficulty=None)
        added = sorted(set(ui.quest_ids(app)) - before, key=ui.id_sort_key)
        screen.busy_off(app, restore=False)
        if not added:
            write("rescue: no quest was generated")
            undo("rescue not generated")
            screen.say(app, FAILED_TEXT)
            choices.repaint(app)
            return
        quest_id = added[-1]
        write_difficulty(app, quest_id, difficulty, "generated")
        title = ui.quest_value(ui.quest_of(app, quest_id), "quest_title", "")
        save_rescue(app, {"quest": quest_id, "area": area_id, "name": name, "title": title,
                          "client": str(npc_id)})
        write("rescue: {} (affinity {}) leads the escape; quest {} {!r} difficulty {}".format(
            name, affinity, quest_id, ui.quest_value(ui.quest_of(app, quest_id), "quest_title", ""),
            difficulty))

        def start():
            write_difficulty(app, quest_id, difficulty, "start")   # 作った直後は記録の片方にまだ無いことがある
            try:
                phase = start_cls(app, QUEST_TYPE, str(quest_id))
            except Exception:
                ctx.log_exc("crime incentive: QuestStartManager failed for the rescue")
                save_rescue(app, None)
                undo("rescue not started")
                choices.repaint(app)
                return
            screen.start_phase(app, phase, "受ける")
        screen.when_idle(app, start, proceed_on_timeout=True, tag="rescue start")

    def join(app, npc_id, area_id):
        """手引きした人物をパーティに入れる（同房の囚人の加勢と同じ手順）。入れたら真。"""
        if npc_id in ui.party_member_ids(app):
            return False
        character = ui.character_of(app, npc_id)
        # 人物の `current_area` は土地の実体のことがある（id の文字列のこともある。実機）
        current = getattr(character, "current_area", None)
        if current is not None and not isinstance(current, (str, int)):
            current = ui.area_id_of(current)
        home = str(current or area_id)
        area = ui.world_areas(app).get(home)
        if area is not None:
            # 仲間は冒険者の一覧に載っていることが前提（GAME.md §2.23）。既に載っていれば何もしない
            npcs.enroll(app, area, home, npc_id)
        add = getattr(app, "add_party_member", None)
        if not callable(add):
            write("WARN rescue: add_party_member is not available")
            return False
        add(npc_id)
        # ここはワーカースレッド。塗り直しはメインスレッドへ回す（ここで塗ると顔の絵が読み込まれず、枠が空のままだった。実機）
        screen.schedule(lambda: screen.paint_party(app))
        write("rescue: {} joined the party (enrolled at area {})".format(npc_id, home))
        return True

    def write_difficulty(app, quest_id, difficulty, when):
        """依頼の記録の難易度を書き直す（敵の強さはこの値で決まる。生きた一覧とセーブの両方）。"""
        if not isinstance(difficulty, int):
            return
        before = ui.quest_value(ui.quest_of(app, str(quest_id)), "difficulty", None)
        written = ui.set_quest_value(app, str(quest_id), "difficulty", difficulty)
        write("rescue: quest {} difficulty {!r} -> {} at {} ({} store(s))".format(
            quest_id, before, difficulty, when, written))

    def press(app):
        write("pressed the rescue")
        screen.start_phase(app, RescuePhase(app), LABEL_HEAD, fallback=lambda: make_rescue(app))
        return None

    # ---------------------------------------------------- 終わり方
    def drop_reward(context):
        """脱出の帰還の間だけ、報酬の行を止める（払った額は後で引く）。"""
        if ending["on"] and underworld.reward_amount(context):
            return context, True
        return context, False

    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        rescue = load_rescue(app) if app is not None else None
        if not is_ours(app, rescue):
            return orig(self, *args, **kwargs)
        before_gold = ui.gold_of(app)
        ending["on"] = True
        try:
            return orig(self, *args, **kwargs)
        finally:
            ending["on"] = False
            try:
                set_free(app, rescue, before_gold)
            except Exception:
                ctx.log_exc("crime incentive: cannot settle the rescue")

    def set_free(app, rescue, before_gold):
        after_gold = ui.gold_of(app)
        if isinstance(before_gold, int) and isinstance(after_gold, int) and after_gold > before_gold:
            ui.add_gold(app, before_gold - after_gold)
        save_rescue(app, None)
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            # 片付いた後も、手引き役と表に出さない依頼の印を残す（`office` の `client_rows` / `kept_off`）。
            common.remember_past(worlds.load(playthrough), rescue.get("quest"), rescue.get("title"),
                                 client=rescue.get("client"), name=rescue.get("name"))
            worlds.save(playthrough)
        lines = [FREE_TEXT.format(name=rescue.get("name") or "誰か"), NO_REWARD_TEXT]
        area_id = str(rescue.get("area") or "")
        entry = ui.area_record(getattr(app, "player", None), area_id)
        before = ui.lawfulness_of(entry)
        loss = max(0, int(cfg.RESCUE_LOSS))
        if before is not None and loss and ui.set_lawfulness(entry, before - loss):
            town = getattr(ui.world_areas(app).get(area_id), "name", None) or "この街"
            lines.append(LAW_TEXT.format(town=town, before=before, after=before - loss))
        write("rescue: escaped (quest {}); gold {} -> {} kept {}".format(
            rescue.get("quest"), before_gold, after_gold, before_gold))

        def tell():
            env.refresh_gold(app)
            for line in lines:
                screen.say(app, line)
        screen.when_idle(app, tell, proceed_on_timeout=True, tag="rescue free")
        env.prison_event("exit", app, how="rescue")     # 自分の本文を予約した後に知らせる（受ける側の本文が後に出る）

    @ctx.wrap("__main__:QuestRetireManager.execute", required=False, safe=True)
    def quest_retire(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        rescue = load_rescue(app) if app is not None else None
        if not is_ours(app, rescue):
            return orig(self, *args, **kwargs)
        result = orig(self, *args, **kwargs)
        save_rescue(app, dict(rescue, retired=True))
        write("rescue: retreated from quest {}; the execution goes ahead".format(rescue.get("quest")))
        schedule_execution(app)
        return result

    env.on_refresh(on_refresh)
    env.on_offer(offer)
    env.on_text(drop_reward)
