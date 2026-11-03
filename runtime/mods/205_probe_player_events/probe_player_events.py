# -*- coding: utf-8 -*-
"""計測: プレイヤーの行動をトリガーにしたイベントを差し込む場所を特定する。

やりたいこと（機能追加）は「宿屋に着いたら主人が話しかけてくる」の類で、
そのために必要な情報は3つある。
どれもソースが読めない以上、実行中のプロセスに聞くしかない。

  1. いつ発火させるか。移動が完了した瞬間はどこか。候補は
     `MovePhaseManager.move_phase`（移動先の facility_id を持つ）と
     `llm_manager:narrator`（移動後の情景描写。current_location を受け取る）。

  2. その時点で何が分かるか。現在地の施設オブジェクト、その
     `facility_type`（セーブ上は 'inn'/'guild'/... ）と `owner`（NPC id）。
     セーブファイルの形は判明済みだが、**実行時のオブジェクトが同じ属性名を
     持っているとは限らない**（Facility.__init__ が何を self に置くかは不明）。

  3. どうやって出すか。テキストの表示経路（`InstantaleApp.add_text`）と、
     自前の LLM 呼び出し（`send_request_with_no_structure`）の戻り値の形。
     `output_data/` の記録では応答は {"text": ...} だが、Python 側で何の型で
     返ってくるかは分からない。

この mod は観測しかしない。
値は変えず、例外も握り潰さない。

版3: 3つとも答えが出て `300_event_facility_arrival` が組まれた後も、記録が上限なしで積もっていた
（`events.log` が1か月で約 21MB）。
内訳は、注入のたびのスナップショットが約半分（1回約 12KB。遅延の当て直しを含めて
1プロセス平均6回ほど走る）、移動1回ごとの書き出し（移動の管理役・施設・情景描写の全属性）が約 5MB、
表示される本文の全行の写しが約 2MB だった。
スナップショットは `sys` の印で1プロセス1回にし、メインスレッドで取る。
移動ごとの書き出し・本文・選択の行には1プロセスあたりの上限を設け、
上限を越えたら何も組み立てずに素通しにする。
包みはすべて `safe=True` にし、受け取った引数をそのまま `orig` へ渡す（キーワードを位置に直さない）。

版4: 記録の書き方と、LLM の送り口の見張りを直した。
戻り値が `str` / `dict` などの組み込み型のとき、属性の書き出しが `<vars() unavailable>` を毎回書いていた
（ゲームの `send_request_with_no_structure` は `str` を返す。GAME.md §2.12）。
組み込み型は属性を開かず、値の行だけにした（戻り値の行は直前の `-> [型]` で足りるので書かない）。
起動時の写しと、移動・情景描写・送り口の記録は、行を貯めて1回で書く（`210_` の版3と同じ手）。
版3は1行ごとにファイルを開いていて、写し1回で約150回開いていた。
`ctx.logger` は複数行を1回で書けるが、時刻は先頭の行にだけ付く。
送り口は llama.cpp のプロバイダ（`request_llm_inference_llama_cpp_completion`）だけを包んでいて、クラウドの日は0件だった。
どのプロバイダでも通る `llm_manager:send_request_with_no_structure`（プロバイダの関数を from-import した別名。GAME.md §2.12）も包む。
この別名は後から生え、クラウドでは「開始する」のたびに作り直されるので、ローダの `llm.watch_aliases` で当てる。
ローカルでは同じ関数の別名どうしなので2枚重なることがあり、外側の1枚だけが書く（スレッドごとの入れ子の印）。
記録の行には、どちらの包みが書いたかを `via` で添える。
"""

import os
import sys
import threading

from instantale_modloader import frames, llm, ui

from instantale_modloader.frames import repr_value

LOG_BASENAME = "events.log"

# 属性の全列挙は量が多いので、中身まで出すのはこのキーワードを含むものだけ。
INTERESTING = ("facility", "node", "area", "location", "player", "npc",
               "character", "current", "phase", "world", "party", "narration")

# 1マネージャあたり何回まで LLM 呼び出しの中身を記録するか（1プロセスあたり。版3）。
MAX_LLM_SAMPLES = 3

# 1プロセスあたりの上限（版3）。
# 移動の書き出し（管理役・施設・情景描写の全属性）は1回で約 3KB あるので少なく。
MAX_MOVE_DUMPS = 20
# 1行で済む記録（移動の開始・選択・会話の開始）。
MAX_LINES = 300
# 表示される本文の写し。
MAX_TEXT_LINES = 500

