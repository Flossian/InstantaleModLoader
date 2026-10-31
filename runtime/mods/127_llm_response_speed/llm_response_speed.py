# -*- coding: utf-8 -*-
r"""ローカルLLMを速くする。サーバを起こす直前に起動引数を書き換える。

ゲームは `llama-server` をスロット4本の統合 KV で起こす（`--parallel` を渡さないと
llama.cpp が `auto` を選ぶ）。
だがこのゲームは並列でほとんど走らない。
実測ではリクエストの94%が逐次で、同時に走ったのは最大2本だった（VERIFICATION_LOG.md
§2.48）。
4本ぶんの KV を抱えたまま1本しか使っていない。

`--parallel 1` を渡すと統合が外れて専用の1本になり、KV が減って速くなる。
同じ窓 16384 での実測（VERIFICATION_LOG.md §2.48）:

    統合4スロット（素のゲーム）   KV 1220 MiB   生成 94 / 96 t/s
    --parallel 1                  KV  620 MiB   生成 133 / 127 t/s

この MOD の本体はこれ。
`--ctx-size` には既定では触らない。
窓を広げるのは任意の追加機能で、KV が増えるぶん VRAM の余裕が要る（GAME.md §2.12.2 と VERIFICATION_LOG.md
§2.48）。

## 1本にすると再計算が増える。ので同時に打ち消す

SWA 層は古いトークンを捨てるため、
途中から再開するにはその時点のスナップショット（コンテキストチェックポイント）が要る。
スロットを1本に絞ると、
種類の違うプロンプトが同じスロットを奪い合い、**復元に失敗してフル再計算に落ちる回が出る**（実測で4件に1件、
プロンプト処理が +20%）。

`--checkpoint-every-n-tokens 256` を足すとスナップショットが密になり、
この失敗が消える。
**VRAM は1バイトも増えない**（ホスト側に載る。実測で 9054 MiB のまま）。
そのため既定で付ける。
設定欄で明示されている場合だけ、そちらを尊重する。

## 旗は llama-server の版で選ぶ

ゲームの main_026 で同梱が b7054 から b10642 に替わり、`--checkpoint-every-n-tokens` が無くなった。
知らない旗を渡すと llama-server は `invalid argument` で即座に終わり、
ゲームは「同梱llama-serverが起動しませんでした」で落ちる（VERIFICATION.md §3.100）。

b10642 は RAM のプロンプトキャッシュ（`--cache-ram`、既定 8192 MiB）が追い出された状態を戻すので、
旗が無くても1本にした損は出ない（実プレイの戦闘の頼み60件で、統合と処理時間が同じ）。
近い旗 `--checkpoint-min-step 256` を足すと、再計算がさらに少し減る（同じ60件で 1.2%。RAM +88 MiB、VRAM は不変）。

そこで起こす実行ファイルの `--help` を見て、`CHECKPOINT_FLAGS` の先頭から知っている旗を1つ足す。
どれも知らない・`--help` が読めないときは足さない（足し損ねても遅くなるだけで、落ちはしない）。

## MTP を使えるモデルでは自動で使う

b10642 は MTP（複数トークン予測）を受け付ける。
使い方はモデルで2通りあり、この MOD がモデルを見て選ぶ。

- 本体の GGUF に MTP の層がある（Qwen3.8-27B など）: `--spec-type draft-mtp` だけで、本体の層を下書きに使う（`builtin_mtp`）。
  Qwen3.8-27B で生成が 1.6〜1.8 倍、VRAM は +約 1.1 GB。別に配られている下書き役を渡すより軽い（渡すと基準より遅くなった。VERIFICATION.md §3.100）
- 本体に無い: 別の下書き役を `--model-draft` で渡す（下の通り）

Gemma 4 26B A4B 系に Gemma の下書き役（`mtp-gemma-4-26B-A4B-it.gguf`、252 MB）を付けると、
戦闘の頼みで生成が約 1.5 倍、全体が約 2 割速い（ゲームと同じ温度 1.0。温度 0 では 1.7 倍・3 割）。Fenrir-X のような調整済みのモデルにも当たる（VERIFICATION.md §3.100）。
ゲームは `--model-draft` を渡さないので、ここで足す。

- 下書き役は、起動するモデルと同じフォルダの `mtp\` から探す（モデルの置き場に直に置くとゲームの一覧に並ぶ）
- 合うかは GGUF の先頭の情報で決める（`mtp_matches`）。合わない下書き役を渡すと llama-server が起きずゲームが落ちるので、
  読めない値が1つでもあれば付けない
- 付けて起きなかったら（VRAM に入らないなど）、`AIManager.set_ai_models` の包みが外して1回だけ起こし直す。
  その組はプロセスの間は付けない
- 設定欄に投機の旗（`--spec-type` など）があれば触らない

MTP を付けるときは `ngram-mod`（頼みの文や書いた分の繰り返しを写して下書きする）も重ねる（`MTP_NGRAM`）。
Fenrir-X・温度 1.0 で、MTP だけと比べて戦闘の頼みの生成が +5%、それ以外の頼みは +1%。VRAM は増えない。
Qwen3.8-27B では −2% / +1% で、効かないが害も無い（VERIFICATION.md §3.100）。
下書き役が無いモデルに `ngram-mod` だけを付けることはしない（測っていない）。
起きなかったときは MTP ごと外して起こし直す。

設定画面の「サーバーパラメータ」欄からでは窓を変えられない。
**ゲームは追加パラメータを繋ぐ前に `--ctx-size` を取り除く**（実測・GAME.md
§2.12.1）。
`--parallel` も `--cache-reuse` もサンプリングもそのまま渡るのに、
この1つだけが消える。

```
config.json の欄  --parallel 2 --ctx-size 32768 --cache-reuse 256
実際のコマンドライン
    ... --ctx-size 16384 ... --parallel 2 --cache-reuse 256
                  ^^^^^ ゲームの値が残り、こちらの指定は消えている
```

そこで欄ではなくプロセスを起こす関数に仕掛ける。
剥がす処理より後ろに立てば、剥がされようがない。

## どこに仕掛けるか

    sidecar_process:popen_sidecar(*args, **kwargs) -> subprocess.Popen

`subprocess.Popen` の薄い包みで、サイドカーはすべてここを通る。
`LlamaCppSidecar` の `start` ではなくこちらを選ぶ理由は3つ:

- `start` が引数をどう組み立てるかはコンパイル済みで読めない。
  ここには **実際に渡る argv がそのまま来る
- `llama_cpp_runtime_completion` はローカル実行のときしか import されないが、
  `sidecar_process` は常に載っている（クラウド実行のリコンにも出る）
- 画像生成の背景除去も同じ関数を通る（`request_remove_background:popen_sidecar` は
  from-import の別名）。
  だから argv の中身で llama-server を選り分ける**。
  別名はローダの alias_scan が張り替えるので、どちらの経路から来ても掴める

## 窓とスロットは必ず一緒に決める

`--parallel` を書くかどうかで、llama-server の KV の持ち方そのものが変わる（実測・GAME.md
§2.12.2）:

```
--ctx-size 16384                → kv_unified=true。4スロットが 16384 の
                                  プールを共有する。窓は 16384（ゲームの既定）
--ctx-size 16384 --parallel 2   → kv_unified=false。スロットごとに専用の
                                  プールを取り、窓は 16384÷2 = 8192
```

明示すると統合が外れ、SWA 側の確保量が減る（実測で3分の1）。
同じ窓でも KV が変わるので、設定は2つに分けてある:

    SLOTS = 0        --parallel を外す（統合に戻す）
    SLOTS = 1        --parallel 1。専用1本で KV 最小 ← 既定
    SLOTS >= 2       --parallel SLOTS。窓 × スロット数ぶんの KV を取る

    CTX_SIZE = 0     --ctx-size に触らない（ゲームの値のまま） ← 既定
    CTX_SIZE > 0     その値を窓にする。SLOTS>=2 なら合計＝窓×スロット数

**既定（CTX_SIZE=0 / SLOTS=1）は KV がどのモデルでも増えない。
窓を据え置いたまま持ち方だけを変えるので、素のゲームに対して確保量は同じか減る。
速度だけが変わるので、環境を選ばない。

`CTX_SIZE` を上げると KV は窓に比例して増える。
単価はモデルの構造で決まる**（SWA を持つ gemma-4-26B は 20 KiB/token、
持たない Qwen3.5-9B は 32 KiB/token）ので、
安全な値はマシンとモデルの組でしか決まらない。
`tools\llm_ctx_probe.bat` が実測して出す。
溢れてもエラーにはならず「異様に遅い」になるだけなので、勘で上げないこと。

## 窓を共有する起動ではスキーマ説明を縮める（版6）

統合 KV（`--parallel` 無し）は4スロットが窓を1つの領域で分け合うので、
2本が同時に走ると1件あたりは約半分になり、足りなければ `context-exceeded` で止まる。
`105_fix_schema_compact` はこれに備えて、本文に埋め込まれたスキーマ説明を縮めていた。
1件ごとに窓を丸ごと持つ起動（既定の `--parallel 1` など）では縮める理由が無く、
書き方を変えるだけで答えが寄る場面もあった（VERIFICATION_LOG.md §2.3）。
そこで縮めるかを、最後に渡した起動引数で決める（`needs_schema_compact`）:

    --parallel 無し                           縮める（統合 KV。SLOTS=0 か、127 が触らない場合）
    --parallel N で 窓 ÷ N ≥ ゲームの窓        縮めない（SLOTS=1 の既定・SLOTS>=2）
    --parallel N で 窓 ÷ N < ゲームの窓        縮める（設定欄に --parallel を書いて窓が割られた場合）

起動を見ていないうちに頼みが来たら（注入がサーバより後）、走っているサーバの引数を読んで決め、
読めなければ縮める。
縮め方の部品は `schema_compact.py`。
`105_` は取込済として伏せた。

## 割り切り

- **効くのは次にサーバが起きるときから。**
  既に走っているサーバの引数は変えられない。
  注入がサーバ起動より後なら、ゲーム側で LLM を開き直す（設定画面でモデルを選び直す）か、
  注入してからゲームを起動し直す。
  `apply()` の時点で走っているサーバがあれば、
  その持ち方を読んでログに出す（効いているかを目で確かめるため）
- **窓の拡張は詰まりの根治ではない。**
  `life_log` が膨らんでプロンプトが窓を超える問題は、
  プロンプト側で外科的に直す（MODS.md）。
  ここで `CTX_SIZE` を上げるのは時間稼ぎでしかないので、既定では触らない
- **`CTX_SIZE` を上げたら VRAM を見ること。**
  溢れても Windows は失敗せず共有メモリへ退避するので、
  症状は「エラー」ではなく「異様に遅い」になる。
  `tools\llm_ctx_probe.bat` が安全な上限を実測で出す
"""

