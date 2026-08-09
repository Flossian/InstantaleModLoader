# -*- coding: utf-8 -*-
"""行動の成否判定に、キャラクタの能力値を効かせる（2経路）。

##### 何が起きているか

フィールドイベント中にプレイヤーが行動を入力すると、`field_event_evaluator`
（`quest_referee_event_evaluate_new`）が2つの値を返す:

- `credibility` … その行動が成功することの説得力を 1〜10 で採点したもの
- `reference_attribute` … 判定に最も関わる能力値（6種のどれか）

ゲームはこのうち **`credibility` だけを確率に変えている**。`quest_event_log` に
書き込まれる `<確率N%: …>` は `credibility*10 + 20` を超えず、半数はちょうど
その値、残りは負の差が付くだけになる。つまり `reference_attribute` は選ばれる
だけで、**その能力値がいくつであっても確率は上がらない**。

素早さに振ったキャラでも、力に振ったキャラでも、同じ入力なら同じ確率になる。
「判定が機械的で、能力値が効いていないように見える」という体感の実体はここ。
根拠となった実データは GAME.md §2.9「フィールドイベントの成否判定」と
VERIFICATION.md §2.37。

##### どこを触るか

判定そのものは `QuestEventManager`（コンパイル済み）の中で、上の戻り値を
受け取ってから行われる。ゲームのコードは読めないので、**判定に入る前の
`credibility` を能力値に応じて上げる**。

    確率 = (credibility + (能力値の加点% + 底上げ%) / 10) * 10 + 20

既定の刻みは 15 から 3 点ごと、上限 +20%:

| 能力値 | 加点 |
|---|---|
| 〜15 | なし |
| 16〜18 | +4% |
| 19〜21 | +8% |
| 22〜24 | +12% |
| 25〜27 | +16% |
| 28〜30 | +20% |

基準が平均より下にあるのは、**能力値がレベルでは伸びない**から。動くのは宿の
訓練だけで、作成時に振った値がほぼそのまま最後まで続く。平均を基準にすると
「素早さに振ったのに補正0」が普通になってしまう（VERIFICATION.md §2.37）。

下側は既定で減点しない ― 低い能力値の判定が素のゲームより不利になるのは
理不尽なので、**素の水準を下回らせない**（`MAX_PENALTY=0`）。

##### 10% 未満の刻みについて（`FINE_STEPS`）

説得力1段は確率10%にあたる。4% のような刻みを出すには **`credibility` に端数を
渡す**しかない（`5.4` → 74%）。スキーマ上は整数だが、代入時に検証が走らない型
なら通る。**実機では未確認**なので、次のように守ってある:

- 書いた後に読み直して、端数がそのまま入ったかを確かめる
- 入らなければ**整数に丸めて入れ直す**（刻みは 10% に落ちるが、加点は効く）
- 落ちたことは1度だけ記録に出す。`FINE_STEPS` を切れば最初から整数で動く

ゲーム側の式がどうであれ、`credibility` が上がって確率が下がることはない ―
式を推測せずに済む形を選んでいる。

##### 何を触らないか

- LLM のプロンプトには手を入れない。説得力の採点は「その行動がもっともらしいか」
  の判断で、そこはモデルに任せたままにする。**キャラクタの強さは能力値から
  こちらで足す**。TRPG でいえば、難易度は GM が決め、修正値はシートから引く
- 確定成功・確定失敗（`certain_success` / `certain_failure`）には触らない。
  サイコロを振らない結果を能力値でひっくり返すと、行動入力の意味が壊れる

##### もう1つの経路: クエスト外の自由入力（マスターAI）

街・施設・会話中の自由入力は `master_ai_facilitator` 系が処理する。こちらは
**確率を LLM 自身が出す**（`process` の `roll_the_dice.chance_percent`）別系統で、
やはりプロンプトに能力値は1つも載っていない（GAME.md §2.26）。

こちらは確率がそのまま数字で出てくるので、**`chance_percent` に加点を足す**だけ。
説得力の端数の話は関係しない。

問題は「どの能力値の判定か」を誰も言っていないこと（フィールドイベントの
`reference_attribute` に当たる出力が無い）。そこで**行動文から推定する**:

| `FREE_ACTION_MODE` | やり方 |
|---|---|
| `llm` (既定) | 行動文と `think` を渡して、6能力値のどれかを1問だけ LLM に選ばせる |
| `keywords` | 語句の表で決める。追加の推論を1回も起こさない |
| `off` | この経路には触らない |

`llm` は自前の `manager_name`（`mod_ability_for_action`）で送るので、
`output_data/<世界>/<PC>/mod_ability_for_action/` に問いと答えが残る。
返らない場合に備えて `timeout` を必ず渡し、失敗したら `keywords` に降りる。
同じ行動文には何度も聞かない（マスターAI は1つの入力で何ターンも回るため）。

語句の表だけで参照能力値が決まるのは、行動文だけなら 32%、`think` を足しても
56% にとどまる。残りは語句が拾えず補正なしになるので、**既定は `llm`**
（VERIFICATION.md §2.37）。

##### 既定は「能力値の差だけ」

`FLAT_BONUS` の既定は 0 ― 素のゲームの成功率をそのままにして、能力値の差だけを
足す。つまり**能力値が 15 以下の判定では何も起きない**。作成直後のキャラは
才能点が既定なら各値 9〜16 なので（GAME.md §2.17）、**訓練で伸ばすまで
ほとんど発火しない**のが正しい姿。全体を底上げしたいなら `FLAT_BONUS` を上げる。

加点が 0 だった回も `補正なし: dexterity=12 (基準 15 以下)` の形で記録に残す。
残さないと「MOD が動いていない」のと見分けが付かない。

##### 記録

`out/event_roll.log`。`215_probe_event_roll` と同じファイルに書く ―
判定を触る側と計測が別々の時系列になると、差がこの MOD のせいか元からかを
見分けられないため。実際に書き込まれた確率は probe 側の `percent` に出る。
"""

