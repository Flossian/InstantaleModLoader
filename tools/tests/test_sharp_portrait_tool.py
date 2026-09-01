# -*- coding: utf-8 -*-
"""131_sharp_portrait の道具（tool.py）を窓抜きで通す。

    python tools/tests/test_sharp_portrait_tool.py

  走査    … 世界ごとの NPC を数え、face_image.png の寸法で「顔 / 全身の縮小 / 無し」を分ける
  対象    … 世界で絞れる。既定は全身の縮小（と無し）だけ、全員も選べる
  箱      … 顔の箱を中心に一辺 256。絵の縁で止まる（ゲームの戻り値と同じ形）
  切り直し … **本物の cv2 とゲームのカスケードと手元の絵**で、控えを残して書き、元に戻せる
            （無ければ飛ばす）
  設定    … 既定と違う値だけを mod_settings.json に書き、読み返せる
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
MOD_DIR = os.path.join(ROOT, "runtime", "mods", "131_sharp_portrait")
GAME_DIR = r"C:\Program Files\Epic Games\Instantaleq6Ve7"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load("sharp_portrait_tool_under_test", os.path.join(MOD_DIR, "tool.py"))
faces = tool.faces

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


def png(path, width, height):
    """寸法だけ正しい PNG（中身は読まない検査用）。"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "wb") as fh:
        fh.write(blob)