import glob
import os
import re
import struct
import subprocess
import sys
import types

from . import schema_compact

# 1リクエストが使える窓（トークン）。
# 0 は `--ctx-size` に触らない。
# **`mod.json` の "default" と揃えること**（`tools/check_mods.py` が
# AST で突き合わせる。TECH.md §3.8.3）。
CTX_SIZE = 0

# 同時に持つスロット数。
# 0 は `--parallel` を外す（統合 KV に戻す）。
# 1 以上はスロットごとに専用の KV を確保する（docstring の表を参照）。
# `mod.json` の "default" と揃えること。
SLOTS = 1

# 何も書き換えず、実際の起動引数を記録するだけにする。
# `mod.json` の "default" と揃えること。
OBSERVE_ONLY = False

# MTP（複数トークン予測）を使えるモデルでは自動で使う（本体の層か、`mtp\` の下書き役）。
# `mod.json` の "default" と揃えること。
MTP = True

# MTP で1回に下書きするトークンの数（`--spec-draft-n-max`）。
# `mod.json` の "default" と揃えること。
MTP_DRAFT_MAX = 4

# MTP に ngram-mod を重ねる（`--spec-type draft-mtp,ngram-mod`）。MTP を付けるときだけ効く。
# `mod.json` の "default" と揃えること。
MTP_NGRAM = True

