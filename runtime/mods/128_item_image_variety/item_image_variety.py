# -*- coding: utf-8 -*-
"""均し: アイテム画像の偏りを直す。

素のゲームはアイテム生成時の外見文（item_appearance、英語一文）を埋め込み、
`Data/item_embeddings/<item_detail>.json` の画像埋め込みと突き合わせて最も似た1枚を選ぶ（`Embedding.get_similar_id.get_similar_embedding_id`）。
乱数が無い argmax なので、外見文が定型化している品種では同じ画像に集中する（実測: potion は 52個中35個が同じ1枚。
調査の全数値は VERIFICATION.md §3 の 128_ の項）。

仕様は2行に尽きる（版6でここへ立ち返って作り直した）:

- **同じアイテムの再入手は同じ絵**（同じ敵の同じドロップ・再ドロップ）
- **別のアイテムには別の絵**（別名なのに同じ見た目、を避ける）

これを1つの表で達成する。**「外見文 → 絵」の対応を世界ごとに
`state/item_image/<世界>.json` へ永続化**し、

- 対応表にある外見文 → その絵を返す（ログは `kept`）。
  ドロップの定義（名前と外見文）はクエスト生成時に作られてセーブに保存される
  ので、同じドロップは必ず同じ外見文で来る ― 名前の解決は要らない
- 初めての外見文 → 類似度が top1 の SIM_FLOOR 倍以上の候補を最大 TOP_K 件とり、
  **対応表でまだ使われていない（使用数最少の）絵**を割り当てる（同数なら
  類似度の高いほう。乱数は無い）。ログは `chose`

使用数は対応表の値から数えるので、別のカウントは持たない。過去の版が
`counts` として残した累計は `legacy_counts` として読み、使用数の下駄に
足し続ける（見せた履歴を捨てないため。新規には書かない）。

併せて、素の埋め込み辞書に無い305枚（document 105 / creature_part 171 /
mushroom 28 / accessory 1）の追加辞書 `data/<item_detail>.json` を実行時に
合成して選択肢に加える（キャプションを付けて同じ埋め込み空間で作ったもの。
作り方は build/README.md）。

セーブには何も足さない。選ばれた image_src がアイテムに書かれるのは素の
ゲームと同じ。履歴を消してやり直したいときは該当世界の
`state/item_image/<世界>.json` を消せばよい。

対象はゲーム側の関数1つだけ。`__main__` への別名の張り替えはローダに任せる:

    Embedding.get_similar_id:get_similar_embedding_id(query_text, item_sub_type)
"""

import os
import sys
import threading

from instantale_modloader.state import (UNKNOWN_WORLD, world_filename,
                                        world_key)

# 候補を類似度上位から最大何件まで広げるか。
TOP_K = 10

# 候補に入れる類似度の下限（top1 の類似度に対する比）。
# クエリの近傍が密な品種では TOP_K 件まで開き、
# 急落する品種（clothing 等）では自動的に絞れる。
SIM_FLOOR = 0.8

# 同梱の追加辞書を使うか。
# 切ると素の辞書だけで均しをかける（切り分け用）。
USE_EXTRA_DICT = True

# 選択のたびに1行残すログの上限。
# 0 で無効。
LOG_LIMIT = 200

EMBEDDING_MODULE = "Embedding.get_similar_id"


