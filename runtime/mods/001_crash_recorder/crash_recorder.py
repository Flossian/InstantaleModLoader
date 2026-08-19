# -*- coding: utf-8 -*-
"""クラッシュしたときの情報を、省略なしで out/live_crashes.log に残す。

いま動いている版で何が落ちるかは、この MOD の記録だけが答える。
ゲームに同梱されていた crash_log.txt は全て `game_version` 013 のもので、
現行版（main_023 ＝ `014`）への更新で消えている。
直っているかもしれないし、まだ残っているかもしれない（GAME.md §1.4 / §1.5）。
件数を根拠にする議論は 013／014 の境目を跨がせないこと。

そのうえ、クラッシュ地点はモジュールのグローバルから引けるとは限らない（`send_request_on_id` はトレースバックに 62 回出るのに
`vars(module)` には無い ＝ ネスト関数）。
地点名から場所を辿れない以上、**起きたその瞬間を自分で記録する**しかない。

そこでゲーム自身のクラッシュ処理にフックして、より詳しい記録を別に取る。
同梱のものとの違いは3点:

  * 例外の連鎖（__cause__ / __context__）を全部たどる。
    ある例外の処理中にさらに別の例外が出た場合、標準の記録では後者しか
    残らないことがあるが、それだと本当の原因が消える。
  * 連鎖に番号を振るので、どれが引き金でどれが巻き添えかが一目で分かる
    （`100_fix_kivy_shutdown` の後始末クラッシュがまさにこの形）。
  * 各記録にスレッド名・ゲームバージョン・時刻を付ける。

ゲームの挙動は変えない。
元のクラッシュ処理もそのまま呼ぶので、
ゲーム側のログ出力やクラッシュ送信は今までどおり動く。
"""

import datetime
import threading
import time
import traceback

from instantale_modloader.frames import format_locals

LOG_BASENAME = "live_crashes.log"

#: `__main__.report_crash` が現れるのを待つ間隔と、諦めるまでの時間（秒）。
#: 待つ相手はゲーム本体の起動なので、短くしても意味は無い。
WATCH_POLL = 2.0
WATCH_SECONDS = 600.0


def _fmt_chain(exc_value) -> str:
    """例外と、それが連鎖している元の例外を全部まとめて整形する。"""
    # まず新しい方から古い方へたどってリストにする。
    # id() で既出チェックしているのは、連鎖が輪になっていても止まるようにするため。
    chain = []
    seen = set()
    current = exc_value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__

    # 出力は古い方（＝引き金になった例外）から並べる。
    # Python の標準表示と同じ向き。
    parts = []
    for index, exc in enumerate(reversed(chain)):
        position = len(chain) - index
        label = "trigger" if position == len(chain) else "raised while handling the above"
        parts.append("--- [{}/{}] {} ---".format(index + 1, len(chain), label))
        parts.append("".join(traceback.format_exception_only(type(exc), exc)).rstrip())
        tb = getattr(exc, "__traceback__", None)
        if tb is not None:
            parts.append("".join(traceback.format_tb(tb)).rstrip())
    return "\n".join(parts)


