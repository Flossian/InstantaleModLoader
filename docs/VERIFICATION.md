# VERIFICATION — 検証記録と現在地

最終更新: 2026-07-28

**何がどこまで確かめられているか**の記録。

- ここにあるのはこれまでの**検証結果**（実測値・ログの抜粋・件数）と**未確認項目の確認手順**
- 「なぜそう実装したか」は TECH.md 側。

---

## 1. 一覧

### 修正（100番台）

| mod | 内容 | 状態 | 根拠 |
|---|---|---|---|
| `100_fix_kivy_shutdown` | Kivy終了時 `ctypes.ArgumentError`（crash_log 47件・最多） | **決着** | §2.1（2026-07-25。実際に発火し、クラッシュにならなかった） |
| `101_fix_npc_employ_price` | `KeyError: 80`（雇用価格の定義域外） | **解決** | §2.2（実行時の総当たりで確定） |
| `102_fix_prompt_dedup` | DEDUP | **実経路で検証済** | §2.3 |
| `103_fix_eventlog_trim` | EVENTLOG | **実経路で検証済** | §2.3 |
| `104_balance_area_bgm` | エリアBGMの偏り是正 | オフライン検証済・**ゲーム内未確認** | §2.4 / §3.4 |
| `105_fix_schema_compact` | COMPACT | **実機で検証済**（33件 73.5%減） | §2.3 |
| `106_fix_battle_bgm_restore` | 戦闘後にBGMが戻らない | **決着**（原因を行・型まで確定。3つの起点すべてが実機で発火し、終了後 1/8 チャンネル） | §2.5 |
| `107_fix_battle_flag_stuck` | 戦闘後も `in_battle` が 1 のまま残る | **決着**（原因確定 8/8 対 9/9 の非対称・注入時の掃除・**戦闘終了時の発火**まで実機確認。2026-07-28）。ロード時の発火のみ未観測 | §2.5 / §2.12 / §3.1 |
| `108_fix_shop_inventory_overflow` | 売買画面を開くと `IndexError` で落ちる | **修正投入済**（原因はクラッシュ全文から確定）。救済経路は**まだ一度も発火していない**（正常時の寸法 104 件のみ） | §2.16 / §3.8 |
| `109_fix_item_detail_autosize` | アイテム説明欄が固定サイズで長い説明・名前が切れる | **実機で発火**（箱が 500 → 最大 1300 まで伸びている）。横幅の拡張は未観測 | §2.17 |
| `110_fix_character_name_path` | 名前の `"` でキャラクタ画像が生成できない（`OSError: [WinError 123]`） | **決着**（`id='101'` の改名・画像8点の生成・`WinError 123` が増えないことまで実機確認。2026-07-28） | §2.14 / §2.15 |

### 機能追加（300番台）

| mod | 内容 | 状態 | オフライン検証 |
|---|---|---|---|
| `300_event_facility_arrival` | 施設到着時にNPCが話しかけてくる | **実機確認済**（両モード） | 39件全通 |
| `301_quest_from_conversation` | 会話から依頼を受注/生成 | **実機確認済**（設置・生成・受注・掲示板の絞り込み・HUD塗り替えまで。2026-07-28・§2.13） | 33件中32件。残る1件はハーネスの人工物と判明（§3.2.1） |
| `302_leave_party_in_conversation` | 仲間と会話から別れる | **実機確認済**（一部経路が未実測。§3.3） | 65件全通 |
| `303_quest_end_party_to_guild` | クエスト解散で町のギルドに残す | **実機未確認**（§3.3） | 45件全通 |
| `304_quest_end_keep_party` | クエストクリアで解散しない（`303_` より外側） | **実機未確認**（§3.3） | 50件全通 |
| `305_mini_quest` | 戦闘を伴わないミニクエスト（採集・救助・偵察） | **実機4回**。依頼の中身・非戦闘の進行・イベント主体の道中まで成立。残るのは**達成としての帰還**だけ（4回とも `return_after_completion` が選ばれず、文面では2回外した。戻り値を差し替える形に切り替えて投入済み・再確認待ち。§2.19〜§2.22 / §3.10） | 94件全通。うち4件は `output_data/` の実プロンプト 612件との突き合わせ |

### ローダ

| 項目 | 状態 | 根拠 |
|---|---|---|
| 世代管理（再注入で層が積み重ならない） | 検証済 | 再注入で層が積み上がらないことを確認 |
| 遅延 import の保留＋当て直し | オフライン16項目全通・**実機での流れは未確認** | §2.7 / §3.5 |

### 計測待機中（発生すれば自動で原因が確定する）

| バグ | 状態 |
|---|---|
| `AttributeError: 'FreeInputStart' object has no attribute 'facility_move_to'`（2件） | `201_` のトリップワイヤが待機中。**発火ゼロ・ノイズゼロ**。実呼び出し5回とも属性は無いまま正常終了 |
| `AssertionError: literal "expected" cannot be empty, typing.Literal[]`（2件） | `203_` が `create_model` をラップ。**`Literal[0]` は全期間で0件**。2026-07-28 にクエスト1件を頭から終わりまで通し、`Literal` が消費に応じて減っていく様子と、**候補が尽きたモデルはゲームが作らない**ことを実測（§2.18）。第一容疑だった「敵候補0件」は**弱まった**が、0件を観測できたのは `FieldEvent` だけで `Battle` / `EncounterFinalBoss` では未観測 |

`create_model` の alias は5モジュールに複製されている（`pydantic.main` /
`llm_manager_world_generate` / `llm_manager_character_create` / `save_world_json` /
`llm_manager_battle`）。全て再束縛済み。

### 未修正（原因確定済み・実装は別セッション）

| バグ | 状態 |
|---|---|
| （無し） | `OSError: [WinError 123]` は `110_fix_character_name_path` で**決着**（実装 §2.14 / 実機 §2.15） |

### 対象外（ユーザー判断）

| 件数 | 内容 | 理由 |
|---|---|---|
| 35 | `RuntimeError: 同梱llama-serverが起動しませんでした` | 動作確認中の操作に起因。ゲームのバグではない |
| 40 | LLM系（`KeyError: 'timings'` / `response_format` / 接続リセット） | 同上 |
| 6 | `KeyError: '52'` `'53'` `'109'`（str キー） | 過去の手動データ作成時の不整合 |

crash_log.txt の 114 件からこれらを除くと、**実バグは 12 件 / 4 種**だった。

---

## 2. 実機・実データでの検証記録

### 2.1 Kivy 終了時クラッシュ（2026-07-25、決着）

前回は「クリーン終了したがログが無く、効いたのか踏まなかったのか不明」だった。
呼び出しレベルの計測を追加して再度終了操作を行い、**今度は実際に発火した**:

```
[17:32:15.545] INFO  SetWindowLong_WndProc_wrapper called: hWnd=None (NoneType) wndProc=<WinFunctionType object at 0x...5F0>
[17:32:15.545] WARN  SetWindowLong_WndProc_wrapper failed: ArgumentError: argument 3: TypeError: wrong type
[17:32:15.546] WARN    no usable WndProc address; skipping restore (window is being destroyed anyway)
  （同じ組が wm_touch 側でもう1回）
```

判定に使った4指標（全て一致）:

| 指標 | ベースライン | 終了後 | 判定 |
|---|---|---|---|
| `crash_log.txt` サイズ | 173,055 bytes | 173,055 bytes | 増えていない |
| `ctypes.ArgumentError` 件数 | 47 | 47 | 増えていない |
| `out/live_crashes.log` | 未作成 | 未作成 | `001_` が何も記録せず |
| ガードの発火 | — | 2 回（`wm_pen` / `wm_touch`） | **バグ自体は起きた** |

「起きたが、クラッシュにはならなかった」が揃ったので**効果が確定**。
判明した真の原因と、`alias_scan` が無ければ素通りしていた件は TECH.md §4.1。

以後、他の例外が上書きされずに `live_crashes.log` へ残るようになった。

### 2.2 `KeyError: 80`（再現を待たずに確定）

稼働中プロセスを直接叩いた:

```
get_npc_employ_price(0..150)  -> 0..76 が成功（連続）、77 以上は KeyError(level)
                                 80 は報告どおりのクラッシュを再現
clamp_npc_difficulty_value(v) -> [0, 76] にクランプ
scripts.functions             -> NPC_DIFFICULTY_VALUE_MIN / _MAX を定義
```

修正後: `get_npc_employ_price(0..200)` が例外を出さなくなり、76 以上は一律 5045。

**残っている疑問**: そもそも難易度 80 の NPC がどこで生まれるのか。クランプ発生時の
ログがその頻度を示す。

### 2.3 プロンプト肥大化対策

**実経路での発火**（DEDUP / EVENTLOG）:

```
[EVENTLOG] quest_referee_event_evaluate_new: dropped 5 turn(s), kept 3 | 2005 -> 620 chars (saved 1385)
[EVENTLOG] quest_referee_event_resolve:      dropped 5 turn(s), kept 3 | 2115 -> 730 chars (saved 1385)
[DEDUP] removed 1 duplicate block(s) ['idx1 system 8250c'] | 4 -> 3 msgs, 20248 -> 11998 chars (saved 8250)
[DEDUP] removed 2 duplicate block(s) ['idx1 system 8250c', 'idx2 system 8250c'] | 5 -> 3 msgs, 28498 -> 11998 chars (saved 16500)
```

スキーマブロックは**最大3コピーまで増殖**することを確認。EVENTLOG は監査出力
（before/after の先頭・末尾）でターン境界と前置きの扱いが正しいことを目視確認済み
（前置きが空文字列のケースで先頭の区切りが正しく再現されている）。

クエスト序盤で既に蓄積が始まっている:

```
quest_referee_event_evaluate_new: quest_event_log str chars=1699 turns=7
quest_referee_event_resolve:      quest_event_log str chars=1813 turns=7
```

**COMPACT のオフライン検証**（`<ゲームdir>/output_data/` の 12,067 件 ＝ ゲーム自身が
保存した `messages` を mod の関数に通した結果。66 マネージャ種 / 46,931 メッセージ）:

| 項目 | 結果 |
|---|---|
| 埋め込みスキーマを検出 | 7,967 件（`$defs` 有り 4,097 / 無し 3,870） |
| 解析失敗 | **0 件** |
| 誤爆（`title`+`type:'object'` でない dict を掴んだ） | **0 件** |
| フィールド名 / enum 値の欠落 | **0 種 / 0 種** |
| 2回通すと結果が変わる / 1メッセージに2個目のスキーマ | **0 件 / 0 件** |
| 合計 | 16,508,011 → 4,529,299 文字（**72.6% 減**） |

