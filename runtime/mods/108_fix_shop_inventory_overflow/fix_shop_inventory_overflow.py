# -*- coding: utf-8 -*-
"""修正: 売買画面（twin inventory）を開くと IndexError で落ちるのを直す。

## main_024 で本体が取り込んだ扱いにして降ろした（VERIFICATION.md §3.8.1）

main_024 のアナウンスにこの件（売買画面）が挙がっている。
ただし他の5本と違い、印での判定は付いていない。
このクラッシュは能動的に起こせないので、本体が直したのか、
この MOD が防いでいるのかを区別できない（GAME.md の main_024 の表）。
救済経路も `out/inventory.log` の正常サンプル
192 件の間で一度も走っていない（VERIFICATION.md §3.8）。

`mod.json` に `superseded` を入れてデバッグモード限定にしてある。
コードはそのままなので、デバッグモードを入れれば一覧の元の位置に戻る。
以下は当時の記録。

## 原因（GAME.md §2.13 / VERIFICATION_LOG.md §2.16）

会話から売買に入った瞬間、`InventoryGrid.occupy_slots` が
`IndexError: list index out of range` で落ちる。

* `occupy_slots` は `grid_x/grid_y` から始めてアイテムの占有マスを走査する。
  **縦2マス以上のアイテムが最下段に置かれ、1マスはみ出した**ときに範囲外へ出る。
* つまり `place_existing_item` は**置ける場所かどうかを確かめずに**
  `occupy_slots` を呼んでいる。
  クラスには `is_valid_placement()` があるのに、この経路だけ通っていない。
* `place_existing_item` が受け取るのはピクセル座標。
  「既存の位置をそのまま復元する」経路なので、**元いたグリッドと売買画面のグリッドで寸法が違う**と、
  そのままでは収まらない位置になり得る。

`toggle_twin_inventory_visibility` は所持品を順に並べている最中で、
これが Kivy の property dispatch → Clock コールバックの中で起きるため、
例外がそのままアプリのループまで抜けてゲームごと落ちる。

## 直し方

座標をこちらで計算し直さない。
グリッドの寸法もマスのサイズも実測していない以上、
位置を発明すればレイアウトの仕様をこちらで勝手に決めることになる。

代わりに、はみ出したときは**ゲーム自身の「新しく置く」経路に流す**。
`InventoryGrid` は `find_placement_position(w, h)` で空きを探し、
`place_new_item(item)` で置く手段を持っている。
店の在庫を初めて並べるときにゲーム自身が使っている道具。
復元位置が使えないアイテムを、そこへ渡すだけ。
（`101_` で `clamp_npc_difficulty_value` を当てたのと同じ形）

途中まで書き込まれた占有マスは `item.clear_current_slots()` で片付ける。
これもゲーム自身の後始末 API で、
`occupy_slots` が最初の範囲外インデックスで落ちる前に埋めたマスが残るのを防ぐ。

## 記録

落ちた時のグリッドとアイテムの実寸を `out/inventory.log` に出す。
ここが埋まれば「そもそもなぜ復元位置がはみ出すのか」（グリッドの列数が画面ごとに違うのか、
ピクセル→マスの変換が別スケールなのか）を、座標を推測せずに次の段で詰められる。
成功した呼び出しも見本として同じ形式で残す（グリッドの種類ごとに1件、`SAMPLE_OK` 件まで）。
正常時の寸法が無いと異常の判定ができない。

## 版の記録

版2: 成功の見本を apply ごとに最初の30件まで書いていた。
遅れて当て直す boot が1回の起動で3〜4回走り、そのたびに数え直していたので、
`inventory.log` が約40日で3,205行・約1.5MB になり、全行が `ok` だった（はみ出し・諦めは0件）。
見本の控えを `sys` に置いて1プロセスで数え、グリッドの種類（situation・cols・rows）ごとに
最初の1件だけ書くようにした。
注入のときにログが世代送りされたら（ファイルが入れ替わったら）、新しいログにもう一度書く。
行に時刻が無かったので、`ctx.logger` の既定（時刻付き）に揃えた。
はみ出し（`OVERFLOW`）と諦め（`GIVEUP`）の行は今までどおり毎回書く。
"""

