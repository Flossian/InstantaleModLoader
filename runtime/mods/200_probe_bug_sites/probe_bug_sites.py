# -*- coding: utf-8 -*-
"""未解決の4つのクラッシュ地点を、失敗時だけでなく毎回計測する。

**再現を待つのは筋が悪い。** これらは数回しか発火しない稀なクラッシュで、
何セッション遊んでも起きないことがある。そこで各地点をラップし、*成功した*
呼び出しの時点で既にデータの形が分かるようにする。4つのうち2つは、二度と
クラッシュを見ないまま決着できる見込みが高い:

  get_npc_employ_price(npc_difficulty_level) -> KeyError: 80
      引数は難易度レベルで、失敗したキーは int。つまりティア表にエントリが
      欠けているだけである。毎回の引数と表の実キーを記録すれば定義域が即座に
      分かる。

  master_ai_process_summarizer_in_conversation(..., npc_list, ...)
      -> AssertionError: literal "expected" cannot be empty, typing.Literal[]
      会話中の NPC で Literal を作って pydantic モデルを組み立てている。
      npc_list が空ならちょうど `Literal[]` になる。呼び出しごとに
      len(npc_list) を記録すれば1セッションで確認も否定もできる。

残り2つは失敗した呼び出しそのものが要る: `generate_npc_detail`（KeyError '52'、
*文字列* キー。`get_npc_employ_price` の int と対照的）と
`FreeInputStart.method`（facility_move_to への AttributeError）。どちらも例外時に
状態を全部ダンプする。

何も握り潰さない。例外はログしてから再送出する。この mod は観測するだけ。
"""

import datetime
import sys
import traceback

from instantale_modloader.frames import describe_instance, format_locals, repr_value