削減率は 56%（`create_look`）〜79%（`vacation_scene_generator`）。TECH.md の
「平均約4割」（プロキシ側の記録）より大きいのは分母の違い（あちらはリクエスト本文全体、
こちらはスキーマを含むメッセージ）。

**COMPACT の実機検証**（2026-07-25、pid 10744 の実プレイ 33 リクエスト）:

```
合計 67,475 -> 17,870 文字（削減 49,605 / 73.5%）  オフライン実測 72.6% と一致
最大 6,974 -> 1,869（クエスト系）  最小 182 -> 51
発火した site: chat 33 / payload 0
```

> **`payload` 側は1件も発火しなかった。** ストリーミング経路は
> `_post_with_model_loading_retry` を通らない。プロキシと同位置（payload）だけに移植して
> いたら**何も起きないまま「移植した」と報告するところだった**。保険として足した `chat`
> 側が結果的に本命だった。

監査ログ（`out/prompt_bloat.log`、最初の5回のみ全文）に実スキーマが残っている:

```
Skill: name, description, element, skill_type:∈{physical,magical,hybrid,other},
       effects:InstantDamage|InstantHeal|TextStatusEffect|BuffEffect|DebuffEffect[], max_uses...
```

### 2.4 BGM 偏り是正（実セーブ・実アセットに対して）

外部 41 件＋mod 内蔵 18 件、全通過:

- 全エリアで **`folder == SIZE_ALIAS[area["size"]]`** を満たす・全曲がディスク上に実在
- 無音エリアは `size` の示すフォルダに割り当てられる・2回通しても結果が変わらない
- 曲の使用回数のばらつきは全カテゴリで 1 以内
- **各世界の全エリアが異なる曲を得る**（peld: 22→37、vestia: 22→37、dos: 23→33）
- 3世界合計の到達曲数 47/97 → 57/97、到達 mood 21/37 → 23/37
- シミュレート 300 世界で town の 9 mood 全てに到達（締め出しゼロ）
- Astergrave（無音5エリア）の dry-run で 12→20 曲、town 1→3 mood

**セーブ難読化のラウンドトリップ**: ライブ5世界すべてで復号→再暗号化がバイト単位で一致。
`tools/rebalance_saved_bgm.py` は書き込み前に毎回これを検査し、一致しなければ拒否する。

**ゲーム内実動作は未確認**（§3.4）。

### 2.5 戦闘BGM（原因確定と注入時掃除）

`207_` の計測、実プレイ12戦で原因が確定した（詳細は GAME.md §2.11）。決め手になったログ:

```
戦闘開始 instantale.py:6957  play_music_from_src(battle) .music before=<Sound 5B0> after=<Sound 850>
戦闘終了 instantale.py:7993  play_music_from_src(area)   .music before=<missing>          ← ★
        instantale.py:7339  stop_music()                .music =<Sound 850>（戦闘曲）
次の戦闘 instantale.py:6963  stop_music()                .music =<Sound 850>（まだ戦闘曲）
```

12戦の後、町に立っているだけでこうなっていた:

```
mixer     = 8/8 channel(s) busy: [0,1,2,3,4,5,6,7]
app.music = Sound#232d25b5350 playing_on=0    ← 本来の曲は鳴らず、迷子だけが鳴っている
```

**注入時の掃除が、その場でこれを直した**（2026-07-26）:

```
[BGMFIX] sweep after injection: stopped stray track on channel 1; ... channel 7;
         restarted solemn/Ambient 7 Loop.mp3 on the app
mixer 8/8 busy -> 1/8 busy: [0]      app.music playing_on=1
```

**渡されている物の正体も確定した**（2026-07-27、自由会話からの戦闘1回）。`207_` に型と
id を出させたところ、`BattleEndInFreeAction` のインスタンスだった:

```
play_music_from_src('town/solemn/Ambient 7 Loop.mp3')
    target = BattleEndInFreeAction#21c9ee5bdc0 NOT-THE-APP
    caller = instantale.py:7993 <lambda>
[BGMFIX] orphan: solemn/Ambient 7 Loop.mp3 was attached to BattleEndInFreeAction
         instead of the app -- nothing can stop it now
```

この戦闘では**ユーザー確認で BGM は元に戻った**が、`sweep after ...` は1行も出ていない
＝ **後始末は走っていなかった**。ログを追うと `in_battle` が戦闘終了後も 1 のままで、
「戦闘中なら何もしない」の条件に引っかかって毎回黙って降りていた（GAME.md §2.10）。
戦闘終了フックからの予約を `trust_end`（フラグを見ない）に変更した。

> **「聞いて正常」は合格条件にならなかった。** このときゲーム側が迷子として鳴らした曲が
> たまたま正しく聞こえていただけで、チャンネルは 2/8 に増えていた。判定は耳ではなく
> `mixer = n/8` で行うこと。

**戦闘終了時の発火を確認**（2026-07-27、`trust_end` 修正後の戦闘1回）:

```
11:20:32.692 play_music_from_src('town/solemn/Ambient 7 Loop.mp3')
             target = BattleEndInFreeAction#21dbad26bc0 NOT-THE-APP
             caller = instantale.py:7993 <lambda>
11:20:32.881 [BGMFIX] orphan: ... nothing can stop it now
             mixer  = 1/8 channel(s) busy: [0]
11:20:35.179 [BGMFIX] sweep after BattleEndInFreeAction.end_phase:
             handed solemn/Ambient 7 Loop.mp3 back to the app (channel 0)
```

**3つの起点すべてが実機で発火した**（注入時の掃除 / 保険の
`sweep after refresh_choice_buttons: stopped stray track on channel 1` / 戦闘終了）。
終了後のチャンネルは 1/8 で、その1本は app が握っている。

**壊れているのは1経路だけだと確定した。** 同じセッションの通常戦闘では:

```
BattleEndManager.end_phase done
play_music_from_src('dungeons/mystic/melt.wav')
    target = InstantaleApp#21cf30c7a70 IS-APP        ← 正しく app を渡している
    caller = instantale.py:7958 <lambda>
```

`BattleEndManager`（通常）は `:7958` で正しく、`BattleEndInFreeAction`（自由入力・
会話から）は `:7993` で誤っている。**ユーザー報告が「会話から入った戦闘」に限られて
いた理由がこれで説明できる。**

ユーザーが報告した派生症状のうち、タイトル画面のものは迷子で説明がつく:

| 症状 | 説明 |
|---|---|
| タイトルに戻っても街のBGMが鳴り続ける（本来は無音） | `return_to_title` の `stop_music(app)` は `app.music` しか止めない。迷子は残る |

### 2.6 ロードすると戦闘BGMで始まる（`in_battle` の下ろし忘れ）

**迷子とは別のバグだった**（2026-07-27。「まだゲームをロードしたら戦闘BGMが流れる」の
報告を受けて計測）。ロード処理が `in_battle` を見て曲を選んでいる:

```
13:42:16 play_music_from_src('musics/battle/1. Echoes of Valhalla.mp3')
         target = InstantaleApp IS-APP        ← 迷子ではない。app に正しく付いている
         flags  = in_battle=1
         caller = instantale.py:1458 <lambda>
（比較）  play_music_from_src('town/solemn/Ambient 7 Loop.mp3')
         flags  = in_battle=0
         caller = instantale.py:1460 <lambda>   ← 隣の行。if/else の反対側
```

そのフラグが 1 のままなのは、**`106_` と同じマネージャの、同じ種類の書き忘れ**:

| 終了マネージャ | `end_phase` 完了時の `in_battle` | 件数 |
|---|---|---|
| `BattleEndManager`（通常の戦闘） | **0**（ゲーム自身が下ろしている） | 8/8 |
| `BattleEndInFreeAction`（自由入力・会話から） | **1**（下ろし忘れ） | 9/9 |

`207_` のログ全件を数えた結果で、例外は無い。戦闘後に保存すると `in_battle=True` が
セーブに焼かれ、次のロードが戦闘曲の枝を引く。

**戦闘の実体の有無は `app.current_enemy_dict` で判定できる**（残骸のとき `len=0`。
`combat_log` は前の戦闘の本文が残るので使えない）。実測:

```
flags                  = in_battle=1
app.current_enemy_dict = dict(len=0, keytypes=-) keys=[]     ← 敵は居ない ＝ 残骸
```

**注入時の掃除が、その場でこれを直した**（`107_` がフラグ、`106_` が鳴っている曲）:

```
[FLAGFIX] injection: cleared in_battle (the game left it set)
[BGMFIX]  injection: in_battle was set with no enemies -- replaced the playing track
          with solemn/Ambient 7 Loop.mp3
→ flags = in_battle=0   mixer = 1/8 channel(s) busy: [0]
```

**未確認**: 戦闘終了時とロード時の発火（§3.1）。

### 2.7 遅延 import の保留＋当て直し（オフライン16項目全通）

後から現れるモジュールを狙う mod を用意し、以下を確認:

- `apply-error` にならず保留されること
- 現れた時点で当て直されて実際にフックが効くこと
- 層が積み上がらないこと（`__original__` の連鎖が常に1段）
- 監視が暴走しないこと
- **属性名の間違いは従来どおりエラーになること**

> **テストで identity 比較を使ってはいけない**（`alias_scan` がテスト側の握っている
> グローバルまで張り替える）。TECH.md §4.1。

### 2.8 施設到着イベント（両モードとも実機で発火）

**narration モード**（宿屋・雑貨屋・闇市の3件）:

```
fire: 睡蓮の揺り籠 (Lotus Cradle) (inn) roll 0.01 < 1.00 speaker='マナ'
generated in 0.4s: '「あら、いらっしゃい。こんな場所まで、何をお探しなの？」'
```

**conversation モード**（2026-07-26）。6回発火し、6回とも `conversation_starter` まで
到達した（＝会話フェーズが実際に始まった）:

```
fire: 影の取引所 (Shadow Exchange) (underworld_office) roll 0.15 < 1.00 speaker='謎の女・ミラ' id='69'
launch: process_choice(ConversationStartManager, '謎の女・ミラ') npc_id='69'
rephrase: '<行動: 話しかける>' -> '<状況: 影の取引所に入ってきたヴァンに、あなたの方から声をかけた…>'
```

確認が済んだので `CHANCE_OVERRIDE` は `None`（施設種別ごとの確率）に戻してある。
残るのは運用感の調整だけ（施設別の発生率と `COOLDOWN_MOVES` を実プレイの頻度に合わせる）。

**この確認の副産物**（どちらも他の mod の土台になった）:

- **スレッドの扱いが確定**（`process_choice` はメインスレッド、`execute` は別スレッド）
- **`in_shopping` が当てにならないことが判明**（素の移動38回すべてで True。不発39件のうち
  38件がこれだった）。GAME.md §2.6

### 2.9 仲間と別れる（実機確認済み・4回外して到達）

