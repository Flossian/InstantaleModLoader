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

## 頼みの名前で振り分けて書き換える（`on_request`）

`wrap_outgoing` は本文の文字列だけを渡し、頼みの名前（`manager_name`）と返却型を渡さない。
頼みの名前で選んで messages の1件に文を足す・返却型に欄を足す MOD は、送り口を各自で包んでいた
（136 / 334 / 336 / 339 / 342 / 347 / 401）。包むのはここの1か所にし、MOD は登録する:

    llm.on_request(owner, ctx, fn, managers=("referee_attack",), after=fn2, write=write)
    fn(req)    -> 新しい messages / None（`req.structure` に型を入れれば返却型も替わる）
    fn2(req)   `orig` の後（`req.result`）

書き換えの部品は `edit_turn`（messages の1件の本文）と `extend_structure`（返却型に欄を足した子の型）。
決まりは `on_request` の説明にある。

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
#: main_026 から、ゲームは部品ごとに `send_request` を言語の包み（`scripts.llm.output_language.localized_request`）
#: で包み直すので、部品ごとに別の関数になった。`llm_manager` だけを包んで別名を掴み直す形では、
#: 戦闘・世界の生成・キャラの作成の頼みに届かなかった（VERIFICATION.md §3.100）。部品ごとに包む。
#: 前の版のように同じ関数を指していれば、`watch_aliases` が別名として見分けて二重には包まない。
MANAGER_SEND_TARGETS = (
    ("scripts.llm.llm_manager:send_request", False),
    ("scripts.llm.llm_manager:send_request_with_no_structure", True),
    ("scripts.llm.llm_manager_battle:send_request", False),
    ("scripts.llm.llm_manager_battle:send_request_with_no_structure", True),
    ("scripts.llm.llm_manager_world_generate:send_request", False),
    ("scripts.llm.llm_manager_world_generate:send_request_with_no_structure", True),
    ("scripts.llm.llm_manager_character_create:send_request", False),
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

#: ゲームが送り口を作り直す所。題の画面の「開始する」と世界の生成の画面に入るたびに、メインスレッドで走る。
#: main_026 から、4つの部品の `send_request*` をその都度新しい関数で置き直すので、当てた包みが外れる
#: （main_025 までは置き直してもローダが張り替えたプロバイダの関数が入った。GAME.md §2.12）。
REBUILD_TARGET = "__main__:AIManager.set_ai_models"


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


#: クラウドの SDK（openai / anthropic）の資源の基底クラスが居るモジュール。
SDK_RESOURCE_MODULES = ("openai._resource", "anthropic._resource")

#: 資源が作られたときにクライアントから掴む送りの手（`SyncAPIResource.__init__`）。
SDK_RESOURCE_VERBS = ("_get", "_post", "_patch", "_put", "_delete", "_get_api_list")


def rebind_cloud_resources() -> int:
    """クラウドの SDK の資源が掴んでいる送りの手を、クライアントの今の手に掴み直させる。

    SDK の資源（`client.messages` など）は、作られたときに `self._post = client.post` と
    その時点の関数を掴む。注入でクラスの `post` を包み直しても、作られた後の資源は
    前の世代の包みを呼び続け、切った MOD の差し替えもゲームを閉じるまで効いたままになる
    （`136_` で、差し替えを切って注入し直しても前の宛先へ送り続けた。VERIFICATION.md §3.88）。
    注入の終わりとローダを外すときに呼ぶ。掴み直した手の数を返す。
    SDK を読み込んでいなければ何もしない。
    """
    import gc

    classes = []
    for name in SDK_RESOURCE_MODULES:
        module = sys.modules.get(name)
        cls = getattr(module, "SyncAPIResource", None) if module is not None else None
        if isinstance(cls, type):
            classes.append(cls)
    if not classes:
        return 0
    kinds = tuple(classes)
    count = 0
    for obj in gc.get_objects():
        try:
            if not isinstance(obj, kinds):
                continue
        except Exception:
            continue                       # 消えかけの弱参照の代理など
        client = getattr(obj, "_client", None)
        held_by = getattr(obj, "__dict__", None)
        if client is None or not isinstance(held_by, dict):
            continue
        for verb in SDK_RESOURCE_VERBS:
            held = held_by.get(verb)
            fresh = getattr(client, verb[1:], None)
            if held is None or fresh is None:
                continue
            if getattr(held, "__func__", None) is getattr(fresh, "__func__", None):
                continue
            try:
                setattr(obj, verb, fresh)
                count += 1
            except Exception:
                continue
    return count


#: `config.json` の `ai_setting.llm_inference` で、API キーでクラウドを使う値。
CLOUD_API_KEY = "cloud_api_key"


def game_choice(path=None):
    """ゲームの設定画面で選ばれている LLM。`{"inference", "provider", "model"}`。読めなければ None。

    `inference` は `local` / `cloud_api_key` など、`provider` は `OpenAI API` / `Claude API` など
    （ゲームの設定画面の表記そのまま）、`model` はゲームの一覧の名前。
    切り替えるとゲームが起動し直すので、注入の時に1回読めば足りる（`imagegen.backend` と同じ読み方。`saves._game_ai_setting`）。
    """
    from . import saves

    setting = saves._game_ai_setting(path)
    if setting is None:
        return None
    try:
        cloud = setting.get("cloud_model_setting") or {}
        return {"inference": setting.get("llm_inference"),
                "provider": cloud.get("cloud_llm_provider"),
                "model": cloud.get("cloud_llm")}
    except Exception:
        return None


def runs_bundled_llm(path=None) -> bool:
    """ゲーム同梱の llama.cpp で推論しているか。

    同梱の llama-server は `--ctx-size` をゲームが 16384 に固定する（設定欄に書いても外される。
    GAME.md §2.12.1）。
    v1.19.0 では、MOD が頼みに足す文を短く切るのをこのときだけにするために足した。
    v1.20.0 で同梱の MOD（`143_` / `144_`）は切るのをやめた（16384 に収まると測れた。VERIFICATION.md §3.95）。
    公開している口なので残す。v2.0.0 で外す（TECH.md §3.9「次の API（v2.0.0）で外すもの」）。
    まだ1度も LLM を呼んでいない間は送信モジュールで決まらないので、`config.json` の選択で決める。
    読めなければ同梱の側（短い方）に倒す。
    """
    if is_local_runtime():
        return True
    if is_cloud_runtime():
        return False
    choice = game_choice(path)
    return choice is None or choice.get("inference") == "local"


def text_limit(local_chars, path=None):
    """MOD が頼みに足す文の上限（字）。同梱の llama.cpp なら `local_chars`、それ以外は None（切らない）。

    同梱の MOD はもう使っていない。v2.0.0 で外す（`runs_bundled_llm` と同じ）。
    """
    return local_chars if runs_bundled_llm(path) else None


#: 任意の OpenAI 互換サーバーの送信モジュール。
ANY_SERVER_REQUEST_MODULE = "scripts.llm.request_llm_inference_any_server"

#: `config.json` の `cloud_llm_provider` で、任意の OpenAI 互換サーバーを選んだときの値（ゲームの設定画面の表記）。
ANY_SERVER_PROVIDER = "任意OpenAI互換サーバー"


def is_any_server_runtime() -> bool:
    """任意の OpenAI 互換サーバー（LM Studio など）で推論しているか。"""
    return sys.modules.get(ANY_SERVER_REQUEST_MODULE) is not None


def is_grammar_runtime(path=None) -> bool:
    """返却型を grammar で縛る経路か。内蔵の llama.cpp と、任意の OpenAI 互換サーバー。

    この経路では型の配列に件数の上限（`maxItems`）を付けると、上限を超える要素を書き始められなくなる。
    上限の無い配列は、モデルが書き足し続けてコンテキスト長を使い切ることがある（`139_`。
    VERIFICATION.md §3.135）。
    互換サーバーの宛先はクラウドのこともあるが、上限を付けて困る形は測れていない。
    まだ1度も LLM を呼んでいない間は送信モジュールで決まらないので、`config.json` の選択で決める。
    読めなければ False（上限を付けない側）。
    """
    if is_local_runtime() or is_any_server_runtime():
        return True
    if is_cloud_runtime():
        return False
    choice = game_choice(path)
    if choice is None or not isinstance(choice.get("inference"), str):
        return False
    return choice.get("inference") != CLOUD_API_KEY or choice.get("provider") == ANY_SERVER_PROVIDER


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


def edit_turn(message, edit, *, role="system", last=False, unless=None):
    """messages の1件の本文を `edit` で書き換えた写しを返す。書き換えなければ None。

        rule = lambda text: text.rstrip() + "\\n\\n" + RULE
        llm.edit_turn(message, rule)                                   # 先頭の system の末尾に足す
        llm.edit_turn(message, lambda text: text + NOTE, role="user", last=True, unless=MARK)

    | 引数 | |
    |---|---|
    | `edit(content)` | 本文（str）を受けて、新しい本文か None を返す。None ならその発言は飛ばして次の候補を試す |
    | `role` | 対象の発言の role。None なら role を問わない |
    | `last` | 真なら後ろから探す（最後の user に足す形） |
    | `unless` | この文字列がどれかの発言の本文に入っていたら何もしない（二重に足さない印） |

    `edit` が新しい本文を返した最初の発言（`last` なら最後の発言）だけを差し替える。
    「決まった行の直後に差し込み、その行が無ければ次の発言を見る」形（`342_` の手引き・`339_` の勢力の段）は、
    行が無いときに `edit` が None を返せばよい。
    次の場合は None を返す: `message` が list でない・対象の発言が無い・`edit` が全部 None を返した・
    返した本文が元と同じ・`unless` の印がもう入っている。
    `edit` が str と None 以外を返したら `TypeError`（呼ぶ側の書き間違い）。

    ゲームが持つ list と dict は書き換えない（list を浅く写し、差し替える1件だけ dict を写す）。
    ゲームは同じ list を使い回すことがあり、書き換えると頼むたびに文が積み上がる。
    写しは `talk_summary.with_instruction`・`334_` の `append_note`・`342_` の `with_vfx_guide`・`336_` の
    `with_instruction` にあった（足す場所と区切りがそれぞれ違うので、場所は `role` / `last` で、区切りは `edit` で決める）。
    """
    if not isinstance(message, list) or not callable(edit):
        return None
    if unless:
        for turn in message:
            content = content_of(turn)
            if content is not None and unless in content:
                return None
    indexes = range(len(message) - 1, -1, -1) if last else range(len(message))
    for index in indexes:
        turn = message[index]
        if not isinstance(turn, dict):
            continue
        content = content_of(turn)
        if content is None or (role is not None and turn.get("role") != role):
            continue
        new = edit(content)
        if new is None:
            continue
        if not isinstance(new, str):
            raise TypeError("edit_turn: edit() returned {!r} (a str or None)".format(type(new)))
        if new == content:
            return None
        copied = list(message)
        copied[index] = dict(turn, content=new)
        return copied
    return None


#: `extend_structure` が作った型の控えの数。直近のこれだけを強く持つ。
#: 返却型を頼むたびに作り直す送り口（戦闘の審判。`342_`）があり、型ごとに持ち続けると溜まる。
#: 子の型は `__bases__` から元の型を強く掴むので、弱い鍵の辞書にしても元の型が回収されない（`342_` が踏んだ）。
#: 数で止めれば、定数の型（会話の返事。`347_`）は使い回し、作り直される型は古い順に落ちる。
EXTENDED_LIMIT = 64
_EXTENDED = {}
_EXTENDED_LOCK = threading.Lock()


def model_factory():
    """pydantic の `create_model`。pydantic を先に、無ければ `llm_manager` から引く。どちらも無ければ None。

    ゲームの `llm_manager` は pydantic の同じ関数を import しているので、どちらでも同じ型ができる。
    `342_` / `347_` は pydantic から、`talk_summary` / `336_` は `llm_manager` から引いていた。
    """
    pydantic_module = sys.modules.get("pydantic")
    factory = getattr(pydantic_module, "create_model", None) if pydantic_module is not None else None
    if callable(factory):
        return factory
    module = manager()
    factory = getattr(module, "create_model", None) if module is not None else None
    return factory if callable(factory) else None


def structure_fields(structure):
    """pydantic の型の欄の辞書（`model_fields`、古い版は `__fields__`）。型でなければ None。"""
    for attr in ("model_fields", "__fields__"):
        fields = getattr(structure, attr, None)
        if isinstance(fields, dict):
            return fields
    return None


def extend_structure(structure, fields, *, replace=False, name=None):
    """返却型 `structure` に欄を足した子の型（`__base__=structure`）。足せなければ None。

        child = llm.extend_structure(structure, {"expression": (str, Field(..., description="..."))})

    | 引数 | |
    |---|---|
    | `fields` | `{"欄の名前": 型}` か `{"欄の名前": (型, 既定値か Field)}`。型だけなら必須（`...`） |
    | `replace` | 偽（既定）なら、`structure` に既にある欄の名前が来たら None（足さない。`347_` の形）。真なら上書きする（`342_` の `vfx` の選択肢を広げる形。元の説明・既定値を引き継ぐかは呼ぶ側が Field で決める） |
    | `name` | 子の型の名前。省けば `structure` と同じ名前（スキーマ文に出る名前を変えない） |

    子の型は元の型の子なので、ゲームは足した欄を無視してそのまま読める。
    足した欄はスキーマ文と grammar の両方に出る（`send_request` の中で型から作られる）。
    答えから足した欄を抜いて元の型に戻すのは `talk_summary.restore`。

    同じ型に同じ `fields`（`repr` で比べる。説明が変われば別の組）なら、前に作った子の型を返す。
    控えは直近 `EXTENDED_LIMIT` 組（定数のところに理由）。
    `structure` が pydantic の型でない・`create_model` が引けない・作るのに失敗したら None（失敗はローダのログに残す）。
    写しは `342_` の `extend_structure`・`347_` の `widen`・`336_` と `talk_summary` の `extended` にあった。
    """
    have = structure_fields(structure)
    if not isinstance(structure, type) or have is None or not isinstance(fields, dict) or not fields:
        return None
    if not replace and any(field in have for field in fields):
        return None
    specs = {}
    for field, spec in fields.items():
        specs[str(field)] = spec if isinstance(spec, tuple) and len(spec) == 2 else (spec, ...)
    key = (id(structure), bool(replace), name, tuple((field, repr(spec)) for field, spec in specs.items()))
    with _EXTENDED_LOCK:
        known = _EXTENDED.get(key)
        if known is not None and known[0] is structure:
            _EXTENDED[key] = _EXTENDED.pop(key)        # 使った組を新しい側へ
            return known[1]
    factory = model_factory()
    if factory is None:
        return None
    try:
        child = factory(name or getattr(structure, "__name__", "Structure"), __base__=structure, **specs)
    except Exception:
        from . import log_exc
        log_exc("llm: cannot extend the structure {!r} with {}".format(
            getattr(structure, "__name__", structure), ", ".join(specs)))
        return None
    with _EXTENDED_LOCK:
        _EXTENDED.pop(key, None)
        _EXTENDED[key] = (structure, child)
        while len(_EXTENDED) > EXTENDED_LIMIT:
            _EXTENDED.pop(next(iter(_EXTENDED)))
    return child


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
FALSE_WORDS = ("false", "no", "0", "", "なし", "無し", "変更なし", "none", "いいえ")

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


def whole(value, low=None, high=None, *, choices=None):
    """モデルが返した整数（段・尺度・増減）を読む。読めなければ None。

    `truthy` と並ぶ「LLM の答えを均す」口。写しの `336_` の `clamp_scale`・`337_` の `clamp_step`・
    `339_` の `to_int` と同じ答えにそろえた:

    - 真偽値は None（`int` の仲間だが、段の答えではない）
    - 文字列は前後の空白と先頭の `+` を外す（`"+2"` / `" 3 "`）
    - `int(float(...))` で整数にする。小数は 0 の方向へ切り捨てる（`"2.7"` は 2、`-2.7` は -2）
    - 数にならない・NaN・inf は None
    - `low` / `high` を渡したら、その範囲に止める（None の側は止めない）

    `choices` を渡すと、範囲に止めた後の値がその中に無ければ None にする（`341_` の `clamp_stance` の形）。
    このときは整数でない小数を切り捨てずに None にする（決まった値のどれかを選ぶ答えで、
    1.5 を 1 と読むと別の選択になるため。`341_` と同じ）。
    `346_` の写しは四捨五入していたので、寄せると 0.5 以上の端数の読み方が変わる。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("+")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if choices is not None and not number.is_integer():
        return None
    result = int(number)
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    if choices is not None and result not in choices:
        return None
    return result


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
        max_tokens=None, label: str = "llm", write=None, errors=None):
    """LLM に1問だけ聞く。呼べない・失敗した・読めないときは None。

    戻り値は `structure` を渡したときは辞書（`as_dict` で均したもの）、
    渡さなければ文字列。

    | 引数 | |
    |---|---|
    | `manager_name` | 記録の分かれ目。MOD 専用の名前にする（`output_data/` に別々に残る） |
    | `message` | **必ずリスト**（`[{"role": "user", "content": ...}]`）。素の文字列は `TypeError` になる（GAME.md §2.12） |
    | `timeout` | **キーワードで必ず渡す。** 既定値は置いていない。待ちの目安で、過ぎたらログに出す（打ち切らない。下） |
    | `write` | MOD 自身のログ関数（かかった秒数と結果を1行）。無くてよい |
    | `errors` | list を渡すと、送信が投げた例外をそこへ積む。None の理由（呼べなかった `TypeError` か、通信エラーか）を呼び側で分けたいときに使う。無くてよい |

    `timeout` はゲームの送り口へは渡さない。送り口は受け取っても待ちを打ち切らない
    （main_026 で、クラウドもローカルも `timeout=0.5` で返事を最後まで待った。VERIFICATION.md §3.101）が、
    守るプロバイダでは打ち切られ、下の「打ち切らない」と逆になるため。
    ここでも打ち切らない。`timeout` 秒を過ぎたら WARN を1行出し、返事が来るまで待ち続ける。
    ローカルの速さは GPU で何倍も違い、上限は限られた環境の実測から決めているので、打ち切ると遅い環境で
    MOD の働きが毎回黙って消える。止まったきりの頼みは WARN で見分ける（VERIFICATION.md §3.101）。
    """
    name = "send_request" if structure is not None else "send_request_with_no_structure"
    send, where = resolve_send(name)
    if send is None:
        if write is not None:
            write("{}: {} unavailable (no provider module is loaded yet)".format(
                manager_name, name))
        return None
    kwargs = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    args = (manager_name, list(message))
    if structure is not None:
        args += (structure,)
    started = time.monotonic()
    box = {}

    def call():
        try:
            box["raw"] = send(*args, **kwargs)
        except Exception as exc:
            box["exc"] = exc

    worker = threading.Thread(target=call, name="llm_ask:{}".format(manager_name), daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        ctx.log("{}: {} via {} has not returned after {}s; still waiting (slow LLM, or a stuck request)".format(
            label, manager_name, where, timeout), level="WARN")
        worker.join()
        ctx.log("{}: {} returned after {:.1f}s (over the {}s guide)".format(
            label, manager_name, time.monotonic() - started, timeout), level="WARN")
    if "exc" in box:
        try:
            raise box["exc"]
        except Exception:
            ctx.log_exc("{}: {} failed via {}".format(label, manager_name, where))
        if errors is not None:
            errors.append(box["exc"])
        return None
    raw = box.get("raw")
    result = as_dict(raw) if structure is not None else raw
    if write is not None:
        write("{}: {:.1f}s via {}{} -> {!r}".format(
            manager_name, time.monotonic() - started, where,
            " (structured)" if structure is not None else "",
            repr(result)[:300]))
    return result


#: `watch_aliases` の登録（ctx, label, rearm）。作り直しの後に、並んだ順に包み直す。
#: ローダが読み直されるのは注入し直すたびで、boot ごとではない（遅れて当て直す boot は同じモジュールの
#: `boot` を呼ぶ。`__init__._state` の説明と `tools/injector.py` の `BOOTSTRAP_TEMPLATE`）。
#: それでも sys に置くのは、注入し直した後も、前の注入の `set_ai_models` の包み（読み直す前のモジュールの
#: コード）が走りきるまで、その包みが `rearm_watches` を呼ぶため。モジュール変数にすると、その包みは
#: 前の注入の登録しか見えない。sys に置けば、前の世代の包みからも今の世代の登録を包み直せる
#: （用済みの ctx の登録は `_superseded` で外す）。錠も同じ理由で sys に置き、新旧のコードが同じ並びを取り合わないようにする。
_WATCHES = vars(sys).setdefault("__instantale_llm_watches__", [])
_WATCHES_LOCK = vars(sys).setdefault("__instantale_llm_watches_lock__", threading.Lock())

#: `set_ai_models` が走っている間の旗。注入し直した後は、前の注入の包みが立てた旗を
#: 読み直した後のモジュールのコードが読むので、sys に置く（`_WATCHES` と同じ理由）。
_REBUILDING_ATTR = "__instantale_llm_rebuilding__"

#: `watch_aliases` の当て方が「作り直しの最中なので後で当てる」と返すときの値。
LATER = "later"


def _rebuilding_state():
    return vars(sys).setdefault(_REBUILDING_ATTR, {"depth": 0})


def rebuilding():
    """ゲームが送り口を作り直している最中か（`set_ai_models` の中）。"""
    try:
        return _rebuilding_state()["depth"] > 0
    except Exception:
        return False


def _clean_provider_senders(ctx):
    """プロバイダの送り口に残ったこちらの包みを外し、素の関数に戻す。外した数。

    作り直しの途中を包むと、同じ関数を持つプロバイダの属性までこちらの包みに張り替わり、
    以後の作り直しで言語の包みがそれを包み続けた（VERIFICATION.md §3.100）。
    ローカルは起動直後に4つの部品がプロバイダの関数そのもので、別名の張り替えで同じ形になる。
    包みは1回ずつ通るので働きは変わらないが、ここで戻して部品ごとの形に揃える。
    main_026 は部品ごとに包むので、プロバイダの属性にこちらの包みは要らない。
    部品の送り口と同じ関数なら、別名の張り替えでそうなった正規の形なので触らない
    （ローカルの `send_request_with_no_structure` は言語の包みが掛からず、プロバイダの関数そのもの）。
    """
    from . import patch as _patch
    sender_targets = {target for target, _ns in MANAGER_SEND_TARGETS}
    parts = set()
    for target, _ns in MANAGER_SEND_TARGETS:
        module_name, _, attr = target.partition(":")
        part = sys.modules.get(module_name)
        if part is not None and getattr(part, attr, None) is not None:
            parts.add(id(getattr(part, attr)))
    count = 0
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(REQUEST_MODULE_PREFIX):
            continue
        for attr in ("send_request", "send_request_with_no_structure"):
            value = getattr(module, attr, None)
            if value is None or id(value) in parts:
                continue
            # 剥がすのは、送り口を包んだ層が別名の張り替えで載ったものだけ。
            # プロバイダの関数を直に包んだ層（計測の `205_` など）は残す。
            below = value
            while below is not None and _patch.is_ours(below) and                     getattr(below, _patch.PATCH_MARK, None) in sender_targets:
                below = getattr(below, "__original__", None)
            if below is value or below is None:
                continue
            setattr(module, attr, below)
            count += 1
            ctx.log("llm: put the plain {}.{} back before the game rebuilds the senders".format(name, attr))
    return count


def _register_watch(ctx, label, rearm):
    with _WATCHES_LOCK:
        _WATCHES[:] = [w for w in _WATCHES if not _superseded(w[0])]
        _WATCHES.append((ctx, label, rearm))


def _superseded(ctx):
    # 生死の判定はローダ本体の alive に1つにまとめた（窓口と同じく、例外なら生きているとみなす）。
    from . import alive
    return not alive(ctx)


def rearm_watches(ctx):
    """ゲームが送り口を作り直した後に、今の世代の `watch_aliases` の登録を順に包み直す。包み直した数。"""
    with _WATCHES_LOCK:
        watches = [w for w in _WATCHES if not _superseded(w[0])]
    count, labels = 0, []
    for _wctx, label, rearm in watches:
        try:
            done = rearm()
        except Exception:
            ctx.log_exc("{}: cannot wrap the rebuilt senders again".format(label))
            continue
        if done:
            count += len(done)
            labels.append(label)
    if count:
        ctx.log("llm: the game rebuilt the senders; wrapped {} again for {} watch(es): {}".format(
            count, len(labels), ", ".join(labels)))
    return count


def install_rebuild_hook(ctx):
    """`set_ai_models` の直後に `rearm_watches` を呼ぶ。boot が MOD の適用の前に1回呼ぶ。

    `set_ai_models` が走っている間は旗（`rebuilding`）を立て、boot の錠も取れれば持つ。旗の間は、
    遅れて当て直す boot の適用も見張りも送り口を包まない（`watch_aliases` の `LATER`）。
    途中では送り口にまだプロバイダの素の関数が入っていて、それを包むと、同じ関数を持つ
    プロバイダの属性までこちらの包みに張り替わった。続く言語の包みがそれを包み、こちらの印を写し取った
    （2026-10-15 の実機。起動の約6秒後に走る boot #2 と「開始する」が重なった。VERIFICATION.md §3.100）。
    作り直しの直後から頼みが出ていくことがあるので、見張りの間隔（`ALIAS_POLL_SECONDS`）を待たず同じスレッドで当てる。
    別の boot が錠を持っているときは、画面を止めないよう待たずに走らせ、別スレッドで当てる。
    """
    from . import _boot_lock

    def run():
        try:
            rearm_watches(ctx)
        except Exception:
            ctx.log_exc("llm: cannot wrap the rebuilt senders again")

    @ctx.wrap(REBUILD_TARGET, required=False)
    def set_ai_models(orig, *args, **kwargs):
        try:
            _clean_provider_senders(ctx)
        except Exception:
            ctx.log_exc("llm: cannot check the provider senders")
        lock = _boot_lock()
        held = lock.acquire(blocking=False)
        state = _rebuilding_state()
        state["depth"] += 1
        try:
            return orig(*args, **kwargs)
        finally:
            state["depth"] -= 1
            # この包みが前の世代のものでも走らせる（包み直すのは今の世代の登録だけ）
            try:
                if held:
                    run()
                else:
                    threading.Thread(target=run, name="llm_rearm", daemon=True).start()
            finally:
                if held:
                    lock.release()


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

    ゲームは `set_ai_models` のたびに送り口を新しい関数で置き直す（main_026 から。GAME.md §2.12）。
    置き直された対象は、ローダが `set_ai_models` の直後に、登録した順に包み直す（`rearm_watches`）。

    戻り値は「見張りに回した対象」の並び（すぐ当たったものは入らない）。
    """
    def can_resolve(target):
        try:
            return ctx.resolve(target)[2] is not None
        except Exception:
            return False

    def alias_of_armed(target):
        """`target` の今の関数が、同じ並びの**別の**対象に当てた包みの別名か。

        包むと同じ関数を指す別名もローダが張り替えるので、別名をもう一度包むと二重になる
        （main_025 までは `llm_manager_battle.send_request` などが `llm_manager.send_request` と同じ関数だった）。
        """
        from . import patch as _patch
        try:
            current = ctx.resolve(target)[2]
        except Exception:
            return False
        if not _patch.is_ours(current):
            return False      # ゲームの関数（こちらの印を写し取っただけの言語の包みも）
        mark = getattr(current, _patch.PATCH_MARK, None)
        return bool(mark) and mark != target and mark in targets

    # 対象 → この見張りが当てた包みの鍵（`patch.KEY_MARK`）。今の値の鎖に残っていなければ、ゲームが作り直した。
    # 鍵で見るので、`drift` の見回りが同じ包みを付け直した後も二重にしない。
    placed = {}

    def top_of(target):
        try:
            return ctx.resolve(target)[2]
        except Exception:
            return None

    def holds(target):
        """`target` の今の値の鎖に、この見張りが当てた包みが残っているか。"""
        from . import patch as _patch
        mine = placed.get(target)
        if mine is None:
            return False
        kind, mark = mine
        value = top_of(target)
        if kind == "key":
            return _patch.layer_present(target, mark, value) is True
        return any(node is mark for node in _patch.chain(value))

    def install_once(target):
        """当てたら真。もう当たっている・別名だったら偽。作り直しの最中なら `LATER`（後で当てる）。"""
        from . import patch as _patch
        if rebuilding():
            return LATER
        if holds(target) or alias_of_armed(target):
            return False
        before = top_of(target)
        install(target)
        after = top_of(target)
        if after is None or after is before:
            placed.pop(target, None)
        elif getattr(after, _patch.KEY_MARK, None) is not None:
            placed[target] = ("key", getattr(after, _patch.KEY_MARK))
        else:
            placed[target] = ("obj", after)      # 鍵の無い包み（検査の偽物など）はそのもので見る
        return True

    # 遅れて当てる包みの帰属（呼んだ MOD）。見張りのスレッドでは `ctx._mod` はもう空なので、呼ばれた時点で控える。
    # ローダ自身の見張り（`stalls`）は boot が `(loader)` で当てている。当て直しでも同じ名前にしないと、
    # 包みの鍵（`patch.KEY_MARK`）が変わり、`drift` の見回りが別の包みと見て二重に付け直した。
    from . import patch_registry as _registry_now
    owner = getattr(ctx, "_mod", None) or _registry_now.current_mod()

    unarmed = []
    for target in targets:
        if not can_resolve(target) or install_once(target) is LATER:
            unarmed.append(target)

    def arm(target):
        """boot の錠の中で、まだ今の世代かを確かめてから当てる。

        当てたら真、当てるまでもなかったら偽、作り直しの最中なら `LATER`、用済みの世代なら None。
        錠の外で当てると、確かめた直後に次の boot が始まったとき、古い世代の閉包が
        新しい世代の印で当たり（`ctx.wrap` は今の `patch` を引く）、新しい世代の包みと二重に掛かった。
        帰属もその瞬間に適用中の別の MOD になっていた。
        """
        from . import _boot_lock
        from . import patch_registry as _registry
        with _boot_lock():
            if ctx.superseded():
                return None
            _registry.begin_mod(owner or "<loader>")
            try:
                return install_once(target)
            finally:
                _registry.end_mod()

    def notify(target):
        if on_arm is not None:
            try:
                on_arm(target)
            except Exception:
                ctx.log_exc("{}: on_arm failed".format(label))

    def rearm():
        """ゲームが送り口を作り直した後に呼ばれる（`rearm_watches`）。包み直した対象の並び。"""
        done = []
        for target in targets:
            if not can_resolve(target):
                continue
            armed = arm(target)
            if armed is None:
                break
            if armed is True:
                done.append(target)
                notify(target)
        return done

    _register_watch(ctx, label, rearm)
    if not unarmed:
        return []

    def loop():
        deadline = time.monotonic() + ALIAS_WATCH_SECONDS
        remaining = list(unarmed)
        while remaining and not ctx.superseded():
            for target in list(remaining):
                if not can_resolve(target):
                    continue
                armed = arm(target)
                if armed is None:
                    break
                if armed is LATER:
                    continue
                remaining.remove(target)
                if not armed:
                    # 作り直しの後に `rearm` が先に当てたか、別名だった
                    continue
                ctx.log("{}: late-armed on {} (the alias appeared)".format(
                    label, target))
                notify(target)
            if not remaining:
                return
            if time.monotonic() > deadline:
                ctx.log("{}: gave up waiting for {} ({}s)".format(
                    label, ", ".join(remaining), int(ALIAS_WATCH_SECONDS)),
                    level="WARN")
                return
            time.sleep(ALIAS_POLL_SECONDS)
        if remaining:
            # 注入し直されて降りた。見張りは新しい世代が立て直す。
            ctx.log("{}: stopped watching {} (superseded by a newer "
                    "injection)".format(label, ", ".join(remaining)))

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
        if result is not None:
            result = list(result)
            if len(result) != len(texts):
                ctx.log("{}: {} returned {} text(s) for {} (ignored)".format(
                    label, site, len(result), len(texts)), level="WARN")
                result = None
            elif result == texts:
                result = None
        # 当たった数を版ごとに控える（ゲームの文面が変わって当たらなくなったのを見つける。drift.py）
        try:
            from . import drift
            drift.note_rewrite(label, getattr(pass_local, "manager", None), result is not None)
        except Exception:
            pass
        return result

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
                    manager = args[0] if args and isinstance(args[0], str) else kwargs.get("manager_name")
                    pass_local.manager = manager if isinstance(manager, str) else None
                    try:
                        args, kwargs = safely(
                            site, lambda: rewrite_message_arg(site, args, kwargs),
                            (args, kwargs))
                    finally:
                        pass_local.manager = None
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


# --------------------------------------------------------------------------
# 送り口の窓口: 頼みの名前で振り分けて、送る messages と返却型を書き換える
# --------------------------------------------------------------------------
#: 窓口の置き場（`sys` の属性）。注入し直しをまたいで残る。
REQUESTS_ATTR = "_instantale_llm_requests"
_REQUESTS_LOCK = threading.Lock()
REQUESTS_LABEL = "llm requests"


class Request(object):
    """送り口の1回の頼み。この頼みに当たる登録の全部の `fn` と `after` に、同じものが1つ渡る。

    | 属性 | |
    |---|---|
    | `manager_name` | 頼みの名前（送り口の1つ目の引数）。文字列でなければ None |
    | `message` | 送る messages。`fn` が新しい並びを入れる（か返す）と、それが送られ、次の MOD はそれを見る |
    | `structure` | 返却型（`send_request` の3つ目の引数）。`fn` が型を入れると、それで送る。`ns` の頼みでは None で、入れると WARN を出して戻す |
    | `ns` | `send_request_with_no_structure` か |
    | `target` | 包んだ送り口（`MANAGER_SEND_TARGETS` の1つ） |
    | `provider` | 送り口の持ち主から採ったプロバイダ名（`provider_of`） |
    | `result` | `orig` の戻り値（`after` から読む） |
    | `memo` | 前と後で控えを受け渡す辞書（鍵は持ち主の名前にする） |
    | `app` | 走っている app（読んだ時に引く。読めなければ None） |
    """

    def __init__(self, manager_name, message, structure, ns, target, provider):
        self.manager_name = manager_name
        self.message = message
        self.structure = structure
        self.ns = bool(ns)
        self.target = target
        self.provider = provider
        self.result = None
        self.memo = {}

    @property
    def app(self):
        from . import ui
        try:
            return ui.find_app()
        except Exception:
            return None


def _requests_store():
    from . import state
    return state.process_store(REQUESTS_ATTR, {"hooks": {}, "seq": 0, "installed": None})


def send_targets(*modules, ns=None):
    """`MANAGER_SEND_TARGETS` のうち、部品と構造の有無で選んだ送り口のタプル（`on_request` の `targets` に渡す）。

        llm.send_targets("llm_manager_battle")              # 戦闘の2つ
        llm.send_targets("llm_manager", ns=False)           # llm_manager の send_request だけ

    `modules` は部品のモジュール名の末尾（`llm_manager` / `llm_manager_battle` / `llm_manager_world_generate` /
    `llm_manager_character_create`）。省けば全部。`ns` は None なら両方、True なら `send_request_with_no_structure`
    だけ、False なら `send_request` だけ。知らない部品の名前は `ValueError`。
    MOD が送り口の文字列を自分で書かずに済む（`tools/check_mods.py` は窓口が包む対象の文字列を MOD の中で探す）。
    """
    parts = {}
    for target, target_ns in MANAGER_SEND_TARGETS:
        part = target.partition(":")[0].rpartition(".")[2]
        parts.setdefault(part, []).append((target, target_ns))
    unknown = sorted(name for name in modules if name not in parts)
    if unknown:
        raise ValueError("llm.send_targets: unknown modules {} (known: {})".format(unknown, ", ".join(parts)))
    chosen = modules or tuple(parts)
    return tuple(target for target, target_ns in MANAGER_SEND_TARGETS
                 if target.partition(":")[0].rpartition(".")[2] in chosen and (ns is None or target_ns == bool(ns)))


def _manager_filter(managers):
    """`managers` の受け方を1つにする。None（全部）・呼べるもの・名前の集まり。"""
    if managers is None or callable(managers):
        return managers
    if isinstance(managers, str):
        return frozenset((managers,))
    names = frozenset(name for name in managers if isinstance(name, str))
    if not names:
        raise ValueError("llm.on_request: managers has no names: {!r}".format(managers))
    return names


def on_request(owner, ctx, fn=None, *, managers=None, targets=None, after=None, write=None):
    """送り口（`MANAGER_SEND_TARGETS`）を通る頼みの messages と返却型を、頼みの名前で振り分けて書き換える。

        def add_rule(req):                       # 送る前。req は Request
            return llm.edit_turn(req.message, lambda text: text.rstrip() + "\\n\\n" + RULE)
        llm.on_request(OWNER, ctx, add_rule, managers=("referee_attack",), write=write)

    | 引数 | |
    |---|---|
    | `fn(req)` | 送る前。新しい messages の並びを返す（か `req.message` に入れる）と、それを送る。返却型は `req.structure` に入れる。None を返せばそのまま |
    | `managers` | 当てる頼みの名前。None（全部）・名前1つ・名前の集まり・`fn(manager_name) -> bool` |
    | `targets` | 当てる送り口（`send_targets(...)` で選ぶ。`MANAGER_SEND_TARGETS` の対象の文字列の集まり）。None なら全部。前に自分で包んでいた送り口と同じにすると、届く範囲が変わらない |
    | `after(req)` | `orig` が返った後（投げたときは来ない）。`req.result` が答え。戻り値は変えられない（答えの中身を読む・その場で直すまで） |
    | `write` | MOD のログ関数。`fn` / `after` が投げたときの WARN を書く |

    呼ぶ順は、MOD が自分で送り口を包んでいたときの重なりと同じにする（gates の決まり。TECH.md §3.3.21）。
    `fn` は登録の新しい順で、前の MOD が書き換えた messages と型を次の MOD が見る。`after` は登録の古い順。

    | 決まり | 理由 |
    |---|---|
    | 包むのはローダの1か所。全 MOD の適用の後に一番外側に立てる（`install_requests`・`gates.defer_outermost`） | 9本の MOD が同じ送り口を包み、引数の読み方・頼みの名前での振り分けを写していた |
    | 1回の頼みに1度だけ当てる。同じスレッドで送り口の中からもう一度送り口が呼ばれたら素通し | 送り口は言語の包みと別名で入れ子に通ることがある。送り直す MOD（`talk_summary`）の2回目にも、外で書き換えた引数がそのまま渡る |
    | `fn` / `after` の例外は握って記録し、その MOD は何もしなかったことにして続ける（`fn` が入れた値も戻す） | 1本の不具合で頼みごと落とさない |
    | messages は list、返却型は型（`type`）だけを受ける。違う値は WARN を出して戻す | ゲームの送り口に渡ると、頼みの最中に落ちる |
    | 引数は来た側（位置かキーワード）に合わせて差し替える（`frames.replace_arg`） | 呼び手の渡し方を決め打ちしない |
    | 登録は持ち主ごとに1つ（同じ持ち主は差し替える）。登録した ctx が用済みなら呼ばない | 注入し直すたびに `apply()` が走る |
    | ローカル実行でも当てる | 前に自分で包んでいた MOD は、どれもローカルでも書き換えていた（クラウドだけに当てる MOD は `fn` の中で `is_local_runtime()` を見る） |

    `orig` を2回呼ぶ（足した形が失敗したら元の頼みで送り直す）・答えそのものを差し替える形は窓口に載らない。
    その形は自分で送り口を包む（`talk_summary` と `336_` の盗みの判定）。
    """
    if fn is None and after is None:
        raise ValueError("llm.on_request: needs fn or after=")
    for value in (fn, after):
        if value is not None and not callable(value):
            raise TypeError("llm.on_request needs callables, got {!r}".format(type(value)))
    known = dict(MANAGER_SEND_TARGETS)
    if targets is not None:
        targets = frozenset([targets] if isinstance(targets, str) else targets)
        unknown = sorted(target for target in targets if target not in known)
        if unknown or not targets:
            raise ValueError("llm.on_request: unknown targets {} (use MANAGER_SEND_TARGETS)".format(unknown))
    entry = {"ctx": ctx, "fn": fn, "after": after, "managers": _manager_filter(managers),
             "targets": targets, "write": write}
    from . import window_put
    with _REQUESTS_LOCK:
        store = _requests_store()
        store["seq"] += 1
        entry["seq"] = store["seq"]
        window_put(store["hooks"], owner, entry)
    install_requests(ctx, write)


def off_request(owner):
    """`on_request` の登録を下げる。下げたら True。"""
    with _REQUESTS_LOCK:
        return _requests_store()["hooks"].pop(str(owner), None) is not None


def _request_entries():
    from . import alive
    with _REQUESTS_LOCK:
        rows = sorted(_requests_store()["hooks"].items(), key=lambda row: row[1]["seq"])
    return [(owner, entry) for owner, entry in rows if alive(entry["ctx"])]


def request_owners():
    """`on_request` の生きている登録の持ち主（登録の古い順）。ログと検査用。"""
    return [owner for owner, _entry in _request_entries()]


def reset_requests():
    """`on_request` の登録と包みの印を全部忘れる（検査用）。"""
    setattr(sys, REQUESTS_ATTR, {"hooks": {}, "seq": 0, "installed": None})


def _wants(entry, target, manager_name):
    if entry["targets"] is not None and target not in entry["targets"]:
        return False
    managers = entry["managers"]
    if managers is None:
        return True
    if manager_name is None:
        return False
    if callable(managers):
        return bool(managers(manager_name))
    return manager_name in managers


def _request_oops(owner, entry, side, exc):
    from . import log_exc
    log_exc("{}: the {} of {} failed".format(REQUESTS_LABEL, side, owner))
    write = entry.get("write")
    if callable(write):
        try:
            write("WARN {}: the {} raised {}: {}".format(REQUESTS_LABEL, side, type(exc).__name__, exc))
        except Exception:
            pass


def send_through(target, ns, orig, args, kwargs, provider=None):
    """送り口の包みの中身。登録に当てて `orig` を呼び、`orig` の戻り値を返す（例外はそのまま流す）。

    包みの中と検査から呼ぶ。
    """
    from . import frames
    rows = _request_entries()
    if not rows:
        return orig(*args, **kwargs)
    manager_name = frames.arg(args, kwargs, "manager_name", 0)
    manager_name = manager_name if isinstance(manager_name, str) else None
    picked = []
    for owner, entry in rows:
        try:
            if _wants(entry, target, manager_name):
                picked.append((owner, entry))
        except Exception as exc:
            _request_oops(owner, entry, "managers filter", exc)
    if not picked:
        return orig(*args, **kwargs)
    message = frames.arg(args, kwargs, "message", 1)
    structure = None if ns else frames.arg(args, kwargs, "structure", 2)
    req = Request(manager_name, message, structure, ns, target,
                  provider if provider is not None else provider_of(orig))
    for owner, entry in reversed(picked):
        if entry["fn"] is None:
            continue
        kept = (req.message, req.structure)
        try:
            got = entry["fn"](req)
            if got is not None:
                if not isinstance(got, list):
                    raise TypeError("fn returned {!r} (a list of messages or None)".format(type(got)))
                req.message = got
            if req.message is not kept[0] and not isinstance(req.message, list):
                raise TypeError("req.message became {!r} (a list)".format(type(req.message)))
            if req.structure is not kept[1]:
                if ns:
                    raise TypeError("req.structure was set on {} (it sends no structure)".format(target))
                if not isinstance(req.structure, type):
                    raise TypeError("req.structure became {!r} (a type)".format(type(req.structure)))
        except Exception as exc:
            req.message, req.structure = kept
            _request_oops(owner, entry, "request hook", exc)
    if req.message is not message:
        args, kwargs, _ = frames.replace_arg(args, kwargs, "message", 1, req.message)
    if not ns and req.structure is not structure:
        args, kwargs, _ = frames.replace_arg(args, kwargs, "structure", 2, req.structure)
    req.result = orig(*args, **kwargs)
    for owner, entry in picked:
        if entry["after"] is None:
            continue
        try:
            entry["after"](req)
        except Exception as exc:
            _request_oops(owner, entry, "after hook", exc)
    return req.result


def install_requests(ctx, write=None):
    """`on_request` の包みを、送り口の全部（`MANAGER_SEND_TARGETS`）に立てる。1つの世代につき1回。立てたら True。

    MOD を適用している間は立てない。`boot()` が全 MOD の後にローダの ctx で一番外側に立てる
    （`gates.defer_outermost`。TECH.md §3.3.21）。送り口は後から生え、ゲームが作り直すので、
    当て方は `watch_aliases` に任せる（作り直しの後は登録の順に包み直すので、後に立てたこの包みが外側のまま）。
    """
    from . import alive, gates
    gates.track_install_attr(REQUESTS_ATTR)
    # 用済みの ctx（前の世代）からは立てない。立てると今の世代の包みの印を前の世代で上書きし、次の登録で二重に立つ
    if gates.deferring() or not alive(ctx):
        return False
    generation = getattr(ctx, "generation", None)
    with _REQUESTS_LOCK:
        store = _requests_store()
        done = store.get("installed")
        if isinstance(done, dict) and done.get("generation") == generation:
            return False
        store["installed"] = {"generation": generation}
    passing = threading.local()

    def hook(target, ns):
        @ctx.wrap(target, required=False, safe=True)
        def request_send(orig, *args, **kwargs):
            if getattr(passing, "active", False):
                return orig(*args, **kwargs)
            passing.active = True
            try:
                return send_through(target, ns, orig, args, kwargs)
            finally:
                passing.active = False

    ns_of = dict(MANAGER_SEND_TARGETS)
    watch_aliases(ctx, list(ns_of), lambda target: hook(target, ns_of[target]), label=REQUESTS_LABEL)
    if write:
        write("{}: the window was declared on {} sender(s)".format(REQUESTS_LABEL, len(ns_of)))
    return True