LOG_BASENAME = "llm_speed.log"

#: サイドカーの起動が必ず通る1点。
TARGET = "sidecar_process:popen_sidecar"

#: ローカル LLM のサーバを起こす口。起きなければここから例外が上がる（MTP を外して起こし直すのに使う）。
LAUNCH_TARGET = "__main__:AIManager.set_ai_models"

#: argv がローカルLLMのものだと判定する目印。
#: 実行ファイル名で見る。
EXE_MARK = "llama-server"

CTX_FLAG = "--ctx-size"
PARALLEL_FLAG = "--parallel"

#: チェックポイントを取る間隔（トークン）。
#: `--parallel 1` の副作用を打ち消す。
#: 詳しくは docstring の「1本にすると再計算が増える」と「旗は llama-server の版で選ぶ」。
CHECKPOINT_EVERY = 256

#: チェックポイントの旗。サーバが知っているもののうち先頭の1つだけを足す。
#: b7054 まで: 何トークンごとに取るか。b10642 から: 取る間隔の最小（既定 8192）。
CHECKPOINT_FLAGS = ("--checkpoint-every-n-tokens", "--checkpoint-min-step")

#: `--help` を待つ上限（秒）。メインスレッドで1回だけ走る。
HELP_TIMEOUT = 20

#: 実行ファイルごとの `--help` の控え。鍵は (パス, 大きさ, 更新時刻) で、ゲームの更新で差し替わったら読み直す。
_HELP_CACHE = {}

#: MTP の下書き役を探すフォルダ。起動するモデルと同じフォルダの中の `mtp\`
#: （モデルの置き場に直に置くと、ゲームのモデルの一覧に並ぶため）。
MTP_DIR_NAME = "mtp"

#: 下書き役を渡す旗と、MTP を選ぶ旗。
DRAFT_FLAG = "--model-draft"
SPEC_TYPE_FLAG = "--spec-type"
SPEC_MAX_FLAG = "--spec-draft-n-max"
MTP_TYPE = "draft-mtp"

#: MTP に重ねる投機。頼みの文や書いた分の繰り返しを写して下書きする（下書き役は要らない）。
NGRAM_TYPE = "ngram-mod"

#: 設定欄に投機の旗が1つでも書いてあれば、MTP には触らない（欄の指定を尊重する）。
SPEC_FLAGS = (DRAFT_FLAG, "-md", SPEC_TYPE_FLAG, "--spec-default", SPEC_MAX_FLAG)

#: 起動するモデルの旗。
MODEL_FLAGS = ("-m", "--model")

#: 本体と下書き役で同じでなければならない値（`<構造>.` の後ろの名前）。
#: 下書き役は本体の KV を共有するので、注意の形と RoPE が揃っていないと読み込めないか、当たらない。
MTP_SAME_KEYS = ("attention.head_count", "attention.key_length", "attention.value_length",
                 "attention.key_length_swa", "attention.value_length_swa",
                 "attention.sliding_window", "rope.freq_base", "rope.freq_base_swa",
                 "rope.dimension_count", "rope.dimension_count_swa")

#: GGUF の先頭の情報の控え。鍵は (パス, 大きさ, 更新時刻)。
_META_CACHE = {}

#: MTP の層の重みの名前に入る印（`blk.64.nextn.eh_proj.weight` など）と、その数を控える鍵（GGUF のキーと被らない名前）。
NEXTN_MARK = ".nextn."
NEXTN_TENSORS_KEY = "(127) nextn tensors"

#: 本体の MTP の層を使うときに、下書き役の代わりにログと控えへ書く名前。
BUILTIN_DRAFT = "本体の MTP の層"

#: MTP を付けて起こせなかった (モデル, 下書き役)。プロセスにつき1つの表（注入し直しても残す）。
_MTP_STATE = sys.modules.setdefault("instantale_127_mtp_state", types.ModuleType("instantale_127_mtp_state"))
_MTP_STATE.__dict__.setdefault("failed", set())
_MTP_STATE.__dict__.setdefault("last", None)

#: ゲームが必ず渡してくる `--ctx-size`。
#: `SLOTS>=2` で窓を保つ計算に使う。
GAME_CTX_SIZE = 16384

#: 頼みを送る地点。本文に埋め込まれたスキーマ説明を縮める。
CHAT_TARGET = "llama_cpp_runtime_completion:LlamaCppClient.chat"
#: スキーマ説明を縮めたときの記録。`105_` と同じ置き場で、`204_` の記録と並べて読む。
COMPACT_LOG = "prompt_bloat.log"
#: 最初の数回だけ、縮めた骨組みを全文ログに残す。
COMPACT_AUDIT_FIRST_N = 5

