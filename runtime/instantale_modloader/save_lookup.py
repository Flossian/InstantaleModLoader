# -*- coding: utf-8 -*-
"""ゲームが世界の素データ（`world_dict`）から引く対象がセーブ側にしか無いとき、その間だけ引き先をセーブ側にする窓口。

ゲームは素データの辞書を2つ持つ（GAME.md §2.28）。

```text
app.world_dict       worlds\\<世界>\\world_data.json   世界の骨格（同じ世界の主人公どうしで共有）
app.save_data_dict   saves\\<世界>\\savedata.json      その周回の進み（施設・NPC は world 側の上位集合）
```

遊んでいる最中にいくつかの処理が、いま遊んでいる施設や人物を `world_dict` から引く
（売買・人物の詳細の生成・闘技場の試合。VERIFICATION.md §3.106）。
セーブエディタで足した施設や人物は `save_data_dict` にしか無く、そこで `KeyError` になってスレッドが落ちる。
人物の詳細の生成は、落ちた後のセーブが詳細度 2 のまま話し方を欠き、二度と作り直されない。

直し方（本人の決めた線引き。2026-10-20）:

- 世界側に対象があるときはゲームのまま（品揃えの雛形・人物の詳細・闘技場の相手は世界側にも書かれる）
- 世界側に無いときだけ、その関数の間 `app.world_dict` をセーブ側の辞書に差し替える。書き込みはセーブ側にだけ入る

```text
修正する側（apply() の中）   save_lookup.install(ctx, write)            保存の書き手の守り（1つの世代につき1組）
                             save_lookup.cover(owner, ctx, site)         この場面を直していると名乗る
                             with save_lookup.redirected(app, why): …   その間だけ引き先をセーブ側に
読む側                       save_lookup.covered(site) -> 持ち主 / None   （`340_` の宿屋の守りを外すかの判断）
                             save_lookup.in_world(app, *keys) -> True / False / None
```

差し替えている間は、どのスレッドから `app.world_dict` を読んでもセーブ側が見える。
読むだけなら上位集合なので困らない。困るのは保存で、`world_data.json` にセーブの中身（主人公の進み）が
焼かれる。保存の書き手（`save_world_json:write_obfuscated_json_file`）を包み、差し替えている間に
`world_data*` へ書かれるのがセーブ側の辞書なら、本来の世界の辞書に取り替えて書く。

差し替えは数えて入れ子にする（同時に2つの処理が差し替えても、最後の1つが抜けたときに戻す）。
戻すときに `app.world_dict` がもう差し替えたセーブ側の辞書でなければ（間に読み込みが入って辞書が入れ替わった）、戻さない。

置き場は `sys` の属性（`_instantale_save_lookup`）。注入し直しをまたいで残る。
"""
import contextlib
import sys
import threading

STORE_ATTR = "_instantale_save_lookup"
ATTR = "world_dict"
SAVE_ATTR = "save_data_dict"

#: 保存の書き手。`scripts.save_codec` の名前は起動直後の当て直しで `save_world_json` の別名に上書きされる
#: （`242_` の実測。GAME.md §2.11 と同じ名前を包む）。
WRITE_TARGET = "save_world_json:write_obfuscated_json_file"
#: 世界データのファイル名の頭（`world_data.json.temp` / `world_data.json.bulk.tmp` もこの名前で始まる）。
WORLD_FILE_PREFIX = "world_data"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"covers": {}, "depth": 0, "real": None, "swapped": None,
                 "lock": threading.RLock(), "guarded": 0, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


# ---------------------------------------------------------------- 名乗りと問い合わせ
def cover(owner, ctx, site):
    """`owner` が場面 `site`（"shopping" / "npc_detail" / "colosseum" など）を直していると名乗る。"""
    _store()["covers"][str(site)] = (str(owner), ctx)


def withdraw(site):
    _store()["covers"].pop(str(site), None)


