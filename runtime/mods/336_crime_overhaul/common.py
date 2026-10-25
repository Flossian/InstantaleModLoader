# -*- coding: utf-8 -*-
"""336 の機能が共有する土台。ゲームの値の読み方、控え、画面の部品、選択肢と押下と本文の振り分け。

機能ごとのファイル（`loot` / `law` / `theft` / `office` / `prison`）は `install(env)` を1つ持ち、
ここの `Env` を受け取る。設定は入口（`crime_overhaul.py`）の定数で、`env.cfg.<名前>` をその場で読む
（ローダは apply() の前に入口の定数へ書き込む。後から変えた値も読めるよう、控えない）。

ゲームの同じ入口を 336 の中で何度も包まないよう、機能はここに登録し、入口が配る。
選択肢はローダの窓口 `choices`（TECH.md §3.3.14）を通す。自前のボタンは宣言型の口（`on_offer` / `on_claim` /
`on_screen`）で組み直しのたびに状態から作り、ゲームの一覧を直すもの（判決の書き換え・掲示板の間引き）だけ
`on_refresh` に残す。本文（`add_text`）は入口が1枚だけ包んで配る。
"""
import os
import sys

from instantale_modloader import choices, guards, ui
from instantale_modloader import state as loader_state

LOG_BASENAME = "crime_overhaul.log"
FUNCTIONS_MODULE = "scripts.functions"
#: 人生の記録の文を作るゲーム自身の関数（`get_life_log_text`）のモジュール。
LIFE_LOG_MODULE = "scripts.llm.context_manager"
STATE_DIRNAME = "crime_overhaul"
STORE_ATTR = "_instantale_crime_incentive"
#: 版2までの自前のボタンの印のキー（他の MOD と別のキー）。いまは宣言型の口で組むので付けない。
#: セーブから戻った前の版の印のボタンを片付けるためだけに残す（`drop_old_marks`）。
MARK = "mod_crime_incentive_action"
#: 施設の選択肢だと見なす目印（出口のマネージャ。`309_` と同じ）。
FACILITY_MARK = "MovePhaseManager"
#: ゲーム自身の衛兵戦を起こすときに `process_choice` へ渡す文字列（GAME.md §2.20）。
GUARD_CHOICE_TEXT = "衛兵"


def life_log_text(app, character, limit):
    """ゲーム自身の作り方の人生の記録を、頼みに載せる文にする。読めなければ空。`limit` 字で切る。"""
    module = sys.modules.get(LIFE_LOG_MODULE)
    build = getattr(module, "get_life_log_text", None)
    try:
        value = build(app, character) if callable(build) else None
    except Exception:
        value = None
    if not value:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


#: 片付いた裏の仕事と処刑場からの脱出の控え（`state\` の周回の鍵の下）。
#: 進行中の控え（`underworld` / `rescue`）は片付くと消すので、依頼人と「表に出さない依頼」の印はこちらに残す。
PAST_KEY = "kept_past"


def remember_past(bucket, quest_id, title, client="", name=""):
    """片付いた依頼を `PAST_KEY` に残す。呼び側が錠を持ち、保存する。"""
    past = bucket.get(PAST_KEY)
    if not isinstance(past, dict):
        past = bucket[PAST_KEY] = {}
    past[str(quest_id)] = {"title": title or "", "client": str(client or ""), "name": name or ""}


def past_of(app, bucket):
    """`PAST_KEY` のうち、いまのセーブに同じ題で残っている依頼。`{依頼の id: 控え}`。

    依頼の id は作り直した周回で使い回されうるので、題も見る（`rescue.is_ours` と同じ）。
    """
    past = bucket.get(PAST_KEY)
    if not isinstance(past, dict):
        return {}
    found = {}
    for quest_id, record in past.items():
        if not isinstance(record, dict):
            continue
        quest = ui.quest_of(app, str(quest_id))
        if quest is None:
            continue
        title = record.get("title")
        if title and ui.quest_value(quest, "quest_title", "") != title:
            continue
        found[str(quest_id)] = record
    return found


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


