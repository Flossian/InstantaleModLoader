# -*- coding: utf-8 -*-
"""戦闘 BGM を、置いてある曲から戦闘の種類ごとの重みで選んで鳴らす。

何が問題か
----------
戦闘 BGM は `Assets/sounds/musics/battle/1. Echoes of Valhalla.mp3` の1曲に固定されている
（実機ログ `out/battle_bgm.log` の `play_music_from_src` は全てこのパス）。
曲を変えるには同名のファイルで上書きするしかなく、複数曲を持てないし、
ボス戦・闘技場で曲を分けることもできない。

何をするか
----------
曲を鳴らす口はプロセス内に1つしかない（GAME.md §2.11）。

    scripts.sounds:SoundManager.play_music_from_src(self, app, music_src)

ここを包み、渡された `music_src` が戦闘曲（`/musics/battle/` 配下）なら、
**どの曲を鳴らすかだけを差し替える**。
鳴らすタイミングも止め方もゲームのまま。
「戦闘曲を鳴らせ」という判断はゲームが下し、「どの戦闘曲か」をこの MOD が決める。

曲の置き場は2つ。

    <ゲーム>/Assets/sounds/musics/battle/   素の1曲が居る場所
    <ローダ>/state/musics/battle/           足したい曲を置く場所

両方を走査してファイル名で1つのプールにする（同名なら `state/` 側が勝つ）。
重みは `state/musics/battle/playlist.json` に曲ごと・戦闘の種類ごとに持つ。

    {"tracks": {"1. Echoes of Valhalla.mp3": {"normal": 100, "boss": 0, "colosseum": 50},
                "決戦.mp3":                   {"normal": 0,   "boss": 100, "colosseum": 50}}}

重みは比率で、同じ種類の合計に対する割合がそのまま確率になる
（合計を 100 に揃えれば書いた数字がそのままパーセント）。
0 はその種類では鳴らない。
見つかった曲は重み 0 で書き出す（3種類とも）。
**置いただけでは鳴らない**。設定画面（`tool.py`）で「使う」に入れた曲だけが候補になる。
何も入れていなければ素の曲が鳴る。
新しい曲を足すと次の戦闘（または次の注入）で 0 の行が増える。
書いた数字は消さない。

戦闘の種類の見分け方
--------------------
曲が鳴る時点の `app` のフラグで見る。

    in_colosseum_battle → colosseum
    in_boss_battle      → boss
    それ以外            → normal

ボス戦は曲が鳴る時点で `in_boss_battle=1`、闘技場は `in_colosseum_battle=1` が立っている
（実機ログ2世代。DOC.md §3.2 / §3.3）。
フラグに加えて `ColosseumMatchStart.execute` / `QuestEncounterFinalBoss.execute` を通った印（`pending`）も見る。
実機では3つの合図（フラグ・印・`enemy_type`）が毎回同じ答えを出したので、
どれか1つが欠けても判定は変わらない。
`BattleStartManager.__init__` の `enemy_type` も控えて、語に `boss` / `colosseum` が
入っていればそれも合図にする（採取済みの語は衛兵の `'guard'`（GAME.md §2.20）、
依頼中の戦闘の `'in_quest'`（通常もボスも同じ語）、闘技場の `'colosseum'`。7戦ぶん）。
どの合図で決めたかは毎回ログに残すので、実機で確かめるときはそこを読む。

`in_boss_battle` はボス戦の後の戦闘で 0 に戻っていた（1回観測。GAME.md §2.10 の
「未観測」はこの1回で更新できる）。`in_colosseum_battle` の 1→0 は未観測。
立ったままなら以後の戦闘が全部その種類の扱いになるので、その症状が出たら
ログの `flags=` と `pending=` を突き合わせる（DOC.md §3）。

鳴らし方
--------
選んだ曲が `Assets/` 側なら、ゲームが渡してきたパスのファイル名だけを差し替える
（ゲームが使っている相対パスの形をそのまま保つ）。
`state/` 側なら絶対パスをスラッシュ区切りで渡す。
どちらも `/musics/battle/` を含むので、`106_` の戦闘曲判定はそのまま効く。
鳴らせなかった（例外）ときはログに残して素の曲を鳴らし直すので、
曲の置き間違いでゲームが黙ることはない。
絶対パスがそのまま通ることは実機で確認済み（DOC.md §3.1）。

乱数は MOD 専用の `random.Random`（ゲームの乱数列をずらさない。GAME.md §2.11）。
"""

