# VERIFICATION LOG: 実機・実データでの検証記録

最終更新: 2026-08-13

[VERIFICATION.md](VERIFICATION.md) の §2。何をどう確かめたのか（実測値・ログの抜粋・
件数）を1件ずつ残してある。**溜まる一方の記録なので、現在地を知りたいときは
VERIFICATION.md の §1 を見ること。**

- どこまで確かめてあるかの一覧と、未確認項目の確認手順は VERIFICATION.md（§1 / §3 / §4）
- 「なぜそう実装したか」は TECH.md 側
- 節番号は2冊で通し。`§3.x` などこの文書に無い番号は VERIFICATION.md を指す

---

## 2. 実機・実データでの検証記録

### 2.1 Kivy 終了時クラッシュ（2026-07-25、決着）

前回は「クリーン終了したがログが無く、効いたのか踏まなかったのか不明」だった。
呼び出しレベルの計測を追加して再度終了操作を行い、今度は実際に発火した:

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
| ガードの発火 | - | 2 回（`wm_pen` / `wm_touch`） | バグ自体は起きた |

「起きたが、クラッシュにはならなかった」が揃ったので効果が確定。
**ガードは毎回発火するわけではない**（記録の 47 件も日を跨いでばらけている）。
そのため `100_` は呼ばれるたびにログを出し、「効いた」と「今回は踏まなかった」を
後から区別できるようにしてある。
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

残っている疑問は、そもそも難易度 80 の NPC がどこで生まれるのか。クランプ発生時の
ログがその頻度を示す。

### 2.3 プロンプト肥大化対策

実経路での発火（DEDUP / EVENTLOG）:

```
[EVENTLOG] quest_referee_event_evaluate_new: dropped 5 turn(s), kept 3 | 2005 -> 620 chars (saved 1385)
[EVENTLOG] quest_referee_event_resolve:      dropped 5 turn(s), kept 3 | 2115 -> 730 chars (saved 1385)
[DEDUP] removed 1 duplicate block(s) ['idx1 system 8250c'] | 4 -> 3 msgs, 20248 -> 11998 chars (saved 8250)
[DEDUP] removed 2 duplicate block(s) ['idx1 system 8250c', 'idx2 system 8250c'] | 5 -> 3 msgs, 28498 -> 11998 chars (saved 16500)
```

スキーマブロックは最大3コピーまで増殖することを確認。EVENTLOG は監査出力
（before/after の先頭・末尾）でターン境界と前置きの扱いが正しいことを目視確認済み
（前置きが空文字列のケースで先頭の区切りが正しく再現されている）。

クエスト序盤で既に蓄積が始まっている:

```
quest_referee_event_evaluate_new: quest_event_log str chars=1699 turns=7
quest_referee_event_resolve:      quest_event_log str chars=1813 turns=7
```

COMPACT のオフライン検証（`<ゲームdir>/output_data/` の 12,067 件 ＝ ゲーム自身が
保存した `messages` を mod の関数に通した結果。66 マネージャ種 / 46,931 メッセージ）:

| 項目 | 結果 |
|---|---|
| 埋め込みスキーマを検出 | 7,967 件（`$defs` 有り 4,097 / 無し 3,870） |
| 解析失敗 | 0 件 |
| 誤爆（`title`+`type:'object'` でない dict を掴んだ） | 0 件 |
| フィールド名 / enum 値の欠落 | 0 種 / 0 種 |
| 2回通すと結果が変わる / 1メッセージに2個目のスキーマ | 0 件 / 0 件 |
| 合計 | 16,508,011 → 4,529,299 文字（72.6% 減） |

削減率は 56%（`create_look`）〜79%（`vacation_scene_generator`）。TECH.md の
「平均約4割」（プロキシ側の記録）より大きいのは分母の違い（あちらはリクエスト本文全体、
こちらはスキーマを含むメッセージ）。

COMPACT の実機検証（2026-07-25、pid 10744 の実プレイ 33 リクエスト）:

```
合計 67,475 -> 17,870 文字（削減 49,605 / 73.5%）  オフライン実測 72.6% と一致
最大 6,974 -> 1,869（クエスト系）  最小 182 -> 51
発火した site: chat 33 / payload 0
```

> `payload` 側は1件も発火しなかった。ストリーミング経路は
> `_post_with_model_loading_retry` を通らない。プロキシと同位置（payload）だけに移植して
> いたら何も起きないまま「移植した」と報告するところだった。保険として足した `chat`
> 側が結果的に本命だった。

監査ログ（`out/prompt_bloat.log`、最初の5回のみ全文）に実スキーマが残っている:

```
Skill: name, description, element, skill_type:∈{physical,magical,hybrid,other},
       effects:InstantDamage|InstantHeal|TextStatusEffect|BuffEffect|DebuffEffect[], max_uses...
```

### 2.4 BGM 偏り是正（実セーブ・実アセットに対して）

外部 41 件＋mod 内蔵 18 件、全通過:

- 全エリアで `folder == SIZE_ALIAS[area["size"]]` を満たす・全曲がディスク上に実在
- 無音エリアは `size` の示すフォルダに割り当てられる・2回通しても結果が変わらない
- 曲の使用回数のばらつきは全カテゴリで 1 以内
- 各世界の全エリアが異なる曲を得る（peld: 22→37、vestia: 22→37、dos: 23→33）
- 3世界合計の到達曲数 47/97 → 57/97、到達 mood 21/37 → 23/37
- シミュレート 300 世界で town の 9 mood 全てに到達（締め出しゼロ）
- Astergrave（無音5エリア）の dry-run で 12→20 曲、town 1→3 mood

セーブ難読化のラウンドトリップは、ライブ5世界すべてで復号→再暗号化がバイト単位で一致。
`tools/rebalance_saved_bgm.py` は書き込み前に毎回これを検査し、一致しなければ拒否する。

ゲーム内実動作は未確認（§3.4）。

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

注入時の掃除が、その場でこれを直した（2026-07-26）:

```
[BGMFIX] sweep after injection: stopped stray track on channel 1; ... channel 7;
         restarted solemn/Ambient 7 Loop.mp3 on the app
mixer 8/8 busy -> 1/8 busy: [0]      app.music playing_on=1
```

渡されている物の正体も確定した（2026-07-27、自由会話からの戦闘1回）。`207_` に型と
id を出させたところ、`BattleEndInFreeAction` のインスタンスだった:

```
play_music_from_src('town/solemn/Ambient 7 Loop.mp3')
    target = BattleEndInFreeAction#21c9ee5bdc0 NOT-THE-APP
    caller = instantale.py:7993 <lambda>
[BGMFIX] orphan: solemn/Ambient 7 Loop.mp3 was attached to BattleEndInFreeAction
         instead of the app -- nothing can stop it now
```

この戦闘では聞く限り BGM は元に戻っていたが、`sweep after ...` は1行も出ていない
＝ 後始末は走っていなかった。ログを追うと `in_battle` が戦闘終了後も 1 のままで、
「戦闘中なら何もしない」の条件に引っかかって毎回黙って降りていた（GAME.md §2.10）。
戦闘終了フックからの予約を `trust_end`（フラグを見ない）に変更した。

> 「聞いて正常」は合格条件にならなかった。このときゲーム側が迷子として鳴らした曲が
> たまたま正しく聞こえていただけで、チャンネルは 2/8 に増えていた。判定は耳ではなく
> `mixer = n/8` で行うこと。

戦闘終了時の発火を確認（2026-07-27、`trust_end` 修正後の戦闘1回）:

```
11:20:32.692 play_music_from_src('town/solemn/Ambient 7 Loop.mp3')
             target = BattleEndInFreeAction#21dbad26bc0 NOT-THE-APP
             caller = instantale.py:7993 <lambda>
11:20:32.881 [BGMFIX] orphan: ... nothing can stop it now
             mixer  = 1/8 channel(s) busy: [0]
11:20:35.179 [BGMFIX] sweep after BattleEndInFreeAction.end_phase:
             handed solemn/Ambient 7 Loop.mp3 back to the app (channel 0)
```

3つの起点すべてが実機で発火した（注入時の掃除 / 保険の
`sweep after refresh_choice_buttons: stopped stray track on channel 1` / 戦闘終了）。
終了後のチャンネルは 1/8 で、その1本は app が握っている。

壊れているのは1経路だけだと確定した。同じセッションの通常戦闘では:

```
BattleEndManager.end_phase done
play_music_from_src('dungeons/mystic/melt.wav')
    target = InstantaleApp#21cf30c7a70 IS-APP        ← 正しく app を渡している
    caller = instantale.py:7958 <lambda>
```

`BattleEndManager`（通常）は `:7958` で正しく、`BattleEndInFreeAction`（自由入力・
会話から）は `:7993` で誤っている。症状が「会話から入った戦闘」に限られて
いた理由がこれで説明できる。

派生症状のうち、タイトル画面のものは迷子で説明がつく:

| 症状 | 説明 |
|---|---|
| タイトルに戻っても街のBGMが鳴り続ける（本来は無音） | `return_to_title` の `stop_music(app)` は `app.music` しか止めない。迷子は残る |

### 2.6 ロードすると戦闘BGMで始まる（`in_battle` の下ろし忘れ）

迷子とは別のバグだった（2026-07-27。「ロードすると戦闘BGMが流れる」が残っているのを
見つけて計測）。ロード処理が `in_battle` を見て曲を選んでいる:

```
13:42:16 play_music_from_src('musics/battle/1. Echoes of Valhalla.mp3')
         target = InstantaleApp IS-APP        ← 迷子ではない。app に正しく付いている
         flags  = in_battle=1
         caller = instantale.py:1458 <lambda>
（比較）  play_music_from_src('town/solemn/Ambient 7 Loop.mp3')
         flags  = in_battle=0
         caller = instantale.py:1460 <lambda>   ← 隣の行。if/else の反対側
```

そのフラグが 1 のままなのは、`106_` と同じマネージャの、同じ種類の書き忘れ:

| 終了マネージャ | `end_phase` 完了時の `in_battle` | 件数 |
|---|---|---|
| `BattleEndManager`（通常の戦闘） | 0（ゲーム自身が下ろしている） | 8/8 |
| `BattleEndInFreeAction`（自由入力・会話から） | 1（下ろし忘れ） | 9/9 |

`207_` のログ全件を数えた結果で、例外は無い。戦闘後に保存すると `in_battle=True` が
セーブに焼かれ、次のロードが戦闘曲の枝を引く。

戦闘の実体の有無は `app.current_enemy_dict` で判定できる（残骸のとき `len=0`。
`combat_log` は前の戦闘の本文が残るので使えない）。実測:

```
flags                  = in_battle=1
app.current_enemy_dict = dict(len=0, keytypes=-) keys=[]     ← 敵は居ない ＝ 残骸
```

注入時の掃除が、その場でこれを直した（`107_` がフラグ、`106_` が鳴っている曲）:

```
[FLAGFIX] injection: cleared in_battle (the game left it set)
[BGMFIX]  injection: in_battle was set with no enemies -- replaced the playing track
          with solemn/Ambient 7 Loop.mp3
→ flags = in_battle=0   mixer = 1/8 channel(s) busy: [0]
```

未確認は、戦闘終了時とロード時の発火（§3.1）。

### 2.7 遅延 import の保留＋当て直し（オフライン16項目全通）

後から現れるモジュールを狙う mod を用意し、以下を確認:

- `apply-error` にならず保留されること
- 現れた時点で当て直されて実際にフックが効くこと
- 層が積み上がらないこと（`__original__` の連鎖が常に1段）
- 監視が暴走しないこと
- 属性名の間違いは従来どおりエラーになること

> テストで identity 比較を使ってはいけない（`alias_scan` がテスト側の握っている
> グローバルまで張り替える）。TECH.md §4.1。

### 2.8 施設到着イベント（両モードとも実機で発火）

narration モード（宿屋・雑貨屋・闇市の3件）:

```
fire: テスト宿屋 (Test Inn) (inn) roll 0.01 < 1.00 speaker='テストNPC C'
generated in 0.4s: '「あら、いらっしゃい。こんな場所まで、何をお探しなの？」'
```

conversation モード（2026-07-26）。6回発火し、6回とも `conversation_starter` まで
到達した（＝会話フェーズが実際に始まった）:

```
fire: テスト闇市 (Test Market) (underworld_office) roll 0.15 < 1.00 speaker='テストNPC A' id='69'
launch: process_choice(ConversationStartManager, 'テストNPC A') npc_id='69'
rephrase: '<行動: 話しかける>' -> '<状況: テスト闇市に入ってきた<プレイヤー名>に、あなたの方から声をかけた…>'
```

確認が済んだので `CHANCE_OVERRIDE` は `None`（施設種別ごとの確率）に戻してある。
残るのは運用感の調整だけ（施設別の発生率と `COOLDOWN_VISITS` を実プレイの頻度に合わせる）。

この確認の副産物が2つあり、どちらも他の mod の土台になった。

- スレッドの扱いが確定した（`process_choice` はメインスレッド、`execute` は別スレッド）
- `in_shopping` が当てにならないと判明した（素の移動38回すべてで True。不発39件の
  うち38件がこれだった。GAME.md §2.6）

### 2.9 仲間と別れる（実機確認済み・4回外して到達）

ボタンの設置・確認画面の表示更新・別れの実行・初期位置への再配置まで実機で確認済み
（2026-07-26）。外した4点と、そこから確定した事実:

| 外した点 | 実機のログ | 確定したこと |
|---|---|---|
| 名簿の在り処 | `add_party_member('83' 'テスト仲間D') -> party=[]` | `app.party` は名簿ではない |
| 名簿の形 | `(no candidate found)` | `list` とは限らない。`dict` も受ける |
| 差し替えの順序 | 押下の2ミリ秒後に refresh、以後 refresh なし | 押下と同じ流れで差し替えると古い画面に戻される |
| 描画の経路 | `update_button_texts(list [...]) <- InstantaleApp` | 塗るのは HUD。`app.to_display_buttons` は監視対象ではない |

いずれも「セーブに出ている形＝実行時の形」と決めつけたのが原因。結論は GAME.md §2.8 / §2.2。

ゲーム本来の解散経路もこの過程で捕まえた（`303_` の前提）:

```
remove_party_member('71' 'テスト仲間C')
  from QuestEndManager.method_1 (instantale.py:6602)
  <- QuestEndManager.execute (instantale.py:6635) <- run (threading.py:953)
remove_party_member: party ['player', '71'] -> ['player']
observed: the game placed '71' at '<エリア11>のギルド' after its own removal
```

ただしこの2件では「初期位置」と「いま居る町のギルド」が同じ場所だったため、ログだけ
では規則を区別できなかった。ゲーム側の規則が `initial_location` であることは
実プレイで確認してある（2026-07-27。セーブでも `npcs['71'].current_location = '127'`）。

### 2.10 依頼受注の絞り込み（オフラインでライブ世界と照合）

ライブ世界で:

| | 結果 |
|---|---|
| mod の絞り込み（エリア7） | `[39, 43, 45]`（依頼 15/16/17） |
| ゲーム自身の `get_quest_difficulties(area, world)` | `[45, 43, 39]` |
| 判定 | 一致 |

mod は実行時にもこの照合をして、食い違ったら `quest_offer.log` に
`WARN difficulty mismatch` を残す。

`client_name` は実在 NPC と結び付いていないことも判明した（ライブ5世界・全114依頼で一致
0件）。したがって `FILTER_BY_NPC = True` の既定では、初対面の NPC の一覧は
「この話から依頼を作る」だけになる。GAME.md §2.7。

### 2.11 オフライン検証（ゲーム不要）

```powershell
python tools/tests/test_arrival_event.py    # 300_  55件
python tools/tests/test_quest_offer.py      # 301_  49件
python tools/tests/test_party_leave.py      # 302_  78件
python tools/tests/test_quest_end_guild.py  # 303_  45件
python tools/tests/test_quest_end_keep.py   # 304_  50件
python tools/tests/test_party_train_exp.py  # 306_  59件
python tools/tests/test_area_move_dungeon.py # 307_  130件
python tools/tests/test_battle_damage_display.py # 308_  72件
python tools/tests/test_office_pardon.py    # 309_  73件
python tools/tests/test_item_detail_autosize.py      # 109_  25件
python tools/tests/test_character_name_sanitize.py   # 110_  36件
python tools/tests/test_llm_prompt_replace.py        # 111_  76件（うち3件は output_data/ の実プロンプトと突き合わせ）
python tools/tests/test_ui_text_spacing.py           # 112_  23件
python tools/tests/test_ui_text_expand.py            # 113_  76件
python tools/tests/test_ui_input_focus.py            # 114_  24件
python tools/tests/test_ui_item_list_fit.py          # 115_  54件
python tools/tests/test_ui_party_expand.py           # 116_  88件
python tools/tests/test_message_text_integrity.py    # 117_  12件
python tools/tests/test_batch_message_render.py      # 118_  74件
python tools/tests/test_crime_attribution.py         # 119_  9件
python tools/tests/test_npc_name_dedup.py            # 120_  93件
python tools/tests/test_ui_character_sheet.py        # 121_  82件
python tools/tests/test_ui_conversation_log.py       # 122_  43件
python tools/tests/test_new_character_level.py       # 123_  27件
python tools/tests/test_npc_profile_memory.py        # 311_  220件
python tools/tests/test_shop_restock.py              # 312_  26件
python tools/tests/test_event_ability_check.py       # 313_  65件
python tools/tests/test_patch_registry.py            # ローダ本体（世代・設定・デバッグモード・共通部品）  190件
python tools/tests/test_state.py                     # state/ の保存先の決め方と壊れない書き込み 58件
python tools/tests/test_recon_archive.py             # 000_ リコンの退避                        34件
```

各本が何を通すかは、その本の docstring の冒頭にまとまっている（`tools\tests\test_*.py` を
開けば1画面で読める）。**ここには写さない**。検査を足すたびに二重に書き換えることに
なり、実際にずれる（TECH.md §6.1）。


偽の `on_button_press` は本物と同じく `getattr(__main__, cls_name)(app, *args)` を組んで
`process_choice` に渡す。自前ボタンに無害な spec を持たせる意味（mod 無しで押されても
害の無いクラスが起きる）がそこで確かめられる。

`test_quest_end_guild` は解散の検出をスタックで行うので、テストも
`QuestEndManager.method_1` の中から呼ぶ形にしてある（app のメソッドを直接叩くと本番と
違う経路になり、検出そのものを検証できない）。

> テストのクラスをグローバル名から派生させないこと（TECH.md §4.3）。
> 派生元は `BASES` の表に控える。

ローダ全体の読み込み確認:

```powershell
python -c "import sys; sys.path.insert(0,'runtime'); import instantale_modloader as l; print(l.boot('out/test/bootcheck'))"
```

`boot complete: 34/34 mod(s) applied` が出れば読み込み側は健全。mod を足したら
この数も更新すること。34 本は、デバッグモードを切ったときに読み込まれる公開ぶん
（同梱 56 本から、`debug` の 14 本と、本体が取り込んだ 8 本を伏せた残り）。
手元だけの MOD を `load_order.local.json` に載せている間は、その本数ぶん多く出る
（TECH.md §1.3）。

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

残る留保が1つある。直前に `mixer = 2/8 channel(s) busy: [0, 1]` が一度出ており、
2.3秒後の sweep で引き取られたが、sweep 後の mixer 読み取りがログの末尾に無いため
1本に戻ったことは未確認。セッション全体（00:02〜00:15）の読み取りは 0〜1本で、
この 2/8 以外に増加は無いので合格と見ているが、次の戦闘で 1 本のままなら確定。

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
| HUD 塗り替え | `to_display_buttons [...5件...] -> ['甘美なる平原の沈静化...', '未完の対話...', 'やめる'] via display_button_load+hud.update_button_texts` |

最大の未確認点だった「画面が実際に塗り替わるか」が通った（`via (nothing)` でも
`hud not found` でもない）。待機表示の復元も動いている
（`busy off: to_display_buttons ['.', '.', '.', '.'] -> [元の3ボタン]`）。

生成された依頼が会話の内容になっているかは、2件目
`未完の対話、あるいは沈黙の追跡` / `・先程の件、まだ話が終わっていない`（直前に会話を
中断していた）が会話を反映しており、差し込み文は効いている。

### 2.14 `OSError: [WinError 123]`: 名前が原因で画像が生成できない（2026-07-28、新種）

`out/live_crashes.log`。crash_log.txt の 114 件には無い新しいバグで、2回発生した。

```
OSError: [WinError 123] ファイル名、ディレクトリ名、またはボリューム ラベルの
構文が間違っています。:
'...\worlds\...\characters\試験人形「テストダミー"'
```

キャラクタ `id='101'` の名前 `試験人形「テストダミー"` が `「` で開いて
ASCII の `"` で閉じている。`"` は Windows のパス構成要素に使えない。

