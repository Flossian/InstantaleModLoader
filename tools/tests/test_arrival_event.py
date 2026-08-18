# -*- coding: utf-8 -*-
"""300_event_facility_arrival.py をゲーム抜きで通す。

    python tools/tests/test_arrival_event.py

偽の app / Facility / Character / Clock / LLM を差し込み、次を確認する。

  conversation モード … 会話フェーズの起こし方（`process_choice` に何を渡すか）、
                        手が空くまでの待ち合わせ、待機中の取り消し、第一声の読み替え
  narration    モード … 情景描写への合流（`narrator` は `move_phase` の *内側*）

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

# mod は `instantale_modloader.ui` を使う（ゲームの中では runtime/ が
# sys.path に入っている）。
# オフラインでも同じように見えるようにする。
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    """mod を **番号を除いた名前** で探し、入口ファイルのパスを返す。

    mod の入口はトップレベルでは何もしない（処理は全て apply() の中）ので、
    ゲームの外から読み込んでも安全。
    フォルダ名の番号は自分用の通し番号で、
    分類を見直すたびに振り直される（実際に 40_ -> 300_ と変わった）ので、
    番号ごと書かずに末尾で引く。
    入口のファイル名は `mod.json` が名指しする。
    """
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


MOD = find_mod("_event_facility_arrival")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Facility:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Area:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class World:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class InstantaleApp:
    def __init__(self):
        self.process_choice_calls = []

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((function, choice_text))


class ConversationStartManager:
    """本物と同じ __init__(self, app, character_id) だけを持つ偽物。"""

    def __init__(self, app, character_id):
        self.app = app
        self.character_id = character_id


def build_world():
    owner = Character(name="テストNPC C", id="64", job="inn",
                      profile="宿の主人。", personality="面倒見が良い。",
                      speech_style=None, relationship={"player": ["顔見知り"]})
    inn = Facility(id="128", name="テスト宿屋", facility_type="inn",
                   owner="64", characters=["64", "64"], description="旅人が集う宿。")
    ward = Facility(id="125", name="テスト区画", facility_type="ward",
                    owner=None, characters=[], description="広場。")
    area = Area(name="テストの町A", descriptions={"overview": "水没した都市の跡。"})
    player = Character(name="テストプレイヤー", profile="放浪の剣士。",
                       location=inn, current_area=area)
    app = InstantaleApp()
    app.player = player
    app.world = World(worldview="快楽を求める世界。", characters={"64": owner})
    app.current_narration_log = [{"action": "移動した", "narration": "静かな入口。"}]
    app.language = "japanese"
    app.is_adding_text = False
    app.is_button_enabled = True
    app.is_popup_window_opened = False
    app.current_quest_data = None
    for flag in ("in_battle", "in_boss_battle", "in_colosseum_battle", "in_conversation",
                 "in_free_input", "in_shopping", "in_action_in_conversation"):
        setattr(app, flag, False)
    return app, inn, ward


# --------------------------------------------------------------- 偽の Kivy
class FakeClock:
    """Clock の代わり。登録されたコールバックを手で回せるようにする。"""

    def __init__(self):
        self.intervals = []
        self.onces = []

    def schedule_interval(self, callback, timeout):
        self.intervals.append(callback)

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def tick(self, times=1):
        """見張りを回す。False を返したものは Clock から外れる。"""
        for _ in range(times):
            self.intervals = [cb for cb in self.intervals if cb(0.3) is not False]

    def run_onces(self):
        pending, self.onces = self.onces, []
        for callback in pending:
            callback(0.0)


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    kivy_app = types.ModuleType("kivy.app")

    class App:
        @staticmethod
        def get_running_app():
            return None      # __main__ 走査へ落ちる（本番では kivy 経由で取れる）

    kivy_app.App = App
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules["kivy.app"] = kivy_app
    return clock


def install_fake_llm(reply):
    name = "scripts.llm.request_llm_inference_llama_cpp_completion"
    module = types.ModuleType(name)
    calls = []

    def send(manager_name, message, max_tokens=None, timeout=None):
        calls.append((manager_name, message, max_tokens))
        if isinstance(reply, BaseException):
            raise reply
        return reply

    module.send_request_with_no_structure = send
    sys.modules[name] = module
    return calls


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    # ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location("arrival_mod", MOD,
                                            submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(mode="conversation", override=1.0, reply="「いらっしゃい」", **settings):
    """mod を読み込んで apply() する。

    `settings` はローダの `config.apply_to_module` と同じ扱い
    ― **apply() を呼ぶ前に**モジュールのグローバルへ書き込む。
    `mod.json` で宣言した設定が実際に効くかは、
    この順序でしか確かめられない（施設別の発生率は
    apply() の中で表に組み直しているため）。
    """
    mod = load_mod()
    mod.EVENT_MODE = mode
    mod.CHANCE_OVERRIDE = override
    for name, value in settings.items():
        assert hasattr(mod, name), name      # 打ち間違いを黙って通さない
        setattr(mod, name, value)
    ctx = FakeCtx(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
    calls = install_fake_llm(reply)
    mod.apply(ctx)
    hooks = {
        "move": ctx.hooks["__main__:MovePhaseManager.move_phase"],
        "narrate": ctx.hooks["scripts.llm.llm_manager:narrator"],
        "starter": ctx.hooks["scripts.llm.llm_manager:conversation_starter"],
    }
    return mod, ctx, calls, hooks


def do_move(hooks, narration="扉を開けると暖炉の匂いがした。"):
    """本物と同じ入れ子で1回移動する（narrator は move_phase の内側）。"""
    captured = {}

    def orig(self, *args, **kwargs):
        captured["narration"] = hooks["narrate"](lambda *a, **k: narration)
        return None

    hooks["move"](orig, object())
    return captured.get("narration")


# ============================================================ conversation
print("=== conversation モード ===")
print("1. 宿屋に到着 -> 手が空いてから会話フェーズが始まる")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app, inn, ward = build_world()
main = sys.modules["__main__"]
main.InstantaleApp = InstantaleApp
main.ConversationStartManager = ConversationStartManager
main.app_instance = app

app.is_adding_text = True          # まだテキストを流している最中
do_move(hooks)
check("見張りが Clock に載る", len(clock.intervals) == 1, len(clock.intervals))
clock.tick(3)
check("流し込み中は押さない", not app.process_choice_calls, app.process_choice_calls)

app.is_adding_text = False         # 手が空いた
clock.tick()
check("手が空いたら見張りが外れる", not clock.intervals)
check("押す処理が予約される", len(clock.onces) == 1, len(clock.onces))
clock.run_onces()
check("process_choice が1回呼ばれる", len(app.process_choice_calls) == 1,
      app.process_choice_calls)
function, choice_text = app.process_choice_calls[0]
check("ConversationStartManager のインスタンスを渡す",
      isinstance(function, ConversationStartManager), type(function).__name__)
check("character_id は NPC の id", function.character_id == "64", function.character_id)
check("choice_text は NPC の名前", choice_text == "テストNPC C", choice_text)
check("LLM を自前で呼ばない（会話はゲーム側が回す）", not calls, calls)
check("エラーを出していない", not ctx.errors, ctx.errors)

print("2. ボタンがまだ有効化されていなければ待つ")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
app.is_button_enabled = False
do_move(hooks)
clock.tick(2)
check("押さない", not app.process_choice_calls)
app.is_button_enabled = True
clock.tick()
clock.run_onces()
check("有効化されたら押す", len(app.process_choice_calls) == 1)

print("3. 待っている間に施設を出たら取り消す")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
do_move(hooks)
clock.tick()
app.player.location = ward         # プレイヤーが先に出て行った
clock.run_onces()
check("押さない", not app.process_choice_calls, app.process_choice_calls)
app.player.location = inn

print("4. 待っている間に会話・戦闘に入ったら取り消す")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
do_move(hooks)
clock.tick()
app.in_conversation = True
clock.run_onces()
check("押さない", not app.process_choice_calls, app.process_choice_calls)
app.in_conversation = False

print("5. いつまでも手が空かなければ諦める")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
app.is_adding_text = True
# 期限は launch の時点で決まるので、移動より前に縮めておく。
mod.IDLE_TIMEOUT = -1
do_move(hooks)
clock.tick()
check("見張りが外れる", not clock.intervals)
check("押さない", not app.process_choice_calls)
app.is_adding_text = False

print("6. 通路（ward）では発火しない / 同じ施設は連続しない")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
app.player.location = ward
do_move(hooks)
check("ward では待ち合わせが始まらない", not clock.intervals and not clock.onces,
      (clock.intervals, clock.onces))
app.player.location = inn
do_move(hooks)
clock.tick()
clock.run_onces()
check("宿屋では発火する", len(app.process_choice_calls) == 1)
do_move(hooks)
check("直後の再訪はクールダウンで発火しない",
      not clock.intervals and not clock.onces, (clock.intervals, clock.onces))

print("6b. 同じ施設の間引きは「その施設に入った回数」で数える")
# 出入りを繰り返しても、間に他所へ寄っても、数えるのはこの施設への訪問だけ。
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(override=1.0, COOLDOWN_VISITS=2)
app.process_choice_calls = []


def enter(facility):
    app.player.location = facility
    do_move(hooks)
    clock.tick()
    clock.run_onces()


enter(inn)
check("1回目は出る", len(app.process_choice_calls) == 1, app.process_choice_calls)
enter(ward)                      # 別の場所を挟んでも宿の回数は増えない
enter(inn)
check("2回目は出ない", len(app.process_choice_calls) == 1, app.process_choice_calls)
enter(inn)
check("3回目も出ない", len(app.process_choice_calls) == 1, app.process_choice_calls)
enter(inn)
check("4回目で出る", len(app.process_choice_calls) == 2, app.process_choice_calls)

# 0 なら毎回抽選する。
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(override=1.0, COOLDOWN_VISITS=0)
app.process_choice_calls = []
enter(inn)
enter(inn)
check("0 にすると毎回出る", len(app.process_choice_calls) == 2,
      app.process_choice_calls)
app.player.location = inn

print("7. 会話中・戦闘中は発火しない")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
app.in_battle = True
do_move(hooks)
check("待ち合わせが始まらない", not clock.intervals and not clock.onces,
      (clock.intervals, clock.onces))
app.in_battle = False

print("7b. in_shopping は発火を止めない（実測で居座るため除外した）")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app.process_choice_calls = []
app.in_shopping = True             # 店の外を歩いていても True のままになる
do_move(hooks)
clock.tick()
clock.run_onces()
check("買い物フラグが立っていても発火する", len(app.process_choice_calls) == 1,
      app.process_choice_calls)
app.in_shopping = False

print("7c. mod.json の施設別発生率が効く")
# 宿だけ 0 にする（利用者が「宿では出さない」を選んだ状態）。
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(override=None, CHANCE_INN=0.0, CHANCE_GENERAL_STORE=1.0)
app.process_choice_calls = []
app.player.location = inn
do_move(hooks)
check("宿は 0 なので発火しない", not clock.intervals, clock.intervals)
# 同じ設定のまま、1.0 にした種別では出ること（表が実際に引かれている証拠）。
store = Facility(id="129", name="テスト雑貨屋", facility_type="general_store",
                 owner="64", characters=["64"], description="雑貨屋。")
app.player.location = store
do_move(hooks)
clock.tick()
clock.run_onces()
check("1.0 にした種別では発火する", len(app.process_choice_calls) == 1,
      app.process_choice_calls)
app.player.location = inn

print("8. 確率0なら発火しない")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(override=0.0)
app.process_choice_calls = []
do_move(hooks)
check("待ち合わせが始まらない", not clock.intervals and not clock.onces,
      (clock.intervals, clock.onces))

print("9. process_choice が落ちても移動は通る")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()


def boom(function, choice_text=""):
    raise RuntimeError("phase machine said no")


app.process_choice = boom
do_move(hooks)
clock.tick()
clock.run_onces()
check("例外は記録されるが外へ出ない", bool(ctx.errors), ctx.errors)
app.process_choice = types.MethodType(InstantaleApp.process_choice, app)
app.process_choice_calls = []

print("10. 第一声の読み替え")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
do_move(hooks)
clock.tick()
clock.run_onces()
original = [{"role": "user", "content": "<行動: 話しかける>"}]
seen = {}
hooks["starter"](lambda messages, *a, **k: seen.setdefault("messages", messages),
                 original, "life_log", "player")
check("最後のメッセージが差し替わる",
      "あなたの方から声をかけた" in seen["messages"][-1]["content"],
      seen["messages"][-1]["content"])
check("元の messages は書き換えない",
      original[-1]["content"] == "<行動: 話しかける>", original)
seen.clear()
hooks["starter"](lambda messages, *a, **k: seen.setdefault("messages", messages),
                 original, "life_log", "player")
check("印は1回で使い切る（次の会話は素通し）",
      seen["messages"][-1]["content"] == "<行動: 話しかける>", seen["messages"])

# ============================================================== narration
print()
print("=== narration モード ===")
print("11. 情景描写にセリフが足される（narrator は move_phase の内側）")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(mode="narration")
app.process_choice_calls = []
out = do_move(hooks)
check("元の情景描写が残る", out.startswith("扉を開けると"), out)
check("セリフが足される", "「いらっしゃい」" in out, out)
check("LLM を1回呼ぶ", len(calls) == 1, len(calls))
check("manager 名が mod_arrival_event", calls[0][0] == "mod_arrival_event")
check("プロンプトに施設名が入る", "テスト宿屋" in calls[0][1][0]["content"])
check("会話フェーズは起こさない", not app.process_choice_calls)

print("12. 移動していなければ足さない（narrator 単独）")
out12 = hooks["narrate"](lambda *a, **k: "剣を研いだ。")
check("足されない", out12 == "剣を研いだ。", out12)

print("13. LLM が失敗しても情景描写は壊れない")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(mode="narration", reply=RuntimeError("llama down"))
out13 = do_move(hooks)
check("元の情景描写がそのまま返る", out13 == "扉を開けると暖炉の匂いがした。", out13)
check("例外は記録される", bool(ctx.errors), ctx.errors)

print("14. 生成文の整形")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(mode="narration",
                               reply="テストNPC C: いらっしゃい。\n（彼女は笑った）")
out14 = do_move(hooks)
check("1行目だけ採る", "彼女は笑った" not in out14, out14)
check("鉤括弧が付く", "「テストNPC C: いらっしゃい。」" in out14, out14)

print("15. narrator が str 以外を返す版でも壊れない")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(mode="narration")


class Resp:
    def __init__(self):
        self.text = "扉を開けた。"


captured = {}


def orig_obj(self, *a, **k):
    captured["r"] = hooks["narrate"](lambda *a2, **k2: Resp())
    return None


hooks["move"](orig_obj, object())
check(".text に足される", "「" in captured["r"].text, captured["r"].text)

clock = install_fake_kivy()
mod, ctx, calls, hooks = setup(mode="narration")


def orig_dict(self, *a, **k):
    captured["d"] = hooks["narrate"](lambda *a2, **k2: {"text": "扉を開けた。"})
    return None


hooks["move"](orig_dict, object())
check("dict['text'] に足される", "「" in captured["d"]["text"], captured["d"])

print("16. 現在地が違う主は話者に選ばない")
clock = install_fake_kivy()
mod, ctx, calls, hooks = setup()
app, inn, ward = build_world()
main = sys.modules["__main__"]
main.InstantaleApp = InstantaleApp
main.ConversationStartManager = ConversationStartManager
main.app_instance = app
owner = app.world.characters["64"]
owner.location = ward          # 名簿には居るが、今は別の施設にいる
app.process_choice_calls = []
do_move(hooks)
clock.tick()
clock.run_onces()
check("現在地外の主では会話を起こさない", not app.process_choice_calls,
      app.process_choice_calls)
owner.location = None          # 現在地が読めない相手は名簿を信じる
app.process_choice_calls = []
do_move(hooks)
clock.tick()
clock.run_onces()
check("現在地不明の主なら従来どおり起こす",
      len(app.process_choice_calls) == 1, app.process_choice_calls)

print("17. llama_cpp が無くても any_server で narration できる")
clock = install_fake_kivy()
# llama_cpp を外して any_server だけ載せる
sys.modules.pop("scripts.llm.request_llm_inference_llama_cpp_completion", None)
any_name = "scripts.llm.request_llm_inference_any_server"
any_mod = types.ModuleType(any_name)
any_calls = []


def any_send(manager_name, message, max_tokens=None, timeout=None):
    any_calls.append((manager_name, message, max_tokens))
    return "「奥からどうぞ」"


any_mod.send_request_with_no_structure = any_send
sys.modules[any_name] = any_mod
mod, ctx, calls, hooks = setup(mode="narration")
# setup() が llama_cpp を載せ直すので、apply 後に差し替える
sys.modules.pop("scripts.llm.request_llm_inference_llama_cpp_completion", None)
sys.modules[any_name] = any_mod
out17 = do_move(hooks)
check("any_server 経由でセリフが足される", "「奥からどうぞ」" in out17, out17)
check("any_server が呼ばれる", len(any_calls) == 1, any_calls)

print("18. 名前を知らないプロバイダでもセリフが作れる")
# Gemini / OpenAI / Claude は `llama_cpp` / `any_server` の2名リストに無い。
# 名指しの一覧を持っていた版は、これらの環境で毎回空振りしていた。
clock = install_fake_kivy()
for stale in list(sys.modules):
    if stale.startswith("scripts.llm.request_llm_inference_"):
        sys.modules.pop(stale, None)
gemini_name = "scripts.llm.request_llm_inference_gemini_test_streaming"
gemini_mod = types.ModuleType(gemini_name)
gemini_calls = []


def gemini_send(manager_name, message, max_tokens=None, timeout=None):
    gemini_calls.append((manager_name, message, max_tokens, timeout))
    return "「ようこそ、旅の方」"


gemini_mod.send_request_with_no_structure = gemini_send
sys.modules[gemini_name] = gemini_mod
mod, ctx, calls, hooks = setup(mode="narration")
sys.modules.pop("scripts.llm.request_llm_inference_llama_cpp_completion", None)
sys.modules[gemini_name] = gemini_mod
out18 = do_move(hooks)
check("知らないプロバイダ経由でもセリフが足される",
      "「ようこそ、旅の方」" in out18, out18)
check("そのプロバイダが呼ばれる", len(gemini_calls) == 1, gemini_calls)
check("timeout を必ず渡している",
      bool(gemini_calls) and gemini_calls[0][3] == mod.LINE_TIMEOUT, gemini_calls)

print("19. llm_manager の別名があればそちらを先に使う")
# 送信モジュールを名指しせずに済む本筋の経路（GAME.md §2.12）。
clock = install_fake_kivy()
manager_name_mod = "scripts.llm.llm_manager"
manager_mod = types.ModuleType(manager_name_mod)
manager_calls = []


def manager_send(manager_name, message, max_tokens=None, timeout=None):
    manager_calls.append((manager_name, message, max_tokens, timeout))
    return "「いらっしゃい」"


manager_mod.send_request_with_no_structure = manager_send
mod, ctx, calls, hooks = setup(mode="narration")
sys.modules[manager_name_mod] = manager_mod
out19 = do_move(hooks)
check("別名経由でセリフが足される", "「いらっしゃい」" in out19, out19)
check("別名が呼ばれ、送信モジュールは呼ばれない", len(manager_calls) == 1, manager_calls)
sys.modules.pop(manager_name_mod, None)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("全て通過")
