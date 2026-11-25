# -*- coding: utf-8 -*-
r"""労働の MOD の設定画面。左に表の形の入力欄、右にその設定での試算。

    python runtime/mods/349_labor_overhaul/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 宣言の設定は 52 項目で、どれも「数を変えると稼ぎと危険がどう変わるか」を見ないと決められない。

- 左は、数を **表の形** で入れる。短い契約／長い契約、勤続の4段階、危険度 1〜3 のように、
  並べて比べる数は列に並べ、表にならない数は文の中に欄を挟む（`CARDS`）
- 右は **試算**。依頼の最高難易度・その街の実績・能力値の平均・勤続を動かすと、その設定での1回の賃金と内訳、
  契約の稼ぎ（素の労働・依頼1件との比べ）、闇労働の危険度ごとの賃金と危険の確率が出る。
  左の数を変えると、その場で変わる（保存の前に確かめられる）
- 欄に入ると、下の1行にその項目の説明と既定値を出す

| 画面 | 書く先 |
|---|---|
| 左の入力欄 | `settings/mod_settings.json`（`instantale_modloader.config` 経由。既定と同じ値は書かない） |

**一括設定だけ。** ワールド個別設定は出さない（MOD 本体は設定をモジュールの定数からしか読まない）。

項目の名前・型・既定値・上下限・説明は `mod.json` の `"settings"` が唯一の出所（`modtool.decls`）。
この画面が持つのは、どの項目をどこに置くか（`CARDS`）と、試算の式の呼び方（`simulate`）。
式そのものは MOD の `rules.py`（ゲームに触らない）を読み込んで使うので、ゲームの中の計算と同じ。
場所・設定の読み書き・ウィンドウの記憶・配色は `tools/modtool.py`（どの道具も同じ）。
"""
import importlib.util
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする。
# `IML_ROOT` が指す先に `tools/` が無いこと（オフラインの検査）と、
# 環境変数の無い直接起動の両方があるので、3つ上も候補に入れる。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    # 見つかった最初の候補で止める（両方を先頭へ足すと、後の3つ上が IML_ROOT より先になる）
    if os.path.isfile(os.path.join(_tools, "modtool.py")):
        if _tools not in sys.path:
            sys.path.insert(0, _tools)
        break

import modtool  # noqa: E402

# ---------------------------------------------------------------- 置き方
# カード = (鍵, 見出し, 入切の設定名か None, 一言, 中身の並び)
# 中身 = ("line", [文字列 か 設定名, ...])          文の中に欄を挟んだ1行
#        ("table", [列の見出し, ...], [(行の見出し, [設定名 か 固定の文字, ...]), ...])
#        ("check", 設定名)                           真偽の1行
#: 表の中で、欄ではなく固定の文字として出す印（`FIXED + "100"`）。
FIXED = "="

