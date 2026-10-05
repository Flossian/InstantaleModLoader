# -*- coding: utf-8 -*-
"""913 の機能が共有する土台。ゲームの値の読み方、控え、画面の部品、選択肢と押下と本文の振り分け。

機能ごとのファイル（`loot` / `law` / `theft` / `office` / `prison`）は `install(env)` を1つ持ち、
ここの `Env` を受け取る。設定は入口（`crime_incentive.py`）の定数で、`env.cfg.<名前>` をその場で読む
（ローダは apply() の前に入口の定数へ書き込む。後から変えた値も読めるよう、控えない）。

ゲームの同じ入口を913の中で何度も包まないよう、選択肢を組み直した合図（`refresh_choice_buttons`）・
押下（`on_button_press`）・本文（`add_text`）は入口が1枚だけ包み、ここに登録された処理へ配る。
"""
import os
import sys

from instantale_modloader import guards, ui
from instantale_modloader import state as loader_state

LOG_BASENAME = "crime_incentive.log"
FUNCTIONS_MODULE = "scripts.functions"
STATE_DIRNAME = "crime_incentive"
STORE_ATTR = "_instantale_crime_incentive"
#: 押下を横取りする印（他の MOD と別のキー）。
MARK = "mod_crime_incentive_action"
#: 施設の選択肢だと見なす目印（出口のマネージャ。`309_` と同じ）。
FACILITY_MARK = "MovePhaseManager"
#: ゲーム自身の衛兵戦を起こすときに `process_choice` へ渡す文字列（GAME.md §2.20）。
GUARD_CHOICE_TEXT = "衛兵"


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None}
        setattr(sys, STORE_ATTR, store)
    return store


def is_facility_screen(buttons):
    """施設の選択肢か（出口のボタンがある）。"""
    return isinstance(buttons, (list, tuple)) and any(
        ui.spec_cls_name(entry) == FACILITY_MARK for entry in buttons)


class Env(object):
    """機能のファイルへ渡す土台。"""

    def __init__(self, ctx, cfg):
        self.ctx = ctx
        self.cfg = cfg
        self.write = ctx.logger(LOG_BASENAME)
        self.owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "913_crime_incentive"
        self.screen = ui.Screen(ctx, self.write, tag="crime incentive", mark=MARK)
        # 控えは `state\crime_incentive\<世界×主人公>.json` に1つ。機能ごとに鍵を分ける
        # （`away` 時効 / `theft` 店で盗む / `underworld` 裏の仕事 / `jail` 脱獄）。
        store = _store()
        if store["worlds"] is None:
            store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=self.write)
        self.worlds = store["worlds"].rebind(ctx, self.write)
        self.refresh_handlers = []
        self.press_handlers = []
        self.text_filters = []

    # ---------------------------------------------------- 振り分けの登録
    def on_refresh(self, handler):
        """選択肢を組み直した合図で `handler(app, buttons)` を呼ぶ（`buttons` はゲームの一覧そのもの）。"""
        self.refresh_handlers.append(handler)

    def on_press(self, prefix, handler):
        """印が `prefix` で始まる自前のボタンが押されたら `handler(app, 印)` を呼ぶ。"""
        self.press_handlers.append((prefix, handler))

    def on_text(self, handler):
        """本文へ出る1行ごとに `handler(文) -> (文, 出さないか)` を通す。"""
        self.text_filters.append(handler)

    # ---------------------------------------------------- ゲームの値を読む
    def area_difficulty(self, app):
        """いまの土地の平均難易度（ゲームのヘルパ）。引けなければ None。"""
        functions = sys.modules.get(FUNCTIONS_MODULE)
        average = getattr(functions, "get_area_average_difficulty", None)
        area = ui.current_area(app)
        if not callable(average) or area is None:
            return None
        value = average(area, getattr(app, "world", None))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return max(1, int(round(value)))

    def quest_reward(self, difficulty):
        """その難易度の依頼1件の報酬（ゲームのヘルパ）。引けなければ None。"""
        functions = sys.modules.get(FUNCTIONS_MODULE)
        reward = getattr(functions, "get_quest_reward", None)
        if not callable(reward) or difficulty is None:
            return None
        value = reward(difficulty)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    def here_lawfulness(self, app):
        """いまの土地の手配度。読めなければ None。"""
        player = getattr(app, "player", None)
        area_id = ui.area_id_of(ui.current_area(app))
        return ui.lawfulness_of(ui.area_record(player, area_id)) if area_id else None

    def area_label(self, app, area_id):
        name = getattr(ui.world_areas(app).get(area_id), "name", None)
        return name if isinstance(name, str) and name else area_id

    def refresh_gold(self, app):
        updater = getattr(app, "update_ui", None)
        if callable(updater):
            updater()

    def current_facility(self, app):
        location = getattr(getattr(app, "player", None), "location", None)
        if isinstance(location, (str, int)):
            facility, _node = ui.find_facility(ui.current_area(app), str(location))
            return facility
        return location

    # ---------------------------------------------------- 画面の部品
    def back_to_shop(self, app, why):
        """施設の選択肢に戻す（自前のボタンは外す。足し直すかは選択肢の合図の処理が決める）。"""
        saved = [entry for entry in (getattr(app, "buttons", None) or [])
                 if not self.screen.mark_of(entry)]
        self.write("back to the facility ({})".format(why))
        self.screen.apply_buttons(app, saved, "back")

    def call_guards(self, app):
        """ゲーム自身の衛兵戦を起こす（強さはゲームのまま）。起こせなければ施設の選択肢に戻す。"""
        phase = guards.build(app)
        if phase is None:
            self.back_to_shop(app, "no guards")
            return
        if not self.screen.start_phase(app, phase, GUARD_CHOICE_TEXT):
            self.back_to_shop(app, "guards did not start")
