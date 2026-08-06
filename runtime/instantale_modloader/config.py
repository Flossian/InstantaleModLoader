# -*- coding: utf-8 -*-
"""mod ごとの設定値。宣言は `mod.json`、利用者が選んだ値は `mod_settings.json`。

この仕組みが入る前、設定は「mod の .py を開いて先頭の定数を書き換える」だった。
動きはするが、フレームワークとしては2つ困ることがある。

  * **GUI から見えない。** 一覧に出るのは名乗りだけで、何が変えられるか分からない
  * **mod を更新すると設定が消える。** 値がコードの中にあるので、新しい版で
    ファイルを差し替えた瞬間に利用者の選択が上書きされる

そこで、値の置き場所をコードの外へ出す。

    mods/300_event/mod.json         "settings" に**何が変えられるか**を宣言する
    mods/300_event/event.py         `EVENT_MODE = "conversation"` ← 既定値。そのまま残す
    settings/mod_settings.json      利用者が選んだ値だけ

ローダは mod を読み込んだ**後・apply() を呼ぶ前**に、選ばれた値をモジュールの
グローバルへ書き込む（`apply_to_module`）。mod のコードは何も変えなくてよく、
入れ子の関数がグローバルとして定数を読んでいてもそのまま新しい値を見る。

`mod_settings.json` を `mods/` の中に置かないのは、そこが配布物そのものだから
（TECH.md: mods/ は読む専用、書くのは out/）。mod のフォルダを丸ごと差し替えても
利用者の設定は残る。置き場所は配布フォルダ直下の `settings/` で、**利用者が
選んだものは全部ここに集める**（GUI のウィンドウ位置なども同じ場所）。

**既定値が2箇所に書かれる**ことは認めている。コードの定数（実際に使われる値）と
`mod.json` の `"default"`（GUI が表示に使う値）で、GUI は mod のコードを import
しない決まりなので前者を読めない。ずれは `tools/check_mods.py` が AST で静的に
突き合わせて報告する。

宣言の形:

    "settings": {
      "EVENT_MODE": {
        "type": "choice", "values": ["conversation", "narration"],
        "default": "conversation",
        "label": {"ja": "イベントの出方", "en": "Event style"}
      },
      "COOLDOWN_MOVES": {"type": "int", "default": 3, "min": 0, "max": 99},
      "CHANCE_OVERRIDE": {"type": "float", "default": null, "allow_null": true}
    }

扱う型は `bool` / `int` / `float` / `str` / `choice` だけ。辞書やタプルの設定
（`KINDS` など）は宣言しない。GUI の1行で編集できる形に収まらず、無理に載せると
「JSON を手で書く欄」になって、コードを直接編むより分かりにくい。そういう設定は
コード側に置いたままにする。

ただし**プレイヤーの体験に関わる設定なら、宣言できる形に割るほうを選ぶ**。施設別の
発生率は元々 `CHANCE_BY_TYPE` という辞書1つだったが、`CHANCE_INN` / `CHANCE_GUILD`
… と種別ごとの `float` に割ってある（施設種別はゲーム側で閉じた集合なので、
1つずつ並べても項目が際限なく増えない）。
"""

from __future__ import annotations

import json
import os

from . import log, log_exc, write_json

# 利用者が選んだ値の置き場所。配布フォルダ直下の settings/（runtime/ の1つ上）。
STORE_NAME = "mod_settings.json"
SETTINGS_DIR_NAME = "settings"

#: ローダ自身の切り替え。**mod ごとの設定とは別ファイル**にしてある。
#: `mod_settings.json` の中身は「mod フォルダ名 -> 値」で、ここに mod ではない
#: キーを混ぜると `load_store()` の形が崩れる。
#:
#: GUI の `gui.json` にも置けない。あれは GUI しか読まない覚え書き（窓の位置など）
#: だが、こちらは**ゲームの中で `discover()` が読む**。両者が同じファイルを指せる
#: 場所は `settings/` だけで、GUI もローダもここの `settings_dir()` を通っている。
FLAGS_NAME = "loader.json"

TYPES = ("bool", "int", "float", "str", "choice")


def settings_dir(runtime_dir: str) -> str:
    return os.path.join(os.path.dirname(runtime_dir), SETTINGS_DIR_NAME)


def store_path(runtime_dir: str) -> str:
    return os.path.join(settings_dir(runtime_dir), STORE_NAME)


def flags_path(runtime_dir: str) -> str:
    return os.path.join(settings_dir(runtime_dir), FLAGS_NAME)


