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

# 上書きする前の `out/recon/` を zip で残すか。
#
# 残しておかないと**ゲームが更新された瞬間に前の版のダンプが消える**ので、
# 「何が増えて何が消えたか」を機械的に出せなくなる（GAME.md §1.5 に、退避が
# あった main_023 → main_024 では 68 ターゲット増を出せて、退避が無かった
# main_024 → main_025 では出せなかった、という記録がそのまま残っている）。
#
# 退避先は `out/recon_snapshots/<版>_YYYYMMDD.zip` の形で、走るのは
# **ビルドが変わったときだけ**（同じ版を何度走らせても増えない）。見分け方と
# 「中身の差を引き金にしない」理由は `instantale_modloader/recon.py` の「退避」の節にある。
BACKUP_PREVIOUS = True


def apply(ctx):
    # ローダ本体のモジュールは、mod ファイルの先頭ではなく apply() の中で import する。
    # 再注入のたびにローダは sys.modules から消して読み直されるので、
    # ファイル先頭で掴んでも古い方を掴むことがあるため。
    from instantale_modloader import recon

    # 実行環境（Python のバージョン、実行ファイル、ロード済みモジュール数）を先に記録。
    # 「注入はできたのに何も見つからない」ときの切り分けに使う。
    ctx.log("environment:\n" + ctx.describe())

    # sys.modules を全部なめて、out/recon/ に成果物一式を書き出す。
    recon_dir = recon.dump(ctx.out_dir, backup=BACKUP_PREVIOUS)
    ctx.log("recon written to {}".format(recon_dir))
