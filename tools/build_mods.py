# -*- coding: utf-8 -*-
"""同梱 MOD の説明（`docs/MODS.md`）を各 MOD の `DOC.md` から綴じる。

    python tools/build_mods.py           docs/MODS.md を書き出す
    python tools/build_mods.py --check   ずれていたら 1 で終わる（書かない）

1本ずつの説明はその MOD のフォルダの `DOC.md` に置く。コードの隣に在れば、
MOD を1本足すのに 2000 行の正しい位置を探さずに済み、番号を振り直すときも
文書がついてくる。`docs/MODS.md` はそれを綴じ直したもので、配布物も README も
ソースのコメントも今までどおりここを指す（手で書き換えても次の生成で消える）。

並びは下の `BANDS`。`1xx` はフォルダ名順ではなく読む順に手で並べてあるので、
規則では出せない。`load_order.json` と突き合わせるので、MOD を足して
ここへ書き忘れれば `--check` が止める。

計測（2xx）だけは節ではなく表の1行になる。見出しに説明を続けず、
本文の1行がそのままセルになる。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

import mods_meta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS_DIR = os.path.join(ROOT, "runtime", "mods")
DOCS_DIR = os.path.join(ROOT, "docs")
OUT_NAME = "MODS.md"
DOC_NAME = "DOC.md"

#: `DOC.md` の1行目。`tools/list_mods.py` の `HEADING` と同じ形（見出しの深さだけ違う）。
#: 説明が続かないのは計測（2xx）だけ。
HEADING = re.compile(r"^#\s+`([^`]+)`(?:\s*[:：]\s*(.+?))?\s*$")

PREAMBLE = r"""# MODS: 同梱している MOD

何がどこまで直るか・何が増えるかの一覧。
1行ずつ見渡したいときは [MODLIST.md](MODLIST.md)。
ローダと GUI の使い方は [README.md](README.md)。

**この文書は `tools/build_mods.py` が各 MOD の `DOC.md` を綴じたもの。**
手で書き換えても次の生成で消える。
直す先はその MOD のフォルダの `DOC.md`。

フォルダ名の先頭の番号が種類と適用順を表す。

| 番号帯 | 種類 | 中身 |
| --- | --- | --- |
| `000`-`0xx` | 調査・記録 | ゲームは変えない。構造の書き出しとクラッシュ記録 |
| `100`-`1xx` | 修正 | ゲームのバグ・不便の修正 |
| `200`-`2xx` | 計測 | ゲームは変えない。原因を測るための道具。デバッグモードのときだけ動く |
| `300`-`3xx` | 追加 | ゲームに無かった遊びの追加 |
| `400`-`4xx` | 提供 | 提供を受けて取り込んだ MOD。この帯だけは中身ではなく出どころを表す |

