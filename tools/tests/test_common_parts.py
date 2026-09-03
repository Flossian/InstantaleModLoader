# -*- coding: utf-8 -*-
"""共通部品（`state.WorldStore` / `jobs.Worker` / `llm` の読み取り / `ui.game_day`）。

写して回っていたものをローダへ寄せた4つ。
ここで見るのは**寄せた先が、寄せる前の9本ぶんの要求を全部満たすか**:

| 見るもの | なぜ |
|---|---|
| `WorldStore` が読めないファイルを黙って空へ倒さない | 倒すと次の書き込みが空に近い正本を無傷で作る（TECH.md §3.11.1） |
| `own=False` がフォルダを作らない | 相手を切っている人の `state/` に空のフォルダを置かない（§3.11） |
| `fresh=True` が相手の更新を拾う | `403_` / `404_` が `311_` の控えを読む経路 |
| `Worker` が溢れたら古い方から捨てる | 推論が返らない間に会話を続けても際限なく溜めない |
| `Worker` が同じ鍵を二度積まない | `317_` / `321_` の編纂の重複除け |
| `Worker` が1件の失敗で止まらない | 止まると遊んでいる側からは何も起きなくなる |
| `llm.parse_json` が囲みと前置きを越える | 5本が各自で剥がしていて、3通りに枝分かれしていた |
| `llm.truthy` が両方の倒し方を持つ | `changed` は True へ、`content_violation` は False へ |
| `ui.game_day` がロード中も読める | 受け皿を持っていたのは `312_` だけだった |
| `ui.pressed_entry` がページ送りの枠を None にする | 地図の値が `'next'` の枠を添字に落とすと、自前の一覧を出す MOD が「次」を横取りする（`325_` で実際に起きた） |

ゲームは要らない（偽の `ctx` を渡す）。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

from instantale_modloader import jobs, llm, ui                      # noqa: E402
from instantale_modloader import state as state_mod                 # noqa: E402
from instantale_modloader.state import world_filename               # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    if ok:
        print("  ok   " + label)
    else:
        FAILURES.append(label)
        print("  FAIL " + label + ((" :: " + str(detail)) if detail else ""))


class FakeCtx(object):
    """`ctx` のうち `WorldStore` / `Worker` が使う分だけ。"""

    def __init__(self, root):
        self.state_dir = os.path.join(root, "state")
        self.errors = []
        self.reads = []

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        self.reads.append(path)
        if not os.path.exists(path):
            return default
        try:
            with io.open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            # 本物と同じ振る舞い ―「在るのに読めない」は記録してから倒す。
            self.errors.append("read_json failed: " + path)
            return default

    def write_json(self, path, data, *, indent=1):
        try:
            with io.open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=indent)
            return True
        except Exception:
            return False

    def log_exc(self, msg):
        self.errors.append(msg)


# ---------------------------------------------------------------- WorldStore

def test_world_store(root):
    print("state.WorldStore")
    ctx = FakeCtx(root)
    store = state_mod.WorldStore(ctx, "my_mod")

    check("初回は空", store.load("灰の街") == {})
    bucket = store.load("灰の街")
    bucket["n1"] = {"profile": "無口"}
    check("読んだ dict がそのまま控え（書き換えたら効く）",
          store.load("灰の街") is bucket)
    check("書けた", store.save("灰の街"))

    path = os.path.join(ctx.state_dir, "my_mod", world_filename("灰の街"))
    with io.open(path, encoding="utf-8") as fh:
        check("ファイルの中身", json.load(fh) == {"n1": {"profile": "無口"}})

    store.forget()
    check("読み直しても同じ", store.load("灰の街") == {"n1": {"profile": "無口"}})

    # 世界が違えばファイルも別。
    store.save("鉄の港", {"n2": {}})
    check("世界の一覧", store.worlds() == sorted(["灰の街", "鉄の港"]))

    # 壊れたファイルは「無い」と同じ扱いにしない。
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("{壊れた")
    store.forget()
    before = len(ctx.errors)
    check("壊れていれば空に倒すが、倒したことを残す",
          store.load("灰の街") == {} and len(ctx.errors) > before)

    # 並びを固定する口。
    ordered = state_mod.WorldStore(
        ctx, "ordered_mod",
        order=lambda b: {k: b[k] for k in sorted(b)})
    ordered.save("灰の街", {"b": 2, "a": 1})
    with io.open(os.path.join(ctx.state_dir, "ordered_mod",
                              world_filename("灰の街")), encoding="utf-8") as fh:
        check("order= が書く前に並べ直す", list(json.load(fh)) == ["a", "b"])

    # 古い形の移行。
    calls = []

    def normalize(bucket):
        calls.append(dict(bucket))
        if "old" in bucket:
            return {"new": bucket["old"]}, True
        return bucket, False

    migrating = state_mod.WorldStore(ctx, "migrate_mod", normalize=normalize)
    ctx.write_json(migrating.path("灰の街"), {"old": 1})
    check("normalize= が読んだ形を直す", migrating.load("灰の街") == {"new": 1})
    migrating.forget()
    check("直した形は書き戻してある（2回目は直す必要が無い）",
          migrating.load("灰の街") == {"new": 1} and calls[-1] == {"new": 1})


def test_foreign_store(root):
    print("state.WorldStore（他の MOD の控えを読む）")
    ctx = FakeCtx(root)
    foreign = state_mod.WorldStore(ctx, "someone_else", own=False)

    dir_path = os.path.join(ctx.state_dir, "someone_else")
    check("読んでもフォルダを作らない",
          foreign.load("灰の街") == {} and not os.path.exists(dir_path))

    raised = False
    try:
        foreign.save("灰の街", {"x": 1})
    except ValueError:
        raised = True
    check("読むだけの控えには書かせない", raised)

    # 相手が書いた後は読めるようになる。
    owner = state_mod.WorldStore(ctx, "someone_else")
    owner.save("灰の街", {"n1": {"profile": "初版"}})
    check("相手が書けば読める",
          foreign.load("灰の街", fresh=True) == {"n1": {"profile": "初版"}})

    # 相手が書き換えたら読み直す。**同じ秒に書いても**気付くこと
    # （更新時刻だけでなく大きさも見ている）。
    owner.save("灰の街", {"n1": {"profile": "第二版"}, "n2": {}})
    check("相手の書き換えを拾う（fresh=True）",
          foreign.load("灰の街", fresh=True)["n1"]["profile"] == "第二版")
    check("fresh を渡さなければ読み直さない",
          foreign.load("灰の街") is foreign.load("灰の街"))


# -------------------------------------------------------------------- Worker

def test_worker(root):
    print("jobs.Worker")
    ctx = FakeCtx(root)
    done = []
    gate = threading.Event()

    def run(job):
        gate.wait(5.0)
        done.append(job)

    worker = jobs.Worker(ctx, run, name="test_worker", max_pending=2, idle=1.0)
    check("積む前はスレッドが居ない", not worker.alive())

    worker.enqueue({"n": 1})     # これは走り出して gate で止まる
    worker.enqueue({"n": 2})
    worker.enqueue({"n": 3})
    worker.enqueue({"n": 4})     # 上限2を超えたので {"n": 2} が落ちる
    check("待ち行列は上限まで", worker.pending() == 2)
    gate.set()
    check("捌け終わる", worker.drain(5.0))
    check("溢れたぶんは古い方から捨てた（残るのは 1・3・4）",
          [job["n"] for job in done] == [1, 3, 4], done)

    # 同じ鍵は二度積まない。
    seen = []
    hold = threading.Event()

    def run2(job):
        hold.wait(5.0)
        seen.append(job)

    keyed = jobs.Worker(ctx, run2, name="test_keyed", idle=1.0,
                        key=lambda job: job["area"])
    keyed.enqueue({"area": "a"})          # 走り出して hold で止まる
    check("走り出した1件は待ち行列から出ている", keyed.pending() == 0)
    keyed.enqueue({"area": "b"})
    check("違う鍵は積む", keyed.enqueue({"area": "c"}))
    check("待っている鍵は積まない", not keyed.enqueue({"area": "b"}))
    check("待っているか答えられる", keyed.waiting("b") and not keyed.waiting("z"))
    hold.set()
    check("捌け終わる", keyed.drain(5.0))
    check("こなしたのは3件", [job["area"] for job in seen] == ["a", "b", "c"], seen)
    check("片付いた鍵はもう待っていない", not keyed.waiting("b"))

    # 1件の失敗で止まらない。
    ran = []

    def boom(job):
        ran.append(job)
        if job == "bad":
            raise RuntimeError("落ちる仕事")

    ctx.errors[:] = []
    hardy = jobs.Worker(ctx, boom, name="test_hardy", label="hardy", idle=1.0)
    hardy.enqueue("bad")
    hardy.enqueue("good")
    check("捌け終わる", hardy.drain(5.0))
    check("失敗した次の仕事もこなす", ran == ["bad", "good"], ran)
    check("失敗は記録に残る", any("hardy" in e for e in ctx.errors), ctx.errors)

    # 仕事が無ければ畳み、次の仕事で起き直す。
    quick = jobs.Worker(ctx, lambda job: None, name="test_idle", idle=0.05)
    quick.enqueue(1)
    quick.drain(5.0)
    deadline = time.monotonic() + 5.0
    while quick.alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    check("仕事が無ければ自分で畳む", not quick.alive())
    quick.enqueue(2)
    check("次の仕事で起き直す", quick.alive() or quick.drain(5.0))

    # 世代の繋ぎ替え。
    later = []
    quick.rebind(ctx, run=lambda job: later.append(job))
    quick.enqueue(3)
    quick.drain(5.0)
    check("rebind で新しい世代の仕事に切り替わる", later == [3], later)


# ----------------------------------------------------------------------- llm

def test_llm_reading():
    print("llm.strip_fence / parse_json / truthy")
    payload = {"changed": True, "profile": "無口な傭兵"}
    body = json.dumps(payload, ensure_ascii=False)

    check("素の JSON", llm.parse_json(body) == payload)
    check("囲み付き", llm.parse_json("```json\n" + body + "\n```") == payload)
    check("札が大文字でも数字付きでも剥がす",
          llm.parse_json("```JSON5\n" + body + "\n```") == payload)
    check("札の無い囲み", llm.parse_json("```\n" + body + "\n```") == payload)
    check("前置きと後書きを越える",
          llm.parse_json("こちらです。\n" + body + "\n以上です。") == payload)
    check("辞書はそのまま", llm.parse_json(payload) == payload)
    check("読めなければ None",
          llm.parse_json("霧が晴れました。") is None
          and llm.parse_json("{壊れた json") is None
          and llm.parse_json(None) is None)
    check("配列は受けない（1つの辞書を返す約束）", llm.parse_json("[1, 2]") is None)

    check("囲みの中の素の文章は本文だけ残る",
          llm.strip_fence("```\nこんにちは\n世界\n```") == "こんにちは\n世界")
    check("囲みが無ければそのまま", llm.strip_fence("  ただの文  ") == "ただの文")
    check("文字列でなければ空", llm.strip_fence(None) == "")

    check("真偽値はそのまま", llm.truthy(True) and not llm.truthy(False))
    check("`false` と書く相手にも耐える",
          not llm.truthy("false") and not llm.truthy("なし")
          and not llm.truthy("変更なし"))
    check("`true` 系は両方の倒し方で True",
          llm.truthy("yes") and llm.truthy("yes", unknown=False))
    check("判らない語は unknown が決める",
          llm.truthy("よく分からない語")
          and not llm.truthy("よく分からない語", unknown=False))
    check("None と 0 は False", not llm.truthy(None) and not llm.truthy(0))


# ------------------------------------------------------------------ game_day

def test_game_day():
    print("ui.game_day")
    world = types.SimpleNamespace(days_elapsed=12)
    check("app から", ui.game_day(types.SimpleNamespace(world=world)) == 12)
    check("World そのものから（`World.__init__` を包む場面）",
          ui.game_day(world) == 12)
    check("float は int にする",
          ui.game_day(types.SimpleNamespace(world=types.SimpleNamespace(
              days_elapsed=7.0))) == 7)
    check("ロードの途中は world_dict から拾う",
          ui.game_day(types.SimpleNamespace(
              world=None, world_dict={"world_data": {"days_elapsed": 3}})) == 3)
    check("真偽値は日数ではない",
          ui.game_day(types.SimpleNamespace(world=types.SimpleNamespace(
              days_elapsed=True))) is None)
    check("読めなければ None", ui.game_day(object()) is None)


def test_pressed_entry():
    print("ui.pressed_entry")
    buttons = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    plain = types.SimpleNamespace(buttons=buttons)
    check("地図が無ければ添字そのまま", ui.pressed_entry(plain, 1) is buttons[1])
    mapped = types.SimpleNamespace(buttons=buttons, display_button_map=[2, 0])
    check("地図があれば地図で引き直す", ui.pressed_entry(mapped, 0) is buttons[2])
    # 1ページに収まらないとき、最後の枠は `次` で地図の値は 'next'（`206_` の記録。GAME.md §2.2）。
    paged = types.SimpleNamespace(buttons=buttons, display_button_map=[0, "next"])
    check("ページ送りの枠（整数でない）は None ＝ ボタンではない",
          ui.pressed_entry(paged, 1) is None)
    check("地図が bool でも None", ui.pressed_entry(
        types.SimpleNamespace(buttons=buttons, display_button_map=[True]), 0) is None)
    check("地図より外の添字は添字そのまま", ui.pressed_entry(mapped, 2) is buttons[2])
    check("範囲外は None", ui.pressed_entry(plain, 9) is None)


def main():
    root = tempfile.mkdtemp(prefix="instantale_common_")
    try:
        test_world_store(root)
        test_foreign_store(os.path.join(root, "foreign"))
        test_worker(root)
        test_llm_reading()
        test_game_day()
        test_pressed_entry()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if FAILURES:
        print("\n失敗: " + ", ".join(FAILURES))
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
