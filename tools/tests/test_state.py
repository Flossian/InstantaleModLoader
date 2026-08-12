# -*- coding: utf-8 -*-
"""`state/` の保存先の決め方と、壊れない書き込みを通す。ゲーム不要。

    python tools/tests/test_state.py

見ているのは2つ。どちらも**ローダの語彙**で、MOD 側に写すとドリフトする
（TECH.md §3.2.3）。

  保存先 … `world_key(app)` と `world_filename(key)`
  書込   … `write_json` / `write_text`（隣に書いてから差し替える）

保存先の検査が厚いのは、ここが**複数の MOD にまたがる取り決め**だから。
`301_` は `311_` が書いた `state/npc_profiles/<世界>.json` を読む ― 名前の
作り方が1文字でも違えば「相手のデータが無い」ことになる。以前は MOD ごとに
写していて、実際に `312_` がずれた。
"""

import json
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import state as st           # noqa: E402

_FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        _FAILS.append(msg)


class _World(object):
    def __init__(self, name):
        self.name = name


class _App(object):
    """世界名の在り処だけを持つ偽の app。"""

    def __init__(self, world_dict=None, world=None):
        if world_dict is not None:
            self.world_dict = world_dict
        if world is not None:
            self.world = world


def test_world_key():
    print("=== world_key: 世界の見分け方 ===")
    check(st.world_key(_App({"world_data": {"world_name": "灰の街"}})) == "灰の街",
          "world_data の world_name を最優先で読む")
    check(st.world_key(_App({"world_data": {"title": "題名だけ"}})) == "題名だけ",
          "world_name が無ければ name / title の順に落ちる")
    # セーブ側を先に見るのは、ロード直後に app.world がまだ組み上がっていない
    # 場合があるため。
    check(st.world_key(_App({"world_data": {"world_name": "セーブ側"}},
                            _World("実行時側"))) == "セーブ側",
          "両方あればセーブ側を採る")
    check(st.world_key(_App(world=_World("実行時の名"))) == "実行時の名",
          "セーブ側が読めなければ app.world.name を使う")
    check(st.world_key(_App()) == st.UNKNOWN_WORLD,
          "どこからも読めなければ既定の鍵に倒す")
    check(st.world_key(_App({"world_data": {"world_name": ""}})) == st.UNKNOWN_WORLD,
          "空文字は「読めた」に数えない")


def test_world_filename_is_stable():
    """**同じ鍵からは必ず同じ名前。** 複数の MOD が同じファイルを指すため。"""
    print("=== world_filename: 同じ鍵からは同じ名前 ===")
    check(st.world_filename("灰の街") == st.world_filename("灰の街"),
          "同じ鍵からは同じ名前")
    check(st.world_filename(" 灰の街 ") == st.world_filename(" 灰の街 "),
          "均される鍵でも毎回同じ（印は鍵から作る。hash() は使わない）")
    check(st.world_filename("灰の街", ".jsonl").endswith(".jsonl"),
          "拡張子は呼ぶ側が決める（122_ は1行1レコードなので .jsonl）")

    print("=== world_filename: 使えない名前を直す ===")
    check(st.world_filename("斜線_入り") == "斜線_入り.json",
          "そのまま使える名前は触らない")
    check(st.world_filename("末尾ドット.").startswith("末尾ドット"),
          "末尾の . を落とす（Windows が黙って切るため）")
    check(st.world_filename("末尾空白 ").startswith("末尾空白"),
          "末尾の空白も落とす")
    for bad in ("", ".", ".."):
        made = st.world_filename(bad)
        check(made.endswith(".json") and made != ".json",
              "ファイル名にできない鍵でも名前になる: {!r} -> {!r}".format(bad, made))
    long_name = st.world_filename("あ" * 300)
    check(len(long_name) - len(".json") <= st.MAX_STEM,
          "長すぎる鍵は上限に収める: {}".format(len(long_name) - len(".json")))

    print("=== world_filename: 予約デバイス名 ===")
    # 不正な文字が1つも無いのに作れない別種。素通しすると open() が失敗し、
    # 広い except に吸われて控えが黙って空に倒れる（知識は 110_ から）。
    for reserved in ("CON", "nul", "COM3", "LPT9"):
        made = st.world_filename(reserved)
        stem = made.split(".")[0]
        check(stem.upper() not in st.RESERVED,
              "予約名をそのまま使わない: {!r} -> {!r}".format(reserved, made))
        check(reserved in made,
              "予約名でも元の名前が読み取れる: {!r}".format(made))
    check(st.world_filename("CONTAINER") == "CONTAINER.json",
          "予約名で始まるだけの名前は触らない")


def test_world_filename_is_injective():
    """**違う鍵からは違う名前。** 別の世界の控えを自分のものとして読まないため。

    使える文字に均すだけでは単射にならない。均した結果が元と違うときだけ、
    鍵そのものから作った印を後ろに付けて分ける。
    """
    print("=== world_filename: 違う鍵は必ず違う名前 ===")
    pairs = [
        ("a/b", "a\\b", "区切り文字が違うだけの2つ"),
        ("CON", "_CON", "予約名を避けた印と、実在する同じ名前"),
        ("あ" * 130 + "X", "あ" * 130 + "Y", "先頭が同じで上限を超える2つ"),
        (" 灰の街", "灰の街 ", "前後の空白の位置だけが違う2つ"),
        ("灰の街", " 灰の街", "空白の有無だけが違う2つ"),
    ]
    for left, right, label in pairs:
        check(st.world_filename(left) != st.world_filename(right),
              "{}が同じファイルに落ちない".format(label))

    # 総当たりでも重ならないこと（取りこぼしの網）。
    keys = ["灰の街", " 灰の街", "灰の街 ", "灰/の街", "灰\\の街", "灰:の街",
            "CON", "_CON", "con", "NUL", "", ".", "..", "_",
            "あ" * 130 + "X", "あ" * 130 + "Y", "A" * 200, "A" * 201]
    made = [st.world_filename(k) for k in keys]
    dupes = {n for n in made if made.count(n) > 1}
    check(not dupes, "重なった名前: {}".format(sorted(dupes)))


