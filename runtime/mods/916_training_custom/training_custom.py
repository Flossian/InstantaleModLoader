# -*- coding: utf-8 -*-
"""機能調整: 訓練所の代金・修行の年数・1年で進む日数を設定で変えられるようにする。

素のゲームの訓練所は、代金が 300G で固定、1回の修行が3年、
活動1段で「その年数 × 365 日」の暦が一度に進む（実測は GAME.md §2.17。
録ったのは `231_probe_training`）。
3年の活動を1つ選ぶと **1095 日**が飛ぶので、訓練所は事実上使いどころが無い。
この MOD はその3つを mod.json の設定から変えられるようにする。
既定値はすべて素のゲームの値で、そのままなら挙動は何も変わらない。

変更点は3つ。

| 何を変えるか | どこで変えるか |
|---|---|
| 代金 | `TrainingStartManager.__init__` に渡る `training_price` を差し替える |
| 1回の修行の年数 | 同じ `__init__` の `training_years` を差し替える |
| 1年で進む日数 | 段の実行中だけ、ローダの日数送りの関所へ「この段は何日か」を出す |

留意点:

- **代金と年数はゲームが自分で受け取る引数を差し替える。**
  ボタンの spec（`args=[3, 300]`）は触らない（それはセーブに入る側）。
  差し替えた値でゲーム自身が引き落とし、ゲーム自身が残り年数を数える。
  `314_area_move_custom` の前払い調整のような細工が要らないのは、
  訓練所の代金が**引数で渡ってくる**から（馬車の運賃は渡ってこない）。
  それでも引き落としが設定額にならなかった回は `execute` の前後の所持金で気付けるので、
  差額をその場で戻して WARN を残す（別の場所で徴収しているビルドに備えた保険）
- **日数はローダの関所**（`durations`。TECH.md §3.3.3）に望みを出すだけで、
  `elapse_days` はこちらでは包まない。
  望むのは「ゲームが送ろうとした日数を、設定の1年の長さで測り直した数」で、
  段の窓（`TrainingPhaseManager.execute` の間）の外では何も望まない
  ＝ 街移動・宿泊・他の依頼の日数送りには触らない。
  **活動ごとの年数はゲームのまま**にしてある（残り年数の減り方と、
  残り年数に収まる活動だけを並べる判定はゲームの内側に在って、引数には出てこない）。
  1年の長さだけを変えれば、その勘定はゲームの中で辻褄が合ったまま暦だけが縮む
- ボタンは `text` だけ触る（`314_` と同じ）。
  代金を変えたときだけ `訓練を受ける(300G)` を書き直し、
  段のボタン（`ただ鍛える(1年)`）は**既定では触らない**。
  1年の長さを変えても年数の表示は変わらないので、実日数を出したいときは
  設定の表示テンプレートに `{days}` を入れる

開発中（9xx。TECH.md §2.6）なので、遊び方も検証の記録も同じフォルダの DOC.md にある
（§1 が MODS.md 相当、§3 が実機で見る8点）。
"""

import os
import re
import sys
import time

from instantale_modloader import durations, ui
from instantale_modloader.state import UNKNOWN_WORLD, WorldStore, world_key

LOG_BASENAME = "training_custom.log"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_training_custom_store__"
SETTINGS_STORE_ATTR = "__instantale_training_custom_settings_store__"

# ボタンには何も足さないが、`ui.Screen` の道具（say / apply_buttons）を使うので
# 印のキーは他の MOD と別にして持つ（TECH.md §3.3）。
MARK = "mod_training_custom"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む。
# TECH.md §3.8）。これが全ワールド共通の一括設定。
# ワールド個別の値は同梱の tool.py が `state/training_custom/<世界>.json` に書き、
# `apply()` がその世界を見ているあいだだけここへ上書きする（`refresh_world`）。
# 他のファイルへ移さないこと。
# 既定値はすべて素のゲームの値。
# 素の値のままなら、この MOD はその項目に一切触らない。
# 「-1 で無効」のような番人値は使わない。
# 素の値との照合は下の GAME_* 定数。

# 訓練の代金（素のゲームは 300G で固定。実測）。
# 0 にするとタダ。
TRAINING_PRICE = 300

