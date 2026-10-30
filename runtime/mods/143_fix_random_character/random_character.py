# -*- coding: utf-8 -*-
"""143_fix_random_character: さいころで作るキャラを世界観に合わせ、作りもばらつかせる（DOC.md）。

キャラを作る画面のさいころは `llm_manager_character_create:create_character(talent_point_left)` を呼ぶ。
引数は才能点だけで、頼み文に世界の情報が1字も入らない。
続けて立ち絵の英語の要素を作る `create_look` も世界を渡していない（GAME.md §2.35）。
才能点は貯まっている点がそのまま渡り、頼み文の目安（30 で一端・150 で伝説）に引かれて
毎回同じくらいの点数・年齢に寄る。

この MOD がすること:

  世界     さいころと `create_look` と、作成画面のスキル・特質の生成（`create_skill` /
           `create_trait`）の頼み文に、作成画面の世界の名前・概要・情勢と
           「世界に合わせること」の指示を足す（スキルと特質も世界の技術・文化から作る）
  作り     さいころの頼み文に、さいころで引いた才能点・年齢・能力値の目安を足す

頼み文の役の一文（「ダークファンタジーRPG」）と絵の前置きの medieval を外すのは `144_`。

頼み文はローダの `llm.wrap_outgoing`（`132_` と同じ）で、本文の印で見分ける:

  create_character  「プレイヤーキャラクター」「ランダムに」「才能点」の3語
  create_look       「主人公の外見のカテゴリ」と「画像生成プロンプト」の2語

世界は次の順で決める:

  1. 作成画面（`scripts.create_world:StoryApp`）が受け取った `world_name`。
     画面を作るとき・さいころ・外見の生成のたびに控える
  2. 控えが無ければ、遊んでいる世界（`app.world_dict`）。
     `335_` が遊んでいる最中に `create_look` を呼ぶのはこちら

作成画面の時点の `app.world_dict` は前に遊んだ別の世界のことがあるので、1 を先に見る。
タイトルの「続きから」（`InstantaleApp.load_game_new`）で控えを捨てる。
世界の中身は `app.world_dict` の世界名が一致すればそれ、違えば
`worlds\\<世界名>\\world_data.json` をローダの `saves.decode` で読む。
"""

from __future__ import annotations

import io
import os
import random
import re
import sys
import threading
import time

from instantale_modloader import frames, llm, loads, saves, state, ui
from instantale_modloader.llm import wrap_outgoing

LOG_BASENAME = "random_character.log"

# ---------------------------------------------------------------- 設定
# mod.json の "settings" と同じ名前・同じ既定値（ローダが apply() の前に書き込む）。
ADD_WORLD = True             # 頼み文に世界を足すか
ADD_SITUATION = True         # 世界の情勢（story.world_situation）も渡すか
VARY_BUILD = True            # さいころの作り（才能点・年齢・能力値）を引いて渡すか
TALENT_MIN = 10              # さいころで使う才能点の下限
TALENT_MAX = 150             # さいころで使う才能点の上限（貯まっている点が少なければそこまで）
LOG_DETAIL = True            # [SCREEN] / [WORLD] / [BUILD] / [COST] を残すか

# ---------------------------------------------------------------- 印
MARK_CHARACTER = ("プレイヤーキャラクター", "ランダムに", "才能点")
MARK_LOOK = ("主人公の外見のカテゴリ", "画像生成プロンプト")
#: 作成画面のスキル・特質の生成（`create_skill` / `create_trait`。GAME.md §2.35）。
MARK_SKILL = "キャラクターに追加する1つのアクティブスキル"
MARK_TRAIT = "キャラクターに追加する1つの特質"
#: create_look の user 本文の書き出し。世界はこの本文の後ろに足す。
LOOK_USER = "主人公の名前"
#: create_skill / create_trait の user 本文の書き出し。
DATA_USER = "【キャラクターのデータ】"
HEAD = "【この世界のデータ】"
HEAD_RULES = "【世界に合わせること】"
HEAD_BUILD = "【今回の作り】"
#: さいころの頼み文の才能点の行（`今回利用可能な才能点：**2497**`）。
TALENT_LINE = re.compile(r"今回利用可能な才能点[：:]\s*\**\s*(\d+)")

