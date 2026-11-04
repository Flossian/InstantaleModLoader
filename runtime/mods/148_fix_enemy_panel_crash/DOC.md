# `148_fix_enemy_panel_crash`: 戦闘中に持ち物を開いてもゲームが落ちない

戦闘の処理中（敵の画像の生成中や、選択肢が「…」の間）に持ち物を開け閉めすると、ゲームが落ちることがある。

この MOD を入れると、同じ操作をしても落ちない。
持ち物を閉じると、敵の欄は最新の内容で表示される。

- 設定は無い

## 困ったとき

| 症状 | 見るところ |
| --- | --- |
| 戦闘中に持ち物を開くとまだ落ちる | `crash_log.txt` の最後が `update_enemy_display` の `ZeroDivisionError` か。`out\enemy_panel_crash.log` に `updated while folded` の行があるか |
| 持ち物を閉じても敵の欄が古いまま | 同じログに `redrew the enemy panel` の行があるか。次の手の後には素のゲームの更新でも直る |
