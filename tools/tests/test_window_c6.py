# -*- coding: utf-8 -*-
"""ローダの窓口 `hud`（HUD を塗り直した後・人物欄を開け閉めした後）と `status_text`（能力値の欄の書き換え）を
ゲーム抜きで通す。

    python tools/tests/test_window_c6.py

hud:
  順       … before は登録の新しい順、after は古い順。value と instance は orig の前に読む（位置でもキーワードでも）
  種類     … TEXT / BUTTONS / SHEET は別々の包み。SHEET の value は None
  投げた回 … orig が投げたら always=True の after だけ来る。前後の処理の例外は握る
  素通し   … 登録が無ければ app も引数も読まない
  立つ位置 … 最初に登録した MOD の適用の時に立つ（適用の間も包む）。後から自分で包んだ MOD（118 の形）より内側
  高さ     … settle_text は同じ HUD への予約を1つにまとめ、走った後はまた積める。Kivy が無ければその場で呼ぶ
status_text:
  順       … order の小さい順、同じなら登録の新しい順。after はその逆
  差し替え … value を位置でもキーワードでも、来た側に合わせて差し替える。None はそのまま、例外は飛ばす
  保険     … ラベルが元の文字列のままなら直に入れる。値を取ったとき・別の文字列のときは触らない
  塗り直し … repaint は今の status_texts で見張りを呼び、書き換えを通す
"""
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))
from instantale_modloader import gates, hud, status_text, ui  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print("  {}  {}{}".format("ok  " if ok else "FAIL", name, "  " + str(detail) if not ok and detail else ""))
    if not ok:
        failures.append(name)


class Ctx(object):
    """後から当てた包みが外側になる（本物の wrap と同じ）。"""
    seq = 0

    def __init__(self):
        Ctx.seq += 1
        self.generation = Ctx.seq
        self.hooks = {}
        self.dead = False

    def superseded(self):
        return self.dead

    def wrap(self, target, **kw):
        def decorator(fn):
            previous = self.hooks.get(target)
            if previous is None:
                self.hooks[target] = fn
            else:
                self.hooks[target] = lambda orig, this, *a, _p=previous, _f=fn, **k: _f(
                    lambda obj, *a2, **k2: _p(orig, obj, *a2, **k2), this, *a, **k)
            return fn
        return decorator


class App(object):
    pass


class Label(object):
    def __init__(self, text=""):
        self.text = text


class Hud(object):
    def __init__(self):
        self.status_texts = ""
        self.status_label = Label()
        self.heights = 0

    def update_label_height(self):
        self.heights += 1


def fresh():
    gates.reset_all()
    hud.reset()
    status_text.reset()
    return Ctx()


TEXT_TARGET = hud.TARGETS[hud.TEXT]
BUTTONS_TARGET = hud.TARGETS[hud.BUTTONS]
SHEET_TARGET = hud.TARGETS[hud.SHEET]
APP = App()
real_find_app = ui.find_app
ui.find_app = lambda: APP

