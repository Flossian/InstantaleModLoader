# -*- coding: utf-8 -*-
r"""元 `InstantaleStableDiffusionMod` の `sd_upscale.ini` を読み込む。

    settings, rules, notes = iniimport.read(r"...\InstantaleSDMod\sd_upscale.ini")

DLL 版を使っていた手元の調整を、こちらの設定と規則へ写すためのもの。
`settings` はローダの設定（`mod.json` の宣言に合う値）、
`rules` は `rules.py` の形、`notes` は写しきれなかったものの一覧。

##### 種類の対応

元 MOD は**縦横比**で画像を3つに分けていた。
こちらは呼び手で分かるので、実測した寸法（GAME.md §2.33）で対応させる。

| 元 | ここ | 根拠 |
|---|---|---|
| `portrait`（縦長） | 立ち絵 | 256x512 と 512x1024 |
| `landscape`（横長） | 背景 | 1024x512 |
| `square`（正方形） | 敵・モンスター | 512x512 |

##### 写せないもの

`round`（丸めの倍数）はこちらでは 64 の固定。
`enabled` / `enabled_<種類>` は、こちらでは「ゲームの値のままにする」ことで同じになる
（切ってあった種類は寸法を既定に戻す）。
サンプラーは元が種類別、こちらは段別なので、
`portrait_*` を1段目と2段目の両方へ、`landscape_*` と `square_*` を LCM の段へ写す
（後ろ2つが食い違うときは `landscape_*` を採り、`notes` に残す）。
"""
import io
import re

#: 元の種類 → こちらの種類。
KIND_OF = {"portrait": "portrait", "landscape": "background",
           "square": "enemy", "any": "any"}

#: ゲームの素の寸法（`sizes.GAME_SIZE` と同じ値。取り込みの既定に使う）。
GAME_SHORT = {"portrait": 512, "enemy": 512, "background": 512}
GAME_LONG = {"portrait": 1024, "enemy": 512, "background": 1024}

#: 設定の名前の頭。
PREFIX = {"portrait": "PORTRAIT", "enemy": "ENEMY", "background": "BACKGROUND"}

#: 段の設定の名前の頭。
STAGE_PREFIX = {"stage1": "STAGE1", "stage2": "STAGE2", "lcm": "LCM"}

#: サンプラーの名前の書式が違う（ini は `dpm++2m`、ゲームの語彙は `dpmpp2m`）。
#: 元 MOD は C の層へ自分で列挙値を当てていたので、ini 側の書き方が独自になっている。
METHOD_NAMES = {"dpm++2m": "dpmpp2m", "dpm++2mv2": "dpmpp2mv2",
                "dpm++2s_a": "dpmpp2s_a", "ddim trailing": "ddim_trailing"}


def parse(text):
    """ini を `{節: {鍵: 値}}` に。`;` と `#` で始まる行は注記として落とす。

    元 MOD の ini は「行を消すかコメントアウトすると既定に戻る」作りなので、
    **コメント行は読まない**のが正しい（読むと切ってある設定が生き返る）。
    """
    found = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ";#":
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            found.setdefault(section, {})
            continue
        if section is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # **鍵の大小は潰さない。** LoRA の名前がそのまま鍵になる節があり、
        # 小文字にすると画面に出したときに元の綴りが失われる。
        found[section][key.strip()] = value.strip()
    return found


def get(section, key, fallback=None):
    """節から鍵を引く。鍵の大小は問わない（節の名前も同じ扱い）。"""
    for name, value in (section or {}).items():
        if str(name).strip().lower() == str(key).strip().lower():
            return value
    return fallback


def read(path):
    """ini を読んで `(settings, rules, notes)` を返す。"""
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except Exception as exc:
        return {}, _empty_rules(), ["読めない: {}".format(exc)]
    return convert(parse(text))


def convert(ini):
    """読み込んだ ini を、こちらの設定と規則へ写す。"""
    settings = {}
    rules = _empty_rules()
    notes = []

    up = ini.get("upscale", {})
    if up:
        _sizes(up, settings, notes)
        _skips(up, rules)
    _sampler(ini.get("sampler", {}), settings, notes)

    for game_name, target in (("lora_map", None),
                              ("lora_add", "prompt"), ("negative_add", "negative"),
                              ("prompt_remove", "prompt"), ("negative_remove", "negative"),
                              ("prompt_replace", "prompt"),
                              ("negative_replace", "negative")):
        _rows(ini.get(game_name, {}), game_name, target, rules)
    _conditional(ini.get("lora_add_if", {}), rules, notes)
    return settings, rules, notes


def _empty_rules():
    return {"lora_map": [], "remove": [], "replace": [], "add": [], "skip_upscale": []}


