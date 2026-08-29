# -*- coding: utf-8 -*-
"""LLM へ出ていく文章が通る**場所**。

MOD が「送る前の本文を書き換える」ためには、
まず**どこで捕まえるか**を知っていなければならない。
それは MOD 固有の判断ではなく、このゲームの読み方（TECH.md §3.2.3 の表でいう**ローダ側**）なので、
ここに1つだけ置く。

    from instantale_modloader.llm import wrap_outgoing

    def rewrite(texts, site):
        return [t.replace("前", "後") for t in texts]   # 変えないなら None

    hooks = wrap_outgoing(ctx, rewrite, label="crime attribution fix")

`rewrite` が受け取るのは**この1回の推論で出ていく文字列の並び**（messages の本文、
または templating 後の prompt 1本）。
戻り値は同じ長さの並びか None。
messages の dict を組み直す・元のリストを壊さない・例外を握って素通しする、
といった後始末はこちらで行う。

## どこに仕掛けるか（経路の実測と経緯は GAME.md §2.12 / VERIFICATION_LOG.md §2.24）

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

クラウド境界で見えるのは呼び出し側が渡した `message` だけで、
send_request の中で足される部分（Gemini のスキーマ文など）には当たらない（GAME.md
§1.8）。

## MOD から LLM に1問だけ聞く（`ask`）

書き換えではなく**自分から聞く**側の口。
こちらも「どこから呼ぶか」がゲームの読み方なので、ここに置く。

    from instantale_modloader import llm

    text = llm.ask(ctx, "mod_my_question", [{"role": "user", "content": "..."}],
                   timeout=30, label="my mod")

`timeout` は**キーワードで必ず渡す**（既定値を置いていない）。
ゲーム側の既定は `timeout=None` ＝ 無期限で、
1回返らないと呼んだ側が永久に止まる ―
`311_` は抽出を1本のワーカーで直列に回しているので以後の抽出が全部止まり、
`300_` は情景描写のスレッドを巻き込む（GAME.md §2.12）。

**送信モジュールを名指ししないこと。**
プロバイダごとに違ううえ、名指しの一覧は必ず古くなる（`300_` / `311_` が `llama_cpp` と `any_server` の2つしか知らないまま、
Gemini / OpenAI / Claude で毎回空振りしていた）。
`resolve_send()` は `llm_manager` の別名を先に見て、
無ければ**前置きで**送信モジュールを走査する。

## 印だけでは足りない場合は MOD 側で止める

印はスレッドに立つので、`chat` が返った後に**別のスレッド**が送る経路には届かない。
同じ文章に二度当たって困るかどうかは書き換えの中身しだいなので、
ここでは面倒を見ない:

  * `119_` … 当てた印（`【犯罪帰属MOD】`）が本文にあれば何もしない＝冪等
  * `111_` … 確率の抽選が二度走ると分母が壊れるので、自分が作った文章の
    ハッシュを覚えておく（`Seen`）

## 適用順は約束しない

複数の MOD がここを使うと、同じ対象に層が重なる。
ローカルの3点は `mod.json` の `after` /
`before` どおりに重なるが、**クラウドの別名は「見張りが先に当てた方が内側」**になる（後生えを待つ時刻が
MOD ごとに違う）。
互いの書き換えが相手の目印を壊さない前提で書くこと。
"""

from __future__ import annotations

import json
import sys
import threading
import time

#: ローカル（llama.cpp）で本文が通る3点。
#: どれが通るかはビルドと経路で変わる。
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"
TEMPLATE_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._apply_chat_template"
POST_TARGET = "llama_cpp_runtime_completion:LlamaCppClient._post_with_model_loading_retry"

#: クラウド（APIキー）経路。
#: 第2要素は `send_request_with_no_structure` か（site 名に
#: `_ns` を付けて見分ける）。
MANAGER_SEND_TARGETS = (
    ("scripts.llm.llm_manager:send_request", False),
    ("scripts.llm.llm_manager:send_request_with_no_structure", True),
)

#: 送信の別名が集まる場所。
#: プロバイダを問わずここを通る（GAME.md §2.12）。
MANAGER_MODULE = "scripts.llm.llm_manager"

#: これが import されていたらローカル実行（クラウドとどちらか片方しか載らない）。
LOCAL_REQUEST_MODULE = "scripts.llm.request_llm_inference_llama_cpp_completion"

