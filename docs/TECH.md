# TECH — MOD 開発リファレンス

Instantale（Epic版 / Nuitka standalone / CPython 3.10）に外部から Python を注入し、
実行中のゲームを monkeypatch する仕組み。**これから MOD を書く人のための資料**。

- **Instantale 自身の構造・語彙・作法は [GAME.md](GAME.md)**（パッチ対象の見つけ方、
  選択肢ボタン、会話、パーティ、クエスト、BGM、LLM 経路 …）
- 遊ぶだけなら [README.md](README.md)
- 各 MOD の検証状況・未確認項目・実測ログは [VERIFICATION.md](VERIFICATION.md)
- 本書は**事実とルール**。個々の MOD の設計判断はその MOD の docstring に、実機で確かめた
  「ゲームがどう動いているか」は `ui.py` / `frames.py` と GAME.md に置く

GAME.md と分けているのは、読む理由が違うから。本書は**このローダで MOD をどう書くか**
（他のゲームにも通じる話）で、あちらは**Instantale が何をしているか**（このゲーム限定の
事実）。ゲームが更新されて食い違うのはあちら側だけなので、疑う場所が1つに寄る。

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
`runtime/mods/` の各フォルダを `load_order.json` の順に適用する。

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
    frames.py     フレームローカル採取・値の要約・呼び出し元の特定
    ui.py         選択肢 / 画面の塗り替え / 会話の閉じ方 / idle待ち / 施設の引き当て
    recon.py      実行時リコン（モジュール構造ダンプ）
runtime/mods/     MOD 本体（1バグ・1機能 = 1フォルダ。入口は mod.json が名指し）
runtime/mods/load_order.json  適用順（"order"）と無効一覧（"disabled"）
settings/         利用者が変えたものだけ（無くてよい）
                  mod_settings.json … MOD の設定 / gui.json … ゲームの場所・窓の位置
out/              ログ・リコン成果物・status.json（最後の boot の結果）
tools/            上記に加え、オフライン検証・セーブ操作（ゲーム不要）
docs/             README.md / TECH.md / GAME.md / VERIFICATION.md
```

**探索と適用順は `discover()` が1箇所で決める。** ローダ・GUI・静的検査の3者が同じ関数を
呼ぶ。以前はこの規則（`_` で始まるフォルダを除く / `mod.json` を持つものだけ / 未記載は
末尾 / `disabled` の意味）が3箇所に書き写されていて、片方だけ直すと**GUI の一覧と実際の
適用順がずれた**。

```python
found = instantale_modloader.discover()      # ゲームの中でも外でも同じ結果
found["order"]      # 有効な MOD。適用順（依存の制約も解決済み）
found["listed"]     # 一覧に出す順。無効なものも宣言された位置に含む
found["manifests"]  # 名乗り・api・settings・依存（MOD のコードは import しない）
found["problems"]   # 宣言と実体のずれ。人が読む行
```

**注入のタイミング。** `tools/watcher.py` は新しい pid に対し、(1) `Py_IsInitialized` をリモート
スレッドで直接呼んでインタプリタ初期化を確認し、(2) 可視ウィンドウの出現を待つ。後者は
Kivy が立ち上がり `__main__` の実行が終わった合図。これより早く注入してもパッチ対象が
まだ存在しない。

**ログの世代管理。** ログは全て「開く→追記→閉じる」で書かれるので、何もしなければ
プレイをまたいで積み上がる（実測で `events.log` / `quest_flow.log` が数MB）。
`tools/logrotate.py` が**注入の直前に** `out/` 直下の `*.log` を `名前.log.1` へ送り、本体を
空から始める（`KEEP_GENERATIONS` 世代ぶん保持、既定 1）。切り替えは
`--no-log-rotate` → 環境変数 `INSTANTALE_LOG_ROTATE` → `ROTATE_LOGS` の順に優先。

入れ替えを**ゲームプロセスの中（`boot()`）でやらない**理由が2つある。1つは `boot()` が
自分で `modloader.log` に書いている最中に走ること。もう1つは遅延設置の当て直し（§3.4）
でも `boot()` が呼ばれるため、1回のプレイの記録が途中で分断されること。注入は世代の
境目そのものなので、注入する側で1回だけ行えば両方とも起きない。対象は `out/` 直下の
`*.log` だけで、`out/test/` `out/recon/` と `quest_clients.json` のような状態ファイルには
触らない。

---

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
python tools/injector.py
python tools/injector.py --dry-run   # アドレス解決だけ。何も書き込まない
python tools/injector.py --unload    # 当てたパッチを剥がす（§3.10）

# 5. 結果を読む（注入が成功したかと、MOD が入ったかは別の話）
type out\status.json                 # 適用結果・台帳・効いている設定
```

