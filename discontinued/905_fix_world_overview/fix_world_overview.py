# -*- coding: utf-8 -*-
"""修正: 世界生成で入力した世界の概要が、書き直された別物に差し替わる。

##### 何が起きているか

「世界を生成する」画面で名前と概要を入れると、本体はまず LLM に世界を1つ書かせる。

```
save_world_json:generate_new_world(world_name, world_overview, free_facility_enabled)
  └ llm_manager_world_generate:create_world_overview_from_plot(world_name, world_overview)
       → World(world_name, overview, structure_description, structure)
```

入力した文章はこの1回目のプロンプトに
`【予め指定済みの設定】- 世界の概要: …` として渡るだけで、保存されるのは
**LLM が書き直した `World.overview`** の方。
それが `world_data["overview"]` になり、以後の生成が読むのは全部そちらになる。

```
world_data["overview"]        ← World.overview（LLM が書いた文章）
  ├ create_story              物語・噂・ストーリークエスト5本
  ├ create_settlement_detail  9エリアの施設・NPC
  └ settlement_quest_generator / random_quest_generator   依頼（遊んでいる間も）
```

つまり入力は要約を1回通ってから世界になる。
書いた設定が長いほど落ちる情報が増え、書いていないものが混ざる。

##### 直し方

`create_world_overview_from_plot` を包み、**返ってきた `World.overview` を
入力した文章そのものに差し替える**。

差し替えるのは `overview` の1項目だけ。
`structure_description`（地理の説明文）と `structure`（3層9エリアの構造）は
LLM が書いたものをそのまま使う。
あちらは「序盤3・中盤3・終盤3」というゲームシステム側の決まりから組む部分で、
入力した文章の代わりが無い。

差し替えは `create_story` より前に済むので、
物語も噂もストーリークエストも入力した文章から作られる。

##### なぜ生成を止めて自前で組まないのか

`World` には `structure` が要る。
入力を使うために LLM の呼び出しごと省くと、9エリアを自分で作ることになり、
「入力どおりの世界にする」ためのはずが**入力に無いもの**を大量に足す側に回る。
呼んでから1項目だけ戻すほうが、足すものが無い。

##### 効かない場面

概要を空のまま生成した場合、本体は
`create_world_overview_from_plot` ではなく `create_world_overview`（引数なし）を呼ぶ。
差し替える元の文章が無いので、この MOD は何もしない。
そちらにも包みを載せてあるが、**記録だけ**で応答には触らない
（何もしなかったことが `out\\world_overview.log` に残る）。

##### 二段目: 保存されたものを読み返す

差し替えた文章が本当に `world_data["overview"]` になったかを、
書き出しの実体（`save_world_json:write_obfuscated_json_file`）を包んで確かめる
（記録だけ。書き換えはしない）。
`world_data["overview"]` に届くまでの間に本体が何かを挟んでいれば、
`out\\world_overview.log` の `WARN` で分かる。

**`generate_new_world` には仕掛けない。**
あの関数は `WorldGenerateScreen.__init__(screen_manager, generate_new_world_callback)`
へ**関数のまま渡されて控えられる**（DOC.md §4）。
画面が組み上がった後にモジュールの属性を差し替えても、控えの方には届かない。
包みが間に合うかどうかが起動の速さ次第になるので、地点ごと変えてある（DOC.md §3）。
書き出しの実体なら、呼ぶ側が毎回モジュールを引くので当てた時点に関係なく通る
（世界の保存がここを通ることは実測済み。VERIFICATION.md §3.4）。

読むのは本体自身の復号器（`scripts.save_codec:read_json_with_obfuscation_fallback`）。
セーブの暗号化の仕様をこちらに写さないため（GAME.md §2.16）。
引けなければ、書き出す直前の `data` で照合して代わりにする。

`write_obfuscated_json_file` は遊んでいる間の保存でも通る。
照合するのは**差し替えた直後の `world_data.json` 1回だけ**で、以後は素通しする。
"""

import os
import sys

from instantale_modloader import frames

LOG_BASENAME = "world_overview.log"

