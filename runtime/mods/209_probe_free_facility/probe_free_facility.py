# -*- coding: utf-8 -*-
"""計測: 自由生成施設のシーン記述エンジン（`scripts.free_facility`）を写し取る。

リコン（`out/recon/game_modules.txt`）で、
ゲームの中に**イベント記述用の実行エンジン**が入っていることが分かった。
ステップ18種・条件演算子7種・効果（金/アイテム/経験値/状態/治癒）・日数送り・フラグ・LLM ステップ・ゲームのフェーズ呼び出しまで揃っている。

    STEP_TYPES = {label, text, choice, input, goto, if, calc, random,
                  flag_set, flag_get, var_set, effect, llm, elapse,
                  memory, call_phase, history_clear, end}
    CALL_PHASE_ALLOWED = {ShoppingStartManagerRemake, DisplayTrainingChoice,
                          DisplayVacationChoice, DisplayQuestChoice,
                          BattleStartManager}

これが MOD から使えるなら、
シーンを持つ機能（調査もの・お使い・連作イベント）は **自前で状態機械を書かずに済む**。
使えないなら全部自前になる。
**その分かれ目を決めるのに必要な事実だけ**を集めるのがこの MOD。

##### 何が分かれば設計が決まるか（優先順）

1. フラグのスコープ（`_flag_store(scope)`）。
   施設ごとに閉じているなら、複数の施設をまたぐ話が表現できない。
   ここが全体の可否を握る
2. プログラムの出どころ（`_lookup_program`）。
   `world_dict` から来るなら MOD が書き込める。
   ゲームフォルダのデータファイルなら書けない（このローダはゲームフォルダに書かない）
3. プログラムの実物。
   `_DSL_SPEC` と `_EXAMPLE_PROGRAM` がモジュール定数として置いてあるので、
   書き方を推測する必要が無い。
   リコンでは切れているが実行時なら全文が取れる
4. 日数と報酬が本当に動くか（`_do_elapse` / `_effect_item_add`）。
   `307_` は日数送りが `elapse_days` を通らないビルドを踏んでいるので、
   ここは宣言ではなく実際に呼ばれたかを見る
5. この世界で使えるのか。
   `free_facility_enabled` は世界生成時のオプション。
   切って作った世界ではプログラムが1つも無い可能性がある

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
値は書かず、記録に失敗しても本体は必ず呼ぶ。

例外が1つある。
`get_phase_class(name)` をこちらから呼ぶ（下の `probe_phase_classes`）。
これは名前からクラスを引くだけの純粋な参照で、副作用が無い。
`CALL_PHASE_ALLOWED` の各名前が実際に引けるかどうかは、
呼んでみる以外に確かめようがなく、しかも「MOD が
`PhaseSpec` にこれらの名前を書けるか」（`305_` が踏んだ制約）に直結する。
呼ぶのはこの1関数だけで、戻り値は記録するだけで使わない。

##### 引数の並びを決め打ちしない

フックはすべて `*args, kwargs` で受ける。
リコンに出ている署名（`__init__(self, app, program_id, resume=None, vars=None)`）はこのビルドのものでしかなく、
名前で受けると版が変わった瞬間に `TypeError` でゲームを落とす。
計測が本体を壊すのが最悪**なので、位置引数から拾って記録する（`305_` の
`referee_result` と同じ受け方）。

##### 未 import でも構わない

`scripts.free_facility` は自由生成施設に入るまで import されない可能性がある。
フックは `required=False` で先に登録しておけばローダが保留し、
現れた時点で当て直す（TECH.md §3.4）。
だから**フックの登録を先に済ませ、定数のダンプはその後**に置いてある。
順序を逆にすると、モジュールが無い回に早期 return して保留の監視すら始まらない。

定数のダンプと世界の調査は印を分けてある。
前者はモジュールさえ在ればよく、後者は `app` が要る。
1つの印にまとめると、`app` がまだ無い回にダンプが走っただけで「調査済み」になり、
世界の調査が永久に行われない。
"""

import datetime
import json
import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "free_facility.log"

MODULE = "scripts.free_facility"
GEN_MODULE = "scripts.llm.llm_manager_free_facility"

# 1プロセスに1回だけにするための印。
# `sys` に置く。
# 注入し直すと MOD のモジュールごと読み直されるので、
# モジュール変数に持つと印ごと消えて毎回やり直すことになる（TECH.md
# §3.6 と同じ理由）。
DUMP_MARK = "_instantale_probe_ff_dumped"
SURVEY_MARK = "_instantale_probe_ff_surveyed"

