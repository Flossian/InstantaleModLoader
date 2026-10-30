# -*- coding: utf-8 -*-
"""143_fix_random_character をゲーム抜きで通す。

    python tools/tests/test_random_character.py

確認するもの:

  見分け     さいころ（プレイヤーキャラクター・ランダムに・才能点）と立ち絵の要素
             （主人公の外見のカテゴリ・画像生成プロンプト）だけ。
             NPC の生成・町の生成・背景は触らない
  世界の文   名前・概要・情勢を1行ずつ。情勢は設定で外せる。概要も情勢も無ければ足さない。
             長い文は切る
  作り       才能点は [下限, 上限] から引き、使える点で頭を打つ。使える点が無ければ引かない。
             年齢はその年齢の目安が才能点の半分以下に収まる範囲。能力値は高2つ・低1つが重ならない。
             頼み文の才能点の行を読む
  書き換え   さいころには世界と作りを、立ち絵の要素には世界だけを、user 本文の末尾に足す。
             元の並びは変えない。2度目は足さない。世界と作りは片方だけでも足せる
  世界の決め方 作成画面の控えが無ければ遊んでいる世界（app.world_dict）。
             控えがあれば、app の世界と同じならそれ、違えば worlds\\<世界名>\\world_data.json
             （XOR のまま読む）。ファイルは中身が変わるまで読み直さない。どれも無ければ足さない
  画面の控え 作成画面を開く・さいころ・外見の生成で世界名を控え、「続きから」で捨てる
  経路       偽の LlamaCppClient の chat に流すと user 本文に世界と作りが付き、system は変わらず、
             画面に載った後に [COST] が残る。
             [WORLD] / [BUILD] / [SCREEN] / [SKIP] がログに残る。自己検証が通る
"""

import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import saves                 # noqa: E402


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


MOD_DIR, MOD_PATH = find_mod("_fix_random_character")


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_random_character", MOD_PATH, submodule_search_locations=[MOD_DIR])
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
# 実機の create_character の書き出し・才能点の行・「自動で足す」一覧（output_data の形）。
CHARACTER = ["あなたは大人向けの無骨で無慈悲なダークファンタジーRPG『Instantale』のキャラクター生成AIです。\n",
             "以下の仕様に基づいて、**1体のプレイヤーキャラクター**を**ランダムに**生成してください。\n"
             "【指示】\n- キャラクターの作成は「才能点」を消費して行われます。\n"
             "- 今回利用可能な才能点：**2497**\n"
             "   - [medieval, masterpiece, high quality, full body, standing, black background] は自動で追加されるため、\n"]
# Gemini 以外のクラウドで型が先頭の system に来る形（create_character の 1.json）。
CHARACTER_WITH_SCHEMA = ["必ず日本語で、以下のjson形式で出力すること: {'properties': {'name': ...}}"] + CHARACTER
# 実機の create_look の形。
LOOK = ["あなたはいわゆる大人向けのダークファンタジーRPGの管理者です。世界観と背景に基づき、"
        "物語の主人公の外見のカテゴリと立ち絵の英語の画像生成プロンプトを設定してください。"
        "君の出力に対して[medieval, masterpiece,high quality,full body,standing,black background, {category}]を追加で自動挿入する",
        "【設定】- 主人公の名前: 見本\n- 性別: ♂\n- 出自: 見本の出自\n- 外見: 見本の外見\n"]
# 実機の background_image_creator の形。
BACKGROUND = ["あなたは中世ファンタジーRPGビジュアルノベルの背景画像の生成担当です。エリア内の指定ロケーションの背景画像の"
              "画像生成プロンプトを作成してください。",
              "【エリア情報】\n- 名前: 見本の地区\n"]
# 作成画面のスキル・特質の生成（create_skill / create_trait。exe の定数の書き出し）。
SKILL = ["あなたは大人向けの無骨で無慈悲なダークファンタジーRPGのキャラクター生成AIです。\n"
         "userに与えられたキャラクターデータ、スキルリクエストと以下の仕様に基づいて、"
         "**キャラクターに追加する1つのアクティブスキル**を生成してください。\n"
         "  - `element`: スキルの属性（例：火、水、毒、闇など）。",
         "【キャラクターのデータ】\n- 名前: 見本\n\n【スキルのリクエスト】\n- 得意なことを生かした技"]
