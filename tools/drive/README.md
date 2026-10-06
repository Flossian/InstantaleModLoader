# tools\drive: 動いているゲームを外から操作する道具（開発用）

実機で MOD を確かめるときに、ゲームの操作をコマンドで行うための道具。
起動し直す・選択肢を押す・画面が変わるのを待つ・所持金などを書き換える・写真を撮る、をマウスの代わりに Git Bash から行い、結果を文字で読む。

配布物には入らない（`make_dist.bat` が tools から写すのは名前を挙げたファイルだけ）。
CI は構文（compileall）だけを見る。ゲームが動いていないと使えない。

## ファイルは3種類

| 種類 | ファイル | 動かし方 | 動く場所 |
|---|---|---|---|
| シェルスクリプト | `relaunch.sh` / `backup.sh` / `reload.sh` / `waitchoice.sh` / `nav.sh` | `sh tools/drive/relaunch.sh`。`nav.sh` だけは `. tools/drive/nav.sh` で読み込む | ゲームの外（Git Bash） |
| ゲーム内スクリプト | `press.py` / `where.py` / `setstate.py` など、`def main(say):` を持つ `.py` | `python tools/drive/drive.py press.py --args '{"text": "出る"}'` | ゲームの中 |
| 外部ツール | `toolshot.py` / `modsettings.py` / `records.py` | `python tools/drive/toolshot.py …` | ゲームの外 |

ふだんは `nav.sh` の短いコマンド（`p` / `w` / `d` など）がゲーム内スクリプトを呼ぶので、`drive.py` を直に打つことは少ない。

### ゲーム内スクリプトが動く仕組み

`drive.py` は、ローダの注入（`tools/injector.py`）と同じ方法で、ゲーム内スクリプトをゲームのメインスレッドで1回動かす。
結果はゲーム内スクリプトが `out/drive/` に書いたファイルから受け取り、画面に出す。
押す操作は本物のマウス入力と同じ経路（`MouseMotionEvent`）を通るので、人が押したときと同じ処理が動く。
選択肢の関数を直に呼ぶことはしない。

## 準備（最初に1回、Git Bash で）

```sh
export IML_GAME_DIR='<instantale.exe の在るフォルダ>'
export IML_DRIVE_WORLD='<試す世界のフォルダ名>'
export PYTHONIOENCODING=utf-8
```

| 変数 | 中身 | 使うもの |
|---|---|---|
| `IML_GAME_DIR` | `instantale.exe` の在るフォルダ | `relaunch.sh` |
| `IML_DRIVE_WORLD` | セーブの `saves\` の下の世界のフォルダ名。世界の一覧でこの名前の札を押す | `relaunch.sh` / `reload.sh` / `backup.sh` |
| `IML_DRIVE_BACKUP` | `backup.sh` が作った控えのフォルダ。控えを取った後に入れる | `relaunch.sh restore` |
| `IML_INSTANTALE_DATA` | ゲームのデータの場所。無ければ `%LOCALAPPDATA%\Darmabeko\Instantale` | 全部（ふだんは要らない） |

作業フォルダの場所に ASCII でない字が入っていると、`drive.py` は止まる（注入で渡せるのが ASCII だけのため）。

## 試すときの流れ

```sh
sh tools/drive/backup.sh 試しの名前       # 1. 控えを取る。最後の行に控えの場所が出る
export IML_DRIVE_BACKUP='<その場所>'
sh tools/drive/relaunch.sh                # 2. ゲームを起動し直し、世界を読み込んで遊べる画面まで進める
. tools/drive/nav.sh                      # 3. 操作のコマンドを読み込む
p 出る && w '<次の画面の選択肢>' 30       #    「出る」を押し、次の画面の選択肢が並ぶまで待つ
s '{"gold": 1000}'                        #    所持金を 1000 にする
shot 出た後                                #    窓の写真を out/drive/出た後.png に撮る
sh tools/drive/relaunch.sh restore        # 4. 控えへ戻して起動し直す
```

ゲームは行動のたびにセーブを上書きするので、試した結果はそのまま残る。
試し終えたら必ず4で戻す。

## nav.sh のコマンド

`. tools/drive/nav.sh` で読み込むと使える。

| コマンド | すること | 例 |
|---|---|---|
| `p 文字` | 画面の選択肢を1つ押す。押す前の画面を控え、押せなかったら印を残す | `p 出る` |
| `w 文字 [秒]` | その選択肢が並ぶまで待つ（既定 90 秒）。直前の `p` が押せなかったら待たない。別の画面で落ち着いたら `OTHER` を付けて返す | `w やめる 30` |
| `d` | 画面に出ている選択肢と、ゲームのデータ上の選択肢を並べる | `d` |
| `here [人物の id …]` | 今いる所と日数。id を渡すと、その人物の居場所と好感度 | `here 53` |
| `s 'JSON'` | 遊びの状態を書き換える（`setstate.py`）。`law`（土地の id ごとの手配度）/ `gold` / `affinity`（人物の id ごとの好感度）/ `elapse`（日数を進める）/ `save`（保存する） | `s '{"law": {"6": -5}, "save": true}'` |
| `m MOD 定数 値` | 動いている MOD の定数（設定値）を書き換える。MOD はフォルダ名の一部でよい | `m 314_ COACH_PRICE 50` |
| `a 文` | 入力欄に書いて送り、返事を待つ（会話の発言・自由行動） | `a 'いつも助かっている'` |
| `tx 語 …` | 画面の本文から、語を含む行を拾う（写真に写らない上の行も読める） | `tx 剥がされ しくじ` |
| `shot 名前` | 窓の写真を `out/drive/名前.png` に撮る | `shot 会話の後` |

`m` の値は JSON として読み、読めなければ文字列になる。
`None` を入れたいときは `null` と書く（`None` と書くと文字列の `'None'` が入る）。
`m` で変えた値は注入し直すと元に戻る。
注入し直しても残したいときは `modsettings.py` を使う。

## シェルスクリプト

| ファイル | すること |
|---|---|
| `backup.sh [名前]` | セーブ・世界の骨格（`world_data.json`）・`state\` を `out/backup/<名前>_<日時>/` へ写す。立ち絵のフォルダは写さず、名前の一覧だけ控える。`state\models\`（画像生成のモデル、数 GB）は写さない |
| `relaunch.sh [restore]` | ゲームを閉じて起動し直し、注入・題の画面・世界の一覧・遊べる画面を順に待つ。`restore` を付けると、起動の前に控えへ戻す |
| `reload.sh` | ゲームを閉じずに題の画面へ戻り、同じ世界を読み直す（約9秒）。保存とロードをまたいで値が残るかの確認に使う |
| `waitchoice.sh 文字 [秒]` | その選択肢が並ぶまで待つ（`w` の中身）。終了コードは 0 並んだ / 1 時間切れ / 2 ゲームオーバー / 3 別の画面で落ち着いた |
| `nav.sh` | 上のコマンドを読み込む |
| `common.sh` | ほかのシェルスクリプトが読む共通部分。直には動かさない |

`relaunch.sh restore` は、控えの後にできたものを消さずに `out/backup/test_leftovers/` へ移す。
移すのは、控えの時に無かった立ち絵のフォルダと、控えの後にできた `state\` のファイル。

## ゲーム内スクリプト

```sh
python tools/drive/drive.py <ファイル> --args '<JSON>' --wait <秒>
```

| ファイル | ARGS | すること |
|---|---|---|
| `ready.py` | `world` | 今どの段か（title / worlds / playing / gameover / busy） |
| `press.py` | `text` | 画面の選択肢を1つ押す |
| `choices.py` | | 画面とデータの選択肢、手が空いているかの旗 |
| `clicktext.py` | `texts`・`contains`・`pause` | 選択肢の欄の外の文字（題の画面・世界の札）を押す |
| `setstate.py` | `law`・`gold`・`affinity`・`elapse`・`save` | 遊びの状態を書き換える |
| `where.py` | `npcs` | 今いる所・日数・会話の相手・手待ち、並べた人物の居場所と好感度 |
| `texts.py` | `contains`・`last` | 画面の本文から語を含む行を拾う |
| `totitle.py` | | 閉じずに題の画面へ戻る（`reload.sh` が使う） |
| `setmod.py` | `mod`・`name`・`value` | MOD の定数を書き換える |
| `act.py` | `text`・`timeout` | 入力欄に書いて送り、手が空くまで待つ |
| `trade.py` | `do`（open / close / status / prices） | 売買の窓。`prices` は手持ちの品の売値を、値段の計算の段ごとに並べる |
| `sell.py` | `item` | 開いている売買の窓で、手持ちの品を1つ売る |
| `steal.py` | `mode`・`enter` | `336_crime_overhaul` の店で盗む（乱数をその間だけ固定する） |

### ゲーム内スクリプトを足すとき

```python
def main(say):
    say("今の所持金 {}".format(app().player.gold))
