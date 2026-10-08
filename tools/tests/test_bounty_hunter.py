# -*- coding: utf-8 -*-
"""316_bounty_hunter をゲーム抜きで通す。

    python tools/tests/test_bounty_hunter.py

偽の `ui` と偽の `BattleStartManager` を差し込み、次を確認する。

  数え方   … 手配の重さ・合計・2つの開始条件・難易度・クールダウンの境界
  出す     … 条件を満たすと `BattleStartManager(app, 'guard', None)` が組まれ、
             `process_choice` に乗る。1行も出る
  出さない … 条件・抽選・クールダウン・既に戦闘中・追手が向かっている最中
  強さ     … 起こした戦闘の間だけ難易度が差し替わる。ゲーム自身の衛兵には触らない。
             引数の並びが変わっても数のある位置を選ぶ
  降ろす   … 敵が揃えば差し替えが降りる。起こせなかったときも降りる。時限でも降りる
  暦       … 暦が進んだ日数（読めなければ `elapse_days` の日数）を足して控えに残す。
             関所が縮めた回も縮めた後の日数で数える。控えは世界ごと
  衛兵     … 数えるのはゲーム自身の衛兵（'guard'）だけ。依頼中と闘技場は数えない
  合図     … 契機は決めるだけで、起こすのは refresh_choice_buttons の中だけ
  窓口     … 全域手配の線を `wanted` に置く（追手が来ない設定では置かない）。
             追手の戦闘の終わりだけを勝敗つきで1回知らせる。ゲーム自身の衛兵・途中で別の戦闘に変わった回は知らせない
  変えない … どのフックでも本体が1回だけ呼ばれ、戻り値がそのまま返る
  補う     … 取り残された右の欄は自分で畳まない（版4から窓口 `choices`）
"""
import importlib.util
import io
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import confinement, guards  # noqa: E402

TARGETS = (
    "scripts.llm.llm_manager:guard_npc_generator",
    "__main__:InstantaleApp.generate_enemy_instance_from_quest_dict",
    "__main__:BattleStartManager.__init__",
    "__main__:BattleStartManager.start_battle",
    "__main__:BattleEndManager.end_phase",
    "__main__:AreaMoveManager.execute",
    "__main__:MovePhaseManager.move_phase",
    "__main__:InstantaleApp.elapse_days",
    "__main__:InstantaleApp.refresh_choice_buttons",
    "scripts.llm.llm_manager:master_ai_facilitator",
    "__main__:ImprisonmentStartManager.execute",
    "__main__:ImprisonmentEndManager.execute",
    # ローダの窓口 `choices`（組み直しの後の合図を預けた）が、押下と保存も包む。
    "__main__:InstantaleApp.on_button_press",
    "__main__:InstantaleApp.save_game",
)

LOG_NAME = "bounty_hunter_send.log"


