# `226_probe_item_consume`

回復アイテムを使ったとき何が起きるかを録る。右クリックで押された項目と popup の中身（`usable` 相当の値）、`ItemConsumeManager.consume_item` に渡る `usable` の実値、プレイヤーと品の持ち主の HP・スタミナ・上限・`status`・持ち物の数の前後とその間に足された文、`Item.consume` の呼び出し元、純関数 `get_heal_spec` / `get_heal_physical_integrity_barden` / `get_max_physical_integrity` の対応表、`update_max_hp` / `update_max_physical_integrity` の前後。`134_balance_item_effects` が本体を呼んだ後に戻すのか本体を呼ばずに全部書くのかを決める材料（結果は GAME.md §2.13.2）。出力は `out\item_consume.log` と `out\item_consume.jsonl`
