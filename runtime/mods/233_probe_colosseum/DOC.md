# `233_probe_colosseum`

闘技場の試合を録る。相手の強さの伸びと報酬を設定で決める MOD（`334_colosseum_custom`）の下調べ。試合を1つの窓にして、前後の所持金と日付、報酬の文、相手のランクと難易度の渡り方、施設の `config`、終わり方の経路を残す。ランクの式は決着（`rank(n) = round(D * (9 + 4n) / 18)`。GAME.md §2.11）。報酬に乱数は乗らないが、ランクだけでは決まらない。残るのは報酬の式。出力は `out\colosseum.log` / `out\colosseum.jsonl`