ボタンの設置・確認画面の表示更新・別れの実行・初期位置への再配置まで実機で確認済み
（2026-07-26）。外した4点と、そこから確定した事実:

| 外した点 | 実機のログ | 確定したこと |
|---|---|---|
| 名簿の在り処 | `add_party_member('83' 'ルナ・エクリプス') -> party=[]` | `app.party` は名簿ではない |
| 名簿の形 | `(no candidate found)` | `list` とは限らない。`dict` も受ける |
| 差し替えの順序 | 押下の2ミリ秒後に refresh、以後 refresh なし | 押下と同じ流れで差し替えると古い画面に戻される |
| 描画の経路 | `update_button_texts(list [...]) <- InstantaleApp` | 塗るのは HUD。`app.to_display_buttons` は監視対象ではない |

いずれも「セーブに出ている形＝実行時の形」と決めつけたのが原因。結論は GAME.md §2.8 / §2.2。

**ゲーム本来の解散経路もこの過程で捕まえた**（`303_` の前提）:

```
remove_party_member('71' '魔導師・リアナ')
  from QuestEndManager.method_1 (instantale.py:6602)
  <- QuestEndManager.execute (instantale.py:6635) <- run (threading.py:953)
remove_party_member: party ['player', '71'] -> ['player']
observed: the game placed '71' at '漣のギルド (Ripple Guild)' after its own removal
```

**ただしこの2件では「初期位置」と「いま居る町のギルド」が同じ場所だった**ため、ログだけ
では規則を区別できなかった。**ゲーム側の規則が `initial_location` であることはユーザーが
実プレイで確認**（2026-07-27。セーブでも `npcs['71'].current_location = '127'`）。

### 2.10 依頼受注の絞り込み（オフラインでライブ世界と照合）

ライブ世界（ドスケベフェスティバル、24依頼）で:

| | 結果 |
|---|---|
| mod の絞り込み（エリア7 = 水底の隠れ家） | `[39, 43, 45]`（依頼 15/16/17） |
| ゲーム自身の `get_quest_difficulties(area, world)` | `[45, 43, 39]` |
| 判定 | **一致** |

mod は実行時にもこの照合を行い、食い違ったら `quest_offer.log` に
`WARN difficulty mismatch` を残す。

**`client_name` は実在 NPC と結び付いていない**ことも判明（ライブ5世界・全114依頼で一致
**0件**）。したがって `FILTER_BY_NPC = True` の既定では、初対面の NPC の一覧は
「この話から依頼を作る」だけになる。GAME.md §2.7。

### 2.11 オフライン検証（ゲーム不要）

```powershell
python tools/test_arrival_event.py    # 300_  39件
python tools/test_quest_offer.py      # 301_  33件
python tools/test_party_leave.py      # 302_  65件
python tools/test_quest_end_guild.py  # 303_  45件
python tools/test_quest_end_keep.py   # 304_  50件
python tools/test_mini_quest.py       # 305_  94件（うち4件は output_data/ の実プロンプトと突き合わせ）
python tools/test_item_detail_autosize.py      # 109_  25件
python tools/test_character_name_sanitize.py   # 110_  34件
```

| tool | 何を通すか |
|---|---|
| `test_item_detail_autosize` | 短い文では設計値のまま1px も変わらないこと / 長い説明で高さが伸びること / 上端を保って下へ伸びること / `pos_hint` の `top` が新しい高さに追従すること / 縦横比を基準にした横の拡張と窓の右端での頭打ち / 設計値の写し取り（ホバーのたびに値が育たないこと・ゲームが箱を組み直したときだけ写し直すこと）/ 伸びた箱を窓の内側へ戻すこと / ラベルが欠けていても触らないこと / 例外を握り潰していないこと |
| `test_character_name_sanitize` | 変換表 / 末尾の空白・ピリオド / 制御文字 / 実データの正しい名前が 1 文字も変わらないこと / 生成時の適用（位置引数・`name=None` を含む）/ 注入時とロード時の救済（名簿が辞書でも配列でも）/ 予約デバイス名と空になる名前に触らないこと / 旧名がログに残ること / **実地の `os.makedirs`** |
| `test_arrival_event` | 会話フェーズの起こし方 / 待ち合わせ / 取り消し / 読み替え / 発火条件 / 整形 / 戻り値の形3種 |
| `test_quest_offer` | 設置位置 / 依頼一覧で入れ子にならないこと / 押下の横取りと素通し / 会話を閉じてから開くこと / `end_text` の差し替え / **押下と同じ流れでは塗らず次のフレームで HUD を塗ること** / 掲示板の絞り込み / ゲーム本来の掲示板を触らないこと / `302_` との印の衝突 |
| `test_party_leave` | 設置条件 / 確認 / 実行 / 置き場所が無い場合 / ゲームが自分で置いた場合 / 名簿が残った場合 / 名簿の在り処が違う場合 / `301_` との印の衝突 |
| `test_quest_end_guild` | 検出 / 置き先 / 差し替えの2層 / ギルドが無い土地 / 時間切れの保険 / 画面 / `302_` との重ね掛け |
| `test_quest_end_keep` | 引き留め（名簿も置き直しも動かないこと）/ 離脱文の差し替え / 死別・放棄・普段の移動の素通り / **本当に外れる相手の置き直しを止めないこと** / 置き先を先に聞くビルド / `303_` との重ね掛け（どちらが勝つか・`303_` が降りない場面） |

偽の `on_button_press` は本物と同じく `getattr(__main__, cls_name)(app, *args)` を組んで
`process_choice` に渡す。**自前ボタンに無害な spec を持たせる意味**（mod 無しで押されても
害の無いクラスが起きる）がそこで確かめられる。

`test_quest_end_guild` は**解散の検出をスタックで行うので、テストも
`QuestEndManager.method_1` の中から呼ぶ形**にしてある（app のメソッドを直接叩くと本番と
違う経路になり、検出そのものを検証できない）。

> **テストのクラスをグローバル名から派生させないこと**（TECH.md §4.3）。
> 派生元は `BASES` の表に控える。

**ローダ全体の読み込み確認**:

```powershell
python -c "import sys; sys.path.insert(0,'runtime'); import instantale_modloader as l; print(l.boot('out/test/bootcheck'))"
```

`boot complete: 28/28 mod(s) applied` が出れば読み込み側は健全（mod を足したら
この数も更新すること。2026-07-28 時点で 28 個）。

### 2.12 `107_` 戦闘終了時の発火（2026-07-28、決着）

§3.1 が「今いちばん見たい行」としていたものが実機で出た。NPC 会話からの戦闘。

```
[00:15:43.703] BattleEndInFreeAction.end_phase(...) start
                   ... in_battle=1 ...                        ← ゲームは下ろしていない
[00:15:45.700] stop_music() target=InstantaleApp IS-APP
[00:15:45.986] [FLAGFIX] BattleEndInFreeAction.end_phase: cleared in_battle (the game left it set)
[00:15:45.991]     ... in_battle=0 ...                        ← 下りた
[00:15:46.213] [BGMFIX] orphan: lively/Tyr Guide You...wav was attached to
                        BattleEndInFreeAction instead of the app
[00:15:48.479] [BGMFIX] sweep after BattleEndInFreeAction.end_phase:
                        handed lively/Tyr Guide You...wav back to the app (channel 1)
```

`in_battle=1` のまま `end_phase` に入り、修正が下ろし、直後に 0 になっている。
BGM の引き取りも成功。§2.5 で確定していた「`BattleEndInFreeAction` だけ下ろし忘れる」
という読みが、修正後の実機で裏付けられた。

**残る留保が1つある。** 直前に `mixer = 2/8 channel(s) busy: [0, 1]` が一度出ており、
2.3秒後の sweep で引き取られたが、**sweep 後の mixer 読み取りがログの末尾に無い**ため
1本に戻ったことは未確認。セッション全体（00:02〜00:15）の読み取りは 0〜1本で、
この 2/8 以外に増加は無いので合格と見ているが、**次の戦闘で 1 本のままなら確定**。

`[FLAGFIX] load_game_new: cleared in_battle`（ロード時）は未観測。残骸入りのセーブを
読まないと出ないので、これは条件が揃っていないだけ。

### 2.13 `301_` 会話からの依頼受注・生成（2026-07-28、実機）

§3.2 の3段階が実機で全部通った。`out/quest_offer.log`。

| 段階 | 結果 |
|---|---|
| ボタン設置 | `added '依頼を受ける（話を切り上げる）' to the conversation menu` |
| 会話からの生成 | `generate: took 37.5s; new quest ids=['31']` / `took 140.1s; new quest ids=['32']` |
| 受注 | `open board: process_choice(DisplayQuestChoice, ...)` |
| 絞り込み | `quest board: filtered for '事務官 ゼノ' -> kept 2, dropped ['2', '29']` |
| **HUD 塗り替え** | `to_display_buttons [...5件...] -> ['甘美なる平原の沈静化...', '未完の対話...', 'やめる'] via display_button_load+hud.update_button_texts` |

**最大の未確認点だった「画面が実際に塗り替わるか」が通った**（`via (nothing)` でも
`hud not found` でもない）。待機表示の復元も動いている
（`busy off: to_display_buttons ['.', '.', '.', '.'] -> [元の3ボタン]`）。

生成された依頼が会話の内容になっているかは、2件目
`未完の対話、あるいは沈黙の追跡` / `・先程の件、まだ話が終わっていない`（直前に会話を
中断していた）が会話を反映しており、差し込み文は効いている。

### 2.14 `OSError: [WinError 123]` — 名前が原因で画像が生成できない（2026-07-28、新種）

`out/live_crashes.log`。**crash_log.txt の 114 件には無い新しいバグ。** 2回発生。

```
OSError: [WinError 123] ファイル名、ディレクトリ名、またはボリューム ラベルの
構文が間違っています。:
'...\worlds\ドスケベフェスティバル\characters\魔導演習人形「プロト・レガリア"'
```

キャラクタ `id='101'` の名前 `魔導演習人形「プロト・レガリア"` が **`「` で開いて
ASCII の `"` で閉じている**。`"` は Windows のパス構成要素に使えない。

| 時刻 | スレッド | 経路 |
|---|---|---|
| 00:06:36 | Thread-111 (execute) | `start_battle` → `create_enemies_from_npc_id` → `generate_and_write_character_detail` → `generate_character_image` → `os.makedirs` |
| 00:11:20 | Thread-123 (generate_images) | `ConversationStartManager.generate_images` → 同上 |

**バックグラウンドスレッドなのでゲームは落ちない。** 画像が生成されないまま無言で
失敗し、その NPC に関わるたび再発する（2回とも同一人物）。保存先ディレクトリが
実際に存在しないことを確認済み。

