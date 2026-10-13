#!/bin/sh
# ゲームを閉じて起動し直し、世界を読み込んで遊べる画面まで進める。決め打ちの待ちはせず、段ごとに状態を見る。
# 使い方: sh tools/drive/relaunch.sh [restore|title]
#   要るもの: IML_GAME_DIR・IML_DRIVE_WORLD（restore なら IML_DRIVE_BACKUP も）
#   restore を付けると、起動の前にセーブ・世界の骨格・state\ を控え（backup.sh が作ったもの）へ戻す。
#   控えの後にできた立ち絵のフォルダと state\ のファイルは消さず、out/backup/test_leftovers/ へ移す。
#   title を付けると、題の画面が出たところで止め、世界は選ばない（キャラのまだ居ない世界へ自分で進むとき）。
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
    # 移すのは控えの時に無かった立ち絵のフォルダだけ（backup.sh の characters.txt）。
    # 名前の一覧が無い古い控えは、更新時刻で見分ける（控えの前から居る人物を描き直したフォルダも移る）。
    find "$W/characters" -mindepth 1 -maxdepth 1 -type d | while read -r d; do
      if [ -f "$B/world/characters.txt" ]; then
        grep -qxF "$(basename "$d")" "$B/world/characters.txt" && continue
      else
        [ "$d" -nt "$B/world/world_data.json" ] || continue
      fi
      mkdir -p "$LEFT/characters"; mv "$d" "$LEFT/characters/$(basename "$d")_$(date +%H%M%S)" && echo "moved portrait $(basename "$d")"; done
  fi
  cd "$ROOT" || exit 1
  # 戻すのは試した世界の控え（`<世界>.json` と、周回の `<世界>×<主人公>.json`）だけ。
  # 全部を戻すと、控えの後に別のセッションが遊んだ別の世界の控えまで控えの時点へ巻き戻す（2026-10-11 に踏みかけた）。
  # 名前の前方一致にしないのも同じ理由（「テスト」で「テストワールド」の控えまで戻る）。
  # 世界をまたいで1つのファイル（`state\city_case.json` など）は戻さない。別の世界の遊びも入っている。
  # 名前に空白を含む控え（世界×主人公）があるので、find の出力は1行ずつ読む（for で回すと空白で切れる）。
  (cd "$B" && find state -type f \( -name "${IML_DRIVE_WORLD}.json" -o -name "${IML_DRIVE_WORLD}×*" \)) | while read -r f; do
    cmp -s "$B/$f" "$f" && continue
    mkdir -p "$(dirname "$f")" && cp -p "$B/$f" "$f" && echo "restored $f"; done
  find state -path state/models -prune -o -type f \( -name "${IML_DRIVE_WORLD}.json" -o -name "${IML_DRIVE_WORLD}×*" \) -newer "$B/save/savedata.json" -print | while read -r f; do
    [ -f "$B/$f" ] && continue
    mkdir -p "$LEFT/$(dirname "$f")"; mv "$f" "$LEFT/${f}_$(date +%H%M%S)" && echo "moved new state $f"; done
fi

# 入出力を切り離して起動する。切り離さないと、このスクリプトの出力をパイプへ流したとき、ゲームが流し先を握ってスクリプトが終わらない。
(cd "$GAME" && ./instantale.exe </dev/null >/dev/null 2>&1 &) </dev/null >/dev/null 2>&1
stamp "launched"

# 注入: GUI と同じく、ゲームの Python の初期化と窓の出現を待ってから流す（watcher.py --once）。
# 待たずに流すと、初期化の前に PyGILState_Ensure を呼びうる。プロセスがまだ無ければ 1 で返るので試し直す。
n=0
until (cd "$ROOT" && timeout 240 python tools/watcher.py --once >/dev/null 2>&1); do
  n=$((n + 1)); [ $n -ge 60 ] && { stamp "inject gave up"; exit 1; }; sleep 2
done
stamp "injected"

wait_stage title 90 >/dev/null || exit 1
[ "$1" = "title" ] && { stamp "title"; exit 0; }
drive clicktext.py --wait 30 --args "$(jarg texts '["開始する"]')" >/dev/null
stamp "title -> 開始する"
wait_stage worlds 60 >/dev/null || exit 1
drive clicktext.py --wait 20 --args "$(jarg texts "[\"$IML_DRIVE_WORLD\"]" contains true)" >/dev/null
stamp "world picked"
s=$(wait_stage playing 120) || { echo "$s"; exit 1; }
stamp "$s"
