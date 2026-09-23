# `900_cloud_model_override`: クラウドのモデルを差し替える（開発中）

この MOD は**開発中（9xx）**。
git には入れるが、CI・配布物・`load_order.json`・`docs\` の文書には入らない（TECH.md §2.6）。
動かすには `runtime\mods\load_order.local.json` に名前を書く。

## 0. 経緯

| | |
|---|---|
| 2026-08-30 | 開発終了として `discontinued\` へ移した。OpenAI の1モデルを名前で差し替えるだけで、手元の事情に閉じていた |
| 2026-09-23 | 再開して `runtime\mods\` へ戻した。**Claude にも対応し、差し替え先を一覧から選べるようにした**（版2） |
| 2026-09-23 | GPT-6（Astra / Sol / Luna）に対応。OpenAI の既定を `gpt-6-luna` に変更（版3） |
| 2026-09-23 | 実機で `gpt-5.5` → `gpt-6-luna` の差し替えが成立（§3） |
| 残っている確認 | Claude の経路（anthropic の SDK）と、GPT-6 Astra への差し替えを実機で当てていない（§3） |

---

## 1. 何をするか

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
| OpenAI GPT-6 Astra | `effort="none"` → `"low"`（`none` を受け付けない） |
| OpenAI GPT-5 以降 | `temperature` / `top_p` / `top_logprobs` を外す。chat.completions の `max_tokens` → `max_completion_tokens` |
| Claude Opus 4.7 以降 / Sonnet 5 / Opus 5 / Fable | `temperature` / `top_p` / `top_k` を外す。`thinking` の `enabled`（budget_tokens）→ `adaptive` |
| Claude Opus 5.5 / Fable 5 / Fable 5.1 | 上に加えて `thinking: disabled` を外す（思考を切れない） |
| Claude Opus 5.5 / Fable 5.1 | 上に加えて `tool_choice` の `any` / `tool` → `auto` |
| Claude Haiku 4.5 | `output_config.effort` を外す（受け付けない） |

一覧に無い名前（「一覧に無いモデル」の欄）には、**いちばん厳しい側**の直しを当てる。
新しいモデルを試すときに書く欄なので、そちらの方が 400 を踏みにくい。

## 2. 設定

### ゲーム側で選ぶもの

差し替えは**同じプロバイダの中だけ**で行う。
OpenAI のモデルを Claude に、Claude のモデルを OpenAI に変えることはできない。
ゲームはプロバイダごとに別のクライアント（`openai.OpenAI` / `anthropic.Anthropic`）で送り、API キーも別だから（GAME.md「プロバイダは1つだけ import される」）。
先にゲームの設定画面で、使いたい側のプロバイダを選んで API キーを入れておく。

| 使いたいモデル | ゲームの設定画面 | この MOD の設定 |
| --- | --- | --- |
| OpenAI（`gpt-6-luna` など） | プロバイダは OpenAI、キーは OpenAI の API キー。モデルは OpenAI の一覧のどれでもよい（`gpt-5.5` を推奨。effort `none` の速い経路を組み立てる） | 「OpenAI: 実際に送るモデル」 |
| Claude（`claude-opus-5-5` など） | プロバイダは Claude、キーは Anthropic の API キー。モデルは Claude の一覧のどれでもよい（素のゲームの既定は `claude-sonnet-5`） | 「Claude: 実際に送るモデル」。既定は `off` なので必ず選ぶ |

ゲーム側を OpenAI にしたまま Claude の欄だけ設定しても、何も起きない。
Claude の送信が1度も走らないため。
使わない側の欄は既定のままでよい。
その SDK が読み込まれなければ、仕掛けも当たらない。

### 設定の一覧

| 設定 | 既定 | 意味 |
| --- | --- | --- |
| OpenAI: 実際に送るモデル | `gpt-6-luna` | 一覧から選ぶ。`off` で差し替えない |
| OpenAI: 一覧に無いモデル | 空 | 書けば一覧より優先 |
| OpenAI: 推論量 | `keep` | `keep` はゲームの値のまま（gpt-5.5 などを選んでいれば `none`）。`gpt-6-astra` へは `low` 以上で送る |
| Claude: 実際に送るモデル | `off` | 一覧から選ぶ。`off` で差し替えない |
| Claude: 一覧に無いモデル | 空 | 書けば一覧より優先 |
| Claude: 推論量 | `low` | `output_config.effort`。Opus 5.5 は思考を切れず既定が `medium` なので、low にしないと応答が遅い |
| 差し替えをログに出す回数 | 3 | `out\modloader.log`。効いているかの確認用 |

一覧の中身（2026-09 時点）:

- OpenAI: `gpt-6-astra` / `gpt-6-sol` / `gpt-6-luna` / `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna` / `gpt-5.5` / `gpt-5.4` / `gpt-5.4-mini` / `gpt-5.4-nano`
- Claude: `claude-opus-5-5` / `claude-opus-5` / `claude-sonnet-5` / `claude-haiku-4-5` / `claude-fable-5-1` / `claude-opus-4-8` / `claude-sonnet-4-6`

OpenAI 互換の別サーバー（任意互換 / Alibaba）も `openai` の SDK を通るので、
**宛先が `api.openai.com` のときだけ**差し替える。

## 3. 確認すること

| 項目 | 手順 | 状態 |
| --- | --- | --- |
| OpenAI → GPT-6 | ゲームで `gpt-5.5` を選び、`out\modloader.log` に `[openai] gpt-5.5 -> gpt-6-luna` が出て応答が返る | **成立**（実機1回。推論量 `none`。`/responses` で3回とも差し替わり、400 は出ず応答がゲームに渡った。`fixed:` は付かない。`gpt-5.5` が組み立てる `none` を `gpt-6-luna` がそのまま受けるため） |
| OpenAI → GPT-6 Astra | 同上で `fixed: effort none->low` が付き、400 が出ないこと | 未確認 |
| Claude → Opus 5.5 | ゲームで `claude-sonnet-5` を選び、`[claude] claude-sonnet-5 -> claude-opus-5-5` の行と `fixed:` の中身を見る。400 が出ないこと | 未確認 |
| Claude → Haiku 4.5 | 同上で `-effort` が付くこと | 未確認 |

## 4. 困ったとき

| 症状 | 見るところ |
| --- | --- |
| 差し替わっていない | ログに `cloud model override: [...]` の行が出ているか。出ていなければ、ゲーム側のプロバイダとこの MOD で設定した側が合っていない（§2「ゲーム側で選ぶもの」）か、ゲームがローカル LLM で動いている |
| API がモデル名を知らないと返す | 一覧に無いモデルの欄の綴り。鍵の側でそのモデルが使えるかも見る |
| 400 で引数を断られる | ログの `fixed:` に何が載っているか。ゲームが新しい引数を送り始めた可能性がある。エラー文にある引数名を添えて報告する |
| 応答が遅い | 推論量を下げる（OpenAI は `none`、Claude は `low`）。`gpt-6-astra` は `none` にできないので、速さが要るなら `gpt-6-luna` / `gpt-6-sol` |
| ゲーム内のコスト表示が合わない | 仕様。価格はゲームが選んだモデルの単価で計算される（表示だけで、実際の課金には関わらない） |
