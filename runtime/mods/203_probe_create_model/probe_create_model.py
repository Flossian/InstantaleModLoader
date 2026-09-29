# -*- coding: utf-8 -*-
"""pydantic モデルが組み立てられる、まさにその瞬間に空 `Literal[]` を捕らえる。

scripts.llm.llm_manager は `Literal = typing.Literal` と pydantic の
`create_model` の両方を import している。
つまり構造化出力のスキーマは、
呼び出し側がたまたま持っていたリストから実行時に組み立てられる。
そして

    AssertionError: literal "expected" cannot be empty, obj=typing.Literal[]

は、選択肢のない `Literal` を pydantic が拒否したものである。
元のトレースバックは `_literal_schema` の前に `_list_schema` を通っていたので、
問題のアノテーションは入れ子（`List[Literal[...]]` のような形）だと分かる。
下の走査が最上位だけを見ず `__args__` を再帰するのはこのためである。

create_model を包むと、検出器が障害地点そのものに置かれる:
pydantic 内部のトレースバックだけが残ってゲーム側の文脈が失われる代わりに、
モデル名・フィールド名・呼び出し元フレームが名指しされる。
成功した呼び出しも記録するので、
クラッシュしていないときの正常なスキーマの形も記録に残る。

読み取り専用: アノテーションは検査するだけで一切変更せず、例外はそのまま再送出する。

版3: 3点直した。
`scripts.llm.llm_manager` がまだ import されていない注入では早々に降りていて、
保留の見張りも立たなかったので、`required=False` の包みに任せる形にした（`202_` / `204_` と同じ）。
成功した呼び出しは、同じモデル名で同じ形が既に出ていれば書かない（形が変わったときは出す。
`Battle.enemies` の選択肢数の推移は GAME.md が引いている）。控えは `sys` に置き、上限を設けた。
包みは `safe=True` にし、受け取った引数をそのまま `orig` へ渡す。
"""

import sys
import typing

from instantale_modloader.frames import owner_of, repr_value

MODULE = "scripts.llm.llm_manager"
MAX_SCAN_DEPTH = 6

# 成功した呼び出しで既に書いた (モデル名, 形) の控え。1プロセスで共有する（版3）。
# 注入し直すとモジュール変数は消えるので `sys` に置く。
SEEN_ATTR = "_instantale_probe_create_model_seen"
# 控えの上限。越えたら成功行はもう書かない（空 Literal の行は上限に関係なく書く）。
MAX_SEEN_SHAPES = 400


def _find_empty_literals(annotation, path="", depth=0):
    """アノテーションの木をたどり、見つかった空 Literal を全て報告する。"""
    # 自己参照や深い入れ子で無限再帰しないための安全弁。
    if depth > MAX_SCAN_DEPTH:
        return []
    found = []
    try:
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
    except Exception:
        return found

    if origin is typing.Literal:
        if not args:
            found.append(path or "<root>")
        return found      # Literal の中身は型ではなく値なので、これ以上潜らない

    # List[...] / Optional[...] などの内側にある Literal を拾うため再帰する。
    for index, arg in enumerate(args):
        found += _find_empty_literals(arg, "{}[{}]".format(path or _name(annotation), index),
                                      depth + 1)
    return found


def _name(annotation) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


def _describe(annotation) -> str:
    """アノテーションを1行に要約する。Literal は選択肢数だけを示す。"""
    try:
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if origin is typing.Literal:
            return "Literal[{} option(s)]".format(len(args))
        if args:
            # 引数が多すぎるとログが読めなくなるので先頭6個で打ち切る。
            return "{}[{}]".format(_name(origin or annotation),
                                   ", ".join(_describe(a) for a in args[:6]))
        return _name(annotation)
    except Exception:
        return "<?>"


def _seen_shapes() -> set:
    """成功行を書いた (モデル名, 形) の控え。プロセスに1つ。"""
    seen = getattr(sys, SEEN_ATTR, None)
    if not isinstance(seen, set):
        seen = set()
        setattr(sys, SEEN_ATTR, seen)
    return seen


def apply(ctx):
    # 未 import でも降りない（版3）。
    # `scripts.llm.llm_manager` は最初の LLM リクエストまで import されない（TECH.md §3.4）。
    # 版2はここで早々に return していて、フックも保留の見張りも立たなかった。
    # `required=False` で登録しておけば、現れた時点でローダが当て直す。

    write = ctx.logger("probes.log")

    def inspect(args, kwargs):
        """呼び出しを調べて書く。本体の呼び出しとは切り離して、失敗しても素通しにする。"""
        model_name = args[0] if args else kwargs.get("model_name", kwargs.get("__model_name"))
        empty_fields = []
        summary = []

        for field, definition in kwargs.items():
            if not isinstance(field, str) or field.startswith("__") or field == "model_name":
                continue      # __base__ / __config__ などは設定であってフィールドではない
            # pydantic のフィールド定義は (型, デフォルト値) のタプルか型そのもの。
            annotation = definition[0] if isinstance(definition, tuple) and definition else definition
            summary.append("{}: {}".format(field, _describe(annotation)))
            for where in _find_empty_literals(annotation):
                empty_fields.append("{} ({})".format(field, where))

        if empty_fields:
            write("!!! EMPTY Literal in create_model({!r}): {}".format(
                model_name, ", ".join(empty_fields)))
            write("    fields: {}".format("; ".join(summary)))
            # このモデルを要求したゲーム側の関数を名指しする。
            # pydantic 内部のフレームを飛び越し、ゲームのファイルに当たるまで遡る。
            # 段数は数えない（`@ctx.wrap` の層が1段挟まる。frames.caller の説明）。
            # 版3で `inspect` と `safe=True` の層が2段増えたので、上限もその分広げた。
            for level in range(1, 14):
                try:
                    frame = sys._getframe(level)
                except ValueError:
                    break     # スタックの底に到達
                code = frame.f_code
                if "llm_manager" in code.co_filename or "instantale.py" in code.co_filename:
                    # 関数名だけでは足りない。
                    # `method_1` / `execute` のような名前は多くのマネージャが共有しているので、
                    # 同じコードオブジェクトを持つクラスを探して持ち主まで名指しする（`302_` で確立。
                    # ここへ反映した）。
                    write("    built by {}:{} in {}".format(
                        code.co_filename, frame.f_lineno,
                        owner_of(code) or code.co_name))
                    for key, value in list(frame.f_locals.items())[:20]:
                        write("      {:<22} = {}".format(key, repr_value(value)))
                    break
            return

        # 正常時の形も記録する。
        # 異常時に比較する基準が無いと判断できない。
        # 版3: 同じモデル名で同じ形は1プロセス1回だけ書く（形が変われば書く）。
        shape = "; ".join(summary) or "no fields"
        seen = _seen_shapes()
        key = (str(model_name), shape)
        if key in seen or len(seen) >= MAX_SEEN_SHAPES:
            return
        seen.add(key)
        write("create_model({!r}): {}".format(model_name, shape))

    @ctx.wrap("{}:create_model".format(MODULE), required=False, safe=True)
    def create_model(orig, *args, **kwargs):
        try:
            inspect(args, kwargs)
        except Exception:
            pass          # 計測の失敗で本体を止めない
        return orig(*args, **kwargs)

    ctx.log("create_model probe armed on {} (or deferred until it is imported)".format(MODULE))
