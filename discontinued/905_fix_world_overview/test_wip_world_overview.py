# -*- coding: utf-8 -*-
"""905_fix_world_overview をゲーム抜きで通す。

    python tools/tests/test_wip_world_overview.py

偽の `llm_manager_world_generate`（入力を無視して自分の文章を返す）と偽の
`save_world_json`（応答の `overview` を `write_obfuscated_json_file` で保存する）を
組んで、次を確認する。

  差し替え  … 応答の `overview` が、入力した文章そのものになる
  位置引数  … 引数がキーワードでも位置でも同じように拾える
  保存      … 保存された `world_data["overview"]` が入力した文章になる（二段目の照合が OK）
  空入力    … 概要が空なら何もしない（`create_world_overview` 側の経路）
  残す      … KEEP_GENERATED を入れると入力の後ろに本体の文章が続く
  辞書      … 応答が pydantic ではなく辞書で来ても差し替えられる
  項目なし  … 応答に `overview` が無ければ触らず、警告だけ残す
  ずれ      … 保存された文章が差し替えたものと違えば WARN が出る
  1回だけ   … 照合は生成の直後の1回だけ。以後の保存では走らない
  他の書出  … `world_data.json` 以外への書き出しは素通しする
  無事故    … どの経路でも ctx.log_exc が呼ばれない

二段目は `save_world_json:write_obfuscated_json_file` に載る。
`generate_new_world` ではない理由は入口ファイルの docstring と DOC.md §3。
偽ゲーム側も**保存を `write_obfuscated_json_file` 経由にしてある**ので、
包みの通り道は本番と同じ。

差し替えの根拠（`World` の項目名・呼び出しの順）は
この MOD の `DOC.md` §1 と、入口ファイルの docstring。
開発中（9xx）なので CI では走らない（TECH.md §2.6）。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    # この検査は mod と同じフォルダに置いてある（`local/` へ移した後の形）。
    # 隣に `mod.json` が在るならそれが対象。`runtime/mods` は見ない。
    here_manifest = os.path.join(HERE, "mod.json")
    if os.path.isfile(here_manifest):
        with io.open(here_manifest, encoding="utf-8") as fh:
            return os.path.join(HERE, json.load(fh)["entry"])
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


MOD = find_mod("_fix_world_overview")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# 入力した文章（ヴェスティアの世界観の書き出しを短くしたもの）。
PLOT = ("# 世界観\nこの資料は【A】と【B】の二つに分かれています。\n"
        "【A】は、あなただけが知る世界の真実です。出力に一切書きません。")

# 本体の LLM が書き直して返してくる文章。入力の【A】【B】の構造は残らない。
GENERATED = "神が去った後の世界。人々は遺物を巡って争っている。"

SAVE_KEY = b"Instantale_Save_Key_2026"


def xor_with_key(payload):
    return bytes(b ^ SAVE_KEY[i % len(SAVE_KEY)] for i, b in enumerate(payload))


class World:
    """`create_world_overview_from_plot` の応答（`World`）。"""

    def __init__(self, world_name, overview, structure_description, structure):
        self.world_name = world_name
        self.overview = overview
        self.structure_description = structure_description
        self.structure = structure


class FakeGame:
    """呼ばれ方を本番に合わせた最小の世界生成。

    `generate_new_world` が `llm_manager_world_generate` の属性を経由して呼ぶので、
    フックを属性に載せれば本番と同じ経路を通る。
    """

    def __init__(self, root, response=None):
        self.root = root
        self.response = response
        self.calls = []

    # -- scripts.llm.llm_manager_world_generate ---------------------------
    def create_world_overview_from_plot(self, world_name, world_overview):
        self.calls.append(("from_plot", world_name, world_overview))
        if self.response is not None:
            return self.response
        return World(world_name=world_name, overview=GENERATED,
                     structure_description="中心から放射状に広がる3層9エリア。",
                     structure={"connected_settlement_1": "ヘイズル"})

    def create_world_overview(self):
        self.calls.append(("no_plot",))
        return World(world_name="でたらめな世界", overview=GENERATED,
                     structure_description="中心から放射状に広がる3層9エリア。",
                     structure={"connected_settlement_1": "ヘイズル"})

    # -- save_world_json ---------------------------------------------------
    def generate_new_world(self, world_name="", world_overview="",
                           free_facility_enabled=False):
        llm = sys.modules["scripts.llm.llm_manager_world_generate"]
        if world_overview:
            base = llm.create_world_overview_from_plot(world_name, world_overview)
        else:
            base = llm.create_world_overview()
        overview = (base.get("overview") if isinstance(base, dict)
                    else getattr(base, "overview", None))
        self.save(world_name, overview)
        return "saved"

    def save(self, world_name, overview, basename="world_data.json"):
        """本体と同じく `write_obfuscated_json_file` 経由で書き出す。

        モジュールの属性を毎回引く（本番の呼ばれ方）。
        直接 `self.write_obfuscated_json_file` を呼ぶと包みを通らないので、
        検査が本番より甘くなる。
        """
        folder = self.world_folder(world_name)
        os.makedirs(folder, exist_ok=True)
        data = {"world_data": {"name": world_name, "overview": overview,
                               "structure_description": "…", "story": {},
                               "days_elapsed": 0},
                "areas": {}, "npcs": {}, "version": 0}
        save_world = sys.modules["save_world_json"]
        save_world.write_obfuscated_json_file(
            os.path.join(folder, basename), data)

    def world_folder(self, world_name):
        return os.path.join(self.root, "Darmabeko", "Instantale", "worlds",
                            sanitize_path_name(world_name))

    def write_obfuscated_json_file(self, file_path, data):
        self.calls.append(("write", os.path.basename(str(file_path))))
        payload = json.dumps(data, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        with open(file_path, "wb") as fh:
            fh.write(xor_with_key(payload))


def sanitize_path_name(name):
    for bad in "\\/:*?\"<>|":
        name = name.replace(bad, "_")
    return name.strip() or "world"


def read_json_with_obfuscation_fallback(path):
    """本体の復号器と同じ約束（暗号化されていなければ素の JSON として読む）。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return json.loads(xor_with_key(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return json.loads(raw.decode("utf-8"))


def install_fakes(game, with_codec=True):
    scripts = types.ModuleType("scripts")
    llm_pkg = types.ModuleType("scripts.llm")
    llm = types.ModuleType("scripts.llm.llm_manager_world_generate")
    llm.create_world_overview_from_plot = game.create_world_overview_from_plot
    llm.create_world_overview = game.create_world_overview
    llm_pkg.llm_manager_world_generate = llm
    scripts.llm = llm_pkg

    save_world = types.ModuleType("save_world_json")
    save_world.generate_new_world = game.generate_new_world
    save_world.write_obfuscated_json_file = game.write_obfuscated_json_file

    sys.modules["scripts"] = scripts
    sys.modules["scripts.llm"] = llm_pkg
    sys.modules["scripts.llm.llm_manager_world_generate"] = llm
    sys.modules["save_world_json"] = save_world

    sys.modules.pop("scripts.save_codec", None)
    if with_codec:
        codec = types.ModuleType("scripts.save_codec")
        codec.read_json_with_obfuscation_fallback = \
            read_json_with_obfuscation_fallback
        scripts.save_codec = codec
        sys.modules["scripts.save_codec"] = codec
    return llm, save_world


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

    # ログは本物の `ctx.logger` をそのまま借りる（検査だけ別の処理を通さない）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label, cap=cap)

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
        "world_overview_mod", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, llm, save_world):
    """フックを本番と同じ形（モジュールの属性の差し替え）で載せる。"""
    for target, owner, name in (
            ("scripts.llm.llm_manager_world_generate:"
             "create_world_overview_from_plot", llm,
             "create_world_overview_from_plot"),
            ("scripts.llm.llm_manager_world_generate:create_world_overview",
             llm, "create_world_overview"),
            ("save_world_json:write_obfuscated_json_file", save_world,
             "write_obfuscated_json_file")):
        hook = hooks.get(target)
        if hook is None:
            continue
        original = getattr(owner, name)

        def make(hook=hook, original=original):
            def call(*args, **kwargs):
                return hook(original, *args, **kwargs)
            return call

        setattr(owner, name, make())


ROOT = os.path.join(OUT_DIR, "worlds_root")
LOG_PATH = os.path.join(OUT_DIR, "world_overview.log")


def setup(keep_generated=False, response=None, with_codec=True):
    """mod を適用し、(mod, ctx, game, save_world) を返す。"""
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT, exist_ok=True)
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)

    game = FakeGame(ROOT, response=response)
    llm, save_world = install_fakes(game, with_codec=with_codec)
    mod = load_mod()
    mod.KEEP_GENERATED = keep_generated
    ctx = FakeCtx(OUT_DIR)
    mod.apply(ctx)
    install(ctx.hooks, llm, save_world)
    return mod, ctx, game, save_world


