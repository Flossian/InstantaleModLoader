# `245_probe_shop_negotiation`

店の売買の窓で値切ったとき、決まった額が品のどこに書かれ、いつまで残るかを録る。`shopping_negotiator` に送った文と返った `player_attempt`、`ShoppingNegotiateManager` の前後で動いた店と手持ちの品の `買価` / `売価` と所持金を残す。値切った品はその後も id で追い、一覧の描き直し・品の詳細・売買・ゲームの付け直し・保存・窓を閉じる、の後で額が動いたら、いまローダで値段を決めている MOD と一緒に書く。値段の MOD を入れると値切りが効かないという指摘の切り分け。出力は `out\shop_negotiation.log` / `out\shop_negotiation.jsonl`
