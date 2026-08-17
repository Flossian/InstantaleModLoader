# -*- coding: utf-8 -*-
"""均し: アイテム画像の偏りを直す。

素のゲームはアイテム生成時の外見文（item_appearance、英語一文）を埋め込み、
`Data/item_embeddings/<item_detail>.json` の画像埋め込みと突き合わせて
**最も似た1枚**を選ぶ（`Embedding.get_similar_id.get_similar_embedding_id`）。
乱数が無い argmax なので、外見文が定型化している品種では同じ画像に集中する
（実測: potion は 52個中35個が同じ1枚。調査の全数値は VERIFICATION.md §3 の
128_ の項）。

やることは2つ。

1. **候補を広げて使用回数最少を選ぶ。** 類似度が top1 の SIM_FLOOR 倍以上の
   候補を最大 TOP_K 件とり、その中で使用回数が最少の画像を選ぶ（同数なら
   類似度の高いほう）。乱数は使わない ― 同じ外見文が連続しても、2個目からは
   まだ使っていない候補へ順に流れる。
2. **辞書の欠落を補う。** 画像フォルダにあるのに埋め込み辞書に無い画像
   （document 105 / creature_part 171 / mushroom 28 / accessory 1 の計305枚）は
   素のゲームでは一度も選ばれない。この MOD が同梱する `data/<item_detail>.json`
   （キャプションを付けて同じ埋め込み空間で作った追加辞書。作り方も同 §3）を
   実行時に合成して、選択肢に加える。

使用回数はプロセス内でしか持たない（state/ には何も書かず、セーブにも
何も足さない ― 選ばれた image_src がアイテムに書かれるのは素のゲームと同じ）。
再起動すると数え直しになり、最初の1個は素のゲームと同じ top1 に落ちるが、
偏りの均しとしてはそれで足りる。

対象はゲーム側の関数1つだけ。`__main__` に別名があるので張り替えはローダに任せる:

    Embedding.get_similar_id:get_similar_embedding_id(query_text, item_sub_type)
"""

import os
import sys

# 候補を類似度上位から最大何件まで広げるか。
TOP_K = 10

# 候補に入れる類似度の下限（top1 の類似度に対する比）。クエリの近傍が
# 密な品種では TOP_K 件まで開き、急落する品種（clothing 等）では自動的に絞れる。
SIM_FLOOR = 0.8

# 同梱の追加辞書を使うか。切ると素の辞書だけで均しをかける（切り分け用）。
USE_EXTRA_DICT = True

# 選択のたびに1行残すログの上限。0 で無効。
LOG_LIMIT = 200

EMBEDDING_MODULE = "Embedding.get_similar_id"


def apply(ctx):
    emb_mod = sys.modules.get(EMBEDDING_MODULE)
    if emb_mod is None:
        ctx.log("{} not loaded; skipping".format(EMBEDDING_MODULE), level="WARN")
        return

    log = ctx.logger("item_image.log")
    # ctx.mod_dir は apply() の外では None になるので、ここで控える。
    mod_dir = ctx.mod_dir
    state = {
        "counts": {},   # (sub_type, key) -> この世界で選んだ回数（プロセス内のみ）
        "pools": {},    # sub_type -> (keys, 正規化済み行列) のキャッシュ
        "logged": 0,
        "extra_total": 0,
    }

    def load_pool(sub_type):
        """素の辞書と同梱の追加辞書を合成して (keys, tensor) を返す。"""
        if sub_type in state["pools"]:
            return state["pools"][sub_type]
        import json

        import torch

        # ゲーム自身と同じ相対パス（カレントはゲーム本体のフォルダ）。
        base_path = os.path.join("Data", "item_embeddings", sub_type + ".json")
        if not os.path.isfile(base_path):
            state["pools"][sub_type] = None
            return None
        with open(base_path, encoding="utf-8") as fh:
            table = json.load(fh)
        n_base = len(table)
        if USE_EXTRA_DICT:
            extra_path = os.path.join(mod_dir, "data", sub_type + ".json")
            extra = ctx.read_json(extra_path, default=None)
            if extra:
                for key, vec in extra.items():
                    table.setdefault(key, vec)  # 素の辞書が同じキーを得たら素を優先
        keys = list(table)
        matrix = torch.tensor([table[k] for k in keys], dtype=torch.float32)
        matrix = matrix / matrix.norm(dim=1, keepdim=True).clamp_min(1e-12)
        pool = (keys, matrix)
        state["pools"][sub_type] = pool
        state["extra_total"] += len(keys) - n_base
        log("pool {}: base={} merged={}".format(sub_type, n_base, len(keys)))
        return pool

    @ctx.wrap("Embedding.get_similar_id:get_similar_embedding_id", safe=True)
    def get_similar_embedding_id(orig, query_text, item_sub_type, *args, **kwargs):
        pool = load_pool(item_sub_type)
        if pool is None:
            return orig(query_text, item_sub_type, *args, **kwargs)
        keys, matrix = pool

        query = emb_mod.text_to_embedding(query_text)
        query = query.detach().reshape(-1).to(dtype=matrix.dtype)
        query = query / query.norm().clamp_min(1e-12)
        sims = matrix @ query

        top_sim, top_idx = sims.max(dim=0)
        top_sim = float(top_sim)
        if top_sim <= 0:
            # 近い候補がまったく無い。広げても意味が無いので素の挙動どおり。
            chosen = int(top_idx)
        else:
            order = sims.argsort(descending=True)[:TOP_K].tolist()
            qualified = [i for i in order if float(sims[i]) >= top_sim * SIM_FLOOR]
            chosen = min(
                qualified,
                key=lambda i: state["counts"].get((item_sub_type, keys[i]), 0))
        key = keys[chosen]
        state["counts"][(item_sub_type, key)] = \
            state["counts"].get((item_sub_type, key), 0) + 1

        if state["logged"] < LOG_LIMIT:
            state["logged"] += 1
            rank = 0 if top_sim <= 0 else qualified.index(chosen)
            log("{}: chose {} (rank {} of {} qualified, sim {:.3f}, top1 {} {:.3f}) "
                "for {!r}".format(
                    item_sub_type, key, rank,
                    1 if top_sim <= 0 else len(qualified),
                    float(sims[chosen]), keys[int(top_idx)], top_sim,
                    query_text[:80]))
        return key

    ctx.log("item_image_variety: installed (TOP_K={}, SIM_FLOOR={}, extra dict={})".format(
        TOP_K, SIM_FLOOR, "on" if USE_EXTRA_DICT else "off"))