import datetime
import math
import time

from instantale_modloader import ui

LOG_BASENAME = "event_roll.log"

# quest_referee_event_evaluate_new(quest_data, quest_log, quest_event_log,
#     enemies_info, player, skill_dict, item_dict, player_action,
#     triggered_event, event_turn)                     ― out/recon/targets.txt
EVAL_ARGS = ("quest_data", "quest_log", "quest_event_log", "enemies_info",
             "player", "skill_dict", "item_dict", "player_action",
             "triggered_event", "event_turn")

ABILITIES = ("strength", "constitution", "dexterity",
             "intelligence", "wisdom", "charisma")

# クエスト外（と、クエスト中の自由入力）を処理するマスターAI。
# 引数の並びは out/recon/targets.txt より。`faciltiator` の綴りはゲーム側のまま。
FREE_ACTION_TARGETS = {
    "master_ai_facilitator": (
        "player", "player_life_log", "worldview", "facility_list", "npc_list",
        "npc_list_text", "choice_text", "skill_choice_text", "item_choice_text",
        "master_process_log"),
    "master_ai_facilitator_in_quest": (
        "player", "quest_log", "worldview", "facility_list", "npc_list",
        "npc_list_text", "choice_text", "skill_choice_text", "item_choice_text",
        "master_process_log"),
    "master_ai_facilitator_from_conversation": (
        "player", "player_life_log", "worldview", "facility_list", "npc_list",
        "npc_list_text", "conversation_log", "master_process_log"),
    "master_ai_faciltiator_from_conversation_in_quest": (
        "player", "quest_log_summary", "worldview", "facility_list", "npc_list",
        "npc_list_text", "conversation_log", "master_process_log"),
}

# 推定を頼むときの自前の manager 名。output_data に問いと答えが残る。
MANAGER_INFER = "mod_ability_for_action"
INFER_TIMEOUT = 30          # 返らない場合に備える（GAME.md §2.12）
INFER_CACHE_MAX = 64        # マスターAI は1入力で何ターンも回る。同じ文は聞き直さない
INFER_TEXT_MAX = 900        # 頼み文に載せる行動文の長さ

INFER_PROMPT = (
    "TRPG の判定に使う能力値を1つ選べ。\n"
    "次の行動が成功するかどうかを判定する。最も関わる能力値を、下の6つから"
    "**1つだけ**選び、英語の識別子だけを答えろ。\n"
    "- strength: 腕力。持ち上げる・押し破る・力任せに振るう\n"
    "- constitution: 頑健さ。耐える・毒や寒さに抗う・無理を通す\n"
    "- dexterity: 素早さと器用さ。避ける・忍ぶ・盗む・登る・細かい手作業\n"
    "- intelligence: 知識と分析。解読する・仕組みを読む・術式を組む\n"
    "- wisdom: 勘と観察。探す・気づく・見抜く・危険を察する\n"
    "- charisma: 人を動かす力。説得・交渉・威圧・欺き\n"
    "\n【行動】\n{text}\n"
)

