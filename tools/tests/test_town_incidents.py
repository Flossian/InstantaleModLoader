# -*- coding: utf-8 -*-
"""338_town_incidents をゲーム抜きで通す。

    python tools/tests/test_town_incidents.py

確認するもの:

  規則     … 抽選は間隔ごとに1回・確率・同時の上限・街の休み・候補はギルドのある街だけ
             ／規模で起こりやすさが変わる／段は間隔ごとに1段、1回の日数送りで1段まで、深刻から日数で収まる
             ／値段の率（品の種類・向き・段・強さ）と宿代の率／表の文と LLM の文／窓の行／巻き戻し／控えの並び
  流れ     … 日数送りで起き、文を書かせて始まる／合わないと言われたら引き直す／掲示板を開くと依頼を作らせる
             ／その依頼を片付けると終わる／自然に収まったら依頼を世界から片付ける／ライバルが片付けたら終わる
             ／値段と宿代の段がいまいる街の出来事で効く／会話と情景の文
  ローダ   … ui.remove_quest が5か所から消す／hudicons の並べ方／宿代の関所（315 なし）の答え
"""
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_town_incidents")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import hudicons, llm, modnpc, prices, ui  # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_town_incidents")
MOD_NAME = "town_incidents_mod"
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load(name, path):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rules = load(MOD_NAME + "_rules", os.path.join(MOD_DIR, "incidents.py"))


class FixedRng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


TOWNS = [("0", "始まりの町", "town"), ("1", "灰の都", "city")]

# ================================================================ 規則
print("[規則] 抽選の間隔・確率・上限・休み")
bucket = rules.new_bucket()
kinds = list(rules.KINDS)
args = dict(chance_percent=100, interval_days=30, max_active=2, by_size=True)
check("初回は数え始めるだけ", rules.roll(bucket, 10, TOWNS, FixedRng(0.0), kinds, **args) is None
      and bucket["next_roll_day"] == 40, bucket)
check("間隔に届かなければ引かない", rules.roll(bucket, 39, TOWNS, FixedRng(0.0), kinds, **args) is None)
first = rules.roll(bucket, 40, TOWNS, FixedRng(0.0), kinds, **args)
check("届いたら起きる（答え待ち・1段）", first is not None and first["status"] == rules.PENDING
      and first["stage"] == 1 and first["area"] == "0" and first["started"] == 40, first)
check("次の抽選は届いた日から間隔ぶん先", bucket["next_roll_day"] == 70, bucket["next_roll_day"])
second = rules.roll(bucket, 200, TOWNS, FixedRng(0.0), kinds, **args)
check("何日経っても1回の呼び出しで起きるのは1つ", second is not None and second["area"] == "1"
      and len(rules.events_of(bucket)) == 2, rules.events_of(bucket))
check("同時の上限に届けば起きない",
      rules.roll(bucket, 400, TOWNS, FixedRng(0.0), kinds, **args) is None)
first["status"] = rules.ACTIVE
rules.end(bucket, first, 400, rules.END_PLAYER, 180)
check("終わった街は休みが付く", bucket["cooldown"]["0"] == 580, bucket["cooldown"])
check("休みの街には起きない（空いている街が無い）",
      rules.roll(bucket, 430, TOWNS, FixedRng(0.0), kinds, **args) is None)
check("休みが明ければ起きる", rules.roll(bucket, 600, TOWNS, FixedRng(0.0), kinds, **args) is not None)
class CountingRng(FixedRng):
    def __init__(self, value):
        FixedRng.__init__(self, value)
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.value


check("抽選の間隔は基準から前後にずらす", rules.next_roll_in(30, 15, FixedRng(0.0)) == 15
      and rules.next_roll_in(30, 15, FixedRng(0.999)) == 45 and rules.next_roll_in(30, 15, FixedRng(0.5)) == 30)
check("間隔は1日より短くしない", rules.next_roll_in(5, 15, FixedRng(0.0)) == 1)
check("幅が 0 なら乱数を引かない", (lambda rng: rules.next_roll_in(30, 0, rng) == 30 and rng.calls == 0)(CountingRng(0.0)))
spread_bucket = rules.new_bucket()
rules.roll(spread_bucket, 10, TOWNS, FixedRng(0.999), kinds, spread_days=15, **args)
check("次の抽選の日もずらす", spread_bucket["next_roll_day"] == 10 + 45, spread_bucket)
check("外れの乱数なら起きない", rules.roll(rules.new_bucket() | {"next_roll_day": 0}, 30, TOWNS,
                                FixedRng(0.99), kinds, **dict(args, chance_percent=25)) is None)
check("候補の街が無ければ起きない", rules.roll(rules.new_bucket() | {"next_roll_day": 0}, 30, [],
                                   FixedRng(0.0), kinds, **args) is None)

print("[規則] 規模で起こりやすさが変わる")
weights = dict((kind["key"], weight) for kind, weight in rules.weights(kinds, "village"))
check("村: 外敵・物資の途絶・失踪が 3", weights["invaders"] == 3 and weights["shortage"] == 3
      and weights["abductions"] == 3, weights)
check("どの規模でも 2（災害の後・不可解な怪事）", weights["aftermath"] == 2 and weights["anomaly"] == 2)
check("ほかは 1（良い出来事は表の倍率 0.5 を掛ける）", weights["epidemic"] == 1 and weights["boom"] == 0.5, weights)
flat = [(kind["key"], weight) for kind, weight in rules.weights(kinds, "city", by_size=False)]
check("規模で変えないなら表の倍率だけ", all(weight == rules.KIND_BY_KEY[key]["weight"] for key, weight in flat), flat)
check("表は18種（困り事14・良い出来事3とにわか景気）", len(kinds) == 18 and len(rules.KIND_BY_KEY) == 18
      and sorted(k["key"] for k in kinds if rules.is_good(k)) == ["boom", "festival", "harvest", "trade"],
      [k["key"] for k in kinds])

print("[規則] 出来事の表（ファイル）")
check("同梱の表は読めない出来事が無い", rules.PROBLEMS == [], rules.PROBLEMS)
boom = rules.KIND_BY_KEY["boom"]
check("にわか景気は4段（予兆・過熱・崩壊・収束）で依頼なし", boom["stages"] == ("予兆", "過熱", "崩壊", "収束")
      and rules.is_good(boom) and rules.stage_tones(boom) == ("good1", "good2", "bad3", "calm"), boom["stages"])
check("困り事の色の調子は黄・橙・赤の順", rules.stage_tones(rules.KIND_BY_KEY["epidemic"]) == ("bad1", "bad2", "bad3"))
check("良い出来事は兆し・盛況・収束", rules.KIND_BY_KEY["harvest"]["stages"] == ("兆し", "盛況", "収束")
      and rules.stage_tones(rules.KIND_BY_KEY["harvest"]) == ("good1", "good2", "calm"))
check("同じ品の買値と売値は別の効き目", ((rules.ALL, "buy", (5, 15, -10, -5)) in boom["effects"]
                                     and (rules.ALL, "sell", (10, 30, -25, -10)) in boom["effects"]), boom["effects"])
bad, why = rules.parse_kind({"key": "x y", "name": "a"})
check("key が英数字でなければ読まない", bad is None and "key" in why[0], why)
bad, why = rules.parse_kind({"key": "oops", "name": "a", "what": "b",
                             "stages": [{"name": "一", "tone": "wow", "arrive": "a"},
                                        {"name": "二", "tone": "bad", "arrive": "b"}]})
check("tone と into の書き間違いを理由に挙げる", bad is None and any("tone" in w for w in why)
      and any("into" in w for w in why), why)
