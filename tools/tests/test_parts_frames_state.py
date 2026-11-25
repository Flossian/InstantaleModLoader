# -*- coding: utf-8 -*-
"""ローダの汎用の部品（frames / state / guards）を通す。ゲーム不要。

    python tools/tests/test_parts_frames_state.py

見ているもの:

  frames … field / set_field / is_number / to_number / short(collapse=) / safe_format
  state  … ordered / process_store / WorldSettings
  guards … is_number が frames.is_number と同じ答えのまま

既存の関数（short・guards.is_number・ui.fill_template）は、前の本体の写しと答えを突き合わせる。
"""

import json
import math
import os
import shutil
import sys
import tempfile
import threading
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import frames                # noqa: E402
from instantale_modloader import guards                # noqa: E402
from instantale_modloader import state as st           # noqa: E402
from instantale_modloader import ui                    # noqa: E402

_FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        _FAILS.append(msg)


# ---------------------------------------------------------------- 前の本体の写し（答えの突き合わせ用）
def _old_short(value, limit=200):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if limit is None or len(value) <= limit else value[:limit] + "…"


def _old_is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _OldKeep(dict):
    def __missing__(self, key):
        return "{" + str(key) + "}"


def _old_fmt(template, **values):
    try:
        return str(template).format_map(_OldKeep(values))
    except Exception:
        return str(template)


def _same(a, b):
    """nan どうしも同じとみなす比べ方。型も比べる。"""
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and math.isnan(a):
        return math.isnan(b)
    return a == b


# ---------------------------------------------------------------- frames
class _Holder(object):
    def __init__(self):
        self.name = "灰"
        self.empty = None
        self.trap = frames.MISSING

    @property
    def broken(self):
        raise RuntimeError("boom")

    @property
    def readonly(self):
        return 1


class _Slots(object):
    __slots__ = ("a",)


def test_field():
    print("=== frames.field / set_field ===")
    holder = _Holder()
    check(frames.field({"a": 1}, "a") == 1, "辞書は get で読む")
    check(frames.field({"a": 1}, "b", 7) == 7, "辞書に無い鍵は default")
    check(frames.field({"a": None}, "a", 7) is None, "辞書の None はそのまま（default にしない）")
    check(frames.field(holder, "name") == "灰", "属性を読む")
    check(frames.field(holder, "nothing", "d") == "d", "無い属性は default")
    check(frames.field(holder, "empty", "d") is None, "None の属性はそのまま")
    check(frames.field(holder, "broken", "d") == "d", "property が投げたら default（番人を返さない）")
    check(frames.field(holder, "trap", "d") == "d", "MISSING の番人は default")
    check(frames.field(None, "a", 3) == 3, "None からは default")
    check(frames.field(holder, "broken") is None, "default の既定は None")

    data = {}
    check(frames.set_field(data, "a", 1) is True and data == {"a": 1}, "辞書に書く")
    check(frames.set_field(holder, "name", "炎") is True and holder.name == "炎", "属性に書く")
    check(frames.set_field(holder, "readonly", 2) is False, "書けない property は False")
    check(frames.set_field(_Slots(), "b", 2) is False, "__slots__ に無い名前は False")
    check(frames.set_field(None, "a", 1) is False, "None には書けない")


_NUMBER_CASES = [0, 1, -3, 2.5, float("nan"), float("inf"), float("-inf"), True, False, None,
                 "3", "", [1], {"a": 1}, 10 ** 30]


def test_is_number():
    print("=== frames.is_number / guards.is_number ===")
    for value in _NUMBER_CASES:
        old = _old_is_number(value)
        check(frames.is_number(value) is old, "frames.is_number({!r}) は前と同じ {}".format(value, old))
        check(guards.is_number(value) is old, "guards.is_number({!r}) は前と同じ {}".format(value, old))


def test_to_number():
    print("=== frames.to_number ===")
    cases = [
        (3, None, 3), (2.5, None, 2.5), (True, None, None), (False, 0, 0), (None, -1, -1),
        ("3", None, 3), (" 3 ", None, 3), ("3.0", None, 3), ("+4", None, 4), ("-2.5", None, -2.5),
        ("1e3", None, 1000), ("abc", "x", "x"), ("", None, None), ([1], None, None),
        (b"3", None, None),
    ]
    for value, default, want in cases:
        got = frames.to_number(value, default)
        check(_same(got, want), "to_number({!r}, {!r}) -> {!r}（得た {!r}）".format(value, default, want, got))
    check(type(frames.to_number(3.0)) is float, "float の 3.0 は float のまま")
    check(math.isnan(frames.to_number(float("nan"))), "nan はそのまま")
    check(frames.to_number("inf") == float("inf"), "文字列の inf も読む（is_number と同じ扱い）")
    check(type(frames.to_number("7")) is int, "整数の文字列は int")