def drop_old_marks(buttons):
    """前の版の印（`MARK`）の付いたボタンを片付ける。片付けた文言を返す。

    版2までは自前のボタンを一覧に置き、ゲームのボタン（「大人しく捕まる」・掲示板の枠）にも印を付けていた。
    ローダの印の付け直しで戻った分のうち、無害な spec の自前のボタンは外し、ゲームのボタンからは印だけ外す。
    """
    if not isinstance(buttons, list):
        return []
    dropped, kept = [], []
    for entry in buttons:
        if not isinstance(entry, dict) or MARK not in entry:
            kept.append(entry)
            continue
        dropped.append(entry.get("text"))
        if ui.spec_cls_name(entry) == ui.SAFE_CLS:
            continue
        entry.pop(MARK, None)
        kept.append(entry)
    if dropped:
        buttons[:] = kept
    return dropped


class Env(object):
    """機能のファイルへ渡す土台。"""

    def __init__(self, ctx, cfg):
        self.ctx = ctx
        self.cfg = cfg
        self.write = ctx.logger(LOG_BASENAME)
        self.owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "336_crime_overhaul"
        self.screen = ui.Screen(ctx, self.write, tag="crime incentive", mark=MARK)
        # 控えは `state\crime_overhaul\<世界×主人公>.json` に1つ。機能ごとに鍵を分ける
        # （`away` 時効 / `theft` 店で盗む / `underworld` 裏の仕事 / `jail` 脱獄）。
        store = _store()
        if store["worlds"] is None:
            store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=self.write)
        self.worlds = store["worlds"].rebind(ctx, self.write)
        self.refresh_handlers = []
        self.offer_handlers = []
        self.claim_handlers = []
        self.screen_builders = {}
        self.label_handlers = []
        #: 窓口 `choices` の持ち主の名前（入口の `provide` が返す）。MOD だけの画面を開く・閉じるのに使う。
        self.choices_owner = None
        self.text_filters = []
        #: 裁判へ持ち越す介入（衛兵の買収が突き返されたら `{"bribe": "caught"}`）。次の裁判が始まったら空にする。
        #: ロードを挟んでも続くよう、世界×主人公の控え（`carry_to_trial`）にも持つ（`carry` / `take_carry`）。
        self.carry_to_trial = {}
        #: `carry_to_trial` がどの周回のものか。周回が替わったらメモリの分は持ち越さない。
        self.carry_playthrough = None
        #: 次の依頼の生成（`random_quest_generator`）へ差し込む指示と難易度。1回で使い切る（`office` が包む）。
        #: 裏の仕事（`office`）と処刑場からの脱出（`rescue`）が使う。
        self.quest_inject = {"brief": None, "difficulty": None, "at": 0.0, "tag": ""}
        #: 品の値段の段。`fn(item, key, price) -> 新しい額 | None`。`law` が置く1枚（`prices.adjust` は持ち主ごとに1枚）の中で、
        #: 「店が委縮して値を下げる」の後に順に通す。盗品の買い取り額（`theft`）が使う。
        self.price_layers = []
        #: 盗品買取商の売買の窓を開いている間の相手（主の id）。閉じたら None。`opening` は押してから窓が出るまで（`fence`）。
        self.fence = {"broker": None, "opening": False}
        #: 機能どうしの口（入れたファイルが置く）。`stolen_entry(app, item)` は盗品の控え（`theft`）、
        #: `underworld_banned(app)` は司法取引の締め出しの間か（`office`）。
        self.stolen_entry = lambda app, item: None
        self.underworld_banned = lambda app: False
        #: 牢の出来事を受ける関数（`cellmate` が受ける）。`prison` / `rescue` が `prison_event` で知らせる。
        #: "break"（決行の戦闘を起こす直前）/
        #: "exit"（牢を出た。how は "release" / "escape" / "rescue"）。
        self.prison_handlers = {"break": [], "exit": []}

    def carry(self, app, key, value):
        """裁判へ持ち越す介入を置く（`value` が None なら外す）。メモリと控えの両方。"""
        playthrough = self.worlds.playthrough(app)
        if playthrough != self.carry_playthrough:
            self.carry_to_trial.clear()         # 別の周回のメモリを混ぜない
            self.carry_playthrough = playthrough
        if value is None:
            self.carry_to_trial.pop(key, None)
        else:
            self.carry_to_trial[key] = value
        with self.worlds.lock:
            bucket = self.worlds.load(playthrough)
            if self.carry_to_trial:
                bucket["carry_to_trial"] = dict(self.carry_to_trial)
            else:
                bucket.pop("carry_to_trial", None)
            self.worlds.save(playthrough)

    def take_carry(self, app):
        """持ち越した介入を受け取って空にする（裁判が始まったとき）。ロードで消えたメモリは控えから補う。"""
        playthrough = self.worlds.playthrough(app)
        with self.worlds.lock:
            bucket = self.worlds.load(playthrough)
            stored = bucket.pop("carry_to_trial", None)
            if stored is not None:
                self.worlds.save(playthrough)
        taken = dict(stored) if isinstance(stored, dict) else {}
        if playthrough == self.carry_playthrough:
            taken.update(self.carry_to_trial)
        self.carry_to_trial.clear()
        return taken

    def on_prison(self, kind, handler):
        """牢の出来事 `kind` で `handler(app, **kw)` を呼ぶ。"""
        self.prison_handlers[kind].append(handler)

    def prison_event(self, kind, app, **kw):
        """牢の出来事を知らせる。受けた側の例外は握る（知らせる側の流れを止めない）。"""
        for handler in list(self.prison_handlers.get(kind) or []):
            try:
                handler(app, **kw)
            except Exception:
                self.ctx.log_exc("crime incentive: a prison handler failed ({})".format(kind))

    # ---------------------------------------------------- 振り分けの登録
    def on_refresh(self, handler):
        """選択肢を組み直した合図で `handler(app, buttons)` を呼ぶ（`buttons` はゲームの一覧そのもの）。"""
        self.refresh_handlers.append(handler)

    def on_offer(self, handler):
        """組み直しのたびに `handler(app, view)` を呼び、返した `choices.Choice` の並びを画面に足す（窓口の `offer`）。"""
        self.offer_handlers.append(handler)

    def on_claim(self, handler):
        """その画面を自前のボタンだけにするなら `handler(app, view)` が `Choice` の並びを返す（窓口の `claim`）。"""
        self.claim_handlers.append(handler)

    def on_screen(self, name, builder):
        """MOD だけの画面 `name` のボタンを `builder(app, view, data)` で組む（窓口の `screens`）。"""
        self.screen_builders[name] = builder

    def on_labels(self, handler):
        """前の版がセーブに焼いた自前のボタンの文言を `handler(app)` が返す（窓口の `labels`）。"""
        self.label_handlers.append(handler)

    def open_screen(self, app, name, data=None, repaint=True):
        """MOD だけの画面を開く（下のゲームの一覧は窓口が残す）。"""
        choices.open_screen(app, self.choices_owner, name, data, repaint_now=repaint)

    def close_screen(self, app, repaint=True):
        """336 の MOD だけの画面を閉じる。閉じたら真。"""
        return choices.close_screen(app, self.choices_owner, repaint_now=repaint)

    def screen_open(self, app, name):
        """336 の MOD だけの画面 `name` が開いているか。"""
        row = choices.opened(app)
        return bool(row) and row.get("owner") == self.choices_owner and row.get("name") == name

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
        """施設の選択肢に戻す（MOD だけの画面を閉じて組み直す。自前のボタンは窓口が状態から組み直す）。"""
        self.write("back to the facility ({})".format(why))
        self.close_screen(app, repaint=False)
        choices.repaint(app)

    def call_guards(self, app):
        """ゲーム自身の衛兵戦を起こす（強さはゲームのまま）。起こせなければ施設の選択肢に戻す。"""
        phase = guards.build(app)
        if phase is None:
            self.back_to_shop(app, "no guards")
            return
        if not self.screen.start_phase(app, phase, GUARD_CHOICE_TEXT):
            self.back_to_shop(app, "guards did not start")
