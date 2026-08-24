# -*- coding: utf-8 -*-
"""機能追加: 手配されていると、土地を跨いで追手が来る。

遊ぶ側から見た仕様は MODS.md（`316_bounty_hunter`）。
ここに書くのは**なぜこの形にしたか**だけ。

##### 追手を発明しない（`enemy_type='guard'` を借りる）

ゲームには手配された者へ衛兵を差し向ける仕掛けが元からある
（GAME.md §2.20 / VERIFICATION_LOG.md §2.51）。

    BattleStartManager(app, 'guard', None) -> execute -> start_battle
      -> create_guard_enemies -> guard_npc_generator / generate_enemy_instance…

だからこの MOD がするのは「このマネージャを組んで `process_choice` に乗せる」だけ。
敵を自分で作らないので、`Character` の33項目もセーブへの書き込みも無い。

強さも同じで、`create_guard_enemies` の中から呼ばれる2箇所に届く**難易度の数1つ**を
差し替えるだけ。式は発明しない（レベルも能力値もゲームがそこから決める）。

##### 契機は「決める」だけ。起こすのは画面が整った合図の中

契機（土地・施設・日数・自由行動）は**どれも場面の途中**で来る。
土地の移動は `辿り着いた。` より前に `execute` が返り、
宿泊は部屋を選んだ後の日数送りで発火し、
自由行動は `in_free_input` が立ったまま呼ばれる。

以前は「手が空くのを待つ」ポーリングで凌いでいたが、
自由入力の後には推論・画像生成・場面移動が続くので、
**静かな瞬間は場面の途中にも現れる**。当て推量だった。

そこで契機では控え（`memo["due"]`）を置くだけにして、起こすのは

    InstantaleApp.refresh_choice_buttons     ← ゲームが選択肢を組み直した合図

の中だけにした。この入口は画面が変わるたびに1回来て、そのとき本文は流れ終わっていて、
並んでいる選択肢から**今どの場面か**も読める（実測。VERIFICATION_LOG.md §2.56）。
まだ場面の中なら控えを残し、次に画面が整ったときに出る。

##### 割り込まない相手

| 相手 | 見分け方 |
| --- | --- |
| 多段の場面（宿泊・訓練・賭博ほか） | 並んでいる選択肢のクラス名（`SEQUENCE_MARKS`）。旗でも手待ちでも捕まらない。`Display...Choice` は品書きなので数えない |
| 戦闘 | 旗と `current_enemy_dict`。戦闘中は控えすら置かない（置くと戦闘の直後に次の戦闘が始まる） |
| ゲーム自身の衛兵 | `BattleStartManager.__init__` を包み、自分が組んだぶん（`memo["building"]`）以外を数える。出たら控えを落とし、1回の遭遇として数える |

##### 作り替えるのは自分の戦闘の中だけ

難易度の差し替えも改名も、**自分が組んだマネージャの `start_battle` の中**でしか開かない
（`memo["phase"]` と同一のインスタンスか）。
以前は時間で開けていて、その窓の中でゲームが出した衛兵まで強くして改名していた。

改名は敵が揃った直後（最初の1手より前）。
鍵は戦闘中の1手ごとの識別に使われる（GAME.md §2.10）ので、
**入れ物は作り直さず** `clear` + `update` で中身だけ入れ替える。

##### 倒しても手配度は増やさない

ゲームは衛兵との戦闘の後にその土地の手配度を 10 下げる（手配の有無に関わらず）。
そのままだと追手が追手を呼ぶので、出す直前の値を控えて戻す（`KEEP_WANTED`）。

戻すのは**下がった側だけ**、**追手を出した土地だけ**。
下がる時機は一定ではない（戦闘の終わりの回と、それより後の回がある）ので、
戦闘の終わりと、その後に画面が整うたびに見る。逃げて終わった回もここで拾われる。

##### 押されていないぶんを補う

ボタンから入る戦闘では、押下の流れの中でゲームが選択肢を塗り直す。
こちらは押さずに起こすのでその1手が抜け、
選択肢の多い画面から入ると**右の欄（溢れたぶんを出す欄）が開いたまま**残り、
戦闘の情報欄と重なる。

`app.display_button_load(0)` を1回通す（座標も `opacity` も触らない）。
通すのは**選択肢が戦闘のものになった最初の合図**。
敵が揃った時点では早すぎて、読み込みが延びた回はまだ戦闘前の選択肢が並んでいる。

##### 時計を見ない

区切りは**合図の回数**と**ゲーム内の日数**だけ。
秒で区切ると、待っている間にゲームが何をしていたかと無関係に時間だけが過ぎる
（実機で踏んだ不具合はどれも「秒は経ったが場面は終わっていない」形だった）。

`Clock.schedule_once(..., 0)` を1回だけ使うが、これは待ちではなく次のフレームまで譲るもの。

##### 記録

`out/bounty_hunter_send.log`。来た回も、来なかった回とその理由も1行ずつ。
"""

