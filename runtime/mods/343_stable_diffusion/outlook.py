# -*- coding: utf-8 -*-
r"""次に描く絵の見通し。道具画面のタブの上の帯に出す。

設定（保存の前の欄の値も）・カスタムプリセット・ゲームの `config.json`・`out\stable_diffusion.log` から、
種類ごとに「どのモデル・寸法・サンプラーで描くか」と「いま効いていない設定」を組む。

決め方は本体（`stable_diffusion.py` の `wanted` / `preset_route` / `add_safety` / `stage_of`）に合わせてある。
ずれないように、プリセットの重ね方は `presets.overlay`、寸法は `sizes` を本体と共有する。
ゲームの画質ごとの経路は測れた分だけ（GAME.md §2.33）。測れていない画質は、ログに残った前回の段で補う。

ここはゲームにもローダにも触らない計算だけ（道具画面は本体を import しない）。
"""
import io
import os
import re

try:                      # MOD 本体からはパッケージの隣、道具と検査からは素の名前で
    from . import presets
    from . import sizes
except ImportError:
    import presets  # noqa: E402  自分の隣
    import sizes    # noqa: E402  自分の隣

KINDS = (("portrait", "立ち絵"), ("enemy", "敵"), ("background", "背景"))
KIND_NAMES = dict(KINDS)

#: 種類ごとのプリセットの設定名（`live.KIND_PRESET_NAMES` と同じ）。
KIND_PRESET_NAMES = {"portrait": "PORTRAIT_PRESET", "enemy": "ENEMY_PRESET",
                     "background": "BACKGROUND_PRESET"}

#: 寸法の設定名の頭。
SIZE_HEADS = {"portrait": "PORTRAIT", "enemy": "ENEMY", "background": "BACKGROUND"}

#: ゲームの `config.json` の、種類ごとの画質の欄。
QUALITY_KEYS = {"portrait": "character_generation_quality",
                "enemy": "monster_generation_quality",
                "background": "background_generation_quality"}

#: sdcpp 系で、画質ごとに通る段（実測できた組だけ。GAME.md §2.33）。
#: 立ち絵の `highres_upscale` は1段目で下描きして2段目で描き直す。
STAGES_BY_QUALITY = {
    "portrait": {"highres_upscale": ("stage1", "stage2"), "highres_faster": ("lcm",)},
    "enemy": {"highres_faster": ("lcm",)},
    "background": {"highres_faster": ("lcm",)},
}

#: 段ごとの、ゲームが出口へ渡す寸法（実測。GAME.md §2.33）。
BASE_SIZES = {("portrait", "stage1"): (256, 512), ("portrait", "stage2"): (512, 1024),
              ("portrait", "lcm"): (512, 1024), ("enemy", "lcm"): (512, 512),
              ("background", "lcm"): (1024, 512)}

STAGE_LABELS = {"stage1": "1段目", "stage2": "2段目", "lcm": "LCM の段"}
STAGE_ORDER = ("stage1", "stage2", "lcm")

#: ログの1枚ぶんの行（`make_exit_hook` の `write`）。プロンプトの行は字下げがあるので当たらない。
_DRAW_LINE = re.compile(r"^\[\d{4}-(\d\d)-(\d\d)T(\d\d):(\d\d)[^\]]*\] (?=\S)\S+ "
                        r"\[(portrait|enemy|background)/(stage1|stage2|lcm)\](.*)$")
_MODE = re.compile(r"^ (描き直し|続けて描く \d/\d)")
_ROUTE = re.compile(r" <([^>]*)>")
_SIDE = {"width": re.compile(r"\bwidth: \d+->(\d+)"), "height": re.compile(r"\bheight: \d+->(\d+)")}
#: ゲームの部品が建ったときの行（`safety: 系統は sd15（sotemix_v30.safetensors）`）。
_BUILT = re.compile(r"\] safety: 系統は (sd15|sdxl)（(.+?)）")

#: ログは末尾だけ読む（数 MB になる）。
LOG_TAIL_BYTES = 1024 * 1024