# `FREE_ACTION_MODE=keywords` と、推論が使えないときの受け皿。
# `roll_the_dice` の記録に当てて選んだ語。行動文だけでは 32%、マスターAI の
# think を足して 56% が決まる。決まらない回は補正しない。
INFER_KEYWORDS = {
    "strength": (
        "持ち上げ", "押し破", "押し開", "引きずり", "殴", "叩き", "打ち砕",
        "破壊", "こじ開", "こじ", "担ぎ", "抱え", "投げ飛ば", "力ずく",
        "腕力", "筋力", "振り下ろ", "組み伏せ", "押さえ込", "引き剥が", "重い"),
    "constitution": (
        "耐え", "踏ん張", "持ちこた", "我慢", "毒", "痛み", "寒さ", "暑さ",
        "飲み比べ", "徹夜", "泳", "長距離", "抵抗", "体力", "無理を", "耐性"),
    "dexterity": (
        "避け", "回避", "かわ", "忍び", "隠れ", "潜入", "盗", "掏", "すり抜け",
        "素早", "駆け", "跳", "飛び", "よじ", "解錠", "鍵を", "罠を", "細工",
        "投擲", "狙撃", "手先", "器用", "足音", "こっそり", "背後"),
    "intelligence": (
        "分析", "解析", "読み解", "知識", "推理", "術式", "魔術", "魔法陣",
        "計算", "鑑定", "解読", "仕組み", "構造", "理論", "調合", "考察",
        "図面", "設計", "文献", "研究"),
    "wisdom": (
        "探知", "気配", "察知", "見抜", "観察", "感じ取", "直感", "警戒",
        "耳を", "匂い", "見張", "索敵", "探索", "追跡", "痕跡", "見回",
        "注意深", "見極め", "危険を", "違和感", "調べ", "捜", "探す", "探る"),
    "charisma": (
        "説得", "交渉", "値切", "嘘", "誤魔化", "脅", "口説", "演技",
        "惹きつけ", "宥め", "挑発", "頼む", "頼み", "問いただ", "取り入",
        "懐柔", "取引", "口車", "威圧"),
}

# 推定の答えが日本語で返ってきたときの読み替え。
INFER_ALIASES = {
    "腕力": "strength", "筋力": "strength", "力": "strength",
    "頑健": "constitution", "耐久": "constitution", "体力": "constitution",
    "敏捷": "dexterity", "器用": "dexterity", "素早さ": "dexterity",
    "知力": "intelligence", "知能": "intelligence", "知識": "intelligence",
    "判断力": "wisdom", "感知": "wisdom", "賢さ": "wisdom", "知覚": "wisdom",
    "魅力": "charisma", "話術": "charisma",
}

# スキーマ（field_event_evaluator の RollRequired.credibility）の範囲。
# ここを外れた値を返すとゲーム側の検証に落ちる可能性がある。
CREDIBILITY_MIN = 1
CREDIBILITY_MAX = 10

# 説得力1段が確率何%にあたるか（確率 = credibility*10 + 20。GAME.md §2.9）。
PERCENT_PER_CREDIBILITY = 10

# 設定（mod.json と対。ローダが apply() の前にこのグローバルへ書き込む）。
PIVOT = 15
STEP = 3
BONUS_PER_STEP = 4
MAX_BONUS = 20
MAX_PENALTY = 0
FLAT_BONUS = 0
FINE_STEPS = True
FREE_ACTION_MODE = "llm"
SCORE_SOURCE = "raw"


def _get(container, name, default=None):
    """dict でも属性でも読む。`305_mini_quest` の `_get` と同じ方針。

    `quest_referee*` の戻り値の形は特定できていない。両方で通し、
    どちらでもなければ何もしない（推測して壊すより素通しするほうがよい）。
    """
    if container is None:
        return default
    if isinstance(container, dict):
        return container.get(name, default)
    value = getattr(container, name, None)
    return default if value is None else value