CARDS = (
    ("contract", "役場の契約", None, "", (
        ("line", ["90日の賃金の基準は、同じ難易度の依頼1件の報酬の", "QUEST_PAY_PERCENT", "%（素の賃金より低ければ素の賃金）"]),
        ("line", ["「働く」1回は", "TERM_DAYS", "日（29歳まで）"]),
        ("line", ["一覧には施設の仕事を", "LIST_JOBS", "件並べ、", "ROTATE_DAYS", "日ごとに入れ替える"]),
        ("table", ["短い契約", "長い契約"], [
            ("「働く」の回数", ["SHORT_TERMS", "LONG_TERMS"]),
            ("満了の割増（%）", ["SHORT_BONUS_PERCENT", "LONG_BONUS_PERCENT"]),
            ("満了で上がる好感度", ["SHORT_AFFINITY", "LONG_AFFINITY"]),
        ]),
    )),
    ("pay", "能力値と出来事", None, "仕事ごとに決まった能力値2つの平均で、賃金と判定が変わる", (
        ("line", ["能力値の平均が 30 のとき賃金は", "ABILITY_MAX_PERCENT", "%、低いときの下限は",
                  "ABILITY_LOW_PERCENT", "%"]),
        ("line", ["出来事は1回ごとに", "EVENT_PERCENT", "% で起きる（闇労働は危険が起きなかった回だけ）"]),
        ("line", ["出来事の判定は、能力値1つにつき", "CHECK_PER_POINT", "% 上がる"]),
        ("line", ["店の品揃えから作る品は、買値が1回の賃金の", "ITEM_VALUE_TERMS", "倍まで。作れなければ心付け",
                  "ITEM_FALLBACK_PERCENT", "%"]),
    )),
    ("age", "年齢", "AGE_RULES", "素のゲームと同じく、30歳から5歳ごとに1回が3分の1ずつ長くなり（賃金は増えない）、老いると断られる", (
        ("line", ["役場は", "OFFICE_AGE_LIMIT", "歳から、裏の事務所は", "ILLEGAL_AGE_LIMIT", "歳から断られる"]),
    )),
    ("growth", "街での実績", "TOWN_GROWTH", "契約を務め終えるたびに、その街の賃金の基準（難易度）が上がる", (
        ("line", ["短い契約で", "GROWTH_SHORT", "、長い契約で", "GROWTH_LONG", "上がる"]),
        ("line", ["1つの街での上限は", "GROWTH_MAX"]),
    )),
    ("seniority", "勤続", "SENIORITY", "同じ雇い主で働いた日数で段階が上がる", (
        ("table", ["見習い", "一人前", "任される", "右腕"], [
            ("届く日数", [FIXED + "0", "RANK2_DAYS", "RANK3_DAYS", "RANK4_DAYS"]),
            ("賃金（%）", [FIXED + "100", "RANK2_PAY_PERCENT", "RANK3_PAY_PERCENT", "RANK4_PAY_PERCENT"]),
            ("その店の値引き（%）", [FIXED + "0", "RANK2_DISCOUNT_PERCENT", "RANK3_DISCOUNT_PERCENT",
                                     "RANK4_DISCOUNT_PERCENT"]),
        ]),
        ("check", "SHOP_DISCOUNT"),
        ("line", ["途中でやめると勤続が", "QUIT_TENURE_DAYS", "日ぶん減り、", "QUIT_BARRED_DAYS",
                  "日のあいだ一覧に出ない"]),
    )),
    ("town", "街の出来事", "INCIDENT_LINK", "街の出来事の MOD が入っているときだけ効く", (
        ("line", ["良い出来事は段階ごとに賃金", "GOOD_EVENT_STEP_PERCENT", "% 増し、上限", "GOOD_EVENT_MAX_PERCENT",
                  "%"]),
        ("line", ["良い出来事が崩れた段階の賃金は", "BAD_STAGE_PERCENT", "%"]),
        ("line", ["臨時の募集は賃金", "RELIEF_PAY_PERCENT", "% で、", "RELIEF_TERM_DAYS", "日 ×", "RELIEF_TERMS",
                  "回"]),
        ("line", ["臨時の募集を務め終えると、困り事が", "RELIEF_STEPS", "段階収まる"]),
    )),
    ("illegal", "闇労働", None, "賃金は役場の同じ長さの賃金に対する割合。危険は能力値が 11 のときの確率", (
        ("line", ["1回は", "ILLEGAL_TERM_DAYS", "日（29歳まで）で、", "ILLEGAL_TERMS", "回"]),
        ("line", ["危険の起きやすさ", "RISK_STRENGTH_PERCENT", "%"]),
        ("table", ["危険度 1", "危険度 2", "危険度 3"], [
            ("賃金（%）", ["ILLEGAL_PAY_MIN_PERCENT", FIXED + "中間", "ILLEGAL_PAY_MAX_PERCENT"]),
            ("危険の確率（%）", ["RISK_PERCENT_1", "RISK_PERCENT_2", "RISK_PERCENT_3"]),
            ("避ける判定の基準（%）", ["AVOID_PERCENT_1", "AVOID_PERCENT_2", "AVOID_PERCENT_3"]),
        ]),
        ("line", ["街に困り事がある間は賃金", "ILLEGAL_TROUBLE_PERCENT", "%"]),
        ("line", ["その間に務め終えると、困り事が", "ILLEGAL_STEPS", "段階悪くなる"]),
    )),
    ("screen", "画面", None, "数には効かない。見せ方だけ", (
        ("check", "LIST_WINDOW"),
        ("check", "PROGRESS_STRIP"),
        ("check", "FINISH_SCENE"),
        ("check", "FINISH_LINE"),
    )),
    ("risk", "危険を避けられなかったとき", None, "どれも、その回の賃金は入らない", (
        ("line", ["見張りに握らせる額は1回の賃金の", "BRIBE_PAY_PERCENT", "%"]),
        ("line", ["取り締まり: 手配度が、危険度1つにつき", "CRACKDOWN_LAW", "下がる"]),
        ("line", ["仕返し: 賃金", "RETALIATION_TERMS", "回ぶんを奪われる"]),
        ("line", ["裏切り: その契約の稼ぎを失い、手配度が", "BETRAYAL_LAW", "下がる"]),
        ("line", ["裏切り: 勤続が", "BETRAYAL_TENURE_DAYS", "日減る"]),
    )),
)

