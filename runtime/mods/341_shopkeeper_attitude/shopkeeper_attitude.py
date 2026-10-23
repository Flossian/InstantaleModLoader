# -*- coding: utf-8 -*-
"""店主の人柄と主人公との関係で、売買の値段が変わる。遊び方は DOC.md、決まりは `attitude`。

- 店に着いたら（`MovePhaseManager.move_phase` の後、選択肢が組み上がって手が空いてから）、
  店主の性格・関係・感情・好感度・会話の回数・人生ログを渡して、裏で LLM に応じ方を1回頼む
- 手配中の土地では、手配を数える MOD（`336_crime_overhaul`）がローダの窓口 `wanted` に置いた率（店の値の圧）を
  引き受けて、怯える・動じない・足元を見るを決める。窓口に率が無ければ平時として、自分の率で負ける・普段どおり・ふっかける
- 売買を押したとき（`ShoppingStartManagerRemake.execute`。ワーカースレッド）に判定が済んでいなければ、
  待機表示を出して待つ。頼んでいない店主（店の中でロードした等）はその場で頼む
- 判定は店主ごとに控え（`state\\shopkeeper_attitude\\<世界×主人公>.json`）、場面・好感度（手配中は手配度も）が変わるか、
  使い回す日数が過ぎるまで使う。日数を過ぎた控えは書くときに消す
- 値段はローダの関所（`prices.adjust`）へ一時の段として置く。保存の直前には外れる（セーブへ焼き付けない）
- 判定が無い（LLM が答えない・主が MOD の人物）ときは、手配中は好感度の線、平時は普段どおり
"""
import os
import sys
import threading

from instantale_modloader import arrivals, llm, prices, ui, wanted
from instantale_modloader import state as loader_state

from . import attitude

USUAL_ENABLED = True   # 手配されていない土地でも、店主の応じ方で値段が動く
USUAL_PCT = 10         # 平時に負ける・ふっかけるときの率（%）
SELL = True            # 売値も動かす（切ると買値だけ）
NOTICE = True          # 売買を押したときに1行出す
JUDGE_DAYS = 30        # 同じ店主の応じ方を使い回す日数
FRIEND_AFFINITY = 20   # 判定が無いとき、手配中でもこの好感度以上なら動じない（20 は「嫌いではない」。336 の線と同じ）

LOG_BASENAME = "shopkeeper_attitude.log"
STATE_DIRNAME = "shopkeeper_attitude"
STORE_ATTR = "_instantale_shopkeeper_attitude"
MARK = "mod_shopkeeper_attitude"
SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
#: 店の選択肢の印（`340_inn_shop` が宿屋に足すのも同じ）と、MOD の人物の id の頭。
SHOP_CHOICE = "売買する"
MOD_NPC_PREFIX = "mod:"
#: 品の値段の鍵（ローダの関所が渡す。`買価` は店の品、`売価` は売りに出した品）。
BUY_KEY = "買価"
SELL_KEY = "売価"
#: 応じ方の頼み。記録の名前・待ちの目安・出力の上限。
MANAGER_NAME = "mod_shopkeeper_attitude"
ASK_TIMEOUT = 60.0
ASK_MAX_TOKENS = 500
#: 売買を押した後に判定を待つ上限（秒）。過ぎたら判定が無い扱いで開く（届いた判定は次から使う）。
WAIT_SECONDS = 180.0
#: 頼みに載せるプロフィールと人生の記録の上限（字）。
PROFILE_LIMIT = 300
LIFE_LOG_LIMIT = 1500
LIFE_LOG_MODULE = "scripts.llm.context_manager"


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None}
        setattr(sys, STORE_ATTR, store)
    return store


