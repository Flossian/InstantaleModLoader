# -*- coding: utf-8 -*-
"""選択肢ボタンと画面描画の共通部品。

ここに置いてあるのは **実機で確かめた事実だけ**。同じ発見を mod ごとに書き直すと
「片方に入って片方に入っていない」状態になる ― 実際に `301_` と `302_` で起きた:

  * `302_` が4回外してようやく突き止めた**描画の経路**（`paint`）が `301_` には
    無く、`301_` は `refresh_choice_buttons` を直接呼ぶだけだった。それでは
    画面は塗り替わらない（`302_` の実測）
  * `301_` が痛い目に遭った**会話を閉じずに画面を変える**（立ち絵が付いてくる）を
    `302_` は取り込んだが、`302_` が足した **`end_text` の差し替え**は `301_` に
    無かった
  * `300_` が確立した**手が空くまで待つ**（移動の後始末の最中に割り込むと
    噛み合わない）は、会話終了の後始末にもそのまま当てはまるのに、どちらの mod も
    固定待ちだった

以後、この種の知見は判明した時点でここへ移し、全部の mod がここを使う。

## 分かっている事実

    app.buttons               [{'text': 表示文字列, 'spec': PhaseSpec}]
    app.to_display_buttons    表示中の文字列のリスト
    app.display_button_map    表示位置 -> buttons の添字
    app.refresh_choice_buttons(reset_page=True)
        to_display_buttons / display_button_map を組み直す。**ここまでで
        画面は塗り替わらない**
    InstanTaleHUD.update_button_texts(self, instance, value)
        実際に塗っているのはこれ。Kivy のプロパティ監視で、監視対象は **HUD 側**に
        あり `app.to_display_buttons` は監視されていない（空にして入れ直しても
        dispatch が1本も出なかった）。だから **直接呼ぶのが正解**

`PhaseSpec(cls_name, args)` はマネージャのインスタンスではなく**その作り方**。
押されると `getattr(__main__, cls_name)(app, *args)` が組み立てられ
`app.process_choice(それ, ボタン文字列)` に渡る。

## 触ってはいけないこと

  * **`PhaseSpec` に自前のクラス名を書かない。** `to_dict()` がある ＝ ボタンは
    セーブに焼かれうる。注入はプロセスと一緒に消えるので、mod 無しの次回起動で
    `getattr(__main__, ...)` が必ず失敗する。自前ボタンには無害な
    `JustSetButtonToNormalPhase` を持たせ、押下はボタン辞書の**印**で横取りする
  * **印のキーは mod ごとに別にする。** 共有すると、相手の
    `on_button_press` が自分のボタンを握り潰す
  * **`app.buttons` を書き換えて `refresh_choice_buttons()` を直接呼ぶだけでは
    駄目。** 押下と同じ流れの中で差し替えると、ゲームがその後に描画するので古い
    画面に戻る。`apply_buttons` が `Clock.schedule_once(..., 0)` に載せて
    「次のフレーム・メインスレッド」で行うのはこのため
"""

from __future__ import annotations

import time

# HUD は属性名で探さず、この型で見分ける（属性名は決めつけない）。
HUD_MODULE = "scripts.hud.new_hud"
HUD_CLASS = "InstanTaleHUD"

# 自前ボタンに持たせる無害な既存クラス。mod 無しで押されても選択肢が戻るだけ。
SAFE_CLS = "JustSetButtonToNormalPhase"

# 会話の終了処理は要約で LLM を回すことがあるので、待ちは長めに取る。
END_POLL = 0.3
END_TIMEOUT = 120.0

# 「手が空く」のを待つ設定（`300_` の実測値）。
IDLE_POLL = 0.3
IDLE_TIMEOUT = 30.0
IDLE_SETTLE = 0.6

# 手が空いているかの判定に使う信号（`300_` で実測。フラグ名を信用せず、
# 実際に効くと確かめられたものだけを並べてある）。
#   is_adding_text          テキストを流している最中
#   is_button_enabled       操作を受け付けているか（False なら待つ）
#   is_popup_window_opened  買い物窓などが開いている
#
# **`in_shopping` は入れない。** 店の外を往復しているだけの 38 回の移動すべてで
# True のままだった（`300_` の実測）。フラグ名が意味するとおりに動いているとは
# 限らないので、状態の判定に使う前に必ず実測で裏を取ること。
IDLE_SIGNALS = ("is_adding_text", "is_button_enabled", "is_popup_window_opened")


