# -*- coding: utf-8 -*-
"""LLM へ出ていく文章が通る**場所**。

MOD が「送る前の本文を書き換える」ためには、まず**どこで捕まえるか**を知って
いなければならない。それは MOD 固有の判断ではなく、このゲームの読み方
（TECH.md §3.2.3 の表でいう**ローダ側**）なので、ここに1つだけ置く。

    from instantale_modloader.llm import wrap_outgoing

    def rewrite(texts, site):
        return [t.replace("前", "後") for t in texts]   # 変えないなら None

    hooks = wrap_outgoing(ctx, rewrite, label="crime attribution fix")

`rewrite` が受け取るのは**この1回の推論で出ていく文字列の並び**（messages の
本文、または templating 後の prompt 1本）。戻り値は同じ長さの並びか None。
messages の dict を組み直す・元のリストを壊さない・例外を握って素通しする、
といった後始末はこちらで行う。

## どこに仕掛けるか（経路の実測と経緯は GAME.md §2.12 / VERIFICATION.md §2.24）

  * ローカル（llama.cpp）: `LlamaCppClient` の chat / _apply_chat_template /
    _post_with_model_loading_retry の3点。どれが通るかはビルドと経路で変わる
  * クラウド（APIキー）: 送信モジュールがプロバイダごとに違うので名指しせず、
    どの経路でも import される **`llm_manager` の別名 `send_request*`** を包む
    （`patch.py` の alias_scan が、同じ関数を持つ全モジュールを張り替える）。
    site 名はラップした元関数の `__module__` から採る＝プロバイダ名が残る

はまりどころ3つ:

  * **1回の推論で1回だけ。** 上の経路は入れ子で通ることがある。登録ごとの
    スレッド印で内側を素通しする（印は `wrap_outgoing` の呼び出しごとに別。
    MOD どうしが互いを塞がないため）
  * **ローカル実行では `llm_manager` 境界に触らない。** send_request は内部で
    別スレッドに降りてから chat を呼ぶため印が届かず、二重に当たる。
    llama.cpp の送信モジュールが import されているかで見分ける
  * **`llm_manager` の別名は初期化時に後から生える。** ローダの保留はモジュール
    単位で、属性の後生えは拾わない。無かったぶんは見張って当てる
    （`ALIAS_POLL_SECONDS` ごと・`ALIAS_WATCH_SECONDS` で諦める・注入し直しで降りる）

クラウド境界で見えるのは呼び出し側が渡した `message` だけで、send_request の中で
足される部分（Gemini のスキーマ文など）には当たらない（GAME.md §1.8）。

## 印だけでは足りない場合は MOD 側で止める

印はスレッドに立つので、`chat` が返った後に**別のスレッド**が送る経路には
届かない。同じ文章に二度当たって困るかどうかは書き換えの中身しだいなので、
ここでは面倒を見ない:

  * `119_` … 当てた印（`【犯罪帰属MOD】`）が本文にあれば何もしない＝冪等
  * `111_` … 確率の抽選が二度走ると分母が壊れるので、自分が作った文章の
    ハッシュを覚えておく（`Seen`）

## 適用順は約束しない

複数の MOD がここを使うと、同じ対象に層が重なる。ローカルの3点は
`mod.json` の `after` / `before` どおりに重なるが、**クラウドの別名は
「見張りが先に当てた方が内側」**になる（後生えを待つ時刻が MOD ごとに違う）。
互いの書き換えが相手の目印を壊さない前提で書くこと。
"""

from __future__ import annotations

import sys
import threading
import time

#: ローカル（llama.cpp）で本文が通る3点。どれが通るかはビルドと経路で変わる。
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"
TEMPLATE_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template"
POST_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry"

#: クラウド（APIキー）経路。第2要素は `send_request_with_no_structure` か
#: （site 名に `_ns` を付けて見分ける）。
MANAGER_SEND_TARGETS = (
    ("scripts.llm.llm_manager:send_request", False),
    ("scripts.llm.llm_manager:send_request_with_no_structure", True),
)

#: これが import されていたらローカル実行（クラウドとどちらか片方しか載らない）。
LOCAL_REQUEST_MODULE = "scripts.llm.request_llm_inference_llama_cpp_completion"

#: 送信モジュールの名前空間。プロバイダごとに1つだけ import される（GAME.md §2.12）。
#: 名前を全部並べないのは、Alibaba のモジュール名が未実測だから ― 前置きで拾えば
#: 知らないプロバイダでも「llama.cpp ではない」ことは言える。
REQUEST_MODULE_PREFIX = "scripts.llm.request_llm_inference_"

#: ローカル（llama.cpp）でしか import されないモジュール。クラウドで動いていると
#: 分かった時点で、ここ宛ての保留は待っても無駄になる（`_arm_deferred`）。
LOCAL_ONLY_MODULES = ("llama_cpp_runtime_completion", LOCAL_REQUEST_MODULE)

