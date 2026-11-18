# -*- coding: utf-8 -*-
r"""会話の立ち絵の表情差分を、描く MOD と見せる MOD のあいだで仲介する窓口（TECH.md §3.3.29）。

差分を描くのは画像を描く MOD（`343_`）、表情を選んで見せるのは会話の MOD（`347_`・`404_`）。
MOD どうしは import しない（TECH.md §3.2.3）ので、表情の一覧・絵の置き場・「描いてほしい」の宣言をここに置く。

    見せる側（apply() の中）   expressions.want(owner, ctx)          差分を描いてほしいと宣言する
    描く側（描く前）           expressions.wanted()                  宣言した MOD が居るか
                               folder = expressions.character_dir(world, name)
                               stamp = expressions.stamp(folder)     元の絵の印（描き始める前に取る）
                               expressions.clear(folder)             前の差分を消す
                               expressions.variant_path(folder, e)   差分を書く場所
                               expressions.seal(folder, stamp, names, complete)
                                                                     描き終えたら印を書く（描けない人物は差分の無い印）
                               expressions.complete(folder)          印が描き揃った回のものか（欠けたら描き直す）
    見せる側（見せるとき）     expressions.image(folder, e)          今の立ち絵に合う差分。無ければ None
                               expressions.show(widget, folder, e, base)  部品の絵を差分（無ければ base）に替える
    見せる側（選ばせるとき）   expressions.GUIDE                     LLM に渡す表情の説明
                               expressions.name_of(value)            LLM が返した値を表情の名前に直す

置き場は立ち絵のフォルダ（`worlds\<世界>\characters\<名前>\`）の `expressions\<表情>.png`。
差分は元の絵（`generated_image.png`）から描くので、描き終えたら元の絵の印（SHA-1）を `source.json` に書く。
`image` は今の元の絵の印と合うときだけ差分を返す。
立ち絵がどの経路で描き直されても（再生成のボタン・ほかの MOD）、古い差分を見せない。

宣言は持ち主ごとに1つ（同じ持ち主の宣言は差し替える）。
宣言した ctx が用済み（`ctx.superseded()`）になった宣言は数えない（`talk_affinity` と同じ）。
置き場は `sys` の属性（`_instantale_expressions`）。注入し直しをまたいで残る。

差分は同じファイル名へ描き直されるので、`show` は前に見せたときと更新時刻が違えば
Kivy の画像のキャッシュを捨てて読み直す（`138_` が立ち絵で直しているのと同じ問題）。
"""
import hashlib
import json
import os
import sys

from . import alive
from . import saves
from . import ui

#: 表情の一覧。最初の1つはふだんの顔で、差分を描かない（ゲームの立ち絵をそのまま見せる）。
NAMES = ("neutral", "smile", "angry", "sad", "surprised")
NEUTRAL = NAMES[0]

#: 差分を描く表情。
VARIANTS = NAMES[1:]

#: 立ち絵のフォルダの下の差分の置き場。
FOLDER = "expressions"

#: 差分の元にする絵（立ち絵のフォルダの下。ゲームが描いたままの絵。GAME.md §2.30）。
SOURCE = "generated_image.png"

#: 元の絵の印を書くファイル（差分の置き場の下）。
SEAL = "source.json"

STORE_ATTR = "_instantale_expressions"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"wants": {}, "stamps": {}, "shown": {}}
        setattr(sys, STORE_ATTR, store)
    return store


# ---- 宣言 ----------------------------------------------------------------

def want(owner, ctx):
    """`owner` が表情差分を見せると宣言する。同じ持ち主の宣言は差し替える。"""
    _store()["wants"][str(owner)] = ctx


def withdraw(owner):
    """宣言を下げる。"""
    _store()["wants"].pop(str(owner), None)


def wanters():
    """いま数えている宣言の持ち主。"""
    return sorted(owner for owner, ctx in _store()["wants"].items() if alive(ctx))


def wanted():
    """表情差分を見せる MOD が1つでも居るか。描く側はこれが偽なら描かない。"""
    return bool(wanters())


def is_name(expression):
    """表情の一覧に在る名前か。"""
    return isinstance(expression, str) and expression in NAMES


