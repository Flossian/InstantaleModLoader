# `909_image_no_pixelate`: 生成した画像を荒くしない

この MOD は**開発中（9xx）**。
git には入れるが、CI・配布物・`load_order.json`・`docs\` の文書には入らない（TECH.md §2.6）。
配らない理由は「正式化しない」と決めてあるため。
手元の絵の好みに閉じた変更で、素の画風を壊す方向に効くので、配る意味が無い。

そのため遊び方も検証の記録もこの1枚にまとめてある。
気が変わって正式な番号へ振り直すときは、ここの各節を元の場所へ戻す。

| ここにある節 | 戻す先 |
| --- | --- |
| 1. 遊び方・困ったとき | この1枚のまま。`load_order.json` と `BANDS`（`1xx` の末尾）に載せれば `docs\MODS.md` に載る（TECH.md §2.7） |
| 2. 検証の一覧に載せる行 | `docs\VERIFICATION.md` §1「修正（100番台）」の表 |
| 3. 確かめたことと、まだのこと | `docs\VERIFICATION.md` §3 の末尾（節番号はそのとき振る） |
| 4. ゲーム構造からの行き先 | `docs\GAME.md`（画像生成の節。無ければ §2.15 の隣に新しく作る） |

振り直す先は `131_image_no_pixelate`。
ゲーム本体の挙動を変えるので 1xx（TECH.md §3.2.2）。

`tools\tests\test_wip_image_no_pixelate.py`（47件）も同じ理由で管理外。
戻すときは `test_image_no_pixelate.py` に改名して CI の対象へ入れる。

---

## 1. 遊び方（`docs\MODS.md` 相当）

### `909_image_no_pixelate`: 生成した画像を荒くしない

ゲームは Stable Diffusion で描いた絵を、保存する前にわざと荒くしている。
実機で採った経路（§3.2。寸法は実測値）:

```
generated_image.png            512x1024   SD の出力
no_bg_image.png                512x1024   背景を抜いた絵
  pixel_art_process(512x1024) -> (512x1024, 165x330)
pixelated_image_original.png   165x330    ← 2つ目がそのまま保存される
  それを2倍にして 330x660
  reduce_image_colors(330x660) -> 330x660
