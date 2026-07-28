# TECH — MOD 開発リファレンス

Instantale（Epic版 / Nuitka standalone / CPython 3.10）に外部から Python を注入し、
実行中のゲームを monkeypatch する仕組み。**これから MOD を書く人のための資料**。

- 遊ぶだけなら [README.md](README.md)
- 各 MOD の検証状況・未確認項目・実測ログは [VERIFICATION.md](VERIFICATION.md)
- 本書は**事実とルール**。個々の MOD の設計判断はその MOD の docstring に、実機で確かめた
  「ゲームがどう動いているか」は `ui.py` / `frames.py` に置く

---

## 1. 仕組み

ゲームは Nuitka standalone ビルドで、純 Python モジュールは全て `instantale.exe`（715MB）
内にネイティブコード化されている。**`.pyc` は無く、逆コンパイルもファイル差し替えもできない。**

一方 **CPython ランタイムは `python310.dll` として動的リンクされたまま**で、C-API 1607
関数がエクスポートされている。そこで既に走っているインタプリタにコードを流し込む:

```c
PyGILState_STATE s = PyGILState_Ensure();
PyRun_SimpleString(bootstrap);
PyGILState_Release(s);
```

これを 74 バイトの x64 スタブとして `VirtualAllocEx` + `CreateRemoteThread` で実行する。
C コンパイラも管理者権限も要らず、ゲームフォルダは読むだけ（`sys.path` は本ディレクトリ
配下の `runtime/` を向く）。注入されたコードは `instantale_modloader.boot()` を呼び、
`runtime/mods/*.py` をファイル名順に適用する。

```
watch.bat / watcher.py    ゲームの起動を監視して自動注入
injector.py               PE解析 → x64スタブ → CreateRemoteThread
logrotate.py              out/*.log の世代管理（注入 = 1世代の境目）
runtime/instantale_modloader/
    __init__.py   boot() / ログ / mod ディスカバリ / 世代発行 / 遅延設置の監視 / on_ready
    patch.py      @patch / @wrap / alias再束縛 / 世代管理 / 未import保留 / revert
    patch_registry.py  どの MOD がどこへ当てたかの台帳・重なり・未解決の報告
    frames.py     フレームローカル採取・値の要約・呼び出し元の特定
    ui.py         選択肢 / 画面の塗り替え / 会話の閉じ方 / idle待ち / 施設の引き当て
    recon.py      実行時リコン（モジュール構造ダンプ）
runtime/mods/     MOD 本体（1バグ・1機能 = 1ファイル）
out/              ログとリコン成果物
tools/            静的検査・オフライン検証・セーブ操作（すべてゲーム不要）
```

**注入のタイミング。** `watcher.py` は新しい pid に対し、(1) `Py_IsInitialized` をリモート
スレッドで直接呼んでインタプリタ初期化を確認し、(2) 可視ウィンドウの出現を待つ。後者は
Kivy が立ち上がり `__main__` の実行が終わった合図。これより早く注入してもパッチ対象が
まだ存在しない。

**ログの世代管理。** ログは全て「開く→追記→閉じる」で書かれるので、何もしなければ
プレイをまたいで積み上がる（実測で `events.log` / `quest_flow.log` が数MB）。
`logrotate.py` が**注入の直前に** `out/` 直下の `*.log` を `名前.log.1` へ送り、本体を
空から始める（`KEEP_GENERATIONS` 世代ぶん保持、既定 1）。切り替えは
`--no-log-rotate` → 環境変数 `INSTANTALE_LOG_ROTATE` → `ROTATE_LOGS` の順に優先。

入れ替えを**ゲームプロセスの中（`boot()`）でやらない**理由が2つある。1つは `boot()` が
自分で `modloader.log` に書いている最中に走ること。もう1つは遅延設置の当て直し（§3.4）
でも `boot()` が呼ばれるため、1回のプレイの記録が途中で分断されること。注入は世代の
境目そのものなので、注入する側で1回だけ行えば両方とも起きない。対象は `out/` 直下の
`*.log` だけで、`out/test/` `out/recon/` と `quest_clients.json` のような状態ファイルには
触らない。

---

## 2. 開発の流れ

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

# 3. ローダ全体が読めるかの確認（フックは大半が保留になるが、import と apply() の失敗が出る）
python -c "import sys; sys.path.insert(0,'runtime'); import instantale_modloader as l; print(l.boot('out/test/bootcheck'))"

# 4. 注入（ゲームが起動している状態で）
python injector.py
python injector.py --dry-run   # アドレス解決だけ。何も書き込まない
```

**編集ループは「MOD を編集 → `python injector.py`」。** `boot()` が
`instantale_modloader` を `sys.modules` から落として再 import するので、そのまま反映される
（層は積み上がらない。§3.5）。

> **注入はプロセスと一緒に消える。ゲームを起動するたびに注入し直すこと。**

**`tools/check_mods.py` は必ず通す。** `compileall` は構文しか見ないが、実際にゲームを
落とすのは構文として正しいコードのほう —「`@ctx.wrap` の対象名と、飾っている関数の引数の
並びが食い違っている」類は静的に捕まえられる。`@ctx.wrap` が飾る関数の第1引数は `orig`、
メソッド対象なら第2引数は `self`。

**ゲーム側は Python 3.10。** 3.11 以降の構文を使わないこと（この環境の python は 3.13 しか
無いので `compileall` は 3.10 互換を保証しない）。

**`watch.bat` は ASCII のみ。** `.bat` はその時のコンソールのコードページで読まれるため、
日本語を入れると環境によって解析が壊れる。

**ツールから MOD を読むときは番号を書かない。** `tools/` の各スクリプトは
`find_mod("_balance_area_bgm.py")` のように番号を除いた名前で引く。分類を見直して番号を
振り直しても壊れないようにするため。

---

## 3. MOD の書き方

### 3.1 最小の形

```python
# -*- coding: utf-8 -*-
"""何をする MOD か。なぜその作りなのか。"""

NAME = "fix: swallow KeyError 'timings'"

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

| | |
|---|---|
| `@ctx.patch(target)` | 完全置換。置換関数から `__original__` で元にアクセスできる |
| `@ctx.wrap(target)` | 元関数を第1引数で受け取るラッパ |
| `ctx.resolve(target)` | `(owner, name, value)` を返す。調査用 |
| `ctx.log(...)` / `ctx.log_exc(...)` | `out/modloader.log` へ |
| `ctx.out_path(name)` | `out/<name>` の絶対パス。MOD 専用ログはここへ |
| `ctx.on_ready(fn)` | **プロセスにつき1回だけ**メインスレッドで実行（§3.6） |
| `ctx.patches()` | 対象 → 当てた MOD の一覧。自分より前の分が見える（§3.7） |

`target` は `module:qualname` 形式（`llm_manager:quest_referee_event_resolve`、
`llama_cpp_runtime_completion:LlamaCppClient.chat`）。`required=False` を渡すと対象が
無くても黙って降りる。

`@ctx.patch` は**対象の名前が無ければ撥ねる**。`setattr` は黙って新しい名前を作るので、
対象名を打ち間違えた MOD が「当たった」ことになってしまうため。名前を新設したいときだけ
`required=False` を明示する。

**名乗り（モジュール変数。すべて任意・表示専用）:**

```python
NAME    = "Item detail autosize"          # 一覧に出す短い名前。無ければファイル名
NAME_JA = "アイテム説明欄の拡張"
VERSION = "1"
DESCRIPTION    = "Grows the item detail box only when a long name will not fit"
DESCRIPTION_JA = "アイテム説明欄を、長い名前・説明が入り切らないときだけ広げる"
AUTHOR  = "R01/Flossian"
```

多言語は**接尾辞**で持つ（`_JA` が日本語、無印が英語）。`status()["manifests"]` は
言語ごとの分岐を書かなくて済むよう、次の形に均して返す:

```python
{"file": "109_...py",
 "name":        {"en": "Item detail autosize", "ja": "アイテム説明欄の拡張"},
 "description": {"en": "...",                  "ja": "..."},
 "version": "1", "author": "R01/Flossian"}
```

**片方しか書かれていなければもう片方で埋める**ので、`name["ja"]` は必ず何かを返す
（GUI の行が空にならない）。`_JA` を書かない MOD もそのまま動く。

`NAME` は**一覧に並べる名前**なので短く保つ（英語 半角30 / 日本語 全角12 まで。
`tools/test_patch_registry.py` が長さを検査する）。何をする MOD かの説明は
`DESCRIPTION` 側に置き、設計判断は docstring に書く。

**ログにはファイル名を出す**（名乗りは出さない）。ファイル名は適用順そのもので一意、
cp932 のコンソールでも化けず、grep もしやすい。VERSION を必須にしないのは、
バグ修正1本の MOD にまで版番号を付けて回ることになるため。

### 3.2 採番と適用順

ファイル名順（`sorted()`）に適用され、**後から適用した MOD が外側**になる。

