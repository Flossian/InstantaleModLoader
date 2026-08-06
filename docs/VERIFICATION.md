# VERIFICATION: 検証記録と現在地

最終更新: 2026-08-06

何がどこまで確かめられているかの記録。

- ここにあるのはこれまでの検証結果（実測値・ログの抜粋・件数）と未確認項目の確認手順
- 「なぜそう実装したか」は TECH.md 側

---

## 1. 一覧

### 修正（100番台）

| mod | 内容 | 状態 | 根拠 |
|---|---|---|---|
| `100_fix_kivy_shutdown` | Kivy終了時 `ctypes.ArgumentError`（crash_log 47件・最多） | 決着 | §2.1（2026-07-25。実際に発火し、クラッシュにならなかった） |
| `101_fix_npc_employ_price` | `KeyError: 80`（雇用価格の定義域外） | 解決 | §2.2（実行時の総当たりで確定） |
| `102_fix_prompt_dedup` | DEDUP | 実経路で検証済 | §2.3 |
| `103_fix_eventlog_trim` | EVENTLOG | 実経路で検証済 | §2.3 |
| `104_balance_area_bgm` | エリアBGMの偏り是正 | オフライン検証済・ゲーム内未確認 | §2.4 / §3.4 |
| `105_fix_schema_compact` | COMPACT | 実機で検証済（33件 73.5%減） | §2.3 |
| `106_fix_battle_bgm_restore` | 戦闘後にBGMが戻らない | 決着（原因を行・型まで確定。3つの起点すべてが実機で発火し、終了後 1/8 チャンネル） | §2.5 |
| `107_fix_battle_flag_stuck` | 戦闘後も `in_battle` が 1 のまま残る | 決着（原因確定 8/8 対 9/9 の非対称・注入時の掃除・戦闘終了時の発火まで実機確認。2026-07-28）。ロード時の発火のみ未観測。2026-08-03 に初版のままだった `hasattr` を `frames.attr` へ（§2.33） | §2.5 / §2.12 / §3.1 |
| `108_fix_shop_inventory_overflow` | 売買画面を開くと `IndexError` で落ちる | 修正投入済（原因はクラッシュ全文から確定）。救済経路はまだ一度も発火していない（正常時の寸法 104 件のみ） | §2.16 / §3.8 |
| `109_fix_item_detail_autosize` | アイテム説明欄が固定サイズで長い説明・名前が切れる | 実機で発火（箱が 500 → 最大 1300 まで伸びている）。横幅の拡張は未観測 | §2.17 |
| `110_fix_character_name_path` | 名前の `"` でキャラクタ画像が生成できない（`OSError: [WinError 123]`） | 決着（`id='101'` の改名・画像8点の生成・`WinError 123` が増えないことまで実機確認。2026-07-28） | §2.14 / §2.15 |
| `111_llm_prompt_replace` | プロンプトの置換ルール（プロキシの REPLACE をプロセス内へ） | オフライン検証済（63件全通。同梱ルール29行を実プロンプト51,897件に当てて 26/27 グループが発火）・実機未確認 | §2.24 |
| `112_ui_text_spacing` | 本文の行間が広く、段落の間に空行が入って読みづらい | 決着（`line_height` 1.8 → 1.44 を実機確認。2026-08-03 の退行 ＝ ラベルを見失う件と、余計なテクスチャ作り直しで打ち出しが 1.6 倍遅くなる件も、修正後の実測まで確認） | §2.25 / §2.32 / §2.34 |
| `113_ui_text_expand` | 本文の表示域が狭く、長い応答を読むのに毎回スクロールする | 実機で6回直している（最新は 2026-08-02 のアイテム移動が壊れる不具合。ボタンを HUD 直下ではなくその中の `FloatLayout` へ移した）。それ以前の4回目までは利用者の確認済み: 1回目 枠線が付いてこない、2回目 上下に伸びる＋ボタンが画面上端、3回目 会話でボタンが左へ飛ぶ。2026-08-03 に置き場所の選び方を `ui.overlay_host` へ移した（先頭の子 → 最後尾の子。他の MOD のウィジェットを掴まない） | §2.26 / §2.33 |
| `114_ui_input_focus` | 自由入力を送るたびに入力欄からフォーカスが外れ、毎回クリックし直す | オフライン検証済（24件全通）・実機未確認。入力欄の特定（`focus` と `insert_text` を持つウィジェット）と、送信ボタンの `disabled` の出入りを合図にする作りはどちらも実機で未観測。最初の起動で `out\input_focus.log` の `input ...` の行を確認すること | §2.27 |
| `115_ui_item_list_fit` | 入力欄から開くアイテム一覧が、件数が多いと画面の上へはみ出して押せない | 実機で成立（2026-08-02。20件が2列で表示され、画面内に収まることを利用者が確認）。そこまでに実機で3回外している（吹き出しの誤認で画面が崩れる → 一覧を1つも掴めない → `cols` は当たるのに見た目が変わらない）。決定打は `rows`（件数）が入っていたこと。オフライン50件全通。残るのはスキル一覧と、窓の大きさを変えたときの追従 | §2.28 |
| `116_ui_party_expand` | 4人目以降の仲間がパーティ欄に出ない（ゲームは枠を3つしか作らない） | 実機で表示・押下とも成立（2026-08-03、6人まで）。同じ日に実機で5回外して直した: (1) 枠線が付かない（canvas は複製されない → ゲームの `add_border` を借りる）、(2) 元の枠が数 px ずれる（`size_hint_y` は `0.33` で 1/3 ではない → 実測座標に釘付け）、(3) ゲームの選択肢が押せなくなる（重なるものの `disabled` を控え→書き戻していた）、(4) 覆った先の選択肢が透けて見え・押せる（足した枠は背景を持たない → 黒い板を1枚敷く）、(5) 雇い直しでクラッシュ（`update_party_display` は帯の子を1つずつ枠として塗るので、帯に置いた黒い板が枠として塗られて `IndexError` → 板は帯の外・帯のすぐ後ろへ）。教訓は2つ ―「他人が管理する状態は控えて書き戻さない」「他人の入れ物に、他人が数えている物と違う物を混ぜない」。仲間の増減は `add_party_member` / `remove_party_member` からその場で追う。2026-08-03 に、`113_` から写した「HUD の先頭の子を置き場所にする」を `ui.overlay_host` へ直した（相手のボタンの中へ入り込みうる状態だった）。残るのは足した枠の立ち絵の見え方（`PORTRAIT_FIT` で選べる） | §2.8（GAME.md）/ §2.33 |
| `117_message_text_integrity` | ゲームがラベルへ載せ切る前に長い本文を切ってしまう（1,000文字にも届かないことがある） | オフライン検証済（12件）。実機で発火（打ち出しの計測中、ラベルが `len=1020` で頭打ちになっていた。2026-08-03）。切り詰めの見え方そのものは未評価。取り込み後に `texture_update()` の明示呼び出しを外した（§2.34） | §2.34 |
| `118_batch_message_render` | 本文の出し方（逐次／一括）と、読み終わった本文の灰色化 | オフライン検証済（68件）。既定を逐次表示＋クリックで打ち切りに変え、灰色化の基準に経過セッション数を足した。**打ち切りは実機で5回外している。** 順に、(1) 正本を `app.display_text` から読もうとした（そこには無い。`frames.attr` の既定値が文字列の `MISSING` なので `isinstance(str)` を素通りしていた ― TECH.md §5.2）、(2) 終端の呼び出しより先に本文を書いた（ゲームが組み直すと消える）、(3) 正本へ書けば画面が塗り直されると思っていた、(4) 塗り直しても高さが古いままで増えた行が切り落とされた（Kivy はテクスチャの作り直しを次のフレームへ回す。GAME.md §2.3）、(5) 正本の末尾が本文の先頭からの切り出しと文字単位で一致しない（ゲームは打ち出しの最中に改行を混ぜる）。決着したのは**書くのをやめたとき**。`hud.display_text` は正本ではなく写しで、書いても 0.1 秒後に作り直されていた（`after the skip +0.1s` で `canonical` が書く前の長さに戻る）。いまは1文字ずつの呼び出しを**その場で最後まで回し**、回している間だけ塗り直しを止めて、終わってから1回だけ塗る。書くのはゲーム自身なので、正本の在り処も打ち出し中の整形も知らなくてよい。回した後に残る予約は捨てる（渡すと終端を二度踏んで次の本文が消える）。**教訓は「他人の状態は名前で当てて書かない。他人の経路を回す」。** 灰色化も同じ根で外していた ― 控えた本文をそのまま探すと、打ち出し中に混ざる改行のせいで見つかる本文と見つからない本文が混ざり、色が白・灰・白・灰と交互になる。空白を落として突き合わせ、位置だけ元に戻す形に直した（`compact`）。色を**外す**側でも1つ ― タグの付いた文字列を残したまま `markup` を落とすと、ゲームが塗り直すまでの間タグが文字として出る（場面転換で見えた）。素の本文に戻してから落とす。順序の間違いは描く側（次のフレーム）では捕まらないので、オフラインでは `markup` を切る瞬間に検査している。 **多重塗り直しは出していない（実機実測、2026-08-06）。** `211_probe_text_speed` の同時計測で `render x1.00/tick`（1ティックにつきテクスチャの作り直しは1回 ＝ 余計な作り直し無し。2.00 以上なら誰かが二度手間）、`repaint avg=0.0〜0.5ms`（間隔 42.9ms の約1%）、`fps=78〜80`。クリックで打ち切った回は `tick x60 interval avg=8.5ms render x0.22/tick` ＝ 回している間の塗り直しが実際に止まっている。オフラインで MOD 側の手間だけを測ると 1文字あたり +0.001ms、`117_` の窓が1文字ずつずれて色の控えが毎回無効になる状況でも +0.16ms（同じく間隔の 0.4%）。効くのは覚えている本文の数（`MAX_SEGMENTS`）なので、重くなったらそこを削る。一括表示の前提は実測と一致していて、二度手間を外した後でも1文字あたり 8.1ms（間隔の21%）が残る ＝ 狙いどころは合っている | §2.34 |
| `120_fix_npc_name_collision` | NPC の名前が重複する（バルガス / ヴァルガス / 「隻眼の」バルガス） | オフライン検証済（93件全通・`tools/test_npc_name_dedup.py`）・実機未確認。仕掛け先は `World.generate_character`（本命）と `Character.__init__`（受け皿）で、どちらも `out/recon/targets.txt` に実在する。最初の起動で `out\modloader.log` の `npc name dedup: roster=...` の行（名簿が読めているか）と、`out
| `121_ui_character_sheet` | プレイヤーの人物欄に手配度・スキル・特性が出ない（右半分は空の箱のまま） | オフライン検証済（82件全通・`tools/test_ui_character_sheet.py`）。載せる値の在り処は `212_probe_character_sheet` の計測で確定（2026-08-06、窓 1920x1000）。実機で1度外して直している ― 「ゲームが決めた寸法の 1.4 倍」で広げると窓を小さくしたときに下の情報欄や本文へ食い込むので、四辺を窓に対する割合で持つ形にした。残るのは窓の大きさを変えたときの追従と、プレイヤー以外の人物欄を開いた場合 | なし（記録は §2 に未作成。経緯は MOD の docstring） |
pc_name.log` に行が出るかを見ること。新しい NPC が `save_data_dict['npcs']` に先に書かれるという前提（GAME.md §2.23）に立っているので、素データの書き換え件数（`raw table(s)`）が 0 のままなら前提が外れている | §2.35 |
| `119_fix_crime_attribution` | NPC や第三者の犯罪が主人公の犯罪として扱われる | オフライン検証済（4件）。適用は実機で確認したが一度も発火していない（`out\crime_attribution_fix.log` が未生成 ＝ LLM がまだ判別のマーカーを出していない）。既定が素通し側に倒れているので、出るまでは何も起きない | なし |

### 機能追加（300番台）

| mod | 内容 | 状態 | オフライン検証 |
|---|---|---|---|
| `300_event_facility_arrival` | 施設到着時にNPCが話しかけてくる | 実機確認済（両モード） | 45件全通 |
| `301_quest_from_conversation` | 会話から依頼を受注/生成 | 実機確認済（設置・生成・受注・掲示板の絞り込み・HUD塗り替えまで。2026-07-28・§2.13） | 49件全通（§3.2.1 の1件は解消済み） |
| `302_leave_party_in_conversation` | 仲間と会話から別れる | 実機確認済（一部経路が未実測。§3.3）。2026-08-03 に残骸の掃除が `309_` のキャンセルを消していたのを修正（§2.31） | 78件全通 |
| `303_quest_end_party_to_guild` | クエスト解散で町のギルドに残す | 実機未確認（§3.3） | 45件全通 |
| `304_quest_end_keep_party` | クエストクリアで解散しない（`303_` より外側） | 実機未確認（§3.3） | 50件全通 |
| `305_mini_quest` | 戦闘を伴わないミニクエスト（採集・救助・偵察） | **デバッグ用に降格（2026-08-06、ユーザー判断）。** 品質が目標に届かないため、既定では読み込まない（`mod.json` の `"debug": true`）。依頼の中身と非戦闘の進行までは実機で成立、**終わり方が定まらない**（実機4回。§2.19〜§2.23）。最後の修正（撤退の判定を戻り値ごと差し替え）は**未検証**。再開するなら §3.10 から | 111件全通。うち4件は `output_data/` の実プロンプト 612件との突き合わせ |
| `306_party_train_exp` | 訓練の経験値を同行者にも入れる（仲間もレベルアップ） | 実機で発火（2026-07-30。宿屋の訓練で `VacationTrainManager` を捕まえ、プレイヤーと同額 686852 exp を同行者に写した）。レベルアップは未観測で、この回は必要経験値に届いていない（プレイヤーも上がっていない）。§3.12 | 59件全通 |
| `307_area_move_dungeon` | エリア移動の第3の手段「危険な道を行く」（道中のクエストを踏破すると着く。合計14日以下） | 実機3回で成立（2026-08-01。3回目は帰還の直後に移動するところまで確認）。その後に足した2点（移動中の文言を伏せる・体力3分の1未満で断る）は実機未確認。放棄の経路も未実施。§3.13 | 130件全通 |
| `308_battle_damage_display` | 戦闘で動いた HP を数字で出す（味方が与えたぶんも、受けたぶんも） | 実機で成立（2026-08-01）。通常攻撃・スキル・とどめの一撃（1回目で落ちていた不具合。修正後に確認）まで表示。残るのはコロシアムと、味方が倒れた／逃げた場合。§3.14 | 72件全通 |
| `309_office_pardon` | 役場で罰金を納めて、その土地の手配（`area_history` の `lawfulness`）を帳消しにする | 実機で成立（2026-08-01。手配度 -10 の状態で役場に入り、設置・会話を挟んでの再設置・支払い・所持金の減り・`-10` → `10`・ゲーム自身のセーブへの永続まで通しで確認）。残るのは投獄・市民権の系統との関係。§3.15 | 73件全通 |
| `311_npc_profile_memory` | 会話の内容から NPC の人物像と「その人物から見たプレイヤー」を作り、以降の会話のプロフィール欄に載せる | 実機で発火（2026-08-03。`state\npc_profiles\<世界名>.json` に3世界ぶんの人物像が溜まっている）。受け答えが実際に変わったかは未評価。取り込み時に、`apply()` が最大8回走る前提での二重ワーカーを修正（状態を `sys` に載せる。§3.4）。**版2で `about_player` 欄・構造化出力での受け取り・判明した事実の追記控えを追加。いずれも実機未評価** | 162件全通 |
| `312_shop_restock` | ゲーム内で一定の日数が経った店の品揃えを入れ替える（売った品で埋まって売却できなくなるのを防ぐ） | オフライン検証済・**実機未確認**。前提「主の持ち物を空にすれば、ゲームが初回と同じ経路で作り直す」は未実測（本体のソースが読めないため確かめようがない）。外れた場合に備えて、空にした後の補充を見て駄目なら控えを戻す作りにしてある。§3.16 | 26件全通 |

