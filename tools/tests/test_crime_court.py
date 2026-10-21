# -*- coding: utf-8 -*-
"""336_crime_overhaul の裁判（court.py）と脱獄の後始末（prison.py）を、偽の env に載せて流れで通す。

    python tools/tests/test_crime_court.py

  取引と無罪 … 司法取引を持ちかけて無罪放免になったら、締め出しは放免の日から数える
               （ボタンは窓口 `choices` の宣言型の口。介入は `offer`、無罪放免の判決の画面は `claim`）
  無罪の道   … 前の送りで開いた道は、閉じた送りで控えから外す。判決の後に裁判の画面へ戻ったら判決を読み直す
  脱獄       … 決行の戦闘の最中に注入し直してメモリの控えを失っても、戦闘の終わりに牢を出た後の始末をする
"""
import importlib
import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import choices, llm, ui  # noqa: E402

matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
if len(matches) != 1:
    raise SystemExit("cannot find exactly one *_crime_overhaul: {}".format(matches))
package = types.ModuleType("crime_overhaul_court_parts")
package.__path__ = [os.path.join(MODS_DIR, matches[0])]
sys.modules[package.__name__] = package
entry = importlib.import_module(package.__name__ + ".crime_overhaul")
court = importlib.import_module(package.__name__ + ".court")
prison = importlib.import_module(package.__name__ + ".prison")
trial = importlib.import_module(package.__name__ + ".trial")

failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({})".format(detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽のゲーム
class Player:
    def __init__(self):
        self.name = "検査の主人公"
        self.records = {"0": {"lawfulness": -60, "achievements": []}}


class App:
    def __init__(self):
        self.player = Player()
        self.area = "0"
        self.day = 100
        self.gold = 10000
        self.buttons = []


APP = App()
ui.find_app = lambda *a, **k: APP
ui.saver = lambda ctx, write, tag: (lambda app, why=None: None)
ui.spec_cls_name = lambda entry: ((entry or {}).get("spec") or {}).get("cls_name")
ui.spec_args = lambda entry: ((entry or {}).get("spec") or {}).get("args")
ui.current_area = lambda app: getattr(app, "area", None)
ui.area_id_of = lambda area: str(area) if area is not None else None
ui.area_record = lambda player, area_id: player.records.get(str(area_id))
ui.lawfulness_of = lambda record: (record or {}).get("lawfulness")
ui.lawfulness_by_area = lambda player: {k: v.get("lawfulness") for k, v in player.records.items()}


def set_lawfulness(record, value):
    record["lawfulness"] = value
    return True


ui.set_lawfulness = set_lawfulness
ui.game_day = lambda app: app.day
ui.gold_of = lambda app: app.gold
ui.add_gold = lambda app, delta: setattr(app, "gold", app.gold + delta) or app.gold
ui.money = lambda value: str(value)
ui.rewrite_coins = lambda text: text
ui.battle_on_screen = lambda app: False
ui.world_areas = lambda app: {}
llm.watch_aliases = lambda ctx, targets, install, label=None: [install(t) for t in targets]


class Ctx:
    def __init__(self):
        self.hooks = {}
        self.errors = []
        self.mod_dir = os.path.join(MODS_DIR, matches[0])

    def wrap(self, target, required=True, safe=False):
        def deco(fn):
            self.hooks.setdefault(target, []).append(fn)
            return fn
        return deco

    def log_exc(self, message):
        import traceback
        self.errors.append(message + "\n" + traceback.format_exc())

    def log(self, *_a, **_k):
        pass

    def on_ready(self, fn):
        pass

    def superseded(self):
        return False


class Worlds:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = {}

    def playthrough(self, app):
        return "検査の世界×" + app.player.name

    def load(self, key):
        return self.data.setdefault(key, {})

    def save(self, key):
        pass


class Screen:
    def __init__(self):
        self.said = []

    def button(self, text, mark=None):
        return {"text": text, "mark": mark, "spec": None}

    def mark_of(self, entry):
        return (entry or {}).get("mark")

    def prune_stale(self, buttons, heads):
        pass

    def say(self, app, text):
        self.said.append(text)

    def schedule(self, fn):
        fn()

    def make_spec(self, name, args):
        return {"cls_name": name, "args": list(args)}

    def apply_buttons(self, *_a, **_k):
        pass

    def when_idle(self, app, fn, **_k):
        fn()