import os
import random
import sys
import time

# GUI から変えられる値。mod.json の "settings" と同じ名前・同じ既定値。
DEFAULT_WEIGHT = 100     # 設定画面で「使う」に入れたときの重み（本体は読まない。宣言と既定値を揃えるためにある）
AVOID_REPEAT = True      # 候補が2曲以上あれば前回と同じ曲は選ばない

LOG_BASENAME = "battle_bgm.log"     # 106_ / 207_ と同じログに時系列で並べる
LOG_TAG = "[BGMPICK]"

PLAYLIST_NAME = "playlist.json"
STATE_SUBDIR = ("musics", "battle")
ASSET_SUBDIR = ("Assets", "sounds", "musics", "battle")
BATTLE_DIR_MARK = "/musics/battle/"
EXTENSIONS = (".mp3", ".ogg", ".wav")

CATEGORY_NORMAL = "normal"
CATEGORY_BOSS = "boss"
CATEGORY_COLOSSEUM = "colosseum"
CATEGORIES = (CATEGORY_NORMAL, CATEGORY_BOSS, CATEGORY_COLOSSEUM)

# playlist.json に無かった曲に付ける重み。0 ＝ 載せるだけで鳴らさない。
NEW_TRACK_WEIGHT = 0

# 印（`pending`）の寿命（秒）。
# 闘技場の申し込みを取り消したまま別の戦闘に入ったとき、古い印で誤らないため。
PENDING_TTL = 180.0

PLAYLIST_HELP = [
    "戦闘 BGM の重み。曲名 → {normal: 通常戦闘, boss: ボス戦, colosseum: 闘技場}",
    "重みは比率。同じ種類の合計に対する割合が確率になる（合計 100 なら数字がそのままパーセント）",
    "0 はその種類では鳴らない。3種類とも 0 なら素の曲が鳴る",
    "曲は Assets/sounds/musics/battle と state/musics/battle の .mp3 / .ogg / .wav。同名なら state 側",
    "見つけた曲は 0 で書き足される。設定画面で使うに入れるまで鳴らない。書いた数字は消えない",
]

_rng = random.Random()


# --------------------------------------------------------------- 純関数
def is_battle_track(src):
    """ゲームが渡してきたパスが戦闘曲か。`106_` と同じ判定。"""
    if not isinstance(src, str) or not src:
        return False
    return BATTLE_DIR_MARK in ("/" + src.replace("\\", "/").lstrip("/")).lower()


def game_root():
    """ゲーム本体のフォルダ。`Assets/sounds/musics/battle` が在る場所を探す（`104_` と同じ順）。"""
    seen = []
    for get in (os.getcwd,
                lambda: os.path.dirname(os.path.abspath(sys.executable)),
                lambda: sys.prefix):
        try:
            base = get()
        except Exception:
            continue
        if not base or base in seen:
            continue
        seen.append(base)
        if os.path.isdir(os.path.join(base, *ASSET_SUBDIR)):
            return base
    return None


def list_tracks(folder):
    """フォルダ直下の曲ファイル名（ソート済み）。無ければ空。"""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names
                  if n.lower().endswith(EXTENSIONS)
                  and os.path.isfile(os.path.join(folder, n)))


def scan_tracks(asset_dir, state_dir):
    """{曲名: (絶対パス, 置き場)} を作る。同名なら `state` が勝つ。"""
    found = {}
    for folder, where in ((asset_dir, "assets"), (state_dir, "state")):
        if not folder:
            continue
        for name in list_tracks(folder):
            found[name] = (os.path.join(folder, name), where)
    return found


def coerce_weight(value):
    """重みを 0 以上の数にする。読めない値は 0。"""
    if isinstance(value, bool):
        return 100.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:      # NaN / 負
        return 0.0
    return number


def weight_of(entry, category):
    """曲1つの、その種類の重み。項目が無ければ 0（省いた種類では鳴らない）。"""
    if not isinstance(entry, dict):
        return 0.0
    return coerce_weight(entry.get(category, 0))


def sync_playlist(playlist, names, default_weight):
    """見つかった曲を playlist に足す。既にある行は触らない。

    戻りは (playlist, 追加BGMのリスト)。`playlist` は壊れていれば作り直す。
    """
    if not isinstance(playlist, dict):
        playlist = {}
    tracks = playlist.get("tracks")
    if not isinstance(tracks, dict):
        tracks = {}
    added = []
    for name in sorted(names):
        if name in tracks:
            continue
        tracks[name] = dict((c, default_weight) for c in CATEGORIES)
        added.append(name)
    out = {"_help": list(PLAYLIST_HELP), "tracks": tracks}
    return out, added


