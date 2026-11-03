# -*- coding: utf-8 -*-
"""計測: 遊んでいる最中にゲームが世界の素データ（`app.world_dict`）を引く箇所。

##### 何を知りたいか

ゲームは素データの辞書を2つ持つ（GAME.md §2.28）。

```text
app.world_dict       worlds\\<世界>\\world_data.json   世界の骨格
app.save_data_dict   saves\\<世界>\\savedata.json      その周回の進み（world 側の上位集合）
```

売買は施設と主を `world_dict` から引き、そこに無い施設（セーブエディタで足したもの）では
`KeyError` でスレッドが落ちる（VERIFICATION.md §3.105）。
遊んでいる最中に `world_dict` を引く箇所を全部挙げ、要るもの（世界の生成・街の生成・主人公の作り直し）と
要らないもの（セーブ側で足りるもの）に分ける下調べ。

##### 録り方

辞書そのものは差し替えない（中の段を別の物にすると、ゲームが掴んだままの参照とずれる）。
`app` の型を、同じ名前の派生クラスに差し替える（`228_probe_area_move_reject` と同じ形）。
派生クラスは `world_dict` の属性だけを持ち、引かれた瞬間に呼び出し元のフレームを控えて、元の値をそのまま返す。

- 箇所の鍵は、引いたフレームの `(ファイル, 関数, 行)`。初めて見た箇所だけ呼び出しの連鎖と場面（居場所・旗・スレッド）を
  `out\\world_reads.log` に1行書き、あとは数だけ増やす。数と連鎖の一覧は `out\\world_reads.json`（30秒ごと、変わったときだけ）
- 引いたのがローダか MOD のフレームなら、MOD の名前ごと記録する（ゲームの箇所と分けて読む）
- 代入（ロードで辞書が入れ替わる）も同じく記録する
- 保存の書き手（`save_world_json:write_obfuscated_json_file`）に渡ったファイルを、名前と呼び出しの連鎖ごとに数える
- 暗号化の層の読み手（`scripts.save_codec:read_obfuscated_json_file` / `read_json_with_obfuscation_fallback`）も同じく数える。
  ディスクから `world_data.json` を読み直す場面（読み込み以外）があるかを見る

拾えないもの: 属性を引かずに辞書を持ち回す経路（`World` などが参照を掴んでいて、後からそこを読む場合）。
関数の頭で一度だけ `self.app.world_dict` を引いて、中で何度も読む形は、その関数の1件として拾う。
どの段（エリア・施設・NPC）を読んだかは分からない（Nuitka の生きたフレームの `f_locals` は空）。

値・乱数・state\\・セーブは動かさない。型の差し替えは `app` の1つだけで、外すときは元の型へ戻す。

版2: 数える手間を減らし、終わりの直前の数を残す。

- 箇所の鍵を `(種類, フレームのコード, 行)` のタプルにした。版1は `world_dict` を1回引くたびに
  ファイル名・関数名・行を繋いだ文字列を組んでいた。文字列（連鎖と場面）は初めて見た箇所だけ組む
- ファイルの読み書きの鍵を、ゲーム側のフレームの `(コード, 行)` の並びにした。
  版1は1回ごとに最大120段をなめながら1段ずつ名前を整形してから既出かを見ていた。整形は初出だけ
- 元の型の `world_dict` がデスクリプタかの判定（版1は引くたびに `hasattr`）を、差し替えの型を作るときの1回にした
- 5秒ごとの見回りと起動直後の1回を1つの関数にまとめた
- 閉じるときに一覧を書き出す（Kivy の `on_stop` と `atexit`。先に走った方が書き、後の方は書くものが無い）。
  版1は最後の書き出しから閉じるまでの最大30秒分の数が `world_reads.json` に残らなかった
"""

import atexit
import sys
import threading
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "world_reads.log"
SUMMARY_BASENAME = "world_reads.json"

#: 再注入しても1組だけ持つ（差し替えた型・元の型・数え）。
STORE = "_instantale_probe_world_reads"

ATTR = "world_dict"
#: 保存の書き手。`scripts.save_codec` の名前は起動直後の当て直しで `save_world_json` の別名に上書きされ、包みが外れた（実機）。
#: 発火するのは `save_world_json` の名前（GAME.md §2.11・`104_` と同じ）。
WRITE_TARGET = "save_world_json:write_obfuscated_json_file"
WORLD_FILE = "world_data.json"
#: 暗号化の層の読み手（ディスクからの読み込み）。書き手と同じく、ファイル名と連鎖ごとに数える。
READ_TARGETS = (
    ("scripts.save_codec:read_obfuscated_json_file", "read"),
    ("scripts.save_codec:read_json_with_obfuscation_fallback", "read_fallback"),
)

