# -*- coding: utf-8 -*-
"""128_item_image_variety の「部位をまたいで探す」をゲーム抜きで通す。

    python tools/tests/test_item_image_cross.py

偽のゲームのフォルダ（3次元の小さな埋め込みと絵）をカレントにして、本体の選定の関数を呼ぶ。
torch は開発機に無いので、本体が使う分だけ素の Python で真似る（CI のランナーには numpy も無い）。
見るのは、自分の部位の最良が低く、ほかの 32x32 の部位に決まりを満たす絵があるときだけ `xs.<部位>.<鍵>` を返して写しを置くこと・
決まり（0.55 未満・0.65 以上・差 0.15 以上）の境目・装備品の部位はまたがないこと・
一度付いた絵は写しが消えても置き直して同じ絵を返し、設定を切っても変わらないこと・
元の絵が無ければ自分の部位に戻ること・世界を読み直すと写しを置き直すこと。
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


# ---- torch の代わり（本体が使う演算だけ） ----------------------------------------
# CI のランナーには numpy も無いので素の Python で書く。値は演算のたびに float32 へ丸める
# （本体は float32 の行列で比べる。決まりの境目の検査が 64 ビットの丸めで変わらないように）。
def f32(x):
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def _map(a, fn):
    return [_map(v, fn) for v in a] if isinstance(a, list) else fn(a)


def _zip(a, b, fn):
    """要素ごとの演算。b が数か長さ 1 の段なら広げる（numpy の broadcast のうち本体が使う形だけ）。"""
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

# ---- 偽のゲーム ------------------------------------------------------------------
GAME = tempfile.mkdtemp()
# 4つ目の次元は絵の長さ合わせ、5つ目は外見文の長さ合わせ（どちらも互いに重ならない）。
# 外見文 (a, b, c) の類似度: 道具 1 = a・道具 2 = 0.9a、薬 5 = c・薬 6 = 0.8c、遺物 7 = b、巻物 3 = 0.7b
EMB = {
    "tool": {"1": [1, 0, 0, 0, 0], "2": [0.9, 0, 0, 0.436, 0]},
    "medicine": {"5": [0, 0, 1, 0, 0], "6": [0, 0, 0.8, 0.6, 0]},
    "relic": {"7": [0, 1, 0, 0, 0]},
    "small_weapon": {"9": [1, 0, 0, 0, 0]},
    "scroll": {"3": [0, 0.7, 0, 0.714, 0]},
}
for sub, table in EMB.items():
    path = os.path.join(GAME, "Data", "item_embeddings")
    os.makedirs(path, exist_ok=True)
    json.dump(table, open(os.path.join(path, sub + ".json"), "w"))
    img = os.path.join(GAME, "Assets", "images", "item_candidates_dark", sub)
    os.makedirs(img, exist_ok=True)
    for key in table:
        if (sub, key) != ("relic", "7"):        # relic の 7 は絵が無い（元の絵が無いときの戻り）
            open(os.path.join(img, key + ".png"), "wb").write(("%s/%s" % (sub, key)).encode())
os.chdir(GAME)

QUERIES = {}


def unit(a, b, c):
    """類似度が道具 a・遺物 b・薬 c になる外見文（長さ 1 に余りの次元で合わせる）。"""
    return [a, b, c, 0, (1.0 - a * a - b * b - c * c) ** 0.5]


emb = types.ModuleType("Embedding.get_similar_id")
emb.text_to_embedding = lambda text: T(QUERIES[text])
emb.get_similar_embedding_id = lambda *a, **k: "orig"
sys.modules["Embedding.get_similar_id"] = emb
llm.wrap_outgoing = lambda *a, **k: None
main = sys.modules["__main__"]
main.instantale_app = types.SimpleNamespace(world_dict={"world_data": {"name": "試しの世界"}})

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


STATE = tempfile.mkdtemp()
OUT = tempfile.mkdtemp()


class FakeCtx:
    def __init__(self):
        self.mod_dir = MOD_DIR
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


def load(cross=True):
    """本体を読み直して当てる（注入し直しと同じ）。追加の絵の分類は全部切り、偽のゲームの辞書だけで選ぶ。"""
    spec = importlib.util.spec_from_file_location("iiv_cross_under_test", os.path.join(MOD_DIR, "item_image_variety.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.USE_EXTRA_DICT = False
    mod.CROSS_SLOT = cross
    for _name, setting in mod.IMAGE_SETS:
        setattr(mod, setting, "off")
    ctx = FakeCtx()
    mod.apply(ctx)
    hook = ctx.hooks["Embedding.get_similar_id:get_similar_embedding_id"]
    return mod, (lambda text, sub: hook(emb.get_similar_embedding_id, text, sub))


def copy_of(sub, key):
    return os.path.join(STATE, "item_image_cross", "Assets", "images", "item_candidates_dark", sub, key + ".png")


mod, pick = load()
# 包帯が道具で来た: 道具の最良 0.2、薬の 5 が 0.95 → 薬の絵を写す
QUERIES["bandage"] = unit(0.2, 0, 0.95)
key = pick("bandage", "tool")
check("低い部位はほかの部位の絵を返す", key == "xs.medicine.5", key)
check("写しを頼まれた部位のフォルダに置く", os.path.isfile(copy_of("tool", "xs.medicine.5")))
check("写しの中身は元の絵", open(copy_of("tool", "xs.medicine.5"), "rb").read() == b"medicine/5")
# 同じ外見文は写しが消えても置き直して同じ絵
os.remove(copy_of("tool", "xs.medicine.5"))
check("一度付いた絵は同じ絵", pick("bandage", "tool") == "xs.medicine.5")
check("消えた写しを置き直す", os.path.isfile(copy_of("tool", "xs.medicine.5")))

# 自分の部位の最良が 0.55 以上ならまたがない（道具 0.56・薬 0.80）
QUERIES["wrench"] = unit(0.56, 0, 0.80)
check("自分が 0.55 以上ならまたがない", pick("wrench", "tool") in ("1", "2"))
# 0.55 未満で差も 0.15 以上だが、最良が 0.65 に届かない（道具 0.45・薬 0.64）
QUERIES["low"] = unit(0.45, 0, 0.64)
check("最良が 0.65 に届かなければまたがない", pick("low", "tool") in ("1", "2"))
# 0.55 未満で最良も 0.65 以上だが、差が 0.15 に届かない（道具 0.54・薬 0.68）
QUERIES["margin"] = unit(0.54, 0, 0.68)
check("差が 0.15 に届かなければまたがない", pick("margin", "tool") in ("1", "2"))
# 境目の内側（道具 0.50・薬 0.66）はまたぐ
QUERIES["edge"] = unit(0.50, 0, 0.66)
check("決まりを満たせばまたぐ（0.50→0.66）", pick("edge", "tool").startswith("xs.medicine."))
# 装備品の部位はまたがない
QUERIES["blade-bandage"] = unit(0.2, 0, 0.95)
check("装備品の部位はまたがない", pick("blade-bandage", "small_weapon") == "9")
# 最良の部位に元の絵が無ければ自分の部位に戻る（遺物 0.95 は絵が無い、巻物は 0.67）
QUERIES["tablet"] = unit(0.1, 0.95, 0)
key = pick("tablet", "tool")
check("元の絵が無ければ自分の部位に戻る", key in ("1", "2"), key)
check("戻ったことをログに残す", "cross: no image for xs.relic.7" in io.open(os.path.join(OUT, "item_image.log"), encoding="utf-8").read())

# 設定を切ると新しい外見文はまたがず、一度付いた絵はそのまま
mod, pick = load(cross=False)
QUERIES["bandage2"] = unit(0.2, 0, 0.95)
check("切ると新しい外見文はまたがない", pick("bandage2", "tool") in ("1", "2"))
check("切っても一度付いた絵はそのまま", pick("bandage", "tool") == "xs.medicine.5")

# 世界を読み直すと写しを置き直す（ロードしたセーブの品は選定を通らずに絵を引く）
os.remove(copy_of("tool", "xs.medicine.5"))
mod, pick = load()
QUERIES["other"] = unit(1, 0, 0)
pick("other", "tool")
check("世界を読み直すと写しを置き直す", os.path.isfile(copy_of("tool", "xs.medicine.5")))
table = json.load(io.open(os.path.join(STATE, "item_image", os.listdir(os.path.join(STATE, "item_image"))[0]), encoding="utf-8"))
check("対応表に頼まれた部位で残る", table["images"].get("tool/bandage") == "xs.medicine.5", table)

shutil.rmtree(GAME, ignore_errors=True)
print("")
print("%d failed" % len(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
