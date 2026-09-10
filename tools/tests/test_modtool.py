# -*- coding: utf-8 -*-
"""MOD 同梱の設定画面が共有する土台（`tools/modtool.py`）を偽の配布フォルダで通す。

    python tools/tests/test_modtool.py

6本の `tool.py` に写して回っていたものを1本にまとめた先（TECH.md §3.12）。
写しは既にずれていて、そのうち1つは**最大化した窓の寸法を壊していた**ので、
ここはその再発を捕まえる場所でもある。

  場所    … 環境変数が優先。無ければ MOD の3つ上。game_dir は gui.json の game_path から
  宣言    … 項目と既定値は mod.json の "settings" から。写しを持たない
  設定    … 既定と違う値だけ mod_settings.json に入る。他の MOD の項は触らない
  窓      … 最大化中は normal に戻してから寸法を取る。他の覚えごとを落とさない
  書込    … ローダの write_json（tmp → fsync → replace）。tmp を残さない
  無状態  … 1プロセスで2つの MOD を扱っても混ざらない
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import modtool                                       # noqa: E402

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def gui_json(root):
    with io.open(os.path.join(root, "settings", "gui.json"), encoding="utf-8") as fh:
        return json.load(fh)


DECLS = {
    "COUNT": {"type": "int", "default": 3, "min": 0, "max": 10,
              "label": {"ja": "数", "en": "Count"}, "note": {"ja": "", "en": ""}},
    "LOUD": {"type": "bool", "default": True,
             "label": {"ja": "大きく", "en": "Loud"}, "note": {"ja": "", "en": ""}},
}

tmp = tempfile.mkdtemp(prefix="modtool_")
try:
    # 偽の配布フォルダ。`runtime/instantale_modloader` は本物を指す（symlink は使わない）。
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, "settings"))
    os.makedirs(os.path.join(root, "runtime"))
    mods = os.path.join(root, "runtime", "mods")
    first = os.path.join(mods, "801_first_mod")
    second = os.path.join(mods, "802_second_mod")
    for path, decls, extra in ((first, DECLS, {}),
                               (second, {"COUNT": DECLS["COUNT"]}, {})):
        os.makedirs(path)
        manifest = {"name": {"ja": "偽の MOD", "en": "Fake"},
                    "description": {"ja": "検査用", "en": "for tests"},
                    "settings": decls}
        manifest.update(extra)
        with io.open(os.path.join(path, "mod.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
    with io.open(os.path.join(root, "settings", "gui.json"), "w", encoding="utf-8") as fh:
        json.dump({"game_path": "X:/games/instantale.exe",
                   "window": {"geometry": "1000x700"}}, fh)

    print("[名前と場所]")
    check("MOD の名前はフォルダ名", modtool.mod_name(first) == "801_first_mod")
    check("控えのフォルダ名は番号無し", modtool.state_dirname(first) == "first_mod")
    for key in ("IML_ROOT", "IML_STATE_DIR", "IML_GAME_DIR"):
        os.environ.pop(key, None)
    found_root, found_state, found_game = modtool.locate(first)
    check("root は MOD の3つ上",
          os.path.normcase(found_root) == os.path.normcase(root), found_root)
    check("state は root の下", found_state == os.path.join(root, "state"))
    check("game_dir は gui.json の game_path から",
          found_game == os.path.dirname("X:/games/instantale.exe"), found_game)
    os.environ["IML_ROOT"] = root
    os.environ["IML_STATE_DIR"] = os.path.join(tmp, "elsewhere")
    os.environ["IML_GAME_DIR"] = os.path.join(tmp, "game")
    check("環境変数が優先",
          modtool.locate(first) == (root, os.path.join(tmp, "elsewhere"), os.path.join(tmp, "game")))
    os.environ["IML_STATE_DIR"] = os.path.join(root, "state")
    os.environ.pop("IML_GAME_DIR", None)

    print("[宣言]")
    check("宣言は mod.json から", sorted(modtool.decls(first, root)) == ["COUNT", "LOUD"])
    check("既定値だけ取り出せる", modtool.defaults(first, root) == {"COUNT": 3, "LOUD": True})
    check("mod.json が無ければ空", modtool.decls(os.path.join(mods, "nope"), root) == {})
    check("2つ目の MOD は自分の宣言（混ざらない）",
          modtool.defaults(second, root) == {"COUNT": 3}, modtool.defaults(second, root))

    print("[設定]")
    check("最初は既定", modtool.load_settings(root, first) == {"COUNT": 3, "LOUD": True})
    check("保存できる", modtool.save_settings(root, first, {"COUNT": 7, "LOUD": True}))
    store_path = os.path.join(root, "settings", "mod_settings.json")
    with io.open(store_path, encoding="utf-8") as fh:
        store = json.load(fh)
    check("既定と違う値だけ入る", store == {"801_first_mod": {"COUNT": 7}}, store)
    check("読み返せる", modtool.load_settings(root, first) == {"COUNT": 7, "LOUD": True})
    modtool.save_settings(root, second, {"COUNT": 1})
    with io.open(store_path, encoding="utf-8") as fh:
        store = json.load(fh)
    check("他の MOD の項を触らない",
          store == {"801_first_mod": {"COUNT": 7}, "802_second_mod": {"COUNT": 1}}, store)
    modtool.save_settings(root, first, {"COUNT": 3, "LOUD": True})
    with io.open(store_path, encoding="utf-8") as fh:
        check("既定に戻せば項が消える", "801_first_mod" not in json.load(fh))
    check("上下限の外は既定に倒れる（resolve）",
          modtool.load_settings(root, first) == {"COUNT": 3, "LOUD": True})
    with io.open(store_path, "w", encoding="utf-8") as fh:
        json.dump({"801_first_mod": {"COUNT": 999, "LOUD": "yes"}}, fh)
    check("読めない値は宣言の既定へ",
          modtool.load_settings(root, first) == {"COUNT": 3, "LOUD": True},
          modtool.load_settings(root, first))
    os.remove(store_path)

    print("[入力欄の値を整える]")
    values, bad = modtool.coerce_all(first, {"COUNT": "5", "LOUD": False}, root)
    check("文字列から型どおりに戻る", values == {"COUNT": 5, "LOUD": False} and not bad, (values, bad))
    values, bad = modtool.coerce_all(first, {"COUNT": "11", "LOUD": True}, root)
    check("上限の外は通さない", values is None and bad, bad)

    print("[書き込み]")
    target = os.path.join(tmp, "deep", "written.json")
    check("親フォルダごと作って書ける", modtool.write_json(root, target, {"a": 1}, indent=2))
    with io.open(target, encoding="utf-8") as fh:
        check("読み返せる", json.load(fh) == {"a": 1})
    check("tmp を残さない", not os.path.exists(target + ".tmp"))

    print("[窓の記憶]")
    import tkinter as tk
    window = tk.Tk()
    window.geometry("800x600+50+60")
    window.update_idletasks()
    check("覚えが無ければ空", modtool.load_window(root, first) == {})
    modtool.save_window(root, first, window)
    entry = modtool.load_window(root, first)
    check("素の窓はその寸法で残る",
          entry.get("geometry", "").startswith("800x600") and entry.get("maximized") is False, entry)
    # ---- ドリフトで壊れていた道: 最大化中の geometry() は画面いっぱいの寸法を返す。
    #      そのまま残すと、次に開いて「元に戻す」を押したときの大きさが壊れる。
    window.state("zoomed")
    window.update_idletasks()
    zoomed_geometry = window.geometry()
    modtool.save_window(root, first, window)
    entry = modtool.load_window(root, first)
    check("最大化を覚える", entry.get("maximized") is True, entry)
    check("最大化中でも残るのは元の寸法（画面いっぱいの値ではない）",
          entry.get("geometry", "").startswith("800x600")
          and not entry.get("geometry", "").startswith(zoomed_geometry.split("+")[0]),
          (entry.get("geometry"), zoomed_geometry))
    cfg = gui_json(root)
    check("他の覚えごとを落とさない",
          cfg.get("game_path") == "X:/games/instantale.exe"
          and cfg.get("window") == {"geometry": "1000x700"}, cfg)
    modtool.save_window(root, second, window)
    check("MOD ごとに分かれて残る",
          sorted(gui_json(root)["tool_window"]) == ["801_first_mod", "802_second_mod"],
          sorted(gui_json(root)["tool_window"]))
    window.destroy()

    print("[壊れた gui.json]")
    with io.open(os.path.join(root, "settings", "gui.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    check("読めなければ空を返す（例外にしない）", modtool.load_window(root, first) == {})
    check("locate も倒れない", modtool.locate(first)[0] == root)
finally:
    for key in ("IML_ROOT", "IML_STATE_DIR", "IML_GAME_DIR"):
        os.environ.pop(key, None)
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    print("{} 件 失敗: {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("全て通った")