# 1回の修行の年数（開始時の残り年数。素のゲームは3年）。
# 活動を1つ選ぶたびにその活動の年数ぶん減り、0 になると卒業する。
# 増やすと1回の修行で活動を何段も受けられる。
COURSE_YEARS = 3

# 1年で進む日数（素のゲームは 365 日）。
# 3年の活動は「3 × この日数」進む。
# 30 にすれば `新たな技を学ぶ(3年)` は 90 日で終わる（年数の表示は変わらない）。
DAYS_PER_YEAR = 365

# 訓練を受けるボタンの表示テンプレート。
# 使える変数: {name}（ボタンから料金の括弧を落とした呼び名）{price} {years}。
# **代金を変えたときだけ**この形で表示し直す（素のままならゲームの表示のまま）。
START_BUTTON = "{name}({price}G)"

# 修行内容のボタンの表示テンプレート。
# 使える変数: {name} {years}（ゲームが出している年数）{days}（実際に進む日数）。
# 既定はゲームの表示と同じ形なので、既定のままなら1文字も変わらない。
# 1年の日数を縮めたときに実日数を出したいなら `{name}({days}日)` にする。
PHASE_BUTTON = "{name}({years}年)"

# 手持ちが設定した代金に足りないときの一言（`314_` / `315_` と同じ形）。
REFUSE_TEXT = "（訓練の代金{price}Gに足りない ― 手持ち{gold}G）"

# ---------------------------------------------------------------- コード側の設定
#: 訓練所のマネージャ（`targets.txt` の実在クラス。GAME.md §2.17 の表）。
#:   TrainingStartManager(app, training_years, training_price)
#:   TrainingPhaseManager(app, training_type, remaining_years, training_log)
START_CLS = "TrainingStartManager"
PHASE_CLS = "TrainingPhaseManager"

# 素のゲームの値。
# **設定がこれと同じ項目には触らない**ための照合値。
# 3つとも実測で、日数と年数はローダの窓口が持っているものを借りる
# （同じ数字を2箇所に書かない。`231_probe_training`、GAME.md §2.17）。
GAME_PRICE = 300
GAME_COURSE_YEARS = durations.GAME_TRAINING_COURSE_YEARS
GAME_DAYS_PER_YEAR = durations.GAME_TRAINING_DAYS_PER_YEAR

# 修行内容のボタンの年数（`ただ鍛える(1年)` の 1）。
# **ゲームが出している数をそのまま読む**（こちらの表から引かない）。
# 活動の種類ごとの年数はゲームの内側に在り、引数にも出てこないので、
# 画面に出ている数字が唯一の出どころになる。
YEARS_RE = re.compile(r"[（(]\s*(\d+)\s*年\s*[)）]\s*$")

# ラベル末尾の括弧（`(300G)` `(1年)`）。呼び名だけを取り出すのに使う。
TAIL_RE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")

# ワールド個別の設定の控え。
# 書くのは同梱の tool.py だけ。ゲーム中はこの MOD が読む。
SETTINGS_DIRNAME = "training_custom"
SETTING_NAMES = ("TRAINING_PRICE", "COURSE_YEARS", "DAYS_PER_YEAR",
                 "START_BUTTON", "PHASE_BUTTON", "REFUSE_TEXT")


class _SafeDict(dict):
    """テンプレートに無い変数名が来ても落とさない（`{typo}` はそのまま残る）。"""

    def __missing__(self, key):
        return "{" + str(key) + "}"


def fmt(template, **values):
    """設定のテンプレートを埋める。壊れたテンプレートでも素の文字列で返す。

    埋めた後に通貨の表記を今の表記へ直す（`130_` が差し替えていれば
    `訓練を受ける(500G)` → `訓練を受ける(500円)`）。
    設定のテンプレートは素のゲームの言い方（`G`）のままでよい。
    """
    try:
        filled = str(template).format_map(_SafeDict(values))
    except Exception:
        filled = str(template)
    return ui.rewrite_coins(filled)


def phase_years(label):
    """修行内容のボタンから年数を読む。読めなければ None。"""
    match = YEARS_RE.search(str(label or ""))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def plain_name(label):
    """ラベルから末尾の括弧を落とした呼び名。落とすものが無ければそのまま。"""
    return TAIL_RE.sub("", str(label or "")) or str(label or "")