### ローダ

| 項目 | 状態 | 根拠 |
|---|---|---|
| 世代管理（再注入で層が積み重ならない） | 検証済 | 再注入で層が積み上がらないことを確認 |
| 遅延 import の保留＋当て直し | オフライン16項目全通・実機での流れは未確認 | §2.7 / §3.5 |

### 計測待機中（発生すれば自動で原因が確定する）

| バグ | 状態 |
|---|---|
| `AttributeError: 'FreeInputStart' object has no attribute 'facility_move_to'`（2件） | `201_` のトリップワイヤが待機中。発火ゼロ・ノイズゼロ。実呼び出し5回とも属性は無いまま正常終了 |
| `AssertionError: literal "expected" cannot be empty, typing.Literal[]`（2件） | `203_` が `create_model` をラップ。`Literal[0]` は全期間で0件。2026-07-28 にクエスト1件を頭から終わりまで通し、`Literal` が消費に応じて減っていく様子と、候補が尽きたモデルはゲームが作らないことを実測（§2.18）。第一容疑だった「敵候補0件」は弱まったが、0件を観測できたのは `FieldEvent` だけで `Battle` / `EncounterFinalBoss` では未観測 |

`create_model` の alias は5モジュールに複製されている（`pydantic.main` /
`llm_manager_world_generate` / `llm_manager_character_create` / `save_world_json` /
`llm_manager_battle`）。全て再束縛済み。

### 設計判断のための計測（バグではない）

| mod | 決めたいこと | 状態 |
|---|---|---|
| `209_probe_free_facility` | シーン記述エンジン（`scripts.free_facility`）を MOD から使えるか | 決着（2026-08-02）。フラグは施設ローカルで跨げないが、普通の施設でも走り、セーブを汚さずにプログラムを渡せる。GAME.md §2.21・記録は §2.29 / §2.30 |
| `210_probe_character_state` | NPC の退場に `is_dead` が使えるか | 答えは出た（2026-08-02）。`Character.config['is_dead']`。名簿からは外れず、読む側が飛ばす。施設の主 24/35 には使えない。GAME.md §2.22・記録は §2.29 |

どちらも読み取り専用で、答えが出た後は無効にしてよい。

### 未修正（原因確定済み・実装は別セッション）

| バグ | 状態 |
|---|---|
| （無し） | `OSError: [WinError 123]` は `110_fix_character_name_path` で決着（実装 §2.14 / 実機 §2.15） |

### 対象外

| 件数 | 内容 | 理由 |
|---|---|---|
| 35 | `RuntimeError: 同梱llama-serverが起動しませんでした` | 動作確認中の操作に起因。ゲームのバグではない |
| 40 | LLM系（`KeyError: 'timings'` / `response_format` / 接続リセット） | 同上 |
| 6 | `KeyError: '52'` `'53'` `'109'`（str キー） | 過去の手動データ作成時の不整合 |

crash_log.txt の 114 件からこれらを除くと、実バグは 12 件 / 4 種だった。

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
残るのは運用感の調整だけ（施設別の発生率と `COOLDOWN_MOVES` を実プレイの頻度に合わせる）。

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
python tools/test_arrival_event.py    # 300_  45件
python tools/test_quest_offer.py      # 301_  49件
python tools/test_party_leave.py      # 302_  78件
python tools/test_quest_end_guild.py  # 303_  45件
python tools/test_quest_end_keep.py   # 304_  50件
python tools/test_mini_quest.py       # 305_  111件（うち4件は output_data/ の実プロンプトと突き合わせ）
python tools/test_party_train_exp.py  # 306_  59件
python tools/test_area_move_dungeon.py # 307_  130件
python tools/test_battle_damage_display.py # 308_  72件
python tools/test_office_pardon.py    # 309_  73件
python tools/test_item_detail_autosize.py      # 109_  25件
python tools/test_character_name_sanitize.py   # 110_  36件
python tools/test_llm_prompt_replace.py        # 111_  63件（うち3件は output_data/ の実プロンプトと突き合わせ）
python tools/test_ui_text_spacing.py           # 112_  23件
python tools/test_ui_text_expand.py            # 113_  76件
python tools/test_ui_input_focus.py            # 114_  24件
python tools/test_ui_item_list_fit.py          # 115_  50件
python tools/test_ui_party_expand.py           # 116_  88件
python tools/test_message_text_integrity.py    # 117_  12件
python tools/test_batch_message_render.py      # 118_  68件
python tools/test_crime_attribution.py         # 119_  4件
python tools/test_npc_name_dedup.py            # 120_  93件
python tools/test_ui_character_sheet.py        # 121_  82件
python tools/test_npc_profile_memory.py        # 311_  162件
python tools/test_shop_restock.py              # 312_  26件
python tools/test_patch_registry.py            # ローダ本体（世代・設定・デバッグモード）
```

| tool | 何を通すか |
|---|---|
| `test_ui_text_spacing` | 本文のラベルを名前（`hud.text_display`）で引くこと / 本文を載せ替える MOD が先に走っていても外さないこと（2026-08-03 の退行） / 名前で引けないときだけ `vars(hud)` と木の探索に落ちること / 状態表示など無関係なラベルを掴まないこと / `line_height` がゲームの値の倍率になること / 段落の空行が詰まること / ゲームが持つ `display_text` を書き換えないこと / 1文字ずつ呼ばれても行間が縮み続けないこと / 注入し直しても倍率が二重に掛からないこと / 高さの決め直しが1フレームに1回で済むこと / 設定が空欄なら触らないこと / ラベルが見つからないビルドで何もしないこと |
| `test_ui_text_expand` | ボタンが HUD に1枚だけ足されること（塗り直しで増えない）/ アイコンが背景なしの白い線で、ボタンの内側に描かれ、押すと上下が入れ替わること・絵柄をどれに変えても描けること・「文字」を選ぶと文字ボタンに戻ること（そのときフォントを本文から写すこと）/ 「枠の右上」に置くと枠の内側に入り、枠が伸びれば一緒に上がること / キャラの欄（枠の右隣）の上に置かれること（会話で立ち絵が差し替わっても動かないこと・`source` を持たないウィジェットを立ち絵と取り違えないこと・右隣が無ければ立ち絵の上、それも無ければ隅へ落ちること・隅の指定はそのまま使うこと）/ 下端が動かず上へだけ伸びること / 幅倍率 1.0 では幅・`size_hint_x`・折り返し幅のどれにも触らないこと / 窓の大きさを変えるとボタンが付いてきて、枠の控えも新しい寸法に取り直されること（畳めなかった枠からは控え直さないこと）/ 同じ矩形に置かれた枠線が一緒に広がり一緒に戻ること・関係ない場所のウィジェットには触らないこと / 枠が倍率どおりに広がり、折り返し幅が追従すること / 窓からはみ出さないこと / `pos_hint` の無い枠は上端を保って広がり、ある枠では位置に触らないこと / もう一度押すと `size_hint` / `size` / `pos` / `text_size` が元に戻ること / ゲームが枠を組み直しても次の塗りで広がったままになること / ゲームの採寸が枠を戻すビルドではそれを呼ぶのをやめること / 注入し直しても広げた後の寸法を設計値と取り違えず、画面の今の姿を引き継ぐこと / ラベルが見つからないビルドで何もしないこと / 置き場所（HUD 自身の子は増やさないこと・古い版が HUD 直下に足したボタンが移されること・他の MOD のウィジェットを置き場所にしないこと・ゲームが一時的に出している窓を置き場所にせず、その窓が消えてもボタンが残ること。2026-08-03） |
| `test_ui_party_expand` | 4人目以降の枠が足されること（増減がその場で追われること・雇い直しでゲームの塗り直しを落とさないこと）/ 枠線をゲームの `add_border` から借りること・無いビルドでは自前で描くこと / 元の枠の座標に触らないこと / 覆う相手を帯の直接の子だけにすること（ゲームの選択肢の `disabled` を書き戻さないこと）/ 黒い板を帯の外に置くこと（帯の子に混ぜない）/ 板と隠した相手が次の注入で片付くこと / 立ち絵の見せ方（`PORTRAIT_FIT`）/ 置き場所（`test_ui_text_expand` と同じ4点。2026-08-03）/ パーティ欄が無いビルドで何もしないこと |
| `test_item_detail_autosize` | 短い文では設計値のまま1px も変わらないこと / 長い説明で高さが伸びること / 上端を保って下へ伸びること / `pos_hint` の `top` が新しい高さに追従すること / 縦横比を基準にした横の拡張と窓の右端での頭打ち / 設計値の写し取り（ホバーのたびに値が育たないこと・ゲームが箱を組み直したときだけ写し直すこと）/ 伸びた箱を窓の内側へ戻すこと / ラベルが欠けていても触らないこと / 例外を握り潰していないこと |
| `test_character_name_sanitize` | 変換表 / 末尾の空白・ピリオド / 制御文字 / 実データの正しい名前が 1 文字も変わらないこと / 生成時の適用（位置引数・`name=None` を含む）/ 注入時とロード時の救済（名簿が辞書でも配列でも）/ 予約デバイス名と空になる名前に触らないこと / 旧名がログに残ること / 実地の `os.makedirs` |
| `test_npc_name_dedup` | 名簿（`male` / `female` / `epithets` を読むこと・知らない鍵は読まないこと・同梱の名簿が読む鍵だけを持つこと・壊れた行と壊れたファイルで落ちないこと・利用者の `npc.json` が同梱より優先されること・`mod_dir` が `None` でも落ちないこと）/ 男女（実データの `category` 8種すべて。`woman` は `man` を含む）/ 二つ名（既定 30% 前後・0% と 100%・引くたび変わること・名簿のものであること）/ 選び方（1件も落とさず並べ替えること・引くたび並びが変わること・どの名前も先頭に来ること・渡した名簿を書き換えないこと・空の名簿で落ちないこと・MOD 専用の `Random` から引きグローバルの列をずらさないこと）/ 読みの骨（`バルガス` / `ヴァルガス` / `ばるがす` / `バルカス` / `「隻眼の」バルガス` / `隻眼のバルガス` / `バルガス2` が同じ鍵に落ちること・`ティ`/`チ`・`ジェ`/`ゼ`・`ファ`/`ハ`・長音・ラテン文字の `v`/`b`）/ 別人を巻き込まないこと（`アレン・スミス` と `アレン・ジョーンズ` / `ジル` と `ジン` / `ナナシ` と `ナシ` / 別々の漢字）/ `SIMILARITY` の3段と `FOLD_VOICING` / 生成時の改名（名簿の名前が付くこと・男女が分かれること・元の二つ名を引き継がないこと・素データ（`npcs`）の書き換え・セーブの項目の並びが動かないこと）/ 名簿が無いとき・使い切ったときに元の名前のまま通し、無いことを警告すること / 触らないもの（敵・プレイヤー）/ プレイヤーは突き合わせ相手には入ること / 既に世界に居る重複は既定で記録だけ・`FIX_EXISTING` で直ること / 引くたび違う名前になること・一度付いた名前は二度目に変わらないこと・30人が同じ名前で来ても全員が別の名前になること / 世界を読み直すと控えを作り直すこと |
| `test_arrival_event` | 会話フェーズの起こし方 / 待ち合わせ / 取り消し / 読み替え / 発火条件 / 整形 / 戻り値の形3種 |
| `test_quest_offer` | 設置位置 / 依頼一覧で入れ子にならないこと / 押下の横取りと素通し / 会話を閉じてから開くこと / `end_text` の差し替え / 押下と同じ流れでは塗らず次のフレームで HUD を塗ること / 掲示板の絞り込み / ゲーム本来の掲示板を触らないこと / `302_` との印の衝突 / 残骸の掃除（セーブから戻った印無しの自前ボタンを差し直すこと・ゲーム側の同名ボタンを落とさないこと・他の MOD の印が付いたボタンを落とさないこと・印を持たない `Screen` では何もしないこと）/ `311_` が覚えた依頼人の人物像（生成プロンプトに載ること・会話の記録より後ろに置くこと・依頼の中身にしないよう釘を刺すこと・`311_` を入れていなければ節ごと足さないこと。**本物と同じく `generate_random_quest()` の内側でフックを通す** ― 外から呼ぶと印を使い切った後になる） |
| `test_party_leave` | 設置条件 / 確認 / 実行 / 置き場所が無い場合 / ゲームが自分で置いた場合 / 名簿が残った場合 / 名簿の在り処が違う場合 / `301_` との印の衝突 / 他の MOD のボタンを消さないこと（`309_` の確認画面から1枚も落とさない・印の無い汎用語のボタンも落とさない・掃除に使う文言がこちらにしか無いものであること）/ 自分の残骸は今までどおり差し直すこと |
| `test_quest_end_guild` | 検出 / 置き先 / 差し替えの2層 / ギルドが無い土地 / 時間切れの保険 / 画面 / `302_` との重ね掛け |
| `test_npc_profile_memory` | 1ターンで控えが残ること / 抽出が返答の**後**に回りメインスレッドを止めないこと / 続けて来た抽出が直列に処理され後の更新が前を踏まえること / 受け取り（構造化出力を使える版では使うこと・`message` がリストで `timeout` 付きで `Literal` を使わないこと・落ちたら頼み文だけの JSON に降りること・囲み付き JSON を読めること・`changed` が false なら変えないこと・壊れた JSON では変えないこと・素の文章の受け皿）/ 2欄（人物像とプレイヤーへの認識が別の見出しで注入されること・認識だけでも注入すること）/ 判明した事実の控え（追記されること・重複しないこと・いつ分かったかが残ること・上限で古い方から落ちること）/ 控えの形（鍵が `RECORD_KEYS` の並びであること・移行しても旧 `slots` を消さないこと・`301_` が同じ規則で同じファイルを引けること）/ 書き出し（`json.dump` の途中で落ちても前の控えが壊れないこと・書きかけを残さないこと・落ちたことを記録すること）/ 設定（`mod.json` とコードの既定値が一致）/ 注入（複製の `profile` にだけ足し元 NPC・`messages`・人生ログを書き換えないこと・5経路すべて・キーワード引数・`.id` の無い相手）/ 別の世界・別の人物と混ざらないこと / 当て直しても控え・待ち行列・錠が1組のままであること / LLM が無くても抽出が落ちてもターンが壊れないこと |
| `test_shop_restock` | 初めて開いた店を入れ替えず基準の日だけ控えること / 日数が足りなければ持ち物に触らないこと / 日数が経った店は**ゲームの生成が走る前に**空になり、プレイヤーが売った品が残らないこと / 生成がゲームの経路（`set_item_from_world_data`）を通ること・次のフレームへ回される版でも取りこぼさないこと / 日付が巻き戻ったら控えを付け直すだけで空にしないこと / 補充されない作りでは控えを戻し、以後は空にしないこと / 段(tier)を見ていれば自分で生成を呼ぶこと / 控えの鍵が `RECORD_KEYS` の並びであること・世界ごとに別ファイルになること / 主が引けない・日数が読めない場面で何もしないこと / ロード直後（`location` が施設 id の文字列）でも主を引けること |
| `test_party_train_exp` | 写す（同じ点数が同行者に入る）/ 文言がプレイヤーの獲得経験値より後に出ること（実機の並びをそのまま真似る）/ 1行も出さずに訓練が終わったときの受け皿 / 次の訓練に持ち越さないこと/ レベルが何段でも上がること / `gain_exp` が内部で上げるビルドで二重に上げないこと / 戦闘・休養（既定）では触らないこと / 設定（宿屋・施設・休養・割合・表示）/ ゲーム自身が仲間に配ったときの二重取り回避 / 名簿が `game_variables` 側にある場合 / `world` に居ない id / `gain_exp` を通らない支給を WARN として残すこと / 当てた対象の一覧 |
| `test_llm_prompt_replace` | 置き場所（MOD フォルダの中だけを読み、`settings\` も外部プロキシも見ないこと / 利用者の `llm_replacements.txt` が同梱の `.default.txt` に優先し、置いた・消したで次のリクエストから切り替わること / 利用者のファイルが配布物に入らないこと）/ 同梱ルールが警告なしで読めること / 書式（タブ / `#offtab:` / `#off:` / 確率の省略と 0-100 の外）/ 復号（`\n` `\uXXXX` `\\` と代理対・知らないエスケープを壊さないこと）/ 正規表現（`$1` `${名}` `$&` `$$` の読み替え・不正なパターン・存在しない番号）/ グループごとに1回だけ抽選すること（合計 100 超は必ず置換・0% は抽選しない・当たれば本文の全箇所）/ 3経路（`chat` / `_apply_chat_template` / `payload`）それぞれ単体で当たること / 入れ子の経路で抽選が1回で済むこと（スレッドの印と、自分の出力の記憶。別スレッドからの二度目も止まる）/ 再読込（保存で即反映・消えたら止める・置き直せばまた効く）/ 壊さない（ルール無し・例外・`content` が無いメッセージ・元の `messages` を書き換えない）/ 記録の ON/OFF / 実在ルールと実プロンプトの突き合わせ |
| `test_area_move_dungeon` | 日数の上限（道中と移動の合計が `TRAVEL_DAYS` を超えないこと・使い切ったら 0 を渡すこと・上限 0 でも着くこと・道の外の日数送りには触らないこと）/ 確認画面への設置（「やめる」の手前・二重にならない・徒歩と馬車のボタンに触らない）/ 押下の横取りと `process_choice` 経由 / 難易度が移動元と移動先の間から抽選され、生成にもクエスト辞書（両方の格納先）にも同じ値が入ること / 難易度が引けないときに 1 へ落ちて WARN を残すこと / `area_description` への差し込みが1回で使い切られること / 受注画面へ渡すこと / 完了直後は移動せず、集落の画面に戻ってから1回だけ移動すること / 放棄では移動しないこと / `AreaMoveManager` の `args` をゲームのボタンから写すこと（徒歩・馬車の選び分け）/ 普通に移動したら控えを捨てること / 控えが `out/` に残りセーブに触らないこと・注入し直しでも生き残ること / `301_` / `302_` / `305_` との印の衝突 / 残骸の掃除（印の落ちた自前ボタンを掴むこと・他の MOD とゲームのボタンを落とさないこと） |
| `test_battle_damage_display` | 味方が敵に与えたダメージ・敵が自分や味方に与えたダメージがそれぞれ数字で出ること / とどめの一撃（その手の中で `current_enemy_dict` から抜ける敵）のダメージが出ること・撃破と分かる形になること・次の手で二重に出さないこと・HP が動かずに消えた敵（逃走）は出さないこと / 地の文の後ろに出ること（ゲーム自身の行を消さない・並べ替えない）/ 台帳方式で同じ変化が2回出ないこと（報告点を何度通しても増えない・その後の変化は取りこぼさない）/ 差が無ければ1行も出さないこと / 回復と身体の負傷 / 設定（敵側・味方側・回復・残量・下限・負傷）/ 戦闘の境目で台帳を捨てること（`start_battle` と `current_enemy_dict` の差し替えの両方。前の戦闘の HP を「回復」と誤報しない）/ 倒れて消えた敵を落とすこと / 敵が `Character` でも辞書でも読めること / 最大 HP が読めないビルドで現在値だけを出し、その旨を記録すること / 1回の報告の行数の上限 / HP を1点も書き換えないこと / 行の頭の記号（既定では付かないこと・選ぶと記号＋半角空白が付くこと・とどめの行と負傷の行にも付くこと・宣言側の既定が「なし」でそれ自身も選び直せること・候補に環境依存文字が無いこと（cp932 の NEC 特殊 / NEC 選定 IBM 拡張 / IBM 拡張を弾く判定つき）・空白や空文字を候補にしないこと） / 当てた対象の一覧（`resolve_battle_effect` とダメージの式を包まないこと） |
| `test_quest_end_keep` | 引き留め（名簿も置き直しも動かないこと）/ 離脱文の差し替え / 死別・放棄・普段の移動の素通り / 本当に外れる相手の置き直しを止めないこと / 置き先を先に聞くビルド / `303_` との重ね掛け（どちらが勝つか・`303_` が降りない場面） |

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

