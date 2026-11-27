# -*- coding: utf-8 -*-
"""募集のウィンドウと契約の帯に出す中身を組む（Kivy を使わない。描くのは `window.py`）。

入口（`labor_overhaul.py`）が募集と契約の控えを渡し、ここが画面の札・帯のマスの辞書にする。
金額の書き方（3桁区切りと通貨の表記）は入口が `money(値)` で渡す。
"""

from . import rules

#: 施設の種類の呼び名（札の題の横に小さく出す）。
FACILITY_KIND_NAMES = {
    "blacksmith": "鍛冶屋", "inn": "宿屋", "general_store": "雑貨屋", "specialty_shop": "専門店",
    "medical_facility": "医療施設", "guild": "ギルド", "administrative_office": "役場",
    "training_facility": "訓練所", "colosseum": "闘技場",
}

#: 色の鍵（`window.py` が色に引く）。
GOOD, BAD, PLAIN, GOLD, ACCENT, DANGER = "good", "bad", "plain", "gold", "accent", "danger"

#: 帯に並べるマスの上限。超えたら、いまの期の前後だけを並べる。
STRIP_CELLS_MAX = 12
#: 帯で、いまの期より前に残すマスの数（上限を超えたとき）。
STRIP_CELLS_BEFORE = 5

PER_TEXT = "1回 {pay}"
OWNER_ROW = ("雇い主", "{owner}（好感度 {affinity}）")
OWNER_ROW_PLAIN = ("雇い主", "{owner}")
WORK_ROW = "仕事"
ABILITY_ROW = ("能力", "{scores}　賃金 ×{rate}")
ABILITY_ROW_PLAIN = ("能力", "{words}")
PORTER_ABILITY_ROW = ("能力", "問わない")
#: 雇い主と勤続のない札（人足・臨時）の行。勤続のある札と行の高さをそろえる。
PORTER_OWNER_ROW = ("雇い主", "その日ごと")
NO_TENURE_ROW = ("勤続", "付かない")
RELIEF_EVENT_ROW = ("街の様子", "{event}（段階 {stage}）")
ILLEGAL_ROWS = (("仕事", "{detail}"), ("能力", "{scores}"), ("危険", "{risks}"))
#: 契約のボタン。長さは契約の全体（1回の長さ × 回数）で出す。
SHORT_BUTTON = "短い契約（{period}）"
LONG_BUTTON = "長い契約（{period}）"
ONE_BUTTON = "引き受ける（{period}）"
#: ボタンの2段目の額。満了の割増が付く仕事は満了までの合計、裏の仕事（割増なし）は全部の回の合計。
BUTTON_FULL = "満了で {total}"
BUTTON_TOTAL = "計 {total}"
BARRED_NOTE = "途中でやめたため、あと {days}日は雇われない"
NEXT_RANK = "昇格まで あと {days}日"
TOP_RANK = "最高位"
RANK_EFFECT = "賃金 ×{pay}"
RANK_EFFECT_DISCOUNT = "賃金 ×{pay}・買値 {discount}%引き"
PLACEHOLDER_ANYONE = ("誰でも", "")
PLACEHOLDER_RELIEF = ("臨時", "街の出来事")
PLACEHOLDER_RISK = "{risk}%"
PLACEHOLDER_RISK_NOTE = "1回ごとの危険"
CORNER_RISK = "危険 {risk}%"
BADGE_AGAIN = "前回の仕事"
BADGE_RELIEF = "臨時"
SCORE_PART = "{name}({score})"

