# 動いているゲームを外から操作する台本（開発用）

実機の確認で、画面を押す・待つ・遊びの状態を作る・写真を撮るのを、ゲームの外から台本で行うための道具。
配布物には入らない（`make_dist.bat` が tools から写すのは名前を挙げたファイルだけ）。
CI は構文（compileall）だけを見る。ゲームが無いと走らない。

仕組みは `tools/injector.py` の注入と同じ。
`drive.py` が手順を1本、ゲームのメインスレッド（Kivy の Clock）で走らせ、結果をファイルで受け取って標準出力に写す。
押すのは本物の入力（`MouseMotionEvent`）なので、ゲームが実際に押されたのと同じ経路で動く。

## 準備（Git Bash）

```sh
export IML_GAME_DIR='<instantale.exe の在るフォルダ>'    # relaunch.sh が要る
export IML_DRIVE_WORLD='<saves の下の世界のフォルダ名>'   # 世界の一覧の札にもこの名前が出る前提
export IML_DRIVE_BACKUP='<backup.sh が出した控えの場所>'  # relaunch.sh restore が要る
```

ゲームのデータの場所は `IML_INSTANTALE_DATA`、無ければ `%LOCALAPPDATA%\Darmabeko\Instantale`（ローダの `saves.data_dir` と同じ）。
注入の中身は ASCII に限るので、作業場の場所に ASCII でない字が入っていると `drive.py` が止まる。

## 流れ

```sh
sh tools/drive/backup.sh 試しの名前      # 試す前の控え。最後の行の場所を IML_DRIVE_BACKUP に
sh tools/drive/relaunch.sh               # ゲームを閉じて起動し直し、世界を読み込んで遊べる画面まで待つ
. tools/drive/nav.sh                     # 対話用の関数を読む
p 出る && w '<次の画面の選択肢>' 30       # 押して、その選択肢が並ぶまで待つ
s '{"law": {"6": -5}, "gold": 1000}'     # 手配度（土地の id ごと）と所持金を書き換える
m 314_ COACH_PRICE 50                    # MOD の定数（GUI の設定値）を書き換える
here 62                                  # 今いる所と日数、人物 62 の居場所と好感度
a 'いつも助かっている。礼を言わせてくれ'    # 会話で発言する（送って返事を待つ）。閉じるのは p 会話を終了する
tx 剥がされ しくじ                        # 本文から知らせの文を拾う
sh tools/drive/reload.sh                 # 閉じずに題の画面へ戻って読み直す（保存とロードをまたぐ確認）
shot 名前                                # 窓の写真を out/drive/名前.png に
sh tools/drive/relaunch.sh restore       # 試した後、控えへ戻して起動し直す
```

| 台本 | すること |
|---|---|
| `relaunch.sh [restore]` | 閉じて起動し直し、注入・題の画面・世界の一覧・遊べる画面を段ごとに待つ。`restore` で先に控えへ戻す（控えの後にできた立ち絵のフォルダと state\ のファイルは `out/backup/test_leftovers/` へ移す） |
| `backup.sh [名前]` | セーブ・世界の骨格・state\ を `out/backup/<名前>_<日時>/` へ写す。`state\models\`（画像生成のモデル）は写さない |
| `reload.sh` | 閉じずに題の画面へ戻り（`totitle.py`）、同じ世界を読み直して遊べる画面まで段ごとに待つ。ゲームは行動のたびに保存しているので、戻る前の状態が読み直される |
| `nav.sh` | `p` / `w` / `s` / `m` / `d` / `here` / `a` / `tx` / `shot`（中の説明を参照） |
| `waitchoice.sh 文字 [回]` | その選択肢が並ぶまで待つ。押す前と違う画面で落ち着いたら `OTHER` で返す（終了コード 3） |

ゲームの外で走る道具（`python tools/drive/<名前>.py …`）:

| 道具 | すること |
|---|---|
| `toolshot.py MOD タブ [--out]` | MOD の設定画面を組み、指定のタブ（名前か番号）を選んで撮る。`check_tool_screens.py` は最初のタブしか撮れないので、様子のタブなどはこちら |
| `modsettings.py MOD show / set 名=値 … / reset` | `settings/mod_settings.json` の MOD の欄を書き換える。次の注入から効き、注入し直しても残る（`m` は動いている定数だけで、注入し直すと消える）。試し終えたら `reset` |
| `records.py manager [-n 件] [--system]` | ゲームが残した LLM の入出力の記録を新しい順に読む（会話の頼み文に何が載ったか・返答の項目）。`out/drive/records_<manager>.txt` にも書く |

手順（`python tools/drive/drive.py <手順> --args '<JSON>' --wait <秒>`）:

| 手順 | ARGS | すること |
|---|---|---|
| `ready.py` | `world` | 起動の段（title / worlds / playing / gameover / busy） |
| `press.py` | `text` | 画面の選択肢を1つ押す |
| `choices.py` | | 画面とデータの選択肢、手待ちの旗 |
| `clicktext.py` | `texts`・`contains`・`pause` | 選択肢の欄の外の文字（題の画面・世界の札）を押す |
| `setstate.py` | `law`・`gold`・`affinity`・`elapse`・`save` | 遊びの状態を書き換える（`affinity` は `{人物の id: 好感度}`） |
| `where.py` | `npcs` | 今いる所・日数・会話の相手・手待ち、並べた人物の居場所と好感度・感情の文 |
| `texts.py` | `contains`・`last` | 画面の本文から語を含む行を拾う（色の印は外す） |
| `totitle.py` | | 閉じずに題の画面へ戻る（`reload.sh` が使う） |
| `setmod.py` | `mod`・`name`・`value` | MOD の定数を書き換える |
| `act.py` | `text`・`timeout` | 入力欄に書いて送り、手が空くまで待つ（自由行動・会話） |
| `trade.py` | `do`（open / close / status / prices） | 売買の窓。`prices` は手持ちの品の売価を値段の表の段ごとに並べる |
| `sell.py` | `item` | 開いている売買の窓で、手持ちの品を1つ売る |
| `steal.py` | `mode`・`enter` | `336_crime_overhaul` の店で盗む（乱数を手順の間だけ固定する） |

手順を足すときは `def main(say):` を書く。`lib.py` の部品（`app()` / `press_choice()` / `choices()` / `click()` / `Steps` / `mod()` など）と `ARGS` は、読む前に名前空間へ入っている。
Clock で後から書く手順は `KEEP_OPEN = True` を置き、終わりに `say("<done>")` を書く。

## 気をつけること

- 遊んでいるセーブで動く。手配度・所持金・仲間・依頼を書き換えた結果は、ゲームが行動のたびに保存する。試した後は控えへ戻す。
- 待ちは状態で決める（`w` / `relaunch.sh` は画面を見て進む）。決め打ちの `sleep` で待たない。
- 台本で作った状態（会話を経ずに仲間へ入れる、など）は、素の遊び方で辿り着けないことがある。そこで出た不具合は、素の経路で届くかを先に確かめる。
- 設定の額を変えた試しは、素の値のままだとゲーム自身の処理が動き、MOD の処理を通らないことがある（所持金不足の断りなど）。
- 結果のファイル・写真・押す前の画面の控えは `out/drive/` に置く（git の外）。