| 時刻 | スレッド | 経路 |
|---|---|---|
| 00:06:36 | Thread-111 (execute) | `start_battle` → `create_enemies_from_npc_id` → `generate_and_write_character_detail` → `generate_character_image` → `os.makedirs` |
| 00:11:20 | Thread-123 (generate_images) | `ConversationStartManager.generate_images` → 同上 |

バックグラウンドスレッドなのでゲームは落ちない。画像が生成されないまま無言で
失敗し、その NPC に関わるたび再発する（2回とも同一人物）。保存先ディレクトリが
実際に存在しないことを確認済み。

原因は、LLM が生成した名前への引用符の混入で確定。

#### 実装する側への調査結果

- `worlds/<世界>/characters/` のディレクトリ名はキャラクタ名そのもの
  （実データで確認。`「試作」のテストA` / `テスト・ネーム (Test Name)` のような形）
- 名前の唯一の入口は
  `scripts.characters:Character.__init__(self, name=None, id=None, ...)`。
  LLM 生成・プリセット・プレイヤー・セーブからのロードが全部ここを通る
- 名前からパスを組む箇所は5つある（`generate_and_write_character_detail` /
  `generate_character_image` / `generate_character_image_from_enemy` /
  `generate_enemy_image_from_character` / `delete_world_character_images`）。
  書き込みと削除で消毒がずれると別の不整合を生むので、入口で正すほうが安全
- 不正文字を含むディレクトリは Windows 上に作成できない ＝ 旧名のディレクトリは
  存在し得ないので、既存キャラクタを改名しても迷子は発生しない（安全側の根拠）
- `world_data.json` は暗号化されていて外部から読めない（先頭が `2L\x04\x1b...`、
  zlib/gzip でもない）。既存 `id='101'` の救済は注入後にゲーム内から行う必要がある
- 置換は消さずに全角へ写すのが穏当（`< > : " / \ | ? *` → `＜ ＞ ： ” ／ ＼ ｜ ？ ＊`）。
  Windows は末尾の空白・ピリオドも黙って切るので落としておくこと
- 残る懸念として、セーブ側 JSON には旧名が残り、次の保存で入れ替わる。その間、名前で
  突き合わせる処理があると食い違う。実測では突き合わせは id（`'101'`）で行われて
  いるように見えるが未確証なので、旧名は控えて置換は必ずログすること

#### 実装（`110_fix_character_name_path`、2026-07-28）

上の調査結果のとおり入口ひとつ（`scripts.characters:Character.__init__`）で名前を
正す。5箇所のパス組み立ては今までどおり「名前をそのまま使う」ので、書き込みと削除で
消毒がずれる余地が無い。

| 項目 | 内容 |
|---|---|
| 置換 | `< > : " / \ \| ? *` → `＜ ＞ ： ” ／ ＼ ｜ ？ ＊`（消さずに全角へ写す） |
| 追加で落とすもの | 末尾の空白・ピリオド（Windows が黙って切る）／制御文字 |
| 触らないもの | 予約デバイス名（`CON` `NUL` `COM1` …）と、消毒すると空になる名前。直すには名前を発明するしかなく、一度も観測していないので記録だけする（`107_` と同じ立場） |
| 既存の救済 | 注入直後・`load_game_new`・`start_game` の3か所で `app.world.characters` と `app.player` を掃く（`id='101'` はこれで直る）。保存はこちらから起こさず、ゲームが次に保存するときに入る |
| ログ | `out/character_name.log` に `[NAMEFIX] <場面>: id=... '旧名' -> '新名'`。旧名を必ず残す（上の「残る懸念」のため）。名前と同じ生文字列を持つ他の属性があれば、書き換えずに併記する |

オフライン検証は `python tools/tests/test_character_name_sanitize.py`（36件全通）。
最後の2件は実地で、§2.14 で落ちた名前そのものを使いこの OS 上で
`os.makedirs` を叩いている。直した名前ではディレクトリが作れ、生の名前では
今でも `winerror == 123` で落ちることを確認済み。実機での結果は §2.15。

### 2.15 `110_` 名前の消毒（2026-07-28、決着）

`out/character_name.log` に1行だけ出た:

```
[2026-07-28T01:18:05.185] [NAMEFIX] Character.__init__: id='101'
    '試験人形「テストダミー"' -> '試験人形「テストダミー”'
```

同じ時刻に画像が実際に出来ている（`%LOCALAPPDATA%\Darmabeko\Instantale\worlds\
...\characters\試験人形「テストダミー”\`、01:18）:

```
face_image.png 2,066  generated_image.png 1,290,917  no_bg_image.png 522,195
opponent_image.png  pixelated_image_original.png  prompts.json
reduced_color_image.png  reduced_color_image.orig.png        （8点）
```

判定に使った3指標（全て一致）:

| 指標 | 期待 | 結果 |
|---|---|---|
| `out/character_name.log` | 改名が1件記録される | `id='101'` の1行（旧名も残っている） |
| ディレクトリ名 | 末尾が全角 `”` | `…テストダミー”`（生成物8点入り） |
| `out/live_crashes.log` の `WinError 123` | 既存2件から増えない | 2 件のまま |

発火したのは `Character.__init__`（入口）で、注入時の掃除ではなかった。
注入（00:43）の時点ではこのキャラクタのインスタンスがまだ無く、01:18 に組み立てられた
ときに直っている。入口ひとつに置いた狙いがそのまま効いた形。ロード時の掃除は
「入口で既に直っている」ため無音（`load_game_new` の行は出ない）で、これは正常。

`-- not touching`（予約デバイス名 / 消毒すると空になる名前）は一度も出ていない。
§2.14 の「残る懸念」（次の保存までセーブ側に旧名が残る）は、`id='101'` が普段どおり
振る舞ったことで実害無しと確認できたが、突き合わせが id で行われていることの
直接の証拠ではない。旧名はログに残してあるので、食い違いが出たらそこから追える。

### 2.16 `108_` 売買画面の `IndexError`（2026-07-27、原因確定・救済は未発火）

> **2026-08-09、`superseded: main_024` で降ろした（ユーザー判断。§3.8.1）。**
> 既定では読み込まれない。この節は**原因の記録**としてそのまま残す。

会話から売買に入った瞬間に落ちた。`out/live_crashes.log` の MAIN CRASH:

```
instantale.py:1692   InstantaleApp.toggle_twin_inventory_window        situation='shop'
new_hud.py:2661      InstanTaleHUD.toggle_twin_inventory_visibility    item_id='item_1'
new_hud.py:379       InventoryGrid.place_existing_item                 new_x=1422.7  new_y=1052.855
new_hud.py:410       InventoryGrid.occupy_slots   grid_x=1 grid_y=5 x=1 y=6
                     IndexError: list index out of range   slot_index=25
```

`x` が `grid_x` のまま（幅1）で `y` だけ `grid_y+1` に進んだところで落ちているので、
縦2マス以上のアイテムが最下段に置かれ1マスはみ出した状態。`slot_index=25` は
`self.slots`（24マス）の範囲外で、`place_existing_item` が `is_valid_placement()` を
通さずに `occupy_slots` を呼んでいる。

修正後、はみ出しは一度も再現していない。`out/inventory.log` の 192 件はすべて
正常サンプル（`ok`。2026-08-09 時点）で、そこから正常時の寸法が確定した:

```
cols=4  rows=6  len(slots)=24  size=[259, 389]  spacing=[1,1]
所持品グリッド pos=[1150.5, 741.725]   売買グリッド pos=[943.3, 727.855] (situation='shop')
1マス=64px、2x2 のアイテムは 129px・current_slots=[17,21,18,22]
```

`grid_x` / `grid_y` / `slot_size` は `<missing>`（アイテム側の属性としては存在せず、
`current_slots` の添字で持っている）。救済経路そのものの実地確認は §3.8。

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

設計値が勝つケース（`box_h=500`）が同じログに並んでいるのが要点で、「長い文だけ
伸びる・普通のアイテムは 1px も変わらない」が実データで確認できている。

**横幅の拡張も観測できた（2026-08-09、§2.39）。** ログの書式へ幅を足した結果:

```
item='item_113' box=405x486 (design 324x486)   ← 横だけ伸びた
item='item_55'  box=405x531 (design 324x486)   ← 縦横とも伸びた
item='item_105' box=324x486 (design 324x486)   ← 短い説明は設計値のまま
```

324 → 405。これで `109_` は縦・横とも実機で確認済み。

### 2.18 クエスト1件を頭から終わりまで実測（2026-07-28）

`206_` と `203_` が仕掛かった状態で通常クエスト「テスト依頼B」
（`settlement_quest` / id 28 / 難易度2）を受注〜完了。目的は「討伐でないクエストが
成立するか」の判定材料を取ることで、結論は「プロンプトの差し替えだけで成立する」。

進行ループと `Literal` の推移は GAME.md §2.9 に移した（ゲームがどう動いているかなので）。
ここには判定に使った事実と、それが何を意味するかだけ残す。

| 観測 | 出所 | 意味 |
|---|---|---|
| `ReturnAfterCompletion` が1ターン目から毎ターン作られる（ボス健在でも） | `probes.log` 09:48:05 以降すべて | 完了はスキーマで塞がれていない。プロンプトの1文を差し替えれば討伐以外で完了できる |
| 残イベントが尽きた瞬間 `FieldEvent` モデルが union から消えた | `probes.log` 09:59:04 | 空 `Literal[]` を避ける分岐が実在する。敵0件でも同じなら落ちない見込み（未確認） |
| `Battle.enemies` が `Literal[7→6→5→1]` と減る | 同上 | `Literal` は残りであって辞書の `enemies` 全体ではない |
| 完了フェーズは `QuestEndManager(app)`、引数ゼロ | `quest_flow.log` 10:00:08 | LLM が `return_after_completion` を選ばなかった場合に MOD から完了させる手が残る |
| `QuestEventManager(app, event_name, enemies_info, event_turn)` | `quest_flow.log` 09:55:56 | ミニイベント単体の入口。戦闘を通さずに1回分の出来事を起こせる |
| クラッシュ・`Literal[0]` ともに0件、`live_crashes.log` は無傷 | - | この1周では既知バグをどれも踏んでいない |

この計測で戦闘なしクエストの設計が決まった（実装は開発中の MOD 側。TECH.md §2.6）。ゲームのコードにもセーブにも触らず、
生成（`random_quest_generator`）と進行判定（`quest_referee*`）の2つのプロンプトだけを
差し替える形にできる。敵とボスのデータは削らずに残す（`Literal` を空にしないため）。

### 2.19〜2.22（欠番）

開発中の MOD（9xx）の実機記録だったので、その MOD の `DOC.md` へ移した
（TECH.md §2.6）。番号は詰めていない。他の節から番号で参照されているため。

### 2.23 待機表示を共通部品へ移した（2026-07-28）

クエスト作成の待ち時間にも「…」を出す（`301_` の会話からの生成と同じ）。
生成は LLM を回すので実測 27〜352 秒かかり、何も出ないと画面が固まったように見える。

点のアニメーションの作りは `301_` が実機で測って作ったもの。ゲーム自身の
「クエストを探す」を1回押した記録から、`.` → `..` → `...` を 0.3 秒周期・
ボタン全枠・`is_button_enabled=False`・送信ボタン無効まで真似ている。
これは「ゲームがどう動いているか」なので `ui.Screen` へ移した（TECH.md §5.1）。
`301_` は `screen.busy_on` / `busy_off` を呼ぶだけになった。

呼ぶ側の設計判断だけ MOD に残してある:

- 生成に入る直前に出し、例外で抜けるときも必ず解く（出したまま抜けると操作不能）
- 掲示板を開き直す経路は `busy_off(restore=False)`。元の選択肢を塗り直すと
  一瞬だけ古い画面が見える（`301_` の教訓）

オフライン検証は 94 → 107 件。点のアニメーション自体は共通部品として直接
確かめている（偽ゲームは同期なので、生成の流れでは1コマも進まない。
本物は `generate_random_quest()` が別スレッドで止まっている間に Clock が回る）。

### 2.24 `111_` プロンプトの置換（2026-07-30 移植、2026-08-08 に全プロバイダで決着）

プロキシ（`Proxy.Rules.cs`）の置換機能をプロセス内へ移した MOD。ルールファイルの書式は
プロキシと同じなので、プロキシ用に書いたものは MOD のフォルダへコピーすれば動く。

置き場所は `mods/111_llm_prompt_replace/` の中だけ。`llm_replacements.txt` があれば
それを、無ければ同梱の `llm_replacements.default.txt` を読む。名前を分けているのは、
MOD の更新で利用者のルールが消えないようにするため（`make_dist.bat` は
`llm_replacements.txt` を除外する）。

最初は `settings\` と既存プロキシの置き場所も見ていたが、MOD 単体の部品は MOD の
フォルダで完結させる方針に合わせて廃止した（TECH.md §3.1.1）。外を見ることで実際に
2つ踏んでいる。1つは、古いプロキシのファイル（22行）が同梱ルール（29行）より
優先され、新しいルールが黙って使われないこと。もう1つは、場所が分からないと
リクエストごとに数十回 `stat` を叩くので、間引きの仕組みが要ること。固定にした
結果、後者は仕組みごと消えた。

#### 移植で効いた差: プロキシは JSON にした後のボディを見ていた

| プロキシが見ていたもの | プロセス内で見えるもの | 対応 |
|---|---|---|
| `\n`（2文字） | 本物の改行 | 置換後は必ず復号する。置換前は素の形と復号形の両方を登録 |
| `あ`（`ensure_ascii=True`） | `あ` | 同上（プロキシは逆向きにエスケープ版を足していた） |
| .NET の `$1` / `${名}` / `$&` / `$$` | Python の後方参照 | 読み替える。存在しない番号は警告して文字列扱い |
| 1 秒の正規表現タイムアウト | Python の `re` に無い | 照合に1秒以上かかったルールを以後捨てる（1回目は止められない） |

#### 実データでの照合（2026-07-30）

`output_data/` の 13,461 ファイル・51,897 メッセージに、同梱の `llm_replacements.txt`
（29 行 / 27 グループ）をそのまま当てた。

| 項目 | 結果 |
|---|---|
| 読み込み時の警告 | 0 件（`#memo:` 行を含む新しい書式のファイルでも 0 件） |
| 発火したグループ | 26 / 27（残る1件は `\n` を含むルールの素の形。復号形の方が当たっている ＝ 復号が無ければ死んでいたルール） |
| 置換が起きたメッセージ | 16,636 件 |
| 置換後に `105_` がスキーマを圧縮できたか | 8,859 / 8,859 件（素の状態と同数） |

同じ照合を古い 22 パターンの版でも通してある（21/22 発火・`105_` は 8,855/8,855 件）。
書式が増えても読み込みが壊れないことの確認になるので、ルールファイルを差し替えたら
この照合を通し直すこと。

適用順は最後の行が根拠で、`105_` より外側（`after`）に置いて圧縮前の本文を見せる。

#### 仕掛け先が5版で変わった（クラウド対応）

「APIキー経由では置換されない」という報告（2026-08-08）から、同じ日に v2 → v5 まで
動いた。落とし穴は毎回同じで、**送信の入口を名指しで包むと、プロバイダが変わった
瞬間に素通りする**（GAME.md §2.12。ローカルとクラウドはどちらか片方しか import
されない）。

| 版 | 包んだ場所 | 何が足りなかったか | オフライン |
|---|---|---|---|
| v1 | `LlamaCppClient` の3点 | クラウドはこの関数を通らない | 76件 |
| v2 | ＋ `any_server:send_request*` | Gemini は `request_llm_inference_gemini_test_streaming` だけを import する（recon dump で確認。any_server もローカルも `[not loaded]`） | 70件 |
| v3 | ＋ gemini の `send_request*` | 選択肢は他に OpenAI / Claude / Alibaba / 任意 OpenAI 互換がある。モジュール名指しでは、増えるたびに素通りが再発する | 72件 |
| v4 | **`llm_manager:send_request*`**（使われる送信モジュールからの from-import 別名） | 別名は**プロバイダの初期化時に生える**ことがあり、注入時に無ければ包めない | 72件 |
| v5 | ＋ 無かった別名を5秒ごとに見張り、生えたら包む（1時間で諦める） | ― | 76件 |

v4 が今の形。alias_scan が同じ関数を持つ全モジュールを張り替えるので、モジュール名を
知らないまま全プロバイダに届く。site（ログのプロバイダ名）はラップした元関数の
`__module__` から採る。**ローカル実行ではこの地点は素通し**（send_request は内部で
別スレッドに降りるため印が届かず、`chat` 側と二重抽選になる。llama.cpp の送信
モジュールが import されているかで見分ける）。

#### 実機で4経路とも発火（2026-08-08、決着）

| 経路 | 出た行 | 補足 |
|---|---|---|
| Gemini | `[REPLACE] gemini_test_streaming` / `_ns` の両境界から計6件 | 当たったのは同梱ルール（「口調: None →指定なし」等）。クラッシュ記録なし |
| OpenAI | `[REPLACE] openai` | site 名から `request_llm_inference_openai` と判明 ＝ **名指ししていないモジュールに自動で届いた** |
| Claude | `[REPLACE] claude` / `claude_ns` | モジュールは `request_llm_inference_claude`（`anthropic` SDK 直・既定 `claude-sonnet-5`） |
| ローカル（llama.cpp） | `[REPLACE] chat` のみ | `llm_manager` 境界からは何も出ない ＝ 二重抽選の歯止めが実機で機能。同じ回で v5 の見張りも発火し（注入の25秒後に `late-armed on ... send_request`）、**別名の後生えはローカルでも起こる**ことが確定 |

未実測は Alibaba と任意 OpenAI 互換サーバーだけで、設計上は同じ別名を通る。

運用で2度踏んでいる。**プロバイダを切り替えたら再注入**（切替後に再注入が抜けて
いて、その間のプレイは 111 なしで動いていた。`[RULES]` が出ていないことで判別）。
**注入の版は armed 行の形でしか見分けられない**（v3: `request_llm_inference_...`、
v4: `llm_manager:...`）。再注入せずに会話した時間帯はプロセスに残った前の版のフックが
効いていた。

残る限界は、境界で見えるのが呼び出し側の渡した `message` だけで、send_request の
**中で**足される部分には当たらないこと。Gemini では `SCHEMA_IN_PROMPT = True` の
スキーマ文（`_schema_instruction`）がこれに当たる（GAME.md §2.12）。

確かめ方は `out\prompt_bloat.log` に `[RULES] 読込`（起動時）と `[REPLACE] <site>`
（会話1回で出るはず）が出ること。`[SKIP]` が出るのは確率付きルールを書いたときだけ。

#### ルールの見直し（2026-08-11、コードは無変更）

`output_data/` の全件（15,557 ファイル・59,447 メッセージ）に同梱ルールを当て直して
1行ずつ検分した。**既存 29 行は全ルールが存命**（0件は `\n` 入りルールの素の形 ＝
復号形の双子だけ。設計どおり）。`空のリストを返す。- lawfulness_loss` だけ最終発火が
古いが、これは改行漏れ形（76 件）と改行あり形（519 件・現役）の**2種のプロンプトが
併存**しているためで、当たる方が低頻度なだけ。`True` / `False` の広い当て方も、当たる
文脈はスキーマの説明文と『マスターのこれまでの処理』の repr だけで誤爆は無かった。

当てた後に残る同種の問題を走査すると、句点の直後に `- 項目:` や `【見出し】` が改行
なしで続く形が20種類・延べ2,800件超（直近30日）残っていた。ルールの増殖を避けるため
**列挙を正規表現3本に畳んだ**。手順は「列挙で全数検証 → 正規表現が当たるものを全期間で
完全列挙 → 旧新の出力を全メッセージで突き合わせ」の三段:

| 追加・整理 | 内容 |
|---|---|
| `regex:。(}?)(- [A-Za-z_]+:)` | 箇条書きの改行漏れ。全期間で当たる13種すべて本物（既存2行を吸収し、列挙が拾えなかった `player_attempt` / `skill_usefulness` も拾う） |
| `regex:。(【(?:指示\|基本情報\|…)】)`（見出し13名の Whitelist） | 見出しの改行漏れ。`。【見出し】` は全期間14種で、**文中の参照は `【既に記録した事実】` の1つだけ**。それを外した13名を名指しする（今後この形を見つけたら名前を足す） |
| `regex:記述する。(\d\. )` | 番号付きリストの改行漏れ（全期間3種のみ） |
| リテラル 2 行 | `- 現在エリア: None` → `不明`、`- 構造の説明: None` → `なし`（置換後の言葉が要るので正規表現にしない） |

等価性の証明: 列挙版（55行）と正規化版（31行）を全 59,447 メッセージに当てて突き合わせ、
**改行以外の差 0・列挙が当てて正規表現が逃した箇所 0**。差が出た 128 メッセージは全て
正規表現側だけが拾った正しい改行漏れ。重さも実測して、旧 1.27s → 新 0.75s
（グループ 54 → 30）と**むしろ半減**した（リテラル1本より正規表現1本は重いが、本数が
減る方が効く）。

