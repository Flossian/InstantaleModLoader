# -*- coding: utf-8 -*-
r"""MOD 同梱の設定画面（`mod.json` の `"tool"`）が共有する土台。TECH.md §3.12。

`tool.py` は**ゲームの外**で走る tkinter スクリプト。
どの道具も同じことを最初にする ― 場所を決め、設定を読み、窓の大きさを思い出し、
配色を借り、保存のときに壊れない書き込みをする。
これを6本に写していたら実際にずれた（`save_window` が4変種になり、
そのうち1本は最大化した窓の寸法を壊していた）。

##### なぜ `instantale_modloader` ではなく `tools\` なのか

| | 置き場 | 理由 |
|---|---|---|
| ゲームの中でも要るもの | `instantale_modloader.*` | 注入したモジュールから引ける唯一の場所 |
| ゲームの外だけのもの | `tools\modtool.py`（ここ） | ローダ package はゲームが boot で読む。tkinter 依存を足さない |

裏返しの決まり: **ゲームの中のコードは `tools\` を import しない**
（配布物ではローダ側にしか無く、注入時の `sys.path` にも無い）。
セーブの読み方のように両側で要るものは `instantale_modloader.saves` に在る。

##### 道具側に書く仕掛け（これだけ）

    MOD_DIR = os.path.dirname(os.path.abspath(__file__))
    _ROOT = os.environ.get("IML_ROOT") or ""
    for _tools in ([os.path.join(_ROOT, "tools")] if _ROOT else []) + [
            os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
        if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
            sys.path.insert(0, _tools)

    import modtool

3つ上のフォールバックは**省略できない**。
`IML_ROOT` が指す先に `tools\` が無い場合（オフラインの検査は `runtime\` だけの
仮フォルダを渡す）と、環境変数が無い直接起動（§3.12 の約束）の両方がここを通る。
`_ROOT` が空のとき `os.path.join("", "tools")` は相対 `"tools"` になり、
`cwd` は MOD フォルダなので MOD 自身が `tools\` を持つと誤爆する ― だから空なら候補に入れない。

##### ここは状態を持たない

`MOD_DIR` をモジュール変数に持たず、全部の関数が引数で受ける。
オフラインの検査は1プロセスで複数の `tool.py` を読み込むので、
状態を持つと2本目が1本目の場所を掴む。
"""

import io
import json
import os
import sys

#: 窓の大きさと位置を残す先（設定画面が自分の窓を覚えるのと同じファイル）。
GUI_CONFIG = ("settings", "gui.json")

#: `gui.json` の中で道具の窓を集めている項。鍵は MOD のフォルダ名。
WINDOW_KEY = "tool_window"


# ----------------------------------------------------------------- 名前と場所
def mod_name(mod_dir):
    """`mod_settings.json` と `gui.json` で使う MOD の名前（フォルダ名）。

    番号込み（`322_battle_bgm`）。設定の置き場の鍵はローダ側もこの形なので、
    番号を振り直すと**その MOD の設定は既定に戻る**。
    戻ってよいことにしてある（番号の振り直しは配る前の作業で、遊んでいる人には起きない）。

    `normpath` を通すのは、末尾に区切りが付いた `mod_dir` を渡されても
    `basename` が空文字にならないようにするため。
    """
    return os.path.basename(os.path.normpath(mod_dir))


def state_dirname(mod_dir):
    r"""`state\` の下のフォルダ名。番号を落とした形（各 MOD の本体の定数と同じ）。

    `mod_settings.json` の鍵（`mod_name`）と違って**番号を落とす**。
    こちらは遊びの続きが入っているので、番号を振り直しても
    控えが行方不明になってはいけない（§3.11）。

    落とし方は最初の `_` まで。`split("_", 1)[-1]` にしてあるので、
    `_` を含まない名前（`local/` に置いた手元の MOD など）でも
    添字の外に出ずに元の名前がそのまま返る。
    """
    return mod_name(mod_dir).split("_", 1)[-1]


def locate(mod_dir):
    r"""`(root, state_dir, game_dir)`。環境変数が無ければ自分の位置から組む。

    `root` は配布フォルダの根（`runtime\` と `settings\` の親）。
    `game_dir` は `instantale.exe` の在る所で、**セーブはそこには無い**
    （セーブの置き場は `instantale_modloader.saves.data_dir()`）。
    渡されていなければ設定画面が覚えている `game_path` から組む。
    """
    # 3つ上は `runtime/mods/<MOD>/` の親の親の親（＝配布フォルダの根）。
    # 環境変数を先に見るのは、`IML_ROOT` が別の場所を指す使い方があるため
    # （検証用の仮フォルダ、`local/` に置いた MOD）。
    root = os.environ.get("IML_ROOT") or os.path.normpath(
        os.path.join(mod_dir, os.pardir, os.pardir, os.pardir))
    state_dir = os.environ.get("IML_STATE_DIR") or os.path.join(root, "state")
    game_dir = os.environ.get("IML_GAME_DIR") or ""
    if not game_dir:
        # 直接起動（`python runtime/mods/322_battle_bgm/tool.py`）では環境変数が無い。
        # 設定画面が覚えているゲームの場所を借りると、そのときも曲や絵が見える。
        # `AttributeError` も捕るのは、`gui.json` が配列だったときに
        # `.get` が無くて落ちるため（壊れた設定で道具が開かないのは割に合わない）。
        try:
            with io.open(gui_config_path(root), encoding="utf-8") as fh:
                game_path = json.load(fh).get("game_path") or ""
            if game_path:
                game_dir = os.path.dirname(game_path)
        except (OSError, ValueError, AttributeError):
            game_dir = ""
    return root, state_dir, game_dir