#: 縮めるかの判定。プロセスにつき1つ（注入し直しても残す）。
#: `need` は True / False / None（まだ起動を見ていない）、`why` はログに出す理由。
_COMPACT_STATE = sys.modules.setdefault("instantale_127_compact_state",
                                        types.ModuleType("instantale_127_compact_state"))
_COMPACT_STATE.__dict__.setdefault("need", None)
_COMPACT_STATE.__dict__.setdefault("why", "")


def is_llama_server(argv):
    """この argv が llama-server の起動か。実行ファイル名で判定する。

    背景除去のサイドカーも同じ関数を通るので、
    ここを緩めると無関係なプロセスに `--ctx-size` を足すことになる。
    """
    if not isinstance(argv, (list, tuple)):
        return False
    for item in argv:
        try:
            text = str(item).lower()
        except Exception:
            continue
        # 実行ファイルは argv[0] に来るはずだが、
        # 位置は決め打ちしない（ゲームが
        # python 経由で起こす形に変わっても拾えるように）。
        if EXE_MARK in os.path.basename(text) or EXE_MARK in text:
            return True
    return False


def server_exe(argv):
    """argv の中の llama-server の実行ファイルのパス。見つからなければ None。"""
    for item in argv:
        text = str(item)
        if EXE_MARK in os.path.basename(text).lower():
            return text
    return None


def server_help(exe):
    """`exe --help` の出力。読めなければ None。実行ファイルごとに1回だけ起こす。"""
    if not exe:
        return None
    try:
        stat = os.stat(exe)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(exe)), stat.st_size, stat.st_mtime)
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    text = None
    try:
        done = subprocess.run([exe, "--help"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, timeout=HELP_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = done.stdout.decode("utf-8", "replace")
    except Exception:
        text = None
    _HELP_CACHE[key] = text
    return text


def server_knows(argv, flag):
    """起こす llama-server が `flag` を知っているか。`--help` が読めなければ None。"""
    text = server_help(server_exe(argv))
    if text is None:
        return None
    # 長い名前の一部（`--checkpoint-every-n-tokens-x` など）に当たらないよう前後を区切る。
    return any(re.search(r"(?<![\w-]){}(?![\w-])".format(re.escape(name)), text)
               for name in _flag_names(flag))


#: llama-server の短い綴り。設定画面のサーバーパラメータ欄に書かれることがある（`-np 2` を残すと統合 KV に戻らなかった）。
FLAG_ALIASES = {"--parallel": ("-np",), "--ctx-size": ("-c",),
                "--checkpoint-every-n-tokens": ("-cpent",), "--checkpoint-min-step": ("-cms",)}


def _flag_names(flag):
    return (flag,) + FLAG_ALIASES.get(flag, ())


def set_flag(argv, flag, value):
    """`flag` の値を `value` にする。無ければ末尾に足す。戻り値は (新しい列, 前の値)。

    llama-server は同じ旗が2回出ると後勝ちなので末尾に足すだけでも効くが、
    それだとログを見たときに前後どちらが効いているのか読み取れない。
    既にあるものを書き換える形にして、コマンドラインが常に1つだけを持つようにする。
    """
    out = list(argv)
    text = str(value)
    alias_value = None
    for alias in FLAG_ALIASES.get(flag, ()):
        out, removed = drop_flag(out, alias)      # 短い綴りは長い綴りへまとめる（1つだけ持つ）
        alias_value = alias_value if removed is None else removed
    for i, item in enumerate(out):
        if str(item) != flag:
            continue
        if i + 1 < len(out):
            before = str(out[i + 1])
            out[i + 1] = text
            return out, before
        # 旗だけあって値が無い（壊れた並び）。
        # 値を足して形を整える。
        out.append(text)
        return out, None
    out.extend([flag, text])
    return out, alias_value


def has_flag(argv, flag):
    """`flag` が既に argv にあるか。設定欄からの指定を上書きしないための確認。"""
    names = _flag_names(flag)
    return any(str(item) in names for item in argv)


def flag_value(argv, flag):
    """`flag` に付いている値を数で返す。無ければ None。

    `--ctx-size` を引き伸ばすときの**土台**にする。
    定数（`GAME_CTX_SIZE`）を土台にすると、ゲームが別の窓を渡してきた場合
    （更新や、設定欄の server-params に手が入っている場合）に、
    こちらが書き戻す値が実際の窓と食い違う。
    高く外すと VRAM から溢れ、エラーは出ないまま桁で遅くなる。
    """
    items = [str(item) for item in argv]
    names = _flag_names(flag)
    for index, item in enumerate(items):
        if item in names and index + 1 < len(items):
            try:
                return int(items[index + 1])
            except (TypeError, ValueError):
                return None
        if any(item.startswith(name + "=") for name in names):
            try:
                return int(item.split("=", 1)[1])
            except (TypeError, ValueError):
                return None
    return None


def drop_flag(argv, flag):
    """`flag` とその値を取り除く。戻り値は (新しい列, 取り除いた値)。

    統合 KV にするには `--parallel` が無い必要がある。
    設定画面の「サーバーパラメータ」欄から
    `--parallel 1` が渡ってくる実例があるので（実測・GAME.md §2.12.1）、
    足すだけでなく消せないと `SLOTS=0` が効かない。
    """
    out = []
    removed = None
    skip = False
    names = _flag_names(flag)
    for i, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if str(item) in names:
            if i + 1 < len(argv):
                removed = str(argv[i + 1])
                skip = True
            continue
        out.append(item)
    return out, removed


# -------------------------------------------------------------- MTP の下書き役
_GGUF_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
                10: "<Q", 11: "<q", 12: "<d"}


def _gguf_string(fh):
    (size,) = struct.unpack("<Q", fh.read(8))
    return fh.read(size).decode("utf-8", "replace")


def _gguf_value(fh, kind):
    if kind in _GGUF_SCALAR:
        fmt = _GGUF_SCALAR[kind]
        return struct.unpack(fmt, fh.read(struct.calcsize(fmt)))[0]
    if kind == 8:
        return _gguf_string(fh)
    if kind == 9:
        (inner,) = struct.unpack("<I", fh.read(4))
        (count,) = struct.unpack("<Q", fh.read(8))
        if inner in _GGUF_SCALAR and count > 64:
            fh.seek(count * struct.calcsize(_GGUF_SCALAR[inner]), 1)    # 長い数の並びは読まない
            return ("array", count)
        if inner == 8 and count > 64:
            for _ in range(count):                                       # 語彙など。長さだけ見て飛ばす
                (size,) = struct.unpack("<Q", fh.read(8))
                fh.seek(size, 1)
            return ("array", count)
        return [_gguf_value(fh, inner) for _ in range(count)]
    raise ValueError("unknown GGUF value type {}".format(kind))


def gguf_meta(path):
    """GGUF の先頭の情報（キーと値）を読む。重みは読まない。読めなければ None。ファイルごとに1回だけ読む。"""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime)
    if key in _META_CACHE:
        return _META_CACHE[key]
    meta = None
    try:
        with open(path, "rb") as fh:
            if fh.read(4) == b"GGUF":
                (_version,) = struct.unpack("<I", fh.read(4))
                _tensors, count = struct.unpack("<QQ", fh.read(16))
                meta = {}
                for _ in range(count):
                    name = _gguf_string(fh)
                    (kind,) = struct.unpack("<I", fh.read(4))
                    meta[name] = _gguf_value(fh, kind)
                # テンソルの一覧は情報のすぐ後ろに並ぶ。名前だけ見て、MTP の層の重みの数を数える。
                nextn = 0
                for _ in range(_tensors):
                    if NEXTN_MARK in _gguf_string(fh):
                        nextn += 1
                    (dims,) = struct.unpack("<I", fh.read(4))
                    fh.seek(dims * 8 + 4 + 8, 1)                       # 形・型・置き場所
                meta[NEXTN_TENSORS_KEY] = nextn
    except Exception:
        meta = None
    _META_CACHE[key] = meta
    return meta