| 帯 | 分類 | 基準 |
|---|---|---|
| `000` | 動作の根本 | リコン・クラッシュ記録。他が触る前の素の状態を押さえる |
| `100` | ゲーム本体の挙動の修正 | 既にある動作を直す・調整する。バグ修正に限らない |
| `200` | 計測（読み取り専用） | 値を変えない。**修正より後**に置くことに意味がある |
| `300` | 新規機能追加 | 元々無かったものを足す |

計測を修正より後に置くのは、**プローブが修正前の生の引数を記録する**ようにするため。
修正の効果は修正 MOD 自身がログすること。

**順序が効くのは同じ対象を2つの MOD が包むときだけ**で、その関係は帯順で決まる:

```
204_probe_prompt_bloat     が 103_fix_eventlog_trim   を包む
206_probe_quest_flow       が 104_balance_area_bgm    を包む（save_area_json:generate_quest_area を共有）
300_event_facility_arrival が 205_probe_player_events を包む
304_quest_end_keep_party   が 303_quest_end_party_to_guild を包む
305_mini_quest             が 105_fix_schema_compact を包む（LlamaCppClient.chat を共有）
                             → 305_ が先に前提を書き換え、105_ がその後でスキーマを縮める
```

`NAME` の接頭辞（`fix:` / `feature:` / `probe:`）は帯とは別の軸。ゲーム本体の挙動を変える
なら機能追加でも 100番台でよい（`104_balance_area_bgm`）。番号を振り直すときは
「計測は対象より外側」という関係が崩れていないかだけ確かめる。

**先頭に `_` を付けたファイルは読み込まれない**（一時的な無効化）。

### 3.3 同じ場面に複数の MOD が乗るとき

外側が処理を止めれば内側には呼び出しが届かない。`304_` が解散そのものを止めると、
`303_`（外れた仲間の置き先を変える）には `remove_party_member` が来ない。**重ねるなら
「外側が降りたとき内側が本来どおり動く」形にしておく**こと。

**印のキーは MOD ごとに別にする。** 自前のボタンは `on_button_press` を包んでボタン辞書の
独自キーで横取りするが（§5.2）、同じキーだと押下が食い合う（`301_` は `mod_action`、
`302_` は `mod_party_action`）。

### 3.4 未 import のモジュールを狙う（保留と当て直し）

ゲームは `llama_cpp_runtime_completion` と `scripts.llm.llm_manager` を**最初の LLM
リクエストまで import しない**。注入はそれより前に済むので、素朴に書くとプロンプト関係の
フックが1つも設置されないまま進む。`patch.py` はこれを吸収する:

| 状況 | 挙動 |
|---|---|
| モジュールが未 import | `required` に関わらず**保留**（`defer wrap ...` を記録） |
| モジュールは在るが属性が無い | `required` に従う（本物の間違いなので黙らせない） |

保留があると `boot()` が監視スレッドを立て、`sys.modules` を 5 秒ごとに見て**現れた時点で
`boot()` をやり直す**（当て直しは手作業の再注入と同じ経路なので層は重ならない）。
1つでも現れたら当て直す。上限は 8 回 / 1 時間。

```
defer wrap llama_cpp_runtime_completion:LlamaCppClient.chat (... is not imported yet)
deferred: waiting for llama_cpp_runtime_completion, scripts.llm.llm_manager (checking every 5s)
deferred: llama_cpp_runtime_completion imported; re-applying mods
boot complete: 27/27 mod(s) applied
```

**MOD 側でやること**: 対象が未 import でも `apply()` は普通に書いてよい。ただし `apply()`
は当て直しのたびに走るので、**何度走らせても結果が変わらないように書く**
（グローバルな状態を作るなら、既にあるかを見てから作る）。副作用のある初期化は
`ctx.on_ready()` へ預ける（§3.6）。

### 3.5 再注入しても層が積み重ならない（世代管理）

`boot()` は再 import でローダを作り直すが、ゲーム側に差し込んだ関数は残る。`patch.py` は
各 boot に世代 ID を振り、**他世代の層だけを剥がす**（自分の層まで剥がすと、同一 boot 内で
`200_` が `101_` を包んだ瞬間に修正が消える）。

| ログ | 意味 |
|---|---|
| `boot #N gen=xxxxxxxx` | この注入の世代 |
| `replacing a previous patch layer on ...` | 前回注入の層を剥がした（正常） |
| （この行が出ない） | 同一 boot 内で後段の MOD が包んだ＝先の層が保持されている |

### 3.6 1回きりの初期化（`ctx.on_ready`）

`apply()` は**1プロセスの中で何度も呼ばれる**。手で注入し直したときと、未 import の
モジュールが現れて当て直したとき（§3.4、最大8回）。

パッチを当てるだけなら世代管理（§3.5）が結果を1回分にまとめるので問題ない。困るのは
**副作用のある初期化**で、`apply()` の中で直接やると回数ぶん繰り返される:

```
溜まった「迷子の曲」の掃除 / 状態ファイルの初期化 / スレッドの起動
```

これを `ctx.on_ready()` に預ける。

```python
def apply(ctx):
    @ctx.wrap("...")            # パッチは今までどおり apply() で当てる
    def hook(orig, *a, **kw):
        return orig(*a, **kw)

    ctx.on_ready(lambda: sweep_orphan_tracks(ctx))   # 掃除は1回だけ
```

| | |
|---|---|
| 実行回数 | **プロセスにつき1回**。再注入・当て直しをまたいでも増えない |
| 実行スレッド | Kivy の `Clock` 経由＝**メインスレッド**（`boot()` はリモートスレッドの上） |
| タイミング | 全 MOD の適用が済んでから。`delay=` で先送りできる |
| 例外 | 握り潰してログへ。`Clock` の中で投げるとゲームが落ちるため |
| キー | 既定は「ファイル名 + 関数名」。`key=` で明示できる |
| 戻り値 | 積まれたら `True`、既に実行済みで捨てられたら `False` |

印は**積んだ時点**で付ける（実行時ではない）。流す前に次の boot が来ても二重に積まれない
ようにするため。ただし `Clock` に載せられなかった場合は**一度も走っていない**ので印を外し、
次の boot で積み直せるようにしてある ― 印の意味は「実行した」ではなく
「実行したか、もう走ることが確定している」。処理自体が例外で終わった場合は印を残す
（毎回の再注入で失敗し続けるのを避けるため）。

「1回だけ」の印は `sys` に置いてある。**注入し直すとローダのモジュール自体が読み込み
直される**ので、モジュール変数に持つと印ごと消えて再実行されてしまう。

`Clock` が無い環境（`tools/` のオフライン検証）ではその場で同期的に呼ばれる。

### 3.7 誰がどこへ当てたか（台帳）

`patch_registry.py` が「どの MOD がどの対象に当てたか」を持つ。同じ対象に複数の MOD を
**意図的に重ねる**設計（§3.2）なので、意図しない重なりをログを目で追わずに見つけるため。

`boot()` の最後にまとめて出る:

```
patches: 61 applied on 54 target(s) by 26 mod(s)
overlapping targets (5):
  llama_cpp_runtime_completion:LlamaCppClient.chat <- 105_fix_schema_compact.py, 305_mini_quest.py
deferred (2): waiting for the module to be imported
  llm_manager:quest_referee_event_resolve (scripts.llm.llm_manager) <- 206_probe_quest_flow.py
UNRESOLVED (1): target not found in the running build
  scripts.ui.shop:ShopFrame.refresh <- 108_fix_shop_inventory_overflow.py (attribute not found)
```

| 節 | 意味 | 対処 |
|---|---|---|
| `overlapping targets` | 2つ以上の MOD が同じ対象を触っている | 正常なことも多い。§3.2 の帯順と突き合わせる |
| `deferred` | モジュールが未 import。後で当て直す（§3.4） | 待てばよい |
| `UNRESOLVED` | モジュールは在るが対象が無い | **ゲーム更新を最初に疑う**。`out/recon/` で名前を取り直す |

`UNRESOLVED` は `required=True`（既定）なら例外にもなるが、**投げる前に記録している**ので、
その MOD が `apply-error` で落ちても何が見つからなかったかは報告に残る。バージョン番号を
宣言させるより、実際に対象が在るかを見る方がこの環境では確実（`.pyc` が無く、ゲームの
バージョンを取得する経路も無い）。

動いているプロセスへの問い合わせ:

```python
instantale_modloader.patches()      # {対象: [MOD, ...]}
instantale_modloader.mod_patches()  # {MOD: [対象, ...]}（逆引き）
instantale_modloader.conflicts()    # 重なっている対象だけ
instantale_modloader.status()       # 下記すべてを1回で
```

`status()` はこれ1回で GUI に要るものが揃う:

| キー | 内容 |
|---|---|
| `["mods"]` | ファイル名 → `ok` / `load-error` / `apply-error` / `no-apply` |
| `["manifests"]` | ファイル名 → 名乗り（`name` / `description` は `{"en", "ja"}`。§3.1） |
| `["patches"]` | 台帳（`by_target` / `by_mod` / `conflicts` / `deferred` / `unresolved` / `counts`） |

`format_report()` が「人が読む行」を返すのに対し、`status()["patches"]`（＝
`patch_registry.summary()`）は**データのまま**返す。並び順や言い回しは受け取った側で
決められるよう、ここでは整形しない。

`apply()` の中からは `ctx.patches()`。ファイル名順の適用なので、見えるのは**自分より前に
読み込まれた MOD の分だけ**。

---

## 4. パッチ対象の見つけ方

### 4.1 リコン成果物 (`out/recon/`)

**ソースが読めない以上、正確なパッチ対象名はここからしか得られない。**

| ファイル | 内容 |
|---|---|
| `targets.txt` | `module:qualname(signature)` 形式。`@patch` にそのまま貼れる（1466件） |
| `game_modules.txt` | ゲーム自身のモジュールの全属性ダンプ（擬似ソース一覧） |
| `modules.json` | 全モジュールの機械可読インベントリ |
| `summary.txt` | 環境・`sys.path`・モジュール census |
| `bug_sites.txt` | crash_log.txt の各クラッシュ地点のプローブ + キーワード掃引 |

### 4.2 ゲーム自身のモジュール

```
__main__                       instantale.py, 約10,600行, 516ターゲット
scripts                        scripts.hud.* / scripts.llm.* / items / functions ほか
Embedding, image_generation, llama_cpp_runtime_completion, sidecar_process
save_area_json, save_world_json, api_key_manager, build_type, sdcpp_cuda
```

**`__main__` は `sys.stdlib_module_names` に含まれる。** 素朴に stdlib を除外すると
ゲーム本体が丸ごと漏れる（`recon.py` の `GAME_TOPLEVEL` はアローリスト）。

### 4.3 掃引で見つからないもの

- **ネスト関数はモジュールのグローバルに現れない。** `send_request_on_id` はトレース
  バックに 62 回出るが `vars(module)` には無い（`send_request` 内の `backoff` デコレータ
  付きネスト関数）。実際の対象は外側の `send_request` / `send_request_with_no_structure`
- **クラスのメソッドはモジュールレベルのキーワード掃引で 0 件に見える**
  （`set_ai_models` / `show_world_choice` など）。`game_modules.txt` を見る
- **属性名を推測して探すと空振りする。** `vars(obj)` を一度全部出すほうが速い
  （HUD の描画先を `texts` / `labels` という名前で探して見つからなかった実例がある。
  正解は `hud.buttons[i].text`。§7.3）

### 4.4 環境の基本値

| 項目 | 値 |
|---|---|
| ゲーム本体 | `C:\Program Files\Epic Games\Instantaleq6Ve7\instantale.exe` |
| ランタイム | CPython 3.10.11 / Kivy / SDL2 |
| `game_version` | **`013`**（`__main__.get_game_version()`）。Epic の `AppVersion: main_022` は別系統 |
| ロード済みモジュール | 4175（うち 3240 が Nuitka コンパイル済み）／ゲーム自身は 70 |
| セーブ | `%LOCALAPPDATA%\Darmabeko\Instantale\` |
| クラッシュログ | `<ゲームdir>\crash_log.txt` |
| LLM 入出力の記録 | `<ゲームdir>\output_data\<世界>\<PC>\<manager>\N.json` |

**ゲーム内部のバージョンは実行時に問い合わせること**（Epic のマニフェストとは無関係）。

---

## 5. Nuitka 環境の制約

### 5.1 効くもの・効かないもの

効く:

- `mod.func = new` — コンパイル済みコードもグローバルはモジュール辞書経由で引く
- `Cls.method = new` — Nuitka のクラスは通常の `type`

効かない・要注意:

- **`from x import y` で他モジュールに複製された束縛。** `patch()` / `wrap()` は既定で
  `alias_scan=True` にしてあり、`sys.modules` を掃引して同一オブジェクトを指す全グローバルを
  再束縛する。これが無いと「`x.y` は直したのに呼ばれ続ける」が起きる（Kivy の `wm_pen` /
  `wm_touch` が実例で、修正はどちらも複製束縛側から呼ばれる）
- **単一のコンパイル済み関数内でローカル解決された呼び出し** → 到達不能。呼び出し元の
  関数ごと差し替えること

### 5.2 テストで identity 比較を使わない

`alias_scan` が `sys.modules` を掃引して古いラッパを指す変数を全部張り替えるため、
**テスト側が握っている `__main__` のグローバルまで張り替えられる**。
`Cls.method is not before` は成立しない。確かめるべきは**連鎖の段数と呼び出し結果**。

### 5.3 テストのクラスをグローバル名から派生させない

直接実行時の `sys.modules['__main__']` はテスト自身なので、`main.InstantaleApp = app_cls`
はテストのグローバル名を書き換える。`type("InstantaleApp", (InstantaleApp,), {})` と書くと
2回目以降は前回の派生クラスから派生し、前のテストのフックが積み上がって同じ処理が何度も
走る。**派生元は `BASES` のような表に控えておく。**

---

## 6. 共通部品

実機で確かめた「ゲームがどう動いているか」はここに集約する。**同じ発見を MOD ごとに
書き直さないこと** — 片方が古くなるのは時間の問題で、実際に8件の反映漏れが生まれた。
MOD に残すのは**その MOD の設計判断**（どこにボタンを出すか、確率、置き場所の規則など）だけ。

### 6.1 `instantale_modloader.ui`

```python
from instantale_modloader import ui

screen = ui.Screen(ctx, write, tag="quest offer", mark="mod_action")

entry = screen.button("依頼を受ける", mark="offer")    # 無害な既存 spec を持たせる
screen.apply_buttons(app, [entry, cancel], "confirm")   # 次のフレームで差し替え＋塗る
screen.start_phase(app, MyPhase(app), "依頼を受ける")   # process_choice に乗せる
screen.end_conversation(app, end_entry, follow_up, end_text="<行動: …>")
screen.when_idle(app, then, cancel_if=..., proceed_on_timeout=True)
screen.paint(app) / screen.refresh(app) / screen.say(app, text)
```

| 関数 | 何をするか |
|---|---|
| `apply_buttons` | `Clock.schedule_once(..., 0)` 経由で `app.buttons` を差し替え、`refresh` と `paint` まで行う |
| `paint` | `display_button_load(0)` と `hud.update_button_texts` の2手。`hud not found` を返したら HUD の構成が変わった合図 |
| `start_phase` | `app.process_choice(インスタンス, 文字列)`。自前フェーズを `PhaseSpec` に載せずに起こす |
| `end_conversation` | 画面のボタンの args を写し `end_text` だけ差し替えて閉じ、閉じ終わってから続きを実行 |
| `when_idle` | `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を見張り、手が空いてから実行 |

読み取り系:

```python
ui.spec_cls_name(entry) / ui.spec_args(entry) / ui.pressed_entry(app, index)
ui.conversation_partner(buttons) / ui.find_spec_button(...)
ui.find_app() / ui.find_hud(app) / ui.cls_of(...) / ui.IDLE_SIGNALS / ui.SAFE_CLS
ui.current_area(app) / ui.world_areas(...) / ui.nodes_of(...) / ui.facilities_of(...)
ui.find_guild(area) / ui.find_facility(area, id) / ui.facility_name(app, facility)
ui.facility_type_of(...) / ui.GUILD_FACILITY_TYPE
```

### 6.2 `instantale_modloader.frames`

```python
frames.caller()            # 呼び出し元の連鎖。段数では数えない（wrap の層が挟まる）
frames.owner_of(code)      # method_1 / execute の持ち主クラスを名指しする
frames.attr(obj, name)     # hasattr を使わない存在確認
frames.repr_value(value)   # dict はキーとキーの型を出す
frames.format_locals(...)  # クラッシュ記録用
frames.describe_instance(...)
frames.MISSING             # 「属性が無い」を None と区別する番兵
```

---

## 7. ゲーム内部リファレンス

### 7.1 スレッド

```
process_choice(MovePhaseManager, ...)                   [MainThread]
ConversationStartManager.execute('謎の女・ミラ')         [Thread-767 (execute)]
QuestEndManager.execute -> method_1                      [別スレッド]
```

**`process_choice` はメインスレッドで呼ばれ、その中で `execute` を別スレッドへ渡す。**

- **UI と pygame を触るのは Kivy の `Clock` から。** `execute` の中から直接触らない
- Clock から押す実装が本来のボタン押下と同じ経路になる。**自前でスレッドを立てる必要は無い**
- **長い処理は `execute` の中で同期的にやりきる。** そこからさらに別スレッドへ投げて即座に
  返すと、ゲームは行動が終わったと判断して操作を戻してしまう
