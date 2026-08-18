# -*- coding: utf-8 -*-
"""mod ローダ本体。ゲームの Python インタプリタ（CPython 3.10）の中で動く。

tools/injector.py がこのディレクトリを sys.path に追加して
boot() を呼ぶことで読み込まれる。
ここから先はゲームと同じプロセス・同じインタプリタなので、
ここで例外を投げるとゲームを巻き込む。
そのため mod が失敗した場合はログに残して次へ進むだけにしてあり、外へは投げない。

ディレクトリ構成:

    instantale_modloader/   このパッケージ（ローダ + パッチ API + リコン）
    mods/                   1つの修正につき1フォルダ
    mods/load_order.json    適用順（"order"）と無効一覧（"disabled"）

mod は1フォルダで、名乗りは `mod.json`、中身は入口の `.py`:

    mods/mini_quest/mod.json      {"entry": "quest.py", "name": ..., ...}
    mods/mini_quest/quest.py      入口。apply(ctx) を定義する

    def apply(ctx):
        @ctx.wrap("モジュール名:関数名")
        def 好きな名前(orig, *args, **kwargs):
            ...
            return orig(*args, **kwargs)

`mod.json` は `entry` 以外すべて任意。
名乗り（name / description / version / author / kind）は書いてあればログや
GUI に出るだけだが、次の5つは動作に関わる:

    "api": 1                        前提にしているローダ API（下の API を参照）
    "after": ["101_fix_..."]         適用順の制約（_sort_dependencies）
    "settings": {...}               利用者が変えられる設定の宣言（config.py）
    "debug": true                   開発者向け。デバッグモードのときだけ動く（discover）
    "superseded": "main_024"        本体がその版で同じ修正を取り込んだので降ろした

`kind` は mod 自身が名乗る種別（`KINDS` の1つ。GUI の「種別」列に出る。表示だけで
読み込みの扱いは変えない）。

`debug` と`superseded` は**読み込みの扱いが同じ**（どちらもデバッグモードのときだけ動く）。
分けてあるのは伏せた理由が違うからで、GUI が表示で見分ける。
計測のために作ったものと、要らなくなった修正とが同じ見た目で並んでいると、
次にゲームが更新されたときに「どれを試しに戻すか」が分からなくなる。

名乗りをコード側の変数ではなく JSON に置いているのは、**一覧を作るために mod を
import しない**ため（GUI は無効な mod も壊れた mod も、走らせずに一覧へ出せる）。
上の5つも同じ理由でここに置いてある。
適用順も API の可否も伏せるかどうかも、
コードを1行も走らせる前に決まっていなければならない。

ctx に何があるかは下の ModContext を参照。
パッチの当て方は patch.py に詳しく書いてある。

インジェクタを実行し直すとこのパッケージも mod も全て読み込み直されるので、
「mod を編集する → もう一度注入する」が開発時のループになる。
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys
import threading
import time
import traceback
import uuid

__version__ = "1.6.0"

# mod との契約。`mod.json` の "api" がこれと突き合わされる。
#
# `__version__` とは別に持っている。前者はこの配布物の版で、上がっても mod が
# 壊れるとは限らない。こちらは**壊れる変更のときだけ**動かす番号で、だからこそ
# 判定へ使える情報になる。上げるのは次のような場合:
#
#   ctx のメンバを削除・改名 / 引数の順序や意味の変更 / 既定値の変更
#   （alias_scan, required）/ on_ready のキー導出の変更 / ui.Screen の signature 変更
#
# 上げないのは: ctx へのメンバ追加 / 省略可能な引数の追加 / ログ書式 / 内部の整理。
#
# ゲーム側のバージョンを mod に宣言させないのとは事情が違う（TECH.md §3.7）。
# ゲームの版は信頼できる形で取れず、依存先が在るかは実行時に確かめられる
# （台帳の UNRESOLVED）。ローダ API はその逆で、版は自分のコードにあるので確実に
# 取れる一方、**意味の変化は hasattr では捕まえられない**。
API = 1

# まだ受け入れる最古の API。
# ここを上げると、それより古い mod は読み込まずに撥ねる。
MIN_API = 1

# "api" を書いていない mod をどう扱うか。
# この番号が導入された時点では外部の mod が存在しなかったので、
# 「無い＝1」と定義できる。
# 後から入れると「API
# 1 向けに書かれた」と「作者がこの項目を知らなかった」が区別できなくなる。
DEFAULT_API = 1

# ゲーム自身のトップレベルモジュール。
# 最初のリコンで確認した。
# 「ゲーム以外を除外する」ではなく「ゲームのものを列挙する」形にしている。
# 配布物には約 4200 のモジュールが入っており、
# 除外方式では click / joblib / keyring / dill /
# pygments といった同梱ライブラリが紛れ込む。
# さらに悪いことに "__main__" は sys.stdlib_module_names に含まれるため、
# 標準ライブラリを除外すると
# instantale.py 本体（＝最も重要な対象）まで落ちてしまった。
# ここに置いているのは、patch.py（エイリアス張り替えの範囲）と
# recon.py（ダンプ対象の選別）の両方が同じ表を見るため。
GAME_TOPLEVEL = {
    "__main__",                      # instantale.py。約1万行のメインモジュール
    "scripts",                       # scripts.hud.*, scripts.llm.*, scripts.items ...
    "Embedding",
    "image_generation",
    "llama_cpp_runtime_completion",
    "sidecar_process",
    "save_area_json",
    "save_world_json",
    "api_key_manager",
    "build_type",
    "sdcpp_cuda",                    # 同梱の stable-diffusion.cpp バインディング
}


def is_game_module(name: str) -> bool:
    return name.split(".")[0] in GAME_TOPLEVEL


# 配布フォルダ直下の書き込み先は3つある。**役割で分けてあり、混ぜない。**
#
#     settings/   利用者が決めたこと（mod の設定・GUI の覚え書き・デバッグモード）
#     out/        mod が吐いたもの（ログ・リコン成果物・status.json）
#     state/      mod が持つ永続データ（進行中の道中、依頼の出所、NPC の控え）
#
# 元は out/ が後ろ2つを兼ねていたが、性質が正反対だった。out/ は「消してよい・
# 消せば静かになる」ものの置き場で、GUI は「ログを開く」の案内先として指し、
# logrotate は注入のたびに中身を送る。永続データを同じ場所に置くと:
#
#   * 不具合報告で「out/ を消してから再現してください」と言えない
#     （消すと進行中の依頼や NPC の記憶まで飛ぶ）
#   * 世代管理の対象が「*.log だけ」という但し書きでしか守られない
#   * 利用者が掃除のつもりで消したものが、遊びの続きだった
#
# 置き場所を分ければ、どちらも説明が1行で済む。out/ は捨ててよい。state/ は
# セーブと同じ重みで残す。
STATE_DIR_NAME = "state"


def state_dir(runtime_dir: str) -> str:
    """`state/` の場所。`runtime/` の1つ上＝配布フォルダ直下（`settings/` と同じ並び）。"""
    return os.path.join(os.path.dirname(runtime_dir), STATE_DIR_NAME)


#: 書きかけの一時ファイルに付ける拡張子。
#: `write_json()` が使う。
TEMP_SUFFIX = ".tmp"


def write_text(path: str, text: str, *, report=None) -> bool:
    """テキストを**壊れないように**書く。書けたら True、書けなければ False。

    **残すデータを書くときは必ずここを通すこと。**
    `open(path, "w")` は開いた時点でファイルを切り詰めるので、
    素朴に書くと書いている途中で落ちた瞬間に中身が壊れる。
    読む側は壊れた JSON を黙って `{}` に倒すのが常なので、**消えたことに気付けないまま次の更新で上書きされる**（NPC の記憶なら
    1人ぶんだけが書かれ、他の全員が消える）。
    ゲームは落ちるものだという前提で作っている（`001_crash_recorder` がある）以上、
    ここは落ちても壊れない形にしておく必要がある。

    やっていることは3つ:

      1. 隣に `名前.tmp` として書く（本体は最後まで無傷）
      2. `flush` + `fsync` で中身をディスクまで落とす。ここを省くと、電源断で
         「差し替えは済んだが中身は空」になりうる
      3. `os.replace` で差し替える。同じフォルダなので不可分に入れ替わる

    **例外を投げない。**
    呼ぶのはゲームのスレッドの中で、書けないことよりゲームを巻き込むことの方が困る。
    成否は戻り値で返す（`311_` の `save_bucket` が採っていた作法をここに寄せた）。

    JSON なら `write_json()` を使う。
    こちらを直に使うのは、1行1レコードの記録（`122_` の会話ログ）のように
    JSON 文書1つではないものを書くとき。
    """
    tmp = path + TEMP_SUFFIX
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except Exception:
        (report or log_exc)("cannot write {}".format(path))
        try:
            os.remove(tmp)      # 書きかけを残さない（残しても実害は無い）
        except Exception:
            pass
        return False


def write_json(path: str, data, *, indent: int = 1, sort_keys: bool = False,
               report=None) -> bool:
    """JSON を壊れないように書く。書けたら True、書けなければ False。

    差し替えの作法は `write_text()` にある。
    分けてあるのは、**JSON にできない記録**（1行1レコードの会話ログなど）にも同じ安全さが要るため ― 規則を 2箇所に書かないよう、
    土台はテキスト側に置いて JSON はその上に載せている。

    `default=str` を付けてあるのは、記録に日時や
    `Path` が紛れても書けなくならないようにするため。
    **書けないより、文字列になってでも残る方がよい。**
    """
    try:
        text = json.dumps(data, ensure_ascii=False, indent=indent,
                          sort_keys=sort_keys, default=str) + "\n"
    except Exception:
        (report or log_exc)("cannot serialise {}".format(path))
        return False
    return write_text(path, text, report=report)


def read_json(path: str, default=None, *, report=None):
    """JSON を読む。無ければ `default`。**在るのに読めなければ、記録してから** `default`。

    読めない理由は2つに割れていて、**同じ扱いにしてはいけない**:

        無い（`FileNotFoundError`）   初回・正常。黙って `default` でよい
        在るのに読めない              一時的なロック（ウイルス対策・インデクサ）
                                      か破損。黙って倒すと「無かったこと」になる

    後者が危ないのは、読んだ側が**その結果を次の書き込みの土台にする**から。
    `write_json()` でいくら壊れない書き方をしても、
    読み側が壊れた控えを黙って `{}` に倒せば、
    次の書き込みは空に近い正本を**無傷で**作ってしまう ― 壊れずに、静かに失われる。
    だからここは必ず記録を残す。

    倒した先が `default` なのは変わらない（読めない控えのために
    mod を止めるほうが損害が大きい。
    `load_order.json` が壊れていても必ず動かすのと同じ判断）。
    変わるのは**消えたことが後から分かる**ことだけ。
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception:
        (report or log_exc)("cannot read {}".format(path))
        return default


