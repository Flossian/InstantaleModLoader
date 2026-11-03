# `204_probe_prompt_bloat`

EVENTLOG / DEDUP / サイドカー起動という3つのプロンプト肥大化挙動と、ローカル LLM の頼みごとの頼みと出力のトークン数・窓あふれを測る。llama-server の起動は1回ごとに `SIDECAR launch` の1行で残り、先に起こして生きている本数（`alive_before`）で多重起動が分かる
