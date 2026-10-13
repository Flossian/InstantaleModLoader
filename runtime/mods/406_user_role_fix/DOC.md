# `406_user_role_fix`: クラウド API の Gemini・Claude で、user role の無い依頼が返らない・断られるのを防ぐ

素のゲームには、system role だけで組んで送る依頼が4件ある。
クラウド API で Gemini を使うと、この4件に返答が無く、裏で落ちる。
Claude を使うと、API が system を別の欄へ移すので送る会話（messages）が空になり、`400 messages: at least one message is required` で断られる。
店では品揃えが作られず、売買の画面が開かないまま止まる。
この MOD はその4件へ最小限の user role を1件足す。
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
- Gemini か Claude の送信モジュールが読み込まれているときだけ効く。それ以外と、ローカル実行では何もしない
- 対象の依頼に user role が1件も無いときだけ、最初の system の直後へ足す。既に user があれば触らない
- 足したときは `out\modloader.log` に `406_user_role_fix: inserted user role for <manager>` が残る

## 困ったとき

| 症状 | やること |
|---|---|
| Gemini・Claude で店の品揃え・エピローグ・自由施設の要約が返ってこない | `out\modloader.log` に `inserted user role` が出ているか見る。出ていなければ送信モジュール名に `gemini` も `claude` も含まれていない |
| 上の4件以外の依頼が Gemini・Claude で返ってこない | 対象表に無い依頼。ゲームの `output_data` の記録で user role が無いのはこの4件だけだった（85 種を確認） |
| 前の版から上書きで入れ直した | 版3でフォルダの名前を変えた。GUI を開いたとき（更新の後に開き直したときも）と watcher の起動のときに、古いフォルダ `406_gemini_user_role_fix` は消え、古い名前で切っていれば新しい名前も切られる。zip を手で展開して GUI を通さずに注入したときは、古いフォルダが残っていれば消す。残っていても、user role がもうある依頼には足さないので二重には足さない |