STRIP_SUB = "{kind}　{terms}回・1回 {period}"
STRIP_SUB_ILLEGAL = "闇労働　{terms}回・1回 {period}　危険度 {danger}"
STRIP_KIND = {True: "長い契約", False: "短い契約"}
STRIP_OWNER = "雇い主 {owner}"
STRIP_TENURE = "　勤続 {rank}（{next}）"
STRIP_TENURE_TOP = "　勤続 {rank}"
STRIP_EARNED = "稼ぎ {earned}"
#: 割増の額は最後のマスに出すので、ここは「割増込み」だけ（4:3 の窓では右の欄が狭い）
STRIP_LEFT = "あと{left}回　満了で {total}（割増込み）"
STRIP_LEFT_PLAIN = "あと{left}回　満了で {total}"
STRIP_LEFT_ILLEGAL = "あと{left}回　務め終えると {total}"
STRIP_DONE = "務め終えた　割増 {bonus}"
STRIP_DONE_PLAIN = "務め終えた"
STRIP_TERM = "{n}回目"
STRIP_NOW = "次の回"
STRIP_RISK = "危険 {risk}%（{risks}）"
STRIP_FINAL_BONUS = "満了 +{bonus}"
STRIP_BANNER = "勤続が上がった　{before} → {after}（{effect}）"
TAG_EVENT = "出来事 {delta}"
TAG_EVENT_PLAIN = "出来事"
TAG_FIRED = "打ち切り"
TAG_SAFE = "無事"
TAG_CRACKDOWN = "取り締まり　手配度 −{law}"
TAG_RETALIATION = "仕返し −{loss}"
TAG_RETALIATION_PLAIN = "仕返し"
TAG_BETRAYAL = "裏切り"
TAG_AVOIDED = "{risk} 回避"
TAG_SETTLED = "仕返し 収めた"
TAG_WITHDREW = "手を引いた"
TAG_ITEM = "品をもらった"
UNKNOWN_PAY = "—"


def signed(value, money):
    """「+80」「−40」の形（通貨の表記込み）。"""
    value = int(value)
    return ("+" if value >= 0 else "−") + money(abs(value))


def rate_text(percent):
    """%（125.0）を「1.25」の倍率の文字にする。"""
    return "{:g}".format(round(float(percent) / 100.0, 2))


def tenure_view(days, discount=True):
    """勤続の帯に出すもの。`{"stage", "name", "ratio", "next", "effect"}`。"""
    days = max(0, int(rules._number(days)))
    stage = rules.rank_index(days)
    row = rules.RANKS[stage]
    left = rules.days_to_next_rank(days)
    if left is None:
        ratio, nxt = 1.0, TOP_RANK
    else:
        start, end = row[1], rules.RANKS[stage + 1][1]
        ratio = max(0.0, min(1.0, (days - start) / float(max(1, end - start))))
        nxt = NEXT_RANK.format(days=left)
    if discount and row[3] > 0:
        effect = RANK_EFFECT_DISCOUNT.format(pay=rate_text(row[2]), discount=row[3])
    else:
        effect = RANK_EFFECT.format(pay=rate_text(row[2]))
    return {"stage": stage, "name": row[0], "ratio": ratio, "next": nxt, "effect": effect}


def score_words(abilities, scores):
    """「筋力 21・耐久 17」。読めない能力値は名前だけ。"""
    parts = []
    for name in abilities or ():
        label = rules.ABILITY_NAMES.get(name, name)
        score = (scores or {}).get(name)
        parts.append(SCORE_PART.format(name=label, score=int(score)) if rules._number(score, None) is not None
                     else label)
    return "・".join(parts)


def contract_buttons(offer, total_of, money):
    """札の契約のボタン `[(文言, 額, [札の番号, 回数, 長い契約か])]`。"""
    out = []
    rows = offer.get("terms") or []
    for terms, long_contract in rows:
        terms = int(terms)
        period = rules.days_in_words(int(offer["term_days"]) * terms)
        if len(rows) == 1:
            label = ONE_BUTTON.format(period=period)
        else:
            label = (LONG_BUTTON if long_contract else SHORT_BUTTON).format(period=period)
        amount = (BUTTON_TOTAL if offer.get("kind") == rules.ILLEGAL else BUTTON_FULL).format(
            total=money(total_of(offer, terms, bool(long_contract))))
        out.append((label, amount, [terms, bool(long_contract)]))
    return out


def title_keep(name, place):
    """施設の仕事の名前「〈施設名〉の〈役名〉」の「の〈役名〉」。題を詰めるときに残す。施設の仕事でなければ空。"""
    name, place = name or "", place or ""
    if place and name.startswith(place + "の") and len(name) > len(place) + 1:
        return name[len(place):]
    return ""


