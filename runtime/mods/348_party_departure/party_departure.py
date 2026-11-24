# -*- coding: utf-8 -*-
"""機能追加: 依頼の後も仲間が残り、会話で別れられる。

`302_leave_party_in_conversation`・`303_quest_end_party_to_guild`・`304_quest_end_keep_party` を
1本に書き直したもの（3本は取込済）。機能は3つで、入切は設定で分ける。

| 機能 | 設定 | 置き場 |
|---|---|---|
| 依頼が終わっても解散しない | `KEEP_PARTY` | このファイル |
| 別れた仲間を会いに行ける場所に残す | `STAY_NEARBY` | このファイル（依頼の解散）と `farewell.py`（会話） |
| 会話から仲間と別れる | `FAREWELL_ENABLED` | `farewell.py` |

## 3つの機能が同じフックを分け合う

仲間が外れる経路は、どれも `remove_party_member` の後に呼び出し元が `move_npc_to_facility` で置き直す形（GAME.md §2.8）。
フックはこの2つと `get_party_leave_facility` の3つだけにして、場面で振り分ける。

| 場面 | 見分け方 | `remove_party_member` | `move_npc_to_facility` |
|---|---|---|---|
| 依頼の解散・解散しない | スタックに解散のマネージャ | 通さない（名簿はそのまま） | 通さない。離脱の文も差し替える |
| 依頼の解散・近くに残す | 同上 | 通す | 置き先を差し替える |
| 会話の別れ | `farewell.py` が控えた相手 | 通す | 通す。ゲームが置いたら二重に置かない |
| それ以外（死別など） | どれにも当たらない | 通す。控えを落とす | 通す |

解散の中かどうかは `frames.MethodWatch` で見る（コードオブジェクトの同一性。段数でも関数名でもない。GAME.md §2.8）。
`move_npc_to_facility` は NPC の日常の移動でも呼ばれるので、そこではスタックを見ずに控えの辞書を引くだけにする。
控えは `HOLD_SECONDS` で切れる。置き直しが来ないまま残って、後の普段の移動を巻き込まないため。

## 依頼の解散での置き先

雇った土地で解散したなら、ゲームの置き先（雇った場所。GAME.md §2.8）をそのまま使う。
土地を跨いでいたら、いまの町のギルドへ差し替える。
ギルドが無い土地（ダンジョンなど）ではゲームの置き先に任せる。
"""

import time

from instantale_modloader import frames, text_filters, ui

from . import farewell
from .places import character_key, home_of, reshape

OWNER = "348_party_departure"

# `302_` / `303_` / `304_` と同じ名前（パーティの増減・解散・引き留めを1つの時系列で読む）。
LOG_BASENAME = "party_leave.log"

# ---------------------------------------------------------------- 設定（mod.json）
# 会話の相手が仲間なら「ここで別れる」を出す（`farewell.py`）。
FAREWELL_ENABLED = True
# 依頼のクリアでパーティを解散しない。
KEEP_PARTY = True
# 別れた仲間を、雇った土地なら雇った場所、土地を跨いでいたらいまの町のギルドに残す。
# 切るとゲームのまま（雇った場所へ帰る）。会話の別れと依頼の解散の両方に効く。
STAY_NEARBY = True
# 依頼の放棄（`QuestRetireManager`）でも、クリアと同じ扱いにする。
ALSO_ON_QUEST_RETIRE = False
# 依頼の解散で置き先を差し替えたとき、行き先を本文に出す（ゲーム自身は行き先を言わない）。
ANNOUNCE_DESTINATION = True
# 解散を止めたときに「…はパーティから離脱した。」の代わりに出す文。
KEEP_TEXT = "{name}はパーティに残り、引き続き行動を共にすることになった。"

# ---------------------------------------------------------------- コード側の設定
DISBAND_MANAGERS = ("QuestEndManager",)
RETIRE_MANAGERS = ("QuestRetireManager",)
# 解散そのものは `method_1` の中だが、どちらの層から呼ばれるかは決めつけない。
WATCH_METHODS = ("method_1", "execute")
MAX_STACK = 60

