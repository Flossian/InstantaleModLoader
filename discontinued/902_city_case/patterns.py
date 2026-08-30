# -*- coding: utf-8 -*-
"""事件の材料を JSON から読んで、1件ぶんを抽選する。

ここも事件の論理を知らない。
持っているのは語彙と文言だけで、誰が犯人か・どの事実が誰を消すかには一切関わらない（`case` と
`city_case` の役目）。

##### なぜ外に出すのか

材料がコードに焼き付いていると、何度遊んでも同じ形の事件になる。
盗みだけ、髪と体格だけ、同じ8人。
増やすにはコードを触ることになり、遊ぶ側が足せない。

`patterns/*.json` に出して、**1件ごとに抽選して組み合わせる**形にした。
ファイルを足せば事件の幅が増える。
コードは触らなくてよい。

##### 3つの束

| ファイル | 何の候補か |
|---|---|
| `incidents.json` | 何が起きたか（盗み・付け火・毒盛り…）。文言と、AI に渡す説明 |
| `traits.json` | 目撃される特徴の軸（髪・体格・服の色…）。1件ごとに何本か引いて使う |
| `whereabouts.json` | その晩どこに居たか（裏の取れる場所／取れない場所） |
| `cast.json` | 下地の人物（AI を切ったときに使う顔ぶれ） |

##### 壊れていても止まらない

JSON は人が書き換える前提なので、壊れている前提で読む。
読めない項目は落とし、束ごと空になったら同梱の既定値に戻る。
始まらないくらいなら、定型の事件でも始めたほうがよい（`writer` と同じ方針）。

検算はここではなく組む側が持つ。
この束が何を返しても、必要性・解けること・重複の無さは `build_cast` が確かめる。
だから語彙を足しても謎は壊れない。
"""

import io
import json
import os

#: 材料の置き場所（この MOD のフォルダの下）。
FOLDER = "patterns"

#: 1件の事件で使う特徴の軸の上限。
#: 実際に引く本数は容疑者の数から決まる（`axes_wanted`）。
#: ここは「それ以上は引かない」という頭打ちで、一覧が長くなりすぎるのを防ぐ。
AXES_PER_CASE = 3


def axes_wanted(suspects, cap=AXES_PER_CASE):
    """容疑者の数から、引くべき軸の本数を決める。

    ##### 余った軸は「全員同じ値」になって一覧を濁す

    容疑者のうち**特徴で見分けるのは犯人以外の `人数 - 1` 人**。

    手がかりは「どれも抜けない」ことを求めるので、1本が1人だけを消す形に落ち着く。
    つまり**使える特徴の手がかりは `人数 - 1` 本まで**で、
    それより多く引いた軸は使い道が無い。
    使い道の無い軸は「全員同じ値」になり、一覧の全行に同じ語が並ぶ。

        68 エレーナ  足を引きずる・低い声・煙のにおい・女   ← 犯人
        69 リナ      足を引きずる・低い声・煙のにおい・女   ← 双子
        70 ゴードン  足を引きずる・甲高い声・煙のにおい・男
        71 オーリン  足を引きずる・低い声・潮のにおい・男

    4人に3本引いた実機の例。
    `足を引きずる` が4人とも同じで、**読む側にとっては意味の無い1語**。
    「犯人の特徴が被りすぎ」の正体。

    だから**引くのは `人数 - 1` 本**。
    4人なら3本になり、並んだ語が全部何かを決めるようになる。

    （かつては双子。犯人と特徴が完全に一致する者。
    を1人置いていたので `人数 - 2` だった。双子は面白さに寄与しないので外した）
    """
    return max(1, min(cap, int(suspects) - 1))


def _read(path, write=None):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:
        if write:
            write("patterns: cannot read {}: {}: {}".format(
                os.path.basename(path), type(exc).__name__, exc))
        return None


def _text(value, limit=200):
    if not isinstance(value, str):
        return None
    got = " ".join(value.split())
    return got[:limit] if got else None


