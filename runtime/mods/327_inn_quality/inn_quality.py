# -*- coding: utf-8 -*-
"""機能追加: 宿の部屋の等級を効かせる。

素のゲームでは 10G の部屋と 1000G の部屋で起きることが同じで、
高い部屋を選ぶ理由が無い（体力の回復・仲間の回復はどの部屋でも全快。実測）。
この MOD は等級に3つの意味を足す。

1. **1回の宿泊でできる活動の数**が部屋で変わる。
   素のゲームは宿泊1回につき活動1回（実測。`out/vacation.jsonl` の宿泊54回すべてが
   `VacationStartManager` → 活動1回 → `VacationEndManager`。GAME.md §2.17）。
   宿代は活動1回の料金で、同時に暦を30日払っている。
   個室なら2回、高級個室なら3回を同じ宿代・同じ30日で過ごせる。
   買っているのは暦で、金が余る終盤でも日数は他で買えない。
2. **宿の主が常連を覚える**。
   宿泊のたびに宿の主の好感度（`relationship["player"]["affinity"]`）が等級ぶん上がり、
   宿泊由来の累計は上限で止まる（泊まり続けて家族同然になるのは不自然）。
   宿の主と話すときは、出ていく本文へ宿泊の履歴を1行足す。
   記録は `state\\inn_regular\\<世界名>.json`。
3. **社交で会う相手が部屋で変わる**。
   素のゲームの社交は、泊まっている宿の名簿（`Facility.characters`）から相手を選ぶ
   （実測: `output_data/.../vacation_scene_generator` の【イベントの場所】が宿、【参加NPC】が宿の主）。
   この MOD は `VacationSocializeManager.execute` の間だけ宿の名簿を選んだ1人に差し替える。
   相手の優先は 同行者 → エリア内の好感度の高い NPC → エリア内の生成済み NPC からランダム で、
   部屋の等級がどこまで上から選べるかを決める（既定: 犬小屋=ランダムだけ / 簡易寝台=ゲームのまま /
   個室=好感度から / 高級個室=同行者から）。
   選んだ相手の `location` が宿でなければ窓の間だけ宿にする（名簿の相手を現在地で弾くビルドへの保険）。
   `vacation_scene_generator` に渡った `npc_list` を控えて、差し替えが効いたかをログに残す。

活動の数の作り:

- 活動の一覧はゲームが最初に出す「何をして過ごす？」の画面から写す
  （`refresh_choice_buttons` の直前に `app.buttons` を読む。ボタンの `cls` と `args` をそのまま）。
  文言も語彙もこちらでは持たない
- 活動が終わって「まだ宿泊する／宿泊を終える」だけの画面になったとき、
  残りがあれば写した活動をその前に並べ直す。
  spec はゲーム自身のクラスなので、押せばゲームが普通に活動を起こす
- 残りは `VacationStartManager.execute` ごとに積み直す（連泊の2周目も同じ数）。
  数えるのは活動マネージャの `execute`（社交の `...ResolveManager` は数えない）
- 宿泊が成立したかは窓の中で `elapse_days` が呼ばれたかで見る
  （金が足りないときは日数が動かない。GAME.md §2.18 の馬車と同型）

常連の作り:

- 宿の主は `app.player.location`（宿泊中は `Facility`）の `owner`（GAME.md §2.7）
- 好感度は本体の `relationship` に直接足す。文（`affinity_text`）は本体が会話のたびに
  書き直すので触らない（GAME.md §2.25.1）。`"player"` の欄が無い初対面の主には足さず記録だけ残す
  （欄を新設しない。TECH.md §6.4）
- 会話相手は `app.in_conversation`（`311_` と同じ読み方）。
  1行は `llm.wrap_outgoing` で先頭の本文に足す。同じ行が既に在れば足さない
- `311_` / `403_` / `300_` の控えは読みも書きもしない

遊び方の説明は DOC.md、検証の経過は VERIFICATION.md §3.54。
"""

import random
import sys

from instantale_modloader import llm, ui
from instantale_modloader.state import WorldStore

