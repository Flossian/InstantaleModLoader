# -*- coding: utf-8 -*-
"""111_llm_prompt_replace.py をゲーム抜きで通す。

    python tools/tests/test_llm_prompt_replace.py

偽の `LlamaCppClient` と偽の `ctx` を差し込み、次を確認する。

  書式     … タブ / `#offtab:` / `#off:` / コメント / 確率の省略と読み取り
  同梱     … 同梱の `llm_replacements.txt` を警告なしで読めること
  復号     … `\\n` `\\uXXXX` を実際の文字に直すこと（本文は復号済みなので）
  正規表現 … `$1` `${name}` `$&` `$$` の読み替えと、存在しない番号の扱い
  確率     … グループごとに1回だけ抽選すること・合計 100 超・0%
  経路     … chat / _apply_chat_template / payload のどこでも当たること
  1回だけ  … 入れ子の経路で二重に抽選しないこと（スレッドの印と、出力の記憶）
  置き場所 … **MOD フォルダの中だけ**を読み、外は一切見ないこと。利用者の
             `llm_replacements.txt` が同梱の `.default.txt` に優先すること
  配布     … 利用者のファイルが配布物に入らないこと（＝更新で消えない根拠）
  再読込   … ファイルを書き換えると次のリクエストから効くこと
  壊さない … ルールが無い・読めない・例外が出た場合に文章をそのまま送ること

**確率の抽選は `roll` を差し替えて固定する**（乱数のままでは「1回だけ抽選している」
ことを確かめられない）。実経路の検証は偽クライアントの `chat` を呼んで行うので、
`wrap` した層の引数の受け渡しまで一緒に通る。
"""
import glob
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

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
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD_PATH = find_mod("_llm_prompt_replace")

# 仕掛ける場所はローダの語彙（`instantale_modloader.llm`）。経路の定数と見張りの
# 間隔はそちらにあるので、テストからもそちらを触る（TECH.md §3.2.3）。
from instantale_modloader import llm as ml_llm          # noqa: E402