import os
import sys

from instantale_modloader import frames, patch

LOG_BASENAME = "inventory.log"

# 正常に置けた呼び出しを何件まで記録するか（1プロセス。ログが入れ替わったら数え直す）。
# 比較用の下地なので少しでいい。
# グリッドの種類（`sample_kind`）ごとに1件で、実ログの種類は7通りだった。
SAMPLE_OK = 10

# 見本の控え `{"kinds": 書いたグリッドの種類, "file": ログのファイルの身元}`。
# 1プロセスに1組（遅れて当て直す boot と注入し直しで数え直さない）。
# MOD のモジュールは boot のたびに読み直されるので、モジュール変数では持てない。
SAMPLE_STORE_ATTR = "_instantale_shop_inventory_samples"

# グリッド／アイテムから読む属性。
# dir() を全部舐めると Kivy の property を大量に評価することになるので、
# 寸法に関係するものだけ明示する。
# 名前が違って空振りしても、下の _unknown_dims() が dir() で拾い直す。
GRID_ATTRS = ("cols", "rows", "situation", "slot_size", "spacing", "size", "pos")
ITEM_ATTRS = ("item_id", "width_slots", "height_slots", "grid_x", "grid_y",
              "pos", "size", "current_slots")


def sample_store():
    """見本の控え。1プロセスに1組。"""
    store = getattr(sys, SAMPLE_STORE_ATTR, None)
    if not isinstance(store, dict) or not isinstance(store.get("kinds"), set):
        store = {"kinds": set(), "file": None}
        setattr(sys, SAMPLE_STORE_ATTR, store)
    return store


def file_id(path):
    """ログのファイルの身元。無ければ None（世代送りで名前を変えられた直後など）。"""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def log_replaced(store, path):
    """前の apply の後で `path` が入れ替わった（世代送り・削除）なら True。控えは今の身元へ進める。

    世代送りは注入のとき（apply より前）にしか起きないので、apply の頭で見れば足りる。
    """
    now = file_id(path)
    before = store.get("file")
    store["file"] = now
    return before is not None and before != now


def sample_kind(grid):
    """見本を分ける単位。売買・戦利品・仲間との受け渡しなどで寸法が違う。"""
    return tuple(frames.repr_value(frames.attr(grid, k))
                 for k in ("situation", "cols", "rows"))


def apply(ctx):
    # 無ければローダの保留に積んで降りる（`patch.await_module`）。来たらローダが当て直す。
    # `sys.modules` を見て自分で降りると保留に載らず、ほかに保留が無い構成ではその起動で一度も当たらない。
    new_hud = patch.await_module("scripts.hud.new_hud", "InventoryGrid")
    if new_hud is None:
        ctx.log("scripts.hud.new_hud not loaded yet; waiting for it")
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

    log_line = ctx.logger(LOG_BASENAME)
    store = sample_store()
    kinds = store["kinds"]
    if log_replaced(store, ctx.out_path(LOG_BASENAME)):
        kinds.clear()                       # 新しいログにも見本を残す
    state = {"recovered": 0, "failed": 0}

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
            log_line("{:<9} {}".format(kind, "  ".join(parts)))
        except Exception:
            # 記録のせいでゲームを落とさない。
            # ここは常に握り潰す。
            ctx.log_exc("inventory log write failed")

    @ctx.wrap("scripts.hud.new_hud:InventoryGrid.place_existing_item")
    def place_existing_item(orig, self, item, *args, **kwargs):
        try:
            result = orig(self, item, *args, **kwargs)
        except IndexError as exc:
            # 報告されたクラッシュそのもの。
            # ここから先は回復処理。
            state["recovered"] += 1
            write("OVERFLOW", self, item, note=repr(str(exc)))
            ctx.log("place_existing_item overflowed the grid ({}); "
                    "falling back to the game's own placement".format(exc))
        else:
            if len(kinds) < SAMPLE_OK:
                try:
                    kind = sample_kind(self)
                    if kind not in kinds:
                        kinds.add(kind)
                        write("ok", self, item)
                except Exception:
                    ctx.log_exc("inventory sample failed")
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
