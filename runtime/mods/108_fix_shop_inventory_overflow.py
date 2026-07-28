# -*- coding: utf-8 -*-
"""修正: 売買画面（twin inventory）を開くと IndexError で落ちるのを直す。

## 実測（2026-07-27T21:01:41、`out/live_crashes.log` の MAIN CRASH）

会話から売買に入った瞬間に落ちた。トレースの末端はこう:

```
instantale.py:1692   InstantaleApp.toggle_twin_inventory_window   situation='shop'
new_hud.py:2661      InstanTaleHUD.toggle_twin_inventory_visibility   item_id='item_1'
new_hud.py:379       InventoryGrid.place_existing_item   new_x=1422.7  new_y=1052.855
new_hud.py:410       InventoryGrid.occupy_slots          grid_x=1 grid_y=5 x=1 y=6
                     IndexError: list index out of range   slot_index=25
```

読み取れること:

* `occupy_slots` は `grid_x/grid_y` から始めてアイテムの占有マスを走査している。
  `x` が `grid_x` のまま（幅1）で `y` が `grid_y+1` に進んだところで落ちているので、
  **縦2マス以上のアイテムが最下段に置かれ、1マスはみ出した**状態。
* `slot_index=25` は `self.slots` の範囲外。つまり `place_existing_item` は
  **置ける場所かどうかを確かめずに** `occupy_slots` を呼んでいる。
  クラスには `is_valid_placement()` があるのに、この経路だけ通っていない。
* `place_existing_item` が受け取っているのはピクセル座標（1422.7, 1052.855）。
  「既存の位置をそのまま復元する」経路なので、**元いたグリッドと売買画面の
  グリッドで寸法が違う**と、そのままでは収まらない位置になり得る。

`toggle_twin_inventory_visibility` は所持品を順に並べている最中で、
これが Kivy の property dispatch → Clock コールバックの中で起きるため、
例外がそのままアプリのループまで抜けてゲームごと落ちる。

## 直し方

**座標をこちらで計算し直さない。** グリッドの寸法もマスのサイズも実測していない
以上、位置を発明すればレイアウトの仕様をこちらで勝手に決めることになる。

代わりに、はみ出したときは**ゲーム自身の「新しく置く」経路に流す**。
`InventoryGrid` は `find_placement_position(w, h)` で空きを探し、
`place_new_item(item)` で置く手段を持っている ― 店の在庫を初めて並べるときに
ゲーム自身が使っている道具。復元位置が使えないアイテムを、そこへ渡すだけ。
（`101_` で `clamp_npc_difficulty_value` を当てたのと同じ形）

途中まで書き込まれた占有マスは `item.clear_current_slots()` で片付ける。
これもゲーム自身の後始末 API で、`occupy_slots` が最初の範囲外インデックスで
落ちる前に埋めたマスが残るのを防ぐ。

## 記録

落ちた時のグリッドとアイテムの実寸を `out/inventory.log` に出す。
ここが埋まれば「そもそもなぜ復元位置がはみ出すのか」（グリッドの列数が
画面ごとに違うのか、ピクセル→マスの変換が別スケールなのか）を、
座標を推測せずに次の段で詰められる。成功した呼び出しも最初の
`SAMPLE_OK` 件だけ同じ形式で残す ― 正常時の寸法が無いと異常の判定ができない。
"""

import sys

from instantale_modloader import frames

NAME = "fix: IndexError when the shop's twin inventory restores an out-of-range item"

LOG_BASENAME = "inventory.log"

# 正常に置けた呼び出しを何件記録するか。比較用の下地なので少しでいい。
SAMPLE_OK = 30

# グリッド／アイテムから読む属性。dir() を全部舐めると Kivy の property を
# 大量に評価することになるので、寸法に関係するものだけ明示する。
# 名前が違って空振りしても、下の _unknown_dims() が dir() で拾い直す。
GRID_ATTRS = ("cols", "rows", "situation", "slot_size", "spacing", "size", "pos")
ITEM_ATTRS = ("item_id", "width_slots", "height_slots", "grid_x", "grid_y",
              "pos", "size", "current_slots")