def make_env():
    env = types.SimpleNamespace()
    env.ctx = Ctx()
    env.write = lambda line: env.lines.append(line)
    env.lines = []
    env.screen = Screen()
    env.worlds = Worlds()
    env.cfg = entry._Settings()
    env.refreshers, env.pressers = [], {}
    env.offers, env.claims, env.screens, env.labels = [], [], {}, []
    env.on_refresh = env.refreshers.append
    env.on_press = lambda head, fn: env.pressers.__setitem__(head, fn)
    env.on_offer = env.offers.append
    env.on_claim = env.claims.append
    env.on_screen = env.screens.__setitem__
    env.on_labels = env.labels.append
    env.screen_open = lambda app, name: False
    env.take_carry = lambda app: {}
    env.quest_reward = lambda difficulty: 100
    env.area_difficulty = lambda app: 10
    env.refresh_gold = lambda app: None
    env.area_label = lambda app, area_id: "検査の町"
    env.owner = matches[0]
    env.prison_event = lambda *a, **k: None
    return env


def trial_screen(sought=None):
    sought = sought or {"sentencing_request": {"type": "imprisonment", "years": 5}}
    return [{"text": "わかりました", "spec": {"cls_name": trial.TRIAL_SPEC, "args": [sought, "窃盗", "店で盗んだ"]}},
            {"text": "濡れ衣だ", "spec": {"cls_name": trial.TRIAL_SPEC, "args": [sought, "窃盗", "店で盗んだ"]}}]


def verdict_screen(years):
    return [{"text": "はい", "spec": {"cls_name": trial.PRISON_SPEC, "args": [years, "窃盗", "店で盗んだ"]}}]


def judge(env, plea):
    """判事の頼みを送る（court の包みを通す）。"""
    sent = []
    hook = env.ctx.hooks[court.SEND_TARGET][-1]
    message = [{"role": "user", "content": "被告'検査の主人公'の釈明は'{}'です".format(plea)}]
    hook(lambda *a, **k: sent.append(a), "judge", message)
    return sent[-1][1][0]["content"] if sent else ""


def refresh(env, buttons):
    """組み直し（窓口と同じ順）: 今の口の refresh → claim（あればそれだけ）→ offer。並んだボタンを返す。

    ボタンは `{"text", "choice"}`（宣言型の口のボタン）か、ゲームのボタンそのもの。
    """
    for fn in env.refreshers:
        fn(APP, buttons)
    view = choices.View(APP, buttons)
    for fn in env.claims:
        picked = fn(APP, view)
        if picked:
            return [{"text": c.text, "choice": c} for c in picked]
    shown = list(buttons)
    for fn in env.offers:
        for c in fn(APP, view) or ():
            shown.append({"text": c.text, "choice": c})
    return shown


def press(buttons, text):
    """並んだボタンのうち文言が `text` で始まる宣言型のボタンを押す。"""
    found = next(b for b in buttons if "choice" in b and b["text"].startswith(text))
    return found["choice"].on_press(APP)


# ================================================================ 取引と無罪
print("[取引と無罪] 司法取引を持ちかけて無罪放免になった")
env = make_env()
prison.install(env)                     # apply と同じく prison が先（締め出しを始める口 `env.start_ban` を置く）
court.install(env)
key = env.worlds.playthrough(APP)
env.worlds.load(key)["underworld_done"] = 1          # 裏の仕事を片付けたことがある（取引を持ちかけられる）
game = trial_screen()
buttons = refresh(env, game)
check("裁判の画面に司法取引のボタン", any(b["text"] == trial.PLEA_LABEL and "choice" in b for b in buttons),
      [b.get("text") for b in buttons])
check("介入のボタンはゲームの一覧に足さない（窓口が組む）", len(game) == 2, [b.get("text") for b in game])
press(buttons, trial.PLEA_LABEL)
check("取引の締め出しは牢を出てから数える控え", env.worlds.load(key).get("underworld_ban_after") is not None)
check("使った介入は組み直すと出ない", trial.PLEA_LABEL not in [b["text"] for b in refresh(env, trial_screen())])
content = judge(env, "その夜は宿で寝ていて、店の主人も私を見ていないと証言している")
check("書いた釈明で無罪の道が開く", trial.NOTE_ACQUIT in content)
game = verdict_screen(0)
buttons = refresh(env, game)
check("0年の判決は無罪放免の1つだけにする（claim）", [b.get("text") for b in buttons] == [trial.ACQUIT_LABEL])
check("ゲームの判決のボタンは書き換えない（セーブには判決のボタンが残る）",
      [b.get("text") for b in game] == ["はい"], game)
