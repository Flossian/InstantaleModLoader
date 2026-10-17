# -*- coding: utf-8 -*-
"""`128_item_image_variety` の設定画面（tool.py）と、ワールド個別設定の決まり。

- 画像一覧の分類（`load_categories`。`categories.json`）は本体の `IMAGE_SETS` と同じ並び（足した分類が一覧に出ない、を防ぐ）
- `gallery_sets` は絵のある分類・部位だけを返し、枚数は `list_images` と一致する。部位の表示名が欠けていない
- キャプションは `build/*_captions.txt` から引け、一覧に出る絵の鍵の大半に付く
- ワールド個別の控えの置き場（本体の `SETTINGS_DIRNAME`）は道具の置き場（`modtool.state_dirname`）と同じ
- 世界ごとに上書きできる設定（本体の `SETTING_NAMES`）は mod.json の settings とちょうど同じ
- `mod.json` は `"tool"` を宣言し、道具は窓を組む関数を持つ（窓は開かない）
- ジャンル: `genres.json` のジャンルは本体の `GENRE_SETTINGS` と同じ並びで、全分類に重みの表（0〜1）がある。
  変えた値のファイルは既定に重なり、範囲の外の値と知らないジャンルは捨てる。道具は既定と違う値だけを書く
- イベントの判定の頼みに外見文を英語で頼む一文を足す書き換え（`event_item_note`）は、目印の行の後ろに1回だけ足し、
  目印の無い本文と、もう足してある本文は変えない（None）
"""
import ast
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
MOD = os.path.join(ROOT, "runtime", "mods", "128_item_image_variety")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import modtool  # noqa: E402

spec = importlib.util.spec_from_file_location("item_image_tool", os.path.join(MOD, "tool.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

source = open(os.path.join(MOD, "item_image_variety.py"), encoding="utf-8").read()
body = {node.targets[0].id: node.value for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
image_sets = ast.literal_eval(body["IMAGE_SETS"])
assert [name for name, _ in tool.load_categories(MOD)] == [name for name, _ in image_sets], "画像一覧の分類が本体と違う"

sets = tool.gallery_sets(MOD)
assert {name for name, _label, _f in sets} == {name for name, _ in image_sets}, "絵の無い分類がある"
known = {f for f, _ in tool.FOLDER_LABELS}
captions = tool.load_captions(MOD)
total = with_caption = 0
for name, _label, folders in sets:
    for folder, _flabel, count in folders:
        assert folder in known, (name, folder)
        items = tool.list_images(MOD, name, folder)
        assert len(items) == count and count > 0, (name, folder)
        total += count
        with_caption += sum(1 for key, _p in items if key in captions)
        for key, path in items[:3]:
            assert os.path.isfile(path), path
assert with_caption >= 0.95 * total, ("キャプションの無い絵が多い", with_caption, total)

assert ast.literal_eval(body["SETTINGS_DIRNAME"]) == modtool.state_dirname(MOD)
manifest = json.load(open(os.path.join(MOD, "mod.json"), encoding="utf-8"))
genre_settings = ast.literal_eval(body["GENRE_SETTINGS"])
names = (("TOP_K", "SIM_FLOOR", "USE_EXTRA_DICT") + tuple(setting for _g, setting in genre_settings) + ("GENRE_BONUS",)
         + tuple(setting for _n, setting in image_sets))
assert list(manifest["settings"]) == list(names), "SETTING_NAMES と mod.json の settings が違う"
assert manifest["tool"]["entry"] == "tool.py"
assert callable(tool.build_window) and callable(tool.gallery_tab) and callable(tool.genre_tab)
for _g, setting in genre_settings:
    assert manifest["settings"][setting]["default"] == 0.0, setting   # 既定は素の選び方

sys.path.insert(0, os.path.join(ROOT, "runtime"))
spec = importlib.util.spec_from_file_location("item_image_variety_t", os.path.join(MOD, "item_image_variety.py"))
body_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(body_mod)
mark, note = body_mod.EVENT_ITEM_MARK, body_mod.EVENT_ITEM_NOTE
prompt = "【効果】\n" + mark + "\n- heal: 回復する。"
got = body_mod.event_item_note(["system", prompt])
assert got == ["system", prompt.replace(mark, mark + note)], got
assert body_mod.event_item_note(got) is None, "二重に足している"
assert body_mod.event_item_note(["目印の無い本文"]) is None
assert "英語" in note and "item_appearance" in note


import tempfile
genres = json.load(open(os.path.join(MOD, "genres.json"), encoding="utf-8"))
assert [g["id"] for g in genres["genres"]] == [g for g, _s in genre_settings]
assert set(genres["weights"]) == {name for name, _ in image_sets}, "genres.json の分類が足りない"
for cat, table in genres["weights"].items():
    assert all(g in dict(genre_settings) and 0.0 <= w <= 1.0 for g, w in table.items()), cat
with tempfile.TemporaryDirectory() as tmp:
    user = os.path.join(tmp, "weights.json")
    json.dump({"stock": {"fantasy": 0.5}, "weights": {"wa_swords": {"wa": 0.2, "nope": 1, "sf": 3}, "zzz": {"wa": 1}}},
               open(user, "w", encoding="utf-8"))
    stock, weights = body_mod.read_genre_weights(MOD, user)
    assert stock["fantasy"] == 0.5
    assert weights["wa_swords"]["wa"] == 0.2 and weights["wa_swords"]["sf"] == 1.0 and "nope" not in weights["wa_swords"]
    assert "zzz" not in weights
    names_g, base_stock, base_weights = tool.genre_defaults()
    assert tool.genre_diff(base_stock, base_weights, base_stock, base_weights) == {}
    assert tool.genre_diff(stock, weights, base_stock, base_weights) == {
        "stock": {"fantasy": 0.5}, "weights": {"wa_swords": {"wa": 0.2, "sf": 1.0}}}

print("ok")
