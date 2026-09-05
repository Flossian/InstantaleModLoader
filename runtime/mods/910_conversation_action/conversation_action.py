# -*- coding: utf-8 -*-
"""会話中に ＜＞ / <> で包んだ入力を、会話の外の自由入力と同じ「行動」として処理させる。

素のゲームの会話には、入力を行動として処理する経路が既にある。
会話の LLM（`conversation_facilitator`）が `action.call_free_action=True` を返すと、
本体が上位の GM（`master_ai_facilitator_from_conversation`）を呼び、
そこには `move_item` / `move_gold` / `start_battle` など14種の権限がある（GAME.md §2.5 / §2.26）。

足りないのは2つで、この MOD はその2つだけを埋める。

1. 入るかどうかを LLM が決めていて、プレイヤーから明示する手が無い。
   素のプロンプトが「それ以外のあらゆる要求や取引: casual_response」と倒しているので、
   括弧で行動を書いても大半は台詞として返される（DOC.md §3 に計数）。
   → 入力全体が ＜＞ / <> で包まれていたら、facilitator の返答の
   `call_free_action` を True に書き換える。
2. 会話からの GM には会話ログしか渡らず、会話の外の自由入力が持つ
   `【プレイヤーキャラの行動入力】`（入力文・使用アイテム）に当たる材料が無い。
   `move_item` の `item_id` はアイテムの**名前**なので、名前を見せないと創作される。
   → GM を包み、`conversation_log` の末尾に行動の1行と所持品の1行を足す。
   系のプロンプトが「<> で区切られたテキストはシステム側が確定させた事実」と
   定めているので、その形で書く。

（）は既定では扱わない。
台詞に添える所作（「（微笑む）」）にも使われていて、全部を GM へ回すと
雑談のたびに数千字のプロンプトが `finished=true` まで回る。

半角 <> はゲーム自身が会話ログへ書く記法でもある
（`<行動: 話しかける>` / `<行動: ○○を売却した。>` / `<結果:成功>` / `<確率N%: 成功>`、
`300_` の `<状況: ...>`）。
中身がその見出しで始まるものは行動と見なさない（`extract_action`）。

書き換えは facilitator の**戻り値**に対して行うので、
同じ関数を包む他の MOD（`301_` / `311_` / `404_`）より外側に置く（`mod.json` の `after`）。
`404_` は横取りしたとき常に `call_free_action=False` を返すが、外側で書き換えれば通る。

会話の1手ごとに控え（`pending`）を作り直すので、
GM が `finished=false` で何度回っても同じ材料が載り、次の1手で消える。
会話の終了とタイトルへの復帰でも消す。
"""
import re
import typing

from instantale_modloader import frames, llm, ui

# ---------------------------------------------------------------- 設定（mod.json と同じ既定値）

#: （）も行動の目印として扱うか。
ACCEPT_PAREN = False

#: GM へ所持品の一覧を渡すか。
SHOW_INVENTORY = True

#: 渡す所持品の上限。0 で一覧そのものを出さない。
MAX_ITEMS = 30

#: 会話ログの末尾に足す行動の1行。`{action}` が括弧の中身。
NOTE_ACTION = "<システム: プレイヤーは会話の中で次の行動をとった。台詞ではなく行動として処理すること: {action}>"

#: 会話ログの末尾に足す所持品の1行。`{items}` が名前→説明の辞書。
NOTE_INVENTORY = "<システム: プレイヤーの所持品。move_item の item_id にはこの名前をそのまま使うこと: {items}>"

# ---------------------------------------------------------------- 定数

LOG_BASENAME = "conversation_action.log"

#: 開き括弧 → 許す閉じ括弧。全角と半角は混ざってよい（`＜渡す>`）。
ANGLE_OPEN = ("＜", "<")
ANGLE_CLOSE = ("＞", ">")
PAREN_OPEN = ("（", "(")
PAREN_CLOSE = ("）", ")")

#: ゲーム（と `300_`）が半角 <> で会話ログへ書く見出し。
#: この語で始まり、続きがコロンか無しか `N%` なら本体の記法。
GAME_LABELS = ("行動", "状況", "会話", "出来事の要約", "出来事", "要約", "結果", "確率")
LABEL_TAIL = re.compile(r"^(?:[:：]|\d+\s*[%％])")

#: facilitator。3つとも第1引数が `messages`（targets.txt）。
FACILITATORS = (
    "conversation_facilitator",
    "conversation_facilitator_after_retrieval",
    "conversation_facilitator_in_quest",
)

