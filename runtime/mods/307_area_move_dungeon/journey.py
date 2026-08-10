# -*- coding: utf-8 -*-
"""進行中の道中の控え（`state/road_travel.json`）。**ゲームには触らない部分。**

危険な道は「押す → 生成 → 受注 → 踏破 → 移動」と長く、その間に
ゲームの再起動も MOD の注入し直しも挟まりうる。だから状態は
プロセスの中ではなくファイルに持ち、段階（`stage`）で進める。

    offered   道を選んでクエストを作った（まだ受注していない）
    armed     ゲームがそのクエストを進めている
    ready     踏破した。移動を起こしてよい
    arriving  移動を起こす予約をした（二重に起こさないための札）
    moving    移動を渡した。着いたか、期限が来たら外す

**ファイルが正本。** 注入し直すと `apply()` がもう一度走り、新しい層は
新しい `Journey` を持つ。古い層が走り続けている（LLM を待っている）間に
書いた控えを見落とさないよう、読む前に更新時刻を見て読み直す（`sync`）。
これが無いと、注入し直しをまたいだ時点で道が切れる。

日数の予算（`spend`）もここが持つ。危険な道は「危険だが早い」ことが値打ちで、
道中と最後の移動を合わせた日数に上限がある。使った日数は控えに積むので、
再起動をまたいでも上限が守られる。
"""

import os
import time

from instantale_modloader import read_json, write_json


class Journey(object):
    """道中の控え1件ぶん。無い状態（`record is None`）も普通の状態。"""

    def __init__(self, path, write, ttl, move_timeout):
        self.path = path
        self.write = write            # MOD 自身のログ関数
        self.ttl = ttl                # 控えの寿命（秒）
        self.move_timeout = move_timeout  # `moving` のまま残さない期限（秒）
        self.record = None
        self._stat = None

    # -- ファイル -----------------------------------------------------------
    def _file_stat(self):
        try:
            info = os.stat(self.path)
            return (info.st_mtime, info.st_size)
        except OSError:
            return None

    def _read(self):
        """ファイルから読む。寿命切れ・壊れているときは None。

        **読みも `read_json` を通す**（TECH.md §3.11.1）。素朴な `open` +
        広い `except` だと「まだ道に出ていない（正常）」と「**在るのに
        読めない**（一時ロック・外部破損）」が同じ None になり、後者でも
        `sync()` が「道が消えた」と判断して進行中の道中が黙って捨てられる。
        読めなかったことは MOD のログに残す。
        """
        data = read_json(self.path, None,
                         report=lambda msg: self.write("WARN pending: " + msg))
        record = data.get("pending") if isinstance(data, dict) else None
        if not isinstance(record, dict):
            return None
        if time.time() - float(record.get("at") or 0) > self.ttl:
            self.write("pending: dropped a stale record ({})".format(
                record.get("target_area_name")))
            return None
        return record

    def save(self):
        # **この控えは注入をまたぐ。** 道中は「押す → 生成 → 受注 → 踏破 →
        # 移動」と長く、その間にゲームの再起動も注入し直しも挟まりうる ―
        # つまり落ちうる局面をまたいで書き続ける。素朴に open(..., "w") で
        # 書くと、その瞬間に落ちれば道が切れる。差し替えの作法は
        # `instantale_modloader.write_json` に一本化してある。
        write_json(self.path, {"pending": self.record},
                   report=lambda msg: self.write("WARN pending: " + msg))
        self._stat = self._file_stat()

    def reload(self):
        """明示的にファイルから読み直す（`apply()` の時点で1回）。"""
        self._stat = self._file_stat()
        self.record = self._read()
        if self.record is not None:
            self.write("pending: restored {!r} -> {!r} (stage={}, quest={})".format(
                self.record.get("origin_area_name"),
                self.record.get("target_area_name"),
                self.record.get("stage"), self.record.get("quest_id")))
        return self.record

    def sync(self):
        """外から変わっていたら読み直す。**読む前に必ず通す。**"""
        stat = self._file_stat()
        if stat == self._stat:
            return
        self._stat = stat
        record = self._read()
        if record != self.record:
            self.write("pending: reloaded from disk ({} -> {})".format(
                describe(self.record), describe(record)))
            self.record = record

    # -- 段階 ---------------------------------------------------------------
    def start(self, record):
        self.record = record
        self.save()

    def drop(self, why):
        record = self.record
        if record is None:
            return False
        self.write("pending: dropped ({}) target={!r} quest={!r} stage={!r}".format(
            why, record.get("target_area_name"), record.get("quest_id"),
            record.get("stage")))
        self.record = None
        self.save()
        return True

    def advance(self, stage, **fields):
        """段階を進めて保存する。`fields` は一緒に書き換える項目。"""
        if self.record is None:
            return None
        self.record["stage"] = stage
        self.record.update(fields)
        self.save()
        return self.record

    def current(self, world=None, stages=()):
        """いま有効な控え。段階・世界が合わなければ None。

        `moving`（最後の移動を渡した後）だけは実時間の期限を見て、過ぎていたら
        その場で外す。**残ったままだと無関係な日数送りまで切り詰めてしまう。**
        """
        record = self.record
        if record is None:
            return None
        if record.get("stage") == "moving" and \
                time.time() - float(record.get("moving_at") or 0) > self.move_timeout:
            self.drop("the move did not finish in {:.0f}s".format(self.move_timeout))
            return None
        if stages and record.get("stage") not in stages:
            return None
        key = record.get("world")
        if key and world and key != world:
            return None
        return record

    # -- 日数の予算 ---------------------------------------------------------
    def spend(self, days, budget):
        """予算の残りぶんだけ日数を使い、実際に使える日数を返す。

        予算を使い切っていれば 0。**呼ぶ側は必ずこの数をゲームへ渡す**
        （呼ばないのではなく、減らして渡す）。
        """
        if self.record is None:
            return days
        spent = int(self.record.get("days_spent") or 0)
        granted = max(0, min(int(days), int(budget) - spent))
        self.record["days_spent"] = spent + granted
        self.save()
        return granted

    def days_spent(self):
        if self.record is None:
            return 0
        return int(self.record.get("days_spent") or 0)


def describe(record):
    """ログ1行に収まる形。"""
    if not isinstance(record, dict):
        return "none"
    return "{}:{}->{}".format(record.get("stage"), record.get("quest_id"),
                              record.get("target_area_name"))