# 「まだ import されていないモジュール」を待つ見張りの設定。
# ゲームは LLM 系モジュールを最初のリクエストまで import しないので、
# 起動直後に注入すると llama 系のフックが1つも載らない。
# 詳しくは _arm_deferred。
DEFERRED_POLL = 5.0          # sys.modules を見に行く間隔（秒）
DEFERRED_TIMEOUT = 3600.0    # ここまで来なければ諦める（1時間）
MAX_DEFERRED_BOOTS = 8       # 当て直しの上限。暴走よけ

# ローダの状態。
# 再注入のたびに作り直される。
# ゲーム側に残っているパッチとの対応付けは generation（下の boot を参照）で行う。
_state: dict = {
    "booted": False,
    "boot_count": 0,
    "api": API,
    "version": __version__,
    "out_dir": None,
    "state_dir": None,
    "log_path": None,
    "mods": {},
    # mod フォルダ名 -> マニフェスト（名乗り / api / settings / 適用順の制約）。
    "manifests": {},
    # mod フォルダ名 -> その mod に実際に効いている設定値（config.py）。
    "settings": {},
    # discover() が見つけた宣言と実体のずれ。
    # status() から GUI に出す。
    "problems": [],
    # この boot で ctx.on_ready() に積まれた処理。
    # mod の適用が全部済んでから流す。
    "ready": [],
    # 見張りが mod を当て直した回数。
    # boot() では数え直さない（上限の判定に使う）。
    # 手で注入し直すとローダごと読み直されるので、そこで 0 に戻る。
    "deferred_boots": 0,
}

# on_ready の「もう実行した」印を置く場所。
# sys に生やしているのは、**注入し直すとこのモジュール自体が読み込み直される**ため。
# ここをモジュール変数にすると印ごと消えて、
# 再注入や遅延再適用のたびに
# 1回きりのはずの処理（溜まった状態の掃除など）が走り直してしまう。
# sys はプロセスに1つしか無く、誰かが読み直すこともない。
_ONCE_ATTR = "__instantale_ready_once__"


def _once_store() -> set:
    store = getattr(sys, _ONCE_ATTR, None)
    if not isinstance(store, set):
        store = set()
        setattr(sys, _ONCE_ATTR, store)
    return store


def reset_once(prefix: str | None = None) -> int:
    """`on_ready` の「もう実行した」印を捨てる。**開発中の逃げ道**。

    印はプロセスに残るので、注入し直しても1回きりの初期化は二度と走らない。
    それが本来の意図だが、その初期化そのものを直しているときは邪魔になる。
    `prefix` を渡すとその mod のぶんだけ落とす。

    捨てるのは印だけで、**既に起きた副作用は戻らない**。
    もう一度走らせても問題ない処理かどうかは呼ぶ側が判断すること。
    """
    store = _once_store()
    victims = {k for k in store if prefix is None or k.startswith(prefix)}
    store -= victims
    if victims:
        log("on_ready: dropped {} once-mark(s){}".format(
            len(victims), "" if prefix is None else " for " + prefix))
    return len(victims)


# --------------------------------------------------------------------------
# ログ出力
# --------------------------------------------------------------------------
def log(msg: str, *, level: str = "INFO") -> None:
    line = "[{}] {:<5} {}".format(datetime.datetime.now().isoformat(timespec="milliseconds"),
                                  level, msg)
    # ファイルと stderr の両方に出す。
    # どちらも失敗する可能性があるので（出力先が未設定、stderr が閉じている等）、
    # 別々に握り潰す。
    # ログのせいでゲームを落とすのは本末転倒なので。
    path = _state.get("log_path")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    try:
        sys.stderr.write("[modloader] " + line + "\n")
    except Exception:
        pass


def log_exc(msg: str) -> None:
    """今処理している例外を、トレースバック付きで ERROR として記録する。

    except 節の中から呼ぶこと。
    """
    log(msg + "\n" + traceback.format_exc(), level="ERROR")


