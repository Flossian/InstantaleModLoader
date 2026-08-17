# -*- coding: utf-8 -*-
"""all-MiniLM-L6-v2 を numpy だけで再現する最小実装。

ゲーム同梱の model.safetensors / vocab.txt を読み、
text_to_embedding（mean pooling）を再現する。
検証は item_image_sim.py 側で行う（実選択の argmax 再現率）。
"""
import json
import struct
import unicodedata

import numpy as np

MODEL_DIR = r"C:\Program Files\Epic Games\Instantaleq6Ve7\runtime\models\embedding\models--sentence-transformers--all-MiniLM-L6-v2"


def load_safetensors(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n).decode("utf-8"))
        base = 8 + n
        blob = f.read()
    out = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        dtype = {"F32": np.float32, "F16": np.float16, "I64": np.int64}[info["dtype"]]
        s, e = info["data_offsets"]
        arr = np.frombuffer(blob[s:e], dtype=dtype).reshape(info["shape"])
        out[name] = arr.astype(np.float32) if arr.dtype != np.float32 else arr
    return out


# --- BERT uncased トークナイザ（basic + WordPiece） ---
class Tokenizer:
    def __init__(self, vocab_path):
        self.vocab = {}
        with open(vocab_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                self.vocab[line.rstrip("\n")] = i
        self.unk = self.vocab["[UNK]"]

    @staticmethod
    def _is_punct(ch):
        cp = ord(ch)
        if (33 <= cp <= 47) or (58 <= cp <= 64) or (91 <= cp <= 96) or (123 <= cp <= 126):
            return True
        return unicodedata.category(ch).startswith("P")

    def _basic(self, text):
        text = text.lower()
        text = unicodedata.normalize("NFD", text)
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")
        tokens = []
        cur = []
        for ch in text:
            if ch.isspace():
                if cur:
                    tokens.append("".join(cur)); cur = []
            elif self._is_punct(ch):
                if cur:
                    tokens.append("".join(cur)); cur = []
                tokens.append(ch)
            else:
                cur.append(ch)
        if cur:
            tokens.append("".join(cur))
        return tokens

    def _wordpiece(self, word):
        if len(word) > 100:
            return [self.unk]
        ids = []
        start = 0
        while start < len(word):
            end = len(word)
            cur_id = None
            while start < end:
                sub = word[start:end]
                if start > 0:
                    sub = "##" + sub
                if sub in self.vocab:
                    cur_id = self.vocab[sub]
                    break
                end -= 1
            if cur_id is None:
                return [self.unk]
            ids.append(cur_id)
            start = end
        return ids

    def encode(self, text, max_len=512):
        ids = [self.vocab["[CLS]"]]
        for w in self._basic(text):
            ids.extend(self._wordpiece(w))
        ids = ids[: max_len - 1]
        ids.append(self.vocab["[SEP]"])
        return ids


class MiniLM:
    def __init__(self, model_dir=MODEL_DIR):
        import os
        self.w = load_safetensors(os.path.join(model_dir, "model.safetensors"))
        self.tok = Tokenizer(os.path.join(model_dir, "vocab.txt"))
        cfg = json.load(open(os.path.join(model_dir, "config.json"), encoding="utf-8"))
        self.n_layers = cfg["num_hidden_layers"]
        self.n_heads = cfg["num_attention_heads"]
        self.eps = cfg.get("layer_norm_eps", 1e-12)

    @staticmethod
    def _ln(x, g, b, eps):
        mu = x.mean(-1, keepdims=True)
        var = x.var(-1, keepdims=True)
        return (x - mu) / np.sqrt(var + eps) * g + b

    def embed(self, text):
        w = self.w
        ids = np.array(self.tok.encode(text))
        L = len(ids)
        x = (w["embeddings.word_embeddings.weight"][ids]
             + w["embeddings.position_embeddings.weight"][:L]
             + w["embeddings.token_type_embeddings.weight"][0])
        x = self._ln(x, w["embeddings.LayerNorm.weight"], w["embeddings.LayerNorm.bias"], self.eps)
        H = self.n_heads
        d = x.shape[-1] // H
        for i in range(self.n_layers):
            p = f"encoder.layer.{i}."
            q = x @ w[p + "attention.self.query.weight"].T + w[p + "attention.self.query.bias"]
            k = x @ w[p + "attention.self.key.weight"].T + w[p + "attention.self.key.bias"]
            v = x @ w[p + "attention.self.value.weight"].T + w[p + "attention.self.value.bias"]
            q = q.reshape(L, H, d).transpose(1, 0, 2)
            k = k.reshape(L, H, d).transpose(1, 0, 2)
            v = v.reshape(L, H, d).transpose(1, 0, 2)
            att = q @ k.transpose(0, 2, 1) / np.sqrt(d)
            att = att - att.max(-1, keepdims=True)
            att = np.exp(att)
            att /= att.sum(-1, keepdims=True)
            ctx = (att @ v).transpose(1, 0, 2).reshape(L, -1)
            ctx = ctx @ w[p + "attention.output.dense.weight"].T + w[p + "attention.output.dense.bias"]
            x = self._ln(x + ctx, w[p + "attention.output.LayerNorm.weight"], w[p + "attention.output.LayerNorm.bias"], self.eps)
            h = x @ w[p + "intermediate.dense.weight"].T + w[p + "intermediate.dense.bias"]
            h = 0.5 * h * (1.0 + erf_np(h / np.float32(np.sqrt(2.0))))
            h = h @ w[p + "output.dense.weight"].T + w[p + "output.dense.bias"]
            x = self._ln(x + h, w[p + "output.LayerNorm.weight"], w[p + "output.LayerNorm.bias"], self.eps)
        return x.mean(0)  # mean pooling（全トークン、マスク無し=単文なので同じ）


def erf_np(x):
    # Abramowitz–Stegun 7.1.26 の近似では精度不足の恐れがあるため、
    # tanh 近似ではなく scipy 相当の有理近似を使う
    a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-ax * ax)
    return sign * y
