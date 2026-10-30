# `105_fix_schema_compact`: LLM へ送るスキーマ説明を圧縮する

プロンプトに埋め込まれたスキーマ説明文を、簡潔な骨組み表記に置き換える。
ゲームは `json_schema`（grammar）を送ると同時に、同じスキーマを dict の repr としてプロンプト本文にも埋め込む。
構造は grammar が強制するので、全フィールドに付く `title` などの定型句は落としてよい。
型と値は出力する JSON に近い形で残す。

    元:   {'$defs': {'Location': {'properties': {'name': {'title': 'Name', ...
    後:   Location = {"name": string, "kind": "shop"|"inn"}
          Area = {"name": string, "locations": Location[], "atomosphere": "tense"|"normal", "note"?: string|null}

外部プロキシ（InstantaleLLMProxy）の COMPACT と同じ判定をプロセス内でやる。
出力の書式は版2でプロキシの一覧表記から離れたので、同じスキーマから同じ文は出ない。
両方を同時に動かしても、先に圧縮した方でマーカー（`{'$defs':` 等）が消えるため、もう片方は何もしない。

## 型と値を削らない（版2）

版1はフィールド名と enum・参照・配列・任意の印だけを `kind:∈{shop,inn}` の形で残し、素の型と null を落としていた。
grammar が許す範囲は変わらないが、その中でモデルの選び方が変わる欄があった。
会話で「雇いたい」と言ったとき、相手が文では応じていても雇用の返事として扱われず、「雇う（40G）」が出ない回が増えていた。
版2は値と型を JSON の形で書くので、圧縮しないときとほぼ同じ割合で出る。
縮み方は 73% から 67% になった（VERIFICATION_LOG.md §2.3）。

## どこに仕掛けるか

`LlamaCppClient.chat(model, messages, format=...)` の1点。
`format` が dict のときだけ messages を圧縮する。
`"json"` のような汎用指定はフィールド単位の強制をしないので、説明文を削ると手がかりが消える。

プロキシと同位置の `_post_with_model_loading_retry`（payload に `prompt` と `json_schema` が揃う地点）にも仕掛けていたが、
`chat` が上流で先に圧縮するとマーカーが残らず、実機 2,803 件で payload 側の発火は 0 だった（VERIFICATION_LOG.md §2.3）。
その仕掛けは外した。
`chat` に仕掛かったかどうかは `bootstrap.log` の `schema compact: armed on chat` で分かる。

## 割り切り

- 圧縮するのは最初に見つかったスキーマ1個だけ。
  2個目を含むメッセージは実データに 0 件
- `$defs` の中でも `properties` を持たない定義（Enum クラス単体など）は行を出さず、参照側に型名だけが残る。
  pydantic は `Literal` を参照先ではなくプロパティ側に展開するため、enum 値は落ちない
- `description` は残さない。
  実データ（`output_data/` の埋め込みスキーマ 2,485 件）に `description` を持つプロパティは 0 件なので、落として失うものが無い
- 置換後の方が長くなる場合は何もしない

## クラウド（APIキー）では動かない。それでよい

`llm.wrap_outgoing`（プロバイダ非依存の口）には載せていない。
圧縮してよいかの判定が `format` の有無で、これは llama.cpp の経路にしか無い。
そもそもクラウドではスキーマ文がプロンプトに埋まっていない。
Gemini は `send_request` の中で足すので境界の外、OpenAI / Claude は API 側に任せていて埋め込み自体が無い（GAME.md §2.12）。
`119_` / `305_` が「ローカルにしか仕掛けていない」ことで取りこぼしていたのとは事情が違う。

## 検証

版1は実データ 12,067 件のオフライン検証と実機の両方で済んでいる。
誤爆・欠落とも 0 件、削減率は 72〜73%（VERIFICATION_LOG.md §2.3）。
版2は実データ 3,896 件で解析の失敗・二重の圧縮とも 0 件、雇用の会話をローカル LLM へ投げ比べて確かめた。
`tools/tests/test_schema_compact.py` が同じ見本で骨組みの形・べき等・無加工を検査する。
