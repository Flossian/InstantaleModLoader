# -*- coding: utf-8 -*-
"""`state/` に置くデータの**保存先の決め方**。世界を見分けて、ファイル名にする。

MOD は世界ごとにデータを分けて持つ（依頼の出所・NPC の人物像・店の入れ替え日・道中の控え）。
そのために要るのは2つだけ:

    world_key(app)                  この世界を見分ける鍵（＝世界名）
    world_filename(key, suffix)     鍵から `state/<MOD>/` 配下のファイル名

**この2つは MOD 固有ではなくローダの語彙**なので、ここに1つだけ置く。

## なぜ MOD 側に書かないのか

以前は各 MOD が写していた。
`world_key` は5本（`301_` / `305_` / `307_` / `311_` /
`312_`）にコメントごと同じものがあり、`world_filename` に当たるものは 4本にあった。
`301_` の docstring は「`311_` と1文字も違ってはいけない」と書いていた
― **同じファイルを指すため**。

そして**実際にずれた**。
`312_` は `strip` と `sub` の順が逆で、
120文字での切り詰めも `"."` / `".."` の除外も無かった。
末尾に空白の残る名前を作るので、Windows が黙って切る先とこちらの持つ文字列がずれる。
写した時点で予告されていたドリフトが、1つの版で現実になった。

「MOD どうしはコードを共有しない」は**別の話**（TECH.md §3.2.3）。
あれは `instantale_mod_<フォルダ名>` を名指しで
import すると番号を振り直した瞬間に壊れる、という理由で、
共有してよい相手は最初からローダ（`instantale_modloader.*`）だった。
**ここはその共有してよい側**にある。

## 予約デバイス名

`CON` / `NUL` / `COM1` のような名前は、
不正な文字が1つも無いのに **パス構成要素にできない**。
世界名がこれだと `open()` が失敗し、広い `except` に吸われて控えが黙って空に倒れる
― 遊びの続きが消えたことに気付けない。

この知識は `110_fix_character_name_path` が既に持っていた。
あちらが**直さずに記録するだけ**にしているのは、
対象が画面に出るキャラクタ名だから（名前を発明することになる）。
**こちらはファイル名なので直してよい**
― 中身の鍵には元の世界名がそのまま入っているので、表示に影響しない。
"""

from __future__ import annotations

import hashlib
import os
import re
import threading

#: ファイル名に使えない文字（Windows の禁則＋制御文字）。
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

#: Windows が末尾で黙って切るもの。
#: 切られると書いた先と読む先がずれる。
_TRAILING = ". "

#: パス構成要素にできない予約デバイス名（`110_` と同じ表）。
#: 大文字小文字を問わない。
RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM{}".format(n) for n in range(1, 10)]
    + ["LPT{}".format(n) for n in range(1, 10)]
)

#: ファイル名の長さの上限（拡張子を除く）。
#: パス全体の上限ではなく、「1要素が長すぎて作れない」を避けるための目安。
MAX_STEM = 120

#: 世界名が読めなかったときの鍵。
#: **1世界しか使わない前提に落ちる。**
UNKNOWN_WORLD = "_"

#: 世界名を探す場所（`world_dict["world_data"]` の中）。
#: **この順で見る。**
_WORLD_NAME_KEYS = ("world_name", "name", "title")


def world_key(app):
    """この世界を見分ける鍵を返す。読めなければ `"_"`。

    セーブの `world_data` に入っている世界名を使う。
    実行時のオブジェクト（`app.world.name`）より先にセーブ側を見るのは、
    ロード直後に `app.world` がまだ組み上がっていない場合があるため。

    **名前で見分けている。**
    外部のデータ改変ツールで世界名を書き換えられると控えが行方不明になるが、
    名前に代わる安定した id が無い（GAME.md §2.7 の「決めつけない」に従い、
    読めた方を使う）。
    行方不明になったことが分かるよう、
    `311_` は既知の世界ファイルを一覧して1度だけ知らせる形にしている。
    """
    found = world_key_of_dict(getattr(app, "world_dict", None))
    if found is not None:
        return found
    name = getattr(getattr(app, "world", None), "name", None)
    return name if isinstance(name, str) and name else UNKNOWN_WORLD


def world_key_of_dict(world_dict, fallback=None):
    """**セーブの辞書から**世界を見分ける鍵。読めなければ `fallback`。

    `world_key(app)` は `app` を受けるが、
    `104_balance_area_bgm` のようにゲームから **`world_dict` を直に渡されるフック**もある（そちらには
    app が無い）。
    同じ鍵の見方を MOD 側に写すとずれるので、入口だけ分けて中身は共有する ― 実際、
    写した版は `world_data["name"]` の1鍵しか見ておらず、
    `world_name` / `title` で名前を持つ世界では毎回 `fallback` に落ちていた。

    `fallback` を呼び側に決めさせるのは、用途が違うため。
    控えのファイル名にするなら `UNKNOWN_WORLD`、
    集計の見出しなら「その辞書に固有の何か」（`104_` は `id()`）が要る。
    """
    if isinstance(world_dict, dict):
        data = world_dict.get("world_data")
        if isinstance(data, dict):
            for key in _WORLD_NAME_KEYS:
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
    return fallback