TRAIT = ["あなたは大人向けの無骨で無慈悲なダークファンタジーRPGのキャラクター生成AIです。\n"
         "userに与えられたキャラクターデータ、特質リクエストと以下の仕様に基づいて、"
         "**キャラクターに追加する1つの特質（戦闘スキルや6属性値で表せない性質など）の獲得に必要な消費ポイント量**を生成してください。",
         "【キャラクターのデータ】\n- 名前: 見本\n\n【特質のリクエスト】\n- 生まれ育ちに由来する勘"]
NPC = ["あなたはダークファンタジーRPGのキャラクター生成AIだ。",
       "【生成するNPC】\n- 名前: 宿屋の主人\n- 概要: 宿を営む\n- 強さのランク: 8\n"]
TOWN = ["あなたはダークファンタジーRPGのワールドマップジェネレーターです。",
        "【世界のデータ】\n- 世界の概要: 見本\n【NPCの生成要素】\n- settlement_name: 風の村\n"]

WORLD_A = {"world_data": {"name": "見本の島々", "overview": "言葉を話す動物たちが暮らす島々。",
                          "structure_description": "三層の島々。",
                          "story": {"world_situation": "霧が濃くなっている。"}}}
WORLD_B = {"world_data": {"name": "見本の都市", "overview": "剣も魔法も無い企業都市。",
                          "story": {"world_situation": "企業が下層を締め付けている。"}}}


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


class FakeApp(object):
    def __init__(self, world_dict):
        self.world_dict = world_dict


class FakeVM(object):
    def __init__(self, age):
        self.age = age


class FakeStoryApp(object):
    def __init__(self, world_name):
        self.world_name = world_name
        self.talent_point = 2497
        self.character_vm = FakeVM(33)


class FakeCtx(object):
    """LLM の3点は偽の client へ当て、それ以外（作成画面・続きから・絵の関数）は控えて手で呼ぶ。"""

    def __init__(self, client, out_dir, mod_dir):
        self.client = client
        self.out_dir = out_dir
        self.mod_dir = mod_dir
        self.api = 1
        self.lines = []
        self.errors = []
        self.hooks = {}
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

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def wrap(self, target, required=True, **kw):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            return lambda fn: fn          # クラウド別名は無い（ローカル経路だけ見る）
        if name not in _PRISTINE:
            def keep(fn):
                self.hooks[target] = fn
                return fn
            return keep

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


