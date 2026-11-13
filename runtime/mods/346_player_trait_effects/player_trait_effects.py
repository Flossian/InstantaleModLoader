# -*- coding: utf-8 -*-
"""主人公の特質を、能力値の補正・戦闘の効果・店の値段に効かせる（DOC.md）。

ゲームの特質は `{name, description, severity}` の文だけで、severity は作成時の才能点にしか使われない
（GAME.md §2.35）。会話や審判の頼みには載るが、数値には何も効いていない。

    変換     特質ごとに LLM へ1回だけ頼み（`rules.MANAGER`）、決まった効果の一覧に置き換えて
             `state\\trait_effects\\<世界×主人公>.json` に控える。特質の文が変わったら頼み直す
    能力値   窓口 `abilities` に答える（常に／依頼の最中／戦闘中／ボス戦／体力が半分以下。場面は `abilities.situations`）
    戦闘     窓口 `battle_effects` に答える（会心・軽減・不屈など。効かせるのはローダ。TECH.md §3.3.28）
    値段     窓口 `prices` に段を置く（店の買値・売値。セーブには焼かない）
    LLM      頼みに主人公の特質が載っているとき、その文の後ろに効果の一覧を足す（`wrap_outgoing`）
    表示     控えに人物欄の行（`display`）を書き、`121_ui_character_sheet` が特性の箱のマウスオーバーの窓に出す

変換は読み込みの後と人物欄の開閉の後に、背景のスレッドで行う（LLM を待つのでゲームのスレッドでは回さない）。
severity の扱い（目安として渡す・寄せる・渡さない）は設定 `SEVERITY_MODE`。どれでも、効果の重さと
severity の差をログに残す（扱いを決める材料。DOC.md）。
"""

import datetime
import os
import sys

from instantale_modloader import abilities, battle_effects, frames, jobs, llm, loads, prices, ui
from instantale_modloader.llm import wrap_outgoing
from instantale_modloader.state import WorldStore

from . import rules

# ---------------------------------------------------------------- 設定（mod.json と同じ既定値）
USE_ABILITIES = True
USE_BATTLE = True
USE_PRICES = True
NOTES_TO_AI = True
SEVERITY_MODE = "guide"
STAT_CAP = 10
PRICE_CAP = 30

# ---------------------------------------------------------------- 実装
STATE_DIRNAME = "trait_effects"
STORE_ATTR = "_instantale_trait_effects"
LOG_BASENAME = "trait_effects.log"
#: 変換の待ちの目安（秒）。過ぎても打ち切らない（`llm.ask`）。
CONVERT_TIMEOUT = 120
#: 控えの形の版。
FORMAT_VERSION = 1
#: 1回だけ書く行の覚えの上限（溢れたら忘れ直す）。
NOTED_LIMIT = 200


def language():
    """ゲームの表示言語。`scripts.languages.language`。読めなければ ja。"""
    module = sys.modules.get("scripts.languages")
    lang = getattr(module, "language", None) if module is not None else None
    return "en" if lang == "en" else "ja"


def severity_mode():
    return SEVERITY_MODE if SEVERITY_MODE in rules.SEVERITY_MODES else "guide"


def as_bucket(data):
    """読んだ控えを `{"version", "player", "traits", "display"}` に均す。`(控え, 直したか)`。"""
    if not isinstance(data, dict):
        return {"version": FORMAT_VERSION, "player": "", "traits": {}, "display": []}, False
    fixed = False
    if not isinstance(data.get("traits"), dict):
        data["traits"] = {}
        fixed = True
    if not isinstance(data.get("display"), list):
        data["display"] = []
        fixed = True
    data.setdefault("version", FORMAT_VERSION)
    data.setdefault("player", "")
    return data, fixed


def stored_bucket(bucket):
    """書くときの並び（差分を読めるように固定する）。"""
    return {"version": bucket.get("version", FORMAT_VERSION),
            "player": bucket.get("player", ""),
            "display": bucket.get("display") or [],
            "traits": bucket.get("traits") or {}}


