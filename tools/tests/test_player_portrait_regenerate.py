# -*- coding: utf-8 -*-
"""335_player_portrait_regenerate をゲーム抜きで通す。

    python tools/tests/test_player_portrait_regenerate.py

偽の app・HUD・create_look・画像生成関数を差し込み、次を確認する。

  英語の外見 … create_look の英語だけを使い、外見文が同じ間は呼び直さない。
               辞書・モデル・並びのどの戻りも読む。英語が取れなければ描かない
  付け替え   … 前の版のローダが作った Worker でも on_done を差し替える
  待ち合わせ … 他の立ち絵の生成中は待ち、待ちきれなければ取りやめる
  描き直し   … 選ばれている方式の関数へ、英語の要素を配列で渡す。装備が無くても描く
  ボタン     … 立ち絵の描かれている矩形の左上に置き、立ち絵に付いていく。
               主人公の立ち絵が出ているときだけ見せる。描いている間は隠す
"""
import importlib.util
import io
import json
import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_player_portrait_regenerate")


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        print("  FAIL  {}  {!r}".format(label, detail))
        failures.append(label)


class FakeStore(object):
    def __init__(self):
        self.buckets = {}

    def playthrough(self, app=None):
        return "w|p"

    def load(self, key):
        return self.buckets.setdefault(key, {})

    def save(self, key):
        return True


logs = []
write = logs.append
mod = load_module("player_portrait_under_test")

# ---------------------------------------------------------------- 英語の外見
print("=== 英語の外見 ===")
check("日本語は落ちる", mod._sd_item("黒髪") == "")
check("括弧は落ちる", mod._sd_item("(red eyes:1.2)") == "")
check("大文字は小文字に", mod._sd_items(["Pale Skin, Short Bob"]) == ["pale skin", "short bob"])
calls = []
fake = types.ModuleType(mod.CREATE_LOOK_MODULE)


def create_look(name, gender, profile, look):
    calls.append((name, gender, profile, look))
    return {"category": "young woman",
            "image_generation_prompt": ["Pale skin", "黒髪", "short bob cut"]}


fake.create_look = create_look
sys.modules[mod.CREATE_LOOK_MODULE] = fake
player = {"name": "主", "category": "young woman", "profile": "出自",
          "look_description": "【外見】黒髪"}
store = FakeStore()
w1 = mod._player_look(store, write, None, player)
w2 = mod._player_look(store, write, None, player)
check("create_look の英語だけ使う", w1 == ["pale skin", "short bob cut"], w1)
check("外見文が同じ間は呼び直さない", len(calls) == 1 and w2 == w1)
check("控えは look に置く", store.buckets["w|p"]["look"]["prompts"] == w1, store.buckets)
check("性別は category から", calls[0][1] == "♀", calls)
check("gender があればそれを使う", mod._gender_of({"gender": "male", "category": "young woman"}) == "male")
player["look_description"] = "【外見】変わった"
mod._player_look(store, write, None, player)
check("外見文が変わると呼び直す", len(calls) == 2)
fake.create_look = lambda *a: ["young man", "scar"]
check("配列の戻りも読む",
      mod._player_look(FakeStore(), write, None, dict(player, look_description="x"))
      == ["young man", "scar"])


class Model(object):
    def model_dump(self):
        return {"image_generation_prompt": ["iron mask"]}


fake.create_look = lambda *a: Model()
check("モデルの戻りも読む",
      mod._player_look(FakeStore(), write, None, dict(player, look_description="y"))
      == ["iron mask"])
fake.create_look = lambda *a: {"image_generation_prompt": ["全部日本語"]}
check("英語が取れなければ None",
      mod._player_look(FakeStore(), write, None, dict(player, look_description="z")) is None)
check("外見文が無ければ None",
      mod._player_look(FakeStore(), write, None, dict(player, look_description="")) is None)
del sys.modules[mod.CREATE_LOOK_MODULE]
check("create_look が無ければ None",
      mod._player_look(FakeStore(), write, None, dict(player, look_description="w")) is None)

# ---------------------------------------------------------------- 付け替え
print("=== 付け替え ===")


class OldWorker(object):
    """前の版のローダの Worker。rebind が on_done を受けない。"""

    def __init__(self):
        self.on_done = "old"

    def rebind(self, ctx, run=None, write=None):
        self.run = run
        return self


old_worker = OldWorker()
setattr(sys, mod.WORKER_ATTR, old_worker)
new_done = lambda job: None
got = mod._worker(None, "run", None, new_done)
check("前の版の Worker でも on_done を差し替える",
      got is old_worker and old_worker.on_done is new_done and old_worker.run == "run")
setattr(sys, mod.WORKER_ATTR, None)

