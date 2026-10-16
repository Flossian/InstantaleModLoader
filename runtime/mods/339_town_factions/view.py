# -*- coding: utf-8 -*-
"""勢力と情勢の窓に出す中身を組む。ゲームにも Kivy にも触らない（辞書と文字列だけを扱う）。

窓の見た目と言葉は VERIFICATION.md §3.98「勢力と情勢の窓」。
色は段階（-2〜+2）で渡し、描く側（`window.py`）が段階の色に塗る。

    model = view.build(ledger, day=…, here=…, area_names=…, laws=…)

返す形:

    {"status": "ready" | "building" | "none" | "failed",
     "message": 窓に1文だけ出すとき（ready 以外）,
     "here": "ヴァルガルド（白翼騎士団の影響下）" / "",
     "cards": [{"name", "kind", "stage", "title", "note", "towns", "relations": [(相手, 段)], "cautions"}],
     "rumors": [{"when", "text", "follow": [(文, 段 or None)]}],
     "history": [{"when", "pieces": [(文, 段 or None)], "reason"}]}
"""
from . import rules

#: 窓に出す噂と来歴の期間（日）。
SHOWN_DAYS = 365

MESSAGES = {
    None: "読み込み中、時間を置いて再度開き直してください。",
    rules.NONE: "この世界の設定には、代表する勢力が見当たらない。",
    rules.FAILED: "読み込み失敗。ゲームをロードし直した際に、もう一度生成予定。",
}
WANTED_TEXT = "手配が解除されるまで、これ以上関係性が改善する見込みはない。"
LOCKED_TEXT = "並大抵の働きでは、この敵意が解ける見込みはない。"


def when_text(day, then):
    if day is None or not isinstance(then, (int, float)):
        return ""
    ago = int(day - then)
    return "今日" if ago <= 0 else "{}日前".format(ago)


def _recent(day, then):
    return day is None or not isinstance(then, (int, float)) or day - then <= SHOWN_DAYS


def _name(ledger, faction_id):
    faction = rules.faction_by_id(ledger, faction_id)
    return faction["name"] if faction else "?"


def _worst_law(ledger, faction_id, laws):
    towns = [t for t, row in (ledger.get("towns") or {}).items() if row.get("faction") == faction_id]
    found = [laws[t] for t in towns if isinstance(laws, dict) and isinstance(laws.get(t), (int, float))
             and not isinstance(laws.get(t), bool)]
    return min(found) if found else None


def cautions_of(ledger, faction_id, laws):
    """札の注意の行。手配の上限は、その上限に届いているときだけ（届く前は上がる余地がある）。"""
    row = (ledger.get("standing") or {}).get(faction_id) or {}
    score = row.get("score", 0)
    out = []
    worst = _worst_law(ledger, faction_id, laws)
    if worst is not None and worst < 0:
        ceiling = rules.ENEMY_CEILING if worst <= rules.HEAVILY_WANTED_AT else rules.WANTED_CEILING
        if score >= ceiling:
            out.append(WANTED_TEXT)
    if row.get("locked"):
        out.append(LOCKED_TEXT)
    return out


def card_of(ledger, faction, area_names, laws):
    faction_id = faction["id"]
    row = (ledger.get("standing") or {}).get(faction_id) or {}
    stage = rules.standing_stage(row.get("score", 0))
    towns = [area_names.get(t, t) for t, r in (ledger.get("towns") or {}).items()
             if r.get("faction") == faction_id]
    relations = []
    for other in ledger.get("factions") or []:
        if other["id"] != faction_id:
            relations.append((other["name"], rules.relation_stage(ledger, faction_id, other["id"])))
    relations.sort(key=lambda pair: pair[1])
    title = row.get("title") if row.get("title") and row.get("title_stage") == stage else ""
    return {"name": faction["name"], "kind": faction.get("kind") or "", "stage": stage,
            "title": title or "", "note": faction.get("note") or "", "towns": towns,
            "relations": relations, "cautions": cautions_of(ledger, faction_id, laws)}