**原因はユーザー判断で確定**: LLM が生成した名前への引用符の混入。

#### 実装する側への調査結果

- `worlds/<世界>/characters/` の**ディレクトリ名はキャラクタ名そのもの**
  （実データで確認: `「銀鱗」のジーン` / `イリス・ステラ (Iris Stella)` 等）
- 名前の唯一の入口は
  `scripts.characters:Character.__init__(self, name=None, id=None, ...)`。
  LLM生成・プリセット・プレイヤー・セーブからのロードが全部ここを通る
- 名前からパスを組む箇所は**5つある**（`generate_and_write_character_detail` /
  `generate_character_image` / `generate_character_image_from_enemy` /
  `generate_enemy_image_from_character` / `delete_world_character_images`）。
  **書き込みと削除で消毒がずれると別の不整合を生む**ので、入口で正すほうが安全
- **不正文字を含むディレクトリは Windows 上に作成できない** ＝ 旧名のディレクトリは
  存在し得ないので、既存キャラクタを改名しても迷子は発生しない（安全側の根拠）
- `world_data.json` は**暗号化されていて外部から読めない**（先頭が `2L\x04\x1b...`、
  zlib/gzip でもない）。既存 `id='101'` の救済は注入後にゲーム内から行う必要がある
- 置換は消さずに全角へ写すのが穏当（`< > : " / \ | ? *` → `＜ ＞ ： ” ／ ＼ ｜ ？ ＊`）。
  Windows は末尾の空白・ピリオドも黙って切るので落としておくこと
- **残る懸念**: セーブ側 JSON には旧名が残り、次の保存で入れ替わる。その間、名前で
  突き合わせる処理があると食い違う。実測では突き合わせは id（`'101'`）で行われて
  いるように見えるが**未確証**なので、旧名は控えて置換は必ずログすること

#### 実装（`110_fix_character_name_path`、2026-07-28）

上の調査結果のとおり **入口ひとつ**（`scripts.characters:Character.__init__`）で名前を
正す。5箇所のパス組み立ては今までどおり「名前をそのまま使う」ので、書き込みと削除で
消毒がずれる余地が無い。

| 項目 | 内容 |
|---|---|
| 置換 | `< > : " / \ \| ? *` → `＜ ＞ ： ” ／ ＼ ｜ ？ ＊`（消さずに全角へ写す） |
| 追加で落とすもの | 末尾の空白・ピリオド（Windows が黙って切る）／制御文字 |
| **触らないもの** | 予約デバイス名（`CON` `NUL` `COM1` …）と、消毒すると空になる名前。直すには名前を発明するしかなく、**一度も観測していない**ので記録だけする（`107_` と同じ立場） |
| 既存の救済 | 注入直後・`load_game_new`・`start_game` の3か所で `app.world.characters` と `app.player` を掃く（`id='101'` はこれで直る）。保存はこちらから起こさず、ゲームが次に保存するときに入る |
| ログ | `out/character_name.log` に `[NAMEFIX] <場面>: id=... '旧名' -> '新名'`。**旧名を必ず残す**（上の「残る懸念」のため）。名前と同じ生文字列を持つ他の属性があれば、書き換えずに併記する |

オフライン検証は `python tools/test_character_name_sanitize.py`（**34件全通**）。
最後の2件は実地で、§2.14 で落ちた名前そのものを使い**この OS 上で**
`os.makedirs` を叩いている ― 直した名前ではディレクトリが作れ、生の名前では
今でも `winerror == 123` で落ちることを確認済み。実機での結果は §2.15。

### 2.15 `110_` 名前の消毒（2026-07-28、決着）

`out/character_name.log` に**1行だけ**出た:

```
[2026-07-28T01:18:05.185] [NAMEFIX] Character.__init__: id='101'
    '魔導演習人形「プロト・レガリア"' -> '魔導演習人形「プロト・レガリア”'
```

同じ時刻に**画像が実際に出来ている**（`%LOCALAPPDATA%\Darmabeko\Instantale\worlds\
ドスケベフェスティバル\characters\魔導演習人形「プロト・レガリア”\`、01:18）:

```
face_image.png 2,066  generated_image.png 1,290,917  no_bg_image.png 522,195
opponent_image.png  pixelated_image_original.png  prompts.json
reduced_color_image.png  reduced_color_image.orig.png        （8点）
```

判定に使った3指標（全て一致）:

| 指標 | 期待 | 結果 |
|---|---|---|
| `out/character_name.log` | 改名が1件記録される | `id='101'` の1行（旧名も残っている） |
| ディレクトリ名 | 末尾が全角 `”` | `…プロト・レガリア”`（生成物8点入り） |
| `out/live_crashes.log` の `WinError 123` | 既存2件から**増えない** | 2 件のまま |

**発火したのは `Character.__init__`（入口）で、注入時の掃除ではなかった。**
注入（00:43）の時点ではこのキャラクタのインスタンスがまだ無く、01:18 に組み立てられた
ときに直っている ― 入口ひとつに置いた狙いがそのまま効いた形。ロード時の掃除は
「入口で既に直っている」ため無音（`load_game_new` の行は出ない）で、これは正常。

`-- not touching`（予約デバイス名 / 消毒すると空になる名前）は**一度も出ていない**。
§2.14 の「残る懸念」（次の保存までセーブ側に旧名が残る）は、`id='101'` が普段どおり
振る舞ったことで実害無しと確認できたが、**突き合わせが id で行われている**ことの
直接の証拠ではない。旧名はログに残してあるので、食い違いが出たらそこから追える。

### 2.16 `108_` 売買画面の `IndexError`（2026-07-27、原因確定・救済は未発火）

会話から売買に入った瞬間に落ちた。`out/live_crashes.log` の MAIN CRASH:

```
instantale.py:1692   InstantaleApp.toggle_twin_inventory_window        situation='shop'
new_hud.py:2661      InstanTaleHUD.toggle_twin_inventory_visibility    item_id='item_1'
new_hud.py:379       InventoryGrid.place_existing_item                 new_x=1422.7  new_y=1052.855
new_hud.py:410       InventoryGrid.occupy_slots   grid_x=1 grid_y=5 x=1 y=6
                     IndexError: list index out of range   slot_index=25
```

`x` が `grid_x` のまま（幅1）で `y` だけ `grid_y+1` に進んだところで落ちているので、
**縦2マス以上のアイテムが最下段に置かれ1マスはみ出した**状態。`slot_index=25` は
`self.slots`（24マス）の範囲外で、`place_existing_item` が `is_valid_placement()` を
通さずに `occupy_slots` を呼んでいる。

**修正後、はみ出しは一度も再現していない。** `out/inventory.log` の 104 件はすべて
正常サンプル（`ok`）で、そこから正常時の寸法が確定した:

```
cols=4  rows=6  len(slots)=24  size=[259, 389]  spacing=[1,1]
所持品グリッド pos=[1150.5, 741.725]   売買グリッド pos=[943.3, 727.855] (situation='shop')
1マス=64px、2x2 のアイテムは 129px・current_slots=[17,21,18,22]
```

`grid_x` / `grid_y` / `slot_size` は `<missing>`（アイテム側の属性としては存在せず、
`current_slots` の添字で持っている）。**救済経路そのものの実地確認は §3.8。**

### 2.17 `109_` アイテム説明の自動リサイズ（2026-07-27、実機で発火）

`208_probe_item_detail` が写した固定サイズの内訳（window=2560x1387）:

```
ItemDetailBox     size=[333, 500]  size_hint=(None, None)
  name_label       height=50   text_size=[316,  50]  top=0.95
  attributes_label height=225  text_size=[316, 225]  top=0.85
  desc_label       height=150  text_size=[300, 150]  top=0.40   len(text)=108  font_size=27
```

`max_lines=0`（無制限）なので行数で切られているのではなく、`text_size` の高さ 150 に
入らない行が描かれていないだけ。300px 幅・`font_size=27` で半角24文字/行 × 3行 = 72文字
が、実際に見えていた文字数と一致する。

修正後、`out/item_detail_autosize.log` に伸びが記録されている:

```
item='item_0' box_h=550  (design 500)  desc_label=200/150 len=108
item='item_1' box_h=630  (design 500)  desc_label=280/150 len=67
item='item_1' box_h=500  (design 500)  desc_label=150/150 len=0    ← 短い文は設計値のまま
                box_h=670 / 1300 も観測
```

**設計値が勝つケース（`box_h=500`）が同じログに並んでいる**のが要点で、「長い文だけ
伸びる・普通のアイテムは 1px も変わらない」が実データで確認できている。**横幅の拡張は
まだ観測できていない**（ログの書式に幅が入っていないので、次に確かめるなら書式を足す）。

### 2.18 クエスト1件を頭から終わりまで実測（2026-07-28）

`206_` と `203_` が仕掛かった状態で通常クエスト「貯蔵庫の不協和音」
（`settlement_quest` / id 28 / 難易度2）を受注〜完了。**目的は「討伐でないクエストが
成立するか」の判定材料を取ること**で、結論は「プロンプトの差し替えだけで成立する」。

進行ループと `Literal` の推移は GAME.md §2.9 に移した（ゲームがどう動いているかなので）。
ここには**判定に使った事実と、それが何を意味するか**だけ残す。

| 観測 | 出所 | 意味 |
|---|---|---|
| `ReturnAfterCompletion` が**1ターン目から毎ターン**作られる（ボス健在でも） | `probes.log` 09:48:05 以降すべて | 完了はスキーマで塞がれていない。**プロンプトの1文を差し替えれば討伐以外で完了できる** |
| 残イベントが尽きた瞬間 `FieldEvent` モデルが**union から消えた** | `probes.log` 09:59:04 | 空 `Literal[]` を避ける分岐が実在する。敵0件でも同じなら落ちない見込み（未確認） |
| `Battle.enemies` が `Literal[7→6→5→1]` と減る | 同上 | `Literal` は**残り**であって辞書の `enemies` 全体ではない |
| 完了フェーズは `QuestEndManager(app)`、**引数ゼロ** | `quest_flow.log` 10:00:08 | LLM が `return_after_completion` を選ばなかった場合に MOD から完了させる手が残る |
| `QuestEventManager(app, event_name, enemies_info, event_turn)` | `quest_flow.log` 09:55:56 | ミニイベント単体の入口。戦闘を通さずに1回分の出来事を起こせる |
| クラッシュ・`Literal[0]` ともに0件、`live_crashes.log` は無傷 | — | この1周では既知バグをどれも踏んでいない |

**この計測で `305_mini_quest` の設計が決まった。** ゲームのコードにもセーブにも触らず、
生成（`random_quest_generator`）と進行判定（`quest_referee*`）の2つのプロンプトだけを
差し替える形にできる。**敵とボスのデータは削らずに残す**（`Literal` を空にしないため）。

### 2.19 `305_` 戦闘なしミニクエスト — 実機1回目（2026-07-28、半分成功）

投入した当日に1件通した。**依頼の中身は成功、進行の止め方が失敗**。
ユーザー報告は「ボスを倒すまでイベントがほぼ無限に続く形だった」。

##### 効いていたもの

```
request_summary: ネブリス外縁の湿原地帯に自生する、極めて希少な薬草
                 『銀光の雫』を5株採取してきてほしい。
