# -*- coding: utf-8 -*-
"""ワールド別設定画面（`mod.json` の "tool"）。中身は `tools/modtool.py` に在る。

ローダの設定画面（`tools/gui.py`）が「設定…」でこのファイルを別プロセスで開く。
画面そのもの（一括設定 / ワールド個別設定の2段）は宣言駆動で MOD 固有のコードが
1行も要らないので、共有の土台へ移した（TECH.md §3.12.1）。
項目の一覧・型・既定値・上下限・選択肢は `mod.json` の "settings" から読む。

**このファイルは `__file__` 以外に何も書かない。**
だから `130_currency_unit` と `314_area_move_custom` で同じもので、
`tools/tests/test_world_settings_tool.py` が `filecmp` で同一性を検査している。
中身を足したくなったら、それは MOD 固有の判断なので `modtool` ではなくこちらへ
― ただしそのとき2本の同一性は崩れる。
"""

import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする。
# `IML_ROOT` が指す先に `tools/` が無いこと（オフラインの検査）と、
# 環境変数の無い直接起動の両方があるので、3つ上も候補に入れる。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)

import modtool  # noqa: E402


def main():
    modtool.world_settings_main(MOD_DIR)


if __name__ == "__main__":
    main()
