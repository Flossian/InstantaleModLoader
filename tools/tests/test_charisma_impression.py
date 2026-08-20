# -*- coding: utf-8 -*-
"""125_balance_charisma_impression をゲーム抜きで通す。

    python tools/tests/test_charisma_impression.py

偽の `scripts.functions:document_emotion_scores_new`（魅力6段・好感度13段のはしご）と、
`character_instance` を持つ偽の呼び出し元を組んで、次を確認する。

  学習    … 段の並び・位置・初対面の段をゲームから読み取る（文言は持たない）
  一律解消 … 同じ魅力・同じ好感度でも、相手によって段が散る
  最上段  … 初対面では誰も最上段にならない
  親密    … 好意を持たれている相手には最上段まで届く
  下限    … 好みや親しさで「ひどく醜く」にはしない
  低魅力  … 素が最下段のキャラは初対面でも下げない（そのまま）
  好感度  … 戻り値の1つ目（好感度の段）は1文字も触らない
  決定的  … 同じ相手・同じ条件なら何度呼んでも同じ段
  設定    … 好みの幅0 / 最上段を切る / 親しさを見ない、の3つが効く
  相手なし … 呼び出し元に NPC が居なくても落ちず、親しさだけが効く
  マネージャ … `self.character_id` しか無いフレームからでも相手が決まる
  抱え込み … `self.character_instance` に相手を持っているフレームでも決まる
  初対面  … `relationship` が空の NPC でも相手として認める
  名簿優先 … 相手ではない id（会話の通し番号）より、外側の相手を採る
  受け皿  … フレームが読めなくても、会話の入口で控えた相手で決まる
  終了経路 … 会話の終了（`resolve_conversation` の引数）からも相手が決まる
  診断    … 拾えないときは、その場のローカルと `self` の属性を記録に残す
  空段    … 段が「無い」帯（戻り値が1要素）でも列を壊さない
  言語    … 文言が英語でも同じ結果（段は位置で扱う）
  閾値    … 引けない点を飛ばしても、記録に残る閾値がずれない
  別物    … 段が単調でない・動く位置が2つあるゲームでは何もしない
  無事故  … どの経路でも ctx.log_exc が呼ばれない

はしごの形（魅力5段＋文の付かない帯1つ・好感度13段）は
exe の定数の並びから起こしたもので、**実測ではない**（VERIFICATION.md §3.23）。
閾値はここで置いた適当な値であり、実機の値と揃える意味も無い。
この試験に要るのは数値ではなく「段が単調に並んでいる関数」だから。
mod は閾値を持たず、その場で読む。
"""
import ast
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

# 失敗したときは記録の中身をそのまま出す。
# cp932 のコンソールに出せない文字が混ざっていても試験自体は落とさない。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
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


MOD = find_mod("_balance_charisma_impression")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
# 好感度の段（`(この値以上, 文言)` の昇順）。
AFFINITY_RUNGS = [
    (-60, "深く憎悪している"), (-40, "憎悪している"), (-30, "強い嫌悪感を抱いている"),
    (-20, "嫌悪している"), (-10, "多少嫌悪している"), (0, "警戒心がある"),
    (10, "好きでも嫌いでもない"), (20, "嫌いではない"), (30, "興味がある"),
    (40, "多少の好意がある"), (60, "仲間だと感じている"), (80, "盟友だと思っている"),
    (150, "家族同然に感じている"),
]

# 魅力の段。
# 3段目は「文が付かない」帯（戻り値が1要素になる）。
CHARM_RUNGS = [
    (0, "ひどく醜く思っている"), (7, "あまり好みではない"), (9, None),
    (12, "魅力を感じている"), (15, "強い魅力を感じている"),
    (18, "耐え難いほど魅力的に見えている"),
]

CHARM_RUNGS_EN = [
    (0, "Finds you extremely ugly"), (7, "Not really their type"), (9, None),
    (12, "Feels attracted"), (15, "Feels a strong attraction"),
    (18, "Finds you irresistibly attractive"),
]

