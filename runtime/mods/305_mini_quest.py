# -*- coding: utf-8 -*-
"""戦闘を伴わないミニクエスト（採集・救助・偵察）。

掲示板に「軽い頼まれごとを探す」を1つ足す。押すとゲーム自身の依頼生成を呼び、
**討伐ではないお題**の依頼を1件作って登録する。受注も進行も帰還もゲーム本来の
クエスト機構がそのまま動く ― この MOD が触るのは**2つのプロンプトだけ**。

##### なぜプロンプトだけで足りるのか（2026-07-28 の実測。VERIFICATION §2.18）

クエストを1件、頭から終わりまで通して `203_` の記録を読んだ結果:

- **`ReturnAfterCompletion` は1ターン目から毎ターン作られている**（ボスが健在でも
  union に居る）。つまり「攻略しないと帰還できない」は**スキーマではなく地の文の
  指示**。referee の system に `return_after_completion: クエストを攻略した後にのみ
  実行可能` と書いてあるだけ
- 同様に「ラスボス撃破か撤退でのみ終了」「戦闘ベースのRPGなので field_event よりも
  battle が多くなるように」も全て地の文

したがって**討伐前提が書かれている2箇所**（生成 `random_quest_generator` と進行判定
`quest_referee*`）を差し替えれば、ゲームのコードにもセーブにも触らずに成立する。

##### 敵とボスのデータは削らない

`Battle.enemies` / `EncounterFinalBoss.enemies` は**残りの敵から作られる
`Literal`**（実測で `Literal[7→6→5→1]` と減っていく）。`crash_log.txt` には
`AssertionError: literal "expected" cannot be empty, typing.Literal[]` が2件あり、
**敵候補0件が第一容疑のまま未解決**。

残イベントが尽きたとき `FieldEvent` モデルが union ごと消えたので「候補0件のモデルは
作らない」分岐は実在するが、**それを確認できたのは `FieldEvent` だけ**。`Battle` /
`EncounterFinalBoss` で 0 件を試すのは、既知の未解決クラッシュを自分から踏みに行く
ことになる。

> **だから敵は生成させたまま残し、戦わせないのはプロンプトで行う。**
> 空 `Literal` が生まれる状況を一度も作らないので、この MOD はそのバグと無関係でいられる。

生成された敵は「その土地に潜む危険」として設定は在るが、依頼の目的ではない。

##### アンカーは1メッセージに揃っていない

進行判定のプロンプトは system が2本＋user が1本で、**1文目（ラスボス宣言）は system、
クエスト名と【強制事項】は user** にある。したがって「どのクエストか」の判定は
`messages` 全体を繋いだもので行い、**書き換えは各メッセージに在るアンカーだけ**に
適用する。1メッセージ単位で判定すると、必ずどちらかが「見つからない」になる。

##### 置き換える文字列は実データで裏を取ってある

`output_data/` の記録（`quest_referee*` の 424 ターン、`*quest_generator` 113件）全件に
対して、下の `REFEREE_*` / `GEN_KIND_MARK` が **424/424・113/113 で存在**することを
確認済み。1つでも当たらなければ `mini_quest.log` に `missed:` が出る（＝ゲーム側の
文面が変わった合図）。**当たらなかった時は書き換えを丸ごと諦める** ― 中途半端に
混ざった指示を送るより、討伐クエストとして正しく動くほうがよい。

##### `PhaseSpec` に自前のクラス名を書かない

ボタンは `PhaseSpec.to_dict()` でセーブに焼かれうる。自前のクラス名を書くと MOD 無しの
次回起動で `getattr(__main__, ...)` が失敗する。無害な既存クラスを持たせ、押下は
`on_button_press` を包んでボタン辞書の印で横取りする（`301_` と同じ。印のキーは
別にすること ― 同じにすると押下を食い合う）。
"""

import datetime
import json
import time

from instantale_modloader import ui


NAME = "Non-combat mini quests"
NAME_JA = "戦闘なしミニクエスト"
VERSION = "1"
DESCRIPTION = "Adds non-combat mini quests (gather / rescue / scout) to the notice board"
DESCRIPTION_JA = "戦闘を伴わないミニクエスト（採集・救助・偵察）を掲示板に追加する"
AUTHOR = "R01/Flossian"

LOG_BASENAME = "mini_quest.log"

# どの依頼がこの MOD 製かの控え。**セーブには書かない**（クエスト辞書に独自キーを
# 足すとセーブに焼かれるうえ、再読み込み後に Quest がそのキーを持つ保証が無い）。
RECORD_BASENAME = "mini_quests.json"

# 掲示板に出すボタンの文言。
BOARD_LABEL = "軽い頼まれごとを探す"

# 押下を横取りするための印。**`301_`（mod_action）/ `302_`（mod_party_action）と
# 別のキーにすること。**
MARK = "mod_mini_action"

# 戦闘の扱い。
#   "none"  お題の達成だけで完結させる。battle も encounter_final_boss も出させない
#   "mobs"  雑魚との戦闘は道中の障害として許す。ラスボスとの邂逅だけ出させない
#           （＝「ボスなしの雑魚のみ」。ボスのデータ自体は残るので Literal は空にならない）
COMBAT_MODE = "none"

# お題の種類。順に選ばれる（乱数を使わないのは、どれが出たかログで読めるようにするため）。
# `label` が生成プロンプトの【討伐】と置き換わる。
KINDS = (
    {
        "key": "gather",
        "label": "採集",
        "objective": "指定された物を必要な数だけ集めること。",
        "brief": (
            "この依頼は**採集**である。指定の場所で特定の物（薬草・鉱石・落とし物・"
            "特産品など）を集めることが目的。何をいくつ集めるのかを request_summary で"
            "はっきり示すこと。"
        ),
    },
    {
        "key": "rescue",
        "label": "救助",
        "objective": "行方の分からない者を見つけ出し、安全な場所まで連れ出すこと。",
        "brief": (
            "この依頼は**救助**である。行方不明者・遭難者・迷子（人でも家畜でもよい）を"
            "見つけて連れ帰ることが目的。誰がどこで行方を絶ったのかを request_summary で"
            "はっきり示すこと。討伐や仇討ちにしないこと。"
        ),
    },
    {
        "key": "scout",
        "label": "偵察",
        "objective": "目的の場所まで到達して状況を確かめること。",
        "brief": (
            "この依頼は**偵察**である。ある場所の様子を確かめて報告することが目的。"
            "何を確かめてほしいのかを request_summary ではっきり示すこと。"
            "「異変の元凶を討て」のような討伐依頼にしないこと。"
        ),
    },
)