# 全文を出す定数。
# **`scripts.free_facility` ではなく生成側（`llm_manager_free_facility`）に置かれている。**
# 実行側に無いのは道理で、
# DSL の説明文と実例は「LLM にプログラムを書かせる」ためのものだから。
# 実行側（`scripts.free_facility`）から引こうとすると何も出ない。
FULL_CONSTANTS = ("_DSL_SPEC", "_EXAMPLE_PROGRAM")

# 1行に収まる定数。
# 存在しないものは黙って飛ばす（版で増減しうる）。
SHORT_CONSTANTS = (
    "PROGRAM_VERSION", "STEP_TYPES", "IF_OPS", "CALC_OPS", "MANAGER_EFFECTS",
    "CALL_PHASE_ALLOWED", "DEFAULT_MAX_LLM_CALLS", "MAX_STEPS_PER_EXECUTE",
    "SESSION_MAX_CHARS", "SESSION_MAX_ENTRIES", "ELAPSE_MAX_UNITS",
    "STATUS_MAX_PER_VISIT", "STATUS_MAX_DURATION", "EXP_MAX_SCALE",
    "EXP_MAX_SCALE_PER_VISIT", "ITEM_RARITIES", "ITEM_TYPE_SUBTYPES",
    "ITEM_VALUE_COEFFICIENTS", "PRICE_UNITS_CHARGE", "PRICE_UNITS_PAYOUT",
    "PRICE_TABLE_MAX", "PLAYER_COND_ATTRS", "_OWNER_ATTRS", "_FACILITY_ATTRS",
    "_PLAYER_ATTRS", "_TEMPLATE_RE", "EFFECTS",
)

GEN_CONSTANTS = ("OUTPUT_MODES", "CONTEXT_KEYS", "FIELD_TYPE_MAP",
                 "GENERATED_MAX_LLM_CALLS", "_CtxKey")

# `_flag_store` は1回の来訪で何度も呼ばれる。
# **scope の種類が知りたいだけ** なので、
# 同じ scope は最初の1回しか書かない（種類が出揃えばそれで足りる）。
MAX_FLAG_SAMPLES = 60

# プログラム本体は大きい。
# 最初の数本を JSON で全文残せば形は分かる。
MAX_PROGRAM_DUMPS = 3

# ステップの実行記録の上限。
# 1シーンぶん追えれば十分。
MAX_STEP_EVENTS = 400

# 世界を舐めるときの上限（大きい世界でも注入を待たせない）。
MAX_AREAS_SCANNED = 40

# 自由生成施設の `facility_type`。
# 実機の census で確認した値（`owner=['0/10 free']` のように出た）。
FREE_FACILITY_TYPE = "free"

# プログラム id の付き方。
# 実機で施設 id 10 の `free` 施設に対して
# `FreeFacilityManager(app, 'free_10')` が来た。
PROGRAM_ID_PREFIX = "free_"

