# -*- coding: utf-8 -*-
"""裏の仕事（裏の事務所で受ける違法な依頼）の決まり。ゲームに触らない部品。

依頼の形はゲームの通常の依頼と同じダンジョン（道中のイベント・敵・ボス）で、
種類ごとに「何をする依頼か」「敵とボスに何を立てるか」を生成の頼み文へ差し込む。
"""

import re

#: 種類の表。並びは解禁の軽い順。
#: `unlock` は解禁に要る手配の重さの合計（`wanted.total_weight`）、`mult` は報酬の倍率、
#: `loss` はクリアしたときに依頼の街で下がる手配度。設定の調整（%）はこれに掛ける。
KINDS = (
    {"key": "smuggling", "name": "密輸", "unlock": 0, "mult": 5, "loss": 10,
     "brief": "禁制品を目的の場所まで運び込む。道中には検問と巡回の兵が待ち受ける",
     "enemies": "検問の兵・巡回の兵・関所の番犬など", "boss": "関所の隊長"},
    {"key": "grave_robbing", "name": "盗掘", "unlock": 0, "mult": 5, "loss": 10,
     "brief": "立ち入りを禁じられた墓所や封じられた遺跡を荒らし、副葬品を持ち出す",
     "enemies": "墓守・遺跡の番人・眠りを妨げられた死者など", "boss": "墓所や遺跡の守護者"},
    {"key": "sabotage", "name": "破壊工作", "unlock": 10, "mult": 7, "loss": 20,
     "brief": "倉庫・砦・水門などに忍び込み、焼き討ちか爆破で使い物にならなくする",
     "enemies": "警備兵・見張り・番兵など", "boss": "守備の責任者"},
    {"key": "jailbreak", "name": "脱獄の手引き", "unlock": 10, "mult": 7, "loss": 20,
     "brief": "牢獄へ潜り込み、捕らわれた者を連れ出す。帰り道も追手がかかる",
     "enemies": "看守・獄卒・牢の番兵など", "boss": "典獄長"},
    {"key": "robbery", "name": "強盗", "unlock": 20, "mult": 8, "loss": 20,
     "brief": "商家の屋敷・商隊・金庫に押し入り、財貨を奪う",
     "enemies": "衛兵・私兵・用心棒など", "boss": "金庫番か警護隊長"},
    {"key": "assassination", "name": "暗殺", "unlock": 40, "mult": 10, "loss": 30,
     "brief": "標的の屋敷や隠れ家へ潜り込み、標的を始末する",
     "enemies": "護衛・刺客・番兵など", "boss": "標的本人かその護衛長"},
)

KIND_BY_KEY = {kind["key"]: kind for kind in KINDS}

#: 依頼の概要の末尾に足す1行の目印。何度足しても二重にしない。
NOTE_MARK = "【裏の仕事："
NOTE_TEXT = "\n\n【裏の仕事：{name}】報酬は表の相場の{mult}倍。片付ければ{town}で手配される（手配度 -{loss}）。"

BRIEF_TEXT = (
    "\n\n【裏の仕事】この依頼はギルドを通さない裏社会の依頼として作ること。"
    "依頼人は{town}の裏の事務所を仕切る{broker}。"
    "種類は「{name}」：{brief}。"
    "依頼の中身は違法で、成功すれば{town}の官憲に追われることになる。"
    "quest_title は裏の仕事らしい名前にし、client_name は{broker}とすること。"
    "enemies には{enemies}を、boss には{boss}を立てること。"
    "依頼人の言葉（client_statement）は表の依頼のような丁寧な頼みではなく、裏の取引の口ぶりにすること。"
)

#: 帰還の報酬の文（`(数)ゴールドの報酬を受け取った。` GAME.md §2.29）の目印。
#: 通貨の呼び名は `130_` が変えうるので、単位ではなくこちらで見る。
REWARD_MARK = "の報酬を受け取った"
_AMOUNT = re.compile(r"([0-9][0-9,]*)")


def scaled(value, pct):
    """表の値に設定の調整（%）を掛ける。0 未満にしない。"""
    return max(0, int(round(value * max(0, pct) / 100.0)))


def unlocked(total_weight, unlock_pct):
    """いまの手配の重さの合計で受けられる種類（表の並び）。"""
    weight = max(0, int(total_weight or 0))
    return [kind for kind in KINDS if weight >= scaled(kind["unlock"], unlock_pct)]


def multiplier(kind, reward_pct):
    """報酬の倍率（設定の調整込み）。1 未満にしない。"""
    return max(1.0, kind["mult"] * max(0, reward_pct) / 100.0)


def mult_text(value):
    """画面に出す倍率。整数なら整数で。"""
    return str(int(value)) if float(value).is_integer() else "{:.1f}".format(value)


def brief(kind, town, broker):
    """生成の頼み文（`area_description`）の末尾に足す文。"""
    return BRIEF_TEXT.format(town=town or "この街", broker=broker or "素性を明かさない人物",
                             name=kind["name"], brief=kind["brief"],
                             enemies=kind["enemies"], boss=kind["boss"])


def note(kind, town, mult, loss):
    """依頼の概要の末尾に足す1行。"""
    return NOTE_TEXT.format(name=kind["name"], mult=mult_text(mult),
                            town=town or "この街", loss=loss)


def reward_amount(text):
    """報酬の文から額を読む。報酬の文でなければ None。"""
    if not isinstance(text, str) or REWARD_MARK not in text:
        return None
    head = text.split(REWARD_MARK, 1)[0]
    found = _AMOUNT.findall(head)
    if not found:
        return None
    try:
        return int(found[-1].replace(",", ""))
    except ValueError:
        return None


def replace_amount(text, base, want):
    """文の中の額だけを置き換える。書けなければ None（桁区切りは元の書き方に合わせる）。"""
    if not isinstance(text, str):
        return None
    for form in ("{:,}".format(base), str(base)):
        if form in text:
            new = "{:,}".format(want) if "," in form else str(want)
            return text.replace(form, new, 1)
    return None