try:
    print("[hud: 順と値]")
    ctx = fresh()
    seen = []

    def make(owner):
        def before(call):
            seen.append(("before", owner, call.kind, call.value, call.instance, call.app is APP, call.label))
            return "memo-" + owner

        def after(call):
            seen.append(("after", owner, call.memo, call.result, type(call.error).__name__ if call.error else None))
        return before, after

    for name, always in (("113", True), ("114", False), ("116", False)):
        b, a = make(name)
        hud.on(name, ctx, (hud.TEXT, hud.BUTTONS), before=b, after=a, always=always)
    check("TEXT と BUTTONS を包み、SHEET は包まない",
          TEXT_TARGET in ctx.hooks and BUTTONS_TARGET in ctx.hooks and SHEET_TARGET not in ctx.hooks, sorted(ctx.hooks))
    check("登録の持ち主（古い順）", hud.owners(hud.TEXT) == ["113", "114", "116"], hud.owners(hud.TEXT))
    widget = Hud()

    def paint(self, instance, value):
        seen.append(("orig", value))
        return "painted"

    result = ctx.hooks[TEXT_TARGET](paint, widget, "inst", "本文")
    check("orig の戻り値を返す", result == "painted")
    check("before は新しい順で、value / instance / app を読む",
          [row[:5] for row in seen[:3]] == [("before", "116", hud.TEXT, "本文", "inst"),
                                            ("before", "114", hud.TEXT, "本文", "inst"),
                                            ("before", "113", hud.TEXT, "本文", "inst")], seen[:3])
    check("call.app と call.label", seen[0][5:] == (True, "update_display_text"), seen[0])
    check("orig は before の後", seen[3] == ("orig", "本文"), seen[3])
    check("after は古い順で、memo と戻り値が渡る",
          seen[4:] == [("after", "113", "memo-113", "painted", None), ("after", "114", "memo-114", "painted", None),
                       ("after", "116", "memo-116", "painted", None)], seen[4:])

    seen.clear()
    ctx.hooks[BUTTONS_TARGET](lambda self, instance=None, value=None: "ok", widget, instance="i", value=["a", "b"])
    check("キーワードで来た value も読む（BUTTONS）",
          seen[0][:5] == ("before", "116", hud.BUTTONS, ["a", "b"], "i") and seen[0][6] == "update_button_texts",
          seen[0])

    print("[hud: 投げた回と握る]")
    seen.clear()

    def broken(self, instance, value):
        raise RuntimeError("boom")

    try:
        ctx.hooks[TEXT_TARGET](broken, widget, None, "x")
    except RuntimeError:
        check("orig の例外はそのまま流す", True)
    else:
        check("orig の例外はそのまま流す", False)
    check("always=True の after だけ来る（call.error）",
          [row for row in seen if row[0] == "after"] == [("after", "113", "memo-113", None, "RuntimeError")], seen)

    ctx = fresh()
    seen = []
    warned = []

    def bad_after(call):
        raise ValueError("bad")

    hud.on("112", ctx, hud.TEXT, after=bad_after, write=warned.append)
    hud.on("122", ctx, hud.TEXT, after=lambda call: seen.append("122"))
    result = ctx.hooks[TEXT_TARGET](lambda self, i, v: seen.append("orig") or "ok", widget, None, "x")
    check("後の処理が投げても戻り値と残りは走る", result == "ok" and seen == ["orig", "122"], seen)
    warns = [line for line in warned if line.startswith("WARN")]
    check("持ち主の write に WARN を残す", len(warns) == 1 and warns[0].startswith("WARN hud.text"), warned)

    print("[hud: 用済みの登録と下げた登録]")
    seen.clear()
    dead = Ctx()
    dead.generation = ctx.generation
    dead.dead = True
    hud.on("old", dead, hud.TEXT, after=lambda call: seen.append("old"))
    hud.off("112")
    ctx.hooks[TEXT_TARGET](lambda self, i, v: "ok", widget, None, "x")
    check("用済みの ctx の登録と下げた登録は呼ばない", seen == ["122"], seen)
    check("持ち主は生きている登録だけ", hud.owners(hud.TEXT) == ["122"], hud.owners(hud.TEXT))

    print("[hud: SHEET]")
    ctx = fresh()
    seen = []
    hud.on("121", ctx, (hud.SHEET,), after=lambda call: seen.append(("121", call.value, call.instance, call.obj)))
    hud.on("317", ctx, (hud.SHEET,), after=lambda call: seen.append(("317", call.label)))
    check("SHEET だけを包む", sorted(ctx.hooks) == [SHEET_TARGET], sorted(ctx.hooks))
    ctx.hooks[SHEET_TARGET](lambda self, *a: "toggled", widget, 0)
    check("SHEET は value / instance を読まず、after は古い順",
          seen == [("121", None, None, widget), ("317", "toggle_character_sheet_visibility")], seen)

    print("[hud: 素通し]")
    ctx = fresh()
    hud.install(ctx, hud.TEXT)
    reads = []
    ui.find_app = lambda: reads.append("find_app") or APP
    result = ctx.hooks[TEXT_TARGET](lambda self, i, v: "plain", widget, None, "x")
    ui.find_app = lambda: APP
    check("登録が無ければ app を引かずに orig を呼ぶ", result == "plain" and reads == [], reads)
    check("同じ世代では2枚目を立てない", hud.install(ctx, hud.TEXT) == [TEXT_TARGET])

    print("[hud: 種類の誤り]")
    for bad in ((), ("text", "nope"), "nope"):
        try:
            hud.on("x", ctx, bad, after=lambda call: None)
        except ValueError:
            check("知らない種類は ValueError: {!r}".format(bad), True)
        else:
            check("知らない種類は ValueError: {!r}".format(bad), False)

    print("[hud: 立つ位置]")
    ctx = fresh()
    order = []
    gates.defer_outermost(True)
    try:
        hud.on("112", ctx, hud.TEXT, after=lambda call: order.append("112 の行間"))
        check("MOD の適用の間でも包む（一番外側には立てない）", TEXT_TARGET in ctx.hooks)

        # 後から適用した MOD が自分で包み、打ち切りの最中は orig を呼ばずに返す（118 の形）
        muted = {"on": False}

        @ctx.wrap(TEXT_TARGET)
        def batch(orig, self, *args, **kwargs):
            if muted["on"]:
                order.append("118 が止めた")
                return None
            return orig(self, *args, **kwargs)

        hud.on("122", ctx, hud.TEXT, after=lambda call: order.append("122 のボタン"))
    finally:
        gates.defer_outermost(False)
    ctx.hooks[TEXT_TARGET](lambda self, i, v: order.append("orig"), widget, None, "x")
    check("止めていないときは orig の後に古い順", order == ["orig", "112 の行間", "122 のボタン"], order)
    order.clear()
    muted["on"] = True
    ctx.hooks[TEXT_TARGET](lambda self, i, v: order.append("orig"), widget, None, "x")
    check("止めた回は窓口の後の処理も走らない（窓口は 118 の内側）", order == ["118 が止めた"], order)

    print("[hud.settle_text: Kivy が無い]")
    hud.reset()
    widget = Hud()
    check("Kivy が無ければその場で呼ぶ", hud.settle_text(widget) is True and widget.heights == 1, widget.heights)
    check("走った後はまた積める", hud.settle_text(widget) is True and widget.heights == 2, widget.heights)
    check("HUD が None なら何もしない", hud.settle_text(None) is False)

    class BadHud(object):
        def update_label_height(self):
            raise RuntimeError("no texture")

    warned = []
    check("update_label_height の例外は握る", hud.settle_text(BadHud(), write=warned.append) is True)
    check("→ write に WARN を残す", len(warned) == 1 and warned[0].startswith("WARN hud:"), warned)
    check("高さを決める手の無い HUD でも落ちない", hud.settle_text(object()) is True)

    print("[hud.settle_text: Kivy の Clock]")
    scheduled = []

    class Clock(object):
        fail = False

        @staticmethod
        def schedule_once(fn, delay):
            if Clock.fail:
                raise RuntimeError("no clock")
            scheduled.append((fn, delay))

    kivy_mod = types.ModuleType("kivy")
    clock_mod = types.ModuleType("kivy.clock")
    clock_mod.Clock = Clock
    kivy_mod.clock = clock_mod
    saved = {name: sys.modules.get(name) for name in ("kivy", "kivy.clock")}
    sys.modules["kivy"] = kivy_mod
    sys.modules["kivy.clock"] = clock_mod
    try:
        hud.reset()
        widget = Hud()
        other = Hud()
        first = hud.settle_text(widget)
        second = hud.settle_text(widget)
        third = hud.settle_text(other)
        check("同じ HUD への2回目は積まない", (first, second) == (True, False) and len(scheduled) == 2, scheduled)
        check("別の HUD は別に積む", third is True)
        check("次のフレーム（delay 0）に積む", all(delay == 0 for _fn, delay in scheduled), scheduled)
        check("走るまでは高さを決めない", widget.heights == 0)
        scheduled[0][0](0.016)
        check("走ると1回だけ決め直す", widget.heights == 1, widget.heights)
        check("走った後はまた積める", hud.settle_text(widget) is True and len(scheduled) == 3, scheduled)
        # 予約が落ちて走らないまま古くなったら積み直す
        hud._pending()[id(widget)] = time.monotonic() - hud.SETTLE_STALE_SECONDS - 0.5
        check("古い予約は積み直す", hud.settle_text(widget) is True and len(scheduled) == 4, len(scheduled))
        Clock.fail = True
        failing = Hud()
        check("積めなかったら False", hud.settle_text(failing) is False)
        Clock.fail = False
        check("→ 予約の印は残さない（次は積める）", hud.settle_text(failing) is True)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        hud.reset()

    # ---------------------------------------------------------------- status_text
    STATUS = "Atk:10(+5)\nGold:100\nLocation:町"

    print("[status_text: 順]")
    ctx = fresh()
    seen = []

    def adder(owner, mark):
        def fn(text, app):
            seen.append((owner, text, app is APP))
            return text + mark
        return fn

    def after_of(owner):
        def after(call):
            seen.append(("after", owner, call.value, call.text, call.result, call.status_label is call.obj.status_label,
                         call.label))
        return after

    status_text.on("130", ctx, adder("130", "[通貨]"), after=after_of("130"))
    status_text.on("333", ctx, adder("333", "[装備]"))
    status_text.on("345", ctx, adder("345", "[日付]"), after=after_of("345"))
    check("包みを立てる", status_text.TARGET in ctx.hooks)
    check("持ち主は書き換えの順（同じ order なら新しい順）", status_text.owners() == ["345", "333", "130"],
          status_text.owners())
    widget = Hud()
    widget.status_texts = STATUS
    painted = []

    def paint_value(self, instance, value):
        painted.append(value)
        self.status_label.text = value
        return "done"

    result = ctx.hooks[status_text.TARGET](paint_value, widget, widget, STATUS)
    final = STATUS + "[日付][装備][通貨]"
    check("orig の戻り値を返す", result == "done")
    check("新しい順に通し、次の MOD は前の答えを見る",
          seen[:3] == [("345", STATUS, True), ("333", STATUS + "[日付]", True),
                       ("130", STATUS + "[日付][装備]", True)], seen[:3])
    check("見張りには全部を通した文字列が渡る", painted == [final], painted)
    check("after は逆の順で、元の文字列・渡した文字列・戻り値・ラベルが渡る",
          seen[3:] == [("after", "130", STATUS, final, "done", True, "update_status_texts"),
                       ("after", "345", STATUS, final, "done", True, "update_status_texts")], seen[3:])

    print("[status_text: order]")
    seen.clear()
    painted.clear()
    status_text.on("130", ctx, adder("130", "[通貨]"), order=-1)
    check("order の小さい方が先", status_text.owners() == ["130", "345", "333"], status_text.owners())
    ctx.hooks[status_text.TARGET](paint_value, widget, widget, value=STATUS)
    check("キーワードで来た value も差し替える", painted == [STATUS + "[通貨][日付][装備]"], painted)

    print("[status_text: None と例外]")
    ctx = fresh()
    painted = []
    warned = []

    def boom(text, app):
        raise ValueError("bad")

    status_text.on("130", ctx, lambda text, app: None)
    status_text.on("333", ctx, boom, write=warned.append)
    status_text.on("345", ctx, lambda text, app: text.upper() if isinstance(text, str) else None)
    ctx.hooks[status_text.TARGET](paint_value, widget, widget, "abc")
    check("None はそのまま、例外の MOD は飛ばして続ける", painted == ["ABC"], painted)
    check("持ち主の write に WARN を残す", len(warned) == 1 and warned[0].startswith("WARN status_text"), warned)
    painted.clear()
    ctx.hooks[status_text.TARGET](paint_value, widget, widget, None)
    check("文字列でない value もそのまま渡る", painted == [None], painted)

    print("[status_text: ラベルへの保険]")
    ctx = fresh()
    status_text.on("345", ctx, lambda text, app: text + "\nDate:1年")
    widget = Hud()
    widget.status_texts = STATUS

    def paint_own(self, instance, value):
        # 渡された value を使わず、自分の status_texts を塗るビルド
        self.status_label.text = self.status_texts

    ctx.hooks[status_text.TARGET](paint_own, widget, widget, STATUS)
    check("ラベルが元の文字列のままなら直に入れる", widget.status_label.text == STATUS + "\nDate:1年",
          widget.status_label.text)

    def paint_other(self, instance, value):
        self.status_label.text = "別の形:" + value

    ctx.hooks[status_text.TARGET](paint_other, widget, widget, STATUS)
    check("別の文字列なら触らない", widget.status_label.text == "別の形:" + STATUS + "\nDate:1年",
          widget.status_label.text)

    class NoLabel(object):
        pass

    got = []
    status_text.on("130", ctx, after=lambda call: got.append(call.status_label))
    ctx.hooks[status_text.TARGET](lambda self, i, v: None, NoLabel(), None, STATUS)
    check("ラベルが無くても落ちず、status_label は None", got == [None], got)

    print("[status_text: 用済みの登録・下げた登録・素通し]")
    ctx = fresh()
    painted = []
    dead = Ctx()
    dead.generation = ctx.generation
    dead.dead = True
    status_text.on("333", ctx, lambda text, app: "333")
    status_text.on("old", dead, lambda text, app: "old")
    status_text.off("333")
    reads = []
    ui.find_app = lambda: reads.append("find_app") or APP
    ctx.hooks[status_text.TARGET](paint_value, Hud(), None, "raw")
    ui.find_app = lambda: APP
    check("生きている登録が無ければ app を引かずに素通し", painted == ["raw"] and reads == [], (painted, reads))
    check("同じ世代では2枚目を立てない", status_text.install(ctx) is False)
    gates.reset_all()
    check("gates.reset_all の後は立て直す", status_text.install(ctx) is True)

    print("[status_text: 登録の誤り]")
    for kwargs, error in (({}, ValueError), ({"fn": "not callable"}, TypeError),
                          ({"fn": lambda t, a: None, "order": "1"}, TypeError),
                          ({"fn": lambda t, a: None, "order": True}, TypeError)):
        try:
            status_text.on("x", ctx, **kwargs)
        except error:
            check("{} を投げる: {!r}".format(error.__name__, sorted(kwargs)), True)
        else:
            check("{} を投げる: {!r}".format(error.__name__, sorted(kwargs)), False)

    print("[status_text.repaint]")
    ctx = fresh()
    status_text.on("345", ctx, lambda text, app: text + "\nDate:2年")
    hook = ctx.hooks[status_text.TARGET]
    painted = []

    class InstanTaleHUD(Hud):
        def update_status_texts(self, instance, value):
            return hook(paint_value, self, instance, value)

    hud_module = types.ModuleType(ui.HUD_MODULE)
    setattr(hud_module, ui.HUD_CLASS, InstanTaleHUD)
    saved_hud = sys.modules.get(ui.HUD_MODULE)
    sys.modules[ui.HUD_MODULE] = hud_module
    try:
        app = App()
        app.root = InstanTaleHUD()
        app.root.status_texts = STATUS
        check("見張りを今の文字列で呼ぶ", status_text.repaint(app) is True)
        check("→ 書き換えを通して塗る", painted == [STATUS + "\nDate:2年"] and
              app.root.status_label.text == STATUS + "\nDate:2年", painted)
        app.root.status_texts = None
        check("文字列が無ければ呼ばない", status_text.repaint(app) is False and len(painted) == 1)
        check("app が None なら呼ばない", status_text.repaint(None) is False)

        class Broken(InstanTaleHUD):
            def update_status_texts(self, instance, value):
                raise RuntimeError("watcher")

        app.root = Broken()
        app.root.status_texts = STATUS
        check("見張りの例外は握って False", status_text.repaint(app) is False)
    finally:
        if saved_hud is None:
            sys.modules.pop(ui.HUD_MODULE, None)
        else:
            sys.modules[ui.HUD_MODULE] = saved_hud
finally:
    ui.find_app = real_find_app
    gates.reset_all()
    hud.reset()
    status_text.reset()

print()
if failures:
    print("FAILED: {} check(s)".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all checks passed")
