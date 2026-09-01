# `224_probe_npc_carryover`

ロードのどの地点から世界へ NPC を入れられるかを測る。見るのは `load_game_new` / `start_game` の前後・ロード後の最初の選択肢・注入時の4地点。NPC を作るのに要るもの（世界の鍵・素データの `npcs` 辞書・採番台帳 `index['npc']`・`generate_character`・`move_npc_to_facility`・ダンジョン以外のギルドか宿）が揃っているかを数え、地点ごとに `READY` / `NOT READY` を残す。台帳と実在 id の食い違いも同じ行に控える
