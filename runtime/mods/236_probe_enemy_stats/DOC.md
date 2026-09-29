# `236_probe_enemy_stats`

敵と味方の強さの出どころを録る。`319_battle_tactics` を HP・攻撃・防御で組み直すための下調べ。敵の能力値の生まれ方（`kind: gen`）、戦闘の開始の全員の値（`kind: start`）、1手ごとの裁きと素点（`kind: act`）を残す。設定「素点の試し打ち」（既定 OFF）を入れると、戦闘の開始でゲームの素点の段だけを呼び、相手ごとの素点を録る（`kind: dry`。前後で乱数の状態を戻し、HP か状態異常が動いたら止める）。決着（GAME.md §2.10.4）。出力は `out\enemy_stats.log` / `out\enemy_stats.jsonl`
