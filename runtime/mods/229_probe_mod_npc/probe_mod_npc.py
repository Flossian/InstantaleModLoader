# -*- coding: utf-8 -*-
r"""MOD が持つ NPC と、正規 NPC への被せがどこまで通るかを測る。

土台はローダの `instantale_modloader.modnpc`。
そこに書いてあることは、この probe が実機で確かめるまでどれも見込みでしかない。

##### 何を作るか

プレイヤーが入った施設に、**セーブに残らない人物を1人**立てる
（id は `mod:229_probe_mod_npc:visitor`）。
同じ施設に主が居れば、その主の `profile` の末尾に印を1行足す（被せ）。
どちらも保存の直前にローダの関所が引き上げる。

##### 何を測るか

| # | 見るもの | 記録 |
|---|---|---|
| 1 | 保存に漏れないか | `at=disk`。保存の直後にディスクのセーブを読み、`npcs` と `characters` に `mod:` の鍵が無いかを数える |
| 2 | 会話が始まって終わるか | `at=conversation_start` / `at=prompt` / `at=conversation_end` |
| 3 | 詳細生成が要るか | `at=detail`。既定では本体へ通さない。`TRY_DETAIL` を立てると通す |
| 4 | 被せが頼み文に載るか | `at=prompt` の `mark`。正規 NPC の `profile` に足した印が引数に含まれるか |
| 5 | 立ち絵 | `at=image`。作り直しの入口が呼ばれるか |

記録は `out\mod_npc.jsonl`（後で数える表）と `out\mod_npc.log`（読む文）。

##### 通らない見込みのものは試さない

戦闘とパーティ加入はここでは触らない。
スキルと立ち絵が空のまま戦闘に入るとゲーム本体が落ちる
（VERIFICATION_LOG.md §2.40 / §2.42）ので、測る前に本体側の話になる。

##### 値を変えるプローブであること

200 番台は読み取り専用が原則（TECH.md §3.2.2）だが、
ここは**置いてみないと何も分からない**（素のままの観測はどの見込みにも等しく一致する）。
変えたものは保存の直前に全部戻り、`debug` の旗が立っている間だけ動く。
"""

import json
import threading

from instantale_modloader import frames, llm, modnpc, saves, state, ui

TALK_TARGET = "__main__:DisplayTalkChoice.execute"

LOG_BASENAME = "mod_npc.log"
RECORD_BASENAME = "mod_npc.jsonl"

#: 登録の名乗り。フォルダ名を使う（ログに出る名前と揃う。TECH.md §3.1.6）。
OWNER = "229_probe_mod_npc"

#: 被せの印。正規 NPC の `profile` の末尾に足して、
#: 頼み文の引数にこの字面が現れるかを見る。
OVERRIDE_MARK = "【229 の被せ】"

#: 立てる人物。項目名はセーブのもの（`npcs.NEW_NPC_TEMPLATE` の33項目）。
#: 埋めるのは名前と素性だけで、HP もスキルも装備も空のまま。
#: 名前はそのままファイルのパスになる（GAME.md §2.15）ので記号を入れない。
NPC_FIELDS = {
    "name": "測定用の来訪者",
    "profile": "ローダの modnpc が組んだ人物。セーブには残らない",
    "personality": "淡々としている",
    "look_description": "旅装の人物",
    "job": "adventure",
    "age": 20,
    "experience_level": 1,
}

