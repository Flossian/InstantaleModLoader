# GAME: Instantale 内部リファレンス

実機で確かめた「ゲームがどう動いているか」をまとめた。
MOD を書くときに必要な、Instantaleそのものの構造・語彙・作法をここに集める。

- ローダの仕組みと MOD の書き方は [TECH.md](TECH.md)
- 遊ぶだけなら [README.md](README.md)（ローダと GUI）と [MODS.md](MODS.md)（同梱 MOD の一覧）
- 各 MOD の検証状況・未確認項目・実測ログは [VERIFICATION.md](VERIFICATION.md)

TECH.md と分けているのは、読む理由が違うから。あちらはこのローダで MOD をどう書くか
（他のゲームにも通じる話）で、こちらは Instantale が何をしているか（このゲーム限定の
事実）。ゲームが更新されて食い違うのはこちら側だけなので、疑う場所が1つになる。

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
| `bug_sites.txt` | crash_log.txt の各クラッシュ地点のプローブ + キーワードスキャン |

### 1.2 ゲーム自身のモジュール

```
__main__                       instantale.py, 516ターゲット
scripts                        scripts.hud.* / scripts.llm.* / items / functions ほか
                               scripts.save_codec, scripts.steam.server_process
Embedding, image_generation, llama_cpp_runtime_completion, sidecar_process
save_area_json, save_world_json, api_key_manager, build_type, sdcpp_cuda
```

`__main__` は `sys.stdlib_module_names` に含まれる。素朴に stdlib を除外すると
ゲーム本体が丸ごと対象から抜け落ちる（`recon.py` の `GAME_TOPLEVEL` はアローリスト）。

### 1.3 スキャンで見つからないもの

- ネスト関数はモジュールのグローバルに現れない。`send_request_on_id` はトレース
  バックに 62 回出るが `vars(module)` には無い（`send_request` 内の `backoff` デコレータ
  付きネスト関数）。実際の対象は外側の `send_request` / `send_request_with_no_structure`
- クラスのメソッドはモジュールレベルのキーワードスキャンで 0 件に見える
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
| クラッシュログ | `<ゲームdir>\crash_log.txt`。更新で消えることがある（§1.5） |
| LLM 入出力の記録 | `<ゲームdir>\output_data\<世界>\<PC>\<manager>\N.json` |

ゲーム内部のバージョンは実行時に問い合わせること（Epic のマニフェストとは無関係）。

### 1.5 更新の記録

#### main_023 → main_024（2026-08-05 に更新、同日リコン）

`game_version` は `014` のまま。 Epic の `AppVersion` だけが上がった（§1.4 の
「別系統」がそのまま出た例）。`python310.dll` は 6/03 のままで注入基盤は無傷。

満額は 216 patches / 130 target / 43 mod で main_023 と同一。失った対象は無い。
公式アナウンスの「起動処理を変更した」は注入にも効いていて、満額到達が
80 秒 → 41 秒に縮んだ（起動 16:44:12 → 満額 16:44:53）。

アナウンスに無い追加要素（更新前のリコンを退避してあったので機械的に出せた）:

| | |
|---|---|
| ハイアンドロー | `DisplayHighLowChoice` / `HighLowStartManager` / `HighLowJudgeManager` / `HighLowCashOutManager` / `HighLowEndManager` / `HighLowRuleExplainManager` / `HighLowLatchChoice` |
| ロシアンルーレット | `DisplayRussianRouletteChoice` / `RussianRouletteDealerTurnManager` / `RussianRouletteCancelManager` ほか、`InstantaleApp.russian_roulette_dealer_statement` / `_get_owner` |

計 68 ターゲット増。どちらも賭博で、`process_choice` を通る新しいマネージャ
なので §2.5 の画面判定に関わる（`FreeFacilityManager` と同じ扱いが要る）。

> **リコンの差分は「取った時点」を揃えないと嘘になる。** 同じ突き合わせで 85 件が
> 「消えた」ように見えたが、実体はまだ import されていないだけだった
> （`image_generation` / `sdcpp_cuda` / `scripts.image_processing` が丸ごと）。
> 退避した main_023 は長時間プレイ後（モジュール 4235・ゲーム 72）、main_024 は
> 起動 41 秒後（3452・55）。増えた側だけが信用できる。減った側を読むなら、
> 同じくらい遊んだ状態で取り直すこと。

#### main_024 で本体に取り込まれた MOD の修正

アナウンスが 6 件を「Reported by ModLoader」として挙げている。
MOD が手を出したかどうかは、その MOD 自身の印で判定する（症状が出ないだけでは、
本体が直したのか MOD が直したのか区別が付かない）:

| MOD | 対応するアナウンス | 印 | 判定 |
|---|---|---|---|
| `107_fix_battle_flag_stuck` | 自由入力の戦闘後に戦闘状態が残る | `[FLAGFIX]` 0 件 | 本体が直した |
| `106_fix_battle_bgm_restore` | 戦闘後にBGMが止まらない／曲が重なる | `[BGMFIX]` 0 件 | 本体が直した（下記の但し書きあり） |
| `101_fix_npc_employ_price` | 高レベルNPCの雇用でクラッシュ | 上流プローブ＋リコン差分 | 本体が直した |
| `110_fix_character_name_path` | 禁則文字を含む名前で画像が生成されない | `[NAMEFIX]` 実ゲームで 0 件 | 本体が直した（§2.15） |
| `103_fix_eventlog_trim` | クエストログが際限なく溜まる | `[EVENTLOG]` 0 件（イベント5回） | 本体が直した |
| `108_` | 売買画面 | なし | 未検証（能動的に起こせない） |

`107_` の根拠（2026-08-05 16:54、会話から戦闘）:

```
BattleEndInFreeAction.end_phase(...) start   in_battle=1
BattleEndInFreeAction.end_phase done         in_battle=0  app.music = 'None'
```

`clear_stale` は立っているフラグだけに触って `[FLAGFIX]` を書く。それが 0 件で
`in_battle` が落ちている＝ゲーム自身が下ろした。§2.10 の「`BattleEndInFreeAction`
は下ろし忘れる」は main_024 で解消。

> **`106_` の但し書き**: `battle_bgm.log` は注入のたびに世代交代するので、この
> ファイルには更新前の記録が残っていない（16:44 以降のみ）。「以前は出ていた印が
> 出なくなった」という形の比較はできておらず、根拠は「症状が出ていない」＋
> 「MOD が手を出していない」の 2 点。

> **`101_` は「雇用した」だけでは確かめられない。** clamp が 0 件ということは、
> 雇ったNPCの難易度が有効域 0..76 に収まっていた＝壊れる側の入力を通していない。
> しかも 76 を超える難易度のNPCは、出現を待つしかなく能動的に用意できない。
> なお `NPC_DIFFICULTY_VALUE_MIN=0 MAX=76` は main_024 でも同じで、
> テーブル自体は広がっていない（本体は呼び出し側で抑えたと見られる）。
>
> `103_` は「イベントを何回も通す」ことでしか判定できない。 1本のクエストを
> クリアしただけでは足りない（2026-08-05 18:43〜18:48 のクエストでは発火せず、
> フィールドイベントが 1 回だけだったため出番が来ていなかった）。刈り込みの条件は
> `KEEP_TURNS = 3` 超えで、フィールドイベントは仕様上 3 ターンで決着する ―
> つまり 1 イベント単位では原理的に届かない。旧バグはイベントを跨いでログが
> 残ることで 29 ターン・8,300 文字まで育つものだったので、複数回通して初めて
> 差が出る。19:02〜19:12 にイベント 5 回ぶんを通して 0 件だったことが根拠:
>
> ```
> 19:03:08 3771 → 19:03:18 3961 → 19:03:48 5271   ← 3件で1組（対象の3関数に対応）
> 19:05:04 4085 → 19:05:16 4273 → 19:06:00 5561
> ...  以降も同じ形で 5 組
> ```
>
> 組ごとに ~300 文字ずつ増えているのは `quest_log`（クエスト全体の記録）が
> 伸びているぶんで、こちらは正常。`quest_event_log` の側は 3 ターンを超えていない。

> **`110_` は「無害だから残す」が通らない唯一の一本。** `102_` や `108_` は対象が
> 無ければ何もしない検出器なので置いておけばよいが、`110_` は発火すると
> 名前そのものを書き換え、それがセーブに残る（README にもそう書いてある）。
> 本体が名前を触らずに直しているなら、残す限りこちらだけが余計に改変する側に回る。
> ただし main_024 では実ゲームで一度も発火していないので、急いで外す理由も無い。
> 外すかどうかは、上の「保存名を確認できていない」が片付いてからでよい。

> そこで `101_` に上流プローブを足した（§3）。包む前の素の関数を控えておき、
> 注入のたびに `0..200` を総当たりする。2026-08-05 の結果:
>
> ```
> upstream: get_npc_employ_price(0..200) no longer raises (unwrapped=2)
> ```
>
> リコンの差分が独立に裏付けている。 main_024 の `scripts.functions` には
> `clamp_character_level` / `clamp_quest_difficulty_value` / `clamp_quest_scaling_value`
> が新規に増えている（`sanitize_path_name` も同じ並び）。`clamp_npc_difficulty_value`
> 自体は main_023 から在った ― つまりヘルパはあったのに雇用価格の経路だけ通して
> いなかったわけで、`101_` が塞いだのと同じ穴を本体が同じ方法で塞いだことになる。
>
> `101_` は残してよい。 クランプは冪等なので、本体が先に抑えていれば何もしない。
> `110_` と違って名前のような残るものを書き換えないので、置いておけば
> `upstream:` 行が毎回の注入で上流の状態を教える検出器になる。

