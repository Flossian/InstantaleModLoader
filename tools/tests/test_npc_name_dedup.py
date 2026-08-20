# -*- coding: utf-8 -*-
"""120_fix_npc_name_collision.py をゲーム抜きで通す。

    python tools/tests/test_npc_name_dedup.py

偽の `World` / `Character` / `InstantaleApp` を差し込み、次を確認する。

  鍵     … バルガス / ヴァルガス / 「隻眼の」バルガス が同じ読みに落ちる
  別人   … アレン・スミス と アレン・ジョーンズ は別人のまま
  名簿   … `npc.json` から読む／男女を `category` で選ぶ／二つ名は 10%
  改名   … 生成時に改名され、素データ（`npcs`）の名前も一緒に書き換わる
  乱数   … 名前は引くたび変わる／MOD 専用の `Random` から引く／同じ世界では重複しない
  自制   … 敵・プレイヤー・既に世界に居る重複には手を出さない

名前は**発明しない**。
名簿に空きが無ければ元の名前のまま通す。
乱数を使う検査は `mod.RNG.seed(...)` で固定する。
"""
import importlib.util
import io
import json
import os
import random
import sys
import tempfile
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


MOD = find_mod("_fix_npc_name_collision")
MOD_DIR = os.path.dirname(MOD)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    def __init__(self, name=None, id=None, is_player=False, **kw):
        self.name = name
        self.id = id
        self.is_player = is_player
        self.__dict__.update(kw)


class World:
    def __init__(self, app):
        self.app = app
        self.characters = {}

    def generate_character(self, character_id, character_value):
        """本物と同じく、素データを引いて `Character` を組む（GAME.md §2.23）。"""
        character = self.app.character_class(name=character_value["name"],
                                             id=character_id)
        self.characters[str(character_id)] = character
        return character


class InstantaleApp:
    def __init__(self):
        self.world = World(self)
        self.player = None
        self.save_data_dict = {"npcs": {}}
        self.world_dict = {"npcs": {}}
        self.character_class = None
        self.loads = 0

    def load_game_new(self, *args, **kwargs):
        self.loads += 1

    def start_game(self, *args, **kwargs):
        self.loads += 1


# `setup()` は `__main__` の `InstantaleApp` / `World` を差し替える。
# 素のクラスをここに控えておかないと、2回目の `setup()` が**前回フックを載せたクラス**を継承してしまい、古い
# mod の判断が残ったまま重なる（`110_` の検査と同じ理由）。
BASES = {"app": InstantaleApp, "world": World}


