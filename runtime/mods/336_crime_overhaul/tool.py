# -*- coding: utf-8 -*-
r"""犯罪まわりの全面改修の設定画面。機能を左に並べ、選んだ機能の項目だけを右に出す。

    python runtime/mods/336_crime_overhaul/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 宣言の設定は 74 項目（機能11）で、1列に並べると探せない。
機能ごとに入切と項目がまとまっているので、左の一覧で機能を選び、右でその機能の項目だけを見る。
一覧には機能ごとの入切と、既定から変えた項目の数を出す。

| 画面 | 書く先 |
|---|---|
| 機能ごとの項目（入切を含む） | `settings/mod_settings.json`（`instantale_modloader.config` 経由。既定と同じ値は書かない） |

**一括設定だけ。** ワールド個別設定は出さない。MOD 本体は設定を `env.cfg.<名前>`
（モジュールの定数）からしか読まないので、出しても効かない。

項目の名前・型・既定値・上下限・説明は `mod.json` の `"settings"` が唯一の出所
（`modtool.decls`）。この画面が持つのは、どの項目をどの機能に入れるか（`GROUPS`）だけ。
場所・設定の読み書き・窓の記憶・配色は `tools/modtool.py`（どの道具も同じ）。
"""
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする。
# `IML_ROOT` が指す先に `tools/` が無いこと（オフラインの検査）と、
# 環境変数の無い直接起動の両方があるので、3つ上も候補に入れる。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)

import modtool  # noqa: E402

#: 機能の並び。`(鍵, 見出し, 入切の設定名か None, 項目名の接頭辞, 一言)`。
#: 並びは DOC.md の節の順。入切の無い機能（裁判の改修）は、中の入切がどれだけ立っているかで状態を出す。
GROUPS = (
    ("loot", "盗みの稼ぎ", "LOOT_ENABLED", ("LOOT_",),
     "自由行動の盗みの額を、その土地の依頼の報酬を目安に整える"),
    ("shop", "店が委縮して値を下げる", "INTIMIDATION_ENABLED", ("INTIMIDATION_",),
     "手配されている土地では、手配が重いほど買値が下がり売値が上がる"),
    ("statute", "時効", "STATUTE_ENABLED", ("STATUTE_",),
     "離れている土地の手配度が、時とともに平常へ戻る"),
    ("theft", "店で盗む・盗品", "THEFT_ENABLED", ("THEFT_", "STOLEN_"),
     "売買画面で店の品を右クリックして盗む。盗品は盗んだ店では売れず、よその店では値が下がる"),
    ("fence", "盗品買取商", "FENCE_ENABLED", ("FENCE_",),
     "裏の事務所で、盗品を買い取ってもらう"),
    ("underworld", "裏の仕事", "UNDERWORLD_ENABLED", ("UNDERWORLD_",),
     "裏の事務所の「裏の依頼掲示板」から、手配の重さに応じた裏の仕事を受ける"),
    ("guard", "衛兵の買収", "GUARD_BRIBE_ENABLED", ("GUARD_BRIBE_",),
     "衛兵に見つかったとき、金を握らせて見逃してもらう"),
    ("custody", "捕まったとき", "CUSTODY_DISBAND", ("CUSTODY_",),
     "捕まったら裁判の前に仲間と引き離し、パーティを解散させる"),
    ("trial", "裁判の改修", None, ("TRIAL_",),
     "釈明の知らせ・情状・弁護人・司法取引・袖の下・釈明しだいで無罪。手ごとに入切がある"),
    ("jailbreak", "脱獄", "JAILBREAK_ENABLED", ("JAILBREAK_",),
     "服役中に備えを積み、看守との戦闘で牢を破る"),
    ("cellmate", "同房の囚人", "CELLMATE_ENABLED", ("CELLMATE_",),
     "懲役の始まりに確率で囚人が同じ房に入る。話せて、年ごとに親しくなり、決行に加勢し、出所後はギルドで雇える"),
    ("rescue", "処刑場からの脱出", "RESCUE_ENABLED", ("RESCUE_",),
     "死刑の判決のとき、好感度の高い人物の手引きで処刑場からの脱出を試みる"),
)


def group_of(key):
    """その設定名が入る機能の鍵。どこにも入らなければ None。"""
    for gkey, _title, _enable, prefixes, _blurb in GROUPS:
        if any(key.startswith(prefix) for prefix in prefixes):
            return gkey
    return None


def group_keys(found):
    """機能ごとの設定名（`{鍵: [設定名, ...]}`）。入切を先頭に、残りは宣言の順。"""
    out = dict((g[0], []) for g in GROUPS)
    for key in found:
        gkey = group_of(key)
        if gkey is not None:
            out[gkey].append(key)
    for gkey, _title, enable, _prefixes, _blurb in GROUPS:
        if enable in out[gkey]:
            out[gkey].remove(enable)
            out[gkey].insert(0, enable)
    return out


def orphans(found):
    """どの機能にも入らない設定名（宣言の順）。画面の最後に「その他」として出す。"""
    return [key for key in found if group_of(key) is None]


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def group_state(group, keys, found, values):
    """一覧の「状態」の列。入切のある機能はその値、無い機能は中の入切の立ち方（ON / 一部 / OFF）。"""
    _gkey, _title, enable, _prefixes, _blurb = group
    if enable:
        return "ON" if _truthy(values.get(enable)) else "OFF"
    switches = [key for key in keys if found[key]["type"] == "bool"]
    on = sum(1 for key in switches if _truthy(values.get(key)))
    if not switches or on == len(switches):
        return "ON"
    return "OFF" if on == 0 else "一部"