def scaled_days(days, per_year=None):
    """ゲームが送ろうとした日数を、設定の1年の長さで測り直す。

    ゲームは「活動の年数 × 365 日」を送ってくるので、
    それを `365` で割って設定の日数を掛ければ、**活動の種類を知らなくても**
    その段の実日数になる（種類ごとの年数はゲームの内側に在って読めない）。
    素の 365 のままなら同じ数＝触らない。1日は必ず進める（0 日の段を作らない）。

    `per_year` は自己検証が設定と無関係に式を確かめるための引数で、
    実経路では常に設定（`DAYS_PER_YEAR`）を読む。
    """
    per_year = int(DAYS_PER_YEAR) if per_year is None else int(per_year)
    days = int(days)
    if per_year == GAME_DAYS_PER_YEAR:
        return days
    return max(1, int(round(days * per_year / float(GAME_DAYS_PER_YEAR))))


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="training custom", mark=MARK)

    # **置き場は `sys`。** `apply()` は1プロセスで何度も走り、当て直しは背景スレッドの
    # `boot()` から来る（未 import のモジュールが現れた時＝最初の LLM リクエストの時）。
    # 訓練の最中にそれが挟まると、ここで作り直した空の器を新しいラッパが握り、
    # 段の窓が None のまま素の 1095 日が通る（`314_` が同じ理由で `sys` に置いている）。
    state = getattr(sys, STATE_STORE_ATTR, None)
    if state is None:
        state = {
            # いま `TrainingPhaseManager.execute` の中に居るかの窓。
            "phase": None,
            # ボタンと引数から読み取った素の代金（読めた最新の値）。
            "game_price": None,
            # 自分が書いたラベル。画面がラベルを組み直さないビルドで同じ処理が
            # もう一度来たとき、**自分の書いた値を素の値として読み込まない**ための目印。
            "ours": set(),
            # 年数が読めなかったラベル（同じものを何度もログに出さない）。
            "unknown": set(),
        }
        setattr(sys, STATE_STORE_ATTR, state)

    # ワールド個別の設定。控えがあればその世界を見ているあいだだけ上書きし、
    # 無い世界では一括設定（ローダが注入した値）へ戻す。
    settings_store = getattr(sys, SETTINGS_STORE_ATTR, None)
    if not isinstance(settings_store, WorldStore):
        settings_store = WorldStore(ctx, SETTINGS_DIRNAME, default=dict, write=write)
        setattr(sys, SETTINGS_STORE_ATTR, settings_store)
    elif settings_store is not None and hasattr(settings_store, "rebind"):
        settings_store.rebind(ctx, write)
    # 注入のたびにモジュールは作り直され、一括設定を注入してからここへ来る（TECH.md §3.8）。
    # だからここで控えた値がそのまま一括設定（`sys` に固定してはいけない。
    # 一括設定を変えて注入し直しても古い値が残る）。
    base_settings = {name: globals()[name] for name in SETTING_NAMES}
    active_settings = [None]    # 直前に反映した (世界, 値) の署名

    def refresh_world(app=None):
        """現在のワールドの控えを読み直す。変わっていれば True。"""
        current_world = UNKNOWN_WORLD
        if app is None:
            try:
                app = ui.find_app()
            except Exception:
                app = None
        try:
            if app is not None:
                current_world = world_key(app)
        except Exception:
            pass
        record = {}
        if isinstance(current_world, str) and current_world != UNKNOWN_WORLD:
            try:
                loaded = settings_store.load(current_world, fresh=True)
                if isinstance(loaded, dict):
                    record = loaded
            except Exception:
                ctx.log_exc("training custom: cannot read world settings")
        values = {}
        for name in SETTING_NAMES:
            default = base_settings[name]
            value = record.get(name, default)
            values[name] = value if type(value) is type(default) else default
        signature = (current_world,
                     tuple((name, values[name]) for name in SETTING_NAMES))
        changed = signature != active_settings[0]
        if changed:
            globals().update(values)
            active_settings[0] = signature
        return changed

    # 起動時に現在ワールドが分かっていれば即時に反映する。
    refresh_world()

    # `ctx.mod_dir` はフックの中では読めない（apply() の間だけ）ので、ここで控える。
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "training_custom"

    def training_for(app):
        """ローダの窓口（`durations.TRAINING`）に置く答え。

        活動の種類ごとの年数は渡さない（こちらは変えないので、窓口が素の値で埋める）。
        読む側はこの MOD の名前を知らず、窓口に聞くだけ（TECH.md §3.3.2）。
        """
        return {"days_per_year": max(1, int(DAYS_PER_YEAR)),
                "course_years": max(1, int(COURSE_YEARS))}

    durations.declare(durations.TRAINING, training_for, owner=owner, write=write)

    def set_gold(app, value):
        """所持金を書く。型を保つ（float の世界に int を混ぜない）。"""
        player = getattr(app, "player", None)
        current = getattr(player, "gold", None)
        player.gold = float(value) if isinstance(current, float) else int(round(value))

    # ============================================================ ボタンの表示
    def relabel_start(old):
        """訓練を受けるボタンの新しいラベル。触らないなら None。

        代金を変えたときだけテンプレートで表示し直す。
        素の値のままならゲームの表示（`訓練を受ける(300G)`）をそのまま残す。
        """
        if int(TRAINING_PRICE) == GAME_PRICE:
            return None
        new = fmt(START_BUTTON, name=plain_name(old), price=int(TRAINING_PRICE),
                  years=int(COURSE_YEARS))
        return new if new != old else None

    def relabel_phase(old):
        """修行内容のボタンの新しいラベル。触らないなら None。

        年数はゲームが出している数をそのまま使う（こちらは活動の年数を変えない）。
        既定のテンプレートはゲームの表示と同じ形なので、既定のままなら None が返る。
        """
        years = phase_years(old)
        if years is None:
            if old not in state["unknown"]:
                state["unknown"].add(old)
                write("no years in the phase label {!r}; leaving it alone "
                      "(the game may have changed its wording)".format(old))
            return None
        new = fmt(PHASE_BUTTON, name=plain_name(old), years=years,
                  days=years * max(1, int(DAYS_PER_YEAR)))
        return new if new != old else None

    def relabel(app, cls_name, make):
        """そのクラスのボタンのラベルを書き直す。1つでも変えたら True。

        触るのは `text` だけ（spec と `args` はゲーム自身のものを残す）ので、
        この MOD を外しても押下の挙動は壊れない。
        """
        changed = False
        buttons = getattr(app, "buttons", None) if app is not None else None
        if not isinstance(buttons, (list, tuple)):
            return False
        for entry in buttons:
            if not isinstance(entry, dict) or ui.spec_cls_name(entry) != cls_name:
                continue
            old = entry.get("text") or ""
            if not old or old in state["ours"]:
                continue          # 自分が書いたラベルは読み直さない
            new = make(old)
            if new and new != old:
                entry["text"] = new
                state["ours"].add(new)
                changed = True
                write("label: {!r} -> {!r} ({})".format(old, new, cls_name))
        return changed

    def repaint(app, cls_name, make):
        """ラベルを書き直して、変わっていれば次のフレームで塗り直す。"""
        try:
            if relabel(app, cls_name, make):
                screen.apply_buttons(app, None, "relabel")
        except Exception:
            ctx.log_exc("training custom: cannot relabel the buttons")

    @ctx.wrap("__main__:DisplayTrainingChoice.update_button_display",
              required=False, safe=True)
    def choice_buttons(orig, self, *args, **kwargs):
        """`訓練を受ける(300G)` が並び終えた後、ラベルの `text` だけを書き直す。

        素の代金はここで（書き換える前のラベルから）読み取って控える。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is None:
                return result
            refresh_world(app)
            for entry in getattr(app, "buttons", None) or []:
                if isinstance(entry, dict) and ui.spec_cls_name(entry) == START_CLS \
                        and (entry.get("text") or "") not in state["ours"]:
                    price = ui.parse_coin(entry.get("text") or "")
                    if price is not None:
                        state["game_price"] = price
            repaint(app, START_CLS, relabel_start)
        except Exception:
            ctx.log_exc("training custom: cannot relabel the training choice")
        return result

    # ============================================================ 代金と年数
    @ctx.wrap("__main__:{}.__init__".format(START_CLS), required=False)
    def start_init(orig, self, app, *args, **kwargs):
        """ゲームが受け取る `training_years` / `training_price` を差し替える。

        位置で決め打ちせず、**渡ってきた数だけ**を触る
        （引数が減ったビルドでも、そこには何も足さずに素通しする）。
        差し替えた値でゲーム自身が引き落とし、ゲーム自身が残り年数を数えるので、
        画面の文言（`あと3年間`）も勝手に追随する。
        """
        argv = list(args)
        try:
            refresh_world(app)
            if len(argv) >= 2 and isinstance(argv[1], int) \
                    and not isinstance(argv[1], bool):
                state["game_price"] = int(argv[1])
                if int(TRAINING_PRICE) != GAME_PRICE:
                    argv[1] = max(0, int(TRAINING_PRICE))
            if len(argv) >= 1 and isinstance(argv[0], int) \
                    and not isinstance(argv[0], bool) \
                    and int(COURSE_YEARS) != GAME_COURSE_YEARS:
                argv[0] = max(1, int(COURSE_YEARS))
            if argv != list(args):
                write("start: the terms {} became {}".format(list(args), argv))
        except Exception:
            ctx.log_exc("training custom: cannot set the training terms")
            argv = list(args)
        return orig(self, app, *argv, **kwargs)

    def settle_price(app, before):
        """訓練の開始時点の帳尻。

        引数を差し替えているのでゲームは既に設定額を引いているはず。
        ここで見るのは**本当にその額だったか**だけ。
        素の代金のほうが引かれていたら（代金を別の場所で決めているビルド）、
        差額をその場で戻して WARN を残す。
        想定外の値になっていたら触らずに WARN（訓練の中で代金以外の出入りがあった場合を
        壊さないため）。
        """
        want = int(TRAINING_PRICE)
        if want == GAME_PRICE or before is None:
            return
        after = ui.gold_of(app)
        if after is None:
            write("WARN price: cannot re-read the gold; leaving it as is")
            return
        moved = before - after
        game = state["game_price"] if state["game_price"] is not None else GAME_PRICE
        if moved == want:
            write("price: charged {} in one deduction; gold {} -> {}".format(
                want, before, after))
        elif moved == 0:
            write("price: nothing was charged (gold {}); the training did not "
                  "start".format(before))
        elif moved == game:
            set_gold(app, before - want)
            write("WARN price: the game charged its own {} instead of {}; "
                  "gold {} -> {} (corrected)".format(
                      game, want, after, before - want))
        else:
            write("WARN price: the gold moved {} (ours is {}); leaving it as is "
                  "({} -> {})".format(moved, want, before, after))

    @ctx.wrap("__main__:{}.execute".format(START_CLS), required=False)
    def start_execute(orig, self, choice_text=None, *args, **kwargs):
        """代金の引き落としを前後の所持金で確かめ、続く修行内容のボタンを整える。"""
        app = getattr(self, "app", None) or ui.find_app()
        refresh_world(app)
        before = ui.gold_of(app)
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            try:
                settle_price(app, before)
            except Exception:
                ctx.log_exc("training custom: cannot settle the price")
            repaint(app, PHASE_CLS, relabel_phase)

    # ============================================================ 日数
    @ctx.wrap("__main__:{}.__init__".format(PHASE_CLS), required=False, safe=True)
    def phase_init(orig, self, app, *args, **kwargs):
        """`execute` には引数が来ないので、活動の種類と残り年数をここで控える。

        自分専用の属性名で持つだけで、ゲームのデータには何も書かない
        （マネージャはセーブに入らない。セーブに入るのは spec のほう）。
        """
        result = orig(self, app, *args, **kwargs)
        try:
            self._mod_training_custom = {
                "activity": args[0] if len(args) >= 1 else None,
                "remaining": args[1] if len(args) >= 2 else None,
            }
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:{}.execute".format(PHASE_CLS), required=False)
    def phase_execute(orig, self, choice_text=None, *args, **kwargs):
        """段の間だけ窓を開ける。日数の差し替えはこの窓の中だけ。"""
        app = getattr(self, "app", None) or ui.find_app()
        refresh_world(app)
        window = None
        try:
            info = getattr(self, "_mod_training_custom", None) or {}
            window = {"activity": info.get("activity"),
                      "remaining": info.get("remaining"),
                      # この段が始まった時刻。日数送りに複数の MOD が望みを出したとき、
                      # **先に始まった事情が決める**（ローダの `durations`）。
                      "since": time.time(),
                      "granted": []}
            write("phase: activity={!r} remaining={!r} choice={!r} "
                  "days_per_year={}".format(window["activity"], window["remaining"],
                                            choice_text, DAYS_PER_YEAR))
            state["phase"] = window
        except Exception:
            ctx.log_exc("training custom: cannot open the phase window")
            window = None
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["phase"] = None
            if window is not None and int(DAYS_PER_YEAR) != GAME_DAYS_PER_YEAR \
                    and not window["granted"]:
                write("WARN phase: no elapse_days call came through this phase; "
                      "the days were left as they are")
            repaint(app, PHASE_CLS, relabel_phase)

    # `elapse_days` はこちらでは包まない。包むのはローダの関所1枚だけで、
    # ここは「この段を何日にしたいか」を答える側に回る（TECH.md §3.3.3）。
    def days_wish(app, days):
        """段の窓の間だけ、設定の1年の長さで測り直した日数を望む。

        窓の外・素のままでは None。
        1段で何回来ても、そのつど**送られてきた数を測り直す**ので
        （固定の予算ではないので）合計がずれない。
        実測では1段につき1回（`231_probe_training`）。
        """
        window = state["phase"]
        if window is None or int(DAYS_PER_YEAR) == GAME_DAYS_PER_YEAR:
            return None
        return {"days": scaled_days(days), "since": window.get("since")}

    def days_note(app, days, granted):
        """実際に渡った日数を控える。**他所が決めた回も来る**。"""
        window = state["phase"]
        if window is not None:
            window["granted"].append(granted)

    durations.claim_days(owner, days_wish, note=days_note, write=write)
    durations.install(ctx, write)

    # ============================================================ 手持ちの確認
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """設定した代金に手持ちが満たないときは、押された時点で断る。

        ゲームのボタンは触らず押下だけ握る。画面はそのまま残るので選び直せる。
        読めなかったときは通す（値が読めないことを理由に遊びを止めない。`314_` と同じ）。
        """
        try:
            refresh_world(self)
            if int(TRAINING_PRICE) != GAME_PRICE:
                entry = ui.pressed_entry(self, button_index)
                if isinstance(entry, dict) \
                        and ui.spec_cls_name(entry) == START_CLS:
                    gold = ui.gold_of(self)
                    if gold is not None and gold < int(TRAINING_PRICE):
                        write("refused: price {} > gold {}".format(
                            int(TRAINING_PRICE), gold))
                        screen.say(self, fmt(REFUSE_TEXT,
                                             price=int(TRAINING_PRICE), gold=gold))
                        return None
        except Exception:
            ctx.log_exc("training custom: the price check failed")
        return orig(self, button_index, *args, **kwargs)

    # ------------------------------------------------------------ 自己検証
    # 実経路は訓練所で1回訓練するまで通らない。
    # ラベルの読み書きと日数の式だけは作ったデータで先に確かめておく（`314_` と同じ方針）。
    # 通貨の表記は `130_` が差し替えていることがあるので、
    # 見本のほうも同じ表記へ通してから突き合わせる。
    sample = fmt(START_BUTTON, name="訓練を受ける", price=500, years=3)
    expected = ui.rewrite_coins("訓練を受ける(500G)")
    survives = fmt("{name}と{typo}", name="訓練")
    labels = (plain_name("訓練を受ける(300G)"), plain_name("ただ鍛える(1年)"),
              plain_name("やった"))
    years = (phase_years("ただ鍛える(1年)"), phase_years("新たな技を学ぶ(3年)"),
             phase_years("やった"))
    # 日数の式。設定と無関係に確かめる（per_year= を明示で渡す）。
    rescaled = (scaled_days(1095, 30), scaled_days(365, 30), scaled_days(730, 30),
                scaled_days(1095, 365), scaled_days(365, 1))
    if sample == expected and survives == "訓練と{typo}" \
            and labels == ("訓練を受ける", "ただ鍛える", "やった") \
            and years == (1, 3, None) and rescaled == (90, 30, 60, 1095, 1):
        ctx.log("verified: reads the years from a label, formats templates, "
                "and rescales the days")
    else:
        ctx.log("VERIFY FAILED: sample={!r} survives={!r} labels={!r} years={!r} "
                "rescaled={!r}".format(sample, survives, labels, years, rescaled),
                level="ERROR")

    ctx.log("training custom: price={} course={}y year={}d log={}".format(
        TRAINING_PRICE, COURSE_YEARS, DAYS_PER_YEAR, log_path))
