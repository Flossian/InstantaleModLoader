# TECH: MOD 開発リファレンス

Instantale（Epic版 / Nuitka standalone / CPython 3.10）に外部から Python を注入し、
実行中のゲームを monkeypatch する仕組み。
これから MOD を書く人のための資料。

## 0. この文書の位置

| 文書 | 何が書いてあるか |
|---|---|
| TECH.md（本書） | このローダで MOD をどう書くか。事実とルール。他のゲームにも通じる話 |
| [GAME.md](GAME.md) | Instantale が何をしているか。このゲーム限定の事実 |
| [README.md](README.md) | 遊ぶだけの人向け。ローダと GUI の使い方 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 作った MOD を同梱に入れてほしいとき。ライセンス・送るときに揃えるもの・取り込みで何が起きるか |
| [MODS.md](MODS.md) | 同梱している MOD 一本ずつの説明・設定・困ったとき。`tools/build_mods.py` が各 MOD の `DOC.md` から綴じる |
| [MODLIST.md](MODLIST.md) | 同梱 MOD の早見表。`tools/list_mods.py` が `mod.json` から組む |
| [VERIFICATION.md](VERIFICATION.md) | 検証状況・未確認項目・その確認手順（§1 / §3 / §4） |
| [VERIFICATION_LOG.md](VERIFICATION_LOG.md) | 実機・実データでの検証記録（§2） |

GAME.md と分けているのは、**ゲームが更新されて食い違うのはあちら側だけ**だから。
疑う場所が1つになる。同じ理由で置き場所を決めてある:

| 内容 | 置き場所 |
|---|---|
| 事実とルール（ローダの作法） | 本書 |
| 実機で確かめた「ゲームがどう動いているか」 | `ui.py` / `frames.py` と GAME.md |
| 遊ぶ側から見た1本ぶんの説明・設定・困ったとき | その MOD の `DOC.md`（§2.7） |
| 個々の MOD の設計判断 | その MOD の入口ファイルの docstring |

> 節番号は他の md とソースから参照されている。欠番になっても詰めない。

### 目次

| 項目 | 節 |
|---|---|
| なぜ注入方式なのか / ファイル構成 / 探索と適用順 / 注入のタイミング / ログの世代管理 | §1 |
| 検査・オフライン検証・注入のコマンド / CI / 開発中の MOD / MOD の文書 | §2 |
| **初めて MOD を書く**（対象名の入手 → 雛形 → 検査 → 注入 → 確認） | §3.0 |
| 最小の形（`mod.json` と `ctx`）／`safe=True` | §3.1 |
| 適用順と依存の宣言／複数 MOD の重なり／伏せ方 | §3.2 / §3.3 |
| `apply()` が何度も呼ばれる理由と書き方（保留・世代・`on_ready`） | §3.4 / §3.5 / §3.6 |
| 台帳／設定／API 番号／剥がし方／`out/` と `state/` | §3.7 〜 §3.11 |
| MOD 同梱の設定画面（`"tool"`。設定ダイアログに収まらない設定） | §3.12 |
| Nuitka で効くもの・効かないもの | §4 |
| 画面・選択肢・会話・LLM・世界ごとの控え・背景ワーカーの既製部品 | §5 |
| **踏んだ罠の一覧（守るべきルール）** | §6 |
| 近い手口の既存 MOD を探す | §7 |
| このローダでできないこと | §8 |

---

## 1. 仕組みと構成

### 1.1 なぜ注入なのか

ゲームは Nuitka standalone ビルドで、純 Python モジュールは全て
`instantale.exe`（716MB）内にネイティブコード化されている。
`.pyc` は無く、逆コンパイルもファイル差し替えもできない。

一方 CPython ランタイムは `python310.dll` として動的リンクされたままで、
C-API 1607 関数がエクスポートされている。
そこで既に走っているインタプリタにコードを流し込む:

```c
PyGILState_STATE s = PyGILState_Ensure();
PyRun_SimpleString(bootstrap);
PyGILState_Release(s);
```

これを 74 バイトの x64 スタブとして `VirtualAllocEx` + `CreateRemoteThread` で実行する。
C コンパイラと管理者権限は要らない。ゲームフォルダは読むだけ。
注入されたコードは `instantale_modloader.boot()` を呼び、
`runtime/mods/` の各フォルダを `load_order.json` の順に適用する。

> 流し込むコードは、ゲームの `__main__` に名前を残してはならない。
> `PyRun_SimpleString` は**ゲームの `__main__` の辞書で**その文字列を実行する。
> モジュール階層に `import` や代入を書くと、
> 本体がその名前に束縛していたものを黙って上書きする。
>
> 実際に踏んだ（2026-08-21）。`import sys, os, datetime, traceback` の1行が、
> 本体の `from datetime import datetime`（クラス束縛）をモジュールで上書きしていた。
> そのせいで本体の `make_crash_log` が
> `AttributeError: module 'datetime' has no attribute 'now'` で落ち、
> 注入したセッションでは `crash_log.txt` も送信も丸ごと止まっていた。
> 素のゲームでは書けている。
> 見つけるまでの14件を「素の不具合」として数えていた（VERIFICATION.md §3.33）。
>
> いまは全体を関数で包んであり、モジュール階層に残るのは包みひとつだけで、それも `del` する。
> `tools/tests/test_injector_bootstrap.py` が `ast` でその構造を検査している。
> 平らに書き直したくなったら先にあの検査を読むこと。

### 1.2 ファイル構成

```
InstantaleModLoader.bat   GUI を開く（配布物で唯一の入口）
tools/gui.py              MOD 一覧・適用順・有効/無効・設定・追加・起動と注入・結果表示
tools/watch.bat, watcher.py  ゲームの起動を監視して自動注入（GUI 無し）
tools/injector.py         PE解析 → x64スタブ → CreateRemoteThread（--unload で剥がす）
tools/logrotate.py        out/*.log の世代管理（注入 = 1世代の境目）
tools/check_mods.py       静的検査（デコレータ・宣言と実体のずれ）
tools/build_mods.py       docs/MODS.md を各 MOD の DOC.md から綴じる（--check で照合）
tools/list_mods.py        docs/MODLIST.md を mod.json から組む（--check で照合）
tools/llm_ctx_probe.py    ローカル LLM の窓を実測して最適値を出す（127_ 用）
tools/epithet_probe.py    ローカル LLM で二つ名を引いて偏りを測る（317_ 用）
tools/npc_variety_probe.py  ローカル LLM で NPC を生成させ、外見・性格・経歴の偏りを測る（頼み文の写しは npc_variety_prompts.json）
tools/rebalance_saved_bgm.py  既存セーブの BGM を後からまとめて均す（104_ 用）
tools/tests/test_*.py     ゲーム抜きで走る検査。開発用で配布物には入らない
runtime/instantale_modloader/
    __init__.py   boot() / discover() / ログ / 世代発行 / 遅延設置の監視 / on_ready
                  / API 契約 / status.json の書き出し / unload()
    patch.py      @patch / @wrap / alias再束縛 / 世代管理 / 未import保留 / safe / revert
    patch_registry.py  どの MOD がどこへ当てたかの台帳・重なり・未解決の報告
    config.py     MOD ごとの設定 / ローダ自身の切り替え（デバッグモード）
    frames.py     フレームローカル採取・値の要約・呼び出し元の特定
    ui.py         選択肢 / 画面の塗り替え / 会話の閉じ方 / idle待ち / 施設の引き当て
    state.py      世界の見分け方と保存先の決め方・世界ごとの控え（§3.2.3 / §5.4）
    jobs.py       重い処理を背景で直列にこなすワーカー（§5.5）
    llm.py        LLM へ出ていく文章の捕まえ方・1問だけ聞く口・返答の読み方（§5.3）
    npcs.py       NPC の作り方（素データの置き場所・ひな型・配置。GAME.md §2.23）
    ids.py        ゲームの採番台帳（`index`）を通した id の採り方（§3.2.3）
    recon.py      実行時リコン（モジュール構造ダンプ）
runtime/mods/     MOD 本体（1バグ・1機能 = 1フォルダ。入口は mod.json が名指し）
    <フォルダ>/DOC.md      遊ぶ側から見た説明（§2.7）。docs/MODS.md へ綴じられる
    load_order.json        適用順（"order"）と無効一覧（"disabled"）
    load_order.local.json  手元だけの適用順。在れば上に優先（git 管理外）
settings/         変えたものだけ（無くてよい）
                  mod_settings.json / gui.json / loader.json（デバッグモード。§3.2.5）
out/              ログ・リコン成果物・status.json（最後の boot の結果）。消してよい
state/            MOD が持つ永続データ（§3.11）。消すと遊びが巻き戻る
discontinued/     開発を終了した MOD（§2.6.1）。git には残るが、ローダ・配布物・
                  CI のどれからも見えない
local/            配る予定の無い MOD（§2.6.2）。ローダだけが読む。git 管理外
docs/             README.md / MODS.md / MODLIST.md / TECH.md / GAME.md
                  / VERIFICATION*.md / CONTRIBUTING.md
```

### 1.3 探索と適用順は `discover()` が1箇所で決める

ローダ・GUI・静的検査の3者が同じ関数を呼ぶ。
以前はこの規則が3箇所に書き写されていて、1箇所だけ直したら
GUI の一覧と実際の適用順がずれた。

```python
found = instantale_modloader.discover()      # ゲームの中でも外でも同じ結果
found["order"]      # 有効な MOD。適用順（依存の制約も解決済み）
found["listed"]     # 一覧に出す順。無効なものも宣言された位置に含む
found["manifests"]  # 名乗り・api・settings・依存（MOD のコードは import しない）
found["debug"]      # "debug": true の MOD。デバッグモードが切なら order に居ない
found["debug_mode"] # デバッグモードが入っているか（§3.2.5）
found["superseded"] # {MOD 名: 取り込まれた版}。伏せ方は debug と同じ
found["wip"]        # 開発中（9xx）。順序ファイルに名前が無ければ読まない（§2.6）
found["local"]      # local/ から読んだもの（配る予定が無い。§2.6.2）
found["dirs"]       # {MOD 名: 在り処}。runtime/mods か local/。入口はここから組む
found["problems"]   # 宣言と実体のずれ。人が読む行
found["notes"]      # 直すべきずれではない知らせ（手元用の順序ファイルを使っている等）
```

`problems` と `notes` を分けているのは、
未公開の MOD を手元で動かしている間ずっと赤が出る状態を作らないため
（赤が常態になると本当のずれが埋もれる）。
`check_mods.py --strict` は `notes` も問題に格上げする。

#### 手元だけの適用順（`load_order.local.json`）

まだ公開しない MOD を手元で動かすためのもの。
在れば `load_order.json` の代わりに**丸ごとこれが使われる**
（効いている順序ファイルは `instantale_modloader.order_path` が1箇所で決める）。

要る理由は3つ。
`load_order.json` は配布する構成そのものなので開発中の MOD を書くと配った先で実体の無い記述になる、
GUI は保存のたびに順序ファイルを書き戻すので消しても戻ってくる、
コミットに未公開 MOD の名前が残る。

仕掛けは3点で、**どれか1つでも欠けると漏れる**:

| 場所 | 何をしているか |
|---|---|
| `.gitignore` | `load_order.local.json` を除外（MOD のフォルダ自体は `.git/info/exclude`） |
| `tools/gui.py` | 書き戻し先を `ml.order_path()` に聞く |
| `make_dist.bat` | `load_order.json` に載っていない MOD フォルダを staging から落とす。**ここが最後の砦** |

2つのファイルを混ぜないのは、差分から順序を組み立てる規則を増やさないため。
何で動いているかは `notes` と `modloader.log` に必ず出る。

### 1.4 注入のタイミング

`tools/watcher.py` は新しい pid に対し、
`Py_IsInitialized` をリモートスレッドで直接呼んでインタプリタ初期化を確認し、
そのうえで**可視ウィンドウの出現を待つ**
（Kivy が立ち上がり `__main__` の実行が終わった合図）。
これより早く注入してもパッチ対象がまだ存在しない。

> 窓が出た直後でも、`CreateToolhelp32Snapshot` は `ERROR_BAD_LENGTH` で失敗しうる。
> ゲームが torch / arrow / onnx などを読んでいる最中で、DLL の一覧が動いているため。
>
> ```
> pid 21260: injection error: PermissionError: [WinError 24]
> CreateToolhelp32Snapshot(module, pid=21260) failed
> ```
>
> Python 側では `PermissionError` として上がるので権限や多重起動を疑いたくなるが、
> MSDN が「成功するまで再試行せよ」と書いている類の失敗で、待てば通る（2026-08-21）。
> `injector._snapshot()` が `ERROR_BAD_LENGTH` のときだけ 0.1 秒おきに 20 回まで粘る。
> 権限やプロセス不在は待っても変わらないので、1回目でそのまま投げる。
> `tools/tests/test_injector_snapshot.py` が両方の振る舞いを見ている。

### 1.5 ログの世代管理

ログは全て「開く→追記→閉じる」で書かれるので、何もしなければプレイをまたいで積み上がる。
`tools/logrotate.py` が**注入の直前に** `out/` 直下の `*.log` を `名前.log.1` へ送る
（`KEEP_GENERATIONS` 世代ぶん保持、既定 1）。

入れ替えをゲームプロセスの中（`boot()`）でやらない理由が2つ。
`boot()` が自分で `modloader.log` に書いている最中に走ることと、
遅延設置の当て直し（§3.4）でも `boot()` が呼ばれるので
1回のプレイの記録が途中で分断されること。
注入は世代の境目そのものなので、注入する側で1回だけ行えば両方とも起きない。

対象は `out/` 直下の `*.log` だけで、`out/test/` `out/recon/` と `status.json` には触らない。
MOD が持つ永続データはそもそも `out/` に来ない（`state/`。§3.11）。

---

## 2. 開発の流れ

### 2.1 手順

```powershell
# 1. 静的検査（構文だけでなくデコレータと引数の整合も見る）
python -m compileall -q runtime tools
python tools/check_mods.py
python tools/build_mods.py --check   # docs/MODS.md が DOC.md とずれていないか（§2.7）
python tools/list_mods.py --check    # docs/MODLIST.md が mod.json とずれていないか

# 2. オフライン検証（ゲーム不要）。CI と同じく全件を走らせる
Get-ChildItem tools/tests/test_*.py | Sort-Object Name | ForEach-Object {
  python $_.FullName > $null 2>&1
  if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL  $($_.BaseName)" }
  else                     { Write-Host "  ok    $($_.BaseName)" }
}
# 直している最中は、触った MOD のものだけを直接叩けばよい（落ちた内容が読める）

# 3. ローダ全体が読めるかの確認（フックは大半が保留になるが、import と apply() の失敗が出る）
python -c "import sys; sys.path.insert(0,'runtime'); import instantale_modloader as l; print(l.boot('out/test/bootcheck'))"

# 4. 注入（ゲームが起動している状態で）
python tools/injector.py
python tools/injector.py --dry-run   # アドレス解決だけ。何も書き込まない
python tools/injector.py --unload    # 当てたパッチを剥がす（§3.10）

# 5. 結果を読む（注入が成功したかと、MOD が入ったかは別の話）
type out\status.json                 # 適用結果・台帳・効いている設定
```

### 2.2 編集ループ

「MOD を編集 → `python tools/injector.py`」で回す。
`boot()` が `instantale_modloader` を `sys.modules` から落として再 import するので、
そのまま反映される（層は積み上がらない。§3.5）。

> 注入はプロセスと一緒に消える。ゲームを起動するたびに注入し直すこと。

`on_ready` に預けた1回きりの初期化（§3.6）は注入し直しても走らない。
その初期化自体を直しているときは `ctx.on_ready(fn, force=True)` を一時的に使うか
`reset_once("300_")` で印を落とす。
どちらも開発中の逃げ道で、配布する MOD に `force=True` を書いてはいけない。

### 2.3 静的検査（`tools/check_mods.py`）は必ず通す

`compileall` は構文しか見ないが、**実際にゲームを落とすのは構文として正しいコードのほう**。
「`@ctx.wrap` の対象名と、飾っている関数の引数の並びが食い違っている」類は静的に捕まえられる
（`@ctx.wrap` が飾る関数の第1引数は `orig`、メソッド対象なら第2引数は `self`）。

同じ考えで、`"entry"` の不在・扱えない `"api"`・`load_order.json` との食い違い・
`"after"`/`"before"` の循環・`"settings"` の既定値とコード側の定数のずれも捕まる。

| 出力 | 意味 |
|---|---|
| `MISMATCH` | 直すべきもの。終了コード 1 |
| `note` | 表示だけの項目の欠落（`name` などは仕様では任意）。終了コード 0 |
| `--strict` | `note` も失敗として数える。同梱 MOD はこちらを通す |

### 2.4 環境の決まり

| 決まり | 理由 |
|---|---|
| ゲーム側は Python 3.10。3.11 以降の構文を使わない | 手元の python は 3.13 なので `compileall` だけでは 3.10 互換を保証できない。`check_mods.py` が `ast` の `feature_version=(3,10)` で構文を弾き、CI が本物の 3.10 で `runtime/` をコンパイルする |
| `.bat` は ASCII のみ | その時のコンソールのコードページで読まれるため、日本語を入れると環境によって解析が壊れる |
| ツールから MOD を読むときは番号を書かない | `find_mod("_balance_area_bgm.py")` のように番号を除いた名前で引く。分類を見直して番号を振り直しても壊れないように |

### 2.5 CI（`.github/workflows/ci.yml`）

push と pull request で、§2.1 と同じコマンドを同じ順に走らせる（CI 専用の細工は無い）。
Windows で動かすのは、このプロジェクトが Windows 専用だから（注入が Win32 API を直接叩く）。

