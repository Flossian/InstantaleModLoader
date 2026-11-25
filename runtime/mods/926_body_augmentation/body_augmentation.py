# -*- coding: utf-8 -*-
"""持ち物の品のうち体に適合するものを体に組み込む（人体改造）。主人公だけ。

    [治療院]      「人体改造」→ 開くと持ち物を見立てる（LLM 10品で1回・無料）→ 適合した品だけが候補に並ぶ
                  → 選んで施術を受ける / 札の改造を取り外す
    [裏の事務所]  「違法の改造」→ 同じ画面で違法の施術（効果が大きく、失敗しやすく、2つめの枠に入れられる）
    [MOD の一覧]  画面左下の MOD 用ボタンから、いつでも同じ画面を見られる（付け外しは施設の中だけ）

遊び方と数は DOC.md、規則（表と式）は `rules.py`、LLM へ送る文は `prompts.py`、画面は `window.py`。

##### ゲームのどこに触るか

- 施設の選択肢に1つ足す（窓口 `choices` の `offer`。ゲームのボタンは変えない）
- 押下はゲームと同じ経路（`process_choice`）で、自前のフェーズとして動かす
- 暦はゲームの `elapse_days` で進める。怪我はゲームの `body_parts` に足して `check_body_damage` を呼ぶ
- 能力値と戦闘の効果はローダの窓口（`abilities` / `battle_effects`）に答える。人物にもセーブにも書かない
- 侵食・傷が開く・自己修復のサイクルは、日数（窓口 `durations.on_days`）と依頼の終わり（窓口 `quest_ends`。
  放棄は同じ部品の `quest_ends.on_retire`）で進める
- 入っている改造は `state\\body_augmentation_play\\<世界>×<主人公>.json`、
  見立てと世界観の判定は `state\\body_augmentation_world\\<世界>.json`、
  世界ごとの設定は `state\\body_augmentation\\<世界>.json`（同梱の `tool.py` が書く）
- 違法の改造を持ったまま治療院で治療・施術・取り外しを受けると、その土地の手配度が下がる
- 施術・失敗・取り外しのたびに、主人公の `life_log` へ1行足す（改造そのものはセーブに書かない）
"""

import os
import random
import threading
import typing

from instantale_modloader import (abilities, battle_effects, choices, durations, equipment, frames, hudicons, ids,
                                  items, llm, loads, panels, quest_ends, shop_lineup, state as loader_state, ui)

from . import prompts, rules, window

# ---- 設定（既定値は mod.json の "settings" と一致させること。`tools/check_mods.py` が AST で突き合わせる）
THEME = "自動"                 # 見た目の種類。自動 / 機械 / 魔術 / 中立
TITLE = ""                     # 画面の題（空なら見た目の種類か判定の語）
ILLEGAL_WORD = ""              # 違法の呼び方（空なら見た目の種類か判定の語）
RETURN_ITEM = True             # 取り外した品を持ち物へ戻す
FEE_BASE = 200                 # 料金の基準
FEE_PER_VALUE = 20             # 品の値段 1 につき足す料金
ILLEGAL_FEE_PERCENT = 150      # 違法の施術の料金（%）
REMOVE_FEE_PERCENT = 50        # 取り外しの料金（施術に対する %）
DAYS_PERCENT = 100             # 入院の日数（%）
REMOVE_DAYS_PERCENT = 50       # 取り外しの日数（施術に対する %）
FAIL_PERCENT = 100             # 合法の施術の失敗の見込み（%）
ILLEGAL_FAIL = 30              # 違法の施術の失敗の見込み（%）
OVERLOAD_FAIL = 15             # 2つめの枠に入れるときに足す失敗の見込み（%）
DISCOVERY_LAW = 15             # 違法の改造が治療院で見つかったときに下がる手配度
SHOP_NOTE = True               # 治療院の棚に、体に組み込める素材や部品も並べさせる
SPECIAL_MIN_RARITY = "epic"    # 合法の施術に特殊効果が付くレア度

#: 世界ごとに変えられる設定（`tool.py` が `state\\body_augmentation\\<世界>.json` に書く）。
WORLD_SETTINGS = ("THEME", "TITLE", "ILLEGAL_WORD", "RETURN_ITEM", "FEE_BASE", "FEE_PER_VALUE",
                  "ILLEGAL_FEE_PERCENT", "REMOVE_FEE_PERCENT", "DAYS_PERCENT", "REMOVE_DAYS_PERCENT",
                  "FAIL_PERCENT", "ILLEGAL_FAIL", "OVERLOAD_FAIL", "DISCOVERY_LAW", "SHOP_NOTE", "SPECIAL_MIN_RARITY")

# ---- 文（ゲーム画面の文は常体）
INSTALL_TEXT = "{place}で{days}の入院をして施術を受けた。{part}に「{name}」を組み込んだ。"
INSTALL_ILLEGAL_TEXT = "{place}の奥の部屋で{days}を過ごし、{part}に「{name}」を組み込ませた。"
TRY_TEXT = "{place}で{days}の入院をして施術を受けた。"
TRY_ILLEGAL_TEXT = "{place}の奥の部屋で{days}を過ごした。"
FAIL_TEXT = "施術はうまくいかなかった。{part}に怪我を負い（怪我 +{amount}）、{item}は使い物にならなくなった。"
FAIL_OVERLOAD_TEXT = "先に入れていた「{name}」も巻き込まれて壊れた。"
REMOVE_TEXT = "{place}で{days}の入院をして、{part}の「{name}」を取り外した。"
RESTORE_TEXT = "失っていた{part}の働きを、「{name}」が取り戻した。"
RAMPAGE_TEXT = "暴走の負荷で{part}が傷んだ。"
RETURN_TEXT = "{item}は手元に戻った。"
NO_RETURN_TEXT = "取り外した{item}は、もう使えない。"
PAID_TEXT = "代金{fee}Gを支払った。"
DISCOVERED_TEXT = "医師は{illegal}に気付き、役人へ届け出た。この土地での立場が悪くなった。"
NO_GOLD_TEXT = "代金が足りない。"
NO_ROOM_TEXT = "所持品に空きが無い。"
GONE_TEXT = "選んだアイテムが持ち物に見当たらない。"
EQUIPPED_TEXT = "身に着けているアイテムは組み込めない。"
RECORD_MARK = "〈施術の記録〉"
RECORD_INSTALL = "{player}は{area}で{part}に{name}を組み込んだ。"
RECORD_FAIL = "{player}は{area}で{part}への施術に失敗し、傷を負った。"
RECORD_REMOVE = "{player}は{area}で{part}の{name}を取り外した。"
AREA_FALLBACK = "この土地"
PLACE_FALLBACK = "施設"
PLAYER_FALLBACK = "主人公"

