# GAME: Instantale 内部リファレンス

実機で確かめた「ゲームがどう動いているか」。MOD を書くときに必要な、Instantale
そのものの構造・語彙・作法をここに集める。

- ローダの仕組みと MOD の書き方は [TECH.md](TECH.md)
- 遊ぶだけなら [README.md](README.md)
- 各 MOD の検証状況・未確認項目・実測ログは [VERIFICATION.md](VERIFICATION.md)

TECH.md と分けているのは、読む理由が違うから。あちらはこのローダで MOD をどう書くか
（他のゲームにも通じる話）で、こちらは Instantale が何をしているか（このゲーム限定の
事実）。ゲームが更新されて食い違うのはこちら側だけなので、疑う場所が1つに寄る。

> ここに書いてあるのはすべて実測で、ソースは読めない（Nuitka standalone）。
> 推測は書かない。確かめていないことは VERIFICATION.md の未確認項目に置く。

---

## 1. パッチ対象の見つけ方

### 1.1 リコン成果物 (`out/recon/`)

ソースが読めない以上、正確なパッチ対象名はここからしか得られない。

| ファイル | 内容 |
|---|---|
| `targets.txt` | `module:qualname(signature)` 形式。`@patch` にそのまま貼れる（1585件） |
| `game_modules.txt` | ゲーム自身のモジュールの全属性ダンプ（擬似ソース一覧） |
| `modules.json` | 全モジュールの機械可読インベントリ |
| `summary.txt` | 環境・`sys.path`・モジュール census |
| `bug_sites.txt` | crash_log.txt の各クラッシュ地点のプローブ + キーワード掃引 |

### 1.2 ゲーム自身のモジュール

```
__main__                       instantale.py, 516ターゲット
scripts                        scripts.hud.* / scripts.llm.* / items / functions ほか
                               scripts.save_codec, scripts.steam.server_process
Embedding, image_generation, llama_cpp_runtime_completion, sidecar_process
save_area_json, save_world_json, api_key_manager, build_type, sdcpp_cuda
```

`__main__` は `sys.stdlib_module_names` に含まれる。素朴に stdlib を除外すると
ゲーム本体が丸ごと漏れる（`recon.py` の `GAME_TOPLEVEL` はアローリスト）。

### 1.3 掃引で見つからないもの

- ネスト関数はモジュールのグローバルに現れない。`send_request_on_id` はトレース
  バックに 62 回出るが `vars(module)` には無い（`send_request` 内の `backoff` デコレータ
  付きネスト関数）。実際の対象は外側の `send_request` / `send_request_with_no_structure`
- クラスのメソッドはモジュールレベルのキーワード掃引で 0 件に見える
  （`set_ai_models` / `show_world_choice` など）。`game_modules.txt` を見る
- 属性名を推測して探すと空振りする。`vars(obj)` を一度全部出すほうが速い
  （HUD の描画先を `texts` / `labels` という名前で探して見つからなかった実例がある。
  正解は `hud.buttons[i].text`。§2.3）

### 1.4 環境の基本値

| 項目 | 値 |
|---|---|
| ゲーム本体 | `C:\Program Files\Epic Games\Instantaleq6Ve7\instantale.exe` |
| ランタイム | CPython 3.10.11 / Kivy / SDL2 |
| `game_version` | `014`（`__main__.get_game_version()`）。Epic の `AppVersion: main_023` は別系統 |
| ロード済みモジュール | 4208（うち 3243 が Nuitka コンパイル済み）／ゲーム自身は 72 |
| セーブ | `%LOCALAPPDATA%\Darmabeko\Instantale\` |
| クラッシュログ | `<ゲームdir>\crash_log.txt`。更新で消える（§1.5） |
| LLM 入出力の記録 | `<ゲームdir>\output_data\<世界>\<PC>\<manager>\N.json` |

ゲーム内部のバージョンは実行時に問い合わせること（Epic のマニフェストとは無関係）。

### 1.5 更新の記録

#### main_022 (`013`) → main_023 (`014`)（2026-07-30 に更新、同日リコン）

MOD 側の対応は不要だった。28/28 が適用され、警告・例外ともゼロ。

置き換わったのは `instantale.exe` だけで、同梱の site-packages と `python310.dll` は
2026-06-03 のまま（＝注入基盤は無傷。CPython 3.10 も据え置き）。

| | main_022 | main_023 |
|---|---|---|
| `game_version` | `013` | `014` |
| ロード済みモジュール | 4175（3240 compiled） | 4208（3243 compiled） |
| ゲーム自身のモジュール | 70 | 72 |
| `targets.txt` | 1466 | 1585 |
| `__main__` のターゲット | 516 | 516 |

現行の MOD が掴んでいるゲーム内の対象 78 件は、全て新ビルドにも存在する
（`targets.txt` と機械的に突き合わせ）。シグネチャも一致していて、
GAME.md が記録している以下は変わっていない:

```
QuestEndManager.__init__(self, app)                             引数ゼロ（§2.9）
InstantaleApp.remove_party_member(self, member_id)              （§2.8）
InstantaleApp.move_npc_to_facility(self, character_id, character_instance,
                                   target_facility, target_node=None,
                                   register_facility=True)      （§2.8）
QuestChoiceManager.__init__(self, app, quest_type, quest_id)    （§2.9）
ConversationEndManager.__init__(self, app, in_conversation_id,
                                finisher, end_text)             （§2.5）
send_request_with_no_structure(manager_name, message,
                               max_tokens=16384, timeout=None)  （§2.12）