# --------------------------------------------------------------------------
# 設定（既定値。`mod.json` の "settings" が同じ値を宣言している）
# --------------------------------------------------------------------------
# 本体が書いた世界観を、入力した文章の後ろに残すか。
# 既定は切＝入力した文章だけが世界の概要になる。
KEEP_GENERATED = False

# 残すときの区切り。
KEEP_SEPARATOR = "\n\n"

# --------------------------------------------------------------------------
# 対象
# --------------------------------------------------------------------------
PLOT_TARGET = "scripts.llm.llm_manager_world_generate:create_world_overview_from_plot"
NO_PLOT_TARGET = "scripts.llm.llm_manager_world_generate:create_world_overview"
SAVE_TARGET = "save_world_json:write_obfuscated_json_file"

SAVE_CODEC_MODULE = "scripts.save_codec"

# `World` の項目名と `create_world_overview_from_plot` の引数名。
OVERVIEW_FIELD = "overview"
WORLD_DATA_FIELD = "world_data"
PLOT_ARG = "world_overview"

# `write_obfuscated_json_file(file_path, data)` の引数名。
PATH_ARG = "file_path"
DATA_ARG = "data"

# 照合する書き出し先。これ以外（エリア・セーブ）は素通しする。
SAVE_FILE = "world_data.json"

SNIP = 60          # ログに出す断片の長さ


def _arg(args, kwargs, name, index):
    """引数を1つ拾う。キーワードを先に見て、無ければ位置で拾う。

    位置は版で動きうるので、名前で当たるならそちらを採る（GAME.md §2.24 と同じ理由）。
    """
    if name in kwargs:
        return kwargs[name]
    if len(args) > index:
        return args[index]
    return None


def _read_overview(response):
    """返ってきた `World` の `overview`。読めなければ `None`。

    pydantic のモデルで来るが、辞書で来ても読めるようにしておく
    （本体が構造の受け取り方を変えても、こちらが黙って空振りしないため）。
    """
    if isinstance(response, dict):
        value = response.get(OVERVIEW_FIELD)
    else:
        value = frames.attr(response, OVERVIEW_FIELD, None)
    return value if isinstance(value, str) else None


def _write_overview(response, text):
    """`overview` を差し替える。"""
    if isinstance(response, dict):
        response[OVERVIEW_FIELD] = text
    else:
        setattr(response, OVERVIEW_FIELD, text)


def _is_world_data(path):
    """書き出し先が世界の `world_data.json` か。

    `write_obfuscated_json_file` はエリアにも通常の保存にも使われるので、
    ここで絞らないと遊んでいる間じゅう照合が走る。
    """
    if not isinstance(path, (str, bytes)) and not hasattr(path, "__fspath__"):
        return False
    try:
        return os.path.basename(os.fspath(path)) == SAVE_FILE
    except Exception:
        return False


def _overview_of(data):
    """保存する辞書の `world_data["overview"]`。読めなければ `None`。"""
    if not isinstance(data, dict):
        return None
    world_data = data.get(WORLD_DATA_FIELD)
    if not isinstance(world_data, dict):
        return None
    value = world_data.get(OVERVIEW_FIELD)
    return value if isinstance(value, str) else None