#: 会話からの GM。引数の並びは targets.txt の実シグネチャ（綴りはゲーム側のまま）。
GM_TARGETS = {
    "master_ai_facilitator_from_conversation": (
        "player", "player_life_log", "worldview", "facility_list", "npc_list",
        "npc_list_text", "conversation_log", "master_process_log"),
    "master_ai_faciltiator_from_conversation_in_quest": (
        "player", "quest_log_summary", "worldview", "facility_list", "npc_list",
        "npc_list_text", "conversation_log", "master_process_log"),
}


# ---------------------------------------------------------------- 目印の読み取り（純関数）

def extract_action(text, accept_paren=False):
    """入力全体が1組の括弧で包まれていれば中身を返す。そうでなければ None。

    - 全角・半角の ＜＞ / <>。`accept_paren` なら （） / () も
    - 台詞の末尾に添えた所作（「こんにちは（笑う）」）は対象外
    - 中に同じ族の閉じ括弧が出るもの（「<a> と <b>」）は対象外
    - ゲーム自身の記法（`<行動: ...>` `<結果:成功>` `<確率70%: 成功>` `<出来事>`）は対象外
    """
    if not isinstance(text, str):
        return None
    body = text.strip()
    if len(body) < 3:
        return None
    head, tail = body[0], body[-1]
    if head in ANGLE_OPEN:
        closers = ANGLE_CLOSE
    elif accept_paren and head in PAREN_OPEN:
        closers = PAREN_CLOSE
    else:
        return None
    if tail not in closers:
        return None
    inner = body[1:-1].strip()
    if not inner or any(ch in inner for ch in closers):
        return None
    if is_game_notation(inner):
        return None
    return inner


def is_game_notation(inner):
    """`<行動: 話しかける>` のような本体の見出しか。"""
    for label in GAME_LABELS:
        if inner.startswith(label):
            rest = inner[len(label):].lstrip()
            if not rest or LABEL_TAIL.match(rest):
                return True
    return False


def last_player_input(messages):
    """`messages` の末尾の user の本文。無ければ None。"""
    if not isinstance(messages, (list, tuple)):
        return None
    for message in reversed(messages):
        role = _get(message, "role")
        if role != "user":
            continue
        content = _get(message, "content")
        return content if isinstance(content, str) else None
    return None


