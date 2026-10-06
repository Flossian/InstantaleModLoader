#!/bin/sh
# ゲームを閉じずに題の画面へ戻り、同じ世界を読み直して遊べる画面まで進める。段ごとに状態を見て待つ。
# 使い方: sh tools/drive/reload.sh      要るもの: IML_DRIVE_WORLD
#   ゲームは行動のたびに保存しているので、戻る前の状態がそのまま読み直される。
#   保存とロードをまたぐ確認（ボタンの添え字・好感度が残るか）に使う。注入し直しも起動し直しもしない。
. "$(dirname "$0")/common.sh"
need IML_DRIVE_WORLD || exit 1
drive totitle.py | grep "^T"
wait_stage title 60 >/dev/null || exit 1
drive clicktext.py --wait 30 --args "$(jarg texts '["開始する"]')" >/dev/null
wait_stage worlds 60 >/dev/null || exit 1
drive clicktext.py --wait 20 --args "$(jarg texts "[\"$IML_DRIVE_WORLD\"]" contains true)" >/dev/null
wait_stage playing 120