def gui_config_path(root):
    r"""`settings\gui.json`。設定画面と道具が同じファイルを分けて使う（§3.12）。"""
    return os.path.join(root, *GUI_CONFIG)


# ----------------------------------------------------------------- ローダを引く
def add_loader_path(root="", mod_dir=""):
    r"""`instantale_modloader` を import できるようにする。足した `runtime\` を返す。

    `root\runtime` を先に見て、無ければ `mod_dir` の2つ上（`runtime\mods\<MOD>\` の親の親）。
    どちらも無ければ空文字（呼び側の `except` に任せる）。

    **2つ目の候補は省略できない。**
    `IML_ROOT` が指す先に `runtime\` が無いことがあり（検証用の仮フォルダ）、
    そのとき「同梱のローダを使う」に倒れてほしい。
    `322_` の写しが最初からこの2段だったのを、そのまま持ってきている。

    `instantale_modloader` フォルダの実在を見てから `sys.path` に足すのは、
    在りもしない場所を先頭に積むと後続の import が遅くなるため。
    """
    for runtime in (os.path.join(root, "runtime") if root else "",
                    os.path.normpath(os.path.join(mod_dir, os.pardir, os.pardir))
                    if mod_dir else ""):
        if runtime and os.path.isdir(os.path.join(runtime, "instantale_modloader")):
            if runtime not in sys.path:
                sys.path.insert(0, runtime)
            return runtime
    return ""


# 下の3つは「`sys.path` を整えてから import する」を1行にするための口。
# import を関数の中に置いているのは、`modtool` を import しただけでは
# ローダを掴まないようにするため（`check_mods.py` のような、ローダを別に
# 用意している側から読まれても邪魔をしない）。
def loader(root="", mod_dir=""):
    """`instantale_modloader` そのもの（`write_json` を引くのに使う）。"""
    add_loader_path(root, mod_dir)
    import instantale_modloader
    return instantale_modloader


def config_module(root="", mod_dir=""):
    """`instantale_modloader.config`（宣言の読み方と設定の置き場）。"""
    add_loader_path(root, mod_dir)
    from instantale_modloader import config
    return config


def saves_module(root="", mod_dir=""):
    """`instantale_modloader.saves`（ディスクのセーブの読み方）。

    道具は別プロセスで `app` が無いので、世界の一覧はセーブを読んで作る。
    その知識はゲームの中でも要るのでローダ側に在る（§3.2.3）。
    """
    add_loader_path(root, mod_dir)
    from instantale_modloader import saves
    return saves


def world_filename(root, key, mod_dir=""):
    """世界の鍵からファイル名。本体と同じ規則（`state.world_filename`）。"""
    try:
        add_loader_path(root, mod_dir)
        from instantale_modloader import state
        return state.world_filename(key)
    except Exception:
        return key + ".json"


def world_name(save, fallback="", root="", mod_dir=""):
    """セーブの辞書から世界名。本体と同じ見方（`state.world_key_of_dict`）。

    鍵を探す順（`world_name` → `name` → `title`）を道具側に写さないための口。
    写した版が `name` の1鍵しか見ておらず、他の鍵で名前を持つ世界を取りこぼした
    実例がある（`state.py` の `world_key_of_dict` の docstring）。
    """
    try:
        add_loader_path(root, mod_dir)
        from instantale_modloader import state
        return state.world_key_of_dict(save, fallback)
    except Exception:
        return fallback