def _int(value, fallback=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _float(value, fallback=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _stem(path):
    return os.path.splitext(os.path.basename(str(path or "")))[0]


def method_family(backend_name):
    """ゲームの方式の名前を一族へ。`sdcpp` / `diffusers` / None（読めない）。"""
    name = str(backend_name or "")
    if name.startswith("sdcpp"):
        return "sdcpp"
    if name.startswith("diffusers"):
        return "diffusers"
    return None


def read_log(path, tail=LOG_TAIL_BYTES):
    """ログの末尾から、種類ごとの前回の1枚と、ゲームの部品の系統を読む。

    `{"last": {種類: {"when", "stage", "mode", "route", "size"}}, "built": (系統, ファイル名) か None}`。
    """
    got = {"last": {}, "built": None}
    try:
        with io.open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail))
            raw = fh.read()
    except Exception:
        return got
    text = raw.decode("utf-8", "replace")
    if size > tail:
        text = text.split("\n", 1)[-1]      # 途中から読んだ1行目は捨てる
    for line in text.splitlines():
        found = _DRAW_LINE.match(line)
        if found:
            month, day, hour, minute, kind, stage, rest = found.groups()
            mode = _MODE.match(rest)
            route = _ROUTE.search(rest)
            sides = [_SIDE[name].search(rest) for name in ("width", "height")]
            got["last"][kind] = {
                "when": "{}-{} {}:{}".format(month, day, hour, minute),
                "stage": stage,
                "mode": mode.group(1) if mode else "",
                "route": route.group(1) if route else "",
                "size": (int(sides[0].group(1)), int(sides[1].group(1))) if all(sides) else None,
            }
            continue
        built = _BUILT.search(line)
        if built:
            got["built"] = (built.group(1), built.group(2))
    return got


class _FamilyCache(object):
    """チェックポイントの系統（中身のヘッダを読む）。同じファイルは読み直さない。"""

    def __init__(self):
        self._seen = {}

    def __call__(self, path):
        try:
            stamp = os.stat(path).st_mtime_ns
        except Exception:
            return None
        key = (os.path.normcase(path), stamp)
        if key not in self._seen:
            self._seen[key] = sizes.family_of(path)
        return self._seen[key]


family_of = _FamilyCache()


def _full(path, game_dir):
    path = str(path or "").strip()
    if not path:
        return ""
    return path if os.path.isabs(path) or not game_dir else os.path.join(game_dir, path)


def stages_for(kind, family, quality, last):
    """その種類が通る段と、どう分かったか（`"known"` / `"last"` / `"unknown"`）。"""
    if family == "diffusers":
        return ("lcm",), "known"            # diffusers 系は立ち絵もここを通る（`stage_of`）
    known = STAGES_BY_QUALITY.get(kind, {}).get(quality)
    if known:
        return known, "known"
    if last:
        return (last["stage"],), "last"
    return (), "unknown"


def sampler_of(stage, values):
    """段のサンプラー（方法, steps, cfg, スケジューラ）。数字は数字に直す。"""
    head = {"stage1": "STAGE1", "stage2": "STAGE2", "lcm": "LCM"}[stage]
    game = sizes.GAME_SAMPLER[stage]
    return (str(values.get(head + "_METHOD") or game[0]),
            _int(values.get(head + "_STEPS"), game[1]),
            _float(values.get(head + "_CFG"), game[2]),
            str(values.get(head + "_SCHEDULER") or game[3]))


def size_of(kind, stage, values, family):
    """この段で出口に渡る寸法（本体の `wanted` と同じ計算）。"""
    base = BASE_SIZES.get((kind, stage))
    if base is None:
        return None
    if family == "diffusers" and not values.get("DIFFUSERS_RESIZE"):
        return base
    head = SIZE_HEADS[kind]
    short = _int(values.get(head + "_SHORT"))
    long_ = _int(values.get(head + "_MAX_LONG"))
    if short is None or long_ is None:
        return base
    new = sizes.scaled(base[0], base[1], sizes.size_scale(kind, short, long_), long_)
    return new or base