def life_log_text(app, character, limit):
    """ゲーム自身の作り方の人生の記録（`context_manager.get_life_log_text`）を、頼みに載せる文にする。読めなければ空。"""
    module = sys.modules.get(LIFE_LOG_MODULE)
    build = getattr(module, "get_life_log_text", None)
    try:
        value = build(app, character) if callable(build) else None
    except Exception:
        value = None
    if not value:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="shopkeeper attitude", mark=MARK)
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "341_shopkeeper_attitude"
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    lock = threading.Lock()
    #: 頼んでいる最中の店主（周回と主の id の鍵 → threading.Event）。
    judging = {}

    wanted.take_shop_pressure(owner, ctx)

    # -------------------------------------------------- いまの店
    def current_facility(app):
        location = getattr(getattr(app, "player", None), "location", None)
        if isinstance(location, (str, int)):
            facility, _node = ui.find_facility(ui.current_area(app), str(location))
            return facility
        return location

    def keeper_of(app):
        """いま居る施設の `(主の id, 主)`。主がいなければ `(None, None)`。"""
        owner_id = getattr(current_facility(app), "owner", None)
        if owner_id is None:
            return None, None
        return str(owner_id), ui.character_of(app, str(owner_id))

    def judgeable_shop(app):
        """判定を頼む店なら `(主の id, 主)`。店でない・主が MOD の人物なら None。"""
        choices = getattr(current_facility(app), "choices", None)
        if not isinstance(choices, dict) or SHOP_CHOICE not in choices:
            return None
        keeper_id, keeper = keeper_of(app)
        if keeper is None or keeper_id.startswith(MOD_NPC_PREFIX):
            return None
        return keeper_id, keeper

    def lawfulness_here(app):
        player = getattr(app, "player", None)
        area_id = ui.area_id_of(ui.current_area(app))
        return ui.lawfulness_of(ui.area_record(player, area_id)) if area_id else None

    def scene(app):
        """`(場面, 率)`。値段を動かさない場面なら `(None, 0.0)`。"""
        rate, _by = wanted.shop_pressure(app)
        if rate > 0:
            return attitude.WANTED, rate
        if USUAL_ENABLED and USUAL_PCT > 0:
            return attitude.USUAL, min(1.0, float(USUAL_PCT) / 100.0)
        return None, 0.0

    # -------------------------------------------------- 控え
    def stance_key(app, keeper_id):
        return "{}:{}".format(worlds.playthrough(app), keeper_id)

    def stored_entry(app, mode, keeper_id, keeper):
        """使える控えの判定。無ければ None。"""
        with worlds.lock:
            entry = worlds.load(worlds.playthrough(app)).get(keeper_id)
        if attitude.fresh(entry, mode, lawfulness_here(app), attitude.affinity_of(keeper),
                          ui.game_day(app), JUDGE_DAYS):
            return entry
        return None

    def store_entry(app, keeper_id, entry):
        """判定を控える。使い回す日数を過ぎた控えは消す（繰り返し遊んでも増えない）。"""
        day = ui.game_day(app)
        days = max(1, int(JUDGE_DAYS))
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            # 日数が読めない（ロードの途中など）ときは掃除しない。読めないまま比べると全部の控えを消す
            for key in (list(bucket) if isinstance(day, int) else ()):
                old = bucket[key]
                if not (isinstance(old, dict) and isinstance(old.get("day"), int) and 0 <= day - old["day"] < days):
                    del bucket[key]
            if entry is not None:
                bucket[keeper_id] = entry
            worlds.save(playthrough)

    # -------------------------------------------------- 頼む
    def ask(app, mode, keeper_id, keeper):
        """LLM に応じ方を頼み、控える。答えが無ければ None。ワーカースレッドで呼ぶ。"""
        player = getattr(app, "player", None)
        relationship = getattr(keeper, "relationship", None)
        row = relationship.get("player") if isinstance(relationship, dict) else None
        row = row if isinstance(row, dict) else {}
        affinity = attitude.affinity_of(keeper)
        lawfulness = lawfulness_here(app)
        text = attitude.prompt(
            mode, area=getattr(ui.current_area(app), "name", None), player=getattr(player, "name", None),
            player_profile=attitude.clean(getattr(player, "profile", None), PROFILE_LIMIT),
            lawfulness=lawfulness, weight=wanted.weight_of(lawfulness),
            keeper=getattr(keeper, "name", None),
            profile=attitude.clean(getattr(keeper, "profile", None), PROFILE_LIMIT),
            personality=getattr(keeper, "personality", None), speech=getattr(keeper, "speech_style", None),
            relation=row.get("relationship"), feeling=row.get("affinity_text"), affinity=affinity,
            count=row.get("conversation_count"), life_log=life_log_text(app, keeper, LIFE_LOG_LIMIT))
        structure = llm.create_structure(ctx, "ModShopkeeperAttitude", dict(attitude.FIELDS),
                                         label="shopkeeper attitude")
        data = llm.ask(ctx, MANAGER_NAME, [{"role": "user", "content": text}],
                       timeout=ASK_TIMEOUT, structure=structure, max_tokens=ASK_MAX_TOKENS,
                       label="shopkeeper attitude", write=write)
        parsed = attitude.parse(data)
        if parsed is None:
            write("no usable answer for {} ({!r})".format(keeper_id, data))
            return None
        value, reason, line = parsed
        entry = {"mode": mode, "stance": value, "lawfulness": lawfulness if mode == attitude.WANTED else None,
                 "affinity": affinity, "day": ui.game_day(app), "line": line, "told": False}
        store_entry(app, keeper_id, entry)
        write("judged {} ({}) [{}] -> {} ({})".format(
            getattr(keeper, "name", keeper_id), keeper_id, mode, attitude.NAMES[mode][value], reason))
        return entry

    def start(app, mode, keeper_id, keeper):
        """裏で応じ方を頼む。控えが使える・頼んでいる最中なら何もしない。"""
        key = stance_key(app, keeper_id)
        with lock:
            if key in judging or stored_entry(app, mode, keeper_id, keeper) is not None:
                return
            done = judging[key] = threading.Event()

        def run():
            try:
                ask(app, mode, keeper_id, keeper)
            except Exception:
                ctx.log_exc("shopkeeper attitude: cannot judge the shopkeeper")
            finally:
                with lock:
                    judging.pop(key, None)
                done.set()

        threading.Thread(target=run, name="shopkeeper_attitude", daemon=True).start()

    def await_entry(app, mode, keeper_id, keeper):
        """売買を押した時の判定。頼んでいる最中なら待ち、無ければその場で頼む（ワーカースレッド）。"""
        if stored_entry(app, mode, keeper_id, keeper) is not None:
            return "stored"
        with lock:
            done = judging.get(stance_key(app, keeper_id))
        # 待機表示は出さない。ここはゲーム自身の `execute` の中で、ゲームが旗を下ろして点を送っている（TECH.md §5.1.3）。
        # `busy_on` を重ねると下ろされた旗を覚え、`busy_off` がそれを書き戻す
        if done is not None:
            if not done.wait(WAIT_SECONDS):
                write("gave up waiting for {} after {}s".format(keeper_id, WAIT_SECONDS))
            return "waited"
        ask(app, mode, keeper_id, keeper)
        return "asked"

    def stance_here(app, mode):
        """いまの店の `(応じ方, 控えの判定)`。判定が無ければ決まった決め方で、控えは None。"""
        keeper_id, keeper = keeper_of(app)
        if keeper is not None:
            entry = stored_entry(app, mode, keeper_id, keeper)
            if entry is not None:
                return entry["stance"], entry
        return attitude.fallback(mode, attitude.affinity_of(keeper), FRIEND_AFFINITY), None

    # -------------------------------------------------- 値段
    def layer(item, key, price):
        """関所の段。店主の応じ方で率を掛ける。触らないなら None。"""
        app = ui.find_app()
        if app is None:
            return None
        mode, rate = scene(app)
        if mode is None:
            return None
        value, _entry = stance_here(app, mode)
        return attitude.price(BUY_KEY, SELL_KEY, key, price, rate, value, SELL)

    prices.install(ctx, write)
    prices.adjust(owner, layer, temporary=True, write=write)

    # -------------------------------------------------- 入口
    def arrive(call):
        """店に着いたら、選択肢が組み上がるのを待って、裏で応じ方を頼む（売買を押すまでに済ませる）。

        施設に着いた後はローダの窓口 `arrivals` から呼ばれる（TECH.md §3.3.21）。
        """
        try:
            app = ui.find_app()
            if app is not None and scene(app)[0] is not None:
                screen.when_idle(app, lambda: begin(app), proceed_on_timeout=True, tag="shopkeeper attitude")
        except Exception:
            ctx.log_exc("shopkeeper attitude: cannot plan the judgment")

    arrivals.on(owner, ctx, (arrivals.FACILITY,), after=arrive, write=write)

    def begin(app):
        mode, _rate = scene(app)
        shop = judgeable_shop(app) if mode is not None else None
        if shop is not None:
            start(app, mode, shop[0], shop[1])

    @ctx.wrap(SHOP_TARGET, required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        """売買を押した時（ワーカースレッド）。応じ方が決まるのを待ち、1行知らせてから開く。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None:
                tell(app)
        except Exception:
            ctx.log_exc("shopkeeper attitude: cannot tell the attitude")
        return orig(self, *args, **kwargs)

    def tell(app):
        mode, rate = scene(app)
        if mode is None:
            return
        shop = judgeable_shop(app)
        how = await_entry(app, mode, shop[0], shop[1]) if shop is not None else "-"
        value, entry = stance_here(app, mode)
        if entry is None:
            how = "fallback"
        keeper_id, keeper = keeper_of(app)
        name = getattr(keeper, "name", None) or "店主"
        pct = int(round(rate * 100))
        write("shop [{}] lawfulness {} keeper {} ({}) {} [{}] affinity {} -> {}".format(
            mode, lawfulness_here(app), name, keeper_id, attitude.NAMES[mode][value], how,
            attitude.affinity_of(keeper), attitude.rate_text(value, pct, SELL) or "normal prices"))
        line = ""
        if entry is not None and not entry.get("told") and entry.get("line"):
            # 平時の普段どおりは知らせない（店に入るたびに1文が出ると煩い）。手配中は動じないことも知らせる。
            if not (mode == attitude.USUAL and value == attitude.NEUTRAL):
                line = entry["line"]
            entry["told"] = True
            store_entry(app, keeper_id, entry)
        if NOTICE:
            text = attitude.notice(mode, value, pct, SELL, name, line)
            if text:
                screen.schedule(lambda: screen.say(app, text))

    ctx.log("shopkeeper attitude: installed (usual {} {}%, sell {}, judge for {} day(s), "
            "unafraid at affinity {} without a judgment)".format(
                "on" if USUAL_ENABLED else "off", USUAL_PCT, "on" if SELL else "off", JUDGE_DAYS,
                FRIEND_AFFINITY))
