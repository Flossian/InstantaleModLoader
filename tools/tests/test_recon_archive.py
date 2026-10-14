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


def _plant(recon_dir, marker, build=None, function=None):
    """前回のダンプが在ることにする。`marker` が中身の見分けになる。

    `function`（`"m:f(x)"` の形）を渡すと、`targets.txt` の2行目に足す。重複の判定は関数で見るので、
    中身の違うダンプとして扱わせたいときに要る。
    """
    os.makedirs(recon_dir, exist_ok=True)
    for name in recon.OUTPUT_FILES:
        with io.open(os.path.join(recon_dir, name), "w", encoding="utf-8") as fh:
            fh.write(marker + "\n")
            if function and name == "targets.txt":
                fh.write(function + "\n")
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

    # 注入が `__main__` の組み上がりより早いと game_version だけ取れない（main_025・026 の実機）。
    check(recon._same_build(dict(base, game_version=None), dict(base)),
          "前回 game_version が取れていなくても、他が揃えば同じビルド")
    check(recon._same_build(dict(base), dict(base, game_version=None)),
          "今回 game_version が取れていなくても、他が揃えば同じビルド")
    check(not recon._same_build(dict(base, game_version=None),
                                dict(base, app_version="main_026")),
          "game_version が無くても、版が違えば別のビルド")
    # 起動直後は実行ファイルの場所が読めず exe_size が取れなかった（2026-10-15 の実機で2本退避した）。
    check(recon._same_build(dict(base), dict(base, exe_size=None, game_version=None)),
          "起動直後に exe_size が取れなくても、他が揃えば同じビルド")
    exe = recon._exe_path()
    check(os.path.isfile(exe), "実行ファイルの場所は実在のファイル: {}".format(exe))


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
        # 配布フォルダの形にする（退避先は `out/` の1つ上）。
        recon_dir = os.path.join(sandbox, "out", "recon")
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
        _plant(recon_dir, "main_024 の最後の1回", build=old, function="m:first(a)")
        path = recon.archive_previous(recon_dir, dict(new))
        check(path is not None and os.path.isfile(path), "ビルドが変われば退避する")
        check(_zips(snap_dir) == ["main_024_20260805.zip"],
              "  名前は <前の版>_<そのダンプを取った日>.zip")

        with zipfile.ZipFile(path) as archive:
            names = sorted(archive.namelist())
            expected = sorted(list(recon.OUTPUT_FILES) + [recon.BUILD_NAME])
            check(names == expected, "  成果物5つと build.json が入っている")
            check(archive.read("targets.txt").decode("utf-8").replace("\r\n", "\n").strip()
                  == "main_024 の最後の1回\nm:first(a)",
                  "  中身は上書きされる前の（＝前の版の）ダンプ")
            check(json.loads(archive.read(recon.BUILD_NAME).decode("utf-8"))["app_version"]
                  == "main_024",
                  "  zip 自身に「どの版のものか」が入っている")

        # 同じ版・同じ日にもう一度。
        # 滅多に無いが、黙って上書きしてはいけない。
        _plant(recon_dir, "main_024 のもう1回", build=old, function="m:second(b)")
        recon.archive_previous(recon_dir, dict(new))
        check(_zips(snap_dir) == ["main_024_20260805.zip", "main_024_20260805_2.zip"],
              "名前が埋まっていれば _2 を足す（先の退避を消さない）")

        # 同じビルドで中身も同じダンプは、もう1本作らない。
        _plant(recon_dir, "main_024 の3回目", build=old, function="m:second(b)")
        check(recon.archive_previous(recon_dir, dict(new)) is None
              and _zips(snap_dir) == ["main_024_20260805.zip", "main_024_20260805_2.zip"],
              "同じビルドで関数も同じなら退避しない")

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
        out_dir = os.path.join(sandbox, "out")
        recon_dir = recon.dump(out_dir)
        snap_dir = os.path.join(sandbox, recon.SNAPSHOT_DIR_NAME)
        check(recon.snapshot_dir(out_dir) == snap_dir and not snap_dir.startswith(out_dir + os.sep),
              "退避先は out/ の外（1つ上の {}）".format(recon.SNAPSHOT_DIR_NAME))
        missing = [n for n in list(recon.OUTPUT_FILES) + [recon.BUILD_NAME]
                   if not os.path.isfile(os.path.join(recon_dir, n))]
        check(not missing, "成果物一式と build.json が揃う（欠け: {}）".format(missing))

        record = json.load(io.open(os.path.join(recon_dir, recon.BUILD_NAME),
                                   encoding="utf-8"))
        check(set(recon.OUTPUT_FILES) <= set(record.get("files") or {}),
              "build.json に各ファイルの sha256 が入る")
        check(record.get("written", "")[:2] == "20", "いつ書いたかが入る")

        current = os.path.join(snap_dir, recon.CURRENT_DIR_NAME)
        check(os.path.isfile(os.path.join(current, recon.BUILD_NAME))
              and os.path.isfile(os.path.join(current, recon.SIGNATURES_NAME)),
              "最初のダンプが基準（current）になる")

        # 2回目。
        # 同じビルドで関数も同じなので退避は走らない。
        recon.dump(out_dir)
        check(_zips(snap_dir) == [], "同じビルドで走らせ直しても zip は増えない")

        def edit_current(**changes):
            path = os.path.join(current, recon.BUILD_NAME)
            record = json.load(io.open(path, encoding="utf-8"))
            record.update(changes)
            io.open(path, "w", encoding="utf-8").write(json.dumps(record, ensure_ascii=False))

        # ゲームが更新されたことにする（基準が前の版）。
        edit_current(app_version="main_000", written="2026-08-05T16:44:53")
        recon.dump(out_dir)
        check(_zips(snap_dir) == ["main_000_20260805.zip"],
              "版が変わると、前の版の基準が退避される")
        with zipfile.ZipFile(os.path.join(snap_dir, "main_000_20260805.zip")) as archive:
            reason = archive.read(recon.REASON_NAME).decode("utf-8")
        check(reason.startswith("build changed") and "app_version: main_000 -> " in reason,
              "  理由が添えてある: {!r}".format(reason[:60]))
        check(json.load(io.open(os.path.join(current, recon.BUILD_NAME), encoding="utf-8"))
              .get("app_version") != "main_000", "  基準は今回のダンプに替わる")

        # 同梱の llama-server だけが替わった（版の表記は同じ）。
        edit_current(llama_builds="llama-b7054-bin-win-cuda-12.4-x64", written="2026-09-20T00:00:00")
        live = json.load(io.open(os.path.join(recon_dir, recon.BUILD_NAME), encoding="utf-8"))
        if live.get("llama_builds") is None:
            # ゲームの外では取れないので比べない（取れない鍵は比べない決まり）
            check(_zips(snap_dir) == ["main_000_20260805.zip"], "  （ゲームの外では llama_builds を比べない）")

        # 関数が変わった（版は同じ）。
        sig_path = os.path.join(current, recon.SIGNATURES_NAME)
        sigs = json.load(io.open(sig_path, encoding="utf-8"))
        some = sorted(sigs)[0]
        sigs[some] = "(old, signature)"
        sigs[some.split(":", 1)[0] + ":gone_function"] = "()"
        io.open(sig_path, "w", encoding="utf-8").write(json.dumps(sigs, ensure_ascii=False))
        edit_current(written="2026-10-01T00:00:00")
        recon.dump(out_dir)
        zips = _zips(snap_dir)
        changed = [n for n in zips if n.startswith(("unknown_20261001", "None_20261001"))
                   or n.endswith("_20261001.zip")]
        check(len(changed) == 1, "関数が変わると、版が同じでも基準が退避される: {}".format(zips))
        if changed:
            with zipfile.ZipFile(os.path.join(snap_dir, changed[0])) as archive:
                reason = archive.read(recon.REASON_NAME).decode("utf-8")
            check(reason.startswith("functions changed (2)") and "removed " in reason
                  and "changed {}(old, signature)".format(some) in reason,
                  "  理由に消えた関数と変わった引数が並ぶ")

        # 基準が読み込みの途中（起動直後）で、今回のほうが多く読み込み、関数が「増えた」だけ。
        sigs = json.load(io.open(sig_path, encoding="utf-8"))
        partial = {k: v for k, v in sigs.items() if k != sorted(sigs)[-1]}
        io.open(sig_path, "w", encoding="utf-8").write(json.dumps(partial, ensure_ascii=False))
        edit_current(modules=1, written="2026-10-03T00:00:00")
        before = _zips(snap_dir)
        recon.dump(out_dir)
        check(_zips(snap_dir) == before, "  基準より多く読み込んで「増えた」だけなら退避しない")

        # 基準より読み込みの少ないダンプ（起動直後）は比べない。
        sigs = json.load(io.open(sig_path, encoding="utf-8"))
        edit_current(modules=10 ** 9, written="2026-10-02T00:00:00")
        sigs[some] = "(again, different)"
        io.open(sig_path, "w", encoding="utf-8").write(json.dumps(sigs, ensure_ascii=False))
        before = _zips(snap_dir)
        recon.dump(out_dir)
        check(_zips(snap_dir) == before, "  基準より読み込みが少ない回は比べない（zip は増えない）")
        check(json.load(io.open(os.path.join(current, recon.BUILD_NAME), encoding="utf-8"))
              .get("modules") == 10 ** 9, "  基準もそのまま（少ない回で置き換えない）")

        # 退避を切ってあれば走らない。
        edit_current(app_version="main_001", modules=0, written="2026-08-06T00:00:00")
        before = _zips(snap_dir)
        recon.dump(out_dir, backup=False)
        check(_zips(snap_dir) == before, "backup=False なら版が変わっても退避しない")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_signatures_scope():
    print("=== signatures.json: その場所で定義されたゲームの関数だけ ===")
    import functools
    import types as _types
    sandbox = _sandbox()
    name = "scripts.recon_scope_probe"
    module = _types.ModuleType(name)
    exec("class Item:\n"
         "    def defined(self, a):\n"
         "        pass\n"
         "def top(x):\n"
         "    pass\n", module.__dict__)
    for value in (module.Item, module.top, module.Item.defined):
        value.__module__ = name

    def callback(item_instance):            # 後から代入される関数（ゲームの Item.set_callback と同じ形）
        pass
    callback.__module__ = name
    module.Item.item_buy_callback = callback
    module.imported = json.dumps            # よそから import した関数

    @functools.wraps(module.top)
    def wrapped(*args, **kwargs):           # ローダの包み（元の名前を写す）
        return None
    module.top = wrapped

    def from_mod(y):
        pass
    from_mod.__module__ = "instantale_mod_999_test"
    module.Item.from_mod = from_mod
    sys.modules[name] = module
    try:
        recon_dir = recon.dump(os.path.join(sandbox, "out"), backup=False)
        sigs = json.load(io.open(os.path.join(recon_dir, recon.SIGNATURES_NAME), encoding="utf-8"))
        mine = sorted(k for k in sigs if k.startswith(name + ":"))
        check(mine == [name + ":Item.defined", name + ":top"],
              "定義されたメソッドと、包まれた関数だけが入る: {}".format(mine))
        targets = io.open(os.path.join(recon_dir, "targets.txt"), encoding="utf-8").read()
        check(name + ":Item.item_buy_callback" in targets,
              "  targets.txt（貼るための一覧）には従来どおり全部載る")
    finally:
        sys.modules.pop(name, None)
        shutil.rmtree(sandbox, ignore_errors=True)