def test_short():
    print("=== frames.short（collapse） ===")
    cases = [None, "", "  灰の街  ", "a\nb\tc", 12, 0, False, "x" * 250, "  " + "y" * 10 + "  "]
    for value in cases:
        for limit in (200, 5, None):
            check(frames.short(value, limit) == _old_short(value, limit),
                  "既定は前と同じ: short({!r}, {})".format(value if not isinstance(value, str) else value[:12], limit))
    check(frames.short(" a \n\n b\t c ", collapse=True) == "a b c", "空白・改行・タブを1つに畳む")
    check(frames.short("a　　b", collapse=True) == "a b", "全角の空白も畳む")
    check(frames.short("ab  cd  ef", 5, collapse=True) == "ab cd…", "畳んでから切り、… を足す")
    check(frames.short(None, collapse=True) == "", "None は空文字")
    check(frames.short("a b", 3, collapse=True) == "a b", "収まるなら … を付けない")


def test_safe_format():
    print("=== frames.safe_format ===")
    cases = [
        ("{name}は{age}歳", {"name": "灰", "age": 3}),
        ("{name}と{typo}", {"name": "灰"}),
        ("{", {}),
        ("{0}", {}),
        ("{name:>4}", {"name": "a"}),
        ("{name.x}", {"name": "a"}),
        (None, {}),
        (12, {}),
    ]
    for template, values in cases:
        got = frames.safe_format(template, **values)
        check(got == _old_fmt(template, **values), "写しと同じ: {!r} -> {!r}".format(template, got))
    check(frames.safe_format("{a}-{b}", a=1) == "1-{b}", "知らない名前は {名前} のまま")
    # 通貨の文字を含まない書式なら ui.fill_template と同じ答え（あちらは通貨の表記の書き換えだけが多い）
    for template, values in cases[:6]:
        if isinstance(template, str):
            check(frames.safe_format(template, **values) == ui.fill_template(template, **values),
                  "ui.fill_template と同じ: {!r}".format(template))


# ---------------------------------------------------------------- state
def test_ordered():
    print("=== state.ordered ===")
    record = {"z": 1, "b": 2, "a": 3, "extra": 4}
    got = st.ordered(record, ("a", "b", "missing"))
    check(list(got) == ["a", "b", "z", "extra"], "keys の順、知らない鍵は元の順で後ろ")
    check("missing" not in got, "無い鍵は足さない")
    check(got == record and got is not record, "中身は同じで別の辞書")
    check(st.ordered(None, ("a",)) == {} and st.ordered([1], ("a",)) == {}, "辞書でなければ {}")
    inner = {"x": 1}
    check(st.ordered({"a": inner}, ("a",))["a"] is inner, "値は写さない")


_ATTR = "_instantale_test_parts_process_store"


