# -*- coding: utf-8 -*-
"""910_conversation_action をゲーム抜きで通す。

    python tools/tests/test_wip_conversation_action.py

見ているのは、この MOD が自分で決めている所だけ。

  目印   … 入力全体を包む ＜＞ / <>（全角・半角・混在）だけを行動と読む。
           台詞に添えた所作・入れ子・ゲーム自身の記法（`<行動: ...>` ほか）は読まない
  書換   … facilitator の返答の `call_free_action` を True にする。
           content_violation / retrieve / もとから True の回は触らない
  材料   … GM の `conversation_log` の末尾に行動の1行と所持品の1行を足す。
           list / str の両方、上限、所持品なし、本体のリストを書き換えない
  片付け … 次の1手・会話終了・タイトル復帰で控えが消える
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402
from instantale_modloader import llm         # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pick *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


MOD_PATH = find_mod("_conversation_action")
MANIFEST_PATH = os.path.join(os.path.dirname(MOD_PATH), "mod.json")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


spec = importlib.util.spec_from_file_location("conversation_action_under_test", MOD_PATH)
MOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MOD)


# ---------------------------------------------------------------- 目印
print("目印")
ex = MOD.extract_action
check("全角", ex("＜薬草を渡す＞") == "薬草を渡す")
check("半角", ex("<薬草を渡す>") == "薬草を渡す")
check("混在（開き全角・閉じ半角）", ex("＜薬草を渡す>") == "薬草を渡す")
check("混在（開き半角・閉じ全角）", ex("<薬草を渡す＞") == "薬草を渡す")
check("前後の空白", ex("  ＜ 薬草を渡す ＞\n") == "薬草を渡す")
check("複数行", ex("＜鞄を開け\n薬草を渡す＞") == "鞄を開け\n薬草を渡す")
check("台詞の末尾の所作は対象外", ex("こんにちは＜笑う＞") is None)
check("台詞の先頭の所作は対象外", ex("＜笑う＞こんにちは") is None)
check("入れ子・連結は対象外", ex("<渡す> と言って <笑う>") is None)
check("中身が空", ex("＜＞") is None and ex("< >") is None)
check("文字列以外", ex(None) is None and ex(12) is None)
check("開きと閉じの族が違う", ex("＜薬草を渡す）") is None)
check("（）は既定で対象外", ex("（薬草を渡す）") is None and ex("(薬草を渡す)") is None)
check("（）は設定で対象", ex("（薬草を渡す）", True) == "薬草を渡す"
      and ex("(薬草を渡す)", True) == "薬草を渡す")
check("（）を許しても＜＞は変わらない", ex("＜薬草を渡す＞", True) == "薬草を渡す")

print("ゲーム自身の記法")
for text in ("<行動: 話しかける>", "<行動：話しかける>", "<行動: 古代の採掘道具を売却した。>",
             "<状況: 宿に入ってきたエリスに、あなたの方から声をかけた>",
             "<会話>", "<出来事>", "<出来事の要約>", "<要約>",
             "<結果:成功>", "<結果: 失敗>", "<確率70%: 成功>", "<確率 30％: 失敗>",
             "＜行動: 話しかける＞"):
    check("除外 {}".format(text), ex(text) is None)
for text, want in (("<行動する>", "行動する"), ("<出来事を尋ねる>", "出来事を尋ねる"),
                   ("<結果を聞く>", "結果を聞く"), ("<確率は五分だと言う>", "確率は五分だと言う"),
                   ("<会話を打ち切って席を立つ>", "会話を打ち切って席を立つ")):
    check("見出しで始まっても続きが文なら行動 {}".format(text), ex(text) == want)

print("最後の user")
lp = MOD.last_player_input
check("末尾の user", lp([{"role": "system", "content": "s"}, {"role": "user", "content": "a"},
                        {"role": "assistant", "content": "b"}, {"role": "user", "content": "<c>"}]) == "<c>")
check("assistant が最後でも user を遡る",
      lp([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]) == "a")
check("user が無い", lp([{"role": "system", "content": "s"}]) is None)
check("list 以外", lp(None) is None and lp("x") is None)
msg = types.SimpleNamespace(role="user", content="<obj>")
check("属性で持つ message", lp([msg]) == "<obj>")

with io.open(MANIFEST_PATH, encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
for key in ("ACCEPT_PAREN", "SHOW_INVENTORY", "MAX_ITEMS", "NOTE_ACTION", "NOTE_INVENTORY"):
    check("{} の既定値がコードと mod.json で同じ".format(key),
          getattr(MOD, key) == MANIFEST["settings"][key]["default"])
check("facilitator を包む MOD より後（after）",
      set(MANIFEST.get("after", [])) >= {"301_quest_from_conversation",
                                         "311_npc_profile_memory", "404_party_talk"})


# ---------------------------------------------------------------- 偽ゲーム
class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.notes = []

    _mod = None

    def out_path(self, *parts):
        return self._under(self.out_dir, parts)

    def state_path(self, *parts):
        return self._under(self.state_dir, parts)

    @staticmethod
    def _under(root, parts):
        path = os.path.join(root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        real = ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


class Model(object):
    """create_model の代わり。項目を持つだけの型。"""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class Frozen(object):
    """書き換えを拒む応答（frozen な pydantic の代わり）。"""

    def __init__(self, **fields):
        object.__setattr__(self, "_f", dict(fields))

    def __getattr__(self, name):
        try:
            return self._f[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        raise TypeError("frozen")


class Item(object):
    def __init__(self, name, description):
        self.name = name
        self.description = description


class Inventory(object):
    def __init__(self, items):
        self.inventory = {"item_{}".format(i): it for i, it in enumerate(items)}


class Player(object):
    def __init__(self, items):
        self.inventory = Inventory(items)


class InstantaleApp(object):
    def __init__(self):
        self.player = None


main = sys.modules["__main__"]
main.InstantaleApp = InstantaleApp   # ui.find_app() が __main__ の属性からこの型の実体を拾う
llm.create_structure = lambda ctx, name, fields, label="llm": (
    lambda **kw: Model(**kw))


def fresh():
    ctx = Ctx(tempfile.mkdtemp(prefix="conversation_action_test_"))
    main.APP = InstantaleApp()
    MOD.apply(ctx)
    return ctx


def reply(kind="casual_response", statement="はい", free=False, violation=False):
    return Model(content_violation=violation,
                 action=Model(type=kind, accepted=None, statement=statement,
                              call_free_action=free))


def messages(last):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": "やあ"},
            {"role": "assistant", "content": "どうも"}, {"role": "user", "content": last}]


FAC = "scripts.llm.llm_manager:conversation_facilitator"
FAC_R = "scripts.llm.llm_manager:conversation_facilitator_after_retrieval"
FAC_Q = "scripts.llm.llm_manager:conversation_facilitator_in_quest"
GM = "scripts.llm.llm_manager:master_ai_facilitator_from_conversation"
GM_Q = "scripts.llm.llm_manager:master_ai_faciltiator_from_conversation_in_quest"
END = "__main__:ConversationEndManager.execute"
TITLE = "__main__:InstantaleApp.return_to_title"


def gm_args(log, player=None):
    """from_conversation の実引数（位置）。"""
    return (player, "life", "world", [], [], "npcs", log, [])


# ---------------------------------------------------------------- 書換
print("書換")
ctx = fresh()
check("包む対象が揃っている", all(t in ctx.hooks for t in (FAC, FAC_R, FAC_Q, GM, GM_Q, END, TITLE)))

got = ctx.hooks[FAC](lambda *a, **k: reply(), messages("＜薬草を渡す＞"))
check("＜＞ で call_free_action が True", got.action.call_free_action is True)
check("返事はそのまま", got.action.statement == "はい" and got.action.type == "casual_response")
check("ログに forced", any("forced free action" in n for n in ctx.notes))

got = ctx.hooks[FAC](lambda *a, **k: reply(), messages("薬草を渡す"))
check("目印なしは触らない", got.action.call_free_action is False)
got = ctx.hooks[FAC](lambda *a, **k: reply(), messages("こんにちは（笑う）"))
check("所作つきの台詞は触らない", got.action.call_free_action is False)

got = ctx.hooks[FAC](lambda *a, **k: reply(violation=True, statement=""), messages("<殴る>"))
check("content_violation は触らない", got.action.call_free_action is False)

retrieve = Model(content_violation=False, action=Model(type="retrieve", targets=["rumor"]))
got = ctx.hooks[FAC](lambda *a, **k: retrieve, messages("<噂を聞く>"))
check("retrieve は触らない", not hasattr(got.action, "call_free_action"))
got = ctx.hooks[FAC_R](lambda *a, **k: reply(), messages("<噂を聞く>"))
check("after_retrieval で書き換わる", got.action.call_free_action is True)

got = ctx.hooks[FAC](lambda *a, **k: reply(free=True), messages("<殴る>"))
check("もとから True ならそのまま", got.action.call_free_action is True)
check("そのときも控えは立つ", any("already chosen" in n for n in ctx.notes))

got = ctx.hooks[FAC](lambda *a, **k: reply(), messages=messages("<払う>"))
check("messages がキーワードでも読む", got.action.call_free_action is True)

frozen = Frozen(content_violation=False,
                action=Frozen(type="casual_response", accepted=True, statement="ふむ",
                              call_free_action=False))
got = ctx.hooks[FAC_Q](lambda *a, **k: frozen, messages("<渡す>"))
check("書けない型は作り直す", got is not frozen and got.action.call_free_action is True)
check("作り直しても項目は写る", got.action.statement == "ふむ" and got.action.accepted is True
      and got.content_violation is False)

saved = llm.create_structure
llm.create_structure = lambda ctx, name, fields, label="llm": None
ctx2 = fresh()
got = ctx2.hooks[FAC](lambda *a, **k: frozen, messages("<渡す>"))
check("型も作れなければ素のまま（WARN）", got is frozen and any("WARN" in n for n in ctx2.notes))
llm.create_structure = saved

with_paren = MOD.ACCEPT_PAREN
MOD.ACCEPT_PAREN = True
got = ctx.hooks[FAC](lambda *a, **k: reply(), messages("（薬草を渡す）"))
check("設定で（）も書き換わる", got.action.call_free_action is True)
MOD.ACCEPT_PAREN = with_paren

check("例外は記録されていない", not ctx.errors, ctx.errors)


# ---------------------------------------------------------------- 材料
print("材料")
ctx = fresh()
player = Player([Item("薬草", "傷を癒す"), Item("鉄の剣", "重い"), Item("薬草", "同名")])
seen = {}


def gm_orig(*args, **kwargs):
    seen["args"], seen["kwargs"] = args, kwargs
    return "gm"


original_log = ["アール:'やあ'", "商人:'どうも'", "アール:'<薬草を渡す>'"]
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("控えが無ければ足さない", seen["args"][6] is original_log)

ctx.hooks[FAC](lambda *a, **k: reply(), messages("<薬草を渡す>"))
got = ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
new_log = seen["args"][6]
check("戻り値は素通し", got == "gm")
check("本体のリストは書き換えない", len(original_log) == 3 and new_log is not original_log)
check("2行足す", len(new_log) == 5 and new_log[:3] == original_log)
check("行動の1行", new_log[3] == MOD.NOTE_ACTION.format(action="薬草を渡す"))
check("所持品の1行に名前と説明", "'薬草': '傷を癒す'" in new_log[4] and "'鉄の剣': '重い'" in new_log[4])
check("同名は1つ", new_log[4].count("'薬草'") == 1)
check("<> の形", new_log[3].startswith("<") and new_log[3].endswith(">")
      and new_log[4].startswith("<") and new_log[4].endswith(">"))

ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("finished=false の再呼び出しでも同じ材料", len(seen["args"][6]) == 5)

ctx.hooks[GM](gm_orig, *gm_args("アール:'<薬草を渡す>'", player))
check("str のログにも足す", isinstance(seen["args"][6], str)
      and seen["args"][6].count("\n") == 2 and "'薬草'" in seen["args"][6])

ctx.hooks[GM](gm_orig, **dict(zip(MOD.GM_TARGETS["master_ai_facilitator_from_conversation"],
                                 gm_args(original_log, player))))
check("キーワード引数でも差し替わる", len(seen["kwargs"]["conversation_log"]) == 5)

ctx.hooks[GM](gm_orig, *gm_args(original_log, None))
check("player が無ければ app.player", len(seen["args"][6]) == 4)
main.APP.player = player
ctx.hooks[GM](gm_orig, *gm_args(original_log, None))
check("app.player から所持品", len(seen["args"][6]) == 5)

ctx.hooks[GM](gm_orig, *gm_args(original_log, Player([])))
check("所持品が空なら行動の1行だけ", len(seen["args"][6]) == 4)

ctx.hooks[GM](gm_orig, *gm_args(12345, player))
check("読めないログは足さず WARN", seen["args"][6] == 12345
      and any("nothing added" in n for n in ctx.notes))

ctx.hooks[GM_Q](gm_orig, *gm_args(original_log, player))
check("in_quest の綴りの経路にも", len(seen["args"][6]) == 5)

limit = MOD.MAX_ITEMS
MOD.MAX_ITEMS = 1
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("上限で切る", "'鉄の剣'" not in seen["args"][6][4] and "'薬草'" in seen["args"][6][4])
MOD.MAX_ITEMS = 0
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("上限 0 で一覧を出さない", len(seen["args"][6]) == 4)
MOD.MAX_ITEMS = limit

show = MOD.SHOW_INVENTORY
MOD.SHOW_INVENTORY = False
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("設定で所持品を出さない", len(seen["args"][6]) == 4)
MOD.SHOW_INVENTORY = show

check("例外は記録されていない", not ctx.errors, ctx.errors)


# ---------------------------------------------------------------- 片付け
print("片付け")
ctx = fresh()
ctx.hooks[FAC](lambda *a, **k: reply(), messages("<薬草を渡す>"))
ctx.hooks[FAC](lambda *a, **k: reply(), messages("ありがとう"))
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("次の1手で消える", seen["args"][6] is original_log)

ctx.hooks[FAC](lambda *a, **k: reply(), messages("<薬草を渡す>"))
ctx.hooks[END](lambda self, *a, **k: "end", object())
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("会話終了で消える", seen["args"][6] is original_log and any("cleared" in n for n in ctx.notes))

ctx.hooks[FAC](lambda *a, **k: reply(), messages("<薬草を渡す>"))
ctx.hooks[TITLE](lambda self, *a, **k: "title", object())
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("タイトル復帰で消える", seen["args"][6] is original_log)

ctx.hooks[FAC](lambda *a, **k: reply(), messages("<薬草を渡す>"))
ctx.hooks[FAC](lambda *a, **k: reply(), messages("<剣を抜く>"))
ctx.hooks[GM](gm_orig, *gm_args(original_log, player))
check("次の行動に置き換わる", "剣を抜く" in seen["args"][6][3] and "薬草" not in seen["args"][6][3])

check("例外は記録されていない", not ctx.errors, ctx.errors)

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
