#!/bin/sh
# 選択肢に $1 が並び、手が空くまで待つ（1秒ごとに見る。上限 $2 回、既定 90）。
# 押す前の画面（out/drive/before.txt。nav.sh の p が書く）と違う画面で手が空き、2回続けて同じなら、
# $1 が無くてもそこで返す（終了コード 3、行の頭は OTHER）。遭遇・確認画面など別の画面へ進んだときに待ち続けない。
# 終了コード: 0 並んだ / 1 時間切れ / 2 ゲームオーバー / 3 別の画面で落ち着いた
. "$(dirname "$0")/common.sh"
before=$(cat "$OUT/before.txt" 2>/dev/null)
i=0; last=""
while [ $i -lt "${2:-90}" ]; do
  s=$(stage)
  case "$s" in
    "STAGE playing"*"'$1'"*) echo "after ${i}s: $s"; exit 0;;
    "STAGE gameover"*) echo "after ${i}s: GAMEOVER"; exit 2;;
    "STAGE playing"*) if [ "$s" != "$before" ] && [ "$s" = "$last" ]; then echo "after ${i}s: OTHER $s"; exit 3; fi;;
  esac
  last="$s"; i=$((i + 1)); sleep 1
done
echo "timeout waiting for $1 (last: $s)"; exit 1