# 丸ごと開く自由生成施設の数。
# 1つ在れば形は分かる。
MAX_FREE_DUMPS = 3


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, stamp=False)

    def stamp():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def own_names(obj):
        try:
            return sorted(vars(obj))
        except Exception:
            return []

    def as_json(value):
        """プログラムなど構造物を全文で残す。pydantic でも辞書でも通す。"""
        for name in ("model_dump", "dict"):
            method = getattr(value, name, None)
            if callable(method):
                try:
                    value = method()
                    break
                except Exception:
                    pass
        try:
            return json.dumps(value, ensure_ascii=False, indent=2, default=repr)
        except Exception:
            return repr(value)

    def full_repr(value):
        """定数は切らずに出す。

        `frames.repr_value` は
        220 文字で切る（トレースバックの1行に収めるための既定）。
        ここで見たいのは「集合に何が入っているか」そのもので、
        1回目の実機では `STEP_TYPES` が 18 件のうち 12 件までしか出なかった。
        """
        if isinstance(value, (set, frozenset)):
            return "(len={}) {}".format(
                len(value), sorted(value, key=str))
        if isinstance(value, dict):
            return "(len={}) {}".format(len(value), as_json(value))
        return frames.repr_value(value)

    def first_arg(args, kwargs, name):
        """位置引数を優先し、無ければ名前で拾う。署名は決め打ちしない。"""
        if args:
            return args[0]
        return kwargs.get(name)

    # ------------------------------------------------------------------
    # フックの登録を先に済ませる（未 import なら保留され、現れた時点で当て直る）
    # ------------------------------------------------------------------

    @ctx.wrap(MODULE + ":FreeFacilityManager.__init__", required=False)
    def manager_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            write("\n[{}] FreeFacilityManager(args={} kwargs={})".format(
                stamp(), frames.repr_value(args), frames.repr_value(kwargs)))
            write("    from {}".format(frames.caller()))
            # `resume` に中身があれば、中断と再開がセーブを跨ぐということ。
            # 事件の状態をエンジン側に預けられるかがここで分かる。
            write("    vars(manager): " + (", ".join(own_names(self)) or "<none>"))
        except Exception:
            ctx.log_exc("free facility probe: manager init record failed")
        return result

    @ctx.wrap(MODULE + ":FreeFacilityManager._lookup_program", required=False)
    def lookup_program(orig, self, *args, **kwargs):
        program = orig(self, *args, **kwargs)
        try:
            if state["programs"] < MAX_PROGRAM_DUMPS:
                state["programs"] += 1
                write("\n" + "=" * 72)
                write("[{}] _lookup_program -> {}".format(
                    stamp(), type(program).__name__))
                write("    from {}".format(frames.caller()))
                # ★ ここが「プログラムの出どころ」の答えになる。
                #    world_dict から来ているなら MOD が書き込める。
                write(as_json(program))
            else:
                write("[{}] _lookup_program -> {} (dump limit reached)".format(
                    stamp(), type(program).__name__))
        except Exception:
            ctx.log_exc("free facility probe: program dump failed")
        return program

    @ctx.wrap(MODULE + ":FreeFacilityManager._flag_store", required=False)
    def flag_store(orig, self, *args, **kwargs):
        store = orig(self, *args, **kwargs)
        try:
            # ★ 最重要。
            # scope に何が来るかで、施設をまたぐ話が書けるかが決まる。
            scope = first_arg(args, kwargs, "scope")
            key = frames.repr_value(scope)
            if key not in state["flags"] and len(state["flags"]) < MAX_FLAG_SAMPLES:
                state["flags"].add(key)
                shape = type(store).__name__
                if isinstance(store, dict):
                    shape += " keys={}".format(sorted(store)[:20])
                write("[{}] _flag_store(scope={}) -> {}".format(
                    stamp(), key, shape))
                # 同じ辞書が返るなら共有、別なら分かれている。
                # id を並べれば「スコープが本当に別々か」が目で分かる。
                write("    id(store)={:x}  from {}".format(
                    id(store) if store is not None else 0, frames.caller()))
        except Exception:
            ctx.log_exc("free facility probe: flag store record failed")
        return store

    def step_event(label, payload):
        if state["steps"] >= MAX_STEP_EVENTS:
            return
        state["steps"] += 1
        write("[{}] {} {}".format(stamp(), label, payload))

    @ctx.wrap(MODULE + ":FreeFacilityManager._do_elapse", required=False)
    def do_elapse(orig, self, *args, **kwargs):
        # `307_` は日数送りが `elapse_days` を通らないビルドを踏んでいる。
        # ここで実際に呼ばれるかどうかを見る（宣言ではなく実測）。
        step_event("_do_elapse", frames.repr_value(first_arg(args, kwargs, "step")))
        return orig(self, *args, **kwargs)

    @ctx.wrap(MODULE + ":FreeFacilityManager._effect_item_add", required=False)
    def effect_item_add(orig, self, *args, **kwargs):
        step_event("_effect_item_add", frames.repr_value(args))
        return orig(self, *args, **kwargs)

    @ctx.wrap(MODULE + ":FreeFacilityManager._effect_exp_add", required=False)
    def effect_exp_add(orig, self, *args, **kwargs):
        step_event("_effect_exp_add", frames.repr_value(args))
        return orig(self, *args, **kwargs)

    @ctx.wrap(MODULE + ":FreeFacilityManager._do_llm", required=False)
    def do_llm(orig, self, *args, **kwargs):
        # LLM ステップの出力モード（fields / choice / text）が実際にどう使われるか。
        # 「AI に描写させ、進行はスクリプトが持つ」形の実例になる。
        step_event("_do_llm", frames.repr_value(args[1:2] or args))
        return orig(self, *args, **kwargs)

    @ctx.wrap(MODULE + ":FreeFacilityManager.execute", required=False)
    def execute(orig, self, *args, **kwargs):
        step_event("execute", "choice_text=" + frames.repr_value(
            first_arg(args, kwargs, "choice_text")))
        return orig(self, *args, **kwargs)

    @ctx.wrap(GEN_MODULE + ":generate_program", required=False)
    def generate_program(orig, *args, **kwargs):
        write("\n[{}] generate_program(args={} kwargs={})".format(
            stamp(), frames.repr_value(args), frames.repr_value(kwargs)))
        program = orig(*args, **kwargs)
        try:
            write("[{}] generate_program -> {}".format(
                stamp(), type(program).__name__))
            if state["programs"] < MAX_PROGRAM_DUMPS:
                state["programs"] += 1
                write(as_json(program))
        except Exception:
            ctx.log_exc("free facility probe: generated program dump failed")
        return program

    # ------------------------------------------------------------------
    # 定数のダンプ（モジュールさえ在ればよい。1プロセスに1回）
    # ------------------------------------------------------------------
    def dump_constants():
        module = sys.modules.get(MODULE)
        if module is None:
            ctx.log("{} not imported yet; the dump waits for it".format(MODULE))
            return
        if getattr(sys, DUMP_MARK, False):
            return
        setattr(sys, DUMP_MARK, True)

        write("\n" + "#" * 72)
        write("# {}  free facility engine dump".format(stamp()))
        write("# file={}".format(getattr(module, "__file__", "?")))
        write("#" * 72)

        for name in SHORT_CONSTANTS:
            value = getattr(module, name, frames.MISSING)
            if value is frames.MISSING:
                continue
            if name == "EFFECTS" and isinstance(value, dict):
                # 中身は関数なので、名前だけ並べたほうが読める。
                write("{:<28} = {}".format(name, sorted(value)))
                continue
            write("{:<28} = {}".format(name, full_repr(value)))

        dump_generator()
        probe_phase_classes(module)

    def dump_generator():
        gen = sys.modules.get(GEN_MODULE)
        if gen is None:
            write("\n{} not imported yet".format(GEN_MODULE))
            return
        write("\n" + "-" * 72)
        write("--- {} ---".format(GEN_MODULE))
        write("-" * 72)
        for name in GEN_CONSTANTS:
            value = getattr(gen, name, frames.MISSING)
            if value is frames.MISSING:
                continue
            write("{:<28} = {}".format(name, full_repr(value)))

        # DSL の仕様書と実例。
        # ここが本命（実行側には無い）。
        for name in FULL_CONSTANTS:
            value = getattr(gen, name, frames.MISSING)
            if value is frames.MISSING:
                write("\n{} not found on {}".format(name, GEN_MODULE))
                continue
            write("\n" + "-" * 72)
            write("--- {}.{} (full) ---".format(GEN_MODULE, name))
            write("-" * 72)
            write(value if isinstance(value, str) else as_json(value))

        # ステップ型の union。
        # DSL で書ける step の正確な一覧と、各ステップが持つ項目が出る。
        # これが実質の DSL リファレンスになる。
        step_union = getattr(gen, "_GenStep", frames.MISSING)
        if step_union is not frames.MISSING:
            members = getattr(step_union, "__args__", ())
            write("_GenStep members ({}):".format(len(members)))
            for member in members:
                write("    " + getattr(member, "__name__", repr(member)))
                fields = getattr(member, "model_fields", None)
                if isinstance(fields, dict):
                    for field, info in fields.items():
                        write("        {:<20} {}".format(
                            field,
                            frames.repr_value(getattr(info, "annotation", info))))

        program_model = getattr(gen, "GeneratedFreeFacilityProgram", frames.MISSING)
        if program_model is not frames.MISSING:
            schema = getattr(program_model, "model_json_schema", None)
            if callable(schema):
                try:
                    write("\n--- GeneratedFreeFacilityProgram schema ---")
                    write(as_json(schema()))
                except Exception:
                    ctx.log_exc("free facility probe: schema dump failed")

    def probe_phase_classes(module):
        """`call_phase` の許可リストが実際に引けるかを見る。

        呼ぶのは名前からクラスを引くだけの `get_phase_class` で、副作用は無い。
        `PhaseSpec` に書ける名前かどうかは `305_` が踏んだ制約に直結する。
        """
        resolver = getattr(module, "get_phase_class", None)
        allowed = getattr(module, "CALL_PHASE_ALLOWED", ())
        if not callable(resolver) or not allowed:
            return
        write("\n--- get_phase_class(CALL_PHASE_ALLOWED) ---")
        for name in sorted(allowed):
            try:
                found = resolver(name)
                write("    {:<32} -> {}.{}".format(
                    name, getattr(found, "__module__", "?"),
                    getattr(found, "__name__", repr(found))))
            except Exception as exc:
                write("    {:<32} -> ERROR {}: {}".format(
                    name, type(exc).__name__, exc))

    # ------------------------------------------------------------------
    # 世界の調査（`app` が要るので on_ready 側。1プロセスに1回）
    # ------------------------------------------------------------------
    def survey_world():
        """この世界に自由生成施設のプログラムが在るのか。

        `free_facility_enabled` は世界生成時のオプションなので、
        切って作った世界では1つも無いことがありうる。
        既存セーブで使えるかの答えになる。
        """
        if getattr(sys, SURVEY_MARK, False):
            return
        app = ui.find_app()
        if app is None:
            return
        # 世界が載るまで印を付けない。
        # `on_ready` は全 MOD の適用直後＝まだタイトル画面のことがあり、
        # 1回目の実機はそれで `world_dict` が None のまま「調査済み」になった。
        # 載っていなければ黙って諦め、
        # プレイヤーが何か押したときにもう一度試す（`retry_survey`）。
        try:
            areas = list(ui.world_areas(app).items())[:MAX_AREAS_SCANNED]
        except Exception:
            areas = []
        if not areas:
            return
        setattr(sys, SURVEY_MARK, True)

        write("\n--- world survey: where do programs live? ---")
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            write("    world_dict keys: {}".format(sorted(world_dict)[:40]))
            hits = [key for key in world_dict
                    if "facility" in str(key).lower()
                    or "program" in str(key).lower()]
            write("    world_dict candidates: {}".format(hits or "<none>"))
        else:
            write("    app.world_dict is {}".format(type(world_dict).__name__))

        found, scanned = [], 0
        # 実機で `facility_type == 'free'` の施設が在り、
        # そこで走ったプログラムの id が `free_10`（＝施設 id 10）だった。
        # **つまりプログラムは施設1つにつき1本、id で結び付いている。**
        # だからまず `free` 型の施設を名指しで開いて、
        # プログラム本体がそこに載っているのかを見る。
        free_facilities = []
        for area_id, area in areas:
            for node in ui.nodes_of(area):
                for facility_id, facility in ui.facilities_of(node).items():
                    scanned += 1
                    kind = ui.facility_type_of(facility)
                    try:
                        own = vars(facility)
                    except Exception:
                        continue
                    if kind == FREE_FACILITY_TYPE:
                        free_facilities.append(
                            (area_id, facility_id, facility, own))
                    marks = [key for key in own
                             if "program" in str(key).lower()
                             or "free" in str(key).lower()]
                    if marks:
                        found.append(
                            "area={} facility={} type={} keys={}".format(
                                area_id, facility_id, kind, marks))
        write("    facilities scanned: {}".format(scanned))
        write("    facility_type == {!r}: {}".format(
            FREE_FACILITY_TYPE,
            [(a, f) for a, f, _obj, _own in free_facilities] or "<none>"))
        if found:
            for line in found[:40]:
                write("    " + line)
        else:
            write("    no facility carries a program-looking attribute")

        # `free` 型の施設を丸ごと開く。
        # **プログラムがここに載っているなら
        # MOD が書き込める**（＝自前のシーンを置ける）。
        # 載っていなければ出どころは別（データファイルなら書けない）。
        for area_id, facility_id, facility, own in free_facilities[:MAX_FREE_DUMPS]:
            write("\n    --- free facility {}/{} (expected program id {}{}) ---"
                  .format(area_id, facility_id, PROGRAM_ID_PREFIX, facility_id))
            for key in sorted(own):
                write("        {:<24} = {}".format(
                    key, frames.repr_value(own[key])))
            config = own.get("config")
            if isinstance(config, dict):
                write("        config (full):")
                write(as_json(config))

    # 世界が載るのはタイトルで「つづきから」を押した後。
    # `on_ready` の時点ではまだ無いことがあるので、**プレイヤーが何か押すたびに試す**。
    # 印が立った後は辞書引き1回で降りるだけなので、押下の経路を重くしない。
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def retry_survey(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if not getattr(sys, SURVEY_MARK, False):
            try:
                survey_world()
            except Exception:
                ctx.log_exc("free facility probe: survey retry failed")
        return result

    # 保留から復帰した回でも拾えるよう、apply() のたびに試す（印で1回に絞る）。
    dump_constants()
    ctx.on_ready(survey_world)

    ctx.log("free facility probe: watching {}; notes go to out/{}".format(
        MODULE, LOG_BASENAME))
