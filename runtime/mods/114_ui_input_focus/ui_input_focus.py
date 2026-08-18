# -*- coding: utf-8 -*-
"""追加: 自由入力の欄を、送信したあともそのまま打てる状態に保つ。

## 何が困っているか

自由入力を1回送るたびに、入力欄からフォーカスが外れる。
次の一言を打つには毎回そこをクリックし直すことになり、会話が続くほど手数が増える。

外れる理由は Kivy の作りそのもので、ゲームの不具合ではない:

* 送信ボタンを押す＝別のウィジェットに触る。
  `TextInput` は自分の外を触られると
  `focus = False` になる（`FocusBehavior` の既定）
* Enter で送る経路でも、ゲームが送信処理の中で欄を空にしたり、
  待機表示のために入力を塞いだり（`hud.text_send_button.disabled = True`、
  GAME.md §2.4）する間にフォーカスが落ちる

どちらも「フォーカスが外れる」という同じ結果になるので、外れたことを見て戻す。
送信の経路を1つずつ捕まえに行かない。
経路はビルドで変わりうるが、
「入力欄が今フォーカスを持っているか」はどのビルドでも同じ場所に出ている。

## 入力欄の見つけ方（属性名でも型名でも決めつけない）

GAME.md §1.3 の方針どおり、持っている物で見分ける。
`focus` と `insert_text` の両方を持ち、
`text` が文字列なら Kivy の `TextInput`（選択肢のボタンは
`insert_text` を持たない）。
HUD の属性を先に見て、無ければウィジェット木を降りる。

候補が複数あるビルド（名前入力の窓など）では、**送信ボタンと同じ親に居るもの**を自由入力の欄とみなす。
それも決まらなければ幅がいちばん広いものを採る。
選んだ相手は `out/input_focus.log` に1回だけ書く。
ここが唯一の資料になる。

## 戻してよいときだけ戻す

無条件に戻すと、ゲームが意図して入力を塞いでいる間もこちらが割り込むことになる。
次のどれかが立っているなら何もしない:

| 見るもの | 意味 |
|---|---|
| `hud.text_send_button.disabled` | 待機表示の最中（LLM の応答待ち）。GAME.md §2.4 |
| `app.text_input_disabled` / 欄自身の `disabled` | ゲームが入力を塞いでいる |
| `app.is_popup_window_opened` | 別の窓が開いている |
| 他の入力欄が `focus` を持っている | そちらに打っている最中。奪わない |

待機が明けたときは、こちらから戻しに行く（`REFOCUS_AFTER_SEND`）。
送信ボタンの `disabled` を監視して、False へ戻った瞬間に1回だけ焦点を入れる。
これが「送ったあともそのまま次を打てる」の本体で、応答を待つ間は塞がれたままになる。

## 取り合いにならないようにする（歯止め）

ゲーム側が「外す」を毎フレーム行うビルドがあれば、
こちらの「戻す」と無限に取り合いになる。
画面が固まったように見えるので、回数で頭打ちにする: `GUARD_WINDOW` 秒の間に `GUARD_LIMIT` 回を超えて戻そうとしたら、
`GUARD_PAUSE` 秒手を引く（警告を1回出す）。
手を引いている間はゲームの振る舞いがそのまま出るので、
最悪でも「今までどおり毎回クリックする」に戻るだけで済む。

## 触らないもの

入力欄の中身（`text`）、送信の経路、`app.buttons`、セーブ。
このMODが変えるのは **どのウィジェットが焦点を持っているか**だけ。
剥がす（`--unload`）とフックは外れ、束ねた監視も次の注入で付け替わる。
"""

import time
import weakref

from instantale_modloader import frames, ui

LOG_BASENAME = "input_focus.log"

# 焦点が外れたら戻すか。
# 既定 ON ＝ 送信でも、画面のどこかを触ったときでも戻る。
# OFF にすると「送信の直後だけ」戻る（下の設定と合わせて使う）。
REFOCUS_ON_BLUR = True

# 待機（応答待ち）が明けたときに戻すか。
# 既定 ON。
REFOCUS_AFTER_SEND = True

# 戻すまでの待ち（秒）。
# 0 でも次のフレームまでは待つ。
# ゲーム側が「外す」処理を終える前に入れ直すと、
# そのあとの外しに巻き込まれてしまうので少しだけ置く。
REFOCUS_DELAY = 0.05

# 取り合いの歯止め。
# この秒数の間にこの回数を超えたら、しばらく手を引く。
GUARD_WINDOW = 2.0
GUARD_LIMIT = 12
GUARD_PAUSE = 5.0

# ウィジェット木を何段まで降りて入力欄を探すか。
# 入力欄は画面下の帯の中に居る（`113_ui_text_expand` が記録した並びで、
# 本文の枠と同じ深さ）。
MAX_DEPTH = 10

# 記録の上限。
# 焦点は何度も出入りするので、
# 書くのは「見つけたとき」と「手を引いたとき」だけに絞る。
MAX_LOG = 40

