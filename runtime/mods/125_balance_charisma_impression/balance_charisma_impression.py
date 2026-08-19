# -*- coding: utf-8 -*-
"""調整: 魅力の高いキャラが、初対面の全員から同じ最上段で見られる。

##### 何が起きているか

NPC がプレイヤーに対して抱いている感情は、
セーブの `relationship.player.affinity_text` に2つ入っている。

```
"affinity_text": ["警戒心がある", "耐え難いほど魅力的に見えている"]
```

1つ目が好感度の段、2つ目が見た目の魅力の段で、
どちらも
`scripts.functions:document_emotion_scores_new(affinity, player_charisma)` が決める（`out/recon/targets.txt`）。
この文はそのまま保存され、以後その相手との会話プロンプトに毎回・全文載る（GAME.md
§2.25）。

問題は2つ目のほうにある。
**引数はプレイヤーの魅力だけ**なので、

- 誰が見ても同じ段になる（相手の好みが入らない）
- 初対面でも打ち解けた後でも同じ段になる（関係の深さが入らない）

そして段の上端が低い。
魅力に振ったキャラは世界の全員から最上段「耐え難いほど魅力的に見えている」で見られ続けることになり、
NPC の応答が無条件に友好的になる ＝ 交渉も説得も勝手に通る。

> 段の英訳がそれぞれ `Not really their type` / `Finds you irresistibly attractive`
> であることからも、ゲーム自身がこれを相手の好みの問題として書いているのが分かる。
> 一律なのは「好み」を持てる材料が引数に無いから。

##### どう変えるか

段そのものはゲームのものを使い、どの段になるかを2つの要素で決め直す。

| 要素 | 何を表すか | 効き方 |
|---|---|---|
| 好み（`TASTE_SPREAD`） | その相手の好みに合うか | 相手ごとに固定の増減（段） |
| 親しさ（`ACQUAINTANCE_STEPS`） | どこまで打ち解けたか | 好感度が浅いほど段を下げる |

```
段 = 素の段 + 好み - 親しさの不足
親しさの不足 = (max(0, ACQUAINTANCE_STEPS - 好感度の段差) + 1) // 2
```

`好感度の段差` は好感度の段が既定（初対面の「警戒心がある」）から何段上かで、
`ACQUAINTANCE_STEPS` の既定 4 は「多少の好意がある」に当たる。

| 好感度 | 段差 | 不足 |
|---|---|---|
| 嫌悪している | -2 | 3 |
| 警戒心がある（初対面） | 0 | 2 |
| 好きでも嫌いでもない | 1 | 2 |
| 嫌いではない | 2 | 1 |
| 興味がある | 3 | 1 |
| 多少の好意がある 以上 | 4+ | 0 |

最上段は好感度が `ACQUAINTANCE_STEPS` に届いている相手にしか出ない。
魅力に振ったキャラでも、初対面の相手からは「魅力を感じている」前後で、
そこから相手ごとに好みのぶんだけ散る。
打ち解けた相手にだけ最上段まで届く。

下限は「ひどく醜く思っている」の1つ上に置いてある（素の段がそれより下ならそのまま）。
好みや親しさで醜いことにはしない。
魅力に振ったキャラが相手次第で醜く見えるのは、
この MOD が直そうとしている不自然さと同じ種類のものになるため。

##### 段の一覧をゲームから読む（文言を持たない）

この MOD は「耐え難いほど魅力的に見えている」のような文字列を1つも持たない。
注入した後の最初の呼び出しで元の関数を総当たりし、段の並びをその場で覚える。

```
魅力  : 魅力を 0,1,2,… と変えて呼び、戻り値のどの位置が動くかを見る
好感度: 好感度を -200,…,200 と変えて呼び、同じことをする
```

こうしてある理由:

- 文言は言語設定で変わる（ja / en / zh-Hant の3つがゲームに入っている）。
  文字列を持つと日本語以外で何も起きない
- 段の数・閾値・戻り値の並びが版で変わっても、覚え直すだけで済む
- 段が1つも見つからない・並びが単調でない場合は何もしない（素通し）。
  推測した並びで書き換えるより、効かないほうがよい

覚えた並びと閾値は `out/charisma_impression.log` の先頭に出る。
ここが実機での確認箇所（exe の定数からは閾値が 6/8/11/14/17 と読めるが、
そちらは根拠にしていない。VERIFICATION.md §3.23）。

##### 相手が誰かは呼び出し元のフレームから拾う

`document_emotion_scores_new` の引数に NPC は居ない。
呼び出し元のフレームを遡り、`relationship` に `"player"` を持つ
`Character`（プレイヤー自身を除く）を探す。
見つからなければ好みの増減を 0 にする（親しさのぶんだけが効く）。
拾えなかった回は記録に1度だけ出る。

好みの増減は「世界名:NPC の id:プレイヤー名」の md5 から決める決定的な値で、
遊び直しても同じ相手なら同じになる。
乱数を使わないのは、
同じ相手の印象が会話のたびに揺れると「好み」ではなく「気まぐれ」になるため。
増減の分布は中央が厚い三角形（`TASTE_SPREAD=1` なら -1 / ±0 / +1 が 25 / 50 /
25%）。

##### 触らないもの

- 好感度の段（戻り値の1つ目）。
  会話の結果として動く値で、この MOD の話ではない
- `affinity` そのもの。
  書き換えるのは文にする段だけで、セーブの数値は動かさない
- 会話プロンプトの組み立て。
  段が保存された後の経路（`master_ai_facilitator_ from_conversation` など、
  会話系5関数を通らないもの）にも同じ文が載るので、
  文を作る1箇所だけを直せば全経路に届く
"""