# 進行側に「短い依頼である」と伝えるターン数の目安。
# 2026-07-28 の実機では、達成条件が曖昧だったせいで `nothing_happens` が12ターン
# 続いた。**判定条件と締切の両方が要る。**
TURN_BUDGET = 10

# 生成させるフィールドイベントの数。
# 素のプロンプトは「1から3個程度」で、実機では**2個しか作られず3ターン目で枯れた**。
# 戦闘を封じたぶんイベントが依頼の中身そのものになるので、多めに要る。
EVENT_COUNT = "4から6"

# 「目的を果たしたうえでの帰還」を達成として扱う。
#
# **プロンプトでこれを2回外している**（2026-07-28 の実機2回目と4回目）。どちらも
# referee 自身が「目標の数は既に十分」「目的の品は十分に確保されており」と描写
# しながら `retire_from_the_quest` を選んだ。素の説明文が
# 「プレイヤーがその意思を明確に示したときにのみ選択するべき」なので、
# **帰還の意思＝放棄**という読みが強く、書き換えても押し切られる。
#
# ミニクエストには倒すべきラスボスが居ない。**目的を果たした状態での帰還は
# 定義上そのまま達成**なので、戻り値の側で差し替える。文面での説得をやめて
# 判定そのものを持つ、という切り替え（TECH.md §9「ゲームの処理そのものを
# 起こさせない」の系統）。
#
# `False` にするとプロンプトの書き換えだけになり、撤退はゲームの判断のまま。
RETURN_INSTEAD_OF_RETIRE = True

# 差し替えの対象になる `turn_resolution` の type。
RETIRE_TYPE = "retire_from_the_quest"
RETURN_TYPE = "return_after_completion"

# 生成の印が古くなったら使わない（押してから生成が始まるまでの間に別の生成が
# 割り込んだ場合に、そちらへ付いてしまうのを防ぐ）。
INJECT_TTL = 300.0

# 控えの上限。世界ごとにこの件数を超えたら古いものから捨てる。
MAX_RECORDS = 200

# 生成後に画面を触るまでの落ち着き待ち（`300_` の「手が空くまで待つ」を使う）。
SETTLE = 0.4

# 書き換えの中身をログに全文残す回数（目で確かめるため。以後は件数だけ）。
AUDIT_FIRST_N = 3


# --------------------------------------------------------------------------
# 差し替える文字列（実データ全件で存在を確認済み。上の docstring 参照）
# --------------------------------------------------------------------------
# 生成側。前後が可変なので、種類の札そのものを置き換える。
GEN_HEADER = "あなたはRPGのクエストジェネレータです。"
GEN_KIND_MARK = "【討伐】"

# 進行側の1文目。ボス名が挟まるので、開始と終了の2つの目印で挟んで丸ごと置き換える。
REFEREE_HEAD_START = "現在クエストが進行中である。このクエストはラスボス"
REFEREE_HEAD_END = "を討伐することか、プレイヤーが攻略自体を諦めて撤退することのどちらかでのみ終了される。"