# ----------------------------------------------------------------- 宣言と設定
def manifest(mod_dir):
    """`mod.json`。読めなければ `{}`。"""
    try:
        with io.open(os.path.join(mod_dir, "mod.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def decls(mod_dir, root=""):
    """`mod.json` の `"settings"` を宣言の形（`config.normalize_decls`）で。

    **写しを持たない。** 項目の名前・型・既定値・上下限・選択肢は宣言が唯一の出所で、
    本体の定数とのずれは `tools/check_mods.py` が見ている。

    以前は各 `tool.py` が `SETTING_DEFAULTS = {...}` を手で書いていた。
    それは本体の定数と `mod.json` に続く**3つ目の写し**で、しかも
    `check_mods.py` の突き合わせ（宣言と本体の2者）の外に居た。
    宣言から引くようにしたので、写しが消えたうえに既存の検査が効くようになった。

    読めなければ `{}`。呼び側は空を「宣言が無い MOD」と同じに扱えばよい。
    """
    try:
        return config_module(root, mod_dir).normalize_decls(manifest(mod_dir).get("settings"))
    except Exception:
        return {}


def defaults(mod_dir, root=""):
    """宣言の既定値だけ（各 `tool.py` の `SETTING_DEFAULTS` の代わり）。

    道具側は `SETTING_DEFAULTS = modtool.defaults(MOD_DIR)` と書けば
    呼び出し側を1行も変えずに済む。
    ただし**添字ではなく `.get()` で引くこと** ―
    `mod.json` が読めないと空になるので、`SETTING_DEFAULTS[key]` は
    窓を組む途中で `KeyError` になる（リテラルだった頃は絶対に在った）。
    """
    return dict((name, decl["default"]) for name, decl in decls(mod_dir, root).items())


def load_settings(root, mod_dir):
    """この MOD に効いている設定。読めない値は宣言の既定に倒れる（`config.resolve`）。

    ゲームの中でローダが行うのと**同じ関数**を通す。
    以前は道具ごとに `isinstance` を並べて型を見ていて、
    見方が4通りに枝分かれしていた（`bool` を弾く/弾かない、負値を 0 に切る/既定へ倒す）。
    `resolve` に寄せたので、画面に出る値とゲームに効く値が食い違わない。

    読めないときは既定に倒す。**画面が開かないより出したほうがよい** ―
    設定が壊れているせいで直す画面すら開かない、が一番困る。
    """
    found = decls(mod_dir, root)
    if not found:
        return {}
    try:
        config = config_module(root, mod_dir)
        store = config.load_store(os.path.join(root, "runtime"))
        return config.resolve(found, store.get(mod_name(mod_dir)))
    except Exception:
        return dict((name, decl["default"]) for name, decl in found.items())


def save_settings(root, mod_dir, values, strict=False):
    """既定と違う値だけ `mod_settings.json` に書く。他の MOD の項は触らない。

    `strict=True` のときは例外をそのまま通す。
    書けなかった理由（権限・ディスク）を画面に出す道具のため
    ― 「保存できませんでした」だけでは何を直せばよいか分からない。
    """
    try:
        config = config_module(root, mod_dir)
        runtime = os.path.join(root, "runtime")
        found = decls(mod_dir, root)
        name = mod_name(mod_dir)
        # 読んでから書く。他の MOD の項が同じファイルに同居しているので、
        # 丸ごと置き換えると隣を消す。
        store = config.load_store(runtime)
        # 既定と同じ値は書かない（§3.8）。
        # 書いてしまうと、後で本体の既定を変えたときに
        # 「触っていない項目が古い値に固定される」が起きる。
        # `key in found` を見るのは、宣言から消えた項目を拾わないため。
        changed = dict((key, value) for key, value in values.items()
                       if key in found and value != found[key]["default"])
        if changed:
            store[name] = changed
        else:
            # 全部既定なら項ごと消す。空の辞書を残すと
            # 「この MOD は設定を持っている」と読めてしまう。
            store.pop(name, None)
        config.save_store(runtime, store)
        return True
    except Exception:
        if strict:
            raise
        return False


def coerce_all(mod_dir, raw, root=""):
    """入力欄の値を宣言に照らして整える。`(値, 最初の不備)`。不備が無ければ第2要素は空。

    `load_settings` の `resolve` と違って、**駄目な値を既定へ倒さず不備として返す。**
    向きが逆なのは出所が違うため:

        ファイルから読む   壊れていても遊べたほうがよい → 既定へ倒す（`resolve`）
        画面に打たれた値   打った本人に直してもらう     → 断る（ここ）

    最初の不備で打ち切って、項目名を添えて返す。
    全部の不備を集めないのは、1つ直すと次が見えれば足りるため。
    """
    try:
        config = config_module(root, mod_dir)
    except Exception:
        return None, "設定の読み方が引けない"
    values = {}
    for key, decl in decls(mod_dir, root).items():
        ok, value, why = config.coerce(decl, raw.get(key))
        if not ok:
            return None, "{}: {}".format(decl["label"]["ja"], why)
        values[key] = value
    return values, ""


# ----------------------------------------------------------------- 書き込み
def write_json(root, path, data, indent=1):
    """ローダの `write_json`（tmp → fsync → replace）で書く。

    引けなければ同じ手順を自前で踏む。
    道具はローダより古い配布物の上でも開くので、書けない理由を作らない。

    隣に書いてから差し替えるのは、途中で電源が落ちても
    **半分書けたファイルを残さない**ため（§3.11.1）。
    `playlist.json` や `gui.json` が半分になると、次の起動で設定ごと消える。
    """
    try:
        return bool(loader(root).write_json(path, data, indent=indent))
    except Exception:
        pass
    # ここから下はローダを引けなかったときの写しではなく、**同じ手順の再実装**。
    # 写しに見えるが、消すと「古いローダの上では道具が保存できない」になる。
    tmp = path + ".tmp"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # `newline="\n"` は Windows で CRLF に化けるのを止める（差分が全行になる）。
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())      # replace の前に本当にディスクへ落とす
        os.replace(tmp, path)          # 同一ボリューム内なら原子的に入れ替わる
        return True
    except OSError:
        return False


# ----------------------------------------------------------------- 窓の記憶
def load_window(root, mod_dir):
    """前回の窓の大きさと位置。`{"geometry": "WxH+X+Y", "maximized": bool}`。無ければ空。"""
    try:
        with io.open(gui_config_path(root), encoding="utf-8") as fh:
            cfg = json.load(fh)
        entry = (cfg.get(WINDOW_KEY) or {}).get(mod_name(mod_dir)) or {}
        return entry if isinstance(entry, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save_window(root, mod_dir, window):
    """窓の大きさと位置を `gui.json` の `tool_window[<MOD 名>]` に残す。

    **最大化されていたら先に `normal` へ戻してから `geometry()` を取る。**
    最大化中の `geometry()` は画面いっぱいの寸法なので、そのまま残すと
    次に開いて「元に戻す」を押したときの大きさが壊れる
    （写しの1本が実際にこうなっていた）。

    他の覚えごと（ゲームの場所・ローダの窓）を落とさないよう、読んでから書く。
    残せなくても止めない ― 窓が使えないことと設定が残らないことは別。

    実測（VERIFICATION.md §3.58）: 最大化中の `geometry()` は `2576x1426` を返し、
    `normal` に戻してから取ると `1180x760`。前者を残すと、次に開いて
    「元に戻す」を押した窓が画面いっぱいのまま動かなくなる。
    """
    try:
        maximized = window.state() == "zoomed"
        if maximized:
            # **これが要る。** 最大化中の `geometry()` は画面いっぱいの寸法で、
            # 「元に戻したときの大きさ」ではない。
            # 写しの1本がこの2行を持っていなくて、実際に寸法が壊れていた。
            # `update_idletasks` は、戻した結果が `geometry()` に反映されるまで待つため
            # （待たないと戻す前の値を読む）。
            window.state("normal")
            window.update_idletasks()
        geometry = window.geometry()
        path = gui_config_path(root)
        # `gui.json` はローダの設定画面と共有している。
        # 丸ごと書くと `game_path` やローダ自身の窓の記憶を消すので、読んでから足す。
        try:
            with io.open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
        # 辞書でなければ捨てて作り直す。壊れた `gui.json` のせいで
        # 以降ずっと窓を覚えられないより、1回分の覚えを失うほうがよい。
        if not isinstance(cfg, dict):
            cfg = {}
        windows = cfg.get(WINDOW_KEY)
        if not isinstance(windows, dict):
            windows = {}
        windows[mod_name(mod_dir)] = {"geometry": geometry, "maximized": maximized}
        cfg[WINDOW_KEY] = windows
        write_json(root, path, cfg, indent=2)     # gui.json は設定画面と同じ体裁
    except Exception:
        # `window` が既に壊されている（閉じる順が違う）道もある。
        # 覚えられなかっただけなので、閉じる邪魔はしない。
        pass


def restore_window(root, mod_dir, window, fallback="900x640"):
    """`load_window` の内容を窓に当てる。覚えが無ければ `fallback`。

    `geometry` を当ててから `zoomed` にする順。逆にすると、
    最大化を解いたときの大きさが `fallback` のままになる。

    `zoomed` は Windows と一部の X11 でしか通らないので、
    通らない環境では大きさだけ当たって最大化されない（落とさない）。
    """
    remembered = load_window(root, mod_dir)
    window.geometry(remembered.get("geometry") or fallback)
    if remembered.get("maximized"):
        try:
            window.state("zoomed")
        except Exception:
            pass
    return remembered


# ----------------------------------------------------------------- 配色と書体
def setup_theme(window, root=""):
    r"""配色と書体は設定画面（`tools/gui.py`）のものを借りる。無ければ素の Tk。

    **戻り値は `gui` モジュールそのもの**（借り損なったときは `None`）。
    設定画面から借りたいものは配色だけではないので（`323_` は `check_images` も使う）、
    import に成功した口をそのまま渡す。

    **先にローダを import しておく。**
    `gui` は import しただけで自分の隣の `runtime\` を `sys.path` の先頭に入れるので、
    `IML_ROOT` が別の場所を指しているときに順序が逆だと違うローダを掴む。
    """
    try:
        if root:
            add_loader_path(root)
        import gui
        gui.setup_theme(window)
        return gui
    except Exception:
        return None


# ==========================================================================
#  宣言駆動のワールド別設定画面（TECH.md §3.12.1）
#
#  `mod.json` の "settings" を読んで2段の設定画面を組む。MOD 固有のコードは無い。
#
#      一括設定          全ワールド共通。`settings/mod_settings.json`
#      ワールド個別設定  `state/<MOD の控え>/<世界>.json`。
#                        一括設定と違う項目だけ書く。全部同じならファイルを消す
#
#  `130_currency_unit` と `314_area_move_custom` が同じ 501 行の写しを持っていた。
#  中身が完全に汎用（`mod.json` の name / description / settings しか読まない）
#  だったので、写しを増やす前にここへ移した（§3.12.1 が「3本目が要るときは
#  写しを増やさず、ローダの語彙へ移すこと」と書いていた宿題）。
#  道具側は `world_settings_main(MOD_DIR)` を呼ぶだけ。
# ==========================================================================

def shown(value):
    """説明に添える値の見せ方。空は（空）、真偽は ON / OFF、他はそのまま。

    空文字をそのまま出すと説明が「既定: 」で終わって、
    値が無いのか説明が切れているのか分からない。
    """
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return "（空）" if value == "" else str(value)


def world_store_path(root, state_dir, mod_dir, world):
    r"""その世界の控えの置き場。`state\<MOD の控え>\<世界名>.json`。

    ファイル名は `state.world_filename` が決める（§3.11）。
    使えない字を含む世界名を素で開くと、`open()` が失敗して広い `except` に吸われ、
    **控えが黙って空に倒れる**（遊びの続きが消えたことに気付けない）。
    ゲームの中の本体も同じ関数でファイル名を作るので、書いた先と読む先が一致する。
    """
    return os.path.join(state_dir, state_dirname(mod_dir),
                        world_filename(root, world, mod_dir))


def load_world_settings(found, path, base):
    """その世界の控えを一括設定に重ねた値。無ければ一括設定のまま。

    `base` は一括設定。控えは**差分だけ**入っているので、重ねて初めて全項目が揃う
    （§3.12.1 の「個別の控えには一括設定と違う項目だけ書く」の読み側）。
    """
    values = dict(base)
    try:
        with io.open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        # 控えが無いのが普通の状態（その世界を一度も触っていない）。
        # 壊れている場合も同じく一括設定に倒す。
        return values
    if isinstance(record, dict):
        # 本体（`refresh_world`）と同じく型で見る。`coerce` は入力欄の文字列用で、
        # ここで使うと手で書いた `[1, 2]` が文字列として通ってしまう。
        for key, decl in found.items():
            value = record.get(key)
            # `is not None` を先に見るのは、`False` や `0` や `""` を
            # 「無い」と取り違えないため（真偽値や空文字も正しい設定値）。
            # `type(...) is type(...)` にしてあるのは `isinstance` だと
            # `bool` が `int` として通るため（`True` が 1 の項目に入る）。
            if value is not None and type(value) is type(decl["default"]):
                values[key] = value
    return values


def save_world_settings(root, path, found, base, values):
    """一括設定と違う項目だけを書く。全部同じならファイルを消す。書けなければ False。

    差分だけにする理由は §3.12.1 と同じ。
    全項目を書くと、後で一括設定を変えたときに
    **触っていない項目までその世界だけ古い値に固定される。**

    全部同じならファイルを消すのは、空の控えを残すと
    「この世界は個別設定を持っている」と読めてしまうため。
    """
    record = dict((k, v) for k, v in values.items() if k in found and v != base.get(k))
    if not record:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass                       # 元から無い。消せた扱いでよい
        except OSError:
            return False               # 在るのに消せない（読み取り専用・掴まれている）
        return True
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return write_json(root, path, record, indent=2)


def _scrollable(parent):
    """縦にスクロールする枠。項目が窓に収まらない MOD（`314_` は 16 項目）のため。

    Canvas の中に Frame を置き、幅は Canvas に合わせ、高さは中身に任せる。
    返すのは中身を置く Frame。

    **中身が収まっているときは動かさない。**
    scrollregion を中身の高さのまま渡すと、車輪で枠の外まで動いてしまい、
    上に空きが出たまま戻らない（`130_` は4項目しかないので必ずこうなる）。
    領域の下端を「中身と枠の高いほう」にすると、収まっている間は
    つまみが溝を埋めて動く先が無くなる。
    """
    import tkinter as tk
    from tkinter import ttk

    canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0,
                       background=ttk.Style().lookup("TFrame", "background") or None)
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)

    def overflow():
        """はみ出している高さ（収まっていれば 0）。"""
        return max(0, inner.winfo_reqheight() - canvas.winfo_height())

    def fit(event=None):
        if event is not None and event.widget is canvas:
            canvas.itemconfigure(window, width=event.width)
        canvas.configure(scrollregion=(
            0, 0, canvas.winfo_width(),
            max(inner.winfo_reqheight(), canvas.winfo_height())))
        if not overflow():
            canvas.yview_moveto(0)      # 窓を広げて収まったときに空きを残さない

    inner.bind("<Configure>", fit)
    canvas.bind("<Configure>", fit)

    # 車輪は枠の上に居るときだけ受ける（タブが2つあるので、全体に束ねると裏の枠まで動く）。
    def wheel(event):
        if overflow():
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    return inner


