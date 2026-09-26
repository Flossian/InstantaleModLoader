# -*- coding: utf-8 -*-
"""選択肢ボタンと画面描画の共通部品。

ここに置いてあるのは **実機で確かめた事実だけ**。
同じ知見を mod ごとに書き写すと「片方に入って片方に入っていない」状態になるので、
選択肢まわりの発見は全てここへ集め、どの mod もここを使う。

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

## どのスレッドから呼ぶか

断りが無ければ**ゲームのスレッド（Kivy のメインスレッド）から呼ぶ**。
背景スレッド（`jobs.Worker` に渡した `run` の中）から直に呼んでよいのは、

  * `Screen.schedule(fn)` と `scheduler(ctx)` が返す `schedule(fn)`
    ― どちらも「メインスレッドの次のフレームへ渡す」ための入口そのもの
  * `app` に載っている素のデータを読むだけのもの（`gold_of` / `current_area` /
    `area_record` …）。読んでいる間にゲーム側が書き換えないことまでは見ていない

の2つだけ。

**`when_idle` と `end_conversation` は、最初の1回を呼んだスレッドでそのまま行う。**
`when_idle` の1回目の `tick` は `is_button_enabled` / `is_adding_text` /
`is_popup_window_opened` をその場で読む（`Clock` に載るのは2回目以降の見張りと
`then` の実行）。`end_conversation` はその場で `app.process_choice` を呼ぶ。
背景の仕事が終わってから画面を触りたいときは、`schedule` を1枚挟んでから呼ぶ:

    def run(job):                       # 背景スレッド
        result = ask_llm(job)
        schedule(lambda: screen.when_idle(app, lambda: show(result)))

`apply_buttons` は中身を丸ごと `schedule` に載せているので、この縛りは無い。
"""

from __future__ import annotations

import re
import sys
import time

from . import frames

# HUD は属性名で探さず、この型で見分ける（属性名は決めつけない）。
HUD_MODULE = "scripts.hud.new_hud"
HUD_CLASS = "InstanTaleHUD"

# MOD が自分のウィジェットに付ける控えの接頭辞。
# どの MOD も `_instantale_<mod>_<用途>` の形で印を持たせている（`113_` の
# `_instantale_expand_callback`、`116_` の `_instantale_party_icon`）。
# `overlay_host` が「他の
# MOD が足したウィジェット」を置き場所の候補から外すのに使う。
# ボタン辞書の印（`MARK_PREFIX`）とは別物で、こちらは **ウィジェット**の印。
# 新しい MOD もこの接頭辞に揃えること。
MOD_WIDGET_PREFIX = "_instantale_"

# 自前ボタンに持たせる無害な既存クラス。
# mod 無しで押されても選択肢が戻るだけ。
SAFE_CLS = "JustSetButtonToNormalPhase"