def plan_kind(kind, values, custom, family, quality, game, last, game_dir=""):
    """1種類ぶんの見通し。

    `values` は道具画面の欄の値（足りない項目は既定で埋めたもの）。`custom` はカスタムプリセットの並び。
    `game` は `{"model": ゲームのモデル名, "family": 系統か None}`。`last` はログの前回の1枚（無ければ None）。
    戻り値は `{"model", "source", "family", "stages", "how", "size", "samplers", "values", "missing"}`。
    """
    used = values
    source = ""
    model = "ゲーム既定"
    model_family = game.get("family")
    missing = ""
    replaced = str(values.get("CHECKPOINT_PATH") or "").strip()
    if replaced:
        source = "差し替え" if family != "diffusers" else "差し替え。diffusers 系で効くかは未確認"
        model = _stem(replaced)
        model_family = family_of(_full(replaced, game_dir))
    name = str(values.get(KIND_PRESET_NAMES[kind]) or "").strip() if values.get("KIND_PRESETS") else ""
    if name and family != "diffusers":
        entry = next((row for row in custom if row["name"] == name), None)
        if entry is None:
            missing = name
        else:
            used = presets.overlay(values, entry["values"])
            checkpoint = str(entry["values"].get("CHECKPOINT_PATH") or "").strip()
            source = "プリセット"
            model = name
            model_family = family_of(_full(checkpoint, game_dir)) if checkpoint else game.get("family")
    stages, how = stages_for(kind, family, quality, last)
    samplers = []
    for stage in stages:
        got = sampler_of(stage, used)
        # 安全弁（本体の `add_safety`）: SDXL を素のままの LCM の段で描くときは SDXL 向きへ寄せる。
        if (stage == "lcm" and model_family == "sdxl" and got == sizes.GAME_SAMPLER["lcm"]
                and str(used.get("MODEL_SAFETY") or "auto") == "auto"):
            got = sizes.SDXL_SAMPLER
        samplers.append((stage, got))
    size = size_of(kind, stages[-1], used, family) if stages else None
    return {"model": model, "source": source, "family": model_family, "stages": stages, "how": how,
            "size": size, "samplers": samplers, "values": used, "missing": missing}


def sampler_text(sampler, family):
    method, steps, cfg, scheduler = sampler
    if family == "diffusers":
        return "steps {} / cfg {:g}".format(steps, cfg)
    return "{} {} / cfg {:g}{}".format(method, steps, cfg,
                                       "" if scheduler == "default" else " " + scheduler)


def kind_line(kind, plan, family):
    """帯の1行（種類, モデル, 寸法, サンプラー）。"""
    model = "{}（{}）".format(plan["model"], plan["source"]) if plan["source"] else plan["model"]
    size = "{}x{}".format(*plan["size"]) if plan["size"] else "寸法は不明"
    if not plan["stages"]:
        sampler = "段が分からない（まだ描いていない画質）"
    elif len(plan["samplers"]) == 1:
        sampler = sampler_text(plan["samplers"][0][1], family)
    else:
        sampler = "　".join("{} {}".format(STAGE_LABELS[stage], sampler_text(value, family))
                           for stage, value in plan["samplers"])
    if plan["how"] == "last":
        sampler += "（前回の段から）"
    return KIND_NAMES[kind], model, size, sampler


def last_text(last):
    """前回の1枚（ログ）を短く。"""
    if not last:
        return "記録なし"
    parts = [last["when"]]
    if last["mode"]:
        parts.append(last["mode"])
    if last["route"]:
        parts.append(last["route"])
    if last["size"]:
        parts.append("{}x{}".format(*last["size"]))
    return "・".join(parts)


def method_model(name, custom, game_model):
    """描き直しと続けて描くのプリセットの欄が描くモデル。空か見つからないならゲーム既定（本体の `method_route`）。"""
    if name and any(row["name"] == name for row in custom):
        return name
    return "ゲーム既定"


def method_cells(kind, values, custom, family, game_model):
    """その種類の (img2img のモデル, txt2img→img2img のモデル)。使う設定が切なら「切」。"""
    if family == "diffusers":
        return "使えない", "使えない"
    head = SIZE_HEADS[kind]
    text = lambda key: str(values.get(key) or "").strip()
    redraw = method_model(text("IMG2IMG_{}_PRESET".format(head)), custom, game_model)         if values.get("IMG2IMG") else "切"
    if not values.get("CHAIN"):
        return redraw, "切"
    chain = "{} → {}".format(method_model(text("CHAIN_{}_FIRST".format(head)), custom, game_model),
                             method_model(text("CHAIN_{}_SECOND".format(head)), custom, game_model))
    if values.get("CHAIN_{}_AUTO".format(head)):
        chain += "（通常の生成も）"
    return redraw, chain


def method_missing(values, custom):
    """描き直しと続けて描くで、使う設定が入なのに見つからないプリセット（知らせの文）。"""
    notes = []
    names = set(row["name"] for row in custom)
    for kind, label in KINDS:
        head = SIZE_HEADS[kind]
        keys = []
        if values.get("IMG2IMG"):
            keys.append(("img2img", "IMG2IMG_{}_PRESET".format(head)))
        if values.get("CHAIN"):
            keys += [("txt2img→img2img の1つ目", "CHAIN_{}_FIRST".format(head)),
                     ("txt2img→img2img の2つ目", "CHAIN_{}_SECOND".format(head))]
        for what, key in keys:
            name = str(values.get(key) or "").strip()
            if name and name not in names:
                notes.append("{}の{}のプリセット「{}」（見つからない。ゲーム既定のモデルで描く）".format(label, what, name))
    return notes


