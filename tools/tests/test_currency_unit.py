# -*- coding: utf-8 -*-
"""`130_currency_unit` と、ローダの通貨の語彙をゲーム抜きで通す。

    python tools/tests/test_currency_unit.py

見ているのは5つ。

  語彙     … `ui.set_currency` / `ui.rewrite_coins` / `ui.parse_coin`。
             **何度通しても伸びない**こと（伸びる表記は受け取らず素へ据え置く）
  実文言   … 実機のビルドから読み出したゲームの文言（長い形・短い形・英語・
             埋める前のテンプレート）が狙いどおり変わり、`8GB` や `GUI` を
             巻き込まないこと
  tr       … `scripts.languages:tr` の**戻り値だけ**を直すこと。
             素の表記のままなら何も仕掛けないこと
  LLM      … ローカル（chat）でもクラウド（`llm_manager` の別名）でも、
             送る本文の中の通貨が直ること
  噛み合い … 差し替えた後のラベル（`馬車(1000円)`）から、
             `314_` / `315_` が使う `ui.parse_coin` が額を読めること

ゲームの文言は実機のビルドから読み出したもの（GAME.md §2.29）。
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
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import ui                     # noqa: E402


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


MOD_DIR, MOD_PATH = find_mod("_currency_unit")


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "currency_unit_test", MOD_PATH, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_mod()

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# --------------------------------------------------------------------------
# 実機のビルドから読み出したゲームの文言（GAME.md §2.29）
# --------------------------------------------------------------------------
GAME_TEXTS = (
    ("1000ゴールドを支払った。快適な旅だ...", "1000円を支払った。快適な旅だ..."),
    ("務め終えた。90ゴールドを稼いだ。この地に少し馴染んだ...",
     "務め終えた。90円を稼いだ。この地に少し馴染んだ..."),
    ("馬車(1000G)", "馬車(1000円)"),
    ("簡易寝台(10G)", "簡易寝台(10円)"),
    ("貴族権(1,000,000G)", "貴族権(1,000,000円)"),
    ("<行動>300Gを手に入れた。", "<行動>300円を手に入れた。"),
    ("雇う(5045G)", "雇う(5045円)"),
    ("傷薬を煎じてもらう({price.salve}G)", "傷薬を煎じてもらう({price.salve}円)"),
    # ゲームが LLM へ送る指示文。これも `tr` を通る。
    ("必ず前払いで5045ゴールドの雇用費を提示する。",
     "必ず前払いで5045円の雇用費を提示する。"),
    ("市民権を10000G, 貴族権を100000Gで購入できる。",
     "市民権を10000円, 貴族権を100000円で購入できる。"),
    # 英語表示。
    ("You paid 1000 gold.", "You paid 1000 円."),
    ("Doghouse (0G)", "Doghouse (0円)"),
    # 巻き込んではいけないもの。
    ("VRAM 8GB では足りない", "VRAM 8GB では足りない"),
    ("プレイヤーの GUI 上には表示されている", "プレイヤーの GUI 上には表示されている"),
    ("金の剣 / a golden sword", "金の剣 / a golden sword"),
)


def test_vocabulary():
    try:
        check("素の表記", ui.currency_names() == ("ゴールド", "G"),
              ui.currency_names())
        check("差し替え", ui.set_currency("円", "円") == ("円", "円"))

        for before, after in GAME_TEXTS:
            check("文言 " + before[:18], ui.rewrite_coins(before) == after,
                  repr(ui.rewrite_coins(before)))

        # 二度通しても伸びない（画面と LLM の両方の経路で当たるため）。
        twice = ui.rewrite_coins(ui.rewrite_coins("馬車(1000G)と1000ゴールド"))
        check("冪等", twice == "馬車(1000円)と1000円", repr(twice))

        # 文字列でない値はそのまま返す（`tr` には文字列以外も来る）。
        check("非文字列", ui.rewrite_coins(None) is None
              and ui.rewrite_coins(7) == 7)

        # 額を読む側。素の `G` も、差し替えた表記も読む。
        check("額を読む",
              ui.parse_coin("馬車(1,000円)") == 1000
              and ui.parse_coin("馬車(1000G)") == 1000
              and ui.parse_coin("徒歩(3ヵ月)") is None)

        # 伸びる表記は受け取らない（素へ据え置き、戻り値で分かる）。
        check("伸びる長い形を拒む",
              ui.set_currency("金ゴールド", "G") == ("ゴールド", "G"))
        check("伸びる短い形を拒む",
              ui.set_currency("ゴールド", "0G") == ("ゴールド", "G"))
        check("伸びる英語形を拒む",
              ui.set_currency("gold coin", "G") == ("ゴールド", "G"))

        # 空白だけ・文字列でない指定は「指定なし」。
        check("空の指定", ui.set_currency("   ", None) == ("ゴールド", "G"))
        check("数の指定", ui.set_currency(1, 2) == ("ゴールド", "G"))

        # 長い形だけを変えることもできる。
        check("片方だけ", ui.set_currency("シルバー", None) == ("シルバー", "G"))
        check("片方だけの結果",
              ui.rewrite_coins("馬車(1000G)と1000ゴールド")
              == "馬車(1000G)と1000シルバー")
    finally:
        ui.set_currency(None, None)


# --------------------------------------------------------------------------
# 偽のゲーム側（`test_crime_attribution.py` と同じ形）
# --------------------------------------------------------------------------
def plain_tr(text):
    """`scripts.languages:tr`。今の言語の文を返すだけの偽物。"""
    return text


class FakeClient(object):
    """`LlamaCppClient` の3つの地点だけを持つ偽物。本物と同じ入れ子。"""

    def __init__(self):
        self.sent = []

    def chat(self, model, messages, format=None):
        prompt = self._apply_chat_template(model, messages)
        return self._post_with_model_loading_retry(
            "/completion", {"prompt": prompt, "json_schema": {}})

    def _apply_chat_template(self, model, messages, timeout=None):
        return "\n".join(m.get("content") or "" for m in messages)

    def _post_with_model_loading_retry(self, url, payload, timeout=None):
        self.sent.append(payload["prompt"])
        return {"content": "ok"}


_PRISTINE = {name: FakeClient.__dict__[name] for name in
             ("chat", "_apply_chat_template", "_post_with_model_loading_retry")}


def revert_client():
    """`wrap` で差し替えたメソッドを素に戻す（テスト間で層を積まないため）。"""
    for name, func in _PRISTINE.items():
        setattr(FakeClient, name, func)


class FakeManager(object):
    """`llm_manager` の別名。本物はモジュール関数なので **self が無い**。"""

    BACKEND = "scripts.llm.request_llm_inference_gemini_test_streaming"

    def __init__(self):
        self.sent = []

        def send_request(manager_name, message, structure,
                         model=None, max_tokens=30000, timeout=None):
            self.sent.append([m.get("content") or "" for m in message])
            return {"content": "ok"}

        def send_request_with_no_structure(manager_name, message, model=None,
                                           max_tokens=30000, timeout=30):
            self.sent.append([m.get("content") or "" for m in message])
            return "ok"

        send_request.__module__ = self.BACKEND
        send_request_with_no_structure.__module__ = self.BACKEND
        self.send_request = send_request
        self.send_request_with_no_structure = send_request_with_no_structure


#: 実機で観測した能力欄の形（`206_probe_quest_flow` の 40 文字＋ビルド内の定数）。
STATUS_TEXT = ("Atk:432(+500)\nDef:0(+500)\nExp:1675/3237\n"
               "Gold:1116472\nAge:31\nSta:88/100\nLocation:ヴェスティア")


class FakeLabel(object):
    """能力欄を塗る `status_label`。この MOD が触るのは `text` だけ。"""

    def __init__(self):
        self.text = ""


class FakeHUD(object):
    """`InstanTaleHUD` の能力欄まわりだけ。

    `status_texts` は StringProperty なので、値を入れると見張り
    （`update_status_texts`）が呼ばれる。そこだけ真似ている。

    `USES_VALUE` は**ゲーム側の実装の分かれ目**。
    見張りが渡された `value` を塗るビルドと、
    `self.status_texts` を読み直して塗るビルドの両方を試すために置いてある。
    """

    USES_VALUE = True

    def __init__(self):
        self.status_label = FakeLabel()
        self.status_texts = ""

    def update_status_texts(self, instance, value):
        if type(self).USES_VALUE:
            self.status_label.text = value
        else:
            self.status_label.text = self.status_texts

    def set_status(self, text):
        """ゲームが `hud.status_texts = ...` を書いたときの流れ。"""
        self.status_texts = text
        self.update_status_texts(self, text)
        return self.status_label.text


_PRISTINE_HUD = FakeHUD.__dict__["update_status_texts"]


def revert_hud():
    """`wrap` で差し替えた見張りを素に戻す（テスト間で層を積まないため）。"""
    FakeHUD.update_status_texts = _PRISTINE_HUD
    FakeHUD.USES_VALUE = True


class FakeCtx(object):
    """`apply(ctx)` が使うぶんだけの ctx。"""

    def __init__(self, languages, client, manager, out_dir):
        self.languages = languages
        self.client = client
        self.manager = manager
        self.out_dir = out_dir
        self.mod_dir = os.path.join(out_dir, "mod")
        self.lines = []
        self.errors = []

    def log(self, msg, level="INFO"):
        self.lines.append("{} {}".format(level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、検査だけが別のログ処理を通ることになる。
    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    # 警告も本物の `ctx.warner` を借りる（1度しか出さない側の作りを検査に通すため）。
    def warner(self, tag):
        import instantale_modloader as _ml
        return _ml.ModContext.warner(self, tag)

    def _owner_of(self, target):
        """`(持ち主, メソッドか)` を返す。"""
        module = target.partition(":")[0]
        if module.endswith("languages"):
            return self.languages, False
        if "llm_manager" in target:
            return self.manager, False
        if module.endswith("new_hud"):
            return FakeHUD, True
        return type(self.client), True

    def wrap(self, target, required=True, safe=False):
        owner, is_method = self._owner_of(target)
        name = target.partition(":")[2].rsplit(".", 1)[-1]

        def decorate(fn):
            original = getattr(owner, name, None)
            if original is None:
                return fn                     # required=False と同じ扱い
            if is_method:
                def wrapper(client_self, *args, **kwargs):
                    return fn(original, client_self, *args, **kwargs)
            else:
                def wrapper(*args, **kwargs):
                    return fn(original, *args, **kwargs)
            setattr(owner, name, wrapper)
            return fn
        return decorate

    def resolve(self, target):
        owner, _is_method = self._owner_of(target)
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        return owner, name, getattr(owner, name, None)

    def superseded(self):
        return False


def arm(out_dir, long_name="円", short_name="円", prompts=True,
        hud_format=None, uses_value=True):
    """素の偽物に `apply()` を当てて `(languages, client, manager, ctx)` を返す。"""
    revert_client()
    revert_hud()
    FakeHUD.USES_VALUE = uses_value
    languages = types.SimpleNamespace(tr=plain_tr)
    client = FakeClient()
    manager = FakeManager()
    ctx = FakeCtx(languages, client, manager, out_dir)
    M.UNIT_LONG, M.UNIT_SHORT, M.REWRITE_PROMPTS = long_name, short_name, prompts
    M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD if hud_format is None else hud_format
    M.apply(ctx)
    return languages, client, manager, ctx


# --------------------------------------------------------------------------
# `tr` の口
# --------------------------------------------------------------------------
def test_tr(tmp):
    try:
        languages, _client, _manager, ctx = arm(os.path.join(tmp, "tr"))
        check("tr の戻り値", languages.tr("馬車(1000G)") == "馬車(1000円)",
              languages.tr("馬車(1000G)"))
        check("tr の指示文",
              languages.tr("必ず前払いで5045ゴールドの雇用費を提示する。")
              == "必ず前払いで5045円の雇用費を提示する。")
        check("tr は無関係な文を変えない", languages.tr("VRAM 8GB") == "VRAM 8GB")
        check("tr は文字列以外を通す", languages.tr(None) is None)
        check("例外を出さない", not ctx.errors, ctx.errors)

        log = io.open(os.path.join(tmp, "tr", M.LOG_BASENAME),
                      encoding="utf-8").read()
        check("置換を記録する", "馬車(1000G)" in log, log[:200])
    finally:
        ui.set_currency(None, None)


def test_left_alone(tmp):
    """素の表記のままなら何も仕掛けない（設定の既定値＝ゲームの値）。"""
    try:
        languages, _client, manager, ctx = arm(
            os.path.join(tmp, "plain"), long_name="ゴールド", short_name="G")
        check("素なら tr を包まない", languages.tr is plain_tr)
        check("素なら理由を残す",
              any("nothing installed" in line for line in ctx.lines), ctx.lines)
        manager.send_request("m", [{"role": "user", "content": "1000ゴールド"}],
                             object())
        check("素なら LLM も包まない", manager.sent == [["1000ゴールド"]],
              manager.sent)
    finally:
        ui.set_currency(None, None)


# --------------------------------------------------------------------------
# LLM の経路（`tr` を通らずに出ていく本文＝セーブに貯まった文）
# --------------------------------------------------------------------------
def test_local_path(tmp):
    try:
        _languages, client, _manager, ctx = arm(os.path.join(tmp, "local"))
        client.chat("m", [{"role": "system", "content": "手持ちは1116472Gある。"},
                          {"role": "user", "content": "宿代は10ゴールドか？"}], {})
        check("ローカル経路",
              len(client.sent) == 1 and "1116472円" in client.sent[0]
              and "10円" in client.sent[0], client.sent)
        check("例外を出さない", not ctx.errors, ctx.errors)
    finally:
        ui.set_currency(None, None)


def test_cloud_path(tmp):
    try:
        _languages, _client, manager, ctx = arm(os.path.join(tmp, "cloud"))
        manager.send_request(
            "m", [{"role": "system", "content": "報酬は90ゴールド。"}], object())
        check("クラウド経路", manager.sent[0][0] == "報酬は90円。", manager.sent)

        manager.send_request_with_no_structure(
            "m", [{"role": "user", "content": "馬車(1000G)"}])
        check("別名も同じ", manager.sent[1][0] == "馬車(1000円)", manager.sent)

        manager.send_request("m", [{"role": "user", "content": "こんにちは"}],
                             object())
        check("無関係な本文はそのまま", manager.sent[2] == ["こんにちは"],
              manager.sent)
        check("例外を出さない", not ctx.errors, ctx.errors)
    finally:
        ui.set_currency(None, None)


def test_prompts_off(tmp):
    """LLM 側を切ったら画面だけが変わること。"""
    try:
        languages, _client, manager, _ctx = arm(
            os.path.join(tmp, "off"), prompts=False)
        check("画面は変わる", languages.tr("馬車(1000G)") == "馬車(1000円)")
        manager.send_request("m", [{"role": "user", "content": "1000ゴールド"}],
                             object())
        check("LLM は素のまま", manager.sent == [["1000ゴールド"]], manager.sent)
    finally:
        ui.set_currency(None, None)


# --------------------------------------------------------------------------
# `314_` / `315_` との噛み合い
# --------------------------------------------------------------------------
def test_price_labels():
    """差し替えた後のラベルからも運賃・宿代が読めること。

    `314_` / `315_` は**画面のラベルから**素の額を読み取っている。
    表記を差し替えるとラベルが `馬車(1000円)` になるので、
    読む側（`ui.parse_coin`）が新しい表記を知っていないと素の額を見失う。
    """
    try:
        ui.set_currency("円", "円")
        for label, price in (("馬車(1000G)", 1000), ("馬車(1,000G)", 1000),
                             ("個室(100G)", 100), ("犬小屋(0G)", 0)):
            shown = ui.rewrite_coins(label)
            check("ラベル " + label,
                  ui.parse_coin(shown) == price and "G" not in shown,
                  "{!r} -> {!r}".format(shown, ui.parse_coin(shown)))
    finally:
        ui.set_currency(None, None)


# --------------------------------------------------------------------------
# 画面上部の所持金の欄
# --------------------------------------------------------------------------
def test_hud_format():
    """組み直しは純粋関数。ゲーム抜きで形だけ見る。"""
    try:
        ui.set_currency("円", "円")
        M.HUD_GOLD_FORMAT = "所持金:{money}{short}"
        fixed = M.restate(STATUS_TEXT)
        check("見出しの差し替え", "所持金:1,116,472円" in fixed, repr(fixed))
        check("素の見出しは消える", "Gold:" not in fixed, repr(fixed))
        check("他の欄はそのまま",
              "Atk:432(+500)" in fixed and "Age:31" in fixed
              and "Location:ヴェスティア" in fixed, repr(fixed))

        # 見出しの `Gold` は呼び名と同じもの。書式を触らなくても付け替わる。
        M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD
        check("見出しは呼び名に付いていく",
              "円:1116472" in M.restate(STATUS_TEXT), repr(M.restate(STATUS_TEXT)))
        check("書式の見出しも同じ",
              M.effective_template() == "円:{amount}", M.effective_template())
        check("自前の見出しは触らない",
              M.effective_template("所持金:{money}") == "所持金:{money}")
        M.HUD_GOLD_FORMAT = "所持金:{money}{short}"

        check("数字をそのまま使う",
              M.restate(STATUS_TEXT, "{amount}") == STATUS_TEXT.replace(
                  "Gold:1116472", "1116472"),
              repr(M.restate(STATUS_TEXT, "{amount}")))
        check("記号を前に置ける",
              "$1,116,472" in M.restate(STATUS_TEXT, "${money}"))
        check("長い表記",
              "1,116,472 円" in M.restate(STATUS_TEXT, "{money} {long}"))

        # 打ち間違いは画面に残す（`314_` / `315_` と同じ。黙って素へ落とさない）。
        check("打ち間違いは残る", "{typo}" in M.restate(STATUS_TEXT, "{typo}"))

        # 当たらないものは None（見張りはゲームのまま呼ばれる）。
        check("見出しが無い", M.restate("Atk:432 Def:0") is None)
        check("空", M.restate("") is None)
        check("非文字列", M.restate(None) is None and M.restate(7) is None)

        ui.set_currency(None, None)
        check("呼び名が素なら書式も素",
              M.effective_template("Gold:{amount}") == "Gold:{amount}")
        ui.set_currency("円", "円")

        # 数の読み方。ゲームが何を書くかは実機で確かめていないので3通り受ける。
        check("数を読む",
              M.number("1116472") == 1116472
              and M.number("1,116,472") == 1116472
              and M.number("1116472.0") == 1116472
              and M.number("?") == "?")
    finally:
        ui.set_currency(None, None)
        M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD


def test_hud_value_build(tmp):
    """見張りが渡された `value` を塗るビルド。"""
    try:
        _languages, _client, _manager, ctx = arm(
            os.path.join(tmp, "hud_value"), hud_format="所持金:{money}{short}",
            uses_value=True)
        hud = FakeHUD()
        shown = hud.set_status(STATUS_TEXT)
        check("value を塗るビルド", "所持金:1,116,472円" in shown
              and "Gold:" not in shown, repr(shown))
        check("例外を出さない", not ctx.errors, ctx.errors)

        log = io.open(os.path.join(tmp, "hud_value", M.LOG_BASENAME),
                      encoding="utf-8").read()
        check("見張りで効いたと残る", "hud:" in log, log[:200])
        check("保険は使われない", "hud repaint" not in log, log[:200])
    finally:
        revert_hud()
        ui.set_currency(None, None)
        M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD


def test_hud_reread_build(tmp):
    """見張りが `self.status_texts` を読み直すビルド。保険が効くこと。"""
    try:
        _languages, _client, _manager, ctx = arm(
            os.path.join(tmp, "hud_reread"), hud_format="所持金:{money}{short}",
            uses_value=False)
        hud = FakeHUD()
        hud.set_status(STATUS_TEXT)
        shown = hud.status_label.text
        check("読み直すビルドでも直る", "所持金:1,116,472円" in shown
              and "Gold:" not in shown, repr(shown))
        check("例外を出さない", not ctx.errors, ctx.errors)

        log = io.open(os.path.join(tmp, "hud_reread", M.LOG_BASENAME),
                      encoding="utf-8").read()
        check("保険で効いたと残る", "hud repaint" in log, log[:300])
    finally:
        revert_hud()
        ui.set_currency(None, None)
        M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD


def test_hud_follows_wording(tmp):
    """呼び名を変えたら、書式を触らなくても見出しが付いていくこと。

    **最初に踏んだ食い違い**（2026-08-26）。
    通貨を `シルバー` にしても画面上部だけ `Gold:` のまま残っていた。
    """
    try:
        _languages, _client, _manager, ctx = arm(
            os.path.join(tmp, "hud_follow"), long_name="シルバー",
            short_name="Slv")
        hud = FakeHUD()
        shown = hud.set_status(STATUS_TEXT)
        check("見出しが付いていく", "シルバー:1116472" in shown, repr(shown))
        check("素の見出しは残らない", "Gold:" not in shown, repr(shown))
        check("例外を出さない", not ctx.errors, ctx.errors)
    finally:
        revert_hud()
        ui.set_currency(None, None)
        M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD


def test_hud_only(tmp):
    """表示だけ変えることもできる（呼び名は素のまま）。"""
    try:
        languages, _client, manager, _ctx = arm(
            os.path.join(tmp, "hud_only"), long_name="ゴールド", short_name="G",
            hud_format="所持金:{money}")
        check("呼び名は素のまま", languages.tr("馬車(1000G)") == "馬車(1000G)")
        manager.send_request("m", [{"role": "user", "content": "1000ゴールド"}],
                             object())
        check("LLM も素のまま", manager.sent == [["1000ゴールド"]], manager.sent)

        hud = FakeHUD()
        shown = hud.set_status(STATUS_TEXT)
        check("表示だけ変わる",
              "所持金:1,116,472" in shown and "Gold:" not in shown, repr(shown))
    finally:
        revert_hud()
        ui.set_currency(None, None)
        M.HUD_GOLD_FORMAT = M.GAME_HUD_GOLD


def main():
    tmp = tempfile.mkdtemp(prefix="currency_unit_test_")
    try:
        print("vocabulary")
        test_vocabulary()
        print("tr")
        test_tr(tmp)
        test_left_alone(tmp)
        print("llm")
        test_local_path(tmp)
        test_cloud_path(tmp)
        test_prompts_off(tmp)
        print("price labels")
        test_price_labels()
        print("hud")
        test_hud_format()
        test_hud_value_build(tmp)
        test_hud_reread_build(tmp)
        test_hud_follows_wording(tmp)
        test_hud_only(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
