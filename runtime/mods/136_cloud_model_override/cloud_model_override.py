# -*- coding: utf-8 -*-
"""ゲームが知らないクラウドモデルを使う。**送信直前にモデル名を差し替える**。

OpenAI API と Claude API の両方に対応する。
差し替え先は設定画面の一覧から選ぶ（一覧に無い名前は「一覧に無いモデル」の欄に書く）。

## ファイルの分け方

    cloud_model_override.py   入口。設定の定数・ログ・apply()
    openai_side.py            OpenAI API 宛ての直し・キャッシュ・包み
    claude_side.py            Claude API 宛ての直し・キャッシュ・包み
    prefix_cache.py           キャッシュの印をどこに置くか（両方が使う）

設定の定数は入口に置く（ローダが値を書き込むのは入口のモジュールで、
`tools/check_mods.py` も入口の定数と `mod.json` を突き合わせる。TECH.md §3.8.3）。
宛先ごとのファイルは定数を読まず、`apply()` が値を引数で渡す。

設定の名前は API の名前（OpenAI API / Claude API）に合わせて `OPENAI_` / `CLAUDE_` で始める。
ゲームの設定画面のプロバイダ名も同じ「OpenAI API」「Claude API」。

## なぜ送信直前か

ゲームのクラウド対応は、
モデル名の一覧がコード定数として埋まっている（`scripts.hud.hud_option:OptionLLMScreen.get_cloud_llm_list` と
`hud_auto_configuration` に同じ表がある）。
一覧に無い名前は設定画面から選べない。
一覧に足すだけでも足りない（OpenAI はモデル名で API の経路を分けている。`openai_side.py`）。

そこで名前を足しに行かず、**ゲームには一覧にある名前のまま持たせ、
HTTP に出る直前で差し替える**。
ゲームは一覧のモデルのつもりで要求を組み立て、実際に飛ぶのは選んだモデル。

## どこに仕掛けるか

    openai._base_client:SyncAPIClient.post
    anthropic._base_client:SyncAPIClient.post

どちらの SDK も同じ生成器（Stainless）で作られていて、
`responses.parse` も `messages.create` も最後はここを通る。
`body` は送信するそのままの dict なので、`body["model"]` を書き換えれば済む。

`body` の dict は写しを作って差し替える。
SDK が呼び出し側の dict をそのまま握っている場合に、
こちらの都合で中身を変えないため。

ゲームが組み立てた要求は**ゲームが選んだモデル向け**なので、
差し替え先が 400 で断る引数を直してから送る（各ファイルの `fix`）。

## 割り切り

  * ゲーム内のコスト表示はずれる。価格計算はゲームが選んだモデルの
    単価で行う。表示だけの話で、実際の課金には関わらない
  * 設定画面の表示もゲームが選んだモデルのまま
"""

from . import claude_side, openai_side

# 「差し替えない」を表す選択肢。一覧の先頭に置く。
OFF = "off"

# **`mod.json` の "default" と "values" に揃えること**（`tools/check_mods.py` が
# AST で突き合わせる。TECH.md §3.8.3）。

# ------------------------------------------------------------------ OpenAI API
# 実際に送るモデル。
OPENAI_MODEL = "gpt-6-luna"

# 一覧に無いモデル名。空でなければ OPENAI_MODEL より優先する。
OPENAI_CUSTOM = ""

# 推論量。"keep" はゲームが組み立てた値のまま。
OPENAI_EFFORT = "keep"

# プロンプトキャッシュ（GPT-5.6 以降）。"keep" はゲームのまま（OpenAI が末尾に自動で印を置く）。
# "off" は印を置かない、"stable" は前回と同じ所までに印を置く（`openai_side.py`）。
# ゲームのままでは当たらずに書き込みの割増しだけを払うので、stable を既定にしてある。
OPENAI_CACHE = "stable"

# ------------------------------------------------------------------ Claude API
# 実際に送るモデル。既定は差し替えない（ゲームの一覧に claude-sonnet-5 が在る）。
CLAUDE_MODEL = "off"

# 一覧に無いモデル名。空でなければ CLAUDE_MODEL より優先する。
CLAUDE_CUSTOM = ""

# 推論量（output_config.effort）。"keep" はゲームが組み立てた値のまま。
# ゲームは応答の速さが効くので low を既定にしてある。
CLAUDE_EFFORT = "low"

# プロンプトキャッシュ。"off" は素のゲームのまま（ゲームは cache_control を送らない）。
# "5m" / "1h" で前回と同じ所までに印を付ける（保持の長さ。`claude_side.py`）。
# 保持は当たるたびに延びるので、書き込みの安い 5m を既定にしてある。
CLAUDE_CACHE = "5m"

# ------------------------------------------------------------------ 共通
# 差し替え（とキャッシュの使用量）をログに出す回数。
# 毎回出すとログが埋まるので先頭だけ。
LOG_LIMIT = 3


def _pick(custom, chosen):
    """一覧に無い名前が書いてあればそちら、無ければ一覧の選択。"off" は None。"""
    name = (custom or "").strip() or (chosen or "").strip()
    if not name or name == OFF:
        return None
    return name


def _setting(value, fallback):
    return (value or fallback).strip() or fallback


class _Log(object):
    """宛先ごとのファイルが使うログ。種類（"swap" / "usage"）ごとに LOG_LIMIT 回まで出す。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.counts = {}

    def wants(self, kind):
        return self.counts.get(kind, 0) < LOG_LIMIT

    def write(self, kind, text):
        if not self.wants(kind):
            return
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.ctx.log("cloud model override: " + text)

    def swap(self, provider, source, target, path, changed):
        self.write("swap", "[{}] {} -> {} ({}){}".format(
            provider, source, target, path,
            " fixed: " + ", ".join(changed) if changed else ""))


def apply(ctx):
    openai_target = _pick(OPENAI_CUSTOM, OPENAI_MODEL)
    openai_effort = _setting(OPENAI_EFFORT, "keep")
    openai_cache = _setting(OPENAI_CACHE, "keep")
    claude_target = _pick(CLAUDE_CUSTOM, CLAUDE_MODEL)
    claude_effort = _setting(CLAUDE_EFFORT, "keep")
    claude_cache = _setting(CLAUDE_CACHE, OFF)

    # 差し替え先が無い側は、フックごと仕掛けない。
    # 「何もしない包み」を残すより、通り道を素のままにしておく方が安い。
    log = _Log(ctx)
    openai_side.install(ctx, openai_target, openai_effort, openai_cache, log)
    claude_side.install(ctx, claude_target, claude_effort, claude_cache, log)

    ctx.log("cloud model override: openai={} (effort {}, cache {}) / claude={} (effort {}, cache {})"
            .format(openai_target or OFF, openai_effort, openai_cache,
                    claude_target or OFF, claude_effort, claude_cache))
