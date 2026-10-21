# `920_attack_animations`: 攻撃の演出が武器と属性に合ったものになる（開発中）

この MOD は**開発中（9xx）**。git には入れるが、CI・配布物・`load_order.json`・
`docs\` の文書には入らない（TECH.md §2.6）。
理由は**実機で一部しか確かめていない**こと（§3）。
リリースで正式な番号へ振り直すとき、各節を元の場所へ戻す。

| ここにある節 | 戻す先 |
|---|---|
| 1. 遊び方・困ったとき | この1枚のまま。`load_order.json`（`401_battle_character_context` の後）と `BANDS` に載せれば `docs\MODS.md` に載る（TECH.md §2.7） |
| 2. 分かったこと | `docs\GAME.md` §2.10.1（審判の戻りの `vfx`）と、演出・効果音の節 |
| 3. 確かめたことと未確認項目 | `docs\VERIFICATION.md` §3 の末尾（節番号は振り直す） |

`tools\tests\test_wip_attack_animations.py` も同じ理由で CI の外（`test_wip_*` は CI が除く）。

---

## 1. 遊び方（`docs\MODS.md` 相当）

### `920_attack_animations`: 攻撃の演出が武器と属性に合ったものになる

素のゲームでは、攻撃の演出は戦闘の審判 LLM が8通りから選ぶ（斬撃・炎・氷・雷・土と、炎・氷・雷の全体）。
銃で撃っても弓で射ても拳で殴っても、ほとんどの攻撃で同じ斬撃の絵が出る。
この MOD を入れると、審判が選べる演出が増え、武器や技に合った絵と効果音が出る。

| 足す演出 | 合う攻撃 | 絵 |
|---|---|---|
| 重い一撃・十字斬り・突き | 大剣・斧、二刀・連撃、槍・細剣 | 新規追加 |
| 光の刃 | ビームサーベル・魔法剣 | 新規追加 |
| 鞭 | 鞭・鎖・触手 | 新規追加 |
| 打撃 | 拳・蹴り・棍棒・槌 | ゲーム内蔵 |
| 爪・噛みつき | 獣の攻撃 | 新規追加 |
| 矢・投擲 | 弓・弩、投げナイフ・手裏剣・石 | 新規追加 |
| 銃撃・連射・散弾・狙撃 | 拳銃・小銃、機関銃、散弾銃、狙撃銃 | 新規追加 |
| 光線 | レーザー・光線銃・レールガン | 新規追加 |
| 衝撃波 | 衝撃波・咆哮・音波・気功 | 新規追加 |
| 毒（1体と全体） | 毒・酸・毒霧 | 新規追加 |
| 爆発 | 爆弾・榴弾・ロケット | ゲーム内蔵 |
| 水 | 水の魔法（1体） | ゲーム内蔵 |
| 風・光・闇・魔力 | それぞれの魔法（1体と全体） | ゲーム内蔵 |
| 水・土の全体 | 全体を巻き込む水と土の魔法 | ゲーム内蔵 |

- どの演出を出すかは、今までどおり審判が選ぶ。装備している武器や技の説明から選ぶので、同じ武器でも技によって違う演出になることがある
- 「ゲーム内蔵」は、ゲームに入っているのに使われていなかった絵
- 新規追加は、設定で「控えめ」（既定。ゲームの斬撃に近い大きさと明るさ）と「はっきり」（一回り大きく強く光る）を選べる
- 効果音は、銃・光線・魔力は雷の音、爆発は大きな炎の音、毒は炎が飛ぶ音、重い一撃・噛みつき・衝撃波・土は打撃の音に替わる
- 演出が出るのは、今までどおり主人公と仲間が敵を攻撃したときだけ

## 困ったとき

| 症状 | やること |
|---|---|
| 新しい演出が一度も出ない | `out\attack_animations.log` に `widened vfx for` の行があるかを見る。無ければ審判に選択肢が届いていない |
| 選ばれたのに斬撃や炎の絵のまま | 同じログで、その演出の行に `no art` か `NOTE … no … effect call` が出ていないかを見る |
| 戦闘で自由入力の行動（入力欄に書いた行動・`319_` の防御）をしたら手番が止まった | 版2で直した。自由入力の審判はゲームの元の型で答えを作り直していて、足した値が通らなかった |
| 魔力や光の全体の演出の後、絵が画面に残った | 版2で直した。ゲームの絵は最後のコマが空で、残った絵は見えない作りだった。空のコマを足した写しを `out\attack_animations\art\` に作って出す。消しても次の起動で作り直す |

---

## 2. 分かったこと（`docs\GAME.md` 相当）

- 演出は `BattlePhaseManager.resolve_battle_effect` が `battle_action["vfx"]` を読んで決める。
  exe の定数で読めた値と絵のフォルダの対応は次のとおり（`resolve_opponents` の中）

  | vfx | 絵のフォルダ |
  |---|---|
  | slash | `Assets/effects/battle_dark_/slash1` |
  | blow | `Assets/effects/blow` |
  | fire / fire_aoe | `battle_dark_/fire2` / `battle_dark_/fire_aoe3` |
  | ice / ice_aoe | `battle_dark_/mix_ice_earth1` / `battle_dark_/ice_aoe1` |
  | thunder / thunder_aoe | `Thunder_480p` / `Thunder_aoe` |
  | earth | `battle_dark_/earth3` |
  | water | `485.png_(64x64)` |

- 審判の型（`referee_player_attack_new_new` などの `<locals>.Structure`）の `vfx` は
  slash / fire / fire_aoe / ice / ice_aoe / thunder / thunder_aoe / earth の8つの Literal。
  `blow` と `water` は処理があるのに選択肢に無いので、素のゲームでは出ない。
  `referee_member_new_new` の定数には slash / fire / fire_aoe / ice / ice_aoe の5つしか並んでいなかった（型としては未確認）
- 自由入力の審判（`referee_player_any_input_new_new`）は、送り口に `<locals>.Structure` を渡した後、答えを関数の中の
  `FormattedStructure`（同じ8つの Literal。局所のクラスで、モジュールの属性ではない）で作り直す（`llm_manager_battle.py:821`）。
  送り口の型だけ広げると、足した値（`arcane`）がそこで `ValidationError` になり、手番のスレッドが落ちる（2026-10-21 の実機。`319_` の防御の手番）。
  モジュールの `FormattedStructure` を替えても届かなかった（実機）
- `out\*.jsonl` に残っていた審判の戻り 6230 件のうち、slash が 5415 件（87%）
- `Assets/effects` の絵のフォルダ 75 個のうち、ゲームが使うのは上の 10 個だけ。
  武器・体の攻撃に使える絵は slash1・slash2・blow（どれも 64x64・8〜10 コマの白い筋と粒）しか無い
- 効果音は `SoundManager.play_sound(app, sound_src)` の2つ目に名前を渡して鳴らす。名前の表は クリック / ダメージ1〜3 / 斬撃 / 打撃 / 炎（小・大）/ 雷（小・大）/ 氷（小・大）/ 接敵（通常・ボス）/ 取引開始。
  戦闘の絵と音は `resolve_battle_effect` の中で Clock に積まれ、少し後に出る（`235_probe_sound_effects` の呼び出し元が `<lambda>`）。
  実測では手から約0.4秒後
- 単体の絵は `InstantaleApp.effect(src, width=192, height=192, pos_hint={敵の位置})` → `InstanTaleHUD.start_effect`、
  全体の絵は `InstantaleApp.window_effect` → `InstanTaleHUD.start_window_effect` を通る（全体は1手に1回）。
  場面の切り替え（`simple_fadeout` / `frames_gyaku`）と雷の全体（`Thunder_aoe`、960x960）も window_effect
- 絵は `NearestNeighborImage` で、フォルダのコマを名前順に約 0.07 秒ずつ出す。拡大は最近傍なので、64 の絵はドット絵のように粗く出る。
  絶対パスのフォルダも再生できる
- ダメージの無い手（強化・弱体化だけ）と、敵が味方を打つ手（`character_side` が `敵側`）には絵を出さない
- 全体の絵（window_effect）は、再生が終わっても部品を外さず最後のコマを出したまま残る
  （2026-10-21 の実機。場面の切り替えの `frames_gyaku\frame_029.png` が不透明度1のまま残っていた）。
  ゲームが使う絵は最後のコマが空なので見えない。1体の絵の部品は再生の後に残っていなかった。
  ゲームに在って使われていない絵のうち、この MOD が使う9つ（`fire_aoe4`、`battle/` の `wind2`・`wind_aoe2`・`thunder_aoe2`・`wisp1`・`wisp_aoe1`・`neutral_energy1`・`neutral_energy_aoe1`・`water_aoe1`）は最後のコマに絵がある。
  `battle_dark_` の下の絵は、最後に空の `image_end.png` を置いたものが多い

---

## 3. 確かめたことと未確認項目

2026-10-20、テストワールドで依頼3件（Claude、136 で Haiku 5.5）。主人公の剣は試しの間だけ名前と説明を拳銃にし、終わってから控えへ戻した。

| 確かめたこと | 結果 |
|---|---|
| 審判（`referee_player_attack_new_new` / `referee_npc`）に32択の型が届く | 成立。戻りに足した値が入る |
| 拳銃を持った主人公の通常攻撃で gun_shot を選ぶ | 成立。主人公の手は全部 gun_shot |
| 仲間の手で足した値を選ぶ | thrown_weapon・earth_aoe・light_aoe が出た |
| 単体の差し替え（slash の運び手） | 成立。銃撃・投擲の絵が敵の位置に 240x240 で出た（写真で確認） |
| 全体の差し替え（fire_aoe の運び手） | 成立。earth_aoe が window_effect で1回差し替わった |
| 効果音の差し替え | 成立。斬撃 → 雷（小） |

版2（2026-10-21）: 自由入力の審判（`NARROWED_MANAGERS`）の答えだけ、送り口から返る所で足した値を運び手（ゲームが描ける `blow` / `water` は slash / ice）に置き換えて控え、
`resolve_battle_effect` の入口で控えから元の値に戻すようにした（控えは使い切り、`PENDING_SECONDS` で捨てる）。
`tools\tests\test_wip_attack_animations.py` に7件（pydantic のある環境で）。
実機（テストワールド、衛兵との戦闘）: 直す前は `319_` の防御の手番で `arcane` が元の型に落ち、敵の手番に進まなかった。
直した後は防御の答えの `blow` が `slash` に置き換わって返り（`goes back to the game's choices`）、演出を決める所で `blow` に戻ってゲームが描き、手番が進んで戦闘が終わった。`stall guard` の行は出ていない。

