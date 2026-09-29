# `235_probe_sound_effects`

効果音を録る。鳴らす出口（`SoundManager.play_sound` / `play_sound_from_src`）を包み、鳴らすたびに音の名前、装備の入口や戦闘の1手の中で鳴ったか、呼んだ MOD とゲーム側の呼び出し元を残す。`333_equipment_slots` が装備の音を何度も鳴らしていた件の裏取りに使い、決着（GAME.md §2.11「効果音」）。出力は `out\sound_effects.log` / `out\sound_effects.jsonl`