# ゲームが自分で保存した実プロンプト。無ければ実データの照合だけ飛ばす。
GAME_DIR = r"C:\Program Files\Epic Games\Instantaleq6Ve7"
REAL_FILE_SAMPLE = 1200          # 実データは間引いて読む（全件は1万件を超える）


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "mod_llm_prompt_replace", MOD_PATH, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_neighbour(suffix):
    """他の mod を番号を除いた名前で読む（`105_` の関数を借りるため）。"""
    folder, path = find_mod(suffix)
    spec = importlib.util.spec_from_file_location(
        "neighbour" + suffix, path, submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_mod()

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("{} {}{}".format("ok  " if ok else "FAIL", name,
                           "" if ok else "  <- " + str(detail)))


# --------------------------------------------------------------------------
# 偽のゲーム側
# --------------------------------------------------------------------------
class FakeClient(object):
    """`LlamaCppClient` の3つの地点だけを持つ偽物。

    本物と同じ入れ子（`chat` → `_apply_chat_template` → `_post_...`）にしてある。
    「1回の推論で置換は1回だけ」を確かめるにはこの入れ子が要る。
    """

    def __init__(self):
        self.sent = []          # 実際に送られた（＝一番内側に届いた）文章

    def chat(self, model, messages, format=None):
        prompt = self._apply_chat_template(model, messages)
        return self._post_with_model_loading_retry("/completion",
                                                   {"prompt": prompt, "json_schema": {}})

    def _apply_chat_template(self, model, messages, timeout=None):
        return "\n".join(m.get("content") or "" for m in messages)

    def _post_with_model_loading_retry(self, url, payload, timeout=None):
        self.sent.append(payload["prompt"])
        return {"content": "ok"}


class FakeLlmManager(object):
    """`llm_manager` が持つ `send_request*` の別名の偽物。

    本物はモジュール関数なので **self が無い**。インスタンス属性の関数として
    持たせて、`FakeCtx.wrap` もこちらには self を差し込まない。
    `__module__` は from-import 元（＝送信モジュール）の名前になるので、
    偽物にもそれを付ける。MOD はここから site（プロバイダ名）を採る。
    """

    BACKEND = "scripts.llm.request_llm_inference_gemini_test_streaming"

    def __init__(self, backend=None):
        self.sent = []          # 実際に送られた本文（リストのリスト）

        def send_request(manager_name, message, structure,
                         model=None, max_tokens=30000, timeout=None):
            self.sent.append([m.get("content") or "" for m in message])
            return {"content": "ok"}

        def send_request_with_no_structure(manager_name, message,
                                           model=None, max_tokens=30000,
                                           timeout=30):
            self.sent.append([m.get("content") or "" for m in message])
            return "ok"

        send_request.__module__ = backend or self.BACKEND
        send_request_with_no_structure.__module__ = backend or self.BACKEND
        self.send_request = send_request
        self.send_request_with_no_structure = send_request_with_no_structure


class FakeCtx(object):
    """`apply(ctx)` が使うぶんだけの ctx。`wrap` は偽クライアントに当てる。

    `mod_dir` は**本物の MOD フォルダを指さない**。ルールは MOD フォルダの中の
    `llm_replacements.txt` だけを読むので、本物を渡すと同梱のルールで動いてしまい、
    テストがその中身に左右される（同梱ルールは `test_real_prompts` で当てる）。
    """

    def __init__(self, client, out_dir, mod_dir=None):
        self.client = client
        self.manager = FakeLlmManager()     # llm_manager の別名（クラウド経路）
        self.out_dir = out_dir
        self.runtime_dir = os.path.join(out_dir, "runtime")
        self.mod_dir = mod_dir or os.path.join(out_dir, "mod")
        self.game_dir = os.path.join(out_dir, "game")
        self.api = 1
        self.lines = []
        self.errors = []

    # -- ローダの API --------------------------------------------------------
    def log(self, msg, level="INFO"):
        self.lines.append("{} {}".format(level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def wrap(self, target, required=True):
        name = target.partition(":")[2].rsplit(".", 1)[-1]

        # llm_manager の別名はモジュール関数なので self を差し込まず、
        # インスタンス（＝偽モジュール）の属性を入れ替える。
        if "llm_manager" in target:
            manager = self.manager

            def decorate_manager(fn):
                original = getattr(manager, name)

                def wrapper(*args, **kwargs):
                    return fn(original, *args, **kwargs)

                setattr(manager, name, wrapper)
                return fn
            return decorate_manager

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
            return self.manager, name, getattr(self.manager, name, None)
        return type(self.client), name, getattr(type(self.client), name, None)

    def superseded(self):
        return False              # テスト中に注入し直しは起きない


def revert_client():
    """`wrap` で差し替えたメソッドを素に戻す（テスト間で層を積まないため）。"""
    for name in ("chat", "_apply_chat_template", "_post_with_model_loading_retry"):
        setattr(FakeClient, name, getattr(_PRISTINE, name))


class _Pristine(object):
    pass


_PRISTINE = _Pristine()
for _name in ("chat", "_apply_chat_template", "_post_with_model_loading_retry"):
    setattr(_PRISTINE, _name, FakeClient.__dict__[_name])


def arm(rules_text, out_dir, roll=None, settings=None):
    """偽 MOD フォルダにルールを置いて `apply(ctx)` を通す。

    `(client, ctx, rules_path)` を返す。`roll` を渡すと抽選を固定する。
    """
    revert_client()
    mod.LOG_REPLACE = True
    mod.LOG_RULES = True
    for name, value in (settings or {}).items():
        setattr(mod, name, value)
    if roll is not None:
        mod._roll = roll

    # 既定側（`llm_replacements.default.txt`）に置く。利用者のファイルが優先される
    # ことは `test_self_contained` で別に見る。
    mod_dir = os.path.join(out_dir, "mod")
    os.makedirs(mod_dir, exist_ok=True)
    rules_path = os.path.join(mod_dir, "llm_replacements.default.txt")
    if rules_text is None:
        if os.path.isfile(rules_path):
            os.remove(rules_path)
    else:
        with io.open(rules_path, "w", encoding="utf-8") as fh:
            fh.write(rules_text)
    client = FakeClient()
    ctx = FakeCtx(client, out_dir)
    mod.apply(ctx)
    if any("VERIFY FAILED" in line for line in ctx.lines):
        check("自己検証: apply() の中の _verify が通る", False,
              [line for line in ctx.lines if "VERIFY" in line])
    return client, ctx, rules_path


def read_log(out_dir):
    path = os.path.join(out_dir, "prompt_bloat.log")
    if not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def reset_settings():
    mod.LOG_REPLACE = True
    mod.LOG_RULES = True
    mod._roll = _REAL_ROLL


_REAL_ROLL = mod._roll


# --------------------------------------------------------------------------
# 1. 書式
# --------------------------------------------------------------------------
def test_format():
    lines = [
        "\ufeff# コメント",
        "",
        "#tab:標準",
        "#off:切った行=>効かない",
        "  古い=>新しい  ",
        "確率つき=>替わった=>60",
        "確率0=>替わらない=>0",
        "確率のふり=>替わった=>200",          # 0-100 の外＝置換後の一部として扱う
        "#offtab:切ったタブ",
        "無効タブの中=>効かない",
        "#tab:また有効",
        "戻った=>効く",
        "壊れた行",
    ]
    rules, warnings = mod.parse_rules(lines)
    froms = [rule.from_text for rule in rules]
    check("書式: 有効な行だけ読む",
          froms == ["古い", "確率つき", "確率0", "確率のふり", "戻った"], froms)
    check("書式: 前後の空白を落とす", rules[0].to_text == "新しい", rules[0].to_text)
    check("書式: 確率を読む",
          [rule.prob for rule in rules] == [100, 60, 0, 100, 100],
          [rule.prob for rule in rules])
    check("書式: 0-100 の外は置換後の一部",
          rules[3].to_text == "替わった=>200", rules[3].to_text)
    check("書式: 壊れた行は警告して捨てる",
          len(warnings) == 1 and "壊れた行" in warnings[0], warnings)


def test_decode():
    rules, warnings = mod.parse_rules([
        "改行=>1行目\\n2行目",
        "\\u3042い=>う\\u3048",
        "本物の円記号=>C:\\\\Users",
        "知らない逃げ=>そのまま\\d+",
    ])
    by_from = {rule.from_text: rule for rule in rules}
    check("復号: 置換後の \\n は改行になる",
          by_from["改行"].to_text == "1行目\n2行目", repr(by_from["改行"].to_text))
    check("復号: 置換前は素の形と復号した形の両方を登録",
          "\\u3042い" in by_from and "あい" in by_from, sorted(by_from))
    check("復号: 復号した側の置換後も復号済み",
          by_from["あい"].to_text == "うえ", by_from["あい"].to_text)
    check("復号: \\\\ は本物の円記号1つ",
          by_from["本物の円記号"].to_text == "C:\\Users",
          by_from["本物の円記号"].to_text)
    check("復号: 知らないエスケープはそのまま残す",
          by_from["知らない逃げ"].to_text == "そのまま\\d+",
          by_from["知らない逃げ"].to_text)
    check("復号: 警告は出ない", not warnings, warnings)

    # `unescape` を単体でも通す（代理対＝ensure_ascii が絵文字に使う形）。
    check("復号: 代理対を1文字に戻す",
          mod.unescape("\\ud83d\\ude00") == "\U0001F600",
          repr(mod.unescape("\\ud83d\\ude00")))


def test_regex():
    rules, warnings = mod.parse_rules([
        "regex:(残り: -?\\d+)  - 効果:=>$1\\n  - 効果:",
        "regex:(?P<名>[A-Z]+)さん=>${名}様",
        "regex:(あ)(い)=>$2$1",
        "regex:値=>$$100",
        "regex:(だけ)=>$&$&",
        "regex:(1つだけ)=>$3 は無い",
        "regex:(壊れ=>閉じ括弧が無い",
    ])
    check("正規表現: 不正なパターンは警告して捨てる",
          len(rules) == 6 and any("不正な正規表現" in w for w in warnings), warnings)
    check("正規表現: 存在しない後方参照を警告する",
          any("後方参照" in w for w in warnings), warnings)

    def apply_one(rule, text):
        got, _count = rule.replace(text)
        return got

    check("正規表現: $1 と \\n",
          apply_one(rules[0], "残り: -1  - 効果: なし") == "残り: -1\n  - 効果: なし",
          repr(apply_one(rules[0], "残り: -1  - 効果: なし")))
    check("正規表現: ${名前}", apply_one(rules[1], "ABさん") == "AB様",
          apply_one(rules[1], "ABさん"))
    check("正規表現: 番号の入れ替え", apply_one(rules[2], "あい") == "いあ",
          apply_one(rules[2], "あい"))
    check("正規表現: $$ は $ 1つ", apply_one(rules[3], "値") == "$100",
          apply_one(rules[3], "値"))
    check("正規表現: $& は一致全体", apply_one(rules[4], "だけ") == "だけだけ",
          apply_one(rules[4], "だけ"))
    check("正規表現: 無い番号は文字として出す",
          apply_one(rules[5], "1つだけ") == "$3 は無い",
          apply_one(rules[5], "1つだけ"))


# --------------------------------------------------------------------------
# 2. 確率
# --------------------------------------------------------------------------
def test_probability():
    rules, _warnings = mod.parse_rules([
        "同じ前=>Aへ=>60",
        "同じ前=>Bへ=>60",
        "別の前=>Cへ=>30",
    ])
    groups = mod.group_rules(rules)
    check("確率: 同じ置換前は1グループ",
          [len(g) for g in groups] == [2, 1], [len(g) for g in groups])

    picked = []
    for value in (0, 59, 60, 119):
        chosen, _skipped = mod.decide(groups, "同じ前", roll=lambda d, v=value: v)
        text, _hits = mod.apply_chosen("同じ前", chosen)
        picked.append(text)
    check("確率: 合計120なら必ずどちらかに置換",
          picked == ["Aへ", "Aへ", "Bへ", "Bへ"], picked)

    # 合計が 100 以下のグループは「置換しない」枝がある（分母は 100）。
    chosen, skipped = mod.decide(groups, "別の前", roll=lambda d: 29)
    check("確率: 30%が当たると置換", len(chosen) == 1 and chosen[0][1] == 100, chosen)
    chosen, skipped = mod.decide(groups, "別の前", roll=lambda d: 30)
    check("確率: 外れると置換しない",
          not chosen and len(skipped) == 1 and "確率判定" in skipped[0][3],
          (chosen, skipped))

    zero, _warnings = mod.parse_rules(["出ない=>出た=>0"])
    chosen, skipped = mod.decide(mod.group_rules(zero), "出ない", roll=lambda d: 0)
    check("確率: 0% は抽選せず見送る",
          not chosen and "確率0" in skipped[0][3], (chosen, skipped))

    # 抽選はグループごとに1回。当たった置換前が本文に2箇所あっても両方替わる。
    one, _warnings = mod.parse_rules(["猫=>犬=>50"])
    chosen, _skipped = mod.decide(mod.group_rules(one), "猫と猫", roll=lambda d: 0)
    text, hits = mod.apply_chosen("猫と猫", chosen)
    check("確率: 当たったら本文の全箇所を替える",
          text == "犬と犬" and hits[0][1] == 2, (text, hits))


# --------------------------------------------------------------------------
# 3. 経路（実際に wrap して偽クライアントを呼ぶ）
# --------------------------------------------------------------------------
RULES = "\n".join([
    "#tab:試験",
    "古い言い方=>新しい言い方",
    "半分=>替わった=>50",
])


def test_paths(tmp):
    out_dir = os.path.join(tmp, "paths")
    os.makedirs(out_dir, exist_ok=True)
    client, ctx, _path = arm(RULES, out_dir, roll=lambda d: 0)

    client.chat("model", [{"role": "system", "content": "古い言い方をする"},
                          {"role": "user", "content": "半分の話"}])
    check("経路: chat で messages が替わる",
          client.sent == ["新しい言い方をする\n替わったの話"], client.sent)
    check("経路: 例外を握り潰していない", not ctx.errors, ctx.errors)
    log = read_log(out_dir)
    check("経路: [REPLACE] が site 付きで残る",
          log.count("[REPLACE] chat") == 2, log)

    # `chat` を通らない経路（テンプレート適用と payload を直接呼ぶ）。
    client2, _ctx2, _path = arm(RULES, out_dir, roll=lambda d: 0)
    prompt = client2._apply_chat_template("model", [{"role": "user", "content": "古い言い方"}])
    check("経路: _apply_chat_template 単体でも替わる", prompt == "新しい言い方", prompt)

    client3, _ctx3, _path = arm(RULES, out_dir, roll=lambda d: 0)
    client3._post_with_model_loading_retry("/completion", {"prompt": "古い言い方"})
    check("経路: payload 単体でも替わる", client3.sent == ["新しい言い方"], client3.sent)


def test_cloud_path(tmp):
    """外部APIキー経路でも置換されること。

    クラウドは LlamaCppClient を通らず、送信モジュールもプロバイダごとに違う
    （v1 で素通りの報告 → v3 の Gemini 実測を経て、v4 からプロバイダに依存しない
    `llm_manager:send_request*` の別名を包む）。境界で見えるのは第2引数の
    message だけ。site はラップした元関数の `__module__` から採る。
    """
    out_dir = os.path.join(tmp, "cloud")
    os.makedirs(out_dir, exist_ok=True)

    _client, ctx, _path = arm(RULES, out_dir, roll=lambda d: 0)
    ctx.manager.send_request("quest_referee",
                             [{"role": "user", "content": "古い言い方をする"}],
                             object())
    check("クラウド: send_request の message が替わる",
          ctx.manager.sent == [["新しい言い方をする"]], ctx.manager.sent)

    ctx.manager.send_request_with_no_structure(
        "narrator", [{"role": "system", "content": "半分の話"},
                     {"role": "user", "content": "古い言い方"}])
    check("クラウド: send_request_with_no_structure でも替わる",
          ctx.manager.sent[-1] == ["替わったの話", "新しい言い方"],
          ctx.manager.sent[-1])
    check("クラウド: 例外を握り潰していない", not ctx.errors, ctx.errors)

    # site は送信モジュール名から request_llm_inference_ を落としたもの。
    log = read_log(out_dir)
    check("クラウド: [REPLACE] がプロバイダ名の site 付きで残る",
          "[REPLACE] gemini_test_streaming " in log
          and "[REPLACE] gemini_test_streaming_ns " in log, log)

    # message= のキーワード渡しでも同じ（未実測のモジュールに備えて、位置に
    # 決め打ちしていないことを固定する）。
    _client2, ctx2, _path = arm(RULES, out_dir, roll=lambda d: 0)
    ctx2.manager.send_request("quest_referee",
                              message=[{"role": "user", "content": "古い言い方"}],
                              structure=object())
    check("クラウド: message= のキーワード渡しでも替わる",
          ctx2.manager.sent == [["新しい言い方"]], ctx2.manager.sent)

    # 抽選は1回だけ（確率付きルールの分母が経路の数で増えないこと）。
    draws = []

    def counting_roll(denom):
        draws.append(denom)
        return 0

    _client3, ctx3, _path = arm("#tab:t\n半分=>替わった=>50\n", out_dir,
                                roll=counting_roll)
    ctx3.manager.send_request("m", [{"role": "user", "content": "半分の話"}],
                              object())
    check("クラウド: 抽選は1回", len(draws) == 1, draws)
    check("クラウド: 置換は1度だけ当たる",
          ctx3.manager.sent == [["替わったの話"]], ctx3.manager.sent)

    # ローカル実行（llama.cpp の送信モジュールが import されている）では、この
    # 地点では何もしない。send_request は内部で別スレッドに降りるため印が届かず、
    # LlamaCppClient 側の3点と二重に抽選してしまうから。
    del draws[:]
    _client4, ctx4, _path = arm("#tab:t\n半分=>替わった=>50\n", out_dir,
                                roll=counting_roll)
    sys.modules[ml_llm.LOCAL_REQUEST_MODULE] = object()      # 印だけ。中身は見ない
    try:
        ctx4.manager.send_request("m", [{"role": "user", "content": "半分の話"}],
                                  object())
    finally:
        del sys.modules[ml_llm.LOCAL_REQUEST_MODULE]
    check("クラウド: ローカル実行ではこの地点で抽選しない", not draws, draws)
    check("クラウド: ローカル実行ではこの地点で置換しない",
          ctx4.manager.sent == [["半分の話"]], ctx4.manager.sent)


def test_alias_appears_late(tmp):
    """`llm_manager` の別名が注入の後から生えても包まれること。

    起動直後の注入では llm_manager に send_request がまだ無い（プロバイダの
    初期化時に生える）。無かったぶんは
    見張りが5秒ごとに（テストでは短縮）当て直す。
    """
    out_dir = os.path.join(tmp, "late")
    mod_dir = os.path.join(out_dir, "mod")
    os.makedirs(mod_dir, exist_ok=True)
    with io.open(os.path.join(mod_dir, "llm_replacements.default.txt"),
                 "w", encoding="utf-8") as fh:
        fh.write(RULES)

    revert_client()
    mod.LOG_REPLACE = True
    mod.LOG_RULES = True
    mod._roll = lambda denom: 0
    old_poll = ml_llm.ALIAS_POLL_SECONDS
    ml_llm.ALIAS_POLL_SECONDS = 0.02
    try:
        ctx = FakeCtx(FakeClient(), out_dir)
        send = ctx.manager.send_request
        send_ns = ctx.manager.send_request_with_no_structure
        del ctx.manager.send_request
        del ctx.manager.send_request_with_no_structure

        mod.apply(ctx)
        check("後生え: 無い間は属性を作らない",
              not hasattr(ctx.manager, "send_request"), vars(ctx.manager).keys())

        # プロバイダの初期化に相当。生えたら見張りが包む。
        ctx.manager.send_request = send
        ctx.manager.send_request_with_no_structure = send_ns
        # 15 秒は「壊れたときに止まらない」ためだけの上限。見張りは 0.02 秒
        # ごとに回るので通る回は一瞬で抜ける（VERIFICATION.md §4「CI だけで
        # 落ちるもの」）。
        deadline = time.time() + 15.0
        while time.time() < deadline and (
                ctx.manager.send_request is send
                or ctx.manager.send_request_with_no_structure is send_ns):
            time.sleep(0.01)
        check("後生え: 生えたら両方とも包まれる",
              ctx.manager.send_request is not send
              and ctx.manager.send_request_with_no_structure is not send_ns,
              "watcher did not arm in time")

        ctx.manager.send_request(
            "m", [{"role": "user", "content": "古い言い方"}], object())
        check("後生え: 置換が効く",
              ctx.manager.sent == [["新しい言い方"]], ctx.manager.sent)
        check("後生え: 例外を握り潰していない", not ctx.errors, ctx.errors)
    finally:
        ml_llm.ALIAS_POLL_SECONDS = old_poll
        reset_settings()


def test_single_pass(tmp):
    """入れ子の経路で抽選が2回起きないこと。

    `chat` → `_apply_chat_template` → `_post_...` と3段とも仕掛かっているので、
    歯止めが無ければ 50% のルールが3回抽選される。抽選の回数をそのまま数える。
    """
    out_dir = os.path.join(tmp, "single")
    os.makedirs(out_dir, exist_ok=True)
    draws = []

    def counting_roll(denom):
        draws.append(denom)
        return 0

    client, ctx, _path = arm("#tab:t\n半分=>替わった=>50\n", out_dir, roll=counting_roll)
    client.chat("model", [{"role": "user", "content": "半分の話"}])
    check("1回だけ: 入れ子の3経路で抽選は1回", len(draws) == 1, draws)
    check("1回だけ: 置換は1度だけ当たる", client.sent == ["替わったの話"], client.sent)

    # 同じ文章をもう一度渡すと、こちらが作ったものとして素通しになる
    # （印が届かない経路＝別スレッドで送られた場合の歯止め）。
    del draws[:]
    done = []

    def send():
        done.append(client._apply_chat_template(
            "model", [{"role": "user", "content": "替わったの話"}]))

    thread = threading.Thread(target=send)
    thread.start()
    thread.join()
    check("1回だけ: 自分が作った文章は別スレッドでも触らない",
          not draws and done == ["替わったの話"], (draws, done))

    # 印は呼び出しの後に必ず降りる（次のリクエストが素通しにならないこと）。
    del draws[:]
    client.chat("model", [{"role": "user", "content": "半分の話"}])
    check("1回だけ: 次のリクエストでは抽選し直す", len(draws) == 1, draws)


# --------------------------------------------------------------------------
# 4. 置き場所と再読込
# --------------------------------------------------------------------------
def test_self_contained(tmp):
    """読むのは **MOD フォルダの中の1ファイルだけ**。探索も外部参照もしない。

    以前は `settings\\` と既存プロキシの置き場所も探していた。MOD 単体の部品は
    MOD のフォルダで完結させる決まりにしたので（外に出るのは `out\\` のログだけ）、
    その両方を見ないことをここで固定する。
    """
    mod_dir = os.path.join(tmp, "contained", "mod")
    os.makedirs(mod_dir, exist_ok=True)
    default_path = os.path.join(mod_dir, "llm_replacements.default.txt")
    user_path = os.path.join(mod_dir, "llm_replacements.txt")
    check("置き場所: 利用者のファイルが無ければ同梱の既定",
          mod.rules_path(mod_dir) == default_path, mod.rules_path(mod_dir))
    with io.open(user_path, "w", encoding="utf-8") as fh:
        fh.write("#tab:t\n中=>利用者のファイルが効いた\n")
    check("置き場所: 利用者のファイルがあればそちら",
          mod.rules_path(mod_dir) == user_path, mod.rules_path(mod_dir))
    os.remove(user_path)
    check("置き場所: mod_dir が無ければ None（apply() の外）",
          mod.rules_path(None) is None, mod.rules_path(None))

    # 外に紛らわしいものを置いても読まない（配布フォルダの settings\ と、
    # プロキシ風のフォルダ・目印）。
    out_dir = os.path.join(tmp, "contained")
    for directory, text in (
            (os.path.join(out_dir, "settings"), "#tab:t\n外=>settings が効いた\n"),
            (os.path.join(out_dir, "InstantaleLlmProxy"), "#tab:t\n外=>プロキシが効いた\n"),
            (os.path.join(tmp, "InstantaleLlmProxy"), "#tab:t\n外=>親のプロキシが効いた\n")):
        os.makedirs(directory, exist_ok=True)
        with io.open(os.path.join(directory, "llm_replacements.txt"),
                     "w", encoding="utf-8") as fh:
            fh.write(text)
    with io.open(os.path.join(out_dir, "llm_proxy_dir.txt"), "w", encoding="utf-8") as fh:
        fh.write(os.path.join(tmp, "InstantaleLlmProxy"))

    # 外のルールはどれも「外」を置換する。効いていなければ「外」が残る。
    client, ctx, _path = arm("#tab:t\n中=>MOD の中が効いた\n", out_dir, roll=lambda d: 0)
    client.chat("model", [{"role": "user", "content": "中と外"}])
    check("置き場所: MOD の中のルールが効く",
          client.sent == ["MOD の中が効いたと外"], client.sent)
    check("置き場所: 外部（settings\\・プロキシ）は参照しない",
          "が効いた" not in client.sent[-1].replace("MOD の中が効いた", ""),
          client.sent)

    # 利用者のファイルを後から置くと、次のリクエストでそちらに切り替わる
    # （MOD を更新しても残るのはこちら側。`.default.txt` は上書きされる）。
    user_file = os.path.join(out_dir, "mod", "llm_replacements.txt")
    with io.open(user_file, "w", encoding="utf-8") as fh:
        fh.write("#tab:t\n中=>利用者のファイルが効いた\n")
    client.chat("model", [{"role": "user", "content": "中と外"}])
    check("置き場所: 利用者のファイルを置くと次のリクエストで切り替わる",
          client.sent[-1] == "利用者のファイルが効いたと外", client.sent)
    log = read_log(out_dir)
    check("置き場所: 切り替えを [RULES] に残す",
          "[RULES] 読む先を切り替えた" in log, log)

    # 消せば既定に戻る。
    os.remove(user_file)
    client.chat("model", [{"role": "user", "content": "中と外"}])
    check("置き場所: 利用者のファイルを消すと既定に戻る",
          client.sent[-1] == "MOD の中が効いたと外", client.sent)

    # 設定に場所も流用の切り替えも残っていないこと（宣言を消したので、
    # `mod_settings.json` に古い値が残っていても効かない）。
    for gone in ("RULES_PATH", "USE_PROXY_RULES"):
        check("置き場所: 設定 {} は廃止".format(gone), not hasattr(mod, gone),
              getattr(mod, gone, None))


def test_not_shipped():
    """**利用者のルールは配布物に入らない。** これが「更新で消えない」の根拠。

    `make_dist.bat` が `llm_replacements.txt` を除外していることを見る（配布物に
    入ってしまうと、次の利用者の更新でその人のルールを上書きしてしまう）。
    """
    with io.open(os.path.join(ROOT, "make_dist.bat"), encoding="utf-8",
                 errors="replace") as fh:
        script = fh.read()
    check("配布: make_dist.bat が llm_replacements.txt を除外している",
          '"llm_replacements.txt"' in script and "/XF" in script, None)
    check("配布: 同梱される既定は .default.txt",
          os.path.isfile(os.path.join(MOD_DIR, "llm_replacements.default.txt")), None)


def test_bundled_rules():
    """同梱の既定（`llm_replacements.default.txt`）が警告なしで読めること。

    ルールを書き換えたときにここが落ちる（＝書式を間違えた）。利用者のファイルを
    置いている環境ではそちらを読む（`rules_path` と同じ判定）。
    """
    path = mod.rules_path(MOD_DIR)
    if not os.path.isfile(path):
        print("skip 同梱: ルールファイルが無い")
        return
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        rules, warnings = mod.parse_rules(fh.read().splitlines())
    groups = mod.group_rules(rules)
    check("同梱: 警告なしで読める（{}行 / {}グループ）".format(len(rules), len(groups)),
          not warnings, warnings)
    # 当ててみる。当たらないルールがあってもよいが、**例外は許さない**。
    sample = "\n".join(rule.from_text for rule in rules)
    chosen, _skipped = mod.decide(groups, sample, roll=lambda d: 0)
    text, hits = mod.apply_chosen(sample, chosen)
    check("同梱: 当てても落ちない（{}箇所）".format(len(hits)),
          isinstance(text, str) and (not rules or hits), len(hits))


def test_real_prompts():
    """記録済みの実プロンプトに、同梱のルールをそのまま当ててみる。

    見るのは2つ。**当たること**（書式の取り違えがあれば1件も当たらない）と、
    **`105_` の後段が壊れないこと** ― この MOD は `105_` より外側に居るので、
    置換した本文をあちらのスキーマ解析が読むことになる（`mod.json` の `after`）。
    `output_data/` が無い環境では飛ばす。
    """
    found = mod.rules_path(MOD_DIR)
    files = sorted(glob.glob(os.path.join(
        GAME_DIR, "output_data", "*", "*", "*", "*.json")))
    if not os.path.isfile(found) or not files:
        print("skip 実データ: ルールファイルか output_data/ が無い")
        return
    files = files[::max(1, len(files) // REAL_FILE_SAMPLE)]

    with io.open(found, encoding="utf-8", errors="replace") as fh:
        rules, _warnings = mod.parse_rules(fh.read().splitlines())
    groups = mod.group_rules(rules)
    compact = load_neighbour("_fix_schema_compact")

    fired = {}
    schema_messages = 0
    still_compacts = 0
    broken = []
    for path in files:
        try:
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        messages = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
        texts = [m.get("content") for m in messages if isinstance(m.get("content"), str)]
        if not texts:
            continue
        # 確率は 0 を返して先頭を選ぶ（当たりうるルールを全部通したいので）。
        chosen, _skipped = mod.decide(groups, "\n".join(texts), roll=lambda d: 0)
        for text in texts:
            replaced, hits = mod.apply_chosen(text, chosen)
            for rule, count, _denom in hits:
                fired[rule.disp_from] = fired.get(rule.disp_from, 0) + count
            if compact.find_schema_start(text) < 0:
                continue
            schema_messages += 1
            if compact.compact_embedded_schema(text) is None:
                continue                      # 素でも圧縮できない形。ここでは問わない
            if compact.compact_embedded_schema(replaced) is None:
                broken.append(os.path.basename(os.path.dirname(path)))
            else:
                still_compacts += 1

    check("実データ: 実在のルールが当たる（{}種 / {}ファイル）".format(
        len(fired), len(files)), len(fired) >= 1, sorted(fired)[:3])
    check("実データ: 置換後も 105_ がスキーマを圧縮できる（{}件）".format(schema_messages),
          schema_messages and not broken, broken[:5])
    check("実データ: 圧縮できた件数が減らない",
          still_compacts == schema_messages, (still_compacts, schema_messages))


def test_reload(tmp):
    out_dir = os.path.join(tmp, "reload")
    os.makedirs(out_dir, exist_ok=True)
    client, ctx, path = arm("#tab:t\n前=>後\n", out_dir, roll=lambda d: 0)
    client.chat("model", [{"role": "user", "content": "前の話"}])
    first = client.sent[-1]

    # ルールを書き換える。ゲームを再起動せずに次のリクエストから効くこと。
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("#tab:t\n前=>もっと後\n")
    os.utime(path, (os.path.getmtime(path) + 2, os.path.getmtime(path) + 2))
    client.chat("model", [{"role": "user", "content": "前の話"}])
    check("再読込: 保存すると次のリクエストから効く",
          first == "後の話" and client.sent[-1] == "もっと後の話", client.sent)

    # ファイルを消すと置換を止める（前のルールを握り続けない）。
    os.remove(path)
    client.chat("model", [{"role": "user", "content": "前の話"}])
    check("再読込: 消えたら置換を止める", client.sent[-1] == "前の話", client.sent)

    # 後から置き直しても拾う（場所は固定なので探索は要らない）。
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("#tab:t\n前=>また後\n")
    client.chat("model", [{"role": "user", "content": "前の話"}])
    check("再読込: 置き直せば次のリクエストから効く",
          client.sent[-1] == "また後の話", client.sent)


def test_harmless(tmp):
    out_dir = os.path.join(tmp, "harmless")
    os.makedirs(out_dir, exist_ok=True)

    # ルールが1つも無い（全部コメント）。
    client, ctx, _path = arm("# なにも無い\n#tab:空\n", out_dir)
    client.chat("model", [{"role": "user", "content": "そのまま"}])
    check("壊さない: ルールが無ければ素通し",
          client.sent == ["そのまま"] and not ctx.errors, (client.sent, ctx.errors))

    # 置換の途中で例外が出ても文章はそのまま送り、記録に残す。
    client, ctx, _path = arm("#tab:t\n前=>後\n", out_dir, roll=lambda d: 0)
    broken = mod.RuleFile.current

    def explode(self):
        raise RuntimeError("試験用")

    mod.RuleFile.current = explode
    try:
        client.chat("model", [{"role": "user", "content": "前の話"}])
    finally:
        mod.RuleFile.current = broken
    check("壊さない: 置換が失敗しても送る",
          client.sent[-1] == "前の話" and len(ctx.errors) == 1,
          (client.sent, ctx.errors))

    # 本文が str でないメッセージ・dict でないメッセージに触らない。
    client, ctx, _path = arm("#tab:t\n前=>後\n", out_dir, roll=lambda d: 0)
    messages = [{"role": "user", "content": None}, {"role": "user"},
                {"role": "user", "content": "前の話"}]
    result = client._apply_chat_template("model", messages)
    check("壊さない: content が無いものに触らない", "後の話" in result, result)
    check("壊さない: 元の messages を書き換えない",
          messages[2]["content"] == "前の話", messages[2])


def test_settings(tmp):
    out_dir = os.path.join(tmp, "settings_off")
    os.makedirs(out_dir, exist_ok=True)
    client, ctx, _path = arm("#tab:t\n前=>後\n", out_dir, roll=lambda d: 0,
                             settings={"LOG_REPLACE": False, "LOG_RULES": False})
    client.chat("model", [{"role": "user", "content": "前の話"}])
    log = read_log(out_dir)
    check("設定: 記録を切っても置換は効く", client.sent == ["後の話"], client.sent)
    check("設定: 切った記録は出ない",
          "[REPLACE]" not in log and "[RULES] 読込" not in log, log)
    reset_settings()

    # 宣言（mod.json）とコードの既定値がずれていないこと（check_mods.py と同じ判定）。
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        declared = json.load(fh)["settings"]
    fresh = load_mod()
    mismatch = [name for name, spec in declared.items()
                if getattr(fresh, name) != spec["default"]]
    check("設定: mod.json とコードの既定値が一致", not mismatch, mismatch)
    check("設定: 記録の ON/OFF だけを宣言している（場所の設定は持たない）",
          sorted(declared) == ["LOG_REPLACE", "LOG_RULES"], sorted(declared))


def main():
    tmp = tempfile.mkdtemp(prefix="instantale_replace_")
    try:
        test_format()
        test_decode()
        test_regex()
        test_probability()
        test_paths(tmp)
        test_cloud_path(tmp)
        test_alias_appears_late(tmp)
        test_single_pass(tmp)
        test_self_contained(tmp)
        test_not_shipped()
        test_bundled_rules()
        test_real_prompts()
        test_reload(tmp)
        test_harmless(tmp)
        test_settings(tmp)
    finally:
        revert_client()
        reset_settings()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok, _detail in RESULTS if not ok]
    print("\n{} / {} ok".format(len(RESULTS) - len(failed), len(RESULTS)))
    for name in failed:
        print("  FAILED: {}".format(name))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