check("組み直しても無罪放免の1つだけ", [b.get("text") for b in refresh(env, verdict_screen(0))]
      == [trial.ACQUIT_LABEL])
APP.day = 120
press(buttons, trial.ACQUIT_LABEL)
bucket = env.worlds.load(key)
check("放免の日から締め出しを数え、控えは残さない",
      bucket.get("underworld_ban") == 120 + int(env.cfg.TRIAL_PLEA_BAN_DAYS)
      and "underworld_ban_after" not in bucket, bucket)
check("その土地の手配度は平常へ", APP.player.records["0"]["lawfulness"] == trial.NORMAL_LAWFULNESS)

# ================================================================ 無罪の道
print("[無罪の道] 開いた道は、閉じた送りで外す")
APP.player.name = "二人目"
env = make_env()
court.install(env)
key = env.worlds.playthrough(APP)
refresh(env, trial_screen())
judge(env, "その夜は宿で寝ていて、店の主人も私を見ていないと証言している")
check("長い釈明で開く", env.worlds.load(key)["court"]["acquit"] == "open")
judge(env, "濡れ衣だ")
check("落ちる前の送りで開いた道は、短い釈明の送りでは残さない", env.worlds.load(key)["court"]["acquit"] is None,
      env.worlds.load(key)["court"])
buttons = refresh(env, verdict_screen(0))
check("閉じた後の0年は無罪放免へ差し替えない", trial.ACQUIT_LABEL not in [b.get("text") for b in buttons],
      [b.get("text") for b in buttons])

print("[無罪の道] 判決の後に、同じ裁判の画面のセーブを読んだ")
APP.player.name = "三人目"
env = make_env()
court.install(env)
key = env.worlds.playthrough(APP)
refresh(env, trial_screen())
judge(env, "その夜は宿で寝ていて、店の主人も私を見ていないと証言している")
refresh(env, verdict_screen(0))
check("1回目は無罪放免", env.worlds.load(key)["court"]["applied"] is True)
refresh(env, trial_screen())
check("同じ裁判の画面に戻ったら、判決をまだ読んでいない形に戻す",
      env.worlds.load(key)["court"]["applied"] is False and env.worlds.load(key)["court"]["acquit"] is None)
judge(env, "その夜は宿で寝ていて、店の主人も私を見ていないと証言している")
buttons = refresh(env, verdict_screen(0))
check("判事がまた0年を返せば、もう一度無罪放免にする", [b.get("text") for b in buttons] == [trial.ACQUIT_LABEL],
      [b.get("text") for b in buttons])
check("例外が無い（裁判）", not env.ctx.errors, env.ctx.errors)

# ================================================================ 脱獄
print("[脱獄] 決行の戦闘の最中に注入し直してメモリの控えを失った")
APP.player.name = "四人目"
APP.player.records["0"]["lawfulness"] = -60
env = make_env()
prison.install(env)
key = env.worlds.playthrough(APP)
bucket = env.worlds.load(key)
bucket["jail_break"] = {"area": "0", "prep": 2}         # 決行の控え（戦闘が始まった後に立つ）
bucket["underworld_ban_after"] = 30
APP.day = 200
end_hook = env.ctx.hooks["__main__:BattleEndManager.end_phase"][-1]
end_hook(lambda self, *a: None, types.SimpleNamespace(app=APP, end_type="escape"))
bucket = env.worlds.load(key)
check("決行の控えから牢を出た後の始末をする（控えを消す）", "jail_break" not in bucket, bucket)
check("締め出しを数え始める", bucket.get("underworld_ban") == 230 and "underworld_ban_after" not in bucket, bucket)
check("その土地の手配度を下げる", APP.player.records["0"]["lawfulness"] == -60 - int(env.cfg.JAILBREAK_LOSS),
      APP.player.records["0"])
check("脱獄の知らせを出す", any(line == prison.jailbreak.ESCAPED_TEXT for line in env.screen.said),
      env.screen.said)
said = list(env.screen.said)
end_hook(lambda self, *a: None, types.SimpleNamespace(app=APP, end_type="win"))
check("控えの無い戦闘の終わりでは何もしない", env.screen.said == said)
check("例外が無い（脱獄）", not env.ctx.errors, env.ctx.errors)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
