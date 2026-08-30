# -*- coding: utf-8 -*-
"""907_fix_party_opponent（開発中。リリース時に `130_` へ戻す）をゲーム抜きで通す。

    python tools/tests/test_wip_party_opponent.py

`test_wip_*.py` は CI の対象外（TECH.md §2.1）。
正式な番号へ戻すときに `test_party_opponent.py` へ改名して対象に入れる。

偽のマスターAI の応答（`start_battle` を含む `process`）と偽の名簿を差し込み、
次を確認する。

  外す     … `player_opponents` からパーティの仲間だけが外れ、残りで戦闘になる。
             空白付きの名前でも照合できる
  起こさない … 敵が仲間だけなら `start_battle` が `process` から抜け、
             他の段は残る。同じ内容の別の段を巻き込まない
  素通し   … 仲間が混ざっていない・同行者が居ない・`start_battle` が無い・
             知らない形、のどれでも応答をそのまま返す
  触らない … `player_allies` は書き換えない
  形       … 応答が dict でもオブジェクトでも、`process` が tuple でも通る
  拒否     … `player_opponents` を書き換えられない型なら素通しして記録に残す
  経路     … マスターAI の4関数すべてにフックが登録される
  安全     … 途中で何が壊れても本体は必ず1回呼ばれ、戻り値がそのまま返る
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

TARGET = "scripts.llm.llm_manager:master_ai_facilitator_from_conversation"


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    # この検査は mod と同じフォルダに置いてある（`local/` へ移した後の形）。
    # 隣に `mod.json` が在るならそれが対象。`runtime/mods` は見ない。
    here_manifest = os.path.join(HERE, "mod.json")
    if os.path.isfile(here_manifest):
        with io.open(here_manifest, encoding="utf-8") as fh:
            return HERE, os.path.join(HERE, json.load(fh)["entry"])
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
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_fix_party_opponent")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Battle(object):
    """`process` の1要素（`start_battle`）。"""

    def __init__(self, opponents, allies=None):
        self.type = "start_battle"
        self.player_opponents = list(opponents)
        self.player_allies = list(allies or [])


class FrozenBattle(Battle):
    """代入を受け付けない型（pydantic の frozen モデルの代わり）。"""

    def __setattr__(self, name, value):
        if name == "player_opponents" and "player_opponents" in vars(self):
            raise TypeError("frozen")
        object.__setattr__(self, name, value)


class Say(object):
    def __init__(self, statement="やあ"):
        self.type = "npc_say"
        self.statement = statement


class Master(object):
    """マスターAI の応答。`process` はリスト（tuple の回は個別に作る）。"""

    def __init__(self, steps):
        self.think = ""
        self.narration = ""
        self.process = list(steps)


class FakeUI(object):
    """`instantale_modloader.ui` の代わり。名簿だけ返す。

    `members` は {id: 名前}。None なら app が見つからない状態。
    """

    def __init__(self, members):
        self.members = members

    def find_app(self):
        return self if self.members is not None else None

    def party_member_ids(self, app):
        return list(self.members or {})

    def character_of(self, app, member_id):
        name = (self.members or {}).get(member_id)
        return types.SimpleNamespace(name=name) if name else None


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`test_event_ability_check` と同じ）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="party_opponent_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(members):
    """mod を読み直して当て直す。`members` が偽の名簿（{id: 名前} か None）。"""
    module = load_mod()
    module.ui = FakeUI(members)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def call(ctx, result, target=TARGET):
    """包まれたマスターAI を1回呼ぶ。戻り値と、本体が呼ばれた回数を返す。"""
    calls = []

    def orig(*args, **kw):
        calls.append((args, kw))
        return result

    returned = ctx.hooks[target](orig, None, None, None, None, None, None,
                                 None, None)
    return returned, len(calls)


def read_log():
    path = os.path.join(OUT_DIR, "party_opponent.log")
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


# ------------------------------------------------------------------ 検査
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    party = {"63": "レオン", "71": "エリス"}

    print("外す")
    module, ctx = fresh_mod(party)
    check("注入時の自己検証が通っている",
          not [level for level, _ in ctx.logs if level == "ERROR"], ctx.logs)
    battle = Battle(["ゴブリン", "レオン"])
    result = Master([battle])
    returned, times = call(ctx, result)
    check("敵から仲間だけが外れる",
          battle.player_opponents == ["ゴブリン"], battle.player_opponents)
    check("本体は1回呼ばれ、応答はそのまま返る",
          returned is result and times == 1)
    check("start_battle 自体は残る（相手が居るので戦闘になる）",
          result.process == [battle], result.process)
    battle = Battle(["ゴブリン", " レオン "])
    call(ctx, Master([battle]))
    check("空白付きの名前でも照合できる", battle.player_opponents == ["ゴブリン"],
          battle.player_opponents)

    print("起こさない")
    module, ctx = fresh_mod(party)
    say = Say()
    battle = Battle(["レオン", "エリス"])
    result = Master([say, battle])
    call(ctx, result)
    check("敵が仲間だけなら start_battle が抜ける",
          result.process == [say], [getattr(s, "type", s) for s in result.process])
    check("抜いたことが記録に残る",
          any("戦闘を起こさない" in line for line in read_log()), read_log()[-3:])
    twin_a, twin_b = Battle(["レオン"]), Battle(["レオン"])
    result = Master([twin_a, twin_b])
    call(ctx, result)
    check("同じ内容の段が2つあっても両方それぞれ抜ける（等値でなく同一性で選ぶ）",
          result.process == [], result.process)

    print("素通し")
    module, ctx = fresh_mod(party)
    battle = Battle(["ゴブリン", "オーク"])
    returned, times = call(ctx, Master([battle]))
    check("仲間が混ざっていなければ触らない",
          battle.player_opponents == ["ゴブリン", "オーク"] and times == 1,
          battle.player_opponents)
    check("素通しの回も記録に残る",
          any("仲間は居ない" in line for line in read_log()), read_log()[-3:])
    module, ctx = fresh_mod({})
    battle = Battle(["レオン"])
    call(ctx, Master([battle]))
    check("同行者が居なければ触らない（名前が偶々同じ敵を外さない）",
          battle.player_opponents == ["レオン"], battle.player_opponents)
    module, ctx = fresh_mod(None)
    battle = Battle(["レオン"])
    call(ctx, Master([battle]))
    check("app が見つからなくても素通しして落ちない",
          battle.player_opponents == ["レオン"], battle.player_opponents)
    module, ctx = fresh_mod(party)
    plain = Master([Say()])
    returned, times = call(ctx, plain)
    check("start_battle が無ければ何もしない", returned is plain and times == 1)
    weird, times = call(ctx, "まさかの文字列")
    check("知らない形は素通し", weird == "まさかの文字列" and times == 1)

    print("触らない")
    module, ctx = fresh_mod(party)
    battle = Battle(["ゴブリン", "レオン"], allies=["エリス"])
    call(ctx, Master([battle]))
    check("player_allies はそのまま", battle.player_allies == ["エリス"],
          battle.player_allies)

    print("形")
    module, ctx = fresh_mod(party)
    payload = {"process": [{"type": "start_battle",
                            "player_opponents": ["ゴブリン", "レオン"],
                            "player_allies": []}]}
    call(ctx, payload)
    check("dict で返っても書き換わる",
          payload["process"][0]["player_opponents"] == ["ゴブリン"],
          payload["process"][0]["player_opponents"])
    payload = {"process": ({"type": "start_battle",
                            "player_opponents": ["レオン"],
                            "player_allies": []},)}
    call(ctx, payload)
    check("process が tuple でも start_battle を抜ける",
          payload["process"] == (), payload["process"])

    print("拒否")
    module, ctx = fresh_mod(party)
    frozen = FrozenBattle(["ゴブリン", "レオン"])
    returned, times = call(ctx, Master([frozen]))
    check("書き換えられない型はそのまま通す",
          frozen.player_opponents == ["ゴブリン", "レオン"] and times == 1,
          frozen.player_opponents)
    check("拒否は記録に残る",
          any("書き換えられなかった" in line for line in read_log()),
          read_log()[-3:])

    print("経路")
    module, ctx = fresh_mod(party)
    missing = [name for name in module.FACILITATORS
               if "scripts.llm.llm_manager:" + name not in ctx.hooks]
    check("4関数すべてにフックが登録される", not missing, missing)
    battle = Battle(["レオン", "ドラゴン"])
    call(ctx, Master([battle]),
         target="scripts.llm.llm_manager:master_ai_facilitator")
    check("会話以外の経路（自由入力）でも外れる",
          battle.player_opponents == ["ドラゴン"], battle.player_opponents)

    print("安全")
    module, ctx = fresh_mod(party)

    class Exploding(object):
        @property
        def process(self):
            raise RuntimeError("boom")

    boom = Exploding()
    returned, times = call(ctx, boom)
    check("戻り値の読み取りが爆ぜても本体は1回だけ呼ばれ、そのまま返る",
          returned is boom and times == 1)
    check("爆ぜた回は log_exc に残る", ctx.errors, ctx.errors)

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
