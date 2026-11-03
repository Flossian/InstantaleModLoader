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
| 犯人はゲームか MOD か | 記録した呼び出しの起点が MOD の枠か（`mod_caller`。版6）。違えばゲーム側 |

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
総当たりで呼ぶのは引数から戻り値を決めるだけの関数で、ゲームの状態を触らない。

版3: 読み取り専用の約束を1つ破っていたので直し、ログを絞った（問いは VERIFICATION_LOG.md §2.36 で決着済み）。

- 表の総当たりから `get_npc_exp_level` を外した。
  モジュール共用の `random` を引く関数で（表の値が注入ごとに全部違った）、
  注入のたびにゲームの乱数の列を 81 回ぶん進めていた。
  残りの3本は決定的で毎回同じ値だったので、1プロセスに1回にした
- `Character.__init__` はプレイヤーだけを写す。
  版2は開始処理の窓の間に生まれる NPC も全員写していて、
  ロードのたびに全員を作り直すので1人3行・呼び出し元つきで積もった（1か月で約40MB、このログの8割）。
  NPC は判定の前に素通しにし、呼び出し元も組まない
- 2秒ごとの見張りは、レベルとレベルから決まる値だけを素の値で比べる。
  版2は所持金や HP まで repr して比べていたので、行動のたびに1行出ていた

版4: 見出し・見張りの開始の行を1プロセスに1回にし、見張りが最後に見た値を世代をまたいで引き継ぐ。
版3までは注入し直しのたびに見出しと開始の行が積もり、プレイヤーが居れば `player appeared` も重なっていた。

版5: 作成画面の「人生を始める」の対象を `scripts.hud.hud_charamake` に直した。
main_025 で画面のクラスがこのモジュールへ移り、版4までの `scripts.create_world` の名前では包めていなかった。

版6: 呼び出し元の「MOD か」の判定と、記録の数え方を直した。

- 版5の `[MOD frames: …]` は、スタックのどこかに MOD のファイルが1枚でもあれば陽性だった。
  ロードや NPC の生成を包んでいる MOD（`orig` をそのまま呼ぶだけの層）が必ず挟まるので、
  ほぼ全行が陽性で、判定として働いていなかった（30日で 9,301/10,773 行）。
  版6は内側から辿り、最も内側から続くゲーム側の枠を抜けた直後の1枚が MOD かで決める（`mod_caller`）。
  ローダ本体・自分・受け取った `orig` を呼んでいる最中の包みの層は飛ばす。
  印は `[MOD caller: …]` に変えた（旧い行と混ぜて数えないため）
- MOD の置き場の判定を `frames.is_ours` に寄せた。版5は `runtime/mods` だけを見ていて、`local/` の MOD を見落としていた
- 他の probe の試し打ち（`sys` の `DRY_THREAD_MARK` が自分のスレッド）の間は記録しない。
  `243_` の総当たりと `236_` の試しが、窓のログの約半分を占めていた