LOG_BASENAME = "inn_quality.log"
STATE_DIRNAME = "inn_regular"
STORE_ATTR = "__instantale_inn_quality_store__"
MARK = "mod_inn_quality"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる。他のファイルへ移さないこと。
# 1回の宿泊でできる活動の数。素のゲームは全部屋 1。
ACTIONS_KENNEL = 1
ACTIONS_BUNK = 1
ACTIONS_PRIVATE = 2
ACTIONS_LUXURY = 3

# 宿の主が常連を覚える（好感度と会話の1行）。
REGULAR_MEMORY = True

# 宿泊1回で宿の主の好感度に足す量。
AFFINITY_KENNEL = 0
AFFINITY_BUNK = 1
AFFINITY_PRIVATE = 2
AFFINITY_LUXURY = 4

# 宿泊由来の好感度の累計の上限。
# 好感度の段は 0/10/20/30/40（GAME.md §2.25.1）。20 で「嫌いではない」まで。
AFFINITY_CAP = 20

# 宿の主と話すとき、出ていく本文に足す1行。
# {name} 宿の主 / {stays} 宿泊回数 / {rooms} 部屋ごとの内訳 / {ago} 最後の宿泊からの日数（読めなければ「不明」）
REGULAR_LINE = ("【宿の客として】プレイヤーは{name}の宿にこれまで{stays}回泊まっている"
                "（{rooms}）。最後の宿泊は{ago}日前。")

# 社交で会う相手を、どの段から選ぶか（部屋ごと）。
# 「同行者」→ 同行者 → 好感度の高い NPC → ランダム の順に埋める。
# 「好感度」→ 好感度の高い NPC → ランダム。「ランダム」→ エリアの生成済み NPC から。
# 「ゲームのまま」→ 触らない（素のゲームは宿の名簿から選ぶ）。
SOCIAL_KENNEL = "ランダム"
SOCIAL_BUNK = "ゲームのまま"
SOCIAL_PRIVATE = "好感度"
SOCIAL_LUXURY = "同行者"

# 「好感度の高い NPC」とみなす下限（好感度の段は 0/10/20/30/40。10 で「好きでも嫌いでもない」）。
FRIEND_AFFINITY = 10

# ---------------------------------------------------------------- コード側の設定
SOCIAL_TIERS = {"同行者": ("party", "friends", "random"),
                "好感度": ("friends", "random"),
                "ランダム": ("random",),
                "ゲームのまま": ()}
SOCIALIZE_CLASS = "VacationSocializeManager"

# `quality` の実値 → 設定名の接尾（実機で観測した4つ。GAME.md §2.17）。
SLOT_OF_QUALITY = {"kennel": "KENNEL", "bunk": "BUNK",
                   "private_room": "PRIVATE", "luxury_suite": "LUXURY"}
ROOM_NAMES = {"kennel": "犬小屋", "bunk": "簡易寝台",
              "private_room": "個室", "luxury_suite": "高級個室"}

# 数える活動（targets.txt の実在クラス）。社交の Resolve は同じ活動の後半なので数えない。
ACTIVITY_CLASSES = ("VacationTrainManager", "VacationRestManager",
                    "VacationLaborManager", "VacationSocializeManager",
                    "VacationBeggingManager")
START_CLASS = "VacationStartManager"
END_CLASS = "VacationEndManager"

RECORD_KEYS = ("name", "facility_id", "stays", "by_quality", "last_day", "granted")


def setting(prefix, quality, default=0):
    slot = SLOT_OF_QUALITY.get(str(quality))
    if slot is None:
        return default
    try:
        return int(globals().get(prefix + slot, default))
    except (TypeError, ValueError):
        return default


def actions_for(quality):
    return max(1, setting("ACTIONS_", quality, 1))


def ordered(bucket):
    """`state/` の差分が読めるよう並びを固定する。"""
    out = {}
    for owner in sorted(bucket, key=ui.id_sort_key):
        record = bucket[owner]
        if isinstance(record, dict):
            out[owner] = {k: record[k] for k in RECORD_KEYS if k in record}
    return out


