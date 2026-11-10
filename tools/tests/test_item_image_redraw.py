# -*- coding: utf-8 -*-
"""128_item_image_variety の描き直し・候補の幅の上限・色と材質の補正をゲーム抜きで通す。

    python tools/tests/test_item_image_redraw.py

- 同梱の描き直し: `images/redraw/<品種>/<鍵>.png` の鍵と `data/redraw/<品種>.json` の鍵が同じで、埋め込みは 384 次元。
  `data/tags.json` の語の組は本体の `parse_tags` で読め、`tags_text` に戻すと同じ文字列
- 素の絵のパスの読み方（区切りが / でも \\ でも。写し `xs.<元の部位>.<鍵>` は元の部位へ）と語の組の決まり
- 偽のゲームと偽の 128 のフォルダで選定の関数を呼び、
  描き直しのある素の絵は描き直しの埋め込みで選ばれること・切れば素の埋め込みに戻ること・
  候補の幅の上限（SIM_GAP）で外れた絵が使われないこと・色が食い違う絵が下がること
- 品の絵を描く所で、描き直しのある素の絵だけ描く部品に渡すパスを替え、品の image_src（セーブに残る値）は変えないこと

torch と numpy は使わない（CI に無い）。本体が使う演算だけ素の Python で真似る。
"""
import importlib.util
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "128_item_image_variety")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402
import instantale_modloader.llm as llm  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---- 同梱の描き直し ---------------------------------------------------------------
redraw_images = os.path.join(MOD_DIR, "images", "redraw")
redraw_data = os.path.join(MOD_DIR, "data", "redraw")
pngs = {slot: {n[:-4] for n in os.listdir(os.path.join(redraw_images, slot)) if n.endswith(".png")}
        for slot in os.listdir(redraw_images)}
dicts = {n[:-5]: json.load(io.open(os.path.join(redraw_data, n), encoding="utf-8"))
         for n in os.listdir(redraw_data) if n.endswith(".json") and n != "captions.json"}
check("描き直しの絵がある", sum(len(v) for v in pngs.values()) > 0)
lost = sorted("{}/{}".format(s, k) for s, keys in pngs.items() for k in keys - set(dicts.get(s, {}).get("index", {})))
check("描き直しの絵はどれも埋め込みがある", not lost, lost[:5])
extra = sorted("{}/{}".format(s, k) for s, d in dicts.items() for k in set(d["index"]) - pngs.get(s, set()))
check("埋め込みの鍵はどれも絵がある", not extra, extra[:5])
dims = {len(v) for d in dicts.values() for v in d["vectors"]}
check("埋め込みは 384 次元", dims == {384}, dims)
check("番号は埋め込みの範囲内", all(0 <= i < len(d["vectors"]) for d in dicts.values() for i in d["index"].values()))

spec = importlib.util.spec_from_file_location("iiv_redraw_pure", os.path.join(MOD_DIR, "item_image_variety.py"))
pure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pure)
tags = json.load(io.open(os.path.join(MOD_DIR, "data", pure.TAGS_FILE), encoding="utf-8"))
check("語の組の表は own と redraw", set(tags) == {"own", "redraw"}, list(tags))
bad = [(g, s, k, t) for g, table in tags.items() for s, keys in table.items() for k, t in keys.items()
       if pure.tags_text(pure.parse_tags(t)) != t or not t]
check("語の組は読めて同じ文字列に戻る", not bad, bad[:3])

# ---- 素の絵のパスと語の組の決まり ---------------------------------------------------
check("パス", pure.stock_key("Assets/images/item_candidates_dark/accessory/46.png") == ("accessory", "46"))
check("パス（\\ 区切り）", pure.stock_key("Assets\\images\\item_candidates_dark\\potion\\3.png") == ("potion", "3"))
check("写しは元の部位", pure.stock_key("Assets/images/item_candidates_dark/potion/xs.medicine.12.png") == ("medicine", "12"))
check("素の絵でないパス", pure.stock_key("C:/x/y.png") is None and pure.stock_key(None) is None)
check("色と材質", pure.tags_of("A rusted iron dagger with a teal gem.") == (
    frozenset({"orange", "brown", "green", "blue"}), frozenset({"metal"})), pure.tags_of("A rusted iron dagger with a teal gem."))
