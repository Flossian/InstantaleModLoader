# 対話で使う短い関数。`. tools/drive/nav.sh` で読む（Git Bash）。
#   p 文字            画面の選択肢を押す（押す前の画面を控え、押せなければ印を残す）
#   w 文字 [回]       その選択肢が並ぶまで待つ（直前の p が押せなかったら待たない。別の画面で落ち着いたら OTHER）
#   s '{json}'        遊びの状態を書き換える（setstate.py。law / gold / elapse / save）
#   m 部分 定数 値    MOD の定数を書き換える（setmod.py）。例: m 314_ COACH_PRICE 50
#   d                 画面とデータの選択肢を並べる（choices.py）
#   here [id …]       今いる所と日数、並べた人物の居場所・好感度（where.py）
#   a 文              入力欄に書いて送り、返事を待つ（act.py。会話の発言・自由行動）
#   tx [語 …]         画面の本文から語を含む行を拾う（texts.py。写真に写らない上の行も）
#   shot 名前         窓の写真を out/drive/<名前>.png に撮る
DRIVE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$DRIVE/common.sh"
p() {
  stage > "$OUT/before.txt"
  r=$(drive press.py --args "$(jarg text "$1")" | grep "^press"); echo "$r"
  case "$r" in *"-> True") rm -f "$OUT/pressfailed";; *) touch "$OUT/pressfailed"; return 1;; esac
}
w() {
  [ -f "$OUT/pressfailed" ] && { echo "skip wait: the last press failed"; return 1; }
  sh "$DRIVE/waitchoice.sh" "$1" "${2:-90}" | cut -c1-300
}
s() { drive setstate.py --wait 60 --args "$1" | grep "^S " | cut -c1-300; }
m() { drive setmod.py --args "$(jarg mod "$1" name "$2" value "$3")" | grep "^V "; }
d() { drive choices.py | grep "^D " | cut -c1-400; }
here() { drive where.py --args "$(PYTHONIOENCODING=utf-8 python -c 'import json, sys; print(json.dumps({"npcs": sys.argv[1:]}))' "$@")" | grep "^[WN] "; }
a() { drive act.py --wait 600 --args "$(jarg text "$1")" | grep -v "^input boxes\|^send buttons\|^<done>"; }
tx() { drive texts.py --args "$(PYTHONIOENCODING=utf-8 python -c 'import json, sys; print(json.dumps({"contains": sys.argv[1:]}, ensure_ascii=True))' "$@")" | grep "^X "; }
shot() { powershell -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$DRIVE/shot.ps1")" -Path "$(cygpath -w "$OUT/$1.png")"; }
