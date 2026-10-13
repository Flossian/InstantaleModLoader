# -*- coding: utf-8 -*-
"""検証の記録（`docs/verification/`）の目次を作り、`§` 参照の行き先を確かめる。

    python tools/build_verification.py           目次を書き出す
    python tools/build_verification.py --check   目次のずれと行き先の無い参照があれば 1 で終わる（書かない）

§2（実機・実データの記録）と §3（未確認項目と確認手順）は1節1ファイルで
`docs/verification/<番号>.md` に置く。1冊に積むと際限なく伸びて、読むにも
直すにも全体を開くことになった（2026-10 に 800 KB）。ファイルの1行目が
`# 3.88 見出し` の形で、ファイル名はその番号。欠番をまとめた節だけは
`# 2.19〜2.22（欠番）` の形で、ファイル名は先頭の番号。

目次は `docs/VERIFICATION_LOG.md`（§2）と `docs/VERIFICATION.md`（§3）の
印の間に書く。手で書き換えても次の生成で消える。

参照の検査は、git が管理するテキストの中の `VERIFICATION.md §3.x` /
`VERIFICATION_LOG.md §2.x` と、検証の文書の中の文書名の無い `§2.x` / `§3.x` を
見る。番号は §1〜§4 で通しなので、どちらの文書名で書かれていても同じ番号を探す。
`§3.2.2` のような深い番号は、`3.2.md` の中の見出しを探す。
行を跨いで文書名と番号が離れた参照は拾えない。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
SECTIONS_DIR = os.path.join(DOCS_DIR, "verification")

#: 目次を書く文書と、そこへ載せる章。
INDEXES = (("VERIFICATION_LOG.md", 2), ("VERIFICATION.md", 3))
BEGIN = "<!-- verification-index: begin -->"
END = "<!-- verification-index: end -->"

#: 節のファイルの1行目。
TITLE = re.compile(r"^# (\d+)\.(\d+)(?:〜(\d+)\.(\d+))?(.*)$")
#: 本体に残る章の見出し（§1 / §4 と、目次だけになった §2 / §3）。
CHAPTER = re.compile(r"^## (\d+)\. ")

#: 参照の拾い方。文書名（`TECH.md`、`.md` の無い大文字の名前 `HANDOVER §6`）が付けば
#: それを文脈にし、後ろへ区切りだけを挟んで続く番号（`§3.82 / §3.84`・
#: `（§1 / §3 / §4）`・`§3.3.9〜§3.3.13`）も同じ文書の参照と読む。
TOKEN = re.compile(r"\b([A-Za-z_]+)\.md\b|\b([A-Z][A-Z_]{2,})\s*(?=§)|§(\d+(?:\.\d+)*)")
CHAIN = re.compile(r"^[\s/・、，,（(のとや:：〜~-]{0,6}$")
SERIES = ("VERIFICATION", "VERIFICATION_LOG")
TEXT_EXT = (".md", ".py", ".bat", ".sh", ".yml", ".json", ".txt", ".ps1")


def load_sections():
    """番号 → (ファイル名の幹, 1行目, 本文の行) と、問題の一覧を返す。"""
    sections, problems = {}, []
    for name in sorted(os.listdir(SECTIONS_DIR)):
        if not name.endswith(".md"):
            continue
        stem = name[:-3]
        with open(os.path.join(SECTIONS_DIR, name), encoding="utf-8") as f:
            lines = f.read().split("\n")
        m = TITLE.match(lines[0])
        if not m:
            problems.append(f"verification/{name}: 1行目が `# 番号 見出し` の形でない")
            continue
        first = (int(m.group(1)), int(m.group(2)))
        if stem != f"{first[0]}.{first[1]}":
            problems.append(f"verification/{name}: ファイル名と1行目の番号が違う（{m.group(1)}.{m.group(2)}）")
            continue
        last = (int(m.group(3)), int(m.group(4))) if m.group(3) else first
        if last[0] != first[0] or last < first:
            problems.append(f"verification/{name}: 番号の範囲が読めない")
            continue
        for minor in range(first[1], last[1] + 1):
            key = (first[0], minor)
            if key in sections:
                problems.append(f"verification/{name}: §{key[0]}.{key[1]} が {sections[key][0]}.md と重なる")
            sections[key] = (stem, lines[0][2:].strip(), lines)
    return sections, problems


def index_lines(sections, chapter):
    seen, out = set(), []
    for key in sorted(sections):
        stem, title, _ = sections[key]
        if key[0] != chapter or stem in seen:
            continue
        seen.add(stem)
        # 角括弧はリンクの文の区切りと読まれるので逃がす。コードスパンの中は逃がすと `\` が出る。
        parts = title.split("`")
        parts[::2] = [p.replace("[", r"\[").replace("]", r"\]") for p in parts[::2]]
        title = "`".join(parts)
        out.append(f"- [{title}](verification/{stem}.md)")
    return out


def render(text, lines):
    head, sep, rest = text.partition(BEGIN)
    _, sep2, tail = rest.partition(END)
    if not sep or not sep2:
        return None
    return head + BEGIN + "\n" + "\n".join(lines) + "\n" + END + tail


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             check=True).stdout.decode("utf-8")
        names = [n for n in out.split("\n") if n]
    except (OSError, subprocess.CalledProcessError):
        names = []
        for d, subs, files in os.walk(ROOT):
            subs[:] = [s for s in subs if not s.startswith(".") and s != "__pycache__"]
            names += [os.path.relpath(os.path.join(d, f), ROOT) for f in files]
    # 生成の途中でまだ git に載っていない節のファイルも見る。
    names += [f"docs/verification/{n}" for n in os.listdir(SECTIONS_DIR)]
    return sorted({n.replace("\\", "/") for n in names if n.endswith(TEXT_EXT)})


def in_series(rel):
    return rel in ("docs/VERIFICATION.md", "docs/VERIFICATION_LOG.md") or rel.startswith("docs/verification/")


def resolve(number, sections, chapters):
    parts = [int(p) for p in number.split(".")]
    if len(parts) == 1:
        return parts[0] in chapters
    entry = sections.get((parts[0], parts[1]))
    if entry is None:
        return False
    if len(parts) == 2:
        return True
    pat = re.compile(r"^#+\s+" + re.escape(number) + r"(?![\d.])")
    return any(pat.match(ln) for ln in entry[2])


def check_refs(sections):
    chapters = set()
    for name, _ in INDEXES:
        with open(os.path.join(DOCS_DIR, name), encoding="utf-8") as f:
            chapters |= {int(m.group(1)) for m in map(CHAPTER.match, f) if m}
    bad = []
    for rel in tracked_files():
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            continue
        if "§" not in text:
            continue
        default = "VERIFICATION" if in_series(rel) else None
        for lineno, line in enumerate(text.split("\n"), 1):
            ctx, prev_end, other = default, 0, False
            for m in TOKEN.finditer(line):
                doc = m.group(1) or m.group(2)
                if doc:
                    ctx, prev_end = doc, m.end()
                    other = other or doc not in SERIES
                    continue
                explicit = CHAIN.match(line[prev_end:m.start()]) and ctx != default
                if not explicit:
                    ctx = default
                prev_end = m.end()
                if ctx not in SERIES or resolve(m.group(3), sections, chapters):
                    continue
                # 文書名の無い番号は、同じ行で先に別の文書を挙げていればそちらの続きと読む
                # （`TECH.md §3.3.10）と `defeat`（§3.3.11）`）。行き先が在れば上で通っている。
                if not explicit and other:
                    continue
                bad.append(f"{rel}:{lineno}: §{m.group(3)} の行き先が無い")
    return bad


def main(argv):
    check = "--check" in argv
    sections, problems = load_sections()
    stale = []
    for name, chapter in INDEXES:
        path = os.path.join(DOCS_DIR, name)
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        new = render(text, index_lines(sections, chapter))
        if new is None:
            problems.append(f"{name}: 目次の印（{BEGIN} / {END}）が無い")
            continue
        if new == text:
            continue
        if check:
            stale.append(name)
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            print(f"  wrote docs/{name}")
    if stale:
        problems.append("目次が古い: " + ", ".join(stale)
                        + "（python tools/build_verification.py で作り直す）")
    problems += check_refs(sections)
    count = len({v[0] for v in sections.values()})
    for p in problems:
        print("  " + p)
    print(f"  {count} 節 / 問題 {len(problems)} 件")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