def candidates(playlist, found, category):
    """[(曲名, 重み)]。プールに実在し、重みが正のものだけ。"""
    tracks = (playlist or {}).get("tracks") or {}
    out = []
    for name, entry in tracks.items():
        if name not in found:
            continue
        weight = weight_of(entry, category)
        if weight > 0:
            out.append((name, weight))
    out.sort(key=lambda item: item[0])
    return out


def pick(playlist, found, category, last=None, avoid_repeat=True, rng=None):
    """重み付きで1曲選ぶ。候補が無ければ None。"""
    rng = rng or _rng
    pool = candidates(playlist, found, category)
    if not pool:
        return None
    if avoid_repeat and last is not None and len(pool) >= 2:
        pool = [item for item in pool if item[0] != last] or pool
    total = sum(w for _n, w in pool)
    roll = rng.random() * total
    for name, weight in pool:
        roll -= weight
        if roll < 0:
            return name
    return pool[-1][0]


def classify(flags, pending=None, enemy_type=None):
    """戦闘の種類。フラグ → 印 → enemy_type の語 の順で決める。

    `flags` は {名前: 真偽}。`pending` は印（None / "boss" / "colosseum"）。
    戻りは (種類, 決め手)。
    """
    flags = flags or {}
    if flags.get("in_colosseum_battle"):
        return CATEGORY_COLOSSEUM, "flag in_colosseum_battle"
    if pending == CATEGORY_COLOSSEUM:
        return CATEGORY_COLOSSEUM, "pending ColosseumMatchStart"
    if flags.get("in_boss_battle"):
        return CATEGORY_BOSS, "flag in_boss_battle"
    if pending == CATEGORY_BOSS:
        return CATEGORY_BOSS, "pending QuestEncounterFinalBoss"
    word = str(enemy_type or "").lower()
    if "colosseum" in word or "arena" in word:
        return CATEGORY_COLOSSEUM, "enemy_type {!r}".format(enemy_type)
    if "boss" in word:
        return CATEGORY_BOSS, "enemy_type {!r}".format(enemy_type)
    return CATEGORY_NORMAL, "default"


def rewrite_src(original_src, name, path, where):
    """鳴らすパスを組む。

    `assets` 側はゲームが渡してきたパスのファイル名だけを差し替える
    （相対パスの形を保つ）。`state` 側は絶対パス。区切りはスラッシュ。
    """
    if where == "assets" and isinstance(original_src, str) and "/" in original_src.replace("\\", "/"):
        head = original_src.replace("\\", "/").rsplit("/", 1)[0]
        return head + "/" + name
    return os.path.abspath(path).replace("\\", "/")


def short(value):
    if not isinstance(value, str) or not value:
        return repr(value)
    parts = value.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else value


