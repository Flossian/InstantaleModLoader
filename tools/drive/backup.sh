#!/bin/sh
# 試す前の控えを取る。取った場所を最後の行に出す（relaunch.sh restore の IML_DRIVE_BACKUP に渡す）。
# 使い方: sh tools/drive/backup.sh [名前]      要るもの: IML_DRIVE_WORLD
#
#   out/backup/<名前>_<日時>/save/savedata.json     遊んでいる世界のセーブ
#   out/backup/<名前>_<日時>/world/world_data.json  世界の骨格（ゲームはセーブのたびにここも書く）
#   out/backup/<名前>_<日時>/state/…                state\ のうち models\ を除いたもの
#
# state\models\ は画像生成のモデル（数 GB）で、遊びの状態ではないので写さない（写すと控え1つで十数 GB になった）。
# ゲームは行動のたびに上書き保存するので、取るのはゲームを閉じたときか、手が空いているときにする。
. "$(dirname "$0")/common.sh"
need IML_DRIVE_WORLD || exit 1
B="$ROOT/out/backup/${1:-drive}_$(date +%Y%m%d_%H%M%S)"
SAVE="$DATA/saves/$IML_DRIVE_WORLD/savedata.json"
WORLD="$DATA/worlds/$IML_DRIVE_WORLD/world_data.json"
[ -f "$SAVE" ] || { echo "ERROR: no save at $SAVE" >&2; exit 1; }
mkdir -p "$B/save" "$B/world" "$B/state"
cp -p "$SAVE" "$B/save/savedata.json"
[ -f "$WORLD" ] && cp -p "$WORLD" "$B/world/world_data.json"
(cd "$ROOT/state" && find . -path ./models -prune -o -type f -print) | while read -r f; do
  mkdir -p "$B/state/$(dirname "$f")"; cp -p "$ROOT/state/$f" "$B/state/$f"
done
echo "$B"