**編集ループは「MOD を編集 → `python tools/injector.py`」。** `boot()` が
`instantale_modloader` を `sys.modules` から落として再 import するので、そのまま反映される
（層は積み上がらない。§3.5）。

> **注入はプロセスと一緒に消える。ゲームを起動するたびに注入し直すこと。**

`on_ready` に預けた1回きりの初期化（§3.6）は**注入し直しても走らない**。その初期化自体を
直しているときは `ctx.on_ready(fn, force=True)` を一時的に使うか、`reset_once("300_")` で
印を落とす。どちらも開発中の逃げ道で、配布する MOD に `force=True` を書いてはいけない。

**`tools/check_mods.py` は必ず通す。** `compileall` は構文しか見ないが、実際にゲームを
落とすのは構文として正しいコードのほう —「`@ctx.wrap` の対象名と、飾っている関数の引数の
並びが食い違っている」類は静的に捕まえられる。`@ctx.wrap` が飾る関数の第1引数は `orig`、
メソッド対象なら第2引数は `self`。

同じ考えで、**宣言と実体のずれ**もここで捕まる。`"entry"` が指すファイルの不在、扱えない
`"api"`、`load_order.json` との食い違い、`"after"` / `"before"` の循環、そして
**`"settings"` の既定値がコード側の定数とずれている**こと（§3.8）。

| | |
|---|---|
| `MISMATCH` | 直すべきもの。終了コード 1 |
| `note` | 表示だけの項目の欠落（`name` などは仕様では任意）。終了コード 0 |
| `--strict` | `note` も失敗として数える。同梱 MOD はこちらを通す |

**ゲーム側は Python 3.10。** 3.11 以降の構文を使わないこと（この環境の python は 3.13 しか
無いので `compileall` は 3.10 互換を保証しない）。

**`.bat` は ASCII のみ。** `.bat` はその時のコンソールのコードページで読まれるため、
日本語を入れると環境によって解析が壊れる。

**ツールから MOD を読むときは番号を書かない。** `tools/` の各スクリプトは
`find_mod("_balance_area_bgm.py")` のように番号を除いた名前で引く。分類を見直して番号を
振り直しても壊れないようにするため。

---

---

## 3. MOD の書き方

### 3.1 最小の形

**1つの MOD = 1つのフォルダ**で、`mod.json` を持つものが MOD:

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

**フォルダ名にもファイル名にも決まりは無い。** 入口は `mod.json` が名指しする:

```json
{
  "entry": "timings.py",
  "name":        {"en": "Timings KeyError fix", "ja": "timings 欠落の修正"},
  "description": {"en": "Swallows the KeyError ...", "ja": "..."},
  "version": "1",
  "author": "R01/Flossian"
}
```

探索は**この1階層だけ**で、再帰しない。深く潜ると MOD の中の補助モジュール
（上の `prompts.py`）まで MOD として拾ってしまい、「何が MOD なのか」の規則が増える。

小さい MOD でもフォルダにする。**単一ファイルとの混在を許さない**のは、
探索・静的検査・GUI・「新しい MOD をどう作るか」の4箇所すべてに分岐が増えるため。

入口ファイル:

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

