# -*- coding: utf-8 -*-
r"""SD1.5 / SDXL のプリセット（元 MOD の `sd_upscale.sd15.ini` / `sd_upscale.sdxl.ini` に当たる）。

元 MOD はモードを切り替えるとき ini を丸ごと差し替えていた。
こちらは設定が1枚なので、**プリセットで入れ替える**ボタンを道具画面に置く。
片方だけ外す（SDXL のチェックポイントを外して寸法とサンプラーは残す）と、
SD1.5 に 1024x2048 を 24 steps で描かせることになり、
2段目が数分かかって次の生成と重なった末にプロセスごと落ちた（実機）。

値は元 MOD の ini をそのまま写し、こちらで測って要ると分かったものを足してある。

| | SD1.5 プリセット | SDXL プリセット |
| --- | --- | --- |
| 立ち絵 / 敵 | 832 / 1216 | 1024 / 2048 |
| 背景 | ゲームのまま | 704 / 1408 |
| 1段目・2段目 | euler_a / 24 / 5.0 | euler_a / 24 / 5.0 |
| LCM の段 | ゲームのまま | euler_a / 24 / 5.0（SDXL に lcm / 5 / 1.0 は絵にならない。実測） |
| LoRA タグを外す | 切 | 入（SD1.5 用の LoRA が当たると落ちる。実測） |
| チェックポイント / TAESD | 空（ゲームのまま） | `state\stable_diffusion\models\sdxl\` の一番新しいもの（中身が SDXL のものだけ）。無ければ空のまま注記 |

元 MOD の `portrait_*` はこちらの1段目と2段目の両方に写す（`iniimport` と同じ）。
元 MOD の `square`（敵）は `portrait` と同じ値を使っていたので、敵も同じにする。

##### カスタムプリセット（版26）

道具画面の設定をまるごと名前を付けて保存し、あとで欄へ読み込める。
置き場は `state\stable_diffusion\presets.json`（規則の `prompt_rules.json` の隣）。
残すのは設定（`mod.json` の項目）だけで、規則は持たない。規則は「モデル別」の行でモデルごとに切り替わる。

    {"presets": [{"name": "WAI Mature 1段", "saved": "2026-10-26T14:10:00",
                  "values": {"CHECKPOINT_PATH": "...", "PORTRAIT_SHORT": 704, ...}}]}
"""
import io
import json
import os
import time

try:                      # MOD 本体からはパッケージの隣、道具と検査からは素の名前で
    from . import assets
except ImportError:
    import assets  # noqa: E402  自分の隣（モデルの置き場）

#: 段のサンプラーの設定名。
_STAGE = ("METHOD", "STEPS", "CFG", "SCHEDULER")

#: 元 MOD の portrait_* サンプラー（両方の ini で同じ値）。
_DLL_SAMPLER = ("euler_a", 24, 5.0, "default")

#: ゲームの LCM の段（`sizes.GAME_SAMPLER["lcm"]` と同じ値。道具は本体を import しない）。
_GAME_LCM = ("lcm", 5, 1.0, "default")


def _sampler(stage, values):
    return dict(("{}_{}".format(stage, suffix), value)
                for suffix, value in zip(_STAGE, values))


def _base(portrait, enemy, background, lcm, strip_lora):
    got = {
        "PORTRAIT_SHORT": portrait[0], "PORTRAIT_MAX_LONG": portrait[1],
        "ENEMY_SHORT": enemy[0], "ENEMY_MAX_LONG": enemy[1],
        "BACKGROUND_SHORT": background[0], "BACKGROUND_MAX_LONG": background[1],
        "STRIP_LORA": strip_lora,
        "MODEL_SAFETY": "auto",
        "VAE_PATH": "", "LORA_DIR": "",
        "DISABLE_TAESD": False, "DISABLE_VAE": False,
    }
    got.update(_sampler("STAGE1", _DLL_SAMPLER))
    got.update(_sampler("STAGE2", _DLL_SAMPLER))
    got.update(_sampler("LCM", lcm))
    return got


PRESETS = (
    {
        "key": "sd15",
        "label": "SD1.5 プリセット",
        "about": "元 MOD の sd_upscale.sd15.ini。立ち絵と敵 832 / 1216、euler_a / 24 / 5.0。"
                 "チェックポイントと TAESD は空に戻す（ゲームのまま）",
        "values": _base((832, 1216), (832, 1216), (512, 1024), _GAME_LCM, False),
        "family": None,
    },
    {
        "key": "sdxl",
        "label": "SDXL プリセット",
        "about": "元 MOD の sd_upscale.sdxl.ini。立ち絵と敵 1024 / 2048、背景 704 / 1408、euler_a / 24 / 5.0。"
                 "LoRA タグを外し、LCM の段も euler_a / 24 / 5.0 にする。"
                 r"チェックポイントと TAESD は state\stable_diffusion\models\sdxl\ の一番新しいものを指す",
        "values": _base((1024, 2048), (1024, 2048), (704, 1408), _DLL_SAMPLER, True),
        "family": "sdxl",
    },
)