> **上流プローブは「何を測ったか」を自分で検算させること。** 最初の版は
> `functions.get_npc_employ_price` をそのまま控えていたため、再注入では前回の
> ラッパを測っていた（層を剥がすのは `ctx.wrap` の中＝この控えより後。
> `patch.py:unwrap_ours`）。clamp 済みの関数はどの入力でも落ちないので、本体が
> 直っていなくても「もう要らない」と出る ― 実際に一度そう誤判定した。いまは
> `__original__` を最下層までたどり、剥がした層数（`unwrapped=`）と、底に
> ローダの印（`__instantale_patch__` / `__wrapper_of__`）が残っていないかを
> 併せて記録し、残っていれば測定結果を捨てる。

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
| クラウドLLMのサブスクリプション | `hud_auto_configuration:AutoConfigurationScreen.use_cloud_subscription_process` / `hud_option:OptionAIScreen.show_subscription_screen` / `OptionLLMScreen.get_cloud_llm_list(cloud_billing_type=None)` / `check_device_info:get_setting_for_cloud_llm`。**サブスクはゲーム側が未実装**（2026-08-08 時点。画面と関数はあるが生きているのは APIキー方式だけ。クラウド経路の検証は APIキーで行えば足りる） |
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

`crash_log.txt` は main_023 への更新で消えた。同梱されていた 114 件（`100_` や
`204_` が件数を根拠に挙げているもの）は現物が無くなっている。
`out/crashlog_baseline.txt` の 173055 バイトも、もう対応する相手がいない。
あの 114 件は `013` の記録。件数を根拠にする議論は、この境目を跨がせないこと。

その後 main_024 で `crash_log.txt` は復活した（2026-08-05 時点で 1986 バイト）。
中身はこの環境で新しく積まれた 2 件だけで、`game_version` と `config_version` が
付く形式になっている:

```
MAIN CRASH: 2026-07-31T01:16:02 / 2026-08-04T20:11:36
game_version: 014   config_version: 0.6
kivy/input/providers/wm_common.py:115 _closure
ctypes.ArgumentError: argument 3: TypeError: wrong type
```

2 件とも `100_fix_kivy_shutdown` が対象にしている終了時クラッシュで、
main_024 のアナウンスには入っていない＝直っていない。どちらも注入前のセッションの
もの（8/4 のものは 20:11、その日の注入は 20:30）なので、素のゲームで起動した回の
記録であり、`100_` が破られたわけではない。

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
- クラウドLLM経路は素通りが確定し、`111_` は v4 で対処済み（§1.8・§2.12）

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

- クラウドLLM経路はプロンプト系 MOD が**素通りになることが確定した**
  （2026-08-08・APIキー利用者から「`111_` の置換が効かない」との報告。同日、
  無料 Gemini の実機で経路を実測）。経路はプロバイダごとの
  `request_llm_inference_*`（Gemini は `_gemini_test_streaming`）で、
  `LlamaCppClient` を通らない（§2.12）。`111_` は v3 で gemini / any_server の
  境界 `send_request*` を包み、Gemini 実機で `[REPLACE]` の発火を確認。v4 で
  プロバイダに依存しない `llm_manager` の別名包みに置き換えた（OpenAI / Claude /
  Alibaba / 任意互換サーバーもモジュール名を知らないまま届く。§2.12 のパターン。
  v4 も Gemini 実機で発火確認済み・同日）。境界で見えるのは
  呼び出し側が渡した `message` だけで、**send_request の中で足される部分
  （Gemini なら `_schema_instruction` のスキーマ文）には当たらない**。
  `102_` / `103_` / `105_` はクラウドでは素通りだが、**実害があるのは `103_` だけ**:
  `102_` の対象バグはゲーム本体（main_023）で発生源から修正済み（§1.6。公式文言も
  「ローカルLLM使用時」で、Gemini の再生成は壊れた部分木だけ修復する別実装なので
  重複の仕組み自体が無いとみられる）。`105_` が削るスキーマ repr の埋め込みは
  ローカル送信モジュール側の行いで、Gemini は `_schema_instruction` という別実装
  （§2.12）＝対象がクラウドの本文に無いとみられる。`103_` の対象
  （`quest_event_log` の肥大）はマネージャ層で起きるのでクラウドでも育つ＝素通りの
  実害あり。`305_` は半分だけ素通り（プロンプトの書き換えは `LlamaCppClient.chat`
  なので効かず、ミニクエストが普通の討伐依頼として生成される。撤退→達成の
  差し替えは `llm_manager:quest_referee*` なので効く）。`301_` は `LlamaCppClient`
  を使っておらず（差し込みは `llm_manager_world_generate:random_quest_generator` の
  引数へ）、クラウドでも効く。ローカル実行では従来どおり効いている（§1.6）
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

セーブに焼かれるのは `text` と `spec` だけ。独自キー（印）は落ちる。
実セーブ 8 件で確認（2026-08-02）:

```
savedata.json に '依頼を受ける' は入っている / 'mod_action' は入っていない
```

したがってタイトルへ戻る・ロード・再注入のあと、印の無い自分のボタンが復元されている。
印で重複を判定していると自分のものと見なせず、同じボタンが2つ並ぶ。しかも復元された方は
spec が `JustSetButtonToNormalPhase` なので押しても無反応 ―
見た目は同じなのに片方だけ効かないという、最も分かりにくい壊れ方になる。

差し込む前に `ui.Screen.prune_stale(buttons, ラベル一覧)` を通すこと。
「自分のラベル（前方一致）」かつ「どの MOD の印も無い」かつ「無害 spec」の3つが
揃ったものだけを落とすので、ゲーム側の同名ボタンは巻き込まない。落としてから差し直す
ため、残骸は消えるのではなく生き返る。

「自分の印が無い」では足りない（2026-08-03）。`refresh_choice_buttons` を包む
MOD の掃除は画面が何であれ走るので、他の MOD が今その場に出しているボタンも
「自分の印が無い」に見える。`302_`（`やめておく`）と `309_`（同じ文字列）が衝突し、
役場の罰金の確認画面からキャンセルが最初から消えていた。復元された残骸は印を
1つも持たないので、判定は「`mod_` で始まるキーが1つも無い」で行う
（`ui.MARK_PREFIX` / `ui.Screen.marked_by_a_mod`）。掃除に使うラベルも、
その MOD にしか無い文言だけにすること ― ゲーム自身が同じ文言のボタンを出して
いた場合、印では見分けようがない。

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

仲間欄（パーティ表示）も同じ構図。 `app.party` を書き換えただけでは変わらない。
塗るのはゲーム自身の次の2つで、どちらも引数を作らずに呼べる:

```
InstantaleApp.update_party_member(self, dt)      Clock コールバックの形。dt は 0 でよい
InstanTaleHUD.update_party_display(self, *args)  HUD 側
```

`ui.Screen.paint_party(app)` がこの2手を通す。パーティを増減させた MOD は最後にこれを
呼ぶこと（`302_` は別れの文が流れ終わってから呼んでいる ― 文より先に消えると、
まだ別れていないうちに居なくなったように見えるため）。

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

本文の打ち出し（タイプライタ）は `InstantaleApp.add_text_display(self, dt, context, index=-1)`
（`out/text_speed.log` の実測、2026-08-03。`211_probe_text_speed`）:

| | 実測 |
|---|---|
| 1ティックで進む文字数 | 1文字（平均 1.03〜1.11。まれに 2〜3）|
| ティックの間隔 | `app.text_speed` 秒。設定を変えると即座に変わる（再起動不要）|
| `text_speed=0.04` | 間隔 48〜50ms ＝ 20 文字/秒 |
| `text_speed=0.08` | 間隔 80〜83ms ＝ 12 文字/秒 |
| 言語 `ja` の既定 | 0.07（`scripts.functions:get_default_text_speed_for_language`）|

間隔はフレーム境界に丸められる。 60fps ＝ 16.7ms 刻みなので、0.04 は 3 フレーム
（50.0ms）、0.08 は 5 フレーム（83.3ms）に乗る ― 実測の 49.4ms / 83.3ms と一致する。
つまりどれだけ小さい `text_speed` を入れても最速は「1フレーム1文字」＝ 60 文字/秒
で、そこが下限になる。

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

移動が終わった瞬間は `__main__:MovePhaseManager.move_phase` の復帰後。
情景描写（`llm_manager:narrator`）は `move_phase` の内側で呼ばれる。

```
process_choice(MovePhaseManager, ...)    ボタン押下
  move_phase()
    narrator(...)                        ← 情景描写はここ（内側）
  move_phase 復帰                        ← ここで到着が確定している
```

したがって「復帰後に印を置いて次の `narrator` で回収する」形にすると1手ずれる
（回収するのは次の移動の `narrator`）。情景描写に合流したいなら、印は `orig` を
呼ぶ前に置いて入れ子の `narrator` に拾わせる（`300_event_facility_arrival`）。

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
- `in_shopping` などの旗はセーブに焼かれて下りないことがある。実機で、
  セーブをロードした後にギルドへ入っても `app.in_shopping` が `True` のまま
  だった（2026-08-05）。画面は施設の選択肢そのもの（`MovePhaseManager` と
  `DisplayTalkChoice` が並ぶ）なのに旗だけが古い。

  > `107_fix_battle_flag_stuck` が直した「戦闘中の印が下りない」と同じ形。
  > **旗は「別の画面が出ている」ことの当て推量**でしかないので、
  > `is_facility_screen` のように画面そのものを見る手段があるなら、
  > 画面のほうを信じること。旗だけで出し分けると、一度立ちっぱなしに
  > なった世界では機能が二度と現れない。

- `player.location` も同じ。セーブでは施設 id の文字列（`'106'`）で、遊んでいる
  最中にその施設へ入ると `Facility` に置き換わる。ロード直後は文字列のままなので、
  `facility_type_of` に直接渡すと空文字が返る

  > **「新しい世界では動くのに、セーブをロードすると動かない」の正体はこれ。**
  > 施設の種類で出し分けるボタンが、ロード直後だけ出ない ― どこかへ移動して
  > 入り直すと直るので、原因が掴みにくい（2026-08-05 に実機で踏んだ）。
  > `309_` は先に踏んで両対応にしていたが、その知見が横に伝わっていなかった。
  > 施設を引くときは `player.location` を直接使わず、id でも引き当てる関数を通すこと。
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
   `player.party`、加えて名前に `party` が入る属性・キーのスキャン（`escaped_member_in_battle`
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

#### 画面のパーティ欄は3枠で固定（`instantale.exe` の定数表、main_023）

名簿に何人入っていても、画面に出せるのは3人まで。HUD が枠を3つしか作らない。