利用者ファイルにも同じ整理を反映した。1点だけ順序の罠があり、
`発言者名は不要です。【現在の状況】` の控えのリテラルは個人ルール
`「」や発言者名は不要です。=>…` より**前**に置く必要がある（あちらが先に動くと間に文が
挟まり、見出しの正規表現が当たらなくなる）。ファイル内の `#memo:` に残した。

> 読み方の注意。§2.43 のとおり **2026-08-09 19:19 以降のクラウド経路の記録は
> `111_` 適用後の姿**なので、この集計の発火数は実際より少なめに出る。存命判定には
> 影響しない（当たる ＝ 原文がまだ出ている、の証明はそのまま成り立つ）。

#### 効果で3種に分けて、効かない1種を無効化した（2026-08-11）

| 種類 | 中身 | 効果の見立て | 扱い |
|---|---|---|---|
| ① 指示の書き換え | NPC改善2行・生成文言改変6行・要約1行 | **モデルに依らず効く**。指示追従の良いクラウドほど素直に反映される。要約ルールは実測された欠陥（GAME.md §2.25: 8ターンが118字に潰れ確定事項が消える）への対処 | 残す |
| ② JSON安定化 | `True=>true` ほか4行 | **効かない**。ローカルは grammar が構造をトークン単位で強制し、クラウドも構造化出力はプロバイダ側が保証する（GAME.md §2.12）。応答の記録はパース済み dict なので「壊れた率」の実測もできず、効果を示す材料が無い | `#offtab:` で無効化 |
| ③ 誤字・改行漏れ | 正規表現3本＋リテラル数行 | **保険**。クラウドは崩れた改行を難なく読み分けるのでほぼ無意味。小型ローカルモデルで理解が改善する余地はあるが未実測。維持費3行・数十µs なので残す | 残す |

②は**消さずに `#offtab:`** にした。行は読み飛ばされる（実行時の費用は 0）が、構造化
出力を保証しない任意の OpenAI 互換サーバーを使う場合だけは意味が戻るため、`#tab:` に
書き換えれば復活できる。無効化後は同梱 26 グループ・利用者 29 グループ、オフライン
76 件全通（`105_` の圧縮も維持。あちらのパーサは `True/False/None` と
`true/false/null` の両方を読む）。

未測定として残るのは③の実効果だけ。測るなら、記録済みの同じプロンプトを同じモデルに
ルールあり／なしで通して応答を比べる A/B が要る（`instantale_modloader.llm` から
直接呼べる）。①は実プレイで効果が確認できている。


### 2.25 `112_` 本文の行間（2026-07-31、画面から採寸）

起点は実機のスクリーンショット1枚（低地居住区の診療所。2561x1440）。
本文を表示座標から実寸に戻して測ると:

| 測ったもの | 実寸 |
|---|---|
| 文字の大きさ | 31px 前後 |
| 行の間隔（同じ段落の中） | 70px |
| 段落の間隔 | 138px（＝行の間隔のちょうど2倍） |

読み取れたことが2つ。

- 段落の間には空行が1行入っている（間隔が2倍ちょうど）
- その本来の行間そのものが広い（Kivy の既定 `line_height=1.0` なら
  31 × 1.3〜1.4 ＝ 42px 前後になるはずが 70px 出ている ＝ ラベルの `line_height` が
  1.6 前後に上げてある）

「会話などの場合は一部間隔が狭まる」という報告は、
空行を挟まない行が続いているところ。つまり狭い方が本来の行間。

対処は両方に当てる（片方だけでは足りない）。ゲームが持っている本文は触らない。
`display_text` は本文そのもの（会話履歴・セーブに入る文字列と同じ出所）なので、
`update_display_text` が塗り終わった後のラベルの `line_height` と `text` だけを
整える。倍率は絶対値ではなくゲームの値に対する比で持ち、設計値はラベル自身に
控える（注入し直すたびに掛け直して縮み続けるのを防ぐ）。

#### 実機（同日、`out/text_spacing.log`）

発火した。画面でも行間が変わったことを確認済み（利用者の確認）。ラベルの特定に成功し、
採寸からの推定より1つ広い値が出た:

```
label Label at hud.text_display (match=2) line_height=1.8 design=1.8
      font_size=27 texture=[1340, 3738] text_size=[1340.8, None] len(text)=750
label Label at hud.text_display (match=2) line_height=1.44 design=1.8
      font_size=27 texture=[1340, 1776] ...
```

| | 値 |
|---|---|
| 本文のラベル | `hud.text_display`（`Label`。GAME.md §2.3 に転記した）|
| `line_height` | 1.8（採寸からの推定は 1.6 だった）→ 既定 0.8 倍で 1.44 |
| `font_size` | 27（推定は 31）|
| 750文字の本文の高さ | 3738 → 1776（行間 0.8 倍と空行の除去の合計。約 2.1 倍縮んだ）|

一致は完全一致では取れなかった。当たったのは `match=2`（ラベルの `text` が
`display_text` で終わる）で、ゲームは塗るときに前へ何かを足している。
完全一致だけを条件にしていたらこのラベルは1回も見つからなかった。
本文のラベルを文字列で探す MOD を書くならここが要点になる。

残る未確認は、詰め具合が適当かどうか（`LINE_SCALE=0.8` / `BLANK_LINES=0` は
こちらが決めた既定で、読みやすさの評価はしていない）。
`0.7`〜`0.9` の範囲は GUI から変えられる。

### 2.26 `113_` 本文の表示域の拡張（2026-08-01）

`112_` が行間を詰めても、本文の枠は変わらない。長い応答はスクロールしないと
読めないままなので、押している間だけ枠を広げる切り替えを足した。

広げる相手は本文のラベルではなく、それを載せている入れ物。高さはゲームが
`update_label_height()` で決めているので（GAME.md §2.3）、ラベルの高さを
こちらで決めても次の1文字で塗り直される。入れ物を広げれば同じ仕組みのまま行数が増える。

| 判断 | 理由 |
|---|---|
| 入れ物は `scroll_y` / `do_scroll_y` を持つ最初の親（型では見ない） | `scroll_enabled` / `_on_scroll_resize` から ScrollView が居ることは分かるが、クラス名は実測していない（GAME.md §1.3） |
| 元の寸法一式は入れ物のウィジェット自身に控える | MOD 側の変数に持つと、注入し直したときに広げた後の寸法を設計値として控える（`112_` が `line_height` で踏んだ罠） |
| `pos_hint` があるときは位置に触らない | FloatLayout がアンカーを寸法に追従させるので、サイズだけ変えればゲームの寄せ方のまま広がる |
| 折り返し幅はまずゲームの `update_text_display_size()` に任せる | ビルドごとの決まりを知らずに済む。追従しないときだけ「枠の幅 − 設計の左右余白」を入れる（`109_` と同じ立場で寸法を発明しない） |
| ボタンは選択肢の枠（実測4枠）を使わず HUD へ直接足す | 1枠を常時潰すと遊ぶ手数が減る |
| ボタンのフォントは本文のラベルから写す | Kivy の既定（Roboto）に日本語が無く、写さないと文字が豆腐になる |
| ボタンはキャラの欄の上（既定）。その欄は枠の右隣に並ぶいちばん大きい入れ物として位置から探す | 画面の隅はどれも既存の表示と重なる。画像（`source`）で立ち絵を探すと、会話に入った瞬間に別の絵へ乗り換えてボタンが飛ぶ（実機3回目） |

#### 実機1回目（2026-08-01）: 本文は広がったが枠線が付いてこなかった

スクロールする入れ物を広げると本文は増えたが、画面に見えている枠線は元の
大きさのままで、増えた行が枠の外（画像や状態表示の上）へはみ出した。

枠線は `add_border(widget)` / `update_border` がウィジェットの `pos` / `size` に
束ねて描くので、束ねられている相手が別のウィジェットだとこちらが広げた入れ物には
付いてこない。相手をクラス名で決めつけずに直す。今その枠と同じ場所に同じ大きさで
置かれているもの（枠線・背景・影は定義上そこに重なる）を仲間とみなし、寸法ではなく
倍率を配って一緒に広げる。兄弟は HUD の直下でも見る（枠が HUD に直接乗っている
ビルドがある。実機がこれだった可能性が高い）。

同時にボタンの既定位置を画面の隅から立ち絵の上へ移した（利用者の指定）。

#### 実機2回目（2026-08-01）: 上下に伸びた／ボタンが画面の上端に貼り付いた

| 見えたこと | 原因 | 直し方 |
|---|---|---|
| 枠が上下（中心から）に伸び、下の入力欄にかぶった | 枠が `pos_hint={'center_x':0.5,'center_y':0.5}` を持っていて、高さを増やすと中心から両側へ伸びる | 位置を一切入れない。Kivy の `y` は下端なので、`y` 据え置きで `height` だけ増やすと下端が動かず上へ伸びる。幅の既定倍率も 1.0（横には広げない）にした |
| ボタンが立ち絵の上ではなく画面の右上のまま | `frames.MISSING` は文字列（`"<missing>"`）。`frames.attr(hud, "image_portrait_right")` の戻り値をそのまま照合の材料にしたので、`source` を持たないウィジェットが全部「立ち絵」に一致し、いちばん大きいもの（背景）の上＝画面の上端に置かれていた | 既定値を明示して `None` を受け取る。加えて、立ち絵が見つからないときの受け皿として枠の右隣に並ぶ入れ物を使う（実機の並びは下の帯に「左・入力欄・本文の枠・右（立ち絵と HP）」） |

`frames.attr` の既定値は `MISSING` で、これは文字列である。存在確認には
`is frames.MISSING` を使い、値そのものを他と照合するなら既定を `None` にする。
`112_` / `109_` は `is` で見ていたので踏んでいなかった。

#### 実機3回目（2026-08-01）: 会話に入るとボタンが画面の左へ飛ぶ

立ち絵を `hud.image_portrait` と同じ `source` を持つウィジェットとして探していたが、
会話に入ると相手の絵を持つ別のウィジェット（画面左の大きな立ち絵）に一致して、
ボタンがそちらの上へ移っていた。絵は場面ごとに差し替わるので、これを手がかりに
置き場所を決めると画面が変わるたびにボタンが動く。

置き場所は位置で決める。 キャラの欄＝本文の枠の右隣に並ぶいちばん大きい入れ物は、
会話中も同じ場所に居る（画面下の帯の並びは変わらない）。画像で探す経路は、
右隣が無いビルドのための予備に降格した。幅は広げる前の値で見る
（広げた枠を基準にすると、右隣に居るはずの相手が枠の中に入ってしまう）。

置き場所が変わったときは `out	ext_expand.log` に
`button placed above the panel at (x, y, w, h)` が出る。ボタンが飛ぶ症状は
この行でしか追えないので、記録は残してある。

#### 実機4回目（2026-08-01）: 幅倍率 1.0 でも少しだけ横に広がる

`size_hint` を丸ごと `(None, None)` に外してから測った寸法を入れ直していたため、
`size_hint_x=1` でゲームが決めていた幅が「その瞬間の実測値」に固定され、数 px 動いて
見えていた。触る軸は伸ばす軸だけにする。横に広げないなら `size_hint_y` だけ
外し、幅・`size_hint_x`・折り返し幅（`text_display.text_size`）には一切触らない。
戻すときも、触った軸だけ戻す。

ゲーム自身の採寸 `update_text_display_size()` を呼ぶのも横に広げるときだけになった
（幅の話だから）。高さだけ変えるときはゲームの採寸と競合する余地が無い。

#### 実機6回目（2026-08-02）: アイテムを持ち物へ移せない・装備できない

全 MOD 適用下で、新しく手に入れたアイテムを持ち物へ移す・装備する操作が効かなくなり、
この MOD だけを無効にすると直った（利用者の報告）。

原因は足す先。素の `InstanTaleHUD` の子は `FloatLayout` 1枚だけで
（`out/text_expand.log` の `frame neighbours:` に出ている）、そこへボタンを直接
足すと子が2つになる。`scripts.hud.new_hud` には `get_current_screen_root()` があり、
この種の関数は「画面の最初の子」を返す。つまりドラッグ中のアイテムの置き場所や
`InventoryItem.get_all_inventories()` の起点が、ゲームのレイアウトではなく
こちらのボタンにすり替わる。

直し方は、ボタンをその `FloatLayout` の中へ足すこと（`host_of`）。HUD 自身の
子は1枚のまま保たれる。ゲームを起動したまま新しい版を注入した場合は、HUD 直下に
残っている古いボタンを外してから足し直す。

> 他人の画面に物を足すときは、**その画面の子の並びを変えない**。並びを手がかりに
> している処理はこちらからは見えず（Nuitka でソースが読めない）、確かめようもない。
> 同じ理由で、`ui.Screen` 経由の選択肢ボタンは `app.buttons` の中身だけを触っている。

#### 実機5回目（2026-08-01）: 窓の大きさを変えるとボタンの位置が崩れる

塗り直し（`update_display_text` / `update_button_texts`）は本文や選択肢が変わった
ときにしか来ない。窓だけ変えられると MOD 側は何も気付けず、ボタンは古い座標に
取り残され、控えている「触る前の寸法」も古い窓の値のまま残っていた。

`Window.on_resize` を直に拾うようにした。拾った後の順番が要点で、ここを外すと
古い窓の寸法を新しい設計値として控えてしまう（そうなると元に戻せない）:

| 順 | やること | なぜ |
|---|---|---|
| 1 | `stale` を立てて寸法を測らない | 組み直しの途中の値を控えないため |
| 2 | 次のフレームで畳んで（`size_hint` を戻して）控えを捨てる | 先に戻さずに控えだけ捨てると、次に控えるのが「広げた後の寸法」になる |
| 3 | さらに次のフレームで控え直して当て直す | 間の1フレームでゲームが自分の寸法を入れ直す |
| 4 | `RESETTLE_DELAY`（0.3秒）後にもう一度当て直す | レイアウトが1フレームで終わらないビルド用。`upkeep` は何度呼んでも同じ結果 |

注入し直したときは、窓に結んだ手を外してから結び直す（`WINDOW_ATTR`）。
外さないと注入のたびに手が積み重なる。

安全弁として、広がっている枠からは絶対に控え直さない（`design_of`）。畳むのに
失敗した・控えだけ失われた状態で控え直すと、広げた後の寸法が設計値になって二度と
元に戻せなくなる。その状態になったら枠には触らず、WARN を1回出す
（`the text frame is expanded but its original size is gone`）。同じ理由で、
組み直しのときに控えを捨てるのは畳めたときだけにしてある。

#### 実機（2026-08-01）: 確認済

4回目の版で想定どおりになった（利用者の確認）。押すと本文の枠が下端を保ったまま
上へ伸び、もう一度押すと元の寸法に戻る。ボタンは通常画面でも会話中もキャラ欄の
すぐ上に留まる。

作りを疑うときに見る場所（ログ）:

| 見るもの | 合格 |
|---|---|
| `out\text_expand.log` の `frame ... design size=...` | 本文の枠が見つかっている。型名と寸法がここに出る |
| 同 `frame family: ...` | 一緒に広げる相手（枠線・背景）が拾えているか。ここが1件だけなら枠線は付いてこない |
| 同 `frame neighbours: ...` | 枠のまわりの親・兄弟とその矩形。仲間の見つけ方（`RECT_SLACK` / `RECT_RATIO`）を直すときの唯一の資料 |
| 同 `expanded to WxH (design WxH), N widget(s)` | 押下で枠が広がった（N が一緒に動いた数）|
| 画面 | ボタンが押せる位置に出ているか（他の表示と重なっていないか）／広げた枠が選択肢やアイコンを隠しすぎないか |
| ログの `WARN` | `the game's own sizer resets the text frame` が出たら、ゲーム側の採寸と噛み合っていない合図（そのまま動くが、幅の追従はこちらの計算になる） |
| `no label is showing the narration` / `there is no frame to grow` | 枠の見つけ方が実機と違う。この2つが出たら作りを直す |

残る未評価は倍率（幅 1.0 ＝ 広げない / 高さ 4.0 ＝ 上限）が読みやすいかどうか。こちらが決めた既定で、GUI から変えられる。

### 2.27 `114_` 自由入力の欄からフォーカスが外れる（2026-08-01、実機未確認）

利用者の申告: 自由入力を1回送るたびに入力欄からフォーカスが外れ、次の一言を
打つのに毎回クリックし直す。

外れること自体は Kivy の作りどおりで、ゲームの不具合ではない。送信ボタンを押す＝
入力欄の外を触るので `TextInput` は `focus = False` になり、応答を待つ間はゲームが
`hud.text_send_button.disabled = True` で入力を塞ぐ（GAME.md §2.4）。

| 判断 | 理由 |
|---|---|
| 送信の経路は捕まえず、焦点が外れたことを見て戻す | 送信の経路（ボタン・Enter・待機表示）はビルドで変わりうるが、「入力欄が焦点を持っているか」はどのビルドでも同じ場所に出ている |
| 入力欄は `focus` と `insert_text` の両方を持つウィジェットとして探す（型名でも属性名でもない） | GAME.md §1.3。選択肢のボタンも `text` は持つが `insert_text` は持たない |
| 欄が複数あるビルドでは送信ボタンと同じ親に居るものを選ぶ | 名前入力の窓など別の欄に焦点を移すと、そちらが打てなくなる。決まらなければ幅がいちばん広いもの（自由入力の欄は帯の幅いっぱい） |
| 応答待ち・ポップアップ・入力の封鎖中・他の欄が焦点を持っている間は戻さない | ゲームが意図して塞いでいる場面に割り込まない |
| 待機が明けた合図は送信ボタンの `disabled` が False に戻ること | 「送った直後」を捕まえるより素直で、応答が返るまで焦点を先取りしない |
| 戻すのは次のフレーム（`REFOCUS_DELAY` 0.05秒後） | ゲーム側の「外す」処理がまだ途中のことがある。Kivy の焦点は触った側の後始末で最後にもう一度動く |
| 短い間に戻しすぎたら手を引く（2秒に12回で5秒休む） | ゲーム側が毎フレーム外すビルドがあれば取り合いになり、画面が固まったように見える。手を引けば最悪でも「今までどおり毎回クリックする」に戻るだけ |

オフライン検証 24件全通（`tools/tests/test_ui_input_focus.py`）。実機では未確認で、
次の2点はどちらも実測していない仮定:

* 入力欄が `focus` / `insert_text` を持つウィジェットとして HUD の木から見つかること
* 応答待ちの間、`hud.text_send_button.disabled` が実際に出入りすること（§2.4 の実測は
  待機表示の観測で、`disabled` の変化を監視できるかは未確認）

最初の起動で `out\input_focus.log` を見る:

| 見るもの | 合格 |
|---|---|
| `input <型名> width=... (of N candidate(s) on the HUD)` | 入力欄が見つかっている。`N` が1なら選別の余地なし |
| `refocused after blur` / `after enter` / `after send finished` | どの経路で戻したか |
| `stood down for 5.0s (...)` | ゲーム側と取り合いになっている。出るなら `REFOCUS_DELAY` を上げるか「外れたら戻す」を切る |
| ログの `WARN` `no text input on the HUD` | 入力欄の見つけ方が実機と違う。出たら作りを直す |

### 2.28 `115_` アイテム一覧が画面の上へはみ出す（2026-08-02）

利用者の申告と画面写真: 入力欄からアイテム一覧を出すと、アイテム数が多いと枠が
上にはみ出る。表示個数は画面解像度にもよるが、はみ出ないようにしてほしい。

#### 一覧の正体（実測。`out/item_list.log` と `out/recon/modules.json`）

```
list ToolListPopup(486.2, 80.9, 926.6, 900.0) rows=18 parent=FloatLayout
     sample=['新しいアイテム', '名前が長すぎてアイテム名', '新しいアイテム3']
```

| 分かったこと | 出どころ |
|---|---|
| 一覧は `scripts.hud.new_hud:ToolListPopup`。`GridLayout` の派生 | `modules.json` の `bases` / `mro`。`__init__(self, callback, tool_text_list=[...])` |
| 行は直接の子で、幅は 175 前後・高さ 57 前後。一覧の幅は 926.6 と行よりずっと広い | `item_list.log` と画面写真（窓 1876x1000） |
| アイテム説明の吹き出しは `ItemDetailBox`（`FloatLayout` 派生・`parent=Button`・中身は `['', '', 'item_detail:']`） | 同上 |

`GridLayout` だと分かった時点で直し方は1つに決まる。`cols` を増やす。位置も
行の大きさも変えずに高さが 1/N になり、幅にも余裕がある。

#### 実機で2回外している（記録）

1回目: 画面を崩した。 最初の版は「縦に積まれた文字のあるウィジェット」を一覧とみなし、寄せる → 行を
詰める → `ScrollView` に入れる、の3段で収めようとした。実機での結果:

* アイテム説明の吹き出しを一覧と誤認し、4行の箱を次々と `ScrollView` に入れた
  （`scrolling: 4 row(s) in a 474px frame` が何度も出ている）