#: 送信モジュールの名前空間。
#: プロバイダごとに1つだけ import される（GAME.md §2.12）。
#: 名前を全部並べないのは、Alibaba のモジュール名が未実測だから
#: ― 前置きで拾えば知らないプロバイダでも「llama.cpp ではない」ことは言える。
REQUEST_MODULE_PREFIX = "scripts.llm.request_llm_inference_"

#: ローカル（llama.cpp）でしか import されないモジュール。
#: クラウドで動いていると分かった時点で、
#: ここ宛ての保留は待っても無駄になる（`_arm_deferred`）。
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

    `is_local_runtime()` の否定ではない。
    **起動直後はどちらも False** ― まだ1度も
    LLM を呼んでいなければプロバイダは決まっておらず、
    そこで「クラウドだ」と決めつけると、ローカル実行の保留を取り下げてしまう。

    `any_server`（任意の OpenAI 互換サーバー）は手元のサーバーを指すこともあるので、
    厳密には「クラウド」ではなく**プロセス内の `LlamaCppClient` を通らない経路**。
    保留を取り下げてよいかの判定としてはこれで正しい。
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


# --------------------------------------------------------------------------
# MOD から1問だけ聞く
# --------------------------------------------------------------------------
def manager():
    """`llm_manager` モジュール。まだ import されていなければ None。"""
    return sys.modules.get(MANAGER_MODULE)


def resolve_send(name: str = "send_request_with_no_structure"):
    """送信関数を `(関数, どこから引いたか)` で返す。無ければ `(None, None)`。

    **`llm_manager` の別名を先に見る。**
    どのプロバイダでもここを通るので、送信モジュールを名指しせずに済む（GAME.md
    §2.12）。
    別名は初期化時に後から生えるので、まだ無いときだけ送信モジュール側を**前置きで**走査する ― 名前を並べた一覧は、
    プロバイダが増えた時点で黙って古くなる。
    """
    module = manager()
    if module is not None:
        found = getattr(module, name, None)
        if callable(found):
            return found, MANAGER_MODULE
    for module_name in request_modules():
        found = getattr(sys.modules.get(module_name), name, None)
        if callable(found):
            return found, module_name
    return None, None


def create_structure(ctx, name: str, fields: dict, *, label: str = "llm"):
    """`create_model` で構造化出力の返却型を作る。作れなければ None。

    `fields` は `{"項目名": (型, ...)}`。
    **`Literal` を使わないこと** ― 候補が空の `Literal[]` は
    pydantic が拒否してゲームごと落ちる（`203_probe_create_model` が実際の落ち方を押さえている）。
    真偽も `bool` ではなく `str` で受けて、読み取りは呼び側で行う。
    """
    module = manager()
    factory = getattr(module, "create_model", None) if module is not None else None
    if not callable(factory):
        return None
    try:
        return factory(name, **fields)
    except Exception:
        ctx.log_exc("{}: cannot build the structure {!r}".format(label, name))
        return None


def as_dict(raw):
    """返ってきたものを辞書にする。**形を決めつけない。**

    pydantic のモデル・素の辞書・JSON 文字列のどれで返るかはプロバイダと版で変わる。
    読めなければ None（呼び側が降りる）。
    """
    if isinstance(raw, dict):
        return raw
    for name in ("model_dump", "dict"):
        method = getattr(raw, name, None)
        if callable(method):
            try:
                got = method()
            except Exception:
                continue
            if isinstance(got, dict):
                return got
    if isinstance(raw, str):
        try:
            import json

            got = json.loads(raw)
        except Exception:
            return None
        if isinstance(got, dict):
            return got
    return None


#: 「変更なし」と読む語。**大文字小文字と前後の空白は問わない。**
#: 素の文字列で返るのは、構造化出力を通さない経路と、
#: `create_structure` が bool を str で受けている項目（共通 API の規約）。
FALSE_WORDS = ("false", "no", "0", "", "なし", "無し", "変更なし", "none")

#: 「そうだ」と読む語。
TRUE_WORDS = ("true", "yes", "on", "1", "はい", "あり")