# 段が無い版（全部の帯に文言がある）。
# 列の長さが常に2になる。
CHARM_RUNGS_FULL = [
    (0, "ひどく醜く思っている"), (7, "あまり好みではない"), (9, "気にならない"),
    (12, "魅力を感じている"), (15, "強い魅力を感じている"),
    (18, "耐え難いほど魅力的に見えている"),
]

TOP_CHARM = 20          # 魅力に振ったキャラ（素で最上段に貼り付く）
LOW_CHARM = 3           # 素で最下段のキャラ


def rung_at(table, value):
    picked = table[0][1]
    for threshold, text in table:
        if value >= threshold:
            picked = text
    return picked


def make_game(charm_table=CHARM_RUNGS, broken=None):
    """`document_emotion_scores_new` を組む。ゲームと同じく列で返す。"""

    def document_emotion_scores_new(affinity, player_charisma):
        row = [rung_at(AFFINITY_RUNGS, affinity)]
        charm = rung_at(charm_table, player_charisma)
        if charm is not None:
            row.append(charm)
        if broken == "picky" and (player_charisma < 4 or affinity < -100):
            # 素の関数は定義域の外で投げる（実測: `charisma=0` で ValueError）。
            raise ValueError("max() arg is an empty sequence")
        if broken == "two_moving":
            # 魅力で動く位置が2つあるゲーム。
            row.append("x" if player_charisma < 10 else "y")
        elif broken == "not_a_ladder":
            # 同じ段が離れて2度出る（段ではない）。
            row[-1] = "A" if (player_charisma // 3) % 2 == 0 else "B"
        return row

    return document_emotion_scores_new


class Character(object):
    """`scripts.characters.Character` 相当。

    mod は Character を**型名ではなく持ち物**で見分けるので、`__init__` が
    持っている項目（`out/recon/targets.txt` の 639 行）を並べておく。
    ここを削ると、実機では通る判定がオフラインだけ落ちる。
    """

    def __init__(self, name, is_player=False):
        self.name = name
        self.profile = "{}の来歴".format(name)
        self.personality = "穏やか"
        self.speech_style = "丁寧"
        self.look_description = "旅装"
        self.life_log = []
        self.current_log = []
        self.relationship = {"player": {"affinity": 0, "affinity_text": [],
                                        "relationship": [],
                                        "conversation_count": 0}}
        self.config = {"is_player": is_player}
        self.is_player = is_player


class Manager(object):
    """`ConversationStartManager` 相当。

    実機の呼び出し元がこれ。`__init__(self, app, character_id)` で、
    `conversation_start_method_1(self)` の中には **self しか居ない**
    （VERIFICATION.md §3.23）。
    """

    def __init__(self, character_id, affinity, charisma):
        self.app = None
        self.character_id = character_id
        self.text_store = {}
        self._affinity = affinity
        self._charisma = charisma

    def execute(self, choice_text=None):
        return self.conversation_start_method_0()

    def conversation_start_method_0(self):
        return self.conversation_start_method_1()

    def conversation_start_method_1(self):
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            self._affinity, self._charisma)

    def conversation_start_method_3(self):
        return None


class EndManager(object):
    """`ConversationEndManager` 相当。

    `__init__(self, app, in_conversation_id, finisher, end_text)` /
    `resolve_conversation(self, character_id)`（`out/recon/targets.txt`）。
    **相手の id は引数で来る**。`in_conversation_id` は相手の id とは限らないので、
    ここでは名簿に無い値を入れてある（掴んだら好みが会話ごとに変わってしまう）。
    """

    def __init__(self, in_conversation_id, character_id, affinity, charisma):
        self.in_conversation_id = in_conversation_id
        self.end_text = "会話を終了する"
        self._real = character_id
        self._affinity = affinity
        self._charisma = charisma

    def execute(self, choice_text=None):
        return self.finish_conversation()

    def finish_conversation(self):
        return self.resolve_conversation(self._real)

    def resolve_conversation(self, character_id):
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            self._affinity, self._charisma)


