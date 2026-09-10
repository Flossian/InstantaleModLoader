# -*- coding: utf-8 -*-
"""エリア移動の拒否（`AreaMoveManager.execute` → `area_move_rejector`）が何を読むかを録る。

`913_` は版1（友好度 999）も版2（`state` を `家族`）も実機で外れた（2026-09-10）。
分岐は本体の中で読めないので、判定の窓の間だけ読まれる側に印を付ける:

- `app` / `AreaMoveManager` / プレイヤー / 同行者の `Character` を、属性読みを記録する派生クラスへ
  `__class__` で差し替える（同じ持ち物の派生なので差し替えられる）
- 同行者の `relationship` とその `player` の欄を、鍵読みを記録する dict 派生に差し替える
- `app.party` / `app.original_party` / `app.world.characters` を、読みを記録する dict / list 派生に差し替える

`area_move_rejector` が呼ばれた時点で記録を止める（そこから先は頼み文を組む側の読み）。
窓を抜けるとき全部元に戻す。記録は `out\\area_move_reject.log`。
読まれた順に並ぶので、`>> area_move_rejector` の直前に並ぶ属性が分岐の材料。
"""

from instantale_modloader import ui

LOG_BASENAME = "area_move_reject.log"

# 記録しない属性名（数が多くて分岐の材料にならないもの）
NOISE = {"app", "__class__", "__dict__"}


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    state = {"active": False, "n": 0, "last": None}

    def note(kind, what):
        if not state["active"]:
            return
        key = (kind, what)
        if key == state["last"]:
            return
        state["last"] = key
        state["n"] += 1
        write("  #{:<3} {:<22} {}".format(state["n"], kind, what))

    class LoggingDict(dict):
        _tag = "dict"

        def __getitem__(self, key):
            note(self._tag, "[{!r}]".format(key))
            return dict.__getitem__(self, key)

        def get(self, key, default=None):
            note(self._tag, ".get({!r})".format(key))
            return dict.get(self, key, default)

        def __contains__(self, key):
            note(self._tag, "{!r} in".format(key))
            return dict.__contains__(self, key)

        def __iter__(self):
            note(self._tag, "iter")
            return dict.__iter__(self)

        def keys(self):
            note(self._tag, ".keys()")
            return dict.keys(self)

        def values(self):
            note(self._tag, ".values()")
            return dict.values(self)

        def items(self):
            note(self._tag, ".items()")
            return dict.items(self)

        def __len__(self):
            note(self._tag, "len")
            return dict.__len__(self)

    class LoggingList(list):
        _tag = "list"

        def __iter__(self):
            note(self._tag, "iter")
            return list.__iter__(self)

        def __contains__(self, item):
            note(self._tag, "{!r} in".format(item))
            return list.__contains__(self, item)

        def __getitem__(self, index):
            note(self._tag, "[{!r}]".format(index))
            return list.__getitem__(self, index)

        def __len__(self):
            note(self._tag, "len")
            return list.__len__(self)

    def logging_dict(tag, source):
        return type("Logging_" + tag, (LoggingDict,), {"_tag": tag})(source)

    def logging_list(tag, source):
        return type("Logging_" + tag, (LoggingList,), {"_tag": tag})(source)

    def spy(obj, label, undo):
        """属性読みを記録する派生クラスへ差し替える。戻しは `undo` に積む。"""
        base = type(obj)

        def __getattribute__(self, name):
            if name not in NOISE and not name.startswith("__"):
                value = object.__getattribute__(self, name)
                if not callable(value):
                    note(label, ".{} = {}".format(name, short(value)))
                else:
                    note(label, ".{}()".format(name))
                return value
            return object.__getattribute__(self, name)

        cls = type("Spy_" + base.__name__, (base,), {"__getattribute__": __getattribute__})
        try:
            object.__setattr__(obj, "__class__", cls)
        except TypeError as exc:
            write("  cannot spy {} ({}): {}".format(label, base.__name__, exc))
            return
        undo.append(lambda o=obj, b=base: object.__setattr__(o, "__class__", b))

    def short(value):
        try:
            text = repr(value)
        except Exception:
            text = "<unrepr>"
        return text if len(text) <= 120 else text[:117] + "..."

    def swap_attr(owner, name, make, undo):
        value = getattr(owner, name, None)
        if isinstance(value, dict):
            new = make("dict", value)
        elif isinstance(value, list):
            new = make("list", value)
        else:
            return
        setattr(owner, name, new)
        undo.append(lambda o=owner, n=name, v=value: setattr(o, n, v))

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def execute(orig, self, choice_text=None, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        undo = []
        try:
            write("=" * 72)
            write("window: choice={!r} party={} original_party={!r} quest={} accompany={!r}".format(
                choice_text, ui.party_ids(app), getattr(app, "original_party", None),
                type(getattr(app, "current_quest_data", None)).__name__,
                (getattr(app, "quest_party_accompany_backgrounds", None) or "")[:60]))
            for npc_id in ui.party_member_ids(app):
                character = ui.character_of(app, npc_id)
                if character is None:
                    write("  no Character for {}".format(npc_id))
                    continue
                label = "npc{}".format(npc_id)
                rel = getattr(character, "relationship", None)
                if isinstance(rel, dict):
                    spied = logging_dict(label + ".rel", rel)
                    player = rel.get("player")
                    if isinstance(player, dict):
                        dict.__setitem__(spied, "player", logging_dict(label + ".rel.player", player))
                    object.__setattr__(character, "relationship", spied)
                    undo.append(lambda c=character, r=rel: object.__setattr__(c, "relationship", r))
                spy(character, label, undo)
            player = getattr(app, "player", None)
            if player is not None:
                spy(player, "player", undo)
            for name in ("party", "original_party"):
                swap_attr(app, name, lambda kind, v, n=name: (logging_dict if kind == "dict" else logging_list)("app." + n, v), undo)
            world = getattr(app, "world", None)
            if world is not None:
                swap_attr(world, "characters", lambda kind, v: logging_dict("world.characters", v), undo)
            spy(self, "manager", undo)
            spy(app, "app", undo)
            state["active"] = True
            state["n"] = 0
            state["last"] = None
        except Exception:
            ctx.log_exc("area move reject probe: cannot arm")
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["active"] = False
            for fn in reversed(undo):
                try:
                    fn()
                except Exception:
                    ctx.log_exc("area move reject probe: cannot disarm")
            write("window closed ({} reads)".format(state["n"]))

    @ctx.wrap("scripts.llm.llm_manager:area_move_rejector", required=False, safe=True)
    def area_move_rejector(orig, *args, **kwargs):
        if state["active"]:
            write("  >> area_move_rejector called (reads above are the branch's inputs)")
        state["active"] = False
        return orig(*args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days, *args, **kwargs):
        if state["active"]:
            write("  >> elapse_days({}) (the branch passed)".format(days))
            state["active"] = False
        return orig(self, days, *args, **kwargs)

    ctx.log("area move reject probe: installed -> {}".format(ctx.out_path(LOG_BASENAME)))
