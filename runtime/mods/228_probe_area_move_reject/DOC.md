# `228_probe_area_move_reject`: プローブ: エリア移動の拒否が何を読むか

`913_area_move_with_party` の版1（友好度）と版2（`Character.state`）が実機で外れたので、
当て推量をやめて、判定の窓の間だけ読まれる側に印を付けて録る。

`AreaMoveManager.execute` に入ったら、`app`・移動のマネージャ・プレイヤー・同行者の `Character` を
属性読みを記録する派生クラスへ `__class__` で差し替え、
同行者の `relationship` と `app.party` / `original_party` / `world.characters` を鍵読みを記録する dict / list 派生に差し替える。
`area_move_rejector` か `elapse_days` が呼ばれた時点で記録を止め、窓を抜けるとき全部元に戻す。
セーブには何も書かない。

## 出力

`out\area_move_reject.log`。`window:` から `window closed` まで、読まれた順に1行ずつ。
`>> area_move_rejector called` の直前に並ぶ行が分岐の材料。

## 実機の記録（2026-09-10 16:25、ヴェスティア、同行者 103 残響のエリオス）

48 読みで `rejector` へ。分岐の材料は #33〜#40:

```text
#32  manager   .method_1()
#33  app       .party = {...}
#34  app.party .items()
#35  npc103    .relationship = {'player': {'affinity': 68, ..., 'relationship': ['同行中'], ...}}
#36  npc103.rel        ['player']
#37  npc103.rel.player ['relationship']
#38〜#40  同じ3行がもう一度
#41  app       .display_button_load()
#42  npc103    .life_log
#46  npc103    .current_log
>> area_move_rejector called
```

- 読んでいるのは `relationship["player"]["relationship"]` の配列だけ。友好度・`state`・`initial_location`・`original_party`・`quest_party_accompany_backgrounds` は読まない
- 2回読むのは2条件（`家族` と別の値）の可能性。値そのものは分岐からは分からない
- それより前（#1〜#31）は `execute` 側で、`gold`・`current_area`・`world_dict`・ボタンの表示。拒否の分岐は `method_1` の中

`913_` 版3はこの記録から `['家族']` を置く形にした。

## 困ったとき

| 症状 | やること |
|---|---|
| `cannot spy app` が出る | `InstantaleApp` の `__class__` 差し替えができない環境。`app` の読みだけ欠けるが、同行者側の読みは残る |
| 読みが多すぎる | `NOISE` に属性名を足す。連続する同じ読みは畳んである |
