# -*- coding: utf-8 -*-
"""ローダの窓口 `expressions`（会話の立ち絵の表情差分）をゲーム抜きで通す。

    python tools/tests/test_expressions.py

  宣言   … 表情を見せる MOD が宣言すれば wanted が真。同じ持ち主は差し替え、用済みの宣言は数えない
  置き場 … 立ち絵のフォルダの下の expressions\\<表情>.png
  印     … 描き終えたら元の絵の印を書く。今の元の絵と合う差分だけ image が返す
  消す   … clear は表情の一覧のファイルと印だけ消し、ほかの物は残す
  一覧   … 描く側が登録した一覧（追加の表情）に、説明・名前の見分けが従う
  判定   … plan が描く・名前を変える・消す表情を、印のタグと id から見分ける
"""
import io
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import expressions  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Ctx(object):
    def __init__(self):
        self.old = False

    def superseded(self):
        return self.old


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "wb") as fh:
        fh.write(body)


def main():
    expressions.reset()
    print("宣言")
    check("表情の一覧はふだんの顔が先頭で、差分はその後の4つ",
          expressions.NAMES[0] == expressions.NEUTRAL == "neutral"
          and expressions.VARIANTS == ("smile", "angry", "sad", "surprised"))
    check("誰も宣言しなければ描かない", expressions.wanted() is False)
    first, second = Ctx(), Ctx()
    expressions.want("347", first)
    check("宣言すれば描く", expressions.wanted() is True and expressions.wanters() == ["347"])
    expressions.want("347", second)
    first.old = True
    check("同じ持ち主の宣言は差し替える（前の ctx が用済みでも数える）", expressions.wanted() is True)
    second.old = True
    check("用済みの ctx の宣言は数えない", expressions.wanted() is False)
    expressions.want("347", Ctx())
    expressions.withdraw("347")
    check("withdraw で下げる", expressions.wanted() is False)
    check("表情の名前の見分け", expressions.is_name("smile") and not expressions.is_name("happy")
          and not expressions.is_name(None))

    print("置き場")
    base = tempfile.mkdtemp(prefix="expressions_test_")
    try:
        folder = expressions.character_dir("世界", "アリス", base)
        check("立ち絵のフォルダは worlds\\<世界>\\characters\\<名前>",
              folder == os.path.join(base, "worlds", "世界", "characters", "アリス"), folder)
        check("世界か名前が空なら None",
              expressions.character_dir("", "アリス", base) is None
              and expressions.character_dir("世界", None, base) is None)
        check("差分は expressions\\<表情>.png",
              expressions.variant_path(folder, "smile") == os.path.join(folder, "expressions", "smile.png"))

        print("印")
        check("元の絵が無ければ印も無い", expressions.stamp(folder) is None)
        source = os.path.join(folder, expressions.SOURCE)
        write(source, b"first portrait")
        stamp = expressions.stamp(folder)
        check("印は元の絵の中身の SHA-1", isinstance(stamp, str) and len(stamp) == 40, stamp)
        for name in expressions.VARIANTS:
            write(expressions.variant_path(folder, name), name.encode())
        check("印を書くまでは差分を返さない（描いている途中）", expressions.image(folder, "smile") is None)
        check("印を書ける", expressions.seal(folder, stamp) is True and expressions.sealed(folder) == stamp)
        check("印の合う差分を返す", expressions.image(folder, "smile") == expressions.variant_path(folder, "smile"))
        check("ふだんの顔と一覧に無い表情は None（ゲームの立ち絵）",
              expressions.image(folder, "neutral") is None and expressions.image(folder, "happy") is None)
        os.remove(expressions.variant_path(folder, "sad"))
        check("ファイルの無い表情は None", expressions.image(folder, "sad") is None)
        time.sleep(0.01)
        write(source, b"redrawn portrait")
        check("元の絵が描き直されたら、前の差分は返さない", expressions.image(folder, "smile") is None)
        check("印の無いフォルダには印を書かない", expressions.seal(folder, None) is False)
        expressions.clear(folder)
        now = expressions.stamp(folder)
        check("差分の無い印も書ける（顔が見つからない人物。描く側が同じ元の絵に描き直しを繰り返さない）",
              expressions.seal(folder, now) is True and expressions.sealed(folder) == now
              and expressions.image(folder, "smile") is None)
        write(expressions.variant_path(folder, "smile"), b"left by an older job")
        check("印に載っていない表情の差分は、置き場に在っても返さない（前の立ち絵の仕事が後から書いた差分）",
              expressions.image(folder, "smile") is None)
        expressions.seal(folder, now, names=["smile"], complete=False)
        check("描き揃わなかった印は complete が偽で、載せた表情は返す",
              not expressions.complete(folder) and expressions.image(folder, "smile") is not None)
        loads = []
        real_load = expressions.json.load
        expressions.json.load = lambda fh: loads.append(1) or real_load(fh)
        try:
            for _ in range(5):
                expressions.image(folder, "smile")
            check("印が変わらない間は source.json を読み直さない", len(loads) <= 1, loads)
            write(expressions.variant_path(folder, "angry"), b"angry")
            expressions.seal(folder, now, names=["angry"], complete=False)     # 同じ大きさの書き直し
            check("印を書き直したら読み直す（同じ大きさでも）",
                  expressions.image(folder, "smile") is None
                  and expressions.image(folder, "angry") is not None, loads)
        finally:
            expressions.json.load = real_load
        os.remove(expressions.variant_path(folder, "angry"))
        expressions.seal(folder, now, names=[])
        check("印を書いた後に一時ファイルを残さない",
              sorted(os.listdir(expressions.folder_of(folder))) == sorted([expressions.SEAL, "smile.png"]),
              os.listdir(expressions.folder_of(folder)))

        print("消す")
        other = os.path.join(expressions.folder_of(folder), "note.txt")
        write(other, b"keep")
        expressions.clear(folder)
        left = sorted(os.listdir(expressions.folder_of(folder)))
        check("表情の一覧のファイルと印だけ消し、ほかの物は残す", left == ["note.txt"], left)
        expressions.clear(os.path.join(base, "nowhere"))
        check("無いフォルダを消しても投げない", True)

        print("選ばせる・見せる")
        check("LLM の返した表情は空白と大文字を均し、一覧に無い・読めない値はふだんの顔",
              [expressions.name_of(v) for v in (" SAD ", "happy", None, 3)] == ["sad", "neutral", "neutral", "neutral"])
        check("表情の説明は5つの表情を名指しする", all(name in expressions.GUIDE for name in expressions.NAMES))

        class Character(object):
            def __init__(self, src):
                self.image_src = src
        portrait = os.path.join(folder, "reduced_color_image.png")
        check("立ち絵のパスは image_src の fullbody か文字列。無ければ None",
              expressions.portrait_path(Character({"fullbody": portrait})) == portrait
              and expressions.portrait_path(Character(portrait)) == portrait
              and expressions.portrait_path(Character({"fullbody": " "})) is None
              and expressions.portrait_path(None) is None)

        class Widget(object):
            def __init__(self, source):
                self.source = source
                self.reloads = 0

            def reload(self):
                self.reloads += 1
        write(portrait, b"portrait")
        stamp = expressions.stamp(folder)
        for e in expressions.VARIANTS:
            write(expressions.variant_path(folder, e), e.encode())
        expressions.seal(folder, stamp)
        widget = Widget(portrait)
        got = expressions.show(widget, folder, "smile", portrait)
        check("部品の絵を表情の差分に替える（初めて見せる絵は読み直さない）",
              got == widget.source == expressions.variant_path(folder, "smile") and widget.reloads == 0,
              (got, widget.source, widget.reloads))
        check("同じ絵のままなら何もしない", expressions.show(widget, folder, "smile", portrait) is None
              and widget.reloads == 0)
        expressions.show(widget, folder, "neutral", portrait)
        check("ふだんの顔ならゲームの立ち絵に戻す", widget.source == portrait, widget.source)
        time.sleep(0.01)
        write(expressions.variant_path(folder, "smile"), b"smile redrawn")
        os.utime(expressions.variant_path(folder, "smile"), ns=(time.time_ns(), time.time_ns() + 10 ** 9))
        expressions.show(widget, folder, "smile", portrait)
        check("前に見せた後に描き直された差分は、キャッシュを捨てて読み直す",
              widget.source == expressions.variant_path(folder, "smile") and widget.reloads == 1, widget.reloads)
        # 同じ差分を出す部品が2つ。描き直しの後、1つ目が読み直しても2つ目も読み直す
        left, right = Widget(portrait), Widget(portrait)
        expressions.show(left, folder, "angry", portrait)
        expressions.show(right, folder, "angry", portrait)
        write(expressions.variant_path(folder, "angry"), b"angry redrawn")
        os.utime(expressions.variant_path(folder, "angry"), ns=(time.time_ns(), time.time_ns() + 2 * 10 ** 9))
        expressions.show(left, folder, "angry", portrait)
        expressions.show(right, folder, "angry", portrait)
        check("同じ差分を出す2つ目の部品も、描き直しの後に読み直す",
              left.reloads == 1 and right.reloads == 1, (left.reloads, right.reloads))
        other_widget = Widget(os.path.join(base, "w", "characters", "someone", "reduced_color_image.png"))
        check("部品がほかの人物の絵を出しているときは触らない",
              expressions.show(other_widget, folder, "smile", portrait) is None
              and other_widget.source.endswith(os.path.join("someone", "reduced_color_image.png")))

        print("一覧の登録")
        check("登録が無ければ基本表情の5つ", expressions.names() == expressions.NAMES
              and expressions.guide() == expressions.GUIDE)
        owner = Ctx()
        rows = [{"name": "smile"}, {"name": "shy", "meaning": "照れる\n恥ずかしがる"}, {"name": "shy"},
                {"name": "Bad Name"}, {"name": "neutral"}, "x"]
        expressions.define("343", owner, lambda: rows)
        check("登録した一覧（ふだんの顔が先頭。重なり・使えない名前は飛ばし、基本表情の意味は窓口のもの）",
              expressions.entries() == [("neutral", "普段の顔"), ("smile", "笑う・機嫌がよい"),
                                        ("shy", "照れる 恥ずかしがる")], expressions.entries())
        check("説明・名前の見分け・値の直し方は今の一覧に従う（外した基本表情は選ばせない）",
              "shy=照れる 恥ずかしがる" in expressions.guide() and "angry" not in expressions.guide()
              and expressions.name_of(" SHY ") == "shy" and expressions.name_of("angry") == "neutral"
              and expressions.variants() == ("smile", "shy"), expressions.guide())
        rows.append({"name": "pout", "meaning": "むくれる"})
        check("一覧は使うたびに読む（設定を変えれば次の会話から効く）", "pout" in expressions.names())
        expressions.define("343", owner, lambda: 1 / 0)
        check("読み出しに失敗したら基本表情の5つ", expressions.names() == expressions.NAMES)
        expressions.define("343", owner, lambda: rows)
        owner.old = True
        check("用済みの ctx の登録は使わない", expressions.names() == expressions.NAMES)
        expressions.define("343", Ctx(), lambda: rows)
        expressions.undefine("343")
        check("undefine で下げる", expressions.names() == expressions.NAMES)
        check("追加の表情の名前の形", expressions.is_valid_name("shy_2") and not expressions.is_valid_name("smile")
              and not expressions.is_valid_name("2shy") and not expressions.is_valid_name("Shy")
              and not expressions.is_valid_name("a" * 33))

        print("描き直しの判定")
        person = expressions.character_dir("世界", "ボブ", base)
        write(os.path.join(person, expressions.SOURCE), b"bob portrait")
        now = expressions.stamp(person)
        tags = {"smile": "s1", "angry": "a1", "shy": "blush"}
        wanted = [{"name": "smile", "tags": "s1"}, {"name": "angry", "tags": "a1"},
                  {"name": "shy", "tags": "blush", "id": "x1"}]
        check("印が無ければ None（全部描く）", expressions.plan(person, wanted) is None
              and expressions.needs(person, wanted))
        for e in ("smile", "angry", "shy"):
            write(expressions.variant_path(person, e), e.encode())
        expressions.seal(person, now, names=["smile", "angry", "shy"], tags=tags, ids={"shy": "x1"})
        got = expressions.plan(person, wanted)
        check("描き揃っていれば何もしない",
              got == {"keep": ["smile", "angry", "shy"], "draw": [], "rename": [], "remove": [], "stale": {}}
              and not expressions.needs(person, wanted), got)
        got = expressions.plan(person, [dict(wanted[0], tags="s2")] + wanted[1:]
                               + [{"name": "pout", "tags": "pout", "id": "x2"}])
        check("タグの変わった表情と足した表情を描く（変わった表情は前のタグを返す）",
              got["draw"] == ["smile", "pout"] and got["stale"] == {"smile": "s1"}
              and got["keep"] == ["angry", "shy"], got)
        got = expressions.plan(person, wanted[:2] + [{"name": "blushing", "tags": "blush", "id": "x1"}])
        check("id が同じで名前だけ変わった表情はファイル名を変える", got["rename"] == [("shy", "blushing")]
              and got["draw"] == [] and got["remove"] == [], got)
        got = expressions.plan(person, wanted[:2] + [{"name": "blushing", "tags": "red face", "id": "x1"}])
        check("名前とタグが両方変わったら描く（前の名前は消す）", got["rename"] == [] and got["draw"] == ["blushing"]
              and got["remove"] == ["shy"], got)
        got = expressions.plan(person, wanted[:1])
        check("一覧から外した表情は消す", got["remove"] == ["angry", "shy"] and got["draw"] == [], got)
        moved = expressions.tidy(person, expressions.plan(
            person, wanted[:1] + [{"name": "blushing", "tags": "blush", "id": "x1"}]))
        check("tidy は名前を変え、外した表情を消す", moved == ["blushing"]
              and os.path.isfile(expressions.variant_path(person, "blushing"))
              and not os.path.isfile(expressions.variant_path(person, "shy"))
              and not os.path.isfile(expressions.variant_path(person, "angry")))
        expressions.seal(person, now, names=["smile"])                 # 前の版の印（タグが無い）
        got = expressions.plan(person, [{"name": "smile", "tags": "anything"}, {"name": "sad", "tags": "x"}])
        check("タグの無い印は今のタグで描いたものとみなし、足りない表情だけ描く",
              got["keep"] == ["smile"] and got["draw"] == ["sad"], got)
        expressions.seal(person, now, names=[])
        check("顔が見つからなかった印には何もしない",
              expressions.plan(person, wanted) == {"keep": [], "draw": [], "rename": [], "remove": [], "stale": {}})
        write(expressions.variant_path(person, "pout"), b"pout")
        expressions.seal(person, now, names=["pout"])
        expressions.clear(person)
        check("clear は印に載った追加の表情も消す（一覧にも印にも無いファイルは残す）",
              not os.path.isfile(expressions.variant_path(person, "pout"))
              and os.path.isfile(expressions.variant_path(person, "blushing")))
    finally:
        shutil.rmtree(base, ignore_errors=True)
        expressions.reset()

    print()
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
