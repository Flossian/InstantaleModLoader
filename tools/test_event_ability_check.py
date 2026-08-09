# -*- coding: utf-8 -*-
"""313_event_ability_check をゲーム抜きで通す。

    python tools/test_event_ability_check.py

偽の `player` と偽の評価結果（`roll_required` / `certain_success`）、および偽の
マスターAI（`roll_the_dice` を含む `process`）を差し込み、次を確認する。

  刻み     … 基準(15)までは補正なし。1点でも超えれば1段目で、3点ごとにもう1段。
             16-18 +4% / 19-21 +8% / 22-24 +12% / 25-27 +16% / 28以上 +20%。
             **低い能力値でも減点しない**（既定 MAX_PENALTY=0 ―
             素のゲームより不利にならないこと）
  減点     … MAX_PENALTY を上げたときだけ、基準を下回るぶんが同じ幅で下がる
  端数     … 10%未満の刻みは説得力の端数として渡る。端数を受け付けない型なら
             整数に丸めて入れ直し、その旨が1度だけ記録に出る。FINE_STEPS を
             切れば最初から整数
  頭打ち   … 加点は MAX_BONUS、減点は MAX_PENALTY まで。説得力は 1〜10 の外に
             出ない（スキーマの範囲。外すとゲーム側の検証に落ちうる）
  確定     … certain_success / certain_failure には触らない
  読めない … 能力値が読めない・参照能力値が知らない名前でも止まらず、
             底上げだけが効く
  形       … 戻り値が dict でもオブジェクトでも同じように通る
  拒否     … 書き換えを受け付けない型なら、元の説得力のまま素通しする
  引数     … player は位置でもキーワードでも読む。無ければ app.player から拾う
  換算     … SCORE_SOURCE=condition なら calculate_attribute を通した値を使う
  安全     … 途中で何が壊れても本体は必ず1回呼ばれ、戻り値がそのまま返る

自由入力（マスターAI）側:

  加点     … roll_the_dice の chance_percent に、推定した能力値ぶんと底上げが
             足される。1〜100 を外れない
  推定     … llm=1問だけ聞き、答えが読めなければ語句の表に降りる /
             keywords=聞かずに表だけ / off=触らない
  節約     … 同じ行動文には聞き直さない（マスターAI は1入力で何ターンも回る）
  素通し   … roll_the_dice が無い応答・process が無い応答には触らない
  会話     … 行動文を持たない経路では会話ログの末尾を材料にする
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

TARGET = "scripts.llm.llm_manager:quest_referee_event_evaluate_new"


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_event_ability_check")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Player(object):
    """能力値の持ち方は1つに決まっていないので、経路ごとに作れるようにする。"""

    def __init__(self, scores=None, flat=None, name="テスト冒険者", divisor=None):
        self.name = name
        if scores is not None:
            self.original_ability_scores = dict(scores)
        for key, value in (flat or {}).items():
            setattr(self, "attribute_" + key, value)
        if divisor:
            self.calculate_attribute = lambda score: score // divisor


class RollRequired(object):
    def __init__(self, credibility, attribute="dexterity"):
        self.type = "roll_required"
        self.narration = "……試みた。"
        self.credibility = credibility
        self.reference_attribute = attribute


class Frozen(RollRequired):
    """代入を受け付けない型（pydantic の frozen モデルの代わり）。"""

    def __setattr__(self, name, value):
        if name == "credibility" and "credibility" in vars(self):
            raise TypeError("frozen")
        object.__setattr__(self, name, value)


class IntOnly(RollRequired):
    """端数を整数に丸める型（`credibility: int` の検証が走るモデルの代わり）。"""

    def __setattr__(self, name, value):
        if name == "credibility" and isinstance(value, float):
            value = int(value)
        object.__setattr__(self, name, value)


class Result(object):
    def __init__(self, result_type):
        self.result_type = result_type


class Roll(object):
    """`process` の1要素（`roll_the_dice`）。"""

    def __init__(self, chance):
        self.type = "roll_the_dice"
        self.chance_percent = chance


class Master(object):
    """マスターAI の応答。`process` はリスト。"""

    def __init__(self, steps, think="", narration=""):
        self.think = think
        self.narration = narration
        self.process = list(steps)


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


class FakeLLM(object):
    """`scripts.llm.llm_manager` の代わり。`send_request` の呼ばれ方を数える。"""

    def __init__(self, answer="dexterity", raises=False):
        self.answer = answer
        self.raises = raises
        self.asked = []

    def create_model(self, name, **fields):
        return ("model", name)

    def send_request(self, manager_name, message, structure, **kw):
        self.asked.append((manager_name, message, kw))
        if self.raises:
            raise RuntimeError("no llm")
        return {"attribute": self.answer}


def load_mod(name="event_ability_check_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(llm=None, **settings):
    """mod を読み直して当て直す。設定はローダと同じくグローバルへ書き込む。"""
    module = load_mod()
    for key, value in settings.items():
        setattr(module, key, value)
    # `scripts.llm.llm_manager` が居ないと apply() は何もせずに戻る。
    sys.modules["scripts.llm.llm_manager"] = llm or type(sys)("stub")
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def free_call(ctx, result, player, action="", target="master_ai_facilitator"):
    """包まれたマスターAI を1回呼ぶ。`master_ai_facilitator` の引数の並びで渡す。"""
    calls = []

    def orig(*args, **kw):
        calls.append((args, kw))
        return result

    hook = ctx.hooks["scripts.llm.llm_manager:" + target]
    returned = hook(orig, player, None, None, None, None, None, action,
                    None, None, None)
    return returned, len(calls)


def call(ctx, result, player="__omit__", **kwargs):
    """包まれた evaluate を1回呼ぶ。戻り値と、本体が呼ばれた回数を返す。"""
    calls = []

    def orig(*args, **kw):
        calls.append((args, kw))
        return result

    hook = ctx.hooks[TARGET]
    if player == "__omit__":
        returned = hook(orig, None, None, None, None, **kwargs)
    elif kwargs.pop("by_keyword", False):
        returned = hook(orig, None, None, None, None, player=player, **kwargs)
    else:
        returned = hook(orig, None, None, None, None, player, **kwargs)
    return returned, len(calls)


def fresh_neg_floor():
    """底上げを負にして、説得力が 1 を下回らないことだけを見る。"""
    module, ctx = fresh_mod(FLAT_BONUS=-50)
    result = Result(RollRequired(1))
    call(ctx, result, Player({"dexterity": 1}))
    return result.result_type.credibility == 1


# ------------------------------------------------------------------ 検査
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("刻み")
    module, ctx = fresh_mod(PIVOT=15, STEP=3, BONUS_PER_STEP=4, MAX_BONUS=20,
                            MAX_PENALTY=0, FLAT_BONUS=0)
    for score, want in ((1, 0), (14, 0), (15, 0), (16, 4), (18, 4), (19, 8),
                        (21, 8), (22, 12), (24, 12), (25, 16), (27, 16),
                        (28, 20), (30, 20), (99, 20)):
        result = Result(RollRequired(5))
        call(ctx, result, Player({"dexterity": score}))
        got = (result.result_type.credibility - 5) * 10
        check("能力値{} -> {:+d}%".format(score, want), abs(got - want) < 1e-9, got)
    check("注入時の自己検証が通っている",
          not [level for level, _ in ctx.logs if level == "ERROR"], ctx.logs)

    print("減点")
    module, ctx = fresh_mod(PIVOT=15, STEP=3, BONUS_PER_STEP=4, MAX_PENALTY=8,
                            FLAT_BONUS=0)
    for score, want in ((15, 0), (14, -4), (12, -4), (11, -8), (1, -8)):
        result = Result(RollRequired(5))
        call(ctx, result, Player({"dexterity": score}))
        got = (result.result_type.credibility - 5) * 10
        check("MAX_PENALTY=8 のとき 能力値{} -> {:+d}%".format(score, want),
              abs(got - want) < 1e-9, got)

    print("底上げ")
    module, ctx = fresh_mod(FLAT_BONUS=10)
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 15}))
    check("基準ちょうどでも底上げは効く", result.result_type.credibility == 6,
          result.result_type.credibility)

    print("端数")
    module, ctx = fresh_mod(FLAT_BONUS=0)
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 16}))
    check("端数がそのまま入る（+4% = 説得力 5.4）",
          result.result_type.credibility == 5.4, result.result_type.credibility)
    module, ctx = fresh_mod(FLAT_BONUS=0, FINE_STEPS=False)
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 25}))
    check("FINE_STEPS を切ると整数に丸まる（+16% -> +20%）",
          result.result_type.credibility == 7, result.result_type.credibility)
    module, ctx = fresh_mod(FLAT_BONUS=0)
    coarse = IntOnly(5)
    call(ctx, Result(coarse), Player({"dexterity": 25}))
    check("端数を丸める型なら整数で入れ直す", coarse.credibility == 7,
          coarse.credibility)
    check("丸めたことが記録に残る",
          any("端数を入れられない" in line for line in read_log()), read_log()[-3:])
    module, ctx = fresh_mod(FLAT_BONUS=0)
    unchanged = IntOnly(5)
    call(ctx, Result(unchanged), Player({"dexterity": 16}))
    check("丸めて元の値に戻るなら書かない（+4% は 5 のまま）",
          unchanged.credibility == 5, unchanged.credibility)

    print("頭打ち")
    module, ctx = fresh_mod(FLAT_BONUS=10)
    result = Result(RollRequired(9))
    call(ctx, result, Player({"dexterity": 99}))
    check("上は10で止まる", result.result_type.credibility == 10,
          result.result_type.credibility)
    check("下は1で止まる（底上げを負にしても）",
          fresh_neg_floor(), "1 を下回った")
    module, ctx = fresh_mod(MAX_BONUS=4, FLAT_BONUS=0)
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 99}))
    check("能力値の加点は MAX_BONUS まで", result.result_type.credibility == 5.4,
          result.result_type.credibility)

    print("確定")
    module, ctx = fresh_mod(FLAT_BONUS=3)
    for kind in ("certain_success", "certain_failure"):
        certain = RollRequired(5)
        certain.type = kind
        result = Result(certain)
        returned, times = call(ctx, result, Player({"dexterity": 30}))
        check("{} には触らない".format(kind),
              certain.credibility == 5 and returned is result and times == 1,
              certain.credibility)

    print("読めない")
    module, ctx = fresh_mod(FLAT_BONUS=10)
    result = Result(RollRequired(5))
    call(ctx, result, Player())                      # 能力値をどこにも持たない
    check("能力値が読めなくても底上げは効く", result.result_type.credibility == 6,
          result.result_type.credibility)
    result = Result(RollRequired(5, attribute="luck"))
    call(ctx, result, Player({"dexterity": 30}))
    check("知らない参照能力値でも止まらない", result.result_type.credibility == 6,
          result.result_type.credibility)
    result = Result(RollRequired(5))
    returned, times = call(ctx, result, None)
    check("player が None でも本体は通る", returned is result and times == 1)
    check("ここまで例外を握り潰していない", not ctx.errors, ctx.errors)

    print("形")
    module, ctx = fresh_mod(FLAT_BONUS=10)
    payload = {"result_type": {"type": "roll_required", "credibility": 5,
                               "reference_attribute": "dexterity"}}
    call(ctx, payload, Player({"dexterity": 30}))
    check("dict で返っても書き換わる",
          payload["result_type"]["credibility"] == 8,
          payload["result_type"]["credibility"])
    returned, times = call(ctx, "まさかの文字列", Player({"dexterity": 30}))
    check("知らない形は素通し", returned == "まさかの文字列" and times == 1)

    print("拒否")
    module, ctx = fresh_mod(FLAT_BONUS=10)
    frozen = Frozen(5)
    returned, times = call(ctx, Result(frozen), Player({"dexterity": 30}))
    check("書き換えられない型はそのまま通す",
          frozen.credibility == 5 and times == 1, frozen.credibility)
    check("拒否は記録に残る",
          any("説得力を書き換えられなかった" in text
              for text in read_log()), read_log()[-3:])

    print("引数")
    module, ctx = fresh_mod(FLAT_BONUS=0)
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 30}), by_keyword=True)
    check("キーワードで渡された player を読む",
          result.result_type.credibility == 7, result.result_type.credibility)

    print("換算")
    module, ctx = fresh_mod(FLAT_BONUS=0, SCORE_SOURCE="condition")
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 34}, divisor=2))   # 34 -> 17 -> +4%
    check("condition は calculate_attribute を通す",
          result.result_type.credibility == 5.4, result.result_type.credibility)
    module, ctx = fresh_mod(FLAT_BONUS=0, SCORE_SOURCE="raw")
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 34}, divisor=2))   # 34 のまま -> +20%
    check("raw は生の能力値を使う",
          result.result_type.credibility == 7, result.result_type.credibility)

    print("自由入力 / 加点")
    llm = FakeLLM("dexterity")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=10, FREE_ACTION_MODE="llm")
    roll = Roll(60)
    free_call(ctx, Master([roll]), Player({"dexterity": 28}), "物陰に隠れる")
    check("chance_percent に加点と底上げが足される（60 -> 90）",
          roll.chance_percent == 90, roll.chance_percent)
    check("推定は1問だけ聞く", len(llm.asked) == 1, llm.asked)
    check("自前の manager 名で送る",
          llm.asked and llm.asked[0][0] == module.MANAGER_INFER, llm.asked)
    check("message はリストで渡す",
          llm.asked and isinstance(llm.asked[0][1], list), llm.asked)
    check("timeout を必ず渡す",
          llm.asked and llm.asked[0][2].get("timeout"), llm.asked)
    roll = Roll(95)
    free_call(ctx, Master([roll]), Player({"dexterity": 28}), "物陰に隠れる")
    check("100 を超えない", roll.chance_percent == 100, roll.chance_percent)
    module, ctx = fresh_mod(llm=FakeLLM("dexterity"), FLAT_BONUS=-50,
                            FREE_ACTION_MODE="llm")
    roll = Roll(10)
    free_call(ctx, Master([roll]), Player({"dexterity": 1}), "物陰に隠れる")
    check("1 を下回らない", roll.chance_percent == 1, roll.chance_percent)

    print("自由入力 / 推定")
    module, ctx = fresh_mod(llm=FakeLLM(raises=True), FLAT_BONUS=0,
                            FREE_ACTION_MODE="llm")
    roll = Roll(50)
    free_call(ctx, Master([roll]), Player({"strength": 28}),
              "崩れた瓦礫をこじ開けて押し破る")
    check("推論が落ちたら語句の表に降りる（strength +20%）",
          roll.chance_percent == 70, roll.chance_percent)
    llm = FakeLLM("よくわからない")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=0, FREE_ACTION_MODE="llm")
    roll = Roll(50)
    free_call(ctx, Master([roll]), Player({"wisdom": 28}),
              "周囲の気配を探り、罠の痕跡を見抜く")
    check("答えが読めなくても語句の表に降りる（wisdom +20%）",
          roll.chance_percent == 70, roll.chance_percent)
    llm = FakeLLM("dexterity")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=0, FREE_ACTION_MODE="keywords")
    roll = Roll(50)
    free_call(ctx, Master([roll]), Player({"charisma": 28}), "店主を説得して値切る")
    check("keywords では1問も聞かない", not llm.asked, llm.asked)
    check("keywords でも加点は入る（charisma +20%）",
          roll.chance_percent == 70, roll.chance_percent)
    llm = FakeLLM("dexterity")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=10, FREE_ACTION_MODE="off")
    roll = Roll(50)
    free_call(ctx, Master([roll]), Player({"dexterity": 28}), "物陰に隠れる")
    check("off なら触らない", roll.chance_percent == 50 and not llm.asked,
          roll.chance_percent)
    module, ctx = fresh_mod(llm=FakeLLM(), FLAT_BONUS=0, FREE_ACTION_MODE="llm")
    roll = Roll(50)
    free_call(ctx, Master([roll]), Player({"dexterity": 10}), "曖昧な何か")
    check("能力値が基準以下なら加点0（素のまま）", roll.chance_percent == 50,
          roll.chance_percent)

    print("自由入力 / 節約")
    llm = FakeLLM("dexterity")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=0, FREE_ACTION_MODE="llm")
    for i in range(3):
        # マスターAI は1入力で何ターンも回り、think は毎回変わる。
        # 控えの鍵が行動文だけになっていないと、ここで3回聞いてしまう。
        free_call(ctx, Master([Roll(50)], think="{}ターン目の思考".format(i)),
                  Player({"dexterity": 28}), "同じ行動文")
    check("同じ行動文には聞き直さない（think が変わっても）",
          len(llm.asked) == 1, llm.asked)

    print("自由入力 / 補正なしの記録")
    module, ctx = fresh_mod(llm=FakeLLM("dexterity"), FLAT_BONUS=0,
                            FREE_ACTION_MODE="llm")
    roll = Roll(50)
    free_call(ctx, Master([roll]), Player({"dexterity": 12}), "物陰に隠れる")
    check("加点0でも確率はそのまま", roll.chance_percent == 50, roll.chance_percent)
    check("加点0の回も記録に残る（動いていないのと見分けが付くように）",
          any("補正なし" in line and "dexterity=12" in line for line in read_log()),
          read_log()[-3:])
    module, ctx = fresh_mod(FLAT_BONUS=0)
    result = Result(RollRequired(5))
    call(ctx, result, Player({"dexterity": 12}))
    check("フィールドイベント側も加点0を残す",
          result.result_type.credibility == 5
          and any("補正なし" in line for line in read_log()[-4:]),
          read_log()[-3:])

    print("自由入力 / 素通し")
    llm = FakeLLM("dexterity")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=10, FREE_ACTION_MODE="llm")
    plain = Master([{"type": "npc_say", "text": "やあ"}])
    returned, times = free_call(ctx, plain, Player({"dexterity": 28}), "話しかける")
    check("roll_the_dice が無ければ何もしない（推論も起こさない）",
          returned is plain and times == 1 and not llm.asked, llm.asked)
    weird, times = free_call(ctx, "まさかの文字列", Player({"dexterity": 28}), "何か")
    check("知らない形は素通し", weird == "まさかの文字列" and times == 1)
    dict_result = {"process": [{"type": "roll_the_dice", "chance_percent": 50}],
                   "think": "", "narration": ""}
    free_call(ctx, dict_result, Player({"dexterity": 28}), "物陰に隠れる")
    check("dict で返っても書き換わる",
          dict_result["process"][0]["chance_percent"] == 80,
          dict_result["process"][0]["chance_percent"])

    print("自由入力 / 会話")
    llm = FakeLLM("charisma")
    module, ctx = fresh_mod(llm=llm, FLAT_BONUS=0, FREE_ACTION_MODE="llm")
    calls = []

    def orig(*args, **kw):
        calls.append(args)
        return Master([Roll(50)])

    hook = ctx.hooks["scripts.llm.llm_manager:"
                     "master_ai_facilitator_from_conversation"]
    player = Player({"charisma": 28})
    result = hook(orig, player, None, None, None, None, None,
                  "エリス「金貨を分けてくれ」", None)
    check("会話からの経路も加点される",
          result.process[0].chance_percent == 70,
          result.process[0].chance_percent)
    check("会話ログを推定の材料にする",
          llm.asked and "金貨" in llm.asked[0][1][0]["content"], llm.asked)

    print("安全")
    module, ctx = fresh_mod(FLAT_BONUS=10)

    class Exploding(object):
        @property
        def result_type(self):
            raise RuntimeError("boom")

    boom = Exploding()
    returned, times = call(ctx, boom, Player({"dexterity": 30}))
    check("戻り値の読み取りが爆ぜても本体は1回だけ呼ばれ、そのまま返る",
          returned is boom and times == 1)

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all good")
    return 0


def read_log():
    path = os.path.join(OUT_DIR, "event_roll.log")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


if __name__ == "__main__":
    sys.exit(main())