def work_rows(offer):
    """仕事の中身の1行（募集に説明があれば）。"""
    return [(WORK_ROW, offer["work"])] if offer.get("work") else []


def office_card(offer, total_of, money, discount=True):
    """役場の募集1件の札。"""
    period = rules.days_in_words(offer["term_days"])
    card = {"title": offer["name"], "kind": FACILITY_KIND_NAMES.get(offer.get("job"), ""), "badge": None,
            "face": offer.get("face"), "placeholder": None, "per": PER_TEXT.format(period=period,
                                                                                  pay=money(offer["pay"])),
            "rows": [], "tenure": None, "buttons": contract_buttons(offer, total_of, money), "grey": False,
            "note": None, "keep": title_keep(offer["name"], offer.get("place"))}
    job = offer.get("job")
    if job == rules.PORTER:
        card["kind"] = FACILITY_KIND_NAMES["administrative_office"]
        card["placeholder"] = PLACEHOLDER_ANYONE
        card["rows"] = [PORTER_OWNER_ROW] + work_rows(offer) + [PORTER_ABILITY_ROW, NO_TENURE_ROW]
        return card
    scores = score_words(offer.get("abilities"), offer.get("scores"))
    rate = rate_text(offer.get("ability_percent") or 100)
    if job == rules.RELIEF:
        card["kind"] = "街の出来事"
        card["placeholder"] = PLACEHOLDER_RELIEF
        card["placeholder_tone"] = ACCENT
        card["badge"] = (BADGE_RELIEF, ACCENT)
        card["rows"] = [(RELIEF_EVENT_ROW[0], RELIEF_EVENT_ROW[1].format(
            event=offer.get("event_name") or "困り事", stage=offer.get("event_stage") or 1))]
        card["rows"] += work_rows(offer) + [(ABILITY_ROW[0], ABILITY_ROW[1].format(scores=scores, rate=rate)),
                                            NO_TENURE_ROW]
        return card
    affinity = offer.get("affinity")
    if rules._number(affinity, None) is not None:
        card["rows"].append((OWNER_ROW[0], OWNER_ROW[1].format(owner=offer.get("owner_name"), affinity=int(affinity))))
    else:
        card["rows"].append((OWNER_ROW_PLAIN[0], OWNER_ROW_PLAIN[1].format(owner=offer.get("owner_name"))))
    card["rows"] += work_rows(offer)
    card["rows"].append((ABILITY_ROW[0], ABILITY_ROW[1].format(scores=scores, rate=rate)))
    if offer.get("tenure") is not None:
        card["tenure"] = tenure_view(offer["tenure"], discount)
    if offer.get("again"):
        card["badge"] = (BADGE_AGAIN, ACCENT)
    elif card["tenure"] is not None and card["tenure"]["stage"] == len(rules.RANKS) - 1:
        card["badge"] = (card["tenure"]["name"], GOLD)
    return card


def barred_card(row, discount=True):
    """途中でやめて、まだ雇ってもらえない雇い主の札（押せない）。"""
    return {"title": row["name"], "kind": FACILITY_KIND_NAMES.get(row.get("job"), ""), "badge": None,
            "face": row.get("face"), "placeholder": None, "per": "",
            "rows": [(OWNER_ROW_PLAIN[0], OWNER_ROW_PLAIN[1].format(owner=row.get("owner_name"))),
                     (ABILITY_ROW_PLAIN[0], ABILITY_ROW_PLAIN[1].format(
                         words=rules.ability_words(row.get("abilities"))))],
            "tenure": tenure_view(row.get("tenure") or 0, discount), "buttons": [], "grey": True,
            "note": BARRED_NOTE.format(days=max(1, int(row.get("days_left") or 1))),
            "keep": title_keep(row["name"], row.get("place"))}