# ---------------------------------------------------------------- 待ち合わせ
print("=== 待ち合わせ ===")
rt = {"lock": threading.Lock(), "inflight": 1, "busy": False}
check("生成中なら待ちきれずに False", mod._wait_idle(rt, timeout=0.2, poll=0.05) is False)
with mod._InFlight(rt):
    pass
check("数え戻す", rt["inflight"] == 1)
rt["inflight"] = 0
check("空いていれば True", mod._wait_idle(rt, timeout=0.2, poll=0.05) is True)

print("=== 方式 ===")
check("選ばれた方式のモジュール",
      mod.module_names("sdcpp_cpu") == ["image_generation.sdcppcpu.image_generation_creature"])
check("分からなければ4つとも", len(mod.module_names(None)) == 4)

# ---------------------------------------------------------------- 描き直し
print("=== 描き直し ===")
mod = load_module("player_portrait_under_test_hooks")
setattr(sys, mod.RUNTIME_ATTR, None)        # 前の読み込みの数えを持ち越さない
del logs[:]


class Ctx(object):
    def __init__(self):
        self.wraps = {}

    def logger(self, *args, **kwargs):
        return logs.append

    def log(self, message):
        logs.append(message)

    def log_exc(self, message):
        logs.append("EXC " + message)

    def wrap(self, target, **kwargs):
        def decorator(fn):
            self.wraps[target] = fn
            return fn
        return decorator


hook_store = FakeStore()
mod._store = lambda ctx, write: hook_store
captured = {}


def fake_worker(ctx, run, write, done):
    captured.update(run=run, done=done)
    return types.SimpleNamespace(enqueue=lambda job: captured.setdefault("jobs", []).append(job) or True)


mod._worker = fake_worker
mod.config_backend = lambda path=None: "sdcpp_cuda"
hero = types.SimpleNamespace(name="主", category="young woman", profile="出自",
                             look_description="【外見】黒髪", equipments={})
app = types.SimpleNamespace(player=hero, world=types.SimpleNamespace(name="W"), world_dict=None)
mod.ui.find_app = lambda: app

ctx = Ctx()
mod.apply(ctx)
target = "image_generation.sdcppcuda.image_generation_creature:generate_character_image"
check("選ばれた方式の関数を包む（生成中の数え）",
      target in ctx.wraps and len([t for t in ctx.wraps if "image_generation" in t]) == 1,
      sorted(ctx.wraps))
count_hook = ctx.wraps[target]
seen = []


def orig(*args, **kwargs):
    seen.append((args, kwargs, mod._runtime()["inflight"]))
    return ("big", "small")


look_module = types.ModuleType(mod.CREATE_LOOK_MODULE)
look_module.create_look = lambda *a: {"image_generation_prompt": ["pale skin", "short bob cut"]}
sys.modules[mod.CREATE_LOOK_MODULE] = look_module
image_module = types.ModuleType("image_generation.sdcppcuda.image_generation_creature")
image_module.generate_character_image = lambda *a, **k: count_hook(orig, *a, **k)
sys.modules[image_module.__name__] = image_module

captured["run"]({"app": app, "key": "k"})
check("英語の要素を配列で渡す（装備が無くても描く）",
      seen and seen[-1][0] == ("W", "主", "young woman", ["pale skin", "short bob cut"]), seen)
check("描いている間は生成中に数える", seen and seen[-1][2] == 1, seen)
check("描き終えたら数え戻す", mod._runtime()["inflight"] == 0)
count = len(seen)
look_module.create_look = lambda *a: {"image_generation_prompt": ["日本語"]}
hook_store.buckets.clear()
captured["run"]({"app": app, "key": "k"})
check("英語が取れなければ描かない", len(seen) == count)
del sys.modules[image_module.__name__]
look_module.create_look = lambda *a: {"image_generation_prompt": ["pale skin"]}
captured["run"]({"app": app, "key": "k"})
check("描く関数が無ければ描かない", len(seen) == count
      and any("image module unavailable" in str(line) for line in logs))

# ---------------------------------------------------------------- ボタン
print("=== ボタン ===")
mine = "C:\\worlds\\W\\characters\\主\\reduced_color_image.png"
other = "C:\\worlds\\W\\characters\\甲\\reduced_color_image.png"
# 実測（212 の dump）: 窓 1920x1027 で widget 1920x825 が pos (595.2, 255.05)。
# 絵は 640x1216 なので、高さ 825 に合わせて幅 434 で中央に描かれる。
Image = types.SimpleNamespace
wide = Image(x=595.2, y=255.05, width=1920.0, height=825.0,
             norm_image_size=(434.2, 825.0), opacity=1, source=mine)
rect = mod._portrait_rect(wide)
check("描かれている矩形は widget の中央",
      rect is not None and abs(rect[0] - (595.2 + (1920 - 434.2) / 2)) < 0.01
      and abs(rect[2] - 434.2) < 0.01, rect)