# --------------------------------------------------------------------------
# 各 mod に渡されるコンテキスト
# --------------------------------------------------------------------------
class ModContext:
    """mod が使える機能をまとめたもの。apply(ctx) の ctx がこれ。

    よく使うのは次の5つ:

        ctx.wrap(対象)      元の関数を第1引数で受け取るラッパを付ける
        ctx.patch(対象)     関数を丸ごと差し替える
        ctx.log(文字列)     modloader.log に出す
        ctx.log_exc(文字列) 例外をトレースバック付きで出す
        ctx.out_path(名前)  out/ 以下のパスを作る（親ディレクトリも作成）

    書き込み先は2つあり、**役割で使い分ける**（STATE_DIR_NAME の説明を参照）:

        ctx.out_path(名前)    ログ・調査の出力。消してよいもの
        ctx.state_path(名前)  遊びの続きに要る永続データ。消すと巻き戻るもの

    それに加えて、1回だけ実行したい処理を預けられる:

        ctx.on_ready(関数)  プロセスにつき1回だけ、メインスレッドで実行する

    設定とローダの版:

        ctx.config          この mod に効いている設定値（mod.json の宣言 + 利用者の選択）
        ctx.setting(名前)   その1件だけ引く
        ctx.api             ローダ API の番号（下位互換の分岐が要るとき用）
    """

    def __init__(self, out_dir: str, runtime_dir: str, state_root: str | None = None):
        self.out_dir = out_dir
        self.runtime_dir = runtime_dir
        # 既定は配布フォルダ直下の state/。
        # オフライン検証が別の場所を指せるよう引数でも受ける（out_dir と同じ扱い）。
        # 引数名を `state_dir` にしないのは、
        # 場所を決める関数 `state_dir()` を中で呼べなくなるため。
        self.state_dir = state_root or state_dir(runtime_dir)
        self.log = log
        self.log_exc = log_exc
        self.api = API
        self.version = __version__
        # この boot の世代。
        # `superseded()` がこれと今の世代を突き合わせる。
        # boot() が `_state["generation"]` を決めた後にこの ctx を作っている。
        self.generation = _state.get("generation")
        # 今 apply() を実行中の mod ファイル名。
        # boot() が出し入れする。
        # on_ready の既定のキーに使う。
        self._mod: str | None = None

    # -- patch モジュールへの入口 -------------------------------------------
    # ここで遅延 import しているのは循環 import を避けるため。
    # patch.py はこのモジュールから log / log_exc を import している。
    def patch(self, target: str, **kw):
        from . import patch as _patch
        return _patch.patch(target, **kw)

    def wrap(self, target: str, **kw):
        from . import patch as _patch
        return _patch.wrap(target, **kw)

    def resolve(self, target: str):
        """対象を (持ち主, 属性名, 現在の値) として取り出す。調査用。"""
        from . import patch as _patch
        return _patch.resolve(target)

    def patches(self) -> dict:
        """対象 -> その対象に当てた mod の一覧。

        自分より前に読み込まれた mod が同じ関数を触っているかを、
        apply() の中から確かめられる。
        ファイル名順の適用なので、見えるのは自分より前の分だけ。
        """
        from . import patch_registry as _registry
        return _registry.by_target()

    # -- 設定 ---------------------------------------------------------------
    @property
    def config(self) -> dict:
        """この mod に効いている設定値。`mod.json` の "settings" が宣言したものだけ。

        値はローダが**apply() を呼ぶ前にモジュールの定数へ書き込んである**ので（config.py）、
        普通は `EVENT_MODE` のように定数をそのまま読めばよい。
        こちらを使うのは、宣言した覚えのある名前を一覧で確かめたいときと、
        `apply()` の外（`on_ready` の中など）で値を控えておきたいとき。

        apply() の外では `{}` になる（どの mod の話か決まらないため）。
        """
        if self._mod is None:
            return {}
        return dict(_state["settings"].get(self._mod) or {})

    def setting(self, name: str, default=None):
        """設定を1件引く。宣言されていなければ `default`。"""
        return self.config.get(name, default)

    # -- 1回きりの処理 -------------------------------------------------------
    def on_ready(self, fn, *, key: str | None = None, delay: float = 0.0,
                 force: bool = False) -> bool:
        """`fn` を「このプロセスで1回だけ」「メインスレッドで」実行する。

        apply() は1プロセスの中で**何度も呼ばれる**。
        手で注入し直したときと、未 import のモジュールが現れて当て直したとき（`_arm_deferred`）で、後者は最大
        MAX_DEFERRED_BOOTS 回まで起こりうる。

        パッチを当てる分にはこれで問題ない。
        patch.py の世代管理が前の層を置き換えるので、何度当てても結果は1回分になる。
        困るのは**副作用のある初期化**で、
        apply() の中で直接やると回数ぶん繰り返される:

            溜まった「迷子の曲」の掃除、状態ファイルの初期化、スレッドの起動

        そういう処理をここへ預ける。
        同じキーで2回目以降に積まれたものは黙って捨てられる（戻り値 False）。

        実行は Kivy の Clock 経由にしてある。
        ゲームの状態を触ってよいのはメインスレッドだけで、
        `boot()` 自体は注入したリモートスレッドの上で走っているため。
        Clock が無い環境（オフライン検証など）ではその場で呼ぶ。

        キーの既定は「mod ファイル名 + 関数名」。
        同じ mod の中で複数の on_ready を使い分けたい場合や、
        mod をまたいで1回にしたい場合は `key` を明示する。

        `force=True` は印を無視して積み直す。
        **開発中の逃げ道**で、
        注入し直しても初期化がもう走らない（印はプロセスに残る）のを抜けるためにある。
        配布する mod に書いてはいけない。
        遅延当て直しのたびに副作用が起きる。
        """
        name = key or "{}:{}".format(
            self._mod or "<loader>",
            getattr(fn, "__qualname__", None) or getattr(fn, "__name__", repr(fn)))
        store = _once_store()
        # 印を「積んだ時点」で付ける。
        # 実行時に付けると、実行前にもう一度
        # boot() が走ったときに二重に積まれる（Clock はまだ流していない）。
        if name in store and not force:
            log("on_ready: {} already done in this process; skipped".format(name))
            return False
        if force and name in store:
            log("on_ready: {} forced (the once-mark was set)".format(name), level="WARN")
        store.add(name)
        _state["ready"].append((name, fn, delay))
        return True

    # -- 自分より新しい注入が来たか ------------------------------------------
    def superseded(self) -> bool:
        """この apply() が仕掛けたものが**用済みか**。降りるべきなら True。

        `apply()` は1プロセスの中で何度も走る（手での再注入と、遅延当て直し）。
        パッチは
        patch.py の世代管理が前の層を置き換えるので重ならないが、**自分で回し続けるもの**は誰も止めてくれない:

            自前のスレッド（`while True:` の見張り）
            `Clock.schedule_interval` の繰り返し

        `revert_all()` は Clock の予約もスレッドも取り消せないので、
        放っておくと注入のたびに1本ずつ積み上がり、全員が同じログへ書き、
        全員が同じ状態を触る。
        そこで**自分で降りる**:

            def watch():
                while not ctx.superseded():     # スレッド
                    ...
                    time.sleep(POLL)

            def poll(_dt):
                if ctx.superseded():
                    return False                # Clock から外れる
                ...
                return True

        判定は2つ。
        同じローダで別の boot が走った（`generation` が変わった）か、
        注入し直されて**ローダ自体が読み込み直された**か。
        後者では自分が握っている `_state` はもう誰も更新しないので、
        世代の比較だけでは気付けない（`sys.modules` の中身が別の
        `_state` を持っているかで見分ける）。

        ローダの遅延設置（`_superseded`）と同じ判定をそのまま出したもの。
        MOD 側で `sys` に合言葉を置いて代用すると、
        置き場所も判定も MOD ごとにばらつく上、**2つ目の判定が抜けたものが混ざる**。

        `apply()` の外でも呼べる（世代は ctx が作られた時点で控えてある）。
        """
        return _superseded(self.generation)

    def out_path(self, *parts: str) -> str:
        """out/ 以下のパスを返す。親ディレクトリは先に作っておく。

        **消してよいもの**の置き場。
        ログ・調査の出力・status.json。
        注入のたびに `tools/logrotate.py` が直下の `*.log` を1世代送る。
        遊びの続きに要るものは `state_path()` へ。
        """
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name: str, *, tag: str = None, stamp: bool = True,
               label: str = None):
        """この MOD 専用のログ関数を作る。`out/<name>` に1行ずつ追記する。

            write = ctx.logger("quest_offer.log")
            write("offered 3 quest(s)")     # -> [YYYY-MM-DDThh:mm:ss.mmm] offered ...

        **MOD のログはローダのログ（`ctx.log`）と分ける。**
        何が起きたかはその MOD の記録に残したいが、
        `modloader.log` は全 MOD の共用なので、
        混ぜると1本を追うのに他の全部を読むことになる。

        | 引数 | |
        |---|---|
        | `tag` | 時刻と本文の間に**そのまま**挟む印（区切りの記号も込みで渡す）。`"[FLAGFIX]"` なら `[時刻] [FLAGFIX] 本文`、`"quest-end:"` なら `[時刻] quest-end: 本文` |
        | `stamp` | 時刻を付けるか。既定 True。自分で時刻を組み立てて渡す記録では False |
        | `label` | 書けなかったときに `modloader.log` へ出す名前。既定は MOD のフォルダ名 |

        `tag` を逐語にしてあるのは、**既にあるログの見た目を変えないため**。
        角括弧の形（`[BGMFIX]`）と区切りの形（`quest-end:`）が両方使われていて、
        どちらも実機の記録として GAME.md / VERIFICATION_LOG.md に引用されている。
        ここで体裁を揃えると、その引用が次のプレイのログと一致しなくなる。

        書けなくても**例外にしない**（`ctx.log_exc` に残して素通り）。
        呼ぶのはゲームのスレッドの中で、
        記録が取れないことよりゲームを巻き込むことの方が困る。
        ログの追記なので `write_text()` の
        tmp→replace は通さない（1行ずつ足すだけで、壊れても捨てられる。
        TECH.md §3.11.1 の表）。

        **`out/` へ書くこと自体に意味がある。**
        注入のたびに `tools/logrotate.py` が1世代送るので、
        1回のプレイぶんだけが残る。

        以前はこの7行が**42本の
        MOD に写されていた**（時刻付き・印付き・時刻なし・錠付きの4通りに枝分かれした状態で）。
        写して回るものはローダの語彙（TECH.md §3.2.3）。
        """
        path = self.out_path(name)
        whose = label or (self._mod or "mod")
        lock = threading.Lock()

        def write(text):
            try:
                line = str(text).rstrip("\n")
                if tag:
                    line = "{} {}".format(tag, line)
                if stamp:
                    line = "[{}] {}".format(
                        datetime.datetime.now().isoformat(timespec="milliseconds"),
                        line)
                with lock:
                    with open(path, "a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
            except Exception:
                self.log_exc("{}: write failed".format(whose))

        return write

    def state_path(self, *parts: str) -> str:
        """state/ 以下のパスを返す。親ディレクトリは先に作っておく。

        **消すと巻き戻るもの**の置き場。
        進行中の道中、依頼の出所、NPC の控え。
        セーブに書けない（または書きたくない）が、次に遊ぶときに要るデータ。

        同じ名前が `out/` に在って `state/` に無ければ、**1度だけ移してくる**。
        置き場所を分ける前に遊んでいた人の続きを、こちらで拾うため。
        移設は `state/` 側が空のときだけなので、2回目以降は何もしない。
        """
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._adopt_from_out(parts, path)
        return path

    def read_json(self, path: str, default=None):
        """JSON を読む。無ければ `default`。**在るのに読めなければ**記録してから `default`。

        `state_path()` で取った場所を読むときは必ずこれを使う。
        理由はモジュール側の `read_json()` にある ― 素朴な `open` + 広い
        `except` で `{}` に倒すと、一時的に読めなかっただけの控え（ロック・破損）が「無かったこと」になり、**次の
        `write_json()` が空に近い正本を無傷で作る**。
        読めなかった記録にはこの MOD の名前が入るので、
        どの控えが飛んだのかログから追える。
        """
        return read_json(path, default, report=self.log_exc)

    def write_json(self, path: str, data, *, indent: int = 1) -> bool:
        """JSON を壊れないように書く。書けたら True、書けなければ False。

        **`state_path()` で取った場所へ書くときは必ずこれを使う。**
        素朴に `open(path, "w")` で書くと、
        途中で落ちた瞬間に控えが壊れる ― 読む側は壊れた JSON を `{}` に倒すので、
        消えたことに気付けないまま次の更新で上書きされる。
        詳しくはモジュール側の `write_text()` を参照。

        失敗は例外ではなく戻り値で返る。
        記録にはこの MOD の名前が入るので、
        どの MOD が書けなかったかがログから分かる。
        """
        return write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path: str, text: str) -> bool:
        """テキストを壊れないように書く。JSON なら `write_json()` を使う。

        こちらを使うのは1行1レコードの記録のように、JSON 文書1つではないもの。
        """
        return write_text(path, text, report=self.log_exc)

    def _adopt_from_out(self, parts: tuple, path: str) -> None:
        """`out/<同じ名前>` が在れば `state/` へ移す（移設の跡はログに残す）。

        コピーではなく移動にしてある。
        両方に残すと、次に読むのがどちらなのか分からないファイルが
        `out/` に居座る（`out/` を消してよいという説明も崩れる）。
        移せなかった場合は何もしない ― state/ 側が空のまま始まるだけで、
        壊れるより巻き戻るほうがましなため。
        """
        legacy = os.path.join(self.out_dir, *parts)
        if not os.path.exists(legacy) or os.path.abspath(legacy) == os.path.abspath(path):
            return
        try:
            os.replace(legacy, path)
        except OSError:
            # ディレクトリの移設と、ドライブを跨ぐ場合。
            try:
                import shutil
                shutil.move(legacy, path)
            except Exception:
                log_exc("state: cannot move {} to {}".format(legacy, path))
                return
        except Exception:
            log_exc("state: cannot move {} to {}".format(legacy, path))
            return
        log("state: moved {} from out/ (kept as {})".format(
            os.path.join(*parts), path))

    @property
    def mod_dir(self) -> str | None:
        """いま apply() が走っている mod のフォルダ。同梱データを読むとき用。

            table = json.load(open(os.path.join(ctx.mod_dir, "data", "x.json")))

        **読む専用**。
        書き込みは `ctx.out_path()`（ログ）か `ctx.state_path()`（永続データ）へ。
        mods/ は配布物そのもので、遊ぶ側が書き換わることを想定していない。

        apply() の外（`on_ready` の中など）では None になるので、
        フォルダを後で使うなら apply() の中で控えておく。
        """
        if self._mod is None:
            return None
        return os.path.join(self.runtime_dir, "mods", self._mod)

    # -- 実行環境の情報 -----------------------------------------------------
    @property
    def game_dir(self) -> str:
        return os.path.dirname(os.path.abspath(sys.executable))

    def describe(self) -> str:
        return (
            "python      : {}\n"
            "executable  : {}\n"
            "compiled    : {}\n"
            "modules     : {}\n"
            "out_dir     : {}\n"
            "state_dir   : {}\n"
        ).format(sys.version.replace("\n", " "),
                 sys.executable,
                 # __compiled__ があれば Nuitka でビルドされたモジュール。
                 # 素の Python で動かしているのか、ゲームの中なのかの区別に使える。
                 "__compiled__" in dir(sys.modules.get("__main__", object())),
                 len(sys.modules),
                 self.out_dir,
                 self.state_dir)