import hashlib
import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "charisma_impression.log"

TARGET = "scripts.functions:document_emotion_scores_new"

# -- 設定（mod.json の "settings" から上書きされる） -------------------------

# 相手ごとの好みの幅（段）。
# 0 にすると誰から見ても同じ段になる（親しさだけ効く）。
TASTE_SPREAD = 1

# 魅力が満額で効くまでに要る好感度の段数（既定の「警戒心がある」から何段上か）。
# 0 にすると親しさを見ない（好みの差だけ）。
ACQUAINTANCE_STEPS = 4

# 最上段（「耐え難いほど魅力的に見えている」）を残すか。
ALLOW_TOP_RUNG = True

# -- 段の並びを覚えるための総当たり ------------------------------------------

# 魅力の側。
# 能力値は作成時 9〜16、セーブで見た上端は 30（GAME.md §2.17）。
# 上端の段がどこから始まるかを確かめたいので、その倍まで振る。
CHARISMA_PROBE = tuple(range(0, 61))

# 好感度の側。
# 実データで見えているのは 0 だけなので、広めに振って端を探す。
AFFINITY_PROBE = tuple(range(-200, 201))

# 好感度の側を総当たりするときに固定する魅力（普通のキャラの中央付近）。
CHARISMA_FIXED = 12

#: 総当たりで最低限引けていてほしい点の数。
#: ゲームは定義域の外で例外を出すので、全点は引けない前提で数える。
MIN_PROBE_ROWS = 10

# 呼び出し元をどこまで遡って NPC を探すか。
FRAME_DEPTH_MAX = 24

# 呼び出し元のフレームで先に見る変数名（会話系5関数の並びより。GAME.md §2.24）。
PREFERRED_LOCALS = ("character_instance", "npc", "npc_instance",
                    "character", "target_character", "self")

# 記録の上限。
# 1回の会話で数回しか呼ばれないが、長く遊んでも埋まらないようにする。
MAX_LINES = 400


def _taste_of(key, spread):
    """相手ごとに固定の増減（段）。中央が厚い三角形の分布。

    `hash()` を使わないのは、Python の文字列ハッシュがプロセスごとに変わるため。
    同じ相手の印象が起動のたびに変わってしまう。
    """
    if spread <= 0 or not key:
        return 0
    weights = [spread + 1 - abs(step) for step in range(-spread, spread + 1)]
    digest = hashlib.md5(key.encode("utf-8", "replace")).hexdigest()
    pick = int(digest[:8], 16) % sum(weights)
    for step, weight in zip(range(-spread, spread + 1), weights):
        if pick < weight:
            return step
        pick -= weight
    return 0


def _as_tuple(result):
    """戻り値を比べられる形にする。リスト・タプル以外は None（＝触らない）。"""
    if isinstance(result, (list, tuple)):
        return tuple(result)
    return None


def _moving_positions(rows):
    """総当たりの間に中身が動いた位置。長さが違う回は「無い」を値として数える。"""
    width = max(len(row) for row in rows)
    moving = []
    for index in range(width):
        seen = {row[index] if index < len(row) else None for row in rows}
        if len(seen) > 1:
            moving.append(index)
    return moving


