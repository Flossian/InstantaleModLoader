# -*- coding: utf-8 -*-
"""ローダの窓口 `holdings`（家と施設の窓）をゲーム抜きで通す。

    python tools/tests/test_holdings.py

Kivy は読まない（窓を描く部分は実機で見る）。次を確認する。

  側       … 置いた側だけが出る。用済みの ctx の側は出さない。同じ側は差し替え。知らない側は断る
  一覧     … どちらかの側があれば `hudicons` に「家と施設」の行が1つ。世界を遊んでいない間は出さない
  中身     … 落ちた側は None（もう片方は出る）。札の文は通貨の表記へ直す
  上の欄   … 現在地・いまいる街の自分の建物・軒数・受け取れる売上の合計
  並べ方   … 左の列を上から埋め、入らない札から右へ（1枚目は必ず左）
  知らせ   … notify は「家と施設」の行に周回つきの印を付ける
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import holdings, hudicons, panels, ui  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  {}   {}{}".format("ok" if cond else "FAIL", name, "" if cond else " -- {}".format(detail)))
    if not cond:
        failures.append(name)


class FakeCtx:
    def __init__(self, generation=1, superseded=False):
        self.generation = generation
        self._superseded = superseded

    def superseded(self):
        return self._superseded

    def wrap(self, target, required=True, safe=False):
        return lambda fn: fn


# 前の検査の残りを持ち込まない
setattr(sys, holdings.STORE_ATTR, None)
setattr(sys, hudicons.STORE_ATTR, None)
logged = []

print("[側]")
houses_ctx, facilities_ctx = FakeCtx(), FakeCtx()


def houses(app):
    return {"cards": [{"name": "借りている家", "where": "灰の都", "rows": [("家賃", "1,600G／1期 480日")]}],
            "count": 1, "income": None, "here": ["借りている家"]}


def facilities(app):
    return {"cards": [{"name": "灯火亭", "notes": ["1日の売上 60G　建設費 20,000G（受け取り済み 21,600G）"]}],
            "count": 2, "income": 9000, "here": ["灯火亭"]}


holdings.provide(houses_ctx, holdings.HOUSES, houses, write=logged.append)
check("家だけ置けば家の側だけ", holdings.sides() == [holdings.HOUSES], holdings.sides())
holdings.provide(facilities_ctx, holdings.FACILITIES, facilities, write=logged.append)
check("両方置けば家・施設の順", holdings.sides() == [holdings.HOUSES, holdings.FACILITIES], holdings.sides())
facilities_ctx._superseded = True
check("用済みの世代の側は出さない", holdings.sides() == [holdings.HOUSES], holdings.sides())
facilities_ctx = FakeCtx(generation=2)
holdings.provide(facilities_ctx, holdings.FACILITIES, facilities, write=logged.append)
check("次の世代が置き直せば戻る", holdings.sides() == [holdings.HOUSES, holdings.FACILITIES], holdings.sides())
try:
    holdings.provide(FakeCtx(), "garden", houses)
    check("知らない側は断る", False)
except ValueError:
    check("知らない側は断る", True)

print("[一覧]")
rows = [entry for entry in hudicons._store()["icons"].values() if entry["key"] == holdings.ICON_KEY]
check("「家と施設」の行が1つ", len(rows) == 1 and rows[0]["label"] == holdings.TITLE
      and rows[0]["order"] == holdings.ICON_ORDER, rows)
check("行の絵は3枚とも在る", all(os.path.isfile(path) for path in holdings.ICON_IMAGES), holdings.ICON_IMAGES)
real_playthrough = hudicons.current_playthrough
hudicons.current_playthrough = lambda app: None
check("世界を遊んでいない間は行を出さない", not holdings.listed(object()))
hudicons.current_playthrough = lambda app: "世界×主人公"
check("遊んでいる間は出す", holdings.listed(object()))

print("[中身]")
real_coins = ui.rewrite_coins
ui.rewrite_coins = lambda text: text.replace("G", "円") if isinstance(text, str) else text
try:
    model = holdings.collect(object())
finally:
    ui.rewrite_coins = real_coins
check("両方の側が並ぶ", list(model) == [holdings.HOUSES, holdings.FACILITIES], list(model))
check("札の中の文（並びの中の組も）を通貨の表記へ直す",
      model[holdings.HOUSES]["cards"][0]["rows"][0] == ("家賃", "1,600円／1期 480日")
      and "受け取り済み 21,600円" in model[holdings.FACILITIES]["cards"][0]["notes"][0], model)


def broken(app):
    raise RuntimeError("帳簿が壊れている")


real_log_exc = holdings.log_exc
holdings.log_exc = lambda msg: None
holdings.provide(facilities_ctx, holdings.FACILITIES, broken, write=logged.append)
model = holdings.collect(object())
holdings.log_exc = real_log_exc
check("落ちた側は None、もう片方は出る",
      model[holdings.FACILITIES] is None and model[holdings.HOUSES]["count"] == 1, model)
check("落ちたことをその MOD のログに書く", any(line.startswith("WARN holdings:") for line in logged), logged)
holdings.provide(facilities_ctx, holdings.FACILITIES, facilities, write=logged.append)

print("[上の欄]")
model = holdings.collect(object())
pieces = holdings.subtitle(model, "陽光の砦")
text = "".join(piece for piece, _color in pieces)
check("現在地・いまいる街の建物・軒数・売上の合計",
      text == "現在地　陽光の砦（借りている家・灯火亭）　　家 1軒・施設 2軒　受け取れる売上 合計 9,000G", text)
check("街の名前と合計は濃い字", [p for p, c in pieces if c == panels.TEXT] == ["陽光の砦", "9,000G"], pieces)
only_houses = {holdings.HOUSES: houses(None)}
text = "".join(piece for piece, _color in holdings.subtitle(only_houses, ""))
check("家だけで現在地が読めなければ軒数だけ", text == "家 1軒", text)

print("[並べ方]")
check("全部入れば全部左", panels.flow_split([100, 100, 100], 400, 10) == 3)
check("入らない札から右へ", panels.flow_split([200, 150, 100, 50], 400, 10) == 2)
check("後ろの札が小さくても、一度右へ回ったら左へ戻さない", panels.flow_split([300, 200, 10], 400, 10) == 1)
check("1枚目は入らなくても左", panels.flow_split([500, 100], 400, 10) == 1)
check("札が無ければ0", panels.flow_split([], 400, 10) == 0)

print("[知らせ]")
holdings.notify(playthrough="世界×主人公")
check("「家と施設」の行に周回つきの印", hudicons._store()["attention"].get(holdings.ICON_KEY) == {"世界×主人公"},
      hudicons._store()["attention"])

hudicons.current_playthrough = real_playthrough
holdings.remove_all()
check("外すと側が空になる", holdings.sides() == [], holdings.sides())

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
