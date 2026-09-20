# -*- coding: utf-8 -*-
r"""生成のたびに読み直す設定と規則（元 MOD のホットリロードに当たる）。

元の DLL 版は `generate_image` の先頭で毎回 `sd_upscale.ini` を開き直していた
（`proxy.c` の `load_config()`。更新時刻の確認も無い無条件の読み直し）。
ゲームを動かしたまま ini を直すと、次に生成される絵から効く。

こちらは出口のフックの先頭で、次の2つが変わっていたら読み直す。

    settings\mod_settings.json     寸法・サンプラー・安全弁（ローダの設定）
    state\image_quality\prompt_rules.json   プロンプトの規則

読み直すのは更新時刻と大きさの組が変わったときだけで、普段は属性を1回見るだけ。
書く側はどちらも隣に書いてから `os.replace` で差し替えるので、半端なファイルは掴まない。

**熱くならないもの**: チェックポイント・TAESD・VAE・LoRA の置き場と、
TAESD / VAE を渡さない指定。パイプラインが建つ瞬間にしか効かないので、
`apply()` の値のまま（元 MOD も同じで、ini にモデルのパスは無かった）。

`ctx` の値は `apply()` の間しか当てにならないので、
場所（`runtime_dir`・自分のフォルダ名・`state_dir`）は組むときに控える。
"""
import io
import json
import os

from instantale_modloader import config as loader_config
from instantale_modloader import frames

from . import rules as rulebook
from . import sizes

#: 熱く読み直す設定の名前。ここに無いものは `apply()` の値のまま。
HOT_NAMES = ("PORTRAIT_SHORT", "PORTRAIT_MAX_LONG",
             "ENEMY_SHORT", "ENEMY_MAX_LONG",
             "BACKGROUND_SHORT", "BACKGROUND_MAX_LONG",
             "STAGE1_METHOD", "STAGE1_STEPS", "STAGE1_CFG", "STAGE1_SCHEDULER",
             "STAGE2_METHOD", "STAGE2_STEPS", "STAGE2_CFG", "STAGE2_SCHEDULER",
             "LCM_METHOD", "LCM_STEPS", "LCM_CFG", "LCM_SCHEDULER",
             "STRIP_LORA", "MODEL_SAFETY", "DIFFUSERS_RESIZE")


def stamp_of(path):
    """ファイルの (更新時刻 ns, 大きさ)。無ければ None。"""
    try:
        st = os.stat(path)
    except Exception:
        return None
    return (getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)), st.st_size)


def derive(values):
    """設定の値から、出口で使う形（倍率・段ごとのサンプラー・旗）を組む。"""
    return {
        "scales": {
            "portrait": sizes.size_scale("portrait", values["PORTRAIT_SHORT"],
                                         values["PORTRAIT_MAX_LONG"]),
            "enemy": sizes.size_scale("enemy", values["ENEMY_SHORT"],
                                      values["ENEMY_MAX_LONG"]),
            "background": sizes.size_scale("background", values["BACKGROUND_SHORT"],
                                           values["BACKGROUND_MAX_LONG"]),
        },
        "samplers": {
            "stage1": (values["STAGE1_METHOD"], values["STAGE1_STEPS"],
                       values["STAGE1_CFG"], values["STAGE1_SCHEDULER"]),
            "stage2": (values["STAGE2_METHOD"], values["STAGE2_STEPS"],
                       values["STAGE2_CFG"], values["STAGE2_SCHEDULER"]),
            "lcm": (values["LCM_METHOD"], values["LCM_STEPS"],
                    values["LCM_CFG"], values["LCM_SCHEDULER"]),
        },
        "strip_lora": bool(values["STRIP_LORA"]),
        "safety": str(values["MODEL_SAFETY"] or "auto"),
        "diffusers_resize": bool(values["DIFFUSERS_RESIZE"]),
    }


