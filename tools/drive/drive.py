# -*- coding: utf-8 -*-
"""動いているゲームの中で手順を1回走らせ、結果を受け取る（開発用。配布物には入らない）。

    python tools/drive/drive.py <手順.py> [--wait 秒] [--args JSON]

手順は `def main(say):` を持つ Python。`say(文字列)` で結果に1行書く。
読む前に、同じフォルダの `lib.py`（画面を押す・待つ部品）と `ARGS`（`--args` の辞書）が名前空間に入る。
手順が `KEEP_OPEN = True` を置いたときは、手順自身が `say("<done>")` で終わりを知らせる（Clock で後から書く手順）。

ゲームのメインスレッド（Kivy の Clock）で走らせるので、画面を触っても落ちない。
結果は `out/drive/<手順>.<時刻>.out` に書かれ、標準出力に写してから消す（`--keep` で残す）。
注入の中身は ASCII に限る（`tools/injector.py` の都合）。作業場の場所に ASCII でない字が入っていると使えない。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import injector  # noqa: E402

# ゲームの `__main__` に名前を残さない（TECH.md §1.1）。全部を1つの関数に入れ、呼んだら消す。
PAYLOAD = r'''
def _iml_drive():
    import json
    import traceback
    from kivy.clock import Clock

    def run():
        out = open({result!r}, "w", encoding="utf-8")

        def say(text):
            if out.closed:
                return
            out.write(str(text) + "\n"); out.flush()
            if text == "<done>":
                out.close()
        ns = {{"__name__": "drive", "ARGS": json.loads({args!r}), "say": say}}
        try:
            exec(open({lib!r}, encoding="utf-8").read(), ns)
            exec(open({script!r}, encoding="utf-8").read(), ns)
            ns["main"](say)
        except Exception:
            say(traceback.format_exc())
        finally:
            if not ns.get("KEEP_OPEN"):
                say("<done>")
    Clock.schedule_once(lambda _dt: run(), 0)
try:
    _iml_drive()
finally:
    del _iml_drive
'''


def main():
    ap = argparse.ArgumentParser(description="run one step inside the running game")
    ap.add_argument("script", help="the step file (a path, or a name in tools/drive)")
    ap.add_argument("--wait", type=float, default=20.0, help="seconds to wait for <done>")
    ap.add_argument("--args", default="{}", help="JSON object handed to the step as ARGS")
    ap.add_argument("--keep", action="store_true", help="keep the result file")
    opts = ap.parse_args()

    script = opts.script if os.path.exists(opts.script) else os.path.join(HERE, opts.script)
    script = os.path.abspath(script)
    if not os.path.exists(script):
        print("ERROR: no such step: {}".format(opts.script))
        return 2
    try:
        args = json.loads(opts.args)
    except ValueError as exc:
        print("ERROR: --args is not JSON: {}".format(exc))
        return 2
    out_dir = os.path.join(ROOT, "out", "drive")
    os.makedirs(out_dir, exist_ok=True)
    result = os.path.join(out_dir, "{}.{}.out".format(os.path.basename(script), int(time.time() * 1000)))
    payload = PAYLOAD.format(result=result, script=script, lib=os.path.join(HERE, "lib.py"),
                             args=json.dumps(args, ensure_ascii=True))
    if not payload.isascii():
        print("ERROR: the paths must be ASCII: {}".format(ROOT))
        return 2

    pids = injector.find_processes(injector.TARGET_EXE)
    if not pids:
        print("ERROR: {} is not running.".format(injector.TARGET_EXE))
        return 1
    if len(pids) > 1:
        # どれに流すかを決めつけない（`injector.py` と同じ）。
        print("ERROR: {} game processes are running: {}".format(
            len(pids), ", ".join(str(pid) for pid, _name in pids)))
        return 1
    rc = injector.inject(pids[0][0], payload.encode("ascii") + b"\0")
    if rc != 0:
        print("ERROR: inject returned {}".format(rc))
        return 1

    deadline = time.time() + opts.wait
    text = None
    while time.time() < deadline:
        if os.path.exists(result):
            with open(result, encoding="utf-8") as fh:
                text = fh.read()
            if "<done>" in text:
                break
        time.sleep(0.3)
    if os.path.exists(result):
        with open(result, encoding="utf-8") as fh:
            text = fh.read()
        if not opts.keep:
            for _ in range(10):   # 書き手が閉じるのを少し待つ（開いている間は Windows が消させない）
                try:
                    os.remove(result)
                    break
                except OSError:
                    time.sleep(0.2)
    print(text if text is not None else "no result")
    return 0


if __name__ == "__main__":
    sys.exit(main())