def covered(site):
    """場面 `site` を直している持ち主。誰も直していなければ None。"""
    entry = _store()["covers"].get(str(site))
    if not entry:
        return None
    owner, ctx = entry
    return owner if _alive(ctx) else None


# ---------------------------------------------------------------- 世界側にあるか
def _holder(app):
    holder = getattr(app, "__dict__", None)
    return holder if isinstance(holder, dict) else None


def world_of(app):
    """本来の世界の辞書（差し替えている間は控えたほう）。"""
    store = _store()
    if store["depth"] and store["real"] is not None:
        return store["real"]
    holder = _holder(app)
    value = holder.get(ATTR) if holder is not None and ATTR in holder else getattr(app, ATTR, None)
    return value if isinstance(value, dict) else None


def in_world(app, *keys):
    """世界の辞書をキーの並びでたどれるか。True / False、辞書が読めなければ None（判断しない）。"""
    node = world_of(app)
    if node is None:
        return None
    for key in keys:
        if not isinstance(node, dict):
            return False
        key = str(key)
        if key not in node:
            return False
        node = node[key]
    return True


# ---------------------------------------------------------------- 差し替え
@contextlib.contextmanager
def redirected(app, why="", write=None):
    """その間だけ `app.world_dict` をセーブ側の辞書にする。セーブ側が読めなければ何もしない。"""
    holder = _holder(app)
    save = getattr(app, SAVE_ATTR, None)
    if holder is None or not isinstance(save, dict) or not isinstance(holder.get(ATTR), dict):
        if write:
            write("WARN save lookup: cannot redirect ({}): the dicts are unreadable".format(why))
        yield False
        return
    store = _store()
    with store["lock"]:
        if store["depth"] == 0:
            store["real"] = holder[ATTR]
            store["swapped"] = save
            holder[ATTR] = save
        store["depth"] += 1
    if write:
        write("redirect on ({}): world_dict -> save_data_dict".format(why))
    try:
        yield True
    finally:
        with store["lock"]:
            store["depth"] -= 1
            if store["depth"] <= 0:
                store["depth"] = 0
                real, swapped = store["real"], store["swapped"]
                store["real"] = store["swapped"] = None
                if holder.get(ATTR) is swapped:
                    holder[ATTR] = real
                elif write:
                    write("WARN save lookup: world_dict was replaced while redirected ({}); left as is".format(why))
        if write:
            write("redirect off ({})".format(why))


def redirecting():
    """いま差し替えているか。"""
    return _store()["depth"] > 0


def guard(file_path, data):
    """保存の書き手へ渡すデータ。差し替えの間に世界データへセーブ側の辞書が渡されたら、本来の世界の辞書を返す。"""
    store = _store()
    real, swapped = store["real"], store["swapped"]
    if real is None or swapped is None or data is not swapped:
        return data
    name = str(file_path).replace("\\", "/").rsplit("/", 1)[-1]
    if not name.startswith(WORLD_FILE_PREFIX):
        return data
    store["guarded"] += 1
    return real


def install(ctx, write=None):
    """保存の書き手を包む（守り）。`apply()` の中で呼ぶ。1つの世代につき1組（ctx.wrap が世代を管理する）。"""
    from . import frames

    @ctx.wrap(WRITE_TARGET, required=False, safe=True)
    def write_obfuscated_json_file(orig, *args, **kwargs):
        try:
            path = frames.arg(args, kwargs, "file_path", 0)
            data = frames.arg(args, kwargs, "data", 1)
            fixed = guard(path, data)
            if fixed is not data:
                args, kwargs, _done = frames.replace_arg(args, kwargs, "data", 1, fixed)
                if write:
                    write("save lookup: wrote the real world dict to {} (the save dict was passed while redirected)"
                          .format(str(path)[-40:]))
        except Exception:
            ctx.log_exc("save lookup: the write guard failed")
        return orig(*args, **kwargs)
    return write_obfuscated_json_file


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, None)
    _store()