| | |
|---|---|
| `@ctx.patch(target)` | 完全置換。置換関数から `__original__` で元にアクセスできる |
| `@ctx.wrap(target)` | 元関数を第1引数で受け取るラッパ |
| `ctx.resolve(target)` | `(owner, name, value)` を返す。調査用 |
| `ctx.log(...)` / `ctx.log_exc(...)` | `out/modloader.log` へ |
| `ctx.out_path(name)` | `out/<name>` の絶対パス。MOD 専用ログはここへ |
| `ctx.mod_dir` | いま apply() 中の MOD のフォルダ。**同梱データを読む用**（書くのは `out/`） |
| `ctx.on_ready(fn)` | **プロセスにつき1回だけ**メインスレッドで実行（§3.6） |
| `ctx.patches()` | 対象 → 当てた MOD の一覧。自分より前の分が見える（§3.7） |
| `ctx.config` / `ctx.setting(名前)` | この MOD に効いている設定値（§3.8） |
| `ctx.api` / `ctx.version` | ローダの API 番号と版（§3.9） |

`target` は `module:qualname` 形式（`llm_manager:quest_referee_event_resolve`、
`llama_cpp_runtime_completion:LlamaCppClient.chat`）。

`@ctx.patch` / `@ctx.wrap` に渡せるキーワード引数:

| | |
|---|---|
| `required=False` | 対象が見つからなくても黙って降りる（既定は例外） |
| `safe=True` | **フックの例外をゲームへ流さず、元の動作に落とす**（下記） |
| `alias_scan="all"` | エイリアス張り替えを全モジュールに広げる（既定は関係する範囲だけ。§4.1） |

`@ctx.patch` は**対象の名前が無ければ撥ねる**。`setattr` は黙って新しい名前を作るので、
対象名を打ち間違えた MOD が「当たった」ことになってしまうため。名前を新設したいときだけ
`required=False` を明示する。

**`safe=True` はフックの例外をゲームへ流さない。** 「ゲームを落とさない」ためだけの
`try` / `except` を毎回書く代わりに使える。落とし方は元の関数が**もう走ったか**で分かれる:

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
| `orig` を呼んだ後に壊れた | **その結果をそのまま返す**（後処理だけが失敗した） |

2つ目が要点で、単純に「失敗したら元を呼び直す」と書くと元の関数の副作用（テキストの追加・
セーブ・状態の更新）が2回起きる。`safe=True` は**例外を隠す**ので、直すべき不具合を
見えなくもする ― 例外は `ERROR` としてログに残るので、`safe hook failed` を見たら直すこと。

**名乗り（`mod.json`）:**

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

`entry` 以外は任意。うち **`api` / `after` / `before` / `conflicts` / `settings` は動作に
関わり**（§3.2 / §3.8 / §3.9）、`name` / `description` / `version` / `author` は表示専用。

**名乗りを Python ではなく JSON に置いているのが要点。** GUI は MOD の一覧を作るのに
**コードを1行も走らせずに済む** — 無効化中の MOD も、壊れている MOD も、名前付きで
並べられる。モジュール変数に置くと、一覧表示のためだけに他人の MOD を import する
ことになり、import した時点でトップレベルのコードは走ってしまう。

`status()["manifests"]` は言語ごとの分岐を書かなくて済むよう、次の形に均して返す:

```python
{"dir": "109_fix_item_detail_autosize", "entry": "item_detail.py",
 "name":        {"en": "Item detail autosize", "ja": "アイテム説明欄の拡張"},
 "description": {"en": "...",                  "ja": "..."},
 "version": "1", "author": "R01/Flossian"}
```

**片方の言語しか書かれていなければもう片方で埋める**ので、`name["ja"]` は必ず何かを
返す（GUI の行が空にならない）。`"name": "Some mod"` のように文字列1つでも書ける。

`name` は**一覧に並べる名前**なので短く保つ（英語 半角30 / 日本語 全角12 まで。
`tools/test_patch_registry.py` が長さを検査する）。何をする MOD かの説明は
`description` 側に置き、設計判断は入口ファイルの docstring に書く。

**ログにはフォルダ名を出す**（名乗りは出さない）。フォルダ名はインストール単位で一意、
cp932 のコンソールでも化けず、grep もしやすい。`version` を必須にしないのは、
バグ修正1本の MOD にまで版番号を付けて回ることになるため。

### 3.2 適用順

**`runtime/mods/load_order.json` が適用順を決める。** 先に適用した MOD ほど内側、
**後から適用した MOD が外側**になる。

