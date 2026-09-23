# -*- coding: utf-8 -*-
r"""闘技場の相手の強さと懸賞金を設定で決める。

素の闘技場は、勝つほど相手が際限なく強くなり、負けるとゲームオーバーになる。
懸賞金のほうは逆に、強い相手ほど伸びが鈍る。
どこで打ち止めるか・どれだけ払うかを設定で決められるようにする。

## 素の仕組み（`233_probe_colosseum` の実機。GAME.md §2.11）

相手の強さ（ランク）は、その土地の依頼の難易度から決まる。

    rank(n) = round(D * (9 + 4n) / 18)     D = 土地の依頼の難易度、n = current_phase / 2

初戦は D の半分、1試合勝つごとに D の 2/9 ずつ増え、**頭打ちは無い**（実機で格97まで）。
プレイヤーのレベルには依らない（レベル1の主人公でも格30から始まる）。
`current_phase` は施設の `config` に焼かれるので、この伸びはセーブをまたいで続く。

懸賞金に乱数は乗っていない（同じ格で2回やると同額）。
格30→173G / 43→282 / 44→288 / 58→359 / 71→426 / 85→452 / 97→454 で、
**格70を超えると頭打ち**（85 と 97 の差は 2G）。式そのものは当てていない。
格だけでも決まらず（`D` 32 の土地では格30で 177）、施設に焼いた格も読まない
（上限で下げても素の格の額が出る）。

## この MOD の当て所

| やること | 包む先 |
|---|---|
| 相手の**実際の強さ** | `scripts.functions:get_enemy_exp_lvl` / `get_enemy_attributes_base_point` の第2引数。レベルは難易度 + 1（GAME.md §2.20）。ゲームは施設に焼いた格をここへ渡すので、焼く格を揃えれば済む。この包みは揃える前の値が来たときの控え |
| 相手の**描写** | `llm_manager:colosseum_enemy_generator` の第4引数（頼み文に載る格） |
| 保存される格 | `ColosseumMatchStart.generate_enemy_data` の戻りの `data.rank` |
| 懸賞金 | `BattleEndInColosseum.end_phase`。所持金はこの中で動く（`instantale.py:8105`） |
| 相手の格を告げる | `EntryColosseumMatchManager.method` の後 |
| 負けても死なない | `BattlePhaseManager.check_battle_end` の前（ここから `GameOverManager` が作られる）。体力を戻したうえで、逃げたときと同じ `BattleEndManager(app, 'escaped')` を起こして試合を切り上げる。起こす前にゲーム自身の逃走と同じ状態にする（敵の一覧を空にし、倒れて一覧から外された主人公を `escaped_member_in_battle` に預けてゲームに戻させる）。`True` を返して戦闘の繰り返しを抜けさせる |
| 死なずに退く描写 | 審判（`referee_*`）と試合の要約（`colosseum_battle_summarizer`）の送り口。最後の user message の末尾に一文を足す |

## 描写の出どころ

闘技場の試合の文章を書いているのは2か所で、どちらも LLM。
1手ごとの描写は戦闘の審判の `narration`（GAME.md §2.10.1）、
勝った後の締めは `colosseum_battle_summarizer`（GAME.md §2.11）。
審判の頼み文には闘技場かどうかが載らないので、敵の体力が尽きる手では
依頼の戦闘と同じく絶命や惨殺が書かれる。
審判は `in_colosseum_battle` が立っている間だけ、要約はその呼び出しの間だけ足す。
要約の頼みは `guard_battle_summarizer` の名前で送られる（ゲーム側の取り違え。GAME.md §2.11）ので、
名前だけでは衛兵戦の要約と見分けられず、呼び出しの間の印で見分ける。
送り口は `401_battle_character_context` と同じ所で、後から生える別名はローダの `llm.watch_aliases` に任せる。

**頼み文の難易度だけを変えても相手は弱くならない**（版1の実機。上限70を当てたのに
敵のレベルは 72 と 86 のままで、`data.rank` も 71 / 85 で焼かれた）。
ゲームは格を自分で計算し、**頼み文・保存・敵の数値の3か所で別々に使う**。
だから3か所とも同じ値に揃える。

揃える範囲は**試合を仕込んでいる間だけ**にする（`ColosseumMatchStart.execute` で開き、
`BattleStartManager.start_battle` で閉じる窓）。`get_enemy_*` は依頼の敵も作る共有の関数なので、
窓の外では指一本触れない。閉じ損ねても `WINDOW_SECONDS` で失効する。

**素の式から `D` を復元して組み直す。**
ゲームが渡してきた難易度と `current_phase` があれば `D = raw * 18 / (9 + 4n)` で戻せるので、
土地の難易度を別途引かなくてよく、設定が全部既定なら渡ってきた値がそのまま返る
（丸める前の値が一致するので、既定のままなら1ゴールドも1ランクも動かない）。

懸賞金は**倍率**で乗せる。素の式そのものは当てていない（頭打ちに向かう形までは測れているが、
ランク70〜90の点がまだ無い）ので、額を置き換えるのではなく、ゲームが出した額に掛ける。
画面の文（`報酬として<額>Gを貰った。`）も同じ額に書き換え、
**文を書き換えられたときだけ**差額を足す（表示と所持金が食い違わないように）。

## 効かせない場面

`in_colosseum_battle` が立っていない戦闘には触らない。
`331_facility_investment` が建てた闘技場も本物の闘技場も同じに扱う（どちらもゲームの経路）。
既出の闘士を頼み文に足す一文は 331 と同じ文面で、
どちらも既に入っていれば足さない（読む順ではこちらが外側の包みで、先に足す）。
"""