```

討伐ではなく**採集依頼が生成された**。掲示板への設置・押下の横取り・控え・
進行側の書き換え（25ターン全部に命中）も動いた。`encounter_final_boss` は
**一度も選ばれていない**（素のプロンプトなら在庫が尽きた時点で強制される）。
10:49〜10:50 には実際に薬草を採取する描写まで出ている。

##### 壊れていたもの — 原因は3つ、それぞれ別

| # | 症状 | 原因 | 直し方 |
|---|---|---|---|
| 1 | `return_after_completion` が**一度も選ばれない** | 進行側に渡していたお題が**種類のテンプレ文**（「指定された物を必要な数だけ集める」）で、このクエストの達成条件ではなかった。referee は何をもって達成かを判定できない | 生成時に `request_summary` を控えへ保存し、`【依頼の内容（達成条件はこれ）】` として渡す |
| 2 | `nothing_happens` が**12ターン連続**（ほぼ同じ描写の繰り返し＝報告された「無限」） | 生成されたフィールドイベントが**2個だけ**で3ターン目に枯れた。battle を封じたので残る選択肢が `nothing_happens` しか無くなった | 生成側でイベントを 4〜6 個要求。加えて `nothing_happens` の説明行を書き換え、進展が2ターン無ければ帰還を選ばせる |
| 3 | 最終的に battle を8回選びボスを倒して終了 | プロンプトに `- 残り通常戦闘回数: 3` `- 残り中ボス戦闘: [10体]` `- 残りラスボス戦闘: [...]` が事実として並び続け、**在庫消化の圧力**になっていた。生成時に miniboss が5体も作られていた（通常は1体） | 1文目に「在庫を消化する必要は一切無い。目的が果たされたなら帰還させてよい」を明記。生成側で `miniboss は1体も設定しないこと`（`normal` と `boss` は残すので `Literal` は空にならない） |

##### この回で誤判定しかけたこと（2件）

- **`output_data/` に書き換えが映らない。** 保存は `chat` より上流なので、記録された
  プロンプトは書き換え前のもの。「効いていない」と読みかけた。GAME.md §2.12 に
  独立した事実として書いた（同じ記録で `105_` の COMPACT 後も `$defs` が残っている
  のが証拠）
- **監査ログの枠を生成と進行で共有していた。** 生成が内部で4回 LLM を呼んだ回に
  `AUDIT_FIRST_N` を使い切り、**肝心の進行側の中身が1行も残らなかった**。
  原因を読むためのログが、原因を読みたい場面で無かった。枠は用途ごとに分けること

### 2.20 `305_` 実機2回目 — 直った3点と、新しく出た1点（2026-07-28）

§2.19 の修正を入れて再度1件通した。ユーザー報告は
**「帰還したら撤退してクエスト失敗扱いになった」**。

##### 直った（§2.19 の3点はすべて解消）

| 前回の症状 | 今回 |
|---|---|
| お題がテンプレ文だった | `【依頼の内容（達成条件はこれ）】湿原に自生する『紫光の菌株』を15個採取して持ち帰ってください。` が毎ターン入った |
| イベントが2個で枯れた | **5個生成**され、`field_event` が5ターン連続で走った（採取が実際に進む描写） |
| miniboss 5体・battle 8回 | `- 残り中ボス戦闘: []`（0体）。battle は**1回だけ** |

進行ログも読めるようになった（監査枠を分けた効果）。`encounter_final_boss` は
今回も0回。

##### 新しく出た1点 — **達成したのに「撤退」になった**

最終ターンの実データ:

```
プレイヤーの入力: 「採取を終了し、帰還の準備を整える」
narration:       「…目標の数は、既に十分な数に達している。」
turn_resolution: retire_from_the_quest        ← ★
```

**referee 自身が「目標の数に達している」と描写しながら撤退を選んでいる。**
原因は書き換えていなかった2行で、どちらも**帰還の意思＝放棄**と定義していた:

```
- retire_from_the_quest: クエストを放棄し帰還する。プレイヤーがその意思を明確に
  示したときにのみ選択するべきだが…しかし具体的が無いならばさっさと撤退させること。
- クエスト攻略を諦めての撤退以外では、クエストエリア外への移動は認めない。
  プレイヤーが明確に中断と帰還の意思を持つ場合のみ認められる。
```

討伐クエストではこれで正しい（帰るなら諦めたということ）。**お使いでは逆で、
目的を果たしたから帰る。** 両方とも `output_data/` の 460/460 で安定していたので、
アンカーに追加して書き換えた:

- 撤退は「**目的を果たせないまま**放棄して帰還する」に限定し、
  「目的が果たされているなら、プレイヤーが帰還を望んでもこれを選んではならない。
  必ず `return_after_completion` を選ぶこと」を明記
- エリア外への移動は「帰還のときにのみ認められる。**まず目的が果たされているかを
  判断**し、果たされていれば達成としての帰還、そうでなければ放棄」に置き換え
- 1文目にも同じ判断を書いた

**撤退そのものは残してある。** 達成できない依頼で詰ませないため（回帰項目に入れた）。

##### この2回で分かった、書き換えの当て方

> **1つの前提は1箇所には書かれていない。** 「討伐クエストである」という前提は
> 完了条件・戦闘優先・ラスボス強制・`nothing_happens`・**撤退の定義**・
> **エリア外移動の定義**と、少なくとも6箇所に分かれて書かれていた。
> 1回の実機で1〜2箇所ずつ見つかる形になったので、**実機で1周する前に全部を
> 見つけようとしない**のが正しい進め方だった。毎回ログの1行が次の箇所を教える。

##### まだ分かっていないこと

**`return_after_completion` をゲームが素直に受けるかは、依然として未検証。**
2回とも LLM が選ばなかったので（1回目は完了判定できず、2回目は撤退と取り違えた）、
`quest_process_phase` 側の可否を試せていない。次の実機で選ばれれば同時に判明する。

### 2.21 `305_` 実機3回目 — 撤退は直った。今度は在庫切れでボス戦へ流れた（2026-07-28）

##### 直った

**`QuestRetireManager` は 0 回**（`quest_flow.log` 全期間で 0）。§2.20 の
「達成したのに撤退」は再発していない。

##### 今回の終わり方

```
14:40:06 quest_referee_with_free_action -> battle  enemies=['湿原の支配者・霧の魔女']
         - 残り通常戦闘回数: **0**   - 残り中ボス戦闘: **[]**
         - 残りフィールドイベント: **[]**   - 残りラスボス戦闘: **['湿原の支配者・霧の魔女']**
14:41:13 quest_boss_battle_log_summarizer → QuestEndManager
14:42:08 quest_summarizer
```

**在庫が全部尽きて、残ったのがラスボスだけになった。** そこへ `battle` で流れ、
倒したのでクエストが終わった ― つまり2回目と同じ「ボスを倒して完了」。
`return_after_completion` は**3回とも一度も選ばれていない**。

##### 原因は、まだ書き換えていなかった2行

| 行 | なぜ効くか |
|---|---|
| `- battle: 雑魚敵や中ボスやラスボスとの戦闘の開始。…` | **ラスボスを `battle` の相手に選んでよい**と明記されている。`encounter_final_boss` を封じても、こちらから同じ結果に行ける |
| `- 残りラスボス戦闘: **[名前]**` | ゲームの状態そのもの。**「まだやることが残っている」という圧力**として毎ターン効き続ける |

**`- battle:` の行は完全一致では当たらない。** 末尾の適正数（「平均1.6体」）が
難易度で変わり、実データでは 6 通りあった（1.0 / 1.1 / 1.2 / 1.3 / 1.4 / 1.6）。
**行頭で当てて末尾に書き足す**規則（`REFEREE_LINE_RULES`）を新設した ―
合計は 476/476 で全件に在る。

`- 残りラスボス戦闘:` も同じ仕組みで注記する。**数や名前は書き換えない**
（ゲームが握っている事実なので嘘を書かない。扱い方だけ足す）:

```
- 残りラスボス戦闘: **['迷宮の守護龍']**（**このクエストでは戦わない。** 残っていても
  無視すること。これが残っているせいでクエストが終わらない、ということは無い）