```json
{"order": ["000_recon", "001_crash_recorder", "100_fix_kivy_shutdown", "..."],
 "disabled": ["000_recon"]}
```

`"disabled"` に載っている MOD は読み込まない。GUI のチェックボックスの実体で、
**フォルダ名を変えずに切れる**ようにしてある（無効化を `_` 接頭辞でやると、切った
瞬間に `"order"` の中の名前と食い違う）。切った MOD は `modloader.log` に
`disabled in load_order.json; not loaded: ...` として必ず残す。

順序をフォルダ名から決めない理由は、フォルダ名を自由に付けられるようにするため
（「名前は自由」と「順序は名前で決まる」は両立しない）。同梱 MOD のフォルダ名に付いて
いる `000_` `100_` などの番号は**作者側の整理のためだけのもので、ローダは見ていない**。

| 状況 | 挙動 |
|---|---|
| 順序ファイルに無い MOD | 捨てずに**末尾へ回す**（フォルダ名順）。置いただけで動く |
| 順序ファイルにあるが実体が無い | 黙って飛ばす（消した MOD の記述が残っていても壊れる） |
| 順序ファイルが壊れている / 無い | **フォルダ名順で動く**。ここで例外にすると MOD が全滅する |
| `"disabled"` にあるが実体が無い | 何もしない（無効化の記述が残っていても壊れない） |

順序は動作の前提なので、`tools/check_mods.py` が
**宣言と実体のずれ（未記載・実体なし・重複）を注入前に報告する。**

同梱 MOD の番号は次の意味で振ってある:

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

帯は帯であって分類の軸ではない。ゲーム本体の挙動を変えるなら機能追加でも 100番台で
よい（`104_balance_area_bgm`）。**番号を振り直すときは `load_order.json` も直すこと**
（フォルダ名を変えるので）。`check_mods.py` が食い違いを報告する。

**先頭に `_` を付けたフォルダは読み込まれない**。手元で一時的に外すときの手段で、
配って使う無効化は `load_order.json` の `"disabled"` を使う。

**上の関係は MOD 自身に宣言させる。** 順序ファイルは利用者が触るもので、こういう前提を
知らない ― GUI で行をドラッグすれば壊せてしまう。文章で書いてあるだけでは守れないので、
`mod.json` に書く:

```json
{"entry": "probe.py", "api": 1,
 "after":  ["103_fix_eventlog_trim"],      これより後（＝外側）に適用してほしい
 "before": ["105_fix_schema_compact"],     これより先（＝内側）に適用してほしい
 "conflicts": ["104_balance_area_bgm"]}    同時に有効にしても意味を成さない
```

`discover()` が**安定なトポロジカルソート**でこれを満たす。基準の並びは `load_order.json`
のままで、**制約に触れない MOD の相対順は動かさない**（利用者が並べ替えた意図を、制約を
満たす範囲でそのまま残す）。

| 状況 | 挙動 |
|---|---|
| 制約が実体の無い / 無効な MOD を指している | 黙って捨てる。ただし `problems` に報告する |
| 制約が循環している | **`load_order.json` の並びで動かす**（ここで全滅させない）。報告する |
| `conflicts` の相手が同時に有効 | **報告するだけで落とさない**（下記） |

`conflicts` で片方を落とさないのは、このローダでは**同じ対象に複数の MOD を重ねるのが
正常な使い方**で（§3.7）、どちらを外すべきかローダには決められないから。両方動かして
名指しし、外すかどうかは利用者が `"disabled"` で決める。

同梱 MOD の宣言は**いまの `load_order.json` の並びをそのまま固定している**ので、これを
入れても適用順は変わらない。変わるのは「壊せなくなった」ことだけ。

### 3.3 同じ場面に複数の MOD が乗るとき

外側が処理を止めれば内側には呼び出しが届かない。`304_` が解散そのものを止めると、
`303_`（外れた仲間の置き先を変える）には `remove_party_member` が来ない。**重ねるなら
「外側が降りたとき内側が本来どおり動く」形にしておく**こと。

