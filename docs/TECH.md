# TECH: MOD 開発リファレンス

Instantale（Epic版 / Nuitka standalone / CPython 3.10）に外部から Python を注入し、
実行中のゲームを monkeypatch する仕組み。これから MOD を書く人のための資料。

## 0. この文書の位置

5つの文書はそれぞれ読む理由が違う。

| 文書 | 何が書いてあるか |
|---|---|
| TECH.md（本書） | このローダで MOD をどう書くか。事実とルール。他のゲームにも通じる話 |
| [GAME.md](GAME.md) | Instantale が何をしているか。パッチ対象の見つけ方、選択肢ボタン、会話、パーティ、クエスト、BGM、LLM 経路。このゲーム限定の事実 |
| [README.md](README.md) | 遊ぶだけの人向け。ローダと GUI の使い方 |
| [MODS.md](MODS.md) | 同梱している MOD 一本ずつの説明・設定・困ったとき |
| [VERIFICATION.md](VERIFICATION.md) | 各 MOD の検証状況・未確認項目・実測ログ |

GAME.md と分けているのは、ゲームが更新されて食い違うのはあちら側だけだから。疑う場所が
1つになる。同じ理由で置き場所を決めてある:

| 内容 | 置き場所 |
|---|---|
| 事実とルール（ローダの作法） | 本書 |
| 実機で確かめた「ゲームがどう動いているか」 | `ui.py` / `frames.py` と GAME.md |
| 個々の MOD の設計判断 | その MOD の入口ファイルの docstring |

### 目次

| 項目名 | 記載先 |
|---|---|
| なぜ注入方式なのか / どのファイルが何をするか | §1 |
| 検査・オフライン検証・注入のコマンド | §2 |
| 初めて MOD を書く（対象名の入手 → 雛形 → 検査 → 注入 → 確認） | §3.0 |
| MOD の最小の形（`mod.json` と `ctx`） | §3.1 |
| 適用順と依存の宣言 / 複数 MOD の重なり | §3.2 / §3.3 |
| `apply()` が何度も呼ばれる理由と書き方 | §3.4 / §3.5 / §3.6 |
| 誰がどこへ当てたか / 利用者設定 / API 番号 / 剥がし方 | §3.7 / §3.8 / §3.9 / §3.10 |
| ログと永続データの置き分け（`out/` と `state/`） | §3.11 |
| Nuitka で効くもの・効かないもの | §4 |
| 画面・選択肢・会話を扱う既製部品 | §5 |
| 踏んだ罠の一覧（守るべきルール） | §6 |
| 近い手口の既存 MOD を探す | §7 |
| このローダでできないこと | §8 |

---

## 1. 仕組みと構成

### 1.1 なぜ注入なのか

ゲームは Nuitka standalone ビルドで、純 Python モジュールは全て `instantale.exe`（715MB）
内にネイティブコード化されている。`.pyc` は無く、逆コンパイルもファイル差し替えもできない。

一方 CPython ランタイムは `python310.dll` として動的リンクされたままで、C-API 1607
関数がエクスポートされている。そこで既に走っているインタプリタにコードを流し込む:

```c
PyGILState_STATE s = PyGILState_Ensure();
PyRun_SimpleString(bootstrap);
PyGILState_Release(s);
```

これを 74 バイトの x64 スタブとして `VirtualAllocEx` + `CreateRemoteThread` で実行する。
C コンパイラも管理者権限も要らず、ゲームフォルダは読むだけ（`sys.path` は本ディレクトリ
配下の `runtime/` を向く）。注入されたコードは `instantale_modloader.boot()` を呼び、
`runtime/mods/` の各フォルダを `load_order.json` の順に適用する。

### 1.2 ファイル構成

```
InstantaleModLoader.bat   GUI を開く（配布物で唯一の入口）
tools/gui.py              MOD 一覧・適用順・有効/無効・設定・追加・起動と注入・結果表示
tools/watch.bat, watcher.py  ゲームの起動を監視して自動注入（GUI 無し）
tools/injector.py         PE解析 → x64スタブ → CreateRemoteThread（--unload で剥がす）
tools/logrotate.py        out/*.log の世代管理（注入 = 1世代の境目）
tools/check_mods.py       静的検査（デコレータ・宣言と実体のずれ）
runtime/instantale_modloader/
    __init__.py   boot() / discover() / ログ / 世代発行 / 遅延設置の監視 / on_ready
                  / API 契約 / status.json の書き出し / unload()
    patch.py      @patch / @wrap / alias再束縛 / 世代管理 / 未import保留 / safe / revert
    patch_registry.py  どの MOD がどこへ当てたかの台帳・重なり・未解決の報告
    config.py     MOD ごとの設定（mod.json の宣言 → settings/mod_settings.json → 定数）
                  / ローダ自身の切り替え（settings/loader.json。デバッグモード）
    frames.py     フレームローカル採取・値の要約・呼び出し元の特定
    ui.py         選択肢 / 画面の塗り替え / 会話の閉じ方 / idle待ち / 施設の引き当て
    recon.py      実行時リコン（モジュール構造ダンプ）
runtime/mods/     MOD 本体（1バグ・1機能 = 1フォルダ。入口は mod.json が名指し）
runtime/mods/load_order.json  適用順（"order"）と無効一覧（"disabled"）
runtime/mods/load_order.local.json  手元だけの適用順。在れば上に優先（git 管理外）
settings/         利用者が変えたものだけ（無くてよい）
                  mod_settings.json … MOD の設定 / gui.json … ゲームの場所・窓の位置
                  loader.json … デバッグモード（GUI とローダの両方が読む。§3.2.4）
out/              ログ・リコン成果物・status.json（最後の boot の結果）。消してよい
state/            MOD が持つ永続データ（§3.11）。消すと遊びが巻き戻る
tools/            上記に加え、オフライン検証・セーブ操作（ゲーム不要）
docs/             README.md / MODS.md / TECH.md / GAME.md / VERIFICATION.md
```

### 1.3 探索と適用順は `discover()` が1箇所で決める

ローダ・GUI・静的検査の3者が同じ関数を呼ぶ。以前はこの規則（`_` で始まるフォルダを除く /
`mod.json` を持つものだけ / 未記載は末尾 / `disabled` の意味）が3箇所に書き写されていて、
1箇所だけ直すと GUI の一覧と実際の適用順がずれた。

```python
found = instantale_modloader.discover()      # ゲームの中でも外でも同じ結果
found["order"]      # 有効な MOD。適用順（依存の制約も解決済み）
found["listed"]     # 一覧に出す順。無効なものも宣言された位置に含む
found["manifests"]  # 名乗り・api・settings・依存（MOD のコードは import しない）
found["debug"]      # "debug": true の MOD。デバッグモードが切なら order に居ない
found["debug_mode"] # デバッグモードが入っているか（settings/loader.json。§3.2.4）
found["superseded"] # {MOD 名: 取り込まれた版}。伏せ方は debug と同じ
found["problems"]   # 宣言と実体のずれ。人が読む行
found["notes"]      # 直すべきずれではない知らせ（手元用の順序ファイルを使っている等）
```

`problems` と `notes` を分けているのは、未公開の MOD を手元で動かしている間ずっと
赤が出る状態を作らないため。赤が常態になると本当のずれが埋もれる。
`check_mods.py --strict` は `notes` も問題に格上げする。

#### 手元だけの適用順（`load_order.local.json`）

まだ公開しない MOD を手元で動かすためのもの。`runtime/mods/load_order.local.json`
が在れば、`load_order.json` の代わりに丸ごとこれが使われる
（`instantale_modloader.order_path` が「効いている順序ファイル」を1箇所で決める）。

| なぜ要るか | |
|---|---|
| `load_order.json` は配布する構成そのもの | 開発中の MOD を書くと、配った先で「実体の無い記述」になる |
| GUI は保存のたびに順序ファイルを書き戻す | 一覧に出ている MOD が全部書かれるので、消しても次の保存で戻ってくる |
| コミットに紛れ込む | 未公開の MOD の名前が履歴に残る |

仕掛けは3点。どれか1つでも欠けると漏れる。

| 場所 | 何をしているか |
|---|---|
| `.gitignore` | `load_order.local.json` を除外（MOD のフォルダ自体は `.git/info/exclude` で各自が除外する） |
| `tools/gui.py` | 書き戻し先を `ml.order_path()` に聞く。手元用が在る間は配布用を書き換えない |
| `make_dist.bat` | `load_order.json` に載っていない MOD フォルダはstaging から落とす（`[mods] skipping ...` が出る）。ここが最後の砦で、落とさないと未完成の MOD がリリースに入る |

2つのファイルを混ぜないのは、差分から順序を組み立てる規則を増やさないため ―
効いている順序は常に1ファイルを読めば分かる。何で動いているかは `notes` と
`modloader.log` に必ず出る。

### 1.4 注入のタイミング

`tools/watcher.py` は新しい pid に対し、

1. `Py_IsInitialized` をリモートスレッドで直接呼んでインタプリタ初期化を確認し、
2. 可視ウィンドウの出現を待つ。

後者は Kivy が立ち上がり `__main__` の実行が終わった合図。これより早く注入してもパッチ対象が
まだ存在しない。

### 1.5 ログの世代管理

ログは全て「開く→追記→閉じる」で書かれるので、何もしなければプレイをまたいで積み上がる
（実測で `events.log` / `quest_flow.log` が数MB）。`tools/logrotate.py` が注入の直前に
`out/` 直下の `*.log` を `名前.log.1` へ送り、本体を空から始める（`KEEP_GENERATIONS` 世代ぶん
保持、既定 1）。切り替えは `--no-log-rotate` → 環境変数 `INSTANTALE_LOG_ROTATE` →
`ROTATE_LOGS` の順に優先。

入れ替えをゲームプロセスの中（`boot()`）でやらない理由が2つある。1つは `boot()` が
自分で `modloader.log` に書いている最中に走ること。もう1つは遅延設置の当て直し（§3.4）
でも `boot()` が呼ばれるため、1回のプレイの記録が途中で分断されること。注入は世代の
境目そのものなので、注入する側で1回だけ行えば両方とも起きない。対象は `out/` 直下の
`*.log` だけで、`out/test/` `out/recon/` と `status.json` には触らない。

MOD が持つ永続データはそもそも `out/` に来ない（`state/`。§3.11）。以前は
`out/quest_clients.json` のような状態ファイルが同じ場所に居て、「`*.log` だけ」
という但し書きだけが遊びの続きを守っていた。

---

## 2. 開発の流れ

### 2.1 手順