# --------------------------------------------------------------------------
# mod の探索と読み込み
# --------------------------------------------------------------------------
def _mods_dir() -> str:
    # このファイルは runtime/instantale_modloader/__init__.py にあるので、
    # 2階層上がって runtime/ に出て、その下の mods/ を指す。
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mods")


MANIFEST_NAME = "mod.json"
ORDER_NAME = "load_order.json"

#: mod.json の "kind" に書ける語彙。mod 自身が名乗る種別で、GUI の「種別」列が
#: 表示に使う（読み込みの扱いは変えない）。表示名: core=基盤 / fix=修正 /
#: probe=計測 / feature=追加。番号帯（TECH.md §3.2.2）は置き場の整理であって
#: 分類の軸ではないので、種別は帯から導かずこの宣言で持つ。
KINDS = ("core", "fix", "probe", "feature")

#: 手元だけの順序ファイル。
#: **在ればこちらが `load_order.json` に優先する。**
#: `.gitignore` に入れてあるので配布物にもコミットにも入らない。
#: 用途は「まだ公開しない MOD を手元で動かす」こと。
#: `load_order.json` は配布する構成そのものなので、
#: 開発中の MOD を書くと配った先で「実体の無い記述」になり、
#: GUI で保存するたびに書き戻されて毎回コミットに紛れ込む。
#: 利用者のファイルが同梱ぶんに優先する形は `111_` の置換ルールと同じ（TECH.md
#: §3.1.1）。
ORDER_LOCAL_NAME = "load_order.local.json"


def order_path(mods_dir: str) -> str:
    """いま効いている順序ファイルの場所。**書き戻すのもここ。**

    手元用（`load_order.local.json`）が在ればそれ、
    無ければ配布用（`load_order.json`）。
    GUI の保存先をこれで決めるので、
    手元用を置いている間は配布用のファイルが書き換わらない。
    """
    local = os.path.join(mods_dir, ORDER_LOCAL_NAME)
    return local if os.path.isfile(local) else os.path.join(mods_dir, ORDER_NAME)




def _installed(mods_dir: str) -> list[str]:
    """mods/ に入っている mod のフォルダ名。並びは名前順。

    **1つの mod は1つのフォルダ**で、`mod.json` を持つものだけを mod とみなす:

        mods/mini_quest/mod.json      名乗りと入口ファイルの宣言
        mods/mini_quest/quest.py      入口（mod.json の "entry"）
        mods/mini_quest/prompts.py    分割した中身（from . import prompts）
        mods/mini_quest/data/...      同梱データ（ctx.mod_dir から読む）

    フォルダ名にもファイル名にも決まりは無い。
    入口は `mod.json` が名指しする。

    探索は**この1階層だけ**で、再帰しない。
    深く潜ると mod の中の補助モジュール（上の `prompts.py`）まで
    mod として拾ってしまい、「何が mod なのか」の規則が増える。

    先頭が "_" のフォルダは読み込まない（手で切るときの逃げ道。
    GUI から切ったものは `load_order.json` の "disabled" に載る。
    `_discover` を参照）。
    """
    found = []
    for name in sorted(os.listdir(mods_dir)):
        if name.startswith("_") or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(mods_dir, name, MANIFEST_NAME)):
            found.append(name)
    return found


def is_wip(name: str) -> bool:
    """開発中の mod か（フォルダ名が `900`〜`999` で始まる）。

    番号帯そのものが印。
    `debug` / `superseded` のようにマニフェストの旗で持たないのは、**まだ配る形が決まっていないもの**だからで、
    フォルダ名を見ただけで「これは配布物に入らない」と分かるほうが事故が少ない（TECH.md
    §2.6）。
    リリースするときに正式な番号へ振り直す。

    読み込むのは「順序ファイルに名前があり（手元の
    `load_order.local.json`）、**かつデバッグモードのとき**」だけ（`debug` /
    `superseded` と同じ伏せ方）。
    順序ファイルに名前が無ければ黙って外す ― 配布物に入らないものなので、
    「記載の無い MOD」として報告しても直しようが無い。
    """
    return len(name) >= 3 and name[:3].isdigit() and name[0] == "9"


def discover(mods_dir: str | None = None, *, debug: bool | None = None) -> dict:
    """インストールされている mod を調べて、適用順まで決めて返す。

    **ローダ・GUI・静的検査の3者が共通で使う唯一の入口**。
    以前はこの規則（`_` と `.` で始まるフォルダを除く / `mod.json` を持つものだけ /
    `order` と `disabled` の意味 / 載っていない
    mod は末尾）が3箇所に別々に書かれていて、
    片方だけ直すと GUI の一覧と実際の適用順がずれる状態だった。

        {"mods_dir":  "...\\runtime\\mods",
         "order":     ["000_recon", ...],       有効な mod。**適用順**
         "listed":    ["000_recon", ...],       一覧に出す順（無効なものも宣言位置に）
         "installed": ["000_recon", ...],       在るもの全部（フォルダ名順）
         "disabled":  ["..."],                  切られているもの
         "debug":     {"200_probe_..."},        開発者向け。今は伏せられている
         "debug_mode": False,                   デバッグモードが入っているか
         "superseded": {"101_...": "main_024"}, 本体が取り込んだので降ろしたもの
         "wip":       {"901_..."},              開発中（9xx）。順序ファイルに載り、かつデバッグモードのときだけ読む
         "manifests": {名前: マニフェスト},      無効なものも壊れたものも含む
         "problems":  ["..."],                  宣言と実体のずれ。人が読む行
         "notes":     ["..."]}                  直すべきずれではない知らせ

    `manifests` に無効な mod も入れているのは、GUI が「消えたように見せない」ため。

    `order` と `listed` を分けているのは、無効な mod には適用順が無い一方で、
    一覧では**宣言された位置に置いたままにしたい**から。
    表示順を「有効なものの後」にすると、
    GUI で切って保存するたびにその mod が末尾へ移動してしまう（利用者は順序を触っていないのに
    `load_order.json` が書き換わる）。

    ゲームの中でも外でも同じ結果になる（ファイルを読むだけで、
    mod のコードは一切 import しない）。

    `debug` を渡すとデバッグモードの設定（`settings/loader.json`）を無視してそちらに従う。
    `tools/check_mods.py` が `True` で呼ぶためにある ― 静的検査は **入っている
    mod を全部見る**のが仕事で、利用者が今どちらに倒しているかで検査の範囲が変わってはいけない（切っている間だけ計測 mod の
    `after` が誰にも確かめられない、という穴を作らない）。
    """
    mods_dir = mods_dir or _mods_dir()
    if not os.path.isdir(mods_dir):
        return {"mods_dir": mods_dir, "order": [], "listed": [], "installed": [],
                "disabled": [], "debug": set(), "debug_mode": False,
                "superseded": {}, "wip": set(), "manifests": {}, "notes": [],
                "problems": ["mods ディレクトリが無い: {}".format(mods_dir)]}

    installed = _installed(mods_dir)
    manifests = {name: _manifest(mods_dir, name) for name in installed}

    # 開発者向けの mod（計測系）は、デバッグモードを入れている間だけ動く。
    # 伏せるのは `order` からだけで、`listed`（GUI の一覧）には残す ― 一覧の並びは保存時にそのまま
    # `order` へ書き戻されるので（gui.py の `save`）、
    # ここで落とすと利用者が保存した瞬間に順序ファイルから記述ごと消える。
    debug_mode = (_config_module().debug_mode(os.path.dirname(mods_dir))
                  if debug is None else bool(debug))
    marked = {name for name in installed if (manifests[name] or {}).get("debug")}
    # 本体が取り込んだので降ろした mod。
    # 伏せ方は計測系と同じだが、
    # 名前と一緒に「どの版で取り込まれたか」を持ち回る（GUI がそこを表示で分ける）。
    superseded = {name: (manifests[name] or {}).get("superseded")
                  for name in installed
                  if (manifests[name] or {}).get("superseded")}
    # 開発中の mod（9xx）。読み込みの条件は2つで、両方を満たしたときだけ動く:
    #   1. 順序ファイルに名前がある（無いものは `_order()` が伏せる。開発者が
    #      置いただけのものを勝手に動かさない）
    #   2. デバッグモードが入っている（`debug` / `superseded` と同じ扱い。
    #      作りかけの mod が普段の遊びに紛れ込まない）
    wip = {name for name in installed if is_wip(name)}
    hide = (frozenset() if debug_mode
            else frozenset(marked) | frozenset(superseded) | frozenset(wip))

    order, listed, disabled, problems, notes = _order(mods_dir, installed, hide)

    order, dep_notes = _sort_dependencies(order, manifests, silent=hide)
    problems += dep_notes
    problems += _check_conflicts(order, manifests)

    # 並べ替えが起きたぶんを一覧の順にも反映する。
    # **無効な mod は動かさない**ので、有効な mod が入っていた位置を新しい並びで順に埋め直す形にする（末尾に寄せると、
    # 切ってあるだけの mod が一覧の下に集まってしまう）。
    enabled = set(order)
    if order != [n for n in listed if n in enabled]:
        fresh = iter(order)
        listed = [next(fresh) if n in enabled else n for n in listed]

    return {"mods_dir": mods_dir, "order": order, "listed": listed,
            "installed": installed, "disabled": disabled,
            "debug": marked, "debug_mode": debug_mode,
            "superseded": superseded, "wip": wip,
            "manifests": manifests, "problems": problems, "notes": notes}


