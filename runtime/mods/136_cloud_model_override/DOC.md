# `136_cloud_model_override`: クラウドのモデルを差し替える

ゲームの一覧に無い OpenAI / Claude のモデルで遊ぶ。
ゲームには一覧のモデルを選ばせたまま、送る直前にモデル名を差し替える。
差し替え先が受け付けない引数も、送る前に直す。
プロンプトキャッシュの印の置き方も、ここで選べる。

## ゲーム側で選ぶもの

差し替えは**同じプロバイダの中だけ**で行う。
OpenAI のモデルを Claude に、Claude のモデルを OpenAI に変えることはできない。
ゲームはプロバイダごとに別のクライアント（`openai.OpenAI` / `anthropic.Anthropic`）で送り、API キーも別だから（GAME.md「プロバイダは1つだけ import される」）。
先にゲームの設定画面で、使いたい側のプロバイダを選んで API キーを入れておく。

| 使いたいモデル | ゲームの設定画面 | この MOD の設定 |
| --- | --- | --- |
| OpenAI（GPT-6 Luna など） | プロバイダは OpenAI、キーは OpenAI の API キー。モデルは OpenAI の一覧のどれでもよい（`gpt-5.5` を推奨。effort `none` の速い経路を組み立てる） | 「OpenAI API」の「実際に送るモデル」 |
| Claude（Claude Opus 5.5 など） | プロバイダは Claude、キーは Anthropic の API キー。モデルは Claude の一覧のどれでもよい（素のゲームの既定は `claude-sonnet-5`） | 「Claude API」の「実際に送るモデル」。既定は `off` なので必ず選ぶ |

ゲーム側を OpenAI にしたまま Claude の欄だけ設定しても、何も起きない。
Claude の送信が1度も走らないため。
使わない側の欄は既定のままでよい。
その SDK が読み込まれなければ、仕掛けも当たらない。

## 設定

設定画面は「OpenAI API」「Claude API」「共通」の見出しで分かれている。

### OpenAI API

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| 実際に送るモデル | GPT-6 Luna | 一覧から選ぶ。`off` で差し替えない |
| 一覧に無いモデル | 空 | 書けば一覧より優先 |
| 推論量 | `keep` | `keep` はゲームの値のまま（gpt-5.5 などを選んでいれば `none`）。GPT-6 Astra と GPT-6.1 Sol へは `low` 以上で送る |
| プロンプトキャッシュ | 前回と同じ所まで控える | ゲームのまま / 使わない。GPT-5.6 以降だけに効く。「プロンプトキャッシュ」の節 |

### Claude API

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| 実際に送るモデル | `off` | 一覧から選ぶ。`off` で差し替えない |
| 一覧に無いモデル | 空 | 書けば一覧より優先 |
| 推論量 | `low` | `output_config.effort`。Opus 5.5 は思考を切れず既定が `medium` なので、low にしないと応答が遅い |
| プロンプトキャッシュ | 5分 | 前回と同じ所までをその時間だけ控えさせる。1時間 / 使わない。「プロンプトキャッシュ」の節 |

### 共通

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| 差し替えをログに出す回数 | 3 | `out\modloader.log`。効いているかの確認用。応答の入力・キャッシュの書き込み・読み出しの数も同じ回数だけ出す（Claude はキャッシュを使うときだけ） |

一覧の中身（画面には名前で出る。ログと「一覧に無いモデル」の欄はモデル ID）:

| OpenAI API | モデル ID | Claude API | モデル ID |
| --- | --- | --- | --- |
| GPT-6 Astra | `gpt-6-astra` | Claude Opus 5.5 | `claude-opus-5-5` |
| GPT-6.1 Sol | `gpt-6.1-sol` | Claude Sonnet 5.5 | `claude-sonnet-5-5` |
| GPT-6 Sol | `gpt-6-sol` | Claude Haiku 5.5 | `claude-haiku-5-5` |
| GPT-6 Luna | `gpt-6-luna` | Claude Opus 5 | `claude-opus-5` |
| GPT-5.6 Sol | `gpt-5.6-sol` | Claude Sonnet 5 | `claude-sonnet-5` |
| GPT-5.6 Terra | `gpt-5.6-terra` | Claude Haiku 4.5 | `claude-haiku-4-5` |
| GPT-5.6 Luna | `gpt-5.6-luna` | Claude Fable 5.1 | `claude-fable-5-1` |
| GPT-5.5 | `gpt-5.5` | Claude Opus 4.8 | `claude-opus-4-8` |
| GPT-5.4 | `gpt-5.4` | Claude Sonnet 4.6 | `claude-sonnet-4-6` |
| GPT-5.4 mini | `gpt-5.4-mini` | | |
| GPT-5.4 nano | `gpt-5.4-nano` | | |

OpenAI 互換の別サーバー（任意互換 / Alibaba）も `openai` の SDK を通るので、
**宛先が `api.openai.com` のときだけ**差し替える。

## 仕組み

ゲームのクラウド対応は、選べるモデル名がコード定数として埋まっている
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

そこで名前を足しに行かず、**ゲームには一覧に在る名前のまま持たせ、送信の直前に差し替える**。
仕掛けるのは各 SDK で全リクエストが最後に通る1点。

```
openai._base_client:SyncAPIClient.post       OpenAI
anthropic._base_client:SyncAPIClient.post    Claude
```

### 名前だけでは通らないものを直す

ゲームが組み立てた要求は「ゲームで選んだモデル」向けなので、
差し替え先が 400 で断る引数を送る前に直す。

| 差し替え先 | 直すもの |
| --- | --- |
| OpenAI（GPT-5 無印以外） | `effort="minimal"` → `"none"` |
| OpenAI GPT-6 Astra / GPT-6.1 Sol | `effort="none"` → `"low"`（`none` を受け付けない） |
| OpenAI GPT-5 以降 | `temperature` / `top_p` / `top_logprobs` を外す。chat.completions の `max_tokens` → `max_completion_tokens` |
| Claude Opus 4.7 以降 / Sonnet 5 以降 / Haiku 5.5 / Fable | `temperature` / `top_p` / `top_k` を外す。`thinking` の `enabled`（budget_tokens）→ `adaptive` |
| Claude Opus 5.5 / Fable 5 / Fable 5.1 | 上に加えて `thinking: disabled` を外す（思考を切れない） |
| Claude Sonnet 5.5 | 「Opus 4.7 以降」の行に加えて `thinking: disabled` → `between_tools`（同じく思考を切る指定） |
| Claude Opus 5 / Sonnet 5.5 / Haiku 5.5 | 推論量が `xhigh` / `max` のときは思考を切る指定を受け付けないので、`thinking: disabled` を外す |
| Claude Opus 5.5 / Sonnet 5.5 / Fable 5.1 | `tool_choice` の `any` / `tool` → `auto` |
| Claude Haiku 4.5 | `output_config.effort` を外す（受け付けない） |

一覧に無い名前（「一覧に無いモデル」の欄）には、**いちばん厳しい側**の直しを当てる。
新しいモデルを試すときに書く欄なので、そちらの方が 400 を踏みにくい。

## プロンプトキャッシュ

前の頼みと同じ部分を控えておき、次の頼みでその分の入力を安く読ませる仕組み。
ゲームの頼みは毎回の状況を末尾に足して組み直すので、前の頼みと同じなのは先頭の部分だけになる。
この MOD は、**前の頼みと先頭から同じだった所まで**に印を置く。
最初の1回は比べる相手が無いので印を置かず、2回目から控え、3回目から読む。

### OpenAI API

GPT-5.6 以降（GPT-6 系を含む）だけに効く。既定は「前回と同じ所まで控える」。
GPT-5.5 以前は書き込みの割増しが無く、頭が同じなら自動で控えるので、どれを選んでも何もしない。