`boot complete: 30/30 mod(s) applied` が出れば読み込み側は健全。mod を足したら
この数も更新すること。30 個は 2026-08-06 時点の公開ぶんで、デバッグモードを切った
ときに読み込まれる本数にあたる（計測用の 12 本と、本体が取り込んだ 5 本は伏せられる）。
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
  LLM生成・プリセット・プレイヤー・セーブからのロードが全部ここを通る
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

オフライン検証は `python tools/test_character_name_sanitize.py`（36件全通）。
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

修正後、はみ出しは一度も再現していない。`out/inventory.log` の 104 件はすべて
正常サンプル（`ok`）で、そこから正常時の寸法が確定した:

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
伸びる・普通のアイテムは 1px も変わらない」が実データで確認できている。横幅の拡張は
まだ観測できていない（ログの書式に幅が入っていないので、次に確かめるなら書式を足す）。

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

この計測で `305_mini_quest` の設計が決まった。ゲームのコードにもセーブにも触らず、
生成（`random_quest_generator`）と進行判定（`quest_referee*`）の2つのプロンプトだけを
差し替える形にできる。敵とボスのデータは削らずに残す（`Literal` を空にしないため）。

### 2.19 `305_` 戦闘なしミニクエスト: 実機1回目（2026-07-28、半分成功）

投入した当日に1件通した。依頼の中身は成功、進行の止め方が失敗。
症状は「ボスを倒すまでイベントがほぼ無限に続く」。

##### 効いていたもの

```
request_summary: 辺境の湿原地帯に自生する、極めて希少な薬草
                 『テスト薬草』を5株採取してきてほしい。
```

討伐ではなく採集依頼が生成された。掲示板への設置・押下の横取り・控え・
進行側の書き換え（25ターン全部に命中）も動いた。`encounter_final_boss` は
一度も選ばれていない（素のプロンプトなら在庫が尽きた時点で強制される）。
10:49〜10:50 には実際に薬草を採取する描写まで出ている。

##### 壊れていたもの: 原因は3つ、それぞれ別

| # | 症状 | 原因 | 直し方 |
|---|---|---|---|
| 1 | `return_after_completion` が一度も選ばれない | 進行側に渡していたお題が種類のテンプレ文（「指定された物を必要な数だけ集める」）で、このクエストの達成条件ではなかった。referee は何をもって達成かを判定できない | 生成時に `request_summary` を控えへ保存し、`【依頼の内容（達成条件はこれ）】` として渡す |
| 2 | `nothing_happens` が12ターン連続（ほぼ同じ描写の繰り返し＝観測された「無限」） | 生成されたフィールドイベントが2個だけで3ターン目に枯れた。battle を封じたので残る選択肢が `nothing_happens` しか無くなった | 生成側でイベントを 4〜6 個要求。加えて `nothing_happens` の説明行を書き換え、進展が2ターン無ければ帰還を選ばせる |
| 3 | 最終的に battle を8回選びボスを倒して終了 | プロンプトに `- 残り通常戦闘回数: 3` `- 残り中ボス戦闘: [10体]` `- 残りラスボス戦闘: [...]` が事実として並び続け、在庫消化の圧力になっていた。生成時に miniboss が5体も作られていた（通常は1体） | 1文目に「在庫を消化する必要は一切無い。目的が果たされたなら帰還させてよい」を明記。生成側で `miniboss は1体も設定しないこと`（`normal` と `boss` は残すので `Literal` は空にならない） |

##### この回で誤判定しかけたこと（2件）

- `output_data/` に書き換えが映らない。保存は `chat` より上流なので、記録された
  プロンプトは書き換え前のもの。「効いていない」と読みかけた。GAME.md §2.12 に
  独立した事実として書いた（同じ記録で `105_` の COMPACT 後も `$defs` が残っている
  のが証拠）
- 監査ログの枠を生成と進行で共有していた。生成が内部で4回 LLM を呼んだ回に
  `AUDIT_FIRST_N` を使い切り、肝心の進行側の中身が1行も残らなかった。
  原因を読むためのログが、原因を読みたい場面で無かった。枠は用途ごとに分けること

### 2.20 `305_` 実機2回目: 直った3点と、新しく出た1点（2026-07-28）

§2.19 の修正を入れて再度1件通した。症状は
「帰還したら撤退してクエスト失敗扱いになった」。

##### 直った（§2.19 の3点はすべて解消）

| 前回の症状 | 今回 |
|---|---|
| お題がテンプレ文だった | `【依頼の内容（達成条件はこれ）】湿原に自生する『テスト菌株』を15個採取して持ち帰ってください。` が毎ターン入った |
| イベントが2個で枯れた | 5個生成され、`field_event` が5ターン連続で走った（採取が実際に進む描写） |
| miniboss 5体・battle 8回 | `- 残り中ボス戦闘: []`（0体）。battle は1回だけ |

進行ログも読めるようになった（監査枠を分けた効果）。`encounter_final_boss` は
今回も0回。

##### 新しく出た1点: 達成したのに「撤退」になった

最終ターンの実データ:

```
プレイヤーの入力: 「採取を終了し、帰還の準備を整える」
narration:       「…目標の数は、既に十分な数に達している。」
turn_resolution: retire_from_the_quest        ← ★
```

referee 自身が「目標の数に達している」と描写しながら撤退を選んでいる。
原因は書き換えていなかった2行で、どちらも帰還の意思＝放棄と定義していた:

```
- retire_from_the_quest: クエストを放棄し帰還する。プレイヤーがその意思を明確に
  示したときにのみ選択するべきだが…しかし具体的が無いならばさっさと撤退させること。
- クエスト攻略を諦めての撤退以外では、クエストエリア外への移動は認めない。
  プレイヤーが明確に中断と帰還の意思を持つ場合のみ認められる。
```

討伐クエストではこれで正しい（帰るなら諦めたということ）。お使いでは逆で、
目的を果たしたから帰る。両方とも `output_data/` の 460/460 で安定していたので、
アンカーに追加して書き換えた:

- 撤退は「目的を果たせないまま放棄して帰還する」に限定し、
  「目的が果たされているなら、プレイヤーが帰還を望んでもこれを選んではならない。
  必ず `return_after_completion` を選ぶこと」を明記
- エリア外への移動は「帰還のときにのみ認められる。まず目的が果たされているかを
  判断し、果たされていれば達成としての帰還、そうでなければ放棄」に置き換え
- 1文目にも同じ判断を書いた

撤退そのものは残してある。達成できない依頼で詰ませないため（回帰項目に入れた）。

##### この2回で分かった、書き換えの当て方

> 1つの前提は1箇所には書かれていない。「討伐クエストである」という前提は
> 完了条件・戦闘優先・ラスボス強制・`nothing_happens`・撤退の定義・
> エリア外移動の定義と、少なくとも6箇所に分かれて書かれていた。
> 1回の実機で1〜2箇所ずつ見つかる形になったので、実機で1周する前に全部を
> 見つけようとしないのが正しい進め方だった。毎回ログの1行が次の箇所を教える。

##### まだ分かっていないこと

`return_after_completion` をゲームが素直に受けるかは、依然として未検証。
2回とも LLM が選ばなかったので（1回目は完了判定できず、2回目は撤退と取り違えた）、
`quest_process_phase` 側の可否を試せていない。次の実機で選ばれれば同時に判明する。

### 2.21 `305_` 実機3回目: 撤退は直った。今度は在庫切れでボス戦へ流れた（2026-07-28）

##### 直った

`QuestRetireManager` は 0 回（`quest_flow.log` 全期間で 0）。§2.20 の
「達成したのに撤退」は再発していない。

##### 今回の終わり方

```
14:40:06 quest_referee_with_free_action -> battle  enemies=['テストボスA']
         - 残り通常戦闘回数: **0**   - 残り中ボス戦闘: **[]**
         - 残りフィールドイベント: **[]**   - 残りラスボス戦闘: **['テストボスA']**
14:41:13 quest_boss_battle_log_summarizer → QuestEndManager
14:42:08 quest_summarizer
```

在庫が全部尽きて、残ったのがラスボスだけになった。そこへ `battle` で流れ、
倒したのでクエストが終わった。つまり2回目と同じ「ボスを倒して完了」。
`return_after_completion` は3回とも一度も選ばれていない。

##### 原因は、まだ書き換えていなかった2行