def _get(obj, name, default=None):
    """辞書でも属性でも読む。無ければ `default`。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    value = frames.attr(obj, name, default)
    if isinstance(value, str) and value.endswith(" while reading>"):
        return default
    return value


def arg_of(args, kwargs, names, name):
    """位置引数・キーワード引数のどちらで来ても読む（`313_` と同じ理由）。"""
    if name in kwargs:
        return kwargs[name]
    try:
        index = names.index(name)
    except ValueError:
        return None
    return args[index] if len(args) > index else None


def replace_arg(args, kwargs, names, name, value):
    """来た側（位置かキーワード）に合わせて差し替えた `(args, kwargs)` を返す。"""
    if name in kwargs:
        replaced = dict(kwargs)
        replaced[name] = value
        return args, replaced
    index = names.index(name)
    if len(args) > index:
        replaced = list(args)
        replaced[index] = value
        return tuple(replaced), kwargs
    replaced = dict(kwargs)
    replaced[name] = value
    return args, replaced


# ---------------------------------------------------------------- 所持品

def inventory_items(player, limit):
    """`{名前: 説明}`。読めなければ空。並びは持ち物の辞書の順、`limit` で切る。"""
    items = {}
    if player is None or limit <= 0:
        return items
    inv = frames.attr(player, "inventory", None)
    if not isinstance(inv, dict):
        inv = frames.attr(inv, "inventory", None)
    if not isinstance(inv, dict):
        return items
    for item in inv.values():
        name = frames.text_of(item, "name")
        if not name or name in items:
            continue
        description = frames.text_of(item, "description") or ""
        items[name] = description
        if len(items) >= limit:
            break
    return items


def notes_for(action, player, show_inventory, limit):
    """会話ログへ足す行。行動の1行と、所持品があればその1行。"""
    notes = [NOTE_ACTION.format(action=action)]
    if show_inventory:
        items = inventory_items(player, limit)
        if items:
            notes.append(NOTE_INVENTORY.format(items=repr(items)))
    return notes


def with_notes(log, notes):
    """`conversation_log` の型（list / tuple / str）を保って末尾に足す。読めなければ None。

    本体のリストは会話の要約にも使われるので、書き足さず写しを作る。
    """
    if isinstance(log, (list, tuple)):
        return list(log) + list(notes)
    if isinstance(log, str):
        return log + "\n" + "\n".join(notes)
    return None


# ---------------------------------------------------------------- 本体

def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    st = {
        "pending": None,      # この1手で GM へ渡す行動。facilitator が立て、次の1手・会話終了で消える
        "structures": {},     # `llm.create_structure` で作った型の控え
    }

    def structure(name, fields):
        cached = st["structures"].get(name)
        if cached is not None:
            return cached
        made = llm.create_structure(ctx, name, fields, label="conversation action")
        if made is not None:
            st["structures"][name] = made
        return made

    def rebuilt(result, action_obj):
        """`call_free_action=True` にした同じ形の応答。作れなければ None。

        まず本体の応答の項目をそのまま書き換える（型が本体のまま残る）。
        書けない型（frozen）なら `404_` と同じく同じ項目の型を作って返す。
        """
        try:
            setattr(action_obj, "call_free_action", True)
            if _get(action_obj, "call_free_action") is True:
                return result
        except Exception:
            pass
        action_cls = structure("ConversationActionReply", {
            "type": (str, ...),
            "accepted": (typing.Optional[bool], ...),
            "statement": (str, ...),
            "call_free_action": (bool, ...),
        })
        if action_cls is None:
            return None
        response_cls = structure("ConversationActionResponse", {
            "content_violation": (bool, ...),
            "action": (action_cls, ...),
        })
        if response_cls is None:
            return None
        statement = _get(action_obj, "statement")
        accepted = _get(action_obj, "accepted")
        return response_cls(
            content_violation=False,
            action=action_cls(type=str(_get(action_obj, "type") or "casual_response"),
                              accepted=accepted if isinstance(accepted, bool) else None,
                              statement=statement if isinstance(statement, str) else "",
                              call_free_action=True))

    def make_facilitator(name):
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False, safe=True)
        def facilitate(orig, *args, **kwargs):
            messages = kwargs.get("messages", args[0] if args else None)
            action = extract_action(last_player_input(messages), ACCEPT_PAREN)
            # 新しい1手。前の手の控えはここで消える（retrieve → after_retrieval の
            # 2回目も同じ入力なので、読み直せば同じ値になる）。
            st["pending"] = None
            result = orig(*args, **kwargs)
            if action is None:
                return result
            if llm.truthy(_get(result, "content_violation"), unknown=False):
                write("{}: content_violation; left as is".format(name))
                return result
            action_obj = _get(result, "action")
            kind = _get(action_obj, "type")
            if kind == "retrieve":
                write("{}: retrieve first; after_retrieval decides".format(name))
                return result
            if llm.truthy(_get(action_obj, "call_free_action"), unknown=False):
                st["pending"] = action
                write("{}: free action already chosen: {!r}".format(name, action[:60]))
                return result
            replaced = rebuilt(result, action_obj)
            if replaced is None:
                write("WARN {}: cannot rewrite the response; left as talk: {!r}".format(
                    name, action[:60]))
                return result
            st["pending"] = action
            write("{}: forced free action (type={!r}): {!r}".format(name, kind, action[:60]))
            return replaced
        return facilitate

    def make_gm(name, names):
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False, safe=True)
        def facilitate(orig, *args, **kwargs):
            action = st.get("pending")
            if not action:
                return orig(*args, **kwargs)
            player = arg_of(args, kwargs, names, "player")
            if player is None:
                player = frames.attr(ui.find_app(), "player", None)
            notes = notes_for(action, player, SHOW_INVENTORY, MAX_ITEMS)
            log = arg_of(args, kwargs, names, "conversation_log")
            extended = with_notes(log, notes)
            if extended is None:
                write("WARN {}: conversation_log is {}; nothing added".format(
                    name, type(log).__name__))
                return orig(*args, **kwargs)
            args, kwargs = replace_arg(args, kwargs, names, "conversation_log", extended)
            write("{}: +{} line(s), {} chars".format(
                name, len(notes), sum(len(n) for n in notes)))
            return orig(*args, **kwargs)
        return facilitate

    for target in FACILITATORS:
        make_facilitator(target)
    for target, arg_names in GM_TARGETS.items():
        make_gm(target, arg_names)

    def clear(reason):
        if st["pending"] is not None:
            write("cleared ({})".format(reason))
        st["pending"] = None

    @ctx.wrap("__main__:ConversationEndManager.execute", required=False, safe=True)
    def end_conversation(orig, self, *args, **kwargs):
        clear("conversation end")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.return_to_title", required=False, safe=True)
    def return_to_title(orig, self, *args, **kwargs):
        clear("return to title")
        return orig(self, *args, **kwargs)

    ctx.log("910_conversation_action: installed (paren={}, inventory={}, max_items={})".format(
        ACCEPT_PAREN, SHOW_INVENTORY, MAX_ITEMS))