def test_function_changes():
    print("=== function_changes / identity_changes ===")
    old = {"m:a": "(x)", "m:b": "(y)", "m:C.c": "(self)"}
    new = {"m:a": "(x, z=None)", "m:C.c": "(self)", "m:d": "()", "lazy:e": "()"}
    lines = recon.function_changes(old, new)
    check(lines == ["changed m:a(x) -> (x, z=None)", "removed m:b(y)", "added m:d()"],
          "引数の変化・消えた・増えた（前に無かったモジュールの関数は数えない）: {}".format(lines))
    check(recon.function_changes(old, dict(old)) == [], "同じなら空")
    check(recon.function_changes(dict(old, **{"__main__:_instantale_modloader_bootstrap": "()"}), old) == [],
          "ローダが置いた名前（_instantale…）は消えても数えない")
    check(recon._parse_targets("# x\n__main__:_instantale_modloader_bootstrap()\nm:a(x)\n") == {"m:a": "(x)"},
          "重複の判定でもローダが置いた名前を外す")
    base = {"app_version": "main_026", "game_version": "014", "exe_size": 1,
            "python": "3.10.11", "kivy": "2.3.0", "llama_builds": "llama-b10642"}
    check(recon.identity_changes(base, dict(base, llama_builds="llama-b11000"))
          == ["llama_builds: llama-b10642 -> llama-b11000"],
          "同梱の llama-server が替われば特異な変化として出る")
    check(not recon._same_build(base, dict(base, kivy="2.3.1")), "Kivy が替われば別のビルド")
    check(recon._same_build(dict(base, kivy=None, llama_builds=None), dict(base)),
          "前の控えに無い鍵（2026-10-14 より前）は比べない")