* 一覧そのものも、まだ組み上がる前の `(0, 0)` を「触る前の位置」として控え、
  そこへ戻したうえで窓の内側へ寄せた ＝ 一覧が画面の下端いっぱいまで動いた
* `spacing` を持たない相手では、行の位置から間隔を逆算する経路に落ちる。その
  経路では「レイアウトが走ったか」の判定が必ず真になる（自分で作った値と
  突き合わせているため）。ここが素通りの穴だった

利用者の指摘（初期位置のまま、列を2列に）が、そのまま正しい直し方だった。

2回目: 一覧を1つも掴めなかった（`nothing to fit after the icon press` だけが
出る）。列にする版で「入れ物の高さが中身と噛み合っていること」を、レイアウトが
走ったかの判定に足したのが原因。実機では行が並び終わっているのに
`ToolListPopup` の矩形は `(0, 0, 926.6, 78.75)` のままという瞬間があり、
その条件は永久に成立しなかった。

3回目（現在）は行だけを見る: 行の位置と高さが噛み合っていれば組み上がったと
みなし、下端も左端も行から採る。入れ物の矩形は、こちらが書き換える項目
（`cols` / `height` / `width`）を書き換える直前に1つずつ控えるだけにした ―
まとめて控えると、控えた瞬間の矩形が組み上がる前の値だったときに、戻すと
そのおかしな値が入る。

加えて、掴まなかった相手と理由をログに残すようにした（`skipped ... (...)`）。
1回目・2回目とも、なぜ掴めた／掴めなかったのかがログから読めず、実機で1往復ずつ
潰すことになったため。

3回目: `cols=2` は当たったのに見た目が変わらなかった（ログに `-> cols=2` が
出ているのに1列のまま）。同時に測った値で理由が見えた:

```
popup ToolListPopup cols=1 rows=None spacing=[0,0] padding=[0,0,0,0]
      size_hint=[1,1] pos=(0,0) size=[926.64, 78.75] minimum_height=1026
      rows=18 row=173x57 bottom=0
```

* `size_hint=[1,1]` なので一覧の寸法は親（入力欄の帯）と同じ 926x78.75。
  中身が要求する高さ 1026 とまるで噛み合っていない
* それでも行は 0〜1026 に並んでいる ＝ 行の位置をこの格子が決めていない

4回目（現在）は3手順にした。

1. `cols` を入れ、`rows` が入っていたら外す（`cols x rows` の枠が足りないと
   Kivy はレイアウトを行わない）
2. 箱の高さを中身ぶんにする（下端は動かさないので伸びるのは上へ）
3. `VERIFY_DELAY` 秒後に、行が実際に何列に並んでいるかを数える。1列のままなら
   行の位置を自分で入れる

判定に使うのは実測した列数だけで、ビルドの作りは決めつけない。

| 判断 | 理由 |
|---|---|
| 列を増やすだけにする。位置・行の高さ・文字には触らない | 見え方が今までのままで、高さだけが減る。`ScrollView` への移し替えも要らない（移すとゲームの `親.remove_widget(一覧)` が空振りする） |
| 相手は「列を持てる入れ物」（`cols` と `minimum_height` を持つ）に限る | 名前で弾くためではなく、この直し方が成り立つ相手そのものだから。`ItemDetailBox`（`FloatLayout` 派生）はここで落ちる |
| ボタンの中に居る入れ物は触らない | 吹き出しはアイテムのボタンにぶら下がっている（`parent=Button`） |
| 行の大半が空でない文字を持つこと | 吹き出しの中身は `['', '', 'item_detail:']` |
| 「使ってよい高さ」の基準になる下端は、触る前の値を1回だけ控えて使い続ける | 高さを変えると下端が動くビルドでは、測り直すたびに列数が行ったり来たりする |
| 高さは入れ物自身の `minimum_height` を優先。かけ離れた値は使わない | まだ組み直されていない（前の列数のままの）値を掴まないため |
| 列数には上限（既定 4）と、窓の幅から入る数の頭打ちを置く | 横に長い一覧は読みにくい。上限でまだはみ出すならログに出して利用者に上げてもらう |

オフライン検証 54件全通（`tools/tests/test_ui_item_list_fit.py`）。偽の一覧は実測値
（`GridLayout` 相当・幅 926.64・行 175x57・窓 1876x1000）に合わせてあり、
吹き出し（列を持てない・ボタンの中・中身が空）を触らないことも検査に入れてある。

#### 実機で成立（2026-08-02）

20件が2列で表示され、画面内に収まることを利用者が確認。決定打はログの
`rows_prop=20`。ゲームは `rows`（件数）を入れていた。`cols` だけ変えても格子は
`cols x rows` のまま噛み合わず、`rows` を外して箱の高さを中身ぶんにして初めて通った。
並び順は「左の列に先頭から、下から上へ。埋まったら右の列の下端から」で、
1列だったときの並びをそのまま保つ。これは `place()`（こちらで行を置く経路）の形。

残っているもの:

* スキル一覧（`press_skill_icon`）は未確認
* 一覧を開いたまま窓の大きさを変えたときの追従（列数の計算は開いた時点の窓の高さで
  決めている）
* 記録の上限に当たって `after:` の行が落ちていた（同じ形の一覧は1回しか書かない
  ようにし、上限も 200 に上げた）

次に開いたとき `out\item_list.log` を見る:

| 見るもの | 合格 |
|---|---|
| `list ToolListPopup(...) rows=N ... -> cols=2` | 列数を決めて当てた |
| その行が出ない（`nothing to fit ...` だけ） | 1列で収まっている、または一覧の見分け方が実機と違う |
| `still taller than the window at 4 column(s) ...` | 列の上限に当たってまだはみ出している。「列数の上限」を上げる |
| `ItemDetailBox` の行が出る | 出てはいけない（1回目の版の誤認。出たら見分け方を直す） |
| `skipped <型名> (<理由>)` | 掴まなかった相手とその理由。一覧が収まらないのにこれしか出ないなら、その理由が見分け方の直し先 |
| `after: cols=2 ... columns_seen=2 ...` | 折り返った（格子が並べた）|
| `the grid did not wrap; placed N row(s) into 2 column(s) by hand ...` | 格子が並べなかったので自分で置いた。これでも1列に見えるなら、行の位置を持っているのは別の誰か |

### 2.29 `209_` / `210_` 計測: シーン記述エンジンと NPC の退場（2026-08-02）

「街を舞台にした調査もの（猫探し・事件調査）を作れるか」を決めるための計測。
結論は [GAME.md §2.21 / §2.22](GAME.md) に移してある。ここには測り方と、
外した点を残す。

#### 決めたかったこと

| 問い | 答え |
|---|---|
| シーン記述エンジンは MOD から使えるか | プログラムは `world_dict['free_facility_programs']`＝セーブの中。書ける |
| フラグは施設をまたげるか | またげない。`facility.config['free_flags']` で施設ローカル |
| 報酬・日数・戦闘は届くか | 届く。`effect` 各種と `call_phase`（5クラスとも `get_phase_class` で解決） |
| NPC の退場に `is_dead` が使えるか | 使える。`Character.config['is_dead']` |
| 印を立てると参照が切れるか | 切れない。名簿には残り、読む側が飛ばす |

#### 3回外している（記録）

1回目: 定数を引く先を間違えた。 `_DSL_SPEC` と `_EXAMPLE_PROGRAM` を実行側
（`scripts.free_facility`）から引いていたが、実際は生成側
（`llm_manager_free_facility`）にあった。DSL の説明文と実例は「LLM にプログラムを
書かせる」ためのものなので、実行側に無いのが道理だった。リコンのモジュール名を
最後まで読んでいれば防げた。

2回目: `on_ready` で世界を触った。 世界の調査と NPC の census を
`ctx.on_ready` に預けたが、これは全 MOD の適用直後に走る。そのときはまだ
タイトル画面で、`app.world_dict` も `world.characters` も `None` だった。

```
--- world survey: where do programs live? ---
    app.world_dict is NoneType
    facilities scanned: 0
--- census: world.characters is NoneType ---
```

さらに悪いことに、片方は成否を見る前に「実行済み」の印を立てていたので、
以後2度と走らなかった。

> `on_ready` は「ローダの仕事が終わった」であって「世界が載った」ではない。
> **世界を読む処理は `on_ready` に預けない。** 載っていなければ印を立てずに
> 諦め、プレイヤーが何か押したときに試し直す（`on_button_press` を包み、
> 印が立った後は辞書引き1回で降りる）。

3回目: 注入し直しでは印が落ちないことを忘れた。 1回目・2回目を直した後、
ゲームを起動したまま注入し直してもらったが、`sys` に立てた印はプロセスが
生きている限り残る（TECH.md §3.6。そう作ってある）。壊れた版が立てた印が
効いたままで、直したはずのダンプがまた出なかった。

> 一度きりの処理を直したら、**確認にはゲームの再起動が要る。**
> 注入し直しでは検証にならない。

#### 値まで出さないと意味が無い項目がある

census は最初 `frames.repr_value` で状態を出していたが、これは辞書をキーだけに
畳む（トレースバックの1行に収めるための既定）。結果:

```
config=dict(len=4, keytypes=str) keys=['level_of_detail', 'is_player', 'is_dead', 'difficulty_level']
```

`is_dead` という項目が在ることは分かるのに、真偽が読めない。 小さい辞書は
値ごと出すようにして初めて `is_dead=True` が取れた。同じ理由で `STEP_TYPES` が
18件中12件までしか出ていなかった（220文字で切られていた）。

> 計測で「在るか」を見るのか「何か」を見るのかを先に決めること。
> 既定の要約はトレースバック向けで、計測向けではない。

#### 答えの出た実データ

```
_flag_store(scope='facility') -> dict keys=['visited_fire']       2回とも facility
FreeFacilityManager(app, 'free_10')                               施設 id 10 と対応
facility 0/10 config = {"program_id": "free_10",
                        "free_flags": {"visited_fire": 1}, "concept": "…"}
world_dict keys = [..., 'free_facility_enabled', 'free_facility_programs', ...]

characters=35 facilities=100
  facility owners: 24            ← 消すと壊れる
  is_dead=True: 1 ['34']         current_hp=-966、それでも roster x1
  referenced by nothing: 0
```

`is_dead=True` の NPC が名簿に残ったまま、ゲーム内では会話に出てこないことは
実プレイで確認済み（利用者の申告）。

#### 残っている未確認

MOD から `FreeFacilityManager(app, program_id)` を起こせるか。通れば
`facility_type == 'free'` 以外の普通の施設でもシーンを走らせられる（実測した世界に
`free` 施設は3つしか無い）。決着は次の §2.30。

### 2.30 シーン記述エンジンは普通の施設でも走る（2026-08-02、成立）

§2.29 で残った1点 ―「MOD から `FreeFacilityManager` を起こせるか」― の決着。
使い捨ての実験 mod（`3xx_scene_engine_test`、確認後に削除）で実機に通した。

#### 結果

```
lint_program -> list []                    指摘ゼロ。手書きの16ステップが通った
launch #1 at facility '2' type='inn'       宿屋
serving our program to mod_scene_test_1    差し替え成立（入店＋選択2回で計3回）
_do_llm {'type':'llm','prompt':'この施設の様子を2文で描写せよ。…'}   約14秒
_flag_store(scope='facility') -> dict keys=[]
_end_sequence reached
after run: facility config = keys=['level_of_detail', 'free_flags']
```

| 確かめたこと | 結果 |
|---|---|
| 普通の施設で走るか | 走る。エンジンは `facility_type` を見ていない |
| MOD からマネージャを起こせるか | 起こせる。`ui.Screen.start_phase` → `process_choice` にそのまま乗る（マネージャが `execute(choice_text)` を持つ） |
| セーブに書かずに済むか | 済む。`_lookup_program` を包んで自前のプログラムを返せばよい |
| `lint_program` は使えるか | 使える。戻り値は指摘の `list`。空なら合格 |
| 普通の施設でフラグが持てるか | 持てる。受け皿の無い宿屋にも `free_flags` が新設された |
| LLM ステップは通るか | 通る |

選択肢を押すたびにマネージャへ入り直す形（`{'kind':'goto','label':'look'}` と
`{'__session': [...]}` を渡す）も、`free` 施設のときと同じに動いた。

#### 前提が1つ覆った

§2.29 の時点では「`free` 施設は3つしか無い」を制約として数えていたが、
制約ではなかった。`program_id` を渡せばどの施設でも走るので、
宿屋・ギルド・役場・商店のどこにでも自前のシーンを置ける。

#### 分かった副作用（本番の設計に効く）

`flag_set` を1つ使っただけで、宿屋の `config` に `free_flags` が生えた。

```
launch 時:  facility config keys: ['level_of_detail']
run 後:     facility config keys: ['level_of_detail', 'free_flags']
```

プログラム自体は世界に書かない。フラグのほうは施設に書かれてセーブに残り、
MOD を外しても消えない（ゲーム自身の項目と同じ形なので壊れはしない）。

> `_lookup_program` を包む方式なら、**そもそも `flag_set` が要らない。**
> 渡すプログラムを MOD が毎回組むので、分岐の前提はプログラムに焼き込めばよく、
> DSL 側は `var`（訪問中だけ）で足りる。セーブを一切汚さずに済む。

これが実験でいちばん大きい収穫。詳細は [GAME.md §2.21.3](GAME.md)。

### 2.31 残骸の掃除が他の MOD のボタンを消していた（2026-08-03、コードで確定）

既存 MOD の一斉点検で見つけた。v1.2.1 で入れた `ui.Screen.prune_stale`
（セーブから復元された印無しの自前ボタンを落とす）が、MOD をまたいで発火する。

#### 経路（すべてコード上で確定・オフラインで再現）

| 順 | 起きること | 場所 |
|---|---|---|
| 1 | `309_` が罰金の確認画面を出す | `office_pardon.py` `screen.apply_buttons(app, [pay, cancel], "confirm")` |
| 2 | その中で `app.refresh_choice_buttons(reset_page=True)` が呼ばれる | `ui.Screen.apply_buttons` → `refresh` |
| 3 | `302_` のフックが `orig` の前に掃除を走らせる | `leave_party_in_conversation.py` の `refresh_choice_buttons` |
| 4 | `309_` の cancel が3条件すべてに当たり、消える | text=`やめておく` / spec=`JustSetButtonToNormalPhase` / `mod_party_action` を持たない |

`CANCEL_LABEL = "やめておく"` が `302_` と `309_` で完全に同じ文字列だったのが
直接の原因。利用者から見えるのは「役場で罰金の確認画面を出すと、最初から
キャンセルが無い」で、`302_` のログ（`dropped 1 stale button(s)`）を見ないと
原因に辿り着けない。

```
[309 confirm] before: ['1000ゴールドを納める', 'やめておく']
[309 confirm] 302 removed: ['やめておく']
[309 confirm] after : ['1000ゴールドを納める']
```

#### 直し方（2段。片方だけでは塞がらない）

| # | 何を | どこ |
|---|---|---|
| 1 | 「どの MOD の印も無い」を条件に足す。セーブに焼かれるのは text と spec だけ ＝ 復元された残骸は印を1つも持たない。逆に印があれば今この場で誰かが挿したもの | `ui.Screen.marked_by_a_mod` / `ui.MARK_PREFIX`（印は全て `mod_` 始まり） |
| 2 | 掃除に使う文言をその MOD にしか無いものだけにする。`302_` の `OUR_LABELS` から `CANCEL_LABEL` を外した | 各 MOD の `OUR_LABELS` |

1 だけでは、ゲーム自身が `やめておく` を出していた場合に消してしまう（印では
見分けようがない）。2 だけでは、文言が偶然揃った次の MOD で再発する。

#### ついでに塞いだ「残骸」の残り（`307_` / `309_`）

`prune_stale` が入っていたのは `301_` / `302_` だけで、自前ボタンを挿す残り3本は
重複判定が印だけだった。

| mod | 挿す場所 | 露出 |
|---|---|---|
| `309_` | `InstantaleApp.refresh_choice_buttons`（ゲームが組んだ一覧を塗り直すだけ） | `301_` と同じ。役場でセーブ → タイトル → ロードで二重化する |
| `307_` | `AreaMoveCofirmation.update_button_display` の後 | 同上。保険 |

`307_` は実機で症状を観測したものではない（組み直さないビルドがあっても
壊れないように、というだけ）。コード側のコメントにもそう書いてある。

#### 検証

オフラインのみ。実機未確認だが、症状は画面を見ればすぐ分かる（役場の確認画面に
キャンセルが出るか）。

| 追加した検査 | どこ |
|---|---|
| 他の MOD の印が付いたボタンを落とさない／`marked_by_a_mod` の真偽 | `tools/tests/test_quest_offer.py` |
| `302_` が `309_` の確認画面から1枚も消さない／汎用語を掃除に使っていない | `tools/tests/test_party_leave.py` |
| 復元された残骸を差し直す（二重にならない・残る1枚は印を持つ）／他 MOD・ゲームのボタンを消さない | `tools/tests/test_office_pardon.py` |
| 残骸を掴む・掃除が仕掛けてある・他 MOD のボタンを落とさない | `tools/tests/test_area_move_dungeon.py` |

3つの修正はそれぞれ、戻すと対応する検査が落ちることを確認済み（`ui.py` の1行を
外すと `test_quest_offer` が、`309_` の掃除を外すと `test_office_pardon` が、
`302_` に汎用語を戻すと `test_party_leave` が失敗する）。

`python tools/check_mods.py` は問題 0、オフライン検証 19 本すべて通過、
`boot complete: 30/30 mod(s) applied`。この 30 本はデバッグモードを切ったときの
公開ぶんで、手元に未公開の MOD を置いている場合はその本数ぶん多く出る。
`load_order.local.json` を使っている旨は `notes` に1行出る（TECH.md §1.3）。

#### この点検で問題が無かったもの

| 見たこと | 結果 |
|---|---|
| HUD への `add_widget` | `113_` 以外に無し（v1.2.1 の事故は他へ波及していない） |
| 設定の選択肢（空文字・末尾空白・cp932 外 / NEC・IBM 拡張） | 全 mod.json を機械検査して 0 件 |
| 画面に出す文字列の環境依存文字 | 実コードには無し（`308_` の `▶` 等はコメント内の反例表のみ） |
| グローバル乱数 | `104_` / `111_` / `300_` / `307_` すべて専用の `random.Random` |
| `frames.MISSING` の値照合 | `is` と `not in (None, frames.MISSING)` のみ。既定値をそのまま比較材料にしている箇所は無し |
| `bind` の積み重なり | `113_`（Window）・`114_`（ウィジェット）とも結び直す前に外している |
| `PhaseSpec` への自前クラス名 | 全 MOD 既定の `JustSetButtonToNormalPhase` のみ |

残った懸念が1つ。`113_` の `host_of` は HUD の `children` の先頭を host に採る
（Kivy の `children` は新しい順）。素の HUD の子が `FloatLayout` 1枚だけ、という
実測に乗っているので、HUD 直下に一時的な子が増えている瞬間に `ensure_button` が
走るとボタンがそちらへ移り、その子が消えるときに一緒に消える。実機では未観測。

> この懸念は **§2.33 で解消した**（2026-08-03）。`116_` が同じ書き方を写していて、
> HUD へウィジェットを足す MOD が2本になった時点で成立する組み合わせになっていた。

### 2.32 `112_` が効かなくなっていた（2026-08-03、原因確定）

「行間が詰まっていない」という報告。MOD は入っていた（`modloader.log` に
`applied: 112_ui_text_spacing`）が、`out/text_spacing.log` が1行も無い ＝
本文のラベルを1回も見つけられていない。決め手はこの1行:

```
[2026-08-03T14:15:41.280] WARN  text spacing: no label is showing display_text;
                                leaving the text as the game drew it
```

原因は他の MOD との相互作用で、ゲームの更新ではない。`112_` はラベルを
「`display_text` と同じ文字列を持っているウィジェット」として探していた。そこへ
`117_message_text_integrity` が入り、長い本文を

    前置き + ［表示負荷を抑えるため、前の本文は省略］ + 末尾 1000 文字

に載せ替えるようになった。`117_` は `112_` より内側で走る（適用順が先）ので、
`112_` が見る頃にはラベルの text は `display_text` と一致も包含もしない
→ 探索が空振り → 200 回で警告を出して以後は何もしない。

直し方: 名前で引く。 ラベルが `hud.text_display` であることは
2026-07-31 の実測で分かっており（GAME.md §2.3）、`117_` / `118_` / `113_` は
最初から名前で引いている。`112_` だけが初版の探索を使い続けていた。
文字列の探索は名前で引けなかったときの予備に降ろした。

| | 前 | 後 |
|---|---|---|
| 探し方 | `display_text` と一致するラベルを探す | `hud.text_display` を引く |
| 本文を書き換える MOD | 共存できない（探索が外れる） | 影響を受けない |
| 属性名が変わった場合 | 探索で拾える | 予備の探索で拾える（据え置き） |

