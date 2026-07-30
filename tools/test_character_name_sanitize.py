# -*- coding: utf-8 -*-
"""110_fix_character_name_path.py をゲーム抜きで通す。

    python tools/test_character_name_sanitize.py

偽の `scripts.characters.Character` / `InstantaleApp` を差し込み、次を確認する。

  変換   … `< > : " / \\ | ? *` が全角に写り、末尾の空白・ピリオドが落ちる
  素通り … 正しい名前（実データの `「試作」のテストA` 等）は 1 文字も変わらない
  入口   … `Character.__init__` を通った全員が直る（LLM生成・プリセット・ロード）
  救済   … 注入時とロード時に `app.world.characters` と `app.player` が掃かれる
  自制   … 予約デバイス名・空になる名前は**触らずに記録だけ**
  実地   … 直した名前で実際に `os.makedirs` が通る（生の名前では WinError 123）

最後の1つが本命。VERIFICATION.md §2.14 の落ちた名前そのもので、**この OS 上で**確かめる。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
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


MOD = find_mod("_fix_character_name_path")

# 実機で落ちた名前そのもの（out/live_crashes.log、2026-07-28T00:06:36）。
CRASHED = '試験人形「テストダミー"'

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    """本物と同じく、名前は `__init__` から入る。"""

    def __init__(self, name=None, id=None, **kw):
        self.name = name
        self.id = id
        self.__dict__.update(kw)


class World:
    def __init__(self, characters):
        self.characters = characters


class InstantaleApp:
    def __init__(self, world, player=None):
        self.world = world
        self.player = player
        self.loads = 0

    def load_game_new(self, *args, **kwargs):
        self.loads += 1

    def start_game(self, *args, **kwargs):
        self.loads += 1


BASES = {"app": InstantaleApp}


def install_fake_characters():
    """`scripts.characters` を差し込む。mod はここを入口として包む。"""
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
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.notes = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

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
    spec = importlib.util.spec_from_file_location("character_name_mod", MOD,
                                            submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, char_cls, app_cls):
    """フックを本番と同じ形（メソッドの差し替え）でクラスに載せる。"""
    for target, cls, name in (
            ("scripts.characters:Character.__init__", char_cls, "__init__"),
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


def setup(characters=None, player=None):
    """mod を適用し、フックの載った (mod, ctx, Character, app) を返す。"""
    char_cls = install_fake_characters()
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    sys.modules["__main__"].InstantaleApp = app_cls

    # 注入時の掃除を見るため、app は mod.apply の**前**に組み立てておく。
    # 名前は `Character.__init__` がまだ包まれていないので生のまま入る
    # （＝ この mod を入れる前に作られた既存キャラクタと同じ状態）。
    world = World(dict(characters or {}))
    app = app_cls(world, player)
    sys.modules["__main__"].__dict__["_test_app"] = app

    mod = load_mod()
    ctx = FakeCtx(os.path.join(HERE, os.pardir, "out", "test"))
    mod.apply(ctx)
    install(ctx.hooks, char_cls, app_cls)
    return mod, ctx, char_cls, app


# =========================================================== 変換そのもの
print("\n-- sanitize --")
mod = load_mod()

check("実機で落ちた名前が直る",
      mod.sanitize(CRASHED)[0] == "試験人形「テストダミー”",
      mod.sanitize(CRASHED))

for raw, want in ((r'a<b>c:d"e/f\g|h?i*j', "a＜b＞c：d”e／f＼g｜h？i＊j"),
                  ("末尾の空白   ", "末尾の空白"),
                  ("末尾のピリオド...", "末尾のピリオド"),
                  ("制御\x01文字", "制御文字")):
    check("変換 {!r}".format(raw), mod.sanitize(raw)[0] == want, mod.sanitize(raw))

# 実データにある正しい名前（VERIFICATION.md §2.14 で確認済みのディレクトリ名）。
for good in ("「試作」のテストA", "テスト・ネーム (Test Name)", "テストプレイヤー",
             "試験人形「テストダミー」"):
    check("素通り {!r}".format(good), mod.sanitize(good) == (None, None),
          mod.sanitize(good))

check("空文字・None は素通り",
      mod.sanitize("") == (None, None) and mod.sanitize(None) == (None, None))

# 直し方を決めていないもの ＝ **触らない**（新しい名前は None）。
# 予約名は不正文字が無くても作れないので、変化が無くても検出する。
check("予約デバイス名は触らない", mod.sanitize("CON") == (None, "reserved"),
      mod.sanitize("CON"))
check("予約名は大文字小文字を問わない", mod.sanitize("nul") == (None, "reserved"),
      mod.sanitize("nul"))
check("予約名は拡張子の手前で見る", mod.sanitize("COM1.txt") == (None, "reserved"),
      mod.sanitize("COM1.txt"))
check("予約名に見えるだけの名前は普通に直す",
      mod.sanitize('CON"') == ("CON”", "sanitized"), mod.sanitize('CON"'))
check("全部が不正文字なら触らない", mod.sanitize("...") == (None, "empty"),
      mod.sanitize("..."))

# =========================================================== 入口（生成時）
print("\n-- Character.__init__ --")
mod, ctx, char_cls, app = setup()

made = char_cls(name=CRASHED, id="101")
check("LLM が作った名前が生成時に直る",
      made.name == "試験人形「テストダミー”", made.name)

check("位置引数でも直る", char_cls(CRASHED, "102").name.endswith("”"))
check("名前が無くても落ちない", char_cls().name is None)
check("正しい名前は 1 文字も変わらない",
      char_cls(name="「試作」のテストA", id="70").name == "「試作」のテストA")
check("ctx.log_exc が呼ばれていない", not ctx.errors, ctx.errors)

reserved = char_cls(name="NUL", id="103")
check("予約名は記録だけして元のまま", reserved.name == "NUL", reserved.name)

# ======================================================= 既存キャラクタの救済
print("\n-- 既にいる人の救済 --")
broken = Character(name=CRASHED, id="101")
other = Character(name="「試作」のテストA", id="70")
player = Character(name='プレイヤー"', id="player")
mod, ctx, char_cls, app = setup({"101": broken, "70": other}, player)

check("注入した時点で世界に居る残骸が直る",
      broken.name == "試験人形「テストダミー”", broken.name)
check("プレイヤーも直る", player.name == "プレイヤー”", player.name)
check("正しい名前は触られない", other.name == "「試作」のテストA", other.name)
check("注入時の掃除が報告される",
      any("renamed 1" in note or "renamed 2" in note for note in ctx.notes),
      ctx.notes)

# ロード後（この mod を入れる前に保存されたセーブ）。
mod, ctx, char_cls, app = setup()
loaded = Character(name=CRASHED, id="101")
app.world.characters["101"] = loaded
app.load_game_new()
check("ロード直後にも掃かれる", loaded.name.endswith("”"), loaded.name)
check("ゲーム本来の load_game_new が呼ばれている", app.loads == 1, app.loads)

app.world.characters["104"] = Character(name='別人"', id="104")
app.start_game()
check("start_game でも掃かれる", app.world.characters["104"].name == "別人”")

# 名簿が辞書でない場合（TECH.md GAME.md §2.8: 入れ物は形で決めつけない）。
mod, ctx, char_cls, app = setup()
listed = Character(name=CRASHED, id="101")
app.world.characters = [listed]
app.load_game_new()
check("名簿が配列でも掃ける", listed.name.endswith("”"), listed.name)

# 世界がまだ無い時点で注入しても落ちない。
mod, ctx, char_cls, app = setup()
app.world = None
app.load_game_new()
check("world が無くても落ちない", not ctx.errors, ctx.errors)

# ========================================================= ログ（旧名を残す）
print("\n-- ログ --")
log_path = os.path.join(HERE, os.pardir, "out", "test", mod.LOG_BASENAME)
if os.path.exists(log_path):
    os.remove(log_path)
mod, ctx, char_cls, app = setup({"101": Character(name=CRASHED, id="101")})
char_cls(name='別の"', id="105")
char_cls(name='別の"', id="105")          # 同じ組は 1 回だけ
text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
check("旧名がログに残る", CRASHED in text, text[:200])
check("同じ組は 1 回しか出ない", text.count("id='105'") == 1, text)

# 名前と同じ文字列を持つ他の属性は**記録だけ**する。
mod, ctx, char_cls, app = setup()
echo = char_cls(name=CRASHED, id="106")
echo2 = Character(name=CRASHED, id="107", display_name=CRASHED)
app.world.characters["107"] = echo2
app.load_game_new()
check("名前の写しは書き換えない", echo2.display_name == CRASHED, echo2.display_name)
text = open(log_path, encoding="utf-8").read()
check("名前の写しはログに出る", "display_name" in text, text[-300:])

# ================================================= 実地（この OS で作れるか）
print("\n-- os.makedirs（実地） --")
tmp = tempfile.mkdtemp(prefix="namefix_")
try:
    fixed = mod.sanitize(CRASHED)[0]
    try:
        os.makedirs(os.path.join(tmp, "characters", fixed))
        made_ok, why = True, ""
    except OSError as exc:
        made_ok, why = False, exc
    check("直した名前で実際にディレクトリが作れる", made_ok, why)

    if os.name == "nt":
        try:
            os.makedirs(os.path.join(tmp, "characters2", CRASHED))
            raised = None
        except OSError as exc:
            raised = exc
        check("生の名前は今でも WinError 123 で落ちる",
              raised is not None and getattr(raised, "winerror", None) == 123,
              raised)
    else:
        print("  skip 生の名前の再現（Windows でのみ意味がある）")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("FAILED: {} 件".format(len(failures)))
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("すべて通った")