import sys
import time

from instantale_modloader import frames, llm, ui

LOG_BASENAME = "colosseum_custom.log"
MARK = "_mod_colosseum_custom"

#: 闘技場の `facility_type`。
ARENA_TYPE = "colosseum"

#: 素のランクの式（GAME.md §2.11）。`rank(n) = round(D * (BASE + GAIN * n) / DIV)`。
RANK_BASE, RANK_GAIN, RANK_DIV = 9, 4, 18

#: 懸賞金の文の目印と、格を告げる文。文体はゲーム画面なので常体。
REWARD_MARK = "報酬として"
ANNOUNCE_TEXT = "受付: 次の相手は{word}。腕試しにはちょうどいいだろう。"
ANNOUNCE_RISK_TEXT = "受付: 次の相手は{word}。あんたの腕では命がいくつあっても足りんぞ。"
DEFEAT_TEXT = "膝をついた。これ以上は続けられない。門番に肩を借り、闘技場の外へ退いた。"

#: 負けを認めるときにゲームへ渡す終わり方と、そのマネージャ（実機で採取）。
#: 勝ったときは `'won'` が渡る。倒れた後に残す体力。
END_MANAGER_CLS = "BattleEndManager"
ESCAPED_END_TYPE = "escaped"
SURVIVE_HP = 1

#: 切り上げた後、敵の欄の更新で起きる 0 除算を握っている秒数（`enemy_display`）。
#: 実機では終わってから約 1 秒後に来た。
ENEMY_DISPLAY_TARGET = "scripts.hud.new_hud:InstanTaleHUD.update_enemy_display"
SURRENDER_GUARD_SECONDS = 10

#: 切り上げた戦闘に付ける印と、その後は飛ばす戦闘の手（`after_surrender`）。
SURRENDERED_MARK = MARK + "_surrendered"
AFTER_SURRENDER_STEPS = ("enemy_turn_separate", "handle_battle_situation",
                         "reduce_status_turns_and_log")

#: `app.party` の主人公の鍵（仲間は id。実機のログ `party=['player', '78']`）。
PLAYER_KEY = "player"

#: 格の言い換え。ゲーム自身の頼み文の説明（ランク1が凡人や雑魚動物、
#: ランク70が伝説の勇者や魔王、半神の怪物）に合わせてある。
RANK_WORDS = ((10, "駆け出しの闘士"), (25, "腕に覚えのある程度の相手"),
              (45, "一角の闘士"), (70, "歴戦の猛者"),
              (100, "伝説に並ぶ強者"), (None, "神話の域の化け物"))

#: 敵の数値を作る関数（`scripts.functions`。GAME.md §2.20）。
#: 闘技場の相手の**実際の強さ**はここで決まる。依頼の敵も同じ関数を通るので、
#: 触るのは試合を仕込んでいる窓の中だけ。
ENEMY_NUMBER_FNS = ("get_enemy_exp_lvl", "get_enemy_attributes_base_point")

#: 窓の寿命（秒）。戦闘が始まらずに終わった回で閉じ損ねても、ここで失効する。
WINDOW_SECONDS = 300

#: 危ないと告げる境目。相手の格がプレイヤーのレベルのこの倍を超えたら一言足す
#: （相手のレベルは格 + 1 なので、等倍が「互角」に当たる）。
RISK_RATIO = 1.2

#: 初戦の相手を何に合わせるか（`mod.json` の "values" と同じ語）。
BASIS_AREA, BASIS_PLAYER, BASIS_HIGHER = "土地の難易度", "プレイヤーのレベル", "高いほう"

# 設定（`mod.json` の "settings"）。**既定はすべて素のゲームの値**で、
# ローダが選ばれた値でこの定数を上書きする（TECH.md §3.2.2）。
RANK_START_SCALE = 1.0
RANK_STEP_SCALE = 1.0
RANK_CAP = 0
RANK_BASIS = BASIS_AREA
RANK_PLAYER_RATIO = 1.0
REWARD_SCALE = 1.0
REWARD_PER_LEVEL = 0.0
REWARD_PER_RANK = 0.0
ANNOUNCE_RANK = True
SURVIVE_DEFEAT = False
VARY_OPPONENT = True
NONLETHAL_NARRATION = False

#: 審判と要約へ足す一文。見出しは同じ message に2度足さないための印も兼ねる。
#: 審判には**判定は普段どおり・決着は体力**を必ず添える。
#: 「殺し合いではない」だけを渡した版では、審判が描写の中で「審判が試合を止めた」と書いて
#: ダメージを付けず、以後の手も戦闘ログのその一文を引き継いで効果を出さなくなった
#: （敵の体力が残ったまま、試合が終わらなくなった。実機）。
NONLETHAL_MARK = "【闘技場の試合】"
NONLETHAL_NOTE = ("\n\n" + NONLETHAL_MARK + "\n"
                  "これは観客の前で行う闘技場の試合で、殺し合いではない。"
                  "これは描写の言葉づかいだけの指示で、攻撃・ダメージ・効果の判定は普段どおり出すこと。"
                  "試合の決着はゲームが体力で判定する。"
                  "体力が残っている者を描写の中で倒したり、試合を止めたり、決着させたりしない"
                  "（戦闘ログにそう書かれていても、体力が残っていれば試合は続いている）。"
                  "体力が尽きた者も死なず、気を失う・降参する・膝をついて退く・担ぎ出されるなどして試合から退く。"
                  "絶命・惨殺・致命傷・遺体の描写はしない。")
