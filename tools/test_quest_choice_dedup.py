# -*- coding: utf-8 -*-
"""会話依頼ボタンのセーブ復元後における重複防止テスト。"""

import importlib.util
import os
import shutil
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "runtime"))


class PhaseSpec(object):
    def __init__(self, cls_name, args):
        self.cls_name = cls_name
        self.args = list(args)


class Ctx(object):
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.wrappers = {}
        self.errors = []

    def out_path(self, basename):
        return os.path.join(self.output_dir, basename)

    def wrap(self, target, required=False):
        def decorate(function):
            self.wrappers[target] = function
            return function
        return decorate

    def log(self, _message):
        pass

    def log_exc(self, message):
        self.errors.append(message)


class Value(object):
    def __init__(self, **values):
        self.__dict__.update(values)


def load_mod():
    path = os.path.join(
        ROOT, "runtime", "mods", "301_quest_from_conversation",
        "quest_from_conversation.py")
    spec = importlib.util.spec_from_file_location("test_quest_from_conversation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def button(text, cls_name, args=()):
    return {"text": text, "spec": PhaseSpec(cls_name, args)}


def main():
    output_dir = tempfile.mkdtemp(prefix="quest-choice-dedup-")
    try:
        ctx = Ctx(output_dir)
        load_mod().apply(ctx)
        start = ctx.wrappers["__main__:ConversationStartManager.__init__"]
        refresh = ctx.wrappers["__main__:InstantaleApp.refresh_choice_buttons"]

        app = Value(
            in_conversation=True,
            player=Value(name="リノ"),
            world=Value(characters={"77": Value(name="ナエ")}),
            current_conversation_history=[
                {"role": "user", "content": "困り事はあるか"},
                {"role": "assistant", "content": "頼みたいことがある"},
            ],
            buttons=[
                button("この話から依頼を作る（ナエ）",
                       "JustSetButtonToNormalPhase"),
                button("依頼を受ける（話を切り上げる）",
                       "JustSetButtonToNormalPhase"),
                button("会話を終了する", "ConversationEndManager",
                       ["77", "user", "<行動: 会話を終了する>"]),
            ])

        start(lambda _self, _app, _character_id: None, object(), app, "77")
        refresh(lambda _self, _reset=False: None, app, True)
        refresh(lambda _self, _reset=False: None, app, True)

        labels = [entry["text"] for entry in app.buttons]
        assert labels.count("この話から依頼を作る（ナエ）") == 1, labels
        assert labels.count("依頼を受ける（話を切り上げる）") == 1, labels
        assert app.buttons[0].get("mod_action") == "generate", app.buttons[0]
        assert app.buttons[1].get("mod_action") == "offer", app.buttons[1]
        assert not ctx.errors, ctx.errors
        print("4 passed, 0 failed")
        return 0
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
