# `226_probe_item_consume`

回復アイテムを使ったとき、何が誰にどれだけ効くかを録る。押された項目と `usable` の実値、使用の前後の HP・スタミナ・上限・`status`・持ち物の数と足された文、回復量を出す純関数の対応表が対象。`134_balance_item_effects` が本体を呼ぶか全部書くかを決める材料（結果は GAME.md §2.13.2）。出力は `out\item_consume.log` / `out\item_consume.jsonl`