class EndManagerBare(object):
    """相手の id を引数で受け取らないまま終了処理へ入るマネージャ。

    持っているのは `in_conversation_id` だけ ― **相手の id とは限らない値**。
    これを鍵にすると好みが会話ごとに変わるので、mod は採ってはいけない。
    """

    def __init__(self, in_conversation_id, affinity, charisma):
        self.in_conversation_id = in_conversation_id
        self.end_text = "会話を終了する"
        self._affinity = affinity
        self._charisma = charisma

    def execute(self, choice_text=None):
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            self._affinity, self._charisma)


class Holder(object):
    """相手を id ではなく実体で抱えているマネージャ。"""

    def __init__(self, character_instance, affinity, charisma):
        self.character_instance = character_instance
        self._affinity = affinity
        self._charisma = charisma

    def run(self):
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            self._affinity, self._charisma)


class Session(object):
    """相手ではない id（会話の通し番号）を持つ内側の呼び出し元。

    こういう id を掴むと**全員が同じ鍵**になり、好みが一律に戻る。
    """

    def __init__(self, affinity, charisma):
        self.character_id = "session-1"          # 世界の名簿には無い
        self._affinity = affinity
        self._charisma = charisma

    def run(self):
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            self._affinity, self._charisma)


class Bare(object):
    """相手の手掛かりを何も持っていない呼び出し元。"""

    def __init__(self):
        self.phase = "start"
        self.text_store = {}

    def run(self, affinity, charisma):
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            affinity, charisma)


class World(object):
    def __init__(self, name, npcs):
        self.name = name
        self.characters = dict(npcs)


class App(object):
    def __init__(self, world, player):
        self.world = world
        self.player = player


# TECH.md §4.3: `__main__` のグローバル名から派生させない（派生が積み上がる）。
BASE_APP = App


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.notes = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（検査だけ別経路にしない）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        self.notes.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "charisma_impression_mod", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOG_NAME = "charisma_impression.log"
OUT_DIR = os.path.join(HERE, os.pardir, os.pardir, "out", "test")

#: 直前の setup() が組んだ世界（相手の拾い方の検査で使う）。
LAST = {}

# `ConversationStartManager` の4メソッド。mod はここを包んで
# 「いま誰と話しているか」を控える（フレームが読めないときの受け皿）。
MANAGER_TARGETS = (
    ("__main__:ConversationStartManager.execute", "execute"),
    ("__main__:ConversationStartManager.conversation_start_method_0",
     "conversation_start_method_0"),
    ("__main__:ConversationStartManager.conversation_start_method_1",
     "conversation_start_method_1"),
    ("__main__:ConversationStartManager.conversation_start_method_3",
     "conversation_start_method_3"),
)

END_BARE_TARGETS = (("__main__:ConversationEndManager.execute", "execute"),)

END_TARGETS = (
    ("__main__:ConversationEndManager.execute", "execute"),
    ("__main__:ConversationEndManager.finish_conversation",
     "finish_conversation"),
    ("__main__:ConversationEndManager.resolve_conversation",
     "resolve_conversation"),
)