# スナップショットを「このプロセスで取った」印と、上限の数え。
# 注入し直すと MOD のモジュールごと読み直されるので、モジュール変数では消える。`sys` に置く。
SNAPSHOT_MARK = "_instantale_probe_player_events_snapshot"
COUNTS_ATTR = "_instantale_probe_player_events_counts"

# 自前で LLM を1回だけ呼んで戻り値の型を確かめる。
# 自分でイベント文を生成する以上ここが分からないと書けないが、
# プレイヤーの行動を待つ必要は無い（`output_data/` の記録は {"text": ...} だが、
# それが dict なのか属性を持つオブジェクトなのかは保存形式からは分からない）。
# 確認が済んだら False に戻すこと。
# ゲーム側スレッドを止めないよう別スレッドで走る。
RUN_LLM_SHAPE_PROBE = False

# LLM の送り口（版4で2つにした）。
# 1つ目は llama.cpp のプロバイダの関数で、ローカルの日だけ読み込まれる（クラウドの日はローダが保留を降ろす）。
# 2つ目はプロバイダを問わず通る別名で、後から生える（GAME.md §2.12）。
LOCAL_SEND_TARGET = ("scripts.llm.request_llm_inference_llama_cpp_completion:"
                     "send_request_with_no_structure")
MANAGER_SEND_TARGET = "scripts.llm.llm_manager:send_request_with_no_structure"


def _is_builtin(obj) -> bool:
    """`str` / `dict` / `list` などの組み込み型か（版4）。属性を開いても何も無い。"""
    return type(obj).__module__ == "builtins"


def _guarded(ctx, fn):
    """別スレッドで走らせる計測。ここで投げるとゲームのスレッドを巻き込む。"""
    try:
        fn()
    except Exception:
        ctx.log_exc("events probe: background probe failed")


def _counts() -> dict:
    """上限の数え。プロセスに1つ（版3）。"""
    counts = getattr(sys, COUNTS_ATTR, None)
    if not isinstance(counts, dict):
        counts = {}
        setattr(sys, COUNTS_ATTR, counts)
    return counts


