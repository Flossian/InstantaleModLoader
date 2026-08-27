# `001_crash_recorder`: クラッシュの全文を残す

クラッシュ時のトレースバックとフレーム変数を、
省略なしで `out\live_crashes.log` に残す。
不具合を報告するときはこのファイルを見る。

本体自身のクラッシュ記録（`<ゲームdir>\crash_log.txt`）も、今までどおり書かれる。

## 作者のサーバへの送信は止める

本体にはクラッシュの記録を外へ送る段があるが、**注入している間はこれを止める**。
MOD を入れて遊んでいる間の記録にはローダと MOD の枠が混ざっていて、
素のゲームで起きたことの記録にはならないため。
止めたことは `out\modloader.log` に1行残る。

```
[...] crash recorder: not sending the crash log to the server (1635 chars);
      mods are loaded, so it would not be a report about the plain game
```

MOD 無しで起きた不具合を作者へ報告したいときは、
`MOD を外す`（または MOD を入れずに起動）してから再現すれば、本体の送信がそのまま働く。
