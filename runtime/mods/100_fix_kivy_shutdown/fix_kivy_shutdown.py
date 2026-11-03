# -*- coding: utf-8 -*-
"""ゲーム終了時に Kivy が落ちる問題を直す。

出ていたエラー（VERIFICATION_LOG.md §2.1）:

    File "...\\kivy\\input\\providers\\wm_pen.py", line 119, in stop
    File "...\\kivy\\input\\providers\\wm_common.py", line 115, in _closure
    ctypes.ArgumentError: argument 3: TypeError: wrong type

Kivy は終了処理の中で、
乗っ取っていたウィンドウプロシージャ（WndProc）を元に戻そうとする。
そのとき保存しておいたハンドラを ctypes が受け付けずに落ちている。

これを他より先に直す理由は2つ。

1つ目。
これは stopTouchApp() から呼ばれる ＝「本当に落ちた原因」の後始末をしている最中に発生するので、
本来知りたかった原因がこのエラーで上書きされてしまう（該当箇所は例外なく "During
handling of the above exception, another exception occurred" で始まる）。
先にこれを潰さないと、他のバグの調査が進まない。

2つ目。
この処理は実質的に無意味である。
直後に破棄されるウィンドウの WndProc を戻しているだけなので、
失敗しても目に見える影響はない。
つまり握り潰しても安全。

パッチの中身は、元の処理をそのまま呼び、失敗したら引数の型をログに出して諦める。

版3: 失敗したときにハンドラを整数のアドレスに直して `SetWindowLongPtrW` で呼び直す仕掛けを外した。
10-16〜11-01 の失敗72回は全部 `hWnd=None`（ウィンドウが先に消えている）で、
戻す相手が無く、呼び直しは一度も走らなかった。
呼ばれるたびに書いていた2行もやめ、失敗の行だけにした。
毎回書いていたのは「ガードが効いた」と「今回は踏まなかった」を区別するためで（§2.1）、
その区別は失敗の行の有無で付く。

なお wm_pen と wm_touch はどちらも
`from ... import SetWindowLong_WndProc_wrapper` で関数を取り込んでいる。
そのため wm_common だけを書き換えても、この2つは元の関数を持ったままになる。
実際に効かせているのは ctx.wrap のエイリアス走査（patch.py 参照）で、
sys.modules を掃いて同じ関数を指している変数を全部張り替えている。
Nuitka 環境でパッチが「効かない」ときは、まずここを疑うとよい。
"""


def apply(ctx):
    import sys

    from instantale_modloader import patch

    # 無ければローダの保留に積んで降りる（`patch.await_module`）。来たらローダが当て直す。
    # `sys.modules` を見て自分で降りると保留に載らず、ほかに保留が無い構成ではその起動で一度も当たらない。
    wm_common = patch.await_module("kivy.input.providers.wm_common")
    if wm_common is None:
        ctx.log("kivy.input.providers.wm_common not loaded yet; waiting for it")
        return

    @ctx.wrap("kivy.input.providers.wm_common:SetWindowLong_WndProc_wrapper")
    def guarded(orig, hWnd, wndProc):
        try:
            return orig(hWnd, wndProc)
        except Exception as exc:
            # 直後に破棄されるウィンドウの WndProc を戻しているだけなので、諦めても影響は無い。
            # 引数の値と型は残す（記録の失敗はどれも hWnd=None だった。版3の註）。
            ctx.log(
                "SetWindowLong_WndProc_wrapper failed: {}: {} | "
                "hWnd={!r} ({}) wndProc={!r} ({}) -- swallowed (window is being "
                "destroyed anyway)".format(
                    type(exc).__name__, exc,
                    hWnd, type(hWnd).__name__,
                    wndProc, type(wndProc).__name__),
                level="WARN")
            return None

    # エイリアス走査がどこまで届いたかを確認しておく。
    # ここが False なら wm_pen / wm_touch は元の関数を呼び続けていて、
    # このパッチは効いていない。
    # 黙って外れているのが一番困るので明示的に出す。
    for name in ("wm_pen", "wm_touch"):
        module = sys.modules.get("kivy.input.providers." + name)
        if module is None:
            continue
        bound = getattr(module, "SetWindowLong_WndProc_wrapper", None)
        # __wrapper_of__ は patch.py がラッパに付ける目印。
        rebound = getattr(bound, "__wrapper_of__", None) is not None
        ctx.log("  {}: SetWindowLong_WndProc_wrapper rebound={}".format(name, rebound))
