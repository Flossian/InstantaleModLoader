# `219_probe_crash_log`

本体のクラッシュ記録が壊れる退行を見張る。以前は注入がゲームの `__main__.datetime` を上書きしていて、`make_crash_log` が `AttributeError: module 'datetime' has no attribute 'now'` で落ち、`crash_log.txt` も送信も走らなかった（原因はこちら側で、直してある）。記録を組む呼び出しごとに、呼ばれたスレッド・`title`・例外の連鎖の数・そのときの `__main__.datetime`・呼び出し元の経路・呼び出しの起点に MOD が居るか（`orig` をそのまま呼ぶだけの包みは数えない）を控え、後段の書き込みと送信が走ったかも控える。また落ちたときは、`datetime` を差し替えて素の関数でもう一度組ませた結果も控える。やり直しの結果は捨てて元の例外を投げ直すので、ゲームの挙動は変わらない
