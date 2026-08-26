# MOD 一覧

同梱している MOD の早見表。
1本ずつの説明・設定・困ったときは [MODS.md](MODS.md)、
ローダと GUI の使い方は [README.md](README.md)。

**この表は `tools/list_mods.py` が `mod.json` から組む。**
手で書き換えても次の生成で消える。
直す先は各 MOD の `mod.json` か MODS.md の見出し。

同梱 72 本（基盤 2 / 修正 31 / 追加 17 / 計測 22）。

並びはフォルダ名順。
適用順はこれとは別で、GUI の `順` 列（`load_order.json`）が持つ。

「GUI の名前」は GUI の一覧に出る文字（`mod.json` の名乗り）で、
「何をするか」は MODS.md の見出し。
同じ文字になっている MOD もある。

「設定」は GUI の `設定` 列から変えられる項目の数。
「状態」の空欄は、入れれば普通に効くもの。
`取込済` はゲーム本体がその版で同じ修正を取り込んだので降ろしたもので、
デバッグモードのときだけ読み込まれる（TECH.md §3.2.5）。

---

## 基盤（2本）

ゲームは変えない。他が触る前の素の状態を押さえる。

| フォルダ | GUI の名前 | 何をするか | 設定 | 状態 |
|---|---|---|---|---|
| [`000_recon`](MODS.md#000_recon-ゲームの内部構造を書き出す) | モジュール調査 | ゲームの内部構造を書き出す | 1 |  |
| [`001_crash_recorder`](MODS.md#001_crash_recorder-クラッシュの全文を残す) | クラッシュ記録 | クラッシュの全文を残す | - |  |

---

## 修正（31本）

ゲームのバグ・不便を直す。

| フォルダ | GUI の名前 | 何をするか | 設定 | 状態 |
|---|---|---|---|---|
| [`100_fix_kivy_shutdown`](MODS.md#100_fix_kivy_shutdown-終了時に落ちるのを防ぐ) | 終了時クラッシュの修正 | 終了時に落ちるのを防ぐ | - |  |
| [`101_fix_npc_employ_price`](MODS.md#101_fix_npc_employ_price-npc-を雇うと落ちるのを直す) | NPC雇用価格の修正 | NPC を雇うと落ちるのを直す | - | 取込済 main_024 |
| [`102_fix_prompt_dedup`](MODS.md#102_fix_prompt_dedup-llm-へ送る文章の重複を畳む) | プロンプト重複の除去 | LLM へ送る文章の重複を畳む | - | 取込済 main_023 |
| [`103_fix_eventlog_trim`](MODS.md#103_fix_eventlog_trim-llm-へ送るイベントログを刈り込む) | イベントログの刈り込み | LLM へ送るイベントログを刈り込む | - | 取込済 main_024 |
| [`104_balance_area_bgm`](MODS.md#104_balance_area_bgm-新しいエリアのbgmの偏りを均す) | エリアBGMの均し | 新しいエリアのBGMの偏りを均す | 1 |  |
| [`105_fix_schema_compact`](MODS.md#105_fix_schema_compact-llm-へ送るスキーマ説明を圧縮する) | スキーマ説明の圧縮 | LLM へ送るスキーマ説明を圧縮する | - |  |
| [`106_fix_battle_bgm_restore`](MODS.md#106_fix_battle_bgm_restore-戦闘後にbgmが戻らないのを直す) | 戦闘BGMの復帰 | 戦闘後にBGMが戻らないのを直す | 1 | 取込済 main_024 |
| [`107_fix_battle_flag_stuck`](MODS.md#107_fix_battle_flag_stuck-ロードすると戦闘bgmで始まるのを直す) | 戦闘フラグの修正 | ロードすると戦闘BGMで始まるのを直す | - | 取込済 main_024 |
| [`108_fix_shop_inventory_overflow`](MODS.md#108_fix_shop_inventory_overflow-売買画面を開くと落ちるのを直す) | 売買画面クラッシュの修正 | 売買画面を開くと落ちるのを直す | - | 取込済 main_024 |
| [`109_fix_item_detail_autosize`](MODS.md#109_fix_item_detail_autosize-アイテムの説明が途中で切れるのを直す) | アイテム説明欄の拡張 | アイテムの説明が途中で切れるのを直す | 1 |  |
| [`110_fix_character_name_path`](MODS.md#110_fix_character_name_path-名前のせいでnpcの画像が作れないのを直す) | キャラクタ名の正規化 | 名前のせいでNPCの画像が作れないのを直す | - | 取込済 main_024 |
| [`111_llm_prompt_replace`](MODS.md#111_llm_prompt_replace-llm-への指示文を置換ルールで書き換える) | LLM への指示文を置換 | LLM への指示文を置換ルールで書き換える | 2 |  |
| [`112_ui_text_spacing`](MODS.md#112_ui_text_spacing-広すぎる本文の行間を詰める) | 本文の行間を詰める | 広すぎる本文の行間を詰める | 2 |  |
| [`113_ui_text_expand`](MODS.md#113_ui_text_expand-本文の表示域をボタンで広げる) | 本文の表示域を広げる | 本文の表示域をボタンで広げる | 10 |  |
| [`114_ui_input_focus`](MODS.md#114_ui_input_focus-自由入力のあと入力欄にフォーカスを戻す) | 入力欄のフォーカスを保つ | 自由入力のあと入力欄にフォーカスを戻す | 3 |  |
| [`115_ui_item_list_fit`](MODS.md#115_ui_item_list_fit-はみ出すアイテム一覧を画面内に収める) | アイテム一覧を収める | はみ出すアイテム一覧を画面内に収める | 3 |  |
| [`116_ui_party_expand`](MODS.md#116_ui_party_expand-4人目以降の仲間も表示する) | 4人目以降を表示 | 4人目以降の仲間も表示する | 12 |  |
| [`117_message_text_integrity`](MODS.md#117_message_text_integrity-長い応答が途中で切れるのを直す) | 長い本文をより多く見せる | 長い応答が途中で切れるのを直す | 1 |  |
| [`118_batch_message_render`](MODS.md#118_batch_message_render-本文の出し方逐次一括と既読の色を選ぶ) | メッセージの表示速度と文字色 | 本文の出し方（逐次／一括）と既読の色を選ぶ | 4 |  |
| [`119_fix_crime_attribution`](MODS.md#119_fix_crime_attribution-他人の犯罪が主人公のものになるのを直す) | 犯罪主体の誤帰属修正 | 他人の犯罪が主人公のものになるのを直す | - |  |
| [`120_fix_npc_name_collision`](MODS.md#120_fix_npc_name_collision-npc-の名前が重複するのを直す) | NPC名の重複防止 | NPC の名前が重複するのを直す | 5 |  |
| [`121_ui_character_sheet`](MODS.md#121_ui_character_sheet-人物欄に手配度スキル特性を出す) | 人物欄を広げて手配度・スキル・特性を出す | 人物欄に手配度・スキル・特性を出す | 5 |  |
| [`122_ui_conversation_log`](MODS.md#122_ui_conversation_log-流れた本文を後から読み返す) | 会話ログのビューア | 流れた本文を後から読み返す | 11 |  |
| [`123_fix_new_character_level`](MODS.md#123_fix_new_character_level-新規キャラがレベル60で始まるのを直す) | 新規キャラをレベル1で始める | 新規キャラがレベル60で始まるのを直す | 1 | 取込済 main_025 |
| [`124_ui_craft_window_fit`](MODS.md#124_ui_craft_window_fit-クラフト画面の枠の重なりを直す) | クラフト画面の枠の重なりを直す | クラフト画面の枠の重なりを直す | 3 |  |
| [`125_balance_charisma_impression`](MODS.md#125_balance_charisma_impression-魅力が高いと初対面から全員に好かれるのを直す) | 魅力が高いと初対面から全員に好かれるのを直す | 魅力が高いと初対面から全員に好かれるのを直す | 3 |  |
| [`126_ui_title_version`](MODS.md#126_ui_title_version-タイトル画面にローダの版を出す) | タイトル画面にローダの版を出す | タイトル画面にローダの版を出す | 4 |  |
| [`127_llm_response_speed`](MODS.md#127_llm_response_speed-ローカル-llm-の応答を速くする) | ローカルLLMの応答を速くする | ローカル LLM の応答を速くする | 4 |  |
| [`128_item_image_variety`](MODS.md#128_item_image_variety-アイテム画像の偏りを均す) | アイテム画像の均し | アイテム画像の偏りを均す | 4 |  |
| [`129_balance_item_price`](MODS.md#129_balance_item_price-アイテムの値段を付け直す) | アイテムの値付けの調整 | アイテムの値段を付け直す | 19 |  |
| [`130_currency_unit`](MODS.md#130_currency_unit-通貨の呼び名と所持金の表示を変える) | 通貨の表記を変更する | 通貨の呼び名と所持金の表示を変える | 5 |  |

---

## 追加（17本）

ゲームに無かった遊びを足す。

| フォルダ | GUI の名前 | 何をするか | 設定 | 状態 |
|---|---|---|---|---|
| [`300_event_facility_arrival`](MODS.md#300_event_facility_arrival-施設でnpcから話しかけてくる) | 施設でNPCが話しかける | 施設でNPCから話しかけてくる | 12 |  |
| [`301_quest_from_conversation`](MODS.md#301_quest_from_conversation-会話から依頼を受けられる) | 会話から依頼を受ける | 会話から依頼を受けられる | 9 |  |
| [`302_leave_party_in_conversation`](MODS.md#302_leave_party_in_conversation-会話から仲間と別れられる) | 会話で仲間と別れる | 会話から仲間と別れられる | 1 |  |
| [`303_quest_end_party_to_guild`](MODS.md#303_quest_end_party_to_guild-解散した仲間を町のギルドに残す) | 解散先をいまの町に | 解散した仲間を町のギルドに残す | 2 |  |
| [`304_quest_end_keep_party`](MODS.md#304_quest_end_keep_party-クエストをクリアしても解散しない) | クエスト後も解散しない | クエストをクリアしても解散しない | 2 |  |
| [`306_party_train_exp`](MODS.md#306_party_train_exp-宿屋の訓練で仲間も育つ) | 仲間も訓練で育つ | 宿屋の訓練で仲間も育つ | 6 |  |
| [`307_area_move_dungeon`](MODS.md#307_area_move_dungeon-第3の移動手段危険な道を行くを足す) | 危険な道を行く | 第3の移動手段「危険な道を行く」を足す | 8 |  |
| [`308_battle_damage_display`](MODS.md#308_battle_damage_display-戦闘のダメージ表示) | 戦闘のダメージ表示 | 戦闘のダメージ表示 | 7 |  |
| [`309_office_pardon`](MODS.md#309_office_pardon-役場で罰金を納めて手配を解く) | 役場で手配を解く | 役場で罰金を納めて手配を解く | 4 |  |
| [`311_npc_profile_memory`](MODS.md#311_npc_profile_memory-npcが会話の内容を覚える) | NPCが会話を覚える | NPCが会話の内容を覚える | 5 |  |
| [`312_shop_restock`](MODS.md#312_shop_restock-日数経過で店の在庫を更新) | 店の品揃えの入れ替え | 日数経過で店の在庫を更新 | 2 |  |
| [`313_event_ability_check`](MODS.md#313_event_ability_check-行動の成否判定に能力値を効かせる) | 行動判定に能力値を効かせる | 行動の成否判定に能力値を効かせる | 9 |  |
| [`314_area_move_custom`](MODS.md#314_area_move_custom-エリア移動の日数料金文言を変える) | 街移動のカスタマイズ | エリア移動の日数・料金・文言を変える | 10 |  |
| [`315_vacation_custom`](MODS.md#315_vacation_custom-宿の宿泊期間部屋宿代を変える) | 宿泊のカスタマイズ | 宿の宿泊期間・部屋・宿代を変える | 14 |  |
| [`316_bounty_hunter`](MODS.md#316_bounty_hunter-手配されていると追手が来る) | 賞金稼ぎが襲ってくる | 手配されていると追手が来る | 15 |  |
| [`317_reputation`](MODS.md#317_reputation-評判と二つ名) | 評判と二つ名 | 評判と二つ名 | 8 |  |
| [`318_area_difficulty_growth`](MODS.md#318_area_difficulty_growth-土地が育つ依頼の難易度が上がる) | 依頼クリアで難易度上昇 | 土地が育つ（依頼の難易度が上がる） | 7 |  |

---

## 計測（22本）

ゲームは変えない。`out\` にログを残すだけ。デバッグモードのときだけ読み込まれる。

| フォルダ | 何を測るか |
|---|---|
| `200_probe_bug_sites` | 未解決のクラッシュ地点を、失敗時だけでなく毎回計測する |
| `201_probe_missing_attr` | `FreeInputStart` の欠落属性を読んでいる箇所を特定する |
| `202_probe_summarizers` | サマライザ／ファシリテータの引数を測り、空 `Literal[]` の発生源を追う |
| `203_probe_create_model` | pydantic モデル生成の瞬間に空 `Literal[]` を捕らえ、呼び出し元まで記録する |
| `204_probe_prompt_bloat` | EVENTLOG / DEDUP / サイドカー起動という3つのプロンプト肥大化挙動を測る |
| `205_probe_player_events` | プレイヤーの行動をトリガーにしたイベントの差し込み場所を特定する |
| `206_probe_quest_flow` | クエストの受注経路と、選択肢ボタンの登録方法を特定する |
| `207_probe_battle_bgm` | 戦闘BGMの切り替え経路（`play_music_from_src` の呼び出し元）を計測する |
| `208_probe_item_detail` | アイテム説明欄の実寸と中身を写し取る |
| `209_probe_free_facility` | シーン記述エンジンの中身（ステップ・フラグのスコープ・プログラムの出どころ）を写し取り、MOD から使えるかを測る |
| `210_probe_character_state` | NPC の死亡の印を特定し、誰がその NPC を参照しているかを数えて、印だけで安全に退場させられるかを測る |
| `211_probe_text_speed` | 本文の1文字ごとの間隔・`app.text_speed`・フレームレート・ラベルのテクスチャ作り直しの重さを測る |
| `212_probe_character_sheet` | プレイヤーの人物欄の実寸と組み立てを写し取り、載せられる値（手配度・スキル・特性）の在り処を確かめる |
| `213_probe_npc_memory` | ゲーム自身が NPC ごとに覚えるもの（`memory`・`life_log`・`relationship`・`knowledge`）の実体と、それが会話プロンプトへどう載るか、プロンプト内の重複量（`311_` の注入分も含む）を測る |
| `214_probe_new_character` | 新規作成したキャラクタが経験値0のままレベル60で始まる経路を写す |
| `215_probe_event_roll` | クエスト中のミニイベントの成否判定を写す |
| `216_probe_llm_overlap` | LLM リクエストの多重送信をプロセス内で数える（`127_` の `--parallel 1` によるキュー待ちが実プレイでどれだけ起こるか） |
| `217_probe_area_move` | エリア移動の未実測部分を録る |
| `218_probe_vacation` | 宿の宿泊の未実測部分を録る |
| `219_probe_crash_log` | 本体のクラッシュ記録が落ちる呼び出しを見分ける |
| `220_probe_bounty_hunter` | 手配度に応じて追手を出す MOD（`316_`）を書くための下調べ |
| `221_probe_item_level` | 品物のレベルを誰が決めているかを録る |
