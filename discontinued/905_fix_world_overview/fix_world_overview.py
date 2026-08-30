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

##### 二段目: 保存されたものを読み返す

差し替えた文章が本当に `world_data["overview"]` になったかを、
生成が終わってから保存済みの `world_data.json` を読んで確かめる（記録だけ。書き換えはしない）。
`world_data["overview"]` に届くまでの間に本体が何かを挟んでいれば、
`out\\world_overview.log` の `WARN` で分かる。

読むのは本体自身の復号器（`scripts.save_codec:read_json_with_obfuscation_fallback`）。
セーブの暗号化の仕様をこちらに写さないため（GAME.md §2.16）。
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
GENERATE_TARGET = "save_world_json:generate_new_world"

SAVE_CODEC_MODULE = "scripts.save_codec"
FUNCTIONS_MODULE = "scripts.functions"

# `World` の項目名と `generate_new_world` の引数名。
OVERVIEW_FIELD = "overview"
NAME_ARG = "world_name"
PLOT_ARG = "world_overview"

# 保存先（本体と同じ組み立て）。
SAVE_VENDOR = "Darmabeko"
SAVE_PRODUCT = "Instantale"
SAVE_WORLDS = "worlds"
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


def _saved_world_path(world_name):
    """保存された `world_data.json` の場所。無ければ `None`。

    フォルダ名は本体が `sanitize_path_name(world_name)` で作る。
    その関数が引けなければ入力した名前のままで探す。
    """
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    root = os.path.join(base, SAVE_VENDOR, SAVE_PRODUCT, SAVE_WORLDS)

    candidates = []
    functions = sys.modules.get(FUNCTIONS_MODULE)
    sanitize = getattr(functions, "sanitize_path_name", None) if functions else None
    if callable(sanitize):
        try:
            candidates.append(sanitize(world_name))
        except Exception:
            pass
    candidates.append(world_name)

    for name in candidates:
        if not isinstance(name, str) or not name:
            continue
        path = os.path.join(root, name, SAVE_FILE)
        if os.path.exists(path):
            return path
    return None


def _saved_overview(path):
    """保存された `world_data["overview"]`。読めなければ `None`。"""
    codec = sys.modules.get(SAVE_CODEC_MODULE)
    read = getattr(codec, "read_json_with_obfuscation_fallback", None) if codec else None
    if not callable(read):
        return None
    data = read(path)
    if not isinstance(data, dict):
        return None
    world_data = data.get("world_data")
    if not isinstance(world_data, dict):
        return None
    value = world_data.get(OVERVIEW_FIELD)
    return value if isinstance(value, str) else None


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 差し替えた文章。二段目の読み返しが照合に使う。
    state = {"forced": None}

    ctx.log("world overview: keep_generated={!r}".format(bool(KEEP_GENERATED)))

    @ctx.wrap(PLOT_TARGET, safe=True)
    def create_world_overview_from_plot(orig, *args, **kwargs):
        plot = _arg(args, kwargs, PLOT_ARG, 1)
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

    # -- 二段目: 保存されたものを読み返す（記録だけ） -------------------------
    @ctx.wrap(GENERATE_TARGET, required=False, safe=True)
    def generate_new_world(orig, *args, **kwargs):
        world_name = _arg(args, kwargs, NAME_ARG, 0)
        plot = _arg(args, kwargs, PLOT_ARG, 1)
        state["forced"] = None
        write("generating {!r} ({} chars of world overview typed in)".format(
            frames.short(world_name, SNIP), len(plot) if isinstance(plot, str) else 0))

        result = orig(*args, **kwargs)

        try:
            _record_saved(ctx, write, state, world_name)
        except Exception:
            ctx.log_exc("world overview: reading the saved world back failed")
        return result


def _record_saved(ctx, write, state, world_name):
    """保存された `world_data["overview"]` を照合して記録する。書き換えはしない。"""
    forced = state["forced"]
    if forced is None:
        write("nothing was replaced for this world; not reading it back")
        return

    path = _saved_world_path(world_name)
    if path is None:
        write("WARN could not find the saved world for {!r}; not reading it back"
              .format(frames.short(world_name, SNIP)))
        return

    saved = _saved_overview(path)
    if saved is None:
        write("WARN could not read {!r} back".format(path))
        return

    if saved == forced:
        write("OK the saved world_data['overview'] is what you wrote ({} chars)"
              .format(len(saved)))
        return

    write("WARN the saved world_data['overview'] is not what was put in "
          "({} chars saved, {} chars replaced)".format(len(saved), len(forced)))
    write("    saved:    {}".format(frames.short(saved, SNIP)))
    write("    replaced: {}".format(frames.short(forced, SNIP)))
    ctx.log("world overview: the saved overview does not match the replacement; "
            "see out/{}".format(LOG_BASENAME), level="WARN")