# 進行側の個別の指示。`(元の文字列, COMBAT_MODE ごとの置換)`。
REFEREE_RULES = (
    (
        "- return_after_completion: クエストを攻略した後にのみ実行可能。攻略として帰還する。",
        {
            "none": "- return_after_completion: 【このクエストの目的】が果たされた後にのみ実行可能。達成として帰還する。目的が果たされたと判断できるなら、ためらわずこれを選ぶこと。",
            "mobs": "- return_after_completion: 【このクエストの目的】が果たされた後にのみ実行可能。達成として帰還する。目的が果たされたと判断できるなら、ためらわずこれを選ぶこと。",
        },
    ),
    (
        "- ラスボスが残っている限りクエストは攻略完了しない。",
        {
            "none": "- このクエストにラスボスは登場しない。【このクエストの目的】が果たされた時点で攻略完了とする。",
            "mobs": "- このクエストにラスボスは登場しない。【このクエストの目的】が果たされた時点で攻略完了とする。",
        },
    ),
    (
        "- 戦闘ベースのRPGなので、field_eventよりもbattleが多くなるようにすること。",
        {
            "none": "- このクエストは戦闘を目的としない。battleは選ばず、field_eventや描写による進行で目的に近づけること。",
            "mobs": "- このクエストの主目的は戦闘ではない。battleは道中の障害としてのみ、控えめに発生させること。",
        },
    ),
    (
        "- 雑魚敵や中ボスがまだ残っている場合、プレイヤーがラスボスの元にたどり着く前に出来る限りそれらとの戦闘を発生させるよう試みること。",
        {
            "none": "- 敵との戦闘を無理に発生させないこと。土地に潜む危険として気配を描写するにとどめ、目的の達成に向けた進行を優先する。",
            "mobs": "- 敵は道中の障害である。目的の達成を妨げない範囲でのみ戦闘を発生させること。",
        },
    ),
    (
        "- 雑魚敵や中ボスがもはやいない場合、ラスボスと邂逅させること。",
        {
            "none": "- encounter_final_boss は決して選ばないこと。このクエストにラスボスは登場しない。",
            "mobs": "- encounter_final_boss は決して選ばないこと。このクエストにラスボスは登場しない。",
        },
    ),
    (
        "ラスボス以外に残り戦闘が無い場合には強制的にラスボス戦を開始する。",
        {"none": "", "mobs": ""},
    ),
    # 2026-07-28 の実機2回目で失敗した点。**達成したのに「撤退」になった。**
    # プレイヤーの入力は「採取を終了し、帰還の準備を整える」で、referee 自身も
    # 「目標の数は、既に十分な数に達している」と描写しながら
    # `retire_from_the_quest` を選んだ。素の説明文が
    # 「プレイヤーがその意思を明確に示したときにのみ選択するべき」なので、
    # **帰還の意思＝放棄** と読まれる。討伐クエストでは正しいが、お使いでは逆。
    (
        "- retire_from_the_quest: クエストを放棄し帰還する。プレイヤーがその意思を明確に示したときにのみ選択するべきだが、ふさわしい状況下（幽閉されているので脱出不可能、今まさに敵に襲撃を受けようとしているなど）では他の処理を選んでもいい。しかし具体的が無いならばさっさと撤退させること。",
        {
            "none": "- retire_from_the_quest: **目的を果たせないまま**クエストを放棄して帰還する。これは失敗として扱われる。**【このクエストの目的】が果たされているなら、プレイヤーが帰還・離脱・引き上げを望んでもこれを選んではならない。その場合は必ず return_after_completion を選ぶこと。** 目的が果たされていない状態で、プレイヤーが明確に諦めの意思を示したときにだけ選ぶ。",
            "mobs": "- retire_from_the_quest: **目的を果たせないまま**クエストを放棄して帰還する。これは失敗として扱われる。**【このクエストの目的】が果たされているなら、プレイヤーが帰還・離脱・引き上げを望んでもこれを選んではならない。その場合は必ず return_after_completion を選ぶこと。** 目的が果たされていない状態で、プレイヤーが明確に諦めの意思を示したときにだけ選ぶ。",
        },
    ),
    # 同じ取り違えを起こすもう1行。「エリア外への移動＝攻略を諦めての撤退」と
    # 定義してしまっているので、帰還そのものが失敗に寄る。
    (
        "- クエスト攻略を諦めての撤退以外では、クエストエリア外への移動は認めない。プレイヤーが明確に中断と帰還の意思を持つ場合のみ認められる。",
        {
            "none": "- クエストエリア外への移動は、帰還のときにのみ認められる。プレイヤーが帰還の意思を示したら、**まず【このクエストの目的】が果たされているかを判断すること。** 果たされているなら return_after_completion（達成としての帰還）、果たされていないなら retire_from_the_quest（放棄）である。",
            "mobs": "- クエストエリア外への移動は、帰還のときにのみ認められる。プレイヤーが帰還の意思を示したら、**まず【このクエストの目的】が果たされているかを判断すること。** 果たされているなら return_after_completion（達成としての帰還）、果たされていないなら retire_from_the_quest（放棄）である。",
        },
    ),
    # 2026-07-28 の実機1回目で最も効いていなかった穴。イベントの在庫が尽きたあと
    # `nothing_happens` が12ターン続き、ほぼ同じ描写が繰り返された。
    # 「何も起きない」を選ぶこと自体は正しくても、**同じ場所で足踏みし続けるのは
    # 進行の放棄**なので、そこを塞ぐ。
    (
        "- nothing_happens: 上述のどの処理も発生しない。プレイヤーが無理のある試みをした場合にもこれ。",
        {
            "none": "- nothing_happens: 上述のどの処理も発生しない。プレイヤーが無理のある試みをした場合にもこれ。ただし前のターンと似た描写を繰り返してはならない。進展の無い状況が2ターン続いたなら、【このクエストの目的】が果たされたものとして return_after_completion を選ぶこと。",
            "mobs": "- nothing_happens: 上述のどの処理も発生しない。プレイヤーが無理のある試みをした場合にもこれ。ただし前のターンと似た描写を繰り返してはならない。進展の無い状況が2ターン続いたなら、【このクエストの目的】が果たされたものとして return_after_completion を選ぶこと。",
        },
    ),
)

# 行の**先頭**で当てて、その行の末尾に書き足す規則。
#
# 2026-07-28 の実機3回目でこれが要ることが分かった。イベントも雑魚も尽きた終盤、
# 残る選択肢が実質「ラスボスとの battle」しか無くなり、そこへ流れて決着していた。
# `- battle:` の行は末尾の適正数だけが難易度で変わる（実データで 6 通り・合計
# 476/476）ので、**完全一致では当たらない**。行頭で当てて追記する。
#
# 追記にするのは、`- 残りラスボス戦闘:` のように**ゲームが握っている事実**の行を
# 書き換えたくないため（数や名前はゲームの状態そのもの。嘘を書かず、扱い方だけ足す）。
REFEREE_LINE_RULES = (
    (
        "- battle: 雑魚敵や中ボスやラスボスとの戦闘の開始。",
        {
            "none": " **このクエストでは battle を選ばないこと。とりわけラスボスを enemies に含めてはならない。**",
            "mobs": " **このクエストではラスボスを enemies に含めてはならない。** 雑魚のみとすること。",
        },
    ),
    (
        "- 残りラスボス戦闘: ",
        {
            "none": "（**このクエストでは戦わない。** 残っていても無視すること。これが残っているせいでクエストが終わらない、ということは無い）",
            "mobs": "（**このクエストでは戦わない。** 残っていても無視すること。これが残っているせいでクエストが終わらない、ということは無い）",
        },
    ),
)

# 進行プロンプトからクエスト名を読む目印。
REFEREE_TITLE_MARK = "- quest_title: "


def head_replacement(objective, summary=None):
    """進行プロンプトの1文目に置く文。**達成条件をここで具体的に宣言する。**

    2026-07-28 の実機で分かったこと ― 種類のテンプレ文（「指定された物を集める」）
    だけでは referee が達成を判定できず、`return_after_completion` を一度も
    選ばなかった。**そのクエストの `request_summary` をそのまま渡す**のが要点。

    在庫消化の圧力を打ち消す1文も要る。プロンプトには
    `- 残り通常戦闘回数` `- 残り中ボス戦闘` `- 残りラスボス戦闘` が事実として
    並び続けるので、明示的に「消化しなくてよい」と言わないと battle に流れる。
    """
    lines = [
        "現在クエストが進行中である。このクエストは以下の目的を果たすか、"
        "プレイヤーが達成を諦めて撤退することのどちらかでのみ終了される。",
        "【このクエストの目的】{}".format(objective),
    ]
    if summary:
        lines.append("【依頼の内容（達成条件はこれ）】{}".format(summary))
    lines.extend([
        "このクエストに討伐対象はいない。",
        "クエストエリアに残っている敵・中ボス・ラスボス・イベントを消化する必要は"
        "一切無い。在庫が残っていても、目的が果たされたなら帰還させてよい。",
        "これは短い依頼である。おおむね{}ターン以内に決着させること。"
        "同じ場所で足踏みを続けさせてはならない。".format(TURN_BUDGET),
        # 実機2回目の失敗。達成済みなのに帰還を「撤退」と解釈された。
        "プレイヤーが帰還・引き上げ・撤収の意思を示したときは、まず目的が"
        "果たされているかを判断すること。果たされているなら、それは撤退ではなく"
        "**達成としての帰還（return_after_completion）**である。",
    ])
    return "\n".join(lines)


