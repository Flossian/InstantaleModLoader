# -*- coding: utf-8 -*-
"""123_fix_new_character_level をゲーム抜きで通す。

    python tools/test_new_character_level.py

偽の `scripts.characters.Character`（レベルから HP を決める）と偽の
`scripts.functions`（本体と同じレベル→体力上限）を差し込んで、次を確認する。

  新規      … 新規作成の引数（レベル60・経験値なし・体力上限はレベル1の値）が
              レベル1に直り、HP もレベル1で計算し直される
  NPC       … `is_player` でないキャラは触らない
  読み込み  … 経験値のある普通のセーブは触らない
  手編集    … レベル60・経験値0でも**体力上限がレベル相応**なら触らない
  バグ済み  … レベル60・経験値0・体力上限10 のセーブは既定では直さず警告だけ
  設定      … REPAIR_LOADED を入れるとそのセーブも直る
  冪等      … 本体が直って 1 が渡るようになったら何もしない
  位置引数  … `experience_level` が引数名で来ないときは触らず、1度だけ警告
  上限なし  … 体力上限が引数に無いときは触らない（食い違いを判定できない）
  表なし    … `scripts.functions` が無ければ何も包まない
  二段目    … `__init__` の後でレベルが戻された場合に警告が出る
  無事故    … どの経路でも ctx.log_exc が呼ばれない

根拠は VERIFICATION_LOG.md §2.36。`instantale.py:876` が `experience_level=60` を
渡し、883〜885 行が `get_max_physical_integrity(1) -> 10` を渡している。
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

# 失敗したときは記録の中身をそのまま出す。cp932 のコンソールに出せない文字が
# 混ざっていても試験自体は落とさない（落とすと本当の失敗が読めなくなる）。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

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


MOD = find_mod("_fix_new_character_level")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# レベル→体力上限。実測（VERIFICATION_LOG.md §2.36 / `214_` の総当たり）の一部。
STAMINA_BY_LEVEL = {1: 10, 15: 15, 30: 26, 49: 39, 60: 45, 73: 50}


def get_max_physical_integrity(level):
    for key in sorted(STAMINA_BY_LEVEL):
        if level <= key:
            return STAMINA_BY_LEVEL[key]
    return 50


class Character:
    """本物と同じく、HP は `__init__` の中でレベルから決まる。

    式そのものは特定していない。実測の2点（レベル1で `耐久 × 4.8`、
    レベル60・耐久15 で 780）を通る形にしてある ― この試験に必要なのは
    「HP がレベルに従って決まる」ことだけ。
    """

    def __init__(self, name=None, experience_level=0, experience_point=0,
                 original_ability_scores=None, max_physical_integrity=100,
                 is_player=False, config=None, **kw):
        self.name = name
        self.experience_level = experience_level
        self.experience_point = experience_point
        self.original_ability_scores = original_ability_scores or {}
        self.max_physical_integrity = max_physical_integrity
        self.is_player = is_player
        self.config = config
        self.__dict__.update(kw)
        con = self.original_ability_scores.get("constitution", 10)
        if experience_level <= 1:
            self.max_hp = int(con * 4.8)
        else:
            self.max_hp = int(con * experience_level * 0.8667)
        self.current_hp = self.max_hp


class InstantaleApp:
    """`start_game` は新規開始の入口。二段目の保険の確認に使う。"""

    def __init__(self):
        self.player = None
        self.after_start_level = None

    def start_game(self, world_name):
        # 本番と同じく `scripts.characters.Character` を通す（包まれる側はこちら。
        # この試験ファイルの `Character` を直に呼ぶとフックを通らない）。
        cls = sys.modules["scripts.characters"].Character
        self.player = cls(
            name="新規", experience_level=60,
            original_ability_scores={"constitution": 15},
            max_physical_integrity=10, is_player=True)
        if self.after_start_level is not None:
            # `__init__` より後でレベルを戻す経路の再現（未確認の想定）。
            self.player.experience_level = self.after_start_level
        return "started"


def install_fakes(with_functions=True):
    scripts = types.ModuleType("scripts")
    characters = types.ModuleType("scripts.characters")
    characters.Character = type("Character", (Character,), {})
    scripts.characters = characters
    sys.modules["scripts"] = scripts
    sys.modules["scripts.characters"] = characters
    sys.modules.pop("scripts.functions", None)
    if with_functions:
        functions = types.ModuleType("scripts.functions")
        functions.CHARACTER_LEVEL_MIN = 1
        functions.CHARACTER_LEVEL_MAX = 100
        functions.get_max_physical_integrity = get_max_physical_integrity
        scripts.functions = functions
        sys.modules["scripts.functions"] = functions
    return characters.Character


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.notes = []

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

    def log(self, msg, level="INFO"):
        self.notes.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "new_character_level_mod", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, char_cls, app_cls):
    """フックを本番と同じ形（メソッドの差し替え）でクラスに載せる。"""
    for target, cls, name in (
            ("scripts.characters:Character.__init__", char_cls, "__init__"),
            ("__main__:InstantaleApp.start_game", app_cls, "start_game")):
        hook = hooks.get(target)
        if hook is None:
            continue
        original = getattr(cls, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(cls, name, make())


def setup(with_functions=True, repair_loaded=False):
    """mod を適用し、(mod, ctx, Character, InstantaleApp) を返す。"""
    char_cls = install_fakes(with_functions)
    app_cls = type("InstantaleApp", (InstantaleApp,), {})
    sys.modules["__main__"].InstantaleApp = app_cls
    mod = load_mod()
    mod.REPAIR_LOADED = repair_loaded
    ctx = FakeCtx(os.path.join(HERE, os.pardir, "out", "test"))
    mod.apply(ctx)
    install(ctx.hooks, char_cls, app_cls)
    return mod, ctx, char_cls, app_cls


def log_text(ctx):
    path = os.path.join(ctx.out_dir, "new_character_level.log")
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


# 前回の実行のログを持ち越さない（含有判定に混ざる）。
_log = os.path.join(HERE, os.pardir, "out", "test", "new_character_level.log")
if os.path.exists(_log):
    os.remove(_log)

# 新規作成が実機で通る引数（`214_` のログの写し）。
NEW_PLAYER = dict(name="ヴァルガス・グレイヴ", experience_level=60,
                  original_ability_scores={"constitution": 15},
                  max_physical_integrity=10,
                  original_max_physical_integrity=10, physical_integrity=10,
                  is_player=True)

# ==================================================== 新規作成が直ること
print("\n-- 新規作成 --")
mod, ctx, Char, App = setup()
player = Char(**NEW_PLAYER)
check("レベルが1に戻る", player.experience_level == 1, player.experience_level)
check("HP がレベル1で計算し直される", player.max_hp == 72, player.max_hp)
check("体力上限は渡された値のまま", player.max_physical_integrity == 10,
      player.max_physical_integrity)
check("記録が残る", "experience_level 60 -> 1" in log_text(ctx), log_text(ctx)[-300:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 触らないもの --")
npc = Char(name="ヘルデ", experience_level=8, experience_point=0,
           original_ability_scores={"constitution": 8},
           max_physical_integrity=100)
check("NPC は触らない", npc.experience_level == 8, npc.experience_level)

loaded = Char(name="アーリ", experience_level=49, experience_point=2118280,
              original_ability_scores={"constitution": 26},
              max_physical_integrity=39, is_player=True)
check("普通の読み込みは触らない", loaded.experience_level == 49,
      loaded.experience_level)

edited = Char(name="ヴァン", experience_level=60, experience_point=0,
              original_ability_scores={"constitution": 30},
              max_physical_integrity=100, is_player=True)
check("手編集セーブ（体力上限がレベル相応）は触らない",
      edited.experience_level == 60, edited.experience_level)
check("触らない理由が記録に出る",
      "consistent enough" in log_text(ctx), log_text(ctx)[-300:])

fixed_game = Char(name="本体が直った後", experience_level=1,
                  original_ability_scores={"constitution": 15},
                  max_physical_integrity=10, is_player=True)
check("レベル1で渡ってきたら何もしない（冪等）",
      fixed_game.experience_level == 1 and fixed_game.max_hp == 72,
      (fixed_game.experience_level, fixed_game.max_hp))

# `config` 経由の `is_player` も拾えること（本体は両方の渡し方をする）。
via_config = Char(name="config 経由", experience_level=60,
                  original_ability_scores={"constitution": 15},
                  max_physical_integrity=10,
                  config={"level_of_detail": 2, "is_player": True})
check("is_player は config からも見る", via_config.experience_level == 1,
      via_config.experience_level)

print("\n-- 既に保存されたセーブ --")
mod, ctx, Char, App = setup()
bugged = Char(name="バグ済み", experience_level=60, experience_point=0,
              original_ability_scores={"constitution": 16},
              max_physical_integrity=10, is_player=True)
check("既定では直さない", bugged.experience_level == 60, bugged.experience_level)
check("警告だけ残る", "WARN loaded save carries the bug" in log_text(ctx),
      log_text(ctx)[-300:])

mod, ctx, Char, App = setup(repair_loaded=True)
repaired = Char(name="バグ済み", experience_level=60, experience_point=0,
                original_ability_scores={"constitution": 16},
                max_physical_integrity=10, is_player=True)
check("REPAIR_LOADED を入れると直る", repaired.experience_level == 1,
      repaired.experience_level)
check("直した理由が記録に出る", "REPAIR_LOADED is on" in log_text(ctx),
      log_text(ctx)[-300:])

print("\n-- 判定できない形 --")
mod, ctx, Char, App = setup()
# レベルだけ位置引数で来た形（`is_player` は引数名で来ているので**プレイヤーだと
# 分かるのに**レベルが読めない、という一番厄介な組み合わせ）。
positional = Char("位置引数", 60, is_player=True,
                  original_ability_scores={"constitution": 15},
                  max_physical_integrity=10)
check("引数名で来なければ触らない", positional.experience_level == 60,
      positional.experience_level)
check("1度だけ警告する",
      log_text(ctx).count("without an experience_level keyword") == 1,
      log_text(ctx).count("without an experience_level keyword"))
Char("位置引数2", 60, is_player=True,
     original_ability_scores={"constitution": 15}, max_physical_integrity=10)
check("2度目は黙る",
      log_text(ctx).count("without an experience_level keyword") == 1,
      log_text(ctx).count("without an experience_level keyword"))

no_stamina = Char(name="上限なし", experience_level=60,
                  original_ability_scores={"constitution": 15}, is_player=True)
check("体力上限が引数に無ければ触らない", no_stamina.experience_level == 60,
      no_stamina.experience_level)
check("その理由も記録に出る",
      "cannot tell level 1 from level 60" in log_text(ctx), log_text(ctx)[-400:])
check("ここまで例外なし", ctx.errors == [], ctx.errors)

print("\n-- scripts.functions が無い --")
mod2, ctx2, Char2, App2 = setup(with_functions=False)
check("何も包まない", ctx2.hooks == {}, list(ctx2.hooks))
check("理由を残して降りる",
      any("not loaded" in note for note in ctx2.notes), ctx2.notes)

print("\n-- 二段目（開始処理の後の確認）--")
mod, ctx, Char, App = setup()
app = App()
app.start_game("テストワールド")
check("新規開始でプレイヤーが直る", app.player.experience_level == 1,
      app.player.experience_level)
check("開始処理の後の様子が残る", "after start_game: level=1" in log_text(ctx),
      log_text(ctx)[-300:])

mod, ctx, Char, App = setup()
app = App()
app.after_start_level = 60          # `__init__` の後で戻される経路の再現
app.start_game("テストワールド")
check("戻されたら警告する",
      "after start_game the player is still level 60" in log_text(ctx),
      log_text(ctx)[-300:])
check("最後まで例外なし", ctx.errors == [], ctx.errors)

print("\n" + ("all passed" if not failures
              else "{} failure(s): {}".format(len(failures), failures)))
sys.exit(1 if failures else 0)