def illegal_card(offer, total_of, money):
    """裏の仕事1件の札。"""
    job = rules.ILLEGAL_JOB_BY_KEY.get(offer.get("job")) or {}
    period = rules.days_in_words(offer["term_days"])
    risk = offer.get("risk")
    values = {"detail": offer.get("work") or job.get("detail") or "",
              "scores": score_words(offer.get("abilities"), offer.get("scores")),
              "risk": "{:g}".format(round(float(risk), 1)) if rules._number(risk, None) is not None else "?",
              "risks": rules.risk_words(job) if job else ""}
    danger = int(offer.get("danger") or 1)
    tone = DANGER if danger >= rules.MAX_DANGER else (ACCENT if danger > 1 else PLAIN)
    # 顔の欄には仕事の様子のアイコンを出し、危険の確率を右下に重ねる（雇い主は全部の札で同じ人なので、見出しに名前だけ出す）。
    # アイコンが無ければ、危険の確率を欄いっぱいに出す
    icon = offer.get("icon")
    return {"title": offer["name"], "kind": "危険度 {}".format(danger),
            "badge": None, "face": icon, "placeholder": (PLACEHOLDER_RISK.format(risk=values["risk"]),
                                                          PLACEHOLDER_RISK_NOTE),
            "corner": (CORNER_RISK.format(risk=values["risk"]), tone) if icon else None,
            "placeholder_tone": tone,
            "per": PER_TEXT.format(period=period, pay=money(offer["pay"])),
            "rows": [(label, text.format(**values)) for label, text in ILLEGAL_ROWS],
            "tenure": None, "buttons": contract_buttons(offer, total_of, money), "grey": False, "note": None,
            "danger": int(offer.get("danger") or 1) >= rules.MAX_DANGER,
            # 契約が1つで勤続の帯も無いので、1回の額をボタンの段へ寄せた詰めた札にする
            "compact": True}


# ---------------------------------------------------------------- 帯
def tag_of(entry, money):
    """1期の結果の印 `(文, 色の鍵)`。印の無い期は None。"""
    if not isinstance(entry, dict):
        return None
    kind = entry.get("kind")
    if kind in rules.RISK_NAMES and entry.get("avoided"):
        if entry.get("how") == "sure":
            return (TAG_SETTLED if kind == rules.RETALIATION else TAG_WITHDREW), PLAIN
        return TAG_AVOIDED.format(risk=rules.RISK_NAMES[kind]), GOOD
    if kind == "event":
        if entry.get("fired"):
            return TAG_FIRED, BAD
        if entry.get("item"):
            return TAG_ITEM, GOOD
        delta = int(rules._number(entry.get("delta")))
        tone = entry.get("tone") or PLAIN
        if delta:
            return TAG_EVENT.format(delta=signed(delta, money)), (GOOD if delta > 0 else BAD)
        return TAG_EVENT_PLAIN, tone
    if kind == rules.CRACKDOWN:
        return TAG_CRACKDOWN.format(law=int(rules._number(entry.get("law")))), BAD
    if kind == rules.RETALIATION:
        loss = int(rules._number(entry.get("loss")))
        return (TAG_RETALIATION.format(loss=money(loss)) if loss else TAG_RETALIATION_PLAIN), BAD
    if kind == rules.BETRAYAL:
        return TAG_BETRAYAL, BAD
    if kind == "safe":
        return TAG_SAFE, PLAIN
    return None


def strip_window(terms, done):
    """並べる期の範囲 `(最初, 終わりの次)`。上限を超えたら、いまの期の前後だけ。"""
    terms = max(0, int(terms))
    if terms <= STRIP_CELLS_MAX:
        return 0, terms
    first = max(0, min(int(done) - STRIP_CELLS_BEFORE, terms - STRIP_CELLS_MAX))
    return first, first + STRIP_CELLS_MAX


