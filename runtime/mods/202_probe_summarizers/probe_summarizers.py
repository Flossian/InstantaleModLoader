# -*- coding: utf-8 -*-
"""空 Literal バグのため、サマライザ/ファシリテータ一族を丸ごと計測する。

トレースバックは master_ai_process_summarizer_in_conversation を名指ししている
が、会話を一通り行ってもこの関数は呼ばれない。実行は兄弟の別変種を通っている。
llm_manager はサマライザ5種とファシリテータ4種を公開して
おり、いずれも生成される pydantic モデルの `Literal[...]` に流し込まれ得る
リスト引数（npc_list, facility_list, ...）を取る。

`AssertionError: literal "expected" cannot be empty, obj=typing.Literal[]` は、
モデル構築時にそれらのリストのどれかが空だったことを意味する。どの入口が該当
するかを当てるより、全部包んでシーケンス引数の長さを片端から記録する方が早い。
空であれば呼び出しが成功していても印を付けるので、次にクラッシュする前に犯人が
浮かび上がる。
"""

import sys

from instantale_modloader.frames import format_locals, repr_value

MODULE = "scripts.llm.llm_manager"

TARGETS = (
    "master_ai_process_summarizer",
    "master_ai_process_summarizer_in_conversation",
    "master_ai_process_summarizer_in_conversation_in_quest",
    "master_ai_process_summarizer_in_quest",
    "master_ai_process_summarizer_with_no_recipients",
    "master_ai_facilitator",
    "master_ai_facilitator_from_conversation",
    "master_ai_facilitator_in_quest",
    "master_ai_faciltiator_from_conversation_in_quest",   # ゲーム側の綴りママ（typo）
)


def apply(ctx):
    # **未 import でも降りない。** `scripts.llm.llm_manager` は最初の LLM
    # リクエストまで import されない（TECH.md §3.4）。ここで早期 return すると
    # フックが1本も登録されず、**保留の見張りが立たない** ― 動くかどうかが
    # 「他の MOD が同じモジュールを保留してくれるか」に依存する。`required=False`
    # で先に登録しておけば、現れた時点でローダが当て直す（`209_` の形）。

    write = ctx.logger("probes.log")

    # 未 import なら None のまま進む（上の理由で降りない）。None のときは
    # 存在確認ができないだけなので、全対象を required=False で登録しておく。
    module = sys.modules.get(MODULE)

    def describe_args(name, args, kwargs):
        """シーケンス系の引数だけを拾い、型と長さを記録する。

        中身は出さない（プロンプト全文がログに溢れる）。空かどうかだけが争点。
        """
        parts = []
        empties = []
        for index, value in enumerate(args):
            if isinstance(value, (list, tuple, set, dict)):
                try:
                    size = len(value)
                except Exception:
                    continue
                parts.append("arg{}={}(len={})".format(index, type(value).__name__, size))
                if size == 0:
                    empties.append("arg{}".format(index))
        for key, value in kwargs.items():
            if isinstance(value, (list, tuple, set, dict)):
                try:
                    size = len(value)
                except Exception:
                    continue
                parts.append("{}={}(len={})".format(key, type(value).__name__, size))
                if size == 0:
                    empties.append(key)
        # 空であること自体は証拠に *ならない*: master_ai_facilitator の arg9
        # (master_process_log) は成功した呼び出しでも毎回空で来ていた。ここでは
        # 文脈として記録するに留める ― 空 Literal を実際に同定するのは、モデルが
        # 組み立てられる瞬間を押さえる 203_probe_create_model の方である。
        write("{}({}){}".format(
            name, ", ".join(parts) if parts else "no sequence args",
            "  [empty: {}]".format(", ".join(empties)) if empties else ""))

    installed = 0
    for name in TARGETS:
        # 存在確認は既定値付き getattr で行う（TECH.md §6 の hasattr 禁止）。
        if module is not None and getattr(module, name, None) is None:
            ctx.log("  {} not present; skipped".format(name), level="WARN")
            continue

        # ループ変数を閉じ込める工場関数。これが無いと全ラッパが最後の name を見る。
        def make_probe(fn_name):
            @ctx.wrap("{}:{}".format(MODULE, fn_name), required=False)
            def probe(orig, *args, **kwargs):
                # 計測の失敗で本体の呼び出しを妨げない。
                try:
                    describe_args(fn_name, args, kwargs)
                except Exception:
                    pass
                try:
                    return orig(*args, **kwargs)
                except Exception as exc:
                    # 観測に徹するので、記録したうえで必ず再送出する。
                    write("!! {} raised {}: {}".format(fn_name, type(exc).__name__, exc))
                    write(format_locals(exc.__traceback__, depth=3))
                    raise
            return probe

        make_probe(name)
        installed += 1

    ctx.log("summarizer probes installed on {}/{} target(s)".format(installed, len(TARGETS)))
