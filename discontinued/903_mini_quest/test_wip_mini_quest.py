# -*- coding: utf-8 -*-
"""903_mini_quest.py をゲーム抜きで通す（開発終了。DOC.md 冒頭を見ること）。

    python discontinued/903_mini_quest/test_wip_mini_quest.py

CI では走らない（`discontinued/` は検査もビルドも通らない。TECH.md §2.6）。
`301_` / `302_` との共存だけは `runtime/mods` の実体を見るので、
向こうが動いているあいだはこの検査も通り続ける。

偽の app / PhaseSpec / DisplayQuestChoice / HUD / Clock /
LlamaCppClient を差し込み、次を確認する。

  設置     … 掲示板の「やめる」の手前に「軽い頼まれごとを探す」が出る。
             開き直しても二重にならない。ゲームが並べた依頼ボタンには触らない
  押下     … 印で横取りしてゲームの経路（`process_choice`）に乗せる。
             `PhaseSpec` には自前クラス名を書かない
  生成     … ゲーム自身の `generate_random_quest()` を呼び、`area_description` に
             お題を足し、生成プロンプトの【討伐】を種類の札に差し替える
  控え     … `out/test/state/mini_quests.json` に残り、**セーブには触らない**
  進行     … 控えに在るクエストのときだけ referee プロンプトを書き換える。
             書き換えは system と user に**分かれて**入る（1メッセージに揃っていない）
  素通し   … 控えに無いクエスト・イベント処理・要約のプロンプトは1バイトも変えない
  目印切れ … ゲーム側の文面が変わったら書き換えを丸ごと諦める（中途半端に送らない）
  実データ … `output_data/` があるなら、記録済みの実プロンプト全件に目印が在ることを
             確かめる（無ければその項目だけ飛ばす）
  共存     … `301_` / `302_` と印のキーが衝突していない

ゲームが起動していなくても走るので、mod を編集したらまずこれを通すこと。
"""
import glob
import importlib.util
import json
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def local_mod():
    """この検査と同じフォルダに置いてある mod の入口。

    `903_mini_quest` は開発終了で `discontinued/` へ出したので、
    `runtime/mods` を探しても見つからない（TECH.md §2.6）。
    共存の相手（`301_` / `302_`）だけは runtime に居るので `find_mod` で引く。
    """
    with io.open(os.path.join(HERE, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(HERE, json.load(fh)["entry"])


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


MOD = local_mod()
OFFER_MOD = find_mod("_quest_from_conversation")
PARTY_MOD = find_mod("_leave_party_in_conversation")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Quest:
    def __init__(self, **kw):
        self.config = {"status": "incomplete"}
        self.__dict__.update(kw)


class World:
    def __init__(self, quests):
        self.quests = quests


class PhaseSpec:
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class JustSetButtonToNormalPhase:
    """自前ボタンに持たせる無害な spec の相手。mod 無しで押されても害が無い。"""

    def __init__(self, app, *args):
        self.app = app

    def execute(self, choice_text):
        self.app.harmless += 1
        return None


class QuestChoiceManager:
    def __init__(self, app, quest_type, quest_id):
        self.app = app
        self.quest_type = quest_type
        self.quest_id = quest_id

    def execute(self, choice_text):
        self.app.accepted.append((self.quest_type, self.quest_id))
        return None


class DisplayQuestChoice:
    """ゲーム自身のクエスト掲示板。依頼ボタンを並べるところまでを真似る。"""

    #: 生成させる依頼の題名と依頼文。
    #: テストから差し替える。
    next_title = "谷底の薬草採り"
    next_summary = "谷底に自生する月光草を5株、採ってきてほしい。"

    def __init__(self, app):
        self.app = app

    def execute(self, choice_text):
        self.app.opened_board += 1
        self.update_button_display()
        return None

    def update_button_display(self):
        self.app.buttons = [
            {"text": "【39】水底の警備",
             "spec": PhaseSpec("QuestChoiceManager", ["settlement_quest", "39"])},
            {"text": "【43】霧の追跡",
             "spec": PhaseSpec("QuestChoiceManager", ["settlement_quest", "43"])},
            {"text": "やめる", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
        ]
        self.app.refresh_choice_buttons(reset_page=True)

    def generate_random_quest(self):
        """ゲーム自身の生成経路。

        本物は内側で `random_quest_generator` を呼び、
        その中で LLM（`LlamaCppClient.chat`）が回る。
        mod のフックが**その順で**効くことを確かめたいので、ここでも同じ順に呼ぶ。
        """
        if self.app.generator_hook is not None:
            self.app.generator_hook("世界の概要", "風鳴りの村", "静かな農村",
                                    "丘陵地帯", "風が吹き抜ける丘陵地帯。", 6)
        new_id = str(max(int(k) for k in self.app.world.quests) + 1)
        quest = Quest(id=new_id, quest_title=type(self).next_title,
                      request_summary=type(self).next_summary,
                      client_name="テスト依頼人B", difficulty=6,
                      neighboring_settlement_id="7")
        self.app.world.quests[new_id] = quest
        self.app.world_dict["quests"][new_id] = {
            "id": new_id, "quest_title": type(self).next_title}
        return quest


class InstantaleApp:
    def __init__(self, world):
        self.world = world
        self.world_dict = {"world_data": {"world_name": "テスト世界"},
                           "quests": {}}
        self.buttons = []
        self.display_button_map = None
        self.to_display_buttons = []
        self.texts = []
        self.opened_board = 0
        self.refreshes = 0
        self.loaded = []
        self.harmless = 0
        self.accepted = []
        self.process_choice_calls = []
        self.pressed_by_game = []
        self.generator_hook = None
        self.generated_area_descriptions = []
        self.is_button_enabled = True
        self.hud = HUD_CLS()

    def add_text(self, context):
        self.texts.append(context)

    def process_choice(self, function, choice_text=""):
        self.process_choice_calls.append((type(function).__name__, choice_text))
        return function.execute(choice_text)

    def refresh_choice_buttons(self, reset_page=False):
        self.refreshes += 1
        self.to_display_buttons = [entry["text"] for entry in self.buttons]

    def display_button_load(self, dt):
        self.loaded.append(list(self.to_display_buttons))

    def on_button_press(self, button_index):
        """ゲーム本来の押下処理。**spec からマネージャを組んで process_choice に渡す。**"""
        entry = self.buttons[button_index]
        text = entry.get("text")
        self.pressed_by_game.append(text)
        data = entry["spec"].to_dict()
        cls = getattr(sys.modules["__main__"], data["cls_name"], None)
        if cls is None:
            return None
        return self.process_choice(cls(self, *data["args"]), text)


class LlamaCppClient:
    """`chat` の相手。mod のラッパが実際に何を送ったかを控える。"""

    def __init__(self):
        self.sent = []

    def chat(self, model, messages, format=None, **kwargs):
        self.sent.append([dict(m) for m in messages])
        return {"message": {"content": "{}"}}


BASES = {"app": InstantaleApp, "board": DisplayQuestChoice,
         "client": LlamaCppClient}


class FakeClock:
    def __init__(self):
        self.intervals = []
        self.onces = []

    def schedule_interval(self, callback, timeout):
        self.intervals.append(callback)

    def schedule_once(self, callback, timeout=0):
        self.onces.append(callback)

    def tick(self, times=1):
        for _ in range(times):
            self.intervals = [cb for cb in self.intervals if cb(0.3) is not False]

    def run_onces(self):
        for _ in range(8):
            pending, self.onces = self.onces, []
            if not pending:
                return
            for callback in pending:
                callback(0.0)

    def settle(self, times=3):
        for _ in range(times):
            self.run_onces()
            self.tick()
        self.run_onces()


def install_fake_hud():
    name = "scripts.hud.new_hud"
    module = types.ModuleType(name)

    class InstanTaleHUD:
        def __init__(self):
            self.painted = []

        def update_button_texts(self, instance, value):
            self.painted.append(list(value))

    module.InstanTaleHUD = InstanTaleHUD
    sys.modules[name] = module
    return InstanTaleHUD


def install_fake_kivy():
    clock = FakeClock()
    kivy = types.ModuleType("kivy")
    kivy_clock = types.ModuleType("kivy.clock")
    kivy_clock.Clock = clock
    sys.modules["kivy"] = kivy
    sys.modules["kivy.clock"] = kivy_clock
    sys.modules.pop("kivy.app", None)
    return clock


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []
        self.logs = []

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

    def state_path(self, *parts):
        """永続データの置き場。本番と同じく out/ とは**別のフォルダ**にする。"""
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    # 本物の `ctx.write_json` / `write_text` / `read_json` と同じものを使う。
    # ここを自前の open にすると、
    # テストだけが「壊れない書き方」「読めなかったことを記録する読み方」を通らなくなる。
    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def write_text(self, path, text):
        return ml.write_text(path, text, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator

    # `llm.wrap_outgoing` はここを見て「今その名前が在るか」を決める。
    # 返さないとクラウド側の別名が全部「後生え待ち」に回り、**この検査でクラウド経路が一度も通らなくなる**（版4がクラウドで無音だったのと同じ死角をテストに作る）。
    def resolve(self, target):
        return (None, target.rpartition(":")[2], object())

    def superseded(self):
        return False


def load_mod(path=MOD, name="mini_quest_mod"):
    spec = importlib.util.spec_from_file_location(name, path,
                                            submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(hooks, targets):
    for target, owner, name in targets:
        hook = hooks.get(target)
        if hook is None:
            continue
        original = getattr(owner, name)

        def make(hook=hook, original=original):
            def method(self, *args, **kwargs):
                return hook(original, self, *args, **kwargs)
            return method

        setattr(owner, name, make())


HUD_CLS = install_fake_hud()
CLOCK = install_fake_kivy()


def setup(records=None):
    """mod を適用し、掲示板の前に立っている app を返す。

    クラスは毎回作り直す（前のテストで載せたフックを持ち越さない。
    派生元は `BASES` から引く ― `sys.modules['__main__']` は直接実行時にはこのテスト自身なので、
    名前から派生させると層が積み上がる）。
    """
    app_cls = type("InstantaleApp", (BASES["app"],), {})
    board_cls = type("DisplayQuestChoice", (BASES["board"],), {})
    client_cls = type("LlamaCppClient", (BASES["client"],), {})

    main = sys.modules["__main__"]
    main.InstantaleApp = app_cls
    main.DisplayQuestChoice = board_cls
    main.QuestChoiceManager = QuestChoiceManager
    main.JustSetButtonToNormalPhase = JustSetButtonToNormalPhase
    main.PhaseSpec = PhaseSpec

    record_path = os.path.join(OUT_DIR, "state", "mini_quests.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    # ログは追記なので、
    # 消しておかないと**前回の実行の行を数えてしまう**（「1度だけ記録する」の判定が実行のたびに増えて落ちた）。
    log_path = os.path.join(OUT_DIR, "mini_quest.log")
    if os.path.exists(log_path):
        os.remove(log_path)
    if records is None:
        if os.path.exists(record_path):
            os.remove(record_path)
    else:
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False)

    mod = load_mod()
    ctx = FakeCtx(OUT_DIR)
    mod.apply(ctx)
    install(ctx.hooks, (
        ("__main__:InstantaleApp.on_button_press", app_cls, "on_button_press"),
        ("__main__:DisplayQuestChoice.update_button_display", board_cls,
         "update_button_display"),
        ("llama_cpp_runtime_completion:LlamaCppClient.chat", client_cls, "chat"),
    ))

    world = World({"39": Quest(id="39", quest_title="水底の警備"),
                   "43": Quest(id="43", quest_title="霧の追跡")})
    app = app_cls(world)
    app.world_dict["quests"] = {"39": {"id": "39"}, "43": {"id": "43"}}

    # `random_quest_generator` のフックは `(orig, ...)` を取るので、
    # 素の生成側を `orig` として渡す形にしておく（本番と同じ呼ばれ方）。
    raw = ctx.hooks.get(
        "scripts.llm.llm_manager_world_generate:random_quest_generator")

    def plain_generator(world_overview, settlement_name, settlement_overview,
                        structure, area_description, difficulty, *a, **kw):
        app.generated_area_descriptions.append(area_description)
        return {"quest_title": board_cls.next_title,
                "request_summary": board_cls.next_summary}

    def generator(*args, **kwargs):
        if raw is None:
            return plain_generator(*args, **kwargs)
        return raw(plain_generator, *args, **kwargs)

    app.generator_hook = generator
    client = client_cls()
    return mod, ctx, app, board_cls, client


def referee_messages(title):
    """本物と同じ形（system 2本 + user 1本）の進行プロンプト。"""
    mod = load_mod(MOD, "mini_quest_probe")
    return [
        {"role": "system", "content": "必ず日本語で、以下のjson形式で出力すること: {...}"},
        {"role": "system", "content": mod.SAMPLE_REFEREE_SYSTEM},
        {"role": "user",
         "content": mod.SAMPLE_REFEREE_USER.replace("テスト依頼C", title)},
    ]


def generator_messages():
    mod = load_mod(MOD, "mini_quest_probe")
    return [
        {"role": "system", "content": mod.SAMPLE_GENERATOR},
        {"role": "user", "content": "【世界のデータ】\n- 世界の概要: ..."},
    ]


def blob_of(messages):
    return "\n".join(m.get("content", "") for m in messages)


# ============================================================ 書き換えの中身
print("=== 書き換えの中身（純粋関数） ===")
MOD_MODULE = load_mod(MOD, "mini_quest_pure")

for mode in ("none", "mobs"):
    check("自己検証が通る ({})".format(mode),
          MOD_MODULE.check_samples(mode) == [],
          MOD_MODULE.check_samples(mode))

blob = MOD_MODULE.SAMPLE_REFEREE_SYSTEM + MOD_MODULE.SAMPLE_REFEREE_USER
check("実プロンプトの写しに目印が全て在る",
      MOD_MODULE.referee_anchors_missing(blob) == [],
      MOD_MODULE.referee_anchors_missing(blob))
check("目印が欠けたら見逃さない",
      MOD_MODULE.referee_anchors_missing(
          blob.replace("- ラスボスが残っている限りクエストは攻略完了しない。", "")) != [])

head = MOD_MODULE.rewrite_referee_text(
    MOD_MODULE.SAMPLE_REFEREE_SYSTEM, "薬草を集める。", None, "月光草を5株。")
body = MOD_MODULE.rewrite_referee_text(
    MOD_MODULE.SAMPLE_REFEREE_USER, "薬草を集める。", None, "月光草を5株。")
check("system 側: ラスボス宣言が消える",
      MOD_MODULE.REFEREE_HEAD_END not in head)
check("system 側: お題が入る", "薬草を集める。" in head)
check("user 側: 完了条件が討伐から外れる",
      "クエストを攻略した後にのみ実行可能" not in body)
check("user 側: battle 優先の指示が消える",
      "戦闘ベースのRPGなので" not in body)
check("user 側: ラスボス邂逅が禁じられる",
      "encounter_final_boss は決して選ばない" in body)
check("user 側: 触っていない行はそのまま残る",
      "- battleとfield_eventは同時に実行できない。" in body)
check("user 側: クエストの構造は壊さない",
      "- quest_title: テスト依頼C" in body)

mobs = MOD_MODULE.rewrite_referee_text(MOD_MODULE.SAMPLE_REFEREE_USER, "お題。", "mobs")
check("mobs モードでは戦闘を全否定しない",
      "battleは選ばず" not in mobs and "道中の障害" in mobs)
check("mobs モードでもラスボス邂逅は禁じる",
      "encounter_final_boss は決して選ばない" in mobs)

check("お題のうち1つは採集", any(k["key"] == "gather" for k in MOD_MODULE.KINDS))
check("お題のうち1つは救助", any(k["key"] == "rescue" for k in MOD_MODULE.KINDS))
check("お題のうち1つは偵察", any(k["key"] == "scout" for k in MOD_MODULE.KINDS))

gen = MOD_MODULE.rewrite_generator(MOD_MODULE.SAMPLE_GENERATOR, MOD_MODULE.KINDS[1])
check("生成: 【討伐】が種類の札に変わる",
      "【救助】" in gen and "【討伐】" not in gen, gen)
check("生成: 敵の生成要素には触らない（札以外は同じ長さ）",
      len(gen) == len(MOD_MODULE.SAMPLE_GENERATOR), (len(gen),
                                                     len(MOD_MODULE.SAMPLE_GENERATOR)))
brief = MOD_MODULE.generator_brief(MOD_MODULE.KINDS[0])
check("生成: boss は残す指示（Literal を空にしないため）",
      "boss は指示どおり1体設定する" in brief, brief)
check("生成: normal も残す（同上）", "enemies は normal のみ" in brief, brief)
check("生成: miniboss は作らせない（在庫過剰の是正）",
      "miniboss は1体も設定しないこと" in brief, brief)
check("生成: イベントを多めに作らせる（在庫切れの是正）",
      MOD_MODULE.EVENT_COUNT in brief, brief)
check("生成: 討伐を達成条件にしない指示が入っている",
      "敵の討伐を達成条件にしてはならない" in brief)
check("生成: 達成条件を判定可能な形で書かせる",
      "誰が読んでも判定できる形" in brief, brief)

# ---- 実機で壊れていた3点の回帰（VERIFICATION_LOG.md §2.19）
print()
print("=== 実機で壊れていた3点（回帰） ===")
check("進行: 達成条件（request_summary）が本文に入る",
      "月光草を5株。" in head, head)
check("進行: 在庫を消化しなくてよいと明言する",
      "消化する必要は一切無い" in head, head)
check("進行: ターンの締切を与える",
      str(MOD_MODULE.TURN_BUDGET) in head and "ターン以内" in head, head)
check("進行: 足踏みの繰り返しを禁じる",
      "前のターンと似た描写を繰り返してはならない" in body, body)
check("進行: 進展が無いなら帰還を選ばせる",
      "進展の無い状況が2ターン続いたなら" in body, body)
check("進行: ダンジョン要素（在庫の実数）は書き換えない",
      "- 残りラスボス戦闘: **['テストボスB']**" in body, body)

# ---- 達成したのに「撤退」になった件の回帰（VERIFICATION_LOG.md §2.20）
check("進行: 達成済みなら帰還を撤退にしない（retire の説明を上書き）",
      "その場合は必ず return_after_completion を選ぶこと" in body, body)
check("進行: 撤退は「目的を果たせないまま」に限定される",
      "**目的を果たせないまま**クエストを放棄して帰還する" in body, body)
check("進行: エリア外への移動＝撤退、という定義を外す",
      "クエスト攻略を諦めての撤退以外では" not in body, body)
check("進行: 帰還時にどちらかを判断させる",
      "まず【このクエストの目的】が果たされているかを判断すること" in body, body)
check("進行: 1文目でも帰還の解釈を示す",
      "達成としての帰還" in head, head)
check("撤退そのものは残す（達成できない依頼で詰ませない）",
      "retire_from_the_quest" in body, body)

# retire の行は**行頭だけ**で当てる。
# ゲームの原文には誤植（「しかし具体的が無いならば」）があり、
# `111_llm_prompt_replace` の同梱ルールがそれを直すので、
# 送信される文の末尾は 111_ を入れているかどうかで変わる（VERIFICATION_LOG.md
# §2.43）。
# 末尾まで含めた完全一致にしていたときは、直された側で当たらなくなっていた。
FIXED_TYPO = MOD_MODULE.SAMPLE_REFEREE_USER.replace(
    "しかし具体的が無いならば", "しかし具体的な理由が無いならば")
check("見本に誤植が在る（この回帰の前提。ゲームが直したらここが落ちる）",
      FIXED_TYPO != MOD_MODULE.SAMPLE_REFEREE_USER)
check("進行: retire の行は末尾が変わっても当たる（111_ が誤植を直した後の文）",
      MOD_MODULE.referee_anchors_missing(
          MOD_MODULE.SAMPLE_REFEREE_SYSTEM + FIXED_TYPO) == [],
      MOD_MODULE.referee_anchors_missing(
          MOD_MODULE.SAMPLE_REFEREE_SYSTEM + FIXED_TYPO))
fixed_body = MOD_MODULE.rewrite_referee_text(FIXED_TYPO, "お題。", None, "見本。")
check("進行: 末尾が変わっていても説明ごと差し替わる",
      "その場合は必ず return_after_completion を選ぶこと" in (fixed_body or ""),
      fixed_body)
check("進行: 差し替えた行にゲームの原文が残らない",
      "さっさと撤退させること" not in (fixed_body or ""), fixed_body)

# ---- 在庫が尽きた終盤にラスボス戦へ流れた件の回帰（VERIFICATION_LOG.md §2.21）
check("進行: battle 行に「ラスボスを含めるな」を書き足す",
      "ラスボスを enemies に含めてはならない" in body, body)
check("進行: 残りラスボス戦闘の行に「戦わない」と注記する",
      "このクエストでは戦わない" in body, body)
check("進行: 行頭で当てる規則は数値のゆらぎを吸収する（末尾の適正数は6通り）",
      MOD_MODULE.rewrite_referee_text(
          MOD_MODULE.SAMPLE_REFEREE_USER.replace("平均1.6体", "平均1.2体"),
          "お題。", None, "見本。").count("ラスボスを enemies に含めてはならない") == 1)
check("進行: 在庫の実数そのものは書き換えない（追記のみ）",
      "- 残りラスボス戦闘: **['テストボスB']**（" in body, body)
check("書き換えは冪等（同じ行に二度足さない）",
      MOD_MODULE.rewrite_referee_text(body, "お題。", None, "見本。") is None)

# ---- 達成しても撤退になる件（文面では直らない。VERIFICATION_LOG.md §2.22）
print()
print("=== 撤退を達成に差し替える（戻り値の側） ===")


class _Obj(object):
    pass


def _retire_dict():
    return {"game_master_statement": {"narration": "x",
                                      "turn_resolution": {"type": "retire_from_the_quest"}}}


def _retire_obj():
    resolution = _Obj(); resolution.type = "retire_from_the_quest"
    statement = _Obj(); statement.turn_resolution = resolution
    top = _Obj(); top.game_master_statement = statement
    return top


r = _retire_dict()
check("dict 形の戻り値を差し替えられる",
      MOD_MODULE.retire_to_return(r)
      and r["game_master_statement"]["turn_resolution"]["type"] == "return_after_completion", r)
o = _retire_obj()
check("オブジェクト形の戻り値を差し替えられる",
      MOD_MODULE.retire_to_return(o)
      and o.game_master_statement.turn_resolution.type == "return_after_completion")
other = {"game_master_statement": {"turn_resolution": {"type": "battle"}}}
check("撤退以外は触らない",
      MOD_MODULE.retire_to_return(other) is False
      and other["game_master_statement"]["turn_resolution"]["type"] == "battle", other)
check("知らない形なら何もしない（推測して壊さない）",
      MOD_MODULE.retire_to_return(object()) is False)
check("形が読めなければ素通し（None も落ちない）",
      MOD_MODULE.retire_to_return({"game_master_statement": None}) is False)
check("既定で有効（文面での説得は効かないので戻り値で持つ）",
      MOD_MODULE.RETURN_INSTEAD_OF_RETIRE is True)

# 実際のフック経由。
# 控えに在る依頼のときだけ差し替わること。
_RETIRE_RECORDS = {"テスト世界": {"44": {"kind": "gather", "label": "採集",
                                         "title": "谷底の薬草採り",
                                         "summary": "月光草を5株、採ってきてほしい。",
                                         "objective": "薬草を集めて持ち帰る。"}}}
mod, ctx, app, board_cls, client = setup(_RETIRE_RECORDS)
hook = ctx.hooks["scripts.llm.llm_manager:quest_referee_with_free_action"]


def _orig_retire(quest_data, *a, **kw):
    return _retire_dict()


ours = hook(_orig_retire, {"quest_title": "谷底の薬草採り"}, [], {}, [], None, {}, {}, "帰る")
check("控えに在る依頼なら差し替わる",
      ours["game_master_statement"]["turn_resolution"]["type"] == "return_after_completion",
      ours)
theirs = hook(_orig_retire, {"quest_title": "尾根路のカラス退治"}, [], {}, [], None, {}, {}, "帰る")
check("控えに無い依頼は撤退のまま（ゲーム本来の依頼を壊さない）",
      theirs["game_master_statement"]["turn_resolution"]["type"] == "retire_from_the_quest",
      theirs)
with open(os.path.join(OUT_DIR, "mini_quest.log"), encoding="utf-8") as _fh:
    _log_lines = _fh.read().splitlines()
check("差し替えを1行残す",
      any("retire -> return_after_completion" in line for line in _log_lines))
check("戻り値の形を1度だけ記録する",
      sum(1 for line in _log_lines if "result shape =" in line) == 1,
      [line for line in _log_lines if "result shape =" in line])
check("例外を出していない", ctx.errors == [], ctx.errors)
check("達成条件が無くても書き換えは成立する（古い控え対策）",
      "【依頼の内容" not in (MOD_MODULE.rewrite_referee_text(
          MOD_MODULE.SAMPLE_REFEREE_SYSTEM, "お題。", None, "") or ""))

# ============================================================ 掲示板への設置
print()
print("=== 掲示板への設置 ===")
mod, ctx, app, board_cls, client = setup()
board = board_cls(app)
board.update_button_display()
CLOCK.settle()

texts = [entry["text"] for entry in app.buttons]
check("掲示板に出る", mod.BOARD_LABEL in texts, texts)
check("「やめる」の手前に出る",
      texts.index(mod.BOARD_LABEL) == texts.index("やめる") - 1, texts)
check("ゲームが並べた依頼ボタンは1件も減らない",
      sum(1 for e in app.buttons
          if getattr(e.get("spec"), "cls_name", "") == "QuestChoiceManager") == 2)
check("自前ボタンの spec は無害な既存クラス",
      app.buttons[texts.index(mod.BOARD_LABEL)]["spec"].cls_name
      == "JustSetButtonToNormalPhase")
check("次のフレームで HUD まで塗る",
      any(mod.BOARD_LABEL in painted for painted in app.hud.painted),
      app.hud.painted)

board.update_button_display()
CLOCK.settle()
texts = [entry["text"] for entry in app.buttons]
check("開き直しても二重にならない", texts.count(mod.BOARD_LABEL) == 1, texts)

# セーブから復元された残骸（印が落ちている）を掴めること。
# 掲示板が一覧を組み直すビルドではゲーム自身が消しているので**保険**だが、
# 組み直さないビルドで二重化しないことをここで担保する。
from instantale_modloader import ui as _ui
screen = _ui.Screen(ctx, lambda m: None, tag="t", mark=mod.MARK)
restored = [{"text": mod.BOARD_LABEL,
             "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
check("印の落ちた自前ボタンを残骸として掴む",
      screen.prune_stale(restored, mod.OUR_LABELS) == [mod.BOARD_LABEL], restored)
check("掲示板の掃除が仕掛けてある",
      "prune_stale(buttons, OUR_LABELS)" in open(MOD, encoding="utf-8").read())

# 他 MOD の生きているボタン（印のキーだけが違う）を巻き込まないこと。
foreign = [{"text": mod.BOARD_LABEL, "mod_pardon_action": "open",
            "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
check("他の MOD の印が付いたボタンは落とさない",
      screen.prune_stale(foreign, mod.OUR_LABELS) == [], foreign)
check("掃除に使う文言はこちらにしか無いものだけ",
      mod.OUR_LABELS == (mod.BOARD_LABEL,), mod.OUR_LABELS)

# ============================================================ 押下と生成
print()
print("=== 押下と生成 ===")
mod, ctx, app, board_cls, client = setup()
board_cls.next_title = "谷底の薬草採り"
board = board_cls(app)
board.update_button_display()
CLOCK.settle()
index = [e["text"] for e in app.buttons].index(mod.BOARD_LABEL)
app.on_button_press(index)
CLOCK.settle()

check("押下はゲームの経路（process_choice）に乗る",
      any(name == "MiniQuestPhase" for name, _ in app.process_choice_calls),
      app.process_choice_calls)
check("無害な spec のクラスは起こされない（横取りできている）", app.harmless == 0)
check("依頼が1件増えた", "44" in app.world.quests, list(app.world.quests))
check("生成後は掲示板を開き直す", app.opened_board >= 1, app.opened_board)

# ---- 待機表示（「...」）。
# 生成は数十秒〜数分かかるので、無いと固まって見える
with open(os.path.join(OUT_DIR, "mini_quest.log"), encoding="utf-8") as _fh:
    _busy_lines = _fh.read().splitlines()
check("生成中は待機表示を出す",
      any("busy on" in line for line in _busy_lines), _busy_lines[-3:])
check("生成が終われば解く",
      any("busy off" in line for line in _busy_lines), _busy_lines[-3:])
check("生成が終われば操作を戻す", app.is_button_enabled is True,
      app.is_button_enabled)
check("掲示板を開き直す前は選択肢を塗り直さない（古い画面を見せない）",
      any("busy off (restore=False)" in line for line in _busy_lines), _busy_lines)
check("app.buttons（spec の一覧）には触らない",
      all(isinstance(entry, dict) and "spec" in entry for entry in app.buttons),
      app.buttons)

# 点のアニメーションそのものは共通部品（ui.Screen）の担当なので直接確かめる。
# 本物では `generate_random_quest()` が別スレッドで止まっている間に Clock が回るが、
# この偽ゲームは同期なので、生成の流れでは1コマも進まない。
import instantale_modloader.ui as _ui

_busy_app = BASES["app"](World({"1": Quest(id="1")}))
_busy_app.buttons = [{"text": "A", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])},
                     {"text": "B", "spec": PhaseSpec("JustSetButtonToNormalPhase", [])}]
_busy_app.refresh_choice_buttons()
_busy_screen = _ui.Screen(FakeCtx(OUT_DIR), lambda _t: None, tag="busy test")

_busy_screen.busy_on(_busy_app)
check("待機中は操作を止める", _busy_app.is_button_enabled is False)
CLOCK.run_onces()
CLOCK.tick(3)
check("待機表示は「.」→「..」→「...」のアニメーション",
      [p[0] for p in _busy_app.hud.painted[:4]] == [".", "..", "...", "."],
      _busy_app.hud.painted[:4])
check("待機表示はボタン全枠に出る",
      all(len(set(p)) == 1 and len(p) == 2 for p in _busy_app.hud.painted[:4]),
      _busy_app.hud.painted[:4])
check("待機中も app.buttons は触らない",
      [e["text"] for e in _busy_app.buttons] == ["A", "B"], _busy_app.buttons)

_painted = len(_busy_app.hud.painted)
_busy_screen.busy_off(_busy_app)
check("解けば操作が戻る", _busy_app.is_button_enabled is True)
CLOCK.tick(3)
CLOCK.run_onces()
check("解けばアニメーションが止まる（Clock から外れる）",
      all(set(p) <= {"A", "B"} for p in _busy_app.hud.painted[_painted:]),
      _busy_app.hud.painted[_painted:])
check("解けば元の選択肢に戻る",
      _busy_app.hud.painted[-1] == ["A", "B"], _busy_app.hud.painted[-1])
check("エラーを1件も出していない", ctx.errors == [], ctx.errors)

record_path = os.path.join(OUT_DIR, "state", "mini_quests.json")
records = json.load(open(record_path, encoding="utf-8"))
bucket = records.get("テスト世界", {})
check("控えに残る", "44" in bucket, records)
check("控えに題名とお題が入る",
      bucket.get("44", {}).get("title") == "谷底の薬草採り"
      and bucket.get("44", {}).get("objective"), bucket.get("44"))
check("控えに達成条件（request_summary）が入る",
      bucket.get("44", {}).get("summary")
      == "谷底に自生する月光草を5株、採ってきてほしい。", bucket.get("44"))
check("控えの1件目は採集", bucket.get("44", {}).get("kind") == "gather", bucket.get("44"))
check("セーブ側のクエスト辞書に独自キーを足していない",
      set(app.world_dict["quests"]["44"]) == {"id", "quest_title"},
      app.world_dict["quests"]["44"])

# 2件目は別の種類になる（順に選ばれる）
index = [e["text"] for e in app.buttons].index(mod.BOARD_LABEL)
board_cls.next_title = "行方知れずの羊飼い"
app.on_button_press(index)
CLOCK.settle()
records = json.load(open(record_path, encoding="utf-8"))
check("2件目は別の種類になる",
      records["テスト世界"]["45"]["kind"] == "rescue",
      records["テスト世界"].get("45"))

# ============================================================ 生成プロンプト
print()
print("=== 生成プロンプトの差し替え ===")
mod, ctx, app, board_cls, client = setup()
chat = ctx.hooks["llama_cpp_runtime_completion:LlamaCppClient.chat"]
messages = generator_messages()

sent = {}


def fake_orig(self, model, msgs, fmt=None, *a, **kw):
    sent["messages"] = msgs
    return None


# 印が立っていないときは触らない
chat(fake_orig, client, "m", messages, {"a": 1})
check("印が無ければ生成プロンプトは素通し",
      blob_of(sent["messages"]) == blob_of(messages))

# 生成中（＝押下から戻るまで）だけ差し替わることを、実際の生成経路で確かめる
board_cls.next_title = "苔むした祠の下見"
board = board_cls(app)
board.update_button_display()
CLOCK.settle()


def generator_with_llm(*args, **kwargs):
    """本物の `random_quest_generator` の代わり。中で chat が回るのを再現する。"""
    hook = ctx.hooks["scripts.llm.llm_manager_world_generate:random_quest_generator"]

    def inner(world_overview, settlement_name, settlement_overview,
              structure, area_description, difficulty, *a, **kw):
        sent["area_description"] = area_description
        chat(fake_orig, client, "m", generator_messages(), {"a": 1})
        return None

    return hook(inner, *args, **kwargs)


app.generator_hook = generator_with_llm
index = [e["text"] for e in app.buttons].index(mod.BOARD_LABEL)
app.on_button_press(index)
CLOCK.settle()

check("生成中は【討伐】が差し替わる",
      "【討伐】" not in blob_of(sent["messages"])
      and "【採集】" in blob_of(sent["messages"]),
      blob_of(sent["messages"])[:120])
check("area_description にお題が足される",
      "【この依頼の種類" in (sent.get("area_description") or ""),
      sent.get("area_description"))
check("元の area_description は残る",
      (sent.get("area_description") or "").startswith("風が吹き抜ける丘陵地帯。"),
      sent.get("area_description"))

# 生成が終われば印は下りている
chat(fake_orig, client, "m", generator_messages(), {"a": 1})
check("生成が終われば生成プロンプトは素通しに戻る",
      "【討伐】" in blob_of(sent["messages"]))

# ============================================================ 進行プロンプト
print()
print("=== 進行プロンプトの差し替え ===")
RECORDS = {"テスト世界": {"44": {"kind": "gather", "label": "採集",
                                 "title": "谷底の薬草採り",
                                 "summary": "月光草を5株、採ってきてほしい。",
                                 "objective": "薬草を集めて持ち帰る。"}}}
mod, ctx, app, board_cls, client = setup(RECORDS)
chat = ctx.hooks["llama_cpp_runtime_completion:LlamaCppClient.chat"]

messages = referee_messages("谷底の薬草採り")
chat(fake_orig, client, "m", messages, {"a": 1})
out = blob_of(sent["messages"])
check("控えに在るクエストは書き換わる", "薬草を集めて持ち帰る。" in out)
check("控えの達成条件が進行プロンプトに乗る",
      "月光草を5株、採ってきてほしい。" in out)
check("ラスボス宣言が消える", mod.REFEREE_HEAD_END not in out)
check("完了条件が討伐から外れる", "クエストを攻略した後にのみ実行可能" not in out)
check("system と user の両方が書き換わる",
      sent["messages"][1]["content"] != messages[1]["content"]
      and sent["messages"][2]["content"] != messages[2]["content"])
check("スキーマの system は触らない",
      sent["messages"][0]["content"] == messages[0]["content"])
check("元の messages を書き換えていない（浅いコピーを渡す）",
      mod.REFEREE_HEAD_END in blob_of(messages))

other = referee_messages("尾根路のカラス退治")
chat(fake_orig, client, "m", other, {"a": 1})
check("控えに無いクエストは1バイトも変えない",
      blob_of(sent["messages"]) == blob_of(other))

event_prompt = [{"role": "user",
                 "content": "今、フィールドイベントである「古の祭壇」が実行中である。\n"
                            "- quest_title: 谷底の薬草採り\n"}]
chat(fake_orig, client, "m", event_prompt, {"a": 1})
check("イベント処理のプロンプトは触らない",
      blob_of(sent["messages"]) == blob_of(event_prompt))

plain = [{"role": "user", "content": "会話の要約を作れ。"}]
chat(fake_orig, client, "m", plain, None)
check("関係の無いプロンプトは触らない",
      blob_of(sent["messages"]) == blob_of(plain))

# ---------------------------------------------------------- クラウド（APIキー）
# **版4までは `LlamaCppClient.chat` の1点にしか仕掛けていなかった。**
# クラウドは chat を一度も通らないので、討伐前提を外す書き換えが丸ごと落ち、
# しかも `plan()` が呼ばれないため `missed:` すら出ない ＝ 無音で普通の討伐になっていた（`119_` v1 と同じ死角。TECH.md §5.3 /
# VERIFICATION_LOG.md §2.41）。
mod, ctx, app, board_cls, client = setup(RECORDS)
send_hook = ctx.hooks.get("scripts.llm.llm_manager:send_request")
check("クラウドの送信口にも仕掛かっている", send_hook is not None,
      sorted(ctx.hooks))

cloud_sent = {}


def cloud_orig(manager_name, message, structure=None, **kw):
    cloud_sent["message"] = message
    return None


if send_hook is not None:
    # ローカル（llama.cpp）が載っていないクラウド実行を装う。
    # 載っているとローダは
    # `llm_manager` 境界を意図的に素通しする（二重に当たるため）。
    saved = sys.modules.pop(
        "scripts.llm.request_llm_inference_llama_cpp_completion", None)
    gemini = "scripts.llm.request_llm_inference_gemini_test_streaming"
    sys.modules[gemini] = types.ModuleType(gemini)
    try:
        send_hook(cloud_orig, "manager", referee_messages("谷底の薬草採り"), None)
        cloud_out = blob_of(cloud_sent.get("message") or [])
        check("クラウド経由でも控えの達成条件が乗る",
              "月光草を5株、採ってきてほしい。" in cloud_out, cloud_out[:200])
        check("クラウド経由でもラスボス宣言が消える",
              mod.REFEREE_HEAD_END not in cloud_out)

        cloud_sent.clear()
        send_hook(cloud_orig, "manager", referee_messages("尾根路のカラス退治"), None)
        check("クラウドでも控えに無いクエストは触らない",
              mod.REFEREE_HEAD_END in blob_of(cloud_sent.get("message") or []))
    finally:
        sys.modules.pop(gemini, None)
        if saved is not None:
            sys.modules["scripts.llm.request_llm_inference_llama_cpp_completion"] = saved

# ============================================================ 目印が変わったとき
print()
print("=== ゲーム側の文面が変わったとき ===")
mod, ctx, app, board_cls, client = setup(RECORDS)
chat = ctx.hooks["llama_cpp_runtime_completion:LlamaCppClient.chat"]
broken = referee_messages("谷底の薬草採り")
broken[2]["content"] = broken[2]["content"].replace(
    "- ラスボスが残っている限りクエストは攻略完了しない。", "- （文面が変わった）")
chat(fake_orig, client, "m", broken, {"a": 1})
check("目印が欠けたら書き換えを丸ごと諦める",
      blob_of(sent["messages"]) == blob_of(broken))
check("諦めたことは WARN で残る",
      any(level == "WARN" for level, _ in ctx.logs), ctx.logs)
check("例外にはしない", ctx.errors == [], ctx.errors)

# ============================================================ 実データとの突き合わせ
print()
print("=== 記録済みの実プロンプトとの突き合わせ ===")
GAME_DIR = r"C:\Program Files\Epic Games\Instantaleq6Ve7"
referee_files = sorted(glob.glob(os.path.join(
    GAME_DIR, "output_data", "*", "*", "quest_referee_with_free_action", "*.json")))
generator_files = sorted(glob.glob(os.path.join(
    GAME_DIR, "output_data", "*", "*", "*quest_generator", "*.json")))

if not referee_files and not generator_files:
    print("  skip  output_data/ が無いので飛ばす（ゲーム未導入の環境）")
else:
    bad = []
    for path in referee_files:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        text = blob_of(data.get("messages", []))
        if "turn_resolution" not in text:
            continue
        missing = mod.referee_anchors_missing(text)
        if missing:
            bad.append((os.path.basename(path), missing))
    check("進行プロンプト全件に目印が在る ({}件)".format(len(referee_files)),
          not bad, bad[:3])

    bad = [os.path.basename(p) for p in generator_files
           if mod.GEN_KIND_MARK not in blob_of(
               json.load(open(p, encoding="utf-8")).get("messages", []))]
    check("生成プロンプト全件に【討伐】が在る ({}件)".format(len(generator_files)),
          not bad, bad[:3])

    # 実物のプロンプトをそのまま通して、置換後に討伐前提が残らないこと
    if referee_files:
        with open(referee_files[0], encoding="utf-8") as fh:
            real = json.load(fh)["messages"]
        rewritten = []
        for message in real:
            content = message.get("content", "")
            new = mod.rewrite_referee_text(content, "薬草を集めて持ち帰る。",
                                           None, "月光草を5株。")
            rewritten.append(new if new is not None else content)
        joined = "\n".join(rewritten)
        check("実物を通しても討伐前提が残らない",
              mod.REFEREE_HEAD_END not in joined
              and "クエストを攻略した後にのみ実行可能" not in joined
              and "戦闘ベースのRPGなので" not in joined)
        check("実物を通してもクエストの構造は残る",
              "- quest_title: " in joined and "- enemies:" in joined)

# ============================================================ 他の mod との共存
print()
print("=== 301_ / 302_ との共存 ===")
offer_mark = load_mod(OFFER_MOD, "quest_offer_probe").MARK
party_mark = load_mod(PARTY_MOD, "party_leave_probe").MARK
check("印のキーが 301_ と違う", mod.MARK != offer_mark, (mod.MARK, offer_mark))
check("印のキーが 302_ と違う", mod.MARK != party_mark, (mod.MARK, party_mark))

mod, ctx, app, board_cls, client = setup()
board = board_cls(app)
board.update_button_display()
CLOCK.settle()
app.buttons.append({"text": "この話から依頼を作る",
                    "spec": PhaseSpec("JustSetButtonToNormalPhase", []),
                    offer_mark: "generate"})
app.on_button_press(len(app.buttons) - 1)
check("301_ のボタンはこちらでは素通しになる",
      app.pressed_by_game == ["この話から依頼を作る"], app.pressed_by_game)

print()
if failures:
    print("{} 件失敗: {}".format(len(failures), failures))
    raise SystemExit(1)
print("すべて通った")