import random

from instantale_modloader import ui
from instantale_modloader.state import world_filename, world_key

from . import hunt

LOG_BASENAME = "bounty_hunter_send.log"

# 通算日数と最後に追手が来た日の控え。世界ごとに1つ。
# セーブには書かない（`state/` は遊びの続きの置き場。TECH.md §3.11）。
STATE_DIRNAME = "bounty_hunter"

# ゲーム自身の衛兵の戦闘を指す語（実測。GAME.md §2.20）。
GUARD_ENEMY_TYPE = "guard"

# 寿命はどれも**画面が整った合図の回数**で数える（秒は数えない）。
DUE_MAX_SIGNALS = 20        # 決めたのに出せないまま過ぎたら捨てる
ARM_MAX_SIGNALS = 20        # 出したのに戦闘が始まらないまま過ぎたら控えを降ろす
PROTECT_MAX_SIGNALS = 10    # 倒したぶんの手配度を見張る間（長いと戦闘の外の罪まで戻す）

# `process_choice` に渡す文字列（押されたボタンの文字の代わり）。
# 画面のボタンとしては使わない（TECH.md §6.2）。
CHOICE_TEXT = "追手"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
START_WANTED = 20
START_TOTAL = 40
CHANCE_PERCENT = 30
COOLDOWN_DAYS = 10
DIFFICULTY_BASE = 20
DIFFICULTY_PER_WANTED = 1.0
DIFFICULTY_MAX = 75
ON_AREA_ARRIVAL = True
ON_FACILITY_ARRIVAL = False
ON_DAYS = True
ON_FREE_ACTION = False
ANNOUNCE = "首にかけられた賞金を目当てに、追手が行く手を塞いだ。"
HUNTER_NAME = "賞金稼ぎ"
KEEP_WANTED = True
YIELD_TO_GUARDS = True

# 追手を出せない状態。ゲーム自身の旗で、既に別の場面が進んでいることの合図。
# 全部は見ない（`in_shopping` はロード後に立ちっぱなしのことがある。GAME.md §2.7）。
BLOCKING_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
                  "in_conversation", "in_free_input")

# そのうち戦闘の旗。**戦闘中は控えすら置かない**
# （置くと、その戦闘が終わった瞬間に次の戦闘が始まる）。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")


# **多段の場面**の目印（選択肢のクラス名に含まれる語。GAME.md §2.2）。
# 宿泊のように旗でも手待ちでも捕まらない場面があり、
# そこへ割り込むと続きの場面が消える（実機で踏んだ）。
SEQUENCE_MARKS = (
    "Vacation", "Training", "Labor", "Imprisonment", "Trial", "Begging",
    "Colosseum", "HighLow", "Slot", "RussianRoulette",
    "Quest", "Battle", "Loot", "Conversation", "Utterance",
    "Employ", "Citizenship", "MedicalTreatment", "Reinforcement",
    "Enchantment", "Craft", "Shopping", "Execution", "SkillChoice",
    "FreeInput", "Epilogue", "GameOver", "DieFromOldAge",
)


