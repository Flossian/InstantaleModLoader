# -*- coding: utf-8 -*-
"""Gemini・Claude の売買・エピローグ・自由施設の要約に、不足している user role を補う。

素のゲームはこの4件を system role だけで組んで送る。
Gemini は返答が無く裏で落ち、Claude は system を別の欄へ移すので messages が空になり 400 で断られる（DOC.md「user role の補い」）。

他のファイルと違い、SDK の送信ではなく `llm_manager:send_request` を包む。
足す本文をリクエストの種類（manager 名）で選ぶためで、SDK の送信からは manager 名が見えない。
差し替えの設定とは関わりなく、Gemini・Claude で送るときは補う。

元は `406_user_role_fix`（MoririnJP 様の提供を共同で仕上げた MOD）で、この MOD に取り込んだ。
"""

from instantale_modloader import llm


MANAGER_TARGET = "scripts.llm.llm_manager:send_request"
USER_TEXTS = {
    "shop_item_generator_ordinary": "＜売買する＞",
    "epilogue_pre_evaluator": "＜エピローグを評価する＞",
    "epilogue_generator": "＜エピローグを生成する＞",
    "free_facility_summary": "＜行動記録を要約する＞",
}
# user role が無いと返答できない送信モジュール（名前の一部）。GPT 系は system だけでも返答できるので入れない。
NEEDS_USER = ("gemini", "claude")
LABEL = "136_cloud_model_override: user role"


def needs_user_runtime():
    """Gemini か Claude で送るときだけ補い、ローカル LLM と GPT 系には触れない。"""
    try:
        return any(part in name.lower() for name in llm.request_modules() for part in NEEDS_USER)
    except Exception:
        return False


def message_from(args, kwargs):
    """send_request の message を位置・キーワードの両方から読む。"""
    if len(args) >= 2 and isinstance(args[1], list):
        return args[1], "args"
    message = kwargs.get("message")
    if isinstance(message, list):
        return message, "kwargs"
    return None, None


def add_user_role(message, user_text):
    """元の message を壊さず、system の直後へ user を1件だけ足す。"""
    rewritten = list(message)
    user_message = {"role": "user", "content": user_text}
    for index, item in enumerate(rewritten):
        if isinstance(item, dict) and item.get("role") == "system":
            rewritten.insert(index + 1, user_message)
            return rewritten
    rewritten.append(user_message)
    return rewritten


def replace_message(args, kwargs, new_message, where):
    """呼び出し引数を壊さず、message だけを差し替える。"""
    if where == "args":
        return args[:1] + (new_message,) + args[2:], kwargs
    if where == "kwargs":
        new_kwargs = dict(kwargs)
        new_kwargs["message"] = new_message
        return args, new_kwargs
    return args, kwargs


def rewrite(args, kwargs):
    """補うなら `(args, kwargs, manager 名)`、触らないなら None。"""
    manager_name = args[0] if args else kwargs.get("manager_name")
    user_text = USER_TEXTS.get(manager_name) if isinstance(manager_name, str) else None
    if user_text is None:
        return None
    message, where = message_from(args, kwargs)
    if not message:
        return None
    if any(isinstance(item, dict) and item.get("role") == "user" for item in message):
        return None
    new_args, new_kwargs = replace_message(args, kwargs, add_user_role(message, user_text), where)
    return new_args, new_kwargs, manager_name


def install(ctx):
    """send_request はプロバイダの初期化の後に生えるため、共有部品の watcher に任せる。"""
    def wrap(target):
        @ctx.wrap(target, required=False, safe=True)
        def manager_send(orig, *args, **kwargs):
            if llm.is_local_runtime() or not needs_user_runtime():
                return orig(*args, **kwargs)
            found = rewrite(args, kwargs)
            if found is None:
                return orig(*args, **kwargs)
            new_args, new_kwargs, manager_name = found
            ctx.log("cloud model override: inserted user role for {}".format(manager_name))
            return orig(*new_args, **new_kwargs)

    llm.watch_aliases(ctx, [MANAGER_TARGET], wrap, label=LABEL)
