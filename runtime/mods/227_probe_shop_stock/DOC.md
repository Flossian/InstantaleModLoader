# `227_probe_shop_stock`

買った品が店の棚へ戻るのはどこかを録る。決着済みで、棚の品はゲームが店を開くたびに雛形 `config['goods']` から作り直した別の現物だった（GAME.md §2.13.1.3。対処は `312_shop_restock`）。店の場面で生まれた品の id・持ち主・呼び出し元と、店の経路の境目ごとの持ち物の増減（差分だけ）を残す。店の外の品の誕生は `ITEM_SAMPLES` を決めたときだけ録る。出力は `out\shop_stock.log` / `out\shop_stock.jsonl`