def strip_model(contract, money, *, face=None, tenure=None, risk=None, discount=True, finished=False,
                bonus=0, rank_up=None):
    """契約の帯に出すもの。

    `tenure` はいまの勤続の日数（雇い主の無い仕事は None）、`risk` は裏の仕事のこの期の危険（%）。
    `finished` は務め終えた後の帯（全部のマスが済み）。`rank_up` は `(前の段の番号, 後の段の番号)`。
    """
    illegal = contract.get("kind") == rules.ILLEGAL
    terms = int(contract.get("terms") or 0)
    done = int(contract.get("done") or 0)
    pay = int(contract.get("pay") or 0)
    period = rules.days_in_words(contract.get("term_days") or 0)
    log = [entry for entry in contract.get("log") or [] if isinstance(entry, dict)]
    if illegal:
        sub = STRIP_SUB_ILLEGAL.format(terms=terms, period=period, danger=contract.get("danger") or 1)
    else:
        sub = STRIP_SUB.format(kind=STRIP_KIND[bool(contract.get("long"))], terms=terms, period=period)
    line = ""
    if contract.get("owner"):
        line = STRIP_OWNER.format(owner=contract.get("owner_name") or "")
        if tenure is not None:
            view = tenure_view(tenure, discount)
            line += (STRIP_TENURE_TOP.format(rank=view["name"]) if view["stage"] == len(rules.RANKS) - 1
                     else STRIP_TENURE.format(rank=view["name"], next=view["next"]))
    earned = int(contract.get("earned") or 0)
    left = max(0, terms - done)
    relief = contract.get("job") == rules.RELIEF
    whole_bonus = 0 if illegal else rules.finish_bonus(pay * terms, bool(contract.get("long")), relief=relief)
    if finished:
        right2 = STRIP_DONE.format(bonus=money(bonus)) if bonus > 0 else STRIP_DONE_PLAIN
    elif illegal:
        right2 = STRIP_LEFT_ILLEGAL.format(left=left, total=money(earned + pay * left))
    else:
        rest = earned + pay * left
        bonus_left = rules.finish_bonus(rest, bool(contract.get("long")), relief=relief)
        right2 = (STRIP_LEFT.format(left=left, total=money(rest + bonus_left), bonus=money(bonus_left))
                  if bonus_left > 0 else STRIP_LEFT_PLAIN.format(left=left, total=money(rest)))
    cells = []
    first, end = strip_window(terms, done)
    for index in range(first, end):
        cell = {"n": STRIP_TERM.format(n=index + 1), "pay": money(pay), "tag": None, "tone": PLAIN, "note": None}
        if index < done or finished:
            cell["state"] = "done"
            entry = log[index] if index < len(log) else None
            if entry is not None:
                cell["pay"] = money(int(rules._number(entry.get("pay"))))
                found = tag_of(entry, money)
                if found is not None:
                    cell["tag"], cell["tone"] = found
                if int(rules._number(entry.get("pay"))) <= 0:
                    cell["pay_tone"] = BAD
            else:
                cell["pay"] = UNKNOWN_PAY
        elif index == done:
            if illegal:
                cell["state"] = "danger"
                if risk is not None:
                    job = rules.ILLEGAL_JOB_BY_KEY.get(contract.get("job")) or {}
                    cell["note"] = STRIP_RISK.format(risk="{:g}".format(round(float(risk), 1)),
                                                     risks=rules.risk_words(job) if job else "")
                    cell["tone"] = DANGER
            else:
                cell["state"] = "now"
                cell["note"] = STRIP_NOW
                cell["tone"] = GOOD
        else:
            cell["state"] = "next"
        if index == terms - 1 and not finished and not illegal and whole_bonus > 0 and index >= done:
            cell["tag"], cell["tone"] = STRIP_FINAL_BONUS.format(bonus=money(whole_bonus)), GOLD
        cells.append(cell)
    banner = None
    if rank_up is not None and rank_up[1] > rank_up[0] and tenure is not None:
        banner = STRIP_BANNER.format(before=rules.RANKS[rank_up[0]][0], after=rules.RANKS[rank_up[1]][0],
                                     effect=tenure_view(tenure, discount)["effect"])
    return {"face": face, "title": contract.get("name") or "", "sub": sub, "line": line,
            "keep": title_keep(contract.get("name"), contract.get("place")) if not illegal else "",
            "right": STRIP_EARNED.format(earned=money(earned + (bonus if finished else 0))),
            "right2": right2, "cells": cells, "banner": banner, "illegal": illegal, "finished": bool(finished)}


def event_summary(log, limit=3):
    """雇い主の一言の頼みに渡す、働いた間の出来事の行（新しい順に `limit` 件まで、古い順に並べ直す）。"""
    rows = [entry for entry in log or [] if isinstance(entry, dict) and entry.get("summary")]
    return [entry["summary"] for entry in rows[-int(limit):]]
