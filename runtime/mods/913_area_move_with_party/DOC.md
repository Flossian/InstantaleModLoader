# `913_area_move_with_party`: 雇った仲間がエリア移動を拒まなくなる（開発中）

この MOD は**開発中（9xx）**。git には入れるが、CI・配布物・`load_order.json`・
`docs\` の文書には入らない（TECH.md §2.6）。
理由は**実機が1回**で、セーブ→ロードのまたぎと `326_` との併用が未確認なこと（§3）。
遊び方も検証の記録もこの1枚にまとめてあり、リリースで正式な番号へ振り直すとき各節を元の場所へ戻す。

| ここにある節 | 戻す先 |
|---|---|
| 1. 遊び方・困ったとき | この1枚のまま。`load_order.json`（`326_npc_travel` の次）に載せれば `docs\MODS.md` に載る |
| 2. 検証の一覧に載せる行 | `docs\VERIFICATION.md` §1 の `3xx` の表 |
| 3. 検証の記録・未確認項目 | `docs\VERIFICATION.md` §3 の末尾（節番号は振り直す） |
| 4. ゲーム側の事実 | `docs\GAME.md` §2.18 の末尾（`area_move_rejector` の1行を置き換える）。`228_` の DOC.md も同じ節へ |

`tools\tests\test_wip_area_move_with_party.py` も同じ理由で CI の外。

---

## 1. 遊び方（`docs\MODS.md` 相当）

### `913_area_move_with_party`: 雇った仲間がエリア移動を拒まなくなる

素のゲームは、雇った NPC を連れて「他の土地へ行く」と同行を拒まれる。
拒否の一言（AI が書く）が出るだけで、移動も日数も運賃も動かない。
一緒に移動できるのは「家族になろう」で家族になった相手だけ。
この MOD を入れると、誰を連れていても移動できる。
拒否の一言は出ない。
家族になる経路や雇用の値段は変えない。

設定は無い。
本体の分岐は関係の配列（`同行中` か `家族` か）しか見ていないので、
条件を緩める（友好度いくつ以上なら同行、など）形にするなら MOD 側で自前の条件を持つことになる。

## 困ったとき

| 症状 | やること |
|---|---|
| まだ拒まれる | `out\area_move_party.log` の `WARN rejected:` の行。出ていれば本体の分岐は友好度を見ていない（§3 の未確認 1）。出ていなければ MOD が効いていない（`out\modloader.log` の `applied`） |
| 移動の後で友好度が変わった | 同じログの `WARN restore` の行。移動の最中に本体が友好度を書き換えたときだけ出る |

---

## 2. 検証の一覧に載せる行（`docs\VERIFICATION.md` §1 相当）

| MOD | 何を確かめるか | 状態 |
|---|---|---|
| `913_area_move_with_party` | 雇用 NPC を連れてエリア移動できる。移動の前後で関係の配列が元に戻り、セーブにも残らない | 版3 実機1回で成立（版1・2は外れ） |

---

## 3. 検証の記録・未確認項目（`docs\VERIFICATION.md` §3 相当）

版1（2026-09-10）: 友好度（`relationship.player.affinity`）を 999 にする形。**実機1回で外れ**。
友好度 68（仲間だと感じている）の同行者を 999 にしても拒まれた（`out\area_move_party.log` 14:41:52 `lift: 103 ... 68 -> 999` → `rejector:` → `WARN rejected:`）。
分岐は友好度を見ていない。
同じ回で、拒否の途中に本体が保存し、`savedata.json` の相手の `affinity` に 999 が残った（実行時の値は戻していたのに）。
本体は保存のときに Character の値を `save_data_dict["npcs"]` へ写してから書くので、戻す前に写されると残る。
版2は `save_game` の直前にも戻し、`save_data_dict` 側に写った値も戻す。

版2（2026-09-10）: `Character.state` を `家族` にする形。**実機2回で外れ**（16:21・16:22、`state='家族'` のまま `rejector`）。
`save_game` の前で戻す口は効いた（`restore (save_game)` が `WARN rejected:` より前）。

`228_probe_area_move_reject` の実機記録（16:25、`out\area_move_reject.log`）で分岐の材料が決まった。
`execute` → `method_1` が `app.party.items()` を回し、同行者の `relationship["player"]["relationship"]`（`['同行中']`）を2回読んで `area_move_rejector` へ行く（#33〜#40）。
友好度・`state`・`initial_location`・`original_party` は読んでいない。

版3（2026-09-10）: この配列を判定の間だけ `['家族']` に置き換える形。
**実機1回で成立**（16:43、残響のエリオス 103 を連れてラスト・リフレクション → 陽光の港、馬車）。
`lift: 103 relationship ['同行中'] -> ['家族']` → `228_` の `>> elapse_days(14) (the branch passed)` → `restore (elapse_days(0))`。
`WARN` は無し。戻した後の 16:25 の保存で 103 の `affinity` は 68、配列は `['同行中']`（版1の 999 は後の保存で消えた）。
`restore` の行が `elapse_days(0)` なのは、窓の中で最初に通る `elapse_days` が別の MOD か本体の 0 日で、`217_` が見る 14 日より前に戻っているということ。
`家族` の値は exe の定数表の並び（`家族` → `パーティに入れる` → `FamilyPartyJoinManager`、`二人は家族になった。`）から。

**素のゲームの条件は実データで確定した**（16:38、`228_` の記録）。
配列が `['同行中', '同行中', '家族']` の同行者（テラ 45、実データで家族にした相手）を連れた移動は通った（`>> elapse_days(14) (the branch passed)`）。
`['同行中']` の同行者（残響のエリオス 103、16:21・16:22）は拒否。
`同行中` が残っていても `家族` が入れば通るので、判定は「配列に `家族` を含むか」。
版3が置く `['家族']` はこの条件を満たす。

| # | 確かめること | どう分かるか |
|---|---|---|
| 1 | 本体の分岐が配列の `家族` で通る | 済（16:43）。雇用 NPC を連れて移動する。`out\area_move_party.log` に `lift:` → `restore (elapse_days(14))` が出て、`217_` の `out\area_move.log` が `days=[14]` で `辿り着いた。`。**`WARN rejected:` が出たら外れ**。そのときは `228_` を入れて `out\area_move_reject.log` の `>> area_move_rejector` の直前の行を見る（配列の何と比べているかは値からは分からないので、`['家族', '同行中']` など置く値を変えて試す） |
| 2 | 配列が戻っている | 済（16:25 の保存で `同行中`。16:43 の後の保存は未確認）。移動の後に会話で「パーティに入れる」ではなく「ここで別れる」系が出る。セーブエディタで `relationship.player.relationship` が `同行中` のまま。`WARN restore` と `save_data_dict copied` が無い |
| 3 | 所持金不足のとき | `金が足りない...` で止まり、`restore (execute done)` が出る |
| 4 | `326_` との併用 | 移動で日数が進んだときの旅立ちの判定に `['家族']` が見えていない（`restore (elapse_days(...))` が `326_` の行より前） |
| 5 | 版1の残り | 済。16:25 の保存で 68 に戻っていた |

判定に使ったログ: `out\area_move.log`（`217_`）の 2026-08-19T20:10:29 と 2026-09-02T03:16:18 と 2026-09-10T14:41:56 の3件が素の拒否。
どれも `gold` 不変・`days=[]`・`texts=1`（AI の一言のみ）で、`send_request_with_no_structure('area_move_rejector')` の直後に出ている。

---

## 4. ゲーム側の事実（`docs\GAME.md` §2.18 相当）

- 同行者による拒否は `AreaMoveManager.execute` の中、運賃と `elapse_days` より前。
  拒否の一言は `llm_manager:area_move_rejector(character_life_log, player, character_instance, worldview)` が AI に書かせる（2件の実測とも約 2〜4 秒）。
  頼み文は「クエスト一回限りの条件で雇用された NPC」「月単位の時間を要する」「関係性が深くないためにシステム的に拒否されるべき」の3点で固定
- 拒否のあと本体は `current_log` に2つ書く（exe の定数表。`AreaMoveManager.method_1` の並び）:
  NPC 側 `<会話: 雇い主の〈PC〉が遠い'〈エリア〉'エリアへの移動を試みたので、それには付き合えないことを伝えた。>`、
  PC 側 `<会話: 遠い'〈エリア〉'エリアへの移動を試みたところ、雇用している〈NPC〉にその同行を拒否された。>`
- 分岐が読むのは同行者の `relationship["player"]["relationship"]` の配列だけ（`228_` の実機記録。§3）。友好度も `state` も読まない。雇用 NPC は `['同行中']`
- 家族: 会話の「家族になろう」（`conversation_become_family_response`）で `二人は家族になった。` と書き、以後は「雇いたい」の代わりに「パーティに入れる」（`FamilyPartyJoinManager`、雇用の値段なし）が並ぶ。印は `relationship.player.relationship` に `家族` が入ることと読んでいる（定数表の並び。版3で確認中）。`Character.state` は雇用 NPC で `""` か `None` で、分岐は読まない（版2で外れ）
- 定数表で `area_move_rejector` の近くに並ぶ属性名は `target_area` / `target_area_dict` / `mode` / `show_loading_finished` だけで、`relationship` や `party` は畳まれていて位置から辿れない。読み手の側に印を付けて録る（`228_`）ほうが早い
- 保存は Character の値を `save_data_dict["npcs"]` へ写してから書く。拒否の途中でも保存が走る（版1で 999 が残った）