check("日本語の文は空", pure.tags_of("青い小瓶") == (frozenset(), frozenset()))
blue, orange = pure.tags_of("a pale blue vial"), pure.tags_of("an orange glass vial")
check("色が食い違えば引く", pure.tag_penalty(blue, orange, 0.05) == -0.05)
check("片方に語が無ければ引かない", pure.tag_penalty(blue, pure.tags_of("a vial"), 0.05) == 0.0)
check("色と材質が両方違えば2回", pure.tag_penalty(pure.tags_of("a red wooden box"), pure.tags_of("a blue glass box"), 0.05) == -0.1)


# ---- torch の代わり（本体が使う演算だけ） ----------------------------------------
def f32(x):
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def _map(a, fn):
    return [_map(v, fn) for v in a] if isinstance(a, list) else fn(a)


def _zip(a, b, fn):
    if isinstance(a, list):
        if not isinstance(b, list):
            return [_zip(x, b, fn) for x in a]
        if len(b) == 1:
            return [_zip(x, b[0], fn) for x in a]
        return [_zip(x, y, fn) for x, y in zip(a, b)]
    return fn(a, b)


def _dot(u, v):
    total = 0.0
    for x, y in zip(u, v):
        total = f32(total + f32(x * y))
    return total


class T:
    def __init__(self, a):
        self.a = _map(a, f32) if isinstance(a, list) else f32(a)

    dtype = "float32"

    def __matmul__(self, o):
        if isinstance(self.a[0], list):
            return T([_dot(row, o.a) for row in self.a])
        return T(_dot(self.a, o.a))

    def __add__(self, o):
        return T(_zip(self.a, o.a if isinstance(o, T) else o, lambda x, y: f32(x + y)))

    def __truediv__(self, o):
        return T(_zip(self.a, o.a if isinstance(o, T) else o, lambda x, y: f32(x / y)))

    def __getitem__(self, i):
        return T(self.a[i])

    def __float__(self):
        return float(self.a)

    def __int__(self):
        return int(self.a)

    def norm(self, dim=None, keepdim=False):
        if dim is None:
            return T(f32(_dot(self.a, self.a) ** 0.5))
        rows = [f32(_dot(row, row) ** 0.5) for row in self.a]
        return T([[r] for r in rows] if keepdim else rows)

    def clamp_min(self, v):
        return T(_map(self.a, lambda x: max(x, v)))

    def max(self, dim=0):
        i = max(range(len(self.a)), key=lambda k: (self.a[k], -k))
        return T(self.a[i]), T(i)

    def argsort(self, descending=False):
        order = sorted(range(len(self.a)), key=lambda k: -self.a[k] if descending else self.a[k])
        return types.SimpleNamespace(tolist=lambda: list(order))

    def detach(self):
        return self

    def reshape(self, *shape):
        return T(self.a)

    def to(self, dtype=None):
        return self


torch = types.ModuleType("torch")
torch.float32 = "float32"
torch.tensor = lambda data, dtype=None: T(data)
sys.modules["torch"] = torch

# ---- 偽のゲームと偽の 128 のフォルダ ------------------------------------------------
# 素の辞書: 1 は軸0、2 は軸1、3 は軸2。描き直しの辞書は 1 を軸3 に替える（描き直しの一文の埋め込みの代わり）
GAME = tempfile.mkdtemp()
FAKE_MOD = tempfile.mkdtemp()
os.makedirs(os.path.join(GAME, "Data", "item_embeddings"))
json.dump({"1": [1, 0, 0, 0, 0], "2": [0, 1, 0, 0, 0], "3": [0, 0, 1, 0, 0]},
          open(os.path.join(GAME, "Data", "item_embeddings", "tool.json"), "w"))