#: 覚える箇所の上限（超えたら数えない。ログも増やさない）。
MAX_SITES = 400
#: 初めて見た箇所に添える呼び出しの連鎖の段数。
CHAIN_DEPTH = 6
#: 型が外れていないか見る間隔と、一覧を書き出す間隔（秒）。
CHECK_SECONDS = 5.0
FLUSH_SECONDS = 30.0

#: 場面として添える app の旗。
FLAGS = ("in_battle", "in_conversation", "in_quest", "is_loading")

#: 段の記録（試し用。既定は切。`m 242_ TRACE_PATHS true` で入れる）。
#: 下の関数が走っている間だけ `world_dict` を「引いた段を記録する写し」に替え、どの段を読み書きしたかを録る。
#: 写しへの書き込みは元の辞書にも通す（データは食い違わない）。写しを掴んだまま残す呼び出し元があれば、
#: その後も元の辞書へ書き込みが通る（記録は窓の間だけ）。試した世界は控えへ戻すこと。
TRACE_PATHS = False
TRACE_TARGETS = ("__main__:Facility.__init__", "__main__:World.generate_character",
                 "__main__:InstantaleApp.generate_npc_detail",
                 "__main__:ColosseumMatchStart.generate_enemy_data",
                 "__main__:ShoppingStartManagerRemake.shopping_start_method_1")
#: 段の数字の id は `<id>` にまとめて数える（例は1つ残す）。
TRACE_DEPTH = 6


def _frame_label(frame):
    """1段の名前。`関数 (ファイルの末尾:行)`。ローダと MOD のフレームは `mod:` を頭に付ける。"""
    code = frame.f_code
    name = code.co_filename
    tail = name.replace("\\", "/").rsplit("/", 2)
    where = "/".join(tail[-2:]) if len(tail) >= 2 else name
    label = "{} ({}:{})".format(frames.owner_of(code) or code.co_name, where, frame.f_lineno)
    return ("mod:" + label) if frames.is_ours(name) else label


def _chain(frame, depth=CHAIN_DEPTH):
    out = []
    while frame is not None and len(out) < depth:
        out.append(_frame_label(frame))
        frame = frame.f_back
    return out


def _game_codes(frame, depth=CHAIN_DEPTH, limit=120):
    """ローダと MOD のフレームを飛ばして、ゲームの側を `depth` 段そろえた `((コード, 行), ...)`。

    包みの層（`patch.py` の `guarded` / `orig`）が MOD の数だけ挟まるので、段数で切ると
    ゲームの呼び出し元まで届かない（実機。保存の連鎖が `save_game` 1段で尽きた）。
    名前は組まない（既出かを見る鍵にする。名前は `_labels` で初出のときだけ組む）。
    """
    out = []
    while frame is not None and len(out) < depth and limit > 0:
        code = frame.f_code
        if not frames.is_ours(code.co_filename):
            out.append((code, frame.f_lineno))
        frame = frame.f_back
        limit -= 1
    return tuple(out)


def _code_label(code, lineno):
    """`_frame_label` と同じ形の名前を、コードと行から組む（ゲーム側の段だけに使う）。"""
    name = code.co_filename
    tail = name.replace("\\", "/").rsplit("/", 2)
    where = "/".join(tail[-2:]) if len(tail) >= 2 else name
    return "{} ({}:{})".format(frames.owner_of(code) or code.co_name, where, lineno)


def _labels(codes):
    return [_code_label(code, lineno) for code, lineno in codes]


def _game_chain(frame, depth=CHAIN_DEPTH, limit=120):
    """ローダと MOD のフレームを飛ばして、ゲームの側を `depth` 段そろえた名前の並び。"""
    return _labels(_game_codes(frame, depth, limit))