| 行 | なぜ効くか |
|---|---|
| `- battle: 雑魚敵や中ボスやラスボスとの戦闘の開始。…` | ラスボスを `battle` の相手に選んでよいと明記されている。`encounter_final_boss` を封じても、こちらから同じ結果に行ける |
| `- 残りラスボス戦闘: [名前]` | ゲームの状態そのもの。「まだやることが残っている」という圧力として毎ターン効き続ける |

`- battle:` の行は完全一致では当たらない。末尾の適正数（「平均1.6体」）が
難易度で変わり、実データでは 6 通りあった（1.0 / 1.1 / 1.2 / 1.3 / 1.4 / 1.6）。
行頭で当てて末尾に書き足す規則（`REFEREE_LINE_RULES`）を新設した。
合計は 476/476 で全件に在る。

`- 残りラスボス戦闘:` も同じ仕組みで注記する。数や名前は書き換えない
（ゲームが握っている事実なので嘘を書かない。扱い方だけ足す）:

```
- 残りラスボス戦闘: **['テストボスB']**（**このクエストでは戦わない。** 残っていても
  無視すること。これが残っているせいでクエストが終わらない、ということは無い）
```

##### この回で見つけた自分のバグ

書き換えが冪等でなかった。`nothing_happens` の置換文は元の文を含む形
（元の説明に書き足す）なので、同じ文字列に2度当てると際限なく伸びる。実経路では
プロンプトが毎ターン組み直されるので一度しか通らず害は出ていなかったが、
テストで再適用したら伸びた。置換後の文が既に在るなら当てないようにして、
回帰項目に入れた（`104_` の「冪等なら発火箇所が致命的でなくなる」と同じ話）。

### 2.22 `305_` 実機4回目: 撤退は直っていなかった。文面をやめて戻り値を持つ（2026-07-28）

##### 前提: この回は修正前のコードで走っている

```
最後の注入   14:28:05   (out/modloader.log)
305_ の修正  14:47      ← §2.21 の battle / 残りラスボス戦闘 の行
208_ の修正  14:48      ← opacity の見張り
プレイ       14:48〜14:55
```

`update_content` は 14:53 / 14:54 / 14:55 と記録されているのに `opacity ->` が
0行。注入し直していないので新しいコードが載っていない。§2.21 の修正も
この回では効いていない。

> 編集したら注入し直す。§2 の最重要項目（`python injector.py`）。
> 「直したのに変わらない」と読み違える一番の原因なので、判定の前に
> `modloader.log` の最後の `boot #N` と mod ファイルの更新時刻を必ず見比べること。

##### 撤退が再発した。そして原因は §2.20 の修正では消せない

```
入力: 「湿原を離れる」
描写: 「…目的の品は十分に確保されており、これ以上の滞在は命の保証を危うくするに過ぎない。」
判定: retire_from_the_quest
```

§2.20 の書き換え（撤退の説明文・エリア外移動の定義・1文目の判断）はこの回に
載っていた（14:28 の注入に含まれる）。それでも押し切られている。

§2.21 で「撤退は直った」と書いたのは誤りだった。根拠にした
「`QuestRetireManager` が0回」は、その回はプレイヤーが帰還を口にしなかった
だけで、直った証拠になっていない。症状が出る条件を踏んでいない結果を、
直った証拠に使わないこと。

##### 文面での説得をやめ、戻り値を差し替える

同じ1点を文面で2回外している（2回目・4回目）。素の説明文の
「プレイヤーがその意思を明確に示したときにのみ選択するべき」が強く、
帰還の意思＝放棄という読みを上書きしきれない。

ミニクエストには倒すべきラスボスが居ない。目的を果たした状態での帰還は定義上
そのまま達成なので、`quest_referee*` の戻り値で `retire_from_the_quest` を
`return_after_completion` に差し替える（`RETURN_INSTEAD_OF_RETIRE`、既定 ON）。
対象はこの MOD が作った依頼だけ（`quest_data['quest_title']` を控えと照合）。

戻り値の形は実測できていない。`output_data/` に残るのは保存側が整えた形で、
関数が返したそのものではない。そこで dict でも属性でも読み書きできるようにし、
どちらでもなければ何もせず素通しする（推測して壊すより素通しが正しい）。
最初の1回だけ `result shape = ...` を残すので、次のプレイで形も確定する。

これで「ゲームが `return_after_completion` を受けるか」も同時に判明する。
4回とも LLM が選ばなかったので、まだ一度も試せていない唯一の項目。

### 2.23 待機表示を共通部品へ移した（2026-07-28）

クエスト作成の待ち時間にも「…」を出す（`301_` の会話からの生成と同じ）。
生成は LLM を回すので実測 27〜352 秒かかり、何も出ないと画面が固まったように見える。

点のアニメーションの作りは `301_` が実機で測って作ったもの。ゲーム自身の
「クエストを探す」を1回押した記録から、`.` → `..` → `...` を 0.3 秒周期・
ボタン全枠・`is_button_enabled=False`・送信ボタン無効まで真似ている。
これは「ゲームがどう動いているか」なので `ui.Screen` へ移した（TECH.md §5.1）。
`301_` は `screen.busy_on` / `busy_off` を呼ぶだけになり、`305_` も同じものを使う。

`305_` 側の設計判断だけ MOD に残してある:

- 生成に入る直前に出し、例外で抜けるときも必ず解く（出したまま抜けると操作不能）
- 掲示板を開き直す経路は `busy_off(restore=False)`。元の選択肢を塗り直すと
  一瞬だけ古い画面が見える（`301_` の教訓）

オフライン検証は 94 → 107 件。点のアニメーション自体は共通部品として直接
確かめている（偽ゲームは同期なので、生成の流れでは1コマも進まない。
本物は `generate_random_quest()` が別スレッドで止まっている間に Clock が回る）。

### 2.24 `111_` プロンプトの置換（2026-07-30、既存ルールで実データ照合）

プロキシ（`Proxy.Rules.cs`）の置換機能をプロセス内へ移した MOD。ルールファイルの書式は
プロキシと同じなので、プロキシ用に書いたものは MOD のフォルダへコピーすれば動く。

置き場所は `mods/111_llm_prompt_replace/` の中だけ。`llm_replacements.txt` があれば
それを、無ければ同梱の `llm_replacements.default.txt` を読む。名前を分けているのは、
MOD の更新で利用者のルールが消えないようにするため。更新は上書きマージなので、
配布物が持たない名前のファイルは残る（`make_dist.bat` は `llm_replacements.txt` を
除外する）。

最初は `settings\` と既存プロキシの置き場所も見ていた（目印 `llm_proxy_dir.txt` と
上位5階層の探索）。これは、MOD 単体の部品は MOD のフォルダで完結させる方針に
合わせて廃止した（TECH.md §3.1.1）。実際に踏んだ問題が2つあって、どちらも「外を見る」ことが原因だった:

- 古いプロキシのファイル（22行）が同梱ルール（29行）より優先され、新しいルールが
  黙って使われない状態になっていた（どのファイルを読んだかはログに出るが、
  それを見るまで気付けない）
- 場所が分からないとリクエストごとに数十回 `stat` を叩くので、間引きの仕組みが要った。
  固定にした結果、その仕組みごと消えた

設定も `LOG_REPLACE` / `LOG_RULES` の2つだけになった（場所と流用の切り替えは廃止）。

移植で効いたのは「プロキシは JSON にした後のボディを見ていた」という差:

| プロキシが見ていたもの | プロセス内で見えるもの | 対応 |
|---|---|---|
| `\n`（2文字） | 本物の改行 | 置換後は必ず復号する。置換前は素の形と復号形の両方を登録 |
| `あ`（`ensure_ascii=True`） | `あ` | 同上（プロキシは逆向きにエスケープ版を足していた） |
| .NET の `$1` / `${名}` / `$&` / `$$` | Python の後方参照 | 読み替える。存在しない番号は警告して文字列扱い |
| 1 秒の正規表現タイムアウト | Python の `re` に無い | 照合に1秒以上かかったルールを以後捨てる（1回目は止められない） |

実データでの照合（`output_data/` の 13,461 ファイル・51,897 メッセージに、同梱の
`llm_replacements.txt`（29 行 / 27 グループ）をそのまま当てた）:

| 項目 | 結果 |
|---|---|
| 読み込み時の警告 | 0 件（`#memo:` 行を含む新しい書式のファイルでも 0 件） |
| 発火したグループ | 26 / 27（残る1件は `\n` を含むルールの素の形。復号形の方が当たっている＝復号が無ければ死んでいたルール） |
| 置換が起きたメッセージ | 16,636 件（置換は縮める処理ではないので、文字数はわずかに増える） |
| 置換後に `105_` がスキーマを圧縮できたか | 8,859 / 8,859 件（素の状態と同数。`True=>true` などがスキーマの repr を触っても、あちらのパーサは両表記のスーパーセットなので読める） |

同じ照合を古い 22 パターンの版（プロキシ側に置いてあるファイル）でも通してある
（21/22 発火・`105_` は 8,855/8,855 件）。書式が増えても読み込みが壊れないことの
確認になるので、ルールファイルを差し替えたらこの照合を通し直すこと。

最後の行が適用順の根拠。`105_` より外側（`after`）に置いて圧縮前の本文を見せ、
`305_` より内側（`before`）に置いてあちらの完全一致の前提を触らせない。

未確認は、実機で1回も通していないこと。確かめ方は `out\prompt_bloat.log` に
`[RULES] 読込`（起動時）と `[REPLACE] chat`（会話1回で出るはず）が出ること。
`[SKIP]` が出るのは確率付きルールを書いたときだけ。

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
付いてこない。相手をクラス名で決めつけずに直す ― 今その枠と同じ場所に同じ大きさで
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
見えていた。触る軸は伸ばす軸だけにする ― 横に広げないなら `size_hint_y` だけ
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

オフライン検証 24件全通（`tools/test_ui_input_focus.py`）。実機では未確認で、
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

`GridLayout` だと分かった時点で直し方は1つに決まる ― `cols` を増やす。位置も
行の大きさも変えずに高さが 1/N になり、幅にも余裕がある。

#### 実機で2回外している（記録）

1回目 ― 画面を崩した。 最初の版は「縦に積まれた文字のあるウィジェット」を一覧とみなし、寄せる → 行を
詰める → `ScrollView` に入れる、の3段で収めようとした。実機での結果:

* アイテム説明の吹き出しを一覧と誤認し、4行の箱を次々と `ScrollView` に入れた
  （`scrolling: 4 row(s) in a 474px frame` が何度も出ている）
* 一覧そのものも、まだ組み上がる前の `(0, 0)` を「触る前の位置」として控え、
  そこへ戻したうえで窓の内側へ寄せた ＝ 一覧が画面の下端いっぱいまで動いた
* `spacing` を持たない相手では、行の位置から間隔を逆算する経路に落ちる。その
  経路では「レイアウトが走ったか」の判定が必ず真になる（自分で作った値と
  突き合わせているため）。ここが素通りの穴だった

利用者の指摘（初期位置のまま、列を2列に）が、そのまま正しい直し方だった。

2回目 ― 一覧を1つも掴めなかった（`nothing to fit after the icon press` だけが
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

3回目 ― `cols=2` は当たったのに見た目が変わらなかった（ログに `-> cols=2` が
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

オフライン検証 50件全通（`tools/test_ui_item_list_fit.py`）。偽の一覧は実測値
（`GridLayout` 相当・幅 926.64・行 175x57・窓 1876x1000）に合わせてあり、
吹き出し（列を持てない・ボタンの中・中身が空）を触らないことも検査に入れてある。

#### 実機で成立（2026-08-02）

20件が2列で表示され、画面内に収まることを利用者が確認。決定打はログの
`rows_prop=20` ― ゲームは `rows`（件数）を入れていた。`cols` だけ変えても格子は
`cols x rows` のまま噛み合わず、`rows` を外して箱の高さを中身ぶんにして初めて通った。
並び順は「左の列に先頭から、下から上へ。埋まったら右の列の下端から」で、
1列だったときの並びをそのまま保つ ― これは `place()`（こちらで行を置く経路）の形。

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

1回目 ― 定数を引く先を間違えた。 `_DSL_SPEC` と `_EXAMPLE_PROGRAM` を実行側
（`scripts.free_facility`）から引いていたが、実際は生成側
（`llm_manager_free_facility`）にあった。DSL の説明文と実例は「LLM にプログラムを
書かせる」ためのものなので、実行側に無いのが道理だった。リコンのモジュール名を
最後まで読んでいれば防げた。

2回目 ― `on_ready` で世界を触った。 世界の調査と NPC の census を
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

3回目 ― 注入し直しでは印が落ちないことを忘れた。 1回目・2回目を直した後、
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

#### ついでに塞いだ「残骸」の残り（`305_` / `307_` / `309_`）

`prune_stale` が入っていたのは `301_` / `302_` だけで、自前ボタンを挿す残り3本は
重複判定が印だけだった。

| mod | 挿す場所 | 露出 |
|---|---|---|
| `309_` | `InstantaleApp.refresh_choice_buttons`（ゲームが組んだ一覧を塗り直すだけ） | `301_` と同じ。役場でセーブ → タイトル → ロードで二重化する |
| `305_` | `DisplayQuestChoice.update_button_display` の後 | 掲示板が一覧を組み直すビルドならゲーム自身が消している。保険 |
| `307_` | `AreaMoveCofirmation.update_button_display` の後 | 同上。保険 |

`305_` / `307_` は実機で症状を観測したものではない（組み直さないビルドがあっても
壊れないように、というだけ）。コード側のコメントにもそう書いてある。

#### 検証

オフラインのみ。実機未確認だが、症状は画面を見ればすぐ分かる（役場の確認画面に
キャンセルが出るか）。

| 追加した検査 | どこ |
|---|---|
| 他の MOD の印が付いたボタンを落とさない／`marked_by_a_mod` の真偽 | `tools/test_quest_offer.py` |
| `302_` が `309_` の確認画面から1枚も消さない／汎用語を掃除に使っていない | `tools/test_party_leave.py` |
| 復元された残骸を差し直す（二重にならない・残る1枚は印を持つ）／他 MOD・ゲームのボタンを消さない | `tools/test_office_pardon.py` |
| 残骸を掴む・掃除が仕掛けてある・他 MOD のボタンを落とさない | `tools/test_mini_quest.py` / `tools/test_area_move_dungeon.py` |

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
| 他の MOD のウィジェットを置き場所にしない／その中に入り込まない | `tools/test_ui_text_expand.py` / `tools/test_ui_party_expand.py` |
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
| 残骸の掃除（`prune_stale`）を持つ MOD の文言の重複 | `301_` / `302_` / `305_` / `307_` / `309_` の `OUR_LABELS` に汎用語・他 MOD との重複は無し |
| 自前ボタンを挿すのに `prune_stale` を通していない MOD | 無し（挿す6本すべてが通している） |
| 名前・ID がそのままファイルパスになる箇所 | MOD が `out/` へ書くファイル名は全て定数。唯一の可変（`311_` の世界名）は `safe_world_filename` を通している |
| LLM 経路のフック地点 | `102_` は `_apply_chat_template` 1点だが、そこは実機で発火が確認済み（§2.3 の DEDUP 行）。`105_` は `chat` + `payload`、`111_` は3点すべて。どれも二重に効いても結果が変わらない書き方 |
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