class Live(object):
    """いま効いている設定と規則。`refresh()` で変わっていれば読み直す。

    `scales` / `samplers` / `strip_lora` / `safety` / `diffusers_resize` / `rules`
    を属性で持ち、本体はそれを読むだけ。
    """

    def __init__(self, ctx, write):
        self._write = write
        self._log_exc = ctx.log_exc
        self._state_dir = ctx.state_dir
        self._runtime_dir = frames.attr(ctx, "runtime_dir", None)
        mod_dir = frames.attr(ctx, "mod_dir", None)
        self._mod_name = os.path.basename(mod_dir) if mod_dir else None
        self._decls = self._read_decls(mod_dir)
        self._settings_path = (loader_config.store_path(self._runtime_dir)
                               if self._runtime_dir else None)
        self._rules_path = rulebook.rules_path(self._state_dir)

        # 最初の値は apply() の ctx から（読み直しの経路と同じファイルの中身）。
        self.values = dict((name, ctx.setting(name)) for name in HOT_NAMES)
        self._settings_stamp = stamp_of(self._settings_path) if self._settings_path else None
        self.rules = rulebook.load(self._state_dir)
        self._rules_stamp = stamp_of(self._rules_path)
        self._apply(self.values)

    @staticmethod
    def _read_decls(mod_dir):
        if not mod_dir:
            return {}
        try:
            with io.open(os.path.join(mod_dir, "mod.json"), encoding="utf-8") as fh:
                raw = json.load(fh).get("settings")
        except Exception:
            return {}
        return loader_config.normalize_decls(raw)

    def _apply(self, values):
        got = derive(values)
        self.scales = got["scales"]
        self.samplers = got["samplers"]
        self.strip_lora = got["strip_lora"]
        self.safety = got["safety"]
        self.diffusers_resize = got["diffusers_resize"]

    def hot(self):
        """読み直しができる状態か（場所が控えられていて、宣言も読めた）。"""
        return bool(self._settings_path and self._mod_name and self._decls)

    def describe(self):
        """ログ用の1行ぶん。"""
        lines = ["size x{portrait:.3f}/{enemy:.3f}/{background:.3f} (立ち絵/敵/背景)".format(
            **self.scales)]
        for stage, values in sorted(self.samplers.items()):
            if values != sizes.GAME_SAMPLER[stage]:
                lines.append("sampler {}: {} -> {}".format(
                    stage, sizes.GAME_SAMPLER[stage], values))
        counts = sum(len(rows) for rows in self.rules.values())
        if counts:
            lines.append("rules: {} 行（{}）".format(
                counts, ", ".join("{} {}".format(name, len(rows))
                                  for name, rows in sorted(self.rules.items()) if rows)))
        return lines

    def refresh(self):
        """変わっていたら読み直す。読み直したものの名前を返す（無ければ空）。"""
        changed = []
        try:
            if self.hot():
                stamp = stamp_of(self._settings_path)
                if stamp != self._settings_stamp:
                    self._settings_stamp = stamp
                    if self._reload_settings():
                        changed.append("settings")
            stamp = stamp_of(self._rules_path)
            if stamp != self._rules_stamp:
                self._rules_stamp = stamp
                self.rules = rulebook.load(self._state_dir)
                changed.append("rules")
        except Exception:
            self._log_exc("image quality: reload failed; keeping the previous values")
            return changed
        if changed:
            self._write("reloaded {} ({})".format(
                " + ".join(changed), "; ".join(self.describe())))
        return changed

    def _reload_settings(self):
        """設定ファイルを読み直して、熱い項目に差があれば差し替える。"""
        chosen = loader_config.load_store(self._runtime_dir).get(self._mod_name, {})
        resolved = loader_config.resolve(self._decls, chosen)
        values = dict((name, resolved.get(name, self.values[name])) for name in HOT_NAMES)
        if values == self.values:
            return False
        diff = ", ".join("{} {!r}->{!r}".format(name, self.values[name], values[name])
                         for name in HOT_NAMES if values[name] != self.values[name])
        self.values = values
        self._apply(values)
        self._write("settings changed: {}".format(diff))
        return True