def _clean(key: str) -> str:
    """使える文字だけにした語幹。**この時点ではまだ一意ではない。**

      1. 前後の空白を落とす
      2. 使えない文字を `_` に置き換える
      3. 末尾の `.` と空白を落とす ― **Windows が黙って切る**ので、
         残すと書いた先と読む先がずれる
      4. 空・`"."`・`".."` は `"_"` に倒す（どれもファイル名にできない）
      5. 長すぎれば切り、切った結果また末尾が `.` になったら落とす
      6. 予約デバイス名なら `_` を前に付ける（`CON` → `_CON`）
    """
    name = _UNSAFE.sub("_", key.strip()).rstrip(_TRAILING)
    if not name or name in (".", ".."):
        name = UNKNOWN_WORLD
    if len(name) > MAX_STEM:
        name = name[:MAX_STEM].rstrip(_TRAILING) or UNKNOWN_WORLD
    if name.upper() in RESERVED:
        name = "_" + name
    return name


def _digest(key: str) -> str:
    """鍵そのものから作る短い印。**同じ鍵からは必ず同じ値**（`hash()` は使わない
    ― プロセスごとに変わるので、次に起動したとき同じファイルを指せない）。"""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def world_filename(key, suffix: str = ".json") -> str:
    """世界の鍵から `state/<MOD>/` 配下のファイル名を作る。

    **同じ鍵からは必ず同じ名前が出る。**
    複数の MOD が同じファイルを指すため（`301_` が `311_` の
    `npc_profiles/<世界>.json` を読む）。
    ここが揺れると「読めない」＝相手のデータが無いことになる。

    **違う鍵からは必ず違う名前が出る。**
    使える文字に均すだけだと単射でなく、
    別の世界の控えを自分のものとして読む事故が起きる:

        "a/b" と "a\\b"          どちらも "a_b"
        "CON" と "_CON"          予約名を避けるために付けた印が実在の名前と衝突
        120文字を超える2つの世界  先頭が同じなら切り詰め後は同じ

    そこで**均した結果が元の鍵と違うときだけ**、
    鍵そのものから作った短い印を後ろに付ける（`灰の街_東.json` はそのまま、
    `a/b` は `a_b-3f2a91c4.json`）。
    普通の世界名（使えない文字が無く、短く、予約名でない）は**印が付かない**ので、
    置き場所を分ける前から遊んでいる人のファイルもそのまま引ける。

    印が付く側は**一度だけ空から始まる**。
    別の世界の記憶を自分のものとして読むより、
    巻き戻るほうがよい（`ctx._adopt_from_out` と同じ判断）。

    **印の形をした鍵は分けていない。**
    均す必要が無い鍵には印を付けないので、最初から印の形をした世界名は、
    印を付けた別の鍵と重なりうる:

        world_filename("a/b")            -> a_b-3ec69c85.json
        world_filename("a_b-3ec69c85")   -> a_b-3ec69c85.json   同じ

    直すなら「印の形に見える鍵にも印を付ける」ことになるが、
    そうすると **普通の名前にも印が付く場合が増え**（`-` と16進8桁で終わる世界名はありえる）、
    置き場所を分ける前のファイルを引けなくなる側の損が大きい。
    上の衝突は**この関数の出力をそのまま世界名にする**という、
    故意でしか起こせない操作でしか作れない。
    割に合わないので直さず、ここに書いておく。

    > `301_` の `quest_clients.json` はこの心配が無い。
    > あちらは単一ファイルで、**世界名が中身のキー**になっている
    > （`data[world_key(app)]`）。
    > 世界ごとにファイルを分ける形（`311_` / `312_` / `122_`）だけが、
    > 名前の一意性に頼っている。
    """
    key = key if isinstance(key, str) else ""
    stem = _clean(key)
    if stem != key:
        # 均した時点で別の鍵と重なりうる。
        # 元に戻せる印を足して分ける。
        room = MAX_STEM - 9         # "-" + 8桁ぶんを空ける
        stem = "{}-{}".format(stem[:room].rstrip(_TRAILING) or UNKNOWN_WORLD,
                              _digest(key))
    return stem + suffix


# ---------------------------------------------------------------------------
# 世界ごとに1つの JSON を持つ控え
# ---------------------------------------------------------------------------