def truthy(value, *, unknown: bool = True) -> bool:
    """モデルが返した真偽を読む。`"false"` と書いてくる相手にも耐える。

    どちらの語にも当たらない文字列を True と False のどちらに倒すかは、
    **項目の意味で決まる**ので `unknown` で受ける:

    | 項目 | 倒す先 | なぜ |
    |---|---|---|
    | `changed`（`311_` / `403_`） | `unknown=True`（既定） | 読めない返答で「変更なし」に倒すと、抽出した内容を黙って捨てる |
    | `content_violation`（`404_`） | `unknown=False` | 読めない返答で「違反あり」に倒すと、普通の台詞が消える |

    文字列でも真偽でもないものは `bool()` に任せる（`None` は False、`0` は False）。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in FALSE_WORDS:
            return False
        if word in TRUE_WORDS:
            return True
        return unknown
    return bool(value)


def strip_fence(text) -> str:
    """```` ```json … ``` ```` で包まれていたら中身だけにする。包みが無ければそのまま。

    包むなとプロンプトで頼んでも、モデルは包む。
    5本の MOD（`311_` / `317_` / `321_` / `403_` / `404_`）が各自で剥がしていて、
    **剥がし方が3通りに枝分かれしていた**
    （行で切るもの、言語の札を決め打ちで並べたもの、正規表現で一度に取るもの）。
    札の並びに無い言語名（```` ```JSON5 ````）を書かれると、
    決め打ちの側だけが札を本文として残して JSON を壊す。
    """
    if not isinstance(text, str):
        return ""
    body = text.strip()
    if not body.startswith("```"):
        return body
    body = body[3:]
    end = body.rfind("```")
    if end >= 0:
        body = body[:end]
    body = body.strip()
    # 残った先頭行が言語の札（`json` / `JSON5` / `text`）なら落とす。
    # 短い ASCII の1語だけを札と見なす ― 本文の1行目を札と読み違えないため。
    head, sep, rest = body.partition('\n')
    head = head.strip()
    if sep and head and len(head) <= 12 and head.isascii() and head.isalnum():
        return rest.strip()
    return body


def parse_json(raw):
    """モデルの返却から辞書を1つ取り出す。読めなければ `None`。

    `as_dict` が「返ってきた**物**の形」（pydantic のモデル・辞書・素の JSON 文字列）
    を均すのに対し、こちらは「返ってきた**文章**」を相手にする。
    構造化出力を使わない経路では、頼んでいなくても前置きと囲みが付く:

        こちらが人物像です。
        ```json
        {"changed": true, "profile": "..."}
        ```
        以上です。

    包みを剥がし、それでも読めなければ**最初の `{` から最後の `}` まで**を試す。
    `[` で始まる配列は読まない（1つの辞書を返す約束の関数なので、
    受けたところで呼び側が扱えない）。

    構造化経路と非構造化経路の**出口を1つにする**ために使う。
    2つの経路それぞれに検証を書くと、片方だけ直したときに黙ってすり抜ける。
    """
    got = as_dict(raw)
    if got is not None:
        return got
    if not isinstance(raw, str):
        return None
    body = strip_fence(raw)
    if not body:
        return None
    try:
        got = json.loads(body)
    except Exception:
        got = None
    if isinstance(got, dict):
        return got
    start, end = body.find("{"), body.rfind("}")
    if not (0 <= start < end):
        return None
    try:
        got = json.loads(body[start:end + 1])
    except Exception:
        return None
    return got if isinstance(got, dict) else None


def ask(ctx, manager_name: str, message, *, timeout, structure=None,
        max_tokens=None, label: str = "llm", write=None):
    """LLM に1問だけ聞く。呼べない・失敗した・読めないときは None。

    戻り値は `structure` を渡したときは辞書（`as_dict` で均したもの）、
    渡さなければ文字列。

    | 引数 | |
    |---|---|
    | `manager_name` | 記録の分かれ目。MOD 専用の名前にする（`output_data/` に別々に残る） |
    | `message` | **必ずリスト**（`[{"role": "user", "content": ...}]`）。素の文字列は `TypeError` になる（GAME.md §2.12） |
    | `timeout` | **キーワードで必ず渡す。** 既定値は置いていない ― ゲーム側の既定は無期限で、1回返らないと呼んだ側が永久に止まる |
    | `write` | MOD 自身のログ関数（かかった秒数と結果を1行）。無くてよい |

    `timeout` を受け付けない未実測のプロバイダでは `TypeError` で失敗して
    None を返す（呼び側は LLM を使わない道へ降りる）。
    渡さずに呼び直さないのは、**止まらないことのほうが大事**だから。
    """
    name = "send_request" if structure is not None else "send_request_with_no_structure"
    send, where = resolve_send(name)
    if send is None:
        if write is not None:
            write("{}: {} unavailable (no provider module is loaded yet)".format(
                manager_name, name))
        return None
    kwargs = {"timeout": timeout}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    args = (manager_name, list(message))
    if structure is not None:
        args += (structure,)
    started = time.monotonic()
    try:
        raw = send(*args, **kwargs)
    except Exception:
        ctx.log_exc("{}: {} failed via {}".format(label, manager_name, where))
        return None
    result = as_dict(raw) if structure is not None else raw
    if write is not None:
        write("{}: {:.1f}s via {}{} -> {!r}".format(
            manager_name, time.monotonic() - started, where,
            " (structured)" if structure is not None else "",
            repr(result)[:300]))
    return result


