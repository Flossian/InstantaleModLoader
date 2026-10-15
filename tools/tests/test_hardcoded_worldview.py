# -*- coding: utf-8 -*-
"""144_fix_hardcoded_worldview をゲーム抜きで通す。

    python tools/tests/test_hardcoded_worldview.py

確認するもの:

  役の一文   実機の書き出し7通り（TRPG のゲームマスター・RPG のキャラクター生成AI・大人向けの無骨で無慈悲な・
             いわゆる大人向けの・中世ファンタジーRPG・TRPGシステム・MOD の写し）からジャンルだけが外れる。
             世界の文の中の「ダークファンタジー」には当てない
  世界を足す 世界の情報が無い頼みにだけ、役の一文の後ろへ名前と概要を足す。
             世界の情報がある頼み・キャラ作成の5つ・世界の生成・世界が読めないときは足さない。長い概要は切る
  世界なし   世界の情報が無く足せもしない頼み（世界が読めない語り・143_ が世界を足さなかったキャラ作成・
             概要を空にした世界の生成）は、ジャンルに関わる書き換えをせず既定のまま。許可の理由だけは直す。
             143_ が【この世界のデータ】を足したキャラ作成の頼みは、世界ありとして直す
  会話の調子 111 の同梱ルールの文から「救いのないダークファンタジー」と「現代的」を外す（会話は【土地情報】で世界ありとみなす）
  許可の理由 「ダークファンタジーであるため、」→「ゲームであるため、」。許可の文は残る
  共通世界観 冒険者の項だけを絞り、エリアの項と後ろの文は残る
  一覧       頼み文の「[medieval, …]」から medieval を抜く
  設定       どれも個別に切れる。全部切れば何もしない。2度目は何もしない
  絵         前置きから medieval / dark fantasy / shabby / fantasy setting、否定から modern / urban を抜く。
             書き方と別の語（medieval fantasy cloak）は残す。prompts.json も直す
  経路       偽の LlamaCppClient の chat に流すと system の役の一文が直り世界が付く。
             絵の関数へ渡る前置きと否定が直る。[TEXT] / [IMAGE] がログに残る。自己検証が通る
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

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


MOD_DIR, MOD_PATH = find_mod("_fix_hardcoded_worldview")


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_hardcoded_worldview", MOD_PATH, submodule_search_locations=[MOD_DIR])
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


# ---------------------------------------------------------------- 見本（実機の output_data の書き出し）
ROLES = [
    ("あなたはダークファンタジーTRPGのゲームマスターである。文体は常体。", "あなたはTRPGのゲームマスターである。文体は常体。"),
    ("あなたはダークファンタジーRPGのキャラクター生成AIだ。", "あなたはRPGのキャラクター生成AIだ。"),
    ("あなたは大人向けの無骨で無慈悲なダークファンタジーRPG『Instantale』のキャラクター生成AIです。",
     "あなたはRPG『Instantale』のキャラクター生成AIです。"),
    ("あなたはいわゆる大人向けのダークファンタジーRPGの管理者です。", "あなたはRPGの管理者です。"),
    ("あなたは中世ファンタジーRPGビジュアルノベルの背景画像の生成担当です。", "あなたはRPGビジュアルノベルの背景画像の生成担当です。"),
    ("あなたはダークファンタジーTRPGシステムのベースモデルとして、以下の役割を持つ", "あなたはTRPGシステムのベースモデルとして、以下の役割を持つ"),
    ("あなたはダークファンタジーRPGの登場人物", "あなたはRPGの登場人物"),
]
NARRATOR = ["【指示】\nあなたはダークファンタジーRPGの物語を紡ぐ担当です。プレイヤーの<今回の行動>に対し…",
            "<今回の行動> 酒場に入る"]
GUARD = ["型", "あなたはダークファンタジーRPGのキャラクター生成AIだ。\n衛兵NPCをデザインしろ。",
         "【世界の情報】…\n【生成するNPC】\n- 強さのランク: 20\n"]
CREATE_LOOK = ["あなたはいわゆる大人向けのダークファンタジーRPGの管理者です。世界観と背景に基づき、物語の主人公の外見のカテゴリと"
               "立ち絵の英語の画像生成プロンプトを設定してください。君の出力に対して[medieval, masterpiece,high quality,full body,"
               "standing,black background, {category}]を追加で自動挿入する", "【設定】- 主人公の名前: 見本\n"]
CHECK_ILLEGAL = ["あなたはいわゆる大人向けのダークファンタジーRPGの管理者です。今プレイヤーは主人公キャラクターの設定を提出しました。",
                 "- 名前: 見本"]
TALK = ["【全体の指示】…AIアシスタントとしてのあなたの性格はさておき、ただキャラのロールプレイとして振舞ってください。"
        "この世界は救いのないダークファンタジーであり、優等生的・現代的な言い回しや不要な気遣いはしない。要約や前置きを避け…\n"
        "【土地情報】\n- 見本の街: 港の町\n"
        "【違法・安全ポリシー違反・禁止表現の基準】\n- ダークファンタジーであるため、窃盗、暴力、暗殺、戦闘などの犯罪行為は許可する。\n"
        "- 未成年（18歳未満）が関与する性的内容は絶対に禁止。", "こんにちは"]
COMMON = ["あなたはダークファンタジーRPGのワールドマップジェネレーターです。",
          "【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、夢のある仕事ではない。"
          "社会的立場は低いし、殆どが日雇い労働とあわせてぎりぎり日々の暮らしを維持している。彼らは様々な理由で命がけのこの仕事に就く。"
          "宗教的な理由や信念、野心に基づくもの、故郷を追われた犯罪者、市民権とひきかえの元奴隷。"
          "わずかに成功を得た者たちも生きる道を他に作って引退していくのが普通だ。- エリア: 世界はいくつかのエリアに分かれている。\n"
          "【手順】\n1. 指定されたエリアの拠点の…\n【世界のデータ】\n- 世界の概要: 見本の概要"]
# 実機の create_story / create_world_overview の形（2026-10-11 の output_data）。冒険者の項の後ろにエリアの項が無い。
STORY = ["あなたはダークファンタジーRPGの世界そのものを生成する担当です。\n\n"
         "【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、夢のある仕事ではない。"
         "社会的立場は低いし、殆どが日雇い労働とあわせてぎりぎり日々の暮らしを維持している。\n"
         "彼らは様々な理由で命がけのこの仕事に就く。宗教的な理由や信念、野心に基づくもの、故郷を追われた犯罪者、市民権とひきかえの元奴隷。\n"
         "そして多くは大した戦果も挙げられずに死ぬか、何らかの後遺を負って引退する。\n\n"
         "【手順】\n1. 現在の世界はこんな状況であるという…", "【世界】"]
OVERVIEW = ["あなたはダークファンタジーRPGの世界そのものを生成する担当です。\n\n"
            "【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、夢のある仕事ではない。"
            "市民権とひきかえの元奴隷。わずかに成功を得た者たちも生きる道を他に作って引退していくのが普通だ。\n"
            "【予め指定済みの設定】- 世界の名前: 見本- 世界の概要: 見本の概要\n【手順】\n1."]
# 作成画面のスキル・特質の生成（exe の定数の書き出し）。
SKILL = ["あなたは大人向けの無骨で無慈悲なダークファンタジーRPGのキャラクター生成AIです。\n"
         "userに与えられたキャラクターデータ、スキルリクエストと以下の仕様に基づいて、"
         "**キャラクターに追加する1つのアクティブスキル**を生成してください。",
         "【キャラクターのデータ】\n- 名前: 見本\n\n【スキルのリクエスト】\n- 技"]
TRAIT = ["あなたは大人向けの無骨で無慈悲なダークファンタジーRPGのキャラクター生成AIです。\n"
         "userに与えられたキャラクターデータ、特質リクエストと以下の仕様に基づいて、"
         "**キャラクターに追加する1つの特質（戦闘スキルや6属性値で表せない性質など）の獲得に必要な消費ポイント量**を生成してください。\n"
         "【その他】\n- この世界の誰もが魔法、精霊、神、悪魔、自然の力を借りた大いなる術を唱えられるわけでは無し、"
         "だからといって代わりに何らか戦いのための明確な力をもつとも限らない。全ては個人の才能と出自による。",
         "【キャラクターのデータ】\n- 名前: 見本\n\n【特質のリクエスト】\n- 勘"]
# 概要を空にした世界の生成（2026-10-11 の output_data の create_world_overview\5.json の形）。世界の情報が無い。
NO_PLOT = ["あなたはダークファンタジーRPGの世界そのものを生成する担当です。\n面白そうな世界の概要を生成してください。\n"
           "【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、夢のある仕事ではない。"
           "わずかに成功を得た者たちも生きる道を他に作って引退していくのが普通だ。\n【手順】\n1."]
# 役の一文も世界の情報も無く、許可の理由だけがある頼み。
POLICY_ONLY = ["【違法・安全ポリシー違反・禁止表現の基準】\n- ダークファンタジーであるため、窃盗は許可する。"]
# 世界の文の中に「ダークファンタジー」がある（作った人が書いた世界）。役の一文ではないので触らない。
WORLD_TEXT = ["【世界の情報】\n- 世界の概要: 救いのないダークファンタジーRPGの世界。", "質問"]

WORLD = {"world_data": {"name": "見本の都市", "overview": "剣も魔法も無い企業都市。人々は義体と銃で生きる。"}}

CHARACTER_PREFIX = ("full-body, masterpiece,high quality, detailed face, medieval, dark fantasy, watercolor, "
                    "standing,shabby, young man, pale skin, empty background,black background,wallpaper")
BACKGROUND_PREFIX = ("masterpiece, best quality, watercolor, fantasy setting, medieval, highly detailed, "
                     "indoor, shop interior")
BACKGROUND_NEGATIVE = "photoreal, modern, urban, people, person, human, 1girl, 1boy, women, man, crowd, asian, pink"


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


class FakeCtx(object):
    """LLM の3点は偽の client へ当て、それ以外（絵の関数）は控えて手で呼ぶ。"""

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


def main():
    # -- 役の一文 --------------------------------------------------------------
    print("[役の一文]")
    for before, after in ROLES:
        got, done = mod.rewrite([before, "【世界の情報】"], WORLD)
        check("役: " + before[:24], got is not None and got[0] == after and done == ["role"], got)
    check("世界の文の中の語には当てない", mod.rewrite(WORLD_TEXT, WORLD) == (None, []))

    # -- 世界を足す ------------------------------------------------------------
    print("\n[世界を足す]")
    got, done = mod.rewrite(NARRATOR, WORLD)
    check("語り: 役の一文の後ろに世界の名前と概要", done == ["role", "world"]
          and got[0].startswith("【指示】\nあなたはRPGの物語を紡ぐ担当です。")
          and "- 世界の名前: 見本の都市" in got[0] and "剣も魔法も無い企業都市" in got[0]
          and got[1] == NARRATOR[1], got)
    check("語り: 元の並びは変わらない", "ダークファンタジー" in NARRATOR[0])
    check("語り: 2度目は何もしない", mod.rewrite(got, WORLD) == (None, []))
    got, done = mod.rewrite(GUARD, WORLD)
    check("世界の情報がある頼み（衛兵）には足さない", done == ["role"] and mod.HEAD_WORLD not in "".join(got), done)
    # キャラ作成の頼みは、143_ が【この世界のデータ】を足した後の形で 144_ に届く（143_ が外側）。
    in_world = lambda texts: texts[:-1] + [texts[-1] + "\n\n【この世界のデータ】\n- 世界の概要: 見本"]  # noqa: E731
    got, done = mod.rewrite(in_world(CREATE_LOOK), WORLD)
    check("立ち絵の要素: 143_ の世界があればジャンルと一覧の medieval を外し、世界は足さない", done == ["role", "list"]
          and mod.HEAD_WORLD not in "".join(got) and "[masterpiece,high quality" in got[0], done)
    check("立ち絵の要素: 世界が無ければゲームの既定のまま", mod.rewrite(CREATE_LOOK, WORLD) == (None, []))
    got, done = mod.rewrite(in_world(CHECK_ILLEGAL), WORLD)
    check("内容の審査: 世界があれば役を外し、足さない", done == ["role"] and mod.HEAD_WORLD not in "".join(got), done)
    check("内容の審査: 世界が無ければ既定のまま", mod.rewrite(CHECK_ILLEGAL, WORLD) == (None, []))
    got, done = mod.rewrite(in_world(SKILL), WORLD)
    check("スキルの生成: 143_ の世界があれば役を外し、足さない", done == ["role"]
          and mod.HEAD_WORLD not in "".join(got), done)
    check("スキルの生成: 世界が無ければ既定のまま", mod.rewrite(SKILL, WORLD) == (None, []))
    got, done = mod.rewrite(in_world(TRAIT), WORLD)
    check("特質の生成: 魔法の前提の文を外し、世界は足さない", done == ["role", "magic"]
          and "魔法、精霊" not in got[0] and "【その他】\n- 誰もが戦いのための明確な力をもつとは限らない。全ては個人の才能と出自による。"
          in got[0] and mod.HEAD_WORLD not in "".join(got), got[0][-200:])
    check("特質の生成: 世界が無ければ魔法の文も残す", mod.rewrite(TRAIT, WORLD) == (None, []))
    check("特質の生成: 役を切れば魔法の文も残す", mod.rewrite(in_world(TRAIT), WORLD, role=False) == (None, []))
    check("概要を空にした世界の生成: 遊んでいる世界を足さず、既定のまま",
          mod.rewrite(NO_PLOT, WORLD) == (None, []))
    check("世界が読めなければ役も外さない（既定のまま）", mod.rewrite(NARRATOR, None) == (None, []))
    check("概要が無ければ足さず、役も外さない",
          mod.rewrite(NARRATOR, {"world_data": {"name": "名前だけ"}}) == (None, []))
    got, done = mod.rewrite(POLICY_ONLY, None)
    check("許可の理由は世界が無くても直す", done == ["policy"] and "ゲームであるため" in got[0], done)
    long_world = {"world_data": {"name": "x", "overview": "長" * (mod.TEXT_LIMIT + 10)}}
    got, done = mod.rewrite(NARRATOR, long_world)
    check("長い概要は切る", ("長" * mod.TEXT_LIMIT + "…") in got[0] and ("長" * (mod.TEXT_LIMIT + 1)) not in got[0])

    # -- 会話・許可・共通世界観 --------------------------------------------------
    print("\n[会話・許可・共通世界観]")
    got, done = mod.rewrite(TALK, WORLD)
    check("会話: 調子と許可の理由", done == ["tone", "policy"], done)
    check("会話: 「優等生的な言い回し」は残る", "ロールプレイとして振舞ってください。優等生的な言い回しや不要な気遣いはしない。要約や前置き"
          in got[0], got[0][:200])
    check("会話: ダークファンタジーと現代的が消える", "ダークファンタジー" not in got[0] and "現代的" not in got[0])
    check("会話: 許可の文は残る", "- ゲームであるため、窃盗、暴力、暗殺、戦闘などの犯罪行為は許可する。" in got[0]
          and "未成年" in got[0])
    got, done = mod.rewrite(COMMON, WORLD)
    check("共通世界観: 役と冒険者の項", done == ["role", "common"], done)
    check("共通世界観: 冒険者はギルドがいることだけ",
          "- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいる。ギルドと冒険者の呼び名や描写は、その世界に合わせてよい。"
          "ただし施設の種類（type）は guild など決められた値をそのまま使う。"
          "- エリア: 世界はいくつかのエリアに分かれている。" in got[1]
          and "夢のある" not in got[1] and "元奴隷" not in got[1], got[1][:300])
    check("共通世界観: 後ろの手順と世界のデータは残る", "【手順】" in got[1] and "見本の概要" in got[1])
    got, done = mod.rewrite(STORY, WORLD)
    check("共通世界観: 世界とストーリーの生成（エリアの項が無く、改行を挟んで【手順】が続く）",
          done == ["role", "common"] and "元奴隷" not in got[0] and "夢のある" not in got[0]
          and "そのまま使う。\n\n【手順】\n1. 現在の世界は" in got[0], got[0][:400])
    got, done = mod.rewrite(OVERVIEW, WORLD)
    check("共通世界観: 世界の概要の生成（すぐ【予め指定済みの設定】が続く）",
          done == ["role", "common"] and "元奴隷" not in got[0]
          and "そのまま使う。\n【予め指定済みの設定】- 世界の名前: 見本" in got[0], got[0][:400])
    check("共通世界観: 世界の情報が無ければ既定のまま",
          mod.rewrite(["【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、暗い。"], WORLD)
          == (None, []))
    got, done = mod.rewrite(["【共通世界観】- 冒険者: この世界にはギルドとそれに属する職業冒険者たちがいるが、暗い。",
                             "【世界の情報】"], WORLD)
    check("共通世界観: 項が文の終わりまでなら終わりまで", done == ["common"] and got[0].endswith("そのまま使う。"), got)

    # -- 設定 ------------------------------------------------------------------
    print("\n[設定]")
    off = dict(role=False, add_world=False, policy=False, common=False, image=False)
    check("全部切れば何もしない", all(mod.rewrite(t, WORLD, **off) == (None, [])
                                      for t in (NARRATOR, TALK, COMMON, in_world(CREATE_LOOK))))
    check("役を切れば世界も足さない", mod.rewrite(NARRATOR, WORLD, role=False) == (None, []))
    check("世界を足さなければ、世界の無い頼みは役も外さない", mod.rewrite(NARRATOR, WORLD, add_world=False) == (None, []))
    check("許可だけ切る", mod.rewrite(TALK, WORLD, policy=False)[1] == ["tone"])
    check("共通世界観だけ切る", mod.rewrite(COMMON, WORLD, common=False)[1] == ["role"])
    check("一覧だけ切る", mod.rewrite(in_world(CREATE_LOOK), WORLD, image=False)[1] == ["role"])

    # -- 絵 --------------------------------------------------------------------
    print("\n[絵]")
    check("人物の前置き", mod.drop_tokens(CHARACTER_PREFIX, mod.POSITIVE_DROP) ==
          "full-body, masterpiece,high quality, detailed face, watercolor, standing, young man, pale skin, "
          "empty background,black background,wallpaper", mod.drop_tokens(CHARACTER_PREFIX, mod.POSITIVE_DROP))
    check("背景の前置き", mod.drop_tokens(BACKGROUND_PREFIX, mod.POSITIVE_DROP) ==
          "masterpiece, best quality, watercolor, highly detailed, indoor, shop interior")
    check("背景の否定", mod.drop_tokens(BACKGROUND_NEGATIVE, mod.NEGATIVE_DROP) ==
          "photoreal, people, person, human, 1girl, 1boy, women, man, crowd, asian, pink")
    check("先頭・大文字", mod.drop_tokens("Medieval, a, DARK FANTASY", mod.POSITIVE_DROP) == "a")
    check("別の語は残す", mod.drop_tokens("a, medieval fantasy cloak, urban legend", mod.POSITIVE_DROP + mod.NEGATIVE_DROP)
          == "a, medieval fantasy cloak, urban legend")
    check("無ければ同じものを返す", mod.drop_tokens("a, b", mod.POSITIVE_DROP) == "a, b"
          and mod.drop_tokens(None, mod.POSITIVE_DROP) is None)
    base = tempfile.mkdtemp(prefix="hardcoded_worldview_")
    try:
        folder = os.path.join(saves.worlds_dir(base), "見本の都市", "characters", "見本")
        os.makedirs(folder)
        record = {"category": "young man", "positive_prompt": CHARACTER_PREFIX, "negative_prompt": "photoreal, urban"}
        with io.open(os.path.join(folder, "prompts.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        check("prompts.json を直す", mod.fix_prompts_json("見本の都市", "見本", base=base))
        with io.open(os.path.join(folder, "prompts.json"), encoding="utf-8") as fh:
            fixed = json.load(fh)
        check("prompts.json: 前置きと否定が直り、他は残る", "medieval" not in fixed["positive_prompt"]
              and fixed["negative_prompt"] == "photoreal" and fixed["category"] == "young man", fixed)
        check("prompts.json: 2度目は書かない", not mod.fix_prompts_json("見本の都市", "見本", base=base))
        check("prompts.json: 無ければ何もしない", not mod.fix_prompts_json("見本の都市", "居ない", base=base))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # -- 経路 --------------------------------------------------------------------
    print("\n[経路]")
    out_dir = tempfile.mkdtemp(prefix="hardcoded_worldview_out_")
    data_dir = tempfile.mkdtemp(prefix="hardcoded_worldview_data_")
    old_env = os.environ.get("IML_INSTANTALE_DATA")
    old_find = mod.ui.find_app
    try:
        os.environ["IML_INSTANTALE_DATA"] = data_dir
        mod.ui.find_app = lambda: FakeApp(WORLD)
        for name, fn in _PRISTINE.items():
            setattr(FakeClient, name, fn)
        client = FakeClient()
        ctx = FakeCtx(client, out_dir, MOD_DIR)
        # 読み込まれた方式（sdcppcuda）には `generate_upscaled_image_anime` が無い（main_023 から）
        loaded_name = mod.imagegen.CREATURE_FORMAT.format("sdcppcuda")
        loaded = types.ModuleType(loaded_name)
        loaded.generate_image_anime = lambda positive, negative, **k: None
        sys.modules[loaded_name] = loaded
        try:
            mod.apply(ctx)
        finally:
            sys.modules.pop(loaded_name, None)
        check("読み込まれた方式に無い関数は包まない（台帳の「見つからない」に載せない）",
              loaded_name + ":generate_upscaled_image_anime" not in ctx.hooks
              and loaded_name + ":generate_image_anime" in ctx.hooks, sorted(ctx.hooks))
        check("  読み込まれる前の方式はローダの保留に任せて包む",
              mod.imagegen.CREATURE_FORMAT.format("diffusers_openvino") + ":generate_upscaled_image_anime"
              in ctx.hooks)
        check("自己検証が通る", any("self-check passed" in l for l in ctx.lines)
              and not any("VERIFY FAILED" in l for l in ctx.lines), ctx.lines)
        check("仕掛かった記録", any("hardcoded worldview: armed on" in l for l in ctx.lines), ctx.lines)
        creature = mod.imagegen.CREATURE_FORMAT.format("diffusers_openvino")
        background = mod.BACKGROUND_MODULE.format("sdcppvulkan")
        check("絵の関数を包む（4方式とも）",
              all(t in ctx.hooks for t in (creature + ":generate_image_anime", creature + ":generate_character_image",
                                            creature + ":image_to_image_anime",
                                            background + ":generate_and_save_background",
                                            background + ":generate_image_real_lcm")), sorted(ctx.hooks))

        messages = [{"role": "system", "content": NARRATOR[0]}, {"role": "user", "content": NARRATOR[1]}]
        client.chat("m", messages)
        sent = client.sent[-1]
        check("chat: system の役が直り、世界が付く", sent[0]["content"].startswith("【指示】\nあなたはRPGの物語")
              and "剣も魔法も無い企業都市" in sent[0]["content"], sent[0]["content"][:200])
        check("chat: user は変わらない", sent[1]["content"] == NARRATOR[1])
        check("chat: 元の messages は変わらない", messages[0]["content"] == NARRATOR[0])
        client.chat("m", [{"role": "user", "content": "ただの話"}])
        check("chat: 対象でなければそのまま", client.sent[-1][0]["content"] == "ただの話")

        seen = []
        ctx.hooks[creature + ":generate_image_anime"](
            lambda positive, negative, **k: seen.append((positive, negative)), CHARACTER_PREFIX, "photoreal, modern")
        ctx.hooks[background + ":generate_and_save_background"](
            lambda world, location, positive_prompt, negative_prompt: seen.append((positive_prompt, negative_prompt)),
            "w", "loc", positive_prompt=BACKGROUND_PREFIX, negative_prompt=BACKGROUND_NEGATIVE)
        ctx.hooks[creature + ":image_to_image_anime"](
            lambda image, positive, negative, w, h: seen.append((positive, negative)),
            "img", CHARACTER_PREFIX, "neg", 1, 2)
        check("絵: 人物・背景・img2img の前置きと否定が直る（位置でもキーワードでも）",
              len(seen) == 3 and not any(w in p for p, _n in seen for w in ("medieval", "dark fantasy", "shabby", "fantasy setting"))
              and seen[0][1] == "photoreal" and "modern" not in seen[1][1] and seen[2][1] == "neg", seen)
        folder = os.path.join(saves.worlds_dir(""), "見本の都市", "characters", "見本")
        os.makedirs(folder)
        with io.open(os.path.join(folder, "prompts.json"), "w", encoding="utf-8") as fh:
            json.dump({"positive_prompt": CHARACTER_PREFIX}, fh)
        ctx.hooks[creature + ":generate_character_image"](lambda *a, **k: "drawn", "見本の都市", "見本", "young man", [])
        with io.open(os.path.join(folder, "prompts.json"), encoding="utf-8") as fh:
            check("絵: 描いた後に prompts.json も直る", "medieval" not in fh.read())
        mod.IMAGE_PREFIX = False
        seen.clear()
        ctx.hooks[creature + ":generate_image_anime"](
            lambda positive, negative, **k: seen.append(positive), CHARACTER_PREFIX, "neg")
        check("絵: 設定を切れば素のまま", seen == [CHARACTER_PREFIX])
        mod.IMAGE_PREFIX = True

        with io.open(os.path.join(out_dir, mod.LOG_BASENAME), encoding="utf-8") as fh:
            log = fh.read()
        check("[TEXT] が残る（語り1）", log.count("[TEXT]") == 1 and "role+world" in log, log[-500:])
        check("[IMAGE] が残る（前置き3・否定2・prompts.json 1）", log.count("[IMAGE]") == 6, log[-800:])
        check("例外を握り潰していない", not ctx.errors, ctx.errors)
    finally:
        mod.ui.find_app = old_find
        mod.IMAGE_PREFIX = True
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
