# 追加辞書（`../data/*.json`）の作り直し方

ゲーム更新で `Assets\images\item_candidates_dark\` に画像が増えたら、ここで作り直す。
一度きりのビルド用で、**実行時には読まれない**（実行時に読むのは `../data/` だけ）。

## 手順

1. 欠落一覧を出す（`missing_images.json` を作り直す。中身は
   フォルダ → 素の辞書に無い画像の絶対パスの一覧）。判定は
   「`Assets\images\item_candidates_dark\<folder>\` にあるのに
   `Data\item_embeddings\<folder>.json` にキーが無い」こと
2. 新しい画像1枚ごとに英語一文のキャプションを付け、`captions_<folder>.json`
   （`{拡張子なしファイル名: キャプション}`）に足す。文体はゲームの
   `item_appearance` と同じ（例: "A rusted, chipped small dagger with a worn
   wooden hilt."）。**見えるものだけを書く**。document フォルダには紙物でない
   画像（鍵・フラスコ等）が多数あるが、正直に書くこと（嘘のキャプションは
   検索を汚す）
3. `python build_extra_embeddings.py` を実行。キャプションを同梱モデル
   （all-MiniLM-L6-v2）の numpy 再実装（`minibert.py`）で埋め込み、
   `../data/<folder>.json` に素の辞書と同じ形式（`{キー: [384 floats]}`）で書く。
   自己検索（各キャプションが合成辞書で自分を top1 に引くか）まで検算される

## 前提と検証の経緯

- `minibert.py` はゲーム同梱の `runtime/models/embedding/` の重みをそのまま読む。
  ゲームの選択（実セーブで選ばれた画像）の argmax 再現率は 109/114（96%）で、
  埋め込み空間はゲーム本体と互換（VERIFICATION.md §3.26）
- 同梱のキャプション305件は画像を1枚ずつ目視して付けた。自己検索 305/305
- 必要なのは Python + numpy だけ（torch 不要）