提供を受けた MOD は `4xx` だけではない。
この帯が出来る前に取り込んだものは種別どおりの帯に入っている
（番号を振り直すと、遊んでいる人の `state\` と設定が行方不明になるため動かしていない）。
どの節にも、提供があればその名乗りを節の頭に置いてある。
一覧は [MODLIST.md の「提供を受けた MOD」](MODLIST.md#提供を受けた-mod)、
権利の所在は [NOTICE](../NOTICE)。

各項の「設定」は GUI の `設定` 列から変えられるもの。
変え方は [README.md の「設定の変え方」](README.md#設定の変え方)。"""

#: 提供の帯の見出し。`render` が導入を差し込む目印にする。
CONTRIB_HEAD = "## 提供（4xx）"

#: 提供の帯の導入。番号帯の意味が他と違う（中身ではなく出どころ）ので、
#: 節の頭で一度だけ言う。
#: **末尾の名乗りは `mod.json` の `author` から組む**（`contrib_credit`）。
#: 以前ここは「この節の3本は MoririnJP 様の提供」という固定の文字列で、
#: 4本目（`404_party_talk`）が入った時点で嘘になっていた。
CONTRIB_INTRO = r"""ユーザから提供を受けて取り込んだ MOD。
`4xx` の番号は出どころを表すもので、中身の種別は他の帯と同じく
`mod.json` の `"kind"` が名乗る（提供された計測 MOD は 2xx に入る。`223_` がそれ）。
取り込みの際にこちらの環境へ合わせた調整を行っており、経緯は各 MOD の説明にある。"""

#: 計測の帯だけは節を持たないので、導入をここに置く。
PROBE_INTRO = r"""いずれもゲームは変更しない。
原因を測って `out\` にログを残すだけの道具。

遊ぶだけなら要らないので、既定では動かない。
`mod.json` に `"debug": true` と書いてあり、
デバッグモード（`実行` メニュー）を入れているあいだだけ一覧に出て読み込まれる。
切っているあいだは同梱されているだけで、何も起きない。"""

#: 綴じる順。帯ごとに、その帯に入る MOD をフォルダ名で並べる。
#: 計測（2xx）は節を持たないので `None`（表を組む）。
BANDS = (
    ("core", "## 調査・記録（0xx）", (
        "000_recon",
        "001_crash_recorder",
    )),
    ("fix", "## 修正（1xx）", (
        "126_ui_title_version",
        "100_fix_kivy_shutdown",
        "101_fix_npc_employ_price",
        "102_fix_prompt_dedup",
        "103_fix_eventlog_trim",
        "104_balance_area_bgm",
        "105_fix_schema_compact",
        "106_fix_battle_bgm_restore",
        "107_fix_battle_flag_stuck",
        "108_fix_shop_inventory_overflow",
        "109_fix_item_detail_autosize",
        "110_fix_character_name_path",
        "120_fix_npc_name_collision",
        "123_fix_new_character_level",
        "125_balance_charisma_impression",
        "128_item_image_variety",
        "129_balance_item_price",
        "130_currency_unit",
        "131_sharp_portrait",
        "111_llm_prompt_replace",
        "117_message_text_integrity",
        "127_llm_response_speed",
        "112_ui_text_spacing",
        "113_ui_text_expand",
        "114_ui_input_focus",
        "115_ui_item_list_fit",
        "124_ui_craft_window_fit",
        "116_ui_party_expand",
        "121_ui_character_sheet",
        "118_batch_message_render",
        "122_ui_conversation_log",
        "119_fix_crime_attribution",
    )),
    ("probe", "## 計測（2xx）", None),
    ("feature", "## 追加（3xx）", (
        "300_event_facility_arrival",
        "301_quest_from_conversation",
        "302_leave_party_in_conversation",
        "303_quest_end_party_to_guild",
        "304_quest_end_keep_party",
        "306_party_train_exp",
        "307_area_move_dungeon",
        "308_battle_damage_display",
        "309_office_pardon",
        "311_npc_profile_memory",
        "312_shop_restock",
        "313_event_ability_check",
        "314_area_move_custom",
        "315_vacation_custom",
        "316_bounty_hunter",
        "317_reputation",
        "318_area_difficulty_growth",
        "319_battle_tactics",
        "320_guild_adventurer_recruit",
        "321_area_chronicle",
        "322_battle_bgm",
        "323_npc_carryover",
    )),
    ("feature", CONTRIB_HEAD, (
        "401_battle_character_context",
        "402_party_inventory_transfer",
        "403_npc_social_memory",
        "404_party_talk",
    )),
)


def cell(s: str) -> str:
    """表の中で `|` が列の区切りに化けないようにする。"""
    return s.replace("|", r"\|")


def band_credit(folders) -> str:
    """その節の名乗り。`mod.json` の `author` から**数えて**組む。

    帯ごとに出すのは、**提供が `4xx` の帯だけに居るわけではない**ため。
    `4xx` は出どころを表す帯だが、それが出来る前に取り込んだものは種別どおりの帯に
    入っていて（`117_` / `118_` / `119_` は修正、`311_` は追加、`223_` は計測）、
    「提供」節を読んだだけでは見つからない。

    節の全部が提供なら本数で、一部なら名指しで言う。
    提供者ごとに1文に分ける（誰が何を出したかを混ぜない）。
    提供が1本も無い節は空文字（そのときは何も足さない）。
    """
    folders = list(folders)
    rows = mods_meta.contributed(folders)
    if not rows:
        return ""
    groups = []
    for folder, names, is_shared in rows:
        key = tuple(names)
        for got in groups:
            if got[0] == key:
                got[1].append((folder, is_shared))
                break
        else:
            groups.append((key, [(folder, is_shared)]))

    lines = []
    for names, items in groups:
        if len(items) == len(folders):
            who = "この節の{}本".format(len(items))
        else:
            # 名指しのときは末尾がバッククォートなので、後ろにも空白を置く
            # （docs の他の箇所と同じ、欧文・コードは前後を空ける書き方）。
            who = ("この節の "
                   + "・".join("`%s`" % f for f, _s in items) + " ")
        shared = [f for f, is_shared in items if is_shared]
        if shared and len(shared) == len(items):
            # 全部が共同なら、名指しを繰り返さずに1文へ畳む
            # （1本しかない節で「`311_` は提供。うち `311_` は共同」になる）。
            line = "{}は {}の提供（こちらとの共同）。".format(
                who, mods_meta.credit(names))
        else:
            line = "{}は {}の提供。".format(who, mods_meta.credit(names))
            if shared:
                line += "うち {} はこちらとの共同。".format(
                    "・".join("`%s`" % f for f in shared))
        lines.append(line)
    return " ".join(lines)


def probe_folders() -> list:
    """計測の帯に入る MOD。この帯だけ `BANDS` が並びを持たない（表を組む）。"""
    return sorted(f for f in load_order() if kind_of(f) == "probe")


def mod_json(folder: str) -> dict:
    path = os.path.join(MODS_DIR, folder, "mod.json")
    return json.load(io.open(path, encoding="utf-8"))


def promote(lines: list) -> list:
    """`#` -> `###`、`##` -> `####`。コードフェンスの中は触らない。"""
    out, fence = [], False
    for l in lines:
        if l.startswith("```"):
            fence = not fence
            out.append(l)
            continue
        out.append("##" + l if not fence and re.match(r"^#{1,4} ", l) else l)
    return out