def mtp_matches(target, draft):
    """下書き役 `draft` が本体 `target` に付けられるか。戻り値は (付けられるか, 理由)。

    構造の名前が「本体の名前 + `-assistant`」で、出口の幅（`embedding_length_out`）が本体の幅と同じ、
    注意の形と RoPE（`MTP_SAME_KEYS`）と語彙が同じこと。どれかが読めなければ付けない。
    Gemma 4 26B A4B の下書き役は、同じ構造を調整したモデル（Fenrir-X など）にも当たる（VERIFICATION.md §3.100）。
    """
    arch = target.get("general.architecture")
    draft_arch = draft.get("general.architecture")
    if not arch or draft_arch != "{}-assistant".format(arch):
        return False, "構造が合わない（{} と {}）".format(arch, draft_arch)
    if not draft.get("{}.nextn_predict_layers".format(draft_arch)):
        return False, "下書き役に MTP の層が無い"
    width = target.get("{}.embedding_length".format(arch))
    if width is None or draft.get("{}.embedding_length_out".format(draft_arch)) != width:
        return False, "幅が合わない（{} と {}）".format(
            width, draft.get("{}.embedding_length_out".format(draft_arch)))
    for name in MTP_SAME_KEYS:
        mine, theirs = target.get("{}.{}".format(arch, name)), draft.get("{}.{}".format(draft_arch, name))
        if mine is None or theirs is None or mine != theirs:
            return False, "{} が合わない（{} と {}）".format(name, mine, theirs)
    for name in ("tokenizer.ggml.model", "tokenizer.ggml.tokens"):
        if target.get(name) is None or target.get(name) != draft.get(name):
            return False, "語彙が合わない"
    return True, "合う"


def builtin_mtp(argv):
    """起動するモデルの本体に MTP の層があるか。戻り値は (あるか, 理由)。

    層の数（`<構造>.nextn_predict_layers`）と、その重み（名前に `.nextn.` が入るテンソル）の両方が要る。
    Qwen3.8-27B は本体に1層を持ち、`--spec-type draft-mtp` だけで使える（VERIFICATION.md §3.100）。
    """
    model = model_path(argv)
    meta = gguf_meta(model) if model else None
    if not meta:
        return False, "モデルの情報が読めない"
    if not meta.get("{}.nextn_predict_layers".format(meta.get("general.architecture"))):
        return False, "本体に MTP の層が無い"
    if not meta.get(NEXTN_TENSORS_KEY):
        return False, "本体に MTP の層の重みが無い"
    return True, BUILTIN_DRAFT


def model_path(argv):
    """argv の中の、起動するモデルのパス。見つからなければ None。"""
    items = [str(item) for item in argv]
    for index, item in enumerate(items):
        if item in MODEL_FLAGS and index + 1 < len(items):
            return items[index + 1]
    return None