tmp = tempfile.mkdtemp(prefix="sharp_portrait_tool_test_")
try:
    data = os.path.join(tmp, "data")
    os.environ["IML_INSTANTALE_DATA"] = data
    worlds = os.path.join(data, "worlds")

    print("走査")
    for world, name, face in (("A", "顔あり", (165, 165)), ("A", "外れ", (32, 64)),
                              ("A", "顔なし", None), ("B", "外れ2", (32, 64))):
        folder = os.path.join(worlds, world, "characters", name)
        png(os.path.join(folder, tool.GENERATED), 512, 1024)
        png(os.path.join(folder, tool.STANDING), 330, 660)
        if face:
            png(os.path.join(folder, tool.FACE), *face)
    png(os.path.join(worlds, "A", "characters", "絵なし", tool.FACE), 165, 165)     # generated が無い
    npcs = tool.scan()
    check("generated_image.png のある NPC だけ数える", [n.name for n in npcs] == ["外れ", "顔あり", "顔なし", "外れ2"], [n.name for n in npcs])
    states = {n.name: n.state for n in npcs}
    check("寸法で見分ける", states == {"顔あり": tool.FOUND, "外れ": tool.FALLBACK, "顔なし": tool.MISSING, "外れ2": tool.FALLBACK}, states)
    check("世界ごとの数", tool.summarize(npcs) == {"A": [3, 1, 1, 1, 0], "B": [1, 0, 1, 0, 0]}, tool.summarize(npcs))

    print("対象")
    check("既定は全身の縮小と無しだけ", sorted(n.name for n in tool.targets_of(npcs, [], False)) == ["外れ", "外れ2", "顔なし"])
    check("全員", len(tool.targets_of(npcs, [], True)) == 4)
    check("世界で絞る", [n.name for n in tool.targets_of(npcs, ["B"], True)] == ["外れ2"])

    print("箱")
    check("顔の箱を中心に一辺 256", faces.crop_box((201, 136, 81, 81), (512, 1024)) == (113, 48, 369, 304))
    check("上の縁で止まる", faces.crop_box((200, 10, 80, 80), (512, 1024)) == (112, 0, 368, 256))
    check("右の縁で止まる", faces.crop_box((480, 300, 60, 60), (512, 1024)) == (256, 202, 512, 458))

    print("導入")
    command = tool.pip_command()
    check("この画面の Python に pip で入れる", command[:4] == [sys.executable, "-m", "pip", "install"] and command[-1] == tool.PIP_PACKAGE, command)
    check("4 系に留める", "<5" in tool.PIP_PACKAGE)
    check("--user で入れ直せる", tool.pip_command(user=True)[-1] == "--user")

    print("設定")
    os.environ["IML_ROOT"] = tmp
    os.makedirs(os.path.join(tmp, "settings"), exist_ok=True)
    check("読めなければ既定", tool.load_settings(tmp) == tool.SETTING_DEFAULTS)
    check("保存できる", tool.save_settings(tmp, {"SHARP_PORTRAIT": True, "FACE_RETRY": True}))
    check("既定と違う値だけ残り、読み返せる", tool.load_settings(tmp) == {"SHARP_PORTRAIT": True, "FACE_RETRY": True})

    print("切り直し")
    cv2, np = tool.load_cv2()
    cascades = tool.load_cascades(cv2, GAME_DIR) if cv2 else {}
    real = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Darmabeko", "Instantale", "worlds")
    sample = None
    if cv2 and cascades and os.path.isdir(real):
        # 手元の世界から、顔が見つかっている個体を1体借りる（切り直しが通ることを見る）。
        for world in sorted(os.listdir(real)):
            chars = os.path.join(real, world, "characters")
            if not os.path.isdir(chars):
                continue
            for name in sorted(os.listdir(chars)):
                folder = os.path.join(chars, name)
                if all(os.path.isfile(os.path.join(folder, f)) for f in (tool.GENERATED, tool.NO_BG, tool.STANDING, tool.FACE)):
                    sample = folder
                    break
            if sample:
                break
    if not sample:
        print("  --   cv2 かゲームのカスケードか手元の絵が無いので、切り直しの検査は飛ばす")
    else:
        folder = os.path.join(worlds, "C", "characters", "借りた")
        shutil.copytree(sample, folder)
        # 実機で道具を通した個体は控えを持っている。持ち込むと「書かない」「戻せる」の前提が崩れるので、写しの側だけ消す。
        if os.path.isfile(os.path.join(folder, tool.FACE_BACKUP)):
            os.remove(os.path.join(folder, tool.FACE_BACKUP))
        npc = tool.Npc("C", "借りた", folder)
        before = tool.png_size(npc.path(tool.FACE))
        text, wrote = tool.apply_face(cv2, np, cascades, npc, tool.SOURCE_NO_BG, dry_run=True)
        check("検出だけなら書かない", not wrote and not os.path.isfile(npc.path(tool.FACE_BACKUP)), text)
        text, wrote = tool.apply_face(cv2, np, cascades, npc, tool.SOURCE_NO_BG, dry_run=False)
        if not wrote:
            print("  --   借りた絵で顔が見つからなかった（{}）ので、書く側は飛ばす".format(text))
        else:
            check("控えを残して書く", os.path.isfile(npc.path(tool.FACE_BACKUP)) and tool.png_size(npc.path(tool.FACE_BACKUP)) == before)
            check("元絵から切ると 256x256", tool.png_size(npc.path(tool.FACE)) == (256, 256), tool.png_size(npc.path(tool.FACE)))
            text2, wrote2 = tool.apply_face(cv2, np, cascades, npc, tool.SOURCE_STANDING, dry_run=False)
            check("2度目は控えを上書きしない", tool.png_size(npc.path(tool.FACE_BACKUP)) == before)
            standing = tool.png_size(npc.path(tool.STANDING))
            want = int(round(256 * standing[0] / 512.0))
            check("立ち絵から切ると立ち絵の比に合う（{}）".format(want), tool.png_size(npc.path(tool.FACE))[0] in (want, want + 1), tool.png_size(npc.path(tool.FACE)))
            check("元に戻せる", tool.restore_face(npc) and tool.png_size(npc.path(tool.FACE)) == before and not os.path.isfile(npc.path(tool.FACE_BACKUP)))
            check("控えが無ければ戻さない", tool.restore_face(npc) is False)
finally:
    os.environ.pop("IML_INSTANTALE_DATA", None)
    os.environ.pop("IML_ROOT", None)
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
