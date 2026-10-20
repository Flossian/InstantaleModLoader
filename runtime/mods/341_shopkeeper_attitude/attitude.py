# -*- coding: utf-8 -*-
"""店主が主人公にどう応じるか（値段の向き）の決まり。ゲームに触らない部品。仕様は DOC.md。

場面は2つ。手配中の土地（手配を数える MOD がローダの窓口 `wanted` に店の値の圧を置いているとき）と、平時。
応じ方は場面ごとに3通りで、どちらも 0 が主人公に得、1 が素の値、2 が主人公に損。

| 場面 | 0 | 1 | 2 |
|---|---|---|---|
| 手配中 `wanted` | 怯える | 動じない | 足元を見る |
| 平時 `usual` | 負ける | 普段どおり | ふっかける |

根拠が無いときの答えは、手配中は 0（怯える。手配を数える MOD の素の動きと同じ）、平時は 1（普段どおり）。
"""

WANTED, USUAL = "wanted", "usual"
FAVOR, NEUTRAL, AGAINST = 0, 1, 2
NAMES = {
    WANTED: {FAVOR: "cowed", NEUTRAL: "unmoved", AGAINST: "gouging"},
    USUAL: {FAVOR: "discount", NEUTRAL: "usual", AGAINST: "overcharge"},
}
#: 根拠が無いときの答え（頼みの文と、判定が無いときの決め方の両方で使う）。
DEFAULT = {WANTED: FAVOR, USUAL: NEUTRAL}

#: 店主の反応の1文の上限（字）。
LINE_LIMIT = 120
#: 判定の理由をログに残す上限（字）。
REASON_LIMIT = 80

NOTICES = {
    WANTED: {FAVOR: "手配書の顔に気づいた店主は、怯えたように値を改めた。",
             NEUTRAL: "{name}は手配書の顔に気づいたが、いつもどおりの値で応じてくれた。",
             AGAINST: "手配書の顔に気づいた店主は、足元を見るように値を吊り上げた。"},
    USUAL: {FAVOR: "{name}は少し値を負けてくれた。",
            NEUTRAL: "",
            AGAINST: "{name}は値をふっかけてきた。"},
}
#: 値段の動きの括弧書き。0 は買値が下がり売値が上がる。2 は逆。
RATE_TEXT = {FAVOR: ("（買値 -{pct}%{sell}）", "・売値 +{pct}%"),
             AGAINST: ("（買値 +{pct}%{sell}）", "・売値 -{pct}%")}

PLAYER_BLOCK = """【プレイヤー】
- 名前: {player}
- プロフィール: {player_profile}{wanted_line}"""
KEEPER_BLOCK = """【店主】
- 名前: {keeper}
- プロフィール: {profile}
- 性格: {personality}
- 口調: {speech}
- プレイヤーとの関係: {relation}
- プレイヤーへの感情: {feeling}（好感度 {affinity}）
- プレイヤーと会話した回数: {count}

【店主の人生ログと今日の記録】
{life_log}"""
WANTED_LINE = "\n- {area}での手配: 手配度 {lawfulness}（平常は 10。0 を下回ると手配されている。手配の重さ {weight}）"

PROMPTS = {
    WANTED: """あなたは TRPG の GM の補佐だ。
{area}で手配されているプレイヤーが、{keeper}の店に入ってきた。
{keeper}がプレイヤーの手配に気づいたうえで、どう応じるかを判定しろ。

{player_block}

{keeper_block}

【判定】
stance に 0〜2 の整数で答えること。
- 0: 怯える。手配された者を恐れ、値を下げてでも早く帰ってほしい
- 1: 動じない。肝が据わっている、手配を気にしない、プレイヤーと親しい・恩があるなどの理由で、普段どおりの値で応じる
- 2: 足元を見る。手配された者はほかの店へ行けないと見て、売る値を吊り上げ、買い取る値を叩く
1 か 2 にするのは、性格・関係・記録のどれかに根拠があるときだけ。根拠が無ければ 0 とすること。
reason: 判定の根拠を1文で書くこと（性格・関係・記録のどれに拠ったか）。
line: {keeper}の応じ方を地の文で1文。主語は{keeper}。口調を映した短い台詞を「」で入れてよい。値段の数字は書かないこと。""",
    USUAL: """あなたは TRPG の GM の補佐だ。
プレイヤーが{area}の{keeper}の店に入ってきた。
{keeper}がプレイヤーにどんな値で応じるかを判定しろ。

{player_block}

{keeper_block}

【判定】
stance に 0〜2 の整数で答えること。
- 0: 負ける。プレイヤーを気に入っている、恩がある、常連として大事にしているなどの理由で、売る値を下げ、買い取る値を上げる
- 1: 普段どおり。誰に対しても同じ値で応じる
- 2: ふっかける。プレイヤーを嫌っている、見下している、よそ者やカモと見たなどの理由で、売る値を吊り上げ、買い取る値を叩く
0 か 2 にするのは、性格・関係・記録のどれかに根拠があるときだけ。根拠が無ければ 1 とすること。
reason: 判定の根拠を1文で書くこと（性格・関係・記録のどれに拠ったか）。
line: {keeper}の応じ方を地の文で1文。主語は{keeper}。口調を映した短い台詞を「」で入れてよい。値段の数字は書かないこと。""",
}