def find_drafter(argv):
    """起動するモデルに付ける MTP の下書き役を探す。戻り値は (パス, 理由)。見つからなければパスは None。

    探すのはモデルと同じフォルダの `mtp\\` だけ。合うものが複数あれば小さいほう（VRAM を食わない）。
    """
    model = model_path(argv)
    if not model:
        return None, "モデルのパスが読めない"
    target = gguf_meta(model)
    if not target:
        return None, "モデルの情報が読めない"
    folder = os.path.join(os.path.dirname(model), MTP_DIR_NAME)
    candidates = sorted(glob.glob(os.path.join(folder, "*.gguf")), key=lambda path: (os.path.getsize(path), path))
    if not candidates:
        return None, "{} に下書き役が無い".format(folder)
    reasons = []
    for path in candidates:
        draft = gguf_meta(path)
        if not draft:
            reasons.append("{}: 読めない".format(os.path.basename(path)))
            continue
        ok, why = mtp_matches(target, draft)
        if ok:
            return path, why
        reasons.append("{}: {}".format(os.path.basename(path), why))
    return None, "合う下書き役が無い（{}）".format("; ".join(reasons))


def plan_flags(ctx_size, slots, game_ctx=None):
    """設定から実際に渡す値を決める。戻り値は (--ctx-size の値, --parallel の値)。

    どちらも None は「その旗に触らない」。
    `--ctx-size` の None はゲームの値をそのまま通す、
    `--parallel` の None は旗を外す（統合 KV に戻す）。
    """
    slots = max(0, int(slots))
    parallel = slots if slots >= 1 else None
    if ctx_size <= 0:
        # `--ctx-size` は合計なので、2本以上でそのまま渡すと窓が黙ってスロット数ぶん縮む（実測・VERIFICATION_LOG.md
        # §2.48）。
        # ゲームの値を引き伸ばして窓を保つ。
        if slots >= 2:
            # 土台はいま渡されている値。読めなければ既知の既定値に落とす。
            base = GAME_CTX_SIZE if game_ctx is None else game_ctx
            return base * slots, parallel
        return None, parallel
    # スロットごとに専用の窓を持たせるときだけ、合計へ引き伸ばす。
    return ctx_size * max(1, slots), parallel


def rewrite_argv(argv, ctx_size=None, slots=None, knows=None, drafter=None):
    """llama-server の argv を書き換える。戻り値は (新しい列, 変更の説明のリスト)。

    **書き換えないときは受け取った列をそのまま返す**（同一オブジェクト）。
    呼び出し側はそれを見て「素通しした」と判断できる。
    `knows(argv, flag)` はサーバがその旗を知っているか（True / False / 読めなければ None）。
    省くと `server_knows`。True のときだけ任意の旗（チェックポイント・MTP）を足す。
    `drafter(argv)` は MTP の下書き役を探す（戻り値は (パス, 理由)）。省くと `find_drafter`。
    """
    knows = server_knows if knows is None else knows
    ctx_size = CTX_SIZE if ctx_size is None else ctx_size
    slots = SLOTS if slots is None else slots

    if not is_llama_server(argv):
        return argv, []
    # 設定で切った状態を、フックを外さずに作れるようにする。
    if OBSERVE_ONLY:
        return argv, []

    total, parallel = plan_flags(ctx_size, slots,
                                 flag_value(argv, CTX_FLAG))
    new_argv = argv
    changes = []
    if total is not None:
        new_argv, before = set_flag(new_argv, CTX_FLAG, total)
        changes.append((CTX_FLAG, before, str(total)))
    if parallel is None:
        # 統合 KV は `--parallel` が無いことが条件。
        # 既にあれば消す。
        new_argv, before = drop_flag(new_argv, PARALLEL_FLAG)
        if before is not None:
            changes.append((PARALLEL_FLAG, before, "(外した)"))
    else:
        new_argv, before = set_flag(new_argv, PARALLEL_FLAG, parallel)
        changes.append((PARALLEL_FLAG, before, str(parallel)))
        # スロットを絞ると SWA のチェックポイント復元に失敗してフル再計算が混ざる。
        # 密に取らせると消える（VRAM は増えない。ホスト側に載る）。
        # 知らない旗を渡すとサーバが起きずゲームが落ちるので、知っていると分かった旗を1つだけ足す。
        # 欄でどれかが指定済みなら、そちらを尊重して足さない。
        if CHECKPOINT_EVERY > 0 and not any(has_flag(argv, flag) for flag in CHECKPOINT_FLAGS):
            answers = []
            for flag in CHECKPOINT_FLAGS:
                known = knows(argv, flag)
                answers.append(known)
                if known:
                    new_argv, before = set_flag(new_argv, flag, CHECKPOINT_EVERY)
                    changes.append((flag, before, str(CHECKPOINT_EVERY)))
                    break
            else:
                changes.append(("/".join(CHECKPOINT_FLAGS), None,
                                "(足さない: --help が読めない)" if None in answers
                                else "(足さない: このサーバはどれも知らない)"))
    new_argv = _add_mtp(argv, new_argv, changes, knows, drafter or find_drafter)
    if not changes:
        return argv, []
    return new_argv, changes