#: 期間のひな形 `(ボタンの文言, {設定名: 値})`。
PRESETS = (
    ("素のゲームと同じ（3ヵ月 × 4回 / 8回）", {"TERM_DAYS": 90, "SHORT_TERMS": 4, "LONG_TERMS": 8}),
    ("1ヵ月刻み（1ヵ月 × 3回 / 6回）", {"TERM_DAYS": 30, "SHORT_TERMS": 3, "LONG_TERMS": 6}),
)

#: 試算に使う、ゲームの賃金（1回ぶん）と依頼1件の報酬。難易度 0〜76 の順（ゲームの式を測った値）。
#: 設定画面はゲームの外で動くので式を呼べず、測った表を持つ。76 より上は 76 の値で止める（MOD なしのゲームと同じ）。
LABOR_CURVE = (
    6, 9, 13, 43, 59, 69, 73, 77, 81, 90, 100, 107, 112, 118, 123, 130, 138, 141, 144, 147,
    151, 153, 155, 157, 159, 161, 162, 165, 169, 173, 178, 183, 192, 203, 216, 231, 237, 243, 250, 258,
    264, 271, 279, 285, 291, 294, 299, 304, 309, 315, 320, 326, 330, 334, 338, 342, 347, 353, 360, 367,
    371, 375, 379, 385, 389, 394, 399, 403, 408, 414, 421, 425, 431, 437, 441, 445, 449)
QUEST_CURVE = (
    7, 7, 11, 21, 37, 60, 75, 81, 107, 121, 326, 416, 503, 614, 704, 796, 857, 889, 958, 1046,
    2011, 2118, 2272, 2452, 2663, 2905, 3166, 3400, 3672, 4006, 4234, 4505, 4788, 5114, 5477, 5882, 6079, 6300,
    6545, 6814, 7236, 7699, 5791, 6314, 9271, 9475, 9689, 9912, 10145, 10387, 10639, 10901, 11172, 11453, 11882,
    12334, 12806, 13301, 13817, 14354, 14813, 15290, 15787, 16303, 16841, 17494, 18178, 18893, 19639, 20633,
    21223, 21682, 22154, 22644, 23150, 23674, 24216)
DIFFICULTY_MAX = len(LABOR_CURVE) - 1
#: つまみの初めの位置（依頼の最高難易度・この街の実績・能力値の平均・勤続の日数・年齢）。
START = (30, 0, 20, 600, 25)
AGE_RANGE = (15, 70)
GROWTH_SLIDER_MAX = 100
ABILITY_RANGE = (1, 30)
TENURE_MAX = 3600
#: 通貨の表記を読む MOD のフォルダ名の後ろと、その設定名・無いときの表記。
CURRENCY_MOD_SUFFIX = "_currency_unit"
CURRENCY_KEY = "UNIT_SHORT"
CURRENCY_FALLBACK = "G"


def card_keys(card):
    """そのカードに置いた設定名（入切を先頭に、置いた順）。"""
    keys = [card[2]] if card[2] else []
    for part in card[4]:
        if part[0] == "line":
            keys += [token for token in part[1] if token.isupper() and token.replace("_", "").isalnum()
                     and token == token.strip()]
        elif part[0] == "table":
            for _label, cells in part[2]:
                keys += [cell for cell in cells if not cell.startswith(FIXED)]
        elif part[0] == "check":
            keys.append(part[1])
    return keys


def placed_keys():
    """画面に置いた設定名の全部（置いた順）。"""
    return [key for card in CARDS for key in card_keys(card)]


def is_setting(token, found):
    """文の並びの1つが、欄にする設定名か（そうでなければ文字として出す）。"""
    return token in found


