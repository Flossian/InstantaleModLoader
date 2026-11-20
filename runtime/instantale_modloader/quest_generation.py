# -*- coding: utf-8 -*-
"""依頼の生成（`random_quest_generator`）に渡す引数を MOD どうしで分け合う窓口。TECH.md §3.3.30。

依頼の中身を寄せたい MOD は、生成に渡る `area_description` に指示を足し、`quest_difficulty` を差し替える
（GAME.md §2.9。出力の型にも呼び出し側にも影響しない）。
7本の MOD（301/307/318/325/328/336/338）がこの関数を自分で包み、引数の並び（`ARGS`）と
印の有効期限（300秒）をそれぞれに写していた。ゲームの更新で引数が変わると7か所を直すことになるので、
包むのはローダの1枚にし、MOD は「次の生成に足すもの」を置くだけにする。

    1回だけ（生成を呼ぶ直前に置き、呼び終えたら下げる）
        quest_generation.install(ctx, write)              # apply() の中で（包みを立てる）
        quest_generation.offer(owner, ctx, brief="…", difficulty=12, after=fn, tag="road")
        quest_generation.cancel(owner)
    毎回（その生成ごとに引数を見て決める）
        quest_generation.on(owner, ctx, rewrite)          # apply() の中で
        rewrite(request)   # request.get(name) / request.set(name, value)

1回の生成で、窓口は次の順に当てる。

| 順 | 当てるもの | 例 |
|---|---|---|
| 1 | `on` の `rewrite`（登録の新しい順） | `328_` が街の3欄を伏せる |
| 2 | `offer` の `rewrite`（置いた順の逆） | `318_` が土地の難易度を上乗せする |
| 3 | `offer` の `brief` を `area_description` の後ろへ足し、`difficulty` で `quest_difficulty` を差し替える（置いた順） | `325_` の道の性質と選んだ難易度 |
| 4 | `orig` を呼ぶ | |
| 5 | `offer` の `after(result)`（置いた順） | `301_` が作られた依頼の題を記録する |

この順は、前に各 MOD が自分で包んでいたときの重なり（後から当てた MOD ほど外側で、先に引数を見る）と同じ結果になる。
伏せる・上乗せするといった、その回の引数を見て決めるものが先で、明示の指示と難易度が後から勝つ。

- `offer` は1つの持ち主に1つ。置き直すと差し替える。当てたら（期限切れでも）下げる
- 置いてから `ttl` 秒（既定 300）を過ぎた `offer` は当てずに捨て、1行残す（生成が走らずに残った印を次の別の生成へ持ち越さない）
- 引数は受け取った形（位置かキーワード）のまま渡し、来ていない引数は足さない（`frames.replace_arg`）
- `rewrite` と `after` が投げた例外は握って記録し、残りの MOD と `orig` は止めない
- 登録した ctx が用済み（`ctx.superseded()`）なら当てない
- ラッパーは、全 MOD の適用の後にローダが一番外側に1枚立てる（`gates.defer_outermost`・TECH.md §3.3.21）。MOD の適用の間に呼んだ `on` / `install` は包まない

置き場は `sys` の属性（`_instantale_quest_generation`）。注入し直しをまたいで残る。
"""
import sys
import threading
import time

from . import alive, frames, gates, log, log_exc

TARGET = "scripts.llm.llm_manager_world_generate:random_quest_generator"
#: `random_quest_generator` の引数の並び（recon の signatures.json）。
ARGS = ("world_overview", "settlement_name", "settlement_overview",
        "settlement_structure_description", "area_description", "quest_difficulty")
#: `offer` の有効期限（秒）の既定。生成は `generate_random_quest` の中で呼ばれ、ふつうは数十秒で済む。
TTL = 300.0

STORE_ATTR = "_instantale_quest_generation"
_LOCK = threading.Lock()
# 検査の `gates.reset_all()` で登録と包みの印を一緒に忘れる（偽の ctx を作り直す検査）。
gates.track_install_attr(STORE_ATTR)


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"rewriters": {}, "offers": {}, "seq": 0, "installed": None}
        setattr(sys, STORE_ATTR, store)
    for key, empty in (("rewriters", {}), ("offers", {}), ("seq", 0), ("installed", None)):
        store.setdefault(key, empty)
    return store


class Request(object):
    """1回の生成の引数。`get` / `set` で名前を指して読み書きする（受け取った形のまま渡る）。"""

    def __init__(self, args, kwargs):
        self.args = tuple(args)
        self.kwargs = dict(kwargs)

    def get(self, name, default=None):
        return frames.arg(self.args, self.kwargs, name, ARGS, default)

    def set(self, name, value):
        """差し替えたら True。その引数が来ていなければ何もせず False。"""
        self.args, self.kwargs, placed = frames.replace_arg(self.args, self.kwargs, name, ARGS, value)
        return placed


def _say(entry, text):
    write = entry.get("write")
    if callable(write):
        try:
            write(text)
            return
        except Exception:
            pass
    log("quest_generation: " + text)


def _run(owner, entry, label, fn, *args):
    try:
        fn(*args)
        return True
    except Exception as exc:
        log_exc("quest_generation: the {} of {} failed".format(label, owner))
        _say(entry, "WARN quest_generation: the {} raised {}: {}".format(label, type(exc).__name__, exc))
        return False


def _is_difficulty(value):
    return isinstance(value, int) and not isinstance(value, bool)


# -- 登録 ---------------------------------------------------------------------
def on(owner, ctx, rewrite, write=None):
    """その生成ごとに引数を書き換える関数を登録し、包みを立てる。`apply()` の中で呼ぶ。同じ持ち主は差し替える。"""
    if not callable(rewrite):
        raise TypeError("quest_generation.on needs a callable, got {!r}".format(type(rewrite)))
    with _LOCK:
        store = _store()
        store["seq"] += 1
        store["rewriters"].pop(str(owner), None)
        store["rewriters"][str(owner)] = {"ctx": ctx, "rewrite": rewrite, "write": write,
                                          "seq": store["seq"]}
    install(ctx, write)


