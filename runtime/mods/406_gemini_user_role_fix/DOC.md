# `406_gemini_user_role_fix`: クラウド API の Gemini で user role の無い依頼を補う

クラウド API で Gemini を使うと、system role だけで組まれた依頼に返答が無く、裏で落ちる。
素のゲームには system role だけで送る依頼が4件あり、この MOD はその4件へ最小限の user role を1件足す。
system role の本文には触れない。
GPT 系は system role だけでも返答できるので素通し、ローカル（llama.cpp）も素通しする。
公式で直ったら外してよい。

| 依頼（manager） | 足す user の本文 |
| --- | --- |
| `shop_item_generator_ordinary` | `＜売買する＞` |
| `epilogue_pre_evaluator` | `＜エピローグを評価する＞` |
| `epilogue_generator` | `＜エピローグを生成する＞` |
| `free_facility_summary` | `＜行動記録を要約する＞` |

## どう動くか

- 共有部品の `llm.watch_aliases` で、後から生える `llm_manager:send_request` を見張って包む
- Gemini の送信モジュールが読み込まれているときだけ効く。それ以外と、ローカル実行では何もしない
- 対象の依頼に user role が1件も無いときだけ、最初の system の直後へ足す。既に user があれば触らない
- 足したときは `out\modloader.log` に `406_gemini_user_role_fix: inserted user role for <manager>` が残る

## 困ったとき

| 症状 | やること |
|---|---|
| Gemini で店の品揃え・エピローグ・自由施設の要約が返ってこない | `out\modloader.log` に `inserted user role` が出ているか見る。出ていなければ送信モジュール名に `gemini` が含まれていない |
| 上の4件以外の依頼が Gemini で返ってこない | 対象表に無い依頼。ゲームの `output_data` の記録で user role が無いのはこの4件だけだった（85 種を確認） |
