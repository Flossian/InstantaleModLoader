# -*- coding: utf-8 -*-
"""計測: クエストの受注経路と、選択肢ボタンの登録方法を特定する。

やりたいこと（機能追加）は「NPC との会話中に依頼を受注する」で、
そのために必要な情報は3つある。
どれもソースが読めない以上、実行中のプロセスに聞くしかない。

  1. 選択肢ボタンをどう出すか。`Display*Choice.update_button_display()` が
     何を触っているのか。`app` には `buttons` / `to_display_buttons` /
     `display_button_map` / `function_correspond_to_input` / `choice_button_page`
     の5つがあり、どれが「押されたときに呼ぶマネージャ」の対応表なのかが不明。
     これが分からないと「依頼を受ける」ボタンも「個別依頼の一覧」も出せない。
     **`update_button_display` の前後で差分を取る**ことで確定させる。

  2. どうやって受注するか。`QuestChoiceManager(app, quest_type, quest_id)` と
     `QuestStartManager(app, quest_type, quest_id)` がある。`quest_type` に何が
     入るのか（セーブ上は 'normal_quest'）、`quest_id` は str か int か、
     受注確定はどちらのどのメソッドか。300_ で確立した
     `app.process_choice(マネージャのインスタンス, choice_text)` の形に乗せたい。

  3. **クエストの実体をどう作るか**。セーブ上のクエストは
     `world_dict['quests'][id]` に QuestStructure（random_quest_generator の出力）
     ＋ difficulty / neighboring_settlement_id / id / quest_type / config /
     quest_area_id が付いた dict。**`quest_area_id` が指すエリアは別に生成が要る**
     （`World.generate_quest_area` / `save_area_json:generate_quest_area`）。
     `World.generate_quests` と `DisplayQuestChoice.generate_random_quest` の
     どちらが「1件を世界に登録する」入口なのかを見る。

この mod は観測しかしない。
値は変えず、例外も握り潰さない（wrap の中で記録に失敗しても本体は必ず呼ぶ）。

版3: 読み取り専用の約束が破れていたので直した。
`quest_type` の語彙の総当たり（`QuestChoiceManager` を候補12個 × 2件で組み立てる）は、
注入のたびに生きた世界に対して走っていた。
`QuestChoiceManager.__init__` は `318_area_difficulty_growth` が包んでいて、受注前の寄せ直しで
依頼の難易度と `state` フォルダの控えを書く。そのため総当たりのたびに `318_` の書き込みが起きていた
（`area_difficulty.log` の `accept:` の行が、どれも総当たりと同じ時刻だった）。
答えは `'settlement_quest'` で決着している（GAME.md §2.9）ので、総当たりはコードごと消した。
待機表示の 20Hz の見張りスレッドも消した。問いは `234_probe_busy_display` で決着していて
（GAME.md §2.4）、上限に達した後も書かずに回り続け、メインスレッドの外から HUD のウィジェットを読んでいた
（`quest_flow.log` の約6割がこの行だった）。
スナップショットは `sys` の印で1プロセス1回にし、メインスレッドで取る。
標本の数えは1プロセスあたりにし、上限を先に見てから `button_state` / `wait_state` を組み立てる。
`on_button_press` を2回包んでいたのを1つにまとめた。
包みはすべて `safe=True` にし、受け取った引数をそのまま `orig` へ渡す。
HUD の文字はメインスレッドの上でだけ読む。
"""

import os
import sys
import threading

from instantale_modloader import frames, ui
from instantale_modloader.frames import repr_value

LOG_BASENAME = "quest_flow.log"

# 起動直後に一度だけ、今のゲーム状態を写し取る（版3で1プロセス1回）。
SNAPSHOT_ON_BOOT = True

# ボタン関係で差分を取る app の属性。1. の候補全部。
BUTTON_ATTRS = ("buttons", "buttons_backup", "to_display_buttons",
                "display_button_map", "function_correspond_to_input",
                "choice_button_page")

# クエスト dict の中身を全部出すと敵データで数千行になる。
# 上位だけ出す。
QUEST_TOP_KEYS = ("quest_title", "client_name", "request_summary", "difficulty",
                  "id", "quest_type", "config", "quest_area_id",
                  "neighboring_settlement_id")

# 1つの wrap あたり何回まで記録するか（版3で1プロセスあたりにした）。
# 会話中に何度も走るものがあるため。
MAX_SAMPLES = 6
# 依頼の生成・受注のように稀にしか起きないものの上限。
MAX_RARE_SAMPLES = 30

# スナップショットの印・標本の数え・PhaseSpec の初見の控え。
# 注入し直すと MOD のモジュールごと読み直されるので、モジュール変数では消える。`sys` に置く。
SNAPSHOT_MARK = "_instantale_probe_quest_flow_snapshot"
COUNTS_ATTR = "_instantale_probe_quest_flow_counts"
SEEN_SPECS_ATTR = "_instantale_probe_quest_flow_specs"


def _sig_of(func):
    """コンパイル済み関数でも読める範囲で引数の形を出す。"""
    try:
        import inspect
        return str(inspect.signature(func))
    except Exception as exc:
        return "<signature unavailable: {}>".format(type(exc).__name__)


