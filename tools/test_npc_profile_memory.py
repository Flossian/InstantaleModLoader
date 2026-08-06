# -*- coding: utf-8 -*-
"""`311_npc_profile_memory` の検証。ゲーム不要。

    python tools/test_npc_profile_memory.py

**偽のゲームを1つ組んで、本物と同じ形で会話を1ターン流す。** ターンは
`ConversationPhaseManager.conversation_continued` が
`llm_manager.conversation_facilitator` を呼び、返答を履歴に足して `add_text` する
という実測どおりの並びにしてある。この形を守っているので、「NPC の浅い複製の
`profile` にだけ足される」「抽出が返答の**後**に回る」を確かめられる。

見ているのは振る舞いだけ:

- 1ターン終わると抽出が走り、更新後のプロフィール全文が `state/npc_profiles/<世界名>.json` に残る
- **変更なし・空・読めない返答では既存プロフィールを維持する**
- 旧版の分類別 `slots` は情報を落とさず `profile` へ自動移行する
- 控えは次の会話で、浅く複製した NPC の `profile` に足される
- ゲーム世界の NPC 本体と人生ログは書き換えない
- **`messages` は書き換えない**（会話履歴・要約・関係性に波及させない）
- 別の世界・別の人物の控えは混ざらない
- LLM が居なくても、抽出が落ちても、会話のターンそのものは壊れない
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader as ml                      # noqa: E402

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


def load_neighbour(suffix):
    """他の mod を番号を除いた名前で読む（取り決めの突き合わせ用）。

    **これが許されるのは `tools/` の検証だけ**（TECH.md §2.4）。mod どうしは
    実行時に互いを import しない ― ローダは mod を `instantale_mod_<フォルダ名>`
    で登録するので、番号を振り直した瞬間に名前で掴む側が壊れる。
    """
    path = find_mod(suffix)
    folder = os.path.dirname(path)
    spec = importlib.util.spec_from_file_location(
        "neighbour" + suffix, path, submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD_PATH = find_mod("_npc_profile_memory")

FOUND = "北方の村で育った無口な傭兵。古い剣の話を好み、冒険者とギルドの件を調べる約束をしている。"
GIBBERISH = "……（彼について新しく分かった事は特に無いように思われた）"


# ==========================================================================
# 偽のゲーム
# ==========================================================================
class Character(object):
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name
        self.profile = "{}の来歴".format(name)
        self.personality = "無口"
        self.job = "傭兵"
        self.life_log = ["<出来事>故郷を焼かれた"]


class World(object):
    def __init__(self, name, characters):
        self.name = name
        self.worldview = "灰の空の下"
        self.characters = {c.id: c for c in characters}


class InstantaleApp(object):
    def __init__(self, world, player):
        self.world = world
        self.player = player
        self.world_dict = {"world_data": {"world_name": world.name}}
        self.texts = []
        self.in_conversation = "77"
        self.current_conversation_history = [
            {"role": "user", "content": "<行動: 話しかける>"},
            {"role": "assistant", "content": "「……聞くだけは聞こう」"},
        ]

    def add_text(self, text):
        self.texts.append(text)


class ConversationPhaseManager(object):
    """自由入力の1ターン。**実測どおり、返答は関数を抜ける前に足される。**

    `conversation_facilitator` を呼ぶ引数の並びは本物と同じにしてある
    （`out/recon/targets.txt` L1354）。注入が第4引数に当たるかはここで決まる。
    """

    def __init__(self, app, instruction, retrieved_knowledge=None,
                 retrieved_last_time=False):
        self.app = app
        self.instruction = instruction

    def conversation_continued(self, choice_text):
        app = self.app
        npc = app.world.characters[app.in_conversation]
        app.add_text("{}: {}".format(app.player.name, choice_text))
        app.current_conversation_history.append(
            {"role": "user", "content": choice_text})
        reply = llm_manager.conversation_facilitator(
            app.current_conversation_history, "人生ログの本文", app.player, npc,
            app.world.worldview, "世界の物語", "居住", "実績", [], [], "", None)
        app.current_conversation_history.append(
            {"role": "assistant", "content": reply})
        app.add_text("{}: {}".format(npc.name, reply))
        return reply


# ==========================================================================
# 偽の kivy と偽のゲームモジュール
# ==========================================================================
class Clock(object):
    """`schedule_once` を貯めておいて `pump()` で流す。"""

    once = []

    @classmethod
    def schedule_once(cls, callback, delay=0):
        cls.once.append(callback)

    @classmethod
    def schedule_interval(cls, callback, poll):
        pass

    @classmethod
    def reset(cls):
        cls.once = []

    @classmethod
    def pump(cls, rounds=4):
        for _ in range(rounds):
            pending, cls.once = cls.once, []
            for callback in pending:
                callback(0)


class Llm(object):
    """`send_request_with_no_structure` の代わり。**渡された文面も控える。**"""

    answers = {}
    raises = set()
    prompts = []
    delay = 0.0

    @classmethod
    def send(cls, manager_name, messages, max_tokens=None, timeout=None):
        cls.prompts.append((manager_name, messages))
        if cls.delay:
            time.sleep(cls.delay)
        if manager_name in cls.raises:
            raise RuntimeError("推論が落ちた")
        queue = cls.answers.get(manager_name)
        if isinstance(queue, list) and queue:
            return queue.pop(0)
        return queue if isinstance(queue, str) else ""

    @classmethod
    def reset(cls, answers=None, raises=()):
        cls.answers = dict(answers or {})
        cls.raises = set(raises)
        cls.prompts = []
        cls.delay = 0.0

    @classmethod
    def last_prompt(cls, manager_name):
        for name, messages in reversed(cls.prompts):
            if name == manager_name:
                return "\n".join(m.get("content", "") for m in messages)
        return ""


class Structured(object):
    """`send_request` + `create_model`（構造化出力）の代わり。

    この2つが `llm_manager` に載っている版を再現する。**載っていない版もある**
    ので既定では外しておき、`Run(structured=...)` のときだけ載せる。そうすると
    「構造化が使える版では使う」「使えない版では頼み文だけの JSON に降りる」の
    両方を、同じ筋書きの並びで確かめられる。
    """

    payloads = {}
    raises = set()
    calls = []
    models = []

    @classmethod
    def create_model(cls, model_name, **fields):
        cls.models.append((model_name, dict(fields)))
        return type(str(model_name), (object,), {"mod_fields": dict(fields)})

    @classmethod
    def send(cls, manager_name, message, structure, max_tokens=None, timeout=None):
        cls.calls.append({"manager": manager_name, "message": message,
                          "structure": structure, "timeout": timeout})
        if manager_name in cls.raises:
            raise RuntimeError("構造化推論が落ちた")
        queue = cls.payloads.get(manager_name)
        if isinstance(queue, list) and queue:
            return queue.pop(0)
        return queue

    @classmethod
    def reset(cls, payloads=None, raises=()):
        cls.payloads = dict(payloads or {})
        cls.raises = set(raises)
        cls.calls = []
        cls.models = []


def install_structured(on):
    """`llm_manager` に構造化出力の2関数を載せる／外す。"""
    for name, value in (("send_request", Structured.send),
                        ("create_model", Structured.create_model)):
        if on:
            setattr(llm_manager, name, value)
        elif getattr(llm_manager, name, None) is not None:
            delattr(llm_manager, name)


class Facilitator(object):
    """会話の返答を作る側。**受け取った引数をそのまま控える。**"""

    calls = []
    reply = "「……北の村の生まれだ。もう焼けて無いがな」"

    @classmethod
    def reset(cls):
        cls.calls = []


def _conversation_facilitator(messages, character_life_log, player,
                              character_instance, worldview, world_story,
                              area_residency, area_achievements, response_list,
                              negotiate_response_list, response_prompt_instruction,
                              retrieved_knowledge, job_knowledge=""):
    Facilitator.calls.append({"messages": messages,
                              "character_life_log": character_life_log,
                              "npc": character_instance})
    return Facilitator.reply


def _conversation_starter(messages, character_life_log, player, character_instance,
                          character_relationship, worldview, world_story,
                          area_residency, area_achievements):
    Facilitator.calls.append({"messages": messages,
                              "character_life_log": character_life_log,
                              "npc": character_instance})
    return "「また来たのか」"


LLAMA_CPP = "scripts.llm.request_llm_inference_llama_cpp_completion"


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

    llm = types.ModuleType(LLAMA_CPP)
    llm.send_request_with_no_structure = Llm.send

    context = types.ModuleType("scripts.llm.context_manager")
    context.conversation_history_to_text = lambda history, player, npc: "\n".join(
        "{}: {}".format(turn.get("role"), turn.get("content")) for turn in history)

    manager = types.ModuleType("scripts.llm.llm_manager")
    manager.conversation_facilitator = _conversation_facilitator
    manager.conversation_starter = _conversation_starter

    sys.modules.update({
        LLAMA_CPP: llm,
        "scripts.llm.context_manager": context,
        "scripts.llm.llm_manager": manager,
    })
    return App, manager


APP_HOLDER, llm_manager = install_modules()


class Ctx(object):
    """ローダの `ctx` の代わり。`wrap` は対象ごとに関数を控えるだけ。"""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        # 永続データの置き場は out/ と別（ローダの `state_dir`）。ここでも
        # **別のフォルダ**にしておかないと、状態を out/ に書く mod が素通りする。
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        return self._under(self.out_dir, parts)

    def state_path(self, *parts):
        return self._under(self.state_dir, parts)

    @staticmethod
    def _under(root, parts):
        path = os.path.join(root, *parts)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    def log(self, message, level="INFO"):
        pass

    def log_exc(self, message):
        self.errors.append(message)

    # 本物の `ctx.write_json` と同じものを使う。ここを自前の open(..., "w") に
    # すると、テストだけが「壊れない書き方」を通らなくなる。
    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def wrap(self, target, required=True):
        def decorate(fn):
            self.hooks[target] = fn
            return fn
        return decorate


def load_mod():
    spec = importlib.util.spec_from_file_location("npc_profile_memory_under_test",
                                                  MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = load_mod()

TURN = "__main__:ConversationPhaseManager.conversation_continued"
FACILITATOR = "scripts.llm.llm_manager:conversation_facilitator"
STARTER = "scripts.llm.llm_manager:conversation_starter"

# 筋書きごとに `apply` をやり直すので、**掛ける前に必ず素の実装へ戻す**。
# 戻さないと前の筋書きのフック（既に消えた一時フォルダを指す控えを掴んでいる）が
# 内側に残り、後の筋書きの控えを勝手に書き足す。
ORIGINAL_TURN = ConversationPhaseManager.conversation_continued
ORIGINAL_FUNCS = {"conversation_facilitator": _conversation_facilitator,
                  "conversation_starter": _conversation_starter}


def unbind_all():
    ConversationPhaseManager.conversation_continued = ORIGINAL_TURN
    for name, original in ORIGINAL_FUNCS.items():
        setattr(llm_manager, name, original)


def bind_method(ctx, target, owner, name):
    """控えたフックをクラスに掛ける（ローダの `patch` と同じ形）。"""
    hook = ctx.hooks.get(target)
    if hook is None:
        raise AssertionError("hook not registered: {}".format(target))
    original = getattr(owner, name)

    def patched(self, *args, **kwargs):
        return hook(original, self, *args, **kwargs)

    setattr(owner, name, patched)


def bind_function(ctx, target, module, name):
    """モジュール直下の関数に掛ける（`self` を挟まない）。"""
    hook = ctx.hooks.get(target)
    if hook is None:
        raise AssertionError("hook not registered: {}".format(target))
    original = getattr(module, name)

    def patched(*args, **kwargs):
        return hook(original, *args, **kwargs)

    setattr(module, name, patched)


# ==========================================================================
# 検査の土台
# ==========================================================================
def reset_mod_store():
    """`sys` に置かれた mod の共有一式を落とす（筋書きごとの独立を保つ）。"""
    if hasattr(sys, MOD.STATE_STORE_ATTR):
        delattr(sys, MOD.STATE_STORE_ATTR)


class Run(object):
    """1つの筋書き。偽のゲームを組んで mod を適用したところまで。"""

    def __init__(self, world_name="灰都", answers=None, raises=(), seed=None,
                 seed_world=None, with_llm=True, legacy=False, structured=None,
                 structured_raises=False, seed_record=None):
        self.tmp = tempfile.mkdtemp(prefix="npc_profile_test_")
        unbind_all()
        # mod は控えとワーカーを `sys` に置いて**世代をまたいで共有する**
        # （再注入で `apply()` が何度も走っても2本目のワーカーを立てないため）。
        # 筋書きごとに新しいプロセスにはできないので、ここで落として1件ずつ
        # 独立させる。落とさないと前の筋書きのワーカーが今回の仕事を拾い、
        # 古い `out/` へ書いてしまう（実際に 20 件が時間切れで落ちた）。
        reset_mod_store()
        Clock.reset()
        Facilitator.reset()
        Llm.reset({MOD.MANAGER_EXTRACT: answers} if answers else None, raises)
        Structured.reset({MOD.MANAGER_EXTRACT: structured} if structured else None,
                         (MOD.MANAGER_EXTRACT,) if structured_raises else ())
        install_structured(structured is not None or structured_raises)

        self.npc = Character("77", "傭兵ガロ")
        self.other = Character("88", "薬売りミラ")
        player = Character("player", "冒険者")
        world = World(world_name, [self.npc, self.other, player])

        self.app = InstantaleApp(world, player)
        APP_HOLDER.running = self.app

        self.saved_llm = sys.modules.get(LLAMA_CPP)
        if not with_llm:
            sys.modules.pop(LLAMA_CPP, None)

        self.ctx = Ctx(self.tmp)
        if seed:
            self.seed(seed_world or world_name, seed, legacy=legacy)
        if seed_record:
            self.seed_full(seed_world or world_name, seed_record)
        MOD.apply(self.ctx)

        bind_method(self.ctx, TURN, ConversationPhaseManager,
                    "conversation_continued")
        bind_function(self.ctx, FACILITATOR, llm_manager, "conversation_facilitator")
        bind_function(self.ctx, STARTER, llm_manager, "conversation_starter")

    def seed(self, world_name, profile, npc_id="77", legacy=False):
        """既に何か覚えている状態から始める。"""
        record = {"name": "傭兵ガロ", "updated": "2026-07-30T00:00:00"}
        record["slots" if legacy else "profile"] = profile
        path = os.path.join(self.tmp, "state", "npc_profiles",
                            ml.state.world_filename(world_name))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({npc_id: record}, fh, ensure_ascii=False)

    def seed_full(self, world_name, record, npc_id="77"):
        """控えの中身をこちらで丸ごと決めて始める（欄ごとの検査用）。"""
        path = os.path.join(self.tmp, "state", "npc_profiles",
                            ml.state.world_filename(world_name))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({npc_id: record}, fh, ensure_ascii=False)

    # -- 操作 -------------------------------------------------------------
    def turn(self, text="お前の故郷はどこだ"):
        """自由入力を1ターン。押下から返答までは本物と同じ並び。"""
        phase = ConversationPhaseManager(self.app, "自由入力")
        reply = phase.conversation_continued(text)
        Clock.pump()
        self.wait_finished()
        return reply

    def wait_finished(self, count=1):
        deadline = time.monotonic() + 2.0
        while self.log().count("extract: finished") < count:
            if time.monotonic() >= deadline:
                self.ctx.errors.append("background extraction timed out")
                break
            time.sleep(0.005)

    def profiles(self):
        """全世界分。キーはファイル名の幹（通常は世界名そのもの）。"""
        root = os.path.join(self.tmp, "state", "npc_profiles")
        result = {}
        if not os.path.isdir(root):
            return result
        for name in os.listdir(root):
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if isinstance(data, dict):
                result[name[:-5]] = data
        return result

    def log(self):
        path = os.path.join(self.tmp, "npc_profile.log")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""

    def facilitate(self, npc=None):
        """通常ターンと同じ並びで `conversation_facilitator` を1回呼ぶ。"""
        return llm_manager.conversation_facilitator(
            self.app.current_conversation_history, "人生ログの本文",
            self.app.player, npc if npc is not None else self.npc,
            "", "", "", "", [], [], "", None)

    def record(self, npc_id="77", world_name=None):
        stem = ml.state.world_filename(world_name or self.app.world.name)[:-5]
        return self.profiles().get(stem, {}).get(npc_id) or {}

    def profile(self, npc_id="77", world_name=None):
        return self.record(npc_id, world_name).get("profile", "")

    def about_player(self, npc_id="77", world_name=None):
        return self.record(npc_id, world_name).get("about_player", "")

    def facts(self, npc_id="77", world_name=None):
        got = self.record(npc_id, world_name).get("facts")
        return [item.get("text") for item in got] if isinstance(got, list) else []

    def cleanup(self):
        APP_HOLDER.running = None
        install_structured(False)
        if self.saved_llm is not None:
            sys.modules[LLAMA_CPP] = self.saved_llm
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
def test_turn_updates_profile():
    """1ターン終わると更新後のプロフィール全文が残る"""
    run = Run(answers=FOUND)
    run.turn()
    check(run.profile() == FOUND, "プロフィールが残らない: {!r}".format(run.profile()))
    stem = ml.state.world_filename(run.app.world.name)[:-5]
    record = run.profiles()[stem]["77"]
    check(record.get("name") == "傭兵ガロ", "名前が控えられていない: {}".format(record))
    check(bool(record.get("updated")), "更新時刻が無い: {}".format(record))
    path = os.path.join(run.tmp, "state", "npc_profiles",
                        ml.state.world_filename(run.app.world.name))
    check(os.path.isfile(path), "世界ファイルが無い: {}".format(path))
    return run


def test_extraction_runs_after_the_reply():
    """抽出は返答が出た後に回る"""
    run = Run(answers=FOUND)
    run.turn()
    # `Clock.pump()` を回す前に、返答が既に出ていること。
    check(run.app.texts[-1].startswith("傭兵ガロ: "),
          "返答が出ていない: {}".format(run.app.texts))
    check("extract queued: '傭兵ガロ' (77)" in run.log(),
          "キュー投入の記録が無い: {!r}".format(run.log()))
    # 抽出のプロンプトには、そのターンのやり取りが載っている。
    prompt = Llm.last_prompt(MOD.MANAGER_EXTRACT)
    check("お前の故郷はどこだ" in prompt, "入力が載っていない: {}".format(prompt[:200]))
    check(Facilitator.reply[1:6] in prompt, "返答が載っていない: {}".format(prompt[:200]))
    check("JSON オブジェクト1つ" in prompt,
          "JSON で返す指示が無い: {}".format(prompt[:200]))
    for key in (MOD.KEY_CHANGED, MOD.KEY_PROFILE, MOD.KEY_ABOUT_PLAYER,
                MOD.KEY_NEW_FACTS):
        check('"{}"'.format(key) in prompt,
              "項目 {} の指示が無い: {}".format(key, prompt[:300]))
    return run


def test_extraction_does_not_block_the_game_thread():
    """LLMが遅くても会話とClockは即座に戻る"""
    run = Run(answers=FOUND)
    Llm.delay = 0.2
    phase = ConversationPhaseManager(run.app, "自由入力")
    started = time.monotonic()
    reply = phase.conversation_continued("お前の故郷はどこだ")
    Clock.pump()
    elapsed = time.monotonic() - started
    check(reply == Facilitator.reply, "会話の返答が返らない")
    check(elapsed < 0.1, "メインスレッドを {:.3f}s 止めた".format(elapsed))
    check(run.profile() == "", "完了前に保存された: {!r}".format(run.profile()))
    check(run.app.texts[-1].startswith("傭兵ガロ: "),
          "抽出待ちで返答が出ていない: {}".format(run.app.texts))
    run.wait_finished()
    check(run.profile() == FOUND, "バックグラウンドで保存されない")
    return run


def test_queued_extractions_are_serialized():
    """続けて来た抽出は順番に処理し、後の更新が前を踏まえる"""
    first = "北方の村で育った無口な傭兵。"
    second = "北方の村で育った無口な傭兵。古い剣の話を好む。"
    run = Run(answers=[first, second])
    phase = ConversationPhaseManager(run.app, "自由入力")
    phase.conversation_continued("故郷はどこだ")
    phase.conversation_continued("何が好きだ")
    Clock.pump()
    run.wait_finished(2)
    check(run.profile() == second, "キューの最終結果が違う: {!r}".format(run.profile()))
    prompts = [messages for name, messages in Llm.prompts
               if name == MOD.MANAGER_EXTRACT]
    second_prompt = "\n".join(item["content"] for item in prompts[1])
    check(first in second_prompt, "後の抽出が前の更新を読んでいない")
    return run


def test_no_change_keeps_profile():
    """「変更なし」なら既存プロフィールを維持する"""
    run = Run(answers=MOD.NO_CHANGE, seed="古い剣を好む無口な傭兵。")
    run.turn()
    check(run.profile() == "古い剣を好む無口な傭兵。",
          "プロフィールが変わった: {!r}".format(run.profile()))
    return run


def test_empty_explanation_keeps_profile():
    """新情報なしの説明文でも既存プロフィールを維持する"""
    run = Run(answers=GIBBERISH, seed="古い剣を好む無口な傭兵。")
    run.turn()
    check(run.profile() == "古い剣を好む無口な傭兵。",
          "説明文で上書きされた: {!r}".format(run.profile()))
    return run


def test_legacy_slots_migrate_to_profile():
    """旧slotsは情報を落とさずprofileへ移行する"""
    slots = {"好み": ["古い剣の話"], "経歴": ["北方の村の生まれ"],
             "約束": ["ギルドの件を調べる"]}
    run = Run(seed=slots, legacy=True)
    run.facilitate()
    profile = run.profile()
    check("好み: 古い剣の話" in profile, "好みが落ちた: {!r}".format(profile))
    check("経歴: 北方の村の生まれ" in profile, "経歴が落ちた: {!r}".format(profile))
    check("約束: ギルドの件を調べる" in profile, "約束が落ちた: {!r}".format(profile))
    stem = ml.state.world_filename(run.app.world.name)[:-5]
    check(run.profiles()[stem]["77"].get("slots") == slots,
          "移行で旧slotsを消した: {}".format(run.profiles()))
    return run


def test_profile_keeps_full_llm_response():
    """保存でも注入でもLLMの返却を切らない"""
    answer = "新" * (MOD.INJECT_CHARS + 200)
    run = Run(answers=answer)
    run.turn()
    check(run.profile() == answer,
          "保存で切った: {} chars".format(len(run.profile())))
    run.facilitate()
    passed = Facilitator.calls[-1]["npc"]
    check(answer in passed.profile,
          "注入で切った: {} chars".format(len(passed.profile)))
    return run


def test_extraction_prompt_asks_for_summary_budget():
    """抽出LLMには欄ごとの目標長を指示する"""
    run = Run(answers=FOUND)
    run.turn()
    prompt = Llm.last_prompt(MOD.MANAGER_EXTRACT)
    check("{}文字以内".format(MOD.INJECT_CHARS) in prompt,
          "人物像の目標長指示が無い: {}".format(prompt[:400]))
    about = max(1, MOD.INJECT_CHARS // MOD.ABOUT_PLAYER_RATIO)
    check("{}文字以内".format(about) in prompt,
          "プレイヤーの記録の目標長指示が無い: {}".format(prompt[:400]))
    check("要約して統合" in prompt, "要約の指示が無い: {}".format(prompt[:400]))
    return run


def test_profile_reaches_the_next_reply():
    """控えは複製NPCのprofileに足され、元NPCは変わらない"""
    run = Run(answers=MOD.NO_CHANGE, seed="北方の村で育った無口な傭兵。")
    original_profile = run.npc.profile
    run.turn()
    passed = Facilitator.calls[-1]["npc"]
    check(passed is not run.npc, "NPC本体をそのまま渡した")
    check(isinstance(passed, Character), "同じCharacter型でない: {}".format(type(passed)))
    check(MOD.PROFILE_HEADING in passed.profile,
          "追加プロフィールの見出しが無い: {!r}".format(passed.profile))
    check("北方の村で育った" in passed.profile,
          "追加プロフィールが載らない: {!r}".format(passed.profile))
    check(passed.profile.startswith(original_profile),
          "ゲーム本体のプロフィールが消えた: {!r}".format(passed.profile))
    check(run.npc.profile == original_profile,
          "ゲーム世界のNPCを書き換えた: {!r}".format(run.npc.profile))
    check(Facilitator.calls[-1]["character_life_log"] == "人生ログの本文",
          "人生ログを書き換えた: {!r}".format(
              Facilitator.calls[-1]["character_life_log"]))
    return run


def test_profile_reaches_keyword_calls():
    """キーワード引数で呼ばれても足される"""
    run = Run(seed="妹を探して旅を続けている。")
    llm_manager.conversation_facilitator(
        messages=run.app.current_conversation_history,
        character_life_log="人生ログの本文", player=run.app.player,
        character_instance=run.npc, worldview="", world_story="",
        area_residency="", area_achievements="", response_list=[],
        negotiate_response_list=[], response_prompt_instruction="",
        retrieved_knowledge=None)
    passed = Facilitator.calls[-1]["npc"]
    check("妹を探して" in passed.profile,
          "控えが載らない: {!r}".format(passed.profile))
    return run


def test_profile_reaches_the_opening_line():
    """第一声にも足される（conversation_starter も同じ並び）"""
    run = Run(seed="冒険者とギルドの件を調べる約束をしている。")
    llm_manager.conversation_starter(
        run.app.current_conversation_history, "人生ログの本文", run.app.player,
        run.npc, "顔見知り", "", "", "", "")
    passed = Facilitator.calls[-1]["npc"]
    check("ギルドの件を調べる" in passed.profile,
          "控えが載らない: {!r}".format(passed.profile))
    return run


def test_messages_are_not_touched():
    """注入で messages は書き換えない"""
    run = Run(answers=MOD.NO_CHANGE, seed="北方の村の生まれ。")
    history = run.app.current_conversation_history
    before = [dict(turn) for turn in history]
    run.turn()
    passed = Facilitator.calls[-1]["messages"]
    check(passed is history, "履歴が別物に差し替わった")
    # ターンで増えるのは入力の1行だけ。注入の分が混ざっていないこと。
    check(passed[:len(before)] == before, "履歴が書き換わった: {}".format(passed))
    check(not any("北方の村の生まれ" in turn.get("content", "") for turn in passed),
          "控えが履歴に漏れた: {}".format(passed))
    return run


def test_other_npc_gets_nothing():
    """別の人物の控えは混ざらない"""
    run = Run(seed="賞金首を匿っている。")
    run.facilitate(run.other)
    passed = Facilitator.calls[-1]["npc"]
    check(passed is run.other, "記録の無いNPCまで複製した")
    check(MOD.PROFILE_HEADING not in passed.profile,
          "他人の控えが載った: {!r}".format(passed.profile))
    return run


def test_worlds_do_not_mix():
    """世界が違えば控えは混ざらない"""
    run = Run(world_name="別の世界", seed="賞金首を匿っている。",
              seed_world="灰都")
    run.facilitate()
    passed = Facilitator.calls[-1]["npc"]
    check(passed is run.npc, "別世界の記録でNPCを複製した")
    check(MOD.PROFILE_HEADING not in passed.profile,
          "別の世界の控えが引き継がれた: {!r}".format(passed.profile))
    return run


def test_id_comes_from_the_world_index():
    """`.id` を持たない相手でも控えを引ける"""
    run = Run(seed="北方の村の生まれ。")
    # `Character.id` が実在するかは実測できていない。無くても引けること。
    del run.npc.id
    run.facilitate()
    passed = Facilitator.calls[-1]["npc"]
    check("北方の村の生まれ" in passed.profile,
          "控えが載らない: {!r}".format(passed.profile))
    return run


def test_injection_leaves_a_trace():
    """注入したかどうかがログに残る"""
    run = Run(seed="北方の村の生まれ。")
    run.facilitate()
    check("facilitator: '傭兵ガロ' (77) +" in run.log()
          and "into profile" in run.log(),
          "注入の記録が無い: {!r}".format(run.log()))
    run.facilitate(run.other)
    check("nothing recorded for '薬売りミラ' (88)" in run.log(),
          "素通りした理由が残っていない: {!r}".format(run.log()))
    return run


def test_life_log_is_not_touched():
    """人生ログはどの型でも書き換えない"""
    run = Run(seed="北方の村の生まれ。")
    original = {"450日前": "['<会話>…']", "180日前": "['<会話>…']"}
    llm_manager.conversation_facilitator(
        run.app.current_conversation_history, original, run.app.player,
        run.npc, "", "", "", "", [], [], "", None)
    passed = Facilitator.calls[-1]["character_life_log"]
    check(passed is original, "人生ログを差し替えた")
    check(passed == original, "人生ログを書き換えた: {!r}".format(passed))
    return run


def test_reapply_shares_one_worker_and_one_lock():
    """当て直しても控え・待ち行列・錠は1組のまま

    `apply()` は再注入と遅延当て直しで最大8回走る（TECH.md §3.4）。
    以前は `state` / `jobs` / `data_lock` を `apply()` の中で作っていたため、
    走るたびに `state["worker"]` が `None` に戻り、前の世代のワーカーが
    生きている間に2本目が起動した。しかも `data_lock` が別インスタンスに
    なるので、2本が同じ `state/npc_profiles/<世界>.json` を排他なしで
    read-modify-write できた（人物像の更新が消える経路）。
    """
    run = Run(seed="北方の村の生まれ。")
    first = getattr(sys, MOD.STATE_STORE_ATTR)

    MOD.apply(run.ctx)          # 遅延当て直し・手での再注入に相当

    second = getattr(sys, MOD.STATE_STORE_ATTR)
    check(second is first, "当て直しで控え一式が作り直された")
    for key in ("state", "cache", "jobs", "data_lock", "worker_lock", "log_lock"):
        check(second[key] is first[key], "{} が別物になった".format(key))
    return run


def test_all_five_conversation_hooks_inject_profile():
    """5つの会話生成経路すべてで追加プロフィールを渡す"""
    run = Run(seed="古い剣を好む無口な傭兵。")
    targets = (
        "scripts.llm.llm_manager:conversation_facilitator",
        "scripts.llm.llm_manager:conversation_facilitator_after_retrieval",
        "scripts.llm.llm_manager:conversation_facilitator_in_quest",
        "scripts.llm.llm_manager:conversation_starter",
        "scripts.llm.llm_manager:conversation_starter_in_quest",
    )
    seen = []

    def capture(*args, **kwargs):
        seen.append(kwargs.get("character_instance") or args[3])
        return "ok"

    for target in targets:
        hook = run.ctx.hooks.get(target)
        check(hook is not None, "フックが未登録: {}".format(target))
        if hook is not None:
            hook(capture, [], {}, run.app.player, run.npc)
    check(len(seen) == len(targets), "全経路を通らない: {}".format(len(seen)))
    check(all(MOD.PROFILE_HEADING in npc.profile for npc in seen),
          "追加プロフィールが無い経路がある")
    check(all(npc is not run.npc for npc in seen), "NPC本体を渡した経路がある")
    return run


# ========================================================== 構造化出力と JSON
def test_structured_output_is_used_when_available():
    """`send_request` が在る版では構造化出力で受け取る"""
    run = Run(structured={MOD.KEY_CHANGED: "true",
                          MOD.KEY_PROFILE: FOUND,
                          MOD.KEY_ABOUT_PLAYER: "命の恩人として扱っている。",
                          MOD.KEY_NEW_FACTS: ["北方の村の生まれ", "古い剣を好む"]})
    run.turn()
    check(run.profile() == FOUND, "人物像が残らない: {!r}".format(run.profile()))
    check(run.about_player() == "命の恩人として扱っている。",
          "プレイヤーの記録が残らない: {!r}".format(run.about_player()))
    check(run.facts() == ["北方の村の生まれ", "古い剣を好む"],
          "判明した事実が残らない: {}".format(run.facts()))
    check(len(Structured.calls) == 1,
          "構造化で1回呼んでいない: {}".format(len(Structured.calls)))
    # 非構造化には**降りていない**こと（両方に投げると2回課金される）。
    check(not [name for name, _m in Llm.prompts if name == MOD.MANAGER_EXTRACT],
          "構造化が通ったのに非構造化にも投げた")
    return run


def test_structured_call_follows_the_house_rules():
    """構造化の呼び方は message=リスト・timeout 付き・Literal 無し"""
    run = Run(structured={MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: FOUND,
                          MOD.KEY_ABOUT_PLAYER: "", MOD.KEY_NEW_FACTS: []})
    run.turn()
    call = Structured.calls[-1]
    # 文字列を渡すとゲームの内部スレッドで落ち、**呼び出しが永久に返らない**
    # （GAME.md §2.12・実機で実測）。
    check(isinstance(call["message"], list),
          "message がリストでない: {}".format(type(call["message"])))
    check(all(isinstance(item, dict) and "role" in item and "content" in item
              for item in call["message"]),
          "message の形が違う: {}".format(call["message"]))
    check(call["timeout"] == MOD.EXTRACT_TIMEOUT,
          "timeout を渡していない: {!r}".format(call["timeout"]))
    check(call["manager"].startswith("mod_"),
          "manager_name が mod_ で始まらない: {!r}".format(call["manager"]))
    # 空の `Literal[]` は pydantic が拒否してゲームごと落ちる（`203_` の実測）。
    fields = dict(Structured.models[-1][1])
    for name, definition in fields.items():
        annotation = definition[0] if isinstance(definition, tuple) else definition
        check("Literal" not in repr(annotation),
              "{} に Literal を使っている: {!r}".format(name, annotation))
    check(fields.get(MOD.KEY_CHANGED, (None,))[0] is str,
          "changed が str でない: {!r}".format(fields.get(MOD.KEY_CHANGED)))
    return run


def test_structured_failure_falls_back_to_text():
    """構造化が落ちたら頼み文だけのJSONに降りる"""
    run = Run(structured_raises=True,
              answers=json.dumps({MOD.KEY_CHANGED: "true",
                                  MOD.KEY_PROFILE: FOUND,
                                  MOD.KEY_ABOUT_PLAYER: "借りがあると思っている。",
                                  MOD.KEY_NEW_FACTS: []}, ensure_ascii=False))
    run.turn()
    check(run.profile() == FOUND, "降りた先で保存されない: {!r}".format(run.profile()))
    check(run.about_player() == "借りがあると思っている。",
          "降りた先でプレイヤーの記録が残らない: {!r}".format(run.about_player()))
    # 落ちたことは握り潰さず記録する。ここだけは errors が空でないのが正しい。
    check(any("mod_npc_profile_extract" in message for message in run.ctx.errors),
          "落ちたのに記録が無い: {}".format(run.ctx.errors))
    run.ctx.errors = []
    return run


def test_changed_false_keeps_the_record():
    """`changed` が false なら控えを変えない"""
    run = Run(seed_record={"name": "傭兵ガロ", "profile": "古い剣を好む。",
                           "about_player": "まだ値踏みしている。"},
              structured={MOD.KEY_CHANGED: "false",
                          MOD.KEY_PROFILE: "全く別の人物像",
                          MOD.KEY_ABOUT_PLAYER: "全く別の態度",
                          MOD.KEY_NEW_FACTS: ["作り話"]})
    run.turn()
    check(run.profile() == "古い剣を好む。",
          "人物像が変わった: {!r}".format(run.profile()))
    check(run.about_player() == "まだ値踏みしている。",
          "プレイヤーの記録が変わった: {!r}".format(run.about_player()))
    check(run.facts() == [], "変更なしなのに事実が増えた: {}".format(run.facts()))
    return run


def test_json_in_plain_text_is_read():
    """構造化が無い版でも、本文のJSONを読む"""
    run = Run(answers="```json\n" + json.dumps(
        {MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: FOUND,
         MOD.KEY_ABOUT_PLAYER: "恩義を感じている。",
         MOD.KEY_NEW_FACTS: ["妹を探している"]}, ensure_ascii=False) + "\n```")
    run.turn()
    check(run.profile() == FOUND, "囲み付きJSONを読めない: {!r}".format(run.profile()))
    check(run.about_player() == "恩義を感じている。",
          "プレイヤーの記録を読めない: {!r}".format(run.about_player()))
    check(run.facts() == ["妹を探している"],
          "事実を読めない: {}".format(run.facts()))
    return run


def test_broken_json_changes_nothing():
    """JSONのつもりで壊れた返却では控えを変えない

    丸ごと人物像として保存すると、壊れた JSON がそのままプロフィールになって
    以後の会話に注入される。**安全側は「変更しない」。**
    """
    run = Run(seed="古い剣を好む無口な傭兵。",
              answers='{"changed": "true", "profile": "北方の村の生ま')
    run.turn()
    check(run.profile() == "古い剣を好む無口な傭兵。",
          "壊れたJSONで上書きされた: {!r}".format(run.profile()))
    return run


# ========================================================== プレイヤーへの認識
def test_about_player_is_injected_under_its_own_heading():
    """プレイヤーへの認識は人物像とは別の見出しで注入する"""
    run = Run(seed_record={"name": "傭兵ガロ", "profile": "古い剣を好む。",
                           "about_player": "ギルドの件を調べる約束をしている。"})
    run.facilitate()
    passed = Facilitator.calls[-1]["npc"]
    check(MOD.PROFILE_HEADING in passed.profile,
          "人物像の見出しが無い: {!r}".format(passed.profile))
    heading = MOD.ABOUT_PLAYER_HEADING.format(player=run.app.player.name)
    check(heading in passed.profile,
          "プレイヤーの見出しが無い: {!r}".format(passed.profile))
    check("古い剣を好む。" in passed.profile and "ギルドの件を調べる" in passed.profile,
          "両方が載っていない: {!r}".format(passed.profile))
    check(passed.profile.index(MOD.PROFILE_HEADING) < passed.profile.index(heading),
          "並びが逆: {!r}".format(passed.profile))
    return run


def test_about_player_alone_is_enough_to_inject():
    """人物像がまだ無くても、プレイヤーへの認識だけで注入する"""
    run = Run(seed_record={"name": "傭兵ガロ", "about_player": "借りがある。"})
    run.facilitate()
    passed = Facilitator.calls[-1]["npc"]
    check(passed is not run.npc, "複製していない")
    check("借りがある。" in passed.profile,
          "認識だけでは注入されない: {!r}".format(passed.profile))
    check(MOD.PROFILE_HEADING not in passed.profile,
          "空の人物像の見出しまで足した: {!r}".format(passed.profile))
    return run


# ========================================================== 判明した事実の控え
def test_facts_accumulate_without_duplicates():
    """事実は追記され、同じものは二度入らない"""
    run = Run(structured=[{MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: "一度目。",
                           MOD.KEY_ABOUT_PLAYER: "", MOD.KEY_NEW_FACTS: ["北方の生まれ"]},
                          {MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: "二度目。",
                           MOD.KEY_ABOUT_PLAYER: "",
                           MOD.KEY_NEW_FACTS: ["北方の生まれ", "妹を探している"]}])
    phase = ConversationPhaseManager(run.app, "自由入力")
    phase.conversation_continued("故郷はどこだ")
    phase.conversation_continued("何を探している")
    Clock.pump()
    run.wait_finished(2)
    check(run.facts() == ["北方の生まれ", "妹を探している"],
          "事実の積み方がおかしい: {}".format(run.facts()))
    stamped = run.record().get("facts")
    check(all(isinstance(item, dict) and item.get("at") for item in stamped),
          "いつ分かったのかが残っていない: {}".format(stamped))
    return run


def test_facts_are_capped():
    """事実の控えは上限を超えたら古い方から落とす"""
    old = [{"at": "2026-07-30T00:00:00", "text": "古い事実{}".format(i)}
           for i in range(MOD.FACT_LOG_LIMIT)]
    run = Run(seed_record={"name": "傭兵ガロ", "profile": "既に何か覚えている。",
                           "facts": old},
              structured={MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: FOUND,
                          MOD.KEY_ABOUT_PLAYER: "",
                          MOD.KEY_NEW_FACTS: ["新しい事実A", "新しい事実B"]})
    run.turn()
    facts = run.facts()
    check(len(facts) == MOD.FACT_LOG_LIMIT,
          "上限に収まっていない: {}".format(len(facts)))
    check(facts[-2:] == ["新しい事実A", "新しい事実B"],
          "新しい事実が末尾に無い: {}".format(facts[-3:]))
    check("古い事実0" not in facts, "落ちたのが古い方でない: {}".format(facts[:3]))
    return run


# ========================================================== 控えの形（取り決め）
def test_record_keys_are_written_in_order():
    """控えの鍵は `RECORD_KEYS` の並びで書く

    この JSON は `301_quest_from_conversation` も読む取り決めなので、
    人物ごとに鍵の順が違うと差分を見たときに何が増えたのか分からない。
    """
    run = Run(structured={MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: FOUND,
                          MOD.KEY_ABOUT_PLAYER: "恩義を感じている。",
                          MOD.KEY_NEW_FACTS: ["北方の生まれ"]})
    run.turn()
    keys = list(run.record().keys())
    check(keys == [key for key in MOD.RECORD_KEYS if key in keys],
          "鍵の並びが宣言と違う: {}".format(keys))
    check(set(keys) == set(MOD.RECORD_KEYS),
          "書かれた鍵が揃っていない: {}".format(keys))
    return run


def test_legacy_slots_survive_the_migration():
    """移行しても旧`slots`は消さない（古い版へ戻せるように）"""
    slots = {"好み": ["古い剣の話"], "経歴": ["北方の村の生まれ"]}
    run = Run(seed=slots, legacy=True)
    run.facilitate()
    check(run.record().get("slots") == slots,
          "旧slotsを消した: {}".format(run.record()))
    keys = list(run.record().keys())
    check(keys[-1] == "slots",
          "知らない鍵が先頭に来た: {}".format(keys))
    return run


def test_a_failed_write_keeps_the_previous_file():
    """書き込みが途中で落ちても前の控えは残る

    **落ちても壊れない書き方そのもの**（隣に書いてから差し替える）は
    `instantale_modloader.write_text` に一本化されていて、その仕組みは
    `tools/test_patch_registry.py` が見ている。ここで確かめるのは
    **この mod 側の約束**の方 ―

      * 書けなかったとき、前の控えがそのまま残る（`{}` に倒れない）
      * 書けなかったことを握り潰さず記録する

    書き込みを失敗させるには、`os.replace`（＝差し替えの瞬間）を落とす。
    仮ファイルまでは書けていて本体だけが古いまま、という**いちばん危ない窓**を
    再現できる。
    """
    run = Run(seed_record={"name": "傭兵ガロ", "profile": "先に覚えていた人物像。"},
              structured={MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: FOUND,
                          MOD.KEY_ABOUT_PLAYER: "", MOD.KEY_NEW_FACTS: []})
    path = os.path.join(run.tmp, "state", "npc_profiles",
                        ml.state.world_filename(run.app.world.name))
    with open(path, "rb") as fh:
        before = fh.read()

    saved_replace = ml.os.replace

    def broken_replace(src, dst):
        raise OSError("差し替えが落ちた")

    ml.os.replace = broken_replace
    try:
        run.turn()
    finally:
        ml.os.replace = saved_replace

    with open(path, "rb") as fh:
        after = fh.read()
    check(after == before, "落ちた書き込みで前の控えが壊れた: {!r}".format(after[:80]))
    check(run.profile() == "先に覚えていた人物像。",
          "読み直すと控えが消えている: {!r}".format(run.profile()))
    leftovers = [name for name in os.listdir(os.path.dirname(path))
                 if name.endswith(ml.TEMP_SUFFIX)]
    check(not leftovers, "書きかけが残った: {}".format(leftovers))
    # 落ちたことは握り潰さず記録する。ここだけは errors が空でないのが正しい。
    check(any("cannot write" in message for message in run.ctx.errors),
          "落ちたのに記録が無い: {}".format(run.ctx.errors))
    run.ctx.errors = []
    return run


def test_a_normal_write_leaves_no_temp_file():
    """普通に書けたときは書きかけを残さない"""
    run = Run(structured={MOD.KEY_CHANGED: "true", MOD.KEY_PROFILE: FOUND,
                          MOD.KEY_ABOUT_PLAYER: "恩義を感じている。",
                          MOD.KEY_NEW_FACTS: []})
    run.turn()
    root = os.path.join(run.tmp, "state", "npc_profiles")
    names = sorted(os.listdir(root))
    check(names == [ml.state.world_filename(run.app.world.name)],
          "世界ファイル以外が残った: {}".format(names))
    # 世界の一覧を出す診断が、書きかけを世界として数えないこと。
    check(not ml.TEMP_SUFFIX.endswith(".json"),
          "書きかけの名前が世界ファイルと同じ拡張子: {!r}".format(ml.TEMP_SUFFIX))
    return run


def test_settings_match_the_manifest():
    """`mod.json` の既定値とコードの定数が一致する

    実際に効くのはコードの定数で、GUI が出すのは `mod.json` の `default`。
    ずれると「GUI では 8 と出るのに実際は 5 で動く」になる。
    """
    folder = os.path.dirname(MOD_PATH)
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        declared = json.load(fh)["settings"]
    mismatch = [name for name, spec in declared.items()
                if getattr(MOD, name, object()) != spec["default"]]
    check(not mismatch, "mod.json とコードの既定値がずれている: {}".format(mismatch))


def test_the_quest_mod_reads_the_same_file():
    """`301_` が同じ世界ファイルを同じ規則で引ける

    mod どうしはコードを共有せず**同じ場所を読む**ことで繋がっている
    （TECH.md §3.2.3）。ファイル名の規則がずれた時点で、依頼の生成に
    人物像が載らなくなる。そのずれをここで捕まえる。
    """
    offer = load_neighbour("_quest_from_conversation")
    for world in ("灰都", "a/b:c", "  ", "." , "長" * 200):
        check(ml.state.world_filename(world) == ml.state.world_filename(world),
              "ファイル名の規則がずれた: {!r}".format(world))
    check(offer.NPC_MEMORY_DIRNAME == MOD.STATE_DIRNAME,
          "置き場所がずれた: {!r} / {!r}".format(offer.NPC_MEMORY_DIRNAME,
                                                MOD.STATE_DIRNAME))
    unknown = [key for key, _label in offer.NPC_MEMORY_FIELDS
               if key not in MOD.RECORD_KEYS]
    check(not unknown, "301_ が知らない欄を読もうとしている: {}".format(unknown))


def test_turn_survives_without_llm():
    """推論モジュールが無くてもターンは壊れない"""
    run = Run(with_llm=False)
    reply = run.turn()
    check(reply == Facilitator.reply, "返答が返らない: {!r}".format(reply))
    check(run.profiles() == {}, "何かが残った: {}".format(run.profiles()))
    return run


def test_turn_survives_a_failing_extraction():
    """抽出が例外で落ちてもターンは壊れない"""
    run = Run(raises=(MOD.MANAGER_EXTRACT,))
    reply = run.turn()
    check(reply == Facilitator.reply, "返答が返らない: {!r}".format(reply))
    check(run.app.texts[-1].startswith("傭兵ガロ: "),
          "返答が出ていない: {}".format(run.app.texts))
    check(run.profiles() == {}, "何かが残った: {}".format(run.profiles()))
    # 握り潰さずに記録していること。ここだけは errors が空でないのが正しい。
    check(any("mod_npc_profile_extract" in message for message in run.ctx.errors),
          "落ちたのに記録が無い: {}".format(run.ctx.errors))
    run.cleanup()


def main():
    for test in (test_turn_updates_profile,
                 test_extraction_runs_after_the_reply,
                 test_extraction_does_not_block_the_game_thread,
                 test_queued_extractions_are_serialized,
                 test_no_change_keeps_profile,
                 test_empty_explanation_keeps_profile,
                 test_legacy_slots_migrate_to_profile,
                 test_profile_keeps_full_llm_response,
                 test_extraction_prompt_asks_for_summary_budget,
                 test_profile_reaches_the_next_reply,
                 test_profile_reaches_keyword_calls,
                 test_profile_reaches_the_opening_line,
                 test_messages_are_not_touched,
                 test_other_npc_gets_nothing,
                 test_worlds_do_not_mix,
                 test_id_comes_from_the_world_index,
                 test_injection_leaves_a_trace,
                 test_life_log_is_not_touched,
                 test_reapply_shares_one_worker_and_one_lock,
                 test_all_five_conversation_hooks_inject_profile,
                 test_structured_output_is_used_when_available,
                 test_structured_call_follows_the_house_rules,
                 test_structured_failure_falls_back_to_text,
                 test_changed_false_keeps_the_record,
                 test_json_in_plain_text_is_read,
                 test_broken_json_changes_nothing,
                 test_about_player_is_injected_under_its_own_heading,
                 test_about_player_alone_is_enough_to_inject,
                 test_facts_accumulate_without_duplicates,
                 test_facts_are_capped,
                 test_record_keys_are_written_in_order,
                 test_legacy_slots_survive_the_migration,
                 test_a_failed_write_keeps_the_previous_file,
                 test_a_normal_write_leaves_no_temp_file,
                 test_settings_match_the_manifest,
                 test_the_quest_mod_reads_the_same_file,
                 test_turn_survives_without_llm,
                 test_turn_survives_a_failing_extraction):
        scenario(test)
    print("{} passed, {} failed".format(RESULTS["pass"], RESULTS["fail"]))
    return 1 if RESULTS["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
