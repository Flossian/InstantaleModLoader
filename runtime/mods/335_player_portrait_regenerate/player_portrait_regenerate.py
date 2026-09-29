# -*- coding: utf-8 -*-
"""人物欄の主人公の立ち絵の左上にボタンを置き、主人公の立ち絵を描き直す（DOC.md）。

主人公は英語の外見プロンプトをセーブに持たない（日本語の look_description だけ）。
本体が主人公作成時に使う ``create_look`` で英語の要素を作ってから描く
（DOC.md「英語の外見」）。
描き終えた絵を画面へ出し直すのは 138_fix_character_image_refresh。
装備の見た目を足すのは 408_visual_changes_based_on_equipment（入っていれば）。
"""

import hashlib
import io
import json
import os
import re
import sys
import threading
import time

from instantale_modloader import frames, jobs, llm, saves, state, ui


CREATE_LOOK_MODULE = "scripts.llm.llm_manager_character_create"
STORE_ATTR = "_instantale_335_player_portrait_store"
WORKER_ATTR = "_instantale_335_player_portrait_worker"
RUNTIME_ATTR = "_instantale_335_player_portrait_runtime"
STORE_DIRNAME = "player_portrait"
LOG_BASENAME = "player_portrait.log"

# 人物の絵を描く関数は画像生成の方式ごとに別のモジュールに居る（GAME.md §2.33）。
# ゲームは config.json で選ばれた1つだけを import する。
FAMILIES = {
    "sdcpp_cuda": "sdcppcuda",
    "sdcpp_vulkan": "sdcppvulkan",
    "sdcpp_cpu": "sdcppcpu",
    "diffusers_openvino": "diffusers_openvino",
}
MODULE_FORMAT = "image_generation.{}.image_generation_creature"
FUNC = "generate_character_image"

# SD1.5 へ渡す1要素の形。英小文字・数字・空白・ハイフン・アポストロフィだけ。
# 括弧は SD の強調記法になるので通さない。
SD_ITEM = re.compile(r"^[a-z0-9][a-z0-9 '-]*$")
MAX_ITEM_WORDS = 4
MAX_ITEM_CHARS = 40

# 他の立ち絵の生成が終わるまでこの秒数だけ待つ。
IDLE_WAIT = 30.0
IDLE_POLL = 0.5

# ボタン。人物欄を開いたときに出る主人公の立ち絵の左上に置き、立ち絵に追従させる。
# 置き場所は HUD の overlay_host（HUD 直下に足すと本体の操作が壊れる。ui.overlay_host）。
BUTTON_ATTR = ui.MOD_WIDGET_PREFIX + "335_player_portrait"
BUTTON_ICON_ATTR = BUTTON_ATTR + "_icon"
BUTTON_CALLBACK_ATTR = BUTTON_ATTR + "_callback"
BUTTON_PAINT_ATTR = BUTTON_ATTR + "_paint"
BUTTON_WATCH_ATTR = BUTTON_ATTR + "_watch"
BUTTON_SIZE = 24.0
# 立ち絵の左上の角からの間（upx）。
BUTTON_INSET = 6.0
PORTRAIT_WIDGET = "character_image_right"
# 立ち絵のこれが変わったら置き直す（窓の大きさ・絵の差し替え・開閉）。
PORTRAIT_EVENTS = ("pos", "size", "texture", "opacity", "source")
# 絵柄は人の胸像（頭と肩）と、右上のやり直しの矢印（円弧＋矢尻）。
# 二つ名の引き直し（121 のサイコロ）と並んでも、立ち絵を描き直すボタンと読めるようにする。
BUTTON_STROKES = [
    [(0.48, 0.58), (0.47, 0.63), (0.44, 0.68), (0.39, 0.71), (0.34, 0.72),
     (0.29, 0.71), (0.24, 0.68), (0.21, 0.63), (0.20, 0.58), (0.21, 0.53),
     (0.24, 0.48), (0.29, 0.45), (0.34, 0.44), (0.39, 0.45), (0.44, 0.48),
     (0.47, 0.53), (0.48, 0.58)],
    [(0.08, 0.10), (0.11, 0.23), (0.20, 0.33), (0.34, 0.36), (0.48, 0.33),
     (0.57, 0.23), (0.60, 0.10)],
    [(0.55, 0.64), (0.54, 0.70), (0.56, 0.77), (0.59, 0.83), (0.65, 0.86),
     (0.71, 0.88), (0.78, 0.87), (0.84, 0.83), (0.88, 0.78), (0.90, 0.72),
     (0.89, 0.65), (0.86, 0.59), (0.81, 0.54)],
    [(0.81, 0.54), (0.92, 0.54)],
    [(0.81, 0.54), (0.86, 0.64)],
]


