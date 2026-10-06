#!/bin/sh
# ゲームを閉じて起動し直し、世界を読み込んで遊べる画面まで進める。決め打ちの待ちはせず、段ごとに状態を見る。
# 使い方: sh tools/drive/relaunch.sh [restore]
#   要るもの: IML_GAME_DIR・IML_DRIVE_WORLD（restore なら IML_DRIVE_BACKUP も）
#   restore を付けると、起動の前にセーブ・世界の骨格・state\ を控え（backup.sh が作ったもの）へ戻す。
#   控えの後にできた立ち絵のフォルダと state\ のファイルは消さず、out/backup/test_leftovers/ へ移す。
. "$(dirname "$0")/common.sh"
need IML_GAME_DIR IML_DRIVE_WORLD || exit 1
GAME=$(cygpath -u "$IML_GAME_DIR" 2>/dev/null || echo "$IML_GAME_DIR")
t0=$(date +%s)
stamp() { echo "[$(( $(date +%s) - t0 ))s] $*"; }

taskkill //F //IM instantale.exe >/dev/null 2>&1
while tasklist //FI "IMAGENAME eq instantale.exe" 2>/dev/null | grep -qi instantale.exe; do sleep 0.5; done
stamp "stopped"

if [ "$1" = "restore" ]; then
  need IML_DRIVE_BACKUP || exit 1
  B=$(cygpath -u "$IML_DRIVE_BACKUP" 2>/dev/null || echo "$IML_DRIVE_BACKUP")
  S="$DATA/saves/$IML_DRIVE_WORLD"
  W="$DATA/worlds/$IML_DRIVE_WORLD"
  LEFT="$ROOT/out/backup/test_leftovers"
  cp -p "$B/save/savedata.json" "$S/savedata.json"
  if [ -f "$B/world/world_data.json" ]; then
    cmp -s "$B/world/world_data.json" "$W/world_data.json" || { cp -p "$B/world/world_data.json" "$W/world_data.json"; echo "restored world_data.json"; }
    find "$W/characters" -mindepth 1 -maxdepth 1 -type d -newer "$B/world/world_data.json" | while read -r d; do
      mkdir -p "$LEFT/characters"; mv "$d" "$LEFT/characters/$(basename "$d")_$(date +%H%M%S)" && echo "moved portrait $(basename "$d")"; done
  fi
  cd "$ROOT" || exit 1
  for f in $(cd "$B" && find state -type f); do cmp -s "$B/$f" "$f" || { mkdir -p "$(dirname "$f")"; cp -p "$B/$f" "$f"; echo "restored $f"; }; done
  find state -path state/models -prune -o -name "${IML_DRIVE_WORLD}*" -type f -newer "$B/save/savedata.json" -print | while read -r f; do
    [ -f "$B/$f" ] && continue
    mkdir -p "$LEFT/$(dirname "$f")"; mv "$f" "$LEFT/${f}_$(date +%H%M%S)" && echo "moved new state $f"; done
fi

# 入出力を切り離して起動する。切り離さないと、この台本の出力をパイプへ流したとき、ゲームが流し先を握って台本が終わらない。
(cd "$GAME" && ./instantale.exe </dev/null >/dev/null 2>&1 &) </dev/null >/dev/null 2>&1
stamp "launched"

# 注入: 通るまで試し直す（ゲームの Python が立ち上がる前は失敗する）
n=0
until (cd "$ROOT" && timeout 60 python tools/injector.py >/dev/null 2>&1); do
  n=$((n + 1)); [ $n -ge 60 ] && { stamp "inject gave up"; exit 1; }; sleep 2
done
stamp "injected"

wait_stage title 90 >/dev/null || exit 1
drive clicktext.py --wait 30 --args "$(jarg texts '["開始する"]')" >/dev/null
stamp "title -> 開始する"
wait_stage worlds 60 >/dev/null || exit 1
drive clicktext.py --wait 20 --args "$(jarg texts "[\"$IML_DRIVE_WORLD\"]" contains true)" >/dev/null
stamp "world picked"
s=$(wait_stage playing 120) || { echo "$s"; exit 1; }
stamp "$s"
