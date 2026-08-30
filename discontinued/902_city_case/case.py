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
だから状態は `out/` に持ち、**渡すプログラムをその都度組む**。
"""

from instantale_modloader import read_json, write_json

#: 段階。
#: 「揃った」段階は無い。
#: いつでも告発できる。
NONE = "none"
INVESTIGATING = "investigating"
CLOSED = "closed"


def empty():
    return {"stage": NONE}


def load(path):
    """控えを読む。壊れていたら記録してから無かったことにする。

    ここで例外にすると、控えが1文字壊れただけで MOD が丸ごと死ぬ。
    事件は作り直せるので、読めないなら捨てるほうが損害が小さい。
    ただし「無い（初回）」と「在るのに読めない」は `read_json` が区別して、
    後者はログに残す。
    消えたことが後から追えるように。
    """
    data = read_json(path)
    return data if isinstance(data, dict) and "stage" in data else empty()


def save(path, case):
    # 隣に書いてから差し替える（`write_json`）。
    # 素朴な open(..., "w") だと書いている最中に落ちた瞬間に事件が消える。
    # 親フォルダも作ってくれる。
    return write_json(path, case, indent=2)


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


def close(case, accused):
    """告発の決着。一度きり。外したらそこで終わり。"""
    case["accused"] = str(accused)
    case["stage"] = CLOSED
    case["solved"] = str(accused) == str(case.get("culprit"))
    return case["solved"]


def is_active(case):
    return case.get("stage") == INVESTIGATING
