# -*- coding: utf-8 -*-
"""世界概要から依頼を生成する。掲示板の依頼を、一定の確率で街の描写抜きで作らせる。

`llm_manager_world_generate:random_quest_generator` は毎回
街の概要・構造・周辺の描写（3欄）を読むので、同じ街に居る限り
似た場所・似た敵・似た文章の依頼が続く（GAME.md §2.11）。
確率で当たった回だけ、その3欄を「伏せてあるので世界の概要だけで作れ」の1文に差し替える。
街の名前と難易度と `world_overview` はそのまま渡す。
引数の書き換えだけなので出力スキーマ（QuestStructure）も呼び出し側も変わらない。

`301_quest_from_conversation` より外側に置く（`mod.json` の `after`）。
外側なら、当たった回でも 301 が `area_description` の末尾へ会話を足す側になるので、
会話から作る依頼の発端は消えない。
"""
import random

# ---------------------------------------------------------------- 設定（mod.json と同じ既定値）

#: 世界だけから作る確率（%）。
CHANCE_PERCENT = 30

#: 街の3欄の代わりに渡す文。
NOTE_WORLD_ONLY = "（この街の描写は意図的に伏せてある。世界の概要だけを手掛かりに、この街の特徴に依らない依頼を作ること。依頼人はこの街に居る人物とし、舞台は街の外の別の場所でもよい）"

LOG_BASENAME = "quest_from_world.log"


def roll(chance_percent, rnd=random.random):
    """当たりなら True。0 で決して当たらず、100 で必ず当たる。"""
    return rnd() * 100.0 < chance_percent


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False)
    def random_quest_generator(orig, world_overview, settlement_name,
                               settlement_overview, settlement_structure_description,
                               area_description, quest_difficulty, *args, **kwargs):
        if roll(CHANCE_PERCENT):
            write("world-only: settlement={!r} difficulty={!r}".format(
                settlement_name, quest_difficulty))
            settlement_overview = NOTE_WORLD_ONLY
            settlement_structure_description = NOTE_WORLD_ONLY
            area_description = NOTE_WORLD_ONLY
        else:
            write("as-is: settlement={!r} difficulty={!r}".format(
                settlement_name, quest_difficulty))
        return orig(world_overview, settlement_name, settlement_overview,
                    settlement_structure_description, area_description,
                    quest_difficulty, *args, **kwargs)

    ctx.log("quest_from_world: installed (chance={}%)".format(CHANCE_PERCENT))