def referee_anchors_missing(blob):
    """進行プロンプト（全メッセージを繋いだもの）に、目印が揃っているか。

    揃っていなければ、その名前の一覧を返す（空なら揃っている）。
    """
    missed = []
    if REFEREE_HEAD_START not in blob or REFEREE_HEAD_END not in blob:
        missed.append("head")
    for old, _table in REFEREE_RULES:
        if old not in blob:
            missed.append(old[:24])
    lines = blob.split("\n")
    for prefix, _table in REFEREE_LINE_RULES:
        if not any(line.startswith(prefix) for line in lines):
            missed.append(prefix[:24])
    return missed


def rewrite_referee_text(content, objective, combat_mode=None, summary=None):
    """1メッセージ分の書き換え。**そのメッセージに在る目印だけ**を置き換える。

    変化しなければ `None`（アンカーは複数のメッセージに散っているため、
    「このメッセージには何も無かった」は正常）。
    """
    mode = combat_mode or COMBAT_MODE
    new = content

    start = new.find(REFEREE_HEAD_START)
    if start >= 0:
        end = new.find(REFEREE_HEAD_END, start + 1)
        if end >= 0:
            new = (new[:start] + head_replacement(objective, summary)
                   + new[end + len(REFEREE_HEAD_END):])

    for old, table in REFEREE_RULES:
        replacement = table.get(mode)
        if replacement is None:
            continue
        # **置換後の文が元の文を含む項目がある**（`nothing_happens` は元の説明に
        # 書き足す形）。素朴に replace を繰り返すと、既に書き換わった文にもう一度
        # 当たって際限なく伸びる。実経路ではプロンプトは毎ターン組み直されるので
        # 一度しか通らないが、冪等でない書き換えを残す理由が無い。
        if replacement and replacement in new:
            continue
        new = new.replace(old, replacement)

    # 行頭で当てて末尾に書き足すもの。**同じ行に二度足さない**（`chat` は
    # 同じ会話履歴を何度も運ぶので、追記が積み上がると際限なく伸びる）。
    lines = new.split("\n")
    for index, line in enumerate(lines):
        for prefix, table in REFEREE_LINE_RULES:
            addition = table.get(mode)
            if not addition or not line.startswith(prefix):
                continue
            if line.endswith(addition):
                continue
            lines[index] = line + addition
    new = "\n".join(lines)

    return new if new != content else None


def rewrite_referee(blob, objective, combat_mode=None, summary=None):
    """目印の確認と書き換えをまとめて行う（自己検証とテスト用）。

    戻り値は `(新しい文字列またはNone, 当たらなかった目印)`。
    """
    missed = referee_anchors_missing(blob)
    if missed:
        return None, missed
    return rewrite_referee_text(blob, objective, combat_mode, summary), []


def rewrite_generator(prompt, kind):
    """生成プロンプトの【討伐】を、この依頼の種類に差し替える。

    差し替えるのは種類の札1個だけ。**敵の生成要素には触らない**（理由は
    モジュールの docstring）。お題の中身は `area_description` 側に足す。
    """
    if GEN_KIND_MARK not in prompt:
        return None
    return prompt.replace(GEN_KIND_MARK, "【{}】".format(kind["label"]))


def generator_brief(kind):
    """`area_description` に足す、お題の仕様。

    引数を足すのではなく既存の自由記述欄に載せるだけなので、出力スキーマ
    （`QuestStructure`）にも呼び出し側にも影響しない（`301_` と同じ手）。
    """
    return (
        "\n\n【この依頼の種類 — 最優先で反映すること】\n"
        "{brief}\n"
        "- request_summary と client_statement は、上記の目的を頼むものにすること。\n"
        "- request_summary には**何をどれだけ達成すれば依頼が完了するのか**を、"
        "誰が読んでも判定できる形で書くこと（例:「薬草を5株」「行方不明の娘を1人」）。\n"
        "- 敵の討伐を達成条件にしてはならない。\n"
        "- events（フィールドイベント）は**{events}個**設定すること。"
        "これがこの依頼の中身になるので、上記の目的を進めるうえで出会う出来事に"
        "すること。\n"
        "- enemies は normal のみとし、**miniboss は1体も設定しないこと**。"
        "boss は指示どおり1体設定する。これらは**その土地に潜む危険**であって"
        "依頼の目的ではない。依頼文で討伐を求めないこと。"
    ).format(brief=kind["brief"], events=EVENT_COUNT)


def _get(container, name):
    """dict でも属性でも読む。無ければ `None`。

    `quest_referee*` の戻り値の形は**実測できていない**（`output_data/` に
    残るのは保存側が整えた形で、関数が返したそのものではない）。dict と
    オブジェクトのどちらでも通るようにしておき、**どちらでもなければ何も
    しない**（推測して壊すより素通しするほうがよい）。
    """
    if isinstance(container, dict):
        return container.get(name)
    value = getattr(container, name, None)
    return value


def _set(container, name, value):
    """`_get` と対。書けたら True。"""
    try:
        if isinstance(container, dict):
            container[name] = value
            return True
        setattr(container, name, value)
        return getattr(container, name, None) == value
    except Exception:
        # pydantic の Literal で弾かれることがありうる。その場合は書けない。
        return False


