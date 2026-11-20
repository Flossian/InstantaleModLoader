# -*- coding: utf-8 -*-
"""世界概要から依頼を生成する。掲示板の依頼を、一定の確率で街の描写抜きで作らせる。

`llm_manager_world_generate:random_quest_generator` は毎回
街の概要・構造・周辺の描写（3欄）を読むので、同じ街に居る限り
似た場所・似た敵・似た文章の依頼が続く（GAME.md §2.11）。
確率で当たった回だけ、その3欄を「伏せてあるので世界の概要だけで作れ」の1文に差し替える。
街の名前と難易度と `world_overview` はそのまま渡す。
引数の書き換えだけなので出力スキーマ（QuestStructure）も呼び出し側も変わらない。

生成はローダの窓口 `quest_generation` が1枚だけ包み、この MOD は生成ごとの書き換え（`on`）を渡す（TECH.md §3.3.30）。
窓口は生成ごとの書き換えを、301/307/325/336/338 が置く1回だけの指示より先に当てる。
当たった回でも、会話・道のり・裏の仕事などの指示は伏せた後の `area_description` の末尾へ足されるので消えない。

版4: フックは引数を受け取った形のまま元の関数へ渡し、3欄は来た側に合わせて差し替える
（名前付きで受けて位置引数に直さない。TECH.md の規則の表）。
"""
import os
import random

from instantale_modloader import quest_generation

# ---------------------------------------------------------------- 設定（mod.json と同じ既定値）

#: 世界だけから作る確率（%）。
CHANCE_PERCENT = 30

#: 街の3欄の代わりに渡す文。
NOTE_WORLD_ONLY = "（この街の描写は意図的に伏せてある。世界の概要だけを手掛かりに、この街の特徴に依らない依頼を作ること。依頼人はこの街に居る人物とし、舞台は街の外の別の場所でもよい）"

LOG_BASENAME = "quest_from_world.log"

#: この MOD 専用の乱数（ゲーム全体の乱数の並びをずらさない。TECH.md §6.1）。
_rng = random.Random()


def roll(chance_percent, rnd=_rng.random):
    """当たりなら True。0 で決して当たらず、100 で必ず当たる。"""
    return rnd() * 100.0 < chance_percent


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "328_quest_from_world"

    def rewrite(request):
        """確率で当たった回だけ、街の3欄を伏せる（ローダの窓口の生成ごとの書き換え）。"""
        settlement_name = request.get("settlement_name")
        quest_difficulty = request.get("quest_difficulty")
        if roll(CHANCE_PERCENT):
            write("world-only: settlement={!r} difficulty={!r}".format(settlement_name, quest_difficulty))
            for name in ("settlement_overview", "settlement_structure_description", "area_description"):
                request.set(name, NOTE_WORLD_ONLY)
        else:
            write("as-is: settlement={!r} difficulty={!r}".format(settlement_name, quest_difficulty))

    quest_generation.on(owner, ctx, rewrite, write=write)

    ctx.log("quest_from_world: installed (chance={}%)".format(CHANCE_PERCENT))