def test_dump_module_vanishes():
    print("=== dump: 走査中にモジュールが消えても止まらない ===")
    # 写しを取った後で `sys.modules[name]` を直に引くと、消えたモジュールで KeyError になり
    # summary 以降と build.json が書かれない。
    import types
    name = "scripts._recon_vanishing_probe"
    sys.modules[name] = types.ModuleType(name)
    real_describe = recon.describe_module
    popped = []

    def describe_and_drop(mod_name, module, *, deep):
        if not popped:
            popped.append(sys.modules.pop(name, None))
        return real_describe(mod_name, module, deep=deep)

    sandbox = _sandbox()
    recon.describe_module = describe_and_drop
    try:
        try:
            recon_dir = recon.dump(sandbox, backup=False)
        except Exception as exc:
            check(False, "消えたモジュールで dump が止まった: {!r}".format(exc))
            return
        check(popped and popped[0] is not None, "（前提）走査の途中で消えている")
        missing = [n for n in list(recon.OUTPUT_FILES) + [recon.BUILD_NAME]
                   if not os.path.isfile(os.path.join(recon_dir, n))]
        check(not missing, "成果物一式と build.json が揃う（欠け: {}）".format(missing))
        with io.open(os.path.join(recon_dir, "game_modules.txt"), encoding="utf-8") as fh:
            check(name in fh.read(), "写しを取った時点のモジュールとして載る")
        check(not [n for n in os.listdir(recon_dir) if n.endswith(".tmp")],
              "build.json の書きかけを残さない（壊れない書き方で書く）")
    finally:
        recon.describe_module = real_describe
        sys.modules.pop(name, None)
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


