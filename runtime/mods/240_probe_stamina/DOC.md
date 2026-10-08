# `240_probe_stamina`

主人公と同行者のスタミナ（`physical_integrity` / `max_physical_integrity`）と疲労の旗（`exhausted`）が変わるたびに、呼び出し元の連鎖と、依頼や戦闘の最中かを録る。依頼に出たとき・クリア・放棄の前後では、依頼の難易度と種類、レベル、スタミナ、HP、部位の怪我、依頼の間に減った HP と回復した HP を並べる。クリアで減るスタミナを難易度と被弾で変える MOD の下調べ。出力は `out\stamina.log` / `out\stamina.jsonl`
