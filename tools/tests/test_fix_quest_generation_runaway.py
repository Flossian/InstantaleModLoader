# -*- coding: utf-8 -*-
"""139_fix_quest_generation_runaway をゲーム抜きで通す。

    python tools/tests/test_fix_quest_generation_runaway.py

  上限     … 依頼の型（QuestStructure）の写しの8つの並びに上限が付く。敵は設定（既定 5）
  元のまま … ゲームが持っている型の辞書は書き換えない
  対象外   … 依頼でない型・dict でない format は触らない（同じものがそのまま渡る）
  据え置き … 既に同じか小さい上限が付いている欄は触らない
  設定     … ENEMY_LIMIT を変えれば敵の上限が変わる（1 未満は 1）
  包み     … 本体は1回だけ、format は位置でもキーワードでも上限つきの写しが渡る。記録が1行出る
  名前違い … 依頼らしい型（enemies と boss を持つ）の名前が違えば WARN を1度だけ出す

実機の LLM での効き目（失敗が消えるか・打ち切っても型どおりか）は `tools\\quest_gen_probe.py` で見る。
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402

CHAT = "llama_cpp_runtime_completion:LlamaCppClient.chat"
LOG_NAME = "quest_generation_runaway.log"


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


MOD = find_mod("_fix_quest_generation_runaway")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def array(ref=None, **extra):
    node = {"type": "array", "items": {"$ref": "#/$defs/" + ref} if ref else {"type": "string"}}
    node.update(extra)
    return node


#: ゲームの依頼の型（pydantic の model_json_schema）を縮めた見本。並びの欄と入れ子の形は実物どおり。
QUEST = {
    "$defs": {
        "Area": {"properties": {"name": {"type": "string"}, "locations": array()},
                 "required": ["name", "locations"], "title": "Area", "type": "object"},
        "FieldEvent": {"properties": {"event_name": {"type": "string"}}, "title": "FieldEvent",
                       "type": "object"},
        "Look": {"properties": {"image_generation_prompt": array()}, "title": "Look", "type": "object"},
        "Skill": {"properties": {"effects": array("DamageOrHeal")}, "title": "Skill", "type": "object"},
        "DamageOrHeal": {"properties": {"power": {"type": "string"}}, "title": "DamageOrHeal",
                         "type": "object"},
        "TextStatusEffect": {"properties": {"effects_per_turn": array("DamageOrHeal")},
                             "title": "TextStatusEffect", "type": "object"},
        "EnemyData": {"properties": {"look": {"$ref": "#/$defs/Look"}, "skills": array("Skill"),
                                     "drops": array("ItemData", maxItems=2)},
                      "title": "EnemyData", "type": "object"},
        "ItemData": {"properties": {"item_name": {"type": "string"}}, "title": "ItemData",
                     "type": "object"},
        "Enemy": {"properties": {"type": {"enum": ["normal", "miniboss"], "type": "string"},
                                 "data": {"$ref": "#/$defs/EnemyData"}},
                  "title": "Enemy", "type": "object"},
        "Boss": {"properties": {"data": {"$ref": "#/$defs/EnemyData"}}, "title": "Boss", "type": "object"},
    },
    "properties": {"quest_title": {"type": "string"}, "area": {"$ref": "#/$defs/Area"},
                   "events": array("FieldEvent"), "enemies": array("Enemy"),
                   "boss": {"$ref": "#/$defs/Boss"}},
    "required": ["quest_title", "area", "events", "enemies", "boss"],
    "title": "QuestStructure", "type": "object",
}


class FakeCtx:
    _mod = None

    def __init__(self):
        self.out_dir = OUT_DIR
        self.hooks = {}
        self.errors = []
        self.lines = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, message, level="INFO"):
        self.lines.append((level, message))

    def log_exc(self, message):
        self.errors.append(message)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def fresh(**settings):
    path = os.path.join(OUT_DIR, LOG_NAME)
    if os.path.exists(path):
        os.remove(path)
    spec = importlib.util.spec_from_file_location("quest_runaway_mod", MOD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in settings.items():
        setattr(module, name, value)
    ctx = FakeCtx()
    module.apply(ctx)
    return module, ctx


def read_log():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def call(ctx, fmt, keyword=False):
    """包みを通す。`(本体に渡った format, 本体の呼ばれた回数, 戻り値)`。"""
    seen = []

    def orig(self, model, messages, format=None, *args, **kwargs):
        seen.append(format)
        return "戻り値"
    if keyword:
        result = ctx.hooks[CHAT](orig, object(), "model", [{"role": "user", "content": "x"}], format=fmt,
                                 stream=True)
    else:
        result = ctx.hooks[CHAT](orig, object(), "model", [{"role": "user", "content": "x"}], fmt)
    return (seen[0] if seen else None), len(seen), result


def max_of(schema, def_name, field):
    node = schema if def_name == "QuestStructure" else schema["$defs"][def_name]
    return node["properties"][field].get("maxItems")


def main():
    module, ctx = fresh()
    check("自己検査が通る", any("enemies <= 5" in m for _l, m in ctx.lines) and not
          any(level == "ERROR" for level, _m in ctx.lines), ctx.lines)

    original = json.loads(json.dumps(QUEST))
    sent, calls, result = call(ctx, QUEST)
    check("本体は1回・戻り値はそのまま", calls == 1 and result == "戻り値", (calls, result))
    check("写しが渡る（元の型は書き換えない）", sent is not QUEST and QUEST == original)
    expected = {("QuestStructure", "enemies"): 5, ("QuestStructure", "events"): 4,
                ("Area", "locations"): 8, ("EnemyData", "skills"): 3,
                ("Look", "image_generation_prompt"): 6, ("Skill", "effects"): 3,
                ("TextStatusEffect", "effects_per_turn"): 3}
    got = {key: max_of(sent, *key) for key in expected}
    check("8つの並びのうち7つに上限が付く", got == expected, got)
    check("既に小さい上限（drops<=2）は据え置き", max_of(sent, "EnemyData", "drops") == 2,
          max_of(sent, "EnemyData", "drops"))
    check("並び以外の形は変わらない", sent["required"] == QUEST["required"]
          and sent["$defs"]["Enemy"] == QUEST["$defs"]["Enemy"])
    check("記録が1行出る", len([l for l in read_log() if "limited QuestStructure #1" in l]) == 1,
          read_log())

    sent, _calls, _result = call(ctx, QUEST, keyword=True)
    check("キーワードで渡っても上限つき", max_of(sent, "QuestStructure", "enemies") == 5)

    other = {"title": "Structure", "properties": {"summary": {"type": "string"}}}
    sent, calls, _result = call(ctx, other)
    check("依頼でない型はそのまま", sent is other and calls == 1)
    sent, calls, _result = call(ctx, "json")
    check("dict でない format はそのまま", sent == "json" and calls == 1)
    sent, calls, _result = call(ctx, None)
    check("format 無しはそのまま", sent is None and calls == 1)

    renamed = json.loads(json.dumps(QUEST))
    renamed["title"] = "QuestStructureV2"
    call(ctx, renamed)
    call(ctx, renamed)
    check("名前の違う依頼らしい型は WARN を1度だけ",
          len([l for l in read_log() if "WARN a quest-like schema" in l]) == 1, read_log())

    module, ctx = fresh(ENEMY_LIMIT=3)
    sent, _calls, _result = call(ctx, QUEST)
    check("設定の敵の上限が効く", max_of(sent, "QuestStructure", "enemies") == 3)
    module, ctx = fresh(ENEMY_LIMIT=0)
    sent, _calls, _result = call(ctx, QUEST)
    check("1 未満は 1", max_of(sent, "QuestStructure", "enemies") == 1)

    tight = json.loads(json.dumps(QUEST))
    tight["properties"]["enemies"]["maxItems"] = 4
    module, ctx = fresh()
    sent, _calls, _result = call(ctx, tight)
    check("敵に既に小さい上限があれば据え置き", max_of(sent, "QuestStructure", "enemies") == 4)
    check("例外を出していない", not ctx.errors, ctx.errors)

    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
