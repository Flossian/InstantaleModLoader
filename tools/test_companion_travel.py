# -*- coding: utf-8 -*-
"""`1308_companion_travel` の検証。ゲーム不要。

    python tools/test_companion_travel.py

**偽のゲームを1つ組んで、本物と同じ経路で押下を流す。** 押されたボタンは
`getattr(__main__, cls_name)(app, *args)` に組み立てられ `process_choice` に
渡る（GAME.md §3）。この形を守っているので、「押下を横取りして spec を
そのまま再発行する」が**原形のまま**行われたかどうかを、ここで確かめられる。

見ているのは振る舞いだけ:

- パーティに居る相手にはボタンを出さない（ゲームの機能と二重にしない）
- 承諾で同行リストに入り、**そのときの会話が控えられる**（継続判定の材料）
- 拒否では入らない。ボタンのラベルは同行状態で入れ替わる
- 施設間の移動は判定せずに移送する
- 土地跨ぎは押下を横取りし、全員が継続なら **spec が原形のまま再発行される**
- 誰かが拒否したら「置いて行く」「やめる」が出て、やめると元の選択肢に戻る
- **判定語が読めないとき、合意は拒否・継続は継続に倒れる**（安全側が逆）
- 置き先が引けなければ移送しない（`302_` の教訓）
- 世界が違えば同行リストは混ざらない
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

MODS_DIR = os.path.join(_ROOT, "runtime", "mods")


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_companion_travel")

ACCEPT = "承諾\n「ああ、付いて行こう」"
REFUSE = "拒否\n「悪いが、ここを離れられない」"
GIBBERISH = "……（黙ったまま何も答えなかった）"


# ==========================================================================
# 偽のゲーム
# ==========================================================================
class Spec(object):
    """`PhaseSpec(cls_name, args)`。押されると `getattr(__main__, cls_name)` を組む。"""

    def __init__(self, cls_name, args=None):
        self.cls_name = cls_name
        self.args = list(args or [])

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


# `ui.Screen.make_spec` は `__main__` から名前で引く。
PhaseSpec = Spec


class JustSetButtonToNormalPhase(object):
    """自前ボタンが持つ無害な spec の実体（mod 無しで押されても選択肢が戻るだけ）。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        return None


class ConversationEndManager(object):
    def __init__(self, app, in_conversation_id, finisher, end_text):
        self.app = app
        self.in_conversation_id = in_conversation_id

    def execute(self, choice_text):
        self.app.in_conversation = False


class Facility(object):
    def __init__(self, facility_id, name, facility_type="inn"):
        self.id = facility_id
        self.name = name
        self.facility_type = facility_type


class Node(object):
    def __init__(self, node_id, facilities):
        self.id = node_id
        self.facilities = {f.id: f for f in facilities}


class Area(object):
    def __init__(self, area_id, name, nodes):
        self.id = area_id
        self.name = name
        self.nodes = {n.id: n for n in nodes}


class Character(object):
    def __init__(self, character_id, name, location=None, node=None, area=None):
        self.id = character_id
        self.name = name
        self.profile = "{}の来歴".format(name)
        self.personality = "無口"
        self.speech_style = "ぶっきらぼう"
        self.job = "傭兵"
        self.relationship = {"player": ["顔見知り"]}
        self.emotion_scores = {"affinity": 40}
        self.current_log = ["<会話>酒場で身の上を話した"]
        self.life_log = ["<出来事>故郷を焼かれた"]
        self.location = location
        self.current_node = node
        self.current_area = area


class World(object):
    def __init__(self, name, areas, characters):
        self.name = name
        self.worldview = "灰の空の下"
        self.areas = {a.id: a for a in areas}
        self.characters = {c.id: c for c in characters}