| 設定 | 動き |
| --- | --- |
| ゲームのまま | OpenAI が頼みの末尾に自動で印を置く。末尾は毎回変わるので当たらず、入力のほぼ全部を毎回 1.25 倍で払う |
| 使わない | 印を置かない。控えも割増しも無くなり、ゲームのままより入力の料金が約2割下がる |
| 前回と同じ所まで控える | 同じ部分を 1.25 倍で書き込み、次からは 0.1 倍（GPT-6.1 Sol は 0.05 倍）で読む。控えは最後に使ってから30分残る |

### Claude API

ゲームは Claude へキャッシュの指定を送らないので、「使わない」が素のゲームのまま。既定は「5分」。
「実際に送るモデル」を `off` にしていても効く。

| 設定 | 書き込む回 | 当たった回 |
| --- | --- | --- |
| 5分 | 入力の 1.25 倍 | 入力の 0.1 倍（Opus 5.5 は 0.05 倍、Fable 5.1 は 0.025 倍） |
| 1時間 | 入力の 2 倍 | 同上 |

保持の時間は当たるたびに延びる。
「1時間」が得になるのは、同じ頼みの間が5〜60分空く回が、書き込み3回につき2回より多いとき。
ふだんの遊び方では頼みの間はほとんど5分未満なので、「5分」の方が安い（下の「効果の目安」）。

### 効果の目安

入力の料金を、キャッシュ無しを 100 として比べた。

| 設定 | 入力の料金 | 測り方 |
| --- | --- | --- |
| OpenAI「ゲームのまま」（GPT-6 Luna） | 約 125 | 実機で会話5ターン。19回で当たりは0回 |
| OpenAI「使わない」 | 100 | 控えも割増しも無い |
| OpenAI「前回と同じ所まで控える」（GPT-6 Luna） | 約 82 | 実機で会話5ターン。30回 |
| Claude「5分」（Sonnet 5.5） | 約 84 | 実機で会話5ターン。28回 |
| Claude「5分」 | 約 88 | 18日分の頼みの記録 5,055件で試算 |
| Claude「1時間」 | 約 95 | 同上 |

- 実機の5ターンは、書き込みの割合が大きく出る。長く遊ぶほど読み出しが増えて下がる
- 試算は、ゲームが残した頼みの記録を時刻の順に並べ、この MOD と同じ置き方で印を置いて数えた。記録の中で、同じ控えを次に使うまでの間は、5分未満が 1,019回、5〜60分が 157回、60分超が 81回だった
- 頼みの種類によっては、控えが読まれずに終わってキャッシュ無しより少し高くなるものもある。合わせると上の数字になる

### 効かない場合

- 同じ部分がモデルごとの最小の長さに足りない頼みには、印を置かない（OpenAI は 1,024 トークン、Claude は 512〜4,096 トークン）
- 効いているかは `out\modloader.log` の `usage:` の行で見る。2回目以降の `cache read` が 0 より大きければ当たっている。書き込みばかりで読み出しが 0 のままなら「使わない」に戻す

ゲーム内のコスト表示は、当たった分の値引きは入るが、書き込みの割増しは入らない。

## 困ったとき

| 症状 | 見るところ |
| --- | --- |
| 差し替わっていない | ログに `cloud model override: [...]` の行が出ているか。出ていなければ、ゲーム側のプロバイダとこの MOD で設定した側が合っていない（「ゲーム側で選ぶもの」）か、ゲームがローカル LLM で動いている |
| API がモデル名を知らないと返す | 一覧に無いモデルの欄の綴り。鍵の側でそのモデルが使えるかも見る |
| 400 で引数を断られる | ログの `fixed:` に何が載っているか。ゲームが新しい引数を送り始めた可能性がある。エラー文にある引数名を添えて報告する |
| 応答が遅い | 推論量を下げる（OpenAI は `none`、Claude は `low`）。GPT-6 Astra と GPT-6.1 Sol は `none` にできないので、速さが要るなら GPT-6 Luna / GPT-6 Sol |
| ゲーム内のコスト表示が合わない | 仕様。価格はゲームが選んだモデルの単価で計算される（表示だけで、実際の課金には関わらない） |