# --------------------------------------------------------------------------
# 読み取りだけの道具（状態を変えないので、そのまま関数で置く）
# --------------------------------------------------------------------------
def main_module():
    import sys
    return sys.modules.get("__main__")


def cls_of(name):
    """`__main__` のクラスを名前で引く。無ければ None。"""
    module = main_module()
    cls = getattr(module, name, None) if module is not None else None
    return cls if isinstance(cls, type) else None


def find_app():
    """走っている `InstantaleApp` を探す。"""
    module = main_module()
    cls = getattr(module, "InstantaleApp", None)
    try:
        from kivy.app import App
        app = App.get_running_app()
        if app is not None and (cls is None or isinstance(app, cls)):
            return app
    except Exception:
        pass
    if module is not None and isinstance(cls, type):
        try:
            values = list(vars(module).values())
        except Exception:
            values = []
        for value in values:
            if isinstance(value, cls):
                return value
    return None


def spec_of(entry):
    if not isinstance(entry, dict):
        return None
    return entry.get("spec")


def spec_data(spec):
    """`PhaseSpec` を `{'cls_name':..., 'args':[...]}` として読む。"""
    if spec is None:
        return None
    try:
        to_dict = getattr(spec, "to_dict", None)
        if callable(to_dict):
            data = to_dict()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    name = getattr(spec, "cls_name", None)
    args = getattr(spec, "args", None)
    if isinstance(name, str):
        return {"cls_name": name,
                "args": list(args) if isinstance(args, (list, tuple)) else []}
    try:
        data = dict(vars(spec))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def spec_cls_name(entry):
    """ボタンが呼ぶクラス名。**画面の見分けは文字列ではなくこれで行う。**

    表記や言語設定に依存しないし、依頼一覧そのもの（`QuestChoiceManager` が
    並ぶ）には会話・施設の目印が無いので入れ子にならない。
    """
    data = spec_data(spec_of(entry))
    name = data.get("cls_name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def spec_args(entry):
    """ボタンの spec の args。**読むだけ** ― 値の意味は解釈しない。

    セーブのフィールド値をそのまま引数の語彙だと決めつけてゲームを落とした
    ことがある（TECH.md GAME.md §2.2）。ボタンに載っている args をそのまま使えば、
    値が何を意味するのか知らなくても正しく起こせる。
    """
    data = spec_data(spec_of(entry))
    args = data.get("args") if isinstance(data, dict) else None
    return list(args) if isinstance(args, (list, tuple)) else None


def find_spec_button(buttons, cls_name):
    """その `cls_name` を呼ぶボタンを1つ返す。無ければ None。"""
    if not isinstance(buttons, (list, tuple)):
        return None
    for entry in buttons:
        if spec_cls_name(entry) == cls_name:
            return entry
    return None


def conversation_partner(buttons):
    """会話画面なら `(相手の id, 「会話を終了する」ボタン)` を返す。

    `ConversationEndManager.__init__(self, app, in_conversation_id, finisher,
    end_text)` なので **`args[0]` がいま話している相手**（実セーブで確認)。
    `ConversationStartManager.__init__` を追跡しなくても、ボタンを**読むだけ**で
    相手が分かる。
    """
    entry = find_spec_button(buttons, "ConversationEndManager")
    if entry is None:
        return None, None
    args = spec_args(entry)
    return (str(args[0]) if args else None), entry


def pressed_entry(app, button_index):
    """押された添字から `app.buttons` の要素を引く。

    **地図があるなら地図を使う。** ゲーム自身が
    `display_button_map[button_index]` で添字を引き直していることは、事故時の
    フレームローカルに `mapped_button = 1` が残っていたことで確定した
    （TECH.md GAME.md §2.2）。恒等写像なら結果は同じ、恒等でなければこちらが正しい。
    """
    buttons = getattr(app, "buttons", None)
    if not isinstance(buttons, (list, tuple)) or not isinstance(button_index, int):
        return None
    index = button_index
    mapping = getattr(app, "display_button_map", None)
    if isinstance(mapping, (list, tuple)) and 0 <= button_index < len(mapping):
        mapped = mapping[button_index]
        if isinstance(mapped, int):
            index = mapped
    if 0 <= index < len(buttons):
        return buttons[index]
    return None


def find_hud(app):
    """HUD のインスタンスを探す。**属性名は決めつけない。**

    `scripts.hud.new_hud.InstanTaleHUD` かどうか、つまり**型**で見分ける。
    """
    import sys
    module = sys.modules.get(HUD_MODULE)
    cls = getattr(module, HUD_CLASS, None) if module is not None else None
    if not isinstance(cls, type):
        return None
    if isinstance(getattr(app, "root", None), cls):
        return app.root
    try:
        values = list(vars(app).values())
    except Exception:
        return None
    for value in values:
        if isinstance(value, cls):
            return value
    return None


def busy_signals(app):
    """手が空いていない理由を並べる。空なら手が空いている。"""
    reasons = []
    if getattr(app, "is_adding_text", False):
        reasons.append("is_adding_text")
    if not getattr(app, "is_button_enabled", True):
        reasons.append("is_button_enabled=False")
    if getattr(app, "is_popup_window_opened", False):
        reasons.append("is_popup_window_opened")
    return reasons


def is_idle(app):
    return not busy_signals(app)


# --------------------------------------------------------------------------
# エリアの引き当て（`302_` の実測）
# --------------------------------------------------------------------------
def world_areas(app):
    """エリア表 `{id: Area}`。**属性名ではなく中身で見分ける。**"""
    world = getattr(app, "world", None)
    if world is None:
        return {}
    for name in ("areas", "area_dict", "areas_dict"):
        value = getattr(world, name, None)
        if isinstance(value, dict) and value:
            return value
    try:
        items = list(vars(world).items())
    except Exception:
        return {}
    for _name, value in items:
        if isinstance(value, dict) and value:
            sample = next(iter(value.values()))
            if getattr(sample, "nodes", None) is not None:
                return value
    return {}


def current_area(app):
    """いまプレイヤーが居るエリア。**id で持っている場合も引き当てる。**

    `player.current_area` はエリアのオブジェクトとは限らない ― NPC 側のセーブでは
    `"7"` という id の文字列だった。持ち方を決めつけて2度外している（`302_`）ので、
    どちらでも引き当てる。
    """
    value = getattr(getattr(app, "player", None), "current_area", None)
    if isinstance(value, (str, int)):
        return world_areas(app).get(str(value))
    return value


def area_id_of(area):
    return str(getattr(area, "id", "")) if area is not None else ""


# --------------------------------------------------------------------------
# 施設の引き当て（`302_` の実測。`303_` にも同じものが要ったのでここへ移した）
# --------------------------------------------------------------------------
# 施設はエリアの直下ではなく**ノードの下**にぶら下がっている（実セーブ）:
#
#     areas[area_id].nodes[node_id].facilities[facility_id]
#
# `initial_location` は `{"area": "7", "node": null, "facility": "127"}` の形で、
# **`node` が null のことがある**。だから施設は id だけを頼りにノードを総当たり
# して探す（`find_facility`）。
#
# 実在する `facility_type`（実セーブで確認・2026-07-26）:
#   entrance / exit / ward / guild / inn / general_store / specialty_shop /
#   blacksmith / medical_facility / administrative_office / underworld_office /
#   colosseum / slave_market / location / dungeon_location
GUILD_FACILITY_TYPE = "guild"


def nodes_of(area):
    """エリアの下のノード一覧。辞書でも配列でも同じ形で返す。"""
    nodes = getattr(area, "nodes", None)
    if isinstance(nodes, dict):
        return list(nodes.values())
    if isinstance(nodes, (list, tuple)):
        return list(nodes)
    return []


def facilities_of(node):
    """ノードの下の施設 `{id: Facility}`。無ければ空の辞書。"""
    facilities = getattr(node, "facilities", None)
    return facilities if isinstance(facilities, dict) else {}


def facility_type_of(facility):
    """`facility_type`。属性でも辞書でも読む。取れなければ空文字。"""
    value = getattr(facility, "facility_type", None)
    if value is None and isinstance(facility, dict):
        value = facility.get("facility_type")
    return value if isinstance(value, str) else ""


def find_facility(area, facility_id):
    """エリアの中から id で施設を引く。`(施設, ノード)`。

    ノードも一緒に返すのは `move_npc_to_facility(character_id,
    character_instance, target_facility, target_node=None, ...)` が施設とノードを
    **別々に**取るため。
    """
    if area is None or not facility_id:
        return None, None
    target = str(facility_id)
    for node in nodes_of(area):
        for key, facility in facilities_of(node).items():
            if str(key) == target or str(getattr(facility, "id", "")) == target:
                return facility, node
    return None, None


def find_guild(area, facility_type=GUILD_FACILITY_TYPE):
    """そのエリアのギルド。`(施設, ノード)`。無ければ `(None, None)`。

    ダンジョンや野外のエリアにはギルドが無い ― **見つからないことが正常な答え**
    なので、呼ぶ側はそこで別の置き場所へ下がること。
    """
    if area is None:
        return None, None
    for node in nodes_of(area):
        for facility in facilities_of(node).values():
            if facility_type_of(facility) == facility_type:
                return facility, node
    return None, None


def facility_name(app, facility, limit=40):
    """施設の名前。取れなければ空文字（呼ぶ側は場所抜きの文言に切り替える）。

    施設そのものが渡ってくるとは限らない ― id の文字列で持っていることがあるので、
    そのときは世界の施設表から引き直す。
    """
    if facility is None:
        return ""
    name = getattr(facility, "name", None)
    if isinstance(name, str) and name.strip():
        return _short(name, limit)
    if isinstance(facility, dict):
        value = facility.get("name")
        return _short(value, limit) if isinstance(value, str) else ""
    world = getattr(app, "world", None)
    for attr in ("facilities", "locations"):
        table = getattr(world, attr, None)
        if isinstance(table, dict):
            found = table.get(str(facility))
            if found is not None:
                return facility_name(app, found, limit)
    return ""


def _short(value, limit):
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


# --------------------------------------------------------------------------
# 画面を触る側（ログと例外処理が要るので ctx を握る）
# --------------------------------------------------------------------------
class Screen(object):
    """1つの mod から見た「選択肢と画面」。

        screen = ui.Screen(ctx, write, tag="quest offer", mark=MARK)

    `write` は mod 自身のログ関数。何が起きたかは mod のログに残したいので、
    ローダのログ（`ctx.log`）とは分けてある。`mark` はボタン辞書に付ける印の
    キーで、**mod ごとに別の文字列**にすること。
    """

    def __init__(self, ctx, write, tag="mod", mark=None, safe_cls=SAFE_CLS):
        self.ctx = ctx
        self.write = write
        self.tag = tag
        self.mark = mark
        self.safe_cls = safe_cls

    # -- 例外を外へ出さないための土台 ---------------------------------------
    def _oops(self, what):
        self.ctx.log_exc("{}: {}".format(self.tag, what))

    def guarded(self, fn):
        """Clock から呼ばれる処理を包む。ここで投げるとゲームを巻き込む。"""
        try:
            return fn()
        except Exception:
            self._oops("scheduled call failed")
            return None

    def schedule(self, fn, delay=0.0):
        """メインスレッドの次のフレーム（以降）で走らせる。

        `Clock.schedule_once(..., 0)` は「次のフレーム」かつ「メインスレッド」。
        順序とスレッドの両方が同時に片付くので、UI を触る処理は必ずここを通す。
        """
        try:
            from kivy.clock import Clock
        except Exception:
            self._oops("kivy Clock unavailable")
            return False
        Clock.schedule_once(lambda _dt: self.guarded(fn), delay)
        return True

    def _interval(self, fn, poll):
        try:
            from kivy.clock import Clock
        except Exception:
            self._oops("kivy Clock unavailable")
            return False
        Clock.schedule_interval(fn, poll)
        return True

    # -- ボタンを作る -------------------------------------------------------
    def make_spec(self, cls_name, args=()):
        spec_cls = cls_of("PhaseSpec")
        if spec_cls is None:
            return None
        try:
            return spec_cls(cls_name, list(args))
        except Exception:
            self._oops("PhaseSpec({!r}) failed".format(cls_name))
            return None

    def button(self, text, mark=None, cls_name=None, args=(), extra=None):
        """自前のボタンを1つ作る。作れなければ None（呼び出し側が諦める）。

        `cls_name` を省略すると無害な `JustSetButtonToNormalPhase` になる
        （自前クラス名を spec に書かない、の実装）。`mark` は
        `on_button_press` での横取りに使う印。
        """
        spec = self.make_spec(cls_name or self.safe_cls, args)
        if spec is None:
            return None
        entry = {"text": text, "spec": spec}
        if mark is not None and self.mark:
            entry[self.mark] = mark
        if isinstance(extra, dict):
            entry.update(extra)
        return entry

    def mark_of(self, entry):
        """このボタンが自分のものならその action、違えば None。

        判定は文字列ではなく印。同じ文字列のゲーム側ボタンを巻き込まないため。
        """
        if not self.mark or not isinstance(entry, dict):
            return None
        return entry.get(self.mark)

    def instantiate_spec(self, app, entry_or_spec):
        """ボタンの `PhaseSpec` から、それが呼ぶはずのマネージャを組み立てる。

        **引数を自分で考えない**のが要点。`QuestChoiceManager` の `quest_type` を
        推測して組み立ててゲームを落とした前科がある（TECH.md GAME.md §2.2）。
        ゲームが既にボタンへ載せている `cls_name` と `args` をそのまま使えば、
        値の意味を知らなくても正しく起こせる。
        """
        spec = entry_or_spec
        if isinstance(entry_or_spec, dict):
            spec = spec_of(entry_or_spec)
        data = spec_data(spec)
        if not isinstance(data, dict):
            return None
        cls = cls_of(data.get("cls_name"))
        if cls is None:
            return None
        try:
            return cls(app, *list(data.get("args") or []))
        except Exception:
            self._oops("cannot instantiate {!r}".format(data.get("cls_name")))
            return None

    # -- 画面に反映させる ---------------------------------------------------
    def say(self, app, text):
        try:
            app.add_text(text)
        except Exception:
            self._oops("add_text failed")

    def refresh(self, app):
        try:
            app.refresh_choice_buttons(reset_page=True)
            return True
        except Exception:
            self._oops("refresh_choice_buttons failed")
            return False

    def paint(self, app, texts):
        """選択肢の文字列を実際に画面へ塗る。効いた手段の一覧を返す。

        `refresh_choice_buttons` は `to_display_buttons` と `display_button_map`
        を組み直すところまでで、**そこまで正しくても画面は塗り替わらない**
        （実機・2026-07-26）:

            confirm: to_display_buttons ['ここで別れる',...] -> ['ああ、…','やめておく']
            （それでも画面は古いまま）

        塗っているのは HUD 側の `InstanTaleHUD.update_button_texts(self,
        instance, value)`。上限付きのトレースで誰が起こしているかを実測した:

            update_button_texts(ObservableList [...]) <- InstanTaleHUD  ゲーム自身
            update_button_texts(list ['ああ、…','やめておく']) <- InstantaleApp  こちら

        **監視されているプロパティは HUD 側にあり、`app.to_display_buttons` は
        監視対象ではない**（空にして入れ直しても dispatch が1本も出なかった）。
        だからそこをどう触っても画面は変わらない ― この関数を直接呼ぶのが正解。

        ついでに `display_button_load(self, dt)`（ゲーム自身のボタン読み込み。
        Clock コールバックの形なので `dt` を渡せば直接呼べる）も通す。
        描画のためにゲームを落とさないよう、例外はどれも外へ出さない。
        """
        done = []

        loader = getattr(app, "display_button_load", None)
        if callable(loader):
            try:
                loader(0)
                done.append("display_button_load")
            except Exception:
                self._oops("display_button_load failed")

        hud = find_hud(app)
        updater = getattr(hud, "update_button_texts", None) if hud is not None else None
        if callable(updater):
            try:
                updater(app, list(texts))
                done.append("hud.update_button_texts")
            except Exception:
                self._oops("hud.update_button_texts failed")
        elif hud is None:
            # ここが出たら画面は塗り替わらない。型で探して見つからない＝
            # HUD の構成が変わったということなので、その合図として残す。
            done.append("hud not found")

        return done

    def apply_buttons(self, app, entries, tag):
        """選択肢を差し替えて画面に反映する。**必ず次のフレーム**で行う。

        実機（2026-07-26）: 押下と同じ流れの中で差し替えると `app.buttons` は
        変わるのに**画面は古いまま**だった。見えているものと押されるものが
        食い違うので、確認画面が出ていないように見えて裏では新しい選択肢が
        押せてしまう（ユーザー報告: 連打したら別れられた）。

        ゲーム自身は押下の処理の中で描画しているので、こちらの差し替えはその
        **後**に置く必要がある。`Clock.schedule_once(..., 0)` なら次のフレーム、
        かつメインスレッドなので順序とスレッドが同時に片付く
        （選択肢を組むゲーム側の `execute` は別スレッドで走ることがある）。

        `entries` に None を渡すと差し替えはせず、いま `app.buttons` に入って
        いるものを塗り直すだけ（ゲームが組んだ一覧に手を入れた場合用）。
        """
        def commit():
            before = list(getattr(app, "to_display_buttons", []) or [])
            if entries is not None:
                app.buttons = entries
            self.refresh(app)
            after = list(getattr(app, "to_display_buttons", []) or [])
            done = self.paint(app, after)
            self.write("{}: to_display_buttons {} -> {} via {}".format(
                tag, before, after, "+".join(done) if done else "(nothing)"))

        return self.schedule(commit, 0)

    # -- ゲームと同じ経路で自前のフェーズを起こす ---------------------------
    def start_phase(self, app, phase, choice_text, fallback=None):
        """`app.process_choice` に自前のフェーズを渡す。

        ゲーム自身は選択肢を変えるとき必ず `process_choice(マネージャ, 文字列)`
        を通し、その中で `execute` が別スレッドへ渡される。描画の面倒はその経路が
        見ているので、同じ経路に乗せる。フェーズは `execute(choice_text)` だけを
        持つ自前クラスでよい ― **`PhaseSpec` には決して載せない。**
        """
        try:
            app.process_choice(phase, choice_text)
            return True
        except Exception:
            self._oops("process_choice failed; falling back")
        if fallback is not None:
            try:
                fallback()
            except Exception:
                self._oops("direct dispatch failed")
        return False

    # -- 手が空くのを待つ ---------------------------------------------------
    def when_idle(self, app, then, timeout=IDLE_TIMEOUT, settle=IDLE_SETTLE,
                  poll=IDLE_POLL, cancel_if=None, proceed_on_timeout=False,
                  tag="idle"):
        """手が空いてから `then` を走らせる。

        移動の後始末（テキストの流し込み・ボタンの張り替え）の最中に割り込むと
        噛み合わない（`300_` の実測）。**会話の終了処理も同じ**で、要約の
        流し込みが続いている間に掲示板を開いたり `add_text` したりすると、
        こちらの出力が押し流される。

        `cancel_if` は理由の文字列（または None）を返す関数。前提が崩れたら
        取り消す（待っている間に施設を出た、戦闘に入った等）。
        `proceed_on_timeout` は「待ちきれなくても実行する」― 既に確定した
        行動の後始末では、遅れても実行する方が正しい。

        既に手が空いているなら見張りは立てず、その場で予約する（無駄に
        1ポーリング分待たないため）。
        """
        deadline = time.monotonic() + timeout

        def tick(_dt):
            try:
                if cancel_if is not None:
                    reason = cancel_if()
                    if reason:
                        self.write("{}: cancelled ({})".format(tag, reason))
                        return False
                busy = busy_signals(app)
                if not busy:
                    self.schedule(then, settle)
                    return False
                if time.monotonic() > deadline:
                    if proceed_on_timeout:
                        self.write("{}: still busy {} after {:.0f}s; going ahead"
                                   .format(tag, busy, timeout))
                        self.schedule(then, 0)
                    else:
                        self.write("{}: gave up waiting for idle ({:.0f}s) {}"
                                   .format(tag, timeout, busy))
                    return False
                return True
            except Exception:
                self._oops("{}: idle watch failed".format(tag))
                return False

        # 1回目はその場で見る。False が返ったなら片が付いている
        # （実行を予約した、または取り消した）ので見張りは要らない。
        if tick(0.0) is False:
            return True
        return self._interval(tick, poll)

    # -- 会話を閉じる -------------------------------------------------------
    def end_conversation(self, app, end_entry, follow_up, end_text=None,
                         on_abort=None, poll=END_POLL, timeout=END_TIMEOUT,
                         tag="end conversation"):
        """会話をゲーム自身の経路で閉じてから `follow_up(app)` を走らせる。

        閉じずに画面を変えると **NPC の立ち絵が消えずに移動しても付いてくる**
        （`301_` で実際に起きた）。会話は「状態」であって画面ではない ―
        立ち絵の片付けも関係値の更新も会話の要約も終了処理の中にある。

        起こし方は「画面にある『会話を終了する』ボタンの spec をそのまま使い、
        **`end_text` だけ差し替える**」。`end_text` は
        `'<行動: 会話を終了する>'` という自由記述なので、そこに事情を書いておけば
        会話の要約とライフログにその通り残る。引数の意味を推測せずに済むうえ、
        記録も正しくなる（`302_` で確立）。

        終了処理は要約で LLM を回すことがあるので、`in_conversation` が落ちるのを
        見張り、落ちてから**手が空くのを待って** `follow_up` を呼ぶ。

        `on_abort(理由)` は閉じられなかったときに呼ばれる。**待ちが打ち切られる
        経路が必ずあるので、呼び出し側は「実行中」の印をここで戻すこと**
        （でないと以後ずっとボタンが効かなくなる）。戻り値は同期的に失敗しなかったか。
        """
        def abort(reason):
            self.write("{}: aborted ({})".format(tag, reason))
            if on_abort is not None:
                try:
                    on_abort(reason)
                except Exception:
                    self._oops("{}: on_abort failed".format(tag))

        if not getattr(app, "in_conversation", False):
            # もう会話が終わっている（施設側から入った等）。そのまま進む。
            self.write("{}: not in a conversation; continuing".format(tag))
            return self.when_idle(app, lambda: follow_up(app),
                                  proceed_on_timeout=True, tag=tag)

        manager = self._end_manager(app, end_entry, end_text)
        if manager is None:
            abort("could not build ConversationEndManager")
            return False

        text = (end_entry or {}).get("text") or "会話を終了する"
        self.write("{}: process_choice(ConversationEndManager, {!r})".format(tag, text))
        try:
            app.process_choice(manager, text)
        except Exception:
            self._oops("ending the conversation failed")
            abort("process_choice raised")
            return False

        deadline = time.monotonic() + timeout

        def wait_for_end(_dt):
            try:
                if getattr(app, "in_conversation", False):
                    if time.monotonic() > deadline:
                        abort("timed out after {:.0f}s".format(timeout))
                        return False
                    return True
                self.write("{}: closed; continuing".format(tag))
                # 要約の流し込みが続いていることがあるので、手が空くまで待つ。
                # 行動は既に確定しているので、待ちきれなくても実行する。
                self.when_idle(app, lambda: follow_up(app),
                               proceed_on_timeout=True, tag=tag)
                return False
            except Exception:
                self._oops("{}: watch failed".format(tag))
                abort("watch failed")
                return False

        if not self._interval(wait_for_end, poll):
            # 見張りを立てられなかった（Clock が無い）。閉じる指示は既に出して
            # いるので、呼び出し側の「実行中」の印は必ず戻す。
            abort("cannot watch for the conversation to close")
            return False
        return True

    def _end_manager(self, app, end_entry, end_text):
        """画面のボタンの args を写し、`end_text` だけ差し替えて組み立てる。"""
        end_cls = cls_of("ConversationEndManager")
        if end_cls is None:
            return None
        args = spec_args(end_entry) or []
        if end_text is not None and len(args) >= 3:
            new_args = list(args)
            new_args[2] = end_text
            try:
                return end_cls(app, *new_args)
            except Exception:
                self._oops("ConversationEndManager({!r}) failed".format(new_args))
        # 差し替えに失敗したら、ボタンの spec をそのまま起こす。
        # 記録は普通の会話終了と同じになるが、閉じられることの方が大事。
        if args:
            try:
                return end_cls(app, *args)
            except Exception:
                self._oops("ConversationEndManager({!r}) failed".format(args))
        return None