def watch_aliases(ctx, targets, install, *, label="llm", on_arm=None):
    """**後から生える別名**を見張って、現れた時点で `install(target)` を呼ぶ。

        watch_aliases(ctx, targets, hook_it, label="my probe")

    `llm_manager` の `send_request*` は初期化時に後から生える。
    ローダの保留はモジュール単位なので**属性の後生えは拾わない**（TECH.md §3.4）。
    今在るぶんはその場で当て、無いぶんだけ見張りに回す。

    `install(target)` は当て方そのもの ― 何を仕掛けるかは呼ぶ側が決める。
    `wrap_outgoing` は書き換えを、
    `213_` は観測を仕掛けるので、**当て方までは共有しない**（あちらはローカル実行でも生の引数を見たいが、
    `wrap_outgoing` はローカルでは `llm_manager` 境界を意図的に素通しする）。

    見張りは `ALIAS_POLL_SECONDS` ごと・`ALIAS_WATCH_SECONDS` で諦め・注入し直されたら
    `ctx.superseded()` で降りる。
    **降りるときは必ず何か残す**（黙って死ぬと、
    残りが一生当たらないのに追う手がかりが1つも無い）。

    戻り値は「見張りに回した対象」の並び（すぐ当たったものは入らない）。
    """
    def can_resolve(target):
        try:
            return ctx.resolve(target)[2] is not None
        except Exception:
            return False

    unarmed = []
    for target in targets:
        if can_resolve(target):
            install(target)
        else:
            unarmed.append(target)
    if not unarmed:
        return []

    def loop():
        deadline = time.monotonic() + ALIAS_WATCH_SECONDS
        remaining = list(unarmed)
        while remaining and not ctx.superseded():
            for target in list(remaining):
                if not can_resolve(target):
                    continue
                install(target)
                remaining.remove(target)
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
                    label, ", ".join(remaining), int(ALIAS_WATCH_SECONDS)),
                    level="WARN")
                return
            time.sleep(ALIAS_POLL_SECONDS)

    def guarded():
        try:
            loop()
        except Exception:
            ctx.log_exc("{}: the alias watch stopped; {} target(s) will not be "
                        "wrapped in this run".format(label, len(unarmed)))

    threading.Thread(target=guarded,
                     name="llm_alias_watch:{}".format(label), daemon=True).start()
    return unarmed


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

    `texts` は**空でない str だけ**。
    `site` はログ用の短い名前（`chat` / `template` / `payload` / プロバイダ名
    / プロバイダ名 + `_ns`）。

    `rewrite` が投げた例外は握り、**本文をそのまま送る**。
    ここで止める方が損害が大きい（`ctx.log_exc` に残す）。

    `local` / `cloud` で経路を絞れる。
    `on_arm(target)` は別名を遅れて当てたときに呼ばれる（ログ用）。
    """
    hooks = Hooks(ctx, label)
    # 印は**この登録ごと**。
    # 共有すると、先に通った MOD が後の MOD を塞ぐ。
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

        並びが `send_request(manager_name, message, structure, ...)` でないプロバイダもありうるので、
        位置に決め打ちせず両方を見る。
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
            # ローカル実行ではここでは触らない。
            # send_request は内部で別スレッドに降りるため印が届かず、
            # LlamaCppClient 側の3点と二重に当たる。
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

    # 居る別名は今すぐ、まだ生えていない別名は見張って当てる。
    # 仕組みは `watch_aliases` に切り出してある（`213_` も同じ見張りを要る）。
    ns_of = dict(MANAGER_SEND_TARGETS)
    watch_aliases(ctx, [target for target, _ns in MANAGER_SEND_TARGETS],
                  lambda target: hook_manager_send(target, ns_of[target]),
                  label=label, on_arm=on_arm)
    return hooks