| ジョブ | Python | 見るもの |
|---|---|---|
| `game-python` | 3.10 | `compileall runtime`。ゲームの中に入るコードが本物の 3.10 で通るか |
| `checks` | 3.13 | `compileall` / `check_mods.py` / 生成物の照合（`build_mods.py --check` / `list_mods.py --check`）/ `tools/tests/test_*.py` 全件 |
| `packaging` | 3.13 | `make_dist.bat` が通ること、zip に `LICENSE` / `NOTICE` が入っていること |

- 除外一覧は置いていない。1本でも落ちたら CI が失敗する
  （「既知の失敗」の枠を作ると、そこに積まれたものが直ったかどうか誰も見なくなる）
- **落ちた本は出力をそのまま吐く**（折り畳み1つ）。通った本は1行だけ。
  名前しか残さない作りにしていたら、手元では再現しない失敗で手掛かりが何も残らなかった（VERIFICATION.md §4）
- `packaging` が zip の中身まで見るのは、`LICENSE` の入っていない配布物は誰も合法的に再配布できないから
- **開発中の MOD（9xx）と `test_wip_*.py` だけは外してある**（§2.6）。
  これは番号帯という決まった形での除外で、正式な番号へ振り直した瞬間に検査の対象へ戻る

> 「手元で通ったものは CI でも通る」はコマンドが同じという意味で、環境まで同じという意味ではない。
> 背景スレッドの待ちのように、ランナーの速さで結果が変わるものはありうる。

### 2.6 開発中の MOD（900番台）

**いずれ配るが、まだ配れない MOD** は `900`〜`999` で採番し、
配ると決めたときに番号帯に応じた正式な番号へ振り直す。
作りかけ・実機で確かめていない・仕様が固まっていない、のどれかがこの帯に居る理由になる。

配る予定が最初から無いものはこの帯ではない。
手元の事情に閉じているもの、素のゲームでは踏めないもの、遊んでみて面白くなかったものは
`local/` へ置く（§2.6.2）。
以前は「配らない」の1語で両方をこの帯に入れていたが、
片方はいつか出ていき、もう片方はずっと居座るので、
帯を眺めても残タスクが数えられなくなっていた。

配らない理由そのものは、どちらの場合もその MOD の `DOC.md` の先頭に書く。

| | 入れる | 入れない |
|---|---|---|
| Git | ○ 普通にコミットする | |
| `load_order.local.json`（手元） | ○ ここに書けば手元では動く | |
| `load_order.json` / 配布物 / CI / `docs/` の8冊 | | × |
| 開発を終了したら | `discontinued/` へ移す（§2.6.1） | |
| 配らないと決めたら | `local/` へ移す（§2.6.2） | |

文書はどの MOD もフォルダの `DOC.md` に置く（§2.7）。
9xx が他と違うのは、`load_order.json` に載らないので `docs/MODS.md` へ綴じられない点だけ。
遊び方はその1枚に書いておけば、正式な番号へ振り直して順序ファイルに載せた時点で
そのまま MODS.md に載る（移し替える作業は要らない）。

`docs/` の8冊へ入れられないぶん（検証の一覧に載せる行、未確認項目と確認手順）も
同じ1枚に書いておき、リリースのときにそこだけ戻す。
どの節をどこへ戻すかは `DOC.md` の先頭に表として持たせておく。

ローダ側の扱いは `is_wip()` の1箇所で、順序ファイルに名前があれば読み込み、無ければ黙って外す
（配布物に入らないものを配った先の画面で警告しても直しようが無い）。
判定は `runtime/mods` の中でだけ効く。
`local/` へ移した MOD は 9xx の番号を残したままなので（§2.6.2）、
`discover()` はそちらを `wip` に数えない。

> なぜ `mod.json` の旗ではなく番号帯なのか。
> `debug` や `superseded` は「配るが伏せる」ので、旗を立てたまま何年でも同梱される。
> 9xx は逆で、**リリースする ＝ 必ずフォルダ名を変える**。
> 旗だと消し忘れたまま配ってしまうが、番号は変えない限り配布物に入らないので事故にならない。

#### 2.6.1 開発を終了した MOD（`discontinued/`）

作るのをやめた MOD は、消さずに `discontinued/<元のフォルダ名>/` へ移す。
消さないのは、**そこまでに分かったこと（DOC.md の検証記録）がいちばんの資産**だから。
番号は 9xx のまま変えない（DOC.md・検査・過去のログ・git の履歴が、その番号で互いを指しているため）。

移す先が `runtime/` の外なのは、**外すための旗を新しく増やさずに済む**から。
MOD を探している4者（`discover()` / `make_dist.bat` / CI の `compileall` / `check_mods.py`）は
全員 `runtime` の下しか見ない。
`.gitignore` には当たらないので**追跡は続く**。検査も一緒に移してフォルダを自己完結させる。

**CI が一切見ない ＝ 構文エラーも検出されない**点は承知して置くこと。
戻すまでのあいだ、そのフォルダは**読み物**であって動くコードではない。

終了の経緯は DOC.md の先頭に節を1つ作って書く。
書くのは「なぜ終了したか / どこまで動いていたか / 残っている症状 / 再開するなら何から」の4点。
どれか1つでも欠けると、半年後に読んだ人が同じ調査をやり直すことになる。

#### 2.6.2 配る予定の無い MOD（`local/`）

配らないと決めた MOD は、配布フォルダの根の直下の `local/<フォルダ名>/` へ置く。
番号はそのまま持っていく。
`DOC.md`・同梱の検査・過去の `out/` のログ・`settings/` の鍵が、
その名前で互いを指しているため（§2.6.1 と同じ理由）。
9xx の番号が付いたまま `local/` に居るものが在る。
印は番号ではなく在り処なので、`discover()` は `local/` から読んだものを
「開発中」として扱わない。

置き場所を `runtime/` の外にしたのは、**外すための仕掛けを1つも増やさずに済む**から。
MOD を探している4者のうち3者は `runtime` の下しか見ない。

| 見る側 | `local/` を見るか | 理由 |
|---|---|---|
| `discover()` | ○ | `_local_mods_dir()` が根の直下を指す。無ければ黙って飛ぶ |
| `make_dist.bat` | × | robocopy の対象は `runtime\mods` |
| CI（`compileall -q runtime tools`） | × | `runtime` と `tools` しか渡していない |
| `tools/check_mods.py` | × | `discover()` の `local` を `installed` から落とす |

`.gitignore` にも `/local/` の1行だけで済む（`out/` や `work/` と同じ節）。
配布物にはこのフォルダが無いので、配った先では `os.path.isdir` が偽になって何も起きない。

読み込みの条件は**順序ファイルに名前があること**の1つだけ。
9xx と違ってデバッグモードは要らない。
作りかけではなく、普段の遊びで動かすために置いてあるものだから。
GUI は一覧に「ローカル」と出し、入切・設定・同梱の道具はそのまま効く。
行の地色は配布物に入るものと別で、デバッグモードに関係なく常に並ぶ（`gui.py` の `local_bg`）。
切ってあっても「無効化されています」の報告には入れない
（配布物に入らないので、知らせる相手が居ない。切ったこと自体は `skipped` に残る）。

| | 入れる | 入れない |
|---|---|---|
| Git | | × （`/local/`） |
| `load_order.local.json`（手元） | ○ 書かなければ読み込まれない | |
| `load_order.json` / 配布物 / CI / `docs/` の8冊 | | × |
| デバッグモード | | 要らない |

**git に残らない**ので、記録を残したい MOD は `local/` へ移す前に
`discontinued/` へ写しを1つ置く（§2.6.1）。
写しは移した日のまま凍り、`local/` 側を直しても追随しない。
再公開するときに取るのは `local/` の側。

### 2.7 MOD の文書（`DOC.md`）

遊ぶ側から見た1本ぶんの説明は、その MOD のフォルダの `DOC.md` に置く。
`docs/MODS.md` はそれを綴じ直したもので、`tools/build_mods.py` が組む。

```
runtime/mods/316_bounty_hunter/
    mod.json
    bounty_hunter.py
    DOC.md      ← 説明・設定・困ったとき
```

```powershell
python tools\build_mods.py            # docs\MODS.md を書き出す
python tools\build_mods.py --check    # ずれていたら 1 で終わる（CI が呼ぶ）
```

**`docs/MODS.md` は生成物**。手で書き換えても次の生成で消える（MODLIST.md と同じ）。
直す先はその MOD の `DOC.md`。

コードの隣に置くのは、MOD を1本足すのに 2000 行の正しい位置を探さずに済ませるため。
番号を振り直すときも、フォルダごと動かせば文書がついてくる。

#### 書き方

1行目は名乗りで、フォルダ名と一致していないと生成が止まる。

```markdown
# `316_bounty_hunter`: 手配されていると追手が来る

（本文）

| 設定 | 意味 |
| --- | --- |

## 困ったとき

| 症状 | やること |
|---|---|
```

綴じるときに見出しが2つ下がる（`#` → `###`、`##` → `####`）ので、
中の小見出しは `##` で書く。コードフェンスの中は触らない。

計測（2xx）だけは節ではなく表の1行になる。見出しに説明を続けず、本文を1行だけ書く。

```markdown
# `217_probe_area_move`

エリア移動の未実測部分を録る。……
```

#### 並び

`docs/MODS.md` に綴じる順は `tools/build_mods.py` の `BANDS` が持つ。
`1xx` はフォルダ名順ではなく読む順に手で並べてあるので、規則では出せない。

MOD を足したら `load_order.json` と `BANDS` の両方に足す。
片方だけだと `--check` が止める（`load_order.json` を universe として突き合わせる）。

---

## 3. MOD の書き方

### 3.0 最初の MOD を作る

初めて書くとき最初に詰まるのは `@ctx.wrap("...")` の `"..."` へ何を書くかで、
対象名は推測ではなく実行中のゲームから取り出す。

#### 手順 0. 対象名の一覧を手に入れる

同梱の `000_recon`（既定で有効）が、注入されたときにゲームの中身を `out/recon/` へダンプする。
必要なのは「一度ゲームを動かして注入する」ことだけ:

```powershell
InstantaleModLoader.bat        # GUI からゲームを起動して注入する
```

| ファイル | 使いどころ |
|---|---|
| `out/recon/targets.txt` | これが本命。`module:qualname(signature)` 形式で 1,635件（main_025 時点） |
| `out/recon/game_modules.txt` | ゲーム自身のモジュールの全属性ダンプ。擬似ソースとして読む |
| `out/recon/modules.json` | 機械可読のインベントリ |
| `out/recon/build.json` | このダンプがどのビルドを見たものか |

読み方と、スキャンで見つからないもの（ネスト関数・クラスのメソッド）は [GAME.md §1](GAME.md)。

`out/recon/` は毎回上書きされるが、**ゲームが更新されていれば上書きの前に
`out/recon_snapshots/<版>_<日付>.zip` へ退避される**（`build.json` と突き合わせる）。
更新の前後で `targets.txt` を突き合わせれば、増えた対象・消えた対象がそのまま出る。

退避は新しい方から 20 本だけ残す（`recon.SNAPSHOT_KEEP`）。
版の判定は安全側に倒してあり、素性が読めない起動は「別のビルド」と見なすので、
同じ版のまま1回の起動から2つ以上できることがある（実測では 109個 / 23MB まで溜まっていた）。

> 中身の差を引き金にしないこと。
> リコンは `sys.modules` を見るので、同じ版でも起動直後と長時間プレイ後で中身が変わる（3452 と 4235）。
> 中身の差で退避すると同じ版の zip が毎回増え、肝心の1回が埋もれる。

#### 手順 1. 雛形をコピーする

```powershell
xcopy /e /i runtime\mods\_template runtime\mods\900_my_mod
```

`_template/` は先頭が `_` なので読み込まれない。コピーして名前を付けた時点で MOD になる。
フォルダ名も入口のファイル名も自由で、番号は分類のためだけのもの（§3.2.2）。

コピー直後は `load_order.json` に載っていないので末尾に置かれる（動くが、静的検査が報告する）。

#### 手順 2. 対象を決める

```powershell
findstr /i "employ_price" out\recon\targets.txt
```

見つけた行をそのまま `@ctx.wrap` に貼る（`(signature)` の部分は貼らない）:

```
scripts.functions:get_npc_employ_price(npc_difficulty_level)
        ↓
@ctx.wrap("scripts.functions:get_npc_employ_price")
```

名前の当てが無いときは `game_modules.txt` を全部眺めるほうが速い
（属性名を推測してスキャンすると空振りする。GAME.md §1.3）。

まず包んでログを出すだけにする。
対象が本当に呼ばれるのか、引数に何が来るのかを確かめてから中身を書く
（雛形が `add_text` を包んで長さを出すだけなのはこのため）。

#### 手順 3. 静的検査を通す

```powershell
python -m compileall -q runtime
python tools\check_mods.py
python tools\build_mods.py --check
python tools\list_mods.py --check
```

`MISMATCH` が出たら直す（`note` は後回しでよい）。
ここで捕まるのはどれも構文としては正しいので `compileall` では出ない（§2.3）。

後ろの2本は文書のずれを見る。
`DOC.md` を書いて `BANDS` に足すまでを済ませていれば通る（§2.7）。

#### 手順 4. 注入して確かめる

```powershell
python tools\injector.py       # ゲームが起動している状態で
type out\status.json           # mods[フォルダ名] が "ok" か
type out\modloader.log         # applied / wrapped の行、失敗のトレースバック
```

注入が成功したことと、MOD が効いたことは別の話。
あとは「MOD を編集 → `injector.py`」で回す（§2.2。ゲームは起動したまま）。

#### 詰まったとき

| 症状 | 見るところ |
|---|---|
| GUI の一覧に出ない | フォルダ名が `_` / `.` で始まっていないか。`mod.json` はあるか（§3.1.1） |
| `no-entry` | `mod.json` の `"entry"` が入口のファイル名と一致していない |
| `load-error` / `apply-error` | `modloader.log` のトレースバック。1本壊れても他は動く |
| 台帳に `UNRESOLVED` | 対象名が違う。`targets.txt` で取り直す。ゲーム更新も疑う（§3.7） |
| 台帳に `DEFERRED` | まだ import されていないだけ。現れた時点で当て直される（§3.4） |
| 何も起きない・ログも出ない | 注入し損ねている。`status.json` の `boot_count` を見る |
| 直したのに古い動作のまま | `from x import y` の複製束縛か、コンパイル済み関数内で解決済み（§4.1） |
| 1回きりの初期化が走らない | 印はプロセスに残る。`force=True` か `reset_once()`（§2.2 / §3.6） |

書き足す前に §6（落とし穴の一覧）を通読しておくと踏まずに済む。
近い手口の既存 MOD は §7 のカタログから引ける。

### 3.1 最小の形

#### 3.1.1 フォルダと入口

1つの MOD = 1つのフォルダで、`mod.json` を持つものが MOD:

```
runtime/mods/
    load_order.json
    area_move_dungeon/
        mod.json                名乗りと入口の宣言。ローダはまずこれを読む
        area_move_dungeon.py    入口。apply(ctx) を定義する
        journey.py              分割した中身（from . import journey）
        data/quest_table.json   同梱データ（ctx.mod_dir から読む）
```

探索はこの1階層だけで、再帰しない
（深く潜ると MOD の中の補助モジュールまで MOD として拾ってしまい、規則が増える）。
小さい MOD でもフォルダにする
（単一ファイルとの混在を許すと、探索・静的検査・GUI・「新しい MOD をどう作るか」の
4箇所すべてに分岐が増える）。

#### 3.1.1.1 1本が大きくなったら分ける

入口が数百行を超えたら分割してよい。
ローダは入口をパッケージとして読み込むので `from . import world` がそのまま使える。

分ける線は**「何を知っているか」**で引く（実例は `307_`、1205行を 756 + 143 + 191 に）:

| ファイル | 知っていること | 知らないこと |
|---|---|---|
| 入口 | この MOD の方針・設定・文言・フックの設置 | - |
| `journey.py` | 自分の状態の持ち方（段階・保存） | ゲームのこと |
| `world.py` | ゲームのどこに何があるか | この MOD の方針 |

| 決まり | 理由 |
|---|---|
| **設定の定数は入口に残す** | ローダは入口モジュールのグローバルへ書き込む（§3.8）。他のファイルへ移すと GUI から変えても効かない |
| 分けた側は設定を読まない | 必要な値は引数で受け取る。読むと「どちらの値が効いているのか」が2箇所になる |
| 分けた側からゲームを触るなら、方針は持たせない | 断る条件・確率・文言は入口 |
| ログ関数（`write`）は引数で渡す | `ctx` を配らない。分けた側が勝手にログの体裁を決めない |

`tools/tests/test_*.py` が mod を読み込む部分も、ローダと同じ形にすること
（`sys.modules` への登録を忘れると `from . import ...` が落ちる）。

> MOD 単体の部品は MOD のフォルダの中で完結させる。
> 出ていってよいのは `out/` のログと `state/` の永続データだけ（§3.11）。
> 手で編むデータファイルも `mods/<その MOD>/` に置き、
> 配布フォルダの `settings/` や外部ツールの置き場所を探しに行かない
> （フォルダを1つコピーすれば動き、消せば残らない状態を保つため）。
> GUI から変える設定だけは例外で `settings/mod_settings.json` に集める（§3.8）。
>
> 手で編むファイルは、配布物が持つ名前と分ける:
> `llm_replacements.default.txt`（配布物の既定。更新で上書きされる）と
> `llm_replacements.txt`（手元のファイル。あればこちらを読む）。
> MOD の更新は上書きマージなので、配布物が同じ名前で持たなければ更新を生き残る。
> `make_dist.bat` は手元側の名前を `/XF` で除外している。

入口は `mod.json` が名指しする:

```json
{"entry": "timings.py", "api": 1,
 "name":        {"en": "Timings KeyError fix", "ja": "timings 欠落の修正"},
 "description": {"en": "Swallows the KeyError ...", "ja": "..."},
 "version": "1", "author": "R01/Flossian"}
```