def _set(container, name, value):
    """`_get` と対。**入れた値がそのまま読み返せたときだけ** True。

    pydantic のモデルなら代入で検証や型変換が走ることがある。端数を渡して
    整数に丸められた場合も「書けなかった」として扱いたいので、
    等値だけでなく型も見る（`5 == 5.0` は True になってしまう）。
    """
    try:
        if isinstance(container, dict):
            container[name] = value
            return True
        setattr(container, name, value)
        current = getattr(container, name, None)
        return current == value and isinstance(current, type(value))
    except Exception:
        return False


def arg_of(args, kwargs, names, name):
    """位置引数・キーワード引数のどちらで来ても読む。無ければ None。

    呼び出し側はコンパイル済みで読めないので、どちらの渡し方かを決め打ちできない
    （`103_fix_eventlog_trim` と同じ理由）。
    """
    if name in kwargs:
        return kwargs[name]
    try:
        index = names.index(name)
    except ValueError:
        return None
    return args[index] if len(args) > index else None


def score_of(character, attribute, source=None):
    """そのキャラクタの `attribute` の能力値。読めなければ None。

    実行時の持ち方は1つに決まっていない（セーブは `original_ability_scores`、
    キャラクタシートは `attribute_<名前>`）。名前を推測して1つだけ見ると、
    読めなかったのか 0 だったのかが区別できなくなるので、順に当たる。
    """
    if character is None or attribute not in ABILITIES:
        return None
    raw = None
    for name in ("ability_scores", "original_ability_scores"):
        table = getattr(character, name, None)
        if isinstance(table, dict) and table.get(attribute) is not None:
            raw = table.get(attribute)
            break
    if raw is None:
        raw = getattr(character, "attribute_" + attribute, None)
    if not isinstance(raw, (int, float)):
        return None
    if (source or SCORE_SOURCE) != "condition":
        return raw
    # 負傷や体力で目減りした換算値。ゲーム自身の計算をそのまま借りる。
    calculate = getattr(character, "calculate_attribute", None)
    if not callable(calculate):
        return raw
    try:
        converted = calculate(raw)
    except Exception:
        return raw
    return converted if isinstance(converted, (int, float)) else raw


def ability_percent(score, pivot=None, step=None, per_step=None,
                    bonus_cap=None, penalty_cap=None):
    """能力値を確率の加点(%)に直す。読めなければ 0。

    基準（既定 15）ちょうどまでは 0。**1点でも超えれば1段目**、そこから
    `STEP` 点ごとにもう1段。既定（15 / 3点 / 1段4% / 上限20%）で

        〜15 → 0 ／ 16-18 → +4 ／ 19-21 → +8 ／ 22-24 → +12
        25-27 → +16 ／ 28以上 → +20（上限）

    上限にちょうど届くのが 28 なのは、能力値の上端が 30 だから。
    """
    if not isinstance(score, (int, float)):
        return 0
    pivot = PIVOT if pivot is None else pivot
    step = STEP if step is None else step
    per_step = BONUS_PER_STEP if per_step is None else per_step
    bonus_cap = MAX_BONUS if bonus_cap is None else bonus_cap
    penalty_cap = MAX_PENALTY if penalty_cap is None else penalty_cap
    if step <= 0:
        return 0
    over = score - pivot
    # 切り上げ。基準を1点でも超えた時点で1段目に入る。
    if over > 0:
        return min(bonus_cap, int(math.ceil(over / float(step))) * per_step)
    if over < 0:
        return -min(penalty_cap, int(math.ceil(-over / float(step))) * per_step)
    return 0


def adjusted(credibility, score, flat=None, fine=None, **kw):
    """`(新しい説得力, 能力値の加点%, 底上げ%)`。範囲外には出さない。

    端数を許さない場合（`fine=False`）は説得力の整数へ丸める。
    丸めで加点が消えることはあるが、**確率が素より下がることはない**
    （`MAX_PENALTY=0` のとき加点は必ず 0 以上で、`round` は下限が元の値）。
    """
    flat = FLAT_BONUS if flat is None else flat
    fine = FINE_STEPS if fine is None else fine
    bonus = ability_percent(score, **kw)
    value = credibility + (bonus + flat) / float(PERCENT_PER_CREDIBILITY)
    value = max(CREDIBILITY_MIN, min(CREDIBILITY_MAX, value))
    if not fine:
        value = round(value)
    # 端数が無いなら int のまま渡す（型を無用に変えない）。
    if float(value).is_integer():
        value = int(value)
    return value, bonus, flat