def renamed_keys(keys, name):
    """`{古い鍵: 新しい鍵}`。連番は元の鍵から写す（実測は `衛兵1`〜`衛兵3`）。

    ぶつかる鍵は付け替えない（同じ鍵の敵が2体になるほうが害が大きい）。
    """
    if not name:
        return {}
    taken, mapping = set(keys), {}
    for order, key in enumerate(keys, start=1):
        text = str(key)
        digits = ""
        while text and text[-1].isdigit():
            digits, text = text[-1] + digits, text[:-1]
        new_key = name + (digits or str(order))
        if new_key == key or new_key in taken:
            continue
        taken.add(new_key)
        mapping[key] = new_key
    return mapping


def rename_enemies(enemies, name):
    """敵の鍵と名前を付け替える。付け替えた数を返す。

    **入れ物は作り直さない。**
    ゲーム側がこの辞書を握っているので、
    別の辞書に入れ替えると握っている先が古いままになる。
    """
    if not isinstance(enemies, dict) or not enemies or not name:
        return 0
    mapping = renamed_keys(list(enemies), name)
    if not mapping:
        return 0
    rebuilt = {}
    for key, character in enemies.items():
        try:
            character.name = name
        except Exception:
            pass                      # 名前が書けない敵も鍵だけは付け替える
        rebuilt[mapping.get(key, key)] = character
    enemies.clear()
    enemies.update(rebuilt)
    return len(mapping)


def sequence_in(names):
    """多段の場面の最中なら、その目印になったクラス名。無ければ `None`。

    `Display...Choice`（品書き）は数えない。
    数えると、宿屋やギルドに立っているだけで追手が来なくなる。
    """
    for name in names or ():
        if not name or name.startswith("Display"):
            continue
        if any(mark in name for mark in SEQUENCE_MARKS):
            return name
    return None


def sequence_on_screen(app):
    """今並んでいる選択肢から、多段の場面の最中かを見る。"""
    buttons = getattr(app, "buttons", None)
    if not isinstance(buttons, (list, tuple)):
        return None
    return sequence_in([ui.spec_cls_name(entry) for entry in buttons])


def fighting(app):
    """いま戦闘中か。理由の一覧を返す（空なら戦闘中ではない）。"""
    reasons = [name for name in BATTLE_FLAGS if getattr(app, name, False)]
    enemies = getattr(app, "current_enemy_dict", None)
    if isinstance(enemies, dict) and enemies:
        reasons.append("current_enemy_dict")
    return reasons


def battle_screen(app):
    """今並んでいるのが戦闘の選択肢か。

    敵が揃っていても選択肢がまだ戦闘のものでないことがある（読み込みが延びた回）。
    その時点で塗り直すと、戦闘前の選択肢の数で右の欄が決まってしまう。
    """
    names = [ui.spec_cls_name(entry)
             for entry in (getattr(app, "buttons", None) or [])]
    return any(name and "Battle" in name for name in names)