```powershell
# 1. 静的検査（構文だけでなくデコレータと引数の整合も見る）
python -m compileall -q runtime tools
python tools/check_mods.py

# 2. 該当するオフライン検証（ゲーム不要）
python tools/test_patch_registry.py    # ローダ本体（台帳 / on_ready / 名乗り）
python tools/test_arrival_event.py     # 300_
python tools/test_quest_offer.py       # 301_
python tools/test_party_leave.py       # 302_
python tools/test_quest_end_guild.py   # 303_
python tools/test_quest_end_keep.py    # 304_
python tools/test_item_detail_autosize.py     # 109_
python tools/test_character_name_sanitize.py  # 110_
python tools/test_llm_prompt_replace.py       # 111_
python tools/test_ui_text_spacing.py          # 112_
python tools/test_ui_text_expand.py           # 113_
python tools/test_ui_input_focus.py           # 114_
python tools/test_ui_item_list_fit.py         # 115_
python tools/test_office_pardon.py     # 309_
python tools/test_npc_profile_memory.py       # 311_（`301_` との取り決めもここで見る）

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

「MOD を編集 → `python tools/injector.py`」で回す。`boot()` が `instantale_modloader` を
`sys.modules` から落として再 import するので、そのまま反映される（層は積み上がらない。§3.5）。

> 注入はプロセスと一緒に消える。ゲームを起動するたびに注入し直すこと。

`on_ready` に預けた1回きりの初期化（§3.6）は注入し直しても走らない。その初期化自体を
直しているときは `ctx.on_ready(fn, force=True)` を一時的に使うか、`reset_once("300_")` で
印を落とす。どちらも開発中の逃げ道で、配布する MOD に `force=True` を書いてはいけない。

### 2.3 静的検査（`tools/check_mods.py`）は必ず通す

`compileall` は構文しか見ないが、実際にゲームを落とすのは構文として正しいコードのほう。
「`@ctx.wrap` の対象名と、飾っている関数の引数の並びが食い違っている」類は静的に捕まえられる。
`@ctx.wrap` が飾る関数の第1引数は `orig`、メソッド対象なら第2引数は `self`。

同じ考えで、宣言と実体のずれもここで捕まる。`"entry"` が指すファイルの不在、扱えない
`"api"`、`load_order.json` との食い違い、`"after"` / `"before"` の循環、そして
`"settings"` の既定値がコード側の定数とずれていること（§3.8）。

| 出力 | 意味 |
|---|---|
| `MISMATCH` | 直すべきもの。終了コード 1 |
| `note` | 表示だけの項目の欠落（`name` などは仕様では任意）。終了コード 0 |
| `--strict` | `note` も失敗として数える。同梱 MOD はこちらを通す |

### 2.4 環境の決まり

| 決まり | 理由 |
|---|---|
| ゲーム側は Python 3.10。3.11 以降の構文を使わない | 手元の python は 3.13 しか無いので `compileall` だけでは 3.10 互換を保証できない。`check_mods.py` が `ast` の `feature_version=(3,10)` で構文は弾き、CI が本物の 3.10 で `runtime/` をコンパイルして残りを見る（§2.5） |
| `.bat` は ASCII のみ | `.bat` はその時のコンソールのコードページで読まれるため、日本語を入れると環境によって解析が壊れる |
| ツールから MOD を読むときは番号を書かない | `tools/` の各スクリプトは `find_mod("_balance_area_bgm.py")` のように番号を除いた名前で引く。分類を見直して番号を振り直しても壊れないようにするため |

### 2.5 CI（`.github/workflows/ci.yml`）

push と pull request で、§2.1 と同じコマンドを同じ順に走らせる。CI 専用の細工は無い
ので、手元で通ったものは CI でも通る。Windows で動かすのは、このプロジェクトが
Windows 専用（注入が Win32 API を直接叩く）で、Linux では実際の利用環境と関係のない
差でしか落ちないため。

| ジョブ | Python | 見るもの |
|---|---|---|
| `game-python` | 3.10 | `compileall runtime`。本物の 3.10 で、ゲームの中に入るコードが通るか |
| `checks` | 3.13 | `compileall` / `check_mods.py` / `tools/test_*.py` 全件 |
| `packaging` | 3.13 | `make_dist.bat` が通ること、zip に `LICENSE` / `NOTICE` が入っていること |

`game-python` が `runtime/` だけを見るのは、`tools/` が利用者の Python（3.13）で動く
もので、ゲームの中には入らないから。

除外一覧は置いていない。`tools/test_*.py` は1本でも落ちたら CI が失敗する。
「既知の失敗」の枠を作ると、そこに積まれたものが直ったかどうか誰も見なくなるため。

`packaging` が zip の中身まで見るのは、`LICENSE` の入っていない配布物は誰も合法的に
再配布できないから。MIT は著作権表示が複製に付いて回ることを要求する。

---

## 3. MOD の書き方

### 3.0 最初の MOD を作る

初めて書くときに最初に詰まるのは、`@ctx.wrap("...")` の `"..."` に何を書くのか。
ゲームはコンパイル済みでソースが読めないので、対象名は推測ではなく実行中のゲームから
取り出す。ここはそのための一直線の手順で、詳しい話はそれぞれの節に譲る。

#### 手順 0. 対象名の一覧を手に入れる

MOD を書き始める前にこれを済ませる。同梱の `000_recon` が、注入されたときに
ゲームの中身をダンプして `out/recon/` に置く。この MOD は `load_order.json` で
既定で有効なので、必要なのは「一度ゲームを動かして注入する」ことだけ:

```powershell
InstantaleModLoader.bat        # GUI からゲームを起動して注入する（配布物の入口）
```

済むと次のファイルが生える。配布物にも git にも入っていない（`out/` は
生成物なので配らない）ので、手元で作るのが唯一の入手経路:

| ファイル | 使いどころ |
|---|---|
| `out/recon/targets.txt` | これが本命。`module:qualname(signature)` 形式で 1,585 件。`@ctx.wrap` にそのまま貼れる |
| `out/recon/game_modules.txt` | ゲーム自身のモジュールの全属性ダンプ。擬似ソースとして読む |
| `out/recon/modules.json` | 機械可読のインベントリ |

読み方と、スキャンで見つからないもの（ネスト関数・クラスのメソッド）は
[GAME.md §1](GAME.md) に集約してある。

#### 手順 1. 雛形をコピーする

```powershell
xcopy /e /i runtime\mods\_template runtime\mods\900_my_mod
```

`_template/` は先頭が `_` なので読み込まれない（探索から外れる。§3.1.1）。
雛形を置いたままにしても一覧に出ないし適用もされない。コピーして名前を付けた時点で
MOD になる。フォルダ名も入口のファイル名も自由で、番号は分類のためだけのもの（§3.2.2）。

コピー直後は `load_order.json` に載っていないので、末尾に置かれる（動くが、
静的検査が「記載の無い MOD」と報告する）。GUI の一覧から並びを決めるか、
`load_order.json` の `"order"` に自分で足す。

#### 手順 2. 対象を決める

`targets.txt` から探す。名前の当てが無いときは `game_modules.txt` を全部眺めるほうが速い
（属性名を推測してスキャンすると空振りする。GAME.md §1.3 の実例）。

```powershell
findstr /i "employ_price" out\recon\targets.txt
findstr /i "add_text"     out\recon\targets.txt
```

見つけた行をそのまま `@ctx.wrap` に貼る。`(signature)` の部分は貼らない:

```
scripts.functions:get_npc_employ_price(npc_difficulty_level)
        ↓
@ctx.wrap("scripts.functions:get_npc_employ_price")
```

まず包んでログを出すだけにする。対象が本当に呼ばれるのか、引数に何が来るのかを
確かめてから中身を書く。雛形が `add_text` を包んで長さを出すだけなのはこのため。

#### 手順 3. 静的検査を通す

```powershell
python -m compileall -q runtime
python tools\check_mods.py
```

`MISMATCH` が出たら直す（`note` は表示専用項目の欠落なので後回しでよい）。ここで
捕まるのは、デコレータの対象名と引数の並びの食い違い、`"entry"` の不在、
`"settings"` と定数のずれ。どれも構文としては正しいので `compileall` では出ない（§2.3）。

#### 手順 4. 注入して確かめる

ゲームが起動している状態で:

```powershell
python tools\injector.py
```

注入が成功したことと、MOD が効いたことは別の話。結果は2箇所を見る:

```powershell
type out\status.json          # mods[フォルダ名] が "ok" か。台帳と効いている設定も入る
type out\modloader.log        # applied / wrapped の行、失敗のトレースバック
```

`status.json` の `["mods"]["900_my_mod"]` が `"ok"`、`modloader.log` に
`wrapped __main__:InstantaleApp.add_text` が出ていれば設置できている。あとは
「MOD を編集 → `python tools\injector.py`」で回す（§2.2。ゲームは起動したまま）。

#### 詰まったとき

| 症状 | 見るところ |
|---|---|
| GUI の一覧に出ない | フォルダ名が `_` / `.` で始まっていないか。`mod.json` はあるか（§3.1.1） |
| `no-entry` | `mod.json` の `"entry"` が入口のファイル名と一致していない |
| `load-error` / `apply-error` | `modloader.log` のトレースバック。1本壊れても他は動く（§3.1） |
| 台帳に `UNRESOLVED` が出る | 対象名が違う。`targets.txt` で取り直す。ゲーム更新も疑う（§3.7） |
| 台帳に `DEFERRED` が出る | まだ import されていないだけ。現れた時点で当て直される（§3.4） |
| 何も起きない・ログも出ない | 注入し損ねている。`status.json` の `boot_count` を見る |
| 直したのに古い動作のまま | `from x import y` の複製束縛か、コンパイル済み関数内で解決済み（§4.1） |
| 1回きりの初期化が走らない | 印はプロセスに残る。`force=True` か `reset_once()`（§2.2 / §3.6） |

書き足す前に §6（落とし穴の一覧）を通読しておくと踏まずに済む。近い手口の
既存 MOD は §7 のカタログから引ける。

### 3.1 最小の形

#### 3.1.1 フォルダと入口

1つの MOD = 1つのフォルダで、`mod.json` を持つものが MOD:

```
runtime/mods/
    load_order.json             適用順（§3.2）
    fix_timings/
        mod.json                名乗りと入口の宣言。ローダはまずこれを読む
        timings.py              入口。apply(ctx) を定義する
    mini_quest/
        mod.json
        quest.py                入口
        prompts.py              分割した中身（from . import prompts）
        data/quest_table.json   同梱データ（ctx.mod_dir から読む）
