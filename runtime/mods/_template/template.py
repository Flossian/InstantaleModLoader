# -*- coding: utf-8 -*-
"""新しい MOD を作るときの雛形。ここを書き換えて使う。

**このフォルダは読み込まれない。** 先頭が `_` のフォルダはローダが探索から
外すので（`discover()`）、雛形を置いたままにしても GUI の一覧に出ないし
適用もされない。使うときは**フォルダごとコピーして名前を付ける**:

    runtime/mods/_template/  →  runtime/mods/900_my_mod/

コピーしたら直すのは4箇所。

    1. mod.json の "name" / "description" / "author"
    2. mod.json の "entry"（入口のファイル名を変えたなら）
    3. 下の `@ctx.wrap(...)` の対象名（`out/recon/targets.txt` から取る）
    4. 要らなくなった "settings" と `LOG_LIMIT` の削除

そのあと `python tools/check_mods.py` を通してから注入する。手順は
docs/TECH.md §3.0 に一直線で書いてある。

この雛形が今やっていること: 画面にテキストが足されるたびに呼ばれる
`InstantaleApp.add_text` を包んで、最初の数回だけ長さをログに出す。
ゲームの動作は何も変えない ― **フックが本当に効いているか**を確かめるための
最小の形として選んである。

書き足すときの決まりは docs/TECH.md §6（落とし穴の一覧）を先に読むこと。
特に効いてくるのは次の2つ:

  * `apply()` は1プロセスの中で**何度も呼ばれる**（再注入と遅延当て直し）。
    何度実行しても結果が変わらないように書く（§3.4 / §3.5）
  * 副作用のある初期化は `ctx.on_ready()` に預ける（§3.6）
"""

# 利用者が GUI から変えられる値は、**モジュール直下の定数**として置き、
# 同じ名前と同じ既定値を mod.json の "settings" にも書く。ローダが apply() を
# 呼ぶ直前にここへ書き込むので、コードは定数をそのまま読めばよい（§3.8）。
# 2箇所に書くことになるので、ずれていたら check_mods.py が MISMATCH を出す。
LOG_LIMIT = 5


def apply(ctx):
    """ローダがこれを呼ぶ。引数の `ctx` でできることは §3.1.3 の表にある。"""

    # フックをまたいで持ち回す値は辞書に入れる。`nonlocal` でも書けるが、
    # 辞書なら入れ子のどこからでも読み書きできる（既存 MOD もこの形）。
    state = {"seen": 0}

    # 対象名は "モジュール名:属性名" の形。クラスのメソッドなら
    # "モジュール名:クラス名.メソッド名"。正確な名前は推測せず
    # `out/recon/targets.txt` から取ること（§3.0 / GAME.md §1.1）。
    #
    # safe=True は、このフックが例外を投げてもゲームへ流さず元の動作に
    # 落とす（§3.1.5）。失敗は ERROR としてログに残るので、
    # `safe hook failed` を見たら直す ― 握り潰しの代わりに使ってはいけない。
    # 引数の名前と並びは targets.txt が教えてくれる:
    #     __main__:InstantaleApp.add_text(self, context)
    # 残りを `*args, **kwargs` で受けておくのは、ゲーム更新で引数が増えても
    # そのまま素通しできるようにするため。既定値を付けておくのも同じ理由。
    @ctx.wrap("__main__:InstantaleApp.add_text", safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        # `@ctx.wrap` が飾る関数は、第1引数が元の関数で、
        # メソッドを包むなら第2引数が self。この並びは check_mods.py が見ている。
        result = orig(self, context, *args, **kwargs)

        # 元の関数を先に呼んでから自分の処理をする。こうしておくと、
        # ここで壊れても safe=True が orig の結果をそのまま返せる（§3.1.5）。
        if state["seen"] < LOG_LIMIT:
            state["seen"] += 1
            ctx.log("template: add_text #{} len={}".format(
                state["seen"], len(context) if isinstance(context, str) else "?"))

        return result

    # apply() の中で出したログは out/modloader.log に入る。
    # 量が多くなるなら MOD 専用のファイルに分ける（`ctx.out_path("template.log")`）。
    ctx.log("template: installed (LOG_LIMIT={})".format(LOG_LIMIT))