def shape_of(value, depth=0):
    """戻り値の形を1行で。実測できていないので、最初の1回だけ記録する。"""
    if isinstance(value, dict):
        return "dict{" + ", ".join(sorted(value)[:8]) + "}"
    if isinstance(value, (list, tuple)):
        return "{}[{}]".format(type(value).__name__, len(value))
    if depth == 0:
        try:
            fields = sorted(vars(value))[:8]
        except Exception:
            fields = []
        return "{}({})".format(type(value).__name__, ", ".join(fields))
    return type(value).__name__


def retire_to_return(result, write=None):
    """`retire_from_the_quest` を `return_after_completion` に差し替える。

    差し替えたら `True`。対象でない・書けなかった場合は `False`（素通し）。
    """
    statement = _get(result, "game_master_statement")
    if statement is None:
        return False
    resolution = _get(statement, "turn_resolution")
    if resolution is None:
        return False
    if _get(resolution, "type") != RETIRE_TYPE:
        return False
    if not _set(resolution, "type", RETURN_TYPE):
        if write:
            write("retire->return: could not write type on {}".format(
                shape_of(resolution)))
        return False
    return True


def title_in(blob):
    """進行プロンプトからクエスト名を読む。無ければ None。"""
    at = blob.find(REFEREE_TITLE_MARK)
    if at < 0:
        return None
    line = blob[at + len(REFEREE_TITLE_MARK):].split("\n", 1)[0]
    return line.strip() or None


def _id_sort(value):
    try:
        return (0, int(value))
    except Exception:
        return (1, str(value))