#### 3.1.2 入口ファイル

```python
# -*- coding: utf-8 -*-
"""何をする MOD か。なぜその作りなのか。"""

def apply(ctx):
    @ctx.wrap("scripts.llm.request_llm_inference_llama_cpp_completion:send_request")
    def send(orig, *args, **kwargs):
        try:
            return orig(*args, **kwargs)
        except KeyError as exc:
            if str(exc) != "'timings'":
                raise
            ctx.log("swallowed KeyError 'timings'")
            return None
```

#### 3.1.3 `ctx` の API

| メンバ | 何をするか |
|---|---|
| `@ctx.patch(target)` | 完全置換。置換関数から `__original__` で元にアクセスできる |
| `@ctx.wrap(target)` | 元関数を第1引数で受け取るラッパ |
| `ctx.resolve(target)` | `(owner, name, value)` を返す。調査用 |
| `ctx.log(...)` / `ctx.log_exc(...)` | `out/modloader.log` へ |
| `ctx.out_path(name)` | `out/<name>` の絶対パス（§3.11） |
| `ctx.logger(name)` | その MOD 専用のログ関数。**自分で `open` を書かない**。`cap=N` で打ち切れる（§3.11.2） |
| `ctx.warner(tag)` | 同じ鍵の警告を一度しか出さない関数を作る（§3.11.2） |
| `ctx.state_path(name)` | `state/<name>` の絶対パス。遊びの続きに要るデータはここへ（§3.11） |
| `ctx.read_json(path, default)` | 無ければ `default`、在るのに読めなければ記録してから `default`（§3.11.1） |
| `ctx.write_json(path, data)` | 落ちても壊れないように書く。成否を返す。**残すデータは必ずこれ** |
| `ctx.write_text(path, text)` | 同上。JSON 文書1つではないもの（1行1レコードなど）用 |
| `ctx.mod_dir` | いま apply() 中の MOD のフォルダ。**`apply()` の外では `None`** |
| `ctx.out_dir` / `ctx.state_dir` | `out/` と `state/` の場所（`out_path` / `state_path` の親。§3.11）。`state/` は `runtime/` の1つ上＝配布フォルダ直下 |
| `ctx.game_dir` | ゲームの exe（`sys.executable`）の在るフォルダ |
| `ctx.on_ready(fn)` | プロセスにつき1回だけメインスレッドで実行（§3.6） |
| `ctx.superseded()` | 自分より新しい注入が来たか。自前のスレッド・`Clock` の繰り返しはこれで降りる（§3.6.1） |
| `ctx.refresh_status()` | `out/status.json` を書き直す。`apply()` を抜けた後に設置したときだけ（§3.7.3） |
| `ctx.patches()` | 対象 → 当てた MOD の一覧。自分より前の分が見える（§3.7） |
| `ctx.config` / `ctx.setting(名前)` | この MOD に効いている設定値（§3.8） |
| `ctx.api` / `ctx.version` | ローダの API 番号と版（§3.9） |
| `ctx.generation` | この注入の世代。`on_ready` のキーに混ぜる用（§3.6.1） |

`target` は `module:qualname` 形式
（`llm_manager:quest_referee_event_resolve` / `llama_cpp_runtime_completion:LlamaCppClient.chat`）。

#### 3.1.4 `@ctx.patch` / `@ctx.wrap` のキーワード引数

| 引数 | 効果 |
|---|---|
| `required=False` | 対象が見つからなくても黙って降りる（既定は例外） |
| `safe=True` | フックの例外をゲームへ流さず、元の動作に落とす（§3.1.5） |
| `alias_scan="all"` | エイリアス張り替えを全モジュールに広げる（既定は関係する範囲だけ。§4.1） |

`@ctx.patch` は対象の名前が無ければ撥ねる。
`setattr` は黙って新しい名前を作るので、
対象名を打ち間違えた MOD が「当たった」ことになってしまうため
（名前を新設したいときだけ `required=False` を明示する）。

#### 3.1.5 `safe=True` の落とし方

`safe=True` は「ゲームを落とさない」ためだけの `try`/`except` を毎回書く代わりに使える。
落とし方は**元の関数がどこまで走ったか**で分かれる:

| 状況 | 落とし先 |
|---|---|
| `orig` を呼ぶ前に壊れた | 元の関数を呼んでその結果を返す（素のゲームと同じ挙動） |
| `orig` が答えを返した後に壊れた | **その結果をそのまま返す**（後処理だけが失敗した） |
| `orig` 自身が投げた | **その例外をそのまま通す**（フックの失敗ではない。`WARN` 1行だけ残す） |

2つ目と3つ目が要点で、単純に「失敗したら元を呼び直す」と書くと
元の関数の副作用（テキストの追加・セーブ・状態の更新）が2回起きる。
3つ目は同じ対象に層が重なると倍々になる（N 層で 2^N 回。VERIFICATION.md §3.46）。
`safe=True` が守るのはフックの失敗であって、ゲーム自身の失敗ではない。
フックが `orig` の例外を握って別の例外を投げた場合も呼び直さず、素の例外を投げ直す。
記録は「最後に呼んだ `orig` の結果」1つ。答えを返した後の2回目の `orig` が投げれば、その例外が通る。

通した `WARN` は例外1つにつき1行（層が重なっても増えない）で、投げた場所を添える:

```
safe hook on __main__:AreaMoveManager.execute: the original raised KeyError: '37' at <ファイル>:<行> in <関数>; passing it through
```

ここでの「素の関数」は**1つ内側**であって、ゲーム本体とは限らない。
同じ対象に safe でない別 MOD のフックが内側に載っていれば、そのフックのバグもこの行で通る
（`safe hook failed` には出ない）。場所のファイル名が MOD のものならそれ。

例外は `ERROR` としてログに残るので、**`safe hook failed` を見たら直すこと**
（`safe=True` は直すべき不具合を見えなくもする）。

#### 3.1.6 名乗り（`mod.json`）

`entry` 以外は任意。
うち `api` / `after` / `before` / `conflicts` / `kind` / `settings` / `tool` /
`debug` / `superseded` は動作に関わり、
`name` / `description` / `version` / `author` は表示専用。
`tool` は MOD 同梱の設定画面の宣言（§3.12）。
`debug` / `superseded` は配るが伏せる旗（§3.2.5）。
`shares` はローダが読まない鍵（`_manifest()` は知っている鍵だけを写す）で、
`tools/check_mods.py` が素の `mod.json` から読む。

名乗りを Python ではなく JSON に置いているのが要点。
GUI は MOD の一覧を作るのにコードを1行も走らせずに済む
（無効化中の MOD も壊れている MOD も、名前付きで並べられる）。
モジュール変数に置くと、一覧表示のためだけに他人の MOD を import することになる。

`status()["manifests"]` は言語ごとの分岐を書かなくて済むよう形を均して返す
（片方の言語しか書かれていなければもう片方で埋めるので、`name["ja"]` は必ず何かを返す）。
`"name": "Some mod"` のように文字列1つでも書ける。

`name` は一覧に並べる名前なので短く保つ（目安は全角12文字／半角30文字）。
何をする MOD かは `description`、遊び方は `DOC.md`（§2.7）、設計判断は入口ファイルの docstring。

> これは書き方の約束で、検査はしない。
> 以前は長さを検査していたが外した。
> 名前列は伸縮するので固定幅で切り落とされるわけではなく、
> 行に描くのは `mod.json` の名前そのものでもない（`superseded` には `〔main_024 で本体が取込〕` が付く）。
> 実際にこの検査は正確な名前を弾いていて、**通すために名前を悪くする**方向に効いていた。

**ログにはフォルダ名を出す**（名乗りは出さない）。
フォルダ名はインストール単位で一意、cp932 のコンソールでも化けず、grep もしやすい。

### 3.2 適用順

#### 3.2.1 `load_order.json`

```json
{"order": ["000_recon", "001_crash_recorder", "100_fix_kivy_shutdown", "..."],
 "disabled": ["000_recon"]}
```

先に適用した MOD ほど内側、後から適用した MOD が外側になる。
`"disabled"` は GUI のチェックボックスの実体で、フォルダ名を変えずに切れる
（無効化を `_` 接頭辞でやると、切った瞬間に `"order"` の中の名前と食い違う）。

**順序をフォルダ名から決めない**のは、フォルダ名を自由に付けられるようにするため
（「名前は自由」と「順序は名前で決まる」は両立しない）。
同梱 MOD の番号はローダが見ていない。

| 状況 | 挙動 |
|---|---|
| 順序ファイルに無い MOD | 捨てずに末尾へ回す（フォルダ名順）。置いただけで動く |
| 順序ファイルにあるが実体が無い | 黙って飛ばす |
| 順序ファイルが壊れている / 無い | フォルダ名順で動く。**ここで例外にすると MOD が全滅する** |
| `"disabled"` にあるが実体が無い | 何もしない |

#### 3.2.2 同梱 MOD の番号帯

| 帯 | 分類 | 基準 |
|---|---|---|
| `000` | 動作の根本 | リコン・クラッシュ記録。他が触る前の素の状態を押さえる |
| `100` | ゲーム本体の挙動の修正 | 既にある動作を直す・調整する（バグ修正に限らない） |
| `200` | 計測（読み取り専用） | 値を変えない。**修正より後に置くことに意味がある** |
| `300` | 新規機能追加 | 元々無かったものを足す |
| `400` | ユーザ提供 | 提供を受けて取り込んだ MOD。この帯だけは中身ではなく**出どころ**を表す |

計測を修正より後に置くのは、プローブが修正前の生の引数を記録するようにするため
（修正の効果は修正 MOD 自身がログする）。

**順序が効くのは同じ対象を2つの MOD が包むときだけ**で、その関係は帯順で決まる:

```
204_ が 103_ を包む / 206_ が 104_ を包む / 300_ が 205_ を包む
304_ が 303_ を包む / 215_ が 313_ を包む（計測は 313_ が動かした後の値を控える）
111_ が 102_ / 103_ / 105_ を包む（置換は圧縮前の本文を見る）
```

**帯は帯であって分類の軸ではない**（ゲーム本体の挙動を変えるなら機能追加でも 100番台でよい。
`400` は出どころの帯で、提供された計測 MOD なら 2xx へ置く。実際 `223_` がそう）。

> `400` に居ないユーザ提供が在る。
> この帯を作る前に取り込んだものは種別どおりの帯に入っていて
> （`117_` / `118_` / `119_` は修正、`311_` は追加、`223_` は計測）、
> 番号を振り直すと遊んでいる人の `state/` と設定が行方不明になるので動かしていない。
> 番号で提供を数えないこと。
> 数える先は `mod.json` の `author` で、
> 一覧は MODLIST.md の「提供を受けた MOD」が `author` から組む。
> 権利の所在は NOTICE が持ち、`tools/check_mods.py` が両者の食い違いで止まる。
だから**種別そのものは各 MOD が `mod.json` の `"kind"` で名乗る**
（`core` / `fix` / `probe` / `feature` の4語）。
GUI はこの宣言を表示するだけで、フォルダ名の番号帯からは導かない。
表示では**状態が種別に勝つ**（`superseded` は「取込済」、9xx は「開発中」と出る）。

#### 3.2.3 MOD どうしは import しない。ローダの語彙は共有する

ローダは MOD を `instantale_mod_<フォルダ名>` で登録する。
**名前で掴むと番号を振り直した瞬間に壊れる**ので、MOD が MOD を import することはしない。
MOD どうしが繋がるのは同じファイルを読むことによってで、相手が入っていなければ何も起きない。

この規約は「何も共有しない」ではない。
共有してよい相手は最初からローダ（`instantale_modloader.*`）で、そこは番号に依存しない。

| 置き場所 | 例 |
|---|---|
| ローダ（共有する） | ゲームの読み方（`ui` / `frames`）、保存先の決め方（`state`）、壊れない書き込み（`ctx.write_json`） |
| MOD（共有しない） | その MOD 固有の判断: どの画面に何を出すか、プロンプトをどう書き換えるか |

**写して回るものが出たら、それはローダの語彙**だと考えること。
写した時点でドリフトは予告されていて、実際に起きた:

- `world_key(app)`（世界の見分け方）は5本にコメントごと同じものがあった
- ファイル名の正規化は4本にあり、`301_` の docstring は「`311_` と1文字も違ってはいけない」と書いていた
- そして `312_` が実際にずれた（`strip` と `sub` の順が逆、120文字の切り詰めも `"."` の除外も無い）
- 3本とも Windows の予約デバイス名（`CON` / `NUL`）を見ていなかった。
  **この知識は `110_` が先に持っていた**のに隣へ届いていない

いまローダに移してあるもの。
**どれも「同じものが2本以上に写っていた」ことが移した理由**で、思い付きで足したものは1つも無い:

| 何を | どこに | 写されていた本数 |
|---|---|---|
| 世界の見分け方と、そこから作るファイル名 | `state.world_key` / `world_key_of_dict` / `world_filename` | 5本 / 4本 |
| LLM へ出ていく文章が通る場所 | `llm.wrap_outgoing`（§5.3） | 2本 |
| LLM に1問だけ聞く呼び方 | `llm.ask` / `create_structure` / `as_dict`（§5.3） | 3本 |
| 後から生える別名の見張り | `llm.watch_aliases`（§5.3） | 2本 |
| MOD 専用のログ | `ctx.logger`（§3.11.2） | **49本** |
| 文字列を期待する読み方 | `frames.text_of`（§5.2） | 3本が別々に取り違えていた |
| 走っている app の探し方 | `ui.find_app` | 7本 |
| クエストの2つの格納先・id の並べ方 | `ui.quest_stores` / `id_sort_key` ほか（§5.1.3） | 3本 |
| HUD に足すボタンの作り方・絵柄 | `ui.make_icon_button` / `paint_icon` ほか | 3本 |
| 所持金と「今は出さない」旗 | `ui.gold_of` / `add_gold` / `money` / `BUSY_FLAGS` | 3本 / 2本 |
| 包む前の素の関数まで剥がす | `patch.unwrap` / `original_of` | 4本（うち2本は1段しか剥がしていなかった） |
| 壊れない書き込み・読み込み | `ctx.write_json` / `read_json`（§3.11.1） | 3本 |
| NPC の作り方（素データ・ひな型・配置） | `npcs.make_npc` ほか（GAME.md §2.23） | 2本（`320_` と `local/` の MOD） |
| ゲームの採番台帳を通した id の採り方 | `ids.claim` / `next_id` / `advance` / `audit` | 2本（`npcs` と `402_`）。`npcs` は `max + 1` で台帳を進めず、次の町の生成でゲームに踏まれた（VERIFICATION_LOG.md §2.77） |
| HUD への置き場所 | `ui.overlay_host`（§5.1.3） | 2本 |
| 表示・ログ用の切り詰め | `frames.short` | 6本 |
| 人物の引き方と表示名 | `ui.character_of` / `character_name` | 5本 |
| 上限付きの記録・一度きりの警告 | `ctx.logger(cap=)` / `ctx.warner`（§3.11.2） | 5本 / 4本 |
| 次のフレームで走らせる | `ui.scheduler` | 5本 |
| 世界ごとの控えの出し入れ（場所・読み・キャッシュ・書き・錠） | `state.WorldStore`（§5.4） | 9本。フォルダを作る／作らない、錠を持つ／持たない、読めなかったときの倒し先の3つで枝分かれし、他の MOD の控えを読む側が相手のフォルダを作っていた |
| 重い処理を背景で直列にこなす | `jobs.Worker`（§5.5） | 4本。溢れの捨て方・重複除け・畳み方まで同じものが写っていた |
| モデルの返答から JSON を拾う | `llm.parse_json` / `strip_fence`（§5.3） | 5本。囲みの剥がし方が3通りに枝分かれしていた |
| モデルの返した真偽の読み方 | `llm.truthy`（§5.3） | 3本。判らない語をどちらへ倒すかが項目ごとに違うのに、関数の側で決め打ちしていた |
| ゲーム内の日付 | `ui.game_day`（§5.6） | 5本。ロード中の受け皿を持っていたのは `312_` だけだった |

```python
from instantale_modloader import state
worlds = state.WorldStore(ctx, "npc_profiles")  # 場所・読み・控え・書きまで（§5.4）
key, bucket = worlds.of(app)

from instantale_modloader.llm import wrap_outgoing
wrap_outgoing(ctx, rewrite, label="my mod")     # rewrite(texts, site) -> 並び / None
```

##### 名前を共有するときは `mod.json` で断る（`"shares"`）

