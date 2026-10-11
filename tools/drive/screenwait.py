# -*- coding: utf-8 -*-
"""画面が変わるまで待つ。ARGS: {"leave": 画面の名前, "texts": [文字, …], "enabled": ボタンの文字, "timeout": 秒}

`leave` を渡すと、画面（ScreenManager の current）がその名前でなくなったら返す。
`texts` を渡すと、そのどれかを一部に含む文字の部品が見えたら返す。
世界やキャラを作る画面のように、遊ぶ画面の手待ち（`idle()`）で見分けられない段を待つのに使う。
世界の生成は終わっても画面が変わらず、同じ画面に「世界'<名前>'を生成し保存しました。」が出る。
`{"texts": ["を生成し保存しました"]}` で待つ（`leave` では返らない）。
`enabled` を渡すと、その文字のボタンが押せるようになったら返す。
キャラを作る画面のさいころは、生成の間「人生を始める」が押せない。`{"enabled": "人生を始める"}` で待つ。
30秒ごとに今の画面の名前を1行書く。`--wait` は `timeout` より長めに取る。
"""
KEEP_OPEN = True


def main(say):
    leave = ARGS.get("leave")
    texts = list(ARGS.get("texts") or ())
    enabled = ARGS.get("enabled")
    timeout = float(ARGS.get("timeout", 900))
    started = time.time()
    state = {"last": started}

    def current():
        return getattr(getattr(app(), "root", None), "current", None)

    def seen():
        for n in ui.walk_widgets(Window):
            text = getattr(n, "text", None)
            if isinstance(text, str) and any(part in text for part in texts):
                return text
        return None

    def tick(_dt):
        now = time.time()
        name = current()
        hit = seen() if texts else None
        if enabled and any(not getattr(n, "disabled", True) for n in find("Button", enabled)):
            hit = enabled
        if (leave and name != leave) or hit:
            say("SCREEN {} after {:.0f}s{}".format(name, now - started, " (text {!r})".format(hit) if hit else ""))
            say("<done>")
            return
        if now - started > timeout:
            say("TIMEOUT on {} after {:.0f}s".format(name, now - started))
            say("<done>")
            return
        if now - state["last"] >= 30:
            state["last"] = now
            say("still {} ({:.0f}s)".format(name, now - started))
        Clock.schedule_once(tick, 1.0)
    _ALIVE.append(tick)
    Clock.schedule_once(tick, 0.5)
