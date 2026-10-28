"""`335_player_portrait_regenerate` の描き直しのボタンを押し、描き方の一覧から選ぶ。ARGS: {"pick": 描き方, "shift": 真偽}

    python tools/drive/drive.py playerportrait.py                              ボタンと一覧の様子だけ
    python tools/drive/drive.py playerportrait.py --args '{"pick": "chain"}'   一覧から txt2img→img2img を選ぶ

`pick` は `txt2img` / `img2img` / `chain`（ローダの `imagegen.METHODS`）。
人物欄が閉じていれば、ゲームの `toggle_character_sheet_window(0)` で主人公のシートを開く。
一覧が出ない（343 が描き方を出していない）ときは、ボタンを押した時点で txt2img で描き始める。
"""
KEEP_OPEN = True

BUTTON_ATTR = "_instantale_335_player_portrait"   # ui.MOD_WIDGET_PREFIX + "335_player_portrait"


def _button():
    button = getattr(hud(), BUTTON_ATTR, None)
    shown = button is not None and button.parent is not None and float(getattr(button, "opacity", 0)) > 0
    return button if shown else None


def main(say):
    from instantale_modloader import imagegen

    pick = ARGS.get("pick")
    step = {"n": 0}

    def open_sheet():
        # 右の欄の顔の枠は押せる部品ではないので、ゲームの開け閉めの関数で主人公（0 番）のシートを開く。
        app().toggle_character_sheet_window(0)
        say("opened the sheet")

    def tick(_dt):
        step["n"] += 1
        button = _button()
        if button is None:
            if step["n"] == 1:
                open_sheet()
                Clock.schedule_once(tick, 1.0)
                return
            say("no 335 button")
            say("<done>")
            return
        if step["n"] <= 2:
            say("methods {}".format(imagegen.methods("portrait")))
            if ARGS.get("shift"):
                say("shift は手で押す（ここでは押さない）")
            if not getattr(hud(), BUTTON_ATTR + "_menu", None):
                click(button)               # 一覧が開いていれば押さない（押すと閉じる）
            step["n"] = 3
            Clock.schedule_once(tick, 0.8)
            return
        menu = getattr(hud(), BUTTON_ATTR + "_menu", None) or []
        say("menu " + " | ".join(str(item.text) for item in menu))
        if pick and menu:
            want = imagegen.METHOD_LABELS.get(pick, pick)
            hits = [item for item in menu if item.text == want]
            say("pick {!r}: {} hit(s)".format(want, len(hits)))
            if hits:
                click(hits[0])
        say("<done>")

    Clock.schedule_once(tick, 0.1)