教訓: 表示中の文字列を手がかりに描画先を探す作りは、その文字列を触る MOD が
増えた時点で壊れる。 一度実測で名前が分かったら、名前で引くほうへ移す。
検証は退行そのものを再現するオフラインの1件で押さえた（`test_ui_text_spacing`:
「本文を丸ごと載せ替えた状態でもラベルを見つける」）。

実機での再確認は未了。確かめ方は `out\text_spacing.log` に
`label Label at hud.text_display (match=name) ...` が1行出ること。

### 2.33 HUD への置き場所を「先頭の子」で選んでいた（2026-08-03、コードで確定）

§2.32 と同じ日の点検で見つけた、§2.31 が「残った懸念」として書き残していた
ものの続き。当時は `113_` 1本の話だったが、その後 `116_` が同じ `host_of` を
写していて、成立条件が揃っていた。

#### 何が起きうるか

`113_` / `116_` はどちらも HUD へ自前のトグルボタンを1枚足す。足す先は
HUD 直下ではなくその中の `FloatLayout`（HUD の子を増やすと
`get_current_screen_root` から見える相手が変わり、アイテムの移動・装備が壊れる。
2026-08-02 に実機で踏んだ）。その `FloatLayout` を、初版はこう選んでいた:

```python
for child in frames.attr(hud, "children"):      # ← Kivy の children は新しい順
    if frames.attr(child, "add_widget") is frames.MISSING: continue
    if child is frames.attr(hud, BUTTON_ATTR):  continue   # ← 自分のボタンだけ
    return child
```

先頭 ＝ いちばん新しい子なので、HUD 直下に何かが載っている瞬間はそちらを掴む。

| 掴む相手 | 何が起きるか |
|---|---|
| ゲームが一時的に出している窓 | その窓が消えるとき、こちらのボタンも一緒に消える（§2.31 が書いた懸念そのもの） |
| 他の MOD が HUD 直下に残したウィジェット | 相手のボタンの中へ入り込む。`113_` の古い版はボタンを HUD 直下へ足していたので、そこから注入し直すと `116_` がその中に入る |

除外していたのが自分のボタンだけだったので、2本目（`116_`）が入った時点で
相互に掴み合える状態になっていた。`113_` が自分を `FloatLayout` へ移した瞬間、
中に入っていた `116_` のボタンも一緒に連れて行かれる。

#### 直し方

規則を `ui.overlay_host` へ移し、両方がそれを呼ぶ形にした（同じ発見を2箇所に
書かない。TECH.md §6.1）。変えたのは2点:

| # | 前 | 後 |
|---|---|---|
| 1 | `children` の先頭から探す | 最後尾から探す。ゲームの `FloatLayout` は画面が組まれた時点で居る ＝ いちばん古い子 |
| 2 | 除外は自分のボタンだけ | `_instantale_` で始まる属性を持つウィジェット ＝ どの MOD が足したものも除外（`ui.added_by_a_mod` / `ui.MOD_WIDGET_PREFIX`） |

1 だけでは、ゲームが `FloatLayout` より後に作る子が居るビルドで再発する。
2 だけでは、ゲームの一時的な窓（印を持たない）を掴む穴が残る。

#### 検証

オフラインのみ。実機未観測（`113_` の古い版から注入し直すか、HUD 直下に子が
増えている瞬間に塗り直しが走らないと出ない）。

| 追加した検査 | どこ |
|---|---|
| 他の MOD のウィジェットを置き場所にしない／その中に入り込まない | `tools/tests/test_ui_text_expand.py` / `tools/tests/test_ui_party_expand.py` |
| ゲームの一時的な窓を置き場所にしない／その窓が消えてもボタンが残る | 同上 |

`ui.overlay_host` を先頭走査に戻すと、両方の検査が落ちることを確認済み
（`113_` は既存の「古い版のボタンが移される」2件も一緒に落ちる）。

#### 同じ点検で直したもう1件: `107_` の `hasattr`

`107_fix_battle_flag_stuck` が `hasattr(self, "in_battle")` で「渡されたのは
app か」を見ていた。`hasattr` を使わないという規則（TECH.md §6.3）は
`201_` のトリップワイヤを踏んでから決めたもので、`107_` はそれより前に書かれた
まま残っていた。`frames.attr(...) is not frames.MISSING` に置き換えた。

いま実害は無い（トリップワイヤが載っているのは `FreeInputStart` で、ここで
読むのは app）。`in_battle` を持たないビルドで `load_game_new` が別の型を
渡してきたときに、無関係な失敗ルックアップを1回起こすだけになる。同梱 MOD で
`hasattr` を使っている箇所はこれで 0 件（残る2件は「使うな」と書いた
`200_` / `201_` のコメント内）。

#### この点検で問題が無かったもの

§2.31 の一覧に加えて、今回見たもの:

| 見たこと | 結果 |
|---|---|
| 表示中の文字列でウィジェットを探している MOD | `112_` のみ（§2.32 で修正）。`113_` は名指しが先・`114_` は能力（`focus` と `insert_text`）・`115_` は能力（`cols` と `minimum_height`）で選んでいる |
| 残骸の掃除（`prune_stale`）を持つ MOD の文言の重複 | `301_` / `302_` / `307_` / `309_` の `OUR_LABELS` に汎用語・他 MOD との重複は無し |
| 自前ボタンを挿すのに `prune_stale` を通していない MOD | 無し（挿す6本すべてが通している） |
| 名前・ID がそのままファイルパスになる箇所 | MOD が `out/` へ書くファイル名は全て定数。唯一の可変（`311_` の世界名）は `safe_world_filename` を通している |
| LLM 経路のフック地点 | `102_` は `_apply_chat_template` 1点だが、そこは実機で発火が確認済み（§2.3 の DEDUP 行）。`105_` は `chat` + `payload`、`111_` はローカル3点＋クラウド（`any_server:send_request*`）2点。どれも二重に効いても結果が変わらない書き方。クラウドでは素通りになるが、実害があるのは `103_` の書き換えだけ（`102_` はゲーム側修正済み・`105_` は対象がローカル固有。GAME.md §1.8。`301_` の判定差し替えはマネージャ層なので効く） |
| 116_ の「他人が管理する状態を控えて書き戻す」 | 対象を帯の直接の子に限ってあり（`panel.coverable`）、ゲームの選択肢は掴まない |

### 2.34 本文の表示速度（2026-08-03、決着・修正まで確認）

報告は「表示速度の設定を変えても速さが変わらない」。MOD は無関係だった。
`211_probe_text_speed` で実測（`out/text_speed.log`、ゲーム内で 0.04 と 0.08 を
往復させた）:

| `app.text_speed` | ティックの間隔（実測） | 1秒あたり |
|---|---|---|
| 0.04 | 48〜50ms | 20 文字/秒 |
| 0.08 | 80〜83ms | 12 文字/秒 |

* 設定は届いている。 ゲーム内で変えた瞬間に `app.text_speed` が動いた
  （`text_speed 0.04 -> 0.08 (poll)`）。再起動も再注入も要らない
* 1ティック＝1文字（平均 1.03〜1.11、まれに 2〜3）
* MOD の負荷は無関係。 `update_display_text` に5本積まれた状態で、
  1文字あたり 0.3ms（最大 1.6ms）。間隔 50ms に対して 0.6% で、
  「重さで頭打ち」は完全に否定された

`text_speed` に載らない差（+8〜10ms）はフレーム境界への丸めで説明が付く。
60fps ＝ 16.7ms 刻みなので:

```
0.04 → 3フレーム = 50.0ms   （実測 48〜50ms）
0.08 → 5フレーム = 83.3ms   （実測 80〜83ms）
```

つまり最速でも「1フレーム1文字」＝ 60 文字/秒が下限で、そこは設定では超えられない。
この事実は GAME.md §2.3 に移した。

#### ただしワールドによって 1.6 倍遅い: こちらは MOD が原因だった

同じ `text_speed=0.04` のまま、ワールドを変えると間隔が 45.7ms → 66ms になる。
フックの中は 0.3ms のままなので、遅いのは `update_display_text` の外側。
`clock dt` まで伸びていた ＝ 遅れているのはフレームそのもの。

疑った「本文が長いから」は外れだった。`117_message_text_integrity` が末尾
1000 文字に載せ替えているので、ラベルは `len=1020` で頭打ちになっている。
代わりに出たのがフレームレートの乱高下（止まっていれば 80fps、打っている間だけ
17〜44fps）。

`kivy.uix.label:Label.texture_update` を包んで数えたら決着した:

```
tick x22  interval avg=63.5ms  repaint avg=0.3ms  render x2.86/tick avg=14.9ms  texture=[1340, 3549]
tick x44  interval avg=63.8ms  repaint avg=0.3ms  render x2.93/tick avg=15.2ms  texture=[1340, 3590]
```

1文字ごとにラベルを約3回、1回 15ms かけて作り直していた（2.86 × 14.9 ＝ 42.6ms
＝ 間隔 63.5ms の 67%）。内訳:

| 回 | 誰 | 要るか |
|---|---|---|
| 1回目 | Kivy 自身（テキストを代入した時点で次のフレームに予約される）| 要る |
| 2回目 | `112_ui_text_spacing` の `settle()` | 要らない |
| 3回目 | `117_message_text_integrity` の `settle()` | 要らない |

どちらも「ゲームが `texture_size` を見て高さを出すから、先に作り直させる」つもりで
呼んでいた。Kivy の作り直しのほうが先に走る（テキストを変えた時点で予約される＝
こちらの `schedule_once` より早い）ので、高さを出す時点の `texture_size` は既に新しい。
2本とも外した。テクスチャの作り直しは 3回 → 1回になるので、1文字あたり 45ms →
15ms の見込み。

ワールドで差が出ていたのもこれで説明が付く。代金はテクスチャの大きさに比例し、
速いワールドは `[1340, 1776]`、遅いワールドは `[1340, 3590]` ＝ 倍だった。

この代金は今までの計測から漏れていた。 作り直しは `update_display_text` の中では
なく次のフレームで走るので、フックの中で測っている限り `repaint` には出てこない
（一般則は TECH.md §6.2 に入れた）。

#### 直した後の実測（同日 19:04、同じ遅いワールド）

```
tick x32  interval avg=38.9ms  repaint avg=0.2ms  render x0.97/tick avg=8.4ms  texture=[1340, 3795]
tick x60  interval avg=40.2ms  repaint avg=0.2ms  render x0.98/tick avg=6.5ms  texture=[1340, 3918]
```

| | 前 | 後 |
|---|---|---|
| 作り直しの回数 | 2.86 回/文字 | 0.97 回/文字（＝ Kivy のぶんだけ） |
| 1文字あたりの代金 | 42.6ms（間隔の 67%） | 8.1ms（21%） |
| ティックの間隔 | 63.5ms | 38.9ms |
| 打ち出し | 15.7 文字/秒 | 25.7 文字/秒（1.63 倍） |

`text_speed=0.04` の設定値（40ms）にそのまま乗った。 ワールドによる差
（45.7ms と 66ms）も消えている。テクスチャは `[1340, 3918]` と、遅かったときより
むしろ大きいのに 40ms で回っているので、大きさの問題ではなく回数の問題だった
ことが裏側からも確かめられた。

打っている間のフレームレートも改善した（実測の下限が 17.3fps → 54.0fps）。

体感が変わらなく見える理由として残るのは総量のほう。本文は 750 文字を超えるので、
0.04 でも 1メッセージに 37 秒かかる（0.08 なら 61 秒）。一段速くしても
「長い」ことは変わらないので、待ち時間の体感は動きにくい。速さを設定の下限より
上げたいなら、1文字ずつ送るのをやめる作り（`118_batch_message_render` の路線）が要る
― これは速度設定の問題ではなく別の機能。

### 2.35 NPC の名前が重複する（2026-08-05、オフライン検証済・実機未確認）

小さい LLM ほど同じ語を繰り返し引く。利用者の報告（Gemma 系）では完全一致より
表記ゆれのほうが多い。

| 形 | 例 | 見分け方 |
|---|---|---|
| 表記ゆれ | バルガス / ヴァルガス | `ヴァ`→`バ` のような拗音の綴り、長音、濁点を落とした鍵で比べる |
| 修飾語付き | 「隻眼の」バルガス | 括弧とその中身、先頭の `〜の` を落とす |
| 姓名の片方だけ一致 | バルガス・ドレイク | 姓名に割って、片方が姓名だけの名前に含まれるかを見る |

`120_fix_npc_name_collision` はこれを鍵の一致・包含・編集距離の3つで判定して改名する。

#### 付け直す名前は名簿から選ぶ（2026-08-05、2度の作り直しを経て）

| 版 | 付け直し方 | なぜ捨てたか |
|---|---|---|
| 1 | 元の名前の末尾の音を機械的に差し替える | `ヴァルガス` → `ヴァロガバ`。元の名前を綴り間違えたような名前になり、重複は消えても読み物として悪い |
| 2 | LLM に書かせる（職業・人物像・既存の名前の一覧を渡す） | 当たり外れが出る。検算を厚くしても「使えるが良くはない名前」は落とせない |
| 3 | 用意した名簿から選ぶ（いま） | 名前の質が入力（名簿）で決まる。乱数も推論も要らない |

名簿の形は `male` / `female` / `epithets` の3つ。読むのはこの3つだけで、他の鍵は
在っても黙って無視する（別の道具で作った名簿をそのまま置けるように）。同梱の
`npc.default.json` は3つの鍵しか持たない。

男女はセーブの `category` で決める。実データは8種（`young` / `middle-aged` / `old` /
`teenage` × 男女）で、`woman` は `man` を含むので女性を先に見ないと全員が男になる。

#### 確かめたこと（オフライン 83件）

- 上の3形が同じ鍵に落ちること、および別人を巻き込まないこと。後者のほうが
  重要で、`アレン・スミス` と `アレン・ジョーンズ`（姓名の片方だけ一致）、
  `ジル` と `ジン`（短い名前の1文字違い）、`ナナシ` と `ナシ` を分けられること
- 改名が素データ（`save_data_dict['npcs']` / `world_dict['npcs']`）まで届くこと。
  ここが届かないと次の保存で古い名前が戻ってくる
- 引くたび違う名前になること（同じ id・同じ名簿でも）。名簿は毎回まぜるので、
  前の方だけが繰り返し使われることもない（5件の名簿で全件が先頭に来ることを確認）
- 一度付いた名前は変わらないこと。改名は素データにも書くので、同じ NPC を
  `generate_character` と `Character.__init__` にもう一度通しても二度目の改名は
  起きない。乱数でも名前が落ち着くのはこれが根拠
- 同じ世界の中では重複しないこと（30人が同じ名前で来ても全員が別の名前になる）
- 乱数を MOD 専用の `random.Random` から引くこと。グローバルの `random` の列を
  ずらさないことを、種を打って前後の値で確かめている（GAME.md §2.11）
- 二つ名が既定 30% 前後に収まること（4000 回で計数）。0% で1件も付かず、
  100% で全部付くこと
- 名簿が読めないとき・使い切ったときに名前を発明せず元のまま通すこと。
  名簿が無いことは `WARN` で名指しする（黙って何もしない MOD が一番分かりにくい）
- 利用者の `npc.json` が同梱の `npc.default.json` より優先されること（TECH.md §3.1.1）

#### 実機で最初に見るところ

| 見る場所 | 期待 |
|---|---|
| `out\npc_name.log` | `generate_character: id=... 'ヴァルガス' -> ...` の行。`raw table(s)` が 1以上であること |
| `out\modloader.log` の `npc name dedup:` | `roster=npc.default.json (male=400 female=400); epithets=200 at 30%`。`no roster` なら名簿が読めていない |
| `raw table(s)` が 0 のまま | `npcs` の辞書に届いていない ＝ GAME.md §2.23 の前提が外れている。保存すると名前が戻るはず |
| 行が1つも出ない | そもそも重複が起きていないか、`World.generate_character` を通らない生成経路がある |
| `no free name in the roster` | 名簿を使い切っている。`npc.json` を足す |

#### 分かっていないこと

- 名前で NPC を突き合わせている処理があるかどうか（`110_` から引き継いだ未確証。
  だから既存 NPC は既定で触らない）
- 立ち絵のディレクトリ。改名した NPC の画像は名前のフォルダに入るので、
  既にいる NPC を改名すると、古い画像はそのまま残り新しい画像だけが別の場所へ行く。
  `FIX_EXISTING` を既定で切ってあるのはこれが理由

---

### 2.36 新規キャラが最初からレベル60（2026-08-09、本体が main_025 で修正して決着）

新しく作ったキャラクタが**経験値0のままレベル60**で始まる。

セーブを復号して読んだ実測（`saves\<世界>\savedata.json` と `backups\*.zip`）:

| セーブ | 経過日 | レベル | 経験値 | HP | 耐久 | 体力/上限 |
|---|---|---|---|---|---|---|
| ペルディション（2026-08-08 19:00・新規） | 0 | **60** | **0** | 832 | 16 | 10 / 10 |
| ペルディション（2026-08-08 19:27・新規・`214_` を入れて再現） | 0 | **60** | **0** | 780 | 15 | 10 / 10 |
| アーリ（2026-06-29・新規） | 0 | 1 | 0 | 88 | 18 | 10 / 10 |
| ヴァン（2026-07-21・新規） | 1 | 1 | 0 | 144 | 30 | 10 / 10 |
| アーリ（2026-07-22 22:26・新規） | 0 | 1 | 0 | 124 | 26 | 10 / 10 |

同じ手順の新規開始が、7月までは 1、8月8日は 60。**後から生えた壊れ方**。

#### バグと判断した根拠

1. **経験値が 0 のままレベル60**。レベルが上がったのではなく、最初から 60 で
   置かれている（`highest_cleared_quest_difficulty=0` / `party=[player]` /
   持ち物・装備は空 ＝ 1手も進んでいない）
2. **世界との段差**。始まりの町の冒険者は 8〜9（`config['difficulty_level']` 2〜3）、
   出ている依頼の難易度は 2 / 3 / 5。レベル60 はこの世界の最上位 NPC（81）に
   近い側で、雇用価格・敵の強さ・依頼の釣り合いが全部ずれる
3. **同じセーブの中で辻褄が合っていない**。これが決定的で、「既存の世界に
   合わせて強く始める」という設計ではないことを示す:

| 項目 | 値 | どのレベルの値か |
|---|---|---|
| `max_physical_integrity` | 10 | **レベル1**（下の表） |
| `max_hp` | 832 | **レベル60**（耐久 16 × 52） |
| `experience_level` | 60 | ― |

作成の途中で、レベル1として計算された値とレベル60として計算された値が混在して
いる。どこかで 60 が紛れ込んだ形。

#### 副産物: レベル → 体力上限の対応（実測）

同一プレイヤーを追ったセーブから拾ったもの（GAME.md §2.19 の未確認を1つ埋める）。
`get_max_physical_integrity(level)` の出力と思われる:

| レベル | 1 | 5 | 8 | 15 | 22 | 25 | 30 | 41 | 49 | 50 | 55 | 58 | 73 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 体力上限 | 10 | 11 | 12 | 15 | 19 | 22 | 26 | 34 | 39 | 40 | 42 | 43 | 50 |

HP のほうは耐久とレベルの両方で伸びる（レベル60 では `耐久 × 52` ―
耐久30→1560 / 耐久16→832 / 耐久15→780。レベル1では耐久の 4.7〜4.9 倍で
倍率が一定でない: 耐久12→56 / 18→88 / 26→124 / 30→144）。どちらも式は
確定していないが、
**体力上限の 10 がレベル1の値**であることはこの表で足りる。

#### 原因（`214_probe_new_character` の実機1回目で確定）

**ゲーム本体の `InstantaleApp.start_game(world_name)`。** 新規開始で
プレイヤーの `Character` を組む行が、レベルに 60 を渡している:

```
instantale.py:876   Character(..., experience_level=60, ...)   ← 60 を渡している
                    （experience_point は渡していない ＝ 既定の 0）
instantale.py:883   original_max_physical_integrity = get_max_physical_integrity(1)  -> 10
instantale.py:884   max_physical_integrity          = get_max_physical_integrity(1)  -> 10
instantale.py:885   physical_integrity              = get_max_physical_integrity(1)  -> 10
```

**同じ関数の 10 行のうちで、レベルが 60 と 1 の2通りに使われている。**
876 行が本来 1（`CHARACTER_LEVEL_MIN`）を渡すべきところだと読める。

裏付け:

- `levelup` / `gain_exp` は作成中に**一度も呼ばれていない**。上がったのではなく、
  最初から 60 で生まれている
- 属性の違う2体（能力値合計 78 と 76）がどちらも**ちょうど 60**。値から
  計算された結果ではなく、定数
- 窓の間に呼ばれた `scripts.functions` は `clamp_character_level(1)` と
  `get_max_physical_integrity(1)` だけ。**60 を返した関数は無い**
- 疑っていた `get_npc_exp_level` は無関係。呼ばれておらず、しかも乱数入りで
  （55→64 / 60→70 / 76→85）定数 60 を作れない。`clamp_character_level` は
  1..100 の恒等