def _order(mods_dir: str, found: list[str],
           hide: frozenset = frozenset()) -> tuple[list[str], list[str],
                                                   list[str], list[str], list[str]]:
    """mod を**適用順に**並べて返す。

    このローダでは適用順が動作の前提になっている（TECH.md
    §3.2: 計測は修正より外側に置く、など）。
    順序はフォルダ名からは決めず、`load_order.json` が明示的に持つ:

        {"order": ["recon", "crash_recorder", "fix_kivy_shutdown", ...],
         "disabled": ["recon"]}

    `disabled` に載っている mod は**読み込まない**。
    GUI のチェックボックスの実体で、フォルダ名を変えずに切れるようにしてある（`_` を付ける方式だと、無効にした瞬間に
    `order` の中の名前と食い違う）。

    こうするのは、フォルダ名を自由に付けられるようにするため。
    名前で順序を表すと「名前は自由」と「順序は名前で決まる」が両立しない。

    **順序ファイルに無い mod は捨てずに末尾へ回す**（フォルダ名順）。
    新しい mod をフォルダごと置いただけで動くようにするため。
    逆に、順序ファイルに載っているが実体が無いものは黙って飛ばす（消した
    mod の記述が残っていても壊れない）。

    順序ファイルが読めなければフォルダ名順。
    **必ず何らかの決まった順で動く**ようにしておく。
    ここで例外にすると、順序ファイルが壊れた瞬間に mod が全滅する。

    手元用の
    `load_order.local.json` が在ればそちらを**丸ごと**使う（`ORDER_LOCAL_NAME`）。
    混ぜないのは、2つのファイルの差分から順序を組み立てる規則を増やしたくないため
    ― 効いている順序は常に1ファイルで読める。

    `hide` に挙げた mod は適用順から外す（デバッグモードが切のときの計測 mod。
    `discover()` が渡す）。
    `disabled` と違って**報告もしない** ― 切ったのは利用者ではないので、
    「無効化されています」「記載の無い MOD」を出すと、
    伏せたはずのものが警告として画面に出てくる。
    切られていることを必ず見せる `disabled` とは目的が逆なので、
    同じ扱いにはできない。

    戻り値は `(有効な mod の適用順, 一覧に出す順, 切られている mod, 報告)`。
    2つ目は無効な mod も**宣言された位置に**含む（GUI の一覧用）。
    """
    problems: list[str] = []
    notes: list[str] = []
    path = order_path(mods_dir)
    order_file = os.path.basename(path)
    data = read_json(path)
    if order_file == ORDER_LOCAL_NAME:
        # 「配った構成と違うもので動いている」ことは必ず見えるようにする。
        # ただし**直すべきずれではない**ので `problems` ではなく `notes`。
        notes.append("{} を使っています（{} より優先。手元だけの構成です）"
                     .format(ORDER_LOCAL_NAME, ORDER_NAME))

    order = []
    if isinstance(data, dict):
        order = data.get("order") or []
    elif isinstance(data, list):        # 素の配列で書かれていても読む
        order = data
    if not isinstance(order, list):
        problems.append("{}: \"order\" が配列ではありません。"
                        "フォルダ名順で読み込みます".format(order_file))
        order = []

    # 開発中の mod（9xx）は、この順序ファイルが名指ししているものだけ読み込む。
    # 名前が無いものは `hide` と同じ扱いにする ― 適用もしないし報告もしない。
    # 配布物には入らないので、利用者の画面に「記載の無い
    # MOD」として出しても直しようが無い（`is_wip` の説明を参照）。
    named = {name for name in order if isinstance(name, str)}
    undeclared_wip = {name for name in found
                      if is_wip(name) and name not in named}
    hide = frozenset(hide) | undeclared_wip

    disabled = data.get("disabled") if isinstance(data, dict) else None
    if not isinstance(disabled, list):
        disabled = []
    off = {name for name in disabled if isinstance(name, str)}
    skipped = sorted(off & set(found))
    # 報告するのは利用者が切ったものだけ。
    # 伏せている mod は `skipped` に残す（利用者の「切る」選択そのものは消さない。
    # GUI が保存で書き戻すため）が、警告としては出さない。
    told = [name for name in skipped if name not in hide]
    if told:
        # 「入れたのに効かない」を黙って起こさない。
        # 切ったことは必ず残す。
        problems.append("無効化されています（読み込まれません）: {}".format(
            ", ".join(told)))

    # 一覧に出す順。
    # 無効なものも宣言された位置に残す。
    listed: list[str] = []
    for name in order:
        if isinstance(name, str) and name in found and name not in listed:
            listed.append(name)
    listed += [name for name in found if name not in listed]
    # 伏せた計測 mod（`hide`）は一覧に残すが、**宣言に無い開発中の mod は外す**。
    # GUI の保存は `listed` をそのまま `order` へ書き戻すので（gui.py の `save`）、
    # 残しておくと、順序ファイルを開いて保存しただけで
    # `load_order.json` に開発中の名前が入ってしまう。
    # 残す理由（保存で記述ごと消えるのを防ぐ）もこちらには無い
    # ― まだどこにも書かれていない mod なので、消える記述が無い。
    listed = [name for name in listed if name not in undeclared_wip]

    known = set(found) - off - hide
    ordered = []
    for name in order:
        if name in known and name not in ordered:
            ordered.append(name)

    extra = [name for name in found if name in known and name not in ordered]
    if extra and order:
        # 順序ファイルに無い mod。
        # 落とさずに末尾へ回したことを残す（「入れたのに効かない」を黙って起こさないため）。
        problems.append("{} に記載の無い MOD（末尾に配置されます）: {}".format(
            order_file, ", ".join(extra)))

    stale = [name for name in order if isinstance(name, str) and name not in found]
    if stale:
        problems.append("{} に実体の無い記述があります: {}".format(
            order_file, ", ".join(stale)))
    dupes = sorted({n for n in order if isinstance(n, str) and order.count(n) > 1})
    if dupes:
        problems.append("{} に重複があります: {}".format(order_file, ", ".join(dupes)))

    return ordered + extra, listed, skipped, problems, notes


def _load_mod_file(path: str):
    """mod をパス指定で読み込む。ゲームのモジュール名とは隔離する。

    `path` は `mod.json` の "entry" が指すファイル。
    **パッケージとして**読み込むので、入口の名前が何であれ、
    mod の中から `from . import prompts` で隣を引ける。
    """
    # 専用の接頭辞を付けた名前で sys.modules に登録する。
    # ゲーム側のモジュール名とぶつかると、どちらかが壊れるため。
    mod_dir = os.path.dirname(path)
    name = "instantale_mod_" + os.path.basename(mod_dir)
    # submodule_search_locations を渡すとパッケージ扱いになる。
    # これが無いと mod の中の相対 import が「親パッケージが無い」で落ちる。
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[mod_dir])
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # 前の注入で読み込んだ**中の部品**を捨ててから入れ直す。
    # 入口は毎回読み直しているのに、`from . import panel` の相手は sys.modules に残るので、放っておくと **新しい入口
    # × 古い部品** で動く。
    # 分割した mod を直して注入し直したのに、
    # 入口が呼ぶ関数だけ古いままで AttributeError になる（分割した mod ＝ `116_` /
    # `307_` / `309_` が該当する）。
    for cached in [key for key in sys.modules if key.startswith(name + ".")]:
        sys.modules.pop(cached, None)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # 読み込みに失敗したモジュールを sys.modules に残さない。
        # 残すと次の注入で「もう読み込み済み」と誤認される可能性がある。
        sys.modules.pop(name, None)
        raise
    return module