def test_plain_names_keep_their_file():
    """普通の世界名には印が付かない。**置き場所を分ける前のファイルを引くため。**"""
    print("=== world_filename: 普通の名前は印が付かない ===")
    for name in ("灰の街", "Ashfall", "テストワールド", "灰の街_東", "第2世界",
                 "Ash Fall", "世界(2)"):
        made = st.world_filename(name)
        check(made == name + ".json",
              "印が付かない: {!r} -> {!r}".format(name, made))


def test_safe_writes():
    print("=== 壊れない書き方（write_json / write_text） ===")
    sandbox = tempfile.mkdtemp(prefix="instantale_state_")
    try:
        ctx = ml.ModContext(os.path.join(sandbox, "out"),
                            os.path.join(_ROOT, "runtime"),
                            os.path.join(sandbox, "state"))
        target = ctx.state_path("atomic.json")
        check(ctx.write_json(target, {"a": 1}) is True, "書けたら True を返す")
        with open(target, encoding="utf-8") as fh:
            check(json.load(fh) == {"a": 1}, "書いた内容がそのまま読める")

        # 素直に書けない値でも default=str が文字列にして通す。**書けないより、
        # 文字列になってでも残る方がよい**という判断なので、成功が正しい。
        check(ctx.write_json(target, {"odd": {1, 2}}) is True,
              "JSON にできない値も文字列にして書く（default=str）")

        # 循環参照は default でも救えない。直列化の時点で失敗し、書き込みまで
        # 到達しない ＝ 本体は無傷のまま。
        loop = {}
        loop["self"] = loop
        before = open(target, encoding="utf-8").read()
        check(ctx.write_json(target, loop) is False,
              "書けなければ False を返す（例外を投げない）")
        check(open(target, encoding="utf-8").read() == before,
              "失敗しても元のファイルは無傷（切り詰められていない）")
        check(not os.path.exists(target + ml.TEMP_SUFFIX), "書きかけの .tmp を残さない")

        # 置き換えであって追記ではない。
        ctx.write_json(target, {"long": "x" * 200})
        ctx.write_json(target, {"s": 1})
        with open(target, encoding="utf-8") as fh:
            check(json.load(fh) == {"s": 1}, "短い内容で上書きしても前の内容が残らない")

        # **いちばん危ない窓**: 仮ファイルは書けたのに差し替えで落ちる場合。
        # ここで本体が壊れると、隣に書く意味そのものが無くなる。
        ctx.write_json(target, {"keep": "me"})
        kept = open(target, encoding="utf-8").read()
        saved_replace = ml.os.replace
        ml.os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("差し替え失敗"))
        try:
            check(ctx.write_json(target, {"lost": 1}) is False,
                  "差し替えに失敗したら False")
        finally:
            ml.os.replace = saved_replace
        check(open(target, encoding="utf-8").read() == kept,
              "差し替えに失敗しても本体は前のまま")
        check(not os.path.exists(target + ml.TEMP_SUFFIX),
              "差し替えに失敗しても書きかけを片付ける")

        # JSON 文書1つではない記録（1行1レコード）は write_text 側。
        lines = ctx.state_path("lines.jsonl")
        check(ctx.write_text(lines, '{"a":1}\n{"a":2}\n') is True,
              "write_text も書けたら True")
        with open(lines, encoding="utf-8") as fh:
            check(len(fh.read().splitlines()) == 2, "1行1レコードで書ける")

        # 親フォルダが無くても自分で作る（呼び出し側に makedirs を強いない）。
        nested = os.path.join(sandbox, "state", "deep", "er", "n.json")
        check(ml.write_json(nested, {"n": 1}) and os.path.isfile(nested),
              "親フォルダが無ければ作ってから書く")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_world_key_of_dict():
    """**セーブの辞書から**引く入口（`104_` のように app を持たないフック用）。"""
    from instantale_modloader.state import world_key_of_dict

    for key in ("world_name", "name", "title"):
        check(world_key_of_dict({"world_data": {key: "灰の街"}}) == "灰の街",
              "world_data['{}'] から引ける".format(key))
    check(world_key_of_dict({}, "id:1") == "id:1",
          "読めなければ呼び側が決めた fallback")
    check(world_key_of_dict(None, "id:1") == "id:1", "辞書でなくても落ちない")
    check(world_key_of_dict({"world_data": {"name": ""}}, "x") == "x",
          "空文字は名前として採らない")
    # app 経由と辞書経由で**同じ鍵**が出ること（別々に持つとここがずれる）。
    from instantale_modloader.state import world_key

    class App(object):
        world_dict = {"world_data": {"world_name": "澱みの宿場町"}}
        world = None

    check(world_key(App()) == world_key_of_dict(App.world_dict),
          "app 経由と辞書経由で同じ鍵が出る")


def main():
    test_world_key()
    test_world_key_of_dict()
    test_world_filename_is_stable()
    test_world_filename_is_injective()
    test_plain_names_keep_their_file()
    test_safe_writes()
    print()
    if _FAILS:
        print("{} 件失敗: {}".format(len(_FAILS), _FAILS))
        return 1
    print("全て通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