def normalize_attribute(value):
    """推定の答えを 6能力値のどれかに直す。読めなければ None。"""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    for name in ABILITIES:
        if name in text:
            return name
    for word, name in INFER_ALIASES.items():
        if word in value:
            return name
    return None


def infer_by_keywords(text):
    """語句の表で能力値を1つ選ぶ。決め手が無ければ None。

    同点は「決まらなかった」として扱う。無理に片方へ倒すと、当たっている
    ように見えて中身は coin flip になる。補正しないほうが正直。
    """
    if not isinstance(text, str) or not text:
        return None
    hits = {}
    for ability, words in INFER_KEYWORDS.items():
        count = 0
        for word in words:
            count += text.count(word)
        if count:
            hits[ability] = count
    if not hits:
        return None
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def as_dict(raw):
    """返ってきたものを辞書にする。形を決めつけない（`311_` と同じ）。"""
    if isinstance(raw, dict):
        return raw
    for name in ("model_dump", "dict"):
        method = getattr(raw, name, None)
        if callable(method):
            try:
                got = method()
            except Exception:
                continue
            if isinstance(got, dict):
                return got
    if isinstance(raw, str):
        import json
        try:
            got = json.loads(raw)
        except Exception:
            return None
        if isinstance(got, dict):
            return got
    return None


def clamp_percent(value):
    """確率を 1〜100 に収める。0 と 100 超は渡さない。"""
    return int(max(1, min(100, round(value))))


def percent_of(credibility):
    """説得力を確率の見込みに直す（式は GAME.md §2.9）。記録用。"""
    value = credibility * PERCENT_PER_CREDIBILITY + 20
    return int(value) if float(value).is_integer() else round(value, 1)