- `max_hp` はレベル60で計算されている（`耐久 × 52`。耐久16→832・耐久15→780）。
  つまり戦闘の強さは 60 側、体力上限は 1 側で、どちらも実際に効いている

#### MOD 側の関与は排除した

`Character.__init__` を呼んでいるのはゲーム側のフレーム（`instantale.py:876`）。
スタックに載っている MOD は `107_` / `110_` / `120_` の `_load` だけで、3本とも
**`orig()` が返った後**に走るロード後の掃除であり、`experience_level` を触らない
（`start_game` を包んでいるので外側に居るだけ）。

#### 決着（main_025・2026-08-09、実機で確認）

**本体が直した。** ゲーム本体の更新（13:52）の直後に作った新規キャラを
`214_probe_new_character` が写している:

```
Character.__init__ PLAYER name='セヴリン・ヴァルコ' experience_level=1
    experience_point=0 max_hp=56 max_physical_integrity=10
    experience_level passed by the caller: 1
    caller: start_game (instantale.py:876) <- fade_out_step (create_world.py:397) ...
```

**行番号は 876 のまま**で、渡す値が 60 から 1 に変わっただけ。883〜885 行の
`get_max_physical_integrity(1) -> 10` も変わっていない ＝ こちらが読んだとおり
876 行だけが食い違っていた。セーブ側も揃っている（`days_elapsed=0` /
`lvl 1` / `exp 0` / `HP 56` / 体力 `10/10` / 耐久 12）。

**`123_fix_new_character_level` は何もしていない**（`after start_game: level=1
experience_point=0 max_hp=56 max_physical_integrity=10 (fixed 0 so far)`）。
効く条件の2つ目（レベルが最小値より大きい）が外れるためで、**「本体が直れば自動で
無効になる」という設計が実機で確かめられた**。`superseded: main_025` を入れて
デバッグモード限定へ降ろした。

> **本体の修正は既存のセーブには効かない。** このバグでレベル60のまま保存された
> キャラは読み込んでもレベル60のままで、`out\new_character_level.log` に
> `WARN loaded save carries the bug: level 60 with experience_point=0 ...` が
> 2026-08-09 の読み込み3回ぶん残っている（10:56 / 10:57 / 11:08）。直すなら
> `REPAIR_LOADED` を入れてそのセーブを読み込む。**新規作成の経路
> （`experience_level 60 -> 1`）は本体が先に直ったので、実機では一度も通らずに
> 終わった**（オフライン27件で確認したまま）。

#### 残っていること

- 既存セーブの修復（`REPAIR_LOADED`）は実機未確認。直したいセーブが出たら
  `experience_level 60 -> 1` の行と、人物欄・HP が下がることを見る
- ゲームの版がいつ壊れたか（7月22日の正常な新規開始と 8月8日の間）。
  main_024 での退行、main_025 で修正、という並びになる

---

### 2.37 ミニイベントの判定に能力値が入っていない（2026-08-08、実データで確定）

体感の申し立ては3つ: 「判定が機械的」「能力値が使われていない」「成功率が低い」。
ゲームは読めないので、残っている記録だけで確かめられるところまで確かめた。

#### 材料

| 出どころ | 何が読めるか | 件数 |
|---|---|---|
| `output_data\*\*\field_event_evaluator\*.json` | LLM が返した `credibility` と `reference_attribute` | 132（うち判定に回ったのは 121） |
| `output_data\*\*\quest_referee_event_resolve\*.json` | プロンプトに載る `quest_event_log`。末尾にゲームが書いた `<確率N%: 成功>` | 122 |
| `saves\*\backups\*.zip`（復号） | 判定当時の HP・体力・負傷・レベル・能力値 | 189 スナップショット |

判定1回は evaluator の `narration` で一意に繋がる（resolve のプロンプトに
その文がそのまま載っている）。**照合は完全一致のみ**で 121/121 が繋がった。
`quest_event_log` は前の判定の印も抱えたまま伸びるので、拾うのは**最後の印**。
先頭一致で拾うと、同じイベントの1ターン目の印を2ターン目の結果に貼ってしまう
（実際に1回目でこれを踏み、`credibility` と確率の対応が崩れて見えた）。

#### 分かったこと

1. **確率 = `credibility × 10 + 20` が上限。**121 回で上振れ 0 回。58 回は
   ちょうどこの値、残りは -2 〜 -40 の負の差
2. **`reference_attribute` は効いていない。** 5種類の能力値が選ばれているのに、
   高い能力値のときに確率が上がった回が1度も無い。同じ時刻の別判定で
   `dexterity` と `intelligence` に**同じ差**が付いた回もある
3. **判定そのものは正直。** 上限90%の19回で15勝、上限80%の32回で22勝、
   上限60%の33回で16勝。宣言どおりの確率で出ている
4. **全体の成功率は 59.5%**（72勝49敗）
5. **入力の 92% がサイコロに回る**（`roll_required` 121 / `certain_success` 10 /
   `certain_failure` 1）。行動の内容で確定するのは 8% しかない。
   「機械的」の実体はここ。何を書いてもだいたい確率判定になる
6. `credibility` は 4〜6 に 86/121 が集中。10 は一度も出ていない
7. **evaluator のプロンプトに能力値は1つも載っていない。** LLM は能力値を
   知らないまま `reference_attribute` を選んでいる（プロフィール・人格・特質・
   装備だけ）。つまり能力値は**プロンプト側にもコード側にも入っていない**

#### 決まらなかったこと（負の差の正体）

次は**外れている**（どれも実データと矛盾する）:

| 仮説 | 反例 |
|---|---|
| 参照能力値の種類 | 同時刻の `dexterity` と `intelligence` に同じ差 |
| キャラごとの固定値 | 同じキャラ・同じ能力値で日をまたぐと差が動く |
| HP や体力（`physical_integrity`）の欠け | 同じクエストの中で -11 → 0 → -4 と往復（`エリス`、2026-08-01 01:31〜01:45。同じイベント名で差が違う回もある） |
| クエストやイベントの難易度 | 同じクエスト・同じイベント名で差が変わる |
| レベル | 相関はある（レベルが上がるほど差 0 が増える）が例外が多く、式にならない |

判定の瞬間の値はセーブにも LLM の記録にも残らない。**ここから先は実機**
（§3.17、`215_probe_event_roll`）。

#### ついでに確かめた: クエスト外の自由入力（2026-08-08）

`output_data/` の全マネージャ（35種）を走査したところ、`credibility` /
`reference_attribute` を持つのは `field_event_evaluator` **だけ**、
`<確率N%: …>` の印が出るのは `field_event_evaluator` と
`quest_referee_event_resolve` **だけ**だった。

クエスト外の自由入力は `master_ai_facilitator` 系が処理し、こちらは
**LLM 自身が `roll_the_dice.chance_percent` を出す**別系統（GAME.md §2.26）。
プロンプトに能力値は1つも載っていない（`能力値` / `strength` / `dexterity` /
`attribute` すべて 0 件）ので、**ここにも能力値は入っていない**。
実測 168 回で全体 56.5%、指定確率どおりに概ね出ている。

`313_event_ability_check` は**両方**を触る（版4、2026-08-08）。マスターAI 側は
参照能力値にあたる出力が無いので、**行動文から推定する**:

- 既定は `llm`。自前の manager（`mod_ability_for_action`）で6択を1問だけ聞く。
  `timeout` 付き、失敗すれば語句の表へ降り、同じ行動文は聞き直さない
- `keywords` は語句の表だけ。**実データ 190 件で当ててある** ―
  行動文だけでは 32%、マスターAI の `think` を足して 56% しか決まらない。
  残りは加点なし（無理に倒すと当たっているように見えて中身は coin flip になる）。
  この数字が既定を `llm` にした理由
- 加点の入れ先は `roll_the_dice.chance_percent`。もともと % なので、
  フィールドイベント側の「端数」の話は関係しない。1〜100 に丸める

#### 入れたもの

`313_event_ability_check`。差の正体が決まらなくても、`credibility` を上げれば
確率が下がることはない（上限式が単調）ので、**判定に入る前の `credibility` を
能力値で上下させる**形にした。ゲームの式を推測せずに済む。

- 15 まで補正 0、そこから3点ごとに +4%、28 以上で +20%（上限）
- **減点はしない**（`MAX_PENALTY=0`）。低い能力値の判定が素のゲームより不利に
  なるのは理不尽なので、素の水準を下回らせない。減点したい人だけ上げる設定
- 底上げは既定 0（2026-08-09 に 10 から変更）。素の確率のまま能力値の差だけを
  効かせる。**作成直後のキャラは各能力値 9〜16 なので、訓練で 15 を超えるまで
  加点は出ない**（GAME.md §2.17。実機1回目でこれに気付いた。§3.18）。
  加点 0 の回も記録に残す
- 確定成功・確定失敗には触らない。`credibility` はスキーマの 1〜10 に丸める
- **4% 刻みは `credibility` の端数で作る**（`5.4` → 74%）。ゲームの確率は
  `credibility*10+20` なので、整数で渡す限り 10% 刻みにしかならない。
  スキーマ上は整数だが、代入時に検証が走らない型なら端数が通る ―
  **これは実機で未確認**。書いた後に読み直して確かめ、端数が入らなければ
  整数に丸めて入れ直す（刻みは 10% に落ちるが加点は効く）。落ちたことは
  1度だけ記録に出る。設定 `FINE_STEPS` で最初から整数にもできる。
  実際に書き込まれた確率は `215_` の `percent` で確認すること

**基準値 15・3点刻みの根拠（2026-08-08 に 24 → 20 → 15 と2度直した）**:
当初は「作成時の平均 23.7、レベル49 で 28.5」を根拠に 24 を基準にしていたが、
**能力値はレベルでは伸びない**（利用者の指摘）。セーブのバックアップで
同一プレイヤーを追った実測:

| レベル | 能力値（筋・耐・敏・知・賢・魅） | 合計 |
|---|---|---|
| 3〜33 | 24・18・26・25・25・24 | 142 |
| 41〜73 | 26・22・26・26・25・24 | 149 |

30 レベル進んでも合計 +7。動かしているのは宿の訓練だけで、作成時に振った値が
ほぼそのまま最後まで続く。基準を平均（24前後）に置くと「素早さに振ったのに
補正0」が普通になる。最終的に**利用者の指定で 15 起点・3点刻み・28〜30 で
最大 +20%** に決めた。15〜30 のあいだにちょうど5段入り、実セーブで見た
能力値の上端（30）が上限に一致する。
（`levelup()` が「HP・能力値の更新まで持つ」という GAME.md §2.17 の記述は
関数名からの推測で、実測はこの表が初めて。§2.17 に追記済み）

オフライン 65 件全通（`tools/tests/test_event_ability_check.py`）。
自由入力側は**実機で経路が成立**（2026-08-09・§3.18）。加点が実際に乗る場面と、
フィールドイベント側は未確認。

### 2.38 NPC の記憶と会話プロンプトの重複（`213_` の計測、2026-08-08）

`311_npc_profile_memory` を足す前から、ゲーム自身も NPC ごとに会話を覚えている。
2つが同じことを二重に覚えていないか、`213_probe_npc_memory` で会話2回ぶんを写した。
構造そのものは GAME.md §2.25 にまとめてある。

#### 覚え方の実体

会話を終えると `conversation_resolver` が要約を作り、`current_log` に追記して
`relationship` を書き直す。`current_log` は一時置き場で、**日付が変わると
`life_log` へ移る**（`str()` の丸写しなので移送では何も落ちない）。以後は
`life_log` として毎回全文プロンプトに載る。`relationship` / `profile` /
`personality` も毎回全文で、`311_` が注入した事実と同じ内容が3箇所に並ぶ。

**落としているのは移送ではなく `resolver` の方。** 出力量が会話の長さにほとんど
依らず一定で、8ターン・5,597 字の会話が 118 字になった。書き直すたびに同じ短さへ
圧縮されるので、この経路の情報は単調に減っていく。

#### 要約が走らない抜け方がある

`resolver` の呼び出し元は `ConversationEndManager.execute → finish_conversation
→ resolve_conversation` の1本だけ。つまり**終了ボタンを通らない抜け方は全部
素通りする**。実際に2件観測した:

| 抜け方 | 何が起きたか |
|---|---|
| 会話中に行動処理（`master_ai_facilitator_from_conversation`）へ進み、そのままセッション終了 | `resolver` 不発。会話の内容は `311_` の毎ターン抽出だけに残った。取りこぼしの保険が実際に効いた形 |
| 会話を閉じないままタイトル／別セーブのロードへ抜けた | 要約されずに消えた |

戦闘移行・依頼受注・勧誘成立・MOD による閉じ、といった抜け方ごとの網羅は未了。

#### ゲーム側にもある重複

- `life_log` に同一要約の5連コピー（911 字がそのまま毎回全文載る）
- `master_ai_from_conversation` のプロンプトにプロフィール行が4回（損 262 字）

#### まだ分かっていないこと

`memory`（5鍵の dict）と `knowledge` の更新契機。会話でも時間経過でも動かなかった。

#### この計測を受けて入れたもの

- **`311_` v4**: 記録済みの `facts` を抽出プロンプトに差し戻す。人物像は毎回
  まるごと書き直されるので、確定した事実でも数ターン後には本文から消える。
  差し戻すと、落ちた事実が戻り、同じ事実を毎ターン報告し直すのも止まる。
  オフライン 220 件全通（`tools/tests/test_npc_profile_memory.py`）
- **`111_` の置換ルール1行**: `resolver` の「一切情報を損なわないが簡潔に」を、
  削ってよい対象を描写・情感に限る形へ書き換える。実物のプロンプトに当たることは
  `output_data` のダンプで確認済みだが、**要約が実際に濃くなるかは実機で未検証**

#### 計測側で踏んだ2つの穴

- 照合を JSON の器のまま行うと2通りに誤る。空の `[]` はプロンプトのどこにでも
  在るので「全文相当」に化け、中身のある dict は鍵や括弧を含む断片が描画済みの
  文には無いので「無し」に倒れる。v2 で**文字列の葉だけ**を照合する形に直した
- NPC の鍵を id だけで持つと、セーブを切り替えたときに別世界の同じ id を同一人物
  として diff してしまう。v4 で世界名を鍵に混ぜた

---

### 2.39 実機ログの棚卸しで潰した残件（2026-08-09）

ゲームを触らずに、溜まっていた `out\` のログだけで未確認項目を突き合わせた。
**新しい実測は1件も取っていない**。すでに出ていたのに読んでいなかったものの棚卸し。
ログを書かせる仕掛けだけ先に入れて、読むのを後回しにしていた項目がこれだけ溜まっていた
という記録でもある。

#### 潰せたもの

| 項目 | それまでの状態 | 確定した根拠（`out\` のログ） |
|---|---|---|
| `104_` エリアBGM | ゲーム内未確認 | `bgm.log` に差し替え3件。`area 40: eerie/Submerged.mp3 -> anxiety/Dark Ambient 3.mp3`（08-06）、`area 41: eerie/05.From_The_Ashes.wav -> mystic/Room(Loop) - piano - drone.mp3`（08-08）、`area 42: tense/Distorted -> anxiety/Echo - hollow - scary - anxiety.mp3`（08-08）。新世界の初回に既存エリアを見送る処理も動作（テストワールド22 / ヴェスティア37→38 / ペルディション37 が `grandfathered`） |
| `107_` ロード時の発火 | 唯一の未観測 | `[2026-08-08T15:41:18.498] [FLAGFIX] load_game_new: cleared in_battle (the game left it set)` が1件。これで3つの起点＋ロードの全経路が実機で発火した |
| `109_` 横幅の拡張 | 未観測 | `box=405x486 (design 324x486)`（`item_113` / `item_461`）と `box=405x531`（`item_55`、縦横同時）。324 → 405 |
| `114_` 入力欄のフォーカス | 実機未確認 | 3系統すべて発火。`input TextInput width=1198 (of 1 candidate(s) on the HUD)`（入力欄の特定・候補は1つに絞れている）、`refocused after send finished`（送信ボタンの `disabled` を合図にする側）、`refocused after blur` |
| `119_` 犯罪の帰属 | ログ未生成 ＝ 未発火 | **発火していた。** `other_zero previous_loss=0`（第三者の犯罪を帳消し ＝ 本題の経路）が4件、`player_keep previous_loss=5` / `=8`（主人公は素通し）も分岐している。ただし §2.41 の欠陥が同時に見つかった |
| `120_` NPC名の重複 | 実機未確認 | `generate_character` 経由で改名10件。`raw table(s)` が 1〜2 で 0 ではない ＝ 素データの書き換えが効いている（GAME.md §2.23 の前提が持っている）。本命の仕掛け先だけで発火し、受け皿の `Character.__init__` は出番なし。プレイヤー名との衝突も検出した（`'“黒蜥蜴”アルカス' -> 'カゲロウ'  (clashed with id='__player__' 'ヴァルガス・ヴォルフレイン')`） |
| `122_` 会話ログ | 実機未確認 | `loaded 300 entr(y/ies) for 'ヴェスティア' … (300 line(s), 0 broken)`。世界ごとに分かれている（テストワールド146 / ヴェスティア300 / ペルディション3）。壊れ行ゼロ。`button placed by the beside 113 at (1835, 417)` で `113_` の隣への設置も確認 |
| `312_` 店の品揃え | **前提が未実測** | `first visit: マルタ(38) @ 79 day=460 items=8 (baseline only)` → `not due: … last=460 (needs 30)` → `cleared: … day=580 items=9` → `restocked: … day=580 items=8`。**空にすればゲームが作り直す**が実測で成立。日数判定も両側動作。控えを戻す救済経路は使わずに済んだ |
| 空 `Literal[]` | 計測待機中 | 実機で再現し、`203_` が locals ごと確保した。§2.40 |
| 遅延 import の当て直し | 実機での流れ未確認 | `defer wrap llama_cpp_runtime_completion:LlamaCppClient.chat (… is not imported yet)` 175件 → `wrapped llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template` 145件 → `replacing a previous patch layer` 36件。保留 → 当て直し → 世代の置き換えが全部出ている |
| `311_` 版2 / 版4 | 実機未評価 | 機構は動作。`updated: 'ルイーザ' (27) profile 305 -> 383 chars, about_player 90 -> 143 chars, +4 facts`、受け取りは `create_model('NpcProfileUpdate'): changed: str; profile: str; about_player: str; new_facts: list[str]`。`state\npc_profiles\ヴェスティア.json` に `about_player` と `facts` を持つ項目がある |
| `307_` 移動中の文言を伏せる | 未確認 | `muted while arriving: '徒歩で目指す。長旅だ...'`。同じ回で通し4回目も成立している（`危険な道を行く` → クエスト31 → `arrived: 'アイアン・ゲート' reached; 14 day(s) spent of 14 allowed`） |

部分的に前進したものが3つ:

- **`115_` スキル一覧**: `ToolListPopup`（11件・15件）を掴んで採寸し、`skipped ToolListPopup
  (one column already fits)` と判断している。**掴めること自体は確認できた**が、はみ出す
  件数に達していないので折り返しそのものは未再現
- **`308_` 味方の被弾**: `action by '雷鳴の小獣2' (敵側): ally アーリ hp 843 -> 842`
- **`123_` の検知**: 修正の発火（`experience_level 60 -> 1`）は0件だが、既存セーブに対して
  `WARN loaded save carries the bug: level 60 with experience_point=0 and
  max_physical_integrity=10 (the level-1 value)` が3回出た。健全な方も
  `player level=49 with max_physical_integrity=39 … consistent enough - not touching it` で
  正しく見送っている ＝ **判定条件「レベルだけが他と食い違っている」が実データに当たる**。
  この棚卸しの数時間後に本体が main_025 で 876 行を直したため、新規作成の経路は
  実機では一度も通らずに終わった（§2.36）。**この WARN 3件が、既存セーブの修復
  （`REPAIR_LOADED`）という残り1つの用途を裏づける唯一の実測**になっている

#### 潰せなかったもの（ログにも痕跡が無い）

いずれも「MOD が動いていない」のではなく、**その場面をまだ踏んでいない**だけ。

| 項目 | ログの状態 |
|---|---|
| `108_` 救済経路 | `inventory.log` 192行すべて `ok`（104件 → 192件に増えたが救済は0） |
| `303_` / `304_` | 全 `screen:` 行で `app.party=['player']`。仲間がいる状態でクエストを終えていないので発火しようがない |
| `306_` レベルアップ | 4件とも `VacationRestManager`（休息）で `shared 0 gain(s) with 0 companion(s) (not a sharing target)`。訓練そのものを通していない |
| `307_` 放棄・体力不足で断る | `refused: not enough stamina` が0件。`stamina: 20/39 (51%) threshold=33%` で毎回しきい値超え |
| `308_` コロシアム・味方が倒れた／逃げた | 0件 |
| `309_` 投獄・市民権 | `office_pardon.log` が 2026-08-05 以降更新なし |
| `215_` フィールドイベント | **適用はされている**（`applied: 215_probe_event_roll`、`quest_referee_event_evaluate_new` / `_resolve` を包んでいる）が `out\event_roll.jsonl` が未生成。クエスト中のミニイベントを一度も踏んでいない |
| `201_` `facility_move_to` | `FreeInputStart.method(choice_text=…)` が15回。属性は依然無いまま正常終了、`AttributeError` の再現0 |
| `117_` 切り詰めの見え方 | 評価に使える行なし |
| `121_` 窓サイズ追従・他人の人物欄 | 該当ログなし |

#### 教訓

**ログを仕掛けたら読む日を決めること。** 潰せた12項目のうち9項目は、必要な行が
1週間以上前から出ていた。`312_` の「前提が未実測」に至っては、本体のソースが読めないから
確かめようがないと書いた当の前提が、書いた2日後のログで成立していた。実機を触る予定を
待つ必要は無かった。

### 2.40 空 `Literal[]` の原因確定（2026-08-08、スキル0件の敵）

長く「計測待機中」だった `AssertionError: literal "expected" cannot be empty,
typing.Literal[]` が実機で再現し、`203_probe_create_model` のトリップワイヤが
`live_crashes.log` に locals ごと残した。

```
THREAD CRASH: Thread-138 (execute)  |  2026-08-08T15:39:44  |  game_version=014
AssertionError: literal "expected" cannot be empty, obj=typing.Literal[]
  instantale.py:7821  BattlePhaseManager.execute       choice_text='良いわ、先に来なさい。'
  instantale.py:7758  BattlePhaseManager.battle        command='自由入力'
  instantale.py:7720  enemy_turn_separate              enemy_key='外套の女'
  llm_manager_battle.py:1143  referee_npc
  pydantic/_internal/_generate_schema.py:1474  _literal_schema
