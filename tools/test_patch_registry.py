# -*- coding: utf-8 -*-
"""パッチ台帳・on_ready・マニフェストをゲーム抜きで通す。

    python tools/test_patch_registry.py

偽のモジュール（`fakegame`）を `sys.modules` に差し込み、そこへ MOD を模した
パッチを当てて次を確認する。ゲームも Kivy も要らない。

  帰属     … どの MOD がどの対象に当てたかが適用順で並ぶ
  重なり   … 2つ以上の MOD が触っている対象だけが競合として出る
  未解決   … 対象が消えている場合を検出し、理由（名前ごと無い / None）まで分かる
  記録先送 … 未 import のモジュールは「未解決」ではなく「保留」に入る
  投げる前 … required=True で例外になる場合も、投げる前に台帳へ載っている
  打ち間違 … @patch は存在しない名前を黙って新設しない
  巻き戻し … apply() が例外で抜けても「実行中の MOD」が残らない
  1回きり  … on_ready は再 boot（再注入・当て直し）をまたいでも1回しか走らない
  降り方   … ctx.superseded() が「次の boot」と「ローダごと読み直し」の両方で立つ
  無害化   … on_ready の中の例外はゲームへ漏らさない
  読み分け … read_json は「無い」と「在るのに読めない」を区別し、後者を記録する
  探索     … mod.json を持つフォルダだけを拾い、入口の名前は mod.json が決める
  適用順   … load_order.json に従い、未記載は末尾・実体なしは飛ばす・壊れても動く
  名乗り   … mod.json の読み取り、英日の穴埋め、欠落時の既定値

`on_ready` の「1回きり」は §3.6 の要。ここが壊れると、再注入のたびに掃除や
スレッド起動が積み上がる。
"""
import json
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import patch as P            # noqa: E402
from instantale_modloader import patch_registry as R   # noqa: E402

_FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        _FAILS.append(msg)


def survey(mods_dir):
    """同梱 mod の一覧と適用順を取り出す。discover() はローダ・GUI・静的検査の共通の入口。

    `debug=True` で呼ぶのは `check_mods.py` と同じ理由 ― 検査は**入っている MOD を
    全部見る**のが仕事で、デバッグモードの入切で範囲が変わってはいけない。
    切のまま呼ぶと、計測 MOD（`"debug": true`）が `order` に居ないぶん
    「同梱 mod は全て名乗っている」が素の配布物でも赤くなる。

    突き合わせに `order` ではなく `listed` を使う。`order` は**切られている mod が
    落ちた後**の並びなので、利用者が GUI で1本切っただけでこの検査が赤くなる
    （手元で未公開の mod を `disabled` に置いている間もずっと赤い）。
    `listed` は無効な mod も宣言された位置に残すので、切った・切らないに
    左右されずに「宣言と実体が一致しているか」だけを見られる。
    """
    return ml.discover(mods_dir, debug=True)


def order_of(mods_dir):
    """適用順だけを取り出す（`survey()` の要約）。"""
    return survey(mods_dir)["order"]