by_texture = Image(x=0.0, y=0.0, width=1000.0, height=500.0, texture_size=(640, 1216))
rect = mod._portrait_rect(by_texture)
check("norm_image_size が無ければ texture から出す",
      rect is not None and abs(rect[3] - 500.0) < 0.01
      and abs(rect[2] - 500.0 * 640 / 1216) < 0.01, rect)
check("寸法が無ければ None", mod._portrait_rect(Image(x=0, y=0, width=0, height=0)) is None)
check("主人公の立ち絵なら出す", mod._portrait_shows_player(app, wide))
check("隠れていれば出さない", not mod._portrait_shows_player(app, Image(opacity=0, source=mine)))
check("他人の立ち絵なら出さない", not mod._portrait_shows_player(app, Image(opacity=1, source=other)))
check("フォルダ名の一致で見る",
      mod._shows_character("C:/x/characters/主/face_image.png", "主")
      and not mod._shows_character("C:/x/characters/主人/face_image.png", "主")
      and not mod._shows_character(None, "主"))


class FakeButton(object):
    def __init__(self, **kwargs):
        self.pos_hint = dict(kwargs.get("pos_hint") or {})
        self.height = self.width = 30.0
        self.x = self.y = 0.0
        self.opacity, self.disabled = 1.0, False
        self.parent = None
        self.bound = {}

    @property
    def pos(self):
        return (self.x, self.y)

    @pos.setter
    def pos(self, value):
        self.x, self.y = value

    def bind(self, **events):
        for name, fn in events.items():
            self.bound.setdefault(name, []).append(fn)

    def unbind(self, **events):
        for name, fn in events.items():
            if fn in self.bound.get(name, []):
                self.bound[name].remove(fn)

    def fire(self, name):
        for fn in list(self.bound.get(name, [])):
            fn()


class FakeHost(object):
    def __init__(self):
        self.children = []

    def add_widget(self, widget):
        widget.parent = self
        self.children.insert(0, widget)

    def remove_widget(self, widget):
        self.children.remove(widget)
        widget.parent = None


portrait = FakeButton()
portrait.x, portrait.y, portrait.width, portrait.height = 595.2, 255.05, 1920.0, 825.0
portrait.norm_image_size, portrait.opacity, portrait.source = (434.2, 825.0), 1, mine
host = FakeHost()
sheet_hud = types.SimpleNamespace(character_image_right=portrait)
mod.ui.make_icon_button = lambda **kwargs: FakeButton(**kwargs)
mod.ui.overlay_host = lambda hud: host
mod.ui.find_hud = lambda app: sheet_hud
mod.ui.paint_icon = lambda *a, **k: None
mod.ui.upx = lambda value: float(value)
mod.ui.window_size = lambda: (1920.0, 1027.0)
mod.ui.show_widget = lambda widget, visible: setattr(widget, "opacity", 1.0 if visible else 0.0)
mod._runtime()["busy"] = False
toggle = ctx.wraps["scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility"]
toggle(lambda self: None, sheet_hud)
button = getattr(sheet_hud, mod.BUTTON_ATTR, None)
left = 595.2 + (1920 - 434.2) / 2
check("ボタンは overlay_host に1枚", button is not None and host.children == [button])
check("立ち絵の左上に置く（上端は窓で切る）",
      button is not None and abs(button.x - (left + 6)) < 0.01
      and abs(button.y - (1027 - 30 - 6)) < 0.01, button and button.pos)
check("主人公の立ち絵なら見せる", button is not None and button.opacity == 1.0)
toggle(lambda self: None, sheet_hud)
check("開閉を繰り返しても1枚", host.children == [button])
check("追従の結びは1本", len(portrait.bound.get("pos", [])) == 1, portrait.bound.get("pos"))
portrait.opacity = 0
portrait.fire("opacity")
check("立ち絵が隠れたら隠す", button.opacity == 0.0)
portrait.opacity = 1
portrait.x = 0.0
portrait.fire("pos")
check("立ち絵が動けば付いていく", abs(button.x - ((1920 - 434.2) / 2 + 6)) < 0.01, button.pos)
check("見えている", button.opacity == 1.0)

button.fire("on_release")
check("押すと仕事を積み、描いている間は隠す",
      captured.get("jobs") and mod._runtime()["busy"] and button.opacity == 0.0,
      (captured.get("jobs"), mod._runtime()["busy"], button.opacity))
portrait.fire("pos")
check("描いている間は置き直しても隠れたまま", button.opacity == 0.0)
captured["done"]({"app": app})
check("描き終えたら戻す", not mod._runtime()["busy"] and button.opacity == 1.0)
check("例外なし", not any(str(line).startswith("EXC") for line in logs),
      [line for line in logs if str(line).startswith("EXC")])

sys.modules.pop(mod.CREATE_LOOK_MODULE, None)
print("\n{} failure(s)".format(len(failures)))
sys.exit(1 if failures else 0)