#: 同梱の llama.cpp で動くときの世界の文1つの上限（字）。実データの概要は 200〜300 字だった。
#: クラウドと任意の OpenAI 互換サーバーでは切らない（`llm.text_limit`）。
TEXT_LIMIT = 800

RULES_COMMON = (
    "- 冒頭の役の説明にあるジャンル（ダークファンタジーなど）より、この世界のデータを優先する。",
)
#: スキルと特質の決まり。さいころと、作成画面のスキル・特質の生成の両方で使う。
#: 属性の例（火・水・毒・闇）と「魔法」の語に引かれて、魔法の無い世界でも薬草・羊・精霊の
#: スキルや特質が出た（VERIFICATION.md §3.93）。
#: 「概要に無いものは使わない」と書くと、剣や魔法を書いていないだけの短い概要の世界で
#: それらまで避けかねない。食い違うものだけを止め、触れていないことは雰囲気から補わせる
#: （2026-10-11 の決定。VERIFICATION.md §3.93）。
RULE_FIT = ("世界の概要と食い違うもの（概要が否定している技術・魔法、世界の雰囲気に合わない生き物・道具・職業）"
            "は使わない。概要が触れていないことは、概要の雰囲気から自然に補ってよい。")
RULE_SKILL = ("- スキルの名前・属性（element）・効果は、この世界の技術・道具・文化から作る。"
              "属性は例の火・水・毒・闇に寄せず、この世界で通じる言葉にする。" + RULE_FIT)
RULE_TRAIT = "- 特質は、この世界での暮らし・生業・体験から作る。" + RULE_FIT
RULES_CHARACTER = RULES_COMMON + (
    "- 名前・出自・生業・装い・持ち物は、この世界にありうるものにする。" + RULE_FIT,
    "- 出自は、この世界に暮らす人々のさまざまな身分・生業から選ぶ。傭兵や兵士に決め打ちしない。",
    RULE_SKILL,
    RULE_TRAIT,
    "- look の image_generation_prompt にも、この世界の装い・技術水準・種族が分かる英語の要素を入れる。"
    "中世の鎧や外套を決まりの形にしない。",
)
RULES_LOOK = RULES_COMMON + (
    "- image_generation_prompt の装い・持ち物は、この世界の装い・技術水準に合う英語の要素にする。"
    "中世の鎧や外套を決まりの形にしない。",
    "- 外見文に、この世界に固有の種族や体の特徴があれば要素に入れる。",
)
RULES_SKILL = RULES_COMMON + (RULE_SKILL,)
RULES_TRAIT = RULES_COMMON + (RULE_TRAIT,)
RULES_OF = {"character": RULES_CHARACTER, "look": RULES_LOOK,
            "skill": RULES_SKILL, "trait": RULES_TRAIT}
#: 世界を足す種類（作り [BUILD] はさいころだけ）。
WORLD_KINDS = tuple(RULES_OF)

# ---------------------------------------------------------------- 作り
#: 年齢ごとの才能点（44 歳が 0）。ゲームの頼み文が書く目安の写し（GAME.md §2.35）。
#: 45 歳から上は1歳ごとに1点戻る。
AGE_COST = {44: 0, 43: 0, 42: 0, 41: 0, 40: 1, 39: 1, 38: 1, 37: 2, 36: 2, 35: 3, 34: 3,
            33: 4, 32: 5, 31: 6, 30: 7, 29: 9, 28: 11, 27: 14, 26: 18, 25: 23, 24: 29,
            23: 36, 22: 44, 21: 54, 20: 66}
AGE_MIN, AGE_MAX = 20, 50
#: 年齢に回してよい才能点の割合の上限。ゲームの頼み文は「年齢で 1/3」。
AGE_SHARE = 0.5
ATTRIBUTES = (("str", "筋力"), ("dex", "器用"), ("con", "耐久"),
              ("int", "知力"), ("wis", "賢さ"), ("cha", "魅力"))
RNG = random.Random()