```

決定打は `referee_npc` の locals の1行:

```
skills_literal_list      = list(len=0) []
current_enemy_dict       = dict(len=1, keytypes=str) keys=['外套の女']
enemies_data             = "…{'外套の女': {'名前': '外套の女', 'HP': '100/100',
                              '説明': '少し前に流れ着いた女。…', '特質': []}}"
```

| 分かったこと | 根拠 |
|---|---|
| 原因は**スキルを1つも持たない敵が敵ターンを迎えたこと** | `skills_literal_list` が空のまま `Literal` に渡っている。敵の `特質` が `[]` |
| 第一容疑だった「敵候補0件」は**否定** | `current_enemy_dict` は len=1 で埋まっている。敵は居る |
| 落ちるのは敵ターンの側だけ | `enemy_turn_separate` → `referee_npc`。プレイヤー側の手は別経路を通る |
| 相手は「戦うために作られていない NPC」 | `外套の女` は HP 100 の街の住人（`少し前に流れ着いた女。何をして暮らしているのか誰も知らない。`）。会話から戦闘に入ったときに出てくる型 |

つまり再現手順は「**スキルを持たない一般 NPC に会話から喧嘩を売り、相手のターンまで
進める**」。§2.18 で観測した「`Literal` が消費に応じて減っていく」現象とは別筋で、
あちらは候補が尽きたモデルをゲームが作らないという話だった。ここで空になっているのは
消費の結果ではなく**最初から空**。

修正は入れていない（§1「未修正」）。要るのは「空なら `Literal` を組ませない」の1点だが、
`skills_literal_list` を空でない何かに差し替えると、その値を LLM が選んで返してくる。
ゲームがそれをスキル名として扱うのかは未調査で、そこを確かめてからでないと
「落ちない代わりに存在しない技を使う敵」になる。

### 2.41 `119_` の注入がローカル LLM 専用だった（2026-08-09、原因確定 → v2 で修正・実機未確認）

§2.39 で `119_fix_crime_attribution` が実機で発火していたことが分かったが、同じログを
時系列で並べると、**途中から動かなくなっている**。

| | 最終 |
|---|---|
| ローカル経由の送信（`prompt_bloat.log` の `[REPLACE] chat`） | 2026-08-08 21:52 |
| `119_` の `prompt … rewritten (count=N)` | 2026-08-08 21:37 |
| 以降のプロバイダ | claude（08-08 22:05）→ openai（08-09 は82件すべて） |
| 以降の `119_` の判定 | **`marker_missing` のみ。`rewritten` は0件** |

原因はコードを見れば1行で、注入の入口が1つしかない:

```python
@ctx.wrap("llama_cpp_runtime_completion:LlamaCppClient.chat", required=False)
def chat(orig, self, model, messages, format=None, *args, **kwargs):
    ...  # ここでマーカーをプロンプトへ差し込む
```

クラウド経由はこの関数を通らないので、マーカーが差し込まれない。マーカーが無ければ
`postprocess_facilitator` / `postprocess_summarizer` は `marker_missing` を返し、
**既定の素通し側**に倒れる。「安全側に倒す」設計が効いているので壊れはしないが、
**MOD が丸ごと無効**になる。

二段構えのうち**効かなかったのは1段目だけ**だった。戻り値を直す2段目は
`llm_manager` の `master_ai_*` を包んでいるのでプロバイダに依存しない。1段目
（プロンプトの置換）が届かずマーカーが出ないので、2段目が既定の素通しに倒れる ―
という連鎖で MOD 全体が黙っていた。判定を受け取る側の別名リスト
（`master_ai_process_summarizer` ほか5つ、`master_ai_process_summarizer_with_no_recipients`
を含む）は最初から揃っていたので、直すのは注入側だけで足りた。

これは `111_llm_prompt_replace` が v4 で解決した「プロバイダ非依存の `llm_manager`
別名包み」とまったく同じ落とし穴。**同じ形（送信の入口を1つだけ包んでいる）の MOD が
他に無いかは、まだ点検していない。**

#### 修正（2026-08-09、`119_` v2 / `111_` v6・オフライン検証まで）

**`111_` の仕掛け口を写さず、ローダへ移した**（`instantale_modloader.llm`。
TECH.md §5.3）。写せばその場は直るが、ドリフトは予告されている。TECH.md §3.2.3 が
`world_key` で4〜5本に増えてから実際にずれた話を書いている。移したのは
**どこで捕まえるか**だけで、**どう書き換えるか**は両方の MOD に残っている
（`111_` は確率つきの置換ルール、`119_` は目印の差し替え）。

保留の理由だった2点は次のように決着した。

| 懸案 | 決着 |
|---|---|
| 世代管理 | `patch.py` の層がそのまま重なる。`wrap` は既存の層を壊さず包むので、2つの MOD が同じ関数を包んでも問題は出ない（`111_` が `105_` を包んでいるのと同じ形。TECH.md §3.2.2 の表） |
| 包む順序 | **ローカルの3点**は `mod.json` の `after` / `before` どおり（`119_` は `111_` の外側）。**クラウドの別名**は「別名の後生えを見張って当てる」ので、**先に当てた方が内側**になり順序を約束できない。約束しないことを TECH.md §5.3 に明記した。互いの書き換えが相手の目印を壊さない前提で書く（`119_` の目印は `【犯罪帰属MOD】` 一語で、`111_` の既定ルールはこれに触らない） |

「1回の推論で1回だけ」の印は**登録ごとに別**にした（`wrap_outgoing` の呼び出しごとに
`threading.local()` を1つ作る）。共有すると、先に通った MOD が後の MOD を塞ぐ。
印が届かない別スレッド経路の受け皿は MOD 側に残した。`111_` は自分の出力の
ハッシュ（`Seen`）、`119_` は本文に自分の目印があるかで見る（＝冪等なので受け皿が要らない）。

オフラインは `tools/tests/test_crime_attribution.py` が9件全通（4件から増やした。
足したのは**経路**の5件: ローカルの chat、クラウドの `send_request` /
`send_request_with_no_structure` / `message=` のキーワード渡し、ローカル実行時に
クラウド境界へ触らないこと、置換→マーカー→評判低下の取り消しまでの一周）。
`111_` 側は移設後も 76件全通で、**判定の中身は1つも変えていない**。

> **実機は未確認。** 次に APIキー経由で遊んだとき、`out\crime_attribution_fix.log`
> に `prompt summarizer rewritten at openai (count=N)` のようにプロバイダ名つきで
> 出るか、そのうえで `other_zero` / `other_loss_zeroed` へ分岐するかを見ること。
> `marker_missing` だけが並ぶなら、まだ注入されていない。

### 2.42 新種: 立ち絵の無い人物で `image_portrait` に None（2026-08-08）

`live_crashes.log` に2件。ゲーム本体のバグで、MOD 側は未対処。

```
MAIN CRASH  |  2026-08-08T15:42:50 / 15:47:52  |  game_version=014
ValueError: None is not allowed for InstanTaleHUD.image_portrait
  instantale.py:2055  InstantaleApp.update_character_image   character_id='50'  image_src=None
  kivy/properties.pyx:794  StringProperty.check
```

起点は2つある:

| 起点 | 経路 |
|---|---|
| `instantale.py:8765 <lambda>` | `FreeInputStart` から。自由入力の最中 |
| `instantale.py:1491 <lambda>` | 別の場所。こちらは呼び出し元の名前が取れていない |

どちらも `Clock` 経由の遅延呼び出しで、`update_character_image` が
`image_src=None` をそのまま `StringProperty` へ代入している。立ち絵の画像が
まだ生成されていない／生成に失敗した人物（どちらの回も `character_id='50'`）を
映そうとしたときに起きると読める。

`110_fix_character_name_path` で潰した `WinError 123`（名前が原因で画像が作れない）と
症状が地続きなので、**画像が作れなかった人物のなれの果て**の可能性がある。
確かめるなら `worlds\<世界>\characters\` に id=50 の人物のフォルダがあるかを見る。

MOD で塞ぐなら `update_character_image` を包んで `image_src` が None のときは
何もせず戻す1行で足りるが、そうすると「立ち絵が前の人物のまま残る」。先に
「None のときゲームが本来どうしたかったのか」（空文字か `placeholder.png` か）を
決める必要がある。`character_sheet.log` に `source='placeholder.png'` の実例があるので、
そちらが本命と見られる。

### 2.43 `output_data/` は `111_` 適用**後**が記録される（2026-08-09、原因確定）

開発中の MOD（9xx。TECH.md §2.6）のオフライン検証で、「進行プロンプト全件に
目印が在る」が、記録済みの実プロンプト 600 件のうち **580 以降**で落ちた。
落ちた目印は `- retire_from_the_quest:` の1行。

**ゲームが文面を変えたのではない。** 食い違っていたのは行末の1箇所だけで、

```
ゲームの原文   … しかし具体的が無いならばさっさと撤退させること。      ← 誤植
580 以降の記録 … しかし具体的な理由が無いならばさっさと撤退させること。
```

これは `111_llm_prompt_replace` の同梱ルール（`llm_replacements.default.txt` の
59 行目。2026-08-11 の見直しで2行増えて 57 → 59 行目）そのもの。`out/prompt_bloat.log` に 42 件の発火が残っていて、直近は
`[REPLACE] openai` ＝ クラウド経路で当たっている。

つまり **`output_data/` に何が記録されるかが途中で変わった**:

| 時期 | `111_` の仕掛け先 | 記録される姿 |
|---|---|---|
| 〜579（2026-08-08 まで） | `LlamaCppClient.chat`（ゲームのダンプより**下流**） | ゲームが組んだ**素**のプロンプト |
| 580〜（2026-08-09 19:19〜） | `llm_manager` の `send_request*` 別名（**上流**。§2.24 の v4） | `111_` が**書き換えた後**のプロンプト |

`111_` をプロバイダ非依存にした（＝より上流を包んだ）副作用で、**実プロンプトの
記録が「ゲームの素の文」ではなくなった**。`output_data/` を根拠に使う検査は、
今後この前提で読むこと。

> ゲーム内の動作は壊れていない。プロンプトを書き換える MOD は組む時点
> （`quest_referee*` など）で当たり、`111_` は送信の直前で当たるので、
> 前者が見るのは今も素の文である。壊れていたのは**照合に使っている資料の性格**だけ。

**対処:** 目印を行末に依存しない形へ変えた（当該 MOD の `DOC.md`）。`REFEREE_LINE_SWAPS` を新設し、
`- retire_from_the_quest: ` という**行頭だけ**で当てて、その行を丸ごと差し替える
（`- battle:` の行を行頭で当てているのと同じ考え方。あちらは末尾の適正数が
難易度で動くため）。これで、誤植が直っていても・本体が語尾を変えても当たる。
オフライン検証に「111_ が直した後の文でも当たる／説明ごと差し替わる／原文が
残らない」の3件を足した。

同梱ルール 30 本を各 MOD の目印と突き合わせた結果、**当たるのはこの1本だけ**で、
他の MOD に影響は無い。

### 2.44 `116_` パーティ欄の拡張（2026-08-03、実機で5回外して直した）

ゲームは仲間の枠を3つしか作らないので、4人目以降がパーティ欄に出ない。実機では
表示・押下とも成立している（6人まで）。

同じ日に実機で5回外していて、その5回がそのまま「他人の画面に物を足すときの規則」に
なっている:

| # | 外したこと | 何が違ったか |
|---|---|---|
| 1 | 足した枠に枠線が付かない | canvas は複製されない。ゲームの `add_border` を借りる |
| 2 | 元の枠が数 px ずれる | `size_hint_y` は `0.33` で 1/3 ではない。実測座標に釘付けにする |
| 3 | ゲームの選択肢が押せなくなる | 重なるものの `disabled` を控え → 書き戻していた。控えない |
| 4 | 覆った先の選択肢が透けて見え、押せる | 足した枠は背景を持たない。黒い板を1枚敷く |
| 5 | 雇い直しでクラッシュ（`IndexError`） | `update_party_display` は帯の子を1つずつ枠として塗る。帯に置いた黒い板が枠として塗られていた。板は帯の外・帯のすぐ後ろへ |

教訓は2つ。**他人が管理する状態は控えて書き戻さない**（3）、**他人の入れ物に、
他人が数えている物と違う物を混ぜない**（5）。仲間の増減は `add_party_member` /
`remove_party_member` からその場で追う。

置き場所の選び方は 2026-08-03 に `ui.overlay_host` へ移した（`113_` から写した
「HUD の先頭の子を掴む」が、相手のボタンの中へ入り込みうる状態だった。§2.33）。

残るのは足した枠の立ち絵の見え方（`PORTRAIT_FIT` で選べる）。オフライン88件全通。

### 2.45 `118_` 本文の出し方と打ち切り（実機で5回外した・多重塗り直しは 2026-08-06 に実測）

逐次表示／一括表示の切り替えと、読み終わった本文の灰色化。既定は逐次表示＋
クリックで打ち切り、灰色化の基準に経過セッション数を足してある。打ち切りが
クラッシュを起こした件は §3.20。

**打ち切りは実機で5回外している。**

| # | 外したこと | 何が違ったか |
|---|---|---|
| 1 | 正本を `app.display_text` から読もうとした | そこには無い。`frames.attr` の既定値が文字列の `MISSING` なので `isinstance(str)` を素通りしていた（TECH.md §5.2） |
| 2 | 終端の呼び出しより先に本文を書いた | ゲームが組み直すと消える |
| 3 | 正本へ書けば画面が塗り直されると思っていた | 塗り直されない |
| 4 | 塗り直しても高さが古いままで、増えた行が切り落とされた | Kivy はテクスチャの作り直しを次のフレームへ回す（GAME.md §2.3） |
| 5 | 正本の末尾が本文の先頭からの切り出しと文字単位で一致しない | ゲームは打ち出しの最中に改行を混ぜる |

決着したのは**書くのをやめたとき**。`hud.display_text` は正本ではなく写しで、
書いても 0.1 秒後に作り直されていた（`after the skip +0.1s` で `canonical` が
書く前の長さに戻る）。いまは1文字ずつの呼び出しを**その場で最後まで回し**、
回している間だけ塗り直しを止めて、終わってから1回だけ塗る。書くのはゲーム自身なので、
正本の在り処も打ち出し中の整形も知らなくてよい。回した後に残る予約は捨てる
（渡すと終端を二度踏んで次の本文が消える）。

> **他人の状態は名前で当てて書かない。他人の経路を回す。**

灰色化も同じ根で外していた。控えた本文をそのまま探すと、打ち出し中に混ざる改行の
せいで見つかる本文と見つからない本文が混ざり、色が白・灰・白・灰と交互になる。
空白を落として突き合わせ、位置だけ元に戻す形に直した（`compact`）。色を**外す**側でも
1つあり、タグの付いた文字列を残したまま `markup` を落とすと、ゲームが塗り直すまでの間
タグが文字として出る（場面転換で見えた）。素の本文に戻してから落とす。順序の間違いは
描く側（次のフレーム）では捕まらないので、オフラインでは `markup` を切る瞬間に
検査している。

#### 多重塗り直しは出していない（実機実測、2026-08-06）

`211_probe_text_speed` の同時計測。

| 見たもの | 実測 | 意味 |
|---|---|---|
| `render x1.00/tick` | 1.00 | 1ティックにつき作り直しは1回 ＝ 余計な作り直し無し（2.00 以上なら誰かが二度手間） |
| `repaint avg` | 0.0〜0.5ms | 間隔 42.9ms の約1% |
| `fps` | 78〜80 | ― |
| クリックで打ち切った回 | `tick x60 interval avg=8.5ms render x0.22/tick` | 回している間の塗り直しが実際に止まっている |

オフラインで MOD 側の手間だけを測ると1文字あたり +0.001ms、`117_` の窓が1文字ずつ
ずれて色の控えが毎回無効になる状況でも +0.16ms（間隔の 0.4%）。効くのは覚えている
本文の数（`MAX_SEGMENTS`）なので、重くなったらそこを削る。

一括表示の前提は §2.34 の実測と一致していて、二度手間を外した後でも1文字あたり
8.1ms（間隔の21%）が残る ＝ 狙いどころは合っている。オフライン74件全通。

### 2.46 `121_` プレイヤーの人物欄（2026-08-06 の計測・実機で1度直した）

素のゲームでは人物欄の右半分が空の箱のままで、手配度・スキル・特性が出ない。
載せる値の在り処は `212_probe_character_sheet` の計測で確定した（窓 1920x1000）。

実機で1度外している。「ゲームが決めた寸法の 1.4 倍」で広げると、窓を小さくしたときに
下の情報欄や本文へ食い込む。四辺を窓に対する割合で持つ形に直した。

残るのは窓の大きさを変えたときの追従と、プレイヤー以外の人物欄を開いた場合。
オフライン82件全通（`tools/tests/test_ui_character_sheet.py`）。

### 2.47 `126_` タイトル画面の版表示（2026-08-13、決着）

利用者が実機で確認。タイトル画面の右上に `modloader v1.6.0` が出て、
**表示・タイトルへ戻ったときの組み直し・窓の追従・タイトルの操作、いずれも
問題なし**。未確認は残っていない。

ラベル1枚のために画面の子を増やしているので、**押下判定を壊していないか**が
ここでの賭けだった（HUD では同じことがアイテムの移動・装備を壊している。§2.33）。
タイトル画面には「最初の子」を読む側が居ない、という読みが実機で持った。

足す経路が2つあるのも、この確認で両方が通ったことになる。注入した時点で
出ている画面へ足す側（`ctx.on_ready` で `Window` から辿る）と、タイトルへ
戻って組み直される画面へ足す側（`StartScreen.__init__` を包む）。後者は
ゲーム内から戻らないと通らない。

読み込みの位置はこの確認のあと `001_crash_recorder` の直後へ移した（利用者判断）。
この MOD は誰も包まず誰にも包まれないので、順序の制約は無い（TECH.md §3.2.2）。

オフライン19件全通（`tools/tests/test_ui_title_version.py`）。

### 2.48 `127_` ローカルLLMの速度と VRAM（2026-08-12〜13、実測）

RTX 4070 Ti SUPER（16376 MiB）。同一プロンプト 4402 トークン、生成 128 トークン。

#### 崖がある。超えるとエラーではなく激遅になる

gemma-4-26B-A4B Q4_K_XXL（重み 13650.92 MiB）で `--ctx-size` を振った実測。

| 起動引数 | 窓 | KV | VRAM | 処理 t/s | 生成 t/s |
|---|---|---|---|---|---|
| `--ctx-size 16384`（素のゲーム） | 16384 | 1220 | 15977 | 4226 / 4292 | 94 / 96 |
| `--ctx-size 16384 --parallel 1` | 16384 | 620 | 15378 | 5712 / 6253 | **133 / 127** |
| `--ctx-size 24576 --parallel 1` | 24576 | 780 | 15589 | 5939 / 6187 | 130 / 129 |
| `--ctx-size 32768 --parallel 1` | 32768 | 940 | 15744 | 5911 / 6150 | 130 / 130 |
| `--ctx-size 32768`（統合） | 32768 | 1540 | 15937 | 4088 / 4318 | 93 / 92 |
| `--ctx-size 65536 --parallel 2` | 32768 | 1880 | 15938 | 294 / 369 | **44 / 47** |
| `--ctx-size 65536 --parallel 4` | 16384 | 2480 | 15938 | 496 / 505 | **29 / 29** |

> 24576 と 32768 の速度は再検証で差し替えた（2026-08-14、別セッション）。初回の測定は
> 1回目のリクエストにウォームアップが乗って 4216 t/s のような外れ値を拾っていた。
> **窓を 24576 から 32768 へ広げても速度は落ちない。** 窓を選ぶときは VRAM の余白だけ
> 見ればよい。

**崖は 15744（正常）と 15938（崩壊）の間。** 超えるとドライバがシステムメモリへ
退避し、PCIe 越しになって処理が約17倍・生成が約3倍遅くなる。Windows は失敗を
返さないので、症状は「エラー」ではなく「異様に遅い」になる。

**飽和後の `nvidia-smi` は当てにならない。** 15977 MiB の素ゲームより 15938 MiB の
65536×4 のほうが3倍遅い。溢れた分は表示に出ないため。判定は起動ログの
`KV buffer size` と `compute buffer size` の合計で行う。

KV の実寸は **20 KiB/token（全文脈層5本）＋ 300 MiB/スロット（SWA 層25本・
n_ctx に依存しない固定）**。起動ログの KV buffer が2行に分かれ、比例する側が
16384→320 / 24576→480 / 32768→640 MiB と一致する。

#### 予算の内訳（ゲームを起動して遊べる状態）

```
16376  VRAM 総量
 − 350  デスクトップ等（何も起動していない状態の実測）
 − 260  ゲーム本体の描画（ワールド読み込み後。タイトル画面では 155）
 −13651  モデル重み        （起動ログ model buffer size）
 −  533  計算バッファ      （n_batch で決まる。ctx を変えても一定）
 −  275  CUDA コンテキスト等（実測から逆算）
