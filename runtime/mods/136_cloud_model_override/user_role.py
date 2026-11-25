# -*- coding: utf-8 -*-
"""Gemini・Claude の売買・エピローグ・自由施設の要約に、不足している user role を補う。

素のゲームはこの4件を system role だけで組んで送る。
Gemini は返答が無く裏で落ち、Claude は system を別の欄へ移すので messages が空になり 400 で断られる（DOC.md「user role の補い」）。

他のファイルと違い、SDK の送信ではなく `llm_manager` の `send_request` で書き換える（ローダの窓口 `llm.on_request`）。
足す本文をリクエストの種類（manager 名）で選ぶためで、SDK の送信からは manager 名が見えない。
差し替えの設定とは関わりなく、Gemini・Claude で送るときは補う。

元は `406_user_role_fix`（MoririnJP 様の提供を共同で仕上げた MOD）で、この MOD に取り込んだ。
"""

from instantale_modloader import llm


USER_TEXTS = {
    "shop_item_generator_ordinary": "＜売買する＞",
    "epilogue_pre_evaluator": "＜エピローグを評価する＞",
    "epilogue_generator": "＜エピローグを生成する＞",
    "free_facility_summary": "＜行動記録を要約する＞",
}
# user role が無いと返答できない送信モジュール（名前の一部）。GPT 系は system だけでも返答できるので入れない。
NEEDS_USER = ("gemini", "claude")


def needs_user_runtime():
    """Gemini か Claude で送るときだけ補い、ローカル LLM と GPT 系には触れない。"""
    try:
        return any(part in name.lower() for name in llm.request_modules() for part in NEEDS_USER)
    except Exception:
        return False


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


def rewrite(manager_name, message):
    """補うなら user を足した新しい messages、触らないなら None。"""
    user_text = USER_TEXTS.get(manager_name) if isinstance(manager_name, str) else None
    if user_text is None:
        return None
    if not isinstance(message, list) or not message:
        return None
    if any(isinstance(item, dict) and item.get("role") == "user" for item in message):
        return None
    return add_user_role(message, user_text)


def install(ctx):
    """送り口はローダの窓口 `llm.on_request` が包む（後から生える送り口の見張りも窓口が持つ）。

    届く範囲は前に自分で包んでいた `llm_manager:send_request` だけ（`targets`）。
    窓口はローカル実行でも呼ぶので、クラウドかどうかはここで見る。
    この MOD は自分のログファイルを持たないので、窓口の WARN も `ctx.log` へ書く。
    """
    def add_user(req):
        if llm.is_local_runtime() or not needs_user_runtime():
            return None
        rewritten = rewrite(req.manager_name, req.message)
        if rewritten is not None:
            ctx.log("cloud model override: inserted user role for {}".format(req.manager_name))
        return rewritten

    llm.on_request(ctx.mod_name or "136_cloud_model_override", ctx, add_user,
                   managers=tuple(USER_TEXTS), targets=llm.send_targets("llm_manager", ns=False),
                   write=ctx.log)
