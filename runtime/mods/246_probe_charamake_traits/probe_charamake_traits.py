# -*- coding: utf-8 -*-
r"""計測: キャラ作成画面の特質の持ち方と、特質の窓の部品を録る。ゲームは変えない。

##### 何を決めるための計測か

`346_player_trait_effects` の効果を、キャラ作成画面の特質の窓（`scripts.hud.hud_charamake:TraitPopup`）に出したい。
リコンには名前しか無く、次の3つが分からない（GAME.md §2.35）。

    録れていない   作成画面が特質をどこに、どの形で持つか（`StoryApp.character_vm` の中・`TraitVM` の中身）
    録れていない   窓の「生成」ボタン（`generate_callback`）が、その特質を作り直すのか
    録れていない   窓の中の部品（説明の文字の Label の大きさ・並び）。効果の行を足せる場所

##### 録るもの

| 何を録るか | 見どころ |
| --- | --- |
| `StoryApp` の特質の入口（`generate_trait` / `delete_trait` / `on_trait_generate_press` / `apply_trait_popup_display` / `apply_character_sheet` / `load_character_preset` / `generate_random_character`） | 引数と、前後の `character_vm` の特質の欄 |
| `TraitPopup.__init__` | 受け取った名前・説明・消費の文・番号と、作り終えた窓の部品の木（型・文字・大きさ・位置） |
| `TraitButton.__init__` | 番号 |
| `TraitVM.to_dict` | 中身の属性と戻り |
| `create_trait`（`llm_manager_character_create`） | 引数の形と戻り |

200番台の約束どおり読み取りだけ。引数は受け取った形のまま `orig` へ渡す。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず1回呼ぶ。
記録の件数（`SAMPLES`）は `sys` に置き、1プロセスで数える。

出力は `out/charamake_traits.log`。
"""

import sys

from instantale_modloader import frames

LOG_BASENAME = "charamake_traits.log"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
SAMPLES = 200

#: 書いた件数。1プロセスで数えるので `sys` に置く。
SEEN_ATTR = "_instantale_probe_charamake_traits_seen"

STORY_APP = "scripts.create_world:StoryApp"
CHARAMAKE = "scripts.hud.hud_charamake"
#: 部品の木を降りる深さと、1つの木で書く部品の数。
TREE_DEPTH = 6
TREE_LIMIT = 80
#: 文字を切り詰める長さ。
TEXT_CHARS = 160


def _seen():
    found = getattr(sys, SEEN_ATTR, None)
    if not isinstance(found, dict):
        found = {"lines": 0}
        setattr(sys, SEEN_ATTR, found)
    return found


def _short(value):
    text = str(value)
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS] + "…"


def _vars(obj):
    """浅い属性（Kivy の内部と関数を除く）。"""
    try:
        found = vars(obj)
    except TypeError:
        return {}
    return {key: value for key, value in found.items()
            if not key.startswith("_") and not callable(value)}


def _traits_of(vm):
    """`character_vm` のうち、名前に trait が入る欄（形を決めつけない）。"""
    found = {}
    for key, value in _vars(vm).items():
        if "trait" in key.lower():
            found[key] = value
    return found


def _describe(value):
    """特質の欄の中身。並び・辞書・属性の物は1段だけ開く。"""
    if isinstance(value, (list, tuple)):
        return "[{}]".format(", ".join(_describe_item(item) for item in value[:10]))
    if isinstance(value, dict):
        return "{{{}}}".format(", ".join("{!r}: {}".format(key, _describe_item(item))
                                         for key, item in list(value.items())[:10]))
    return _describe_item(value)


def _describe_item(item):
    if isinstance(item, dict):
        return _short({key: _short(val) for key, val in item.items()})
    shallow = _vars(item)
    if shallow:
        return "{}({})".format(type(item).__name__,
                               _short({key: _short(val) for key, val in shallow.items()}))
    return _short(repr(item))