def _spy_class(base, store):
    """`world_dict` を引くと `store["hooks"]["read"]` を呼ぶ派生クラス。名前は元のまま。"""
    inner = None
    for klass in base.__mro__:
        if ATTR in vars(klass):
            inner = vars(klass)[ATTR]
            break
    # 元の型の `world_dict` がデスクリプタか。型を作るときに1回だけ見る（引くたびには見ない）
    inner_get = getattr(inner, "__get__", None) if inner is not None else None
    inner_set = getattr(inner, "__set__", None) if inner is not None else None

    def getter(self):
        hooks = store.get("hooks")
        if hooks is not None:
            try:
                hooks["read"](sys._getframe(1))
            except Exception:
                pass
        if inner_get is not None:
            return inner_get(self, type(self))
        try:
            return self.__dict__[ATTR]
        except KeyError:
            raise AttributeError(ATTR)

    def setter(self, value):
        hooks = store.get("hooks")
        if hooks is not None:
            try:
                hooks["set"](sys._getframe(1))
            except Exception:
                pass
        if inner_set is not None:
            inner_set(self, value)
        else:
            self.__dict__[ATTR] = value

    cls = type(base.__name__, (base,), {
        ATTR: property(getter, setter),
        "__module__": base.__module__,
        "__qualname__": getattr(base, "__qualname__", base.__name__),
        "_iml_world_reads_spy": True,
    })
    return cls


def flush_at_exit(*_args):
    """閉じるときに一覧を書き出す（Kivy の `on_stop` と `atexit` から）。

    `sys` の控えにある、いちばん新しい世代の `flush` を呼ぶ。数が変わっていなければ書かないので、
    両方が走っても2回目は何もしない。
    """
    store = getattr(sys, STORE, None)
    flush = store.get("flush") if isinstance(store, dict) else None
    if flush is None:
        return
    try:
        flush()
    except Exception:
        pass


def _norm(key):
    text = str(key)
    return "<id>" if text.isdigit() or text.startswith("item_") else text


class _Rec(dict):
    """元の辞書の写し。読んだ段・書いた段を `sink` に知らせ、書き込みは元の辞書にも通す。"""

    def __init__(self, src, path, sink):
        dict.__init__(self, src)
        self._src = src
        self._path = path
        self._sink = sink

    def _note(self, op, key=None):
        path = self._path + ((key,) if key is not None else ())
        try:
            self._sink(op, path)
        except Exception:
            pass

    def _wrap(self, key, value):
        if isinstance(value, dict) and not isinstance(value, _Rec) and len(self._path) < TRACE_DEPTH:
            child = _Rec(value, self._path + (key,), self._sink)
            dict.__setitem__(self, key, child)
            return child
        return value

    def __getitem__(self, key):
        self._note("get", key)
        return self._wrap(key, dict.__getitem__(self, key))

    def get(self, key, default=None):
        self._note("get" if dict.__contains__(self, key) else "get-miss", key)
        if dict.__contains__(self, key):
            return self._wrap(key, dict.__getitem__(self, key))
        return default

    def __contains__(self, key):
        self._note("in", key)
        return dict.__contains__(self, key)

    def keys(self):
        self._note("keys")
        return dict.keys(self)

    def items(self):
        self._note("items")
        return dict.items(self)

    def values(self):
        self._note("values")
        return dict.values(self)

    def __iter__(self):
        self._note("iter")
        return dict.__iter__(self)

    def __len__(self):
        self._note("len")
        return dict.__len__(self)

    def copy(self):
        self._note("copy")
        return dict(self._src)

    def __deepcopy__(self, memo):
        # 写しは素の dict で返す（記録の写しを呼び出し元に残さない）。
        self._note("deepcopy")
        import copy as _copy
        return _copy.deepcopy(self._src, memo)

    def __reduce_ex__(self, protocol):
        self._note("reduce")
        return (dict, (dict(self._src),))

    def __setitem__(self, key, value):
        self._note("set", key)
        self._src[key] = value
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        self._note("del", key)
        self._src.pop(key, None)
        dict.__delitem__(self, key)

    def setdefault(self, key, default=None):
        self._note("setdefault", key)
        value = self._src.setdefault(key, default)
        dict.setdefault(self, key, value)
        return self._wrap(key, dict.__getitem__(self, key))

    def pop(self, key, *default):
        self._note("pop", key)
        self._src.pop(key, None)
        return dict.pop(self, key, *default)

    def update(self, *args, **kwargs):
        self._note("update")
        self._src.update(*args, **kwargs)
        dict.update(self, *args, **kwargs)

    def clear(self):
        self._note("clear")
        self._src.clear()
        dict.clear(self)


