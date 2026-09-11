# -*- coding: utf-8 -*-
"""事件そのもの: 犯人・手がかり・容疑者。ゲームのことを何も知らない。

ここが持つのは「いま何が分かっているか」だけ。
施設の引き当ても NPC の名前も扱わない（`world.py` の役目）し、
文言も特徴の語彙も持たない（入口の役目）。

##### 手がかりは「行き先」ではなく「条件」

最初の版は手がかりを一本道の鎖にしていた（`requires` で順番を固定し、
揃うまで告発できない）。
実機で遊ぶと推理が一切要らなかった。
言われた場所へ行ってボタンを押すだけの作業になる。

いまの形はこう。

- 手がかりは犯人についての事実（「両手で荷を抱えていた」）
- 事実は容疑者を除外する（片腕の者は当てはまらない）
- 順番は無い。
  どこから調べてもよい
- いつでも告発できる。
  2つで絞れたなら3つ目は要らない

除外の結果は控えに持つが、画面には出さない。
事実と容疑者の特徴を並べて、突き合わせるのはプレイヤーの仕事。
そこを肩代わりすると、また作業に戻る。

##### なぜ状態を自分で持つのか

エンジンのフラグ（`flag_set`）は施設ローカルで、
町を回る話には足りない（GAME.md §2.21.3）。
加えて施設の `config` に書かれてセーブに残る。
だから状態は `state/` に持ち、**渡すプログラムをその都度組む**。

出し入れはローダの `state.WorldStore` が持つ（`state/city_case/<世界>.json`）。
読めなかったときに空へ倒すか記録を残すかの判断も、
壊れないように書く手順もあちらに1つだけ在る。
"""

#: 段階。
#: 「揃った」段階は無い。
#: いつでも告発できる。
NONE = "none"
INVESTIGATING = "investigating"
CLOSED = "closed"


def empty():
    return {"stage": NONE}


def build(world_name, area_id, culprit, suspects, clues, reward):
    """事件を1件組む。答えは最初に決めきる（Shadow of Doubt 型）。

    後から決めると、AI の出力次第で真相が変わってしまう。
    真相を先に固定しておけば、AI は描写しかできない。

    `suspects` は `{"id": ..., "tell": ...}` の並び。
    `tell` は見て分かる特徴で、プレイヤーが手がかりと突き合わせる材料になる。
    """
    return {
        "stage": INVESTIGATING,
        "world": world_name,
        "area": str(area_id),
        "culprit": str(culprit),
        "suspects": [{"id": str(s["id"]), "tell": s.get("tell", ""),
                      "claim": s.get("claim", ""), "heard": False}
                     for s in suspects],
        "clues": [dict(clue, found=False) for clue in clues],
        "reward": int(reward),
        "accused": None,
        "solved": None,
        # 告発の控え。
        # 進行中の古い事件にはこの3つが無いので、読む側は必ず既定値を持つこと
        # （`thin_count` / `judge` / `close`）。
        # 項目を足したときに既にある控えをどうするか決めないと、
        # 「新しい事件は動くのに続きは永久に動かない」という壊れ方になる（DOC.md §3）。
        "evidence": [],
        "verdict": None,
        "thin": 0,
    }


def belongs_to(case, world_name):
    """この控えがいまの世界のものか。違うなら使わない。"""
    return bool(case) and case.get("world") == world_name


def suspect_ids(case):
    return [s["id"] for s in case.get("suspects", [])]


def suspect_by_id(case, npc_id):
    for suspect in case.get("suspects", []):
        if str(suspect.get("id")) == str(npc_id):
            return suspect
    return None


def heard_claims(case):
    """聞いた言い分。覚えていられないので控える。"""
    return [s for s in case.get("suspects", []) if s.get("heard")]


def clue_by_id(case, clue_id):
    for clue in case.get("clues", []):
        if clue.get("id") == clue_id:
            return clue
    return None


def clue_by_label(case, label):
    """シーンの飛び先ラベルから手がかりを引く。

    判定の入口はここ。
    プレイヤーが押した選択肢の飛び先が `resume` に入って戻ってくるので（GAME.md
    §2.21.1）、それをそのまま鍵にする。
    AI もエンジンも介さない。
    """
    for clue in case.get("clues", []):
        if clue.get("label") == label:
            return clue
    return None


