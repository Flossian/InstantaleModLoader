# GAME: Instantale 内部リファレンス

実機で確かめた「ゲームがどう動いているか」。
MOD を書くときに要る、Instantale そのものの構造・語彙・作法を集める。

- ローダの仕組みと MOD の書き方は [TECH.md](TECH.md)
- 遊ぶだけなら [README.md](README.md) と [MODS.md](MODS.md)
- 検証状況と未確認項目は [VERIFICATION.md](VERIFICATION.md)、
  実測の記録は [VERIFICATION_LOG.md](VERIFICATION_LOG.md)

TECH.md と分けているのは読む理由が違うから。
あちらは「このローダで MOD をどう書くか」（他のゲームにも通じる話）、
こちらは「Instantale が何をしているか」（このゲーム限定の事実）。
ゲームが更新されて食い違うのはこちらだけなので、疑う場所が1つになる。

> ここに書いてあるのはすべて実測で、ソースは読めない（Nuitka standalone）。
> 推測は書かず、確かめていないことは VERIFICATION.md の未確認項目に置く。
>
> 節番号は他の md とソースから参照されている。欠番になっても詰めない。

---

## 1. パッチ対象の見つけ方

### 1.1 リコン成果物 (`out/recon/`)

ソースが読めない以上、正確なパッチ対象名はここからしか得られない。
`000_recon` が注入のたびに書き出す。

| ファイル | 内容 |
| --- | --- |
| `targets.txt` | `module:qualname(signature)` 形式。`@ctx.wrap` にそのまま貼れる（1,635件。main_025、2026-09-01 のダンプ） |
| `game_modules.txt` | ゲーム自身のモジュールの全属性ダンプ（擬似ソース） |
| `modules.json` | 全モジュールの機械可読インベントリ（`bases` / `mro`） |
| `build.json` | このダンプがどのビルドを見たものか（版と sha256） |
| `summary.txt` | 環境・`sys.path`・モジュール census |
| `bug_sites.txt` | crash_log.txt の各クラッシュ地点のプローブ |

`out/recon/` は毎回上書きされるが、`build.json` と突き合わせて
**`IDENTITY_KEYS`（`app_version` / `game_version` / `exe_size`）が1つでも違えば
上書き前に `out/recon_snapshots/<版>_<日付>.zip` へ退避される**
（控えが無いときも「別のビルド」扱い。退避は `SNAPSHOT_KEEP = 20` 本まで残し、古いものから消す。
`recon.py` の `_same_build`）。
更新の前後で `targets.txt` を突き合わせれば、増えた対象・消えた対象がそのまま出る。

### 1.2 ゲーム自身のモジュール

```python
__main__                       instantale.py、575ターゲット（main_025、2026-09-01 のダンプ）
scripts                        scripts.hud.* / scripts.llm.* / items / functions ほか
                               scripts.save_codec, scripts.steam.server_process
Embedding, image_generation, llama_cpp_runtime_completion, sidecar_process
save_area_json, save_world_json, api_key_manager, build_type, sdcpp_cuda
```

`__main__` は `sys.stdlib_module_names` に含まれる。
素朴に stdlib を除外するとゲーム本体が丸ごと抜け落ちる
（`recon.py` の `GAME_TOPLEVEL` はアローリスト）。

### 1.3 スキャンで見つからないもの

| 見つからないもの | 見る先 |
| --- | --- |
| ネスト関数（`send_request_on_id` はトレースバックに62回出るが `vars(module)` に無い） | 外側の関数（`send_request` / `send_request_with_no_structure`） |
| クラスのメソッド（`set_ai_models` / `show_world_choice` はモジュールレベルのキーワードスキャンで0件） | `game_modules.txt` |
| 属性名を推測して探したもの | `vars(obj)` を一度全部出す。HUD の描画先を `texts` / `labels` で探して見つからなかった実例がある（正解は `hud.buttons[i].text`。§2.3） |

### 1.4 環境の基本値

| 項目 | 値 |
| --- | --- |
| ゲーム本体 | `C:\Program Files\Epic Games\Instantaleq6Ve7\instantale.exe` |
| ランタイム | CPython 3.10.11 / Kivy / SDL2 |
| `game_version` | `014`（`__main__.get_game_version()`）。Epic の `AppVersion`（`main_025`）は別系統 |
| ロード済みモジュール | 4226（うち 3212 が Nuitka コンパイル済み）／ゲーム自身は 67（main_025、2026-09-01 のダンプ） |
| セーブ | `%LOCALAPPDATA%\Darmabeko\Instantale\` |
| クラッシュログ | `<ゲームdir>\crash_log.txt`。更新で消えることがある |
| LLM 入出力の記録 | `<ゲームdir>\output_data\<世界>\<PC>\<manager>\N.json` |

ゲーム内部のバージョンは実行時に問い合わせる。
Epic の `AppVersion` は
`%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests\*.item` の
`AppVersionString` から、ゲームを起動せずに読める。

### 1.5 更新の記録

`game_version` は main_023 以降 `014` のまま据え置きで、
上がっているのは Epic の `AppVersion` だけ（`main_023` → `024` → `025`）。
`python310.dll` は 2026-06-03 のままなので**注入基盤は無傷**。

| 版 | ゲーム側の変化 | MOD 側 |
| --- | --- | --- |
| main_022 → 023（2026-07-30） | `targets.txt` 1466 → 1585。自由生成施設（§2.21）・`scripts.save_codec`・Steam 認証・装備強化が増えた | 対応不要。28/28 適用、警告0 |
| main_023 → 024（2026-08-05） | 賭博2種（ハイアンドロー / ロシアンルーレット）で 68 ターゲット増。起動処理の変更で満額到達が 80秒 → 41秒 | 6件が「Reported by ModLoader」として取り込まれた（下記） |
| main_024 → 025（2026-08-09） | `InstantaleApp.start_game` の 876 行が `experience_level=60` → `1` | `123_` が何もしなくなった（VERIFICATION_LOG.md §2.36） |

#### 本体に取り込まれた修正の判定

症状が出ないだけでは、本体と MOD のどちらが直したのか区別できない。
判定はその MOD 自身の印で行う。

| MOD | 印 | 判定 |
| --- | --- | --- |
| `107_fix_battle_flag_stuck` | `[FLAGFIX]` 0件で `in_battle` が落ちている | 本体が直した |
| `106_fix_battle_bgm_restore` | `[BGMFIX]` 0件 | 本体が直した（但し書きあり） |
| `101_fix_npc_employ_price` | 上流プローブ＋リコン差分 | 本体が直した |
| `110_fix_character_name_path` | `[NAMEFIX]` 実ゲームで0件 | 本体が直した（§2.15） |
| `103_fix_eventlog_trim` | `[EVENTLOG]` 0件（イベント5回ぶん） | 本体が直した |
| `108_` | **無し** | 判定不能。確かめようがないので降ろした（VERIFICATION.md §3.8.1） |

判定に手間の要ったものが3つあり、それぞれ理由が違う。

- `106_` … ログが注入のたびに世代交代するので「以前は出ていた印が出なくなった」の形で比べられない。
  根拠は「症状が出ていない」＋「MOD が手を出していない」の2点どまり
- `101_` … 雇っただけでは確かめられない。
  clamp が0件 ＝ 難易度が有効域 0..76 に収まっていた ＝ **壊れる側の入力を通していない**。
  76 超の NPC は出現を待つしかなく能動的に用意できない
- `103_` … 刈り込みの条件は `KEEP_TURNS = 3` 超えで、
  フィールドイベントは仕様上3ターンで決着する ＝ **1イベント単位では原理的に届かない**。
  旧バグはイベントを跨いでログが育つものなので、複数回通して初めて差が出る

> **上流プローブ**（§3）。
> `101_` は包む前の素の関数を控えておき、注入のたびに `0..200` を総当たりして
> 上流の状態を `upstream:` 行に記録する。
> 能動的に起こせないバグはこの形でしか判定できない。
> ただし**「何を測ったか」を自分で検算させること**。
> 最初の版は再注入で前回のラッパを測っていて（層を剥がすのは `ctx.wrap` の中＝控えより後）、
> clamp 済みの関数はどの入力でも落ちないので誤判定した。
> いまは `__original__` を最下層までたどり、剥がした層数と、
> 底にローダの印が残っていないかを併せて記録する。

伏せる／残すの判断は、**セーブに残るものを書き換えるか**で分かれる。
`110_` は名前を書き換えてセーブに焼くので、本体が直った後も残すと余計に改変する側に回る。
`101_` のクランプや `102_` のような検出器は冪等・受動的なので、残しても害が無い
（ただし `102_` / `108_` はその後どちらも `superseded` で降ろした。
§1.6 / VERIFICATION.md §3.8.1）。

> リコンの差分は「取った時点」を揃えないと嘘になる。
> main_023 → 024 の突き合わせで85件が「消えた」ように見えたが、
> 実体はまだ import されていないだけだった（退避した main_023 は長時間プレイ後、
> main_024 は起動41秒後）。
> 増えた側だけが信用できる。減った側を読むなら同じくらい遊んだ状態で取り直す。

> `crash_log.txt` は main_023 への更新で消えた。
> 同梱されていた 114件（`100_` / `204_` が件数を根拠に挙げているもの）は `013` の記録で、
> 現物はもう無い。件数を根拠にする議論はこの境目を跨がせないこと。
> main_024 で復活したが、中身はこの環境で新しく積まれた2件だけ
> （どちらも `100_` が対象にしている終了時クラッシュで、注入前のセッションのもの）。

### 1.6 公式修正と MOD の重なり（main_023、実測）

`out/prompt_bloat.log` が世代交代していない区間で前後を比べられた。

| MOD | タグ | 更新前 | 更新後 | 判定 |
| --- | --- | --- | --- | --- |
| `102_fix_prompt_dedup` | `[DEDUP]` | 3 | 0 | 不要（同じ操作を通して確認） |
| `103_fix_eventlog_trim` | `[EVENTLOG]` | 49 | 発火 | 引き続き必要 |
| `105_fix_schema_compact` | `[COMPACT]` | 359 | 継続 | 引き続き必要 |

`102_` は発生源で直っている。
更新前と同じメッセージ（ハッシュ一致）で、重複していた圧縮後のスキーマ system が2つ並ばなくなった。
`103_` が一時0件だったのは出番が来ていなかっただけで、
`quest_event_log` が育つのはクエスト進行の後半なので町にいる間は0件のまま。
`105_` が削るのはプロンプト本文に埋め込まれたスキーマの repr（§2.12）で、
ゲームが直した「再生成時の重複・増幅」とは別物。

`102_` は 2026-08-10 に `superseded: main_023` で降ろした（`103_` は main_024 で同様）。
消さずに伏せるのは**検出器として残す**ため。
降ろす直前の裏付けは、同じ `LlamaCppClient` 経路の `[COMPACT]` が349件出ているのに
`[DEDUP]` は0件だったこと（クラウド実行では両方素通りなので、
この突き合わせはローカルの記録でしか成立しない）。

> 0件だけでは「不要になった」ことの証拠にならない。
> その操作を通していなければ、出番が来ていないのと区別が付かない。

### 1.7 起動直後に注入したときの見え方（更新とは無関係）

段階適用の途中経過が WARN として大量に出るので、更新で壊れたように見える。

| 起動からの時間 | patches | 中身 |
| --- | --- | --- |
| 3秒 | 11 / 9 target / 9 mod | `__main__` がまだ空。`has no attribute 'InstantaleApp'` が大量に出る |
| 9秒 | 47 / 35 / 20 | 一部のモジュールが import され、再適用 |
| 85秒 | 137 / 93 / 26 | 満額 |

- **`boot complete: N/N mod(s) applied` は「掴めた」ことの証拠にならない**
  （`apply()` が例外を出さなければその数になる）。
  見るのは次の行の `patches: N applied on M target(s)`
- 再注入は安全（`replacing a previous patch layer` が出て二重には掛からない）
- `ERROR bgm restore: channel scan failed`（`mixer not initialized`）もこの状況で出る。
  掃除が mixer 起動前に走っただけで、捕捉済み・処理は継続

### 1.8 影響を確かめていないこと

- **クラウド LLM 経路ではプロンプト系 MOD が素通りする**（確定）。
  経路はプロバイダごとの `request_llm_inference_*` で、`LlamaCppClient` を通らない（§2.12）。
  `111_` は v4 でプロバイダ非依存の `llm_manager` 別名包みに置き換えて対処済み。
  素通りの実害があるのは `103_` だけ（`quest_event_log` の肥大はマネージャ層で起きるのでクラウドでも育つ）。
  `102_` は本体が直済み、`105_` の対象はローカル固有。
  `301_` は `LlamaCppClient` を使っていないのでクラウドでも効く。
  **1本の MOD の中で半分だけ効く形もありうる**（`llm_manager:quest_referee*` に仕掛けた側は効く）
- 自由生成施設（`FreeFacilityManager`）の最中に `300_` の施設イベントが乗るか未確認。
  どちらも「施設に入ったとき」に働くので二重に始まる余地がある（§2.5）
- 四体以上の敵との戦闘を更新後に通していない。
  公式修正が入った箇所なので `106_` / `107_` / `207_` と噛み合うかはこれから
- サイドカーの多重起動抑止が三者競合（ゲーム自身の修正・`LlamaCppSidecar` の所有者調停・
  InstantaleLLMProxy）になった（§2.12）
- 装備強化の画面が `109_` と噛み合うか未確認（`upgrade_level` が詳細欄に出るなら文字量が増える）
- `InventoryGrid.try_place_item` / `get_unique_items` は `108_` が掴む
  `place_existing_item` とは別経路。強化画面のグリッドで同じはみ出しが起きるかは未確認

---

## 2. ゲーム内部リファレンス

### 2.1 スレッド

`process_choice` はメインスレッドで呼ばれ、その中で `execute` を**別スレッドへ渡して即座に返る**。

- UI と pygame を触るのは Kivy の `Clock` から。`execute` の中から直接触らない
- Clock から押す実装が本来のボタン押下と同じ経路になる。自前でスレッドを立てる必要は無い
- 長い処理は `execute` の中で同期的にやりきる。
  そこからさらに別スレッドへ投げて即座に返すと、ゲームは行動が終わったと判断して操作を戻す
- 非同期に渡される処理を、呼び出しの前後で測ってはいけない。
  `process_choice` の前後は「最中」を捉えない
  （実測: `process_choice` は 2ms で返り、その 2ms 後に `elapse_days` が来て、
  `execute` が返るのは 5.7 秒後。VERIFICATION_LOG.md §2.50）

### 2.2 選択肢ボタン

```python
app.buttons = [{'text': '会話する', 'spec': PhaseSpec('DisplayTalkChoice', [])},
               {'text': 'テストNPC B', 'spec': PhaseSpec('ConversationStartManager', ['73'])},
               {'text': '出る',     'spec': PhaseSpec('MovePhaseManager', ['20','134','7'])}]
app.to_display_buttons    # 表示中の文字列のリスト
app.display_button_map    # 表示位置 -> buttons の添字
app.choice_button_page    # ページ送りの現在ページ
app.refresh_choice_buttons(reset_page=True)
```

`PhaseSpec(cls_name, args)` はマネージャのインスタンスではなく**その作り方**。
押されると `getattr(__main__, cls_name)(app, *args)` が組み立てられ
`app.process_choice(それ, 文字列)` に渡る。
押された添字は `display_button_map` で引き直される（`ui.pressed_entry` が同じことをする）。

> `app.function_correspond_to_input` は名前に反して対応表ではなく `PhaseSpec` 1個。
> 「いま自由入力を送ったら何を呼ぶか」を保持している。

自前のクラス名を `PhaseSpec` に書かない。
`PhaseSpec.to_dict()` が存在する ＝ ボタンはセーブに焼き込まれうる。
自前のクラス名を書くと、MOD 無しで起動したときに `getattr(__main__, ...)` が失敗する
（注入はプロセスと一緒に消えるので、これは必ず起きる）。

自前ボタンの作り方は、無害な既存クラス（`JustSetButtonToNormalPhase`）を spec に持たせ、
押下は `InstantaleApp.on_button_press` を包んでボタン辞書の独自キーで横取りする。
**文字列ではなく印で見る**のは、同じ文字列のゲーム側ボタンを巻き込まないため。

自前で組む `PhaseSpec` は、引数の値まで実測で確かめたものに限る。
押された瞬間にゲーム側で実行されるので、こちらの `try`/`except` の外側。
引数を1つ間違えるとそのままゲームが落ちる。

#### セーブに焼かれるのは `text` と `spec` だけ

独自キー（印）は落ちる（実セーブ8件で確認）。
したがってタイトルへ戻る・ロード・再注入のあと、**印の無い自分のボタンが復元されている**。
印で重複を判定していると自分のものと見なせず、同じボタンが2つ並ぶ。
しかも復元された方は spec が `JustSetButtonToNormalPhase` なので押しても無反応
＝ 見た目は同じなのに片方だけ効かないという、最も分かりにくい壊れ方になる。

差し込む前に `ui.Screen.prune_stale(buttons, ラベル一覧)` を通す。
「自分のラベル（前方一致）」かつ「どの MOD の印も無い」かつ「無害 spec」の
3つが揃ったものだけを落とす。

> 「自分の印が無い」では足りない。
> `refresh_choice_buttons` を包む掃除は画面が何であれ走るので、
> 他の MOD が今その場に出しているボタンも「自分の印が無い」に見える
> （`302_` の `やめておく` が `309_` の確認画面のキャンセルを消していた。2026-08-03）。
> 判定は「`mod_` で始まるキーが1つも無い」で行う（`ui.MARK_PREFIX` / `marked_by_a_mod`）。
> 掃除に使うラベルも、その MOD にしか無い文言だけにすること。

### 2.3 選択肢を変える手順

この3点を外すと、データは正しいのに画面が変わらない。

1. `process_choice` を通す。
   `app.buttons` を書き換えて `refresh_choice_buttons()` を直接呼んでも塗り替わらない。
   自前のフェーズクラス（`execute(choice_text)` だけを持つ）を作って同じ経路に乗せる
   （そのクラスは `PhaseSpec` には載せない）
2. 押下と同じ流れの中で差し替えない。
   ゲームは押下処理の中で描画するので、その前に差し替えると後の描画で古い内容に戻される。
   `Clock.schedule_once(..., 0)` に載せれば次のフレーム・メインスレッドの両方が同時に片付く
3. 塗るのは HUD。
   `refresh_choice_buttons` は `to_display_buttons` と `display_button_map` を組み直すところまで。
   実際に塗るのは `InstanTaleHUD.update_button_texts`（Kivy のプロパティ監視）で、
   監視対象は HUD 側にある（`app.to_display_buttons` は監視対象ではない）。
   HUD は属性名ではなく `InstanTaleHUD` の型で探す

いずれも `ui.Screen.apply_buttons` / `paint` に入っている。MOD 側で書き直さないこと。

仲間欄も同じ構図で、`app.party` を書き換えただけでは変わらない。
塗るのは `InstantaleApp.update_party_member(dt)` と `InstanTaleHUD.update_party_display(*args)` の
2手（`ui.Screen.paint_party`）。
パーティを増減させた MOD は最後にこれを呼ぶ
（`302_` は別れの文が流れ終わってから呼ぶ。文より先に消えると、まだ別れていないうちに居なくなったように見える）。

#### 画面に実際に出ているもの

| 何 | どこ |
| --- | --- |
| 選択肢の文字 | `hud.buttons[i].text`（`app.to_display_buttons` とは別物）。枠数は 4 で固定 |
| 自由入力の可否 | `hud.text_send_button.disabled` |
| 本文（情景描写・LLM の応答） | `hud.text_display`（`kivy.uix.label.Label`） |

本文ラベルの実測（2026-07-31、`112_` が実行時に探し当てた）:
`font_size=27` / `line_height=1.8`（Kivy の既定は 1.0 ＝ 行間はゲームが意図的に広げている）/
`text_size=[1340.8, None]`（幅だけ固定）/ 750文字の本文で `texture_size=[1340, 3738]`。

> `hud.text_display.text` は `hud.display_text` と**完全一致しない**。
> 実測では「片方がもう片方の末尾（または先頭）を含む」関係だった。
> 本文のラベルを文字列で探すなら完全一致を前提にしないこと。

#### 本文の打ち出し（タイプライタ）

`InstantaleApp.add_text_display(self, dt, context, index=-1)`。
実測（2026-08-03、`211_probe_text_speed`）:

| | 実測 |
| --- | --- |
| 1ティックで進む文字数 | 1文字（平均 1.03〜1.11。まれに 2〜3） |
| ティックの間隔 | `app.text_speed` 秒。設定を変えると即座に変わる（再起動不要） |
| `text_speed=0.04` / `0.08` | 48〜50ms ＝ 20文字/秒 / 80〜83ms ＝ 12文字/秒 |
| 言語 `ja` の既定 | 0.07（`scripts.functions:get_default_text_speed_for_language`） |

間隔はフレーム境界に丸められる（60fps ＝ 16.7ms 刻み。0.04 は3フレーム、0.08 は5フレーム）。
つまり**どれだけ小さい値を入れても最速は「1フレーム1文字」＝ 60文字/秒**。

高さはゲーム自身が `InstanTaleHUD.update_label_height()` で決め直す。
ラベルの `line_height` や `text` を触ったら `texture_update()` の後にこれを呼ぶ。

### 2.4 待機表示（「…」のアニメーション）

ゲームが長い処理の間に操作を止める形。

| 要素 | 値 |
| --- | --- |
| `app.is_button_enabled` | `False` |
| `hud.buttons[i].text` | `.` → `..` → `...`（約 0.3 秒周期）。全枠に出る |
| `hud.text_send_button.disabled` | `True` |
| `app.text_input_disabled` | `False` のまま（＝これは機構ではない） |
| `app.buttons`（spec の一覧） | 触らない。表示だけ差し替えるので後始末が要らない |

自前の処理でも同じものを出せる（`ui.Screen.busy_on` / `busy_off`）。

画面の繋ぎ目を隠すのにも使える。
会話を閉じてから次の画面を開くまでの間、`ConversationEndManager` の終了処理が
`app.buttons_backup`（会話相手の一覧）を復元するので一瞬それが見える。
待機表示を出したまま繋げば隠せる:

```
押下 → busy_on() → 会話を閉じる → in_conversation が落ちるのを待つ
     → busy_off(restore=False) → 次の画面を開く