def _manifest(mods_dir: str, name: str) -> dict:
    """mod.json を読んで、扱いやすい形に均して返す。GUI の一覧に出す用。

        {"entry": "quest.py",
         "name":        {"en": "Non-combat mini quests", "ja": "戦闘なしミニクエスト"},
         "description": {"en": "...", "ja": "..."},
         "version": "1", "author": "R01/Flossian"}

    **mod のコードを import せずに読める**ことがこの形の要点。
    GUI は無効化中の mod も、壊れている mod も、
    一覧に出すだけならコードを一切走らせずに済む。
    名乗りを Python のモジュール変数に置いていると、
    一覧を作るだけのために他人の mod を
    import することになる（import した時点でトップレベルは走る）。

    `entry` 以外は全て任意。
    `name` は文字列でも `{"en":..., "ja":...}` でも書ける。
    **片方の言語しか無ければもう片方で埋める**ので、
    `name["ja"]` は必ず何かを返す（GUI の行が空にならない）。
    """
    data = read_json(os.path.join(mods_dir, name, MANIFEST_NAME))
    if not isinstance(data, dict):
        data = {}

    def text(value) -> str:
        if value is None:
            return ""
        try:
            return str(value).strip()
        except Exception:
            return ""

    def localized(key: str, default: str = "") -> dict:
        value = data.get(key)
        if isinstance(value, dict):
            en, ja = text(value.get("en")), text(value.get("ja"))
        else:
            en, ja = text(value), ""     # 文字列1つなら英語として扱う
        en = en or ja or default
        return {"en": en, "ja": ja or en}

    def names(key: str) -> list[str]:
        """フォルダ名の並びを読む。文字列1つでも受ける。"""
        value = data.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [text(v) for v in value if text(v)]

    api = data.get("api")
    try:
        api = int(api)
    except (TypeError, ValueError):
        if api is not None:
            log("{}: \"api\" が整数ではない（{!r}）。{} として扱う".format(
                name, api, DEFAULT_API), level="WARN")
        api = DEFAULT_API

    kind = text(data.get("kind")).lower()
    if kind and kind not in KINDS:
        log("{}: \"kind\" が語彙に無い（{!r}）。無指定として扱う".format(
            name, data.get("kind")), level="WARN")
        kind = ""

    return {
        "dir": name,
        # 入口の既定。
        # mod.json が無い/壊れている場合の受け皿で、
        # 実際に読めるかどうかは boot() が確かめる。
        "entry": text(data.get("entry")) or "mod.py",
        # 前提にしているローダ API。
        # boot() がこれを見て、扱えない mod は **コードを読み込む前に**撥ねる。
        "api": api,
        # 名前が無ければフォルダ名。
        # GUI の行が名無しにならないように。
        "name": localized("name", name),
        "description": localized("description"),
        "version": text(data.get("version")),
        "author": text(data.get("author")),
        # 適用順の制約と、既知の非互換。
        # _sort_dependencies が使う。
        "after": names("after"),
        "before": names("before"),
        "conflicts": names("conflicts"),
        # 開発者向けの mod（計測系）。
        # デバッグモードを入れている間だけ動く。
        # 判定はここ＝**コードを読み込む前**に済む。
        # 名乗りと同じ理由で、外すために他人の mod を import することにならない。
        "debug": bool(data.get("debug")),
        # mod 自身が名乗る種別（`KINDS` の1つ）。GUI の「種別」列がこれを読む。
        # フォルダ名の番号帯から**導かない** ― 帯は帯であって分類の軸ではない
        # （TECH.md §3.2.2。挙動を変えるなら機能追加でも 1xx に置いてよい）ので、
        # どれに属するかは mod 自身に言わせる。無指定・語彙の外は ""。
        "kind": kind,
        # ゲーム本体が同じ修正を取り込んだので降ろした mod。
        # 値はその版（例 "main_024"）。
        # **読み込みの扱いは "debug" と同じ**で、
        # 違うのは「なぜ伏せられているか」だけ ― 計測用に作ったものと、
        # 要らなくなった修正とを一覧で見分けられないと、
        # 次にゲームが更新されたとき「どれを試しに戻すか」が分からなくなる。
        "superseded": text(data.get("superseded")),
        # 利用者が変えられる設定の宣言（config.py）。
        "settings": _config_module().normalize_decls(data.get("settings")),
    }


def api_status(manifest: dict) -> tuple[str | None, str]:
    """この mod を扱えるか。扱えるなら `(None, "")`、駄目なら `(結果, 理由)`。

    判定を boot() から分けてあるのは、**コードを読み込む前に決まる**ことを形として残すためと、`tools/check_mods.py` と
    GUI が同じ判定を使えるようにするため。
    """
    api = manifest.get("api", DEFAULT_API)
    if api > API:
        return ("api-too-new",
                "needs loader API {} but this loader provides {}; "
                "update InstantaleModLoader".format(api, API))
    if api < MIN_API:
        return ("api-too-old",
                "written for loader API {}, no longer supported (minimum is {})"
                .format(api, MIN_API))
    return (None, "")


def _config_module():
    # 遅延 import。
    # config.py はこのモジュールから log を import している。
    from . import config as _config
    return _config


# --------------------------------------------------------------------------
# 適用順の制約
# --------------------------------------------------------------------------
def _sort_dependencies(order: list[str], manifests: dict,
                       silent: frozenset = frozenset()) -> tuple[list[str], list[str]]:
    """`after` / `before` を満たす並びに直す。`(並び, 報告)` を返す。

    `load_order.json` の並びは利用者が触るもので、**動作の前提を知らない**。
    「計測は修正より外側」「305_ は 105_ より先」といった関係は、
    これまでドキュメントにしか無く、GUI で行を動かせば無警告で壊せた。
    それを mod 自身に宣言させて、ここで満たす。

        "after":  ["101_fix_npc_employ_price"]   自分はこれより後（＝外側）
        "before": ["105_fix_schema_compact"]     自分はこれより先（＝内側）

    やっているのは安定なトポロジカルソート。
    **基準の並びは `load_order.json`** で、制約に触れない mod の相対順は動かさない。
    利用者が並べ替えた意図を、制約を満たす範囲でそのまま残すため。

    存在しない mod や無効な mod を指した制約は黙って捨てる（消した
    mod の記述が残っていても壊れないように。順序ファイルの扱いと同じ）。
    ただし**報告は残す** ― 利用者が切った相手を指していることは、
    順序が変わる理由として見えていてよい。

    `silent` に挙げた相手を指した制約は、報告もしない。
    伏せている mod（デバッグモードが切のときの計測 mod）が相手で、
    これを報告すると `300_event_facility_arrival` の
    `"after": ["205_probe_player_events"]` が「無効な
    mod を指している」として毎回警告に出る。
    利用者から見れば存在しないはずの mod の名前が出てくることになる。

    循環していたら**基準の並びをそのまま返す**。
    ここで例外にすると mod が全滅する。
    """
    notes: list[str] = []
    index = {name: i for i, name in enumerate(order)}
    known = set(order)

    # 辺は「先に来る側 -> 後に来る側」。
    edges: dict[str, set[str]] = {name: set() for name in order}
    for name in order:
        manifest = manifests.get(name) or {}
        for other in manifest.get("after") or []:
            if other in known:
                edges[other].add(name)
            elif other in silent:
                pass
            elif other in manifests:
                notes.append("{}: \"after\" が無効な mod を指している（{}）".format(name, other))
            else:
                notes.append("{}: \"after\" の {} が見つからない".format(name, other))
        for other in manifest.get("before") or []:
            if other in known:
                edges[name].add(other)
            elif other in silent:
                pass
            elif other in manifests:
                notes.append("{}: \"before\" が無効な mod を指している（{}）".format(name, other))
            else:
                notes.append("{}: \"before\" の {} が見つからない".format(name, other))

    incoming = {name: 0 for name in order}
    for src, targets in edges.items():
        for dst in targets:
            incoming[dst] += 1

    # 出せるものが複数あるときは、
    # 基準の並びで一番前のものから出す（安定にするため）。
    ready = sorted((n for n in order if incoming[n] == 0), key=index.get)
    result: list[str] = []
    while ready:
        name = ready.pop(0)
        result.append(name)
        for dst in sorted(edges[name], key=index.get):
            incoming[dst] -= 1
            if incoming[dst] == 0:
                ready.append(dst)
        ready.sort(key=index.get)

    if len(result) != len(order):
        stuck = [n for n in order if n not in result]
        notes.append("適用順の制約が循環している（{}）。load_order.json の並びで動かす"
                     .format(", ".join(stuck)))
        return list(order), notes

    if result != order:
        # 名指しするのは**制約を持っている mod だけ**。
        # 位置が変わった mod を全部並べると、1つ動かした煽りで後ろが全部ずれるので、
        # 20行の羅列になって読めなくなる。
        constrained = [n for n in result
                       if ((manifests.get(n) or {}).get("after")
                           or (manifests.get(n) or {}).get("before"))
                       and result.index(n) != index[n]]
        notes.append("\"after\"/\"before\" に従って並べ替えた: {}".format(
            ", ".join(constrained) or "（順序は同じ）"))
    return result, notes


def _check_conflicts(order: list[str], manifests: dict) -> list[str]:
    """`conflicts` に挙がっている相手が同時に有効なら報告する。

    **落とさずに報告するだけ**にしてある。
    このローダは同じ対象に複数の mod を重ねるのが正常な使い方で（patch_registry.py）、
    どちらを外すべきかはローダには決められない。
    片方を黙って落とすより、両方動かして名指しする方が筋が通る。
    外すのは利用者の判断（`load_order.json` の "disabled"）。
    """
    notes: list[str] = []
    active = set(order)
    for name in order:
        for other in (manifests.get(name) or {}).get("conflicts") or []:
            if other in active and other != name:
                pair = " と ".join(sorted((name, other)))
                note = "非互換が宣言されている mod が両方有効: {}".format(pair)
                if note not in notes:
                    notes.append(note)
    return notes