```

- `def main(say):` を書く。`say(文字)` で結果に1行書く
- 動かす前に `lib.py` の部品（`app()` / `hud()` / `press_choice()` / `choices()` / `click()` / `find()` / `mod()` など）と、`--args` の辞書 `ARGS` が使える状態になっている
- 何フレームか待ってから結果を書くときは、ファイルに `KEEP_OPEN = True` を置き、終わりに `say("<done>")` を書く
- 一度きりの調べ物は `out/drive/` に置いて動かしてよい（git の外）。続けて使うものだけここに置く

## 外部ツール（ゲームの外で動く）

| ファイル | すること |
|---|---|
| `toolshot.py MOD タブ [--out]` | MOD の設定画面を開き、指定のタブ（名前か番号）を選んで撮る。`check_tool_screens.py` は最初のタブしか撮れない |
| `modsettings.py MOD show / set 名=値 … / reset` | `settings/mod_settings.json` の MOD の欄を書き換える。次の注入から効き、注入し直しても残る。試し終えたら `reset` |
| `records.py manager [-n 件] [--system]` | ゲームが残した LLM の入出力の記録を新しい順に読む。`out/drive/records_<manager>.txt` にも書く |

## 気をつけること

- 遊んでいるセーブで動く。手配度・所持金・仲間・依頼を書き換えた結果は、ゲームが行動のたびに保存する。試した後は `relaunch.sh restore` で戻す
- 控えに立ち絵の画像は入らない。控えの前から居る人物（主人公を含む）の立ち絵を試しで描き直すと、元の絵には戻らない
- 待ちは画面の状態で決める（`w` / `relaunch.sh` / `reload.sh` は画面を見て進む）。決め打ちの `sleep` で待たない
- ゲーム内スクリプトから、ゲームのマネージャの `execute` を直に呼ばない（固まったことがある）。進めたい段は、ボタンを押して進める
- スクリプトで作った状態（会話を経ずに仲間へ入れる、など）は、ふつうに遊んでも辿り着けないことがある。そこで出た不具合は、ふつうの遊び方で同じ状態になるかを先に確かめる
- 設定の額を変えて試すとき、額によってはゲーム自身の処理が先に動き、MOD の処理を通らない（所持金が足りない断りなど）
- 同じ作業フォルダで別のセッションがゲームを動かしていることがある。ゲームが知らないうちに閉じられていたら、まずそれを疑う
- 結果のファイル・写真・押す前の画面の控えは `out/drive/` に置く（git の外）