```

##### この回で見つけた自分のバグ

**書き換えが冪等でなかった。** `nothing_happens` の置換文は元の文を含む形
（元の説明に書き足す）なので、同じ文字列に2度当てると際限なく伸びる。実経路では
プロンプトが毎ターン組み直されるので一度しか通らず害は出ていなかったが、
テストで再適用したら伸びた。**置換後の文が既に在るなら当てない**ようにして、
回帰項目に入れた（`104_` の「冪等なら発火箇所が致命的でなくなる」と同じ話）。

---

## 3. 未確認項目と確認手順

優先順。**どれも「プレイしていれば片付く」もの**なので、実機で踏んだらログを見ること。

### 3.1 戦闘まわり（`106_` / `107_`）— 見張り方と、残った未確認

`106_` は決着済み（§2.5）。`107_` も**戦闘終了時の発火を実機で確認して決着**
（2026-07-28・§2.12）。残るのは**ロード時の発火**（残骸入りのセーブを読んだときだけ
出る）と、**sweep 後に mixer が 1 本へ戻ることの確認**の2点だけ。
どちらも `out/battle_bgm.log` を見る。

**合格条件は `mixer = n/8 channel(s) busy` が1本を超えて増えていかないこと。**
**BGM が正常に聞こえること自体は合格条件にならない**（一度これで誤判定した。§2.5）。

戦闘を1回して、次の3行が揃えば `107_` も片付く:

| ログ | 意味 |
|---|---|
| `[FLAGFIX] BattleEndInFreeAction.end_phase: cleared in_battle` | **フラグ側が効いた**（2026-07-28 に確認済み・§2.12） |
| `[FLAGFIX] load_game_new: cleared in_battle` | 既に残骸入りで保存されたセーブを読んだときに出る |
| `[BGMFIX] sweep after BattleEndInFreeAction.end_phase: handed <曲> back to the app` | 曲の引き取り成功（確認済み） |

その他の行の読み方:

| ログ | 意味 |
|---|---|
| `orphan: <曲> was attached to BattleEndInFreeAction instead of the app` | ゲーム側のバグが発火した（毎回出る。正常） |
| `battle track outside a battle: ...` | 戦闘が走っていないのに戦闘曲が鳴った ＝ ロード時の枝。直後に `sweep` が続けば正常 |
| `sweep after ...: stopped battle ...; restarted ...` | 引き取れず鳴らし直した経路。正常だが曲は頭から |
| `sweep after ...` が1行も出ないまま `mixer` が増える | 後始末が走っていない。起点の条件を疑う（前回は `in_battle` の居残り。GAME.md §2.10） |
| `orphan: ... instead of the app` が**別の型名**で出る | 未知の経路。その型が新しい修正対象 |
| `[FLAGFIX] ...: in_boss_battle still set -- not touching` | 観測できていなかった組み合わせが出た。`107_` の対象を広げる材料 |

**`107_` が効いていれば、施設到着イベント（`300_`）も戦闘後に復活する**
（`player_events.log` に `skip: ... busy ['in_battle']` が出なくなる）。こちらも
併せて見ると、フラグが本当に下りているかの裏が取れる。

### 3.2 会話からの依頼受注（`301_`）— 実機確認（3段階）

> **2026-07-28 に3段階とも通った（§2.13）。** 最大の未確認点だった HUD の塗り替えも
> 実機で確認済み。以下は手順として残す（回帰を見るときに使う）。
> 未実測のまま残っているのは末尾の「ついでに片付くもの」のうち**選択肢のページ送り**と
> `generate_random_quest()` の副作用の2点。

**先に `python tools/test_quest_offer.py`（33件）を通しておくこと。** 実機で見るのは
「オフラインでは確かめられないもの」だけになった: HUD が本物でも塗り替わるか・掲示板の
絞り込みが実データで妥当か・生成された依頼が会話の内容になっているか。

1. **ボタンが出るか**: NPC と会話する。**「会話を終了する」の手前に「依頼を受ける
   （話を切り上げる）」**が並んでいれば設置成功
   （`quest_offer.log` に `added '依頼を受ける' to the conversation menu`）。
   「行動」メニューを探す必要は無い
2. **既存依頼の受注**: 「依頼を受ける」→ 一覧に `【難易度】タイトル` が並ぶ。現在地が
   「水底の隠れ家」なら 3 件（難易度 39/43/45）が正解。選ぶとゲーム本来の受注画面に入る。
   `WARN difficulty mismatch` が出たら絞り込みの前提（`neighboring_settlement_id`）が
   崩れている。**既定（`FILTER_BY_NPC = True`）では初対面の NPC の一覧は「この話から
   依頼を作る」だけになる**（§2.10）。全件見たいときは `False` に
3. **会話からの生成**: **先に NPC と少し会話してから**「依頼を受ける」を押すと、一覧の
   先頭に「この話から依頼を作る（NPC名）」が出る。押すと LLM が1回走る（30〜60秒）。
   `remembered talk with ...` → `inject: area_description N -> M chars` →
   `generate: -> quest '24' '...'` → `acceptance: process_choice(...)` の順に出れば通っている。
   **生成された依頼が会話で頼まれた内容になっているか**を目で見ること。なっていなければ
   差し込み文（`addition`）を強める。会話をしていなければこの項目は出ない（仕様）

**画面が実際に塗り替わるか**が最大の未確認点。`quest_offer.log` に
`quest board: to_display_buttons [...] -> [...] via display_button_load+hud.update_button_texts`
が出た上で画面が変わるかを見る。`via (nothing)` や `hud not found` なら HUD の構成が
変わった合図。

**ついでに片付くもの**:

- **NPC と会話する** → `quest_flow.log` に
  `set_top_info_layout_conversation_button_callback:` と `hud top info texts -> [...]` が出て、
  「行動」への切り替えが画面のどのボタンかが確定する
- **依頼掲示板を1回開く** → `206_` が `DisplayQuestChoice` → `QuestChoiceManager` →
  `QuestStartManager` の本来の経路を丸ごと記録する。自前の経路との答え合わせに使える
- **選択肢のページ送り**。実測できたのは1ページに収まる場合だけで、そこでは
  `display_button_map` が恒等写像だったため「表示位置」と「buttons の添字」を区別できて
  いない（TECH.md §8）
- `generate_random_quest()` を掲示板の外から呼んで副作用が無いか

#### 3.2.1 引継ぎ: `tools/test_quest_offer.py` が1件赤（2026-07-27）

**`302_` のセッションで見つけた。修正は `301_` 側で行う。** リポジトリは見つけた状態の
まま戻してある（この件の差分は入っていない）。

```
python tools/test_quest_offer.py
  FAIL 閉じた後で掲示板が開く            (app.opened_board == 0)