- 窓の記録の件数（`MAX_INIT_LINES` など）を `sys` の `COUNTS_MARK` で数える。注入し直しで数え直さない
- 表の総当たりは1プロセス1回のまま、前回の結果（`out/new_character_tables.txt`）と同じなら1行だけ書く
"""

import datetime
import io
import os
import sys
import threading
import time

from instantale_modloader import frames, patch, ui

LOG_BASENAME = "new_character.log"
# 表の総当たりの前回の結果（版6）。`*.log` ではないので、注入のたびの世代送りに掛からない。
TABLES_BASENAME = "new_character_tables.txt"

# ローダが自分の被せたものに付ける印（`patch.py` の PATCH_MARK / __wrapper_of__）。
# 「底まで剥がせたか」の判定に使う。
# 推論で済ませない（`101_` の教訓）。
LOADER_MARKS = ("__instantale_patch__", "__wrapper_of__")

# 「MOD のフレームか」の判定の足場（版6）。
# MOD の置き場（`runtime/mods` と `local/`）は `frames.is_ours` で見る。
# そのうち自分自身とローダ本体は「MOD」に数えない。
# 比べるのは大文字小文字と区切りを均した実パス。
MY_FILE = os.path.normcase(os.path.abspath(__file__)).lower()
LOADER_DIR = os.path.join(
    os.path.normcase(os.path.dirname(os.path.abspath(frames.__file__))).lower(), "")
# 呼び出し元を辿る枠の上限。
CALLER_SCAN_DEPTH = 80

# 他の probe が試し打ちをしている間だけ置く印（`236_` / `243_` / `244_` と同じ名前）。
# 値は試しているスレッドの id。自分のスレッドならゲームの呼び出しではないので記録しない（版6）。
DRY_THREAD_MARK = "_instantale_probe_dry_run_thread"

# 窓の記録の件数（1プロセスに1組。`sys` に置く。版6）。
COUNTS_MARK = "_instantale_probe_newchar_counts"


def in_dry_run():
    """いまのスレッドで他の probe が試し打ちをしている最中か。"""
    return getattr(sys, DRY_THREAD_MARK, None) == threading.get_ident()


def process_counts():
    """窓の記録の件数。注入し直しで MOD が読み直されても数え直さない（版6）。"""
    found = getattr(sys, COUNTS_MARK, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, COUNTS_MARK, found)
    for key in ("inits", "calls", "level_events"):
        found.setdefault(key, 0)
    return found


def _norm(filename):
    try:
        return os.path.normcase(os.path.abspath(filename)).lower()
    except Exception:
        return ""


def passes_to_orig(frame, inner):
    """MOD の枠 `frame` が、受け取った `orig` を呼んでいる最中か（＝通過用の包みの層か）。

    `@ctx.wrap` の層は第1引数の `orig` を呼んで内側へ降りる。
    1つ内側の枠 `inner` がその `orig` の実行中なら、この枠は包みの層で、呼び出しの起点ではない。
    包みの本体からゲームの別の関数を呼んでいる最中なら `inner` は `orig` ではないので、起点として数える。
    """
    if inner is None:
        return False
    code = frame.f_code
    if code.co_argcount < 1:
        return False
    try:
        orig = frame.f_locals.get(code.co_varnames[0])
    except Exception:
        return False
    if orig is None or not callable(orig):
        return False
    original = getattr(orig, "__original__", None)
    if inner.f_code is getattr(orig, "__code__", None):
        if original is None:
            return True
        # ローダの素の包み（`patch.py` の `wrapper` / `guarded`）は同じ種類どうしでコードを共有するので、
        # 包んでいる中身（`old`）まで同じかで見分ける。
        try:
            return inner.f_locals.get("old") is original
        except Exception:
            return False
    # ゲームの関数は Nuitka で組まれていて、関数とフレームのコードが同じ物とは限らない。
    # 包みが素の関数を直に呼んでいる形だけ、名前で見る。
    return (original is None and not frames.is_ours(inner.f_code.co_filename)
            and getattr(orig, "__name__", None) == inner.f_code.co_name)


def mod_caller(start, own_file=MY_FILE):
    """呼び出しの起点が MOD なら、その枠の説明を返す。ゲーム側なら None（版6）。

    `start` から外へ辿る。
    自分のファイル・ローダ本体（`patch.py` など）・`orig` を呼んでいる最中の包みの層は飛ばす。
    最も内側から続くゲーム側の枠を抜けた直後の1枚が MOD の枠なら、その MOD が呼んだとみなす。
    ゲーム側の枠より先に MOD の枠に当たったとき（MOD が直に呼んだ）も MOD とみなす。
    抜けた直後の1枚がゲーム側なら、ゲームの呼び出しとみなして止める。
    """
    inner = None
    frame = start
    in_game = left_game = False
    depth = 0
    while frame is not None and depth < CALLER_SCAN_DEPTH:
        depth += 1
        code = frame.f_code
        name = code.co_filename
        if frames.is_ours(name):
            path = _norm(name)
            if (path == own_file or path.startswith(LOADER_DIR)
                    or passes_to_orig(frame, inner)):
                if in_game:
                    left_game = True
            else:
                return "{} ({}:{})".format(code.co_name, name, frame.f_lineno)
        else:
            if left_game:
                return None
            in_game = True
        inner = frame
        frame = frame.f_back
    return None


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
# 版3で `get_npc_exp_level` を外した（共用の乱数を引くので、呼ぶとゲームの乱数の列が進む）。
# 決定的な表だけを置くこと。
TABLE_TARGETS = (
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
# 版3で `Character.__init__` はプレイヤーだけになった（1回のロードで1〜2件）。
MAX_INIT_LINES = 40
MAX_CALL_LINES = 500
MAX_LEVEL_EVENTS = 80

# プレイヤーの値の見張り。
# 作成の後で誰かが書き換えるなら、この差分に出る。
POLL_SECONDS = 2.0

# 見張りが比べる項目。
# レベルと、レベルから決まる値だけ（所持金や今の HP は行動のたびに動くので比べない。版3）。
POLL_FIELDS = ("experience_level", "max_hp", "original_max_hp",
               "max_physical_integrity", "original_max_physical_integrity")

# 表の総当たりを1プロセスに1回にするための印（`sys` に置く。TECH.md §3.6）。
TABLES_MARK = "_instantale_probe_newchar_tables"

# 見出しと見張りの開始の行を1プロセスに1回にする印と、
# 見張りが最後に見たプレイヤーの値の控え（どちらも `sys` に置く。版4）。
# 控えを世代をまたいで引き継ぐので、注入し直しても同じプレイヤーを「appeared」と書き直さない。
ARMED_MARK = "_instantale_probe_newchar_armed"
LAST_MARK = "_instantale_probe_newchar_last"


def apply(ctx):
    state = {
        # 窓（開始処理の最中か）。
        # 閉じる時刻と、開けた対象の名前。
        "window_until": 0.0,
        "window_by": None,
        # 総当たりの最中は自分の呼び出しを記録しない。
        "probing": False,
    }
    # 窓の記録の件数は `sys` に置く（版6）。
    counts = process_counts()

    write = ctx.logger(LOG_BASENAME, stamp=False)
    tables_path = ctx.out_path(TABLES_BASENAME)

    def stamp():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def window_open():
        return time.monotonic() < state["window_until"]

    def open_window(by):
        state["window_until"] = time.monotonic() + WINDOW_SECONDS
        state["window_by"] = by

    def caller_line():
        """呼び出し元の連鎖と、呼び出しの起点が MOD かどうか。

        `frames.caller` はローダと mod のフレームを飛ばすので、
        連鎖だけでは「MOD が呼んだ」ことが見えない。
        そこで生のスタックを `mod_caller` で辿り、起点が MOD なら併記する。
        これが「ゲームのバグか、こちらのバグか」を分ける唯一の手掛かりになる。
        版6で、通過するだけの包みの層を数えない判定にした（モジュールの docstring）。
        """
        chain = frames.caller(depth=5)
        try:
            found = mod_caller(sys._getframe(1))
        except Exception:
            found = None
        if found:
            return "{}   [MOD caller: {}]".format(chain, found)
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

    @ctx.wrap("scripts.hud.hud_charamake:CharacterCreateScreen.start_story",
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
            # 問いはプレイヤーだけ。NPC はここで素通しにする（版3。版2は窓の間の NPC も全員写していた）。
            is_player = bool(kwargs.get("is_player"))
            config = kwargs.get("config")
            if isinstance(config, dict) and config.get("is_player"):
                is_player = True
            if not is_player:
                return result
            if counts["inits"] >= MAX_INIT_LINES or in_dry_run():
                return result
            counts["inits"] += 1
            write("[{}] Character.__init__ PLAYER name={} experience_level={} "
                  "experience_point={} max_hp={} max_physical_integrity={}".format(
                      stamp(),
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
                # 上限と試し打ちの印を先に見る（版6）。
                if counts["level_events"] >= MAX_LEVEL_EVENTS or in_dry_run():
                    return result
                after = frames.attr(self, "experience_level")
                if before == after and not window_open():
                    return result
                counts["level_events"] += 1
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
                # 他の probe の試し打ち（版6）は、自分の総当たり（`probing`）と同じく記録しない。
                if state["probing"] or not window_open() or in_dry_run():
                    return result
                if counts["calls"] >= MAX_CALL_LINES:
                    return result
                counts["calls"] += 1
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
          VERIFICATION_LOG.md §2.2。同じ形の表なら、ここでも必ず投げる）。
          `patch.unwrap_ours` では剥がれない。
          あちらは**今回の注入で付けた層は残す**（それが仕事）ので、
          剥がしたいこの用途には使えない
        - 落ち始めたらそこで止め、どこから落ちるかを書く。
          表の上限はそれ自体が知りたい事実（`PRICE_TABLE_MAX = 76` と同じ）
        """
        # 表は決定的な関数だけで、毎回同じ値になる（版2の 1,008 回で1通り）。
        # 注入し直しで出直さないよう、印は `sys` に置く（版3）。
        if getattr(sys, TABLES_MARK, False):
            return
        functions = sys.modules.get(FUNCTIONS_MODULE)
        if functions is None:
            write("[{}] {} not loaded; no tables".format(stamp(), FUNCTIONS_MODULE))
            return
        setattr(sys, TABLES_MARK, True)
        state["probing"] = True
        # 行は時刻を付けずに貯める。前回と比べるのは時刻を除いた本文（版6）。
        lines = []
        try:
            for name, low, high in TABLE_TARGETS:
                func, depth, still_ours = bare(getattr(functions, name, None))
                if func is None:
                    lines.append("table {}: missing".format(name))
                    continue
                if still_ours:
                    # 底に着いていない。
                    # この表は自分たちの層を測っているので、
                    # 読み方を間違えないように結果ではなく事実だけ書く（101_ の教訓）。
                    lines.append("table {}: still wrapped after {} layer(s); "
                                 "not measuring".format(name, depth))
                    continue
                pairs, stopped = [], None
                for value in range(low, high + 1):
                    try:
                        pairs.append("{}->{}".format(value, func(value)))
                    except Exception as exc:
                        stopped = "{} raises from {} onward: {}: {}".format(
                            name, value, type(exc).__name__, exc)
                        break
                lines.append("table {}({}..{}) unwrapped={}: {}{}".format(
                    name, low, high, depth, "  ".join(pairs),
                    "   [{}]".format(stopped) if stopped else ""))
        except Exception:
            ctx.log_exc("new character probe: table dump failed")
        finally:
            state["probing"] = False
        write_tables(lines)

    def write_tables(lines):
        """表を書く。前回の結果（`TABLES_BASENAME`）と同じなら1行だけ（版6）。

        表は決定的な関数だけなので、ゲームが変わらない限り毎回同じになる。
        版5までは毎プロセス3行・約3.8KBを書いていた。
        変わったときだけ全文を書き、控えも差し替える。
        """
        if not lines:
            return
        text = "\n".join(lines)
        previous = None
        try:
            with io.open(tables_path, encoding="utf-8") as fh:
                previous = fh.read()
        except Exception:
            previous = None
        if previous == text:
            write("[{}] tables unchanged since the last run ({} table(s), {} chars; "
                  "full text in out/{})".format(stamp(), len(lines), len(text),
                                                TABLES_BASENAME))
            return
        now = stamp()
        write("\n".join("[{}] {}".format(now, line) for line in lines))
        if previous is not None:
            write("[{}] tables CHANGED from the last run (out/{} now holds this run)"
                  .format(now, TABLES_BASENAME))
        ctx.write_text(tables_path, text)

    def start_poll(announce):
        """プレイヤーの値を見張る。作成の後で書き換わるならここに出る。

        メインスレッドで回すため
        `on_ready` 経由（`boot()` は注入したリモートスレッドの上に居る）。
        注入し直したら古い見張りは自分から降りる（TECH.md §3.6.1・`211_` の教訓）。
        見張りそのものは世代ごとに起こすが、開始の行は `announce` のときだけ書く（版4）。
        """
        try:
            from kivy.clock import Clock
        except Exception:
            ctx.log("new character probe: no kivy Clock; polling disabled",
                    level="WARN")
            return

        def poll(_dt):
            if ctx.superseded():
                # 降りたことは書かない（版4）。
                # 注入し直しのたびに1行ずつ積もり、新しい世代の見張りが続きを引き継ぐので読む材料にならない。
                return False
            try:
                # 比べるのはレベル系の素の値だけ（版3）。
                # 行を書くときだけ全項目を写す。
                app = ui.find_app()
                player = frames.attr(app, "player") if app is not None else None
                if player in (None, frames.MISSING):
                    return True
                keyed = tuple(frames.attr(player, f) for f in POLL_FIELDS)
                last = getattr(sys, LAST_MARK, None)
                if last is None:
                    note_player("player appeared (poll)", app)
                elif keyed != last:
                    note_player("player changed (poll)", app)
                setattr(sys, LAST_MARK, keyed)
            except Exception:
                ctx.log_exc("new character probe: polling failed")
            return True

        Clock.schedule_interval(poll, POLL_SECONDS)
        if announce:
            write("[{}] polling app.player every {}s (gen {})".format(
                stamp(), POLL_SECONDS, ctx.generation))

    def on_ready():
        # 見出しは1プロセスに1回（版4。版3までは注入のたびに出ていた）。
        first = not getattr(sys, ARMED_MARK, False)
        if first:
            setattr(sys, ARMED_MARK, True)
            write("")
            write("=" * 78)
            write("[{}] new character probe armed (gen {})".format(
                stamp(), ctx.generation))
        dump_tables()
        start_poll(first)

    # キーに世代を混ぜる。
    # 混ぜないと2回目以降の注入で積まれず、
    # 古い見張りが回り続ける（TECH.md §3.6・`211_` と同じ）。
    ctx.on_ready(on_ready,
                 key="214_probe_new_character:ready:{}".format(ctx.generation))