#: 試合の要約へ足す一文。要約は決着の後なので判定の話は要らない。
NONLETHAL_SUMMARY_NOTE = ("\n\n" + NONLETHAL_MARK + "\n"
                          "これは観客の前で行う闘技場の試合で、殺し合いではない。"
                          "敗れた者も死なず、気を失う・降参する・担ぎ出されるなどして退いた。"
                          "絶命・惨殺・致命傷・遺体の描写はしない。")

#: 審判と要約の送り口（`401_battle_character_context` と同じ所）。
#: 審判は `llm_manager_battle`、要約は `llm_manager` を通る。
NARRATION_SEND_TARGETS = (
    "scripts.llm.llm_manager_battle:send_request",
    "scripts.llm.llm_manager_battle:send_request_with_no_structure",
    "scripts.llm.llm_manager:send_request",
    "scripts.llm.llm_manager:send_request_with_no_structure",
)
#: 審判の manager_name の頭。
REFEREE_PREFIX = "referee_"
#: 試合の要約の manager_name（ゲームは衛兵戦の名前で送る。GAME.md §2.11）。
SUMMARY_MANAGERS = ("colosseum_battle_summarizer", "guard_battle_summarizer")

#: 既に出た闘士を頼み文へ足す一文。`331_facility_investment` と同じ文面
#: （どちらが先に足しても二重にしないため、頭の句で見分ける）。
VARIETY_NOTE = ("この闘技場には既に {names} が出場している。"
                "名前も出自も戦い方もこれらとは重ならない、別の闘士を作ること。")
VARIETY_HEAD = "この闘技場には既に"
VARIETY_LIMIT = 8


def rank_word(rank):
    """格を言い換えた語。"""
    for edge, word in RANK_WORDS:
        if edge is None or rank < edge:
            return word
    return RANK_WORDS[-1][1]


def plain_rank(difficulty, index):
    """素の式（GAME.md §2.11）。"""
    return int(round(difficulty * (RANK_BASE + RANK_GAIN * index) / float(RANK_DIV)))


def difficulty_from(raw, index):
    """渡ってきた難易度と試合数から、その土地の難易度 `D` を戻す。"""
    return raw * float(RANK_DIV) / (RANK_BASE + RANK_GAIN * index)


def variety_note(names):
    """既に出た闘士を並べた一文。名前が無ければ空。"""
    names = [str(n).strip() for n in names if isinstance(n, str) and str(n).strip()]
    if not names:
        return ""
    return VARIETY_NOTE.format(
        names="".join("「{}」".format(name) for name in names[-VARIETY_LIMIT:]))


def replace_amount(text, base, want):
    """文の中の額だけを置き換える。書けなければ `None`。

    桁区切りの有無は元の書き方に合わせる（`130_currency_unit` が単位を
    書き換えていても、数の部分は素のまま残る）。
    """
    if not isinstance(text, str) or base is None or want is None:
        return None
    for form in ("{:,}".format(base), str(base)):
        if form in text:
            new = "{:,}".format(want) if "," in form else str(want)
            return text.replace(form, new, 1)
    return None


def append_note(message, note):
    """最後の user message の末尾に `note` を足した写しを返す。足さなければ `message` そのもの。

    呼び出し元の list も dict も書き換えない（ゲームが同じ list を持ち続けているため）。
    既に印が入っていれば足さない（別名の包みが二重に掛かったときや再送の保険）。
    """
    if not isinstance(message, list):
        return message
    for item in message:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, str) and NONLETHAL_MARK in content:
            return message
    for index in range(len(message) - 1, -1, -1):
        item = message[index]
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        rewritten = list(message)
        replacement = dict(item)
        replacement["content"] = content + note
        rewritten[index] = replacement
        return rewritten
    return message


class _LocationView(object):
    """`location` の身代わり。`description` だけ差し替え、ほかは本物へ素通しする。

    頼み文に足したい一文を本物の施設に書くと、セーブに焼かれて残ってしまう。
    """

    def __init__(self, target, description):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "description", description)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_target"), name, value)