reduced_color_image.png        330x660    ← 立ち絵。会話中に見えるのはこれ
face_image.png                            立ち絵から切り出す
```

**捨てられているのは寸法**。
165x330 まで縮めて2倍に伸ばしているので、立ち絵 330x660 の中身は
165x330 ぶんの細かさしか無い。顔も同じ絵から切るので同じだけ荒い。

この MOD は**荒くする工程が返す絵を、元の絵から作り直した同じ寸法の絵に差し替える**。
165x330 の枠には元の絵を 165x330 へ縮めたものを入れる
（ゲームは最近傍で潰していたところを、フィルタを掛けて縮める）。
減色の段には、1つ手前の元の絵を 330x660 へ縮めたものを渡す。

**寸法はどこも変えない**。
立ち絵は 330x660 のまま、顔の座標もずれない。
細かさの上限が 330x660 になるのはそのためで、SD が描いた 512x1024 には届かない
（`165x330 を2倍` を決めているのはゲーム側で、そこを動かすと表示まで連鎖する）。

効くのは**これから作られる画像だけ**。
既に居る NPC の絵は荒いまま残る。

| 設定 | 意味 |
| --- | --- |
| キャラクタの絵を作り直す | NPC・敵・モンスターの立ち絵。既定は入 |
| 背景の絵も作り直す | 既定は切。背景は画面いっぱいに出るので画風の変化が最も目に付く。背景の減色は別経路（`reduce_color` モジュール越し）なので追っていない |
| 減色の段も作り直す | **立ち絵の中身を決めるのはここ**。既定は入。切ると縮めた絵を2倍に伸ばしたものが立ち絵になり、この MOD はほとんど効かない |
| 記録をログに出す回数 | `out\image_no_pixelate.log` に出す行数。工程ごとに数える。既定8 |

絵の見た目は変わる。
ドット絵寄りの画風は、この工程が作っていたもの。
作り直すと滑らかな絵になるので、素の画風を保ちたいなら入れない。

#### 困ったとき

| 症状 | やること |
| --- | --- |
| **NPC の生成が進まない** | `out\live_crashes.log` に `Thread-N (generate_images)` のクラッシュが無いか見る。版1がこれを起こした（§3.1）。同じ止まり方をしたら真っ先に切って確かめる |
| ピクセル化した絵のまま | `out\image_no_pixelate.log` に `減色: ... 作り直し` の行が出ているか見る。**この行が無いと立ち絵には効かない**（版2がこれで効かなかった。§3.1）。設定の「減色の段も作り直す」が切になっていないか |
| 絵が変わらない | ログに行が1つも無ければ経路を通っていない。行が出ているのに絵が荒いなら、その NPC の絵は前から在ったもの（この MOD は描く経路にしか居ない） |
| 顔が小さい（32x64） | 顔が見つからなかった回。`顔の検出` の行の `座標` が `None` になっている。**素のゲームでも起きる**（検出はこの MOD が触る前の絵に対して走る。§3.4） |
| `WARN image no pixelate: 差し替える画像が見つからない` | 戻り値に縦横比の合う画像が無かった。その回はゲームのものをそのまま返しているので生成は止まらない。警告に出る「元絵」と「戻り」の形を見て、選び方を直す |
| `WARN image no pixelate: 画像ではない値` | その工程には PIL の画像以外が渡っている。実害は無いが作り直しは効いていない |

---

## 2. 検証の一覧に載せる行（`docs\VERIFICATION.md` §1 の表）

| mod | 内容 | 状態 | 根拠 |
| --- | --- | --- | --- |
| `909_image_no_pixelate` | 生成した画像を荒くする工程の成果を作り直す | **決着**（2026-08-30 17:09〜17:12。NPC 4体。§3.4）。版1は実機で NPC 生成を止め、版2は実機で効かなかった（どちらも経路の読み違い。§3.1）。オフライン47件全通・`tools/tests/test_wip_image_no_pixelate.py`。ただし立ち絵だけなら `InstantaleLauncher` の `PortraitWatcher` の方が細かい（§3.5） | 本書 §3 / §4 |

---

## 3. 確かめたことと、まだのこと（`docs\VERIFICATION.md` §3 相当）

### 3.1 版1と版2で踏んだこと

**版1（2026-08-30 16:45）: NPC の生成が止まった。**
`orig` を呼ばずに、渡された画像の写しを1枚返していた。

```
out\image_no_pixelate.log  16:45:22.970  キャラクタ: 素通し 512x1024 RGBA（1回目）
out\live_crashes.log       16:45:22.972  THREAD CRASH: Thread-88 (generate_images)
  TypeError: cannot unpack non-iterable Image object
    instantale.py:3624                ConversationStartManager.generate_images
    instantale.py:2206                InstantaleApp.generate_and_write_character_detail
    image_generation_creature.py:120  generate_character_image