def _add_mtp(argv, new_argv, changes, knows, drafter):
    """MTP を使えるモデルなら `--spec-type draft-mtp` を足す。足した列を返す。

    本体に MTP の層があればそれを使い（`builtin_mtp`）、無ければ合う下書き役を `--model-draft` で渡す（`find_drafter`）。
    `MTP_NGRAM` が立っていてサーバが ngram-mod を知っていれば、`--spec-type draft-mtp,ngram-mod` にする。
    足さないときも、理由を `changes` に1行残す（効いていないことをログで分かるように）。
    `_MTP_STATE.last` に足した組を控える。起動に失敗したら `set_ai_models` の包みが外して起こし直す。
    """
    _MTP_STATE.last = None
    if not MTP or MTP_DRAFT_MAX <= 0:
        return new_argv
    if any(has_flag(argv, flag) for flag in SPEC_FLAGS):
        changes.append(("MTP", None, "(付けない: 欄で投機の旗が指定済み)"))
        return new_argv
    if not knows(argv, MTP_TYPE):
        changes.append(("MTP", None, "(付けない: このサーバは MTP を知らない)"))
        return new_argv
    # 本体に MTP の層があればそれを使う。別の下書き役より軽い（Qwen3.8 で、別ファイルを渡すと基準より遅くなった）。
    builtin, why_builtin = builtin_mtp(argv)
    path = None
    if not builtin:
        if not knows(argv, DRAFT_FLAG):
            changes.append(("MTP", None, "(付けない: {}。このサーバは下書き役を受け付けない)".format(why_builtin)))
            return new_argv
        path, why = drafter(argv)
        if not path:
            changes.append(("MTP", None, "(付けない: {}。{})".format(why_builtin, why)))
            return new_argv
    pair = (model_path(argv), path or BUILTIN_DRAFT)
    if pair in _MTP_STATE.failed:
        changes.append(("MTP", None, "(付けない: この組はさっき起きなかった)"))
        return new_argv
    spec_types, note = MTP_TYPE, ""
    if MTP_NGRAM:
        # 知らない種類を渡すとサーバが起きないので、知っているときだけ重ねる。
        if knows(argv, NGRAM_TYPE):
            spec_types, note = "{},{}".format(MTP_TYPE, NGRAM_TYPE), "、{} を重ねる".format(NGRAM_TYPE)
        else:
            note = "、{} は重ねない: このサーバは知らない".format(NGRAM_TYPE)
    new_argv = (list(new_argv) + ([DRAFT_FLAG, path] if path else [])
                + [SPEC_TYPE_FLAG, spec_types, SPEC_MAX_FLAG, str(MTP_DRAFT_MAX)])
    changes.append(("MTP", None, "{}（下書き {}{}）".format(
        os.path.basename(path) if path else BUILTIN_DRAFT, MTP_DRAFT_MAX, note)))
    _MTP_STATE.last = pair
    return new_argv


def describe(argv):
    """ログ用に argv を1行へ潰す。パスは長いので実行ファイル名だけにする。"""
    parts = []
    for item in argv:
        text = str(item)
        if os.sep in text or (os.altsep and os.altsep in text):
            text = os.path.basename(text)
        parts.append(text)
    return " ".join(parts)


def running_flags():
    """既に走っている llama-server の (ctx, parallel) を読む。読めなければ None。

    注入がサーバ起動より後だと、この MOD は次の起動まで効かない。
    それを「効いていない」と誤読しないための材料をログに出す。
    """
    try:
        import psutil
    except Exception:
        return None

    def value_of(cmdline, flag):
        for i, item in enumerate(cmdline):
            if str(item) == flag and i + 1 < len(cmdline):
                return str(cmdline[i + 1])
        return None

    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            info = proc.info or {}
            if EXE_MARK not in (info.get("name") or "").lower():
                continue
            cmdline = info.get("cmdline") or []
            return value_of(cmdline, CTX_FLAG), value_of(cmdline, PARALLEL_FLAG)
    except Exception:
        return None
    return None


def needs_schema_compact(game_argv, final_argv):
    """スキーマ説明を縮めるか。戻り値は (縮めるか, 理由)。

    1件の頼みが窓を丸ごと使えると言い切れる起動のときだけ縮めない。
    `final_argv` は実際に渡す起動引数、`game_argv` はゲームが渡してきた起動引数（ゲームの窓を読む）。
    """
    parallel = flag_value(final_argv, PARALLEL_FLAG)
    if not parallel:
        return True, "{} 無し（統合 KV。2本が同時に走ると窓を分け合う）".format(PARALLEL_FLAG)
    total = flag_value(final_argv, CTX_FLAG)
    game = flag_value(game_argv, CTX_FLAG) or GAME_CTX_SIZE
    if total is None:
        return True, "{} {} だが {} が読めない".format(PARALLEL_FLAG, parallel, CTX_FLAG)
    per = total // parallel
    if per >= game:
        return False, "{} {} で1件ごとの窓 {}（ゲームの窓 {} 以上）".format(PARALLEL_FLAG, parallel, per, game)
    return True, "{} {} で1件ごとの窓 {}（ゲームの窓 {} より狭い）".format(PARALLEL_FLAG, parallel, per, game)