def _text(value, limit=120):
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:
        return "<unprintable>"
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------
def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)

    state = {
        "pending": None,        # 生成待ちの印 {"kind": ..., "at": ...}
        "generating": False,
        "next_kind": 0,
        "titles": None,         # {クエスト名: (お題, 達成条件)}。控えから遅延で組む
        "rewrites": 0,
        "audited": {},          # 監査ログの枠。生成と進行で別に持つ
        "shape": None,          # quest_referee* の戻り値の形（実測できていない）
        "missed_logged": False,
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("mini quest: write failed")

    screen = ui.Screen(ctx, write, tag="mini quest", mark=MARK)

    # ------------------------------------------------------------ 控え
    def world_key(app):
        """世界を見分ける名前。取れなければ '_'（1世界しか使わない前提に落ちる）。"""
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            data = world_dict.get("world_data")
            if isinstance(data, dict):
                for key in ("world_name", "name", "title"):
                    value = data.get(key)
                    if isinstance(value, str) and value:
                        return value
        name = getattr(getattr(app, "world", None), "name", None)
        return name if isinstance(name, str) and name else "_"

    def load_records():
        try:
            with open(record_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def remember_quest(app, quest_id, kind, title, summary):
        data = load_records()
        bucket = data.setdefault(world_key(app), {})
        bucket[str(quest_id)] = {
            "kind": kind["key"],
            "label": kind["label"],
            "title": title or "",
            # **達成条件そのもの。** これが無いと referee は完了を判定できない
            # （2026-07-28 の実機で、種類のテンプレ文だけでは
            # `return_after_completion` が一度も選ばれなかった）。
            "summary": summary or "",
            "objective": kind["objective"],
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        if len(bucket) > MAX_RECORDS:
            # 古いものから捨てる。id は採番順なので数値として並べられる。
            for stale in sorted(bucket, key=_id_sort)[:len(bucket) - MAX_RECORDS]:
                bucket.pop(stale, None)
        try:
            with open(record_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
        except Exception:
            ctx.log_exc("mini quest: cannot write {}".format(record_path))
        state["titles"] = None      # 次の参照で読み直す
        write("remembered: quest {!r} {!r} kind={} world={!r}".format(
            quest_id, title, kind["key"], world_key(app)))

    def objective_for_title(title):
        """クエスト名から、この MOD が付けたお題と達成条件を引く。

        **進行プロンプトには id が入らないので名前で照合する。** 世界をまたいで
        同名の依頼が並ぶ可能性はあるが、その場合でも壊れ方はしない ― 討伐依頼が
        1件おとなしくなるだけで、例外にもデータの破損にもならない。

        戻り値は `(お題, 達成条件)` か `None`。
        """
        table = state["titles"]
        if table is None:
            table = {}
            for bucket in load_records().values():
                if not isinstance(bucket, dict):
                    continue
                for record in bucket.values():
                    if isinstance(record, dict) and record.get("title"):
                        table[record["title"]] = (
                            record.get("objective") or KINDS[0]["objective"],
                            record.get("summary") or "")
            state["titles"] = table
        return table.get(title)

    # ------------------------------------------------------------ クエスト辞書
    def quest_stores(app):
        """クエストは2箇所にある。**どちらに登録されるか決め打ちしない**。"""
        stores = []
        quests = getattr(getattr(app, "world", None), "quests", None)
        if isinstance(quests, dict):
            stores.append(quests)
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict) and isinstance(world_dict.get("quests"), dict):
            stores.append(world_dict["quests"])
        return stores

    def quest_ids(app):
        seen = []
        for store in quest_stores(app):
            for qid in store:
                if str(qid) not in seen:
                    seen.append(str(qid))
        return seen

    def quest_of(app, quest_id):
        for store in quest_stores(app):
            if quest_id in store:
                return store[quest_id]
        return None

    def quest_value(quest, name, default=None):
        if isinstance(quest, dict):
            return quest.get(name, default)
        return getattr(quest, name, default)

    # ------------------------------------------------------------ 生成
    def pick_kind():
        kind = KINDS[state["next_kind"] % len(KINDS)]
        state["next_kind"] += 1
        return kind

    def generate(app):
        """ゲーム自身の生成経路を、お題を添えて呼ぶ。

        **別スレッドに投げない。ここで最後までやりきる**（`301_` の教訓）。
        `process_choice` は `execute` が返るまで待機表示を出しっぱなしにするので、
        同期的に待つのが正しい。投げっぱなしにすると LLM が回っている最中に
        プレイヤーが移動できてしまう。
        """
        if state["generating"]:
            screen.say(app, "……いま話を聞いているところだ。")
            return
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("generate: DisplayQuestChoice not found")
            screen.say(app, "（頼まれごとは見つからなかった）")
            return

        kind = pick_kind()
        state["generating"] = True
        state["pending"] = {"kind": kind, "at": time.monotonic()}
        write("=" * 78)
        write("generate: kind={} ({}) combat={}".format(
            kind["key"], kind["label"], COMBAT_MODE))
        screen.say(app, "――掲示板の隅の、割の良くない頼まれごとを探している……")

        started = time.monotonic()
        before = set(quest_ids(app))
        quest_id = None
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("mini quest: generate_random_quest failed")
        else:
            added = sorted(set(quest_ids(app)) - before, key=_id_sort)
            quest_id = added[-1] if added else None
            write("generate: took {:.1f}s; new quest ids={}".format(
                time.monotonic() - started, added))
        state["generating"] = False
        state["pending"] = None

        if quest_id is None:
            settle(app, lambda: screen.say(app, "（頼まれごとにはならなかった）"))
            return

        quest = quest_of(app, quest_id)
        title = quest_value(quest, "quest_title", "") or ""
        summary = quest_value(quest, "request_summary", "") or ""
        remember_quest(app, quest_id, kind, title, summary)

        # **受注画面へは自分で飛ばない。** `QuestChoiceManager` を自前で組んで
        # 落とした前科がある（TECH §7.9）。掲示板を開き直せば、作ったばかりの
        # 依頼もゲームが正しい spec で並べてくれる。
        def reopen():
            screen.say(app, "「{}」の頼まれごとが見つかった。".format(
                title or "名も無い依頼"))
            open_board(app)

        settle(app, reopen)

    def settle(app, then):
        """LLM を待った後の後始末をメインスレッドで走らせる（`301_` と同じ）。

        既に行動は確定しているので、待ちきれなくても実行する。
        """
        screen.when_idle(app, then, settle=SETTLE, proceed_on_timeout=True,
                         tag="settle")

    def open_board(app):
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            return
        try:
            screen.start_phase(app, display_cls(app), "クエスト掲示板")
        except Exception:
            ctx.log_exc("mini quest: cannot reopen the quest board")

    class MiniQuestPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。

        `process_choice` はインスタンスを受け取るので、載せる必要も無い。
        """

        def __init__(self, app):
            self.app = app

        def execute(self, choice_text):
            try:
                generate(self.app)
            except Exception:
                ctx.log_exc("mini quest: phase failed")

    # ------------------------------------------------------------ 掲示板に足す
    @ctx.wrap("__main__:DisplayQuestChoice.update_button_display", required=False)
    def quest_board_buttons(orig, self, *args, **kwargs):
        """掲示板が並び終えた**後**に、自前の項目を1つ足す。

        ゲームが作った依頼ボタンには一切触らない（`quest_type` の語彙を知らずに
        済ませるのが要点。触ったら台無しになる）。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is None:
                return result
            buttons = getattr(app, "buttons", None)
            if not isinstance(buttons, list):
                write("board: app.buttons is {}; leaving it alone".format(
                    type(buttons).__name__))
                return result
            if any(screen.mark_of(entry) for entry in buttons):
                return result           # 既に在る（開き直しても二重にしない）
            entry = screen.button(BOARD_LABEL, mark="generate")
            if entry is None:
                write("board: cannot build the button (PhaseSpec unavailable)")
                return result
            # 「やめる」の手前。無ければ末尾。
            at = len(buttons)
            for index, item in enumerate(buttons):
                if ui.spec_cls_name(item) == ui.SAFE_CLS:
                    at = index
                    break
            buttons.insert(at, entry)
            # `app.buttons` は直接いじってあるので差し替えはせず塗り直しだけ頼む。
            # **`refresh` だけでは画面が塗り替わらない**（`302_` の実測）。しかも
            # ここはゲームが掲示板を組み終えた直後なので、次のフレームで塗る。
            screen.apply_buttons(app, None, "board")
            write("board: added {!r} at {}".format(BOARD_LABEL, at))
        except Exception:
            ctx.log_exc("mini quest: cannot add the board button")
        return result

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけを横取りする。

        文字列ではなく**印**で見る（同じ文言のゲーム側ボタンを巻き込まないため）。
        印が無ければ素通しするので、`301_` / `302_` の横取りはそのまま効く。
        """
        try:
            entry = ui.pressed_entry(self, button_index)
            mark = screen.mark_of(entry)
        except Exception:
            mark = None
        if mark != "generate":
            return orig(self, button_index, *args, **kwargs)
        try:
            write("pressed {!r}".format(BOARD_LABEL))
            screen.start_phase(self, MiniQuestPhase(self), BOARD_LABEL)
        except Exception:
            ctx.log_exc("mini quest: press failed")
        return None

    # ------------------------------------------------------------ 生成の中身
    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False)
    def random_quest_generator(orig, world_overview, settlement_name,
                               settlement_overview, settlement_structure_description,
                               area_description, quest_difficulty, *args, **kwargs):
        """`area_description` にお題の仕様を足す（`301_` と同じ手）。

        印は生成が終わるまでしか立っていないので、掲示板から普通に作られた依頼や
        `301_` が会話から作る依頼は素通しする。
        """
        mark = state["pending"]
        if mark is None or time.monotonic() - mark["at"] > INJECT_TTL:
            if mark is not None:
                write("inject: stale marker, left untouched")
                state["pending"] = None
            return orig(world_overview, settlement_name, settlement_overview,
                        settlement_structure_description, area_description,
                        quest_difficulty, *args, **kwargs)
        merged = (area_description or "") + generator_brief(mark["kind"])
        write("inject: area_description {} -> {} chars (kind={})".format(
            len(area_description or ""), len(merged), mark["kind"]["key"]))
        return orig(world_overview, settlement_name, settlement_overview,
                    settlement_structure_description, merged,
                    quest_difficulty, *args, **kwargs)

    # ------------------------------------------------------------ プロンプト差し替え
    def report_missed(where, missed):
        """目印が当たらなかった＝ゲーム側の文面が変わった。書き換えは諦める。"""
        if state["missed_logged"]:
            return
        state["missed_logged"] = True
        write("missed: {} — これらの目印が見つからない: {}".format(
            where, ", ".join(_text(m, 30) for m in missed)))
        write("missed: 書き換えを行わないので、依頼は通常の討伐として進行する")
        ctx.log("mini quest: prompt markers missed in {}; rewriting is disabled "
                "until they match again (see {})".format(where, log_path),
                level="WARN")

    def plan(messages):
        """この `chat` 呼び出しをどう扱うか決める。

        **判定は全メッセージを繋いだもので行う。** 進行判定のプロンプトは
        1文目が system、クエスト名と【強制事項】が user と分かれているので、
        1メッセージずつ見ると必ずどちらかが欠けて見える。
        """
        contents = [m.get("content") for m in messages
                    if isinstance(m, dict) and isinstance(m.get("content"), str)]
        if not contents:
            return None
        blob = "\n".join(contents)

        mark = state["pending"]
        if mark is not None and GEN_HEADER in blob:
            if GEN_KIND_MARK not in blob:
                report_missed("generator", [GEN_KIND_MARK])
                return None
            return ("generator", mark["kind"])

        if REFEREE_HEAD_START not in blob:
            return None                 # 進行判定ではない（イベント処理・要約など）
        title = title_in(blob)
        if not title:
            return None
        found = objective_for_title(title)
        if found is None:
            return None                 # この MOD の依頼ではない。触らない
        missed = referee_anchors_missing(blob)
        if missed:
            report_missed("referee", missed)
            return None
        objective, summary = found
        return ("referee", (title, objective, summary))

    def audit(kind_of_change, before, after, note=""):
        """差し替えの中身を最初の数回だけ残す。

        **枠は生成と進行で別に持つ。** 1つの枠にしていたせいで、生成が内部で
        4回 LLM を呼んだ回に枠を使い切り、**肝心の進行側の中身が1行も残らなかった**
        （2026-07-28）。原因を読むためのログが、原因を読みたい場面で無かった。
        """
        state["rewrites"] += 1
        seen = state["audited"].get(kind_of_change, 0) + 1
        state["audited"][kind_of_change] = seen
        write("rewrite: {} {} -> {} chars {}".format(
            kind_of_change, len(before), len(after), note))
        if seen <= AUDIT_FIRST_N:
            for line in after.split("\n"):
                if any(word in line for word in ("【このクエストの目的】",
                                                 "【依頼の内容",
                                                 "消化する必要は",
                                                 "return_after_completion",
                                                 "encounter_final_boss",
                                                 "nothing_happens")):
                    write("    {}".format(_text(line, 200)))

    @ctx.wrap("llama_cpp_runtime_completion:LlamaCppClient.chat", required=False)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        """`105_` と同じ地点。**ストリーミングでもここは必ず通る**（実測）。

        `messages` そのものは書き換えない。会話履歴としてゲーム側が同じ dict を
        保持している可能性があるので、浅いコピーを渡す。
        """
        try:
            if isinstance(messages, list):
                decision = plan(messages)
                if decision is not None:
                    what, payload = decision
                    new_messages = []
                    changed = False
                    for message in messages:
                        content = (message.get("content")
                                   if isinstance(message, dict) else None)
                        new_content = None
                        if isinstance(content, str):
                            if what == "generator":
                                if GEN_KIND_MARK in content:
                                    new_content = rewrite_generator(content, payload)
                            else:
                                new_content = rewrite_referee_text(
                                    content, payload[1], summary=payload[2])
                        if new_content is not None and new_content != content:
                            replacement = dict(message)
                            replacement["content"] = new_content
                            new_messages.append(replacement)
                            changed = True
                            audit(what, content, new_content,
                                  "kind={}".format(payload["key"])
                                  if what == "generator"
                                  else "quest={!r}".format(_text(payload[0], 40)))
                            continue
                        new_messages.append(message)
                    if changed:
                        messages = new_messages
        except Exception:
            # 書き換えに失敗してもプロンプトはそのまま送る。討伐クエストとして
            # 正しく進むだけなので、ここで止めるほうが損害が大きい。
            ctx.log_exc("mini quest: rewrite pass failed; sending prompt untouched")
        return orig(self, model, messages, format, *args, **kwargs)

    # ------------------------------------------------- 帰還を撤退にさせない
    def referee_result(orig, args, kwargs, site):
        """`quest_referee*` の戻り値を見て、撤退なら達成に差し替える。

        **対象はこの MOD が作った依頼だけ。** 判定は第1引数 `quest_data` の
        `quest_title` を控えと突き合わせて行う（進行プロンプトと同じ根拠）。
        """
        result = orig(*args, **kwargs)
        if not RETURN_INSTEAD_OF_RETIRE:
            return result
        try:
            quest_data = args[0] if args else kwargs.get("quest_data")
            title = _get(quest_data, "quest_title") if quest_data is not None else None
            if not isinstance(title, str) or objective_for_title(title) is None:
                return result           # この MOD の依頼ではない。触らない
            if state["shape"] is None:
                # 形は実測できていなかった。最初の1回だけ残す。
                state["shape"] = shape_of(result)
                write("{}: result shape = {}".format(site, state["shape"]))
            if retire_to_return(result, write):
                write("{}: retire -> return_after_completion (quest={!r})".format(
                    site, _text(title, 40)))
                write("    理由: ミニクエストに討伐対象はいない。"
                      "目的を果たしたうえでの帰還は達成である")
        except Exception:
            # 差し替えに失敗してもゲームの判断をそのまま通す。
            ctx.log_exc("mini quest: could not adjust the referee result")
        return result

    @ctx.wrap("scripts.llm.llm_manager:quest_referee_with_free_action",
              required=False)
    def referee_with_free_action(orig, *args, **kwargs):
        return referee_result(orig, args, kwargs, "referee(free_action)")

    @ctx.wrap("scripts.llm.llm_manager:quest_referee", required=False)
    def referee(orig, *args, **kwargs):
        return referee_result(orig, args, kwargs, "referee")

    # ------------------------------------------------------------ 自己検証
    _verify(ctx, write)

    ctx.log("mini quest: kinds={} combat={} log={} records={}".format(
        "/".join(k["key"] for k in KINDS), COMBAT_MODE, log_path, record_path))


# --------------------------------------------------------------------------
# 注入した時点で、置き換えが本当に成立するかを実プロンプトの写しで確かめる。
# 実経路はクエストを1件受注するまで通らないので、起動直後に確かめておく
# （`105_` と同じ方針）。
# --------------------------------------------------------------------------
SAMPLE_REFEREE_SYSTEM = (
    "現在クエストが進行中である。このクエストはラスボス'迷宮の守護龍'"
    "を討伐することか、プレイヤーが攻略自体を諦めて撤退することのどちらかでのみ終了される。\n"
    "あなたはダークファンタジーTRPGシステムのベースモデルとして、以下の役割を持つ:\n"
)

SAMPLE_REFEREE_USER = (
    "【ダンジョンマネージャーの処理要素】\n"
    "移動や戦闘、イベント実行をできるだけ試みる。"
    "ラスボス以外に残り戦闘が無い場合には強制的にラスボス戦を開始する。"
    "プレイヤーの希望とダンジョンの険しい環境の両方を反映した進行を行う。\n"
    "- retire_from_the_quest: クエストを放棄し帰還する。プレイヤーがその意思を明確に示したときにのみ選択するべきだが、"
    "ふさわしい状況下（幽閉されているので脱出不可能、今まさに敵に襲撃を受けようとしているなど）では他の処理を選んでもいい。"
    "しかし具体的が無いならばさっさと撤退させること。\n"
    "- return_after_completion: クエストを攻略した後にのみ実行可能。攻略として帰還する。\n"
    "【強制事項】\n"
    "- クエスト攻略を諦めての撤退以外では、クエストエリア外への移動は認めない。プレイヤーが明確に中断と帰還の意思を持つ場合のみ認められる。\n"
    "- ラスボスが残っている限りクエストは攻略完了しない。\n"
    "- battleとfield_eventは同時に実行できない。\n"
    "- 戦闘ベースのRPGなので、field_eventよりもbattleが多くなるようにすること。\n"
    "- 雑魚敵や中ボスがまだ残っている場合、プレイヤーがラスボスの元にたどり着く前に"
    "出来る限りそれらとの戦闘を発生させるよう試みること。\n"
    "- 雑魚敵や中ボスがもはやいない場合、ラスボスと邂逅させること。\n"
    "- nothing_happens: 上述のどの処理も発生しない。プレイヤーが無理のある試みをした場合にもこれ。\n"
    "【クエストの構造】\n"
    "- quest_title: 中央商業区の失われた宝\n"
    "- battle: 雑魚敵や中ボスやラスボスとの戦闘の開始。最大3体の敵を一度に選択できるが、"
    "特に状況的な理由が無ければ平均1.6体くらいを出すのが適正難易度。\n"
    "【ダンジョン要素】\n"
    "- 残り通常戦闘回数: **3\\**\n"
    "- 残り中ボス戦闘: **[]**\n"
    "- 残りフィールドイベント: **[]**\n"
    "- 残りラスボス戦闘: **['迷宮の守護龍']**\n"
)

SAMPLE_GENERATOR = (
    "あなたはRPGのクエストジェネレータです。与えられる世界とクエストエリアの記述を基に、"
    "一つの【討伐】ランダムクエストの構造を日本語で生成してください。\n"
)


def check_samples(combat_mode=None):
    """自己検証の中身。オフライン検証からも同じものを呼ぶ。

    問題の一覧を返す（空なら健全）。
    """
    problems = []
    blob = SAMPLE_REFEREE_SYSTEM + SAMPLE_REFEREE_USER

    missed = referee_anchors_missing(blob)
    if missed:
        problems.append("referee anchors missing: {}".format(missed))
    else:
        head = rewrite_referee_text(SAMPLE_REFEREE_SYSTEM, "見本の目的。",
                                    combat_mode, "薬草を5株集める。")
        body = rewrite_referee_text(SAMPLE_REFEREE_USER, "見本の目的。",
                                    combat_mode, "薬草を5株集める。")
        if head is None:
            problems.append("system message was not rewritten")
        else:
            if REFEREE_HEAD_END in head:
                problems.append("the boss clause survived in the system message")
            if "見本の目的。" not in head:
                problems.append("objective was not injected")
            # 実機で失敗した2点。達成条件と、在庫消化の打ち消し。
            if "薬草を5株集める。" not in head:
                problems.append("the concrete completion condition was not injected")
            if "消化する必要は" not in head:
                problems.append("the 'no need to clear the stock' clause is missing")
        if body is None:
            problems.append("user message was not rewritten")
        else:
            for old, table in REFEREE_RULES:
                new = table.get(combat_mode or COMBAT_MODE)
                if new is None:
                    continue
                # 置換後の文が元の文を含む項目（`nothing_happens` は元の説明に
                # 追記する形）があるので、「元が残っているか」では判定できない。
                # **置換後の文が入っているか**で見る。
                if new and new not in body:
                    problems.append("rule not applied: {}".format(old[:24]))
                if not new and old in body:
                    problems.append("rule not removed: {}".format(old[:24]))
            if "- quest_title: 中央商業区の失われた宝" not in body:
                problems.append("the quest structure block was damaged")
            if "- battleとfield_eventは同時に実行できない。" not in body:
                problems.append("an untargeted line was lost")
            if "- 残りラスボス戦闘: **['迷宮の守護龍']**" not in body:
                problems.append("the dungeon stock block was damaged")

    gen = rewrite_generator(SAMPLE_GENERATOR, KINDS[0])
    if gen is None:
        problems.append("generator sample was not rewritten")
    elif GEN_KIND_MARK in gen or "【採集】" not in gen:
        problems.append("the generator kind mark was not replaced")

    return problems


def _verify(ctx, write):
    problems = check_samples()
    if problems:
        for problem in problems:
            ctx.log("mini quest VERIFY FAILED: {}".format(problem), level="ERROR")
            write("VERIFY FAILED: {}".format(problem))
    else:
        write("verify ok: rewrites hold on the recorded prompt samples")