- **非同期に渡される処理を、呼び出しの前後で測ってはいけない。** `process_choice` は
  `execute` を渡して即座に返るので、その前後は「最中」を捉えない。状態を継続的に監視するか、
  内側のフックで測る

### 7.2 選択肢ボタン

```python
app.buttons = [{'text': '会話する', 'spec': PhaseSpec('DisplayTalkChoice', [])},
               {'text': 'リリス・アクエリア', 'spec': PhaseSpec('ConversationStartManager', ['73'])},
               {'text': '出る',     'spec': PhaseSpec('MovePhaseManager', ['20','134','7'])}]
app.to_display_buttons    # 表示中の文字列のリスト
app.display_button_map    # 表示位置 -> buttons の添字
app.choice_button_page    # ページ送りの現在ページ
app.refresh_choice_buttons(reset_page=True)
```

**`PhaseSpec(cls_name, args)` はマネージャのインスタンスではなくその作り方。** 押されると
`getattr(__main__, cls_name)(app, *args)` が組み立てられ `app.process_choice(それ, 文字列)` に渡る。
押された添字は `display_button_map` で引き直される（`ui.pressed_entry` が同じことをする）。

> `app.function_correspond_to_input` は名前に反して対応表ではなく `PhaseSpec` 1個。
> 「いま自由入力を送ったら何を呼ぶか」を保持している。

**自前のクラス名を `PhaseSpec` に書かない。** `PhaseSpec.to_dict()` が存在する＝
**ボタンはセーブに焼き込まれうる**。自前のクラス名を書くと、MOD 無しで起動したときに
`getattr(__main__, ...)` が失敗する。注入はプロセスと一緒に消えるので、これは必ず起きる。

**自前ボタンの作り方**: 無害な既存クラス（`JustSetButtonToNormalPhase`）を spec に持たせ、
押下は `InstantaleApp.on_button_press` を包んで**ボタン辞書に足した独自キー**で横取りする。
文字列ではなく印で見るのは、同じ文字列のゲーム側ボタンを巻き込まないため。MOD が無ければ
残骸のボタンは無害な動作になる。

**自前で組む `PhaseSpec` は、引数の値まで実測で確かめたものに限る。** ボタンが押された
瞬間にゲーム側で実行されるので（`getattr(__main__, cls_name)(app, *args)`）、こちらの
`try`/`except` の外側。引数を1つ間違えるとそのままゲームが落ちる。

### 7.3 選択肢を変える手順

**この3点を外すと、データは正しいのに画面が変わらない。**

1. **`process_choice` を通す。** `app.buttons` を書き換えて `refresh_choice_buttons()` を
   直接呼んでも画面は塗り替わらない。ゲーム自身は選択肢を変えるとき必ず
   `process_choice(マネージャ, 文字列)` を通す。自前のフェーズクラス（`execute(choice_text)`
   だけを持つ）を作って同じ経路に乗せる。**そのクラスは `PhaseSpec` には載せない**
   （`process_choice` はインスタンスを受け取るので載せる必要も無い）
2. **押下と同じ流れの中で差し替えない。** ゲームは押下処理の中で描画するので、その前に
   差し替えると後の描画で古い内容に戻される。`Clock.schedule_once(..., 0)` に載せれば
   次のフレーム・メインスレッドの両方が同時に片付く
3. **塗るのは HUD。** `refresh_choice_buttons` は `to_display_buttons` と
   `display_button_map` を組み直すところまで。実際に塗っているのは
   `scripts.hud.new_hud:InstanTaleHUD.update_button_texts(self, instance, value)`（Kivy の
   プロパティ監視）。**監視対象のプロパティは HUD 側にあり、`app.to_display_buttons` は
   監視対象ではない**（空にして入れ直しても dispatch されない）。`hud.update_button_texts`
   を直接呼ぶ。HUD は属性名ではなく `InstanTaleHUD` の**型**で探す

いずれも `ui.Screen.apply_buttons` / `Screen.paint` に入っている。**MOD 側で書き直さないこと。**

**画面に実際に出ている文字は `hud.buttons[i].text`**（`app.to_display_buttons` とは別物）:

```
hud={'buttons': ['沈黙の森の影を討伐せよ', 'クエストを探す', 'やめる', ''],
     'status_label': 'Atk:299(+326)...', 'send_disabled': False}
```

枠数は `len(hud.buttons)`（実測 4）で固定。自由入力の可否は
`hud.text_send_button.disabled`。**画面の状態を観測するならここを見る。**

### 7.4 待機表示（「…」のアニメーション）

ゲームは長い処理の間、こうやって操作を止めている:

| 要素 | 値 |
|---|---|
| `app.is_button_enabled` | `False` |
| `hud.buttons[i].text` | `.` → `..` → `...` の**アニメーション**（約 0.3 秒周期）。**全枠**に出る |
| `hud.text_send_button.disabled` | `True` |
| `app.text_input_disabled` | **`False` のまま**（＝これは機構ではない） |
| `app.buttons`（spec の一覧） | **触らない** — 表示だけ差し替えるので後始末が要らない |

自前の処理でも同じものを出せる（`301_` の `show_busy()` / `clear_busy()`）。

**画面の繋ぎ目を隠すのにも使える。** 会話を閉じてから次の画面を開くまでの間、
`ConversationEndManager` の終了処理が `app.buttons_backup`（会話相手の一覧）を復元するので
一瞬それが見える。ゲームが正しく元の画面へ戻しているだけなので止められないが、待機表示を
出したまま繋げば隠せる:

```
押下 → show_busy() → 会話を閉じる → in_conversation が落ちるのを待つ
     → clear_busy(restore=False) → 次の画面を開く
```

`restore=False` が要点で、「元の選択肢を塗り直さない」＝古い画面を出さない。点の
アニメーションはゲーム自身の待機表示と同じものなので、割り込みが挟まったようには見えない。

### 7.5 会話

会話画面には必ず「会話を終了する」が並び、その spec は:

```
ConversationEndManager(app, in_conversation_id, finisher, end_text)
args = ['77', 'user', '<行動: 会話を終了する>']
```

- **`args[0]` がいま話している相手の id。** 画面のボタンを読むだけで相手が分かる
  （`ConversationStartManager` を追跡する必要は無い）。仲間の label から入る経路
  （`on_member_label_press` → `process_party_member_choice`）でも会話画面になれば並ぶ
- **会話中の `app.buttons` は「会話を終了する」1個だけ。** 会話画面も施設と同じ選択肢
  リストを使うので、自前の選択肢はその手前に挿せばよい
- **会話は「状態」であって画面ではない。** 立ち絵の片付けも関係値の更新も終了処理の中に
  あるので、ボタンを別の画面に差し替えただけでは閉じられない（`app.in_conversation` が
  残ったままになり、NPC の立ち絵が移動しても付いてくる）
- 閉じるときは画面のボタンの `args` をそのまま写し、**`end_text` だけ**差し替える。
  そこは自由記述なので、事情を書けば会話の要約とライフログに残る
- 閉じ終わるまで待つ（要約で LLM が回るため最大 120 秒程度）。`app.in_conversation` が
  落ちるのを Clock で見張る

**画面の見分けは文字列ではなく spec のクラス名で**（表記や言語設定に依存しない）:

| 目印 | 意味 |
|---|---|
| `ConversationEndManager` がある | 会話画面 |
| `DisplayTalkChoice` がある | 会話相手を選べる＝施設のルートメニュー |

依頼一覧（`QuestChoiceManager` が並ぶ）にはどちらも無いので入れ子にならない。

**「行動」メニューは `app.buttons` とは別系統**（HUD 上部の info レイアウト）:

```
set_top_info_layout_conversation_button_callback:
    callbacks_left  = InstantaleApp.show_npc_item_window
    callbacks_right = InstantaleApp.toggle_to_action_in_conversation
set_top_info_layout_action_in_conversation_button_callback:
    callbacks_button_2 = InstantaleApp.toggle_from_action_in_conversation   （戻る）
    callbacks_button_3 = InstantaleApp.start_battle_with_in_conversation    （戦闘）
```

**会話フェーズを自分から起こす**には、プレイヤーが NPC を選んだのと同じ経路に乗せる:

```python
app.process_choice(ConversationStartManager(app, npc_id), npc_name)
```

`DisplayTalkChoice`（NPC 一覧）は挟まなくてよい。立ち絵・会話履歴・関係値・終了処理は
すべてゲーム本来の実装が動くので、こちらで UI を触る必要が無い。

会話開始の合図は `<行動: 話しかける>`。向きを変えたいときは
`llm_manager:conversation_starter` に渡す **messages のコピーだけ**を差し替える
（ゲームが持つ会話履歴には触らない）。

### 7.6 割り込みのタイミング

移動・クエスト終了・会話終了の後始末（テキストの流し込み・ボタンの張り替え・要約）の
最中に割り込むと噛み合わない。`ui.IDLE_SIGNALS` = `is_adding_text` / `is_button_enabled` /
`is_popup_window_opened` を Clock で見張り、手が空いてから実行する（`ui.Screen.when_idle`）。