def setup(charm_table=CHARM_RUNGS, broken=None, spread=1, steps=4,
          allow_top=True, npc_names=("エルドラ",), fresh_log=True):
    """mod を適用し、`(mod, ctx, call, npcs)` を返す。"""
    if fresh_log:
        try:
            os.remove(os.path.join(OUT_DIR, LOG_NAME))
        except OSError:
            pass

    scripts = types.ModuleType("scripts")
    functions = types.ModuleType("scripts.functions")
    functions.document_emotion_scores_new = make_game(charm_table, broken)
    scripts.functions = functions
    sys.modules["scripts"] = scripts
    sys.modules["scripts.functions"] = functions

    player = Character("ヴァン", is_player=True)
    npcs = [Character(name) for name in npc_names]
    world = World("テストワールド", [(str(100 + i), npc)
                                     for i, npc in enumerate(npcs)])
    app_cls = type("InstantaleApp", (BASE_APP,), {})
    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main._test_app = app_cls(world, player)

    LAST.update(world=world, player=player, npcs=npcs)

    mod = load_mod()
    mod.TASTE_SPREAD = spread
    mod.ACQUAINTANCE_STEPS = steps
    mod.ALLOW_TOP_RUNG = allow_top
    ctx = FakeCtx(OUT_DIR)
    mod.apply(ctx)

    hook = ctx.hooks["scripts.functions:document_emotion_scores_new"]
    original = functions.document_emotion_scores_new

    def patched(*args, **kwargs):
        return hook(original, *args, **kwargs)

    functions.document_emotion_scores_new = patched

    def call(npc, affinity, charisma):
        """ゲーム側の呼び出し元の再現（`character_instance` を持つフレーム）。"""
        character_instance = npc
        assert character_instance is not None
        return sys.modules["scripts.functions"].document_emotion_scores_new(
            affinity, charisma)

    return mod, ctx, call, npcs