```

#### 3.1.1.1 1本が大きくなったら分ける

入口が数百行を超えたら分割してよい。ローダは入口をパッケージとして読み込むので
（`_load_mod_file` が `submodule_search_locations` を渡し、`exec_module` の前に
`sys.modules` へ登録する）、`from . import world` がそのまま使える。

分ける線は「何を知っているか」で引く。 実例（`307_area_move_dungeon`、1205行を
756 + 143 + 191 に分けたもの）:

| ファイル | 知っていること | 知らないこと |
|---|---|---|
| 入口 | この MOD の方針・設定・文言・フックの設置 | ― |
| `journey.py` | 自分の状態の持ち方（段階・保存・予算） | ゲームのこと |
| `world.py` | ゲームのどこに何があるか | この MOD の方針 |

| 分けるときの決まり | 理由 |
|---|---|
| 設定の定数は入口に残す | ローダは入口モジュールのグローバルへ書き込む（§3.8）。他のファイルへ移すと GUI から変えても効かない。`check_mods.py` の突き合わせも入口しか見ない |
| 分けた側は設定を読まない | 必要な値は引数で受け取る。読むと「どちらの値が効いているのか」が2箇所になる |
| 分けた側からゲームを触るなら、方針は持たせない | `world.py` は「どこに何があるか」だけ。断る条件・確率・文言は入口 |
| ログ関数（`write`）は引数で渡す | `ctx` を配らない。分けた側が勝手にログの体裁を決めない |

`tools/test_*.py` が mod を読み込む部分もローダと同じ形にすること
（`sys.modules` への登録を忘れると `from . import ...` が落ちる）。

> MOD 単体の部品は MOD のフォルダの中で完結させる。出ていってよいのは
> `out/` のログ（`ctx.out_path`）と `state/` の永続データ（`ctx.state_path`。
> §3.11）だけ。利用者が編むデータファイルも
> `mods/<その MOD>/` に置き、配布フォルダの `settings/` や外部ツールの置き場所を
> 探しに行かないこと。フォルダを1つコピーすれば動き、消せば残らない状態を保つため
> （`111_llm_prompt_replace` の置換ルールがこの形。当初は `settings/` と外部プロキシ
> まで探していたのをやめた）。GUI から変える設定だけは例外で、
> `settings/mod_settings.json` に集める（ローダの仕組み。§3.8）。
>
> 利用者が編むファイルは、配布物が持つ名前と分ける。
>
> ```
> llm_replacements.default.txt   配布物の既定。更新で上書きされる
> llm_replacements.txt           利用者のファイル。あればこちらを読む
> ```
>
> MOD の更新（GUI の「MOD を追加」）は上書きマージなので、消えるのは配布物が同じ
> 名前で持っているファイルだけ。利用者のファイル名を配布物に含めなければ、フォルダの
> 中に置いたまま更新を生き残る。`make_dist.bat` は `llm_replacements.txt` を
> `/XF` で除外している（手元の自分のルールを配布物に混ぜないため。混ざると、
> 次の利用者の更新でその人のルールを上書きしてしまう）。

フォルダ名にもファイル名にも決まりは無い。入口は `mod.json` が名指しする:

```json
{
  "entry": "timings.py",
  "name":        {"en": "Timings KeyError fix", "ja": "timings 欠落の修正"},
  "description": {"en": "Swallows the KeyError ...", "ja": "..."},
  "version": "1",
  "author": "R01/Flossian"
}
```

探索はこの1階層だけで、再帰しない。深く潜ると MOD の中の補助モジュール
（上の `prompts.py`）まで MOD として拾ってしまい、「何が MOD なのか」の規則が増える。

小さい MOD でもフォルダにする。単一ファイルとの混在を許さないのは、
探索・静的検査・GUI・「新しい MOD をどう作るか」の4箇所すべてに分岐が増えるため。

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
| `ctx.out_path(name)` | `out/<name>` の絶対パス。MOD 専用ログはここへ（§3.11） |
| `ctx.state_path(name)` | `state/<name>` の絶対パス。遊びの続きに要るデータはここへ（§3.11） |
| `ctx.mod_dir` | いま apply() 中の MOD のフォルダ。同梱データを読む用（書くのは `out/` か `state/`） |
| `ctx.on_ready(fn)` | プロセスにつき1回だけメインスレッドで実行（§3.6） |
| `ctx.patches()` | 対象 → 当てた MOD の一覧。自分より前の分が見える（§3.7） |
| `ctx.config` / `ctx.setting(名前)` | この MOD に効いている設定値（§3.8） |
| `ctx.api` / `ctx.version` | ローダの API 番号と版（§3.9） |

`target` は `module:qualname` 形式（`llm_manager:quest_referee_event_resolve`、
`llama_cpp_runtime_completion:LlamaCppClient.chat`）。

#### 3.1.4 `@ctx.patch` / `@ctx.wrap` のキーワード引数

| 引数 | 効果 |
|---|---|
| `required=False` | 対象が見つからなくても黙って降りる（既定は例外） |
| `safe=True` | フックの例外をゲームへ流さず、元の動作に落とす（§3.1.5） |
| `alias_scan="all"` | エイリアス張り替えを全モジュールに広げる（既定は関係する範囲だけ。§4.1） |

`@ctx.patch` は対象の名前が無ければ撥ねる。`setattr` は黙って新しい名前を作るので、
対象名を打ち間違えた MOD が「当たった」ことになってしまうため。名前を新設したいときだけ
`required=False` を明示する。

#### 3.1.5 `safe=True` の落とし方

`safe=True` はフックの例外をゲームへ流さない。「ゲームを落とさない」ためだけの
`try` / `except` を毎回書く代わりに使える。落とし方は元の関数がもう走ったかで分かれる:

```python
@ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_button_texts", safe=True)
def paint(orig, self, instance, value):
    result = orig(self, instance, value)
    decorate(self)          # ここで壊れても、ゲームは orig の結果を受け取る
    return result
```

| 状況 | 落とし先 |
|---|---|
| `orig` を呼ぶ前に壊れた | 元の関数を呼んでその結果を返す（素のゲームと同じ挙動） |
| `orig` を呼んだ後に壊れた | その結果をそのまま返す（後処理だけが失敗した） |

2つ目が要点で、単純に「失敗したら元を呼び直す」と書くと元の関数の副作用（テキストの追加・
セーブ・状態の更新）が2回起きる。`safe=True` は例外を隠すので、直すべき不具合を
見えなくもする。例外は `ERROR` としてログに残るので、`safe hook failed` を見たら直すこと。

#### 3.1.6 名乗り（`mod.json`）

```json
{
  "entry": "item_detail.py",
  "api": 1,
  "name":        {"en": "Item detail autosize", "ja": "アイテム説明欄の拡張"},
  "description": {"en": "Grows the item detail box only when a long name will not fit",
                  "ja": "アイテム説明欄を、長い名前・説明が入り切らないときだけ広げる"},
  "version": "1",
  "author": "R01/Flossian"
}
```

`entry` 以外は任意。うち `api` / `after` / `before` / `conflicts` / `settings` は動作に
関わり（§3.2 / §3.8 / §3.9）、`name` / `description` / `version` / `author` は表示専用。

名乗りを Python ではなく JSON に置いているのが要点。GUI は MOD の一覧を作るのに
コードを1行も走らせずに済む。無効化中の MOD も、壊れている MOD も、名前付きで
並べられる。モジュール変数に置くと、一覧表示のためだけに他人の MOD を import する
ことになり、import した時点でトップレベルのコードは走ってしまう。

`status()["manifests"]` は言語ごとの分岐を書かなくて済むよう、次の形に均して返す:

```python
{"dir": "109_fix_item_detail_autosize", "entry": "item_detail.py",
 "name":        {"en": "Item detail autosize", "ja": "アイテム説明欄の拡張"},
 "description": {"en": "...",                  "ja": "..."},
 "version": "1", "author": "R01/Flossian"}
```

片方の言語しか書かれていなければもう片方で埋めるので、`name["ja"]` は必ず何かを
返す（GUI の行が空にならない）。`"name": "Some mod"` のように文字列1つでも書ける。

`name` は一覧に並べる名前なので短く保つ。何をする MOD かの説明は `description` 側に
置き、設計判断は入口ファイルの docstring に書く。目安は日本語で全角12文字ぶん、
英語で半角30文字ぶん ― 名前列の既定幅（200px）に収まる量。

**これは書き方の約束で、検査はしない。** 以前は `tools/test_patch_registry.py` が
長さを落としていたが、外した:

- 名前列は伸縮する（`gui.py` の `COLUMNS` は `stretch=True`）。固定幅で切り落とされる
  わけではなく、窓を広げれば見えるし大きさは記憶される
- **行に描くのは `mod.json` の名前そのものではない。** `superseded` の MOD には
  `〔main_024 で本体が取込〕` が付く ― それだけで旧上限を超えるので、名前の側だけ
  短くしても守りたかったものは守れていなかった

実際にこの検査は `121_ui_character_sheet` の正確な名前を弾いていて、**通すために
名前を悪くする**方向に効いていた。目安を外れて困るのは読み手だけなので、機械ではなく
書く側が決める。

ログにはフォルダ名を出す（名乗りは出さない）。フォルダ名はインストール単位で一意、
cp932 のコンソールでも化けず、grep もしやすい。`version` を必須にしないのは、
バグ修正1本の MOD にまで版番号を付けて回ることになるため。

### 3.2 適用順

#### 3.2.1 `load_order.json`

`runtime/mods/load_order.json` が適用順を決める。先に適用した MOD ほど内側、
後から適用した MOD が外側になる。

```json
{"order": ["000_recon", "001_crash_recorder", "100_fix_kivy_shutdown", "..."],
 "disabled": ["000_recon"]}
```

`"disabled"` に載っている MOD は読み込まない。GUI のチェックボックスの実体で、
フォルダ名を変えずに切れるようにしてある（無効化を `_` 接頭辞でやると、切った
瞬間に `"order"` の中の名前と食い違う）。切った MOD は `modloader.log` に
`disabled in load_order.json; not loaded: ...` として必ず残す。

順序をフォルダ名から決めない理由は、フォルダ名を自由に付けられるようにするため
（「名前は自由」と「順序は名前で決まる」は両立しない）。同梱 MOD のフォルダ名に付いて
いる `000_` `100_` などの番号は作者側の整理のためだけのもので、ローダは見ていない。

| 状況 | 挙動 |
|---|---|
| 順序ファイルに無い MOD | 捨てずに末尾へ回す（フォルダ名順）。置いただけで動く |
| 順序ファイルにあるが実体が無い | 黙って飛ばす（消した MOD の記述が残っていても壊れる） |
| 順序ファイルが壊れている / 無い | フォルダ名順で動く。ここで例外にすると MOD が全滅する |
| `"disabled"` にあるが実体が無い | 何もしない（無効化の記述が残っていても壊れない） |

順序は動作の前提なので、`tools/check_mods.py` が
宣言と実体のずれ（未記載・実体なし・重複）を注入前に報告する。

先頭に `_` を付けたフォルダは読み込まれない。手元で一時的に外すときの手段で、
配って使う無効化は `load_order.json` の `"disabled"` を使う。

#### 3.2.2 同梱 MOD の番号帯

| 帯 | 分類 | 基準 |
|---|---|---|
| `000` | 動作の根本 | リコン・クラッシュ記録。他が触る前の素の状態を押さえる |
| `100` | ゲーム本体の挙動の修正 | 既にある動作を直す・調整する。バグ修正に限らない |
| `200` | 計測（読み取り専用） | 値を変えない。修正より後に置くことに意味がある |
| `300` | 新規機能追加 | 元々無かったものを足す |

計測を修正より後に置くのは、プローブが修正前の生の引数を記録するようにするため。
修正の効果は修正 MOD 自身がログすること。

順序が効くのは同じ対象を2つの MOD が包むときだけで、その関係は帯順で決まる:

```
204_probe_prompt_bloat     が 103_fix_eventlog_trim   を包む
206_probe_quest_flow       が 104_balance_area_bgm    を包む（save_area_json:generate_quest_area を共有）
300_event_facility_arrival が 205_probe_player_events を包む
304_quest_end_keep_party   が 303_quest_end_party_to_guild を包む
305_mini_quest             が 105_fix_schema_compact を包む（LlamaCppClient.chat を共有）
                             → 305_ が先に前提を書き換え、105_ がその後でスキーマを縮める
111_llm_prompt_replace     が 102_ / 103_ / 105_ を包み、305_ に包まれる
                             → 305_ の完全一致の前提を壊さず、置換は圧縮前の本文を見る
