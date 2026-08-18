# -*- coding: utf-8 -*-
"""修正: 新規作成したキャラクタが最初からレベル60で始まる。

##### main_025 で本体が取り込んだ（この mod は降ろした）

同じ行（`instantale.py:876`）が 1 を渡すようになった。
**この mod は何もしないことを実機で確認済み**（新規作成で `fixed 0`。
効く条件の2つ目が外れる）。
`mod.json` に `superseded` を入れてデバッグモード限定にしてある。

残っている用途は1つだけ。
**このバグで既にレベル60のまま保存されたセーブを直すこと**。
本体の修正は既存のセーブに効かない。
設定 `REPAIR_LOADED` を入れて読み込むと直る（既定は切のまま）。
以下は当時の記録。

##### 何が起きていたか

ゲーム本体の `InstantaleApp.start_game(world_name)` が、
新規開始でプレイヤーの `Character` を組む行でレベルに 60 を渡している。
経験値は渡していない（既定の 0）。

```
instantale.py:876   Character(..., experience_level=60, ...)
instantale.py:883   original_max_physical_integrity = get_max_physical_integrity(1) -> 10
instantale.py:884   max_physical_integrity          = get_max_physical_integrity(1) -> 10
instantale.py:885   physical_integrity              = get_max_physical_integrity(1) -> 10
```

同じ関数の中で、レベルが 60 と 1 の2通りに使われている。
体力上限はレベル1で計算され、`max_hp` はレベル60で計算される（`耐久 × 52`）。
876 行が本来 1 を渡すところだと読める。
経緯と根拠は VERIFICATION_LOG.md §2.36。

##### 直し方

`Character.__init__` を包み、**渡ってきた `experience_level` を 1 に差し替える**。
`max_hp` は `__init__` の中でレベルから計算されるので、
先に直せば HP もレベル1ぶん（`耐久 × 4.8`）になる。
渡された体力上限（レベル1の値）と揃う。

レベルを後から代入し直すのではなく引数を差し替えるのは、
`__init__` がレベルから決めるもの（HP のほか、
まだ特定していないものがあるかもしれない）を全部まとめて正しい値にするため。

##### 効く条件（この4つが揃ったときだけ）

| 条件 | なぜ要るか |
|---|---|
| `is_player` が真 | NPC のレベルは正しい。触る理由がない |
| `experience_level` が最小値より大きい | 本体が直ったら何もしない。この mod は自動で無効化される |
| `experience_point` が引数に無い | 新規作成の署名。読み込みは必ず保存された経験値を渡す（NPC の生成も両方渡している） |
| 渡された体力上限が `get_max_physical_integrity(最小レベル)` と等しい | 食い違いそのものを検出する。キャラの残りがレベル1で組まれている証拠。ここを見ないと、手でレベル60・経験値0に編集したセーブまで巻き戻す |

4つ目が要点。
「新規開始だから」ではなく「レベルだけが他と食い違っているから」直す作りなので、
`start_game` が続きからの読み込みにも使われていた場合でも巻き込まない（新規専用の関数だと確かめたわけではない。
分かっているのは `create_world` から新規で呼ばれる経路だけ）。

##### 既に保存されてしまったセーブ

読み込み時にも同じ食い違いは見えるが、既定では直さない。
遊んでいたキャラのレベルと HP が黙って下がるほうが事故になるため。
記録に警告を出すだけにしてあり、直したい人は設定 `REPAIR_LOADED` を入れる。

##### 二段目の保険

`start_game` を包み、抜けた直後にもう一度プレイヤーを見る。
`__init__` より後でレベルを代入し直している経路があった場合（未確認）、
そこで気付けるようにするため。
既定では直さず記録だけする。
見えていない経路を推測で書き換えないため。
"""

import sys

from instantale_modloader import frames, patch, ui

LOG_BASENAME = "new_character_level.log"

# 既に保存されたキャラも読み込み時に直すか（mod.json の設定で上書きされる）。
REPAIR_LOADED = False

FUNCTIONS_MODULE = "scripts.functions"

# 本体が宣言しているレベルの下限。
# 無ければこの値を使う。
FALLBACK_LEVEL_MIN = 1