def _store(ctx, write):
    """注入し直しを越えて同じ WorldStore を使う。"""
    found = getattr(sys, STORE_ATTR, None)
    if found is None:
        found = state.WorldStore(ctx, STORE_DIRNAME, write=write)
        setattr(sys, STORE_ATTR, found)
    else:
        found.rebind(ctx, write=write)
    return found


def _worker(ctx, run, write, done):
    """描き直しの背景処理を世代を越えて1本にする。"""
    found = getattr(sys, WORKER_ATTR, None)
    if found is None:
        found = jobs.Worker(
            ctx, run, name="335_player_portrait", write=write,
            key=lambda job: job.get("key") if isinstance(job, dict) else None,
            max_pending=2, on_done=done)
        setattr(sys, WORKER_ATTR, found)
    else:
        jobs.rebind(found, ctx, run, write, on_done=done)
    return found


def _runtime():
    """生成中の本数と、描き直しの最中か。プロセス中だけ持つ。"""
    found = getattr(sys, RUNTIME_ATTR, None)
    if isinstance(found, dict) and "inflight" in found:
        return found
    found = {"lock": threading.Lock(), "inflight": 0, "busy": False}
    setattr(sys, RUNTIME_ATTR, found)
    return found


class _InFlight(object):
    """人物の絵を描く関数を通っている本数を数える。"""

    def __init__(self, runtime):
        self.runtime = runtime

    def __enter__(self):
        with self.runtime["lock"]:
            self.runtime["inflight"] += 1
        return self

    def __exit__(self, *_exc):
        with self.runtime["lock"]:
            self.runtime["inflight"] = max(0, self.runtime["inflight"] - 1)
        return False


def _wait_idle(runtime, timeout=IDLE_WAIT, poll=IDLE_POLL):
    """他の立ち絵の生成が終わるまで待つ。待ちきれなければ False。"""
    deadline = time.monotonic() + timeout
    while runtime["inflight"] > 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True


def _value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    value = frames.attr(obj, name, default)
    return default if value is frames.MISSING else value


def _text(obj, name):
    value = _value(obj, name, "")
    return value.strip() if isinstance(value, str) else ""


def config_backend(path=None):
    """ゲームが選んでいる画像生成の方式（`sd_backend.name`）。読めなければ None。"""
    path = path or os.path.join(saves.data_dir(), "config.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data["ai_setting"]["local_model_setting"]["sd_backend"]["name"]
    except Exception:
        return None


def module_names(backend):
    """人物の絵を描くモジュールの名前。方式が分からなければ4つとも。"""
    family = FAMILIES.get(backend)
    families = [family] if family else list(FAMILIES.values())
    return [MODULE_FORMAT.format(family) for family in families]


def _generator(names):
    """読み込まれている方の描く関数（ローダの包みが付いたもの）。無ければ None。"""
    for name in names:
        found = getattr(sys.modules.get(name), FUNC, None)
        if callable(found):
            return found
    return None


def _sd_item(value):
    """SD1.5 へ渡せる1要素に均す。渡せなければ空文字。"""
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).strip(" .;:\"'").lower()
    if not value or len(value) > MAX_ITEM_CHARS:
        return ""
    if not SD_ITEM.match(value) or len(value.split()) > MAX_ITEM_WORDS:
        return ""
    return value


def _sd_items(values):
    """要素の並びを SD1.5 へ渡せるものだけにする。カンマと改行で要素に割る。"""
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        for piece in re.split(r"[,\n]", value):
            item = _sd_item(piece)
            if item and item not in result:
                result.append(item)
    return result


def _gender_of(player):
    """create_look の性別。本体は作成時に ♂ / ♀ を渡していた。

    主人公のセーブに性別の項目は無いので、無ければ category から決める。
    """
    gender = _text(player, "gender")
    if gender:
        return gender
    category = _text(player, "category").lower()
    return "♀" if ("woman" in category or "girl" in category) else "♂"