#: 頼みの型（`llm.create_structure` に渡す）。理由を先に書かせる。
FIELDS = {"reason": (str, ...), "stance": (int, ...), "line": (str, ...)}


def clamp_stance(value):
    """答えの stance を応じ方へ。読めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if isinstance(value, float) and float(value).is_integer():
        value = int(value)
    return value if value in (FAVOR, NEUTRAL, AGAINST) else None


def clean(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def parse(data):
    """LLM の答え（辞書）を `(応じ方, 理由, 1文)` に。読めなければ None。"""
    if not isinstance(data, dict):
        return None
    value = clamp_stance(data.get("stance"))
    if value is None:
        return None
    return value, clean(data.get("reason"), REASON_LIMIT), clean(data.get("line"), LINE_LIMIT)


def affinity_of(character):
    """人物のプレイヤーへの好感度（`relationship["player"]["affinity"]`）。読めなければ None。"""
    relationship = getattr(character, "relationship", None)
    row = relationship.get("player") if isinstance(relationship, dict) else None
    value = row.get("affinity") if isinstance(row, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def fallback(mode, affinity, threshold):
    """判定が無いときの応じ方。手配中は好感度が `threshold` 以上なら動じない、下なら怯える。平時は普段どおり。"""
    if mode == WANTED and affinity is not None and affinity >= threshold:
        return NEUTRAL
    return DEFAULT[mode]


def fresh(entry, mode, lawfulness, affinity, day, days):
    """控えた判定が今も使えるか。

    場面と好感度が同じで、`days` 日より前でないこと。手配中は手配度も同じであること。
    """
    if not isinstance(entry, dict) or clamp_stance(entry.get("stance")) is None:
        return False
    if entry.get("mode") != mode or entry.get("affinity") != affinity:
        return False
    if mode == WANTED and entry.get("lawfulness") != lawfulness:
        return False
    judged = entry.get("day")
    if not isinstance(day, int) or not isinstance(judged, int) or isinstance(judged, bool):
        return False
    return 0 <= day - judged < max(1, int(days))


def price(buy_key, sell_key, key, value, rate, stance, sell_too):
    """応じ方に合わせた値段。触らないなら None。

    0（得）: 店の品（`buy_key`）が下がり、売りに出した品（`sell_key`）が上がる。
    2（損）: その逆。1: 触らない。
    """
    if rate <= 0 or value is None or stance not in (FAVOR, AGAINST):
        return None
    sign = 1.0 if stance == FAVOR else -1.0
    if key == buy_key:
        return value * (1.0 - sign * rate)
    if key == sell_key and sell_too:
        return value * (1.0 + sign * rate)
    return None


def rate_text(stance, pct, sell_too):
    """値段の動きの括弧書き。素の値なら空。"""
    if stance not in RATE_TEXT:
        return ""
    head, sell = RATE_TEXT[stance]
    return head.format(pct=pct, sell=sell.format(pct=pct) if sell_too else "")


def notice(mode, stance, pct, sell_too, name, line=""):
    """売買を押したときの1行。LLM の1文があればそれを、無ければ決まった文を使い、値段の動きを添える。

    平時の普段どおりで1文も無ければ空（何も出さない）。
    """
    head = line or NOTICES[mode][stance].format(name=name or "店主")
    if not head:
        return ""
    return head + rate_text(stance, pct, sell_too)


def joined(value):
    """文字列か文字列の並び（感情の文・関係の印）を1行に。"""
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value if v) or "なし"
    return str(value) if value else "なし"


def prompt(mode, area, player, player_profile, lawfulness, weight, keeper, profile, personality,
           speech, relation, feeling, affinity, count, life_log):
    keeper = keeper or "店主"
    area = area or "この土地"
    wanted_line = WANTED_LINE.format(area=area, lawfulness=lawfulness, weight=weight) if mode == WANTED else ""
    player_block = PLAYER_BLOCK.format(player=player or "プレイヤー", player_profile=player_profile or "不明",
                                       wanted_line=wanted_line)
    keeper_block = KEEPER_BLOCK.format(
        keeper=keeper, profile=profile or "不明", personality=personality or "不明", speech=speech or "不明",
        relation=joined(relation), feeling=joined(feeling),
        affinity=affinity if affinity is not None else "不明",
        count=count if isinstance(count, int) and not isinstance(count, bool) else 0,
        life_log=life_log or "なし")
    return PROMPTS[mode].format(area=area, keeper=keeper, player_block=player_block, keeper_block=keeper_block)