```

帯は帯であって分類の軸ではない。ゲーム本体の挙動を変えるなら機能追加でも 100番台で
よい（`104_balance_area_bgm`）。番号を振り直すときは `load_order.json` も直すこと
（フォルダ名を変えるので）。`check_mods.py` が食い違いを報告する。

#### 3.2.3 順序の前提は MOD 自身に宣言させる

順序ファイルは利用者が触るもので、こういう前提を知らない。GUI で行をドラッグすれば
壊せてしまう。文章で書いてあるだけでは守れないので、`mod.json` に書く:

```json
{"entry": "probe.py", "api": 1,
 "after":  ["103_fix_eventlog_trim"],      これより後（＝外側）に適用してほしい
 "before": ["105_fix_schema_compact"],     これより先（＝内側）に適用してほしい
 "conflicts": ["104_balance_area_bgm"]}    同時に有効にしても意味を成さない
```

`discover()` が安定なトポロジカルソートでこれを満たす。基準の並びは `load_order.json`
のままで、制約に触れない MOD の相対順は動かさない（利用者が並べ替えた意図を、制約を
満たす範囲でそのまま残す）。

| 状況 | 挙動 |
|---|---|
| 制約が実体の無い / 無効な MOD を指している | 黙って捨てる。ただし `problems` に報告する |
| 制約が伏せている MOD を指している | 黙って捨てる。報告もしない（§3.2.4） |
| 制約が循環している | `load_order.json` の並びで動かす（ここで全滅させない）。報告する |
| `conflicts` の相手が同時に有効 | 報告するだけで落とさない（下記） |

`conflicts` で片方を落とさないのは、このローダでは同じ対象に複数の MOD を重ねるのが
正常な使い方で（§3.7）、どちらを外すべきかローダには決められないから。両方動かして
名指しし、外すかどうかは利用者が `"disabled"` で決める。

同梱 MOD の宣言はいまの `load_order.json` の並びをそのまま固定しているので、これを
入れても適用順は変わらない。変わるのは「壊せなくなった」ことだけ。

#### 3.2.4 開発者向けの MOD を伏せる（デバッグモード）

計測 MOD（`2xx`）は原因を測るための道具で、遊ぶだけなら要らない。それが配布物では
全部 `order` に載っていて、利用者の環境で常時動いていた。読み取り専用とはいえ、
ポーリングや1文字ごとのログを描画の経路に載せるものもある。

`mod.json` に印を付け、デバッグモードのあいだだけ動かす:

```json
{"entry": "probe.py", "api": 1, "debug": true}
```

切り替えは `settings/loader.json` の `{"debug": true}`。GUI の `gui.json` ではない ―
あれは GUI しか読まない覚え書きだが、この値はゲームの中で `discover()` が読む。
両者が同じファイルを指せる場所は `settings/` だけで、GUI もローダも同じ
`config.settings_dir()` を通っている。`mod_settings.json` にも混ぜない（あちらの形は
「MOD フォルダ名 → 値」で、MOD でないキーを入れると `load_store()` の形が崩れる）。

稼働の制御はそれだけで足りる。`boot()` が回すのは `order` なので、そこから外れれば
読み込まれずパッチも当たらない。仕組みを別に足す必要は無い。

伏せかたで効くのは次の3点で、いずれも利用者の画面に余計なものを出さないため:

| 場所 | すること | 理由 |
|---|---|---|
| `discover()` | `order` からは外し、`listed` には残す | GUI の一覧の並びは保存時にそのまま `order` へ書き戻される（`gui.py` の `save`）。`listed` から落とすと、利用者が保存した瞬間に順序ファイルから記述ごと消える |
| `_order()` | 「無効化されています」「記載の無い MOD」の報告から外す | 切ったのは利用者ではない。伏せたはずのものが警告として出てくる |
| `_sort_dependencies()` | 伏せた相手を指した制約は報告しない | `300_` の `"after": ["205_probe_player_events"]` が毎回「無効な MOD を指している」に出る |

`tools/check_mods.py` は `discover(debug=True)` で呼ぶ。静的検査は入っている MOD を
全部見るのが仕事で、利用者が今どちらに倒しているかで検査の範囲が変わってはいけない
（切っているあいだだけ計測 MOD の `after` が誰にも確かめられない、という穴を作らない）。

`load_order.local.json` の有無で代用しない案もあったが、「手元用の順序ファイルを
置いている＝開発者」という暗黙の判定になる。明示的なフラグなら、不具合報告のときに
利用者へ「デバッグモードを入れて再現してください」と頼める。

#### ゲーム本体が取り込んだ修正を降ろす（`superseded`）

このゲームは更新で MOD 側の修正を取り込むことがある（main_024 では6件が
`Reported by ModLoader` として入った）。取り込まれた修正は要らなくなるが、
消してしまうと退行したときに気付けない。そこで `debug` と同じ扱いで伏せる:

```json
{"entry": "fix.py", "api": 1, "superseded": "main_024"}
```

値は取り込まれた版。読み込みの扱いは `debug` と全く同じ（`discover()` の `hide`）で、
分けてあるのは伏せた理由が違うから。デバッグモードを入れると両方が一覧に並ぶので、
同じ見た目だと「計測のために作ったもの」と「要らなくなった修正」が混ざり、次に
ゲームが更新されたときどれを試しに戻すかが分からなくなる。GUI は行に
`〔main_024 で本体が取込〕` を出し、選ぶと「その版より古いゲームで遊ぶなら戻して
ください」まで説明する。

降ろす前に、その MOD 自身の印で確かめること。 症状が出ないだけでは、本体が
直したのか MOD が直したのか区別が付かない（GAME.md §1.5 / §1.6）。判定に使った
根拠は GAME.md 側に残す ― `mod.json` に書けるのは結論だけで、なぜそう言えるかは
書けない。

セーブに残るものを書き換える MOD は、降ろす動機が一段強い。 `110_` は名前を
書き換えてセーブに焼く一方、本体はパスの側で消毒する。本体が直した後も残すと、
こちらだけが余計に改変する側に回る。冪等なもの（クランプ・刈り込み）や受動的な
もの（発火時に記録するだけ）は、残しても害が無い。

### 3.3 同じ場面に複数の MOD が乗るとき

外側が処理を止めれば内側には呼び出しが届かない。`304_` が解散そのものを止めると、
`303_`（外れた仲間の置き先を変える）には `remove_party_member` が来ない。重ねるなら
「外側が降りたとき内側が本来どおり動く」形にしておくこと。

印のキーは MOD ごとに別にする。自前のボタンは `on_button_press` を包んでボタン辞書の
独自キーで横取りするが（§4.2）、同じキーだと押下が食い合う（`301_` は `mod_action`、
`302_` は `mod_party_action`）。

### 3.4 未 import のモジュールを狙う（保留と当て直し）

ゲームは `llama_cpp_runtime_completion` と `scripts.llm.llm_manager` を最初の LLM
リクエストまで import しない。注入はそれより前に済むので、素朴に書くとプロンプト関係の
フックが1つも設置されないまま進む。`patch.py` はこれを吸収する:

| 状況 | 挙動 |
|---|---|
| モジュールが未 import | `required` に関わらず保留（`defer wrap ...` を記録） |
| モジュールは在るが属性が無い | `required` に従う（本物の間違いなので黙らせない） |

保留があると `boot()` が監視スレッドを立て、`sys.modules` を 5 秒ごとに見て現れた時点で
`boot()` をやり直す（当て直しは手作業の再注入と同じ経路なので層は重ならない）。
1つでも現れたら当て直す。上限は 8 回 / 1 時間。

```
defer wrap llama_cpp_runtime_completion:LlamaCppClient.chat (... is not imported yet)
deferred: waiting for llama_cpp_runtime_completion, scripts.llm.llm_manager (checking every 5s)
deferred: llama_cpp_runtime_completion imported; re-applying mods
boot complete: 27/27 mod(s) applied
```

MOD 側でやること: 対象が未 import でも `apply()` は普通に書いてよい。ただし `apply()`
は当て直しのたびに走るので、何度走らせても結果が変わらないように書く
（グローバルな状態を作るなら、既にあるかを見てから作る）。副作用のある初期化は
`ctx.on_ready()` へ預ける（§3.6）。

### 3.5 再注入しても層が積み重ならない（世代管理）

`boot()` は再 import でローダを作り直すが、ゲーム側に差し込んだ関数は残る。`patch.py` は
各 boot に世代 ID を振り、他世代の層だけを剥がす（自分の層まで剥がすと、同一 boot 内で
`200_` が `101_` を包んだ瞬間に修正が消える）。

| ログ | 意味 |
|---|---|
| `boot #N gen=xxxxxxxx` | この注入の世代 |
| `replacing a previous patch layer on ...` | 前回注入の層を剥がした（正常） |
| （この行が出ない） | 同一 boot 内で後段の MOD が包んだ＝先の層が保持されている |

読み直されるのはモジュールも同じ。注入のたびに `sys.modules` から落としてから
入れ直すものが3段ある。

| 何を | どこで |
|---|---|
| ローダ本体（`instantale_modloader.*`） | 注入コードの冒頭（`tools/injector.py`） |
| MOD の入口（`instantale_mod_<フォルダ名>`） | `_load_mod_file` |
| MOD の中の部品（`instantale_mod_<フォルダ名>.*`） | `_load_mod_file`（入口を入れ直す直前） |

3段目が要点。入口だけ読み直して `from . import panel` の相手を残すと、
新しい入口が古い部品を呼ぶ。分割した MOD を直して注入し直したのに、
部品に足したばかりの関数が `AttributeError: module ... has no attribute` になる
（`116_ui_party_expand` で実際に踏んだ。2026-08-03）。しかも入口側のコードは
新しいので、ログを読んでも「直したはずの行」で落ちているように見える。

### 3.6 1回きりの初期化（`ctx.on_ready`）

`apply()` は1プロセスの中で何度も呼ばれる。手で注入し直したときと、未 import の
モジュールが現れて当て直したとき（§3.4、最大8回）。

パッチを当てるだけなら世代管理（§3.5）が結果を1回分にまとめるので問題ない。困るのは
副作用のある初期化で、`apply()` の中で直接やると回数ぶん繰り返される:

```
溜まった「迷子の曲」の掃除 / 状態ファイルの初期化 / スレッドの起動
```

これを `ctx.on_ready()` に預ける。

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
| 例外 | 握り潰してログへ。`Clock` の中で投げるとゲームが落ちるため |
| キー | 既定は「MOD 名 + 関数名」。`key=` で明示できる |
| 戻り値 | 積まれたら `True`、既に実行済みで捨てられたら `False` |
| `force=True` | 印を無視して積み直す。開発中の逃げ道（配布する MOD に書かない） |
| `reset_once("300_")` | ローダ側から印を落とす。同じく開発用。副作用は戻らない |

印は積んだ時点で付ける（実行時ではない）。流す前に次の boot が来ても二重に積まれない
ようにするため。ただし `Clock` に載せられなかった場合は一度も走っていないので印を外し、
次の boot で積み直せるようにしてある。印の意味は「実行した」ではなく
「実行したか、もう走ることが確定している」。処理自体が例外で終わった場合は印を残す
（毎回の再注入で失敗し続けるのを避けるため）。