def load_rules(mod_dir=MOD_DIR):
    """MOD の規則（`rules.py`。ゲームに触らない）を読み込む。試算はゲームの中と同じ式で出す。"""
    spec = importlib.util.spec_from_file_location("labor_overhaul_tool_rules", os.path.join(mod_dir, "rules.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def numbers(found, values):
    """画面の値を数にする。読めない欄（打ちかけ・空）は宣言の既定。真偽はそのまま。"""
    out = {}
    for key, decl in found.items():
        value = values.get(key, decl["default"])
        if decl["type"] == "bool":
            out[key] = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes")
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            out[key] = float(decl["default"])
    return out


def simulate(rules, found, values, difficulty, ability, tenure, growth=0, age=None):
    """その設定での試算。

    `difficulty` は主人公が片付けた依頼の最高難易度、`growth` はその街の労働の実績、
    `ability` は効く能力値の平均、`tenure` は勤続の日数、`age` は主人公の年齢（None なら年齢の決まりを当てない）。
    役場の施設の仕事の1回の賃金と内訳、契約の稼ぎ、素の労働と依頼1件との比べ、
    臨時の募集、闇労働の危険度ごとの賃金と危険の確率を返す。
    """
    v = numbers(found, values)
    rules.configure(**rules.values_from_settings(v.get))
    difficulty = max(0, min(DIFFICULTY_MAX, int(difficulty)))
    growth = max(0, min(int(growth), int(v["GROWTH_MAX"]))) if v["TOWN_GROWTH"] else 0
    level = min(DIFFICULTY_MAX, difficulty + growth)
    base = rules.base_pay(LABOR_CURVE[level], QUEST_CURVE[level])
    quest = QUEST_CURVE[difficulty]
    setting_term = max(1, int(v["TERM_DAYS"]))
    aged = bool(v["AGE_RULES"]) and age is not None

    def for_age(days):
        return rules.age_days(days, age) if aged else max(1, int(days))

    term = for_age(setting_term)
    office_refused = aged and int(age) >= int(v["OFFICE_AGE_LIMIT"])
    illegal_refused = aged and int(age) >= int(v["ILLEGAL_AGE_LIMIT"])
    seniority = bool(v["SENIORITY"])
    days = int(tenure) if seniority else 0
    ability_percent = rules.ability_percent(ability, v["ABILITY_MAX_PERCENT"])
    rank_percent = rules.rank_pay_percent(days)
    # 賃金は設定の長さ（年齢で伸ばす前）で決まる。ゲームと同じく、年齢で1回が長くなっても額は増えない
    pay = rules.term_pay(base, setting_term, (ability_percent, rank_percent))
    leveled = rules.term_pay(base, setting_term)                        # 街の実績まで入れた、倍率を掛ける前の賃金
    plain = rules.term_pay(LABOR_CURVE[difficulty], rules.GAME_TERM_DAYS)   # MOD なしの労働の1回の賃金
    short_terms, long_terms = max(1, int(v["SHORT_TERMS"])), max(1, int(v["LONG_TERMS"]))
    short_total = pay * short_terms + rules.finish_bonus(pay * short_terms, False)
    long_total = pay * long_terms + rules.finish_bonus(pay * long_terms, True)
    if setting_term <= int(rules.RELIEF_TERM_DAYS):
        relief_base, relief_terms = setting_term, short_terms
    else:
        relief_base, relief_terms = int(rules.RELIEF_TERM_DAYS), int(rules.RELIEF_TERMS)
    relief_days = for_age(relief_base)
    relief_pay = rules.term_pay(base, relief_base, (
        rules.ability_percent(ability, v["ABILITY_MAX_PERCENT"]), rules.RELIEF_PAY_PERCENT))
    illegal_setting = max(1, int(v["ILLEGAL_TERM_DAYS"]))
    illegal_days = for_age(illegal_setting)
    illegal = []
    for danger in (1, 2, 3):
        percent = rules.illegal_pay_percent(danger, v["ILLEGAL_PAY_MIN_PERCENT"], v["ILLEGAL_PAY_MAX_PERCENT"])
        chance = rules.risk_percent(danger, ability, illegal_days, days) * max(0.0, v["RISK_STRENGTH_PERCENT"]) / 100.0
        illegal.append({"danger": danger, "pay": rules.term_pay(base, illegal_setting, (percent,)),
                        "chance": max(0.0, min(float(rules.RISK_PERCENT_CAP), chance))})
    return {
        "difficulty": difficulty, "growth": growth, "level": level, "age": age,
        "office_refused": office_refused, "illegal_refused": illegal_refused,
        "plain_long_days": for_age(rules.GAME_TERM_DAYS) * 8,
        "term_days": term, "base": leveled, "ability_percent": ability_percent, "rank_percent": rank_percent,
        "rank": rules.rank_name(days), "discount": rules.rank_discount_percent(days) if v["SHOP_DISCOUNT"] else 0,
        "seniority": seniority, "pay": pay,
        "short_days": term * short_terms, "short_total": short_total,
        "long_days": term * long_terms, "long_total": long_total,
        "plain_long": plain * 8, "quest": quest,          # 素の労働は MOD なしの長い契約（8回）
        "share": 100.0 * long_total / quest if quest else 0.0,
        "relief_pay": relief_pay, "relief_days": relief_days, "relief_terms": relief_terms,
        "illegal_days": illegal_days, "illegal": illegal,
    }


def currency(root_dir, mod_dir=MOD_DIR):
    """通貨の短い表記。通貨の表記の MOD の一括設定を読む。無ければ `G`。

    世界ごとの表記は見ない（この画面はどの世界にも効く設定で、世界を選ばない）。
    """
    try:
        mods = os.path.dirname(mod_dir)
        for name in sorted(os.listdir(mods)):
            if name.endswith(CURRENCY_MOD_SUFFIX) and os.path.isfile(os.path.join(mods, name, "mod.json")):
                value = modtool.load_settings(root_dir, os.path.join(mods, name)).get(CURRENCY_KEY)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        pass
    return CURRENCY_FALLBACK


def money(value):
    return "{:,}".format(int(round(value)))


def percent_text(value):
    """確率の表示。小数1桁まで、要らない 0 は出さない。"""
    return "{:g}".format(round(float(value), 1))


def hint_text(found, key):
    """欄に入ったときに下に出す1行。項目の名前・説明・既定。"""
    decl = found[key]
    parts = [decl["label"]["ja"]]
    note = (decl.get("note") or {}).get("ja") or ""
    if note:
        parts.append(note)
    parts.append("既定: " + modtool.shown(decl["default"], decl))
    return "　／　".join(parts)


# ---------------------------------------------------------------- 画面
def build_window(mod_dir=MOD_DIR):
    """ウィンドウを組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
    import tkinter as tk
    from tkinter import ttk

    win = modtool.SettingsWindow(mod_dir, geometry="1200x820", minsize=(1040, 640),
                                 subtitle="左で数を変えると、右の試算がその場で変わる", subtitle_wrap=1100)
    found = win.found
    rules = load_rules(mod_dir)
    unit = currency(win.root_dir, mod_dir)
    palette = getattr(win.theme, "PALETTE", None) or {}
    surface = palette.get("surface", "#ffffff")
    border = palette.get("border", "#d4d7dd")
    accent = palette.get("accent", "#2f6feb")
    text_color = palette.get("text", "#1f2328")
    sub_color = palette.get("text_sub", "#57606a")
    faint_color = palette.get("text_faint", "#8b949e")
    warn_color = palette.get("warn", "#9a5b00")
    ground = palette.get("bg", "#f4f5f7")
    quest_color = "#c9a227"

    style = ttk.Style(win.root)
    style.configure("Card.TFrame", background=surface)
    style.configure("Card.TLabel", background=surface)
    style.configure("CardHead.TLabel", background=surface, font=("", 11, "bold"))
    style.configure("CardFaint.TLabel", background=surface, foreground=faint_color)
    style.configure("CardCol.TLabel", background=surface, foreground=sub_color, font=("", 9, "bold"))
    style.configure("CardBig.TLabel", background=surface, font=("", 20, "bold"))
    style.configure("Card.TCheckbutton", background=surface)

    body = ttk.Frame(win.outer)
    body.pack(fill="both", expand=True)
    left = ttk.Frame(body)
    left.pack(side="left", fill="both", expand=True, padx=(0, 12))
    right = ttk.Frame(body, width=480)
    right.pack(side="left", fill="y")
    right.pack_propagate(False)

    variables = {}

    def variable(key):
        if key not in variables:
            variables[key] = tk.BooleanVar() if found[key]["type"] == "bool" else tk.StringVar()
        return variables[key]

    # ---- 左の下: 欄の説明
    hint = ttk.Label(left, style="Faint.TLabel", justify="left", wraplength=640,
                     text="欄に入ると、ここにその項目の説明と既定が出る")
    hint.pack(side="bottom", anchor="w", fill="x", pady=(6, 0))
    left.bind("<Configure>", lambda e: hint.configure(wraplength=max(240, e.width - 8)), add="+")

    def watch(widget, key):
        widget.bind("<FocusIn>", lambda _e: hint.configure(text=hint_text(found, key)), add="+")
        widget.bind("<Enter>", lambda _e: hint.configure(text=hint_text(found, key)), add="+")

    def entry(parent, key, width=7):
        box = ttk.Entry(parent, textvariable=variable(key), width=width, justify="center")
        watch(box, key)
        return box

    # ---- 左の上: 期間のひな形
    presets = ttk.Frame(left)
    presets.pack(side="top", fill="x", pady=(0, 8))
    ttk.Label(presets, text="期間のひな形", style="Group.TLabel").pack(side="left", padx=(0, 8))

    def apply_preset(values):
        for key, value in values.items():
            if key in variables:
                variables[key].set(modtool.as_shown({key: value})[key])

    for label, values in PRESETS:
        ttk.Button(presets, text=label, command=lambda v=values: apply_preset(v)).pack(side="left", padx=(0, 6))

    # ---- 左: カード
    cards = modtool.scrollable(left)
    cards.columnconfigure(0, weight=1)
    for row, (_gkey, title, enable, note, parts) in enumerate(CARDS):
        frame = tk.Frame(cards, background=surface, highlightbackground=border, highlightthickness=1)
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 10), padx=(0, 6))
        inner = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8, 12, 10))
        inner.pack(fill="x")
        head = ttk.Frame(inner, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=title, style="CardHead.TLabel").pack(side="left")
        if enable:
            switch = ttk.Checkbutton(head, text=found[enable]["label"]["ja"], variable=variable(enable),
                                     style="Card.TCheckbutton")
            switch.pack(side="right")
            watch(switch, enable)
        if note:
            ttk.Label(inner, text=note, style="CardFaint.TLabel").pack(anchor="w", pady=(0, 4))
        for part in parts:
            if part[0] == "line":
                line = ttk.Frame(inner, style="Card.TFrame")
                line.pack(anchor="w", pady=2)
                for token in part[1]:
                    if is_setting(token, found):
                        entry(line, token, width=6).pack(side="left", padx=3)
                    else:
                        ttk.Label(line, text=token, style="Card.TLabel").pack(side="left")
            elif part[0] == "table":
                grid = ttk.Frame(inner, style="Card.TFrame")
                grid.pack(anchor="w", pady=2)
                for column, text in enumerate(part[1]):
                    ttk.Label(grid, text=text, style="CardCol.TLabel", anchor="center").grid(
                        row=0, column=column + 1, padx=4, pady=(0, 2))
                for r, (label, cells) in enumerate(part[2], start=1):
                    ttk.Label(grid, text=label, style="Card.TLabel").grid(
                        row=r, column=0, sticky="w", padx=(0, 10), pady=2)
                    for column, cell in enumerate(cells):
                        if cell.startswith(FIXED):
                            widget = ttk.Label(grid, text=cell[len(FIXED):], style="CardFaint.TLabel",
                                               width=7, anchor="center")
                        else:
                            widget = entry(grid, cell)
                        widget.grid(row=r, column=column + 1, padx=4, pady=2)
            elif part[0] == "check":
                box = ttk.Checkbutton(inner, text=found[part[1]]["label"]["ja"], variable=variable(part[1]),
                                      style="Card.TCheckbutton")
                box.pack(anchor="w", pady=2)
                watch(box, part[1])

    # 置き忘れた設定は、最後のカードに宣言の形のまま出す（見えない設定を作らない）
    missing = [key for key in found if key not in variables]
    if missing:
        frame = tk.Frame(cards, background=surface, highlightbackground=border, highlightthickness=1)
        frame.grid(row=len(CARDS), column=0, sticky="ew", pady=(0, 10), padx=(0, 6))
        inner = ttk.Frame(frame, style="Card.TFrame", padding=(12, 8, 12, 10))
        inner.pack(fill="x")
        ttk.Label(inner, text="その他", style="CardHead.TLabel").pack(anchor="w")
        for key in missing:
            line = ttk.Frame(inner, style="Card.TFrame")
            line.pack(anchor="w", pady=2)
            if found[key]["type"] == "bool":
                ttk.Checkbutton(line, text=found[key]["label"]["ja"], variable=variable(key),
                                style="Card.TCheckbutton").pack(side="left")
            else:
                ttk.Label(line, text=found[key]["label"]["ja"], style="Card.TLabel").pack(side="left")
                entry(line, key, width=10).pack(side="left", padx=6)

    # ---- 右: 試算
    sim = tk.Frame(right, background=surface, highlightbackground=accent, highlightthickness=1)
    sim.pack(fill="both", expand=True)
    box = ttk.Frame(sim, style="Card.TFrame", padding=(14, 10, 14, 12))
    box.pack(fill="both", expand=True)
    ttk.Label(box, text="この設定での試算", style="CardHead.TLabel").pack(anchor="w")
    ttk.Label(box, text="主人公の条件を動かして、稼ぎと危険を確かめる", style="CardFaint.TLabel").pack(anchor="w")

    cond = ttk.Frame(box, style="Card.TFrame")
    cond.pack(fill="x", pady=(8, 4))
    cond.columnconfigure(1, weight=1)
    sliders = {}
    readouts = {}

    def slider(row, label, name, low, high, value):
        ttk.Label(cond, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        sliders[name] = tk.DoubleVar(value=value)
        ttk.Scale(cond, from_=low, to=high, variable=sliders[name],
                  command=lambda _v: refresh()).grid(row=row, column=1, sticky="ew", padx=8)
        readouts[name] = ttk.Label(cond, style="Card.TLabel", width=17, anchor="w")
        readouts[name].grid(row=row, column=2, sticky="w")

    slider(0, "依頼の最高難易度", "difficulty", 0, DIFFICULTY_MAX, START[0])
    slider(1, "この街の実績", "growth", 0, GROWTH_SLIDER_MAX, START[1])
    slider(2, "能力値の平均", "ability", ABILITY_RANGE[0], ABILITY_RANGE[1], START[2])
    slider(3, "勤続", "tenure", 0, TENURE_MAX, START[3])
    slider(4, "年齢", "age", AGE_RANGE[0], AGE_RANGE[1], START[4])

    ttk.Separator(box).pack(fill="x", pady=6)
    ttk.Label(box, text="役場の施設の仕事", style="CardCol.TLabel").pack(anchor="w")
    pay_line = ttk.Frame(box, style="Card.TFrame")
    pay_line.pack(fill="x")
    pay_big = ttk.Label(pay_line, style="CardBig.TLabel")
    pay_big.pack(side="left")
    pay_how = ttk.Label(pay_line, style="CardFaint.TLabel", justify="left")
    pay_how.pack(side="left", padx=10)
    contract_text = ttk.Label(box, style="Card.TLabel", justify="left")
    contract_text.pack(anchor="w", pady=(2, 0))
    extra_text = ttk.Label(box, style="CardFaint.TLabel", justify="left")
    extra_text.pack(anchor="w", pady=(0, 6))

    ttk.Label(box, text="長い契約1つの稼ぎを比べると", style="CardCol.TLabel").pack(anchor="w")
    bars = tk.Canvas(box, height=108, background=surface, highlightthickness=0)
    bars.pack(fill="x", pady=(2, 6))

    ttk.Label(box, text="闇労働（1回ごと）", style="CardCol.TLabel").pack(anchor="w")
    risk = tk.Canvas(box, height=122, background=surface, highlightthickness=0)
    risk.pack(fill="x", pady=(2, 0))

    def bar(y, label, value, top, color, text):
        width = max(1, int(bars.winfo_width()) - 190)
        bars.create_text(0, y + 9, text=label, anchor="w", fill=text_color, font=("", 9))
        length = max(2, int(width * min(1.0, value / float(top or 1))))
        bars.create_rectangle(104, y + 1, 104 + length, y + 17, fill=color, outline="")
        bars.create_text(110 + length, y + 9, text=text, anchor="w", fill=sub_color, font=("", 9))

    def everything():
        return dict((key, var.get()) for key, var in variables.items())

    def refresh(*_args):
        ability = int(round(sliders["ability"].get()))
        tenure = int(round(sliders["tenure"].get()))
        age = int(round(sliders["age"].get()))
        try:
            result = simulate(rules, found, everything(), int(round(sliders["difficulty"].get())), ability,
                              tenure, int(round(sliders["growth"].get())), age)
        except Exception:
            return                      # 打ちかけの値で式が落ちても、画面は止めない
        readouts["difficulty"].configure(text="{}".format(result["difficulty"]))
        readouts["growth"].configure(text="+{}（基準 {}）".format(result["growth"], result["level"]))
        readouts["ability"].configure(text="{}".format(ability))
        readouts["tenure"].configure(text="{} 日（{}）".format(money(tenure), result["rank"])
                                     if result["seniority"] else "数えない")
        readouts["age"].configure(text="{} 歳".format(age))
        period = rules.days_in_words(result["term_days"])
        if result["office_refused"]:
            pay_big.configure(text="この年齢では働けない")
        else:
            pay_big.configure(text="{}{} / {}".format(money(result["pay"]), unit, period))
        pay_how.configure(text="基準の賃金 {}{}（難易度 {}）\n× 能力値 {:.2f} × 勤続 {:.2f}".format(
            money(result["base"]), unit, result["level"], result["ability_percent"] / 100.0,
            result["rank_percent"] / 100.0))
        contract_text.configure(text="短い契約 {} で {}{}　／　長い契約 {} で {}{}（割増こみ）".format(
            rules.days_in_words(result["short_days"]), money(result["short_total"]), unit,
            rules.days_in_words(result["long_days"]), money(result["long_total"]), unit))
        extra = "臨時の募集は {} ごとに {}{}".format(
            rules.days_in_words(result["relief_days"]), money(result["relief_pay"]), unit)
        if result["discount"]:
            extra += "　／　その店の買値は {}% 引き".format(percent_text(result["discount"]))
        extra_text.configure(text=extra)

        bars.delete("all")
        top = max(result["plain_long"], result["long_total"], result["quest"])
        bar(4, "素の労働", result["plain_long"], top, border, "{}{}（{}）".format(
            money(result["plain_long"]), unit, rules.days_in_words(result["plain_long_days"])))
        bar(30, "この設定", result["long_total"], top, accent, "{}{}（{}）".format(
            money(result["long_total"]), unit, rules.days_in_words(result["long_days"])))
        bar(56, "依頼1件", result["quest"], top, quest_color, "{}{}".format(money(result["quest"]), unit))
        bars.create_text(0, 96, anchor="w", fill=faint_color, font=("", 9),
                         text="長い契約（{}）の稼ぎは、難易度 {} の依頼1件の報酬の {:.0f}%".format(
                             rules.days_in_words(result["long_days"]), result["difficulty"], result["share"]))

        risk.delete("all")
        column = max(1, int(risk.winfo_width())) // 3
        for index, row in enumerate(result["illegal"]):
            x = index * column
            risk.create_text(x, 8, anchor="w", text="危険度 {}".format(row["danger"]), fill=text_color,
                             font=("", 9, "bold"))
            risk.create_text(x, 30, anchor="w", text="{}{}".format(money(row["pay"]), unit), fill=text_color,
                             font=("", 14, "bold"))
            risk.create_text(x, 54, anchor="w", text="危険 {}%".format(percent_text(row["chance"])),
                             fill=warn_color, font=("", 9))
            risk.create_rectangle(x, 66, x + column - 24, 76, fill=ground, outline="")
            risk.create_rectangle(x, 66, x + int((column - 24) * min(100.0, row["chance"]) / 100.0), 76,
                                  fill=warn_color, outline="")
        risk.create_text(0, 104, anchor="w", fill=faint_color, font=("", 9),
                         text=("この年齢では裏の事務所でも断られる" if result["illegal_refused"] else
                               "{} の1回ぶん。能力値と勤続が高いほど危険は起きにくい".format(
                                   rules.days_in_words(result["illegal_days"]))))

    for canvas in (bars, risk):
        canvas.bind("<Configure>", refresh, add="+")
    for var in variables.values():
        var.trace_add("write", refresh)

    def set_values(values):
        shown = modtool.as_shown(values)
        for key, var in variables.items():
            var.set(values[key] if isinstance(var, tk.BooleanVar) else shown[key])
        refresh()

    win.finish(everything, set_values, reset_label="すべて既定に戻す")
    win.root.after(50, refresh)
    return win.root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
