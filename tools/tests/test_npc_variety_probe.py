# -*- coding: utf-8 -*-
"""npc_variety_probe.py の、LLM を呼ばない部分をゲーム抜きで通す。

    python tools/tests/test_npc_variety_probe.py

確認するもの:

  頼み文     写し（npc_variety_prompts.json）が system 2通＋型定義で読める。
             plain は user 本文がゲームの形のまま、fields は要素名だけ、
             seed は引いた種が本文に載る。町は TOWN_ROLES の全員ぶん載る
  111_ の当て方  単体には「没個性を避けること」の行が実機と同じに入る
  読み取り   実機の町の返答から必須施設の主・住民・冒険者が役に割れる。
             ward の中の施設も拾う
  検算       種の語が本文にあれば載ったと数え、無ければ数えない
  集計       髪色・体格の率、同じ役の似方、seed の載った率が出る。
             読めない回・空の条件でも落ちない
"""

import io
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import npc_variety_probe as probe                       # noqa: E402

failures = []
passed = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + (("  -- " + str(detail)[:300]) if (detail and not cond) else ""))
    (passed if cond else failures).append(name)


def npc(look="", personality="", description="", category="young man"):
    return {"look_description": look, "personality": personality,
            "description": description, "category": category,
            "image_generation_prompt": []}