「1回だけ」の印は `sys` に置いてある。注入し直すとローダのモジュール自体が読み込み
直されるので、モジュール変数に持つと印ごと消えて再実行されてしまう。

`Clock` が無い環境（`tools/` のオフライン検証）ではその場で同期的に呼ばれる。

#### 3.6.1 見張りを `on_ready` で立てるときの罠（2026-08-03 に踏んだ）

注入し直しても立ち上がらない。 印はプロセスに残るので2回目は黙って捨てられ、
`Clock` の予約は `revert_all()` でも取り消せない（`unload()` の但し書き）。結果、
古い版の見張りが回り続ける。`211_probe_text_speed` で実際に踏んだ ― 計測を
足した版を注入したのにログが1行も増えず、「ゲームが何も出していない」と読み違える
ところだった。

1回きりの初期化（掃除・状態ファイル）なら意図どおりだが、注入し直すたびに
入れ替わってほしいもの（見張り・計測）は別の書き方が要る。組は2つ:

```python
POLL_TOKEN_ATTR = "__instantale_myprobe_poll__"      # 置き場所は sys（上と同じ理由）

def apply(ctx):
    token = "{:x}".format(id(state))        # この apply() 固有の値
    setattr(sys, POLL_TOKEN_ATTR, token)    # いま有効な世代を宣言する

    def start_poll():
        def poll(_dt):
            if getattr(sys, POLL_TOKEN_ATTR, None) != token:
                return False                # 新しい注入が来た ＝ Clock から降りる
            ...
            return True
        Clock.schedule_interval(poll, 1.0)

    # キーに世代を混ぜる。混ぜないと2回目以降は積まれない
    ctx.on_ready(start_poll, key="211_probe_text_speed:poll:{}".format(token))
```

`force=True` でも積み直せるが、あれは印を無視するだけで古い見張りは止まらない
（二重に回る）。降りる側の合図まで含めてこの形にする。

### 3.7 誰がどこへ当てたか（台帳）

`patch_registry.py` が「どの MOD がどの対象に当てたか」を持つ。同じ対象に複数の MOD を
意図的に重ねる設計（§3.2）なので、意図しない重なりをログを目で追わずに見つけるため。

#### 3.7.1 `boot()` の最後に出る報告

```
patches: 61 applied on 54 target(s) by 26 mod(s)
overlapping targets (5):
  llama_cpp_runtime_completion:LlamaCppClient.chat <- 105_fix_schema_compact/, 305_mini_quest/
deferred (2): waiting for the module to be imported
  llm_manager:quest_referee_event_resolve (scripts.llm.llm_manager) <- 206_probe_quest_flow/
UNRESOLVED (1): target not found in the running build
  scripts.ui.shop:ShopFrame.refresh <- 108_fix_shop_inventory_overflow/ (attribute not found)
```

| 節 | 意味 | 対処 |
|---|---|---|
| `overlapping targets` | 2つ以上の MOD が同じ対象を触っている | 正常なことも多い。§3.2 の帯順と突き合わせる |
| `deferred` | モジュールが未 import。後で当て直す（§3.4） | 待てばよい |
| `UNRESOLVED` | モジュールは在るが対象が無い | ゲーム更新を最初に疑う。`out/recon/` で名前を取り直す |

`UNRESOLVED` は `required=True`（既定）なら例外にもなるが、投げる前に記録しているので、
その MOD が `apply-error` で落ちても何が見つからなかったかは報告に残る。バージョン番号を
宣言させるより、実際に対象が在るかを見る方がこの環境では確実（`.pyc` が無く、ゲームの
バージョンを取得する経路も無い）。

#### 3.7.2 プロセスの中からの問い合わせ

```python
instantale_modloader.patches()      # {対象: [MOD, ...]}
instantale_modloader.mod_patches()  # {MOD: [対象, ...]}（逆引き）
instantale_modloader.conflicts()    # 重なっている対象だけ
instantale_modloader.status()       # 下記すべてを1回で
```

`status()` はこれ1回で GUI に要るものが揃う:

| キー | 内容 |
|---|---|
| `["mods"]` | MOD 名（フォルダ名）→ `ok` / `no-entry` / `load-error` / `apply-error` / `no-apply` / `api-too-new` / `api-too-old` |
| `["manifests"]` | MOD 名 → 名乗り（`name` / `description` は `{"en", "ja"}`。§3.1） |
| `["settings"]` | MOD 名 → 実際に効いている設定値（§3.8） |
| `["problems"]` | 宣言と実体のずれ（順序・依存・非互換）。人が読む行 |
| `["patches"]` | 台帳（`by_target` / `by_mod` / `conflicts` / `deferred` / `unresolved` / `counts`） |
| `["api"]` | このローダの API 番号（§3.9） |

`format_report()` が「人が読む行」を返すのに対し、`status()["patches"]`（＝
`patch_registry.summary()`）はデータのまま返す。並び順や言い回しは受け取った側で
決められるよう、ここでは整形しない。

`apply()` の中からは `ctx.patches()`。順に適用されるので、見えるのは自分より前に
読み込まれた MOD の分だけ。

#### 3.7.3 `out/status.json`（ゲームの外との唯一の接点）

`boot()` の最後に `out/status.json` へ書き出す。ゲームの外からこれを読むのが、
GUI と「実際に動いたゲーム」の唯一の接点。注入が成功したことと MOD が入ったことは
別の話なので、ここを読まないと「28個中3個が `apply-error`」を利用者に出せない
（以前は `status()` があるのに呼ぶ側が居らず、利用者が `modloader.log` を自力で開く
しかなかった）。

ゲームの中へ問い合わせる経路を作ると注入をもう1本増やすことになるので、こちらから
書き出す形にしてある。1方向で済み、ゲームが終了した後でも読める。遅延当て直し（§3.4）
のたびに上書きされるので、中身は常に最新の boot。`*.log` とは別扱いで世代管理しない
（常に「今の状態」を表すファイルで、履歴に意味が無い）。

### 3.8 利用者が変えられる設定（`ctx.config`）

設定は今までも「`.py` の先頭の定数を書き換える」でできたが、フレームワークとしては
2つ困ることがあった。

- GUI から見えない。一覧に出るのは名乗りだけで、何が変えられるか分からない
- MOD を更新すると設定が消える。値がコードの中にあるので、新しい版でファイルを
  差し替えた瞬間に利用者の選択が上書きされる

#### 3.8.1 値の置き場所をコードの外へ出す

MOD のコードは何も変えなくてよい。

```
mods/300_event/mod.json    "settings" に何が変えられるかを宣言する
mods/300_event/event.py    EVENT_MODE = "conversation"   ← 既定値。そのまま残す
settings/mod_settings.json 利用者が選んだ値だけ
```

```json
"settings": {
  "EVENT_MODE": {"type": "choice", "values": ["conversation", "narration"],
                 "default": "conversation",
                 "label": {"ja": "イベントの出方", "en": "Event style"},
                 "note":  {"ja": "narration は情景描写に一言足すだけ", "en": "..."}},
  "COOLDOWN_MOVES":  {"type": "int",   "default": 3, "min": 0, "max": 99},
  "CHANCE_OVERRIDE": {"type": "float", "default": null, "allow_null": true}
}
```

ローダは MOD を読み込んだ後・`apply()` を呼ぶ前に、選ばれた値をモジュールのグローバルへ
書き込む。`apply()` の中で作られる入れ子の関数は定数をモジュールのグローバルとして読むので、
この順なら定数をそのまま使っているコードに新しい値が届く。

```
[modloader] INFO    setting EVENT_MODE = 'narration' (default 'conversation')
```

| 項目 | 内容 |
|---|---|
| 扱う型 | `bool` / `int` / `float` / `str` / `choice`（+ `min` / `max` / `allow_null`） |
| 置き場所 | `settings/mod_settings.json`。`mods/` の中には書かない |
| 書くのは | 既定と違う値だけ。既定に戻したら消える |
| 読めない値 | 黙って既定に倒す（設定ファイルが壊れて MOD が全滅しないように）。ログに残る |
| 宣言だけあってコードに定数が無い | 書き込まない＋警告（`@patch` が名前を新設しないのと同じ理由） |
| `apply()` の中から | `ctx.config` / `ctx.setting("EVENT_MODE")` |

`mod_settings.json` を `mods/` の中に置かないのは、そこが配布物そのものだから
（§3.1。`mods/` は読む専用、書くのは `out/`）。MOD のフォルダを丸ごと差し替えても設定は残る。

#### 3.8.2 何を宣言し、何を宣言しないか

辞書やタプルの設定は宣言しない。`KINDS`（お題の一覧）のようなものを GUI の1行に
収めると「JSON を手で書く欄」になり、コードを直接編むより分かりにくい。そういう設定は
コード側に置いたままにする。

ただし「プレイヤーの体験に関わる設定」なら、宣言できる形に割るほうを選ぶ。施設別の
発生率は元々 `CHANCE_BY_TYPE` という1つの辞書だったが、`CHANCE_INN` / `CHANCE_GUILD` /
… と種別ごとの `float` に割って宣言してある（2026-07-26）。施設種別はセーブの
`facility_type` そのもので、ゲーム側で閉じた集合。だから1つずつ並べても項目が
際限なく増えることはない。表は `apply()` の中で組み直す。設定の反映はモジュールの
グローバルへの書き込みなので、トップレベルで組むと既定値の表が固まってしまう。

#### 3.8.3 既定値が2箇所に書かれること

認めている。実際に使われるのはコードの定数で、GUI が表示に使うのは `mod.json` の
`"default"`（GUI は MOD のコードを import しない決まりなので定数を読めない）。ずれると
「GUI では既定 3 と出るのに実際は 5 で動く」という最も気付きにくい形になるので、
`check_mods.py` が AST で突き合わせて報告する。

### 3.9 ローダ API の契約（`"api"`）

`mod.json` の `"api"` が、その MOD が前提にしているローダ API の番号。`boot()` は
コードを読み込む前にこれを見て、扱えない MOD を撥ねる（名乗りが JSON にあるからできる）。

```
[modloader] WARN  someones_mod: needs loader API 2 but this loader provides 1;
                  update InstantaleModLoader
```

| 状況 | 挙動 |
|---|---|
| 書いていない | `1` として扱う（`DEFAULT_API`） |
| ローダより新しい | 読み込まない。`api-too-new` |
| `MIN_API` より古い | 読み込まない。`api-too-old` |
| `ctx.api` | MOD 側から番号を見る（下位互換の分岐が要るとき） |

`__version__` とは別に持っている。前者は配布物の版で、上がっても MOD が壊れるとは限らない。
`API` は壊れる変更のときだけ動かす番号で、だからこそ判定に使える。

| 上げる | 上げない |
|---|---|
| `ctx` のメンバを削除・改名 | `ctx` にメンバを追加 |
| 引数の順序・意味を変更 | 省略可能なキーワード引数を追加 |
| 既定値の変更（`alias_scan` / `required`） | ログの書式・内部の整理 |
| `on_ready` のキー導出の変更 | `__version__` だけの更新 |
| `ui.Screen` の signature 変更 | `ui` / `frames` への関数追加 |

