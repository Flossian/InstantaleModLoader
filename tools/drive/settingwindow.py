"""ゲームの設定の画面（歯車）を開き、中のボタンを並べる。ARGS: {"press": ボタンの文字}

    python tools/drive/drive.py settingwindow.py
    python tools/drive/drive.py settingwindow.py --args '{"press": "画像の再生成(img2img)"}'

開くのはゲームの `InstantaleApp.toggle_setting_window`（HUD の `toggle_setting_window_visibility` を直に呼ぶと、
ゲームが `close_button_binding` 無しで落ちる）。開いている間は枠（`setting_window_layout`）に大きさがあり、
閉じると 0x0 になる（GAME.md §2.33）。開いた形は次のコマで組まれるので、少し待ってから読む。
"""
KEEP_OPEN = True


def main(say):
    layout = getattr(hud(), "setting_window_layout", None)
    if layout is None or layout.width < 1:
        app().toggle_setting_window()

    def later(_dt):
        layout = getattr(hud(), "setting_window_layout", None)
        buttons = find("Button", root=layout) if layout is not None else []
        say("buttons " + " | ".join(str(button.text) for button in buttons))
        want = ARGS.get("press")
        if want:
            hits = [button for button in buttons if button.text == want]
            say("press {!r}: {} hit(s)".format(want, len(hits)))
            if hits:
                click(hits[0])
        say("<done>")

    Clock.schedule_once(later, 0.5)