**印のキーは MOD ごとに別にする。** 自前のボタンは `on_button_press` を包んでボタン辞書の
独自キーで横取りするが（§4.2）、同じキーだと押下が食い合う（`301_` は `mod_action`、
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
    @ctx.wrap("...")            # パッチは apply() の中で当てる
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
| キー | 既定は「MOD 名 + 関数名」。`key=` で明示できる |
| 戻り値 | 積まれたら `True`、既に実行済みで捨てられたら `False` |
| `force=True` | 印を無視して積み直す。**開発中の逃げ道**（配布する MOD に書かない） |
| `reset_once("300_")` | ローダ側から印を落とす。同じく開発用。副作用は戻らない |

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
| `UNRESOLVED` | モジュールは在るが対象が無い | **ゲーム更新を最初に疑う**。`out/recon/` で名前を取り直す |

`UNRESOLVED` は `required=True`（既定）なら例外にもなるが、**投げる前に記録している**ので、
その MOD が `apply-error` で落ちても何が見つからなかったかは報告に残る。バージョン番号を
宣言させるより、実際に対象が在るかを見る方がこの環境では確実（`.pyc` が無く、ゲームの
バージョンを取得する経路も無い）。

プロセスの中からの問い合わせ:

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
`patch_registry.summary()`）は**データのまま**返す。並び順や言い回しは受け取った側で
決められるよう、ここでは整形しない。

**`boot()` の最後に `out/status.json` へ書き出す。** ゲームの外からこれを読むのが、
GUI と「実際に動いたゲーム」の唯一の接点。注入が成功したことと **MOD が入ったことは
別の話**なので、ここを読まないと「28個中3個が `apply-error`」を利用者に出せない
（以前は `status()` があるのに呼ぶ側が居らず、利用者が `modloader.log` を自力で開く
しかなかった）。

ゲームの中へ問い合わせる経路を作ると注入をもう1本増やすことになるので、**こちらから
書き出す**形にしてある。1方向で済み、ゲームが終了した後でも読める。遅延当て直し（§3.4）
のたびに上書きされるので、中身は常に最新の boot。`*.log` とは別扱いで世代管理しない
（常に「今の状態」を表すファイルで、履歴に意味が無い）。

`apply()` の中からは `ctx.patches()`。順に適用されるので、見えるのは**自分より前に
読み込まれた MOD の分だけ**。

### 3.8 利用者が変えられる設定（`ctx.config`）

設定は今までも「`.py` の先頭の定数を書き換える」でできたが、フレームワークとしては
2つ困ることがあった。**GUI から見えない**（一覧に出るのは名乗りだけで、何が変えられるか
分からない）ことと、**MOD を更新すると設定が消える**（値がコードの中にあるので、新しい版で
ファイルを差し替えた瞬間に利用者の選択が上書きされる）こと。

そこで値の置き場所をコードの外へ出す。**MOD のコードは何も変えなくてよい。**

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

ローダは MOD を読み込んだ**後・`apply()` を呼ぶ前**に、選ばれた値をモジュールのグローバルへ
書き込む。`apply()` の中で作られる入れ子の関数は定数をモジュールのグローバルとして読むので、
この順なら定数をそのまま使っているコードに新しい値が届く。

```
[modloader] INFO    setting EVENT_MODE = 'narration' (default 'conversation')
```

| | |
|---|---|
| 扱う型 | `bool` / `int` / `float` / `str` / `choice`（+ `min` / `max` / `allow_null`） |
| 置き場所 | `settings/mod_settings.json`。**`mods/` の中には書かない** |
| 書くのは | 既定と違う値だけ。既定に戻したら消える |
| 読めない値 | **黙って既定に倒す**（設定ファイルが壊れて MOD が全滅しないように）。ログに残る |
| 宣言だけあってコードに定数が無い | 書き込まない＋警告（`@patch` が名前を新設しないのと同じ理由） |
| `apply()` の中から | `ctx.config` / `ctx.setting("EVENT_MODE")` |

`mod_settings.json` を `mods/` の中に置かないのは、そこが配布物そのものだから（§3.1 —
`mods/` は読む専用、書くのは `out/`）。MOD のフォルダを丸ごと差し替えても設定は残る。

