# -*- coding: utf-8 -*-
"""306_party_train_exp.py をゲーム抜きで通す。

    python tools/tests/test_party_train_exp.py

偽の app / Character / 訓練マネージャ / Clock を差し込み、次を確認する。

  写す     … 宿屋の訓練でプレイヤーに入った点数が、同行者にもそのまま入る
  レベル   … 上がるぶんだけ上がる（`gain_exp` が内部で上げるビルドでは二重に上げない）
  場面     … 戦闘・休養（既定）では触らない。設定で施設の訓練・休養を切り替えられる
  二重取り … ゲーム自身が仲間に配ったら、その相手にはこちらから渡さない
  名簿     … `app.party` が空でも `game_variables['party']` から読む
  見えない … `gain_exp` を通らない支給は WARN として必ず記録に残す

**訓練の検出は `execute` を包んで立てる印で行う**ので、テストも
`VacationTrainManager.execute` の**中から**経験値を入れる形にしてある
（`player.gain_exp` を直接叩くと本番と違う経路になり、検出そのものを検証できない）。

このハーネスが最初に捕まえたのは mod 側の実バグ2件:

  * `frames.MethodWatch` で `execute` を見張ると、**自分で包んだラッパのコード
    オブジェクト**（ローダの全パッチが共有する）が表に入り、戦闘の経験値まで
    「訓練の中」と判定された → 印を立てる形に変えた
  * 見張っていない `VacationRestManager` でも WARN が出ていた → 写す対象の
    セッションだけで警告するようにした

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


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
    return os.path.join(folder, entry)


MOD = find_mod("_party_train_exp")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# ここで定義したクラスは __main__ の属性になる。mod は
# `getattr(sys.modules['__main__'], 名前)` で引くので、これで本番と同じ形になる。
#
# レベルの必要経験値は「レベル × 100」。実物の式は読めないが、この mod は式を
# 使わない（`gain_exp` / `check_levelup` / `levelup` を呼ぶだけ）ので、
# 単調に増える表なら何でも検証になる。
EXP_PER_LEVEL = 100


class Character:
    """`experience_level` / `experience_point` と経験値の3メソッドを持つ。

    `self_leveling` を立てると **`gain_exp` の中でレベルまで上げる**ビルドを真似る
    （mod がその上からさらに上げてしまわないことの検証用）。
    """

    def __init__(self, **kw):
        self.experience_level = 1
        self.experience_point = 0
        self.is_player = False
        self.self_leveling = False
        self.gains = []
        self.levelups = 0
        self.__dict__.update(kw)

    def calculate_exp(self):
        return EXP_PER_LEVEL * self.experience_level

    def gain_exp(self, exp):
        self.gains.append(exp)
        self.experience_point += exp
        if self.self_leveling:
            while self.check_levelup():
                self.levelup()

    def check_levelup(self):
        return self.experience_point >= self.calculate_exp()

    def levelup(self):
        self.experience_point -= self.calculate_exp()
        self.experience_level += 1
        self.levelups += 1


class World:
    def __init__(self, characters):
        self.characters = characters


class InstantaleApp:
    def __init__(self, world, party, player):
        self.world = world
        self.party = party
        self.game_variables = {"party": list(party)}
        self.player = player
        self.texts = []
        self.saves = 0

    def add_text(self, context):
        self.texts.append(context)

    def save_game(self):
        self.saves += 1


# 実機で観測した訓練1回の並び（GAME.md §2.17）。
# **`gain_exp` はこの2行の間に来る** ― mod の文言がプレイヤーの獲得経験値より
# 先に出ないことを、この順序で検証する。
NARRATION_TEXT = "テストプレイヤーはしばらくの期間を訓練に費やした..."
EXP_TEXT = "156の経験値を得た。"


class VacationTrainManager:
    """宿屋で月日を訓練に充てる。**本命の経路。**

    `give_to` に id を並べると、プレイヤーより先に**ゲーム自身が**その仲間へ
    配ったことになる（二重取りを避けられるかの検証）。`direct` を立てると
    `gain_exp` を通さず `experience_point` を直に書く（見えない支給の検証）。
    `silent` を立てると支給の後に1行も出さない（＝流す機会が来ないビルド）。
    """

    def __init__(self, app, months=3, quality="standard"):
        self.app = app
        self.months = months
        self.quality = quality
        self.exp = 250
        self.give_to = ()
        self.direct = False
        self.silent = False

    def method(self):
        app = self.app
        if not self.silent:
            app.add_text(NARRATION_TEXT)
        for member_id in self.give_to:
            app.world.characters[member_id].gain_exp(self.exp)
        if self.direct:
            app.player.experience_point += self.exp
            return
        app.player.gain_exp(self.exp)
        if not self.silent:
            app.add_text(EXP_TEXT)

    def execute(self, choice_text):
        return self.method()


class VacationRestManager:
    """宿屋での休養。**既定では対象外。**（継承しないこと ― コードを別にする）"""

    def __init__(self, app, months=3, quality="standard"):
        self.app = app
        self.exp = 60

    def method(self):
        self.app.player.gain_exp(self.exp)

    def execute(self, choice_text):
        return self.method()


class TrainingPhaseManager:
    """施設の主に教わる訓練の各段。既定では対象。"""

    def __init__(self, app, training_type="skill", remaining_years=2, training_log=None):
        self.app = app
        self.exp = 120

    def simple_training(self):
        self.app.player.gain_exp(self.exp)

    def execute(self, choice_text):
        return self.simple_training()


class TrainingStartManager:
    def __init__(self, app, training_years=2, training_price=500):
        self.app = app

    def execute(self, choice_text):
        return None


class BattleEndManager:
    """戦闘の後始末。**見張っていない**マネージャ（触ってはいけない側）。"""

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.player.gain_exp(30)


# **派生元は表に控える**（TECH.md §4.3）。`main.Character = 派生クラス` は
# **このモジュールのグローバル名を書き換える** ― `__main__` はテスト自身なので、
# 次の setup で `globals()["Character"]` から派生すると前のテストのフックが
# 積み上がり、古い設定のまま動く mod が混ざる（実際にこれで6件赤くなった）。
BASES = {
    "app": InstantaleApp,
    "Character": Character,
    "World": World,
    "VacationTrainManager": VacationTrainManager,
    "VacationRestManager": VacationRestManager,
    "TrainingPhaseManager": TrainingPhaseManager,
    "TrainingStartManager": TrainingStartManager,
    "BattleEndManager": BattleEndManager,
}
MANAGER_NAMES = ("VacationTrainManager", "VacationRestManager",
                 "TrainingPhaseManager", "TrainingStartManager",
                 "BattleEndManager")


class FakeClock:
    def __init__(self):
        self.onces = []

    def schedule_interval(self, callback, timeout):
        pass

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def run_onces(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    return clock


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = []          # [(target, func)] 当てた順
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.append((target, func))
            return func
        return decorator


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks):
    """`module:Class.method` を、本番と同じ形（メソッドの差し替え）で載せる。

    対象が無いときに**黙って飛ばす**のも本番と同じ（`required=False`）。
    """
    installed = []
    for target, hook in hooks:
        module_name, _, qualname = target.partition(":")
        module = sys.modules.get(module_name)
        if module is None or "." not in qualname:
            continue
        cls_name, method_name = qualname.split(".", 1)
        cls = getattr(module, cls_name, None)
        if not isinstance(cls, type):
            continue
        original = getattr(cls, method_name, None)
        if original is None:
            continue

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(cls, method_name, make())
        installed.append(target)
    return installed


OUT_DIR = os.path.join(os.environ.get("TEMP", HERE), "instantale_test_train_exp")
LOG_PATH = os.path.join(OUT_DIR, "party_train_exp.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(members=("71",), party=None, in_world=True, configure=None,
          self_leveling=False):
    """mod を適用し、仲間 `members` を連れた状態の app を返す。

    設定は `configure` で **`apply()` の前に**入れること。見張る相手は `apply()`
    の時点で決まる（本番でも設定の変更は再注入で効く）。
    """
    clock = install_fake_kivy()
    main = sys.modules["__main__"]
    try:
        os.remove(LOG_PATH)
    except OSError:
        pass

    # クラスは毎回作り直す（前のテストで差し替えたメソッドを持ち越さないため）。
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    main.InstantaleApp = app_cls
    character_cls = type("Character", (BASES["Character"],), {})
    main.Character = character_cls
    main.World = BASES["World"]
    for name in MANAGER_NAMES:
        setattr(main, name, type(name, (BASES[name],), {}))

    # `gain_exp` を包む対象は `scripts.characters:Character`。ゲームでは
    # `__main__.Character` も同じクラスオブジェクトなので、偽の側も1つで通す。
    characters_module = types.ModuleType("scripts.characters")
    characters_module.Character = character_cls
    sys.modules["scripts.characters"] = characters_module

    player = character_cls(id="player", name="テストプレイヤー", is_player=True,
                           self_leveling=self_leveling)
    table = {"player": player}
    for member_id in members:
        if in_world:
            table[member_id] = character_cls(id=member_id,
                                             name="テスト仲間" + member_id,
                                             self_leveling=self_leveling)
    roster = list(party) if party is not None else ["player"] + list(members)
    app = app_cls(BASES["World"](table), roster, player)
    # `ui.find_app()` は `__main__` の中から InstantaleApp のインスタンスを探す。
    main.current_app = app

    ctx = FakeCtx(OUT_DIR)
    module = load_mod(MOD, "party_train_exp_mod")
    if configure is not None:
        configure(module)
    module.apply(ctx)
    install(ctx.hooks)
    return app, ctx, clock, module


def train(app, **kw):
    """本番と同じ経路（`execute` の中）で訓練させる。"""
    manager = sys.modules["__main__"].VacationTrainManager(app)
    for key, value in kw.items():
        setattr(manager, key, value)
    manager.execute("訓練して過ごす")
    return manager


def member_of(app, member_id="71"):
    return app.world.characters[member_id]


def mod_lines(app):
    """mod が出した行だけ（ゲーム自身の行は除く）。"""
    return [text for text in app.texts
            if "鍛錬に充て" in text or "レベル" in text]


def index_of(app, needle):
    for i, text in enumerate(app.texts):
        if needle in text:
            return i
    return -1


# ================================================================== 検証
print("=== 宿屋の訓練（既定）===")
app, ctx, clock, mod = setup()
train(app)
member = member_of(app)
check("同行者にも同じ点数が入る", member.gains == [250], member.gains)
check("プレイヤーの支給は1回だけ（二重に入れない）",
      app.player.gains == [250], app.player.gains)
check("同行者のレベルが上がる", member.experience_level == 2, member.experience_level)
check("残った経験値も持ち越す", member.experience_point == 150, member.experience_point)
check("レベルアップを画面に出す",
      any("レベル2になった" in text for text in app.texts), app.texts)
check("経験値が入ったことも既定で出す",
      any("鍛錬に充て" in text for text in app.texts), app.texts)
check("**プレイヤーの獲得経験値より後**に出す",
      index_of(app, EXP_TEXT) < index_of(app, "鍛錬に充て"), app.texts)
check("  → レベルアップの行もその後",
      index_of(app, EXP_TEXT) < index_of(app, "レベル2になった"), app.texts)
check("ゲーム自身の行は消さない・並べ替えない",
      app.texts[:2] == [NARRATION_TEXT, EXP_TEXT], app.texts)
check("例外を1つも出していない", not ctx.errors, ctx.errors)

log_text = read_log()
check("写した支給をログに残す", "+250 exp" in log_text, log_text[-400:])
check("その行に呼び出し元が入る", "VacationTrainManager." in log_text)
check("訓練1回ぶんの記録が必ず出る",
      "VacationTrainManager.execute done" in log_text, log_text[-400:])
check("  → 写した本数も入る", "shared 1 gain(s) with 1 companion(s)" in log_text,
      log_text[-400:])

print("=== レベルが何段も上がるとき ===")
app, ctx, clock, mod = setup()
train(app, exp=1000)
member = member_of(app)
check("上がるぶんだけ上がる", member.experience_level == 5, member.experience_level)
check("端数は経験値として残る", member.experience_point == 0, member.experience_point)
check("画面に出るのは最後のレベル",
      any("レベル5になった" in text for text in app.texts), app.texts)

print("=== gain_exp が内部でレベルを上げるビルド ===")
app, ctx, clock, mod = setup(self_leveling=True)
train(app)
member = member_of(app)
check("二重に上げない", member.levelups == 1, member.levelups)
check("レベルは1段だけ上がる", member.experience_level == 2, member.experience_level)
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 触ってはいけない場面 ===")
app, ctx, clock, mod = setup()
sys.modules["__main__"].BattleEndManager(app).execute("戦う")
check("戦闘の経験値は写さない", member_of(app).gains == [], member_of(app).gains)
check("プレイヤーには普通に入る", app.player.gains == [30], app.player.gains)
check("例外も出ない", not ctx.errors, ctx.errors)
check("訓練の外の支給は記録に残す（触らないが経路は分かる）",
      "(not training)" in read_log(), read_log()[-300:])

app, ctx, clock, mod = setup()
sys.modules["__main__"].VacationRestManager(app).execute("休む")
check("休養は既定では写さない", member_of(app).gains == [], member_of(app).gains)

print("=== 設定 ===")
app, ctx, clock, mod = setup(configure=lambda m: setattr(m, "SHARE_REST", True))
sys.modules["__main__"].VacationRestManager(app).execute("休む")
check("休養も入れる設定にすれば写す", member_of(app).gains == [60],
      member_of(app).gains)

app, ctx, clock, mod = setup()
sys.modules["__main__"].TrainingPhaseManager(app).execute("教わる")
check("施設の訓練は既定で写す", member_of(app).gains == [120], member_of(app).gains)

app, ctx, clock, mod = setup(
    configure=lambda m: setattr(m, "SHARE_FACILITY_TRAINING", False))
sys.modules["__main__"].TrainingPhaseManager(app).execute("教わる")
check("  → 切れば施設の訓練では写さない", member_of(app).gains == [],
      member_of(app).gains)
train(app)
check("  → 宿屋の訓練は写したまま", member_of(app).gains == [250],
      member_of(app).gains)

app, ctx, clock, mod = setup(configure=lambda m: setattr(m, "SHARE_RATIO", 0.5))
train(app)
check("割合を効かせられる", member_of(app).gains == [125], member_of(app).gains)

app, ctx, clock, mod = setup(configure=lambda m: setattr(m, "SHARE_RATIO", 0.001))
train(app, exp=1)
check("端数で消えるくらいなら1点入れる", member_of(app).gains == [1],
      member_of(app).gains)

app, ctx, clock, mod = setup(configure=lambda m: setattr(m, "SHARE_RATIO", 0.0))
train(app)
check("0 なら何も入れない", member_of(app).gains == [], member_of(app).gains)
check("  → その旨をログに残す", "not sharing" in read_log(), read_log()[-300:])

app, ctx, clock, mod = setup(
    configure=lambda m: setattr(m, "ANNOUNCE_LEVELUP", False))
train(app)
check("レベルアップの表示は切れる",
      not any("レベル" in text for text in app.texts), app.texts)
check("  → 経験値の行は残る", any("鍛錬に充て" in text for text in app.texts),
      app.texts)
check("  → 経験値そのものは入る", member_of(app).gains == [250], member_of(app).gains)

app, ctx, clock, mod = setup(configure=lambda m: setattr(m, "ANNOUNCE_GAIN", False))
train(app)
check("経験値の行は切れる",
      not any("鍛錬に充て" in text for text in app.texts), app.texts)
check("  → レベルアップの行は残る",
      any("レベル2になった" in text for text in app.texts), app.texts)

print("=== 出す機会が来なかったとき ===")
app, ctx, clock, mod = setup()
train(app, silent=True)
check("訓練が1行も出さずに終わっても、溜めたまま消えない",
      len(mod_lines(app)) == 2, app.texts)
check("  → 訓練の終わりに流したと記録に残る",
      "at the end of the training" in read_log(), read_log()[-400:])
check("例外も出ない", not ctx.errors, ctx.errors)

app, ctx, clock, mod = setup()
train(app)
train(app)
check("次の訓練に持ち越さない（2回ぶんが混ざらない）",
      len(mod_lines(app)) == 4, app.texts)

print("=== 二重取りを避ける ===")
app, ctx, clock, mod = setup()
train(app, give_to=("71",))
check("ゲーム自身が配った相手には渡さない", member_of(app).gains == [250],
      member_of(app).gains)
check("その旨をログに残す", "gave 250 exp to" in read_log(), read_log()[-400:])
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 名簿の在り処 ===")
app, ctx, clock, mod = setup(members=("71",), party=[])
app.game_variables["party"] = ["player", "71"]
train(app)
check("app.party が空でも game_variables から読む", member_of(app).gains == [250],
      member_of(app).gains)

app, ctx, clock, mod = setup(members=(), party=["player"])
train(app)
check("仲間が居なければ何も出さない", mod_lines(app) == [], app.texts)
check("  → 例外も出ない", not ctx.errors, ctx.errors)
check("  → 記録には残す", "nobody is travelling" in read_log(), read_log()[-300:])

app, ctx, clock, mod = setup(members=("71",), in_world=False)
train(app)
check("world に居ない id は飛ばす（落ちない）", not ctx.errors, ctx.errors)
check("  → WARN として残す", "cannot find character" in read_log(),
      read_log()[-300:])

print("=== gain_exp を通らない支給 ===")
app, ctx, clock, mod = setup()
train(app, direct=True)
check("写せないので何も入れない", member_of(app).gains == [], member_of(app).gains)
check("プレイヤー側の処理は止めない", app.player.experience_point == 250,
      app.player.experience_point)
check("黙って終わらせず WARN を残す",
      "no Character.gain_exp call was seen" in read_log(), read_log()[-500:])
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 当てた対象 ===")
app, ctx, clock, mod = setup()
targets = [target for target, _ in ctx.hooks]
check("gain_exp を包む", "scripts.characters:Character.gain_exp" in targets, targets)
check("宿屋・施設・休養の execute を包む",
      all("__main__:{}.execute".format(name) in targets
          for name in ("VacationTrainManager", "TrainingStartManager",
                       "TrainingPhaseManager", "VacationRestManager")), targets)
check("戦闘のマネージャは包まない",
      not any("BattleEndManager" in target for target in targets), targets)
check("設定を切っても記録用の execute は包む（見えない支給を見つけるため）",
      "__main__:VacationRestManager.execute" in targets, targets)

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