def blocked_by(app):
    """追手を出せない理由。空なら出せる。"""
    reasons = [name for name in BLOCKING_FLAGS if getattr(app, name, False)]
    enemies = getattr(app, "current_enemy_dict", None)
    if isinstance(enemies, dict) and enemies:
        reasons.append("current_enemy_dict")
    scene = sequence_on_screen(app)
    if scene:
        reasons.append("場面の最中({})".format(scene))
    return reasons


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="bounty hunter")
    # 「次のフレーム・メインスレッド」。ゲームの外ではその場で実行される（TECH.md §5.1.3）。
    schedule = ui.scheduler(ctx, "bounty hunter")

    # 控え。`world` はどの世界のぶんを読んでいるかで、切り替わったら読み直す。
    memo = {"world": None, "days": 0.0, "last": None,
            # 追手の1回ぶん。`phase` は自分が組んだマネージャそのもので、
            # ゲーム自身の衛兵と取り違えないための唯一の手がかり。
            "phase": None, "armed": None, "arm_signals": 0,
            "building": False, "inside": None,
            "protect": None,
            # 「出すと決まった」控え。起こすのは画面が整った合図の中。
            "due": None,
            # 戦闘の選択肢が並んだら1回だけ塗り直す、の印。
            "repaint": False}

    # ------------------------------------------------------------ 控えの出し入れ
    def store_path(app):
        return ctx.state_path(STATE_DIRNAME, world_filename(world_key(app), ".json"))

    def load_memo(app):
        """世界が変わっていたら読み直す。読めなければ 0 日から始める。"""
        key = world_key(app)
        if key == memo["world"]:
            return
        saved = ctx.read_json(store_path(app), {}) or {}
        memo["world"] = key
        memo["days"] = saved.get("days", 0.0) or 0.0
        memo["last"] = saved.get("last")
        write("控えを読んだ: 世界={} 通算={}日 前回={}".format(
            key, memo["days"], memo["last"]))

    def save_memo(app):
        ctx.write_json(store_path(app), {"days": memo["days"], "last": memo["last"]})

    # ------------------------------------------------------ 難易度の差し替え
    def arm(phase, difficulty):
        """この追手1回ぶんを開く。**目印は組んだマネージャそのもの**。"""
        memo["phase"] = phase
        memo["armed"] = difficulty
        memo["arm_signals"] = 0

    def in_flight():
        """追手を出したが、まだ敵が揃っていない状態か。

        数えるのは合図の回数。
        戦闘が始まっていればその合図には戦闘の画面が出ているので、
        ここで数が伸びるのは**始まらなかったとき**だけになる。
        """
        if memo["phase"] is None:
            return False
        if memo["arm_signals"] > ARM_MAX_SIGNALS:
            write("追手が出ないまま画面が{}回変わった。控えを降ろす".format(
                ARM_MAX_SIGNALS))
            disarm()
            return False
        return True

    def armed():
        """今この瞬間、差し替えてよい難易度。`None` なら触らない。

        **開くのは自分が組んだマネージャの `start_battle` の中だけ**（`memo["inside"]`）。
        時間で開けておくと、その間にゲーム自身が出した衛兵まで
        強くして名前まで変えてしまう。
        """
        return memo["inside"]

    def disarm():
        memo["phase"] = None
        memo["armed"] = None
        memo["inside"] = None

    # -------------------------------------------------- 倒したぶんを戻す
    def protect(app):
        """追手を出す直前の手配度を控える。戻す相手はこの土地1つだけ。"""
        memo["protect"] = None
        if not KEEP_WANTED:
            return
        player = getattr(app, "player", None)
        area_id = ui.area_id_of(ui.current_area(app))
        before = ui.lawfulness_of(ui.area_record(player, area_id))
        if before is None:
            write("手配度が読めない土地（{}）。倒したぶんは戻せない".format(area_id))
            return
        memo["protect"] = {"area": area_id, "value": before, "signals": 0}

    def restore(app, why):
        """下がっていたら控えの値へ戻す。戻したら True。

        **下がった側だけ**を戻す。
        戦闘の最中に手配が軽くなった（罰金を払った）のなら、それはこの MOD の話ではない。
        """
        guard = memo["protect"]
        if guard is None:
            return False
        if guard["signals"] > PROTECT_MAX_SIGNALS:
            memo["protect"] = None
            return False
        player = getattr(app, "player", None)
        entry = ui.area_record(player, guard["area"])
        now_value = ui.lawfulness_of(entry)
        if now_value is None or now_value >= guard["value"]:
            return False
        if ui.set_lawfulness(entry, guard["value"]):
            memo["protect"] = None          # 戻したら見張りは閉じる
            write("{}: 追手を倒したぶんの手配度を戻した 土地={} {} -> {}".format(
                why, guard["area"], now_value, guard["value"]))
            return True
        write("{}: 手配度を書き戻せなかった 土地={} {}".format(
            why, guard["area"], now_value))
        return False

    def count_signal():
        """合図が来たときに、控えの寿命を1つずつ進める。"""
        for key, field in (("due", "signals"), ("protect", "signals")):
            record = memo[key]
            if isinstance(record, dict):
                record[field] = record.get(field, 0) + 1
        if memo["phase"] is not None:
            memo["arm_signals"] += 1

    # ------------------------------------------------------------ 出すか決める
    def measure(app):
        """(今いる土地の重さ, 合計) を返す。"""
        by_area = ui.lawfulness_by_area(getattr(app, "player", None))
        here = hunt.area_weight(by_area, ui.area_id_of(ui.current_area(app)))
        return here, hunt.total_weight(by_area)

    def visit(trigger, app, enabled):
        """契機を1つ通す。**戻す側は契機が切ってあっても働く。**

        戦闘の終わりで捕まえ損ねた手配度は、次にここへ来たときに拾う
        （逃げて終わった回がこれで拾われる。見張っている1分の間だけ）。
        """
        if app is None:
            return
        load_memo(app)
        restore(app, trigger)
        if enabled:
            consider(trigger, app)

    def consider(trigger, app):
        """出すかどうかを決めるだけ。**ここでは起こさない。**

        起こすのはゲームが「選択肢を組み直した」と言ってきたとき
        （`refresh_choice_buttons`）。
        契機の瞬間はまだ本文も場面も動いている最中でありうる。
        """
        here, total = measure(app)
        if not hunt.should_send(here, total, START_WANTED, START_TOTAL):
            return
        battle = fighting(app)
        if battle:
            write("{}: {} なので出さない".format(trigger, ", ".join(battle)))
            return
        if in_flight():
            write("{}: 追手が既に向かっている。見送る".format(trigger))
            return
        if memo["due"] is not None:
            return
        if not hunt.ready(memo["days"], memo["last"], COOLDOWN_DAYS):
            write("{}: 手配 ここ{} 合計{} だが、前回から{:.0f}日（{}日空ける）".format(
                trigger, here, total, memo["days"] - (memo["last"] or 0),
                COOLDOWN_DAYS))
            return
        if random.random() * 100.0 >= CHANCE_PERCENT:
            write("{}: 手配 ここ{} 合計{} で抽選に外れた（発生率{}%）".format(
                trigger, here, total, CHANCE_PERCENT))
            return
        difficulty = hunt.difficulty_of(total, DIFFICULTY_BASE,
                                        DIFFICULTY_PER_WANTED, DIFFICULTY_MAX)
        memo["due"] = {"trigger": trigger, "here": here, "total": total,
                       "difficulty": difficulty, "signals": 0}
        write("{}: 追手が決まった。手配 ここ{} 合計{} 難易度{}。"
              "画面が整うのを待つ".format(trigger, here, total, difficulty))

    def repaint_buttons(app):
        """選択肢をゲーム自身の手で塗り直す。**座標は触らない。**

        右の欄（溢れた選択肢を出す欄）は戦闘の情報欄と同じ矩形を使っていて、
        欄の出し入れを決めているのは `display_button_load` の系統
        （VERIFICATION_LOG.md §2.59 / §2.60）。
        """
        loader = getattr(app, "display_button_load", None)
        if not callable(loader):
            write("display_button_load が無い。選択肢の塗り直しはできない")
            return
        try:
            loader(0)
        except Exception:
            ctx.log_exc("bounty hunter: 選択肢を塗り直せなかった")
            return
        write("選択肢を塗り直した（右の欄の畳み直し）")

    def standing_down(app):
        """起こすのをやめる理由。無ければ `None`。"""
        reasons = blocked_by(app)
        return ", ".join(reasons) if reasons else None

    def launch(app):
        """ゲームの衛兵の戦闘を起こす。**画面が整った合図の中からだけ呼ぶ。**"""
        due = memo["due"]
        if due is None:
            return
        if due["signals"] > DUE_MAX_SIGNALS:
            memo["due"] = None
            write("{}: 画面が{}回変わっても出せなかった。今回は出さない".format(
                due["trigger"], DUE_MAX_SIGNALS))
            return
        reason = standing_down(app)
        if reason:
            # まだ場面の中。控えは残すので、次に画面が整ったときに出る。
            return
        manager = ui.cls_of("BattleStartManager")
        if manager is None:
            memo["due"] = None
            write("BattleStartManager が見つからない。追手は出せない")
            return
        memo["due"] = None
        memo["building"] = True
        try:
            phase = manager(app, GUARD_ENEMY_TYPE, None)
        except Exception:
            ctx.log_exc("bounty hunter: BattleStartManager を組めなかった")
            return
        finally:
            memo["building"] = False
        arm(phase, due["difficulty"])
        protect(app)
        if ANNOUNCE.strip():
            screen.say(app, ANNOUNCE)
        if screen.start_phase(app, phase, CHOICE_TEXT):
            memo["last"] = memo["days"]
            save_memo(app)
            write("{}: 追手を出した。手配 ここ{} 合計{} 難易度{} "
                  "（通算{:.0f}日目）".format(
                      due["trigger"], due["here"], due["total"],
                      due["difficulty"], memo["days"]))
        else:
            disarm()
            write("{}: 戦闘を起こせなかった。控えを降ろした".format(due["trigger"]))

    # ------------------------------------------------- 難易度を差し替える2箇所
    @ctx.wrap("scripts.llm.llm_manager:guard_npc_generator", required=False,
              safe=True)
    def guard_npc_generator(orig, area=None, world=None, npc_difficulty_level=None,
                            *args, **kwargs):
        """敵の姿と説明。難易度は文章の強さ（プロンプト）に効く。"""
        difficulty = armed()
        if difficulty is not None and _is_number(npc_difficulty_level):
            write("難易度 {} -> {}（姿と説明）".format(npc_difficulty_level, difficulty))
            npc_difficulty_level = difficulty
        return orig(area, world, npc_difficulty_level, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.generate_enemy_instance_from_quest_dict",
              required=False, safe=True)
    def enemy_instance(orig, self, enemy_dict=None, *args, **kwargs):
        """敵の実体。難易度からレベルと能力値が決まる。"""
        difficulty = armed()
        if difficulty is not None:
            index = _difficulty_index(args)
            if index is None:
                write("難易度の位置が分からない（引数 {}件）。素のまま作らせる".format(
                    len(args)))
            else:
                args = args[:index] + (difficulty,) + args[index + 1:]
        return orig(self, enemy_dict, *args, **kwargs)

    @ctx.wrap("__main__:BattleStartManager.__init__", required=False, safe=True)
    def battle_start_init(orig, self, app=None, enemy_type=None, enemy_content=None,
                          *args, **kwargs):
        """**ゲーム自身が衛兵を出したこと**を知る唯一の入口。

        自分が組んだぶんは `memo["building"]` で見分ける
        （この時点では `memo["phase"]` にまだ入っていない）。
        """
        result = orig(self, app, enemy_type, enemy_content, *args, **kwargs)
        if not memo["building"] and YIELD_TO_GUARDS:
            # **1回の遭遇として数える。**
            # 秒で猶予を置くのをやめ、追手と同じクールダウン（ゲーム内の日数）に乗せた。
            # ゲームの衛兵と戦った直後に追手が来るのは、
            # プレイヤーから見れば「連続で2回襲われた」で、出どころの違いは見えない。
            target = app if app is not None else ui.find_app()
            if target is not None:
                load_memo(target)
                memo["last"] = memo["days"]
                memo["due"] = None
                save_memo(target)
            write("ゲーム自身が戦闘を起こした（{!r}）。1回の遭遇として数える"
                  "（次は{}日後から）".format(enemy_type, COOLDOWN_DAYS))
        return result

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        """敵が揃ったら名前を付け替える。**難易度を開くのはこの中だけ**。

        ここが最初の1手より前の唯一の足場で、
        `create_guard_enemies` はこの中から呼ばれる（実測）。
        自分が組んだマネージャかどうかで見分けるので、
        ゲーム自身の衛兵を強くしたり改名したりすることはない。
        """
        ours = memo["phase"] is not None and self is memo["phase"]
        if not ours:
            return orig(self, *args, **kwargs)
        memo["inside"] = memo["armed"]
        try:
            result = orig(self, *args, **kwargs)
        finally:
            memo["inside"] = None
        app = getattr(self, "app", None) or ui.find_app()
        enemies = getattr(app, "current_enemy_dict", None)
        renamed = rename_enemies(enemies, HUNTER_NAME.strip())
        write("追手が出そろった: {}{}".format(
            list(enemies) if isinstance(enemies, dict) else "?",
            "（{}体を{}に改名）".format(renamed, HUNTER_NAME.strip())
            if renamed else ""))
        disarm()
        memo["repaint"] = True      # 塗り直すのは選択肢が戦闘のものになってから
        return result

    @ctx.wrap("__main__:BattleEndManager.end_phase", required=False, safe=True)
    def battle_end(orig, self, *args, **kwargs):
        """戦闘の終わり。ここと、その数秒後の2回だけ手配度を見る。

        ゲームがどの時点で下げるのかは測っていないので、
        **時機を1点に決め打ちしない**（`end_phase` の中で下がるなら1回目で、
        後の段で下がるなら2回目で戻る）。
        """
        result = orig(self, *args, **kwargs)
        memo["repaint"] = False
        if memo["protect"] is not None:
            restore(getattr(self, "app", None) or ui.find_app(), "戦闘終了")
        return result

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False,
              safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        """**画面が整った合図**。追手を起こすのはここだけ（VERIFICATION_LOG.md §2.56）。

        `set_buttons_to_normal` は1〜2秒早く、まだ本文を流している最中なので使わない。
        """
        result = orig(self, *args, **kwargs)
        count_signal()
        if memo["repaint"] and battle_screen(self):
            memo["repaint"] = False
            schedule(lambda: repaint_buttons(self))
        if memo["protect"] is not None:
            # 戦闘の終わりで捕まえ損ねた手配度をここで拾う
            # （下がる時機が一定でないため。逃げて終わった回もここに来る）。
            restore(self, "画面が整った")
        if memo["due"] is not None:
            # 1フレーム置く。今はゲームが選択肢を描き終えた直後で、
            # ここから `process_choice` に入ると同じ流れの中で画面を2度触ることになる。
            schedule(lambda: launch(self))
        return result

    # ------------------------------------------------------------ 出す場面（4つ）
    @ctx.wrap("__main__:AreaMoveManager.execute", required=False, safe=True)
    def area_arrival(orig, self, choice_text=None, *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            visit("到着(土地)", getattr(self, "app", None) or ui.find_app(),
                  ON_AREA_ARRIVAL)
        except Exception:
            ctx.log_exc("bounty hunter: 土地への到着で失敗")
        return result

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def facility_arrival(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            visit("到着(施設)", getattr(self, "app", None) or ui.find_app(),
                  ON_FACILITY_ARRIVAL)
        except Exception:
            ctx.log_exc("bounty hunter: 施設への到着で失敗")
        return result

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days=None, *args, **kwargs):
        """暦を数えるのはここだけ。`ON_DAYS` が OFF でも数は数える。"""
        result = orig(self, days, *args, **kwargs)
        try:
            load_memo(self)
            if _is_number(days) and days > 0:
                memo["days"] += float(days)
                save_memo(self)
            visit("日数経過", self, ON_DAYS)
        except Exception:
            ctx.log_exc("bounty hunter: 日数の経過で失敗")
        return result

    @ctx.wrap("scripts.llm.llm_manager:master_ai_facilitator", required=False,
              safe=True)
    def free_action(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            visit("自由行動", ui.find_app(), ON_FREE_ACTION)
        except Exception:
            ctx.log_exc("bounty hunter: 自由行動で失敗")
        return result

    # ------------------------------------------------------------------ 自己検証
    # 実経路は手配されるまで通らないので、条件と難易度だけ先に確かめる。
    cases = (
        # (ここの重さ, 合計, 出すか, 難易度)
        (0, 0, False, 20),
        (20, 20, True, 40),
        (19, 19, False, 39),        # どちらの条件にも届かない
        (0, 40, True, 60),          # 手配されていない土地でも合計で来る
        (0, 500, True, 75),         # 上限で頭打ち
    )
    failures = []
    for here, total, expected, difficulty in cases:
        got = (hunt.should_send(here, total, 20, 40),
               hunt.difficulty_of(total, 20, 1.0, 75))
        if got != (expected, difficulty):
            failures.append((here, total, got))
    scenes = (
        # (画面に並ぶクラス名, 場面の最中か)
        (["MovePhaseManager", "DisplayVacationChoice"], False),
        (["VacationStartManager", "MovePhaseManager"], True),
        (["VacationRestManager", "VacationTrainManager"], True),
        (["DisplayTrainingChoice", "DisplayQuestChoice"], False),
        ([None, ""], False),
    )
    for names, expected in scenes:
        if bool(sequence_in(names)) != expected:
            failures.append(("sequence", names, sequence_in(names)))

    sample = {"衛兵1": type("E", (), {})(), "衛兵2": type("E", (), {})()}
    held = sample
    renamed = rename_enemies(sample, "賞金稼ぎ")
    if (renamed != 2 or sorted(sample) != ["賞金稼ぎ1", "賞金稼ぎ2"]
            or sample is not held):
        failures.append(("rename", sorted(sample), renamed))
    if failures:
        ctx.log("VERIFY FAILED: hunt {}".format(failures), level="ERROR")
    else:
        ctx.log("verified: should_send / difficulty_of on {} cases / rename / "
                "sequence on {} cases".format(len(cases), len(scenes)))

    ctx.log("bounty hunter installed; 条件 ここ{}/合計{} 発生率{}% 難易度{}+{}×重さ"
            "(上限{}) 場面={} 名前={} 倒しても手配は増やさない={} log={}".format(
                START_WANTED, START_TOTAL, CHANCE_PERCENT, DIFFICULTY_BASE,
                DIFFICULTY_PER_WANTED, DIFFICULTY_MAX,
                [name for name, on in (("土地", ON_AREA_ARRIVAL),
                                       ("施設", ON_FACILITY_ARRIVAL),
                                       ("日数", ON_DAYS),
                                       ("自由行動", ON_FREE_ACTION)) if on],
                HUNTER_NAME, KEEP_WANTED,
                ctx.out_path(LOG_BASENAME)))


def _is_number(value):
    """数として読める値か。`bool` は弾く（`True` を1日・難易度1にしない）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _difficulty_index(args):
    """残りの引数のうち難易度の位置。

    実測の並びでは難易度だけが数なので、**数が1つだけならそれを採る**
    （引数が増減しても追随する）。複数あるときだけ実測の位置（5番目）に戻る。
    """
    numbers = [index for index, value in enumerate(args) if _is_number(value)]
    if len(numbers) == 1:
        return numbers[0]
    if 4 in numbers:
        return 4
    return None