#: LLM に渡す表情の説明（見せる MOD が、何を出力させるかの1文の後に続ける）。
#: 型の説明はローカル LLM が読まないので、プロンプトの地の文に置く（TECH.md §3.3.29）。
GUIDE = ("neutral=普段の顔、smile=笑う・機嫌がよい、angry=怒る・不機嫌、sad=悲しむ・困る、surprised=驚く。"
         "台詞に込めた感情に合うものを1つ選ぶ。はっきりしなければ neutral")


def name_of(value):
    """LLM が返した表情を名前に直す（前後の空白と大文字小文字は見ない）。読めない・一覧に無いならふだんの顔。

    型では縛らず `str` で受けて、ここで一覧に照らす（一覧に無い値で返事ごと検証に落とさない）。
    """
    if isinstance(value, str):
        value = value.strip().lower()
    return value if is_name(value) else NEUTRAL


# ---- 置き場 --------------------------------------------------------------

def character_dir(world, name, base=""):
    r"""立ち絵のフォルダ（`worlds\<世界>\characters\<名前>`）。世界か名前が空なら None。

    フォルダ名は人物の名前そのもの（`110_` が名前の段階で直すので、パスに使えない文字は来ない）。
    `base` を渡せるのは検査が仮の置き場を指すため。
    """
    if not isinstance(world, str) or not world or not isinstance(name, str) or not name:
        return None
    return os.path.join(saves.worlds_dir(base), world, "characters", name)


def folder_of(character_folder):
    """差分の置き場。"""
    return os.path.join(character_folder, FOLDER)


def variant_path(character_folder, expression):
    """差分を書く場所（在るかは見ない）。"""
    return os.path.join(folder_of(character_folder), "{}.png".format(expression))


def stamp(character_folder):
    """元の絵の印（中身の SHA-1）。元の絵が無い・読めないなら None。

    同じ大きさと更新時刻のあいだは読み直さない（会話の返事のたびに読むため）。
    """
    if not character_folder:
        return None
    path = os.path.join(character_folder, SOURCE)
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    stamps = _store()["stamps"]
    got = stamps.get(key)
    if got is None:
        digest = hashlib.sha1()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
        except OSError:
            return None
        got = digest.hexdigest()
        if len(stamps) > 256:
            stamps.clear()
        stamps[key] = got
    return got


def _record(character_folder):
    """`source.json` の中身。無い・読めないなら None。

    同じ大きさと更新時刻のあいだは読み直さない（`image` は会話の返事のたびにメインスレッドで呼ばれる。
    `seal` は隣に書いてから差し替えるので、書き直せばファイルの番号（`st_ino`）が変わる。
    更新時刻の刻みが粗くても、同じ大きさの書き直しを取り違えない）。読めなかった回は覚えない。
    """
    path = os.path.join(folder_of(character_folder), SEAL)
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(path)), st.st_ino, st.st_size, st.st_mtime_ns)
    records = _store().setdefault("records", {})
    if key in records:
        return dict(records[key])
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict):
        return None
    if len(records) > 256:
        records.clear()
    records[key] = got
    return dict(got)


def sealed(character_folder):
    """`source.json` に書かれた元の絵の印。無ければ None。"""
    value = (_record(character_folder) or {}).get("sha1")
    return value if isinstance(value, str) and value else None


def complete(character_folder):
    """印が、描くはずの表情を描き揃えた回のものか（印が無ければ False）。

    `complete` の無い印（前の版が書いたもの）は揃っているとみなす。
    """
    got = _record(character_folder)
    return got is not None and got.get("complete", True) is not False


def seal(character_folder, source_stamp, names=None, complete=True):
    """描き終えた差分の元の絵の印を書く。書けたら True。

    `names` はこの印で見せる表情（省けば、いま置き場に在る差分）。`image` は印に載った表情だけ返すので、
    前の立ち絵の仕事が後から書いた差分が残っていても見せない。
    `complete` は、描くはずの表情を描き揃えたか（偽なら描く側が次に描き直す。`complete`）。
    差分が1枚も無い印も書ける（顔が見つからない人物。描く側が同じ元の絵に描き直しを繰り返さないため）。
    書き方はローダの `write_json`（一時ファイルの名前を毎回変え、見せる側が `source.json` を開いている間の
    差し替えの失敗を短くやり直す）。
    """
    if not character_folder or not source_stamp:
        return False
    from . import write_json
    if names is None:
        names = [e for e in VARIANTS if os.path.isfile(variant_path(character_folder, e))]
    body = {"source": SOURCE, "sha1": source_stamp, "expressions": [e for e in VARIANTS if e in names],
            "complete": bool(complete)}
    return write_json(os.path.join(folder_of(character_folder), SEAL), body)