def _take(key: str, limit: int) -> bool:
    """`key` の枠が残っていれば1つ使って True。上限の後は何も組み立てずに素通しにする。"""
    counts = _counts()
    used = counts.get(key, 0)
    if used >= limit:
        return False
    counts[key] = used + 1
    return True


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    write = ctx.logger(LOG_BASENAME)

    # ----------------------------------------------------------------- 補助
    def dump_obj(lines, obj, label, indent="  ", full=False):
        """インスタンスの属性を `lines` に足す。full=False なら INTERESTING のみ中身を出す。

        版4: 書き出しは呼ぶ側がまとめて1回で行う（行の並びを受け取って足すだけ）。
        組み込み型は属性を開かず、値を1行にする（`<vars() unavailable>` は書かない）。
        """
        if obj is None:
            lines.append("{}{}: None".format(indent, label))
            return
        if _is_builtin(obj):
            lines.append("{}{}: {} = {}".format(
                indent, label, type(obj).__name__, repr_value(obj)))
            return
        lines.append("{}{}: {}".format(indent, label, type(obj).__name__))
        try:
            items = sorted(vars(obj).items())
        except Exception:
            lines.append("{}  <vars() unavailable>".format(indent))
            return
        names = [n for n, _ in items]
        lines.append("{}  attrs({}): {}".format(indent, len(names), ", ".join(names)))
        for name, value in items:
            if not full and not any(k in name.lower() for k in INTERESTING):
                continue
            lines.append("{}    {:<28} = {}".format(indent, name, repr_value(value)))

    def flush(lines):
        """貯めた行を1回で書く（版4）。空なら書かない。"""
        if lines:
            write("\n".join(lines))

    find_app = ui.find_app     # 走っている app の探し方はローダの語彙

    # ------------------------------------------------- 一発計測: 現在の状態
    def snapshot():
        """今この瞬間のゲーム状態を写し取る。1プロセス1回（版3）。

        再現待ちが要らない部分はここで全部片付ける。
        プレイヤーが今どこかの施設に立っているなら、
        その施設オブジェクトの属性名がそのまま「イベント側が読める情報」になる。

        印はゲームの中（app と player が揃っている）で取れたときにだけ付ける。
        タイトル画面の注入で印を付けると、そのプロセスでは二度と取れない。
        """
        if getattr(sys, SNAPSHOT_MARK, False):
            return
        app = find_app()
        player = getattr(app, "player", None) if app is not None else None
        if player is None:
            return
        setattr(sys, SNAPSHOT_MARK, True)

        # 版4: 行を貯めて最後に1回で書く。途中で落ちても、そこまでの行は書く。
        lines = []
        try:
            snapshot_lines(lines, app, player)
        finally:
            flush(lines)

    def snapshot_lines(lines, app, player):
        """`snapshot` の中身。行を `lines` に足すだけで、書かない（版4）。"""
        emit = lines.append
        emit("=" * 78)
        emit("snapshot (pid {})".format(os.getpid()))
        dump_obj(lines, app, "app")

        # app が抱えている主要オブジェクトを、
        # 名前で当てずっぽうに掘らず「型が
        # Facility/Node/Area/World/Character のもの」で拾う。
        main = sys.modules.get("__main__")
        characters = sys.modules.get("scripts.characters")
        types_of_interest = {}
        for mod, names in ((main, ("Facility", "Node", "Area", "World")),
                           (characters, ("Character",))):
            for name in names:
                cls = getattr(mod, name, None) if mod is not None else None
                if isinstance(cls, type):
                    types_of_interest[name] = cls
        emit("  resolved classes: {}".format(sorted(types_of_interest)))

        try:
            items = sorted(vars(app).items())
        except Exception:
            items = []
        for attr, value in items:
            for tname, cls in types_of_interest.items():
                if isinstance(value, cls):
                    dump_obj(lines, value, "app.{} [{}]".format(attr, tname),
                             indent="    ", full=True)
                    break

        # 現在地は app ではなく プレイヤーのキャラクタにぶら下がっている
        #   app.player.location      -> Facility（今いる施設そのもの）
        #   app.player.current_node  -> Node
        #   app.player.current_area  -> Area
        # イベントが読む情報は全てここから取れるので、実物の属性名を確認する。
        for attr in ("location", "current_node", "current_area"):
            obj = getattr(player, attr, None)
            dump_obj(lines, obj, "app.player.{}".format(attr), indent="    ", full=True)

        # Node の下の施設一覧（辞書）から、施設オブジェクトの形を1つ見る。
        node = getattr(player, "current_node", None)
        facility_cls = types_of_interest.get("Facility")
        if node is not None and facility_cls is not None:
            for fattr, fvalue in sorted(vars(node).items()):
                if isinstance(fvalue, dict) and fvalue:
                    sample = next(iter(fvalue.values()))
                    if isinstance(sample, facility_cls):
                        emit("    node.{} holds {} Facility object(s)".format(
                            fattr, len(fvalue)))
                        for fid, fac in list(fvalue.items())[:20]:
                            emit("      {:<4} {:<22} type={!r} owner={!r}".format(
                                fid,
                                repr_value(getattr(fac, "name", None))[:22],
                                getattr(fac, "facility_type", "<none>"),
                                getattr(fac, "owner", "<none>")))
                        break

        # 施設の owner が世界の characters にどう対応するか（イベントの話者）。
        world = getattr(app, "world", None)
        location = getattr(player, "location", None)
        owner = getattr(location, "owner", None)
        emit("    current facility owner = {!r}".format(owner))
        roster = getattr(world, "characters", None) if world is not None else None
        if isinstance(roster, dict) and owner is not None:
            npc = roster.get(owner if isinstance(owner, str) else str(owner))
            dump_obj(lines, npc, "owner character", indent="    ")

    # 版3: メインスレッドで取る。`apply()` は注入したスレッドの上で走っている。
    ui.scheduler(ctx, "events probe snapshot")(snapshot)

    # ------------------------------------- 一発計測: LLM 呼び出しの戻り値の形
    def llm_shape_probe():
        module = sys.modules.get("scripts.llm.request_llm_inference_llama_cpp_completion")
        fn = getattr(module, "send_request_with_no_structure", None) if module else None
        if fn is None:
            write("llm shape probe: send_request_with_no_structure unavailable")
            return
        messages = [{"role": "user", "content": "「はい」とだけ答えてください。"}]
        try:
            result = fn("mod_shape_probe", messages, max_tokens=32)
        except Exception as exc:
            write("llm shape probe: raised {}: {}".format(type(exc).__name__, exc))
            return
        lines = ["llm shape probe -> [{}] {}".format(type(result).__name__, repr_value(result))]
        if not _is_builtin(result):
            dump_obj(lines, result, "  probe response", indent="  ", full=True)
        # dict でもオブジェクトでもない場合に備えて、代表的な取り出し方を全部試す。
        for how, getter in (("result.text", lambda r: r.text),
                            ("result['text']", lambda r: r["text"]),
                            ("str(result)", str)):
            try:
                lines.append("    {:<16} = {}".format(how, repr_value(getter(result))))
            except Exception as exc:
                lines.append("    {:<16} !! {}: {}".format(how, type(exc).__name__, exc))
        flush(lines)

    if RUN_LLM_SHAPE_PROBE:
        # **`on_ready` に預ける**（TECH.md §3.6）。
        # `apply()` は再注入と遅延当て直しで最大8回走るので、
        # ここで直に起こすと**実 LLM リクエストが最大8回重なる**。
        # 1回きりの計測は印を付けて1回に畳む。
        ctx.on_ready(lambda: threading.Thread(
            target=lambda: _guarded(ctx, llm_shape_probe),
            name="instantale_mod.llm_shape_probe", daemon=True).start(),
            key="205_probe_player_events:llm_shape")

    # ------------------------------------------------------------ 移動の完了
    @ctx.wrap("__main__:MovePhaseManager.__init__", required=False, safe=True)
    def move_init(orig, self, *args, **kwargs):
        try:
            if _take("move_init", MAX_LINES):
                # `self` の後ろは app, connected_node_id, facility_move_to_id, area_id の順。
                write("MovePhaseManager(connected_node_id={!r}, facility_move_to_id={!r}, "
                      "area_id={!r})".format(
                          frames.arg(args, kwargs, "connected_node_id", 1),
                          frames.arg(args, kwargs, "facility_move_to_id", 2),
                          frames.arg(args, kwargs, "area_id", 3)))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        # 移動が終わった後の状態こそが、イベントを差し込みたい瞬間。
        # 版3: 上限を先に見る。越えたら vars の走査も書き出しもしない。
        if not _take("move_phase", MAX_MOVE_DUMPS):
            return result
        lines = []
        try:
            lines.append("MovePhaseManager.move_phase -> {}".format(repr_value(result)))
            dump_obj(lines, self, "  move manager", indent="  ", full=True)
            app = getattr(self, "app", None) or find_app()
            if app is not None:
                for attr, value in sorted(vars(app).items()):
                    if any(k in attr.lower() for k in
                           ("current_facility", "current_node", "current_area",
                            "player_facility", "location")):
                        lines.append("    app.{:<28} = {}".format(attr, repr_value(value)))
        except Exception:
            ctx.log_exc("events probe: post-move dump failed")
        flush(lines)
        return result

    # ------------------------------------------------ 行動のたびに走る中枢
    @ctx.wrap("__main__:InstantaleApp.process_choice", required=False, safe=True)
    def process_choice(orig, self, *args, **kwargs):
        # function はフェーズ管理オブジェクト。
        # どのクラスが選ばれたかで「プレイヤーが何をしたか」が分かる。
        # スレッド名も出す。
        # 自前でフェーズを起こすとき、
        # メインスレッドから呼んでよいのか（＝ゲーム側が内部で別スレッドに渡しているのか）の判断に要る。
        try:
            if _take("process_choice", MAX_LINES):
                write("process_choice({}, choice_text={!r}) [{}]".format(
                    type(frames.arg(args, kwargs, "function", 0)).__name__,
                    frames.arg(args, kwargs, "choice_text", 1, ""),
                    threading.current_thread().name))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ConversationStartManager.execute", required=False, safe=True)
    def conversation_execute(orig, self, *args, **kwargs):
        try:
            if _take("conversation_execute", MAX_LINES):
                write("ConversationStartManager.execute({!r}) [{}] character_id={!r}".format(
                    frames.arg(args, kwargs, "choice_text", 0),
                    threading.current_thread().name,
                    getattr(self, "character_id", "<none>")))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    # ---------------------------------------------------- 移動後の情景描写
    # 引数の並び: current_narration_log, current_action, player_profile,
    # current_area, current_location, ...
    @ctx.wrap("scripts.llm.llm_manager:narrator", required=False, safe=True)
    def narrator(orig, *args, **kwargs):
        # 版3: 移動ごとの書き出しと同じ枠で数える。越えたら何も組み立てない。
        dump = _take("narrator", MAX_MOVE_DUMPS)
        if dump:
            # 本体の前の行は本体の前に書く（本体が返らなくても前の姿は残る）。
            lines = []
            try:
                current_action = frames.arg(args, kwargs, "current_action", 1)
                current_area = frames.arg(args, kwargs, "current_area", 3)
                current_location = frames.arg(args, kwargs, "current_location", 4)
                lines.append("-" * 78)
                lines.append("narrator(current_action={})".format(repr_value(current_action)))
                lines.append("  current_area     [{}] = {}".format(
                    type(current_area).__name__, repr_value(current_area)))
                lines.append("  current_location [{}] = {}".format(
                    type(current_location).__name__, repr_value(current_location)))
                # current_location がオブジェクトなら、そこから施設情報が取れる。
                if not isinstance(current_location, (str, bytes, int, float, type(None))):
                    dump_obj(lines, current_location, "current_location",
                             indent="  ", full=True)
            except Exception:
                pass
            flush(lines)
        result = orig(*args, **kwargs)
        if dump:
            lines = []
            try:
                lines.append("  -> [{}] {}".format(type(result).__name__, repr_value(result)))
                # 組み込み型は上の行で足りる（版4）。
                if not _is_builtin(result):
                    dump_obj(lines, result, "narrator result", indent="  ", full=True)
            except Exception:
                pass
            flush(lines)
        return result

    # -------------------------------------------- 自前で呼ぶときの戻り値の形
    # 版4: llama.cpp のプロバイダの関数に加えて、どのプロバイダでも通る別名も包む（docstring の版4）。
    # 2枚が重なった呼び出しでは外側の1枚だけが書く。印はスレッドごと。
    sending = threading.local()

    def record_send(site, args, kwargs, call):
        """送り口の包みの中身。`call()` が本体（ほかの層を含む）を呼ぶ。"""
        if getattr(sending, "depth", 0):
            return call()
        sending.depth = 1
        try:
            result = call()
        finally:
            sending.depth = 0
        # マネージャごとに数回だけ記録する（版3で1プロセスあたりにした）。
        # 会話のたびに全文を残すと読めなくなる。
        try:
            manager_name = frames.arg(args, kwargs, "manager_name", 0)
            if _take("llm:{}".format(manager_name), MAX_LLM_SAMPLES):
                message = frames.arg(args, kwargs, "message", 1)
                lines = ["send_request_with_no_structure({!r}) via {} message={}".format(
                    manager_name, site, repr_value(message))]
                if isinstance(message, (list, tuple)) and message:
                    lines.append("  message[0] = {}".format(repr_value(message[0])))
                lines.append("  -> [{}] {}".format(type(result).__name__, repr_value(result)))
                # 組み込み型（ふだんは `str`）は上の行で足りる（版4）。
                if not _is_builtin(result):
                    dump_obj(lines, result, "  response", indent="  ", full=True)
                flush(lines)
        except Exception:
            pass
        return result

    @ctx.wrap(LOCAL_SEND_TARGET, required=False, safe=True)
    def send_no_structure(orig, *args, **kwargs):
        return record_send("llama_cpp", args, kwargs, lambda: orig(*args, **kwargs))

    def hook_manager_send(target):
        @ctx.wrap(target, required=False, safe=True)
        def send_no_structure_any(orig, *args, **kwargs):
            return record_send("llm_manager", args, kwargs, lambda: orig(*args, **kwargs))
        return send_no_structure_any

    # 当て方はこちらが持ち、見張り（後から生える別名・作り直しの後の当て直し）はローダの語彙に任せる
    # （`213_` と同じ形。TECH.md §5.3）。
    llm.watch_aliases(ctx, (MANAGER_SEND_TARGET,), hook_manager_send,
                      label="events probe")

    # ------------------------------------------------------ テキスト表示経路
    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, *args, **kwargs):
        try:
            if _take("add_text", MAX_TEXT_LINES):
                write("add_text({})".format(repr_value(frames.arg(args, kwargs, "context", 0))))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    ctx.log("event probe log: {}".format(log_path))