# --------------------------------------------------------------------------
# 1回きりの処理の実行
# --------------------------------------------------------------------------
def _dispatch_ready() -> None:
    """この boot で積まれた on_ready を流す。

    Clock に載せるのは、`boot()` が注入したリモートスレッドの上で走っていて、
    ゲームの状態（Kivy のウィジェット、pygame の音）をそこから触れないため。
    `schedule_once` は次のフレーム＝メインスレッドで呼ばれる。
    """
    entries = _state["ready"]
    _state["ready"] = []
    if not entries:
        return

    try:
        from kivy.clock import Clock
    except Exception:
        Clock = None      # ゲームの外（オフライン検証など）

    log("on_ready: {} task(s){}".format(
        len(entries), "" if Clock else " (no kivy Clock; running inline)"))

    for name, fn, delay in entries:
        # 既定引数で束縛する。
        # ループ変数のまま閉じ込めると、
        # Clock が呼ぶ頃には最後の1件を全員が指している。
        def call(_dt=None, name=name, fn=fn):
            try:
                fn()
                log("on_ready: {} done".format(name))
            except BaseException:
                # ここは Clock（メインスレッド）の中なので、投げるとゲームが落ちる。
                log_exc("on_ready failed: {}".format(name))

        if Clock is None:
            call()
            continue
        try:
            Clock.schedule_once(call, delay)
        except BaseException:
            # 載せられなかった＝一度も走っていない。
            # 印を外して、次の boot（再注入や遅延当て直し）で積み直せるようにする。
            # 印は「実行した」ではなく「実行したか、Clock に渡ってもう走る」の意。
            # ここで残すと、走らないまま二度と積まれない一件が生まれる。
            _once_store().discard(name)
            log_exc("on_ready: could not schedule {} (will retry next boot)".format(name))


# --------------------------------------------------------------------------
# 後から import されるモジュールを待つ
# --------------------------------------------------------------------------
def _superseded(generation: str) -> bool:
    """この見張りが用済みかどうか。

    降りるべき場合が2つある。
    1つは同じローダで別の boot が走ったとき。
    もう1つは注入し直されてローダ自体が読み込み直されたときで、
    このスレッドが握っているのは古いモジュールなので、
    そのまま当て直すと二重に適用してしまう。
    後者は sys.modules の中身が別の _state を持っているかどうかで見分ける。
    """
    if _state.get("generation") != generation:
        return True
    current = sys.modules.get(__name__)
    return current is not None and getattr(current, "_state", None) is not _state


def _settle_unused_local(out_dir: str, pending: list) -> list:
    """ローカル（llama.cpp）専用の保留を、クラウドと分かった時点で降ろす。

    ゲームは選ばれたプロバイダの送信モジュールを**1つだけ** import する（GAME.md
    §2.12）。
    クラウドなら `llama_cpp_runtime_completion` は一生 import されないので、
    待ち続けても当たらない。
    それでも `deferred` に居座ると GUI は「段階適用の途中」と言い続け、
    注入のたびに無駄な見張りが立つ。

    戻り値は**待ち続けるべきモジュール**の並び。
    降ろすのは「クラウドと分かった」ときだけで、
    起動直後（プロバイダ未確定）は何もしない ― `is_cloud_runtime()` は
    `is_local_runtime()` の否定ではなく、どちらも False の時間帯がある。
    そこで決めつけると、ローカル実行の保留まで降ろしてしまう。
    """
    from . import llm as _llm
    from . import patch_registry as _registry

    local = [name for name in pending if name in _llm.LOCAL_ONLY_MODULES]
    if not local or not _llm.is_cloud_runtime():
        return pending
    providers = [name[len(_llm.REQUEST_MODULE_PREFIX):]
                 for name in _llm.request_modules()
                 if name != _llm.LOCAL_REQUEST_MODULE] or ["cloud"]
    moved = _registry.settle_deferred(local, "not used with " + "/".join(providers))
    log("deferred: {} in use; {} hook(s) for {} will not be waited for "
        "(that path is not taken in this run)".format(
            "/".join(providers), moved, ", ".join(local)))
    # 台帳が変わったので書き直す。
    # GUI は status.json しか見ていないので、
    # ここで書かないと「途中」のまま残る（それがこの関数を足した動機）。
    write_status(out_dir)
    return [name for name in pending if name not in local]


def _deferred_loop(out_dir: str, generation: str, pending: list) -> None:
    deadline = time.monotonic() + DEFERRED_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(DEFERRED_POLL)
        if _superseded(generation):
            return
        # プロバイダが決まるのは最初の
        # LLM リクエスト＝この見張りが立った後のことがある。
        # だから arm のときだけでなく、毎回見る。
        pending = _settle_unused_local(out_dir, pending)
        if not pending:
            return
        arrived = [name for name in pending if sys.modules.get(name) is not None]
        if not arrived:
            continue
        # 1つでも来たら当て直す。
        # 全部揃うのを待つと、片方が永久に来ない場合（エリアを生成しなければ
        # save_area_json は載らない）に全部が道連れになる。
        # 当て直した後は残りの保留に対して新しい見張りが立つ。
        log("deferred: {} imported; re-applying mods".format(", ".join(arrived)))
        _state["deferred_boots"] += 1
        try:
            boot(out_dir)
        except BaseException:
            # ここで投げるとゲーム側のスレッドを道連れにするので、
            # 記録だけして降りる。
            log_exc("deferred re-apply failed")
        return
    from . import patch_registry as _registry
    late = [n for n in pending if sys.modules.get(n) is None]
    log("deferred: gave up after {:.0f}s; still not imported: {}".format(
        DEFERRED_TIMEOUT, ", ".join(late)), level="WARN")
    # 見張りが降りた以上、もう当たらない。
    # `deferred` のまま残すと「まだ待っている」と読めてしまうので、
    # 諦めたことを台帳に書いて status.json へ流す。
    if _registry.settle_deferred(late, "gave up after {:.0f}s".format(DEFERRED_TIMEOUT)):
        write_status(out_dir)


def _arm_deferred(out_dir: str, generation: str) -> None:
    """未 import のモジュール宛てのフックがあれば、現れるまで見張る。

    ゲームは `llama_cpp_runtime_completion` と `scripts.llm.llm_manager` を最初の
    LLM リクエストまで import しない。
    `watcher.py` は「インタプリタ初期化」と「ウィンドウ表示」で注入するが、
    その時点ではまだどちらも載っていないので、
    プロンプト関係の mod（DEDUP / COMPACT
    / 計測）が1つも設置されないままプレイが進むことになる。

    そこで、モジュールが現れた時点で mod を当て直す。
    当て直しは手作業での再注入と同じ経路で、
    世代管理（patch.py）が前の層を置き換えるので重ならない。
    """
    from . import patch as _patch
    from . import patch_registry as _registry
    # 見張りを立てる前に、待っても無駄と分かっているものを降ろす。
    # 当て直しの boot はプロバイダが決まった後に走るので、
    # この時点で片付くことが多い。
    pending = _settle_unused_local(out_dir, _patch.pending_modules())
    if not pending:
        return
    if _state["deferred_boots"] >= MAX_DEFERRED_BOOTS:
        log("deferred: already re-applied {} time(s); not watching again for {}".format(
            _state["deferred_boots"], ", ".join(pending)), level="WARN")
        if _registry.settle_deferred(pending, "re-apply limit reached"):
            write_status(out_dir)
        return
    log("deferred: waiting for {} (checking every {:.0f}s)".format(
        ", ".join(pending), DEFERRED_POLL))
    threading.Thread(target=_deferred_loop, args=(out_dir, generation, pending),
                     name="instantale_modloader.deferred", daemon=True).start()