#### ただしワールドによって 1.6 倍遅い ― こちらは MOD が原因だった

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
（45.7ms と 66ms）も消えている ― テクスチャは `[1340, 3918]` と、遅かったときより
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

## 3. 未確認項目と確認手順

優先順。どれも「プレイしていれば片付く」ものなので、実機で踏んだらログを見ること。

### 3.1 戦闘まわり（`106_` / `107_`）: 見張り方と、残った未確認

`106_` は決着済み（§2.5）。`107_` も戦闘終了時の発火を実機で確認して決着
（2026-07-28・§2.12）。残るのはロード時の発火（残骸入りのセーブを読んだときだけ
出る）と、sweep 後に mixer が 1 本へ戻ることの確認の2点だけ。
どちらも `out/battle_bgm.log` を見る。

合格条件は `mixer = n/8 channel(s) busy` が1本を超えて増えていかないこと。
BGM が正常に聞こえること自体は合格条件にならない（一度これで誤判定した。§2.5）。

戦闘を1回して、次の3行が揃えば `107_` も片付く:

| ログ | 意味 |
|---|---|
| `[FLAGFIX] BattleEndInFreeAction.end_phase: cleared in_battle` | フラグ側が効いた（2026-07-28 に確認済み・§2.12） |
| `[FLAGFIX] load_game_new: cleared in_battle` | 既に残骸入りで保存されたセーブを読んだときに出る |
| `[BGMFIX] sweep after BattleEndInFreeAction.end_phase: handed <曲> back to the app` | 曲の引き取り成功（確認済み） |

その他の行の読み方:

| ログ | 意味 |
|---|---|
| `orphan: <曲> was attached to BattleEndInFreeAction instead of the app` | ゲーム側のバグが発火した（毎回出る。正常） |
| `battle track outside a battle: ...` | 戦闘が走っていないのに戦闘曲が鳴った ＝ ロード時の枝。直後に `sweep` が続けば正常 |
| `sweep after ...: stopped battle ...; restarted ...` | 引き取れず鳴らし直した経路。正常だが曲は頭から |
| `sweep after ...` が1行も出ないまま `mixer` が増える | 後始末が走っていない。起点の条件を疑う（前回は `in_battle` の居残り。GAME.md §2.10） |
| `orphan: ... instead of the app` が別の型名で出る | 未知の経路。その型が新しい修正対象 |
| `[FLAGFIX] ...: in_boss_battle still set -- not touching` | 観測できていなかった組み合わせが出た。`107_` の対象を広げる材料 |

`107_` が効いていれば、施設到着イベント（`300_`）も戦闘後に復活する
（`player_events.log` に `skip: ... busy ['in_battle']` が出なくなる）。こちらも
併せて見ると、フラグが本当に下りているかの裏が取れる。

### 3.2 会話からの依頼受注（`301_`）: 実機確認（3段階）

> 2026-07-28 に3段階とも通った（§2.13）。最大の未確認点だった HUD の塗り替えも
> 実機で確認済み。以下は手順として残す（回帰を見るときに使う）。
> 未実測のまま残っているのは末尾の「ついでに片付くもの」のうち選択肢のページ送りと
> `generate_random_quest()` の副作用の2点。

先に `python tools/test_quest_offer.py`（49件）を通しておくこと。実機で見るのは
「オフラインでは確かめられないもの」だけになった: HUD が本物でも塗り替わるか・掲示板の
絞り込みが実データで妥当か・生成された依頼が会話の内容になっているか。

1. ボタンが出るか: NPC と会話する。「会話を終了する」の手前に「依頼を受ける
   （話を切り上げる）」が並んでいれば設置成功
   （`quest_offer.log` に `added '依頼を受ける' to the conversation menu`）。
   「行動」メニューを探す必要は無い
2. 既存依頼の受注: 「依頼を受ける」→ 一覧に `【難易度】タイトル` が並ぶ。現在地が
   エリア7 なら 3 件（難易度 39/43/45）が正解。選ぶとゲーム本来の受注画面に入る。
   `WARN difficulty mismatch` が出たら絞り込みの前提（`neighboring_settlement_id`）が
   崩れている。既定（`FILTER_BY_NPC = True`）では初対面の NPC の一覧は「この話から
   依頼を作る」だけになる（§2.10）。全件見たいときは `False` に
3. 会話からの生成: 先に NPC と少し会話してから「依頼を受ける」を押すと、一覧の
   先頭に「この話から依頼を作る（NPC名）」が出る。押すと LLM が1回走る（30〜60秒）。
   `remembered talk with ...` → `inject: area_description N -> M chars` →
   `generate: -> quest '24' '...'` → `acceptance: process_choice(...)` の順に出れば通っている。
   生成された依頼が会話で頼まれた内容になっているかを目で見ること。なっていなければ
   差し込み文（`addition`）を強める。会話をしていなければこの項目は出ない（仕様）

画面が実際に塗り替わるかが最大の未確認点。`quest_offer.log` に
`quest board: to_display_buttons [...] -> [...] via display_button_load+hud.update_button_texts`
が出た上で画面が変わるかを見る。`via (nothing)` や `hud not found` なら HUD の構成が
変わった合図。

ついでに片付くもの:

- NPC と会話する → `quest_flow.log` に
  `set_top_info_layout_conversation_button_callback:` と `hud top info texts -> [...]` が出て、
  「行動」への切り替えが画面のどのボタンかが確定する
- 依頼掲示板を1回開く → `206_` が `DisplayQuestChoice` → `QuestChoiceManager` →
  `QuestStartManager` の本来の経路を丸ごと記録する。自前の経路との答え合わせに使える
- 選択肢のページ送り。実測できたのは1ページに収まる場合だけで、そこでは
  `display_button_map` が恒等写像だったため「表示位置」と「buttons の添字」を区別できて
  いない（TECH.md §8）
- `generate_random_quest()` を掲示板の外から呼んで副作用が無いか

#### 3.2.1 引継ぎ: `tools/test_quest_offer.py` が1件赤（2026-07-27）

`302_` のセッションで見つけた。修正は `301_` 側で行う。リポジトリは見つけた状態の
まま戻してある（この件の差分は入っていない）。

```
python tools/test_quest_offer.py
  FAIL 閉じた後で掲示板が開く            (app.opened_board == 0)
1 件失敗
```

この赤は `302_` の作業より前から出ている。`302_` 側の変更（`original_party` の
ガード修正）とは無関係。

##### 決着（2026-07-28）: 製品のバグではなく、テストハーネスの人工物

実機で2回計測した。`out/quest_offer.log`。

| | 1回目 | 2回目 |
|---|---|---|
| `end conversation: closed; continuing` → `open board:` | 0.606秒 | 0.601秒 |
| ボタン押下 → 掲示板 | 2.71秒 | 2.69秒 |
| `still busy ... after 30s; going ahead` | 出ていない | 出ていない |