class _Form(object):
    """宣言から作る入力欄の一組。bool は Checkbutton、choice は Combobox、他は Entry。

    `note_of(key)` は項目の説明の後ろに足す一言（既定や一括設定の値）。
    同じ画面に2組（一括設定とワールド個別設定）出るので、
    どちらの組かはこの一言だけで見分ける形にしてある
    （「既定: ゴールド」と「一括設定: ゴールド」）。

    **値は全部文字列で持つ**（`bool` だけ `BooleanVar`）。
    型に戻すのは保存の直前に `coerce_all` が1箇所で行う。
    入力欄ごとに型を持たせると、打ちかけの「1」を数として読もうとして
    途中の状態を弾いてしまう。
    """

    def __init__(self, parent, found, note_of):
        import tkinter as tk
        from tkinter import ttk

        self.decls = found
        self.vars = {}
        # 1列目（入力欄）だけ伸ばす。0列目のラベルは字の幅のまま。
        parent.columnconfigure(1, weight=1)
        # 1項目で2行使う（入力欄の行と、その下の説明の行）。だから row は 2 ずつ進む。
        row = 0
        for key, decl in found.items():
            ttk.Label(parent, text=decl["label"]["ja"]).grid(
                row=row, column=0, sticky="nw", padx=(0, 12), pady=(6, 0))
            if decl["type"] == "bool":
                var = tk.BooleanVar()
                ttk.Checkbutton(parent, variable=var).grid(row=row, column=1, sticky="w", pady=(6, 0))
            elif decl["type"] == "choice":
                var = tk.StringVar()
                ttk.Combobox(parent, textvariable=var, values=decl["values"],
                             state="readonly").grid(row=row, column=1, sticky="ew", pady=(6, 0))
            else:
                var = tk.StringVar()
                ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=(6, 0))
            self.vars[key] = var
            # 宣言の説明と、呼び側が足す一言を1行に繋ぐ。
            # 空の側を落としてから繋ぐので、片方だけでも余分な空白が出ない。
            note = " ".join(t for t in (decl["note"]["ja"], note_of(key)) if t)
            if note:
                # `wraplength` は px。折り返さないと `314_` の長い説明が窓を横に広げる。
                ttk.Label(parent, text=note, style="Faint.TLabel", wraplength=640,
                          justify="left").grid(row=row + 1, column=1, sticky="w", padx=(0, 8))
            row += 2

    def get(self):
        """入力欄のいまの値。`bool` 以外は文字列（`coerce_all` に渡す形）。"""
        return dict((key, var.get()) for key, var in self.vars.items())

    def set(self, values):
        """値を入力欄に流し込む。`bool` はそのまま、他は文字列にして入れる。

        `str()` を通すのは、`int` を `StringVar` に入れると
        Tk 側で文字列化されて `get()` の戻りと食い違うため。
        """
        import tkinter as tk
        for key, var in self.vars.items():
            var.set(values[key] if isinstance(var, tk.BooleanVar) else str(values[key]))


