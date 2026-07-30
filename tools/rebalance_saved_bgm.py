# -*- coding: utf-8 -*-
"""既にセーブに書き込まれている BGM を、後からまとめて均し直す。

mod（runtime/mods/104_balance_area_bgm.py）が書き換えるのは、注入した後に
新しく作られたエリアだけ。このスクリプトは、既にあるワールドに対して
同じことをする。

選び方のルールは mod ファイルをそのまま import して使っているので、
両者の挙動がずれることはない。

セーブの形式
------------
worlds/<ワールド名>/world_data.json は、JSON を固定の鍵で XOR した
だけのファイルになっている。

    平文 = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
               .encode("utf-8")
    暗号文[i] = 平文[i] ^ KEY[i % len(KEY)]

手元の3ワールドで確認したところ、読み込んだデータを再度エンコードすると
元のファイルとバイト単位で完全に一致した。
--apply を付けたときはこの検査をファイルごとに毎回やり直し、一度でも
一致しなければ書き込みを中止する。JSON の書き出し方が少しでも違っていれば
そこで止まるので、セーブが壊れることはない。

使い方
------
    python tools/rebalance_saved_bgm.py                 # 全ワールドを確認だけする
    python tools/rebalance_saved_bgm.py --world ヴェスティア
    python tools/rebalance_saved_bgm.py --plain path/to/world_data_plain_x.json
    python tools/rebalance_saved_bgm.py --apply         # 実際に書き込む（バックアップあり）

--apply を付けない限り、ファイルには一切書き込まない。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import importlib.util
import io
import json
import os
import shutil
import sys

KEY = b"Instantale_Save_Key_2026"

HERE = os.path.dirname(os.path.abspath(__file__))
MODS_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "runtime", "mods"))


def find_mod(suffix: str) -> str:
    """mod を **番号を除いた名前** で探す。

    mod ファイルの番号は適用順を表すだけの通し番号で、分類を見直すたびに
    振り直される（実際に 14_ -> 104_ と変わった）。番号ごと書くとその都度
    ここが壊れるので、末尾で引く。
    """
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix) and name[:1].isdigit())
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    # 番号違いの同名が複数あるのは事故なので、黙って1つ選ばない。
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    return os.path.join(MODS_DIR, matches[0])


MOD_PATH = find_mod("_balance_area_bgm.py")
DEFAULT_WORLDS = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Darmabeko", "Instantale", "worlds")
DEFAULT_GAME = r"C:\Program Files\Epic Games\Instantaleq6Ve7"


def load_policy():
    """mod ファイルを読み込んで、選び方のルールをそのまま借りる。

    mod ファイルはトップレベルでは何もしない（処理は全て apply() の中）ので、
    ゲームの外から普通に import しても安全。
    ルールを2箇所に書かないための工夫でもある。
    """
    spec = importlib.util.spec_from_file_location("bgm_policy", os.path.normpath(MOD_PATH))
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load {}".format(MOD_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# セーブの読み書き
# --------------------------------------------------------------------------
def xor(blob: bytes) -> bytes:
    key = KEY
    n = len(key)
    return bytes(byte ^ key[i % n] for i, byte in enumerate(blob))


def encode(data) -> bytes:
    # separators はゲーム側と完全に同じにする必要がある。
    # ここが違うと、下のラウンドトリップ検査で必ず引っかかる
    # （つまり、壊れたセーブを書く手前で止まる）。
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return xor(text.encode("utf-8"))


def decode(blob: bytes):
    return json.loads(xor(blob).decode("utf-8"))


def read_world(path: str):
    """(データ, XOR されているか, 再エンコードが一致したか) を返す。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    # "{" か BOM で始まっていれば、素の JSON（--plain で書き出したものなど）。
    if raw[:1] in (b"{", b"\xef"):
        return json.loads(raw.decode("utf-8-sig")), False, None
    data = decode(raw)
    # 読み込んだものを書き戻したら元と同じになるか。
    # これが、このファイルに書き込んで安全かどうかの唯一の判断材料になる。
    return data, True, encode(data) == raw


# --------------------------------------------------------------------------
# 集計と表示
# --------------------------------------------------------------------------
def snapshot(areas, policy):
    """{size: Counter(mood)} と、実際に使われている曲の集合を返す。"""
    moods = collections.defaultdict(collections.Counter)
    tracks = set()
    for area in areas.values():
        parsed = policy.parse_bgm((area or {}).get("bgm"))
        if parsed is None:
            continue
        _prefix, size, mood, track, _sep = parsed
        moods[size][mood] += 1
        tracks.add((size, mood, track))
    return moods, tracks


def describe(label, moods, tracks, pool):
    """変更前後の内訳を表示する。"""
    print("    %s" % label)
    for size in sorted(moods):
        counter = moods[size]
        # 「9 種類中 2 種類」のように分母も出す。
        # 分母が無いと、偏っているのかどうか判断できない。
        available = len(set(m for m, _t in pool.get(size, [])))
        print("      %-9s %2d areas over %d/%d moods : %s" % (
            size, sum(counter.values()), len(counter), available,
            ", ".join("%s x%d" % (m, n) for m, n in counter.most_common())))
    print("      distinct tracks in use: %d" % len(tracks))


