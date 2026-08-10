# -*- coding: utf-8 -*-
"""308_battle_damage_display.py をゲーム抜きで通す。

    python tools/test_battle_damage_display.py

偽の app / Character / BattlePhaseManager / BattleStartManager を差し込み、次を確認する。

  与えた   … 味方が敵に与えたダメージが、地の文の**後ろ**に数字で出る
  受けた   … 敵が自分や味方に与えたダメージも同じように出る
  台帳     … 同じ変化が2回出ない（報告点が入れ子でも重ならない）
  戦闘の境 … 前の戦闘の HP を持ち越して「回復した」と誤報しない
  型       … 敵が Character でも辞書でも読める。最大 HP が無ければ現在値だけ
  設定     … 敵側・味方側・回復・残量・下限のそれぞれで出し分けられる
  無害     … HP を1点も書き換えない。差が無ければ1行も出さない

**HP の差を測る mod なので、テストも「HP を動かす偽の1手」を通す**
（`handle_battle_situation` の中で HP を書き、地の文も出す）。ダメージの式は
mod もテストも一切持たない。

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime"))
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


MOD = find_mod("_battle_damage_display")
MANIFEST = os.path.join(os.path.dirname(MOD), "mod.json")


def manifest():
    with io.open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh)


def charset_verdict(text):
    """その字が**環境依存文字**かどうか。`ok` か、外れた理由を返す。

    cp932 に入っていても、NEC 特殊（先頭 0x87）・NEC 選定 IBM 拡張（0xED-0xEE）・
    IBM 拡張（0xFA-0xFC）は機種依存。`①` や `㈱` がここに居る。素の JIS X 0208
    （0x81-0x84 の記号・仮名と 0x88-0xEA の漢字）と ASCII だけを通す。
    """
    for ch in text:
        try:
            raw = ch.encode("cp932")
        except Exception:
            return "{!r} は cp932 に無い".format(ch)
        if len(raw) == 1:
            continue
        lead = raw[0]
        if lead == 0x87:
            return "{!r} は NEC 特殊文字".format(ch)
        if lead in (0xED, 0xEE):
            return "{!r} は NEC 選定 IBM 拡張".format(ch)
        if 0xFA <= lead <= 0xFC:
            return "{!r} は IBM 拡張".format(ch)
        if not (0x81 <= lead <= 0x84 or 0x88 <= lead <= 0xEA):
            return "{!r} は JIS X 0208 の外（先頭 {:02X}）".format(ch, lead)
    return "ok"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    """HP と負傷を持つだけの Character。mod が読むのはこの2つと名前だけ。"""

    def __init__(self, **kw):
        self.name = "名無し"
        self.id = None
        self.is_player = False
        self.current_hp = 30
        self.max_hp = 30
        self.physical_integrity = 100
        self.max_physical_integrity = 100
        self.__dict__.update(kw)


class World:
    def __init__(self, characters):
        self.characters = characters


class InstantaleApp:
    def __init__(self, world, party, player):
        self.world = world
        self.party = party
        self.game_variables = {"party": list(party)}
        self.player = player
        self.current_enemy_dict = {}
        self.texts = []

    def add_text(self, context):
        self.texts.append(context)


# 実機の並びに合わせた地の文（`process_battle_text` が出す側）。mod の数字は
# **必ずこれより後**に出なければならない。
NARRATION_TEXT = "テストプレイヤーは剣を振り抜いた。"


class BattlePhaseManager:
    """1手ぶんを回す。`plan` に「誰の HP をいくつにするか」を積んで使う。

    本物では `handle_battle_situation` の中で `calculate_battle_effect` →
    `resolve_battle_effect`（HP を動かす）→ `process_battle_text`（地の文）が
    回る。ここではその順序だけを真似る（式は持たない）。

    `finish` に敵の鍵を並べると、**その手の中で `current_enemy_dict` から抜ける**
    ＝ とどめの一撃。これを拾わないと「倒した敵のダメージだけ出ない」になるので
    （GAME.md §2.10）、テストからも同じ形で踏めるようにしてある。
    """

    def __init__(self, app, command=None):
        self.app = app
        self.plan = []            # [(持ち主, 新しい HP)]
        self.integrity_plan = []  # [(持ち主, 新しい負傷値)]
        self.finish = []          # その手で場から消える敵の鍵
        self.narrate = True
        self.calls = 0

    def handle_battle_situation(self, character_key=None, character_side=None,
                                battle_action=None):
        self.calls += 1
        for holder, value in self.plan:
            if isinstance(holder, dict):
                holder["current_hp"] = value
            else:
                holder.current_hp = value
        for holder, value in self.integrity_plan:
            holder.physical_integrity = value
        for key in self.finish:
            self.app.current_enemy_dict.pop(key, None)
        if self.narrate:
            self.app.add_text(NARRATION_TEXT)
        return "done"

    def reduce_status_turns_and_log(self, character=None):
        if character is not None:
            character.current_hp -= 3
        return None

    def check_battle_end(self):
        return False


class BattleStartManager:
    def __init__(self, app, enemy_type=None, enemy_content=None):
        self.app = app

    def start_battle(self):
        return None


BASES = {
    "app": InstantaleApp,
    "Character": Character,
    "World": World,
    "BattlePhaseManager": BattlePhaseManager,
    "BattleStartManager": BattleStartManager,
}
MANAGER_NAMES = ("BattlePhaseManager", "BattleStartManager")


class FakeClock:
    def schedule_interval(self, callback, timeout):
        pass

    def schedule_once(self, callback, timeout=0):
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
        self.hooks = []
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
    """`module:Class.method` を、本番と同じ形（メソッドの差し替え）で載せる。"""
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


OUT_DIR = os.path.join(os.environ.get("TEMP", HERE), "instantale_test_battle_damage")
LOG_PATH = os.path.join(OUT_DIR, "battle_damage.log")


def read_log():
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def setup(members=("71",), enemies=None, configure=None, enemy_as_dict=False,
          drop_max_hp=False):
    """mod を適用し、戦闘の最中の app を返す。

    設定は `configure` で **`apply()` の前に**入れる（本番でも設定の反映は
    再注入で効く）。
    """
    install_fake_kivy()
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

    def make_character(**kw):
        character = character_cls(**kw)
        if drop_max_hp:
            del character.max_hp
        return character

    player = make_character(id="player", name="テストプレイヤー", is_player=True,
                            current_hp=60, max_hp=60)
    table = {"player": player}
    for member_id in members:
        table[member_id] = make_character(id=member_id,
                                          name="テスト仲間" + member_id,
                                          current_hp=40, max_hp=40)
    app = app_cls(BASES["World"](table), ["player"] + list(members), player)

    for key, name, hp in (enemies or [("0", "ゴブリンの斥候", 34)]):
        if enemy_as_dict:
            app.current_enemy_dict[key] = {"name": name, "current_hp": hp,
                                           "max_hp": hp}
        else:
            app.current_enemy_dict[key] = make_character(id=key, name=name,
                                                         current_hp=hp, max_hp=hp)
    main.current_app = app

    ctx = FakeCtx(OUT_DIR)
    module = load_mod(MOD, "battle_damage_display_mod")
    if configure is not None:
        configure(module)
    module.apply(ctx)
    install(ctx.hooks)
    return app, ctx, module


def phase(app):
    return sys.modules["__main__"].BattlePhaseManager(app, "attack")


def enemy(app, key="0"):
    return app.current_enemy_dict[key]


def hp_of(holder):
    return holder["current_hp"] if isinstance(holder, dict) else holder.current_hp


def set_hp(holder, value):
    if isinstance(holder, dict):
        holder["current_hp"] = value
    else:
        holder.current_hp = value


def mod_lines(app):
    """mod が出した行だけ（ゲーム自身の地の文は除く）。"""
    return [text for text in app.texts if text != NARRATION_TEXT]


def index_of(app, needle):
    for i, text in enumerate(app.texts):
        if needle in text:
            return i
    return -1


def strike(app, *changes, **kw):
    """1手ぶん通す。`changes` は `(持ち主, 新しい HP)`。"""
    manager = phase(app)
    manager.plan = list(changes)
    for key, value in kw.items():
        setattr(manager, key, value)
    manager.handle_battle_situation("player", "ally", {"skill": "通常攻撃"})
    return manager


# ================================================================== 検証
print("=== 味方が敵に与えたダメージ ===")
app, ctx, mod = setup()
strike(app, (enemy(app), 11))
check("ダメージの行が出る", any("23 のダメージ" in t for t in mod_lines(app)),
      app.texts)
check("相手の名前が入る", any("ゴブリンの斥候" in t for t in mod_lines(app)), app.texts)
check("残りの HP を添える", any("残り HP 11/34" in t for t in mod_lines(app)), app.texts)
check("**地の文の後ろ**に出る",
      index_of(app, NARRATION_TEXT) < index_of(app, "のダメージ"), app.texts)
check("ゲーム自身の行は消さない", NARRATION_TEXT in app.texts, app.texts)
check("HP を書き換えない", hp_of(enemy(app)) == 11, hp_of(enemy(app)))
check("例外を1つも出していない", not ctx.errors, ctx.errors)
check("記録に残す", "hp 34 -> 11" in read_log(), read_log()[-400:])

print("=== 敵が自分や味方に与えたダメージ ===")
app, ctx, mod = setup()
strike(app, (app.player, 52), (app.world.characters["71"], 25))
check("プレイヤーの被弾が出る",
      any("テストプレイヤー は 8 のダメージを受けた" in t for t in mod_lines(app)),
      app.texts)
check("同行者の被弾も出る",
      any("テスト仲間71 は 15 のダメージを受けた" in t for t in mod_lines(app)),
      app.texts)
check("1手ぶんは1回のテキストにまとめる", len(mod_lines(app)) == 1, mod_lines(app))
check("例外も出ない", not ctx.errors, ctx.errors)

print("=== 同じ変化を2回出さない（台帳）===")
app, ctx, mod = setup()
manager = strike(app, (enemy(app), 11))
before = len(mod_lines(app))
manager.check_battle_end()
manager.check_battle_end()
check("報告点をいくつ通しても増えない", len(mod_lines(app)) == before,
      mod_lines(app))
set_hp(enemy(app), 4)
manager.check_battle_end()
check("その後の変化は取りこぼさない",
      any("7 のダメージ" in t for t in mod_lines(app)), app.texts)

print("=== 差が無ければ何も出さない ===")
app, ctx, mod = setup()
strike(app)
check("HP が動かなければ1行も出さない", mod_lines(app) == [], app.texts)
check("地の文はそのまま出る", app.texts == [NARRATION_TEXT], app.texts)

print("=== 回復 ===")
app, ctx, mod = setup()
set_hp(app.player, 20)
strike(app, (app.player, 45))
check("回復も出る", any("25 回復した" in t for t in mod_lines(app)), app.texts)

app, ctx, mod = setup(configure=lambda m: setattr(m, "SHOW_HEALING", False))
set_hp(app.player, 20)
strike(app, (app.player, 45))
check("設定を切れば出ない", mod_lines(app) == [], app.texts)

print("=== 出し分けの設定 ===")
app, ctx, mod = setup(configure=lambda m: setattr(m, "SHOW_DAMAGE_TO_ENEMIES", False))
strike(app, (enemy(app), 11), (app.player, 52))
check("敵側を切っても味方の被弾は出る",
      mod_lines(app) and "8 のダメージを受けた" in mod_lines(app)[0], app.texts)
check("  → 敵に与えたぶんは出ない",
      not any("23 のダメージ" in t for t in mod_lines(app)), app.texts)

app, ctx, mod = setup(configure=lambda m: setattr(m, "SHOW_DAMAGE_TO_ALLIES", False))
strike(app, (enemy(app), 11), (app.player, 52))
check("味方側を切っても敵に与えたぶんは出る",
      mod_lines(app) and "23 のダメージ" in mod_lines(app)[0], app.texts)
check("  → 被弾は出ない",
      not any("のダメージを受けた" in t for t in mod_lines(app)), app.texts)

app, ctx, mod = setup(configure=lambda m: setattr(m, "SHOW_REMAINING_HP", False))
strike(app, (enemy(app), 11))
check("残量を切れる", not any("残り HP" in t for t in mod_lines(app)), app.texts)
check("  → ダメージ自体は出る", any("23 のダメージ" in t for t in mod_lines(app)),
      app.texts)

app, ctx, mod = setup(configure=lambda m: setattr(m, "MIN_AMOUNT", 5))
strike(app, (enemy(app), 31))
check("下限未満は出さない", mod_lines(app) == [], app.texts)
strike(app, (enemy(app), 20))
check("  → 下限以上は出す", any("11 のダメージ" in t for t in mod_lines(app)),
      app.texts)

print("=== 身体の負傷（既定オフ）===")
app, ctx, mod = setup()
manager = phase(app)
manager.integrity_plan = [(app.player, 80)]
manager.handle_battle_situation("player", "ally", None)
check("既定では出さない", mod_lines(app) == [], app.texts)

app, ctx, mod = setup(configure=lambda m: setattr(m, "SHOW_INTEGRITY", True))
manager = phase(app)
manager.integrity_plan = [(app.player, 80)]
manager.handle_battle_situation("player", "ally", None)
check("設定を入れれば出る",
      any("負傷が 20 深くなった" in t for t in mod_lines(app)), app.texts)

print("=== 状態異常の継続ダメージ ===")
app, ctx, mod = setup()
manager = phase(app)
manager.reduce_status_turns_and_log(app.player)
check("毒などの目減りも出る",
      any("3 のダメージを受けた" in t for t in mod_lines(app)), app.texts)
check("記録に残る", "status effects" in read_log(), read_log()[-300:])

print("=== 戦闘の境目 ===")
app, ctx, mod = setup()
strike(app, (enemy(app), 11))
lines_before = len(mod_lines(app))
# 同じ鍵で別の敵が入る＝次の戦闘。満タンの HP を「回復」と誤報してはいけない。
app.current_enemy_dict["0"] = sys.modules["__main__"].Character(
    id="0", name="オークの兵", current_hp=50, max_hp=50)
sys.modules["__main__"].BattleStartManager(app).start_battle()
strike(app)
check("前の戦闘の HP を持ち越さない", len(mod_lines(app)) == lines_before,
      mod_lines(app))
check("台帳を捨てたことを記録する", "ledger cleared" in read_log(), read_log()[-400:])
strike(app, (enemy(app), 38))
check("新しい戦闘のダメージは出る",
      any("12 のダメージ" in t for t in mod_lines(app)), app.texts)

app, ctx, mod = setup()
strike(app, (enemy(app), 11))
lines_before = len(mod_lines(app))
# 入れ物ごと差し替わる経路（`start_battle` を通らないビルドの保険）。
app.current_enemy_dict = {"0": sys.modules["__main__"].Character(
    id="0", name="オークの兵", current_hp=50, max_hp=50)}
strike(app)
check("current_enemy_dict の差し替えでも捨てる", len(mod_lines(app)) == lines_before,
      mod_lines(app))

print("=== とどめの一撃（消えた敵から測る経路）===")
# 倒した敵は報告より先に `current_enemy_dict` から抜ける。それでも
# **最後のダメージが出る**こと ― 台帳に控えた持ち主から測るため。
app, ctx, mod = setup()
strike(app, (enemy(app), 11))
lines_before = len(mod_lines(app))
victim = enemy(app)
manager = phase(app)
manager.plan = [(victim, 0)]
manager.finish = ["0"]              # 手の中で `current_enemy_dict` から抜ける
manager.handle_battle_situation("player", "ally", {"skill": "渾身の一撃"})
check("場から消えた後でも最後のダメージが出る",
      any("11 のダメージ" in t for t in mod_lines(app)[lines_before:]), app.texts)
check("撃破と分かる形にする",
      any("撃破" in t for t in mod_lines(app)[lines_before:]), app.texts)
check("  → 残り HP 0 は出さない",
      not any("残り HP 0" in t for t in mod_lines(app)), app.texts)
check("記録に残す", "left the field" in read_log(), read_log()[-400:])
check("例外も出ない", not ctx.errors, ctx.errors)

# 出したら台帳から落とす（次の手で二重に出さない）。
after_kill = len(mod_lines(app))
strike(app)
check("倒した敵を次の手で二度と出さない", len(mod_lines(app)) == after_kill,
      mod_lines(app))

app, ctx, mod = setup()
manager = phase(app)
manager.finish = ["0"]              # HP は動かないまま場から消える（逃走など）
manager.handle_battle_situation("player", "ally", None)
check("HP が動かずに消えた敵は出さない", mod_lines(app) == [], app.texts)
check("  → 例外も出ない", not ctx.errors, ctx.errors)

print("=== 持ち主の型を決めつけない ===")
app, ctx, mod = setup(enemy_as_dict=True)
strike(app, (enemy(app), 11))
check("敵が辞書でも読める", any("23 のダメージ" in t for t in mod_lines(app)),
      app.texts)
check("  → 名前も残量も出る", any("ゴブリンの斥候" in t and "11/34" in t
                                   for t in mod_lines(app)), app.texts)

app, ctx, mod = setup(drop_max_hp=True)
strike(app, (enemy(app), 11))
check("最大 HP が無ければ現在値だけ添える",
      any("残り HP 11)" in t.replace("）", ")") for t in mod_lines(app)), app.texts)
check("  → 分母は出さない", not any("/" in t for t in mod_lines(app)), app.texts)
check("  → 記録にその旨を残す", "no max HP attribute" in read_log(), read_log()[-400:])

print("=== 行数の上限 ===")
app, ctx, mod = setup(enemies=[(str(i), "敵" + str(i), 30) for i in range(20)])
strike(app, *[(enemy(app, str(i)), 10) for i in range(20)])
check("1回の報告は MAX_LINES 行まで",
      len(mod_lines(app)[0].split("\n")) == mod.MAX_LINES, mod_lines(app))
check("  → 省いた本数を記録に残す", "not shown" in read_log(), read_log()[-300:])

print("=== 行の頭に付ける記号（LINE_PREFIX）===")
app, ctx, mod = setup()
strike(app, (enemy(app), 11))
check("既定では何も付けない", mod_lines(app)[0].startswith("ゴブリンの斥候"),
      mod_lines(app))

app, ctx, mod = setup(configure=lambda m: setattr(m, "LINE_PREFIX", "●"))
strike(app, (enemy(app), 11))
check("選ぶと記号が付く", mod_lines(app)[0].startswith("● ゴブリンの斥候"),
      mod_lines(app))
check("  → 記号と本文の間は半角空白（選択肢の側に空白を持たせない）",
      "●" + mod.PREFIX_SEPARATOR == "● ", repr(mod.PREFIX_SEPARATOR))

app, ctx, mod = setup(configure=lambda m: setattr(m, "LINE_PREFIX", "※"))
manager = phase(app)
manager.plan = [(enemy(app), 0)]
manager.finish = ["0"]
manager.handle_battle_situation("player", "ally", None)
check("とどめの行にも付く", mod_lines(app)[0].startswith("※ "), mod_lines(app))

app, ctx, mod = setup(configure=lambda m: setattr(m, "SHOW_INTEGRITY", True))
mod.LINE_PREFIX = "◆"
manager = phase(app)
manager.integrity_plan = [(app.player, 80)]
manager.handle_battle_situation("player", "ally", None)
check("負傷の行にも付く", mod_lines(app)[0].startswith("◆ "), mod_lines(app))

app, ctx, mod = setup(configure=lambda m: setattr(m, "LINE_PREFIX", m.NO_PREFIX))
strike(app, (enemy(app), 11))
check("NO_PREFIX を選べば付かない", mod_lines(app)[0].startswith("ゴブリン"),
      mod_lines(app))

# 宣言の側（GUI に出る一覧）。既定・選択肢・文字集合をここで縛る。
decl = (manifest().get("settings") or {}).get("LINE_PREFIX") or {}
values = decl.get("values") or []
check("GUI から選べる設定として宣言されている", decl.get("type") == "choice", decl)
check("既定は「表示なし」", decl.get("default") == mod.NO_PREFIX, decl.get("default"))
check("  → 既定も選択肢に入っている（選び直せる）", mod.NO_PREFIX in values, values)
check("候補が複数ある", len(values) >= 5, values)
bad = [(v, charset_verdict(v)) for v in values if charset_verdict(v) != "ok"]
check("**候補に環境依存文字が無い**", not bad, bad)
check("  → 候補に空白を持たせない（GUI で見えないため）",
      all(v == v.strip() and v for v in values), values)
check("  → 空文字を選択肢にしない（GUI が「未指定」として弾くため）",
      all(v.strip() != "" for v in values), values)
# 最初の版で使っていた記号がこの判定で落ちることを見せる（判定が効いている証拠）。
# **この行に字そのものを書かない。** cp932 のコンソールでは `print` した時点で
# UnicodeEncodeError になる ― 環境依存文字を候補から外す理由の実演でもある。
check("判定そのものが効いている（U+25B6 は環境依存として落ちる）",
      charset_verdict("▶") != "ok", "U+25B6 が ok になっている")
check("  → U+00BB も落ちる", charset_verdict("»") != "ok",
      "U+00BB が ok になっている")
check("  → U+2460（丸数字）も落ちる", charset_verdict("①") != "ok",
      "U+2460 が ok になっている")
check("  → 素の JIS X 0208 は通る",
      all(charset_verdict(ch) == "ok" for ch in "・→※●-*>"),
      "JIS X 0208 の字が落ちている")

print("=== 当てた対象 ===")
app, ctx, mod = setup()
targets = [target for target, _ in ctx.hooks]
check("1手ぶんを包む",
      "__main__:BattlePhaseManager.handle_battle_situation" in targets, targets)
check("状態異常の目減りも包む",
      "__main__:BattlePhaseManager.reduce_status_turns_and_log" in targets, targets)
check("取りこぼしの掃除も包む",
      "__main__:BattlePhaseManager.check_battle_end" in targets, targets)
check("戦闘開始で台帳を取り直す",
      "__main__:BattleStartManager.start_battle" in targets, targets)
check("HP を書く側（resolve_battle_effect）は包まない ― 地の文より先に出るため",
      not any("resolve_battle_effect" in target for target in targets), targets)
check("ダメージの式（get_instant_damage）は読まない",
      not any("damage" in target for target in targets), targets)

print()
if failures:
    print("失敗 {} 件: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
