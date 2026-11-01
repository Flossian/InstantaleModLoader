# -*- coding: utf-8 -*-
"""`instantale_modloader.abilities`（能力値の補正の窓口）。

置いた補正が場面の中でだけ足され、場面の外と書き込みは素の値のままであること、
期限つきの補正が日数・宿の部屋選び・戦闘の終わりで切れ、控えに残ること、
切った MOD の補正を数えないこと、人物欄の文字に `(+4)` を書き足すことを見る。
"""
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

import instantale_modloader  # noqa: E402
from instantale_modloader import abilities, ui  # noqa: E402

failed = []


def check(label, condition):
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        failed.append(label)


class FakeCtx:
    """控え（`state.WorldStore`）が要るものだけ。`out/test/state` に書く。"""

    def __init__(self, root):
        self.state_dir = root

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        try:
            with io.open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return default

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return True


# ---- 偽のゲーム: Character クラスと、外から包む前の読み口（244 の見張りの代わり）----
class Watch(object):
    """前から置いてあったデータデスクリプタ（`244_` の見張りの形）。書き込みを数える。"""

    def __init__(self, name):
        self.name = name
        self.writes = []

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        try:
            return obj.__dict__[self.name]
        except KeyError:
            raise AttributeError(self.name)

    def __set__(self, obj, value):
        self.writes.append(value)
        obj.__dict__[self.name] = value


class Character(object):
    attack_power = Watch("attack_power")

    def __init__(self, name, ident=None, level=10, scores=None, attack=30, magic=20):
        self.name = name
        if ident is not None:
            self.id = ident
        self.experience_level = level
        self.ability_scores = dict(scores or {"strength": 10, "dexterity": 10, "constitution": 10,
                                              "intelligence": 10, "wisdom": 10, "charisma": 10})
        self.original_ability_scores = dict(self.ability_scores)
        self.attack_power = attack
        self.magic_power = magic


module = types.ModuleType(abilities.CHARACTER_MODULE)
module.Character = Character
sys.modules[abilities.CHARACTER_MODULE] = module

for attr in (abilities._REGISTRY_ATTR, abilities._HOOKS_ATTR, abilities.STORE_ATTR):
    if hasattr(sys, attr):
        delattr(sys, attr)

player = Character("主人公", level=10)                       # m = 3
ally = Character("仲間", ident=95, level=20)                 # m = 5
world = types.SimpleNamespace(name="試験の世界", days_elapsed=100, characters={"95": ally})
app = types.SimpleNamespace(world=world, player=player, party=None)
ui.find_app = lambda: app
ui.party_member_ids = lambda _app: ["95"]

state_root = os.path.join(HERE, os.pardir, os.pardir, "out", "test", "state")
shutil.rmtree(os.path.join(state_root, abilities.STATE_DIRNAME), ignore_errors=True)
abilities._stores.bind(FakeCtx(state_root))
check("読み口を3つ置いた", abilities._install_fields() == 3)
check("置き直しても同じ数（2回目は置かない）", abilities._install_fields() == 3)
field = vars(Character)["attack_power"]
check("前からの読み口は内側に残る", isinstance(field.inner, Watch))

# ---- 状態から決まる補正 ----
print("declare")
check("誰も置いていなければ補正なし", abilities.bonus(app, player) == {})
abilities.declare("411_gear", lambda a, h: {"strength": 3, "charisma": 2, "luck": 9, "wisdom": 0}
                  if h is player else None)
check("知らない鍵と 0 は落とす", abilities.bonus(app, player) == {"strength": 3, "charisma": 2})
check("答えない相手は空", abilities.bonus(app, ally) == {})


def broken(a, h):
    raise RuntimeError("boom")


abilities.declare("bad", broken)
check("壊れた答えは無かったことに", abilities.bonus(app, player) == {"strength": 3, "charisma": 2})
check("持ち主の一覧", abilities.owners() == ["411_gear", "bad"])
check("forget で外れる", abilities.forget("bad") == ["abilities"] and abilities.owners() == ["411_gear"])

# ---- 場面の中と外 ----
print("scope")
check("場面の外は素の値", player.ability_scores["strength"] == 10 and player.attack_power == 30)


def inside(fn):
    return abilities._scope(lambda: fn())


check("場面の中は能力値に足す", inside(lambda: player.ability_scores["strength"]) == 13)
check("場面の中は攻撃力に 筋力 × m", inside(lambda: player.attack_power) == 30 + 3 * 3)
check("魔力は知力の補正が無ければ素のまま", inside(lambda: player.magic_power) == 20)
check("入れ子でも足すのは1回", abilities._scope(lambda: inside(lambda: player.attack_power)) == 39)
check("抜けたら素の値", player.attack_power == 30)


def write_inside():
    player.attack_power = 50
    return player.attack_power


check("場面の中の書き込みは素の値へ", inside(write_inside) == 59 and player.__dict__["attack_power"] == 50)
check("書き込みは前の読み口を通る", field.inner.writes[-1] == 50)
check("raw は場面の中でも素の値", inside(lambda: abilities.raw(player, "attack_power")) == 50)
player.attack_power = 30

abilities.declare("reader", lambda a, h: {"dexterity": h.ability_scores["dexterity"] - 9}
                  if h is player else None)
check("答える関数が同じ人物を読んでも回らない", inside(lambda: player.ability_scores["dexterity"]) == 11)
abilities.forget("reader")