MOD どうしは import しないので、**繋がるときは同じ名前を見る**ことになる。
`122_` は `113_` が HUD に控えたボタンを属性名で引き、
`301_` と `403_` は `311_` の控えを `state\` のフォルダ名で読む。
これは正しい繋がり方で、禁じてはいない。

困るのは**繋がるつもりが無いのに同じ名前になった**とき。
例外は出ず、片方の機能が黙って効かなくなる:

| 何 | ぶつかると |
|---|---|
| 自前ボタンの印（`mod_` で始まるもの） | 相手の `on_button_press` が自分のボタンを握り潰す |
| ウィジェットや `sys` の属性（`_instantale` で始まるもの） | 2本が同じ器を書き合う |
| `state\` のフォルダ名（`*_DIRNAME` の定数） | 2本が同じ控えを書き合う |

区別できるのは**断ってあるかどうか**だけなので、書く側に断らせる:

```json
"shares": ["npc_profiles"]
```

「この名前は自分のものではない」という意思表示。
同じ名前を使う MOD のうち、断っていないものが**ちょうど1本**なら持ち主が決まっている。
0本（誰も名乗らない）と2本以上（取り合い）は `tools/check_mods.py` が止める。

見ているのはモジュール直下の文字列定数だけで、
関数の中で組み立てる名前は追わない（**見えるものだけを確かめる**）。
誤検知でこの検査が信用されなくなる方が高くつく。

##### id は `ids.claim` で採る。自分で決めない

ゲームは `area` / `node` / `facility` / `npc` / `item` / `quest` の id を
セーブの `index` から連番で振り、既に同じ id が居ても上書きする（GAME.md §2.23）。
MOD が `max + 1` や `0` からの空き探しで id を決めると、台帳が追いつかないまま
次の生成でゲームが同じ番号を踏む。実際に店主の素データが差し替わった
（VERIFICATION_LOG.md §2.77）。

```python
from instantale_modloader.ids import claim
npc_id = claim(app, "npc", write=write)        # 台帳と実在の大きいほうを採り、台帳を進める
item_key = claim(app, "item", write=write)     # "item_<n>" の書式で返る
```

`ids.audit(app)` は台帳が実在に追いついていない種類を並べる（`make_npc` が
採番の前に呼び、ログに `ids: index behind existing ids:` を残す）。
セーブエディタで足した施設も台帳を進めていない（実セーブで facility が
台帳 230 に対し実在 234。2026-08-29）。

> `import state` ではなく関数を直に import する。
> `301_` は `apply()` の中に `state = {...}` というローカル変数を持っている。
> モジュール名で入れると、その代入によって**関数の中では `state` がローカル扱いになり**
> 参照が `UnboundLocalError` になる。

##### `state.py` は内部ヘルパではなく互換面

MOD から import された時点で、ここは `API = 1` と同格の約束になった。
`world_filename()` の出力を変えると、全 MOD の既存の `state/` がまとめて迷子になる
（`ctx.write_json()` は書き方が変わるだけだが、こちらはファイルの在り処そのもの）。

| 変えてよいもの | 変えてはいけないもの |
|---|---|
| 内部の書き方・コメント | 同じ鍵から出る名前 |
| 新しい引数を既定値付きで足す | 既定の挙動（`suffix=".json"` を含む） |

やむを得ず変えるなら、**古い名前でも読めるようにしてから**にすること。

`world_filename()` は単射でなければならない。
使える文字に均すだけだと同じ名前へ落ちる組が出る（`"a/b"` と `"a\b"`、`"CON"` と `"_CON"`）
＝ **別の世界の控えを自分のものとして読む**事故になる。
そこで均した結果が元の鍵と違うときだけ、鍵から作った短い印を後ろに付ける
（普通の世界名には印が付かないので既存のファイルはそのまま引ける）。検査は `test_state.py`。

#### 3.2.4 順序の前提は MOD 自身に宣言させる

順序ファイルは手で触るもので、こういう前提を知らない
（GUI で行をドラッグすれば壊せてしまう）。**文章で書いてあるだけでは守れない:**

```json
{"after":  ["103_fix_eventlog_trim"],      これより後（＝外側）に適用してほしい
 "before": ["105_fix_schema_compact"],     これより先（＝内側）に適用してほしい
 "conflicts": ["104_balance_area_bgm"]}    同時に有効にしても意味を成さない
```

`discover()` が**安定な**トポロジカルソートでこれを満たす。
基準の並びは `load_order.json` のままで、制約に触れない MOD の相対順は動かさない
（並べ替えた意図を、制約を満たす範囲でそのまま残す）。

| 状況 | 挙動 |
|---|---|
| 制約が実体の無い / 無効な MOD を指している | 黙って捨てる。ただし `problems` に報告 |
| 制約が伏せている MOD を指している | 黙って捨てる。報告もしない（§3.2.5） |
| 制約が循環している | `load_order.json` の並びで動かす（ここで全滅させない）。報告する |
| `conflicts` の相手が同時に有効 | 報告するだけで落とさない |

`conflicts` で片方を落とさないのは、
このローダでは同じ対象に複数の MOD を重ねるのが正常な使い方で、
どちらを外すべきかローダには決められないから。

> `load_order.json` を機械的な番号順に並べ直さないこと。
> 番号順は `after`/`before` を8箇所で破る（`117`→`112` / `213`→`311` / `215`→`313` /
> `217`←`314`/`307` / `218`←`315` / `223`←`402` / `314`→`307`）。
> 壊れはしない（ローダが並べ替えて動かす）が宣言と適用がずれ、
> `check_mods.py` が問題として出す。判定は `python tools/check_mods.py` が問題0になるか。

#### 3.2.5 開発者向けの MOD を伏せる（デバッグモード）

計測 MOD（`2xx`）は原因を測るための道具で、遊ぶだけなら要らない。
`mod.json` に `"debug": true` を付け、デバッグモードのあいだだけ動かす。

切り替えは `settings/loader.json` の `{"debug": true}`。
GUI の `gui.json` ではない（あれは GUI しか読まないが、この値はゲームの中で `discover()` が読む）。
`mod_settings.json` にも混ぜない（あちらの形は「MOD フォルダ名 → 値」）。

稼働の制御は `order` から外すだけで足りる。伏せかたで効くのは次の3点:

| 場所 | すること | 理由 |
|---|---|---|
| `discover()` | `order` からは外し、**`listed` には残す** | 一覧の並びは保存時にそのまま `order` へ書き戻される。`listed` から落とすと、GUI で保存した瞬間に順序ファイルから記述ごと消える |
| `_order()` | 「無効化されています」「記載の無い MOD」の報告から外す | 切ったのは `disabled` ではなくローダ |
| `_sort_dependencies()` | 伏せた相手を指した制約は報告しない | `300_` の `"after": ["205_"]` が毎回「無効な MOD を指している」に出る |

`tools/check_mods.py` は `discover(debug=True)` で呼ぶ。
**静的検査は入っている MOD を全部見るのが仕事**で、
デバッグモードを今どちらに倒しているかで検査の範囲が変わってはいけない。

`load_order.local.json` の有無で代用しない案もあったが、
「手元用の順序ファイルを置いている＝開発者」という暗黙の判定になる。
明示的なフラグなら、不具合報告のときに「デバッグモードを入れて再現してください」と頼める。

##### ゲーム本体が取り込んだ修正を降ろす（`superseded`）

このゲームは更新で MOD 側の修正を取り込むことがある（main_024 では6件）。
取り込まれた修正は要らなくなるが、消してしまうと退行したときに気付けない。
そこで `debug` と同じ扱いで伏せる: `{"superseded": "main_024"}`（値は取り込まれた版）。

読み込みの扱いは `debug` と全く同じで、分けてあるのは**伏せた理由が違う**から
（同じ見た目だと「計測のために作ったもの」と「要らなくなった修正」が混ざり、
次にゲームが更新されたときどれを試しに戻すか分からなくなる）。

降ろす前に、その MOD 自身の印で確かめること。
症状が出ないだけでは、本体と MOD のどちらが直したのか区別できない（GAME.md §1.5 / §1.6）。
判定に使った根拠は GAME.md 側に残す（`mod.json` に書けるのは結論だけ）。

セーブに残るものを書き換える MOD は、降ろす動機が一段強い。
`110_` は名前を書き換えてセーブに焼く一方、本体はパスの側で消毒するので、
本体が直った後も残すとこちらだけが余計に改変する側に回る。
冪等なもの（クランプ・刈り込み）や受動的なもの（発火時に記録するだけ）は残しても害が無い。

### 3.3 同じ場面に複数の MOD が乗るとき

外側が処理を止めれば内側には呼び出しが届かない。
`304_` が解散そのものを止めると、`303_`（外れた仲間の置き先を変える）には
`remove_party_member` が来ない。
重ねるなら「外側の層が降りたとき、内側は本来どおり動く」形にしておくこと。

**印のキーは MOD ごとで変える**（同じキーだと押下が食い合う。§5.1.1）。

#### 3.3.1 同じ値を2つの MOD が書くなら、勝敗は `orig` の前か後かで決まる

適用順では決まらない。**後から書いたほうが勝つ**ので、
「外側＝勝ち」ではなく「`orig` を呼んだ後に書くほうが勝ち」になる。

| 外側の書く時点 | 内側の書く時点 | 最後に書くのは |
|---|---|---|
| `orig` の後 | `orig` の後 | 外側 |
| `orig` の**前** | `orig` の後 | 内側 |

**同じ MOD の中で時点が揃っていないと、相手はどちらの層に居ても負ける。**
`129_balance_item_price` は画面へ出す2箇所だけ描く前に書き（`orig` の前）、
残り8箇所は `orig` の後に書く。
`405_regional_economy` を内側に置くと画面の2箇所では乗るのに、
`set_shop_price_for_owner` では9回とも消えた（VERIFICATION.md §3.19.1）。

層を置き直しても解けないので、**書く側が後処理の口を持つ**:

```python
POST_ATTR = "_instantale_item_price_post"   # sys に置いた (名乗り, 関数) のリスト

hooks = getattr(sys, POST_ATTR, None)       # 相手が先に作っていることがある
if not isinstance(hooks, list):
    hooks = []
    setattr(sys, POST_ATTR, hooks)
hooks[:] = [e for e in hooks if e[0] != 自分の名乗り]   # 再注入で重ねない
hooks.append((自分の名乗り, fn))                        # 中身を書き換える。差し替えない
```

書く側は値を書いた**直後**にこれを回す。
どの地点で書いても同じ呼吸で後処理が乗るので、適用順にも経路にも依存しなくなる。
借りる側は `mod.json` の `"shares"` にこの名前を書く（§3.2.3 の名前の断り）。

### 3.4 まだ現れていない対象を狙う（保留と当て直し）

ゲームは `llama_cpp_runtime_completion` と `scripts.llm.llm_manager` を
最初の LLM リクエストまで import しない。
注入はそれより前に済むので、素朴に書くとプロンプト関係のフックが1つも設置されないまま進む。
`patch.py` はこれを吸収する:

| 状況 | 挙動 |
|---|---|
| モジュールが未 import | `required` に関わらず保留（`defer wrap ...` を記録） |
| モジュールが**import 実行途中**（`__spec__._initializing`） | `required` に関わらず保留 |
| `__main__` に**持ち主のクラス**がまだ無い | `required` に関わらず保留 |
| 属性が無い（上記以外） | `required` に従う（本物の間違いなので黙らせない） |

2行目は**「載っていること」と「中身が揃っていること」が別**だから。
import は先に `sys.modules` へ登録してから本体を走らせるので、
その間に注入するとモジュールは在るのに関数がまだ無い。
走っている間だけ `__spec__._initializing` が True になるので、
聞けば「打ち間違い」と「まだ来ていない」を推測なしに分けられる。

3行目が別に要るのは、**`__main__` は `__spec__` を持たない**ため（起動スクリプトなので）。
`__main__`（約1万行）は最初の1行から載っていて、そこからクラスを組み立てていく。
インタプリタ初期化の時点で注入すると `World` も `InstantaleApp` もまだ無い。
これは打ち間違いではなく順番の問題（2026-08-15〜19 に、初回ブートで5本が
`AttributeError: module '__main__' has no attribute 'World'` で `apply()` ごと落ちていた）。

> `__main__` の側では持ち主だけを見る。
> 葉（`World.generate_character` の `generate_character`）が無いのは
> 打ち間違いかゲーム更新で消えたかなので、待たずに `required` に従う。
> 2行目（実行途中と**分かっている**）は葉も対象にしてよい。

保留があると `boot()` が監視スレッドを立て、5秒ごとに見て現れた時点で `boot()` をやり直す
（当て直しは手作業の再注入と同じ経路なので層は重ならない。上限は 8回 / 1時間）。

来たかどうかの見方は2つで別々:

| 待っているもの | 見方 |
|---|---|
| モジュール | `sys.modules` に載ったか（`patch.pending_modules`） |
| `__main__` の持ち主 | `resolve()` が通るようになったか（`patch.owners_ready`）。**`__main__` は最初から `sys.modules` に居るので、そちらを見ても分からない** |

#### 来ないと分かったら降ろす

ゲームは選ばれたプロバイダの送信モジュールを1つだけ import する（GAME.md §2.12）ので、
クラウド実行では `llama_cpp_runtime_completion` が一生 import されない。
見張りは 5秒ごとに `llm.is_cloud_runtime()` を見て、そうと分かった時点で
ローカル専用の保留を `skipped` へ移し、`status.json` を書き直して降りる。

| 台帳の種類 | 意味 |
|---|---|
| `deferred` | まだ来ていない。見張りが待っている |
| `skipped` | 待つのをやめた。理由は detail に残る（`not used with openai` / `gave up after 3600s`） |

`is_cloud_runtime()` は `is_local_runtime()` の否定ではない。
最初の LLM リクエストまではどちらも False で、
そこで決めつけるとローカル実行の保留まで降ろしてしまう。降ろすのは「クラウドと分かった」ときだけ。

**降ろした分は消さずに残す**（台帳の合計が合わなくなると、
その MOD のフックがどこへ行ったのかを追えなくなる）。
GUI は件数だけを状態欄に出し、失敗ではないので ⚠ には出さない。

> これを入れる前は、クラウドで動かしたときの GUI が「段階適用の途中（未 import 14件）」を出し続けていた。
> 件数が減らないので、正常な起動が毎回「途中で止まっている」ように見えていた。

**MOD 側でやること**: 対象が未 import でも `apply()` は普通に書いてよい。
ただし `apply()` は当て直しのたびに走るので、
**何度走らせても結果が変わらないように書く**（副作用のある初期化は `ctx.on_ready()` へ。§3.6）。

### 3.5 再注入しても層が積み重ならない（世代管理）

`boot()` は再 import でローダを作り直すが、ゲーム側に差し込んだ関数は残る。
`patch.py` は各 boot に世代 ID を振り、**他世代の層だけ**を剥がす
（自分の層まで剥がすと、同一 boot 内で `200_` が `101_` を包んだ瞬間に修正が消える）。

| ログ | 意味 |
|---|---|
| `boot #N gen=xxxxxxxx` | この注入の世代 |
| `replacing a previous patch layer on ...` | 前回注入の層を剥がした（正常） |
| （この行が出ない） | 同一 boot 内で後段の MOD が包んだ ＝ 先の層が保持されている |

読み直されるのはモジュールも同じで、注入のたびに `sys.modules` から落として入れ直すものが3段ある:
ローダ本体 / MOD の入口 / **MOD の中の部品**。

3段目が要点。
入口だけ読み直して `from . import panel` の相手を残すと、新しい入口が古い部品を呼ぶ。
分割した MOD を直して注入し直したのに、部品に足したばかりの関数が `AttributeError` になる
（`116_` で実際に踏んだ）。
しかも入口側のコードは新しいので、ログを読んでも「直したはずの行」で落ちているように見える。

### 3.6 1回きりの初期化（`ctx.on_ready`）

`apply()` は1プロセスの中で何度も呼ばれる（手で注入し直したときと、当て直し。最大8回）。
パッチを当てるだけなら世代管理が結果を1回分にまとめるが、
**副作用のある初期化は回数ぶん繰り返される**（迷子の曲の掃除 / 状態ファイルの初期化 / スレッドの起動）。

```python
def apply(ctx):
    @ctx.wrap("...")            # パッチは apply() の中で当てる
    def hook(orig, *a, **kw):
        return orig(*a, **kw)

    ctx.on_ready(lambda: sweep_orphan_tracks(ctx))   # 掃除は1回だけ
```

| 項目 | 挙動 |
|---|---|
| 実行回数 | プロセスにつき1回。再注入・当て直しをまたいでも増えない |
| 実行スレッド | Kivy の `Clock` 経由＝メインスレッド（`boot()` はリモートスレッドの上） |
| タイミング | 全 MOD の適用が済んでから。`delay=` で先送りできる |
| 例外 | 握り潰してログへ（`Clock` の中で投げるとゲームが落ちる） |
| キー | 既定は「MOD 名 + 関数名」。`key=` で明示できる |
| 戻り値 | 積まれたら `True`、既に実行済みで捨てられたら `False` |
| `force=True` | 印を無視して積み直す。**開発中の逃げ道（配布する MOD に書かない）** |
| `reset_once("300_")` | ローダ側から印を落とす。同じく開発用。副作用は戻らない |

**印は積んだ時点で付ける**（実行時ではない）。
流し切る前に次の boot が来ても二重積みを起こさないため。
ただし `Clock` に載せられなかった場合は一度も走っていないので印を外す
＝ 印の意味は「実行した」ではなく**「実行したか、もう走ることが確定している」**。

「1回だけ」の印は `sys` に置いてある
（注入し直すとローダのモジュール自体が読み込み直されるので、モジュール変数だと印ごと消える）。
`Clock` が無い環境（オフライン検証）ではその場で同期的に呼ばれる。

#### 3.6.1 見張りを `on_ready` で立てるときの罠

注入し直しても立ち上がらない。
印はプロセスに残るので2回目は黙って捨てられ、`Clock` の予約は `revert_all()` でも取り消せない
＝ **古い版の見張りが回り続ける**（`211_` で実際に踏み、
計測を足した版を注入したのにログが1行も増えず「ゲームが何も出していない」と読み違えるところだった）。

1回きりの初期化（掃除・状態ファイル）なら意図どおりだが、
注入し直すたびに入れ替わってほしいもの（見張り・計測）は別の書き方が要る。
組は2つ。新しい版を必ず立てる（キーに世代を混ぜる）ことと、古い版が自分で降りること:

```python
def apply(ctx):
    def start_poll():
        def poll(_dt):
            if ctx.superseded():
                return False                # 新しい注入が来た ＝ Clock から降りる
            ...
            return True
        Clock.schedule_interval(poll, 1.0)

    ctx.on_ready(start_poll,                # キーに世代を混ぜる（混ぜないと2回目以降は積まれない）
                 key="211_probe_text_speed:poll:{}".format(ctx.generation))
```

自前のスレッドも同じで `while not ctx.superseded():` の形にする。
`force=True` でも積み直せるが、あれは印を無視するだけで**古い見張りは止まらない**（二重に回る）。