戦闘・会話中かを見るフラグ: `in_battle` / `in_boss_battle` / `in_colosseum_battle` /
`in_conversation` / `in_free_input` / `in_action_in_conversation`。

**`in_shopping` は状態の判定に使えない。** 店の外を往復しているだけの移動でも True の
まま残る。買い物窓が開いているかは `is_popup_window_opened` で見る。**`in_battle` も経路に
よって下ろし忘れがある**（§7.10）。

> **フラグ名が意味するとおりに動いているとは限らない。** 条件に使う前に実測で裏を取ること。

### 7.7 世界のデータ構造

**現在地は `app` ではなくプレイヤーのキャラクタにぶら下がっている**（`app` 側の 97 属性に
current_facility の類は無い。`app.current_action` は行動文、`app.location_image` は背景画像パス）。

```
app.player.location      -> Facility   app, characters, choices, config, connections,
                                       description, facility_type, id, name, owner, parent_node
app.player.current_node  -> Node       facilities(dict), entrance_facility, ...
app.player.current_area  -> Area       name, descriptions, bgm, resident_npcs, size, ...
app.world.characters     -> {id: Character}    Facility.owner はこの id（str）
```

- 施設は `areas[id].nodes[nid].facilities[fid]` の**入れ子**。`initial_location` は
  `{"area": "7", "node": null, "facility": "127"}` で **`node` は null のことがある**ので、
  ノードを総当たりして探す（`ui.find_facility`）
- **`player.current_area` はエリアのオブジェクトとは限らない**（NPC 側のセーブでは `"7"` と
  いう id の文字列）。どちらでも引き当てること。エリア表も `world.areas` と決めつけず、
  `nodes` を持つものが並んだ辞書を中身で見分ける
- `Facility.characters` は**重複が入る**ことがある（`['69', '69']`）。話者を選ぶときは一意化する
- ギルドは `facility_type == 'guild'`（`ui.find_guild`）
- 実在する `facility_type`: `entrance` / `exit` / `ward` / `guild` / `inn` /
  `general_store` / `specialty_shop` / `blacksmith` / `medical_facility` /
  `administrative_office` / `underworld_office` / `colosseum` / `slave_market` /
  `location` / `dungeon_location`。うち `ward` / `location` / `entrance` / `exit` は
  主のいない通路

### 7.8 パーティ

**名簿の在り処も形も決めつけない。** セーブでは `game_variables['party']` が
`['player', '63', ...]` の id 配列だが、実行時に `app.party` から同じものが読めるとは
限らず、`list` とも限らない（`{id: Character}` の辞書のこともある）。

`302_` の `party_stores` / `pick_store` が採っている手順:

1. 候補を全部集める — `app.party` / `app.game_variables['party']` /
   `world_dict['game_variables']['party']` / `world_dict['party']` / `world.party` /
   `player.party`、加えて名前に `party` が入る属性・キーの掃引（`escaped_member_in_battle`
   のような紛らわしい配列を拾わないよう名前で絞る）
2. **中身を見て本物を選ぶ** — 名簿には必ず `'player'` が入る
3. `list` と `dict` の両方を受ける（辞書ならキーを id として読む）
4. 要素が id の文字列でも Character のインスタンスでも読む
5. **書くときは同じ id を持つ入れ物すべてから落とす**
6. 1つも見つからなければ app の持ち物を全部書き出す（`dump_census`）

関連する `game_variables`:

| キー | 意味 |
|---|---|
| `original_party` | 一時的に差し替えたときの控え。**入っている間は名簿を触らない** |
| `quest_party_accompany_backgrounds` | クエスト同行者の背景（サマライザに渡る） |
| `escaped_member_in_battle` | 戦闘から逃げたメンバー |
| `is_party_member_talk_enabled` | 仲間に話しかけられるか |

**外す処理は書かない。ゲーム自身のものを呼ぶ。**

```
InstantaleApp.remove_party_member(member_id)
InstantaleApp.get_party_leave_facility(character_instance)      -> (施設, ノード)
InstantaleApp.move_npc_to_facility(character_id, character_instance,
                                   target_facility, target_node=None, register_facility=True)
```

- **名簿を実際に書き換えているのは `remove_party_member` の中。** 外す/外さないを決めたい
  なら、この呼び出しを通す/通さないだけでよく、名簿に指一本触れる必要は無い
- `get_party_leave_facility` は **`(施設, ノード)` のタプル**を返す。そのまま
  `move_npc_to_facility` に渡すと `'tuple' object has no attribute 'characters'` で落ちる
  （**別れること自体は成功して置き直しだけが失敗する**ので気付きにくい）。ほどいて
  `target_facility` / `target_node` に入れる。中身が何なのかは解釈しない
- **`remove_party_member` 自身は NPC を動かさない。** 置き直すのは呼び出し元で、
  removal の**後**
- **置き場所を決めずに外さない。** 置き先が引けない土地（ダンジョン等）で外すと、その NPC
  は世界のどこにも居なくなる。逆に、**本当に外れた相手の置き直しを止めてもいけない**

**クエストクリアの解散**:

```
add_text('パーティは帰還した...') → 報酬・才能
remove_party_member('71' '魔導師・リアナ')
  from QuestEndManager.method_1 (instantale.py:6602)
  <- QuestEndManager.execute (instantale.py:6635) <- run (threading.py:953)
add_text('魔導師・リアナはパーティから離脱した。')
```

- `QuestEndManager`（`__init__@6508` / `method_1@6511` / `execute@6634`。解散は 6602 行）。
  **別スレッド**で走る。放棄側は `QuestRetireManager`（`method_1@6642` / `execute@6713`）
- **帰還は解散より先**なので、解散の時点で `player.current_area` はもう町。「いま居る町」を
  その場で引いてよい
- **ゲーム側の置き先は `initial_location`（雇用された場所）**

**解散の中かどうかはコードオブジェクトの同一性で見る。**

- **段数で数えない** — `@ctx.wrap` の層が1段挟まる。`frames.caller` はファイル名で
  `runtime/` 配下を飛ばす
- **関数名でも足りない** — `method_1` / `execute` は12個のマネージャが持つ名前。
  `frames.owner_of` が持ち主クラスを名指しする
- 毎回の判定を軽くしたいなら `QuestEndManager.method_1` / `.execute` の `__code__` を先に
  引いておき、スタックの `f_code` と突き合わせる（辞書引き1回）
- **`move_npc_to_facility` ではスタックを見ない。** NPC の日常の移動でも呼ばれるので、
  「いま解散した相手か」を辞書で引くだけにする

### 7.9 クエスト

**格納場所は2つある。書くときは必ず両方。**

```
app.world.quests          {id: Quest インスタンス}   ゲームが遊ぶときに読む
app.world_dict['quests']  {id: dict}                 セーブに出るのはこちら
```

片方だけ直すと画面の表示と保存内容がずれる。新規 id の検出も**両者の合併**を取る。

**掲示板（`DisplayQuestChoice`）のボタン構成**:

```python
app.buttons = ['沈黙の森の影を討伐せよ' -> PhaseSpec('QuestChoiceManager', ('settlement_quest', '2')),
               'クエストを探す'          -> PhaseSpec('QuestSearchManager', ()),
               'やめる'                 -> PhaseSpec('JustSetButtonToNormalPhase', ())]
```

- **`QuestChoiceManager(app, quest_type, quest_id)` の `quest_type` は `'settlement_quest'`。**
  クエスト辞書の `quest_type` フィールド（`'normal_quest'` など）**とは別の語彙**で、
  セーブの値をそのまま渡すと `KeyError` で落ちる。`world.quests` に対して通るのは
  `'settlement_quest'` だけ（他の候補は全て `story_quests` 側の分岐に落ちる）
- **ゲーム自身の依頼生成の入口は `QuestSearchManager`**（「クエストを探す」）。
  `DisplayQuestChoice.generate_random_quest()` は「いまこの土地に依頼を1件作って登録する」
  内側の入口で、クエストエリアの生成・id の採番・登録まで面倒を見る。内容に手を入れたいなら
  さらに内側の `llm_manager_world_generate:random_quest_generator` の引数
  （`area_description`）に足すだけでよい — 出力スキーマは1バイトも変わらない
- **一覧はゲームに組ませるのが安全。** `DisplayQuestChoice` を `process_choice` で開けば、
  一覧の組み立ても受注画面への受け渡しもゲームがやる＝語彙を知らなくてよい
- 受注できる依頼の絞り込みは `neighboring_settlement_id == 現在エリアの id` かつ
  `config['status'] == 'incomplete'`（**依頼は集落ごとに3件ずつこのキーで束ねられている**）。
  ゲーム自身の `get_quest_difficulties(area, world)` と突き合わせられる