def _texts(value, limit=200):
    if not isinstance(value, (list, tuple)):
        return []
    return [t for t in (_text(v, limit) for v in value) if t]


def read_incidents(data, write=None):
    """何が起きたかの候補。

    `noun` は文中に差し込む短い語（「盗み」）。
    これが本文にも AI への説明にも効くので、無い項目は使わない。
    """
    out = []
    for row in (data or {}).get("incidents", []) or []:
        if not isinstance(row, dict):
            continue
        noun = _text(row.get("noun"), 12)
        if not noun:
            continue
        out.append({
            "id": _text(row.get("id"), 40) or noun,
            "noun": noun,
            "premise": _text(row.get("premise"), 200)
            or "「この町で{}が続いている。誰がやっているのか、調べてほしい」".format(noun),
            "brief": _text(row.get("brief"), 200) or "町で続いている{}".format(noun),
            "targets": _texts(row.get("targets"), 40),
        })
    if not out and write:
        write("patterns: no usable incident in incidents.json")
    return out


def read_axes(data, write=None):
    """目撃される特徴の軸。

    1つの軸に2つ以上の値が要る（1つでは誰も絞れない）。
    値には「一覧に出る語」「目撃情報の文」「言い換え」「立ち絵の語」を持たせる。
    """
    out = []
    for row in (data or {}).get("axes", []) or []:
        if not isinstance(row, dict):
            continue
        axis = _text(row.get("id"), 40)
        if not axis:
            continue
        values = []
        for item in row.get("values", []) or []:
            if not isinstance(item, dict):
                continue
            value = _text(item.get("id"), 40)
            word = _text(item.get("word"), 40)
            fact = _text(item.get("fact"), 80)
            if not (value and word and fact):
                continue
            values.append({
                "id": value, "word": word, "fact": fact,
                # 一覧に出す短い形。
                # 突き合わせるのはここなので、
                # 連体形のまま並べず「黒髪・大柄・男」と読めるようにする。
                "short": _text(item.get("short"), 24) or word.rstrip("のな"),
                # 言い換えが無ければ、一覧の語そのものを唯一の手掛かりにする。
                "hints": _texts(item.get("hints"), 40) or [word.rstrip("のな")],
                "look": _text(item.get("look"), 80) or "",
            })
        if len(values) < 2:
            if write:
                write("patterns: axis {!r} needs at least 2 values; skipped"
                      .format(axis))
            continue
        out.append({"id": axis, "label": _text(row.get("label"), 40) or axis,
                    "values": values})
    if not out and write:
        write("patterns: no usable axis in traits.json")
    return out


def read_whereabouts(data, write=None):
    """その晩どこに居たか。裏の取れる場所と、取れない場所。

    取れない場所は犯人だけが言う。
    両方が要るので、片方でも空なら使わない。

    ファイルの形（`{"public": ...}`）でも束の形（`{"whereabouts": {...}}`）でも読む。
    既定値もこの関数を通すため（`normalise`）。
    """
    data = data or {}
    if isinstance(data.get("whereabouts"), dict):
        data = data["whereabouts"]
    public = _texts(data.get("public"), 80)
    alone = _texts(data.get("alone"), 80)
    if not public or not alone:
        if write and (public or alone):
            write("patterns: whereabouts.json needs both 'public' and 'alone'")
        return None
    return {"public": public, "alone": alone,
            "alibi_fact": _text(data.get("alibi_fact"), 120)
            or "{names}は、あの晩ずっと人前に居たことが裏付けられた"}


