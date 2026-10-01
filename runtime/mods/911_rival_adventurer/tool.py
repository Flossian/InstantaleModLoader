# -*- coding: utf-8 -*-
r"""ライバル冒険者の設定画面。設定を役割ごとのタブに分け、ライバルの様子も見せる。

    python runtime/mods/911_rival_adventurer/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 宣言の設定は 41 項目で、半分以上が文面。1行の入力欄に長い日本語を並べると読めず、
態度の4段の文が8項目にばらける（34件は既存の設定画面では使いづらい）。

| タブ | 中身 | 書く先 |
|---|---|---|
| 登場 / 張り合い / 態度 / 声かけ / 文面 | 宣言の設定。文は複数行の欄に、使える変数と見本を添える | `settings/mod_settings.json`（`modtool.save_settings`） |
| 様子 | `state\rival_adventurer\` の控えを読んで並べる。控えを消すボタン | 読むだけ（消すときだけファイルを消す） |

**一括設定だけ。** MOD 本体がモジュールのグローバルしか読まないので、ワールド個別のタブは出さない。
項目の名前・型・既定値・上下限は `mod.json` の `"settings"` が唯一の出所（`modtool.decls`）。
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

#: 控えのフォルダ（本体の `STATE_DIRNAME`）。
STATE_DIRNAME = "rival_adventurer"

#: タブごとの数の項目（宣言の入力欄をそのまま使う）と文の項目。
TABS = (
    ("登場", ("RIVAL_AFTER_QUESTS", "RIVAL_CHANCE_PERCENT", "INTRO_USE_LLM"),
     ("INTRO_FALLBACK", "REASON_FALLBACK"),
     "依頼をいくつか片付けると、片付けるたびにライバルの抽選をする。\n"
     "当たったら、登場の語りと張り合う理由をLLMに1回書かせる（下の文型はLLMを使わないとき）。"),
    ("張り合い", ("FAILURE_ENABLED", "SUCCESS_BASE_PERCENT", "SUCCESS_SLOPE_PERCENT",
                  "DUE_DAYS_MIN", "DUE_DAYS_MAX", "COOLDOWN_DAYS", "INJURY_DAYS",
                  "REACH_LEVELS", "LEVEL_STEP", "LEVEL_MAX", "RUMOR_DAYS"), (),
     "ライバルは掲示板の依頼に狙いを付け、期限が来たら挑む。\n"
     "成功率はLvと難易度の差で決まり、10〜95%に収める。"),
    ("態度", ("WINS_PER_STANCE", "CLEANUP_SOFTENS", "AFFINITY_PER_STANCE",
              "TALK_AFFINITY_CEILING"), (),
     "態度は「見下す→一目置く→認める→慕う」の4段で、上がるだけで下がらない。\n"
     "上がる機会は、狙われた依頼を先に片付けたとき・ライバルがしくじった依頼を片付けたとき・"
     "ライバルの好感度が上がったとき（同行してのクリアなど）。"),
    ("声かけ", ("APPROACH_CHANCE_PERCENT",),
     ("APPROACH_INTRO", "APPROACH_TAKEN", "APPROACH_LOST", "APPROACH_CLEANUP"),
     "ライバルの居る街のギルドに入ったとき、話の種があればライバルの方から声をかけてくる。\n"
     "話の種は、初対面・先を越した・先を越された・尻拭いされたの4つで、1つにつき1回使う。\n"
     "下の文は、声をかけてきた用件として第一声の頼み文に入る。"),
    ("文面", (), ("TARGET_SUFFIX", "TARGET_ANNOUNCE", "TAKEN_ANNOUNCE", "FAILED_ANNOUNCE",
                  "WIN_ANNOUNCE", "CLEANUP_ANNOUNCE", "RIVAL_NOTE", "RIVAL_AIM_NOTE",
                  "RIVAL_AWAY_NOTE", "RUMOR_NOTE"),
     "画面に出す文と、会話の文脈に足す文（画面には出ない）。\n"
     "{}の中は差し込まれる値。"),
)

#: 態度の表。`(段の名前, 会話に渡す文の設定, 段が上がったときの一文の設定)`。
STANCES = (("0 見下す", "STANCE_NOTE_0", None),
           ("1 一目置く", "STANCE_NOTE_1", "STANCE_ANNOUNCE_1"),
           ("2 認める", "STANCE_NOTE_2", "STANCE_ANNOUNCE_2"),
           ("3 慕う", "STANCE_NOTE_3", "STANCE_ANNOUNCE_3"))

#: 見本に入れる仮の値。
SAMPLE = {"name": "リオ", "player": "主人公", "title": "古城の亡霊", "days": 12,
          "known": "「秩序の剣」の二つ名", "reason": "噂ばかり先行する新顔に格の違いを見せつけたい",
          "rival_wins": 2, "player_wins": 1}

STANCE_NAMES = ("見下す", "一目置く", "認める", "慕う")
OUTCOME_WORDS = {"rival": "ライバルが先に片付けた", "player": "プレイヤーが先に片付けた",
                 "failed": "ライバルがしくじった", "cleanup": "しくじった後をプレイヤーが片付けた"}
TOPIC_WORDS = {"intro": "初対面", "taken": "先を越した", "lost": "先を越された",
               "cleanup": "尻拭いされた"}


def text_keys():
    """複数行の欄で扱う設定名（タブの文と態度の表）。"""
    keys = [key for _name, _numbers, texts, _blurb in TABS for key in texts]
    for _label, note, announce in STANCES:
        keys.append(note)
        if announce:
            keys.append(announce)
    return keys


def number_keys():
    return [key for _name, numbers, _texts, _blurb in TABS for key in numbers]


def sample_of(template):
    """文型に仮の値を入れた見本。鍵が足りなくても落とさない。"""
    class _Missing(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    try:
        return str(template).format_map(_Missing(SAMPLE))
    except (ValueError, IndexError):
        return "（{ } の対応が崩れています）"


def one_line(text):
    """欄の中の改行を落とす（文型は1行の文。改行はそのまま頼み文に入ってしまう）。"""
    return "".join(str(text).splitlines()).strip()


# ---------------------------------------------------------------- 様子
def state_files(state_dir):
    """控えのファイル `[(表示名, パス)]`。新しく書かれた順。"""
    folder = os.path.join(state_dir, STATE_DIRNAME)
    try:
        names = [name for name in os.listdir(folder) if name.endswith(".json")]
    except OSError:
        return []
    stamped = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            stamped.append((os.path.getmtime(path), path))
        except OSError:
            continue                    # 一覧を取った後に消えた（ゲームや別の窓が消した）
    stamped.sort(reverse=True)
    return [(os.path.basename(path)[:-5], path) for _mtime, path in stamped]


def describe(bucket):
    """控え1つを読む用の文にする。"""
    if not isinstance(bucket, dict):
        return "（読めませんでした）"
    rival = bucket.get("rival")
    if not isinstance(rival, dict):
        return "ライバルはまだ現れていない。\n依頼を片付けるたびに抽選する（設定の「登場」）。"
    lines = []
    stance = rival.get("stance") if isinstance(rival.get("stance"), int) else 0
    stance = max(0, min(len(STANCE_NAMES) - 1, stance))
    lines.append("ライバル: {}（id {}）  {}日目に現れた".format(
        rival.get("name"), rival.get("id"), rival.get("chosen_day")))
    lines.append("態度: {} {}".format(stance, STANCE_NAMES[stance]))
    lines.append("張り合う理由: {}".format(rival.get("reason") or "（まだ書かれていない）"))
    lines.append("登場の語り: {}{}".format(
        rival.get("intro") or "（まだ書かれていない）",
        "" if rival.get("intro_told") else "  ※まだ画面に出していない"))
    score = bucket.get("score") or {}
    lines.append("勝敗: ライバル {} / プレイヤー {} / しくじり {}".format(
        score.get("rival", 0), score.get("player", 0), score.get("failed", 0)))
    target = bucket.get("target")
    if isinstance(target, dict):
        lines.append("いまの狙い: 「{}」（依頼 {}・土地 {}）{}日目に狙い、{}日目に挑む".format(
            target.get("title"), target.get("quest"), target.get("area"),
            target.get("set_day"), target.get("due_day")))
    else:
        lines.append("いまの狙い: なし（次に狙ってよい日: {}）".format(bucket.get("next_day")))
    if bucket.get("away_until") is not None:
        lines.append("休み: {}日目まで".format(bucket.get("away_until")))
    failure = bucket.get("failure")
    if isinstance(failure, dict):
        lines.append("最後のしくじり: 「{}」（{}日目）{}".format(
            failure.get("title"), failure.get("day"),
            "  尻拭い済み" if failure.get("cleaned") else ""))
    history = bucket.get("history") or []
    lines.append("")
    lines.append("最近の取り合い:")
    lines.extend("  {}日目 「{}」 {}".format(row.get("day"), row.get("title"),
                                            OUTCOME_WORDS.get(row.get("outcome"), row.get("outcome")))
                 for row in history if isinstance(row, dict))
    if not history:
        lines.append("  （まだ無い）")
    topics = [row for row in bucket.get("topics") or [] if isinstance(row, dict)]
    lines.append("")
    lines.append("ギルドで声をかけるときの話の種: {}".format(
        "、".join("{}{}".format(TOPIC_WORDS.get(row.get("kind"), row.get("kind")),
                               "（「{}」）".format(row.get("title")) if row.get("title") else "")
                 for row in topics) or "（無し）"))
    taken = bucket.get("taken") or {}
    lines.append("")
    lines.append("ライバルが片付けて掲示板から隠している依頼: {}件".format(len(taken)))
    lines.extend("  依頼 {} 「{}」（土地 {}・{}日目）".format(qid, row.get("title"), row.get("area"),
                                                         row.get("day"))
                 for qid, row in taken.items() if isinstance(row, dict))
    return "\n".join(lines)


# ---------------------------------------------------------------- 窓
def build_window(mod_dir=MOD_DIR):
    """窓を組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
    import time
    import tkinter as tk
    from tkinter import messagebox, ttk

    info = modtool.manifest(mod_dir)
    title = (info.get("name") or {}).get("ja") or modtool.mod_name(mod_dir)
    root_dir, state_dir, _game_dir = modtool.locate(mod_dir)
    runtime = os.path.join(root_dir, "runtime")
    config = modtool.config_module(root_dir, mod_dir)
    found = modtool.decls(mod_dir, root_dir)
    current = modtool.load_settings(root_dir, mod_dir)
    handled = set(text_keys()) | set(number_keys())
    missing = [key for key in handled if key not in found]
    unplaced = [key for key in found if key not in handled]

    root = tk.Tk()
    root.title(title)
    root.minsize(860, 600)
    modtool.restore_window(root_dir, mod_dir, root, "1000x760")
    theme = modtool.setup_theme(root, root_dir)
    # 文の欄は ttk ではないので、設定画面の色を自分で渡す（借りられなければ素の色）。
    palette = getattr(theme, "PALETTE", None) if theme is not None else None
    text_style = {} if not isinstance(palette, dict) else {
        "background": palette.get("surface"), "foreground": palette.get("text"),
        "insertbackground": palette.get("text"), "relief": "flat", "bd": 0,
        "highlightthickness": 1, "highlightbackground": palette.get("control_edge"),
        "highlightcolor": palette.get("control_edge"), "padx": 6, "pady": 4}
    text_style = dict((k, v) for k, v in text_style.items() if v is not None)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel", wraplength=940, justify="left",
              text="どの世界でも効く（settings\\mod_settings.json）。次の注入から効く。\n"
                   "様子のタブはライバルの控え（state\\rival_adventurer\\）を読むだけ。"
              ).pack(anchor="w", pady=(0, 8))

    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)

    forms = []
    texts = {}

    def text_field(parent, row, key, label=None, height=2):
        """複数行の欄1つと、変数の説明・見本の行。2行ぶん使う。"""
        decl = found.get(key)
        ttk.Label(parent, text=label or (decl["label"]["ja"] if decl else key)).grid(
            row=row, column=0, sticky="nw", padx=(0, 12), pady=(8, 0))
        if decl is None:
            ttk.Label(parent, text="（宣言がありません）", style="Faint.TLabel").grid(
                row=row, column=1, sticky="w", pady=(8, 0))
            return
        box = tk.Text(parent, height=height, wrap="char", undo=True, **text_style)
        box.grid(row=row, column=1, sticky="ew", pady=(8, 0))
        sample = ttk.Label(parent, style="Faint.TLabel", wraplength=700, justify="left")
        sample.grid(row=row + 1, column=1, sticky="w")
        note = decl["note"]["ja"]

        def refresh(_event=None):
            box.edit_modified(False)
            sample.configure(text="{}\n見本: {}".format(note, sample_of(one_line(box.get("1.0", "end")))))

        box.bind("<<Modified>>", refresh)
        texts[key] = (box, refresh)

    for name, numbers, text_list, blurb in TABS:
        page = ttk.Frame(notebook, padding=(4, 8, 4, 4))
        notebook.add(page, text=name)
        ttk.Label(page, text=blurb, style="Sub.TLabel", wraplength=920, justify="left").pack(
            anchor="w", pady=(0, 6))
        body = modtool._scrollable(page)
        body.columnconfigure(1, weight=1)
        row = 0
        if numbers:
            # 文の欄と同じ格子に置く（別の枠にすると左の見出しの幅が揃わない）。
            # `_Form` は 0 行目から1項目2行ずつ使う。
            present = dict((key, found[key]) for key in numbers if key in found)
            forms.append(modtool._Form(body, present,
                                       lambda k: "既定: " + modtool.shown(found[k]["default"])))
            row += 2 * len(present)
        if name == "態度":
            ttk.Label(body, text="段ごとの文", style="Group.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(12, 0))
            row += 1
            for label, note_key, announce_key in STANCES:
                ttk.Label(body, text=label, style="Group.TLabel").grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
                row += 1
                text_field(body, row, note_key, "会話に渡す文", height=2)
                row += 2
                if announce_key:
                    text_field(body, row, announce_key, "上がったときの一文", height=1)
                    row += 2
        for key in text_list:
            text_field(body, row, key, height=3 if "NOTE" in key or "FALLBACK" in key else 2)
            row += 2

    # ---- 様子
    page = ttk.Frame(notebook, padding=(4, 8, 4, 4))
    notebook.add(page, text="様子")
    ttk.Label(page, style="Sub.TLabel", wraplength=920, justify="left",
              text="ライバルの控え（世界×主人公ごとに1つ）。\n"
                   "ゲームを起動している間は、MODが覚えている内容が次に書かれるまでファイルは古いままのことがある。\n"
                   "「控えを消す」はゲームを閉じてから使う（起動中に消すと、次の保存で書き戻される）。"
              ).pack(anchor="w", pady=(0, 6))
    picker_row = ttk.Frame(page)
    picker_row.pack(fill="x")
    ttk.Label(picker_row, text="控え").pack(side="left", padx=(0, 8))
    picked = tk.StringVar()
    picker = ttk.Combobox(picker_row, textvariable=picked, state="readonly", width=60)
    picker.pack(side="left", fill="x", expand=True)
    view = tk.Text(page, wrap="char", height=20, **text_style)
    view.pack(fill="both", expand=True, pady=(8, 0))
    status_files = {"rows": []}

    def show_state(*_args):
        rows = dict(status_files["rows"])
        path = rows.get(picked.get())
        view.configure(state="normal")
        view.delete("1.0", "end")
        if path is None:
            view.insert("1.0", "控えがまだありません（ライバルが現れる抽選をした世界から作られる）。")
        else:
            view.insert("1.0", describe(modtool.read_json(path)) + "\n\n" + path)
        view.configure(state="disabled")

    def reload_state():
        status_files["rows"] = state_files(state_dir)
        names = [name for name, _path in status_files["rows"]]
        picker.configure(values=names)
        if picked.get() not in names:
            picked.set(names[0] if names else "")
        show_state()

    def delete_state():
        path = dict(status_files["rows"]).get(picked.get())
        if path is None:
            return
        if not messagebox.askyesno(
                "控えを消す", "「{}」の控えを消します。\nライバル・勝敗・隠している依頼がすべて最初に戻ります。\n"
                "ゲームは閉じていますか？".format(picked.get()), parent=root):
            return
        try:
            os.remove(path)
        except OSError as exc:
            messagebox.showerror("消せませんでした", "{}\n{}".format(path, exc), parent=root)
        reload_state()

    picker.bind("<<ComboboxSelected>>", show_state)
    buttons = ttk.Frame(page)
    buttons.pack(fill="x", pady=(6, 0))
    ttk.Button(buttons, text="読み直す", command=reload_state).pack(side="left")
    ttk.Button(buttons, text="控えを消す", command=delete_state).pack(side="right")
    reload_state()

    # ---- 値の出し入れ
    def as_shown(values):
        return dict((k, v if isinstance(v, bool) else str(v)) for k, v in values.items())

    def everything():
        values = {}
        for form in forms:
            values.update(form.get())
        for key, (box, _refresh) in texts.items():
            values[key] = one_line(box.get("1.0", "end"))
        for key in unplaced:
            values[key] = current.get(key, found[key]["default"])
        return values

    saved = {"values": {}}
    # ファイルに在る値。「既定に戻す」の後の比べ先で、保存のたびに差し替える
    # （起動時の値のままだと、保存した後の「既定に戻す」を変更なしと読む）。
    last = {"values": current}

    def load_values(values):
        for form in forms:
            form.set(dict((k, values[k]) for k in form.vars))
        for key, (box, refresh) in texts.items():
            box.delete("1.0", "end")
            box.insert("1.0", str(values.get(key, "")))
            refresh()
        saved["values"] = as_shown(dict((k, values[k]) for k in found))

    load_values(current)

    def dirty():
        return as_shown(dict((k, v) for k, v in everything().items() if k in found)) != saved["values"]

    status = ttk.Label(footer, style="Faint.TLabel", text="次の注入から効きます"
                       + ("  ※ 宣言が無い項目: " + ", ".join(missing) if missing else ""))
    status.pack(side="left")

    def save():
        values, bad = modtool.coerce_all(mod_dir, everything(), root_dir)
        if bad:
            messagebox.showerror("設定を確かめてください", bad, parent=root)
            return False
        try:
            modtool.save_settings(root_dir, mod_dir, values, strict=True)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", "{}\n{}: {}".format(
                config.store_path(runtime), type(exc).__name__, exc), parent=root)
            return False
        saved["values"] = as_shown(values)
        last["values"] = values
        status.configure(text="保存しました {}  {}".format(
            time.strftime("%H:%M:%S"), config.store_path(runtime)))
        return True

    def reset():
        load_values(dict((k, d["default"]) for k, d in found.items()))
        # 「既定に戻す」は未保存の変更のまま。比べ先は `dirty` と同じく宣言の在る項目だけ。
        saved["values"] = as_shown(dict((k, last["values"][k]) for k in found
                                        if k in last["values"]))

    def close():
        modtool.save_window(root_dir, mod_dir, root)
        if dirty():
            answer = messagebox.askyesnocancel("未保存の変更", "変更を保存してから閉じますか？", parent=root)
            if answer is None:
                return
            if answer and not save():
                return
        root.destroy()

    ttk.Button(footer, text="閉じる", command=close).pack(side="right")
    ttk.Button(footer, text="保存", style="Accent.TButton", command=save).pack(side="right", padx=(0, 6))
    ttk.Button(footer, text="既定に戻す", command=reset).pack(side="right", padx=(0, 12))
    root.protocol("WM_DELETE_WINDOW", close)
    return root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