`out/*.log` 全体を検索して `still busy` は一度も出ていない。`IDLE_TIMEOUT`
（[ui.py:67](runtime/instantale_modloader/ui.py#L67) の 30.0）には実機では到達して
いない。下の「疑っていた筋」（自分で立てた合図で自分を待たせている）は否定された。

30秒級の待ちは生成側に実在した（`generate: took 37.5s` / `took 140.1s`）。
次項の筋が正しかった。

したがってオフラインの赤は、偽 Clock が実時間を進めないため `when_idle` が
進行しないというハーネス側の限界であって、`301_` にも `ui` にも直すべきものは無い。
直すならテストハーネス側（偽 Clock に `when_idle` を駆動させる）。

> 前セッションの `ignore=` 案が他2件を壊したのは、存在しないバグを直そうとしていた
> ため。戻したのは正解だった。

##### 疑っていた筋（上記のとおり 2026-07-28 に否定）

`open_quest_board` は会話中なら `show_busy(app)` を呼んでから会話を閉じる。
`show_busy` は待機表示のために `app.is_button_enabled = False` を自分で落とす。
その後 `ui.Screen.end_conversation` → `when_idle` が「手が空くのを待つ」が、
`ui.busy_signals` はまさにその `is_button_enabled` を「塞がっている」と数える。
待機表示を解く `clear_busy` は `open_quest_board` の中、つまり待っている当の
follow_up の中にしか無い。

    show_busy: is_button_enabled = False
      -> end_conversation -> when_idle: busy=['is_button_enabled=False'] -> 待ち続ける
         -> proceed_on_timeout=True なので IDLE_TIMEOUT(30秒) 後にようやく follow_up
            -> open_quest_board -> clear_busy

つまり自分で立てた合図で自分を待たせている。オフラインの偽 Clock は実時間を
進めないので 30 秒に到達せず、掲示板が開かないまま検査に落ちる。これが
テストが赤い理由の説明になる。

##### 「30秒はクエスト生成の待ちでは？」（2026-07-28 に的中を確認）

この筋のとおりだった。実機で待つのは生成経路だけで、受注経路は 0.6 秒で通る。
以下は切り分け前の記述だが、経路の対比はそのまま有効なので残す。紛らわしいのは、
同じ画面で LLM を回す経路が別にあること:

| 経路 | LLM | 想定される待ち |
|---|---|---|
| 「この話から依頼を作る」 | 回す（`random_quest_generator`） | 30〜60秒。これは正常 |
| 「依頼を受ける（話を切り上げる）」 | 回さない（会話終了の要約は別途走りうる） | 本来は待たないはず |

切り分けは `out/quest_offer.log` の時刻で付く:

1. `end conversation: closed; continuing` の時刻と、その後の
   `open board: process_choice(DisplayQuestChoice, ...)` の時刻の差を見る
2. 差がほぼ 30 秒ちょうどなら `IDLE_TIMEOUT` ＝ 自分待ち。加えて
   `end conversation: still busy ['is_button_enabled=False'] after 30s; going ahead`
   の行が出るはず（`when_idle` が時間切れで進むときに書く）。この行が出れば確定
3. 差がばらつく / 30秒未満なら自分待ちではない。生成経路と取り違えている

##### 試したこと（採用しなかった）

`ui.busy_signals` / `when_idle` / `end_conversation` に `ignore=` を足し、
`301_` が待機表示を出している間だけ `ignore=("is_button_enabled",)` を渡す形を試した。

- 「閉じた後で掲示板が開く」は通るようになった（＝上の筋の裏付けにはなる）
- ただし同じスイートの別2件が落ちた:
  `「この話から依頼を作る」が先頭に出る` / `依頼人の名前が文言に入る`。
  掲示板は開き `app.buttons` は `['戻る']`（依頼の間引きは正しい）。生成ボタンだけが
  出ない。会話が実際に閉じた後なので `current_talk(app)` が空を返している疑い
  （`in_conversation` が落ちた後は `last_talk` 頼み。`remember_talk` は
  `state["npc_id"]` が無いと何も控えない）

1件直して2件壊す状態だったので全部戻した。`301_` の流れを把握している側で
やり直すのが早い。`when_idle` が2箇所あることに注意（会話が既に閉じている早期
リターンと、`wait_for_end` の中）。片方だけ直しても効かない。

##### 直すときの選択肢（2026-07-28 の切り分けで対象が変わった）

上の3案はいずれも製品側を直すもので、もう当てはまらない（直す対象が無い）。
記録として残すが、採ってはいけない。

直すのはテストハーネス側。偽 Clock が実時間を進めないため `when_idle` の
ポーリングが進行せず、掲示板が開かないまま検査に落ちている。偽 Clock に
`when_idle` を駆動させる（経過時間を進める／保留中のコールバックを消化する）のが筋。

製品側に手を入れないこと。実機では 0.6 秒で通っており、`ignore=` を入れると
前セッションで実証されたとおり別の2件が壊れる。

### 3.3 パーティ関係（`302_` の残り / `303_` / `304_`）

`302_` の残りは、土地を跨いで別れた場合（いまの町のギルドへ置く経路）が未実測。
`leave: ... [guild of the current area (left home behind)]` が出るのを見る。

決着（2026-07-27）: 「選択肢が出なくなった」は `original_party` の読み違いだった。
症状は「パーティの NPC と別れる選択肢が出ない」。`301_` との競合を疑ったが、
競合ではなく `302_` 単独の誤りだった。ログの1行が答え:

```
screen: partner='8' member=True ... party=['player', '8']
not offering the farewell to 'テスト仲間A': original_party is set
```

セーブを見ると `party` と `original_party` が同じ内容で入っていた
（`current_quest_data` は `None`、クエスト中ですらない）:

```
party            ['player', '8']
original_party   ['player', '8']
```

`original_party` は差し替えの控えであって「差し替え中の印」ではない。
「入っていたら断る」にしていたので、仲間が居ると毎回断っていた。

その直しも外した（2026-07-28、同じ症状で再発）。「控えと名簿が食い違えば
差し替え中」に変えたところ、今度は雇用直後に消えた:

```
not offering the farewell to 'テスト仲間B': party is swapped (original_party=['player'] != party=['18','player'])
```

`original_party` は雇用に追随せず古いままなので、仲間を入れれば当然食い違う。
差し替えではなかった。

結論として `original_party` は判定に使わない。同じフィールドの意味を2度続けて
外して2度ボタンを消しており、3度目を試す根拠が無い。守りたかった「パーティが
一時的に差し替えられている最中」はクエスト中の話で、そこは
`current_quest_data`（クエスト外では `None`。実セーブで確認済み）で既に断っている。
値は `screen:` の行に `original_party=[...]` として記録だけ続ける。本当に
差し替えが起きる場面が来れば、そこに現れる。

教訓が2つある。フラグ名が意味するとおりに動くとは限らないこと（`in_shopping` と
同じ形。GAME.md §2.6）と、意味を確かめていないフィールドで機能を止めないこと。
止める判断に使ってよいのは意味の裏が取れた信号だけで、確かめていないものは「記録」に回す。

> `303_` と `304_` は同じ場面に手を入れる。既定では `304_`（解散しない）が勝つので、
> `303_` の手順をそのまま踏んでも `303_` の行は出ない（それが正しい）。`303_` を
> 確かめたいときは先に `runtime/mods/304_quest_end_keep_party.py` の頭に `_` を付けて
> 注入し直すこと。TECH.md §3.2 / §3.3。

`303_` は全体が未確認。仲間を連れてクエストに行く必要があり、かつ差し替えが目に
見えるのは雇った町とは別の町でクエストを終えたとき（同じ町なら元から同じ場所に
置かれる。§2.9 の実測がまさにそれ）。先に
`python tools/test_quest_end_guild.py`（45件）を通しておくこと。

1. A の町で NPC を雇う → B の町へ移動 → B のクエストを受けてクリアする
2. `party_leave.log` に `quest-end: '<名前>' (<id>) left the party in <B> via
   QuestEndManager.method_1 -> '<B のギルド>'` が出る
3. 続けて `leave facility via ...`（第1層が効いた）か
   `'<名前>': '<A のギルド>' -> '<B のギルド>'`（第2層が効いた）のどちらかが出る。
   どちらが出たかで「ゲームが `get_party_leave_facility` を使っているか」が確定する
4. 画面に `<名前>は<B のギルド>に留まることになった。` が出る
5. B のギルドにその NPC が居ること（再雇用できること）
6. `nobody moved ... placing them by hand` が出たら第3層まで落ちている＝置き直しの経路が
   こちらの前提と違う。その行が出た状況を残すこと

`304_` も全体が未確認。こちらは同じ町でクエストを終えても目に見える（そもそも
外れない）ので、`303_` より確かめやすい。先に `python tools/test_quest_end_keep.py`
（50件）を通しておくこと。

1. NPC を雇う → クエストを受ける → クリアして「帰還する」
2. 画面に `<名前>はパーティに残り、引き続き行動を共にすることになった。` が出る。
   「…はパーティから離脱した。」が出たら差し替えが効いていない
3. `party_leave.log` に
   `quest-end keep: '<名前>' (<id>) stays in the party — QuestEndManager.method_1
   did not disband the party` が出る
4. HUD のパーティ欄にその NPC が残っていること・そのまま次のクエストに同行すること
5. ギルドや宿にその NPC が立っていないこと（居たら置き直しを取りこぼしている。
   `not placing ... anywhere` が出ているかを見る）
6. その後 `302_` の「ここで別れる」で普通に外せること・外した先にちゃんと居ること
   （`is leaving for real` が出て控えが落ちる経路。ここが壊れると NPC が世界から消える）
7. `WARN ... is not in __main__` や `WARN no code object resolved` が出ていたら、
   そのビルドでは解散を捕まえられていない（`303_` の挙動に戻る）

`302_` の実機確認手順（再確認するとき。仲間が居ないと何も起きない。現行3セーブは
全て `party = ['player']` なので、まず NPC を雇う）:

1. 仲間と会話する → 「会話を終了する」の手前に「ここで別れる」が出る
   （`added 'ここで別れる' to the conversation with ...`）
2. 押す → 「ああ、ここで別れよう」「やめておく」の2択になる。やめておくと元のボタンに
   戻る（ここまでで何も起きていないこと）
3. 決定 → 会話が閉じ、`leave: party before = [...]` → `leave: party after = [...]` →
   `leave: moved '63' to ...` → `leave: saved` の順に出る。HUD のパーティ欄から消え、
   別れた施設にその NPC が居ること
4. `WARN remove_party_member left ...` が出たら名簿の入れ物の前提が崩れている。
   ログの `party after` を見る

ついでに片付くものとして、`302_` は自前の解散以外の `remove_party_member` 呼び出しを
`remove_party_member('63') from <関数名> (<ファイル:行>)` の形で記録する。死別が起きれば
その経路も確定する。`add_party_member` / `process_party_member_choice` も1行ずつ記録するので、
雇用と「仲間に話しかける」の経路も同時に分かる。

### 3.4 BGM 偏り是正（`104_`）: ゲーム内動作

`104_` を注入した後、クエスト受注などで新しいエリアを生成する。`out/bgm.log` に
`[BALANCE] <フック名> area N: ... -> ...` が出ることと、どのフックが発火したかを見る。
3つのうちどれで `bgm` が確定するかはコンパイル済みのため分かっていない（GAME.md §2.11）。

既存3世界の是正は `python tools/rebalance_saved_bgm.py`（dry-run）で差分を見て、納得したら
`--apply`（バックアップ自動作成）。未実行。

### 3.5 遅延 import の当て直し: 実機での流れ

`watch.bat` を立てた状態でゲームを起動し、会話などで LLM を1回動かしてから
`out/modloader.log` を見る。次の流れが出れば正常:

```
defer wrap llama_cpp_runtime_completion:LlamaCppClient.chat (... is not imported yet)
deferred: waiting for llama_cpp_runtime_completion, scripts.llm.llm_manager (checking every 5s)
deferred: llama_cpp_runtime_completion imported; re-applying mods
boot complete: 27/27 mod(s) applied
```

### 3.6 その後の予定（優先順）

1. `tools/test_quest_offer.py` の赤1件（§3.2.1）。切り分け済み。製品側では
   なくテストハーネスの偽 Clock を直す。製品側に手を入れないこと
2. 戦闘なしミニクエスト（`305_`）の実機確認（§3.10）。新規投入。掲示板から
   1件作って受注するだけで、3段階すべてが `out/mini_quest.log` で判定できる
3. 上の §3.3（プレイしていれば片付く）。§3.1・§3.2 は決着済みで、以後は
   `mixer = n/8` を時々見るのと、ロード時の `[FLAGFIX]` を待つだけでよい
4. `facility_move_to` / 空 `Literal[]`。計測は仕掛け済みで、発生すれば自動で原因が
   確定する。プレイを続けるだけでよい。空 `Literal[]` は敵候補0件のエンカウントが
   第一容疑なので、エンカウント系の分岐を意識的に踏むと再現に近づく可能性がある
5. 戦闘のダメージ表示（`308_`）の残り2点（§3.14）。コロシアムと、味方が
   倒れた／逃げた場合。どちらも遊んでいれば踏むので、`out/battle_damage.log` に
   `battle start:` と `ally … (left the field)` が出るかを見るだけでよい
6. 多重起動抑止 / `--parallel 1`（後日対応。GAME.md §2.12）
7. ネイティブクラッシュダンプ 7件（未着手領域。TECH.md §8）

### 3.7 名前の消毒（`110_`）: 以後の見張り方

§2.15 で決着済み。以後は `out/character_name.log` を時々見るだけでよい。

* `-- not touching` が出たら予約デバイス名か、消毒すると空になる名前に当たった
  （未観測の種類。直すには名前を発明することになるので記録だけしてある）。
  その名前を控えて設計を決めること
* `(same string also in: ...)` が出たら、名前と同じ文字列を持つ別の属性が居る。
  こちらは書き換えていないので、そこが表示や突き合わせに使われていないかを見る
* 世界名は対象外（`worlds\<世界>\` も同じ壊れ方をしうるが、世界名の入口は未調査）。
  起きれば `001_` が `WinError 123` として同じ形で捕まえる

### 3.8 売買画面の救済経路（`108_`）: まだ一度も通っていない

修正は入っているが、はみ出しが再現していないので救済側のコードは実地では未実行
（`out/inventory.log` の 104 件はすべて正常サンプル）。次に売買画面で落ちたとき、または
`inventory.log` に `ok` 以外の行が出たときが確認の機会:

| ログ | 意味 |
|---|---|
| `ok ...` だけが並ぶ | 復元位置がそのまま使えている（正常。寸法の基準として使う） |
| はみ出しを捕まえた行 | `find_placement_position` → `place_new_item` へ流した。アイテムが別のマスに置かれるので、見た目が変わっても正常 |
| 落ちる | 救済が効いていない。`is_valid_placement` の判定と実際の `slots` 長を突き合わせる |

そもそもなぜ復元位置がはみ出すのかは未解明のまま（グリッドの列数が画面ごとに違うのか、
ピクセル→マスの変換が別スケールなのか）。`inventory.log` に所持品側と売買側の両方の寸法が
出るようにしてあるので、再発時にそこから詰められる。

### 3.9 アイテム説明の横幅（`109_`）

高さの拡張は実機で確認済み（§2.17）だが、横の拡張は未観測。長い説明のアイテムに
マウスを合わせたとき、箱が横にも広がるかを見る。広がらなければ縦横比の判定
（`RATIO_SLACK`）か窓幅の取得を疑う。ログの書式に幅が入っていないので、確かめるなら
先に `item_detail_autosize.log` の出力へ幅を足すこと。

### 3.10 戦闘なしミニクエスト（`305_`）: 実機確認

> **2026-08-06、デバッグ用に降格（ユーザー判断）。** 品質が目標に届かないため、
> 既定では読み込まれない。**この節は「再開するときの続きの手順」として残す。**
> 確かめるにはデバッグモードを入れること（README「デバッグモード」）。
>
> 止まっている場所は1点だけ ― **達成として帰還できるか**。最後に入れた
> 「`quest_referee*` の戻り値で `retire_from_the_quest` を `return_after_completion`
> に差し替える」修正は**まだ一度も実機で走っていない**（§2.22）。
> 次に試すなら、注入し直してから下の 4 を1回踏むだけで結論が出る。

先に `python tools/test_mini_quest.py`（111件）を通しておくこと。設置・押下・生成・
控え・書き換え・素通し・目印切れはそこで済むので、実機で見るのはオフラインでは
確かめられないものだけになる。「LLM が実際に討伐でない依頼を作るか」「進行が戦闘に
ならないか」「達成で帰還できるか」の3点で、判定は全部 `out/mini_quest.log` で付く。

> 4回通した（2026-07-28）。1・2・3 は済みで、残っているのは
> 「達成として帰還できるか」1点だけ。経緯は §2.19〜§2.22。
> 毎回、討伐前提を書いている行が1〜2つずつ新しく見つかった（最終的に8箇所）。
> 4回目でこの1点は文面での説得をやめ、戻り値の差し替えに切り替えた。
>
> 判定の前に注入し直すこと。4回目は 14:28 の注入のまま 14:48 に遊んでいて、
> 直前の修正が1つも載っていなかった（§2.22）。`modloader.log` の最後の
> `boot #N` と `runtime/mods/*.py` の更新時刻を見比べる。

1. ~~ボタンが出るか~~ 済。「やめる」の手前に「軽い頼まれごとを探す」
   （`board: added '軽い頼まれごとを探す' at N`）

2. ~~生成されるか~~ 済。討伐ではなく採集依頼が生成された。押すと LLM が走る
   （実測 350秒。内部で複数回呼ぶことがある）。
   `generate: kind=gather (採集) combat=none` → `inject: area_description N -> M chars`
   → `rewrite: generator ...` → `remembered: quest '44' '…' kind=gather` の順に出れば
   通っている。掲示板が開き直り、作られた依頼が並ぶ。
   生成された依頼文が討伐になっていないかを目で見ること。なっていれば
   `KINDS[*]["brief"]` を強める

3. ~~進行が戦闘にならないか~~ 済（2回目）。イベント5個・`field_event` 5連続・
   battle 1回・`encounter_final_boss` 0回。

4. 達成として帰還できるか（残っている唯一の項目）: 目的を果たしてから
   「帰還する」意思の入力をする。`quest_flow.log` に `QuestEndManager` が出て、
   クエストが成功扱いになれば決着。撤退扱いになったら
   `output_data/` の最新の `quest_referee_with_free_action` を開き、
   `turn_resolution` が `retire_from_the_quest` か `return_after_completion` かを見る。

   `mini_quest.log` に次の2行が出ていれば、MOD 側は仕事をしている:

   ```
   referee(free_action): result shape = ...          ← 戻り値の形（初回のみ）
   referee(free_action): retire -> return_after_completion (quest='...')
   ```

   | 出たもの | 意味 | 次の手 |
   |---|---|---|
   | 上の2行 → クエストが成功で終わる | 決着。ゲームは `return_after_completion` を受ける | §3.10 を閉じる |
   | 上の2行 → それでも失敗扱い | ゲーム側が拒んでいる（`quest_process_phase` がボスの生死を見ている） | `QuestEndManager(app)`（引数ゼロ）を MOD から起こす。このときだけ実装する |
   | `could not write type on ...` | 戻り値が dict でも属性でもない形 | その行に出た型名を見て読み書きの手を足す |
   | 何も出ないまま撤退した | 控えと `quest_title` が突き合わない | `mini_quests.json` の `title` と、`quest_flow.log` のクエスト名を見比べる |

`quest_flow.log` 側の目安（2回目の実測値を括弧に）:

| 見るもの | 期待 |
|---|---|
| `PhaseSpec('QuestEventManager', ...)` | 出てよい（むしろ本命。2回目は5連続） |
| `PhaseSpec('BattlePhaseManager', ...)` | `COMBAT_MODE="none"` なら出ない（2回目は1回だけ出た） |
| `PhaseSpec('QuestEncounterFinalBoss', ...)` | どちらのモードでも出ない（2回とも0回） |
| `PhaseSpec('QuestEndManager', ...)` | 達成したら出る。これが最後の未確認 |

その後に残るもの（急がない）:

- `nothing_happens` の連続。3ターン以上続いたら、イベントの在庫
  （`- 残りフィールドイベント`）が尽きていないかを `output_data/` の
  `quest_referee_with_free_action` で見る。尽きているなら `EVENT_COUNT` を増やす
- `COMBAT_MODE = "mobs"`（＝ボスなしの雑魚のみ）は既定ではない。`"none"` が
  通ってから切り替えて確かめること
- 敵0件の依頼は作らないという設計判断が正しいままか。`probes.log` に
  `Literal[0` が出たら前提が崩れている（`305_` は敵を減らさないので、出たとしたら
  別経路）

### 2.24 `305_` をデバッグ用に降格（2026-08-06、ユーザー判断）

**品質が目標に届いていないため、既定では読み込まないことにした**（`mod.json` に
`"debug": true`）。コードもオフライン検証も消していないので、デバッグモードを
入れれば一覧の元の位置に戻る。

##### どこまで出来ていて、何が足りないか

| | 実機での結果 |
|---|---|
| 討伐でない依頼が生成される | **成立**（採集依頼が3回とも生成された。例:「『銀光の雫』を5株採取してきてほしい」） |
| 道中が戦闘でなくイベント主体になる | **成立**（イベント5個・`field_event` 5連続・battle 1回・`encounter_final_boss` 0回） |
| 掲示板への設置・待機表示・控え | **成立** |
| **達成として終われるか** | **未達**。4回中2回は目的を果たしたのに「撤退」＝失敗扱い、2回は在庫の敵を倒し切って通常のクエストと同じ終わり方 |

##### 降ろす判断が妥当な理由

`return_after_completion` は**4回とも一度も選ばれていない**。プロンプトの
書き換えは8箇所まで積み上げたが、この1点だけは押し切れなかった
（§2.19〜§2.22）。**文面を足すやり方の限界**が見えた時点で戻り値の差し替えに
切り替えたが、**その修正はまだ実機で走っていない**（§2.22 の注入漏れの回で
足したもの）。

つまり「あと1回の実機で決着するかもしれないが、決着しないかもしれない」状態で、
遊ぶ人に既定で見せる品質ではない。**降ろしたうえで再開の手順を残す**のが正しい。

##### 再開するときの入口

1. デバッグモードを入れて注入し直す
2. §3.10 の手順 4 を1回踏む（目的を果たしてから帰還する）
3. `out/mini_quest.log` の `retire -> return_after_completion` の有無で、
   §3.10 の切り分け表がそのまま使える

### 3.11 アイテム説明欄が閉じた後も残る（新種・2026-07-28、計測を仕掛けた）

スクリーンショットで発見。所持品を閉じて
戦闘に入っているのに、アイテム説明の箱が画面に浮いたままだった（テスト用アイテム
「新しいアイテム」／攻撃力500／説明が `testtest…` のもの）。「表示されることがある」
＝間欠。

##### 分かっていること

- 表示・非表示は `opacity` で行われている。`out/item_detail.log` の写しで
  `ItemDetailBox ... opacity=0`、子の Widget/Label はいずれも `opacity=1.0`。
  つまり箱の `opacity` を 0/1 で切り替えて見せ隠ししている
- したがって症状は「1 に上げた誰かが 0 に戻していない」

##### `109_` は原因ではない（切り分け済み）

`109_fix_item_detail_autosize` が触るのは `size` / `pos` / `size_hint` /
`pos_hint` だけで、`opacity` には一切書き込まない。唯一 `clamp` が箱を窓の
内側へ移動するが、`opacity=0` の箱を動かしても見えるようにはならない。
`109_` を止めても症状は変わらないはず（切り分けたいなら
`_109_fix_item_detail_autosize.py` にリネームして1回再現させる）。

##### 仕掛けた計測（`208_`）

誰が `opacity` を上げ下げしているのかはコンパイル済みで読めないので、
プロパティの変化そのものを見張る（`box.bind(opacity=...)`、読み取り専用）。
`out/item_detail.log` に出る:

```
[時刻] opacity -> 1.0 | box=... pos=... size=... parent=... | from <呼び出し元の連鎖>
```

再現したときの読み方:

| ログの形 | 意味 |
|---|---|
| `-> 1.0` の後に `-> 0` が無いまま所持品を閉じた | 戻す側が呼ばれていない。`from` に出ている「上げた側」の対になる関数を探す |
| `-> 0` は出ているのに見えている | 別の箱が残っている（`box=` の id を突き合わせる）か、親ごと生きている（`parent=`） |
| 何も出ない | 見張りが掛かる前（`update_content` を一度も通っていない箱）。`watch_opacity` の掛け方を変える |

戦闘に入った瞬間が怪しい。画像は戦闘画面で、直前に所持品を開いていた。
再現手順の候補は「所持品を開いてアイテムにマウスを乗せたまま戦闘に入る」。


### 3.12 訓練の経験値を仲間にも（`306_`）: 実機1回目（2026-07-30、写しは成立）

##### 実測（`out/party_train_exp.log`。宿屋で月日を訓練に充てた1回）

```
train exp: VacationTrainManager.execute: 'テスト仲間C' +686852 exp (lvl 52 -> 52, point 0 -> 686852)
train exp: VacationTrainManager.execute done: player lvl/point (60, 732806) -> (60, 1419658); shared 1 gain(s) with 1 companion(s)
```

| 分かったこと | 根拠 |
|---|---|
| 宿屋の訓練は `VacationTrainManager` で確定 | その名前でセッションが記録されている（リコンからの推定が当たった） |
| 経験値は `Character.gain_exp` を通る | 写せている。`WARN ... no Character.gain_exp call was seen` は出ていない |
| 写す量はプレイヤーと同額 | プレイヤー `732806 → 1419658`（＝ +686852）と同じ点数が同行者に入っている |
| 支給は訓練1回につき1本 | `shared 1 gain(s)` |
| 誤爆していない | `(not training)` の行が0本（この回は戦闘なし） |

レベルアップだけは未観測。仲間は `lvl 52 -> 52`（`check_levelup()` が False）。
プレイヤーも `lvl 60` のままなので、この回は単に必要経験値に届いていないだけで、
`levelup()` の経路が壊れているわけではない。次に確かめるなら:

- `ANNOUNCE_GAIN` を ON にして「経験値が入ったこと」を画面で見る（レベルが上がらない
  回でも、効いていることがゲーム内で分かる）
- レベルの低い仲間（雇ったばかりの NPC）を連れて訓練する。低レベルほど必要経験値が
  小さいので、`levelup()` と表示（`…はレベル N になった。`）まで一度で通る

##### この回を受けて直した2点（2026-07-30）

| 直したこと | 理由 |
|---|---|
| `ANNOUNCE_GAIN` を既定 ON に | 高レベルでは1回の訓練でレベルが上がらない。ログを見ないと効いているか分からない状態だった |
| 文言をプレイヤーの獲得経験値の後に出す | `gain_exp` はゲームの2行の間で呼ばれている（上の並び）。その場で出すと `156の経験値を得た。` より先に仲間の話が出る。文言は溜めて、ゲームが次の行を出した後に流す（文面では見分けない）。1行も出さずに終わるビルド用に、セッションの終わりで必ず流す受け皿を置いた |

支給の点数と表示の数字は別物（`686852` を写して、画面には `156` と出た）。
`calculate_current_gained_exp_on_display()` が換算しているので、こちらの文言に
数値は入れない。

##### 残りの確認手順

見るのは3点。

1. 仲間を1人雇ってから宿屋に入り、月日を訓練に充てる
2. プレイヤーが経験値を得た画面で、仲間のレベルアップの行が出るか
   （`ANNOUNCE_GAIN` を ON にすれば、上がらなくても1行出るので確認が楽）
3. `out/party_train_exp.log` を見る

```
VacationTrainManager.execute done: player lvl/point (4, 120) -> (5, 30); shared 1 gain(s) with 1 companion(s)
VacationTrainManager.execute: 'テスト仲間C' +250 exp (lvl 3 -> 4, point 40 -> 90)
```

| ログの形 | 意味 | 次にやること |
|---|---|---|
| `+... exp` の行が出ている | 効いている。支給を写せた | ゲーム内のレベル表示と突き合わせる |
| `shared 0 gain(s)` ＋ `WARN ... no Character.gain_exp call was seen` | 訓練は通ったが、経験値が `gain_exp` を通っていない | その行の `player lvl/point` の変化を添えて報告。`experience_point` を直に書く経路を探す |
| `VacationTrainManager` の行が1つも出ない | 宿屋の訓練が別のマネージャで走っている | `(not training)` の行の `from ...` に出ている呼び出し元を見て、そのクラス名を `INN_TRAINING_MANAGERS` に足す |
| `nobody is travelling with them` | 仲間が同行していない（または名簿の在り処が違う） | 同じ行の末尾に候補の入れ物が全部出るので、それを見る |

未確認のまま残るもの:

- 施設での訓練（`TrainingStartManager` / `TrainingPhaseManager`）が本当に宿屋とは
  別の画面なのか。既定では両方に効かせているので、宿屋以外で意図せず入っていないかを
  ログで見る
- 休養（`SHARE_REST`、既定オフ）で経験値が入るのかどうか自体が未観測
- 仲間のレベルアップで HP・能力値がどう動くか。こちらは `levelup()` を呼ぶだけ
  なのでゲーム任せだが、パーティが強くなりすぎるならバランスは `SHARE_RATIO` で下げる

### 3.13 危険な道を行く（`307_`）: 実機1回目（2026-08-01、通しで成立）

`霧の要塞都市`(8) → `澱みの宿場町`(7) を「危険な道を行く」で移動した1回の記録
（`out/road_travel.log` と `out/events.log`）。生成から到着まで通った。

```
01:26:03  confirm: options [('徒歩(3ヵ月)', ['7', 'on_foot']), ('馬車(1000G)', ['7', 'coach'])]
01:26:04  start: '霧の要塞都市'(8) -> '澱みの宿場町'(7) mode='on_foot' via text
01:26:04  start: levels origin=72 target=39 -> difficulty=59 (mode=between offset=0)
01:26:04  inject: area_description 24 -> 470 chars; difficulty 69 -> 59
01:28:50  start: took 165.9s; new quest ids=['9']
01:28:50  start: quest '9' '灰の街道：霧の要塞から澱みの宿場町への死の行路' difficulty 69 -> 59 (1 store(s))
01:29:13  armed: quest '9' started
01:38:26  cleared: the road to '澱みの宿場町' is open
01:38:40  arrive: process_choice(AreaMoveManager, '徒歩(3ヵ月)') args=['7', 'on_foot']
01:38:40  days: 90 -> 14 (spent 14/14 on the road to '澱みの宿場町')
01:38:45  arrived: '澱みの宿場町' reached; 14 day(s) spent of 14 allowed
```

| 確かめられたこと | 根拠 |
|---|---|
| 確認画面への設置と押下 | `徒歩 / 馬車 / 危険な道を行く / やめる` の並びで表示・押下ともに動作 |
| `mode` の実値 | `'on_foot'` / `'coach'`（GAME.md §2.18 に記録。mod の `WALK_MODES` にも書き写した） |
| 道中クエストの生成（166秒） | 題名が `灰の街道：霧の要塞から澱みの宿場町への死の行路` ＝ 2つの土地を繋ぐ道として生成されている（`area_description` への差し込みが効いている） |
| 難易度の抽選 | エリアの水準 72 と 39 の間で 59。ゲーム自身の値は 69 |
| 受注・進行・完了 | ゲーム本来のクエストとして最後まで進行（ボス戦→戦利品→帰還） |
| `elapse_days` が日数送りそのもの | 徒歩の移動で `90` が渡ってきて、`14` に切り詰めた結果その日数で移動が完了した。「危険だが早い」が成立 |
| 到着 | `澱みの宿場町` に到着し、控えが外れた（以後の日数送りは素通し） |

##### 直した点1: 移動が遅れて起きる（この回で見つかった不具合）

帰還した後、一度元の街に戻され、出口まで歩いて初めて移動した（01:38:26 完了 →
01:38:37 プレイヤーが出口へ移動 → 01:38:39 発動）。

原因は「集落の画面に戻ったら移動する」の目印。帰還先はエリアの入口で、そこの
選択肢は隣の施設への `MovePhaseManager` だけしか無い。`DisplayTalkChoice` も
`DisplayAreaMoveChoice` も出ないので、プレイヤーが「他の土地へ行く」のある出口まで
歩くまで拾えなかった。

そもそも完了の直後を避けていたのは「帰還後の『漁る』を取り上げないため」だったが、
戦利品は完了より前だった（01:38:05 `LootPhaseManager('漁る')` →
01:38:18 `QuestEndManager('帰還する')`。GAME.md §2.9 に反映）。避ける理由が無い。

→ `QuestEndManager.execute` が返った直後（`when_idle` で報酬テキストの流し込みを
待ってから）その場で移動するようにした。集落の画面を見る経路は保険として残し、
目印に `MovePhaseManager` を足してある（入口でも拾える）。

##### 直した点2: 難易度が片方の格納先にしか書けていない

`difficulty 69 -> 59 (1 store(s))`。生成した直後は `world_dict['quests']` にまだ
その id が現れていない（`world.quests` にだけ在る）。遊ぶときは正しい値だが、
保存側が古い値のままだと再読み込みでずれる。

→ 受注の時点（`QuestStartManager`）でもう一度書くようにした
（`armed: difficulty 59 written to N store(s)`）。同じ値を書くだけなので何度でも安全。

##### 実機2回目（2026-08-01、移動が起きなかった）: 注入し直しで道が切れる

2回目は移動が一度も起きなかった。ログの時刻を突き合わせると原因は1つ。

```
01:47:41  注入（層A）
01:48:25  「危険な道を行く」を押す → 生成が始まる（層Aの中で61秒待つ）
01:49:01  **注入（層B）** ← 生成の最中
01:49:27  層A が生成を終えて控えを書く（ファイルには入った）
01:49:36  受ける → QuestStartManager → **層B は控えを持っていない** → armed にならない
01:50:55  注入（層C）。ここで初めて控えを読む（stage=offered。クエストはもう進行中）
01:54:45  帰還する → armed の控えが無いので何もしない
```

`apply()` は注入のたびに走り、新しい層は新しい `state` を持つ。控えの引き継ぎが
「`apply()` の時点でファイルを1回読む」だけだったので、その後に古い層が書いたものを
見落とした。さらに、道と受注を結び付ける合図が `QuestStartManager.__init__` の一瞬
だけだったので、そこを外すと二度と回復できなかった。

##### 直した点3: 判定の根拠をゲーム自身の状態に移した

| 直したこと | 中身 |
|---|---|
| 控えはファイルが正本 | 読む前に更新時刻を見て、変わっていれば読み直す（`sync_pending`）。古い層が書いたものを新しい層が拾える |
| 受注の検出を合図から状態へ | `app.current_quest_data` の `id` が控えのクエストと一致したら `armed` にする。画面が組み直されるたびに見るので、いつ入ってきても拾える |
| 完了の検出も状態から | `QuestEndManager.execute` を呼ぶ前に `current_quest_data` を読み、終わったのが道中のクエストなら段階を問わず踏破とする |

`QuestStartManager` を包む経路も残してあるが、もう必須ではない
（`206_` と重なっているだけの補助になった）。

##### 実機3回目（2026-08-01、成立）

注入し直しを挟まずに通したところ、帰還の直後にそのまま移動した。§3.13 の
「直した点1〜3」はいずれも効いている。

そのうえで見つかった違和感を1つ直した。移動中にゲームが出す
`徒歩で目指す。長旅だ...`（実測）が、道中をクエストとして踏破した後に出ると
筋が合わない。こちらが起こした移動の最中（`moving`）だけ、名指しした文言を
`InstantaleApp.add_text` で伏せる（`HIDE_TRAVEL_TEXT`、既定 ON）。到着の合図
`辿り着いた。` と待機表示の点はそのまま通す。普通の徒歩・馬車の移動には触らない。

馬車側の文言は未実測。伏せる語に当てだけ入れてあるが、当たらなければ何も起きない。

##### 足した仕様: 体力（スタミナ）が3分の1を切っていたら断る（2026-08-01）

「スタミナ」の実体は `Character.physical_integrity` / `max_physical_integrity`
（GAME.md §2.19）。同じプレイヤーで 100 → 50 → 0 と減り、最大HPが 1560 → 1365 →
1170 と連動して下がるところまで実測した。`current_hp` は戦闘のHPで別物。

`STAMINA_MIN_PERCENT`（既定33）を下回っていたら、ボタンを押した時点で断る。

```
stamina: 32/100 (32%) threshold=33% exhausted=True
refused: not enough stamina （体力 32/100 ― 休むか、医者にかかるかだ）
```

- 生成にも世界のデータにも触らない（LLM を回さない・依頼を作らない・控えも
  作らない）。確認画面はそのまま残るので、徒歩か馬車を選び直せる
- 値が読めなかったときは通す（遊びを止めない）。`WARN stamina: cannot read
  physical_integrity` が出る
- 未確認: `exhausted` が立つ閾値、体力が減る量と回復量、ここを断ったときに
  プレイヤーが取れる回復手段が実際に足りるか（宿・医療施設の効き）

##### 足した仕様: 依頼概要に移動先を明記する（2026-08-01）

生成した道は、受注しなくても普通の依頼として世界に残る（ゲーム自身の
`generate_random_quest` が登録する。MOD は消さない）。掲示板に残ったそれを後から
見たときに移動の依頼だと分かるよう、LLM が書いた `request_summary` の末尾に
1行足す（`NOTE_IN_SUMMARY`、既定 ON）。

```
※このクエストをクリアすると「澱みの宿場町」に移動します。
```

- 本文には手を入れない。足すのは末尾の1行だけ
- 生成の直後は片方の格納先にしか居ないので、受注の時点でもう一度足しに行く
  （難易度と同じ事情。GAME.md §2.9）。目印で見るので二重にならない
- `request_summary` が文字列でない世界では触らずに WARN を残す
- 生成側の戻り値（`QuestStructure`）ではなく保存されたクエストに足している。
  実測で戻り値は `dict` ではなく（`inject: generated ...` の行が1度も出ていない）、
  型が読めないものへ書き戻すより、既に世界に入ったものを直すほうが確実

紐付けが切れたら、この一文も消す（`drop_road`）。切れた道はただの討伐依頼として
終わるので、「移動します」が残っていると嘘になる。

| 紐付けが切れる場面 | 一文 |
|---|---|
| 別の依頼を受けた / 普通に徒歩・馬車で移動した / 放棄した / 道を選び直した | 消す |
| 踏破して着いた（`arrived`） | 残す ― その時点では本当だったから |
| 控えの寿命（7日・実時間）で消えた | 消せない（控えを読む前に落とすので、どの依頼か分からない）。7日のあいだ別の依頼も移動も1度もしなければ、という条件なので実際には起きにくい |

消すのは目印から後ろだけなので、LLM が書いた本文はそのまま残る。

##### ファイルを3つに分けた（2026-08-01、挙動は変えていない）

1205行の1本になっていたので、`from . import` で分けた（TECH.md §3.1.1.1）。

| ファイル | 行 | 中身 |
|---|---|---|
| `area_move_dungeon.py` | 756 | 方針・設定・文言・フックの設置 |
| `journey.py` | 143 | 道中の控え（段階・保存・日数の予算）。ゲームに触らない |
| `world.py` | 191 | ゲームのデータの読み書き。この MOD の方針は持たない |

設定の定数は入口に残してある（ローダは入口モジュールのグローバルへ書き込む）。
オフライン105件は分割の前後で同じものが通っている ＝ 挙動は変えていない。

##### 未確認のまま残るもの

- `ARRIVAL_MODE=carriage` で所持金が足りないときにゲームが何をするか未確認。
  そこで移動が拒まれると、踏破したのに着かない（控えは `MOVE_TIMEOUT` で外れる）
- 日数を切り詰めることで、日数に紐づく処理（NPC の予定・依頼の期限・季節）が
  どう変わるかは未確認
- 放棄（`QuestRetireManager`）した場合は未実施。移動しないことをまだ実機で見ていない
- 生成に 166 秒かかっている。待機表示は出ているが、移動のたびにこれを待つのが
  受け入れられるかは実際に何度か遊んでみないと分からない

### 3.14 戦闘のダメージ表示（`308_`）: 実機で成立（2026-08-01）

1回目でとどめの一撃だけが落ち、直したうえで再確認まで済んでいる（利用者確認。
通常攻撃・スキル・とどめの一撃がいずれも表示された）。以下は1回目の記録と、そこから
分かったこと。

#### 1回目（とどめだけ落ちた）

4戦闘ぶんの記録（`out/battle_damage.log` 全13行）。通常攻撃もスキルも数字が出た
（スキルは利用者が画面で確認）。地の文との前後関係も問題なし。

```
01:50:03  battle start: 3 combatant(s) on the ledger
01:51:06  action by 'エリス' (味方陣営): enemy 泥濘の亡者 hp 804 -> 5
01:51:13  action by '泥濘の亡者1' (敵側): ally エリス hp 2591 -> 2584
01:51:19  action by '泥濘の亡者2' (敵側): ally エリス hp 2584 -> 2583
（ここに居るはずのとどめの一撃が1行も無い）
01:52:05  ledger cleared (a new battle started; had 1 combatant(s))   ← 敵2体が消えている
01:53:47  action by 'エリス' (味方陣営): enemy 霧の主… hp 752 -> 61; enemy 霧の主… hp 912 -> 275
```

| 確かめられたこと | 根拠 |
|---|---|
| 敵は `app.current_enemy_dict` に居る | `battle start: 3 combatant(s)` ＝ 敵2＋プレイヤー |
| HP は `current_hp` で、戦闘中にここが動く | `hp 804 -> 5` などが1手ごとに出た |
| 1手 = `handle_battle_situation` 1回 | 味方の手・敵の手がそれぞれ1行として出た（`character_side` は `'味方陣営'` / `'敵側'` の日本語） |
| 1手で複数の敵に当たる手がある | 最終行。1回の報告に2体ぶん並んでいる（スキルと見られる） |
| 最大 HP は `max_hp` | `no max HP attribute found` が1行も出ていない（残量が分母付きで出た） |

##### 直した点: とどめの一撃だけが1行も出ない

各戦闘の最後のプレイヤーの手に対応する行が無く、次の `ledger cleared` で
`had 1 combatant(s)`（敵が2体とも台帳から消えている）。

原因は、報告のときに「今 `current_enemy_dict` に居る者」だけを見ていたこと。倒した敵は
報告より先にそこから抜けるので、比べる相手が居なくなり、とどめのダメージが丸ごと
落ちていた（`enemy_delete_animation` / `check_character_death` があるので、1手の中で
消していると読める）。「スキルのダメージが出ない」と見えていたのも、その手で敵が
倒れた回だったため（スキル自体は上のとおり出ている）。

→ 台帳に HP だけでなく持ち主そのものを控え、鍵が場から消えた回にその参照から
最後の HP を読んで1行出してから落とすようにした。残量の代わりに `（撃破）` を添える
（`残り HP 0/804` は読み手に何も足さないため）。HP が動かないまま消えた敵（逃走）は
出さない。オフライン検証は 49件 → 55件。

##### 残っている未確認（後日検証）

- 味方が倒れた／逃げた場合。 ログにはまだ味方が場から消えた例が無い。味方は
  `current_enemy_dict` ではなく名簿から引いているので、倒れた仲間が名簿に残るなら
  そのまま出る（HP 0 のまま動かないだけ）。名簿から抜けるなら、敵と同じ「場から
  消えた者」の経路に乗って `（撃破）` が付く ― 味方に `（撃破）` は出したくない
  ので、そうと分かった時点で味方側の文言を分ける
- コロシアム（`BattleEndInColosseum` の経路）。`BattleStartManager.start_battle`
  を通らない戦闘があるかどうかも同時に分かる（通らなければ、その戦闘の1手目は
  `seed` が拾って報告されないまま始まる）

そのとき見るところ:

```powershell
type out\battle_damage.log
```

| 見るもの | 意味 |
|---|---|
| `battle start: N combatant(s)` が出るか | 出なければ `start_battle` を通らない戦闘（コロシアム）。台帳の取り直しを別の入口にも足す |
| `ally … (left the field)` の行 | 味方が名簿から抜けている。文言を分ける必要がある |
| `battle end check:` に HP の変化が並ぶ | その変化を `handle_battle_situation` が拾えていない（＝報告点が足りない） |

### 3.15 役場で手配を解く（`309_`）: 実機で成立（2026-08-01、通しで確認）

手配度を `-10` にしたセーブで役場（`徴税小屋`）に入り、通しで動かした実測
（`out/office_pardon.log` と `out/events.log` の 19:23〜19:25）。

```
19:23:46  process_choice(MovePhaseManager, '徴税小屋')     役場に入る
          choices = ['労働の募集をみる', '市民権の発行', '出る']   ← ゲーム自身の選択肢
19:23:49  offer: '始まりの泥濘' wanted=10 price=10000
          add_text('窓口の帳面に、始まりの泥濘で手配された者として名が載っている。')
19:23:50  process_choice(ConversationStartManager, '役人カイン')   会話を挟む
19:24:08  process_choice(ConversationEndManager, '会話を終了する')
19:24:16  pressed '罰金を納めて手配を解く(10,000G)' (open)   ← 会話の後も残っている
          to_display_buttons [...] -> ['10,000ゴールドを納める', 'やめておく']
19:24:21  paid: price=10000 gold 46483 -> 36483 lawfulness -10 -> 10
          restore: to_display_buttons -> ['労働の募集をみる','市民権の発行','出る','会話する']
19:24:58  （ゲーム自身のセーブ）savedata.json = gold 36483 / lawfulness 10
```

| 確かめたこと | 結果 |
|---|---|
| 手配度が負の状態でボタンが出る | 成立（`wanted=10` → `10,000G`） |
| ゲーム自身の選択肢を消さない | 成立。`労働の募集をみる` / `市民権の発行` / `出る` / `会話する` が全て残った |
| 素のゲームと二重にならない | 役場の `choices` に手配を解く項目は無い（GAME.md §2.20） |
| 会話を挟んでも残る | 成立。`ConversationEndManager` の後に組み直された選択肢にも入っていた |
| 支払いで所持金と手配度が同時に動く | 成立（46483 → 36483 ／ -10 → 10） |
| ゲーム自身のセーブに残る | 成立。支払いの37秒後のセーブに両方そのまま入っていた（この MOD は `save_game` を呼んでいない） |
| 払った後はボタンが消える | 成立 |

残っている未確認:

| 未確認 | なぜ問題になりうるか |
|---|---|
| `Imprisonment*` / `Citizenship*` との関係 | 投獄・市民権のクラス群が `__main__` にあり、役場には `市民権の発行` も並んでいる。手配度と繋がっているかは未確認で、ゲーム側に別の帳尻があると食い違う（ただし今回、手配度を直接書き換えた後に普通に遊んでセーブまで通っている） |
| 手配度が下がる経路と下限 | 減らしているのは LLM 側（`lawfulness_loss`）で、どの行為でいくつ減るかは未特定。実プレイのセーブで `-40` を観測しているので、既定の 1000G/点 だと 40,000G になる場面がある |
| やめておく／所持金不足の経路 | オフライン検証では通っているが、実機では押していない |

追うときに見るもの:

```powershell
type out\office_pardon.log
```

| 見るもの | 意味 |
|---|---|
| `offer:` の行が出るか | 出なければ、役場に立っている・手配されている・施設の選択肢である、のどれかが成立していない |
| `paid: … lawfulness -10 -> 10` | 書き換えが通った |
| `WARN pay:` | 所持金か手配度を書けなかった。そのときは金も取っていない（片方だけ通さない作り） |

### 3.16 店の品揃えの入れ替え（`312_`）: 実機確認の手順（2026-08-06、未実施）

オフラインでは通っている（`tools/test_shop_restock.py` 26件）が、**この MOD が
立っている前提は実機で一度も確かめていない**:

> 主（`Facility.owner`）の持ち物を空にしてから売買を始めると、ゲームは
> 初回と同じ経路（`set_item_from_world_data`）で品揃えを作り直す

本体のソースは読めないので、この前提はログでしか確かめられない。外れた場合に
備えて、空にした後の補充を見て駄目なら控えを戻し、以後は空にしない作りに
してある（最悪でも「1回だけ空の店を見る」で止まる）。

確かめる手順:

```
1. 店で何か売る（品揃えの中に自分が売った品が混ざる）
2. エリア移動などで 30 日以上進める（徒歩の移動は 90 日。`307_` なら 14 日）
3. 同じ店をもう一度開く
4. type out\shop_restock.log
```

| ログの行 | 意味 |
|---|---|
| `first visit: … (baseline only)` | 1回目。基準の日を控えただけ（既定では入れ替えない） |
| `not due: … day=X last=Y` | まだ日数が足りない |
| `cleared: … items=N` | 空にした。この直後の行で結末が分かる |
| `restocked: … items=N` | **前提が成立**。品揃えが入れ替わった |
| `WARN not refilled:` | **前提が外れた**。品物は書き戻され、以後この MOD は空にしない。ここが出たら、`tier:` の行が出ているか（段を1度でも見ているか）を見る ― 出ていれば直呼びの経路で救えるので、`set_item_from_world_data` の呼ばれ方を測り直す価値がある |
| `tier: owner=… tier=…` | ゲームが渡した段。値の意味は未特定（GAME.md §2.13.1） |

残っている未確認:

| 未確認 | なぜ問題になりうるか |
|---|---|
| 品揃えの質が来店ごとに偏らないか | 段（tier）はゲームが決めるので、こちらは値を渡し直しているだけ。入れ替えのたびに同じ段が使われると、進行に対して品揃えが据え置きになる可能性がある |
| 主が店以外に持ち物を使っているか | 持ち物を丸ごと空にする。装備（`equipments`）は別の項目なので触っていないが、`inventory` を戦闘や会話で読む経路があれば影響する |
| `108_` との併用 | `108_` は置き場所がはみ出したときの救済。入れ替え直後は初回と同じ経路で並ぶので、はみ出しは起きにくくなるはず（`out\inventory.log` に `OVERFLOW` が増えないこと） |

## 4. 運用上の取り決め

### プロキシとの併用（2026-07-25）

InstantaleLLMProxy は `schema_compact` を ON のまま動かし続ける。mod が先に圧縮すると
マーカー（`{'$defs':` 等）が消えるので、正常に効いていればプロキシ側は何もしない。裏を返すと:

> `llm_proxy.log` に `[COMPACT]` / `[DEDUP]` / `[EVENTLOG]` が出たら、
> それは mod が取りこぼした経路。

二重に適用しても結果が変わらないので無害で、漏れの検出器として使えるという判断。

多重起動抑止だけは所有者調停が競合する。この段落を書いた時点（2026-07-25）では
未実装だった。main_023 で `LlamaCppSidecar` の所有者調停が入り、ゲーム自身の修正と
プロキシを合わせた三者が同じことを見る状態になっている（GAME.md §1.8 / §2.12）。
併用するならプロキシ側を `singleton_enabled=0` にする。

### 次回起動時の手順（忘れやすい）

```powershell
cd "$env:USERPROFILE\Desktop\InstantaleMods\InstantaleModLoader"
.\tools\watch.bat
```

> 注入はプロセスと一緒に消える。ゲームを起動するたびに注入し直すこと。
> 一度これで 1 セッション分のデータを失っている。