# 束ねた監視の控え。
# ウィジェット自身に持たせる。
# 注入し直したときに、
# 古い版の監視が残ったまま二重に走るのを防ぐ（`113_` のボタンと同じ作り）。
FOCUS_ATTR = "_instantale_focus_on_focus"
VALIDATE_ATTR = "_instantale_focus_on_validate"
SEND_ATTR = "_instantale_focus_on_send"


def apply(ctx):
    state = {"logged": 0, "attempts": [], "standdown": 0.0, "chosen": None}
    warned = set()
    inputs = weakref.WeakKeyDictionary()      # hud -> 入力欄への弱参照

    write = ctx.logger(LOG_BASENAME)

    def note(text):
        if state["logged"] < MAX_LOG:
            state["logged"] += 1
            write(text)

    def warn_once(key, message):
        if key in warned:
            return
        warned.add(key)
        ctx.log("input focus: " + message, level="WARN")

    def guarded(fn):
        """監視から呼ばれる処理。ここで投げるとゲームを巻き込む。"""
        try:
            return fn()
        except Exception:
            ctx.log_exc("input focus: refocus failed")
            return None

    def schedule(fn, delay=0.0):
        try:
            from kivy.clock import Clock
        except Exception:
            fn()              # ゲームの外（オフライン検証）ではその場で
            return
        try:
            Clock.schedule_once(lambda _dt: fn(), delay)
        except Exception:
            ctx.log_exc("input focus: could not schedule the refocus")

    # -- 入力欄を探す --------------------------------------------------------
    def is_input(widget):
        """文字を打ち込める欄か。型では見ない（GAME.md §1.3）。

        `focus` と `insert_text` の両方を持つのは Kivy では `TextInput` だけ。
        選択肢のボタンも `text` は持つが `insert_text` は持たない。
        """
        for name in ("focus", "insert_text", "text"):
            if frames.attr(widget, name) is frames.MISSING:
                return False
        return isinstance(frames.attr(widget, "text"), str)

    def descend(widget, depth, found):
        if depth and is_input(widget):
            found.append(widget)
        if depth >= MAX_DEPTH:
            return
        children = frames.attr(widget, "children")
        if not isinstance(children, (list, tuple)):
            return
        for child in reversed(list(children)):
            descend(child, depth + 1, found)

    def all_inputs(hud):
        """画面に出ている入力欄すべて。「他へ打っている最中か」を見るのにも使う。"""
        found = []
        try:
            descend(hud, 0, found)
        except Exception:
            ctx.log_exc("input focus: walking the widget tree failed")
        # 属性で持たれているだけで木に出ない相手も拾う（ビルド差の受け皿）。
        try:
            items = list(vars(hud).values())
        except Exception:
            items = []
        for value in items:
            if is_input(value) and not any(value is seen for seen in found):
                found.append(value)
        return found

    def send_button(hud):
        """自由入力の送信ボタン（GAME.md §2.4 の `hud.text_send_button`）。"""
        button = frames.attr(hud, "text_send_button", None)
        if button is None or frames.attr(button, "disabled") is frames.MISSING:
            return None
        return button

    def width_of(widget):
        try:
            return float(frames.attr(widget, "width", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def pick(hud, found):
        """候補から自由入力の欄を1つ選ぶ。

        名前入力の窓のように欄が複数あるビルドでは、
        送信ボタンと同じ親に居るものが自由入力の欄。
        決まらなければ幅がいちばん広いものを採る（自由入力の欄は画面下の帯の幅いっぱいに置かれている）。
        """
        if len(found) == 1:
            return found[0]
        button = send_button(hud)
        parent = frames.attr(button, "parent", None) if button is not None else None
        if parent is not None:
            for widget in found:
                if frames.attr(widget, "parent", None) is parent:
                    return widget
        return max(found, key=width_of)

    def input_of(hud):
        """自由入力の欄。見つからなければ None。"""
        cached = inputs.get(hud)
        widget = cached() if cached is not None else None
        if widget is not None and frames.attr(widget, "parent") not in (None, frames.MISSING):
            return widget

        found = all_inputs(hud)
        if not found:
            warn_once("input", "no text input on the HUD; leaving focus alone")
            return None
        widget = pick(hud, found)
        try:
            inputs[hud] = weakref.ref(widget)
        except TypeError:
            pass          # 弱参照を持てない相手なら毎回探し直す（動作は同じ）
        where = (type(widget).__name__, round(width_of(widget)), len(found))
        if state["chosen"] != where:
            state["chosen"] = where
            note("input {} width={} (of {} candidate(s) on the HUD)".format(*where))
        return widget

    # -- 戻してよいか --------------------------------------------------------
    def blocked(hud, widget):
        """戻してはいけない理由。空なら戻してよい。"""
        reasons = []
        if frames.attr(widget, "disabled", False) is True:
            reasons.append("input disabled")
        button = send_button(hud)
        if button is not None and frames.attr(button, "disabled", False) is True:
            reasons.append("send disabled")      # 待機表示の最中（応答待ち）
        app = ui.find_app()
        if app is not None:
            if frames.attr(app, "text_input_disabled", False) is True:
                reasons.append("text_input_disabled")
            if frames.attr(app, "is_popup_window_opened", False) is True:
                reasons.append("popup open")
        # 他の欄に打っている最中なら奪わない。
        for other in all_inputs(hud):
            if other is not widget and frames.attr(other, "focus", False) is True:
                reasons.append("another input has focus")
                break
        return reasons

    def standing_down(now):
        """取り合いの歯止め。手を引いている最中か。"""
        return now < state["standdown"]

    def too_often(now):
        """短い間に戻しすぎていないか（ゲーム側と取り合いになっている合図）。"""
        attempts = [stamp for stamp in state["attempts"] if now - stamp < GUARD_WINDOW]
        attempts.append(now)
        state["attempts"] = attempts
        if len(attempts) <= GUARD_LIMIT:
            return False
        state["standdown"] = now + GUARD_PAUSE
        state["attempts"] = []
        note("stood down for {}s ({} refocus attempts in {}s)".format(
            GUARD_PAUSE, len(attempts), GUARD_WINDOW))
        warn_once("fight", "the game keeps taking focus away; standing down "
                           "(the input box will behave as it did without this mod)")
        return True

    def refocus(hud, why):
        """今この場で焦点を入れ直す。入れられない場面なら何もしない。"""
        widget = input_of(hud)
        if widget is None:
            return False
        if frames.attr(widget, "focus", False) is True:
            return False          # もう持っている
        now = time.monotonic()
        if standing_down(now):
            return False
        reasons = blocked(hud, widget)
        if reasons:
            return False
        if too_often(now):
            return False
        try:
            widget.focus = True
        except Exception:
            ctx.log_exc("input focus: could not focus the input box")
            return False
        note("refocused after {}".format(why))
        return True

    def request(hud, why):
        """次のフレーム（＋わずかな待ち）で戻す。

        その場で入れ直さないのは、ゲーム側の「外す」処理がまだ途中のことがあるため。
        Kivy の焦点は触った側の後始末で最後にもう一度動く。
        """
        schedule(lambda: guarded(lambda: refocus(hud, why)), REFOCUS_DELAY)

    # -- 監視を束ねる --------------------------------------------------------
    def rebind(widget, event, attr, callback):
        """`widget` の1つの合図に、今の注入の手を1本だけ結ぶ。

        注入し直すと `apply()` がもう一度走る（TECH.md §3.6）。
        前の版の手を外してから結ばないと、古い注入の処理が重なったまま走り続ける。
        """
        previous = frames.attr(widget, attr, None)
        if previous is not None:
            try:
                widget.unbind(**{event: previous})
            except Exception:
                pass          # 外せない相手でも、下の結び直しで動きはする
        try:
            widget.bind(**{event: callback})
            setattr(widget, attr, callback)
        except Exception:
            ctx.log_exc("input focus: could not watch " + event)

    def bind_input(hud, widget):
        def on_focus(_instance=None, value=True, *_args):
            # 外れたときだけ。
            # 入ったときは何もしない。
            if value or not REFOCUS_ON_BLUR:
                return
            request(hud, "blur")

        def on_validate(*_args):
            # Enter で送った経路。
            # 送信ボタンを経由しないビルド用。
            request(hud, "enter")

        rebind(widget, "focus", FOCUS_ATTR, on_focus)
        rebind(widget, "on_text_validate", VALIDATE_ATTR, on_validate)

    def bind_send(hud):
        button = send_button(hud)
        if button is None:
            return

        def on_disabled(_instance=None, value=True, *_args):
            # 塞がれた → 解かれた、が「応答が返ってきた」の合図（GAME.md §2.4）。
            if value or not REFOCUS_AFTER_SEND:
                return
            request(hud, "send finished")

        rebind(button, "disabled", SEND_ATTR, on_disabled)

    def upkeep(hud):
        """塗り直しのたびに、監視が今の注入に結ばれていることを保つ。

        画面が組み直されて入力欄が別のウィジェットに変わっても、ここで結び直る。
        """
        widget = input_of(hud)
        if widget is None:
            return
        bind_input(hud, widget)
        bind_send(hud)

    # -- フック --------------------------------------------------------------
    # 本文が変わったとき（送信の直後に必ず来る）と、選択肢が塗り直されたとき。
    # どちらもゲームに先に仕事をさせてから、こちらの後始末をする。
    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_display_text", safe=True)
    def update_display_text(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        upkeep(self)
        return result

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts", safe=True)
    def update_button_texts(orig, self, instance=None, value=None, *args, **kwargs):
        result = orig(self, instance, value, *args, **kwargs)
        upkeep(self)
        return result

    ctx.log("input focus: keeping the free-input box focused "
            "(on blur={}, after send={}, delay={}s); "
            "what it found goes to out/{}".format(
                REFOCUS_ON_BLUR, REFOCUS_AFTER_SEND, REFOCUS_DELAY, LOG_BASENAME))
