# -*- coding: utf-8 -*-
"""重い仕事を**ゲームのスレッドから外して**直列にこなす背景ワーカー。

MOD が LLM を呼ぶと、返るまでに数十秒かかることがある。
それをゲームのスレッドでやると会話画面がそのぶん固まるので、
`311_` / `317_` / `321_` / `403_` は揃って
「待ち行列 + 背景スレッド1本」を持っていた。
4本とも中身は同じで、次の5つを解いている:

1. **直列にする。** 同時に2本走らせない
   （ローカルの推論は1つのモデルを取り合うので、並べても速くならない）
2. **溢れたら古い方から捨てる。** 推論が返らない間に会話を続けても際限なく溜めない
3. **同じ仕事を二度積まない。** 鍵が同じ仕事は、待っている間は1つ
4. **仕事が無ければ自分で終わる。** 注入し直したときに前の世代のスレッドを残さない
5. **例外を飲む。** 1件の失敗で以後の仕事が全部止まらないようにする

```python
from instantale_modloader import jobs

def compile_area(job):
    ...                                   # ここで LLM を呼んでよい（背景スレッド）

worker = jobs.Worker(ctx, compile_area, name="area_chronicle",
                     label="area chronicle", write=write,
                     key=lambda job: (job["world"], job["area_id"]))

worker.enqueue({"world": ..., "area_id": ..., "why": "arrival"})
```

**`apply()` の外に置くこと。**
`apply()` は1プロセスで何度も呼ばれる（TECH.md §3.6）ので、
中で作ると注入し直すたびに待ち行列が別物になり、
前の世代のスレッドが抱えている仕事が行方不明になる。
"""

from __future__ import annotations

import queue
import threading
import time

#: 待ち行列に積める上限。超えたら古い方から捨てる。
DEFAULT_MAX_PENDING = 8

#: 仕事が来ないまま何秒でスレッドを畳むか。
#: 短くすると注入し直したときの残骸が消えるのが早く、
#: 長くすると立ち上げ直しが減る。
DEFAULT_IDLE = 30.0