- `QuestStructure`（`random_quest_generator` の出力）は `quest_title` / `client_name` /
  `request_summary` / `client_statement` / `area` / `events` / `enemies` / `boss`。
  ゲームはこれに `difficulty` / `neighboring_settlement_id` / `id` / `quest_type` /
  `config` / `quest_area_id` を足して保存する
- **元からある依頼の `client_name` は実在 NPC と結び付いていない**（世界生成時に付いた名前）。
  NPC 単位で依頼を辿りたいなら MOD 側で控えを持つしかない
- **クエスト辞書に独自キーを足さない。** セーブに焼かれるうえ、再読み込み後に `Quest`
  インスタンスがそのキーを持つ保証が無い。控えは `out/` に別ファイルで持つ

#### 進行ループ（2026-07-28、1クエストを頭から終わりまで実測）

```
DisplayQuestChoice                       get_active_quest_count() -> 5
  → QuestChoiceManager(app, 'settlement_quest', '28')
  → quest_acceptance_choice              '受ける' = QuestStartManager(app, 'settlement_quest', '28')
  → QuestStartManager.start_quest()      → PhaseSpec('QuestPhaseManager', [])
       app.current_quest_data = <Quest object>
  → BattlePhaseManager / LootPhaseManager                     戦闘とその戦利品
  → QuestEventManager(app, event_name, enemies_info, event_turn)   フィールドイベント
  → QuestEncounterFinalBoss(app, [[boss_name]])                ラスボスとの邂逅
  → QuestEndManager(app)                                       ★完了。**引数ゼロ**
  → buttons ['帰還する', '漁る']
```

毎ターンの分岐は `QuestPhaseManager.quest_referee_phase` →
`llm_manager:quest_referee_with_free_action` が決める。その戻り値
（`QuestTurnStructure`）の `game_master_statement.turn_resolution` が上のどれに進むかで、
`move_to` に移動先が入る。

#### referee のモデルは毎ターン組み直され、候補が尽きたものは作られない

`203_` の記録（`probes.log`）より。**同じ1クエスト中の推移**:

| 時刻 | `Battle.enemies` | `FieldEvent.event_name` | `EncounterFinalBoss.enemies` |
|---|---|---|---|
| 09:48:05（1ターン目） | `Literal[7]` | `Literal[2]` | `Literal[1]` |
| 09:51:27 | `Literal[6]` | `Literal[2]` | `Literal[1]` |
| 09:56:44 | `Literal[1]` | `Literal[1]` | `Literal[1]` |
| 09:59:04 | `Literal[1]` | **モデルごと消滅** | `Literal[1]` |

- **`Literal` の中身は「残り」**（倒した敵・消費したイベントは消える）。クエスト辞書の
  `enemies` 全体ではない
- **候補が 0 件になったモデルはゲームが union に入れない**（`FieldEvent` で実証）。
  つまり空 `Literal[]` を避ける分岐は存在する。ただし 0 件を観測できたのは `FieldEvent`
  だけで、`Battle` / `EncounterFinalBoss` が 0 件になる場合は未観測
- **`ReturnAfterCompletion` は1ターン目から毎ターン作られている。** ボス健在でも union に
  居る。したがって**「攻略しないと帰還できない」はスキーマではなくプロンプトの縛り**
  （referee の system に `return_after_completion: クエストを攻略した後にのみ実行可能` と
  書かれている）。同様に「ラスボス撃破か撤退でのみ終了」「戦闘ベースのRPGなので
  field_event よりも battle が多くなるように」も全て地の文の指示

**この性質のおかげで、討伐以外のクエストはプロンプトの差し替えだけで成立する**
（`305_mini_quest`）。ゲーム側のコードにもセーブにも触る必要が無い。

### 7.10 戦闘・フラグ

**戦闘終了マネージャは3つあり、経路によって挙動が違う。**

| マネージャ | 入口 | `end_phase` 完了時の `in_battle` |
|---|---|---|
| `BattleEndManager` | クエスト中のエンカウントなど通常の戦闘 | **0**（ゲーム自身が下ろす） |
| `BattleEndInFreeAction` | 自由入力・**会話から入った戦闘** | **1**（下ろし忘れ） |
| `BattleEndInColosseum` | コロシアム | — |

**`in_battle` はセーブに入り、ロード時の分岐に使われる**（`instantale.py:1458` が戦闘BGM、
`:1460` がエリアBGM）。残ったまま保存すると次のロードが戦闘BGMで始まる。MOD 側でも
「戦闘中は出さない」条件に使われるので、残骸があるとイベントが出なくなる。
**残骸かどうかは `app.current_enemy_dict` が空かで見分けられる。**

`in_boss_battle` / `in_colosseum_battle` は 1→0 の遷移を観測できていない。

### 7.11 BGM

```
play_music_from_src(app, src)   app.music に差し替えて再生
stop_music(app)                 app.music を止める
```

**`app.music` が「今鳴っている曲」の唯一の取っ手で、ここを失った曲は誰にも止められなくなる**
（プロセスが終わるまで残る。チャンネルは8本しかないので、埋まると効果音も鳴らせなくなる）。
`BattleEndInFreeAction` の復帰呼び出しは app ではなく別のオブジェクトを渡してくるので、
**`app` を受け取る関数を包むときは渡されたものが本当に app か確かめること**
（`getattr(x, k, "<missing>")` で「属性が無い」を `None` と区別して記録する）。

**音の状態は自前の帳簿ではなく pygame に聞く。** `Sound.get_num_channels()` と
`pygame.mixer.Channel(i)` で「今どの音が実際に鳴っているか」が分かる。曲は再生のたびに
`Sound(パス)` で作られるので、`SoundManager.sounds`（起動時に読む15種）と `play_sound*` で
鳴らされたものを除外集合にすれば、**残ったループ音は曲**と言える。

止められなくなった曲は、**止めずに `app.music` へ入れ直す**と音が途切れず、しかも以後
ゲーム自身の `stop_music` が効くようになる。

**エリアBGMのパスはエリア生成時に確定し、セーブに焼き込まれる。**

```
areas["7"]["bgm"] = "Assets/sounds/musics/town/solemn/Ambient 7 Loop.mp3"
```

フォルダ2段が判定結果（`town|village|city|dungeons|battle` ＝ `area["size"]`、その下が
雰囲気 `calm`/`eerie`/`majestic`/…）で、末尾が曲。

- **フォルダの決定権は `area["size"]` にあり、保存済みパスから取ってはいけない**
  （誤ったフォルダに入ったエリアが永久にそのフォルダ内で再配分され続ける）
- **表記揺れ: エリアは `dungeon`（単数）、フォルダは `dungeons`（複数）**
- `musics/` 直下の単発曲を指しているエリアは意図的な指定とみなして触らない
- どのフックで `bgm` が確定するかはコンパイル済みのため特定できない。候補は
  `save_area_json:write_area_data_to_world_dict` / `save_area_json:generate_quest_area` /
  `save_world_json:write_obfuscated_json_file`。**どれが何回発火しても結果が変わらない
  書き方にして、全部に仕掛ける**のが実務的

**乱数は MOD 専用の `random.Random` を使う。** グローバルの `random` から引くとゲーム自身の
乱数列がずれる。

### 7.12 LLM 経路とプロンプト

```
llama_cpp_runtime_completion:LlamaCppClient.chat                             上流
llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template             messages
llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry   payload
scripts.llm.request_llm_inference_llama_cpp_completion:send_request*         リクエスト
scripts.llm.llm_manager:*                                                    マネージャ群
```

- **ストリーミング経路は `_post_with_model_loading_retry` を通らない。** `prompt` と
  `json_schema` が揃う唯一の地点はそこだが、実際に流れるのは `chat` 側。**位置が確信
  できないときは、判定条件を保ったまま複数箇所に仕掛ける** — どこで何回発火しても結果が
  同じになる書き方にしておけば、二重に効いても壊れない
- `send_request_with_no_structure(manager_name, messages, max_tokens=, timeout=)` は
  **`str` を返す**（`output_data/` の記録が `{"text": ...}` なのは保存側の形式）
- **`output_data/` の記録は `LlamaCppClient.chat` より上流で取られる。**
  保存は `send_request*` の中（`save_output_log`）なので、**`chat` を包んで書き換えた
  内容は記録に一切映らない**。2026-07-28 に `305_` の書き換えを「効いていない」と
  誤判定しかけた。同じ記録の中で、実機で効いていることが確定している `105_` の
  COMPACT 後も `'$defs'` が 64件中39件そのまま残っている ― これが上流である証拠。
  **`chat` に仕掛けた MOD の判定は、その MOD 自身のログで行うこと**
- `manager_name` を自前の名前にすると、自前のプロンプトも
  `output_data/<世界>/<PC>/<manager_name>/N.json` に残る
- **`quest_event_log` はリストではなく文字列。** 区切り（「〈プレイヤーの入力〉」）で割る
- **`messages` の重複は完全一致・隣接で現れる。** テキスト走査は不要で `(role, content)` の
  比較で落とせる
