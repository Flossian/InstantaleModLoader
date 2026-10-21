# -*- coding: utf-8 -*-
"""147_fix_party_flee をゲーム抜きで通す。

    python tools/tests/test_party_flee.py

  後に続く … 主人公が逃げた手の後、一覧に残った仲間も預かりへ移り、地の文の後に1行出る
  動かない … 逃げるのに失敗した手・主人公が前から抜けている手・仲間のいない逃走では何もしない
  戻す     … ゲームが全員を戻した終わり方では触らない。戻さなかった終わり方（勝ち）では移した仲間を戻す
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_fix_party_flee")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  -- {!r}".format(detail)))
    if not cond:
        failures.append(name)


main = types.ModuleType("__main__")
sys.modules["__main__"] = main


class Character:
    def __init__(self, name):
        self.name = name


class InstantaleApp:
    def __init__(self, members):
        self.player_character = Character("主人公")
        self.party = {"player": self.player_character}
        self.party.update(members)
        self.escaped_member_in_battle = {}
        self.texts = []

    def add_text(self, text):
        self.texts.append(text)


main.InstantaleApp = InstantaleApp


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.state_dir = os.path.join(out_dir, "state")
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = None

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


class Manager:
    def __init__(self, app):
        self.app = app


def setup(members):
    spec = importlib.util.spec_from_file_location("party_flee_under_test", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    work = tempfile.mkdtemp(prefix="party_flee_test_")
    ctx = FakeCtx(work)
    mod.apply(ctx)
    app = InstantaleApp(members)
    return mod, ctx, app, work


def game_flee(app):
    """ゲームの逃げる手（`remove_party_member('player')` と預かり）。"""
    app.escaped_member_in_battle["player"] = app.party.pop("player")


def one_action(mod, ctx, app, effect):
    """1手ぶん。`handle_battle_situation` の中で `resolve_battle_effect` → 地の文。"""
    manager = Manager(app)

    def handle(self):
        ctx.hooks[mod.RESOLVE_TARGET](lambda _self: effect(app), self)
        app.add_text("（審判の地の文）")

    ctx.hooks[mod.HANDLE_TARGET](handle, manager)


def end_battle(mod, ctx, app, label, give_back):
    """戦闘の終わり方。`give_back` ならゲームが預かりを全員戻す（逃走の終わり方）。"""
    def end_phase(self):
        if give_back:
            for member_id, value in list(app.escaped_member_in_battle.items()):
                app.party[member_id] = value
            app.escaped_member_in_battle.clear()
    target = dict((lbl, tgt) for tgt, lbl in mod.END_TARGETS)[label]
    ctx.hooks[target](end_phase, Manager(app))


print("後に続く")
mod, ctx, app, work = setup({"95": Character("リュウ"), "104": Character("ジーン")})
one_action(mod, ctx, app, game_flee)
check("主人公が逃げると仲間も預かりへ移る", list(app.escaped_member_in_battle) == ["player", "95", "104"],
      list(app.escaped_member_in_battle))
check("一覧が空になる（主人公1人の逃走と同じ状態）", app.party == {}, app.party)
check("地の文の後に1行出る", app.texts == ["（審判の地の文）", "リュウとジーンも後に続いて戦いを離れた。"], app.texts)
end_battle(mod, ctx, app, "BattleEndManager.end_phase", give_back=True)
check("逃走の終わり方でゲームが全員を戻す（二重にしない）",
      list(app.party) == ["player", "95", "104"] and not app.escaped_member_in_battle,
      (list(app.party), app.escaped_member_in_battle))
check("例外を残さない", ctx.errors == [], ctx.errors)
shutil.rmtree(work, ignore_errors=True)

print("動かない")
mod, ctx, app, work = setup({"95": Character("リュウ")})
one_action(mod, ctx, app, lambda _app: None)
check("逃げるのに失敗した手では動かさない", list(app.party) == ["player", "95"], list(app.party))
check("失敗した手では行を出さない", app.texts == ["（審判の地の文）"], app.texts)
game_flee(app)
app.party.clear()
app.party["95"] = Character("リュウ")
one_action(mod, ctx, app, lambda _app: None)
check("主人公が前から抜けている手では動かさない", list(app.party) == ["95"], list(app.party))
shutil.rmtree(work, ignore_errors=True)

mod, ctx, app, work = setup({})
one_action(mod, ctx, app, game_flee)
check("仲間がいなければ何もしない", app.texts == ["（審判の地の文）"] and app.party == {}, app.texts)
shutil.rmtree(work, ignore_errors=True)

print("戻す")
mod, ctx, app, work = setup({"95": Character("リュウ"), "104": Character("ジーン"), "7": Character("メイ")})
one_action(mod, ctx, app, game_flee)
check("3人なら「、」でつなぐ", app.texts[-1] == "リュウ、ジーン、メイも後に続いて戦いを離れた。", app.texts)
end_battle(mod, ctx, app, "BattleEndInColosseum.end_phase", give_back=False)
check("ゲームが戻さない終わり方では、移した仲間を戻す",
      [m for m in app.party if m != "player"] == ["95", "104", "7"], list(app.party))
check("戻した仲間は預かりから外す", list(app.escaped_member_in_battle) == ["player"],
      list(app.escaped_member_in_battle))
end_battle(mod, ctx, app, "BattleEndInFreeAction.end_phase", give_back=False)
check("次の終わり方では何もしない（控えは使い切り）", list(app.party).count("95") == 1, list(app.party))
check("例外を残さない（戻す）", ctx.errors == [], ctx.errors)
shutil.rmtree(work, ignore_errors=True)

print("名前のつなぎ")
check("1人", mod.join_names(["リュウ"]) == "リュウ")
check("2人", mod.join_names(["リュウ", "ジーン"]) == "リュウとジーン")

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all ok")