##### 降りる合図を MOD 側で作らない

`ctx.superseded()` は2つ見ている。
同じローダで次の boot が走った（`generation` が変わった）か、
注入し直されて**ローダごと読み込み直された**か。
後者では古い版が握っている `_state` はもう誰も更新しないので、
世代を比べるだけでは永遠に「まだ現役」に見える（`sys.modules` の中身で見分けるしかない）。

以前は `206_` が `__main__` に、`211_` が `sys` にそれぞれ自前の合言葉を置いていて、
どちらも2つ目の判定が無かった。
**世代の持ち回りはローダの語彙**（§3.2.3）なので MOD 側で作り直さない。

> 似て見えるが別のものが2つある。
> `118_` の `state["generation"]` は「走っている一括表示の続きを止める」印で注入とは無関係。
> `311_` のワーカーは世代をまたいで1本のまま使い続けるのが正しい（降ろすと処理中の抽出が消える）。
> `ctx.superseded()` を足すのは、自分の世代のためだけに回しているものに限る。

### 3.7 誰がどこへ当てたか（台帳）

`patch_registry.py` が「どの MOD がどの対象に当てたか」を持つ。
同じ対象に複数の MOD を意図的に重ねる設計なので、
意図しない重なりをログを目で追わずに見つけるため。

#### 3.7.1 `boot()` の最後に出る報告

```
patches: 61 applied on 54 target(s) by 26 mod(s)
overlapping targets (5):
  llama_cpp_runtime_completion:LlamaCppClient.chat <- 105_fix_schema_compact/, 111_llm_prompt_replace/
deferred (2): waiting for the module to be imported
UNRESOLVED (1): target not found in the running build
  scripts.ui.shop:ShopFrame.refresh <- 108_fix_shop_inventory_overflow/ (attribute not found)
```

| 節 | 意味 | 対処 |
|---|---|---|
| `overlapping targets` | 2つ以上の MOD が同じ対象を触っている | 正常なことも多い。§3.2.2 の帯順と突き合わせる |
| `deferred` | モジュールが未 import。後で当て直す（§3.4） | 待てばよい |
| `UNRESOLVED` | モジュールは在るが対象が無い | ゲーム更新を最初に疑う。`out/recon/` で名前を取り直す |

`UNRESOLVED` は `required=True` なら例外にもなるが、**投げる前に記録している**
（その MOD が `apply-error` で落ちても、何が見つからなかったかは報告に残る）。
バージョン番号を宣言させるより、実際に対象が在るかを見る方がこの環境では確実。

#### 3.7.2 プロセスの中からの問い合わせ

```python
instantale_modloader.patches()      # {対象: [MOD, ...]}
instantale_modloader.mod_patches()  # {MOD: [対象, ...]}（逆引き）
instantale_modloader.conflicts()    # 重なっている対象だけ
instantale_modloader.status()       # 下記すべてを1回で
```

`status()` のキーは
`["mods"]`（`ok` / `no-entry` / `load-error` / `apply-error` / `no-apply` / `api-too-new` / `api-too-old`）/
`["manifests"]` / `["settings"]` / `["problems"]` /
`["patches"]`（`by_target` / `by_mod` / `conflicts` / `deferred` / `unresolved` / `counts`）/ `["api"]`。

`format_report()` が「人が読む行」を返すのに対し、`status()["patches"]` はデータのまま返す
（並び順や言い回しは受け取った側で決められるよう、ここでは整形しない）。

`apply()` の中からは `ctx.patches()`（見えるのは自分より前に読み込まれた MOD の分だけ）。

#### 3.7.3 `out/status.json`（ゲームの外との唯一の接点）

`boot()` の最後に書き出す。
ゲームの外からこれを読むのが、GUI と「実際に動いたゲーム」の唯一の接点。
**注入が成功したことと MOD が入ったことは別の話**で、
ここを読まないと「85個中3個が `apply-error`」を GUI に出せない。

ゲームの中へ問い合わせる経路を作ると注入をもう1本増やすことになるので、
こちらから書き出す形にしてある（1方向で済み、ゲームが終了した後でも読める）。
遅延当て直しのたびに上書きされるので中身は常に最新の boot で、
`*.log` とは別扱いで世代管理しない（常に「今の状態」を表すファイルで、履歴に意味が無い）。

書き出すのは boot の最後の1回きりなので、
`apply()` を抜けた後に設置したフックはこのファイルに出ない。
`001_crash_recorder` のように「対象が現れるのを見張って後から `ctx.wrap` する」MOD は、
設置できた時点で `ctx.refresh_status()` を呼んで揃える。
`apply()` の中では呼ばない（まだ他の MOD が控えている段階で書いても、boot の締めで上書きされるだけ）。

### 3.8 GUI から変えられる設定（`ctx.config`）

「`.py` の先頭の定数を書き換える」だと2つ困る。
GUI から見えないことと、MOD を更新すると設定が消えること。

#### 3.8.1 値の置き場所をコードの外へ出す

MOD のコードは何も変えなくてよい。

```
mods/300_event/mod.json    "settings" に何が変えられるかを宣言する
mods/300_event/event.py    EVENT_MODE = "conversation"   ← 既定値。そのまま残す
settings/mod_settings.json 選んだ値だけ
```

```json
"settings": {
  "EVENT_MODE": {"type": "choice", "values": ["conversation", "narration"],
                 "default": "conversation",
                 "label": {"ja": "イベントの出方", "en": "Event style"},
                 "note":  {"ja": "narration は情景描写に一言足すだけ", "en": "..."}},
  "COOLDOWN_VISITS": {"type": "int",   "default": 2, "min": 0, "max": 20},
  "CHANCE_OVERRIDE": {"type": "float", "default": null, "allow_null": true}
}
```

ローダは MOD を読み込んだ後・`apply()` を呼ぶ前に、選ばれた値をモジュールのグローバルへ書き込む。
`apply()` の中で作られる入れ子の関数は定数をモジュールのグローバルとして読むので、
この順なら定数をそのまま使っているコードに新しい値が届く。

| 項目 | 内容 |
|---|---|
| 扱う型 | `bool` / `int` / `float` / `str` / `choice`（+ `min` / `max` / `allow_null`） |
| GUI の空欄 | `int` / `float` では「未指定」。**`str` では空文字列という値**（「空でゲームのまま」のような設定が成り立つ） |
| 置き場所 | `settings/mod_settings.json`。`mods/` の中には書かない（そこは配布物そのもの） |
| 書くのは | 既定と違う値だけ。既定に戻したら消える |
| 読めない値 | 黙って既定に倒す（設定ファイルが壊れて MOD が全滅しないように）。ログに残る |
| 宣言だけあってコードに定数が無い | 書き込まない＋警告（`@patch` が名前を新設しないのと同じ理由） |

#### 3.8.2 何を宣言し、何を宣言しないか

辞書やタプルの設定は宣言しない。
GUI の1行に収めると「JSON を手で書く欄」になり、コードを直接編むより分かりにくい。

ただし「プレイヤーの体験に関わる設定」なら、宣言できる形に割るほうを選ぶ。
施設別の発生率は元々 `CHANCE_BY_TYPE` という1つの辞書だったが、
いまは `CHANCE_INN` / `CHANCE_GUILD` / … と種別ごとの `float` に割って宣言してある
（施設種別はセーブの `facility_type` そのもので、ゲーム側で閉じた集合なので際限なく増えることはない）。
表は `apply()` の中で組み直す（トップレベルで組むと既定値の表が固まってしまう）。

#### 3.8.3 既定値が2箇所に書かれること

認めている。
実際に使われるのはコードの定数で、GUI が表示に使うのは `mod.json` の `"default"`
（GUI は MOD のコードを import しない決まりなので定数を読めない）。
ずれると「GUI では既定3と出るのに実際は5で動く」という最も気付きにくい形になるので、
`check_mods.py` が AST で突き合わせて報告する。

### 3.9 ローダ API の契約（`"api"`）

`mod.json` の `"api"` が、その MOD が前提にしているローダ API の番号。
`boot()` はコードを読み込む前にこれを見て、扱えない MOD を撥ねる（名乗りが JSON にあるからできる）。

| 状況 | 挙動 |
|---|---|
| 書いていない | `1` として扱う（`DEFAULT_API`） |
| ローダより新しい | 読み込まない。`api-too-new` |
| `MIN_API` より古い | 読み込まない。`api-too-old` |
| `ctx.api` | MOD 側から番号を見る（下位互換の分岐が要るとき） |

`__version__` とは別に持っている（前者は配布物の版で、上がっても MOD が壊れるとは限らない）。
`API` は壊れる変更のときだけ動かす番号で、だからこそ判定に使える。

| 上げる | 上げない |
|---|---|
| `ctx` のメンバを削除・改名 | `ctx` にメンバを追加 |
| 引数の順序・意味を変更 | 省略可能なキーワード引数を追加 |
| 既定値の変更（`alias_scan` / `required`） | ログの書式・内部の整理 |
| `on_ready` のキー導出の変更 | `__version__` だけの更新 |
| `ui.Screen` の signature 変更 | `ui` / `frames` への関数追加 |

##### この番号が守るのは §5 に載っているものだけ

外部の MOD 作者が使ってよい面（＝この契約が守る面）は次の3つ。

| 面 | どこ |
|---|---|
| `apply(ctx)` に渡る `ctx` | §3.1 / §3.6 / §3.8 / §3.11 |
| `mod.json` の鍵 | §3.1 / §3.2 / §3.8 / §3.9 / §3.12 |
| §5 の共通部品（`ui` / `frames` / `llm` / `state` / `jobs`） | §5 |

これ以外は内部で、予告なく変わる。
`patch.py` の `_defer_if_*` や `__init__.py` の `_order()` のような
先頭に `_` の付くものはもちろん、`recon.py` や `patch_registry.py` の
関数もここには入らない（調査のための道具で、MOD の動作の前提にするものではない）。

線を引いておくのは、**外から使われていると分かった面は直せなくなる**から。
どこまでが約束かを書いていないと、
「内部だから直した」と「使っていたのに壊れた」が同時に成り立つ。

ゲーム側のバージョンを宣言させないのとは事情が違う。
ゲームの版は信頼できる形で取れず、依存先が在るかは実行時に確かめられる（台帳の `UNRESOLVED`）。
ローダ API はその逆で、版は確実に取れる一方、
**意味の変化は `hasattr` では捕まえられない**（`alias_scan` の既定を変えるといった変更は、
例外にならないまま挙動だけを変える）。

### 3.10 パッチを剥がす（`unload`）

```powershell
python tools/injector.py --unload      # GUI なら「MOD を外す」
```

ゲームを終了せずにパッチを剥がす（MOD を疑うときの切り分けに使う）。
剥がすための記録は `sys` に置いてあるので、
注入から今までの間にローダが何度読み直されていても剥がせる。

属性を戻すだけでは足りない。
当てたときに張り替えた複製束縛（`from x import y` のコピー）はラッパを指したままで、
そこから呼ばれる経路が生き残る（当てたときと同じ範囲を逆向きに張り替える。§4.1）。

完全に元通りにはならない。戻らないのは
`on_ready` で既に起きた副作用 / MOD がゲームの状態そのものに書いた値 /
MOD が立てたスレッドや Clock の予約。
素のゲームで確かめたいなら、注入せずに起動し直すのが確実。

### 3.11 書き込み先（`out/` と `state/`）

配布フォルダ直下に書いてよい場所は3つ。役割で分けてあり、混ぜない。

| 場所 | 何が入るか | 消すと |
|---|---|---|
| `settings/` | 設定したこと（MOD の設定・GUI の覚え書き・デバッグモード） | 既定に戻る |
| `out/` | MOD が吐いたもの（ログ・リコン成果物・`status.json`） | 何も起きない |
| `state/` | MOD が持つ永続データ（進行中の道中・依頼の出所・NPC の控え） | **遊びが巻き戻る** |

```python
log_path     = ctx.out_path("road_travel.log")     # 追えればよい記録
journey_path = ctx.state_path("road_travel.json")  # 続きに要るデータ
```

判定は「消されたときに何が起きるか」で行う。

元は `out/` が両方を兼ねていたが、性質が正反対なので破綻した。
不具合報告で「`out/` を消してから再現してください」と言えない、
世代管理が「対象は `*.log` だけ」という但し書きだけで永続データを守っている、
ログのつもりで消したものが遊びの続きだった。

セーブへ書かず `state/` に持つ理由は、
セーブの構造を壊さずに足せない（NPC は33項目の並びが決まっている。GAME.md §2.23）か、
足しても往復で残る保証が無い（`Quest` が独自キーを写すかは読めない）かのどちらか。
どちらも「ゲームのデータを汚さない」ための判断で、
その代わりに置き場所の責任がこちらへ来る（**MOD が足したものは MOD が片付ける**）。

置き場所を分ける前に遊んでいた人のデータは `ctx.state_path()` が拾う
（`state/` 側に無くて `out/` に同じ名前が在れば1度だけ移す。両方には残さない）。

> 他の MOD が持っているデータを読むときは、フォルダを作らないこと。
> `ctx.state_path()` は親を作るので、`os.path.join(ctx.state_dir, ...)` で組む
> （相手を切っている人の `state/` に、使われない空のフォルダを置かないため）。

#### 3.11.1 書くときは `ctx.write_json()`、読むときは `ctx.read_json()` を通す

`open(path, "w")` で残すデータを書かないこと。
開いた時点でファイルを切り詰めるので、書いている途中で落ちるとその瞬間に中身が壊れる。
読む側は壊れた JSON を黙って `{}` に倒すのが常なので、
**消えたことに気付けないまま次の更新で上書きされる**（NPC の記憶なら1人ぶんだけが書かれ、他が全員消える）。

読み側にも同じ規則がある。
素朴な `open` + 広い `except` で `{}` に倒すと、
「無い（初回・正常）」と「在るのに読めない（一時ロック・外部破損）」の区別が消える。
後者を黙って倒したまま次の書き込みをすると、
`write_json()` がいくら壊れない書き方でも**空に近い正本を無傷で作ってしまう**。
壊れずに、静かに失われる。
`ctx.read_json()` は前者だけを黙って `default` に倒し、後者は記録してから倒す
（**倒した先が読めることより、消えたことが後から追えることが要点**）。

やっているのは3つ。
隣に `名前.tmp` を書く → `flush` + `fsync` でディスクまで落とす → `os.replace` で差し替える。
2つ目を省くと電源断で「差し替えは済んだが中身は空」になりうる。

**例外を投げない**（成否は戻り値で返る）。
呼ぶのはゲームのスレッドの中で、書けないことよりゲームを巻き込むことの方が困るため。

| 場面 | 使うもの |
|---|---|
| 残すデータ（`state/`） | `ctx.write_json()` / `ctx.write_text()`、読むのは `ctx.read_json()` |
| ログの追記（`out/`） | `ctx.logger()`（§3.11.2）。1行ずつ足すだけなので tmp→replace は通さない |
| GUI の操作の結果 | **例外にする**（`config._save_settings_json` / `gui.write_order`）。GUI がダイアログに出す。黙って False を返すと、保存されていないのに保存されたように見える |

> 以前は `311_` / `312_` / `122_` が同じ tmp→fsync→replace を各自で持ち、
> 一方で `301_` / `307_` / `config.save_store` は素の `open(..., "w")` のままだった。
> 理屈は全部に等しく当てはまるのに、書いてある場所にだけ適用されている状態だった。

#### 3.11.2 MOD 専用のログは `ctx.logger()` で作る

```python
write = ctx.logger("quest_offer.log")                # [時刻] 本文
write = ctx.logger("bgm.log", tag="[BGMFIX]")        # [時刻] [BGMFIX] 本文
write = ctx.logger("item_detail.log", stamp=False)   # 本文だけ
```

| 引数 | |
|---|---|
| `tag` | 時刻と本文の間にそのまま挟む（区切りの記号も込みで渡す）。角括弧の形と区切りの形が両方使われていて、どちらも実機の記録として GAME.md / VERIFICATION_LOG.md に引用されている。**体裁を揃えると、その引用が次のプレイのログと一致しなくなる** |
| `stamp` | 時刻を付けるか（既定 True） |
| `label` | 書けなかったときに `modloader.log` へ出す名前。既定は MOD のフォルダ名 |
| `cap` | この関数からの書き込みをこの行数で打ち切る。毎フレーム呼ばれる場所からの記録用。**数える器は関数の中なので、注入し直すと上限は戻る**。世代を跨いで数え続けたいものはこれに寄せない |

一度しか出さない警告は `ctx.warner()` で作る
（行き先は `modloader.log`。起きているのは MOD の異常ではなくゲーム側の形が想定と違うことなので、
共用のログでよい）:

```python
warn_once = ctx.warner("party expand")
warn_once("no_hud", "HUD が見つからない")   # 同じ鍵の2回目からは何もしない
```

書けなくても例外にしない。錠は中に持っているので、別スレッドから書く MOD も自分で掛けなくてよい。

MOD のログはローダのログ（`ctx.log`）と分ける
（`modloader.log` は全 MOD の共用なので、混ぜると1本を追うのに他の全部を読むことになる）。

> この7行は**42本の MOD に写されていた**（時刻付き・印付き・時刻なし・錠付きの4通りに枝分かれした状態で）。
> 写して回るものはローダの語彙（§3.2.3）。

### 3.12 MOD 同梱の設定画面（`"tool"`）

§3.8 の設定は「1行に収まる値」しか宣言できない（§3.8.2）。
曲の一覧から選ぶ・他のセーブを読んで NPC を選ぶ、のような設定は、
MOD が自分の画面を持つほうが分かりやすい。
`mod.json` に `"tool"` を宣言すると、ローダの設定画面の「設定…」がその MOD では
宣言の設定ダイアログの代わりに同梱の画面を開く。

