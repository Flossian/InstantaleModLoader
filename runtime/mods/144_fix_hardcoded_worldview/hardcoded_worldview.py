# -*- coding: utf-8 -*-
"""144_fix_hardcoded_worldview: 頼み文と絵の世界観の決め打ちを外す（DOC.md）。

ゲームの頼み文の約30種は「あなたはダークファンタジーTRPGのゲームマスターである」のように、
役の一文でジャンルを決め打ちしている。うち約20種には世界の情報が1字も入っていない。
ほかにも、世界を作る頼みの【共通世界観】が冒険者の暗い暮らしを決め、
犯罪の許可の理由を「ダークファンタジーであるため」と書き、
絵の前置きには `medieval` `dark fantasy` `shabby` `fantasy setting` が、
背景の否定には `modern` `urban` が必ず入る（GAME.md §2.35 / §2.36）。

この MOD がすること（どれも設定で個別に切れる）:

  役の一文   「あなたは（いわゆる）（大人向けの）（無骨で無慈悲な）ダークファンタジーTRPG / RPG」と
             「あなたは中世ファンタジーRPG」からジャンルの語を外す。
             `111_` の同梱ルールが会話に足す「この世界は救いのないダークファンタジーであり、
             優等生的・現代的な言い回し…」も「優等生的な言い回し…」にする
  世界を足す 役の一文のある頼みに世界の情報が無ければ、遊んでいる世界の名前と概要を足す。
             キャラ作成の5つ（さいころ・立ち絵の要素・スキル・特質・内容の審査）と世界の生成は足さない
             （作成画面の世界は `143_` が扱う。`app.world_dict` は作成中の世界とは限らない）
  世界なし   世界の情報が無く足せもしない頼みは、ジャンルに関わる書き換えをせずゲームの既定のまま送る
             （概要を空にした世界の生成、世界が読めないときのキャラ作成など）
  許可の理由 「ダークファンタジーであるため、」を「ゲームであるため、」にする（許可そのものは残す）
  魔法の前提 特質の生成の「この世界の誰もが魔法、精霊、神、悪魔…を唱えられるわけでは無し」を外す
             （役の一文と同じ設定）
  共通世界観 【共通世界観】の冒険者の項を、ギルドと冒険者がいることだけにする
             （エリアの項はゲームの仕組みの説明なので残す）
  絵         描く関数へ渡る前置きから medieval / dark fantasy / shabby / fantasy setting の要素を、
             否定から modern / urban の要素を抜く（人物・背景・敵）。
             頼み文の「自動で足す」一覧からも medieval を抜く。人物の prompts.json も同じに直す

頼み文はローダの `llm.wrap_outgoing`。`111_` が足した文も直すので、`111_` より内側に置く
（`mod.json` の `before`）。`143_` が足した世界を見るので `143_` よりも内側（`143_` の `after`）。
見分けは本文の語で、`manager_name` は見ない。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time

from instantale_modloader import frames, imagegen, patch, saves, state, ui
from instantale_modloader.llm import wrap_outgoing

LOG_BASENAME = "hardcoded_worldview.log"

# ---------------------------------------------------------------- 設定
# mod.json の "settings" と同じ名前・同じ既定値（ローダが apply() の前に書き込む）。
ROLE_LINE = True             # 役の一文と会話の調子からジャンルの語を外す
ADD_WORLD = True             # 世界の情報が無い頼みに、遊んでいる世界の名前と概要を足す
POLICY_REASON = True         # 許可の理由を「ゲームであるため」にする
COMMON_WORLD = True          # 【共通世界観】の冒険者の項を絞る
IMAGE_PREFIX = True          # 絵の前置きと否定から決め打ちの語を抜く
LOG_DETAIL = True            # [TEXT] / [IMAGE] を残すか

# ---------------------------------------------------------------- 頼み文
#: 役の一文。「あなたは」に続くジャンルの語だけを外す（世界の文の中の語には当てない）。
ROLE = re.compile(r"あなたは(?:いわゆる)?(?:大人向けの)?(?:無骨で無慈悲な)?"
                  r"(?:ダークファンタジー|中世ファンタジー)(T?RPG)")
#: `111_` の同梱ルールが会話の頼みに足す文（llm_replacements.default.txt の「生成文言改変」）。
TONE_FROM = "この世界は救いのないダークファンタジーであり、優等生的・現代的な言い回しや不要な気遣いはしない。"
TONE_TO = "優等生的な言い回しや不要な気遣いはしない。"
POLICY_FROM = "ダークファンタジーであるため、"
POLICY_TO = "ゲームであるため、"
#: キャラ作成の特質の生成（`create_trait`）の【その他】。魔法・精霊・神がいる世界を前提にしている。
#: 役の一文と同じ設定で外す（GAME.md §2.36）。
MAGIC_FROM = ("この世界の誰もが魔法、精霊、神、悪魔、自然の力を借りた大いなる術を唱えられるわけでは無し、"
              "だからといって代わりに何らか戦いのための明確な力をもつとも限らない。")
MAGIC_TO = "誰もが戦いのための明確な力をもつとは限らない。"
#: 【共通世界観】の冒険者の項。次の「- エリア:」か、次の【見出し】か、文の終わりの手前まで。
#: 町の生成は後ろに「- エリア:」の項が続くが、世界とストーリーの生成は冒険者の項だけで
#: すぐ【予め指定済みの設定】や【手順】が来る（項の中に改行も入る。GAME.md §2.36）。
COMMON = re.compile(r"(- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいる)が、.*?"
                    r"(?=- エリア:|\s*【|\Z)", re.S)
#: 呼び名は世界に合わせてよいが、施設の種類（type）は決まった値のまま使うと書く。
#: 「呼び名や在り方は合わせてよい」だけだと、町の生成で施設の type に `facility` と書く返答が
#: 続けて出た（Claude。VERIFICATION.md §3.95）。
COMMON_TO = (r"\1。ギルドと冒険者の呼び名や描写は、その世界に合わせてよい。"
             r"ただし施設の種類（type）は guild など決められた値をそのまま使う。")
#: 頼み文の「自動で足す」一覧（`[medieval, masterpiece, ...]`）。
MEDIEVAL_IN_LIST = re.compile(r"\[\s*medieval\s*,\s*")

#: 世界の情報が既に入っている頼みの印。「世界の概要」はコロンまで見る
#: （概要を空にした世界の生成は「面白そうな世界の概要を生成してください」と書くだけで、概要は無い）。
#: 会話は世界の概要を持たず、その土地の説明（【土地情報】）を持つので、それも世界の情報として数える。
WORLD_MARKS = ("世界の概要:", "【世界", "【この世界", "【土地情報】", "world_overview")
#: 世界を足さない頼み。キャラ作成の5つと世界の生成（どちらも作っている世界は app.world_dict と
#: 違うことがある）。概要を空にした世界の生成は、世界の情報が無いのでゲームの既定のまま送る。
NO_WORLD_MARKS = ("プレイヤーキャラクター", "主人公の外見のカテゴリ", "主人公キャラクターの設定を提出",
                  "キャラクターに追加する1つの", "世界そのものを生成する担当")
HEAD_WORLD = "【この世界】"

# ---------------------------------------------------------------- 絵
POSITIVE_DROP = ("medieval", "dark fantasy", "shabby", "fantasy setting")
NEGATIVE_DROP = ("modern", "urban")
#: 前置き込みの文を受け取る関数と、(前置き, 否定) の位置引数の番号（GAME.md §2.35）。
#: 方式によって無い関数もある（無ければ包まずに進む）。
CREATURE_FUNCS = (("generate_image_anime", 0, 1), ("generate_upscaled_image_anime", 0, 1),
                  ("generate_image_real_lcm", 0, 1), ("image_to_image_anime", 1, 2),
                  ("generate_character_image_from_enemy", 3, 4))
BACKGROUND_MODULE = "image_generation.{}.image_generation_background"
BACKGROUND_FUNCS = (("generate_and_save_background", 2, 3), ("generate_image_real_lcm", 0, 1))


# ================================================================== 頼み文

def clip(value, limit=None):
    """`limit` 字で切って1行に畳んだ文。`limit` が None なら切らず、改行も元のまま。"""
    if not isinstance(value, str):
        return ""
    if limit is None:
        return value.strip()
    got = " ".join(value.split())
    return got if len(got) <= limit else got[:limit] + "…"


def world_block(world_dict):
    """足す世界の塊。名前か概要が読めなければ ""。

    概要は切らずに全文足す（同梱のローカル LLM の窓にも収まる。VERIFICATION.md §3.95）。
    """
    if not isinstance(world_dict, dict):
        return ""
    data = world_dict.get("world_data")
    if not isinstance(data, dict):
        return ""
    name = state.world_key_of_dict(world_dict)
    overview = clip(data.get("overview"))
    if not overview:
        return ""
    lines = [HEAD_WORLD]
    if name:
        lines.append("- 世界の名前: {}".format(clip(name, 80)))
    lines.append("- 世界の概要: {}".format(overview))
    lines.append("- 描写・人物・品物は、この世界の技術・文化・雰囲気に合わせる。")
    return "\n".join(lines)


def rewrite(texts, world_dict=None, *, role=True, add_world=True, policy=True, common=True,
            image=True):
    """本文の並びを書き換える。`(新しい並び or None, したことの並び)`。

    したことは `role` / `tone` / `magic` / `world` / `policy` / `common` / `list`（記録用）。

    ジャンルに関わる書き換え（役の一文・会話の調子・魔法の前提・【共通世界観】・「自動で足す」一覧）は、
    頼みに世界の情報があるか、ここで世界を足せたときだけする。
    世界の情報が無い・読めない頼みはゲームの既定（ダークファンタジー）のまま送る
    （ジャンルだけ外すと、生成 AI が世界観を何も知らない頼みになる。2026-10-11 の決定。VERIFICATION.md §3.95）。
    キャラ作成の頼みに世界を足すのは `143_` で、`143_` が足した【この世界のデータ】もここで世界の情報として見る
    （`143_` が外側に居て先に足す。`143_` の `mod.json` の `after`）。
    許可の理由はジャンルではないので、世界の有無に関わらず直す。
    """
    if not texts:
        return None, []
    joined = "\n".join(texts)
    has_world = any(m in joined for m in WORLD_MARKS)
    block = ""
    role_at = next((i for i, t in enumerate(texts) if ROLE.search(t)), None)
    if (role and add_world and not has_world and role_at is not None
            and not any(m in joined for m in NO_WORLD_MARKS)):
        block = world_block(world_dict)
    genre = has_world or bool(block)

    new_texts = list(texts)
    done = []

    def mark(name):
        if name not in done:
            done.append(name)

    for index, text in enumerate(new_texts):
        fixed = text
        if role and genre:
            fixed, count = ROLE.subn(r"あなたは\1", fixed)
            if count:
                mark("role")
            if TONE_FROM in fixed:
                fixed = fixed.replace(TONE_FROM, TONE_TO)
                mark("tone")
            if MAGIC_FROM in fixed:
                fixed = fixed.replace(MAGIC_FROM, MAGIC_TO)
                mark("magic")
        if policy and POLICY_FROM in fixed:
            fixed = fixed.replace(POLICY_FROM, POLICY_TO)
            mark("policy")
        if common and genre:
            fixed, count = COMMON.subn(COMMON_TO, fixed)
            if count:
                mark("common")
        if image and genre:
            fixed, count = MEDIEVAL_IN_LIST.subn("[", fixed)
            if count:
                mark("list")
        new_texts[index] = fixed

    if block:
        new_texts[role_at] = new_texts[role_at].rstrip("\n") + "\n\n" + block + "\n"
        mark("world")
    if new_texts == list(texts):
        return None, []
    return new_texts, done


# ================================================================== 絵

def drop_tokens(prompt, words):
    """カンマ区切りの文から、`words` のどれかと同じ要素だけを抜く。抜かなければそのまま返す。

    要素の前後の空白は元のまま残す（`full-body, masterpiece,high quality, ...` の書き方を崩さない）。
    `medieval fantasy cloak` のような別の語は抜かない。大文字小文字は見ない。
    """
    if not isinstance(prompt, str):
        return prompt
    lowered = prompt.lower()
    if not any(word in lowered for word in words):
        return prompt
    parts = prompt.split(",")
    kept = [part for part in parts if part.strip().lower() not in words]
    if len(kept) == len(parts):
        return prompt
    got = ",".join(kept)
    # 先頭の要素を抜いたとき、次の要素の前の空白が頭に残る。
    return got.lstrip() if parts[0].strip().lower() in words else got


def fix_prompts_json(world, name, note=None, base=""):
    r"""`worlds\<world>\characters\<name>\prompts.json` からも同じ語を抜く。直したら真。"""
    if not (isinstance(world, str) and world and isinstance(name, str) and name):
        return False
    path = os.path.join(saves.worlds_dir(base), world, "characters", name, "prompts.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    changed = False
    for key, words in (("positive_prompt", POSITIVE_DROP), ("negative_prompt", NEGATIVE_DROP)):
        before = data.get(key)
        after = drop_tokens(before, words)
        if after != before:
            data[key] = after
            changed = True
    if not changed:
        return False
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    if note is not None:
        note("[IMAGE] prompts.json {}: 決め打ちの語を抜いた".format(name))
    return True


# ================================================================== 本体

def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    def note(line):
        if LOG_DETAIL:
            write(line)

    def run(texts, site):
        """ローダから、1回の推論で出ていく本文の並びとして呼ばれる。変えなければ None。"""
        started = time.monotonic()
        world_dict = None
        if ADD_WORLD and ROLE_LINE and any(ROLE.search(text) for text in texts):
            world_dict = getattr(ui.find_app(), "world_dict", None)
        new_texts, done = rewrite(texts, world_dict, role=ROLE_LINE, add_world=ADD_WORLD,
                                  policy=POLICY_REASON, common=COMMON_WORLD, image=IMAGE_PREFIX)
        if new_texts is None:
            return None
        head = next((t for t in texts if ROLE.search(t)), texts[0])
        note("[TEXT] {} {} | {} ({:.1f}ms)".format(
            site, "+".join(done), clip(head, 40), (time.monotonic() - started) * 1000))
        return new_texts

    hooks = wrap_outgoing(
        ctx, run, label="hardcoded worldview",
        on_arm=lambda target: write("[ARM] 遅れて仕掛けた: {}".format(target)))

    def strip_hook(target, positive_at, negative_at, kind):
        # 方式によって無い関数がある（sdcppcuda の `generate_upscaled_image_anime` は main_023 から無い）。
        # 読み込まれた方式に名前が無ければ包まない。包もうとすると台帳の「対象が見つからない」に載り、
        # GUI がゲームの更新を疑う警告を出していた。読み込まれる前はローダの保留に任せる。
        module_name, _, func = target.partition(":")
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, func, None) is None and                 not patch.still_loading(module_name):
            return

        @ctx.wrap(target, required=False, safe=True)
        def strip(orig, *args, **kwargs):
            if IMAGE_PREFIX:
                for name, index, words in (("positive_prompt", positive_at, POSITIVE_DROP),
                                           ("negative_prompt", negative_at, NEGATIVE_DROP)):
                    before = frames.arg(args, kwargs, name, index)
                    after = drop_tokens(before, words)
                    if after != before:
                        args, kwargs, _done = frames.replace_arg(args, kwargs, name, index, after)
                        note("[IMAGE] {} {} {}: 決め打ちの語を抜いた".format(
                            kind, target.rpartition(":")[2], name))
            return orig(*args, **kwargs)

    for family in imagegen.families():
        for func, positive_at, negative_at in CREATURE_FUNCS:
            strip_hook("{}:{}".format(imagegen.CREATURE_FORMAT.format(family), func),
                       positive_at, negative_at, "creature")
        for func, positive_at, negative_at in BACKGROUND_FUNCS:
            strip_hook("{}:{}".format(BACKGROUND_MODULE.format(family), func),
                       positive_at, negative_at, "background")

    # 人物の絵はゲームが前置き込みの文を prompts.json に書いてから描く。
    # 描いた後で、その記録からも同じ語を抜く（記録と絵を食い違わせない）。
    @imagegen.wrap_creature(ctx, "generate_character_image", required=False, safe=True)
    def generate_character_image(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        if IMAGE_PREFIX:
            fix_prompts_json(frames.arg(args, kwargs, "world_name", 0),
                             frames.arg(args, kwargs, "name", 1), note)
        return result

    _verify(ctx)

    armed = hooks.armed()
    ctx.log("hardcoded worldview: armed on {} | log {}".format(
        ", ".join(armed) if armed else "nothing (targets missing)", log_path))


# --------------------------------------------------------------------------
# 自己検証
# --------------------------------------------------------------------------
_SAMPLE_WORLD = {"world_data": {"name": "見本の世界", "overview": "見本の概要。"}}
_SAMPLE_NARRATOR = ["あなたはダークファンタジーTRPGのゲームマスターである。文体は常体。", "【ログ】"]
_SAMPLE_TOWN = ["あなたはダークファンタジーRPGのワールドマップジェネレーターです。",
                "【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、"
                "夢のある仕事ではない。- エリア: 世界はいくつかのエリアに分かれている。\n"
                "【世界のデータ】\n- 世界の概要: 見本"]
_SAMPLE_TALK = ["ロールプレイとして振舞ってください。" + TONE_FROM,
                "【世界の情報】\n- 世界の概要: 見本\n- ダークファンタジーであるため、窃盗は許可する。"]
#: 概要を空にした世界の生成（世界の情報が無い）。ゲームの既定のまま送る。
_SAMPLE_NO_WORLD = ["あなたはダークファンタジーRPGの世界そのものを生成する担当です。\n面白そうな世界の概要を生成してください。",
                    "【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、夢のある仕事ではない。"]
_SAMPLE_PREFIX = ("full-body, masterpiece,high quality, detailed face, medieval, dark fantasy, "
                  "watercolor, standing,shabby, young man, medieval fantasy cloak")


def _verify(ctx):
    got, done = rewrite(_SAMPLE_NARRATOR, _SAMPLE_WORLD)
    if got is None or done != ["role", "world"] or not got[0].startswith("あなたはTRPGのゲームマスター") \
            or "見本の概要。" not in got[0] or got[1] != _SAMPLE_NARRATOR[1]:
        ctx.log("VERIFY FAILED: narrator rewrite {!r}".format(got), level="ERROR")
        return
    if rewrite(got, _SAMPLE_WORLD)[0] is not None:
        ctx.log("VERIFY FAILED: rewrote twice", level="ERROR")
        return
    got, done = rewrite(_SAMPLE_TOWN, _SAMPLE_WORLD)
    if got is None or done != ["role", "common"] or "夢のある" in got[1] or "- エリア:" not in got[1] \
            or HEAD_WORLD in got[0]:
        ctx.log("VERIFY FAILED: town rewrite {!r}".format(got), level="ERROR")
        return
    got, done = rewrite(_SAMPLE_TALK, _SAMPLE_WORLD)
    if got is None or done != ["tone", "policy"] or "ダークファンタジー" in "".join(got):
        ctx.log("VERIFY FAILED: talk rewrite {!r}".format(got), level="ERROR")
        return
    if rewrite(_SAMPLE_NO_WORLD, _SAMPLE_WORLD)[0] is not None:
        ctx.log("VERIFY FAILED: a request without a world was rewritten", level="ERROR")
        return
    if rewrite(_SAMPLE_NARRATOR, None)[0] is not None:
        ctx.log("VERIFY FAILED: the genre was removed without a world", level="ERROR")
        return
    stripped = drop_tokens(_SAMPLE_PREFIX, POSITIVE_DROP)
    if stripped != ("full-body, masterpiece,high quality, detailed face, watercolor, standing,"
                    " young man, medieval fantasy cloak"):
        ctx.log("VERIFY FAILED: prefix {!r}".format(stripped), level="ERROR")
        return
    ctx.log("hardcoded worldview: self-check passed")