# --------------------------------------------------------------------------
# 宣言の読み取り
# --------------------------------------------------------------------------
def _label(value, fallback: str) -> dict:
    """`{"en":..., "ja":...}` でも素の文字列でも読む。名乗りと同じ規則。"""
    if isinstance(value, dict):
        en = str(value.get("en") or "").strip()
        ja = str(value.get("ja") or "").strip()
    else:
        en, ja = (str(value).strip() if value is not None else ""), ""
    en = en or ja or fallback
    return {"en": en, "ja": ja or en}


def normalize_decls(raw) -> dict:
    """`mod.json` の "settings" を扱いやすい形に均す。壊れた宣言は落とす。

    ここで例外にしないのは名乗りと同じ理由で、**設定の書き方を間違えた mod が
    読み込まれなくなるのは行き過ぎ**だから。落とした宣言はログに残り、
    `tools/check_mods.py` が注入前に報告する。
    """
    if not isinstance(raw, dict):
        return {}

    decls: dict[str, dict] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(spec, dict):
            log("settings: {!r} の宣言がオブジェクトではない".format(name), level="WARN")
            continue
        kind = str(spec.get("type") or "").strip()
        if kind not in TYPES:
            log("settings: {!r} の type が不明（{!r}）".format(name, kind), level="WARN")
            continue
        values = spec.get("values")
        if kind == "choice":
            if not isinstance(values, list) or not values:
                log("settings: {!r} は choice なので \"values\" が要る".format(name),
                    level="WARN")
                continue
            values = list(values)
        else:
            values = None
        decls[name] = {
            "name": name,
            "type": kind,
            "default": spec.get("default"),
            "values": values,
            "allow_null": bool(spec.get("allow_null")),
            "min": spec.get("min"),
            "max": spec.get("max"),
            "label": _label(spec.get("label"), name),
            "note": _label(spec.get("note"), ""),
        }
    return decls


# --------------------------------------------------------------------------
# 値の検査
# --------------------------------------------------------------------------
def coerce(decl: dict, value):
    """宣言に照らして値を整える。`(ok, 整えた値, 理由)` を返す。

    文字列から拾えるようにしてあるのは、`mod_settings.json` を手で書いた場合と、
    GUI の入力欄（tkinter は常に文字列を返す）の両方を同じ経路で通すため。
    """
    kind = decl["type"]
    if value is None:
        if decl["allow_null"]:
            return True, None, ""
        return False, None, "null は許されていない"

    try:
        if kind == "bool":
            if isinstance(value, bool):
                out = value
            else:
                text = str(value).strip().lower()
                if text in ("true", "1", "yes", "on"):
                    out = True
                elif text in ("false", "0", "no", "off"):
                    out = False
                else:
                    return False, None, "真偽値として読めない: {!r}".format(value)
        elif kind == "int":
            out = int(str(value).strip()) if not isinstance(value, bool) else int(value)
        elif kind == "float":
            out = float(str(value).strip()) if not isinstance(value, bool) else float(value)
        elif kind == "str":
            out = str(value)
        elif kind == "choice":
            out = value if value in decl["values"] else str(value)
            if out not in decl["values"]:
                return False, None, "選択肢の外: {!r}（{}）".format(
                    value, " / ".join(repr(v) for v in decl["values"]))
        else:
            return False, None, "type が不明: {!r}".format(kind)
    except (TypeError, ValueError):
        return False, None, "{} として読めない: {!r}".format(kind, value)

    for bound, cmp, word in (("min", lambda a, b: a < b, "下限"),
                             ("max", lambda a, b: a > b, "上限")):
        limit = decl.get(bound)
        if limit is not None and isinstance(out, (int, float)) and cmp(out, limit):
            return False, None, "{}を外れている（{} {}）".format(word, bound, limit)
    return True, out, ""


def resolve(decls: dict, chosen) -> dict:
    """宣言の既定値に、利用者が選んだ有効な値を重ねた結果を返す。

    読めない値は**黙って捨てて既定に倒す**。設定ファイルが壊れているせいで mod が
    1つも動かないのは割に合わない（`load_order.json` が壊れていても必ず何らかの順で
    動かす、という判断と同じ）。捨てたことはログに残す。
    """
    values = {name: decl["default"] for name, decl in decls.items()}
    if not isinstance(chosen, dict):
        return values

    for name, raw in chosen.items():
        decl = decls.get(name)
        if decl is None:
            log("settings: {!r} は宣言されていない（無視した）".format(name), level="WARN")
            continue
        ok, value, why = coerce(decl, raw)
        if not ok:
            log("settings: {!r} = {!r} を使えない（{}）。既定 {!r} に倒す".format(
                name, raw, why, decl["default"]), level="WARN")
            continue
        values[name] = value
    return values


