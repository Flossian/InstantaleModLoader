# -*- coding: utf-8 -*-
"""実行中の状態を読みやすい文字列にするための補助関数。

ゲームはコンパイル済みでソースが読めないので、
何かが失敗したときに状況を知る手段は「その瞬間の変数の中身を写し取る」ことしかない。
クラッシュ記録（001_crash_recorder.py）などがここの関数を使っている。
"""

from __future__ import annotations

import os
import sys

# ログが読めなくならないようにするための上限。
MAX_VALUE_REPR = 220
MAX_DICT_KEYS = 40
MAX_LOCALS_PER_FRAME = 25
DEFAULT_FRAME_DEPTH = 4

# 中身を出す価値のあるファイル。
# kivy や pydantic、threading のフレームまで出すとノイズで埋まる。
GAME_FILE_HINTS = ("instantale.py", "\\scripts\\", "/scripts/",
                   "llama_cpp_runtime_completion.py", "sidecar_process.py",
                   "save_area_json.py", "save_world_json.py")

# このファイルは runtime/instantale_modloader/ にあるので、2つ上が runtime/。
# 「こちら側のフレーム」（ローダと mod）を見分けるのに使う。
# caller() を参照。
RUNTIME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 「属性が無い」と「値が None」を区別するための番人。
# 戦闘BGMの原因特定では、`app.music` が **None ではなく存在しない** ことが決め手になった（渡されたオブジェクトが
# app ではなかった）。
# getattr(x, k, MISSING) の形で使い、ログには "<missing>" と出す。
MISSING = "<missing>"


def attr(obj, name, default=MISSING):
    """属性を読む。**`hasattr` を使わないこと。**

    `hasattr` は失敗するルックアップを1回起こすので、
    `__getattr__` トリップワイヤ（`201_probe_missing_attr`）を自己発火させる。
    実際に踏んだ（TECH.md §6）。
    既定値付きの `getattr` なら、
    探索は同じ1回でもトリップワイヤ側から見れば「本当に読まれた」1回と区別が付かない。
    つまり計測が計測を汚さない。
    """
    try:
        return getattr(obj, name, default)
    except Exception as exc:
        # ゲーム側の property は評価そのものが失敗することがある。
        return "<{} while reading>".format(type(exc).__name__)