LOG_BASENAME = "probes.log"
MAX_TABLE_DICTS = 12


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    def write(text: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("probe: write failed")

    def on_error(label: str, exc: BaseException) -> None:
        """例外を、後から読める形（型・ローカル変数・トレースバック）で残す。"""
        write("!! {} raised {}: {}".format(label, type(exc).__name__, exc))
        write(format_locals(sys.exc_info()[2] or exc.__traceback__, depth=3))
        write("   traceback:\n" + "".join(traceback.format_tb(exc.__traceback__)).rstrip())

    def dump_module_dicts(module_name: str, note: str) -> None:
        """モジュール直下の dict をログする。参照表はここにあり、f_locals には無い。"""
        module = sys.modules.get(module_name)
        if module is None:
            return
        write("   {} module-level dicts in {}:".format(note, module_name))
        shown = 0
        try:
            items = sorted(vars(module).items())
        except Exception:
            return
        for name, value in items:
            if not isinstance(value, dict) or not value or name.startswith("__"):
                continue
            shown += 1
            # 表が多いモジュールでログを埋め尽くさないよう上限を設ける。
            if shown > MAX_TABLE_DICTS:
                write("     ... more dicts omitted")
                break
            write("     {:<28} {}".format(name, repr_value(value)))

    write("=" * 78)
    write("probe session start (pid {})".format(__import__("os").getpid()))

    # ---------------------------------------------------------------------
    # 一発計測: get_npc_employ_price の有効な定義域を割り出す。
    #
    # この関数は int の難易度レベルを取って int の価格を返し、self を取らず、
    # 観測できる副作用も無い（実際に 45 を渡したら 1974 が返った）。範囲を
    # 総当たりで走査すれば「KeyError: 80」は推測ではなく、表が実際にカバーして
    # いるレベルの正確な集合に変わる ― 再現を待つ必要が無い。
    # ---------------------------------------------------------------------
    def sweep_employ_price():
        functions = sys.modules.get("scripts.functions")
        target = getattr(functions, "get_npc_employ_price", None) if functions else None
        if target is None:
            write("sweep: scripts.functions.get_npc_employ_price unavailable")
            return
        # 101_ が既にクランプを被せているので、素の関数を取り出して測る。
        # そうしないと「クランプ後の挙動」を測ってしまい定義域が見えない。
        original = getattr(target, "__original__", target)

        ok, failed = {}, {}
        for level in range(0, 151):
            try:
                ok[level] = original(level)
            except Exception as exc:
                failed[level] = "{}: {}".format(type(exc).__name__, exc)

        write("-" * 78)
        write("sweep get_npc_employ_price(0..150): {} ok, {} failing".format(
            len(ok), len(failed)))
        if ok:
            levels = sorted(ok)
            # 連続かどうかが重要 ― 飛び飛びなら「表の穴」、連続なら「上限」。
            write("  valid levels   : {}..{} (contiguous={})".format(
                levels[0], levels[-1], levels == list(range(levels[0], levels[-1] + 1))))
            sample = {lv: ok[lv] for lv in levels[:8] + levels[-8:]}
            write("  sample prices  : {}".format(sample))
        if failed:
            bad = sorted(failed)
            write("  failing levels : {}{}".format(bad[:40], " ..." if len(bad) > 40 else ""))
            write("  first failure  : {} -> {}".format(bad[0], failed[bad[0]]))
            if 80 in failed:
                write("  >>> level 80 reproduces the reported KeyError: {}".format(failed[80]))
        write("-" * 78)

    def sweep_clamp():
        """ゲーム自身のクランプは、どこまでを正当な難易度とみなしているか?

        scripts.functions は clamp_npc_difficulty_value() を公開している。その
        上限が価格表の最終ティア（76）と一致するなら、価格参照は他のコードが
        既に適用しているクランプを単に通していないだけ、ということになる。
        つまり修正はゲーム自身のルールであって、こちらの発明ではなくなる。
        """
        functions = sys.modules.get("scripts.functions")
        if functions is None:
            return
        for fname in ("clamp_npc_difficulty_value", "clamp_equipment_value"):
            fn = getattr(functions, fname, None)
            if fn is None:
                write("clamp probe: {} not found".format(fname))
                continue
            fn = getattr(fn, "__original__", fn)
            results = {}
            # 境界（75/76/77）を挟むように値を選び、上限がどこかを一発で見る。
            for value in (-10, -1, 0, 1, 40, 70, 75, 76, 77, 80, 100, 150, 999):
                try:
                    results[value] = fn(value)
                except Exception as exc:
                    results[value] = "{}: {}".format(type(exc).__name__, exc)
            write("{}: {}".format(fname, results))

    def dump_module_constants(module_name: str):
        """モジュール直下のスカラー定数: MAX_* の類が本当の上限を名指しする。"""
        module = sys.modules.get(module_name)
        if module is None:
            return
        try:
            items = sorted(vars(module).items())
        except Exception:
            return
        scalars = {n: v for n, v in items
                   if isinstance(v, (int, float, str, bool)) and not n.startswith("__")}
        if scalars:
            write("   constants in {}: {}".format(module_name, repr_value(scalars)))

    # 一発計測は失敗しても他の計測を止めないよう、個別に try で囲んで回す。
    for probe_fn, label in ((sweep_employ_price, "employ price sweep"),
                            (sweep_clamp, "clamp sweep")):
        try:
            probe_fn()
        except Exception:
            ctx.log_exc("probe: {} failed".format(label))

    for mod_name in ("scripts.functions", "scripts.data_tables"):
        try:
            dump_module_constants(mod_name)
        except Exception:
            ctx.log_exc("probe: constants dump failed for {}".format(mod_name))

    # 一発計測: 参照表はモジュールのグローバルにあり、どのフレームのローカルにも無い。
    dump_module_dicts("scripts.functions", "startup snapshot:")
    dump_module_dicts("scripts.data_tables", "startup snapshot:")

    # ---------------------------------------------------------------- KeyError: 80
    @ctx.wrap("scripts.functions:get_npc_employ_price", required=False)
    def get_npc_employ_price(orig, npc_difficulty_level, *args, **kwargs):
        try:
            result = orig(npc_difficulty_level, *args, **kwargs)
        except Exception as exc:
            # 失敗時は引数の値と *型* を残す。'52'(str) と 80(int) の区別が争点。
            write("get_npc_employ_price(npc_difficulty_level={!r} [{}]) FAILED".format(
                npc_difficulty_level, type(npc_difficulty_level).__name__))
            on_error("get_npc_employ_price", exc)
            dump_module_dicts("scripts.functions", "candidate tables:")
            dump_module_dicts("scripts.data_tables", "candidate tables:")
            raise
        write("get_npc_employ_price(npc_difficulty_level={!r} [{}]) -> {!r}".format(
            npc_difficulty_level, type(npc_difficulty_level).__name__, result))
        return result

    # ------------------------------------------------- AssertionError: Literal[]
    @ctx.wrap("scripts.llm.llm_manager:master_ai_process_summarizer_in_conversation",
              required=False)
    def summarizer(orig, player, player_life_log, worldview, npc_list, *args, **kwargs):
        write("master_ai_process_summarizer_in_conversation: npc_list={}".format(
            repr_value(npc_list)))
        # 空なら成功していても警告する ― これが Literal[] を生む条件だから。
        if not npc_list:
            write("   ^^ npc_list is EMPTY -- this is the Literal[] condition")
        try:
            return orig(player, player_life_log, worldview, npc_list, *args, **kwargs)
        except Exception as exc:
            on_error("master_ai_process_summarizer_in_conversation", exc)
            raise

    # ------------------------------------------------------------- KeyError: '52'
    @ctx.wrap("__main__:InstantaleApp.generate_npc_detail", required=False)
    def generate_npc_detail(orig, self, character_instance, *args, **kwargs):
        # どの NPC で落ちたかを特定できるよう、識別子らしき属性を要約して残す。
        write("generate_npc_detail({})".format(describe_instance(character_instance)))
        try:
            return orig(self, character_instance, *args, **kwargs)
        except Exception as exc:
            on_error("generate_npc_detail", exc)
            raise

    @ctx.wrap("__main__:ConversationStartManager.generate_npc_detail_and_ready",
              required=False)
    def generate_npc_detail_and_ready(orig, self, *args, **kwargs):
        # 呼び出し元側。トレースバックの上段を押さえるためだけに包む。
        try:
            return orig(self, *args, **kwargs)
        except Exception as exc:
            on_error("generate_npc_detail_and_ready", exc)
            raise

    # ------------------------------- AttributeError: FreeInputStart.facility_move_to
    @ctx.wrap("__main__:FreeInputStart.method", required=False)
    def free_input_method(orig, self, choice_text, *args, **kwargs):
        # ここで hasattr() を使わないこと: 201_probe_missing_attr がこのクラスに
        # __getattr__ トリップワイヤを仕掛けており、hasattr は呼び出しのたびに
        # それを自己発火させてしまう。
        write("FreeInputStart.method(choice_text={})".format(repr_value(choice_text)))
        try:
            return orig(self, choice_text, *args, **kwargs)
        except Exception as exc:
            # 失敗時はインスタンスが実際に持っている属性を列挙する ―
            # 「何が無いのか」ではなく「何があるのか」が手がかりになる。
            try:
                attrs = sorted(vars(self))
            except Exception:
                attrs = ["<vars() failed>"]
            write("   FreeInputStart instance attrs: {!r}".format(attrs))
            on_error("FreeInputStart.method", exc)
            raise

    ctx.log("probe log: {}".format(log_path))