def apply(ctx):
    emb_mod = sys.modules.get(EMBEDDING_MODULE)
    if emb_mod is None:
        ctx.log("{} not loaded; skipping".format(EMBEDDING_MODULE), level="WARN")
        return

    log = ctx.logger("item_image.log")
    # ctx.mod_dir / ctx.state_path は apply() の外を当てにしない。ここで控える。
    mod_dir = ctx.mod_dir
    state_dir = ctx.state_path("item_image")
    # 品物の生成は LLM のワーカースレッドから来る（同時に2本まで観測済み）。
    # 表の読み書きも控えの書き出しもここで直列化する。
    # `ctx.write_json` は隣に `<名前>.tmp` を書いてから差し替える形なので、
    # 2本が同時に通ると **片方が書きかけの tmp をもう片方が置き換える**。
    # 控えが壊れ、次の `read_json` で丸ごと失われる。
    lock = threading.Lock()
    state = {
        "world": None,   # images を読んだ世界（world_key）。替わったら読み直す
        "file": None,    # その世界の控え（state/item_image/<世界>.json）
        "images": {},    # (sub_type, 外見文) -> 絵。この表が仕様の本体
        "legacy": {},    # (sub_type, 絵) -> 旧版が数えた累計。使用数の下駄
        "usage": {},     # (sub_type, 絵) -> 使用数（images の値 + legacy から導出）
        "pools": {},     # sub_type -> (keys, 正規化済み行列, キー集合) のキャッシュ
        "logged": 0,
    }

    def ensure_world():
        """世界が替わっていたら、その世界の対応表を読み直す。"""
        app = getattr(sys.modules.get("__main__"), "instantale_app", None)
        world = world_key(app) if app is not None else UNKNOWN_WORLD
        if world == state["world"]:
            return
        if world == UNKNOWN_WORLD:
            # 世界名が読めない窓（読み込み中・新規作成中）。実在の世界として
            # 扱うと通った全世界の対応が1ファイルに混ざるので、控えを持たずに
            # その場をしのぐ（`902_city_case` と同じ判断）。
            state.update(world=world, file=None, images={}, legacy={}, usage={})
            log("seed: the world name is unreadable; not keeping the table for now")
            return
        path = os.path.join(state_dir, world_filename(world, ".json"))
        images = {}
        legacy = {}
        stored = ctx.read_json(path, default=None)
        if isinstance(stored, dict):
            for label, key in (stored.get("images") or {}).items():
                folder, _, text = label.partition("/")
                if folder and text and isinstance(key, str) and key:
                    images[(folder, text)] = key
            # 旧版（〜版5）の counts は「過去に見せた累計」。捨てずに下駄として残す。
            for label, n in (stored.get("legacy_counts")
                             or stored.get("counts") or {}).items():
                folder, _, key = label.partition("/")
                if folder and key and isinstance(n, int) and n > 0:
                    legacy[(folder, key)] = n
        usage = dict(legacy)
        for (folder, _text), key in images.items():
            spot = (folder, key)
            usage[spot] = usage.get(spot, 0) + 1
        state.update(world=world, file=path, images=images,
                     legacy=legacy, usage=usage)
        log("seed {!r}: {} texts mapped, legacy {} kinds".format(
            world, len(images), len(legacy)))

    def persist():
        """対応表を書く。失敗はローダが記録する（ゲームには流れない）。"""
        if not state["file"]:
            return
        data = {"images": {"{}/{}".format(folder, text): key
                           for (folder, text), key in sorted(state["images"].items())}}
        if state["legacy"]:
            data["legacy_counts"] = {"{}/{}".format(folder, key): n
                                     for (folder, key), n in sorted(state["legacy"].items())}
        ctx.write_json(state["file"], data)

    def load_pool(sub_type):
        """素の辞書と同梱の追加辞書を合成して (keys, tensor, キー集合) を返す。"""
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
        pool = (keys, matrix, frozenset(keys))
        state["pools"][sub_type] = pool
        log("pool {}: base={} merged={}".format(sub_type, n_base, len(keys)))
        return pool

    @ctx.wrap("Embedding.get_similar_id:get_similar_embedding_id", safe=True)
    def get_similar_embedding_id(orig, query_text, item_sub_type, *args, **kwargs):
        pool = load_pool(item_sub_type)
        if pool is None:
            return orig(query_text, item_sub_type, *args, **kwargs)
        keys, matrix, key_set = pool
        ensure_world()

        # 同じ外見文には同じ絵。埋め込みの計算より先に引けるので、再入手は速い。
        with lock:
            kept = state["images"].get((item_sub_type, query_text))
            if kept is not None and kept in key_set:
                if state["logged"] < LOG_LIMIT:
                    state["logged"] += 1
                    log("{}: kept {} for {!r}".format(
                        item_sub_type, kept, query_text[:80]))
                return kept

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
            with lock:
                chosen = min(
                    qualified,
                    key=lambda i: state["usage"].get((item_sub_type, keys[i]), 0))
        key = keys[chosen]
        with lock:
            state["images"][(item_sub_type, query_text)] = key
            spot = (item_sub_type, key)
            state["usage"][spot] = state["usage"].get(spot, 0) + 1
            persist()

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