ゲーム側のバージョンを宣言させないのとは事情が違う。ゲームの版は信頼できる形で取れず
（`.pyc` が無く、Epic のマニフェストは別系統）、依存先が在るかは実行時に確かめられる
（台帳の `UNRESOLVED`）。ローダ API はその逆で、版は自分のコードにあるので確実に取れる一方、
意味の変化は `hasattr` では捕まえられない。`alias_scan` の既定を変える・`on_ready` の
キー導出を変えるといった変更は、例外にならないまま挙動だけを変える。

### 3.10 パッチを剥がす（`unload`）

```powershell
python tools/injector.py --unload      # GUI なら「MOD を外す」
```

ゲームを終了せずにパッチを剥がす。MOD を疑うときの切り分けに使う。剥がすための記録は
`sys` に置いてあるので（patch.py の `_undo_log`）、注入から今までの間にローダが何度
読み直されていても剥がせる。

属性を戻すだけでは足りない。当てたときに張り替えた複製束縛（`from x import y` の
コピー）はラッパを指したままで、そこから呼ばれる経路が生き残る。当てたときと同じ範囲を
逆向きに張り替える（§4.1）。

完全に元通りにはならない。戻らないものを断っておく:

```
on_ready で既に起きた副作用（掃除・状態ファイルの初期化）
MOD がゲームの状態そのものに書いた値（パーティの名簿・依頼）
MOD が立てたスレッドや Clock の予約
```

素のゲームで確かめたいなら、注入せずに起動し直すのが確実。

### 3.11 書き込み先（`out/` と `state/`）

配布フォルダ直下に書いてよい場所は3つ。役割で分けてあり、混ぜない。

| 場所 | 何が入るか | 消すと |
|---|---|---|
| `settings/` | 利用者が決めたこと（MOD の設定・GUI の覚え書き・デバッグモード） | 既定に戻る |
| `out/` | MOD が吐いたもの（ログ・リコン成果物・`status.json`） | 何も起きない |
| `state/` | MOD が持つ永続データ（進行中の道中・依頼の出所・NPC の控え） | 遊びが巻き戻る |

MOD からは `ctx.out_path(名前)` と `ctx.state_path(名前)` で引く。どちらも親
ディレクトリを作ってから絶対パスを返すので、使い分けは1行の差でしかない。

```python
log_path    = ctx.out_path("road_travel.log")     # 追えればよい記録
journey_path = ctx.state_path("road_travel.json")  # 続きに要るデータ
```

**判定は「消されたときに何が起きるか」で行う。** 消えても次のプレイに影響しない
なら `out/`。消えるとプレイヤーが積み上げたものが失われるなら `state/`。

元は `out/` が両方を兼ねていた。性質が正反対なので破綻する:

| | |
|---|---|
| 不具合報告の案内 | 「`out/` を消してから再現してください」と言えない（進行中の依頼や NPC の記憶まで飛ぶ） |
| 世代管理（§1.5） | 永続データを飛ばさないことが「対象は `*.log` だけ」という但し書きだけで守られている |
| 利用者の掃除 | ログのつもりで消したものが、遊びの続きだった |

セーブに書かずに `state/` へ持つ理由は MOD ごとに違うが、だいたい次のどちらか。
どちらも「ゲームのデータを汚さない」ための判断で、その代わりに置き場所の責任が
こちらへ来る（「MOD が足したものは MOD が片付ける」）。

| 理由 | 例 |
|---|---|
| セーブの構造を壊さずに足せない | NPC は33項目の並びが決まっている（`310_` の台帳） |
| 足しても往復で残る保証が無い | `Quest` が独自キーを写すかは読めない（`301_` / `305_`） |

置き場所を分ける前に遊んでいた人のデータは、`ctx.state_path()` が拾う。
`state/` 側に無くて `out/` に同じ名前が在れば、1度だけ移してくる（フォルダも
丸ごと移る）。両方には残さない ― 次に読むのがどちらか分からないファイルを
`out/` に残すと、「`out/` は消してよい」がまた崩れるため。移した記録は
`modloader.log` の `state: moved ...` に出る。

> 他の MOD が持っているデータを読むときは、**フォルダを作らないこと**。
> `ctx.state_path()` は親を作るので、`os.path.join(ctx.state_dir, ...)` で組む。
> 相手を切っている人の `state/` に、使われない空のフォルダを置かないため
> （`301_` が `311_` の `npc_profiles/` を読む形）。

---

## 4. Nuitka 環境の制約

### 4.1 効くもの・効かないもの

効く:

- `mod.func = new`（コンパイル済みコードもグローバルはモジュール辞書経由で引く）
- `Cls.method = new`（Nuitka のクラスは通常の `type`）

効かない・要注意:

- `from x import y` で他モジュールに複製された束縛。`patch()` / `wrap()` は既定で
  `alias_scan=True` にしてあり、同一オブジェクトを指すグローバルをスキャンして再束縛する。
  これが無いと「`x.y` は直したのに呼ばれ続ける」が起きる（Kivy の `wm_pen` /
  `wm_touch` が実例で、修正はどちらも複製束縛側から呼ばれる）
- 単一のコンパイル済み関数内でローカル解決された呼び出し → 到達不能。呼び出し元の
  関数ごと差し替えること

張り替えを探す範囲は絞ってある。既定はゲーム自身のモジュール（`GAME_TOPLEVEL`）＋
対象と同じトップレベルパッケージ。配布物には約 4200 のモジュールが入っているので、
全件なめると次の2つが起きる:

- コストが積み上がる。パッチ1本ごとに全モジュールの全グローバルを見ることになり、
  当て直し（§3.4）は最大 8 回ある
- 同じオブジェクトを指しているだけの無関係な名前まで張り替わる

対象のトップレベルを足しているのは、ゲーム以外を狙うパッチのため
（`kivy.input.providers.wm_common` の複製束縛は kivy の中にあるので、ゲームのモジュール
だけに絞ると届かない）。全部なめてほしいときは `alias_scan="all"` を明示する。

### 4.2 テストで identity 比較を使わない

`alias_scan` は古いラッパを指す変数を張り替えるため、テスト側が握っている `__main__` の
グローバルまで張り替えられる（`__main__` は `GAME_TOPLEVEL` に入っていて、直接実行時の
`__main__` はテスト自身）。`Cls.method is not before` は成立しない。確かめるべきは
連鎖の段数と呼び出し結果。

### 4.3 テストのクラスをグローバル名から派生させない

直接実行時の `sys.modules['__main__']` はテスト自身なので、`main.InstantaleApp = app_cls`
はテストのグローバル名を書き換える。`type("InstantaleApp", (InstantaleApp,), {})` と書くと
2回目以降は前回の派生クラスから派生し、前のテストのフックが積み上がって同じ処理が何度も
走る。派生元は `BASES` のような表に控えておく。

---

## 5. 共通部品

実機で確かめた「ゲームがどう動いているか」はここに集約する。同じ発見を MOD ごとに
書き直さないこと。片方が古くなるのは時間の問題で、実際に8件の反映漏れが生まれた。
MOD に残すのはその MOD の設計判断（どこにボタンを出すか、確率、置き場所の規則など）だけ。

### 5.1 `instantale_modloader.ui`

#### 5.1.1 組み立て

`Screen` は `apply()` の中で1つ作って閉じ込める。引数は4つ:

```python
from instantale_modloader import ui

MARK = "mod_my_action"        # モジュール直下に置く（他の MOD と別の文字列にする）

def apply(ctx):
    log_path = ctx.out_path("my_mod.log")

    def write(text):          # この MOD 自身のログ。ローダのログとは分ける
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            ctx.log_exc("my mod: write failed")

    screen = ui.Screen(ctx, write, tag="my mod", mark=MARK)
```

| 引数 | 何を渡すか |
|---|---|
| `ctx` | そのまま渡す。例外を `ctx.log_exc` に流すために握る |
| `write` | この MOD 自身のログ関数。何が起きたかは MOD のログに残したいので `ctx.log` とは分けてある |
| `tag` | ログと例外の見出し（`my mod: scheduled call failed` の形で出る） |
| `mark` | 自前ボタンに付ける印のキー。MOD ごとに別の文字列にする |

`mark` は2段になっているので混同しないこと。`Screen(mark=...)` がボタン辞書の
キーで、`button(mark=...)` がその値（どのボタンか）:

```python
entry = screen.button("依頼を受ける", mark="offer")
# → {'text': '依頼を受ける', 'spec': PhaseSpec(...), 'mod_my_action': 'offer'}
screen.mark_of(entry)        # 'offer'（自分のボタンでなければ None）
```

キーを他の MOD と共有すると、相手の `on_button_press` が自分のボタンを握り潰す。
同梱 MOD が使用中のキーは `mod_action`（`301_`）/ `mod_party_action`（`302_`）/
`mod_mini_action`（`305_`）/ `mod_road_action`（`307_`）/ `mod_pardon_action`
（`309_`）。

印のキーは必ず `ui.MARK_PREFIX`（`mod_`）で始めること。 残骸の掃除
（`prune_stale`）が「他の MOD が今その場に出しているボタン」を見分けるのに、この
接頭辞だけを手がかりにしている（セーブに焼かれるのは `text` と `spec` だけなので、
印が1つでも残っている＝残骸ではない）。接頭辞から外れた印を使うと、その MOD の
ボタンは他の MOD の掃除で消される。

#### 5.1.2 自前の選択肢を出して押下を拾う（最小の流れ）

`app` は `apply()` の時点ではまだ存在しない（ゲームが起動しきる前に注入されうる）。
`ui.find_app()` で引くか、フックの `self` を使う。

```python
    # 1. ボタンを作る。cls_name を省くと無害な JustSetButtonToNormalPhase が付く
    #    （自前クラス名を PhaseSpec に書かない、の実装。§6.2）
    entry = screen.button("依頼を受ける", mark="offer")

    # 2. 差し替えて塗る。Clock 経由なので「次のフレーム・メインスレッド」で行われる
    screen.apply_buttons(app, [entry, cancel], "confirm")

    # 3. 押下は文字列ではなく印で横取りする
    @ctx.wrap("__main__:InstantaleApp.on_button_press", safe=True)
    def on_button_press(orig, self, *args, **kwargs):
        index = args[0] if args else None
        action = screen.mark_of(ui.pressed_entry(self, index))
        if action is None:
            return orig(self, *args, **kwargs)   # 自分のボタンでなければ素通し
        write("pressed {}".format(action))
        # 4. 自前フェーズを起こすなら start_phase（PhaseSpec には載せない）
        screen.start_phase(self, MyPhase(self), "依頼を受ける")
        return None
```

判定を文字列でやらないのは、同じ表示文字列のゲーム側ボタンを巻き込まないため。
UI を触る処理は必ず `screen.schedule` / `apply_buttons` を通す（Clock 経由＝
順序とスレッドが同時に片付く）。Clock から呼ばれる処理で例外を外に出すとゲームを
巻き込むので、自前のコールバックは `screen.guarded(fn)` で包む。

#### 5.1.3 よく使う操作