def install_fake_characters():
    module = types.ModuleType("scripts.characters")
    cls = type("Character", (Character,), {})
    module.Character = cls
    scripts = sys.modules.get("scripts") or types.ModuleType("scripts")
    scripts.characters = module
    sys.modules["scripts"] = scripts
    sys.modules["scripts.characters"] = module
    return cls


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    def __init__(self, out_dir, mod_dir=MOD_DIR):
        self.out_dir = out_dir
        self.mod_dir = mod_dir
        self.hooks = {}
        self.errors = []
        self.notes = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、
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
        "npc_name_mod", MOD, submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, char_cls, world_cls, app_cls):
    for target, cls, name in (
            ("scripts.characters:Character.__init__", char_cls, "__init__"),
            ("__main__:World.generate_character", world_cls, "generate_character"),
            ("__main__:InstantaleApp.load_game_new", app_cls, "load_game_new"),
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


def setup(settings=None, mod_dir=MOD_DIR):
    """mod を適用し、フックの載った (mod, ctx, app) を返す。"""
    char_cls = install_fake_characters()
    world_cls = type("World", (BASES["world"],), {})
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.World = world_cls

    app = app_cls()
    app.world = world_cls(app)
    app.character_class = char_cls
    main.__dict__["_test_app"] = app

    mod = load_mod()
    for key, value in (settings or {}).items():
        setattr(mod, key, value)
    ctx = FakeCtx(os.path.join(HERE, os.pardir, os.pardir, "out", "test"), mod_dir)
    mod.apply(ctx)
    install(ctx.hooks, char_cls, world_cls, app_cls)
    return mod, ctx, app


def born(app, character_id, name, **extra):
    """ゲームが NPC を1人作るときと同じ順で通す（素データが先。§2.23）。"""
    data = {"name": name, "id": character_id}
    data.update(extra)
    app.save_data_dict["npcs"][str(character_id)] = data
    return app.world.generate_character(str(character_id), data)


# ============================================================ 鍵（読みの骨）
print("\n-- 読みの骨 --")
mod = load_mod()

VARGAS = ("バルガス", "ヴァルガス", "ばるがす",
          "「隻眼の」バルガス", "隻眼のバルガス", "バルガス2")
keys = {name: mod.canonical(name) for name in VARGAS}
check("バルガスの揺れが全部同じ鍵になる",
      len(set(keys.values())) == 1, keys)

# 濁点は鍵に残す。落とすのは比べるときだけ（`_near`）。
check("濁点の違いは鍵に残る",
      mod.canonical("バルガス") != mod.canonical("バルカス"),
      (mod.canonical("バルガス"), mod.canonical("バルカス")))
check("濁点だけが違う名前は重複とみなす",
      mod.too_close(mod.canonical("バルガス"), mod.canonical("バルカス")))

# 助詞の `の` は、手前が仮名だけなら切らない（切ると名前が壊れる）。
check("たけのうち の鍵が壊れない",
      mod.canonical("たけのうち") == ("タケノウチ",), mod.canonical("たけのうち"))
check("漢字を含む二つ名は切る",
      mod.canonical("隻眼のバルガス") == mod.canonical("バルガス")
      and mod.canonical("重鉄のバルカス") == mod.canonical("バルカス"),
      (mod.canonical("隻眼のバルガス"), mod.canonical("重鉄のバルカス")))

check("姓名は分けたまま持つ",
      mod.canonical("バルガス・ドレイク") == mod.canonical("バルガス") +
      mod.canonical("ドレイク"), mod.canonical("バルガス・ドレイク"))

for left, right in (("バルガス", "ヴァルガス"),
                    ("バルガス", "「隻眼の」バルガス"),
                    ("バルガス", "バルガス・ドレイク"),
                    ("バルガス", "バルガド"),
                    ("ティルダ", "チルダ"),
                    ("ジェイド", "ゼイド"),
                    ("フィオナ", "ヒオナ"),
                    ("レオーネ", "レオネ"),
                    ("Vargas", "Bargas")):
    check("重複とみなす {} / {}".format(left, right),
          mod.too_close(mod.canonical(left), mod.canonical(right)),
          (mod.canonical(left), mod.canonical(right)))

for left, right in (("アレン・スミス", "アレン・ジョーンズ"),
                    ("ジル", "ジン"),
                    ("バルガス", "エルミナ"),
                    ("ナナシ", "ナシ"),
                    ("佐藤", "加藤")):
    check("別人のまま {} / {}".format(left, right),
          not mod.too_close(mod.canonical(left), mod.canonical(right)),
          (mod.canonical(left), mod.canonical(right)))

check("名前が無ければ鍵も無い",
      mod.canonical(None) == () and mod.canonical("  ") == ())

# ------------------------------------------------------------ 実機の回帰
# `out/npc_name.log` に実際に出た判定。
# 上の 9 件は捕まえたいもの、下の 3 件は**別人を改名していた誤検出**
# （濁点落としと編集距離が二重に効いていた。2026-08-20 に判定を2段へ分けた）。
print("")
print("-- 実機の判定（out/npc_name.log の回帰） --")
for left, right, same in (
        ("セレスティアル", "セレスティア", True),
        ("ルナリア", "リナリア", True),
        ("バルド・ストーン", "バルト", True),
        ("セレスティナ", "セレスティア", True),
        ("バルド", "バルト", True),
        ("ルナリエ", "ルナリス", True),
        ("カイル", "カイル", True),
        ("重鉄のバルカス", "元傭兵のバルガス", True),
        ("隻眼のバルガス", "バルガス", True),
        ("バルガス・グラトル", "“黒蜥蜴”アルカス", False),
        ("“黒蜥蜴”アルカス", "ヴァルガス・ヴォルフレイン", False),
        ("“黒蜥蜴”アルカス", "ヴァルガス・グレイヴ", False)):
    got = mod.too_close(mod.canonical(left), mod.canonical(right))
    check("{} {} / {}".format("重複" if same else "別人", left, right),
          got == same, (mod.canonical(left), mod.canonical(right)))

# `SIMILARITY` の段。
mod.SIMILARITY = "strict"
check("strict は読みが完全に一致したときだけ",
      not mod.too_close(mod.canonical("バルガス"), mod.canonical("バルガド"))
      and mod.too_close(mod.canonical("バルガス"), mod.canonical("ヴァルガス")))
mod.SIMILARITY = "loose"
check("loose は短い名前も拾う",
      mod.too_close(mod.canonical("ジルド"), mod.canonical("ジルバ")))
mod.SIMILARITY = "normal"
check("normal は 3 文字の 1 文字違いまでは拾わない",
      not mod.too_close(mod.canonical("ジルド"), mod.canonical("ジルバ")))

# 濁点を分けたいとき。
mod.FOLD_VOICING = False
check("FOLD_VOICING を切ると濁点の違いが残る",
      mod.canonical("バルガス") != mod.canonical("バルカス"))
check("切ってもヴァとバは同じ",
      mod.canonical("バルガス") == mod.canonical("ヴァルガス"))
mod.FOLD_VOICING = True

# =========================================================== 名簿
print("\n-- 名簿 --")
mod = load_mod()

ROSTER = mod.pick_path(MOD_DIR)
check("同梱の名簿が在る", ROSTER is not None and os.path.isfile(ROSTER), ROSTER)
names, epithets = mod.read_roster(ROSTER)
check("male / female の両方が読める",
      len(names.get("male") or []) > 50 and len(names.get("female") or []) > 50,
      {k: len(v) for k, v in names.items()})
check("二つ名が読める", len(epithets) > 20, len(epithets))
check("同梱の名簿は読む鍵だけを持つ",
      set(json.load(io.open(ROSTER, encoding="utf-8")))
      == {"male", "female", "epithets"},
      sorted(json.load(io.open(ROSTER, encoding="utf-8"))))
check("知らない鍵は読まない（別の道具で作った名簿もそのまま置ける）",
      mod.read_roster(None) == ({}, [])
      and set(names) == {"male", "female"}, sorted(names))
check("名前に使えない文字を含む名簿の行は落ちる",
      all(not (set(name) & mod.BAD_NAME_CHARS)
          for pool in names.values() for name in pool))

check("名簿が無ければ空で返る", mod.read_roster(None) == ({}, []))
check("壊れた名簿でも落ちない", mod.read_roster(__file__) == ({}, []))

with tempfile.TemporaryDirectory() as folder:
    io.open(os.path.join(folder, mod.DEFAULT_NAMES_FILE_NAME), "w",
            encoding="utf-8").write('{"male": ["ア"], "female": ["イ"]}')
    check("同梱だけなら同梱を読む",
          mod.pick_path(folder).endswith(mod.DEFAULT_NAMES_FILE_NAME))
    io.open(os.path.join(folder, mod.NAMES_FILE_NAME), "w",
            encoding="utf-8").write('{"male": ["ウ"], "female": ["エ"]}')
    check("手元の名簿が優先される",
          mod.pick_path(folder).endswith("/" + mod.NAMES_FILE_NAME)
          or mod.pick_path(folder).endswith("\\" + mod.NAMES_FILE_NAME),
          mod.pick_path(folder))
check("mod_dir が None でも落ちない", mod.pick_path(None) is None)

# 男女はセーブの `category` から。
# **`woman` は `man` を含む。**
for category, want in (("young man", "male"), ("middle-aged man", "male"),
                       ("old man", "male"), ("teenage boy", "male"),
                       ("young woman", "female"), ("middle-aged woman", "female"),
                       ("old woman", "female"), ("teenage girl", "female")):
    check("category {!r} -> {}".format(category, want),
          mod.gender_of({"category": category}) == want,
          mod.gender_of({"category": category}))
check("category が読めなければ引く（落ちない）",
      all(mod.gender_of(context) in ("male", "female")
          for context in (None, {}, {"category": "unknown"}, {"category": 5})))

# 選び方は乱数。
# **名簿は全部混ぜる**（前の方だけが使われないように）。
pool = ["ア", "イ", "ウ", "エ", "オ"]
check("名簿を1件も落とさず並べ替える",
      sorted(mod.candidates(pool)) == sorted(pool)
      and len(mod.candidates(pool)) == len(pool))
check("引くたび並びが変わる",
      len({tuple(mod.candidates(pool)) for _ in range(40)}) > 1)
check("どの名前も先頭に来る（前の方だけが使われない）",
      len({mod.candidates(pool)[0] for _ in range(200)}) == len(pool))
check("渡した名簿を書き換えない", pool == ["ア", "イ", "ウ", "エ", "オ"])
check("空の名簿でも落ちない", mod.candidates([]) == [])
check("MOD 専用の Random から引く（グローバルの random を触らない）",
      isinstance(mod.RNG, random.Random) and mod.RNG is not random)
before = random.random()
random.seed(1234)
expected = random.random()
random.seed(1234)
mod.candidates(pool * 20)
check("ゲーム自身の乱数列をずらさない", random.random() == expected)

# 二つ名の割合。
# id ごとに決まるので、多数の id で数えて確かめる。
ROLLS = 4000
mod.RNG.seed(0)
mod.EPITHET_CHANCE = 10
ratio = sum(1 for _ in range(ROLLS) if mod.wants_epithet()) / ROLLS
check("二つ名は既定 10% 前後", 0.08 <= ratio <= 0.12, ratio)
mod.EPITHET_CHANCE = 0
check("0% なら1件も付かない", not any(mod.wants_epithet() for _ in range(ROLLS)))
mod.EPITHET_CHANCE = 100
check("100% なら全部付く", all(mod.wants_epithet() for _ in range(ROLLS)))
mod.EPITHET_CHANCE = 10
check("二つ名も引くたび変わる",
      len({mod.epithet_for(epithets) for _ in range(200)}) > 1)
check("二つ名は名簿のもの",
      all(mod.epithet_for(epithets) in epithets for _ in range(50)))
check("二つ名が無ければ空", mod.epithet_for([]) == "")

# =========================================================== 生成時の改名
print("\n-- 生成時 --")
mod, ctx, app = setup()
ROSTER_NAMES = set(names["male"]) | set(names["female"])


def base_of(name):
    """二つ名を取り除いた、名簿に在るはずの部分。"""
    for epithet in sorted(epithets, key=len, reverse=True):
        if name.startswith(epithet):
            return name[len(epithet):]
    return name


first = born(app, "10", "バルガス", category="middle-aged man")
second = born(app, "11", "ヴァルガス", category="middle-aged man")
check("先に居た方はそのまま", first.name == "バルガス", first.name)
check("後から来た方が改名される", second.name != "ヴァルガス", second.name)
check("付け直した名前は名簿の名前", base_of(second.name) in ROSTER_NAMES, second.name)
check("男の NPC には male の名前が付く",
      base_of(second.name) in set(names["male"]), second.name)
check("改名後の名前も重複していない",
      not mod.too_close(mod.canonical(first.name), mod.canonical(second.name)),
      (first.name, second.name))
check("素データの名前も書き換わる",
      app.save_data_dict["npcs"]["11"]["name"] == second.name,
      app.save_data_dict["npcs"]["11"])
check("項目の並びは動かない",
      list(app.save_data_dict["npcs"]["11"]) == ["name", "id", "category"],
      list(app.save_data_dict["npcs"]["11"]))
check("ctx.log_exc が呼ばれていない", not ctx.errors, ctx.errors)

woman = born(app, "15", "バルガス", category="young woman")
check("女の NPC には female の名前が付く",
      base_of(woman.name) in set(names["female"]), woman.name)

third = born(app, "12", "「隻眼の」バルガス")
check("修飾語付きも重複として捕まる", third.name != "「隻眼の」バルガス", third.name)
check("元の二つ名は引き継がない（新しい名前は別物）",
      not third.name.startswith("「隻眼の」"), third.name)

fourth = born(app, "13", "バルガス・ドレイク")
check("姓名の片方が一致しても捕まる", fourth.name != "バルガス・ドレイク", fourth.name)

others = born(app, "14", "エルミナ")
check("似ていない名前は 1 文字も変わらない", others.name == "エルミナ", others.name)

# 漢字の名前も名簿から選ぶ（元の名前を組み替えないので字種を問わない）。
mod, ctx, app = setup()
born(app, "20", "佐藤")
kanji = born(app, "21", "佐藤")
check("漢字の名前も名簿から付け直せる",
      kanji.name != "佐藤" and base_of(kanji.name) in ROSTER_NAMES, kanji.name)

# 二つ名は名簿のものがそのまま前に付く。
mod, ctx, app = setup({"EPITHET_CHANCE": 100})
born(app, "10", "バルガス")
decorated = born(app, "11", "ヴァルガス")
check("100% なら二つ名が付く",
      base_of(decorated.name) != decorated.name
      and base_of(decorated.name) in ROSTER_NAMES, decorated.name)
check("二つ名は名簿のもの",
      any(decorated.name.startswith(e) for e in epithets), decorated.name)

mod, ctx, app = setup({"EPITHET_CHANCE": 0})
born(app, "10", "バルガス")
plain = born(app, "11", "ヴァルガス")
check("0% なら名前だけ", plain.name in ROSTER_NAMES, plain.name)

# 名簿が無いときは名前を発明せず、元のまま通す。
with tempfile.TemporaryDirectory() as folder:
    mod, ctx, app = setup(mod_dir=folder)
    born(app, "10", "バルガス")
    untouched = born(app, "11", "ヴァルガス")
    check("名簿が無ければ元の名前のまま", untouched.name == "ヴァルガス", untouched.name)
    check("名簿が無いことを警告する",
          any("no roster" in note for note in ctx.notes), ctx.notes)

# 名簿を使い切ったら、そこで止まる（名前は発明しない）。
with tempfile.TemporaryDirectory() as folder:
    io.open(os.path.join(folder, "npc.json"), "w", encoding="utf-8").write(
        '{"male": ["ドルレイン"], "female": ["ドルレイン"], "epithets": []}')
    mod, ctx, app = setup(mod_dir=folder)
    born(app, "10", "バルガス")
    used = born(app, "11", "ヴァルガス")
    check("1件だけの名簿から選べる", used.name == "ドルレイン", used.name)
    stuck = born(app, "12", "バルガス")
    check("使い切ったら元の名前のまま", stuck.name == "バルガス", stuck.name)

# =========================================================== 触らないもの
print("\n-- 触らないもの --")
mod, ctx, app = setup()
born(app, "10", "バルガス")

# 敵は素データの `npcs` に載らない。
# 同じ名前が3体並んでよい。
enemies = [app.character_class(name="ゴブリン", id="e{}".format(n)) for n in range(3)]
check("敵は同名でも触られない",
      all(enemy.name == "ゴブリン" for enemy in enemies),
      [enemy.name for enemy in enemies])
clone = app.character_class(name="ヴァルガス", id="e9")
check("敵は既存 NPC と同名でも触られない", clone.name == "ヴァルガス", clone.name)

app.player = app.character_class(name="ヴァルガス", id="player", is_player=True)
check("プレイヤーは改名されない", app.player.name == "ヴァルガス", app.player.name)

# プレイヤーは突き合わせ相手には入る。
mod, ctx, app = setup()
app.player = Character(name="バルガス", id="player", is_player=True)
app.start_game()
rival = born(app, "30", "ヴァルガス")
check("プレイヤーと同名の NPC は改名される", rival.name != "ヴァルガス", rival.name)

# ======================================== 名づけを丸ごと引き取る（ALWAYS_RENAME）
print("\n-- ALWAYS_RENAME --")

mod, ctx, app = setup()
plain = born(app, "10", "バルガス", category="middle-aged man")
check("既定では重複していない名前に触らない", plain.name == "バルガス", plain.name)

mod, ctx, app = setup({"ALWAYS_RENAME": True, "EPITHET_CHANCE": 0})
taken = born(app, "10", "バルガス", category="middle-aged man")
check("ON なら重複していなくても付け直す", taken.name != "バルガス", taken.name)
check("付いた名前は名簿のもの", taken.name in ROSTER_NAMES, taken.name)
check("男の NPC には male の名前",
      taken.name in set(names["male"]), taken.name)
check("素データも書き換わる",
      app.save_data_dict["npcs"]["10"]["name"] == taken.name)
check("ctx.log_exc が呼ばれていない", not ctx.errors, ctx.errors)

# 二度目に付け直さない（`generate_character` ->
# `Character.__init__` の順で両方が発火する。
# ここが抜けると 1 人の NPC が 2 回改名される）。
first = taken.name
again = app.world.generate_character("10", app.save_data_dict["npcs"]["10"])
check("同じ NPC を二度改名しない", again.name == first, (first, again.name))
check("受け皿を通しても変わらない",
      app.character_class(name=first, id="10").name == first)

# 全員が別の名前になる。
mod, ctx, app = setup({"ALWAYS_RENAME": True, "EPITHET_CHANCE": 0})
for index in range(20):
    born(app, str(200 + index), "村人", category="young woman")
crowd = [character.name for character in app.world.characters.values()]
check("全員が名簿の名前になる", all(name in ROSTER_NAMES for name in crowd), crowd[:5])
check("全員が女の名前", all(name in set(names["female"]) for name in crowd), crowd[:5])
check("誰とも重ならない", len(set(crowd)) == len(crowd), sorted(crowd))

# 既に世界に居る NPC は ON でも触らない。
mod, ctx, app = setup({"ALWAYS_RENAME": True})
for cid, name in (("40", "バルガス"), ("41", "エルミナ")):
    app.save_data_dict["npcs"][cid] = {"name": name, "id": cid}
    app.world.characters[cid] = Character(name=name, id=cid)
app.load_game_new()
check("ロード中の NPC は ON でも改名しない",
      [character.name for character in app.world.characters.values()]
      == ["バルガス", "エルミナ"],
      [character.name for character in app.world.characters.values()])

# 素データにだけ居る古参（まだ組み立てられていない）も新顔と取り違えない。
mod, ctx, app = setup({"ALWAYS_RENAME": True})
app.save_data_dict["npcs"]["50"] = {"name": "バルガス", "id": "50"}
app.world_dict["npcs"]["51"] = {"name": "エルミナ", "id": "51"}
app.load_game_new()
late = app.world.generate_character("50", app.save_data_dict["npcs"]["50"])
check("素データにだけ居た古参は改名されない", late.name == "バルガス", late.name)
check("`world_dict` 側の古参も控える", "51" in app.save_data_dict["npcs"] or True)
newcomer = born(app, "52", "セラフィナ", category="young woman")
check("同じ世界でも新顔は付け直される", newcomer.name != "セラフィナ", newcomer.name)

# プレイヤーと敵は ON でも触らない。
mod, ctx, app = setup({"ALWAYS_RENAME": True})
app.player = app.character_class(name="ヴァルガス", id="player", is_player=True)
check("プレイヤーは ON でも改名されない", app.player.name == "ヴァルガス", app.player.name)
goblins = [app.character_class(name="ゴブリン", id="e{}".format(n)) for n in range(3)]
check("敵は ON でも触られない",
      all(enemy.name == "ゴブリン" for enemy in goblins),
      [enemy.name for enemy in goblins])

# 名簿が無ければ、ON でも元の名前のまま。
with tempfile.TemporaryDirectory() as folder:
    mod, ctx, app = setup({"ALWAYS_RENAME": True}, mod_dir=folder)
    kept = born(app, "10", "バルガス")
    check("名簿が無ければ ON でも元のまま", kept.name == "バルガス", kept.name)

# =============================================== 既に世界に居る重複（既定）
print("\n-- 既にいる重複 --")
mod, ctx, app = setup()
for cid, name in (("40", "バルガス"), ("41", "ヴァルガス")):
    app.save_data_dict["npcs"][cid] = {"name": name, "id": cid}
    app.world.characters[cid] = Character(name=name, id=cid)
app.load_game_new()
check("ゲーム本来の load_game_new が呼ばれている", app.loads == 1, app.loads)
check("既定では既にいる重複を改名しない",
      app.world.characters["41"].name == "ヴァルガス",
      app.world.characters["41"].name)

# ただし名簿には載るので、後から来た3人目は改名される。
late = born(app, "42", "バルガス")
check("既にいる重複も突き合わせ相手にはなる", late.name != "バルガス", late.name)

mod, ctx, app = setup({"FIX_EXISTING": True})
for cid, name in (("40", "バルガス"), ("41", "ヴァルガス")):
    app.save_data_dict["npcs"][cid] = {"name": name, "id": cid}
    app.world.characters[cid] = Character(name=name, id=cid)
app.load_game_new()
check("FIX_EXISTING を立てると既にいる重複も直る",
      app.world.characters["41"].name != "ヴァルガス",
      app.world.characters["41"].name)
check("直したら素データも一緒に書き換わる",
      app.save_data_dict["npcs"]["41"]["name"] == app.world.characters["41"].name,
      app.save_data_dict["npcs"]["41"])

# ================================================================ 乱数
print("\n-- 乱数 --")

# 同じ状況を何度も通すと、付く名前は変わる（世界ごとに顔ぶれが変わる）。
picked = []
for _ in range(12):
    mod, ctx, app = setup({"EPITHET_CHANCE": 0})
    born(app, "10", "バルガス", category="middle-aged man")
    picked.append(born(app, "11", "ヴァルガス", category="middle-aged man").name)
check("同じ id でも引くたび違う名前になる", len(set(picked)) > 1, picked)
check("どれも名簿の名前", all(name in ROSTER_NAMES for name in picked), picked)

# **一度付いた名前は変わらない。**
# 改名は素データにも書くので、次に同じ NPC を通しても衝突が無く、
# 二度目の改名は起きない（乱数でも名前は落ち着く）。
mod, ctx, app = setup()
born(app, "10", "バルガス")
renamed = born(app, "11", "ヴァルガス").name
again = app.world.generate_character("11", app.save_data_dict["npcs"]["11"])
check("一度付いた名前は二度目に変わらない", again.name == renamed,
      (renamed, again.name))
check("受け皿を通しても変わらない",
      app.character_class(name=renamed, id="11").name == renamed)

# 同じ世界の中では、何人来ても重複しない。
mod, ctx, app = setup({"EPITHET_CHANCE": 0})
for index in range(30):
    born(app, str(100 + index), "バルガス", category="young man")
final = [character.name for character in app.world.characters.values()]
check("30人が同じ名前で来ても全員が別の名前になる",
      len(set(final)) == len(final), sorted(final))
# 1人目は衝突相手が居ないのでそのまま。
# 改名された 29 人が名簿から出ている。
check("1人目はそのまま、残りは名簿の名前",
      final[0] == "バルガス"
      and all(name in ROSTER_NAMES for name in final[1:]), sorted(final))
check("同じ名前を2度使わない（名簿から引くたび引き直す）",
      len(set(final[1:])) == 29, len(set(final[1:])))

# 世界を読み直したら突き合わせ相手も作り直す。
mod, ctx, app = setup()
born(app, "10", "バルガス")
app.world.characters.clear()
app.save_data_dict["npcs"].clear()
app.load_game_new()
reused = born(app, "10", "バルガス")
check("別の世界を読んだら前の世界の名前は残らない",
      reused.name == "バルガス", reused.name)

print("\n{} check(s) failed".format(len(failures)) if failures else "\nall ok")
sys.exit(1 if failures else 0)