for key in ("1", "2", "3"):
    path = os.path.join(GAME, "Assets", "images", "item_candidates_dark", "tool", key + ".png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"stock")
for name in ("item_image_variety.py", "categories.json", "genres.json"):
    shutil.copy(os.path.join(MOD_DIR, name), FAKE_MOD)
os.makedirs(os.path.join(FAKE_MOD, "data", "redraw"))
json.dump({"index": {"1": 0}, "vectors": [[0, 0, 0, 1, 0]]}, open(os.path.join(FAKE_MOD, "data", "redraw", "tool.json"), "w"))
json.dump({"own": {"tool": {"2": "orange|glass"}}, "redraw": {"tool": {"1": "grey|metal"}}},
          open(os.path.join(FAKE_MOD, "data", "tags.json"), "w"))
REDRAWN = os.path.join(FAKE_MOD, "images", "redraw", "tool", "1.png")
os.makedirs(os.path.dirname(REDRAWN))
open(REDRAWN, "wb").write(b"redraw")
os.chdir(GAME)

QUERIES = {}
emb = types.ModuleType("Embedding.get_similar_id")
emb.text_to_embedding = lambda text: T(QUERIES[text])
emb.get_similar_embedding_id = lambda *a, **k: "orig"
sys.modules["Embedding.get_similar_id"] = emb
llm.wrap_outgoing = lambda *a, **k: None
main = sys.modules["__main__"]
main.instantale_app = types.SimpleNamespace(world_dict={"world_data": {"name": "描き直しの試し"}})
STATE = tempfile.mkdtemp()
OUT = tempfile.mkdtemp()


class FakeCtx:
    def __init__(self):
        self.mod_dir = FAKE_MOD
        self.hooks = {}
        self._mod = None

    def out_path(self, *parts):
        return os.path.join(OUT, *parts)

    def state_path(self, *parts):
        path = os.path.join(STATE, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        return ml.read_json(path, default)

    def write_json(self, path, data, **kw):
        return ml.write_json(path, data, **kw)

    def logger(self, name, **kw):
        return lambda msg: io.open(os.path.join(OUT, name), "a", encoding="utf-8").write(msg + "\n")

    def log(self, msg, level="INFO"):
        pass

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load(**settings):
    """本体を読み直して当てる。対応表（使用数）は毎回空から（世界を替える）。"""
    shutil.rmtree(os.path.join(STATE, "item_image"), ignore_errors=True)
    spec = importlib.util.spec_from_file_location("iiv_redraw_under_test", os.path.join(FAKE_MOD, "item_image_variety.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CROSS_SLOT = False
    for _name, setting in mod.IMAGE_SETS:
        setattr(mod, setting, "off")
    for name, value in settings.items():
        setattr(mod, name, value)
    ctx = FakeCtx()
    mod.apply(ctx)
    hook = ctx.hooks["Embedding.get_similar_id:get_similar_embedding_id"]
    return ctx, (lambda text, sub="tool": hook(emb.get_similar_embedding_id, text, sub))


def unit(*v):
    """長さ 1 に余りの次元（5つ目）で合わせた外見文。"""
    v = list(v) + [0.0] * (4 - len(v))
    return v + [max(0.0, 1.0 - sum(x * x for x in v)) ** 0.5]


# 描き直しの埋め込みで選ぶ: 軸3 に近い外見文は、描き直しを使えば 1、切れば素の 1 は似ないので 2
QUERIES["redraw-like"] = unit(0.0, 0.5, 0.0, 0.8)
_ctx, pick = load()
check("描き直しのある素の絵は描き直しの埋め込みで選ぶ", pick("redraw-like") == "1")
_ctx, pick = load(USE_REDRAW=False)
check("切れば素の埋め込みで選ぶ", pick("redraw-like") == "2")

# 候補の幅の上限: 2 が 0.60・3 が 0.57。幅 0.05 なら2つ目の外見文は使っていない 3、0.02 なら 2 だけ
QUERIES["near-a"] = unit(0.0, 0.60, 0.57)
QUERIES["near-b"] = unit(0.0, 0.60, 0.57)
_ctx, pick = load(SIM_GAP=0.05, TAG_PENALTY=0.0)
first, second = pick("near-a"), pick("near-b")
check("幅の内なら使っていない絵へ散らす", (first, second) == ("2", "3"), (first, second))
_ctx, pick = load(SIM_GAP=0.02, TAG_PENALTY=0.0)
first, second = pick("near-a"), pick("near-b")
check("幅の外の絵は使わない", (first, second) == ("2", "2"), (first, second))

# 色の食い違い: 2 は橙（own の語の組）。「青い」外見文では 2 が 0.05 下がって 3 が先に選ばれる
QUERIES["a pale blue vial"] = unit(0.0, 0.60, 0.57)
_ctx, pick = load(SIM_GAP=0.05, TAG_PENALTY=0.05)
check("色が食い違う絵を下げる", pick("a pale blue vial") == "3")
_ctx, pick = load(SIM_GAP=0.05, TAG_PENALTY=0.0)
check("0 なら下げない", pick("a pale blue vial") == "2")
QUERIES["a pale orange vial"] = unit(0.0, 0.60, 0.57)
_ctx, pick = load(SIM_GAP=0.05, TAG_PENALTY=0.05)
check("色が合う絵は下げない", pick("a pale orange vial") == "2")
QUERIES["a deep blue flask"] = unit(0.0, 0.60, 0.30)
_ctx, pick = load(SIM_GAP=0.05, TAG_PENALTY=0.05)
check("合う絵が無ければ下げた絵でも選ぶ", pick("a deep blue flask") == "2")
log = io.open(os.path.join(OUT, "item_image.log"), encoding="utf-8").read().splitlines()
line = next((l for l in log if "a deep blue flask" in l and " chose " in l), "")
check("下げた絵を選んだら記録に残す", " tag -0.05" in line, line)

# 品の絵を描く所: 描き直しのある素の絵だけ、描く部品に渡すパスを替える。品の image_src は変えない
for flag in (True, False):
    ctx, _pick = load(USE_REDRAW=flag)
    shown = ctx.hooks["scripts.hud.new_hud:InventoryItem.__init__"]
    drawn = []
    src = "Assets/images/item_candidates_dark/tool/1.png"
    item = types.SimpleNamespace(image_src=src)
    shown(lambda self, *a, **k: drawn.append(a[2] if len(a) > 2 else k.get("image_src")), object(), 1, 1, src, "common", "品", "i1", item, None)
    shown(lambda self, *a, **k: drawn.append(a[2] if len(a) > 2 else k.get("image_src")), object(), 1, 1, image_src=src)
    other = "Assets/images/item_candidates_dark/tool/2.png"
    shown(lambda self, *a, **k: drawn.append(a[2] if len(a) > 2 else k.get("image_src")), object(), 1, 1, other, "common", "品", "i2", item, None)
    cross = "Assets/images/item_candidates_dark/medicine/xs.tool.1.png"
    shown(lambda self, *a, **k: drawn.append(a[2] if len(a) > 2 else k.get("image_src")), object(), 1, 1, cross, "common", "品", "i3", item, None)
    if flag:
        check("描き直しを描く（位置引数）", drawn[0] == REDRAWN, drawn[0])
        check("描き直しを描く（キーワード）", drawn[1] == REDRAWN, drawn[1])
        check("描き直しの無い絵は素のまま", drawn[2] == other, drawn[2])
        check("部位をまたいだ写しは元の部位の描き直し", drawn[3] == REDRAWN, drawn[3])
    else:
        check("切れば素の絵を描く", drawn == [src, src, other, cross], drawn)
    check("品の image_src は素の絵のまま（{}）".format("描き直しあり" if flag else "切った"), item.image_src == src, item.image_src)

os.chdir(HERE)
for folder in (GAME, FAKE_MOD, STATE, OUT):
    shutil.rmtree(folder, ignore_errors=True)
print("")
print("%d failed" % len(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
