# `905_fix_world_overview`: 入力した世界の概要をそのまま使う（開発終了・分離した記録）

## 0. 開発終了（2026-08-30）

**この MOD の公開向けの開発は終了した。**
`runtime\mods\` から `discontinued\905_fix_world_overview\` へ移してある（TECH.md §2.6.1）。
検査（`test_wip_world_overview.py`）もこのフォルダへ一緒に移した。

| | |
|---|---|
| なぜ終了したか | **実機で世界を1つも生成しないまま、確かめる機会が来なかった。** 決着に要るのは §3 の手順を1回通すことだけで、コードは書き上がっている |
| どこまで動いていたか | ゲーム抜きの検査33件は全通。経路の読み（GAME.md §2.27）も exe の定数と実セーブ3件で裏が取れている |
| 残っている症状 | 無し。**未確認**であって不具合が出ているわけではない |
| 再開するなら | §3 の確認手順を1回通す。世界を1つ生成して `out\world_overview.log` に `OK the saved world_data['overview']` が出れば決着する |

**2026-09-11 に外部の実機で世界が1つ生成された。**
一段目（差し替え）は成立し、入力した文章が物語・集落・依頼まで届いている。
二段目（読み返し）は仕掛け口を間違えていて動かなかったので、同日に地点を移した。
経過と残りは §3。

---

この MOD は**開発中（9xx）**。git には入れるが、CI・配布物・`load_order.json`・
`docs\` の文書には入らない（TECH.md §2.6）。そのため遊び方も検証の記録も、
この1枚にまとめてある。リリースで正式な番号へ振り直すとき、ここの各節を
元の場所へ戻す。

| ここにある節 | 戻す先 |
|---|---|
| 1. 遊び方・設定・困ったとき | この1枚のまま。`load_order.json` と `BANDS`（`1xx` の `129_` の次）に載せれば `docs\MODS.md` に載る（TECH.md §2.7） |
| 2. 検証の一覧に載せる行 | `docs\VERIFICATION.md` §1「修正（100番台）」の表 |
| 3. 未確認項目と確認手順 | `docs\VERIFICATION.md` §3 の末尾（節番号は振り直す。`§3.31〜3.32` を欠番にしてある） |
| 4. ゲーム構造からの行き先 | `docs\GAME.md` §2.27 の末尾（§2.27 そのものは移していない。ゲームの事実なので置いたまま） |

`tools\tests\test_wip_world_overview.py`（33件）も同じ理由で管理外。

**もとは `130_fix_world_overview`**。実機で世界を1つも生成しないまま v1.7.2 の
リリースを迎えたので、2026-08-21 に 900 番台へ移した。
コードは書き上がっていて、外しているのは実機を1回通すことだけ。
§3 の確認手順を1回通せば決着し、そのまま正式な番号へ戻せる。

---

## 1. 遊び方（`docs\MODS.md` 相当）

### `905_fix_world_overview`: 入力した世界の概要をそのまま使う

世界を生成するときに書いた概要が、そのまま世界の設定にならないのを直す。

素のゲームでは、入力した概要は最初の1回の指示文に渡るだけで、
保存されるのは **LLM がそれを読んで書き直した文章**の方。
以後の生成（物語・噂・ストーリークエスト5本・9エリアの施設と NPC・依頼）は
全部その書き直しを読むので、書いた設定が落ちたり、書いていないものが混ざったりする。
遊んでいる間に出る依頼も同じ文章を受け取るので、ずれは最後まで残る。
経路は GAME.md §2.27。

この MOD は、最初の応答が返った直後に概要を**入力した文章そのもの**へ戻す。
物語の生成より前なので、物語も噂もストーリークエストも書いたとおりの世界から作られる。

戻すのは概要の1項目だけで、地理の説明文と3層9エリアの構造は
LLM が書いたものを使う（「序盤3 → 中盤3 → 終盤3」というゲーム側の決まりから組む部分で、
書いた文章の代わりが無い）。
概要を空のまま生成した場合は何もしない（戻す元が無い）。

記録は `out\world_overview.log`。
差し替えた直後に `world_data.json` が書き出されるので、それを読み返して照合した結果が続けて出る。

```
[...] overview replaced: generated 214 chars -> yours 3182 chars
[...]     yours:     # ヴェスティア世界観 この資料は【A】と【B】の…
[...]     generated: 神が去った後の世界。人々は遺物を巡って…
[...] OK the saved world_data['overview'] is what you wrote (3182 chars)
```

照合は生成の直後の1回だけで、遊んでいる間の保存では走らない。
概要を空のまま生成した世界には `nothing was replaced for this world` が出る。

| 設定 | 意味 |
|---|---|
| 本体が書いた世界観も後ろに残す | 既定は切。入れると入力した文章の後ろに本体の文章が続く。短い種を膨らませてほしい場合はこちら |

#### 困ったとき

| 症状 | やること |
|---|---|
| 概要が入力どおりにならない | `out\world_overview.log` を見る。`overview replaced:` が無ければ、その世界は概要を空のまま生成している |
| `WARN the saved world_data['overview'] is not what was put in` | 差し替えた後に本体が別の文章を入れている。この行を添えて報告してほしい |
| 生成も依頼も重くなった | 長い概要はこの後の指示文すべてに載る（本体の書き直しは短い）。概要を短く書くか、他の LLM 側の負荷を減らす |
| 既にある世界の概要を直したい | この MOD が効くのは生成のときだけ。作り終えた世界は `world_data.json` を編集する |

---

## 2. 検証の一覧に載せる行（`docs\VERIFICATION.md` §1 の表）

| mod | 内容 | 状態 | 根拠 |
| --- | --- | --- | --- |
| `905_fix_world_overview` | 入力した世界の概要が LLM の書き直しに差し替わる | **一段目は実機で成立**（2026-09-11・外部1回。入力した文章が物語・集落・依頼まで届いた）。二段目の読み返しは同日に仕掛け口を移したところで**未実機**。オフライン33件全通・`tools/tests/test_wip_world_overview.py` | GAME.md §2.27 / 本書 §3 |

---

## 3. 未確認項目と確認手順（`docs\VERIFICATION.md` §3 相当）

### 入力した世界の概要（`905_`）: 一段目は成立。二段目の読み返しが残っている

#### 2026-09-11 の実機1回（外部。ローダ v1.10.0 / game main_025・014）

世界を1つ生成したところまで（`ハイグノシア`。街に入る前に終了）。
`out\world_overview.log` に出たのは一段目だけ。

```
[10:00:14.637] overview replaced: generated 166 chars -> yours 395 chars
```

| 決着したこと | 根拠 |
| --- | --- |
| `create_world_overview_from_plot` を通る（概要を入れた場合の分岐の推定が当たっていた） | `overview replaced:` が出た |
| 応答 `World` の `overview` に代入が通る | 代入後の読み直しが一致（`WARN could not replace` が出ていない） |
| 差し替えが**この後の生成すべてに届く** | `out\test` ではなく本番の `output_data\` で、書き手の文の印（`【概要】`）が `create_story` / `create_settlement_detail` / `settlement_quest_generator` / `area_quest_difficulty` に在り、本体が書いた文の印（`信仰の残骸`）は `create_world_overview` の応答にしか無い |

**決着しなかったこと: 二段目の読み返し。**
`OK the saved world_data['overview']` も、その前に出るはずの `generating '<世界名>'` も
出ていない。つまり `save_world_json:generate_new_world` の包みが一度も呼ばれていない。

原因は当てた**地点**。
`generate_new_world` は
`WorldGenerateScreen.__init__(screen_manager, generate_new_world_callback)` へ
**関数のまま渡されて控えられる**（recon の `targets.txt`。§4 に控えた）。
控えを取った後にモジュールの属性を差し替えても、控えの方には届かない。

| 時刻 | 出来事 |
| --- | --- |
| 09:58:46.517 | `defer wrap save_world_json:generate_new_world`（`save_world_json` が未 import） |
| 09:58:52.701 | `app.world_generate_screen` が既に在る（`out\events.log` のスナップショット） |
| 09:58:54.790 | `wrapped save_world_json:generate_new_world [safe]` |

2秒差で負けている。
**速さの問題ではなく、勝ち負けが起動の速さ次第になる地点だったのが問題。**
同日、二段目を `save_world_json:write_obfuscated_json_file` へ移した。
世界の保存がここを通ることは実測済み（VERIFICATION.md §3.4）で、
呼ぶ側が毎回モジュールを引くので当てた時点に関係なく通る。

あわせて変えたもの:

- 概要を空のまま生成した経路の記録を
  `create_world_overview`（引数なし）の包みへ移した。
  `generate_new_world` が持っていた `nothing was replaced for this world` の
  行き場が無くなったため
- 保存先の組み立て（`LOCALAPPDATA\Darmabeko\Instantale\worlds\<名前>`）を捨てた。
  書き出しの実体からは**実際のパスが引数で来る**ので、推測が要らない
- 復号器（`scripts.save_codec`）が引けないときは、書き出す直前の `data` で照合して
  `OK what was handed to the writer` を出す。
  `OK the saved world_data['overview']` は読み返しが通ったときだけの印にした

検査は 24件 → 33件。
増やしたうちの2件は**載せ先そのもの**を縛る（`generate_new_world` へ戻すと検査が落ちる）。
振る舞いだけを見る検査は偽ゲームに直接フックを載せるので、この取りこぼしを見つけられない。

#### 経路の読み（据え置き）

| 分かっていること | 根拠 |
| --- | --- |
| `generate_new_world` が `create_world_overview_from_plot` / `create_world_overview` の2つを持つ | exe の定数表（GAME.md §2.27） |
| 応答 `World` の項目は `world_name` / `overview` / `structure_description` / `structure` | 同上 |
| 保存される `world_data` の5項目とその順 | 同上。実セーブ3件の鍵とも一致 |
| 入力した概要は1回目のプロンプトにしか渡らない | プロンプトの定数（`【予め指定済みの設定】- 世界の概要:`） |
| 保存されるのは書き直しの方 | `テストワールド` の `world_data["overview"]` が1段落の要約文 |

**推定のまま残っているもの:**

- 概要が空のときに `create_world_overview`（引数なし）へ落ちる分岐の条件。
  定数からは分岐が在ることしか読めない。
  HUD 側の `内容ある` / `内容ない` の分岐から採った
- `world_data["name"]` が入力した名前と `World.world_name` のどちらから来ているか。
  実セーブでは同じ文字列になっていて見分けられない（この MOD は触らない）

#### 確認手順（残っているのは二段目だけ。1回でよい）

1. デバッグモードは要らない。注入してタイトルから「世界を生成する」
2. 名前と、**長めの概要**（節や箇条書きのあるもの）を入れて生成する
3. `out\world_overview.log` を読む

```
[...] overview replaced: generated 214 chars -> yours 3182 chars
[...]     yours:     # ヴェスティア世界観 この資料は【A】と【B】の…
[...]     generated: 神が去った後の世界。人々は遺物を巡って…
[...] OK the saved world_data['overview'] is what you wrote (3182 chars)
```

1行目は 2026-09-11 に出ている。**要るのは4行目だけ。**

| 出た行 | 意味 |
| --- | --- |
| `OK the saved world_data['overview']` | 決着。読みは全部当たっている |
| `WARN the saved ... is not what was put in` | 差し替えの後に本体が別の文章を入れている。差し替える場所が違う |
| `OK what was handed to the writer` | 照合は通ったが読み返せていない。`scripts.save_codec` が引けていない |
| `WARN could not read ... back` | 書き出しの実体は通ったのに中身が読めない。`data` の形から見直す |
| `overview replaced:` の後が何も出ない | `write_obfuscated_json_file` を通っていない。世界の保存が別の経路に変わった |

あわせて見るもの:

- 概要を**空のまま**もう1つ生成し、`nothing was replaced for this world` が出ること
  （素のゲームのままになる経路）。仕掛け口を移したので、この行だけは**再確認が要る**
- 世界を作った後に**一度セーブして**、`OK` の行が増えないこと
  （照合は生成の直後の1回だけ）

済んだもの（2026-09-11）:

- 生成された世界の噂と最初のストーリークエストが、入力した設定の語彙で書かれているか。
  `output_data\` の突き合わせで確認済み（上の表）

---

## 4. ゲーム構造からの行き先（`docs\GAME.md` §2.27 の末尾へ戻す）

世界生成の経路そのもの（GAME.md §2.27）はゲームの事実なので `docs\` に置いたまま。
外したのは、そこから**この MOD へ渡していた3行**だけ。正式な番号へ戻すときは
§2.27 の末尾へ書き戻す。

入力した文章をそのまま世界にするには `905_fix_world_overview`。
あの MOD は差し替えた後に `world_data.json` を読み返して照合するので、
ここの読みが外れていれば `out\world_overview.log` に `WARN` が出る。

**`generate_new_world` を包んでも呼ばれない。**
画面はこの関数を**引数で受け取って控える**:

```text
scripts.hud.hud_world_generate:WorldGenerateScreen.__init__(
    self, screen_manager, generate_new_world_callback, **kwargs)
```

控えを取った後にモジュールの属性を差し替えても、控えの方には届かない。
画面はタイトルへ着くより前に組み上がっているので（`out\events.log` の
`app.world_generate_screen`）、遅延 import を待ってから当てる包みはたいてい間に合わない。
世界生成に仕掛けるなら、呼ぶ側が毎回モジュールを引く地点を選ぶこと
（保存なら `save_world_json:write_obfuscated_json_file`。VERIFICATION.md §3.4）。