# ---------------------------------------------------------------- 作成画面の世界の控え
STORY_APP = "scripts.create_world:StoryApp"
#: 控えはプロセスに1つ。注入し直しても作成画面の世界を忘れないよう sys に置く。
HOLDER_ATTR = "_instantale_143_creation_world"
CACHE_ATTR = "_instantale_143_world_cache"
#: いちばん新しいさいころの作り（画面に反映された後の [COST] の記録に使う）。
BUILD_ATTR = "_instantale_143_last_build"
#: 画面の残りの点の表示（`残り才能: 2497(-31)`。括弧の中がこのキャラの使う点）。
#: 画面は残りを属性に持たず、この文字にだけ出す（VERIFICATION.md §3.93）。
REMAINING_LABEL = "残り才能"
REMAINING_DELAY = 0.5


def remaining_text():
    """作成画面の「残り才能: …」の文字。見つからなければ None。"""
    try:
        from kivy.app import App
        root = App.get_running_app().root
        for widget in root.walk(restrict=False):
            text = getattr(widget, "text", None)
            if isinstance(text, str) and text.startswith(REMAINING_LABEL):
                return text
    except Exception:
        return None
    return None


def creation_world():
    """作成画面の世界名。控えが無ければ None。"""
    found = getattr(sys, HOLDER_ATTR, None)
    return found if isinstance(found, str) and found else None


def remember_world(name):
    if isinstance(name, str) and name:
        setattr(sys, HOLDER_ATTR, name)
        return True
    return False


def forget_world():
    setattr(sys, HOLDER_ATTR, None)


# ================================================================== 見分け

def classify(texts):
    """本文の並びから `(種類, 足す位置)` を返す。対象でなければ None。

    種類は `character`（さいころ）/ `look`（立ち絵の要素）/ `skill` / `trait`
    （作成画面のスキル・特質の生成）。
    """
    if not texts:
        return None
    joined = "\n".join(texts)
    if all(word in joined for word in MARK_CHARACTER):
        for index, text in enumerate(texts):
            if all(word in text for word in MARK_CHARACTER):
                return "character", index
        return "character", len(texts) - 1
    if all(word in joined for word in MARK_LOOK):
        for index, text in enumerate(texts):
            if LOOK_USER in text:
                return "look", index
        return "look", len(texts) - 1
    for kind, mark in (("skill", MARK_SKILL), ("trait", MARK_TRAIT)):
        if mark in joined:
            for index, text in enumerate(texts):
                if DATA_USER in text:
                    return kind, index
            return kind, len(texts) - 1
    return None


# ================================================================== 世界

def clip(value, limit=TEXT_LIMIT):
    """1行に収めた文。文字列でなければ ""。`limit` が None なら切らず、改行も元のまま。"""
    if not isinstance(value, str):
        return ""
    if limit is None:
        return value.strip()
    got = " ".join(value.split())
    return got if len(got) <= limit else got[:limit] + "…"


def world_facts(world_dict, situation=None, limit=TEXT_LIMIT):
    """`world_dict` から `[(見出し, 文), ...]`。読めなければ []。概要と情勢は `limit` 字まで（None なら全文）。"""
    if not isinstance(world_dict, dict):
        return []
    data = world_dict.get("world_data")
    if not isinstance(data, dict):
        return []
    situation = ADD_SITUATION if situation is None else situation
    facts = []
    name = state.world_key_of_dict(world_dict)
    if name:
        facts.append(("世界の名前", clip(name, 80)))
    overview = clip(data.get("overview"), limit)
    if overview:
        facts.append(("世界の概要", overview))
    story = data.get("story")
    if situation and isinstance(story, dict):
        now = clip(story.get("world_situation"), limit)
        if now:
            facts.append(("世界の情勢", now))
    # 名前だけでは世界観にならない。
    return facts if any(head != "世界の名前" for head, _text in facts) else []


def world_block(kind, facts):
    """足す世界の塊。`facts` が空なら ""。"""
    if not facts:
        return ""
    rules = RULES_OF.get(kind, RULES_LOOK)
    lines = [HEAD]
    lines += ["- {}: {}".format(head, text) for head, text in facts]
    lines.append(HEAD_RULES)
    lines += list(rules)
    return "\n".join(lines) + "\n"