def apply(ctx):
    new_hud = sys.modules.get("scripts.hud.new_hud")
    if new_hud is None:
        ctx.log("scripts.hud.new_hud not loaded; skipping", level="WARN")
        return

    grid_cls = getattr(new_hud, "InventoryGrid", None)
    if grid_cls is None:
        ctx.log("InventoryGrid not found; skipping", level="WARN")
        return

    # 逃げ道になる2つのメソッドが本当に在るか、当てる前に確かめる。
    # 無ければこの修正の前提（ゲーム自身の配置経路に流す）が成り立たない。
    missing = [name for name in ("place_new_item", "find_placement_position")
               if frames.attr(grid_cls, name) is frames.MISSING]
    if missing:
        ctx.log("InventoryGrid lacks {}; the fallback would not work, skipping".format(
            ", ".join(missing)), level="WARN")
        return
    ctx.log("InventoryGrid: place_new_item / find_placement_position / "
            "is_valid_placement = {}".format(
                [frames.attr(grid_cls, n) is not frames.MISSING
                 for n in ("place_new_item", "find_placement_position",
                           "is_valid_placement")]))

    log_path = ctx.out_path(LOG_BASENAME)
    state = {"ok": 0, "recovered": 0, "failed": 0}

    def write(kind, grid, item, note=""):
        """グリッドとアイテムの実寸を1行で残す。"""
        try:
            parts = ["{}={}".format(k, frames.repr_value(frames.attr(grid, k)))
                     for k in GRID_ATTRS]
            slots = frames.attr(grid, "slots")
            parts.append("len(slots)={}".format(
                len(slots) if isinstance(slots, (list, tuple)) else frames.repr_value(slots)))
            parts += ["item.{}={}".format(k, frames.repr_value(frames.attr(item, k)))
                      for k in ITEM_ATTRS]
            if note:
                parts.append("note=" + note)
            line = "{:<9} {}\n".format(kind, "  ".join(parts))
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            # 記録のせいでゲームを落とさない。ここは常に握り潰す。
            ctx.log_exc("inventory log write failed")

    @ctx.wrap("scripts.hud.new_hud:InventoryGrid.place_existing_item")
    def place_existing_item(orig, self, item, *args, **kwargs):
        try:
            result = orig(self, item, *args, **kwargs)
        except IndexError as exc:
            # 報告されたクラッシュそのもの。ここから先は回復処理。
            state["recovered"] += 1
            write("OVERFLOW", self, item, note=repr(str(exc)))
            ctx.log("place_existing_item overflowed the grid ({}); "
                    "falling back to the game's own placement".format(exc))
        else:
            if state["ok"] < SAMPLE_OK:
                state["ok"] += 1
                write("ok", self, item)
            return result

        # occupy_slots は範囲外に当たる前に何マスか埋めている可能性がある。
        # ゲーム自身の後始末 API でそれを戻してから置き直す。
        clear = frames.attr(item, "clear_current_slots")
        if clear is not frames.MISSING:
            try:
                clear()
            except Exception:
                ctx.log_exc("clear_current_slots failed; placing anyway")

        try:
            return self.place_new_item(item)
        except Exception as exc:
            # ここまで来たら置き場所が無い（グリッドが満杯など）。
            # 例外を通すと元と同じくゲームが落ちるので、このアイテムだけ諦める。
            # 売買画面は開き、残りのアイテムは並ぶ。
            state["failed"] += 1
            state["recovered"] -= 1
            write("GIVEUP", self, item, note="{}: {}".format(type(exc).__name__, exc))
            ctx.log("place_new_item also failed for {!r} ({}); "
                    "leaving this item unplaced".format(
                        frames.attr(item, "item_id"), exc), level="WARN")
            return None

    ctx.log("watching InventoryGrid.place_existing_item; geometry goes to out/{}".format(
        LOG_BASENAME))
