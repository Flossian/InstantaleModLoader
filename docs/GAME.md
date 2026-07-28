# GAME — Instantale 内部リファレンス

**実機で確かめた「ゲームがどう動いているか」**。MOD を書くときに必要な、Instantale
そのものの構造・語彙・作法をここに集める。

- ローダの仕組みと MOD の書き方は [TECH.md](TECH.md)
- 遊ぶだけなら [README.md](README.md)
- 各 MOD の検証状況・未確認項目・実測ログは [VERIFICATION.md](VERIFICATION.md)

TECH.md と分けているのは、読む理由が違うから。あちらは**このローダで MOD をどう書くか**
（他のゲームにも通じる話）で、こちらは**Instantale が何をしているか**（このゲーム限定の
事実）。ゲームが更新されて食い違うのはこちら側だけなので、疑う場所が1つに寄る。

> ここに書いてあるのは**すべて実測**で、ソースは読めない（Nuitka standalone）。
> 推測は書かない。確かめていないことは VERIFICATION.md の未確認項目に置く。

---

## 1. パッチ対象の見つけ方

### 1.1 リコン成果物 (`out/recon/`)

**ソースが読めない以上、正確なパッチ対象名はここからしか得られない。**

| ファイル | 内容 |
|---|---|
| `targets.txt` | `module:qualname(signature)` 形式。`@patch` にそのまま貼れる（1466件） |
| `game_modules.txt` | ゲーム自身のモジュールの全属性ダンプ（擬似ソース一覧） |
| `modules.json` | 全モジュールの機械可読インベントリ |
| `summary.txt` | 環境・`sys.path`・モジュール census |
| `bug_sites.txt` | crash_log.txt の各クラッシュ地点のプローブ + キーワード掃引 |

### 1.2 ゲーム自身のモジュール

```
__main__                       instantale.py, 約10,600行, 516ターゲット
scripts                        scripts.hud.* / scripts.llm.* / items / functions ほか
Embedding, image_generation, llama_cpp_runtime_completion, sidecar_process
save_area_json, save_world_json, api_key_manager, build_type, sdcpp_cuda
```

**`__main__` は `sys.stdlib_module_names` に含まれる。** 素朴に stdlib を除外すると
ゲーム本体が丸ごと漏れる（`recon.py` の `GAME_TOPLEVEL` はアローリスト）。

### 1.3 掃引で見つからないもの

- **ネスト関数はモジュールのグローバルに現れない。** `send_request_on_id` はトレース
  バックに 62 回出るが `vars(module)` には無い（`send_request` 内の `backoff` デコレータ
  付きネスト関数）。実際の対象は外側の `send_request` / `send_request_with_no_structure`
- **クラスのメソッドはモジュールレベルのキーワード掃引で 0 件に見える**
  （`set_ai_models` / `show_world_choice` など）。`game_modules.txt` を見る
- **属性名を推測して探すと空振りする。** `vars(obj)` を一度全部出すほうが速い
  （HUD の描画先を `texts` / `labels` という名前で探して見つからなかった実例がある。
  正解は `hud.buttons[i].text`。§2.3）

### 1.4 環境の基本値

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

---

## 2. ゲーム内部リファレンス

### 2.1 スレッド

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

### 2.2 選択肢ボタン

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

### 2.3 選択肢を変える手順

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

### 2.4 待機表示（「…」のアニメーション）

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

### 2.5 会話

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

### 2.6 割り込みのタイミング

移動・クエスト終了・会話終了の後始末（テキストの流し込み・ボタンの張り替え・要約）の
最中に割り込むと噛み合わない。`ui.IDLE_SIGNALS` = `is_adding_text` / `is_button_enabled` /
`is_popup_window_opened` を Clock で見張り、手が空いてから実行する（`ui.Screen.when_idle`）。

戦闘・会話中かを見るフラグ: `in_battle` / `in_boss_battle` / `in_colosseum_battle` /
`in_conversation` / `in_free_input` / `in_action_in_conversation`。

**`in_shopping` は状態の判定に使えない。** 店の外を往復しているだけの移動でも True の
まま残る。買い物窓が開いているかは `is_popup_window_opened` で見る。**`in_battle` も経路に
よって下ろし忘れがある**（§2.10）。

> **フラグ名が意味するとおりに動いているとは限らない。** 条件に使う前に実測で裏を取ること。

### 2.7 世界のデータ構造

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

### 2.8 パーティ

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

### 2.9 クエスト

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

### 2.10 戦闘・フラグ

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

### 2.11 BGM

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

### 2.12 LLM 経路とプロンプト

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

### 2.13 インベントリのグリッド

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

### 2.15 キャラクタ名はそのままファイルパスになる

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
  手を入れずに一致する。引数ではなく `self.name`（`__init__` を抜けた後）を正すと、
  名前をどの引数から組み立てているかを知らずに済む
- Windows は**末尾の空白とピリオドを黙って切る**ので、パスに使うなら先に落としておく
- **世界名（`worlds/<世界>/`）の入口は未調査。** 同じ壊れ方をしうる

### 2.16 セーブ

```python
plaintext  = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
cipher[i]  = plaintext[i] ^ b"Instantale_Save_Key_2026"[i % 24]
```

`savedata.json` も同じ方式。セーブを書き換えるツールは、**書き込み前に毎回
復号→再暗号化のラウンドトリップを検査し、一致しなければ拒否する**こと。

---

---

## 3. 調査手法

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