**辞書やタプルの設定は宣言しない。** `KINDS`（お題の一覧）のようなものを GUI の1行に
収めると「JSON を手で書く欄」になり、コードを直接編むより分かりにくい。そういう設定は
コード側に置いたままにする。

**ただし「プレイヤーの体験に関わる設定」なら、宣言できる形に割るほうを選ぶ。** 施設別の
発生率は元々 `CHANCE_BY_TYPE` という1つの辞書だったが、`CHANCE_INN` / `CHANCE_GUILD` /
… と種別ごとの `float` に割って宣言してある（2026-07-26）。施設種別はセーブの
`facility_type` そのもので**ゲーム側で閉じた集合**だから、1つずつ並べても項目が
際限なく増えることはない。表は `apply()` の中で組み直す ― 設定の反映はモジュールの
グローバルへの書き込みなので、**トップレベルで組むと既定値の表が固まってしまう**。

**既定値が2箇所に書かれることは認めている。** 実際に使われるのはコードの定数で、GUI が
表示に使うのは `mod.json` の `"default"`（GUI は MOD のコードを import しない決まりなので
定数を読めない）。ずれると「GUI では既定 3 と出るのに実際は 5 で動く」という最も気付き
にくい形になるので、**`check_mods.py` が AST で突き合わせて報告する**。

### 3.9 ローダ API の契約（`"api"`）

`mod.json` の `"api"` が、その MOD が前提にしているローダ API の番号。`boot()` は
**コードを読み込む前に**これを見て、扱えない MOD を撥ねる（名乗りが JSON にあるからできる）。

```
[modloader] WARN  someones_mod: needs loader API 2 but this loader provides 1;
                  update InstantaleModLoader
```

| | |
|---|---|
| 書いていない | `1` として扱う（`DEFAULT_API`） |
| ローダより新しい | 読み込まない。`api-too-new` |
| `MIN_API` より古い | 読み込まない。`api-too-old` |
| `ctx.api` | MOD 側から番号を見る（下位互換の分岐が要るとき） |

`__version__` とは別に持っている。前者は配布物の版で、上がっても MOD が壊れるとは限らない。
`API` は**壊れる変更のときだけ**動かす番号で、だからこそ判定に使える。

| 上げる | 上げない |
|---|---|
| `ctx` のメンバを削除・改名 | `ctx` にメンバを**追加** |
| 引数の順序・意味を変更 | 省略可能なキーワード引数を追加 |
| 既定値の変更（`alias_scan` / `required`） | ログの書式・内部の整理 |
| `on_ready` のキー導出の変更 | `__version__` だけの更新 |
| `ui.Screen` の signature 変更 | `ui` / `frames` への関数追加 |

**ゲーム側のバージョンを宣言させないのとは事情が違う。** ゲームの版は信頼できる形で取れず
（`.pyc` が無く、Epic のマニフェストは別系統）、依存先が在るかは実行時に確かめられる
（台帳の `UNRESOLVED`）。ローダ API はその逆で、版は自分のコードにあるので確実に取れる一方、
**意味の変化は `hasattr` では捕まえられない**。`alias_scan` の既定を変える・`on_ready` の
キー導出を変えるといった変更は、例外にならないまま挙動だけを変える。

### 3.10 パッチを剥がす（`unload`）

```powershell
python tools/injector.py --unload      # GUI なら「MOD を外す」
```

ゲームを終了せずにパッチを剥がす。MOD を疑うときの切り分けに使う。剥がすための記録は
`sys` に置いてあるので（patch.py の `_undo_log`）、注入から今までの間にローダが何度
読み直されていても剥がせる。

**属性を戻すだけでは足りない。** 当てたときに張り替えた複製束縛（`from x import y` の
コピー）はラッパを指したままで、そこから呼ばれる経路が生き残る。当てたときと同じ範囲を
逆向きに張り替える（§4.1）。

**完全に元通りにはならない。** 戻らないものを断っておく:

```
on_ready で既に起きた副作用（掃除・状態ファイルの初期化）
MOD がゲームの状態そのものに書いた値（パーティの名簿・依頼）
MOD が立てたスレッドや Clock の予約
```

素のゲームで確かめたいなら、**注入せずに起動し直すのが確実**。

