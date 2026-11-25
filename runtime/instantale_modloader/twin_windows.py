# -*- coding: utf-8 -*-
"""持ち物を2列で並べる画面（売買・受け渡し・保管庫）を開く・品を動かす・閉じる場面の前後を MOD どうしで分け合う窓口。TECH.md §3.3.21。

2列の画面は店の売買のほかに、MOD が同じ関数で開く受け渡し（402）・保管庫（330）・盗品の買取（336）がある。
開く前に持ち物を整える MOD（134/312/333/409）、品が動いたら控えを揃える MOD（330/333/402）、
閉じたら後始末する MOD（107/330/336/402/405）が、それぞれ同じ関数を自分で包んでいた。
閉じる経路は2つあり（ボタンの `close_shopping_window_process` と、外側を押したときの
`turnoff_window_visibility`。GAME.md §2.13.1.3）、外側を押した方は次のフレームで画面が消えたかを見る、
という同じ手順を 330/336/402 が別々に写していた。
包むのはローダの1枚にし、MOD は前後の処理だけを渡す（`gates`）。

    twin_windows.on(owner, ctx, (twin_windows.OPEN,), before=fn, after=fn, write=write)
    twin_windows.on(owner, ctx, (twin_windows.MOVE,), before=fn, after=fn, write=write)
    twin_windows.on(owner, ctx, (twin_windows.HIDDEN,), after=fn, write=write)
    fn(call)    # call.app / call.obj / call.result / call.memo …（gates.Call）

| 種類 | 包む関数 | `call.obj` | `call.app` | 足す属性（どれも `orig` の前に読む。読めなければ None） |
|---|---|---|---|---|
| `OPEN` | `InstantaleApp.toggle_twin_inventory_window` | app | app | `left` / `right`（左右の持ち主）・`label_text`（左の見出し）・`situation`（場面名。店は `"shop"`） |
| `MOVE` | `InventoryItem.change_inventory` | 品の部品（`InventoryItem`） | `ui.find_app()` | `widget`（= `call.obj`）・`item`（部品の中の品）・`old_inventory`（動かす前の `self.inventory`）・`new_inventory`（引数の `new_inventory`） |
| `CLOSE` | `InstantaleApp.close_shopping_window_process` | app | app | |
| `BACKDROP` | `InstanTaleHUD.turnoff_window_visibility` | HUD | `ui.find_app()` | `target`（2つ目の引数。消す画面の表示の辞書）・`twin`（`target` が2列の画面の辞書か） |
| `HIDDEN` | （`BACKDROP` の包みが次のフレームで呼ぶ） | HUD | `ui.find_app()` | `BACKDROP` と同じ |

`call.label` は包んだ関数の名前。ログ用。

- `OPEN` の引数は位置でもキーワードでも来る（店を開くゲームの lambda はキーワードで渡す。VERIFICATION.md §3.76）。
  読むのは `frames.arg` で、渡し方はそのまま `orig` へ渡す
- `MOVE` の `old_inventory` は `orig` の前でないと読めない（`orig` が `self.inventory` を移動先へ書き換える）。
  品の受け渡しを揃える MOD は `after` で `call.old_inventory` と `call.new_inventory` を比べる
- `HIDDEN` は外側を押して閉じたことの合図。`BACKDROP` の `orig` が返った次のフレームで、2列の画面
  （`hud.visible_twin_inventory_data['TorF']`）が消えていれば1回来る。消してすぐは表示の辞書がまだ
  書き換わっていないことがあるので次のフレームで見る。外側を押すと `close_shopping_window_process` は通らず、
  HUD の後始末（`on_backdrop_callback`）は HUD が作られたときの `on_close_window` を握るので、注入より前に作られた HUD
  ではその包みも通らない。閉じたことを拾えるのはこの経路だけ（GAME.md §2.13.1.3）。
  消したのが別の画面でも、2列の画面が見えていなければ来る（`call.twin` で見分けられる）。app が引けなければ来ない。
  `before` / `after` のどちらで受けてもよいが、`orig` が無いので `after` で受ける
- 閉じたことを1回にまとめる形にはしていない。ボタンで閉じたときに `orig` の前で後始末する MOD（330）と
  後でする MOD（107/402/405）があり、まとめると時機が変わる。両方の経路を拾う MOD は `CLOSE` と `HIDDEN` の両方に登録する
- 前後の処理は引数も戻り値も変えない。`always=True` の `after` は `orig` が投げても来る（`call.error`）
- `before` の戻り値は、同じ持ち主の `after` に `call.memo` で渡る（種類ごとに別）
- 呼ぶ順は、各 MOD が自分で包んでいたときの重なりと同じ（`before` は登録の新しい順、`after` は古い順。`gates`）。
  `HIDDEN` は `after` の順（古い順）で、各 MOD が自分で次のフレームへ積んでいたときの順と同じ

関所は外側に立てない（`gates.defer_outermost` の対象にしない）。最初に登録した MOD の適用の位置に立つ。
`OPEN` は 405 が自分で包むまま残る（下）。405 は適用順で 134/312/333/409 より先なので、405 の包みは関所より内側で、
405 が `orig` を握った回も、関所の前後の処理は今と同じ時機に走る（`after` は `orig` を握って返った None を見る）。
外側に立てると、計測 MOD（227/245）の直の包みより外側になり、計測が見る値が変わる。

寄せない形（自分で包むまま）:

- `OPEN` の `orig` を握り、検品が済んでから同じ引数で `orig` を呼び直す（405）。戻り値を変え、`orig` の時機を動かすので前後の処理に分けられない
- 2列の画面と違う関数（`InstanTaleHUD.toggle_twin_inventory_visibility`（333）・`InstantaleApp.on_close_window`（336））

切った MOD の登録は、登録した ctx が用済みになった時点で呼ばれなくなる（`gates.alive`）。
"""
from . import frames, gates, log_exc

