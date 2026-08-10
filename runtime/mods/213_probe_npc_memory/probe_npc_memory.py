# -*- coding: utf-8 -*-
"""計測: ゲーム自身の「NPC の記憶」と、会話プロンプトの中の重複。

##### 何を決めるための計測か

`311_npc_profile_memory` は会話から人物像を抽出して `profile` に注入するが、
ゲーム自身にも会話を覚える仕組みがある。会話の終了時にはゲームが要約 LLM を
回し（GAME.md §2.5）、その結果は後の会話に影響する ―「同じ相手が前の話を
引きずる」のは確認済みで、`conversation_facilitator_after_retrieval` という
取り出しの経路まである（GAME.md §2.24）。NPC のセーブ項目にも `memory` /
`life_log` / `relationship` / `knowledge` が並ぶ（GAME.md §2.23）。

つまり同じ会話が「本体の要約 → memory」と「311_ の抽出 → profile 注入」の
2系統で覚えられている可能性があるが、**本体側の実体は分かっていない**。311_ の
重複対応（棲み分け・抽出タイミングの変更）は、この実体を見てから決める。

##### 3つの問いに答える

| 問い | 見るところ |
|---|---|
| `memory` 等の実体は何か・会話の終了で何が書かれるか | 会話系 LLM 呼び出しごとの項目スナップショットと、`app.in_conversation` が落ちた直後の差分 |
| それがプロンプトへどう載るか（毎回全文か・一部か） | `send_request*` に渡った本文への、各項目の抜き取り照合（含有判定） |
| プロンプト内の重複はどれだけか | 同一行の重複量と、311_ の注入見出しの出現数 |

##### どこで測るか

- 会話系5関数（`conversation_starter` / `conversation_facilitator` ほか。
  先頭4引数は `messages, character_life_log, player, character_instance` で
  共通 ― 311_ と同じ前提）を包み、**素の NPC** の記憶系項目を写す。
  この mod は 311_ より後に読み込む（外側で包む）ので、ここで見えるのは
  311_ が複製へ注入する**前**のゲーム自身の値。
- 完成したプロンプトは会話関数の中では見えない（あちらの `messages` は
  会話履歴で、profile や memory はゲームがその先で組み込む）。だから
  `llm_manager:send_request` / `send_request_with_no_structure` も包む。
  この2つは**プロバイダ初期化時に生える別名**なので、起動時に無ければ
  5秒ごとの見張りで生えた時点で包む（GAME.md §2.12・`111_` v5 と同じ手）。
- 会話の終了は `app.in_conversation` が落ちるのを Clock で見張る
  （GAME.md §2.5。落ちるのは要約が終わった後なので、その直後の差分に
  要約の書き込みが写る）。見張りは世代を鍵に登録し、注入し直されたら
  `ctx.superseded()` で止まる（TECH.md §3.6.1・`211_` の教訓）。
- 閉じるたびに **`conversation_resolver` が発火したか不発だったか**を
  その場で判定して書く（最終ターンより後に resolver の send が在ったか）。
  要約は必ずしも走らない（GAME.md §2.25）ので、どの抜け方で不発になるかを
  この行で特定する。resolver の呼び出し元も記録する ― 取りこぼしを MOD
  から回す修正を書くときの recon。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。値は書かず、記録に失敗しても本体は必ず呼ぶ。
`311_` の抽出呼び出し（`manager_name` が `mod_` で始まるもの）は測らない ―
このプローブの対象は**ゲーム自身**の経路。
"""

import datetime
import hashlib
import json
import sys
import threading
import time

from instantale_modloader import frames, llm, ui
from instantale_modloader.state import world_key

LOG_BASENAME = "npc_memory.log"

# 会話系の入口。5関数とも先頭4引数の並びが同じ（GAME.md §2.24・311_ の前提）。
CONV_MODULE = "scripts.llm.llm_manager"
CONV_TARGETS = (
    "conversation_starter",
    "conversation_starter_in_quest",
    "conversation_facilitator",
    "conversation_facilitator_after_retrieval",
    "conversation_facilitator_in_quest",
)

# 完成したプロンプトが通る送信境界。起動直後は別名がまだ無いことがある。
SEND_TARGETS = (
    "scripts.llm.llm_manager:send_request",
    "scripts.llm.llm_manager:send_request_with_no_structure",
)
# 別名の見張りの間隔と諦める時刻はローダが持つ（`llm.ALIAS_*`）。