```json
"tool": {"entry": "tool.py",
         "label": {"ja": "戦闘BGMを選ぶ", "en": "Choose battle BGM"},
         "note":  {"ja": "…", "en": "…"}}
```

| 項目 | 内容 |
|---|---|
| `entry` | MOD フォルダからの相対パス。必須（無ければ宣言ごと無視して WARN） |
| `label` / `note` | 表示用。片方の言語しか無ければもう片方で埋める（`name` と同じ） |
| 開き方 | `gui.py` が `[sys.executable, <MOD>/tool.py]` を**別プロセス**で起動する。`cwd` は MOD のフォルダ |
| 渡すもの | 引数ではなく環境変数。`IML_ROOT`（配布フォルダの根）/ `IML_STATE_DIR` / `IML_GAME_DIR`（未設定なら空）/ `IML_MOD_SETTINGS`（`mod_settings.json` のパス） |
| `"settings"` との関係 | 両方宣言してよい。ただし「設定…」は道具を開くので、**宣言の設定もその画面で引き受ける**（`322_` が `instantale_modloader.config` の `load_store` / `save_store` で同じ `mod_settings.json` に書いている） |
| 一覧の「設定」列 | `settings` か `tool` があれば ○/● が付く |

別プロセスにするのは、GUI が「MOD のコードを一切 import しない」（`gui.py` 冒頭）を守るため。
引数ではなく環境変数で渡すのは、道具側の引数の書式を縛らないため。
道具は普通の tkinter スクリプトでよく、`IML_ROOT/runtime` を `sys.path` に足せばローダの語彙
（`write_json` / `config`）をそのまま使える。配色と書体は `tools/gui.py` の `setup_theme()` を借りる。

直接起動（`python runtime/mods/322_battle_bgm/tool.py`）もできるようにしておく。
環境変数が無いときは自分の位置と `settings/gui.json` から場所を組む（`322_` の `locate()`）。

> 元は `323_npc_carryover` の設計。先に `322_battle_bgm` で実装した。
> `131_sharp_portrait` も同じ契約で、既存 NPC の顔の一括切り直しをこの画面に持つ（設定もそこで引き受ける）。

---

## 4. Nuitka 環境の制約

### 4.1 効くもの・効かないもの

**効く**: `mod.func = new`（コンパイル済みコードもグローバルはモジュール辞書経由で引く）/
`Cls.method = new`（Nuitka のクラスは通常の `type`）。

**効かない・要注意**:

- `from x import y` で他モジュールに複製された束縛。
  `patch()` / `wrap()` は既定で `alias_scan=True` にしてあり、
  同一オブジェクトを指すグローバルをスキャンして再束縛する。
  これが無いと「`x.y` は直したのに呼ばれ続ける」が起きる
  （Kivy の `wm_pen` / `wm_touch` が実例で、修正はどちらも複製束縛側から呼ばれる）
- 単一のコンパイル済み関数内でローカル解決された呼び出し → 到達不能。
  呼び出し元の関数ごと差し替えること

張り替えを探す範囲は絞ってある（既定はゲーム自身のモジュール＋対象と同じトップレベルパッケージ）。
配布物には約4200のモジュールが入っているので、全件なめると
コストが積み上がる（当て直しは最大8回ある）のと、
同じオブジェクトを指しているだけの無関係な名前まで張り替わるのと2つ起きる。
対象のトップレベルを足しているのは、ゲーム以外を狙うパッチのため
（`kivy.input.providers.wm_common` の複製束縛は kivy の中にある）。
全部なめてほしいときは `alias_scan="all"` を明示する。

### 4.2 テストで identity 比較を使わない

`alias_scan` は古いラッパを指す変数を張り替えるため、
テスト側が握っている `__main__` のグローバルまで張り替えられる
（`__main__` は `GAME_TOPLEVEL` に入っていて、直接実行時の `__main__` はテスト自身）。
`Cls.method is not before` は成立しない。確かめるべきは連鎖の段数と呼び出し結果。

### 4.3 テストのクラスをグローバル名から派生させない

直接実行時の `sys.modules['__main__']` はテスト自身なので、
`main.InstantaleApp = app_cls` はテストのグローバル名を書き換える。
`type("InstantaleApp", (InstantaleApp,), {})` と書くと、2回目以降は前回の派生クラスから派生する
（前のテストのフックが積み上がって、同じ処理が何度も走る）。
派生元は `BASES` のような表に控えておく。

---

## 5. 共通部品

実機で確かめた「ゲームがどう動いているか」はここに集約する。
**同じ発見を MOD ごとに書き直さないこと**（片方が古くなるのは時間の問題で、
実際に8件の反映漏れが生まれた）。
MOD に残すのはその MOD の設計判断だけ。

### 5.1 `instantale_modloader.ui`

#### 5.1.1 組み立て

`Screen` は `apply()` の中で1つ作って閉じ込める:

```python
from instantale_modloader import ui

MARK = "mod_my_action"        # モジュール直下に置く（他の MOD と別の文字列にする）

def apply(ctx):
    write  = ctx.logger("my_mod.log")
    screen = ui.Screen(ctx, write, tag="my mod", mark=MARK)
```

| 引数 | 何を渡すか |
|---|---|
| `ctx` | そのまま渡す（例外を `ctx.log_exc` に流すため） |
| `write` | この MOD 自身のログ関数（`ctx.log` とは分ける。§3.11.2） |
| `tag` | ログと例外の見出し |
| `mark` | 自前ボタンに付ける印のキー。**MOD ごとに別の文字列にする** |

`mark` は2段になっているので混同しないこと。
`Screen(mark=...)` がボタン辞書のキーで、`button(mark=...)` がその値:

```python
entry = screen.button("依頼を受ける", mark="offer")
# → {'text': '依頼を受ける', 'spec': PhaseSpec(...), 'mod_my_action': 'offer'}
screen.mark_of(entry)        # 'offer'（自分のボタンでなければ None）
```

キーを他の MOD と共有すると、相手の `on_button_press` が自分のボタンを握り潰す。
同梱 MOD が使用中のキーは4つ（`mod_action` / `mod_party_action` /
`mod_road_action` / `mod_pardon_action`）。

印のキーは必ず `ui.MARK_PREFIX`（`mod_`）で始めること。
残骸の掃除（`prune_stale`）は「他の MOD が今その場に出しているボタン」を見分けるのに
この接頭辞だけを手がかりにしているので、
外れた印を使うとその MOD のボタンは他の MOD の掃除で消される。

#### 5.1.2 自前の選択肢を出して押下を拾う（最小の流れ）

`app` は `apply()` の時点ではまだ存在しない（`ui.find_app()` で引くか、フックの `self` を使う）。

```python
    # 1. ボタンを作る。cls_name を省くと無害な JustSetButtonToNormalPhase が付く
    entry = screen.button("依頼を受ける", mark="offer")

    # 2. 差し替えて塗る。Clock 経由なので「次のフレーム・メインスレッド」で行われる
    screen.apply_buttons(app, [entry, cancel], "confirm")

    # 3. 押下は文字列ではなく印で横取りする
    @ctx.wrap("__main__:InstantaleApp.on_button_press", safe=True)
    def on_button_press(orig, self, *args, **kwargs):
        index  = args[0] if args else None
        action = screen.mark_of(ui.pressed_entry(self, index))
        if action is None:
            return orig(self, *args, **kwargs)   # 自分のボタンでなければ素通し
        # 4. 自前フェーズを起こすなら start_phase（PhaseSpec には載せない）
        screen.start_phase(self, MyPhase(self), "依頼を受ける")
        return None
```

判定を文字列でやらないのは、同じ表示文字列のゲーム側ボタンを巻き込まないため。
UI を触る処理は必ず `screen.schedule` / `apply_buttons` を通す
（Clock 経由 ＝ 順序とスレッドが同時に片付く）。
Clock から呼ばれる処理で例外を外に出すとゲームを巻き込むので、
自前のコールバックは `screen.guarded(fn)` で包む。

#### 5.1.3 よく使う操作

```python
entry = screen.button("依頼を受ける", mark="offer")    # 無害な既存 spec を持たせる
screen.apply_buttons(app, [entry, cancel], "confirm")   # 次のフレームで差し替え＋塗る
screen.start_phase(app, MyPhase(app), "依頼を受ける")   # process_choice に乗せる
screen.end_conversation(app, end_entry, follow_up, end_text="<行動: …>")
screen.when_idle(app, then, cancel_if=..., proceed_on_timeout=True)
screen.busy_on(app) / screen.busy_off(app, restore=False)   # 「…」の待機表示
screen.paint(app) / screen.paint_party(app) / screen.refresh(app) / screen.say(app, text)
```

| 関数 | 何をするか |
|---|---|
| `apply_buttons` | `Clock.schedule_once(..., 0)` 経由で `app.buttons` を差し替え、`refresh` と `paint` まで行う |
| `paint` | `display_button_load(0)` と `hud.update_button_texts` の2手。`hud not found` は HUD の構成が変わった合図 |
| `paint_party` | 仲間欄を塗り直す。パーティを増減させたら最後に呼ぶ |
| `start_phase` | 自前フェーズを `PhaseSpec` に載せずに起こす |
| `end_conversation` | 画面のボタンの args を写し `end_text` だけ差し替えて閉じ、閉じ終わってから続きを実行 |
| `when_idle` | `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を見張る |
| `busy_on` / `busy_off` | LLM を待つ間の待機表示（ゲーム自身と同じ形。GAME.md §2.4）。`busy_off(restore=False)` は「この後すぐ別の画面を出す」経路用 |

読み取り系:

```python
ui.spec_cls_name(entry) / ui.spec_args(entry) / ui.pressed_entry(app, index)
ui.conversation_partner(buttons) / ui.find_spec_button(...)
ui.find_app() / ui.find_hud(app) / ui.cls_of(...) / ui.IDLE_SIGNALS / ui.SAFE_CLS
ui.current_area(app) / ui.world_areas(...) / ui.nodes_of(...) / ui.facilities_of(...)
ui.find_guild(area) / ui.find_facility(area, id) / ui.facility_name(app, facility)
ui.facility_type_of(...) / ui.GUILD_FACILITY_TYPE
```

ボタンを出さない MOD が「次のフレーム・メインスレッド」だけ要るときは `ui.scheduler`
（Kivy が無ければその場で実行するので、オフライン検証でも同じ経路を通る）:

```python
schedule = ui.scheduler(ctx, "text expand")
schedule(fn) / schedule(fn, delay=0.5)
```

**クエストの格納先**（GAME.md §2.9.1。遊びを変えたいだけなら `app.world.quests` にだけ書く。
雛形 `world_dict` へは書かない。下の `set_quest_value` / `quest_stores` は両方へ書くので、
雛形に触れたくない MOD は使わない）:

```python
ui.quest_stores(app) / ui.quest_ids(app) / ui.quest_of(app, id)
ui.quest_value(quest, name, default) / ui.set_quest_value(app, id, name, value, on_error=...)
ui.id_sort_key            # id を数として並べる鍵
```

`id_sort_key` を通すのは、ゲームの id が採番順の**文字列**だから。
素の `sorted()` は辞書順なので `"10" < "9"` になり、
「いちばん新しい id」を採ると1回の生成で複数増えた回だけ取り違える（`301_` が実際にそうなっていた）。

**所持金と「今は画面を出さない」状態**:

```python
ui.gold_of(app) / ui.add_gold(app, amount, on_error=...) / ui.money(value)
ui.BUSY_FLAGS            # 戦闘中・会話中など
```

`gold_of` は `bool` を弾く（Python では `True` が `int` なので、
素朴な `isinstance` だと `gold = True` を所持金1として通してしまう）。

**通貨の表記**（GAME.md §2.29。額ではなく**呼び名**だけを扱う）:

```python
ui.set_currency(long_name, short_name)   # 表記を決める。決まった (長い形, 短い形) を返す
ui.currency_names()                      # 今の (長い形, 短い形)
ui.rewrite_coins(text)                   # 文中の素の表記を今の表記へ
ui.parse_coin(text)                      # ラベルから額を読む（素の `G` も今の表記も読む）
ui.COIN_LONG / ui.COIN_SHORT             # 素のゲームの言い方（`ゴールド` / `G`）
```

決めるのは**1本だけ**（`130_`）で、他は使うだけ。
入っていなければ素のまま（`ゴールド` / `G`）なので、
`rewrite_coins` を通しても何も変わらない。

| 使う側 | 何のために |
|---|---|
| `309_` / `local/` の MOD | 自分で組んだ文言を画面に出す直前に `rewrite_coins` を通す |
| `314_` / `315_` | テンプレートを埋めた後に `rewrite_coins`、ゲームのラベルから額を読むのに `parse_coin` |

`set_currency` は**何度通しても伸びない表記しか受け取らない**
（`ゴールド` → `金ゴールド` のように新しい表記の中に素の表記が残っていると、
通すたびに伸びる）。
当てはまらない指定は素の言い方のまま据え置き、戻り値がそれを伝える。

**手配度**（GAME.md §2.20。読み方だけを共有する）:

```python
ui.area_history_of(character) / ui.area_record(character, area_id)
ui.lawfulness_of(entry) / ui.lawfulness_by_area(character)
ui.set_lawfulness(entry, value) / ui.LAWFULNESS_KEY
```

`lawfulness_by_area` は `{エリアid(str): 手配度}` を返し、読めなかった土地は入れない。
`gold_of` と同じ理由で `bool` を弾く（`True` を手配度1と読むと、
そこから罰金や敵の強さまで計算してしまう）。

**いくつから手配とみなすかは各 MOD の判断**で、ここには置かない
（`309_` は罰金の基準に、`316_` は追手の条件に、`220_` は下調べの要約に、
同じ読み方から別の数え方をする）。

`set_lawfulness` は**読めた記録にしか渡さない**（項目を新設しない）のが呼ぶ側の約束。
書き戻す MOD は2本ある（`309_` が罰金で平常値へ戻す、`316_` が追手を倒したぶんを戻す）。

**HUD に足す自前のボタン**:

```python
ui.CORNERS / ui.AS_TEXT / ui.upx(value) / ui.window_size()
ui.clamp_into_window(widget)          # 置いた後に必ず通す（はみ出すと押せない）
ui.make_icon_button(text=, size=, square=, font_name=, pos_hint=)
ui.icon_strokes(icon, flipped)        # 共有の絵柄（二重山形・山形・矢印・枠）
ui.paint_icon(button, strokes, attr=, key=, width=, alpha=, log_exc=)
ui.show_widget(widget, visible)       # 隠すときは押せなくもする
```

`paint_icon` は**変わったときだけ引き直す**（位置・大きさ・太さ・濃さと `key` を控えて突き合わせる）。
本文は1文字ずつ増え、パーティ欄は HP が動くたびに塗り直されるので、毎回引くと無駄が積み上がる。

**MOD 固有の絵柄はローダに足さない**（`113_` の「伸縮」、`122_` の本や吹き出しはその MOD だけの語彙）。
MOD のフォルダへ置き、`ui.icon_strokes()` へ落とす形にする。

**HUD へ自前のウィジェットを1枚足すとき**（GAME.md §2.3）:

```python
host = ui.overlay_host(hud)     # 置き場所。HUD 直下ではない
host.add_widget(widget)         # 既定は先頭挿入＝一番上に描かれる
setattr(widget, "_instantale_<mod>_<用途>", ...)   # ui.MOD_WIDGET_PREFIX に揃える
```

| 関数 | 何をするか |
|---|---|
| `overlay_host(hud)` | 足す相手を返す。**HUD の子は増やさない**（増やすと「画面の最初の子」を取る側から見える相手が変わり、アイテムの移動・装備が壊れる）。`children` の**古い側**から探すので、ゲームが一時的に出している窓や他の MOD のウィジェットを掴まない |
| `added_by_a_mod(widget)` | `_instantale_` で始まるインスタンス属性を持つか |

自分のウィジェットに付ける控えは `ui.MOD_WIDGET_PREFIX`（`_instantale_`）で始めること
（`overlay_host` がこの接頭辞だけを手がかりにしている）。
ボタン辞書の印（`ui.MARK_PREFIX` ＝ `mod_`）とは別で、あちらは選択肢、こちらはウィジェットの印。

**パーティの名簿**（`302_` が4回外して固めた手順。GAME.md §2.8）:

```python
ui.pick_store(app)          # (どこから, id の一覧)。'player' を含む入れ物を本物とみなす
ui.party_ids(app) / ui.party_member_ids(app)
ui.party_stores(app) / ui.store_ids(store) / ui.drop_from_store(store, id)
ui.element_id(value) / ui.describe_stores(app)
ui.character_of(app, id) / ui.character_name(app, id, fallback="その仲間")
```

`character_name` は引けないとき既定では id をそのまま返す（ログ向け。空にはしない）。
文言に混ぜるときだけ `fallback=` で差し替える。

### 5.2 `instantale_modloader.frames`

```python
frames.text_of(obj, name)  # 文字列を期待する読み方。文字列でなければ None
frames.short(value, limit) # 表示・ログ用の切り詰め。None は空文字
frames.caller()            # 呼び出し元の連鎖。段数では数えない（wrap の層が挟まる）
frames.owner_of(code)      # method_1 / execute の持ち主クラスを名指しする
frames.attr(obj, name)     # hasattr を使わない存在確認
frames.repr_value(value)   # dict はキーとキーの型を出す
frames.format_locals(...) / frames.describe_instance(...)
frames.MISSING             # 「属性が無い」を None と区別する番兵
```

`MISSING` は文字列（`"<missing>"`）。
存在確認は `is frames.MISSING` で書き、
読んだ値を他と照合するなら既定を明示して `None` を受け取る（`frames.attr(w, "source", None)`）。
既定のまま `==` で比べると、その属性を持たない相手が全部一致する
（`113_` がこれで、立ち絵を `source` で探したつもりが画面じゅうのウィジェットに一致し、
ボタンが画面の上端に貼り付いた）。

`MISSING` が文字列であることは型の検査もすり抜ける。
`isinstance(value, str)` は「属性が無い」を弾けない
（`118_` が本文をこれで受けて、`"<missing>"` を本文だと思ったまま照合し続けていた。
症状はログの1行だけで、例外は出ない）。

文字列を期待するなら `frames.text_of()` を使う。
番人は2つとも文字列である（属性が無ければ `"<missing>"`、
property の評価が失敗すれば `"<... while reading>"`）ので、
`attr()` で受けて `isinstance` で弾くやり方は片方しか塞げない。
`text_of()` は番人を作らずに読むので「無い」も「読めない」も一様に `None` になる。

同じ罠を3本が別々に踏んでいる。
`118_`（本文）、`115_`（`text` を持たない飾りのウィジェットが「行」に数えられ**一覧が丸ごと棄却されていた**）、
`116_`（本文ラベルが None のとき `"<missing>"` をフォント名として代入）。

### 5.3 `instantale_modloader.llm`

LLM へ出ていく文章を書き換えたい MOD が使う。
仕掛ける場所（＝ゲームの読み方）だけを持ち、書き換えの中身は持たない。

```python
from instantale_modloader.llm import wrap_outgoing