def test_process_store():
    print("=== state.process_store ===")
    try:
        delattr(sys, _ATTR)
    except AttributeError:
        pass
    defaults = {"logged": 0, "seen": set(), "rows": [], "lock": threading.Lock(), "installed": None}
    store = st.process_store(_ATTR, defaults)
    check(getattr(sys, _ATTR) is store, "sys の属性に置く")
    check(store["seen"] == set() and store["seen"] is not defaults["seen"], "入れ物は写してから置く")
    check(store["rows"] is not defaults["rows"], "list も写す")
    check(store["lock"] is defaults["lock"], "錠はそのまま置く")
    store["logged"] = 5
    store["seen"].add("a")
    store["installed"] = "line"
    again = st.process_store(_ATTR, defaults)
    check(again is store and again["logged"] == 5 and again["seen"] == {"a"}, "在れば数と控えを残す")
    check(again["installed"] == "line", "既定が None の鍵は値が入っていれば残す")
    check(defaults["seen"] == set(), "defaults の入れ物は汚れない")

    # 前の版が置いた入れ物（鍵が足りない・型が変わった）
    setattr(sys, _ATTR, {"logged": 2, "seen": False, "old": 1})
    store = st.process_store(_ATTR, defaults)
    check(store["logged"] == 2, "型の同じ鍵は残す")
    check(store["seen"] == set(), "型の変わった鍵は既定に戻す（129 の手順）")
    check(store["rows"] == [] and "lock" in store and store["installed"] is None, "足りない鍵を足す")
    check(store["old"] == 1, "知らない鍵は残す")
    check(st.process_store(_ATTR, {"logged": 0})["logged"] == 2, "bool でない int は残る")

    setattr(sys, _ATTR, ["not", "a", "dict"])
    store = st.process_store(_ATTR, {"n": 0})
    check(store == {"n": 0}, "辞書でなければ作り直す")

    # 129 の手順との突き合わせ
    blanks = {"logged": 0, "reconciled": 0, "skipped": 0, "settled": set(), "skip_seen": set()}
    for before in ({"logged": 3, "settled": False}, {"logged": "x", "skip_seen": {"a"}}, {}):
        old = dict(before)
        for name, blank in blanks.items():
            if name not in old or not isinstance(old[name], type(blank)):
                old[name] = blank
        setattr(sys, _ATTR, dict(before))
        got = st.process_store(_ATTR, blanks)
        check(got == old, "129 と同じ答え: {!r}".format(before))

    # 注入し直しで作り直されたクラス（同じモジュール名・同じ名前）の実体は残す
    first = types.new_class("Box", (object,))
    first.__module__ = "instantale_mod_example"
    second = types.new_class("Box", (object,))
    second.__module__ = "instantale_mod_example"
    old_box = first()
    setattr(sys, _ATTR, {"box": old_box})
    check(st.process_store(_ATTR, {"box": second()})["box"] is old_box, "名前の同じクラスの実体は残す")
    other = types.new_class("Other", (object,))
    other.__module__ = "instantale_mod_example"
    check(st.process_store(_ATTR, {"box": other()})["box"] is not old_box, "名前の違うクラスなら入れ替える")
    delattr(sys, _ATTR)


class _App(object):
    def __init__(self, world):
        self.world_dict = {"world_data": {"world_name": world}} if world else {}


def _write_record(state_dir, dirname, world, record):
    folder = os.path.join(state_dir, dirname)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, st.world_filename(world)), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False)


