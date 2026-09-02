# -*- coding: utf-8 -*-
"""NPC 生成の外見・性格・経歴が、実際のローカル LLM でどれだけ同じ形に寄るかを測る。

    python tools\\npc_variety_probe.py                       # llama-server を起こして測る
    python tools\\npc_variety_probe.py --runs 20 --town-runs 8
    python tools\\npc_variety_probe.py --conditions plain,seed
    python tools\\npc_variety_probe.py --base-url http://127.0.0.1:1234/v1 --api-model <名前>

ゲーム抜きで llama-server を直接起こす形は `epithet_probe.py` と同じ
（ゲームは終了しておくこと。VRAM とポートを取り合う）。
base_url は `/v1` まで含めること（欠けると HTTP 200 で無言に失敗する）。

測るものは3つ。

  頼み文     ゲームが送るものの写し（`npc_variety_prompts.json`）。
             単体生成（`master_ai_npc_generater`）と町生成（`create_settlement_detail`）の
             system 2通と型定義をそのまま使い、111_ の置換ルールも実機と同じに当てる
             （既定ルールに「没個性を避けること」の1行が入っている。素の条件はそれ込み）。
  条件       plain  = 実機のまま
             fields = 「髪色・瞳・肌・体格・特徴を必ず入れる」と**要素名だけ**足す
             seed   = 人物ごとに髪色・瞳・体格・特徴・性格の軸・経歴の種を表から引いて渡す
             mod    = 132_npc_variety の inject をそのまま通す（MOD の頼み文と同梱の表）
             seed は「具体の指定なら型から外れるか」、fields は「値を渡さずに済むか」、
             mod は「MOD の形（文で渡す・軸を減らす）でも seed と同じ率が出るか」を見る。
  偏り       同じ役の人物を回数ぶん並べ、髪色・瞳・肌・体格が本文に入った率、
             役職の型の語（ローブ・エプロン・眼鏡・鎧）と「鋭い」の率、
             同じ役どうしの本文の似方（文字2-gram の Jaccard）、
             personality の書き出しの異なり、seed の指定が本文に載った率を数える。

場面は単体4役（宿屋の主人・鍛冶師・書記官・冒険者。実機で型に流れていた役）と町1つ。
町は1回で10人前後が出るので回数は別に取る（`--town-runs`）。

サンプリングはゲームの config.json の `llama-cpp-completion-cuda` から読む
（既定 --temp 1.0 --top-p 0.95 --top-k 64）。seed は渡さない＝毎回変わる。
構造は実機と同じく型定義（json_schema）で強制する。

結果は `out\\npc_variety_probe_<時刻>.json`（生の返答ぜんぶ）と
`.jsonl`（1回ごとの追記。途中で切れても残る）と標準出力の集計。
集計の読み方と判断は VERIFICATION_LOG.md 側に書く。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import importlib.util
import io
import itertools
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.join(ROOT, "out")
PROMPTS_PATH = os.path.join(HERE, "npc_variety_prompts.json")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from instantale_modloader.llm import parse_json                      # noqa: E402
from epithet_probe import read_sampling                              # noqa: E402
from llm_ctx_probe import (find_game_dir, find_model, http_json,     # noqa: E402
                           pick_build_dir, read_live_config, running,
                           wait_ready)

DEFAULT_RUNS = 10          # 単体の役ごと
DEFAULT_TOWN_RUNS = 5      # 町
CONDITIONS = ("plain", "fields", "seed", "mod")

#: 1返答の上限。単体はスキル2つ込みで 1000 前後、町は 12人＋施設で 5000 前後。
MAX_TOKENS = {"single": 2000, "town": 8000}

#: 起動時の窓。町の頼み文は 4000 トークン程度（型定義の埋め込みが大半）。
CTX_SIZE = 16384

#: 単体4役。実機で「役職の型」に流れていた役（VERIFICATION_LOG.md 参照）。
#: ゲームの user 本文の形（【生成するNPC】名前・概要・強さのランク）で組む。
SINGLE_SCENARIOS = [
    ("innkeeper", "宿屋の主人", "宿場の古い宿を一人で切り盛りしている。", 8),
    ("blacksmith", "鍛冶師", "村で唯一の鍛冶場を構え、農具と武具の修理で食べている。", 12),
    ("clerk", "役所の書記官", "村の役所で帳簿と通行証を扱う。", 6),
    ("adventurer", "冒険者", "ギルドに登録して日銭を稼ぐ、駆け出しの冒険者。", 20),
]

# ------------------------------------------------------------------ 種の表
#: `(見せる語, 本文で探す語)`。探す語は見せる語の中核（言い換えられても残る部分）。
HAIR_COLORS = [("黒い髪", "黒"), ("濃い茶色の髪", "茶"), ("赤茶の髪", "赤茶"),
               ("亜麻色の髪", "亜麻"), ("金色の髪", "金"), ("銀灰色の髪", "銀"),
               ("白髪交じりの髪", "白髪"), ("煤けた灰色の髪", "灰")]
HAIR_STYLES = [("短く刈った", "刈"), ("肩までの長さ", "肩"), ("長く束ねた", "束ね"),
               ("癖の強い", "癖"), ("剃り上げた", "剃"), ("編んで垂らした", "編")]
EYE_COLORS = [("黒い瞳", "黒"), ("鳶色の瞳", "鳶色"), ("灰色の瞳", "灰"), ("緑の瞳", "緑"),
              ("青い瞳", "青"), ("琥珀色の瞳", "琥珀")]
SKINS = [("浅黒い肌", "浅黒"), ("色白の肌", "色白"), ("日に焼けた肌", "焼け"),
         ("そばかすの浮いた肌", "そばかす")]
BUILDS = [("小柄で痩せている", "小柄"), ("小柄で丸い", "小柄"), ("中背で骨太", "骨太"),
          ("長身で痩せている", "長身"), ("長身で肩幅が広い", "肩幅"), ("ずんぐりしている", "ずんぐり")]
FEATURES = [("左耳が欠けている", "左耳"), ("鼻筋に古い傷がある", "鼻"), ("前歯が一本無い", "前歯"),
            ("右手の指が二本短い", "指"), ("頬に入れ墨がある", "入れ墨"), ("片方の眉だけ白い", "眉"),
            ("左足を引きずる", "引きず"), ("声がひどく嗄れている", "嗄れ")]
CLOTH_COLORS = [("藍色", "藍"), ("えんじ色", "えんじ"), ("鈍色", "鈍色"), ("苔色", "苔"),
                ("生成り", "生成り"), ("黒", "黒")]

TALK = [("無口", "無口"), ("よく喋る", "喋"), ("聞き役に回る", "聞き")]
DISTANCE = [("馴れ馴れしい", "馴れ"), ("よそよそしい", "よそよそ"), ("慇懃", "慇懃"),
            ("誰にでも敬語", "敬語")]
VALUES = [("金より義理", "義理"), ("損得で動く", "損得"), ("信仰が第一", "信仰"),
          ("家族が第一", "家族"), ("名誉にこだわる", "名誉"), ("安全が第一", "安全")]
WEAKNESSES = [("酒に弱い", "酒"), ("高所が苦手", "高所"), ("嘘が下手", "嘘"), ("短気", "短気"),
              ("心配性", "心配"), ("見栄っ張り", "見栄")]
HABITS = [("話すとき指を鳴らす", "指を鳴ら"), ("髭や顎を撫でる", "撫で"), ("独り言が多い", "独り言"),
          ("貧乏揺すり", "貧乏揺すり"), ("相手の目を見ない", "目を見"), ("口笛を吹く", "口笛")]

ORIGINS = [("川向こうの漁村", "漁村"), ("山向こうの鉱山町", "鉱山"), ("この土地の生まれ", "生まれ"),
           ("流れ者", "流れ"), ("没落した家の出", "没落")]
EVENTS = [("火事で店を失った", "火事"), ("戦で片足を痛めた", "片足"), ("借金取りから逃げてきた", "借金"),
          ("兄弟を病で亡くした", "兄弟"), ("盗みで故郷を追われた", "盗"), ("洪水で家族と離れた", "洪水")]
REASONS = [("ここでしか仕事が無い", "仕事"), ("人を探している", "探し"), ("身を隠している", "隠"),
           ("借りを返すため", "借り"), ("親の跡を継いだ", "跡")]
SECRETS = [("元は密偵", "密偵"), ("偽名を使っている", "偽名"), ("前科がある", "前科"),
           ("教会を密かに憎んでいる", "教会"), ("未払いの借金がある", "借金")]


def draw_seed(rng):
    """人物1人ぶんの種。`{"look": [...], "personality": [...], "description": [...]}`。

    各項目は `(見せる語, 探す語)`。表示は `seed_text`、検算は `adherence`。
    """
    return {
        "look": [("髪", rng.choice(HAIR_COLORS)), ("髪型", rng.choice(HAIR_STYLES)),
                 ("瞳", rng.choice(EYE_COLORS)), ("肌", rng.choice(SKINS)),
                 ("体格", rng.choice(BUILDS)), ("目立つ特徴", rng.choice(FEATURES)),
                 ("服の色", rng.choice(CLOTH_COLORS))],
        "personality": [("口数", rng.choice(TALK)), ("他人との距離", rng.choice(DISTANCE)),
                        ("いちばん大事にするもの", rng.choice(VALUES)),
                        ("弱み", rng.choice(WEAKNESSES)), ("癖", rng.choice(HABITS))],
        "description": [("出自", rng.choice(ORIGINS)), ("過去の一件", rng.choice(EVENTS)),
                        ("ここにいる理由", rng.choice(REASONS)), ("隠し事", rng.choice(SECRETS))],
    }


def seed_text(seed, indent="- "):
    """種を頼み文に書く形。3行（外見・性格の軸・経歴の種）。"""
    def line(label, items):
        return "{}{}: {}".format(
            indent, label, "、".join("{}={}".format(k, shown) for k, (shown, _key) in items))
    return "\n".join([line("外見", seed["look"]),
                      line("性格の軸", seed["personality"]),
                      line("経歴の種", seed["description"])])


FIELDS_TEXT = (
    "【外見・性格・経歴の書き方】\n"
    "- look_description: 髪の色と形、瞳の色、肌、体格、その人だけの目立つ特徴（傷・欠け・入れ墨など）、"
    "服の色を必ずそれぞれ入れる。職業から連想される持ち物だけで終わらせない。\n"
    "- personality: 口数、他人との距離の取り方、いちばん大事にしているもの、弱み、癖を入れる。\n"
    "- description: 出自、過去の一件、いまここにいる理由を入れる。\n"
)

SEED_HEAD = (
    "【この人物の指定】\n"
    "次の指定を description・personality・look_description の本文に必ず織り込むこと"
    "（語はそのまま使ってよい）。\n"
)

#: 町の人物の割り当て。`(役の鍵, 頼み文での呼び方)`。
#: 必須施設の主は種別で名指しできる。住民・冒険者は並び順。
TOWN_ROLES = [
    ("inn", "宿屋(inn)の owner"),
    ("guild", "ギルド(guild)の owner"),
    ("administrative_office", "役所(administrative_office)の owner"),
    ("medical_facility", "医療施設(medical_facility)の owner"),
    ("general_store", "雑貨屋(general_store)の owner"),
    ("resident1", "residents の1人目"), ("resident2", "residents の2人目"),
    ("resident3", "residents の3人目"), ("resident4", "residents の4人目"),
    ("adventurer1", "adventurers の1人目"), ("adventurer2", "adventurers の2人目"),
    ("adventurer3", "adventurers の3人目"),
]
TOWN_SEED_HEAD = (
    "【人物の指定】\n"
    "次の人物は、それぞれの指定を description・personality・look_description の本文に"
    "必ず織り込むこと（語はそのまま使ってよい）。指定に無い人物は自由でよい。\n"
)


# ------------------------------------------------------------------ 頼み文
def load_prompts():
    with io.open(PROMPTS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def single_user_text(name, summary, rank):
    return "【生成するNPC】\n- 名前: {}\n- 概要: {}\n- 強さのランク: {}\n".format(name, summary, rank)


def load_variety_mod():
    """132_npc_variety を検査と同じ形で読む。`(module, 同梱の表)`。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith("_npc_variety")
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("*_npc_variety が1つに決まらない: {}".format(matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(
        "npc_variety_mod", os.path.join(folder, entry), submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules["npc_variety_mod"] = module
    spec.loader.exec_module(module)
    return module, module.read_seeds(module.seeds_path(folder))


_VARIETY = {}


def build_messages(prompts, kind, condition, rng, scenario=None):
    """`(messages, seeds)`。seeds は役の鍵 → 種（seed 条件以外は空）。

    mod 条件は MOD の `inject` に本文の並びを通す（実機と同じ入口。種の検算は無い）。
    """
    base = prompts[kind]
    if kind == "single":
        _key, name, summary, rank = scenario
        user = single_user_text(name, summary, rank)
    else:
        user = base["user"]
    seeds = {}
    if condition == "fields":
        user = user.rstrip("\n") + "\n" + FIELDS_TEXT
    elif condition == "seed":
        if kind == "single":
            seed = draw_seed(rng)
            seeds[scenario[0]] = seed
            user = user.rstrip("\n") + "\n" + SEED_HEAD + seed_text(seed) + "\n"
        else:
            lines = [TOWN_SEED_HEAD]
            for key, label in TOWN_ROLES:
                seed = draw_seed(rng)
                seeds[key] = seed
                lines.append("■ {}\n{}".format(label, seed_text(seed, indent="  - ")))
            user = user.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    messages = [{"role": "system", "content": base["system"][0]},
                {"role": "system", "content": base["system"][1]},
                {"role": "user", "content": user}]
    if condition == "mod":
        if not _VARIETY:
            _VARIETY["module"], _VARIETY["tables"] = load_variety_mod()
        texts = [m["content"] for m in messages]
        new_texts, _made = _VARIETY["module"].inject(texts, _VARIETY["tables"], rng)
        if new_texts is None:
            raise SystemExit("mod 条件: {} の本文に種が付かなかった（印が合っていない）".format(kind))
        messages = [dict(m, content=text) for m, text in zip(messages, new_texts)]
    return messages, seeds


# ------------------------------------------------------------------ 111_ の置換
def load_replace_rules():
    """111_ の置換ルールを実機と同じ場所から読む。`(module, groups)`。無ければ None。

    実機はゲームの本文が `chat` を通るときに当てている。素の条件を「実機のまま」に
    するため、同じルール（手元の `llm_replacements.txt` があればそれ）を当てる。
    """
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith("_llm_prompt_replace")
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        return None
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(
        "prompt_replace_mod", os.path.join(folder, entry),
        submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules["prompt_replace_mod"] = module
    spec.loader.exec_module(module)
    path = module.rules_path(folder)
    if not path or not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        rules, _warnings = module.parse_rules(fh.read().splitlines())
    return module, module.group_rules(rules), os.path.basename(path)


def apply_replacements(replace, messages):
    """111_ と同じ抽選で本文を書き換える。ヒットしたルール数も返す。"""
    if replace is None:
        return messages, 0
    module, groups, _name = replace
    out = []
    hits = 0
    for message in messages:
        text = message["content"]
        chosen, _skipped = module.decide(groups, text)
        new_text, applied = module.apply_chosen(text, chosen)
        hits += len(applied)
        out.append({"role": message["role"], "content": new_text})
    return out, hits


# ------------------------------------------------------------------ 送信と読み取り
def chat(base_url, api_model, messages, schema, sampling, max_tokens, timeout):
    """OpenAI 互換の chat 1回（型定義で構造を強制）。返答の本文か None。"""
    body = {"model": api_model, "messages": messages,
            "max_tokens": max_tokens, "cache_prompt": True,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema.get("title", "Structure"),
                                                "schema": schema}}}
    body.update(sampling)
    try:
        got = http_json(base_url.rstrip("/") + "/chat/completions", body, timeout=timeout)
        return got["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        print("      失敗: {}".format(exc), flush=True)
        return None


def walk_facilities(node):
    """町の構造から `(type, owner)` を全部拾う（ward の中も）。"""
    if isinstance(node, dict):
        owner = node.get("owner")
        if isinstance(owner, dict) and "look_description" in owner:
            yield node.get("type"), owner
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from walk_facilities(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_facilities(item)


def extract_npcs(kind, data, scenario_key=None):
    """返答から `[(役の鍵, 人物の辞書)]`。町は必須施設の主・住民・冒険者を役に割る。

    役の鍵は TOWN_ROLES と同じ（種の照合に使う）。それ以外の施設の主は `owner:<type>`。
    """
    if not isinstance(data, dict):
        return []
    if kind == "single":
        return [(scenario_key, data)] if "look_description" in data else []
    rows = []
    seen_types = set()
    for ftype, owner in walk_facilities(data.get("settlement_structure")):
        key = ftype if ftype in dict(TOWN_ROLES) and ftype not in seen_types else "owner:{}".format(ftype)
        seen_types.add(ftype)
        rows.append((key, owner))
    for group, prefix in (("residents", "resident"), ("adventurers", "adventurer")):
        for index, npc in enumerate(data.get(group) or [], start=1):
            if isinstance(npc, dict) and "look_description" in npc:
                rows.append(("{}{}".format(prefix, index), npc))
    return rows


# ------------------------------------------------------------------ 数え方
HAIR_RX = re.compile(r"(黒|茶|金|銀|白|赤|灰|栗|亜麻|藍|青|緑|紫|紅|褐|鳶|蜂蜜|プラチナ|ブロンド|焦げ)"
                     r"[^。、]{0,6}(髪|毛)|(髪|毛)[^。、]{0,6}(黒|茶|金|銀|白|赤|灰|栗|亜麻|青|緑|紫)")
EYE_RX = re.compile(r"(黒|茶|金|銀|灰|青|緑|紫|赤|琥珀|鳶|碧|翠|蒼|榛|藍)[^。、]{0,4}(瞳|眼|目)")
SKIN_RX = re.compile(r"(浅黒|色白|日に焼け|褐色|白い肌|青白い肌|そばかす|肌は|肌の色)")
BUILD_RX = re.compile(r"(小柄|大柄|長身|背が高|背が低|痩せ|やせ|太っ|肥え|ずんぐり|筋肉|骨太|がっしり|華奢|中背|恰幅|巨躯|小太り)")
ROLE_RX = re.compile(r"(ローブ|エプロン|眼鏡|鎧|作業着|制服|法衣|司祭服|白衣)")
SHARP_RX = re.compile(r"鋭い|眼光")
STOCK_RX = re.compile(r"(冷静|冷徹|厳格|寡黙|現実的|合理|実利|忠実|真面目|几帳面|温厚|穏やか|無愛想)")
PUNCT_RX = re.compile(r"[\s。、「」『』（）()・,.:;!?！？…\-]")


def bigrams(text):
    body = PUNCT_RX.sub("", text or "")
    return {body[i:i + 2] for i in range(len(body) - 1)}


def mean_pairwise_jaccard(texts):
    """同じ役の本文どうしの似方。2本未満なら None。"""
    sets = [bigrams(t) for t in texts if t]
    if len(sets) < 2:
        return None
    scores = []
    for a, b in itertools.combinations(sets, 2):
        union = a | b
        scores.append(len(a & b) / len(union) if union else 0.0)
    return sum(scores) / len(scores)


def opening(text):
    """personality の書き出し（最初の読点か句点まで）。"""
    return re.split(r"[、。]", (text or "").strip(), maxsplit=1)[0]


def adherence(seed, npc):
    """種の各項目が対応する本文に載ったか。`{項目名: bool}`。"""
    got = {}
    for field in ("look", "personality", "description"):
        text = npc.get("look_description" if field == "look" else field) or ""
        for label, (_shown, key) in seed[field]:
            got["{}:{}".format(field, label)] = key in text
    return got


def summarize(rows):
    """条件ごとの集計。印字用の行の並び。"""
    lines = []
    by_cond = collections.defaultdict(list)
    for row in rows:
        by_cond[row["condition"]].append(row)

    for condition in CONDITIONS:
        runs = by_cond.get(condition)
        if not runs:
            continue
        npcs = [(row, key, npc) for row in runs for key, npc in row["npcs"]]
        total = len(npcs)
        failed = sum(1 for row in runs if not row["parsed"])
        lines.append("==== {} : {}回 / 読めない {} / 人物 {}人 ====".format(
            condition, len(runs), failed, total))
        if not total:
            continue

        def rate(rx, field):
            return sum(1 for _r, _k, n in npcs if rx.search(n.get(field) or "")) / total

        lines.append("look_description に入った率: 髪色 {:.0%} / 瞳の色 {:.0%} / 肌 {:.0%} / 体格 {:.0%}".format(
            rate(HAIR_RX, "look_description"), rate(EYE_RX, "look_description"),
            rate(SKIN_RX, "look_description"), rate(BUILD_RX, "look_description")))
        lines.append("役職の型の語（ローブ・エプロン・眼鏡・鎧…） {:.0%} / 「鋭い・眼光」 {:.0%}".format(
            rate(ROLE_RX, "look_description"), rate(SHARP_RX, "look_description")))
        lines.append("personality の定型語（冷静・厳格・真面目…） {:.0%}".format(
            rate(STOCK_RX, "personality")))
        openings = collections.Counter(opening(n.get("personality")) for _r, _k, n in npcs)
        lines.append("personality の書き出し: 異なり {} / {}".format(len(openings), total))
        for text, count in openings.most_common(5):
            if count > 1:
                lines.append("    {:2d}x {}".format(count, text))
        categories = collections.Counter(n.get("category") for _r, _k, n in npcs)
        lines.append("category: " + " / ".join(
            "{} {}".format(name, count) for name, count in categories.most_common()))

        # 同じ役の本文の似方（役ごとに出して平均）。
        by_role = collections.defaultdict(list)
        for _row, key, npc in npcs:
            by_role[key].append(npc)
        sims = {}
        for field in ("look_description", "personality", "description"):
            values = [mean_pairwise_jaccard([n.get(field) for n in group])
                      for group in by_role.values() if len(group) >= 2]
            values = [v for v in values if v is not None]
            sims[field] = sum(values) / len(values) if values else None
        lines.append("同じ役どうしの似方（2-gram Jaccard の平均。高いほど同じ文）: " + " / ".join(
            "{} {}".format(field, "{:.2f}".format(v) if v is not None else "-")
            for field, v in sims.items()))
        worst = sorted(((mean_pairwise_jaccard([n.get("look_description") for n in group]) or 0, key)
                        for key, group in by_role.items() if len(group) >= 2), reverse=True)[:3]
        lines.append("    look が最も寄った役: " + "、".join(
            "{} {:.2f}".format(key, v) for v, key in worst))

        if condition == "seed":
            hit = collections.Counter()
            asked = collections.Counter()
            for row, key, npc in npcs:
                seed = row["seeds"].get(key)
                if not seed:
                    continue
                for label, ok in adherence(seed, npc).items():
                    asked[label] += 1
                    hit[label] += 1 if ok else 0
            lines.append("種が本文に載った率:")
            for label in sorted(asked):
                lines.append("    {:<28} {:3.0%} ({}/{})".format(
                    label, hit[label] / asked[label], hit[label], asked[label]))
        lines.append("")
    return lines


# ------------------------------------------------------------------ 採取
def collect(prompts, replace, base_url, api_model, sampling, conditions, runs,
            town_runs, timeout, jsonl_path, rng):
    rows = []
    jsonl = io.open(jsonl_path, "a", encoding="utf-8")

    def one(kind, condition, scenario, index, count):
        messages, seeds = build_messages(prompts, kind, condition, rng, scenario)
        messages, hits = apply_replacements(replace, messages)
        schema = prompts[kind]["schema"]
        started = time.monotonic()
        raw = chat(base_url, api_model, messages, schema, sampling, MAX_TOKENS[kind], timeout)
        data = parse_json(raw) if raw else None
        npcs = extract_npcs(kind, data, scenario[0] if scenario else None)
        row = {
            "kind": kind, "condition": condition,
            "scenario": scenario[0] if scenario else "town",
            "run": index + 1, "seconds": round(time.monotonic() - started, 1),
            "replace_hits": hits, "user_prompt": messages[-1]["content"],
            "seeds": seeds, "raw": raw, "parsed": data is not None,
            "npcs": [(key, npc) for key, npc in npcs],
        }
        rows.append(row)
        jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
        jsonl.flush()
        mark = "（読めない）" if data is None else "{}人 {}".format(
            len(npcs), " / ".join((n.get("look_description") or "")[:24] for _k, n in npcs[:2]))
        print("      {:2d}/{} {:5.1f}s {}".format(index + 1, count, row["seconds"], mark), flush=True)

    for condition in conditions:
        print("  [{}]".format(condition), flush=True)
        for scenario in SINGLE_SCENARIOS:
            print("    単体 {} …".format(scenario[1]), flush=True)
            for index in range(runs):
                one("single", condition, scenario, index, runs)
        if town_runs > 0:
            print("    町 …", flush=True)
            for index in range(town_runs):
                one("town", condition, None, index, town_runs)
    jsonl.close()
    return rows


def main():
    ap = argparse.ArgumentParser(description="NPC 生成の外見・性格・経歴の偏りを測る。")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                    help="単体の役ごとの回数（既定 %d）" % DEFAULT_RUNS)
    ap.add_argument("--town-runs", type=int, default=DEFAULT_TOWN_RUNS,
                    help="町の回数（既定 %d。0 で町を飛ばす）" % DEFAULT_TOWN_RUNS)
    ap.add_argument("--conditions", default=",".join(CONDITIONS),
                    help="測る条件（既定 %s）" % ",".join(CONDITIONS))
    ap.add_argument("--no-replace", action="store_true",
                    help="111_ の置換ルールを当てない（実機と違う形になる。比較用）")
    ap.add_argument("--seed", type=int, default=None, help="種の抽選を固定する乱数の種")
    ap.add_argument("--base-url", help="既に立っているサーバを使う（/v1 まで）")
    ap.add_argument("--api-model", default="probe",
                    help="--base-url 側に渡すモデル名（LM Studio では必須）")
    ap.add_argument("--model-pattern", default=None,
                    help="起こす GGUF の絞り込み（既定はゲームの config.json が今指している"
                         "モデル名。複数残れば一覧を出す）")
    ap.add_argument("--model", help="GGUF をフルパスで名指しする")
    ap.add_argument("--game-dir")
    ap.add_argument("--port", type=int, default=51988)
    ap.add_argument("--timeout", type=int, default=600,
                    help="1回の返答を待つ秒数（既定 600。町は長い）")
    ap.add_argument("--tag", default="", help="出力ファイル名に足す印")
    args = ap.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    unknown = [c for c in conditions if c not in CONDITIONS]
    if unknown:
        print("  知らない条件: {}（{} のどれか）".format(unknown, "/".join(CONDITIONS)))
        return 2

    prompts = load_prompts()
    replace = None if args.no_replace else load_replace_rules()
    game_dir = find_game_dir(args.game_dir)
    # config.json は `ai_setting` の下にモデルと起動引数を持つ。
    settings = read_live_config(game_dir).get("ai_setting") or {}
    sampling = read_sampling(settings)
    rng = random.Random(args.seed)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, "npc_variety_probe_{}{}.json".format(
        stamp, ("_" + args.tag) if args.tag else ""))

    proc = None
    handle = None
    try:
        if args.base_url:
            base_url = args.base_url
            api_model = args.api_model
            model_label = "{} @ {}".format(api_model, base_url)
        else:
            busy = running("instantale.exe", "llama-server.exe")
            if busy:
                print("  ゲームが動いている: {}".format(", ".join(busy)))
                print("  VRAM とポートを取り合うので、終了してから実行すること。")
                return 2
            if args.model:
                model = find_model(game_dir, None, args.model)
            else:
                # 既定はゲームが今使っているモデル（実機と同じ相手で測る）。
                pattern = args.model_pattern or ((settings.get("local_model_setting") or {})
                                                 .get("local_llm") or {}).get("name") or "gemma"
                models_dir = os.path.join(game_dir, "runtime", "models", "llama_cpp")
                found = sorted(name for name in os.listdir(models_dir)
                               if name.lower().endswith(".gguf")
                               and pattern.lower() in name.lower())
                if len(found) != 1:
                    print("  --model-pattern {!r} で1つに決まらない:".format(pattern))
                    for name in found:
                        print("    " + name)
                    print("  --model-pattern を狭めるか --model で名指しすること。")
                    return 2
                model = os.path.join(models_dir, found[0])
            backend = (settings.get("local_model_setting") or {}).get(
                "llm_backend", "llama-cpp-completion-cuda")
            server = pick_build_dir(game_dir, backend) / "llama-server.exe"
            print("  モデル: {}".format(os.path.basename(str(model))))
            print("  起動中（読み込みに数分かかることがある）…", flush=True)
            logfile = os.path.join(OUT_DIR, "npc_variety_probe_server.log")
            handle = io.open(logfile, "w", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [str(server), "-m", str(model),
                 "--host", "127.0.0.1", "--port", str(args.port),
                 "--ctx-size", str(CTX_SIZE), "--n-gpu-layers", "999",
                 "--cache-reuse", "256", "--parallel", "1", "--no-mmproj",
                 # 思考を吐くモデルは切らないと上限を思考で使い切る（VERIFICATION_LOG.md §2.63）。
                 "--reasoning-budget", "0"],
                stdout=handle, stderr=subprocess.STDOUT)
            if not wait_ready(args.port, 300):
                print("  起動しなかった。{} を読むこと。".format(logfile))
                return 1
            base_url = "http://127.0.0.1:{}/v1".format(args.port)
            api_model = "probe"
            model_label = os.path.basename(str(model))

        print("  サンプリング: {}".format(sampling))
        print("  置換ルール: {}".format(replace[2] if replace else "当てない"))
        print("  条件: {} / 単体 {}回×{}役 / 町 {}回".format(
            ",".join(conditions), args.runs, len(SINGLE_SCENARIOS), args.town_runs))
        print()
        rows = collect(prompts, replace, base_url, api_model, sampling, conditions,
                       args.runs, args.town_runs, args.timeout,
                       out_path.replace(".json", ".jsonl"), rng)
    finally:
        if proc is not None:
            proc.kill()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
        if handle is not None:
            handle.close()

    summary = summarize(rows)
    print()
    for line in summary:
        print("  " + line)

    with io.open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "model": model_label,
            "sampling": sampling,
            "conditions": conditions,
            "runs": args.runs, "town_runs": args.town_runs,
            "replace_rules": replace[2] if replace else None,
            "rows": rows,
            "summary": summary,
        }, fh, ensure_ascii=False, indent=2)
    print()
    print("  生の結果: {}".format(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