bad, why = rules.parse_kind({"key": "oops", "name": "a", "what": "b",
                             "stages": [{"name": "一", "tone": "bad", "arrive": "a",
                                         "prices": [{"goods": ["gold"], "buy": 5}]}]})
check("品の種類の書き間違い", bad is None and any("goods" in w for w in why), why)
extra = os.path.join(OUT_DIR, "kinds_extra")
os.makedirs(extra, exist_ok=True)
for name, data in (("plague.json", {"key": "epidemic", "name": "大疫", "what": "病が広がる", "quest": "病を断つ依頼",
                                    "stages": [{"name": "流行", "tone": "bad", "arrive": "咳が聞こえる。"}]}),
                   ("stop.json", {"key": "outlaws", "enabled": False}),
                   ("new.json", {"key": "meteor", "name": "星降り", "what": "空から光が降る", "size": ["town"],
                                 "stages": [{"name": "兆し", "tone": "good", "arrive": "空が明るい。"},
                                            {"name": "見頃", "tone": "good", "into": "見頃を迎える",
                                             "prices": [{"goods": "random", "sell": 10}], "arrive": "星が降る。"}]}),
                   ("broken.json", None)):
    with io.open(os.path.join(extra, name), "w", encoding="utf-8") as fh:
        fh.write("{ not json" if data is None else json.dumps(data, ensure_ascii=False))
loaded, why = rules.load_kinds(os.path.join(rules.__file__.rsplit(os.sep, 1)[0], rules.DEFAULT_FILE), extra)
by_key = {kind["key"]: kind for kind in loaded}
check("同じ key は差し替える", by_key["epidemic"]["stages"] == ("流行",) and by_key["epidemic"]["name"] == "大疫")
check("enabled: false だけのファイルで止める（中身は残す）", by_key["outlaws"]["enabled"] is False
      and by_key["outlaws"]["stages"] == rules.KIND_BY_KEY["outlaws"]["stages"], by_key.get("outlaws"))
check("新しい出来事を足す（random の品・依頼なし）", "meteor" in by_key and rules.is_good(by_key["meteor"])
      and by_key["meteor"]["effects"] == [(rules.GOODS, "sell", (0, 10))], by_key.get("meteor"))
check("読めないファイルは飛ばして理由を残す", len(loaded) == 19 and any("broken.json" in w for w in why), why)
mine = os.path.join(OUT_DIR, "kinds_mine")
os.makedirs(mine, exist_ok=True)
with io.open(os.path.join(mine, rules.USER_FILE), "w", encoding="utf-8") as fh:
    fh.write(json.dumps([{"key": "only", "name": "一つ", "what": "x", "quest": "q",
                          "stages": [{"name": "一", "tone": "bad", "arrive": "a"}]}], ensure_ascii=False))
saved = list(rules.KINDS)
rules.reload_kinds(mine)
check("手元の incidents.json があれば同梱の既定より先に読む", [k["key"] for k in rules.KINDS] == ["only"],
      [k["key"] for k in rules.KINDS])
rules.use_kinds(saved)
saved = list(rules.KINDS)
rules.use_kinds(loaded)
check("止めた出来事は起こす候補から外す", "outlaws" not in [k["key"] for k in rules.KINDS])
stopped = {"id": 5, "area": "0", "kind": "outlaws", "stage": 2, "status": rules.ACTIVE, "text": {}}
check("止めた出来事も進行中の分は段・値段・文が引ける（設定画面で切ったときと同じ）",
      rules.known(stopped) and rules.price_rate(stopped, "weapon", rules.BUY) != 0
      and rules.stage_name(stopped) == rules.KIND_BY_KEY["outlaws"]["stages"][1]
      and rules.arrive_text(stopped), stopped)
check("表から消した出来事は known が偽", not rules.known({"kind": "no_such_kind"}))
event = rules.start(rules.new_bucket(), 10, "0", "町", by_key["meteor"], FixedRng(0.0))
check("起きたときに良い出来事かと段の名前を控えに書く", event["good"] is True and event["stage_name"] == "兆し"
      and event["goods"] in rules.CATEGORIES, event)
event["status"] = rules.ACTIVE
rules.advance({"events": [event]}, 70, stage_days=60, settle_days=90, steps_per_elapse=1)
check("段が進めば段の名前も書き直す", event["stage"] == 2 and event["stage_name"] == "見頃", event)
check("最後の段の次は収まる", rules.next_verb(event) == "収まる" and rules.days_to_next(event, 70, 60, 90) == 90)
rules.use_kinds(saved)
bubble = {"id": 9, "area": "3", "area_name": "鉱山町", "kind": "boom", "stage": 2, "status": rules.ACTIVE,
          "started": 100, "stage_day": 160, "quest": None, "text": {}}
lines = "\n".join(rules.view_lines([bubble], 175, {}, stage_days=60, settle_days=90, ended_days=360))
check("良い出来事の窓の行（段の名前・次の言い方・依頼の行なし）", "鉱山町　にわか景気（過熱）" in lines
      and "弾けるまで約45日" in lines and "依頼" not in lines, lines)
model = rules.view_model([bubble, dict(bubble, id=10, status=rules.ENDED, ended=170, end_by=rules.END_NATURAL)],
                         175, {}, stage_days=60, settle_days=90, ended_days=360)
card = model["cards"][0]
check("窓の札: 段の並び・調子・依頼なし", card["stages"] == ["予兆", "過熱", "崩壊", "収束"] and card["quest"] is None
      and card["tones"] == ["good1", "good2", "bad3", "calm"] and card["stage_name"] == "過熱", card)
check("終わった欄は出来事の言い方", model["ended"][0]["how"] == "収束した", model["ended"])