def write_world(base, folder, world_dict, obfuscate=True):
    path = os.path.join(saves.worlds_dir(base), folder, "world_data.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = json.dumps(world_dict, ensure_ascii=False).encode("utf-8")
    with io.open(path, "wb") as fh:
        fh.write(saves.xor(raw) if obfuscate else raw)
    return path


def main():
    mod.forget_world()
    plan = mod.plan_build(2497, random.Random(3), 10, 150)

    # -- 見分け --------------------------------------------------------------
    print("[見分け]")
    check("さいころ", mod.classify(CHARACTER) == ("character", 1), mod.classify(CHARACTER))
    check("さいころ（型が先頭の system）", mod.classify(CHARACTER_WITH_SCHEMA) == ("character", 2),
          mod.classify(CHARACTER_WITH_SCHEMA))
    check("立ち絵の要素（user 本文に足す）", mod.classify(LOOK) == ("look", 1), mod.classify(LOOK))
    check("背景は触らない（144_ の受け持ち）", mod.classify(BACKGROUND) is None)
    check("スキルの生成（user 本文に足す）", mod.classify(SKILL) == ("skill", 1), mod.classify(SKILL))
    check("特質の生成（user 本文に足す）", mod.classify(TRAIT) == ("trait", 1), mod.classify(TRAIT))
    check("NPC の単体生成は触らない", mod.classify(NPC) is None)
    check("町の生成は触らない", mod.classify(TOWN) is None)
    check("空の並び", mod.classify([]) is None)
    check("3語のうち1つ欠ければ触らない",
          mod.classify(["**1体のプレイヤーキャラクター**を**ランダムに**生成してください。"]) is None)

    # -- 世界の文 --------------------------------------------------------------
    print("\n[世界の文]")
    facts = mod.world_facts(WORLD_A, situation=True)
    check("名前・概要・情勢の3行", [h for h, _t in facts] == ["世界の名前", "世界の概要", "世界の情勢"], facts)
    check("地図の構造は渡さない", not any("三層" in t for _h, t in facts), facts)
    check("情勢を外せる", [h for h, _t in mod.world_facts(WORLD_A, situation=False)] == ["世界の名前", "世界の概要"])
    check("概要も情勢も無ければ空", mod.world_facts({"world_data": {"name": "名前だけ"}}, situation=True) == [])
    check("world_data が無ければ空", mod.world_facts({"areas": {}}) == [] and mod.world_facts(None) == [])
    long_text = "長" * (mod.TEXT_LIMIT + 50)
    clipped = mod.world_facts({"world_data": {"name": "x", "overview": long_text}})
    check("長い概要は切る", clipped and len(clipped[1][1]) == mod.TEXT_LIMIT + 1 and clipped[1][1].endswith("…"),
          clipped and len(clipped[1][1]))
    check("改行は1行に畳む", mod.clip("一行目\n二行目") == "一行目 二行目")
    full = mod.world_facts({"world_data": {"name": "x", "overview": long_text,
                                           "story": {"world_situation": long_text}}},
                           situation=True, limit=None)
    check("上限なしなら概要も情勢も全文", [t for _h, t in full[1:]] == [long_text, long_text],
          [len(t) for _h, t in full])

    # -- 作り ------------------------------------------------------------------
    print("\n[作り]")
    check("頼み文の才能点の行を読む", mod.available_talent(CHARACTER[1]) == 2497)
    check("才能点の行が無ければ None", mod.available_talent("才能点の話") is None)
    plans = [mod.plan_build(2497, random.Random(i), 10, 150) for i in range(400)]
    talents = [p["talent"] for p in plans]
    check("才能点は 10〜150 から引く", min(talents) >= 10 and max(talents) <= 150
          and min(talents) < 30 and max(talents) > 120, (min(talents), max(talents)))
    check("年齢は 20〜50 で、目安が才能点の半分以下",
          all(mod.AGE_MIN <= p["age"] <= mod.AGE_MAX and mod.age_cost(p["age"]) <= p["talent"] * mod.AGE_SHARE
              for p in plans))
    ages = [p["age"] for p in plans]
    check("年齢がばらつく（20代も40代後半も出る）", min(ages) <= 25 and max(ages) >= 47, (min(ages), max(ages)))
    check("若い側に寄る（平均 33 歳未満・40 歳以上は 2 割未満）",
          sum(ages) / len(ages) < 33 and sum(a >= 40 for a in ages) < len(ages) * 0.2,
          (sum(ages) / len(ages), sum(a >= 40 for a in ages)))
    check("若い年齢を引いた回は、届くところまで才能点を引き上げる（20 歳なら 132 点以上）",
          all(p["talent"] >= 132 for p in plans if p["age"] == 20) and any(p["age"] == 20 for p in plans))
    check("高い能力値2つと低い1つは重ならない",
          all(len(p["high"]) == 2 and len(p["low"]) == 1 and p["low"][0] not in p["high"] for p in plans))
    check("高い能力値の先頭が6つとも出る", len({p["high"][0][0] for p in plans}) == 6)
    few = [mod.plan_build(25, random.Random(i), 10, 150) for i in range(100)]
    check("使える点が上限より少なければそこまで", all(10 <= p["talent"] <= 25 for p in few))
    check("使える点が少ないと若い年齢は出ない（目安 66 点の 20 歳）", all(p["age"] > 25 for p in few))
    check("使える点が下限より少なければ使える点ちょうど",
          all(mod.plan_build(4, random.Random(i), 10, 150)["talent"] == 4 for i in range(20)))
    check("使える点が無ければ引かない", mod.plan_build(0) is None and mod.plan_build(None) is None)
    check("45 歳から上は点が戻る", mod.age_cost(50) == -6 and mod.age_cost(44) == 0 and mod.age_cost(20) == 66)
    block = mod.build_block({"talent": 80, "age": 27, "high": [("dex", "器用"), ("int", "知力")],
                             "low": [("cha", "魅力")]})
    check("作りの文に点数・年齢・能力値", "およそ 80 点（64〜80 点）" in block and "27 歳前後" in block
          and "dex（器用）・int（知力） を高く" in block and "cha（魅力） を最も低く" in block, block)

    # -- 書き換え --------------------------------------------------------------
    print("\n[書き換え]")
    got, kind, done = mod.rewrite(CHARACTER, facts, plan)
    check("さいころ: 世界・作り", kind == "character" and done == ["world", "build"], done)
    check("さいころ: 世界と作りは user 本文の末尾", got[1].index(mod.HEAD) < got[1].index(mod.HEAD_BUILD)
          and "言葉を話す動物たち" in got[1] and "傭兵や兵士に決め打ちしない" in got[1], got[1][-500:])
    check("さいころ: 役の説明より世界を優先と言う", "冒頭の役の説明にあるジャンル" in got[1])
    check("さいころ: system は変わらない", got[0] == CHARACTER[0])
    check("さいころ: 元の並びは変わらない", mod.HEAD not in CHARACTER[1])
    check("さいころ: 2度目は足さない", mod.rewrite(got, facts, plan)[0] is None)
    got, kind, done = mod.rewrite(LOOK, facts, plan)
    check("立ち絵: 世界だけ（作りは足さない）", kind == "look" and done == ["world"]
          and mod.HEAD_BUILD not in got[1] and "種族や体の特徴" in got[1], done)
    check("立ち絵: system は変わらない", got[0] == LOOK[0])
    got, kind, done = mod.rewrite(CHARACTER, [], None)
    check("世界も作りも無ければ触らない（種類は返す）", got is None and kind == "character")
    got, kind, done = mod.rewrite(CHARACTER, [], plan)
    check("作りだけ足せる", done == ["build"] and mod.HEAD not in got[1])
    check("対象でなければ (None, None, [])", mod.rewrite(NPC, facts, plan) == (None, None, []))
    check("背景は (None, None, [])", mod.rewrite(BACKGROUND, facts, plan) == (None, None, []))
    got, kind, done = mod.rewrite(CHARACTER, facts, plan)
    check("さいころ: スキルと特質も世界の技術・文化から作ると言う",
          mod.RULE_SKILL in got[1] and mod.RULE_TRAIT in got[1] and "火・水・毒・闇に寄せず" in got[1])
    check("さいころ: 止めるのは概要と食い違うものだけ。触れていないことは雰囲気から補ってよい",
          "世界の概要と食い違うもの" in got[1] and "概要の雰囲気から自然に補ってよい" in got[1]
          and "世界の概要に無い" not in got[1], got[1][-600:])
    got, kind, done = mod.rewrite(SKILL, facts, plan)
    check("スキル: 世界とスキルの決まりだけ（作りは足さない）", kind == "skill" and done == ["world"]
          and mod.RULE_SKILL in got[1] and mod.RULE_TRAIT not in got[1] and mod.HEAD_BUILD not in got[1]
          and got[1].startswith(SKILL[1]) and got[0] == SKILL[0], got and got[1][-300:])
    got, kind, done = mod.rewrite(TRAIT, facts, plan)
    check("特質: 世界と特質の決まりだけ", kind == "trait" and done == ["world"]
          and mod.RULE_TRAIT in got[1] and mod.RULE_SKILL not in got[1] and "言葉を話す動物たち" in got[1], done)
    check("特質: 2度目は足さない", mod.rewrite(got, facts, plan)[0] is None)

    # -- 世界の決め方 ----------------------------------------------------------
    print("\n[世界の決め方]")
    base = tempfile.mkdtemp(prefix="random_character_")
    try:
        path_b = write_world(base, "見本の都市", WORLD_B)
        app_a = FakeApp(WORLD_A)
        mod.forget_world()
        world, name, source = mod.current_world(app_a, base)
        check("控えが無ければ遊んでいる世界", world is WORLD_A and name == "見本の島々" and source == "app",
              (name, source))
        check("控えも app も無ければ none", mod.current_world(None, base) == (None, None, "none"))
        mod.remember_world("見本の島々")
        world, name, source = mod.current_world(app_a, base)
        check("控えと app の世界が同じなら app", world is WORLD_A and source == "app", (name, source))
        mod.remember_world("見本の都市")
        world, name, source = mod.current_world(app_a, base)
        check("控えが別の世界ならファイル（XOR のまま読む）",
              source == "file" and name == "見本の都市" and world == WORLD_B, (name, source))
        again = mod.current_world(app_a, base)[0]
        check("ファイルは中身が変わるまで読み直さない（同じ辞書）", again is world)
        changed = {"world_data": dict(WORLD_B["world_data"], overview="書き換えた概要。")}
        write_world(base, "見本の都市", changed)
        stamp = os.stat(path_b).st_mtime + 5
        os.utime(path_b, (stamp, stamp))
        check("ファイルが変われば読み直す", mod.current_world(app_a, base)[0]["world_data"]["overview"] == "書き換えた概要。")
        write_world(base, "素のまま", WORLD_B, obfuscate=False)
        mod.remember_world("素のまま")
        check("素の JSON の世界も読める", mod.current_world(None, base)[2] == "file")
        mod.remember_world("無い世界")
        check("ファイルが無ければ none（世界名は返す）", mod.current_world(app_a, base) == (None, "無い世界", "none"))
        mod.forget_world()
        check("控えを捨てたら app に戻る", mod.current_world(app_a, base)[2] == "app")
        check("空の世界名は控えない", mod.remember_world("") is False and mod.creation_world() is None)
    finally:
        mod.forget_world()
        shutil.rmtree(base, ignore_errors=True)

    # -- 経路 --------------------------------------------------------------------
    print("\n[経路]")
    out_dir = tempfile.mkdtemp(prefix="random_character_out_")
    data_dir = tempfile.mkdtemp(prefix="random_character_data_")
    old_env = os.environ.get("IML_INSTANTALE_DATA")
    old_find = mod.ui.find_app
    try:
        os.environ["IML_INSTANTALE_DATA"] = data_dir
        write_world("", "見本の都市", WORLD_B)
        mod.ui.find_app = lambda: FakeApp(WORLD_A)
        for name, fn in _PRISTINE.items():
            setattr(FakeClient, name, fn)
        client = FakeClient()
        ctx = FakeCtx(client, out_dir, MOD_DIR)
        mod.RNG = random.Random(5)
        mod.apply(ctx)
        check("自己検証が通る", any("self-check passed" in l for l in ctx.lines)
              and not any("VERIFY FAILED" in l for l in ctx.lines), ctx.lines)
        check("仕掛かった記録", any("random character: armed on" in l for l in ctx.lines), ctx.lines)
        story = mod.STORY_APP
        check("作成画面の4つと続きからを包み、絵の関数は包まない",
              all(t in ctx.hooks for t in (story + ".__init__", story + ".generate_random_character",
                                            story + ".generate_look_async", story + ".apply_character_sheet",
                                            "__main__:InstantaleApp.load_game_new"))
              and not any("image_generation" in t for t in ctx.hooks), sorted(ctx.hooks))

        # 作成画面を開く（world_name は3つ目の位置引数）。
        screen = FakeStoryApp("見本の都市")
        ctx.hooks[story + ".__init__"](lambda self, *a, **k: None, screen, "sm", "cb", "見本の都市", "img")
        check("画面を開くと世界名を控える", mod.creation_world() == "見本の都市", mod.creation_world())
        messages = [{"role": "system", "content": CHARACTER[0]}, {"role": "user", "content": CHARACTER[1]}]
        client.chat("m", messages)
        sent = client.sent[-1]
        check("chat: さいころに作成画面の世界と作り（前の世界の app ではなく）",
              "剣も魔法も無い企業都市" in sent[1]["content"] and "言葉を話す動物たち" not in sent[1]["content"]
              and mod.HEAD_BUILD in sent[1]["content"], sent[1]["content"][-300:])
        check("chat: system は変わらない", sent[0]["content"] == CHARACTER[0])
        check("chat: 元の messages は変わらない", messages[1]["content"] == CHARACTER[1])
        # 画面の表示が見つからない回（ゲーム抜きは Kivy が無い）。
        ctx.hooks[story + ".apply_character_sheet"](lambda self, *a, **k: "applied", screen, {"age": 33})
        # 実機の画面は残りを属性に持たず、「残り才能: 2497(-31)」の文字にだけ出す。
        old_remaining = mod.remaining_text
        mod.remaining_text = lambda: "残り才能: 2497(-31)"
        client.chat("m", [{"role": "system", "content": CHARACTER[0]}, {"role": "user", "content": CHARACTER[1]}])
        ctx.hooks[story + ".apply_character_sheet"](lambda self, *a, **k: "applied", screen, {"age": 33})
        mod.remaining_text = old_remaining
        client.chat("m", [{"role": "system", "content": LOOK[0]}, {"role": "user", "content": LOOK[1]}])
        check("chat: 立ち絵の要素にも同じ世界", "剣も魔法も無い企業都市" in client.sent[-1][1]["content"])
        client.chat("m", [{"role": "system", "content": NPC[0]}, {"role": "user", "content": NPC[1]}])
        check("chat: NPC の生成はそのまま", client.sent[-1][1]["content"] == NPC[1])
        client.chat("m", [{"role": "system", "content": BACKGROUND[0]}, {"role": "user", "content": BACKGROUND[1]}])
        check("chat: 背景はそのまま", client.sent[-1][0]["content"] == BACKGROUND[0])

        # 「続きから」で控えを捨て、遊んでいる世界へ戻る（335 の描き直し）。
        ctx.hooks["__main__:InstantaleApp.load_game_new"](lambda self, *a, **k: None, object(), "見本の島々")
        check("続きからで控えを捨てる", mod.creation_world() is None)
        client.chat("m", [{"role": "system", "content": LOOK[0]}, {"role": "user", "content": LOOK[1]}])
        check("chat: 遊んでいる最中の立ち絵の要素は遊んでいる世界",
              "言葉を話す動物たち" in client.sent[-1][1]["content"], client.sent[-1][1]["content"][-300:])

        # 注入し直した後は、さいころの押下で画面の世界を控え直す。
        ctx.hooks[story + ".generate_random_character"](lambda self, *a, **k: None, screen)
        check("さいころで画面の世界を控え直す", mod.creation_world() == "見本の都市")
        ctx.hooks[story + ".generate_look_async"](lambda self, *a, **k: None, FakeStoryApp("無い世界"))
        client.chat("m", [{"role": "system", "content": CHARACTER[0]}, {"role": "user", "content": CHARACTER[1]}])
        check("chat: 世界が読めなくても作りは足す",
              mod.HEAD not in client.sent[-1][1]["content"] and mod.HEAD_BUILD in client.sent[-1][1]["content"])

        with io.open(os.path.join(out_dir, mod.LOG_BASENAME), encoding="utf-8") as fh:
            log = fh.read()
        check("[WORLD] が残る（さいころ3・立ち絵2）",
              log.count("[WORLD]") == 5 and log.count("character | world+build 800字まで | 見本の都市 from file") == 2
              and "look | world 800字まで | 見本の島々 from app" in log, log[-900:])
        check("[BUILD] が3回", log.count("[BUILD] 才能点") == 3, log[-600:])
        check("[COST] が画面に載った後に（表示の文字から残りと使う点を読む。無ければ ?）",
              log.count("[COST]") == 2 and "画面: 残り ? / 才能点 2497 / 年齢 33" in log
              and "画面: 残り才能: 2497(-31) / 才能点 2497 / 年齢 33" in log, log[-600:])
        check("[SCREEN] が残る（開く・続きから・さいころ・外見）",
              all(w in log for w in ("[SCREEN] open", "[SCREEN] load", "[SCREEN] dice", "[SCREEN] look")), log[-600:])
        check("[SKIP] が残る（読めない世界）", log.count("[SKIP]") == 1 and "無い世界" in log, log[-400:])
        check("例外を握り潰していない", not ctx.errors, ctx.errors)
    finally:
        mod.ui.find_app = old_find
        mod.forget_world()
        if old_env is None:
            os.environ.pop("IML_INSTANTALE_DATA", None)
        else:
            os.environ["IML_INSTANTALE_DATA"] = old_env
        for name, fn in _PRISTINE.items():
            setattr(FakeClient, name, fn)
        shutil.rmtree(out_dir, ignore_errors=True)
        shutil.rmtree(data_dir, ignore_errors=True)

    print("\n{} check(s), {} failure(s)".format(len(passed) + len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