def _process_store(name, factory):
    """プロセスに1つの入れ物（`sys` に置く）。"""
    value = getattr(sys, name, None)
    if not isinstance(value, factory):
        value = factory()
        setattr(sys, name, value)
    return value


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    counts = _process_store(COUNTS_ATTR, dict)

    write = ctx.logger(LOG_BASENAME)

    def sample(key, limit=MAX_SAMPLES):
        """記録回数の上限管理。True の間だけ書く。重い組み立てより先に呼ぶ。"""
        n = counts.get(key, 0)
        if n >= limit:
            return False
        counts[key] = n + 1
        return True

    find_app = ui.find_app     # 走っている app の探し方はローダの語彙

    # ------------------------------------------------------------ ボタンの状態
    def describe_spec(spec):
        """PhaseSpec の中身を「どのクラスを何の引数で呼ぶか」として出す。

        `app.buttons` は `[{'text': str, 'spec': PhaseSpec}, ...]` で、
        `PhaseSpec.__init__(self, cls_name, args)` なので、ボタンは**マネージャの
        インスタンスではなくその作り方**を持っている（GAME.md §2.2）。自前で
        ボタンを足すにはこの `args` の並びを知る必要がある。
        """
        if spec is None:
            return "None"
        data = None
        try:
            to_dict = getattr(spec, "to_dict", None)
            if callable(to_dict):
                data = to_dict()
        except Exception:
            data = None
        if not isinstance(data, dict):
            try:
                data = dict(vars(spec))
            except Exception as exc:
                return "<PhaseSpec unreadable: {}>".format(type(exc).__name__)
        # repr_value は dict をキー一覧に畳んでしまう。
        # ここで欲しいのは cls_name と args の中身なので、自前で組み立てる。
        return "{}(app, *{!r})".format(
            data.get("cls_name"), data.get("args"))[:300]

    def describe_button(entry):
        if not isinstance(entry, dict):
            return repr_value(entry)
        return "{!r} -> {}".format(entry.get("text"), describe_spec(entry.get("spec")))

    def button_state(app):
        """1. のためのスナップショット。ボタンは中身（spec）まで開く。"""
        state = {}
        for attr in BUTTON_ATTRS:
            value = getattr(app, attr, "<missing>")
            if attr in ("buttons", "buttons_backup") and isinstance(value, (list, tuple)):
                state[attr] = "list(len={}) [{}]".format(
                    len(value), " | ".join(describe_button(v) for v in list(value)[:12]))
            elif isinstance(value, dict):
                entries = []
                for k, v in list(value.items())[:24]:
                    entries.append("{!r}: {}".format(k, type(v).__name__))
                state[attr] = "dict(len={}) {{{}}}".format(len(value), ", ".join(entries))
            elif isinstance(value, (list, tuple)):
                entries = []
                for v in list(value)[:24]:
                    entries.append(v if isinstance(v, str) else
                                   "<{}>".format(type(v).__name__))
                state[attr] = "{}(len={}) {!r}".format(
                    type(value).__name__, len(value), entries)
            elif attr == "function_correspond_to_input":
                # 名前に反して対応表ではなく PhaseSpec 1個。
                # 「今テキスト入力を受けたら何を呼ぶか」を保持しているとみられる。
                state[attr] = describe_spec(value)
            else:
                state[attr] = repr_value(value)
        return state

    def diff_buttons(app, label, before):
        """update_button_display の前後で何が変わったかだけを出す。

        全属性を毎回書くとログが読めなくなる。
        変わったものだけ before/after で並べれば、
        「選択肢を出すには何を書けばよいか」がそのまま読める。
        """
        after = button_state(app)
        changed = [k for k in after if before.get(k) != after[k]]
        if not changed:
            write("  {}: no change in {}".format(label, ", ".join(BUTTON_ATTRS)))
            return
        for key in changed:
            write("  {} {}:".format(label, key))
            write("      before = {}".format(before.get(key)))
            write("      after  = {}".format(after[key]))

    # ------------------------------------------------------- 一発計測: 現在の状態
    def dump_quest(quest, label, indent="    "):
        if isinstance(quest, dict):
            write("{}{} [dict] keys={}".format(indent, label, list(quest)))
            for key in QUEST_TOP_KEYS:
                if key in quest:
                    write("{}  {:<26} = {}".format(indent, key, repr_value(quest[key])))
            area = quest.get("area")
            if isinstance(area, dict):
                write("{}  area.name/atomosphere    = {!r} / {!r}".format(
                    indent, area.get("name"), area.get("atomosphere")))
            return
        write("{}{} [{}]".format(indent, label, type(quest).__name__))
        try:
            items = sorted(vars(quest).items())
        except Exception:
            write("{}  <vars() unavailable>".format(indent))
            return
        write("{}  attrs({}): {}".format(indent, len(items), ", ".join(n for n, _ in items)))
        for name, value in items:
            if name in QUEST_TOP_KEYS or name in ("quest_value", "area"):
                write("{}    {:<24} = {}".format(indent, name, repr_value(value)))

    def dump_class(name, module_name="__main__"):
        """クラスが持つメソッド一覧と __init__ の形。

        targets.txt はモジュールレベルのスキャンなので、
        ネストした関数や後から生えたメソッドが漏れることがある（TECH.md
        §4.1 の罠）。
        受注経路を辿るには実物の vars(cls) を見る必要がある。
        """
        module = sys.modules.get(module_name)
        cls = getattr(module, name, None) if module is not None else None
        if not isinstance(cls, type):
            write("  {}: not found in {}".format(name, module_name))
            return
        members = sorted(n for n in vars(cls) if not n.startswith("__"))
        write("  {}: methods={}".format(name, members))
        init = getattr(cls, "__init__", None)
        if init is not None:
            write("      __init__{}".format(_sig_of(init)))

    # **画面を塗っているのは HUD 側**（GAME.md §2.3）。
    # `app.to_display_buttons` は監視対象ではないので、
    # 「…」が HUD の中だけで起きているならこちらを見ないと捕まらない。
    # **画面に出ている文字は `hud.buttons` の各ウィジェットの `.text`** で、
    # `app.to_display_buttons` とは別物。
    def hud_texts(app):
        # 版3: ウィジェットはメインスレッドの上でだけ読む。
        if not _on_main_thread():
            return "<off main thread>"
        hud = ui_find_hud(app)
        if hud is None:
            return "<no hud>"
        out = {}
        widgets = getattr(hud, "buttons", None)
        if isinstance(widgets, (list, tuple)):
            out["buttons"] = [_widget_text(w) for w in list(widgets)[:8]]
        for name in ("status_label", "top_left_info_label",
                     "top_info_layout_normal_label"):
            text = _widget_text(getattr(hud, name, None))
            if text:
                out[name] = text[:40]
        send = getattr(hud, "text_send_button", None)
        if send is not None:
            out["send_disabled"] = getattr(send, "disabled", "?")
        return out or None

    def _widget_text(widget):
        text = getattr(widget, "text", None)
        return text if isinstance(text, str) else ""

    def ui_find_hud(app):
        try:
            return ui.find_hud(app)
        except Exception:
            return getattr(app, "hud", None)

    def wait_state(app):
        return "enabled={!r} input_disabled={!r} adding={!r} buttons={!r} hud={}".format(
            getattr(app, "is_button_enabled", "<missing>"),
            getattr(app, "text_input_disabled", "<missing>"),
            getattr(app, "is_adding_text", "<missing>"),
            list(getattr(app, "to_display_buttons", []) or [])[:6],
            hud_texts(app))

    def dump_hud(app):
        """HUD が持っている属性を一度だけ全部出す。

        ラベルを保持しているプロパティの名前が分からないと監視できない。
        推測で当てずに、実物の `vars(hud)` を見る。
        """
        hud = ui_find_hud(app)
        if hud is None:
            write("hud: not found")
            return
        write("hud: {}".format(type(hud).__name__))
        try:
            items = sorted(vars(hud).items())
        except Exception:
            write("  <vars() unavailable>")
            return
        write("  attrs({}): {}".format(len(items), ", ".join(n for n, _ in items)))
        for name, value in items:
            if isinstance(value, (list, tuple)) and value and \
                    all(isinstance(v, str) for v in value):
                write("    {:<32} = {!r}".format(name, list(value)[:8]))
            elif isinstance(value, str) and value:
                write("    {:<32} = {!r}".format(name, value[:60]))

    def snapshot():
        """起動時の写し。1プロセス1回・メインスレッド（版3）。

        印はゲームの中（app と player が揃っている）で取れたときにだけ付ける。
        タイトル画面の注入で印を付けると、そのプロセスでは二度と取れない。
        """
        if getattr(sys, SNAPSHOT_MARK, False):
            return
        app = find_app()
        if app is None or getattr(app, "player", None) is None:
            return
        setattr(sys, SNAPSHOT_MARK, True)

        write("=" * 78)
        write("quest snapshot (pid {})".format(os.getpid()))

        # --- 1. ボタン関係の今の姿。
        # ここが「選択肢の出し方」の出発点。
        write("  -- button/choice state --")
        for key, value in sorted(button_state(app).items()):
            write("    app.{:<28} = {}".format(key, value))
        # 画面を塗っている HUD の実体。
        # 待機表示（「…」）を探す手掛かり。
        dump_hud(app)
        # 会話中の選択肢は下の choice ボタンではなく HUD 上部に出ているらしい。
        # その文字列はここにある。
        write("    app.hud_top_info_texts       = {}".format(
            repr_value(getattr(app, "hud_top_info_texts", None))))
        write("    app.hud_top_info_label       = {}".format(
            repr_value(getattr(app, "hud_top_info_label", None))))
        for flag in ("in_conversation", "in_free_input", "in_action_in_conversation",
                     "is_button_enabled", "text_input_disabled"):
            write("    app.{:<28} = {!r}".format(flag, getattr(app, flag, "<missing>")))

        # --- 2. 受注経路に関わるクラスの実像。
        write("  -- quest classes --")
        for name in ("DisplayQuestChoice", "QuestChoiceManager", "QuestStartManager",
                     "QuestSearchManager", "QuestPhaseManager", "QuestEndManager",
                     "Quest", "World", "DisplayTalkChoice", "ConversationStartManager",
                     "ConversationPhaseManager", "ConversationEndManager"):
            dump_class(name)

        # --- 3. クエストの実体。world 側と world_dict 側の両方を見る。
        #        どちらが「正」なのかで、新規クエストを差し込む先が決まる。
        write("  -- quest storage --")
        world = getattr(app, "world", None)
        for attr in ("quests", "story_quests", "areas"):
            value = getattr(world, attr, "<missing>")
            write("    app.world.{:<22} = {}".format(attr, repr_value(value)))
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            write("    app.world_dict keys        = {}".format(list(world_dict)))
            write("    app.world_dict['index']    = {}".format(
                repr_value(world_dict.get("index"))))
            quests = world_dict.get("quests")
            if isinstance(quests, dict) and quests:
                write("    app.world_dict['quests']   = {} entries".format(len(quests)))
                statuses = {}
                for qid, quest in quests.items():
                    if isinstance(quest, dict):
                        cfg = quest.get("config")
                        status = cfg.get("status") if isinstance(cfg, dict) else None
                        statuses[status] = statuses.get(status, 0) + 1
                write("    config.status census       = {}".format(statuses))
                first = next(iter(quests))
                dump_quest(quests[first], "quests[{!r}]".format(first))

        # world 側が Quest インスタンスなら、その形も見る（dict とは限らない）。
        wquests = getattr(world, "quests", None)
        if isinstance(wquests, dict) and wquests:
            first = next(iter(wquests))
            dump_quest(wquests[first], "app.world.quests[{!r}]".format(first))

        # --- 4. 現在地とプレイヤーの進捗。
        # 受注可能な難易度の判定に要る。
        write("  -- player / location --")
        player = getattr(app, "player", None)
        area = getattr(player, "current_area", None)
        facility = getattr(player, "location", None)
        write("    current area   = {!r} (id={!r})".format(
            getattr(area, "name", None), getattr(area, "id", None)))
        write("    current facility = {!r} type={!r} owner={!r}".format(
            getattr(facility, "name", None), getattr(facility, "facility_type", None),
            getattr(facility, "owner", None)))
        write("    app.current_quest_data = {}".format(
            repr_value(getattr(app, "current_quest_data", None))))
        write("    app.quest_log          = {}".format(
            repr_value(getattr(app, "quest_log", None))))
        write("    app.highest_cleared_quest_difficulty = {!r}".format(
            getattr(app, "highest_cleared_quest_difficulty", None)))

        # --- 5. ゲーム自身のヘルパを実データで呼ぶ（副作用の無い参照関数）。
        #        「この土地で今どの難易度の依頼が出ているか」がここで分かる。
        #        GAME.md §3「純粋関数は総当たりで定義域を割り出す」と同じ手。
        functions = sys.modules.get("scripts.functions")
        if functions is not None and area is not None and world is not None:
            for fname, args in (("get_quest_difficulties", (area, world)),
                                ("get_active_quest_difficulties", (area, world))):
                fn = getattr(functions, fname, None)
                if fn is None:
                    write("    {}: missing".format(fname))
                    continue
                try:
                    write("    {}(area, world) -> {}".format(
                        fname, repr_value(fn(*args))))
                except Exception as exc:
                    write("    {}(area, world) !! {}: {}".format(
                        fname, type(exc).__name__, exc))
            reward = getattr(functions, "get_quest_reward", None)
            if reward is not None:
                try:
                    write("    get_quest_reward(1/10/30) -> {} / {} / {}".format(
                        reward(1), reward(10), reward(30)))
                except Exception as exc:
                    write("    get_quest_reward !! {}: {}".format(type(exc).__name__, exc))

    def guarded_snapshot():
        try:
            snapshot()
        except Exception:
            ctx.log_exc("quest probe: snapshot failed")

    if SNAPSHOT_ON_BOOT:
        # 版3: メインスレッドで取る（HUD のウィジェットを読むので）。
        ui.scheduler(ctx, "quest probe snapshot")(guarded_snapshot)

    # 記録の失敗で本体の呼び出しを妨げないための共通の囲い。
    def note(fn):
        try:
            fn()
        except Exception:
            ctx.log_exc("quest probe: record failed")

    # ==================================================================
    # 1. 選択肢ボタンの登録方法
    # ==================================================================
    # 「会話する」を押すと DisplayTalkChoice が NPC の一覧を出す。
    # これが作りたい「個別依頼の一覧」と同じ構造なので、
    # その前後の差分が設計図になる。
    def wrap_button_display(target, label):
        @ctx.wrap(target, required=False, safe=True)
        def _display(orig, self, *args, **kwargs):
            # 版3: 上限を先に見る。越えたら前の姿も組み立てない。
            if not sample(target):
                return orig(self, *args, **kwargs)
            app = getattr(self, "app", None) or find_app()
            box = {}
            note(lambda: box.update(before=button_state(app) if app is not None else {}))
            result = orig(self, *args, **kwargs)

            def record():
                write("-" * 78)
                write("{}.update_button_display() -> {}".format(label, repr_value(result)))
                if app is not None:
                    diff_buttons(app, label, box.get("before", {}))
            note(record)
            return result
        return _display

    wrap_button_display("__main__:DisplayTalkChoice.update_button_display",
                        "DisplayTalkChoice")
    wrap_button_display("__main__:DisplayQuestChoice.update_button_display",
                        "DisplayQuestChoice")

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, *args, **kwargs):
        if sample("refresh_choice_buttons"):
            note(lambda: write("refresh_choice_buttons(reset_page={!r}) buttons={}".format(
                frames.arg(args, kwargs, "reset_page", 0, False),
                repr_value(getattr(self, "buttons", None)))))
        return orig(self, *args, **kwargs)

    # ------------------------------------------- 待機表示（ボタンが「…」になる）
    # ゲーム自身の長い処理は、その間ボタンを「…」にしてプレイヤーを待たせる。
    # それをどこで立てているのかを突き止める。
    # `on_button_press` の中なのか `process_choice` の中なのかで、
    # 自前のフェーズを起こすときに素通しすべき経路が変わる（301_ は
    # on_button_press を横取りして process_choice を直接呼んでいるので、
    # 前者だと待機表示が出ない）。
    # 版3: 状態の変化を 20Hz で見張るスレッドは消した（決着は GAME.md §2.4）。
    @ctx.wrap("__main__:InstantaleApp.process_choice", required=False, safe=True)
    def process_choice_waitstate(orig, self, *args, **kwargs):
        if not sample("process_choice.waitstate"):
            return orig(self, *args, **kwargs)
        note(lambda: write("process_choice({}, {!r})".format(
            type(frames.arg(args, kwargs, "function", 0)).__name__,
            frames.arg(args, kwargs, "choice_text", 1, ""))))
        note(lambda: write("    before -> {}".format(wait_state(self))))
        result = orig(self, *args, **kwargs)
        note(lambda: write("    after  -> {}".format(wait_state(self))))
        return result

    @ctx.wrap("__main__:AreaMoveManager.show_loading_text", required=False, safe=True)
    def show_loading_text(orig, self, *args, **kwargs):
        # 版3: 上限を先に見る。越えたら前の姿も組み立てない。
        if not sample("show_loading_text"):
            return orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or find_app()
        box = {}
        note(lambda: box.update(before=wait_state(app) if app is not None else "<no app>"))
        result = orig(self, *args, **kwargs)

        def record():
            write("=" * 78)
            write("AreaMoveManager.show_loading_text()  <- ゲーム native の待機表示")
            write("    before -> {}".format(box.get("before")))
            write("    after  -> {}".format(wait_state(app) if app else "<no app>"))
        note(record)
        return result

    # 版3: 版2は `on_button_press` を2回包んでいた（待機表示の前後と、押された枠の spec）。
    # 同じ世代の層は両方残るので1回の押下で2層走っていた。1つにまとめた。
    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False, safe=True)
    def on_button_press(orig, self, *args, **kwargs):
        # 押された添字と、そのとき表示されていた文字列・spec の対応。
        # 押下 -> PhaseSpec -> マネージャ生成 -> process_choice の連鎖が読める。
        if not sample("on_button_press"):
            return orig(self, *args, **kwargs)
        button_index = frames.arg(args, kwargs, "button_index", 0)

        def before():
            buttons = getattr(self, "buttons", None)
            entry = None
            if isinstance(buttons, (list, tuple)) and isinstance(button_index, int):
                if 0 <= button_index < len(buttons):
                    entry = buttons[button_index]
            write("on_button_press(index={!r}) {}".format(
                button_index, describe_button(entry)))
            write("on_button_press({}) before -> {}".format(
                button_index, wait_state(self)))
        note(before)
        result = orig(self, *args, **kwargs)
        note(lambda: write("on_button_press({}) after  -> {}".format(
            button_index, wait_state(self))))
        return result

    # PhaseSpec が「どのクラスをどんな引数で呼ぶか」の唯一の記述。
    # クラス名ごとに1回だけ記録すれば、自前のボタンを足すときの雛形になる。
    # （例: '会話する' は DisplayTalkChoice を引数なし、NPC 選択は
    #   ConversationStartManager を character_id 1個で呼んでいるはず）
    # 版3: 控えを1プロセスで共有する（版2は注入のたびに白紙に戻っていた）。
    seen_specs = _process_store(SEEN_SPECS_ATTR, set)

    @ctx.wrap("__main__:PhaseSpec.__init__", required=False, safe=True)
    def phase_spec_init(orig, self, *args, **kwargs):
        try:
            cls_name = frames.arg(args, kwargs, "cls_name", 0)
            if isinstance(cls_name, str) and cls_name not in seen_specs:
                seen_specs.add(cls_name)
                write("PhaseSpec({!r}, args={}) -- {}(app, *args)".format(
                    cls_name, repr_value(frames.arg(args, kwargs, "args", 1)), cls_name))
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    # ==================================================================
    # 2. 受注経路
    # ==================================================================
    # 版3: 答えは出ている（GAME.md §2.2 / §2.9）。どれも1プロセスあたりの上限を付けた。
    @ctx.wrap("__main__:DisplayQuestChoice.__init__", required=False, safe=True)
    def display_quest_init(orig, self, *args, **kwargs):
        if sample("DisplayQuestChoice.__init__"):
            note(lambda: write("DisplayQuestChoice(app) constructed"))
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:DisplayQuestChoice.execute", required=False, safe=True)
    def display_quest_execute(orig, self, *args, **kwargs):
        if not sample("DisplayQuestChoice.execute"):
            return orig(self, *args, **kwargs)
        note(lambda: write("DisplayQuestChoice.execute({!r})".format(
            frames.arg(args, kwargs, "choice_text", 0))))
        result = orig(self, *args, **kwargs)
        note(lambda: write("  -> {}".format(repr_value(result))))
        return result

    @ctx.wrap("__main__:DisplayQuestChoice.get_active_quest_count", required=False, safe=True)
    def active_quest_count(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if sample("get_active_quest_count"):
            note(lambda: write("get_active_quest_count() -> {!r}".format(result)))
        return result

    @ctx.wrap("__main__:DisplayQuestChoice.generate_random_quest", required=False, safe=True)
    def generate_random_quest(orig, self, *args, **kwargs):
        # ここが「1件を世界に登録する」入口なら、前後で quests の件数が増える。
        if not sample("generate_random_quest", MAX_RARE_SAMPLES):
            return orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or find_app()
        box = {}

        def before():
            box["ids"] = _quest_ids(app)
            write("=" * 78)
            write("DisplayQuestChoice.generate_random_quest() start; quests={}".format(
                len(box["ids"])))
        note(before)
        result = orig(self, *args, **kwargs)

        def after():
            added = sorted(set(_quest_ids(app)) - set(box.get("ids", [])), key=str)
            write("generate_random_quest -> {}".format(repr_value(result)))
            write("  quest ids added: {}".format(added))
            for qid in added:
                dump_quest(_quest_of(app, qid), "new quest {!r}".format(qid))
        note(after)
        return result

    def _quests_of(app):
        world_dict = getattr(app, "world_dict", None) if app is not None else None
        if isinstance(world_dict, dict) and isinstance(world_dict.get("quests"), dict):
            return world_dict["quests"]
        quests = getattr(getattr(app, "world", None), "quests", None)
        return quests if isinstance(quests, dict) else {}

    def _quest_ids(app):
        return list(_quests_of(app))

    def _quest_of(app, qid):
        return _quests_of(app).get(qid)

    # --- 受注そのもの。
    # 300_ の process_choice 経由で起こせる形かを確かめる。
    # 版3: `quest_type` の語彙の総当たり（候補を並べて `QuestChoiceManager` を組む）は消した。
    # 答えは `'settlement_quest'`（GAME.md §2.9）。組み立てのたびに `318_` の包みが
    # 依頼の難易度と `state\` を書いていたので、読み取り専用の約束が破れていた。
    @ctx.wrap("__main__:QuestChoiceManager.__init__", required=False, safe=True)
    def quest_choice_init(orig, self, *args, **kwargs):
        # 例外も記録する。
        # 301_ がここで2回落ちた（`quest_type` の語彙違い）ので、
        # 誰がどんな値で呼んだのかが最も重要な記録になった。
        quest_type = frames.arg(args, kwargs, "quest_type", 1)
        quest_id = frames.arg(args, kwargs, "quest_id", 2)
        try:
            result = orig(self, *args, **kwargs)
        except Exception as exc:
            note(lambda: write("QuestChoiceManager(quest_type={!r}, quest_id={!r}) !! {}: {}"
                               .format(quest_type, quest_id, type(exc).__name__, exc)))
            raise
        if sample("QuestChoiceManager.__init__", MAX_RARE_SAMPLES):
            note(lambda: write("QuestChoiceManager(quest_type={!r} [{}], quest_id={!r} [{}]) ok"
                               .format(quest_type, type(quest_type).__name__,
                                       quest_id, type(quest_id).__name__)))
        return result

    @ctx.wrap("__main__:QuestChoiceManager.execute", required=False, safe=True)
    def quest_choice_execute(orig, self, *args, **kwargs):
        if not sample("QuestChoiceManager.execute", MAX_RARE_SAMPLES):
            return orig(self, *args, **kwargs)
        note(lambda: write(
            "QuestChoiceManager.execute({!r}) [{}] quest_type={!r} quest_id={!r}".format(
                frames.arg(args, kwargs, "choice_text", 0),
                threading.current_thread().name,
                getattr(self, "quest_type", "<none>"), getattr(self, "quest_id", "<none>"))))
        result = orig(self, *args, **kwargs)
        note(lambda: write("  -> {}".format(repr_value(result))))
        return result

    @ctx.wrap("__main__:QuestChoiceManager.quest_acceptance_choice", required=False, safe=True)
    def quest_acceptance_choice(orig, self, *args, **kwargs):
        # 版3: 版2は上限なしで、毎回前後のボタンの姿を組み立てていた。
        if not sample("quest_acceptance_choice", MAX_RARE_SAMPLES):
            return orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or find_app()
        box = {}
        note(lambda: box.update(before=button_state(app) if app is not None else {}))
        result = orig(self, *args, **kwargs)

        def record():
            write("QuestChoiceManager.quest_acceptance_choice() -> {}".format(
                repr_value(result)))
            if app is not None:
                diff_buttons(app, "quest_acceptance_choice", box.get("before", {}))
        note(record)
        return result

    @ctx.wrap("__main__:QuestStartManager.__init__", required=False, safe=True)
    def quest_start_init(orig, self, *args, **kwargs):
        if sample("QuestStartManager.__init__", MAX_RARE_SAMPLES):
            def record():
                quest_id = frames.arg(args, kwargs, "quest_id", 2)
                write("QuestStartManager(quest_type={!r}, quest_id={!r} [{}])".format(
                    frames.arg(args, kwargs, "quest_type", 1), quest_id,
                    type(quest_id).__name__))
            note(record)
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:QuestStartManager.start_quest", required=False, safe=True)
    def start_quest(orig, self, *args, **kwargs):
        if not sample("QuestStartManager.start_quest", MAX_RARE_SAMPLES):
            return orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or find_app()
        note(lambda: write("QuestStartManager.start_quest() quest_id={!r}".format(
            getattr(self, "quest_id", "<none>"))))
        result = orig(self, *args, **kwargs)

        def record():
            write("  -> {}".format(repr_value(result)))
            write("  app.current_quest_data = {}".format(
                repr_value(getattr(app, "current_quest_data", None))))
        note(record)
        return result

    @ctx.wrap("__main__:QuestSearchManager.search_quest", required=False, safe=True)
    def search_quest(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        if sample("QuestSearchManager.search_quest", MAX_RARE_SAMPLES):
            note(lambda: write("QuestSearchManager.search_quest() -> {}".format(
                repr_value(result))))
        return result

    # ==================================================================
    # 3. クエストの実体を作る経路
    # ==================================================================
    @ctx.wrap("__main__:World.generate_quests", required=False, safe=True)
    def generate_quests(orig, self, *args, **kwargs):
        if not sample("World.generate_quests", MAX_RARE_SAMPLES):
            return orig(self, *args, **kwargs)
        note(lambda: write("World.generate_quests() start"))
        result = orig(self, *args, **kwargs)

        def record():
            write("World.generate_quests -> {}".format(repr_value(result)))
            write("  world.quests now = {}".format(
                repr_value(getattr(self, "quests", None))))
        note(record)
        return result

    @ctx.wrap("__main__:World.generate_quest_area", required=False, safe=True)
    def world_generate_quest_area(orig, self, *args, **kwargs):
        if not sample("World.generate_quest_area", MAX_RARE_SAMPLES):
            return orig(self, *args, **kwargs)
        note(lambda: write("World.generate_quest_area(quest_value={}, next_area_id={!r})".format(
            repr_value(frames.arg(args, kwargs, "quest_value", 0)),
            frames.arg(args, kwargs, "next_area_id", 1))))
        result = orig(self, *args, **kwargs)
        note(lambda: write("  -> {}".format(repr_value(result))))
        return result

    @ctx.wrap("save_area_json:generate_quest_area", required=False, safe=True)
    def save_generate_quest_area(orig, *args, **kwargs):
        if not sample("save_area_json.generate_quest_area", MAX_RARE_SAMPLES):
            return orig(*args, **kwargs)
        # 引数の並び: world_dict, quest_value, next_area_id
        note(lambda: write("save_area_json.generate_quest_area(next_area_id={!r}) quest={}".format(
            frames.arg(args, kwargs, "next_area_id", 2),
            repr_value(frames.arg(args, kwargs, "quest_value", 1)))))
        result = orig(*args, **kwargs)
        note(lambda: write("  -> {}".format(repr_value(result))))
        return result

    # 引数の並び: world_overview, settlement_name, settlement_overview,
    # settlement_structure_description, area_description, quest_difficulty
    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False, safe=True)
    def random_quest_generator(orig, *args, **kwargs):
        if not sample("random_quest_generator", MAX_RARE_SAMPLES):
            return orig(*args, **kwargs)

        def before():
            write("=" * 78)
            write("random_quest_generator(settlement={!r}, difficulty={!r})".format(
                frames.arg(args, kwargs, "settlement_name", 1),
                frames.arg(args, kwargs, "quest_difficulty", 5)))
            write("  area_description = {}".format(
                repr_value(frames.arg(args, kwargs, "area_description", 4))))
        note(before)
        result = orig(*args, **kwargs)

        def after():
            write("  -> [{}] {}".format(type(result).__name__, repr_value(result)))
            if isinstance(result, dict):
                write("     keys = {}".format(list(result)))
        note(after)
        return result

    # ==================================================================
    # 4. 会話側。
    # どこに割り込めば選択肢を足せるか。
    # ==================================================================
    @ctx.wrap("__main__:ConversationPhaseManager.__init__", required=False, safe=True)
    def conversation_phase_init(orig, self, *args, **kwargs):
        if sample("ConversationPhaseManager.__init__"):
            # `self` の後ろは app, instruction, ... の順。
            note(lambda: write("ConversationPhaseManager(instruction={}) args={} kwargs={}".format(
                repr_value(frames.arg(args, kwargs, "instruction", 1)),
                repr_value(args[2:]), repr_value(kwargs))))
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ConversationPhaseManager.conversation_continued",
              required=False, safe=True)
    def conversation_continued(orig, self, *args, **kwargs):
        # 版3: 上限を先に見る。版2は会話の1ターンごとに前の姿を組み立てていた。
        if not sample("conversation_continued"):
            return orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or find_app()
        box = {}

        def before():
            box["before"] = button_state(app) if app is not None else {}
            write("-" * 78)
            write("ConversationPhaseManager.conversation_continued({!r})".format(
                frames.arg(args, kwargs, "choice_text", 0)))
        note(before)
        result = orig(self, *args, **kwargs)
        if app is not None:
            note(lambda: diff_buttons(app, "conversation_continued", box.get("before", {})))
        return result

    @ctx.wrap("__main__:ConversationStartManager.execute", required=False, safe=True)
    def conversation_start_execute(orig, self, *args, **kwargs):
        """会話が始まった直後のボタンの姿。ここが「依頼を受ける」を足す場所。

        会話中は自由入力が主で、
        `function_correspond_to_input` が「入力を送ったら何を呼ぶか」を持っている（PhaseSpec）。
        選択肢ボタンが同時に生きているのか、
        生きているなら何が並んでいるのかを確かめる。
        """
        app = getattr(self, "app", None) or find_app()
        result = orig(self, *args, **kwargs)
        if app is not None and sample("ConversationStartManager.execute"):
            def record():
                write("=" * 78)
                write("ConversationStartManager.execute({!r}) done; character_id={!r}".format(
                    frames.arg(args, kwargs, "choice_text", 0),
                    getattr(self, "character_id", "<none>")))
                for key, value in sorted(button_state(app).items()):
                    write("    app.{:<28} = {}".format(key, value))
                write("    app.in_conversation={!r} in_free_input={!r} "
                      "in_action_in_conversation={!r}".format(
                          getattr(app, "in_conversation", None),
                          getattr(app, "in_free_input", None),
                          getattr(app, "in_action_in_conversation", None)))
            note(record)
        return result

    # ------------------------------------------------- 会話中の上部ボタン
    # 「行動」への切り替えが画面のどこにあるのかを突き止める。
    # 会話中の選択肢は下のボタン列ではなく **HUD 上部の info レイアウト**に
    # 出ているらしく、その文字列は `app.hud_top_info_texts` にある。
    # 割り当ては次の2つで行われる:
    #   set_top_info_layout_conversation_button_callback(callbacks_left, callbacks_right)
    #   set_top_info_layout_action_in_conversation_button_callback(button_2, button_3)
    # どのコールバックが `toggle_to_action_in_conversation` なのかが分かれば、
    # 「画面のどのボタンを押せば行動メニューになるか」がそのまま出る。
    def describe_callback(value):
        if value is None:
            return "None"
        if isinstance(value, (list, tuple)):
            return "[{}]".format(", ".join(describe_callback(v) for v in value))
        name = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
        if name is None:
            # bound method や functools.partial でも中身を辿る
            func = getattr(value, "__func__", None) or getattr(value, "func", None)
            name = getattr(func, "__qualname__", None) or getattr(func, "__name__", None)
        return "{} <{}>".format(name or "?", type(value).__name__)

    def wrap_top_info_callbacks(target, label, names):
        # 版3: 版2は上限なしで、会話のたびに4〜5行出ていた（1か月で約 3,200 行）。
        @ctx.wrap(target, required=False, safe=True)
        def _set(orig, self, *args, **kwargs):
            if sample(target):
                def record():
                    write("=" * 78)
                    write("{}:".format(label))
                    write("    {} = {}".format(names[0], describe_callback(
                        frames.arg(args, kwargs, names[0].strip(), 0))))
                    write("    {} = {}".format(names[1], describe_callback(
                        frames.arg(args, kwargs, names[1].strip(), 1))))
                    app = find_app()
                    if app is not None:
                        write("    app.hud_top_info_texts = {}".format(
                            repr_value(getattr(app, "hud_top_info_texts", None))))
                        write("    app.hud_top_info_label = {}".format(
                            repr_value(getattr(app, "hud_top_info_label", None))))
                note(record)
            return orig(self, *args, **kwargs)
        return _set

    wrap_top_info_callbacks(
        "scripts.hud.new_hud:InstanTaleHUD.set_top_info_layout_conversation_button_callback",
        "set_top_info_layout_conversation_button_callback",
        ("callbacks_left ", "callbacks_right"))
    wrap_top_info_callbacks(
        "scripts.hud.new_hud:InstanTaleHUD."
        "set_top_info_layout_action_in_conversation_button_callback",
        "set_top_info_layout_action_in_conversation_button_callback",
        ("callbacks_button_2", "callbacks_button_3"))

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.update_top_info_texts",
              required=False, safe=True)
    def update_top_info_texts(orig, self, *args, **kwargs):
        # 上部ボタンの文字列が変わるたびに記録する。
        # 会話に入った瞬間に何が並ぶかが分かれば、
        # 「この文字のボタン」と言える。
        if sample("update_top_info_texts"):
            note(lambda: write("hud top info texts -> {}".format(
                repr_value(frames.arg(args, kwargs, "value", 1)))))
        return orig(self, *args, **kwargs)

    # 会話中の「行動」メニュー。
    # `app.in_action_in_conversation` を立てて自由入力から選択肢に切り替える経路で、
    # 雇用・戦闘・買い物がここに並ぶ。
    # 「依頼を受ける」を足すならここが本来の居場所。
    # 並んでいる spec を見る。
    def wrap_action_toggle(target, label):
        @ctx.wrap(target, required=False, safe=True)
        def _toggle(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            if sample(target):
                def record():
                    write("=" * 78)
                    write("{} done".format(label))
                    for key, value in sorted(button_state(self).items()):
                        write("    app.{:<28} = {}".format(key, value))
                    write("    in_action_in_conversation={!r} in_conversation={!r}".format(
                        getattr(self, "in_action_in_conversation", None),
                        getattr(self, "in_conversation", None)))
                note(record)
            return result
        return _toggle

    wrap_action_toggle("__main__:InstantaleApp.toggle_to_action_in_conversation",
                       "toggle_to_action_in_conversation")
    wrap_action_toggle("__main__:InstantaleApp.toggle_from_action_in_conversation",
                       "toggle_from_action_in_conversation")

    @ctx.wrap("__main__:ConversationEndManager.__init__", required=False, safe=True)
    def conversation_end_init(orig, self, *args, **kwargs):
        if sample("ConversationEndManager.__init__"):
            # `self` の後ろは app, in_conversation_id, finisher, end_text の順。
            note(lambda: write("ConversationEndManager(in_conversation_id={!r}, finisher={!r}, "
                               "end_text={})".format(
                                   frames.arg(args, kwargs, "in_conversation_id", 1),
                                   frames.arg(args, kwargs, "finisher", 2),
                                   repr_value(frames.arg(args, kwargs, "end_text", 3)))))
        return orig(self, *args, **kwargs)

    # NPC が「この土地の依頼」を語るときに使う知識。
    # 会話から依頼を作るとき、
    # ゲーム自身が何をクエスト情報として渡しているかがそのまま雛形になる。
    @ctx.wrap("scripts.llm.context_manager:get_quest_data_for_conversation",
              required=False, safe=True)
    def quest_data_for_conversation(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        if sample("get_quest_data_for_conversation"):
            note(lambda: write("get_quest_data_for_conversation -> [{}] {}".format(
                type(result).__name__, repr_value(result))))
        return result

    ctx.log("quest flow probe log: {}".format(log_path))