abilities.declare("411_gear", lambda a, h: {"strength": -50} if h is player else None)
check("下限は 1（能力値）", inside(lambda: player.ability_scores["strength"]) == 1)
check("下限は 1（攻撃力）", inside(lambda: player.attack_power) == 1)
check("scores も下限 1", abilities.scores(app, player)["strength"] == 1)
abilities.declare("411_gear", lambda a, h: {"strength": 3, "charisma": 2} if h is player else None)

# ---- 魅力の引数 ----
print("charisma")
seen = []


def emotion(affinity, player_charisma):
    seen.append(player_charisma)
    return affinity


abilities._charisma(emotion, 5, 10)
abilities._charisma(emotion, 5, 7)
abilities._scope(lambda: abilities._charisma(emotion, 5, 10))
abilities._charisma(emotion, 5, player_charisma=10)
check("主人公の素の魅力なら足す・違う値は触らない・場面の中は足さない・名前渡しも足す",
      seen == [12, 7, 10, 12])

# ---- 期限つき ----
print("timed")
instantale_modloader._state["mods"] = {"411_gear": "ok", "327_inn": "ok", "336_crime": "ok"}
meal = abilities.add(app, player, {"strength": 2}, until=abilities.days(3), owner="327_inn", label="朝食")
rest = abilities.add(app, ally, {"constitution": 1}, until=abilities.REST, owner="327_inn", label="薬湯")
fight = abilities.add(app, player, {"dexterity": 4}, until=abilities.BATTLE, owner="327_inn")
sick = abilities.add(app, player, {"wisdom": -2}, owner="336_crime", label="熱病")
gone = abilities.add(app, player, {"wisdom": 5}, owner="999_cut")
check("id が返る", all(isinstance(x, str) for x in (meal, rest, fight, sick, gone)))
check("宛先は id の文字列", abilities.entries(app, ally)[0]["holder"] == "95")
check("合計（切った MOD の分は数えない）",
      abilities.bonus(app, player) == {"strength": 5, "charisma": 2, "dexterity": 4, "wisdom": -2})
check("仲間の分", abilities.bonus(app, ally) == {"constitution": 1})
check("内訳に名前と期限", [(b["label"], b["until"]) for b in abilities.breakdown(app, player)]
      == [("411_gear", None), ("朝食", {"day": 103}), ("327_inn", "battle"), ("熱病", None)])
try:
    abilities.add(app, player, {"strength": 1}, until="tomorrow")
    check("知らない期限は投げる", False)
except ValueError:
    check("知らない期限は投げる", True)
check("量が空なら置かない", abilities.add(app, player, {"luck": 3}) is None)

world.days_elapsed = 102
check("期限の前日はまだ効く", abilities.bonus(app, player).get("strength") == 5)
world.days_elapsed = 103
check("期限の日に切れる", abilities.bonus(app, player).get("strength") == 3)

check("戦闘の終わりで BATTLE が切れる", abilities.expire(app, abilities.BATTLE) == 2)   # 期限切れの朝食も片付く
check("戦闘の分が消えた", "dexterity" not in abilities.bonus(app, player))
vacation = types.SimpleNamespace(app=app)
abilities._rest(lambda self: abilities.bonus(app, ally), vacation)
check("部屋選びで REST が切れる（orig より前）", abilities.bonus(app, ally) == {})
check("外す", abilities.remove(app, sick) and not abilities.remove(app, sick))

path = abilities._stores.store().path("試験の世界×主人公")
with io.open(path, encoding="utf-8") as fh:
    saved = json.load(fh)
check("控えに残る（切った MOD の分も消さない）", [e["owner"] for e in saved["entries"]] == ["999_cut"])
check("次の id を覚えている", saved["next"] == 6)

# ---- 人物欄 ----
print("sheet")
text = "筋力: 30\n器用: 30\n耐久: 30\n知力: 16\n判断: 22\n魅力: 30\n"
check("補正のある行だけ書き足す", abilities.sheet_text(text, {"strength": 4, "wisdom": -2})
      == "筋力: 30(+4)\n器用: 30\n耐久: 30\n知力: 16\n判断: 22(-2)\n魅力: 30\n")
once = abilities.sheet_text(text, {"strength": 4})
check("開き直しても重ならない", abilities.sheet_text(once, {"strength": 5}).startswith("筋力: 30(+5)\n"))
check("補正が無くなれば消す", abilities.sheet_text(once, {}) == text)
check("英語の行も引ける", abilities.sheet_text("Strength: 12\nCharisma: 9", {"charisma": 1.5})
      == "Strength: 12\nCharisma: 9(+1.5)")
check("文字でなければそのまま", abilities.sheet_text(None, {"strength": 1}) is None)

label = types.SimpleNamespace(text=text)
hud = types.SimpleNamespace(character_sheet_attributes=label,
                            visible_character_sheet_data={"name": "仲間"})
abilities.declare("411_gear", lambda a, h: {"strength": 1} if h is ally else None)
abilities._sheet(lambda self: None, hud)
check("人物欄は名前で相手を引く", label.text.startswith("筋力: 30(+1)\n"))

instantale_modloader._state["mods"] = {}
abilities.detach()
check("detach の後は場面の中でも素の値", inside(lambda: ally.ability_scores["strength"]) == 10)

if failed:
    print("{} failed".format(len(failed)))
    sys.exit(1)
print("all ok")