OPEN = "open"
MOVE = "move"
CLOSE = "close"
BACKDROP = "backdrop"
HIDDEN = "hidden"
KINDS = (OPEN, MOVE, CLOSE, BACKDROP, HIDDEN)

TARGETS = {
    OPEN: "__main__:InstantaleApp.toggle_twin_inventory_window",
    MOVE: "scripts.hud.new_hud:InventoryItem.change_inventory",
    CLOSE: "__main__:InstantaleApp.close_shopping_window_process",
    BACKDROP: "scripts.hud.new_hud:InstanTaleHUD.turnoff_window_visibility",
    # 自分では包まない。`BACKDROP` の包みが次のフレームで呼ぶ。
    HIDDEN: "scripts.hud.new_hud:InstanTaleHUD.turnoff_window_visibility",
}
LABELS = {
    OPEN: "toggle_twin_inventory_window",
    MOVE: "change_inventory",
    CLOSE: "close_shopping_window_process",
    BACKDROP: "turnoff_window_visibility",
    HIDDEN: "turnoff_window_visibility",
}

#: `toggle_twin_inventory_window(left_inventory_obtainer, right_inventory_obtainer, left_label_text, situation)`
#: の引数名の並び（`frames.arg` の添字）。GAME.md §2.13.1。
OPEN_ARGS = ("left_inventory_obtainer", "right_inventory_obtainer", "left_label_text", "situation")
#: `change_inventory(new_inventory)` の引数名の並び。
MOVE_ARGS = ("new_inventory",)
#: 2列の画面の表示の辞書（HUD の属性）。`{'TorF': True, 'situation': 'shop', …}`。GAME.md §2.13.1.3。
TWIN_DATA_ATTR = "visible_twin_inventory_data"

GATES = {kind: gates.Gate("twin_windows." + kind, TARGETS[kind], "_instantale_twin_windows_" + kind)
         for kind in KINDS}
#: 自分の包みを持つ種類。`HIDDEN` は `BACKDROP` の包みに乗る。
_WRAPPED_BY = {OPEN: OPEN, MOVE: MOVE, CLOSE: CLOSE, BACKDROP: BACKDROP, HIDDEN: BACKDROP}


def _kinds_of(kinds):
    if isinstance(kinds, str):
        kinds = (kinds,)
    kinds = tuple(kinds or ())
    unknown = [kind for kind in kinds if kind not in GATES]
    if not kinds or unknown:
        raise ValueError("twin_windows: kinds must be among {}, got {!r}".format(KINDS, kinds))
    return kinds


def on(owner, ctx, kinds, before=None, after=None, always=False, write=None):
    """前後の処理を登録し、関所を立てる。`apply()` の中で呼ぶ。同じ持ち主の登録は差し替える。

    種類ごとに別の登録になる。種類で処理を変えるなら、種類ごとに呼ぶ。
    """
    for kind in _kinds_of(kinds):
        GATES[kind].on(owner, ctx, before=before, after=after, always=always, write=write)
        install(ctx, write, (kind,))


def off(owner, kinds=KINDS):
    """登録を下げる。"""
    for kind in _kinds_of(kinds):
        GATES[kind].withdraw(owner)


def owners(kind):
    """生きている登録の持ち主（登録の古い順）。"""
    return GATES[_kinds_of(kind)[0]].owners()


def reset():
    """全部忘れる（検査用）。"""
    for gate in GATES.values():
        gate.reset()


