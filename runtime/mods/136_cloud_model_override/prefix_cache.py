# -*- coding: utf-8 -*-
"""プロンプトキャッシュの印をどこに置くかを決める。`openai_side.py` と `claude_side.py` が使う。

## なぜ「前回と同じ所まで」か

ゲームの頼みは1回ずつ完結していて、前置きの末尾まで毎回変わる。
2026-10-08 に実機で測ると、会話の system 4,552 字のうち前回と同じなのは先頭の 4,303 字で、
MOD の抜き出しの頼み（system 無し）は先頭の 1,294 字 / 965 字だけが同じだった。
OpenAI の既定（最新の発言の末尾に印）では、19回で読み出しが 0、入力の 98% を毎回書き込んでいた。
末尾にも system の末尾にも印を置いても当たらない。

そこで、頼みの種類ごとに直前の頼みを覚え、**先頭から一致した境目**に印を置く。
境目が本文の途中なら、その本文を2つに分けて前半に印を付ける（読む中身は変わらない）。

## 境目は動かさない

一度決めた境目は、次の頼みでもそこまで一致しているかぎり動かさない。
一致が延びたからといって境目を先へ動かすと、印の位置が前回と変わり、
前回書いた控えを読めずにまた書くことになる。
一致が境目より短くなったときだけ、新しい一致の所へ引き直す。

## 種類の見分け

頼みの名前（どの処理が頼んだか）は送信の1点では分からない。
覚えている頼みのうち、先頭からいちばん長く一致するものを同じ種類とみなす。
`MIN_CHARS` 字に届かなければ新しい種類として覚え、その回は印を置かない
（最初の1回は比べる相手が無いので、控えも書かない）。
"""

import threading

#: これより短い一致には印を置かない。API の最小の長さ（OpenAI 1,024 トークン、
#: Claude 512〜4,096 トークン）に届かない所へ置いても控えられないので、本文を分ける意味が無い。
MIN_CHARS = 1000

#: 覚えておく頼みの種類の数。古いものから忘れる。
FAMILIES = 16


def _common_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def common_chars(old, new):
    """2つの区切りの列が、先頭から何字一致するか。

    区切りは `(役, 本文, 印を置けるか)`。役が違えばそこで止める。
    """
    length = 0
    for a, b in zip(old, new):
        if a[0] != b[0]:
            break
        if a[1] == b[1]:
            length += len(a[1])
            continue
        length += _common_len(a[1], b[1])
        break
    return length


def locate(segments, offset, min_chars=MIN_CHARS):
    """先頭から offset 字の所に印を置くなら、どの区切りの何字目か。`(番号, 字数)` か None。

    印を置けない区切り（assistant の発言・画像・スキーマなど）に当たったら、
    その手前で印を置ける区切りの終わりまで戻す。戻した結果 min_chars に届かなければ None。
    """
    start = 0
    spots = []          # (区切りの番号, その区切りの中で置ける最後の字数, 先頭からの字数)
    for index, (_role, text, markable) in enumerate(segments):
        end = start + len(text)
        if markable and start < offset:
            spots.append((index, min(offset, end) - start, min(offset, end)))
        if end >= offset:
            break
        start = end
    while spots:
        index, chars, total = spots[-1]
        if chars > 0 and total >= min_chars:
            return index, chars
        spots.pop()
    return None


class Prefixes(object):
    """頼みの種類ごとの、直前の区切りの列と決めた境目。ゲームは別々のスレッドから同時に送るので鍵を掛ける。"""

    def __init__(self, min_chars=MIN_CHARS, size=FAMILIES):
        self.min_chars = min_chars
        self.size = size
        self.lock = threading.Lock()
        self.families = []      # [{"segments": [...], "cut": 先頭からの字数 or None}]

    def cut(self, segments):
        """この頼みのどこに印を置くか。`(区切りの番号, 字数)` か None。"""
        with self.lock:
            best, best_len = None, 0
            for family in self.families:
                length = common_chars(family["segments"], segments)
                if length > best_len:
                    best, best_len = family, length
            if best is None or best_len < self.min_chars:
                self.families.insert(0, {"segments": segments, "cut": None})
                del self.families[self.size:]
                return None
            self.families.remove(best)
            self.families.insert(0, best)
            best["segments"] = segments
            if best["cut"] is None or best["cut"] > best_len:
                spot = locate(segments, best_len, self.min_chars)
                if spot is None:
                    best["cut"] = None
                    return None
                index, chars = spot
                best["cut"] = sum(len(s[1]) for s in segments[:index]) + chars
            return locate(segments, best["cut"], self.min_chars)