def pending_at(case, facility_type):
    """その施設でまだ拾っていない手がかり（無ければ None）。

    前提は見ない。
    どこから調べてもよい。
    順番を強制すると、プレイヤーは道順をなぞるだけになる。
    """
    for clue in case.get("clues", []):
        if not clue.get("found") and clue.get("at_type") == facility_type:
            return clue
    return None


def found_clues(case):
    return [c for c in case.get("clues", []) if c.get("found")]


def found_count(case):
    return len(found_clues(case))


def total_count(case):
    return len(case.get("clues", []))


def mark_found(case, clue_id):
    """手がかりを立てる。戻り値は変化の有無。"""
    clue = clue_by_id(case, clue_id)
    if clue is None or clue.get("found"):
        return False
    clue["found"] = True
    return True


def eliminated(case, skip=None):
    """いま分かっている事実で除外された容疑者の id。

    画面には出さない。
    突き合わせるのはプレイヤーの仕事で、そこを肩代わりすると推理が消える。
    ここは「解けるかどうか」を測るためと、ログのためにある。

    `skip` にひとつ id を渡すと、その手がかりを無かったことにして数える（思い違いを見つけるのに使う。
    `without_each`）。

    犯人を除く細工はしない。
    真の事実は犯人を消さない（事実は犯人についてのものなので当たり前）ので、
    以前は保険で引いていた。
    思い違いの証言はわざと犯人を消すので、引いてしまうとその食い違いが見えなくなる。
    """
    out = set()
    for clue in found_clues(case):
        if skip is not None and clue.get("id") == skip:
            continue
        out.update(str(npc_id) for npc_id in clue.get("eliminates", []))
    return out


def remaining(case, skip=None):
    """まだ除外されていない容疑者の id。1人になったら解ける。"""
    gone = eliminated(case, skip=skip)
    return [npc_id for npc_id in suspect_ids(case) if npc_id not in gone]


def solvable(case):
    """いま持っている事実だけで1人に絞れているか。"""
    return len(remaining(case)) == 1


def contradicted(case):
    """集めた話を**すべて信じると誰も当てはまらない**か。

    思い違いの証言が1つ混じっている合図。
    **「解けない」ではなく「1つが間違っている」**という意味なので、詰みとは違う。
    """
    return bool(found_clues(case)) and not remaining(case)


def without_each(case):
    """手がかりを1本ずつ外したとき、残る人数。`{手がかりの id: 残り}`。

    食い違ったときに「どれが思い違いか」を決めるための道具。
    答えは画面に出さない。
    外して数えるのはプレイヤーの仕事で、
    ここは事件を組むときに「一意に決まるか」を検算するために使う。
    """
    return {clue["id"]: remaining(case, skip=clue["id"])
            for clue in found_clues(case)}


# ---------------------------------------------------------------- 告発の根拠
#: 告発の判定。
#: 3値にしてあるのは、**「犯人を当てた」と「追い詰めた」を分ける**ため。
#: 犯人を選ぶだけの2値だと、外したときに世界へ残せるものが「間違えた」しか無い。
SOLVED = "solved"       # 挙げた根拠だけで1人に絞れていて、それが犯人
MISTAKEN = "mistaken"   # 挙げた根拠だけで1人に絞れているが、それは犯人ではない
THIN = "thin"           # その根拠では1人に絞れていない。決着しない


def evidence(case):
    """根拠として挙げられる材料。集めた手がかりと、聞いた言い分。

    **手がかりの本数は増やさない。**
    根拠を複数選ばせるには材料が要るが、そのために手がかりを増やすと
    2026-08-04 に潰した「拾う意味の無い手がかり」が戻る（DOC.md §3）。
    言い分は既に人数ぶん集まっていて、いままで推理の材料として数えていなかっただけ。
    """
    out = []
    for clue in found_clues(case):
        out.append({"id": str(clue.get("id")), "kind": "clue",
                    "text": clue.get("fact", "")})
    for suspect in heard_claims(case):
        out.append({"id": "s" + str(suspect.get("id")), "kind": "claim",
                    "npc": str(suspect.get("id")),
                    "text": suspect.get("claim", "")})
    return out


