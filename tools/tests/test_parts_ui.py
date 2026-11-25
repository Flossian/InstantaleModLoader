# -*- coding: utf-8 -*-
"""ui の汎用の部品（主人公の名前・忙しさと戦闘の旗・土地の規模と名前・依頼の状態・施設の主・
自前のフェーズ・文字の寸法）と、`talk_summary.player_name` の答えが変わらないこと。

| 見るもの | なぜ |
|---|---|
| `ui.player_name` が strip・代わりの語・字数の切りを写しどおりに返す | 7本の MOD に写しがあり、代わりの語は頼みの文に入る |
| `talk_summary.player_name` が前の本体と同じ答え | 公開 I/F。中身を `ui.player_name` に替えた |
| `ui.busy_flags` が `modfacility.game_is_busy` と同じ答え、`on_quest` で依頼の最中を足す | 旗の表の写しが5本に割れていた（`in_shopping` の事故） |
| `ui.battle_flags` が旗だけを見る | 敵が居るかまで見るのは呼ぶ側 |
| `ui.area_size` が属性 → セーブ → 骨格 → 実行時の順で、鍵の型を均して引く | 写しごとに降りる範囲が違い、id 0 を空と取り違えた写しがあった |
| `ui.area_name` が `facility_name` と同じ形で代わりの語に倒す | 代わりの語は MOD ごとに違う |
| `ui.quest_status` が config の型を確かめて状態の文字列だけを返す | 8本が `config['status']` を直に読んでいた |
| `ui.facility_owner` が id・人物・空・dict の主を見分ける | 空の扱いが4通りに割れていた |
| `Screen.run_phase` が例外を握り、連打よけの印を必ず下ろし、使えなければ直に呼ぶ | 12本の自前のフェーズの写しの代わり |
| `ui.text_height` / `text_lines` がラベルの書式を全部写し、Kivy が無ければ None | 写しごとに渡す書式が欠けていた |

ゲームも Kivy も要らない（偽の app と偽の `kivy.core.text` を差し込む）。
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

from instantale_modloader import talk_summary, ui  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    if ok:
        print("  ok   " + label)
    else:
        FAILURES.append(label)
        print("  FAIL " + label + ((" :: " + str(detail)) if detail else ""))


class Obj(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Raising(object):
    @property
    def name(self):
        raise RuntimeError("boom")


# ------------------------------------------------------------ 主人公の名前
def _old_talk_summary_player_name(app):
    """書き換える前の `talk_summary.player_name` の本体（答えを比べるための写し）。"""
    name = getattr(getattr(app, "player", None), "name", None)
    return name.strip() if isinstance(name, str) and name.strip() else "プレイヤー"


def test_player_name():
    print("player_name")
    check("名前を strip して返す", ui.player_name(Obj(player=Obj(name="  アリス \n"))) == "アリス")
    check("空白だけなら fallback", ui.player_name(Obj(player=Obj(name="   ")), "冒険者") == "冒険者")
    check("文字列でなければ fallback", ui.player_name(Obj(player=Obj(name=12)), "その者") == "その者")
    check("player が無ければ fallback", ui.player_name(Obj(), "主人公") == "主人公")
    check("app が None でも fallback", ui.player_name(None, "旅人") == "旅人")
    check("fallback の既定は空", ui.player_name(Obj()) == "")
    check("limit で切る（frames.short）",
          ui.player_name(Obj(player=Obj(name="あ" * 50)), limit=40) == "あ" * 40 + "…")
    check("fallback は切らない", ui.player_name(Obj(), "x" * 50, limit=3) == "x" * 50)
    check("app.name は見ない", ui.player_name(Obj(name="Instantale"), "P") == "P")
    check("name の property が投げても fallback", ui.player_name(Obj(player=Raising()), "P") == "P")

    cases = [Obj(player=Obj(name=" ボブ ")), Obj(player=Obj(name="")), Obj(player=Obj(name=None)),
             Obj(player=Obj(name=3)), Obj(player=Obj(name="\t")), Obj(), None,
             Obj(player=Obj(name="長い" * 100))]
    same = [talk_summary.player_name(app) == _old_talk_summary_player_name(app) for app in cases]
    check("talk_summary.player_name の答えが前と同じ", all(same), same)


# ------------------------------------------------------------ 忙しさと戦闘の旗
def test_flags():
    print("busy_flags / battle_flags")
    # modfacility は state を読む。突き合わせのためだけなので、ここで読み、読めなくても他の確かめは続ける。
    try:
        from instantale_modloader import modfacility
    except Exception as exc:
        check("modfacility を読める", False, repr(exc))
        modfacility = None
    idle = Obj()
    check("旗が無ければ空", ui.busy_flags(idle) == [] and ui.battle_flags(idle) == [])
    app = Obj(in_conversation=True, in_battle=1, in_shopping=True, in_free_input=False)
    check("立っている旗を表の順に", ui.busy_flags(app) == ["in_battle", "in_conversation"], ui.busy_flags(app))
    check("in_shopping は数えない", "in_shopping" not in ui.busy_flags(app))
    if modfacility is not None:
        check("modfacility.game_is_busy と同じ答え", ui.busy_flags(app) == modfacility.game_is_busy(app))
    check("flags で表を差し替える",
          ui.busy_flags(app, flags=("in_shopping", "in_conversation")) == ["in_shopping", "in_conversation"])
    quest = Obj(current_quest_data={"id": 7})
    check("依頼の最中は on_quest のときだけ足す",
          ui.busy_flags(quest) == [] and ui.busy_flags(quest, on_quest=True) == [ui.ON_QUEST])
    both = Obj(in_conversation=True, current_quest_data=Obj(id="3"))
    check("旗の後ろに依頼の最中", ui.busy_flags(both, on_quest=True) == ["in_conversation", ui.ON_QUEST])
    check("最初の1つの取り方", (ui.busy_flags(idle) or [None])[0] is None)
    battle = Obj(in_colosseum_battle=True, in_boss_battle=True, in_conversation=True)
    check("戦闘の旗だけを表の順に", ui.battle_flags(battle) == ["in_boss_battle", "in_colosseum_battle"])
    if modfacility is not None:
        check("modfacility.BATTLE_FLAGS と同じ並び", tuple(modfacility.BATTLE_FLAGS) == ui.BATTLE_FLAGS)
    check("BUSY_FLAGS は前のまま", ui.BUSY_FLAGS == ("in_battle", "in_boss_battle", "in_colosseum_battle",
                                                  "in_conversation", "in_free_input",
                                                  "in_action_in_conversation"))


# ------------------------------------------------------------ 土地
def _world(areas):
    return Obj(areas=areas)


def test_area_size():
    print("area_size")
    save = {"areas": {"0": {"size": " Town "}, "5": {"size": ""}}}
    world = {"world_data": {"areas": {"5": {"size": "City"}, 9: {"size": "village"}}}}
    runtime = {"12": Obj(id="12", size="Dungeon"), "0": Obj(id="0")}
    app = Obj(save_data_dict=save, world_dict=world, world=_world(runtime))
    check("セーブから引いて strip と小文字化", ui.area_size(app, "0") == "town")
    check("整数の id 0 を空にしない", ui.area_size(app, 0) == "town")
    check("セーブが空なら骨格の world_data", ui.area_size(app, "5") == "city")
    check("鍵が整数でも引ける", ui.area_size(app, "9") == "village")
    check("どこにも無ければ実行時の属性", ui.area_size(app, "12") == "dungeon")
    check("渡した土地の属性を先に見る", ui.area_size(app, Obj(id="0", size="Metropolis")) == "metropolis")
    check("属性が無ければ土地の id で引く", ui.area_size(app, Obj(id=0)) == "town")
    check("セーブの dict も受ける", ui.area_size(app, {"id": "5"}) == "city")
    check("読めなければ None", ui.area_size(app, "77") is None)
    check("None と真偽値は None", ui.area_size(app, None) is None and ui.area_size(app, True) is None)
    nested = Obj(save_data_dict={"world_data": {"areas": {"3": {"size": "TOWN"}}}})
    check("save_data_dict の world_data まで降りる", ui.area_size(nested, "3") == "town")
    check("app が None でも投げない", ui.area_size(None, "3") is None)


def test_area_name():
    print("area_name")
    runtime = {"1": Obj(id="1", name="  灰の交易都市 "), "2": Obj(id="2", name="   "), "4": Obj(id="4")}
    save = {"areas": {"2": {"name": "セーブの名前"}, "8": {"name": "骨格前の名前"}}}
    app = Obj(world=_world(runtime), save_data_dict=save)
    check("id から実行時の表で引く", ui.area_name(app, "1") == "灰の交易都市")
    check("整数の id でも引く", ui.area_name(app, 1) == "灰の交易都市")
    check("土地そのものも受ける", ui.area_name(app, runtime["1"]) == "灰の交易都市")
    check("空白だけの名前はセーブ側へ", ui.area_name(app, "2") == "セーブの名前")
    check("実行時に無ければセーブ側", ui.area_name(app, "8") == "骨格前の名前")
    check("読めなければ fallback", ui.area_name(app, "4", fallback="この土地") == "この土地")
    check("fallback の既定は空", ui.area_name(app, "99") == "")
    check("40字で切る", ui.area_name(Obj(), Obj(id="x", name="あ" * 45)) == "あ" * 40 + "…")
    check("limit=None は切らない", ui.area_name(Obj(), Obj(name="あ" * 45), limit=None) == "あ" * 45)
    check("dict の土地も受ける", ui.area_name(Obj(), {"id": "3", "name": "村"}) == "村")
    check("None は fallback", ui.area_name(app, None, fallback="土地") == "土地")


# ------------------------------------------------------------ 依頼の状態
def test_quest_status():
    print("quest_status")
    check("インスタンスの config", ui.quest_status(Obj(config={"status": "completed"})) == ui.QUEST_COMPLETED)
    check("dict の config", ui.quest_status({"config": {"status": "incomplete"}}) == ui.QUEST_INCOMPLETE)
    check("知らない状態もそのまま", ui.quest_status({"config": {"status": "Failed "}}) == "Failed ")
    check("依頼が None なら None", ui.quest_status(None) is None)
    check("config が無ければ None", ui.quest_status(Obj()) is None)
    check("config が dict でなければ None", ui.quest_status(Obj(config="completed")) is None)
    check("状態が文字列でなければ None", ui.quest_status({"config": {"status": 1}}) is None)


# ------------------------------------------------------------ 施設の主
def test_facility_owner():
    print("facility_owner")
    keeper = Obj(id="63", name="店主")
    shop = Obj(id="f1", owner="63")
    area = Obj(id="0", nodes={"n": Obj(facilities={"f1": shop, "f2": Obj(id="f2", owner=63)})})
    app = Obj(world=Obj(areas={"0": area}, characters={"63": keeper}),
              player=Obj(current_area="0", location="f1"))
    check("いま居る施設の主（id と人物）", ui.facility_owner(app) == ("63", keeper))
    check("施設を渡す", ui.facility_owner(app, shop) == ("63", keeper))
    check("整数の主も文字列に", ui.facility_owner(app, "f2") == ("63", keeper))
    check("人物が引けなければ (id, None)", ui.facility_owner(app, Obj(owner="70")) == ("70", None))
    npc = Obj(id=88, name="人物そのもの")
    check("主が人物そのもの", ui.facility_owner(app, Obj(owner=npc)) == ("88", npc))
    anon = Obj(name="id の無い人物")
    check("人物の id が読めなければ (None, 人物)", ui.facility_owner(app, Obj(owner=anon)) == (None, anon))
    for label, owner in (("None", None), ("空文字", ""), ("空白", "  "), ("真偽値", True),
                         ("dict", {"id": "63"}), ("list", ["63"])):
        check("主なし: " + label, ui.facility_owner(app, Obj(owner=owner)) == (None, None))
    check("dict の施設", ui.facility_owner(app, {"owner": "63"}) == ("63", keeper))
    check("施設が無ければ (None, None)", ui.facility_owner(Obj(), None) == (None, None))
    check("引けない施設 id は (None, None)", ui.facility_owner(app, "nope") == (None, None))


# ------------------------------------------------------------ 自前のフェーズ
class PhaseCtx(object):
    def __init__(self):
        self.errors = []

    def log_exc(self, msg):
        self.errors.append(msg)


class PhaseApp(object):
    def __init__(self, broken=False):
        self.broken = broken
        self.phases = []
        self.is_button_enabled = True

    def process_choice(self, phase, text):
        if self.broken:
            raise RuntimeError("no process_choice")
        self.phases.append((type(phase).__name__, text))
        return phase.execute(text)


def test_run_phase():
    print("Screen.run_phase")
    ctx = PhaseCtx()
    lines = []
    screen = ui.Screen(ctx, lines.append, tag="test")
    app = PhaseApp()
    seen = []
    state = {"acting": False}

    def work(app_, action, text):
        seen.append((app_, action, text, state["acting"]))
        return "done"

    ok = screen.run_phase(app, "押す", work, "pay", "押す", acting=(state, "acting"))
    check("process_choice に渡せたら True", ok is True)
    check("fn(app, *args) をフェーズの中で呼ぶ", seen == [(app, "pay", "押す", True)], seen)
    check("連打よけの印は終わったら下りる", state["acting"] is False)
    check("フェーズは自前のクラス（PhaseSpec に載らない）", app.phases == [("_RunPhase", "押す")])

    def broken(app_):
        raise ValueError("bad")

    errors_after = []
    screen._busy["on"] = True
    offs = []
    screen.busy_off = lambda app_: offs.append(app_)
    ok = screen.run_phase(app, "壊れる", broken, acting=(state, "acting"), busy_off_on_error=True,
                          on_error=lambda app_: errors_after.append(app_))
    check("投げても True（ゲームへ抜けない）", ok is True)
    check("例外は log_exc に tag と名前つきで残る", ctx.errors == ["test: phase 'broken' failed"], ctx.errors)
    check("投げても印は下りる", state["acting"] is False)
    check("busy_off_on_error で待機表示を解く", offs == [app])
    check("on_error を呼ぶ", errors_after == [app])

    screen._busy["on"] = False
    offs[:] = []
    screen.run_phase(app, "壊れる", broken, label="search", busy_off_on_error=True)
    check("label がログの名前になる", ctx.errors[-1] == "test: phase 'search' failed")
    check("待機表示が出ていなければ解かない", offs == [])

    ctx.errors[:] = []
    screen.run_phase(app, "壊れる", broken, on_error=lambda app_: 1 / 0)
    check("on_error が投げても握る", len(ctx.errors) == 2 and "cleanup" in ctx.errors[1], ctx.errors)

    dead = PhaseApp(broken=True)
    seen[:] = []
    state["acting"] = False
    ok = screen.run_phase(dead, "直に", work, "x", "直に", acting=(state, "acting"))
    check("process_choice が使えなければ False で直に呼ぶ", ok is False and seen == [(dead, "x", "直に", False)],
          seen)


# ------------------------------------------------------------ 文字の寸法
class FakeCoreLabel(object):
    made = []

    def __init__(self, **options):
        self.options = options
        FakeCoreLabel.made.append(options)
        self.texture = None

    def refresh(self):
        width = self.options["text_size"][0]
        count = max(1, len(self.options["text"]) // 10) if width else 1
        self.texture = Obj(size=(width or 100, count * self.options["font_size"] * self.options["line_height"]))

    def render(self, real=False):
        self._cached_lines = [Obj(h=self.options["font_size"]), Obj(h=self.options["font_size"] + 2)]


def _with_fake_kivy(fn):
    saved = {name: sys.modules.get(name) for name in ("kivy", "kivy.core", "kivy.core.text")}
    kivy = types.ModuleType("kivy")
    core = types.ModuleType("kivy.core")
    text = types.ModuleType("kivy.core.text")
    text.Label = FakeCoreLabel
    kivy.core = core
    core.text = text
    sys.modules.update({"kivy": kivy, "kivy.core": core, "kivy.core.text": text})
    try:
        fn()
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_text_size():
    print("text_height / text_lines")
    label = Obj(font_size=20, line_height=1.5, font_name="NotoSans", padding=[1, 2, 3, 4],
                halign="center", valign="top", strip=True, text_size=(200, None), width=300)
    if "kivy.core.text" not in sys.modules:
        check("Kivy が無ければ None", ui.text_height(label, "abc") is None and ui.text_lines(label, "abc") is None)

    def with_kivy():
        FakeCoreLabel.made[:] = []
        height = ui.text_height(label, "x" * 30)
        options = FakeCoreLabel.made[-1]
        check("高さを測る（texture の高さ）", height == 3 * 20 * 1.5, height)
        check("書式を全部写す", options == {"text": "x" * 30, "font_size": 20, "line_height": 1.5,
                                         "text_size": (200, None), "font_name": "NotoSans",
                                         "padding": (1, 2, 3, 4), "halign": "center", "valign": "top",
                                         "strip": True}, options)
        ui.text_height(label, "y", width=label.width, font_size=10, line_height=1.0)
        options = FakeCoreLabel.made[-1]
        check("width / font_size / line_height を差し替える",
              options["text_size"] == (300, None) and options["font_size"] == 10 and options["line_height"] == 1.0,
              options)
        bare = Obj()
        ui.text_height(bare, None)
        options = FakeCoreLabel.made[-1]
        check("書式が無ければ素の値（12・1.0・折り返さない）",
              options == {"text": "", "font_size": 12, "line_height": 1.0, "text_size": (None, None)}, options)
        ui.text_height(Obj(font_name="<missing>", padding=[1, 2], halign="weird", strip="yes"), "z")
        options = FakeCoreLabel.made[-1]
        check("番人・形の違う値は写さない",
              not any(key in options for key in ("font_name", "padding", "halign", "strip")), options)
        style = {"font_size": 14, "text_size": [120, None], "halign": "left"}
        ui.text_height(style, "dict")
        options = FakeCoreLabel.made[-1]
        check("dict の書式も受ける",
              options["font_size"] == 14 and options["text_size"] == (120, None) and options["halign"] == "left",
              options)
        check("行数と1行の高さ", ui.text_lines(label, "abc") == (2, 22), ui.text_lines(label, "abc"))

        class Broken(FakeCoreLabel):
            def refresh(self):
                raise RuntimeError("no font")

            def render(self, real=False):
                pass                    # _cached_lines を作らない

        sys.modules["kivy.core.text"].Label = Broken
        check("組版が失敗したら None", ui.text_height(label, "a") is None and ui.text_lines(label, "a") is None)
        sys.modules["kivy.core.text"].Label = FakeCoreLabel

    _with_fake_kivy(with_kivy)


def main():
    test_player_name()
    test_flags()
    test_area_size()
    test_area_name()
    test_quest_status()
    test_facility_owner()
    test_run_phase()
    test_text_size()
    if FAILURES:
        print("\n失敗: " + ", ".join(FAILURES))
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
