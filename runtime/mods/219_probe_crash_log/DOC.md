# `219_probe_crash_log`

本体のクラッシュ記録が落ちる呼び出しを見分ける。`make_crash_log` が `AttributeError: module 'datetime' has no attribute 'now'` で落ちると `crash_log.txt` も送信も走らない。呼ばれたスレッド・`title`・例外の連鎖の数・そのときの `__main__.datetime`・呼び出し元の経路（MOD の枠が挟まっているか）を1件ずつ控える。`datetime` を差し替えてもう一度組ませた結果も控える。やり直しの結果は捨てて元の例外を投げ直すので、ゲームの挙動は変わらない。`131_` の前提の検証用
