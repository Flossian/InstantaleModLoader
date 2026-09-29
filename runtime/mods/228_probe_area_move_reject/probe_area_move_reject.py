# -*- coding: utf-8 -*-
"""エリア移動の拒否（`AreaMoveManager.execute` → `area_move_rejector`）が何を読むかを録る。

`329_` は版1（友好度 999）も版2（`state` を `家族`）も実機で外れた。
分岐は本体の中で読めないので、判定の窓の間だけ読まれる側に印を付ける:

- `app` / `AreaMoveManager` / プレイヤー / 同行者の `Character` を、属性読みを記録する派生クラスへ
  `__class__` で差し替える（同じ持ち物の派生なので差し替えられる）
- 同行者の `relationship` とその `player` の欄を、鍵読みを記録する dict 派生に差し替える
- `app.party` / `app.original_party` / `app.world.characters` を、読みを記録する dict / list 派生に差し替える

`area_move_rejector` か `elapse_days` が呼ばれた時点で記録を止め、差し替えを全部解く
（そこから先は頼み文を組む側の読み）。dict / list は写しなので、窓の間に写しへ入った書き込みは
解くときに元のオブジェクトへ移す（移動先のエリアの生成や日数送りの書き込みを捨てないため）。
記録は `out\\area_move_reject.log`。
読まれた順に並ぶので、`>> area_move_rejector` の直前に並ぶ属性が分岐の材料。

##### 版の記録

- 版4: 属性読みの記録が、読まれた値の `repr` を全文作ってから 120 字に切っていた。
  窓の間は `app` の読みが全スレッドで通るので、`world_dict` / `save_data_dict` を読まれるたびに
  世界の辞書まるごとの `repr` を組んでいた。値は数・真偽・短い文字列だけ写し、
  それ以外は型名（入れ物は件数も）にした。窓が閉じた後の読みは値を見る前に素通しする。
  `AreaMoveManager.execute` の包みに `safe=True` を付け、受け取った引数を
  キーワードのまま `orig` へ渡すようにした（`choice_text` を位置に直していた）。
- 版3 の実機（09-10）で `window:` 21回に対し `window closed` が 13回だった。
  欠けた8回は `217_probe_area_move` の `move done` も同じ回で欠けており、
  `execute` そのものが戻っていない（二重押下で `execute` が 0.9 秒差で2本走った直後から、
  その起動の間ずっと戻らなかった）。窓の記録は `>> elapse_days` の時点で解けていて、
  閉じの行が出ないのは `execute` の戻りを待つ行だから。原因は本体側で、この probe では測っていない
"""

