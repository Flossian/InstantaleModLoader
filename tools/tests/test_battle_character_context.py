# -*- coding: utf-8 -*-
"""401_battle_character_context.py をゲーム抜きで通す。

    python tools/tests/test_battle_character_context.py

偽の app / World / Character / send_request を差し込み、次を確認する。

  門     … `in_battle` が立っている時だけ、referee の名前の推論にだけ足す
  追記   … 最後の user message の末尾へ足し、呼び出し元の list / dict は壊さない
  上限   … 1項目ずつの上限と、同行者全員ぶんの合計上限が効く
  控え   … runtime に無い項目だけ `World.generate_character` の保存辞書から補う
  記録   … `in_battle` が落ちている間に他の戦闘フラグが立っていたら1度だけ残す

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml            # noqa: E402


def find_mod(suffix):
    """mod は **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_battle_character_context")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# ここで定義したクラスは __main__ の属性になる。
# `ui.find_app()` は `__main__` の中から InstantaleApp のインスタンスを探すので、
# これで本番と同じ引き方になる。
class Character(object):
    def __init__(self, character_id, name, **fields):
        self.id = character_id
        self.name = name
        self.current_hp = 40
        self.max_hp = 40
        self.equipments = {}
        self.inventory = {}
        for key, value in fields.items():
            setattr(self, key, value)


class World(object):
    def __init__(self, characters):
        self.characters = characters