def _tree(widget, write, depth=0, budget=None):
    """部品の木（型・文字・大きさ・位置・size_hint）。"""
    budget = budget if budget is not None else [TREE_LIMIT]
    if widget is None or depth > TREE_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1
    text = frames.attr(widget, "text", None)
    write("{}{} size={} pos={} hint={}{}".format(
        "  " * depth, type(widget).__name__,
        [round(v) for v in (frames.attr(widget, "size", None) or [])],
        [round(v) for v in (frames.attr(widget, "pos", None) or [])],
        frames.attr(widget, "size_hint", None),
        " text={!r}".format(_short(text)) if text else ""))
    for child in reversed(list(frames.attr(widget, "children", None) or [])):
        _tree(child, write, depth + 1, budget)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    limit = int(SAMPLES or 200)

    def room():
        seen = _seen()
        if seen["lines"] >= limit:
            return False
        seen["lines"] += 1
        return True

    def vm_traits(app):
        vm = frames.attr(app, "character_vm", None)
        return {key: _describe(value) for key, value in _traits_of(vm).items()} if vm is not None else None

    def story_step(name):
        def hook(orig, self, *args, **kwargs):
            # after の行を書くかは before の値ではなく「before の行を書いたか」で決める。
            # character_vm がまだ無い回は before が None になり、枠だけ使って after が抜けるため。
            recorded = False
            try:
                if room():
                    write("{} args={} before={}".format(
                        name, [frames.repr_value(arg) for arg in args],
                        vm_traits(self)))
                    recorded = True
            except Exception:
                ctx.log_exc("charamake traits: cannot record {}".format(name))
            result = orig(self, *args, **kwargs)
            try:
                if recorded:
                    write("{} after={} result={}".format(name, vm_traits(self), frames.repr_value(result)))
            except Exception:
                ctx.log_exc("charamake traits: cannot record after {}".format(name))
            return result
        return hook

    for name in ("generate_trait", "delete_trait", "on_trait_generate_press", "apply_trait_popup_display",
                 "apply_character_sheet", "load_character_preset", "generate_random_character"):
        ctx.wrap("{}.{}".format(STORY_APP, name), required=False, safe=True)(story_step(name))

    @ctx.wrap(CHARAMAKE + ":TraitPopup.__init__", required=False, safe=True)
    def trait_popup(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if room():
                names = ("trait_name", "trait_description", "trait_point_use_text",
                         "delete_callback", "generate_callback", "trait_index")
                got = {name: _short(frames.arg(args, kwargs, name, index)) for index, name in enumerate(names)}
                write("TraitPopup {} size={} hint={}".format(got, frames.attr(self, "size", None),
                                                             frames.attr(self, "size_hint", None)))
                write("TraitPopup vars={}".format(_short({key: type(value).__name__
                                                          for key, value in _vars(self).items()})))
                _tree(self, write)
        except Exception:
            ctx.log_exc("charamake traits: cannot record the trait popup")
        return result

    @ctx.wrap(CHARAMAKE + ":TraitButton.__init__", required=False, safe=True)
    def trait_button(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if room():
                write("TraitButton index={} text={!r} size={}".format(
                    frames.arg(args, kwargs, "trait_index", 0), _short(frames.attr(self, "text", None)),
                    frames.attr(self, "size", None)))
        except Exception:
            ctx.log_exc("charamake traits: cannot record the trait button")
        return result

    @ctx.wrap("scripts.create_world:TraitVM.to_dict", required=False, safe=True)
    def trait_vm(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if room():
                write("TraitVM vars={} -> {}".format(_short({k: _short(v) for k, v in _vars(self).items()}),
                                                    _short(result)))
        except Exception:
            ctx.log_exc("charamake traits: cannot record TraitVM")
        return result

    @ctx.wrap("scripts.llm.llm_manager_character_create:create_trait", required=False, safe=True)
    def create_trait(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            if room():
                write("create_trait args={} -> {}".format([frames.repr_value(arg) for arg in args],
                                                          _short(result)))
        except Exception:
            ctx.log_exc("charamake traits: cannot record create_trait")
        return result

    ctx.log("charamake traits: recording the creation screen's traits ({})".format(ctx.out_path(LOG_BASENAME)))