class Worker(object):
    """仕事を1本の背景スレッドで順番にこなす。

    | 引数 | |
    |---|---|
    | `ctx` | そのまま渡す（例外を `ctx.log_exc` へ流すため） |
    | `run` | 仕事1件をこなす関数。`run(job)`。**背景スレッドで呼ばれる** |
    | `name` | スレッド名。`instantale_mod.<name>` になる。MOD 専用の名前にする |
    | `label` | 例外を出すときの見出し。既定は `name` |
    | `write` | MOD 自身のログ関数。捨てたときに1行出す。無くてよい |
    | `key` | 仕事から重複除けの鍵を作る関数。`None` なら重複を見ない |
    | `max_pending` | 待ち行列の上限（既定 8） |
    | `idle` | 仕事が来ないまま畳むまでの秒数（既定 30） |
    | `on_drop` | 溢れて捨てた仕事を渡す関数。`on_drop(job)`。無くてよい |
    | `on_done` | 1件終わるたびに呼ぶ関数。`on_done(job)`。**失敗した回も呼ぶ**。無くてよい |

    `run` の中で投げた例外は `ctx.log_exc` に流して次の仕事へ進む。
    ここで止めないのは、1件の失敗（読めない返答・落ちたプロバイダ）で
    以後の仕事が全部止まると、**遊んでいる側からは何も起きなくなる**ため。
    """

    def __init__(self, ctx, run, *, name, label=None, write=None, key=None,
                 max_pending=DEFAULT_MAX_PENDING, idle=DEFAULT_IDLE,
                 on_drop=None, on_done=None):
        self.ctx = ctx
        self.run = run
        self.name = name
        self.label = label or name
        self.write = write
        self.key = key
        self.max_pending = max_pending
        self.idle = idle
        self.on_drop = on_drop
        self.on_done = on_done
        #: 待ち行列。検査が `join()` / `unfinished_tasks` で捌け終わりを待つ。
        self.jobs = queue.Queue()
        #: 待ち行列の出し入れと、スレッドの生死を見る錠。
        #: `_loop` が畳む判断もこの中で行う（`enqueue` が同じ錠で生死を見るので、
        #: 積んだ直後に畳んで仕事が取り残されることがない）。
        self.lock = threading.Lock()
        self._thread = None
        self._pending = set()

    # -- 状態 ---------------------------------------------------------------

    def alive(self) -> bool:
        """背景スレッドが動いているか。"""
        with self.lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def pending(self) -> int:
        """待っている仕事の数（処理中の1件は含まない）。"""
        return self.jobs.qsize()

    def waiting(self, key) -> bool:
        """その鍵の仕事が待ち行列に居るか。`key=` を渡していないときは常に False。"""
        with self.lock:
            return key in self._pending

    # -- 世代 ---------------------------------------------------------------

    def rebind(self, ctx, run=None, write=None) -> "Worker":
        """注入し直した世代の `ctx` と仕事の中身へ繋ぎ替える。自分自身を返す。

        ワーカーはプロセス側に置く（`apply()` のたびに作ると背景スレッドが増える）。
        一方で仕事の中身（`run`）は `apply()` の中の閉包なので、
        置いたままにすると**前の世代のコードが動き続ける**:

        ```python
        store["worker"] = (store.get("worker")
                           or jobs.Worker(ctx, compile_area, name="...")
                           ).rebind(ctx, compile_area, write)
        ```

        繋ぎ替えた時点で走っている1件は、そのまま新しい `run` に切り替わる
        （`run` は取り出すたびに読むため）。パッチの世代管理と同じで、
        **後から当てた方が勝つ**。
        """
        self.ctx = ctx
        if run is not None:
            self.run = run
        if write is not None:
            self.write = write
        return self

    # -- 出し入れ -----------------------------------------------------------

    def enqueue(self, job) -> bool:
        """仕事を積む。積んだら True。

        同じ鍵の仕事が既に待っていれば積まずに False
        （**待っている間に世界が進んでも、こなすのは最新の状態1回で足りる**
        ― 仕事の中身はこなすときに読み直す作りにしておくこと）。

        上限を超えたぶんは**古い方から**捨てる。
        新しい仕事ほど今の遊びに効くので、捨てるなら古い側。
        """
        drop_key = self.key(job) if self.key is not None else None
        dropped = []
        with self.lock:
            if self.key is not None and drop_key in self._pending:
                return False
            while self.jobs.qsize() >= self.max_pending:
                try:
                    old = self.jobs.get_nowait()
                except queue.Empty:
                    break
                self.jobs.task_done()
                if self.key is not None:
                    self._pending.discard(self.key(old))
                dropped.append(old)
            if self.key is not None:
                self._pending.add(drop_key)
            self.jobs.put(job)
            self._wake()
        for old in dropped:
            if self.on_drop is not None:
                # 何を捨てたかは MOD しか書けない（仕事の形を知っているのは
                # そちら）。渡してあるときは、こちらからは何も出さない。
                try:
                    self.on_drop(old)
                except Exception:
                    self.ctx.log_exc("{}: on_drop failed".format(self.label))
            elif self.write is not None:
                self.write("{}: 待ち行列が溢れたので古い仕事を捨てた".format(self.label))
        return True

    def _wake(self) -> None:
        """スレッドが居なければ起こす。**錠の中で呼ぶこと。**"""
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=self._loop,
                                  name="instantale_mod." + self.name,
                                  daemon=True)
        self._thread = thread
        thread.start()

    def _loop(self) -> None:
        """仕事を順番に処理する。`idle` 秒空けば畳む。"""
        while True:
            try:
                job = self.jobs.get(timeout=self.idle)
            except queue.Empty:
                with self.lock:
                    if not self.jobs.empty():
                        # 空振りと `enqueue` がすれ違った。まだ畳まない。
                        continue
                    self._thread = None
                return
            try:
                self.run(job)
            except Exception:
                self.ctx.log_exc("{}: background job failed".format(self.label))
            finally:
                with self.lock:
                    if self.key is not None:
                        self._pending.discard(self.key(job))
                if self.on_done is not None:
                    try:
                        self.on_done(job)
                    except Exception:
                        self.ctx.log_exc("{}: on_done failed".format(self.label))
                self.jobs.task_done()

    # -- 検査から使うもの ---------------------------------------------------

    def drain(self, timeout=10.0) -> bool:
        """待ち行列が捌けるまで待つ。捌けたら True。

        **ゲームの中では呼ばない**（捌けるまでゲームのスレッドが止まる）。
        オフライン検証が「ワーカーが片付けたか」を見るためのもの。
        """
        deadline = time.monotonic() + timeout
        while self.jobs.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self.jobs.unfinished_tasks