def read_cast(data, write=None):
    """下地の人物（AI を切ったときの顔ぶれ）。

    名前に記号を入れない（`110_` の対象。立ち絵だけが無言で作られなくなる）。
    """
    bad = set('"<>:|?*/\\')
    out = []
    for row in (data or {}).get("cast", []) or []:
        if not isinstance(row, dict):
            continue
        name = _text(row.get("name"), 24)
        sex = _text(row.get("sex"), 10)
        if not name or sex not in ("man", "woman") or set(name) & bad:
            continue
        out.append({
            "name": name, "sex": sex,
            "category": _text(row.get("category"), 40) or "young man",
            "job": _text(row.get("job"), 40) or "adventure",
            "profile": _text(row.get("profile"), 200) or name,
            "personality": _text(row.get("personality"), 200) or "",
            "look": _text(row.get("look"), 200) or "",
        })
    if not out and write:
        write("patterns: no usable person in cast.json")
    return out


#: 読み方の一覧。
#: `(ファイル名, 読む関数, 束の名前)`。
SHEETS = (
    ("incidents.json", read_incidents, "incidents"),
    ("traits.json", read_axes, "axes"),
    ("whereabouts.json", read_whereabouts, "whereabouts"),
    ("cast.json", read_cast, "cast"),
)


def normalise(defaults, write=None):
    """既定値も**ファイルとまったく同じ読み方**を通す。

    素通しで使うと、
    読み方の側にしか無い項目（`short` を後から足した）が既定値に無いまま流れる。
    実機で `KeyError: 'short'` を踏んだ。
    しかもそのとき初めて既定値に落ちていたので、**普段は通らない道だけが壊れている**という見つけにくい形だった。

    > 受け皿は、本番と同じ口から入れる。
    > 別の入口を作ると、片方にだけ手を入れたときに形がずれる。
    """
    book = {}
    for _filename, reader, key in SHEETS:
        got = reader(defaults or {}, write=write)
        if got:
            book[key] = got
    return book


def load(mod_dir, defaults=None, write=None):
    """材料を読む。読めなかった束だけ既定値で埋める。

    1つのファイルが壊れても他は生きる。
    全部壊れていても既定値で動く。

    `mod_dir` は **`apply()` の中で控えたもの**を渡すこと。
    `ctx.mod_dir` は `apply()` の外では `None` になる（ローダの註）。
    押されてから読もうとして `None` を渡し、
    ファイルが1つも見つからないまま既定値に落ちた。
    実機で踏んだ。
    """
    book = normalise(defaults, write=write)
    folder = os.path.join(mod_dir or "", FOLDER)
    for filename, reader, key in SHEETS:
        data = _read(os.path.join(folder, filename), write=write)
        if data is None:
            continue
        got = reader(data, write=write)
        if got:
            book[key] = got
        elif write:
            write("patterns: {} had nothing usable; keeping the built-in"
                  .format(filename))
    if write:
        write("patterns: {} incident(s), {} axis/axes, {} person/people".format(
            len(book.get("incidents") or []), len(book.get("axes") or []),
            len(book.get("cast") or [])))
    return book


def draw_incident(book, rng):
    rows = book.get("incidents") or []
    return dict(rng.choice(rows)) if rows else None


def draw_axes(book, rng, count=AXES_PER_CASE):
    """使う軸を毎回引き直す。

    ここが「事件のパターン」の芯。
    同じ髪と体格ばかりでなく、回によって服の色や持ち物で絞ることになるので、
    同じ形の推理が続かない。

    引ける本数が足りなければ在るだけ返す（2本あれば事件は組める）。
    """
    rows = book.get("axes") or []
    if not rows:
        return []
    return [dict(axis) for axis in rng.sample(rows, min(count, len(rows)))]


def tables_for(axes):
    """引いた軸から、組む側が使う表を作る。

    `(words, facts, hints, looks, shorts)`。
    鍵はどれも `(軸, 値)`。
    `sex` は含めない。
    あれは人物に付いてくるもので、引くものではない。
    """
    words, facts, hints, looks, shorts = {}, {}, {}, {}, {}
    for axis in axes:
        for value in axis["values"]:
            key = (axis["id"], value["id"])
            words[key] = value["word"]
            shorts[key] = value["short"]
            facts[key] = value["fact"]
            hints[key] = tuple(value["hints"])
            if value["look"]:
                looks[key] = value["look"]
    return words, facts, hints, looks, shorts
