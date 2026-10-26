# -*- coding: utf-8 -*-
r"""Danbooru のタグ一覧（版26）。規則の「タグの照合」が引く。

Illustrious・NoobAI・Pony は Danbooru のタグで学習しているので、
タグに無い言い方は効きが弱く、タグの語が紛れた言い方はタグの意味で描かれる
（`battle-worn plate mail` の `plate` が皿になった。DOC.md §3）。

一覧は a1111-sd-webui-tagcomplete の `tags/danbooru.csv`（MIT）。
1行が `タグ,種類,件数,"別名,別名"`。種類は 0 一般・1 絵師・3 作品・4 キャラ・5 メタ。
**照合に使うのは一般とメタだけ**。絵師とキャラの名前（約10万）まで数えると、
`quiet` がキャラの別名として通るようなことが起きる。

置き場は `state\stable_diffusion\danbooru.csv`（道具画面の「モデル別」から取る）。
"""
import csv
import io
import os
import re

URL = ("https://raw.githubusercontent.com/DominikDoom/a1111-sd-webui-tagcomplete/"
       "main/tags/danbooru.csv")
LICENSE = "MIT（DominikDoom/a1111-sd-webui-tagcomplete）"
DIRNAME = "stable_diffusion"
FILENAME = "danbooru.csv"

#: 照合に数える種類（一般・メタ）。
KEEP_CATEGORIES = ("0", "5")

#: 紛れた語として知らせる1語のタグの下限（件数）。少ないタグは知らせても役に立たない。
HIDDEN_MIN_COUNT = 5000

_CACHE = {"key": None, "book": None}


def path_of(state_dir):
    return os.path.join(state_dir, DIRNAME, FILENAME)


def norm(text):
    """比べる形。小文字・空白は下線・タグの括弧の逃がし（`\\(`）を戻す。"""
    text = str(text or "").replace("\\(", "(").replace("\\)", ")").strip().lower()
    return re.sub(r"[\s_]+", "_", text)


def display(tag):
    """プロンプトに書く形。下線は空白、括弧は強調と読まれないよう逃がす。"""
    return tag.replace("_", " ").replace("(", "\\(").replace(")", "\\)")


class Book(object):
    """タグと別名。`tags` は 形 -> 件数、`alias` は 別名の形 -> タグの形。"""

    def __init__(self, tags, alias):
        self.tags = tags
        self.alias = alias

    def lookup(self, text):
        """`("tag", タグ)` / `("alias", タグ)` / None。"""
        key = norm(text)
        if not key:
            return None
        if key in self.tags:
            return ("tag", key)
        if key in self.alias:
            return ("alias", self.alias[key])
        return None

    def hidden(self, text):
        """タグでない言い方の中に紛れた、よく使われる1語のタグ。"""
        words = re.findall(r"[a-z0-9']+", str(text or "").lower())
        if len(words) < 2:
            return []
        return [w for w in words if self.tags.get(w, 0) >= HIDDEN_MIN_COUNT]


def parse(lines):
    """csv の行から `Book` を作る。読めない行は飛ばす。"""
    tags, pending = {}, []
    for row in csv.reader(lines):
        if len(row) < 3 or row[1] not in KEEP_CATEGORIES:
            continue
        name = norm(row[0])
        try:
            tags[name] = int(row[2] or 0)
        except ValueError:
            tags[name] = 0
        if len(row) > 3 and row[3]:
            pending.extend((norm(a), name) for a in row[3].split(",")
                           if a.strip() and not a.strip().startswith("/"))
    alias = {}
    for key, name in pending:
        if key not in tags:                  # タグそのものと同じ形の別名は、タグが勝つ
            alias.setdefault(key, name)
    return Book(tags, alias)


def load(state_dir):
    """置き場の一覧を読む。無ければ None。更新時刻と大きさが同じなら読み直さない。"""
    path = path_of(state_dir)
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime, stat.st_size)
    if _CACHE["key"] != key:
        try:
            with io.open(path, encoding="utf-8", newline="") as fh:
                _CACHE["book"] = parse(fh)
            _CACHE["key"] = key
        except Exception:
            return None
    return _CACHE["book"]
