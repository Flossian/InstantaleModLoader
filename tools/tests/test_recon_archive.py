# -*- coding: utf-8 -*-
"""リコンの退避（`out/recon/` を上書きする前に zip で残す）を通す。ゲーム不要。

    python tools/tests/test_recon_archive.py

見ているのは3つ。

  引き金   … 走るのは**ビルドが変わったときだけ**。同じ版では走らない
  名前     … `<版>_<退避するダンプを取った日>`。今日の日付ではない
  中身     … 5つの成果物と `build.json` が全部入っていて、上書き前の内容である

引き金の検査が厚いのは、ここを間違えた側の壊れ方が正反対だから ― 緩すぎると同じ版の zip が毎回増えて肝心の1回が埋もれ、
厳しすぎると**更新の瞬間に前の版が黙って消える**（この仕組みを作った理由そのもの。
GAME.md §1.5）。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

from instantale_modloader import recon                 # noqa: E402

_FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        _FAILS.append(msg)


def _sandbox():
    return tempfile.mkdtemp(prefix="recon_archive_")


def _plant(recon_dir, marker, build=None):
    """前回のダンプが在ることにする。`marker` が中身の見分けになる。"""
    os.makedirs(recon_dir, exist_ok=True)
    for name in recon.OUTPUT_FILES:
        with io.open(os.path.join(recon_dir, name), "w", encoding="utf-8") as fh:
            fh.write(marker + "\n")
    path = os.path.join(recon_dir, recon.BUILD_NAME)
    if build is None:
        # 控えの無い（この仕組みが入る前の）ダンプを作る。
        if os.path.exists(path):
            os.remove(path)
        return
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(build, ensure_ascii=False))


def _zips(snap_dir):
    if not os.path.isdir(snap_dir):
        return []
    return sorted(n for n in os.listdir(snap_dir) if n.endswith(".zip"))


# --------------------------------------------------------------------------
def test_same_build():
    print("=== _same_build: 同じビルドを見たダンプか ===")
    base = {"app_version": "main_025", "game_version": "014", "exe_size": 716076544}

    check(recon._same_build(dict(base), dict(base)),
          "3項目が揃っていれば同じビルド")
    check(not recon._same_build({}, dict(base)),
          "控えが無ければ「同じ」ではない（確かめられないだけ）")

    # mtime は起動しただけで動くことがあるので、比較には入れていない。
    # 入れると更新していないのに毎回退避が走る。
    with_mtime = dict(base, exe_mtime="2026-08-09T13:52:29")
    check(recon._same_build(with_mtime, dict(base, exe_mtime="2026-08-09T18:00:00")),
          "exe_mtime が動いても同じビルドと見る")

    for key, value in (("app_version", "main_026"), ("game_version", "015"),
                       ("exe_size", 700000000)):
        check(not recon._same_build(dict(base), dict(base, **{key: value})),
              "{} が変われば別のビルド".format(key))
    check(not recon._same_build(dict(base), dict(base, app_version=None)),
          "版が読めなくなった場合も別のビルド扱い（消さない側に倒す）")


def test_snapshot_name():
    print("=== _snapshot_name: 退避の名前 ===")
    sandbox = _sandbox()
    try:
        recon_dir = os.path.join(sandbox, "recon")
        _plant(recon_dir, "old")

        name = recon._snapshot_name(recon_dir, {
            "app_version": "main_024", "written": "2026-08-05T16:44:53"})
        check(name == "main_024_20260805",
              "日付は退避するダンプを取った日（今日ではない）")

        name = recon._snapshot_name(recon_dir, {"game_version": "014",
                                                "written": "2026-08-05T16:44:53"})
        check(name == "014_20260805", "Epic の版が無ければゲーム側の版を使う")

        name = recon._snapshot_name(recon_dir, {})
        check(name.startswith("unknown_"),
              "版が読めなければ unknown（当て推量の版を付けない）")
        check(len(name) == len("unknown_20260809") and name[-8:].isdigit(),
              "控えが無くてもファイルの更新時刻から日付は入る")

        name = recon._snapshot_name(recon_dir, {"app_version": "main 025/beta",
                                                "written": "2026-08-09T00:00:00"})
        check("/" not in name and " " not in name,
              "版に区切りや空白が混ざってもファイル名にできる形に均す")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_archive_previous():
    print("=== archive_previous: 退避が走る条件 ===")
    sandbox = _sandbox()
    try:
        recon_dir = os.path.join(sandbox, "recon")
        snap_dir = os.path.join(sandbox, recon.SNAPSHOT_DIR_NAME)
        old = {"app_version": "main_024", "game_version": "014",
               "exe_size": 700000000, "written": "2026-08-05T16:44:53"}
        new = {"app_version": "main_025", "game_version": "014",
               "exe_size": 716076544}

        # 初回。
        # out/recon/ が空なら残すものが無い。
        os.makedirs(recon_dir, exist_ok=True)
        check(recon.archive_previous(recon_dir, dict(new)) is None,
              "初回（成果物が1つも無い）は退避しない")
        check(_zips(snap_dir) == [], "  zip も作らない")

        # 同じビルド。
        # 何度走らせても増えない。
        _plant(recon_dir, "main_025 の1回目", build=dict(new, written="2026-08-09T13:53:21"))
        check(recon.archive_previous(recon_dir, dict(new)) is None,
              "同じビルドなら退避しない（同じ版の zip が毎回増えない）")
        check(_zips(snap_dir) == [], "  zip も作らない")

        # ビルドが変わった。
        # ここが本命。
        _plant(recon_dir, "main_024 の最後の1回", build=old)
        path = recon.archive_previous(recon_dir, dict(new))
        check(path is not None and os.path.isfile(path), "ビルドが変われば退避する")
        check(_zips(snap_dir) == ["main_024_20260805.zip"],
              "  名前は <前の版>_<そのダンプを取った日>.zip")

        with zipfile.ZipFile(path) as archive:
            names = sorted(archive.namelist())
            expected = sorted(list(recon.OUTPUT_FILES) + [recon.BUILD_NAME])
            check(names == expected, "  成果物5つと build.json が入っている")
            check(archive.read("targets.txt").decode("utf-8").strip()
                  == "main_024 の最後の1回",
                  "  中身は上書きされる前の（＝前の版の）ダンプ")
            check(json.loads(archive.read(recon.BUILD_NAME).decode("utf-8"))["app_version"]
                  == "main_024",
                  "  zip 自身に「どの版のものか」が入っている")

        # 同じ版・同じ日にもう一度。
        # 滅多に無いが、黙って上書きしてはいけない。
        _plant(recon_dir, "main_024 のもう1回", build=old)
        recon.archive_previous(recon_dir, dict(new))
        check(_zips(snap_dir) == ["main_024_20260805.zip", "main_024_20260805_2.zip"],
              "名前が埋まっていれば _2 を足す（先の退避を消さない）")

        # 控えの無い（この仕組みが入る前の）ダンプ。
        # 確かめられない＝残す。
        shutil.rmtree(snap_dir, ignore_errors=True)
        _plant(recon_dir, "build.json の無い頃のダンプ", build=None)
        path = recon.archive_previous(recon_dir, dict(new))
        check(path is not None, "build.json が無いダンプも退避する（確かめられないので残す）")
        with zipfile.ZipFile(path) as archive:
            check(recon.BUILD_NAME not in archive.namelist(),
                  "  無い build.json を詰めようとはしない")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_dump_end_to_end():
    print("=== dump: 書き出しと退避のつながり ===")
    sandbox = _sandbox()
    try:
        recon_dir = recon.dump(sandbox)
        snap_dir = os.path.join(sandbox, recon.SNAPSHOT_DIR_NAME)
        missing = [n for n in list(recon.OUTPUT_FILES) + [recon.BUILD_NAME]
                   if not os.path.isfile(os.path.join(recon_dir, n))]
        check(not missing, "成果物一式と build.json が揃う（欠け: {}）".format(missing))

        record = json.load(io.open(os.path.join(recon_dir, recon.BUILD_NAME),
                                   encoding="utf-8"))
        check(set(recon.OUTPUT_FILES) <= set(record.get("files") or {}),
              "build.json に各ファイルの sha256 が入る")
        check(record.get("written", "")[:2] == "20", "いつ書いたかが入る")

        # 2回目。
        # 同じビルドなので退避は走らない。
        recon.dump(sandbox)
        check(_zips(snap_dir) == [], "同じビルドで走らせ直しても zip は増えない")

        # ゲームが更新されたことにする。
        path = os.path.join(recon_dir, recon.BUILD_NAME)
        record = json.load(io.open(path, encoding="utf-8"))
        record["app_version"] = "main_000"
        record["written"] = "2026-08-05T16:44:53"
        io.open(path, "w", encoding="utf-8").write(json.dumps(record, ensure_ascii=False))
        recon.dump(sandbox)
        check(_zips(snap_dir) == ["main_000_20260805.zip"],
              "版が変わると、上書きの前に前回ぶんが退避される")

        # 退避を切ってあれば走らない。
        record = json.load(io.open(path, encoding="utf-8"))
        record["app_version"] = "main_001"
        record["written"] = "2026-08-06T00:00:00"
        io.open(path, "w", encoding="utf-8").write(json.dumps(record, ensure_ascii=False))
        recon.dump(sandbox, backup=False)
        check(_zips(snap_dir) == ["main_000_20260805.zip"],
              "backup=False なら版が変わっても退避しない")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_build_identity():
    print("=== build_identity: ビルドの素性 ===")
    identity = recon.build_identity()
    check(set(recon.IDENTITY_KEYS) <= set(identity),
          "判定に使う項目が揃っている")
    check("exe_mtime" in identity and "exe_mtime" not in recon.IDENTITY_KEYS,
          "exe_mtime は控えるだけで判定には使わない")
    check(recon.build_identity() == identity,
          "同じプロセスで2回呼べば同じ素性（起動ごとに動く値を混ぜていない）")
    # ゲームの外なので Epic のマニフェストにも __main__ にも当たらない。
    # 取れないこと自体で落ちないのを見る。
    check(identity["app_version"] is None and identity["game_version"] is None,
          "ゲームの外では版が取れず、それでも例外にならない")


def main():
    test_same_build()
    test_snapshot_name()
    test_archive_previous()
    test_dump_end_to_end()
    test_build_identity()
    print()
    if _FAILS:
        print("{} 件失敗: {}".format(len(_FAILS), _FAILS))
        return 1
    print("全て通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