def log_text():
    if not os.path.exists(LOG_PATH):
        return ""
    with io.open(LOG_PATH, encoding="utf-8") as fh:
        return fh.read()


def saved_overview(world_name):
    path = os.path.join(ROOT, "Darmabeko", "Instantale", "worlds",
                        sanitize_path_name(world_name), "world_data.json")
    if not os.path.exists(path):
        return None
    return read_json_with_obfuscation_fallback(path)["world_data"]["overview"]


# ==================================================== 仕掛け口
# ここだけは振る舞いではなく**どこに載せたか**を見る。
# `generate_new_world` は画面へ関数のまま渡されて控えられるので、
# モジュールの属性を差し替えても控えには届かない（DOC.md §3）。
# 戻すと検査は全部通ったまま実機で二段目だけ黙るので、載せ先を直接縛る。
print("\n-- 仕掛け口 --")
mod, ctx, game, save_world = setup()
check("書き出しの実体に載っている",
      "save_world_json:write_obfuscated_json_file" in ctx.hooks,
      sorted(ctx.hooks))
check("generate_new_world には載せない",
      "save_world_json:generate_new_world" not in ctx.hooks,
      sorted(ctx.hooks))

# ==================================================== 入力どおりになること
print("\n-- 差し替え --")
mod, ctx, game, save_world = setup()
save_world.generate_new_world("ヴェスティア", PLOT, False)
check("保存された概要が入力した文章そのもの", saved_overview("ヴェスティア") == PLOT,
      saved_overview("ヴェスティア"))