def _saved_overview(path):
    """書き出された `world_data["overview"]` を読み返す。読めなければ `None`。"""
    codec = sys.modules.get(SAVE_CODEC_MODULE)
    read = getattr(codec, "read_json_with_obfuscation_fallback", None) if codec else None
    if not callable(read):
        return None
    return _overview_of(read(path))


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 差し替えた文章。二段目の読み返しが照合に使う。
    state = {"forced": None}

    ctx.log("world overview: keep_generated={!r}".format(bool(KEEP_GENERATED)))

    @ctx.wrap(PLOT_TARGET, safe=True)
    def create_world_overview_from_plot(orig, *args, **kwargs):
        plot = _arg(args, kwargs, PLOT_ARG, 1)
        # 前の世界の控えを持ち越さない。差し替えられた場合だけ入れ直す。
        state["forced"] = None
        response = orig(*args, **kwargs)

        # ここから先は `orig` が済んでいる。
        # 壊れても `safe=True` は最後の `orig` の結果を返す（世界生成をやり直さない）。
        if not isinstance(plot, str) or not plot.strip():
            # 概要を空のまま生成した経路。差し替える元が無い。
            write("no world overview in the arguments; leaving the generated one "
                  "as it is")
            return response

        generated = _read_overview(response)
        if generated is None:
            write("WARN the response has no readable {!r}; leaving it as it is "
                  "(type={})".format(OVERVIEW_FIELD, type(response).__name__))
            return response

        wanted = plot
        if KEEP_GENERATED and generated.strip():
            wanted = plot + KEEP_SEPARATOR + generated

        _write_overview(response, wanted)
        state["forced"] = wanted

        after = _read_overview(response)
        if after != wanted:
            # 代入がそのまま通らなかった（項目に検証が付いている等）。
            write("WARN could not replace {!r}: it is {} chars after the "
                  "assignment, wanted {}".format(
                      OVERVIEW_FIELD, len(after or ""), len(wanted)))
            state["forced"] = after
            return response

        write("overview replaced: generated {} chars -> yours {} chars{}".format(
            len(generated), len(wanted),
            " (the generated text is kept after yours)" if KEEP_GENERATED else ""))
        write("    yours:     {}".format(frames.short(plot, SNIP)))
        write("    generated: {}".format(frames.short(generated, SNIP)))
        ctx.log("world overview: replaced the generated overview ({} chars) with "
                "the one you wrote ({} chars)".format(len(generated), len(plot)))
        return response

    # -- 概要を入れずに生成した経路（記録だけ。応答には触らない） -------------
    @ctx.wrap(NO_PLOT_TARGET, required=False, safe=True)
    def create_world_overview(orig, *args, **kwargs):
        state["forced"] = None
        write("nothing was replaced for this world; no world overview was typed "
              "in, so the game is writing its own")
        return orig(*args, **kwargs)

    # -- 二段目: 書き出されたものを読み返す（記録だけ） -----------------------
    @ctx.wrap(SAVE_TARGET, required=False, safe=True)
    def write_obfuscated_json_file(orig, *args, **kwargs):
        result = orig(*args, **kwargs)

        # 差し替えていない／既に1回記録した後は、素通しする。
        if state["forced"] is None:
            return result
        path = _arg(args, kwargs, PATH_ARG, 0)
        if not _is_world_data(path):
            return result

        try:
            _record_saved(ctx, write, state, path,
                          _arg(args, kwargs, DATA_ARG, 1))
        except Exception:
            ctx.log_exc("world overview: reading the saved world back failed")
        return result


def _record_saved(ctx, write, state, path, data):
    """書き出された `world_data["overview"]` を照合して記録する。書き換えはしない。"""
    forced = state["forced"]
    # 1つの世界につき1回。この後は遊んでいる間の保存なので照合しない。
    state["forced"] = None

    saved = _saved_overview(path)
    if saved is not None:
        _compare(ctx, write, forced, saved, "the saved world_data['overview']")
        return

    # 復号器が引けない／読み返せない。書き出す直前の `data` で代用する。
    # 「ディスクから読み返した」とは別の言い回しにしてある。
    # `OK the saved world_data['overview']` は読み返しが通った時だけ出る印。
    handed = _overview_of(data)
    if handed is None:
        write("WARN could not read {!r} back".format(path))
        return
    write("could not read {!r} back; comparing what was handed to the writer"
          .format(path))
    _compare(ctx, write, forced, handed, "what was handed to the writer")


def _compare(ctx, write, forced, saved, what):
    """差し替えた文章と、書き出された文章を突き合わせて記録する。"""
    if saved == forced:
        write("OK {} is what you wrote ({} chars)".format(what, len(saved)))
        return

    write("WARN {} is not what was put in "
          "({} chars saved, {} chars replaced)".format(
              what, len(saved), len(forced)))
    write("    saved:    {}".format(frames.short(saved, SNIP)))
    write("    replaced: {}".format(frames.short(forced, SNIP)))
    ctx.log("world overview: the saved overview does not match the replacement; "
            "see out/{}".format(LOG_BASENAME), level="WARN")