# -- 設定（GUI から変えられる。TECH.md §3.8）--------------------------------
#: 来訪者を居させるか。切って注入し直すと `unregister` で片付け、その結果を録る（片付けの測定用）。
PRESENT = True
#: プレイヤーの入った施設へ連れて回るか。
FOLLOW = True
#: その施設の主に被せを載せるか。
OVERRIDE = True
#: 詳細生成を本体へ通すか（ローダの既定と同じ）。切ると来訪者の詳細は空のまま。
TRY_DETAIL = True
#: 保存の直後にディスクのセーブを読む回数の上限。
DISK_CHECKS = 5
#: 施設に着いたら来訪者→主の順に会話を起こして閉じる（画面を押せない環境用）。
AUTO_TALK = False
#: 自動の会話で、第一声が出てから閉じるまでに置く秒数。
AUTO_TALK_LINGER = 6
#: タイトル画面で注入されたら、この名前の世界を読み込む（画面を押せない環境用）。空なら何もしない。
AUTO_LOAD_WORLD = ""
#: 自動の会話を起こす前に置く秒数。起動直後は LLM の口がまだ無い
#: （ロードの3秒後に起こして `conversation_starter` が `'NoneType' object is not callable`
#: で落ち、画面が「…」のまま止まった。実機 2026-09-12）。
AUTO_TALK_DELAY = 45
#: 保存の間、来訪者を名簿から外す（ローダの `modnpc.LIFT_ROSTER`）。
#: 切って `leaked` が空のままなら、保存は名簿を読んでいない。
LIFT_ROSTER = True


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    warn = ctx.warner("mod npc")
    seen = {"npc_id": None, "spot": None, "owner": None, "disk": 0,
            "sites": set(), "auto": None}
    screen = ui.Screen(ctx, write, tag="mod npc")

    def note(at, **fields):
        record(dict({"at": at}, **fields))

    # -- 層のフック ---------------------------------------------------------
    def on_world(info):
        """セーブを読んだ直後。実体は捨てられているので、次の到着で建て直す。"""
        seen["spot"] = None
        seen["owner"] = None
        note("world", npc=info["npc_id"])
        write("world changed; the visitor will be rebuilt on the next arrival")

    def on_conversation_start(info):
        note("conversation_start", npc=info["npc_id"],
             name=frames.text_of(info.get("character"), "name"))
        write("conversation starts with {}".format(info["npc_id"]))

    def on_conversation_end(info):
        """要約が回った後。記憶がインスタンスに溜まったかを数える。

        `current_log` と `relationship` は `Character` の属性で、
        素データを経由しない（GAME.md §2.25）。
        セーブに残らない人物でも、この2つは溜まるはず ― その確認。
        """
        character = info.get("character")
        log = getattr(character, "current_log", None)
        relationship = getattr(character, "relationship", None)
        note("conversation_end", npc=info["npc_id"],
             current_log=len(log) if isinstance(log, list) else None,
             relationship=sorted(relationship) if isinstance(relationship, dict)
             else None)
        write("conversation ended with {} (current_log={})".format(
            info["npc_id"], len(log) if isinstance(log, list) else "?"))

    def on_detail(info):
        """詳細生成。ローダの既定は本体へ通す。`TRY_DETAIL` を切ると False を返して止める。"""
        note("detail", npc=info["npc_id"], passed=bool(TRY_DETAIL),
             site=(info.get("args") or {}).get("site"))
        write("detail generation for {} via {}: {}".format(
            info["npc_id"], (info.get("args") or {}).get("site"),
            "passed to the game" if TRY_DETAIL else "skipped"))
        return True if TRY_DETAIL else False

    def on_detail_done(info):
        """本体に埋めさせた後。何が埋まり、素データに何が書かれたかを録る。"""
        app = info["app"]
        character = info.get("character")
        skills = getattr(character, "skills", None)
        image_src = getattr(character, "image_src", None)
        plain = (getattr(app, "save_data_dict", None) or {}).get("npcs")
        world_plain = (getattr(app, "world_dict", None) or {}).get("npcs")
        entry = plain.get(info["npc_id"]) if isinstance(plain, dict) else None
        note("detail_done", npc=info["npc_id"],
             plain_entry_skills=sorted(entry.get("skills") or {}) if isinstance(entry, dict) else None,
             plain_entry_lod=(entry.get("config") or {}).get("level_of_detail") if isinstance(entry, dict) else None,
             skills=sorted(skills) if isinstance(skills, dict) else None,
             current_hp=getattr(character, "current_hp", None),
             max_hp=getattr(character, "max_hp", None),
             images={k: bool(v) for k, v in image_src.items()}
             if isinstance(image_src, dict) else None,
             level_of_detail=(getattr(character, "config", None) or {}).get("level_of_detail")
             if isinstance(getattr(character, "config", None), dict) else None,
             in_save_data_npcs=isinstance(plain, dict) and info["npc_id"] in plain,
             in_world_dict_npcs=isinstance(world_plain, dict) and info["npc_id"] in world_plain)
        write("detail done for {}: skills={} hp={}/{} images={} plain-data leak: save={} world={}".format(
            info["npc_id"], sorted(skills) if isinstance(skills, dict) else None,
            getattr(character, "current_hp", None), getattr(character, "max_hp", None),
            {k: bool(v) for k, v in image_src.items()} if isinstance(image_src, dict) else None,
            isinstance(plain, dict) and info["npc_id"] in plain,
            isinstance(world_plain, dict) and info["npc_id"] in world_plain))

    def on_image(info):
        note("image", npc=info["npc_id"])
        write("the game wants to rebuild the portrait of {}".format(
            info["npc_id"]))
        return None

    def on_save(info):
        note("save", npc=info["npc_id"],
             phase=(info.get("args") or {}).get("phase"))

    def on_prompt(info):
        """頼み文を組む直前。引数に何が載っているかを数える。

        本文は書かない（量が多く、遊びの中身がそのまま残る）。
        載っているかどうかだけを数で残す。
        """
        site = info["site"]
        args = info.get("args") or {}
        text = _flatten(args.get("messages"))
        # `messages` は行動の1行だけ（実機で11字）。素性は関数の中で
        # `character_instance` から組まれる。被せの印（`notes`）はこのフックの後で
        # 複製に足されるので、届いたかは `output_data` の頼み文で数える（DOC.md）。
        note("prompt", npc=info["npc_id"], site=site, chars=len(text),
             profile_chars=len(_profile_of(args) or ""),
             keys=sorted(args))
        if site not in seen["sites"]:
            seen["sites"].add(site)
            write("{} carries {} char(s) for {} (profile {} chars)".format(
                site, len(text), info["npc_id"], len(_profile_of(args) or "")))

    HOOKS = {"world": on_world, "conversation_start": on_conversation_start,
             "conversation_end": on_conversation_end, "detail": on_detail,
             "detail_done": on_detail_done, "image": on_image, "save": on_save}

    # -- 登録 ---------------------------------------------------------------
    # 関所はローダが1つだけ立てる。何本の MOD が呼んでも増えない。
    modnpc.LIFT_ROSTER = bool(LIFT_ROSTER)
    targets = modnpc.install(ctx, write=write)
    write("the gate covers {} target(s)".format(len(targets)))

    seen["npc_id"] = modnpc.make_id(OWNER, "visitor")
    if PRESENT:
        modnpc.register(OWNER, key="visitor", fields=NPC_FIELDS,
                        prompt=on_prompt, on=HOOKS, write=write)

    def cleanup(app):
        """`PRESENT` を切った注入で、前の世代が置いたものを片付けて結果を録る。"""
        npc_id = seen["npc_id"]
        owner_id = next((k for k in modnpc.entries(OWNER) if not modnpc.is_mod_npc(k)), None)
        owner_before = frames.text_of(ui.character_of(app, owner_id), "profile") if owner_id else None
        dropped = modnpc.unregister(OWNER, app=app, write=write)
        # 被せは notes なので本物の profile には最初から印が無い。mark は「無い」が正しい。
        roster = getattr(getattr(app, "world", None), "characters", None)
        plain = (getattr(app, "save_data_dict", None) or {}).get("npcs")
        world_plain = (getattr(app, "world_dict", None) or {}).get("npcs")
        facility = getattr(getattr(app, "player", None), "location", None)
        owner_after = frames.text_of(ui.character_of(app, owner_id), "profile") if owner_id else None
        note("cleanup", dropped=dropped,
             in_roster=isinstance(roster, dict) and npc_id in roster,
             in_facility=npc_id in [str(k) for k in (getattr(facility, "characters", None) or [])],
             in_save_data_npcs=isinstance(plain, dict) and npc_id in plain,
             in_world_dict_npcs=isinstance(world_plain, dict) and npc_id in world_plain,
             registry=sorted(modnpc.registry()),
             owner=owner_id,
             owner_mark_before=bool(owner_before and OVERRIDE_MARK in owner_before),
             owner_mark_after=bool(owner_after and OVERRIDE_MARK in owner_after))
        write("cleanup: dropped={} roster={} facility={} plain(save={}, world={}) registry={} "
              "owner {} mark {} -> {}".format(
                  dropped, isinstance(roster, dict) and npc_id in roster,
                  npc_id in [str(k) for k in (getattr(facility, "characters", None) or [])],
                  isinstance(plain, dict) and npc_id in plain,
                  isinstance(world_plain, dict) and npc_id in world_plain,
                  sorted(modnpc.registry()), owner_id,
                  bool(owner_before and OVERRIDE_MARK in owner_before),
                  bool(owner_after and OVERRIDE_MARK in owner_after)))

    # -- 置く ---------------------------------------------------------------
    def where(app):
        """プレイヤーの居る `(エリア id, 施設 id)`。読めなければ `(None, None)`。"""
        area = ui.current_area(app)
        area_id = ui.area_id_of(area)
        player = getattr(app, "player", None)
        facility_id = ui.element_id(getattr(player, "location", None))
        if not area_id or not facility_id:
            return None, None
        return area_id, facility_id

    def follow(app):
        """居る施設へ連れて回り、その施設の主に被せを載せる。"""
        if not FOLLOW or not PRESENT:
            return
        area_id, facility_id = where(app)
        if area_id is None:
            return
        spot = (area_id, facility_id)
        if spot == seen["spot"]:
            return
        seen["spot"] = spot
        npc_id = seen["npc_id"]
        modnpc.spawn(app, npc_id, write=write)
        placed = modnpc.place(app, npc_id, area_id, facility_id, write=write)
        note("place", npc=npc_id, area=area_id, facility=facility_id,
             placed=placed)
        dress_owner(app, area_id, facility_id)
        if AUTO_TALK and seen["auto"] is None and seen["owner"]:
            seen["auto"] = "list"
            write("auto talk: {}s of grace, then waiting for the llm and idle".format(
                AUTO_TALK_DELAY))
            screen.schedule(lambda *_: wait_llm(app, npc_id, 0), AUTO_TALK_DELAY)

    def wait_llm(app, npc_id, waited):
        """LLM の口が生えるまで2秒おきに見る（上限 300 秒）。"""
        if stale():
            return
        if not llm_ready():
            if waited >= 300:
                write("auto talk: the llm never became ready; giving up")
                return
            if waited == 0:
                write("auto talk: waiting for the llm")
            screen.schedule(lambda *_: wait_llm(app, npc_id, waited + 2), 2.0)
            return
        screen.when_idle(app, lambda: open_talk_list(app, npc_id),
                         proceed_on_timeout=True, cancel_if=stale, tag="auto talk")

    def dress_owner(app, area_id, facility_id):
        """その施設の主に `notes` の層を載せる。前の相手の層は外す。

        `profile` は頼み文に毎回全文載る（GAME.md §2.25）ので、
        複製の末尾に1行足せば「被せが読む側まで届いたか」が `output_data` で分かる。
        """
        if not OVERRIDE:
            return
        facility = modnpc.facility_of(app, area_id, facility_id)
        owner_id = str(getattr(facility, "owner", "") or "")
        if owner_id == seen["owner"]:
            return
        if seen["owner"]:
            modnpc.unregister(OWNER, seen["owner"], app=app, write=write)
        seen["owner"] = owner_id or None
        if not owner_id or modnpc.is_mod_npc(owner_id):
            return
        character = ui.character_of(app, owner_id)
        profile = frames.text_of(character, "profile")
        if not profile:
            warn("no profile", "the owner {} has no profile to dress".format(
                owner_id))
            return
        # 被せは `notes`（頼み文を組む瞬間、相手の複製の profile の末尾に足す）。
        # 本物の profile には触らない。
        modnpc.register(OWNER, npc_id=owner_id, prompt=on_prompt, on=HOOKS,
                        notes=lambda info: OVERRIDE_MARK, write=write)
        note("dress", npc=owner_id, chars=len(profile))
        write("the owner {} {!r} wears the mark".format(
            owner_id, frames.short(frames.text_of(character, "name"), 20)))

    # -- 話し相手の一覧は何から組まれるか -----------------------------------
    class ReadCountingList(list):
        """`Facility.characters` の代役。読まれた回数だけ数える（`228_` の手口）。"""
        reads = 0

        def __iter__(self):
            type(self).reads += 1
            return list.__iter__(self)

        def __contains__(self, item):
            type(self).reads += 1
            return list.__contains__(self, item)

        def __getitem__(self, index):
            type(self).reads += 1
            return list.__getitem__(self, index)

        def __len__(self):
            type(self).reads += 1
            return list.__len__(self)

    @ctx.wrap(TALK_TARGET, required=False, safe=True)
    def talk_choice(orig, self, *args, **kwargs):
        """「会話する」の一覧が組まれる間、施設の名簿が読まれるかを見る。

        一覧に出た相手（`ConversationStartManager` の args[0]）と、
        そのとき施設の `characters` に居た id を並べて録る。
        来訪者が名簿に居るのに一覧に出なければ、一覧は名簿から組まれていない。
        """
        app = getattr(self, "app", None) or ui.find_app()
        facility = getattr(getattr(app, "player", None), "location", None)
        roster = getattr(facility, "characters", None)
        swapped = isinstance(roster, list)
        if swapped:
            ReadCountingList.reads = 0
            facility.characters = ReadCountingList(roster)
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                if swapped:
                    facility.characters = list(facility.characters)
                offered = [str(ui.spec_args(entry)[0])
                           for entry in (getattr(app, "buttons", None) or [])
                           if ui.spec_cls_name(entry) == "ConversationStartManager"
                           and ui.spec_args(entry)]
                note("talk_choice", offered=offered,
                     facility_characters=[str(k) for k in (roster or [])],
                     owner=str(getattr(facility, "owner", None)),
                     roster_reads=ReadCountingList.reads if swapped else None,
                     visitor_offered=seen["npc_id"] in offered)
                write("talk choice: offered={} roster={} reads={}".format(
                    offered, [str(k) for k in (roster or [])],
                    ReadCountingList.reads if swapped else "?"))
            except Exception:
                ctx.log_exc("mod npc: cannot record the talk choice")

    def in_roster(app):
        characters = getattr(getattr(app, "world", None), "characters", None)
        return isinstance(characters, dict) and seen["npc_id"] in characters

    def offered_ids(app):
        return [str(ui.spec_args(entry)[0])
                for entry in (getattr(app, "buttons", None) or [])
                if ui.spec_cls_name(entry) == "ConversationStartManager"
                and ui.spec_args(entry)]

    def wrap_talk_step(target, label):
        """`DisplayTalkChoice` の各段が呼ばれたかと、その後の一覧・名簿を録る。"""
        @ctx.wrap(target, required=False, safe=True)
        def step(orig, self, *args, **kwargs):
            app = getattr(self, "app", None) or ui.find_app()
            before = in_roster(app)
            try:
                return orig(self, *args, **kwargs)
            finally:
                try:
                    note("talk_step", step=label, offered=offered_ids(app),
                         visitor_in_roster_before=before,
                         visitor_in_roster_after=in_roster(app),
                         visitor_offered=seen["npc_id"] in offered_ids(app))
                    write("talk step {}: offered={} visitor in roster {}->{}".format(
                        label, offered_ids(app), before, in_roster(app)))
                except Exception:
                    ctx.log_exc("mod npc: cannot record the talk step")
        return step

    wrap_talk_step("__main__:DisplayTalkChoice.__init__", "init")

    # -- 一覧が何を読むか（`228_` の手口: 読まれる側に印を付ける）-----------------
    reads = []

    class LoggingDict(dict):
        _tag = "dict"

        def __getitem__(self, key):
            reads.append((self._tag, "[{!r}]".format(key)))
            return dict.__getitem__(self, key)

        def get(self, key, default=None):
            reads.append((self._tag, ".get({!r})".format(key)))
            return dict.get(self, key, default)

        def __contains__(self, key):
            reads.append((self._tag, "{!r} in".format(key)))
            return dict.__contains__(self, key)

        def __iter__(self):
            reads.append((self._tag, "iter"))
            return dict.__iter__(self)

        def keys(self):
            reads.append((self._tag, ".keys()"))
            return dict.keys(self)

        def values(self):
            reads.append((self._tag, ".values()"))
            return dict.values(self)

        def items(self):
            reads.append((self._tag, ".items()"))
            return dict.items(self)

    class RosterSpy(LoggingDict):
        _tag = "world.characters"

    class NpcsSpy(LoggingDict):
        _tag = "save_data_dict['npcs']"

    def spy_visitor(character):
        """来訪者の実体だけ、属性読みを記録する派生に差し替える（自前の実体なので安全）。"""
        base = type(character)

        class VisitorSpy(base):
            def __getattribute__(self, name):
                if name not in ("__class__", "__dict__"):
                    reads.append(("visitor", "." + name))
                return base.__getattribute__(self, name)
        try:
            character.__class__ = VisitorSpy
            return base
        except Exception:
            return None

    @ctx.wrap("__main__:DisplayTalkChoice.update_button_display", required=False,
              safe=True)
    def talk_display(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        world = getattr(app, "world", None)
        save = getattr(app, "save_data_dict", None)
        facility = getattr(getattr(app, "player", None), "location", None)
        visitor = ui.character_of(app, seen["npc_id"])
        del reads[:]
        swapped = {}
        try:
            if isinstance(getattr(world, "characters", None), dict):
                swapped["roster"] = world.characters
                world.characters = RosterSpy(world.characters)
            if isinstance(save, dict) and isinstance(save.get("npcs"), dict):
                swapped["npcs"] = save["npcs"]
                save["npcs"] = NpcsSpy(save["npcs"])
            roster = getattr(facility, "characters", None)
            if isinstance(roster, list):
                swapped["facility"] = roster
                ReadCountingList.reads = 0
                facility.characters = ReadCountingList(roster)
            if visitor is not None:
                swapped["visitor_cls"] = spy_visitor(visitor)
        except Exception:
            ctx.log_exc("mod npc: cannot spy the talk list")
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                if "roster" in swapped:
                    world.characters = swapped["roster"]
                if "npcs" in swapped:
                    save["npcs"] = swapped["npcs"]
                if "facility" in swapped:
                    facility.characters = swapped["facility"]
                if swapped.get("visitor_cls") is not None:
                    visitor.__class__ = swapped["visitor_cls"]
                offered = offered_ids(app)
                folded = []
                for entry in reads:           # 連続する同じ読みは畳む
                    if not folded or folded[-1] != entry:
                        folded.append(entry)
                note("talk_reads", offered=offered, visitor_offered=seen["npc_id"] in offered,
                     facility_reads=ReadCountingList.reads if "facility" in swapped else None,
                     reads=["{} {}".format(tag, what) for tag, what in folded[:200]])
                write("talk list: offered={} facility roster reads={} reads={}".format(
                    offered, ReadCountingList.reads if "facility" in swapped else "?",
                    ["{} {}".format(tag, what) for tag, what in folded[:60]]))
            except Exception:
                ctx.log_exc("mod npc: cannot record the talk reads")

    # -- 自動の会話（画面を押せない環境用）--------------------------------------
    def stale():
        """前の世代の見張りは降りる（注入し直すと2世代が並走した。実機 2026-09-12）。"""
        return "superseded" if ctx.superseded() else None

    def open_talk_list(app, npc_id):
        """一覧の段。**自分からは開かない。**

        `process_choice(DisplayTalkChoice(app), "会話する")` で開かせた回から、
        その直後にゲームが別スレッドで走らせる `save_game` が戻らなくなった
        （実機 2026-09-12 16:44 以降、5本の保存が宙に浮き、ディスクは書かれなかった）。
        一覧の中身は `talk_step` の受動の記録（`__init__` / `update_button_display` の包み）で
        取れているので、ここでは開かずに来訪者へ進む。
        """
        seen["auto"] = "visitor"
        screen.when_idle(app, lambda: talk(app, npc_id, after_visitor),
                         proceed_on_timeout=True, cancel_if=stale,
                         tag="auto talk")

    def talk(app, npc_id, then):
        """`ConversationStartManager(app, id)` をゲームの経路で起こす（GAME.md §2.5）。"""
        cls = ui.cls_of("ConversationStartManager")
        if modnpc.is_mod_npc(npc_id) and not in_roster(app):
            # 名簿から消えていた（実機 2026-09-12: 一覧を開いた後の `KeyError`）。
            # 誰が消したかは `talk_step` の前後で分かる。ここでは組み直して続ける。
            note("auto", step=npc_id, vanished=True)
            write("auto talk: the visitor is not in the roster any more; respawning")
            modnpc.spawn(app, npc_id, write=write)
        name = ui.character_name(app, npc_id)
        if cls is None:
            note("auto", step=npc_id, error="no ConversationStartManager")
            return
        try:
            manager = cls(app, npc_id)
        except Exception as exc:
            note("auto", step=npc_id, error="{}: {}".format(type(exc).__name__, exc))
            write("auto talk: ConversationStartManager({!r}) raised {}: {}".format(
                npc_id, type(exc).__name__, exc))
            ctx.log_exc("mod npc: cannot build the conversation with {}".format(npc_id))
            return
        write("auto talk: starting with {} {!r}".format(npc_id, name))
        note("auto", step=npc_id, action="start")
        if not screen.start_phase(app, manager, name):
            note("auto", step=npc_id, error="process_choice failed")
            return
        # 第一声は別スレッドで LLM が回る。手が空いてから少し置いて閉じる。
        screen.when_idle(app, lambda: screen.schedule(
            lambda *_: close(app, npc_id, then), AUTO_TALK_LINGER),
            proceed_on_timeout=True, timeout=180, cancel_if=stale,
            tag="auto talk")

    def close(app, npc_id, then):
        entry = ui.find_spec_button(getattr(app, "buttons", None),
                                    "ConversationEndManager")
        note("auto", step=npc_id, action="close",
             in_conversation=bool(getattr(app, "in_conversation", False)),
             has_end_button=entry is not None)
        write("auto talk: closing {} (in_conversation={} end_button={})".format(
            npc_id, getattr(app, "in_conversation", None), entry is not None))
        if entry is None:
            then(app)
            return
        screen.end_conversation(app, entry, then,
                                end_text="<行動: 会話を終了する>",
                                on_abort=lambda why: then(app))

    def after_visitor(app):
        if stale():
            write("auto talk: cancelled (superseded)")
            return
        owner = seen["owner"]
        if not owner or modnpc.is_mod_npc(owner) or seen["auto"] == "done":
            seen["auto"] = "done"
            write("auto talk: done (no owner to talk to)")
            return
        seen["auto"] = "owner"
        talk(app, owner, after_owner)

    def after_owner(app):
        seen["auto"] = "done"
        write("auto talk: done")

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False,
              safe=True)
    def refresh(orig, self, *args, **kwargs):
        """選択肢が組み直されるたびに居場所を見る（`914_` と同じ契機）。"""
        result = orig(self, *args, **kwargs)
        try:
            follow(self)
        except Exception:
            ctx.log_exc("mod npc: cannot follow the player")
        return result

    # -- 保存に漏れていないか -----------------------------------------------
    @ctx.wrap("__main__:InstantaleApp.save_game", required=False)
    def save_game(orig, self, *args, **kwargs):
        """保存の直後にディスクのセーブを読む。関所の外側から数える。

        16:43 以降、包みは呼ばれるのに読み直しが1度も走らなかった（実機 2026-09-12）。
        `orig` が投げてここを素通りしている可能性があるので、入り・戻り・投げを1行ずつ残す。
        """
        write("save: entering (in_conversation={} thread={})".format(
            getattr(self, "in_conversation", None),
            __import__("threading").current_thread().name))
        try:
            result = orig(self, *args, **kwargs)
        except BaseException as exc:
            write("save: the game's save_game raised {}: {}".format(
                type(exc).__name__, frames.short(str(exc), 120)))
            raise
        write("save: returned {!r}".format(frames.short(repr(result), 60)))
        try:
            check_disk(self)
        except Exception:
            ctx.log_exc("mod npc: cannot read the save back")
        return result

    def check_disk(app):
        """書かれたセーブに `mod:` の鍵が残っていないかを数える。"""
        if seen["disk"] >= DISK_CHECKS:
            return
        key = state.world_key(app)
        folder = next((name for world, name in saves.world_names()
                       if world == key), None)
        if folder is None:
            warn("no save", "the save folder of {!r} was not found".format(key))
            return
        data = saves.read_save(folder)
        if not isinstance(data, dict):
            warn("unreadable", "the save of {!r} could not be read".format(key))
            return
        seen["disk"] += 1
        leaked = {}
        for name in ("npcs", "characters"):
            store = data.get(name)
            if isinstance(store, dict):
                leaked[name] = sorted(k for k in store
                                      if modnpc.is_mod_npc(str(k)))
        # `npcs` と `characters` だけでは足りない。
        # `game_variables` の選択肢（`buttons_backup` の `ConversationStartManager`）・
        # 自由入力・パーティ・敵にも id が載る（2026-09-13 に実セーブで確認）。
        # セーブ全体を1つの文字列にして `mod:` を数え、出どころの鍵も並べる。
        whole = json.dumps(data, ensure_ascii=False, default=str)
        leaked["anywhere"] = whole.count(modnpc.PREFIX)
        if leaked["anywhere"]:
            leaked["where"] = sorted(_where_prefix(data))
        counts = {name: len(data.get(name) or {}) for name in
                  ("npcs", "characters") if isinstance(data.get(name), dict)}
        note("disk", world=key, counts=counts, leaked=leaked,
             lift_roster=modnpc.LIFT_ROSTER, registry=sorted(modnpc.registry()))
        write("save on disk: {} / leaked {}".format(
            counts, leaked or "nothing"))

    # 注入した時点で既に施設に居ることがある（注入し直したとき）。
    # 選択肢の組み直しを待たず、1度だけ到着の処理を起こす。
    def llm_ready():
        """LLM の口があるか。`send_request*` の別名はプロバイダの初期化で後から生える（GAME.md §2.12）。"""
        return llm.resolve_send()[0] is not None

    def prepare_llm(app):
        """`AIManager.set_ai_models()` を別スレッドで。

        タイトルのロードは LLM を用意してから世界を読むが、`load_game_new` を直に呼ぶと
        そこを飛ばす（実機 2026-09-12: `conversation_starter` が
        `'NoneType' object is not callable`、4分半待っても変わらず）。
        """
        # 属性は2つある（`ai_mamanger` と `ai_manager`。`210_` の97属性の記録）。
        # どちらが実体かは決めつけず、`set_ai_models` を持つほうを使う。
        fn = None
        for name in ("ai_manager", "ai_mamanger"):
            manager = getattr(app, name, None)
            write("auto load: app.{} = {}".format(name, type(manager).__name__))
            candidate = getattr(manager, "set_ai_models", None)
            if callable(candidate):
                fn = candidate
                break
        if fn is None:
            # 正規の起動が組む `AIManager(app, config)` が無い（`ai_manager` は None。
            # 実機 2026-09-12 の診断）。同じ引数で組んで、同じ属性に置く。
            diagnose_llm(app)
            cls = ui.cls_of("AIManager")
            config = getattr(getattr(app, "auto_configuration_screen", None), "config", None)
            if config is None:
                config = getattr(app, "config", None)
            if cls is None or config is None:
                write("auto load: cannot build AIManager (cls={} config={})".format(
                    cls, type(config).__name__))
                return
            try:
                manager = cls(app, config)
            except Exception:
                ctx.log_exc("mod npc: AIManager(app, config) failed")
                return
            app.ai_manager = manager
            fn = getattr(manager, "set_ai_models", None)
            write("auto load: built AIManager with config {}".format(type(config).__name__))
            if not callable(fn):
                return
        def run():
            try:
                fn()
                write("auto load: set_ai_models returned; llm ready={}".format(llm_ready()))
            except Exception:
                ctx.log_exc("mod npc: set_ai_models failed")
        write("auto load: set_ai_models in a thread")
        threading.Thread(target=run, name="mod npc set_ai_models", daemon=True).start()

    def diagnose_llm(app):
        """LLM の口がどこで生えるのかを掴むための記録（1度きり）。"""
        import sys as _sys
        for name in ("ai_manager", "ai_mamanger", "start_screen", "world_choice_app",
                     "auto_configuration_screen", "initial_setting_screen"):
            obj = getattr(app, name, None)
            public = sorted(a for a in dir(obj) if not a.startswith("_"))[:60] if obj is not None else []
            write("diag: app.{} = {} {}".format(name, type(obj).__name__, public))
        module = _sys.modules.get("scripts.llm.llm_manager")
        if module is not None:
            nones = sorted(k for k, v in vars(module).items()
                           if v is None and not k.startswith("_"))
            write("diag: llm_manager globals that are None: {}".format(nones))
        loaded = sorted(m for m in _sys.modules if "request_llm" in m or "llama_cpp" in m)
        write("diag: provider modules loaded: {}".format(loaded))
        cls = ui.cls_of("AIManager")
        write("diag: AIManager class {} public {}".format(
            cls, sorted(a for a in dir(cls) if not a.startswith("_")) if cls else None))

    def arrive_or_load():
        app = ui.find_app()
        if app is None:
            return
        if getattr(app, "world", None) is None and AUTO_LOAD_WORLD:
            # タイトル画面。`load_game_new` はロードボタンと同じ入口（`107_` / `120_` が包む）。
            write("auto load: load_game_new({!r})".format(AUTO_LOAD_WORLD))
            try:
                app.load_game_new(AUTO_LOAD_WORLD)
            except Exception:
                ctx.log_exc("mod npc: load_game_new failed")
            if not llm_ready():
                prepare_llm(app)
            return
        if not llm_ready() and AUTO_LOAD_WORLD:
            # 世界は読めているのに LLM の口が無い（注入し直した回）。用意を試みる。
            prepare_llm(app)
        if not PRESENT:
            cleanup(app)
            return
        follow(app)

    ctx.on_ready(arrive_or_load,
                 key="229_probe_mod_npc:{}".format(ctx.generation), delay=1.0)

    write("ready: visitor={} present={} follow={} override={} try_detail={} auto_talk={} "
          "lift_roster={}".format(seen["npc_id"], PRESENT, FOLLOW, OVERRIDE, TRY_DETAIL,
                                  AUTO_TALK, LIFT_ROSTER))


def _where_prefix(data, path="", found=None):
    """セーブの中で `mod:` が現れるトップレベルの鍵。**辞書の鍵も見る**（id は鍵で載る）。"""
    if found is None:
        found = set()

    def note(where):
        found.add(where.split("/")[1] if "/" in where else (where or "<root>"))

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and modnpc.PREFIX in key:
                note(path)
            _where_prefix(value, "{}/{}".format(path, key), found)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            _where_prefix(value, "{}/{}".format(path, index), found)
    elif isinstance(data, str) and modnpc.PREFIX in data:
        note(path)
    return found


def _flatten(messages):
    """頼み文を1つの文字列にする。数えるためだけに使う。"""
    if isinstance(messages, str):
        return messages
    if not isinstance(messages, (list, tuple)):
        return ""
    parts = []
    for entry in messages:
        if isinstance(entry, dict):
            value = entry.get("content")
            parts.append(value if isinstance(value, str) else str(value))
        elif isinstance(entry, str):
            parts.append(entry)
    return "\n".join(parts)


def _profile_of(args):
    """引数に入っている相手の `profile`。読めなければ空文字。"""
    return frames.text_of(args.get("character_instance"), "profile")
