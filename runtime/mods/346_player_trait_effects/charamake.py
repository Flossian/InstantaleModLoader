# -*- coding: utf-8 -*-
"""キャラ作成画面の特質にも効果を出す（DOC.md の「キャラ作成画面」）。

作成画面の特質は `StoryApp.character_vm.trait_1` の1つだけで、中身は `{name, description, severity}`
（Kivy のプロパティ。`TraitVM.to_dict` がセーブの `player_data.traits` と同じ形を返す。VERIFICATION.md §3.130）。

    頼む     `trait_1` が変わったら（さいころ・窓の「生成」・プリセット）、入口と同じ頼みを背景のスレッドへ積む。
             名前・説明・severity は1つずつ書き換わるので、少し間を置いてまとめてから読む
    控え     結果はプロセスの中だけに持つ（セーブがまだ無い）。物語を始めた後の照合（`adopt`）で、
             同じ印の特質は世界×主人公の控えへ引き継ぎ、作成画面の控えは捨てる
    箱の下   特質の箱（`TraitButton`）の下に効果を1行で出す
    窓       特質の窓（`TraitPopup`）の説明の欄の下側に効果を並べ、「効果を再生成」のボタンを足す

bind した関数は注入し直しをまたいで残るので、中身は `store` に置いた最新の世代の関数を呼ぶ。
"""

import sys
import threading
import weakref

from instantale_modloader import frames, ui

from . import rules

STORY_APP = "scripts.create_world:StoryApp"
CHARAMAKE = "scripts.hud.hud_charamake"

#: 作成画面の控えの上限（さいころを何度も振っても溜めない）。
CACHE_LIMIT = 40
#: 特質の欄が書き換わってから読むまでの間（秒）。名前・説明・severity が揃うのを待つ。
SETTLE_SECONDS = 0.4
#: 箱の下の1行の字の大きさ（ゲームの本文は 20）。
BOX_FONT_SIZE = 16
#: 窓の説明の欄のうち、説明の文に残す高さの割合（残りに効果を並べる）。
#: 効果の欄は見出しと3行（特質1つの効果の上限）が入る高さにする（0.62 では3行目が切れた。実機）。
DESCRIPTION_SHARE = 0.52
#: 足した部品に付ける印（注入し直した後に、前の世代の部品を見つけて使い直す）。
MARK = "_mod346_charamake"

TEXTS = {
    "ja": {"head": "効果", "waiting": "効果を確かめています…", "failed": "効果を確かめられなかった（効果を再生成で頼み直せる）",
           "none": "数値になる効果は無い", "regen": "効果を再生成"},
    "en": {"head": "Effects", "waiting": "Checking the effects…",
           "failed": "Could not read the effects (Regenerate to retry)",
           "none": "No numeric effects", "regen": "Regenerate"},
}
GREY = (0.6, 0.59, 0.57, 1)
WHITE = (1, 1, 1, 1)
LINE = (0.37, 0.36, 0.35, 1)


def _texts(lang):
    return TEXTS.get(lang) or TEXTS["ja"]


def trait_of(story):
    """作成画面の特質 `(名前, 説明, severity)`。空なら None。"""
    vm = frames.attr(story, "character_vm", None) if story is not None else None
    trait = frames.attr(vm, "trait_1", None) if vm is not None else None
    if trait is None:
        return None
    return rules.trait_fields({"name": frames.attr(trait, "name", None),
                               "description": frames.attr(trait, "description", None),
                               "severity": frames.attr(trait, "severity", None)})


def box_text(state, record, lang):
    """箱の下の1行。"""
    texts = _texts(lang)
    if state == "waiting":
        return texts["waiting"]
    if state == "failed":
        return texts["failed"]
    if state == "done":
        return rules.summary(record or {}, lang) or texts["none"]
    return ""


def popup_lines(state, record, lang):
    """窓に並べる行 `[(文, 灰色か)]`。見出しは含まない。"""
    texts = _texts(lang)
    if state == "waiting":
        return [(texts["waiting"], True)]
    if state == "failed":
        return [(texts["failed"], True)]
    if state == "done":
        lines = [("・" + text, False) for text in
                 (rules.effect_text(effect, lang) for effect in (record or {}).get("effects") or ()) if text]
        return lines or [(texts["none"], True)]
    return []