def doc(folder: str) -> list:
    """その MOD の `DOC.md` を読む。名乗りがフォルダ名と合うかもここで見る。"""
    path = os.path.join(MODS_DIR, folder, DOC_NAME)
    if not os.path.isfile(path):
        raise SystemExit("%s が無い。1本ずつの説明はここに置く。" % path)
    lines = io.open(path, encoding="utf-8").read().split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    m = HEADING.match(lines[0]) if lines else None
    if not m:
        raise SystemExit("%s の1行目が「# `フォルダ名`」で始まっていない。" % path)
    if m.group(1) != folder:
        raise SystemExit("%s の見出しが %s を名乗っている。" % (path, m.group(1)))
    return lines


def section(folder: str) -> list:
    """その MOD の `DOC.md` を、綴じる形（`###` 始まり）で返す。"""
    lines = doc(folder)
    if not HEADING.match(lines[0]).group(2):
        raise SystemExit("%s は見出しに説明が続いていない（計測の帯だけの書き方）。"
                         % os.path.join(MODS_DIR, folder, DOC_NAME))
    return promote(lines)


def probe_rows() -> list:
    """計測の表。並びはフォルダ名順（MODLIST.md と同じ）。"""
    rows = ["| フォルダ | 何を測るか |", "| --- | --- |"]
    for f in probe_folders():
        body = [l for l in doc(f)[1:] if l != ""]
        if len(body) != 1:
            raise SystemExit("%s は表の1セルになるので説明は1行で書く（今 %d行）。"
                             % (os.path.join(MODS_DIR, f, DOC_NAME), len(body)))
        rows.append("| `%s` | %s |" % (f, cell(body[0])))
    return rows


def load_order() -> list:
    path = os.path.join(MODS_DIR, "load_order.json")
    return json.load(io.open(path, encoding="utf-8")).get("order") or []


_KIND = {}


def kind_of(folder: str) -> str:
    if folder not in _KIND:
        _KIND[folder] = (mod_json(folder).get("kind") or "").lower()
    return _KIND[folder]


def check_order() -> None:
    """並びと `load_order.json` を突き合わせる。書き忘れ・余りをここで止める。"""
    listed = []
    for key, _head, folders in BANDS:
        for f in folders or ():
            listed.append(f)
            if not os.path.isdir(os.path.join(MODS_DIR, f)):
                raise SystemExit("実体が無い: %s" % f)
            if kind_of(f) != key:
                raise SystemExit("%s は %s の帯に居るが kind は %s。"
                                 % (f, key, kind_of(f)))
    dup = [f for f in set(listed) if listed.count(f) > 1]
    if dup:
        raise SystemExit("並びに重複: %s" % " ".join(sorted(dup)))

    order = load_order()
    probes = [f for f in order if kind_of(f) == "probe"]
    missing = [f for f in order if f not in listed and f not in probes]
    if missing:
        raise SystemExit("並びに書き忘れ: %s\n"
                         "  tools/build_mods.py の BANDS へ足すこと。"
                         % " ".join(missing))
    extra = [f for f in listed if f not in order]
    if extra:
        raise SystemExit("load_order.json に無いのに並びに居る: %s"
                         % " ".join(extra))


def render() -> str:
    check_order()
    out = PREAMBLE.split("\n")
    for n, (key, head, folders) in enumerate(BANDS):
        if n:
            out += ["", "---"]
        out += ["", head]
        if head == CONTRIB_HEAD:
            out += [""] + CONTRIB_INTRO.split("\n")
        elif key == "probe":
            out += [""] + PROBE_INTRO.split("\n")
        # 提供の名乗りは**どの節にも**出す（`band_credit` の説明を参照）。
        credit = band_credit(probe_folders() if key == "probe" else folders)
        if credit:
            out += ["", credit]
        if key == "probe":
            out += [""] + probe_rows()
            continue
        for f in folders:
            out += [""] + section(f)
    return "\n".join(out) + "\n"


def main(argv) -> int:
    path = os.path.join(DOCS_DIR, OUT_NAME)
    body = render()
    if "--check" in argv:
        old = io.open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
        if old != body:
            print("%s が古い。`python tools/build_mods.py` で作り直すこと。" % OUT_NAME)
            return 1
        print("%s は最新。" % OUT_NAME)
        return 0
    io.open(path, "w", encoding="utf-8", newline="\n").write(body)
    print("%s を書き出した。" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