# --------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------
def process(path, policy, pool, apply_changes):
    """ワールドファイル1つを処理する。実際に書き込んだときだけ True を返す。"""
    print("=" * 78)
    print(path)
    print("=" * 78)

    try:
        data, obfuscated, round_trip = read_world(path)
    except Exception as exc:
        print("    SKIP: cannot read (%s: %s)" % (type(exc).__name__, exc))
        return False

    areas = data.get("areas")
    if not isinstance(areas, dict) or not areas:
        print("    SKIP: no areas")
        return False

    print("    format: %s" % ("obfuscated (XOR)" if obfuscated else "plain JSON"))
    if obfuscated:
        # ここが一番重要な安全装置。
        # 再エンコードが元と一致しないということは、こちらが理解している
        # 書式とゲームの書式が違うということで、書き込めばセーブを壊す。
        if round_trip:
            print("    round-trip check: OK (re-encode reproduces the file exactly)")
        else:
            print("    round-trip check: FAILED -- refusing to touch this file")
            return False

    before_moods, before_tracks = snapshot(areas, policy)
    describe("before:", before_moods, before_tracks, pool)

    # mod と同じ関数を呼ぶ。only を指定しないので全エリアが対象になる。
    changes = policy.balance_world(areas, pool)

    after_moods, after_tracks = snapshot(areas, policy)
    print()
    describe("after:", after_moods, after_tracks, pool)
    print()
    print("    %d of %d area(s) would change; distinct tracks %d -> %d" % (
        len(changes), len(areas), len(before_tracks), len(after_tracks)))

    for aid, old, new in changes:
        # 表示は末尾2階層（mood/曲名）だけにする。フルパスは長くて比較しづらい。
        short_old = "/".join(old.replace("\\", "/").split("/")[-2:])
        short_new = "/".join(new.replace("\\", "/").split("/")[-2:])
        print("      area %-4s %-50s -> %s" % (aid, short_old, short_new))

    if not changes:
        print("    already balanced; nothing to do")
        return False

    if not apply_changes:
        print()
        print("    DRY RUN -- nothing written. Re-run with --apply to write.")
        return False

    # 書き込む前に必ずバックアップを取る。
    # 名前に日時が入るので、実行を繰り返しても以前のバックアップは消えない。
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-%s" % (path, stamp)
    shutil.copy2(path, backup)
    # 読み込んだときと同じ形式で書き戻す。
    # 素の JSON だったものを XOR して返すようなことはしない。
    payload = encode(data) if obfuscated else json.dumps(
        data, ensure_ascii=False, indent=2).encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(payload)
    print()
    print("    WROTE %s  (backup: %s)" % (path, os.path.basename(backup)))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worlds", default=DEFAULT_WORLDS,
                        help="worlds directory (default: %(default)s)")
    parser.add_argument("--world", action="append", default=[],
                        help="only this world name; repeatable")
    parser.add_argument("--plain", action="append", default=[],
                        help="a plain JSON export to process instead; repeatable")
    parser.add_argument("--game", default=DEFAULT_GAME,
                        help="game directory holding Assets/sounds/musics")
    parser.add_argument("--apply", action="store_true",
                        help="actually write (default is a dry run)")
    args = parser.parse_args(argv)

    # ワールド名や曲名に日本語が入るので、cp932 のコンソールでも
    # 文字化けやエラーで止まらないようにしておく。
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    policy = load_policy()

    # 曲の一覧はゲームのインストール先から読む。セーブ側には曲の一覧が無い。
    root = os.path.join(args.game, "Assets", "sounds", "musics")
    if not os.path.isdir(root):
        raise SystemExit("music assets not found: {}".format(root))
    pool = policy.scan_pool(root)
    print("pool: %s\n" % ", ".join(
        "%s=%d tracks" % (s, len(pool[s])) for s in sorted(pool)))

    # --plain が指定されていればそれを使い、無ければ worlds フォルダを探索する。
    targets = list(args.plain)
    if not targets:
        if not os.path.isdir(args.worlds):
            raise SystemExit("worlds directory not found: {}".format(args.worlds))
        for name in sorted(os.listdir(args.worlds)):
            if args.world and name not in args.world:
                continue
            candidate = os.path.join(args.worlds, name, "world_data.json")
            if os.path.isfile(candidate):
                targets.append(candidate)

    if not targets:
        raise SystemExit("nothing to process")

    written = 0
    for path in targets:
        if process(path, policy, pool, args.apply):
            written += 1
        print()

    print("=" * 78)
    if args.apply:
        print("%d file(s) written." % written)
    else:
        print("Dry run over %d file(s). Nothing was written." % len(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