```
bottom_info_layout          size_hint=(0.2, 0.88) pos_hint={center_x:0.88, y:0.1}
  party_cells               range(0, 3)  ← ここが 3 で固定
    ClickableFloatLayout    size_hint=(1, 0.33) / member_id を持つ
      StencilFloatLayout
        Image               立ち絵
InstanTaleHUD.update_party_display(*args)   party_members（DictProperty）の変化で走る
InstanTaleHUD.set_party_member_callback(member_index, callback)
InstantaleApp.on_member_label_press(label_index)  -> party_cells[label_index].member_id
                                                  -> process_party_member_choice(id)
```

- 4人目以降が出ないのは枠が足りないからで、`party_members` は欠けていない
  （会話も戦闘も4人目を含めて進む）。枠を足せば出る（`116_ui_party_expand`）
- 押したときの相手は `party_cells` の添字で決まる。枠を足すならこの並びに
  追記する。並べ替えると、押した相手と話す相手が食い違う
- 枠の中身（立ち絵の `source` がどのキーから来るか）は読めない。埋まっている枠と
  `party_members` のその相手を突き合わせて、一致した場所を対応とみなすこと
- 枠を複製しても枠線は付いてこない（canvas の描画命令は写らない）。線を自分で
  引くより `scripts.hud.new_hud` の `add_border(widget)` / `add_border_before(widget)`
  を呼ぶ。色も太さも寸法追従（`update_border`）もゲームのものになる
- 枠の `size_hint_y` は `0.33` であって 1/3 ではない。 行数で割り直した比率
  （1/4 = 0.25 など）を伸ばした帯に当てると、その差で元から在る枠が数 px 動く。
  並べ直すときは比率ではなく実測の座標で置くこと（実機で確認、2026-08-03）
- `update_party_display` はパーティ欄（`bottom_info_layout`）の子を1つずつ
  「枠」として塗る（クラッシュ時の locals: `cells` len=6 / `member_ids` len=6 /
  `i` / `cell`）。枠の中身を直に触るので、枠でないものをこの入れ物に置くと
  その瞬間に落ちる（`IndexError: list index out of range`。`116_` が黒い板を
  帯の子にして踏んだ。2026-08-03）。枠を足すのは良いが、それ以外は入れないこと
- 選択肢ボタンの `disabled` を控えて書き戻さないこと。 ゲームは応答待ちの間
  だけ選択肢を無効にする（`is_button_enabled` / `set_buttons_to_normal`）。その
  瞬間の値を控えて後で書き戻すと、ゲームが有効に戻した後でも無効に落ちて
  選択肢が押せなくなる（`116_` が実機で踏んだ。2026-08-03）。他人が
  管理している状態は、控えた瞬間と書き戻す瞬間の間に持ち主が変えている

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
- **動かした NPC は自主的には持ち場へ戻らない。** `move_npc_to_facility` で移した
  相手は、移した先に居続ける。ゲーム側に「元の場所へ帰る」動きは無い（過去のプレイで
  確認。生ログは残っていないが、判断の前提に使えるだけの確度はある）。

  だから **NPC を動かす MOD は、戻す責任も持つ。** 動かす前の `location` と
  `current_node` を控え、役目が終わったら帰す。控えずに動かすと、その移動は
  世界のどこからも取り消せなくなる ― MOD を無効化しても、フォルダごと消しても、
  NPC はそこに残る。パーティ由来の移送（`302_` / `303_` / `304_`）が問題に
  ならないのは、**動かす主体がゲームで、MOD は置き先を差し替えているだけ**だから。
  ゲームが動かすつもりの無い NPC を MOD の都合で動かすのは、これとは別の話になる。

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
  → buttons ['帰還する', '漁る']
  → LootPhaseManager(app)     '漁る'      戦利品。**完了より前**
  → QuestEndManager(app)      '帰還する'  ★完了。**引数ゼロ**
```

`帰還する` と `漁る` は完了の後に出るのではなく、`QuestEndManager` を起こす側
（2026-08-01 の実測。`process_choice(QuestEndManager, choice_text='帰還する')` の
14分前に `process_choice(LootPhaseManager, choice_text='漁る')` が来ている）。

`QuestEndManager.execute` の中で帰還・報酬・才能まで済み、抜けた先はエリアの入口
（`facility_type='entrance'`）。入口の選択肢は隣の施設への `MovePhaseManager` だけで、
`DisplayTalkChoice` も `DisplayAreaMoveChoice` も無い。「町に戻ったか」を後者2つで
判定すると、プレイヤーが歩き出すまで拾えない（`307_` が実際にこれを踏んだ）。

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

#### フィールドイベントの成否判定（`credibility` と `<確率N%>`）

道中のミニイベントで自由入力した行動が成功するかは、この順で決まる。

```
QuestEventManager(app, event_name, enemies_info, event_turn)
  → llm_manager:quest_referee_event_evaluate_new(...)      = field_event_evaluator
        result_type: certain_success / certain_failure / roll_required
        roll_required なら narration・credibility(1-10)・reference_attribute(6能力値)
  → ゲームが確率に変え、quest_event_log の末尾に `<確率N%: 成功>` を書き足す
  → llm_manager:quest_referee_event_resolve(..., outcome, ...)  結末と効果
