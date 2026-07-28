# -*- coding: utf-8 -*-
"""計測: プレイヤーの行動をトリガーにしたイベントを差し込む場所を特定する。

やりたいこと（機能追加）は「宿屋に着いたら主人が話しかけてくる」の類で、
そのために必要な情報は3つある。どれもソースが読めない以上、実行中の
プロセスに聞くしかない。

  1. **いつ発火させるか** — 移動が完了した瞬間はどこか。候補は
     `MovePhaseManager.move_phase`（移動先の facility_id を持つ）と
     `llm_manager:narrator`（移動後の情景描写。current_location を受け取る）。

  2. **その時点で何が分かるか** — 現在地の施設オブジェクト、その
     `facility_type`（セーブ上は 'inn'/'guild'/... ）と `owner`（NPC id）。
     セーブファイルの形は判明済みだが、**実行時のオブジェクトが同じ属性名を
     持っているとは限らない**（Facility.__init__ が何を self に置くかは不明）。

  3. **どうやって出すか** — テキストの表示経路（`InstantaleApp.add_text`）と、
     自前の LLM 呼び出し（`send_request_with_no_structure`）の戻り値の形。
     `output_data/` の記録では応答は {"text": ...} だが、Python 側で何の型で
     返ってくるかは分からない。

この mod は**観測しかしない**。値は変えず、例外も握り潰さない。
"""

import datetime
import sys

from instantale_modloader.frames import repr_value

NAME = "probe: player-action event trigger sites"

LOG_BASENAME = "events.log"

# 属性の全列挙は量が多いので、中身まで出すのはこのキーワードを含むものだけ。
INTERESTING = ("facility", "node", "area", "location", "player", "npc",
               "character", "current", "phase", "world", "party", "narration")

# 1マネージャあたり何回まで LLM 呼び出しの中身を記録するか。
MAX_LLM_SAMPLES = 3

# 自前で LLM を1回だけ呼んで**戻り値の型**を確かめる。
# 自分でイベント文を生成する以上ここが分からないと書けないが、プレイヤーの
# 行動を待つ必要は無い（`output_data/` の記録は {"text": ...} だが、それが
# dict なのか属性を持つオブジェクトなのかは保存形式からは分からない）。
# 確認が済んだら False に戻すこと。ゲーム側スレッドを止めないよう別スレッドで走る。
RUN_LLM_SHAPE_PROBE = False