SAVE_OBFUSCATION_KEY = b'Instantale_Save_Key_2026'              （§2.16）
```

セーブの方式も鍵も変わっていないので `tools\rebalance_saved_bgm.py` はそのまま使える。
復号は `scripts.save_codec`（`xor_with_key` / `read_obfuscated_json_file` /
`write_obfuscated_json_file` / `read_json_with_obfuscation_fallback`）に集約されている。

公式アナウンスの変更点と、それに対応する実測の対象:

| アナウンス | 実測で見えている対象 |
|---|---|
| 施設の処理そのものの自由生成（実験的） | `scripts.free_facility`（38ターゲット。`FreeFacilityManager` + `lint_program` / `validate_program` / `get_phase_class`）、`scripts.llm.llm_manager_free_facility`（`generate_program` / `generate_programs_for_node` / `run_llm_step` / `summarize_session`）。世界生成側の入口は `save_world_json:generate_new_world(..., free_facility_enabled=False)` と `create_settlement_detail(..., include_free_facility=False)`、UI は `WorldGenerateScreen.toggle_free_facility` |
| サウンドの設定 | `scripts.sounds:SoundManager.apply_music_volume(self, app)`（`106_` が包んでいる 4 メソッドとは別の新メソッド） |
| ワールドエディタ機能のための処理部品 | `scripts.save_codec`（`xor_with_key` / `read_obfuscated_json_file` / `write_obfuscated_json_file` / `read_json_with_obfuscation_fallback`） |
| 四体以上の敵で表示が壊れる／進行不可 | 戦闘側。未検証（更新後まだ戦闘していない） |
| スキーマの重複・増幅（ローカルLLM） | `105_` の COMPACT とは別物。§1.6 |
| サーバーの多重起動（ローカルLLM） | `LlamaCppSidecar`。§2.12 の所有者調停と関わる。未検証 |
| コンテキスト長限界の超過 | `102_` / `103_` と重なる。§1.6 |
| 味方が敵になる／世界生成の無限テキスト | 対応する対象を特定していない |

アナウンスに無いが増えているもの（新ビルドに存在することを確認した対象）:

| | |
|---|---|
| `scripts.steam.server_process` | Steam 認証と課金。`auth_steam_and_get_jwt` / `get_ticket_hex` / `get_entitlement` / `subscription_start` / `subscription_wait` / `cancel_contract` / `upgrade_start` |
| クラウドLLMのサブスクリプション | `hud_auto_configuration:AutoConfigurationScreen.use_cloud_subscription_process` / `hud_option:OptionAIScreen.show_subscription_screen` / `OptionLLMScreen.get_cloud_llm_list(cloud_billing_type=None)` / `check_device_info:get_setting_for_cloud_llm` |
| 装備の強化 | `__main__:EquipmentReinforcementStart` / `EquipmentReinforcementManager`（`calculate_modification(item_type, item_price)`）/ `InstantaleApp.reinforce_equipment` / `get_upgrade_equipment_price` / `toggle_reinforcement_inventory_window`、HUD 側は `set_reinforce_equipment_button_callback` / `toggle_reinforcement_inventory_visibility` |
| `Item` に `upgrade_level` | `Item.__init__(..., grid_pos=None, upgrade_level=0)` |

> 前版との厳密な差分は取れていない。`out/recon/` は注入のたびに上書きされ、
> main_022 の `targets.txt` は残っていない。上の表は、新ビルドに存在すること（実測）と
> アナウンスを突き合わせたもので、どれが本当に「増えた」のかを機械的には確かめていない
> （`scripts.free_facility` と `llm_manager_free_facility` は前版のリコンにも名前があった
> 可能性がある）。増えた 119 ターゲットの内訳も全部は説明できていない。
> 次の更新に備えるなら、リコン成果物を更新前に退避しておくとここが機械的に出せる。

> `enemy_count_per_battle` は更新で増えたものではない。`Quest` の
> `get_enemy_count_per_battle()` と併せて 2026-07-27 の記録（`quest_flow.log`）に既にある。
> 「四体以上の敵」の修正と結び付けて読まないこと。なお `Quest` のインスタンス属性は 19、
> セーブに出る dict のキーは 14 で、差分（`enemy_count_per_battle` /
> `remaining_boss_list` / `remaining_event_list` / `remaining_miniboss_list` /
> `remaining_normal_battle_count`）は実行時だけのもの。

`crash_log.txt` は更新で消えた。同梱されていた 114 件（`100_` や `204_` が件数を
根拠に挙げているもの）は現物が無くなっている。`out/crashlog_baseline.txt` の
173055 バイトも、もう対応する相手がいない。あの 114 件は `013` の記録であって、
いま走っているのは `014`。件数を根拠にする議論は、この境目を跨がせないこと。

### 1.6 公式修正と MOD の重なり（main_023、実測）

アナウンスの3項目は `102_` / `103_` / `105_` が直しているものと重なる。
`out/prompt_bloat.log` は 2026-07-27 21:50 から連続していて世代交代していない
（`out/*.log.1` は無い）ので、同一ファイル内で前後を比べられる:

| MOD | タグ | 更新前（07-27〜07-28） | 更新後（07-30 13:09〜14:46） | 判定 |
|---|---|---|---|---|
| `102_fix_prompt_dedup` | `[DEDUP]` | 3 | 0 | 不要（同じ操作を通して確認） |
| `103_fix_eventlog_trim` | `[EVENTLOG]` | 49 | 発火（15:16〜） | 引き続き必要 |
| `105_fix_schema_compact` | `[COMPACT]` | 359 | 継続（`8250 -> 2195` ほか） | 引き続き必要 |

- `102_` は仕事が無くなった。発生源で直っている（2026-07-30、クエスト生成を
  実際に通して確認）。更新前と同じメッセージで、重複だけが消えている:

  ```
  07-28  4 msgs, 7475c  system:2195:26683d974c, system:2195:26683d974c,
                        system:1898:59395e72f3, user:1187:843f040d98
  07-30  3 msgs, 4505c  system:2195:26683d974c,
                        system:1898:59395e72f3, user:412:aa22a58bd1
  ```

  `26683d974c` / `59395e72f3` はハッシュが一致＝同じメッセージ。`COMPACT` の
  `8250 -> 2195` も一致するので同一の経路（`QuestStructure` の生成）。
  重複していたのは圧縮後のスキーマ system メッセージで、それが2つ並ばなくなった。
  今日の `_apply_chat_template` 154 回（うち複数メッセージ 153 回）で
  隣接完全一致は 0 件
- `103_` は引き続き必要。一時 0 件だったのは出番が来ていなかっただけで、
  クエストを進めた 15:16 に発火した:

  ```
  [EVENTLOG] quest_referee_event_evaluate_new: dropped 3 turn(s), kept 3 | 1456 -> 531 chars
  [EVENTLOG] quest_referee_event_resolve:      dropped 3 turn(s), kept 3 | 1526 -> 601 chars
  ```

  `quest_event_log` が育つのはクエスト進行の後半なので、町にいる間はいくら遊んでも
  0 件のまま。公式の「コンテキスト長限界の超過」の修正はここを肩代わりしていない
- どちらも外さなくてよい。冪等で、対象が無ければ何もしない。むしろ残しておくと
  検出器になる。再び発火したらゲーム側の修正に穴があったということで、§2.12 で
  プロキシのログを取りこぼしの検出に使っているのと同じ考え方
- `105_` は引き続き効いている。こちらが削るのは
  プロンプト本文に埋め込まれたスキーマの repr（§2.12）で、ゲームが直した
  「再生成時にスキーマが重複・増幅する」とは別物。プロンプト全体も小さくなっていて
  （`total_chars` が 580〜2164）、両方が効いた状態に見える
- クラウドLLM経路は依然として未検証（§1.5 の注記のとおり）

> 0 件だけでは「不要になった」ことの証拠にならない。その操作を通していなければ、
> 出番が来ていないのと区別が付かない。`102_` は発火していた操作を実際に通し、
> 同じメッセージで重複が消えていることを見て初めて言えた。`103_` はまだそこに至って
> いない。戦闘も更新後は未実施。

### 1.7 起動直後に注入したときの見え方（更新とは無関係）

段階適用の途中経過が WARN として大量に出るので、更新で壊れたように見える。
2026-07-30 の実例（ゲーム起動 13:08:14）:

| 時刻 | patches | 中身 |
|---|---|---|
| 13:08:17（起動3秒後） | 11 / 9 target / 9 mod | `__main__` がまだ空。`module '__main__' has no attribute 'InstantaleApp'` が大量に出る |
| 13:08:23 | 47 / 35 / 20 | 一部のモジュールが import され、再適用 |
| 13:09:39 | 137 / 93 / 26 | 満額（12:42・12:50 の健全値と一致） |

- `boot complete: 28/28 mod(s) applied` は「掴めた」ことの証拠にならない。
  `apply()` が例外を出さなければ 28/28 になる。見るのは次の行の
  `patches: N applied on M target(s)`
- 満額は 137 / 93 / 26。ここに届いていなければ、まだ段階適用の途中か、対象を失っている
- 再注入は安全（`replacing a previous patch layer` が出て二重には掛からない）
- `ERROR bgm restore: channel scan failed`（`pygame.error: mixer not initialized`）も
  この状況で出る。迷子の曲の掃除が mixer 起動前に走っただけで、捕捉済み・処理は継続。
  2026-07-27 から出ており更新とは無関係。満額の注入以降は再発していない

### 1.8 影響を確かめていないこと（main_023）

- クラウドLLM経路にプロンプト系 MOD が効くかは未確認。`102_` / `103_` / `105_` と
  `301_` / `305_` は `llama_cpp_runtime_completion:LlamaCppClient.chat` と
  `request_llm_inference_llama_cpp_completion:send_request*`（＝ローカルの llama.cpp）を
  掴んでいる。サブスクリプション経路が別の送信口を通るなら、そこは素通りになる。
  ローカル実行では従来どおり効いている（§1.6）
- 自由生成施設（`FreeFacilityManager`）の最中に `300_` の施設イベントが乗るか未確認。
  どちらも「施設に入ったとき」に働くので、二重に始まる余地がある（§2.5）
- 四体以上の敵との戦闘を更新後にまだ通していない。公式修正が入った箇所なので、
  `106_`（BGM の引き取り）/ `107_`（`in_battle` の下ろし忘れ）/ `207_` と
  噛み合うかはこれから
- サイドカーの多重起動抑止が三者競合になった。ゲーム自身の修正・
  `LlamaCppSidecar` の所有者調停・InstantaleLLMProxy の3つが同じことを見る（§2.12）
- 装備強化の画面が `109_`（アイテム詳細の自動伸縮）と噛み合うか未確認。
  `ItemDetailBox` の構造（`update_content` / `define_text_color` / `_update_rect`）は
  変わっていないが、`upgrade_level` が詳細欄に出るなら文字量が増える側の変化になる
- `InventoryGrid` に `try_place_item(self, item, pos)` と `get_unique_items(self)` がある。
  `108_` が掴む `place_existing_item(self, item)` とは別経路。強化画面のグリッド
  （`toggle_reinforcement_inventory_visibility`）で同じはみ出しが起きるかは未確認

---

## 2. ゲーム内部リファレンス

### 2.1 スレッド

```
process_choice(MovePhaseManager, ...)                   [MainThread]
ConversationStartManager.execute('テストNPC A')         [Thread-767 (execute)]
QuestEndManager.execute -> method_1                      [別スレッド]
```

`process_choice` はメインスレッドで呼ばれ、その中で `execute` を別スレッドへ渡す。

- UI と pygame を触るのは Kivy の `Clock` から。`execute` の中から直接触らない
- Clock から押す実装が本来のボタン押下と同じ経路になる。自前でスレッドを立てる必要は無い
- 長い処理は `execute` の中で同期的にやりきる。そこからさらに別スレッドへ投げて即座に
  返すと、ゲームは行動が終わったと判断して操作を戻してしまう
- 非同期に渡される処理を、呼び出しの前後で測ってはいけない。`process_choice` は
  `execute` を渡して即座に返るので、その前後は「最中」を捉えない。状態を継続的に監視するか、
  内側のフックで測る

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

`PhaseSpec(cls_name, args)` はマネージャのインスタンスではなくその作り方。押されると
`getattr(__main__, cls_name)(app, *args)` が組み立てられ `app.process_choice(それ, 文字列)` に渡る。
押された添字は `display_button_map` で引き直される（`ui.pressed_entry` が同じことをする）。

> `app.function_correspond_to_input` は名前に反して対応表ではなく `PhaseSpec` 1個。
> 「いま自由入力を送ったら何を呼ぶか」を保持している。

自前のクラス名を `PhaseSpec` に書かないこと。`PhaseSpec.to_dict()` が存在する＝
ボタンはセーブに焼き込まれうる。自前のクラス名を書くと、MOD 無しで起動したときに
`getattr(__main__, ...)` が失敗する。注入はプロセスと一緒に消えるので、これは必ず起きる。

自前ボタンの作り方: 無害な既存クラス（`JustSetButtonToNormalPhase`）を spec に持たせ、
押下は `InstantaleApp.on_button_press` を包んでボタン辞書に足した独自キーで横取りする。
文字列ではなく印で見るのは、同じ文字列のゲーム側ボタンを巻き込まないため。MOD が無ければ
残骸のボタンは無害な動作になる。

自前で組む `PhaseSpec` は、引数の値まで実測で確かめたものに限る。ボタンが押された
瞬間にゲーム側で実行されるので（`getattr(__main__, cls_name)(app, *args)`）、こちらの
`try`/`except` の外側。引数を1つ間違えるとそのままゲームが落ちる。

### 2.3 選択肢を変える手順

この3点を外すと、データは正しいのに画面が変わらない。

1. `process_choice` を通す。`app.buttons` を書き換えて `refresh_choice_buttons()` を
   直接呼んでも画面は塗り替わらない。ゲーム自身は選択肢を変えるとき必ず
   `process_choice(マネージャ, 文字列)` を通す。自前のフェーズクラス（`execute(choice_text)`
   だけを持つ）を作って同じ経路に乗せる。そのクラスは `PhaseSpec` には載せない
   （`process_choice` はインスタンスを受け取るので載せる必要も無い）
2. 押下と同じ流れの中で差し替えない。ゲームは押下処理の中で描画するので、その前に
   差し替えると後の描画で古い内容に戻される。`Clock.schedule_once(..., 0)` に載せれば
   次のフレーム・メインスレッドの両方が同時に片付く
3. 塗るのは HUD。`refresh_choice_buttons` は `to_display_buttons` と
   `display_button_map` を組み直すところまで。実際に塗っているのは
   `scripts.hud.new_hud:InstanTaleHUD.update_button_texts(self, instance, value)`（Kivy の
   プロパティ監視）。監視対象のプロパティは HUD 側にあり、`app.to_display_buttons` は
   監視対象ではない（空にして入れ直しても dispatch されない）。`hud.update_button_texts`
   を直接呼ぶ。HUD は属性名ではなく `InstanTaleHUD` の型で探す

いずれも `ui.Screen.apply_buttons` / `Screen.paint` に入っている。MOD 側で書き直さないこと。

画面に実際に出ている文字は `hud.buttons[i].text`（`app.to_display_buttons` とは別物）:

```
hud={'buttons': ['テスト討伐依頼A', 'クエストを探す', 'やめる', ''],
     'status_label': 'Atk:299(+326)...', 'send_disabled': False}
```

枠数は `len(hud.buttons)`（実測 4）で固定。自由入力の可否は
`hud.text_send_button.disabled`。画面の状態を観測するならここを見る。

本文（情景描写・LLM の応答・システムメッセージ）を描いているのは `hud.text_display`
（`out/text_spacing.log` の実測、2026-07-31。`112_ui_text_spacing` が実行時に探し当てた）:

| | 実測値 |
|---|---|
| ウィジェット | `hud.text_display`（`kivy.uix.label.Label`）|
| `font_size` | `27` |
| `line_height` | `1.8`（Kivy の既定は 1.0。行間はゲームが意図的に広げている）|
| `text_size` | `[1340.8, None]`（幅だけ固定・高さは無制限）|
| `texture_size` | `[1340, 3738]`（750文字の本文で）|

`hud.text_display.text` は `hud.display_text` と完全一致しない。実測では
「片方がもう片方の末尾（または先頭）を含む」関係で、ゲームが塗るときに何かを
足すか削るかしている。本文のラベルを文字列で探すなら完全一致を前提にしないこと
（`112_` はここで一致タイプを段階に分けて拾っている）。

高さはゲーム自身が `InstanTaleHUD.update_label_height()` で決め直す。
ラベルの `line_height` や `text` を触ったら、`texture_update()` の後にこれを
呼べば本文の高さがゲーム側の計算で揃う（`112_` が実機でこの経路を通している）。

### 2.4 待機表示（「…」のアニメーション）

ゲームは長い処理の間、こうやって操作を止めている:

| 要素 | 値 |
|---|---|
| `app.is_button_enabled` | `False` |
| `hud.buttons[i].text` | `.` → `..` → `...` のアニメーション（約 0.3 秒周期）。全枠に出る |
| `hud.text_send_button.disabled` | `True` |
| `app.text_input_disabled` | `False` のまま（＝これは機構ではない） |
| `app.buttons`（spec の一覧） | 触らない。表示だけ差し替えるので後始末が要らない |

自前の処理でも同じものを出せる（`301_` の `show_busy()` / `clear_busy()`）。

画面の繋ぎ目を隠すのにも使える。会話を閉じてから次の画面を開くまでの間、
`ConversationEndManager` の終了処理が `app.buttons_backup`（会話相手の一覧）を復元するので
一瞬それが見える。ゲームが正しく元の画面へ戻しているだけなので止められないが、待機表示を
出したまま繋げば隠せる:

```
押下 → show_busy() → 会話を閉じる → in_conversation が落ちるのを待つ
     → clear_busy(restore=False) → 次の画面を開く
```

`restore=False` が要点で、「元の選択肢を塗り直さない」＝古い画面を出さない。点の
アニメーションはゲーム自身の待機表示と同じものなので、割り込みが挟まったようには見えない。

### 2.5 会話

会話画面には必ず「会話を終了する」が並び、その spec は:

```
ConversationEndManager(app, in_conversation_id, finisher, end_text)
args = ['77', 'user', '<行動: 会話を終了する>']
```

- `args[0]` がいま話している相手の id。画面のボタンを読むだけで相手が分かる
  （`ConversationStartManager` を追跡する必要は無い）。仲間の label から入る経路
  （`on_member_label_press` → `process_party_member_choice`）でも会話画面になれば並ぶ
- 会話中の `app.buttons` は「会話を終了する」1個だけ。会話画面も施設と同じ選択肢
  リストを使うので、自前の選択肢はその手前に挿せばよい
- 会話は「状態」であって画面ではない。立ち絵の片付けも関係値の更新も終了処理の中に
  あるので、ボタンを別の画面に差し替えただけでは閉じられない（`app.in_conversation` が
  残ったままになり、NPC の立ち絵が移動しても付いてくる）
- 閉じるときは画面のボタンの `args` をそのまま写し、`end_text` だけ差し替える。
  そこは自由記述なので、事情を書けば会話の要約とライフログに残る
- 閉じ終わるまで待つ（要約で LLM が回るため最大 120 秒程度）。`app.in_conversation` が
  落ちるのを Clock で見張る

画面の見分けは文字列ではなく spec のクラス名で行う（表記や言語設定に依存しない）:

| 目印 | 意味 |
|---|---|
| `ConversationEndManager` がある | 会話画面 |
| `DisplayTalkChoice` がある | 会話相手を選べる＝施設のルートメニュー |

依頼一覧（`QuestChoiceManager` が並ぶ）にはどちらも無いので入れ子にならない。

main_023 で `FreeFacilityManager` が `process_choice` を通るようになった
（施設の自由生成。§1.5）。2026-07-30 の実プレイで 16 回観測:

```
process_choice(FreeFacilityManager, choice_text='店を去る') [MainThread]
```

- クラス自体は `__main__` ではなく `scripts.free_facility` にある。
  `getattr(__main__, cls_name)` では引けない
- 上の2つの目印はどちらも立たないので、「会話画面でも施設のルートメニューでもない
  第3の状態」として通り抜ける。`300_` / `301_` / `206_` はこれで例外を出していない
  （実測）が、自由生成施設の最中を「施設のルートメニュー」と見なす作りにしていると
  取り違える。判定は目印の有無で書き、既知以外を既定側に倒さないこと

「行動」メニューは `app.buttons` とは別系統（HUD 上部の info レイアウト）:

```
set_top_info_layout_conversation_button_callback:
    callbacks_left  = InstantaleApp.show_npc_item_window
    callbacks_right = InstantaleApp.toggle_to_action_in_conversation
set_top_info_layout_action_in_conversation_button_callback:
    callbacks_button_2 = InstantaleApp.toggle_from_action_in_conversation   （戻る）
    callbacks_button_3 = InstantaleApp.start_battle_with_in_conversation    （戦闘）
```

会話フェーズを自分から起こすには、プレイヤーが NPC を選んだのと同じ経路に乗せる:

```python
app.process_choice(ConversationStartManager(app, npc_id), npc_name)
```

`DisplayTalkChoice`（NPC 一覧）は挟まなくてよい。立ち絵・会話履歴・関係値・終了処理は
すべてゲーム本来の実装が動くので、こちらで UI を触る必要が無い。

会話開始の合図は `<行動: 話しかける>`。向きを変えたいときは
`llm_manager:conversation_starter` に渡す messages のコピーだけを差し替える
（ゲームが持つ会話履歴には触らない）。

### 2.6 割り込みのタイミング

移動・クエスト終了・会話終了の後始末（テキストの流し込み・ボタンの張り替え・要約）の
最中に割り込むと噛み合わない。`ui.IDLE_SIGNALS` = `is_adding_text` / `is_button_enabled` /
`is_popup_window_opened` を Clock で見張り、手が空いてから実行する（`ui.Screen.when_idle`）。

戦闘・会話中かを見るフラグ: `in_battle` / `in_boss_battle` / `in_colosseum_battle` /
`in_conversation` / `in_free_input` / `in_action_in_conversation`。

`in_shopping` は状態の判定に使えない。店の外を往復しているだけの移動でも True の
まま残る。買い物窓が開いているかは `is_popup_window_opened` で見る。`in_battle` も経路に
よって下ろし忘れがある（§2.10）。

> フラグ名が意味するとおりに動いているとは限らない。条件に使う前に実測で裏を取ること。

### 2.7 世界のデータ構造

現在地は `app` ではなくプレイヤーのキャラクタにぶら下がっている（`app` 側の 97 属性に
current_facility の類は無い。`app.current_action` は行動文、`app.location_image` は背景画像パス）。

```
app.player.location      -> Facility   app, characters, choices, config, connections,
                                       description, facility_type, id, name, owner, parent_node
app.player.current_node  -> Node       facilities(dict), entrance_facility, ...
app.player.current_area  -> Area       name, descriptions, bgm, resident_npcs, size, ...
app.world.characters     -> {id: Character}    Facility.owner はこの id（str）
```

- 施設は `areas[id].nodes[nid].facilities[fid]` の入れ子。`initial_location` は
  `{"area": "7", "node": null, "facility": "127"}` で `node` は null のことがあるので、
  ノードを総当たりして探す（`ui.find_facility`）
- `player.current_area` はエリアのオブジェクトとは限らない（NPC 側のセーブでは `"7"` と
  いう id の文字列）。どちらでも引き当てること。エリア表も `world.areas` と決めつけず、
  `nodes` を持つものが並んだ辞書を中身で見分ける
- `Facility.characters` は重複が入ることがある（`['69', '69']`）。話者を選ぶときは一意化する
- ギルドは `facility_type == 'guild'`（`ui.find_guild`）
- 実在する `facility_type`: `entrance` / `exit` / `ward` / `guild` / `inn` /
  `general_store` / `specialty_shop` / `blacksmith` / `medical_facility` /
  `administrative_office` / `underworld_office` / `colosseum` / `slave_market` /
  `location` / `dungeon_location`。うち `ward` / `location` / `entrance` / `exit` は
  主のいない通路

### 2.8 パーティ

名簿の在り処も形も決めつけない。セーブでは `game_variables['party']` が
`['player', '63', ...]` の id 配列だが、実行時に `app.party` から同じものが読めるとは
限らず、`list` とも限らない（`{id: Character}` の辞書のこともある）。

`302_` の `party_stores` / `pick_store` が採っている手順:

1. 候補を全部集める。`app.party` / `app.game_variables['party']` /
   `world_dict['game_variables']['party']` / `world_dict['party']` / `world.party` /
   `player.party`、加えて名前に `party` が入る属性・キーの掃引（`escaped_member_in_battle`
   のような紛らわしい配列を拾わないよう名前で絞る）
2. 中身を見て本物を選ぶ。名簿には必ず `'player'` が入る
3. `list` と `dict` の両方を受ける（辞書ならキーを id として読む）
4. 要素が id の文字列でも Character のインスタンスでも読む
5. 書くときは同じ id を持つ入れ物すべてから落とす
6. 1つも見つからなければ app の持ち物を全部書き出す（`dump_census`）

関連する `game_variables`:

| キー | 意味 |
|---|---|
| `original_party` | 一時的に差し替えたときの控え。入っている間は名簿を触らない |
| `quest_party_accompany_backgrounds` | クエスト同行者の背景（サマライザに渡る） |
| `escaped_member_in_battle` | 戦闘から逃げたメンバー |
| `is_party_member_talk_enabled` | 仲間に話しかけられるか |

外す処理は書かない。ゲーム自身のものを呼ぶ。

```
InstantaleApp.remove_party_member(member_id)
InstantaleApp.get_party_leave_facility(character_instance)      -> (施設, ノード)
InstantaleApp.move_npc_to_facility(character_id, character_instance,
                                   target_facility, target_node=None, register_facility=True)
```

- 名簿を実際に書き換えているのは `remove_party_member` の中。外す/外さないを決めたい
  なら、この呼び出しを通す/通さないだけでよく、名簿に指一本触れる必要は無い
- `get_party_leave_facility` は `(施設, ノード)` のタプルを返す。そのまま
  `move_npc_to_facility` に渡すと `'tuple' object has no attribute 'characters'` で落ちる
  （別れること自体は成功して置き直しだけが失敗するので気付きにくい）。ほどいて
  `target_facility` / `target_node` に入れる。中身が何なのかは解釈しない
- `remove_party_member` 自身は NPC を動かさない。置き直すのは呼び出し元で、
  removal の後
- 置き場所を決めずに外さない。置き先が引けない土地（ダンジョン等）で外すと、その NPC
  は世界のどこにも居なくなる。逆に、本当に外れた相手の置き直しを止めてもいけない

クエストクリアの解散:

```
add_text('パーティは帰還した...') → 報酬・才能
remove_party_member('71' 'テスト仲間C')
  from QuestEndManager.method_1 (instantale.py:6602)
  <- QuestEndManager.execute (instantale.py:6635) <- run (threading.py:953)
add_text('テスト仲間Cはパーティから離脱した。')
```

- `QuestEndManager`（`__init__@6508` / `method_1@6511` / `execute@6634`。解散は 6602 行）。
  別スレッドで走る。放棄側は `QuestRetireManager`（`method_1@6642` / `execute@6713`）
- 帰還は解散より先なので、解散の時点で `player.current_area` はもう町。「いま居る町」を
  その場で引いてよい
- ゲーム側の置き先は `initial_location`（雇用された場所）

解散の中かどうかはコードオブジェクトの同一性で見る。

- 段数で数えない。`@ctx.wrap` の層が1段挟まる。`frames.caller` はファイル名で
  `runtime/` 配下を飛ばす
- 関数名でも足りない。`method_1` / `execute` は12個のマネージャが持つ名前。
  `frames.owner_of` が持ち主クラスを名指しする
- 毎回の判定を軽くしたいなら `QuestEndManager.method_1` / `.execute` の `__code__` を先に
  引いておき、スタックの `f_code` と突き合わせる（辞書引き1回）
- `move_npc_to_facility` ではスタックを見ない。NPC の日常の移動でも呼ばれるので、
  「いま解散した相手か」を辞書で引くだけにする

### 2.9 クエスト

格納場所は2つある。書くときは必ず両方。

```
app.world.quests          {id: Quest インスタンス}   ゲームが遊ぶときに読む
app.world_dict['quests']  {id: dict}                 セーブに出るのはこちら
```

片方だけ直すと画面の表示と保存内容がずれる。新規 id の検出も両者の合併を取る。

掲示板（`DisplayQuestChoice`）のボタン構成:

```python
app.buttons = ['テスト討伐依頼A' -> PhaseSpec('QuestChoiceManager', ('settlement_quest', '2')),
               'クエストを探す'   -> PhaseSpec('QuestSearchManager', ()),
               'やめる'           -> PhaseSpec('JustSetButtonToNormalPhase', ())]
```

- `QuestChoiceManager(app, quest_type, quest_id)` の `quest_type` は `'settlement_quest'`。
  クエスト辞書の `quest_type` フィールド（`'normal_quest'` など）とは別の語彙で、
  セーブの値をそのまま渡すと `KeyError` で落ちる。`world.quests` に対して通るのは
  `'settlement_quest'` だけ（他の候補は全て `story_quests` 側の分岐に落ちる）
- ゲーム自身の依頼生成の入口は `QuestSearchManager`（「クエストを探す」）。
  `DisplayQuestChoice.generate_random_quest()` は「いまこの土地に依頼を1件作って登録する」
  内側の入口で、クエストエリアの生成・id の採番・登録まで面倒を見る。内容に手を入れたいなら
  さらに内側の `llm_manager_world_generate:random_quest_generator` の引数
  （`area_description`）に足すだけでよい。出力スキーマは1バイトも変わらない
- 一覧はゲームに組ませるのが安全。`DisplayQuestChoice` を `process_choice` で開けば、
  一覧の組み立ても受注画面への受け渡しもゲームがやる＝語彙を知らなくてよい
- 受注できる依頼の絞り込みは `neighboring_settlement_id == 現在エリアの id` かつ
  `config['status'] == 'incomplete'`（依頼は集落ごとに3件ずつこのキーで束ねられている）。
  ゲーム自身の `get_quest_difficulties(area, world)` と突き合わせられる
- `QuestStructure`（`random_quest_generator` の出力）は `quest_title` / `client_name` /
  `request_summary` / `client_statement` / `area` / `events` / `enemies` / `boss`。
  ゲームはこれに `difficulty` / `neighboring_settlement_id` / `id` / `quest_type` /
  `config` / `quest_area_id` を足して保存する
- 元からある依頼の `client_name` は実在 NPC と結び付いていない（世界生成時に付いた名前）。
  NPC 単位で依頼を辿りたいなら MOD 側で控えを持つしかない
- クエスト辞書に独自キーを足さない。セーブに焼かれるうえ、再読み込み後に `Quest`
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

`203_` の記録（`probes.log`）より。同じ1クエスト中の推移:

| 時刻 | `Battle.enemies` | `FieldEvent.event_name` | `EncounterFinalBoss.enemies` |
|---|---|---|---|
| 09:48:05（1ターン目） | `Literal[7]` | `Literal[2]` | `Literal[1]` |
| 09:51:27 | `Literal[6]` | `Literal[2]` | `Literal[1]` |
| 09:56:44 | `Literal[1]` | `Literal[1]` | `Literal[1]` |
| 09:59:04 | `Literal[1]` | モデルごと消滅 | `Literal[1]` |

- `Literal` の中身は「残り」（倒した敵・消費したイベントは消える）。クエスト辞書の
  `enemies` 全体ではない
- 候補が 0 件になったモデルはゲームが union に入れない（`FieldEvent` で実証）。
  つまり空 `Literal[]` を避ける分岐は存在する。ただし 0 件を観測できたのは `FieldEvent`
  だけで、`Battle` / `EncounterFinalBoss` が 0 件になる場合は未観測
- `ReturnAfterCompletion` は1ターン目から毎ターン作られている。ボス健在でも union に
  居る。したがって「攻略しないと帰還できない」はスキーマではなくプロンプトの縛りで、
  referee の system に `return_after_completion: クエストを攻略した後にのみ実行可能` と
  書かれている。同様に「ラスボス撃破か撤退でのみ終了」「戦闘ベースのRPGなので
  field_event よりも battle が多くなるように」も全て地の文の指示

この性質のおかげで、討伐以外のクエストはプロンプトの差し替えだけで成立する
（`305_mini_quest`）。ゲーム側のコードにもセーブにも触る必要が無い。

### 2.10 戦闘・フラグ

戦闘終了マネージャは3つあり、経路によって挙動が違う。

| マネージャ | 入口 | `end_phase` 完了時の `in_battle` |
|---|---|---|
| `BattleEndManager` | クエスト中のエンカウントなど通常の戦闘 | 0（ゲーム自身が下ろす） |
| `BattleEndInFreeAction` | 自由入力・会話から入った戦闘 | 1（下ろし忘れ） |
| `BattleEndInColosseum` | コロシアム | - |

`in_battle` はセーブに入り、ロード時の分岐に使われる（`instantale.py:1458` が戦闘BGM、
`:1460` がエリアBGM）。残ったまま保存すると次のロードが戦闘BGMで始まる。MOD 側でも
「戦闘中は出さない」条件に使われるので、残骸があるとイベントが出なくなる。
残骸かどうかは `app.current_enemy_dict` が空かで見分けられる。

`in_boss_battle` / `in_colosseum_battle` は 1→0 の遷移を観測できていない。

### 2.11 BGM

```
play_music_from_src(app, src)   app.music に差し替えて再生
stop_music(app)                 app.music を止める
apply_music_volume(app)         main_023 で追加。音量設定（§1.5）
```

`SoundManager` の現在のメソッドは実測で
`apply_music_volume` / `play_music_from_src` / `play_sound` / `play_sound_from_src` /
`stop_music` / `stop_sound` の6つ。`106_` が包んでいるのはこのうち4つで、
`apply_music_volume` は包んでいない（`app.music` を差し替えないので取っ手は失われない）。

`app.music` が「今鳴っている曲」の唯一の取っ手で、ここを失った曲は誰にも止められなくなる
（プロセスが終わるまで残る。チャンネルは8本しかないので、埋まると効果音も鳴らせなくなる）。
`BattleEndInFreeAction` の復帰呼び出しは app ではなく別のオブジェクトを渡してくるので、
`app` を受け取る関数を包むときは渡されたものが本当に app か確かめること
（`getattr(x, k, "<missing>")` で「属性が無い」を `None` と区別して記録する）。

音の状態は自前の帳簿ではなく pygame に聞く。`Sound.get_num_channels()` と
`pygame.mixer.Channel(i)` で「今どの音が実際に鳴っているか」が分かる。曲は再生のたびに
`Sound(パス)` で作られるので、`SoundManager.sounds`（起動時に読む15種）と `play_sound*` で
鳴らされたものを除外集合にすれば、残ったループ音は曲と言える。

止められなくなった曲は、止めずに `app.music` へ入れ直すと音が途切れず、しかも以後
ゲーム自身の `stop_music` が効くようになる。

エリアBGMのパスはエリア生成時に確定し、セーブに焼き込まれる。

```
areas["7"]["bgm"] = "Assets/sounds/musics/town/solemn/Ambient 7 Loop.mp3"
```

フォルダ2段が判定結果（`town|village|city|dungeons|battle` ＝ `area["size"]`、その下が
雰囲気 `calm`/`eerie`/`majestic`/…）で、末尾が曲。

- フォルダの決定権は `area["size"]` にあり、保存済みパスから取ってはいけない
  （誤ったフォルダに入ったエリアが永久にそのフォルダ内で再配分され続ける）
- 表記揺れ: エリアは `dungeon`（単数）、フォルダは `dungeons`（複数）
- `musics/` 直下の単発曲を指しているエリアは意図的な指定とみなして触らない
- どのフックで `bgm` が確定するかはコンパイル済みのため特定できない。候補は
  `save_area_json:write_area_data_to_world_dict` / `save_area_json:generate_quest_area` /
  `save_world_json:write_obfuscated_json_file`。どれが何回発火しても結果が変わらない
  書き方にして、全部に仕掛けるのが実務的

乱数は MOD 専用の `random.Random` を使う。グローバルの `random` から引くとゲーム自身の
乱数列がずれる。

### 2.12 LLM 経路とプロンプト

```
llama_cpp_runtime_completion:LlamaCppClient.chat                             上流
llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template             messages
llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry   payload
scripts.llm.request_llm_inference_llama_cpp_completion:send_request*         リクエスト
scripts.llm.llm_manager:*                                                    マネージャ群
```

- ストリーミング経路は `_post_with_model_loading_retry` を通らない。`prompt` と
  `json_schema` が揃う唯一の地点はそこだが、実際に流れるのは `chat` 側。位置が確信
  できないときは、判定条件を保ったまま複数箇所に仕掛ける。どこで何回発火しても結果が
  同じになる書き方にしておけば、二重に効いても壊れない
- その代償として、ログの件数はそのまま呼び出し回数にならない。1回の推論が複数の
  フック地点から記録されるので、`prompt_bloat.log` の行を数えると実際の3〜4倍に
  膨らむ（2026-07-30 の実測: 146 行 → 相異なる秒は 41。同一ミリ秒に 2〜4 行が 45 箇所）。
  数えるならタイムスタンプで一意化してから。これを怠って「再送が5回→89回に激増した」と
  誤読しかけた

  ```
  14:44:54.378  _apply_chat_template: ... layout=...user:412:aa22a58bd1   ← 同じ1回の推論が
  14:44:54.379  _apply_chat_template: ... layout=...user:412:aa22a58bd1   ← 4行に見える
  14:44:54.379  _apply_chat_template: ... layout=...user:412:aa22a58bd1
  14:44:54.379  _apply_chat_template: ... layout=...user:412:aa22a58bd1
  ```
- プロセス内の本文は復号済み。プロキシが見ていた HTTP ボディでは改行が `\n` の
  2文字、日本語が `あ`（`json.dumps(ensure_ascii=True)`）だった。プロセスの中で
  `messages` / `payload["prompt"]` として見えるのは復号後の Python 文字列なので、
  ボディの形を前提に書かれた文字列（プロキシ用の置換ルールなど）はそのままでは当たらない。
  `111_` は置換前を素の形と復号形の両方で持ち、置換後は必ず復号している
- `send_request_with_no_structure(manager_name, messages, max_tokens=, timeout=)` は
  `str` を返す（`output_data/` の記録が `{"text": ...}` なのは保存側の形式）
- `output_data/` の記録は `LlamaCppClient.chat` より上流で取られる。
  保存は `send_request*` の中（`save_output_log`）なので、`chat` を包んで書き換えた
  内容は記録に一切映らない。2026-07-28 に `305_` の書き換えを「効いていない」と
  誤判定しかけた。同じ記録の中で、実機で効いていることが確定している `105_` の
  COMPACT 後も `'$defs'` が 64件中39件そのまま残っている。これが上流である証拠。
  `chat` に仕掛けた MOD の判定は、その MOD 自身のログで行うこと
- `manager_name` を自前の名前にすると、自前のプロンプトも
  `output_data/<世界>/<PC>/<manager_name>/N.json` に残る
- `quest_event_log` はリストではなく文字列。区切り（「〈プレイヤーの入力〉」）で割る
- `messages` の重複は完全一致・隣接で現れる。テキスト走査は不要で `(role, content)` の
  比較で落とせる
- ゲームは `json_schema`（grammar の実体）と同じスキーマを、Python dict の repr として
  プロンプト本文にも埋め込む。構造は grammar がトークン単位で強制するので、本文側に
  必要なのはフィールド名・enum 候補・参照先の型名だけ:

  ```
  元:   {'$defs': {'Location': {'properties': {'name': {'title': 'Name', 'type': ...
  後:   Location: name, kind:∈{shop,inn}
        Area: name, locations:Location[], atomosphere:∈{tense,normal}, note?
  ```

- `ast.literal_eval` は使えない。式1個しか受け取れず終端位置を返さないので、
  プロンプトの途中から読み始めて置換範囲を決められない。再帰下降パーサが要る
  （`True/False/None` と `true/false/null`、両クォート、末尾カンマ、タプル表記）

クエスト1件に関わるマネージャ（`output_data/` の実データで確認）:

| マネージャ | 役割 | 討伐固定の文言 |
|---|---|---|
| `random_quest_generator` / `settlement_quest_generator` | クエスト構造の生成 | 有（「【討伐】…を生成」「normal2-3種、miniboss1種、boss1種を必ず設定」） |
| `quest_starter` / `quest_starter_with_party` | 開始ナレーション＋初期選択肢 | 無（構造を読んで描写するだけ） |
| `quest_referee_with_free_action` / `quest_referee` | 毎ターンの進行判定 | 有（完了条件・battle 優先・ラスボス強制） |
| `field_event_evaluator` | イベント中の入力を「確定」か「確率判定」に振り分け、説得力を1〜10で採点 | 無 |
| `quest_referee_event_resolve` / `_event_rewrite` | イベントの結末と効果（damage/heal/get_item/status/start_battle/no_effect） | 無 |
| `quest_summarizer` ほか | 帰還後の要約 | 無 |

つまり討伐前提が書かれているのは生成と進行判定の2箇所だけで、イベント処理も
描写もクエスト種別に依存していない。

サイドカー（`LlamaCppSidecar`）は `__init__` / `start` / `_kill_existing` /
`_find_free_port` / `_wait_for_ready` を持つ。`start` の `additional_params` はリストなので
`--parallel 1` の追加は容易。InstantaleLLMProxy と併用する場合、多重起動抑止だけは
所有者調停が競合する（プロキシ側を `singleton_enabled=0` にする）。DEDUP / COMPACT /
EVENTLOG は二重に適用しても結果が変わらないので併用してよく、プロキシ側のログに出たら
MOD の取りこぼしという検出器になる。

### 2.13 インベントリのグリッド

所持品・売買画面（twin inventory）は `scripts.hud.new_hud:InventoryGrid`。

```
InventoryGrid   cols=4  rows=6  len(slots)=24  size=[259, 389]  spacing=[1, 1]
                situation=None（所持品） / 'shop'（売買）
アイテム        width_slots / height_slots / size=[64,64]（1マス）/ [129,129]（2x2）
                current_slots=[17, 21, 18, 22]   ← 占有マスは添字の配列で持つ
```

`grid_x` / `grid_y` / `slot_size` はアイテムの属性としては存在しない（`<missing>`）。
位置は `current_slots` の添字とピクセル座標で持っている。

ゲーム自身が持っている配置の道具:

| | |
|---|---|
| `is_valid_placement(...)` | そこに置けるか |
| `find_placement_position(w, h)` | 空きを探す |
| `place_new_item(item)` | 新しく置く（店の在庫を初めて並べるときの経路） |
| `place_existing_item(item, x, y)` | 既存の位置を復元する。置けるか確かめずに `occupy_slots` を呼ぶ |
| `occupy_slots(...)` | 占有マスを埋める。範囲外で `IndexError` |
| `item.clear_current_slots()` | 途中まで埋めたマスの後始末 |

復元位置は必ずしも収まらない（画面ごとにグリッドの寸法が違いうる）。座標を計算し直す
のではなく、はみ出したら `find_placement_position` → `place_new_item` に流すのが安全。
`toggle_twin_inventory_visibility` は Kivy の property dispatch → Clock コールバックの中で
走るので、ここで例外が出るとアプリのループまで抜けてゲームごと落ちる。

### 2.14 アイテム詳細ボックス

ホバーで出る `ItemDetailBox`（window=2560x1387 のときの実測）:

```
ItemDetailBox      size=[333, 500]  size_hint=(None, None)      ← 箱ごと固定
  background       size_hint=(1, 1)
  name_label       height=50   text_size=[316,  50]  pos_hint={'center_x':.5,'top':0.95}
  attributes_label height=225  text_size=[316, 225]  pos_hint={'center_x':.5,'top':0.85}
  desc_label       height=150  text_size=[300, 150]  pos_hint={'center_x':.5,'top':0.40}
                   font_size=27  halign=center  valign=top  max_lines=0
```

- `max_lines=0`（無制限）なので、切れているのは行数制限ではなく `text_size` の高さ。
  300px 幅・`font_size=27` で半角24文字/行、150px で3行 = 72文字までしか描かれない
- 子の位置は `pos_hint` の `top` 分数で、箱の高さに追従する。箱を伸ばせば中身は自分で
  並び直すので、座標を1つずつ計算し直す必要は無い
- 分数を実寸に直すと `上余白25 + 50 + 225 + 150 + 下余白50 = 500`。ラベル間の隙間は 0 で、
  見た目の余白はラベルが自分の高さの中に持っている
- 箱はホバーのたびに作り直される。`update_content` が呼ばれる時点で箱の `pos` は
  入っているが、子はまだ一度もレイアウトされていない（`name_label.pos` は箱の左下原点の
  まま）。レイアウト前の絶対座標から設計を読んではいけない。余白は `pos_hint` の分数と
  箱の高さから求める
- 箱は上端を固定して置かれている（上端が所持品グリッドの上端と一致する）。高さを変える
  ときは上端を保って下へ伸ばす（`y` を据え置くと上へ伸びて位置が浮く）
- 箱は `pos_hint` を持たず、位置は `update_content` の後で誰かが直接入れている
  （写し取った時点で `opacity=0`）。伸ばした箱を画面内に収めたいなら次のフレームで行う

文字が要求する高さの測り方: `text_size` を `(元の幅, None)` にして `texture_update()` を
呼ぶと、折り返した結果が `texture_size[1]` に出る。幅はこちらで決めず、ゲームの値のまま使う。

### 2.15 キャラクタ名はそのままファイルパスになる

```
worlds/<世界>/characters/<キャラクタ名>/
  例: 「試作」のテストA / テスト・ネーム (Test Name)
```

名前に Windows のパスに使えない文字（`< > : " / \ | ? *`）が入ると `os.makedirs` が落ちる。
LLM が生成した名前に引用符が混じる経路が実在する（`試験人形「テストダミー"` のような形）。

- バックグラウンドスレッドで起きるのでゲームは落ちない。画像が生成されないまま無言で
  失敗し、その NPC に関わるたび再発する
- 名前からパスを組む箇所は5つある（`generate_and_write_character_detail` /
  `generate_character_image` / `generate_character_image_from_enemy` /
  `generate_enemy_image_from_character` / `delete_world_character_images`）。個別に消毒すると
  書き込みと削除でずれて別の不整合を生む
- 名前の唯一の入口は `scripts.characters:Character.__init__`（LLM生成・プリセット・
  プレイヤー・セーブからのロードが全部ここを通る）。ここで名前そのものを正せば5箇所は
  手を入れずに一致する。引数ではなく `self.name`（`__init__` を抜けた後）を正すと、
  名前をどの引数から組み立てているかを知らずに済む
- Windows は末尾の空白とピリオドを黙って切るので、パスに使うなら先に落としておく
- 世界名（`worlds/<世界>/`）の入口は未調査。同じ壊れ方をしうる

### 2.16 セーブ

```python
plaintext  = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
cipher[i]  = plaintext[i] ^ b"Instantale_Save_Key_2026"[i % 24]
```

`savedata.json` も同じ方式。セーブを書き換えるツールは、書き込み前に毎回
復号→再暗号化のラウンドトリップを検査し、一致しなければ拒否すること。

### 2.17 経験値・レベル・訓練

経験値はキャラクタが自分で持っている（`scripts.characters:Character`。
`__main__.Character` は同じクラスオブジェクト）:

```
Character.experience_level / experience_point        値（`__init__` の既定は 0 / 0）
Character.gain_exp(exp)                             経験値を足す
Character.check_levelup()                           上がるか
Character.levelup()                                 上げる（HP・能力値の更新まで持つ）
Character.calculate_exp()                           必要経験値
Character.calculate_current_required_exp_on_display()      表示用
Character.calculate_current_gained_exp_on_display(gained)  表示用
```

- `gain_exp` が内部でレベルまで上げるのか、呼び出し元が `check_levelup` →
  `levelup` を回すのかは読めない。両方に耐える書き方（レベルが動いていなければ
  `check_levelup` を聞く）にすること
- 支給の点数を決めているのは `scripts.functions` 側:
  `get_training_experience_point(cleared_quest_difficulty)` /
  `get_days_elapsed_experience_point(experience_level)` /
  `get_enemy_exp_lvl(enemy_tier, quest_difficulty)` /
  `training_efficiency_ratio(alpha, beta, A, base=1.265)`。
  式を再現するより、支給された点数を写すほうが確実（`306_`）
- `app.exp_to_gain` という溜め場が app 側にある（用途は未確定）

訓練・休暇のマネージャ（すべて `__main__`。`process_choice` から `execute` で起きる）:

| クラス | `__init__` | 何か |
|---|---|---|
| `DisplayVacationChoice` | - | 宿屋の休暇の選択肢（`free_facility` の `CALL_PHASE_ALLOWED` に入っている） |
| `VacationTrainManager` | `(app, months, quality)` | 宿屋で月日を訓練に充てる。`quality` は宿の等級 |
| `VacationRestManager` | `(app, months, quality)` | 宿屋での休養 |
| `DisplayTrainingChoice` | `(app, training_type)` | 施設での訓練の選択肢 |
| `TrainingStartManager` | `(app, training_years, training_price)` | 施設の主に年月と代金を払って教わる |
| `TrainingPhaseManager` | `(app, training_type, remaining_years, training_log)` | その各段（`simple_training` / `fundamental_training` / `enhance_skill` / `learn_new_skill`） |

関連: `InstantaleApp.change_background_image_to_inn_room(quality)` /
`app.vacation_hobby_log` / LLM 側は `scripts.llm.llm_manager` の
`vacation_rest_overview_generator` / `fundamental_training_manager` /
`skill_train_manager` / `free_input_training_manager` / `training_conversation_starter`。

「いま訓練の中か」を `frames.MethodWatch` で見るときは、その `execute` を自分で
包んでいないことを確かめる（包んでいると表に入るのはローダのラッパのコード
オブジェクトで、全パッチが共有しているので誤爆する。`306_` が踏んだ）。

---

## 3. 調査手法

純粋関数は総当たりで定義域を割り出す。副作用の無い参照関数なら、稼働中のプロセスで
直接呼んで有効域を特定できる（`get_npc_employ_price` は `0..150` を走査して `0..76` と判明）。
引数の語彙も同じ手で確定できる（実在 id を渡して例外の出ない値を探す＝
`QuestChoiceManager` の `'settlement_quest'`）。再現を待つより速い。

ゲーム自身のヘルパを探すと、値を発明せずに済む。`targets.txt` を `clamp` / `max` /
`validate` / `generate` / `get_` などで検索する価値がある。修正が「ゲーム自身が別の経路で
やっていることを、抜けている経路にも適用するだけ」に収まれば、バランスを勝手に作らずに済む。

ゲーム自身が作った `PhaseSpec` を読む。コンストラクタが実際に受け取っている引数が
そのまま入っているので、語彙を推測しなくてよい。

属性エラーは `__getattr__` トリップワイヤで捕まえる。Python は通常のルックアップが
失敗した後にのみ `__getattr__` を呼ぶので、それはクラッシュの瞬間そのもの。挙動を変えずに
（常に標準メッセージで `AttributeError` を送出）読み手のフレーム・行番号・ローカル変数が取れる。

dict は値ではなくキーとキーの型を記録する（`frames.repr_value`）。`KeyError` の原因究明で効く。

状態は変化だけを高頻度で拾う。20Hz で見て変わったときだけ書けば、量を増やさずに
アニメーションや非同期処理の途中経過が取れる（`206_` の waitstate watcher）。

プロンプト関係は `output_data/` で実データ検証できる。ゲーム自身が LLM へ投げた
`messages` をそのまま保存している（`request_llm_inference_llama_cpp_completion:save_output_log`）。
12,067 件・66 マネージャ種。プロンプトを触る MOD は、ゲームを起動せずに全件へオフラインで
通してから注入できる。

症状の側から判定条件を書く。どの経路が壊しているかを突き止めなくても直せることがある
（鳴っている音を pygame に聞く、`quest_type` の語彙を知らずに済ませる）。

低頻度バグは再現を待たない。正常呼び出しからデータを取る計測を先に設計する。

オフライン検証は偽の app / `PhaseSpec` / マネージャ / Clock で組む。偽の
`on_button_press` は本物と同じく `getattr(__main__, cls_name)(app, *args)` を組んで
`process_choice` に渡すこと（自前ボタンに無害な spec を持たせる意味がそこで確かめられる）。
スタックを見て判定する MOD は、テストも本番と同じ呼び出し元から呼ぶ形にする。

---
