# シェルスクリプトの共通部分。ほかのシェルスクリプトが `.` で読む（直には走らせない）。
#
# 決めるもの:
#   DRIVE  このフォルダ        ROOT  リポジトリ        OUT  out/drive（結果と控えの一時置き場）
#   DATA   ゲームのデータの場所（IML_INSTANTALE_DATA、無ければ %LOCALAPPDATA%\Darmabeko\Instantale）
# 使う環境変数（スクリプトごとに要るものだけ）:
#   IML_DRIVE_WORLD   遊ぶ世界の saves\ の下のフォルダ名（世界の一覧の札にもこの名前が出る前提）
#   IML_GAME_DIR      instantale.exe の在るフォルダ（設定画面が MOD の道具に渡すのと同じ名前）
#   IML_DRIVE_BACKUP  relaunch.sh restore が戻す控え（backup.sh が作ったフォルダ）
DRIVE=${DRIVE:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}
ROOT=$(cd "$DRIVE/../.." && pwd)
OUT="$ROOT/out/drive"
mkdir -p "$OUT"
if [ -n "$IML_INSTANTALE_DATA" ]; then
  DATA=$(cygpath -u "$IML_INSTANTALE_DATA" 2>/dev/null || echo "$IML_INSTANTALE_DATA")
else
  DATA="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")/Darmabeko/Instantale"
fi

# 手順を1本流す。引数は drive.py にそのまま渡す。
drive() { PYTHONIOENCODING=utf-8 python "$DRIVE/drive.py" "$@" 2>&1; }

# 鍵と値の組から JSON を作る（値は JSON として読めればその型、読めなければ文字列）。例: jarg text 出る
jarg() {
  PYTHONIOENCODING=utf-8 python -c '
import json, sys
pairs = sys.argv[1:]
out = {}
for key, value in zip(pairs[0::2], pairs[1::2]):
    try:
        out[key] = json.loads(value)
    except ValueError:
        out[key] = value
print(json.dumps(out, ensure_ascii=True))' "$@"
}

# 起動の段を1行（ready.py）。
stage() { drive ready.py --wait 8 --args "$(jarg world "$IML_DRIVE_WORLD")" | grep -m1 "^STAGE"; }

need() {   # need 変数名 …: 空なら止める
  for name in "$@"; do
    eval "value=\${$name}"
    [ -n "$value" ] || { echo "ERROR: set $name (see tools/drive/README.md)" >&2; return 1; }
  done
}

# 起動の段が $1 になるまで待つ（1秒ごとに見る。上限 $2 回）。relaunch.sh / reload.sh が使う。
wait_stage() {   # $1 = 待つ段、$2 = 上限（回）
  i=0
  while [ $i -lt "$2" ]; do
    s=$(stage); case "$s" in "STAGE $1"*) echo "$s"; return 0;; esac
    i=$((i + 1)); sleep 1
  done
  echo "timeout waiting for $1 (last: $s)"; return 1
}
