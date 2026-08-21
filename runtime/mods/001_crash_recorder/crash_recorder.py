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

記録を取ったら元のクラッシュ処理をそのまま呼ぶので、
本体の `crash_log.txt` は今までどおり書かれる。

##### 送信だけは止める

本体は `report_crash` の中で、組んだ記録を

    write_crash_log_to_file -> should_skip_crash_log_server_send -> send_crash_log_to_server

の順に渡す（実機で確認。VERIFICATION.md §3.33）。
最後の関数の中身は読めていない。
名前と `CRASH_REPORT_URL` からして外へ出す口で、
渡っているのは `crash_log.txt` に書かれるのと同じ文字列。

MOD を入れて遊んでいる間の記録には、ローダと MOD の枠が混ざる。
外へ出すかどうかはこちらで決めることなので、**入っている間は出さない**ことにした。
`crash_log.txt` は書かれるので手元には残るし、
素のゲームで起きたものを報告したいときは MOD を外して再現すればよい。

止めるのは `send_crash_log_to_server` そのもの。
`should_skip_crash_log_server_send` を True にする形は、
本体がその判断を通る経路でしか効かない。
出したくないのは送信という行為の方なので、そちらを直接止める。

> 2026-08-21 まで、**注入している間はこの後段が丸ごと走っていなかった**。
> 注入で流し込むコードがモジュール階層で `import datetime` していて、
> 本体が `from datetime import datetime` で持っていた束縛を上書きしていたため、
> `make_crash_log` が `AttributeError` で落ちていた
> （`tools/injector.py` の `BOOTSTRAP_TEMPLATE` の注記）。
> あちらを直したので `crash_log.txt` は戻る。送信はここで意図して止める。
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
            # 本体の crash_log.txt はそのまま書かれる。
            record(exc_type, exc_value, exc_traceback, title)
            return orig(exc_type, exc_value, exc_traceback, title)

        @ctx.wrap("__main__:send_crash_log_to_server", required=False)
        def send_crash_log_to_server(orig, *args, **kwargs):
            # **元の関数は呼ばない。**
            # ここを通るトレースバックにはローダと MOD の枠が混ざっているので、
            # 素のゲームの不具合報告としては送らない（上の「送信だけは止める」）。
            size = len(args[0]) if args and isinstance(args[0], str) else -1
            ctx.log("crash recorder: not sending the crash log to the server "
                    "({} chars); mods are loaded, so it would not be a report "
                    "about the plain game".format(size))
            return None

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