def off(owner):
    """`on` の登録を下げる。下げたら True。"""
    with _LOCK:
        return _store()["rewriters"].pop(str(owner), None) is not None


def offer(owner, ctx, brief=None, difficulty=None, rewrite=None, after=None, ttl=TTL, tag=None, write=None):
    """次の1回の生成に足すものを置く。同じ持ち主が置き直すと差し替える。

    包みは立てない（`offer` は生成の直前、`apply()` の外で呼ばれる。ctx の値は `apply()` の間だけ）。
    `offer` を使う MOD は `apply()` の中で `install(ctx)` を呼んでおく。

    `brief` … `area_description` の後ろに足す文。`difficulty` … `quest_difficulty` に渡す値（int）。
    `rewrite(request)` … その回の引数を見て書き換える（上乗せなど）。`after(result)` … `orig` の戻り値を受け取る。
    `tag` はログの頭（既定は持ち主）。
    """
    if difficulty is not None and not _is_difficulty(difficulty):
        # 生成を呼ぶ直前に置くので、投げると呼んだ MOD の操作ごと止まる。難易度だけ外して残りは当てる。
        _say({"write": write}, "WARN {}: difficulty {!r} is not an int; not passed".format(tag or owner, difficulty))
        difficulty = None
    for fn in (rewrite, after):
        if fn is not None and not callable(fn):
            raise TypeError("quest_generation.offer needs callables, got {!r}".format(type(fn)))
    with _LOCK:
        store = _store()
        store["seq"] += 1
        store["offers"].pop(str(owner), None)
        store["offers"][str(owner)] = {
            "ctx": ctx, "brief": brief or None, "difficulty": difficulty, "rewrite": rewrite,
            "after": after, "ttl": float(ttl), "at": time.monotonic(), "tag": tag or str(owner),
            "write": write, "seq": store["seq"],
        }
    return True


def cancel(owner):
    """置いた `offer` を下げる（生成が終わった・失敗した）。下げたら True。"""
    with _LOCK:
        return _store()["offers"].pop(str(owner), None) is not None


def pending(owner):
    """その持ち主の `offer` がまだ当てられずに残っていれば True。"""
    with _LOCK:
        return str(owner) in _store()["offers"]


def owners():
    """`on` で登録している生きた持ち主（登録の古い順）。ログと検査用。"""
    with _LOCK:
        rows = sorted(_store()["rewriters"].items(), key=lambda row: row[1]["seq"])
    return [owner for owner, entry in rows if alive(entry["ctx"])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"rewriters": {}, "offers": {}, "seq": 0, "installed": None})


# -- 包み ---------------------------------------------------------------------
def install(ctx, write=None):
    """`random_quest_generator` を1枚だけ包む。1つの世代につき1回。包んだら True。"""
    generation = getattr(ctx, "generation", None)
    # MOD を適用している間は包まない。`boot()` が全 MOD の後にローダの ctx で外側へ立てる（`gates.defer_outermost`）。
    if gates.deferring():
        return False
    with _LOCK:
        store = _store()
        done = store.get("installed")
        if isinstance(done, dict) and done.get("generation") == generation:
            return False
        store["installed"] = {"generation": generation}

    def random_quest_generator(orig, *args, **kwargs):
        return generate(orig, args, kwargs)

    ctx.wrap(TARGET, required=False)(random_quest_generator)
    if write:
        write("quest_generation: the window was declared on {}".format(TARGET))
    return True


def generate(orig, args, kwargs):
    """1回の生成。モジュールの先頭の表の順に当てて `orig` を呼ぶ。"""
    with _LOCK:
        store = _store()
        rewriters = sorted(store["rewriters"].items(), key=lambda row: row[1]["seq"])
        taken = sorted(store["offers"].items(), key=lambda row: row[1]["seq"])
        store["offers"] = {}
    rewriters = [(owner, entry) for owner, entry in rewriters if alive(entry["ctx"])]
    now = time.monotonic()
    offers = []
    for owner, entry in taken:
        if not alive(entry["ctx"]):
            continue
        if now - entry["at"] > entry["ttl"]:
            _say(entry, "{}: stale request ({:.0f}s old), left untouched".format(entry["tag"], now - entry["at"]))
            continue
        offers.append((owner, entry))

    request = Request(args, kwargs)
    for owner, entry in reversed(rewriters):
        _run(owner, entry, "rewrite", entry["rewrite"], request)
    for owner, entry in reversed(offers):
        if entry["rewrite"] is not None:
            _run(owner, entry, "rewrite", entry["rewrite"], request)
    for owner, entry in offers:
        brief = entry["brief"]
        if brief:
            before = request.get("area_description") or ""
            if request.set("area_description", before + brief):
                _say(entry, "{}: area_description {} -> {} chars".format(
                    entry["tag"], len(before), len(before) + len(brief)))
            else:
                _say(entry, "WARN {}: area_description was not passed; left untouched".format(entry["tag"]))
        difficulty = entry["difficulty"]
        if difficulty is not None:
            old = request.get("quest_difficulty")
            if request.set("quest_difficulty", difficulty):
                _say(entry, "{}: quest_difficulty {!r} -> {}".format(entry["tag"], old, difficulty))
            else:
                _say(entry, "WARN {}: quest_difficulty was not passed; left untouched".format(entry["tag"]))

    result = orig(*request.args, **request.kwargs)
    for owner, entry in offers:
        if entry["after"] is not None:
            _run(owner, entry, "after", entry["after"], result)
    return result