def inn_owner(app):
    """宿泊中の宿の主の `(id, 施設)`。読めなければ `(None, None)`。"""
    facility = getattr(getattr(app, "player", None), "location", None)
    owner = getattr(facility, "owner", None)
    if owner is None or isinstance(owner, (dict, list)):
        return None, None
    return str(owner), facility


def rooms_text(by_quality):
    parts = []
    for quality in SLOT_OF_QUALITY:
        count = by_quality.get(quality)
        if count:
            parts.append("{}{}回".format(ROOM_NAMES[quality], count))
    return "・".join(parts) or "内訳なし"


def regular_line(record, app):
    day = ui.game_day(app)
    last = record.get("last_day")
    ago = (day - last) if isinstance(day, int) and isinstance(last, int) else "不明"
    return str(REGULAR_LINE).format(
        name=record.get("name") or "宿の主", stays=record.get("stays", 0),
        rooms=rooms_text(record.get("by_quality") or {}), ago=ago)


def affinity_of(character):
    player = getattr(character, "relationship", None)
    player = player.get("player") if isinstance(player, dict) else None
    value = player.get("affinity") if isinstance(player, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def area_npc_ids(app):
    """いまのエリアの施設の名簿と主に載っている NPC の id（生成済み＝`world.characters` に居る）。"""
    found = []
    for node in ui.nodes_of(ui.current_area(app)):
        for facility in ui.facilities_of(node).values():
            members = getattr(facility, "characters", None)
            ids = list(members) if isinstance(members, (list, tuple)) else []
            ids.append(getattr(facility, "owner", None))
            for value in ids:
                if value is None or isinstance(value, (dict, list)):
                    continue
                key = str(value)
                if key not in found and ui.character_of(app, key) is not None:
                    found.append(key)
    return found


def pick_partner(app, quality, rng):
    """社交の相手を1人選ぶ。`(id, 段)`。触らないなら `(None, 理由)`。"""
    tiers = SOCIAL_TIERS.get(str(globals().get(
        "SOCIAL_" + SLOT_OF_QUALITY.get(str(quality), ""), "ゲームのまま")), ())
    if not tiers:
        return None, "game default"
    party = [m for m in ui.party_member_ids(app) if ui.character_of(app, m) is not None]
    others = [n for n in area_npc_ids(app) if n not in party and n != ui.PLAYER_ID]
    for tier in tiers:
        if tier == "party" and party:
            return rng.choice(party), tier
        if tier == "friends":
            scored = [(affinity_of(ui.character_of(app, n)), n) for n in others]
            scored = [(a, n) for a, n in scored if a is not None and a >= int(FRIEND_AFFINITY)]
            if scored:
                best = max(a for a, _n in scored)
                return rng.choice([n for a, n in scored if a == best]), tier
        if tier == "random" and others:
            return rng.choice(others), tier
    return None, "no candidate"


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="inn quality", mark=MARK)

    # 置き場は `sys`（`315_` と同じ理由。当て直しが宿泊の最中に挟まっても窓と残りが消えない）。
    store = getattr(sys, STORE_ATTR, None)
    if store is None:
        store = {
            "state": {
                "window": None,   # `VacationStartManager.execute` の中: {"quality", "elapsed"}
                "stay": None,     # 宿泊中: {"quality", "left"}
                "menu": None,     # 写した活動の一覧 [(text, cls, args)]
                "partner": None,  # 社交で差し替えた相手 (id, 名前)
            },
            "worlds": WorldStore(ctx, STATE_DIRNAME, order=ordered),
            "rng": random.Random(),      # ゲーム自身の乱数列をずらさない（TECH.md §6.1）
        }
        setattr(sys, STORE_ATTR, store)
    state = store["state"]
    worlds = store["worlds"].rebind(ctx, write)

    # ============================================================ 活動の数
    @ctx.wrap("__main__:VacationStartManager.__init__", required=False, safe=True)
    def start_init(orig, self, app, months=None, quality=None, *args, **kwargs):
        result = orig(self, app, months, quality, *args, **kwargs)
        self._mod_inn_quality = str(quality)
        return result

    @ctx.wrap("__main__:VacationStartManager.execute", required=False)
    def start_execute(orig, self, choice_text=None, *args, **kwargs):
        quality = getattr(self, "_mod_inn_quality", None)
        state["window"] = {"quality": quality, "elapsed": False}
        state["stay"] = None
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            window, state["window"] = state["window"], None
            try:
                if window and window["elapsed"]:
                    state["stay"] = {"quality": quality, "left": actions_for(quality)}
                    write("stay: quality={!r} actions={}".format(
                        quality, state["stay"]["left"]))
                    if REGULAR_MEMORY:
                        remember(getattr(self, "app", None) or ui.find_app(), quality)
                else:
                    write("stay: no elapse_days in the window (quality={!r}); "
                          "not counted".format(quality))
            except Exception:
                ctx.log_exc("inn quality: cannot open the stay")

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days=None, *args, **kwargs):
        window = state["window"]
        if window is not None:
            window["elapsed"] = True
        return orig(self, days, *args, **kwargs)

    def install_counter(cls_name):
        @ctx.wrap("__main__:{}.execute".format(cls_name), required=False)
        def activity_execute(orig, self, choice_text=None, *args, **kwargs):
            # 入口で数える。活動は自分の `execute` の中で次の画面を描くので、
            # 出口で数えると最後の活動の後にもう1回並んでしまう。
            stay = state["stay"]
            if stay is not None:
                stay["left"] -= 1
                write("activity: {} left={}".format(cls_name, stay["left"]))
            return orig(self, choice_text, *args, **kwargs)

    for name in ACTIVITY_CLASSES:
        if name != SOCIALIZE_CLASS:
            install_counter(name)

    @ctx.wrap("__main__:VacationSocializeManager.execute", required=False)
    def socialize_execute(orig, self, choice_text=None, *args, **kwargs):
        """数えるのは他の活動と同じ。加えて、窓の間だけ宿の名簿を選んだ相手に差し替える。"""
        stay = state["stay"]
        if stay is not None:
            stay["left"] -= 1
            write("activity: {} left={}".format(SOCIALIZE_CLASS, stay["left"]))
        app = getattr(self, "app", None) or ui.find_app()
        facility = getattr(getattr(app, "player", None), "location", None)
        restore = []
        try:
            quality = stay["quality"] if stay else None
            partner, tier = pick_partner(app, quality, store["rng"])
            if partner is not None and facility is not None:
                character = ui.character_of(app, partner)
                restore.append((facility, "characters", getattr(facility, "characters", None)))
                facility.characters = [partner]
                if getattr(character, "location", None) is not facility:
                    restore.append((character, "location", getattr(character, "location", None)))
                    character.location = facility
                state["partner"] = (partner, ui.character_name(app, partner))
                write("social: {} -> {} ({}) by {}".format(
                    quality, state["partner"][1], partner, tier))
            else:
                state["partner"] = None
                write("social: {} untouched ({})".format(quality, tier))
        except Exception:
            ctx.log_exc("inn quality: cannot pick the social partner")
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            for owner, attr, value in reversed(restore):
                try:
                    setattr(owner, attr, value)
                except Exception:
                    ctx.log_exc("inn quality: cannot restore {}".format(attr))

    @ctx.wrap("scripts.llm.llm_manager:vacation_scene_generator", required=False)
    def scene_generator(orig, *args, **kwargs):
        """差し替えが効いたかを控えるだけ（引数には触らない）。"""
        try:
            npc_list = kwargs.get("npc_list", args[6] if len(args) > 6 else None)
            names = [getattr(n, "name", None) or (n.get("name") if isinstance(n, dict) else None)
                     or str(n) for n in (npc_list if isinstance(npc_list, (list, tuple)) else [])]
            partner = state.get("partner")
            if partner is not None:
                hit = any(partner[0] == n or partner[1] == n for n in names)
                write("social: scene npc_list={} -> {}".format(
                    names, "swap effective" if hit else "WARN swap ineffective"))
            else:
                write("social: scene npc_list={}".format(names))
        except Exception:
            ctx.log_exc("inn quality: cannot read npc_list")
        return orig(*args, **kwargs)

    @ctx.wrap("__main__:VacationEndManager.execute", required=False)
    def end_execute(orig, self, choice_text=None, *args, **kwargs):
        state["stay"] = None
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False,
              safe=True)
    def refresh_buttons(orig, self, *args, **kwargs):
        """描かれる直前に、活動の一覧を写す／残りがあれば並べ直す。"""
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list):
                activities = [e for e in buttons
                              if ui.spec_cls_name(e) in ACTIVITY_CLASSES]
                if activities:
                    if not any(screen.mark_of(e) for e in activities):
                        state["menu"] = [(e.get("text"), ui.spec_cls_name(e),
                                          ui.spec_args(e) or []) for e in activities]
                elif state["stay"] and state["stay"]["left"] > 0 \
                        and state["menu"] \
                        and ui.find_spec_button(buttons, END_CLASS) is not None:
                    entries = [screen.button(text, mark="again", cls_name=cls, args=argv)
                               for text, cls, argv in state["menu"]]
                    entries = [e for e in entries if e is not None]
                    buttons[0:0] = entries
                    write("menu: {} activit{} again (left={})".format(
                        len(entries), "y" if len(entries) == 1 else "ies",
                        state["stay"]["left"]))
        except Exception:
            ctx.log_exc("inn quality: cannot rebuild the activity menu")
        return orig(self, *args, **kwargs)

    # ============================================================ 常連
    def remember(app, quality):
        owner, facility = inn_owner(app)
        if owner is None:
            write("regular: no facility owner under the player; not recorded")
            return
        key, bucket = worlds.of(app)
        with worlds.lock:
            record = bucket.get(owner)
            if not isinstance(record, dict):
                record = bucket[owner] = {"stays": 0, "by_quality": {}, "granted": 0}
            record["name"] = ui.character_name(app, owner)
            record["facility_id"] = str(getattr(facility, "id", ""))
            record["stays"] = int(record.get("stays", 0)) + 1
            by_quality = record.setdefault("by_quality", {})
            by_quality[str(quality)] = int(by_quality.get(str(quality), 0)) + 1
            day = ui.game_day(app)
            if isinstance(day, int):
                record["last_day"] = day
            gain = min(setting("AFFINITY_", quality, 0),
                       int(AFFINITY_CAP) - int(record.get("granted", 0)))
            if gain > 0:
                player = getattr(ui.character_of(app, owner), "relationship", None)
                player = player.get("player") if isinstance(player, dict) else None
                affinity = player.get("affinity") if isinstance(player, dict) else None
                if isinstance(affinity, int) and not isinstance(affinity, bool):
                    player["affinity"] = affinity + gain
                    record["granted"] = int(record.get("granted", 0)) + gain
                    write("regular: {} affinity {} -> {} (granted {}/{})".format(
                        record["name"], affinity, affinity + gain,
                        record["granted"], AFFINITY_CAP))
                else:
                    write("regular: {} has no relationship['player']['affinity']; "
                          "recorded only".format(record["name"]))
            worlds.save(key)
        write("regular: {} stays={} rooms={}".format(
            record["name"], record["stays"], record["by_quality"]))

    def rewrite_outgoing(texts, site):
        if not REGULAR_MEMORY or not texts:
            return None
        app = ui.find_app()
        npc = getattr(app, "in_conversation", None)
        if not isinstance(npc, str) or not npc:
            return None
        _key, bucket = worlds.of(app)
        record = bucket.get(npc)
        if not isinstance(record, dict) or not record.get("stays"):
            return None
        line = regular_line(record, app)
        if any(line in t for t in texts):
            return None
        write("prompt at {}: {}".format(site, line))
        return [line + "\n" + texts[0]] + list(texts[1:])

    llm.wrap_outgoing(ctx, rewrite_outgoing, label="inn quality")

    ctx.log("inn quality: actions {}/{}/{}/{} affinity cap {}".format(
        ACTIONS_KENNEL, ACTIONS_BUNK, ACTIONS_PRIVATE, ACTIONS_LUXURY, AFFINITY_CAP))