```

`restore=False` が要点（元の選択肢を塗り直さない ＝ 古い画面を出さない）。

### 2.5 会話

会話画面には必ず「会話を終了する」が並び、その spec は
`ConversationEndManager(app, in_conversation_id, finisher, end_text)`。

- `args[0]` がいま話している相手の id。
  画面のボタンを読むだけで相手が分かる（`ConversationStartManager` を追跡する必要は無い）
- 会話中の `app.buttons` は「会話を終了する」1個だけ。
  会話画面も施設と同じ選択肢リストを使うので、自前の選択肢はその手前に挿す
- 会話は「状態」であって画面ではない。
  立ち絵の片付けと関係値の更新は終了処理の中にあるので、
  ボタンを別の画面に差し替えただけでは閉じられない（`app.in_conversation` が残り、立ち絵が付いてくる）
- 閉じるときは画面のボタンの `args` をそのまま写し、`end_text` だけ差し替える。
  そこは自由記述なので、事情を書けば会話の要約とライフログに残る
- 閉じ終わるまで待つ（要約で LLM が回るため最大120秒程度）。
  `app.in_conversation` が落ちるのを Clock で見張る

#### 画面の見分けは文字列ではなく spec のクラス名で

| 目印 | 意味 |
| --- | --- |
| `ConversationEndManager` がある | 会話画面 |
| `DisplayTalkChoice` がある | 会話相手を選べる ＝ 施設のルートメニュー |

依頼一覧（`QuestChoiceManager` が並ぶ）にはどちらも無いので入れ子にならない。

main_023 で `FreeFacilityManager` が `process_choice` を通るようになった（§2.21）。
クラス自体は `__main__` ではなく `scripts.free_facility` にあるので
`getattr(__main__, cls_name)` では引けない。
上の2つの目印はどちらも立たないので**「会話画面でも施設のルートメニューでもない第3の状態」**として通り抜ける。
判定は目印の有無で書き、既知以外を既定側に倒さないこと。

「行動」メニューは `app.buttons` とは別系統（HUD 上部の info レイアウト）:
`toggle_to_action_in_conversation` / `toggle_from_action_in_conversation` /
`start_battle_with_in_conversation` / `show_npc_item_window`。

会話フェーズを自分から起こすには、プレイヤーが NPC を選んだのと同じ経路に乗せる:

```python
app.process_choice(ConversationStartManager(app, npc_id), npc_name)
```

`DisplayTalkChoice`（NPC 一覧）は挟まなくてよい。
立ち絵・会話履歴・関係値・終了処理はすべてゲーム本来の実装が動く。
会話開始の合図は `<行動: 話しかける>` で、
向きを変えたいときは `llm_manager:conversation_starter` に渡す messages のコピーだけを差し替える。

会話の LLM（`conversation_facilitator` / `_after_retrieval`）を**まるごと差し替えて**
複数人に喋らせるには `404_party_talk`。
本体が読むのは戻り値の `content_violation` と `action.type` / `action.statement` /
`action.call_free_action` だけなので、`llm.create_structure` で同じ項目を持つ型を作って返せば
本体の表示・履歴・終了処理はそのまま動く。
`call_free_action=False` を返す限り自由行動 GM（`master_ai_facilitator_from_conversation`）には入らない。
会話中に見えている立ち絵は `hud.character_image` 1枚（`center_x` 0.5 / `center_y` 0.43、
`size_hint=(1, None)`、クラス `NearestNeighborImage`。`212_` の実測）。
`hud.character_image_right` はキャラクターシートを開いたときだけ出る自分の立ち絵で、会話の要素ではない。
絵は各 NPC の `image_src["fullbody"]`。相手枠の canvas（切り抜きと絵の Rectangle）はウィジェットに追従して動く。

### 2.6 割り込みのタイミング

移動・クエスト終了・会話終了の後始末（テキストの流し込み・ボタンの張り替え・要約）の
最中に割り込むと噛み合わない。
`ui.IDLE_SIGNALS` = `is_adding_text` / `is_button_enabled` / `is_popup_window_opened` を
Clock で見張り、手が空いてから実行する（`ui.Screen.when_idle`）。

戦闘・会話中かを見るフラグは6つ。
`in_battle` / `in_boss_battle` / `in_colosseum_battle` /
`in_conversation` / `in_free_input` / `in_action_in_conversation`。

移動が終わった瞬間は `MovePhaseManager.move_phase` の**復帰後**。
情景描写（`llm_manager:narrator`）は `move_phase` の内側で呼ばれる。
したがって「復帰後に印を置いて次の `narrator` で回収する」形にすると1手ずれる。
情景描写に合流したいなら、印は `orig` を呼ぶ前に置いて入れ子の `narrator` に拾わせる（`300_`）。

> フラグ名が意味するとおりに動いているとは限らない。条件に使う前に実測すること。
> `in_shopping` は店の外を往復しているだけの移動でも True のまま残る
> （買い物窓が開いているかは `is_popup_window_opened` で見る）。
> `in_battle` も経路によって下ろし忘れがある（§2.10）。

### 2.7 世界のデータ構造

現在地は `app` ではなくプレイヤーのキャラクタにぶら下がっている
（`app` 側の97属性に current_facility の類は無い）。

```
app.player.location      -> Facility   app, characters, choices, config, connections,
                                       description, facility_type, id, name, owner, parent_node
app.player.current_node  -> Node       facilities(dict), entrance_facility, ...
app.player.current_area  -> Area       name, descriptions, bgm, resident_npcs, size, ...
app.world.characters     -> {id: Character}    Facility.owner はこの id（str）
```

- 施設は `areas[id].nodes[nid].facilities[fid]` の入れ子。
  `initial_location` の `node` は null のことがあるので、ノードを総当たりして探す（`ui.find_facility`）
- `Character` は自分でも `id` を持つ（`210_` の55属性ダンプ。
  `world.characters` の先頭の1体で鍵と同じ `'0'` だった）。
  **全件で鍵と一致するかは確かめていない**。
  浅い複製にも写るので、複製を渡されうる場所で相手を見分けるならこれを使う
  （同一性で `world.characters` を走査する引き方は複製に対して必ず空振りする）。
  当てにするなら、同一性で引いた答えと突き合わせて食い違いを記録すること
- `Facility.characters` には重複の入ることがある（`['69', '69']`）。話者を選ぶときは一意化する
- ギルドは `facility_type == 'guild'`（`ui.find_guild`）
- 実在する `facility_type`: `entrance` / `exit` / `ward` / `guild` / `inn` /
  `general_store` / `specialty_shop` / `blacksmith` / `medical_facility` /
  `administrative_office` / `underworld_office` / `colosseum` / `slave_market` /
  `location` / `dungeon_location`。
  うち `ward` / `location` / `entrance` / `exit` は主のいない通路

#### セーブの形＝実行時の形ではない

| 項目 | ロード直後 | 遊んでいる最中 |
| --- | --- | --- |
| `player.current_area` | エリア id の文字列（`"7"`） | `Area` オブジェクト |
| `player.location` | 施設 id の文字列（`'106'`） | `Facility` オブジェクト |
| `app.world.characters`（実行時の名簿） | ほぼ空（`224_` の記録で1件。VERIFICATION_LOG.md §2.81） | `{id: Character}` |

> 「新しい世界では動くのに、セーブをロードすると動かない」の正体はこれ。
> 施設の種類で出し分けるボタンがロード直後だけ出ず、
> どこかへ移動して入り直すと直るので原因が掴みにくい（2026-08-05 に実機で踏んだ）。
> 施設を引くときは `player.location` を直接使わず、id でも引き当てる関数を通す。

旗も同じで、セーブに焼かれて下りないことがある
（ロード後にギルドへ入っても `app.in_shopping` が True のままだった実測がある）。

> **旗は「別の画面が出ている」ことの当て推量**でしかない。
> 画面そのものを見る手段があるなら画面のほうを信じること。
> 旗だけで出し分けると、一度立ちっぱなしになった世界では機能が二度と現れない。

### 2.8 パーティ

名簿の在り処と形は決めつけない。
セーブでは `game_variables['party']` が `['player', '63', ...]` の id 配列だが、
実行時に `app.party` から同じものが読めるとは限らず、`list` とも限らない
（`{id: Character}` の辞書のこともある）。

`302_` の `ui.party_stores` / `pick_store` が採っている手順:

1. 候補を全部集める（`app.party` / `game_variables['party']` / `world_dict[...]` /
   `world.party` / `player.party`、加えて名前に `party` が入る属性のスキャン）
2. 中身を見て本物を選ぶ（名簿には必ず `'player'` が入る）
3. `list` と `dict` の両方を受ける
4. 要素が id の文字列でも `Character` のインスタンスでも読む
5. 書くときは同じ id を持つ入れ物**すべて**から落とす
6. 1つも見つからなければ app の持ち物を全部書き出す（`dump_census`）

関連する `game_variables`: `original_party`（一時的な差し替えの控え）/
`quest_party_accompany_backgrounds` / `escaped_member_in_battle` /
`is_party_member_talk_enabled`。

#### 画面のパーティ欄は3枠で固定（`instantale.exe` の定数表）

名簿に何人入っていても、HUD が枠を3つしか作らない。

```
bottom_info_layout
  party_cells               range(0, 3)  ← ここが 3 で固定
    ClickableFloatLayout    size_hint=(1, 0.33) / member_id を持つ
      StencilFloatLayout → Image（立ち絵）
InstanTaleHUD.update_party_display(*args)          party_members（DictProperty）の変化で走る
InstantaleApp.on_member_label_press(label_index)   -> party_cells[i].member_id
                                                   -> process_party_member_choice(id)
```

- 4人目以降が出ないのは枠が足りないからで、`party_members` は欠けていない
  （会話も戦闘も4人目を含めて進む）。枠を足せば出る（`116_`）
- 押したときの相手は `party_cells` の添字で決まる。**並べ替えると押した相手と話す相手が食い違う**
- 枠の中身（立ち絵の `source` がどのキーから来るか）は読めない。
  埋まっている枠と `party_members` を突き合わせて、一致した場所を対応とみなす
- **枠を複製しても枠線は付いてこない**（canvas の描画命令は写らない）。
  線を自分で引くより `scripts.hud.new_hud` の `add_border(widget)` / `add_border_before(widget)` を呼ぶ
- 枠の `size_hint_y` は `0.33` であって 1/3 ではない。
  行数で割り直した比率を伸ばした帯に当てると、その差で元の枠が数 px 動く。
  並べ直すときは比率ではなく実測の座標で置く
- `update_party_display` は帯の子を1つずつ「枠」として塗る。
  **枠でないものをこの入れ物に置くとその瞬間に落ちる**（`IndexError`。`116_` が黒い板を置いて踏んだ）
- 選択肢ボタンの `disabled` を控えて書き戻さない。
  ゲームは応答待ちの間だけ選択肢を無効にするので、
  その瞬間の値を控えて後で書き戻すと有効に戻った後でも無効に落ちる（`116_` が実機で踏んだ）

#### 外す処理は書かない。ゲーム自身のものを呼ぶ

```
InstantaleApp.remove_party_member(member_id)
InstantaleApp.get_party_leave_facility(character_instance)      -> (施設, ノード)
InstantaleApp.move_npc_to_facility(character_id, character_instance,
                                   target_facility, target_node=None, register_facility=True)
```

- 名簿を実際に書き換えているのは `remove_party_member` の中。
  外す/外さないを決めたいならこの呼び出しを通す/通さないだけでよく、名簿に指一本触れる必要は無い
- `get_party_leave_facility` は**タプルを返す**。
  そのまま `move_npc_to_facility` に渡すと落ちる
  （別れること自体は成功して置き直しだけが失敗するので気付きにくい）
- `remove_party_member` 自身は NPC を動かさない。置き直すのは呼び出し元で、removal の後
- 置き場所を決めずに外さない。
  置き先が引けない土地（ダンジョン等）で外すと、その NPC は世界のどこにも居なくなる
- 動かした NPC は自主的には持ち場へ戻らない。
  ゲーム側に「元の場所へ帰る」動きは無い

> だから NPC を動かす MOD は、戻す責任も持つ。
> 動かす前の `location` と `current_node` を控え、役目が終わったら帰す。
> 控えずに動かすと、その移動は世界のどこからも取り消せなくなる（MOD を消しても NPC はそこに残る）。
> パーティ由来の移送（`302_` / `303_` / `304_`）が問題にならないのは、
> **動かす主体がゲームで、MOD は置き先を差し替えているだけ**だから。

#### クエストクリアの解散

```
add_text('パーティは帰還した...') → 報酬・才能
remove_party_member('71')  from QuestEndManager.method_1 (instantale.py:6602)
                           <- QuestEndManager.execute (:6635) <- run (threading.py:953)
add_text('…はパーティから離脱した。')
```

- `QuestEndManager`（`__init__@6508` / `method_1@6511` / `execute@6634`。解散は 6602行）。
  別スレッドで走る。放棄側は `QuestRetireManager`
- 帰還は解散より先なので、解散の時点で `player.current_area` はもう町
- ゲーム側の置き先は `initial_location`（雇用された場所）

解散の中かどうかは**コードオブジェクトの同一性**で見る。
段数で数えない（`@ctx.wrap` の層が1段挟まる）。
関数名でも足りない（`method_1` / `execute` は12個のマネージャが持つ名前）ので
`frames.owner_of` が持ち主クラスを名指しする。
`move_npc_to_facility` ではスタックを見ない（NPC の日常の移動でも呼ばれるので、
「いま解散した相手か」を辞書で引くだけにする）。

### 2.9 クエスト

格納場所は2つある。**役割が違うので、書く前にどちらかを選ぶ**（§2.9.1）。

```
app.world.quests          {id: Quest インスタンス}   遊んでいるあいだの一覧
app.world_dict['quests']  {id: dict}                 世界の雛形
```

新規 id の検出は両者の合併を取る（どちらに登録されるかを決め打ちしない）。

#### 2.9.1 `world_dict` はセーブの中身ではなく世界の雛形（2026-08-26 に訂正）

以前ここには「`world_dict['quests']` がセーブに出るほう」「書くときは必ず両方」と
書いてあった。どちらも実測に反する。

| | 中身 | 書き出し先 |
| --- | --- | --- |
| `app.world.quests` | その周回の全依頼（実測 22件） | `saves/<世界名>/savedata.json` |
| `app.world_dict['quests']` | 世界を作った時点の依頼（実測 12件） | `worlds/<世界名>/world_data.json` |

同じ瞬間の実測（`206_` の記録と、両ファイルの復号。VERIFICATION_LOG.md §2.66）:

- `app.world_dict['quests']` は 12件、`savedata.json` は 22件。
  掲示板で作られた依頼（id 13 / 19 / 20 / 21）は**雛形の側に現れない**
- 状態も食い違う。同じ依頼が雛形では `incomplete`、セーブでは `completed`
- 雛形へ書いた難易度は `world_data.json` に焼かれる。
  **その世界で新しく始めた別のキャラクタにまで乗る**
- 生きた一覧（`app.world.quests`）へ書いた難易度は、画面には出るが
  `savedata.json` には出ない。
  **セーブがどこから依頼を組んでいるかは未特定**（VERIFICATION.md §3.38 の残り）
- `get_quest_difficulties(area, world)` は生きた一覧のほうを読む。
  ある土地で 5件返したとき、雛形にはその土地の依頼が 3件しか無かった
  （返った5件に、雛形に現れない id が2つ入っていた）。
  この関数は店の品揃え・値付け・敵の強さの源（§2.13.1.1）なので、
  **生きた一覧に書けばゲーム自身の計算に届く**

MOD からの書き方はこうなる。

- 遊びを変えたいだけなら `app.world.quests` にだけ書く。
  セーブに残らないので、MOD を外せば素のまま。
  残らないぶんは、次に読む場面で書き直す（`318_` がこの形）
- 依頼と難易度は雛形（`world_dict`）へは書かない。世界のファイルに焼かれる
  （NPC の採番台帳と施設は別で、`world_dict` へ書く手順が決まっている。§2.23 / §2.28）
- ローダの `ui.set_quest_value` / `ui.quest_stores` は**両方**へ書く。
  雛形に触れたくない MOD は使わないこと

掲示板（`DisplayQuestChoice`）のボタン:
`PhaseSpec('QuestChoiceManager', ('settlement_quest', '2'))` /
`PhaseSpec('QuestSearchManager', ())` / `PhaseSpec('JustSetButtonToNormalPhase', ())`。

- `quest_type` は `'settlement_quest'`。
  クエスト辞書の `quest_type` フィールド（`'normal_quest'` など）とは別の語彙で、
  セーブの値をそのまま渡すと `KeyError` で落ちる
- 依頼生成の入口は `QuestSearchManager`。
  `DisplayQuestChoice.generate_random_quest()` はエリアの生成・採番・登録まで面倒を見る内側の入口。
  内容に手を入れたいならさらに内側の
  `llm_manager_world_generate:random_quest_generator` の `area_description` に足すだけでよい
  （出力スキーマは1バイトも変わらない）
- 一覧はゲームに組ませるのが安全。
  `DisplayQuestChoice` を `process_choice` で開けば、組み立ても受け渡しもゲームがやる ＝ 語彙を知らなくてよい
- 受注できる依頼の絞り込みは `neighboring_settlement_id == 現在エリアの id` かつ
  `config['status'] == 'incomplete'`。
  ゲーム自身の `get_quest_difficulties(area, world)` と突き合わせられる
- `config` の鍵は `status` と `level_of_detail` の2つ。
  `status` で観測できている値は `'incomplete'` と `'completed'` の2つだけ
  （`206_` の census。実データで `{'incomplete': 18, 'completed': 9}`）。
  **放棄したときにどちらになるかは未実測**。
  片付いた依頼はセーブから消えず、`world.quests` に残る
- `QuestStructure`（生成の出力）は `quest_title` / `client_name` / `request_summary` /
  `client_statement` / `area` / `events` / `enemies` / `boss`。
  ゲームがこれに `difficulty` / `neighboring_settlement_id` / `id` / `quest_type` /
  `config` / `quest_area_id` を足して保存する
- 元からある依頼の `client_name` は**実在 NPC と結び付いていない**（世界生成時に付いた名前）
- **クエスト辞書に独自キーを足さない**（セーブに焼かれるうえ、
  再読み込み後に `Quest` インスタンスがそのキーを持つ保証が無い）。控えは `state/` に別ファイルで持つ

#### 進行ループ（2026-07-28、1クエストを頭から終わりまで実測）

```
DisplayQuestChoice
  → QuestChoiceManager(app, 'settlement_quest', '28')
  → quest_acceptance_choice   '受ける' = QuestStartManager(app, 'settlement_quest', '28')
  → QuestStartManager.start_quest()   → PhaseSpec('QuestPhaseManager', [])
       app.current_quest_data = <Quest object>
  → BattlePhaseManager / LootPhaseManager                       戦闘とその戦利品
  → QuestEventManager(app, event_name, enemies_info, event_turn) フィールドイベント
  → QuestEncounterFinalBoss(app, [[boss_name]])                  ラスボスとの邂逅
  → LootPhaseManager(app)  '漁る'     戦利品。完了より前
  → QuestEndManager(app)   '帰還する' ★完了。引数ゼロ