```

ゲーム側は `a, b = pixel_art_process(...)` の形で受けている。
戻り値の形を1枚と決め打ちしたのが誤り。
生成はバックグラウンドスレッドなので**ゲームは落ちず**、
画像が書かれないまま `wait_generate_chracter_image` が待ち続けた。

**版2（16:58）: 落ちなくなったが、絵は荒いままだった。**
「入力と同じ寸法の画像」だけを差し替える作りにしたところ、
差し替えていたのは**ゲームが捨てる側**だった。

```
16:58:01.026  キャラクタ: 差し替え 1枚 入力 512x1024 / 戻り (512x1024、165x330)
16:58:01.486  減色:      差し替え 1枚 入力 330x660  / 戻り 330x660
16:58:01.530  キャラクタ: 差し替え 1枚 入力 330x660  / 戻り (330x660、16x32)
```

**`pixel_art_process` の戻り値は `(入力そのまま, 縮めた絵)` で、
ゲームが使うのは2つ目**。1つ目を差し替えても保存されない。
減色の段も、入力（既に潰れた 330x660）の写しを返していたので、
16色への減色を飛ばしただけで細かさは戻らなかった。

版3は戻って来た画像を**全部**元の絵から作り直し、
減色の段には1つ手前の元の絵をスレッドごとに控えて渡す。

### 3.2 実測（ファイルの寸法は PNG のヘッダから）

| 分かったこと | 根拠 |
| --- | --- |
| 経路と寸法（本書 §1 の図） | `%LOCALAPPDATA%\Darmabeko\Instantale\worlds\新テストワールド\characters\グレン\` の5枚 |
| `pixel_art_process` は2つ組を返し、2つ目が縮めた絵 | 版2のログの `戻り (512x1024、165x330)` と、`pixelated_image_original.png` が 165x330 であること |
| 立ち絵は縮めた絵の2倍 | 165x330 → `reduced_color_image.png` 330x660。顔の側も 16x32 → 32x64 で同じ2倍 |
| 立ち絵は減色の戻り値がそのまま保存される | 版2で減色を素通しにした回の `reduced_color_image.png` が 538KB（素の 323KB より大きい＝16色になっていない） |
| 荒くする工程に入る絵は 512x1024 | 版1のログ。**プロキシが 640x1216 で描いても、ここへ来るのは 512x1024**（§4 の推測はここで訂正） |

### 3.3 まだ分かっていないこと

- `165x330` をゲームがどう決めているか（512x1024 に対して約 1/3.1）。
  いまの上限 330x660 はここで決まっているので、細かさを上げるならここ。
  ただし**立ち絵の寸法も一緒に動く**（ゲームがこれを2倍したものが立ち絵）。
  値の出どころが分からないうちは触らない
- 背景の減色（`reduce_color` モジュール越しの呼び出し）。
  背景を入にしても `pixel_art_process` の側しか押さえていない
- 顔が見つからない回の割合と、見つからない絵の傾向（§3.4）。
  17時台の記録では `顔の検出` 8行が全て `座標 None`。
  記録が `LOG_LIMIT` で切れたアリエッタだけが 165x165 で、そこは見つかっている。
  4回中4回外して1回当たり、という数え方しかまだできない
- 17:10:15 の一組（`顔の検出` 2行・`キャラクタ` 2行・`減色` 1行）が、
  ディスクに何も残していない。
  記録は5組あるのに `worlds\新テストワールド\characters\` に増えたのは4体。
  失敗して捨てられた生成か、立ち絵フォルダに残らない絵（敵）のどちらか

### 3.4 実機で通った（2026-08-30 17:09〜17:12。NPC 4体）

`out\image_no_pixelate.log`。4体とも同じ形で通り、
`out\live_crashes.log` に 17時台のクラッシュは1件も無い。

```
17:09:11.391  顔の検出: 入力 512x1024 RGBA / 座標 None（1回目）
17:09:11.429  顔の検出: 入力 512x1024 RGBA / 座標 None（2回目）
17:09:12.702  キャラクタ: 2枚 作り直し 元絵 512x1024 / 入力 512x1024 / 戻り (512x1024、165x330)
17:09:13.142  減色:      1枚 作り直し 元絵 512x1024 / 入力 330x660  / 戻り 330x660
17:09:13.172  キャラクタ: 2枚 作り直し 元絵 330x660  / 入力 330x660  / 戻り (330x660、16x32)
```

| 見たところ | 結果 |
| --- | --- |
| **控えが渡っている** | 減色の `元絵` が毎回 512x1024。立ち絵 330x660 の中身が元の絵から作られている |
| 縮めた側も作り直している | `キャラクタ: 2枚`（1つ目 512x1024 と2つ目 165x330 の両方） |
| 寸法はどこも変わっていない | `pixelated_image_original.png` 165x330 / `reduced_color_image.png` 330x660。素と同じ |
| 生成が止まらない | 4体とも通し。`generate_images` のクラッシュは版1の16:45が最後 |
| ファイルの嵩 | `reduced_color_image.png` 172〜267KB（素は 323KB、版2は 538KB）。16色の点描が消えたぶん小さい |

#### 顔が小さくなる回は、この MOD より前で決まっている

**`detect_face_coordinates` は素の 512x1024（`generated_image.png`）に対して走り、
`None` を返すことがある。** 走る時刻はこの MOD が最初に触るより 1.3 秒前で、
入力はこの MOD が一度も触っていない絵。**顔の失敗はゲーム自身の挙動**。

| NPC | 顔の検出 | `face_image.png` |
| --- | --- | --- |
| グレン / 事務官のセレス / 鉄錆のカイ | `座標 None` | 32x64（見つからなかったときの落とし所） |
| アリエッタ | 記録は `LOG_LIMIT` で切れている | **165x165**（見つかった） |

見つからなかった回は、立ち絵とは別に `pixel_art_process` の2つ目（16x32）を
2倍にしたものが顔として保存される。素のゲームでも同じ落ち方をする。

`顔の検出` の行はすぐ `LOG_LIMIT` に達する（1体につき2回呼ばれる）。
続けて見たいときは設定の「記録をログに出す回数」を上げる。

#### 確認手順

1. デバッグモードは要らない。注入して、まだ絵の無い NPC と会話する
2. `out\live_crashes.log` に `generate_images` のクラッシュが**増えていない**ことを見る
3. `out\image_no_pixelate.log` を読む
4. `%LOCALAPPDATA%\Darmabeko\Instantale\worlds\<世界>\characters\<名前>\` の
   `reduced_color_image.png`（330x660）と `face_image.png` を開く

| 出た行 | 意味 |
| --- | --- |
| `減色: 1枚 作り直し 元絵 512x1024 RGBA / 入力 330x660 ...` | **決着**。`元絵` が 512x1024 なら控えが効いている。立ち絵がこの中身になる |
| `減色: ... 元絵 330x660 ...` | 控えが渡っていない（`元絵` と `入力` が同じ）。細かさは戻らない。手前の `キャラクタ:` の行が同じスレッドに在るか見る |
| `顔の検出: 入力 330x660 RGBA / 座標 ...` | 顔がどの絵のどこから切られたか。座標が空なら見つかっていない |
| `作り直しを仕掛けた: ...` だけで以降が無い | 包みは仕掛けたが描く経路を通っていない。絵の在る NPC としか話していないか、フックがまだ当たっていない（`out\modloader.log` の `wrapped image_generation...`） |
| `WARN ... 差し替える画像が見つからない` | 縦横比で選ぶ読みが外れている。生成は止まらない |
| 行が1つも無い | 注入自体が効いていない。`out\modloader.log` を見る |

### 3.5 同じ目的の道具が既に手元で動いていた（2026-08-30 に気づいた）

`InstantaleHDPortraitScript`（`hd_portrait.ps1`）と、それを取り込んだ
`InstantaleLauncher` の `PortraitWatcher` が、**同じことを別の場所でやっている**。
ゲームが立ち絵を書き終えた後に、`reduced_color_image.png` を
`no_bg_image.png`（背景を抜いた 512x1024 か 640x1216）で上書きし、
元のドット絵を `reduced_color_image.orig.png` へ退避する。

手元の全世界の立ち絵を数えると、どちらが効いていたかがそのまま出る:

| 立ち絵 | 件数 | `.orig.png` |
| --- | --- | --- |
| 512x1024 か 640x1216 | 359 | ある（PortraitWatcher が上書きした） |
| 330x660 | 4 | ない（17時台にこの MOD だけで作った4体） |

**あちらの方が細かい**。
この MOD は 330x660 の枠を守ったまま中身を作り直すので、
上限は 330x660（§1）。
PortraitWatcher は枠ごと 512x1024 に差し替えるので、SD が描いた寸法がそのまま出る。

- 17時台の4体に `.orig.png` が無いのは、そのとき PortraitWatcher が動いていなかったから。
  あちらの「適用済み」の判定は
  `reduced_color_image.png` と `no_bg_image.png` の大きさと更新時刻が一致するかで、
  この MOD の出した 330x660 はそれに当たらない。
  両方動かせば、この MOD の作り直した立ち絵は上書きされて消える
- この MOD にしか残らない持ち分は `face_image.png` と
  `pixelated_image_original.png` の中身。
  PortraitWatcher はゲームが書き終えた後に動くので、
  顔を切る段には間に合わない（顔はドット絵から切られたまま）

**続けるかどうかを決めるのはここ**。
立ち絵だけが目的なら PortraitWatcher で足りていて、この MOD は要らない。
顔まで滑らかにしたいなら、この MOD を残して立ち絵の側だけあちらに任せる形になる。

---

## 4. ゲーム構造からの行き先（`docs\GAME.md` へ戻す）

画像生成の経路そのものはゲームの事実なので、正式化するときは GAME.md へ書く。
§1 の図と §3.2 の表がその中身。

```
image_generation.sdcppcuda.image_generation_creature
    generate_character_image(world_name, name, category, prompt)
    generate_enemy_image(world_name, race, name, appearance, size, positive_prompt=None)
    generate_character_image_from_enemy(world_name, name, base_image_path, pos, neg)
    generate_enemy_image_from_character(world_name, name, fullbody_path, size)
    character_generation_quality = 'highres_upscale'
    monster_generation_quality   = 'highres_faster'