def main():
    # ローダのログをファイルへ出させない。out/ を汚さないため。
    out_dir = os.path.join(_ROOT, "out", "test")

    victim = types.ModuleType("fakegame")
    victim.target_a = lambda x: x + 1
    victim.target_b = lambda x: x * 2
    sys.modules["fakegame"] = victim

    print("=== 帰属と重なり ===")
    P.set_generation("gen1")
    ctx = ml.ModContext(out_dir, os.path.join(_ROOT, "runtime"))

    R.begin_mod("100_alpha.py")
    ctx._mod = "100_alpha.py"

    @ctx.wrap("fakegame:target_a")
    def _a1(orig, x):
        return orig(x) + 10
    R.end_mod()

    R.begin_mod("200_beta.py")
    ctx._mod = "200_beta.py"

    @ctx.wrap("fakegame:target_a")      # わざと同じ対象に重ねる（正常な使い方）
    def _a2(orig, x):
        return orig(x) + 100

    @ctx.wrap("fakegame:target_b")
    def _b1(orig, x):
        return orig(x)

    @ctx.wrap("fakegame:does_not_exist", required=False)    # 消えた関数
    def _gone(orig, *a):
        return None

    @ctx.wrap("not_imported_yet:thing", required=False)     # 未 import
    def _later(orig, *a):
        return None
    R.end_mod()

    table = R.by_target()
    check(table.get("fakegame:target_a") == ["100_alpha.py", "200_beta.py"],
          "同じ対象の 2 MOD が適用順で並ぶ: {}".format(table.get("fakegame:target_a")))
    check(table.get("fakegame:target_b") == ["200_beta.py"], "単独の対象は 1 MOD")
    check(list(R.conflicts()) == ["fakegame:target_a"],
          "競合として出るのは重なった対象だけ: {}".format(list(R.conflicts())))
    # 台帳が実際の挙動と一致していること（+1 → +10 → +100）
    check(victim.target_a(0) == 111, "重ね順どおりに動く: {}".format(victim.target_a(0)))

    print("=== 未解決と保留 ===")
    missing = R.unresolved()
    check(len(missing) == 1 and missing[0][1] == "fakegame:does_not_exist",
          "未解決は消えた関数のみ: {}".format(missing))
    check(missing[0][2] == "200_beta.py", "未解決の帰属が正しい")
    check(missing[0][3] == "attribute not found",
          "理由が『名前ごと無い』と分かる: {}".format(missing[0][3]))

    deferred = R.entries(R.DEFERRED)
    check(len(deferred) == 1 and deferred[0][3] == "not_imported_yet",
          "未 import は保留であって未解決ではない: {}".format(deferred))

    victim.is_none = None
    R.begin_mod("200_beta.py")
    try:
        @ctx.wrap("fakegame:is_none", required=False)
        def _n(orig, *a):
            return None
    finally:
        R.end_mod()
    check(R.unresolved()[-1][3] == "resolved to None",
          "『名前はあるが None』は別の理由として出る: {}".format(R.unresolved()[-1][3]))

    print("=== 投げる前に記録する ===")
    before = len(R.unresolved())
    try:
        @ctx.wrap("fakegame:nope")      # required=True（既定）
        def _r(orig, *a):
            return None
        check(False, "required=True なのに例外が出なかった")
    except LookupError:
        check(len(R.unresolved()) == before + 1,
              "例外で apply が落ちても、何が無かったかは台帳に残る")

    print("=== @patch は名前を新設しない ===")
    R.begin_mod("200_beta.py")
    try:
        @ctx.patch("fakegame:targt_a")      # typo
        def _typo(x):
            return x
        check(False, "打ち間違えた patch が通ってしまった")
    except LookupError:
        check(not hasattr(victim, "targt_a"), "typo で新しい名前を作らない")
        check(R.unresolved()[-1][1] == "fakegame:targt_a", "typo が未解決に載る")
    finally:
        R.end_mod()
    check(R._current is None, "apply を抜けたら『実行中の MOD』は空に戻る")

    print("=== 逆引きと集計 ===")
    check(R.by_mod().get("100_alpha.py") == ["fakegame:target_a"],
          "MOD -> 対象の逆引き: {}".format(R.by_mod().get("100_alpha.py")))
    check(R.by_mod().get("200_beta.py") == ["fakegame:target_a", "fakegame:target_b"],
          "1 MOD が複数対象を触る場合も並ぶ: {}".format(R.by_mod().get("200_beta.py")))
    s = R.summary()
    check(s["counts"]["applied"] == 3 and s["counts"]["targets"] == 2
          and s["counts"]["mods"] == 2 and s["counts"]["conflicts"] == 1,
          "集計が台帳と一致: {}".format(s["counts"]))
    check(set(s) == {"counts", "by_target", "by_mod", "conflicts", "deferred", "unresolved"},
          "summary の項目: {}".format(sorted(s)))

    print("-- 報告の見え方 --")
    for line in R.format_report():
        print("   " + line)

    print("=== on_ready は 1 回きり ===")
    calls = []
    ctx._mod = "300_gamma.py"
    check(ctx.on_ready(lambda: calls.append("x"), key="sweep") is True,
          "1 回目は積まれる")
    check(ctx.on_ready(lambda: calls.append("x"), key="sweep") is False,
          "同じ boot の 2 回目は捨てられる")
    ml._dispatch_ready()
    check(calls == ["x"], "実行は 1 回だけ: {}".format(calls))

    # 世代を進める = 再注入 / 遅延当て直し
    P.set_generation("gen2")
    ml._state["ready"] = []
    check(ctx.on_ready(lambda: calls.append("y"), key="sweep") is False,
          "boot をまたいでも積まれない")
    ml._dispatch_ready()
    check(calls == ["x"], "再 boot で再実行されない: {}".format(calls))
    check(R.by_target() == {}, "台帳は世代ごとに作り直される")

    def boom():
        raise RuntimeError("intentional (this traceback is expected)")

    ctx.on_ready(boom, key="boom")
    try:
        ml._dispatch_ready()
        check(True, "on_ready の中の例外はゲームへ漏れない")
    except BaseException as exc:
        check(False, "on_ready の例外が漏れた: {!r}".format(exc))
    check(ctx.on_ready(boom, key="boom") is False,
          "例外で終わった処理は積み直さない（毎回失敗し続けない）")

    print("=== on_ready: 登録と実行済みの関係 ===")
    # 「積んだが、流す前に次の boot が来た」= 遅延当て直しの最中に起きうる並び。
    # 印は登録時に付くので二重には積まれず、かつ積んだ1件は失われない。
    ran = []
    ml._state["ready"] = []
    ctx.on_ready(lambda: ran.append(1), key="pending")
    queued = list(ml._state["ready"])
    P.set_generation("gen3")                    # 次の boot が始まった
    check(ctx.on_ready(lambda: ran.append(1), key="pending") is False,
          "流す前に再 boot が来ても二重に積まれない")
    ml._state["ready"] = queued                 # boot は自分が積んだ分を流す
    ml._dispatch_ready()
    check(ran == [1], "積んだ1件は失われず、1回だけ走る: {}".format(ran))

    # Clock に載せられなかった場合。一度も走っていないので印を外し、
    # 次の boot で積み直せなければならない（走らないまま迷子になる一件を作らない）。
    class _BadClock:
        @staticmethod
        def schedule_once(fn, delay):
            raise RuntimeError("intentional (Clock unavailable)")

    fake = types.ModuleType("kivy.clock")
    fake.Clock = _BadClock
    sys.modules["kivy"] = types.ModuleType("kivy")
    sys.modules["kivy.clock"] = fake
    try:
        ml._state["ready"] = []
        check(ctx.on_ready(lambda: ran.append(2), key="retry") is True, "1 回目は積まれる")
        ml._dispatch_ready()
        check(ran == [1], "載せられなかったので走っていない: {}".format(ran))
        check(ctx.on_ready(lambda: ran.append(2), key="retry") is True,
              "載せ損ねた分は次の boot で積み直せる")
    finally:
        del sys.modules["kivy.clock"], sys.modules["kivy"]
        ml._state["ready"] = []

    print("=== フォルダ構成の探索と読み込み ===")
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="instantale_runtime_")
    tmp_mods = os.path.join(tmp, "mods")      # 実物と同じ runtime/mods の配置にする
    try:
        def put(rel, text):
            full = os.path.join(tmp_mods, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(text)

        def put_json(rel, obj):
            put(rel, json.dumps(obj, ensure_ascii=False))

        # 入口 + 分割した中身 + 同梱データ を持つ mod。
        # フォルダ名も入口のファイル名も自由（mod.json が入口を名指しする）。
        put_json("zebra/mod.json", {"entry": "whatever.py", "name": "Zebra"})
        put("zebra/whatever.py",
            "from . import helper\n"
            "LOADED = helper.VALUE\n")
        put("zebra/helper.py", "VALUE = 42\n")
        put("zebra/data/table.json", '{"a": 1}\n')
        put_json("apple/mod.json", {"entry": "apple.py"})
        put("apple/apple.py", "X = 1\n")
        # 無効化されたもの・mod.json が無いもの・キャッシュ は拾わない
        put_json("_disabled/mod.json", {"entry": "x.py"})
        put("no_manifest/thing.py", "X = 1\n")
        put("__pycache__/x.py", "X = 1\n")

        check(ml._installed(tmp_mods) == ["apple", "zebra"],
              "拾うのは mod.json を持つフォルダだけ: {}".format(ml._installed(tmp_mods)))

        # 順序ファイルが無ければフォルダ名順
        check(order_of(tmp_mods) == ["apple", "zebra"],
              "順序ファイルが無ければ名前順")
        # あれば、名前順ではなくそちらに従う
        put_json("load_order.json", {"order": ["zebra", "apple"]})
        check(order_of(tmp_mods) == ["zebra", "apple"],
              "load_order.json が適用順を決める: {}".format(order_of(tmp_mods)))
        # 載っていない mod は捨てずに末尾へ（置いただけで動く）
        put_json("load_order.json", {"order": ["zebra"]})
        check(order_of(tmp_mods) == ["zebra", "apple"],
              "順序に無い mod は末尾に回る（落とさない）")
        # 実体の無い記述は飛ばす（消した mod の記述が残っていても壊れない）
        put_json("load_order.json", {"order": ["gone", "zebra", "apple"]})
        check(order_of(tmp_mods) == ["zebra", "apple"], "実体の無い記述は飛ばす")
        # 壊れていても必ず決まった順で動く（ここで全滅させない）
        put("load_order.json", "{ this is not json")
        check(order_of(tmp_mods) == ["apple", "zebra"],
              "順序ファイルが壊れていても名前順で動く")
        put_json("load_order.json", {"order": ["zebra", "apple"]})

        # -- 手元だけの順序ファイル ---------------------------------------
        # 未公開の MOD を手元で動かすためのもの。在れば load_order.json に
        # 優先し、`.gitignore` に入っているのでコミットにも配布物にも入らない。
        check(ml.order_path(tmp_mods).endswith(ml.ORDER_NAME),
              "手元用が無ければ配布用を使う: {}".format(ml.order_path(tmp_mods)))
        put_json("load_order.local.json", {"order": ["apple", "zebra"]})
        check(ml.order_path(tmp_mods).endswith(ml.ORDER_LOCAL_NAME),
              "手元用が在ればそちらを使う: {}".format(ml.order_path(tmp_mods)))
        check(order_of(tmp_mods) == ["apple", "zebra"],
              "手元用が適用順を決める（配布用は無視される）: {}".format(
                  order_of(tmp_mods)))
        found_local = ml.discover(tmp_mods)
        check(any(ml.ORDER_LOCAL_NAME in n for n in found_local["notes"]),
              "何で動いているかを notes に残す: {}".format(found_local["notes"]))
        check(found_local["problems"] == [],
              "**問題としては数えない**（未公開 MOD の間ずっと赤にしない）: {}"
              .format(found_local["problems"]))
        # 手元用にだけ載っている mod は、配布用から見れば「記載が無い」。
        # それでも問題にならないこと（＝配布用の側を読みに行っていないこと）。
        put_json("load_order.json", {"order": ["zebra"]})
        check(ml.discover(tmp_mods)["problems"] == [],
              "手元用が在る間は配布用の記載漏れを問題にしない: {}".format(
                  ml.discover(tmp_mods)["problems"]))
        os.remove(os.path.join(tmp_mods, "load_order.local.json"))
        check(any("記載の無い" in p for p in ml.discover(tmp_mods)["problems"]),
              "手元用を消せば配布用の記載漏れがまた見える: {}".format(
                  ml.discover(tmp_mods)["problems"]))
        put_json("load_order.json", {"order": ["zebra", "apple"]})

        mod = ml._load_mod_file(os.path.join(tmp_mods, "zebra", "whatever.py"))
        check(mod.LOADED == 42, "入口名が何であれ from . import が効く（パッケージ扱い）")
        check(sys.modules.get("instantale_mod_zebra") is mod,
              "sys.modules にはフォルダ名で載る（入口のファイル名ではない）")
        check("instantale_mod_zebra.helper" in sys.modules,
              "分割した中身も mod 固有の名前空間に入る（ゲームと衝突しない）")

        # 分割した mod を直して注入し直したとき、**中の部品も読み直される**こと。
        # 入口だけ読み直して部品を sys.modules に残すと、新しい入口が古い部品を
        # 呼び続け、追加した関数が「無い」と言われる。
        put("zebra/helper.py", "VALUE = 99\nFRESH = True\n")
        again = ml._load_mod_file(os.path.join(tmp_mods, "zebra", "whatever.py"))
        check(again.LOADED == 99,
              "注入し直すと分割した中身も読み直される（{} を読んだ）".format(again.LOADED))
        check(getattr(sys.modules["instantale_mod_zebra.helper"], "FRESH", False),
              "部品に足した名前が、注入し直した入口から見える")

        # 同梱データは ctx.mod_dir から読む
        ctx.runtime_dir = tmp
        ctx._mod = "zebra"
        check(os.path.isfile(os.path.join(ctx.mod_dir, "data", "table.json")),
              "ctx.mod_dir から同梱データを引ける")
        ctx._mod = None
        check(ctx.mod_dir is None, "apply() の外では mod_dir は None")
        ctx.runtime_dir = os.path.join(_ROOT, "runtime")
        for key in ("instantale_mod_zebra", "instantale_mod_zebra.helper"):
            sys.modules.pop(key, None)

        print("=== 名乗り（mod.json）===")
        put_json("full/mod.json", {
            "entry": "m.py",
            "name": {"en": "Something", "ja": "何か"},
            "description": {"en": "does something"},
            "version": 2,                 # str でない値を入れても落ちない
            "author": "  flossian  "})
        man = ml._manifest(tmp_mods, "full")
        check(man["version"] == "2" and man["author"] == "flossian",
              "str 化と空白除去: {}".format(man))
        check(man["name"] == {"en": "Something", "ja": "何か"},
              "英日が揃っていればそのまま: {}".format(man["name"]))
        check(man["description"] == {"en": "does something", "ja": "does something"},
              "ja が無ければ英語で埋める（GUI が空欄にならない）: {}".format(
                  man["description"]))

        put_json("jaonly/mod.json", {"entry": "m.py", "name": {"ja": "日本語だけ"}})
        check(ml._manifest(tmp_mods, "jaonly")["name"] == {"en": "日本語だけ",
                                                           "ja": "日本語だけ"},
              "逆向き（日本語だけ）も埋まる")

        put_json("plain/mod.json", {"entry": "m.py", "name": "Plain string"})
        check(ml._manifest(tmp_mods, "plain")["name"] == {"en": "Plain string",
                                                          "ja": "Plain string"},
              "name は文字列1つでも書ける")

        bare = ml._manifest(tmp_mods, "nothing_here")
        check(bare["name"] == {"en": "nothing_here", "ja": "nothing_here"},
              "mod.json が読めなければフォルダ名にフォールバック: {}".format(bare["name"]))
        check(bare["description"] == {"en": "", "ja": ""},
              "description 無しは空（フォルダ名では埋めない）")
        check(bare["entry"] == "mod.py", "entry の既定は mod.py")
        check(bare["api"] == ml.DEFAULT_API,
              "api を書いていなければ {} 扱い".format(ml.DEFAULT_API))

        print("=== ローダ API の契約 ===")
        check(ml.api_status({"api": ml.API})[0] is None, "同じ API なら通る")
        check(ml.api_status({"api": ml.API + 1})[0] == "api-too-new",
              "新しすぎる mod は読み込む前に撥ねる")
        check(ml.api_status({"api": ml.MIN_API - 1})[0] == "api-too-old",
              "古すぎる mod も読み込む前に撥ねる")
        # 「読み込む前」が要点。壊れた entry を持つ mod でも API 判定が先に出る。
        put_json("futuremod/mod.json", {"entry": "nope.py", "api": ml.API + 1})
        check(ml.api_status(ml._manifest(tmp_mods, "futuremod"))[0] == "api-too-new",
              "entry が実在しなくても API の判定は先に決まる")

        print("=== 適用順の制約（after / before）===")
        put_json("early/mod.json", {"entry": "m.py", "before": ["late"]})
        put_json("late/mod.json", {"entry": "m.py"})
        put_json("load_order.json", {"order": ["late", "early"]})
        order = order_of(tmp_mods)
        check(order.index("early") < order.index("late"),
              'before に従って並べ替える（load_order は late が先）: {}'.format(order))
        put_json("early/mod.json", {"entry": "m.py", "after": ["late"]})
        order = order_of(tmp_mods)
        check(order.index("late") < order.index("early"),
              "after は逆向きに効く: {}".format(order))
        # 制約に触れない mod の相対順は load_order のまま（利用者の意図を壊さない）。
        # zebra / apple はフォルダ名順とは逆に宣言しておく。
        put_json("load_order.json", {"order": ["late", "zebra", "apple"]})
        base = [n for n in order_of(tmp_mods) if n in ("apple", "zebra")]
        check(base == ["zebra", "apple"],
              "制約に関係ない mod の順は load_order のまま: {}".format(base))
        # 循環しても全滅させない
        put_json("early/mod.json", {"entry": "m.py", "after": ["late"]})
        put_json("late/mod.json", {"entry": "m.py", "after": ["early"]})
        found_now = ml.discover(tmp_mods)
        check(len(found_now["order"]) == len(found_now["installed"]),
              "循環していても mod は全部残る: {}".format(len(found_now["order"])))
        check(any("循環" in p for p in found_now["problems"]),
              "循環は problems に出る: {}".format(found_now["problems"]))
        # 実体の無い相手を指した制約は捨てるが、黙ってはいない
        put_json("late/mod.json", {"entry": "m.py", "after": ["ghost"]})
        found_now = ml.discover(tmp_mods)
        check(any("ghost" in p for p in found_now["problems"]),
              "存在しない mod を指した制約は報告する")

        print("=== 非互換の宣言（conflicts）===")
        put_json("early/mod.json", {"entry": "m.py", "conflicts": ["late"]})
        put_json("late/mod.json", {"entry": "m.py"})
        found_now = ml.discover(tmp_mods)
        check(any("非互換" in p for p in found_now["problems"]),
              "両方有効なら報告する: {}".format(found_now["problems"]))
        check("early" in found_now["order"] and "late" in found_now["order"],
              "報告するが**落とさない**（どちらを外すかはローダが決めない）")
        put_json("load_order.json", {"order": ["zebra", "apple"], "disabled": ["late"]})
        found_now = ml.discover(tmp_mods)
        check(not any("非互換" in p for p in found_now["problems"]),
              "片方が無効なら非互換は報告しない")
        put_json("load_order.json", {"order": ["zebra", "apple"]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=== デバッグモード（開発者向けの MOD を伏せる）===")
    # 実物と同じ配置を丸ごと作る。`settings/` は **runtime の隣**（TECH.md §3.2.4）
    # なので、`runtime/mods` だけの temp では書き出し先がシステムの temp 直下に
    # なってしまう。配布物1つ分を temp に作って、その中で閉じる。
    dist = tempfile.mkdtemp(prefix="instantale_dist_")
    dist_runtime = os.path.join(dist, "runtime")
    dist_mods = os.path.join(dist_runtime, "mods")
    try:
        def put_mod(name, manifest):
            folder = os.path.join(dist_mods, name)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "mod.json"), "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False)
            with open(os.path.join(folder, "m.py"), "w", encoding="utf-8") as fh:
                fh.write("def apply(ctx):\n    pass\n")

        def put_order(obj):
            with open(os.path.join(dist_mods, "load_order.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False)

        put_mod("100_fix", {"entry": "m.py"})
        put_mod("200_probe", {"entry": "m.py", "debug": True})
        # 伏せた相手を名指しする制約。実物では `300_` の
        # `"after": ["205_probe_player_events"]` がこれに当たる。
        put_mod("300_feature", {"entry": "m.py", "after": ["200_probe"]})
        put_order({"order": ["100_fix", "200_probe", "300_feature"]})

        from instantale_modloader import config as C
        check(C.debug_mode(dist_runtime) is False,
              "settings/loader.json が無ければ切（配布物には入っていない）")

        off = ml.discover(dist_mods)
        check(off["debug_mode"] is False, "既定は切")
        check(off["order"] == ["100_fix", "300_feature"],
              "切っている間は order に載らない＝読み込まれない: {}".format(off["order"]))
        check(off["listed"] == ["100_fix", "200_probe", "300_feature"],
              "一覧には**宣言された位置に**残る（保存で記述ごと消えない）: {}"
              .format(off["listed"]))
        check(off["debug"] == {"200_probe"},
              "どれを伏せたかは debug で分かる: {}".format(off["debug"]))
        check(off["manifests"].get("200_probe"),
              "名乗りは残す（GUI が「消えた」ように見せない）")
        # 伏せたのは利用者ではないので、**報告に出してはいけない**。
        # 「無効化されています」「記載の無い MOD」「無効な MOD を指している」の
        # どれで出ても、利用者から見れば身に覚えのない警告になる。
        noisy = [line for line in off["problems"] + off["notes"] if "200_probe" in line]
        check(not noisy, "伏せた MOD は報告に出さない: {}".format(noisy or "出ていない"))
        check("300_feature" in off["order"],
              "伏せた相手を after で指している MOD は、そのまま動く")

        C.save_flags(dist_runtime, {"debug": True})
        check(C.debug_mode(dist_runtime) is True, "loader.json に残る")
        on = ml.discover(dist_mods)
        check(on["debug_mode"] is True and
              on["order"] == ["100_fix", "200_probe", "300_feature"],
              "入れれば宣言どおりの位置で読み込まれる: {}".format(on["order"]))

        C.save_flags(dist_runtime, {"debug": False})
        check(ml.discover(dist_mods)["order"] == ["100_fix", "300_feature"],
              "切れば元に戻る")
        # 静的検査（check_mods.py / この検査自身）が使う逃げ道。利用者が今どちらに
        # 倒しているかで検査の範囲が変わってはいけない。
        check(ml.discover(dist_mods, debug=True)["order"] ==
              ["100_fix", "200_probe", "300_feature"],
              "debug=True は設定を無視して全部見る（静的検査はこちら）")
        check(ml.discover(dist_mods, debug=False)["order"] == ["100_fix", "300_feature"],
              "debug=False も設定を無視する")

        # 印の付いていない MOD は、デバッグモードとは無関係。
        check("100_fix" in ml.discover(dist_mods)["order"],
              "印の無い MOD は切っていても動く")
        # 切る（disabled）のと伏せる（debug）のは別物。切ったものは**必ず見せる**。
        put_order({"order": ["100_fix", "200_probe", "300_feature"],
                   "disabled": ["100_fix"]})
        both = ml.discover(dist_mods)
        check("100_fix" in both["disabled"] and "100_fix" not in both["order"],
              "切ったものは disabled に出る（伏せたものとは別扱い）")
        check(any("100_fix" in line for line in both["problems"] + both["notes"]),
              "切ったことは報告する（利用者が切ったので見せる）")
    finally:
        shutil.rmtree(dist, ignore_errors=True)

    print("=== 設定（mod.json の宣言 + 利用者の選択）===")
    from instantale_modloader import config as C
    decls = C.normalize_decls({
        "MODE": {"type": "choice", "values": ["a", "b"], "default": "a"},
        "COUNT": {"type": "int", "default": 3, "min": 0, "max": 10},
        "RATE": {"type": "float", "default": None, "allow_null": True},
        "ON": {"type": "bool", "default": True},
        "BROKEN": {"type": "nonsense", "default": 1},
        "NOVALUES": {"type": "choice", "default": "x"},
    })
    check(sorted(decls) == ["COUNT", "MODE", "ON", "RATE"],
          "壊れた宣言は落とす（type 不明 / choice に values 無し）: {}".format(sorted(decls)))
    check(C.coerce(decls["COUNT"], "7") == (True, 7, ""),
          "文字列から拾う（GUI の入力欄も手書きの JSON も同じ経路）")
    check(C.coerce(decls["COUNT"], "99")[0] is False, "上限を外れた値は撥ねる")
    check(C.coerce(decls["MODE"], "z")[0] is False, "選択肢の外は撥ねる")
    check(C.coerce(decls["ON"], "false") == (True, False, ""), "真偽値は文字列でも読む")
    check(C.coerce(decls["RATE"], None) == (True, None, ""),
          "allow_null なら null を通す（CHANCE_OVERRIDE の形）")
    check(C.coerce(decls["COUNT"], None)[0] is False, "allow_null でなければ null は撥ねる")

    resolved = C.resolve(decls, {"COUNT": 5, "MODE": "z", "GHOST": 1})
    check(resolved["COUNT"] == 5, "有効な選択は効く")
    check(resolved["MODE"] == "a", "読めない値は既定に倒す（mod を止めない）")
    check("GHOST" not in resolved, "宣言されていない名前は無視する")

    fake_mod = types.ModuleType("fake_settings_mod")
    fake_mod.MODE, fake_mod.COUNT, fake_mod.ON = "a", 3, True
    fake_mod.RATE = None
    effective = C.apply_to_module(fake_mod, decls, {"MODE": "b", "COUNT": "8"})
    check(fake_mod.MODE == "b" and fake_mod.COUNT == 8,
          "モジュールの定数を書き換える（mod のコードに手を入れずに効く）")
    check(effective["ON"] is True, "選ばれていない設定は既定のまま")
    no_const = types.ModuleType("fake_no_const")
    no_const.MODE = "a"
    left = C.apply_to_module(no_const, decls, {})
    check("COUNT" not in left,
          "宣言だけあってコードに無い名前は作らない（打ち間違いを黙って通さない）")

    tmp_store = tempfile.mkdtemp(prefix="instantale_store_")
    try:
        runtime_dir = os.path.join(tmp_store, "runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        check(C.load_store(runtime_dir) == {}, "設定ファイルが無ければ空")
        C.save_store(runtime_dir, {"300_x": {"MODE": "b"}, "301_y": {}})
        check(C.load_store(runtime_dir) == {"300_x": {"MODE": "b"}},
              "空の mod は書かない（既定に戻した記述を溜めない）")
        # 置き場所は配布フォルダ直下の settings/。mods/ の中ではない
        # （mods/ は読む専用で、丸ごと差し替えても利用者の設定が消えないため）。
        check(os.path.isfile(os.path.join(tmp_store, C.SETTINGS_DIR_NAME, C.STORE_NAME)),
              "置き場所は配布フォルダ直下の settings/（mods/ の中ではない）")
    finally:
        shutil.rmtree(tmp_store, ignore_errors=True)

    print("=== safe=True（フックの例外をゲームへ流さない）===")
    P.set_generation("gen_safe")
    calls = []
    victim.risky = lambda x: calls.append(("orig", x)) or ("orig", x)

    @P.wrap("fakegame:risky", safe=True)
    def before_orig(orig, x):
        raise RuntimeError("壊れた（orig を呼ぶ前）")

    check(victim.risky(1) == ("orig", 1),
          "orig を呼ぶ前に壊れたら、素の動作に流す")

    P.set_generation("gen_safe2")

    @P.wrap("fakegame:risky", safe=True)
    def after_orig(orig, x):
        result = orig(x)
        raise RuntimeError("壊れた（orig を呼んだ後）")

    calls.clear()
    check(victim.risky(2) == ("orig", 2), "orig を呼んだ後に壊れたら、その結果を返す")
    check(len(calls) == 1,
          "**orig は1回だけ**（呼び直すと副作用が二重になる）: {}".format(calls))

    P.set_generation("gen_safe3")

    @P.wrap("fakegame:risky")
    def unsafe(orig, x):
        raise RuntimeError("素の wrap は投げる")

    try:
        victim.risky(3)
        raised = False
    except RuntimeError:
        raised = True
    check(raised, "safe を指定しなければ従来どおり例外は外へ出る")

    # patch（丸ごと差し替え）でも落とせること。こちらは元を呼ぶかどうかが
    # 差し替え側の自由なので、失敗したら元の実装に流す。
    P.set_generation("gen_safe_patch")
    victim.plain = lambda x: ("original", x)

    @P.patch("fakegame:plain", safe=True)
    def replaced(x):
        raise RuntimeError("差し替えた側が壊れた")

    check(victim.plain(4) == ("original", 4), "patch でも元の実装に落ちる")

    # メソッドを対象にした場合（self が絡む経路）
    P.set_generation("gen_safe_method")

    class Holder:
        def method(self, x):
            return ("method", x)

    victim.Holder = Holder

    @P.wrap("fakegame:Holder.method", safe=True)
    def broken_method(orig, self, x):
        raise RuntimeError("壊れた")

    check(Holder().method(5) == ("method", 5),
          "メソッド対象でも self を保ったまま元に落ちる")

    # `required=False` で**属性を新設**した patch が safe=True で壊れた場合。
    # 元の実装が無い（old=None）ので、落とす先も無い ― ここで `None(...)` を
    # 呼ぶと TypeError がゲームへ抜けて、safe の約束が破れる（踏む前に塞いだ）。
    P.set_generation("gen_safe_new")

    @P.patch("fakegame:invented", required=False, safe=True)
    def invented():
        raise RuntimeError("新設した側が壊れた")

    try:
        result = victim.invented()
        check(result is None,
              "新設＋safe で壊れても None に落ちる: {!r}".format(result))
    except BaseException as exc:
        check(False, "新設＋safe の例外がゲームへ抜けた: {!r}".format(exc))

    print("=== revert（注入をまたいで剥がせる）===")
    # 前の節に触られていない対象を使う。target_a には gen1 の層が乗っている。
    P.set_generation("gen_revert")
    pristine = victim.target_c = lambda x: ("plain", x)
    # ゲーム側の複製束縛を模す。範囲が GAME_TOPLEVEL なので __main__ に置く
    # （テスト自身が __main__ なので、退避してから差し替える）。
    real_main = sys.modules.get("__main__")
    alias_home = types.ModuleType("__main__")
    alias_home.target_c = pristine
    sys.modules["__main__"] = alias_home
    try:
        @P.wrap("fakegame:target_c")
        def counted(orig, x):
            return ("wrapped",) + orig(x)

        check(victim.target_c(1) == ("wrapped", "plain", 1), "当たっている")
        check(alias_home.target_c is victim.target_c,
              "複製束縛も張り替わっている（from x import y 対策）")
        # 記録は sys に置いてあるので、注入し直してローダが読み直されても残る。
        check("fakegame:target_c" in P.active(),
              "剥がすための記録がある: {}".format(len(P.active())))
        P.revert_all()
        check(victim.target_c is pristine, "属性が素の関数に戻る")
        check(alias_home.target_c is pristine,
              "**複製束縛も戻る**（属性だけ戻しても、そこから呼ばれる経路が生き残る）")
        check(P.active() == [], "記録は使い切られる（前の世代の分もまとめて）")
    finally:
        if real_main is not None:
            sys.modules["__main__"] = real_main
        else:
            sys.modules.pop("__main__", None)

    print("=== エイリアス張り替えの範囲 ===")
    outsider = types.ModuleType("totally_unrelated_lib")
    outsider.target_b = victim.target_b
    sys.modules["totally_unrelated_lib"] = outsider
    P.set_generation("gen_scope")

    @P.wrap("fakegame:target_b")
    def scoped(orig, x):
        return orig(x)

    check(outsider.target_b is not victim.target_b,
          "既定では無関係なライブラリの変数まで張り替えない")
    P.set_generation("gen_scope_all")

    @P.wrap("fakegame:target_b", alias_scan="all")
    def scoped_all(orig, x):
        return orig(x)

    check(outsider.target_b is victim.target_b,
          'alias_scan="all" なら全部なめる（逃げ道は残す）')
    sys.modules.pop("totally_unrelated_lib", None)
    P.revert_all()

    print("=== 同梱 mod は全て名乗っている ===")
    mods_dir = os.path.join(_ROOT, "runtime", "mods")
    survey_result = survey(mods_dir)
    found = survey_result["listed"]
    # 本数は数え上げで確かめる。定数で持つと mod を1本足すたびにここが赤くなり、
    # 「テストを直す」が習慣になってしまう（実際 28 -> 30 と追いかけていた）。
    on_disk = sorted(d for d in os.listdir(mods_dir)
                     if not d.startswith(("_", "."))
                     and os.path.isfile(os.path.join(mods_dir, d, "mod.json")))
    check(sorted(found) == on_disk,
          "mod.json を持つフォルダが全て見つかる（{} 個）".format(len(on_disk)))
    check(all(os.path.isdir(os.path.join(mods_dir, f)) for f in found),
          "見つかるのは全てフォルダ（単一ファイルの mod は残っていない）")

    # 名乗りは mod.json から読む＝**mod のコードを1行も走らせずに**一覧が作れる。
    # GUI が他人の mod を並べるときに import せずに済む、という性質の確認。
    # 表示名の長さは検査しない（以前はここで幅を測っていた）。理由は2つある。
    #
    #   * 一覧の名前列は伸縮する（`gui.py` の COLUMNS は stretch=True）。固定幅で
    #     切り落とされるわけではなく、窓を広げれば見えるし大きさは記憶される
    #   * **測る対象がずれていた。** 行に描くのは mod.json の名前そのものではない
    #     （superseded の MOD には `〔main_024 で本体が取込〕` が付く＝それだけで
    #     旧上限を超える）。名前の側だけ短くしても、守りたかったものは守れない
    #
    # 実際にこの検査は、`121_ui_character_sheet` の正確な名前を弾いて
    # 「検査を通すために名前を悪くする」方向に効いていた。名前は短く・詳細は
    # description へ、という編集方針は残す価値があるが、それは書き方の約束
    # （TECH.md）であって、機械が落とすべきずれではない。
    incomplete, missing_entry = [], []
    for f in found:
        r = ml._manifest(mods_dir, f)
        if not os.path.isfile(os.path.join(mods_dir, f, r["entry"])):
            missing_entry.append(f)
        if not (r["version"] and r["author"] and r["description"]["en"]
                and r["description"]["ja"] and r["name"]["en"] != f):
            incomplete.append(f)
    check(not incomplete, "全 mod が名乗りを持つ: {}".format(incomplete or "欠落なし"))
    check(not missing_entry,
          "全 mod の entry が実在する: {}".format(missing_entry or "欠落なし"))

    # 並びが宣言と一致していること。順序は動作の前提なので、
    # 「順序ファイルに無くて末尾に回っている」状態を見逃さない。
    #
    # 突き合わせる相手は**いま効いている順序ファイル**（`ml.order_path`）。
    # 手元に `load_order.local.json` を置いて未公開の MOD を動かしている間は
    # そちらが宣言なので、`load_order.json` を直に読むとここが必ず赤くなる。
    order_file = ml.order_path(mods_dir)
    with open(order_file, encoding="utf-8") as fh:
        declared = json.load(fh)["order"]
    check(found == declared,
          "並びが {} の宣言どおり".format(os.path.basename(order_file)))

    # 適用順は「宣言の並びから、切られているものを抜いたもの」。切った mod が
    # 落ちること・**残りの順序が入れ替わらないこと**の両方をここで見る
    # （`_sort_dependencies` が `after` / `before` で並べ替えると、宣言と実際の
    # 適用順が食い違う。同梱 mod の宣言はその並びをそのまま固定してある）。
    disabled = set(survey_result["disabled"])
    check(survey_result["order"] == [n for n in declared if n not in disabled],
          "適用順は宣言から無効なものを抜いた並び（切っている: {}）"
          .format(sorted(disabled) or "無し"))

    print("=== superseded（自分より新しい注入が来たか）===")
    # 自前のスレッドと Clock の繰り返しは `revert_all()` では止まらないので、
    # 「降りるべきか」を MOD が自分で判定できる必要がある（§3.6.1）。
    # 判定は2つあり、**片方だけでは足りない**。
    saved_generation = ml._state.get("generation")
    try:
        ml._state["generation"] = "gen-now"
        live = ml.ModContext(out_dir, os.path.join(_ROOT, "runtime"))
        check(live.generation == "gen-now", "ctx は作られた時点の世代を控える")
        check(live.superseded() is False, "同じ世代のあいだは降りない")

        # 1. 同じローダで次の boot が走った（遅延当て直し・再注入）。
        ml._state["generation"] = "gen-next"
        check(live.superseded() is True, "次の boot が来たら降りる")

        # 2. 注入し直してローダごと読み込み直された場合。古い ctx が握って
        #    いる `_state` はもう誰も更新しないので、世代の比較だけでは
        #    永遠に「まだ現役」に見える。**ここが自前の合言葉で抜けやすい**。
        ml._state["generation"] = "gen-now"
        check(live.superseded() is False, "戻せば現役（次の検査の前提）")
        reloaded = types.ModuleType(ml.__name__)
        reloaded._state = {"generation": "gen-now"}   # 別インスタンス＝読み直し後
        saved_module = sys.modules[ml.__name__]
        sys.modules[ml.__name__] = reloaded
        try:
            check(live.superseded() is True,
                  "ローダごと読み直されたら、世代が同じでも降りる")
        finally:
            sys.modules[ml.__name__] = saved_module
    finally:
        if saved_generation is None:
            ml._state.pop("generation", None)
        else:
            ml._state["generation"] = saved_generation

    print("=== 書き込み先（out/ と state/） ===")
    # 役割で場所を分けているので、混ざっていないことと、置き場所を分ける前の
    # `out/` に在るものを1度だけ引き取れることの2つを見る。
    import shutil
    import tempfile
    sandbox = tempfile.mkdtemp(prefix="instantale_state_")
    try:
        s_out = os.path.join(sandbox, "out")
        s_state = os.path.join(sandbox, "state")
        sctx = ml.ModContext(s_out, os.path.join(_ROOT, "runtime"), s_state)

        check(os.path.dirname(sctx.state_path("x.json"))
              != os.path.dirname(sctx.out_path("x.log")),
              "out_path と state_path は別のフォルダを指す")

        # 引っ越し前の姿を作る: 永続データが out/ に在り、state/ には無い。
        legacy = os.path.join(s_out, "legacy.json")
        os.makedirs(s_out, exist_ok=True)
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write('{"kept": 1}')
        moved = sctx.state_path("legacy.json")
        check(os.path.exists(moved) and not os.path.exists(legacy),
              "out/ に在った永続データは state/ へ移す（両方には残さない）")
        with open(moved, encoding="utf-8") as fh:
            check(fh.read() == '{"kept": 1}', "移した中身がそのまま読める")

        # 2回目は何もしない。同じ名前のログが後から out/ にできても、
        # 既に state/ 側が在れば触らない（上書きで巻き戻さないこと）。
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write("newer")
        with open(moved, "w", encoding="utf-8") as fh:
            fh.write('{"kept": 2}')
        again = sctx.state_path("legacy.json")
        with open(again, encoding="utf-8") as fh:
            check(fh.read() == '{"kept": 2}',
                  "state/ に在るものは out/ の同名ファイルで上書きされない")

        # フォルダごとの引き取り（`311_` の `npc_profiles/`）。
        os.makedirs(os.path.join(s_out, "bucket"), exist_ok=True)
        with open(os.path.join(s_out, "bucket", "world.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}")
        adopted = sctx.state_path("bucket")
        check(os.path.isfile(os.path.join(adopted, "world.json"))
              and not os.path.exists(os.path.join(s_out, "bucket")),
              "フォルダも丸ごと引き取る（中のファイルを取り残さない）")

        check(ml.state_dir(os.path.join(_ROOT, "runtime"))
              == os.path.join(_ROOT, ml.STATE_DIR_NAME),
              "既定の state/ は配布フォルダ直下（settings/ と同じ並び）")

        # -- 壊れない書き方 ------------------------------------------------
        # 残すデータは全てここを通す（`ctx.write_json` / `write_text`）。
        # 素朴な open(..., "w") は開いた時点で切り詰めるので、途中で落ちると
        # 中身が消える ― 読む側は壊れた JSON を {} に倒すため、消えたことに
        # 気付けないまま次の更新で上書きされる。
        print("=== 壊れない書き方（write_json / write_text） ===")
        target = sctx.state_path("atomic.json")
        check(sctx.write_json(target, {"a": 1}) is True,
              "書けたら True を返す")
        with open(target, encoding="utf-8") as fh:
            check(json.load(fh) == {"a": 1}, "書いた内容がそのまま読める")

        # 素直に書けない値でも `default=str` が文字列にして通す。**書けないより、
        # 文字列になってでも残る方がよい**という判断なので、ここは成功が正しい。
        check(sctx.write_json(target, {"odd": {1, 2}}) is True,
              "JSON にできない値も文字列にして書く（default=str）")

        # **途中で落ちても本体が無傷**であること。循環参照は `default` でも
        # 救えないので、直列化の時点で失敗して書き込みまで到達しない。
        loop = {}
        loop["self"] = loop
        before = open(target, encoding="utf-8").read()
        check(sctx.write_json(target, loop) is False,
              "書けなければ False を返す（例外を投げない）")
        check(open(target, encoding="utf-8").read() == before,
              "失敗しても元のファイルは無傷（切り詰められていない）")
        check(not os.path.exists(target + ml.TEMP_SUFFIX),
              "書きかけの .tmp を残さない")

        # 置き換えであって追記ではない。短い内容で上書きしたときに前の内容が
        # 尻に残らないこと（`os.replace` なので当然だが、規則として押さえる）。
        sctx.write_json(target, {"long": "x" * 200})
        sctx.write_json(target, {"s": 1})
        with open(target, encoding="utf-8") as fh:
            check(json.load(fh) == {"s": 1}, "短い内容で上書きしても前の内容が残らない")

        # JSON にできない記録（1行1レコード）は write_text 側。
        lines = sctx.state_path("lines.jsonl")
        check(sctx.write_text(lines, '{"a":1}\n{"a":2}\n') is True,
              "write_text も書けたら True")
        with open(lines, encoding="utf-8") as fh:
            check(len(fh.read().splitlines()) == 2, "1行1レコードで書ける")

        # 親フォルダが無くても自分で作る（呼び出し側に makedirs を強いない）。
        nested = os.path.join(s_state, "deep", "er", "n.json")
        check(ml.write_json(nested, {"n": 1}) and os.path.isfile(nested),
              "親フォルダが無ければ作ってから書く")

        # **いちばん危ない窓**: 仮ファイルは書けたのに差し替えで落ちる場合。
        # ここで本体が壊れると、隣に書く意味そのものが無くなる。
        sctx.write_json(target, {"keep": "me"})
        kept = open(target, encoding="utf-8").read()
        saved_replace = ml.os.replace
        ml.os.replace = lambda src, dst: (_ for _ in ()).throw(OSError("差し替え失敗"))
        try:
            check(sctx.write_json(target, {"lost": 1}) is False,
                  "差し替えに失敗したら False")
        finally:
            ml.os.replace = saved_replace
        check(open(target, encoding="utf-8").read() == kept,
              "差し替えに失敗しても本体は前のまま")
        check(not os.path.exists(target + ml.TEMP_SUFFIX),
              "差し替えに失敗しても書きかけを片付ける")

        # -- 読み側（read_json）。「無い」と「在るのに読めない」を区別する --
        fresh = os.path.join(sandbox, "read", "data.json")
        silent = []
        check(ml.read_json(fresh, {"d": 1}, report=silent.append) == {"d": 1},
              "無いファイルは default に倒す")
        check(not silent, "無いだけ（初回）なら記録しない")
        sctx.write_json(fresh, {"x": 1})
        check(sctx.read_json(fresh) == {"x": 1}, "書いたものがそのまま読める")
        with open(fresh, "w", encoding="utf-8") as fh:
            fh.write("{broken")      # 外部要因で壊れた控えを装う
        told = []
        check(ml.read_json(fresh, {}, report=told.append) == {},
              "壊れていても default に倒す（mod は止めない）")
        check(told and "cannot read" in told[0],
              "ただし黙らない ― 消えたことが後から追える")

    finally:
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    if _FAILS:
        print("{} 件失敗: {}".format(len(_FAILS), _FAILS))
        return 1
    print("全て通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
