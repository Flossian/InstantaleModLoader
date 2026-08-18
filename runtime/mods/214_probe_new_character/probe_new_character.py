# -*- coding: utf-8 -*-
"""計測: 新しく作ったキャラクタが最初からレベル60で始まる経路。

##### 何を決めるための計測か

新規作成したキャラクタが 経験値0のままレベル60 で始まる。
セーブから読み取れることと、それをバグと判断した根拠は VERIFICATION_LOG.md
§2.36 にある。
要点だけ:

- 1手も進んでいないセーブ（経過日0・経験値0・未クリア・持ち物空）でレベル60。
  始まりの町の冒険者は 8〜9、依頼の難易度は 2〜5 で、世界と釣り合っていない
- 同じセーブの中で辻褄が合っていない。
  体力上限はレベル1の値、`max_hp` はレベル60ぶん。
  「世界に合わせて強く始める」設計ではなく、どこかで 60 が紛れ込んでいる
- 以前の新規開始は レベル1 から始まっていた。
  後から生えた壊れ方

セーブから分かるのはここまで。
**どこで 60 になるのかはセーブに残らない**ので、作成から最初の1手までを実機で写す。

##### 4つの問いに答える

| 問い | 見るところ |
|---|---|
| 作成画面はレベルを渡しているのか | `CharacterVM.to_dict()` の中身（属性と `point_use` だけで、レベルの欄は無いはず） |
| プレイヤーの `Character` は何を受け取って生まれるか | `Character.__init__` の直後の `experience_level`（60 で生まれるのか、0 で生まれて後から上がるのか） |
| 60 を返しているのは誰か | 開始処理の窓の間だけ `scripts.functions` を包み、引数→戻り値と呼び出し元を記録 |
| 犯人はゲームか MOD か | 記録した呼び出し元にこちら側のファイルが居るか（`frames.is_ours`）。居なければゲーム側 |

最後の1つがこの計測の主目的。
ローダは 50 本ほどの MOD を当てているので、「ゲームのバグ」と断じる前に、
こちらが犯人でないことを同じログの中で示す。

##### 表を総当たりして控える

`get_npc_exp_level` / `clamp_character_level` / `get_max_physical_integrity` /
`get_days_elapsed_experience_point` を注入時に総当たりして書き出す（`101_` の上流プローブと同じ手）。
目的は2つ:

- 60 がどれかの表の出力なのかが分かる。
  `clamp_character_level` の上限は `CHARACTER_LEVEL_MAX = 100` なのでクランプの結果ではないが、
  `get_npc_exp_level(76)`（NPC 難易度の上限）が 60 前後なら、**プレイヤーに
  NPC の経路が使われている**線が濃くなる
- レベル→体力上限の対応が、セーブから読んだ値（VERIFICATION_LOG.md
  §2.36 の表）と合うかを確かめられる。
  合えば「体力上限 10 はレベル1の値」の裏取りになる

総当たりの呼び出しは自分の記録から外す（`state["probing"]`）。
外さないと自分の測定で窓のログが埋まる。
`101_` が踏んだのと同じ穴。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
値は書かず、記録に失敗しても本体は必ず呼ぶ（`safe=True` と、書き込みの握り潰し）。
総当たりで呼ぶ4つは引数から戻り値を決めるだけの関数で、ゲームの状態を触らない。
"""

import datetime
import os
import sys
import time

from instantale_modloader import frames, patch, ui

LOG_BASENAME = "new_character.log"

# ローダが自分の被せたものに付ける印（`patch.py` の PATCH_MARK / __wrapper_of__）。
# 「底まで剥がせたか」の判定に使う。
# 推論で済ませない（`101_` の教訓）。
LOADER_MARKS = ("__instantale_patch__", "__wrapper_of__")