#: 「別名の後生え」の見張り。
ALIAS_POLL_SECONDS = 5.0
ALIAS_WATCH_SECONDS = 3600.0


def is_local_runtime() -> bool:
    """ローカル（llama.cpp）で動いているか。クラウドとは排他。"""
    return sys.modules.get(LOCAL_REQUEST_MODULE) is not None


def request_modules() -> list[str]:
    """いま読み込まれている送信モジュール。

    `sys.modules` はゲーム側のスレッドが import している最中に増えるので、
    鍵の並びを先に固めてから引く（走査しながらだと `RuntimeError` になる）。
    """
    return sorted(name for name in list(sys.modules)
                  if name.startswith(REQUEST_MODULE_PREFIX)
                  and sys.modules.get(name) is not None)


def is_cloud_runtime() -> bool:
    """llama.cpp 以外の送信モジュールが読み込まれているか。

    `is_local_runtime()` の否定ではない。**起動直後はどちらも False** ―
    まだ1度も LLM を呼んでいなければプロバイダは決まっておらず、そこで
    「クラウドだ」と決めつけると、ローカル実行の保留を取り下げてしまう。

    `any_server`（任意の OpenAI 互換サーバー）は手元のサーバーを指すことも
    あるので、厳密には「クラウド」ではなく**プロセス内の `LlamaCppClient` を
    通らない経路**。保留を取り下げてよいかの判定としてはこれで正しい。
    """
    return any(name != LOCAL_REQUEST_MODULE for name in request_modules())


def content_of(message):
    """メッセージの本文。dict でなければ None（＝触らない）。"""
    try:
        content = message.get("content")
    except Exception:
        return None
    return content if isinstance(content, str) else None


def provider_of(orig) -> str:
    """包んだ元関数の持ち主から採るプロバイダ名。分からなければ `"cloud"`。"""
    module = (getattr(orig, "__module__", "") or "").rpartition(".")[2]
    if module.startswith("request_llm_inference_"):
        module = module[len("request_llm_inference_"):]
    return module or "cloud"


class Hooks(object):
    """`wrap_outgoing` が仕掛けた口。ログに出す用の見張り結果を持つ。"""

    def __init__(self, ctx, label):
        self._ctx = ctx
        self.label = label
        #: 仕掛けようとした対象（順序を保つ）。
        self.targets = []

    def armed(self):
        """今その名前が存在する対象の短い名前。起動直後は別名がまだ無い。"""
        names = []
        for target in self.targets:
            try:
                _owner, _name, value = self._ctx.resolve(target)
            except Exception:
                value = None
            if value is not None:
                names.append(target.rpartition(".")[2])
        return names


