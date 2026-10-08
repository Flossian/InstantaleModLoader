# -*- coding: utf-8 -*-
"""ローダの `llm.rebind_cloud_resources` をゲーム抜きで通す。

    python tools/tests/test_cloud_resource_rebind.py

確認するもの:

  掴み直し … 資源が作られた後にクラスの `post` を包み直すと、資源は前の包みを呼ぶ。
             掴み直させると今の包みを呼ぶ。数えた数は掴み直した手の数
  そのまま … 今の手を掴んでいる資源・SDK の資源でないもの・`_client` の無いものは触らない
  SDK 無し … SDK を読み込んでいなければ 0
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import llm                    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("SDK 無し")
saved = {name: sys.modules.get(name) for name in llm.SDK_RESOURCE_MODULES}
for name in llm.SDK_RESOURCE_MODULES:
    sys.modules.pop(name, None)
check("SDK を読み込んでいなければ 0", llm.rebind_cloud_resources() == 0)


class Client(object):
    def get(self):
        return "get"

    def post(self):
        return "plain"

    def patch(self):
        return "patch"

    def put(self):
        return "put"

    def delete(self):
        return "delete"

    def get_api_list(self):
        return "list"


class SyncAPIResource(object):
    """SDK の資源と同じく、作られたときにクライアントの手を掴む。"""

    def __init__(self, client):
        self._client = client
        self._get = client.get
        self._post = client.post
        self._patch = client.patch
        self._put = client.put
        self._delete = client.delete
        self._get_api_list = client.get_api_list


class Messages(SyncAPIResource):
    pass


class NotResource(object):
    def __init__(self, client):
        self._client = client
        self._post = client.post


module = types.ModuleType("anthropic._resource")
module.SyncAPIResource = SyncAPIResource
sys.modules["anthropic._resource"] = module

try:
    client = Client()
    plain_post = Client.post

    def old_layer(self):
        return "old"

    Client.post = old_layer
    messages = Messages(client)                 # 前の世代の包みを掴む
    outsider = NotResource(client)

    def new_layer(self):
        return "new"

    Client.post = new_layer                      # 注入し直した
    print("掴み直し")
    check("掴み直す前は前の包みを呼ぶ", messages._post() == "old", messages._post())
    count = llm.rebind_cloud_resources()
    check("掴み直した数は post の1つ", count == 1, count)
    check("掴み直した後は今の包みを呼ぶ", messages._post() == "new", messages._post())
    check("ほかの手は同じ関数のまま", messages._get() == "get")

    print("そのまま")
    check("2回目は 0", llm.rebind_cloud_resources() == 0)
    check("SDK の資源でないものは触らない", outsider._post() == "old", outsider._post())
    Client.post = plain_post                     # ローダを外して素に戻した
    check("素に戻したら素の手を掴み直す", llm.rebind_cloud_resources() == 1
          and messages._post() == "plain", messages._post())
    bare = Messages.__new__(Messages)
    check("_client の無い資源でも落ちない", llm.rebind_cloud_resources() == 0)
    del bare
finally:
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