# ---- コード側の決まり
OWNER = "926_body_augmentation"
LOG_BASENAME = "body_augmentation.log"
SETTINGS_DIRNAME = "body_augmentation"
PLAY_DIRNAME = "body_augmentation_play"
WORLD_DIRNAME = "body_augmentation_world"
STORE_ATTR = "_instantale_body_augmentation"
MARK = "mod_body_augmentation"
ICON_KEY = "body_augmentation"
ICON_ORDER = 60
#: 一覧の行の絵（普段・乗せた時・押した時）。`tools\draw_hud_icons.py` が描き出す。
ICON_FILES = ("icon_augment.png", "icon_augment_hover.png", "icon_augment_click.png")

MEDICAL_TYPE = "medical_facility"
UNDERWORLD_TYPE = "underworld_office"
EXIT_CLS = "MovePhaseManager"
TREATMENT_TARGET = "__main__:MedicalTreatmentManager.medical_treatment"
LLM_TIMEOUT = 60
HISTORY_LIMIT = 50

money = ui.money
coins = ui.rewrite_coins


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5。ローダの `state.process_store`）。"""
    return loader_state.process_store(STORE_ATTR, {"play": None, "world": None, "rng": random.Random(),
                                                   "pending": set(), "theme_pending": set(), "window": None})


def _world_bucket():
    return {"theme": None, "appraisals": {}}


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["play"] is None:
        store["play"] = loader_state.WorldStore(
            ctx, PLAY_DIRNAME, default=rules.new_bucket, normalize=rules.normalize_bucket,
            order=rules.order_bucket, write=write)
        store["world"] = loader_state.WorldStore(ctx, WORLD_DIRNAME, default=_world_bucket, write=write)
    play = store["play"].rebind(ctx, write)
    worlds = store["world"].rebind(ctx, write)
    # 世界ごとの設定の控えはローダの `state.WorldSettings` で読む（読むだけ。フォルダは作らない）
    world_settings = loader_state.WorldSettings(ctx, SETTINGS_DIRNAME, WORLD_SETTINGS, globals(), write=write)
    screen = ui.Screen(ctx, write, tag="augment", mark=MARK)
    save_soon = ui.saver(ctx, write, "augment")
    state = {"owner": None}

    # ------------------------------------------------------------ 設定
    def setting(app, name):
        """世界ごとの設定（`tool.py` が書いた差分）があればそれ、無ければ一括設定。

        一括設定はその場のモジュールの値を読む（試しの道具 `m` で替えた値も効く）ので、
        `WorldSettings` が作ったときに写した値は使わず、控えの読みと型の照合（`accept`）だけを借りる。
        """
        base = globals()[name]
        record = world_settings.record(world_settings.world(app))
        ok, value = world_settings.accept(record.get(name), base)
        return value if ok else base

    def config(app):
        return rules.Config(
            fee_base=setting(app, "FEE_BASE"), fee_per_value=setting(app, "FEE_PER_VALUE"),
            illegal_fee_percent=setting(app, "ILLEGAL_FEE_PERCENT"),
            remove_fee_percent=setting(app, "REMOVE_FEE_PERCENT"),
            days_percent=setting(app, "DAYS_PERCENT"), remove_days_percent=setting(app, "REMOVE_DAYS_PERCENT"),
            fail_percent=setting(app, "FAIL_PERCENT"), illegal_fail=setting(app, "ILLEGAL_FAIL"),
            overload_fail=setting(app, "OVERLOAD_FAIL"),
            special_min=rules.rarity_index(setting(app, "SPECIAL_MIN_RARITY")))

    # ------------------------------------------------------------ 世界を読む
    def player_of(app):
        return getattr(app, "player", None) if app is not None else None

    def area_name(app):
        # 名前は切らない（記録の文に入る）。読み方はローダの `ui.area_name`
        return ui.area_name(app, ui.current_area(app), AREA_FALLBACK, limit=None)

    def place_name(app):
        return ui.facility_name(app, ui.current_facility(app)) or PLACE_FALLBACK

    def facility_type(app):
        return ui.facility_type_of(ui.current_facility(app))

    def mode_here(app):
        """付け外しができる施設なら `"legal"` / `"illegal"`、ほかは None（見るだけ）。"""
        here = facility_type(app)
        if here == MEDICAL_TYPE:
            return "legal"
        if here == UNDERWORLD_TYPE:
            return "illegal"
        return None

    def ledger(app):
        key = play.playthrough(app)
        return key, play.load(key)

    def world_cache(app):
        key = loader_state.world_key(app)
        return key, worlds.load(key)

    def scores(app):
        try:
            found = abilities.scores(app, player_of(app))
        except Exception:
            found = None
        return found if isinstance(found, dict) else {}

    def injuries(app):
        """部位ごとの `(injury, stage)`。"""
        parts = getattr(player_of(app), "body_parts", None)
        found = {}
        if isinstance(parts, dict):
            for part in rules.PARTS:
                row = parts.get(part)
                if isinstance(row, dict):
                    injury = row.get("injury")
                    found[part] = (injury if frames.is_number(injury) else 0, row.get("stage") or "")
        return found

    def losses(app):
        """怪我で下がっている能力値 `{能力値: 量}`（元の値 − いまの素の値。下がっていない能力値は載せない）。"""
        player = player_of(app)
        original = getattr(player, "original_ability_scores", None)
        try:
            current = abilities.raw(player, "ability_scores")
        except Exception:
            current = None
        if not isinstance(original, dict) or not isinstance(current, dict):
            return {}
        found = {}
        for key in rules.ABILITIES:
            a, b = original.get(key), current.get(key)
            if frames.is_number(a) and frames.is_number(b) and a > b:
                found[key] = a - b
        return found

    def set_injuries(app, changes, check=True, force=False):
        """部位の怪我を書き、ゲームの段階の決め直しを呼ぶ。書けた部位を返す。

        `force` なら書いた部位が無くても決め直す（暴走で書いたまま決め直していない怪我の分）。
        """
        player = player_of(app)
        parts = getattr(player, "body_parts", None)
        done = []
        if not isinstance(parts, dict):
            return done
        for part, value in changes.items():
            row = parts.get(part)
            if isinstance(row, dict):
                row["injury"] = value
                done.append(part)
        run = getattr(app, "check_body_damage", None)
        if (done or force) and check and callable(run):
            try:
                run(player)
            except Exception:
                ctx.log_exc("augment: check_body_damage failed")
        return done

    def worn_items(app):
        """身に着けている品（所持品の欄には居ない）。装備欄の MOD が答えなければ空。

        素のゲームで装備中の品が欄に居るかは確かめていないので、装備欄の MOD が居ないときは欄の品として数える。
        """
        try:
            gear = equipment.gear(app, player_of(app))
        except Exception:
            gear = None
        return [item for _region, item in gear or []]

    def is_equipped(app, key, item):
        """身に着けている品か（候補に出さない・施術しない）。

        装備欄の MOD の品と、素のゲームの `equipments` の品（実行時は品の実体、セーブの形は id の文字列。
        GAME.md §2.13.3）。素のゲームの装備中の品は所持品の欄にも居るので、欄の空きの計算（`worn_items`）とは分ける。
        """
        found = list(worn_items(app))
        gear = getattr(player_of(app), "equipments", None)
        if isinstance(gear, dict):
            found += [value for value in gear.values() if value is not None]
        return any(value is item or (isinstance(value, str) and value == str(key)) for value in found)

    def return_spot(app, record):
        """取り外した品を戻す所持品の欄のマス。戻さない設定・戻す品が無いときは `True`、空きが無ければ None。"""
        data = record.get("item") if isinstance(record, dict) else None
        if not setting(app, "RETURN_ITEM") or not isinstance(data, dict):
            return True
        inv = items.inventory_of(player_of(app))
        if not isinstance(inv, dict):
            return None
        worn = worn_items(app)
        placed = [(getattr(item, "grid_pos", None), rules.item_size(item))
                  for item in list(inv.values()) if not any(item is w for w in worn)]
        return rules.free_spot(placed, rules.item_size(data))

    def candidates(app):
        """見立てに回す持ち物の品 `[(鍵, 辞書, 品の情報, 絵)]`。持ち物の並び。身に着けている品は外す。"""
        inv = items.inventory_of(player_of(app))
        found = []
        if not isinstance(inv, dict):
            return found
        for key, item in list(inv.items()):
            if getattr(item, "item_type", None) not in rules.ITEM_TYPES or is_equipped(app, key, item):
                continue
            data = items.to_dict(item)
            info = rules.item_info(data)
            if info is None:
                continue
            found.append((str(key), data, info, getattr(item, "image_src", None)))
        return found

    # ------------------------------------------------------------ 見た目
    def theme_of(app):
        """`(見た目の種類, 言葉)`。自動で判定がまだなら中立（判定は画面を開いたときに頼む）。"""
        choice = setting(app, "THEME")
        detected = None
        try:
            _key, cache = world_cache(app)
            detected = cache.get("theme") if isinstance(cache, dict) else None
        except Exception:
            detected = None
        if choice in rules.THEME_CHOICES:
            theme = rules.THEME_CHOICES[choice]
        else:
            theme = detected.get("theme") if isinstance(detected, dict) else rules.NEUTRAL
        return theme, rules.words_for(theme, detected, setting(app, "TITLE"), setting(app, "ILLEGAL_WORD"))

    def needs_theme(app):
        if setting(app, "THEME") in rules.THEME_CHOICES:
            return False
        _key, cache = world_cache(app)
        return not isinstance(cache.get("theme"), dict)

    def detect_theme(app):
        """世界観の判定を別スレッドで頼む。済んだら画面を描き直す。答えが無ければ保存せず、次に開いたときに頼み直す。"""
        key = loader_state.world_key(app)
        if key in store["theme_pending"] or not needs_theme(app):
            return
        overview = ui.world_overview(app)
        structure = llm.create_structure(ctx, "BodyAugmentTheme",
                                         {name: (str, ...) for name in prompts.THEME_FIELDS}, label="augment")
        # 印はスレッドを起こす直前に立てる（準備で送出すると、下ろす finally に届かず頼み直せなくなる）
        store["theme_pending"].add(key)

        def run():
            result = None
            try:
                raw = llm.ask(ctx, prompts.THEME_MANAGER, prompts.theme_message(overview),
                              timeout=LLM_TIMEOUT, structure=structure, label="augment", write=write)
                result = rules.normalize_theme(llm.as_dict(raw) if raw is not None else None)
            except Exception:
                ctx.log_exc("augment: theme request failed")
            finally:
                store["theme_pending"].discard(key)
            if result is None:
                write("theme: no answer for {!r}".format(key))
                return
            with worlds.lock:
                cache = worlds.load(key)
                cache["theme"] = result
                worlds.save(key)
            write("theme: {!r} -> {}".format(key, result))
            later(refresh_window)
            # 施設のボタンの題も新しい語にする。塗り直さないと、所持品やクラフトの画面を閉じたときに
            # ゲームが入った時の選択肢（判定の前の題）を戻す（VERIFICATION 3.134）
            later(lambda: choices.repaint(ui.find_app()))

        threading.Thread(target=run, name="augment_theme", daemon=True).start()

    # ------------------------------------------------------------ 見立て
    def appraisal_of(app, info):
        _key, cache = world_cache(app)
        return rules.current_appraisal((cache.get("appraisals") or {}).get(rules.fingerprint(info)))

    def appraisal_structure():
        item = llm.create_structure(ctx, "BodyAugmentAppraisal",
                                    {name: (str, ...) for name in prompts.APPRAISE_FIELDS}, label="augment")
        if item is None:
            return None
        return llm.create_structure(ctx, "BodyAugmentAppraisals",
                                    {prompts.APPRAISE_LIST: (typing.List[item], ...)}, label="augment")

    def appraise_all(app):
        """持ち物のうち、まだ見立てていない品をまとめて見立てる（別スレッド・10件ずつ）。済むたびに画面を描き直す。

        同じ名前と説明の品は1つにまとめて頼む。答えに無かった品は失敗の扱い（次に画面を開いたときに頼み直す）。
        """
        todo = {}
        for _key, _data, info, _image in candidates(app):
            print_key = rules.fingerprint(info)
            if print_key in store["pending"] or print_key in todo or appraisal_of(app, info) is not None:
                continue
            todo[print_key] = info
        if not todo:
            return
        key = loader_state.world_key(app)
        overview = ui.world_overview(app)
        illegal = view.get("mode") == "illegal"
        structure = appraisal_structure()
        batches = rules.chunks(list(todo.items()))
        # 印はスレッドを起こす直前に立てる（detect_theme と同じ）
        store["pending"].update(todo)
        write("appraise: {} items in {} requests".format(len(todo), len(batches)))

        def run():
            for batch in batches:
                found = {}
                try:
                    message = prompts.appraise_message([info for _p, info in batch], overview, illegal)
                    raw = llm.ask(ctx, prompts.APPRAISE_MANAGER, message, timeout=LLM_TIMEOUT,
                                  structure=structure, label="augment", write=write)
                    found = rules.normalize_batch(llm.as_dict(raw) if raw is not None else None, len(batch))
                except Exception:
                    ctx.log_exc("augment: appraisal failed")
                with worlds.lock:
                    cache = worlds.load(key)
                    for index, (print_key, info) in enumerate(batch):
                        result = found.get(index)
                        if result is None:
                            view["failed"].add(print_key)
                            write("appraise: no usable answer for {!r}".format(info.get("name")))
                        else:
                            cache.setdefault("appraisals", {})[print_key] = result
                            write("appraise: {!r} -> {}".format(info.get("name"), result))
                    if found:
                        worlds.save(key)
                for print_key, _info in batch:
                    store["pending"].discard(print_key)
                later(refresh_window)

        threading.Thread(target=run, name="augment_appraise", daemon=True).start()

    # ------------------------------------------------------------ 画面
    #: 画面の状態（開いている間だけ。選んでいるもの・付け外しのできる施設か）。
    view = {"mode": None, "select": None, "failed": set()}

    def later(fn):
        try:
            from kivy.clock import Clock
            Clock.schedule_once(lambda _dt: screen.guarded(fn), 0)
        except Exception:
            fn()

    # 画面は `store["window"]` に1枚だけ持つ（ローダの `panels.Toggle`。開き直すときは前の画面を閉じる）
    shown_window = panels.Toggle(store, key="window", log_exc=lambda text: ctx.log_exc("augment: " + text))

    def opener_for(mode):
        """`mode` は `"legal"` / `"illegal"`（施設の中）か None（見るだけ）。"""
        def opener(app, on_dismiss):
            view.update({"mode": mode, "select": None, "failed": set()})
            if needs_theme(app):
                detect_theme(app)
            if mode is not None:
                # 見立ては付け外しのできる施設で開いたときだけ頼む（見るだけのときは問い合わせない）
                appraise_all(app)
            model = build_model(app)
            shown = window.AugmentWindow(app, model, on_select=select, on_action=act,
                                         on_dismiss=on_dismiss, write=write)
            write("open: mode={} augments={}".format(mode, len(model["records"])))
            return shown
        return opener

    def open_view(app, mode):
        """画面を開く（残っている画面は先に閉じる）。"""
        shown_window.open(app, opener_for(mode))

    def close_view():
        shown_window.close()

    def refresh_window():
        current = store.get("window")
        app = ui.find_app()
        if current is None or app is None:
            return
        current.render(build_model(app))

    def select(kind, payload):
        app = ui.find_app()
        if app is None:
            return
        view["select"] = (kind, payload)
        refresh_window()

    def build_model(app):
        """画面に渡す形（`window.py` が描く）。"""
        theme, words = theme_of(app)
        with play.lock:
            _key, bucket = ledger(app)
            records = rules.records_of(bucket)
        hurt = injuries(app)
        mode = view.get("mode")
        selected = view.get("select")
        rows = candidates(app)
        cfg = config(app)
        detail = None
        target = None
        if selected and selected[0] == "slot":
            part, slot = selected[1]
            record = next((r for p, s, r in records if p == part and s == slot), None)
            if record is not None:
                detail = record_detail(app, record, bucket, words, mode, cfg)
        elif selected and selected[0] == "item":
            found = next((row for row in rows if row[0] == selected[1]), None)
            if found is not None:
                detail, target = item_detail(app, found, bucket, words, mode, cfg)
        parts = {}
        # 侵食・自己修復のある部位は、見出しに次の刻みまでの日数を出す
        wear = rules.wear_marks([r for _p, _s, r in records])
        for part in rules.PARTS:
            slots = []
            for slot in range(rules.SLOTS):
                record = (bucket["augments"].get(part) or [None] * rules.SLOTS)[slot]
                if record is None:
                    slots.append(None)
                    continue
                info = record.get("item_info") or {}
                slots.append({"name": record.get("name") or "", "illegal": bool(record.get("illegal")),
                              "image": (record.get("item") or {}).get("image_src"), "rarity": info.get("rarity"),
                              "forbidden": bool(record.get("forbidden")),
                              "pieces": rules.effect_pieces(record, compact=True),
                              "selected": selected == ("slot", [part, slot]) or selected == ("slot", (part, slot)),
                              # マウスを乗せたときの説明の窓（効果と、改造と元のアイテムの説明）
                              "tip": {"tag": (rules.FORBIDDEN_WORD if record.get("forbidden") else
                                              words["illegal"] if record.get("illegal") else ""),
                                      "item": "元のアイテム {}（{}）".format(info.get("name") or "",
                                                                         info.get("rarity") or ""),
                                      "effects": rules.effect_pieces(record, costs=False),
                                      "costs": rules.effect_pieces(record, costs=True),
                                      "description": record.get("description") or "",
                                      "item_description": info.get("description") or ""}})
            injury, _stage = hurt.get(part, (0, ""))
            # 段階（stage）は `check_body_damage` が走るまで古いまま残る（怪我 9 で hurt の主人公がいる）ので、
            # ゲームと同じしきい値（10。GAME.md §2.19.1）で怪我の値から見る
            state_text = ("負傷 {}".format(int(injury)), "hurt") if injury >= rules.HURT_FROM else ("正常", None)
            parts[part] = {"name": rules.PART_NAMES[part], "slots": slots, "state": state_text,
                           "wear": wear.get(part, []),
                           "target": bool(target) and target[0] == part,
                           "target_slot": target[1] if target and target[0] == part else None,
                           "hurt": state_text[1] == "hurt",
                           "illegal": any(s and s["illegal"] for s in slots),
                           "augmented": any(slots)}
        # 候補は見立てで適合した品だけ。見立て中・適合しない・見立てに失敗した品は数だけ見せる
        cands = []
        tally = {"pending": 0, "refused": 0, "failed": 0, "unknown": 0}
        gold = ui.gold_of(app)
        player_scores = scores(app)
        for key, _data, info, image in rows:
            appraisal = appraisal_of(app, info)
            print_key = rules.fingerprint(info)
            if appraisal is None:
                tally["pending" if print_key in store["pending"] else
                      "failed" if print_key in view["failed"] else "unknown"] += 1
                continue
            if not appraisal.get("usable"):
                tally["refused"] += 1
                continue
            forbidden = bool(appraisal.get("restricted"))
            cands.append({"key": key, "image": image, "name": appraisal["name"], "rarity": info.get("rarity"),
                          "label": rules.PART_NAMES[appraisal["part"]] + (
                              "・" + rules.FORBIDDEN_WORD if forbidden else ""),
                          "kind": "forbidden" if forbidden else "ok", "forbidden": forbidden,
                          "selected": selected == ("item", key),
                          "tip": candidate_tip(appraisal, info, bucket, words, mode, cfg, player_scores, gold)})
        count_illegal = sum(1 for _p, _s, r in records if r.get("illegal"))
        counts = [("{} {}".format(words["augment"], len(records)), None)]
        if count_illegal:
            counts.append(("うち{} {}".format(words["illegal"], count_illegal), "illegal"))
        # 画面が HUD の所持金の欄を覆うので、施術の料金と見比べられるよう見出しに所持金を出す（現在地の行の左）
        gold_text = coins("所持金 {}G".format(money(gold))) if gold is not None else ""
        if mode is not None:
            note = "現在地 {}".format(place_name(app))
        else:
            note = "付け外しは治療院（{}の施術は裏の事務所）で行える".format(words["illegal"])
        stats, battle = rules.total_pieces([r for _p, _s, r in records], losses(app))
        hints = rules.total_hints([r for _p, _s, r in records])
        return {"theme": theme, "words": words, "player": ui.player_name(app),
                "counts": counts, "gold": gold_text, "note": note, "mode": mode, "parts": parts, "candidates": cands,
                "candidate_note": rules.candidate_note(tally, mode is not None, hint=bool(cands)),
                "candidate_empty": rules.candidate_empty(tally, mode is not None),
                "detail": detail, "totals": {"stats": stats, "battle": battle, "hints": hints}, "records": records}

    def candidate_tip(appraisal, info, bucket, words, mode, cfg, player_scores, gold):
        """候補にマウスを乗せたときの説明の窓（部位の札と同じ形）。方法が2つなら方法ごとの行。"""
        plans = [rules.install_plan(appraisal, info, bucket, mode, method, cfg, player_scores, gold)
                 for method in rules.methods_for(appraisal, mode)]
        if len(plans) > 1:
            rows = [(rules.METHOD_NAMES[p["method"]], p["effects"] + p["costs"]) for p in plans]
        else:
            rows = [("効果", plans[0]["effects"]), ("代償", plans[0]["costs"])]
        return {"tag": rules.FORBIDDEN_WORD if appraisal.get("restricted") else "",
                "item": "元のアイテム {}（{}）　{}・{}".format(
                    info.get("name") or "", info.get("rarity") or "", rules.PART_NAMES[appraisal["part"]],
                    rules.FINISH_NAMES.get(appraisal.get("finish"), "")),
                "rows": rows, "description": appraisal.get("description") or "",
                "item_description": info.get("description") or ""}

    def record_detail(app, record, bucket, words, mode, cfg):
        part = record["part"]
        # 題の横は部位と仕上げだけ（元のアイテムは札にマウスを乗せたときの説明に出る）
        sub = [rules.PART_NAMES[part], rules.FINISH_NAMES.get(record.get("finish"), "")]
        rows = [("効果", rules.effect_pieces(record, costs=False)),
                ("代償", rules.effect_pieces(record, costs=True))]
        button = None
        notes = []
        if mode is not None:
            underworld = mode == "illegal"
            fee = rules.remove_fee(record, cfg, at_underworld=underworld)
            days = rules.remove_days(record, cfg, at_underworld=underworld)
            # 取り外す改造が合法でも、別の部位に違法の改造があれば見つかる（remove の had_illegal と同じ条件）
            if bucket and rules.has_illegal(bucket) and not underworld:
                notes.append(("取り外すと咎められ、この土地の手配度が {} 下がる".format(setting(app, "DISCOVERY_LAW")),
                              rules.MINUS))
            notes.append(("アイテムは手元に戻る" if setting(app, "RETURN_ITEM") else "アイテムは戻らない", rules.PLAIN))
            gold = ui.gold_of(app)
            enough = gold is None or gold >= fee
            room = return_spot(app, record) is not None
            button = {"text": "取り外す", "enabled": enough and room, "action": "remove",
                      "payload": [part, record["slot"]],
                      "lines": [coins("{}G・{}".format(money(fee), rules.days_words(days)))]}
            if not room:
                notes.insert(0, (NO_ROOM_TEXT, rules.MINUS))
            if not enough:
                notes.insert(0, (NO_GOLD_TEXT, rules.MINUS))
        return {"image": (record.get("item") or {}).get("image_src"), "name": record.get("name") or "",
                "rarity": (record.get("item_info") or {}).get("rarity"), "forbidden": bool(record.get("forbidden")),
                "tag": rules.FORBIDDEN_WORD if record.get("forbidden") else "",
                "illegal": bool(record.get("illegal")), "sub": sub,
                "description": record.get("description") or "", "rows": rows, "buttons": [button] if button else [],
                "notes": notes}

    def item_detail(app, row, bucket, words, mode, cfg):
        """持ち物の品の詳細と、光らせる部位。"""
        key, _data, info, image = row
        appraisal = appraisal_of(app, info)
        sub = ["{}（{}）".format(rules.FINISH_NAMES.get(appraisal.get("finish"), "")
                                if appraisal and appraisal.get("usable") else info["item_type"], info["rarity"])]
        base = {"image": image, "name": info["name"], "rarity": info.get("rarity"), "illegal": False, "sub": sub,
                "description": info.get("description") or "", "rows": [], "button": None, "notes": []}
        if appraisal is None or not appraisal.get("usable"):       # 候補には適合したアイテムしか並ばない
            return base, None
        part = appraisal["part"]
        forbidden = bool(appraisal.get("restricted"))
        # 禁制のアイテムは、名前の横の札を「禁制」にして示す（副題に足すと折り返して題が枠からはみ出す）
        base.update({"name": appraisal["name"], "illegal": forbidden, "forbidden": forbidden,
                     "tag": rules.FORBIDDEN_WORD if forbidden else "",
                     "description": appraisal.get("description") or info.get("description") or ""})
        base["sub"] = [rules.PART_NAMES[part], rules.FINISH_NAMES.get(appraisal.get("finish"), "")]
        gold = ui.gold_of(app)
        player_scores = scores(app)
        # 裏の事務所の正規のアイテムは、通常施術と限界施術の2つを並べる。ほかは1つ
        plans = [rules.install_plan(appraisal, info, bucket, mode, method, cfg, player_scores, gold)
                 for method in rules.methods_for(appraisal, mode)]
        two = len(plans) > 1
        first = plans[0]
        current = injuries(app).get(part, (0, ""))[0]

        def button_lines(p):
            """施術のボタンの小さい行（`成功率70%・2,700G・4日`、`失敗時 怪我 +60`）。"""
            return [coins("成功率{}・{}G・{}".format(rules.percent_words(rules.success_percent(p["fail"])),
                                                   money(p["fee"]), rules.days_words(p["days"]))),
                    "失敗時 " + rules.fail_injury_words(rules.fail_injury(p["illegal"]), current)]

        if not two:
            base["rows"] = [("効果", first["effects"]), ("代償", first["costs"])]
        else:
            # 方法ごとに1行（効果と代償。料金・期間・成功率・失敗の怪我はボタンの中）
            for p in plans:
                base["rows"].append((rules.METHOD_NAMES[p["method"]], p["effects"] + p["costs"]))
        if not first["illegal"] and injuries(app).get(part, (0, ""))[0] >= rules.LOST_FROM:
            base["notes"].append(("{}で、失った{}の働きを取り戻す".format(rules.METHOD_NAMES[rules.NORMAL], rules.PART_NAMES[part])
                                  if two else "失った{}の働きを取り戻す".format(rules.PART_NAMES[part]), rules.PLUS))
        if mode is None:
            return base, (part, first["slot"])
        if not first["proc"]["allowed"]:
            base["notes"].append(("{}のアイテムは治療院では扱えない（裏の事務所で受けられる）".format(
                rules.FORBIDDEN_WORD), rules.MINUS))
            base["buttons"] = [{"text": words["install"], "enabled": False, "action": "install",
                                "payload": [key, rules.NORMAL]}]
            return base, (part, first["slot"])
        # 失敗したときのこと（怪我の量はボタンの中に出すので、ここは1行）
        stacked = [p for p in plans if p["slot"] == 1]
        base["notes"].append((rules.fail_note(part) + ("（{}は先に入れた改造も壊れる）".format(
            rules.METHOD_NAMES[stacked[0]["method"]]) if stacked and two else
            "（先に入れた改造も壊れる）" if stacked else ""), rules.PLAIN))
        last = plans[-1]
        if last["slot"] is None:
            base["notes"].append((("{}の枠は2つとも埋まっている" if last["illegal"]
                                   else "{}の枠が埋まっている。先に取り外す").format(rules.PART_NAMES[part]), rules.MINUS))
        elif first["slot"] is None:
            base["notes"].append(("{}の枠が埋まっている。{}は先に取り外す".format(
                rules.PART_NAMES[part], rules.METHOD_NAMES[rules.NORMAL]), rules.MINUS))
        if plans[-1]["illegal"] and plans[-1]["slot"] == 1:
            base["notes"].append(("{}は2つめの枠に重ねて入れる".format(rules.METHOD_NAMES[rules.OVERLIMIT])
                                  if two else "2つめの枠に重ねて入れる", rules.MINUS))
        if not all(p["enough"] for p in plans):
            base["notes"].append((NO_GOLD_TEXT, rules.MINUS))
        if bucket and rules.has_illegal(bucket) and mode == "legal":
            base["notes"].append(("施術を受けると、{}が見つかる".format(
                rules.underworld_label(words)), rules.MINUS))
        base["buttons"] = [{"text": rules.METHOD_NAMES[p["method"]] if two else words["install"],
                            "enabled": p["slot"] is not None and p["enough"], "action": "install",
                            "payload": [key, p["method"]], "danger": p["illegal"], "lines": button_lines(p)}
                           for p in plans]
        base["danger_rows"] = [rules.METHOD_NAMES[p["method"]] for p in plans if two and p["illegal"]]
        return base, (part, first["slot"])

    # ------------------------------------------------------------ 施術と取り外し
    def act(action, payload):
        """画面のボタン。画面を閉じ、ゲームと同じ経路で自前のフェーズを動かす。"""
        app = ui.find_app()
        if app is None or view.get("mode") is None:
            return
        mode = view["mode"]
        close_view()
        label = "施術を受ける" if action == "install" else "取り外す"
        write("pressed {} {!r} ({})".format(action, payload, mode))
        # 使い捨てのフェーズはローダの `Screen.run_phase` が作る（`PhaseSpec` に載せない。セーブに残さない）。
        # 投げたら記録して施設のボタンを組み直す
        screen.run_phase(app, label, run_action, action, payload, mode, label=action,
                         on_error=choices.repaint)

    def pay(app, fee):
        if fee <= 0:
            return True
        gold = ui.gold_of(app)
        if gold is not None and gold < fee:
            return False
        return ui.add_gold(app, -int(fee), refresh=True) is not None

    def discovered(app, why):
        """違法の改造が治療院で見つかった。その土地の手配度を下げる。"""
        amount = int(setting(app, "DISCOVERY_LAW"))
        if amount <= 0:
            return
        try:
            entry = ui.area_record(player_of(app), ui.area_id_of(ui.current_area(app)))
            current = ui.lawfulness_of(entry)
            if current is None or not ui.set_lawfulness(entry, current - amount):
                return
            write("discovered ({}): lawfulness {} -> {}".format(why, current, current - amount))
        except Exception:
            ctx.log_exc("augment: cannot change the lawfulness")
            return
        _theme, words = theme_of(app)
        screen.say(app, DISCOVERED_TEXT.format(illegal=rules.underworld_label(words)))

    def hurt_part(app, part, amount):
        """部位に怪我を足し、ゲームの段階の決め直しを呼ぶ。"""
        player = player_of(app)
        parts = getattr(player, "body_parts", None)
        row = parts.get(part) if isinstance(parts, dict) else None
        if not isinstance(row, dict):
            write("WARN hurt: no body part {!r}".format(part))
            return
        before = row.get("injury") if frames.is_number(row.get("injury")) else 0
        row["injury"] = before + amount
        check = getattr(app, "check_body_damage", None)
        if callable(check):
            try:
                check(player)
            except Exception:
                ctx.log_exc("augment: check_body_damage failed")
        write("hurt: {} injury {} -> {} ({})".format(part, before, row["injury"], row.get("stage")))

    def record_life(app, template, part, name):
        try:
            player = player_of(app)
            logs = getattr(player, "life_log", None)
            day = ui.game_day(app)
            if not isinstance(logs, list) or day is None:
                return
            text = template.format(player=ui.player_name(app, PLAYER_FALLBACK),
                                   area=area_name(app), part=rules.PART_NAMES.get(part, part), name=name)
            logs.append({"day_start": day, "day_end": day, "summarized_count": 0,
                         "content": RECORD_MARK + text + "\n"})
        except Exception:
            ctx.log_exc("augment: cannot write the life log")

    def remember(bucket, entry):
        history = bucket.setdefault("history", [])
        history.append(entry)
        del history[:-HISTORY_LIMIT]

    def install(app, payload, mode):
        # 押したボタンの `[アイテムの鍵, 方法]`。方法が読めなければ通常施術（違法にはしない）
        if isinstance(payload, (list, tuple)):
            key, method = (list(payload) + [rules.NORMAL])[:2]
        else:
            key, method = payload, rules.NORMAL
        underworld = mode == "illegal"
        inv = items.inventory_of(player_of(app))
        item = inv.get(key) if isinstance(inv, dict) else None
        data = items.to_dict(item) if item is not None else None
        info = rules.item_info(data)
        if info is None:
            screen.say(app, GONE_TEXT)
            return
        if is_equipped(app, key, item):
            # 装備欄に参照を残したまま持ち物から消さない
            screen.say(app, EQUIPPED_TEXT)
            write("install: {!r} is equipped".format(info["name"]))
            return
        appraisal = appraisal_of(app, info)
        if not appraisal or not appraisal.get("usable"):
            write("install: no usable appraisal for {!r}".format(info["name"]))
            return
        proc = rules.procedure(appraisal, mode, method)
        if not proc["allowed"]:
            write("install: {!r} is forbidden here ({})".format(info["name"], mode))
            return
        illegal, forbidden = proc["illegal"], proc["forbidden"]
        cfg = config(app)
        part = appraisal["part"]
        with play.lock:
            pkey, bucket = ledger(app)
            slot = rules.free_slot(bucket, part, illegal)
        if slot is None:
            write("install: no free slot in {}".format(part))
            return
        fee = rules.install_fee(info, illegal, cfg)
        days = rules.install_days(info, illegal, cfg)
        fail = rules.fail_percent(info, appraisal.get("finish"), illegal, slot == 1, scores(app), cfg)
        if not pay(app, fee):
            screen.say(app, NO_GOLD_TEXT)
            return
        try:
            del inv[key]
        except Exception:
            ctx.log_exc("augment: cannot take the item")
            ui.add_gold(app, int(fee), refresh=True)
            return
        place = place_name(app)
        with play.lock:
            pkey, bucket = ledger(app)
            had_illegal = rules.has_illegal(bucket)
        app.elapse_days(days)
        _theme, words = theme_of(app)
        lines = [coins(PAID_TEXT.format(fee=money(fee)))]
        failed = store["rng"].random() * 100.0 < fail
        day = ui.game_day(app)
        with play.lock:
            pkey, bucket = ledger(app)
            if failed:
                broken = None
                if slot == 1:
                    broken = bucket["augments"][part][0]
                    bucket["augments"][part][0] = None
                remember(bucket, {"day": day, "event": "failed", "part": part, "item": info["name"],
                                  "illegal": illegal, "forbidden": forbidden, "percent": fail})
            else:
                record = rules.make_record(appraisal, info, data, illegal, slot, day, place, cfg.special_min,
                                           rng=store["rng"], forbidden=forbidden)
                bucket["augments"][part][slot] = record
                remember(bucket, {"day": day, "event": "installed", "part": part, "name": record["name"],
                                  "illegal": illegal, "forbidden": forbidden})
            play.save(pkey)
        part_name = rules.PART_NAMES[part]
        if failed:
            hurt_part(app, part, rules.fail_injury(illegal))
            lines.append((TRY_ILLEGAL_TEXT if underworld else TRY_TEXT).format(
                place=place, days=rules.days_words(days)))
            lines.append(FAIL_TEXT.format(part=part_name, amount=rules.fail_injury(illegal), item=info["name"]))
            if slot == 1 and broken:
                lines.append(FAIL_OVERLOAD_TEXT.format(name=broken.get("name") or ""))
            record_life(app, RECORD_FAIL, part, appraisal["name"])
        else:
            lines.append((INSTALL_ILLEGAL_TEXT if underworld else INSTALL_TEXT).format(
                place=place, days=rules.days_words(days), part=part_name, name=appraisal["name"]))
            if not illegal and injuries(app).get(part, (0, ""))[0] >= rules.LOST_FROM:
                # 治療で治らない部位は、合法の施術が通ると取り戻す
                set_injuries(app, {part: 0})
                lines.append(RESTORE_TEXT.format(part=part_name, name=appraisal["name"]))
                write("install: restored the lost {}".format(part))
            record_life(app, RECORD_INSTALL, part, appraisal["name"])
        write("install: {} {} {} slot {} {!r} fee {} days {} fail {}% -> {}".format(
            mode, "forbidden" if forbidden else method if underworld else rules.NORMAL, part, slot, info["name"],
            fee, days, fail, "failed" if failed else "ok"))
        for line in lines:
            screen.say(app, line)
        if had_illegal and not underworld:
            discovered(app, "install")
        finish(app, "install")
        return True

    def remove(app, payload, mode):
        try:
            part, slot = payload
            slot = int(slot)
        except Exception:
            return
        cfg = config(app)
        underworld = mode == "illegal"
        with play.lock:
            pkey, bucket = ledger(app)
            record = (bucket["augments"].get(part) or [None] * rules.SLOTS)[slot]
        if not isinstance(record, dict):
            write("remove: nothing in {} slot {}".format(part, slot))
            return
        fee = rules.remove_fee(record, cfg, at_underworld=underworld)
        days = rules.remove_days(record, cfg, at_underworld=underworld)
        spot = return_spot(app, record)
        if spot is None:
            # 戻した品が所持品の欄からあふれると、ゲームが次に欄を組むときに落とす（VERIFICATION 3.134）
            screen.say(app, NO_ROOM_TEXT)
            write("remove: no room in the inventory for {!r}".format((record.get("item_info") or {}).get("name")))
            return
        if not pay(app, fee):
            screen.say(app, NO_GOLD_TEXT)
            return
        with play.lock:
            pkey, bucket = ledger(app)
            had_illegal = rules.has_illegal(bucket)
            slots = bucket["augments"][part]
            slots[slot] = None
            if slot == 0 and slots[1] is not None:
                # 2つめだけが残らないように、1つめへ詰める
                slots[0], slots[1] = slots[1], None
                slots[0]["slot"] = 0
            remember(bucket, {"day": ui.game_day(app), "event": "removed", "part": part,
                              "name": record.get("name"), "illegal": bool(record.get("illegal"))})
            play.save(pkey)
        place = place_name(app)
        app.elapse_days(days)
        lines = [coins(PAID_TEXT.format(fee=money(fee))),
                 REMOVE_TEXT.format(place=place, days=rules.days_words(days),
                                    part=rules.PART_NAMES[part], name=record.get("name") or "")]
        item_name = (record.get("item_info") or {}).get("name") or ""
        if setting(app, "RETURN_ITEM") and isinstance(record.get("item"), dict):
            new_key = ids.claim(app, "item", write=write)
            data = dict(record["item"])
            if isinstance(spot, tuple):
                data["grid_pos"] = list(spot)     # 施術の時の位置のままだと、ほかの品と重なりうる
            made = items.rebuild(app, player_of(app), {new_key: data}, write=write)
            lines.append(RETURN_TEXT.format(item=item_name) if made else NO_RETURN_TEXT.format(item=item_name))
            write("remove: returned {!r} as {} ({})".format(item_name, new_key, made))
        else:
            lines.append(NO_RETURN_TEXT.format(item=item_name))
        record_life(app, RECORD_REMOVE, part, record.get("name") or "")
        write("remove: {} {} slot {} {!r} fee {} days {}".format(mode, part, slot, record.get("name"), fee, days))
        for line in lines:
            screen.say(app, line)
        if had_illegal and not underworld:
            discovered(app, "remove")
        finish(app, "remove")
        return True

    def finish(app, why):
        ui.refresh_status(app)
        choices.repaint(app)
        save_soon(app, "augment " + why)

    ACTIONS = {"install": install, "remove": remove}

    def run_action(app, action, payload, mode):
        """押したボタンの処理（フェーズの中）。戻り値は返さない（前のフェーズの `execute` と同じ）。"""
        if not ACTIONS[action](app, payload, mode):
            # 途中で断った（品が無い・代金や空きが足りない）。finish を通らないので、施設のボタンをここで組み直す
            choices.repaint(app)

    # ------------------------------------------------------------ 施設のボタン
    def offer(app, view_):
        buttons = view_.buttons
        if not buttons or not ui.is_facility_screen(buttons):
            return None
        mode = mode_here(app)
        if mode is None:
            return None
        _theme, words = theme_of(app)
        text = words["title"] if mode == "legal" else rules.underworld_label(words)

        def on_press(app_):
            write("pressed {!r}".format(text))
            later(lambda: open_view(app_, mode))
            return None

        return [choices.Choice(text, place=choices.before(EXIT_CLS), on_press=on_press)]

    state["owner"] = choices.provide(ctx, screen, offer=offer)

    # ------------------------------------------------------------ MOD の一覧から開く（見るだけ）
    # 開いていれば閉じ、閉じていれば見るだけで開く（`app` が無ければ開かない）
    toggle_view = shown_window.on_press(opener_for(None))

    def listed(app):
        return player_of(app) is not None

    _theme0 = rules.THEME_WORDS[rules.NEUTRAL]
    # 絵のパスは apply() の間に組む（`ctx.mod_dir` はフックの中では読めない）。
    icon_dir = getattr(ctx, "mod_dir", None) or os.path.dirname(os.path.abspath(__file__))
    hudicons.add(ctx, ICON_KEY, toggle_view, order=ICON_ORDER, label=_theme0["title"], write=write, visible=listed,
                 images=tuple(os.path.join(icon_dir, name) for name in ICON_FILES))

    # ------------------------------------------------------------ 効果
    def records_for(app):
        if player_of(app) is None:
            return []
        with play.lock:
            _key, bucket = ledger(app)
            return [record for _p, _s, record in rules.records_of(bucket)]

    def ability_answer(app, holder):
        player = player_of(app)
        if player is None or holder is not player:
            return None
        records = records_for(app)
        if not records:
            return None
        return rules.ability_totals(records, losses(app)) or None

    def battle_answer(app, holder):
        player = player_of(app)
        if player is None or holder is not player:
            return None
        return rules.battle_totals(records_for(app)) or None

    def on_kill(app, actor, _defender):
        """暴走: 主人公が敵を倒すたびに、その部位の怪我を増やす（段階の決め直しは戦闘の外で）。"""
        player = player_of(app)
        if player is None or actor is not player:
            return []
        parts = rules.rampage_parts(records_for(app))
        if not parts:
            return []
        now = injuries(app)
        changes = {}
        for part in parts:
            value = changes.get(part, now.get(part, (0, ""))[0])
            if value < rules.WEAR_CAP:
                changes[part] = min(rules.WEAR_CAP, value + rules.RAMPAGE_INJURY)
        if not changes:
            return []
        set_injuries(app, changes, check=False)
        # 決め直しの印は周回の控えに置く（日が進む前にゲームを閉じても、次に読み込んだときに決め直す）
        with play.lock:
            pkey, bucket = ledger(app)
            if not bucket.get("recheck"):
                bucket["recheck"] = True
                play.save(pkey)
        write("rampage: {}".format(changes))
        return [RAMPAGE_TEXT.format(part="・".join(rules.PART_NAMES[part] for part in changes))]

    abilities.declare(OWNER, ability_answer, write=write)
    battle_effects.declare(OWNER, battle_answer, on_kill=on_kill, write=write)

    # ------------------------------------------------------------ サイクルで進む効果（侵食・傷が開く・自己修復）
    def take_recheck(app):
        """暴走の決め直しの印を下ろす。立っていたら True。"""
        with play.lock:
            pkey, bucket = ledger(app)
            if not bucket.get("recheck"):
                return False
            bucket["recheck"] = False
            play.save(pkey)
        return True

    def advance_cycles(app, label, days=0, cycles=0, save=False):
        """サイクルを進めて怪我を動かす。暴走の決め直しもここで呼ぶ（どちらも戦闘の外）。

        日数は改造ごとに組み込んだ日から `WEAR_CYCLE_DAYS` 日ごと、`cycles` は日数によらず進める数（依頼の終わり）。
        `save` は、怪我を書いたらゲームを保存し直す（依頼の終わりは帰還の保存が済んだ後に来る）。
        """
        if player_of(app) is None:
            return
        with play.lock:
            pkey, bucket = ledger(app)
            records = [record for _p, _s, record in rules.records_of(bucket)]
            events, carries = rules.wear_events(records, days, cycles)
            moved = False
            for record, carry in zip(records, carries):
                if record.get("wear_days") != carry:
                    record["wear_days"] = carry
                    moved = True
            if moved:
                play.save(pkey)
        recheck = take_recheck(app)
        now = {part: value for part, (value, _stage) in injuries(app).items()}
        changes = rules.apply_wear(now, records, events) if events else {}
        if changes or recheck:
            set_injuries(app, changes, force=recheck)
            if changes:
                write("wear ({}): {} event(s) {} -> {}".format(
                    label, len(events), {p: now.get(p) for p in changes}, changes))
            if save and callable(getattr(app, "save_game", None)):
                app.save_game()

    def after_days(call):
        advance_cycles(call.app, "{} day(s)".format(call.moved), days=call.moved)

    durations.on_days(OWNER, ctx, after=after_days, write=write)

    def quest_end_before(call):
        """片付けると移動する依頼か（ローダの窓口 `quest_ends`。依頼のデータは元の処理の中で片付くので先に読む）。"""
        return durations.quest_travels(call.app, call.quest_id, write=write)

    def quest_end_after(call):
        """依頼から帰った。移動する依頼は移動の日数でサイクルが進むので重ねない（344 と同じ扱い）。"""
        if call.memo:
            write("quest {!r} moves on to another area; the journey's days advance the cycles".format(call.quest_id))
            return
        advance_cycles(call.app, "quest {!r} cleared".format(call.quest_id), cycles=rules.QUEST_CYCLES, save=True)

    quest_ends.on(OWNER, ctx, before=quest_end_before, after=quest_end_after, write=write)

    def quest_retire_after(call):
        """依頼を放棄した（ローダの窓口 `quest_ends.on_retire`。依頼の id は窓口が元の処理の前に読む）。放棄は移動しない。"""
        advance_cycles(call.app, "quest {!r} abandoned".format(call.quest_id), cycles=rules.QUEST_CYCLES, save=True)

    quest_ends.on_retire(OWNER, ctx, after=quest_retire_after, write=write)

    # ------------------------------------------------------------ 治療院の棚
    def shop_note(facility):
        """治療院の品揃えの頼みに足す一文（窓口 `shop_lineup` の足す文。340 か 312 が頼みに入れる）。"""
        if ui.facility_type_of(facility) != MEDICAL_TYPE:
            return None
        app = ui.find_app()
        if app is None or not setting(app, "SHOP_NOTE"):
            return None
        theme, _words = theme_of(app)
        return rules.SHOP_NOTES.get(theme) or rules.SHOP_NOTES[rules.NEUTRAL]

    shop_lineup.add_note(OWNER, ctx, shop_note)

    # ------------------------------------------------------------ 治療で見つかる
    @ctx.wrap(TREATMENT_TARGET, required=False, safe=True)
    def treatment(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = ui.find_app()
            if app is not None:
                with play.lock:
                    _key, bucket = ledger(app)
                    flagged = rules.has_illegal(bucket)
                if flagged:
                    discovered(app, "treatment")
        except Exception:
            ctx.log_exc("augment: discovery after treatment failed")
        return result

    # ------------------------------------------------------------ ロード
    def on_load(call):
        play.forget()
        close_view()
        # 暴走で書いた怪我の決め直しが前のプロセスで済んでいなければ、読み込んだ後に呼ぶ
        app = getattr(call, "app", None) or ui.find_app()
        try:
            if player_of(app) is not None and take_recheck(app):
                set_injuries(app, {}, force=True)
                write("load: rechecked the injuries left by rampage")
        except Exception:
            ctx.log_exc("augment: recheck after load failed")

    loads.on(OWNER, ctx, (loads.LOAD, loads.NEW_GAME), after=on_load, write=write)

    ctx.log("body augmentation: installed, log={}".format(ctx.out_path(LOG_BASENAME)))
