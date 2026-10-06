# `239_probe_game_flags`

ゲームの「〜の最中」の旗 7 つ（`in_battle` / `in_boss_battle` / `in_colosseum_battle` / `in_conversation` / `in_free_input` / `in_action_in_conversation` / `in_shopping`）を録る。誰が立てて誰が下ろすか（値が変わるたびに呼び出し元の連鎖と居場所）、ゲームのどの関数が読むか（読み手ごとに初回だけ）、ロード後と保存前の値、属性を通らない書き換えが対象。旗が名前のとおりに下りるかの下調べ。出力は `out\game_flags.log` / `out\game_flags.jsonl`