```

`帰還する` と `漁る` は完了の後に出るのではなく、**`QuestEndManager` を起こす側**。
`漁る` は `帰還する` の14分前に来ている（実測）。

`QuestEndManager.execute` の中で帰還・報酬・才能まで済み、
**抜けた先はエリアの入口**（`facility_type='entrance'`）。
入口の選択肢は隣の施設への `MovePhaseManager` だけで、
`DisplayTalkChoice` と `DisplayAreaMoveChoice` は無い。
「町に戻ったか」を後者2つで判定すると、プレイヤーが歩き出すまで拾えない（`307_` が実際に踏んだ）。

毎ターンの分岐を決めるのは `QuestPhaseManager.quest_referee_phase` →
`llm_manager:quest_referee_with_free_action`。
戻り値の `game_master_statement.turn_resolution` が進み先を決める。

#### referee のモデルは毎ターン組み直され、候補が尽きたものは作られない

`203_` の記録より、同じ1クエスト中の推移（`Battle.enemies` が `Literal[7]` → `[6]` → `[1]`、
`FieldEvent` は `Literal[2]` → `[1]` → **モデルごと消滅**）。

- `Literal` の中身は「残り」（倒した敵・消費したイベントは消える）。クエスト辞書の `enemies` 全体ではない
- **候補が0件になったモデルはゲームが union に入れない**（`FieldEvent` で実証）
  ＝ 空 `Literal[]` を避ける分岐は存在する。
  ただし0件を観測できたのは `FieldEvent` だけ
- **`ReturnAfterCompletion` は1ターン目から毎ターン作られている**（ボス健在でも union に居る）。
  したがって「攻略しないと帰還できない」はスキーマではなく**プロンプトの縛り**
  （referee の system に地の文として書かれている）

この性質のおかげで、討伐以外のクエストはプロンプトの差し替えだけで成立する
（ゲーム側のコードとセーブに触る必要は無い）。

#### フィールドイベントの成否判定（`credibility` と `<確率N%>`）

```
QuestEventManager(app, event_name, enemies_info, event_turn)
  → quest_referee_event_evaluate_new(...)  = field_event_evaluator
        result_type: certain_success / certain_failure / roll_required
        roll_required なら narration・credibility(1-10)・reference_attribute(6能力値)
  → ゲームが確率に変え、quest_event_log の末尾に `<確率N%: 成功>` を書き足す
  → quest_referee_event_resolve(..., outcome, ...)  結末と効果
```

`resolve` が受け取るのは成功・失敗の結果だけで、確率と能力値は渡らない。
**サイコロを振っているのは `QuestEventManager` の中**（コンパイル済みで読めない）。

実測（2026-06-06〜08-08、8キャラ、判定121回）:

- **確率が `credibility × 10 + 20` を超えたことは一度も無い**（上振れ0回）。
  半数（58回）はちょうどこの値で、残りは -2 〜 -40 の負の差だけが付く ＝ **この式は上限**
- 実際の成否は宣言どおりの確率で出ている（判定そのものは正直）。全体の成功率は 59.5%
- **`reference_attribute` は 6能力値から毎回選ばれているのに、その能力値が高いことで確率が上がった回は無い**
- `roll_required` 121 / `certain_success` 10 / `certain_failure` 1
  ＝ **入力の 92% がサイコロに回る**（「機械的」の実体）
- `credibility` は 4〜6 に 86/121 が集まり、10 は一度も出ていない
  （プロンプトが「手心を加えないこと」と念を押している側）
- `field_event_evaluator` のプロンプトに能力値は1つも書かれていない。
  LLM は能力値を知らないまま `reference_attribute` を選んでいる

負の差が何に連動しているかは未特定。
参照能力値の種類・キャラごとの固定値・HP や体力・クエストの難易度は
いずれも実データに反例があり、レベルとは緩く相関するが式にならない。
判定の瞬間の値はセーブにも LLM の記録にも残らないので実機で立ち会うしかない
（`215_probe_event_roll`。VERIFICATION.md §3.17）。

クエストの外の自由入力は別系統で、確率を LLM 自身が出す（§2.26）。

### 2.10 戦闘・フラグ

戦闘終了マネージャは3つあり、経路によって挙動が違う。

| マネージャ | 入口 | `end_phase` 完了時の `in_battle` |
| --- | --- | --- |
| `BattleEndManager` | 通常の戦闘 | 0（ゲーム自身が下ろす） |
| `BattleEndInFreeAction` | 自由入力・会話から入った戦闘 | 1（下ろし忘れ。main_024 で解消） |
| `BattleEndInColosseum` | コロシアム | - |

`in_battle` はセーブに入り、ロード時の分岐に使われる
（`instantale.py:1458` が戦闘BGM、`:1460` がエリアBGM）。
残ったまま保存すると次のロードが戦闘BGMで始まる。
MOD 側でも「戦闘中は出さない」条件に使われるので、残骸があるとイベントが出なくなる。
残骸かどうかは `app.current_enemy_dict` が空かで見分ける。

`in_boss_battle` はボス戦の後の戦闘（闘技場）で 0 に戻っていた（2026-08-29 に1回観測。`322_` のログ）。
`in_colosseum_battle` の 1→0 は未観測。

#### 1手ぶんの内訳（`BattlePhaseManager`）

```
battle(command, choice_text)
handle_battle_situation(character_key, character_side, battle_action)   1手ぶん
  calculate_battle_effect / resolve_battle_effect / process_battle_text
reduce_status_turns_and_log / check_character_death / check_team_annihilation
check_battle_end / enemy_delete_animation / convert_llm_output_to_instruction_dict
```

実測（2026-08-01、`308_` のログ）:

- **1手 = `handle_battle_situation` 1回**（味方の手も敵の手もここを通る）
- `character_side` は**日本語の文字列**（`'味方陣営'` / `'敵側'`）。列挙値ではない
- `character_key` は敵だと `'泥濘の亡者1'` のように連番付き。
  `Character.name` の側は連番が付かないので**鍵と表示名は別物**
- 1手で複数の敵に当たる手がある（スキル）
- 倒れた敵は1手の中で `current_enemy_dict` から抜ける。
  1手の前後で敵の状態を比べる MOD は、この抜けた敵を別に拾わないと取りこぼす
  （`308_` が実機1回目でとどめの一撃を落とした原因）

> 入れ子の順序（`calculate` → `resolve` → `process`）は署名から読んだだけで実測していない。
> `308_` はこの順序に寄りかからない形（1手の外側で HP の差を測る）にしてある。

ダメージの計算そのものは `scripts.functions` 側
（`get_base_damage_value` / `get_instant_damage`）だが、
引数は数値だけで誰に当たった値なのか分からない。
数字が欲しいだけなら HP の前後を比べるほうが確実。

| 誰 | HP の在り処 |
| --- | --- |
| 敵 | `app.current_enemy_dict`（鍵 → その敵。戦闘の実体の有無もここで見る） |
| プレイヤー | `app.player` |
| 同行者 | 名簿の id から `world.characters`（§2.8） |

`Character` 側は `current_hp` / `physical_integrity` / `max_physical_integrity`（実測）。
最大 HP は `update_max_hp()` があることから `max_hp` と推測しているだけで未実測。

### 2.10.1 戦闘の審判 LLM の語彙（output_data の実記録より）

1手の中身を決めているのは `scripts.llm.llm_manager_battle` の審判たち。
入出力は `output_data/<世界>/<PC>/<関数名>/N.json` に残る（§1.4）ので、
プロンプトもスキーマも遊んだ後から読める（2026-08-26 に実記録で確認）。

```
referee_player_attack_new_new(combat_log, actor, party, current_enemy_dict)   通常攻撃
referee_player_skill_new_new(..., skill, ...)                                 スキル
referee_player_any_input_new_new(..., command, ...)                           自由入力
referee_player_any_input_new_new_with_skill(..., command, skill, ...)         自由入力＋スキル
referee_enemy_new / referee_npc / referee_npc_rewrite                         敵と同行者の手
```

**自由入力の手にも専用の審判が居る**＝戦闘中のロールプレイの受け口は素のゲームにある。

スキーマ（`referee_player_attack_new_new` の実記録）の語彙は、殴る以外を最初から持っている:

| 型 | 中身 |
| --- | --- |
| `SkillEffect` | `effect_id` / `targets`（名前の Literal）/ `modifications` |
| `PowerModification` | `power_increase` か `power_decrease` × `small` / `medium` / `large` |
| `InstantDamage` / `InstantHeal` | `target` / `power`＝`weak` / `normal` / `strong` / `very_strong` / `extreme` |
| `TextStatusEffect` | 名前付きの状態異常。`duration` 3〜5・`intensity` 1〜5・`effects_per_turn`（毎ターンの damage / heal） |
| `RemoveTextStatusEffect` | 名前を指した解除 |
| `AttributeEffect` | `enhancement` / `reduction` × 6能力値（str/dex/con/wis/int/cha）× power 5段 |

戻りの根は `narration` / `skill_effects` / `additional_effects` / `vfx`（8種の Literal）。
審判は頼まれなくても状態異常を出す
（通常攻撃の記録で、敵の反撃として `TextStatusEffect`（泥濘の拘束、duration 3、
毎ターン weak damage）がプレイヤーに付いた実例）。

システムプロンプトは「TRPGの戦闘のダメージ計算を中立の立場で管理する役。
場の状況で同じ技でも結果が変わる」という建て付けで、
プレイヤー情報（HP・profile・traits・武器・防具）と敵の情報・戦闘ログが user 側に載る。

語彙 → 数の変換は §2.10.2。

### 2.10.2 語彙 → 数の変換（2026-08-26 に `222_` で実測。1クエスト・戦闘6回）

生ログと数表は VERIFICATION_LOG.md §2.68。1手の数の流れは3段:

```
convert_llm_output_to_instruction_dict     審判の戻りを平らにする
calculate_battle_effect(battle_action)     素点を作る（instantale.py:7057）
resolve_battle_effect                      防御を引いて HP に当てる
  resolve_allies    (instantale.py:7126)   味方被弾: get_instant_damage(素点, 500)
  resolve_opponents (instantale.py:7262-4) 敵被弾:   get_instant_damage(素点, get_npc_defense())