def vary_location(location, names):
    """頼み文に渡す `location` に、既出の闘士の一文を足したものを返す。

    ゲームの頼み文が読むのは施設の名前と概要だけ（GAME.md §2.11）なので概要の末尾に足す。
    既に同じ一文が入っていればそのまま返す。
    """
    note = variety_note(names)
    if not note or location is None:
        return location
    if isinstance(location, dict):
        description = location.get("description") or ""
        if VARIETY_HEAD in description:
            return location
        copied = dict(location)
        copied["description"] = "{}\n{}".format(description, note).strip()
        return copied
    description = getattr(location, "description", None) or ""
    if VARIETY_HEAD in description:
        return location
    return _LocationView(location, "{}\n{}".format(description, note).strip())


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="colosseum", mark=MARK)
    #: 試合1回ぶんの控え。`reward` は `end_phase` の間、
    #: `window` は相手を仕込んでいる間だけ入る。
    #: `summarizing` は試合の要約を頼んでいる最中の深さ。
    #: `surrendered_at` は負けとして切り上げた時刻（`time.monotonic()`）。
    #: `removed_player` は試合中に一覧から外された主人公の値（切り上げで戻す）。
    state = {"survived": False, "reward": None, "window": None, "summarizing": 0,
             "surrendered_at": None, "removed_player": None}

    # ------------------------------------------------------------------ 設定
    def rank_untouched():
        """相手の強さの設定が全部素のままか。"""
        return (float(RANK_START_SCALE) == 1.0 and float(RANK_STEP_SCALE) == 1.0
                and int(RANK_CAP) <= 0 and RANK_BASIS == BASIS_AREA)

    def reward_multiplier(app, rank):
        """懸賞金に掛ける倍率。素のままなら 1.0。"""
        scale = float(REWARD_SCALE)
        if REWARD_PER_LEVEL:
            scale += float(REWARD_PER_LEVEL) * max(0, level_of(app) - 1)
        if REWARD_PER_RANK and rank:
            scale += float(REWARD_PER_RANK) * rank
        return scale

    # -------------------------------------------------------------- 写し取り
    def level_of(app):
        value = frames.attr(frames.attr(app, "player", None), "experience_level", None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 1
        return max(1, int(value))

    def facility_of(app):
        """立っている施設。id しか持っていない作りなら街から引く。"""
        player = frames.attr(app, "player", None)
        location = frames.attr(player, "location", None)
        if location is None or isinstance(location, (str, int)):
            area = ui.current_area(app)
            found, _node = ui.find_facility(area, location) if area is not None else (None, None)
            return found
        return location

    def arena_of(app):
        """立っている施設が闘技場ならそれ。よそなら None。"""
        facility = facility_of(app)
        if facility is None or ui.facility_type_of(facility) != ARENA_TYPE:
            return None
        return facility

    def config_of(facility):
        config = getattr(facility, "config", None) if facility is not None else None
        return config if isinstance(config, dict) else {}

    def match_index(app):
        """この闘技場で何試合目か（`current_phase` / 2）。読めなければ 0。"""
        phase = config_of(arena_of(app)).get("current_phase")
        if isinstance(phase, bool) or not isinstance(phase, (int, float)):
            return 0
        return max(0, int(phase) // 2)

    def fighters_so_far(app):
        """この闘技場に既に出た相手の名前（ゲームが貯める順）。"""
        enemies = config_of(arena_of(app)).get("enemy_data")
        if not isinstance(enemies, dict):
            return []

        def key_of(item):
            try:
                return int(item[0])
            except (TypeError, ValueError):
                return 0

        names = []
        for _key, entry in sorted(enemies.items(), key=key_of):
            data = entry.get("data") if isinstance(entry, dict) else None
            name = data.get("name") if isinstance(data, dict) else None
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        return names

    def area_difficulty(app):
        """その土地の依頼の難易度。ゲーム自身の関数に聞く（`133_` と同じ地点）。"""
        functions = sys.modules.get("scripts.functions")
        fn = getattr(functions, "get_quest_difficulties", None) if functions else None
        if fn is None or app is None:
            return None
        try:
            values = [v for v in (fn(ui.current_area(app), getattr(app, "world", None)) or ())
                      if isinstance(v, (int, float)) and not isinstance(v, bool)]
        except Exception:
            return None
        if not values:
            return None
        # ゲームは一覧の**平均を四捨五入してから**式に入れる（実測。GAME.md §2.11）。
        # 小数のまま計算すると告げる格が1ずれ（平均 69.33 の土地で 35 と出たが実際は 34）、
        # 切り捨てでも1ずれる（平均 31.67 の土地で 29 と出たが実際は 30）。
        return int(round(sum(values) / float(len(values))))

    # ------------------------------------------------------ 相手の強さを決める
    def adjusted_rank(app, raw, index):
        """ゲームが決めた難易度を設定で組み直す。素のままなら `raw` をそのまま返す。"""
        if rank_untouched():
            return raw
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
            return raw
        difficulty = difficulty_from(raw, index)
        start = difficulty * RANK_BASE / float(RANK_DIV)
        if RANK_BASIS != BASIS_AREA:
            by_level = level_of(app) * float(RANK_PLAYER_RATIO)
            start = max(start, by_level) if RANK_BASIS == BASIS_HIGHER else by_level
        step = difficulty * RANK_GAIN / float(RANK_DIV)
        value = (start * float(RANK_START_SCALE)
                 + step * float(RANK_STEP_SCALE) * index)
        cap = int(RANK_CAP)
        if cap > 0:
            value = min(value, cap)
        return max(1, int(round(value)))

    def expected_rank(app):
        """次の試合の相手の格。まだ作られていなければ設定を当てて見積もる。"""
        config = config_of(arena_of(app))
        phase = config.get("current_phase")
        index = max(0, int(phase) // 2) if isinstance(phase, (int, float)) else 0
        enemies = config.get("enemy_data")
        if isinstance(enemies, dict):
            entry = enemies.get(str(int(phase))) if isinstance(phase, (int, float)) else None
            data = entry.get("data") if isinstance(entry, dict) else None
            rank = data.get("rank") if isinstance(data, dict) else None
            if isinstance(rank, (int, float)) and not isinstance(rank, bool):
                return int(rank)          # 既に作られている相手（調整後の値）
        difficulty = area_difficulty(app)
        if difficulty is None:
            return None
        return adjusted_rank(app, plain_rank(difficulty, index), index)

    # ------------------------------------------------- 相手を仕込んでいる間の窓
    def open_window(app):
        """`申し込む` から戦闘が始まるまでの窓。この間だけ敵の数値に手を入れる。"""
        state["window"] = {"index": match_index(app), "raw": None, "new": None,
                           "until": time.monotonic() + WINDOW_SECONDS}

    def close_window():
        state["window"] = None

    def window():
        """開いている窓。時間切れなら閉じて `None` を返す。"""
        opened = state.get("window")
        if opened is not None and time.monotonic() > opened["until"]:
            state["window"] = None
            return None
        return opened

    def remember(opened, raw, new, what, index):
        """組み直した値を窓に控え、動いたときだけ1行残す。"""
        if new != raw:
            if opened is not None:
                opened["raw"], opened["new"] = raw, new
            write("{}: {} -> {} (match {})".format(what, raw, new, index + 1))
        return new

    def adjust_difficulty(app, raw, what):
        """頼み文に載る格。窓が無ければ今の施設の試合数で組み直す。"""
        if rank_untouched():
            return raw
        opened = window()
        index = opened["index"] if opened is not None else match_index(app)
        return remember(opened, raw, adjusted_rank(app, raw, index), what, index)

    def adjust_in_window(app, raw, what):
        """敵の数値。**窓の中でしか触らない**（依頼の敵も同じ関数を通るため）。

        ゲームは敵の数値を施設に焼いた格から作る（実機。焼いた格が 16 なら 16 が渡る）。
        焼く格は `generate_enemy_data` で既に揃えてあるので、渡ってきたのがその値なら触らない。
        組み直した値をもう一度組み直すと、上限以外の設定では二重に掛かる
        （伸び0で格16に揃えた試合で、16 をさらに 6 へ下げ、敵がレベル7になった）。
        ここで組み直すのは、揃える前の素の値が渡ってきたときと、頼み文を見ていないときだけ。
        """
        opened = window()
        if opened is None or rank_untouched():
            return raw
        if opened.get("new") is not None:
            if raw == opened["raw"]:
                return remember(opened, raw, opened["new"], what, opened["index"])
            return raw
        return remember(opened, raw, adjusted_rank(app, raw, opened["index"]), what,
                        opened["index"])

    def install_enemy_number(name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def enemy_number(orig, tier=None, difficulty=None, *args, **kwargs):
            """相手の**実際の強さ**。レベルも能力値もこの難易度1つから決まる。"""
            try:
                difficulty = adjust_in_window(ui.find_app(), difficulty, name)
            except Exception:
                ctx.log_exc("colosseum: cannot adjust {}".format(name))
            return orig(tier, difficulty, *args, **kwargs)

    for _name in ENEMY_NUMBER_FNS:
        install_enemy_number(_name)

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        """相手が出来上がったら窓を閉じる。"""
        try:
            return orig(self, *args, **kwargs)
        finally:
            close_window()

    @ctx.wrap("__main__:ColosseumMatchStart.generate_enemy_data", required=False,
              safe=True)
    def generate_enemy_data(orig, self, enemy_id=None, *args, **kwargs):
        """施設に焼かれる格も、こちらが決めた値へ揃える。

        ここで保存された `data.rank` は次の試合以降も残る。
        懸賞金はこの値では決まらない。上限で下げた試合でも、素の格の額が出た
        （素なら格65 → 40 に下げても 393。GAME.md §2.11）。
        """
        result = orig(self, enemy_id, *args, **kwargs)
        try:
            opened = window()
            data = result.get("data") if isinstance(result, dict) else None
            if (opened is not None and opened.get("new") is not None
                    and isinstance(data, dict) and data.get("rank") == opened["raw"]):
                data["rank"] = opened["new"]
                write("saved rank: {} -> {}".format(opened["raw"], opened["new"]))
        except Exception:
            ctx.log_exc("colosseum: cannot align the saved rank")
        return result

    @ctx.wrap("scripts.llm.llm_manager:colosseum_enemy_generator", required=False)
    def enemy_generator(orig, *args, **kwargs):
        """頼み文に載る格を組み直し、既出の闘士を足す。

        難易度は第4位置引数で渡る（実測）が、`kwargs` で来ても拾えるようにしてある。
        ここは**描写にしか効かない**（実際の強さは `get_enemy_*` の側）。
        """
        try:
            app = ui.find_app()
            if "npc_difficulty_level" in kwargs:
                kwargs["npc_difficulty_level"] = adjust_difficulty(
                    app, kwargs["npc_difficulty_level"], "prompt rank")
            elif len(args) >= 4:
                args = (args[:3] + (adjust_difficulty(app, args[3], "prompt rank"),)
                        + args[4:])
            if VARY_OPPONENT:
                names = fighters_so_far(app)
                if names:
                    if args:
                        args = (vary_location(args[0], names),) + args[1:]
                    elif "location" in kwargs:
                        kwargs["location"] = vary_location(kwargs["location"], names)
                    write("variety: {} earlier fighter(s) told to the generator".format(
                        len(names)))
        except Exception:
            ctx.log_exc("colosseum: cannot adjust the opponent")
        return orig(*args, **kwargs)

    # ------------------------------------------------------ 申し込む前の口上
    @ctx.wrap("__main__:EntryColosseumMatchManager.method", required=False, safe=True)
    def entry_method(orig, self, *args, **kwargs):
        """受付の口上の後に、次の相手の格を1行足す。"""
        result = orig(self, *args, **kwargs)
        try:
            if ANNOUNCE_RANK:
                app = getattr(self, "app", None) or ui.find_app()
                rank = expected_rank(app)
                if rank:
                    risky = rank > level_of(app) * RISK_RATIO
                    text = ANNOUNCE_RISK_TEXT if risky else ANNOUNCE_TEXT
                    screen.say(app, text.format(word=rank_word(rank)))
                    write("announce: rank {} ({}) vs level {}".format(
                        rank, rank_word(rank), level_of(app)))
        except Exception:
            ctx.log_exc("colosseum: cannot announce the opponent")
        return result

    # ------------------------------------------------------------ 試合の始め
    @ctx.wrap("__main__:ColosseumMatchStart.execute", required=False, safe=True)
    def match_start(orig, self, choice_text="", *args, **kwargs):
        """1試合ぶんの札を戻し、相手を仕込む窓を開ける。"""
        app = getattr(self, "app", None) or ui.find_app()
        state["survived"] = False
        state["removed_player"] = None
        open_window(app)
        try:
            return orig(self, choice_text, *args, **kwargs)
        except Exception:
            close_window()
            raise

    # ------------------------------------------------------------ 倒れたとき
    def fallen(app):
        """闘技場の試合でプレイヤーが倒れているか。"""
        if not getattr(app, "in_colosseum_battle", False):
            return False
        hp = getattr(getattr(app, "player", None), "current_hp", None)
        return (not isinstance(hp, bool) and isinstance(hp, (int, float)) and hp <= 0)

    def surrender(app):
        """負けを認めて試合を切り上げる。起こせたら `True`。

        ゲームが逃げたときに通るのと**同じマネージャを同じ引数で**起こす。
        実機の並びは `check_battle_end` → `BattleEndManager(app, 'escaped')` →
        2ミリ秒後に `execute("")` → `end_phase`（`233_probe_colosseum` のログ）。
        勝ったときの `end_type` は `'won'` で、こちらは使わない。

        逃げた扱いなので `current_phase` は進まず、懸賞金も出ない。
        次に申し込むと同じ格の相手が作り直される（素のゲームで撤退したときと同じ）。
        """
        cls = ui.cls_of(END_MANAGER_CLS)
        if cls is None:
            write("WARN surrender: {} が見つからない".format(END_MANAGER_CLS))
            return False
        try:
            manager = cls(app, ESCAPED_END_TYPE)
        except Exception:
            ctx.log_exc("colosseum: cannot build the battle end manager")
            return False
        # 落ちても何をしようとしていたかが残るよう、起こす前に書く。
        write("surrender: ending the match as an escape")
        state["surrendered_at"] = time.monotonic()
        try:
            prepare_escape(app)
        except Exception:
            ctx.log_exc("colosseum: cannot prepare the escape")
        try:
            manager.execute("")
        except Exception:
            ctx.log_exc("colosseum: the escape ending failed")
            return False
        try:
            ensure_player_back(app)
        except Exception:
            ctx.log_exc("colosseum: cannot put the player back in the party")
        return True

    def prepare_escape(app):
        """ゲーム自身の逃走と同じ状態にしてから終わり方を起こす（`233_` 版3 の実機の比較）。

        ゲームが逃げたとき:
          判定の中で敵の一覧を空にする（1 → 0）。
          逃げた者は `app.party` から外して `escaped_member_in_battle` に預け、
          終わり方の実行の中でゲームが `app.party` へ戻す（party 0 → 1、預かり 1 → 0）。
        倒れたとき（こちらが切り上げる前）:
          ゲームが主人公を `app.party` から外すだけで、どこにも預けない。
          そのまま終わり方を起こすと、主人公が一覧に戻らず画面から消え、
          敵も一覧に残って 331 の闘技場の出口が足されなかった（実機）。
        なので外された主人公を預かりに入れ、敵の一覧を空にする（仕組みは GAME.md §2.10）。
        """
        enemies = getattr(app, "current_enemy_dict", None)
        if isinstance(enemies, dict) and enemies:
            names = list(enemies)
            enemies.clear()
            write("surrender: cleared {} enem{} like the game's own escape ({})".format(
                len(names), "y" if len(names) == 1 else "ies", ", ".join(names)))
        party = getattr(app, "party", None)
        removed = state.get("removed_player")
        if isinstance(party, dict) and PLAYER_KEY in party:
            return                          # まだ一覧に居る（外されていない）
        if removed is None:
            write("WARN surrender: the player left the party but nothing was kept to bring back")
            return
        escaped = getattr(app, "escaped_member_in_battle", None)
        if isinstance(escaped, dict):
            escaped[PLAYER_KEY] = removed
            write("surrender: handed the player to escaped_member_in_battle "
                  "for the game to bring back ({})".format(type(removed).__name__))
        else:
            write("WARN surrender: escaped_member_in_battle is {} -- will put the player "
                  "back by hand".format(type(escaped).__name__))

    def ensure_player_back(app):
        """ゲームが主人公を一覧へ戻さなかったときだけ、控えた値で戻す。"""
        party = getattr(app, "party", None)
        removed = state.pop("removed_player", None)
        if not isinstance(party, dict) or PLAYER_KEY in party:
            if isinstance(party, dict):
                write("surrender: the player is back in the party")
            return
        if removed is None:
            write("WARN surrender: the player is not in the party and cannot be put back")
            return
        party[PLAYER_KEY] = removed
        escaped = getattr(app, "escaped_member_in_battle", None)
        if isinstance(escaped, dict):
            escaped.pop(PLAYER_KEY, None)
        write("WARN surrender: the game did not bring the player back; put back by hand")

    @ctx.wrap("__main__:InstantaleApp.remove_party_member", required=False, safe=True)
    def remove_party_member(orig, self, member_id=None, *args, **kwargs):
        """闘技場の試合で主人公が一覧から外されるとき、その値を控える（切り上げで戻すため）。"""
        try:
            if (SURVIVE_DEFEAT and member_id == PLAYER_KEY
                    and getattr(self, "in_colosseum_battle", False)):
                party = getattr(self, "party", None)
                if isinstance(party, dict) and PLAYER_KEY in party:
                    state["removed_player"] = party[PLAYER_KEY]
        except Exception:
            ctx.log_exc("colosseum: cannot keep the player leaving the party")
        return orig(self, member_id, *args, **kwargs)

    @ctx.wrap("__main__:BattlePhaseManager.check_battle_end", required=False, safe=True)
    def check_battle_end(orig, self, *args, **kwargs):
        """闘技場で倒れたら、死なずに「負けて退いた」で試合を終える。

        ゲームオーバーはこの関数の中から作られる（`instantale.py:7791`。実機の呼び出し元）。
        体力を戻すのは**その判定より前**でないと間に合わないので、ここで先回りする。

        体力を戻しただけでは試合が続いてしまう（次の一撃で結局倒れる）ので、
        続けて逃走と同じ終わり方を起こす。起こせたらゲームの判定は通さない
        （試合はもう終わっている）。起こせなかったときは素の判定へ落とし、
        少なくとも**その一撃では死なない**状態にしておく。
        """
        if getattr(self, SURRENDERED_MARK, False):
            return True                     # こちらが終わらせた戦闘。もう判定しない
        try:
            if SURVIVE_DEFEAT and not state["survived"]:
                app = getattr(self, "app", None) or ui.find_app()
                if fallen(app):
                    player = getattr(app, "player", None)
                    hp = getattr(player, "current_hp", None)
                    player.current_hp = SURVIVE_HP
                    state["survived"] = True
                    screen.say(app, DEFEAT_TEXT)
                    if surrender(app):
                        setattr(self, SURRENDERED_MARK, True)
                        write("surrender: hp {} -> {}; ended the match as an escape"
                              .format(hp, SURVIVE_HP))
                        return True
                    write("WARN surrender: hp {} -> {} but the match goes on"
                          .format(hp, SURVIVE_HP))
        except Exception:
            ctx.log_exc("colosseum: cannot end the match as a loss")
        return orig(self, *args, **kwargs)

    def install_after_surrender(name):
        @ctx.wrap("__main__:BattlePhaseManager.{}".format(name), required=False)
        def after_surrender(orig, self, *args, **kwargs):
            """こちらが切り上げた戦闘の、残りの手を飛ばす。

            切り上げた後もゲームの戦闘の繰り返しが止まらず、敵の次の手を処理しようとして、
            一覧から消えた敵を引いて `KeyError`（`resolve_opponents`、`instantale.py:7266`）で
            ワーカースレッドが死んだ（実機）。`check_battle_end` が `True` を返せば止まるのかは
            確かめていないので、止まらなかったときの受けとしてここで止める。
            触るのは切り上げた `BattlePhaseManager` だけ（印は実体に付ける）。
            """
            if getattr(self, SURRENDERED_MARK, False):
                write("surrender: skipped {} after the match ended".format(name))
                return None
            return orig(self, *args, **kwargs)

    for _name in AFTER_SURRENDER_STEPS:
        install_after_surrender(_name)

    @ctx.wrap(ENEMY_DISPLAY_TARGET, required=False)
    def enemy_display(orig, *args, **kwargs):
        """切り上げた直後の敵の欄の更新で起きる 0 除算だけを握る。

        こちらは敵の手番の途中で試合を終わらせる（体力が尽きるのは敵の一撃）。
        その一撃の演出が予約した敵の欄の更新が、戦闘の欄が畳まれた（大きさ 0）後に走り、
        `new_hud.py:2306` の `ZeroDivisionError` で落ちた（実機。終わってから約 1 秒後）。
        ゲーム自身の「逃げる」は自分の手番なので、この予約が残らない。
        戦闘はもう終わっていて敵の欄は要らないので、描かないのが正しい。
        切り上げてから `SURRENDER_GUARD_SECONDS` 秒の間の、この例外だけを握る。
        """
        try:
            return orig(*args, **kwargs)
        except ZeroDivisionError:
            since = state.get("surrendered_at")
            if since is None or time.monotonic() - since > SURRENDER_GUARD_SECONDS:
                raise
            write("surrender: skipped an enemy panel update after the match ended "
                  "(ZeroDivisionError)")
            return None

    # -------------------------------------------------------------- 懸賞金
    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        """懸賞金の文の額を、こちらが払う額に書き換える。

        試合の終わりの間（`state["reward"]`）だけ働く。
        書き換えられた回だけ、後で所持金の差額を足す（食い違わせないため）。
        """
        pending = state.get("reward")
        if pending and isinstance(context, str) and REWARD_MARK in context:
            try:
                base = ui.parse_coin(context)
                if base is not None and base > 0:
                    want = max(0, int(round(base * pending["mult"])))
                    rewritten = replace_amount(context, base, want)
                    if rewritten is not None:
                        pending["base"], pending["want"] = base, want
                        context = rewritten
                        write("reward: {} -> {} (x{:.2f})".format(
                            base, want, pending["mult"]))
                    else:
                        write("WARN reward: cannot rewrite the amount in {!r}".format(
                            context))
            except Exception:
                ctx.log_exc("colosseum: cannot rewrite the prize")
        return orig(self, context, *args, **kwargs)

    def settle(app, before):
        """ゲームが入れた額と、こちらが言った額の差を埋める。"""
        pending = state.get("reward") or {}
        want = pending.get("want")
        if want is None:
            return                      # 文が来なかった（勝っていない・書き換えられなかった）
        after = ui.gold_of(app)
        if not isinstance(before, int) or not isinstance(after, int):
            return
        moved = after - before
        if moved <= 0:
            write("WARN reward: the game paid {} but we promised {}".format(moved, want))
            return
        diff = want - moved
        if diff == 0:
            return
        ui.add_gold(app, diff, on_error=lambda msg:
                    write("WARN reward: cannot correct: {}".format(msg)))
        write("reward: the game paid {}, we promised {}; corrected {:+d}".format(
            moved, want, diff))

    @ctx.wrap("__main__:BattleEndInColosseum.end_phase", required=False)
    def end_phase(orig, self, *args, **kwargs):
        """勝ったときの懸賞金に倍率を乗せる。所持金はこの中で動く（実機）。"""
        app = getattr(self, "app", None) or ui.find_app()
        rank = None
        try:
            rank = expected_rank(app)
        except Exception:
            ctx.log_exc("colosseum: cannot read the opponent's rank")
        multiplier = 1.0
        try:
            multiplier = reward_multiplier(app, rank)
        except Exception:
            ctx.log_exc("colosseum: cannot work out the prize")
        if multiplier == 1.0:
            return orig(self, *args, **kwargs)
        before = ui.gold_of(app)
        state["reward"] = {"mult": multiplier, "base": None, "want": None}
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                settle(app, before)
            except Exception:
                ctx.log_exc("colosseum: cannot settle the prize")
            state["reward"] = None

    # -------------------------------------------------------- 死なずに退く描写
    def narration_kind(manager_name):
        """足す相手なら `"referee"` / `"summary"`。それ以外は None。"""
        if not NONLETHAL_NARRATION or not isinstance(manager_name, str):
            return None
        if manager_name.startswith(REFEREE_PREFIX):
            app = ui.find_app()
            if getattr(app, "in_colosseum_battle", False):
                return "referee"
            return None
        if manager_name in SUMMARY_MANAGERS:
            # 勝った試合は `colosseum_battle_summarizer` の中から来る。
            # 逃げた試合（こちらが負けとして切り上げた試合も）は `BattleEndManager.end_phase` から
            # 衛兵戦の名前のまま直接来るので、闘技場の旗で見分ける（旗は `end_phase` の後で下りる）。
            # 足さなかった回は、主人公が消え去る締めが書かれた（実機）。
            if state["summarizing"] > 0:
                return "summary"
            if getattr(ui.find_app(), "in_colosseum_battle", False):
                return "summary"
        return None

    @ctx.wrap("scripts.llm.llm_manager:colosseum_battle_summarizer", required=False)
    def battle_summarizer(orig, *args, **kwargs):
        """試合の要約を頼んでいる間だけ印を立てる（送り口で衛兵戦と見分けるため）。"""
        state["summarizing"] += 1
        try:
            return orig(*args, **kwargs)
        finally:
            state["summarizing"] -= 1

    def install_send(target):
        """送り口1つに包みを掛ける。`llm.watch_aliases` が対象ごとに呼ぶ。"""
        @ctx.wrap(target, required=False, safe=True)
        def narration_send(orig, *args, **kwargs):
            manager_name = args[0] if args else kwargs.get("manager_name")
            try:
                kind = narration_kind(manager_name)
                if kind is not None:
                    note = NONLETHAL_SUMMARY_NOTE if kind == "summary" else NONLETHAL_NOTE
                    if len(args) >= 2 and isinstance(args[1], list):
                        replaced = append_note(args[1], note)
                        if replaced is not args[1]:
                            args = args[:1] + (replaced,) + args[2:]
                            write("nonlethal: {} ({})".format(manager_name, kind))
                    elif isinstance(kwargs.get("message"), list):
                        replaced = append_note(kwargs["message"], note)
                        if replaced is not kwargs["message"]:
                            kwargs = dict(kwargs, message=replaced)
                            write("nonlethal: {} ({})".format(manager_name, kind))
            except Exception:
                ctx.log_exc("colosseum: cannot add the nonlethal note")
            return orig(*args, **kwargs)

    # 送り口はプロバイダの初期化後に生える。後生えと別名はローダの見張りに任せる。
    llm.watch_aliases(ctx, list(NARRATION_SEND_TARGETS), install_send,
                      label="colosseum custom")

    ctx.log("colosseum custom: ready")
