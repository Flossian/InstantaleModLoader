# -*- coding: utf-8 -*-
"""130_ / 314_ のワールド別設定画面（tool.py）を窓抜きで通す。

    python tools/tests/test_world_settings_tool.py

  同一    … 2本の tool.py は同じファイル（写しがずれていない）。控えのフォルダ名は本体の定数と同じ
  宣言    … 項目は mod.json の "settings" から読む（本体の定数と同じ顔ぶれ）
  世界    … セーブを復号して world_data の名前を鍵にする。フォルダ名ではない
  個別    … 一括設定と違う項目だけ書く。全部同じならファイルを消す。型の違う値は読まない
  一括    … 既定と違う値だけ mod_settings.json に入り、resolve で戻る
  検査    … 上下限・選択肢の外は保存しない
"""
import filecmp
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS = ("130_currency_unit", "314_area_move_custom")
sys.path.insert(0, RUNTIME_DIR)

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def xor(raw, key):
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))


print("[同一]")
paths = [os.path.join(RUNTIME_DIR, "mods", m, "tool.py") for m in MODS]
check("2本の tool.py は同じ", filecmp.cmp(paths[0], paths[1], shallow=False))

tmp = tempfile.mkdtemp(prefix="world_settings_tool_")
try:
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, "runtime"))
    os.environ["IML_ROOT"] = root
    os.environ["IML_STATE_DIR"] = os.path.join(root, "state")
    saves = os.path.join(tmp, "data", "saves")

    for mod, core_name, dirname_const in (
            ("130_currency_unit", "currency_unit.py", "STATE_DIRNAME"),
            ("314_area_move_custom", "area_move_custom.py", "SETTINGS_DIRNAME")):
        print("[{}]".format(mod))
        mod_dir = os.path.join(RUNTIME_DIR, "mods", mod)
        tool = load(mod + "_tool", os.path.join(mod_dir, "tool.py"))
        core = load(mod + "_core", os.path.join(mod_dir, core_name))
        config, state, write_json = tool.loader(os.path.join(root, "runtime"))
        decls = tool.read_decls(config)

        check("控えのフォルダ名が本体と同じ", tool.STATE_DIRNAME == getattr(core, dirname_const),
              (tool.STATE_DIRNAME, getattr(core, dirname_const)))
        check("宣言の項目が本体の定数にある", all(hasattr(core, k) for k in decls), list(decls))
        check("宣言の既定値が本体の定数と同じ",
              all(getattr(core, k) == d["default"] for k, d in decls.items()),
              {k: (getattr(core, k), d["default"]) for k, d in decls.items()})

        # -- 世界の一覧: 復号して world_data の名前。フォルダ名ではない
        shutil.rmtree(saves, ignore_errors=True)
        for folder, name, encoded in (("folder_a", "灰の谷", True), ("folder_b", "", False)):
            os.makedirs(os.path.join(saves, folder))
            body = json.dumps({"world_data": {"name": name}} if name else {"x": 1}).encode("utf-8")
            with io.open(os.path.join(saves, folder, "savedata.json"), "wb") as fh:
                fh.write(xor(body, tool.SAVE_KEY) if encoded else body)
        os.makedirs(os.path.join(saves, "no_save"))
        worlds = tool.list_worlds(os.path.join(tmp, "data"))
        check("世界名は復号したセーブの名前", worlds == [("folder_b", "folder_b"), ("灰の谷", "folder_a")], worlds)

        # -- 一括設定
        shared = tool.load_shared(config, os.path.join(root, "runtime"), decls)
        check("一括設定は最初は既定", shared == {k: d["default"] for k, d in decls.items()}, shared)
        first = next(iter(decls))
        changed = dict(shared)
        changed[first] = "変えた" if decls[first]["type"] == "str" else (
            decls[first]["default"] + 1 if decls[first]["type"] in ("int", "float")
            else not decls[first]["default"] if decls[first]["type"] == "bool"
            else decls[first]["values"][-1])
        tool.save_shared(config, os.path.join(root, "runtime"), decls, changed)
        with io.open(config.store_path(os.path.join(root, "runtime")), encoding="utf-8") as fh:
            stored = json.load(fh)
        check("既定と違う値だけが mod_settings.json に入る", stored == {mod: {first: changed[first]}}, stored)
        check("読み直すと同じ値", tool.load_shared(config, os.path.join(root, "runtime"), decls) == changed)
        tool.save_shared(config, os.path.join(root, "runtime"), decls, shared)
        with io.open(config.store_path(os.path.join(root, "runtime")), encoding="utf-8") as fh:
            check("既定に戻せば項が消える", mod not in json.load(fh))

        # -- ワールド個別設定
        path = tool.world_path(state, os.environ["IML_STATE_DIR"], "灰の谷")
        check("控えのパスは state/<フォルダ>/<世界名>.json",
              path == os.path.join(root, "state", tool.STATE_DIRNAME, "灰の谷.json"), path)
        check("一括設定と同じなら書かない（ファイル無し）",
              tool.save_world(write_json, path, decls, shared, dict(shared)) and not os.path.exists(path))
        check("違う項目だけ書く", tool.save_world(write_json, path, decls, shared, changed))
        with io.open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        check("ファイルの中身は差分だけ", record == {first: changed[first]}, record)
        check("読むと一括設定の上に重なる", tool.load_world(decls, path, shared) == changed)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump({first: [1, 2, 3]}, fh)
        check("型の違う値は一括設定のまま", tool.load_world(decls, path, shared) == shared)
        check("一括設定に戻せばファイルが消える",
              tool.save_world(write_json, path, decls, shared, dict(shared)) and not os.path.exists(path))

        # -- 検査
        for key, decl in decls.items():
            if decl.get("max") is not None:
                raw = dict(shared)
                raw[key] = decl["max"] + 1
                values, bad = tool.coerce_all(config, decls, raw)
                check("上限の外は通さない ({})".format(key), values is None and bad, bad)
                break
        for key, decl in decls.items():
            if decl["type"] == "choice":
                raw = dict(shared)
                raw[key] = "no_such_choice"
                values, bad = tool.coerce_all(config, decls, raw)
                check("選択肢の外は通さない ({})".format(key), values is None and bad, bad)
                break
        values, bad = tool.coerce_all(config, decls, {k: str(v) for k, v in shared.items()})
        check("入力欄の文字列から型どおりに戻る", values == shared, (values, bad))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print("FAILED: {}".format(failures))
    raise SystemExit(1)
print("all ok")
