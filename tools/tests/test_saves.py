# -*- coding: utf-8 -*-
r"""ディスクのセーブの読み方（`instantale_modloader.saves`）を偽のセーブで通す。

    python tools/tests/test_saves.py

  置き場  … `IML_INSTANTALE_DATA` が在ればそちら。無ければ `%LOCALAPPDATA%\Darmabeko\Instantale`
  復号    … 素の JSON → XOR の順（ゲームの `read_json_with_obfuscation_fallback` と同じ向き）
  一覧    … `savedata.json` を持つフォルダだけ。読めないセーブはフォルダ名を世界名にする
  世界名  … `state.world_key_of_dict` を借りる（`world_name` → `name` → `title` の順）
  鍵      … `SAVE_KEY` はここが唯一の在り処。写しを持たない
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

from instantale_modloader import saves                        # noqa: E402

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def put(base, folder, body, obfuscated):
    """偽のセーブを1つ置く。`body` が None ならフォルダだけ作る。"""
    folder_path = os.path.join(base, "saves", folder)
    os.makedirs(folder_path)
    if body is None:
        return
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with io.open(os.path.join(folder_path, "savedata.json"), "wb") as fh:
        fh.write(saves.xor(raw) if obfuscated else raw)


tmp = tempfile.mkdtemp(prefix="saves_")
try:
    print("[置き場]")
    os.environ["IML_INSTANTALE_DATA"] = tmp
    check("環境変数が効く", saves.data_dir() == tmp, saves.data_dir())
    check("override が環境変数より優先", saves.data_dir("X:\\other") == "X:\\other")
    check("saves\\ の下", saves.saves_dir() == os.path.join(tmp, "saves"))
    check("worlds\\ の下", saves.worlds_dir() == os.path.join(tmp, "worlds"))
    check("savedata.json まで",
          saves.save_path("slot1") == os.path.join(tmp, "saves", "slot1", "savedata.json"))

    print("[復号]")
    check("XOR は往復する", saves.xor(saves.xor(b"abc\x00\xff")) == b"abc\x00\xff")
    check("鍵は 24 バイト", len(saves.SAVE_KEY) == 24, len(saves.SAVE_KEY))
    body = {"world_data": {"world_name": "ヴェスティア"}}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    check("素の JSON を読む", saves.decode(raw) == body)
    check("XOR を読む", saves.decode(saves.xor(raw)) == body)
    check("読めないものは None", saves.decode(b"\xff\xfe not json") is None)
    check("JSON だが辞書でないものは None", saves.decode(b"[1, 2]") is None)

    print("[一覧]")
    put(tmp, "slot_xor", {"world_data": {"world_name": "ヴェスティア"}}, True)
    put(tmp, "slot_plain", {"world_data": {"title": "アルカディア"}}, False)
    put(tmp, "slot_noname", {"world_data": {}}, True)
    put(tmp, "slot_broken", None, False)                      # フォルダだけ
    with io.open(os.path.join(tmp, "saves", "slot_broken", "savedata.json"), "wb") as fh:
        fh.write(b"\xff\xfe not json")
    put(tmp, "slot_empty", None, False)                       # savedata.json が無い
    with io.open(os.path.join(tmp, "saves", "not_a_folder.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")

    listed = saves.list_worlds()
    check("savedata.json を持つフォルダだけ",
          listed == ["slot_broken", "slot_noname", "slot_plain", "slot_xor"], listed)
    check("フォルダでないものは入らない", "not_a_folder.txt" not in listed)
    check("savedata.json の無いフォルダは入らない", "slot_empty" not in listed)

    named = dict((folder, name) for name, folder in saves.world_names())
    check("world_name を採る", named.get("slot_xor") == "ヴェスティア", named.get("slot_xor"))
    check("title も採る（name が無いとき）",
          named.get("slot_plain") == "アルカディア", named.get("slot_plain"))
    check("世界名が読めなければフォルダ名",
          named.get("slot_noname") == "slot_noname", named.get("slot_noname"))
    check("壊れたセーブもフォルダ名で並ぶ",
          named.get("slot_broken") == "slot_broken", named.get("slot_broken"))
    check("並びは世界名の順",
          [name for name, _f in saves.world_names()]
          == sorted((name for name, _f in saves.world_names()), key=lambda t: t.casefold()))

    print("[読み]")
    check("XOR のセーブを読む",
          saves.read_save("slot_xor") == {"world_data": {"world_name": "ヴェスティア"}})
    check("素のセーブを読む",
          saves.read_save("slot_plain") == {"world_data": {"title": "アルカディア"}})
    check("無いセーブは None", saves.read_save("nope") is None)
    check("壊れたセーブは None", saves.read_save("slot_broken") is None)

    print("[置き場が無いとき]")
    os.environ["IML_INSTANTALE_DATA"] = os.path.join(tmp, "nowhere")
    check("一覧は空", saves.list_worlds() == [])
    check("世界名も空", saves.world_names() == [])
    check("読みは None", saves.read_save("slot_xor") is None)
finally:
    os.environ.pop("IML_INSTANTALE_DATA", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    print("{} 件 失敗: {}".format(len(failures), ", ".join(failures)))
    sys.exit(1)
print("全て通った")