# NPC 側で写す項目。前5つが「会話で増えるかもしれない」側、後2つは
# 「世界生成で決まり増えない」とされる側（311_ の前提の裏取り）。
WATCHED_FIELDS = ("memory", "life_log", "current_log", "relationship",
                  "knowledge", "profile", "personality")

# 初見の NPC は項目の中身まで書き出す（形を知るのが目的なので1回だけ）。
DUMP_CHARS = 1500
# 変化した項目の新しい中身は末尾だけ写す（要約の書き込みが末尾に足されるか、
# 全体が書き直されるかも、この抜粋と長さの変化で読める）。
CHANGE_TAIL_CHARS = 400
# 含有判定に使う、項目1つあたりの控えの上限。
KEEP_CHARS = 6000
# 含有判定の抜き取り1つの長さ。先頭・中央・末尾寄りの3箇所を照合する。
PROBE_CHUNK = 60
# 照合に使う文字列の葉の最短の長さと、項目1つから集める葉の上限。
MIN_LEAF_CHARS = 6
MAX_LEAVES = 24
# 重複と数える行の最短の長さ。短い行（「はい」等）は言い回しの偶然で重なる。
DUP_MIN_LINE = 30
# 重複行の内訳を書く件数。
DUP_TOP = 3
# 控えを持つ NPC の上限（会話した相手だけなので普通は数人）。
MAX_TRACKED_NPCS = 30

# 会話の終了の見張り。落ちた直後は後始末の最中なので、少し置いてから写す。
CLOSE_POLL_SECONDS = 2.0
CLOSE_DELAY_SECONDS = 2.0

# 311_ が注入するブロックの見出し（npc_profile_memory.py の定数と同じ文字列）。
# プロンプトの中の出現数を数えるだけで、311_ を import はしない（TECH.md §3.7）。
HEADING_311_PROFILE = "【会話から形成された追加プロフィール】"
HEADING_311_PLAYER = "【この人物から見た"


def _digest(text):
    """内容の同一性を短い指紋で比べる（全文を控え続けないため）。"""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]


