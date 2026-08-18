# -*- coding: utf-8 -*-
"""NPC の雇用価格を引くときに出る KeyError: 80 を直す。

## 何が起きているか（VERIFICATION_LOG.md §2.2）

    get_npc_employ_price(level)     価格表。0〜76 のみ有効で、77 以上は KeyError
    clamp_npc_difficulty_value(v)   ゲーム自身のクランプ。[0, 76] に収める
    scripts.functions               NPC_DIFFICULTY_VALUE_MIN / _MAX を定義している

価格表がカバーする範囲は、
ゲーム自身が「正当な難易度」として宣言している範囲と一致している。
落ちるのは、この呼び出し箇所だけがクランプを通さずに値をそのまま渡しているから。

## 直し方: 価格を作らない

**ゲーム自身のクランプ関数を通すだけ**にする。
上限を超えた NPC は最上位の 76 と同じ価格（5045）になる。
価格曲線を外挿して
77 以上の値を作ればゲームバランスをこちらで勝手に決めることになる。
クランプ関数はまさにこのために用意されているはずなので、それに従う。

難易度 80 の NPC がどこで生まれているのかには触れていない。
クランプが効いた回数はログに残るので、上限超えの発生頻度はそこから読める。
"""

from instantale_modloader import patch



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
    # 底まで剥がす。
    # 1段だけでは、`200_` など他の MOD が同じ関数を包んでいるときに向こうのラッパを掴む（同じ対象への重ねがけは正常な使い方。
    # TECH.md §3.7）。
    # 剥がし方はローダの語彙（`patch.unwrap`）。
    clamp = patch.original_of(clamp)

    # ゲームが宣言している難易度の上下限を記録しておく。
    # 上の調査結果（0〜76）と食い違っていたら、この修正の前提が崩れている。
    low = getattr(functions, "NPC_DIFFICULTY_VALUE_MIN", None)
    high = getattr(functions, "NPC_DIFFICULTY_VALUE_MAX", None)
    ctx.log("NPC_DIFFICULTY_VALUE_MIN={!r} MAX={!r}".format(low, high))

    # 下の自己テストで 0〜200 を総当たりするので、
    # そのままだと注入のたびに上限超えの分だけクランプのログが出てしまう。
    # テスト中は黙らせるためのフラグ。
    state = {"verifying": False}

    # 包む前の素の関数を控えておく。
    # これが「本体がまだ壊れているか」を測る唯一の手がかりになる。
    # 包んだ後の関数を総当たりしても、確かめられるのは「この mod が効いていること」だけで、
    # mod がもう要らないかは分からない。
    # このバグは能動的に起こせない（難易度 77 以上の
    # NPC が生まれるのを待つしかない）。
    # だが `get_npc_employ_price` は副作用の無い参照関数なので、
    # 稼働中のプロセスで直接呼んで有効域を割り出せる（GAME.md §3。
    # 0..76 もこの手で判明した）。
    # 下の自己テストがどのみち同じ範囲を呼ぶので、呼び出しが増えるわけでもない。
    # ここで module の属性をそのまま掴んではいけない。
    # 再注入のとき、その属性はまだ「前回の注入で付けたラッパ」のままで（層を剥がすのは ctx.wrap の中）、掴むと
    # clamp 済みの関数を測ることになる。
    # 当然どの入力でも落ちないので、
    # 本体が直っていなくても「もう要らない」と出てしまう。
    # 上の `clamp` と同じく `__original__` をたどって最下層まで戻す（`200_probe_bug_sites` もここを包むので、層は
    # 1 枚とは限らない）。
    raw, raw_depth, raw_is_ours = patch.unwrap(functions.get_npc_employ_price)

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
                # 76 以上は全部同じ価格になるはず。
                # 最後の値を控えてログに出す。
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

    # --------------------------------------------------------------- 上流の状態
    # 素の関数がまだ落ちるかを測る。
    # ゲームが本体側で直した版では落ちなくなるので、
    # そうなったらこの mod は要らない。
    # 落ちる限りは要る。
    # 判定に使わず記録するだけにしておく。
    # 「落ちなくなった」の一度の観測で自動的に降りると、
    # 測り方の側が間違っていたときに黙って穴が開く。
    upstream_first_raise, upstream_error = None, None
    try:
        for level in range(0, 201):
            try:
                raw(level)
            except Exception as exc:
                upstream_first_raise = (level, "{}: {}".format(type(exc).__name__, exc))
                break
    except Exception as exc:                      # raw の呼び出し自体が壊れている
        upstream_error = "{}: {}".format(type(exc).__name__, exc)

    if raw_is_ours:
        # ここに来たら測定そのものが無効。
        # 剥がし切れていないラッパを測っている。
        ctx.log("upstream probe measured a patched function (unwrapped={}); "
                "result discarded, keeping the fix".format(raw_depth), level="WARN")
    elif upstream_error is not None:
        ctx.log("upstream probe failed ({}); keeping the fix".format(upstream_error),
                level="WARN")
    elif upstream_first_raise is None:
        # ここに来たら、本体が直したか、こちらの測り方が変わったかのどちらか。
        # unwrapped= を必ず添える。
        # 0 なら素の関数を測っている。
        # 1 以上なら層を剥がした後なので、剥がし損ねていないかを疑う手がかりになる。
        ctx.log("upstream: get_npc_employ_price(0..200) no longer raises "
                "(unwrapped={}) -- this mod may be redundant on this build "
                "(verify before removing)".format(raw_depth))
    else:
        ctx.log("upstream: get_npc_employ_price still raises from level {} ({}) "
                "(unwrapped={}) -- this mod is still needed".format(
                    upstream_first_raise[0], upstream_first_raise[1], raw_depth))