class Creation(object):
    """作成画面の控えと画面の部品。プロセスに1つ（`store["creation"]`）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}     # 印 → 控え
        self.waiting = set()  # 頼んでいる印
        self.failed = set()   # 読めなかった印
        self.ctx = None
        self.write = None
        self.worker = None
        self.make_job = None
        self.language = None
        self.enabled = True
        self.story = None     # weakref
        self.button = None    # weakref（特質の箱）
        self.popup = None     # weakref（開いている特質の窓）
        self.popup_label = None  # weakref（その窓の効果の欄）
        self.trigger = None
        self.store = {}

    # -------------------------------------------------------- 控え
    def state_of(self, fp):
        with self.lock:
            if fp in self.waiting:
                return "waiting", None
            if fp in self.records:
                return "done", self.records[fp]
            if fp in self.failed:
                return "failed", None
        return None, None

    def adopt(self, wanted, traits):
        """物語を始めた後の照合。同じ印で、まだ控えの無い（古い版の）特質に作成画面の控えを写す。

        写した控えの並びを返す。作成画面の控えはここで捨てる（作成画面を出た後は要らない）。
        """
        adopted = []
        with self.lock:
            if not self.records and not self.waiting and not self.failed:
                return adopted
            for fp in wanted:
                record = self.records.get(fp)
                current = traits.get(fp)
                if not isinstance(record, dict) or record.get("rules") != rules.RULES_VERSION:
                    continue
                if isinstance(current, dict) and current.get("rules", 1) >= rules.RULES_VERSION:
                    continue
                traits[fp] = dict(record)
                adopted.append(record)
            self.records.clear()
            self.waiting.clear()
            self.failed.clear()
        return adopted

    def finish(self, fp, record):
        """頼みの結果（背景のスレッド）。読めなければ `record` は None。"""
        with self.lock:
            self.waiting.discard(fp)
            if record is not None:
                self.records[fp] = record
                self.failed.discard(fp)
                while len(self.records) > CACHE_LIMIT:
                    self.records.pop(next(iter(self.records)))
            else:
                self.failed.add(fp)
        self.schedule()

    def request(self, story, force=False):
        """いまの特質を頼む。済んでいる・頼んでいる・読めなかった特質は、`force` でなければ頼まない。"""
        fields = trait_of(story)
        if fields is None or not self.enabled:
            return False
        fp = rules.fingerprint(fields)
        with self.lock:
            if fp in self.waiting:
                return False
            if not force and (fp in self.records or fp in self.failed):
                return False
            previous = self.records.get(fp) if force else None
        vm = frames.attr(story, "character_vm", None)
        # 再生成では前の効果を添えて、違う置き換えを頼む（同じ頼み文では同じ答えが返った）
        job = self.make_job(str(frames.attr(vm, "name", "") or ""),
                            str(frames.attr(vm, "backstory", "") or ""), fields, previous)
        if not self.worker.enqueue(job):
            return False
        with self.lock:
            self.waiting.add(fp)
            self.failed.discard(fp)
            if force:
                self.records.pop(fp, None)
        if self.write:
            self.write("作成画面: {} を頼んだ（{}）".format(fields[0], "再生成" if force else "特質が変わった"))
        return True

    # -------------------------------------------------------- 画面
    def schedule(self):
        """ゲームのスレッドで画面を組み直す（間を置いてまとめる）。"""
        if self.trigger is not None:
            try:
                self.trigger()
                return
            except Exception:
                pass

    def current_story(self):
        return self.story() if self.story is not None else None

    def sync(self, *_args):
        """特質を読み直し、要れば頼み、箱の下と窓を組み直す（ゲームのスレッド）。"""
        story = self.current_story()
        if story is None:
            return
        try:
            self.bind(story)
            self.request(story)
            self.refresh(story)
        except Exception:
            self.ctx.log_exc("trait effects: 作成画面の組み直しで失敗した")

    def regenerate(self, *_args):
        story = self.current_story()
        if story is None:
            return
        try:
            self.request(story, force=True)
            self.refresh(story)
        except Exception:
            self.ctx.log_exc("trait effects: 効果の再生成で失敗した")

    def bind(self, story):
        """特質の欄の変化を受ける。1つの物に1回だけ bind する（中身は最新の世代を呼ぶ）。"""
        store = self.store
        vm = frames.attr(story, "character_vm", None)

        def changed(*_args):
            found = store.get("creation")
            if found is not None:
                found.schedule()

        for holder, names in ((vm, ("trait_1", "name", "backstory")),
                              (frames.attr(vm, "trait_1", None) if vm is not None else None,
                               ("name", "description", "severity"))):
            if holder is None or frames.attr(holder, MARK, False):
                continue
            try:
                holder.bind(**{name: changed for name in names})
                setattr(holder, MARK, True)
            except Exception:
                self.ctx.log_exc("trait effects: 作成画面の特質に bind できなかった")

    def trait_button(self):
        button = self.button() if self.button is not None else None
        if button is not None and frames.attr(button, "parent", None) is not None:
            return button
        try:
            from kivy.core.window import Window
        except Exception:
            return None
        for widget in ui.walk_widgets(Window):
            if type(widget).__name__ == "TraitButton":
                self.button = weakref.ref(widget)
                return widget
        return None

    def refresh(self, story):
        fields = trait_of(story)
        lang = self.language()
        state, record = self.state_of(rules.fingerprint(fields)) if fields is not None else (None, None)
        if not self.enabled:
            state, record = None, None
        self.refresh_box(box_text(state, record, lang))
        # 窓は作った直後にはまだ画面に付いていない（開く前に文字を入れる）。閉じた窓に書いても害は無い
        popup = self.popup() if self.popup is not None else None
        if popup is not None:
            self.refresh_popup(popup, popup_lines(state, record, lang), lang)

    def refresh_box(self, text):
        button = self.trait_button()
        parent = frames.attr(button, "parent", None) if button is not None else None
        if parent is None:
            return
        label = next((child for child in parent.children if frames.attr(child, MARK, False)), None)
        if label is None:
            from kivy.uix.label import Label
            label = Label(font_name=button.font_name, font_size=BOX_FONT_SIZE, color=GREY,
                          size_hint=(0.96, 0.2), pos_hint={"center_x": 0.5, "y": 0.01},
                          halign="center", valign="middle", shorten=True, shorten_from="right")
            label.bind(size=lambda widget, size: setattr(widget, "text_size", (size[0], size[1])))
            setattr(label, MARK, True)
            parent.add_widget(label)
        label.text = text

    def decorate(self, popup):
        """特質の窓に効果の欄と「効果を再生成」のボタンを足す（`TraitPopup.__init__` の後）。"""
        from kivy.graphics import Color, Line
        from kivy.uix.button import Button
        from kivy.uix.label import Label
        lang = self.language()
        layout = frames.attr(popup, "trait_popup_layout", None)
        holder = frames.attr(popup, "trait_popup_description_layout", None)
        description = frames.attr(popup, "trait_popup_description", None)
        generate = frames.attr(popup, "trait_popup_generate_button", None)
        point = frames.attr(popup, "trait_popup_point_label", None)
        if layout is None or holder is None or description is None or generate is None:
            return
        description.size_hint = (1, DESCRIPTION_SHARE)
        description.pos_hint = {"x": 0, "top": 1}
        label = Label(font_name=description.font_name, font_size=description.font_size, color=WHITE,
                      size_hint=(1, 1 - DESCRIPTION_SHARE), pos_hint={"x": 0, "y": 0},
                      halign="left", valign="top", markup=True, padding=(10, 6))
        label.bind(size=lambda widget, size: setattr(widget, "text_size", (size[0], size[1])))
        with label.canvas.before:
            Color(*LINE)
            rule = Line(points=[0, 0, 0, 0], width=1)

        def place(_dt=None):
            rule.points = [label.x + 14, label.top, label.right - 14, label.top]

        # 位置の変化のたびにその場で引くと、y だけが位置の決まる前の値のまま残った（実機。pos・x・y のどれに追従しても同じ）。
        # 変化を受けたら、次の描画の前に1回、決まった位置で引き直す
        from kivy.clock import Clock
        redraw = Clock.create_trigger(place, 0)
        label.bind(pos=lambda *_args: redraw(), size=lambda *_args: redraw())
        setattr(label, MARK, True)
        holder.add_widget(label)

        store = self.store
        button = Button(text=_texts(lang)["regen"], font_name=generate.font_name, font_size=generate.font_size,
                        size_hint=(0.28, 0.12), pos_hint={"x": 0.41, "y": 0.05},
                        background_normal=generate.background_normal, background_color=generate.background_color,
                        color=generate.color)
        button.bind(on_release=lambda *_args: store["creation"].regenerate())
        setattr(button, MARK, True)
        painter = getattr(sys.modules.get(CHARAMAKE), "add_border", None)
        if callable(painter):
            try:
                painter(button)
            except Exception:
                self.ctx.log_exc("trait effects: 効果を再生成のボタンに枠を引けなかった")
        layout.add_widget(button)
        if point is not None:
            point.size_hint = (0.28, 0.12)
            point.pos_hint = {"x": 0.70, "y": 0.05}
        self.popup = weakref.ref(popup)
        self.popup_label = weakref.ref(label)

    def refresh_popup(self, popup, lines, lang):
        label = self.popup_label() if self.popup_label is not None else None
        if label is None:
            return
        if not lines:
            label.text = ""
            return
        head = "[size=17][color=969390]{}[/color][/size]".format(_texts(lang)["head"])
        body = ["[color=999691]{}[/color]".format(_escape(text)) if grey else _escape(text) for text, grey in lines]
        label.text = "\n".join([head] + body)


def _escape(text):
    # Kivy の markup のエスケープ（& と角括弧）。呼ぶのは作成画面だけなので Kivy はここで読む
    from kivy.utils import escape_markup
    return escape_markup(str(text))


def install(ctx, store, write, worker, make_job, language, enabled=True):
    """作成画面の包みを当てる。プロセスの `Creation` を返す（入口が控えの引き継ぎと結果に使う）。"""
    # 注入し直すとこのモジュールも読み直され、前の世代の `Creation` は別の型になる（isinstance で見ると作り直して
    # 控えと窓の参照を失い、窓に結果が届かなかった。実機）。毎回作り、前の世代の中身を写す
    old = store.get("creation")
    creation = Creation()
    for name in ("lock", "records", "waiting", "failed", "story", "button", "popup", "popup_label"):
        if old is not None and hasattr(old, name):
            setattr(creation, name, getattr(old, name))
    store["creation"] = creation
    creation.ctx, creation.write, creation.worker = ctx, write, worker
    creation.make_job, creation.language, creation.enabled = make_job, language, enabled
    creation.store = store
    try:
        from kivy.clock import Clock
        creation.trigger = Clock.create_trigger(lambda _dt: store["creation"].sync(), SETTLE_SECONDS)
    except Exception:
        creation.trigger = None     # ゲームの外（検査）

    def seen(story):
        latest = store["creation"]
        latest.story = weakref.ref(story)
        latest.schedule()

    def after(name):
        def hook(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                seen(self)
            except Exception:
                ctx.log_exc("trait effects: 作成画面の {} の後で失敗した".format(name))
            return result
        return hook

    # 特質の欄の変化は bind で受ける。ここは作成画面を見つける入口と、bind の前の変化の拾い直し。
    for name in ("build", "apply_character_sheet", "generate_trait", "delete_trait", "load_character_preset",
                 "apply_trait_popup_display"):
        ctx.wrap("{}.{}".format(STORY_APP, name), required=False, safe=True)(after(name))

    @ctx.wrap(CHARAMAKE + ":TraitPopup.__init__", required=False, safe=True)
    def trait_popup(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            latest = store["creation"]
            if latest.enabled:
                latest.decorate(self)
                story = latest.current_story()
                if story is not None:
                    latest.refresh(story)
        except Exception:
            ctx.log_exc("trait effects: 特質の窓に効果の欄を足せなかった")
        return result

    return creation