def wrap_outgoing(ctx, rewrite, *, label="llm", local=True, cloud=True,
                  on_arm=None) -> Hooks:
    """LLM へ出ていく本文が通る全経路を包み、`rewrite` に通す。

        rewrite(texts, site) -> 同じ長さの並び / None（変えない）

    `texts` は**空でない str だけ**。`site` はログ用の短い名前
    （`chat` / `template` / `payload` / プロバイダ名 / プロバイダ名 + `_ns`）。

    `rewrite` が投げた例外は握り、**本文をそのまま送る**。ここで止める方が
    損害が大きい（`ctx.log_exc` に残す）。

    `local` / `cloud` で経路を絞れる。`on_arm(target)` は別名を遅れて当てた
    ときに呼ばれる（ログ用）。
    """
    hooks = Hooks(ctx, label)
    # 印は**この登録ごと**。共有すると、先に通った MOD が後の MOD を塞ぐ。
    pass_local = threading.local()

    def begin_pass():
        already = getattr(pass_local, "active", False)
        pass_local.active = True
        return already

    def end_pass(previous):
        pass_local.active = previous

    def safely(site, fn, fallback):
        try:
            return fn()
        except Exception:
            ctx.log_exc("{}: {} pass failed; sending the text untouched".format(
                label, site))
            return fallback

    def rewrite_texts(texts, site):
        """`rewrite` を呼んで、長さが合う並びだけを受け取る。"""
        result = rewrite(texts, site)
        if result is None:
            return None
        result = list(result)
        if len(result) != len(texts):
            ctx.log("{}: {} returned {} text(s) for {} (ignored)".format(
                label, site, len(result), len(texts)), level="WARN")
            return None
        return result if result != texts else None

    def rewrite_messages(messages, site):
        """messages の本文を書き換える。**元のリストは書き換えない**。

        会話履歴としてゲーム側が同じ dict を持ち続けている可能性があるため、
        浅い写しを作って差し替える（`105_` と同じ理由）。
        """
        if not isinstance(messages, list) or not messages:
            return messages
        spots = []
        for index, message in enumerate(messages):
            content = content_of(message)
            if content:
                spots.append((index, content))
        if not spots:
            return messages

        result = rewrite_texts([content for _index, content in spots], site)
        if result is None:
            return messages

        new_messages = list(messages)
        for (index, before), after in zip(spots, result):
            if after == before:
                continue
            replacement = dict(new_messages[index])
            replacement["content"] = after
            new_messages[index] = replacement
        return new_messages

    # ------------------------------------------- ローカル（llama.cpp）の3点
    if local:
        hooks.targets.extend((CHAT_TARGET, TEMPLATE_TARGET, POST_TARGET))

        @ctx.wrap(CHAT_TARGET, required=False)
        def chat(orig, self, model, messages, format=None, *args, **kwargs):
            previous = begin_pass()
            try:
                if not previous:
                    messages = safely(
                        "chat", lambda: rewrite_messages(messages, "chat"), messages)
                return orig(self, model, messages, format, *args, **kwargs)
            finally:
                end_pass(previous)

        @ctx.wrap(TEMPLATE_TARGET, required=False)
        def apply_chat_template(orig, self, model, messages, timeout=None,
                                *args, **kwargs):
            previous = begin_pass()
            try:
                if not previous:
                    messages = safely(
                        "template", lambda: rewrite_messages(messages, "template"),
                        messages)
                return orig(self, model, messages, timeout, *args, **kwargs)
            finally:
                end_pass(previous)

        @ctx.wrap(POST_TARGET, required=False)
        def post_with_retry(orig, self, url, payload, timeout=None, *args, **kwargs):
            # ここだけ messages ではなく templating 済みの prompt 1本。
            previous = begin_pass()
            try:
                if not previous and isinstance(payload, dict):
                    prompt = payload.get("prompt")
                    if isinstance(prompt, str) and prompt:
                        result = safely(
                            "payload", lambda: rewrite_texts([prompt], "payload"), None)
                        if result and result[0] != prompt:
                            # 呼び出し元の dict は変えず、浅い写しを渡す。
                            payload = dict(payload)
                            payload["prompt"] = result[0]
                return orig(self, url, payload, timeout, *args, **kwargs)
            finally:
                end_pass(previous)

    # ------------------------------------------ クラウド（APIキー）の別名包み
    if not cloud:
        return hooks

    hooks.targets.extend(target for target, _ns in MANAGER_SEND_TARGETS)

    def rewrite_message_arg(site, args, kwargs):
        """呼び出しの (args, kwargs) の中の message を書き換える。

        並びが `send_request(manager_name, message, structure, ...)` でない
        プロバイダもありうるので、位置に決め打ちせず両方を見る。
        """
        if len(args) >= 2 and isinstance(args[1], list):
            replaced = rewrite_messages(args[1], site)
            if replaced is not args[1]:
                args = args[:1] + (replaced,) + args[2:]
        elif isinstance(kwargs.get("message"), list):
            replaced = rewrite_messages(kwargs["message"], site)
            if replaced is not kwargs["message"]:
                kwargs = dict(kwargs, message=replaced)
        return args, kwargs

    def hook_manager_send(target, ns):
        @ctx.wrap(target, required=False)
        def manager_send(orig, *args, **kwargs):
            # ローカル実行ではここでは触らない。send_request は内部で別スレッドに
            # 降りるため印が届かず、LlamaCppClient 側の3点と二重に当たる。
            if is_local_runtime():
                return orig(*args, **kwargs)
            previous = begin_pass()
            try:
                if not previous:
                    site = provider_of(orig) + ("_ns" if ns else "")
                    args, kwargs = safely(
                        site, lambda: rewrite_message_arg(site, args, kwargs),
                        (args, kwargs))
                return orig(*args, **kwargs)
            finally:
                end_pass(previous)

    def resolvable(target):
        try:
            return ctx.resolve(target)[2] is not None
        except Exception:
            return False

    # 居る別名は今すぐ包む。まだ生えていない別名は見張りに回す（起動直後の
    # 注入では llm_manager に send_request がまだ無い。docstring のとおり）。
    unarmed = []
    for target, ns in MANAGER_SEND_TARGETS:
        if resolvable(target):
            hook_manager_send(target, ns)
        else:
            unarmed.append((target, ns))
    if not unarmed:
        return hooks

    def watch_alias():
        deadline = time.monotonic() + ALIAS_WATCH_SECONDS
        remaining = list(unarmed)
        while remaining and not ctx.superseded():
            for item in list(remaining):
                target, ns = item
                if not resolvable(target):
                    continue
                hook_manager_send(target, ns)
                remaining.remove(item)
                ctx.log("{}: late-armed on {} (the alias appeared)".format(
                    label, target))
                if on_arm is not None:
                    try:
                        on_arm(target)
                    except Exception:
                        ctx.log_exc("{}: on_arm failed".format(label))
            if not remaining:
                return
            if time.monotonic() > deadline:
                ctx.log("{}: gave up waiting for {} ({}s)".format(
                    label, ", ".join(t for t, _n in remaining),
                    int(ALIAS_WATCH_SECONDS)), level="WARN")
                return
            time.sleep(ALIAS_POLL_SECONDS)

    threading.Thread(target=watch_alias,
                     name="llm_alias_watch:{}".format(label), daemon=True).start()
    return hooks