image_generation.sdcppcuda.image_generation_background
    generate_and_save_background(world_name, location, positive_prompt, negative_prompt)
    background_generation_quality = 'highres_faster'
```

NPC ごとのフォルダに残る5枚（`%LOCALAPPDATA%\Darmabeko\Instantale\worlds\<世界>\characters\<名前>\`）:

| ファイル | 寸法 | 何の絵か |
| --- | --- | --- |
| `generated_image.png` | 512x1024 | SD の出力 |
| `no_bg_image.png` | 512x1024 | 背景を抜いた絵 |
| `pixelated_image_original.png` | 165x330 | `pixel_art_process` の2つ目 |
| `reduced_color_image.png` | 330x660 | 立ち絵。会話中に見えるのはこれ |
| `face_image.png` | まちまち | 立ち絵から切り出した顔 |

セーブの `image_src` は5つの鍵（`base_normal` / `base_upscaled` / `fullbody` /
`opponent` / `face`）を持つ。どの鍵がどのファイルを指すかは突き合わせていない。

### 手元の環境では SD のプロキシ MOD が入っている

ゲームフォルダに別作者の `InstantaleSDMod`（`stable-diffusion.dll` を差し替える
プロキシ）が入っていて、ゲームが頼んだ寸法より大きく描かせている。
`InstantaleSDMod\proxy_resize.log` の記録:

```
256x512  -> 640x1216  [portrait]     127回
512x1024 -> 640x1216  [portrait]     126回
512x512  -> 832x832   [square]        54回
1024x512 (no scale)   [landscape]    100回
```

**ただしゲームの手に渡る絵は 512x1024 だった**（§3.2）。
プロキシが大きく描いても、そこから先へは頼んだ寸法で渡っている。
どこで縮んでいるかは追っていない（プロキシ側で戻しているか、
sd.cpp の束ね口が頼んだ寸法へ揃えているかの2択）。

プロキシは DLL の差し替えで、このローダとは別の仕組み。
どちらか一方だけでも動く。
