# -*- coding: utf-8 -*-
"""Geminiの売買・エピローグ処理に不足しているuser roleを補う。"""

from instantale_modloader import llm


MANAGER_TARGET = "scripts.llm.llm_manager:send_request"
USER_TEXTS = {
    "shop_item_generator_ordinary": "＜売買する＞",
    "epilogue_pre_evaluator": "＜エピローグを評価する＞",
    "epilogue_generator": "＜エピローグを生成する＞",
}


def _is_gemini_runtime():
    """Gemini送信時だけ補正し、ローカルLLMには触れない。"""
    try:
        return any("gemini" in name.lower() for name in llm.request_modules())
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
            if llm.is_local_runtime() or not _is_gemini_runtime():
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
                    "400_user_role_injector: inserted user role for {}".format(
                        manager_name
                    )
                )

            return orig(*args, **kwargs)

    # send_requestはプロバイダ初期化後に生えるため、標準watcherに任せる。
    llm.watch_aliases(
        ctx,
        [MANAGER_TARGET],
        install,
        label="400_user_role_injector",
    )
