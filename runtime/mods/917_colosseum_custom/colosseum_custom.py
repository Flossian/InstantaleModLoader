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

懸賞金は**相手の格だけ**で決まり、乱数は乗っていない（同じ格で2回やると同額）。
格30→173G / 43→282 / 44→288 / 58→359 / 71→426 / 85→452 / 97→454 で、
**格70を超えると頭打ち**（85 と 97 の差は 2G）。式そのものは当てていない。

## この MOD の当て所

| やること | 包む先 |
|---|---|
| 相手の**実際の強さ** | `scripts.functions:get_enemy_exp_lvl` / `get_enemy_attributes_base_point` の第2引数。レベルは難易度 + 1（GAME.md §2.20） |
| 相手の**描写** | `llm_manager:colosseum_enemy_generator` の第4引数（頼み文に載る格） |
| 保存される格 | `ColosseumMatchStart.generate_enemy_data` の戻りの `data.rank` |
| 懸賞金 | `BattleEndInColosseum.end_phase`。所持金はこの中で動く（`instantale.py:8105`） |
| 相手の格を告げる | `EntryColosseumMatchManager.method` の後 |
| 負けても死なない | `BattlePhaseManager.check_battle_end` の前（ここから `GameOverManager` が作られる）。体力を戻したうえで、逃げたときと同じ `BattleEndManager(app, 'escaped')` を起こして試合を切り上げる |

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
既出の闘士を頼み文に足す一文は 331 と同じ文面なので、
向こうが先に足していたら二重には足さない。
"""

import sys
import time

from instantale_modloader import frames, ui

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

#: 既に出た闘士を頼み文へ足す一文。`331_facility_investment` と同じ文面
#: （向こうが先に足していたら二重にしないため、頭の句で見分ける）。
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
    既に同じ一文が入っていれば（`331_` が先に足していれば）そのまま返す。
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
    state = {"survived": False, "reward": None, "window": None}

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
        return sum(values) / float(len(values))

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
        """敵の数値。**窓の中でしか触らない**（依頼の敵も同じ関数を通るため）。"""
        opened = window()
        if opened is None or rank_untouched():
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
        懸賞金もこの値で決まる（版1の実機。頼み文へ渡した値ではなくこちらが効いていた）。
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
        try:
            manager.execute("")
        except Exception:
            ctx.log_exc("colosseum: the escape ending failed")
            return False
        return True

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
                        write("surrender: hp {} -> {}; ended the match as an escape"
                              .format(hp, SURVIVE_HP))
                        return None
                    write("WARN surrender: hp {} -> {} but the match goes on"
                          .format(hp, SURVIVE_HP))
        except Exception:
            ctx.log_exc("colosseum: cannot end the match as a loss")
        return orig(self, *args, **kwargs)

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

    ctx.log("colosseum custom: ready")