def read_world_file(name, base=""):
    r"""`worlds\<name>\world_data.json` を辞書にする。読めなければ None。

    中身は変わらない限り読み直さない（作成画面ではさいころのたびに来る）。
    """
    path = os.path.join(saves.worlds_dir(base), name, "world_data.json")
    try:
        stamp = os.stat(path).st_mtime
    except OSError:
        return None
    cache = getattr(sys, CACHE_ATTR, None)
    if isinstance(cache, dict) and cache.get("key") == (path, stamp):
        return cache.get("data")
    try:
        with io.open(path, "rb") as fh:
            data = saves.decode(fh.read())
    except OSError:
        return None
    setattr(sys, CACHE_ATTR, {"key": (path, stamp), "data": data})
    return data


def current_world(app, base=""):
    """`(世界の辞書 or None, 世界名 or None, 出どころ)`。

    出どころは記録用で、`app`（遊んでいる世界と同じ）/ `file` / `none`。
    """
    wanted = creation_world()
    world_dict = getattr(app, "world_dict", None) if app is not None else None
    loaded = state.world_key_of_dict(world_dict)
    if wanted is None:
        if loaded:
            return world_dict, loaded, "app"
        return None, None, "none"
    if loaded == wanted:
        return world_dict, wanted, "app"
    data = read_world_file(wanted, base)
    if data is None:
        return None, wanted, "none"
    return data, wanted, "file"


# ================================================================== 作り

def age_cost(age):
    """その年齢にする才能点の目安。45 歳から上は負（点が戻る）。"""
    if age > 44:
        return 44 - age
    return AGE_COST.get(age, AGE_COST[AGE_MIN])


def available_talent(text):
    """さいころの頼み文に書かれた、使える才能点。読めなければ None。"""
    found = TALENT_LINE.search(text or "")
    return int(found.group(1)) if found else None