# 「MOD のフレームか」を自分で判定するための足場。
# `frames.is_ours` はローダの置き場との文字列比較なので、
# 区切り文字の揺れに弱い（オフライン検証で実際に外した）。
# 犯人がゲームかこちらかを分ける判定でそれに頼りたくないので、
# 正規化した実パスで比べる。
# 自分自身とローダ本体は「MOD」に数えない。
MY_FILE = os.path.normcase(os.path.abspath(__file__))
MODS_ROOT = os.path.normcase(os.path.dirname(os.path.dirname(MY_FILE)))
LOADER_MARK = os.path.normcase("instantale_modloader")


def bare(func):
    """包まれる前の素の関数を返す `(関数, 剥がした層数, まだ印が残っているか)`。

    `__original__` の連鎖を最下層までたどる（連鎖が壊れていても止まるよう上限つき）。
    底に着いたかを推論で決めず、ローダの印が残っていないかで確かめる。
    """
    return patch.unwrap(func)

# 開始処理の入口。
# どれかに入ったら「窓」を開け、その間だけ細かく記録する。
# `load_game_new` が新規で `start_game` が続きから。
# とは名前だけでは決められないので両方包み、ログで見分ける（GAME.md §1.3）。
START_TARGETS = (
    "__main__:InstantaleApp.load_game_new",
    "__main__:InstantaleApp.start_game",
    "__main__:InstantaleApp.get_character_data",
)

# 窓が開いている間だけ写す `scripts.functions` の関数。
# レベル・属性・体力上限を決めていそうなものを、
# 1つに絞らず並べる（推測で絞ると空振りする）。
FUNCTIONS_MODULE = "scripts.functions"
WATCHED_FUNCTIONS = (
    "get_npc_exp_level",
    "clamp_character_level",
    "get_character_attributes",
    "get_npc_attributes",
    "get_max_physical_integrity",
    "get_days_elapsed_experience_point",
    "get_quest_difficulty_from_stat",
    "get_quest_appropriate_stat",
    "get_area_average_difficulty",
)

# 注入時に総当たりする表と範囲 `(名前, 最小, 最大)`。
TABLE_TARGETS = (
    ("get_npc_exp_level", 0, 80),
    ("clamp_character_level", -5, 120),
    ("get_max_physical_integrity", 1, 80),
    ("get_days_elapsed_experience_point", 1, 80),
)

# プレイヤーから写す項目。
# レベルと、
# レベルから決まるはずの値を並べて置く（辻褄が合っているかを1行で読めるようにする）。
PLAYER_FIELDS = ("experience_level", "experience_point",
                 "current_hp", "max_hp", "original_max_hp",
                 "physical_integrity", "max_physical_integrity",
                 "original_max_physical_integrity",
                 "age", "gold", "original_ability_scores")

# 窓を開けておく秒数。
# 開始処理は画像生成などで非同期に伸びるので長めに取る。
WINDOW_SECONDS = 300.0

# 記録の上限。
# 世界の NPC も同じ `Character.__init__` を通る（1回の開始で
# 100 体ほど生まれる）ので、窓の中でも打ち止めを置く。
MAX_INIT_LINES = 300
MAX_CALL_LINES = 500
MAX_LEVEL_EVENTS = 80

# プレイヤーの値の見張り。
# 作成の後で誰かが書き換えるなら、この差分に出る。
POLL_SECONDS = 2.0