# 自前ボタンに足す印のキーは、**すべてこの接頭辞で始める**（`mod_action` /
# `mod_party_action` / `mod_pardon_action` …）。
# `prune_stale` が「他の
# MOD の生きているボタン」と「セーブから復元された自分の残骸」を見分けるのに使う。
# セーブに焼かれるのは text と
# spec だけなので、**印が1つでも残っている＝いま誰かが挿したもの＝残骸ではない**。
# 新しい MOD の印もこれに揃えること。
MARK_PREFIX = "mod_"

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
# 限らない。
# 状態の判定へ使う前に、必ず実測で裏を取ること。
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

    表記や言語設定に依存しないし、
    依頼一覧そのもの（`QuestChoiceManager` が並ぶ）には会話・施設の目印が無いので入れ子にならない。
    """
    data = spec_data(spec_of(entry))
    name = data.get("cls_name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def spec_args(entry):
    """ボタンの spec の args。**読むだけ**。値の意味は解釈しない。

    セーブのフィールド値をそのまま引数の語彙だと決めつけてゲームを落としたことがある（GAME.md
    §2.2）。
    ボタンに載っている args をそのまま使えば、
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

    `ConversationEndManager.__init__(self, app, in_conversation_id, finisher, end_text)` なので **`args[0]` がいま話している相手**（実セーブで確認)。
    `ConversationStartManager.__init__` を追跡しなくても、
    ボタンを**読むだけ**で相手が分かる。
    """
    entry = find_spec_button(buttons, "ConversationEndManager")
    if entry is None:
        return None, None
    args = spec_args(entry)
    return (str(args[0]) if args else None), entry


#: ゲーム自身の衛兵の戦闘を指す `enemy_type`（GAME.md §2.20）。
GUARD_ENEMY_TYPE = "guard"


def guard_encounter(buttons):
    """衛兵に見つかった画面なら「抵抗する！」のボタンを返す。そうでなければ None。

    ゲームはこの画面で旗を何も立てず、「大人しく捕まる／抵抗する！」だけを並べる
    （`300_` の実機）。抵抗するほうは `BattleStartManager(app, 'guard', None)` を組む
    （`bounty_hunter.log` の呼び出し元が `on_button_press`）。
    文字列ではなく、spec のクラス名と `args[0]` で見分ける。
    """
    if not isinstance(buttons, (list, tuple)):
        return None
    for entry in buttons:
        if spec_cls_name(entry) != "BattleStartManager":
            continue
        args = spec_args(entry)
        if args and args[0] == GUARD_ENEMY_TYPE:
            return entry
    return None


def pressed_entry(app, button_index):
    """押された添字から `app.buttons` の要素を引く。

    **地図があるなら地図を使う。**
    ゲーム自身が `display_button_map[button_index]` で添字を引き直していることは、
    事故時のフレームローカルに `mapped_button = 1` が残っていたことで確定した（GAME.md
    §2.2）。
    恒等写像なら結果は同じ、恒等でなければこちらが正しい。

    **地図の枠が整数でなければ、その枠はボタンではない。**
    選択肢が1ページに収まらないとき、ゲームは最後の枠に `次` を出し、
    地図のその枠には添字ではなく文字列 `'next'` を入れる（`206_` の記録。GAME.md §2.2）。
    以前は「整数でなければ添字そのまま」に落としていたので、`次` を押すと
    `buttons[7]`（8つ目の候補）が押されたことになり、
    自前の一覧を出している MOD がページ送りを横取りしていた（`325_` で実際に起きた。
    VERIFICATION.md §3.50）。
    None を返せば呼び側は `orig` へ素通しし、ページ送りはゲームが行う。
    """
    buttons = getattr(app, "buttons", None)
    if not isinstance(buttons, (list, tuple)) or not isinstance(button_index, int):
        return None
    index = button_index
    mapping = getattr(app, "display_button_map", None)
    if isinstance(mapping, (list, tuple)) and 0 <= button_index < len(mapping):
        mapped = mapping[button_index]
        if isinstance(mapped, bool) or not isinstance(mapped, int):
            return None         # ページ送り（'next' など）。ボタンではない
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


def added_by_a_mod(widget):
    """MOD が足したウィジェットか。印は `MOD_WIDGET_PREFIX` で始まる属性。

    Kivy のプロパティは class 側にあるので `vars()` には出ず、
    ここに現れるのはインスタンスに直接足したものだけ ＝ MOD の印。

    **「MOD が作ったか」ではなく「MOD が触ったか」を見ている。**
    ゲーム自身のウィジェットにもこの接頭辞は付く ― `112_` は本文のラベルに設計値を、
    `113_` は本文の枠に、`114_` は入力欄に、`115_` は一覧に、
    `116_` はパーティの帯に、`121_` は人物欄に、
    それぞれ控えを刻む（元に戻せるようにするには、
    ウィジェット自身に持たせるのがいちばん確実だから。§7.4）。

    いま唯一の呼び出し元は `overlay_host` で、
    見るのは **HUD の直下**（素のゲームでは `FloatLayout`
    1枚）だけなので取り違えは起きない。
    **より深くまで走査する用途に広げるときは、
    この関数では足りない** ―「MOD が作った」を知りたいなら、
    作った側が別の印を1つ足すこと。
    """
    try:
        names = list(vars(widget))
    except Exception:
        return False        # `vars()` を持たない相手（`__slots__` 等）は素通し
    return any(isinstance(name, str) and name.startswith(MOD_WIDGET_PREFIX)
               for name in names)


def overlay_host(hud):
    """HUD へ自前のウィジェットを1枚足すときの置き場所。

    **HUD 自身の子の並びは変えない。**
    素の HUD の子は `FloatLayout` 1枚だけで、そこへ直接足すと子が2つになる。
    すると「画面の最初の子」を取る側（`scripts.hud.new_hud:get_current_screen_root`）から見える相手が変わり、
    アイテムを持ち物へ移す・装備する操作が効かなくなる（VERIFICATION_LOG.md
    §2.33）。
    だから足すのはその `FloatLayout` の**中**。
    ゲーム自身もこの中へ効果や窓を出し入れしている。

    **どれを選ぶかは「いちばん古い子」で決める。**
    Kivy の `children` は新しい順なので、先頭を採ると

      * ゲームが一時的に出している窓（消えるときにこちらのボタンも道連れになる）
      * 他の MOD が HUD 直下に残したウィジェット（その**中**へ入り込む）

    のほうを掴む。
    除外を「自分のボタンだけ」にしていると、
    HUD へウィジェットを足す MOD が2本になった時点で成立してしまう。
    ゲームの `FloatLayout` は画面が組まれた時点で居る ＝
    `children` の**最後尾**なので、そこから探せば両方避けられる。
    """
    children = frames.attr(hud, "children")
    if not isinstance(children, (list, tuple)):
        return hud
    for child in reversed(list(children)):
        if frames.attr(child, "add_widget") is frames.MISSING:
            continue
        if added_by_a_mod(child):
            continue        # 他の MOD のウィジェット。この中には入らない
        return child
    return hud            # 子を持たない画面なら HUD 自身に（従来どおり）


def children_of(widget):
    """ウィジェットの子の写し（Kivy の並びは新しい順）。読めなければ空。"""
    children = frames.attr(widget, "children")
    return list(children) if isinstance(children, (list, tuple)) else []


def walk_widgets(root, max_depth=None, seen=None, oldest_first=False):
    """ウィジェット木を深さ優先の前順で辿る生成器。同じものは1度だけ。

    `max_depth` … `root` を 0 として、この深さのものまで出す（その子へは降りない）。
    `seen` … 出したものの `id` を入れる集合。2本の木を続けて辿るとき、
    同じ集合を渡せば重なった分を二度出さない（`115_` が HUD と窓の直下で使う）。
    `oldest_first` … 兄弟を古い順（`children` の逆）に出す。
    「最初に見つかった1つ」を採る呼び手（`330_` / `402_` の見出し探し）は
    この順で実機を確かめてあるので、変えないこと。

    `330_` / `402_` の `walk_widgets` と `115_` / `124_` / `333_` の `walk`（深さの上限つき）を寄せた。
    """
    if root is None:
        return
    if seen is None:
        seen = set()
    stack = [(root, 0)]
    while stack:
        widget, depth = stack.pop()
        if id(widget) in seen:
            continue
        seen.add(id(widget))
        yield widget
        if max_depth is not None and depth >= max_depth:
            continue
        children = children_of(widget)
        if not oldest_first:
            children.reverse()      # 積んだ逆から出るので、並びどおりに出すには逆に積む
        stack.extend((child, depth + 1) for child in children)


#: 本文を描けるウィジェットが持っている property（`is_label` の既定）。
LABEL_ATTRS = ("text", "texture_update", "text_size")


def is_label(widget, needs=LABEL_ATTRS):
    """本文を描けるウィジェットか。**型では見ない**（GAME.md §1.3）。

    ゲーム側の派生クラスや別名の Label がありうるので、
    `needs` の property が全部在り、`text` が文字列であることで見分ける。
    触る property が違う呼び手は `needs` を渡す（`112_` は `line_height`）。
    """
    for name in needs:
        if frames.attr(widget, name) is frames.MISSING:
            return False
    return isinstance(frames.attr(widget, "text"), str)


def is_scroller(widget):
    """縦に送れる枠（`ScrollView` の類）か。型では見ない。"""
    for name in ("scroll_y", "do_scroll_y"):
        if frames.attr(widget, name) is frames.MISSING:
            return False
    return True


# --------------------------------------------------------------------------
# 寸法（`113_` / `116_` が共有する）
# --------------------------------------------------------------------------
def numbers(value, count):
    """`size_hint` / `size` などを素の tuple にする（Kivy の可変列を持ち歩かない）。"""
    try:
        return tuple(value)[:count]
    except Exception:
        return None


def rect_of(widget):
    """`(x, y, 幅, 高さ)`（親の座標系）。読めなければ None。"""
    size = numbers(frames.attr(widget, "size"), 2)
    pos = numbers(frames.attr(widget, "pos"), 2)
    if not size or not pos:
        return None
    try:
        return (float(pos[0]), float(pos[1]), float(size[0]), float(size[1]))
    except (TypeError, ValueError):
        return None


def same_rect(rect, target, slack, ratio):
    """見た目に同じ矩形か。枠線・背景はぴったり重ならず数 px ずれて置かれている。

    許すずれは `max(slack, 寸法 × ratio)`（位置）とその2倍（寸法）。
    `113_` / `116_` はどちらも `slack=12.0, ratio=0.03` を渡している。
    """
    if rect is None or target is None:
        return False
    slack_x = max(slack, target[2] * ratio)
    slack_y = max(slack, target[3] * ratio)
    return (abs(rect[0] - target[0]) <= slack_x
            and abs(rect[1] - target[1]) <= slack_y
            and abs(rect[2] - target[2]) <= slack_x * 2
            and abs(rect[3] - target[3]) <= slack_y * 2)


def close_enough(value, wanted):
    """寸法が「もうその値になっている」か。浮動小数の丸め（0.5 未満）は差と見ない。"""
    try:
        return abs(float(value) - float(wanted)) < 0.5
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# プレイヤーの所持金と、画面を出してはいけない状態
# --------------------------------------------------------------------------
#: ゲームが「別のこと」をしている最中を表す旗。
#: ここが真の間は施設の選択肢を足さない（`309_` / `902_` が共有）。
#: `300_` は **`in_shopping` を外した** ものを使う
#: ― 店の外を往復しているだけでも真のままなので、
#: イベントの抑止条件に使うと店系の施設でほとんど出なくなる（あちらの註を参照）。
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
              "in_conversation", "in_free_input", "in_action_in_conversation",
              "in_shopping")


def money(value):
    """金額の表示。3桁ごとに区切る。読めない値はそのまま文字列にする。"""
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return str(value)


# --------------------------------------------------------------------------
# 通貨の表記（`130_` が決め、`309_` / `314_` / `315_` / `902_` が使う）
# --------------------------------------------------------------------------
#: 素のゲームの言い方。長い形と短い形の2つある（GAME.md §2.29）。
#: 画面も、ゲームが LLM へ送る指示文も、この2つで書かれている。
COIN_LONG = "ゴールド"
COIN_SHORT = "G"

#: 数のすぐ後ろに来る短い形。
#: `馬車(1000G)` `Doghouse (0G)` `貴族権(1,000,000G)` に当たり、
#: `8GB` や `GUI` には当たらない（後ろに英数字が続かないことを見ている）。
#: 埋める前のテンプレート（`}` の直後）も拾うのは、
#: 自由生成施設の値段が `傷薬を煎じてもらう({price.salve}G)` の形で来るため。
_COIN_SHORT_RE = re.compile("(?<=[0-9０-９}])[ 　]?G(?![A-Za-z0-9])")

#: 英語表示の長い形（`You paid 1000 gold.`）。
#: **数の後ろでしか当たらない**（素材や色の `gold` を巻き込まないため）。
#: 英語の所持金ラベル（`Gold:`）には当たらない。
_COIN_LONG_EN_RE = re.compile("(?<=[0-9０-９}]) gold(?![A-Za-z])")

#: 今の表記。`set_currency` だけが書き換える。
_coin_names = {"long": COIN_LONG, "short": COIN_SHORT}

#: 額を読む形を短い形ごとに控える（`parse_coin`）。
_coin_price_res = {}


def _clean_name(value, fallback):
    """表記として使える文字列だけを通す。使えなければ `fallback`。

    表記を空にできてしまうと `1000` と `1000G` の区別が画面から消えるので、
    空白だけの指定は「指定なし」として扱う。
    """
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    return value if value else fallback


def _rewrite_coins(text, long_name, short_name):
    """`text` の中の**素の表記**を、渡された表記へ直す。"""
    if long_name != COIN_LONG:
        if COIN_LONG in text:
            text = text.replace(COIN_LONG, long_name)
        if "gold" in text:
            text = _COIN_LONG_EN_RE.sub(" " + long_name, text)
    if short_name != COIN_SHORT and "G" in text:
        text = _COIN_SHORT_RE.sub(short_name, text)
    return text


def set_currency(long_name=None, short_name=None):
    """通貨の表記を決める。**決まった** `(長い形, 短い形)` を返す。

    決めるのは MOD 1本だけ（同梱では `130_currency_unit`）。
    ここが持つのは表記だけで、額の計算には何も関わらない。

    **何度通しても結果が変わらない表記しか受け取らない。**
    `rewrite_coins` は画面と LLM の両方の経路で走るので、
    同じ文が二度通ることがある。
    新しい表記の中に素の表記が残っていると
    （`ゴールド` → `金ゴールド`）そのたびに伸びていくため、
    決める時点で1度だけ確かめ、当てはまらない指定は素の言い方のまま据え置く。
    受け取らなかったことは戻り値が指定と違うことで分かる（呼ぶ側が記録する）。
    """
    long_name = _clean_name(long_name, COIN_LONG)
    short_name = _clean_name(short_name, COIN_SHORT)

    # 3つの当たり方（長い形・短い形・英語の長い形）を1本に並べた見本。
    probe = "1000" + COIN_SHORT + COIN_LONG + " 1000 gold"
    once = _rewrite_coins(probe, long_name, short_name)
    if _rewrite_coins(once, long_name, short_name) != once:
        long_name, short_name = COIN_LONG, COIN_SHORT

    _coin_names["long"] = long_name
    _coin_names["short"] = short_name
    return (long_name, short_name)


def currency_names():
    """今の `(長い形, 短い形)`。素のままなら `("ゴールド", "G")`。"""
    return (_coin_names["long"], _coin_names["short"])


def rewrite_coins(text):
    """文中の通貨の表記を今の表記へ直す。素のままなら何もしない。

    文字列でない値はそのまま返す（`scripts.languages:tr` には
    文字列以外も来る）。
    """
    if not isinstance(text, str) or not text:
        return text
    return _rewrite_coins(text, _coin_names["long"], _coin_names["short"])


class _KeepMissing(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fill_template(template, **values):
    """設定のテンプレートを埋め、通貨の表記を今の表記へ直す。

    壊れたテンプレートでも素の文字列で返す。
    知らない変数名（`{typo}`）はそのまま残す。打ち間違いを画面で見えるようにするため。
    設定のテンプレートは素のゲームの言い方（`G`）のままでよい
    （`130_` が差し替えていれば `馬車(1000G・14日)` → `馬車(1000円・14日)`）。

    `314_` / `315_` / `332_` の `fmt` に1字違わず写されていた。
    """
    try:
        filled = str(template).format_map(_KeepMissing(values))
    except Exception:
        filled = str(template)
    return rewrite_coins(filled)


def parse_coin(text):
    """ラベルから額を読む。読めなければ `None`。

    **素の `G` と今の短い形の両方を読む**
    （`馬車(1000G)` も `馬車(1000円)` も 1000）。
    表記を差し替えた後の画面から素の運賃を読み取る側（`314_` / `315_`）が、
    差し替えの有無を気にしなくて済むようにするため。

    桁区切りは落とす。数の**前**に付ける記号（`$1000`）は読めない。
    """
    short = _coin_names["short"]
    pattern = _coin_price_res.get(short)
    if pattern is None:
        units = [re.escape(COIN_SHORT)]
        if short != COIN_SHORT:
            units.insert(0, re.escape(short))
        pattern = re.compile(r"(\d[\d,]*)\s*(?:" + "|".join(units) + ")")
        _coin_price_res[short] = pattern
    match = pattern.search(text or "")
    if match is None:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _raw_gold(app):
    """所持金の素の値（`int` か `float`）。読めなければ `None`。

    **`bool` を弾く。**
    Python では `True` は `int` なので、
    `isinstance` の素朴な判定だと `gold = True` を所持金 1 として通してしまう。
    """
    value = frames.attr(frames.attr(app, "player", None), "gold", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def gold_of(app):
    """プレイヤーの所持金（`int` に切り捨てる）。読めなければ `None`。"""
    value = _raw_gold(app)
    return None if value is None else int(value)


def set_gold(app, value, on_error=None):
    """所持金を `value` にする。書けたら新しい額、書けなければ `None`。

    **今の型を保つ。**
    `float` の所持金には `float` を、`int` の所持金には丸めた `int` を書く
    （float の世界に int を混ぜない）。
    この式は `314_` / `315_` / `332_` に1字違わず写されていた。

    読めない所持金には書き込まない（`gold_of` が `None` を返す相手）。
    ゲーム自身の支払い経路を通さずに直接触るので、**呼ぶ側が理由を記録する** こと。
    """
    current = _raw_gold(app)
    if current is None:
        return None
    try:
        player = frames.attr(app, "player", None)
        player.gold = float(value) if isinstance(current, float) else int(round(value))
        return player.gold
    except Exception:
        if on_error is not None:
            on_error("cannot change the player's gold")
        return None


def add_gold(app, amount, on_error=None):
    """所持金を増減する。書けたら新しい額、書けなければ `None`。

    足すのは**素の値**に対して（`gold_of` の切り捨てを通さない）。
    書き方は `set_gold` と同じで、今の型を保つ。
    """
    current = _raw_gold(app)
    if current is None:
        return None
    try:
        target = current + amount
    except Exception:
        if on_error is not None:
            on_error("cannot change the player's gold")
        return None
    return set_gold(app, target, on_error=on_error)


# --------------------------------------------------------------------------
# クエストの格納先（`301_` / `305_` / `307_` が共有する）
# --------------------------------------------------------------------------
def quest_stores(app):
    """クエストが入っている**2つ**の場所（`206_` の計測。GAME.md §2.9）。

        app.world.quests          {id: Quest インスタンス}  ゲームが遊ぶときに読む
        app.world_dict['quests']  {id: dict}                セーブに出るのはこちら

    **読むのはどちらでもよいが、書くときは必ず両方。**
    片方だけ直すと画面の表示と保存内容がずれる。
    どちらに登録されるかを決め打ちしないので、無い側は黙って落ちる。

    この4つは `301_` / `305_` / `307_` に1字違わず写されていた。
    写して回るものはローダの語彙（TECH.md §3.2.3）。
    """
    stores = []
    quests = frames.attr(frames.attr(app, "world", None), "quests", None)
    if isinstance(quests, dict):
        stores.append(quests)
    world_dict = frames.attr(app, "world_dict", None)
    if isinstance(world_dict, dict) and isinstance(world_dict.get("quests"), dict):
        stores.append(world_dict["quests"])
    return stores


def quest_ids(app):
    """両方の合併（文字列の id）。どちらに登録されるか決め打ちしないため。"""
    seen = []
    for store in quest_stores(app):
        for qid in store:
            if str(qid) not in seen:
                seen.append(str(qid))
    return seen


def quest_of(app, quest_id):
    """先に見つかった側のクエスト。無ければ None。"""
    for store in quest_stores(app):
        if quest_id in store:
            return store[quest_id]
    return None


def world_overview(app, limit=600):
    """世界観の文（`world_data.overview`）。LLM への頼み文に入れる。無ければ空。

    `save_data_dict` → `world_dict` の順に見て、先に読めた方を `limit` 字で切る。
    `330_` / `331_` に同じ本体が写されていた。
    `405_` は別の読み方（遊んでいる世界の控えと `app.world` まで見る）なので寄せていない。
    """
    for attr in ("save_data_dict", "world_dict"):
        holder = frames.attr(app, attr, None)
        data = holder.get("world_data") if isinstance(holder, dict) else None
        text = data.get("overview") if isinstance(data, dict) else None
        if isinstance(text, str) and text.strip():
            return frames.short(text.strip(), limit)
    return ""


def current_quest_id(app):
    """ゲームがいま進めているクエストの id（文字列）。クエスト中でなければ None。

    これがゲーム自身の答え。
    `QuestStartManager` を捕まえられなくても（注入し直しをまたいだ場合など）、
    これを見れば道中のクエストの最中かどうかが分かる。
    `app.current_quest_data` はクエスト中だけ `Quest` が入り、
    それ以外は None（`206_` の記録で確認済み）。

    `307_` / `325_` に同じ本体が写されていた。
    """
    quest = frames.attr(app, "current_quest_data", None) if app is not None else None
    if quest is None:
        return None
    value = quest.get("id") if isinstance(quest, dict) else frames.attr(quest, "id", None)
    return str(value) if value is not None else None


def id_sort_key(value):
    """id を**数として**並べるための鍵。数にできないものは後ろへ。

    ゲームの id は文字列で採番順（`"9"` の次が `"10"`）。
    素の `sorted()` は辞書順なので `"10" < "9"` になり、
    「いちばん新しい
    id」を採ると **1回の生成で複数増えた回だけ取り違える**（`301_` が実際にそうなっていた）。
    """
    try:
        return (0, int(value))
    except Exception:
        return (1, str(value))


def quest_value(quest, name, default=None):
    """クエストの項目を読む。**インスタンスでも dict でも同じ書き方で。**"""
    if isinstance(quest, dict):
        return quest.get(name, default)
    return frames.attr(quest, name, default)


def set_quest_value(app, quest_id, name, value, on_error=None):
    """**両方の格納先に**書く。書けた数を返す。

    片方だけに書くと、画面に出ているものとセーブされるものがずれる。
    書けなかったことは `on_error(メッセージ)` に渡す（無ければ黙る）。
    """
    written = 0
    for store in quest_stores(app):
        target = store.get(quest_id)
        if target is None:
            continue
        try:
            if isinstance(target, dict):
                target[name] = value
            else:
                setattr(target, name, value)
            written += 1
        except Exception:
            if on_error is not None:
                on_error("cannot set {} on quest {!r}".format(name, quest_id))
    return written


# --------------------------------------------------------------------------
# HUD に足す自前のボタン（`113_` / `116_` / `122_` が共有する）
# --------------------------------------------------------------------------
#: 絵柄に「文字」を選んだときの呼び名（アイコンではなく文字ボタンになる）。
AS_TEXT = "文字"

#: 隅と `pos_hint` の対応。
#: 縁からわずかに内側へ入れる。
CORNERS = {
    "右上": {"right": 0.995, "top": 0.995},
    "左上": {"x": 0.005, "top": 0.995},
    "右下": {"right": 0.995, "y": 0.005},
    "左下": {"x": 0.005, "y": 0.005},
}


def upx(value):
    """ゲームの拡縮（`scripts.hud.new_hud:upx`）に合わせる。無ければ素の値。"""
    module = sys.modules.get("scripts.hud.new_hud")
    scale = frames.attr(module, "upx", None) if module is not None else None
    if callable(scale):
        try:
            return float(scale(value))
        except Exception:
            pass
    return float(value)


def window_size():
    """窓の大きさ。引けなければ `(0.0, 0.0)`（＝「分からない」）。"""
    try:
        from kivy.core.window import Window

        return float(Window.width), float(Window.height)
    except Exception:
        return 0.0, 0.0


def clamp_into_window(widget):
    """窓の内側へ寄せる。**置いた後に必ず通す** ― はみ出したボタンは押せない。"""
    width, height = window_size()
    if not width or not height:
        return
    try:
        if widget.y + widget.height > height:
            widget.y = height - widget.height
        if widget.y < 0:
            widget.y = 0
        if widget.x + widget.width > width:
            widget.x = width - widget.width
        if widget.x < 0:
            widget.x = 0
    except Exception:
        pass          # 座標を持たない相手。置けないだけで害は無い


def icon_strokes(icon, flipped=False):
    """共有の絵柄を **0〜1 の座標**で返す。知らない名前なら空。

    画像ファイルを持たないのは、線で描けばどの解像度でも滲まず、
    色も透過もこちらで決められるため（配布物にバイナリが増えないのも利点）。

    `flipped` は「押すと**戻る**状態」＝上下を反転して、
    次に何が起きるかをそのまま形にする。
    **MOD 固有の絵柄はここに足さない** ― `113_` の「伸縮」、`116_` の「人」、
    `122_` の本や吹き出しは、その MOD だけの語彙なので MOD のフォルダに置く（TECH.md §3.2.3 の表でいう
    MOD 側）。
    """
    flip = (lambda y: 1.0 - y) if flipped else (lambda y: y)

    def line(*points):
        return [(x, flip(y)) for x, y in points]

    if icon == "二重山形":
        return [line((0.22, 0.40), (0.50, 0.66), (0.78, 0.40)),
                line((0.22, 0.20), (0.50, 0.46), (0.78, 0.20))]
    if icon == "山形":
        return [line((0.20, 0.34), (0.50, 0.66), (0.80, 0.34))]
    if icon == "矢印":
        return [line((0.50, 0.18), (0.50, 0.82)),
                line((0.28, 0.60), (0.50, 0.82), (0.72, 0.60))]
    if icon == "枠":
        # 四隅のかぎ括弧。
        # 広げる前は外を向き、広がっているときは内を向く。
        if flipped:
            return [line((0.20, 0.42), (0.42, 0.42), (0.42, 0.20)),
                    line((0.80, 0.42), (0.58, 0.42), (0.58, 0.20)),
                    line((0.20, 0.58), (0.42, 0.58), (0.42, 0.80)),
                    line((0.80, 0.58), (0.58, 0.58), (0.58, 0.80))]
        return [line((0.20, 0.42), (0.20, 0.20), (0.42, 0.20)),
                line((0.80, 0.42), (0.80, 0.20), (0.58, 0.20)),
                line((0.20, 0.58), (0.20, 0.80), (0.42, 0.80)),
                line((0.80, 0.58), (0.80, 0.80), (0.58, 0.80))]
    return []


def make_icon_button(*, text="", size=32.0, square=True, font_name=None,
                     pos_hint=None):
    """HUD に足す自前のボタンを1枚作る。作れないビルドでは None。

    | すること | なぜ |
    |---|---|
    | 文字は横長・アイコンは正方形 | 文字だと1文字ぶんでは収まらない |
    | フォントを本文のラベルから写す | Kivy の既定（Roboto）に日本語が無く、写さないと豆腐になる |
    | 背景を5つとも消す | `background_normal` を空にしないと、色を透明にしても既定のテクスチャがうっすら残る |

    `font_name` は `frames.text_of(label, "font_name")` で採ったものを渡す（`"<missing>"` を掴まないため。TECH.md
    §5.2）。
    """
    try:
        from kivy.uix.button import Button
    except Exception:
        return None
    height = upx(size)
    width = height if square else height * 2.0
    button = Button(text=text, size_hint=(None, None), size=(width, height),
                    pos_hint=dict(pos_hint or {}))
    if isinstance(font_name, str) and font_name:
        button.font_name = font_name
    button.font_size = height * 0.45
    if square:
        for name, value in (("background_normal", ""), ("background_down", ""),
                            ("background_disabled_normal", ""),
                            ("background_color", (0, 0, 0, 0)),
                            ("border", (0, 0, 0, 0))):
            try:
                setattr(button, name, value)
            except Exception:
                pass      # その属性を持たないビルドでも描画は成り立つ
    return button


def paint_icon(button, strokes, *, attr, key=(), width=2.0, alpha=0.85,
               log_exc=None):
    """線を引き直す。**変わったときだけ**（毎フレーム描かない）。

    本文は1文字ずつ増え、パーティ欄は相手の HP が動くたびに塗り直されるので、
    塗り直しの呼び出しは何十回も来る。
    位置・大きさ・太さ・濃さと
    `key`（MOD 側の「どの絵柄か・どちら向きか」）が同じなら引き直す必要は無い。
    控えは `attr` に置く ― **`MOD_WIDGET_PREFIX` で始める名前にすること**（`overlay_host` が「他の
    MOD が足したもの」の見分けに使う）。
    """
    signature = (tuple(key) if isinstance(key, (list, tuple)) else (key,),
                 width, alpha,
                 tuple(frames.attr(button, "pos", ()) or ()),
                 tuple(frames.attr(button, "size", ()) or ()))
    if frames.attr(button, attr, None) == signature:
        return
    try:
        from kivy.graphics import Color, Line
    except Exception:
        return            # 線が引けない環境（オフライン検証）では文字のまま
    try:
        group = button.canvas.after
        group.clear()
        x, y = float(button.x), float(button.y)
        box_width, box_height = float(button.width), float(button.height)
        group.add(Color(1, 1, 1, float(alpha)))
        for points in strokes:
            flat = []
            for fx, fy in points:
                flat.extend((x + fx * box_width, y + fy * box_height))
            group.add(Line(points=flat, width=upx(width), cap="round",
                           joint="round"))
        setattr(button, attr, signature)
    except Exception:
        if log_exc is not None:
            log_exc("could not draw the icon")


def show_widget(widget, visible):
    """見せる／隠す。隠すときは**押せなくもする**（見えない当たり判定を残さない）。"""
    try:
        widget.opacity = 1.0 if visible else 0.0
        widget.disabled = not visible
    except Exception:
        pass


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
# ゲーム内の日付（GAME.md §2.16）
# --------------------------------------------------------------------------
def game_day(app):
    """いまのゲーム内日数。読めなければ `None`。

    日付は**世界に1つ**（`world.days_elapsed`）で、
    進めるのは `InstantaleApp.elapse_days`（宿泊と移動はここを大きく飛ばす）。

    `app` でも `World` インスタンスでも受ける。
    `World.__init__` を包む場面では `app.world` がまだ埋まっていないため
    （`areas_of_world` と同じ理由）。

    実行時の世界がまだ無いとき（ロードの途中）は `world_dict["world_data"]`
    から拾う。5本の MOD が各自でこれを読んでいて、
    **その受け皿を持っていたのは `312_` だけ**だった
    ― 他の4本はロード中に呼ぶと `None` に倒れる。

    `float` で入っていても `int` にして返す（日数として使う側は整数を期待する）。
    `True` は `int` の仲間だが日数ではないので弾く。
    """
    for holder in (getattr(app, "world", None), app):
        value = getattr(holder, "days_elapsed", None)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    world_dict = getattr(app, "world_dict", None)
    data = world_dict.get("world_data") if isinstance(world_dict, dict) else None
    value = data.get("days_elapsed") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


# --------------------------------------------------------------------------
# エリアの引き当て（GAME.md §2.7）
# --------------------------------------------------------------------------
def world_areas(app):
    """エリア表 `{id: Area}`。**属性名ではなく中身で見分ける。**"""
    return areas_of_world(getattr(app, "world", None))


def areas_of_world(world):
    """`World` インスタンスから直接引く側。

    `World.__init__` を包む場面では `app.world` がまだ埋まっていない
    （いま作っている最中）ので、`app` ではなく world を受ける入口が要る
    （`318_` の `live_store` と同じ理由）。
    """
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

    `player.current_area` はエリアのオブジェクトとは限らない。
    NPC 側のセーブでは `"7"` という id の文字列。
    持ち方は決めつけず、どちらでも引き当てる。
    """
    value = getattr(getattr(app, "player", None), "current_area", None)
    if isinstance(value, (str, int)):
        return world_areas(app).get(str(value))
    return value