────────
  1307  KV に使える
```

#### `--parallel 1` が速い理由は SWA だった（切り分け・2026-08-13）

「要求が1本なら統合でも独占しているはずで、差が出ないのでは」という疑問から、
VRAM に余裕のあるモデルで切り分けた。**どちらも 16 GiB の半分以下で、溢れは無い。**

| モデル | 構成 | VRAM | KV（全文脈 + SWA） | 生成 t/s |
|---|---|---|---|---|
| Gemma4-12B Q4_K_M（SWA あり） | 統合 | 9961 | 256 + **1440** | 58.7 / 59.6 / 59.7 |
| Gemma4-12B Q4_K_M | `--parallel 1` | 9027 | 256 + **480** | **66.2 / 66.2 / 66.2** |
| Qwen3.5-9B IQ4_XS（SWA なし） | 統合 | 6287 | 512 | 104.0 |
| Qwen3.5-9B IQ4_XS | `--parallel 1` | 6137 | 512 | 104.1 / 104.5 / 104.1 |

**全文脈層の KV は統合でも専用でも同じ**（12B は 256 MiB、9B は 512 MiB で不変）。
差が出るのは **SWA 層の確保量**で、統合では余分に取られる。**実測は4スロットで3倍**
（26B が 300 → 900 MiB、12B が 480 → 1440 MiB）。スロット数の4倍ではない理由は未確認
（`llama_kv_cache_iswa` が2種類のキャッシュを別管理しており、SWA 側の確保式に
`n_ubatch` 等が絡んでいる可能性。未追跡）。SWA 層の窓は 1024 固定なので容量は要らない
はずだが、確保量に読む範囲が連動しているように見える。gemma4 は層の大半が SWA
（26B で30層中25層、12B は48層）なので効く。

したがって `--parallel 1` の利得は2つに分解できる。

| 要因 | 効果 | 条件 |
|---|---|---|
| SWA キャッシュの縮小 | 生成 +11% / 処理 +16% | SWA を持つモデル。VRAM の余裕は不問 |
| 崖から遠ざかる | 残り（26B では合計 +40%） | VRAM がぎりぎりの環境だけ |
| （SWA 無しモデル・余裕あり） | **0%** | 差は出ない。KV も変わらないので害も無い |

> 当初 `127_` の効能を「生成 +35%」と書いていたが、26B での測定値で VRAM 圧の効果が
> 混ざっていた。上の4条件が正しい。

#### モデルが変われば結論も変わる

Qwen3.5-9B IQ4_XS（重み 4373 MiB・SWA 無し）は余地が桁違いに広く、
`--ctx-size 131072 --parallel 4`（窓 32768 × 4本）でも VRAM 9805 MiB・生成 104 t/s で
通る。ただし SWA が無いぶん KV の単価は **32 KiB/token** と 26B より高い。
26B が安いのは層の大半が SWA で窓 1024 に頭打ちになるため。単価はモデルごとに測る。

#### 窓を超えた要求は HTTP 400

```json
{"error":{"code":400,"message":"request (44002 tokens) exceeds the available context size (32768 tokens), try increasing it",
"type":"exceed_context_size_error","n_prompt_tokens":44002,"n_ctx":32768}}
```

黙って遅くなるのではない。ゲームは同じプロンプトを送り直すので必ず同じ所で止まる。

#### このゲームは並列でほとんど走らない

プロキシのログ（`[DIAG]` が有効だった 2026-07-25 の区間）でリクエストの開始と終了を
突き合わせた。

| | 値 |
|---|---|
| ペアで追跡できたリクエスト | 62 |
| 同時に走った最大数 | **2** |
| 重なった回数 | 4（全体の6%） |
| 所要時間 | 中央値 1.92秒 / p90 3.96秒 / 最大 97.4秒 |

94%が逐次で、3本以上は一度も無い。`--parallel 1` で失われるのは並走だけで、溢れた
要求はキューに入って順に処理される（エラーにはならない）。6%が数秒待つ代わりに
全リクエストが速くなる取引。

#### `--parallel` を明示すると SWA のチェックポイント復元が要る（2026-08-14）

SWA 層は古いトークンを捨てるので、途中から再開するにはその時点のスナップショット
（コンテキストチェックポイント、1個 412〜480 MiB）が要る。**`--parallel` を明示して
スロットを絞ると、種類の違うプロンプトが同じスロットを奪い合い、`n_swa` の制約で
チェックポイントが無効化されて4件に1件がフル再計算に落ちる。** ログの
`erased invalidated context checkpoint` がそれ。

`--checkpoint-every-n-tokens 256` を併せて渡すとスナップショットが密になり、復元が
成功するようになる。**VRAM は増えない**（チェックポイントはホスト側。同時に保持される
のは最大3個だった）。Gemma4-12B、前置きの違う4種類を round-robin ×3周、
`cache_prompt: true`。数値は再計算されたトークン数（小さいほどキャッシュが効いている）。

| 起動引数 | 窓 | VRAM | 再計算（定常・4件） | プロンプト処理 |
|---|---|---|---|---|
| **素のゲーム** | 16384 | 9976 | 3332 | 1405 ms |
| `--parallel 1` | 16384 | 9054 | 4653 | 1683 ms |
| **`--parallel 1 --checkpoint-every-n-tokens 256`** | 16384 | **9054** | **3332** | **1408 ms** |

`--cache-reuse 256` では回避できない（効果ゼロ。下記）。`--parallel 2` でも回避
できない（スロット数の問題ではないため）。

#### スロットを増やす方向は成立しない（2026-08-14）

`--ctx-size` は合計値なので、**`--parallel N` を明示すると窓が N 分割される。**
Gemma4-12B、`--checkpoint-every-n-tokens 256` 付き。

| 起動引数 | 窓 | KV | VRAM | 再計算 | 生成 |
|---|---|---|---|---|---|
| **素のゲーム**（共有4本） | 16384 | 1696 | 9976 | 3332 | 65.7 |
| `--parallel 1` | **16384** | **736** | **8969** | 3332 | 67.4 |
| `--parallel 2` | **8192** | 1216 | 9399 | 3332 | 67.8 |
| `--parallel 4` | **4096** | 2176 | 10359 | 3332 | 67.6 |

2本以上は窓が縮んだうえに素のゲームより VRAM を食う。窓を保つには `--ctx-size` を
スロット数倍する必要があり、KV はさらに増える。**「並列を保ちつつ節約」は原理的に
成立しない。**

`--checkpoint-every-n-tokens 256` はスロット数に関わらず効く（2本でも4本でも再計算は
3332 に戻る）。復元が要るのは `--parallel` を明示したことが理由で、スロット数ではない。

#### `--cache-reuse 256` はゲームの既定ではない（2026-08-14）

実機の `config.json` にある `--cache-reuse 256` と `--parallel 1` は利用者が設定欄に
書き足したもの。ゲーム既定の欄は `--n-gpu-layers 999 --temp 1.0 --top-p 0.8 --top-k 20`。
4条件で測ったが、`--cache-reuse` の有無による差は再計算トークン・処理時間・生成速度の
すべてでゼロだった（3332 対 3332、1405 対 1402 ms、65.8 対 66.0 t/s）。**渡っているが
仕事が無い。** したがって上の弊害はゲーム既定を基準にしても同じ形で出る。

#### llama.cpp 本体の版は関係ない

同梱の b8954 と当時の最新 b10369（1400ビルド差）を同条件で A/B したが、差は
±0〜-2% で利得なし（gemma-4-26B tg 152.89 → 152.94、Qwen3.5-9B tg 101.45 → 99.12）。
遅いときに疑う先はエンジンではなく文脈長と KV。

#### 実機

2026-08-13、`--ctx-size 32768 --parallel 1` で起動して体感で明らかに速くなった。
直前の2回が旧設定の 131072 × 4本と 65536 × 2本で、どちらも崖の向こう側だったため
（29 / 44 t/s）。その後、既定は「窓に触らず `--parallel 1` だけ」へ変更。

#### 再検証（2026-08-14、独立測定・4主張とも再現）

`out\reverify_127\` のキットで別セッションが測り直した。条件の差はアイドル時
VRAM のみ（前回 347〜353 → 今回 506 MiB。常駐の差で約 150 MiB 高い）。

Gemma4-12B（4種 round-robin ×3周、`cache_prompt: true`）:

| 起動引数 | VRAM | 再計算（定常・4件） | 生成 |
|---|---|---|---|
| （既定・共有4本） | 9975 | 3332 | 64.1 t/s |
| `--parallel 1` | 9053 | **4653** | 65.8 t/s |
| `--parallel 1 -cpent 256` | 9053 | 3332 | 65.6 t/s |

前回と同じ形。フル再計算に落ちるのは今回も4件中1件（2222 トークン。犠牲になる
種類は前回と違い、順序次第という記載どおり）。`--cache-reuse 256` も再測定し、
有無で再計算・処理・生成が完全同一（効果ゼロ）を再確認。

効き方の条件分け（統合 → `--parallel 1`、KV は起動ログの実測）:

| モデル | VRAM | KV | 生成 |
|---|---|---|---|
| Qwen3.5-9B（SWA 無し・余裕） | 6317 → 6167 | 512 → 512（不変） | 104.0 → 104.2 t/s（差なし・害なし） |
| gemma-4-26B（崖に接触） | 飽和（16008）→ 15536 | 1220 → 620 | 96.4〜98.0 → **129.4〜129.7 t/s** |

26B はプロンプト 4402・生成 128・`cache_prompt: false`（前回と同一手法）。KV の
内訳（全文脈 320 ＋ SWA 900/300）まで前回と一致。

`--ctx-size` の振り直し（`bench_ctx.sh` ほか。26B・プロンプト 4402・生成 128）:

| 起動引数 | KV | 生成 t/s（前回 → 今回） |
|---|---|---|
| `--ctx-size 24576 --parallel 1` | 780 | 124 → 129 |
| `--ctx-size 32768 --parallel 1` | 940 | 126/122 → 130 |
| `--ctx-size 32768`（統合） | 1540 | 93/92 → 95 |
| `--ctx-size 65536 --parallel 2` | 1880 | 44/47 → **54** |
| `--ctx-size 65536 --parallel 4` | 2480 | 29 → **31** |

KV の確保量は5条件とも前回と 1 MiB も違わない。窓の割り算（65536÷2=32768、
65536÷4=16384、統合は全スロットが全窓）も `/slots` で再確認。崖の向こう側の崩壊
（生成が半分〜4分の1）も同じ形で再現した。窓超え 44002 トークンは今回も
HTTP 400 `exceed_context_size_error`（記録と同文の JSON）。

**崖の位置が常駐次第でずれる実例も出た。** 統合2条件（16384 / 32768）のプロンプト
処理が今回 1925〜2016 / 1939〜1953 t/s と、前回（4226〜4292 / 4088〜4318）の半分。
アイドル時 VRAM が 150 MiB 高く、統合はもう崖の向こう側にいる。生成はまだ 95 t/s
前後を保っており、**溢れの初期は処理から先に崩れる**。`--parallel 1` 側は処理
5911〜6187 t/s と前回どおりで、崖から遠ざかる効果そのものも再現した。

オフラインの32項目（`tools\tests\test_llm_response_speed.py`）も全通過。

なお、キットの `bench_ctx.sh` は作成元セッションの scratchpad の絶対パスを持った
ままだった（Python 2本は直っていた）。自分の位置から解決する形に修正済み。

#### 観点の追い足し（2026-08-14、キットの外側）

キットの4主張の外を洗い、4件を実測で閉じ、3件を新たに見つけた。

**閉じた4件。**

- **MOD が実際に渡す長い旗は受理される。** これまでの測定はすべて短縮形 `-cpent`
  で、MOD が渡す `--checkpoint-every-n-tokens` そのものは一度も実バイナリを通って
  いなかった。同梱 b8954 で起動し、打ち消し効果も同値（定常 3332）
- **打ち消しは `--parallel 2` でも成立**（定常 3332。§2.48 の表では単独 4653）。
  MOD が SLOTS>=2 でも旗を付ける仕様の裏が取れた
- **キューは実測で確認。** `--parallel 1` へ同時2本 → 両方 HTTP 200、直列処理で
  エラーなし。「溢れた要求はキューに入って順に処理される」は §2.48 では推定だった
- **実機の応答長の分布**（キット §6 の未確認項目）。`output_data\` の実機記録
  16185件から集計（トークンは文字数÷1.6 の概算）:

  |  | 中央 | p90 | p99 | 最大 |
  |---|---|---|---|---|
  | プロンプト | 1825 | 6194 | 9595 | 約23,000 |
  | 応答 | **124** | 336 | 3752 | 約20,800 |

  窓 16384 を超えるプロンプトは実在する（`--ctx-size` 拡張と HTTP 400 は机上の話では
  ない）。典型リクエスト（プロンプト1825・応答124）だと 26B の崖際で約 2.2 → 1.3 秒、
  余裕のある 12B では 1.9 → 1.8 秒。生成の長い呼び出し（`311_` の抽出は p90 831
  トークン）ほど利得が大きい

**見つけた3件（未対処）。**

- **短縮形の別名に盲目。** `has_flag` / `set_flag` / `drop_flag` は長い旗の完全一致
  だけを見るが、llama-server には `-c`（=`--ctx-size`）`-np`（=`--parallel`）
  `-cpent` がある。別名と長い旗が両方並ぶと**後勝ち**（実測:
  `--ctx-size 16384 -c 8192 -np 2 --parallel 1` → 1スロット・窓8192）。帰結は
  3つに分かれる。(1) SLOTS>=1 は MOD が末尾に足すので後勝ちして意図どおり。
  (2) SLOTS=0 は欄の `-np` を消せず、統合に戻せない。(3) 欄の `-cpent 512` は
  MOD の 256 に上書きされ「明示があれば尊重」の約束が破れる。ゲーム自身は長い旗
  しか使わないので、欄に短縮形を書いた利用者だけの問題
- **SLOTS>=2 & CTX_SIZE=0 は窓が縮む。** `--ctx-size` に触らないまま `--parallel N`
  を立てると窓は 16384÷N。SLOTS=2 の窓 8192 は上の分布の p99（約9600）が超え、
  HTTP 400 が混ざり始める。mod.json の note は VRAM の警告だけで、縮む側に触れて
  いない
- **`erased invalidated context checkpoint` は失敗の確定マーカーではない。**
  `-cpent 256` を付けた状態でも27回出るが、再計算は増えない（復元がより近い
  チェックポイントから成功している）。失敗の判定はログの行ではなく `prompt_n`
  で行うこと

同じ1点（`sidecar_process:popen_sidecar`）を包む MOD が他に無いことも確認した
（62 MOD 中 127 のみ）。

**実機プレイでの確認（2026-08-14 朝）。** 26B・既定設定で起動したゲームのライブの
コマンドラインに v2 の書き換えが載っていることを確認（欄由来の `--parallel 1` を
その場で書き換え、`--checkpoint-every-n-tokens 256` を追加、`--ctx-size 16384` は
不変。`/slots` は1スロット・窓16384）。プレイ15分を20秒間隔で45回サンプリングした
VRAM:

| 区間 | VRAM (MiB) | 状況 |
|---|---|---|
| 通常プレイ | 15,590 前後 | 26B + ゲーム描画 |
| 画像生成中（約2.5分） | **12,180** | ドライバが LLM の約3.4 GB を共有メモリへ退避 |
| 生成後の定常 | 15,748〜15,873 | ページが戻り、生成前より 160〜280 MiB 高い |

最大 15,873 MiB で、崖接触の目安 16,200 を一度も超えなかった（余白の最小 503 MiB）。
画像（背景1枚＋キャラ一式・透過処理込み）は正常に生成され、その間も llama-server は
同一プロセスのまま生き残った。**背景除去サイドカーの素通しは実機でも無害。**
なお生成後の定常は高止まりするので、`CTX_SIZE` を上げる場合はこの分（実測で
最大 280 MiB）も余白に含めて見積もること。

**多重送信のライブ計測（2026-08-14、プレイ30分）。** 2026-07-25 のプロキシログとは
独立に、`/slots` の `is_processing` と `id_task` を0.2秒間隔で 8943 回ポーリングして
測り直した（失敗0）。

- リクエスト 40 件 / busy 率 **4.9%** / 所要は中央 2.6 秒・p90 3.9・最大 4.8 秒
- アイドルを挟まない id_task の切り替え＝**キュー待ちの上限値は 3 件（7.5%）**。
  0.2秒の分解能では「先行完了の直後に次が来た」連鎖呼び出しと区別できないため、
  真の同時送信はこれ以下
- 旧計測にあった最長 97.4 秒のような長い要求は現れなかった（最大 4.8 秒）

利用者の体感も「遅延なし」。キュー待ちが起きても待ちは走行中タスクの残り（この回は
最長でも数秒）で、`--parallel 1` による実害は観測されない。旧計測の「94%が逐次・
重なりは6%」とも符合する。

ただし利用者は合間合間に遊ぶスタイルのため、30分の一枠では母数（40件）が足りない
という指摘があった。重なりの率はリクエストあたりで数えるので放置時間には薄まらない
が、母数が要る。外部スクリプトでのポーリング監視を経て、利用者の提案で
**200番台の probe（`216_probe_llm_overlap`）に置き換えた**。プロセス内で
`LlamaCppClient` の3点を包んで数えるので、0.2秒の分解能問題が消えて連鎖と真の
多重を厳密に区別でき、ゲームを起動するたびに勝手に録れる（`out\llm_overlap.log`）。
溜まり次第集計して、この節の数字を差し替える。つなぎで走らせた
`out\reverify_127\watch_overlap_long.py` は次のゲーム終了後に自動で止まる。

**`216_` による本計測（2026-08-14 15:34〜21:14、260件）。** プロセス内の実測で
判定が出た。

| | 値 |
|---|---|
| リクエスト | 260 件（すべて `chat` 経由） |
| 真の多重送信 | **20 件（7.7%）** |
| 同時本数の最大 | **2**（3本以上は一度も無い） |
| 所要 | 中央 2.26 秒 / p90 3.72 / 最大 31.26 秒 |

3つの独立な測り方が同じ率に収まった: プロキシログ 6%（62件・2026-07-25）、
`/slots` ポーリング ≤7.5%（40件）、プロセス内 7.7%（260件）。

重なりの中身はログの形で読める。**ほぼ全件が「同じ場面が2本を約0.1秒差で
同時発火するペア」**で、`--parallel 1` の直列化による後着の追加待ちは実測
0.4〜0.9 秒。最長 31.26 秒の要求に後着が並んだ最悪例でも、並んだ側の待ちは
1秒未満だった（長い方があとから来た）。逆順（長い要求の後ろに会話が並ぶ）は
今回の260件では発生していない。起こる確率は 7.7% × 長い要求の時間占有率なので
低いが、ゼロではない。これが `--parallel 1` の理論上の最悪ケースとして残る。

**判定: `--parallel 1` の待ちの実害は白。** 多重送信は毎晩起こるが浅く（深さ2）、
追加待ちは1秒未満、体感報告も遅延なし。probe は入れたままなので母数は今後も
勝手に増える。傾向が変われば読み直す。
