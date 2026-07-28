# -*- coding: utf-8 -*-
"""ゲーム終了時に Kivy が落ちる問題を直す。crash_log.txt の 114 件中 47 件がこれ。

出ていたエラー:

    File "...\\kivy\\input\\providers\\wm_pen.py", line 119, in stop
    File "...\\kivy\\input\\providers\\wm_common.py", line 115, in _closure
    ctypes.ArgumentError: argument 3: TypeError: wrong type

Kivy は終了処理の中で、乗っ取っていたウィンドウプロシージャ（WndProc）を
元に戻そうとする。そのとき保存しておいたハンドラを ctypes が受け付けずに
落ちている。

これを最初に直した理由は2つある。

1つ目。これは stopTouchApp() から呼ばれる。つまり「本当に落ちた原因」の後始末を
している最中に発生する。crash_log.txt の該当箇所は例外なく
"During handling of the above exception, another exception occurred" で始まって
いて、本来知りたかった原因がこのエラーで上書きされてしまう。
先にこれを潰さないと、他のバグの調査が進まない。

2つ目。この処理は実質的に無意味である。直後に破棄されるウィンドウの WndProc を
戻しているだけなので、失敗しても目に見える影響はない。つまり握り潰しても安全。

パッチの中身は、元の処理をそのまま呼び、失敗したときだけ以下を行う。

  a. 実際に渡された引数の型をログに出す（原因を特定するため）
  b. ハンドラを整数のアドレスに変換して、ctypes の型チェックを通さない
     SetWindowLongPtrW で呼び直す
  c. それも駄目なら黙って諦める（終了処理を汚さない）

なお wm_pen と wm_touch はどちらも
`from ... import SetWindowLong_WndProc_wrapper` で関数を取り込んでいる。
そのため wm_common だけを書き換えても、この2つは元の関数を持ったままになる。
実際に効かせているのは ctx.wrap のエイリアス走査（patch.py 参照）で、
sys.modules を掃いて同じ関数を指している変数を全部張り替えている。
Nuitka 環境でパッチが「効かない」ときは、まずここを疑うとよい。
"""

import ctypes
from ctypes import wintypes

NAME = "Shutdown crash fix"
NAME_JA = "終了時クラッシュの修正"
VERSION = "1"
DESCRIPTION = "Stops the crash on exit and unmasks the real exception hidden behind it"
DESCRIPTION_JA = "ゲーム終了時に Kivy が落ちるのを抑え、隠れていた本来の例外を表に出す"
AUTHOR = "R01/Flossian"

GWL_WNDPROC_DEFAULT = -4

# user32 を自前で読み込み直している。
# ctypes の windll.user32 はプロセス全体で共有されていて、Kivy はそこの
# SetWindowLongPtrW に argtypes/restype を設定済み。同じオブジェクトを触ると
# Kivy 自身の呼び出しを壊してしまうので、別のハンドルを取って独立させている。
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_raw_set = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
_raw_set.restype = ctypes.c_ssize_t
_raw_set.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]


def _as_address(value):
    """WndProc のハンドラを、ただの整数アドレスに変換する。無理なら None。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value          # すでにアドレスの形
    try:
        # WINFUNCTYPE で作ったコールバックなどは、ポインタ経由で数値が取れる。
        addr = ctypes.cast(value, ctypes.c_void_p).value
    except Exception:
        return None
    return addr or None       # 0 は使えないので None と同じ扱いにする


def apply(ctx):
    import sys

    wm_common = sys.modules.get("kivy.input.providers.wm_common")
    if wm_common is None:
        ctx.log("kivy.input.providers.wm_common not loaded; nothing to patch", level="WARN")
        return

    # 本来は wm_common が持っている定数。念のため無い場合の既定値も用意しておく。
    gwl_wndproc = getattr(wm_common, "GWL_WNDPROC", GWL_WNDPROC_DEFAULT)

    @ctx.wrap("kivy.input.providers.wm_common:SetWindowLong_WndProc_wrapper")
    def guarded(orig, hWnd, wndProc):
        # 失敗したときだけでなく、呼ばれるたびにログを出している。
        # 修正を入れた後の最初の終了はきれいに終わったのに何も記録が残らず、
        # 「ガードが効いた」のか「今回はたまたま出なかった」のか区別できなかった。
        # 過去 47 件は 07-09〜07-25 にばらけていて、毎回出るわけではない。
        # なおこの関数が呼ばれるのは入力プロバイダの開始・終了時だけなので、
        # 毎回ログしても1セッションで数行にしかならない。
        ctx.log("SetWindowLong_WndProc_wrapper called: hWnd={!r} ({}) wndProc={!r} ({})".format(
            hWnd, type(hWnd).__name__, wndProc, type(wndProc).__name__))
        try:
            result = orig(hWnd, wndProc)
        except Exception as exc:
            # 失敗したときは引数の値と型を残す。これが原因特定の材料になる。
            ctx.log(
                "SetWindowLong_WndProc_wrapper failed: {}: {} | "
                "hWnd={!r} ({}) wndProc={!r} ({})".format(
                    type(exc).__name__, exc,
                    hWnd, type(hWnd).__name__,
                    wndProc, type(wndProc).__name__),
                level="WARN")
        else:
            ctx.log("  original call succeeded -> {!r}".format(result))
            return result

        # --- ここから下は、元の呼び出しが失敗したときだけ通る ---
        address = _as_address(wndProc)
        if address is None:
            ctx.log("  no usable WndProc address; skipping restore "
                    "(window is being destroyed anyway)", level="WARN")
            return None

        try:
            # 自前で用意したプロトタイプで直接呼ぶ。
            # ctypes の型チェックを経由しないので「引数3の型が違う」で弾かれない。
            previous = _raw_set(hWnd, gwl_wndproc, address)
        except Exception:
            ctx.log_exc("  raw SetWindowLongPtrW fallback failed too; swallowing")
            return None

        ctx.log("  raw fallback restored WndProc (previous=0x{:X})".format(previous & (2 ** 64 - 1)))
        return previous

    # エイリアス走査がどこまで届いたかを確認しておく。
    # ここが False なら wm_pen / wm_touch は元の関数を呼び続けていて、
    # このパッチは効いていない。黙って外れているのが一番困るので明示的に出す。
    for name in ("wm_pen", "wm_touch"):
        module = sys.modules.get("kivy.input.providers." + name)
        if module is None:
            continue
        bound = getattr(module, "SetWindowLong_WndProc_wrapper", None)
        # __wrapper_of__ は patch.py がラッパに付ける目印。
        rebound = getattr(bound, "__wrapper_of__", None) is not None
        ctx.log("  {}: SetWindowLong_WndProc_wrapper rebound={}".format(name, rebound))