def build_world_settings_window(mod_dir, title="", blurb=""):
    """ワールド別設定の窓を組んで返す（`mainloop()` は呼ばない）。

    `title` と `blurb` を省くと `mod.json` の `name` / `description` から取る
    ― **MOD ごとに違うのはこの2つだけ**なので、道具側に書くことは何も残らない。
    """
    # tkinter は関数の中で import する。
    # `modtool` を import しただけで tkinter を要求しないため（`--dump` の道など）。
    import time
    import tkinter as tk
    from tkinter import messagebox, ttk

    info = manifest(mod_dir)
    # 題も一言も `mod.json` から。読めなければフォルダ名まで落として、それでも窓は開く。
    title = title or (info.get("name") or {}).get("ja") or mod_name(mod_dir)
    blurb = blurb or (info.get("description") or {}).get("ja") or ""

    root_dir, state_dir, _game_dir = locate(mod_dir)
    runtime = os.path.join(root_dir, "runtime")
    config = config_module(root_dir, mod_dir)
    saves = saves_module(root_dir, mod_dir)
    found = decls(mod_dir, root_dir)
    shared = load_settings(root_dir, mod_dir)
    # 世界の一覧はセーブを復号して `world_data` の名前を取る（フォルダ名ではない）。
    # ゲームの中で控えの鍵になるのがこの名前なので、ここで別のものを見せると
    # 「画面で選んだ世界」と「効く世界」がずれる。
    worlds = saves.world_names()
    store = saves.saves_dir()          # 画面の下に出す「どこを見たか」

    root = tk.Tk()
    root.title(title)
    # 下限を決めておく。これより狭いと入力欄と説明が重なる。
    root.minsize(760, 520)
    restore_window(root_dir, mod_dir, root, "900x640")
    # 配色はローダの設定画面から借りる。`Title.TLabel` などの style 名も向こうの定義。
    # **`restore_window` の後**に呼ぶ（先に呼ぶと `zoomed` が効かない環境がある）。
    setup_theme(root, root_dir)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    # --- 見出し
    ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel", wraplength=860, justify="left",
              text=blurb + " 設定は2段。一括設定はどの世界でも効き、"
                   "ワールド個別設定はその世界で一括設定と違う項目だけを持つ").pack(anchor="w", pady=(0, 6))

    # --- 下段のボタン。一覧より先に詰める（窓が低いときに押し出されないように）。
    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    # タブは最後に詰める。`expand=True` なので、上の見出しと下のボタンを
    # 差し引いた残り全部を取る。
    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True)

    # ---- 一括設定
    # 見出しの前後に空白を入れているのはタブの幅を稼ぐため（ttk はタブに padding を持てない）。
    bulk = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(bulk, text="  一括設定  ")
    ttk.Label(bulk, text="どの世界でも効く。settings\\mod_settings.json に入る（他の MOD と同じ）",
              style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
    bulk_bottom = ttk.Frame(bulk)
    bulk_bottom.pack(side="bottom", fill="x", pady=(6, 0))
    body = ttk.Frame(bulk, padding=(0, 0, 6, 0))    # 入力欄は grid、外は pack。混ぜないための子枠
    body.pack(fill="both", expand=True)
    shared_form = _Form(_scrollable(body), found, lambda k: "既定: " + shown(found[k]["default"]))
    shared_form.set(shared)
    ttk.Button(bulk_bottom, text="既定に戻す", command=lambda: shared_form.set(
        dict((k, d["default"]) for k, d in found.items()))).pack(side="right")

    # ---- ワールド個別設定
    per_world = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(per_world, text="  ワールド個別設定  ")
    ttk.Label(per_world, text="その世界だけ。一括設定と違う項目だけが state\\{}\\<世界名>.json に入る。"
              "セーブは世界名を読むだけで書かない".format(state_dirname(mod_dir)),
              style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
    picker = ttk.Frame(per_world)
    picker.pack(fill="x", pady=(0, 4))
    ttk.Label(picker, text="世界", style="Group.TLabel").pack(side="left", padx=(0, 8))
    names = [name for name, _folder in worlds]
    world_var = tk.StringVar(value=names[0] if names else "")
    world_box = ttk.Combobox(picker, textvariable=world_var, values=names, state="readonly")
    world_box.pack(side="left", fill="x", expand=True)
    world_bottom = ttk.Frame(per_world)
    world_bottom.pack(side="bottom", fill="x", pady=(6, 0))
    world_status = ttk.Label(world_bottom, style="Faint.TLabel")
    world_status.pack(side="left", fill="x", expand=True)
    world_status.configure(text="{} 世界（{}）".format(len(worlds), store)
                           if worlds else "世界が見つからない: " + store)
    body = ttk.Frame(per_world, padding=(0, 0, 6, 0))
    body.pack(fill="both", expand=True)
    world_form = _Form(_scrollable(body), found, lambda k: "一括設定: " + shown(shared[k]))
    ttk.Button(world_bottom, text="一括設定に戻す",
               command=lambda: world_form.set(shared)).pack(side="right")

    #: いま「保存済み」として画面が信じている値。未保存の変更の有無をこれと比べて出す。
    #: 辞書1つに束ねているのは、入れ子の関数から `nonlocal` を並べずに書き換えるため。
    #: `name` も入れているのは、世界を切り替えたときに「どの世界の控えを持っているか」
    #: が分からないと、切替の確認を出す相手を間違えるため。
    saved = {"shared": dict(shared), "world": dict(shared), "name": world_var.get()}

    def path_of(name):
        """選んでいる世界の控えのパス。世界が無い（セーブが1つも無い）ときは空。"""
        return world_store_path(root_dir, state_dir, mod_dir, name) if name else ""

    def load_into_form(name):
        """その世界の控えを入力欄に入れ、`saved` も揃える。

        世界が無いときは一括設定をそのまま見せる（空欄より分かる）。
        """
        values = load_world_settings(found, path_of(name), shared) if name else dict(shared)
        world_form.set(values)
        saved["world"] = dict(values)
        saved["name"] = name

    def as_shown(values):
        """型のある値を、入力欄が返す形（`bool` 以外は文字列）に揃える。

        比較の前に必ず通す。`_Form.get()` は `"7"` を返すのに `saved` は `7` を
        持っているので、素で比べると**開いた直後から「未保存の変更あり」になる。**
        """
        return dict((k, v if isinstance(v, bool) else str(v)) for k, v in values.items())

    def dirty():
        """未保存の変更があるか。2段のどちらかが動いていれば真。"""
        return (shared_form.get() != as_shown(saved["shared"])
                or world_form.get() != as_shown(saved["world"]))

    def on_world(_event=None):
        """世界を切り替えた。未保存の変更があれば捨てるか聞く。

        聞くのは**個別設定の側だけ**。一括設定はどの世界でも同じものなので、
        世界を切り替えても打ちかけの値が失われない。
        """
        name = world_var.get()
        if name == saved["name"]:
            return                     # 同じ世界を選び直した（何もしない）
        if world_form.get() != as_shown(saved["world"]) \
                and not messagebox.askyesno("世界の切替", "この世界の未保存の変更を破棄しますか？", parent=root):
            # 断られたので選択を戻す。`set` は `<<ComboboxSelected>>` を呼ばないので、
            # ここで再入にはならない。
            world_var.set(saved["name"])
            return
        load_into_form(name)

    world_box.bind("<<ComboboxSelected>>", on_world)
    load_into_form(world_var.get())

    status = ttk.Label(footer, style="Faint.TLabel",
                       text="一括設定は次の注入から、ワールド個別設定は次にその世界を見たときから効く")
    status.pack(side="left")

    def save():
        """一括設定と、選んでいる世界の個別設定を両方書く。どちらかが不備なら何も書かない。

        **検査を両方先に通してから書き始める。**
        片方書いてから2つ目で断ると、画面と保存済みの中身が食い違った状態で残る
        （個別設定は一括設定との差分なので、一括だけ書き換わると差分の意味が変わる）。

        順は一括設定 → 個別設定。個別は一括との差分なので、
        新しい一括設定を `shared` に入れてから差分を取る必要がある。
        """
        nonlocal shared
        new_shared, bad = coerce_all(mod_dir, shared_form.get(), root_dir)
        if bad:
            messagebox.showerror("一括設定を確かめてください", bad, parent=root)
            return False
        new_world, bad = coerce_all(mod_dir, world_form.get(), root_dir)
        if bad:
            messagebox.showerror("ワールド個別設定を確かめてください", bad, parent=root)
            return False
        try:
            # `strict=True` で例外を通してもらう。書けなかった理由（権限・掴まれている）
            # を出さないと、どこを直せばよいか分からない。
            save_settings(root_dir, mod_dir, new_shared, strict=True)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", "{}\n{}: {}".format(
                config.store_path(runtime), type(exc).__name__, exc), parent=root)
            return False
        shared = new_shared                # 差分の基準を更新（この後の `path_of` より前）
        saved["shared"] = dict(new_shared)
        name = world_var.get()
        if name and not save_world_settings(root_dir, path_of(name), found, shared, new_world):
            messagebox.showerror("保存に失敗しました", path_of(name), parent=root)
            return False
        # 世界が無いときは個別設定を持たないので、基準を一括設定に揃える。
        saved["world"] = dict(new_world) if name else dict(shared)
        # 書いた先を出す。控えが消えた（全部一括設定と同じ）ときもこのパスが出るが、
        # そのときファイルは無い。VERIFICATION.md §3.57 の #3 がその道。
        status.configure(text="保存しました {}  {}".format(
            time.strftime("%H:%M:%S"), path_of(name) if name else config.store_path(runtime)))
        return True

    def close():
        """閉じる。窓の大きさを残してから、未保存の変更を聞く。

        順が逆でないのは意図してこうしている。
        「保存してから閉じますか？」を取り消しても**窓の大きさは残る** ―
        ずらした位置を覚えるのは閉じるかどうかと関係がない。
        """
        save_window(root_dir, mod_dir, root)
        if dirty():
            answer = messagebox.askyesnocancel("未保存の変更", "変更を保存してから閉じますか？", parent=root)
            if answer is None:
                return                 # 取り消し（閉じない）
            if answer and not save():
                return                 # 保存を選んだが失敗した。閉じずに直させる
        root.destroy()

    ttk.Button(footer, text="閉じる", command=close).pack(side="right")
    ttk.Button(footer, text="保存", style="Accent.TButton", command=save).pack(side="right", padx=(0, 6))
    # 窓の × も同じ道を通す。通さないと窓の大きさが残らず、未保存の変更も黙って消える。
    root.protocol("WM_DELETE_WINDOW", close)
    return root


def world_settings_main(mod_dir):
    """道具の入口。窓を組んで回す。

    各 `tool.py` はこれを呼ぶだけの数行で済む（§3.12.1）。
    `build_world_settings_window` と分けてあるのは、`mainloop()` に入らずに
    窓を組んで中を数えたいことがあるため（`tools/tests/` と
    `tools/check_tool_screens.py`）。
    """
    build_world_settings_window(mod_dir).mainloop()