def idle_notes(values, plans, family, backend, log):
    """いま効いていない設定と理由。"""
    notes = []
    if not backend:
        notes.append("ゲームの設定（config.json）が読めないので、方式と画質ごとの段は分からない")
    assigned = [KIND_NAMES[kind] for kind, _label in KINDS
                if str(values.get(KIND_PRESET_NAMES[kind]) or "").strip()]
    if assigned and not values.get("KIND_PRESETS"):
        notes.append("{}に割り当てたプリセット（種類ごとのプリセットが切）".format("・".join(assigned)))
    for kind, _label in KINDS:
        if plans[kind]["missing"]:
            notes.append("{}のプリセット「{}」（見つからない。プリセットを使わずに描く）".format(
                KIND_NAMES[kind], plans[kind]["missing"]))
    if family == "diffusers":
        if values.get("KIND_PRESETS") and assigned:
            notes.append("種類ごとのプリセット（diffusers 系では使わない）")
        if values.get("IMG2IMG") or values.get("CHAIN"):
            notes.append("高度設定（diffusers 系では使わない）")
        resized = any(size_of(kind, "lcm", dict(values, DIFFUSERS_RESIZE=True), "sdcpp")
                      != BASE_SIZES[(kind, "lcm")] for kind, _label in KINDS)
        if resized and not values.get("DIFFUSERS_RESIZE"):
            notes.append("解像度（diffusers 系では寸法を変えない。「実験用」で入にできる）")
        if any(sampler_of(stage, values)[0::3] != sizes.GAME_SAMPLER[stage][0::3] for stage in STAGE_ORDER):
            notes.append("サンプラーの方法とスケジューラ（diffusers 系は steps と cfg だけ）")
    # 道具画面の各タブの値で描く種類（プリセットで描く種類はプリセットの値）が、どの段も通らないなら効かない。
    base_kinds = [kind for kind, _label in KINDS if plans[kind]["values"] is values]
    if not base_kinds and family != "diffusers":
        notes.append("解像度・サンプラーのタブの値（どの種類もプリセットの値で描く）")
    elif all(plans[kind]["how"] != "unknown" for kind in base_kinds):
        used = set(stage for kind in base_kinds for stage in plans[kind]["stages"])
        for stage in STAGE_ORDER:
            if stage not in used and sampler_of(stage, values) != sizes.GAME_SAMPLER[stage]:
                notes.append("{}のサンプラー（ゲームの画質では、この段を通る絵が無い）".format(STAGE_LABELS[stage]))
    built = log.get("built")
    replaced = str(values.get("CHECKPOINT_PATH") or "").strip()
    if built and replaced and _stem(built[1]).lower() != _stem(replaced).lower():
        notes.append("モデルの差し替え（ゲームを起動し直すと効く。いま建っているのは {}）".format(_stem(built[1])))
    return notes


def build(values, custom, backend, game_model, log, game_dir=""):
    """帯の中身。

    `{"rows": [(種類, txt2img のモデル, 寸法, サンプラー, img2img のモデル, txt2img→img2img のモデル)],
    "game": ゲーム既定のモデルの名前, "last": [(種類, 前に描いた絵)], "notes": [文]}`。
    寸法とサンプラーは txt2img（ふだんの生成）のもの。欄の「ゲーム既定」の中身は `game` に1回だけ出す。
    `backend` はゲームの `config.json` の画像生成の欄（読めなければ空）。`log` は `read_log` の戻り値。
    """
    family = method_family(backend.get("name"))
    built = log.get("built")
    game = {"model": game_model,
            "family": built[0] if built and game_model and _stem(built[1]).lower() == str(game_model).lower()
            else None}
    plans = {}
    rows = []
    last_rows = []
    for kind, label in KINDS:
        last = log.get("last", {}).get(kind)
        plans[kind] = plan_kind(kind, values, custom, family, backend.get(QUALITY_KEYS[kind]),
                                game, last, game_dir)
        rows.append(kind_line(kind, plans[kind], family) + method_cells(kind, values, custom, family, game_model))
        last_rows.append((label, last_text(last)))
    notes = idle_notes(values, plans, family, backend, log)
    if family != "diffusers":
        notes += method_missing(values, custom)
    return {"rows": rows, "game": game_model or "", "last": last_rows, "notes": notes}