```

変換後の `battle_action` は審判のスキーマより平らで、鍵は
`actor` / `narration` / `vfx` / `instant_damage` / `instant_heal` /
`text_status` / `escape_from_battle` / `other_action`。
`instant_damage` の1件は `{target, category(physical|magical), power, multiplier}`
（`multiplier` は 0.67 / 1 / 1.5 を観測。審判の `modifications` がここに畳まれる）。
`TextStatusEffect` の `intensity` と `effects_per_turn` は**変換後には現れなかった**。

#### 攻撃（`calculate_battle_effect`）

- 基礎値 = **2 × 幾何平均(character_attack, weapon_attack)**。
  `get_base_damage_value` が `statistics.geometric_mean` を呼ぶ
  （0 を渡すと StatisticsError。実測 390・500 → 883.176 = 2×√(390×500) が一致）
- プレイヤー（能力値オール30・武器500）の実引数は毎手 (390, 500) で不動。
  390 = 13×30 と読めるが、このキャラは全能力30なので**どの能力かは切り分け不能**
- **`get_base_damage_value` を通るのはプレイヤーの手だけ**。
  敵も同行の仲間も通らない（VERIFICATION_LOG.md §2.68 / §2.69。仲間の全手が `base=-`）。
  これは仕様。**NPC は武器を参照しない旨、公式の回答がある**
  （過去の問い合わせへの返答。実測と一致するのでここに残す。
  この文書で実測でない出どころはこの1件）。
  NPC の素点（weak 194〜489 など）がどこから来るかは未計測（武器でないことだけ確定）
- power × multiplier は基礎値に対し weak×1.5 で ×0.92〜1.08、normal×1 で ×1.24 を観測。
  同じ組でも ±10% ほど散る（素点側に乱数がある）。表を出すには通り数が足りない

#### 防御（`get_instant_damage(attack, defense)`）

形は引き算:

| | defense ≤ attack/2 | それより深い防御 |
| --- | --- | --- |
| ダメージ | **attack − defense**（正確に一致） | 緩い曲線で 1 まで落ちる。attack=defense で ≈0.29×attack |

- 敵の防御は `get_npc_defense()`（レベル36で 96〜139。能力値との式は未特定）
- 味方の防御に渡った実値は 500 ＝ 防具の`防御力`と読める
  （`get_npc_defense(プレイヤー)` は 390 で、使われたのは 500 のほう。
  装備を変えた切り分けは未実測）

#### 大味さの実体（この帯の実測）

レベル60・試験装備のプレイヤーで、素点 816〜1105 − 敵防御 ~100 ＝ **ダメージ 701〜1002**、
敵 HP は 428〜788 なので毎回一撃。
逆に敵の weak は素点 163 − 防具 500 ＝ **ダメージ 1**（雑魚は無傷）、
ボスの extreme だけ素点 850〜1027 − 500 ＝ 389〜527 が通る。
つまり大味の実体は**引き算の防御**と、素点・防御・HP の帯の食い違い。
LLM の power の選択は extreme の端でしか意味を持たない。

#### 効かないもの（実測）

- **`text_status` は文章だけ**。「泥濘の拘束」（duration 3）は
  `Character.status` 辞書に `{status_name, description, duration}` で書かれたが、
  直後の自陣の素点は不動・毎ターンのダメージも無し（観測1件）
- **自由入力の防御姿勢は数に落ちない**。narration は防御の描写になるが
  効果リストは全部空（観測1件）。被弾を減らしたのは防具の500だけ
- 敵はデバフ持ち（灰の霧=dex低下、忘却の歌=wis低下）だが、
  素の戦闘では1手目で倒れるので**使う暇が無い**。
  戦闘が複数手になった環境（`319_`）では審判が `AttributeEffect` を
  実際に出した（2026-08-27、耐久低下。VERIFICATION_LOG.md §2.71）。
  **素のゲームで捨てられる**こと自体は変わらない

### 2.11 BGM

```
play_music_from_src(app, src)   app.music に差し替えて再生
stop_music(app)                 app.music を止める
apply_music_volume(app)         main_023 で追加
```

`SoundManager` のメソッドは実測で6つ（上記＋`play_sound` / `play_sound_from_src` / `stop_sound`）。
`106_` が包んでいるのは4つで、`apply_music_volume` は `app.music` を差し替えないので包んでいない。

`app.music` が「今鳴っている曲」の唯一の取っ手。
ここを失った曲は誰にも止められなくなり、プロセスが終わるまで残る。
チャンネルは8本しかないので、埋まると効果音も鳴らせなくなる。
`BattleEndInFreeAction` の復帰呼び出しは app ではなく別のオブジェクトを渡してくるので、
`app` を受け取る関数を包むときは、渡されたものが本当に app か確かめること
（`getattr(x, k, "<missing>")` で「属性が無い」を `None` と区別して記録する）。

音の状態は自前の帳簿ではなく pygame に聞く。
`Sound.get_num_channels()` と `pygame.mixer.Channel(i)` で今どの音が鳴っているかが分かる。
曲は再生のたびに `Sound(パス)` で作られるので、
`SoundManager.sounds`（起動時に読む15種）と `play_sound*` の分を除外集合にすれば、
残ったループ音は曲と言える。

止められなくなった曲は、止めずに `app.music` へ入れ直すと音が途切れず、
しかも以後ゲーム自身の `stop_music` が効くようになる。

#### エリアBGM

パスはエリア生成時に確定し、セーブに焼き込まれる:
`areas["7"]["bgm"] = "Assets/sounds/musics/town/solemn/Ambient 7 Loop.mp3"`。
フォルダ2段が判定結果で、1段目は `town|village|city|dungeons|battle` ＝ `area["size"]`、
その下が雰囲気（`calm` / `eerie` / `majestic` …）。

- **フォルダの決定権は `area["size"]` にあり、保存済みパスから取ってはいけない**
  （誤ったフォルダに入ったエリアが永久にそのフォルダ内で再配分され続ける）
- 表記揺れ: エリアは `dungeon`（単数）、フォルダは `dungeons`（複数）
- `musics/` 直下の単発曲を指しているエリアは意図的な指定とみなして触らない
- どのフックで `bgm` が確定するかは特定できないので、
  どれが何回発火しても結果が変わらない書き方にして全部に仕掛ける
  （実測では `save_world_json:write_obfuscated_json_file` だけが発火した。VERIFICATION.md §3.4）

**乱数は MOD 専用の `random.Random` を使う**（グローバルから引くとゲーム自身の乱数列がずれる）。

#### 戦闘BGM

戦闘曲は `instantale.py:6995` の lambda（`Clock` 経由、MainThread）が `play_music_from_src` に
`Assets/sounds/musics/battle/1. Echoes of Valhalla.mp3` を固定で渡して鳴らす
（2026-08-21〜22 の実機ログ 13 回、全て同一）。
戦闘の種類はパスには現れず、曲が鳴る時点のフラグでだけ見える:

| 種類 | 曲が鳴る時点のフラグ | `BattleStartManager(app, enemy_type, ...)` の `enemy_type` |
|---|---|---|
| 通常（依頼中の遭遇） | `in_battle=1` | `'in_quest'` |
| ボス（`QuestEncounterFinalBoss`） | `in_battle=1 in_boss_battle=1` | `'in_quest'`（通常と同じ語） |
| 闘技場（`ColosseumMatchStart`） | `in_battle=1 in_colosseum_battle=1` | `'colosseum'` |
| 衛兵 | `in_battle=1` | `'guard'`（§2.20） |

（2026-08-08 に2戦、2026-08-29 に7戦。`322_battle_bgm` の `[BGMPICK]` の行）

`play_music_from_src` は絶対パスをそのまま受け付ける（ゲームのフォルダの外に置いた曲が鳴った）。
`106_` の戦闘曲判定は `/musics/battle/` の部分一致なので、外に置く曲もそのフォルダ名の下に置けば戦闘曲として扱われる。

### 2.12 LLM 経路とプロンプト

```
llama_cpp_runtime_completion:LlamaCppClient.chat                             上流
llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template             messages
llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry   payload
scripts.llm.request_llm_inference_llama_cpp_completion:send_request*         ローカル
scripts.llm.request_llm_inference_gemini_test_streaming:send_request*        Gemini（実測）
scripts.llm.request_llm_inference_openai:send_request*                       OpenAI（実測）
scripts.llm.request_llm_inference_claude:send_request*                       Claude（実測）
scripts.llm.request_llm_inference_any_server:send_request*                   任意互換（推定）
scripts.llm.llm_manager:*                                                    マネージャ群
```

#### プロバイダは1つだけ import される

**外部APIキー（クラウド）はプロバイダごとの `request_llm_inference_*` を通り、
`LlamaCppClient` を一切通らない。**
使われるモジュールは1つだけが import され、残りは `sys.modules` に載らない
（Gemini セッションの実測では gemini だけが載り、llama_cpp と any_server は `[not loaded]`）。

この性質はローダ側の判定にも使っている。
クラウドと分かった時点でローカル専用の保留を降ろす（`llm.is_cloud_runtime()` / TECH.md §3.4）。
入れる前は `llama_cpp_runtime_completion` 宛ての保留14件が永久に残っていた。

プロバイダごとの内部（実測・2026-08-08）:

| | Gemini | OpenAI / Claude |
| --- | --- | --- |
| 境界 | `send_request(manager_name, message, structure, model=None, max_tokens=30000, timeout=None)` | 同形 |
| クライアント | `google.genai` の `Client` 直 | `openai.OpenAI` / `anthropic.Anthropic` 直 |
| 既定モデル | `gemini-3.5-flash` | `gpt-5.4-nano` / `claude-sonnet-5` |
| 特徴 | ストリーミング収集（`_stream_and_collect`）、pydantic 検証に失敗した部分木だけを修復するループ（最大3周）、**`SCHEMA_IN_PROMPT = True` でスキーマ文を `send_request` の中で足す** | 修復ループもスキーマ埋め込みも無い（構造化出力を API に任せる） |

#### プロバイダに依存しない仕掛け方（`111_` v4 のパターン）

送信モジュールを名指しせず、`llm_manager:send_request` /
`:send_request_with_no_structure` を包む。
この2つは**使われる送信モジュールから from-import した別名**なので、
alias_scan が同じ関数を持つ全モジュールを張り替え、どのプロバイダでも1箇所で効く。
プロバイダ名はラップした元関数の `__module__` から採れる。

罠が2つある。

- ローカル実行では、この地点で本文を触ってはならない。
  `send_request` は内部で別スレッド（`send_request_on_id`）に降りてから `LlamaCppClient` を呼ぶため、
  スレッド頼みの一回制御が効かず `chat` 側のフックと二重適用になる
  （`111_` は llama.cpp の送信モジュールが import されているかで見分けて素通しする）
- この別名はプロバイダの初期化時に生える。
  起動直後に注入すると `llm_manager` は import 済みなのに `send_request` がまだ無い
  （モジュールの有無ではなく属性の有無）。
  ローダの保留機構はモジュール単位なので、無かった別名は見張って生えた時点で包む

#### 仕掛け位置と件数の読み方

- ストリーミング経路は `_post_with_model_loading_retry` を通らない。
  `prompt` と `json_schema` が揃う唯一の地点はそこだが、実際に流れるのは `chat` 側。
  位置が確信できないときは、判定条件を保ったまま複数箇所に仕掛ける
- その代償として、**ログの件数はそのまま呼び出し回数にならない**。
  1回の推論が複数のフック地点から記録されるので、行を数えると実際の3〜4倍に膨らむ
  （実測: 146行 → 相異なる秒は 41）。**数えるならタイムスタンプで一意化してから**
- `output_data/` の記録は `LlamaCppClient.chat` より上流で取られる。
  保存は `send_request*` の中（`save_output_log`）なので、
  `chat` を包んで書き換えた内容は記録に一切映らない。
  `chat` に仕掛けた MOD の判定は、その MOD 自身のログで行うこと
  （VERIFICATION_LOG.md §2.43 も参照）
- `manager_name` を自前の名前にすると、自前のプロンプトも
  `output_data/<世界>/<PC>/<manager_name>/N.json` に残る

#### 本文の形

- プロセス内の本文は復号済み。
  プロキシが見ていた HTTP ボディでは改行が `\n` の2文字、日本語が `\uXXXX` だった。
  ボディの形を前提に書かれた文字列（プロキシ用の置換ルールなど）はそのままでは当たらない
- `message` は必ずリストで渡す。
  引数名は単数形だが中では `自前のリスト + message` をしているので、
  素の文字列を渡すと `send_request_on_id` で `TypeError`。形は `[{"role": "user", "content": ...}]`

  > この例外は呼んだ側に飛んでこない。
  > ゲームが内部で立てた別スレッドで起きるので、**呼び出しは永久に返らないという形でしか現れない**。
  > `send_request` を呼ぶ MOD は「返ってこない場合」を必ず自分で面倒を見ること。
- `send_request_with_no_structure` は `str` を返す（`output_data/` の `{"text": ...}` は保存側の形式）
- `quest_event_log` はリストではなく文字列。区切り（「〈プレイヤーの入力〉」）で割る
- `messages` の重複は完全一致・隣接で現れる。`(role, content)` の比較で落とせる
- ゲームは `json_schema`（grammar の実体）と同じスキーマを、
  Python dict の repr としてプロンプト本文にも埋め込む。
  構造は grammar がトークン単位で強制するので、
  本文側に必要なのはフィールド名・enum 候補・参照先の型名だけ:

  ```
  元:   {'$defs': {'Location': {'properties': {'name': {'title': 'Name', 'type': ...
  後:   Location: name, kind:∈{shop,inn}
        Area: name, locations:Location[], atomosphere:∈{tense,normal}, note?
  ```

- `ast.literal_eval` は使えない（式1個しか受け取れず終端位置を返さないので、
  プロンプトの途中から読み始めて置換範囲を決められない）。再帰下降パーサが要る

#### クエスト1件に関わるマネージャ

| マネージャ | 役割 | 討伐固定の文言 |
| --- | --- | --- |
| `random_quest_generator` / `settlement_quest_generator` | クエスト構造の生成 | **有** |
| `quest_starter` / `_with_party` | 開始ナレーション＋初期選択肢 | 無 |
| `quest_referee_with_free_action` / `quest_referee` | 毎ターンの進行判定 | **有** |
| `field_event_evaluator` | 入力を「確定」か「確率判定」に振り分け（§2.9） | 無 |
| `quest_referee_event_resolve` / `_event_rewrite` | イベントの結末と効果 | 無 |
| `quest_summarizer` ほか | 帰還後の要約 | 無 |

つまり**討伐前提が書かれているのは生成と進行判定の2箇所だけ**で、
イベント処理と描写はクエスト種別に依存していない。

#### サイドカー

`LlamaCppSidecar` は5メソッド（`__init__` / `start` / `_kill_existing` /
`_find_free_port` / `_wait_for_ready`）。
`start` の `additional_params` はリストなので `--parallel 1` の追加は容易。
InstantaleLLMProxy と併用する場合、多重起動抑止だけは所有者調停が競合する
（プロキシ側を `singleton_enabled=0` にする）。
DEDUP / COMPACT / EVENTLOG は二重に適用しても結果が変わらないので併用してよく、
プロキシ側のログに出たら MOD の取りこぼしという検出器になる。

#### 2.12.1 起動引数は設定欄から全部は届かない（実測・2026-08-12）

設定画面の「サーバーパラメータ」欄は `config.json` の
`ai_setting.server_parameters.<バックエンド名>` に入り、`llama-server` のコマンドラインへ繋がれる。
ただし `--ctx-size` だけが取り除かれる:

```
欄に書いた値   --n-gpu-layers 999 --parallel 2 --ctx-size 32768 --cache-reuse 256
実際のCLI      ... --ctx-size 16384 ... --n-gpu-layers 999 --parallel 2 --cache-reuse 256
                            ^^^^^ ゲームの値が残る          ^^^^^^^^^^ 他はそのまま渡る
```

書く欄を間違えやすい。2次元（バックエンド × 種別）で、効くのは1つ:

- 行 = いま選んでいるバックエンド（実機では `llama-cpp-completion-cuda`。
  `self_server` は「任意 OpenAI 互換サーバー」用でローカル実行では読まれない）
- 列 = `server_parameters`（`environment_setting` は環境変数なので引数を書いても渡らない）

ファイルも2つあり、ゲームが読み書きするのは
`%LOCALAPPDATA%\Darmabeko\Instantale\config.json`（インストール先のものは初期テンプレート）。

#### 2.12.2 `--parallel` は KV の持ち方を変える（実測・2026-08-12）

書かなければ `n_parallel=auto` が選ばれて**統合 KV**（4スロットが1つのプールを共有）になる。
明示すると統合が外れ、スロットごとに専用のプールを取る（起動ログの `kv_unified` に出る）。

| 起動引数 | `kv_unified` | `n_ctx` | `n_ctx_seq`（1リクエストの窓） |
| --- | --- | --- | --- |
| `--ctx-size 16384`（ゲームの既定） | true | 16384 | 16384 |
| `--ctx-size 16384 --parallel 1` | false | 16384 | 16384 |
| `--ctx-size 65536 --parallel 4` | false | 65536 | 16384 |
| `--ctx-size 32768`（`--parallel` なし） | true | 32768 | 32768 |

**`--parallel` を書かなければ窓は縮まない**（指定値がそのままスロットごとの窓になる）。
明示したときだけ `n_ctx ÷ parallel` に割られる。
統合に戻すには旗が1つも無い状態にする必要がある。
確保量への影響は VERIFICATION_LOG.md §2.48。

### 2.13 インベントリのグリッド

所持品・売買画面（twin inventory）は `scripts.hud.new_hud:InventoryGrid`。

```
InventoryGrid   cols=4  rows=6  len(slots)=24  size=[259, 389]  spacing=[1, 1]
                situation=None（所持品） / 'shop'（売買）
アイテム        width_slots / height_slots / size=[64,64]（1マス）/ [129,129]（2x2）
                current_slots=[17, 21, 18, 22]   ← 占有マスは添字の配列で持つ
```

`grid_x` / `grid_y` / `slot_size` は**アイテムの属性としては存在しない**（`<missing>`）。
位置は `current_slots` の添字とピクセル座標で持っている。

ゲーム自身が持っている配置の道具:
`is_valid_placement` / `find_placement_position(w, h)` / `place_new_item(item)` /
`place_existing_item(item, x, y)`（**置けるか確かめずに `occupy_slots` を呼ぶ**）/
`occupy_slots`（範囲外で `IndexError`）/ `item.clear_current_slots()`。

**復元位置は必ずしも収まらない**（画面ごとにグリッドの寸法が違いうる）。
座標を計算し直すのではなく、はみ出したら
`find_placement_position` → `place_new_item` に流すのが安全。
`toggle_twin_inventory_visibility` は Kivy の property dispatch → Clock コールバックの中で走るので、
ここで例外が出るとアプリのループまで抜けてゲームごと落ちる。

### 2.13.1 店の品揃え（`ShoppingStartManagerRemake`）

店に並ぶのはその施設の主の持ち物そのもの。
売買画面は主とプレイヤーの2つの持ち物を左右に並べているだけで、
店専用の在庫という入れ物は無い。

```
ShoppingStartManagerRemake.execute / .shopping_start_method_1
                          .set_item_from_world_data(shop_owner_instance, next_tier)
                          .generate_item_in_shopping(item_data, shop_owner_instance, item_stock_tier)
InstantaleApp.toggle_twin_inventory_window(left, right, left_label_text, situation, *args)
InstantaleApp.buy_item / sell_item / set_shop_price_for_owner / set_shop_price_for_player
InstantaleApp.normalize_shop_inventory_prices(shop_obtainer, player_obtainer)
```

- 主の持ち物はセーブの `npcs[<id>].inventory`。
  実セーブでは51人中8人だけが中身を持っていた（**中身を持っているのは店として開いた施設の主だけ**）
- つまり品揃えは「初めて開いたときに作られて、そのまま残る」。
  入れ替える仕組みは見当たらない。
  プレイヤーが売った品も `sell_item` で主の持ち物に積まれるので、
  24マスが埋まると売却そのものができなくなる
- `next_tier` / `item_stock_tier` は品物の段。**整数**で、
  `get_area_quest_difficulty_for_tier(area, world, tier)` に渡って
  その土地の依頼の難易度に変わる（§2.13.1.2）。
  **どう決まるかはまだ出ていない**（実測1点）ので、
  `312_` はゲームが渡す値をそのまま使い回して解釈しない
- **主の持ち物を空にしてから売買を始めると、ゲームが初回と同じ経路で品揃えを作り直す**
  （実機で成立。`cleared` → `restocked` が4店舗6回、`WARN not refilled` は0件）

#### 2.13.1.1 品揃えの段はその土地の依頼の難易度（実セーブ3世界・店23軒）

店に並ぶ品の `value` は、その土地の依頼の難易度以外の数を取らない。
`world_data.json` を直に読んで、
持ち物を持っている主の居る施設を全部突き合わせた結果:

| 世界 / 土地 | その土地の依頼の難易度 | 施設（tier） | 在庫の `value` |
| --- | --- | --- | --- |
| ペルディション 2 | 27 / 30 / 31（3件とも完了済み） | general_store（standard） | 5,5,27,27,30,30,31,31,31 |
| 〃 | 〃 | medical_facility（basic） | 27,27,27,27,27,30,30,31 |
| 〃 | 〃 | specialty_shop（basic） | 27,27,27,27,30,30,30,30,31 |
| ペルディション 3 | 48 / 54 / 55（3件とも完了済み） | specialty_shop（advanced） | 48,48,48,54,54,54,54,54,55,55 |
| 暮影裂界 1 | 5 / 10 / 12（10 だけ完了済み） | general_store（basic） | 5,5,5,10,10,10,10,12 |
| 暮影裂界 4 | 17 / 18 / 21 | medical_facility（advanced） | 17,17,17,17,18,18,21 |
| Astergrave 7 | 42 / 44 / 45 | general_store（advanced） | 42,44,44,44,44,44,45,45 |

- 品物 190個のうち 186個が、その土地の3つの難易度の**いずれか**そのもの。
  外れた4個は2軒に集まっていて（上表の 5,5 を含む）、
  どれもその土地には無い難易度。プレイヤーが売った品が主の持ち物へ積まれる経路
  （上の売買の項）で入ったものと読める
- **完了済みの依頼も母数に入る**。ペルディションのエリア 2 / 3 / 8 は
  3件とも `config['status'] == 'completed'` だが、在庫はその難易度で並んでいる
  （`get_quest_difficulties` の `include_completed=True` が既定）
- **施設の `tier` は在庫の段を1つに絞らない**。`basic` の店にもその土地の最高難度の品が並ぶ。
  値は `basic`（140）/ `standard`（138）/ `advanced`（36）の3語だけで、
  6世界の施設 1,244件を数えて他は出ていない
  （`entrance` / `ward` / `dungeon_location` など主の居ない施設は `None`）
- ゲーム側の入口は `get_area_quest_difficulty_for_tier(area, world, tier)` と
  `get_quest_difficulties(area, world, include_completed=True)`

#### 2.13.1.2 品揃えを作る経路（実測。VERIFICATION_LOG.md §2.67）

主の持ち物が空の店を開いたときに走る:

```
generate_item_in_shopping(item_data, shop_owner_instance, item_stock_tier=2)
  get_area_quest_difficulty_for_tier(area, world, 2) -> 33
  get_weapon_spec(33) -> 145
  get_equipment_price(33) -> 1021
  get_item_skill_usefulness(33, 'common') -> 10
```

- **`tier` は整数**（実測 2）。施設の `basic` / `standard` / `advanced` ではない。
  返った 33 は `get_quest_difficulties` の並び `[35,34,33,…]` の3番目と一致するが、
  **実測1点なので式は確定していない**
- 品揃えを作っているのは `set_item_from_world_data` ではなく
  **`generate_item_in_shopping`**。
  `312_` が `set_item_from_world_data` の `tier` を控えようとして
  ずっと `null` だったのはこのため
- `generate_item_in_shopping` は `None` を返す。
  作った品はそこから受け取るのではなく主の持ち物へ入る
  （`generate_enemy_instance_from_quest_dict` と同じ形。§2.20）
- ここへ渡る難易度は `get_quest_difficulties` の答えそのものなので、
  **生きた一覧（`app.world.quests`）を書き換えれば品揃えの段が動く**（§2.9.1）。
  `318_area_difficulty_growth` がこれで街を育てている

> つまり**その土地の物価と品揃えを動かす道は、依頼の難易度1つ**。
> `318_area_difficulty_growth` はこれを使って、在庫にもクラフトにも触らずに街を育てる。
> 効き始めるのは品揃えが入れ替わってからなので、`312_shop_restock` と組で意味を持つ。

#### 店の主は `job` が施設の種類と一致している

実セーブで、主の居る施設67件を全部突き合わせた結果:

| | |
| --- | --- |
| `job` が `facility_type` と一致 | 66件 |
| 食い違い | 1件だけ（`general_store` の主が `job='other'`） |

食い違っていた1件はセーブエディタで足した店だった。
ただし売買が開けなかった原因ではない。
`general_store` へ直しただけでは症状が変わらず、
開いたのは素データを揃えてからだった（§2.28）。
`job` を `other` に戻したまま素データだけ揃える形は試していないので、
「無関係」とまでは言えない。
`job` の値は `facility_type` と同じ語彙を使う
（`inn` / `general_store` / `specialty_shop` / `blacksmith` /
`medical_facility` / `administrative_office` / `underworld_office` / `guild`）。

> NPC を店の主にする MOD は `job` を施設の種類に合わせておくこと。
> 揃えない理由が無く、ゲームが作る形が66件そう揃っている。
> 品揃えを持っていた13人（＝店として開いたことがある NPC）も全員 `job` が店系だった。

日付は世界に1つ（`world.days_elapsed`。セーブでは `world_data.days_elapsed`）。
進めているのは `InstantaleApp.elapse_days(days)`（§2.18）。

### 2.13.2 アイテムの値付け

分類は2段。粗いほうが `item_type`、細かいほうが `attributes` の中の `item_detail`。

```
scripts.items:Item.__init__(self, name, item_type, attributes, description,
                            value, size, image_src, rarity, skill, obtainer,
                            id, grid_pos=None, upgrade_level=0)
```

| 段 | 値 | どこから分かるか |
| --- | --- | --- |
| `item_type` | `weapon` / `wearable` / `healing_item` / `consumable` / `utility` / `material` の6種 | 店の品揃え生成の構造化出力スキーマ |
| `attributes["item_detail"]` | `small_weapon` `body_armor` `magical_material` … 32種 | 同スキーマの `sub_type` と `Assets\images\item_candidates_dark\` のフォルダ名 |
| `rarity` | `common` / `rare` / `magical` / `epic` / `legendary` / `mythic` の6段 | 同スキーマ |
| `value` | 1〜70 の価値段階 | 同スキーマ（「最低が1で最高が70」と書かれている） |

> `sub_type` と `item_detail` は綴りが揃っていない（`weapon/small` は `small_weapon`、
> `herb` は `plant`）。**表の鍵にするなら `item_detail` のほうを取る**（アイテムに書かれているのはこちら）。

`value` はその品が出たクエストの難易度と一致する（実セーブの21種類すべてが
その世界の `quests[*].difficulty` かより深い土地の難度に対応）。
この段階を返すのは `get_equipment_price(quest_difficulty)` / `get_heal_item_price` /
`get_other_item_price` の3つで、**gold ではない**。
gold に直すのは `get_item_base_price` と `get_randomized_item_price`。

#### 値段は `attributes` に書かれている

**`買価` と `売価` は排他**で、どちらか一方しか書かれない（実セーブ151個で例外なし）。
店の持ち物には `買価`、売買画面に出したプレイヤーの持ち物には `売価`。
説明欄（§2.14）もここを読んで描く。

`attributes` の並びは `item_detail` → 能力値 → 値段。
能力値は種別ごとに違う（`weapon`＝`攻撃力` / `wearable`＝`防御力` /
`healing_item`＝`回復` と `疲労負荷` / それ以外は無し）。

#### レア度が値段に効いていない

実セーブ（`ヴェスティア`、Lv31 / 3651日）から拾った実額:

| 品 | `value` | 能力値 | 買価 | 売価 |
| --- | --- | --- | --- | --- |
| 短剣 common | 3 | 攻撃力 23 | 72 | - |
| 短剣 common | 20 | 攻撃力 96 | 463 | - |
| 短剣 magical | 48 | 攻撃力 245 | 1,831 | - |
| 魔法素材 magical | 24 | - | 128 | - |
| 魔法素材 mythic | 24 | - | - | 19 |
| 財宝 mythic | 66 | - | - | 102 |

- **買価はレア度でほとんど動かない**（同じ `value` 24 の魔法素材が magical で128、mythic で19）
- **売価は `value` とほぼ同じ数字**（能力値を持たない品では `売価 ≒ value × 0.8〜1.0`）。買価の6分の1ほど
- 買価は能力値に対して上に反る（攻撃力 23 → 72 で 3.1倍、245 → 1,831 で 7.5倍）

#### 物価の目安

宿の主の台詞（実プレイのログ）がこの世界の物価をそのまま言っている:
`簡易寝台なら10G、個室なら100G、…高級個室も1000G`（3ヵ月単位の長期滞在、前払い）。
比較用に、NPC の雇用は難易度76 で 5,045G、実セーブのプレイヤー所持金は 1,116,472G。
**ゲームで一番高いアイテム（2,342G）より、宿の高級個室2部屋ぶんのほうが近い**という開きがある。

### 2.14 アイテム詳細ボックス

ホバーで出る `ItemDetailBox`（window=2560x1387 のときの実測）:

```
ItemDetailBox      size=[333, 500]  size_hint=(None, None)      ← 箱ごと固定
  name_label       height=50   text_size=[316,  50]  pos_hint={'center_x':.5,'top':0.95}
  attributes_label height=225  text_size=[316, 225]  pos_hint={'center_x':.5,'top':0.85}
  desc_label       height=150  text_size=[300, 150]  pos_hint={'center_x':.5,'top':0.40}
                   font_size=27  halign=center  valign=top  max_lines=0
```

- `max_lines=0`（無制限）なので、**切れているのは行数制限ではなく `text_size` の高さ**。
  300px 幅・`font_size=27` で半角24文字/行、150px で3行 = 72文字までしか描かれない
- 子の位置は `pos_hint` の `top` 分数で箱の高さに追従する。
  箱を伸ばせば中身は自分で並び直すので、座標を1つずつ計算し直す必要は無い
- 分数を実寸に直すと `上余白25 + 50 + 225 + 150 + 下余白50 = 500`。
  ラベル間の隙間は0で、見た目の余白はラベルが自分の高さの中に持っている
- 箱はホバーのたびに作り直される。
  `update_content` の時点で箱の `pos` は入っているが、子はまだ一度もレイアウトされていない。
  **レイアウト前の絶対座標から設計を読んではいけない**（余白は `pos_hint` の分数と箱の高さから求める）
- 箱は上端を固定して置かれている。高さを変えるときは上端を保って下へ伸ばす
- 箱は `pos_hint` を持たず、位置は `update_content` の後で誰かが直接入れている。
  伸ばした箱を画面内に収めたいなら次のフレームで行う

**文字が要求する高さの測り方**: `text_size` を `(元の幅, None)` にして `texture_update()` を呼ぶと、
折り返した結果が `texture_size[1]` に出る。幅はこちらで決めず、ゲームの値のまま使う。

### 2.14.1 自由入力のアイテム一覧（`ToolListPopup`）

入力欄の左下のアイコン（`press_item_icon` / `press_skill_icon`）で開く一覧。
選ぶと `select_item_to_action_input(btn)` が入力欄へ差し込む。

```
scripts.hud.new_hud:ToolListPopup(callback, tool_text_list=[...])
    bases = [GridLayout]                     ← 列を持てる

cols=1  rows=None  spacing=[0,0]  padding=[0,0,0,0]
size_hint=[1,1]  pos=(0,0)  size=[926.64, 78.75]   ← 親（入力欄の帯）と同じ寸法
minimum_height=1026                                 ← 中身が要求する高さ
行 18個  173x57  行の下端 y=0 → 上端 y=1026        ← 箱の外まで並んでいる
```

- `GridLayout` 派生なので `cols` を増やせば折り返すはずだが、**`cols=2` を入れても見た目は変わらなかった**。
  `size_hint=[1,1]` で箱は 78.75 しか無いのに行は 0〜1026 に並んでいる
  ＝ **行の位置をこの格子が決めていない**。
  列にするには `cols` を入れたうえで `rows` を外し、箱の高さを中身ぶんにし、
  それでも折り返らなければ行の位置を自分で入れる（`115_`）
- 一覧の幅（926.6）は行の幅（175）よりずっと広いので、2〜4列なら幅は足りる
- 一覧は入力欄を下端にして上へ積まれるので、件数が増えると画面の上端を突き抜ける
  （Kivy の `DropDown` ではないので自動縮小も働かない）
- 開いた直後は、まだレイアウトが走っていない寸法が読める。
  しかも行が並び終わっているのに入れ物の矩形だけが `(0, 0, 926.6, 78.75)` のままという瞬間がある。
  **組み上がったかどうかは行の位置と高さで判断すること**（入れ物の矩形を条件にすると永久に成立しない）

### 2.14.2 クラフト画面（`craft_inventory_*`）

所持品・材料・生成先の3つのグリッドと、そのあいだの矢印・「作成」ボタン。

```
hud.craft_inventory_layout                  窓ぜんたい
hud.craft_inventory_generate_button         「作成」。枠線を持つ
hud.craft_inventory_generate_arrow_label    「→」
```

| | |
| --- | --- |
| 開閉 | `InstanTaleHUD.toggle_craft_inventory_visibility` / `InstantaleApp.toggle_craft_inventory_window` |
| 押下の紐付け | `InstanTaleHUD.set_craft_generate_button_callback(callback_function)` |
| 生成 | `craft_generate_item` → `ItemCraftManager` → `llm_manager:item_craft_generator(material_list, prompt)` |
| 後始末 | `place_crafted_item` / `remove_craft_materials` |
| 進行中の旗 | `app.is_crafting_item` / `app.item_craft_lock` |
| グリッド | `InventoryGrid(cols, rows, item_dict, obtainer, place_item_callback=None, situation=None)` |

#### 成果物の性能は素材の値段で決まる（実測。VERIFICATION_LOG.md §2.67）

`ItemCraftManager.calculate_modification(item_type, item_price)` は
**float の倍率**を返す。成果物の値段は素材の合計値段にそれを掛けた値:

```
素材 value 2 + 8（合計の値段 30.75）
  get_equipment_level_from_price(30.75) -> 2
  calculate_modification("weapon", 30.75) -> 24.375
  30.75 × 24.375 = 749.53125
  get_equipment_level_from_price(749.53125) -> 27
  成果物: value 27 / 攻撃力 116
```

- 値段 → レベルは `get_equipment_level_from_price` /
  `get_heal_item_level_from_price` / `get_other_item_level_from_price` の3本。
  `get_*_price(quest_difficulty)`（§2.13.2）の逆関数
- **副産物（`material`）はその 1/5**。実測2例とも合う
  （`30.75 × 9.5122 = 292.5 → 58.5 → value 14` /
  `1.25 × 24.375 = 30.46875 → 6.09375 → value 2`）
- 倍率は**種別と値段の両方で変わる**（`weapon@30.75` は 24.375、
  `material@30.75` は 9.5122）。**式の形は実測2点では出ない**

つまり素材が高いほど成果物が良い。
店の品揃えは土地の依頼の難易度で決まる（§2.13.1.1）ので、
**土地が育てばクラフトの成果物も自動で追随する**。

グリッドは名前ではなく、アイテムを置く能力で見分ける
（`place_new_item` / `try_place_item` / `occupy_slots` / `find_placement_position` ほか）。
売買・強化のグリッドも同じ HUD にぶら下がったまま `opacity=0` で残るので、
**見えているものだけを数えること**（`124_` が親をたどって確かめている）。

矩形は未採寸で、窓の大きさで変わる。2560x1440 では
「作成」ボタンの枠が生成先グリッドの枠と交差し、矢印が生成先グリッドの裏に埋もれる
（**矢印の定位置がもともとグリッドの中**なのでボタンを動かしても解消しない）。

> 矢印は `Label` なので、**ウィジェットの矩形と見えている文字の箱は別物**。
> Kivy の `Label` は `text_size` を持たなければ文字のテクスチャを矩形の中心に描く。
> 重なりを矩形で測ると、ラベルが大きいビルドで破綻する。

### 2.15 キャラクタ名はそのままファイルパスになる

```
worlds/<世界>/characters/<キャラクタ名>/
```

名前に Windows のパスに使えない文字（`< > : " / \ | ? *`）が入ると `os.makedirs` が落ちる。
LLM の生成した名前に引用符が混じる経路は実在する（`試験人形「テストダミー"` のような形）。

- バックグラウンドスレッドで起きるのでゲームは落ちない。
  画像が生成されないまま無言で失敗し、その NPC に関わるたび再発する
- 名前からパスを組む箇所は5つある（`generate_and_write_character_detail` /
  `generate_character_image` / `generate_character_image_from_enemy` /
  `generate_enemy_image_from_character` / `delete_world_character_images`）。
  個別に消毒すると書き込みと削除でずれて別の不整合を生む
- **名前の唯一の入口は `scripts.characters:Character.__init__`**
  （LLM 生成・プリセット・プレイヤー・ロードが全部ここを通る）。
  ここで `self.name` を正せば5箇所は手を入れずに一致する
- Windows は末尾の空白とピリオドを黙って切るので、パスへ使うなら先に落としておく
- 世界名（`worlds/<世界>/`）の入口は調べていない。同じ壊れ方をしうる

main_024 で本体が直した（`sanitize_path_name(name)` が `scripts.functions` と
`save_world_json` に追加され、置換先は `110_` と同じ全角）。

> 本体とこちらでは直す場所が違う。
> `110_` は `Character.__init__` で名前そのものを書き換える（セーブに残り、画面表示も変わる）。
> 本体はパスを組むところで消毒する設計に見える。
> どちらが動いたかは名前を見れば分かるはずだが、確認できた個体の保存名が読めておらず、
> 「本体が名前ごと直している」のか「パスだけ直していて `110_` に渡る名前がたまたま綺麗だった」のかは未確定。

### 2.16 セーブ

```python
plaintext  = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
cipher[i]  = plaintext[i] ^ b"Instantale_Save_Key_2026"[i % 24]
```

`savedata.json` も同じ方式。復号は `scripts.save_codec` に集約されている
（`xor_with_key` / `read_obfuscated_json_file` / `write_obfuscated_json_file` /
`read_json_with_obfuscation_fallback`）。

> セーブを書き換えるツールは、書き込み前に毎回復号→再暗号化のラウンドトリップを検査し、
> 一致しなければ拒否すること。

### 2.17 経験値・レベル・訓練

```
Character.experience_level / experience_point        値（既定は 0 / 0）
Character.gain_exp(exp) / check_levelup() / levelup() / calculate_exp()
Character.calculate_current_required_exp_on_display() / _gained_exp_on_display(gained)
```

#### 能力値はレベルでは伸びない

セーブのバックアップで同一プレイヤーを追った実測
（`levelup()` が「能力値の更新まで持つ」というのは関数名からの推測で、実際には動かない）:

| レベル | `original_ability_scores`（筋・耐・敏・知・賢・魅） | 合計 |
| --- | --- | --- |
| 3〜33 | 24・18・26・25・25・24 | 142 |
| 41〜73 | 26・22・26・26・25・24 | 149 |

30レベル進んで合計 +7。動かしているのは宿の訓練だけ（`VacationTrainManager`）。
作成時に振った値がほぼそのまま最後まで続く。

#### 作成時の値は才能点（`point_use`）で決まり、既定はかなり低い

| キャラ | `point_use` | 合計 | 各値の幅 |
| --- | --- | --- | --- |
| テスト女性 / テスト男性 | 16 | 66 | 11 一律 |
| ヴァルカ・ヴォルガド | 31 | 71 | 9〜15 |
| アーリ | 300 | 142 | 18〜26 |

普通に始めると各能力値は 9〜16。
24 前後という値は才能点を大量に積んだキャラのもので、既定の姿ではない。
セーブで見た上端は 30。

> 能力値に閾値を置く調整は、9〜16 の側を基準にしないと新規キャラで一度も発火しない
> （`313_` が実機1回目でこれを踏んだ）。

その他:

- `gain_exp` が内部でレベルまで上げるのか、呼び出し元が `check_levelup` → `levelup` を回すのかは読めない。
  **両方に耐える書き方**（レベルが動いていなければ `check_levelup` を聞く）にする
- 支給の点数を決めているのは `scripts.functions` 側
  （`get_training_experience_point` / `get_days_elapsed_experience_point`（点数ではなく率）/
  `get_enemy_exp_lvl` / `training_efficiency_ratio`）。
  **式を再現するより、支給された点数を写すほうが確実**（`306_`）

#### 訓練・休暇のマネージャ（すべて `__main__`）

| クラス | `__init__` | 何か |
| --- | --- | --- |
| `DisplayVacationChoice` | `(app, period_months)` | 宿屋の休暇の選択肢＝部屋選び |
| `VacationStartManager` | `(app, months, quality)` | 部屋を決めて宿泊を始める。連泊の `まだ宿泊する` も同じクラス |
| `VacationTrainManager` / `VacationRestManager` | `(app, months, quality)` | 訓練 / 休養 |
| `VacationLaborManager` / `VacationSocializeManager` / `VacationBeggingManager` | `(app, months, quality)` | 労働・社交・物乞い |
| `VacationEndManager` | `(app)` | 宿泊を終える |
| `DisplayTrainingChoice` | `(app, training_type)` | 施設での訓練の選択肢 |
| `TrainingStartManager` | `(app, training_years, training_price)` | 施設の主に年月と代金を払って教わる |
| `TrainingPhaseManager` | `(app, training_type, remaining_years, training_log)` | その各段 |

#### 宿泊の流れ（実測）

```
process_choice(DisplayVacationChoice, '宿泊する(4ヵ月)')   period_months は int
process_choice(VacationStartManager,  '個室(100G)')        args=[1, 'private_room']
    「4ヵ月泊まることにした。」→ change_background_image_to_inn_room(quality)
    elapse_days(months * 30)   ← 日数はここで1回
    宿代の引き落とし            ← 金もここで1回
process_choice(VacationRestManager, '休養をとる')   描写が出るだけ。日数も金も動かない
process_choice(VacationStartManager, 'まだ宿泊する') 連泊。宿代も日数ももう1回
process_choice(VacationEndManager,   '宿泊を終える')
```

- 部屋は4つ。`犬小屋(0G)`＝`'kennel'` / `簡易寝台(10G)`＝`'bunk'` /
  `個室(100G)`＝`'private_room'` / `高級個室(1000G)`＝`'luxury_suite'`。
  **犬小屋は本当に選択肢に並ぶ**（しかもタダ）
- `宿泊する(Nヵ月)` の月数はプレイヤーの年齢の変動式（若いと3ヵ月、最長6ヵ月）。
  実測は 20代=3・31歳=4 の2点だけで、年齢ごとの境目は未実測
- 日数と宿代は `VacationStartManager.execute` の中で1回ずつ動く。
  宿泊の開始時点で全期間ぶんが一度に進むので、途中の活動を何回挟んでも暦は動かない
- `execute` の本体はワーカースレッドで走る（`process_choice` 自体は MainThread）
- LLM の描写のプロンプトは「このエリアで数ヵ月の宿泊をし」と月数を焼き込んでいる。
  宿泊の長さを変える MOD から見ると、
  ゲームの文言は月数を持つのに LLM 側は「数ヵ月」で固定される

> 「いま訓練の中か」を `frames.MethodWatch` で見るときは、その `execute` が包まれていないかを気にする。
> 包まれていると生の `__code__` はローダのラッパのもので、これは全パッチが共有するため誤爆する。
> **ローダ側で塞いである**ので答えは正しくなるが、名前で見る予備の経路に落ちるので重い。
> 自分で包むなら `MethodWatch` をやめて自分のラッパで印を立てる方が速い（TECH.md §6.3）。

### 2.18 エリア移動（土地から土地へ）

```
process_choice(DisplayAreaMoveChoice, '他の土地へ行く')
process_choice(AreaMoveCofirmation,   '陽光の砦')
process_choice(AreaMoveManager,       '馬車(1000G)' / '徒歩(3ヵ月)')
```

| クラス | `__init__` | 何か |
| --- | --- | --- |
| `DisplayAreaMoveChoice` | `(app)` | 行き先の一覧 |
| `AreaMoveCofirmation` | `(app, target_area_id)` | 手段の確認。綴りは `Cofirmation` |
| `AreaMoveManager` | `(app, target_area_id, mode)` | 実際の移動。`method_1` / `show_loading_text` |
| `AreaMoveRestriction` | `(app, target_area_id)` | 行けないときの画面 |

- `mode` の実値は `'on_foot'` / `'coach'`。
  ただし**値を書き起こして組み立てるより、確認画面のボタンの `args`
  （`[target_area_id, mode]`）をそのまま写すほうが安全**（`307_` はこの形）
- 日数を進めているのは `InstantaleApp.elapse_days(days)`。
  徒歩の移動で `90` が渡ってくる（表示は `徒歩(3ヵ月)`）
- 移動中の表示は `徒歩で目指す。長旅だ...` → `.` `..` `...` → `辿り着いた。`
  （すべて `add_text(context)` を通る。点は `AreaMoveManager.show_loading_text`）。
  馬車は `1000ゴールドを支払った。快適な旅だ...` → 点 → `辿り着いた。`
  （**金額は文に焼き込みで、MOD が実際の引き落としを変えても文中の 1000 は動かない**）
- 馬車の実測: 日数は `elapse_days(14)`、運賃 1000G の徴収は **`AreaMoveManager.execute` の中**
- 手持ちが運賃に満たないときも確認画面に馬車のボタンは普通に並ぶ。
  押すと `execute` が走り `金が足りない...` の一言で中断する
  （日数・所持金・エリアとも動かず、`AreaMoveRestriction` は通らない）
  ＝ **残高チェックも `execute` の中**
- `llm_manager:area_move_rejector(...)` がある（同行者が移動を拒む経路と思われるが未検証）

#### クエストは日数を進めない（2026-08-19、実機と全件計数の両方で確認）

`elapse_days` は LLM に渡している14種の権限の1つ（§2.26）でもあるので、
`output_data/<世界>/<PC>/<manager>/N.json` の **`response`** に
`{"type": "elapse_days", "days": N}` が入っている件数を全件数えた。

| manager | 使った / 総数 |
| --- | --- |
| `master_ai_facilitator`（自由行動） | **128** / 1004 |
| `master_ai_facilitator_from_conversation` | **17** / 161 |
| クエスト側 8 manager（`quest_referee_*` / `*_summarizer` ほか） | **0** / 2029 |
| 他 40 manager | 0 |

クエスト側 2,029 件すべてで0件。
総数は遊ぶたびに増える（この表は 2026-08-19 21:30 時点）ので、
**効くのは総数ではなく「クエスト側は0件」の側**。
数え直したいときは `response`（`messages` ではない）の中の
`{"type": "elapse_days"}` を manager ごとに数える。

日を進めるのは自由行動だけで、実行された144ステップの日数は
`0日 69 / 1日 69 / 3日 4 / 7日 2`（最大7日）。
つまり**クエスト中に暦が動くとすれば、それは戦闘やイベントではなく自由行動を挟んだとき**。
この計数が `307_` の設計を変えた（VERIFICATION.md §3.13）。

### 2.19 体力（スタミナ）は `physical_integrity`

`Character.physical_integrity` / `max_physical_integrity`（`__init__` の既定はどちらも 100）。
戦闘のHP（`current_hp` / `max_hp`）とは別物。

同じプレイヤーを1晩追った実測:

| `physical_integrity` | `max_hp` | `exhausted` |
| --- | --- | --- |
| 100 | 1560（`original_max_hp` と同じ） | `False` |
| 50 | 1365 | `True` |
| 0 | 1170 | `True` |

- 土地の移動やクエストで減る。回復は医療施設（`MedicalTreatmentManager(app, treatment_price)`）
- **体力が減ると最大HPが下がる**（`original_max_hp` は動かない）。
  式は特定していないが、`physical_integrity` が上限を削る側
- `exhausted`（bool）は 50/100 の時点で既に `True`。閾値・減る量・回復量は未特定
- 上限はレベルで伸びる（`get_max_physical_integrity(level)`）。実測:
  1→10 / 5→11 / 8→12 / 15→15 / 22→19 / 25→22 / 30→26 /
  41→34 / 49→39 / 50→40 / 55→42 / 58→43 / 73→50。
  **`100` は既定値であって、実際に遊んで到達する値ではない**。
  レベルに対して上限が合っていないセーブは、どこかが壊れている合図になる
  （VERIFICATION_LOG.md §2.36 はこれで新規キャラのレベル60を切り分けた）
- **`current_hp` が `max_hp` を超えている状態を観測している**（2591 > 1560）。
  HP を条件に使うなら `current_hp <= max_hp` を前提にしないこと

### 2.20 手配度（`area_history` の `lawfulness`）

治安上の立場は**土地ごと**に持たれている。

```python
player_data["area_history"] = {
    "0": {"residency": {"total_days": 909, "last_stay_end": 104},
          "achievements": ["…"], "lawfulness": 10},   # ← エリア id ごとに1つ
}
```

| 項目 | 分かっていること |
| --- | --- |
| 在り処 | `Character.__init__` の引数（`area_history=None`）。プレイヤーもNPCも同じ `Character` |
| 鍵 | エリア id（`player.current_area` と同じ語彙。文字列） |
| `lawfulness` | 素の平常値は `10`（40エリア全てが 10 の実セーブで確認）。小さいほど手配が重く、0 未満で犯罪者。実プレイで `-40` を観測。上限は未特定 |
| `residency` | その土地に滞在した日数の累計と、最後に発った日 |
| `achievements` | その土地で成した事の文章（LLM が書いたもの）の配列 |

- `achievements` と `residency` は**会話にもそのまま渡っている**。
  会話5関数のうち3つが `area_residency` / `area_achievements` を引数で受け取る（§2.24）。
  ただしこの named 引数は実測で**常に None**（`317_` のデバッグログ、13回全部。
  2026-08-24〜27）。実体は別経路で、system メッセージの【プレイヤーキャラの情報】へ
  「この土地での活躍: ['傭兵エリスが…']」（Python 配列の repr）の形で直接描画される
- `achievements` には依頼クリアごとに1〜2文の要約が入り、
  「湿地の霧は晴れ」の粒度で**土地の状態変化まで**書かれる（復号した実セーブ19件）
- **読み書きするヘルパは無い**（`lawfulness` を名前に含む関数が存在しない）。値を直接触るしかない
- 減らしているのは LLM の判定側（プロンプトのスキーマに `lawfulness_loss` がある）。
  どの行為でいくつ減るかは未特定
- 関連しそうなクラス（`ImprisonmentStartManager` / `DisplayCitizenshipChoice` /
  `GetCitizenshipManager` / `DieFromOldAgePrison` ほか）との繋がりは未確認
- **手配度を直接書き換えてもゲーム側の帳尻は崩れない**
  （`-10` → `10` に書き換えたあと普通に遊び、ゲーム自身のセーブに両方そのまま残った）

#### 役場（`administrative_office`）の選択肢（実測）

```
Facility.choices = ['労働の募集をみる', '市民権の発行', '出る']
   ↓ ゲームがこれに『会話する』を足して並べる
app.buttons      = ['労働の募集をみる', '市民権の発行', '出る', '会話する']
```

- **手配を解く選択肢は素のゲームには無い**（`309_` と二重にならない）
- `出る` が `会話する` より前に来る。
  施設の選択肢は「操作 → 退出」の順とは限らないので、位置を文字列や並び順で決め打ちしない
- 会話を挟むと抜けた後に施設の選択肢が組み直されるので、
  足した自前のボタンは組み直しのたびに入れ直す必要がある

#### 衛兵との戦闘（`enemy_type='guard'`。2026-08-21 に実機で全段を実測）

手配された土地でゲーム自身の衛兵を出し、`220_probe_bounty_hunter` で全段を録った
（VERIFICATION_LOG.md §2.51）。

```
BattleStartManager(app, enemy_type='guard', enemy_content=None)
  .execute -> .start_battle -> sb_1 -> create_guard_enemies      (instantale.py:6895)
      guard_npc_generator(area, world, 20)              -> EnemyData 1件
      generate_enemy_instance_from_quest_dict(
          {'type': 'normal', 'data': ...},
          base_image, pixelated_image, pos_prompt, neg_prompt, 20)   × 3体
        get_enemy_exp_lvl('normal', 20)                 -> 21
        get_enemy_attributes_base_point('normal', 20)   -> 11.37
  -> app.current_enemy_dict = {'衛兵1': …, '衛兵2': …, '衛兵3': …}（レベル21、HP 228〜248）
  -> BattleEndManager.end_phase -> guard_battle_summarizer(area, world, player, combat_log)
```

| 項目 | 分かっていること |
| --- | --- |
| `enemy_type` | 衛兵の経路では `'guard'`。コロシアム・クエストの語は未採取 |
| `enemy_content` | 衛兵の経路では `None`。中身はマネージャ側が作る |
| 難易度 | **数値1つ**。敵のレベルも能力値もこれ1つから決まる（`get_enemy_*` の第2引数） |
| `enemy_tier` | `'normal'`（`get_enemy_*` の第1引数） |
| 敵の数 | 3体。`get_enemy_count_in_quest` はこの経路では呼ばれない |
| 敵の名前 | `衛兵`。`EnemyData` に名前の項目は無く、マネージャ側が付けている |
| 敵の返り方 | `generate_enemy_instance_from_quest_dict` は `None` を返す。敵は `app.current_enemy_dict` に入る |

`guard_npc_generator` が返す `EnemyData`（`output_data/…/guard_npc_generator/` にも残る）:

| 項目 | 実値 |
| --- | --- |
| `description` | その衛兵の説明文（日本語。数百文字） |
| `look` | `category`（`monster` / `human_female` / `human_male`）と `image_generation_prompt`（英語の語の配列） |
| `race` | 自由文字列（`Human`） |
| `size` | `tiny` / `small` / `medium` / `large` / `huge` |
| `archetype` | `balanced` 固定（`const`） |

- **難易度の出どころは未特定**。実測は `20` で、そのときプレイヤーはレベル 60、
  その土地の手配度は `-10`。どちらとも一致しないので、
  プレイヤーにも手配度にも連動していない（残る候補は土地の平均難易度）
- **難易度と敵の対応は実測2点**（`316_` が 75 を渡した回を含む。VERIFICATION_LOG.md §2.52）

  | 難易度 | レベル | 能力値の基準点 | 敵の数 | HP |
  | --- | --- | --- | --- | --- |
  | 20 | 21 | 11.37 | 3 | 228〜248 |
  | 75 | 76 | 12.62 | 3 | 776〜908 |

  **レベルは難易度+1**、**敵の数は難易度で動かない**。
  基準点はほとんど動かず、強さの差はレベルと HP の側から来ている
- **衛兵と戦うと、その土地の手配度が 10 下がる**。
  手配の有無に関わらず一律で、**手配されていない土地（平常10）でも `0` になる**
  （実測2回。`-10` → `-20` と `10` → `0`）
- **外から難易度を差し替えられる**（`316_bounty_hunter` が実機で成立）。
  `guard_npc_generator` の第3引数と
  `generate_enemy_instance_from_quest_dict` の最後の引数の両方を差し替えれば、
  姿と説明も敵の実体も差し替え後の値で作られる
- **敵の名前は後から付け替えられる**。`start_battle` が返った直後に
  `current_enemy_dict` の鍵と `Character.name` を書き換えても、
  戦闘は最後まで通る（実測2回。入れ物は作り直さず中身だけ入れ替えた場合）
- MOD からこの戦闘を起こすなら、作る側を発明せずに
  `BattleStartManager(app, 'guard', None)` を組んで `execute` を呼べばよい。
  強さを変えたいときは、`create_guard_enemies` の中から呼ばれる2箇所
  （`guard_npc_generator` の第3引数と `generate_enemy_instance_from_quest_dict` の
  最後の引数）に渡る難易度を差し替える

### 2.21 自由生成施設のシーン記述エンジン（`scripts.free_facility`）

main_023 で入ったイベント記述用の実行エンジン。JSON のステップ列を解釈してシーンを走らせる。
MOD にとっての要点は、プログラムがセーブの中にあり書き換えられること。

```python
world_dict["free_facility_programs"]     # {program_id: プログラム(dict)}
world_dict["free_facility_enabled"]      # 世界生成時のオプション
```

#### 2.21.1 施設との結び付き

ゲーム自身がエンジンを起こすのは `facility_type == 'free'` の施設
（実測した世界には集落ごとに1つ、計3つ）。

> ただし**エンジン自身は施設の種類を見ていない**。
> MOD から `FreeFacilityManager(app, program_id)` を組んで `process_choice` に渡せば、
> 宿屋でもギルドでも同じように走る（実機で確認）。
> 「`free` 施設が3つしか無い」ことは制約にならない。

```python
facility.facility_type = 'free'
facility.choices       = {'利用する', '出る'}
facility.config = {"level_of_detail": 0, "concept": "…",
                   "program_id": "free_10",              # = "free_" + facility.id
                   "free_flags": {"visited_fire": 1}}    # ← フラグの実体はここ
```

`FreeFacilityManager(app, program_id, resume=None, vars=None)` が実行する。
選択肢を1つ押すたびに入り直す形で、再開位置と会話の蓄積を引数で渡す。

`FreeFacilityManager` は `__main__` ではなく `scripts.free_facility` にある
（`getattr(__main__, ...)` では引けない。§2.5 と同じ罠）。

#### 2.21.2 ステップと効果

`STEP_TYPES` は18種。`lint_program(program)` がゲーム自身の検証器として露出しているので、
実機へ入れる前にプログラムの正しさを確かめられる（戻り値は指摘の `list`。空なら合格）。

| 分類 | ステップ |
| --- | --- |
| 制御 | `label` / `goto` / `if` / `end` / `random` / `calc` / `var_set` |
| 表示と入力 | `text` / `choice` / `input` |
| 状態 | `flag_set` / `flag_get` / `memory` / `history_clear` |
| 外部 | `llm` / `effect` / `elapse` / `call_phase` |

`effect` は9種（`gold_add` / `item_add` / `exp_add` / `status_add` / `heal` /
`wait` / `show_character_image` / `play_sound` / `remove_character_image`）。
金・アイテム・経験値はここから渡せる。

`call_phase` で渡せる先は5つで、`get_phase_class` は全部 `__main__.X` に解決する:
`BattleStartManager` / `DisplayQuestChoice` / `DisplayTrainingChoice` /
`DisplayVacationChoice` / `ShoppingStartManagerRemake`。

`llm` ステップは2形で、判断と描写が分けてある
（`output.mode` が `"text"` か `"choice"`。後者は「LLM が状況を判断して1つ選び、
プログラムがその先へ飛ぶ」）。
仕様書（`_DSL_SPEC`）は使い分けまで明記している:

> llm choice mode expresses the judgment of the NPC or the world. Decisions that
> belong to the player (consent, purchases, accepting a price) must be a player
> "choice" step instead.

`memory` は訪問の要約をプレイヤーのライフログと目撃者の記憶に入れる。

#### 2.21.3 フラグは施設ローカル。世界規模の状態は持てない

条件が読めるソースは3つしかない
（`{"type":"if","cond":{"source":"var"|"flag"|"player", ...}}`）:

- `flag` … `facility.config['free_flags']`。その施設だけ。来訪をまたいで残る
- `var` … その訪問の中だけ
- `player` … `gold` と `age` のみ

`_flag_store(scope)` は scope 引数を取るが、実測で観測できたのは `'facility'` だけで、
生成側のスキーマにもスコープを選ぶ項目が無い。

したがって**施設をまたぐ話は DSL だけでは書けない**。
跨ぎたい MOD は状態を自分で持ち、渡すプログラムをその都度組む。

| 渡し方 | 痕跡 |
| --- | --- |
| `world_dict['free_facility_programs']` に足す | セーブに残る。MOD を外しても id が残る |
| **`_lookup_program` を包んで自前のものを返す** | **何も残らない**（こちらを使う） |

```python
@ctx.wrap("scripts.free_facility:FreeFacilityManager._lookup_program")
def lookup_program(orig, self, *args, **kwargs):
    if getattr(self, "program_id", None) == MY_ID:    # 完全一致のみ
        return build_program_for_now()               # その時点の状態で組む
    return orig(self, *args, **kwargs)               # free_* は必ず素通し
```

この形にすると `flag_set` を使う理由も無くなる
（分岐の前提は MOD が組むプログラムに焼き込めばよく、DSL 側は `var` で足りる）。
`flag_set` は施設の `config` に書かれてセーブに残るので、使わずに済むなら使わない
（実測: 受け皿の無い宿屋にも `free_flags` が新設された）。

#### 2.21.4 上限と禁止事項

| | |
| --- | --- |
| `MAX_STEPS_PER_EXECUTE` | 300 |
| LLM 呼び出し | 既定 20、生成物は 12。1本 2〜6 ステップが指針 |
| プログラムの大きさ | 15〜45 ステップ |
| `SESSION_MAX_CHARS` / `_ENTRIES` | 4000 / 40 |
| `elapse` | 訓練・長逗留の概念にのみ。1経路1回、units 1〜2 |
| `item_add` / `status_add` | 1回の来訪で最大2つ / 1回2つまで |
| 禁止 | 賭博をテーマにした施設、性的な内容 |

金額を直に書かないのが作法。
`prices` / `payouts` に名前付きで宣言し、`{price.X}` `{payout.X}` で参照する
（エンジンがその土地の相場で解決する）。
「金を取って文を出すだけ」の活動は `lint_program` が defect として弾く。

### 2.22 NPC の退場は `config['is_dead']`

`Character.config` の中にある（セーブされる33項目のうち `config` の下。トップレベルには無い）:

```python
character.config = {"level_of_detail": 2, "is_player": False,
                    "is_dead": True, "difficulty_level": 4}
```

印が立っても、施設の名簿からは外れない。
世界の全 NPC 35人を舐めて、`is_dead=True` の1人が `roster x1` のまま残っていることを確認した
（`referenced by nothing: 0`）。
にもかかわらずゲーム内では会話と呼び出しのどちらにも出てこない。

> 名簿からは外さない。読む側が印を見て飛ばしている。

MOD にとっては都合がよい（参照が切れないので何も壊れず、`False` に戻せば復帰する）。
NPC を退場させたい MOD は `move_npc_to_facility` で移送する必要が無い。

**ただし施設の主には使えない**（`Facility.owner` に載っている NPC を消すと
その店に話せる相手が居なくなる）。
実測した世界では 35人中24人が主で、自由に使えるのは11人だった。

`state` は死亡とは無関係（全員 `''`）。
HP は `current_hp` / `max_hp` / `original_max_hp` で、`physical_integrity`（§2.19）とは別物。

### 2.23 NPC を作る（`save_data_dict['npcs']` に書いてから組む）

```python
npcs = app.save_data_dict["npcs"]          # ★ ここが本体
npc_id = str(max(max(int(k) for k in 名簿) + 1,
                 app.save_data_dict["index"]["npc"]))   # 台帳も見る
npcs[npc_id] = データ                       # セーブの形（下の33項目）
for d in (app.save_data_dict, app.world_dict):
    d["index"]["npc"] = int(npc_id) + 1     # 台帳を進める
character = app.world.generate_character(npc_id, データ)
app.move_npc_to_facility(npc_id, character, 施設, ノード)
```

> 採番は `index['npc']` で決まる。実在する id の最大値ではない。
> ゲームが新しい町を生成するとき、店主・ギルド員の id は `index['npc']` から
> 連番で振られ、既に同じ id の NPC が居ても構わず上書きする。
> MOD が `max + 1` だけで採ると台帳が追いつかず、次の町の生成でゲームが
> 同じ番号を踏む。テストワールドの灰の交易都市（area 2）では店主 50〜57 の
> 素データが `local/` の MOD が作った登場人物に差し替わり、
> `world_data.json` 側にだけ正しい店主が残った（2026-08-29。
> VERIFICATION_LOG.md §2.77）。ローダの `ids.claim`（TECH.md §3.2.3）が
> 台帳を読んで進める。MOD は id を自分で決めない。

| 関数 | 役割 |
| --- | --- |
| `World.generate_character(id, value)` | 作る側ではない。`save_data_dict['npcs'][id]` を id で引いて `Character` を組む。無い id は `KeyError` |
| `save_area_json:generate_npc(...)` | 呼んでも何も作られない（返るのは `world_dict` そのもの） |
| `scripts.characters:Character(...)` | コンストラクタが完全な署名で露出。最後の手段として直に組める |

> 素データの置き場所は1つではない。
> `app.world_dict['npcs']` と `app.save_data_dict['npcs']` は別の辞書で件数も違う。
> `generate_character` が読むのは後者。
> どちらか一方に賭けず、既存の character id が鍵になっている辞書を全部探して全部に書く。
>
> 採番も決め打たない。
> ゲームは遊んでいる最中にも NPC を作る（新しい町の生成で10体が一度に増えた）。
> その番号は `index['npc']` から来る（上の枠）。

生成した NPC は HP・スキル・装備・立ち絵のいずれも空でよい
（ゲームが会話や戦闘の直前に `ensure_npc_detail_generated` で埋める）。

> 空でよいのは**値**であって鍵ではない。
> `ability_scores` は6つの鍵（strength / dexterity / constitution /
> intelligence / wisdom / charisma）が無いと `generate_character` が
> `KeyError: 'constitution'` で落ち、直組みの `Character(...)` も
> `original_ability_scores` を添字で読んで落ちる
> （2026-08-27 実機。VERIFICATION_LOG.md §2.72）。値は null でよい。
>
> 生成直後（`level_of_detail=1`）の実物（実セーブの id 41、難易度48）は、
> ほかに experience_level（整数。難易度48 → 51）・age（実セーブの全NPCが
> 20 の定数）・current_area / current_location（置いた先の id）・
> 関係値（affinity 0・`警戒心がある`・`初対面`）が入っていて、
> speech_style は null。
> ひな型はローダの `npcs.NEW_NPC_TEMPLATE`（TECH.md §3.2.3）。

> 名前は `generate_character` の前に決まっている。
> 素データを先に書く順序なので、`Character` を組んだ後に `self.name` だけ直しても
> `npcs` には古い名前が残り、次の保存で戻ってくる。
> 名前を直す MOD は id を鍵に持つ辞書を全部書き換える（`120_`）。

#### 項目の並び順は、揃えるだけでなくこの順でなければならない

セーブは辞書をそのまま JSON に落とすので、書いた順がそのままファイルの行順になる。
そしてセーブを読む側には、項目を上から順に並べて見せる道具がある（セーブエディタ）。
順番が変わると、項目は全部揃っているのに表示が崩れる。

| # | 項目 | | # | 項目 | | # | 項目 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `name` | | 12 | `experience_point` | | 23 | `look` |
| 2 | `id` | | 13 | `original_max_hp` | | 24 | `memory` |
| 3 | `category` | | 14 | `max_hp` | | 25 | `life_log` |
| 4 | `profile` | | 15 | `current_hp` | | 26 | `current_log` |
| 5 | `personality` | | 16 | `age` | | 27 | `relationship` |
| 6 | `look_description` | | 17 | `skills` | | 28 | `initial_location` |
| 7 | `speech_style` | | 18 | `equipments` | | 29 | `config` |
| 8 | `job` | | 19 | `weakness` | | 30 | `current_area` |
| 9 | `state` | | 20 | `location` | | 31 | `current_location` |
| 10 | `ability_scores` | | 21 | `inventory` | | 32 | `knowledge` |
| 11 | `experience_level` | | 22 | `image_src` | | 33 | `display_position_in_battle` |

後ろの4つ（30〜33）は遊び始めてから増える
（プリセットの `world_data` は29項目、`savedata` になると33項目）。
`knowledge` はリスト（`[]`）で、辞書ではない。

> 守り方は「全項目を持ったひな型を先に作り、上書きだけする」。
> `dict.update` は既にある鍵の位置を動かさないので、
> ひな型が33項目を漏らさず持っている限り並びは保たれる。
> 1つでも欠けていると、その項目だけが末尾に足されて並びが壊れる。
> 項目を足すときは必ず表の正しい位置へ差し込む。末尾に足さない。

### 2.24 会話中の NPC に知識を持たせる

```python
llm_manager:conversation_starter(messages, ...)          # 第一声
llm_manager:conversation_facilitator(..., retrieved_knowledge, job_knowledge='')
llm_manager:conversation_facilitator_after_retrieval(..., retrieved_knowledge)
```

- 第一声だけ変えたいなら `conversation_starter` に渡す messages のコピーを差し替える（`300_` の手口）
- 自由入力で尋ねたときにも効かせたいなら `retrieved_knowledge` に足す

> 差し込む条件は「誰と話しているか」で決めること。
> `character_instance`（両者とも引数の4番目）から id を引いて突き合わせる。
> MOD が用意したボタン経由かどうかで判定すると、
> 普通に「会話する」で話しかけたときに何も起きない（実機で踏んだ）。
> 引数の位置は版で動きうるので、キーワードを先に見て、無ければ位置で拾う。

会話の履歴・記憶はゲーム自身が持っているので、差し込んだ知識はその会話の要約として記憶に残る
（同じ相手が前の話を引きずる）。
1回きりにしたいなら、差し込む条件の側で絞る。

> なお `conversation_starter` / `conversation_facilitator` /
> `..._after_retrieval` の3つは、いま居る土地の `area_residency` と
> `area_achievements`（§2.20）を引数で直接受け取っている（`targets.txt` の実シグネチャ。
> `*_in_quest` の2つには無い）。
> 成した事の素の文章はプロンプトへ載っている（確定）。
> Epic 版 `output_data\` 19,415件（2026-08-27 分まで）の突き合わせで、
> system メッセージの【プレイヤーキャラの情報】に「この土地での活躍: [...]」の形で
> 毎回描画されていた（計3,119件: `conversation_facilitator` 1,969 /
> `conversation_starter` 924 / `conversation_join_message` 114 /
> `conversation_recruitment_response` 76 / `conversation_facilitator_after_retrieval` 19 /
> `shopping_negotiator` 17）。named 引数の側は常に None（§2.20）。
> だから知識を差し込む MOD が同じ素材を言い換えて足すと二重になる（`317_` はこの理由で、
> 素材の要約ではなく編纂の結果だけを注入している）。
> 載っていても言及はされない。
> 討伐系の活躍ブロック入り会話775件で、応答が功績の語句を自発的に使った回は 0/775
> （プレイヤーが先に口にした回も 0。言い換えは拾えないので参考値）。
> `321_area_chronicle` はこの実測を根拠に、素材の載せ方ではなく
> `Area.descriptions` の原文の側を書き直している。

### 2.25 会話の記憶の実体（`current_log` / `relationship` / `conversation_resolver`）

**書く側。**
会話の終了時に `conversation_resolver` が回り、NPC に書くのは2箇所:

- `current_log`（リスト）… 会話1回ぶんの要約が追記される。
  **2件目の要約は1件目の全文を含んだ累積形**だった
  ＝ `current_log` 自体が会話を重ねるほど同じ文を二重三重に抱えていく
- `relationship`（dict）… `{"player": {affinity, affinity_text[], relationship[], conversation_count}}`

> 要約は必ずしも走らない。
> 呼び出し元は `ConversationEndManager.execute → finish_conversation →
> resolve_conversation → llm_manager:conversation_resolver` の1本だけ（別スレッド）。
> 「会話を終了する」の spec を通らない抜け方は、すべて要約を素通りする。
> 実測した不発は2件（会話の途中で行動処理へ進んだ後にセッションが終わった回と、
> 会話を閉じないままタイトル/別セーブのロードへ抜けた回）。
> 会話の記憶に乗る MOD はこの前提で設計すること
> （`311_` の抽出のように、ターンごとに動く仕掛けがこの取りこぼしの保険になる）。

`current_log` は一時置き場で、移送の契機は日付の変更。
日が変わると中身が `life_log` の1エントリ
（`{day_start, day_end, summarized_count, content}`）へ移り、`current_log` は空になる。
`content` は `current_log` のリストをそのまま `str()` した文字列。
移った後も `life_log` として毎回全文プロンプトに載るので、「前の話を引きずる」経路は途切れない。

> **要約が薄いのは移送のせいではない**（移送は `str()` の丸写しで内容を落とさない）。
> `resolver` の出力量が会話の長さにほとんど依らず一定だから。
> 実測で 3ターン→116字 / 5ターン→185字 / **8ターン（5,597字まで育った会話）→118字**。
> 長く濃い会話ほど捨てられる割合が大きい。
> 日付変更のあとに「内容が大分落ちている」と感じるのは、
> 薄い要約が初めて永続側に現れるのがそこだからで、劣化はもう起きた後である。
> 書き直しのたびに固定の短さへ圧縮されるので、**この経路の情報は単調に減る一方**。

**`resolver` の頼み文に文字数の上限は無い**（出力スキーマは `{"summary": str}` の1欄だけ）。
短さの原因は上限ではなく、**「一切情報を損なわない」と「簡潔に」が同じ文で衝突していて、
モデルが後者に倒れること**。
加えて指示が求めているのは「どこで誰と誰がどういう会話をしたか」＝会話の流れなので、
確定した数値や条件は流れに乗ったものしか残らない。

> 上限が無いということは、**`111_` の置換ルール1行で言い回しを変えられる**ということでもある。
> ただし要約が伸びた分は毎回全文載るので、伸ばす前に下の重複掃除で枠を空けるのが順序として正しい。

**ゲーム側の記憶は重複を抱えたまま膨らむ**（実測2種）:

- `current_log` の累積形が畳まれずにそのまま `life_log` へ移るので、
  1エントリの中に**同一要約の5連コピー**を含む実例があった（3エントリ計911字が毎回全文載る）
- `master_ai_facilitator_from_conversation` のプロンプト（実測 6,200〜8,100字）は
  NPC のプロフィール行と性格行を4回ずつ、感情行を2回含む（行単位の完全一致で損262字）。
  この経路は会話5関数を通らないため `311_` の注入も届かない

`memory` は5鍵の dict（`life_log` / `memory_archive` / `session_log` /
`prior_area_summary` / `brief_summary`）だが、会話2回の間ずっと動かなかった（更新契機は未実測）。
`knowledge` も None のまま。

**読む側。**
`send_request*` 境界での含有照合の結果、
`profile` / `personality` / `current_log` / `relationship`（`affinity_text` の文）が
**毎回・全文プロンプトに載る**。
§2.24 の「同じ相手が前の話を引きずる」の実経路は `current_log` で、
retrieval を待たず第一声から載る。

> `311_` と併用したときの帰結。
> 同じ会話の事実が (a) `current_log` の要約 (b) `311_` の注入プロフィール (c) `about_player` の
> 3箇所で同一プロンプトに並ぶ（実測: 2回目の会話はプレイヤー入力11字に対して開始時点で2,206字）。
> 3者は言い換えの関係で行単位の完全一致は無いため `102_` の隣接重複除去では畳めない。

なお会話系5関数の先頭4引数の並びは共通で、5番目以降は関数ごとに違う。
5番目以降を読む MOD は位置を決め打ちしないこと。

#### 2.25.1 プレイヤーへの感情の文（`affinity_text`）は2本立て

`relationship["player"]["affinity_text"]` は文字列のことも文の配列のこともある
（実セーブで両方あった。ヴェスティア 103人で str 57 / list 46。`323_` の `affinity_of`）。
配列のときの先頭が好感度の段、2つ目以降が魅力の段。
魅力には文が付かない帯がある（下の表）ので、2つ揃うとは限らない。
決めているのは1本の関数だけ:
`scripts.functions:document_emotion_scores_new(affinity, player_charisma)`。

| 位置 | 何の段か | 材料 |
| --- | --- | --- |
| 0 | 好感度 | `affinity`（その NPC がプレイヤーに対して持っている値） |
| 1 | 見た目の魅力 | `player_charisma`（プレイヤーの能力値。§2.17） |

段の文言は **exe の定数表から読み出せる**
（Nuitka はコードを機械語にするが文字列定数はそのまま並んでいる。ja / en / zh-Hant の3言語ぶん）。
**並び順と閾値はそこからは確定しない**（同じ数値の定数は畳まれる）ので、順番だけを写す:

- 好感度は13段。悪いほうから 深く憎悪している / 憎悪している / 強い嫌悪感を抱いている /
  嫌悪している / 多少嫌悪している / **警戒心がある** / 好きでも嫌いでもない / 嫌いではない /
  興味がある / 多少の好意がある / 仲間だと感じている / 盟友だと思っている / 家族同然に感じている
- 魅力は低いほうから ひどく醜く思っている / あまり好みではない / 魅力を感じている /
  強い魅力を感じている / **耐え難いほど魅力的に見えている**

太字が `affinity` 0（＝初対面）の段。

**閾値は実機で総当たりして確定した**（2026-08-20。`125_` が起動のたびに
ゲーム自身を引いて読み直すので、その記録がそのまま答えになる）:

| 魅力 | 段 | | 好感度 | 段 |
| --- | --- | --- | --- | --- |
| 〜7 | ひどく醜く思っている | | 〜-41 | 深く憎悪している |
| 8〜10 | あまり好みではない | | -40〜 | 憎悪している |
| 11〜13 | **文が付かない**（`None`） | | -30〜 | 強い嫌悪感を抱いている |
| 14〜16 | 魅力を感じている | | -20〜 | 嫌悪している |
| 17〜19 | 強い魅力を感じている | | -10〜 | 多少嫌悪している |
| 20〜 | 耐え難いほど魅力的に見えている | | 0〜 | **警戒心がある** |
| | | | 10 / 20 / 30 / 40 | 好きでも嫌いでもない / 嫌いではない / 興味がある / 多少の好意がある |
| | | | 60 / 100 / 150 | 仲間だと感じている / 盟友だと思っている / 家族同然に感じている |

魅力の段は**文が付かない帯を含めて6つ**で、文字列の数（5つ）とは一致しない。
そして能力値は作成時 9〜16（§2.17）なので、**素のままなら「あまり好みではない」か
「文が付かない」**あたりに収まり、最上段（20以上）に届くのは魅力にかなり振ったキャラだけ。
実データで記録に残っている NPC が全員最上段だったのは、そのプレイヤーの魅力が 20 以上
だったということ。

> exe の定数の並びから読めた 6 / 8 / 11 / 14 / 17 は**外れていた**（1段ぶんずれる）。
> 定数は重複が畳まれるので、並びから閾値を当てるところまではできない。
> 段の文言（順番）だけがあそこから取れる。

> 魅力の側は引数が1つしかない。
> 相手も関係の深さも入らないので、同じプレイヤーなら世界の全員が同じ段になり、会話を重ねても動かない。
> 実データでも記録に残っている NPC は全員が最上段だった。
> この文は保存され、以後その相手との会話プロンプトに毎回・全文載るので、応答が無条件に友好的になる。

段を作る場所がこの1本しかないことには利点もある。
**会話系5関数を通らない経路にも同じ保存済みの文が載るので、ここを直せば全経路に届く**（`125_`）。

**書かれるのは会話1回につき2度**（実測2026-08-20。`out/charisma_impression.log`）。

```
会話の開始 ConversationStartManager.execute -> ..._method_0 -> _1
会話の終了 ConversationEndManager.execute -> finish_conversation
           -> resolve_conversation(self, character_id)
```

つまり**セーブに残るのは終了時に計算された値**で、開始時の値は上書きされる。
ここを触る MOD は両方に効かせないと、会話の最中だけ効いて終わった瞬間に元へ戻る。
`resolve_conversation` は相手の id を引数で受け取るので、
「いま誰との会話か」はここから取れる（ゲームのフレームは実行中 `f_locals` が空で、
呼び出し元を遡っても何も読めない。TECH.md §6.3）。

### 2.26 クエストの外の判定（マスターAI の `roll_the_dice`）

クエストに入っていないとき（街・施設・会話中）の自由入力を処理するのは
`master_ai_facilitator` と `master_ai_facilitator_from_conversation`。
フィールドイベント（§2.9）とは別系統で、仕組みも違う。

```
プレイヤーの入力
  → master_ai_facilitator          think / narration / process[] / finished
       process の1つが roll_the_dice: {"type": "roll_the_dice", "chance_percent": 70}
  → ゲームが振り、次のターンのプロンプトに <結果:成功> を差し込む
  → 続きを master_ai_facilitator が処理する（finished=true まで繰り返す）
```

- **確率を決めているのは LLM 自身**（`chance_percent` をそのまま出力する）。
  フィールドイベントのようにゲームが `credibility` から式で作るのではない
- 差し戻されるのは `<結果:成功>` / `<結果:失敗>` だけで確率は書かれない
  （フィールドイベントの `<確率N%: 成功>` とは形が違う。**判定の印を探すコードは両方を見ること**）
- **プロンプトに能力値は1つも載っていない**（`能力値` / `strength` / `dexterity` /
  `attribute` すべて0件）。参照能力値にあたる欄も無い
  ＝ **クエスト外の判定にもキャラクタの能力値は入っていない**
- 権限は14種（`roll_the_dice` / `join_to_player_party` / `move_gold` / `get_gold` /
  `elapse_days` / `generate_item` / `move_item` / `deal_damage` / `add_status_effect` /
  `npc_say` / `start_battle` / `arrest_player` / `generate_npc` ほか）。
  「時系列があるものは1段階目だけ実行して次のターンに回せ」と指示されているので、
  ダイスを振った回は必ず `finished=false` で戻ってくる
- `start_battle` の形は
  `{"type": "start_battle", "player_opponents": [<NPC名>], "player_allies": [<NPC名>]}`
  （敵は最大3・味方は最大2。スキーマの `maxItems`）。
  名前の候補は Literal で、元は同じ呼び出しに渡る `npc_list`。
  **同行中の仲間も候補に入る**ので、LLM が仲間を敵の欄に書くことがある。
  実形は `output_data/` の `master_ai_facilitator_from_conversation` の記録から読んだ

実測（`output_data/` の `master_ai_*` 2,021件。`roll_the_dice` は191回で、
結果まで対応の取れたものが168回）:

| LLM が指定した確率 | 回数 | 実測の成功率 |
| --- | --- | --- |
| 30% 以下 | 10 | 10% |
| 35〜45% | 18 | 44% |
| 50% | 36 | 47% |
| 60% | 49 | 71% |
| 65〜70% | 45 | 62% |
| 75% 以上 | 10 | 60% |

全体で 56.5%（95勝73敗）＝ 振っている側は概ね正直。
指定される確率は 50 / 60 / 70 に 134/191 が集まる
（プロンプトの例文が「まずは成功確率50%でダイスを振ろう」なので、そこへ引かれている）。

> クエスト中の自由入力は `quest_referee_with_free_action` が受ける（§2.9）。
> こちらには `roll_the_dice` に相当する権限も確率の印も無く、
> 進行（battle / field_event / move / retire）を選ぶだけで判定はしない。

`targets.txt` には `master_ai_facilitator_in_quest` と
`master_ai_faciltiator_from_conversation_in_quest`（綴りはゲーム側のまま）もあるが、
`output_data/` には出ていない（回る条件は未確認）。
4つとも同じ形の応答なので、**この経路を触る MOD は4つとも見ること**（`313_`）。

### 2.27 世界生成（入力した概要はそのまま保存されない）

「世界を生成する」画面で入れた名前と概要は、そのまま世界のデータになるわけではない。

```
scripts.hud.hud_world_generate:WorldGenerateScreen
  world_name_input.text / world_overview_input.text
    ├ 概要が空でなければ llm_manager_world_generate:check_world_content_violation(name, overview)
    └ save_world_json:generate_new_world(world_name, world_overview, free_facility_enabled)
         ├ 概要あり → llm_manager_world_generate:create_world_overview_from_plot(world_name, world_overview)
         ├ 概要なし → llm_manager_world_generate:create_world_overview()
         │              どちらも World(world_name, overview, structure_description, structure)
         ├ create_story(world_base, area_name_list)              物語・噂・ストーリークエスト5本
         ├ create_settlement_detail(world_overview=..., ...)     9エリアの施設と NPC
         └ world_data.json を書く
```

入力した概要が渡るのは、1回目のプロンプトの中だけ:

```
【予め指定済みの設定】- 世界の名前: {world_name}
- 世界の概要: {world_overview}
```

`world_data["overview"]` になるのは応答の `World.overview`、
つまり **LLM がそれを読んで書き直した文章**の方。
以後の生成は全部そちらを読み、遊んでいる間に出る依頼
（`random_quest_generator(world_overview, ...)`）も同じ値を受け取る。

| `World` の項目 | 何が入るか |
| --- | --- |
| `world_name` | LLM が書いた世界の名前。`world_data["name"]` がこれと入力した名前のどちらから来ているかは未確定（実セーブでは同じ文字列になっていて見分けられない） |
| `overview` | LLM が書いた世界観。**`world_data["overview"]` はこれ** |
| `structure_description` | 地理・各地の名称の説明文（500文字程度） |
| `structure` | 3層9エリアの入れ子（`StartingArea` → `connected_settlement_1..3` → `MidArea` → `LateArea`） |

層と規模はプロンプトで固定されている。
序盤3エリア（Town 1 + Village 2、うち1つが開始地点）→ 中盤3エリア（Town 2 + City 1）→
終盤3エリア（Village / Town / City 各1）が、中心から放射状に外へ広がる。

保存される `world_data` は5項目で、書かれる順はこう（順序が表示に効く理由は §2.23）:

```
name / overview / structure_description / story / days_elapsed
story = {world_situation, story_flow, current_rumor, current_story_phase}
```

> 呼び出しの並びと項目名は exe の定数表から読んだ（§2.25 と同じ手）。
> **並びは読めるが分岐の条件までは読めない**ので、
> 「概要が空なら `create_world_overview`」は分岐が在ることまでが定数から言えることで、
> 条件そのものは HUD 側の `内容ある` / `内容ない` の分岐から採った推定。
> 「保存されるのは書き直しの方」は実セーブで見える
> （`テストワールド` の `world_data["overview"]` は1段落の要約文）。

### 2.28 素データの辞書は2つあり、遊んでいる最中の追加は片方に届かない

```
app.world_dict       worlds\<世界>\world_data.json
app.save_data_dict   saves\<世界>\savedata.json
```

実セーブを復号して突き合わせた（ヴェスティア、2026-08-21）:

| | world 側 | save 側 |
| --- | --- | --- |
| 施設（ユニークな id） | 228 | 230 |
| NPC | 92 | 100 |
| エリア | 40 | 53 |
| `index` | facility 230 / npc 100 / area 53 | 同じ |

- save 側は world 側の**厳密な上位集合**。world 側にだけ在るものは施設も NPC も0件
- 足りないのは遊んでいる最中に生まれた分だけ（施設2件・NPC 8人・エリア13個）
- 欠けているエリア13個は全部 `size=dungeon` の `dungeon_location`（依頼で生成されるダンジョン）。
  こちらは**仕様**で、そのセーブ限りのエリアは savedata にだけ入る。
  world 側に無くても正常に機能している（移動できる）ので、
  **「savedata にしか無い」こと自体は不具合ではない**
- `index`（採番）は両方とも同じところまで進んでいる

採番だけが両方に届いていて、実体が片方にしか足されていない。

トップレベルの鍵は
`areas` / `index` / `language` / `npcs` / `quests` / `story_quests` / `version` /
`world_data` が共通で、save 側だけが `game_variables` と `player_data` を持つ。
`free_facility_enabled` と `free_facility_programs` は、その旗を立てた世界にだけ在る。

#### 項目の形は同じだが、ゲーム自身が不揃いを持っている

写す処理も比べる処理も、項目が揃っている前提を置かないこと（実測）:

| | 揃っているもの | 欠けているもの |
| --- | --- | --- |
| 施設 | 8項目 284件 | `tier` を欠く7項目 18件 |
| NPC（save） | 33項目 92人 | `speech_style` を欠く32項目 8人（全員が遊んでいる最中に生まれた分） |
| NPC（world） | 33項目 81人 | 29項目 11人（生成時のまま一度も更新されていない分） |
| エリア | 13項目 | 項目の並びが2通り（44件と9件）。`guard_npc` は world 側にしか無い個体がある |
| ノード | 7項目 | 揃っている |

world 側の NPC にも33項目のものが81人居る。
`world_data.json` は生成時の雛形のまま固定されるのではなく、遊んでいる間も更新されている
（後ろの4項目は savedata 化された形。§2.23）。
更新は届いているのに、追加だけが届いていない。

#### 症状

遊んでいる最中に生まれた施設で「売買する」を選ぶとスレッドが落ちる。

```
File "instantale.py", line 3080, in shopping_start_method_1
KeyError: '229'
  area_id = '8'   node_id = '32'   facility_id = '229'
```

3つの id は**ローカル変数で、引数ではない**。
`shopping_start_method_1` は `self` 以外の引数を取らない
（引数から拾おうとした MOD が `args=() kwargs=[]` を記録した）。
どこから求めているのかは読めないので、**この3つを外から再現しようとしないこと**。
プレイヤーはその施設に立ってボタンを押せているので、
組み上がった `Area` / `Node` / `Facility` の側には施設が在る。

> **引き先は `app.world_dict`**（2026-08-21 に実機で確定）。
> `app.save_data_dict` からそこへ施設1件と主1人を写したところ、
> 同じ店がその場で開いた。写した内容は保存され、`world_data.json` の施設が 228→229 に増えた。
>
> ただし引き**方**は読めないまま。
> 直す側は経路を再現しにいかず、`KeyError` のキーを起点にすること
> （何を引き損なったかは例外自身が持っている）。
> キーが何の id かも決めつけない。
> 採番は種類ごとに分かれていて（`index` が `area` / `node` / `facility` /
> `npc` / `item` / `quest` の6本）、同じ番号が別の種類に居るため
> （ノードごと欠けている店ではノード id で落ちる。オフラインで再現）。

### 2.29 文言は `scripts.languages:tr` を1本通る（画面上部の欄を除く）

`scripts.languages` が多言語化の入口。ここで使うのは4つ。

```
scripts.languages:tr(text)          日本語の文 → 今の言語の文
scripts.languages:translate_dict    完全一致の表（{日本語: {'ja':…, 'en':…, 'zh-Hant':…}}）
scripts.languages:pattern_dict      正規表現の表（[(compiled, {'ja':…, 'en':…, 'zh-Hant':…})]）
scripts.languages:language          今の言語（実測 `'ja'`）
```

対応言語は `ja` / `en` / `zh-Hant` の3つ。

`tr` は画面だけの関数ではない。
ゲームが LLM へ送る指示文も同じ表に入っている
（`recruitment_responseとして返答を記述。…前払いで(\d+)ゴールドの雇用費を提示する。`
に `en` と `zh-Hant` が並んでいる）。
つまり**画面へ出る文とプロンプトへ入る文が同じ1点を通る**ので、
戻り値を書き換えると両方が同時に変わる。

`tr` は `from scripts.languages import tr` で **25 のモジュールに写されている**
（`__main__` と `scripts.*`）。
包むときは別名の張り替えが要る（ローダの `alias_scan` が既定で行う。TECH.md §4.1）。

数を埋めるのは `tr` より**前**。
`pattern_dict` の引数側が `(\d+)ゴールドを稼いだ。`、
戻り側が `\1ゴールドを稼いだ。` という形なので、
`tr` が受け取るのは既に数の入った文で、返すのも完成した文。

#### 通貨の表記は2つある

素のゲームの通貨は「ゴールド」で、書かれ方は2通り（どちらもビルド内の文字列を実測）:

| 形 | 実文言 |
| --- | --- |
| 長い形 | `1000ゴールドを支払った。快適な旅だ...` / `務め終えた。(数)ゴールドを稼いだ。` / `(数)ゴールドの報酬を受け取った。` |
| 短い形 | `馬車(1000G)` / `犬小屋(0G)` `簡易寝台(10G)` `個室(100G)` `高級個室(1000G)` / `自由権(1,000G)` `市民権(10,000G)` `貴族権(1,000,000G)` / `雇う((数)G)` `治療を受ける((数)G)` `訓練を受ける((数)G)` / `<行動>(数)Gを手に入れた。` |

英語表示では長い形が ` gold`（`You paid 1000 gold.`）、
所持金のラベルだけ `Gold:`。

指示文の側にも両方が出る:

```
必ず前払いで(数)ゴールドの雇用費を提示する。
治療依頼の場合: … 前払いで(数)Gの費用を提示する。絶対に値引きはしない。
前払いで簡易寝台10G、個室100G、高級個室1000Gが必要な事を説明する。
市民権を10000G, 貴族権を100000Gで購入できる。
```

自由生成施設（§2.21）の値段は**埋める前のテンプレート**の形で通る
（`傷薬を煎じてもらう({price.salve}G)`）。

アイテムの `買価` / `売価`（§2.13.2）は数だけで、単位が付かない。

> 額そのものを持つ属性は `player.gold`（§2.13.2 / TECH.md §5.1）で、
> こちらは表記と無関係。

#### 画面上部の能力欄は `tr` を通らない

所持金が画面に出るのはここで、**この欄だけは翻訳の表に載っていない**。

```
scripts.hud.new_hud:InstanTaleHUD.status_texts            <StringProperty>
scripts.hud.new_hud:InstanTaleHUD.update_status_texts(self, instance, value)
```

`status_texts` は改行区切りの1本の文字列で、塗った結果が `status_label` に入る。
実測（`206_probe_quest_flow` が先頭 40 文字を記録している。2026-08-21）:

```
Atk:432(+500)\nDef:0(+500)\nExp:1675/3237\nGold:1116472\nAge:31\nSta:…\nLocation:…
```

見出しの `Atk` `Def` `Exp` `Gold:` `Age:` `Sta` `Location` は
**ビルド内では素の英語の定数**で、`translate_dict` にも `pattern_dict` にも無い。
日本語で遊んでいても `Gold:` と出るのはこのため。
言語を切り替えても変わらない。
実機の画面でも `Gold:13184` と出ていた（2026-08-26）。
**桁区切りの無い整数**で、単位も付かない。

> 通貨の呼び名は3通りに綴られている。
> 文中の `ゴールド`、英語表示の ` gold`、そして画面上部の `Gold`。
> 呼び名を差し替える側は3つとも拾うこと
> （`130_` は最初これを2つだと思っていて、画面上部だけ英語で残った）。

日本語の `所持金` は別物で、**キャラクタ作成画面**の見出し
（`筋力` `器用` `耐久` `知力` `判断` `魅力` `所持金` と並ぶ側。属性は `gol`）。
こちらは `tr` を通る。

> **見張りは渡された `value` を塗っている**（2026-08-26 に実機で確定。
> VERIFICATION_LOG.md §2.64）。
> `value` を差し替えるだけで画面が変わり、
> `self.status_texts` は読み直されていない。
> ネイティブコード化されていて読めない場所なので、
> これは実装を読んだのではなく**塗り終えたラベルを見て**確かめたもの
> （`130_` の保険の経路が1度も走らなかった）。
> ビルドが変われば逆に振れる側なので、書き換える側は保険を残しておくこと。

### 2.30 NPC の絵の作られ方（2026-08-30〜31 に `131_` で実測）

`image_generation.sdcppcuda.image_generation_creature` がキャラクタ・敵・モンスターの絵を
1本の経路で作る。NPC ごとのフォルダ
（`%LOCALAPPDATA%\Darmabeko\Instantale\worlds\<世界>\characters\<名前>\`）に残る5枚が、
そのまま工程の順:

| ファイル | 寸法 | 工程 |
| --- | --- | --- |
| `generated_image.png` | 512x1024 | SD の出力。`detect_face_coordinates` はこれに対して走る |
| `no_bg_image.png` | 512x1024 | 背景を抜いた絵（`remove_background`。rembg のサイドカー） |
| `pixelated_image_original.png` | 165x330 | `pixel_art_process(絵)` の戻り値 `(入力そのまま, 縮めた絵)` の2つ目 |
| `reduced_color_image.png` | 330x660 | 上を2倍に伸ばし、`reduce_image_colors` で 16色にしたもの。**立ち絵**。会話中に見えるのはこれ |
| `face_image.png` | 165x165 | 立ち絵から切り出した顔 |

- **描いた細かさは `pixel_art_process` で捨てられる**。165x330 に落として2倍に伸ばすので、
  立ち絵の中身は 165x330 ぶん。ドット絵寄りの画風はこの工程が作っている
- 2倍に伸ばす段は `pixel_art_process` と `reduce_image_colors` の間のコンパイル済みの中にあり、包めない
- `pixel_art_process` / `reduce_image_colors` は `scripts.image_processing` の関数を
  `image_generation_creature` と `image_generation_background` が別々に `from ... import` で持つ。
  片方だけに当てるなら写しの側へ `alias_scan=False` で。
  顔の2関数（`detect_face_coordinates` / `extract_and_save_face`）も同じ関係で、
  `scripts.image_processing.face_crop` の写しを `image_generation_creature` が持つ
- 縮小・減色・顔の検出・顔の切り出しは NPC と敵が同じ関数を通るので、工程の側では見分けられない。
  切り分けられるのは入口の2つだけ:
  `generate_enemy_image(world_name, race, name, appearance, size, positive_prompt=None)`
  （モンスター・衛兵）と
  `generate_enemy_image_from_character(world_name, name, fullbody_path, size)`
  （NPC の戦闘用の絵）。`131_` はここを包んでスレッドに旗を立て、
  その中に居る間は工程の包みを全部ゲームのままにする
- SD の段の設定（`character_generation_quality` の `highres_upscale` / `highres`）はここへ来る寸法を変えない。
  プロキシ MOD が 640x1216 で描いていても、この経路へ渡るのは 512x1024

#### 顔

- `detect_face_coordinates(image, cascade_path, padding=0.25, crop_size=256)` は
  `(左, 上, 右, 下)` の一辺 256 の箱を返す。見つからなければ `None`。
  箱は検出した顔の中心から 128 を引いた位置（端数は切り捨て）で、絵の縁で止まる
- 1体につき2回呼ばれる。`lbpcascade_animeface.xml` → `haarcascade_frontalface_alt.xml` の順で、
  `cascade_path` は `runtime/models/face_recognition/<名前>` と**フォルダ付き**で渡る。
  素のファイル名で呼ぶとカスケードが読めず `None` になる
- 感度は OpenCV の既定（`scaleFactor 1.1` / `minNeighbors 3`）。手元の 364 体で 16% が外れ、
  世界（画風）で 2%〜35% の差がある。暗い絵・コントラストの低い絵で外れる
- `extract_and_save_face(pixelated_image, coordinates, output_path)` は箱を **330/512 の定数**で縮めてから
  立ち絵から切る。立ち絵の実寸からは求めていないので、立ち絵の寸法を変えると顔がずれる
- 見つからなかった回は、立ち絵をもう一度 `pixel_art_process` に通した 16x32 を2倍にした
  32x64 の全身が顔の代わりになる

### 2.31 立ち絵のパスとセーブの置き場（2026-08-30 実測、`323_` の作業より）

```
%LOCALAPPDATA%\Darmabeko\Instantale\
├─ saves\<世界名>\savedata.json          遊んでいる世界（`app.save_data_dict`）
├─ worlds\<世界名>\world_data.json       世界の骨格（`app.world_dict`）
└─ worlds\<世界名>\characters\<名前>\    立ち絵
```

インストール先には無い。
Epic 版の `instantale.exe` の隣に `saves` も `worlds` も無かった。

`image_src` は**書いた機械の絶対パス**で入っている。
別の機械で作られた世界を持ってくると、他人のユーザー名を指したまま存在しない
（ペルディションは 95人中 93人が `C:\Users\Owner\...` だった）。
`worlds` から後ろだけを残して手元のデータの場所へ繋ぎ直せば当たる。

`face_image.png` の大きさは揃っていない。
実データ194件のうち 144件が 165×165、**40件は 32×64**、残り10件はまちまち。

##### 施設は生成されるまで無い。生成されたらギルドと宿が揃う

5世界の非ダンジョンのエリア45件のうち、

- 施設が在る 33件は**全件がギルドと宿の両方**を持つ
- 残る12件は施設が0件（まだ生成されていない。初訪問で作られる）

「ギルドが無くて宿だけ在る町」は生成されたデータには無い。

##### NPC に装備は無い

5世界 369体の `equipments` は**全て空**。
持ち物（`inventory`）は 28体（7.6%）が持っている。

## 3. 調査手法

純粋関数は総当たりで定義域を割り出す。
副作用の無い参照関数なら、稼働中のプロセスで直接呼んで有効域を特定できる
（`get_npc_employ_price` は `0..150` を走査して `0..76` と判明）。
引数の語彙も同じ手で確定できる（実在 id を渡して例外の出ない値を探す
＝ `QuestChoiceManager` の `'settlement_quest'`）。再現を待つより速い。

同じ手で**「その MOD がまだ要るか」も測れる**。
MOD が包む前の素の関数を控えておき、注入のたびに総当たりして上流の状態を記録する
（`101_` の `upstream:` 行。§1.5）。
能動的に起こせないバグほど、この形でしか判定できない。

- 判定に使わず記録するだけにする。
  「落ちなくなった」の一度の観測で MOD が自動的に降りると、
  測り方が間違っていたときに黙って穴が開く
- 副作用のある対象には使えない（`place_existing_item` はマスを占有するので突けない）。
  ああいう防御的な MOD は発火したら記録する形にして、沈黙が続いていること自体を証拠にする

ゲーム自身のヘルパを探すと、値を発明せずに済む。
`targets.txt` を `clamp` / `max` / `validate` / `generate` / `get_` で検索する価値がある。
修正が「ゲーム自身が別経路で行う処理を、抜けている経路へ適用するだけ」で収まれば、
余計なバランス調整をせずに済む。

ゲーム自身が作った `PhaseSpec` を読む。
コンストラクタが実際に受け取っている引数がそのまま入っているので、語彙を推測しなくてよい。

属性エラーは `__getattr__` トリップワイヤで捕まえる。
Python は通常のルックアップが失敗した後にのみ `__getattr__` を呼ぶので、
それはクラッシュの瞬間そのもの。
挙動を変えずに読み手のフレーム・行番号・ローカル変数が取れる。

**dict は値ではなくキーとキーの型を記録する**（`frames.repr_value`）。`KeyError` の原因究明で効く。
ただし「在るか」ではなく「何か」を見るなら値ごと出すこと
（既定の要約はトレースバック向けで、`is_dead` の真偽が読めなかった実例がある）。

状態は変化だけを高頻度で拾う。
20Hz で見て変わったときだけ書けば、量を増やさずに非同期処理の途中経過が取れる。

プロンプト関係は `output_data/` で実データ検証できる。
ゲーム自身が LLM へ投げた `messages` をそのまま保存している（12,067件・66マネージャ種）。
プロンプトを触る MOD は、ゲームを起動せずに全件へオフラインで通してから注入できる。
ただし**2026-08-09 以降の記録は `111_` 適用後**である点に注意（VERIFICATION_LOG.md §2.43）。

症状の側から判定条件を書く。
どの経路が壊しているかを突き止めなくても直せることがある
（鳴っている音を pygame に聞く、`quest_type` の語彙を知らずに済ませる）。

低頻度バグは再現を待たない。正常呼び出しからデータを取る計測を先に設計する。

オフライン検証は偽の app / `PhaseSpec` / マネージャ / Clock で組む。
偽の `on_button_press` も本物と同じ形にする
（`getattr(__main__, cls_name)(app, *args)` を組んで `process_choice` に渡す）。
スタックを見て判定する MOD は、テストも本番と同じ呼び出し元から呼ぶ形にする。