- **ゲームは `json_schema`（grammar の実体）と同じスキーマを、Python dict の repr として
  プロンプト本文にも埋め込む。** 構造は grammar がトークン単位で強制するので、本文側に
  必要なのはフィールド名・enum 候補・参照先の型名だけ:

  ```
  元:   {'$defs': {'Location': {'properties': {'name': {'title': 'Name', 'type': ...
  後:   Location: name, kind:∈{shop,inn}
        Area: name, locations:Location[], atomosphere:∈{tense,normal}, note?
  ```

- **`ast.literal_eval` は使えない。** 式1個しか受け取れず**終端位置を返さない**ので、
  プロンプトの途中から読み始めて置換範囲を決められない。再帰下降パーサが要る
  （`True/False/None` と `true/false/null`、両クォート、末尾カンマ、タプル表記）

**クエスト1件に関わるマネージャ**（`output_data/` の実データで確認）:

| マネージャ | 役割 | 討伐固定の文言 |
|---|---|---|
| `random_quest_generator` / `settlement_quest_generator` | クエスト構造の生成 | **有**（「【討伐】…を生成」「normal2-3種、miniboss1種、boss1種を必ず設定」） |
| `quest_starter` / `quest_starter_with_party` | 開始ナレーション＋初期選択肢 | 無（構造を読んで描写するだけ） |
| `quest_referee_with_free_action` / `quest_referee` | 毎ターンの進行判定 | **有**（完了条件・battle 優先・ラスボス強制） |
| `field_event_evaluator` | イベント中の入力を「確定」か「確率判定」に振り分け、説得力を1〜10で採点 | 無 |
| `quest_referee_event_resolve` / `_event_rewrite` | イベントの結末と効果（damage/heal/get_item/status/start_battle/no_effect） | 無 |
| `quest_summarizer` ほか | 帰還後の要約 | 無 |

つまり**討伐前提が書かれているのは生成と進行判定の2箇所だけ**で、イベント処理も
描写もクエスト種別に依存していない。

**サイドカー**（`LlamaCppSidecar`）は `__init__` / `start` / `_kill_existing` /
`_find_free_port` / `_wait_for_ready` を持つ。`start` の `additional_params` はリストなので
`--parallel 1` の追加は容易。**InstantaleLLMProxy と併用する場合、多重起動抑止だけは
所有者調停が競合する**（プロキシ側を `singleton_enabled=0` にする）。DEDUP / COMPACT /
EVENTLOG は二重に適用しても結果が変わらないので併用してよく、**プロキシ側のログに出たら MOD の取りこぼし**という
検出器になる。

### 7.13 インベントリのグリッド

所持品・売買画面（twin inventory）は `scripts.hud.new_hud:InventoryGrid`。

```
InventoryGrid   cols=4  rows=6  len(slots)=24  size=[259, 389]  spacing=[1, 1]
                situation=None（所持品） / 'shop'（売買）
アイテム        width_slots / height_slots / size=[64,64]（1マス）/ [129,129]（2x2）
                current_slots=[17, 21, 18, 22]   ← 占有マスは添字の配列で持つ
```

**`grid_x` / `grid_y` / `slot_size` はアイテムの属性としては存在しない**（`<missing>`）。
位置は `current_slots` の添字とピクセル座標で持っている。

ゲーム自身が持っている配置の道具:

| | |
|---|---|
| `is_valid_placement(...)` | そこに置けるか |
| `find_placement_position(w, h)` | 空きを探す |
| `place_new_item(item)` | 新しく置く（店の在庫を初めて並べるときの経路） |
| `place_existing_item(item, x, y)` | 既存の位置を復元する。**置けるか確かめずに `occupy_slots` を呼ぶ** |
| `occupy_slots(...)` | 占有マスを埋める。範囲外で `IndexError` |
| `item.clear_current_slots()` | 途中まで埋めたマスの後始末 |

**復元位置は必ずしも収まらない**（画面ごとにグリッドの寸法が違いうる）。座標を計算し直す
のではなく、**はみ出したら `find_placement_position` → `place_new_item` に流す**のが安全。
`toggle_twin_inventory_visibility` は Kivy の property dispatch → Clock コールバックの中で
走るので、ここで例外が出るとアプリのループまで抜けてゲームごと落ちる。

### 7.14 アイテム詳細ボックス

ホバーで出る `ItemDetailBox`（window=2560x1387 のときの実測）:

```
ItemDetailBox      size=[333, 500]  size_hint=(None, None)      ← 箱ごと固定
  background       size_hint=(1, 1)
  name_label       height=50   text_size=[316,  50]  pos_hint={'center_x':.5,'top':0.95}
  attributes_label height=225  text_size=[316, 225]  pos_hint={'center_x':.5,'top':0.85}
  desc_label       height=150  text_size=[300, 150]  pos_hint={'center_x':.5,'top':0.40}
                   font_size=27  halign=center  valign=top  max_lines=0
```

- **`max_lines=0`（無制限）なので、切れているのは行数制限ではなく `text_size` の高さ**。
  300px 幅・`font_size=27` で半角24文字/行、150px で3行 = 72文字までしか描かれない
- 子の位置は `pos_hint` の `top` 分数で、**箱の高さに追従する**。箱を伸ばせば中身は自分で
  並び直すので、座標を1つずつ計算し直す必要は無い
- 分数を実寸に直すと `上余白25 + 50 + 225 + 150 + 下余白50 = 500`。ラベル間の隙間は 0 で、
  見た目の余白は**ラベルが自分の高さの中に持っている**
- **箱はホバーのたびに作り直される。** `update_content` が呼ばれる時点で箱の `pos` は
  入っているが、**子はまだ一度もレイアウトされていない**（`name_label.pos` は箱の左下原点の
  まま）。**レイアウト前の絶対座標から設計を読んではいけない** — 余白は `pos_hint` の分数と
  箱の高さから求める
- **箱は上端を固定して置かれている**（上端が所持品グリッドの上端と一致する）。高さを変える
  ときは**上端を保って下へ伸ばす**（`y` を据え置くと上へ伸びて位置が浮く）
- 箱は `pos_hint` を持たず、位置は `update_content` の**後で**誰かが直接入れている
  （写し取った時点で `opacity=0`）。伸ばした箱を画面内に収めたいなら次のフレームで行う

**文字が要求する高さの測り方**: `text_size` を `(元の幅, None)` にして `texture_update()` を
呼ぶと、折り返した結果が `texture_size[1]` に出る。幅はこちらで決めず、ゲームの値のまま使う。

### 7.15 キャラクタ名はそのままファイルパスになる

```
worlds/<世界>/characters/<キャラクタ名>/
  実データ: 「銀鱗」のジーン / イリス・ステラ (Iris Stella)
```

**名前に Windows のパスに使えない文字（`< > : " / \ | ? *`）が入ると `os.makedirs` が落ちる。**
LLM が生成した名前に引用符が混じる経路が実在する（`魔導演習人形「プロト・レガリア"`）。

- **バックグラウンドスレッドで起きるのでゲームは落ちない。** 画像が生成されないまま無言で
  失敗し、その NPC に関わるたび再発する
- 名前からパスを組む箇所は5つある（`generate_and_write_character_detail` /
  `generate_character_image` / `generate_character_image_from_enemy` /
  `generate_enemy_image_from_character` / `delete_world_character_images`）。**個別に消毒すると
  書き込みと削除でずれて別の不整合を生む**
- **名前の唯一の入口は `scripts.characters:Character.__init__`**（LLM生成・プリセット・
  プレイヤー・セーブからのロードが全部ここを通る）。ここで名前そのものを正せば5箇所は
  今までどおりで一致する。引数ではなく `self.name`（`__init__` を抜けた後）を正すと、
  名前をどの引数から組み立てているかを知らずに済む
- Windows は**末尾の空白とピリオドを黙って切る**ので、パスに使うなら先に落としておく
- **世界名（`worlds/<世界>/`）の入口は未調査。** 同じ壊れ方をしうる

### 7.16 セーブ

```python
plaintext  = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
cipher[i]  = plaintext[i] ^ b"Instantale_Save_Key_2026"[i % 24]
```

`savedata.json` も同じ方式。セーブを書き換えるツールは、**書き込み前に毎回
復号→再暗号化のラウンドトリップを検査し、一致しなければ拒否する**こと。

---

## 8. 落とし穴（ルール一覧）