def evidence_ids(case):
    return [item["id"] for item in evidence(case)]


def drops_of(case, item):
    """その材料1つが除外する容疑者の id。

    手がかりは控えに書いてある除外先をそのまま使う。

    言い分は**裏が取れるなら語った本人が消える**。
    犯人だけが誰にも裏の取れない場所を挙げる（`patterns/whereabouts.json` の
    `alone`）ので、犯人の言い分では誰も消えない。
    裏が取れるかどうかを控えに項目として足す必要は無い。
    配るときに犯人だけ `alone` から引いているので、`id != culprit` がそのまま答えになる。
    """
    if item.get("kind") == "clue":
        clue = clue_by_id(case, item["id"])
        return {str(npc_id) for npc_id in (clue or {}).get("eliminates", [])}
    npc_id = str(item.get("npc"))
    return set() if npc_id == str(case.get("culprit")) else {npc_id}


def remaining_with(case, picks):
    """挙げた根拠だけで残る容疑者。集めたもの全部ではない。

    `remaining()` との違いは見る範囲だけ。
    あちらは「いま何が分かっているか」で、こちらは**プレイヤーが根拠として挙げたもの**。
    """
    chosen = {str(pick) for pick in (picks or ())}
    gone = set()
    for item in evidence(case):
        if item["id"] in chosen:
            gone |= drops_of(case, item)
    return [npc_id for npc_id in suspect_ids(case) if npc_id not in gone]


def judge(case, accused, picks):
    """告発の判定。`SOLVED` / `MISTAKEN` / `THIN` のどれか。

    ##### 順番が肝心。根拠を先に見る

    「相手は合っているが根拠が足りない」を独立した結果にすると、
    **その結果が出たこと自体が「相手は合っている」と教えてしまう**。
    総当たりで名前を1人ずつ試せば犯人が割れる。

    だから先に根拠だけを見る。
    1人に絞れていなければ、誰を名指ししていても `THIN`。
    プレイヤーが自分で数えられることしか言っていないので、何も漏れない。

    ##### 誤認は思い違いを信じたときにだけ起きる

    真の事実は犯人を消さない（事実は犯人についてのものなので当たり前）。
    言い分も犯人のものだけ裏が取れない。
    だから真の材料だけで絞ると、残るのは必ず犯人になる。

    無実の者へ辿り着けるのは、**思い違いの証言を根拠に挙げたときだけ**。
    `MISTAKEN` は当てずっぽうの結果ではなく、筋の通った誤りになる。
    """
    left = remaining_with(case, picks)
    if left != [str(accused)]:
        return THIN
    return SOLVED if str(accused) == str(case.get("culprit")) else MISTAKEN


def note_thin(case):
    """根拠が通らなかった回数を数える。

    `THIN` では事件が終わらないので、何度でも出し直せる。
    そのままだと根拠の総当たりができてしまうので、回数だけは控えに残す。
    """
    case["thin"] = int(case.get("thin", 0) or 0) + 1
    return case["thin"]


def thin_count(case):
    return int(case.get("thin", 0) or 0)


def close(case, accused, picks=(), verdict=None):
    """告発の決着。一度きり。外したらそこで終わり。

    `verdict` を渡さなかったときは犯人の照合だけで決める（古い呼び方）。
    `solved` は**残す**。
    決着した事件を読む側（後始末・ログ）が見ているのはこの2値で、
    3値が要るのは告発の場面だけ。
    """
    if verdict is None:
        verdict = (SOLVED if str(accused) == str(case.get("culprit"))
                   else MISTAKEN)
    case["accused"] = str(accused)
    case["evidence"] = sorted(str(pick) for pick in (picks or ()))
    case["verdict"] = verdict
    case["stage"] = CLOSED
    case["solved"] = verdict == SOLVED
    return case["solved"]


def is_active(case):
    return case.get("stage") == INVESTIGATING
