# -*- coding: utf-8 -*-
"""同梱 MOD の早見表（`docs/MODLIST.md`）を `mod.json` から組む。

    python tools/list_mods.py            docs/MODLIST.md を書き出す
    python tools/list_mods.py --check    ずれていたら 1 で終わる（書かない）

一覧が手書きだと、MOD を足したときに更新し忘れる。
名乗り・種別・設定の数はすべて `mod.json` に在るので、そこから組めば
ずれようが無い（[MODS.md](../docs/MODS.md) は説明の本体で、こちらは索引）。

「何をするか」の1行は MODS.md の見出しから採る。
2箇所に同じ文を置かずに済み、詳細への行き先もその見出しへ張れる。
見出しの無い MOD（計測のほとんど）は `mod.json` の説明の1文目に落ちる。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS_DIR = os.path.join(ROOT, "runtime", "mods")
DOCS_DIR = os.path.join(ROOT, "docs")
OUT_NAME = "MODLIST.md"

#: 種別の並びと見出し。語彙はローダの `KINDS`（TECH.md §3.2.2）。
GROUPS = (
    ("core", "基盤", "ゲームは変えない。他が触る前の素の状態を押さえる。"),
    ("fix", "修正", "ゲームのバグ・不便を直す。"),
    ("feature", "追加", "ゲームに無かった遊びを足す。"),
    ("probe", "計測", "ゲームは変えない。`out\\` にログを残すだけ。"
                      "デバッグモードのときだけ読み込まれる。"),
)

HEADING = re.compile(r"^###\s+`([^`]+)`\s*[:：]\s*(.+?)\s*$")
TABLE_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*$")


def anchor(heading: str) -> str:
    """GitHub が見出しに振る id。記号を落とし、空白を `-` にする。"""
    s = heading.lower()
    s = "".join(c for c in s if c.isalnum() or c in " -_")
    return s.strip().replace(" ", "-")


def mods_md() -> tuple:
    """MODS.md から「フォルダ -> (1行, 見出しの id)」と、計測の表を読む。"""
    path = os.path.join(DOCS_DIR, "MODS.md")
    lines = io.open(path, encoding="utf-8").read().split("\n")
    head, table = {}, {}
    for line in lines:
        m = HEADING.match(line)
        if m:
            folder, tail = m.group(1), m.group(2)
            head[folder] = (tail, anchor("`%s`: %s" % (folder, tail)))
            continue
        m = TABLE_ROW.match(line)
        if m and re.match(r"^\d{3}_", m.group(1)):
            table[m.group(1)] = m.group(2)
    return head, table


def text(value) -> str:
    """`{"ja":..., "en":...}` でも素の文字列でも読む。"""
    if isinstance(value, dict):
        return value.get("ja") or value.get("en") or ""
    return value or ""


def first_sentence(s: str) -> str:
    s = s.strip()
    i = s.find("。")
    return s[:i] if i > 0 else s


def collect() -> list:
    order = json.load(io.open(os.path.join(MODS_DIR, "load_order.json"),
                              encoding="utf-8"))
    head, table = mods_md()
    out = []
    for n, folder in enumerate(order.get("order") or [], 1):
        path = os.path.join(MODS_DIR, folder, "mod.json")
        if not os.path.isfile(path):
            print("実体が無い: %s" % folder, file=sys.stderr)
            continue
        data = json.load(io.open(path, encoding="utf-8"))
        tail, link = head.get(folder, ("", ""))
        # 早見表なので1文で足りる。続きは MODS.md にある。
        what = tail or first_sentence(
            table.get(folder) or text(data.get("description")))
        out.append({
            "order": n,
            "folder": folder,
            "kind": (data.get("kind") or "").lower(),
            "name": text(data.get("name")) or folder,
            "what": what,
            "settings": len(data.get("settings") or {}),
            "debug": bool(data.get("debug")),
            "superseded": data.get("superseded") or "",
            "link": link,
        })
    return out


def cell(s: str) -> str:
    """表の中で `|` が列の区切りに化けないようにする。"""
    return s.replace("|", "\\|")


def render(mods: list) -> str:
    counts = {}
    for m in mods:
        counts[m["kind"]] = counts.get(m["kind"], 0) + 1
    tally = " / ".join("%s %d" % (label, counts.get(key, 0))
                       for key, label, _ in GROUPS)

    L = []
    add = L.append
    add("# MOD 一覧")
    add("")
    add("同梱している MOD の早見表。")
    add("1本ずつの説明・設定・困ったときは [MODS.md](MODS.md)、")
    add("ローダと GUI の使い方は [README.md](README.md)。")
    add("")
    add("**この表は `tools/list_mods.py` が `mod.json` から組む。**")
    add("手で書き換えても次の生成で消える。")
    add("直す先は各 MOD の `mod.json` か MODS.md の見出し。")
    add("")
    add("同梱 %d 本（%s）。" % (len(mods), tally))
    add("")
    add("並びはフォルダ名順。")
    add("適用順はこれとは別で、GUI の `順` 列（`load_order.json`）が持つ。")
    add("")
    add("「GUI の名前」は GUI の一覧に出る文字（`mod.json` の名乗り）で、")
    add("「何をするか」は MODS.md の見出し。")
    add("同じ文字になっている MOD もある。")
    add("")
    add("「設定」は GUI の `設定` 列から変えられる項目の数。")
    add("「状態」の空欄は、入れれば普通に効くもの。")
    add("`取込済` はゲーム本体がその版で同じ修正を取り込んだので降ろしたもので、")
    add("デバッグモードのときだけ読み込まれる（TECH.md §3.2.5）。")
    add("")

    for key, label, note in GROUPS:
        group = sorted((m for m in mods if m["kind"] == key),
                       key=lambda m: m["folder"])
        if not group:
            continue
        add("---")
        add("")
        add("## %s（%d本）" % (label, len(group)))
        add("")
        add(note)
        add("")
        if key == "probe":
            add("| フォルダ | 何を測るか |")
            add("|---|---|")
            for m in group:
                add("| `%s` | %s |" % (m["folder"], cell(m["what"])))
        else:
            add("| フォルダ | GUI の名前 | 何をするか | 設定 | 状態 |")
            add("|---|---|---|---|---|")
            for m in group:
                folder = ("[`%s`](MODS.md#%s)" % (m["folder"], m["link"])
                          if m["link"] else "`%s`" % m["folder"])
                state = "取込済 %s" % m["superseded"] if m["superseded"] else ""
                add("| %s | %s | %s | %s | %s |" % (
                    folder, cell(m["name"]), cell(m["what"]),
                    m["settings"] or "-", state))
        add("")
    return "\n".join(L)


def main(argv) -> int:
    path = os.path.join(DOCS_DIR, OUT_NAME)
    body = render(collect())
    if "--check" in argv:
        old = io.open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
        if old != body:
            print("%s が古い。`python tools/list_mods.py` で作り直すこと。" % OUT_NAME)
            return 1
        print("%s は最新。" % OUT_NAME)
        return 0
    io.open(path, "w", encoding="utf-8", newline="\n").write(body)
    print("%s を書き出した。" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