class WorldStore(object):
    """`state/<フォルダ>/<世界>.json` を世界ごとに1つ持つ控え。

    上の `world_key` / `world_filename` が「どのファイルか」だけを決めるのに対し、
    こちらは**その出し入れ**を持つ。
    `path_for` → `ctx.read_json` → キャッシュ → `ctx.write_json` の4行は、
    9本の MOD（`300_` / `311_` / `312_` / `316_` / `317_` / `318_` / `321_` /
    `403_` / `404_`）に写されていた。

    写した先で既にずれていた。
    フォルダを作るものと作らないもの、錠を持つものと持たないもの、
    読めなかったときに `{}` へ倒すものと `None` を返すもの。
    どれも「そう書いてあるから」以上の理由が無い枝分かれで、
    **他の MOD の控えを読む側**（`404_` が `311_` を読む）が
    相手の切られている `state/` に空のフォルダを作る、という実害も出ていた。

    ```python
    from instantale_modloader import state

    def apply(ctx):
        store = state.WorldStore(ctx, "npc_profiles", write=write)

        key, bucket = store.of(app)         # いま居る世界の控え
        bucket[npc_id] = record
        store.save(key)                     # 書く（並びは order= が決める）
    ```

    | 引数 | |
    |---|---|
    | `dirname` | `state/` 直下のフォルダ名。**MOD 専用の名前にする** |
    | `suffix` | ファイルの拡張子（既定 `".json"`）。`121_` の引き直しの頼みのような別系統に使う |
    | `own` | 自分の控えか。`False` は**他の MOD の控えを読むだけ**の意味で、フォルダを作らず、`save()` は拒む |
    | `default` | 読めなかった／初回のときに返す形を作るもの（既定 `dict`）。**毎回呼ぶ**ので、`{}` ではなく `dict` を渡す |
    | `normalize` | 読んだ直後に1度だけ通す。`(控え, 直したか)` を返し、直っていれば書き戻す（古い形の移行用） |
    | `order` | 書く直前に1度だけ通す。並びを固定した控えを返す（`state/` の差分を読めるようにするため） |
    | `write` | MOD 自身のログ関数。書けなかったときに1行出す。無くてよい |

    錠（`self.lock`）は `RLock` で、**呼び側が跨いで持てる**ように公開してある。
    「読んで、書き換えて、書く」を1つの錠の中で行いたい MOD（`311_` / `317_` /
    `318_` / `321_` / `403_`）がそうしている。
    `load` / `save` は自分でも同じ錠を取るので、外から掛けても二重にならない。

    **このクラスは `apply()` の外に置くこと。**
    `apply()` は1プロセスで何度も呼ばれる（TECH.md §3.6）ので、
    中で作ると注入し直すたびにキャッシュと錠が別物になり、
    前の世代のスレッドが書いた内容が見えなくなる。
    """

    def __init__(self, ctx, dirname, *, suffix=".json", own=True,
                 default=dict, normalize=None, order=None, write=None):
        self.ctx = ctx
        self.dirname = dirname
        self.suffix = suffix
        self.own = own
        self.default = default
        self.normalize = normalize
        self.order = order
        self.write = write
        self.lock = threading.RLock()
        #: 世界の鍵 -> 控え。読んだものをそのまま持つ（呼び側が書き換えてよい）。
        self._buckets = {}
        #: 世界の鍵 -> 最後に読んだときの (mtime_ns, size)。`load(fresh=True)` 用。
        self._stamps = {}

    # -- 場所 ---------------------------------------------------------------

    def path(self, key) -> str:
        """世界の鍵からファイルの場所。

        自分の控え（`own=True`）は `ctx.state_path()` を通すのでフォルダが出来る。
        他の MOD の控え（`own=False`）は組み立てるだけで、**フォルダを作らない**
        （相手を切っている人の `state/` に、使われない空のフォルダを置かないため。
        TECH.md §3.11）。
        """
        name = world_filename(key, self.suffix)
        if self.own:
            return self.ctx.state_path(self.dirname, name)
        return os.path.join(self.ctx.state_dir, self.dirname, name)

    def dir_path(self) -> str:
        """フォルダそのもの。**作らない**（数えるだけの用）。"""
        return os.path.join(self.ctx.state_dir, self.dirname)

    def worlds(self) -> list:
        """フォルダに在る世界の名前（＝ファイル名から拡張子を落としたもの）。

        診断用。控えが空だったときに「他の世界のファイルは在るのか」を見る
        （`311_` が世界名の書き換えに気付くのに使っている）。
        """
        try:
            names = os.listdir(self.dir_path())
        except OSError:
            return []
        cut = len(self.suffix)
        return sorted(name[:-cut] for name in names
                      if name.endswith(self.suffix)
                      and os.path.isfile(os.path.join(self.dir_path(), name)))

    # -- 世代 ---------------------------------------------------------------

    def rebind(self, ctx, write=None) -> "WorldStore":
        """注入し直した世代の `ctx` へ繋ぎ替える。自分自身を返す。

        キャッシュを世代をまたいで持ちたい MOD は、この控えを `sys` の属性など
        プロセス側に置き、`apply()` のたびにこれを呼ぶ（TECH.md §3.5）:

        ```python
        store = getattr(sys, STORE_ATTR, None)
        if not isinstance(store, dict):
            store = {"worlds": state.WorldStore(ctx, STATE_DIRNAME)}
            setattr(sys, STORE_ATTR, store)
        worlds = store["worlds"].rebind(ctx, write)
        ```

        使っているのは場所（`state_path` / `state_dir`）と JSON の出し入れだけで、
        どれも世代で変わらない。それでも繋ぎ替えるのは、
        **前の世代の `ctx` を掴んだままにしない**ため
        （`write` は `ctx.logger()` が作る閉包で、打ち切りの数はその中にある）。
        """
        self.ctx = ctx
        if write is not None:
            self.write = write
        return self

    # -- 読み ---------------------------------------------------------------

    def load(self, key, *, fresh=False):
        """1世界ぶんの控え。一度読んだら覚えておく。

        返すのはキャッシュそのものなので、**書き換えたら `save()` を呼ぶこと**。

        読み直さないのは、注入のフックが会話1手のうちに何度も走るため
        （そのたびに読むと1ターンでディスクを何度も叩く）。
        自分しか書かない控えでは、書いた内容をそのまま持てば足りる。

        `fresh=True` は**他の MOD が書く控え**を読むとき用。
        更新時刻とサイズが変わっていれば読み直す（`403_` が `311_` の控えを
        こうして見ている）。動いていない間まで読み直すことはない。
        """
        with self.lock:
            if fresh:
                try:
                    got = os.stat(self.path(key))
                    stamp = (got.st_mtime_ns, got.st_size)
                except OSError:
                    # まだ相手が書いていない。控えると、後から出来たときに読めない。
                    stamp = None
                if stamp is None or self._stamps.get(key) != stamp:
                    self._buckets.pop(key, None)
                    self._stamps[key] = stamp
            bucket = self._buckets.get(key)
            if bucket is not None:
                return bucket
            # 「無い（初回）」と「在るのに読めない（ロック・破損）」を同じ形へ
            # 倒さない。後者を黙って倒すと、次の `save` が空に近い正本を無傷で
            # 作る。記録だけは必ず残す（`ctx.read_json`）。
            data = self.ctx.read_json(self.path(key), None)
            bucket = data if isinstance(data, type(self.default())) else self.default()
            if self.normalize is not None:
                bucket, changed = self.normalize(bucket)
                if changed and self.own:
                    # 直した形は1度だけ書き戻す。**自分の控えだけ**。
                    self.ctx.write_json(self.path(key), bucket)
            self._buckets[key] = bucket
            return bucket

    def of(self, app, *, fresh=False):
        """いま居る世界の `(鍵, 控え)`。鍵は保存のときに要るので一緒に返す。"""
        key = world_key(app)
        return key, self.load(key, fresh=fresh)

    def cached(self, key):
        """読み込みを起こさずにキャッシュだけ見る。無ければ `None`。"""
        with self.lock:
            return self._buckets.get(key)

    # -- 書き ---------------------------------------------------------------

    def save(self, key, bucket=None) -> bool:
        """1世界ぶんを書き出す。書けたかを返す（**例外にしない**）。

        `bucket` を省くとキャッシュにあるものを書く。
        渡した場合はそれをキャッシュにも据える。

        落ちても壊れない書き方（隣に書いてから差し替える）は `ctx.write_json()`
        に寄せてある。ここが素の `open(path, "w")` だと、書いている途中で落ちた
        瞬間に控えが壊れ、次の `load` がそれを黙って空に倒し、
        **消えたことに気付けないまま**次の更新で1件だけが書かれる。
        """
        if not self.own:
            raise ValueError(
                "WorldStore(own=False) is for reading another mod's data; "
                "{} must not be written from here".format(self.dirname))
        with self.lock:
            if bucket is None:
                bucket = self._buckets.get(key)
                if bucket is None:
                    bucket = self.default()
            self._buckets[key] = bucket
            data = self.order(bucket) if self.order is not None else bucket
            if self.ctx.write_json(self.path(key), data):
                return True
            if self.write is not None:
                self.write("控えを書けなかった: {}".format(self.path(key)))
            return False

    def forget(self, key=None) -> None:
        """キャッシュを捨てる。次の `load` で読み直す。

        世界を跨いだとき（ロード）と、検査でファイルを直接置き換えたときに使う。
        **ファイルは消さない。**
        """
        with self.lock:
            if key is None:
                self._buckets.clear()
                self._stamps.clear()
            else:
                self._buckets.pop(key, None)
                self._stamps.pop(key, None)