def area_id_of(area):
    return str(getattr(area, "id", "")) if area is not None else ""


# --------------------------------------------------------------------------
# 手配度（GAME.md §2.20）
# --------------------------------------------------------------------------
# 治安上の立場は土地ごとに `Character.area_history` へ入っている:
#
#     area_history = {"0": {"residency": {...}, "achievements": [...],
#                           "lawfulness": 10}, ...}
#
# 平常値は 10 で、小さいほど手配が重く、0 未満で犯罪者（実プレイで -40 を観測）。
# ゲーム側に読み書きのヘルパは無いので値を直に触る。
# ここに置いてあるのは読み方だけで、**いくつから手配とみなすかは MOD の判断**。
LAWFULNESS_KEY = "lawfulness"


def area_history_of(character):
    """エリアごとの記録 `{area_id: 記録}`。読めなければ `None`。"""
    value = getattr(character, "area_history", None)
    return value if isinstance(value, dict) else None


def area_record(character, area_id):
    """そのエリアの記録。無ければ `None`（＝一度も訪れていない）。

    id は保存されるときに文字列になるが、実行中に int で入っていることも
    ありうるので、素の引きが外れたら文字列に均して引き直す。
    """
    history = area_history_of(character)
    if history is None or area_id in (None, ""):
        return None
    if area_id in history:
        return history[area_id]
    wanted = str(area_id)
    for key, value in history.items():
        if str(key) == wanted:
            return value
    return None


