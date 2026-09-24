# -*- coding: utf-8 -*-
"""405_regional_economy をゲーム抜きで通す。

    python tools/tests/test_regional_economy.py

LLM もゲームも要らない部分だけを見る。

  規模     … 街かダンジョンかを素データの `areas[*]["size"]` から決める。
             **実行時の `Area.size` は読めない**（`324_` の実機 38/38）ので
             `save_data_dict` / `world_dict` から取る
  門       … 街はプロフィールを作り、ダンジョンは作らない。
             世界構造（`World.structure`）は保存されないので条件にしない
  倍率     … 需給スコア 1〜5 と、強い変動 / 弱い変動 の対応
  矢印     … プレイヤー側は需要過多が上向き。店主側は 6-score で引き直すので、
             表が対称であれば必ず逆を向く
  スコア   … 名指しの品がジャンルより先。どちらにも当たらなければ 3
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir,
                                        "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_regional_economy")

spec = importlib.util.spec_from_file_location(
    "regional_economy_mod", MOD, submodule_search_locations=[MOD_DIR])
mod = importlib.util.module_from_spec(spec)
sys.modules["regional_economy_mod"] = mod
spec.loader.exec_module(mod)


# ---------------------------------------------------------------- 偽ゲーム
class App:
    """`world_dict` と `save_data_dict` だけの app（405 が読むのはここ）。"""

    def __init__(self, world=None, save=None):
        if world is not None:
            self.world_dict = world
        if save is not None:
            self.save_data_dict = save


def world(name, areas):
    return {"world_data": {"name": name}, "areas": areas}


#: 実セーブを復号して確かめた形（3世界とも街9件、残りは全部 dungeon）。
AREAS = {
    "0": {"name": "エテルナ", "size": "city", "connections": ["1", "4"]},
    "1": {"name": "リヴェール", "size": "town", "connections": ["0"]},
    "4": {"name": "ソラリス", "size": "village", "connections": ["0"]},
    "9": {"name": "下層区の迷路路地", "size": "dungeon", "connections": []},
}


def main():
    print("[エリアの規模]")
    app = App(world("テスト", AREAS))
    check("都市が読める", mod._area_size(app, "0") == "city")
    check("町が読める", mod._area_size(app, "1") == "town")
    check("村が読める", mod._area_size(app, "4") == "village")
    check("ダンジョンが読める", mod._area_size(app, "9") == "dungeon")
    check("id は数でも文字列でも同じ", mod._area_size(app, 0) == "city")
    check("知らない id は None", mod._area_size(app, "99") is None)
    check("id が空なら None", mod._area_size(app, "") is None)
    check("app が無ければ None", mod._area_size(None, "0") is None)
    check("areas が無ければ None",
          mod._area_size(App({"world_data": {"name": "空"}}), "0") is None)
    check("大文字と空白は均す",
          mod._area_size(App(world("T", {"0": {"size": " CITY "}})), "0") == "city")
    check("size が文字列でなければ None",
          mod._area_size(App(world("T", {"0": {"size": 3}})), "0") is None)

    print("[遊んでいるセーブ側を優先する]")
    # 同じ id が別の世界で別の規模を持つことがある。
    # 世界名が食い違うときは、いま遊んでいるセーブ側を採る（405 の既存の決まり）。
    app = App(world("古い世界", {"0": {"size": "dungeon"}}),
              world("いまの世界", {"0": {"size": "city"}}))
    check("世界名が食い違えばセーブ側", mod._area_size(app, "0") == "city")
    app = App(world("同じ世界", {"0": {"size": "city"}}),
              world("同じ世界", {"0": {"size": "city"}}))
    check("一致していれば同じ答え", mod._area_size(app, "0") == "city")

    print("[プロフィールを作る門]")
    # **世界構造は条件にしない。** `World.structure` は保存される world_data の
    # 5項目に入らないので、ロードした世界では必ず読めない（GAME.md §2.27）。
    app = App(world("テスト", AREAS))
    for area_id, size in (("0", "city"), ("1", "town"), ("4", "village")):
        check("{} はプロフィールを作る".format(size),
              mod._area_size(app, area_id) in mod.SETTLEMENT_SIZES)
    check("ダンジョンは作らない",
          mod._area_size(app, "9") not in mod.SETTLEMENT_SIZES)
    check("読めないエリアは作らない",
          mod._area_size(app, "99") not in mod.SETTLEMENT_SIZES)
    check("街の規模は3語", tuple(mod.SETTLEMENT_SIZES) == ("village", "town", "city"),
          mod.SETTLEMENT_SIZES)
    check("構造を引く関数は残っていない",
          not hasattr(mod, "_active_world_structure"))

    print("[需給スコアの倍率]")
    mod.STRONG_FLUCTUATION_MULTIPLIER = 1.5
    mod.WEAK_FLUCTUATION_MULTIPLIER = 1.2
    check("5（需要過多）は強い変動", mod._regional_multiplier(5) == 1.5)
    check("4 は弱い変動", mod._regional_multiplier(4) == 1.2)
    check("3 は等倍", mod._regional_multiplier(3) == 1.0)
    check("2 は弱い変動の逆数", abs(mod._regional_multiplier(2) - 1 / 1.2) < 1e-9)
    check("1（供給過多）は強い変動の逆数",
          abs(mod._regional_multiplier(1) - 1 / 1.5) < 1e-9)
    check("知らないスコアは等倍", mod._regional_multiplier(9) == 1.0)
    mod.STRONG_FLUCTUATION_MULTIPLIER = "こわれた"
    check("設定が壊れていたら等倍", mod._regional_multiplier(5) == 1.0)
    mod.STRONG_FLUCTUATION_MULTIPLIER = 1.5

    print("[表示は値段の増減（%）]")
    # 既定は 強い変動 1.5 / 弱い変動 1.2。
    check("5（需要過多）は +50%", mod._score_percent(5) == "（価格+50%）",
          mod._score_percent(5))
    check("4 は +20%", mod._score_percent(4) == "（価格+20%）",
          mod._score_percent(4))
    check("3 は空（別のスイッチで `-`）", mod._score_percent(3) == "")
    check("変動なしには既定で何も出さない",
          mod.SCORE_MARK_3 is False and mod.configured_score_mark(3) == "",
          (mod.SCORE_MARK_3, mod.configured_score_mark(3)))
    mod.SCORE_MARK_3 = True
    check("スイッチをONにすると `-` が出る", mod.configured_score_mark(3) == "-")
    mod.SCORE_MARK_3 = False
    check("動いた品には常に表示が付く",
          all(mod.configured_score_mark(s) for s in (1, 2, 4, 5)))
    check("2 は -17%", mod._score_percent(2) == "（価格-17%）",
          mod._score_percent(2))
    check("1（供給過多）は -33%", mod._score_percent(1) == "（価格-33%）",
          mod._score_percent(1))
    mod.STRONG_FLUCTUATION_MULTIPLIER = 2.0
    check("設定を変えれば表示も変わる", mod._score_percent(5) == "（価格+100%）",
          mod._score_percent(5))
    mod.STRONG_FLUCTUATION_MULTIPLIER = 1.5
    check("符号が必ず付く",
          all(("+" in mod._score_percent(s)) or ("-" in mod._score_percent(s))
              for s in (1, 2, 4, 5)))
    # 品名の横に数字だけが出ると、何の%か読み取れない。
    check("何の%かが表示に書いてある",
          all("価格" in mod._score_percent(s) for s in (1, 2, 4, 5)),
          mod._score_percent(5))

    print("[割合と矢印を切り替える]")
    check("既定は割合", mod.MARK_STYLE == mod.MARK_STYLE_PERCENT, mod.MARK_STYLE)
    # 選ぶ画面で形が分かるよう、選択肢の綴りそのものに例を入れてある。
    check("選択肢に表示例が入っている",
          "+20%" in mod.MARK_STYLE_PERCENT and "↑↑" in mod.MARK_STYLE_ARROW,
          (mod.MARK_STYLE_PERCENT, mod.MARK_STYLE_ARROW))
    # 例の書き方を変えても保存済みの設定が効くよう、頭の語だけで見分ける。
    mod.MARK_STYLE = "矢印"
    check("例が付いていない綴りでも矢印になる",
          mod.configured_score_mark(5) == "↑↑", mod.configured_score_mark(5))
    mod.MARK_STYLE = mod.MARK_STYLE_ARROW
    check("矢印にすると矢印が出る", mod.configured_score_mark(5) == "↑↑",
          mod.configured_score_mark(5))
    check("矢印も値段の向き（値上がりが上）",
          mod.configured_score_mark(5) == "↑↑"
          and mod.configured_score_mark(1) == "↓↓")
    check("矢印でも変動なしは既定で出ない", mod.configured_score_mark(3) == "")
    # 形が変わっても色の意味は同じ。向きで決めているので両方に効く。
    for style in (mod.MARK_STYLE_PERCENT, mod.MARK_STYLE_ARROW):
        mod.MARK_STYLE = style
        up = mod.configured_score_mark(5)
        down = mod.configured_score_mark(1)
        check("{}: 自分の品の値上がりは得の色".format(style),
              mod.GAIN_MARK_COLOR in mod.colored_mark(up))
        check("{}: 店の品の値上がりは損の色".format(style),
              mod.LOSS_MARK_COLOR in mod.colored_mark(up, trade_owner=True))
        check("{}: 自分の品の値下がりは損の色".format(style),
              mod.LOSS_MARK_COLOR in mod.colored_mark(down))
    check("知らない綴りなら割合に倒れる",
          (setattr(mod, "MARK_STYLE", "なにか") or
           "価格" in mod.configured_score_mark(5)))
    mod.MARK_STYLE = mod.MARK_STYLE_PERCENT

    print("[プロフィールの中身]")
    # ジャンル別スコアは作るのをやめた。値付けは品ごとの検品が決めるので、
    # 誰も読まないものを毎回LLMに作らせていた。
    check("ジャンル表は残っていない", not hasattr(mod, "GENRES"))
    check("ジャンル別スコアを組む関数も無い", not hasattr(mod, "_genre_scores"))
    check("推測で値付けする関数も無い", not hasattr(mod, "_score_for_item"))
    prompt = mod._build_messages({
        "world_key": "W", "area_id": "1", "area_name": "街",
        "world_overview": "概要", "area_overview": "説明"})[0]["content"]
    check("頼み文にジャンルを求める文が無い", "genre" not in prompt)
    check("要約と品名は引き続き求める",
          all(word in prompt for word in ("regional_economy_summary",
                                          "major_products", "surplus_goods",
                                          "shortage_goods")))

    print("[揃ったプロフィールかの判定]")
    # ここでジャンル表を要求すると、新しく作った控えが毎回「未完成」になって
    # 作り直し続ける。
    check("要約があれば揃っている",
          mod._record_ready({"regional_economy_summary": "鉱山の街"}))
    check("要約が空なら揃っていない",
          not mod._record_ready({"regional_economy_summary": "  "}))
    check("ジャンル表だけでは揃っていない",
          not mod._record_ready({"genre_scores": {"ore": 2}}))
    check("辞書でなければ揃っていない", not mod._record_ready(None))

    print("[検品のプロンプトと突き合わせ]")
    # 固定の指示だけを system に置く。街や棚が変わっても前半が変わらない形。
    snap = {"world_key": "W", "area_id": "5", "area_name": "オーラム",
            "world_overview": "概要", "area_overview": "説明"}
    econ = {"regional_economy_summary": "鉱山の町。",
            "major_industries": ["採掘"], "major_products": ["魔導鋼"],
            "surplus_goods": ["鉄鉱石"], "shortage_goods": ["小麦"]}
    goods = [{"item_key": "k%d" % i, "content_key": "c%d" % i,
              "item_id": "i%d" % i, "name": name, "description": "",
              "item_type": "", "item_detail": "", "rarity": "common"}
             for i, name in enumerate(("鉄鉱石", "小麦", "謎の石"))]
    msgs = mod._build_classification_messages(snap, econ, goods)
    head = next(x["content"] for x in msgs if x["role"] == "system")
    body = next(x["content"] for x in msgs if x["role"] == "user")
    check("system は街や棚の中身を含まない",
          all(word not in head for word in ("オーラム", "鉄鉱石", "鉱山の町")), head[-80:])
    check("街も棚も user にある",
          all(word in body for word in ("オーラム", "鉄鉱石", "鉱山の町")))
    other = mod._build_classification_messages(
        {"world_key": "W2", "area_id": "9", "area_name": "別の街",
         "world_overview": "", "area_overview": ""}, econ, goods)
    check("街が変わっても system は同じ",
          next(x["content"] for x in other if x["role"] == "system") == head)
    check("内部の鍵は渡さない", "item_key" not in body and "item_id" not in body,
          body[:120])
    check("商品には連番が振られる", '"n": 1' in body and '"n": 3' in body)

    print("[応答の突き合わせ]")

    def classify(rows):
        got = mod._normalize_batch({"items": rows}, goods, econ)
        return [(c["item_name"], c["classification"], c["score"]) for c in got]

    ordered = [
        {"n": 1, "classification": "surplus_good", "score": 1,
         "matched_goods": ["鉄鉱石"]},
        {"n": 2, "classification": "shortage_good", "score": 5,
         "matched_goods": ["小麦"]},
        {"n": 3, "classification": "unclassified", "score": 3,
         "matched_goods": []}]
    want = [("鉄鉱石", "surplus_good", 1), ("小麦", "shortage_good", 5),
            ("謎の石", "unclassified", 3)]
    check("順番どおりなら入力順に並ぶ", classify(ordered) == want, classify(ordered))
    check("順番が崩れても連番で拾う",
          classify(list(reversed(ordered))) == want,
          classify(list(reversed(ordered))))
    check("連番が文字列でも拾う",
          classify([{"n": "2", "classification": "shortage_good", "score": 5,
                     "matched_goods": ["小麦"]}])[1]
          == ("小麦", "shortage_good", 5))
    check("連番が無ければ名前で拾う",
          classify([{"item_name": "小麦", "classification": "shortage_good",
                     "score": 5, "matched_goods": ["小麦"]}])[1]
          == ("小麦", "shortage_good", 5))
    check("返って来なかった品は未分類",
          classify([])[0] == ("鉄鉱石", "unclassified", 3))
    # `matched_goods` は飾りではない。照合が空なら分類を捨てる。
    check("照合が空なら分類を採らない",
          classify([{"n": 1, "classification": "surplus_good", "score": 1}])[0]
          == ("鉄鉱石", "unclassified", 3))

    print("[検品の控えは内容で引く]")

    class Goods:
        """`scripts.items.Item` の、検品に関わるところだけ。"""

        def __init__(self, name="宝石", iid="item_1", desc="澄んだ石",
                     detail="gem", rarity="common"):
            self.name = name
            self.id = iid
            self.description = desc
            self.item_type = "material"
            self.rarity = rarity
            self.attributes = {"item_detail": detail}

    one = mod._item_snapshot(Goods(iid="item_1"))
    two = mod._item_snapshot(Goods(iid="item_2"))       # 買って棚に作り直された品
    other = mod._item_snapshot(Goods(iid="item_3", desc="濁った石"))
    check("1回の問い合わせでは品ごとに別の鍵",
          len({one["item_key"], two["item_key"], other["item_key"]}) == 3)
    check("内容が同じなら控えの鍵は同じ（内部IDを含めない）",
          one["content_key"] == two["content_key"],
          (one["content_key"], two["content_key"]))
    check("内容が違えば控えの鍵も違う",
          one["content_key"] != other["content_key"])

    print("[控えは state に残ったぶんも引く]")
    state = {"classification_lock": __import__("threading").RLock(),
             "classifications": {}}
    scope = ("テスト", "5")
    record = {"classifications": {one["content_key"]: {"score": 5}}}
    check("state の控えが引ける",
          mod._known_classification(state, scope, record,
                                    one["content_key"]) == {"score": 5})
    check("買って作り直された品も同じ答え",
          mod._known_classification(state, scope, record,
                                    two["content_key"]) == {"score": 5})
    check("知らない品は None",
          mod._known_classification(state, scope, record,
                                    other["content_key"]) is None)
    check("控えの無いエリアでも落ちない",
          mod._known_classification(state, scope, {}, one["content_key"]) is None)

    print("[聞くのは未検品のぶんだけ]")
    wanted = mod._unclassified(state, scope, record, [one, two, other])
    check("既知の2件は聞かない", [s["content_key"] for s in wanted]
          == [other["content_key"]], [s["name"] for s in wanted])
    check("全部既知なら空",
          mod._unclassified(state, scope, record, [one, two]) == [])
    check("エリアが分からなければ聞かない",
          mod._unclassified(state, None, record, [other]) == [])

    print("[実行中の控えが state より先]")
    state["classifications"][(scope, other["content_key"])] = {"score": 1}
    check("実行中の控えが引ける",
          mod._known_classification(state, scope, record,
                                    other["content_key"]) == {"score": 1})
    check("それも聞かなくなる",
          mod._unclassified(state, scope, record, [one, two, other]) == [])

    print("[矢印の色]")
    # 色は**向き**で決める。スコアで決めると、表記が反転する店主側だけ
    # 色と向きが食い違う（緑の下向き矢印が出る）。
    import importlib.util as _il

    class Ctx:
        """色付けに要るぶんだけの偽 ctx。"""
        generation = 1
        _mod = "405_regional_economy"
        mod_dir = MOD_DIR

        def __init__(self):
            self.hooks = {}
            self.errors = []

        def out_path(self, *p):
            path = os.path.join(OUT_DIR, *p)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path

        state_path = out_path

        def logger(self, name, **kw):
            import instantale_modloader as _ml
            return _ml.ModContext.logger(self, name, **kw)

        def log(self, m, level="INFO"):
            pass

        def log_exc(self, m):
            self.errors.append(m)

        def write_json(self, p, d, indent=1):
            import instantale_modloader as _ml
            return _ml.write_json(p, d, indent=indent, report=self.log_exc)

        def read_json(self, p, default=None):
            import instantale_modloader as _ml
            return _ml.read_json(p, default, report=self.log_exc)

        def write_text(self, p, t):
            import instantale_modloader as _ml
            return _ml.write_text(p, t, report=self.log_exc)

        def superseded(self):
            return False

        def on_ready(self, fn, **kw):
            return None

        def wrap(self, target, **kw):
            def deco(fn):
                self.hooks[target] = fn
                return fn
            return deco

    holder = {}
    original_wrap = Ctx.wrap

    # `colored_mark` は apply() の中にあるので、包みの登録に相乗りして取り出す。
    def capture_wrap(self, target, **kw):
        holder.setdefault("ctx", self)
        return original_wrap(self, target, **kw)

    Ctx.wrap = capture_wrap
    ctx = Ctx()
    mod.apply(ctx)
    check("色付きで当てても例外が出ない", not ctx.errors, ctx.errors)

    check("得と損で別の色", mod.GAIN_MARK_COLOR != mod.LOSS_MARK_COLOR)
    check("色は6桁のHTML色",
          all(len(c) == 7 and c.startswith("#")
              for c in (mod.GAIN_MARK_COLOR, mod.LOSS_MARK_COLOR)),
          (mod.GAIN_MARK_COLOR, mod.LOSS_MARK_COLOR))
    check("色の設定がモジュールに在る", hasattr(mod, "COLOR_SCORE_MARKS"))

    # 値段が上がることの意味は左右で逆。自分の品なら高く売れて得、
    # 店の品なら余計に払うので損。数字は同じで、色だけが分かれる。
    paint = mod.colored_mark
    if True:
        up, down = mod._score_percent(5), mod._score_percent(1)
        mine_up = paint(up, trade_owner=False)
        shop_up = paint(up, trade_owner=True)
        mine_down = paint(down, trade_owner=False)
        shop_down = paint(down, trade_owner=True)
        check("数字は左右で同じ",
              up in mine_up and up in shop_up, (mine_up, shop_up))
        check("自分の品が値上がり＝得の色", mod.GAIN_MARK_COLOR in mine_up, mine_up)
        check("店の品が値上がり＝損の色", mod.LOSS_MARK_COLOR in shop_up, shop_up)
        check("自分の品が値下がり＝損の色", mod.LOSS_MARK_COLOR in mine_down, mine_down)
        check("店の品が値下がり＝得の色", mod.GAIN_MARK_COLOR in shop_down, shop_down)
        check("等倍には色を付けない", paint("-", trade_owner=False) == "-")

        mod.REVERSE_TRADE_MARK = False
        check("反転を切ると色は値段の向きだけを表す",
              mod.GAIN_MARK_COLOR in paint(up, trade_owner=True))
        mod.REVERSE_TRADE_MARK = True

        mod.COLOR_SCORE_MARKS = False
        check("色を切れば素の文字", paint(up, trade_owner=True) == up)
        mod.COLOR_SCORE_MARKS = True

    print("[注入し直しで降りたワーカーの残した仕事]")
    # 前の世代のワーカーは `ctx.superseded()` で降り、待ち行列に残った仕事の
    # `pending` と Event はそのまま残る。同じ地点が積まれに来たら、積み直さずに
    # ワーカーを立て直して片付ける（品揃えが待ちの上限まで止まらない）。
    import threading
    import types as _types

    class GenCtx(Ctx):
        def __init__(self):
            Ctx.__init__(self)
            self.gone = False

        def superseded(self):
            return self.gone

    area = _types.SimpleNamespace(id="0", name="エテルナ")
    app = App(world("ワーカーの世界", AREAS))
    app.player = _types.SimpleNamespace(current_area=area)
    move = "__main__:MovePhaseManager.move_phase"
    asked = []
    original_ask = mod._ask_profile
    mod._ask_profile = lambda c, w, snap: asked.append(snap["area_id"]) or None

    def left_over(state, old_ctx, worker):
        """前の世代が降りた後の形を置く。`(scope, Event)`。"""
        snapshot = mod._snapshot(app)
        scope = (snapshot["world_key"], snapshot["area_id"])
        event = threading.Event()
        with state["data_lock"]:
            state["pending"].add(scope)
            state["profile_events"][scope] = event
        state["jobs"].put((snapshot, "left over"))
        state["worker"] = worker
        state["worker_ctx"] = old_ctx
        return scope, event

    try:
        for label, still_running in (("降りきった", False), ("まだ降りる途中", True)):
            if hasattr(sys, mod.STATE_STORE_ATTR):
                delattr(sys, mod.STATE_STORE_ATTR)
            old_ctx = GenCtx()
            mod.apply(old_ctx)
            old_ctx.gone = True
            hold = threading.Event()
            stuck = threading.Thread(target=hold.wait, daemon=True)
            if still_running:
                stuck.start()
            state = mod._store()
            scope, event = left_over(state, old_ctx, stuck if still_running else None)
            new_ctx = GenCtx()
            mod.apply(new_ctx)
            del asked[:]
            new_ctx.hooks[move](lambda self: None, _types.SimpleNamespace(app=app))
            done = event.wait(timeout=10)
            hold.set()
            check("{}: 残った仕事が片付く".format(label), done)
            check("{}: 積み直さない（1回だけ聞く）".format(label), asked == ["0"], asked)
            check("{}: pending が空く".format(label), scope not in state["pending"],
                  state["pending"])
            check("{}: 例外が出ない".format(label),
                  not new_ctx.errors and not old_ctx.errors, new_ctx.errors + old_ctx.errors)
            new_ctx.gone = True
            if done:                 # 片付かなかったときに検査ごと止まらないように
                state["jobs"].join()
    finally:
        mod._ask_profile = original_ask
        if hasattr(sys, mod.STATE_STORE_ATTR):
            delattr(sys, mod.STATE_STORE_ATTR)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
