# -*- coding: utf-8 -*-
"""機能追加: 手配されていると、土地を跨いで追手が来る。

##### 何が起きるか

手配されたまま旅を続けると、賞金を目当てにした追手が行く手を塞ぐ。

    [陽光の砦に着いた]
    首にかけられた賞金を目当てに、追手が行く手を塞いだ。
                    ↓
    ゲーム本来の戦闘（衛兵3体。強さは手配の重さで決まる）

出てくるのは**ゲーム自身の衛兵の戦闘**そのもので、この MOD は起こす場面と強さだけを決める。
戦闘・戦利品・要約（`guard_battle_summarizer`）はゲームのまま。

##### 追手を発明しない（`enemy_type='guard'` を借りる）

`220_probe_bounty_hunter` が実機で全段を録った（GAME.md §2.20 / VERIFICATION_LOG.md §2.51）。

```
BattleStartManager(app, 'guard', None) -> execute -> start_battle -> create_guard_enemies
    guard_npc_generator(area, world, 難易度)                   敵の姿と説明を1件
    generate_enemy_instance_from_quest_dict(..., 難易度) × 3    敵の実体
```

だから MOD がすることは「このマネージャを組んで `process_choice` に乗せる」だけになる。
敵を自分で作らないので、`Character` の33項目もセーブへの書き込みも一切無い
（**この MOD はセーブに何も足さない**。追手が残す痕跡はゲーム自身の戦闘の記録だけ）。

##### 強さは難易度の数1つで渡す

実測では難易度 20 でレベル 21 の敵3体だった。
難易度は `get_enemy_exp_lvl` と `get_enemy_attributes_base_point` の両方に渡り、
レベルと能力値の両方を決める。
つまり**式を発明せず、数を1つ差し替えるだけでスケーリングになる**。

差し替えるのは `create_guard_enemies` の中から呼ばれる2箇所で、
**この MOD が起こした戦闘の間だけ**（`memo["armed"]`）。
ゲーム自身が出した衛兵には触らない。
起こし損ねたときのために、画面が何度か組み直されたら差し替えは自然に降りる
（`ARM_MAX_SIGNALS`。秒では数えない）。

##### 追手の名前を付け替える

名前を付けているのは `create_guard_enemies` の側で、
`EnemyData` に名前の項目は無い（実測）。
だから**敵が揃った直後に付け替える**。

    app.current_enemy_dict = {'衛兵1': …}  ->  {'賞金稼ぎ1': …}
    Character.name         = '衛兵'        ->  '賞金稼ぎ'

鍵は戦闘中の1手ごとの識別に使われる（GAME.md §2.10）ので、
**入れ物そのものは作り直さない**（`clear` + `update` で同じ辞書のまま中身を入れ替える）。
ゲーム側がこの辞書を握っていても、握っている先が古い辞書になることはない。

付け替えるのは `start_battle` が返った直後、
つまり**敵が揃ってから最初の1手より前**。
この MOD が起こした戦闘のときだけで、ゲーム自身が出した衛兵は `衛兵` のまま。

##### 倒しても手配度は下がらない

ゲーム自身の衛兵と戦うと、その土地の手配度がさらに下がる
（実測: 戦闘前 `-10` → 戦闘後 `-20`。VERIFICATION_LOG.md §2.51）。
追手は「手配されたから来た」側なので、
それを倒すたびに手配が重くなると**追手が追手を呼ぶ**。

そこで、追手を出す直前にその土地の手配度を控えておき、
戦闘が終わった後に**下がっていたら控えの値へ戻す**（`KEEP_WANTED`）。

- 戻すのは**下がった側だけ**。戦闘中に手配が軽くなった（`309_` で罰金を払った）なら触らない
- 戻すのは**追手を出した土地の1つだけ**。よその土地の手配度には触らない
- 見張るのは**画面が整った合図を数回ぶん**（`PROTECT_MAX_SIGNALS`）。
  それを過ぎれば戻さない（戦闘の外で起きた手配の増減まで巻き戻さないため）

どこで下がるのかは測っていない（`BattleEndManager.end_phase` の前後のどこか）。
しかも**下がる時機は一定ではない**。
実測では戦闘の終わりの直後に下がった回と、それより後に下がった回がある
（逃げて終わった回を取りこぼしていた）。
だから時機を決め打ちせず、**戦闘の終わりと、その後に画面が整うたび**に見る。
戻せたらそこで見張りを閉じ、数回ぶんで諦める。

##### ゲーム自身の衛兵に先を譲る

**ゲームにも手配された者へ衛兵を差し向ける仕掛けがある**（実測。GAME.md §2.20）。
そちらもこちらと同じ操作（施設への到着など）から出てくるので、
同じ場面で両方が起きるとゲーム側のイベントが潰れる（実機で踏んだ）。

譲り方は3つ。

| 手 | 何をするか |
| --- | --- |
| 場面の外で起こす | 起こすのは画面が整った合図の中だけ。ゲーム側の衛兵が先に動いていれば、そのときには旗か場面で見える |
| 出た後は控える | ゲームが衛兵を出したら**1回の遭遇として数える**（追手と同じクールダウンに乗せる。秒では猶予を置かない） |
| 取り違えない | 難易度の差し替えと改名は**自分が組んだマネージャの `start_battle` の中だけ**（`memo["phase"]` と同一のインスタンスか）。時間で開けておくと、その間にゲームが出した衛兵まで作り替えてしまう |

見分けは `BattleStartManager.__init__` を包んで、
自分が組んだぶん（`memo["building"]`）以外をゲームのぶんとして数える。

##### 多段の場面には割り込まない

宿泊は1回の操作では終わらない。

    部屋を選ぶ  ->  日数が進む  ->  休養 / 訓練 / 交流  ->  終わる

**日数が進むのはこの途中**なので、`ON_DAYS` の契機がここで発火する。
そこで追手を出すと、続きの場面が消えて部屋選びに戻る（実機で踏んだ）。

ゲームの旗（`in_battle` ほか）では捕まらない。
宿泊の最中は**手も空いている**ので、旗でも手待ちでも捕まらない。
見るのは**今並んでいる選択肢のクラス名**（GAME.md §2.2）で、
場面が続いている間はその場面のマネージャがボタンとして並んでいる。

    部屋選び          VacationStartManager
    休養/訓練/交流    VacationRestManager / VacationTrainManager / …

`Display...Choice`（宿屋の品書きの「宿泊する」）は数えない。
数えると宿屋に立っているだけで追手が来なくなる。

判定は決めるとき（契機）と起こす直前の2回見る。
場面は待っている間に始まることもある。

##### いつ来るか（4つとも設定で切り替え）

| 場面 | 対象 | 既定 |
| --- | --- | --- |
| 土地への到着 | `AreaMoveManager.execute` | ON |
| 施設への到着 | `MovePhaseManager.move_phase` | OFF |
| 日数の経過 | `InstantaleApp.elapse_days` | ON |
| 自由行動 | `master_ai_facilitator` | OFF |

##### 契機は「決める」だけ。起こすのは画面が整った合図の中

契機（上の4つ）はどれも**場面の途中**で来る。

- 自由行動の契機は、行動を解決し終えた時点で呼ばれるが `in_free_input` はまだ立っている
- 土地の移動は、点のアニメーションと `辿り着いた。` より**前**に `execute` が返る
- 宿泊は、部屋を選んだ後の日数送りで発火する

以前は「手が空くのを待つ」ポーリングで凌いでいたが、
自由入力の後には推論・画像生成・場面移動が続くことがあり、
**静かな瞬間はその途中にも現れる**。当て推量になっていた。

そこで契機では控え（`memo["due"]`）を置くだけにして、
起こすのは**ゲームが選択肢を組み直した合図**の中に限った。

    InstantaleApp.refresh_choice_buttons      ← ここでだけ起こす

実測（`220_` の記録、2026-08-22。VERIFICATION_LOG.md §2.56）:

| 見たこと | 値 |
| --- | --- |
| 呼ばれる頻度 | 画面が変わるたびに1回（76秒の実プレイで13回） |
| そのときの旗 | 13回とも空（`is_adding_text` も `is_button_enabled=False` も立っていない） |
| 呼び出し元 | `scripts.functions:finish_button_load`。選択肢を描き終えた後 |
| 自由入力の出口からの間 | `end_process` の 5.5秒後 |

合図の中で、旗・多段の場面・ゲーム自身の衛兵をもう一度見る。
まだ場面の中なら控えは残すので、**次に画面が整ったときに出る**。
画面が `DUE_MAX_SIGNALS` 回組み直されても出せなければ捨てる
（契機とかけ離れた場面で出さないため）。

`set_buttons_to_normal` は使わない。
実測では合図の1〜2秒前に来るが、**そのときはまだ本文を流している最中**だった。

##### 条件は2つ（どちらかを満たせば来る）

今いる土地の手配の重さと、全ての土地の重さの合計。
合計の側があるので、**手配されていない土地へ逃げても追ってくる**。
数え方は `hunt.py`（`309_office_pardon` の罰金と同じ物差し）。

既定の 20 / 40 は殺害1件あたりの手配度の動き（-10）から取った。
1名では追手が来ず、**2名以上でお尋ね者**になる。

発生率は％（0〜100）。条件を満たした場面ごとに1回抽選する。

##### 押されていないぶんを補う

ボタンから入る戦闘では、ゲームが押下の流れの中で選択肢を塗り直す。
こちらはボタンを押さずに起こしているので、その1手が抜ける。

抜けると出るのが**戦闘の選択肢と敵の情報欄の重なり**。
右の欄（`right_button_layout`）は選択肢が左の4つに収まらないときに開き、
戦闘の情報欄と同じ矩形を使っている。
選択肢の多い画面から戦闘に入ると開いたまま残る（実測）。

敵が揃った後に `app.display_button_load(0)` を1回通す。
欄の出し入れを決めているのはゲームのこの系統なので、
**座標は一切触らずに、ゲーム自身の判断で畳ませる**。

##### 時計を見ない

区切りに使うのは**画面が整った合図の回数**と**ゲーム内の日数**だけで、秒は数えない。

| 何を区切るか | 単位 |
| --- | --- |
| 次の追手まで | ゲーム内の日数（`COOLDOWN_DAYS`。`elapse_days` を数えたもの） |
| ゲーム自身の衛兵の後 | 同上（1回の遭遇として数え、同じクールダウンに乗せる） |
| 決めたのに出せないまま | 合図の回数（`DUE_MAX_SIGNALS`） |
| 出したのに戦闘が始まらないまま | 合図の回数（`ARM_MAX_SIGNALS`） |
| 倒したぶんの手配度を見張る間 | 合図の回数（`PROTECT_MAX_SIGNALS`） |

秒で区切ると、待っている間にゲームが何をしていたかと無関係に時間だけが過ぎる。
実機で踏んだ不具合（`辿り着いた。` より前に出る／宿泊の途中に割り込む）は、
どれも「秒は経ったが場面は終わっていない」形だった。

`Clock.schedule_once(..., 0)` は1回だけ使うが、これは待ちではなく
**次のフレームまで譲る**ためのもの（ゲームが選択肢を描き終えた直後に
`process_choice` へ入らないようにする）。

##### 記録

`out/bounty_hunter_send.log`。来た回も、条件を満たしたのに来なかった回も1行ずつ残す。
`220_` の計測（`out/bounty_hunter.log`）とは別のファイル。
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

# 難易度の差し替えを開いておく秒数。
# 起こしたのに敵が作られなかった（LLM が落ちた・画面が変わった）とき、
# 次にゲーム自身が出した衛兵まで強くしないための時限。
# 実測では起こしてから敵が揃うまで約6秒だった。
# 数え方の単位は**画面が整った合図の回数**（`refresh_choice_buttons`）。
# 秒を数えない。
# 時計で区切ると「何秒経ったか」でしか説明できず、
# 遊んでいる側から見た区切り（何画面ぶんか・何日ぶんか）と噛み合わない。
# 合図は画面が変わるたびに1回来る（実測。VERIFICATION_LOG.md §2.56）。
#
# 追手を出すと決めたのに、画面が整わないまま合図がこれだけ過ぎたら捨てる。
DUE_MAX_SIGNALS = 20

# 追手を出したのに戦闘が始まらないまま合図がこれだけ過ぎたら、控えを降ろす。
# 戦闘が始まっていればその合図には戦闘の画面が出ているので、
# 数えているのは「始まらなかった」ときだけになる。
ARM_MAX_SIGNALS = 20

# 追手を倒した後の手配度を見張る合図の回数。
# **ゲームがいつ下げるのかは決まっていない**。
# 実測では戦闘の終わりの直後に下がった回と、それより後に下がった回があった
# （VERIFICATION_LOG.md §2.53）。だから時機を決め打ちせず、
# 戦闘の終わりと、その後の合図を数回見る。
# 長く開けておくと戦闘の外で犯した罪まで巻き戻すので、数回で閉じる。
PROTECT_MAX_SIGNALS = 10

# `process_choice` に渡す文字列。
# ゲームは押されたボタンの文字を渡すが、こちらは押されていないので自前の1語を渡す。
# **自前のクラス名を `PhaseSpec` に載せない**のと同じ理由で、
# ここに出す文字も画面のボタンとしては使わない（TECH.md §6.2）。
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

# そのうち戦闘の旗。
# **戦闘中は控えすら置かない。**
# 置くと、その戦闘が終わって画面が整った瞬間に次の戦闘が始まることになる。
# 会話や自由入力の最中は控えを置いてよい（終われば普通の画面に戻る）。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")


# **多段の場面**の目印（選択肢のクラス名に含まれる語）。
# 旗では捕まらない場面がある。宿泊がその例で、
#
#     部屋選び(VacationStartManager) -> 日数送り -> 休養/訓練/交流(Vacation*Manager)
#
# の途中で `elapse_days` が走る。
# そこへ割り込むと**続きの場面が消えて部屋選びに戻る**（実機で踏んだ）。
#
# 見るのは今並んでいる選択肢のクラス名（`PhaseSpec`。GAME.md §2.2）。
# 場面が続いている間、その場面のマネージャがボタンとして並んでいる。
# `Display...Choice` は「その場面に入る前の品書き」なので数えない
# （宿屋に立っているだけで追手が来なくなってしまう）。
SEQUENCE_MARKS = (
    "Vacation", "Training", "Labor", "Imprisonment", "Trial", "Begging",
    "Colosseum", "HighLow", "Slot", "RussianRoulette",
    "Quest", "Battle", "Loot", "Conversation", "Utterance",
    "Employ", "Citizenship", "MedicalTreatment", "Reinforcement",
    "Enchantment", "Craft", "Shopping", "Execution", "SkillChoice",
    "FreeInput", "Epilogue", "GameOver", "DieFromOldAge",
)


def renamed_keys(keys, name):
    """`{古い鍵: 新しい鍵}`。連番は元の鍵から写す。

    実測の鍵は `衛兵1` / `衛兵2` / `衛兵3` で、名前＋連番の形をしている。
    連番の付け方を発明せず、**末尾の数字をそのまま持ち越す**
    （数字が無ければ順番で振る）。
    新しい鍵が既にある鍵とぶつかるなら、その1体は付け替えない
    （同じ鍵の敵が2体になるほうが害が大きい）。
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
            "due": None}

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

        戦闘の選択肢を出す右の欄（`right_button_layout`）は、
        戦闘の情報欄（`top_info_layout_battle`）と**同じ矩形**を使っている。
        選択肢が左の欄（4つ）に収まらない画面では開いていて、
        そこから戦闘に入ると開いたまま残り、敵の情報と重なる
        （実測。VERIFICATION_LOG.md §2.59 / §2.60）。

        欄の出し入れを決めているのはゲームの
        `display_button_load` → `update_ui` → `update_button_texts` の系統なので、
        敵が揃った後にその入口を1回通す。
        戦闘の選択肢は4つなので、ゲーム自身の判断で右の欄が畳まれる。

        押されたボタンから入る戦闘ではこの塗り直しが自然に入る。
        こちらはボタンを押さずに起こしているので、そのぶんを補っている。
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

        ゲームにも手配された者へ衛兵を差し向ける仕掛けがあり、
        こちらと同じ操作から出てくる。
        同じ場面で両方が起きるとゲーム側のイベントが潰れるので、
        ゲームが出したら**1回の遭遇として数える**（クールダウンに乗せる）。

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
        schedule(lambda: repaint_buttons(app))
        return result

    @ctx.wrap("__main__:BattleEndManager.end_phase", required=False, safe=True)
    def battle_end(orig, self, *args, **kwargs):
        """戦闘の終わり。ここと、その数秒後の2回だけ手配度を見る。

        ゲームがどの時点で下げるのかは測っていないので、
        **時機を1点に決め打ちしない**（`end_phase` の中で下がるなら1回目で、
        後の段で下がるなら2回目で戻る）。
        """
        result = orig(self, *args, **kwargs)
        if memo["protect"] is not None:
            restore(getattr(self, "app", None) or ui.find_app(), "戦闘終了")
        return result

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False,
              safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        """**画面が整った合図**。追手を起こすのはここだけ。

        実測（`220_` の記録、2026-08-22）:
        この入口は画面が変わるたびに1回ずつ呼ばれ、
        13回とも `is_adding_text` も `is_button_enabled=False` も立っていなかった。
        呼び出し元は `scripts.functions:finish_button_load` で、
        **選択肢を描き終えた後**に来る。

        自由入力の出口（`FreeInputStart.end_process`）からは 5.5秒後。
        その間に `set_buttons_to_normal` が挟まるが、
        そちらはまだ本文を流している最中（`is_adding_text`）なので使わない。
        """
        result = orig(self, *args, **kwargs)
        count_signal()
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
    """`generate_enemy_instance_from_quest_dict` の残りの引数のうち難易度の位置。

    実測の並びは
    `(base_image, pixelated_image, positive_prompt, negative_prompt, 難易度)` で、
    難易度だけが数。**位置で決め打ちせず、数が1つだけならそれを採る**
    （ゲームの更新で引数が増減しても、数が1つのうちは追随する）。
    数が複数あるときだけ実測の位置（5番目）に戻る。
    """
    numbers = [index for index, value in enumerate(args) if _is_number(value)]
    if len(numbers) == 1:
        return numbers[0]
    if 4 in numbers:
        return 4
    return None
