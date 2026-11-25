# -*- coding: utf-8 -*-
"""HUD（`InstanTaleHUD`）を塗り直した後と、人物欄を開け閉めした後の処理を MOD どうしで分け合う窓口。TECH.md §3.3.21。

本文の塗り直し（`update_display_text`）・選択肢の塗り直し（`update_button_texts`）の後に自分の後始末を走らせるだけの
包みが 7本（112/113/114/116/117/122 と 118）、人物欄の開け閉め（`toggle_character_sheet_visibility`）の後に
飾りや照合を足す包みが 4本（121/317/335/346）、それぞれ同じ形で写されていた。
包むのはローダの1枚にし、MOD は前後の処理だけを渡す（`gates`）。

    hud.on(owner, ctx, (hud.TEXT, hud.BUTTONS), after=fn, write=write)    # 本文・選択肢を塗った後
    hud.on(owner, ctx, (hud.SHEET,), after=fn, write=write)               # 人物欄を開け閉めした後
    fn(call)    # call.obj（HUD）/ call.app / call.value / call.result / call.memo …（gates.Call）
    hud.settle_text(hud_widget)    # 次のフレームで本文の高さをゲーム自身の計算で決め直させる

| 種類 | 包む関数 | `call.value` |
|---|---|---|
| `TEXT` | `InstanTaleHUD.update_display_text(self, instance, value)` | 引数の `value`（本文。`orig` の前に読む） |
| `BUTTONS` | `InstanTaleHUD.update_button_texts(self, instance, value)` | 引数の `value`（選択肢の並び） |
| `SHEET` | `InstanTaleHUD.toggle_character_sheet_visibility` | None |

`call.obj` は HUD、`call.instance` は引数の `instance`（`SHEET` は None）、`call.app` は走っている app（`ui.find_app()`）、
`call.label` は包んだ関数の名前（ログ用）。

- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）
- 包みは、最初に登録した MOD の適用の時に立つ（一番外側には立てない）。
  `118_` は打ち切りの最中に `orig` を呼ばずに返す包みを自分で持ち、その内側の後始末も止めている。
  一番外側に立てると、内側だった後始末（`112_` の行間）が打ち切りの最中も1文字ごとに走る
- 本文の塗り直しは1文字ごとに来る。登録が無ければ引数も app も読まずに `orig` を呼ぶ

`settle_text` は `112_` / `113_` / `117_` が各自に写していた「次のフレームで `update_label_height` を1回呼ぶ」。
本文は1文字ずつ伸びるので、その場で呼ぶと1文字ごとに仕事が増える。同じ HUD への予約は、どの MOD からでも1つにまとめる。
`texture_update()` は呼ばない（呼ぶと1文字ごとにラベルを作り直す。TECH.md §6.2・GAME.md §2.3）。

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
置き場は `sys` の属性（種類ごとの名前と `_instantale_hud_settle`）。注入し直しをまたいで残る。
"""
import sys
import time

from . import frames, gates, log_exc, state

TEXT = "text"
BUTTONS = "buttons"
SHEET = "sheet"
KINDS = (TEXT, BUTTONS, SHEET)

TARGETS = {
    TEXT: "scripts.hud.new_hud:InstanTaleHUD.update_display_text",
    BUTTONS: "scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
    SHEET: "scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility",
}
LABELS = {TEXT: "update_display_text", BUTTONS: "update_button_texts",
          SHEET: "toggle_character_sheet_visibility"}

GATES = {kind: gates.Gate("hud." + kind, TARGETS[kind], "_instantale_hud_" + kind) for kind in KINDS}

#: 本文の高さの予約（`{id(HUD): 予約した時刻}`）。
SETTLE_ATTR = "_instantale_hud_settle"
#: 予約が走らないまま、これより古くなったら積み直す（秒）。Clock が予約を落とした回に、二度と決め直さなくなるのを避ける。
SETTLE_STALE_SECONDS = 1.0


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    if not kinds or any(kind not in GATES for kind in kinds):
        raise ValueError("hud: kinds must be among {}, got {!r}".format(KINDS, kinds))
    return kinds


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None):
    """前後の処理を登録し、包みを立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は（種類ごとに）差し替える。"""
    for kind in _kinds_of(kinds):
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write)
        install(ctx, kind, write)


def off(owner, kinds=KINDS):
    """登録を下げる。"""
    for kind in _kinds_of(kinds):
        GATES[kind].withdraw(owner)


def owners(kind):
    """生きている登録の持ち主（登録の古い順）。"""
    return GATES[_kinds_of(kind)[0]].owners()


def reset():
    """全部忘れる（検査用）。本文の高さの予約も忘れる。"""
    for gate in GATES.values():
        gate.reset()
    setattr(sys, SETTLE_ATTR, {})


def install(ctx, kind, write=None):
    """その種類の包みを立てる。1つの世代につき1枚。包んだ対象の名前を返す。"""
    kind = _kinds_of(kind)[0]
    gate = GATES[kind]
    label = LABELS[kind]
    reads_value = kind != SHEET

    def hud_method(orig, self, *args, **kwargs):
        # 本文は1文字ごとに来るので、登録が無ければ何も読まない。
        if not gate.entries():
            return orig(self, *args, **kwargs)
        from . import ui
        try:
            app = ui.find_app()
        except Exception:
            app = None
        instance = value = None
        if reads_value:
            instance = frames.arg(args, kwargs, "instance", 0)
            value = frames.arg(args, kwargs, "value", 1)
        call = gates.Call(kind, self, args, kwargs, app=app, label=label, instance=instance, value=value)
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return gate.install(ctx, hud_method, write)


def _pending():
    return state.process_store(SETTLE_ATTR, {})


def settle_text(hud, write=None):
    """次のフレームで、本文の高さをゲーム自身の計算（`hud.update_label_height()`）で決め直させる。

    行間・折り返し幅・本文を変えた後に呼ぶ。同じ HUD への予約がまだ走っていなければ積まない
    （どの MOD からの予約でも1つにまとめる）。予約したら True、積まなかったら False。
    Kivy が無い（ゲームの外の検査）ときはその場で呼ぶ（`ui.scheduler` と同じ）。
    `update_label_height` が投げた例外は握って記録する（`write` があればそこにも1行）。
    """
    if hud is None:
        return False
    key = id(hud)
    pending = _pending()
    now = time.monotonic()
    at = pending.get(key)
    if at is not None and now - at < SETTLE_STALE_SECONDS:
        return False
    pending[key] = now

    def run(_dt=None):
        _pending().pop(key, None)
        try:
            update_height = frames.attr(hud, "update_label_height", None)
            if callable(update_height):
                update_height()
        except Exception as exc:
            log_exc("hud: could not settle the text height")
            if callable(write):
                try:
                    write("WARN hud: update_label_height raised {}: {}".format(type(exc).__name__, exc))
                except Exception:
                    pass

    try:
        from kivy.clock import Clock
    except Exception:
        run()
        return True
    try:
        Clock.schedule_once(run, 0)
    except Exception:
        pending.pop(key, None)
        log_exc("hud: could not schedule the text height")
        return False
    return True