| ルール | 理由 |
|---|---|
| 自前のクラス名を `PhaseSpec` に書かない | セーブに焼かれ、MOD 無しの起動で `getattr` が失敗する |
| 自前で組む spec は引数の値まで実測で確かめる | 押下時にゲーム側で実行される＝こちらの `try` の外。1つ間違えば落ちる |
| 値の語彙を推測するくらいなら、語彙を知らずに済む経路を探す | ゲーム自身の入口（`DisplayQuestChoice` / `ConversationStartManager` / `remove_party_member`）に渡せば引数の意味を知らなくてよい |
| 選択肢の差し替えは次のフレーム＋`hud.update_button_texts` | 押下と同じ流れで塗ると古い画面に戻る。`refresh_choice_buttons` だけでは塗り替わらない |
| 会話は閉じてから画面を変える。`end_text` に理由を書く | 閉じないと立ち絵が付いてくる。`end_text` は要約とライフログに残る |
| 後始末の最中に割り込まない | `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を見る |
| 長い処理の間は待機表示を出す | 出さないと操作が効くように見える。ゲーム自身と同じ形（§7.4）にすれば違和感が無い |
| UI と pygame は Clock（メインスレッド）から触る | `execute` は別スレッドで走る |
| 非同期に渡される処理を呼び出しの前後で測らない | `process_choice` は即座に返る。状態を継続監視するか内側で測る |
| 呼び出し元を段数で数えない | `@ctx.wrap` の層が挟まる。ファイル名で飛ばし、`frames.owner_of` で持ち主クラスを名指しする |
| 計測に `hasattr` を使わない | `__getattr__` トリップワイヤを自己発火させる。`frames.attr` を使う |
| `app` を受け取る関数を包むときは、渡されたものが app か確かめる | 別のオブジェクトが渡ってくる経路がある。`<missing>` と `None` は区別して記録する |
| セーブの形＝実行時の形と決めつけない | 名簿・現在地・戻り値、いずれも実行時は別の形を取りうる |
| フラグ名を信用しない | `in_shopping` は買い物中でなくても True のまま。条件に使う前に実測する |
| 状態は自前の帳簿ではなくランタイムに聞く | 音は `get_num_channels()`、画面は `hud.buttons[i].text`、名簿は中身を見る |
| 寸法・座標を発明しない | グリッドの実寸もレイアウトの仕様も読めない。ゲームが決めた値を最低値にして、足りないぶんだけ動かす |
| レイアウト前の絶対座標から設計を読まない | 作り直された直後のウィジェットは子がまだ配置されていない。`pos_hint` の分数と親のサイズから求める |
| 名前や ID がそのままファイルパスになる箇所を疑う | キャラクタ名がディレクトリ名になる。パスに使えない文字が入ると無言で失敗する |
| 同じ値を複数箇所で加工しない。入口ひとつで正す | 5箇所で個別に消毒すると、書き込みと削除でずれて別の不整合を生む |
| 属性は名前で推測せず `vars()` を全部出す | 名前から探すと空振りする |
| 独自キーをゲームのデータ構造に足さない | セーブに焼かれ、再読み込み後に残る保証も無い |
| 乱数は MOD 専用の `random.Random` | グローバルを使うとゲーム自身の乱数列がずれる |
| 観測できた範囲でしか直さない | 例: `in_battle` は 1→0 を観測できたので下ろす、`in_boss_battle` は観測できないので記録だけ |
| 何度実行しても結果が変わらないように書く | 当て直し（§3.4）と再注入で `apply()` は何度も走る。フックが複数発火しても壊れない形にしておけば、フック選択が致命的でなくなる |
| 同じ発見を2箇所に書かない | 実機で確かめた事実は `ui.py` / `frames.py` へ。MOD には設計判断だけ |

---

## 9. 実装例カタログ

新しい MOD を書くとき、近い手口を使っている既存 MOD を読むのが速い。

| 手口 | 見る MOD |
|---|---|
| ゲーム自身のヘルパを当てるだけの修正 | `101_`（`clamp_npc_difficulty_value`）、`107_`（通常経路がやっていることを抜けている経路に適用）、`108_`（`find_placement_position` → `place_new_item`） |
| ゲームが決めた値を最低値にして、足りない分だけ広げる | `109_`（アイテム詳細ボックスの高さ・幅） |
| 入口ひとつを直して下流の5箇所を一致させる | `110_`（`Character.__init__` で名前を正す） |
| 既にセーブに焼かれた残骸を注入時・ロード時に掃除する | `107_`（`in_battle`）、`110_`（不正な名前） |
| 例外を条件付きで握り潰す | `100_`（`hWnd=None` のときだけ。それ以外は再送出） |
| 関数の引数を書き換える（出力の形は変えない） | `103_`（`quest_event_log`）、`105_`（`messages`）、`301_`（`area_description` に会話を添える） |
| ゲームのプロンプトの前提そのものを差し替える | `305_`（討伐前提の8つの文を実データで裏を取ってから置換。**1つでも当たらなければ丸ごと諦める**） |
| 判定は全メッセージを繋いで、書き換えは各メッセージに | `305_`（進行判定は1文目が system・クエスト名が user と分かれている） |
| どのフックが効くか分からないので全部に仕掛ける（重複しても平気な書き方で） | `104_`（BGM）、`105_`（`chat` と `payload`） |
| ランタイムに現在の状態を聞いて後始末する | `106_`（pygame のチャンネル） |
| 自前の選択肢ボタンを足して押下を横取りする | `301_` / `302_` / `305_`（`on_button_press` + 独自キー） |
| ゲーム本来のフェーズを自分から起こす | `300_`（`ConversationStartManager`）、`301_` / `305_`（`DisplayQuestChoice`） |
| 会話を正しく閉じてから次へ進む | `301_` / `302_`（`ui.Screen.end_conversation`） |
| 待機表示で画面の繋ぎ目を隠す | `301_`（`show_busy` / `clear_busy(restore=False)`） |
| 手が空くのを待ってから実行する | `300_` / `303_`（`ui.Screen.when_idle`） |
| ゲームの処理を止めず「結果の置き先」だけ変える | `303_`（3層で置き先を差し替える） |
| ゲームの処理そのものを起こさせない | `304_`（`remove_party_member` を通さず、置き直しと文言も控えで見分けて抑える） |
| 在り処が不明なデータを中身で見分ける | `302_`（`party_stores` / `pick_store` / `dump_census`） |
| 読み取り専用で経路を特定する | `205_` / `206_` / `207_`（計測は修正より後＝外側に置く） |
| `__getattr__` トリップワイヤ | `201_` |
| 20Hz で画面状態の変化だけ拾う | `206_`（waitstate watcher） |

---

## 10. 調査手法

**純粋関数は総当たりで定義域を割り出す。** 副作用の無い参照関数なら、稼働中のプロセスで
直接呼んで有効域を特定できる（`get_npc_employ_price` は `0..150` を走査して `0..76` と判明）。
引数の語彙も同じ手で確定できる（実在 id を渡して例外の出ない値を探す＝
`QuestChoiceManager` の `'settlement_quest'`）。再現を待つより速い。

**ゲーム自身のヘルパを探す。** 値を発明せずに済む。`targets.txt` を `clamp` / `max` /
`validate` / `generate` / `get_` などで検索する価値がある。修正が「ゲーム自身が別の経路で
やっていることを、抜けている経路にも適用するだけ」に収まれば、バランスを勝手に作らずに済む。

**ゲーム自身が作った `PhaseSpec` を読む。** コンストラクタが実際に受け取っている引数が
そのまま入っているので、語彙を推測しなくてよい。

**属性エラーは `__getattr__` トリップワイヤで捕まえる。** Python は通常のルックアップが
失敗した後にのみ `__getattr__` を呼ぶので、それはクラッシュの瞬間そのもの。挙動を変えずに
（常に標準メッセージで `AttributeError` を送出）読み手のフレーム・行番号・ローカル変数が取れる。

**dict は値ではなくキーとキーの型を記録する**（`frames.repr_value`）。`KeyError` の原因究明で効く。

**状態は変化だけを高頻度で拾う。** 20Hz で見て変わったときだけ書けば、量を増やさずに
アニメーションや非同期処理の途中経過が取れる（`206_` の waitstate watcher）。

**プロンプト関係は `output_data/` で実データ検証できる。** ゲーム自身が LLM へ投げた
`messages` をそのまま保存している（`request_llm_inference_llama_cpp_completion:save_output_log`）。
12,067 件・66 マネージャ種。プロンプトを触る MOD は、ゲームを起動せずに全件へオフラインで
通してから注入できる。

**症状の側から判定条件を書く。** どの経路が壊しているかを突き止めなくても直せることがある
（鳴っている音を pygame に聞く、`quest_type` の語彙を知らずに済ませる）。

**低頻度バグは再現を待たない。** 正常呼び出しからデータを取る計測を先に設計する。

**オフライン検証は偽の app / `PhaseSpec` / マネージャ / Clock で組む。** 偽の
`on_button_press` は本物と同じく `getattr(__main__, cls_name)(app, *args)` を組んで
`process_choice` に渡すこと（自前ボタンに無害な spec を持たせる意味がそこで確かめられる）。
スタックを見て判定する MOD は、**テストも本番と同じ呼び出し元から呼ぶ形**にする。

---

## 11. 制限

- 注入は**ゲーム起動後**なので、import 時点で走るコードにはパッチできない。必要になったら
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