---

## 4. Nuitka 環境の制約

### 4.1 効くもの・効かないもの

効く:

- `mod.func = new` — コンパイル済みコードもグローバルはモジュール辞書経由で引く
- `Cls.method = new` — Nuitka のクラスは通常の `type`

効かない・要注意:

- **`from x import y` で他モジュールに複製された束縛。** `patch()` / `wrap()` は既定で
  `alias_scan=True` にしてあり、同一オブジェクトを指すグローバルを掃引して再束縛する。
  これが無いと「`x.y` は直したのに呼ばれ続ける」が起きる（Kivy の `wm_pen` /
  `wm_touch` が実例で、修正はどちらも複製束縛側から呼ばれる）
- **単一のコンパイル済み関数内でローカル解決された呼び出し** → 到達不能。呼び出し元の
  関数ごと差し替えること

**張り替えを探す範囲は絞ってある。** 既定は**ゲーム自身のモジュール（`GAME_TOPLEVEL`）＋
対象と同じトップレベルパッケージ**。配布物には約 4200 のモジュールが入っているので、
全件なめると次の2つが起きる:

- **コスト** — パッチ1本ごとに全モジュールの全グローバルを見る。当て直し（§3.4）は
  最大 8 回あるので、これが積み上がる
- **巻き添え** — 同じオブジェクトを指しているだけの無関係な名前まで張り替わる

対象のトップレベルを足しているのは、ゲーム以外を狙うパッチのため
（`kivy.input.providers.wm_common` の複製束縛は kivy の中にあるので、ゲームのモジュール
だけに絞ると届かない）。全部なめてほしいときは `alias_scan="all"` を明示する。

### 4.2 テストで identity 比較を使わない

`alias_scan` は古いラッパを指す変数を張り替えるため、**テスト側が握っている `__main__` の
グローバルまで張り替えられる**（`__main__` は `GAME_TOPLEVEL` に入っていて、直接実行時の
`__main__` はテスト自身）。`Cls.method is not before` は成立しない。確かめるべきは
**連鎖の段数と呼び出し結果**。

### 4.3 テストのクラスをグローバル名から派生させない

直接実行時の `sys.modules['__main__']` はテスト自身なので、`main.InstantaleApp = app_cls`
はテストのグローバル名を書き換える。`type("InstantaleApp", (InstantaleApp,), {})` と書くと
2回目以降は前回の派生クラスから派生し、前のテストのフックが積み上がって同じ処理が何度も
走る。**派生元は `BASES` のような表に控えておく。**

---

---

## 5. 共通部品

実機で確かめた「ゲームがどう動いているか」はここに集約する。**同じ発見を MOD ごとに
書き直さないこと** — 片方が古くなるのは時間の問題で、実際に8件の反映漏れが生まれた。
MOD に残すのは**その MOD の設計判断**（どこにボタンを出すか、確率、置き場所の規則など）だけ。

### 5.1 `instantale_modloader.ui`

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

---

---

## 6. 落とし穴（ルール一覧）

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
| 同じ発見を2箇所に書かない | 実機で確かめた事実は `ui.py` / `frames.py` と GAME.md へ。MOD には設計判断だけ |
| 同じ規則を2箇所に実装しない | 探索・適用順は `discover()`、設定は `config.py`。GUI もツールもそれを呼ぶ（§1） |
| 順序の前提は文章ではなく `after` / `before` に書く | 文章は守られない。GUI で行を動かせば壊せる（§3.2） |
| 利用者に触らせる値は `"settings"` に宣言する | コードの定数だけだと GUI から見えず、MOD の更新で消える（§3.8） |
| `safe=True` を握り潰しの代わりに使わない | 例外はログに残るが見えなくなる。`safe hook failed` が出たら直す（§3.1） |
| `on_ready` に `force=True` を残さない | 開発中の逃げ道。配ると当て直しのたびに副作用が起きる（§3.6） |
| 壊れた設定ファイル・順序ファイルで MOD を全滅させない | 既定に倒して動かし、報告する。「動かない」より「報告して動く」（§3.2 / §3.8） |

---

---

## 7. 実装例カタログ

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

---

## 8. 制限

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
