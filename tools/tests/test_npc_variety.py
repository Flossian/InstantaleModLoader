# -*- coding: utf-8 -*-
"""132_npc_variety をゲーム抜きで通す。

    python tools/tests/test_npc_variety.py

確認するもの:

  見分け     単体（【生成するNPC】＋型に look_description）と町（【NPCの生成要素】＋settlement_name）だけ。
             衛兵・闘技場（型が look）と 320 の募集は触らない。種が付いた本文は2度目は触らない
  組み立て   外見→性格→来歴の順で、各節の中は順が混ざる。鍵=値の形も軸の名前も載らない。
             軸の本数は設定どおり。割合 0 で何も足さない
  町         必須施設の主5人＋住民＋冒険者を番号で名指しし、人数は設定どおり
  表         同梱の表が読める。手元の seeds.json が優先される。壊れた句はその1件だけ捨てる。
             知らない鍵は無視。表が無ければ何も足さない。ファイルが変わったら読み直す
  経路       偽の LlamaCppClient の chat に流すと user 本文だけが伸び、system は変わらず、
             送られた本文に種があり、[SEED] がログに残る。自己検証が通る
"""

import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("*{} が1つに決まらない: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD_PATH = find_mod("_npc_variety")


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_npc_variety", MOD_PATH, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_mod()

failures = []
passed = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + (("  -- " + str(detail)[:300]) if (detail and not cond) else ""))
    (passed if cond else failures).append(name)


# ---------------------------------------------------------------- 見本の本文
SINGLE = ["必ず日本語で、以下のjson形式で出力すること: {'properties': {'category': ..., "
          "'look_description': ...}}\n- look_description: 見た目",
          "あなたはダークファンタジーRPGのキャラクター生成AIだ。",
          "【生成するNPC】\n- 名前: 宿屋の主人\n- 概要: 宿場の古い宿を一人で切り盛りしている。\n- 強さのランク: 8\n"]
# 実機の衛兵の頼み文の形（2026-09-02）。system に `- look_description:` が**ある**。
GUARD = ["必ず日本語で、以下のjson形式で出力すること: {'properties': {'look': ...}}",
         "あなたはダークファンタジーRPGのキャラクター生成AIだ。\nプレイヤーが犯罪を行ったときに逮捕や戦闘のために出現する衛兵NPCをデザインしろ。\n"
         "【出力要素】\n- category: 老若男女のカテゴリ。- personality: 性格というか兵としての行動方針。\n- look_description: そのキャラクターの外見、特徴、服装などを自然言語で記述。",
         "【世界の情報】…\n【エリアの情報】\n- エリア名: 錆びた槌の村\n【生成するNPC】\n- 強さのランク: 20\n"]
COLOSSEUM = ["型", "あなたはダークファンタジーRPGのキャラクター生成AIだ。\n闘技場で戦う人またはモンスターを設定しろ。\n- look_description: 外見",
             "【闘技場の情報】\n【生成するNPC】\n- 強さのランク: 30\n"]
GUARD_NAMED = ["型 - look_description:", "衛兵NPCをデザインしろ", "【生成するNPC】\n- 名前: 門番\n- 概要: 村の門番\n- 強さのランク: 20\n"]
TOWN = ["必ず日本語で、以下のjson形式で出力すること: {'$defs': {'NPC': {'look_description': ...}}}",
        "あなたはダークファンタジーRPGのワールドマップジェネレーターです。",
        "エリアの詳細を生成してください。\n【NPCの生成要素】\n- 名前: NPCの名前を記入。\n"
        "【生成対象エリアのデータ】\n- settlement_name: 風の村\n- settlement_size: village\n"]
QUEST = ["型", "指示", "【生成対象エリアのデータ】\n- settlement_name: 風の村\n依頼を作れ"]
RECRUIT = ["あなたはRPGの世界に新しく登場する冒険者NPCを1人考える係です。\n- look_description: 日本語。見た目"]