def text_of(obj, name: str = "text"):
    """文字列の属性を読む。**文字列でなければ None。**

    `attr()` が返す番人は**2つとも文字列**（属性が無ければ `MISSING` ＝
    `"<missing>"`、property の評価が失敗すれば `"<... while reading>"`）なので、
    `isinstance(値, str)` で受けると**どちらも素通りする**。

    実際に踏んだもの:

      * `118_` … 本文を `isinstance(str)` で受け、`"<missing>"` を本文だと
        思ったまま照合し続けた（打ち切りが毎回不発。例外は出ない）
      * `115_` … `text` を持たない飾りのウィジェットが一覧の「行」に数えられ、
        列数の計算と格子の座標が汚れた
      * `116_` … 本文ラベルが None のとき `"<missing>"` をフォント名として
        ボタンに代入していた

    ここは番人を作らずに読む ― 「無い」も「読めない」も呼ぶ側にとっては同じ「文字列が取れなかった」なので、区別が要るときだけ
    `attr()` を使う。
    """
    try:
        value = getattr(obj, name, None)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def short(value, limit: int = 200) -> str:
    """値を「表示・ログに収まる1本の文字列」にする。None は空文字。

    名前やログの1項目を切り詰めるための共通形。
    `_text` の名で 300 番台の mod に写しが増えていたのでここへ集めた
    （写して回るものはローダの語彙。TECH.md §3.2.3）。
    `repr_value()` とは役割が別 ― あちらは調査用に「形」を写し取る、
    こちらは表示用に「そのままの文字」を整える。
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def repr_value(value) -> str:
    """値を短く文字列化する。コンテナは中身ではなく「形」を出す。

    dict については、値を切り詰めた repr ではなく、キーの一覧とキーの型を出す。
    未解決だった KeyError には KeyError: '52'（文字列）と KeyError: 80（整数）の
    2種類があり、どちらなのかを見分けるにはキーの型が必要だったため。
    """
    try:
        if isinstance(value, dict):
            keys = list(value)[:MAX_DICT_KEYS]
            # 型の調査は先頭 200 件まで。
            # 巨大な表でも一瞬で終わるようにする。
            types = sorted({type(k).__name__ for k in list(value)[:200]})
            more = "" if len(value) <= MAX_DICT_KEYS else " ...+{}".format(
                len(value) - MAX_DICT_KEYS)
            return "dict(len={}, keytypes={}) keys={!r}{}".format(
                len(value), "/".join(types) or "-", keys, more)
        if isinstance(value, (list, tuple, set)):
            return "{}(len={}) {}".format(
                type(value).__name__, len(value), repr(list(value)[:12])[:MAX_VALUE_REPR])
        text = repr(value)
    except Exception as exc:
        # ゲーム側のオブジェクトは __repr__ 自体が失敗することがある。
        # ここで落ちると記録そのものが取れなくなるので必ず握り潰す。
        return "<repr failed: {}>".format(type(exc).__name__)
    # 改行を潰さないと、1行1エントリのログ形式が崩れる。
    text = text.replace("\n", "\\n")
    return text if len(text) <= MAX_VALUE_REPR else text[:MAX_VALUE_REPR] + "...<truncated>"


def is_game_frame(filename: str) -> bool:
    return any(hint in filename for hint in GAME_FILE_HINTS)


def is_ours(filename: str) -> bool:
    """このフレームがローダか mod のものか（＝ゲーム側ではない）。"""
    try:
        return filename.lower().startswith(RUNTIME_DIR.lower())
    except Exception:
        return False


def owner_of(code):
    """このコードを持っているクラスとメソッドの名前。分からなければ None。

    `method_1` / `execute` /
    `end_phase` のような名前は多くのマネージャが共有しているので、**関数名だけでは経路が特定できない**（クエストクリアの解散はトレースに `method_1 (instantale.py:6602)` としか出ない。
    GAME.md §2.8）。

    `__main__` のクラスを舐めて**同じコードオブジェクト**を探せば、
    推測なしに持ち主が決まる。
    稀にしか呼ばれない場所で使う前提なので総当たりでよい。
    """
    module = sys.modules.get("__main__")
    if module is None or code is None:
        return None
    try:
        entries = list(vars(module).items())
    except Exception:
        return None
    for cls_name, cls in entries:
        if not isinstance(cls, type):
            continue
        try:
            members = list(vars(cls).items())
        except Exception:
            continue
        for attr_name, member in members:
            func = getattr(member, "__func__", member)
            if getattr(func, "__code__", None) is code:
                return "{}.{}".format(cls_name, attr_name)
    return None


class MethodWatch(object):
    """指定したクラスのメソッドが**いまスタックに載っているか**を答える。

        watch = frames.MethodWatch(("QuestEndManager",))
        label = watch.current()      # 'QuestEndManager.method_1' か None

    `owner_of` と根拠は同じ（Nuitka でも `__code__` は引ける）だが、
    あちらの「全クラス総当たり」と違って**先に表を作っておく**ので、
    毎回の判定は辞書引き 1回で済む。
    フックの中から呼ぶ前提なので、この差が効く。

    3つとも決めつけないのが要点:

    * **段数で数えない**。
      `@ctx.wrap` の層が1段挟まる（`302_` が実際に踏んだ。GAME.md §2.6）。
      スタックを上から順に見て、知っているコードに出会うまで遡る
    * **関数名で決めない**。
      `method_1` / `execute` は12個のマネージャが持つ
    * **コードオブジェクトが引けないビルドもありうる**。
      そのときだけ名前で拾い、
      `owner_of` で持ち主クラスまで確かめる（重いので予備に限る）

    `on_warn` を渡すと、見張る相手が `__main__` に居ないときに mod のログへ書ける。

    **見張る相手を自分で包んではいけない**（`306_` がオフライン検証で踏んだ）。
    表を作るのは最初に呼ばれた時点なので、
    そこに載っているのは既に**ローダのラッパのコードオブジェクト**で、
    これは `patch.py` の全パッチが共有している（ラッパは関数として別物でも
    `__code__` は同一）。
    結果、包まれた関数が1つでもスタックに載っていれば「その中」と答えるようになる。
    包む対象と見張る対象が重なるなら、
    スタックを見るのをやめて**自分のラッパでスレッドごとの印を立てる**方が正確で速い（`306_party_train_exp` がその形）。
    """

    def __init__(self, class_names, method_names=("method_1", "execute"),
                 max_stack=60, on_warn=None):
        self.class_names = tuple(class_names)
        self.method_names = tuple(method_names)
        self.max_stack = max_stack
        self.on_warn = on_warn
        self._codes = None

    def _warn(self, message):
        if self.on_warn is None:
            return
        try:
            self.on_warn(message)
        except Exception:
            pass

    def codes(self):
        """`コードオブジェクト -> 'クラス名.メソッド名'` の表。1度だけ引いて覚える。

        空の表を覚えることもある（そのビルドでは引けなかった、という答え）。
        そのときは `current` が名前で見る予備に落ちる。
        """
        if self._codes is not None:
            return self._codes
        module = sys.modules.get("__main__")
        found = {}
        for cls_name in self.class_names:
            cls = getattr(module, cls_name, None) if module is not None else None
            if not isinstance(cls, type):
                self._warn("WARN {} is not in __main__; cannot watch it".format(cls_name))
                continue
            for attr_name in self.method_names:
                member = getattr(cls, attr_name, None)
                func = getattr(member, "__func__", member)
                code = getattr(func, "__code__", None)
                if code is None:
                    continue
                found[code] = "{}.{}".format(cls_name, attr_name)
        self._codes = found
        if found:
            self._warn("watching {}".format(", ".join(sorted(set(found.values())))))
        else:
            # ここが出たら本命の判定は効かない。
            # 名前で見る予備に落ちる。
            self._warn("WARN no code object resolved for {}; falling back to frame names"
                       .format(", ".join(self.class_names) or "(nothing)"))
        return found

    def current(self):
        """いま見張っているメソッドの中なら `'クラス名.メソッド名'`、外なら None。"""
        codes = self.codes()
        for index in range(1, self.max_stack):
            try:
                frame = sys._getframe(index)
            except Exception:
                break
            code = frame.f_code
            if codes:
                label = codes.get(code)
                if label is not None:
                    return label
            elif code.co_name in self.method_names:
                owner = owner_of(code)
                if owner and owner.split(".")[0] in self.class_names:
                    return owner
        return None


def caller(depth: int = 4, skip_hints=()) -> str:
    """呼び出し元の連鎖を「こちら側のフレームを飛ばして」返す。

    **段数を数えてはいけない。**
    `@ctx.wrap` の層が1段挟まるので、`sys._getframe(2)` のように数えた段数は合わず、
    呼び出し元が `wrapper (…/patch.py)` になる。
    段数ではなく**ファイル名**で判定し、
    ローダと mod 自身のフレームを飛ばして最初のゲーム側フレームから数段ぶん並べる。

    `skip_hints` に部分文字列を渡すと、
    そのファイルも飛ばす（pydantic の内部を越えてゲーム側まで遡りたいときなど）。
    """
    chain, index = [], 1
    while len(chain) < depth and index < 40:
        try:
            frame = sys._getframe(index)
        except Exception:
            break
        index += 1
        code = frame.f_code
        name = code.co_filename
        if is_ours(name):
            continue                       # ローダと mod 自身のフレーム
        if any(hint in name for hint in skip_hints):
            continue
        chain.append("{} ({}:{})".format(
            owner_of(code) or code.co_name, name, frame.f_lineno))
    return " <- ".join(chain) if chain else "?"


def format_locals(exc_traceback, depth: int = DEFAULT_FRAME_DEPTH) -> str:
    """トレースバックのうち、ゲーム側のフレームのローカル変数を書き出す。"""
    # まずトレースバックをリストにする。
    # tb_next は一方向にしかたどれないため。
    frames = []
    tb = exc_traceback
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    if not frames:
        return "  (no traceback frames)"

    parts: list[str] = []
    shown = 0
    # 内側（失敗した場所に近い方）から見ていく。
    # 原因に近いほど情報量が多い。
    for tb in reversed(frames):
        if shown >= depth:
            break
        frame = tb.tb_frame
        code = frame.f_code
        if not is_game_frame(code.co_filename):
            continue      # ライブラリ側のフレームは飛ばす
        shown += 1
        # 関数名だけでは足りない。
        # `method_1` / `execute` は多くのマネージャが持っているので、
        # 持ち主のクラスまで名指しする（owner_of の説明）。
        parts.append("  frame: {}:{} in {}".format(
            code.co_filename, tb.tb_lineno, owner_of(code) or code.co_name))
        try:
            items = list(frame.f_locals.items())
        except Exception:
            parts.append("    <f_locals unavailable>")
            continue
        for name, value in items[:MAX_LOCALS_PER_FRAME]:
            parts.append("    {:<24} = {}".format(name, repr_value(value)))
        if len(items) > MAX_LOCALS_PER_FRAME:
            parts.append("    ... {} more local(s)".format(len(items) - MAX_LOCALS_PER_FRAME))
    return "\n".join(parts) if parts else "  (no game frames in traceback)"


def describe_instance(obj, attrs=("id", "npc_id", "name", "npc_name", "character_id")) -> str:
    """オブジェクトを、識別子になりそうな属性だけで要約する。

    ソースが読めないので、どの属性が実在するかは事前に分からない。
    そのため候補を並べておき、実際に持っているものだけを拾う形にしている。

    **`hasattr` は使わない**（`attr()` の説明）。
    トリップワイヤを仕掛けたクラスが渡ってくると、
    計測しただけで「属性が無い」の記録が量産される。
    `200_probe_bug_sites` にはその注意書きがあるのに、
    そこから呼ばれているこの関数が `hasattr` を使っていた。
    同じ知見の反映漏れ。
    """
    bits = ["{}".format(type(obj).__name__)]
    for name in attrs:
        value = attr(obj, name)
        if value is MISSING:
            continue
        # 値だけでなく型も出す。
        # キーの型が問題になるバグがあったため。
        bits.append("{}={!r}({})".format(name, value, type(value).__name__))
    return " ".join(bits)