```python
from instantale_modloader import ui

screen = ui.Screen(ctx, write, tag="quest offer", mark="mod_action")

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
| `paint` | `display_button_load(0)` と `hud.update_button_texts` の2手。`hud not found` を返したら HUD の構成が変わった合図 |
| `paint_party` | 仲間欄を塗り直す。`update_party_member(0)` と `hud.update_party_display()` の2手。パーティを増減させたら最後に呼ぶ |
| `start_phase` | `app.process_choice(インスタンス, 文字列)`。自前フェーズを `PhaseSpec` に載せずに起こす |
| `end_conversation` | 画面のボタンの args を写し `end_text` だけ差し替えて閉じ、閉じ終わってから続きを実行 |
| `when_idle` | `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を見張り、手が空いてから実行 |
| `busy_on` / `busy_off` | LLM を待つ間の待機表示。ゲーム自身と同じ `.` → `..` → `...` をボタン全枠に 0.3 秒周期で出し、`is_button_enabled=False` と送信ボタンの無効化まで行う。`app.buttons` には触らない（表示だけなので後始末が要らない）。`busy_off(restore=False)` は「この後すぐ別の画面を出す」経路用（塗り直すと一瞬だけ古い画面が見える） |
| `is_busy` / `busy_state` | いま待機表示中か／前後で記録するための一行 |

読み取り系:

```python
ui.spec_cls_name(entry) / ui.spec_args(entry) / ui.pressed_entry(app, index)
ui.conversation_partner(buttons) / ui.find_spec_button(...)
ui.find_app() / ui.find_hud(app) / ui.cls_of(...) / ui.IDLE_SIGNALS / ui.SAFE_CLS
ui.current_area(app) / ui.world_areas(...) / ui.nodes_of(...) / ui.facilities_of(...)
ui.find_guild(area) / ui.find_facility(area, id) / ui.facility_name(app, facility)
ui.facility_type_of(...) / ui.GUILD_FACILITY_TYPE
```

HUD へ自前のウィジェットを1枚足すとき（`113_` / `116_`。GAME.md §2.3）:

```python
host = ui.overlay_host(hud)     # 置き場所。**HUD 直下ではない**
host.add_widget(widget)         # 既定は先頭挿入＝一番上に描かれる
setattr(widget, "_instantale_<mod>_<用途>", ...)   # ui.MOD_WIDGET_PREFIX に揃える
```

| 関数 | 何をするか |
|---|---|
| `overlay_host(hud)` | 足す相手を返す。HUD の子は増やさない（増やすと「画面の最初の子」を取る側から見える相手が変わり、アイテムの移動・装備が壊れる）。`children` の古い側から探すので、ゲームが一時的に出している窓や他の MOD のウィジェットを掴まない |
| `added_by_a_mod(widget)` | `_instantale_` で始まるインスタンス属性を持つか ＝ MOD が足したウィジェットか |

自分のウィジェットに付ける控えは `ui.MOD_WIDGET_PREFIX`（`_instantale_`）で
始めること。 `overlay_host` が「他の MOD が足したもの」を置き場所の候補から
外すのに、この接頭辞だけを手がかりにしている。ボタン辞書の印
（`ui.MARK_PREFIX` ＝ `mod_`）とは別で、あちらは選択肢、こちらは
ウィジェットの印。

パーティの名簿（`302_` が4回外して固めた手順。`306_` にも同じものが要ったので
発見のあった mod ではなくここに置いてある。GAME.md §2.8）:

```python
ui.pick_store(app)          # (どこから, id の一覧)。'player' を含む入れ物を本物とみなす
ui.party_ids(app)           # プレイヤーを含む id
ui.party_member_ids(app)    # 同行者だけ（プレイヤーを除く）
ui.party_stores(app) / ui.store_ids(store) / ui.drop_from_store(store, id)
ui.element_id(value) / ui.describe_stores(app)
ui.character_of(app, id) / ui.character_name(app, id)
```

### 5.2 `instantale_modloader.frames`

```python
frames.caller()            # 呼び出し元の連鎖。段数では数えない（wrap の層が挟まる）
frames.owner_of(code)      # method_1 / execute の持ち主クラスを名指しする
frames.attr(obj, name)     # hasattr を使わない存在確認
frames.repr_value(value)   # dict はキーとキーの型を出す
frames.format_locals(...)  # クラッシュ記録用
frames.describe_instance(...)
frames.MISSING             # 「属性が無い」を None と区別する番兵
```

`MISSING` は文字列（`"<missing>"`）。存在確認は `is frames.MISSING` で書き、
読んだ値を他と照合するなら既定を明示して `None` を受け取る
（`frames.attr(w, "source", None)`）。既定のまま `==` で比べると、その属性を
持たない相手が全部一致する。`113_` がこれで、立ち絵を `source` で探したつもりが
画面じゅうのウィジェットに一致し、ボタンが背景画像の上（＝画面の上端）に貼り付いた。

---

## 6. 落とし穴（ルール一覧）

すべて実際に踏んだもの。

### 6.1 MOD とローダの作り

| ルール | 理由 |
|---|---|
| 何度実行しても結果が変わらないように書く | 当て直し（§3.4）と再注入で `apply()` は何度も走る。フックが複数発火しても壊れない形にしておけば、フック選択が致命的でなくなる |
| 同じ規則を2箇所に実装しない | 探索・適用順は `discover()`、設定は `config.py`。GUI もツールもそれを呼ぶ（§1.3） |
| 同じ発見を2箇所に書かない | 実機で確かめた事実は `ui.py` / `frames.py` と GAME.md へ。MOD には設計判断だけ（§5） |
| 順序の前提は文章ではなく `after` / `before` に書く | 文章は守られない。GUI で行を動かせば壊せる（§3.2.3） |
| 利用者に触らせる値は `"settings"` に宣言する | コードの定数だけだと GUI から見えず、MOD の更新で消える（§3.8） |
| `safe=True` を握り潰しの代わりに使わない | 例外はログに残るが見えなくなる。`safe hook failed` が出たら直す（§3.1.5） |
| `on_ready` に `force=True` を残さない | 開発中の逃げ道。配ると当て直しのたびに副作用が起きる（§3.6） |
| 壊れた設定ファイル・順序ファイルで MOD を全滅させない | 既定に倒して動かし、報告する。「動かない」より「報告して動く」（§3.2 / §3.8） |
| 乱数は MOD 専用の `random.Random` | グローバルを使うとゲーム自身の乱数列がずれる |

### 6.2 ゲームの UI と選択肢