def apply(ctx):
    store = getattr(sys, STORE, None)
    if not isinstance(store, dict):
        store = {"base": None, "spy": None, "sites": {}, "writes": {}, "hooks": None,
                 "since": time.strftime("%Y-%m-%dT%H:%M:%S"), "dirty": False}
        setattr(sys, STORE, store)
    lock = threading.Lock()
    write = ctx.logger(LOG_BASENAME, cap=3000)

    def context():
        app = ui.find_app()
        if app is None:
            return {}
        out = {}
        try:
            loc = getattr(getattr(app, "player", None), "location", None)
            out["at"] = "{} {}".format(getattr(loc, "id", loc), ui.facility_type_of(loc) if loc is not None else "")
        except Exception:
            pass
        for flag in FLAGS:
            value = getattr(app, flag, None)
            if value:
                out[flag] = value if isinstance(value, (bool, int, str)) else type(value).__name__
        return out

    def note(kind, frame):
        # 鍵は生のコードと行（安い）。名前・連鎖・場面の文字列は初めて見た箇所だけ組む
        key = (kind, frame.f_code, frame.f_lineno)
        sites = store["sites"]
        entry = sites.get(key)
        if entry is not None:
            entry["count"] += 1
            store["dirty"] = True
            return
        if len(sites) >= MAX_SITES:
            return
        with lock:
            if key in sites:
                sites[key]["count"] += 1
                store["dirty"] = True
                return
            chain = [_frame_label(frame)] + _game_chain(frame.f_back)
            entry = {"kind": kind, "site": chain[0], "count": 1,
                     "first": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "thread": threading.current_thread().name,
                     "context": context(), "chain": chain}
            sites[key] = entry
            store["dirty"] = True
        write("{} #{} {} | thread={} ctx={} | {}".format(
            kind, len(sites), entry["site"], entry["thread"], entry["context"], " <- ".join(chain[1:])))

    store["hooks"] = {"read": lambda frame: note("read", frame),
                      "set": lambda frame: note("set", frame)}
    #: この世代が持ち主か。注入し直しで次の世代が置き直していたら、降りるときに触らない。
    owner = object()
    store["owner"] = owner

    def ensure():
        """`app` の型を差し替える。既に差し替えてあれば何もしない。"""
        app = ui.find_app()
        if app is None:
            return
        cls = type(app)
        if getattr(cls, "_iml_world_reads_spy", False):
            return
        if store["spy"] is None or store["base"] is not cls:
            store["base"] = cls
            store["spy"] = _spy_class(cls, store)
        try:
            object.__setattr__(app, "__class__", store["spy"])
        except TypeError as exc:
            write("WARN cannot swap {}: {}".format(cls.__name__, exc))
            store["spy"] = None
            return
        write("swapped {} (world_dict is {})".format(
            cls.__name__, type(app.__dict__.get(ATTR, None)).__name__))

    def restore():
        """降りるとき、`app` の型を元へ戻し、見張りを外す。次の世代が持ち主なら何もしない。"""
        if store.get("owner") is not owner:
            return
        store["hooks"] = None
        store["owner"] = None
        app = ui.find_app()
        cls = type(app) if app is not None else None
        if cls is None or not getattr(cls, "_iml_world_reads_spy", False):
            return
        try:
            object.__setattr__(app, "__class__", cls.__mro__[1])
        except TypeError as exc:
            write("WARN cannot restore {}: {}".format(cls.__name__, exc))
            return
        write("restored {}".format(cls.__name__))

    def flush():
        if not store["dirty"]:
            return
        store["dirty"] = False
        rows = sorted(store["sites"].values(), key=lambda e: -e["count"])
        writes = sorted(store["writes"].values(), key=lambda e: -e["count"])
        ctx.write_json(ctx.out_path(SUMMARY_BASENAME),
                       {"since": store["since"], "sites": rows, "world_file_writes": writes,
                        "paths": store.get("paths", {})})

    @ctx.wrap(WRITE_TARGET, required=False, safe=True)
    def record_world_write(orig, *args, **kwargs):
        """書いたファイルを名前ごと・呼び出しの連鎖ごとに数える。

        `world_data.json` だけに絞ると、書き先の名前が違う回（一時ファイルなど）を取りこぼす。
        連鎖はローダと MOD のフレームを飛ばしたゲームの側。
        """
        try:
            note_file("write", args, kwargs)
        except Exception:
            ctx.log_exc("probe world reads: cannot record a file write")
        return orig(*args, **kwargs)

    def note_file(kind, args, kwargs):
        """暗号化の層でファイルを読んだ・書いたのを、名前と呼び出しの連鎖ごとに数える。"""
        path = frames.arg(args, kwargs, "file_path", 0)
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        # 既出かは生のコードと行の並びで引く。段の名前は初めて見た連鎖だけ組む
        codes = _game_codes(sys._getframe(2))
        key = (kind, name, codes)
        entry = store["writes"].get(key)
        if entry is None and len(store["writes"]) < MAX_SITES:
            chain = _labels(codes)
            store["writes"][key] = {"kind": kind, "file": name, "count": 1, "chain": chain,
                                    "first": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "thread": threading.current_thread().name}
            write("file {} #{} {} | thread={} | {}".format(
                kind, len(store["writes"]), name, threading.current_thread().name, " <- ".join(chain)))
        elif entry is not None:
            entry["count"] += 1
        store["dirty"] = True

    def reader(target, kind):
        @ctx.wrap(target, required=False, safe=True)
        def record_read(orig, *args, **kwargs):
            try:
                note_file(kind, args, kwargs)
            except Exception:
                ctx.log_exc("probe world reads: cannot record a file read")
            return orig(*args, **kwargs)
        return record_read

    for target, kind in READ_TARGETS:
        reader(target, kind)

    # ---- 段の記録（TRACE_PATHS）------------------------------------------
    trace = threading.local()
    paths = store.setdefault("paths", {})

    def sink(op, path):
        label = getattr(trace, "label", None)
        if label is None:
            return
        shape = "/".join(_norm(k) for k in path)
        bucket = paths.setdefault(label, {})
        key = "{} {}".format(op, shape)
        entry = bucket.get(key)
        if entry is None:
            bucket[key] = {"count": 1, "example": "/".join(str(k) for k in path)}
            write("trace {} | {} {} (例 {})".format(label, op, shape, "/".join(str(k) for k in path)))
        else:
            entry["count"] += 1
        store["dirty"] = True

    def tracer(target):
        label = target.rpartition(":")[2]

        @ctx.wrap(target, required=False, safe=True)
        def traced(orig, self, *args, **kwargs):
            if not TRACE_PATHS or getattr(trace, "label", None) is not None:
                return orig(self, *args, **kwargs)
            app = ui.find_app()
            holder = getattr(app, "__dict__", None)
            original = holder.get(ATTR) if isinstance(holder, dict) else None
            if not isinstance(original, dict) or isinstance(original, _Rec):
                return orig(self, *args, **kwargs)
            trace.label = label
            holder[ATTR] = _Rec(original, (), sink)
            try:
                return orig(self, *args, **kwargs)
            finally:
                holder[ATTR] = original
                trace.label = None
        return traced

    for target in TRACE_TARGETS:
        tracer(target)

    state = {"last_flush": 0.0}

    def step(force_flush):
        """型が外れていないかを見て、一覧を書き出す。`force_flush` でなければ `FLUSH_SECONDS` おき。
        用済みの世代なら型を戻して False（Clock から外れる）。"""
        if ctx.superseded():
            try:
                restore()
            except Exception:
                ctx.log_exc("probe world reads: cannot restore the app type")
            return False
        try:
            ensure()
            now = time.time()
            if force_flush:
                flush()         # 起動直後の1回。30秒おきの刻みは動かさない（版1の tick と同じ）
            elif now - state["last_flush"] >= FLUSH_SECONDS:
                state["last_flush"] = now
                flush()
        except Exception:
            ctx.log_exc("probe world reads: check failed")
        return True

    # 閉じるときの書き出しは、いちばん新しい世代の `flush` で行う（`flush_at_exit`）
    store["flush"] = flush
    if not store.get("exit_hooked"):
        store["exit_hooked"] = True
        atexit.register(flush_at_exit)

    def bind_on_stop():
        app = ui.find_app()
        if app is not None:
            app.bind(on_stop=flush_at_exit)

    # キーを世代で変えないので、注入し直しても1プロセスに1回だけ結ぶ（TECH.md §3.6）
    ctx.on_ready(bind_on_stop, key="242_probe_world_reads:on_stop")

    try:
        from kivy.clock import Clock
        Clock.schedule_interval(lambda dt: step(False), CHECK_SECONDS)
        Clock.schedule_once(lambda dt: step(True), 0)
    except Exception:
        ctx.log_exc("probe world reads: no Clock; recording off")

    ctx.log("probe world reads: recording who reads app.{} while playing; log goes to out/{}".format(
        ATTR, LOG_BASENAME))