# 控えを持つ秒数。実機では外した直後に置き直しと離脱の文が来るので、数ミリ秒で足りる。
HOLD_SECONDS = 30.0

# 離脱を告げる文の手掛かり。相手の名前が入っていることも併せて見る。
LEAVE_TEXT_HINTS = ("パーティから離脱", "left the party")
STAY_TEXT = "{name}は{place}に留まることになった。"

# `InstantaleApp.move_npc_to_facility` の引数の並び（`self` を除く。recon の signatures.json）。
MOVE_ARGS = ("character_id", "character_instance", "target_facility", "target_node",
             "register_facility")

GUILD_FACILITY_TYPE = ui.GUILD_FACILITY_TYPE


class _Settings(object):
    """入口のモジュールの定数を、その場で読む窓。

    ローダは apply() の前に設定を定数へ書き込む。`farewell.py` は値を控えず、使うたびにここから読む。
    """

    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name)


class Shared(object):
    """機能のあいだで共有する道具と控え。"""

    def __init__(self, ctx, cfg):
        self.ctx = ctx
        self.cfg = cfg
        self.write = ctx.logger(LOG_BASENAME)
        self.screen = ui.Screen(ctx, self.write, tag="party departure")
        # 解散を止めた相手。id -> {"name", "at"}。
        self.held = {}
        # 依頼の解散で置き先を差し替える相手。id -> {"facility", "node", "name", "at"}。
        # `facility` が None の控えは「ゲームの置き先のままにする」と決めた印。
        self.redirects = {}
        # 会話で別れている最中の相手。id -> {"moved": ゲームが自分で置いたか}。
        self.departing = {}


def fresh(table, key):
    """控えを引く。`HOLD_SECONDS` を過ぎたものはここで落とす。"""
    entry = table.get(key)
    if entry is None:
        return None
    if time.monotonic() - entry["at"] > HOLD_SECONDS:
        table.pop(key, None)
        return None
    return entry


