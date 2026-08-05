#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""out/ のログを世代管理する。

このプロジェクトのログは全て「開く → 追記する → 閉じる」で書かれているので、
放っておくと前のプレイの内容が延々と積み上がる。実際に events.log や
quest_flow.log は数 MB まで育っていて、「今回のプレイで何が起きたか」を
読み取るのが難しくなる。

そこで注入のたびに（＝1世代ごとに）ログを新しくする。今あるものは
`名前.log.1` に退避し、本体は空の状態から書き始める。KEEP_GENERATIONS を
増やせば `.1`, `.2`, ... と複数世代を残せる。

【なぜホスト側（injector.py / watcher.py）で行うのか】
ゲームプロセスの中（instantale_modloader.boot）で入れ替えると、boot が
自分で modloader.log に書いている最中や、見張りスレッドによる mod の
当て直し（deferred re-apply）でも走ってしまい、1回のプレイの記録が途中で
分断される。注入は「1世代」の境界そのものなので、注入する側で1回だけ
入れ替えるのが素直で、ゲームを巻き込む危険も無い。

【デバッグ用の ON/OFF】
優先順位は高い順に次の通り。

    1. コマンドライン        --no-log-rotate / --log-rotate
    2. 環境変数              INSTANTALE_LOG_ROTATE=0 / 1
    3. 設定ファイル          settings/loader.json の "log_rotate"（GUI の切り替え）
    4. このファイルの既定値  ROTATE_LOGS

OFF にすると入れ替えずに追記され続けるので、複数回の注入をまたいで
挙動を追いたいときはこちらを使う。

3 を 1・2 より下に置いているのは、その場限りの上書き（コマンドラインや
環境変数）が、保存された設定より強くあるべきだから。逆にすると
`--no-log-rotate` を付けても GUI の設定に負けることになる。
"""

from __future__ import annotations

import json
import os

# 世代管理の既定値。False にすると入れ替えず、常に追記していく。
#
# **ここを書き換えるのは最後の手段**。切り替えたいだけなら GUI の
# 「実行 > 注入のたびにログを新しくする」を使う（`settings/loader.json` に入り、
# この既定値より優先される）。設定ファイルなら MOD を差し替えても残るが、
# ここを直すと版を上げたときに黙って元に戻る。
ROTATE_LOGS = True

# 取っておく古い世代の数。1 なら `名前.log.1` だけが残る。
# 0 にすると退避せずに消す（ディスクを一切使いたくないとき用）。
KEEP_GENERATIONS = 1

ENV_VAR = "INSTANTALE_LOG_ROTATE"

#: GUI の切り替えの保存先。実体は `settings/loader.json` で、
#: 持ち主は `runtime/instantale_modloader/config.py`（`FLAGS_NAME` / `flags_path`）。
#: ここから import しないのは、このファイルが injector / watcher から
#: **runtime を sys.path に入れずに**呼ばれるため。名前を2箇所に書くことになるが、
#: そのために起動経路へ import の前提を足す方が高くつく。
SETTINGS_FLAGS_NAME = "loader.json"
SETTINGS_FLAG_KEY = "log_rotate"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def settings_flag() -> bool | None:
    """`settings/loader.json` の `"log_rotate"` を読む。無ければ `None`。

    `None` を返すのは「書かれていない」場合で、`False`（切ってある）とは区別する。
    区別しないと、設定ファイルが無いだけで既定値 `ROTATE_LOGS` に届かなくなる。

    読めなくても止めない。ログの世代管理は注入のついでの処理で、ここで例外を
    上げると注入そのものが失敗する ― 設定が読めないことより注入できない方が困る。
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "settings", SETTINGS_FLAGS_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(SETTINGS_FLAG_KEY)
    return value if isinstance(value, bool) else None


def enabled(cli_override: bool | None = None) -> bool:
    """世代管理を行うかどうかを決める。cli_override が None なら環境変数→既定値。"""
    if cli_override is not None:
        return cli_override
    raw = os.environ.get(ENV_VAR)
    if raw is not None:
        value = raw.strip().lower()
        if value in _TRUE:
            return True
        if value in _FALSE:
            return False
        # 解釈できない値（綴り間違いなど）は既定値に落とす。
    stored = settings_flag()
    if stored is not None:
        return stored
    return ROTATE_LOGS


def add_arguments(parser) -> None:
    """--log-rotate / --no-log-rotate を argparse に足す。

    dest は log_rotate。未指定なら None になり、enabled() が環境変数と
    既定値を見に行く。

    help はコンソールに出るので ASCII で書く（日本語だと cp932 のコンソールで
    文字化けする。watch.bat と同じ理由。日本語の説明は README / TECH に置く）。
    """
    # 既定値は ROTATE_LOGS ではなく enabled() を出す。設定ファイル（GUI の
    # 切り替え）や環境変数で変わるので、このファイルの定数を出すと嘘になる。
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--log-rotate", dest="log_rotate", action="store_true", default=None,
                       help="start fresh out/*.log on every injection (default: {})".format(
                           "on" if enabled() else "off"))
    group.add_argument("--no-log-rotate", dest="log_rotate", action="store_false",
                       help="keep appending to the existing logs (for debugging)")


def _shift(path: str, keep: int) -> None:
    """path を `path.1` に退避し、既にある世代を1つずつ後ろへずらす。

    keep が 0 なら退避せずに消す。
    """
    if keep <= 0:
        os.remove(path)
        return
    # 一番古い世代を先に片付けてから、後ろの番号へ順に送る。
    # 新しい番号から処理すると上書きで世代が飛ぶので、順序は変えないこと。
    oldest = "{}.{}".format(path, keep)
    if os.path.exists(oldest):
        os.remove(oldest)
    for i in range(keep - 1, 0, -1):
        src = "{}.{}".format(path, i)
        if os.path.exists(src):
            os.replace(src, "{}.{}".format(path, i + 1))
    os.replace(path, path + ".1")


def rotate(out_dir: str, *, cli_override: bool | None = None,
           keep: int = KEEP_GENERATIONS, log=None) -> int:
    """out_dir 直下の *.log を1世代ぶん送る。入れ替えた本数を返す。

    OFF のときは何もせず 0 を返す。out/ 直下の `*.log` だけが対象で、
    サブディレクトリ（out/test, out/recon）や状態ファイル
    （quest_clients.json, crashlog_baseline.txt）には触らない。

    失敗しても例外は投げない。ログの入れ替えに失敗したからといって、
    注入そのものを止める理由は無いため。
    """
    def _say(msg: str) -> None:
        if log is not None:
            log(msg)

    if not enabled(cli_override):
        return 0
    if not os.path.isdir(out_dir):
        return 0

    rotated = 0
    failed = []
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".log"):
            continue
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        if os.path.getsize(path) == 0:
            continue      # 空なら送る意味が無い（前回の退避を無駄に潰さない）
        try:
            _shift(path, keep)
            rotated += 1
        except OSError as exc:
            # Windows では、他のプロセスが開いたままのファイルは動かせない。
            # ここのログは全て開閉を都度行っているので普段は起きないが、
            # 起きても記録して次のファイルへ進む。
            failed.append("{} ({}: {})".format(name, type(exc).__name__, exc))

    if rotated:
        _say("log rotate: {} file(s) moved aside (keeping {} generation(s))".format(
            rotated, keep) if keep > 0 else
            "log rotate: {} file(s) deleted (keep=0)".format(rotated))
    if failed:
        _say("log rotate: could not rotate " + "; ".join(failed))
    return rotated