def flat(value, limit=None):
    """どんな型でも1本のテキストにする。含有判定と指紋の共通の入口。

    list / dict は JSON にする（`life_log` や `knowledge` がどちらで来ても
    同じ照合が効く）。読めない型は repr に倒す。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=repr)
        except Exception:
            text = repr(value)
    return text if limit is None else text[:limit]


def kind_of(value):
    """型と大きさを1語で。`str(1234字)` / `list(3件, 456字)` の形。"""
    if value is None:
        return "None"
    if isinstance(value, str):
        return "str({}字)".format(len(value))
    if isinstance(value, (list, tuple, set, dict)):
        try:
            size = len(value)
        except Exception:
            size = "?"
        return "{}({}件, {}字)".format(type(value).__name__, size,
                                       len(flat(value)))
    return type(value).__name__


def one_line(text, limit=160):
    """抜粋を1行に畳む。改行は見た目で分かる形に置き換える。"""
    if not isinstance(text, str):
        text = str(text)
    # 改行は見える印に置き換える（1レコード1行を保つため）。**cp932 に入る
    # 文字を使う** ― ログを cp932 の端末やエディタで開く人が居る（§6.2）。
    text = text.replace("\r", "").replace("\n", "\n")
    return text if len(text) <= limit else text[:limit] + "…"


def leaf_texts(value, out=None):
    """dict / list の中の**文字列の葉**だけを集める。

    JSON の器のまま抜き取って照合すると2通りに誤る: 空の `[]` はプロンプトの
    どこにでも在るので「全文相当」に化け（偽陽性）、逆に中身のある dict は
    鍵や括弧を含む断片がプロンプトの**描画済み**の文には無いので「無し」に
    倒れる（偽陰性の疑い）。ゲームがプロンプトに埋めるとすれば葉の
    文なので、照合は葉で行う。`MIN_LEAF_CHARS` 未満の葉は数えない
    （「初対面」程度の短さは言い回しの偶然で当たる）。
    """
    if out is None:
        out = []
    if len(out) >= MAX_LEAVES:
        return out
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= MIN_LEAF_CHARS:
            out.append(text)
    elif isinstance(value, dict):
        for item in value.values():
            leaf_texts(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            leaf_texts(item, out)
    return out


def containment(needle, haystack):
    """`needle` が `haystack` にどれだけ入っているか。

    戻り値は (当たった数, 試した数)。短い項目は丸ごと1回、長い項目は
    先頭・中央・末尾寄りの3箇所から `PROBE_CHUNK` 字ずつ抜いて照合する。
    全文一致を試さないのは、途中で改行や整形を挟んで埋め込まれても
    「載っている」と読めるようにするため。
    """
    if not needle:
        return (0, 0)
    if len(needle) <= PROBE_CHUNK:
        return (1 if needle in haystack else 0, 1)
    hits = tries = 0
    for position in (0, max(0, len(needle) // 2 - PROBE_CHUNK // 2),
                     max(0, len(needle) - PROBE_CHUNK - 10)):
        chunk = needle[position:position + PROBE_CHUNK]
        if len(chunk) < PROBE_CHUNK // 2:
            continue
        tries += 1
        if chunk in haystack:
            hits += 1
    return (hits, tries)


def verdict(hits, tries):
    if tries == 0:
        return "空"
    if hits == tries:
        return "全文相当({}/{})".format(hits, tries)
    if hits:
        return "部分({}/{})".format(hits, tries)
    return "無し(0/{})".format(tries)


def duplicated_lines(joined):
    """同一行（`DUP_MIN_LINE` 字以上）の重複を数える。

    戻り値は (重複している行の種類数, 損している字数, 内訳上位)。
    「損」は2回目以降の出現ぶんの合計。302_ 等の言い回しの偶然を拾わない
    よう、短い行は数えない。
    """
    counts = {}
    for line in joined.split("\n"):
        line = line.strip()
        if len(line) >= DUP_MIN_LINE:
            counts[line] = counts.get(line, 0) + 1
    dups = [(line, count) for line, count in counts.items() if count >= 2]
    wasted = sum((count - 1) * len(line) for line, count in dups)
    top = sorted(dups, key=lambda item: (item[1] - 1) * len(item[0]),
                 reverse=True)[:DUP_TOP]
    return len(dups), wasted, top


def apply(ctx):
    # 錠は `ctx.logger` が中に持っている（この計測は別スレッドからも書く）。

    #: `npc` は "世界名:npc_id" -> {"name", "fields": {...}}。**鍵に世界名を
    #: 含める。** id だけで引くと、セーブを切り替えたときに別世界の別人を
    #: 同一人物として diff してしまう（別世界で同じ id が振られるため。
    #: 311_ が `world_key` で分けているのと同じ理由）。
    #: `by_thread` は会話関数の中で send_request が呼ばれたときに、どの NPC の
    #: 会話かを送信境界へ伝える（同じスレッドで降りてくる）。
    #: `in_conv` は前回見た `app.in_conversation`（落ちた瞬間の検出用）。
    #: `last_conv_call` / `last_resolver` は「この会話に要約が走ったか」の判定用
    #: （最終ターンの後に resolver が発火していれば要約された）。
    #: `last_world` は最後に会話ターンを見た世界。閉じの検出時に世界が
    #: 変わっていれば「放置されたままロードで消えた会話」と読める。
    state = {"npc": {}, "by_thread": {}, "in_conv": None, "sends": 0,
             "last_conv_call": None, "last_resolver": None, "last_world": None}

    write = ctx.logger(LOG_BASENAME, stamp=False)

    def stamp():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    # ボタンは出さない。Clock の `schedule` / `_interval` と例外の握りだけ借りる。
    screen = ui.Screen(ctx, write, tag="npc memory probe")

    # ------------------------------------------------------------ NPC の項目
    def npc_id_of(npc):
        """`world.characters` の鍵から引き、無ければ `.id`（311_ と同じ順）。"""
        app = ui.find_app()
        characters = getattr(getattr(app, "world", None), "characters", None) \
            if app is not None else None
        if isinstance(characters, dict):
            for key, value in characters.items():
                if value is npc:
                    return str(key)
        value = getattr(npc, "id", None)
        return str(value) if value is not None else "?"

    def capture_fields(npc):
        """記憶系項目を写す。**読むだけで書かない。**

        `leaves` は含有判定用の文字列の葉（`leaf_texts` の説明を参照）。
        """
        out = {}
        for name in WATCHED_FIELDS:
            value = getattr(npc, name, None)
            text = flat(value)
            out[name] = {"kind": kind_of(value), "digest": _digest(text),
                         "text": text[:KEEP_CHARS], "leaves": leaf_texts(value)}
        return out

    def dump_first_sight(npc_id, npc_name, fields):
        write("\n" + "=" * 72)
        write("[{}] first sight: {!r} ({})".format(stamp(), npc_name, npc_id))
        for name in WATCHED_FIELDS:
            field = fields[name]
            write("  {} = {}".format(name, field["kind"]))
            text = field["text"]
            if text:
                for line in text[:DUMP_CHARS].split("\n"):
                    write("      | " + line)
                if len(text) > DUMP_CHARS:
                    write("      | …（あと{}字）".format(len(text) - DUMP_CHARS))

    def note_changes(old_fields, new_fields, reason):
        """指紋が動いた項目だけを書く。**何も動かなくても、その事実を残す。**

        「会話しても memory が変わらない」は「変わる」と同じ重さの答え。
        """
        changed = []
        for name in WATCHED_FIELDS:
            old = old_fields.get(name) or {"kind": "", "digest": "", "text": ""}
            new = new_fields[name]
            if old["digest"] == new["digest"]:
                continue
            changed.append(name)
            write("  {}: {} -> {}".format(name, old["kind"], new["kind"]))
            tail = new["text"][-CHANGE_TAIL_CHARS:]
            write("      新: …{}".format(one_line(tail, CHANGE_TAIL_CHARS)))
            # 末尾追記か全体の書き直しかは、古い先頭が残っているかで読める。
            head = old["text"][:PROBE_CHUNK]
            if head:
                write("      旧の先頭は{}".format(
                    "残っている（追記に見える）" if head in new["text"]
                    else "消えている（書き直しに見える）"))
        if not changed:
            write("  変化した項目なし（{}）".format(reason))

    def remember(store_key, npc_id, npc_name, fields, reason):
        """控えと比べて差分を書き、控えを差し替える。鍵は「世界名:npc_id」。"""
        known = state["npc"].get(store_key)
        if known is None:
            if len(state["npc"]) >= MAX_TRACKED_NPCS:
                write("[{}] {!r} ({}) は控えの上限（{}人）で追跡しない".format(
                    stamp(), npc_name, npc_id, MAX_TRACKED_NPCS))
                return
            dump_first_sight(npc_id, npc_name, fields)
        else:
            write("\n[{}] {}: {!r} ({})".format(stamp(), reason, npc_name, npc_id))
            note_changes(known["fields"], fields, reason)
        state["npc"][store_key] = {"name": npc_name, "fields": fields}

    # ------------------------------------------------------------ 会話系の入口
    def describe_conv_call(fn_name, args, kwargs):
        """会話関数1回ぶんの引数の実体を書き、スレッドの控えを返す。"""
        npc = kwargs.get("character_instance")
        if npc is None and len(args) >= 4:
            npc = args[3]
        if npc is None:
            write("[{}] {}: character_instance が見つからない "
                  "(args={} kwargs={})".format(stamp(), fn_name, len(args),
                                               sorted(kwargs)))
            return None
        npc_id = npc_id_of(npc)
        npc_name = one_line(flat(getattr(npc, "name", "")), 40) or "?"
        state["last_conv_call"] = time.monotonic()
        wkey = world_key(ui.find_app())
        state["last_world"] = wkey

        fields = capture_fields(npc)
        remember("{}:{}".format(wkey, npc_id), npc_id, npc_name, fields, fn_name)

        messages = args[0] if args else None
        if isinstance(messages, list):
            roles = {}
            chars = 0
            for message in messages:
                role = message.get("role", "?") if isinstance(message, dict) \
                    else type(message).__name__
                roles[role] = roles.get(role, 0) + 1
                chars += len(flat(message.get("content")
                                  if isinstance(message, dict) else message))
            write("  messages: {}件 {}字 ({})".format(
                len(messages), chars,
                ", ".join("{} x{}".format(k, v) for k, v in sorted(roles.items()))))
        else:
            write("  messages: {}".format(kind_of(messages)))

        # 第2引数のライフログは**誰のものか**を突き合わせる。NPC の
        # `life_log` と一致するのか、プレイヤー側なのかで読み方が変わる。
        if len(args) >= 3:
            life_log = args[1]
            log_digest = _digest(flat(life_log))
            owners = []
            if log_digest == fields["life_log"]["digest"]:
                owners.append("npc.life_log と同内容")
            player_log = getattr(args[2], "life_log", None)
            if player_log is not None and _digest(flat(player_log)) == log_digest:
                owners.append("player.life_log と同内容")
            write("  character_life_log: {} [{}]".format(
                kind_of(life_log), " / ".join(owners) or "どちらとも不一致"))

        # 5番目以降は版で動きうるので、名前を決め打ちせず全部書く
        # （`retrieved_knowledge` / `job_knowledge` の実体と位置がここで分かる）。
        for index, value in enumerate(args[4:], start=4):
            write("  args[{}]: {} {!r}".format(
                index, kind_of(value), one_line(flat(value, 200), 120)))
        for key, value in sorted(kwargs.items()):
            if key == "character_instance":
                continue
            write("  kwargs[{}]: {} {!r}".format(
                key, kind_of(value), one_line(flat(value, 200), 120)))

        return {"fn": fn_name, "npc_id": npc_id, "npc_name": npc_name,
                "fields": fields}

    def make_conv_probe(fn_name):
        @ctx.wrap("{}:{}".format(CONV_MODULE, fn_name), required=False)
        def probe(orig, *args, **kwargs):
            token = threading.get_ident()
            try:
                context = describe_conv_call(fn_name, args, kwargs)
            except Exception:
                context = None
                ctx.log_exc("npc memory probe: {} record failed".format(fn_name))
            if context is not None:
                state["by_thread"][token] = context
            try:
                return orig(*args, **kwargs)
            finally:
                if context is not None:
                    state["by_thread"].pop(token, None)
        return probe

    for _fn_name in CONV_TARGETS:
        make_conv_probe(_fn_name)

    # ------------------------------------------------------------ 送信境界
    def inspect_send(target, args, kwargs):
        manager = args[0] if args else kwargs.get("manager_name")
        manager = manager if isinstance(manager, str) else repr(manager)
        # 311_ など MOD 自前の呼び出しは測らない（docstring のとおり）。
        if manager.startswith("mod_"):
            return
        context = state["by_thread"].get(threading.get_ident())
        # 会話の外の推論（クエスト進行など）は 204_ の領分なので黙って通す。
        if context is None and "conversation" not in manager:
            return

        messages = None
        if len(args) >= 2 and isinstance(args[1], list):
            messages = args[1]
        elif isinstance(kwargs.get("message"), list):
            messages = kwargs["message"]
        elif isinstance(kwargs.get("messages"), list):
            messages = kwargs["messages"]
        if messages is None:
            write("[{}] send {}: 本文がリストで見つからない (args={} kwargs={})"
                  .format(stamp(), manager, len(args), sorted(kwargs)))
            return

        joined = "\n".join(
            flat(message.get("content") if isinstance(message, dict)
                 else message) for message in messages)
        state["sends"] += 1
        write("\n[{}] send #{}: manager={!r} via {} messages={}件 {}字".format(
            stamp(), state["sends"], manager,
            target.rpartition(":")[2], len(messages), len(joined)))

        # resolver の発火時刻と呼び出し元。前者は会話の閉じ方ごとの
        # 発火/不発の判定（`after_close`）、後者は「取りこぼしを MOD から
        # 回す」修正（方向1）のための recon。
        if manager == "conversation_resolver":
            state["last_resolver"] = time.monotonic()
            write("  呼び出し元: {}".format(frames.caller(depth=10)))

        profile_count = joined.count(HEADING_311_PROFILE)
        player_count = joined.count(HEADING_311_PLAYER)
        write("  311_の見出し: profile x{}, about_player x{}".format(
            profile_count, player_count))

        kinds, wasted, top = duplicated_lines(joined)
        write("  重複行(>= {}字): {}種 / 損 {}字".format(
            DUP_MIN_LINE, kinds, wasted))
        for line, count in top:
            write("      x{} ({}字) {!r}".format(
                count, len(line), one_line(line, 70)))

        if context is not None:
            # ★ ここが2つ目の問いの答えの出る場所。会話関数で写した**素の**
            #   項目が、完成したプロンプトのどこまで載っているか。照合は
            #   JSON の器ではなく文字列の葉で行う（`leaf_texts` の説明）。
            parts = []
            for name in WATCHED_FIELDS:
                hits = tries = 0
                for leaf in context["fields"][name]["leaves"]:
                    leaf_hits, leaf_tries = containment(leaf[:KEEP_CHARS], joined)
                    hits += leaf_hits
                    tries += leaf_tries
                parts.append("{}={}".format(name, verdict(hits, tries)))
            write("  {}({}) の載り方: {}".format(
                context["npc_name"], context["npc_id"], "  ".join(parts)))
        else:
            write("  （会話関数の外からの呼び出し。項目の照合は無し）")

    def hook_send(target):
        @ctx.wrap(target, required=False)
        def send_probe(orig, *args, **kwargs):
            try:
                inspect_send(target, args, kwargs)
            except Exception:
                ctx.log_exc("npc memory probe: send record failed")
            return orig(*args, **kwargs)
        return send_probe

    # 別名は初期化時に後から生える。**見張りはローダの語彙**
    # （`llm.watch_aliases`。`111_` / `119_` が使うのと同じ仕組み）。当て方だけは
    # こちらが持つ ― あちらは書き換えで、こちらはローカル実行でも生の引数を
    # 観測したいので、`wrap_outgoing` には乗せられない（TECH.md §5.3）。
    llm.watch_aliases(ctx, SEND_TARGETS, hook_send, label="npc memory probe")

    # ------------------------------------------------------------ 会話の終了
    def after_close(npc_id):
        """`in_conversation` が落ちた少し後。要約の書き込みがここに写る。"""
        app = ui.find_app()
        characters = getattr(getattr(app, "world", None), "characters", None) \
            if app is not None else None
        npc = characters.get(npc_id) if isinstance(characters, dict) else None
        if npc is None:
            write("[{}] 会話終了: NPC {!r} が world.characters に居ない".format(
                stamp(), npc_id))
            return
        npc_name = one_line(flat(getattr(npc, "name", "")), 40) or "?"
        wkey = world_key(app)
        # ★ この会話に要約が走ったかの即時判定。最終ターンより後に
        #   resolver が発火していれば「発火」。不発の閉じ方を特定する
        #   ための行なので、ここだけは太字級に目立たせる。
        last_call = state["last_conv_call"]
        last_resolver = state["last_resolver"]
        if state["last_world"] is not None and state["last_world"] != wkey:
            # 会話ターンを見た世界と違う世界で「閉じ」を検出した。会話を
            # 閉じずにタイトル/ロードへ抜けた形で、その会話は要約されて
            # いない。
            write("[{}] resolver: ★不発★ 会話を閉じないまま世界が変わった"
                  "（{} での会話は要約されずに消えた）".format(
                      stamp(), state["last_world"]))
        elif last_call is None:
            write("[{}] resolver: 判定不能（この起動で会話ターンを見ていない）"
                  .format(stamp()))
        elif last_resolver is not None and last_resolver >= last_call:
            write("[{}] resolver: 発火（最終ターンの {:.0f} 秒後）".format(
                stamp(), last_resolver - last_call))
        else:
            write("[{}] resolver: ★不発★ 最終ターン後に要約が走らないまま"
                  "会話が閉じた（この会話はゲーム側のどこにも要約されない）"
                  .format(stamp()))
        remember("{}:{}".format(wkey, npc_id), npc_id, npc_name,
                 capture_fields(npc), "会話終了の後")

    def close_tick(_dt):
        # 注入し直されたら止まる（世代の見張り。TECH.md §3.6.1・`211_` の教訓）。
        if ctx.superseded():
            return False
        try:
            app = ui.find_app()
            if app is None:
                return
            current = getattr(app, "in_conversation", None)
            previous = state["in_conv"]
            state["in_conv"] = current
            if previous and not current:
                npc_id = str(previous)
                screen.schedule(lambda: after_close(npc_id),
                                delay=CLOSE_DELAY_SECONDS)
        except Exception:
            ctx.log_exc("npc memory probe: close watch failed")

    ctx.on_ready(lambda: screen._interval(close_tick, CLOSE_POLL_SECONDS),
                 key="213_probe_npc_memory:poll:{}".format(ctx.generation))

    ctx.log("npc memory probe: conversation fields + prompt overlap "
            "go to out/{}".format(LOG_BASENAME))