def apply(ctx):
    import sys

    module = sys.modules.get("scripts.llm.llm_manager")
    if module is None:
        ctx.log("scripts.llm.llm_manager not loaded; skipping", level="WARN")
        return

    log_path = ctx.out_path(LOG_BASENAME)
    state = {"rolls": 0, "unreadable": 0, "coarse": False,
             "free_rolls": 0, "inferred": {}, "no_llm": False}

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("ability check: write failed")

    def install(result_type, value, credibility):
        """説得力を書き込む。端数が通らなければ整数に丸めて入れ直す。

        戻り値は実際に入った値。1つも入らなければ `None`。
        """
        if _set(result_type, "credibility", value):
            return value
        if isinstance(value, int):
            return None
        rounded = int(max(CREDIBILITY_MIN, min(CREDIBILITY_MAX, round(value))))
        if rounded != credibility and _set(result_type, "credibility", rounded):
            if not state["coarse"]:
                state["coarse"] = True
                write("説得力に端数を入れられないビルド。以後は整数に丸めて入れる"
                      "（刻みは10%になる。設定の FINE_STEPS を切ったのと同じ）")
            return rounded
        return None

    # ------------------------------------------------ 行動文から能力値を推定
    def ask_llm(text):
        """6能力値のどれかを1問だけ聞く。使えない・読めないなら None。

        `send_request` は返らないことがある（GAME.md §2.12）。`timeout` を
        必ず渡し、落ちたら黙って語句の表へ降りる ― マスターAI の応答を
        止めるほうが害が大きい。
        """
        if not callable(getattr(module, "send_request", None)) or \
                not callable(getattr(module, "create_model", None)):
            if not state["no_llm"]:
                state["no_llm"] = True
                write("推定を頼む部品が無い（send_request / create_model）。"
                      "語句の表で決める")
            return None
        try:
            structure = module.create_model("ModAbilityForAction",
                                            attribute=(str, ...))
        except Exception:
            ctx.log_exc("ability check: cannot build the inference structure")
            return None
        message = [{"role": "user",
                    "content": INFER_PROMPT.format(text=text[:INFER_TEXT_MAX])}]
        started = time.monotonic()
        try:
            raw = module.send_request(MANAGER_INFER, message, structure,
                                      max_tokens=32, timeout=INFER_TIMEOUT)
        except Exception:
            ctx.log_exc("ability check: {} failed".format(MANAGER_INFER))
            return None
        data = as_dict(raw) or {}
        found = normalize_attribute(data.get("attribute"))
        write("{}: {:.1f}s -> {!r} ({})".format(
            MANAGER_INFER, time.monotonic() - started,
            data.get("attribute"), found or "読めず"))
        return found

    def infer_attribute(text, key=None):
        """行動文から参照能力値を決める。決まらなければ None。

        控えの鍵は**プレイヤーの入力文だけ**にする。マスターAI は1つの入力で
        何ターンも回り、そのたびに `think` と `narration` が変わる。全部を鍵に
        すると、同じ行動を続けているだけで毎ターン聞き直すことになる。
        同じ行動文なら同じ能力値でよい。
        """
        if FREE_ACTION_MODE == "off" or not text:
            return None
        key = (key or "").strip() or text
        key = key[:INFER_TEXT_MAX]
        if key in state["inferred"]:
            return state["inferred"][key]
        found = None
        if FREE_ACTION_MODE == "llm":
            found = ask_llm(key)
        if found is None:
            found = infer_by_keywords(key)
        if len(state["inferred"]) >= INFER_CACHE_MAX:
            state["inferred"].clear()
        state["inferred"][key] = found
        return found

    def adjust_rolls(site, result, player, action_text):
        """マスターAI の `roll_the_dice.chance_percent` に加点を足す。"""
        steps = _get(result, "process")
        if not isinstance(steps, (list, tuple)):
            return
        rolls = [s for s in steps if _get(s, "type") == "roll_the_dice"]
        if not rolls:
            return
        text = "\n".join(part for part in (
            action_text, _get(result, "think"), _get(result, "narration"))
            if isinstance(part, str))
        attribute = infer_attribute(text, key=action_text)
        score = score_of(player, attribute) if attribute else None
        bonus = ability_percent(score) if score is not None else 0
        total = bonus + FLAT_BONUS
        if not total:
            # 同上。何もしない回こそ、何もしなかった理由を残す。
            write("{}: 補正なし {}={} (基準 {} 以下) 確率 {}% のまま".format(
                site, attribute or "推定できず", score, PIVOT,
                _get(rolls[0], "chance_percent")))
            return
        for step in rolls:
            before = _get(step, "chance_percent")
            if not isinstance(before, (int, float)):
                continue
            after = clamp_percent(before + total)
            if after == before or not _set(step, "chance_percent", after):
                continue
            state["free_rolls"] += 1
            write("{}: 能力値補正 {}={} -> {:+d}%(能力値) {:+d}%(底上げ) | "
                  "確率 {}% -> {}%".format(
                      site, attribute or "推定できず", score, bonus, FLAT_BONUS,
                      before, after))

    def make_free_action(name, names):
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name),
                  required=False, safe=True)
        def facilitate(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                if FREE_ACTION_MODE == "off":
                    return result
                player = arg_of(args, kwargs, names, "player")
                if player is None:
                    player = getattr(ui.find_app(), "player", None)
                action = arg_of(args, kwargs, names, "choice_text")
                if not isinstance(action, str):
                    # 会話からの経路には行動文が無い。会話ログの末尾を使う。
                    log = arg_of(args, kwargs, names, "conversation_log")
                    action = log[-INFER_TEXT_MAX:] if isinstance(log, str) else ""
                adjust_rolls(name, result, player, action)
            except Exception:
                ctx.log_exc("ability check: could not adjust {}".format(name))
            return result
        return facilitate

    for target, arg_names in FREE_ACTION_TARGETS.items():
        make_free_action(target, arg_names)

    @ctx.wrap("scripts.llm.llm_manager:quest_referee_event_evaluate_new",
              required=False, safe=True)
    def evaluate(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            result_type = _get(result, "result_type")
            if _get(result_type, "type") != "roll_required":
                return result           # 確定成功・確定失敗には触らない
            credibility = _get(result_type, "credibility")
            attribute = _get(result_type, "reference_attribute")
            if not isinstance(credibility, (int, float)):
                return result

            player = arg_of(args, kwargs, EVAL_ARGS, "player")
            if player is None:
                player = getattr(ui.find_app(), "player", None)
            score = score_of(player, attribute)
            if score is None:
                # 能力値が読めない回。底上げだけは効かせる（能力値の加点は 0）。
                state["unreadable"] += 1
                if state["unreadable"] <= 3:
                    write("能力値を読めなかった: attribute={!r} player={!r}。"
                          "底上げのみ適用する".format(
                              attribute, getattr(player, "name", player)))

            value, bonus, flat = adjusted(credibility, score)
            if value == credibility:
                # 加点が付かない回も残す。既定は底上げ0で、能力値が基準以下だと
                # 何も起きない ― そのとき記録まで空だと「MOD が動いていない」のと
                # 見分けが付かない。
                write("補正なし: {}={} (基準 {} 以下) 確率 {}% のまま".format(
                    attribute, score, PIVOT, percent_of(credibility)))
                return result

            installed = install(result_type, value, credibility)
            if installed is None:
                write("説得力を書き換えられなかった（型が受け付けない）。"
                      "ゲームの判定をそのまま通す: credibility={}".format(credibility))
                return result

            state["rolls"] += 1
            write("能力値補正: {}={} -> {:+d}%(能力値) {:+d}%(底上げ) | "
                  "説得力 {} -> {} | 確率の見込み {}% -> {}%".format(
                      attribute, score, bonus, flat, credibility, installed,
                      percent_of(credibility), percent_of(installed)))
        except Exception:
            # 補正に失敗してもゲームの判定はそのまま通す。イベントを止めない。
            ctx.log_exc("ability check: could not adjust the credibility")
        return result

    # ------------------------------------------------------------ 自己検証
    # 実経路はクエストでイベントを1回引くまで通らないので、計算だけ先に確かめる。
    table = [(score, ability_percent(score))
             for score in (PIVOT, PIVOT + 1, PIVOT + STEP,
                           PIVOT + STEP + 1, 28, 30)]
    cases = (
        # (能力値, 説得力, 期待する新しい説得力)  既定 15 / 3点 / 1段4% / 上限20% / 底上げ10%
        (15, 5, 5 + (0 + FLAT_BONUS) / 10.0),
        (16, 5, 5 + (BONUS_PER_STEP + FLAT_BONUS) / 10.0),
        (1, 5, 5 + FLAT_BONUS / 10.0),      # 低くても減点しない（MAX_PENALTY=0）
        (99, 5, 5 + (MAX_BONUS + FLAT_BONUS) / 10.0),
        (99, 10, CREDIBILITY_MAX),          # 上限で頭打ち
        (None, 5, 5 + FLAT_BONUS / 10.0),   # 読めない能力値は底上げだけ
    )
    failures = [c for c in cases if adjusted(c[1], c[0])[0] != c[2]]
    if failures:
        ctx.log("VERIFY FAILED: {}".format(
            ["score={} cred={} want={} got={}".format(
                s, c, want, adjusted(c, s)[0]) for s, c, want in failures]),
            level="ERROR")
    else:
        ctx.log("verified: pivot={} step={} 1段{}% 加点上限{}% 減点上限{}% "
                "底上げ{}% fine={} ({})".format(
                    PIVOT, STEP, BONUS_PER_STEP, MAX_BONUS, MAX_PENALTY,
                    FLAT_BONUS, FINE_STEPS,
                    " / ".join("{}→{:+d}%".format(s, p) for s, p in table)))

    # 語句の表も、作った文で1度だけ確かめる（実経路は自由入力を1回通すまで来ない）。
    samples = (("", None),
               ("崩れた瓦礫をこじ開けて押し破る", "strength"),
               ("物陰に隠れてこっそり忍び寄る", "dexterity"),
               ("古代文字を解読し、術式の構造を分析する", "intelligence"),
               ("周囲の気配を探り、罠の痕跡を見抜く", "wisdom"),
               ("店主を説得して値切る", "charisma"),
               ("毒に耐えながら踏ん張る", "constitution"),
               ("ただ歩く", None))
    bad = [(text, want, infer_by_keywords(text))
           for text, want in samples if infer_by_keywords(text) != want]
    if bad:
        ctx.log("VERIFY FAILED (keywords): {}".format(bad), level="ERROR")
    else:
        ctx.log("verified: keyword inference on 8 sample sentences")

    ctx.log("event ability check installed; source={} free_action={} log={}".format(
        SCORE_SOURCE, FREE_ACTION_MODE, log_path))