print("[規則] 段の進み方")
bucket = rules.new_bucket()
event = rules.start(bucket, 100, "0", "始まりの町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
event["status"] = rules.ACTIVE
opts = dict(stage_days=60, settle_days=90, steps_per_elapse=1, rest_days=180)
check("間隔の前は上がらない", rules.advance(bucket, 159, **opts) == [] and event["stage"] == 1)
changes = rules.advance(bucket, 400, **opts)
check("徒歩の移動のように長く進んでも1段まで", event["stage"] == 2 and event["stage_day"] == 400
      and [what for _e, what, _s in changes] == ["stage"], (event, changes))
rules.advance(bucket, 460, **opts)
check("次の間隔で深刻", event["stage"] == 3, event["stage"])
check("深刻のまま日数が足りなければ続く", rules.advance(bucket, 549, **opts) == [])
changes = rules.advance(bucket, 550, **opts)
check("深刻から日数が経つと自然に収まる", event["status"] == rules.ENDED
      and event["end_by"] == rules.END_NATURAL and event["ended"] == 550
      and [what for _e, what, _s in changes] == ["settled"], event)
check("自然に収まっても休みが付く", bucket["cooldown"]["0"] == 730, bucket["cooldown"])
bucket = rules.new_bucket()
event = rules.start(bucket, 0, "0", "町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
event["status"] = rules.ACTIVE
rules.advance(bucket, 999, stage_days=60, settle_days=90, steps_per_elapse=3, rest_days=0)
check("上限を3にすれば1回で収まるまで進む", event["status"] == rules.ENDED, event)

print("[規則] 値段と宿代の率")
active = {"kind": "epidemic", "stage": 2, "status": rules.ACTIVE}
check("疫病の2段: 回復の品の買値 +50%",
      rules.price_rate(active, "medicine", rules.BUY) == 50)
check("疫病の2段: 食べ物の類 +20%", rules.price_rate(active, "food", rules.BUY) == 20)
check("疫病は売値に触らない", rules.price_rate(active, "medicine", rules.SELL) == 0)
check("疫病は武器に触らない", rules.price_rate(active, "weapon", rules.BUY) == 0)
check("強さ 50% で半分", rules.price_rate(active, "medicine", rules.BUY, 50) == 25)
invaders = {"kind": "invaders", "stage": 3, "status": rules.ACTIVE}
check("外敵の3段: 武器は買値も売値も +50%", rules.price_rate(invaders, "weapon", rules.BUY) == 50
      and rules.price_rate(invaders, "weapon", rules.SELL) == 50)
unrest = {"kind": "unrest", "stage": 1, "status": rules.ACTIVE}
check("騒乱の1段: 売値 -10%・買値 +5%（全品）", rules.price_rate(unrest, "material", rules.SELL) == -10
      and rules.price_rate(unrest, "material", rules.BUY) == 5)
hoard = {"kind": "hoarding", "stage": 1, "status": rules.ACTIVE, "goods": "utility"}
check("買い占めは選んだ1種だけ", rules.price_rate(hoard, "utility", rules.BUY) == 30
      and rules.price_rate(hoard, "weapon", rules.BUY) == 0)
check("答え待ちの出来事は効かない",
      rules.price_rate(dict(active, status=rules.PENDING), "medicine", rules.BUY) == 0)
check("額: 100 に +50% で 150、率 0 は触らない", rules.scaled(100, 50) == 150 and rules.scaled(100, 0) is None)
check("額は 0 を下回らない", rules.scaled(10, -150) == 0)
check("宿代: 人の流入の3段 +100%",
      rules.inn_rate({"kind": "influx", "stage": 3, "status": rules.ACTIVE}) == 100)
check("宿代の無い出来事は 0", rules.inn_rate(active) == 0)
event = rules.start(rules.new_bucket(), 0, "0", "町", rules.KIND_BY_KEY["hoarding"], FixedRng(0.5))
check("買い占めは発生のときに品を1つ選ぶ", event["goods"] in rules.CATEGORIES, event["goods"])
check("区分: 食べ物は healing_item の food・drink も", rules.category_of("healing_item", "food") == "food"
      and rules.category_of("healing_item", "potion") == "medicine"
      and rules.category_of("consumable", "drink") == "food" and rules.category_of("weapon") == "weapon")

print("[規則] 文")
event = {"kind": "outlaws", "stage": 2, "status": rules.ACTIVE, "area_name": "灰の都", "text": {}}
check("呼び名は表の名前", rules.text_of(event, "name") == "無法者の横行")
check("着いたときの文は段で変わる", rules.arrive_text(event) == rules.KIND_BY_KEY["outlaws"]["arrive"][1])
check("噂に街の名前が入る", "灰の都" in rules.text_of(event, "rumor"), rules.text_of(event, "rumor"))
event["text"] = {"name": "黒狼団の横行", "arrive_2": "黒狼団の旗が街道に翻っている。"}
check("LLM の文を先に使う", rules.text_of(event, "name") == "黒狼団の横行"
      and rules.arrive_text(event) == "黒狼団の旗が街道に翻っている。")
hoard = {"kind": "hoarding", "goods": "weapon", "area_name": "町", "text": {}}
check("買い占めの店の文に品の名前", "武器" in rules.text_of(hoard, "shop"), rules.text_of(hoard, "shop"))
long_kind = dict(rules.KIND_BY_KEY["outlaws"], key="long_one",
                 arrive=tuple("段{}".format(i) for i in range(1, 13)),
                 stages=tuple("名{}".format(i) for i in range(1, 13)))
rules.KIND_BY_KEY["long_one"] = long_kind
try:
    check("段が10以上でも着いたときの文の段を取り違えない",
          [rules.arrive_text({"kind": "long_one", "stage": s, "text": {}}) for s in (10, 12)] == ["段10", "段12"])
finally:
    rules.KIND_BY_KEY.pop("long_one", None)

print("[規則] 店の1行は段の値段の向きで選ぶ")
boom = rules.KIND_BY_KEY["boom"]
check("向きが途中で変わる出来事は2組", rules.shop_patterns(boom) == [(1, 1, 0), (-1, -1, 0)],
      rules.shop_patterns(boom))
booming = {"kind": "boom", "stage": 2, "status": rules.ACTIVE, "area_name": "町",
           "text": {"shop": "値が上がっている。", "shop_turn": "値が崩れている。"}}
check("値上がりの段は shop", rules.shop_text(booming) == "値が上がっている。")
booming["stage"] = 3
check("崩れた段は shop_turn", rules.shop_text(booming) == "値が崩れている。")
booming["text"] = {"shop": "値が上がっている。"}
check("shop_turn が無い古い出来事は表の文（向きを言わない）", rules.shop_text(booming)
      == boom["shop"], rules.shop_text(booming))
check("強さ 0% なら出さない", rules.shop_text(dict(booming, stage=2), 0) == "")
feud = {"key": "feud", "stages": ("一", "二"), "effects": [(("food",), "buy", (0, 20))], "inn": None,
        "shop": "", "text": {}}
check("1段目が 0 の出来事は2段目の向きで読む", rules.shop_patterns(feud) == [(1, 0)], rules.shop_patterns(feud))
rules.KIND_BY_KEY["feud"] = feud
try:
    check("値段の動かない段では出さない",
          rules.shop_field({"kind": "feud", "stage": 1, "status": rules.ACTIVE}) is None
          and rules.shop_field({"kind": "feud", "stage": 2, "status": rules.ACTIVE}) == "shop")
finally:
    rules.KIND_BY_KEY.pop("feud", None)
check("真偽はローダの語で読む（いいえ は偽）", llm.truthy("はい") is True and llm.truthy("いいえ") is False)

print("[規則] 窓の行")
events = [{"id": 1, "area": "0", "area_name": "始まりの町", "kind": "epidemic", "stage": 2,
           "status": rules.ACTIVE, "started": 100, "stage_day": 160, "quest": "57", "text": {}},
          {"id": 2, "area": "1", "area_name": "灰の都", "kind": "influx", "stage": 1,
           "status": rules.ACTIVE, "started": 170, "stage_day": 170, "quest": None, "text": {}},
          {"id": 3, "area": "2", "area_name": "北の村", "kind": "unrest", "stage": 3,
           "status": rules.ENDED, "started": 10, "stage_day": 50, "ended": 150,
           "end_by": rules.END_NATURAL, "text": {}}]
rival = {"57": {"state": "aiming", "by": "ガラン"}}     # 窓口 quest_claims から組む形
lines = rules.view_lines(events, 175, rival, stage_days=60, settle_days=90, ended_days=360)
text = "\n".join(lines)
check("進行中の見出しと段", "進行中" in text and "始まりの町　感染症の流行（悪化）" in text, text)
check("悪化までの日数", "発生から75日。悪化するまで約45日。" in text, text)
check("ライバルが動き始めた依頼", "冒険者のガランが解決に向けて動き始めた" in text, text)
check("依頼がまだの出来事", "まだ出ていない（その街のギルドで掲示板を開くと出る）" in text, text)
check("概要は表の説明（LLM の文が無いとき）",
      "概要: " + rules.KIND_BY_KEY["epidemic"]["what"] + "。" in text, text)
check("終わったものに概要は出さない", text.count("概要: ") == 2, text)
named = "\n".join(rules.view_lines(
    [dict(events[1], text={"summary": "にわかの鉱脈景気で、街に人と金が流れ込んでいる"})], 175, {},
    stage_days=60, settle_days=90, ended_days=360, guild_names={"1": "傭兵仲介所『鉄の手』"}))
check("概要は LLM の文を先に使い、句点を足す", "概要: にわかの鉱脈景気で、街に人と金が流れ込んでいる。" in named, named)
check("依頼の行にその街のギルドの名前", "傭兵仲介所『鉄の手』で掲示板を開くと出る" in named, named)
staged = {"summary_1": "鉱脈の噂が広まり始めた", "summary_2": "人が押し寄せ値が跳ね上がっている",
          "summary_3": "鉱脈が尽きて人が去り、値が崩れている"}
check("概要は今の段の文", rules.text_of({"kind": "boom", "stage": 3, "text": staged}, "summary")
      == staged["summary_3"])
check("段の文が無ければ最初の段の文", rules.text_of({"kind": "boom", "stage": 4, "text": staged}, "summary")
      == staged["summary_1"])
check("版1 の1文（summary）は段に依らず使う",
      rules.text_of({"kind": "boom", "stage": 2, "text": {"summary": "旧"}}, "summary") == "旧")
check("終わったもの", "北の村　騒乱　自然に収まった（150日目）" in text, text)
check("段の重いものが先", lines[1].startswith("始まりの町"), lines)
check("影響（数字）: 疫病の2段", "影響: 薬の類の買値 +50%、食べ物の類の買値 +20%。" in text, text)
model = rules.view_model(events, 175, rival, stage_days=60, settle_days=90, ended_days=360, here="0",
                         guild_names={"1": "傭兵仲介所『鉄の手』"})
cards = model["cards"]
check("窓の札: 段の重いものが先・街・名前・段", [(c["town"], c["stage"]) for c in cards]
      == [("始まりの町", 2), ("灰の都", 1)] and cards[0]["name"] == "感染症の流行", cards)
check("窓の札: 日数・概要（句点つき）・影響", cards[0]["days"] == "発生から75日。悪化するまで約45日。"
      and cards[0]["summary"].endswith("。")
      and "".join(t for t, _ in cards[0]["effects"]) == "薬の類の買値 +50%、食べ物の類の買値 +20%", cards[0])
check("窓の影響: 数字だけに得か損か（買値が上がるのは損）",
      [(t, tone) for t, tone in cards[0]["effects"] if tone] == [("+50%", rules.BAD), ("+20%", rules.BAD)],
      cards[0]["effects"])
tone_kind, _ = rules.parse_kind({"key": "_tone_test", "name": "x", "what": "x", "stages": [
    {"name": "一", "tone": "bad", "arrive": "a", "inn": 20,
     "prices": [{"goods": ["medicine"], "buy": -10}, {"goods": ["food"], "sell": 20},
                {"goods": ["weapon"], "sell": -5}, {"goods": ["material"], "buy": 10, "sell": 10}]}]})
rules.KIND_BY_KEY["_tone_test"] = tone_kind
tones = [tone for _t, _n, tone in rules.effect_items({"kind": "_tone_test", "stage": 1})]
words = rules.effect_items({"kind": "_tone_test", "stage": 1}, mode=rules.EFFECTS_WORDS)
del rules.KIND_BY_KEY["_tone_test"]
check("影響の得か損か（買値が下がる・売値が上がる＝得、売値が下がる・買値が上がる・宿代が上がる＝損）",
      tones == [rules.GOOD, rules.GOOD, rules.BAD, rules.BAD, rules.GOOD, rules.BAD], tones)
check("言葉の出し方では数字を付けない", all(number == "" for _t, number, _tone in words), words)
check("窓の札: 依頼の行の調子", cards[0]["quest"] == ("冒険者のガランが解決に向けて動き始めた。", rules.QUEST_RIVAL)
      and cards[1]["quest"] == ("まだ出ていない（傭兵仲介所『鉄の手』で掲示板を開くと出る）。", rules.QUEST_PENDING),
      [c["quest"] for c in cards])
check("窓の終わったもの: 何日前と終わり方", model["ended"] == [
    {"when": "25日前", "town": "北の村", "name": "騒乱", "how": "自然に収まった", "by": rules.END_NATURAL}],
      model["ended"])
check("窓の現在地: その街の出来事", model["here"] == "（この街で感染症の流行が進行中）", model["here"])
calm = rules.view_model(events, 175, {}, stage_days=60, settle_days=90, ended_days=360, here="9",
                        in_words=True, effects=rules.EFFECTS_HIDDEN)
check("窓: 何も起きていない街・あいまいな日付・影響を出さない", calm["here"] == "（この街で進行中の出来事はない）"
      and calm["cards"][0]["days"].startswith("長く続いている。") and calm["cards"][0]["effects"] == []
      and calm["ended"][0]["when"] == "最近", calm)
words = "\n".join(rules.view_lines(events, 175, {}, stage_days=60, settle_days=90,
                                   ended_days=360, in_words=True, effects=rules.EFFECTS_WORDS))
check("日付はあいまい: 残り", "しばらく先に悪化するかもしれない" in words, words)
check("日付はあいまい: 発生から", "長く続いている。" in words and "発生から" not in words, words)
check("日付はあいまい: 終わった日", "自然に収まった（最近）" in words and "日目" not in words, words)
check("影響（言葉）", "影響: 薬の類が値上がり、食べ物の類が値上がり。" in words
      and "影響: 食べ物の類が値上がり、宿代が値上がり。" in words, words)
hidden = "\n".join(rules.view_lines(events, 175, {}, stage_days=60, settle_days=90,
                                    ended_days=360, effects=rules.EFFECTS_HIDDEN))
check("影響を出さない", "影響" not in hidden, hidden)
half = "\n".join(rules.view_lines(events, 175, {}, stage_days=60, settle_days=90,
                                  ended_days=360, strength_percent=50))
check("影響は強さの設定を掛けた値", "薬の類の買値 +25%" in half, half)
inn = rules.effect_parts({"kind": "influx", "stage": 3}, 100)
unrest = rules.effect_parts({"kind": "unrest", "stage": 1}, 100)
check("宿代と下がる向き", inn == ["食べ物の類の買値 +50%", "宿代 +100%"]
      and unrest == ["全品の売値 -10%", "全品の買値 +5%"], (inn, unrest))

print("[規則] 巻き戻しと控えの並び")
bucket = rules.new_bucket()
old = rules.start(bucket, 100, "0", "町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
new = rules.start(bucket, 300, "1", "都", rules.KIND_BY_KEY["influx"], FixedRng(0.0))
old["status"] = rules.ACTIVE
rules.end(bucket, old, 280, rules.END_RIVAL, by_name="ガラン")
old["told"] = {"settled": True}
check("古いセーブなら後で起きたことを外す", rules.rewind(bucket, 250)
      and new not in rules.events_of(bucket) and old["status"] == rules.ACTIVE
      and old["end_by"] is None, rules.events_of(bucket))
check("進行中へ戻した出来事は、終わったときの名前と知らせの印も外す",
      "end_by_name" not in old and not old["told"].get("settled"), old)
ordered = rules.order_bucket(bucket)
check("控えの鍵の並び", list(ordered) == list(rules.BUCKET_KEYS), list(ordered))
check("出来事の鍵の並び", list(ordered["events"][0]) == list(rules.EVENT_KEYS),
      list(ordered["events"][0]))
bucket = rules.new_bucket()
for index in range(25):
    item = rules.start(bucket, index, str(index), "", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
    rules.end(bucket, item, index, rules.END_NATURAL)
check("終わった出来事は直近20件", len(rules.events_of(bucket)) == 20
      and min(e["ended"] for e in rules.events_of(bucket)) == 5)


# ================================================================ 流れ（本体）
class Facility:
    def __init__(self, facility_type):
        self.facility_type = facility_type


class Node:
    def __init__(self, *types):
        self.facilities = {str(index): Facility(kind) for index, kind in enumerate(types)}


class Area:
    def __init__(self, area_id, name, *types):
        self.id = area_id
        self.name = name
        self.nodes = {"0": Node(*types)} if types else {}
        self.descriptions = {"overview": "{}の案内文".format(name)}
        self.quests = []


class Quest:
    def __init__(self, data):
        self.__dict__.update(data)


class World:
    def __init__(self, areas, days):
        self.areas = areas
        self.quests = {}
        self.characters = {"1": object(), "2": object()}
        self.days_elapsed = days


class Player:
    def __init__(self, area):
        self.current_area = area
        self.name = "検査の主人公"
        self.gold = 1000


class InstantaleApp:
    def __init__(self):
        areas = {"0": Area("0", "始まりの町", "guild", "inn"), "1": Area("1", "灰の都", "guild"),
                 "2": Area("2", "北の村", "inn"), "3": Area("3", "地下墓地")}
        self.world = World(areas, 100)
        self.player = Player("0")
        self.save_data_dict = {
            "world_data": {"world_name": "出来事の検査世界", "days_elapsed": 100,
                           "overview": "西部劇の荒野"},
            "player_data": {"name": "検査の主人公"},
            "areas": {"0": {"size": "town", "quests": []}, "1": {"size": "city", "quests": []},
                      "2": {"size": "village", "quests": []}, "3": {"size": "dungeon"}},
            "quests": {}}
        self.world_dict = {"quests": {}}
        self.current_quest_data = None
        self.texts = []
        self.next_quest = 50

    def elapse_days(self, days):
        self.world.days_elapsed += days
        self.save_data_dict["world_data"]["days_elapsed"] = self.world.days_elapsed

    def process_choice(self, function, choice_text=""):
        self.chosen = getattr(self, "chosen", [])
        self.chosen.append((type(function).__name__, choice_text))


APP = None


class DisplayQuestChoice:
    """掲示板。`generate_random_quest` は頼み文を通してから、いまいる街の依頼とダンジョンを作る（GAME.md §2.9）。"""
    prompts = []

    def __init__(self, app):
        self.app = app

    def generate_random_quest(self):
        app = self.app
        brief = HOOK_GENERATOR(lambda *a, **k: a[4], "世界", "町", "概要", "構造", "周辺の描写", 10)
        DisplayQuestChoice.prompts.append(brief)
        qid, aid = str(app.next_quest), str(app.next_quest + 100)
        app.next_quest += 1
        data = {"quest_title": "依頼{}".format(qid), "id": qid, "quest_type": "normal_quest",
                "neighboring_settlement_id": ui.area_id_of(ui.current_area(app)),
                "quest_area_id": aid, "config": {"status": "incomplete"}}
        app.world.quests[qid] = Quest(data)
        app.save_data_dict["quests"][qid] = dict(data)
        app.world.areas[aid] = Area(aid, "ダンジョン" + aid)
        app.save_data_dict["areas"][aid] = {"size": "dungeon"}
        ui.current_area(app).quests.append(qid)

    def execute(self, choice_text=""):
        return "board"


class FakeCtx:
    def __init__(self):
        self.out_dir = OUT_DIR
        self.state_dir = STATE_DIR
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.generation = random.random()

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1] else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator

    def hook(self, target):
        return self.hooks[target][-1]

    def superseded(self):
        return False


ui.Screen.when_idle = lambda self, app, then, **kw: then()
ui.Screen.say = lambda self, app, text: app.texts.append(text)
ui.Screen.schedule = lambda self, fn, delay=0.0: fn()
BUSY = []
ui.Screen.busy_on = lambda self, app: BUSY.append("on")
ui.Screen.busy_off = lambda self, app, restore=True: BUSY.append(("off", restore))
ui.find_app = lambda: APP
ui.cls_of = lambda name: DisplayQuestChoice if name == "DisplayQuestChoice" else None


def fresh(**settings):
    global APP, HOOK_GENERATOR
    for name in list(sys.modules):
        if name.startswith(MOD_NAME + ".") or name == MOD_NAME:
            sys.modules.pop(name, None)
    module = load(MOD_NAME, MOD)
    if hasattr(sys, module.STORE_ATTR):
        delattr(sys, module.STORE_ATTR)
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    modnpc.registry().clear()
    settings.setdefault("USE_LLM", False)
    settings.setdefault("ROLL_PERCENT", 100)
    for name, value in settings.items():
        setattr(module, name, value)
    module._store()["rng"] = FixedRng(0.0)
    ctx = FakeCtx()
    module.apply(ctx)
    module._LEGACY_DISABLED.clear()             # 開発機の設定の控えに左右されないように
    worker = module._store()["worker"]
    worker.enqueue = lambda job: worker.run(job)
    APP = InstantaleApp()
    HOOK_GENERATOR = ctx.hook("scripts.llm.llm_manager_world_generate:random_quest_generator")
    return module, ctx, APP


def elapse(ctx, app, days):
    ctx.hook("__main__:InstantaleApp.elapse_days")(lambda self, d: self.elapse_days(d), app, days)


def bucket_of(module, app):
    store = module._store()["worlds"]
    return store.load(store.playthrough(app), fresh=True)


print("[流れ] 日数送りで起き、表の文で始まる（LLM 切）")
module, ctx, app = fresh()
elapse(ctx, app, 0)
check("初回は数え始めるだけ", not rules.events_of(bucket_of(module, app)))
elapse(ctx, app, 30)
events = rules.events_of(bucket_of(module, app))
check("ギルドのある街に起き、すぐ始まる", len(events) == 1 and events[0]["status"] == rules.ACTIVE
      and events[0]["area"] in ("0", "1"), events)
check("ギルドの無い村・ダンジョンには起きない", all(e["area"] not in ("2", "3") for e in events))
event = events[0]
check("いまいる街なら着いた文を出す", (event["area"] != "0") or any(
    rules.KIND_BY_KEY[event["kind"]]["arrive"][0] in t for t in app.texts), app.texts)
check("いまいる街ならギルドへの案内も添える（名前が無ければ「ギルド」）", (event["area"] != "0") or any(
    t == "ギルドに行けば、この件で人手を求める依頼が出ているかもしれない。" for t in app.texts), app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[流れ] 値段と宿代の段は、いまいる街の出来事で効く")
module, ctx, app = fresh()
bucket_store = module._store()["worlds"]
key = bucket_store.playthrough(app)
bucket = bucket_store.load(key)
ev = rules.start(bucket, 100, "0", "始まりの町", rules.KIND_BY_KEY["epidemic"], FixedRng(0.0))
ev["status"] = rules.ACTIVE
ev["stage"] = 3
bucket_store.save(key)
layer = [fn for owner, fn, _temp, _w in prices._item_registry()["adjust"] if owner == module.OWNER][0]
potion = {"name": "薬", "item_type": "healing_item", "attributes": {"買価": 100, "item_detail": "potion"}}
check("いまいる街の疫病3段で回復の品の買値 x2", layer(potion, prices.BUY, 100) == 200,
      layer(potion, prices.BUY, 100))
check("武器には触らない", layer({"item_type": "weapon", "attributes": {}}, prices.BUY, 100) is None)
app.player.current_area = "1"
check("よその街に居れば触らない", layer(potion, prices.BUY, 100) is None)
app.player.current_area = "0"
ev["kind"] = "influx"
bucket_store.save(key)
inn = [fn for owner, fn in prices._inn_store()["adjust"] if owner == module.OWNER][0]
check("人の流入の3段で宿代 x2", inn(app, "private_room", 100) == 200, inn(app, "private_room", 100))
check("窓口の答えも段を通す（315 なし・素の 100）", prices.inn_room(app, "private_room") == 200,
      prices.inn_room(app, "private_room"))

print("[流れ] 会話と情景")
note_fn = None
for entry in modnpc.registry().values():
    for layer in entry.get("layers") or []:
        if layer.get("owner") == module.OWNER and callable(layer.get("notes")):
            note_fn = layer["notes"]
check("会話の窓口に登録した", note_fn is not None, modnpc.registry())
if note_fn is not None:
    text = note_fn({"app": app, "npc_id": "1"}) or ""
    check("その土地の住人には出来事", "人の流入" in text and "深刻" in text, text)
    app.player.current_area = "1"
    text = note_fn({"app": app, "npc_id": "1"}) or ""
    check("よその街の住人には噂（2段以上）", "噂" in text and "始まりの町" in text, text)
    app.player.current_area = "0"
seen = {}
ctx.hook("scripts.llm.llm_manager:narrator")(lambda *a, **k: seen.update(args=a), "w", "s", "主人公")
check("情景描写の素材に情勢を足す", "【この土地の情勢】" in seen["args"][2], seen)

print("[流れ] 掲示板を開くと依頼を作らせ、片付けると終わる")
board = ctx.hook("__main__:DisplayQuestChoice.execute")
DisplayQuestChoice.prompts = []
# 作らせている最中に、もう一度掲示板が押された（押下ごとに別スレッドで `execute` が走る。待機表示を出す前の一瞬）
pressed_again = []
plain_generate = DisplayQuestChoice.generate_random_quest


def generate_with_another_press(self):
    if not pressed_again:
        pressed_again.append(board(lambda s, *a: s.execute(*a), DisplayQuestChoice(self.app), "クエスト掲示板"))
    plain_generate(self)


DisplayQuestChoice.generate_random_quest = generate_with_another_press
del BUSY[:]
app.is_button_enabled = True
try:
    result = board(lambda self, *a: self.execute(*a), DisplayQuestChoice(app), "クエスト掲示板")
finally:
    DisplayQuestChoice.generate_random_quest = plain_generate
bucket = bucket_of(module, app)
ev = rules.live_in(bucket, "0")
check("依頼を作らせた", ev["quest"] == "50" and "50" in app.world.quests, ev)
check("作らせている間に届いた押下は作らせずに捨てる（依頼は1つ）",
      pressed_again == [None] and app.next_quest == 51, (pressed_again, app.next_quest))
check("作らせる間は待機表示を出し、選択肢を塗らずに解く", BUSY == ["on", ("off", False)], BUSY)
check("頼み文に出来事の説明を足した", "人の流入" in DisplayQuestChoice.prompts[-1],
      DisplayQuestChoice.prompts)
check("掲示板の一言", any("依頼が新しく貼り出されている" in t for t in app.texts), app.texts)
check("作らせた回は続きを呼ばず、同じボタンで開き直す", result is None
      and getattr(app, "chosen", []) == [("DisplayQuestChoice", "クエスト掲示板")],
      (result, getattr(app, "chosen", None)))
result = board(lambda self, *a: self.execute(*a), DisplayQuestChoice(app), "クエスト掲示板")
check("開き直した回はゲームの掲示板がそのまま出る", result == "board", result)
board(lambda self, *a: self.execute(*a), DisplayQuestChoice(app), "クエスト掲示板")
check("2回目は作らない", app.next_quest == 51, app.next_quest)
app.current_quest_data = app.world.quests["50"]
ui_current = ui.current_quest_id
ui.current_quest_id = lambda _app: "50" if _app.current_quest_data is not None else None


def finish(self):
    app.world.quests["50"].config["status"] = "completed"
    app.current_quest_data = None


ctx.hook("__main__:QuestEndManager.execute")(lambda self, *a: finish(self), type("QE", (), {"app": app})())
ev = rules.events_of(bucket_of(module, app))[0]
check("片付けたら終わる（プレイヤー）", ev["status"] == rules.ENDED and ev["end_by"] == rules.END_PLAYER, ev)
check("収まった一言", any("これで収まるだろう" in t for t in app.texts), app.texts)
check("片付けた依頼は世界に残る（手柄）", "50" in app.world.quests)
check("エラーなし", not ctx.errors, ctx.errors)

print("[流れ] 良い出来事は依頼を作らず、案内も添えない")
module, ctx, app = fresh()
bucket_store = module._store()["worlds"]
key = bucket_store.playthrough(app)
bucket = bucket_store.load(key)
ev = rules.start(bucket, 100, "0", "始まりの町", rules.KIND_BY_KEY["festival"], FixedRng(0.0))
ev["status"] = rules.ACTIVE
bucket_store.save(key)
board = ctx.hook("__main__:DisplayQuestChoice.execute")
DisplayQuestChoice.prompts = []
next_quest = app.next_quest
result = board(lambda self, *a: self.execute(*a), DisplayQuestChoice(app), "クエスト掲示板")
check("掲示板を開いても依頼を作らせない", result == "board" and app.next_quest == next_quest
      and not DisplayQuestChoice.prompts and rules.live_in(bucket_of(module, app), "0")["quest"] is None,
      (result, app.next_quest, DisplayQuestChoice.prompts))
check("ギルドへの案内を添えない", not any("ギルドに行けば" in t for t in app.texts), app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[流れ] 起こさない出来事（DISABLED_KINDS と版1 の設定）")
module, ctx, app = fresh(DISABLED_KINDS="epidemic, outlaws、 boom")
keys = [kind["key"] for kind in module.enabled_kinds()]
check("カンマ・読点・空白で区切った英名を外す", not {"epidemic", "outlaws", "boom"} & set(keys)
      and len(keys) == len(rules.KINDS) - 3, keys)
check("版1 の KIND_* で False の分だけ拾う",
      module.legacy_disabled({"KIND_FIRE": False, "KIND_FLOOD": True, "USE_LLM": False}) == {"fire"}
      and module.legacy_disabled(None) == set())
module._LEGACY_DISABLED.update({"fire"})
check("版1 の分も合わせて外す", "fire" not in [kind["key"] for kind in module.enabled_kinds()])
module._LEGACY_DISABLED.clear()

print("[流れ] 頼み文の決まりは段の数と良い悪いで変わる")
rule = module.arrive_rule(rules.KIND_BY_KEY["boom"])
check("4段なら arrive_4 まで書かせる", "arrive_1 / arrive_2 / arrive_3 / arrive_4" in rule
      and "4は収束" in rule and "空にする" not in rule, rule)
rule = module.summary_rule(rules.KIND_BY_KEY["boom"])
check("概要も段ごとに書かせる（にわか景気は4段）", "summary_1 / summary_2 / summary_3 / summary_4" in rule
      and "3は崩壊" in rule and "賑わ" not in rule and "空にする" not in rule, rule)
rule = module.summary_rule(rules.KIND_BY_KEY["epidemic"])
check("困り事の概要は「誰がどう困っているか」で、summary_4 は空にさせる", "困っている" in rule
      and "summary_4: 空にする" in rule, rule)
check("頼み文に段ごとの概要の欄がある", all("summary_{}".format(i) in module.TEXT_FIELDS for i in (1, 2, 3, 4))
      and "summary" not in module.TEXT_FIELDS)
rule = module.arrive_rule(rules.KIND_BY_KEY["harvest"])
check("3段なら arrive_4 を空にさせる", "1は兆し、2は盛況、3は収束" in rule and "arrive_4: 空にする" in rule, rule)

print("[流れ] 店の1行の手掛かりは値段の向きの組ごと")
up, turn = module.effect_hint(rules.KIND_BY_KEY["boom"])
check("プロンプトは前半を shop、崩れた後を shop_turn に書かせる", "上がる" in up and "下がる" not in up
      and turn.startswith("- shop_turn:") and "下がる" in turn and "上がる" not in turn, (up, turn))
up, turn = module.effect_hint(rules.KIND_BY_KEY["epidemic"])
check("向きが変わらない出来事は shop_turn を空にさせる", turn == module.SHOP_TURN_NONE, turn)
feud = {"key": "feud", "stages": ("一", "二"), "effects": [(("food",), "buy", (0, 20))], "inn": None}
check("1段目が 0 の出来事は2段目の向きで書かせる（下がると書かせない）", "上がる" in module.effect_hint(feud)[0]
      and "下がる" not in module.effect_hint(feud)[0], module.effect_hint(feud))
check("宿代だけ動く出来事も宿代の向きを書かせる", "宿代が上がる" in module.effect_hint(rules.KIND_BY_KEY["abductions"])[0])
check("プロンプトに shop_turn の欄がある", "shop_turn" in module.TEXT_FIELDS and "{shop_turn_rule}" in module.TEXT_PROMPT)

print("[流れ] 自然に収まったら依頼を世界から片付ける")
module, ctx, app = fresh(STAGE_DAYS=10, SETTLE_DAYS=10, STEPS_PER_ELAPSE=3)
ui.current_quest_id = lambda _app: None
bucket_store = module._store()["worlds"]
key = bucket_store.playthrough(app)
bucket = bucket_store.load(key)
ev = rules.start(bucket, 100, "0", "始まりの町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
ev["status"] = rules.ACTIVE
bucket_store.save(key)
ctx.hook("__main__:DisplayQuestChoice.execute")(lambda self, *a: None, DisplayQuestChoice(app), "")
check("依頼ができている", "50" in app.world.quests and "50" in app.save_data_dict["quests"])
app.texts = []
elapse(ctx, app, 90)
check("その街に居れば、収まったその場で一言", any("どこかへ流れていったらしい" in t for t in app.texts),
      app.texts)
ev = rules.events_of(bucket_of(module, app))[0]
check("収まった", ev["status"] == rules.ENDED and ev["end_by"] == rules.END_NATURAL, ev)
check("依頼は生きた一覧からもセーブの辞書からも消える", "50" not in app.world.quests
      and "50" not in app.save_data_dict["quests"])
check("街の依頼の並びとダンジョンの土地も消える", "50" not in app.world.areas["0"].quests
      and "150" not in app.world.areas and "150" not in app.save_data_dict["areas"])
check("街そのものは消さない", "0" in app.world.areas)
area_hook = ctx.hook("__main__:AreaMoveManager.execute")
app.texts = []
area_hook(lambda self, *a: None, type("AM", (), {"app": app})())
check("その場で知らせた一言は、着いたときに繰り返さない", not any("らしい" in t for t in app.texts), app.texts)
# よその街に居る間に収まったら、次に着いたときに一言。
bucket = bucket_store.load(key)
away = rules.start(bucket, ui.game_day(app), "0", "始まりの町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
away["status"] = rules.ACTIVE
bucket_store.save(key)
app.player.current_area = "1"
app.texts = []
elapse(ctx, app, 90)
check("よその街に居れば、収まったその場では出さない", not any("らしい" in t for t in app.texts), app.texts)
app.player.current_area = "0"
app.texts = []
area_hook(lambda self, *a: None, type("AM", (), {"app": app})())
check("次に着いたとき収まった一言", any("どこかへ流れていったらしい" in t for t in app.texts), app.texts)
app.texts = []
area_hook(lambda self, *a: None, type("AM", (), {"app": app})())
check("一言は1度だけ", not app.texts, app.texts)
bucket = bucket_store.load(key)
again = rules.start(bucket, 200, "0", "始まりの町", rules.KIND_BY_KEY["unrest"], FixedRng(0.0))
again["status"] = rules.ACTIVE
bucket_store.save(key)
app.texts = []
area_hook(lambda self, *a: None, type("AM", (), {"app": app})())
check("依頼がまだなら、着いたときにギルドへの案内を添える", any("人手を求める依頼" in t for t in app.texts),
      app.texts)
again["quest"] = "99"
bucket_store.save(key)
app.texts = []
area_hook(lambda self, *a: None, type("AM", (), {"app": app})())
check("依頼が出た後は案内を添えない", app.texts and not any("人手を求める依頼" in t for t in app.texts),
      app.texts)
check("エラーなし", not ctx.errors, ctx.errors)

print("[流れ] ほかの冒険者が片付けたら終わる（窓口 quest_claims）")
from instantale_modloader import quest_claims  # noqa: E402
quest_claims.reset()
module, ctx, app = fresh()
bucket_store = module._store()["worlds"]
key = bucket_store.playthrough(app)
bucket = bucket_store.load(key)
ev = rules.start(bucket, 100, "0", "始まりの町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
ev["status"] = rules.ACTIVE
bucket_store.save(key)
DisplayQuestChoice.prompts = []
ctx.hook("__main__:DisplayQuestChoice.execute")(lambda self, *a: self.execute(*a), DisplayQuestChoice(app),
                                                "クエスト掲示板")
rival_quest = rules.live_in(bucket_of(module, app), "0")["quest"]
urgent = quest_claims.urgent(app)
check("困り事の依頼を急ぎとして窓口に置く", [(r["quest"], r["area"], r["owner"]) for r in urgent]
      == [(rival_quest, "0", "338_town_incidents")] and "（" in urgent[0]["why"], urgent)
held, release = threading.Event(), threading.Event()


def hold_lock():
    with bucket_store.lock:                 # 別のスレッドが控えを書いている最中
        held.set()
        release.wait(5)


holder = threading.Thread(target=hold_lock)
holder.start()
held.wait(5)
started = time.monotonic()
urgent = quest_claims.urgent(app)
release.set()
holder.join(5)
check("ほかのスレッドが錠を持っていても待たず、写しで答える", time.monotonic() - started < 1
      and [r["quest"] for r in urgent] == [rival_quest], urgent)


class Owner:
    def superseded(self):
        return False


claim_rows = [{"quest": rival_quest, "area": "0", "title": "依頼", "by": "ガラン", "state": quest_claims.DONE,
               "day": 120}]
quest_claims.declare("another_mod", Owner(), lambda app_: claim_rows)
elapse(ctx, app, 1)
ev = rules.events_of(bucket_of(module, app))[0]
check("ほかの冒険者が片付けたので終わる", ev["status"] == rules.ENDED and ev["end_by"] == rules.END_RIVAL, ev)
check("片付けられた出来事の依頼は世界から片付ける（片付けた側の MOD を外しても掲示板に戻らない）",
      rival_quest not in app.world.quests and rival_quest not in app.save_data_dict["quests"],
      sorted(app.world.quests))
check("片付けた人の名前を出来事に残す", ev.get("end_by_name") == "ガラン", ev)
check("いまいる街なら一言", any("ガラン" in t for t in app.texts), app.texts)
check("終わった出来事は急ぎに置かない", quest_claims.urgent(app) == [], quest_claims.urgent(app))
claim_rows[0]["by"] = "別の冒険者"
check("終わった欄は終えたときの名前で書く", rules.ended_words(ev) == "冒険者のガランが解決した",
      rules.ended_words(ev))
check("名前の無い古い出来事は「ほかの冒険者」", rules.ended_words({"end_by": rules.END_RIVAL})
      == "ほかの冒険者が解決した")
quest_claims.reset()

print("[流れ] LLM の文と、合わないときの引き直し")
answers = [{"fits": "いいえ"}, {"fits": "はい", "name": "黒い咳", "arrive_1": "咳が聞こえる。",
                                "rumor": "灰の都で黒い咳が流行っているらしい。"}]
saved = (llm.create_structure, llm.ask)
llm.create_structure = lambda *a, **k: object()
llm.ask = lambda *a, **k: answers.pop(0) if answers else None
try:
    module, ctx, app = fresh(USE_LLM=True)
    elapse(ctx, app, 0)
    elapse(ctx, app, 30)
    ev = rules.events_of(bucket_of(module, app))[0]
    check("合わないと言われたら別の種類で書かせ直す", len(ev["tries"]) == 2
          and ev["tries"][0] != ev["tries"][1], ev["tries"])
    check("LLM の呼び名で始まる", ev["status"] == rules.ACTIVE and ev["text"].get("name") == "黒い咳", ev)

    module, ctx, app = fresh(USE_LLM=True)
    app.world_dict["world_data"] = {"world_name": "出来事の検査世界"}     # 周回の鍵を世界×主人公にする
    elapse(ctx, app, 0)

    def load_another_save(*_a, **_k):
        app.save_data_dict["player_data"]["name"] = "別の主人公"      # 答えを待つ間に別のセーブを読んだ
        return {"fits": "はい", "name": "別の世界で書いた名前"}

    llm.ask = load_another_save
    elapse(ctx, app, 30)
    app.save_data_dict["player_data"]["name"] = "検査の主人公"
    ev = rules.events_of(bucket_of(module, app))[0]
    check("答えを待つ間に別のセーブを読んだら書かず、答え待ちのまま残す（読み込み直したら頼み直す）",
          ev["status"] == rules.PENDING and not ev.get("text"), ev)
finally:
    llm.create_structure, llm.ask = saved

print("[流れ] 表から消した出来事は収まった扱いで片付ける")
module, ctx, app = fresh()
bucket_store = module._store()["worlds"]
key = bucket_store.playthrough(app)
bucket = bucket_store.load(key)
gone = rules.start(bucket, 100, "0", "始まりの町", rules.KIND_BY_KEY["outlaws"], FixedRng(0.0))
gone["status"] = rules.ACTIVE
gone["kind"] = "deleted_kind"
waiting = rules.start(bucket, 100, "1", "灰の都", rules.KIND_BY_KEY["influx"], FixedRng(0.0))
waiting["kind"] = "deleted_too"
bucket_store.save(key)
DisplayQuestChoice.prompts = []
ctx.hook("__main__:DisplayQuestChoice.execute")(lambda self, *a: self.execute(*a), DisplayQuestChoice(app),
                                                "クエスト掲示板")
check("掲示板を開いても落ちない（依頼は作らない）", not ctx.errors and not DisplayQuestChoice.prompts, ctx.errors)
module.ROLL_PERCENT = 0
elapse(ctx, app, 1)
events = {event["id"]: event for event in rules.events_of(bucket_of(module, app))}
check("進行中は自然に収まった扱いで終える", events[gone["id"]]["status"] == rules.ENDED
      and events[gone["id"]]["end_by"] == rules.END_NATURAL, events.get(gone["id"]))
check("文を書かせる前のものは捨てる", waiting["id"] not in events, sorted(events))
check("エラーなし", not ctx.errors, ctx.errors)
ui.current_quest_id = ui_current

# ================================================================ 設定画面（窓は開かない）
print("[設定画面] 見本の行と様子の文")
tool = load(MOD_NAME + "_tool", os.path.join(MOD_DIR, "tool.py"))
sample = "\n".join(tool.preview_lines({"WINDOW_DAYS": "あいまい", "WINDOW_EFFECTS": "言葉",
                                       "PRICE_STRENGTH_PERCENT": "100", "STAGE_DAYS": "60",
                                       "SETTLE_DAYS": "90"}))
check("見本はいまの欄の値で描く（あいまい・言葉）", "長く続いている" in sample and "値上がり" in sample
      and "発生から" not in sample, sample)
sample = "\n".join(tool.preview_lines({"WINDOW_EFFECTS": "出さない", "STAGE_DAYS": "x"}))
check("打ちかけの値でも落ちない・影響を出さない", "影響" not in sample and "発生から" in sample, sample)
check("設定画面が扱う項目は宣言と同じ", sorted(tool.setting_keys()) == sorted(
    json.load(io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8"))["settings"]),
    tool.setting_keys())
described = tool.describe({"events": [dict(e) for e in tool.SAMPLE_EVENTS], "next_roll_day": None,
                           "cooldown": {"2": 600}}, {})
check("様子の文", "まだ数え始めていない" in described and "土地2（600日目まで）" in described
      and "感染症の流行" in described, described)

# ================================================================ ローダの部品
print("[ローダ] ui.remove_quest は5か所から消す")
app = InstantaleApp()
APP = app
DisplayQuestChoice(app).generate_random_quest()
gone = ui.remove_quest(app, "50")
check("5か所", sorted(gone) == sorted(["world.quests", "save_data_dict.quests", "Area.quests",
                                       "world.areas", "save_data_dict.areas"]), gone)
app.next_quest = 60
DisplayQuestChoice(app).generate_random_quest()
app.save_data_dict["areas"]["160"]["size"] = "town"
gone = ui.remove_quest(app, "60")
check("指す土地が街なら土地は残す", "world.areas" not in gone and "160" in app.world.areas, gone)
check("無い依頼は何もしない", ui.remove_quest(app, "999") == [])

print("[ローダ] hudicons の並べ方")
xs = hudicons.positions(0.07, 5, 396.0, 40.0)
check("左右の余白が同じ・等間隔", abs(xs[0] - 0.07) < 1e-9 and abs((1 - xs[-1] - 40 / 396.0) - 0.07) < 1e-9
      and abs((xs[1] - xs[0]) - (xs[4] - xs[3])) < 1e-9, xs)
check("1980 幅の間は約 35px", 34 < (xs[1] - xs[0]) * 396 - 40 < 36, (xs[1] - xs[0]) * 396 - 40)
check("幅が決まっていなければ並べない", hudicons.positions(0.07, 5, 0, 40) == [])
spans = [(12.0, 36.0), (3.0, 37.0), (3.0, 37.0), (3.0, 37.0), (4.0, 36.0)]
xs = hudicons.optical_positions(spans, 396.0, 27.7 + 12.0, 40.0)
seen = [(x * 396 + a, x * 396 + b) for x, (a, b) in zip(xs, spans)]
gaps = [round(seen[i + 1][0] - seen[i][1], 6) for i in range(4)]
check("見えている部分どうしの間が等しい", len(set(gaps)) == 1, gaps)
check("見えている部分の左右の余白が等しい", abs(seen[0][0] - (396 - seen[-1][1])) < 1e-6, seen)
pixels = bytearray(4 * 4 * 2)
for x, light in ((1, 200), (2, 30)):
    for y in range(2):
        at = (y * 4 + x) * 4
        pixels[at:at + 4] = bytes((light, light, light, 255))
check("暗いドットは見えている部分に数えない", hudicons.ink_columns(pixels, 4, 2) == (1, 2),
      hudicons.ink_columns(pixels, 4, 2))

print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
