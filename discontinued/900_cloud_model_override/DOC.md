# `900_cloud_model_override`: クラウドのモデルを差し替える（開発終了・分離した記録）

## 0. 開発終了（2026-08-30）

**この MOD の公開向けの開発は終了した。**
`runtime\mods\` から `discontinued\900_cloud_model_override\` へ移してある（TECH.md §2.6.1）。
git には残るが、ローダは読み込まず、配布物にも CI にも入らない。

| | |
|---|---|
| なぜ終了したか | **手元の事情に閉じていて配る意味が無い。** 効くのは、ゲームの一覧に無い OpenAI のモデルを自分の鍵で使いたいときだけで、そのモデルが使えるかどうかは配る先ごとに違う |
| どこまで動いていたか | 実機で成立。ゲームには一覧に在る名前のまま持たせ、HTTP に出る直前で差し替えている |
| 残っている症状 | 無し |
| 再開するなら | `runtime\mods\` へ戻して `load_order.local.json` に名前を書く。ゲームの側のモデル一覧が版で変わるので、`SOURCE_MODEL` が今も選べる名前かを先に見る |

---

## 1. 何をするか

ゲームの OpenAI 対応は、選べるモデル名がコード定数として埋まっている
（`scripts.hud.hud_option:OptionLLMScreen.get_cloud_llm_list` と
`hud_auto_configuration` に同じ表がある）。
一覧に無い名前は設定画面から選べない。

一覧に足すだけでは足りない。
`scripts.llm.request_llm_inference_openai:send_request` が**モデル名で API の経路を分けている**ため。

```
gpt-5 / -mini / -nano                             responses.parse, effort="minimal"
gpt-5.5 / 5.4 / 5.4-mini / 5.4-nano / 5.2 / 5.1   responses.parse, effort="none"
それ以外                                          beta.chat.completions, max_tokens=
```

3番目は古い経路で、GPT-5 系は `max_tokens` を受け付けない（`max_completion_tokens` が要る）。
reasoning の指定も無いので、通ったとしても既定の推論量になりゲームには遅い。
`config.json` の `cloud_llm` に新しい名前を直接書くと、名前は届くが壊れた経路に落ちる。
価格表（`calculate_price`）にも無い鍵になる。

そこで名前を足しに行かず、**ゲームには一覧に在る名前のまま持たせ、送信の直前に差し替える**。
ゲームは `SOURCE_MODEL` のつもりで速い経路を組み立て、実際に飛ぶのは `TARGET_MODEL`。

## 2. 設定

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| ゲーム側で選ぶモデル | `gpt-5.5` | この名前で送られてきたものだけを差し替える。ゲームの設定画面で選んだものと同じにする |
| 実際に送るモデル | `gpt-5.6-luna` | 空にするか、上と同じにすると何もしない |
| 差し替えをログに出す回数 | 3 | `out\cloud_model_override.log`。効いているかの確認用 |

## 3. 困ったとき

| 症状 | 見るところ |
| --- | --- |
| 差し替わっていない | `out\cloud_model_override.log` に行が出ているか。出ていなければ `SOURCE_MODEL` がゲームで選んでいる名前と違う |
| API がモデル名を知らないと返す | `TARGET_MODEL` の綴り。鍵の側でそのモデルが使えるかも見る |
| 応答が遅い | 差し替え先が reasoning の既定値で動いている。ゲームが組み立てる `effort` は `SOURCE_MODEL` の側で決まる |
