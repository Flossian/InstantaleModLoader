# -*- coding: utf-8 -*-
r"""ゲームが残した LLM の入出力の記録（`<ゲームのフォルダ>\output_data\<世界>\<主人公>\<manager>\N.json`）を読む（ゲームの外で走る）。

    python tools/drive/records.py conversation_resolver            いちばん新しい1件
    python tools/drive/records.py conversation_starter -n 3        新しい方から3件
    python tools/drive/records.py conversation_resolver --system   system の頼み文も出す（長い）

書き出すのは user / assistant の行と response。system は既定で省く（世界観などで長い）。
標準出力の字化けを避けるため、結果は `out/drive/records_<manager>.txt` にも書く。
ゲームのフォルダは `IML_GAME_DIR`、無ければ設定画面の `settings\gui.json` の `game_path` の親。
世界と主人公が分からない記録は `unknown\unknown\` に入る（ゲームの都合）ので、全部の世界から新しい順に拾う。
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_tool_screens as cts  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="dump the game's latest LLM records of one manager")
    ap.add_argument("manager")
    ap.add_argument("-n", type=int, default=1, help="how many (newest first)")
    ap.add_argument("--system", action="store_true", help="include system messages")
    opts = ap.parse_args()
    game = os.environ.get("IML_GAME_DIR") or cts.game_dir()
    if not game:
        print("ERROR: set IML_GAME_DIR (see tools/drive/README.md)")
        return 2
    files = glob.glob(os.path.join(game, "output_data", "*", "*", opts.manager, "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    lines = []
    for path in files[:opts.n]:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        lines.append("######## {}".format(os.path.relpath(path, os.path.join(game, "output_data"))))
        for turn in data.get("messages") or []:
            if turn.get("role") == "system" and not opts.system:
                continue
            lines.append("--- {}: {}".format(turn.get("role"), turn.get("content")))
        lines.append("=== {}".format(json.dumps(data.get("response"), ensure_ascii=False)))
    if not files:
        lines.append("no records for {}".format(opts.manager))
    text = "\n".join(lines) + "\n"
    out = os.path.join(ROOT, "out", "drive", "records_{}.txt".format(opts.manager))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text, end="")
    print("(also written to {})".format(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