def clear(character_folder):
    """前の差分と印を消す（表情の一覧に在るファイルと `source.json` だけ。ほかの物は触らない）。"""
    if not character_folder:
        return
    for name in [SEAL] + ["{}.png".format(e) for e in VARIANTS]:
        try:
            os.remove(os.path.join(folder_of(character_folder), name))
        except OSError:
            pass


def image(character_folder, expression):
    """今の立ち絵に合う差分のパス。ふだんの顔・無い・元の絵と合わないなら None。

    None のときは見せる側がゲームの立ち絵をそのまま見せる。
    """
    if not character_folder or not is_name(expression) or expression == NEUTRAL:
        return None
    path = variant_path(character_folder, expression)
    if not os.path.isfile(path):
        return None
    record = _record(character_folder) or {}
    now = stamp(character_folder)
    if now is None or now != record.get("sha1"):
        return None
    names = record.get("expressions")
    if isinstance(names, list) and expression not in names:
        return None
    return path


# ---- 見せる ----------------------------------------------------------------

def portrait_path(character):
    """人物の立ち絵のパス（ゲームの `Character.image_src` の `fullbody`、文字列ならそのまま）。無ければ None。"""
    try:
        src = getattr(character, "image_src", None)
    except Exception:
        return None
    path = src.get("fullbody") if isinstance(src, dict) else src
    return path if isinstance(path, str) and path.strip() else None


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def shows(source, character_folder):
    """部品の絵（`source`）が、この人物の立ち絵のフォルダか、その差分の置き場のものか。"""
    if not isinstance(source, str) or not source or not character_folder:
        return False
    here = _norm(os.path.dirname(source))
    return here in (_norm(character_folder), _norm(folder_of(character_folder)))


def show(widget, character_folder, expression, base):
    """部品の絵を、その人物の表情の差分（ふだんの顔・差分が無いなら `base`）に替える。メインスレッドで呼ぶ。

    部品がほかの人物の絵を出しているとき（`shows` が偽）は触らない。
    見せる絵が前に見せたときから描き直されていたら（更新時刻が違う）、キャッシュを捨てて読み直す。
    替えた・読み直した絵のパスを返す。何もしなければ None。
    """
    if widget is None or not base:
        return None
    try:
        source = getattr(widget, "source", None)
    except Exception:
        return None
    if not shows(source, character_folder):
        return None
    want = image(character_folder, expression) or base
    try:
        mtime = os.stat(want).st_mtime_ns
    except OSError:
        mtime = None
    key = _norm(want)
    # 更新時刻はパスごと（Kivy のキャッシュが持っている版）と部品ごと（その部品が読み込んだ版）の両方で覚える。
    # パスごとだけだと、同じ絵を出す2つ目の部品は1つ目が読み直した後に描き直しに気付けない。
    # 部品ごとだけだと、初めてその絵を出す部品がキャッシュに残った古い版を受け取る
    store = _store()
    seen = store.setdefault("shown", {})
    marks = store.setdefault("widget_marks", {})
    mark = marks.get(id(widget))
    if mark is not None and mark[0] is not widget:
        mark = None                     # id が別の部品に使い回された
    stale = ((key in seen and seen[key] != mtime)
             or (mark is not None and mark[1] == key and mark[2] != mtime))
    for table in (seen, marks):
        if len(table) > 512:
            table.clear()
    seen[key] = mtime
    marks[id(widget)] = (widget, key, mtime)
    same = _norm(source) == key
    if same and not stale:
        return None
    if not same:
        widget.source = want
    if stale:
        ui.reload_image(widget)
    return want


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"wants": {}, "stamps": {}, "shown": {}, "widget_marks": {}, "records": {}})
