# -*- coding: utf-8 -*-
"""Gemini・Claudeの売買・エピローグ・自由施設の要約に不足しているuser roleを補う。

素のゲームはこの4件をsystem roleだけで組んで送る。
Geminiは返答が無く裏で落ち、Claudeはsystemを別の欄へ移すのでmessagesが空になり400で断られる
（DOC.md）。
"""

from instantale_modloader import llm


MANAGER_TARGET = "scripts.llm.llm_manager:send_request"
USER_TEXTS = {
    "shop_item_generator_ordinary": "＜売買する＞",
    "epilogue_pre_evaluator": "＜エピローグを評価する＞",
    "epilogue_generator": "＜エピローグを生成する＞",
    "free_facility_summary": "＜行動記録を要約する＞",
}
# user roleが無いと返答できない送信モジュール（名前の一部）。GPT系はsystemだけでも返答できるので入れない。
NEEDS_USER = ("gemini", "claude")
LABEL = "406_user_role_fix"


def _needs_user_runtime():
    """GeminiかClaudeで送るときだけ補正し、ローカルLLMとGPT系には触れない。"""
    try:
        return any(part in name.lower() for name in llm.request_modules() for part in NEEDS_USER)
    except Exception:
        return False


def _message_from(args, kwargs):
    """send_requestのmessageを位置・キーワードの両方から読む。"""
    if len(args) >= 2 and isinstance(args[1], list):
        return args[1], "args"
    message = kwargs.get("message")
    if isinstance(message, list):
        return message, "kwargs"
    return None, None


def _add_user_role(message, user_text):
    """元のmessageを壊さず、systemの直後へuserを1件だけ足す。"""
    rewritten = list(message)
    user_message = {"role": "user", "content": user_text}
    for index, item in enumerate(rewritten):
        if isinstance(item, dict) and item.get("role") == "system":
            rewritten.insert(index + 1, user_message)
            return rewritten
    rewritten.append(user_message)
    return rewritten


def _replace_message(args, kwargs, new_message, where):
    """呼び出し引数を壊さず、messageだけを差し替える。"""
    if where == "args":
        return args[:1] + (new_message,) + args[2:], kwargs
    if where == "kwargs":
        new_kwargs = dict(kwargs)
        new_kwargs["message"] = new_message
        return args, new_kwargs
    return args, kwargs


def apply(ctx):
    """ローダが呼ぶ入口。"""
    def install(target):
        @ctx.wrap(target, required=False, safe=True)
        def manager_send(orig, *args, **kwargs):
            if llm.is_local_runtime() or not _needs_user_runtime():
                return orig(*args, **kwargs)

            manager_name = args[0] if args else kwargs.get("manager_name")
            user_text = (
                USER_TEXTS.get(manager_name)
                if isinstance(manager_name, str) else None
            )
            message, where = _message_from(args, kwargs)
            if user_text is not None and message:
                if any(
                    isinstance(item, dict) and item.get("role") == "user"
                    for item in message
                ):
                    return orig(*args, **kwargs)
                new_message = _add_user_role(message, user_text)
                args, kwargs = _replace_message(
                    args, kwargs, new_message, where
                )
                ctx.log(
                    "{}: inserted user role for {}".format(LABEL, manager_name)
                )

            return orig(*args, **kwargs)

    # send_requestはプロバイダ初期化後に生えるため、標準watcherに任せる。
    llm.watch_aliases(
        ctx,
        [MANAGER_TARGET],
        install,
        label=LABEL,
    )