1 件失敗
```

**この赤は `302_` の作業より前から出ている。** `302_` 側の変更（`original_party` の
ガード修正）とは無関係。

##### 決着（2026-07-28）: **製品のバグではなく、テストハーネスの人工物**

実機で2回計測した。`out/quest_offer.log`。

| | 1回目 | 2回目 |
|---|---|---|
| `end conversation: closed; continuing` → `open board:` | **0.606秒** | **0.601秒** |
| ボタン押下 → 掲示板 | 2.71秒 | 2.69秒 |
| `still busy ... after 30s; going ahead` | **出ていない** | **出ていない** |

`out/*.log` 全体を検索して `still busy` は**一度も出ていない**。`IDLE_TIMEOUT`
（[ui.py:67](runtime/instantale_modloader/ui.py#L67) の 30.0）には**実機では到達して
いない**。下の「疑っていた筋」＝自分で立てた合図で自分を待たせている、は**否定された**。

30秒級の待ちは生成側に実在した（`generate: took 37.5s` / `took 140.1s`）。
**ユーザーの指摘（次項）が正しかった。**

したがってオフラインの赤は、**偽 Clock が実時間を進めないため `when_idle` が
進行しないというハーネス側の限界**であって、`301_` にも `ui` にも直すべきものは無い。
**直すならテストハーネス側**（偽 Clock に `when_idle` を駆動させる）。

> 前セッションの `ignore=` 案が他2件を壊したのは、**存在しないバグを直そうとしていた**
> ため。戻したのは正解だった。

##### 疑っていた筋（上記のとおり 2026-07-28 に否定）

`open_quest_board` は会話中なら `show_busy(app)` を呼んでから会話を閉じる。
`show_busy` は待機表示のために **`app.is_button_enabled = False` を自分で落とす**。
その後 `ui.Screen.end_conversation` → `when_idle` が「手が空くのを待つ」が、
`ui.busy_signals` は**まさにその `is_button_enabled` を「塞がっている」と数える**。
待機表示を解く `clear_busy` は `open_quest_board` の中、つまり**待っている当の
follow_up の中**にしか無い。

    show_busy: is_button_enabled = False
      -> end_conversation -> when_idle: busy=['is_button_enabled=False'] -> 待ち続ける
         -> proceed_on_timeout=True なので IDLE_TIMEOUT(30秒) 後にようやく follow_up
            -> open_quest_board -> clear_busy

つまり**自分で立てた合図で自分を待たせている**。オフラインの偽 Clock は実時間を
進めないので 30 秒に到達せず、掲示板が開かないまま検査に落ちる ― というのが
テストが赤い理由の説明になる。

##### 「30秒はクエスト生成の待ちでは？」（ユーザーの指摘 — **2026-07-28 に的中を確認**）

**指摘のとおりだった。** 実機で待つのは生成経路だけで、受注経路は 0.6 秒で通る。
以下は切り分け前の記述だが、経路の対比はそのまま有効なので残す。紛らわしいのは、
同じ画面で LLM を回す経路が別にあること:

| 経路 | LLM | 想定される待ち |
|---|---|---|
| 「この話から依頼を作る」 | **回す**（`random_quest_generator`） | 30〜60秒。**これは正常** |
| 「依頼を受ける（話を切り上げる）」 | 回さない（会話終了の要約は別途走りうる） | 本来は待たないはず |

切り分けは `out/quest_offer.log` の時刻で付く:

1. `end conversation: closed; continuing` の時刻と、その後の
   `open board: process_choice(DisplayQuestChoice, ...)` の時刻の差を見る
2. **差がほぼ 30 秒ちょうど**なら `IDLE_TIMEOUT` ＝ 自分待ち。加えて
   `end conversation: still busy ['is_button_enabled=False'] after 30s; going ahead`
   の行が出るはず（`when_idle` が時間切れで進むときに書く）。**この行が出れば確定**
3. **差がばらつく / 30秒未満**なら自分待ちではない。生成経路と取り違えている

##### 試したこと（採用しなかった）

`ui.busy_signals` / `when_idle` / `end_conversation` に `ignore=` を足し、
`301_` が待機表示を出している間だけ `ignore=("is_button_enabled",)` を渡す形を試した。

- 「閉じた後で掲示板が開く」は**通るようになった**（＝上の筋の裏付けにはなる）
- ただし**同じスイートの別2件が落ちた**:
  `「この話から依頼を作る」が先頭に出る` / `依頼人の名前が文言に入る`。
  掲示板は開き `app.buttons` は `['戻る']`（依頼の間引きは正しい）。**生成ボタンだけが
  出ない** ― 会話が実際に閉じた後なので `current_talk(app)` が空を返している疑い
  （`in_conversation` が落ちた後は `last_talk` 頼み。`remember_talk` は
  `state["npc_id"]` が無いと何も控えない）

1件直して2件壊す状態だったので**全部戻した**。`301_` の流れを把握している側で
やり直すのが早い。**`when_idle` が2箇所ある**ことに注意（会話が既に閉じている早期
リターンと、`wait_for_end` の中）。片方だけ直しても効かない。

##### 直すときの選択肢（**2026-07-28 の切り分けで対象が変わった**）

上の3案は**いずれも製品側を直すもので、もう当てはまらない**（直す対象が無い）。
記録として残すが、採ってはいけない。

**直すのはテストハーネス側。** 偽 Clock が実時間を進めないため `when_idle` の
ポーリングが進行せず、掲示板が開かないまま検査に落ちている。偽 Clock に
`when_idle` を駆動させる（経過時間を進める／保留中のコールバックを消化する）のが筋。

**製品側に手を入れないこと。** 実機では 0.6 秒で通っており、`ignore=` を入れると
前セッションで実証されたとおり別の2件が壊れる。

### 3.3 パーティ関係（`302_` の残り / `303_` / `304_`）

**`302_` の残り**: 土地を跨いで別れた場合（いまの町のギルドへ置く経路）が未実測。
`leave: ... [guild of the current area (left home behind)]` が出るのを見る。

**決着（2026-07-27）: 「選択肢が出なくなった」は `original_party` の読み違い。**
ユーザー報告「パーティの NPC と別れる選択肢が出ない。おそらく `301_` と競合」。
競合ではなく `302_` 単独の誤りだった。ログの1行が答え:

```
screen: partner='8' member=True ... party=['player', '8']
not offering the farewell to '彷徨える剣士 ログ': original_party is set
```

セーブを見ると `party` と `original_party` が**同じ内容**で入っていた
（`current_quest_data` は `None`、クエスト中ですらない）:

```
party            ['player', '8']
original_party   ['player', '8']
```

`original_party` は**差し替えの控え**であって「差し替え中の印」ではない。
「入っていたら断る」にしていたので、仲間が居ると毎回断っていた。

**その直しも外した（2026-07-28、同じ症状で再発）。** 「控えと名簿が食い違えば
差し替え中」に変えたところ、今度は**雇用直後**に消えた:

```
not offering the farewell to 'カイン': party is swapped (original_party=['player'] != party=['18','player'])
```

`original_party` は雇用に追随せず**古いまま**なので、仲間を入れれば当然食い違う。
差し替えではなかった。

**結論: `original_party` は判定に使わない。** 同じフィールドの意味を2度続けて
外して2度ボタンを消しており、3度目を試す根拠が無い。守りたかった「パーティが
一時的に差し替えられている最中」はクエスト中の話で、そこは
`current_quest_data`（クエスト外では `None`。実セーブで確認済み）で既に断っている。
値は `screen:` の行に `original_party=[...]` として**記録だけ続ける** ― 本当に
差し替えが起きる場面が来れば、そこに現れる。

**教訓（2つ）**:

- フラグ名が意味するとおりに動くとは限らない（`in_shopping` と同じ形。GAME.md §2.6）
- **意味を確かめていないフィールドで機能を止めない。** 止める判断に使ってよいのは
  意味の裏が取れた信号だけ。確かめていないものは*記録*に回す

> **`303_` と `304_` は同じ場面に手を入れる。** 既定では `304_`（解散しない）が勝つので、
> **`303_` の手順をそのまま踏んでも `303_` の行は出ない**（それが正しい）。`303_` を
> 確かめたいときは先に `runtime/mods/304_quest_end_keep_party.py` の頭に `_` を付けて
> 注入し直すこと。TECH.md §3.2 / §3.3。

**`303_` は全体が未確認。** **仲間を連れてクエストに行く**必要があり、かつ差し替えが目に
見えるのは**雇った町とは別の町でクエストを終えたとき**（同じ町なら元から同じ場所に
置かれる。§2.9 の `漣のギルド` がまさにそれ）。先に
`python tools/test_quest_end_guild.py`（45件）を通しておくこと。

1. A の町で NPC を雇う → B の町へ移動 → B のクエストを受けてクリアする
2. `party_leave.log` に `quest-end: '<名前>' (<id>) left the party in <B> via
   QuestEndManager.method_1 -> '<B のギルド>'` が出る
3. 続けて `leave facility via ...`（第1層が効いた）か
   `'<名前>': '<A のギルド>' -> '<B のギルド>'`（第2層が効いた）のどちらかが出る。
   **どちらが出たかで「ゲームが `get_party_leave_facility` を使っているか」が確定する**
4. 画面に `<名前>は<B のギルド>に留まることになった。` が出る
5. B のギルドにその NPC が居ること（再雇用できること）
6. `nobody moved ... placing them by hand` が出たら第3層まで落ちている＝置き直しの経路が
   こちらの前提と違う。**その行が出た状況を残すこと**

**`304_` も全体が未確認。** こちらは**同じ町でクエストを終えても目に見える**（そもそも
外れない）ので、`303_` より確かめやすい。先に `python tools/test_quest_end_keep.py`
（50件）を通しておくこと。

1. NPC を雇う → クエストを受ける → クリアして「帰還する」
2. 画面に `<名前>はパーティに残り、引き続き行動を共にすることになった。` が出る。
   **「…はパーティから離脱した。」が出たら差し替えが効いていない**
3. `party_leave.log` に
   `quest-end keep: '<名前>' (<id>) stays in the party — QuestEndManager.method_1
   did not disband the party` が出る
4. HUD のパーティ欄にその NPC が残っていること・**そのまま次のクエストに同行すること**
5. ギルドや宿にその NPC が**立っていない**こと（居たら置き直しを取りこぼしている。
   `not placing ... anywhere` が出ているかを見る）
6. その後 `302_` の「ここで別れる」で普通に外せること・**外した先にちゃんと居ること**
   （`is leaving for real` が出て控えが落ちる経路。ここが壊れると NPC が世界から消える）
7. `WARN ... is not in __main__` や `WARN no code object resolved` が出ていたら、
   そのビルドでは解散を捕まえられていない（`303_` の挙動に戻る）

**`302_` の実機確認手順**（再確認するとき。**仲間が居ないと何も起きない** — 現行3セーブは
全て `party = ['player']` なので、まず NPC を雇う）:

1. 仲間と会話する → 「会話を終了する」の**手前**に「ここで別れる」が出る
   （`added 'ここで別れる' to the conversation with ...`）
2. 押す → 「ああ、ここで別れよう」「やめておく」の2択になる。**やめておくと元のボタンに
   戻る**（ここまでで何も起きていないこと）
3. 決定 → 会話が閉じ、`leave: party before = [...]` → `leave: party after = [...]` →
   `leave: moved '63' to ...` → `leave: saved` の順に出る。HUD のパーティ欄から消え、
   別れた施設にその NPC が居ること
4. `WARN remove_party_member left ...` が出たら名簿の入れ物の前提が崩れている。
   ログの `party after` を見る

**ついでに片付くもの**: `302_` は自前の解散以外の `remove_party_member` 呼び出しを
`remove_party_member('63') from <関数名> (<ファイル:行>)` の形で記録する。**死別**が起きれば
その経路も確定する。`add_party_member` / `process_party_member_choice` も1行ずつ記録するので、
雇用と「仲間に話しかける」の経路も同時に分かる。

### 3.4 BGM 偏り是正（`104_`）— ゲーム内動作

`104_` 注入後に新エリアを生成し（クエスト受注等）、`out/bgm.log` に
`[BALANCE] <フック名> area N: ... -> ...` が出ること・**どのフックが発火したか**を確認する
（3つのうちどれで `bgm` が確定するかはコンパイル済みのため未確定。GAME.md §2.11）。

既存3世界の是正は `python tools/rebalance_saved_bgm.py`（dry-run）で差分を見て、納得したら
`--apply`（バックアップ自動作成）。**ユーザー指示により未実行。**

### 3.5 遅延 import の当て直し — 実機での流れ

`watch.bat` を立てた状態でゲームを起動し、会話などで LLM を1回動かしてから
`out/modloader.log` を見る。次の流れが出れば正常:

```
defer wrap llama_cpp_runtime_completion:LlamaCppClient.chat (... is not imported yet)
deferred: waiting for llama_cpp_runtime_completion, scripts.llm.llm_manager (checking every 5s)
deferred: llama_cpp_runtime_completion imported; re-applying mods
boot complete: 27/27 mod(s) applied
```

### 3.6 その後の予定（優先順）

1. **`tools/test_quest_offer.py` の赤1件（§3.2.1）** — 切り分け済み。**製品側では
   なくテストハーネスの偽 Clock を直す**。製品側に手を入れないこと
1.5 **戦闘なしミニクエスト（`305_`）の実機確認**（§3.10）。新規投入。掲示板から
   1件作って受注するだけで、3段階すべてが `out/mini_quest.log` で判定できる
2. 上の §3.3（プレイしていれば片付く）。§3.1・§3.2 は決着済みで、以後は
   `mixer = n/8` を時々見るのと、ロード時の `[FLAGFIX]` を待つだけでよい
3. **`facility_move_to` / 空 `Literal[]`** — 計測は仕掛け済み。発生すれば自動で原因が
   確定する。プレイを続けるだけでよい。空 `Literal[]` は**敵候補0件のエンカウント**が
   第一容疑なので、エンカウント系の分岐を意識的に踏むと再現に近づく可能性がある
4. **多重起動抑止 / `--parallel 1`**（後日対応と指示済み。GAME.md §2.12）
5. **ネイティブクラッシュダンプ 7件**（未着手領域。TECH.md §8）

### 3.7 名前の消毒（`110_`）— 以後の見張り方

§2.15 で決着済み。以後は `out/character_name.log` を時々見るだけでよい。

* `-- not touching` が出たら**予約デバイス名か、消毒すると空になる名前**に当たった
  （未観測の種類。直すには名前を発明することになるので記録だけしてある）。
  その名前を控えて設計を決めること
* `(same string also in: ...)` が出たら、名前と同じ文字列を持つ別の属性が居る。
  こちらは書き換えていないので、そこが表示や突き合わせに使われていないかを見る
* **世界名は対象外**（`worlds\<世界>\` も同じ壊れ方をしうるが、世界名の入口は未調査）。
  起きれば `001_` が `WinError 123` として同じ形で捕まえる

### 3.8 売買画面の救済経路（`108_`）— まだ一度も通っていない

修正は入っているが、**はみ出しが再現していないので救済側のコードは実地では未実行**
（`out/inventory.log` の 104 件はすべて正常サンプル）。次に売買画面で落ちたとき、または
`inventory.log` に `ok` 以外の行が出たときが確認の機会:

| ログ | 意味 |
|---|---|
| `ok ...` だけが並ぶ | 復元位置がそのまま使えている（正常。寸法の基準として使う） |
| はみ出しを捕まえた行 | `find_placement_position` → `place_new_item` へ流した。**アイテムが別のマスに置かれる**ので、見た目が変わっても正常 |
| 落ちる | 救済が効いていない。`is_valid_placement` の判定と実際の `slots` 長を突き合わせる |

**そもそもなぜ復元位置がはみ出すのか**は未解明のまま（グリッドの列数が画面ごとに違うのか、
ピクセル→マスの変換が別スケールなのか）。`inventory.log` に所持品側と売買側の両方の寸法が
出るようにしてあるので、再発時にそこから詰められる。

### 3.9 アイテム説明の横幅（`109_`）

高さの拡張は実機で確認済み（§2.17）だが、**横の拡張は未観測**。長い説明のアイテムに
マウスを合わせたとき、箱が横にも広がるかを見る。広がらなければ縦横比の判定
（`RATIO_SLACK`）か窓幅の取得を疑う。ログの書式に幅が入っていないので、確かめるなら
先に `item_detail_autosize.log` の出力へ幅を足すこと。

### 3.10 戦闘なしミニクエスト（`305_`）— 実機確認

**先に `python tools/test_mini_quest.py`（94件）を通しておくこと。** 設置・押下・生成・
控え・書き換え・素通し・目印切れはそこで済むので、実機で見るのは**オフラインでは
確かめられないもの**だけ ―「LLM が実際に討伐でない依頼を作るか」「進行が戦闘に
ならないか」「達成で帰還できるか」。判定は全部 `out/mini_quest.log` で付く。

> **4回通した（2026-07-28）。1・2・3 は済み**、残っているのは
> **「達成として帰還できるか」1点だけ**。経緯は §2.19〜§2.22。
> **毎回、討伐前提を書いている行が1〜2つずつ新しく見つかった**（最終的に8箇所）。
> 4回目でこの1点は**文面での説得をやめ、戻り値の差し替えに切り替えた**。
>
> **判定の前に注入し直すこと。** 4回目は 14:28 の注入のまま 14:48 に遊んでいて、
> 直前の修正が1つも載っていなかった（§2.22）。`modloader.log` の最後の
> `boot #N` と `runtime/mods/*.py` の更新時刻を見比べる。

1. ~~**ボタンが出るか**~~ — 済。**「やめる」の手前に「軽い頼まれごとを探す」**
   （`board: added '軽い頼まれごとを探す' at N`）

2. ~~**生成されるか**~~ — 済。討伐ではなく採集依頼が生成された。押すと LLM が走る
   （実測 350秒。内部で複数回呼ぶことがある）。
   `generate: kind=gather (採集) combat=none` → `inject: area_description N -> M chars`
   → `rewrite: generator ...` → `remembered: quest '44' '…' kind=gather` の順に出れば
   通っている。掲示板が開き直り、作られた依頼が並ぶ。
   **生成された依頼文が討伐になっていないか**を目で見ること。なっていれば
   `KINDS[*]["brief"]` を強める

3. ~~**進行が戦闘にならないか**~~ — 済（2回目）。イベント5個・`field_event` 5連続・
   battle 1回・`encounter_final_boss` 0回。

4. **達成として帰還できるか**（残っている唯一の項目）: 目的を果たしてから
   「帰還する」意思の入力をする。**`quest_flow.log` に `QuestEndManager` が出て、
   クエストが成功扱いになれば決着**。撤退扱いになったら
   `output_data/` の最新の `quest_referee_with_free_action` を開き、
   `turn_resolution` が `retire_from_the_quest` か `return_after_completion` かを見る。

   `mini_quest.log` に次の2行が出ていれば、MOD 側は仕事をしている:

   ```
   referee(free_action): result shape = ...          ← 戻り値の形（初回のみ）
   referee(free_action): retire -> return_after_completion (quest='...')
   ```

   | 出たもの | 意味 | 次の手 |
   |---|---|---|
   | 上の2行 → クエストが**成功で終わる** | **決着**。ゲームは `return_after_completion` を受ける | §3.10 を閉じる |
   | 上の2行 → それでも**失敗扱い** | **ゲーム側が拒んでいる**（`quest_process_phase` がボスの生死を見ている） | `QuestEndManager(app)`（引数ゼロ）を MOD から起こす。**このときだけ実装する** |
   | `could not write type on ...` | 戻り値が dict でも属性でもない形 | その行に出た型名を見て読み書きの手を足す |
   | 何も出ないまま撤退した | 控えと `quest_title` が突き合わない | `mini_quests.json` の `title` と、`quest_flow.log` のクエスト名を見比べる |

`quest_flow.log` 側の目安（2回目の実測値を括弧に）:

| 見るもの | 期待 |
|---|---|
| `PhaseSpec('QuestEventManager', ...)` | 出てよい（むしろ本命。2回目は5連続） |
| `PhaseSpec('BattlePhaseManager', ...)` | `COMBAT_MODE="none"` なら出ない（2回目は1回だけ出た） |
| `PhaseSpec('QuestEncounterFinalBoss', ...)` | どちらのモードでも出ない（2回とも0回） |
| `PhaseSpec('QuestEndManager', ...)` | **達成したら出る。これが最後の未確認** |

**その後に残るもの**（急がない）:

- **`nothing_happens` の連続。** 3ターン以上続いたら、イベントの在庫
  （`- 残りフィールドイベント`）が尽きていないかを `output_data/` の
  `quest_referee_with_free_action` で見る。尽きているなら `EVENT_COUNT` を増やす
- **`COMBAT_MODE = "mobs"`**（＝ボスなしの雑魚のみ）は既定ではない。`"none"` が
  通ってから切り替えて確かめること
- **敵0件の依頼は作らない**という設計判断が正しいままか。`probes.log` に
  `Literal[0` が出たら前提が崩れている（`305_` は敵を減らさないので、出たとしたら
  別経路）

### 2.22 `305_` 実機4回目 — 撤退は直っていなかった。文面をやめて戻り値を持つ（2026-07-28）

##### 前提: この回は修正前のコードで走っている

```
最後の注入   14:28:05   (out/modloader.log)
305_ の修正  14:47      ← §2.21 の battle / 残りラスボス戦闘 の行
208_ の修正  14:48      ← opacity の見張り
プレイ       14:48〜14:55
```

`update_content` は 14:53 / 14:54 / 14:55 と記録されているのに `opacity ->` が
**0行**。**注入し直していないので新しいコードが載っていない。** §2.21 の修正も
この回では効いていない。

> **編集したら注入し直す。** §2 の最重要項目（`python injector.py`）。
> 「直したのに変わらない」と読み違える一番の原因なので、判定の前に
> `modloader.log` の最後の `boot #N` と mod ファイルの更新時刻を必ず見比べること。

##### 撤退が再発した — そして原因は §2.20 の修正では消せない

```
入力: 「湿原を離れる」
描写: 「…目的の品は十分に確保されており、これ以上の滞在は命の保証を危うくするに過ぎない。」
判定: retire_from_the_quest
```

§2.20 の書き換え（撤退の説明文・エリア外移動の定義・1文目の判断）は**この回に
載っていた**（14:28 の注入に含まれる）。それでも押し切られている。

**§2.21 で「撤退は直った」と書いたのは誤りだった。** 根拠にした
「`QuestRetireManager` が0回」は、**その回はプレイヤーが帰還を口にしなかった**
だけで、直った証拠になっていない。**症状が出る条件を踏んでいない結果を、
直った証拠に使わないこと。**

##### 文面での説得をやめ、戻り値を差し替える

同じ1点を文面で2回外している（2回目・4回目）。素の説明文の
「プレイヤーがその意思を明確に示したときにのみ選択するべき」が強く、
**帰還の意思＝放棄**という読みを上書きしきれない。

ミニクエストには倒すべきラスボスが居ない。**目的を果たした状態での帰還は定義上
そのまま達成**なので、`quest_referee*` の戻り値で `retire_from_the_quest` を
`return_after_completion` に差し替える（`RETURN_INSTEAD_OF_RETIRE`、既定 ON）。
対象は**この MOD が作った依頼だけ**（`quest_data['quest_title']` を控えと照合）。

**戻り値の形は実測できていない。** `output_data/` に残るのは保存側が整えた形で、
関数が返したそのものではない。そこで dict でも属性でも読み書きできるようにし、
**どちらでもなければ何もせず素通し**する（推測して壊すより素通しが正しい）。
最初の1回だけ `result shape = ...` を残すので、次のプレイで形も確定する。

これで**「ゲームが `return_after_completion` を受けるか」も同時に判明する** ―
4回とも LLM が選ばなかったので、まだ一度も試せていない唯一の項目。

### 3.11 アイテム説明欄が閉じた後も残る（新種・2026-07-28、計測を仕掛けた）

ユーザー報告 + 画像（`Pictures/Screenshots/floating item.bmp`）。**所持品を閉じて
戦闘に入っているのに、アイテム説明の箱が画面に浮いたまま**だった（テスト用アイテム
「新しいアイテム」／攻撃力500／説明が `testtest…` のもの）。「表示されることがある」
＝**間欠**。

##### 分かっていること

- **表示・非表示は `opacity` で行われている。** `out/item_detail.log` の写しで
  `ItemDetailBox ... opacity=0`、子の Widget/Label はいずれも `opacity=1.0`。
  つまり箱の `opacity` を 0/1 で切り替えて見せ隠ししている
- したがって症状は「**1 に上げた誰かが 0 に戻していない**」

##### `109_` は原因ではない（切り分け済み）

`109_fix_item_detail_autosize` が触るのは `size` / `pos` / `size_hint` /
`pos_hint` だけで、**`opacity` には一切書き込まない**。唯一 `clamp` が箱を窓の
内側へ移動するが、`opacity=0` の箱を動かしても見えるようにはならない。
`109_` を止めても症状は変わらないはず（切り分けたいなら
`_109_fix_item_detail_autosize.py` にリネームして1回再現させる）。

##### 仕掛けた計測（`208_`）

誰が `opacity` を上げ下げしているのかはコンパイル済みで読めないので、
**プロパティの変化そのものを見張る**（`box.bind(opacity=...)`、読み取り専用）。
`out/item_detail.log` に出る:

```
[時刻] opacity -> 1.0 | box=... pos=... size=... parent=... | from <呼び出し元の連鎖>
```

再現したときの読み方:

| ログの形 | 意味 |
|---|---|
| `-> 1.0` の後に `-> 0` が無いまま所持品を閉じた | **戻す側が呼ばれていない**。`from` に出ている「上げた側」の対になる関数を探す |
| `-> 0` は出ているのに見えている | 別の箱が残っている（`box=` の id を突き合わせる）か、親ごと生きている（`parent=`） |
| 何も出ない | 見張りが掛かる前（`update_content` を一度も通っていない箱）。`watch_opacity` の掛け方を変える |

**戦闘に入った瞬間が怪しい。** 画像は戦闘画面で、直前に所持品を開いていた。
再現手順の候補は「所持品を開いてアイテムにマウスを乗せたまま戦闘に入る」。

---

## 4. 運用上の取り決め

### プロキシとの併用（ユーザー判断・2026-07-25）

**InstantaleLLMProxy は `schema_compact` を ON のまま動かし続ける。** mod が先に圧縮すると
マーカー（`{'$defs':` 等）が消えるので、正常に効いていればプロキシ側は何もしない。裏を返すと:

> **`llm_proxy.log` に `[COMPACT]` / `[DEDUP]` / `[EVENTLOG]` が出たら、
> それは mod が取りこぼした経路。**

二重に適用しても結果が変わらないので無害で、**漏れの検出器として使える**という判断。多重起動抑止だけは
所有者調停が競合しうるが、そちらは未実装なので現状は問題ない（実装したらプロキシ側を
`singleton_enabled=0` にする）。

### 次回起動時の手順（忘れやすい）

```powershell
cd "$env:USERPROFILE\Desktop\InstantaleMods\InstantaleModLoader"
python watcher.py
```

> **注入はプロセスと一緒に消える。ゲームを起動するたびに注入し直すこと。**
> 一度これで 1 セッション分のデータを失っている。
