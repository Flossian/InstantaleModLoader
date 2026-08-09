# -*- coding: utf-8 -*-
"""ゲームが知らないクラウドモデルを使う。**送信直前にモデル名だけ差し替える**。

ゲームの OpenAI API 対応は、モデル名の一覧がコード定数として埋まっている
（`scripts.hud.hud_option:OptionLLMScreen.get_cloud_llm_list` と
`hud_auto_configuration` に同じ表がある）。一覧に無い名前は設定画面から選べない。

一覧に足すだけでは足りない。`scripts.llm.request_llm_inference_openai:send_request`
は、**モデル名で API の経路を分けている**:

    gpt-5 / -mini / -nano                          responses.parse, effort="minimal"
    gpt-5.5 / 5.4 / 5.4-mini / 5.4-nano / 5.2 / 5.1  responses.parse, effort="none"
    それ以外                                        beta.chat.completions, max_tokens=

3番目は古い経路で、GPT-5 系は `max_tokens` を受け付けない（`max_completion_tokens`
が要る）。reasoning の指定も無いので、通ったとしても既定の推論量になりゲームには
遅い。つまり `config.json` の `cloud_llm` に新しい名前を直接書くと、名前は届くが
**壊れた経路に落ちる**。加えて価格表（`calculate_price`）にも無いキーになる。

そこで名前を足しに行かず、**ゲームには一覧にある名前のまま持たせ、HTTP に出る
直前で差し替える**。ゲームは `SOURCE_MODEL` のつもりで速い経路
（responses.parse + effort="none"）を組み立て、実際に飛ぶのは `TARGET_MODEL`。

## どこに仕掛けるか

    openai._base_client:SyncAPIClient.post

`client.responses.parse` も `client.responses.create` も
`client.beta.chat.completions.parse` も、最後はここを通る（リソース側の
`self._post` は `self._client.post` そのもの）。エンドポイントごとに包むと
SDK の更新で取りこぼすので、1本に絞ってある。`body` は送信するそのままの dict
なので、`body["model"]` を書き換えれば済む。

`body` の dict は**写しを作って**差し替える。SDK が呼び出し側の dict を
そのまま握っている場合に、こちらの都合で中身を変えないため。

## 使い方

  1. ゲームの設定画面で `SOURCE_MODEL`（既定 `gpt-5.5`）を選ぶ
  2. `TARGET_MODEL` に実際に使いたい名前を入れる

効いていれば `out/modloader.log` に `cloud model override: ... -> ...` が出る。
出ないときは、ゲーム側が別のモデルを選んでいるか、クラウドではなくローカル LLM で
動いている（`openai` を通らない）。

## 割り切り

  * **ゲーム内のコスト表示はずれる。** 価格計算はゲームが `SOURCE_MODEL` の
    単価で行う。表示だけの話で、実際の課金には関わらない
  * **設定画面の表示も `SOURCE_MODEL` のまま。** 一覧に名前を足していないので
    当然だが、「5.5 と書いてあるのに 5.6 が動く」ことは承知しておく
  * **差し替え先が `SOURCE_MODEL` と同じ経路で動くこと**が前提。responses API と
    構造化出力に対応し、reasoning effort に `none` を受けるモデルであること
"""

# ゲーム側で選ぶ名前（この名前で来たリクエストだけを差し替える）。
SOURCE_MODEL = "gpt-5.5"

# 実際に送る名前。空にするか SOURCE_MODEL と同じにすると何もしない。
TARGET_MODEL = "gpt-5.6-terra"

# 差し替えをログに出す回数。毎回出すとログが埋まるので先頭だけ。
LOG_LIMIT = 3

#: openai の SDK で、全リクエストが最後に通る1点。
POST_TARGET = "openai._base_client:SyncAPIClient.post"


def apply(ctx):
    source = (SOURCE_MODEL or "").strip()
    target = (TARGET_MODEL or "").strip()

    # 差し替え先が無い／同じなら、フックごと仕掛けない。
    # 「何もしない包み」を残すより、通り道を素のままにしておく方が安い。
    if not source or not target or source == target:
        ctx.log("cloud model override: off (source={!r} target={!r})".format(
            SOURCE_MODEL, TARGET_MODEL))
        return

    state = {"swapped": 0}

    # required=False: ローカル LLM で遊んでいる間は openai が読み込まれない。
    # モジュールが現れた時点でローダが当て直すので、ここでは黙って見送る。
    # safe=True: ここが壊れても素の送信に落とす（LLM が止まる方が損害が大きい）。
    @ctx.wrap(POST_TARGET, required=False, safe=True, alias_scan=False)
    def post(orig, self, path=None, *args, **kwargs):
        body = kwargs.get("body")
        if isinstance(body, dict) and body.get("model") == source:
            # 呼び出し側の dict は変えず、浅い写しを渡す。
            kwargs = dict(kwargs, body=dict(body, model=target))
            state["swapped"] += 1
            if state["swapped"] <= LOG_LIMIT:
                ctx.log("cloud model override: {} -> {} ({})".format(
                    source, target, path))
        return orig(self, path, *args, **kwargs)

    ctx.log("cloud model override: installed ({} -> {})".format(source, target))