def apply(ctx):
    cfg = _Settings()
    shared = Shared(ctx, cfg)
    write, screen = shared.write, shared.screen

    managers = list(DISBAND_MANAGERS) + (list(RETIRE_MANAGERS) if ALSO_ON_QUEST_RETIRE else [])
    disband = frames.MethodWatch(managers, WATCH_METHODS, max_stack=MAX_STACK, on_warn=write)

    def in_disband():
        """いま依頼の解散の中か。中なら `'QuestEndManager.method_1'` のような名前を返す。

        依頼の解散に手を入れる設定がどちらも切なら、スタックを見に行かない。
        """
        if not (cfg.KEEP_PARTY or cfg.STAY_NEARBY):
            return None
        return disband.current()

    # ------------------------------------------------------------ 解散しない
    def hold(app, key, source, why):
        entry = shared.held.get(key)
        if entry is None:
            # 離脱の文との照合に使うので、名前は切らない
            entry = {"name": ui.character_name(app, key, limit=None)}
            shared.held[key] = entry
            write("quest end: {!r} ({}) stays in the party ― {} {}".format(entry["name"], key, source, why))
        entry["at"] = time.monotonic()

    def release(key):
        """解散の外で本当に外れる相手の控えを落とす（置き直しを止めると世界のどこにも居なくなる）。"""
        held = shared.held.pop(key, None)
        shared.redirects.pop(key, None)
        if held is not None:
            write("quest end: {!r} ({}) is leaving for real; not holding them any more"
                  .format(held["name"], key))

    def keep_text(context, app):
        """「…はパーティから離脱した。」を、残った旨の文に差し替える（ローダの窓口 `text_filters`）。

        引き留めた相手の名前が入った離脱の文だけを見る。控えが空なら何もしない。
        """
        if not shared.held or not cfg.KEEP_TEXT or not isinstance(context, str):
            return None
        if not any(hint in context for hint in LEAVE_TEXT_HINTS):
            return None
        for key in list(shared.held):
            entry = fresh(shared.held, key)
            if entry is not None and entry["name"] and entry["name"] in context:
                replaced = ui.fill_template(cfg.KEEP_TEXT, name=entry["name"])
                write("quest end: text {!r} -> {!r}".format(frames.short(context, 60), frames.short(replaced, 60)))
                return replaced
        return None

    text_filters.on(OWNER, ctx, keep_text, write=write)

    # ------------------------------------------------------ 近くに残す
    def plan_redirect(app, key, source):
        """依頼の解散で、その相手をどこに置くかを決めて控える。"""
        area = ui.current_area(app)
        here_id = ui.area_id_of(area)
        name = ui.character_name(app, key)
        entry = {"facility": None, "node": None, "name": name, "at": time.monotonic()}
        home_area, _home_facility = home_of(ui.character_of(app, key))
        if home_area and home_area == here_id:
            write("quest end: {!r} ({}) left the party in their home area {} via {}; "
                  "keeping the game's destination".format(name, key, here_id, source))
        else:
            facility, node = ui.find_guild(area, GUILD_FACILITY_TYPE)
            if facility is None:
                write("quest end: {!r} ({}) left the party in {} via {}, which has no {!r}; "
                      "keeping the game's destination".format(name, key, here_id or "?", source,
                                                              GUILD_FACILITY_TYPE))
            else:
                entry["facility"], entry["node"] = facility, node
                write("quest end: {!r} ({}) left the party in {} via {} -> {!r}".format(
                    name, key, here_id or "?", source, ui.facility_name(app, facility) or "the guild"))
        shared.redirects[key] = entry
        return entry

    def redirect_for(app, key, source):
        return fresh(shared.redirects, key) or plan_redirect(app, key, source)

    def announce(app, entry):
        if not cfg.ANNOUNCE_DESTINATION:
            return
        place = ui.facility_name(app, entry["facility"])
        if not place:
            return
        text = ui.fill_template(STAY_TEXT, name=entry["name"], place=place)
        # 依頼の終わりは報酬・才能・要約と本文が続くので、流し終わるのを待つ。
        # 既に決まった出来事なので、待ちきれなくても出す。
        screen.when_idle(app, lambda: screen.say(app, text),
                         proceed_on_timeout=True, tag="quest end announce")

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.remove_party_member", required=False)
    def remove_party_member(orig, self, *args, **kwargs):
        key = str(frames.arg(args, kwargs, "member_id", 0))
        source = None
        if key not in shared.departing:
            try:
                source = in_disband()
            except Exception:
                ctx.log_exc("party departure: cannot tell where remove_party_member was called from")
        if source is None:
            release(key)
            return orig(self, *args, **kwargs)
        if cfg.KEEP_PARTY:
            try:
                hold(self, key, source, "did not disband the party")
            except Exception:
                # 控えられなかったら止めない。置き直しだけが通って、
                # パーティに居るのに施設にも居る状態になるため。
                ctx.log_exc("party departure: cannot hold {!r}; letting the game disband".format(key))
                return orig(self, *args, **kwargs)
            return None
        created = key not in shared.redirects
        try:
            entry = redirect_for(self, key, source)
        except Exception:
            ctx.log_exc("party departure: cannot decide where {!r} goes".format(key))
            entry = None
        try:
            return orig(self, *args, **kwargs)
        except Exception:
            # 外せなかった相手はまだパーティに居る。控えを残すと、後の普段の移動を差し替えてしまう。
            if created and entry is not None and shared.redirects.get(key) is entry:
                shared.redirects.pop(key, None)
                write("quest end: {!r} ({}) was not removed (the game raised); dropped the plan"
                      .format(entry["name"], key))
            raise

    @ctx.wrap("__main__:InstantaleApp.get_party_leave_facility", required=False)
    def get_party_leave_facility(orig, self, *args, **kwargs):
        """依頼の解散の中で置き先を聞かれたら、解散しないなら相手を控え、近くに残すなら置き先を答える。

        実機は外してから置き直すので、ここまで来なくても `remove_party_member` 側で間に合う。
        置き先を聞いてから外すビルドでも取りこぼさないための先回り。
        """
        try:
            source = in_disband()
        except Exception:
            ctx.log_exc("party departure: cannot tell where get_party_leave_facility was called from")
            source = None
        if source is None:
            return orig(self, *args, **kwargs)
        key = character_key(frames.arg(args, kwargs, "character_instance", 0))
        if cfg.KEEP_PARTY:
            if key:
                try:
                    hold(self, key, source, "was asked where to leave them")
                except Exception:
                    ctx.log_exc("party departure: cannot hold {!r}".format(key))
            return orig(self, *args, **kwargs)
        try:
            value = orig(self, *args, **kwargs)
        except Exception:
            # 解散の中なら、こちらが答えれば先へ進める。
            ctx.log_exc("party departure: the game's get_party_leave_facility failed")
            value = None
        if not key:
            return value
        try:
            entry = redirect_for(self, key, source)
            if entry["facility"] is None:
                return value
            return reshape(value, entry["facility"], entry["node"])
        except Exception:
            ctx.log_exc("party departure: cannot answer get_party_leave_facility")
            return value

    @ctx.wrap("__main__:InstantaleApp.move_npc_to_facility", required=False)
    def move_npc_to_facility(orig, self, *args, **kwargs):
        """控えた相手の置き直しだけに手を入れる。控えが空なら何もせず抜ける。"""
        if not (shared.held or shared.redirects or shared.departing):
            return orig(self, *args, **kwargs)
        key = str(frames.arg(args, kwargs, "character_id", MOVE_ARGS))
        mine = shared.departing.get(key)
        if mine is not None:
            # 会話の別れの最中にゲームが自分で置いた。`farewell.py` は二重に置かない
            mine["moved"] = True
            return orig(self, *args, **kwargs)
        try:
            held = fresh(shared.held, key)
            entry = None if held is not None else fresh(shared.redirects, key)
        except Exception:
            ctx.log_exc("party departure: cannot tell whether {!r} was held".format(key))
            held = entry = None
        if held is not None:
            write("quest end: not placing {!r} anywhere: they are still in the party".format(held["name"]))
            return None
        if entry is None:
            return orig(self, *args, **kwargs)
        # 差し替えは1回だけ。その後の普段の移動は追わない
        shared.redirects.pop(key, None)
        if entry["facility"] is None:
            return orig(self, *args, **kwargs)
        target = frames.arg(args, kwargs, "target_facility", MOVE_ARGS)
        try:
            if target is entry["facility"]:
                write("quest end: {!r} is already headed for the guild".format(entry["name"]))
            else:
                write("quest end: {!r}: {!r} -> {!r}".format(
                    entry["name"], ui.facility_name(self, target) or target,
                    ui.facility_name(self, entry["facility"]) or "the guild"))
                # 受け取った形のまま渡す。ノードは省かれうる（既定 None）ので、来ていなければキーワードで足す
                args, kwargs, _ = frames.replace_arg(args, kwargs, "target_facility", MOVE_ARGS,
                                                     entry["facility"], insert=True)
                args, kwargs, _ = frames.replace_arg(args, kwargs, "target_node", MOVE_ARGS,
                                                     entry["node"], insert=True)
        except Exception:
            ctx.log_exc("party departure: cannot redirect {!r}".format(key))
        result = orig(self, *args, **kwargs)
        try:
            announce(self, entry)
        except Exception:
            ctx.log_exc("party departure: cannot announce the destination")
        return result

    farewell.install(shared)

    ctx.log("party departure: keep={} nearby={} farewell={} retire={} log={}".format(
        KEEP_PARTY, STAY_NEARBY, FAREWELL_ENABLED, ALSO_ON_QUEST_RETIRE, ctx.out_path(LOG_BASENAME)))