def lawfulness_of(entry):
    """記録の手配度。読めなければ `None`。

    `bool` を弾いているのは `isinstance(True, int)` が真だから。
    True を手配度 1 と読むと、そこから金額や敵の強さまで計算してしまう。
    """
    if entry is None:
        return None
    value = entry.get(LAWFULNESS_KEY) if isinstance(entry, dict)         else getattr(entry, LAWFULNESS_KEY, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def set_lawfulness(entry, value):
    """記録の手配度を書き戻す。書けたら `True`。

    **項目を新設しないのは呼ぶ側の責任**（先に `lawfulness_of` で読めた記録にしか
    渡さない）。ここで無条件に作ると、手配度を持たないビルドにそれらしい項目が
    生えて、以後どちらが本物か分からなくなる。
    """
    if entry is None:
        return False
    try:
        if isinstance(entry, dict):
            entry[LAWFULNESS_KEY] = int(value)
        else:
            setattr(entry, LAWFULNESS_KEY, int(value))
    except Exception:
        return False
    return True


def lawfulness_by_area(character):
    """`{area_id(str): 手配度}`。読めなかったエリアは入れない。

    エリア id を文字列に均すのは、セーブと実行中で型が違いうるから
    （`area_record` と同じ理由）。
    """
    history = area_history_of(character)
    if not history:
        return {}
    values = {}
    for key, entry in history.items():
        value = lawfulness_of(entry)
        if value is not None:
            values[str(key)] = value
    return values


# --------------------------------------------------------------------------
# 施設の引き当て（GAME.md §2.7）
# --------------------------------------------------------------------------
# 施設はエリアの直下ではなく**ノードの下**にぶら下がっている（実セーブ）:
#
#     areas[area_id].nodes[node_id].facilities[facility_id]
#
# `initial_location` は `{"area": "7", "node": null, "facility": "127"}` の形で、
# **`node` が null のことがある**。だから施設は id だけを頼りにノードを総当たり
# して探す（`find_facility`）。
#
# 実在する `facility_type`（実セーブで確認）:
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

    ノードも一緒に返すのは
    `move_npc_to_facility(character_id, character_instance, target_facility, target_node=None, ...)` が施設とノードを **別々に**取るため。
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

    ダンジョンや野外のエリアにはギルドが無い。
    **見つからないことが正常な答え** なので、
    呼ぶ側はそこで別の置き場所へ下がること。
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

    施設そのものが渡ってくるとは限らない。
    id の文字列で持っていることがあるので、そのときは世界の施設表から引き直す。
    """
    if facility is None:
        return ""
    name = getattr(facility, "name", None)
    if isinstance(name, str) and name.strip():
        return frames.short(name, limit)
    if isinstance(facility, dict):
        value = facility.get("name")
        return frames.short(value, limit) if isinstance(value, str) else ""
    world = getattr(app, "world", None)
    for attr in ("facilities", "locations"):
        table = getattr(world, attr, None)
        if isinstance(table, dict):
            found = table.get(str(facility))
            if found is not None:
                return facility_name(app, found, limit)
    return ""


# --------------------------------------------------------------------------
# パーティの名簿（GAME.md §2.8）
# --------------------------------------------------------------------------
# **在り処も形も決めつけない。**
# セーブに出るのは `game_variables['party']` の `['player', '63', ...]` という
# id の配列だが、実行時に `app.party` から同じものが読めるとは限らず（仲間を入れた直後でも空だった）、
# `list` とも限らない（`{id: Character}` の辞書のこともある）。
# だから候補を全部集め、**中身を見て** 本物を選ぶ。
# ここに置いてあるのは読み取りと1人落とすところまで。
# **名簿に誰を足す/落とすかを決めるのは mod 側の判断**で、
# 外す処理そのものはゲーム自身の `remove_party_member` を通すこと（GAME.md §2.8）。
PLAYER_ID = "player"

# 名簿の要素から id を読むときに見る属性・キー（文字列でも Character でも読む）。
ID_ATTRS = ("id", "character_id", "npc_id")


def element_id(value):
    """名簿の要素を id の文字列にする。

    セーブでは `['player', '83']` の文字列だが、
    実行時に Character のインスタンスが並んでいる可能性もある。
    **どちらの形でも読めるように**してある（形を決めつけて空振りしたのが
    `302_` の最初の失敗）。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for attr in ID_ATTRS:
        found = getattr(value, attr, None)
        if isinstance(found, (str, int)):
            return str(found)
    if isinstance(value, dict):
        for key in ID_ATTRS:
            if key in value:
                return str(value[key])
    return str(value)


def party_stores(app):
    """メンバーの名簿になりうる入れ物を、心当たりのある場所から全部集める。

    `(どこから取ったか, その入れ物)` の一覧で返す。
    書くときは同じ
    id を持つ入れ物**すべて**を直す（片方だけ直すと画面とセーブがずれる）。
    """
    stores, seen = [], set()

    def add(label, value):
        if not isinstance(value, (list, dict)) or id(value) in seen:
            return
        seen.add(id(value))
        stores.append((label, value))

    add("app.party", getattr(app, "party", None))
    variables = getattr(app, "game_variables", None)
    if isinstance(variables, dict):
        add("app.game_variables['party']", variables.get("party"))
    world_dict = getattr(app, "world_dict", None)
    if isinstance(world_dict, dict):
        add("world_dict['party']", world_dict.get("party"))
        inner = world_dict.get("game_variables")
        if isinstance(inner, dict):
            add("world_dict['game_variables']['party']", inner.get("party"))
    world = getattr(app, "world", None)
    if world is not None:
        add("world.party", getattr(world, "party", None))
    player = getattr(app, "player", None)
    if player is not None:
        add("player.party", getattr(player, "party", None))

    # 心当たりが全部空振りしたときの最後の網。
    # **名前に party が入っている属性・キーだけ**を見る。
    # 何でも拾うと `escaped_member_in_battle` や
    # `surrendered_characters` のような「'player' が入りうる別の配列」を名簿と誤認する。
    # `original_party`（差し替えの控え）と
    # `accompany`（クエスト同行者）は名簿ではないので外す。
    def sweep(container, label_for):
        if not isinstance(container, dict):
            return
        for key, value in list(container.items()):
            if not isinstance(key, str) or "party" not in key:
                continue
            if "original" in key or "accompany" in key:
                continue
            add(label_for(key), value)

    sweep(getattr(app, "__dict__", None), lambda key: "app." + key)
    sweep(variables if isinstance(variables, dict) else None,
          lambda key: "game_variables[{!r}]".format(key))
    return stores


def store_ids(store):
    """名簿の中身を id の一覧にする。配列でも辞書でも同じ形で返す。

    辞書のときは**キー**が id（`{id: Character}` の形を想定）。
    キーが id に見えないときだけ値から引く。
    """
    if isinstance(store, dict):
        ids = []
        for key, value in store.items():
            found = element_id(key)
            if not found or found.startswith("<"):
                found = element_id(value)
            ids.append(found)
        return ids
    return [element_id(value) for value in store]


def drop_from_store(store, member_id):
    """名簿から1人落とす。配列でも辞書でも扱えるようにする。"""
    if isinstance(store, dict):
        for key in [k for k in list(store) if element_id(k) == member_id]:
            store.pop(key, None)
        return
    store[:] = [value for value in store if element_id(value) != member_id]


def pick_store(app):
    """本物の名簿を選ぶ。`(どこから, id の一覧)`。無ければ `(None, [])`。

    セーブの `party` は必ず `'player'` を含む
    `['player', '83']` の形なので、**`'player'` を含む入れ物を本物とみなす**。
    それが無ければ中身のある入れ物、それも無ければ空を返す。
    """
    candidates = party_stores(app)
    for label, store in candidates:
        ids = store_ids(store)
        if PLAYER_ID in ids:
            return label, ids
    for label, store in candidates:
        ids = store_ids(store)
        if ids:
            return label, ids
    return None, []


def party_ids(app):
    """名簿の id の一覧（プレイヤーを含む）。"""
    return pick_store(app)[1]


def party_member_ids(app):
    """プレイヤーを除いた同行者の id。順序は名簿のまま。"""
    return [member_id for member_id in party_ids(app)
            if member_id and member_id != PLAYER_ID]


def paint_choices(app, texts, oops=None):
    """選択肢の文字列を実際に画面へ塗る。効いた手段の一覧を返す（`Screen.paint` の中身）。

    `refresh_choice_buttons()` が組み直すのは `to_display_buttons` までで、画面の文字は
    HUD 側の `update_button_texts` を呼ばないと変わらない（冒頭の事実）。`app.buttons` だけ
    直して塗らないと、見えている文字と押される処理が食い違う（ロード後の組み直しで実機）。
    `oops(what)` は例外の記録先。無ければ黙って続ける。
    """
    done = []

    def failed(what):
        if oops is not None:
            oops(what)

    loader = getattr(app, "display_button_load", None)
    if getattr(app, "is_button_enabled", None) is False:
        # 待機中（「…」を出している間）は呼ばない。`display_button_load` は待機中に
        # 呼ばれると次のコマを自分で予約し直すので、呼ぶたびにゲームの点送りが1本ずつ増え、
        # 点が飛ぶ（`234_probe_busy_display` の実測。GAME.md §2.4）。
        # 待機中の枠はどのみちゲームが点で塗り直す。
        done.append("display_button_load skipped (waiting)")
    elif callable(loader):
        try:
            loader(0)
            done.append("display_button_load")
        except Exception:
            failed("display_button_load failed")

    hud = find_hud(app)
    updater = getattr(hud, "update_button_texts", None) if hud is not None else None
    if callable(updater):
        try:
            updater(app, list(texts))
            done.append("hud.update_button_texts")
        except Exception:
            failed("hud.update_button_texts failed")
    elif hud is None:
        # ここが出たら画面は塗り替わらない。
        # 型で探して見つからない＝ HUD の構成が変わったということなので、
        # その合図として残す。
        done.append("hud not found")

    return done


BUSY_DOTS = (".", "..", "...")


def shown_dots(app):
    """いま選択肢の枠に出ている点（`.` / `..` / `...`）。点でなければ None。"""
    hud = find_hud(app)
    widgets = getattr(hud, "buttons", None) if hud is not None else None
    if not isinstance(widgets, (list, tuple)) or not widgets:
        return None
    text = getattr(widgets[0], "text", None)
    return text if text in BUSY_DOTS else None


def stop_button_load(app):
    """回っているゲームの点送り（`display_button_load` の予約）を Clock から外す。

    外したら True。`process_choice` は自分で点送りを始めるので、
    回っているまま次の場面を起こすと2本になり、点が速くなる（GAME.md §2.4）。
    """
    loader = getattr(app, "display_button_load", None)
    if loader is None or not button_load_pending(app):
        return False
    try:
        from kivy.clock import Clock
        Clock.unschedule(loader)
    except Exception:
        return False
    return True


def button_load_pending(app):
    """ゲームの点送り（`display_button_load` の予約）が Clock に載っているか。

    真なら回っている。読めないときは None（呼ぶ側は「回っていない」として1回だけ回す。
    点が出ないより、1本多いほうがまし）。
    """
    try:
        from kivy.clock import Clock
        events = Clock.get_events()
    except Exception:
        return None
    for event in events or ():
        try:
            callback = event.get_callback()
        except Exception:
            callback = getattr(event, "callback", None)
        if getattr(callback, "__name__", "") == "display_button_load":
            return True
    return False


_AFTER_LOAD_ATTR = "_instantale_after_load"


def refresh_choices_after_load(ctx, write=None, tries=12, interval=0.25):
    """ロードのあと、名簿（party）が復元されてから選択肢を 1 度組み直す。

    ロード中に本体が選択肢を組む時点では `app.party` がまだ `['player']` で、同行者との会話を
    復元しても相手が仲間だと分からない（`302_` が「ここで別れる」を落とし、`301_` が依頼の
    選択肢を足した。実機）。名簿に同行者が入るまで（上限 `tries` 回、`interval` 秒おき）待ってから
    `refresh_choice_buttons()` を 1 度呼び、画面にも塗る（`paint_choices`。組み直すだけでは文字が
    古いままで、押される処理と食い違った。実機）。同行者が居ないセーブでは上限で 1 度呼ぶ（害は無い）。
    何本の MOD が呼んでも、1 回のロードで組み直すのは 1 度（後から入った層の見張りが勝つ）。
    Kivy の Clock が無ければ（ゲームの外）何もしない。
    """
    shared = getattr(sys, _AFTER_LOAD_ATTR, None)
    if not isinstance(shared, dict):
        shared = {"token": None}
        setattr(sys, _AFTER_LOAD_ATTR, shared)

    @ctx.wrap("__main__:InstantaleApp.load_game_new", required=False, safe=True)
    def load_game_new(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            from kivy.clock import Clock
        except Exception:
            return result
        token = object()
        shared["token"] = token
        left = [int(tries)]

        def check(_dt):
            if shared.get("token") is not token:
                return False                          # 別の層（または次のロード）が引き継いだ
            left[0] -= 1
            if not party_member_ids(self) and left[0] > 0:
                return True
            shared["token"] = None
            try:
                before = list(getattr(self, "to_display_buttons", []) or [])
                self.refresh_choice_buttons()
                after = list(getattr(self, "to_display_buttons", []) or [])
                done = paint_choices(self, after, ctx.log_exc)
                if write:
                    write("refreshed the choices after the load (party={}): {} -> {} via {}".format(
                        party_ids(self), before, after, "+".join(done) if done else "(nothing)"))
            except Exception:
                ctx.log_exc("after load: refresh_choice_buttons failed")
            return False
        Clock.schedule_interval(check, interval)
        return result


def describe_stores(app):
    """名簿の在り処と中身を1行で。切り分けのときこれが頼りになる。"""
    return "; ".join("{}={}".format(label, store_ids(store))
                     for label, store in party_stores(app)) or "(no party store found)"


def character_of(app, character_id):
    """id から Character を引く。`world.characters` は id の**文字列**が鍵。"""
    characters = getattr(getattr(app, "world", None), "characters", None)
    if isinstance(characters, dict) and character_id is not None:
        return characters.get(str(character_id))
    return None


def character_name(app, character_id, limit=40, fallback=None):
    """表示に使う名前。引けなければ `fallback`、無指定なら id をそのまま返す（空にはしない）。

    `fallback` は文言に混ぜる mod 向け（`302_` の「その仲間」、`311_` の
    「その相手」）。ログ向けには id が残る既定のままがよい。
    """
    name = getattr(character_of(app, character_id), "name", None)
    if isinstance(name, str) and name.strip():
        return frames.short(name, limit)
    return str(character_id) if fallback is None else fallback


# --------------------------------------------------------------------------
# 画面を触る側（ログと例外処理が要るので ctx を握る）
# --------------------------------------------------------------------------
def scheduler(ctx, tag="mod"):
    """「次のフレーム・メインスレッドで走らせる」関数を1つ作る。

        schedule = ui.scheduler(ctx, "party expand")
        schedule(fn)            # 次のフレームで
        schedule(fn, delay=0.5) # 0.5秒後に

    `Screen.schedule` との違いは **Kivy が無ければその場で実行する**こと。
    UI を整える 1xx 系の mod はオフライン検証（tools/tests/）でも
    同じ経路を通したいので、ゲームの外では即時実行に落ちる。
    ボタンを挿す mod は `Screen` の方を使うこと（あちらは
    Clock が無い＝画面が無いので、実行せず諦めるのが正しい）。

    Clock から呼ぶ `fn` の例外はここで握ってローダのログへ残す。
    Clock の中で投げるとゲームごと落ちる（TECH.md §5.1.2）ので、mod が素の関数を
    渡しても窓口の側で守る。ゲームの外の即時実行は握らない
    （検査で例外がそのまま見える方がよい）。
    """
    def guarded(fn):
        try:
            fn()
        except Exception:
            ctx.log_exc("{}: scheduled call failed".format(tag))

    def schedule(fn, delay=0.0):
        try:
            from kivy.clock import Clock
        except Exception:
            fn()              # ゲームの外（オフライン検証）ではその場で
            return
        try:
            Clock.schedule_once(lambda _dt: guarded(fn), delay)
        except Exception:
            ctx.log_exc("{}: could not schedule".format(tag))
    return schedule


#: `saver` が保存を呼ぶまでの待ち（秒）。同じ操作の中で続く書き換えを済ませてから保存する。
SAVE_DELAY = 0.15


def saver(ctx, write=None, tag="mod", delay=SAVE_DELAY):
    """ゲーム自身の `save_game` を少し待ってから呼ぶ関数を1つ作る。

        save_soon = ui.saver(ctx, write, "real estate")
        save_soon(app, "rent")      # 続けて呼べば、保存は最後の1回だけ

    `state/` の控えとセーブの両方にまたがる変更をした MOD が、控えを書いた直後に呼ぶ。
    控えはその場でファイルになるが、所持金や持ち物はメモリの中で動くだけなので、
    保存しないまま終えたりロードし直したりすると控えだけが進んだ形が残る
    （家や道がタダで手に入る、預けた品が控えと持ち物の両方に残る）。

    続けて呼ばれたら古い予約は捨てる（世代で見分ける。1回の操作で何度動かしても保存は1回）。
    実行は `scheduler` と同じく次のフレーム以降のメインスレッドで、ゲームの外では
    その場で保存する。落ちた `save_game` はローダのログへ残す。
    `330_` と `402_` が同じ形を持っていて、`331_` / `325_` が3本目・4本目になるところで移した。
    """
    schedule = scheduler(ctx, tag)
    generation = [0]

    def save_soon(app, why):
        generation[0] += 1
        mine = generation[0]

        def do_save():
            if mine != generation[0]:
                return
            try:
                app.save_game()
            except Exception:
                ctx.log_exc("{}: save_game after {} failed".format(tag, why))
                return
            if write is not None:
                write("{}: save_game complete".format(why))

        schedule(do_save, delay)

    return save_soon


def window_watcher(ctx, handler, attr, tag="mod"):
    """Kivy の窓の大きさが変わったら `handler` を呼ぶ、その結び役を作る。

        watch_window = ui.window_watcher(ctx, on_window_resize, WINDOW_ATTR, "party expand")
        watch_window()          # 何度呼んでも結ぶのは1本

    **`attr` は mod ごとに別の文字列**にすること（`MOD_WIDGET_PREFIX` を頭に付ける）。
    ぶつかると先に結んだ mod の手が外される（§3.2.3 の名前の断り）。

    ##### なぜ `Window` に印を残すのか

    ローダは注入し直すたびに mod のモジュールを作り直すが、
    **`kivy.core.window.Window` は作り直されない**。
    素直に `bind` するだけだと、注入の回数だけ手が積もって
    1回のリサイズで同じ処理が何度も走る。
    前回の手を `Window` の属性に残しておき、結ぶ前に外す。

    `hasattr` ではなく `frames.attr` で読むのは、失敗するルックアップが
    `201_probe_missing_attr` のトリップワイヤを自己発火させるため（TECH.md §6）。

    ##### なぜ「本文が変わったとき」では足りないのか

    窓だけ変えられると、本文を塗り直すフックは呼ばれない。
    そちらだけを見ている mod は、ボタンを古い座標に取り残す。
    だから窓の側も別経路で見る。

    ゲームの外（オフライン検証）では Kivy が無いので**何もしない**。
    窓が無いのだから結ぶ相手も居ない、というだけで異常ではない。

    `handler` は `scheduler` と同じく例外を握る包みに入れてから結ぶ
    （リサイズの通知も Kivy の中から呼ばれる）。`Window` に残す印も包みの方。
    """
    watching = [False]

    def guarded(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception:
            ctx.log_exc("{}: window resize handler failed".format(tag))
            return None

    def watch_window():
        if watching[0]:
            return False
        try:
            from kivy.core.window import Window
        except Exception:
            return False      # ゲームの外（オフライン検証）では窓が無い
        watching[0] = True
        # 注入し直したときに古い版の手が残らないよう、前のものを外してから結ぶ。
        previous = frames.attr(Window, attr, None)
        if previous is not None:
            try:
                Window.unbind(on_resize=previous)
            except Exception:
                pass          # 既に外れている（Kivy が畳んだ後）
        try:
            Window.bind(on_resize=guarded)
            setattr(Window, attr, guarded)
            return True
        except Exception:
            watching[0] = False
            ctx.log_exc("{}: could not watch the window size".format(tag))
            return False

    return watch_window


class Screen(object):
    """1つの mod から見た「選択肢と画面」。

        screen = ui.Screen(ctx, write, tag="quest offer", mark=MARK)

    `write` は mod 自身のログ関数。
    何が起きたかは mod のログに残したいので、
    ローダのログ（`ctx.log`）とは分けてある。
    `mark` はボタン辞書に付ける印のキーで、**mod ごとに別の文字列**にすること。
    """

    def __init__(self, ctx, write, tag="mod", mark=None, safe_cls=SAFE_CLS):
        self.ctx = ctx
        self.write = write
        self.tag = tag
        self.mark = mark
        self.safe_cls = safe_cls
        self._busy = {"on": False, "enabled": None}

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

        `cls_name` を省略すると無害な `JustSetButtonToNormalPhase` になる（自前クラス名を spec に書かない、
        の実装）。
        `mark` は `on_button_press` での横取りに使う印。
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

        判定は文字列ではなく印。
        同じ文字列のゲーム側ボタンを巻き込まないため。
        """
        if not self.mark or not isinstance(entry, dict):
            return None
        return entry.get(self.mark)

    def prune_stale(self, buttons, labels):
        """**印を失った自前ボタンの残骸**を取り除く。差し込む前に必ず通すこと。

        `PhaseSpec.to_dict()` がボタンをセーブに焼くとき、**書かれるのは `text` と
        `spec` だけで、こちらが足した印（`mod_action` 等）は落ちる**（実セーブで確認。GAME.md §2.16 ―
        `'依頼を受ける'` は入っているのに `'mod_action'` は無い）。

        するとタイトルへ戻る・ロード・再注入のあと、**印の無い自分のボタンが復元されている**。
        `mark_of()` はそれを自分のものと見なせないので重複判定をすり抜け、
        同じボタンが2つ並ぶ。
        しかも復元された方は spec が `JustSetButtonToNormalPhase` なので押しても無反応 ― 見た目は同じなのに片方だけ効かない、
        という最も分かりにくい壊れ方になる。

        取り除く条件は **「自分のラベル」かつ「印がどれも無い」かつ「無害
        spec」** の3つ揃ったときだけ。
        ゲーム側・他 MOD の同名ボタンを巻き込まないための保険で、
        `labels` は完全一致か前後の括弧付き（`'この話から依頼を作る（誰か）'`）を見るため**前方一致**で照合する。

        「印がどれも無い」の判定に**自分の印だけを見てはいけない**。
        `302_` の `やめておく` と `309_` の `やめておく` のように、
        別々の MOD が同じ文言のボタンを出すことがある ―
        `mark_of()` は自分の印しか見ないので、
        他 MOD の生きているボタンが「印が無い」に見えて消える（VERIFICATION_LOG.md
        §2.31。`309_` の確認画面からキャンセルが最初から消える壊れ方をする）。
        セーブから復元された残骸は印を1つも持たないので、
        `MARK_PREFIX` で始まるキーが1つでもあれば残骸ではないと分かる。

        取り除いた分は呼び出し側が新しい印つきで差し直すので、
        残骸は「消える」のではなく「生き返る」。
        """
        if not isinstance(buttons, list) or not labels:
            return []
        if not self.mark:
            # 印を持たない Screen（`300_` のようにボタンを作らない
            # MOD）では「自分のもの」を見分けられない。
            # ラベルだけで消すとゲーム側の同名ボタンまで巻き込むので、**何もしない**のが正しい。
            self.write("{}: prune_stale skipped (this Screen has no mark)"
                       .format(self.tag))
            return []
        removed, kept = [], []
        for entry in buttons:
            if self._is_stale(entry, labels):
                removed.append(entry.get("text"))
                continue
            kept.append(entry)
        if removed:
            buttons[:] = kept
            self.write("{}: dropped {} stale button(s) without our mark: {}".format(
                self.tag, len(removed), removed))
        return removed

    def _is_stale(self, entry, labels):
        if not isinstance(entry, dict) or self.mark_of(entry) is not None:
            return False
        if self.marked_by_a_mod(entry):
            return False        # 他の MOD が今この場で挿したもの。残骸ではない
        if spec_cls_name(entry) != self.safe_cls:
            return False
        text = entry.get("text")
        if not isinstance(text, str):
            return False
        return any(text == label or text.startswith(label) for label in labels)

    @staticmethod
    def marked_by_a_mod(entry):
        """どれかの MOD の印が付いているか（自分のものとは限らない）。

        セーブに焼かれるのは text と spec だけ ＝ **復元された残骸に印は
        1つも残らない**。
        逆に印があれば、いまこの場で誰かが挿したものなので触ってはいけない。
        `MARK_PREFIX` の約束が効くのはここ。
        """
        if not isinstance(entry, dict):
            return False
        return any(isinstance(key, str) and key.startswith(MARK_PREFIX)
                   for key in entry)

    @staticmethod
    def back_button_index(buttons):
        """ゲーム側の「やめる」の位置。無ければ None（＝一覧ではない／まだ組み上がっていない）。

        無害 spec（`SAFE_CLS`）で、どの MOD の印も付いていないもの。
        自前のボタンも同じ spec を使うので、印で除く。
        `320_` / `326_` / `404_` に同じ本体が写されていた。
        """
        for index, entry in enumerate(buttons):
            if spec_cls_name(entry) == SAFE_CLS and not Screen.marked_by_a_mod(entry):
                return index
        return None

    def instantiate_spec(self, app, entry_or_spec):
        """ボタンの `PhaseSpec` から、それが呼ぶはずのマネージャを組み立てる。

        **引数を自分で考えない**のが要点。
        `QuestChoiceManager` の `quest_type` は推測して組み立てるとゲームが落ちる（GAME.md
        §2.2）。
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

        `refresh_choice_buttons` は `to_display_buttons` と
        `display_button_map` を組み直すところまでで、**そこまで正しくても画面は塗り替わらない**。

        塗っているのは HUD 側の
        `InstanTaleHUD.update_button_texts(self, instance, value)`。
        **監視されているプロパティは HUD 側にあり、
        `app.to_display_buttons` は監視対象ではない**ので、
        そこをどう触っても画面は変わらない。
        この関数を直接呼ぶのが正解（GAME.md §2.3）。

        ついでに `display_button_load(self, dt)`（ゲーム自身のボタン読み込み。
        Clock コールバックの形なので `dt` を渡せば直接呼べる）も通す。
        描画のためにゲームを落とさないよう、例外はどれも外へ出さない。
        """
        return paint_choices(app, texts, self._oops)

    def paint_party(self, app):
        """HUD の仲間欄を塗り直す。効いた手段の一覧を返す。

        パーティの増減は `app.party` を書き換えるだけでは画面に出ない
        ― 選択肢ボタンと同じ構図で、塗るのは別の関数。
        ゲーム自身が持っている
        2つをそのまま通す（引数を作らずに済む形になっている）:

            InstantaleApp.update_party_member(self, dt)   Clock コールバックの形
            InstanTaleHUD.update_party_display(self, *args)

        `dt` は Clock が渡す経過秒なので `0` でよい。
        HUD は属性名ではなく **型**で探す。
        例外はどれも外へ出さない。
        """
        done = []

        updater = getattr(app, "update_party_member", None)
        if callable(updater):
            try:
                updater(0)
                done.append("update_party_member")
            except Exception:
                self._oops("update_party_member failed")

        hud = find_hud(app)
        display = getattr(hud, "update_party_display", None) if hud is not None else None
        if callable(display):
            try:
                display()
                done.append("hud.update_party_display")
            except Exception:
                self._oops("hud.update_party_display failed")
        elif hud is None:
            done.append("hud not found")

        return done

    # -- 待機表示（「.」→「..」→「...」）-----------------------------------
    #
    # **点を送っているのはゲーム自身**（GAME.md §2.4。`234_probe_busy_display` の実測）:
    #
    #   * `is_button_enabled` が False のあいだ、Clock に載った
    #     `InstantaleApp.display_button_load` が約0.3秒ごとに `.` → `..` → `...` を
    #     `to_display_buttons` に書いて塗り、次のコマを自分で予約し直す
    #   * 待機中に `display_button_load` を呼ぶと、その呼び出しからも予約が始まる
    #     （**回し手が1本増える**）。True に戻ると、どの回し手も今の一覧を塗って止まる
    #   * `text_send_button.disabled = True`（自由入力を塞ぐ）
    #   * `app.text_input_disabled` は False のまま ＝ **これは機構ではない**
    #
    # だからこちらは旗を下ろすだけにし、回っていなければ1回だけ回し始める。
    # 以前は自前でコマを送っていて、ゲームの回し手と二重に回ったうえ、
    # 塗るたびに `display_button_load` を呼んで回し手を増やしていた（点が飛び、
    # 解いた後にも点が一瞬戻った。実機 2026-09-25）。
    #
    # **`app.buttons`（spec の一覧）には触らない。** ゲームも表示だけ差し替えて
    # いるので、こちらも表示だけにすれば後始末が要らない。

    def busy_slots(self, app):
        """待機表示を出す枠の数。実物のボタンウィジェット数に合わせる。"""
        hud = find_hud(app)
        widgets = getattr(hud, "buttons", None) if hud is not None else None
        if isinstance(widgets, (list, tuple)) and widgets:
            return len(widgets)
        return max(1, len(list(getattr(app, "to_display_buttons", []) or [])))

    def set_send_button(self, app, enabled):
        """自由入力の送信ボタンを塞ぐ／戻す。効かなくても処理は続ける。"""
        hud = find_hud(app)
        name = "enable_text_send_button" if enabled else "disable_text_send_button"
        toggle = getattr(hud, name, None) if hud is not None else None
        if callable(toggle):
            # HUD のウィジェットを触るのでメインスレッドへ回す。
            self.schedule(toggle, 0)

    def busy_state(self, app):
        """待機表示になっているかを見るための一行。前後で記録する用。"""
        return "is_button_enabled={!r} text_input_disabled={!r} buttons={!r}".format(
            getattr(app, "is_button_enabled", "<missing>"),
            getattr(app, "text_input_disabled", "<missing>"),
            list(getattr(app, "to_display_buttons", []) or [])[:6])

    def is_busy(self):
        """いま待機表示を出しているか。"""
        return bool(self._busy["on"])

    def busy_on(self, app):
        """待機表示を出す。ゲーム自身と同じ出し方（上の説明。GAME.md §2.4）。

        LLM を待つ間これを出さないと、**画面が固まったように見える**（GAME.md
        §2.4）。

        ワーカースレッドからも呼べる。ここで直に触るのは旗と一覧（`to_display_buttons`）
        だけで、画面に触る手（送信ボタン・点送りの始動）は Clock へ回す。
        ゲームが選択肢を組んだその場で覆いたいとき（組んだ次のフレームでゲームが塗る）に使う。

        枠に点が出ていれば（ゲームの待機の直後）、その点を一覧に書いておく。
        ゲームは待機を終えた次のフレームで今の一覧を塗るので、書いておかないと
        そのフレームだけ選択肢が見える。点送りは一覧の文字から次のコマを決めるので、
        続きから進む（実機 2026-09-25）。

        出している間にもう一度呼ばれても、戻す値（`is_button_enabled`）は
        取り直さない。取り直すと自分が立てた `False` を覚えてしまい、
        `busy_off` の後も選択肢が押せないまま残る。
        入れ子は数えない（先に来た `busy_off` で解く）。`busy_on` と対でなく
        `busy_off` だけを呼ぶ経路があるので、数えると解けなくなる側に倒れる。
        """
        busy = self._busy
        again = bool(busy["on"])
        if not again:
            busy["enabled"] = getattr(app, "is_button_enabled", None)
        busy["on"] = True
        slots = self.busy_slots(app)

        try:
            app.is_button_enabled = False
        except Exception:
            self._oops("cannot clear is_button_enabled")
        frame = shown_dots(app)
        if frame is not None:
            try:
                app.to_display_buttons = [frame] * slots
            except Exception:
                self._oops("cannot hold the dots")
        self.set_send_button(app, False)

        if again:
            self.write("{}: busy on again ({} slots)".format(self.tag, slots))
            return slots

        def start():
            # 待っている間に解かれていたら何もしない。
            if not busy["on"]:
                return
            pending = button_load_pending(app)
            if pending:
                # ゲームの点送りが回っている（ゲーム自身の待機の直後など）。増やさない。
                self.write("{}: the game is already turning the dots".format(self.tag))
                return
            loader = getattr(app, "display_button_load", None)
            if callable(loader):
                loader(0)       # 1コマ目を塗り、以後はゲームが自分で予約し直す

        self.schedule(start, 0)
        self.write("{}: busy on ({} slots) -> {}".format(
            self.tag, slots, self.busy_state(app)))
        return slots

    def busy_off(self, app, restore=True):
        """待機表示を解く。

        `restore=False` は「この後すぐ別の画面を出すので、
        選択肢は塗らない」（掲示板を開き直す経路。
        ここで元に戻すと一瞬だけ古い画面が見える）。
        """
        busy = self._busy
        busy["on"] = False
        try:
            app.is_button_enabled = (True if busy["enabled"] is None
                                     else busy["enabled"])
        except Exception:
            self._oops("cannot restore is_button_enabled")
        self.set_send_button(app, True)
        if restore:
            # `app.buttons` は触っていないので、いまの中身を塗り直すだけでよい。
            self.apply_buttons(app, None, "busy off")
        self.write("{}: busy off (restore={})".format(self.tag, restore))

    def apply_buttons(self, app, entries, tag):
        """選択肢を差し替えて画面に反映する。**必ず次のフレーム**で行う。

        押下と同じ流れの中で差し替えると
        `app.buttons` は変わるのに**画面は古いまま**になる。
        見えているものと押されるものが食い違うので、
        確認画面が出ていないように見えて裏では新しい選択肢が押せてしまう。

        ゲーム自身は押下の処理の中で描画しているので、
        こちらの差し替えはその **後**に置く必要がある。
        `Clock.schedule_once(..., 0)` なら次のフレーム、
        かつメインスレッドなので順序とスレッドが同時に片付く（選択肢を組むゲーム側の
        `execute` は別スレッドで走ることがある）。

        `entries` に None を渡すと差し替えはせず、
        いま
        `app.buttons` に入っているものを塗り直すだけ（ゲームが組んだ一覧に手を入れた場合用）。
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

        ゲーム自身は選択肢を変えるとき必ず
        `process_choice(マネージャ, 文字列)` を通し、
        その中で `execute` が別スレッドへ渡される。
        描画の面倒はその経路が見ているので、同じ経路に乗せる。
        フェーズは `execute(choice_text)` だけを持つ自前クラスでよい。
        **`PhaseSpec` には決して載せない。**

        待機中（点送りが回っている）なら外してから起こす。
        `process_choice` は自分で点送りを始めるので、残すと2本になって点が速くなる
        （実機 2026-09-25。締めの場面の間だけ 0.1 秒刻みになった。GAME.md §2.4）。
        """
        if getattr(app, "is_button_enabled", None) is False and stop_button_load(app):
            self.write("{}: stopped the running dots before {}".format(
                self.tag, type(phase).__name__))
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
    def _others_busy(self, app):
        """手が空いていない理由。ただし**自分が出している待機表示は数えない。**

        `busy_on` は `is_button_enabled=False` にする。
        これは「ゲームが忙しい」印ではなく「こちらが待たせている」印なので、
        そのまま
        `busy_signals` に通すと**自分の出した印が消えるのを自分で待つ**ことになる。
        誰も消さないので必ずタイムアウトまで進まない。

        実際に踏んだ: `301_` の「会話を閉じてから掲示板を開く」経路は、
        NPC 一覧が一瞬見えるのを隠すため `busy_on` してから
        `end_conversation` を呼ぶ。
        その中の `when_idle` がこの印を見て待ち続け、
        掲示板が開くのが
        `proceed_on_timeout` の分だけ遅れていた（`tools/tests/test_quest_offer.py` が捕まえていた失敗）。

        他の2つ（`is_adding_text` / `is_popup_window_opened`）はゲーム側が立てるものなので、
        そのまま数える。
        """
        reasons = busy_signals(app)
        if self.is_busy():
            reasons = [r for r in reasons if r != "is_button_enabled=False"]
        return reasons

    def when_idle(self, app, then, timeout=IDLE_TIMEOUT, settle=IDLE_SETTLE,
                  poll=IDLE_POLL, cancel_if=None, proceed_on_timeout=False,
                  tag="idle"):
        """手が空いてから `then` を走らせる。

        移動の後始末（テキストの流し込み・ボタンの張り替え）の最中に割り込むと噛み合わない（`300_` の実測）。
        **会話の終了処理も同じ**で、要約の流し込みが続いている間に掲示板を開いたり `add_text` したりすると、
        こちらの出力が押し流される。

        `cancel_if` は理由の文字列（または None）を返す関数。
        前提が崩れたら取り消す（待っている間に施設を出た、戦闘に入った等）。
        `proceed_on_timeout` は「待ちきれなくても実行する」。
        既に確定した行動の後始末では、遅れても実行する方が正しい。

        既に手が空いているなら見張りは立てず、
        その場で予約する（無駄に 1ポーリング分待たないため）。
        """
        deadline = time.monotonic() + timeout

        def tick(_dt):
            try:
                if cancel_if is not None:
                    reason = cancel_if()
                    if reason:
                        self.write("{}: cancelled ({})".format(tag, reason))
                        return False
                busy = self._others_busy(app)
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

        # 1回目はその場で見る。
        # False が返ったなら片が付いている（実行を予約した、
        # または取り消した）ので見張りは要らない。
        if tick(0.0) is False:
            return True
        return self._interval(tick, poll)

    # -- 会話を閉じる -------------------------------------------------------
    def end_conversation(self, app, end_entry, follow_up, end_text=None,
                         on_abort=None, poll=END_POLL, timeout=END_TIMEOUT,
                         tag="end conversation"):
        """会話をゲーム自身の経路で閉じてから `follow_up(app)` を走らせる。

        閉じずに画面を変えると **NPC の立ち絵が消えずに移動しても付いてくる**（`301_` で実際に起きた）。
        会話は「状態」であって画面ではない。
        立ち絵の片付けも関係値の更新も会話の要約も終了処理の中にある。

        起こし方は「画面にある『会話を終了する』ボタンの
        spec をそのまま使い、**`end_text` だけ差し替える**」。
        `end_text` は `'<行動: 会話を終了する>'` という自由記述なので、
        そこに事情を書いておけば会話の要約とライフログにその通り残る。
        引数の意味を推測せずに済むうえ、記録も正しくなる（`302_` で確立）。

        終了処理は要約で LLM を回すことがあるので、
        `in_conversation` が落ちるのを見張り、
        落ちてから**手が空くのを待って** `follow_up` を呼ぶ。

        `on_abort(理由)` は閉じられなかったときに呼ばれる。
        **待ちが打ち切られる経路が必ずあるので、
        呼び出し側は「実行中」の印をここで戻すこと**（でないと以後ずっとボタンが効かなくなる）。
        戻り値は同期的に失敗しなかったか。
        """
        def abort(reason):
            self.write("{}: aborted ({})".format(tag, reason))
            if on_abort is not None:
                try:
                    on_abort(reason)
                except Exception:
                    self._oops("{}: on_abort failed".format(tag))

        if not getattr(app, "in_conversation", False):
            # もう会話が終わっている（施設側から入った等）。
            # そのまま進む。
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
            # 見張りを立てられなかった（Clock が無い）。
            # 閉じる指示は既に出しているので、呼び出し側の「実行中」の印は必ず戻す。
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


# ---------------------------------------------------------------- ダメージの出どころ
# 戦闘で HP を動かす mod と、その増減を画面に出す mod の受け渡し。
# `319_battle_tactics` が「この増減は『泥の浸食』のぶん」と控え、
# `308_battle_damage_display` が行に出どころを添える
# （毎ターンの継続ダメージは地の文と切り離れて出るので、
# 出どころが無いと新しいバグに見える。mod どうしは import しない ―
# 共有の語彙はここへ。TECH.md §3.2.3）。

#: 控えの寿命（秒）。報告点は同じ1手の中にあるので、実際は1秒も生きない。
#: 表示側が居ない（308_ を切っている）ときに積もらないための時限。
_DAMAGE_NOTE_TTL = 12.0
_DAMAGE_NOTE_CAP = 32

_damage_notes = []


def note_damage(name, amount, label):
    """HP をこれから動かす側が、増減の出どころを控える。

    `amount` は実際に動かした量（符号は見ない）。
    """
    now = time.monotonic()
    _damage_notes[:] = [note for note in _damage_notes
                        if now - note[0] <= _DAMAGE_NOTE_TTL]
    _damage_notes.append((now, str(name), int(round(abs(amount))), str(label)))
    del _damage_notes[:-_DAMAGE_NOTE_CAP]


def take_damage_notes(name, amount):
    """`name` の増減 `amount` に合う出どころを取り出して消す。無ければ None。

    1件がぴったり合えばその名前。
    複数の控えの合計が合えば「・」で繋いだ名前
    （毒と燃焼が同じ報告に畳まれると、表示側には合計しか見えないため）。
    どちらでもなければ何も消さない（他人の増減に他人の出どころを貼らない）。
    """
    now = time.monotonic()
    _damage_notes[:] = [note for note in _damage_notes
                        if now - note[0] <= _DAMAGE_NOTE_TTL]
    amount = int(round(abs(amount)))
    mine = [note for note in _damage_notes if note[1] == str(name)]
    if not mine:
        return None
    for note in mine:
        if note[2] == amount:
            _damage_notes.remove(note)
            return note[3]
    if sum(note[2] for note in mine) == amount:
        labels = []
        for note in mine:
            _damage_notes.remove(note)
            if note[3] not in labels:
                labels.append(note[3])
        return "・".join(labels)
    return None