# ログの上限。
# 1回の起動で何度も新規作成することはないので小さくてよいが、
# 二段目の保険（`start_game` の後の確認）が毎回1行出すので少し余裕を持たせる。
MAX_LINES = 200


def _bare(func):
    """包まれる前の素の関数を返す。剥がし方はローダの語彙（`patch.unwrap`）。

    他の mod や `214_probe_new_character` が同じ関数を包んでいることがある。
    包まれたものを掴むと、
    こちらの判定のたびに向こうのログが増える（`101_` が同じ理由で剥がしている）。
    """
    return patch.original_of(func)


def apply(ctx):
    state = {"lines": 0, "fixed": 0, "warned_positional": False}
    append = ctx.logger(LOG_BASENAME)

    def write(text):
        """行数に上限を付けた記録。上限だけがこの MOD の都合。

        書き方そのもの（時刻・追記・落としても止めない）はローダに任せる（`ctx.logger`）。
        上限が要るのは、この MOD がキャラクタ1体ごとに判定を記録するため。
        上限を外すと、大量の NPC を抱えたセーブでログが埋まる。
        """
        if state["lines"] >= MAX_LINES:
            return
        state["lines"] += 1
        append(text)

    functions = sys.modules.get(FUNCTIONS_MODULE)
    if functions is None:
        # 体力上限の照合ができない＝食い違いを検出できない。
        # 推測で書き換えるより何もしないほうがよい（この
        # mod の唯一の判断材料がこれ）。
        ctx.log("{} not loaded; skipping".format(FUNCTIONS_MODULE), level="WARN")
        return

    level_min = getattr(functions, "CHARACTER_LEVEL_MIN", FALLBACK_LEVEL_MIN)
    if not isinstance(level_min, int):
        level_min = FALLBACK_LEVEL_MIN

    max_stamina = _bare(getattr(functions, "get_max_physical_integrity", None))
    if max_stamina is None:
        ctx.log("get_max_physical_integrity not found; skipping", level="WARN")
        return
    try:
        stamina_at_min = max_stamina(level_min)
    except Exception:
        ctx.log_exc("get_max_physical_integrity({}) failed; skipping".format(level_min))
        return

    ctx.log("new character level: level_min={!r} stamina_at_min={!r}".format(
        level_min, stamina_at_min))

    def stamina_of(kwargs):
        """引数に載っている体力上限。3つのうち1つでも読めればよい。"""
        for name in ("max_physical_integrity", "original_max_physical_integrity",
                     "physical_integrity"):
            if name in kwargs:
                return name, kwargs[name]
        return None, None

    def looks_like_player(kwargs):
        if kwargs.get("is_player"):
            return True
        config = kwargs.get("config")
        return bool(isinstance(config, dict) and config.get("is_player"))

    def verdict(kwargs):
        """`(直すか, 理由)`。直さない理由も残す。効かないときに読む側が要る。"""
        if not looks_like_player(kwargs):
            return False, None                     # NPC。黙って通す
        level = kwargs.get("experience_level")
        if "experience_level" not in kwargs:
            # 位置引数で来ている可能性。
            # 並びを数えて当てるのは危ういので触らない。
            if not state["warned_positional"]:
                state["warned_positional"] = True
                return False, ("the player was built without an experience_level "
                               "keyword; not touching it (report this line)")
            return False, None
        if not isinstance(level, int) or level <= level_min:
            # 本体が直った、または既に最小値。
            # ここでこの mod は無効になる。
            return False, None
        name, stamina = stamina_of(kwargs)
        if name is None:
            return False, ("player level={} but no physical integrity in the "
                           "arguments; cannot tell level 1 from level {} "
                           "- not touching it".format(level, level))
        if stamina != stamina_at_min:
            # 体力上限がレベル相応なら食い違っていない（手で編集したセーブなど）。
            return False, ("player level={} with {}={} (level {} would be {}); "
                           "consistent enough - not touching it".format(
                               level, name, stamina, level_min, stamina_at_min))
        if "experience_point" not in kwargs:
            return True, ("new character: level {} with no experience_point and "
                          "{}={} (the level-{} value) - the level is the odd one "
                          "out".format(level, name, stamina, level_min))
        point = kwargs.get("experience_point")
        if point in (0, None, 0.0):
            # 既にこのバグで保存されたセーブの読み込み。
            # 既定では触らない。
            if REPAIR_LOADED:
                return True, ("loaded save carries the bug: level {} with "
                              "experience_point={} and {}={} - repairing "
                              "(REPAIR_LOADED is on)".format(
                                  level, point, name, stamina))
            return False, ("WARN loaded save carries the bug: level {} with "
                           "experience_point={} and {}={} (the level-{} value). "
                           "Left as it is; turn REPAIR_LOADED on to fix it"
                           .format(level, point, name, stamina, level_min))
        return False, None                         # 経験値がある＝普通の読み込み

    @ctx.wrap("scripts.characters:Character.__init__", safe=True)
    def character_init(orig, self, *args, **kwargs):
        # 判定を先に済ませ、引数を触るのは決まった後だけにする。
        # `safe=True` の落とし先は「元の関数を同じ引数で呼ぶ」なので、
        # 壊れた状態の引数を残したまま例外を投げると、その引数で本体が走る。
        fix, note = False, None
        try:
            fix, note = verdict(kwargs)
        except Exception:
            ctx.log_exc("new character level: verdict failed; passing through")
            return orig(self, *args, **kwargs)

        if not fix:
            if note:
                write(note)
                write("    caller: {}".format(frames.caller()))
            return orig(self, *args, **kwargs)

        before = kwargs.get("experience_level")
        kwargs["experience_level"] = level_min
        result = orig(self, *args, **kwargs)
        try:
            state["fixed"] += 1
            write("{}".format(note))
            write("    experience_level {} -> {}  name={} max_hp={} "
                  "max_physical_integrity={}".format(
                      before, level_min,
                      frames.repr_value(frames.attr(self, "name")),
                      frames.repr_value(frames.attr(self, "max_hp")),
                      frames.repr_value(frames.attr(self, "max_physical_integrity"))))
            write("    caller: {}".format(frames.caller()))
            after = frames.attr(self, "experience_level")
            if after != level_min:
                # `__init__` の中でレベルを決め直している。
                # 引数だけでは足りない。
                write("    WARN experience_level is {!r} after __init__ (expected "
                      "{!r}); the game decides it inside __init__ too".format(
                          after, level_min))
            ctx.log("new character level: {} -> {} for the new player".format(
                before, level_min))
        except Exception:
            ctx.log_exc("new character level: record failed")
        return result

    # -- 二段目: 開始処理を抜けた直後にもう一度見る（記録だけ） ---------------
    # `__init__` より後でレベルを代入し直す経路が在るかは未確認。
    # 在った場合に「直したのに 60 のまま」で終わらないよう、
    # ここで気付けるようにしておく。
    # 直さない。
    # 見えていない経路を推測で書き換えると、
    # 次に読む人がどちらが効いたのか分からなくなる。
    @ctx.wrap("__main__:InstantaleApp.start_game", required=False, safe=True)
    def start_game(orig, self, *args, **kwargs):
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                player = frames.attr(self, "player")
                if player in (None, frames.MISSING):
                    player = frames.attr(ui.find_app(), "player")
                level = frames.attr(player, "experience_level")
                point = frames.attr(player, "experience_point")
                stamina = frames.attr(player, "max_physical_integrity")
                if (isinstance(level, int) and level > level_min
                        and point in (0, None, 0.0) and stamina == stamina_at_min):
                    write("WARN after start_game the player is still level {} "
                          "(experience_point={}, max_physical_integrity={}). "
                          "Something sets the level after __init__ - report this "
                          "line".format(level, point, stamina))
                else:
                    write("after start_game: level={} experience_point={} "
                          "max_hp={} max_physical_integrity={} (fixed {} so far)"
                          .format(frames.repr_value(level),
                                  frames.repr_value(point),
                                  frames.repr_value(frames.attr(player, "max_hp")),
                                  frames.repr_value(stamina), state["fixed"]))
            except Exception:
                ctx.log_exc("new character level: post-start check failed")