```

`resolve` が受け取るのは成功・失敗の結果だけで、確率も能力値も渡らない。
**サイコロを振っているのは `QuestEventManager` の中**（コンパイル済みで読めない）。

実測は `output_data/` の LLM 記録と、`saves/*/backups/` に残る `quest_event_log`
の突き合わせ（2026-06-06〜08-08、8キャラ、判定 121 回）。

| `credibility` | `*10+20` | 回数 | この値ちょうどだった回数 |
|---|---|---|---|
| 2 | 40% | 2 | 1 |
| 3 | 50% | 13 | 5 |
| 4 | 60% | 33 | 14 |
| 5 | 70% | 21 | 10 |
| 6 | 80% | 32 | 20 |
| 7 | 90% | 19 | 8 |
| 8 | 100% | 1 | 0 |

- **確率が `credibility*10 + 20` を超えたことは一度も無い**（121 回・上振れ 0 回）。
  半数（58回）はちょうどこの値で、残りは -2 〜 -40 の負の差だけが付く。
  つまりこの式は上限で、差は必ず引き算の側にしか現れない
- 実際の成否は宣言どおりの確率で出ている（上限90%の19回で15勝、上限60%の33回で
  16勝）。**判定そのものは正直**で、体感の低さは確率の作り方の側にある
- 全体の成功率は 59.5%（72勝49敗）
- `reference_attribute` は 6 能力値から毎回選ばれているが（`dexterity` 80 /
  `intelligence` 21 / `wisdom` 10 / `constitution` 8 / `strength` 2）、
  **その能力値が高いことで確率が上がった回は無い**。能力値に振った差は結果に出ない
- 評価の内訳は `roll_required` 121 / `certain_success` 10 / `certain_failure` 1。
  **入力の 92% はサイコロに回る。**行動の内容で確定するのは 8% しかない
- `credibility` は 4〜6 に 86/121 が集まる。プロンプトが
  「手心を加えないこと。これは無理だろうと思えば迷わず1」と念を押している側で、
  10 は一度も出ていない

差（-2 〜 -40）が何に連動しているかは**未特定**。次は外れている:

- 参照能力値の種類ではない。同じ時刻の別判定で、`dexterity` と `intelligence`
  に同じ差が付いた回がある
- 固定値でもない。同じキャラ・同じ能力値で、日をまたぐと差が動く
- HP でも体力（`physical_integrity`）でもない。同じクエストの中で
  -11 → 0 → -4 と往復した回がある（`エリス`、2026-08-01 01:31〜01:45）
- レベルとは緩く相関する（レベルが上がるほど差 0 が増える）が、
  例外が多く式にならない

クエストの**外**の自由入力は別系統で、確率を LLM 自身が出す（§2.26）。

判定の瞬間の値はセーブにも LLM の記録にも残らないので、**実機で立ち会うしかない**。
`215_probe_event_roll` がその計測（`Character.calculate_attribute` が判定の窓の間に
呼ばれるかを、呼び出し元ごと控える）。

プロンプトの側も見ておくこと ― `field_event_evaluator` に渡る
【プレイヤーのパーティ】にはプロフィール・人格・特質・装備しか入っておらず、
**能力値は1つも書かれていない**。LLM は能力値を知らないまま
`reference_attribute` を選んでいる。

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

#### 1手ぶんの内訳（`BattlePhaseManager`。署名は実測、順序は未実測）

`out/recon/targets.txt` にある `BattlePhaseManager` のメソッド:

```
battle(command, choice_text)
handle_battle_situation(character_key, character_side, battle_action)   1手ぶん
  calculate_battle_effect(battle_action)                               効果を決める
  resolve_battle_effect(character_key, character_side, battle_action,
                        effect_to_enemies, *args)                      当てる
  process_battle_text(character_key, character_side, battle_action,
                      effect_to_enemies, death_player, death_member_list,
                      escape_succeeded_member_list, escape_failed_member_list,
                      death_enemy_list, *args)                         地の文
reduce_status_turns_and_log(character)      毒などの継続分
check_character_death(index, character) / check_team_annihilation() / check_battle_end()
enemy_delete_animation(index, character)
convert_llm_output_to_instruction_dict(actor, skill, referee_response)
```

実測（2026-08-01。`308_` のログ。VERIFICATION.md §3.14）で分かっていること:

- 1手 = `handle_battle_situation` 1回。 味方の手も敵の手もここを通る
- `character_side` は日本語の文字列（`'味方陣営'` / `'敵側'`）。列挙値ではないので
  文字列で分岐しないこと
- `character_key` は敵だと `'泥濘の亡者1'` のように連番付き。`Character.name` の側は
  連番が付かない（`'泥濘の亡者'`）ので、鍵と表示名は別物
- 1手で複数の敵に当たる手がある（スキル）
- 倒れた敵は1手の中で `current_enemy_dict` から抜ける。 手が終わった後に見ると
  もう居ない（`enemy_delete_animation` / `check_character_death` がその担当と読める）。
  1手の前後で敵の状態を比べる MOD は、この抜けた敵を別に拾わないと取りこぼす
  （`308_` が実機1回目でとどめの一撃を落とした原因）

> 入れ子の順序（`calculate` → `resolve` → `process`）は署名から読んだだけで
> 実測していない。 引数名（`effect_to_enemies` が `resolve` と `process` の
> 両方に居る）からそう読めるだけ。`308_battle_damage_display` はこの順序に
> 寄りかからない形（1手の外側で HP の差を測る）にしてある。

ダメージの計算そのものは `scripts.functions` 側:

```
get_base_damage_value(character_attack, weapon_attack)
get_instant_damage(attack, defense)
```

引数は数値だけで、誰に当たった値なのかは引数から分からない（1手で何回呼ばれるかも
不明）。数字が欲しいだけなら、ここを包むより HP の前後を比べるほうが確実。

戦闘中の HP の在り処:

| 誰 | どこ |
|---|---|
| 敵 | `app.current_enemy_dict`（鍵 → その敵。戦闘の実体の有無もここで見る） |
| プレイヤー | `app.player` |
| 同行者 | 名簿の id から `world.characters`（§2.6 のパーティ名簿） |

`Character` 側の項目は `current_hp` / `physical_integrity` /
`max_physical_integrity`（`__init__` の署名。実測）。最大 HP は
`update_max_hp()` があることから `max_hp` と推測しているだけで未実測。

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
scripts.llm.request_llm_inference_llama_cpp_completion:send_request*         リクエスト（ローカル）
scripts.llm.request_llm_inference_gemini_test_streaming:send_request*        リクエスト（Gemini・実測）
scripts.llm.request_llm_inference_openai:send_request*                       リクエスト（OpenAI・実測）
scripts.llm.request_llm_inference_claude:send_request*                       リクエスト（Claude・実測）
scripts.llm.request_llm_inference_any_server:send_request*                   リクエスト（任意 OpenAI 互換サーバー用と推定・未実測）
scripts.llm.llm_manager:*                                                    マネージャ群
```

- **外部APIキー（クラウド）はプロバイダごとの `request_llm_inference_*` を通り、
  `LlamaCppClient` を一切通らない**。使われるモジュールは1つだけが import され、
  残りは `sys.modules` に載らない（Gemini セッションの実測・2026-08-08: gemini だけが
  載り、llama_cpp_completion も any_server も `[not loaded]`）。UI の選択肢は
  Gemini / OpenAI API / Claude API / Alibaba Cloud API / 任意 OpenAI 互換サーバー。
  実測済みは Gemini（`_gemini_test_streaming`）・OpenAI（`_openai`）・
  Claude（`_claude`）。Alibaba は未実測、any_server が任意互換サーバー用と
  推定される。`LlamaCppClient` に仕掛けたプロンプト系 MOD はクラウドでは
  素通りになる（`111_` で実際に報告があった）
- OpenAI モジュールの内部（実測・2026-08-08）: 境界は Gemini と同形
  （`send_request(manager_name, message, structure, model=None, max_tokens=30000,
  timeout=None)`）。`openai.OpenAI` クライアント直で、`default_model =
  'gpt-5.4-nano'`。Gemini に在ったストリーミング収集・JSON修復ループ・
  `SCHEMA_IN_PROMPT` は**無い**（構造化出力を API に任せる作りとみられる＝
  スキーマ文のプロンプト埋め込みも無いとみられる）。`save_output_log` /
  `calculate_price` / `total_cost` は Gemini と同名で持つ
- Claude モジュールの内部（実測・2026-08-08）: OpenAI 版と同構成。
  `anthropic.Anthropic` クライアント直で、`default_model = 'claude-sonnet-5'`。
  境界のシグネチャも同じ。ストリーミング収集・修復ループ・スキーマ埋め込みは無い
- **プロバイダに依存しない仕掛け方（`111_` v4 のパターン）**: 送信モジュールを
  名指しせず、`scripts.llm.llm_manager:send_request` /
  `:send_request_with_no_structure` を包む。この2つは**使われる送信モジュールから
  from-import した別名**（Gemini セッションの recon で `__module__` が gemini 側を
  指すことを確認）なので、alias_scan（既定有効）が同じ関数を持つ全モジュール
  （送信モジュール本体・`llm_manager_battle` などの別名）を張り替え、どの
  プロバイダでも1箇所で効く。プロバイダ名はラップした元関数の `__module__` から
  採れる。**ただしローカル実行では、この地点で本文を触ってはならない**。
  `send_request` は内部で別スレッド（`send_request_on_id`）に降りてから
  `LlamaCppClient` を呼ぶため、スレッド頼みの一回制御が効かず、`chat` 側の
  フックと二重適用になる（`111_` は llama.cpp の送信モジュールが import されて
  いるかで見分けて、ローカル時は素通ししている）。**もう1つの罠: この別名は
  プロバイダの初期化時に生える。** 起動直後に注入すると `llm_manager` は
  import 済みなのに `send_request` が**まだ無い**（Claude 選択の実機で観測・
  2026-08-08。同じ apply の中で `llm_manager:conversation_starter` の wrap は
  成立していたので、モジュールの有無ではなく属性の有無）。ローダの保留機構は
  **モジュール単位**で、属性の後生えは当て直さない。`111_` v5 は無かった別名を
  5秒ごとに見張って、生えた時点で包む（1時間で諦める・注入し直されたら
  `ctx.superseded()` で降りる）
- Gemini モジュールの内部（APIキー環境の recon dump・2026-08-08）:
  `send_request(manager_name, message, structure, model=None, max_tokens=30000,
  timeout=None)` / `send_request_with_no_structure(..., timeout=30)`。
  `google.genai` の `Client` を直接使い、ストリーミングで受けて
  （`_stream_and_collect`）、pydantic 検証に失敗した部分木だけを LLM に修復させる
  ループを持つ（`_validate_with_repair` / `_regenerate_subtree`、最大3周・
  修復用の system 文は `_REPAIR_SYSTEM_LINES`）。`SCHEMA_IN_PROMPT = True` で
  スキーマ文（`_schema_instruction`）を **send_request の中で**足すので、境界の
  `message` には入っていない。system 文は `_build_config(system_text, ...)` 経由
  （Gemini の system_instruction）。`save_output_log` はローカル版と同名・同位置。
  `default_model = 'gemini-3.5-flash'`、`calculate_price` / `total_cost` で
  料金も追っている。他プロバイダを実測したら同様に recon dump を取ること

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
- `ctx.mod_dir` は `apply()` の中で控える。外では `None` になる（ローダの註の
  とおり）。押されてから読もうとして `None` を掴み、同梱データが1つも見つからない
  まま受け皿へ落ちた（実機・2026-08-04）。そのとき初めて受け皿の道を通るので、
  普段は隠れている不具合が同時に表へ出る
- `message` は必ずリストで渡す。引数名は単数形（`send_request(manager_name,
  message, structure, ...)`）だが、中では `自前のリスト + message` をしている。
  素の文字列を渡すと `send_request_on_id` で
  `TypeError: can only concatenate list (not "str") to list` になる
  （実機で踏んだ・2026-08-04）。形は `[{"role": "user", "content": ...}]`
  > **この例外は呼んだ側に飛んでこない。**ゲームが内部で立てた別スレッド
  > （`Thread-86 (send_request_on_id)`）で起きるので、呼び出しは永久に
  > 返らないという形でしか現れない。`send_request` を呼ぶ MOD は
  > 「返ってこない場合」を必ず自分で面倒を見ること。
  > 見つけられたのは `001_crash_recorder` がスレッドの例外を拾っていたから
  > （`out/live_crashes.log`）。LLM を呼ぶ MOD の不調は、まずそこを見る。
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
| `field_event_evaluator` | イベント中の入力を「確定」か「確率判定」に振り分け、説得力を1〜10で採点（判定の実体は §2.9 の「フィールドイベントの成否判定」） | 無 |
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

### 2.13.1 店の品揃え（`ShoppingStartManagerRemake`）

店に並ぶのは**その施設の主の持ち物そのもの**。売買画面は主とプレイヤーの
2つの持ち物を左右に並べているだけで、店専用の在庫という入れ物は無い。

```
__main__:ShoppingStartManagerRemake.execute(self, choice_text)
                                    .shopping_start_method_1(self)
                                    .set_item_from_world_data(self, shop_owner_instance, next_tier)
                                    .generate_item_in_shopping(self, item_data, shop_owner_instance,
                                                               item_stock_tier)
__main__:InstantaleApp.toggle_twin_inventory_window(left_inventory_obtainer,
                       right_inventory_obtainer, left_label_text, situation, *args)
__main__:InstantaleApp.buy_item(item_instance) / sell_item(item_instance)
__main__:InstantaleApp.set_shop_price_for_owner(item_instance) / set_shop_price_for_player(...)
__main__:InstantaleApp.normalize_shop_inventory_prices(shop_obtainer, player_obtainer)
```

- 主の持ち物はセーブの `npcs[<id>].inventory`（`{item_id: アイテム}`。§2.23 の21番）。
  実セーブでは51人中8人だけが中身を持っていた（2026-08-06 に復号して確認）。
  中身を持っているのは店として開いた施設の主だけで、**開いていない店は空**
- つまり品揃えは「初めて開いたときに作られて、そのまま残る」。**入れ替える
  仕組みは見当たらない**。プレイヤーが売った品も `sell_item` で主の持ち物に
  積まれるので、24マス（§2.13）が埋まると売却そのものができなくなる
- `next_tier` / `item_stock_tier` は品物の段。値の意味も決め方も未特定。
  ゲームが `set_item_from_world_data` に渡す値をそのまま使い回すこと
  （`312_shop_restock` はこの形で、値は解釈しない）
- 主の持ち物を空にしてから売買を始めると、ゲームが初回と同じ経路で品揃えを
  作り直す ― という前提で `312_` は書かれているが、**実機では未確認**。
  外れたときのために、空にした後で補充されたかを見て、駄目なら控えを戻す
  （VERIFICATION.md §3 の該当項）

日付は世界に1つ（`world.days_elapsed`。セーブでは `world_data.days_elapsed`。
実セーブで `3651` を確認）。進めているのは `InstantaleApp.elapse_days(days)`（§2.19）。

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

### 2.14.1 自由入力のアイテム一覧（`ToolListPopup`）

入力欄の左下のアイコン（`InstanTaleHUD.press_item_icon` / `press_skill_icon`）で開く
一覧。選ぶと `select_item_to_action_input(btn)` が入力欄へ差し込む。

```
scripts.hud.new_hud:ToolListPopup(callback, tool_text_list=['a','b','c'])
    bases = [GridLayout]                     ← 列を持てる（modules.json の mro）

cols=1  rows=None  spacing=[0,0]  padding=[0,0,0,0]
size_hint=[1,1]  pos=(0,0)  size=[926.64, 78.75]   ← 親（入力欄の帯）と同じ寸法
minimum_height=1026                                 ← 中身が要求する高さ
行 18個  173x57  行の下端 y=0 → 上端 y=1026        ← 箱の外まで並んでいる
```

- `GridLayout` 派生なので `cols` を増やせば折り返すはずだが、`cols=2` を入れても
  見た目は変わらなかった（2026-08-02 実測）。`size_hint=[1,1]` で箱は入力欄の帯と
  同じ 78.75 しか無いのに行は 0〜1026 に並んでいる ＝ 行の位置をこの格子が決めて
  いない。列にするには、`cols` を入れたうえで箱の高さを中身ぶんにし、それでも
  折り返らなければ行の位置を自分で入れる（`115_ui_item_list_fit`）
- 一覧の幅（926.6）は行の幅（175）よりずっと広い。2〜4列なら幅は足りる
- 行は直接の子。`spacing` は `[x, y]` の列（`GridLayout` の形）
- 一覧は入力欄を下端にして上へ積まれるので、件数が増えると画面の上端を突き抜ける。
  ゲーム側に高さの頭打ちは無い（Kivy の `DropDown` ではないので `_reposition()` の
  自動縮小も働かない）
- 開いた直後は、まだレイアウトが走っていない寸法が読める。しかも
  行が並び終わっているのに入れ物の矩形だけが `(0, 0, 926.6, 78.75)` のまま、
  という瞬間がある（2026-08-02 実測）。組み上がったかどうかは行の位置と高さで
  判断すること。入れ物の矩形を条件にすると永久に成立しない。§2.14 の
  `ItemDetailBox` と同じ注意がここにも要る

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

main_024 で本体が直した。`sanitize_path_name(name)` が `scripts.functions` と
`save_world_json` の2箇所に追加され、置換先は `110_` と同じ全角になっている。
どちらも main_023 のリコンには無く、`__main__:sanitize_enemy_names(quest_value)` も
新規。2026-08-05 の実測:

```
worlds/<世界>/characters/「沈黙の」ミテ／     末尾は U+FF0F（全角スラッシュ）
  face_image.png / generated_image.png / no_bg_image.png ほか計7件が生成済み
```

その間 `110_` は実ゲームで一度も発火していない（`out/character_name.log` が無い。
`out/test/character_name.log` にある 17:17 の記録はオフラインテストの合格分）。
禁則文字を含む名前で画像が出来ている＝本体の消毒が働いている。

> **本体とこちらでは直す場所が違う。** `110_` は `Character.__init__` で名前そのものを
> 書き換える（セーブに残り、画面表示も変わる）。本体はパスを組むところで消毒する
> 設計に見える。どちらが動いたかは名前を見れば分かるはずだが、上の個体は
> `world_data.json` に載っておらず（`npcs` 64件のどれでもない）保存名を確認できていない。
> 「本体が名前ごと直している」のか「パスだけ直していて `110_` に渡る名前がたまたま
> 綺麗だった」のかは未確定。

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

- **能力値はレベルでは伸びない**（セーブのバックアップで同一プレイヤーを追った実測。
  `levelup()` が「能力値の更新まで持つ」というのは関数名からの推測で、実際には動かない）:

  | レベル | `original_ability_scores`（筋・耐・敏・知・賢・魅） | 合計 |
  |---|---|---|
  | 3〜33 | 24・18・26・25・25・24 | 142 |
  | 41〜73 | 26・22・26・26・25・24 | 149 |

  30 レベル進んで合計 +7。動かしているのは宿の訓練（`VacationTrainManager` /
  `get_training_price(attribute_average, experience_level)`）だけで、**作成時に
  振った値がほぼそのまま最後まで続く**。能力値を基準にした調整を書くなら、
  「レベルで伸びる」前提を置かないこと（`313_event_ability_check` が最初にこれを
  外した。VERIFICATION.md §2.37）
- **作成時の値は才能点（`point_use`）で決まり、既定はかなり低い。**
  `characters/<名前>/character_sheet.json` の実測:

  | キャラ | `point_use` | 合計 | 各値の幅 |
  |---|---|---|---|
  | テスト女性 / テスト男性 | 16 | 66 | 11 一律 |
  | ヴァルカ・ヴォルガド | 31 | 71 | 9〜15 |
  | ヴォルガ・クレイグ | 37 | 75 | 9〜16 |
  | アーリ | 300 | 142 | 18〜26 |

  つまり**普通に始めると各能力値は 9〜16**。24 前後という値は才能点を大量に
  積んだキャラのもので、既定の姿ではない。セーブで見た上端は 30
  （`original_ability_scores` の最大値。上限かどうかは未確認）。
  能力値に閾値を置く調整は、9〜16 の側を基準にしないと**新規キャラで一度も
  発火しない**（`313_` が実機1回目でこれを踏んだ。VERIFICATION.md §2.37）
- `gain_exp` が内部でレベルまで上げるのか、呼び出し元が `check_levelup` →
  `levelup` を回すのかは読めない。両方に耐える書き方（レベルが動いていなければ
  `check_levelup` を聞く）にすること
- 支給の点数を決めているのは `scripts.functions` 側:
  `get_training_experience_point(cleared_quest_difficulty)` /
  `get_days_elapsed_experience_point(experience_level)`（**点数ではなく率**。
  総当たりの実測でレベル1→0.011・レベル13→0.174 と 1 未満の float を返す。
  VERIFICATION.md §2.36 の `214_` のログ） /
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
包んでいないことを確かめる。包んでいると、表に入るのはローダのラッパのコード
オブジェクトになる。これは全パッチが共有しているので誤爆する（`306_` が踏んだ）。

### 2.18 エリア移動（土地から土地へ）

`out/events.log` の実測（2026-07-28 / 07-30 / 07-31、計4回）と `targets.txt`:

```
process_choice(DisplayAreaMoveChoice, choice_text='他の土地へ行く')   [MainThread]
process_choice(AreaMoveCofirmation,   choice_text='陽光の砦')          [MainThread]
process_choice(AreaMoveManager,       choice_text='馬車(1000G)')       [MainThread]
                                                   '徒歩(3ヵ月)'
```

| クラス | `__init__` | 何か |
|---|---|---|
| `DisplayAreaMoveChoice` | `(self, app)` | 行き先の一覧 |
| `AreaMoveCofirmation` | `(self, app, target_area_id)` | 手段の確認（徒歩・馬車が並ぶ）。綴りは `Cofirmation` |
| `AreaMoveManager` | `(self, app, target_area_id, mode)` | 実際の移動。`method_1` / `show_loading_text` |
| `AreaMoveRestriction` | `(self, app, target_area_id)` | 行けないときの画面 |

- `mode` の実値は `'on_foot'` / `'coach'`（2026-08-01、確認画面のボタンの
  `args` を読んで実測）:

  ```
  ('徒歩(3ヵ月)', ['7', 'on_foot'])    ('馬車(1000G)', ['7', 'coach'])
  ```

  それでも値を書き起こして組み立てるより、確認画面のボタンの `args`
  （`[target_area_id, mode]`）をそのまま写すほうが安全（`307_` はこの形で、
  実値は照合にだけ使う）
- 日数を進めているのは `InstantaleApp.elapse_days(days)`（2026-08-01 に実測）。
  徒歩の移動で `90` が渡ってくる（表示は `徒歩(3ヵ月)`）。`307_` がここで渡す数を
  `14` に減らし、実際にその日数で移動が完了することを確認済み。LLM 側にも
  `ElapseDays: type:="elapse_days", days` というモデルがある（`out/prompt_bloat.log`）
- 移動中の表示は `徒歩で目指す。長旅だ...` → `.` `..` `...` → `辿り着いた。`
  （すべて `InstantaleApp.add_text(context)` を通る。点は
  `AreaMoveManager.show_loading_text`）。馬車の側の文言は未実測
- `AreaMoveManager.show_loading_text` は `__main__` にある数少ない
  「ゲーム native の待機表示」の入口（`206_` が発火を確認済み）
- LLM 側に `llm_manager:area_move_rejector(character_life_log, player,
  character_instance, worldview)` がある。同行者が移動を拒む経路と思われるが未検証

### 2.19 体力（スタミナ）は `physical_integrity`

`Character.physical_integrity` / `max_physical_integrity`（`__init__` の既定は
どちらも 100）。戦闘のHP（`current_hp` / `max_hp`）とは別物。

同じプレイヤーを1晩追った実測（2026-08-01、`out/events.log` のダンプ3点）:

| 時刻 | `physical_integrity` | `max_hp` | `exhausted` |
|---|---|---|---|
| 01:47 | 100 | 1560（`original_max_hp` と同じ） | `False` |
| 01:49 | 50 | 1365 | `True` |
| 02:03 | 0 | 1170 | `True` |

- 土地の移動やクエストで減る。回復は医療施設
  （`MedicalTreatmentManager(app, treatment_price)` /
  `scripts.functions:get_heal_physical_integrity_barden(value)`）
- 体力が減ると最大HPが下がる（1560 → 1365 → 1170。`original_max_hp` は
  1560 のまま）。式は特定していないが、`physical_integrity` が上限を削る側
- `exhausted`（bool）は 50/100 の時点で既に `True`。どの閾値で立つかは未特定。
  減る量・回復量は未確認
- **上限はレベルで伸びる**（`get_max_physical_integrity(level)`）。同一プレイヤーを
  追ったセーブの実測 ― レベル 1→10 / 5→11 / 8→12 / 15→15 / 22→19 / 25→22 /
  30→26 / 41→34 / 49→39 / 50→40 / 55→42 / 58→43 / 73→50。式は未特定だが、
  **`100` は既定値（`__init__`）で、実際に遊んで到達する値ではない**。
  レベルに対して上限が合っていないセーブは、どこかが壊れている合図になる
  （VERIFICATION.md §2.36 でこれを使って新規キャラのレベル60を切り分けた）
- `current_hp` が `max_hp` を超えている状態を観測している（2591 > 1560）。
  戦闘に入る時点で丸めていると思われるが未確認。HP を条件に使うなら
  `current_hp <= max_hp` を前提にしないこと
- 「体力が足りないなら断る」を書くならここを見る（`307_` の
  `STAMINA_MIN_PERCENT`）

### 2.20 手配度（`area_history` の `lawfulness`）

治安上の立場は土地ごとに持たれている。実セーブの復号（2026-08-01。§2.16）:

```python
player_data["area_history"] = {
    "0": {"residency": {"total_days": 909, "last_stay_end": 104},
          "achievements": ["…", "…"],
          "lawfulness": 10},          # ← エリア id ごとに1つ
    "1": {...},
}
```

| 項目 | 分かっていること |
|---|---|
| 在り処 | `Character.__init__` の引数にある（`area_history=None`）。プレイヤーもNPCも同じ `Character` |
| 鍵 | エリア id（`player.current_area` と同じ語彙。文字列） |
| `lawfulness` | 素の平常値は `10`（40エリア全てが 10 の実セーブで確認）。小さいほど手配が重く、0 未満で犯罪者。実プレイのセーブで `-40` を観測している（2026-07-16 の別ワールド。エリア `"2"` だけが -40 で他は 10）ので、負の側は少なくとも -40 まで伸びる。上限は未特定 |
| `residency` | その土地に滞在した日数の累計と、最後に発った日 |
| `achievements` | その土地で成した事の文章（LLM が書いたもの）の配列 |

- 読み書きするヘルパは無い（`scripts.functions` にも `__main__` にも
  `lawfulness` を名前に含む関数は無い）。値を直接触るしかない
- 減らしているのは LLM の判定側。プロンプトのスキーマに `lawfulness_loss` が
  ある（`111_llm_prompt_replace` の置換ルールが実プロンプトで拾っている）。
  どの行為でいくつ減るかは未特定
- 関連しそうな `__main__` のクラス: `ImprisonmentStartManager(app,
  imprisonment_years, charges, incident_details)` /
  `ImprisonmentPhaseManager` / `ImprisonmentEndManager(app, imprisonment_years)` /
  `DisplayCitizenshipChoice` / `CitizenshipChoiceManager(app, status)` /
  `GetCitizenshipManager(app, status)` / `DieFromOldAgePrison`。
  手配度との繋がりは未確認（`309_office_pardon` はこれらを一切通らず、
  `area_history` の値だけを書き換える）
- 手配度を直接書き換えても、ゲーム側の帳尻は崩れない（2026-08-01 の実機。
  `-10` → `10` に書き換えたあと普通に遊び、ゲーム自身のセーブに `10` と
  所持金の減りが両方そのまま残った）

#### 役場（`administrative_office`）の選択肢（2026-08-01 実測）

```
Facility.choices = ['労働の募集をみる', '市民権の発行', '出る']
   ↓ ゲームがこれに『会話する』を足して並べる
app.buttons      = ['労働の募集をみる', '市民権の発行', '出る', '会話する']
```

- 手配を解く選択肢は素のゲームには無い（`309_office_pardon` と二重にならない）
- `出る` が `会話する` より前に来る。施設の選択肢は「操作 → 退出」の順とは
  限らないので、位置を文字列や並び順で決め打ちしないこと（`309_` は
  `MovePhaseManager` を呼ぶ最初のボタンの手前に挿している）
- 会話（`ConversationStartManager` → `ConversationEndManager`）を挟んでも、
  抜けた後に施設の選択肢が組み直されるので、そこへ足した自前のボタンは
  組み直しのたびに入れ直す必要がある（`refresh_choice_buttons` を包む形）

### 2.21 自由生成施設のシーン記述エンジン（`scripts.free_facility`）

main_023 で入ったイベント記述用の実行エンジン。JSON のステップ列を解釈して
シーンを走らせる。`209_probe_free_facility` の実測（2026-08-02）。

MOD にとっての要点は、プログラムがセーブの中にあり、書き換えられること。

```python
world_dict["free_facility_programs"]     # {program_id: プログラム(dict)}
world_dict["free_facility_enabled"]      # 世界生成時のオプション
```

#### 2.21.1 施設との結び付き

ゲーム自身がエンジンを起こすのは `facility_type == 'free'` の施設。実測した世界には
集落ごとに1つ、計3つあった（`0/10` `7/29` `8/57`）。

> **ただしエンジン自身は施設の種類を見ていない。** MOD から
> `FreeFacilityManager(app, program_id)` を組んで `process_choice` に渡せば、
> 宿屋でもギルドでも同じように走る（2026-08-02 に実機で確認。
> VERIFICATION.md §2.30）。`free` 施設が3つしか無いことは制約にならない。

```python
facility.facility_type = 'free'
facility.choices       = {'利用する', '出る'}
facility.config = {
    "level_of_detail": 0,
    "concept":    "プレイヤーはここで、わずかな運勢を占うか、…",
    "program_id": "free_10",              # = "free_" + facility.id
    "free_flags": {"visited_fire": 1},    # ← フラグの実体はここ
}
```

`FreeFacilityManager(app, program_id, resume=None, vars=None)` が実行する。
選択肢を1つ押すたびに入り直す形で、再開位置と会話の蓄積を引数で渡す:

```
FreeFacilityManager(app, 'free_10')
FreeFacilityManager(app, 'free_10', {'kind': 'goto', 'label': 'drink_action'},
                                    {'__session': ['また泥にまみれて戻ってきたのか。…']})
```

`FreeFacilityManager` は `__main__` ではなく `scripts.free_facility` にある
（`getattr(__main__, ...)` では引けない。§2.5 と同じ罠）。

#### 2.21.2 ステップと効果

`STEP_TYPES` は18種。`lint_program(program)` がゲーム自身の検証器として露出して
いるので、実機に入れる前にプログラムの正しさを確かめられる。

| 分類 | ステップ |
|---|---|
| 制御 | `label` / `goto` / `if` / `end` / `random` / `calc` / `var_set` |
| 表示と入力 | `text` / `choice` / `input`（`store` に自由入力、`append_history`） |
| 状態 | `flag_set` / `flag_get` / `memory` / `history_clear` |
| 外部 | `llm` / `effect` / `elapse` / `call_phase` |

`effect` は `gold_add` / `item_add` / `exp_add` / `status_add` / `heal` / `wait` /
`show_character_image` / `play_sound` / `remove_character_image`。
金もアイテムも経験値もここから渡せる。

`call_phase` で渡せる先は5つで、`get_phase_class` は全部 `__main__.X` に解決する
（実測）:

```
BattleStartManager / DisplayQuestChoice / DisplayTrainingChoice
DisplayVacationChoice / ShoppingStartManagerRemake
```

`llm` ステップは2形。判断と描写が分けてある。

```json
{"type":"llm","context":["facility","player","owner","area"],
 "prompt":"（ゲームマスターへの指示。プレイヤーには見えない）",
 "use_history":true,"output":{"mode":"text"},"store":"VAR"}

{"type":"llm","...","output":{"mode":"choice","options":["A","B"]},
 "branch":[{"choice":"A","goto":"..."},{"choice":"B","goto":"..."}]}
```

後者は「LLM が状況を判断して1つ選び、プログラムがその先へ飛ぶ」。仕様書
（`_DSL_SPEC`）は使い分けまで明記している:

> llm choice mode expresses the judgment of the NPC or the world.
> Decisions that belong to the player (consent, purchases, accepting a price)
> must be a player "choice" step instead.

`memory` は訪問の要約をプレイヤーのライフログと目撃者の記憶に入れる。

#### 2.21.3 フラグは施設ローカル。世界規模の状態は持てない

これが MOD 側の設計を決める制約。 条件が読めるソースは3つしかない:

```json
{"type":"if","cond":{"source":"var"|"flag"|"player","key":K,"op":"==","value":V}}
```

- `flag` … `facility.config['free_flags']`。その施設だけ。来訪をまたいで残る
  （実測: `visited_fire` が前回の来訪から生きていて `welcome_back` へ分岐した）
- `var` … その訪問の中だけ
- `player` … `gold` と `age` のみ

`_flag_store(scope)` は scope 引数を取るが、実測で観測できたのは `'facility'` だけ。
生成側のスキーマ（`_GenFlagSet`）にもスコープを選ぶ項目が無いので、プログラムから
選べるスコープは施設ローカル1種。

したがって施設をまたぐ話は DSL だけでは書けない。跨ぎたい MOD は状態を自分で
持ち、渡すプログラムをその都度組む（DSL に分岐を書かせるのではなく、
その時点のプログラムを渡す）。

渡し方は2つある。後者を使うこと。

| 方法 | 痕跡 |
|---|---|
| `world_dict['free_facility_programs']` に足す | セーブに残る。MOD を外しても id が残る |
| `_lookup_program` を包んで自前のものを返す | 何も残らない |

```python
@ctx.wrap("scripts.free_facility:FreeFacilityManager._lookup_program")
def lookup_program(orig, self, *args, **kwargs):
    if getattr(self, "program_id", None) == MY_ID:    # 完全一致のみ
        return build_program_for_now()               # その時点の状態で組む
    return orig(self, *args, **kwargs)               # free_* は必ず素通し
```

この形にすると`flag_set` を使う理由も無くなる。分岐の前提は MOD が組む
プログラムに焼き込めばよく、DSL 側は `var`（訪問中だけ）で足りる。
`flag_set` は施設の `config` に書かれてセーブに残るので、使わずに済むなら
使わない（実測: 受け皿の無い宿屋にも `free_flags` が新設された）。

#### 2.21.4 上限と禁止事項

| | |
|---|---|
| `MAX_STEPS_PER_EXECUTE` | 300 |
| LLM 呼び出し | 既定 20、生成物は 12。1本 2〜6 ステップが指針 |
| プログラムの大きさ | 15〜45 ステップ |
| `SESSION_MAX_CHARS` / `_ENTRIES` | 4000 / 40 |
| `elapse` | 訓練・長逗留の概念にのみ。1経路1回、units 1〜2 |
| `item_add` | 1回の来訪で最大2つ |
| `status_add` | 1回2つまで、`duration` は戦闘ターン数 |
| 禁止 | 賭博をテーマにした施設、性的な内容 |

金額を直に書かないのが作法。`prices` / `payouts` に名前付きで宣言し、
`{price.X}` `{payout.X}` で参照する（エンジンがその土地の相場で解決する）。
「金を取って文を出すだけ」の活動は `lint_program` が defect として弾く。

### 2.22 NPC の退場は `config['is_dead']`

`Character.config` の中にある（セーブされる33項目のうち `config` の下。トップレベル
には無い）。`210_probe_character_state` の実測（2026-08-02）:

```python
character.config = {"level_of_detail": 2, "is_player": False,
                    "is_dead": True, "difficulty_level": 4}
```

印が立っても、施設の名簿からは外れない。世界の全 NPC 35 人を舐めて、
`is_dead=True` の1人が `roster x1` のまま残っていることを確認した
（`referenced by nothing: 0`）。にもかかわらずゲーム内では会話にも呼び出しにも
出てこない（実プレイで確認）。つまり:

> 名簿からは外さない。**読む側が印を見て飛ばしている。**

MOD にとっては都合がよい。参照が切れないので何も壊れず、`False` に戻せば復帰する。
NPC を退場させたい MOD は `move_npc_to_facility` で移送する必要が無い。

ただし施設の主には使えない。`Facility.owner` に載っている NPC を消すと、その店に
話せる相手が居なくなる。実測した世界では 35 人中 24 人が主で、自由に使えるのは
名簿にだけ載っている 11 人だった。事件ものなどで NPC を退場させる MOD は、
主でない NPC から選ぶか、`save_area_json:generate_npc` で自前に用意する。

`state` は死亡とは無関係（全員 `''`）。HP は `current_hp` / `max_hp` /
`original_max_hp` で、`physical_integrity`（§2.19）とは別物。

### 2.23 NPC を作る（`save_data_dict['npcs']` に書いてから組む）

MOD が事件の登場人物などを世界に足したいとき。実測でここに至るまでに
3回外しているので、通る手順だけを書く。

```python
npcs = app.save_data_dict["npcs"]          # ★ ここが本体
npc_id = str(max(int(k) for k in 名簿) + 1)
npcs[npc_id] = データ                       # セーブの形（§2.22 の33項目）
character = app.world.generate_character(npc_id, データ)
app.move_npc_to_facility(npc_id, character, 施設, ノード)
```

| 関数 | 役割 |
|---|---|
| `World.generate_character(id, value)` | 作る側ではない。`save_data_dict['npcs'][id]` を id で引いて `Character` を組む。無い id は `KeyError` |
| `save_area_json:generate_npc(...)` | 呼んでも何も作られない（世界のどこにも現れない）。返るのは `world_dict` そのもの |
| `scripts.characters:Character(...)` | コンストラクタが完全な署名で露出。最後の手段として直に組める |

> **素データの置き場所は1つではない。**`app.world_dict['npcs']` と
> `app.save_data_dict['npcs']` は別の辞書で件数も違う。`generate_character`
> が読むのは後者。どちらか一方に賭けず、既存の character id が鍵になって
> いる辞書を全部探して全部に書くのが確実（`302_` の `ui.party_stores` と
> 同じ考え方）。
>
> 採番も決め打たない。ゲームは遊んでいる最中にも NPC を作る（新しい町の
> 生成で 10 体が一度に増えた）。空き id は `world.characters` と
> `npcs` の両方を見て決める。

生成した NPC は HP もスキルも装備も立ち絵も空でよい。ゲームが会話や戦闘の
直前に `ensure_npc_detail_generated` で埋める（§2.22）。名前に `"` などを
入れないこと（`110_` の対象。立ち絵だけが無言で作られなくなる）。

> **名前は `generate_character` の前に決まっている。**素データを先に書く順序なので、
> `Character` を組んだ後に `self.name` だけ直しても `npcs` には古い名前が残り、
> 次の保存で戻ってくる。名前を直す MOD はid を鍵に持つ辞書を全部書き換える
> （`120_fix_npc_name_collision`。既にある鍵への代入なので並び順は動かない）。
>
> 名前の重複も同じ場所の話。LLM は近い名前を繰り返し引くので、世界に
> 「バルガス」と「ヴァルガス」が同居する。`120_` がここで弾き、付け直す名前は
> 同梱の名簿から選ぶ（`male` / `female` は `category` で選び分ける。
> 実データは `young man` / `teenage girl` など8種で、`woman` は `man` を含む）。

#### 項目の並び順は、揃えるだけでなくこの順でなければならない

セーブは辞書をそのまま JSON に落とす。書いた順がそのままファイルの行順に
なる。そしてセーブを読む側には、項目を上から順に並べて見せる道具がある
（別途あるセーブエディタ）。順番が変わると、項目は全部揃っているのに
表示が崩れる。

33項目の正しい並びは実際のセーブから起こせる
（`saves/<世界名>/savedata_plain.json` の `npcs`）。

| # | 項目 | | # | 項目 | | # | 項目 |
|---|---|---|---|---|---|---|---|
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

後ろの4つ（30〜33）は遊び始めてから増える。プリセットの `world_data` は
29項目までで、`savedata` になると33項目になる。`knowledge` はリスト
（`[]`）で、辞書ではない。

> **守り方は「全項目を持ったひな型を先に作り、上書きだけする」。**
> `dict.update` は既にある鍵の位置を動かさないので、ひな型が33項目を漏らさず
> 持っている限り並びは保たれる。逆に1つでも欠けていると、その項目だけが
> 末尾に足されて並びが壊れる。項目を足すときは必ず表の正しい位置へ差し込む
> こと ― 末尾に足さない。
>
> セーブに NPC を足す MOD は、33項目を揃えたひな型を定数で持ち、並び順が崩れて
> いないかをオフライン検証で見ること。

### 2.24 会話中の NPC に知識を持たせる

「この人物はこれを知っている」を会話に差し込む口が、ゲーム側に2つある。

```python
llm_manager:conversation_starter(messages, ...)          # 第一声
llm_manager:conversation_facilitator(..., retrieved_knowledge, job_knowledge='')
llm_manager:conversation_facilitator_after_retrieval(..., retrieved_knowledge)
```

- 第一声だけ変えたいなら `conversation_starter` に渡す messages の
  コピーを差し替える（`300_` の手口。§2.5）
- プレイヤーが自由入力で尋ねたときにも効かせたいなら
  `retrieved_knowledge` に足す。ゲーム自身が持っている「この人物が引き出した
  知識」の枠で、受け答えを組むときに読まれる

> **差し込む条件は「誰と話しているか」で決めること。**`character_instance`
> （両者とも引数の4番目）から id を引いて突き合わせる。MOD が用意した
> ボタン経由かどうかで判定すると、プレイヤーが普通に「会話する」で
> 話しかけたときに何も起きない（実機で踏んだ・2026-08-03）。
>
> 引数の位置は版で動きうるので、キーワードを先に見て、無ければ位置で拾う。

会話の履歴・記憶はゲーム自身が持っている。差し込んだ知識はその会話の要約と
して記憶に残るので、後の会話にも影響する（同じ相手が前の話を引きずる）。
1回きりにしたいなら、差し込む条件の側で「まだ拾っていない手がかりだけ」と
絞る。

### 2.25 会話の記憶の実体（`current_log` / `relationship` / `conversation_resolver`）

`213_probe_npc_memory` の実測（2026-08-08。会話2回・同一 NPC・ローカル LLM）。

**書く側。** 会話の終了時（`app.in_conversation` が落ちる数秒前）に
`conversation_resolver` というマネージャが回り、NPC に書くのは2箇所:

- `current_log`（リスト）… `〈会話〉…` で始まる会話1回ぶんの要約が**追記**
  される。2件目の要約は**1件目の全文を含んだ累積形**だった（153字 →
  2件目が「1件目の全文＋続き」の411字）。つまり `current_log` 自体が
  会話を重ねるほど同じ文を二重三重に抱えていく
- `relationship`（dict）… `{"player": {affinity, affinity_text[],
  relationship[], conversation_count}}`。`conversation_count` が加算される

> **要約は必ずしも走らない（実測2件）。要約の入口は終了ボタンの manager
> 1つしかない。** 呼び出し元は実測で確定している:
>
> ```
> ConversationEndManager.execute (instantale.py:4419)
>   -> finish_conversation (:4350)
>   -> ConversationEndManager.resolve_conversation (:4380)
>   -> llm_manager:conversation_resolver (llm_manager.py:824)   ※別スレッド
> ```
>
> つまり「会話を終了する」の spec を**通らない**抜け方は、すべて要約を
> 素通りする。実測した不発は2件:
>
> 1. 会話の途中で行動処理（`master_ai_facilitator_from_conversation`）へ
>    進んだ後にセッションが終わった回（2026-08-08 15:30〜。アプリ終了との
>    切り分けは未了）
> 2. **会話を閉じないままタイトル/別セーブのロードへ抜けた回**
>    （同 16:18〜。ハンスとの会話が放置され、要約されずに消えた）
>
> **走らない抜け方では、その会話はゲーム側のどこにも要約されない。**
> 会話の記憶に乗る MOD は「要約は必ずしも走らない」前提で設計すること。
> ターンごとに動く仕掛け（`311_` の抽出など）はこの取りこぼしの保険に
> なる。発火の有無は `213_` を入れたまま抜け方を変えて閉じれば、
> `out\npc_memory.log` の `resolver: 発火/不発` の行で読める。

**`current_log` は一時置き場。移送の契機は日付の変更**（実機で確認・
2026-08-08）。日が変わると中身が `life_log` の1エントリへ移り、
`current_log` は空になる。エントリの形は
`{day_start, day_end, summarized_count, content}` で、`content` は
**`current_log` のリストをそのまま `str()` した文字列**。移った後も
`life_log` として毎回全文プロンプトに載るので、「前の話を引きずる」経路は
途切れない。`summarized_count` が在ることから、`life_log` はさらに
`memory.memory_archive` へ畳まれる段があると読めるが、その契機は未実測。

> **要約が薄いのは移送のせいではない。`resolver` の出力量が実質固定だから。**
> 移送は `str()` の丸写しで内容を落とさない。落ちているのは**その前**で、
> `resolver` が書く要約は会話の長さにほとんど依らず一定の短さになる
> （実測: 3ターン→116字 / 3ターン→142字 / 5ターン→185字 /
> **8ターン（`messages` が 5,597字まで育った会話）→118字**）。つまり
> 長く濃い会話ほど捨てられる割合が大きい。日付変更のあとに
> 「内容が大分落ちている」と感じるのは、薄い要約が初めて永続側に現れる
> のがそこだからで、劣化はもう起きた後である。

なお `current_log` が空になった後の次の会話終了では、`resolver` は過去の
会話の内容まで含めて**1本に書き直した**要約を新規に書いた（実測268字。
読み込みは `life_log` 経由と思われる）。書き直しのたびに固定の短さへ
圧縮されるので、**この経路の情報は単調に減る一方**で、増えることはない。

**`resolver` の頼み文に文字数の上限は無い**（`output_data/<世界>/<PC>/
`conversation_resolver/N.json` の実物・2026-08-08）。要点だけ:

```
「今日の現在ログ」に続きとして書き足す形で、会話の内容(user, assistant に
よって記述された履歴)を、一切情報を損なわないが簡潔に要約して下さい。
客観的に、どこで誰と誰がどういう会話をしたかを記述する事。
```

出力スキーマは `{"summary": str}` の1欄だけ。つまり短さの原因は上限では
なく、**「一切情報を損なわない」と「簡潔に」が同じ文で衝突していて、
モデルが後者に倒れること**。加えて指示が求めているのは「どこで誰と誰が
どういう会話をしたか」＝**会話の流れ**なので、確定した数値や条件は流れに
乗ったものしか残らない（実測の126字要約では取引額の `5000Gold` は残り、
8ターンの会話→118字では宿泊料金の体系が落ちた）。

> 文字数の上限が無いということは、**`111_llm_prompt_replace` の置換ルール
> 1行で言い回しを変えられる**ということでもある（新しい MOD が要らない）。
> ただし要約が伸びた分は同じ相手との会話プロンプトに毎回全文載るので、
> 伸ばす前に下の重複掃除で枠を空けるのが順序として正しい。

**ゲーム側の記憶は重複を抱えたまま膨らむ（実測2種）。**

- `current_log` の累積形（1件目の全文を含む2件目）が畳まれずにそのまま
  `life_log` へ移るので、`life_log` の1エントリの中に**同一要約の5連
  コピー**を含む実例があった（事務官エドガー・3エントリ計911字。この
  911字は毎回全文プロンプトに載る）。会話の実内容は1行ぶんしかない
- 会話中の行動を処理する `master_ai_facilitator_from_conversation` の
  プロンプト（実測6,200〜8,100字）は、NPC のプロフィール行と性格行を
  **4回ずつ**、プレイヤーへの感情行を2回含む（行単位の完全一致で損262字。
  `102_` は `messages` の隣接重複しか見ないのでこれは畳めない）。この
  経路は会話5関数を通らないため `311_` の注入も届かない ― 重複はゲーム
  自身のプロンプト組み立てによるもの

`memory` はセーブ表（§2.23）の顔ぶれに反して**この時点では動かない**。
実体は5鍵の dict（`life_log` / `memory_archive` / `session_log` /
`prior_area_summary` / `brief_summary`）で、会話2回の間ずっと
`brief_summary: "ゲーム開始"` のままだった。更新契機は未実測
（エリア移動・セッション区切りと推定）。`knowledge` も None のままで、
会話系関数の第2引数 `character_life_log` は NPC・プレイヤーどちらの
`life_log` とも一致しない空 dict だった。

**読む側。** `send_request*` 境界での含有照合では、`profile` /
`personality` / `current_log` / `relationship`（`affinity_text` の文）が
**毎回・全文**プロンプトに載る。§2.24 の「同じ相手が前の話を引きずる」の
実経路は `current_log` で、retrieval を待たず開始の第一声
（`conversation_starter`）から載る。

> **`311_npc_profile_memory` と併用したときの帰結。** 同じ会話の事実が
> (a) `current_log` の要約 (b) `311_` の注入プロフィール (c) `311_` の
> `about_player` の3箇所で**同一プロンプトに並ぶ**（実測: 2回目の会話は
> プレイヤー入力11字に対して開始時点で2,206字）。しかも `current_log` の
> 累積形により (a) 自体も膨らむ。3者は言い換えの関係で行単位の完全一致は
> 無いため、`102_` の隣接重複除去では畳めない。`311_` 側の対応を考える
> ときは、出来事の再記述（(a) と被る部分）を抽出から外すのが筋になる。

なお会話系5関数の**先頭4引数**の並びは共通（§2.24）だが、5番目以降は
関数ごとに違う（`conversation_starter` は `args[4]='NPC'` の文字列が挟まり、
worldview 以降が1つ後ろへずれる）。5番目以降を読む MOD は位置を決め打ち
しないこと。

---

### 2.26 クエストの外の判定（マスターAI の `roll_the_dice`）

クエストに入っていないとき（街・施設・会話中）の自由入力は
`master_ai_facilitator` / `master_ai_facilitator_from_conversation` が処理する。
フィールドイベント（§2.9）とは**別系統で、仕組みも違う**。

```
プレイヤーの入力
  → master_ai_facilitator          think / narration / process[] / finished
       process の1つが roll_the_dice: {"type": "roll_the_dice", "chance_percent": 70}
  → ゲームが振り、次のターンのプロンプトに <結果:成功> を差し込む
  → 続きを master_ai_facilitator が処理する（finished=true まで繰り返す）
```

- **確率を決めているのは LLM 自身**。`chance_percent` をそのまま出力する。
  フィールドイベントのように、ゲームが `credibility` から式で作るのではない
- 差し戻されるのは `<結果:成功>` / `<結果:失敗>` **だけ**。確率は書かれない
  （フィールドイベントの `<確率N%: 成功>` とは形が違う。判定の印を探すコードは
  両方を見ること）
- **プロンプトに能力値は1つも載っていない**（`能力値` / `strength` / `dexterity` /
  `attribute` すべて 0 件）。参照能力値にあたる欄も無い。つまり
  **クエスト外の判定にもキャラクタの能力値は入っていない**
- 権限は 14 種（`roll_the_dice` / `join_to_player_party` / `move_gold` /
  `get_gold` / `elapse_days` / `generate_item` / `move_item` / `deal_damage` /
  `add_status_effect` / `npc_say` / `start_battle` / `arrest_player` /
  `generate_npc`）。「時系列があるものは1段階目だけ実行して次のターンに回せ」と
  指示されているので、ダイスを振った回は必ず `finished=false` で戻ってくる

実測（`output_data/` の `master_ai_*` 2,021 件。`roll_the_dice` は 191 回、
うち結果まで対応が取れたのが 168 回）:

| LLM が指定した確率 | 回数 | 実測の成功率 |
|---|---|---|
| 30% 以下 | 10 | 10% |
| 35〜45% | 18 | 44% |
| 50% | 36 | 47% |
| 60% | 49 | 71% |
| 65〜70% | 45 | 62% |
| 75% 以上 | 10 | 60% |

- 全体で 56.5%（95勝73敗）。**振っている側は概ね正直**
- 指定される確率は 50 / 60 / 70 に 134/191 が集まる。プロンプトの例文が
  「まずは成功確率50%でダイスを振ろう」なので、そこへ引かれている
- 対応付けは本文の並び（`chance_percent` の直後に現れる `<結果:…>`）で取った
  文字列ベースなので、数件のずれは残りうる

> クエスト中の自由入力は `quest_referee_with_free_action` が受ける（§2.9）。
> こちらには `roll_the_dice` に相当する権限も確率の印も無く、
> 進行（battle / field_event / move / retire）を選ぶだけ。**判定はしない。**

`targets.txt` には `master_ai_facilitator_in_quest` /
`master_ai_faciltiator_from_conversation_in_quest`（綴りはゲーム側のまま）も
あるが、`output_data/` には出ていない。クエスト中の自由入力がここへ回る条件は
未確認。**4つとも同じ形の応答**なので、この経路を触る MOD は4つとも見ること
（`313_event_ability_check`）。

---

## 3. 調査手法

純粋関数は総当たりで定義域を割り出す。副作用の無い参照関数なら、稼働中のプロセスで
直接呼んで有効域を特定できる（`get_npc_employ_price` は `0..150` を走査して `0..76` と判明）。
引数の語彙も同じ手で確定できる（実在 id を渡して例外の出ない値を探す＝
`QuestChoiceManager` の `'settlement_quest'`）。再現を待つより速い。

同じ手で「その MOD がまだ要るか」も測れる。ゲームが更新されて本体側が直った場合、
症状が出ないことだけでは本体が直したのか MOD が直したのか区別が付かない。そこで
MOD が包む前の素の関数を控えておき、注入のたびに総当たりして上流の状態を記録する
（`101_` の `upstream:` 行）。能動的に起こせないバグほど、この形でしか判定できない ―
`get_npc_employ_price` の `KeyError` は難易度 77 以上の NPC が生まれるのを待つしかなく、
プレイでは踏みに行けない。

- 判定に使わず記録するだけにする。 「落ちなくなった」の一度の観測で MOD が自動的に
  降りると、測り方の側が間違っていたときに黙って穴が開く
- 副作用のある対象には使えない。 `InventoryGrid.place_existing_item` はマスを占有
  するので突けない。ああいう防御的な MOD は発火したら記録する形にしておいて、
  沈黙が続いていること自体を証拠にするしかない

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