def apply(ctx):
    state = {
        # 窓（開始処理の最中か）。
        # 閉じる時刻と、開けた対象の名前。
        "window_until": 0.0,
        "window_by": None,
        "inits": 0,
        "calls": 0,
        "level_events": 0,
        # 総当たりの最中は自分の呼び出しを記録しない。
        "probing": False,
        # 見張りが最後に見たプレイヤーの値。
        "last": None,
    }

    write = ctx.logger(LOG_BASENAME, stamp=False)

    def stamp():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def window_open():
        return time.monotonic() < state["window_until"]

    def open_window(by):
        state["window_until"] = time.monotonic() + WINDOW_SECONDS
        state["window_by"] = by

    def caller_line():
        """呼び出し元の連鎖と、そこにこちら側のファイルが居るかどうか。

        `frames.caller` はローダと mod のフレームを飛ばすので、
        連鎖だけでは「MOD が呼んだ」ことが見えない。
        そこで生のスタックも別に走査して、
        こちら側のファイル（ローダ自身は除く）が居れば併記する。
        これが「ゲームのバグか、こちらのバグか」を分ける唯一の手掛かりになる。
        """
        chain = frames.caller(depth=5)
        ours = []
        index = 1
        while index < 40:
            try:
                frame = sys._getframe(index)
            except Exception:
                break
            index += 1
            name = frame.f_code.co_filename
            try:
                normalized = os.path.normcase(os.path.abspath(name))
            except Exception:
                continue
            if not normalized.startswith(MODS_ROOT):
                continue
            if normalized == MY_FILE or LOADER_MARK in normalized:
                continue
            ours.append("{} ({}:{})".format(
                frame.f_code.co_name, name, frame.f_lineno))
        if ours:
            return "{}   [MOD frames: {}]".format(chain, " <- ".join(ours[:3]))
        return chain

    # -- プレイヤーの写し取り ------------------------------------------------

    def player_snapshot(app=None):
        if app is None:
            app = ui.find_app()
        if app is None:
            return None
        player = frames.attr(app, "player")
        if player in (None, frames.MISSING):
            return None
        snap = {"name": frames.attr(player, "name")}
        for field in PLAYER_FIELDS:
            snap[field] = frames.attr(player, field)
        return snap

    def snapshot_line(snap):
        if not snap:
            return "<no player>"
        parts = ["name={}".format(frames.repr_value(snap.get("name")))]
        for field in PLAYER_FIELDS:
            parts.append("{}={}".format(field, frames.repr_value(snap.get(field))))
        return "  ".join(parts)

    def note_player(tag, app=None):
        snap = player_snapshot(app)
        write("[{}] {}: {}".format(stamp(), tag, snapshot_line(snap)))
        if snap:
            level = snap.get("experience_level")
            point = snap.get("experience_point")
            if isinstance(level, int) and level > 1 and point in (0, None):
                # 再現の合図。
                # 1行で目に付くようにしておく。
                write("    ^^^ MISMATCH experience_level={} with experience_point={} "
                      "(a fresh character should be level 1)".format(level, point))
        return snap

    # -- 作成画面が渡すもの --------------------------------------------------

    @ctx.wrap("scripts.create_world:CharacterVM.to_dict", required=False, safe=True)
    def vm_to_dict(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if isinstance(result, dict):
                # 長い本文（`profile` / `look_description`）は要らない。
                brief = {k: v for k, v in result.items()
                         if k not in ("profile", "look_description")}
                write("[{}] CharacterVM.to_dict -> keys={} {}".format(
                    stamp(), sorted(result.keys()), frames.repr_value(brief)))
                write("    caller: {}".format(caller_line()))
        except Exception:
            ctx.log_exc("new character probe: to_dict record failed")
        return result

    @ctx.wrap("scripts.create_world:CharacterCreateScreen.start_story",
              required=False, safe=True)
    def start_story(orig, self, *args, **kwargs):
        open_window("CharacterCreateScreen.start_story")
        write("")
        write("=" * 78)
        write("[{}] start_story (the creation screen hands over)".format(stamp()))
        write("    caller: {}".format(caller_line()))
        try:
            vm = frames.attr(self, "character_vm")
            if vm is not frames.MISSING:
                write("    character_vm: {}".format(frames.repr_value(vm)))
        except Exception:
            ctx.log_exc("new character probe: start_story record failed")
        return orig(self, *args, **kwargs)

    # -- 開始処理の窓 --------------------------------------------------------

    def hook_start(target):
        label = target.rsplit(":", 1)[-1]

        @ctx.wrap(target, required=False, safe=True)
        def start(orig, self, *args, **kwargs):
            open_window(label)
            write("")
            write("-" * 78)
            write("[{}] {} enter args={} kwargs={}".format(
                stamp(), label, frames.repr_value(args),
                frames.repr_value(kwargs)))
            write("    caller: {}".format(caller_line()))
            note_player("{} before".format(label), self)
            try:
                return orig(self, *args, **kwargs)
            finally:
                note_player("{} after".format(label), self)
        return start

    for target in START_TARGETS:
        hook_start(target)

    # -- プレイヤーの `Character` はどう生まれるか ---------------------------

    @ctx.wrap("scripts.characters:Character.__init__", required=False, safe=True)
    def character_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            is_player = bool(kwargs.get("is_player"))
            config = kwargs.get("config")
            if isinstance(config, dict) and config.get("is_player"):
                is_player = True
            if not is_player and not window_open():
                return result
            if state["inits"] >= MAX_INIT_LINES:
                return result
            state["inits"] += 1
            write("[{}] Character.__init__{} name={} experience_level={} "
                  "experience_point={} max_hp={} max_physical_integrity={}".format(
                      stamp(), " PLAYER" if is_player else "",
                      frames.repr_value(frames.attr(self, "name")),
                      frames.repr_value(frames.attr(self, "experience_level")),
                      frames.repr_value(frames.attr(self, "experience_point")),
                      frames.repr_value(frames.attr(self, "max_hp")),
                      frames.repr_value(frames.attr(self, "max_physical_integrity"))))
            # レベルが引数で来たのか、既定の 0 で生まれたのかを分けて書く。
            # ここが 60 なら決まるのは作成の側、0 なら後から上げている側。
            if "experience_level" in kwargs:
                given = "passed by the caller: {!r}".format(
                    kwargs["experience_level"])
            else:
                given = "not in kwargs (default 0, or positional)"
            write("    experience_level {} (positional args={} kwargs={})".format(
                given, len(args), sorted(kwargs.keys())))
            write("    caller: {}".format(caller_line()))
        except Exception:
            ctx.log_exc("new character probe: __init__ record failed")
        return result

    # レベルが後から動くなら、この2つのどちらかを通るはず（GAME.md §2.17）。
    def hook_level_mover(target):
        label = target.rsplit(":", 1)[-1]

        @ctx.wrap(target, required=False, safe=True)
        def mover(orig, self, *args, **kwargs):
            before = frames.attr(self, "experience_level")
            result = orig(self, *args, **kwargs)
            try:
                after = frames.attr(self, "experience_level")
                if before == after and not window_open():
                    return result
                if state["level_events"] >= MAX_LEVEL_EVENTS:
                    return result
                state["level_events"] += 1
                write("[{}] {} name={} level {} -> {} args={}".format(
                    stamp(), label,
                    frames.repr_value(frames.attr(self, "name")),
                    frames.repr_value(before), frames.repr_value(after),
                    frames.repr_value(args)))
                write("    caller: {}".format(caller_line()))
            except Exception:
                ctx.log_exc("new character probe: level mover record failed")
            return result
        return mover

    for target in ("scripts.characters:Character.levelup",
                   "scripts.characters:Character.gain_exp"):
        hook_level_mover(target)

    # -- 窓の間だけ `scripts.functions` を写す -------------------------------

    def hook_function(name):
        @ctx.wrap("{}:{}".format(FUNCTIONS_MODULE, name),
                  required=False, safe=True)
        def watched(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                if state["probing"] or not window_open():
                    return result
                if state["calls"] >= MAX_CALL_LINES:
                    return result
                state["calls"] += 1
                write("[{}] {}({}{}) -> {}".format(
                    stamp(), name, frames.repr_value(args),
                    " " + frames.repr_value(kwargs) if kwargs else "",
                    frames.repr_value(result)))
                write("    caller: {}".format(caller_line()))
            except Exception:
                ctx.log_exc("new character probe: function record failed")
            return result
        return watched

    for name in WATCHED_FUNCTIONS:
        hook_function(name)

    # -- 表の総当たりと見張り（プロセスに1回） -------------------------------

    def dump_tables():
        """引数から戻り値を決めるだけの関数を総当たりして控える。

        `101_` の上流プローブと同じ。
        ゲームの状態を触らないので、開始処理を待たずに注入時に取れる。
        ただし同じ mod が2つ、あちらの教訓を守る:

        - 包む前の素の関数を測る（`__original__` を最下層までたどる）。
          自分のラッパを測ると、`safe=True` のガードが例外を「フックの失敗」として拾い、
          `modloader.log` に総当たりぶんのトレースバックが並ぶ（`get_npc_employ_price` は 77 以上で KeyError を投げた。
          VERIFICATION.md §2.2。同じ形の表なら、ここでも必ず投げる）。
          `patch.unwrap_ours` では剥がれない。
          あちらは**今回の注入で付けた層は残す**（それが仕事）ので、
          剥がしたいこの用途には使えない
        - 落ち始めたらそこで止め、どこから落ちるかを書く。
          表の上限はそれ自体が知りたい事実（`PRICE_TABLE_MAX = 76` と同じ）
        """
        functions = sys.modules.get(FUNCTIONS_MODULE)
        if functions is None:
            write("[{}] {} not loaded; no tables".format(stamp(), FUNCTIONS_MODULE))
            return
        state["probing"] = True
        try:
            for name, low, high in TABLE_TARGETS:
                func, depth, still_ours = bare(getattr(functions, name, None))
                if func is None:
                    write("[{}] table {}: missing".format(stamp(), name))
                    continue
                if still_ours:
                    # 底に着いていない。
                    # この表は自分たちの層を測っているので、
                    # 読み方を間違えないように結果ではなく事実だけ書く（101_ の教訓）。
                    write("[{}] table {}: still wrapped after {} layer(s); "
                          "not measuring".format(stamp(), name, depth))
                    continue
                pairs, stopped = [], None
                for value in range(low, high + 1):
                    try:
                        pairs.append("{}->{}".format(value, func(value)))
                    except Exception as exc:
                        stopped = "{} raises from {} onward: {}: {}".format(
                            name, value, type(exc).__name__, exc)
                        break
                write("[{}] table {}({}..{}) unwrapped={}: {}{}".format(
                    stamp(), name, low, high, depth, "  ".join(pairs),
                    "   [{}]".format(stopped) if stopped else ""))
        except Exception:
            ctx.log_exc("new character probe: table dump failed")
        finally:
            state["probing"] = False

    def start_poll():
        """プレイヤーの値を見張る。作成の後で書き換わるならここに出る。

        メインスレッドで回すため
        `on_ready` 経由（`boot()` は注入したリモートスレッドの上に居る）。
        注入し直したら古い見張りは自分から降りる（TECH.md §3.6.1・`211_` の教訓）。
        """
        try:
            from kivy.clock import Clock
        except Exception:
            ctx.log("new character probe: no kivy Clock; polling disabled",
                    level="WARN")
            return

        def poll(_dt):
            if ctx.superseded():
                write("polling stopped (a newer injection took over)")
                return False
            try:
                snap = player_snapshot()
                if snap is None:
                    return True
                keyed = tuple(frames.repr_value(snap.get(f)) for f in PLAYER_FIELDS)
                if state["last"] is None:
                    note_player("player appeared (poll)")
                elif keyed != state["last"]:
                    note_player("player changed (poll)")
                state["last"] = keyed
            except Exception:
                ctx.log_exc("new character probe: polling failed")
            return True

        Clock.schedule_interval(poll, POLL_SECONDS)
        write("[{}] polling app.player every {}s (gen {})".format(
            stamp(), POLL_SECONDS, ctx.generation))

    def on_ready():
        write("")
        write("=" * 78)
        write("[{}] new character probe armed (gen {})".format(
            stamp(), ctx.generation))
        dump_tables()
        start_poll()

    # キーに世代を混ぜる。
    # 混ぜないと2回目以降の注入で積まれず、
    # 古い見張りが回り続ける（TECH.md §3.6・`211_` と同じ）。
    ctx.on_ready(on_ready,
                 key="214_probe_new_character:ready:{}".format(ctx.generation))
