# -*- coding: utf-8 -*-
"""検証の記録（`docs/verification/`）の目次とリンクを作り、`§` 参照の行き先を確かめる。

    python tools/build_verification.py           目次とリンクを書き出す
    python tools/build_verification.py --check   目次・リンクのずれと行き先の無い参照があれば 1 で終わる（書かない）

§2（実機・実データの記録）と §3（未確認項目と確認手順）は1節1ファイルで
`docs/verification/<番号>.md` に置く。1冊に積むと際限なく伸びて、読むにも
直すにも全体を開くことになった（2026-10 に 800 KB）。ファイルの1行目が
`# 3.88 見出し` の形で、ファイル名はその番号。欠番をまとめた節だけは
`# 2.19〜2.22（欠番）` の形で、ファイル名は先頭の番号。

目次は `docs/VERIFICATION_LOG.md`（§2）と `docs/VERIFICATION.md`（§3）の
印の間に書く。手で書き換えても次の生成で消える。

検証の文書（その2冊と節のファイル）の中の `§2.x` / `§3.x` は、その節のファイルへの
リンク `[§3.98](3.98.md)` に置き換える。書くときは番号だけでよい。
`§3.2.2` のような深い番号は `3.2.md` へ繋ぐ（見出しのアンカーは表示する側ごとに
作り方が違うので付けない）。見出し・コード・既にあるリンクの中は触らない。

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

#: リンクを貼らない所（コードスパンと、既にあるリンク）。
PROTECT = re.compile(r"`[^`]*`|\[[^\]]*\]\([^)]*\)")
#: 節のファイルを指すリンクの行き先。
SECTION_LINK = re.compile(r"\]\((?:verification/)?(\d+\.\d+)\.md\)")
FENCE = re.compile(r"^\s*(```|~~~)")
#: 番号の直後の見出しの名前（`§3.98「最初の見られ方」`）。リンクにした後の `](…)` も越える。
NAMED = re.compile(r"(?:\]\([^)]*\))?\s*「([^」]+)」")


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


def load_chapters():
    chapters = set()
    for name, _ in INDEXES:
        with open(os.path.join(DOCS_DIR, name), encoding="utf-8") as f:
            chapters |= {int(m.group(1)) for m in map(CHAPTER.match, f) if m}
    return chapters


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


def series_files():
    """検証の文書の (docs からの相対パス, 節の幹か None)。"""
    out = [(name, None) for name, _ in INDEXES]
    out += [(f"verification/{n}", n[:-3]) for n in sorted(os.listdir(SECTIONS_DIR)) if n.endswith(".md")]
    return out


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


def has_heading(number, name, sections):
    """§3.98「最初の見られ方」の名前が、その節の見出しにあるか。§1「…」は本体の見出しを見る。"""
    if "." not in number:
        lines = []
        for doc, _ in INDEXES:
            with open(os.path.join(DOCS_DIR, doc), encoding="utf-8") as f:
                lines += f.read().split("\n")
    else:
        major, minor = number.split(".")[:2]
        entry = sections.get((int(major), int(minor)))
        lines = entry[2] if entry else []
    return any(ln.startswith("#") and name in ln for ln in lines)


def refs_in(line, default):
    """行の中の、検証の文書を指す § 参照を (match, 確か) で返す。

    確かでないのは、文書名の無い番号が、同じ行で先に別の文書を挙げた後に離れて続くもの
    （`TECH.md §3.3.10）と `defeat`（§3.3.11）`）。そちらの続きとも読めるので、
    行き先が無くても咎めず、リンクも貼らない。
    """
    ctx, prev_end, other = default, 0, False
    for m in TOKEN.finditer(line):
        doc = m.group(1) or m.group(2)
        if doc:
            ctx, prev_end = doc, m.end()
            other = other or doc not in SERIES
            continue
        explicit = bool(CHAIN.match(line[prev_end:m.start()])) and ctx != default
        if not explicit:
            ctx = default
        prev_end = m.end()
        if ctx in SERIES:
            yield m, explicit or not other


def link_line(line, here, prefix, sections, chapters):
    spans = [(m.start(), m.end()) for m in PROTECT.finditer(line)]
    out, last = [], 0
    for m, sure in refs_in(line, "VERIFICATION"):
        number = m.group(3)
        if not sure or "." not in number or any(a <= m.start() < b for a, b in spans):
            continue
        if not resolve(number, sections, chapters):
            continue
        major, minor = number.split(".")[:2]
        stem = sections[(int(major), int(minor))][0]
        if stem == here:
            continue
        out += [line[last:m.start()], f"[§{number}]({prefix}{stem}.md)"]
        last = m.end()
    return "".join(out) + line[last:]


def link_text(text, here, prefix, sections, chapters):
    lines, fenced, in_index = text.split("\n"), False, False
    for i, line in enumerate(lines):
        if line.startswith(BEGIN):
            in_index = True
        elif line.startswith(END):
            in_index = False
        elif FENCE.match(line):
            fenced = not fenced
        elif not (fenced or in_index or line.startswith("#")) and "§" in line:
            lines[i] = link_line(line, here, prefix, sections, chapters)
    return "\n".join(lines)


def check_refs(sections, chapters):
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
        series = in_series(rel)
        if "§" not in text and not series:
            continue
        default = "VERIFICATION" if series else None
        for lineno, line in enumerate(text.split("\n"), 1):
            for m, sure in refs_in(line, default):
                if not sure:
                    continue
                if not resolve(m.group(3), sections, chapters):
                    bad.append(f"{rel}:{lineno}: §{m.group(3)} の行き先が無い")
                    continue
                named = NAMED.match(line, m.end())
                if named and not has_heading(m.group(3), named.group(1), sections):
                    bad.append(f"{rel}:{lineno}: §{m.group(3)}「{named.group(1)}」の見出しが無い")
            if series:
                for m in SECTION_LINK.finditer(line):
                    major, minor = m.group(1).split(".")
                    if sections.get((int(major), int(minor)), ("",))[0] != m.group(1):
                        bad.append(f"{rel}:{lineno}: リンク先 {m.group(1)}.md が無い")
    return bad


def main(argv):
    check = "--check" in argv
    sections, problems = load_sections()
    chapters = load_chapters()
    indexes = dict(INDEXES)
    stale = []
    for rel, here in series_files():
        path = os.path.join(DOCS_DIR, rel)
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        new = text
        if rel in indexes:
            new = render(text, index_lines(sections, indexes[rel]))
            if new is None:
                problems.append(f"{rel}: 目次の印（{BEGIN} / {END}）が無い")
                continue
        prefix = "" if here else "verification/"
        new = link_text(new, here, prefix, sections, chapters)
        if new == text:
            continue
        if check:
            stale.append(rel)
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            print(f"  wrote docs/{rel}")
    if stale:
        shown = ", ".join(stale[:5]) + (f" ほか {len(stale) - 5} 件" if len(stale) > 5 else "")
        problems.append(f"目次かリンクが古い: {shown}（python tools/build_verification.py で作り直す）")
    problems += check_refs(sections, chapters)
    count = len({v[0] for v in sections.values()})
    for p in problems:
        print("  " + p)
    print(f"  {count} 節 / 問題 {len(problems)} 件")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
