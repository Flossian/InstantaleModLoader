# -*- coding: utf-8 -*-
"""pydantic モデルが組み立てられる、まさにその瞬間に空 `Literal[]` を捕らえる。

scripts.llm.llm_manager は `Literal = typing.Literal` と pydantic の
`create_model` の両方を import している。つまり構造化出力のスキーマは、呼び出し
側がたまたま持っていたリストから実行時に組み立てられる。そして

    AssertionError: literal "expected" cannot be empty, obj=typing.Literal[]

は、選択肢のない `Literal` を pydantic が拒否したものである。元のトレースバックは
`_literal_schema` の前に `_list_schema` を通っていたので、問題のアノテーションは
入れ子（`List[Literal[...]]` のような形）だと分かる。下の走査が最上位だけを見ず
`__args__` を再帰するのはこのためである。

create_model を包むと、検出器が障害地点そのものに置かれる: pydantic 内部の
トレースバックだけが残ってゲーム側の文脈が失われる代わりに、モデル名・フィー
ルド名・呼び出し元フレームが名指しされる。成功した呼び出しも記録するので、
クラッシュしていないときの正常なスキーマの形も記録に残る。

読み取り専用: アノテーションは検査するだけで一切変更せず、例外はそのまま再送出する。
"""

import datetime
import sys
import typing

from instantale_modloader.frames import owner_of, repr_value

MODULE = "scripts.llm.llm_manager"
MAX_SCAN_DEPTH = 6


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


def apply(ctx):
    module = sys.modules.get(MODULE)
    # 存在確認は既定値付き getattr で行う（TECH.md §8 の hasattr 禁止）。
    if module is None or getattr(module, "create_model", None) is None:
        ctx.log("{}.create_model not available; skipping".format(MODULE), level="WARN")
        return

    log_path = ctx.out_path("probes.log")

    def write(text: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("create_model probe: write failed")

    @ctx.wrap("{}:create_model".format(MODULE))
    def create_model(orig, model_name, *args, **field_definitions):
        empty_fields = []
        summary = []

        for field, definition in field_definitions.items():
            if field.startswith("__"):
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
            for level in range(1, 12):
                try:
                    frame = sys._getframe(level)
                except ValueError:
                    break     # スタックの底に到達
                code = frame.f_code
                if "llm_manager" in code.co_filename or "instantale.py" in code.co_filename:
                    # **関数名だけでは足りない。** `method_1` / `execute` のような
                    # 名前は多くのマネージャが共有しているので、同じコード
                    # オブジェクトを持つクラスを探して持ち主まで名指しする
                    # （`302_` で確立。ここへ反映した）。
                    write("    built by {}:{} in {}".format(
                        code.co_filename, frame.f_lineno,
                        owner_of(code) or code.co_name))
                    for key, value in list(frame.f_locals.items())[:20]:
                        write("      {:<22} = {}".format(key, repr_value(value)))
                    break
        else:
            # 正常時の形も記録する。異常時に比較する基準が無いと判断できない。
            write("create_model({!r}): {}".format(model_name, "; ".join(summary) or "no fields"))

        return orig(model_name, *args, **field_definitions)

    ctx.log("create_model probe installed on {}".format(MODULE))