def _ladder_of(rows, index):
    """並びの順に段を拾う。同じ段が離れて2度出たら段ではない（None を返す）。"""
    ladder = []
    for row in rows:
        value = row[index] if index < len(row) else None
        if not ladder or ladder[-1] != value:
            ladder.append(value)
    if len(set(ladder)) != len(ladder):
        return None
    return ladder


def _edges_of(values, rows, index):
    """`値 -> 段` の並びが変わった境目。閾値を記録に残すためだけのもの。"""
    edges, previous = [], None
    for value, row in zip(values, rows):
        rung = row[index] if index < len(row) else None
        if previous is not None and rung != previous:
            edges.append("{}から{!r}".format(value, rung))
        previous = rung
    return edges


def apply(ctx):
    append = ctx.logger(LOG_BASENAME)
    state = {"lines": 0, "ladders": False, "taste": {},
             "no_npc_warned": False}

    def write(text):
        if state["lines"] >= MAX_LINES:
            return
        state["lines"] += 1
        append(text)

    # ------------------------------------------------------------ 段を覚える
    def learn(orig):
        """元の関数を総当たりして、戻り値のどの位置がどの段の並びかを決める。

        `orig` は（他の MOD が同じ関数を包んでいれば）その層を含んだもの。
        素の関数まで剥がさないのは、実際に効いている振る舞いの段を覚えたいため。
        剥がすと、他の MOD の書き換えを知らないまま段を数えることになる。
        """
        def probe(affinity, charisma):
            """1点だけ引く。**ゲームが受け付けない組み合わせは飛ばす。**

            素の `document_emotion_scores_new` は定義域の外で例外を出す
            （実測: `charisma=0` で `ValueError: max() arg is an empty sequence`。
            `out/modloader.log` に 08-12 以降ずっと出ていた）。
            1点でも投げたら総当たり全体を諦める作りだったため、
            **この MOD は一度も段を覚えられていなかった**
            （`out/charisma_impression.log` が 0 バイトのままだった）。
            段の並びは動く位置さえ分かればよく、全点は要らない。
            """
            try:
                return _as_tuple(orig(affinity, charisma))
            except Exception:
                return None

        charm_rows, affinity_rows = [], []
        for value in CHARISMA_PROBE:
            row = probe(0, value)
            if row is not None:
                charm_rows.append(row)
        for value in AFFINITY_PROBE:
            row = probe(value, CHARISMA_FIXED)
            if row is not None:
                affinity_rows.append(row)
        # 段の並びを見分けるには、動く位置が分かるだけの点数が要る。
        if len(charm_rows) < MIN_PROBE_ROWS or len(affinity_rows) < MIN_PROBE_ROWS:
            return None, "総当たりで引けた点が少なすぎる（魅力 {} 点 / 好感度 {} 点）".format(
                len(charm_rows), len(affinity_rows))

        charm_moving = _moving_positions(charm_rows)
        affinity_moving = _moving_positions(affinity_rows)
        if len(charm_moving) != 1 or len(affinity_moving) != 1:
            return None, "魅力で動く位置 {} / 好感度で動く位置 {}（1つずつのはず）".format(
                charm_moving, affinity_moving)
        charm_index, affinity_index = charm_moving[0], affinity_moving[0]
        if charm_index == affinity_index:
            return None, "魅力と好感度が同じ位置 {} を動かしている".format(charm_index)

        charm = _ladder_of(charm_rows, charm_index)
        affinity = _ladder_of(affinity_rows, affinity_index)
        if not charm or len(charm) < 2 or not affinity:
            return None, "段の並びが単調でない（魅力 {} / 好感度 {}）".format(
                charm, affinity)

        # 既定（初対面）の好感度の段。
        # ここを 0 として段差を数える。
        neutral_row = _as_tuple(orig(0, CHARISMA_FIXED))
        neutral_text = neutral_row[affinity_index] \
            if neutral_row is not None and affinity_index < len(neutral_row) else None
        if neutral_text not in affinity:
            return None, "好感度 0 の段 {!r} が並びに無い".format(neutral_text)

        return {"charm_index": charm_index, "charm": charm,
                "affinity_index": affinity_index, "affinity": affinity,
                "neutral": affinity.index(neutral_text),
                "charm_edges": _edges_of(CHARISMA_PROBE, charm_rows, charm_index),
                "affinity_edges": _edges_of(AFFINITY_PROBE, affinity_rows,
                                            affinity_index)}, None

    def report(ladders):
        write("=" * 72)
        write("段の並びをゲームから読んだ（{}）".format(TARGET))
        write("  魅力   位置 {}: {}".format(
            ladders["charm_index"],
            " < ".join(repr(rung) for rung in ladders["charm"])))
        write("  魅力の閾値: " + (" / ".join(ladders["charm_edges"]) or "(段が動かない)"))
        write("  好感度 位置 {}: {}".format(
            ladders["affinity_index"],
            " < ".join(repr(rung) for rung in ladders["affinity"])))
        write("  好感度の閾値: "
              + (" / ".join(ladders["affinity_edges"]) or "(段が動かない)"))
        write("  好感度 0（初対面）の段: {!r}（{}段目）".format(
            ladders["affinity"][ladders["neutral"]], ladders["neutral"]))
        write("  設定 TASTE_SPREAD={} ACQUAINTANCE_STEPS={} ALLOW_TOP_RUNG={}".format(
            TASTE_SPREAD, ACQUAINTANCE_STEPS, ALLOW_TOP_RUNG))
        write("=" * 72)

    # -------------------------------------------------------- 相手を見つける
    def player_of():
        app = ui.find_app()
        if app is None:
            return None
        player = frames.attr(app, "player", None)
        return player if player not in (None, frames.MISSING) else None

    def looks_like_npc(value, player):
        if value is None or value is player:
            return None
        relationship = frames.attr(value, "relationship", None)
        if not isinstance(relationship, dict) or "player" not in relationship:
            return None
        if frames.attr(value, "is_player", None) is True:
            return None
        config = frames.attr(value, "config", None)
        if isinstance(config, dict) and config.get("is_player"):
            return None
        return value

    def npc_in_frames(player):
        """呼び出し元を遡って NPC を探す。段数は数えない（TECH.md §5.2）。"""
        index = 1
        while index < FRAME_DEPTH_MAX:
            try:
                frame = sys._getframe(index)
            except Exception:
                break
            index += 1
            code = frame.f_code
            if frames.is_ours(code.co_filename):
                continue                      # ローダと mod 自身のフレーム
            if code.co_name == "<module>":
                # モジュール直下のフレームの中身はグローバル。
                # 呼び出しの文脈ではないので、たまたま置かれている
                # Character を掴まない。
                continue
            try:
                # `dict()` で写す。
                # Python 3.13 以降の `f_locals` は
                # dict ではなく書き戻し用のプロキシで、
                # `isinstance(..., dict)` で弾くと関数のフレームが1つも読めなくなる（ゲームは 3.10 だが、
                # オフラインの試験は手元の Python で走る）。
                local_vars = dict(frame.f_locals)
            except Exception:
                continue
            for name in PREFERRED_LOCALS:
                found = looks_like_npc(local_vars.get(name), player)
                if found is not None:
                    return found
            for value in list(local_vars.values()):
                found = looks_like_npc(value, player)
                if found is not None:
                    return found
        return None

    def key_of(npc, player):
        """「世界名:NPC の id:プレイヤー名」。id は `world.characters` の鍵が本命。"""
        app = ui.find_app()
        world = frames.attr(app, "world", None) if app is not None else None
        world_name = frames.text_of(world, "name") or "?"
        npc_id = None
        characters = frames.attr(world, "characters", None)
        if isinstance(characters, dict):
            for key, value in characters.items():
                if value is npc:
                    npc_id = str(key)
                    break
        if npc_id is None:
            value = frames.attr(npc, "id", None)
            if value not in (None, frames.MISSING):
                npc_id = str(value)
        if npc_id is None:
            npc_id = frames.text_of(npc, "name")
        if npc_id is None:
            return None
        return "{}:{}:{}".format(world_name, npc_id,
                                 frames.text_of(player, "name") or "?")

    # ------------------------------------------------------------ 段を決める
    def decide(ladders, row, taste):
        """`(新しい段, 覚え書き)`。触らないときは新しい段に None を返す。"""
        charm_index = ladders["charm_index"]
        charm = ladders["charm"]
        current = row[charm_index] if charm_index < len(row) else None
        if current not in charm:
            return None, "魅力の段 {!r} が並びに無い".format(current)
        base = charm.index(current)

        affinity_index = ladders["affinity_index"]
        affinity_text = row[affinity_index] if affinity_index < len(row) else None
        if affinity_text in ladders["affinity"]:
            gap = ladders["affinity"].index(affinity_text) - ladders["neutral"]
        else:
            gap = 0                            # 読めない段は初対面と同じに扱う
        short = (max(0, ACQUAINTANCE_STEPS - gap) + 1) // 2

        top = len(charm) - 1
        floor = min(base, 1)                   # 好み・親しさで醜いことにはしない
        picked = max(floor, min(top, base + taste - short))
        if picked == top and (not ALLOW_TOP_RUNG or gap < ACQUAINTANCE_STEPS):
            picked = max(floor, top - 1)
        note = "好感度={!r}(段差{:+d}) 好み={:+d} 親しさ={:+d} {}段目→{}段目".format(
            affinity_text, gap, taste, -short, base, picked)
        if picked == base:
            return None, note
        return picked, note

    def rebuild(row, index, value, original):
        """位置 `index` を差し替えた戻り値。元がリストならリストで返す。"""
        items = list(row)
        if value is None:
            if index < len(items):
                # 段が「無い」は、その位置ごと落とす（ゲームが短い列を返す形）。
                items = items[:index] + items[index + 1:]
        elif index < len(items):
            items[index] = value
        elif index == len(items):
            items.append(value)
        else:
            return original                    # 埋められない穴は作らない
        return items if isinstance(original, list) else tuple(items)

    # ------------------------------------------------------------------ 本体
    @ctx.wrap(TARGET, safe=True)
    def emotion_scores(orig, *args, **kwargs):
        result = orig(*args, **kwargs)

        # ここから先は**必ず飲む**。
        # `safe=True` のとき渡される `orig` は `_guard` の記録係で、
        # 呼ぶたびに「素の答え」を覚え直す。`learn()` は段を覚えるために
        # その `orig` を合成引数で何度も呼ぶので、覚えられているのは
        # **探りの答え**になっている。
        # この後ろで投げると `_guard` は「orig は既に走った」と見なして
        # その探りの答えをゲームへ返す。それが `affinity_text` に書かれ、
        # 以後その NPC の会話プロンプトに乗り、セーブされる。
        # 落とす先は自分で持つ。
        try:

            if state["ladders"] is False:
                # 最初の1回で段の並びを覚える。
                # 覚えられなければ二度と試さない。
                # 会話の要約は別スレッドで回る（GAME.md §2.25）ので、
                # 2つのスレッドがここへ同時に入りうる。
                # 錠は掛けない。
                # 総当たりは元の関数を読むだけで副作用が無く、
                # 二重に走っても結果は同じ（記録が1組増えるだけ）。
                try:
                    ladders, why = learn(orig)
                except Exception:
                    ctx.log_exc("charisma impression: 段の総当たりが失敗した; 素通しにする")
                    state["ladders"] = None
                    return result
                state["ladders"] = ladders
                if ladders is None:
                    ctx.log("charisma impression: 段の並びが読めない（{}）; "
                            "何もしない".format(why), level="WARN")
                    write("段の並びが読めないので何もしない: {}".format(why))
                    return result
                report(ladders)
                ctx.log("charisma impression: 魅力 {} 段 / 好感度 {} 段を覚えた".format(
                    len(ladders["charm"]), len(ladders["affinity"])))

            ladders = state["ladders"]
            if ladders is None:
                return result
            row = _as_tuple(result)
            if row is None:
                return result

            try:
                player = player_of()
                npc = npc_in_frames(player)
                key = key_of(npc, player) if npc is not None else None
                if key is None and not state["no_npc_warned"]:
                    # 1度だけ記録する。
                    # 毎回出すと、効いている行が埋まる。
                    state["no_npc_warned"] = True
                    write("{}。好みの差は付かない（親しさだけ効く）。呼び出し元: {}".format(
                        "呼び出し元から NPC を拾えない" if npc is None
                        else "相手は拾えたが id も名前も読めない", frames.caller()))
                taste = state["taste"].get(key)
                if taste is None:
                    taste = _taste_of(key, TASTE_SPREAD)
                    if key is not None:
                        state["taste"][key] = taste
                picked, note = decide(ladders, row, taste)
            except Exception:
                ctx.log_exc("charisma impression: 段の決定が失敗した; 素通しにする")
                return result

            name = frames.text_of(npc, "name") if npc is not None else None
            before = row[ladders["charm_index"]] \
                if ladders["charm_index"] < len(row) else None
            if picked is None:
                write("{}: {}（そのまま）".format(name or "?", note))
                return result

            write("{}: {} {!r} → {!r}".format(
                name or "?", note, before, ladders["charm"][picked]))
            return rebuild(row, ladders["charm_index"],
                           ladders["charm"][picked], result)
        except Exception:
            ctx.log_exc("charisma impression: 調整に失敗した; 素通しにする")
            return result