import threading

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

    def spy(obj, label):
        """属性読みを記録する派生クラスへ差し替える。戻しは窓の `undo` に積む。"""
        base = type(obj)

        def __getattribute__(self, name):
            # 版4: 窓が閉じた後（戻しが済むまでの間）は、値を見る前に素通しする。
            if state["active"] and name not in NOISE and not name.startswith("__"):
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
        window["undo"].append(lambda o=obj, b=base: object.__setattr__(o, "__class__", b))

    def short(value):
        """読まれた値の写し。数・真偽・短い文字列だけ値ごと、ほかは型名（版4）。

        前は `repr` を全文作ってから切っていたので、`app.world_dict` を読まれるたびに
        世界の辞書まるごとを文字にしていた。
        """
        if value is None or isinstance(value, (bool, int, float)):
            return repr(value)
        if isinstance(value, str):
            return repr(value) if len(value) <= 60 else "str(len={})".format(len(value))
        name = type(value).__name__
        if isinstance(value, (dict, list, tuple, set)):
            return "{}(len={})".format(name, len(value))
        return "<{}>".format(name)

    # 窓の差し替えの戻し。窓を開いた側と `area_move_rejector` / `elapse_days` の側
    # （別スレッドのこともある）のどちらが先に解いても1回だけ走るよう、鍵を掛けて取り出す。
    window = {"undo": [], "copies": {}}
    lock = threading.Lock()

    def original_of(value):
        """写しの中に入れた写し（relationship の player）は元に置き換えて書き戻す。"""
        return window["copies"].get(id(value), value)

    def copy_back(original, copied, taken):
        """写しの中身を元のオブジェクトへ移す。

        写した後に元へ直接入った書き込み（写す前から元を握っていた側の分）は残し、
        写しから消えた鍵だけ元からも消す。写しの読みの印は通らない（`dict.items` を直に呼ぶ）。
        """
        if isinstance(original, dict):
            items = [(key, original_of(value)) for key, value in dict.items(copied)]
            kept = set(key for key, _ in items)
            for key in taken:
                if key not in kept:
                    original.pop(key, None)
            original.update(items)
        else:
            original[:] = [original_of(value) for value in list.__getitem__(copied, slice(None))]

    def track(original, copied, get=None, put=None):
        """写しを戻しに積む。`get` / `put` があれば、まだ写しが置かれているときだけ元を置き直す
        （窓の間に本体が別のオブジェクトを置いたなら、そちらを残す）。"""
        taken = set(dict.keys(original)) if isinstance(original, dict) else None
        window["copies"][id(copied)] = original

        def undo():
            if get is not None and get() is copied:
                put(original)
            copy_back(original, copied, taken)
        window["undo"].append(undo)

    def disarm():
        """記録を止め、差し替えを全部解く。2度目以降は何もしない。"""
        state["active"] = False
        with lock:
            undo, window["undo"] = window["undo"], []
        for fn in reversed(undo):
            try:
                fn()
            except Exception:
                ctx.log_exc("area move reject probe: cannot disarm")
        if undo:
            window["copies"] = {}

    def swap_attr(owner, name, make):
        value = getattr(owner, name, None)
        if isinstance(value, dict):
            new = make("dict", value)
        elif isinstance(value, list):
            new = make("list", value)
        else:
            return
        setattr(owner, name, new)
        track(value, new, get=lambda o=owner, n=name: getattr(o, n, None),
              put=lambda v, o=owner, n=name: setattr(o, n, v))

    # 版4: `safe=True` を付け、受け取った引数をそのまま `orig` へ渡す。
    @ctx.wrap("__main__:AreaMoveManager.execute", required=False, safe=True)
    def execute(orig, self, *args, **kwargs):
        choice_text = args[0] if args else kwargs.get("choice_text")
        try:
            app = getattr(self, "app", None) or ui.find_app()
            disarm()
        except Exception:
            app = None
            ctx.log_exc("area move reject probe: cannot reset the window")
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
                        inner = logging_dict(label + ".rel.player", player)
                        dict.__setitem__(spied, "player", inner)
                        track(player, inner)
                    object.__setattr__(character, "relationship", spied)
                    track(rel, spied,
                          get=lambda c=character: object.__getattribute__(c, "relationship"),
                          put=lambda v, c=character: object.__setattr__(c, "relationship", v))
                spy(character, label)
            player = getattr(app, "player", None)
            if player is not None:
                spy(player, "player")
            for name in ("party", "original_party"):
                swap_attr(app, name, lambda kind, v, n=name: (logging_dict if kind == "dict" else logging_list)("app." + n, v))
            world = getattr(app, "world", None)
            if world is not None:
                swap_attr(world, "characters", lambda kind, v: logging_dict("world.characters", v))
            spy(self, "manager")
            spy(app, "app")
            state["active"] = True
            state["n"] = 0
            state["last"] = None
        except Exception:
            ctx.log_exc("area move reject probe: cannot arm")
        try:
            return orig(self, *args, **kwargs)
        finally:
            disarm()
            write("window closed ({} reads)".format(state["n"]))

    @ctx.wrap("scripts.llm.llm_manager:area_move_rejector", required=False, safe=True)
    def area_move_rejector(orig, *args, **kwargs):
        if state["active"]:
            write("  >> area_move_rejector called (reads above are the branch's inputs)")
        disarm()
        return orig(*args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        # 版4: 日数を位置に直して渡していたのをやめ、受け取った形のまま渡す。
        if state["active"]:
            days = args[0] if args else kwargs.get("days")
            write("  >> elapse_days({}) (the branch passed)".format(days))
        disarm()
        return orig(self, *args, **kwargs)

    ctx.log("area move reject probe: installed -> {}".format(ctx.out_path(LOG_BASENAME)))