def apply(ctx):
    import sys

    # フックするのはゲーム本体（instantale.py = __main__）のクラッシュ処理関数。
    # まだ無ければ降りずに待つ（下の `watch`）。
    # 存在確認に hasattr を使わない（TECH.md §6。
    # トリップワイヤを仕掛けた相手だと計測しただけで記録が量産される。
    # ここは module 相手なので実害は無いが、
    # 「無い」の確かめ方はプロジェクト全体で揃えておく）。
    def install(main):
        """`__main__.report_crash` が在ることを確かめてから設置する。"""
        log_path = ctx.out_path(LOG_BASENAME)

        # 各記録に書き込むバージョン番号。
        # 取れなくても記録自体は続ける。
        try:
            version = main.get_game_version()
        except Exception:
            version = "?"
        # バージョンはログに書くだけ。
        # 判定には使わない（'013' 前提で書いていた頃の文面は main_023 で嘘になった）。
        # 同梱の crash_log.txt は '013' のもので、その版はもう走っていない。
        ctx.log("running build reports game_version={!r} "
                "(the bundled crash_log.txt was '013'; do not read it as this "
                "build's bug list)".format(version))

        def record(exc_type, exc_value, exc_traceback, title):
            # 記録処理そのものが例外を出すと、
            # ゲームのクラッシュ処理を巻き込んでさらに壊れる。
            # 全体を try で囲んで、失敗してもログに残すだけにする。
            try:
                header = "=" * 78
                body = "\n".join([
                    header,
                    "{}  |  {}  |  game_version={}  |  thread={}".format(
                        title, datetime.datetime.now().isoformat(), version,
                        threading.current_thread().name),
                    header,
                    # 例外オブジェクトがあれば連鎖を全部、無ければ標準の整形で済ませる。
                    _fmt_chain(exc_value) if exc_value is not None else
                    "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
                    "",
                    "--- locals at the innermost game frames ---",
                    # ソースが読めないので、その瞬間の変数の中身が唯一の手がかりになる。
                    format_locals(getattr(exc_value, "__traceback__", None) or exc_traceback),
                    "",
                ])
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(body + "\n")
            except Exception:
                ctx.log_exc("crash recorder failed while recording")

        @ctx.wrap("__main__:report_crash")
        def report_crash(orig, exc_type, exc_value, exc_traceback, title="CRASH"):
            # 自前の記録を取ってから、必ず元の処理を呼ぶ。
            # ゲームの挙動は変えない。
            record(exc_type, exc_value, exc_traceback, title)
            return orig(exc_type, exc_value, exc_traceback, title)

        ctx.log("live crash log: {}".format(log_path))
        # ゲームが excepthook を差し替えているかどうかを記録しておく。
        # 差し替えられていると、ここでフックした関数を通らない経路が残る可能性がある。
        ctx.log("  sys.excepthook       = {!r}".format(getattr(sys.excepthook, "__name__", sys.excepthook)))
        ctx.log("  threading.excepthook = {!r}".format(
            getattr(threading.excepthook, "__name__", threading.excepthook)))


    # **在るとは限らない。**
    # インタプリタ初期化の時点で注入すると `__main__.report_crash` はまだ無く、
    # ここで降りると設置されないまま1セッションが終わる
    # （実測: 89セッション中77で初回ブートが記録係なしで走り、
    #  ブートが1回きりだった2つの pid では最後まで載らなかった）。
    # `__main__` は常に `sys.modules` に在るのでローダの保留にも載せられない。
    # 保留は「モジュールがまだ無い」ためのもので、
    # 「モジュールは在るが属性がまだ無い」はここで待つしかない。
    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "report_crash", None) is not None:
        install(main)
        return

    ctx.log("__main__.report_crash not found yet; watching for it "
            "(every {:.0f}s, up to {:.0f}s)".format(WATCH_POLL, WATCH_SECONDS))

    def watch():
        deadline = time.monotonic() + WATCH_SECONDS
        while not ctx.superseded():
            module = sys.modules.get("__main__")
            if module is not None and getattr(module, "report_crash", None) is not None:
                # 直前でもう一度確かめる。
                # ループの判定から今までの間に新しい boot が始まっていることがあり、
                # その boot は台帳を作り直している最中。
                # ここで割り込むと、記録が別の世代へ紛れ込むか落ちる。
                # 降りて構わない。新しい boot の apply() は
                # `report_crash` が既に在る状態で走るので、その場で設置される。
                if ctx.superseded():
                    ctx.log("crash recorder: a newer boot took over; "
                            "leaving the late arm to it")
                    return
                try:
                    install(module)
                    ctx.log("crash recorder: late-armed (report_crash appeared)")
                    # `boot()` は報告を書き終えて降りている。
                    # 書き直さないと、この設置は status.json に出ない。
                    ctx.refresh_status()
                except Exception:
                    ctx.log_exc("crash recorder: could not arm late")
                return
            if time.monotonic() > deadline:
                ctx.log("crash recorder: gave up waiting for "
                        "__main__.report_crash", level="WARN")
                return
            time.sleep(WATCH_POLL)

    threading.Thread(target=watch, name="instantale_crash_recorder.watch",
                     daemon=True).start()