def is_open(app, popup=False):
    """2列の画面が見えているか（`hud.visible_twin_inventory_data['TorF']`）。

    `popup=True` なら、ゲームの開いている印（`app.is_popup_window_opened`）が立っているときも True。
    印は HUD の後始末（`on_close_window`）が下ろすので、画面が消えても後始末の前は立っている（GAME.md §2.13.1.3）。
    """
    if popup:
        flag = frames.attr(app, "is_popup_window_opened", False)
        if flag and not isinstance(flag, str):      # 番人の文字列は立っているとみなさない
            return True
    return _twin_visible(_hud_of(app))


def _hud_of(app):
    if app is None:
        return None
    try:
        from . import ui
        return ui.find_hud(app)
    except Exception:
        return None


def _twin_data(hud):
    data = frames.attr(hud, TWIN_DATA_ATTR, None) if hud is not None else None
    return data if isinstance(data, dict) else None


def _twin_visible(hud):
    data = _twin_data(hud)
    return bool(data.get("TorF")) if data is not None else False


def _find_app():
    try:
        from . import ui
        return ui.find_app()
    except Exception:
        return None


def _schedule(fn):
    """次のフレーム（メインスレッド）で `fn()` を呼ぶ。Kivy が無い所（検査）ではその場で呼ぶ。"""
    def run(*_args):
        try:
            fn()
        except Exception:
            log_exc("twin_windows: the next-frame check failed")
    try:
        from kivy.clock import Clock
    except Exception:
        run()
        return
    try:
        Clock.schedule_once(run, 0)
    except Exception:
        log_exc("twin_windows: cannot schedule the next-frame check")


def _open_call(self, args, kwargs):
    return gates.Call(OPEN, self, args, kwargs, app=self, label=LABELS[OPEN],
                      left=frames.arg(args, kwargs, "left_inventory_obtainer", OPEN_ARGS),
                      right=frames.arg(args, kwargs, "right_inventory_obtainer", OPEN_ARGS),
                      label_text=frames.arg(args, kwargs, "left_label_text", OPEN_ARGS),
                      situation=frames.arg(args, kwargs, "situation", OPEN_ARGS))


def _move_call(self, args, kwargs):
    from . import items
    item = items.unwrap(self)
    inventory = frames.attr(self, "inventory", None)
    return gates.Call(MOVE, self, args, kwargs, app=_find_app(), label=LABELS[MOVE],
                      widget=self,
                      item=None if item is self else item,
                      old_inventory=None if isinstance(inventory, str) else inventory,
                      new_inventory=frames.arg(args, kwargs, "new_inventory", MOVE_ARGS))


def _backdrop_extra(self, args):
    target = args[1] if len(args) > 1 else None
    data = _twin_data(self)
    return {"target": target, "twin": data is not None and target is data}


def _wrapper(kind):
    gate = GATES[kind]

    if kind == BACKDROP:
        hidden = GATES[HIDDEN]

        def backdrop(orig, self, *args, **kwargs):
            # 登録が無ければ何も読まずに通す（外側を押すたび・画面を消すたびに来る）。
            if not gate.entries() and not hidden.entries():
                return orig(self, *args, **kwargs)
            app = _find_app()
            extra = _backdrop_extra(self, args)
            call = gates.Call(BACKDROP, self, args, kwargs, app=app, label=LABELS[BACKDROP], **extra)
            result = gate.run(call, lambda: orig(self, *args, **kwargs))
            if app is not None and hidden.entries():
                hud = self

                def check():
                    if _twin_visible(hud):
                        return
                    gone = gates.Call(HIDDEN, hud, args, kwargs, app=app, label=LABELS[HIDDEN], **extra)
                    hidden.run(gone, lambda: None)
                _schedule(check)
            return result
        return backdrop

    def window(orig, self, *args, **kwargs):
        if not gate.entries():
            return orig(self, *args, **kwargs)
        if kind == OPEN:
            call = _open_call(self, args, kwargs)
        elif kind == MOVE:
            call = _move_call(self, args, kwargs)
        else:
            call = gates.Call(kind, self, args, kwargs, app=self, label=LABELS[kind])
        return gate.run(call, lambda: orig(self, *args, **kwargs))
    return window


def install(ctx, write=None, kinds=(OPEN, MOVE, CLOSE, BACKDROP)):
    """関所を立てる。種類ごとに1つの世代につき1枚。包んだ対象の名前を返す。

    `HIDDEN` を渡すと `BACKDROP` の関所を立てる（`HIDDEN` は自分では包まない）。
    """
    done = []
    for kind in _kinds_of(kinds):
        wrapped = _WRAPPED_BY[kind]
        for target in GATES[wrapped].install(ctx, _wrapper(wrapped), write):
            if target not in done:
                done.append(target)
    return done