def _int(value, fallback=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _float(value, fallback=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _sizes(up, settings, notes):
    """`[upscale]` を種類別の短辺・長辺へ。切ってある種類はゲームの値のまま。"""
    if _int(get(up, "enabled", 1), 1) == 0:
        notes.append("[upscale] enabled=0 なので寸法は写していない（全部ゲームのまま）")
        return
    if _int(get(up, "round", 64), 64) != 64:
        notes.append("round={} は写せない（こちらは 64 の固定）".format(get(up, "round")))

    short = _int(get(up, "goal_short"), GAME_SHORT["portrait"])
    long_ = _int(get(up, "max_long"), GAME_LONG["portrait"])
    land_short = _int(get(up, "goal_short_landscape"), 0) or short
    land_long = _int(get(up, "max_long_landscape"), 0) or long_

    for kind, values in (("portrait", (short, long_)),
                         ("enemy", (short, long_)),
                         ("background", (land_short, land_long))):
        flag = {"portrait": "enabled_portrait", "enemy": "enabled_square",
                "background": "enabled_landscape"}[kind]
        if _int(get(up, flag, 1), 1) == 0:
            notes.append("{}=0 なので {} は写していない".format(flag, kind))
            continue
        settings[PREFIX[kind] + "_SHORT"] = values[0]
        settings[PREFIX[kind] + "_MAX_LONG"] = values[1]


def _skips(up, rules):
    """`skip_if` / `skip_if_<種類>` を「拡大しない条件」の行へ。"""
    for key, kind in (("skip_if", "any"), ("skip_if_portrait", "portrait"),
                      ("skip_if_landscape", "background"),
                      ("skip_if_square", "enemy")):
        value = str(get(up, key) or "").strip()
        if value:
            rules["skip_upscale"].append({"enabled": True, "kind": kind, "when": value})


def _sampler(sampler, settings, notes):
    """種類別のサンプラーを段別へ。`portrait_*` は1段目と2段目の両方に効く。"""
    if not sampler:
        return
    for cls, stages in (("portrait", ("stage1", "stage2")),
                        ("landscape", ("lcm",)), ("square", ("lcm",))):
        got = {}
        for field, suffix in (("method", "METHOD"), ("steps", "STEPS"),
                              ("cfg", "CFG"), ("scheduler", "SCHEDULER")):
            value = str(get(sampler, "{}_{}".format(cls, field)) or "").strip()
            if not value:
                continue
            if suffix == "STEPS":
                got[suffix] = _int(value)
            elif suffix == "CFG":
                got[suffix] = _float(value)
            elif suffix == "METHOD":
                got[suffix] = METHOD_NAMES.get(value.lower(), value)
            else:
                got[suffix] = value
        if not got:
            continue
        for stage in stages:
            head = STAGE_PREFIX[stage]
            for suffix, value in got.items():
                name = "{}_{}".format(head, suffix)
                if name in settings and settings[name] != value:
                    notes.append("{} が {} と食い違うので先に読んだほうを残した".format(
                        cls, name))
                    continue
                settings[name] = value
        if cls == "portrait":
            notes.append("portrait のサンプラーを1段目と2段目の両方へ写した"
                         "（元 MOD は段を分けていない）")


def _rows(section, name, target, rules):
    """節の1行1行を規則の行へ。"""
    for key, value in sorted(section.items()):
        value = str(value or "").strip()
        if not value:
            continue
        if name == "lora_map":
            rules["lora_map"].append({"enabled": True, "from": key, "to": value})
            continue
        kind = KIND_OF.get(str(key).strip().lower())
        if kind is None:
            continue
        bucket = ("add" if name.endswith("_add")
                  else "remove" if name.endswith("_remove") else "replace")
        rules[bucket].append({"enabled": True, "kind": kind, "target": target,
                              "text": value})


def _conditional(section, rules, notes):
    """`[lora_add_if]` の `名前 = クラス | 条件 | 追加内容` を条件つきの行へ。"""
    for name, value in sorted(section.items()):
        parts = [part.strip() for part in str(value or "").split("|")]
        if len(parts) < 3:
            notes.append("[lora_add_if] {} は「クラス | 条件 | 追加内容」の形ではない".format(name))
            continue
        cls, condition, text = parts[0], parts[1], "|".join(parts[2:])
        kind = KIND_OF.get(cls.lower())
        if kind is None:
            notes.append("[lora_add_if] {} のクラス {} が読めない".format(name, cls))
            continue
        rules["add"].append({"enabled": True, "kind": kind, "target": "prompt",
                             "when": condition, "text": text, "name": name})


def summarize(settings, rules, notes):
    """取り込みの結果を人が読む形で。画面にそのまま出す。"""
    lines = []
    if settings:
        lines.append("設定 {} 項目: {}".format(
            len(settings), ", ".join(sorted(settings))))
    counts = [(name, len(rows)) for name, rows in sorted(rules.items()) if rows]
    if counts:
        lines.append("規則 {} 行: {}".format(
            sum(count for _name, count in counts),
            ", ".join("{} {}".format(name, count) for name, count in counts)))
    if not lines:
        lines.append("写すものが無かった（ini が空か、全部コメントアウトされている）")
    lines.extend("※ " + note for note in notes)
    return "\n".join(lines)