def plan_build(available, rng=None, low=None, high=None):
    """さいころの作りを引く。`{"talent", "age", "high", "low"}`。使える点が無ければ None。

    年齢を先に引く。若いほど出やすい重み（`AGE_MAX + 1 - 年齢`。平均は約 30 歳）。
    才能点は `[下限, 上限]` から一様に引き（上限は使える点で頭を打つ）、
    引いた年齢の目安が才能点の `AGE_SHARE` を超えるなら、届くところまで才能点を引き上げる
    （ゲームの決まりでは若くするほど点が要る。若いキャラほど点を多く使う）。
    引き上げても届かない（使える点が足りない）ときは、届く年齢から同じ重みで引き直す。
    「届く年齢から一様に」引くと 40 代に寄った（VERIFICATION.md §3.93）。
    能力値は6つの順を混ぜ、先頭2つを高く、末尾1つを低くする。
    """
    rng = RNG if rng is None else rng
    low = TALENT_MIN if low is None else low
    high = TALENT_MAX if high is None else high
    if not isinstance(available, int) or available <= 0:
        return None
    top = min(max(0, high), available)
    if top <= 0:
        return None
    bottom = min(max(0, low), top)
    talent = rng.randint(bottom, top)
    ages = list(range(AGE_MIN, AGE_MAX + 1))
    weight = lambda age: AGE_MAX + 1 - age  # noqa: E731
    age = rng.choices(ages, weights=[weight(a) for a in ages])[0]
    need = -(-age_cost(age) // AGE_SHARE) if age_cost(age) > 0 else 0
    if talent < need:
        talent = min(int(need), top)
    if age_cost(age) > talent * AGE_SHARE:
        fits = [a for a in ages if age_cost(a) <= talent * AGE_SHARE]
        age = rng.choices(fits, weights=[weight(a) for a in fits])[0]
    order = list(ATTRIBUTES)
    rng.shuffle(order)
    return {"talent": talent, "age": age, "high": order[:2], "low": order[-1:]}


def build_block(plan):
    """足す作りの塊。"""
    names = lambda pairs: "・".join("{}（{}）".format(key, label) for key, label in pairs)  # noqa: E731
    floor = max(1, int(plan["talent"] * 0.8))
    return "\n".join((
        HEAD_BUILD,
        "さいころで決めた目安。上の【指示】の配分の目安より優先する。",
        "- 使う才能点: およそ {} 点（{}〜{} 点）。利用可能な才能点が多くても、この点数を目安にする".format(
            plan["talent"], floor, plan["talent"]),
        "- 年齢: {} 歳前後（前後2歳まで）".format(plan["age"]),
        "- 能力値: {} を高くし、{} を最も低くする".format(names(plan["high"]), names(plan["low"])),
        "- 出自・スキル・特質の強さも、この点数と年齢に見合うものにする",
    )) + "\n"


# ================================================================== 頼み文の書き換え

def rewrite(texts, facts, plan=None):
    """本文の並びを書き換える。`(新しい並び or None, 種類 or None, したこと)`。

    したことは `world` / `build` の並び（記録用）。
    """
    found = classify(texts)
    if found is None:
        return None, None, []
    kind, index = found
    new_texts = list(texts)
    done = []
    blocks = []
    if kind in WORLD_KINDS and HEAD not in "\n".join(texts):
        block = world_block(kind, facts)
        if block:
            blocks.append(block)
            done.append("world")
    if kind == "character" and plan and HEAD_BUILD not in "\n".join(texts):
        blocks.append(build_block(plan))
        done.append("build")
    if blocks:
        new_texts[index] = new_texts[index].rstrip("\n") + "\n\n" + "\n".join(blocks)
    if new_texts == list(texts):
        return None, kind, []
    return new_texts, kind, done


# ================================================================== 本体

def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    lock = threading.Lock()

    def note(line):
        if LOG_DETAIL:
            write(line)

    def from_screen(where, value):
        if remember_world(value):
            note("[SCREEN] {}: 作成画面の世界 = {}".format(where, value))

    @ctx.wrap(STORY_APP + ".__init__", required=False, safe=True)
    def story_init(orig, self, *args, **kwargs):
        from_screen("open", frames.arg(args, kwargs, "world_name", 2))
        return orig(self, *args, **kwargs)

    # 注入し直したときは画面がもう開いているので、押下のたびに画面の値を控え直す。
    @ctx.wrap(STORY_APP + ".generate_random_character", required=False, safe=True)
    def generate_random_character(orig, self, *args, **kwargs):
        from_screen("dice", frames.attr(self, "world_name", None))
        return orig(self, *args, **kwargs)

    @ctx.wrap(STORY_APP + ".generate_look_async", required=False, safe=True)
    def generate_look_async(orig, self, *args, **kwargs):
        from_screen("look", frames.attr(self, "world_name", None))
        return orig(self, *args, **kwargs)

    # さいころの返答が画面に載った後に、画面が数えた才能点を記録する（目安と突き合わせる）。
    @ctx.wrap(STORY_APP + ".apply_character_sheet", required=False, safe=True)
    def apply_character_sheet(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        plan = getattr(sys, BUILD_ATTR, None)
        if isinstance(plan, dict):
            setattr(sys, BUILD_ATTR, None)
            vm = frames.attr(self, "character_vm", None)

            def record(*_args):
                note("[COST] 目安 {} 点・{} 歳 | 画面: {} / 才能点 {} / 年齢 {}".format(
                    plan.get("talent"), plan.get("age"), remaining_text() or "残り ?",
                    frames.attr(self, "talent_point", "?"), frames.attr(vm, "age", "?")))

            # 表示は載せた後に書き換わるので、少し置いてから読む。
            try:
                from kivy.clock import Clock
                Clock.schedule_once(record, REMAINING_DELAY)
            except Exception:
                record()
        return result

    def before_load(call):
        """ロードの前（ローダの窓口 `loads`。TECH.md §3.3.21）。"""
        if creation_world() is not None:
            forget_world()
            note("[SCREEN] load: 作成画面の世界の控えを捨てた")

    loads.on(os.path.basename(getattr(ctx, "mod_dir", "") or "") or "143_fix_random_character", ctx, (loads.LOAD,), before=before_load)

    def run(texts, site):
        """ローダから、1回の推論で出ていく本文の並びとして呼ばれる。変えなければ None。"""
        found = classify(texts)
        if found is None:
            return None
        kind, index = found
        started = time.monotonic()
        facts, name, source = [], None, "-"
        limit = TEXT_LIMIT
        if ADD_WORLD and kind in WORLD_KINDS:
            with lock:
                world_dict, name, source = current_world(ui.find_app())
            limit = llm.text_limit(TEXT_LIMIT)
            facts = world_facts(world_dict, limit=limit)
            if not facts:
                write("[SKIP] {} {} | 世界が読めない（{}）".format(
                    site, kind, name or "世界名が分からない"))
        plan = None
        if VARY_BUILD and kind == "character":
            plan = plan_build(available_talent(texts[index]))
        new_texts, kind, done = rewrite(texts, facts, plan)
        if new_texts is None:
            return None
        if "build" in done:
            setattr(sys, BUILD_ATTR, plan)
        size = (" 全文" if limit is None else " {}字まで".format(limit)) if "world" in done else ""
        note("[WORLD] {} {} | {}{} | {} from {} ({:.1f}ms)".format(
            site, kind, "+".join(done), size, name, source, (time.monotonic() - started) * 1000))
        if "build" in done:
            note("[BUILD] 才能点 {} / 年齢 {} / 高 {} / 低 {}".format(
                plan["talent"], plan["age"], "・".join(k for k, _l in plan["high"]),
                "・".join(k for k, _l in plan["low"])))
        return new_texts

    hooks = wrap_outgoing(
        ctx, run, label="random character",
        on_arm=lambda target: write("[ARM] 遅れて仕掛けた: {}".format(target)))

    _verify(ctx)

    armed = hooks.armed()
    ctx.log("random character: armed on {} | log {}".format(
        ", ".join(armed) if armed else "nothing (targets missing)", log_path))


# --------------------------------------------------------------------------
# 自己検証
# --------------------------------------------------------------------------
_SAMPLE_WORLD = {"world_data": {"name": "見本の世界", "overview": "見本の概要。",
                                "story": {"world_situation": "見本の情勢。"}}}
_SAMPLE_CHARACTER = ["あなたは大人向けの無骨で無慈悲なダークファンタジーRPGのキャラクター生成AIです。",
                     "**1体のプレイヤーキャラクター**を**ランダムに**生成してください。"
                     "- 今回利用可能な才能点：**2497**"]
_SAMPLE_LOOK = ["あなたはダークファンタジーRPGの管理者です。物語の主人公の外見のカテゴリと"
                "立ち絵の英語の画像生成プロンプトを設定してください。",
                "【設定】- 主人公の名前: 見本 - 外見: 見本の外見"]
_SAMPLE_OTHER = ["あなたはダークファンタジーRPGのキャラクター生成AIだ。",
                 "【生成するNPC】- 名前: 宿屋の主人"]


def _verify(ctx):
    facts = world_facts(_SAMPLE_WORLD, situation=True)
    plan = plan_build(2497, random.Random(1), 10, 150)
    got, kind, done = rewrite(_SAMPLE_CHARACTER, facts, plan)
    if got is None or kind != "character" or done != ["world", "build"] \
            or HEAD not in got[1] or HEAD_BUILD not in got[1] or got[0] != _SAMPLE_CHARACTER[0]:
        ctx.log("VERIFY FAILED: character rewrite {!r}".format(got), level="ERROR")
        return
    if rewrite(got, facts, plan)[0] is not None:
        ctx.log("VERIFY FAILED: the world was added twice", level="ERROR")
        return
    got, kind, done = rewrite(_SAMPLE_LOOK, facts)
    if got is None or kind != "look" or HEAD not in got[1] or done != ["world"]:
        ctx.log("VERIFY FAILED: look rewrite {!r}".format(got), level="ERROR")
        return
    if rewrite(_SAMPLE_OTHER, facts)[0] is not None:
        ctx.log("VERIFY FAILED: an NPC prompt was touched", level="ERROR")
        return
    if not (10 <= plan["talent"] <= 150 and AGE_MIN <= plan["age"] <= AGE_MAX
            and age_cost(plan["age"]) <= plan["talent"] * AGE_SHARE):
        ctx.log("VERIFY FAILED: build {!r}".format(plan), level="ERROR")
        return
    ctx.log("random character: self-check passed")