def boot(out_dir: str) -> dict:
    """注入されたブートストラップから呼ばれる入口。"""
    _state["out_dir"] = out_dir
    _state["log_path"] = os.path.join(out_dir, "modloader.log")
    _state["boot_count"] += 1

    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass

    # 今回の注入に固有の ID を振る。
    # 同じプロセスに2回目以降の注入をしたとき、
    # patch.py がこの
    # ID を見て「前回の注入が残したラッパ」と「今回自分が付けたラッパ」を区別する。
    # これが無いと、注入のたびにラッパが入れ子で積み上がってしまう。
    generation = uuid.uuid4().hex[:12]
    _state["generation"] = generation
    from . import patch as _patch
    _patch.set_generation(generation)

    log("=" * 70)
    log("instantale_modloader {} (API {}) boot #{} gen={} (pid {})".format(
        __version__, API, _state["boot_count"], generation, os.getpid()))
    log("python {} | {} modules loaded".format(sys.version.split()[0], len(sys.modules)))

    runtime_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ctx = ModContext(out_dir, runtime_dir)
    # 場所は毎回ログに出す。
    # out/ を消してくださいと頼めるのは、永続データがそこに無いと言い切れるときだけなので、
    # どこを使っているかを残す。
    _state["state_dir"] = ctx.state_dir
    log("out {} | state {}".format(out_dir, ctx.state_dir))

    from . import config as _config
    from . import patch_registry as _registry

    results: dict[str, str] = {}
    settings: dict[str, dict] = {}
    _state["ready"] = []      # 前回の boot で流し残した分は持ち越さない
    # `ctx.config` はここを読む。
    # apply() が始まる前に**この boot のもの**に差し替えておくこと（同じ辞書を持たせるので、以降は
    # settings に足すだけで見える）。
    # 前回の boot の値を残すと、設定を宣言しなくなった mod に古い値が見え続ける。
    _state["settings"] = settings

    found = discover()
    mods_dir = found["mods_dir"]
    manifests = found["manifests"]
    names = found["order"]
    _state["manifests"] = manifests
    _state["problems"] = found["problems"]

    # 宣言と実体のずれは先に出す。
    # この後に mod ごとのログが数十行流れるので、後ろに回すと埋もれる。
    for line in found["problems"]:
        log(line, level="WARN")
    # 知らせ（手元用の順序ファイルを使っている、など）は WARN にしない。
    # 直すべきずれではないが、**何で動いているかは必ず残す**。
    for line in found.get("notes", []):
        log(line)

    if not os.path.isdir(mods_dir):
        log("no mods dir at {}".format(mods_dir), level="WARN")
    else:
        # 利用者が選んだ設定値。
        # 全 mod ぶんまとめて1回読む。
        chosen = _config.load_store(runtime_dir)
        log("{} mod(s) in {}".format(len(names), mods_dir))
        for fname in names:
            manifest = manifests[fname]

            # ローダ API の可否を**コードを読み込む前に**判定する。
            # 名乗りが JSON にあるからここで撥ねられる。
            # 扱えない mod を import してから AttributeError で落ちるのに比べ、
            # 利用者に読める形で止まる。
            verdict, reason = api_status(manifest)
            if verdict:
                log("{}: {}".format(fname, reason), level="WARN")
                results[fname] = verdict
                continue

            path = os.path.join(mods_dir, fname, manifest["entry"])

            # 読み込みでも apply() でも、失敗したらログに残して次の mod へ進む。
            # 1つの mod が壊れているせいで残り全部が動かないのを防ぐため。
            if not os.path.isfile(path):
                log("{}: entry {!r} not found".format(fname, manifest["entry"]),
                    level="WARN")
                results[fname] = "no-entry"
                continue
            try:
                module = _load_mod_file(path)
            except BaseException:
                log_exc("load failed: {}".format(fname))
                results[fname] = "load-error"
                continue

            apply_fn = getattr(module, "apply", None)
            if apply_fn is None:
                log("{} has no apply(), skipped".format(fname), level="WARN")
                results[fname] = "no-apply"
                continue

            # 設定は**読み込んだ後・apply() の前**に書き込む。
            # apply() の中で作られる入れ子の関数は定数をモジュールのグローバルとして読むので、
            # この順なら mod のコードに手を入れずに値が効く（config.py）。
            if manifest["settings"]:
                try:
                    settings[fname] = _config.apply_to_module(
                        module, manifest["settings"], chosen.get(fname))
                except BaseException:
                    # 設定の適用で落ちても mod は動かす。
                    # 既定値のままになるだけ。
                    log_exc("settings failed: {}".format(fname))

            # 台帳とコンテキストに「今どの mod を実行中か」を教える。
            # patch.py はこれを見てパッチの帰属を決め、
            # ctx.on_ready は既定のキーに使う。
            # apply が例外で抜けても必ず戻すこと（finally）。
            # 戻し忘れると、次に記録されたパッチが前の mod のものとして残る。
            _registry.begin_mod(fname)
            ctx._mod = fname
            try:
                apply_fn(ctx)
            except BaseException:
                log_exc("apply failed: {}".format(fname))
                results[fname] = "apply-error"
                continue
            finally:
                _registry.end_mod()
                ctx._mod = None
            log("applied: {}".format(fname))
            results[fname] = "ok"

    _state["mods"] = results
    _state["settings"] = settings
    _state["booted"] = True

    ok = sum(1 for v in results.values() if v == "ok")
    log("-" * 70)
    log("boot complete: {}/{} mod(s) applied".format(ok, len(results)))

    # 適用に失敗した mod を先に名指しする。
    # トレースバックは上に出ているが、
    # 何本読み込んだか分からないログの中では埋もれるため。
    broken = {f: v for f, v in results.items() if v != "ok"}
    if broken:
        log("{} mod(s) not applied:".format(len(broken)), level="WARN")
        for fname in sorted(broken):
            log("  {} [{}]".format(fname, broken[fname]), level="WARN")

    # どの mod がどこへ当てたか、重なりはどこか、解決できなかった対象は何か。
    # ゲーム更新で関数が消えた場合はここの UNRESOLVED に出る。
    for line in _registry.format_report():
        log(line)
    log("-" * 70)

    # ここまでの結果をファイルへ落とす。
    # GUI はこれを読んで「何本入ったか・何が失敗したか」を出す（動いているプロセスへ問い合わせる経路が無いため）。
    write_status(out_dir)

    # 1回きりの処理を流す。
    # mod の適用が全部済んでから（適用中に Clock が回り始めると、
    # まだ当たっていないパッチを前提にした処理が走りうる）。
    _dispatch_ready()

    # まだ import されていないモジュール宛てのフックがあれば、現れるまで見張る。
    # mod の適用が全部終わってから立てること（適用中に当て直しが走らないように）。
    _arm_deferred(out_dir, generation)
    return dict(results)


STATUS_NAME = "status.json"


def write_status(out_dir: str | None = None) -> str | None:
    """`status()` の中身を `out/status.json` に書く。

    台帳も適用結果も、これまでプロセスの中にしか無かった。
    `status()` は用意されていたが**呼ぶ側が存在せず**、
    GUI は注入が成功したかどうかしか出せていなかった（「28個中3個が
    apply-error」は利用者がログを自力で開かないと分からない）。

    ゲームの中から問い合わせる経路を作るには注入をもう1本増やすことになるので、
    boot の最後に**こちらから書き出す**。
    1方向で済み、ゲームが終了した後でも読める。

    遅延当て直し（`_arm_deferred`）のたびに上書きされるので、中身は常に最新の boot。
    ログ（`*.log`）とは別扱いなので世代管理の対象にしない。
    常に「今の状態」を表すファイルで、履歴に意味が無い。
    """
    out_dir = out_dir or _state.get("out_dir")
    if not out_dir:
        return None
    path = os.path.join(out_dir, STATUS_NAME)
    # 失っても次の注入で作り直される軽いファイルだが、
    # 書き方は他と揃える（「なぜここだけ素朴な open なのか」を残さない）。
    # 書けなくても boot は続ける ― 報告のためのファイルなので、無くても遊べる。
    return path if write_json(path, status(), indent=2) else None


def unload(out_dir: str | None = None) -> dict:
    """当てたパッチを全て剥がして、素のゲームに戻す。

    ゲームを終了せずに mod を外すための入口。
    `tools/injector.py --unload` がこれを呼ぶ。
    記録は `sys` に置いてあるので（patch.py の `_undo_log`）、
    注入から今までの間にローダが何度読み直されていても剥がせる。

    **完全に元通りにはならない**ことは断っておく。
    戻せるのは属性の差し替えと複製束縛の張り替えだけで、次のものは戻らない:

        `on_ready` で既に起きた副作用（掃除・状態ファイルの初期化）
        mod がゲームの状態そのものに書いた値（パーティの名簿・依頼）
        mod が立てたスレッドや Clock の予約

    そのため「入れ忘れた状態に戻す」用途ではなく、**mod を疑うときの切り分け**に使うもの。
    素のゲームで確かめたいなら、注入せずに起動し直すのが確実。
    """
    # 注入し直して呼ばれるので、
    # このモジュールは読み込み直された直後＝out_dir を知らない状態で入ってくる。
    # ログの行き先を先に決める（記録が残らないと、
    # 剥がしたのか失敗したのかが後から分からない）。
    if out_dir:
        _state["out_dir"] = out_dir
        _state["log_path"] = os.path.join(out_dir, "modloader.log")

    from . import patch as _patch
    log("=" * 70)
    log("unload: reverting patches (gen={})".format(_state.get("generation")))
    count = _patch.revert_all()
    _state["mods"] = {}
    _state["settings"] = {}
    _state["booted"] = False
    _state["unloaded"] = count
    from . import patch_registry as _registry
    _registry.reset()
    write_status()
    log("unload: done; {} patch(es) reverted".format(count))
    log("note: on_ready side effects and game-state writes are not undone")
    return {"reverted": count}


def status() -> dict:
    """今のローダの状態を返す。動いているプロセスに問い合わせて調べるとき用。

    これ1回で GUI が要るものが揃うようにしてある:

        ["mods"]       フォルダ名 -> "ok" / "load-error" / "apply-error" / "no-apply"
                       / "no-entry" / "api-too-new" / "api-too-old"
        ["manifests"]  フォルダ名 -> 名乗り（name/description は {"en","ja"}）
        ["settings"]   フォルダ名 -> その mod に実際に効いている設定値
        ["problems"]   宣言と実体のずれ（順序・依存・非互換）。人が読む行
        ["patches"]    台帳（by_target / by_mod / conflicts / unresolved / counts）
        ["api"]        このローダの API 番号

    `_state` を浅く写してから台帳を足している。
    `_state` をそのまま返すと呼び出し側の書き換えがローダに届いてしまうため。

    `ready`（実行待ちの関数）は落とす。
    関数は JSON にできないので、そのまま残すと
    `write_status()` がこれ1つのために失敗する。
    """
    from . import patch_registry as _registry
    snapshot = dict(_state)
    snapshot.pop("ready", None)
    snapshot["ready_pending"] = len(_state.get("ready") or [])
    snapshot["patches"] = _registry.summary()
    snapshot["written_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return snapshot


def patches() -> dict:
    """対象 -> その対象に当てた mod の一覧。動いているプロセスへの問い合わせ用。"""
    from . import patch_registry as _registry
    return _registry.by_target()


def mod_patches() -> dict:
    """mod -> その mod が当てた対象の一覧（`patches()` の逆引き）。"""
    from . import patch_registry as _registry
    return _registry.by_mod()


def conflicts() -> dict:
    """2つ以上の mod が触っている対象だけ。"""
    from . import patch_registry as _registry
    return _registry.conflicts()