def _guarded(ctx, fn):
    """別スレッドで走らせる計測。ここで投げるとゲームのスレッドを巻き込む。"""
    try:
        fn()
    except Exception:
        ctx.log_exc("events probe: background probe failed")


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    llm_samples = {}

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("events probe: write failed")

    # ----------------------------------------------------------------- 補助
    def dump_obj(obj, label, indent="  ", full=False):
        """インスタンスの属性を書き出す。full=False なら INTERESTING のみ中身を出す。"""
        if obj is None:
            write("{}{}: None".format(indent, label))
            return
        write("{}{}: {}".format(indent, label, type(obj).__name__))
        try:
            items = sorted(vars(obj).items())
        except Exception:
            write("{}  <vars() unavailable>".format(indent))
            return
        names = [n for n, _ in items]
        write("{}  attrs({}): {}".format(indent, len(names), ", ".join(names)))
        for name, value in items:
            if not full and not any(k in name.lower() for k in INTERESTING):
                continue
            write("{}    {:<28} = {}".format(indent, name, repr_value(value)))

    def find_app():
        """走っている InstantaleApp のインスタンスを取る。"""
        main = sys.modules.get("__main__")
        cls = getattr(main, "InstantaleApp", None)
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and (cls is None or isinstance(app, cls)):
                return app
        except Exception:
            pass
        # kivy 経由が駄目なら __main__ のグローバルから探す。
        if main is not None and cls is not None:
            for value in vars(main).values():
                if isinstance(value, cls):
                    return value
        return None

    # ------------------------------------------------- 一発計測: 現在の状態
    def snapshot():
        """今この瞬間のゲーム状態を写し取る。

        再現待ちが要らない部分はここで全部片付ける。プレイヤーが今どこかの
        施設に立っているなら、その施設オブジェクトの属性名がそのまま
        「イベント側が読める情報」になる。
        """
        write("=" * 78)
        write("snapshot (pid {})".format(__import__("os").getpid()))
        app = find_app()
        if app is None:
            write("  InstantaleApp instance not found (not in a game yet?)")
            return
        dump_obj(app, "app")

        # app が抱えている主要オブジェクトを、名前で当てずっぽうに掘らず
        # 「型が Facility/Node/Area/World/Character のもの」で拾う。
        main = sys.modules.get("__main__")
        characters = sys.modules.get("scripts.characters")
        types_of_interest = {}
        for mod, names in ((main, ("Facility", "Node", "Area", "World")),
                           (characters, ("Character",))):
            for name in names:
                cls = getattr(mod, name, None) if mod is not None else None
                if isinstance(cls, type):
                    types_of_interest[name] = cls
        write("  resolved classes: {}".format(sorted(types_of_interest)))

        try:
            items = sorted(vars(app).items())
        except Exception:
            items = []
        for attr, value in items:
            for tname, cls in types_of_interest.items():
                if isinstance(value, cls):
                    dump_obj(value, "app.{} [{}]".format(attr, tname),
                             indent="    ", full=True)
                    break

        # 現在地は app ではなく **プレイヤーのキャラクタ**にぶら下がっている
        #   app.player.location      -> Facility（今いる施設そのもの）
        #   app.player.current_node  -> Node
        #   app.player.current_area  -> Area
        # イベントが読む情報は全てここから取れるので、実物の属性名を確認する。
        player = getattr(app, "player", None)
        for attr in ("location", "current_node", "current_area"):
            obj = getattr(player, attr, None) if player is not None else None
            dump_obj(obj, "app.player.{}".format(attr), indent="    ", full=True)

        # Node の下の施設一覧（辞書）から、施設オブジェクトの形を1つ見る。
        node = getattr(player, "current_node", None) if player is not None else None
        facility_cls = types_of_interest.get("Facility")
        if node is not None and facility_cls is not None:
            for fattr, fvalue in sorted(vars(node).items()):
                if isinstance(fvalue, dict) and fvalue:
                    sample = next(iter(fvalue.values()))
                    if isinstance(sample, facility_cls):
                        write("    node.{} holds {} Facility object(s)".format(
                            fattr, len(fvalue)))
                        for fid, fac in list(fvalue.items())[:20]:
                            write("      {:<4} {:<22} type={!r} owner={!r}".format(
                                fid,
                                repr_value(getattr(fac, "name", None))[:22],
                                getattr(fac, "facility_type", "<none>"),
                                getattr(fac, "owner", "<none>")))
                        break

        # 施設の owner が世界の characters にどう対応するか（イベントの話者）。
        world = getattr(app, "world", None)
        location = getattr(player, "location", None) if player is not None else None
        owner = getattr(location, "owner", None)
        write("    current facility owner = {!r}".format(owner))
        characters = getattr(world, "characters", None) if world is not None else None
        if isinstance(characters, dict) and owner is not None:
            npc = characters.get(owner if isinstance(owner, str) else str(owner))
            dump_obj(npc, "owner character", indent="    ")

    try:
        snapshot()
    except Exception:
        ctx.log_exc("events probe: snapshot failed")

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
        write("llm shape probe -> [{}] {}".format(type(result).__name__, repr_value(result)))
        dump_obj(result, "  probe response", indent="  ", full=True)
        # dict でもオブジェクトでもない場合に備えて、代表的な取り出し方を全部試す。
        for how, getter in (("result.text", lambda r: r.text),
                            ("result['text']", lambda r: r["text"]),
                            ("str(result)", str)):
            try:
                write("    {:<16} = {}".format(how, repr_value(getter(result))))
            except Exception as exc:
                write("    {:<16} !! {}: {}".format(how, type(exc).__name__, exc))

    if RUN_LLM_SHAPE_PROBE:
        import threading
        threading.Thread(target=lambda: _guarded(ctx, llm_shape_probe),
                         name="instantale_mod.llm_shape_probe", daemon=True).start()

    # ------------------------------------------------------------ 移動の完了
    @ctx.wrap("__main__:MovePhaseManager.__init__", required=False)
    def move_init(orig, self, app, connected_node_id, facility_move_to_id, area_id=None,
                  *args, **kwargs):
        write("MovePhaseManager(connected_node_id={!r}, facility_move_to_id={!r}, "
              "area_id={!r})".format(connected_node_id, facility_move_to_id, area_id))
        return orig(self, app, connected_node_id, facility_move_to_id, area_id,
                    *args, **kwargs)

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False)
    def move_phase(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        # 移動が終わった後の状態こそが、イベントを差し込みたい瞬間。
        write("MovePhaseManager.move_phase -> {}".format(repr_value(result)))
        dump_obj(self, "  move manager", indent="  ", full=True)
        try:
            app = getattr(self, "app", None) or find_app()
            if app is not None:
                for attr, value in sorted(vars(app).items()):
                    if any(k in attr.lower() for k in
                           ("current_facility", "current_node", "current_area",
                            "player_facility", "location")):
                        write("    app.{:<28} = {}".format(attr, repr_value(value)))
        except Exception:
            ctx.log_exc("events probe: post-move dump failed")
        return result

    # ------------------------------------------------ 行動のたびに走る中枢
    @ctx.wrap("__main__:InstantaleApp.process_choice", required=False)
    def process_choice(orig, self, function, choice_text="", *args, **kwargs):
        # function はフェーズ管理オブジェクト。どのクラスが選ばれたかで
        # 「プレイヤーが何をしたか」が分かる。
        # スレッド名も出す ― 自前でフェーズを起こすとき、メインスレッドから
        # 呼んでよいのか（＝ゲーム側が内部で別スレッドに渡しているのか）の判断に要る。
        import threading
        write("process_choice({}, choice_text={!r}) [{}]".format(
            type(function).__name__, choice_text,
            threading.current_thread().name))
        return orig(self, function, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:ConversationStartManager.execute", required=False)
    def conversation_execute(orig, self, choice_text, *args, **kwargs):
        import threading
        write("ConversationStartManager.execute({!r}) [{}] character_id={!r}".format(
            choice_text, threading.current_thread().name,
            getattr(self, "character_id", "<none>")))
        return orig(self, choice_text, *args, **kwargs)

    # ---------------------------------------------------- 移動後の情景描写
    @ctx.wrap("scripts.llm.llm_manager:narrator", required=False)
    def narrator(orig, current_narration_log, current_action, player_profile,
                 current_area, current_location, *args, **kwargs):
        write("-" * 78)
        write("narrator(current_action={})".format(repr_value(current_action)))
        write("  current_area     [{}] = {}".format(
            type(current_area).__name__, repr_value(current_area)))
        write("  current_location [{}] = {}".format(
            type(current_location).__name__, repr_value(current_location)))
        # current_location がオブジェクトなら、そこから施設情報が取れる。
        if not isinstance(current_location, (str, bytes, int, float, type(None))):
            dump_obj(current_location, "current_location", indent="  ", full=True)
        result = orig(current_narration_log, current_action, player_profile,
                      current_area, current_location, *args, **kwargs)
        write("  -> [{}] {}".format(type(result).__name__, repr_value(result)))
        dump_obj(result, "narrator result", indent="  ", full=True)
        return result

    # -------------------------------------------- 自前で呼ぶときの戻り値の形
    @ctx.wrap("scripts.llm.request_llm_inference_llama_cpp_completion:"
              "send_request_with_no_structure", required=False)
    def send_no_structure(orig, manager_name, message, *args, **kwargs):
        count = llm_samples.get(manager_name, 0)
        result = orig(manager_name, message, *args, **kwargs)
        # マネージャごとに数回だけ記録する。会話のたびに全文を残すと読めなくなる。
        if count < MAX_LLM_SAMPLES:
            llm_samples[manager_name] = count + 1
            write("send_request_with_no_structure({!r}) message={}".format(
                manager_name, repr_value(message)))
            if isinstance(message, (list, tuple)) and message:
                write("  message[0] = {}".format(repr_value(message[0])))
            write("  -> [{}] {}".format(type(result).__name__, repr_value(result)))
            dump_obj(result, "  response", indent="  ", full=True)
        return result

    # ------------------------------------------------------ テキスト表示経路
    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context, *args, **kwargs):
        write("add_text({})".format(repr_value(context)))
        return orig(self, context, *args, **kwargs)

    ctx.log("event probe log: {}".format(log_path))
