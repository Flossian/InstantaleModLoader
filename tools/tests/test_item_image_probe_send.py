# -*- coding: utf-8 -*-
"""アイテム画像の外見文の試し（`tools/item_image_probe.py`）が Claude へ送る中身を、送らずに確かめる。

    python tools/tests/test_item_image_probe_send.py

  system … 1つ目の型の指示は output_config へ移し、残りの system は全部つなぐ（`effort_probe` と同じ）。
           最後の1つだけにすると、system が3つ以上ある記録で間の指示が落ちる
           型の指示が先頭に無い記録（Claude で動いた回）は、先頭の system も残す

送信口（`136_` の差し替え）は偽物に替え、組んだ本文だけを受け取る。API 鍵は使わない。
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import item_image_probe as probe                       # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Reply(object):
    raw = {"usage": {}, "model": "m", "stop_reason": "end_turn"}
    content = []


def sent_body(messages):
    """`ClaudeSender.generate` が送る本文。`__init__`（鍵と `136_` の読み込み）は通さない。"""
    seen = {}
    sender = object.__new__(probe.ClaudeSender)
    sender.usage = collections.Counter()

    def post(_send, _client, _path, body=None, options=None):
        seen["body"] = body
        return Reply()

    sender.post = post
    sender.generate({"messages": messages}, {"type": "object", "properties": {}})
    return seen["body"]


FMT = "必ず日本語で、" + probe.FORMAT_HEAD + ": {...}"
print("[system]")
body = sent_body([{"role": "system", "content": FMT}, {"role": "system", "content": "世界の説明"},
                  {"role": "system", "content": "品の決まり"}, {"role": "user", "content": "＜生成＞"}])
check("**型の指示の後の system を全部つなぐ**", body["system"] == "世界の説明\n\n品の決まり", body["system"])
check("型の指示は system に入れない", probe.FORMAT_HEAD not in body["system"], body["system"])
body = sent_body([{"role": "system", "content": FMT}, {"role": "system", "content": "頼み文"}])
check("2つなら2つ目（今までどおり）", body["system"] == "頼み文", body["system"])
body = sent_body([{"role": "system", "content": "世界の説明"}, {"role": "system", "content": "品の決まり"},
                  {"role": "user", "content": "＜生成＞"}])
check("型の指示が先頭に無ければ先頭も残す", body["system"] == "世界の説明\n\n品の決まり", body["system"])


print("\n失敗 {} 件".format(len(failures)))
for name in failures:
    print("  - " + name)
if failures:
    sys.exit(1)
print("すべて通った")