def find_mod(suffix, exclude=None):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。

    `exclude` は名前に含まれていたら外す語。
    この MOD には同じ語尾の計測（`220_probe_bounty_hunter`）が居る。
    """
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and (exclude is None or exclude not in name)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry), matches[0]


MOD_DIR, MOD, MOD_FOLDER = find_mod("_bounty_hunter", exclude="_probe_")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class BattleStartManager(object):
    """組まれた引数を控えるだけ。ゲームの戦闘は起こさない。"""

    built = []
    last = None

    def __init__(self, app, enemy_type, enemy_content):
        self.app = app
        self.enemy_type = enemy_type
        self.enemy_content = enemy_content
        BattleStartManager.built.append((enemy_type, enemy_content))
        BattleStartManager.last = self


class App(object):
    def __init__(self, lawfulness, area_id="0", flags=(), world="テストワールド",
                 buttons=()):
        self.player = types.SimpleNamespace(
            experience_level=60,
            area_history={key: {"residency": {}, "achievements": [],
                                "lawfulness": value}
                          for key, value in lawfulness.items()})
        self.area_id = area_id
        self.current_enemy_dict = {}
        # 画面に並んでいる選択肢。`PhaseSpec` の代わりに辞書で持つ（GAME.md §2.2）。
        self.buttons = [{"spec": types.SimpleNamespace(cls_name=name, args=[])}
                        for name in buttons]
        self.world_dict = {"world_data": {"world_name": world}}
        self.said = []
        self.choices = []
        self.painted = []
        for name in flags:
            setattr(self, name, True)

    def lawfulness(self, area_id):
        return self.player.area_history[area_id]["lawfulness"]

    def hurt_wanted(self, area_id, amount=10):
        """ゲームが衛兵との戦闘の後に手配度を下げるのを真似る（実測 -10）。"""
        self.player.area_history[area_id]["lawfulness"] -= amount

    def display_button_load(self, dt=0):
        """ゲーム自身の選択肢の塗り直し。呼ばれた回数だけ控える。"""
        self.painted.append(dt)

    def add_text(self, text):
        self.said.append(text)

    def process_choice(self, phase, choice_text):
        self.choices.append((phase, choice_text))


class FakeScreen(object):
    """`ui.Screen` の代わり。**待たずにその場で走らせる。**"""

    def __init__(self, ctx, write, tag="mod"):
        self.write = write
        self.cancelled = []
        self.scheduled = []

    def when_idle(self, app, then, cancel_if=None, tag="idle", **kw):
        reason = cancel_if() if cancel_if is not None else None
        if reason:
            self.cancelled.append(reason)
            return
        then()

    def schedule(self, fn, delay=0.0):
        self.scheduled.append(delay)
        fn()
        return True

    def say(self, app, text):
        app.add_text(text)

    def start_phase(self, app, phase, choice_text, fallback=None):
        app.process_choice(phase, choice_text)
        return True


class FailingScreen(FakeScreen):
    """`process_choice` が通らない画面。"""

    def start_phase(self, app, phase, choice_text, fallback=None):
        return False


class FakeUI(object):
    """`instantale_modloader.ui` の代わり。**読み方は本物を借りる。**"""

    def __init__(self, app, screen_cls=FakeScreen, manager=BattleStartManager):
        import instantale_modloader.ui as real
        self._real = real
        self.app = app
        self.manager = manager
        self.screen_cls = screen_cls
        self.lawfulness_by_area = real.lawfulness_by_area
        self.lawfulness_of = real.lawfulness_of
        self.area_record = real.area_record
        self.set_lawfulness = real.set_lawfulness
        self.spec_cls_name = real.spec_cls_name
        self.game_day = real.game_day
        self.gold_of = real.gold_of
        self.add_gold = real.add_gold
        self.money = real.money
        self.rewrite_coins = real.rewrite_coins

    def scheduler(self, ctx, tag="mod"):
        """本物と同じく「次のフレーム」。ゲームの外ではその場で実行する。"""
        def schedule(fn, delay=0.0):
            fn()
        return schedule

    def Screen(self, ctx, write, tag="mod", **kw):
        self.screen = self.screen_cls(ctx, write, tag)
        return self.screen

    def find_app(self):
        return self.app

    def current_area(self, app):
        return types.SimpleNamespace(id=getattr(app, "area_id", "0"))

    def area_id_of(self, area):
        return self._real.area_id_of(area)

    def cls_of(self, name):
        return self.manager if name == "BattleStartManager" else None

    def choices_painted(self, app):
        """選択肢が塗り終わったか。`choices_shown` を偽にすると、塗り終わるまで追手を起こさない。"""
        return getattr(app, "choices_shown", True)


class FakeCtx(object):
    _mod = MOD_FOLDER

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.files = {}

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def state_path(self, *parts):
        path = os.path.join(STATE_DIR, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return self.files.get(path, default)

    def write_json(self, path, data):
        self.files[path] = data
        return True

    def logger(self, name, **kw):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        """同じ対象を2度包んだら、後の包みを外側に重ねる（ローダの窓口 `guards` も同じ対象を包む）。"""
        def decorator(func):
            inner = self.hooks.get(target)
            if inner is None:
                self.hooks[target] = func
            else:
                def layered(orig, *args, **kwargs):
                    return func(lambda *a, **k: inner(orig, *a, **k), *args, **kwargs)
                self.hooks[target] = layered
            return func
        return decorator


def load_mod(name="bounty_hunter_mod"):
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(app, screen_cls=FakeScreen, **settings):
    """mod を読み直して当て直す。設定は当てる前に差し替える。"""
    path = os.path.join(OUT_DIR, LOG_NAME)
    if os.path.exists(path):
        os.remove(path)
    BattleStartManager.built = []
    BattleStartManager.last = None
    module = load_mod()
    module.ui = FakeUI(app, screen_cls)
    # 衛兵の戦闘を組むのと強さの差し替えはローダの窓口 `guards`。偽の画面を同じに見せ、包みは当て直す。
    guards.reset()
    guards.ui = module.ui
    module._RNG = types.SimpleNamespace(random=lambda: 0.0)   # 抽選は必ず当たる
    for name, value in settings.items():
        setattr(module, name, value)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def read_log():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def counting(result=None):
    calls = []

    def orig(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    return orig, calls


_display_button_load = App.display_button_load


def game_guard(ctx, app, enemy_type="guard"):
    """ゲーム自身が戦闘を起こしたことにする（`__init__` を素で通す）。

    既定は衛兵。依頼中の遭遇・ボスは 'in_quest'、闘技場は 'colosseum'（GAME.md の戦闘BGMの表）。
    """
    orig, _ = counting(None)
    ctx.hooks["__main__:BattleStartManager.__init__"](
        orig, types.SimpleNamespace(), app, enemy_type, None)


def calendar(app, step):
    """暦を `step` 日だけ進める `elapse_days` の本体（関所が差し替えた後の数の代わり）。"""
    calls = []

    def orig(this, days, *args, **kwargs):
        calls.append(days)
        this.days_elapsed = getattr(this, "days_elapsed", 0) + step
        return "戻り値"
    return orig, calls


def start_of(ctx, app, phase):
    """組まれたマネージャの `start_battle` を通す。"""
    orig, calls = counting(None)
    ctx.hooks["__main__:BattleStartManager.start_battle"](orig, phase)
    return calls


def ready_screen(ctx, app):
    """ゲームが選択肢を組み直した合図を通す（起こすのはここだけ）。"""
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](orig, app)
    return returned, len(calls)


def arrive(ctx, app, ready=True):
    """土地に着いたことにして、画面が整ったところまで進める。"""
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:AreaMoveManager.execute"](
        orig, types.SimpleNamespace(app=app), "馬車(1000G)")
    if ready:
        ready_screen(ctx, app)
    return returned, len(calls)


# ------------------------------------------------------------------ 検査
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    import importlib
    hunt = importlib.import_module("bounty_hunter_mod.hunt") \
        if "bounty_hunter_mod" in sys.modules else None

    print("数え方")
    wanted = {"0": -22, "1": 10, "2": -18}
    module, ctx = fresh_mod(App(wanted))
    hunt = module.hunt
    check("注入時の自己検証が通っている",
          not [level for level, _ in ctx.logs if level == "ERROR"], ctx.logs)
    check("手配された土地だけに重さが付く",
          hunt.weights(wanted) == {"0": 22, "2": 18}, hunt.weights(wanted))
    check("合計は手配された土地ぶんだけ",
          hunt.total_weight(wanted) == 40, hunt.total_weight(wanted))
    check("今いる土地の重さを引ける",
          hunt.area_weight(wanted, "0") == 22 and hunt.area_weight(wanted, "1") == 0)
    check("平常（10）は重さ0", hunt.weight_of(10) == 0)
    check("手配度 0 も重さ0（309_ と同じ境界）", hunt.weight_of(0) == 0)
    check("数でない値は重さ0",
          hunt.weight_of(True) == 0 and hunt.weight_of(None) == 0
          and hunt.weight_of("-5") == 0)
    check("今いる土地だけで条件を満たす",
          hunt.should_send(20, 20, 20, 40) and not hunt.should_send(19, 19, 20, 40))
    check("合計だけでも条件を満たす（逃げても追ってくる）",
          hunt.should_send(0, 40, 20, 40))
    check("手配が無ければ出さない", not hunt.should_send(0, 0, 20, 40))
    check("殺害1件（重さ10）では追手が来ない（既定の線）",
          not hunt.should_send(10, 10, 20, 40))
    check("2件（重さ20）で追手が来る", hunt.should_send(20, 20, 20, 40))
    check("難易度は合計で上がる",
          hunt.difficulty_of(0, 20, 1.0, 75) == 20
          and hunt.difficulty_of(30, 20, 1.0, 75) == 50)
    check("難易度は上限で頭打ち", hunt.difficulty_of(500, 20, 1.0, 75) == 75)
    check("重さ1あたりの値を変えられる",
          hunt.difficulty_of(10, 20, 2.5, 75) == 45)
    check("初回はクールダウン無し", hunt.ready(0, None, 10))
    check("日数が足りなければ出さない", not hunt.ready(5, 0, 10))
    check("ちょうど空けば出す", hunt.ready(10, 0, 10))
    check("クールダウン 0 なら毎回", hunt.ready(0, 0, 0))
    check("前金は依頼1件の報酬 × 割合", hunt.purse(4500, 30) == 1350 and hunt.purse(1001, 50) == 500)
    check("報酬が引けない・割合が 0 なら前金は 0",
          hunt.purse(None, 30) == 0 and hunt.purse(4500, 0) == 0 and hunt.purse(True, 30) == 0)

    print("出す")
    app = App({"0": -22, "1": 10, "2": -18})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    check("対象が全部登録される",
          sorted(ctx.hooks) == sorted(TARGETS), set(TARGETS) ^ set(ctx.hooks))
    returned, times = arrive(ctx, app)
    check("土地に着くと追手が出る", BattleStartManager.built == [("guard", None)],
          BattleStartManager.built)
    check("ゲームの選択肢の経路に乗る",
          len(app.choices) == 1 and app.choices[0][1] == module.CHOICE_TEXT,
          app.choices)
    check("1行出る", app.said == [module.ANNOUNCE], app.said)
    check("到着そのものは素通し（本体1回・戻り値そのまま）",
          returned == "戻り値" and times == 1, (returned, times))
    check("出したことが記録に残る",
          any("追手を出した" in line for line in read_log()), read_log()[-2:])

    print("出さない")
    clean = App({"0": 10, "1": 10})
    module, ctx = fresh_mod(clean, CHANCE_PERCENT=100)
    arrive(ctx, clean)
    check("手配されていなければ何も起きない",
          not BattleStartManager.built
          and not [line for line in read_log() if "追手" in line], read_log())

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    module._RNG = types.SimpleNamespace(random=lambda: 0.99)
    module.CHANCE_PERCENT = 30
    arrive(ctx, app)
    check("発生率は％で読む（30% に 0.99 は外れ）",
          module.CHANCE_PERCENT == 30)
    check("抽選に外れたら出さない（外れも記録する）",
          not BattleStartManager.built
          and any("抽選に外れた" in line for line in read_log()), read_log())

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, COOLDOWN_DAYS=10)
    arrive(ctx, app)
    # 敵が揃ったことにして差し替えを降ろす（降りていないと、次は
    # 「追手が既に向かっている」で止まってクールダウンまで届かない）。
    ctx.hooks["__main__:BattleStartManager.start_battle"](
        counting(None)[0], BattleStartManager.last)
    arrive(ctx, app)
    check("一度出たらクールダウンの間は出ない",
          len(BattleStartManager.built) == 1
          and any("日空ける" in line for line in read_log()), read_log()[-2:])

    fighting = App({"0": -25}, flags=("in_battle",))
    module, ctx = fresh_mod(fighting, CHANCE_PERCENT=100)
    arrive(ctx, fighting)
    check("戦闘中は出さない",
          not BattleStartManager.built
          and any("in_battle" in line for line in read_log()), read_log()[-2:])

    painting = App({"0": -25})
    painting.choices_shown = False            # ゲームの塗りがまだ（偽の画面ではずっと塗り終わらない）
    module, ctx = fresh_mod(painting, CHANCE_PERCENT=100)
    arrive(ctx, painting)
    check("塗り終わらなくても、待つフレームの上限で起こす",
          BattleStartManager.built == [("guard", None)]
          and any("塗り終わらないまま" in line for line in read_log()), read_log()[-2:])
    painted = App({"0": -25})
    module, ctx = fresh_mod(painted, CHANCE_PERCENT=100)
    seen = len(read_log())
    arrive(ctx, painted)
    check("塗り終わっていればすぐ起こす（待った記録は出ない）",
          BattleStartManager.built == [("guard", None)]
          and not any("塗り終わらないまま" in line for line in read_log()[seen:]),
          read_log()[seen:])

    print("釈放と、決まった後に軽くなった手配")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app, ready=False)                 # 服役の年に追手が決まった（控えがある）
    ctx.hooks["__main__:ImprisonmentEndManager.execute"](
        lambda self: "釈放", types.SimpleNamespace(app=app))
    ready_screen(ctx, app)
    check("釈放されたら決まっていた追手を落とす",
          not BattleStartManager.built
          and any("決まっていた追手を落とし" in line for line in read_log()), read_log()[-2:])
    arrive(ctx, app)
    check("釈放の後は追手の間を数え直す（すぐには来ない）",
          not BattleStartManager.built
          and any("日空ける" in line for line in read_log()), read_log()[-2:])

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app, ready=False)
    app.player.area_history["0"]["lawfulness"] = 10      # 決まった後に罰金を納めた など
    ready_screen(ctx, app)
    check("決まった後に手配が軽くなったら出さない",
          not BattleStartManager.built
          and any("決まった後に手配が軽くなった" in line for line in read_log()), read_log()[-2:])

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app, ready=False)
    app.player.area_history["0"]["lawfulness"] = -60     # 決まった後にさらに罪を重ねた
    ready_screen(ctx, app)
    check("出すときの強さは今の手配の重さで決め直す",
          BattleStartManager.built == [("guard", None)]
          and any("難易度75" in line for line in read_log()), read_log()[-2:])

    print("服役中")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app, ready=False)                 # 逮捕の前に追手が決まっていた
    ctx.hooks["__main__:ImprisonmentStartManager.execute"](
        lambda self: "刑", types.SimpleNamespace(app=app))
    app.buttons = [{"spec": types.SimpleNamespace(cls_name="ImprisonmentPhaseManager", args=[])}]
    ready_screen(ctx, app)
    check("刑が始まったら決まっていた追手を落とす",
          not BattleStartManager.built
          and any("刑が始まった。決まっていた追手を落とし" in line for line in read_log()), read_log()[-2:])
    seen = len(read_log())
    ctx.hooks["__main__:InstantaleApp.elapse_days"](counting(None)[0], app, 365)
    ready_screen(ctx, app)
    check("服役の年は追手を決めない",
          not BattleStartManager.built
          and not any("追手が決まった" in line for line in read_log()[seen:]), read_log()[seen:])

    app = App({"0": -25}, buttons=("ImprisonmentPhaseManager",))     # 服役中のセーブを読んだ
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](counting(None)[0], app, 365)
    check("服役の選択肢が並んでいれば、刑の始まりを見ていなくても決めない",
          not any("追手が決まった" in line for line in read_log()), read_log()[-3:])

    # 脱獄の一覧（ゲームの服役のボタンが無い画面）は、閉じ込める側の MOD が窓口で知らせる
    confinement.reset()
    confinement.declare("336_test", ctx, lambda app_: "牢の中で脱獄の備えを選んでいる")
    app.buttons = [{"spec": types.SimpleNamespace(cls_name="JustSetButtonToNormalPhase", args=[])}]
    ctx.hooks["__main__:InstantaleApp.elapse_days"](counting(None)[0], app, 365)
    ready_screen(ctx, app)
    check("脱獄の一覧の間も追手を決めず、起こさない",
          not BattleStartManager.built
          and not any("追手が決まった" in line for line in read_log()), read_log()[-3:])
    confinement.reset()
    app.buttons = [{"spec": types.SimpleNamespace(cls_name="MovePhaseManager", args=[])}]
    ready_screen(ctx, app)                        # 脱獄して牢の外の画面に戻った
    check("牢の外の画面に戻ったら服役中の印を下ろす",
          any("牢の外の画面に戻った" in line for line in read_log()), read_log()[-2:])
    arrive(ctx, app)
    check("牢を出た後は追手が来る", BattleStartManager.built == [("guard", None)], read_log()[-3:])

    print("強さ")
    app = App({"0": -30})            # 合計30 -> 難易度 20 + 30 = 50
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    guard_hook = ctx.hooks["scripts.llm.llm_manager:guard_npc_generator"]
    instance = ctx.hooks["__main__:InstantaleApp.generate_enemy_instance_from_quest_dict"]

    def build_enemies():
        """`create_guard_enemies` の中で起きることを真似る。渡った引数を控える。"""
        seen = {}
        orig, calls = counting("EnemyData")
        guard_hook(orig, "area", "world", 20)
        seen["guard"] = calls
        orig, calls = counting(None)
        instance(orig, app, {"type": "normal"}, "base.png", "pixel.png",
                 "pos", "neg", 20)
        seen["instance"] = calls
        return seen

    orig, calls = counting("EnemyData")
    returned = guard_hook(orig, "area", "world", 20)
    check("戦闘の外では触らない",
          calls[0][0] == ("area", "world", 20) and returned == "EnemyData", calls)
    arrive(ctx, app)
    seen = {}

    def start_body(*args, **kwargs):
        seen.update(build_enemies())
        return "戻り値"

    returned = ctx.hooks["__main__:BattleStartManager.start_battle"](
        start_body, BattleStartManager.last)
    check("自分の start_battle の中では難易度が差し替わる",
          seen["guard"][0][0] == ("area", "world", 50), seen)
    check("敵の実体にも同じ難易度が渡る",
          seen["instance"][0][0][-1] == 50, seen)
    check("start_battle の戻り値はそのまま返る", returned == "戻り値", returned)
    orig, calls = counting("EnemyData")
    guard_hook(orig, "area", "world", 20)
    check("start_battle を抜けたら差し替えは閉じる",
          calls[0][0] == ("area", "world", 20), calls)

    app = App({"0": -30})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    guard_hook = ctx.hooks["scripts.llm.llm_manager:guard_npc_generator"]
    instance = ctx.hooks["__main__:InstantaleApp.generate_enemy_instance_from_quest_dict"]
    arrive(ctx, app)
    seen = {}

    def start_body_odd(*args, **kwargs):
        orig, calls = counting(None)
        instance(orig, app, {"type": "normal"}, "base.png", 20)
        seen["short"] = calls
        orig, calls = counting(None)
        instance(orig, app, {"type": "normal"}, "base.png", "pos")
        seen["none"] = calls
        return None

    ctx.hooks["__main__:BattleStartManager.start_battle"](
        start_body_odd, BattleStartManager.last)
    check("引数の並びが変わっても、数のある位置を選ぶ",
          seen["short"][0][0][-1] == 50, seen["short"])
    check("数が無ければ素のまま作らせる",
          seen["none"][0][0] == (app, {"type": "normal"}, "base.png", "pos"),
          seen["none"])

    print("ゲーム自身の衛兵とぶつからない")
    app = App({"0": -30})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    game_guard(ctx, app)
    check("ゲームが衛兵を出したことを記録する",
          any("ゲーム自身が衛兵を出した" in line for line in read_log()), read_log())
    arrive(ctx, app)
    check("その直後は追手を出さない（1回の遭遇として数える）",
          not BattleStartManager.built
          and any("日空ける" in line for line in read_log()), read_log()[-2:])
    check("数えたことが記録に残る",
          any("1回の遭遇として数える" in line for line in read_log()), read_log()[-3:])

    app = App({"0": -30})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, YIELD_TO_GUARDS=False)
    game_guard(ctx, app)
    arrive(ctx, app)
    check("譲る設定を切れば出す", BattleStartManager.built == [("guard", None)],
          BattleStartManager.built)

    for enemy_type, what in (("in_quest", "依頼中の遭遇・ボス"), ("colosseum", "闘技場")):
        app = App({"0": -30})
        module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
        arrive(ctx, app, ready=False)                 # 追手が決まった（控えがある）
        game_guard(ctx, app, enemy_type)
        check("{}の戦闘は衛兵として数えない".format(what),
              not any("1回の遭遇として数える" in line for line in read_log())
              and not ctx.files, (read_log()[-2:], ctx.files))
        ready_screen(ctx, app)
        check("{}の戦闘では決まっていた追手を落とさない".format(what),
              BattleStartManager.built == [("guard", None)],
              BattleStartManager.built)

    app = App({"0": -30})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    guard_hook = ctx.hooks["scripts.llm.llm_manager:guard_npc_generator"]
    orig, calls = counting("EnemyData")
    guard_hook(orig, "a", "w", 20)
    check("自分の戦闘の外なら、ゲームの衛兵は素の難易度で作られる",
          calls[0][0] == ("a", "w", 20), calls)
    app.current_enemy_dict.update({"衛兵1": types.SimpleNamespace(name="衛兵")})
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:BattleStartManager.start_battle"](
        orig, types.SimpleNamespace(app=app))
    check("ゲームが出した衛兵は改名しない",
          sorted(app.current_enemy_dict) == ["衛兵1"], app.current_enemy_dict)
    check("そのときも本体は1回だけ呼ばれ、戻り値はそのまま",
          returned == "戻り値" and len(calls) == 1, (returned, calls))

    print("降ろす")
    app = App({"0": -30})
    module, ctx = fresh_mod(app, FailingScreen, CHANCE_PERCENT=100)
    arrive(ctx, app)
    check("戦闘を起こせなかったら控えを降ろす",
          any("起こせなかった" in line for line in read_log()), read_log()[-2:])
    arrive(ctx, app)
    check("降ろした後はまた出せる（向かっているまま固まらない）",
          len(BattleStartManager.built) == 2, BattleStartManager.built)

    app = App({"0": -30})
    module, ctx = fresh_mod(app, FailingScreen, CHANCE_PERCENT=100,
                            ARM_MAX_SIGNALS=0)
    arrive(ctx, app, ready=False)
    module.ui.screen.__class__ = FakeScreen      # 起こせる画面に戻す
    for _ in range(3):
        ready_screen(ctx, app)
    arrive(ctx, app)
    check("出ないまま画面が変われば控えを降ろす",
          any("控えを降ろす" in line or "控えを降ろした" in line
              for line in read_log()), read_log()[-4:])

    print("名前")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    check("連番は元の鍵から写す",
          module.renamed_keys(["衛兵1", "衛兵2", "衛兵3"], "賞金稼ぎ")
          == {"衛兵1": "賞金稼ぎ1", "衛兵2": "賞金稼ぎ2", "衛兵3": "賞金稼ぎ3"},
          module.renamed_keys(["衛兵1", "衛兵2", "衛兵3"], "賞金稼ぎ"))
    check("数字が無ければ順番で振る",
          module.renamed_keys(["衛兵"], "賞金稼ぎ") == {"衛兵": "賞金稼ぎ1"},
          module.renamed_keys(["衛兵"], "賞金稼ぎ"))
    check("ぶつかる鍵は付け替えない",
          module.renamed_keys(["衛兵1", "賞金稼ぎ1"], "賞金稼ぎ") == {},
          module.renamed_keys(["衛兵1", "賞金稼ぎ1"], "賞金稼ぎ"))
    check("名前が空なら何もしない", module.renamed_keys(["衛兵1"], "") == {})

    def enemy():
        return types.SimpleNamespace(name="衛兵")

    arrive(ctx, app)
    app.current_enemy_dict.update({"衛兵1": enemy(), "衛兵2": enemy()})
    held = app.current_enemy_dict
    orig, _ = counting(None)
    ctx.hooks["__main__:BattleStartManager.start_battle"](
        orig, BattleStartManager.last)
    check("鍵が付け替わる",
          sorted(app.current_enemy_dict) == ["賞金稼ぎ1", "賞金稼ぎ2"],
          sorted(app.current_enemy_dict))
    check("名前も付け替わる",
          all(e.name == "賞金稼ぎ" for e in app.current_enemy_dict.values()))
    check("入れ物は作り直さない（ゲームが握っている辞書のまま）",
          app.current_enemy_dict is held)
    check("付け替えたことが記録に残る",
          any("改名" in line for line in read_log()), read_log()[-2:])

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, HUNTER_NAME="")
    arrive(ctx, app)
    app.current_enemy_dict.update({"衛兵1": enemy()})
    ctx.hooks["__main__:BattleStartManager.start_battle"](
        orig, BattleStartManager.last)
    check("名前を空にすればゲームのまま",
          sorted(app.current_enemy_dict) == ["衛兵1"], app.current_enemy_dict)

    print("押されていないぶんを補う")
    # 版4から、取り残された右の欄はローダの窓口 `choices` が畳む（tools/tests/test_choices.py の [右の欄]）。
    app = App({"0": -25}, buttons=["MovePhaseManager", "DisplayTalkChoice"])
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    app.current_enemy_dict.update({"衛兵1": types.SimpleNamespace(name="衛兵")})
    ctx.hooks["__main__:BattleStartManager.start_battle"](
        counting(None)[0], BattleStartManager.last)
    app.buttons = App({"0": -25}, buttons=["BattlePhaseManager",
                                           "SkillChoicePhaseManager"]).buttons
    ready_screen(ctx, app)
    check("自分では塗り直さない（右の欄は窓口 choices が畳む）", app.painted == [], app.painted)

    print("倒しても手配度は増えない")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    app.hurt_wanted("0")                      # ゲームが戦闘後に下げる（-25 -> -35）
    check("戦闘の後は下がっている", app.lawfulness("0") == -35, app.lawfulness("0"))
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:BattleEndManager.end_phase"](
        orig, types.SimpleNamespace(app=app))
    check("下がったぶんが戻る", app.lawfulness("0") == -25, app.lawfulness("0"))
    check("戦闘の終わりは素通し（本体1回・戻り値そのまま）",
          returned == "戻り値" and len(calls) == 1, (returned, calls))
    check("戻したことが記録に残る",
          any("手配度を戻した" in line for line in read_log()), read_log()[-2:])
    check("見張りは合図の回数で数える",
          isinstance(module.PROTECT_MAX_SIGNALS, int), module.PROTECT_MAX_SIGNALS)

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    ctx.hooks["__main__:BattleEndManager.end_phase"](
        counting(None)[0], types.SimpleNamespace(app=app))
    app.hurt_wanted("0")                      # 戦闘の終わりより後に下がった回
    # 施設の契機は既定で切ってあるが、**戻す側は契機の入切に関わらず働く**
    ready_screen(ctx, app)
    check("戦闘の終わりで捕まえ損ねても、次に画面が整えば戻す",
          app.lawfulness("0") == -25, app.lawfulness("0"))

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    app.player.area_history["0"]["lawfulness"] = 10     # 罰金を払った（軽くなった）
    ctx.hooks["__main__:BattleEndManager.end_phase"](
        counting(None)[0], types.SimpleNamespace(app=app))
    check("軽くなった側は戻さない（309_ で払ったぶんを巻き戻さない）",
          app.lawfulness("0") == 10, app.lawfulness("0"))

    app = App({"0": -25, "1": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    app.hurt_wanted("0")
    app.hurt_wanted("1")
    ctx.hooks["__main__:BattleEndManager.end_phase"](
        counting(None)[0], types.SimpleNamespace(app=app))
    check("戻すのは追手を出した土地だけ",
          (app.lawfulness("0"), app.lawfulness("1")) == (-25, -35),
          (app.lawfulness("0"), app.lawfulness("1")))

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, KEEP_WANTED=False)
    arrive(ctx, app)
    app.hurt_wanted("0")
    ctx.hooks["__main__:BattleEndManager.end_phase"](
        counting(None)[0], types.SimpleNamespace(app=app))
    check("切れば戻さない（ゲームのまま手配が重くなる）",
          app.lawfulness("0") == -35, app.lawfulness("0"))

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, PROTECT_MAX_SIGNALS=0)
    arrive(ctx, app)
    for _ in range(2):
        ready_screen(ctx, app)
    app.hurt_wanted("0")
    ctx.hooks["__main__:BattleEndManager.end_phase"](
        counting(None)[0], types.SimpleNamespace(app=app))
    check("見張る合図の回数を過ぎたら戻さない",
          app.lawfulness("0") == -35, app.lawfulness("0"))

    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    app.current_enemy_dict = {"賞金稼ぎ1": object()}
    app.buttons = App({"0": -25}, buttons=["BattlePhaseManager"]).buttons
    for _ in range(module.PROTECT_MAX_SIGNALS + 5):     # 手番ごとに合図が来る長い戦闘
        ready_screen(ctx, app)
    app.current_enemy_dict = {}
    app.buttons = []
    app.hurt_wanted("0")
    ready_screen(ctx, app)
    check("戦闘中の合図では見張りが尽きない（長い戦闘の後も戻す）",
          app.lawfulness("0") == -25, app.lawfulness("0"))

    print("追手の戦闘の終わりを窓口へ知らせる")
    from instantale_modloader import wanted
    heard = []

    def listen(app_, hunt):
        heard.append(hunt)

    def end(app_, end_type):
        ctx.hooks["__main__:BattleEndManager.end_phase"](
            counting(None)[0], types.SimpleNamespace(app=app_, end_type=end_type))

    wanted.reset()
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    wanted.on_hunt_end("listener", None, listen)
    check("全域手配の線を窓口に置く", wanted.hunted_line(app) == (40, "316_bounty_hunter"),
          wanted.hunted_line(app))
    arrive(ctx, app)
    start_of(ctx, app, BattleStartManager.last)
    end(app, "won")
    check("勝った追手の戦闘を知らせる（勝敗・難易度・重さ・持ち主）",
          len(heard) == 1 and heard[0]["outcome"] == "won"
          and heard[0]["difficulty"] == 45 and heard[0]["here"] == 25
          and heard[0]["by"] == "316_bounty_hunter", heard)
    end(app, "won")
    check("知らせるのは1回だけ", len(heard) == 1, heard)

    del heard[:]
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    start_of(ctx, app, BattleStartManager.last)
    end(app, "escaped")
    check("逃げた回も勝敗つきで知らせる", [h["outcome"] for h in heard] == ["escaped"], heard)

    print("追手に勝つと前金")
    functions = types.ModuleType("scripts.functions")
    functions.get_quest_reward = lambda difficulty: difficulty * 100
    saved_functions = sys.modules.get("scripts.functions")
    sys.modules["scripts.functions"] = functions

    def hunt_once(end_type, **settings):
        app_ = App({"0": -25})
        app_.player.gold = 1000
        module_, ctx_ = fresh_mod(app_, CHANCE_PERCENT=100, **settings)
        arrive(ctx_, app_)
        start_of(ctx_, app_, BattleStartManager.last)
        ctx_.hooks["__main__:BattleEndManager.end_phase"](
            counting(None)[0], types.SimpleNamespace(app=app_, end_type=end_type))
        return app_

    won = hunt_once("won")
    check("勝てば難易度での依頼1件の報酬 × 30% が入る（難易度45 → 4500 × 30%）",
          won.player.gold == 1000 + 1350, won.player.gold)
    check("本文に前金の1行", any("1,350" in text for text in won.said), won.said)
    check("逃げた回は入らない", hunt_once("escaped").player.gold == 1000)
    check("切ってあれば入らない", hunt_once("won", BOUNTY_ENABLED=False).player.gold == 1000)
    check("割合を変えられる", hunt_once("won", BOUNTY_PURSE_PCT=10).player.gold == 1450)
    guard_app = App({"0": -25})
    guard_app.player.gold = 1000
    _module, guard_ctx = fresh_mod(guard_app, CHANCE_PERCENT=100)
    game_guard(guard_ctx, guard_app)
    start_of(guard_ctx, guard_app, types.SimpleNamespace(app=guard_app))
    guard_ctx.hooks["__main__:BattleEndManager.end_phase"](
        counting(None)[0], types.SimpleNamespace(app=guard_app, end_type="won"))
    check("ゲーム自身の衛兵に勝っても入らない", guard_app.player.gold == 1000, guard_app.player.gold)
    if saved_functions is None:
        sys.modules.pop("scripts.functions", None)
    else:
        sys.modules["scripts.functions"] = saved_functions

    del heard[:]
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    game_guard(ctx, app)
    start_of(ctx, app, types.SimpleNamespace(app=app))
    end(app, "won")
    check("ゲーム自身の衛兵の戦闘は知らせない", heard == [], heard)

    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app)
    start_of(ctx, app, BattleStartManager.last)
    game_guard(ctx, app, "in_quest")          # 追手の戦闘が終わりを通らずに別の戦闘が始まった
    end(app, "won")
    check("別の戦闘が始まったら追手の戦闘と取り違えない", heard == [], heard)

    module, ctx = fresh_mod(app, CHANCE_PERCENT=0)
    check("追手が来ない設定では線を置かない", wanted.hunted_line(app) == (None, None),
          wanted.hunted_line(app))
    wanted.reset()

    print("抽選は画面ごとに1回")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    module._RNG = types.SimpleNamespace(random=lambda: 0.99)
    module.CHANCE_PERCENT = 30
    for _ in range(3):        # 1つの行動で契機が3回来る
        ctx.hooks["__main__:InstantaleApp.elapse_days"](counting(None)[0], app, 1)
    check("画面が変わるまで抽選は1回だけ",
          len([l for l in read_log() if "抽選に外れた" in l]) == 1, read_log())
    ready_screen(ctx, app)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](counting(None)[0], app, 1)
    check("画面が変われば また抽選する",
          len([l for l in read_log() if "抽選に外れた" in l]) == 2, read_log())

    print("多段の場面には割り込まない")
    module, ctx = fresh_mod(App({"0": -25}), CHANCE_PERCENT=100)
    check("品書きは場面の最中ではない",
          not module.sequence_in(["MovePhaseManager", "DisplayVacationChoice"]))
    check("部屋選びは場面の最中",
          module.sequence_in(["VacationStartManager"]) == "VacationStartManager")
    check("休養/訓練/交流も場面の最中",
          module.sequence_in(["VacationRestManager"]) == "VacationRestManager")
    check("空の名前で落ちない", not module.sequence_in([None, ""]))

    # 宿泊の最中に日数が進む（部屋選び -> 日数送り -> 休養…）。
    inn = App({"0": -25}, buttons=["VacationStartManager", "MovePhaseManager"])
    module, ctx = fresh_mod(inn, CHANCE_PERCENT=100)
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, inn, 7)
    ready_screen(ctx, inn)                    # 宿泊の中でも画面は組み直される
    check("宿泊の途中では追手を出さない",
          not BattleStartManager.built, BattleStartManager.built)
    check("日数送りそのものは素通し（本体1回・戻り値そのまま）",
          returned == "戻り値" and len(calls) == 1, (returned, calls))
    check("日数は数える（場面が終わってから使う）",
          [v for v in ctx.files.values()] == [{"days": 7.0, "last": None}],
          ctx.files)
    # 宿泊が終わって普通の選択肢に戻る。
    inn.buttons = App({"0": -25},
                      buttons=["MovePhaseManager", "DisplayVacationChoice"]).buttons
    ready_screen(ctx, inn)
    check("場面が終わって画面が整えば出る",
          BattleStartManager.built == [("guard", None)], BattleStartManager.built)

    town = App({"0": -25}, buttons=["MovePhaseManager", "DisplayVacationChoice"])
    module, ctx = fresh_mod(town, CHANCE_PERCENT=100)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](counting(None)[0], town, 7)
    ready_screen(ctx, town)
    check("宿屋の品書きの前なら出す", BattleStartManager.built == [("guard", None)],
          BattleStartManager.built)

    print("起こすのは画面が整った合図の中だけ")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    arrive(ctx, app, ready=False)
    check("契機だけでは出さない（決めるところまで）",
          not BattleStartManager.built
          and any("追手が決まった" in line for line in read_log()), read_log()[-2:])
    returned, calls = ready_screen(ctx, app)
    check("合図が来たら出る", BattleStartManager.built == [("guard", None)],
          BattleStartManager.built)
    check("合図そのものは素通し（本体1回・戻り値そのまま）",
          returned == "戻り値" and calls == 1, (returned, calls))
    check("追手の文は合図の後に出る", app.said == [module.ANNOUNCE], app.said)
    ready_screen(ctx, app)
    check("2度目の合図では出ない（控えは1回で消える）",
          len(BattleStartManager.built) == 1, BattleStartManager.built)

    app = App({"0": -25})
    module, ctx = fresh_mod(app, FailingScreen, CHANCE_PERCENT=100,
                            DUE_MAX_SIGNALS=0)
    arrive(ctx, app, ready=False)
    module.ui.screen.__class__ = FakeScreen
    for _ in range(2):
        ready_screen(ctx, app)
    check("画面が何度変わっても出せなければ捨てる",
          not BattleStartManager.built
          and any("回変わっても出せなかった" in line for line in read_log()),
          read_log()[-3:])

    print("自由入力は旗が立ったまま契機が来る")
    # 実機では `master_ai_facilitator` の後、`in_free_input` はまだ立っている。
    busy = App({"0": -25}, flags=("in_free_input",))
    module, ctx = fresh_mod(busy, CHANCE_PERCENT=100, ON_FREE_ACTION=True)
    orig, calls = counting("応答")
    returned = ctx.hooks["scripts.llm.llm_manager:master_ai_facilitator"](orig, "x")
    check("旗が立っていても控えは置く",
          any("追手が決まった" in line for line in read_log()), read_log()[-2:])
    check("自由行動そのものは素通し（本体1回・戻り値そのまま）",
          returned == "応答" and len(calls) == 1, (returned, calls))
    ready_screen(ctx, busy)
    check("旗が立ったままの合図では出さない",
          not BattleStartManager.built, BattleStartManager.built)
    busy.in_free_input = False
    ready_screen(ctx, busy)
    check("旗が下りた後の合図で出る",
          BattleStartManager.built == [("guard", None)], BattleStartManager.built)

    fighting = App({"0": -25}, flags=("in_battle",))
    module, ctx = fresh_mod(fighting, CHANCE_PERCENT=100, ON_FREE_ACTION=True)
    ctx.hooks["scripts.llm.llm_manager:master_ai_facilitator"](counting(None)[0], "x")
    check("戦闘中は控えすら置かない",
          not [line for line in read_log() if "追手が決まった" in line]
          and any("in_battle なので出さない" in line for line in read_log()),
          read_log()[-2:])
    fighting.in_battle = False
    ready_screen(ctx, fighting)
    check("戦闘が終わっても、その回は出ない",
          not BattleStartManager.built, BattleStartManager.built)

    print("暦")
    app = App({"0": -25})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, ON_DAYS=False, COOLDOWN_DAYS=10)
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, app, 14)
    check("日数送りは素通し（本体1回・戻り値そのまま）",
          returned == "戻り値" and calls == [((app, 14), {})], (returned, calls))
    check("日数を足して控えに残す",
          [v for v in ctx.files.values()] == [{"days": 14.0, "last": None}],
          ctx.files)
    check("ON_DAYS が OFF なら日数では出さない", not BattleStartManager.built)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, app, True)
    check("True は1日と数えない",
          [v for v in ctx.files.values()] == [{"days": 14.0, "last": None}],
          ctx.files)
    module, ctx2 = fresh_mod(app, CHANCE_PERCENT=100, ON_DAYS=True)
    ctx2.hooks["__main__:InstantaleApp.elapse_days"](orig, app, 3)
    ready_screen(ctx2, app)
    check("ON_DAYS が ON なら日数の経過で出る",
          BattleStartManager.built == [("guard", None)], BattleStartManager.built)
    key = list(ctx2.files)[0]
    check("控えは世界ごとのファイルに置く",
          "テストワールド" in key or key.endswith(".json"), key)

    # 関所（durations）が 90 を 14 に縮めた移動。この包みには素の 90 が来る。
    app = App({"0": -25})
    app.days_elapsed = 100
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100, ON_DAYS=False)
    orig, calls = calendar(app, 14)
    returned = ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, app, 90)
    check("暦が読めれば、渡された日数ではなく暦の進みで数える",
          [v for v in ctx.files.values()] == [{"days": 14.0, "last": None}],
          ctx.files)
    check("そのときも本体は1回・渡す日数はそのまま",
          returned == "戻り値" and calls == [90], (returned, calls))
    orig, calls = calendar(app, 0)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, app, 30)
    check("暦が動かなかった回は数えない",
          [v for v in ctx.files.values()] == [{"days": 14.0, "last": None}],
          ctx.files)
    orig, calls = calendar(app, 7)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, app, 30)
    check("週単位の宿泊（30 が 7 に縮む）も7日と数える",
          [v for v in ctx.files.values()] == [{"days": 21.0, "last": None}],
          ctx.files)

    print("変えない")
    app = App({"0": 10})
    module, ctx = fresh_mod(app, CHANCE_PERCENT=100)
    for target in TARGETS:
        orig, calls = counting("戻り値")
        returned = ctx.hooks[target](orig, app, "A", "B", "C", "D", 1)
        if returned != "戻り値" or len(calls) != 1:
            check("{} が素通しする".format(target), False, (returned, calls))
            break
    else:
        check("10の対象すべてで本体が1回だけ呼ばれ、戻り値がそのまま返る", True)
    check("記録に失敗していない", not ctx.errors, ctx.errors)

    print()
    if failures:
        print("失敗 {} 件".format(len(failures)))
        for name in failures:
            print("  - " + name)
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
