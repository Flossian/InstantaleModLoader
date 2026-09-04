# `226_probe_item_consume`

回復アイテムを使ったとき実際に何が起きるかを録る。右クリックのどの項目が押されたかと popup の中身（`usable` 相当の値）、`ItemConsumeManager.consume_item` に渡る `usable` の実値、プレイヤーと品の持ち主の HP・スタミナ・上限・`status`・持ち物の数の前後、その間に足された文、`Item.consume` を誰が呼ぶか、純関数 `get_heal_spec` / `get_heal_physical_integrity_barden` / `get_max_physical_integrity` の対応表、`update_max_hp` / `update_max_physical_integrity` の前後。回復アイテムの効き方を種別ごとに作り直す MOD（食料・飲み物はスタミナ、医薬品は HP、ポーションはバフ／デバフ解除、薬草・きのこは生成時に決まった幅で増減）が、本体を呼んだ後に戻すのか本体を呼ばずに全部書くのかを決めるための材料。出力は `out\item_consume.log` と `out\item_consume.jsonl`