def _same(decl, value):
    """画面の値が既定と同じか。打ちかけ（数として読めない）は違うと数える。"""
    default = decl["default"]
    if decl["type"] == "bool":
        return _truthy(value) == bool(default)
    if decl["type"] in ("int", "float"):
        try:
            return float(value) == float(default)
        except (TypeError, ValueError):
            return False
    return str(value) == str(default)


def changed_count(keys, found, values):
    """既定から変えた項目の数。"""
    return sum(1 for key in keys if not _same(found[key], values.get(key)))


def build_window(mod_dir=MOD_DIR):
    """窓を組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
    from tkinter import ttk

    win = modtool.SettingsWindow(mod_dir, geometry="1040x700", minsize=(860, 560),
                                 subtitle="左で機能を選ぶ。入切は右の先頭の項目", subtitle_wrap=980)
    found = win.found
    grouped = group_keys(found)
    extra = orphans(found)
    sections = [g for g in GROUPS if grouped[g[0]]]
    if extra:
        sections.append(("other", "その他", None, (), "どの機能にも入らない項目"))
        grouped["other"] = extra

    main = ttk.Frame(win.outer)
    main.pack(fill="both", expand=True)

    # ---- 左: 機能の一覧
    side = ttk.Frame(main)
    side.pack(side="left", fill="y", padx=(0, 12))
    tree = ttk.Treeview(side, columns=("state", "changed"), selectmode="browse",
                        height=len(sections))
    tree.heading("#0", text="機能")
    tree.heading("state", text="状態")
    tree.heading("changed", text="変更")
    tree.column("#0", width=210, stretch=False)
    tree.column("state", width=56, anchor="center", stretch=False)
    tree.column("changed", width=56, anchor="center", stretch=False)
    tree.pack(side="top", fill="y", expand=True)
    ttk.Label(side, style="Faint.TLabel", wraplength=280, justify="left",
              text="変更は既定から変えた項目の数").pack(side="top", anchor="w", pady=(6, 0))

    # ---- 右: 選んだ機能の項目
    pane = ttk.Frame(main)
    pane.pack(side="left", fill="both", expand=True)
    head = ttk.Frame(pane)
    head.pack(side="top", fill="x")
    head_title = ttk.Label(head, style="Group.TLabel")
    head_title.pack(side="left", anchor="w")
    head_blurb = ttk.Label(pane, style="Sub.TLabel", wraplength=680, justify="left")
    head_blurb.pack(side="top", anchor="w", pady=(2, 6))
    body = modtool.scrollable(pane)
    body.columnconfigure(0, weight=1)

    forms = {}
    frames = {}
    for section in sections:
        gkey = section[0]
        frame = ttk.Frame(body, padding=(0, 0, 6, 0))
        forms[gkey] = win.form(frame, grouped[gkey])
        frames[gkey] = frame
        tree.insert("", "end", iid=gkey, text=section[1])

    # 項目の説明は `Form` が自分の枠の幅で折り返すが、枠は選んだ機能のものしか置かれていない。
    # 機能を選び直した直後にも幅が合うよう、欄の幅から見出しの列を引いた幅でここでも折り返し直す。
    notes = dict((gkey, [w for w in frame.grid_slaves(column=1)
                         if isinstance(w, ttk.Label) and str(w.cget("style")) == "Faint.TLabel"])
                 for gkey, frame in frames.items())

    def rewrap(_event=None):
        gkey = shown_now["key"]
        if gkey is None:
            return
        frame = frames[gkey]
        head_blurb.configure(wraplength=max(240, body.winfo_width()))
        width = body.winfo_width() - frame.grid_bbox(0, 0)[2] - 24
        for label in notes[gkey]:
            label.configure(wraplength=max(240, width))

    body.bind("<Configure>", rewrap, add="+")

    def everything():
        values = {}
        for form in forms.values():
            values.update(form.get())
        return values

    def refresh_list(*_args):
        values = everything()
        for section in sections:
            gkey = section[0]
            keys = grouped[gkey]
            changed = changed_count(keys, found, values)
            tree.set(gkey, "state", group_state(section, keys, found, values))
            tree.set(gkey, "changed", str(changed) if changed else "")

    shown_now = {"key": None}

    def show(gkey):
        if shown_now["key"] == gkey:
            return
        if shown_now["key"] is not None:
            frames[shown_now["key"]].grid_forget()
        frames[gkey].grid(row=0, column=0, sticky="nsew")
        section = [s for s in sections if s[0] == gkey][0]
        head_title.configure(text=section[1])
        head_blurb.configure(text=section[4])
        shown_now["key"] = gkey
        frames[gkey].update_idletasks()
        rewrap()

    def on_select(_event=None):
        picked = tree.selection()
        if picked:
            show(picked[0])

    tree.bind("<<TreeviewSelect>>", on_select)

    def reset_group():
        gkey = shown_now["key"]
        if gkey is None:
            return
        forms[gkey].set(dict((k, found[k]["default"]) for k in grouped[gkey]))

    ttk.Button(head, text="この機能を既定に戻す", command=reset_group).pack(side="right")

    for form in forms.values():
        for var in form.vars.values():
            var.trace_add("write", refresh_list)

    def set_values(values):
        for gkey, form in forms.items():
            form.set(dict((k, values[k]) for k in grouped[gkey]))
        refresh_list()

    win.finish(everything, set_values, reset_label="すべて既定に戻す")
    if sections:
        tree.selection_set(sections[0][0])
        show(sections[0][0])
    return win.root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