# ----------------------------------------------------------------- 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG)
    warn_once = ctx.warner("battle bgm")

    state = {
        "last": None,            # 前回選んだ曲名
        "pending": None,         # 印: "boss" / "colosseum"
        "pending_at": 0.0,
        "enemy_type": None,      # BattleStartManager.__init__ で控える
    }

    # 置き場所は apply() の中で確定させておく（`ctx` の値はここでしか当てにならない）。
    # `state_path()` は親フォルダを作るので、曲を置く場所も同時にできる。
    playlist_path = ctx.state_path(*(STATE_SUBDIR + (PLAYLIST_NAME,)))
    state_dir = os.path.dirname(playlist_path)
    root = game_root()
    asset_dir = os.path.join(root, *ASSET_SUBDIR) if root else None
    if asset_dir is None:
        ctx.log("battle bgm: Assets/sounds/musics/battle not found; "
                "only state/musics/battle will be used", level="WARN")

    def load_pool(reason):
        """走査して playlist を同期する。戻りは (playlist, found)。"""
        found = scan_tracks(asset_dir, state_dir)
        playlist = ctx.read_json(playlist_path, None)
        # 見つけた曲は 0 で載せるだけ。鳴らすかどうかは設定画面で決める。
        playlist, added = sync_playlist(playlist, found.keys(), NEW_TRACK_WEIGHT)
        if added or not os.path.isfile(playlist_path):
            if ctx.write_json(playlist_path, playlist):
                write("playlist updated ({}): +{} {}".format(
                    reason, len(added), ", ".join(added) if added else "(new file)"))
        for name in ((playlist.get("tracks") or {}).keys()):
            if name not in found:
                warn_once(name, "playlist.json にある曲が見つからない: {}".format(name))
        return playlist, found

    def flags_of(app):
        return dict((k, bool(getattr(app, k, False)))
                    for k in ("in_battle", "in_boss_battle", "in_colosseum_battle"))

    def current_pending():
        if state["pending"] and time.monotonic() - state["pending_at"] <= PENDING_TTL:
            return state["pending"]
        return None

    def choose(app, music_src):
        """戻りは (鳴らすパス, 曲名, ログ用の説明)。候補が無ければ (None, None, 説明)。"""
        flags = flags_of(app)
        pending = current_pending()
        category, why = classify(flags, pending, state["enemy_type"])
        playlist, found = load_pool("battle")
        name = pick(playlist, found, category, state["last"], AVOID_REPEAT)
        detail = "{} by {} | flags={} pending={} enemy_type={!r} | pool={}".format(
            category, why,
            " ".join("{}={}".format(k, 1 if v else 0) for k, v in flags.items()),
            pending, state["enemy_type"],
            len(candidates(playlist, found, category)))
        if name is None:
            return None, None, detail
        path, where = found[name]
        return rewrite_src(music_src, name, path, where), name, detail

    @ctx.wrap("scripts.sounds:SoundManager.play_music_from_src")
    def play_music_from_src(orig, self, app, music_src, *args, **kwargs):
        chosen = name = None
        detail = ""
        try:
            if is_battle_track(music_src):
                chosen, name, detail = choose(app, music_src)
        except Exception:
            ctx.log_exc("battle bgm: choosing failed; playing the game's track")
            chosen = None
        if chosen is not None:
            try:
                result = orig(self, app, chosen, *args, **kwargs)
            except Exception:
                ctx.log_exc("battle bgm: {} could not be played; "
                            "falling back to the game's track".format(chosen))
                write("FAILED {} -> {}".format(short(chosen), short(music_src)))
            else:
                state["last"] = name
                state["pending"] = None
                state["enemy_type"] = None
                write("{} <- {} ({})".format(short(chosen), detail,
                                             "same as the game's" if chosen == music_src
                                             else "replaced " + short(music_src)))
                return result
        elif is_battle_track(music_src):
            state["pending"] = None
            state["enemy_type"] = None
            write("no candidate: {} -> playing {}".format(detail, short(music_src)))
        return orig(self, app, music_src, *args, **kwargs)

    # ------------------------------------------------------- 種類の合図
    def mark(target, category):
        @ctx.wrap(target, required=False, safe=True)
        def _mark(orig, self, *args, **kwargs):
            state["pending"] = category
            state["pending_at"] = time.monotonic()
            return orig(self, *args, **kwargs)
        return _mark

    mark("__main__:ColosseumMatchStart.execute", CATEGORY_COLOSSEUM)
    mark("__main__:QuestEncounterFinalBoss.execute", CATEGORY_BOSS)

    @ctx.wrap("__main__:CancelEntryColosseum.execute", required=False, safe=True)
    def cancel_colosseum(orig, self, *args, **kwargs):
        if state["pending"] == CATEGORY_COLOSSEUM:
            state["pending"] = None
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:BattleStartManager.__init__", required=False, safe=True)
    def battle_start_init(orig, self, app, enemy_type=None, enemy_content=None,
                          *args, **kwargs):
        state["enemy_type"] = enemy_type
        return orig(self, app, enemy_type, enemy_content, *args, **kwargs)

    # ------------------------------------------------------- 起動時の同期
    # playlist.json を戦闘より前に作っておく（書き換える場所を先に見せる）。
    # 副作用なのでプロセスにつき1回。
    def boot_sync():
        try:
            playlist, found = load_pool("injection")
            where = {}
            for _name, (_path, w) in found.items():
                where[w] = where.get(w, 0) + 1
            write("pool: {} track(s) (assets {}, state {}) playlist={}".format(
                len(found), where.get("assets", 0), where.get("state", 0), playlist_path))
        except Exception:
            ctx.log_exc("battle bgm: boot sync failed")

    ctx.on_ready(boot_sync, key="322_battle_bgm:boot_sync")

    ctx.log("battle bgm: state={} assets={} default_weight={} avoid_repeat={}".format(
        state_dir, asset_dir, DEFAULT_WEIGHT, AVOID_REPEAT))
