# `234_probe_busy_display`

待機表示（選択肢の枠の「…」）を録る。ゲーム標準の「…」とローダの `ui.Screen.busy_on` の「…」を同じ物差しで並べ、見た目の違いの元を探す。0.2秒ごとに枠の文字と旗を読んで変わったときだけ書き、塗る手・組み直し・背景の差し替えを呼び出し元つきで残す。点送りはひと続きを1行にまとめる。MOD が出した区間は、その MOD のログの `busy on` / `busy off` と時刻で突き合わせる。出力は `out\busy_display.log` / `out\busy_display.jsonl`