def test_prune_keeps_each_version():
    print("=== _prune_snapshots: 版ごとの1本は消さない ===")
    sandbox = _sandbox()
    try:
        snap_dir = os.path.join(sandbox, recon.SNAPSHOT_DIR_NAME)
        os.makedirs(snap_dir)
        base = 1_700_000_000

        def plant(name, size, age):
            path = os.path.join(snap_dir, name)
            with open(path, "wb") as fh:
                fh.write(b"x" * size)
            os.utime(path, (base + age, base + age))

        # 古い版が1本ずつ、新しい版の退避が大小交互に溜まった形（main_025 の 10-14 と同じ）。
        plant("main_023_20260805.zip", 300, 0)
        plant("main_024_20260809.zip", 300, 1)
        for i in range(12):
            plant("main_025_20261014{}.zip".format("" if i == 0 else "_{}".format(i + 1)),
                  350 if i % 2 else 65, 10 + i)
        dropped = recon._prune_snapshots(snap_dir, keep=4)
        left = sorted(os.listdir(snap_dir))
        check("main_023_20260805.zip" in left and "main_024_20260809.zip" in left,
              "同じ版の退避が溜まっても、前の版の1本は消さない")
        check(dropped == 12 - 1 - 4 and len(left) == 2 + 1 + 4,
              "  消すのは同じ版の2本目以降で、上限はそれだけで数える（残り {}）".format(len(left)))
        kept25 = [n for n in left if n.startswith("main_025")]
        check("main_025_20261014_12.zip" in kept25,
              "  版の1本は、いちばん大きい（モジュールを多く見た）うち新しいもの")
        check(recon._snapshot_version("main_025_20261014_12.zip") == "main_025"
              and recon._snapshot_version("unknown_20260801.zip") == "unknown"
              and recon._snapshot_version("odd.zip") == "odd.zip",
              "  名前から版を取り出す（形が違えば名前そのもの）")
        check(recon._prune_snapshots(snap_dir, keep=4) == 0, "  もう一度刈っても減らない")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_redundant():
    print("=== _redundant: 重複した記録だけを落とす ===")
    sandbox = _sandbox()
    try:
        snap_dir = os.path.join(sandbox, recon.SNAPSHOT_DIR_NAME)
        os.makedirs(snap_dir)
        build = {"app_version": "main_026", "game_version": "014", "exe_size": 1}
        full = ["m:a(x)", "m:b(y)", "__main__:Item.cb(i)"]

        def zipped(name, lines, build=build, pad=0):
            path = os.path.join(snap_dir, name)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("targets.txt", "# header\n" + "\n".join(lines) + "\n" + "#" * pad)
                if build is not None:
                    archive.writestr(recon.BUILD_NAME, json.dumps(build))
            return path

        zipped("main_026_20261014.zip", full[:1], dict(build, game_version=None))   # 起動直後（読み込み途中）
        zipped("main_026_20261014_2.zip", full, pad=10)                             # 全部読み込んだ
        zipped("main_026_20261014_3.zip", full)                                     # 中身が同じで小さい
        zipped("main_026_20261015.zip", ["m:a(x, z=None)", "m:b(y)"])               # 関数が変わった
        zipped("main_027_20261101.zip", full[:1], dict(build, app_version="main_027"))  # 別のビルド
        zipped("unknown_20260801.zip", full[:1], build=None)                        # build.json が無い
        paths = [os.path.join(snap_dir, n) for n in sorted(os.listdir(snap_dir))]
        drop = sorted(os.path.basename(p) for p in recon._redundant(paths))
        check(drop == ["main_026_20261014.zip", "main_026_20261014_3.zip"],
              "読み込み途中の記録と、中身が同じで小さい記録だけ落とす: {}".format(drop))
        recon._prune_snapshots(snap_dir)
        left = sorted(os.listdir(snap_dir))
        check(left == ["main_026_20261014_2.zip", "main_026_20261015.zip",
                       "main_027_20261101.zip", "unknown_20260801.zip"],
              "  刈り込みでも同じものが落ちる: {}".format(left))
        check(recon._duplicate_of(os.path.join(snap_dir, "main_026_20261015.zip"), snap_dir) is None,
              "  関数が変わった記録は、ほかの記録の重複ではない")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_migrate_old_snapshots():
    print("=== migrate_old_snapshots: 前の退避先（out/recon_snapshots）から移す ===")
    sandbox = _sandbox()
    try:
        out_dir = os.path.join(sandbox, "out")
        old_dir = os.path.join(out_dir, recon.OLD_SNAPSHOT_DIR_NAME)
        snap_dir = recon.snapshot_dir(out_dir)
        os.makedirs(os.path.join(old_dir, "main_023"))          # zip にする前の頃のフォルダ
        io.open(os.path.join(old_dir, "main_023", "targets.txt"), "w", encoding="utf-8").write("023")
        for name in ("main_025_20261014.zip", "main_026_20261014.zip"):
            io.open(os.path.join(old_dir, name), "wb").write(b"old")
        os.makedirs(snap_dir)
        io.open(os.path.join(snap_dir, "main_026_20261014.zip"), "wb").write(b"new")  # 名前がぶつかる

        moved = recon.migrate_old_snapshots(out_dir, snap_dir)
        left = sorted(os.listdir(snap_dir))
        check(moved == 3, "zip もフォルダも移す（{}件）".format(moved))
        check(left == ["main_023", "main_025_20261014.zip", "main_026_20261014.zip",
                       "main_026_20261014_2.zip"], "  名前がぶつかれば _2 を足してどちらも残す: {}".format(left))
        check(io.open(os.path.join(snap_dir, "main_026_20261014.zip"), "rb").read() == b"new",
              "  先に在った退避は上書きしない")
        check(not os.path.exists(old_dir), "  空になった前の退避先は消す")
        check(recon.migrate_old_snapshots(out_dir, snap_dir) == 0, "  2回目は何もしない")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def main():
    test_same_build()
    test_snapshot_name()
    test_archive_previous()
    test_prune_keeps_each_version()
    test_redundant()
    test_migrate_old_snapshots()
    test_function_changes()
    test_signatures_scope()
    test_dump_end_to_end()
    test_dump_module_vanishes()
    test_build_identity()
    print()
    if _FAILS:
        print("{} 件失敗: {}".format(len(_FAILS), _FAILS))
        return 1
    print("全て通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