def _decide_compact(write, game_argv, final_argv):
    need, why = needs_schema_compact(game_argv, final_argv)
    changed = (need, why) != (_COMPACT_STATE.need, _COMPACT_STATE.why)
    _COMPACT_STATE.need, _COMPACT_STATE.why = need, why
    if changed:
        write("[COMPACT] {}: {}".format("スキーマ説明を縮める" if need else "スキーマ説明を縮めない", why))


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # main_026 は「開始する」を押した瞬間に llama のモジュールを読み込み、その場でサーバを起こす。
    # 保留の当て直し（5秒ごと）を待つと、すぐ押したときに包みの無いまま起きる（VERIFICATION.md §3.100）。
    # 包む先を先に読み込んでおけば注入の1回目で張れ、llama のモジュールは後から包んだ方を from-import する。
    try:
        import sidecar_process  # noqa: F401
    except Exception:
        pass

    @ctx.wrap(LAUNCH_TARGET, required=False, safe=True)
    def set_ai_models(orig, *args, **kwargs):
        """MTP を付けた llama-server が起きなかったら、MTP を外して1回だけ起こし直す。

        下書き役が VRAM に入らないなどで起きないと、ゲームは「同梱llama-serverが起動しませんでした」で落ちる。
        その組（モデル, 下書き役）はこのプロセスの間は付けない。
        """
        _MTP_STATE.last = None
        try:
            return orig(*args, **kwargs)
        except Exception as error:
            pair = _MTP_STATE.last
            if not pair:
                raise
            _MTP_STATE.failed.add(pair)
            _MTP_STATE.last = None
            message = "MTP（{}）を付けた llama-server が起きなかった（{}）。MTP を外して起こし直す".format(
                os.path.basename(pair[1]), error)
            write("[MTP] " + message)
            ctx.log("llm speed: " + message, level="WARN")
            return orig(*args, **kwargs)

    @ctx.wrap(TARGET, required=False, safe=True)
    def popen(orig, *args, **kwargs):
        # `popen_sidecar` は Popen の薄い包みなので、
        # argv は第1位置引数かキーワード `args` のどちらかで来る。
        # 受け取ったのと同じ渡し方で返す。
        from_kwargs = "args" in kwargs
        argv = kwargs.get("args") if from_kwargs else (args[0] if args else None)

        if not is_llama_server(argv):
            return orig(*args, **kwargs)

        write("[LAUNCH] {}".format(describe(argv)))

        new_argv, changes = rewrite_argv(argv)
        try:
            _decide_compact(write, argv, new_argv)
        except Exception:
            ctx.log_exc("llm speed: スキーマ説明を縮めるかの判定に失敗")
        if not changes:
            write("    観測のみ（CTX_SIZE={}）".format(CTX_SIZE))
            return orig(*args, **kwargs)

        for flag, before, after in changes:
            write("    {} {} -> {}".format(
                flag, before if before is not None else "(無し)", after))
        write("[REWRITE] {}".format(describe(new_argv)))

        if from_kwargs:
            kwargs = dict(kwargs, args=new_argv)
        else:
            args = (new_argv,) + tuple(args[1:])
        return orig(*args, **kwargs)

    def report():
        """既に走っているサーバの持ち方をログに出す（プロセスにつき1回）。"""
        current = running_flags()
        if current is None:
            return
        ctx_now, par_now = current
        want_ctx, want_par = plan_flags(CTX_SIZE, SLOTS)
        ok = ((want_ctx is None or ctx_now == str(want_ctx))
              and (str(want_par) if want_par else None) == par_now)
        shown = "{}={} {}={}".format(CTX_FLAG, ctx_now, PARALLEL_FLAG,
                                     par_now if par_now else "(無し)")
        if ok:
            ctx.log("llm context size: 稼働中のサーバは既に " + shown)
        else:
            ctx.log("llm context size: 稼働中のサーバは {}。次にサーバが起きる"
                    "ときから効く".format(shown), level="WARN")

    ctx.on_ready(report)

    if OBSERVE_ONLY:
        ctx.log("llm context size: 観測のみ（OBSERVE_ONLY）")
    else:
        total, parallel = plan_flags(CTX_SIZE, SLOTS)
        plan = []
        if total is not None:
            plan.append("{} {}".format(CTX_FLAG, total))
        plan.append("{} {}".format(PARALLEL_FLAG, parallel) if parallel
                    else "{} を外す".format(PARALLEL_FLAG))
        ctx.log("llm context size: installed ({}{})".format(
            " ".join(plan),
            "" if CTX_SIZE > 0 else "。窓はゲームの値のまま"))
        # 観測のみのときは頼みの本文も書き換えない。
        install_schema_compact(ctx, write)


def install_schema_compact(ctx, write):
    """窓を共有する起動のときだけ、頼みの本文のスキーマ説明を縮める（版6。docstring）。"""
    compact_write = ctx.logger(COMPACT_LOG)
    state = {"hits": 0}

    def need_now():
        if _COMPACT_STATE.need is None:
            # 起動を見ていない（注入がサーバより後）。走っているサーバの引数で決める。
            current = running_flags()
            if current is None:
                _COMPACT_STATE.need = True
                _COMPACT_STATE.why = "起動を見ておらず、走っているサーバの引数も読めない"
                write("[COMPACT] スキーマ説明を縮める: " + _COMPACT_STATE.why)
            else:
                ctx_now, par_now = current
                seen = []
                if ctx_now:
                    seen += [CTX_FLAG, ctx_now]
                if par_now:
                    seen += [PARALLEL_FLAG, par_now]
                _decide_compact(write, [], seen)
        return _COMPACT_STATE.need

    @ctx.wrap(CHAT_TARGET, required=False, safe=True)
    def chat(orig, self, model, messages, format=None, *args, **kwargs):
        # format が dict のとき（＝実体のあるスキーマが渡っているとき）だけ。
        # "json" のような汎用指定はフィールド単位の強制をしないので触らない。
        if isinstance(format, dict) and format and isinstance(messages, list) and need_now():
            new_messages, done = schema_compact.compact_messages(messages)
            if new_messages is not None:
                messages = new_messages
                for role, before, after, compact in done:
                    state["hits"] += 1
                    compact_write("[COMPACT] chat {} | スキーマ説明を圧縮 {} -> {} 文字 (削減 {})".format(
                        role, len(before), len(after), len(before) - len(after)))
                    if state["hits"] <= COMPACT_AUDIT_FIRST_N:
                        for line in compact.split("\n"):
                            compact_write("    {}".format(line))
        return orig(self, model, messages, format, *args, **kwargs)

    # 実経路はゲームが構造付きの頼みを出したときにしか通らず、起動直後に通る保証が無いので、
    # 注入の時点で見本を通して正しさを確かめておく。
    failure, sizes = schema_compact.verify()
    if failure:
        ctx.log("llm speed: VERIFY FAILED: schema compaction " + failure, level="ERROR")
    else:
        ctx.log("llm speed: verified schema compaction {} -> {} chars, idempotent, "
                "no-op without a schema or under {} chars".format(
                    sizes[0], sizes[1], schema_compact.MIN_SCHEMA_CHARS))
