# -*- coding: utf-8 -*-
"""FreeInputStart.facility_move_to を *読んでいる箇所* を特定する。

FreeInputStart.method の実呼び出し（現時点で5回）はいずれも属性が無いまま
正常に戻った。つまり属性はそもそも設定されておらず、特定の分岐でのみ *読まれる*
のであって、その分岐は通らなかったということである。したがってメソッドを
ラップしても読み取り箇所は分からない。「宿に行く」「ギルドに行く」等の施設移動は
正常動作を確認済みなので、条件はそれより稀なもの。

__getattr__ トリップワイヤなら分かる。Python は通常のルックアップが失敗した
*後にのみ* __getattr__ を呼ぶので、それはまさにクラッシュの瞬間そのものである。
クラスに仕掛ければ、クラッシュが完全に記述されたイベントに変わる: 読んでいる
フレーム、その行番号、そのローカル変数 ― つまり分岐が何をしていて LLM が直前に
何を決めたのかが取れる。

挙動は保存される: トリップワイヤは常に標準メッセージで AttributeError を送出
するので、hasattr() は従来どおり False を返し、既存のコード経路は以前と全く
同じものを見る。このクラスは自前の __getattr__ を持っていない（リコンダンプで
確認済み）。将来それが変わった場合に備え、元のものへ委譲する経路も用意してある。
"""

import sys

from instantale_modloader.frames import repr_value

WATCH_CLASSES = ("FreeInputStart",)
HIGHLIGHT = "facility_move_to"

# Python 自身が投機的に探す属性名。記録してもノイズにしかならない。
DUNDER_NOISE_PREFIX = "__"


def apply(ctx):
    main = sys.modules.get("__main__")
    if main is None:
        ctx.log("__main__ not available; skipping", level="WARN")
        return

    log_path = ctx.out_path("probes.log")

    def write(text: str) -> None:
        try:
            import datetime
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("tripwire: write failed")

    for class_name in WATCH_CLASSES:
        cls = getattr(main, class_name, None)
        if cls is None:
            ctx.log("{} not found in __main__; skipping".format(class_name), level="WARN")
            continue

        # クラス属性の設定は patch.py を通さないので、二重に付かないようにするのは自前で行う。
        # 自前の印が付いていれば、前回注入で仕掛けたものなので二重にしない。
        previous = cls.__dict__.get("__getattr__")
        if getattr(previous, "__instantale_tripwire__", False):
            ctx.log("{}: tripwire already installed".format(class_name))
            continue

        # ループ変数を閉じ込める工場関数（class_name と previous を固定する）。
        def make_tripwire(cls_name, chained):
            def __getattr__(self, name):
                if not name.startswith(DUNDER_NOISE_PREFIX):
                    # 記録処理が例外を漏らすと挙動が変わってしまうので全体を包む。
                    try:
                        # sys._getframe(1) は「属性を読んだ側」のフレーム。
                        # これがまさに知りたかった呼び出し元である。
                        frame = sys._getframe(1)
                        code = frame.f_code
                        marker = " <<< THE BUG" if name == HIGHLIGHT else ""
                        write("MISSING ATTR {}.{}{} read from {}:{} in {}".format(
                            cls_name, name, marker,
                            code.co_filename, frame.f_lineno, code.co_name))
                        # 本命の属性のときだけ、分岐の状況が分かるよう詳細を残す。
                        if name == HIGHLIGHT:
                            items = list(frame.f_locals.items())[:25]
                            for key, value in items:
                                write("    {:<24} = {}".format(key, repr_value(value)))
                            try:
                                write("    instance attrs: {!r}".format(sorted(vars(self))))
                            except Exception:
                                pass
                    except Exception:
                        pass

                # 挙動は不変に保つ: 元の __getattr__ があれば委譲し、無ければ
                # Python 標準と同一メッセージの AttributeError を送出する。
                if chained is not None:
                    return chained(self, name)
                raise AttributeError("{!r} object has no attribute {!r}".format(
                    type(self).__name__, name))

            # 再注入時に自分の仕掛けを見分けるための印。
            __getattr__.__instantale_tripwire__ = True
            return __getattr__

        cls.__getattr__ = make_tripwire(class_name, previous)
        ctx.log("{}: __getattr__ tripwire installed (chained={})".format(
            class_name, previous is not None))