def by_key(key):
    for preset in PRESETS:
        if preset["key"] == key:
            return preset
    raise KeyError(key)


def resolve(key, state_dir):
    """プリセットの値と、画面に出す注記。材料は置き場から引く（無ければ空のまま注記）。"""
    preset = by_key(key)
    values = dict(preset["values"])
    notes = []
    family = preset["family"]
    if family is None:
        values["CHECKPOINT_PATH"] = ""
        values["TAESD_PATH"] = ""
        return values, notes
    for kind, name, label in (("checkpoints", "CHECKPOINT_PATH", "チェックポイント"),
                              ("taesd", "TAESD_PATH", "TAESD デコーダ")):
        found = assets.newest(state_dir, family, kind)
        values[name] = found
        if found:
            notes.append("{}: {}".format(label, found))
        else:
            notes.append("{}: {} に無いので空のまま。置くか「参照」で指す".format(
                label, assets.dir_of(state_dir, family, kind)))
    for name in assets.foreign(state_dir, family):
        notes.append("{} は SDXL のチェックポイントとして読めないので選ばなかった".format(name))
    return values, notes



# ---------------------------------------------------------- カスタムプリセット

CUSTOM_DIRNAME = "stable_diffusion"
CUSTOM_NAME = "presets.json"


def custom_path(state_dir):
    return os.path.join(state_dir, CUSTOM_DIRNAME, CUSTOM_NAME)


def load_custom(state_dir):
    """カスタムプリセットの並び（保存した順）。無い・壊れているなら空。"""
    try:
        with io.open(custom_path(state_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    rows = data.get("presets") if isinstance(data, dict) else None
    # v1.18.0 の置き場（state\models\）を指したままのパスは、新しい置き場へ読み替えて渡す。
    return [dict(row, values=assets.relocate_values(row["values"], state_dir))
            for row in rows or []
            if isinstance(row, dict) and str(row.get("name") or "").strip()
            and isinstance(row.get("values"), dict)]


def _write_custom(state_dir, rows):
    path = custom_path(state_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"presets": rows}, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def find_custom(state_dir, name):
    name = str(name or "").strip()
    for row in load_custom(state_dir):
        if row["name"] == name:
            return row
    return None


def save_custom(state_dir, name, values, now=None):
    """名前を付けて残す。同じ名前は置き換える（並びの位置は保つ）。書けたかを返す。"""
    name = str(name or "").strip()
    if not name:
        return False
    entry = {"name": name, "saved": now or time.strftime("%Y-%m-%dT%H:%M:%S"),
             "values": dict(values)}
    rows = load_custom(state_dir)
    for index, row in enumerate(rows):
        if row["name"] == name:
            rows[index] = entry
            break
    else:
        rows.append(entry)
    return _write_custom(state_dir, rows)


def delete_custom(state_dir, name):
    """名前のプリセットを消す。消せたかを返す（無ければ False）。"""
    rows = load_custom(state_dir)
    kept = [row for row in rows if row["name"] != str(name or "").strip()]
    if len(kept) == len(rows):
        return False
    return _write_custom(state_dir, kept)


def overlay(values, preset_values):
    """いまの設定にプリセットの値を重ねる。`values` に無い項目（版の違いで知らない項目）は捨てる。

    本体（種類ごとのプリセットで描く回）と道具画面の帯（次に描く絵）で同じ重ね方にする。
    """
    got = dict(values)
    got.update((key, value) for key, value in (preset_values or {}).items() if key in got)
    return got


def custom_values(entry, known):
    """欄へ入れる値と、画面に出す注記。

    `known` はいまの設定の項目名。知らない項目（版が変わって無くなったもの）は落とし、
    プリセットに無い項目（後から足された設定）はいまの値のまま残す。
    差し替えるファイルが見当たらなければ注記する（入れることは止めない）。
    """
    values = dict((key, value) for key, value in (entry.get("values") or {}).items()
                  if key in known)
    notes = []
    dropped = sorted(set(entry.get("values") or {}) - set(known))
    if dropped:
        notes.append("現在の版にない項目は読み込みませんでした: {}".format(", ".join(dropped)))
    missing = sorted(set(known) - set(entry.get("values") or {}))
    if missing:
        notes.append("プリセットにない項目は現在の値のままです: {}".format(", ".join(missing)))
    for key, label in (("CHECKPOINT_PATH", "チェックポイント"), ("TAESD_PATH", "TAESD デコーダ"),
                       ("VAE_PATH", "VAE")):
        path = str(values.get(key) or "").strip()
        if path and os.path.isabs(path) and not os.path.isfile(path):
            notes.append("{}が見つかりません: {}".format(label, path))
    path = str(values.get("LORA_DIR") or "").strip()
    if path and os.path.isabs(path) and not os.path.isdir(path):
        notes.append("LoRA の置き場が見つかりません: {}".format(path))
    return values, notes