def _look_words(got):
    """create_look の戻りから英語の要素を取る。辞書・モデル・並びのどれでも読む。"""
    if isinstance(got, (list, tuple)):
        return _sd_items(list(got))
    data = llm.as_dict(got)
    if isinstance(data, dict):
        return _sd_items(data.get("image_generation_prompt"))
    return []


def _player_look(store, write, app, player):
    """主人公の英語の外見プロンプト。外見文が同じ間は控えを使う。

    look_description（日本語）をそのまま SD1.5 へ渡さない。
    作れなければ None（描き直しは止める）。
    """
    desc = _text(player, "look_description")
    if not desc:
        write("skipped: no look_description")
        return None
    source = hashlib.sha256(desc.encode("utf-8")).hexdigest()
    playthrough = store.playthrough(app)
    bucket = store.load(playthrough)
    record = bucket.get("look")
    if isinstance(record, dict) and record.get("source") == source:
        words = _sd_items(record.get("prompts"))
        if words:
            return words

    module = sys.modules.get(CREATE_LOOK_MODULE)
    create = getattr(module, "create_look", None)
    if not callable(create):
        write("skipped: create_look unavailable")
        return None
    got = create(_text(player, "name"), _gender_of(player),
                 _text(player, "profile"), desc)
    words = _look_words(got)
    if not words:
        write("skipped: create_look returned {!r}".format(repr(got)[:200]))
        return None
    bucket["look"] = {"source": source, "prompts": list(words)}
    store.save(playthrough)
    write("look prepared ({} item(s))".format(len(words)))
    return words


def _shows_character(source, name):
    """その立ち絵の source が、この人物の画像フォルダの中を指しているか。"""
    if not isinstance(source, str) or not name:
        return False
    parts = re.split(r"[\\/]", source)
    return len(parts) >= 3 and parts[-2] == name and parts[-3] == "characters"


def _portrait_rect(image):
    """立ち絵が実際に描かれている矩形 `(x, y, 幅, 高さ)`。読めなければ None。

    Image は絵を縦横比を保ったまま widget の中央に描くので、widget の矩形とは違う
    （人物欄の立ち絵は窓の幅いっぱいの widget の中央に描かれる）。
    """
    try:
        x, y = float(image.x), float(image.y)
        width, height = float(image.width), float(image.height)
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    norm = frames.attr(image, "norm_image_size", None)
    try:
        drawn_w, drawn_h = float(norm[0]), float(norm[1])
    except (TypeError, ValueError, IndexError):
        texture = frames.attr(image, "texture_size", None)
        try:
            texture_w, texture_h = float(texture[0]), float(texture[1])
        except (TypeError, ValueError, IndexError):
            return None
        if texture_w <= 0 or texture_h <= 0:
            return None
        scale = min(width / texture_w, height / texture_h)
        drawn_w, drawn_h = texture_w * scale, texture_h * scale
    if drawn_w <= 0 or drawn_h <= 0:
        return None
    return (x + (width - drawn_w) / 2.0, y + (height - drawn_h) / 2.0, drawn_w, drawn_h)