class AreaMoveCofirmation(object):
    """行き先の一覧（綴りはゲームのまま）。**文言が行き先の名前になる。**"""

    def __init__(self, app, target_area_id):
        self.app = app
        self.target_area_id = target_area_id

    def execute(self, choice_text):
        self.app.buttons = [
            {"text": "徒歩(3ヵ月)",
             "spec": Spec("AreaMoveManager", [self.target_area_id, "on_foot"])},
            {"text": "馬車(1000G)",
             "spec": Spec("AreaMoveManager", [self.target_area_id, "coach"])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)


class AreaMoveManager(object):
    """土地移動。**組み立てられた引数をそのまま控える**（原形の検査に使う）。

    到着が確定するのは `method_1` の中（実測・2026-07-30）。`execute` はそれを
    呼んで戻るだけなので、偽物も同じ形にしてある。
    """

    built = []

    def __init__(self, app, target_area_id, mode):
        self.app = app
        self.target_area_id = target_area_id
        self.mode = mode
        AreaMoveManager.built.append((target_area_id, mode))

    def execute(self, choice_text):
        self.method_1()
        return "arrived"

    def method_1(self):
        self.app.arrive(self.target_area_id)


class MovePhaseManager(object):
    """施設間の移動。"""

    def __init__(self, app, connected_node_id, facility_move_to_id, area_id=None):
        self.app = app
        self.facility_move_to_id = facility_move_to_id

    def move_phase(self):
        area = self.app.world.areas[self.app.player.current_area]
        for node in area.nodes.values():
            facility = node.facilities.get(self.facility_move_to_id)
            if facility is not None:
                self.app.player.location = facility
                self.app.player.current_node = node
        return "moved"


class InstantaleApp(object):
    def __init__(self, world, player):
        self.world = world
        self.player = player
        self.world_dict = {"world_data": {"world_name": world.name}}
        self.game_variables = {"party": ["player"]}
        self.buttons = []
        self.to_display_buttons = []
        self.display_button_map = []
        self.texts = []
        self.moves = []
        self.passed_through = []
        self.in_conversation = True
        self.is_button_enabled = True
        self.is_adding_text = False
        self.is_popup_window_opened = False
        self.current_conversation_history = [
            {"role": "user", "content": "お前の腕を見込んで頼みがある"},
            {"role": "assistant", "content": "「……聞くだけは聞こう」"},
        ]

    # -- ゲーム自身の振る舞い ---------------------------------------------
    def add_text(self, text):
        self.texts.append(text)

    def refresh_choice_buttons(self, reset_page=False):
        self.to_display_buttons = [entry.get("text", "") for entry in self.buttons]
        self.display_button_map = list(range(len(self.buttons)))

    def process_choice(self, phase, choice_text):
        return phase.execute(choice_text)

    def on_button_press(self, button_index):
        """本物と同じ組み立て方（GAME.md §3）。**引数はボタンから写すだけ。**"""
        entry = self.buttons[self.display_button_map[button_index]]
        self.passed_through.append(entry.get("text"))
        spec = entry["spec"]
        cls = getattr(sys.modules["__main__"], spec.cls_name)
        return self.process_choice(cls(self, *spec.args), entry.get("text"))

    def move_npc_to_facility(self, character_id, character_instance,
                             target_facility, target_node=None,
                             register_facility=True):
        self.moves.append((character_id, target_facility.id))
        character_instance.location = target_facility
        character_instance.current_node = target_node
        character_instance.current_area = self.player.current_area

    def arrive(self, area_id):
        area = self.world.areas[area_id]
        node = list(area.nodes.values())[0]
        self.player.current_area = area_id
        self.player.current_node = node
        self.player.location = list(node.facilities.values())[0]


# ==========================================================================
# 偽の kivy と偽のゲームモジュール
# ==========================================================================
class Clock(object):
    """`schedule_once` / `schedule_interval` を貯めておいて `pump()` で流す。"""

    once = []
    intervals = []

    @classmethod
    def schedule_once(cls, callback, delay=0):
        cls.once.append(callback)

    @classmethod
    def schedule_interval(cls, callback, poll):
        cls.intervals.append(callback)

    @classmethod
    def reset(cls):
        cls.once = []
        cls.intervals = []

    @classmethod
    def pump(cls, rounds=6):
        for _ in range(rounds):
            pending, cls.once = cls.once, []
            for callback in pending:
                callback(0)
            for callback in list(cls.intervals):
                if callback(0) is False:
                    cls.intervals.remove(callback)


class Llm(object):
    """`send_request_with_no_structure` の代わり。**渡された文面も控える。**"""

    answers = {}
    prompts = []

    @classmethod
    def send(cls, manager_name, messages, max_tokens=None, timeout=None):
        cls.prompts.append((manager_name, messages))
        queue = cls.answers.get(manager_name)
        if isinstance(queue, list) and queue:
            return queue.pop(0)
        return queue if isinstance(queue, str) else ""

    @classmethod
    def reset(cls, answers=None):
        cls.answers = dict(answers or {})
        cls.prompts = []

    @classmethod
    def last_prompt(cls, manager_name):
        for name, messages in reversed(cls.prompts):
            if name == manager_name:
                return "\n".join(m.get("content", "") for m in messages)
        return ""


def install_modules():
    """`sys.modules` に偽物を置く。`ui` も mod も**名前で**掴みに来るだけ。"""
    kivy = types.ModuleType("kivy")
    clock = types.ModuleType("kivy.clock")
    clock.Clock = Clock
    app_module = types.ModuleType("kivy.app")

    class App(object):
        running = None

        @staticmethod
        def get_running_app():
            return App.running

    app_module.App = App
    sys.modules.update({"kivy": kivy, "kivy.clock": clock, "kivy.app": app_module})

    llm = types.ModuleType("scripts.llm.request_llm_inference_llama_cpp_completion")
    llm.send_request_with_no_structure = Llm.send

    context = types.ModuleType("scripts.llm.context_manager")
    context.conversation_history_to_text = lambda history, player, npc: "\n".join(
        "{}: {}".format(turn.get("role"), turn.get("content")) for turn in history)
    context.get_life_log_text = lambda app, character: "\n".join(
        getattr(character, "life_log", []) or [])

    functions = types.ModuleType("scripts.functions")
    functions.document_emotion_scores = lambda scores: str(scores)

    sys.modules.update({
        "scripts.llm.request_llm_inference_llama_cpp_completion": llm,
        "scripts.llm.context_manager": context,
        "scripts.functions": functions,
    })
    return App


APP_HOLDER = install_modules()


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, basename):
        return os.path.join(self.out_dir, basename)

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    def wrap(self, target, required=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


def load_mod():
    spec = importlib.util.spec_from_file_location("companion_travel_under_test",
                                                  MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = load_mod()


# 筋書きごとに `apply` をやり直すので、**掛ける前に必ず素の実装へ戻す**。
# 戻さないと前の筋書きのフック（既に消えた一時フォルダを指す控えを掴んでいる）が
# 内側に残り、後の筋書きの選択肢を勝手に書き換える。
PRISTINE = [(InstantaleApp, "refresh_choice_buttons"),
            (InstantaleApp, "on_button_press"),
            (MovePhaseManager, "move_phase"),
            (AreaMoveManager, "method_1")]
ORIGINALS = [(owner, name, getattr(owner, name)) for owner, name in PRISTINE]


def unbind_all():
    for owner, name, original in ORIGINALS:
        setattr(owner, name, original)


def bind(ctx, target, owner, name):
    """控えたフックを実際のクラスに掛ける（ローダの `patch` と同じ形）。"""
    hook = ctx.hooks.get(target)
    if hook is None:
        raise AssertionError("hook not registered: {}".format(target))
    original = getattr(owner, name)

    def patched(self, *args, **kwargs):
        return hook(original, self, *args, **kwargs)

    setattr(owner, name, patched)
    return original


# ==========================================================================
# 検査の土台
# ==========================================================================
class Run(object):
    """1つの筋書き。偽のゲームを組んで mod を適用したところまで。"""

    def __init__(self, world_name="灰都", party=None, answers=None,
                 companion_ids=(), facilities=True, seed_world=None):
        self.tmp = tempfile.mkdtemp(prefix="companion_test_")
        unbind_all()
        Clock.reset()
        Llm.reset(answers)
        AreaMoveManager.built = []

        inn = Facility("10", "灰の宿", "inn")
        gate = Facility("11", "灰都 - 出口", "exit")
        far = Facility("20", "嘆きの村 - 入口", "entrance")
        home = Area("1", "灰都", [Node("n1", [inn, gate] if facilities else [])])
        away = Area("2", "嘆きの村", [Node("n2", [far])])

        self.npc = Character("77", "傭兵ガロ", location=inn, node=home.nodes["n1"],
                             area="1")
        self.other = Character("88", "薬売りミラ", location=inn,
                               node=home.nodes["n1"], area="1")
        player = Character("player", "冒険者", location=inn if facilities else None,
                           node=home.nodes["n1"], area="1")
        world = World(world_name, [home, away], [self.npc, self.other, player])

        self.app = InstantaleApp(world, player)
        self.app.player.current_area = "1"
        if party:
            self.app.game_variables["party"] = ["player"] + list(party)
        APP_HOLDER.running = self.app

        self.ctx = Ctx(self.tmp)
        if companion_ids:
            self.seed(seed_world or world_name, companion_ids)
        MOD.apply(self.ctx)

        bind(self.ctx, "__main__:InstantaleApp.refresh_choice_buttons",
             InstantaleApp, "refresh_choice_buttons")
        bind(self.ctx, "__main__:InstantaleApp.on_button_press",
             InstantaleApp, "on_button_press")
        bind(self.ctx, "__main__:MovePhaseManager.move_phase",
             MovePhaseManager, "move_phase")
        bind(self.ctx, "__main__:AreaMoveManager.method_1",
             AreaMoveManager, "method_1")

    def seed(self, world_name, ids):
        """既に同行している状態から始める。"""
        data = {world_name: {}}
        for npc_id in ids:
            data[world_name][npc_id] = {
                "name": "傭兵ガロ", "since": "2026-07-30T00:00:00",
                "agreement": "user: 一緒に来てくれ\nassistant: 「ああ」",
                "reply": "「ああ、付いて行こう」"}
        with open(os.path.join(self.tmp, "companions.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    # -- 画面 -------------------------------------------------------------
    def conversation(self, npc_id="77"):
        """会話画面の選択肢を組み直す（ゲームが並べるのは終了ボタンだけ）。"""
        self.app.in_conversation = True
        self.app.buttons = [{"text": "会話を終了する",
                             "spec": Spec("ConversationEndManager",
                                          [npc_id, "user", "<行動: 会話を終了する>"])}]
        self.app.refresh_choice_buttons(reset_page=True)
        return self.labels()

    def travel_screen(self, area_id="2", destination="東京"):
        """行き先を選んで徒歩／馬車の画面まで進む（実測どおりの2段）。

        行き先の表示名（`destination`）は**わざとエリア名と変えてある**。実機でも
        一覧のボタンは '東京' で、着いた先の施設は '鉄鎖の町 - 入口' だった ―
        判定に載るのが**押されたボタンの文言**であることをここで確かめる。
        """
        self.app.in_conversation = False
        self.app.buttons = [
            {"text": destination, "spec": Spec("AreaMoveCofirmation", [area_id])},
            {"text": "やめる", "spec": Spec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)
        self.press(destination)
        return self.labels()

    def labels(self):
        return [entry.get("text") for entry in self.app.buttons]

    def press(self, label):
        for index, entry in enumerate(self.app.buttons):
            if entry.get("text") == label:
                self.app.on_button_press(index)
                Clock.pump()
                return True
        raise AssertionError("no such button: {!r} in {}".format(label, self.labels()))

    def companions(self):
        path = os.path.join(self.tmp, "companions.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def cleanup(self):
        APP_HOLDER.running = None
        shutil.rmtree(self.tmp, ignore_errors=True)


RESULTS = {"pass": 0, "fail": 0}


def check(condition, label):
    if condition:
        RESULTS["pass"] += 1
    else:
        RESULTS["fail"] += 1
        print("  FAIL  {}".format(label))


def scenario(fn):
    print("- {}".format(fn.__doc__.strip().splitlines()[0]))
    run = fn()
    if isinstance(run, Run):
        check(not run.ctx.errors, "例外が記録された: {}".format(run.ctx.errors))
        run.cleanup()


# ==========================================================================
# 筋書き
# ==========================================================================
def test_party_member_gets_no_button():
    """パーティに居る相手にはボタンを出さない"""
    run = Run(party=["77"])
    labels = run.conversation("77")
    check(labels == ["会話を終了する"], "パーティの相手にボタンが出た: {}".format(labels))
    return run


LLAMA_CPP = "scripts.llm.request_llm_inference_llama_cpp_completion"
ANY_SERVER = "scripts.llm.request_llm_inference_any_server"


def with_llm_backend(backend):
    """推論モジュールを一時的に差し替える。実機では片方しか載らない。"""
    saved = {name: sys.modules.get(name) for name in (LLAMA_CPP, ANY_SERVER)}
    for name in (LLAMA_CPP, ANY_SERVER):
        sys.modules.pop(name, None)
    if backend is not None:
        module = types.ModuleType(backend)
        module.send_request_with_no_structure = Llm.send
        sys.modules[backend] = module
    return saved


def restore_llm_backend(saved):
    for name in (LLAMA_CPP, ANY_SERVER):
        sys.modules.pop(name, None)
    for name, module in saved.items():
        if module is not None:
            sys.modules[name] = module


def test_offer_via_any_server():
    """外部 API スロット（any_server のみ）でも承諾できる"""
    saved = with_llm_backend(ANY_SERVER)
    try:
        run = Run(answers={"mod_companion_join": [ACCEPT]})
        run.conversation("77")
        run.press("同行を持ちかける")
        check("77" in run.companions().get("灰都", {}),
              "any_server 経路で同行できない: {}".format(run.companions()))
        check(any("付いて行こう" in text for text in run.app.texts),
              "any_server 経路のセリフが出ない: {}".format(run.app.texts))
        return run
    finally:
        restore_llm_backend(saved)


def test_offer_llm_unavailable():
    """推論モジュールが無いとき、偽の拒否セリフではなく失敗文言を出す"""
    saved = with_llm_backend(None)
    try:
        run = Run(answers={"mod_companion_join": [ACCEPT]})
        run.conversation("77")
        run.press("同行を持ちかける")
        check(run.companions().get("灰都", {}) == {},
              "LLM 無しなのに同行した: {}".format(run.companions()))
        check(any("切り出せない" in text for text in run.app.texts),
              "失敗文言が出ていない: {}".format(run.app.texts))
        check(not any("離れるわけにはいかない" in text for text in run.app.texts),
              "偽の拒否セリフが出た: {}".format(run.app.texts))
        return run
    finally:
        restore_llm_backend(saved)


def test_offer_accepted():
    """承諾で同行リストに入り、そのときの会話が控えられる"""
    run = Run(answers={"mod_companion_join": [ACCEPT]})
    labels = run.conversation("77")
    check("同行を持ちかける" in labels, "ボタンが出ない: {}".format(labels))
    check(labels[-1] == "会話を終了する", "終了ボタンが最後でない: {}".format(labels))

    run.press("同行を持ちかける")
    bucket = run.companions().get("灰都", {})
    check("77" in bucket, "同行リストに入っていない: {}".format(bucket))
    record = bucket.get("77", {})
    check("腕を見込んで" in record.get("agreement", ""),
          "合意時の会話が控えられていない: {!r}".format(record.get("agreement")))
    check(record.get("reply", "").startswith("「"),
          "セリフが控えられていない: {!r}".format(record.get("reply")))
    check(any("付いて行こう" in text for text in run.app.texts),
          "承諾のセリフが表示されていない: {}".format(run.app.texts))
    prompt = Llm.last_prompt("mod_companion_join")
    check("腕を見込んで" in prompt, "書き起こしがプロンプトに載っていない")
    check("顔見知り" in prompt, "関係がプロンプトに載っていない")
    # 会話を閉じてから持ちかけ直すと、経緯はここにしか残らない。
    check("酒場で身の上を話した" in prompt, "直近の会話の要約が載っていない")
    check("故郷を焼かれた" in prompt, "ライフログが載っていない")

    # ラベルは同行状態で入れ替わる。
    run.conversation("77")
    check("同行を解く" in run.labels(), "ラベルが切り替わらない: {}".format(run.labels()))
    return run


def test_offer_refused():
    """拒否では同行リストに入らない"""
    run = Run(answers={"mod_companion_join": [REFUSE]})
    run.conversation("77")
    run.press("同行を持ちかける")
    check(run.companions().get("灰都", {}) == {},
          "拒否なのに入っている: {}".format(run.companions()))
    check(any("離れられない" in text for text in run.app.texts),
          "拒否のセリフが表示されていない: {}".format(run.app.texts))
    return run


def test_companion_opening_is_not_a_reunion():
    """同行者の第一声だけを道中の会話として読み替える"""
    run = Run(companion_ids=["77"])
    seen = []

    def starter(messages, *args, **kwargs):
        seen.append(messages)
        return "第一声"

    start_init = run.ctx.hooks["__main__:ConversationStartManager.__init__"]
    opening = run.ctx.hooks["scripts.llm.llm_manager:conversation_starter"]
    manager = object()
    start_init(lambda *args, **kwargs: None, manager, run.app, "77")
    opening(starter, [{"role": "user", "content": "<行動: 話しかける>"}])
    companion_prompt = seen[-1][-1]["content"]
    check("現在'冒険者'に同行しており" in companion_prompt,
          "同行の文脈が載っていない: {!r}".format(companion_prompt))
    check("『また会えた』『久しぶり』などと扱わず" in companion_prompt,
          "再会表現の抑止が無い: {!r}".format(companion_prompt))

    start_init(lambda *args, **kwargs: None, manager, run.app, "88")
    opening(starter, [{"role": "user", "content": "<行動: 話しかける>"}])
    check(seen[-1][-1]["content"] == "<行動: 話しかける>",
          "同行していない相手の第一声まで変わった: {!r}".format(
              seen[-1][-1]["content"]))
    return run


def test_release():
    """同行を解くとリストから外れる"""
    run = Run(companion_ids=["77"], answers={"mod_companion_leave": ["「達者でな」"]})
    run.conversation("77")
    check("同行を解く" in run.labels(), "解除のボタンが出ない: {}".format(run.labels()))
    run.press("同行を解く")
    check(run.companions().get("灰都", {}) == {},
          "解除されていない: {}".format(run.companions()))
    check(any("達者" in text for text in run.app.texts),
          "別れのセリフが出ていない: {}".format(run.app.texts))
    # 道中の記憶を渡さないと、職務上の関係だけで別れの言葉が書かれる。
    prompt = Llm.last_prompt("mod_companion_leave")
    check("酒場で身の上を話した" in prompt, "直近の会話の要約が載っていない")
    check("故郷を焼かれた" in prompt, "ライフログが載っていない")
    check("口調" in prompt, "口調の指示が無い")
    return run


def test_facility_move_relocates():
    """施設間の移動では判定せずに移送する"""
    run = Run(companion_ids=["77"])
    MovePhaseManager(run.app, "n1", "11").move_phase()
    Clock.pump()
    check(run.npc.location is not None and run.npc.location.id == "11",
          "同行者が移動していない: {}".format(getattr(run.npc.location, "id", None)))
    check(Llm.prompts == [], "施設間の移動で LLM を呼んでいる: {}".format(Llm.prompts))
    return run


def test_area_move_all_follow():
    """土地跨ぎで押下を横取りし、全員継続なら spec が原形のまま再発行される"""
    run = Run(companion_ids=["77"], answers={"mod_companion_continue": [ACCEPT]})
    run.travel_screen("2")
    run.press("徒歩(3ヵ月)")
    check(AreaMoveManager.built == [("2", "on_foot")],
          "spec が原形で再発行されていない: {}".format(AreaMoveManager.built))
    check(run.app.player.current_area == "2", "移動していない")
    check(run.npc.location is not None and run.npc.location.id == "20",
          "同行者が付いて来ていない: {}".format(getattr(run.npc.location, "id", None)))
    prompt = Llm.last_prompt("mod_companion_continue")
    check("東京" in prompt,
          "行き先が**押されたボタンの文言**で載っていない（エリア名に落ちた）")
    check("徒歩(3ヵ月)" in prompt, "移動手段がプロンプトに載っていない")
    check("故郷を焼かれた" in prompt, "ライフログがプロンプトに載っていない")
    return run


def test_area_move_leave_behind():
    """拒否されたら置いて行く／やめるが出て、置いて行けば旅は続く"""
    run = Run(companion_ids=["77"], answers={"mod_companion_continue": [REFUSE]})
    run.travel_screen("2")
    run.press("徒歩(3ヵ月)")
    check(AreaMoveManager.built == [], "拒否なのに出発した: {}".format(AreaMoveManager.built))
    check(run.labels() == ["置いて行く", "旅をやめる"],
          "選択肢が出ていない: {}".format(run.labels()))

    run.press("置いて行く")
    check(AreaMoveManager.built == [("2", "on_foot")],
          "置いて行った後に出発していない: {}".format(AreaMoveManager.built))
    check(run.companions().get("灰都", {}) == {},
          "置いて行ったのにリストに残っている: {}".format(run.companions()))
    check(run.npc.location.id == "10", "置いて行った相手が動いた")
    return run


def test_area_move_cancel():
    """やめると元の選択肢に戻り、同行者はそのまま残る"""
    run = Run(companion_ids=["77"], answers={"mod_companion_continue": [REFUSE]})
    run.travel_screen("2")
    run.press("徒歩(3ヵ月)")
    run.press("旅をやめる")
    check(run.labels() == ["徒歩(3ヵ月)", "馬車(1000G)"],
          "元の選択肢に戻っていない: {}".format(run.labels()))
    check(AreaMoveManager.built == [], "やめたのに出発した: {}".format(AreaMoveManager.built))
    check("77" in run.companions().get("灰都", {}),
          "やめただけで同行が解けた: {}".format(run.companions()))
    return run


def test_unreadable_verdicts():
    """判定語が読めないとき、合意は拒否・継続は継続に倒れる"""
    run = Run(answers={"mod_companion_join": [GIBBERISH]})
    run.conversation("77")
    run.press("同行を持ちかける")
    check(run.companions().get("灰都", {}) == {},
          "読めない返事で同行させた: {}".format(run.companions()))
    run.cleanup()

    run = Run(companion_ids=["77"], answers={"mod_companion_continue": [GIBBERISH]})
    run.travel_screen("2")
    run.press("徒歩(3ヵ月)")
    check(AreaMoveManager.built == [("2", "on_foot")],
          "読めない返事で旅を止めた: {}".format(AreaMoveManager.built))
    check("77" in run.companions().get("灰都", {}),
          "読めない返事で同行者を落とした: {}".format(run.companions()))
    return run


def test_no_destination():
    """置き先が引けなければ移送しない"""
    run = Run(companion_ids=["77"], facilities=False)
    run.app.player.location = None
    MovePhaseManager(run.app, "n1", "11").move_phase()
    Clock.pump()
    check(run.app.moves == [], "置き先が無いのに動かした: {}".format(run.app.moves))
    return run


def test_worlds_do_not_mix():
    """世界が違えば同行リストは混ざらない"""
    run = Run(world_name="別の世界", companion_ids=["77"], seed_world="灰都")
    # 控えは「灰都」の下にあるので、この世界では同行していない。
    labels = run.conversation("77")
    check("同行を持ちかける" in labels,
          "別の世界の同行者が引き継がれた: {}".format(labels))
    return run


def test_orphan_label_is_not_duplicated():
    """印の落ちた残骸があっても同行ボタンを二重に足さない"""
    run = Run()
    run.app.in_conversation = True
    # タイトル復帰などで印だけ落ちた残骸を模す。
    run.app.buttons = [
        {"text": "同行を持ちかける",
         "spec": Spec("JustSetButtonToNormalPhase", [])},
        {"text": "会話を終了する",
         "spec": Spec("ConversationEndManager",
                      ["77", "user", "<行動: 会話を終了する>"])},
    ]
    run.app.refresh_choice_buttons(reset_page=True)
    run.app.refresh_choice_buttons(reset_page=True)
    labels = run.labels()
    check(labels.count("同行を持ちかける") == 1,
          "同行ボタンが多重: {}".format(labels))
    check(labels[-1] == "会話を終了する",
          "終了ボタンが最後でない: {}".format(labels))
    orphan = next(b for b in run.app.buttons if b.get("text") == "同行を持ちかける")
    check(orphan.get("mod_companion_action") == "offer",
          "印が付け直されていない: {}".format(orphan))
    return run


def main():
    for test in (test_party_member_gets_no_button,
                 test_offer_accepted,
                 test_offer_via_any_server,
                 test_offer_llm_unavailable,
                 test_offer_refused,
                 test_companion_opening_is_not_a_reunion,
                 test_release,
                 test_facility_move_relocates,
                 test_area_move_all_follow,
                 test_area_move_leave_behind,
                 test_area_move_cancel,
                 test_unreadable_verdicts,
                 test_no_destination,
                 test_worlds_do_not_mix,
                 test_orphan_label_is_not_duplicated):
        scenario(test)
    print("{} passed, {} failed".format(RESULTS["pass"], RESULTS["fail"]))
    return 1 if RESULTS["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