| ルール | 理由 |
|---|---|
| 自前のクラス名を `PhaseSpec` に書かない | セーブに焼かれ、MOD 無しの起動で `getattr` が失敗する |
| 自前で組む spec は引数の値まで実測で確かめる | 押下時にゲーム側で実行される＝こちらの `try` の外。1つ間違えば落ちる |
| 値の語彙を推測するくらいなら、語彙を知らずに済む経路を探す | ゲーム自身の入口（`DisplayQuestChoice` / `ConversationStartManager` / `remove_party_member`）に渡せば引数の意味を知らなくてよい |
| 選択肢の差し替えは次のフレーム＋`hud.update_button_texts` | 押下と同じ流れで塗ると古い画面に戻る。`refresh_choice_buttons` だけでは塗り替わらない |
| 会話は閉じてから画面を変える。`end_text` に理由を書く | 閉じないと立ち絵が付いてくる。`end_text` は要約とライフログに残る |
| 後始末の最中に割り込まない | `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を見る |
| 長い処理の間は待機表示を出す | 出さないと操作が効くように見える。ゲーム自身と同じ形（GAME.md §2.4）にすれば違和感が無い |
| UI と pygame は Clock（メインスレッド）から触る | `execute` は別スレッドで走る |
| 寸法・座標を発明しない | グリッドの実寸もレイアウトの仕様も読めない。ゲームが決めた値を最低値にして、足りないぶんだけ動かす |
| レイアウト前の絶対座標から設計を読まない | 作り直された直後のウィジェットは子がまだ配置されていない。`pos_hint` の分数と親のサイズから求める |
| 画面に出す文字列に環境依存文字を使わない | cp932 の外（`▶` U+25B6・`»` U+00BB）と NEC/IBM 拡張（`①` U+2460）は、フォントや端末によって出ない・化ける・`print` した時点で `UnicodeEncodeError`（`308_` のテストが実際に落ちた）。判定は「cp932 に入り、かつ先頭バイトが 0x87 / 0xED-0xEE / 0xFA-0xFC でない」＝ JIS X 0208 の範囲。`tools/test_battle_damage_display.py` の `charset_verdict()` がそのまま使える |
| `"choice"` の候補に空文字・空白だけの値を入れない | GUI は空欄を「未指定」（`None`）として扱うので、`allow_null` でない設定では選んだ瞬間に弾かれる（`tools/gui.py` の `_ok`）。一覧は読み取り専用なので戻すこともできない。「無し」を選ばせたいなら `"なし"` のような名前を値にして、コード側で空文字に読み替える（`308_` の `NO_PREFIX`） |
| 選択肢の値の末尾に空白を持たせない | JSON でも GUI の一覧でも見えず、消えたことに気付けない。記号と本文の区切りはコード側の定数で足す（`308_` の `PREFIX_SEPARATOR`） |
| 他人のボタンを消す判定に、自分の印が無いことだけを使わない | `refresh_choice_buttons` を包む掃除は画面が何であれ走るので、他の MOD が今その場に出しているボタンも「自分の印が無い」に見える。`302_` が `309_` の確認画面から `やめておく` を消していた（2026-08-03）。判定は `ui.Screen.marked_by_a_mod`（`mod_` で始まるキーが1つも無いこと）で行う |
| 残骸の掃除に使う文言は、その MOD にしか無いものだけにする | `やめておく` / `やめる` のような汎用語は他の MOD もゲーム自身も出す。印が落ちている相手は文言でしか見分けられないので、汎用語を混ぜた時点でゲームのボタンを消す穴になる |
| 表示中の文字列を手がかりに描画先のウィジェットを探さない | その文字列を書き換える MOD が入った時点で探索が空振りする。`117_` が本文を載せ替えたら `112_` がラベルを見失い、行間の修正が丸ごと効かなくなった（2026-08-03）。一度実測で属性名が分かったら名前で引く（`hud.text_display`）。文字列の探索は名前で引けなかったときの予備に降ろす（§2.32） |
| ウィジェットの再描画（`texture_update()`）を自分から呼ばない | Kivy はテキストや行間を代入した時点で次のフレームに作り直しを1回予約する。そこへ MOD が自分でも呼ぶと二度手間になり、しかもその代金はフレーム時間に乗るので、フックの中で測っている限り見えない。本文のラベルは1文字ごとに作り直されるので効き方が大きく、実測では 1文字 3回 × 15ms ＝ 45ms（ティック間隔 63.5ms の3分の2）を `112_` と `117_` の2本が食っていた（§2.34）。寸法が要るなら次のフレームに読むだけでよい（Kivy の作り直しのほうが先に走る） |
| 入れ物の子を「先頭」で選ばない | Kivy の `children` は新しい順。先頭はゲームが一時的に出している窓や、他の MOD が足したウィジェットでありうる。画面が組まれた時点から居るものが欲しいなら最後尾から探す。HUD へウィジェットを足すときは `ui.overlay_host`（§5.1.3）を使い、自分で書かない（§2.33） |

### 6.3 計測と観測

| ルール | 理由 |
|---|---|
| 非同期に渡される処理を呼び出しの前後で測らない | `process_choice` は即座に返る。状態を継続監視するか内側で測る |
| 呼び出し元を段数で数えない | `@ctx.wrap` の層が挟まる。ファイル名で飛ばし、`frames.owner_of` で持ち主クラスを名指しする |
| 自分で包んだメソッドを `MethodWatch` で見張らない | 表に入るのはローダのラッパのコードオブジェクトで、`patch.py` の全パッチが共有している。包まれた関数が1つでもスタックに載れば「その中」と答える（`306_` がオフライン検証で踏んだ）。包む対象と見張る対象が重なるなら、自分のラッパでスレッドごとの印を立てる |
| 計測に `hasattr` を使わない | `__getattr__` トリップワイヤを自己発火させる。`frames.attr` を使う |
| `app` を受け取る関数を包むときは、渡されたものが app か確かめる | 別のオブジェクトが渡ってくる経路がある。`<missing>` と `None` は区別して記録する |
| 属性は名前で推測せず `vars()` を全部出す | 名前から探すと空振りする |
| 状態は自前の帳簿ではなくランタイムに聞く | 音は `get_num_channels()`、画面は `hud.buttons[i].text`、名簿は中身を見る |
| 観測できた範囲でしか直さない | 例: `in_battle` は 1→0 を観測できたので下ろす、`in_boss_battle` は観測できないので記録だけ |

### 6.4 ゲームのデータと状態

| ルール | 理由 |
|---|---|
| セーブの形＝実行時の形と決めつけない | 名簿・現在地・戻り値、いずれも実行時は別の形を取りうる |
| フラグ名を信用しない | `in_shopping` は買い物中でなくても True のまま。条件に使う前に実測する |
| 名前や ID がそのままファイルパスになる箇所を疑う | キャラクタ名がディレクトリ名になる。パスに使えない文字が入ると無言で失敗する |
| 同じ値を複数箇所で加工しない。入口ひとつで正す | 5箇所で個別に消毒すると、書き込みと削除でずれて別の不整合を生む |
| 独自キーをゲームのデータ構造に足さない | セーブに焼かれ、再読み込み後に残る保証も無い |

---

## 7. 実装例カタログ

新しい MOD を書くとき、近い手口を使っている既存 MOD を読むのが速い。

### 7.1 直し方（パッチの当て方）

| 手口 | 見る MOD |
|---|---|
| ゲーム自身のヘルパを当てるだけの修正 | `101_`（`clamp_npc_difficulty_value`）、`107_`（通常経路がやっていることを抜けている経路に適用）、`108_`（`find_placement_position` → `place_new_item`） |
| ゲームが決めた値を最低値にして、足りない分だけ広げる | `109_`（アイテム詳細ボックスの高さ・幅） |
| 入口ひとつを直して下流の5箇所を一致させる | `110_`（`Character.__init__` で名前を正す） |
| 組み立てられる前に素データを直す（オブジェクトだけ直しても保存で戻るとき） | `120_`（`World.generate_character` の `character_value` を `orig` の前に直し、id を鍵に持つ辞書を全部書き換える。`Character.__init__` は受け皿に降ろす） |
| 「直してよい相手」を素データの名簿で決める | `120_`（`npcs` に id があるものだけ ＝ 敵と魔物とプレイヤーが自然に落ちる。`category` の値を知らずに済む） |
| 生成物の質が要るところで、生成をやめて用意した表から選ぶ | `120_`（名前は音替えでも LLM でも当たり外れが出た。同梱の名簿から空いているものを引く形にすると、質が入力で決まる。引くたび引き直すが名前は落ち着く ― 結果を素データにも書くので、次に同じ NPC を見たときには衝突が無い。再現性を持たせようと `crc32(id)` で選んだ版は、世界をまたぐと同じ id が同じ名前になった） |
| LLM の出力の揺れを、正規化した鍵で畳んでから裁く | `120_`（表記ゆれ・修飾語・姓名を落とした「読みの骨」で比べる。モデルを問わない） |
| 例外を条件付きで握り潰す | `100_`（`hWnd=None` のときだけ。それ以外は再送出） |
| どのフックが効くか分からないので全部に仕掛ける（重複しても平気な書き方で） | `104_`（BGM）、`105_`（`chat` と `payload`） |

### 7.2 プロンプトと LLM

| 手口 | 見る MOD |
|---|---|
| 関数の引数を書き換える（出力の形は変えない） | `103_`（`quest_event_log`）、`105_`（`messages`）、`301_`（`area_description` に会話を添える） |
| ゲームのプロンプトの前提そのものを差し替える | `305_`（討伐前提の8つの文を実データで裏を取ってから置換。1つでも当たらなければ丸ごと諦める） |
| 判定は全メッセージを繋いで、書き換えは各メッセージに | `305_`（進行判定は1文目が system・クエスト名が user と分かれている）、`111_`（確率の抽選も繋いだ本文に対して1回） |
| 外部（プロキシ）でやっていた加工をプロセス内へ移す | `102_` / `103_` / `105_`（判定条件と出力書式を揃える）、`111_`（ルールファイルの書式まで揃えるので、外部で書いたものをフォルダにコピーすれば動く。本文が復号済みなので `\n` / `\uXXXX` / `$1` の読み替えが要る） |
| 利用者が編むデータファイルを持つ | `111_`（`mods/111_.../llm_replacements.txt` があればそれ、無ければ同梱の `.default.txt`。更新で消えない名前の分け方は §3.1.1。探索も外部参照もしない） |
| 利用者が書いた規則をリクエストのたびに読み直す | `111_`（更新時刻と大きさを見る。読めない間は前回の規則で続ける＝保存の書き込み途中で壊れない。消えたら置換を止める） |
| 同じ加工を複数の地点に仕掛けても1回しか効かせない | `111_`（スレッドの印で内側を素通しし、自分が作った文章を覚えて別スレッド経由の二度目も止める。確率付きの加工はこれが無いと成立しない） |

### 7.3 UI・選択肢・会話

| 手口 | 見る MOD |
|---|---|
| 自前の選択肢ボタンを足して押下を横取りする | `301_` / `302_` / `305_`（`on_button_press` + 独自キー） |
| ゲーム本来のフェーズを自分から起こす | `300_`（`ConversationStartManager`）、`301_` / `305_`（`DisplayQuestChoice`） |
| 引数の語彙を知らないまま、ゲームのボタンの `args` を写して同じ処理を起こす | `307_`（`AreaMoveManager` の `mode`。確認画面から読み取って控え、後で同じ値で起こす） |
| 会話を正しく閉じてから次へ進む | `301_` / `302_`（`ui.Screen.end_conversation`） |
| 待機表示で画面の繋ぎ目を隠す | `301_` / `305_`（`ui.Screen.busy_on` / `busy_off(restore=False)`） |
| 手が空くのを待ってから実行する | `300_` / `303_`（`ui.Screen.when_idle`） |
| 選択肢の枠を使わず、HUD へ自前のウィジェットを1枚足す | `113_`（`Button` を `pos_hint` で隅に置く。`add_widget` の既定は先頭挿入＝一番上に描かれる。フォントは本文のラベルから写す。Kivy の既定に日本語が無いため） |
| ゲームが決めた寸法を、元に戻せる形で変える | `109_` / `113_`（設計値はウィジェット自身に控える。MOD 側の変数に持つと、注入し直したときに変えた後の値を設計値として控える） |
| はみ出した一覧を、位置も中身の大きさも変えずに収める | `115_`（`GridLayout` の `cols` を増やして折り返す。ウィジェットを移し替えないので、ゲームの開閉の後始末と衝突しない） |
| 触ってよい相手を「その直し方が成り立つ能力」で選ぶ | `115_`（列にできるのは `cols` と `minimum_height` を持つ入れ物だけ。型名で弾くのではなく、能力で選ぶと関係ない相手（`ItemDetailBox`）が自然に落ちる） |
| レイアウトが走る前の寸法を控えない | `115_`（入れ物の `spacing` / `padding` から出した高さと実測が噛み合ったときだけ控える。Kivy のレイアウトは次のフレーム。逆算した値と突き合わせる判定は必ず真になる。1回目の版はこの穴で `(0, 0)` を設計値にした） |

### 7.4 状態と後始末

| 手口 | 見る MOD |
|---|---|
| 既にセーブに焼かれた残骸を注入時・ロード時に掃除する | `107_`（`in_battle`）、`110_`（不正な名前） |
| ランタイムに現在の状態を聞いて後始末する | `106_`（pygame のチャンネル） |
| ゲームの処理を止めず「結果の置き先」だけ変える | `303_`（3層で置き先を差し替える） |
| ゲームの処理そのものを起こさせない | `304_`（`remove_party_member` を通さず、置き直しと文言も控えで見分けて抑える） |
| 在り処が不明なデータを中身で見分ける | `302_`（`ui.party_stores` / `pick_store` / `dump_census`） |
| ゲームが計算した値を横取りして、別の相手にも同じことをする | `306_`（`Character.gain_exp` を包み、プレイヤーに入った点数を同行者へ写す。式は読まない） |
| 複数の場面をまたぐ状態を `out/` の控えで持つ（再注入・再起動をまたぐ） | `307_`（移動の予約。段階を `offered` → `armed` → `ready` と進め、前提が崩れたら捨てる） |
| 「いまその処理の中か」を自分のラッパの印で持つ | `306_`（`execute` を包んでスレッドごとの印。見張る対象を自分で包むので `MethodWatch` は使えない） |
| ゲームが出さない数字を、状態の前後の差から出す | `308_`（1手の前後で全員の HP を比べる。ダメージの式も、誰が誰に当てたかの語彙も読まない） |
| 差分の報告点を何箇所にも置いて二重に出さない | `308_`（台帳方式。「比べる → 出す → 台帳を今の値へ進める」を1つの操作にする。内側が先に報告すれば外側には差が残らないので、報告点をいくつ足しても重ならない） |

### 7.5 計測・調査

| 手口 | 見る MOD |
|---|---|
| 読み取り専用で経路を特定する | `205_` / `206_` / `207_`（計測は修正より後＝外側に置く） |
| `__getattr__` トリップワイヤ | `201_` |
| 20Hz で画面状態の変化だけ拾う | `206_`（waitstate watcher） |

---

## 8. 制限

- 注入はゲーム起動後なので、import 時点で走るコードにはパッチできない。必要になったら
  `python310.dll` プロキシ DLL で `Py_InitializeEx` をフックする方式に切り替える
  （要 MSVC Build Tools・要管理者権限・Epic の repair で戻る）
- GIL を長時間占有する推論中に注入すると、スタブの完走が遅れる（30 秒でタイムアウト表示に
  なるが、スタブ自体はその後完走する）
- 自前の選択肢ボタンはセーブに残骸として焼かれうる。無害な既存クラスを spec に持たせて
  あるので壊れないが、MOD 無しで押すと何も起きない
- 選択肢のページ送りは1ページに収まる場合しか実測できていない。`ui.pressed_entry` は
  `display_button_map` があればそれを使う形にしてある
- ネイティブクラッシュ（`%LOCALAPPDATA%\CrashDumps\instantale.exe.*.dmp`）は Python 例外
  ではないので `crash_log.txt` にも `001_` にも残らない。解析には cdb/WinDbg が要る