def _portrait_shows_player(app, image):
    """立ち絵が出ていて、それが主人公の絵か。"""
    try:
        opacity = float(_value(image, "opacity", 0) or 0)
    except (TypeError, ValueError):
        return False
    name = _text(_value(app, "player"), "name")
    return opacity > 0 and _shows_character(_value(image, "source"), name)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="player portrait:")
    schedule = ui.scheduler(ctx, "player portrait")
    store = _store(ctx, write)
    runtime = _runtime()
    # 注入し直したら下ろす。立ったままだとボタンが隠れて押せなくなる。
    # 走っている1件は付け替えた on_done で終わり、同じ仕事は Worker の鍵で二重に積まれない。
    runtime["busy"] = False
    names = module_names(config_backend())

    def count_inflight(target):
        @ctx.wrap(target, required=False, safe=True)
        def generate_character_image(orig, *args, **kwargs):
            # 他の立ち絵（会話を始めたときの NPC など）と同時に描かないための数え。
            with _InFlight(runtime):
                return orig(*args, **kwargs)

    for name in names:
        count_inflight("{}:{}".format(name, FUNC))

    def finish_job(job):
        app = job.get("app") if isinstance(job, dict) else None
        if app is None:
            return

        def refresh():
            # 描いた絵を画面へ出し直すのは 138。ここはボタンを戻すだけ。
            runtime["busy"] = False
            hud = ui.find_hud(app)
            if hud is not None:
                place_button(hud)

        schedule(refresh)

    def run_job(job):
        app = job.get("app")
        player = _value(app, "player")
        if app is None or player is None:
            return
        generator = _generator(names)
        if generator is None:
            write("skipped: image module unavailable")
            return
        words = _player_look(store, write, app, player)
        if not words:
            return
        if not _wait_idle(runtime):
            write("skipped: another portrait is still generating")
            return
        # 本体は並びを「, 」で繋ぐ（実測。408 の native prompt type: list）。
        generator(state.world_key(app), _text(player, "name"),
                  _text(player, "category") or "character", list(words))
        write("regenerated {}".format(_text(player, "name")))

    worker = _worker(ctx, run_job, write, finish_job)

    def place_button(hud, button=None):
        """立ち絵の左上へ置き、主人公の立ち絵が出ているときだけ見せる。"""
        if button is None:
            button = getattr(hud, BUTTON_ATTR, None)
        if button is None:
            return
        app = ui.find_app()
        image = _value(hud, PORTRAIT_WIDGET)
        rect = _portrait_rect(image) if image is not None else None
        if rect is not None:
            x, y, width, height = rect
            inset = ui.upx(BUTTON_INSET)
            _window_w, window_h = ui.window_size()
            # 立ち絵は窓の上へはみ出して描かれることがあるので、上端は窓で切る。
            top = min(y + height, window_h) if window_h else y + height
            try:
                button.pos_hint = {}
                button.pos = (x + inset, top - button.height - inset)
            except Exception:
                ctx.log_exc("player portrait: could not place the button")
            ui.clamp_into_window(button)
        visible = (rect is not None and app is not None and not runtime["busy"]
                   and _portrait_shows_player(app, image))
        ui.show_widget(button, visible)

    def request(button):
        app = ui.find_app()
        hud = ui.find_hud(app) if app is not None else None
        if app is None or hud is None or runtime["busy"]:
            return
        if not _portrait_shows_player(app, _value(hud, PORTRAIT_WIDGET)):
            return
        runtime["busy"] = True
        ui.show_widget(button, False)
        key = state.playthrough_key(app) + ":player"
        if not worker.enqueue({"app": app, "key": key}):
            runtime["busy"] = False
            place_button(hud, button)
            return
        write("regeneration requested")

    def rebind(button, attr, target, events, callback):
        """target の events に callback を結ぶ。前の注入が結んだものは外す。

        控えは button の attr に (結んだ相手, 関数) で持つ。
        外さないと、古い注入の関数が呼ばれ続ける。
        """
        previous = frames.attr(button, attr, None)
        if isinstance(previous, tuple) and len(previous) == 2:
            try:
                previous[0].unbind(**{name: previous[1] for name in events})
            except Exception:
                pass
        target.bind(**{name: callback for name in events})
        setattr(button, attr, (target, callback))

    def ensure_button(hud):
        image = _value(hud, PORTRAIT_WIDGET)
        host = ui.overlay_host(hud)
        if image is None or host is None:
            return None
        button = getattr(hud, BUTTON_ATTR, None)
        if button is None:
            button = ui.make_icon_button(size=BUTTON_SIZE)
            if button is None:
                return None
            setattr(button, BUTTON_ATTR, True)
            setattr(hud, BUTTON_ATTR, button)
        parent = frames.attr(button, "parent", None)
        if parent is not host:
            try:
                if parent is not None:
                    parent.remove_widget(button)
                host.add_widget(button)       # 先頭に入る＝いちばん上に描かれる
            except Exception:
                ctx.log_exc("player portrait: could not add the button")
                return None

        def repaint(*_args):
            ui.paint_icon(button, BUTTON_STROKES, attr=BUTTON_ICON_ATTR,
                          key=("portrait",), log_exc=ctx.log_exc)

        def pressed(*_args):
            request(button)

        def follow(*_args):
            place_button(hud, button)

        rebind(button, BUTTON_PAINT_ATTR, button, ("pos", "size"), repaint)
        rebind(button, BUTTON_CALLBACK_ATTR, button, ("on_release",), pressed)
        rebind(button, BUTTON_WATCH_ATTR, image, PORTRAIT_EVENTS, follow)
        repaint()
        return button

    @ctx.wrap(
        "scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility",
        required=False, safe=True)
    def character_sheet(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        button = ensure_button(self)
        if button is not None:
            place_button(self, button)
        return result

    ctx.log("player portrait: installed ({})".format(", ".join(names)))