def follow_of(ledger, event):
    """噂の下に添える一文。段が変わればその関係を段の色で、変わらなければ向きだけを書く。"""
    a, b = _name(ledger, event.get("a")), _name(ledger, event.get("b"))
    before, after = event.get("before"), event.get("after")
    direction = event.get("direction") or 0
    if isinstance(before, int) and isinstance(after, int) and before != after:
        return [("これにより、{}と{}は".format(a, b), None),
                ("{}関係".format(rules.RELATION_NAMES.get(after, "")), after), ("となったようだ。", None)]
    floor = rules.PEACEFUL_FLOOR if ledger.get("peaceful") else rules.RELATION_MIN
    if direction < 0:
        at_limit = isinstance(after, int) and after <= floor
        text = "の溝は一層深まったようだ。" if at_limit else "の関係には悪化の兆しが見えるようだ。"
    else:
        at_limit = isinstance(after, int) and after >= rules.RELATION_MAX
        text = "の結束は一層強まったようだ。" if at_limit else "の関係には改善の兆しが見えるようだ。"
    return [("これにより、{}と{}{}".format(a, b, text), None)]


def pieces_of(ledger, entry):
    """来歴の一文。段階が変わったときは段階の名前を段階の色で書く。"""
    name = _name(ledger, entry.get("faction"))
    before, after = entry.get("before", 0), entry.get("after", 0)
    stage = rules.standing_stage(after)
    changed = rules.standing_stage(before) != stage
    kind = entry.get("kind")
    if kind == rules.RECONCILE:
        return [("説得が実り、{}の敵意に和らぐ余地が生まれたようだ。".format(name), None)]
    if kind == rules.INCIDENT:
        lead = "{}を解決したことで、".format(entry.get("incident") or "街の出来事")
    elif kind == rules.ECHO:
        lead = "{}への働きが伝わり、".format(_name(ledger, entry.get("by")))
    else:
        lead = "会話により、"
    if changed:
        return [("{}{}の態度は".format(lead, name), None), (rules.STANDING_NAMES[stage], stage),
                ("となった。", None)]
    return [("{}{}の評価が{}。".format(lead, name, "上がった" if after > before else "下がった"), None)]


def build(ledger, *, day=None, here=None, area_names=None, laws=None):
    """窓の中身。`here` はいまいる土地の id、`area_names` は `{土地 id: 名前}`、`laws` は `{土地 id: 手配度}`。"""
    ledger = ledger if isinstance(ledger, dict) else {}
    area_names = area_names or {}
    status = ledger.get("status")
    if status != rules.READY:
        return {"status": status or "building", "message": MESSAGES.get(status, MESSAGES[None]),
                "here": "", "cards": [], "rumors": [], "history": []}
    here_text = ""
    if here is not None:
        here_text = area_names.get(str(here), "")
        owner = rules.faction_of_town(ledger, str(here))
        if here_text and owner:
            here_text += "（{}の影響下）".format(_name(ledger, owner))
    cards = [card_of(ledger, faction, area_names, laws or {}) for faction in ledger.get("factions") or []]
    rumors = []
    for event in reversed(ledger.get("events") or []):
        if not isinstance(event, dict) or not event.get("rumor") or not _recent(day, event.get("day")):
            continue
        rumors.append({"when": when_text(day, event.get("day")), "text": event["rumor"],
                       "follow": follow_of(ledger, event)})
    history = []
    for entry in reversed(ledger.get("history") or []):
        if not isinstance(entry, dict) or not _recent(day, entry.get("day")):
            continue
        history.append({"when": when_text(day, entry.get("day")), "pieces": pieces_of(ledger, entry),
                        "reason": entry.get("reason") or ""})
    return {"status": rules.READY, "message": "", "here": here_text, "cards": cards, "rumors": rumors,
            "history": history}