check("本体が書いた文章は混ざらない", GENERATED not in (saved_overview("ヴェスティア") or ""))
check("差し替えが記録に残る", "overview replaced:" in log_text(), log_text()[-400:])
check("読み返しが一致する", "OK the saved world_data['overview']" in log_text(),
      log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 引数の拾い方 --")
mod, ctx, game, save_world = setup()
hook = ctx.hooks["scripts.llm.llm_manager_world_generate:"
                 "create_world_overview_from_plot"]
by_keyword = hook(game.create_world_overview_from_plot,
                  world_name="ヴェスティア", world_overview=PLOT)
check("キーワードで拾える", by_keyword.overview == PLOT, by_keyword.overview)
by_position = hook(game.create_world_overview_from_plot, "ヴェスティア", PLOT)
check("位置で拾える", by_position.overview == PLOT, by_position.overview)

print("\n-- 触らないもの --")
mod, ctx, game, save_world = setup()
save_world.generate_new_world("でたらめな世界", "", False)
check("概要が空なら本体のまま", saved_overview("でたらめな世界") == GENERATED,
      saved_overview("でたらめな世界"))
check("何も差し替えていないと記録される",
      "nothing was replaced" in log_text(), log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

mod, ctx, game, save_world = setup()
hook = ctx.hooks["scripts.llm.llm_manager_world_generate:"
                 "create_world_overview_from_plot"]
spaces = hook(game.create_world_overview_from_plot, "ヴェスティア", "   \n  ")
check("空白だけの入力も差し替えない", spaces.overview == GENERATED, spaces.overview)

print("\n-- 本体の文章も残す（KEEP_GENERATED） --")
mod, ctx, game, save_world = setup(keep_generated=True)
save_world.generate_new_world("ヴェスティア", PLOT, False)
kept = saved_overview("ヴェスティア")
check("入力が先頭に来る", (kept or "").startswith(PLOT), (kept or "")[:40])
check("本体の文章が後ろに続く", (kept or "").endswith(GENERATED), (kept or "")[-40:])
check("区切りが入る", kept == PLOT + mod.KEEP_SEPARATOR + GENERATED)
check("読み返しが一致する", "OK the saved world_data['overview']" in log_text(),
      log_text()[-400:])

print("\n-- 応答の形が違うとき --")
mod, ctx, game, save_world = setup(
    response={"world_name": "ヴェスティア", "overview": GENERATED,
              "structure_description": "…", "structure": {}})
save_world.generate_new_world("ヴェスティア", PLOT, False)
check("辞書で来ても差し替えられる", saved_overview("ヴェスティア") == PLOT,
      saved_overview("ヴェスティア"))

mod, ctx, game, save_world = setup(response={"world_name": "ヴェスティア"})
save_world.generate_new_world("ヴェスティア", PLOT, False)
check("overview が無ければ触らない", saved_overview("ヴェスティア") is None,
      saved_overview("ヴェスティア"))
check("触れなかったことが警告に出る",
      "has no readable 'overview'" in log_text(), log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 保存された文章がずれたとき --")
mod, ctx, game, save_world = setup()
hook = ctx.hooks["scripts.llm.llm_manager_world_generate:"
                 "create_world_overview_from_plot"]
# 差し替えた後、本体が別の文章を保存してしまう場合の再現。
hook(game.create_world_overview_from_plot, "ヴェスティア", PLOT)
game.save("ヴェスティア", GENERATED)
check("ずれたら WARN が出る",
      "WARN the saved world_data['overview'] is not what was put in"
      in log_text(), log_text()[-400:])
check("WARN はローダのログにも出る",
      any("does not match" in note for note in ctx.notes), ctx.notes)

print("\n-- 照合は生成の直後の1回だけ --")
mod, ctx, game, save_world = setup()
save_world.generate_new_world("ヴェスティア", PLOT, False)
first = log_text()
check("1回目で照合が済む", "OK the saved world_data['overview']" in first,
      first[-400:])
# 遊んでいる間の保存。本体が概要を書き換えていても、ここでは何も言わない。
game.save("ヴェスティア", GENERATED)
check("2回目以降は照合しない", log_text() == first,
      log_text()[len(first):][:400])
check("2回目の WARN も出ない",
      not any("does not match" in note for note in ctx.notes), ctx.notes)

print("\n-- world_data.json 以外への書き出し --")
mod, ctx, game, save_world = setup()
hook = ctx.hooks["scripts.llm.llm_manager_world_generate:"
                 "create_world_overview_from_plot"]
hook(game.create_world_overview_from_plot, "ヴェスティア", PLOT)
before = log_text()
game.save("ヴェスティア", GENERATED, basename="area_data.json")
check("エリアの書き出しは素通し", log_text() == before,
      log_text()[len(before):][:400])
# 素通しした後も控えは生きていて、本物の world_data.json で照合できる。
game.save("ヴェスティア", PLOT)
check("その後の world_data.json で照合する",
      "OK the saved world_data['overview']" in log_text(), log_text()[-400:])

print("\n-- 復号器が引けないとき --")
mod, ctx, game, save_world = setup(with_codec=False)
save_world.generate_new_world("ヴェスティア", PLOT, False)
check("差し替え自体は効く", saved_overview("ヴェスティア") == PLOT,
      saved_overview("ヴェスティア"))
check("読み返せなかったと記録される", "could not read" in log_text(),
      log_text()[-400:])
check("書き出す直前の中身で照合する",
      "OK what was handed to the writer is what you wrote" in log_text(),
      log_text()[-400:])
check("読み返しの印は出さない",
      "OK the saved world_data['overview']" not in log_text(),
      log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

shutil.rmtree(ROOT, ignore_errors=True)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    raise SystemExit(1)
print("all checks passed")