同じ日の午後（テストワールド、Claude）: 入力欄に直に書いた魔法の行動で、自由入力の審判が `arcane_aoe` を返した。
`fire_aoe` に置き換えて返り、演出を決める所で戻して、絵（`battle/neutral_energy_aoe1`）と音（炎（大）→ 雷（大））を差し替えた。
その絵の最後のコマが次の全体の演出まで画面に残った（本人の指摘。上の「分かったこと」）。
空のコマを足した写しを渡すようにした後は、全体の部品が `image_end.png` で止まり、写真にも残っていない。
`tools\tests\test_wip_attack_animations.py` に写しの8件を足した。
仲間の手で `blow` を選んだ回は、`blow: the game draws it` の後にゲームの `InstantaleApp.effect` が `Assets/effects/blow` で呼ばれた（記録だけ。写真は撮っていない）。

| 未確認 | 確かめ方 |
|---|---|
| ローカル LLM（grammar）で足した値を選ぶ | ローカルで戦闘をして、ログの `carries it` の行 |
| `blow` / `water` をゲームが描く画面（`blow` は記録で描く関数が呼ばれたところまで。`water` は未） | 味方の手で選ばれたときの画面（敵の手では描かれない） |
| 最後のコマに絵が残る9つの絵のうち、`arcane_aoe` 以外の写しで画面に残らないか | 選ばれたときに、全体の部品の `source` が `image_end.png` で止まるか |
| 10-20 夕方に足した6種（光の刃・鞭・連射・衝撃波・毒・毒の全体）を審判が選ぶか | 実機の戦闘のログ。毒の全体は 128x128 の絵を全体の大きさで出す（他の新規追加絵は 64x64） |
| 控えめの画風の実機での見え方（実機で見たのは、はっきりの銃撃・投擲だけ） | 設定を控えめのまま戦闘をして撮る |
| 新規追加絵のうち、銃撃・投擲以外の見え方 | 選ばれたときの画面。絵の再生は約 0.07 秒ずつなので、外からの写真ではなくゲームの中で `NearestNeighborImage` の `source` を見張って撮る |
| `vfx` を運び手に書き換えたまま後ろへ流してよいか（戦闘の文・記録への影響） | 3件では戦闘の文に崩れは見えなかった。`222_probe_battle_mechanics` の記録は運び手の値になる |
