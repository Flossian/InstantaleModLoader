# -*- coding: utf-8 -*-
"""ゲームの中身を調べて、パッチ対象の一覧を out/recon/ に書き出す。

このゲームは Nuitka でビルドされているのでソースが読めない。
「どんな関数が、どんな名前で、どんな引数を取って存在するのか」を知る方法は、
動いているプロセスに直接聞くことだけになる。それをやるのがこの mod で、
出力された out/recon/targets.txt に載っている名前を、他の mod がそのまま
@ctx.wrap(...) に貼って使う。

ファイル名を 00 で始めているのは、mod がファイル名順に適用されるから。
他の mod がゲームを書き換える前の、素の状態を記録しておきたい。

このファイルはゲームを一切変更しない。書き出すだけ。
"""


def apply(ctx):
    # ローダ本体のモジュールは、mod ファイルの先頭ではなく apply() の中で import する。
    # 再注入のたびにローダは sys.modules から消して読み直されるので、
    # ファイル先頭で掴んでも古い方を掴むことがあるため。
    from instantale_modloader import recon

    # 実行環境（Python のバージョン、実行ファイル、ロード済みモジュール数）を先に記録。
    # 「注入はできたのに何も見つからない」ときの切り分けに使う。
    ctx.log("environment:\n" + ctx.describe())

    # sys.modules を全部なめて、out/recon/ に成果物一式を書き出す。
    recon_dir = recon.dump(ctx.out_dir)
    ctx.log("recon written to {}".format(recon_dir))