def test_world_settings():
    print("=== state.WorldSettings ===")
    sandbox = tempfile.mkdtemp(prefix="instantale_parts_")
    try:
        state_dir = os.path.join(sandbox, "state")
        ctx = ml.ModContext(os.path.join(sandbox, "out"), os.path.join(_ROOT, "runtime"), state_dir)
        lines = []
        module = {"ENABLED": True, "PRICE": 100, "RATE": 0.5, "NAME": "G", "OTHER": 1}
        names = ("ENABLED", "PRICE", "RATE", "NAME")
        settings = st.WorldSettings(ctx, "parts_test", names, module, write=lines.append)

        app = _App("灰の街")
        check(settings.values(app) == {"ENABLED": True, "PRICE": 100, "RATE": 0.5, "NAME": "G"},
              "控えが無ければ一括設定")
        check(not os.path.exists(os.path.join(state_dir, "parts_test")), "読むだけではフォルダを作らない")
        check(settings.values(None)["PRICE"] == 100, "app が None なら一括設定")

        _write_record(state_dir, "parts_test", "灰の街", {"PRICE": 250, "NAME": "円", "UNKNOWN": 1})
        check(settings.values(app)["PRICE"] == 250 and settings.values(app)["NAME"] == "円",
              "控えの項目を重ねる")
        check(settings.values(_App("別の街"))["PRICE"] == 100, "控えの無い世界は一括設定")
        check(settings.value(app, "PRICE") == 250 and settings.value(app, "OTHER", "d") == "d",
              "value は1項目。names に無い名前は default")
        check(settings.overrides(app) == {"PRICE": 250, "NAME": "円"}, "overrides は違う項目だけ")

        # 型の照合
        _write_record(state_dir, "parts_test", "灰の街",
                      {"ENABLED": 1, "PRICE": 300.0, "RATE": 2, "NAME": None})
        values = settings.values(app)
        check(values["ENABLED"] is True, "bool の設定に 1 は使わない")
        check(values["PRICE"] == 300 and type(values["PRICE"]) is int, "int の設定に 300.0 は int にして使う")
        check(values["RATE"] == 2.0 and type(values["RATE"]) is float, "float の設定に 2 は float にして使う")
        check(values["NAME"] == "G", "null は一括設定のまま")
        check(sum("ENABLED" in line for line in lines) == 1, "捨てた値を1度書く")
        settings.values(app)
        check(sum("ENABLED" in line for line in lines) == 1, "同じ値は2度書かない")
        _write_record(state_dir, "parts_test", "灰の街", {"PRICE": 12.5, "ENABLED": "yes"})
        values = settings.values(app)
        check(values["PRICE"] == 100 and values["ENABLED"] is True, "整数でない float と文字列の真偽は使わない")
        check(st.WorldSettings.accept(float("nan"), 1)[0] is False, "int の設定に nan は使わない")
        check(st.WorldSettings.accept(float("inf"), 1)[0] is False, "int の設定に inf は使わない")
        check(st.WorldSettings.accept(True, 1)[0] is False, "int の設定に True は使わない")

        # refresh
        target = dict(module)
        fresh = st.WorldSettings(ctx, "parts_test", names, module)
        _write_record(state_dir, "parts_test", "灰の街", {"PRICE": 500})
        check(fresh.refresh(app, target) is True and target["PRICE"] == 500, "初回は反映して True")
        check(fresh.refresh(app, target) is False, "変わっていなければ False")
        _write_record(state_dir, "parts_test", "灰の街", {"PRICE": 6000})
        check(fresh.refresh(app, target) is True and target["PRICE"] == 6000, "ファイルが変われば読み直して True")
        check(fresh.refresh(_App("別の街"), target) is True and target["PRICE"] == 100,
              "控えの無い世界へ移れば一括設定に戻して True")
        check(target["OTHER"] == 1, "names に無い定数は触らない")
        check(fresh.refresh(_App("三つ目の街"), target) is True and target["PRICE"] == 100,
              "世界が替われば値が同じでも True（314・332 の署名）")
        got = []
        check(fresh.refresh(app, got.append) is True and got and got[0]["PRICE"] == 6000,
              "呼べる物にも渡せる")
        calls = []

        def broken(values):
            calls.append(values)
            if len(calls) == 1:
                raise RuntimeError("boom")

        other = st.WorldSettings(ctx, "parts_test", names, module)
        check(other.refresh(app, broken) is False, "反映が投げたら False")
        check(other.refresh(app, broken) is True and len(calls) == 2, "次の呼び出しで反映し直す")

        # follow_file=False は世界が変わったときだけ読む
        lazy = st.WorldSettings(ctx, "parts_test", names, module, follow_file=False)
        lazy_target = dict(module)
        check(lazy.refresh(app, lazy_target) is True and lazy_target["PRICE"] == 6000, "初回は読む")
        _write_record(state_dir, "parts_test", "灰の街", {"PRICE": 70})
        check(lazy.refresh(app, lazy_target) is False and lazy_target["PRICE"] == 6000,
              "同じ世界ではファイルが変わっても読まない")
        check(lazy.values(app)["PRICE"] == 6000, "values も覚えた値")
        lazy.refresh(_App("別の街"), lazy_target)
        check(lazy.refresh(app, lazy_target) is True and lazy_target["PRICE"] == 70, "世界が戻れば読み直す")
        lazy.forget()
        check(lazy.refresh(app, lazy_target) is True, "forget の後は必ず反映し直す")

        # aliases と check
        _write_record(state_dir, "parts_test", "灰の街", {"OLD_PRICE": 42, "NAME": 5})
        aliased = st.WorldSettings(ctx, "parts_test", names, module, aliases={"OLD_PRICE": "PRICE"},
                                   check=lambda name, value, base: name != "NAME" or isinstance(value, int))
        values = aliased.values(app)
        check(values["PRICE"] == 42, "旧い名前の値を新しい名前で読む")
        check(values["NAME"] == 5, "check が真なら既定の照合を使わない")
        _write_record(state_dir, "parts_test", "灰の街", {"OLD_PRICE": 42, "PRICE": 43})
        check(aliased.values(app)["PRICE"] == 43, "新しい名前が先")

        lines[:] = []
        st.WorldSettings(ctx, "parts_test", ("PRICE", "NOPE"), module, write=lines.append)
        check(any("NOPE" in line for line in lines), "一括設定に無い名前は記録して使わない")
        _write_record(state_dir, "parts_test", "灰の街", {"PRICE": 1})
        snap = {"PRICE": 100}
        overlay = st.WorldSettings(ctx, "parts_test", ("PRICE",), snap)
        overlay.refresh(app, snap)
        check(snap["PRICE"] == 1 and overlay.base["PRICE"] == 100, "base は作ったときの写し（反映で汚れない）")
        overlay.refresh(_App("別の街"), snap)
        check(snap["PRICE"] == 100, "控えの無い世界で一括設定に戻る")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def main():
    test_field()
    test_is_number()
    test_to_number()
    test_short()
    test_safe_format()
    test_ordered()
    test_process_store()
    test_world_settings()
    print()
    if _FAILS:
        print("{} 件失敗: {}".format(len(_FAILS), _FAILS))
        return 1
    print("全て通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
