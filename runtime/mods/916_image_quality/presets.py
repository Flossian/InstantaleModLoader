# -*- coding: utf-8 -*-
r"""SD1.5 / SDXL のプリセット（元 MOD の `sd_upscale.sd15.ini` / `sd_upscale.sdxl.ini` に当たる）。

元 MOD はモードを切り替えるとき ini を丸ごと差し替えていた。
こちらは設定が1枚なので、**プリセットで入れ替える**ボタンを道具画面に置く。
片方だけ外す（SDXL のチェックポイントを外して寸法とサンプラーは残す）と、
SD1.5 に 1024x2048 を 24 steps で描かせることになり、
2段目が数分かかって次の生成と重なった末にプロセスごと落ちた（実機 2026-09-20）。

値は元 MOD の ini をそのまま写し、こちらで測って要ると分かったものを足してある。

| | SD1.5 プリセット | SDXL プリセット |
| --- | --- | --- |
| 立ち絵 / 敵 | 832 / 1216 | 1024 / 2048 |
| 背景 | ゲームのまま | 704 / 1408 |
| 1段目・2段目 | euler_a / 24 / 5.0 | euler_a / 24 / 5.0 |
| LCM の段 | ゲームのまま | euler_a / 24 / 5.0（SDXL に lcm / 5 / 1.0 は絵にならない。実測） |
| LoRA タグを外す | 切 | 入（SD1.5 用の LoRA が当たると落ちる。実測） |
| チェックポイント / TAESD | 空（ゲームのまま） | `state\models\sdxl\` の一番新しいもの。無ければ空のまま注記 |

元 MOD の `portrait_*` はこちらの1段目と2段目の両方に写す（`iniimport` と同じ）。
元 MOD の `square`（敵）は `portrait` と同じ値を使っていたので、敵も同じにする。
"""
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
                 r"チェックポイントと TAESD は state\models\sdxl\ の一番新しいものを指す",
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
    return values, notes
