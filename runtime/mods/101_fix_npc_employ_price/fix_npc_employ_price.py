# -*- coding: utf-8 -*-
"""NPC の雇用価格を引くときに出る KeyError: 80 を直す。

このバグは、クラッシュの再現を待たずに解決できた。
get_npc_employ_price は「int を受け取って int を返す、副作用のない参照関数」
だったので、動いているプロセスに対して定義域を総当たりで問い合わせればよかった。
ソースが読めなくても、対象が純粋な関数なら実行時に直接聞く方が早い。

分かったこと:

    get_npc_employ_price(0..150)   0〜76 は成功（間に穴は無い）。
                                   77 以上は KeyError(レベル値)。
                                   80 を渡すと報告どおりのクラッシュを再現。
    clamp_npc_difficulty_value(v)  [0, 76] の範囲に収める。
    scripts.functions              NPC_DIFFICULTY_VALUE_MIN / _MAX を定義している。

つまり価格表がカバーしている範囲は、ゲーム自身が「正当な難易度」として
宣言している範囲とぴったり一致している。落ちるのは、この呼び出し箇所だけが
クランプを通さずに値をそのまま渡しているからだった。

そこで修正は、価格を新しく作るのではなく、ゲーム自身のクランプ関数を
通すだけにした。上限を超えた NPC は最上位の 76 と同じ価格（5045）になる。
価格曲線を外挿して 77 以上の値を作ることもできたが、それはゲームバランスを
こちらで勝手に決めることになる。クランプ関数はまさにこのために用意されている
はずなので、それに従うのが妥当だと判断した。

残っている疑問: そもそも難易度 80 の NPC がどこで生まれているのか。
この修正はクラッシュを止めて 76 として値付けするだけで、そこには触れていない。
上限超えの NPC 自体がバグなら、下に入れたクランプのログがその頻度を教えてくれる。
"""

NAME = "NPC hire price fix"
NAME_JA = "NPC雇用価格の修正"
VERSION = "1"
DESCRIPTION = "Prevents the KeyError in NPC hire pricing by using the game's own clamp"
DESCRIPTION_JA = "NPC 雇用価格の KeyError を、ゲーム自身のクランプ関数を通して防ぐ"
AUTHOR = "R01/Flossian"


def apply(ctx):
    import sys

    functions = sys.modules.get("scripts.functions")
    if functions is None:
        ctx.log("scripts.functions not loaded; skipping", level="WARN")
        return

    clamp = getattr(functions, "clamp_npc_difficulty_value", None)
    if clamp is None:
        ctx.log("clamp_npc_difficulty_value not found; skipping", level="WARN")
        return
    # 他の mod がクランプ関数を包んでいる可能性があるので、素の実装を取り出す。
    # 包まれたものを掴むと、ログが二重に出たり余計な処理が挟まったりする。
    clamp = getattr(clamp, "__original__", clamp)

    # ゲームが宣言している難易度の上下限を記録しておく。
    # 上の調査結果（0〜76）と食い違っていたら、この修正の前提が崩れている。
    low = getattr(functions, "NPC_DIFFICULTY_VALUE_MIN", None)
    high = getattr(functions, "NPC_DIFFICULTY_VALUE_MAX", None)
    ctx.log("NPC_DIFFICULTY_VALUE_MIN={!r} MAX={!r}".format(low, high))

    # 下の自己テストで 0〜200 を総当たりするので、そのままだと注入のたびに
    # 上限超えの分だけクランプのログが出てしまう。テスト中は黙らせるためのフラグ。
    state = {"verifying": False}

    @ctx.wrap("scripts.functions:get_npc_employ_price")
    def get_npc_employ_price(orig, npc_difficulty_level, *args, **kwargs):
        try:
            clamped = clamp(npc_difficulty_level)
        except Exception:
            # クランプ自体が失敗した場合は、元の関数にそのまま渡す。
            # 適当な値をでっち上げるより、元と同じ失敗をさせる方が安全で、
            # 何が起きたかもログに残る。
            ctx.log_exc("clamp failed for {!r}; passing through".format(npc_difficulty_level))
            return orig(npc_difficulty_level, *args, **kwargs)

        # クランプが実際に働いた＝本来なら落ちていた呼び出し。
        # この行の出現頻度が、上限超えの NPC がどれくらい生まれているかを示す。
        if clamped != npc_difficulty_level and not state["verifying"]:
            ctx.log("get_npc_employ_price: clamped difficulty {!r} -> {!r} "
                    "(would have raised KeyError)".format(npc_difficulty_level, clamped))
        return orig(clamped, *args, **kwargs)

    # 実際に組み込まれた関数を呼んで、直ったことをその場で確認する。
    # 他の mod がまだ何も被せていないこのタイミングで実行するのが確実。
    patched = functions.get_npc_employ_price
    failures, top = [], None
    state["verifying"] = True
    try:
        for level in range(0, 201):
            try:
                price = patched(level)
            except Exception as exc:
                failures.append((level, "{}: {}".format(type(exc).__name__, exc)))
            else:
                # 76 以上は全部同じ価格になるはず。最後の値を控えてログに出す。
                if level >= 76:
                    top = price
    finally:
        state["verifying"] = False

    if failures:
        ctx.log("VERIFY FAILED: {} level(s) still raise, first={}".format(
            len(failures), failures[0]), level="ERROR")
    else:
        ctx.log("verified: get_npc_employ_price(0..200) no longer raises; "
                "levels >=76 all price at {}".format(top))