def process_store(ctx):
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": WorldStore(ctx, STATE_DIRNAME, normalize=as_bucket, order=stored_bucket),
                 "worker": None, "noted": set(), "price_seen": None}
        setattr(sys, STORE_ATTR, store)
    return store


def apply(ctx):
    store = process_store(ctx)
    write = ctx.logger(LOG_BASENAME)

    def note_once(line):
        """同じ中身の行は1回だけ書く（戦闘の1手ごと・頼みごとに来る行を溢れさせない）。

        `dedup=True` の記録は直前の行としか比べないので、2種類の行が交互に来ると毎回書いていた（実機）。
        """
        noted = store["noted"]
        if line in noted:
            return
        if len(noted) >= NOTED_LIMIT:
            noted.clear()
        noted.add(line)
        write(line)
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "346_player_trait_effects"
    worlds = store["worlds"].rebind(ctx, write)
    lock = worlds.lock

    # ------------------------------------------------------------ 読む
    def player_of(app):
        player = frames.attr(app, "player", None) if app is not None else None
        return None if player is frames.MISSING else player

    def current_traits(player):
        return rules.read_traits(frames.attr(player, "traits", None))

    def records_for(app, player=None):
        """いまの主人公の特質の控え `[(名前, 控え)]`（特質の並び。変換がまだのものは入らない）。"""
        player = player_of(app) if player is None else player
        if player is None:
            return []
        fields = current_traits(player)
        if not fields:
            return []
        key = worlds.playthrough(app)
        with lock:
            records = worlds.load(key)["traits"]
            return [(f[0], dict(records[rules.fingerprint(f)])) for f in fields
                    if isinstance(records.get(rules.fingerprint(f)), dict)]

    # ------------------------------------------------------------ 控え
    def refresh_display(bucket, fields):
        """人物欄の行を、いまの特質の並びで組み直す。"""
        lang = language()
        rows = []
        for fields_one in fields:
            record = bucket["traits"].get(rules.fingerprint(fields_one))
            rows.append(rules.display_row(fields_one[0], record if isinstance(record, dict) else None, lang))
        bucket["display"] = rows

    def check(app, why):
        """変換がまだの特質があれば頼みを積む。いまの主人公に無い特質の控えは片付ける。"""
        player = player_of(app)
        if player is None:
            return
        fields = current_traits(player)
        key = worlds.playthrough(app)
        if not key:
            return
        wanted = {rules.fingerprint(f): f for f in fields}
        with lock:
            bucket = worlds.load(key)
            stale = [fp for fp in bucket["traits"] if fp not in wanted]
            for fp in stale:
                record = bucket["traits"].pop(fp)
                write("片付け: {}（いまの主人公の特質に無い）".format(record.get("name")))
            name = str(frames.attr(player, "name", "") or "")
            before = list(bucket["display"])
            refresh_display(bucket, fields)
            if stale or bucket.get("player") != name or bucket["display"] != before:
                bucket["player"] = name
                if fields or stale or os.path.exists(worlds.path(key)):
                    worlds.save(key, bucket)
            # 古い決まりの版（量の物差しが違う）で置き換えた特質も頼み直す。それまでは古い効果のまま効く。
            missing = [f for fp, f in wanted.items()
                       if not isinstance(bucket["traits"].get(fp), dict)
                       or bucket["traits"][fp].get("rules", 1) < rules.RULES_VERSION]
        if not missing:
            note_once("{}: 変換済み（特質 {} 件）".format(why, len(fields)))
            return
        job = {"world": key, "player": name,
               "profile": str(frames.attr(player, "profile", "") or ""),
               "traits": missing, "all": fields, "mode": severity_mode(), "why": why,
               "day": ui.game_day(app)}
        if worker.enqueue(job):
            write("変換を予約: {}（{} 件、契機 {}）".format(
                key, len(missing), why))

    def convert(job):
        """特質を効果に置き換えて控える（背景のスレッド）。"""
        traits, mode = job["traits"], job["mode"]
        messages = rules.build_messages(job["player"], job["profile"], traits, mode)
        raw = llm.ask(ctx, rules.MANAGER, messages, timeout=CONVERT_TIMEOUT,
                      label="trait effects", write=write)
        data = None
        if isinstance(raw, str):
            data = llm.parse_json(llm.strip_fence(raw))
        elif raw is not None:
            data = llm.as_dict(raw)
        parsed = rules.normalize(data, traits)
        if parsed is None:
            write("変換: 読めなかったので控えない（次の読み込みで頼み直す）: {!r}".format(
                (raw if isinstance(raw, str) else repr(raw))[:200]))
            return
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        changed = False
        with lock:
            bucket = worlds.load(job["world"])
            for fields in traits:
                name, _description, severity = fields
                answer = parsed.get(name)
                if answer is None:
                    write("変換: {} が返答に無かった（次の読み込みで頼み直す）".format(name))
                    continue
                effects = answer["effects"]
                raw_weight = rules.weight(effects)
                fitted = False
                if mode == "fit":
                    effects, fitted = rules.fit_to_severity(effects, severity)
                record = {"name": name, "severity": severity, "effects": effects,
                          "note": answer["note"], "weight": rules.weight(effects),
                          "mode": mode, "rules": rules.RULES_VERSION, "day": job.get("day"),
                          "updated": stamp}
                if fitted:
                    record["weight_before_fit"] = raw_weight
                bucket["traits"][rules.fingerprint(fields)] = record
                changed = True
                write("変換: {} severity={}（目安の重さ {}） 重さ={}{} -> [{}]{}".format(
                    name, severity, rules.target_weight(severity) if severity is not None else "-", raw_weight,
                    " → 寄せて {}".format(record["weight"]) if fitted else "",
                    rules.summary(record, "ja") or "効果なし",
                    " / {}".format(answer["note"]) if answer["note"] else ""))
            if changed:
                refresh_display(bucket, job["all"])
                worlds.save(job["world"], bucket)
        if changed and USE_PRICES:
            refresh_prices("converted")

    def job_key(job):
        return job["world"]

    def note_dropped(job):
        write("変換: 古い頼みを捨てた（{}）".format(job_key(job)))

    worker = store["worker"] = jobs.rebind(
        store["worker"]
        or jobs.Worker(ctx, convert, name="trait_effects", label="trait effects",
                       key=job_key, on_drop=note_dropped),
        ctx, convert, write, on_drop=note_dropped)

    # ------------------------------------------------------------ 能力値
    def answer(app, holder):
        """窓口 `abilities` が場面ごとに聞く。主人公にだけ答える。"""
        if not USE_ABILITIES:
            return None
        player = player_of(app)
        if player is None or holder is not player:
            return None
        records = records_for(app, player)
        if not records:
            return None
        active = abilities.situations(app, holder)
        totals = rules.ability_totals([record for _name, record in records], active, STAT_CAP)
        conditional = sorted(active - {"always"})
        if conditional:
            note_once("能力値: 条件 {} のもとで {}".format(conditional, totals))
        return totals or None

    abilities.declare(owner, answer, write=write)

    # ------------------------------------------------------------ 戦闘の効果
    def battle_answer(app, holder):
        """窓口 `battle_effects` が手ごとに聞く。主人公にだけ答える。"""
        if not USE_BATTLE:
            return None
        player = player_of(app)
        if player is None or holder is not player:
            return None
        records = records_for(app, player)
        if not records:
            return None
        totals = rules.battle_totals([record for _name, record in records])
        if totals:
            note_once("戦闘の効果: {}".format(totals))
        return totals or None

    battle_effects.declare(owner, battle_answer, write=write)

    # ------------------------------------------------------------ 値段
    def percents(app):
        records = [record for _name, record in records_for(app)]
        return (rules.price_percent(records, "buy", PRICE_CAP),
                rules.price_percent(records, "sell", PRICE_CAP))

    def price_layer(_item, key, price):
        """店の買値・売値に、主人公の特質の割合を掛ける。触らないなら None。"""
        if not USE_PRICES or not isinstance(price, (int, float)) or isinstance(price, bool):
            return None
        app = ui.find_app()
        if app is None:
            return None
        buy, sell = percents(app)
        percent = buy if key == prices.BUY else sell if key == prices.SELL else 0
        if not percent:
            return None
        return rules.priced(price, percent)

    def refresh_prices(why):
        try:
            app = ui.find_app()
            now = percents(app) if app is not None else None
            if now != store.get("price_seen"):
                store["price_seen"] = now
                prices.refresh("trait effects: " + why)
        except Exception:
            ctx.log_exc("trait effects: could not refresh prices")

    prices.install(ctx, write)
    prices.adjust(owner, price_layer, temporary=True, write=write)

    @ctx.wrap("__main__:ShoppingStartManagerRemake.execute", required=False, safe=True)
    def shop_start(orig, self, *args, **kwargs):
        """特質が変わった後に店へ入ったら、古い額が残らないように組み直す。"""
        if USE_PRICES:
            refresh_prices("shop")
        return orig(self, *args, **kwargs)

    # ------------------------------------------------------------ LLM へ足す文
    def rewrite(texts, site):
        """頼みに主人公の特質が載っていれば、その文の後ろに効果の一覧を足す。"""
        if not NOTES_TO_AI or not isinstance(texts, (list, tuple)):
            return None
        blob = "\n".join(text for text in texts if isinstance(text, str))
        if rules.NOTE_HEAD in blob or rules.CONVERTER_MARK in blob:
            return None
        app = ui.find_app()
        player = player_of(app)
        if player is None:
            return None
        fields = current_traits(player)
        if not fields:
            return None
        index = None
        for i, text in enumerate(texts):
            if not isinstance(text, str):
                continue
            for name, description, _severity in fields:
                head = description.split("\n")[0][:12]
                if name in text and (not head or head in text):
                    index = i
                    break
            if index is not None:
                break
        if index is None:
            return None
        block = rules.note_block(records_for(app, player))
        if not block:
            return None
        new_texts = list(texts)
        new_texts[index] = texts[index].rstrip("\n") + "\n\n" + block
        note_once("LLM: 効果の一覧を足した（{}、{}字）".format(site, len(block)))
        return new_texts

    wrap_outgoing(ctx, rewrite, label="trait effects")

    # ------------------------------------------------------------ 変換の契機
    def after_load(call):
        try:
            check(call.app, "読み込み")
            refresh_prices("load")
        except Exception:
            ctx.log_exc("trait effects: 読み込みの後の照合で失敗した")

    loads.on(owner, ctx, (loads.LOAD, loads.NEW_GAME), after=after_load, write=write)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility",
              required=False, safe=True)
    def character_sheet_toggled(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            check(ui.find_app(), "人物欄")
        except Exception:
            ctx.log_exc("trait effects: 人物欄の開閉で失敗した")
        return result

    # 起動直後は注入のやり直しと読み込みが重なり、読み込みの包みが効く前に世界が読み込まれることがある
    # （317_ と同じ）。注入が済んだ時点で主人公が居れば1回照合する。
    def after_inject(_dt=None):
        try:
            app = ui.find_app()
            if app is not None and player_of(app) is not None and ui.world_areas(app):
                check(app, "注入")
        except Exception:
            ctx.log_exc("trait effects: 注入の後の照合で失敗した")

    try:
        from kivy.clock import Clock
        Clock.schedule_once(after_inject, 1.0)
    except Exception:
        pass        # ゲームの外（検査）には Kivy が無い

    write("installed abilities={} battle={} prices={} notes={} severity={} cap={} price_cap={}".format(
        USE_ABILITIES, USE_BATTLE, USE_PRICES, NOTES_TO_AI, severity_mode(), STAT_CAP, PRICE_CAP))
    ctx.log("trait effects: installed (severity={})".format(severity_mode()))