def main():
    prompts = probe.load_prompts()

    # -- 頼み文 --------------------------------------------------------------
    print("[頼み文]")
    for kind in ("single", "town"):
        check("{}: system が2通".format(kind), len(prompts[kind]["system"]) == 2)
        check("{}: 型定義に look_description か residents".format(kind),
              "look_description" in json.dumps(prompts[kind]["schema"]))
    rng = random.Random(7)
    scenario = probe.SINGLE_SCENARIOS[0]
    plain, seeds = probe.build_messages(prompts, "single", "plain", rng, scenario)
    check("plain: 3通（system, system, user）",
          [m["role"] for m in plain] == ["system", "system", "user"])
    check("plain: user はゲームの形のまま",
          plain[-1]["content"] == probe.single_user_text(scenario[1], scenario[2], scenario[3]))
    check("plain: 種は無い", seeds == {})
    fields, _ = probe.build_messages(prompts, "single", "fields", rng, scenario)
    check("fields: 要素名の指示が載る", "【外見・性格・経歴の書き方】" in fields[-1]["content"])
    check("fields: 値は載らない", "髪=" not in fields[-1]["content"])
    seeded, seeds = probe.build_messages(prompts, "single", "seed", rng, scenario)
    check("seed: 役の鍵で種が控えられる", list(seeds) == [scenario[0]])
    shown = seeds[scenario[0]]["look"][0][1][0]
    check("seed: 引いた語が本文に載る", shown in seeded[-1]["content"], seeded[-1]["content"][-300:])
    town, town_seeds = probe.build_messages(prompts, "town", "seed", rng)
    check("town seed: 全役ぶんの種", sorted(town_seeds) == sorted(k for k, _ in probe.TOWN_ROLES))
    check("town seed: 呼び方が全部載る",
          all(label in town[-1]["content"] for _k, label in probe.TOWN_ROLES))
    check("town plain: ゲームの user 本文で始まる",
          probe.build_messages(prompts, "town", "plain", rng)[0][-1]["content"]
          == prompts["town"]["user"])

    # -- 111_ の当て方 ----------------------------------------------------------
    print("\n[111_]")
    replace = probe.load_replace_rules()
    check("ルールが読める", replace is not None)
    if replace is not None:
        rewritten, hits = probe.apply_replacements(replace, plain)
        check("単体の system に没個性の行が入る（実機と同じ）",
              "没個性" in rewritten[1]["content"], hits)
        check("user は変わらない", rewritten[-1]["content"] == plain[-1]["content"])
    check("ルール無しは素通し", probe.apply_replacements(None, plain) == (plain, 0))

    # -- 読み取り ------------------------------------------------------------
    print("\n[読み取り]")
    data = {
        "settlement_structure": [
            {"type": "inn", "owner": npc("宿")},
            {"type": "ward", "locations": [
                {"type": "guild", "owner": npc("組合")},
                {"type": "blacksmith", "owner": npc("鍛冶")},
                {"type": "location", "name": "井戸"},
            ]},
            {"type": "inn", "owner": npc("二軒目")},
        ],
        "residents": [npc("住1"), {"name": "壊れた"}, npc("住3")],
        "adventurers": [npc("冒1")],
    }
    keys = [k for k, _ in probe.extract_npcs("town", data)]
    check("必須施設の主は種別が鍵", keys[:2] == ["inn", "guild"], keys)
    check("ward の中も拾う", "guild" in keys and "owner:blacksmith" in keys, keys)
    check("同じ種別の2軒目は owner: へ", "owner:inn" in keys, keys)
    check("住民は並び順（壊れた項目は飛ばす）",
          [k for k in keys if k.startswith("resident")] == ["resident1", "resident3"], keys)
    check("冒険者", keys[-1] == "adventurer1", keys)
    check("単体は場面の鍵", probe.extract_npcs("single", npc("x"), "clerk") == [("clerk", npc("x"))])
    check("読めない返答は空", probe.extract_npcs("town", None) == [] and
          probe.extract_npcs("single", {"name": "no look"}, "clerk") == [])

    # -- 検算 ------------------------------------------------------------------
    print("\n[検算]")
    seed = probe.draw_seed(random.Random(3))
    hair_key = dict(seed["look"])["髪"][1]
    talk_key = dict(seed["personality"])["口数"][1]
    got = probe.adherence(seed, npc(look="{}の髪。".format(hair_key),
                                    personality="とにかく{}。".format(talk_key)))
    check("載った語は True", got["look:髪"] and got["personality:口数"], got)
    check("無い語は False", not got["look:瞳"] and not got["description:出自"], got)
    check("髪色の正規表現: 「赤茶の短い髪」", probe.HAIR_RX.search("赤茶の短い髪を後ろで束ね"))
    check("髪色の正規表現: 「髪は白髪交じり」", probe.HAIR_RX.search("髪は白髪交じりで"))
    check("髪色の正規表現: 色の無い髪は拾わない", not probe.HAIR_RX.search("乱れた髪に汗"))
    check("体格: 小柄", probe.BUILD_RX.search("小柄だが手足には筋肉"))
    check("書き出し", probe.opening("厳格、潔癖、不屈。") == "厳格")
    check("似方: 同じ文は 1", probe.mean_pairwise_jaccard(["同じ文です", "同じ文です"]) == 1.0)
    check("似方: 1本では None", probe.mean_pairwise_jaccard(["一本"]) is None)

    # -- 集計 ------------------------------------------------------------------
    print("\n[集計]")
    rows = []
    for index in range(3):
        rows.append({"condition": "plain", "kind": "single", "scenario": "innkeeper",
                     "run": index + 1, "parsed": True, "seeds": {},
                     "npcs": [("innkeeper", npc("深い皺と灰色のローブ。", "穏やかだが頑固。", "宿の主人。",
                                                 "old man"))]})
    rows.append({"condition": "plain", "kind": "single", "scenario": "innkeeper",
                 "run": 4, "parsed": False, "seeds": {}, "npcs": []})
    rows.append({"condition": "seed", "kind": "single", "scenario": "innkeeper", "run": 1,
                 "parsed": True, "seeds": {"innkeeper": seed},
                 "npcs": [("innkeeper", npc("{}の髪、長身で痩せている。".format(hair_key),
                                             "無口。", "漁村の出。", "old man"))]})
    summary = "\n".join(probe.summarize(rows))
    check("plain の見出しに読めない回数", "plain : 4回 / 読めない 1 / 人物 3人" in summary, summary)
    check("髪色 0% と体格の率", "髪色 0%" in summary and "体格 0%" in summary, summary)
    check("役職の型 100%", "100% / 「鋭い・眼光」 0%" in summary, summary)
    check("同じ役の似方 1.00", "look_description 1.00" in summary, summary)
    check("seed の載った率が出る", "look:髪" in summary and "100% (1/1)" in summary, summary)
    check("空でも落ちない", probe.summarize([]) == [])

    print("\n{} check(s), {} failure(s)".format(len(passed) + len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