def rewrite(texts, site):
    """この1回の推論で出ていく本文の並び。変えないなら None を返す。"""
    return [t.replace("前", "後") for t in texts]

hooks = wrap_outgoing(ctx, rewrite, label="my mod")
hooks.armed()          # 今その名前がある対象（起動直後はクラウドの別名がまだ無い）
```

包む先は4種類（全部 `required=False`）:

| 経路 | 対象 | site |
|---|---|---|
| ローカル | `LlamaCppClient.chat` | `chat` |
| ローカル | `LlamaCppClient._apply_chat_template` | `template` |
| ローカル | `LlamaCppClient._post_with_model_loading_retry` | `payload` |
| クラウド | `llm_manager:send_request` / `_with_no_structure` | プロバイダ名 / `+_ns` |

引き受けているのは次の4つ。
**MOD ごとに書くと必ずどれかが抜ける**（`119_` は最初の1つしか知らず、クラウドで丸ごと素通しだった）:

- クラウドはモジュール名で名指ししない。
  どの経路でも import される `llm_manager` の別名を包む（alias_scan が持ち主を全部張り替える）
- その別名は初期化時に後から生える。
  ローダの保留はモジュール単位なので属性の後生えは拾わない ＝ 見張って当てる
- ローカル実行では `llm_manager` 境界に触らない。
  `send_request` は内部で別スレッドに降りてから `chat` を呼ぶため、印が届かず二重に当たる
- 入れ子で通る地点は内側を素通しする（印は `wrap_outgoing` の呼び出しごとに別なので、
  MOD どうしが互いを塞がない）

面倒を見ないものは2つ（どちらも書き換えの中身しだいなので MOD 側の責任）:

- 印はスレッドに立つので、`chat` が返った後に別のスレッドが送る経路には届かない。
  二度当たって困るなら自分で止める（`111_` は自分の出力のハッシュ、`119_` は本文の目印 ＝ 冪等）
- 適用順は約束しない。
  ローカルの3点は `mod.json` の `after`/`before` で重なるが、
  クラウドの別名は見張りが先に当てた方が内側になる（後生えを待つ時刻が MOD ごとに違う）。
  互いの書き換えが相手の目印を壊さない前提で書くこと

クラウド境界で見えるのは呼び出し側が渡した `message` だけで、
`send_request` の中で足される部分（Gemini のスキーマ文など）には当たらない（GAME.md §1.8）。

#### MOD から LLM に1問だけ聞く（`llm.ask`）

```python
from instantale_modloader import llm

text = llm.ask(ctx, "mod_my_question", [{"role": "user", "content": "..."}],
               timeout=30, label="my mod", write=write)

structure = llm.create_structure(ctx, "MyAnswer", {"attribute": (str, ...)})
data = llm.ask(ctx, "mod_my_question", message, timeout=30, structure=structure)
```

| 決まり | 理由 |
|---|---|
| **`timeout` はキーワードで必ず渡す**（既定値を置いていない） | ゲーム側の既定は無期限。1回返らないと呼んだ側が永久に止まる（`311_` は抽出を1本のワーカーで直列に回しているので以後の抽出が全部止まり、`300_` は情景描写のスレッドを巻き込む） |
| `message` は必ずリスト | 素の文字列は `send_request_on_id` で `TypeError`（GAME.md §2.12） |
| `manager_name` は MOD 専用の名前にする | `output_data/` に別々に残り、後から質を見られる |

`timeout` を受け付けない未実測のプロバイダでは `TypeError` で失敗して `None` を返す
（呼び側は LLM を使わない道へ降りる）。
渡さずに呼び直さないのは、**止まらないことのほうが大事**だから。

#### 返答を読む（`llm.parse_json` / `llm.strip_fence` / `llm.truthy`）

```python
data = llm.parse_json(raw)                  # 囲みと前置きを越えて辞書を1つ
body = llm.strip_fence(raw)                 # 囲みを剥がすだけ（素の文章の受け皿がある MOD 用）
changed = llm.truthy(data.get("changed", True))                  # 判らない語は True へ
violation = llm.truthy(data.get("content_violation"), unknown=False)  # 判らない語は False へ
```

「JSON だけを返せ」と頼んでも、モデルは囲む。前置きも書く。
`311_` / `317_` / `321_` / `403_` / `404_` の5本が各自で剥がしていて、
**剥がし方が3通りに枝分かれしていた**（行で切るもの、言語の札を決め打ちで並べたもの、
正規表現で一度に取るもの）。
札の並びに無い言語名（```` ```JSON5 ````）を書かれると、決め打ちの側だけが JSON を壊す。

`truthy` の `unknown` は**項目の意味で決まる**ので、既定に任せずに考えること。
`changed` を False へ倒すと抽出した内容を黙って捨て、
`content_violation` を True へ倒すと普通の台詞が消える。

`as_dict` との違いは、相手が**物**か**文章**か。
`as_dict` は返ってきた物（pydantic のモデル・辞書・素の JSON 文字列）を均すもので、
`parse_json` はその後ろに「文章から JSON を1つ拾う」を足したもの
（構造化経路と非構造化経路の**出口を1つにする**ために使う。
2つの経路それぞれに検証を書くと、片方だけ直したときに黙ってすり抜ける）。

### 5.4 `instantale_modloader.state`

世界ごとにデータを分けて持つ MOD が使う。
`world_key(app)` と `world_filename(key)` が**どのファイルか**を決め、
`WorldStore` がその**出し入れ**を持つ。

```python
from instantale_modloader import state

worlds = state.WorldStore(ctx, "npc_profiles", order=ordered_bucket, write=write)

key, bucket = worlds.of(app)      # いま居る世界の (鍵, 控え)
bucket[npc_id] = record           # 返る dict は控えそのもの
worlds.save(key)                  # 書く（`order=` が並びを固定する）
```

| 引数 | |
|---|---|
| `dirname` | `state/` 直下のフォルダ名。**MOD 専用の名前にする** |
| `suffix` | 拡張子（既定 `".json"`） |
| `own` | 自分の控えか。`False` は**他の MOD の控えを読むだけ**の意味で、フォルダを作らず `save()` を拒む |
| `normalize` | 読んだ直後に1度だけ通す。`(控え, 直したか)` を返し、直っていれば書き戻す（古い形の移行） |
| `order` | 書く直前に1度だけ通す。並びを固定する（`state/` の差分が読めるように） |
| `write` | MOD 自身のログ関数。書けなかったときに1行出す |

錠（`worlds.lock`）は `RLock` で公開してある。
「読んで、書き換えて、書く」を1つの錠で括りたい MOD は `data_lock = worlds.lock` として使う。

他の MOD の控えを読むときの決まりが2つあり、どちらも `own=False` が引き受ける:

- フォルダを作らない。相手を切っている人の `state/` に空のフォルダを置かない（§3.11）
- `load(key, fresh=True)` で読む。相手はワーカーで書き換えるので、
  更新時刻と大きさが動いたときだけ読み直す（動いていない間まで読み直さない）

> 寄せる前は9本（`300_` / `311_` / `312_` / `316_` / `317_` / `318_` / `321_` /
> `403_` / `404_`）が `path_for` → `read_json` → キャッシュ → `write_json` を写していた。
> 写した先で既にずれていて、フォルダを作るものと作らないもの、錠を持つものと持たないもの、
> 読めなかったときに `{}` へ倒すものと `None` を返すものに枝分かれし、
> **他の MOD の控えを読む側**（`403_` / `404_` が `311_` を読む）が
> 相手のフォルダを勝手に作っていた。

### 5.5 `instantale_modloader.jobs`

LLM を待つような重い処理を、ゲームのスレッドから外して直列にこなす。

```python
from instantale_modloader import jobs

worker = store["worker"] = (
    store["worker"]
    or jobs.Worker(ctx, compile_area, name="area_chronicle",
                   label="area chronicle", key=job_key,
                   max_pending=MAX_PENDING, on_drop=note_dropped)
).rebind(ctx, compile_area, write)

if worker.enqueue(job):
    write("編纂を予約: ...")
```

引き受けているのは5つ。`311_` / `317_` / `321_` / `403_` が同じものを持っていた:

1. **直列にする**（ローカルの推論は1つのモデルを取り合うので、並べても速くならない）
2. **溢れたら古い方から捨てる**（推論が返らない間に会話を続けても際限なく溜めない）
3. **同じ鍵の仕事を二度積まない**（`key=` を渡したとき）
4. **仕事が無ければ自分で畳む**（注入し直したときに前の世代のスレッドを残さない）
5. **例外を飲む**（1件の失敗で以後が全部止まると、遊んでいる側からは何も起きなくなる）

MOD 側に残るのは**何をログに出すか**だけ（`on_drop` / `on_done` / `enqueue` の戻り値）。

`Worker` と `WorldStore` はどちらも `apply()` の外に置くこと。
`apply()` は1プロセスで何度も呼ばれる（§3.5）ので、
中で作るとスレッドが増え、錠が別インスタンスになって
同じファイルを排他なしで read-modify-write できてしまう。
プロセス側（`sys` の属性）に置き、`apply()` のたびに `rebind(ctx, ...)` で
今の世代へ繋ぎ替える。

### 5.6 ゲーム内の日付（`ui.game_day`）

```python
day = ui.game_day(app)      # 読めなければ None
```

日付は**世界に1つ**（`world.days_elapsed`。GAME.md §2.16）で、
進めるのは `InstantaleApp.elapse_days`。
`app` でも `World` インスタンスでも受ける（`World.__init__` を包む場面では
`app.world` がまだ埋まっていない）。
実行時の世界がまだ無いとき（ロードの途中）は `world_dict["world_data"]` から拾う。
5本が各自でこれを読んでいて、**その受け皿を持っていたのは `312_` だけ**だった。

---

## 6. 落とし穴（ルール一覧）

すべて実際に踏んだもの。

### 6.1 MOD とローダの作り

| ルール | 理由 |
|---|---|
| **何度実行しても結果が変わらないように書く** | 当て直し（§3.4）と再注入で `apply()` は何度も走る。フックが複数発火しても壊れない形なら、フック選択が致命的でなくなる |
| 同じ規則を2箇所に実装しない | 探索・適用順は `discover()`、設定は `config.py`（§1.3） |
| 同じ発見を2箇所に書かない | 実機で確かめた事実は `ui.py` / `frames.py` と GAME.md へ。MOD には設計判断だけ（§5） |
| 順序の前提は文章ではなく `after`/`before` に書く | 文章は守られない。GUI で行を動かせば壊せる（§3.2.4） |
| GUI から触れる値は `"settings"` に宣言する | コードの定数だけだと GUI から見えず、MOD の更新で消える（§3.8） |
| `safe=True` を握り潰しの代わりに使わない | 例外はログに残るが見えなくなる。`safe hook failed` が出たら直す（§3.1.5） |
| **`safe=True` のフックで、本番の後に捨て玉の `orig` を呼ばない** | `_guard` が覚えているのは「最後に `orig` を呼んだ答え」。合成引数でゲーム側を探ると記録がその探りの答えに化け、そこで投げると `safe=True` が**素でもフックでもない値**をゲームへ返す（`125_` が段の総当たりで `orig` を約460回呼んで踏んだ）。探るなら `patch.unwrap()` で剥がした素の関数を呼ぶ |
| `on_ready` に `force=True` を残さない | 開発中の逃げ道。配ると当て直しのたびに副作用が起きる（§3.6） |
| 壊れた設定ファイル・順序ファイルで MOD を全滅させない | 既定に倒して動かし、報告する。「動かない」より「報告して動く」 |
| **直せなかったときに何が見えていたかを残す** | 黙って抜ける道を作らない。売買の素データを写す MOD の初版は実機でフックまで届いていたのに記録を1行も残さず、「効かなかった」以外に何も分からないまま実機1回を捨てた。次の版で記録を足したら、読み違え（包んだ関数が引数を取らないこと）が1行で出て決着した。**落ちたときにだけ書けば**、正常時のノイズにもならない |
| 乱数は MOD 専用の `random.Random` | グローバルを使うとゲーム自身の乱数列がずれる |
| **設計の土台にする前提には出典を書く** | 測った事実には `実測` か `GAME.md §` を添える約束（§5）。出典の無い断定は仮説と区別が付かず、**読む側が仮説だと決めつけるのも同じくらい危ない**。`307_` の「移動は別スレッドで走ることがある」は出典が書かれていなかったが**正しかった**（`206_` が実測済み）。**否定する側も測ってから言う**（VERIFICATION.md §3.13） |
| **累積する数で挙動を決めない**（ログの上限は別） | 単調に増える値がホットパスのフックを門番していると、使い切った後の挙動が全部同じ値に張り付く（`307_` は日数の予算を使い切った後 `elapse_days` に 0 を渡し続け、その世界の暦を止めていた）。窓（`try`/`finally` で開閉する印）か、その場のクランプにする。**ただし窓で足りるかは包んでいる相手が何かで決まる**。`314_` は `AreaMoveManager.execute` そのもの（ワーカースレッド側で5.7秒開く）を包むので窓が持つが、`process_choice` を呼ぶ側は 2ms で返るので窓では届かない（VERIFICATION.md §3.13） |

### 6.2 ゲームの UI と選択肢