def install_manager_hooks(ctx, base=Manager, targets=MANAGER_TARGETS,
                          cls_name="ConversationStartManager"):
    """会話の入口・出口のフックを、偽のマネージャへ本番と同じ形で載せる。"""
    cls = type(cls_name, (base,), {})
    for target, name in targets:
        hook = ctx.hooks.get(target)
        if hook is None:
            continue
        original = getattr(base, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(cls, name, make())
    return cls


def npc_id_of(npc):
    """`world.characters` の鍵（setup が振った id）。"""
    for key, value in LAST["world"].characters.items():
        if value is npc:
            return key
    raise AssertionError("npc not in world")


def call_via_manager(npc, affinity, charisma, cls=Manager):
    """id しか持たないマネージャからの呼び出し（実機と同じ形）。"""
    return cls(npc_id_of(npc), affinity, charisma).conversation_start_method_1()


def call_via_session(npc, affinity, charisma):
    """内側に「相手ではない id」、外側に相手が居る呼び出し。"""
    character_instance = npc
    assert character_instance is not None
    return Session(affinity, charisma).run()


def call_without_npc(affinity, charisma):
    """NPC を1つも持たないフレームからの呼び出し。"""
    return sys.modules["scripts.functions"].document_emotion_scores_new(
        affinity, charisma)


def log_text(ctx):
    path = os.path.join(ctx.out_dir, LOG_NAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def charm_of(row):
    """戻り値から魅力の段を読む（無ければ None）。"""
    return row[1] if len(row) > 1 else None


NAMES = ["エルドラ", "ハンス", "リナ", "ヴァルカ", "セシル", "トビアス",
         "ミラ", "グレン", "アイナ", "ドルグ", "レイン", "ユーリ"]


# ------------------------------------------------------------------ 学習
print("\n-- 学習")
mod, ctx, call, npcs = setup()
row = call(npcs[0], 0, TOP_CHARM)
text = log_text(ctx)
check("学習: 魅力の段を6つ覚える", text.count(" < ") >= 1 and "魅力   位置 1" in text,
      text[:400])
check("学習: 好感度の段を覚える", "好感度 位置 0" in text, text[:400])
check("学習: 初対面の段を覚える", "'警戒心がある'" in text and "5段目" in text,
      text[:600])
check("学習: 閾値も記録に残る", "魅力の閾値: " in text and "18から" in text,
      text[:600])
def literals_of(path):
    """mod の中の文字列リテラル（説明文は除く）。"""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs]


held = [text for text in literals_of(MOD)
        if any(rung in text for _, rung in CHARM_RUNGS + AFFINITY_RUNGS if rung)]
check("学習: 段の文言を mod は持たない（説明文の中だけ）", not held, held)

# ------------------------------------------------------------ 一律の解消
print("\n-- 一律の解消")
mod, ctx, call, npcs = setup(npc_names=NAMES)
first_meeting = {npc.name: charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("一律解消: 初対面の段が相手ごとに散る", len(set(first_meeting.values())) >= 2,
      first_meeting)
check("最上段: 初対面では誰も最上段にならない",
      "耐え難いほど魅力的に見えている" not in first_meeting.values(), first_meeting)
check("下限: 誰からも「ひどく醜く」にはならない",
      "ひどく醜く思っている" not in first_meeting.values(), first_meeting)
check("素のゲームは一律だった",
      len({charm_of(make_game()(0, TOP_CHARM)) for _ in NAMES}) == 1)

close = {npc.name: charm_of(call(npc, 40, TOP_CHARM)) for npc in npcs}
check("親密: 好意を持たれた相手には最上段が出る",
      "耐え難いほど魅力的に見えている" in close.values(), close)
check("親密: それでも相手ごとに差は残る", len(set(close.values())) >= 2, close)

# 好感度が上がるほど段が下がらない（単調）ことを1人で確かめる。
one = npcs[0]
ladder = ["ひどく醜く思っている", "あまり好みではない", None, "魅力を感じている",
          "強い魅力を感じている", "耐え難いほど魅力的に見えている"]
seq = [ladder.index(charm_of(call(one, value, TOP_CHARM)))
       for value in (-60, -20, 0, 20, 30, 40, 80)]
check("親密: 好感度が上がって段が下がることはない",
      all(b >= a for a, b in zip(seq, seq[1:])), seq)

# --------------------------------------------------------- 触らないもの
print("\n-- 触らないもの")
mod, ctx, call, npcs = setup(npc_names=NAMES)
kept = all(call(npc, value, TOP_CHARM)[0] == rung_at(AFFINITY_RUNGS, value)
           for npc in npcs for value in (-60, -20, 0, 30, 40, 150))
check("好感度: 1つ目の段は1文字も触らない", kept)

low = {npc.name: call(npc, 0, LOW_CHARM) for npc in npcs}
check("低魅力: 素が最下段なら初対面でも変えない",
      all(charm_of(row) == "ひどく醜く思っている" for row in low.values()), low)

# ---------------------------------------------------------------- 決定的
print("\n-- 決定的")
mod, ctx, call, npcs = setup(npc_names=NAMES)
again = {npc.name: charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("決定的: 同じ相手・同じ条件なら同じ段", again == first_meeting,
      (again, first_meeting))
mod2, ctx2, call2, npcs2 = setup(npc_names=NAMES)
restart = {npc.name: charm_of(call2(npc, 0, TOP_CHARM)) for npc in npcs2}
check("決定的: 読み込み直しても同じ段", restart == first_meeting,
      (restart, first_meeting))

# ------------------------------------------------------------------ 設定
print("\n-- 設定")
mod, ctx, call, npcs = setup(npc_names=NAMES, spread=0)
flat = {charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("設定: 好みの幅0 なら全員同じ段", len(flat) == 1, flat)

mod, ctx, call, npcs = setup(npc_names=NAMES, allow_top=False)
capped = {charm_of(call(npc, 150, TOP_CHARM)) for npc in npcs}
check("設定: 最上段を切ると、家族同然でも最上段は出ない",
      "耐え難いほど魅力的に見えている" not in capped, capped)

mod, ctx, call, npcs = setup(npc_names=NAMES, steps=0)
early = {charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("設定: 親しさを見なければ初対面でも満額（最上段が出る）",
      "耐え難いほど魅力的に見えている" in early, early)

# -------------------------------------------------------------- 相手なし
print("\n-- 相手なし")
mod, ctx, call, npcs = setup()
alone = call_without_npc(0, TOP_CHARM)
check("相手なし: 落ちずに段が返る", isinstance(alone, list) and alone, alone)
check("相手なし: 親しさだけが効く（最上段は出ない）",
      charm_of(alone) != "耐え難いほど魅力的に見えている", alone)
check("相手なし: 拾えなかったことが記録に1度だけ出る",
      log_text(ctx).count("相手が拾えない") == 1, log_text(ctx)[-400:])

# ------------------------------------------------------------ 相手の拾い方
print("\n-- 相手の拾い方")
mod, ctx, call, npcs = setup(npc_names=NAMES)
by_manager = {npc.name: charm_of(call_via_manager(npc, 0, TOP_CHARM))
              for npc in npcs}
check("マネージャ: self.character_id しか無くても相手が決まる",
      len(set(by_manager.values())) >= 2, by_manager)
check("マネージャ: 引数から相手を渡したときと同じ段になる",
      by_manager == {npc.name: charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs},
      by_manager)
check("マネージャ: 相手の名前も記録に残る",
      all(npc.name in log_text(ctx) for npc in npcs))

mod, ctx, call, npcs = setup(npc_names=NAMES)
by_holder = {npc.name: charm_of(Holder(npc, 0, TOP_CHARM).run()) for npc in npcs}
check("抱え込み: self.character_instance からでも相手が決まる",
      len(set(by_holder.values())) >= 2, by_holder)

mod, ctx, call, npcs = setup(npc_names=NAMES)
for npc in npcs:
    npc.relationship = {}          # 初対面（まだ player の欄が無い）
first_time = {npc.name: charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("初対面: relationship が空でも相手として認める",
      len(set(first_time.values())) >= 2, first_time)

mod, ctx, call, npcs = setup(npc_names=NAMES)
by_session = {npc.name: charm_of(call_via_session(npc, 0, TOP_CHARM))
              for npc in npcs}
check("名簿優先: 相手ではない id より、外側の相手を採る",
      by_session == {npc.name: charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
      and len(set(by_session.values())) >= 2, by_session)

# 受け皿。フレームがまったく読めない環境（FRAME_DEPTH_MAX=1）でも、
# 会話の入口を通っていれば相手が分かる。
mod, ctx, call, npcs = setup(npc_names=NAMES)
mod.FRAME_DEPTH_MAX = 1
manager_cls = install_manager_hooks(ctx)
by_phase = {npc.name: charm_of(call_via_manager(npc, 0, TOP_CHARM, manager_cls))
            for npc in npcs}
check("受け皿: 会話の入口で控えた相手で決まる", len(set(by_phase.values())) >= 2,
      by_phase)
call_without_npc(0, TOP_CHARM)
tail = log_text(ctx).strip().splitlines()[-1]
check("受け皿: 会話を抜けた後は控えが残らない", "好み=+0" in tail, tail)
check("受け皿: 引数から渡したときと同じ段になる",
      by_phase == by_manager, (by_phase, by_manager))

# 会話の終了側。相手の id は `resolve_conversation` の**引数**で来る。
# 実機のフレームは `f_locals` が空なので、ここを包めるかどうかが全て。
mod, ctx, call, npcs = setup(npc_names=NAMES)
mod.FRAME_DEPTH_MAX = 1                      # 実機と同じくフレームは読めない
end_cls = install_manager_hooks(ctx, EndManager, END_TARGETS,
                                "ConversationEndManager")
by_end = {npc.name: charm_of(
    end_cls("conv-7", npc_id_of(npc), 0, TOP_CHARM).execute()) for npc in npcs}
check("終了経路: 引数の id からでも相手ごとに散る",
      len(set(by_end.values())) >= 2, by_end)
check("終了経路: 開始側と同じ段になる（会話の後に上書きされない）",
      by_end == by_manager, (by_end, by_manager))
mod, ctx, call, npcs = setup(npc_names=NAMES)
mod.FRAME_DEPTH_MAX = 1
bare_cls = install_manager_hooks(ctx, EndManagerBare, END_BARE_TARGETS,
                                 "ConversationEndManager")
bare = {npc.name: charm_of(bare_cls("conv-7", 0, TOP_CHARM).execute())
        for npc in npcs}
check("終了経路: 相手の id とは限らない値は鍵にしない", len(set(bare.values())) == 1,
      bare)
check("終了経路: 決まらなかったマネージャの中身が記録に残る",
      "in_conversation_id" in log_text(ctx) and "conv-7" in log_text(ctx),
      log_text(ctx)[-700:])

# 診断。拾えないときは、その場に何が居たかを記録に残す。
mod, ctx, call, npcs = setup()
Bare().run(0, TOP_CHARM)
blind = log_text(ctx)
check("診断: 拾えないときにローカルの中身が残る", "ローカル " in blind, blind[-500:])
check("診断: self の属性も残る", "self の属性" in blind, blind[-500:])
check("診断: 記録は1度だけ", blind.count("相手が拾えない") == 1)

# ------------------------------------------------------------------ 空段
print("\n-- 空段と言語")
mod, ctx, call, npcs = setup(npc_names=NAMES)
lengths = {len(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("空段: 段が無い帯では列が1要素になる", lengths == {1, 2}, lengths)
check("空段: 好感度の段は必ず残る",
      all(call(npc, 0, TOP_CHARM)[0] == "警戒心がある" for npc in npcs))

mod, ctx, call, npcs = setup(charm_table=CHARM_RUNGS_FULL, npc_names=NAMES)
full = {len(call(npc, 0, TOP_CHARM)) for npc in npcs}
check("空段: 全部の帯に文言があるゲームでは常に2要素", full == {2}, full)

mod, ctx, call, npcs = setup(charm_table=CHARM_RUNGS_EN, npc_names=NAMES)
english = {npc.name: charm_of(call(npc, 0, TOP_CHARM)) for npc in npcs}
same = all((english[name] is None) == (first_meeting[name] is None)
           and (english[name] is None
                or CHARM_RUNGS_EN[[t for _, t in CHARM_RUNGS].index(
                    first_meeting[name])][1] == english[name])
           for name in english)
check("言語: 文言が英語でも同じ段を選ぶ", same, (english, first_meeting))

# ------------------------------------------------------------------ 閾値
print("\n-- 閾値")
mod, ctx, call, npcs = setup(broken="picky")
call(npcs[0], 0, TOP_CHARM)
edges = log_text(ctx)
check("閾値: 引けなかった点があってもずれない",
      "7から'あまり好みではない'" in edges
      and "18から'耐え難いほど魅力的に見えている'" in edges, edges[:900])
check("閾値: 文の付かない帯へ入る境目が出る", "9からNone" in edges, edges[:900])
check("閾値: 文の付かない帯から出る境目も出る", "12から'魅力を感じている'" in edges,
      edges[:900])
check("閾値: 好感度側もずれない", "-40から'憎悪している'" in edges, edges[:1200])

# ------------------------------------------------------------------ 別物
print("\n-- 別物")
mod, ctx, call, npcs = setup(broken="two_moving")
before = make_game(CHARM_RUNGS, "two_moving")(0, TOP_CHARM)
check("別物: 動く位置が2つあるゲームでは何もしない",
      call(npcs[0], 0, TOP_CHARM) == before, (call(npcs[0], 0, TOP_CHARM), before))
check("別物: 何もしない理由が記録に残る", "段の並びが読めないので何もしない" in log_text(ctx),
      log_text(ctx)[:300])

mod, ctx, call, npcs = setup(broken="not_a_ladder")
before = make_game(CHARM_RUNGS, "not_a_ladder")(0, TOP_CHARM)
check("別物: 段が単調でないゲームでは何もしない",
      call(npcs[0], 0, TOP_CHARM) == before)

# ---------------------------------------------------------------- 無事故
print("\n-- 無事故")
mod, ctx, call, npcs = setup(npc_names=NAMES)
for npc in npcs:
    for affinity in (-200, -60, 0, 40, 200):
        for charisma in (0, 3, 9, 12, 20, 60):
            call(npc, affinity, charisma)
check("無事故: ctx.log_exc が1度も呼ばれない", not ctx.errors, ctx.errors)

print("")
if failures:
    print("FAILED: {} 件".format(len(failures)))
    for name in failures:
        print("  - " + name)
    raise SystemExit(1)
print("all checks passed")
