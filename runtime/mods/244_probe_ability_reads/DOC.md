# `244_probe_ability_reads`

ゲームが能力値をどこで読み、そこから作る値（攻撃力・魔力・防御・最大 HP）をいつ組み直すかを録る。`update_ability_scores` / `update_max_hp` の前後で変わった項目と呼び出し元、能力値と攻撃力などへの代入、`calculate_attribute`（負傷や消耗での目減り）の引数と戻り、印象の計算に渡った魅力（ローダの窓口が足した魅力の補正と並べて）を残す。戦闘の開始で1プロセス1回、包む前の素の素点の計算と `get_npc_defense` を、乱数を同じ状態に戻しながら、攻撃力だけ・`ability_scores` だけ・`original_ability_scores` だけを変えて呼び比べる（変えた値はすぐ戻す）。能力値の補正をゲームが読む所で足すローダの窓口と、`802_equipment_ability_bonus` をそこへ乗せ替える下調べ。出力は `out\ability_reads.log` / `out\ability_reads.jsonl`