# --------------------------------------------------------------------------
# 置き場所の読み書き
# --------------------------------------------------------------------------
def load_store(runtime_dir: str) -> dict:
    """`mod_settings.json` を `{mod フォルダ名: {設定名: 値}}` として返す。"""
    path = store_path(runtime_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        log_exc("cannot read {}".format(path))
        return {}
    if not isinstance(data, dict):
        log("{}: オブジェクトではない（無視した）".format(STORE_NAME), level="WARN")
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def load_flags(runtime_dir: str) -> dict:
    """`loader.json` を読む。無ければ `{}`（＝全て既定＝切）。

    このファイルは**配布物に入っていない**。利用者が何か切り替えて初めてできるので、
    「無い＝デバッグモードは切」が自然に成り立つ。
    """
    path = flags_path(runtime_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        log_exc("cannot read {}".format(path))
        return {}
    if not isinstance(data, dict):
        log("{}: オブジェクトではない（無視した）".format(FLAGS_NAME), level="WARN")
        return {}
    return data


def debug_mode(runtime_dir: str) -> bool:
    """デバッグモードが入っているか。

    入れると `"debug": true` の mod（計測系）が読み込まれ、GUI の一覧にも出る。
    切っている間は**一覧にも出ないし読み込まれもしない**（`discover()`）。

    切り替えが効くのは `discover()` を通る時＝次の注入から。動いているゲームには
    届かない（既に当たっているパッチをファイル1つで剥がす経路は無い）。
    """
    return bool(load_flags(runtime_dir).get("debug"))


def _save_settings_json(runtime_dir: str, path: str, data: dict) -> None:
    """`settings/` の JSON を書く。**書けなければ例外を投げる。**

    書き方（隣に書いてから差し替える）は `write_json()` に一本化してある。
    ここだけ戻り値ではなく例外にしているのは、**呼び出し元が GUI だから**。
    MOD の書き込みはゲームのスレッドで走るので黙って False を返すのが正しいが、
    こちらは利用者が「保存」を押した結果で、失敗したら画面に出す必要がある
    （`gui.py` が捕まえてダイアログにする）。黙って False を返すと、
    設定が保存されていないのに保存されたように見える。
    """
    # settings/ は配布物に入っていない（利用者が何か変えて初めてできる）。
    os.makedirs(settings_dir(runtime_dir), exist_ok=True)
    if not write_json(path, data, indent=2, sort_keys=True):
        raise OSError("cannot write {}".format(path))


def save_flags(runtime_dir: str, data: dict) -> None:
    """`loader.json` を書く。GUI の切り替えから呼ばれる。"""
    _save_settings_json(runtime_dir, flags_path(runtime_dir), dict(data))


def save_store(runtime_dir: str, data: dict) -> None:
    """`mod_settings.json` を書く。**既定と同じ値は書かない**のは呼び出し側の責任。

    空の mod は落としてから書く。設定を既定に戻したときに `{"300_x": {}}` が
    溜まっていくと、差分を見たときに何を変えたのか分からなくなる。
    """
    trimmed = {mod: dict(values) for mod, values in sorted(data.items()) if values}
    _save_settings_json(runtime_dir, store_path(runtime_dir), trimmed)


# --------------------------------------------------------------------------
# モジュールへの適用
# --------------------------------------------------------------------------
def apply_to_module(module, decls: dict, chosen) -> dict:
    """解決した値をモジュールのグローバルへ書き込み、実際に効いた値を返す。

    **apply() を呼ぶ前に**通すこと。apply() の中で作られる入れ子の関数は、定数を
    自分のクロージャではなくモジュールのグローバルとして読むので、ここで書き換えて
    おけば mod のコードに手を入れずに設定が効く。

    宣言されているのにモジュールに無い名前は書き込まずに警告する。`@patch` が
    「属性が無ければ撥ねる」のと同じ理由で、設定名の打ち間違いが「効いているように
    見える」のを防ぐため（`setattr` は黙って新しい名前を作ってしまう）。
    """
    values = resolve(decls, chosen)
    effective: dict = {}
    for name, value in values.items():
        if not hasattr(module, name):
            log("settings: {} に {!r} という定数が無い（宣言だけある）".format(
                getattr(module, "__name__", "?"), name), level="WARN")
            continue
        current = getattr(module, name)
        effective[name] = value
        if current == value:
            continue
        try:
            setattr(module, name, value)
        except Exception:
            log_exc("settings: {!r} を書き込めなかった".format(name))
            effective[name] = current
            continue
        log("  setting {} = {!r} (default {!r})".format(name, value, current))
    return effective