TABLES = {
    "look": [("髪", ["赤茶の髪を短く刈っている"], 100), ("瞳", ["鳶色の瞳"], 100), ("肌", ["色白"], 100)],
    "personality": [("口数", ["無口"], 100), ("距離", ["慇懃"], 100), ("大事", ["金より義理"], 100)],
    "description": [("出自", ["川向こうの漁村の生まれ"], 100), ("一件", ["火事で店を失った"], 100)],
}
CHANCY = {
    "look": [("髪", ["赤茶の髪"], 100), ("特徴", ["片目に眼帯"], 25), ("無い", ["出ない"], 0)],
    "personality": [], "description": [],
}


# ---------------------------------------------------------------- 偽のゲーム側
class FakeClient(object):
    def __init__(self):
        self.sent = []

    def chat(self, model, messages, format=None):
        prompt = self._apply_chat_template(model, messages)
        return self._post_with_model_loading_retry("/completion", {"prompt": prompt})

    def _apply_chat_template(self, model, messages, timeout=None):
        self.sent.append([dict(m) for m in messages])
        return "\n".join(m.get("content") or "" for m in messages)

    def _post_with_model_loading_retry(self, url, payload, timeout=None):
        return {"content": "ok"}


_PRISTINE = {name: FakeClient.__dict__[name]
             for name in ("chat", "_apply_chat_template", "_post_with_model_loading_retry")}