| ルール | 理由 |
|---|---|
| 自前のクラス名を `PhaseSpec` に書かない | セーブに焼かれ、MOD 無しの起動で `getattr` が失敗する |
| 自前で組む spec は引数の値まで実測で確かめる | 押下時にゲーム側で実行される ＝ こちらの `try` の外。1つ間違えば落ちる |
| 値の語彙を推測するくらいなら、語彙を知らずに済む経路を探す | ゲーム自身の入口に渡せば引数の意味を知らなくてよい |
| 選択肢の差し替えは次のフレーム＋`hud.update_button_texts` | 押下と同じ流れで塗ると古い画面に戻る（GAME.md §2.3） |
| 会話は閉じてから画面を変える。`end_text` に理由を書く | 閉じないと立ち絵が付いてくる。`end_text` は要約とライフログに残る |
| 後始末の最中に割り込まない | `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を見る |
| 長い処理の間は待機表示を出す | 出さないと操作が効くように見える（GAME.md §2.4） |
| UI と pygame は Clock（メインスレッド）から触る | `execute` は別スレッドで走る |
| **寸法・座標を発明しない** | グリッドの実寸もレイアウトの仕様も読めない。ゲームが決めた値を最低値にして、足りないぶんだけ動かす |
| レイアウト前の絶対座標から設計を読まない | 作り直された直後のウィジェットは子がまだ配置されていない。`pos_hint` の分数と親のサイズから求める |
| 画面に出す文字列に環境依存文字を使わない | cp932 の外（`▶` `»`）と NEC/IBM 拡張（`①`）は出ない・化ける・`print` で `UnicodeEncodeError`。判定は `test_battle_damage_display.py` の `charset_verdict()` がそのまま使える |
| `"choice"` の候補に空文字・空白だけの値を入れない | GUI は空欄を「未指定」として扱うので、`allow_null` でない設定では選んだ瞬間に弾かれ、一覧は読み取り専用なので戻せない。「無し」を選ばせたいなら `"なし"` のような名前を値にする |
| 選択肢の値の末尾に空白を持たせない | JSON でも GUI でも見えず、消えたことに気付けない |
| **他人のボタンを消す判定に、自分の印が無いことだけを使わない** | 掃除は画面が何であれ走るので、他の MOD が今その場に出しているボタンも「自分の印が無い」に見える（`302_` が `309_` の確認画面からキャンセルを消していた）。判定は `marked_by_a_mod`（`mod_` で始まるキーが1つも無いこと） |
| 残骸の掃除に使う文言は、その MOD にしか無いものだけにする | `やめておく` のような汎用語は他の MOD もゲーム自身も出す。印が落ちている相手は文言でしか見分けられない |
| **表示中の文字列を手がかりに描画先のウィジェットを探さない** | その文字列を書き換える MOD が入った時点で探索が空振りする（`117_` が本文を載せ替えたら `112_` がラベルを見失った）。一度実測で属性名が分かったら名前で引く（VERIFICATION_LOG.md §2.32） |
| **ウィジェットの再描画（`texture_update()`）を自分から呼ばない** | Kivy はテキストを代入した時点で次のフレームに作り直しを1回予約する。そこへ MOD が自分でも呼ぶと二度手間になり、しかもその代金はフレーム時間に乗るのでフックの中で測っている限り見えない（実測で 1文字 3回 × 15ms を `112_` と `117_` が食っていた。VERIFICATION_LOG.md §2.34） |
| 入れ物の子を「先頭」で選ばない | Kivy の `children` は新しい順。画面が組まれた時点から居るものが欲しいなら最後尾から探す。HUD へ足すときは `ui.overlay_host`（§5.1.3）を使い、自分で書かない |

### 6.3 計測と観測

| ルール | 理由 |
|---|---|
| 非同期に渡される処理を呼び出しの前後で測らない | `process_choice` は即座に返る。状態を継続監視するか内側で測る |
| **ゲームの生きたフレームから変数を読もうとしない** | Nuitka のフレームはトレースバックに載るときしか中身を作らない。実行中に `frame.f_locals` を覗くと**空**が返る（実測: 会話終了の3フレームすべて「ローカル 0件」。`125_`）。クラッシュ記録で locals が読めているのはトレースバック経由だから。呼び出し元の情報が要るなら、**それが引数として届く関数を包む** |
| 呼び出し元の `f_locals` を dict と決めつけない | Python 3.13 以降は書き戻し用のプロキシ（PEP 667）で、`isinstance(..., dict)` で弾くと**関数のフレームが1つも読めない**。ゲームは 3.10 だがテストは手元の Python で走るのでそこで初めて出る。`dict(frame.f_locals)` で写す |
| モジュール直下のフレームからオブジェクトを拾わない | そこの `f_locals` はモジュールのグローバル。たまたま置かれている同型のオブジェクトを掴む |
| 呼び出し元を段数で数えない | `@ctx.wrap` の層が挟まる。ファイル名で飛ばし、`frames.owner_of` で持ち主クラスを名指しする |
| **包まれたメソッドを `MethodWatch` で見張るなら、答えが遅くなることを承知する** | 生の `__code__` を採るとそれはローダのラッパのもので、`patch.py` の全パッチが共有している ＝ 包まれた関数が1つでもスタックに載れば「その中」と答える（`307_` が `QuestEndManager.execute` を包んだとき `303_` / `304_` が道連れになった）。**ローダ側で塞いである**が、予備の経路は `__main__` の全クラスを舐めるので重い。包む対象と見張る対象が自分の中で重なるなら、自分のラッパで印を立てる方が速い |
| 計測に `hasattr` を使わない | `__getattr__` トリップワイヤを自己発火させる。`frames.attr` を使う |
| `app` を受け取る関数を包むときは、渡されたものが app か確かめる | 別のオブジェクトが渡ってくる経路がある |
| 属性は名前で推測せず `vars()` を全部出す | 名前から探すと空振りする |
| 状態は自前の帳簿ではなくランタイムに聞く | 音は `get_num_channels()`、画面は `hud.buttons[i].text`、名簿は中身を見る |
| **観測できた範囲でしか直さない** | `in_battle` は 1→0 を観測できたので下ろす、`in_boss_battle` は観測できないので記録だけ |

### 6.4 ゲームのデータと状態

| ルール | 理由 |
|---|---|
| セーブの形＝実行時の形と決めつけない | 名簿・現在地・戻り値、いずれも実行時は別の形を取りうる（GAME.md §2.7） |
| フラグ名を信用しない | `in_shopping` は買い物中でなくても True のまま |
| 名前や ID がそのままファイルパスになる箇所を疑う | パスに使えない文字が入ると無言で失敗する |
| 同じ値を複数箇所で加工しない。入口ひとつで正す | 5箇所で個別に消毒すると、書き込みと削除でずれて別の不整合を生む |
| 独自キーをゲームのデータ構造に足さない | セーブに焼かれ、再読み込み後に残る保証も無い |

---

## 7. 実装例カタログ

新しい MOD を書くとき、近い手口を使っている既存 MOD を読むのが速い。

### 7.1 直し方（パッチの当て方）

| 手口 | 見る MOD |
|---|---|
| ゲーム自身のヘルパを当てるだけの修正 | `101_` / `107_`（通常経路がやっていることを抜けている経路に適用）/ `108_` |
| ゲームが決めた値を最低値にして、足りない分だけ広げる | `109_`（アイテム詳細ボックスの高さ・幅） |
| 入口ひとつを直して下流の5箇所を一致させる | `110_`（`Character.__init__` で名前を正す） |
| 組み立てられる前に素データを直す（オブジェクトだけ直しても保存で戻るとき） | `120_`（`generate_character` の `character_value` を `orig` の前に直し、id を鍵に持つ辞書を全部書き換える） |
| 「直してよい相手」を素データの名簿で決める | `120_`（`npcs` に id があるものだけ ＝ 敵と魔物とプレイヤーが自然に落ちる） |
| 生成物の質が要るところで、生成をやめて用意した表から選ぶ | `120_`（名前は音替えでも LLM でも当たり外れが出た。名簿から引く形にすると質が入力で決まる。結果を素データにも書くので名前は落ち着く） |
| LLM の出力の揺れを、正規化した鍵で畳んでから裁く | `120_`（表記ゆれ・修飾語・姓名を落とした「読みの骨」で比べる。モデルを問わない） |
| **本体が直ったら自動で降りる修正にする** | `123_`（「新規開始だから」ではなく「レベルだけが他の値と食い違っているから」直す。食い違いそのものを条件にすると、本体が直った版では1行も動かない） |
| ゲームの文言を持たずに、ゲームの分類を書き換える | `125_`（段の並びを注入後の最初の呼び出しで総当たりして覚え、以後は位置で扱う。文字列を1つも持たないので言語設定にも版差にも巻き込まれない） |
| 引数に居ない相手を、場面のマネージャを包んで拾う | `125_`。直そうとしている関数が相手を受け取っていないとき、**フレームを遡る手はこのゲームでは使えない**（生きたフレームの `f_locals` は空。§6.3）。相手が引数として届く場所（その場面の入口と出口のマネージャ）を包み、**通っている間だけ**相手をスレッドごとに控える。id は世界の名簿で引けたものだけ採る（会話の通し番号のような「相手ではない id」を鍵にすると、値が場面ごとに変わって「気まぐれ」に化ける）。**入口と出口の両方を包むこと**。片方だけだと、付けた差がもう片方の再計算で上書きされる |
| 失敗を握り潰す前に、必ず引数と型を記録する | `100_`（値と型を残し、自前のプロトタイプで直接呼び直す。それも駄目なら諦めて `None`。再送出はしない） |
| どのフックが効くか分からないので全部に仕掛ける | `104_`（BGM）/ `105_`（`chat` と `payload`） |

### 7.2 プロンプトと LLM

| 手口 | 見る MOD |
|---|---|
| 関数の引数を書き換える（出力の形は変えない） | `103_` / `105_` / `301_`（`area_description` に会話を添える） |
| 判定は全メッセージを繋いで、書き換えは各メッセージに | `111_`（目印が system と user に散っているプロンプトでは、これでないと当たらない） |
| 外部（プロキシ）でやっていた加工をプロセス内へ移す | `102_` / `103_` / `105_` / `111_`（ルールファイルの書式まで揃える。本文が復号済みなので読み替えが要る） |
| 手で編むデータファイルを持つ | `111_`（更新で消えない名前の分け方は §3.1.1.1。探索も外部参照もしない） |
| 手で書いた規則をリクエストのたびに読み直す | `111_`（更新時刻と大きさを見る。読めない間は前回の規則で続ける ＝ 保存の書き込み途中で壊れない） |
| **ゲームの式を読まずに、入口の値を動かして結果を動かす** | `313_`（確率は `credibility*10+20` が上限で単調なので、判定に入る前の `credibility` を上げれば確率が下がることはない） |
| 代入が通ったかを書いた後に読み直して確かめる | `313_`（入らなければ整数に丸めて入れ直し、落ちたことを1度だけ記録する。「たぶん通る」で進めない） |
| 自前の manager 名で LLM に1問だけ聞く | `313_`（記録が `output_data/` に分かれるので後から質を見られる。§5.3） |
| 同じ加工を複数の地点に仕掛けても1回しか効かせない | `111_`（スレッドの印で内側を素通しし、自分が作った文章を覚えて別スレッド経由の二度目も止める） |
| **重い編纂を素材の印で止め、フックでは結果だけを注入する** | `317_`（素材のハッシュを控えと突き合わせ、変わった土地だけ別スレッドで1回編纂する。会話フックの中では文字列を足すだけなので遅延ゼロ・返らない事故も無い） |
| 印に「時間で動く値」を入れない | `317_`（滞在日数を印に入れると、その土地に居るだけで毎日編纂し直す。印に入れるのは行いだけ） |
| 2段の編纂で、2段目には1段目の**出力**を渡す | `317_`（各地の評判文 → 世界に1つの二つ名。素の出来事まで渡すと同じ事実が2度 LLM を通り、頼み文も土地の数だけ伸びる） |
| **回数は契機で、結果は指示で絞る** | `317_`（質的な変化のときだけ編み直し、頼み文には「覆す材料が無ければ同じ名を返せ」を載せる。契機の定義漏れとモデルの揺れが互いを補う） |
| 「読めない」と「読めたが中身が無い」を分ける | `317_`（前者は印を控えず次の照合で引き直し、後者は印を控えて消費と数える。分けないと、答えを出さないモデルに同じ問いを繰り返す） |
| LLM が**指示から逃げる**先を塞ぐ | `317_`（「記録に無いことを足さない」が二つ名にまで効いて 73% が空になった。頼み文に例外を1行書き、コード側にも番人を置く。VERIFICATION_LOG.md §2.63） |
| 生成物の長さを毎回揺らす | `317_`（「N字以内」だけを頼むとモデルは同じ長さ帯に寄る。狙いを乱数で引いて「N字くらい」と頼み、上限は別に固く言う） |

### 7.3 UI・選択肢・会話

| 手口 | 見る MOD |
|---|---|
| 自前の選択肢ボタンを足して押下を横取りする | `301_` / `302_`（`on_button_press` + 独自キー） |
| ゲーム本来のフェーズを自分から起こす | `300_`（`ConversationStartManager`）/ `301_`（`DisplayQuestChoice`） |
| 引数の語彙を知らないまま、ゲームのボタンの `args` を写して同じ処理を起こす | `307_`（`AreaMoveManager` の `mode`） |
| 会話を正しく閉じてから次へ進む | `301_` / `302_`（`ui.Screen.end_conversation`） |
| 待機表示で画面の繋ぎ目を隠す | `301_`（`busy_off(restore=False)`） |
| 手が空くのを待ってから実行する | `300_` / `303_`（`when_idle`） |
| 選択肢の枠を使わず、HUD へ自前のウィジェットを1枚足す | `113_`（フォントは本文のラベルから写す。Kivy の既定に日本語が無いため） |
| 注入した時点でもう組み上がっている画面へ足す | `126_`（`__init__` を包むだけでは目の前のタイトル画面に何も出ない。`on_ready` で `Window` から辿る側と2つ持ち、同じ関数を呼ぶ。足す前に自分の印が付いたものを外すのでどちらから来ても1枚） |
| 他の MOD が置いたウィジェットの隣に並ぶ | `122_`（相手は HUD の控えから引き、大きさを写して `pos`/`size` に束ねる。塗り直しを待つと1手ぶん遅れて追いかけることになる） |
| ゲームの画面を一切動かさずに読み物を出す | `122_`（`ModalView` + `ScrollView`。版差のあるプロパティは持っているほうにだけ効かせる） |
| **長い文章を Label 1枚に入れない** | `122_`（Kivy の Label は中身を1枚のテクスチャに焼くので、GPU の上限（多くの環境で 16384px）を超えると**例外も出さずに何も描かれない**。VERIFICATION.md §3.21） |
| 流れて消える情報を、追記専用の控えとして残す | `122_`（`state/` に JSON Lines で1行1件。途中で落ちても壊れるのは最後の1行だけ） |
| ゲームが決めた寸法を、元に戻せる形で変える | `109_` / `113_`（**設計値はウィジェット自身に控える**。MOD 側の変数に持つと、注入し直したときに変えた後の値を設計値として控える） |
| はみ出した一覧を、位置も中身の大きさも変えずに収める | `115_`（`GridLayout` の `cols` を増やす。ウィジェットを移し替えないので開閉の後始末と衝突しない） |
| 触ってよい相手を「その直し方が成り立つ能力」で選ぶ | `115_`（列にできるのは `cols` と `minimum_height` を持つ入れ物だけ。型名で弾くのではなく能力で選ぶと関係ない相手が自然に落ちる） |
| レイアウトが走る前の寸法を控えない | `115_`（逆算した値と突き合わせる判定は必ず真になる。1回目の版はこの穴で `(0, 0)` を設計値にした） |

### 7.4 状態と後始末

| 手口 | 見る MOD |
|---|---|
| **MOD どうしをファイルで繋ぐ（import しない）** | `301_`←`311_` / `121_`←`317_`（読む側はファイルが無ければ何も足さない。相手を切っていても成り立つ） |
| 読む側は `os.path.join(ctx.state_dir, …)` で組む | `121_`（`ctx.state_path()` は親を作るので、相手を使っていない人の `state\` に空のフォルダが残る。§3.11） |
| 押した合図をファイル1つで渡す | `121_`→`317_`（UI 側が置き、持ち主が次の照合で消す。在ることが頼みで中身は読まない ＝ 競合も版ずれも起きない） |
| 既にセーブに焼かれた残骸を注入時・ロード時に掃除する | `107_`（`in_battle`）/ `110_`（不正な名前） |
| ランタイムに現在の状態を聞いて後始末する | `106_`（pygame のチャンネル） |
| ゲームの処理を止めず「結果の置き先」だけ変える | `303_`（3層で置き先を差し替える） |
| ゲームの処理そのものを起こさせない | `304_`（`remove_party_member` を通さず、置き直しと文言も控えで見分けて抑える） |
| 在り処が不明なデータを中身で見分ける | `302_`（`ui.party_stores` / `pick_store` / `dump_census`） |
| ゲームが計算した値を横取りして、別の相手にも同じことをする | `306_`（`gain_exp` を包み、プレイヤーに入った点数を同行者へ写す。式は読まない） |
| 複数の場面をまたぐ状態を控えで持つ（再注入・再起動をまたぐ） | `307_`（移動の予約。段階を進め、前提が崩れたら捨てる） |
| 「いまその処理の中か」を自分のラッパの印で持つ | `306_`（`MethodWatch` だと重い予備の経路に落ちる。§6.3） |
| 書き直しで落ちる情報を、控えから差し戻す | `311_`（記録済みの `facts` を抽出プロンプトへ戻すと、落ちた事実が戻り、同じ事実を毎ターン報告し直すのも止まる） |
| ゲームが出さない数字を、状態の前後の差から出す | `308_`（1手の前後で全員の HP を比べる。ダメージの式も語彙も読まない） |
| 差分の報告点を何箇所にも置いて二重に出さない | `308_`（台帳方式。「比べる → 出す → 台帳を今の値へ進める」を1つの操作にすると、報告点をいくつ足しても重ならない） |
| **上流の数1つだけ動かして、下流はゲームに任せる** | `318_`（依頼の難易度を上げると、敵・報酬・才能・店の在庫・素材の値段がゲーム自身の計算で付いてくる。在庫やクラフトを別々に細工すると、その瞬間からゲームの更新に付いていけなくなる。`312_` の「消して、ゲーム自身に作らせる」と同じ考え） |
| **差分を足さず、素の値を控えて「素 + いまの量」を毎回書く** | `318_`（何度走っても同じ値に落ち着き、設定を変えても正しい高さへ寄る。差分を足す形は、取りこぼしと二重掛けがどちらも黙って積み上がる。控えがあるので外すときに元へ戻せる） |
| **ゲームが作り直す値は、細工を見ずに素で生まれてくる** | `318_`（土地の依頼を +20 まで上げても、新しい依頼は素の帯で生まれた。「ゲームが自分の帯を見て作る」は実機で否定されたので、生成のたびに上げ直す。VERIFICATION_LOG.md §2.66） |
| **書き換えた値が画面に出ても、セーブに残るとは限らない** | `318_`（`app.world_dict['quests']` は世界の雛形で、遊んでいる一覧は `app.world.quests`。前者へ書くと世界のファイルに焼かれ、同じ世界の別のキャラクタにまで乗る。GAME.md §2.9.1） |

### 7.5 計測・調査

| 手口 | 見る MOD |
|---|---|
| 読み取り専用で経路を特定する | `205_` / `206_` / `207_`（計測は修正より後＝外側に置く） |
| `__getattr__` トリップワイヤ | `201_` |
| 20Hz で画面状態の変化だけ拾う | `206_`（waitstate watcher） |
| 残っている記録だけで先に詰める | `215_`（`output_data/` の LLM 記録とセーブのバックアップを突き合わせ、実機に行く前に候補を潰す） |
| 計測 mod が自分の測定でログを埋めない | `214_`（総当たりの呼び出しは `state["probing"]` で自分の記録から外す） |

---

## 8. 制限

- 注入はゲーム起動後なので、**import 時点で走るコードにはパッチできない**。
  必要になったら `python310.dll` プロキシ DLL で `Py_InitializeEx` をフックする方式に切り替える
  （要 MSVC Build Tools・要管理者権限・Epic の repair で戻る）
- GIL を長時間占有する推論中に注入すると、スタブの完走が遅れる
  （30秒でタイムアウト表示になるが、スタブ自体はその後完走する）
- 自前の選択肢ボタンはセーブに残骸として焼かれうる。
  無害な既存クラスを spec に持たせてあるので壊れないが、MOD 無しで押すと何も起きない
- 選択肢のページ送りは `次` の枠（地図の値 `'next'`）まで実測済み。`ui.pressed_entry` は整数でない枠を None にして `orig` へ素通しさせる（GAME.md §2.2）。2ページ目以降の戻る側の枠は未実測
- ネイティブクラッシュ（`%LOCALAPPDATA%\CrashDumps\instantale.exe.*.dmp`）は Python 例外ではない。
  `crash_log.txt` と `001_` のどちらにも残らず、解析には cdb/WinDbg が要る