class InstantaleApp(object):
    def __init__(self, characters, party):
        self.world = World(characters)
        self.party = party
        self.in_battle = False
        self.in_boss_battle = False
        self.in_colosseum_battle = False


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.notes = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        # 本物の logger をそのまま借りる（検査だけ別のログ処理を通らないように）。
        real = ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

        def write(message):
            self.notes.append(str(message))
            return real(message)
        return write

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def resolve(self, target):
        # 見張りに回さず、その場で当てさせる。
        return (None, None, object())

    def wrap(self, target, required=True, safe=False, alias_scan=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


def load_mod():
    spec = importlib.util.spec_from_file_location("battle_character_context_under_test",
                                                  MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = load_mod()

SEND_TARGET = "scripts.llm.llm_manager_battle:send_request"
NO_STRUCTURE_TARGET = "scripts.llm.llm_manager_battle:send_request_with_no_structure"
GENERATE = "__main__:World.generate_character"
RETURN_TO_TITLE = "__main__:InstantaleApp.return_to_title"

APP = None   # __main__ の属性として ui.find_app() に見つけてもらう


def build(app):
    """mod を当て、包まれた send_request を返す。"""
    global APP
    APP = app
    out_dir = tempfile.mkdtemp(prefix="battle_context_test_")
    ctx = Ctx(out_dir)
    MOD.apply(ctx)

    sent = {}

    def original(manager_name, message, *args, **kwargs):
        sent["manager_name"] = manager_name
        sent["message"] = message
        return "ok"

    hook = ctx.hooks[SEND_TARGET]

    def send(manager_name, message, *args, **kwargs):
        return hook(original, manager_name, message, *args, **kwargs)

    return ctx, send, sent, out_dir


def user_message(text="今の一手を裁いてください。"):
    return [{"role": "system", "content": "あなたは戦闘の審判です。"},
            {"role": "user", "content": text}]


def appended_block(sent):
    for item in sent["message"]:
        content = item.get("content", "")
        if MOD.MARKER in content:
            return content[content.index(MOD.MARKER):]
    return ""


def party_of(count, **fields):
    characters = {"player": Character("player", "主人公")}
    party = ["player"]
    for n in range(count):
        cid = str(80 + n)
        characters[cid] = Character(cid, "同行者{}".format(n), **fields)
        party.append(cid)
    return InstantaleApp(characters, party)


# ---------------------------------------------------------------- 門
print("戦闘中だけ足す")
app = party_of(1, profile="剣士。")
ctx, send, sent, out_dir = build(app)

message = user_message()
send("referee_player_attack_new_new", message)
check("戦闘中でなければ足さない", MOD.MARKER not in appended_block(sent),
      sent["message"])

app.in_battle = True
send("conversation_starter", message)
check("referee 以外の推論には触らない", appended_block(sent) == "", sent["message"])

send("referee_player_attack_new_new", message)
check("戦闘中の referee には足す", MOD.MARKER in appended_block(sent))
check("元の list を書き換えない", message[1]["content"] == "今の一手を裁いてください。",
      message[1]["content"])
check("最後の user の末尾へ足す",
      sent["message"][1]["content"].startswith("今の一手を裁いてください。"),
      sent["message"][1]["content"][:60])

sent["message"] = None
send("referee_player_attack_new_new", sent["message"] or user_message())
already = [{"role": "user", "content": "本文" + MOD.MARKER + "既に在る"}]
send("referee_player_attack_new_new", already)
check("印が既に在れば二重に足さない",
      sent["message"] is already, sent["message"])

shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 上限
print("上限")
long_text = "あ" * 5000
app = party_of(3, profile=long_text, personality=long_text, speech_style=long_text,
               job=long_text, tactics=long_text, traits=long_text, status=long_text)
app.in_battle = True
ctx, send, sent, out_dir = build(app)
send("referee_player_attack_new_new", user_message())
block = appended_block(sent)

profile_line = [l for l in block.split("\n") if l.startswith("  profile: ")]
check("profile は1項目の上限で切れる",
      profile_line and len(profile_line[0]) <= len("  profile: ") + 800 + 1,
      profile_line[:1])
check("同行者ぶんの合計は上限の中に収まる",
      len(block) <= MOD.BLOCK_TOTAL_CHARS + 400, len(block))
check("溢れても3人ぶんの見出しは残る",
      block.count("- party_member: ") == 3, block.count("- party_member: "))
check("足した文字数をログに残す",
      any("appended 3 character(s)," in note for note in ctx.notes),
      [n for n in ctx.notes if "appended" in n][:2])
shutil.rmtree(out_dir, ignore_errors=True)


# ---------------------------------------------------------------- 装備と控え
print("装備と保存辞書の控え")
app = party_of(1)
npc = app.world.characters["80"]
npc.equipments = {"weapon": "item_0"}
npc.inventory = {"item_0": {"name": "鉄の剣", "description": "よく研がれている。",
                            "attributes": {"attack": 12}}}
app.in_battle = True
ctx, send, sent, out_dir = build(app)
send("referee_player_attack_new_new", user_message())
block = appended_block(sent)
check("weapon は 名前(説明) の形で載る", "  weapon: 鉄の剣(よく研がれている。)" in block, block)
check("attributes は別の行に分ける", "  weapon_attributes: " in block, block)

# runtime に speech_style が無い NPC は、ロード時の保存辞書から補う。
bare = party_of(1)
bare.in_battle = True
ctx2, send2, sent2, out_dir2 = build(bare)
ctx2.hooks[GENERATE](lambda self, cid, cv: None, object(), "80",
                     {"speech_style": "ぶっきらぼうに話す。"})
send2("referee_player_attack_new_new", user_message())
check("runtime に無い項目は保存辞書から補う",
      "  speech_style: ぶっきらぼうに話す。" in appended_block(sent2),
      appended_block(sent2))

ctx2.hooks[RETURN_TO_TITLE](lambda self: None, object())
send2("referee_player_attack_new_new", user_message())
check("タイトルへ戻ると控えを捨てる",
      "  speech_style: " not in appended_block(sent2), appended_block(sent2))
shutil.rmtree(out_dir, ignore_errors=True)
shutil.rmtree(out_dir2, ignore_errors=True)


# ---------------------------------------------------------------- 他の戦闘フラグ
print("in_battle が落ちている間の他の戦闘フラグ")
app = party_of(1, profile="剣士。")
app.in_battle = False
app.in_boss_battle = True
ctx, send, sent, out_dir = build(app)
send("referee_player_attack_new_new", user_message())
send("referee_player_attack_new_new", user_message())
notes = [n for n in ctx.notes if "in_boss_battle is set" in n]
check("立っていたら記録する", len(notes) == 1, notes)
check("記録しても足しはしない", MOD.MARKER not in appended_block(sent), sent["message"])
check("構造化なしの送り口にも仕掛ける",
      NO_STRUCTURE_TARGET in ctx.hooks, sorted(ctx.hooks))
shutil.rmtree(out_dir, ignore_errors=True)


print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