class FakeCtx(object):
    def __init__(self, client, out_dir, mod_dir):
        self.client = client
        self.out_dir = out_dir
        self.mod_dir = mod_dir
        self.api = 1
        self.lines = []
        self.errors = []
        self._mod = None

    def log(self, msg, level="INFO"):
        self.lines.append("{} {}".format(level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, *, tag=None, stamp=True, label=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def wrap(self, target, required=True):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            return lambda fn: fn          # クラウド別名は無い（ローカル経路だけ見る）

        def decorate(fn):
            original = getattr(type(self.client), name)

            def wrapper(client_self, *args, **kwargs):
                return fn(original, client_self, *args, **kwargs)
            setattr(type(self.client), name, wrapper)
            return fn
        return decorate

    def resolve(self, target):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            return None, name, None
        return type(self.client), name, getattr(type(self.client), name, None)

    def superseded(self):
        return False


def main():
    # -- 見分け --------------------------------------------------------------
    print("[見分け]")
    check("単体", mod.classify(SINGLE) == ("single", 2), mod.classify(SINGLE))
    check("町", mod.classify(TOWN) == ("town", 2), mod.classify(TOWN))
    check("衛兵は触らない（look_description があっても名前と概要が無い）", mod.classify(GUARD) is None)
    check("闘技場の敵は触らない", mod.classify(COLOSSEUM) is None)
    check("衛兵の語があれば名前が有っても触らない", mod.classify(GUARD_NAMED) is None)
    check("名前だけで概要が無ければ触らない",
          mod.classify(["- look_description:", "指示", "【生成するNPC】\n- 名前: x\n- 強さのランク: 8\n"]) is None)
    check("依頼生成は触らない（settlement_name だけ）", mod.classify(QUEST) is None)
    check("320 の募集は触らない", mod.classify(RECRUIT) is None)
    seeded, made = mod.inject(SINGLE, TABLES, random.Random(1))
    check("種が付いた本文は2度目は触らない", mod.classify(seeded) is None)
    check("空の並び", mod.classify([]) is None)

    # -- 組み立て ------------------------------------------------------------
    print("\n[組み立て]")
    line = mod.compose_person(TABLES, random.Random(3), look_axes=3, personality_axes=3,
                              description_axes=2)
    parts = line.rstrip("。").split("。")
    check("外見→性格→来歴の順", parts[:3] and set(parts[:3]) == {"赤茶の髪を短く刈っている", "鳶色の瞳", "色白"}
          and set(parts[3:6]) == {"無口", "慇懃", "金より義理"}
          and set(parts[6:]) == {"川向こうの漁村の生まれ", "火事で店を失った"}, line)
    check("鍵=値も軸の名前も載らない", "=" not in line and "髪" not in parts[3:] and "口数" not in line, line)
    check("句点で終わる", line.endswith("。"))
    orders = {mod.compose_person(TABLES, random.Random(i), look_axes=3, personality_axes=0,
                                 description_axes=0) for i in range(20)}
    check("節の中の順は混ざる", len(orders) > 1, orders)
    short = mod.compose_person(TABLES, random.Random(1), look_axes=1, personality_axes=1,
                               description_axes=0)
    check("軸の本数は指定どおり", short.count("。") == 2, short)
    check("全部 0 なら空", mod.compose_person(TABLES, random.Random(1), look_axes=0,
                                          personality_axes=0, description_axes=0) == "")
    check("表が空なら空", mod.compose_person({"look": [], "personality": [], "description": []},
                                        random.Random(1)) == "")
    hairy = {"look": [("髪色", ["黒い髪"], 100), ("髪の長さ", ["髪は肩まで"], 100), ("瞳", ["緑の瞳"], 100),
                      ("髪型", ["髪を束ねている"], 100), ("肌", ["色白"], 100)], "personality": [], "description": []}
    hair_lines = [mod.compose_person(hairy, random.Random(i)) for i in range(40)]
    check("髪の軸は隣り合い、表の順（色→長さ→型）",
          all("黒い髪。髪は肩まで。髪を束ねている。" in l for l in hair_lines), hair_lines[:3])
    check("髪以外の順は混ざる", len(set(hair_lines)) > 1, hair_lines[:3])
    droppy = {"look": [("髪の長さ", ["髪は短く刈っている"], 100), ("髪型", ["髪を三つ編みにしている"], 100),
                       ("瞳", ["緑の瞳"], 100)], "personality": [], "description": [],
              "_drops": {"look": {("髪の長さ", "髪は短く刈っている"): ["髪型"]}, "personality": {}, "description": {}}}
    drop_lines = [mod.compose_person(droppy, random.Random(i)) for i in range(30)]
    check("drops: 短い髪では髪型が省かれ、他の軸は残る",
          all("三つ編み" not in l and "緑の瞳" in l and "短く刈って" in l for l in drop_lines), drop_lines[:3])
    crossy = {"look": [("年齢", ["まだ子ども"], 100)], "personality": [],
              "description": [("家族", ["嫁いだ娘がいる"], 100), ("出自", ["漁村の生まれ"], 100)],
              "_drops": {"look": {("年齢", "まだ子ども"): ["家族"]}, "personality": {}, "description": {}}}
    cross_lines = [mod.compose_person(crossy, random.Random(i), description_axes=2) for i in range(20)]
    check("drops: 節をまたぐ（子どもの年齢で来歴の家族が省かれる）",
          all("嫁いだ娘" not in l and "漁村" in l and "まだ子ども" in l for l in cross_lines), cross_lines[:2])
    roly = {"look": [("年齢", ["まだ子ども", "三十路"], 100), ("肌", ["色白"], 100)], "personality": [], "description": [],
            "_not_for": {"look": {"年齢": {"owner": {"まだ子ども"}, "single": {"まだ子ども", "三十路"}}},
                         "personality": {}, "description": {}}}
    owner_lines = [mod.compose_person(roly, random.Random(i), role="owner") for i in range(40)]
    free_lines = [mod.compose_person(roly, random.Random(i), role="resident") for i in range(40)]
    single_lines = [mod.compose_person(roly, random.Random(i), role="single") for i in range(10)]
    check("not_for: 役に出さない句を引かない", all("まだ子ども" not in l and "三十路" in l for l in owner_lines), owner_lines[:2])
    check("not_for: 他の役には出る", any("まだ子ども" in l for l in free_lines))
    check("not_for: 句が全部消えた軸は付けない（他の軸は残る）",
          all("年齢" not in l and "三十路" not in l and "まだ子ども" not in l and "色白" in l for l in single_lines), single_lines[:2])
    check("役の名: owner / resident / adventurer",
          [mod.role_class(k) for k in ("inn", "guild", "resident2", "adventurer1")] == ["owner", "owner", "resident", "adventurer"])
    lines = [mod.compose_person(CHANCY, random.Random(i), look_axes=9) for i in range(200)]
    with_mark = sum(1 for l in lines if "眼帯" in l)
    check("付く割合 25 の軸は 4人に1人ほど（200人で 30〜70）", 30 <= with_mark <= 70, with_mark)
    check("割合 0 の軸は付かない・割合 100 は必ず付く",
          not any("出ない" in l for l in lines) and all("赤茶の髪" in l for l in lines))
    check("外れた軸のぶん本数は減らない（上限は残った軸で数える）",
          all(l.count("。") in (1, 2) for l in lines) and
          all(mod.compose_person(CHANCY, random.Random(i), look_axes=1).count("。") == 1 for i in range(50)))

    got, made = mod.inject(SINGLE, TABLES, random.Random(1), chance=100)
    check("単体: user 本文だけ伸びる", got[0] == SINGLE[0] and got[1] == SINGLE[1]
          and got[2].startswith(SINGLE[2]) and mod.HEAD_SINGLE in got[2], got)
    check("単体: 元の本文に足す前は改行で切れている", "\n" + mod.HEAD_SINGLE + "\n" in got[2])
    check("単体: 締めの指示が付く", mod.FOOT_SINGLE in got[2])
    check("単体: 付けた種が1本返る", len(made) == 1 and made[0][1] in got[2], made)
    check("割合 0 は触らない", mod.inject(SINGLE, TABLES, random.Random(1), chance=0) == (None, []))
    check("元のリストは変えない", SINGLE[2].endswith("ランク: 8\n"))
    check("表が空なら触らない", mod.inject(SINGLE, {"look": [], "personality": [],
                                                    "description": []}, random.Random(1)) == (None, []))
    check("衛兵は触らない", mod.inject(GUARD, TABLES, random.Random(1)) == (None, []))

    # -- 町 --------------------------------------------------------------------
    print("\n[町]")
    got, made = mod.inject(TOWN, TABLES, random.Random(2), chance=100, residents=2, adventurers=1)
    labels = [label for label, _line in made]
    check("必須施設の主5人＋住民2＋冒険者1",
          labels == ["宿屋(inn)の owner", "ギルド(guild)の owner", "役所(administrative_office)の owner",
                     "医療施設(medical_facility)の owner", "雑貨屋(general_store)の owner",
                     "residents の1人目", "residents の2人目", "adventurers の1人目"], labels)
    check("町: 1人1行で名指し", all("- {}: {}".format(l, t) in got[2] for l, t in made))
    check("町: system は変わらない", got[0] == TOWN[0] and got[1] == TOWN[1])
    check("町: 締めの指示", mod.FOOT_TOWN in got[2])
    got, made = mod.inject(TOWN, TABLES, random.Random(2), chance=100, residents=0, adventurers=0)
    check("住民 0・冒険者 0 なら主5人だけ", len(made) == 5, made)
    got, made = mod.inject(TOWN, TABLES, random.Random(5), chance=50, residents=4, adventurers=3)
    check("割合 50 は人数が減る（0 でも 12 でもない）", got is not None and 0 < len(made) < 12, len(made))
    check("既定の人数は 5+4+3", len(mod.town_roles()) == 12)

    # -- 表 --------------------------------------------------------------------
    print("\n[表]")
    bundled = mod.read_seeds(os.path.join(MOD_DIR, mod.DEFAULT_SEEDS_FILE_NAME))
    axes, phrases = mod.table_size(bundled)
    check("同梱の表が読める（外見9軸・性格6軸・来歴9軸）",
          [len(bundled[s]) for s in mod.SECTIONS] == [9, 6, 9], [len(bundled[s]) for s in mod.SECTIONS])
    check("同梱の来歴は各軸 24句以上",
          all(len(rs) >= 24 for _a, rs, _c in bundled["description"]), [(a, len(rs)) for a, rs, _c in bundled["description"]])
    check("同梱の句に = や改行が無い", all(mod.usable(p) for s in mod.SECTIONS for _a, rows, _c in bundled[s] for p in rows))
    check("同梱の句は句点を含まない（繋ぐときに付ける）",
          not any("。" in p for s in mod.SECTIONS for _a, rows, _c in bundled[s] for p in rows))
    check("同梱: 短い髪では髪型を省く",
          bundled["_drops"]["look"].get(("髪の長さ", "髪は短く刈っている")) == ["髪型"], bundled["_drops"])
    bundled_lines = [mod.compose_person(bundled, random.Random(i)) for i in range(300)]
    owners = [mod.compose_person(bundled, random.Random(i), role="owner") for i in range(300)]
    singles = [mod.compose_person(bundled, random.Random(i), role="single") for i in range(300)]
    check("同梱: 施設の主に子どもが出ない（十代は出る）",
          not any("まだ子ども" in l for l in owners) and any("十代半ば" in l for l in owners))
    check("同梱: 単体生成には年齢の軸が付かない",
          not any(w in l for l in singles for w in ("まだ子ども", "十代半ば", "二十歳", "三十路", "四十路", "五十路", "老いて", "読めない顔")))
    check("同梱: 施設の主には年齢が付く", any("三十路" in l or "五十路" in l for l in owners))
    check("同梱: 住民には子どもが出る", any("まだ子ども" in l for l in bundled_lines))
    got, made = mod.inject(TOWN, bundled, random.Random(4), chance=100, residents=4, adventurers=3)
    check("町: owner の行に子どもが無い",
          not any("まだ子ども" in line for label, line in made if "owner" in label), made)
    import re as _re
    gendered=[p for s in mod.SECTIONS for _a, rs, _c in bundled[s] for p in rs
              if _re.search(r"妻|夫|娘|息子|嫁い|婿|男|女|少年|少女|母|父|髭", p) and "嫁いだ妹" not in p]
    check("同梱: 性別を決める語が無い（性別は名前と概要からモデルが決める）", not gendered, gendered)
    check("同梱: 子どもの年齢と前の生業が同じ人物に出ない",
          not any(("十代半ば" in l or "まだ子ども" in l) and "元は" in l for l in bundled_lines))
    check("同梱: 子どもの年齢と家族が同じ人物に出ない",
          not any(("十代半ば" in l or "まだ子ども" in l) and any(w in l for w in ("連れ合い", "嫁いだ妹", "親を養", "独り身", "犬を"))
                  for l in bundled_lines), [l for l in bundled_lines if "まだ子ども" in l][:2])
    check("同梱: 短髪と髪型（束ねる・編む・まとめる）が同じ人物に出ない",
          not any(("短く刈って" in l or "耳にかかる" in l) and any(w in l for w in ("束ね", "編", "まとめ", "三つ編み"))
                  for l in bundled_lines), [l for l in bundled_lines if "短く刈って" in l][:2])
    chances = {axis: chance for axis, _rows, chance in bundled["look"]}
    check("同梱の目立つ特徴だけ 25%、他は 100",
          chances.get("目立つ特徴") == 25 and all(c == 100 for a, c in chances.items() if a != "目立つ特徴"), chances)
    check("同梱の目立つ特徴は立ち絵で描ける大きさのものだけ（細部の語が無い）",
          not any(w in p for _a, rows, _c in bundled["look"] for p in rows
                  for w in ("前歯", "指が", "耳飾り", "唇", "嗄れ", "引きずる")))
    tmp = tempfile.mkdtemp(prefix="npc_variety_")
    try:
        with io.open(os.path.join(tmp, "seeds.default.json"), "w", encoding="utf-8") as fh:
            json.dump({"look": {"髪": ["黒い髪", "", 3, "黒い髪", "a=b", "x" * 61],
                                "特徴": {"chance": 30, "rows": ["眼帯"]},
                                "壊れた割合": {"chance": "多め", "rows": ["x"]},
                                "行が無い": {"chance": 50}},
                       "personality": {"口数": "壊れている"}, "unknown": {"x": ["y"]},
                       "description": {}}, fh, ensure_ascii=False)
        tables = mod.read_seeds(mod.seeds_path(tmp))
        check("壊れた句はその1件だけ捨て、重複は1度", tables["look"][0] == ("髪", ["黒い髪"], 100), tables)
        check("chance の形を読む。壊れた割合は 100、行の無い軸は捨てる",
              tables["look"][1:] == [("特徴", ["眼帯"], 30), ("壊れた割合", ["x"], 100)], tables)
        with io.open(os.path.join(tmp, "seeds.default.json"), "w", encoding="utf-8") as fh:
            json.dump({"look": {"長さ": {"rows": ["短い", "長い"], "drops": {"短い": ["型"], "無い句": ["型"], "長い": "壊れ"}},
                                "型": ["三つ編み"]}}, fh, ensure_ascii=False)
        tables = mod.read_seeds(mod.seeds_path(tmp))
        check("drops を読む（無い句・壊れた宛先は捨てる）",
              tables["_drops"]["look"] == {("長さ", "短い"): ["型"]}, tables["_drops"])
        with io.open(os.path.join(tmp, "seeds.default.json"), "w", encoding="utf-8") as fh:
            json.dump({"look": {"年齢": {"rows": ["子ども", "大人"], "not_for": {"owner": ["子ども", "無い句"], "single": "壊れ"}}}},
                      fh, ensure_ascii=False)
        tables = mod.read_seeds(mod.seeds_path(tmp))
        check("not_for を読む（無い句・壊れた役は捨てる）",
              tables["_not_for"]["look"] == {"年齢": {"owner": {"子ども"}}}, tables["_not_for"])
        with io.open(os.path.join(tmp, "seeds.default.json"), "w", encoding="utf-8") as fh:
            json.dump({"look": {"年齢": {"rows": ["子ども", "大人"], "not_for": {"single": True, "owner": "*"}},
                                "肌": ["色白"]}}, fh, ensure_ascii=False)
        tables = mod.read_seeds(mod.seeds_path(tmp))
        check("not_for: true / * は軸ごと", tables["_not_for"]["look"] == {"年齢": {"single": "*", "owner": "*"}}, tables["_not_for"])
        whole = [mod.compose_person(tables, random.Random(i), role="single") for i in range(20)]
        check("not_for: 軸ごと省かれ、他の軸は残る", all(l == "色白。" for l in whole), whole[:3])
        check("壊れた節は空、知らない鍵は無視", tables["personality"] == [] and tables["description"] == []
              and set(tables) == set(mod.SECTIONS) | {"_drops", "_not_for"}, tables)
        with io.open(os.path.join(tmp, "seeds.json"), "w", encoding="utf-8") as fh:
            json.dump({"look": {"髪": ["手元の髪"]}}, fh, ensure_ascii=False)
        check("手元の seeds.json が優先", mod.seeds_path(tmp).endswith("seeds.json")
              and mod.read_seeds(mod.seeds_path(tmp))["look"] == [("髪", ["手元の髪"], 100)])
        check("読めない JSON は空", mod.read_seeds(os.path.join(tmp, "none.json")) == {s: [] for s in mod.SECTIONS})
        check("mod_dir が無ければ None", mod.seeds_path(None) is None and mod.seeds_path(os.path.join(tmp, "x")) is None)
        reports = []
        seed_file = mod.SeedFile(tmp, reports.append)
        first = seed_file.current()
        check("SeedFile: 読んだ記録", reports and "[TABLE]" in reports[-1] and "seeds.json" in reports[-1], reports)
        time.sleep(0.05)
        with io.open(os.path.join(tmp, "seeds.json"), "w", encoding="utf-8") as fh:
            json.dump({"look": {"髪": ["書き直した髪"]}}, fh, ensure_ascii=False)
        os.utime(os.path.join(tmp, "seeds.json"), (time.time() + 5, time.time() + 5))
        second = seed_file.current()
        check("SeedFile: 変わったら読み直す", second["look"] == [("髪", ["書き直した髪"], 100)] and first is not second, second)
        os.remove(os.path.join(tmp, "seeds.json"))
        os.remove(os.path.join(tmp, "seeds.default.json"))
        empty = seed_file.current()
        check("SeedFile: 表が消えたら空になり記録が残る", mod.table_size(empty) == (0, 0)
              and "表が無い" in reports[-1], reports[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # -- 経路 --------------------------------------------------------------------
    print("\n[経路]")
    out_dir = tempfile.mkdtemp(prefix="npc_variety_out_")
    try:
        for name, fn in _PRISTINE.items():
            setattr(FakeClient, name, fn)
        client = FakeClient()
        ctx = FakeCtx(client, out_dir, MOD_DIR)
        mod.RNG = random.Random(7)
        mod.apply(ctx)
        check("自己検証が通る", any("self-check passed" in l for l in ctx.lines)
              and not any("VERIFY FAILED" in l for l in ctx.lines), ctx.lines)
        check("仕掛かった記録", any("armed on" in l and "句 from seeds.default.json" in l for l in ctx.lines), ctx.lines)
        messages = [{"role": "system", "content": SINGLE[0]}, {"role": "system", "content": SINGLE[1]},
                    {"role": "user", "content": SINGLE[2]}]
        client.chat("m", messages)
        sent = client.sent[-1]
        check("chat: user 本文に種が載る", mod.HEAD_SINGLE in sent[2]["content"], sent[2]["content"][-200:])
        check("chat: system は変わらない", sent[0]["content"] == SINGLE[0] and sent[1]["content"] == SINGLE[1])
        check("chat: 元の messages は変わらない", messages[2]["content"] == SINGLE[2])
        client.chat("m", [{"role": "user", "content": GUARD[2]}, {"role": "system", "content": GUARD[0]}])
        check("chat: 衛兵はそのまま", client.sent[-1][0]["content"] == GUARD[2])
        town_messages = [{"role": "system", "content": TOWN[0]}, {"role": "system", "content": TOWN[1]},
                         {"role": "user", "content": TOWN[2]}]
        client.chat("m", town_messages)
        check("chat: 町は12人ぶん", client.sent[-1][2]["content"].count("\n- ") >= 12 + 2,
              client.sent[-1][2]["content"][-300:])
        with io.open(os.path.join(out_dir, mod.LOG_BASENAME), encoding="utf-8") as fh:
            log = fh.read()
        check("[SEED] が残る（単体1・町1）", log.count("[SEED]") == 2 and "single" in log and "town" in log, log[-400:])
        check("[SEED] に種の文", "宿屋(inn)の owner:" in log and "この人物:" in log, log[-400:])
        check("例外を握り潰していない", not ctx.errors, ctx.errors)
    finally:
        for name, fn in _PRISTINE.items():
            setattr(FakeClient, name, fn)
        shutil.rmtree(out_dir, ignore_errors=True)

    print("\n{} check(s), {} failure(s)".format(len(passed) + len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
