# -*- coding: utf-8 -*-
"""会話や自由入力から入る戦闘で、パーティの仲間が敵側に選ばれるのを直す。

##### 何が起きているか

クエストの外の自由入力と会話はマスターAI（`master_ai_facilitator` 系の4関数。
GAME.md §2.26）が処理していて、戦闘を起こすときは応答の `process` に

    {"type": "start_battle",
     "player_opponents": [<NPC名> ...最大3],
     "player_allies":    [<NPC名> ...最大2]}

を入れて返す。名前の候補はゲームがスキーマの Literal として組み、
その元は同じ呼び出しに渡る `npc_list`。
同行中の仲間もその場に居るので候補に入り、
LLM が `player_opponents` に仲間の名前を書くと、そのまま仲間が敵として戦闘に出てくる。

##### どこを触るか

4関数の応答が返った直後に `process` を見て、
`start_battle` の `player_opponents` からパーティ名簿に居る名前を外す。

- 外しても相手が残るなら、残った相手だけで戦闘を起こす
- 誰も残らないなら `start_battle` そのものを `process` から抜く
  （敵0件の戦闘をゲームがどう食うか確かめていないので、起こさない側に倒す）

どちらの編集も、結果はスキーマが元々許す形（候補の部分集合・空の `process`）に
収まる。ゲーム側から見れば「LLM が最初からそう答えた」のと区別が付かない。

名簿はローダの `ui.party_member_ids`（在り処を決めつけない取り方。GAME.md §2.8）、
名前は `world.characters` から引く。
名前で照合するのは、`player_opponents` に入っているのが名前だけだから
（id は載らない。名前の重複は `120_` が防いでいる前提に乗る）。

##### 何を触らないか

- `player_allies` はそのまま通す。仲間が味方側で挙がるのは向きとして正しく、
  ゲームがそれをどう扱うかも測っていない
- 「行動」メニューの「戦闘を仕掛ける」（`start_battle_with_in_conversation`）は
  触らない。自分で選んで仕掛けた相手まで外さない
- `think` と `narration` は書き換えない。仲間との戦いを語った直後に
  戦闘が起きない回はあり得る（起きたかどうかはログで分かる）

仲間との手合わせ（模擬戦）も起こせなくなる。
それも「仲間が敵として選出される」の一形なので、区別せずに外す。

##### 記録

`out/party_opponent.log`。外した回も、外さなかった回も1行ずつ残す。
"""

from instantale_modloader import ui

LOG_BASENAME = "party_opponent.log"

# クエスト外（と、クエスト中の自由入力・会話）を処理するマスターAI の4関数
# （GAME.md §2.26。`faciltiator` の綴りはゲーム側のまま。
# この経路を触る MOD は4つとも見る決まり）。
FACILITATORS = (
    "master_ai_facilitator",
    "master_ai_facilitator_in_quest",
    "master_ai_facilitator_from_conversation",
    "master_ai_faciltiator_from_conversation_in_quest",
)


def _get(container, name, default=None):
    """dict でも属性でも読む（`313_` の `_get` と同じ方針）。

    応答は pydantic のモデルだが、形を決めつけずに両方で通す。
    どちらでもなければ何もしない。
    """
    if container is None:
        return default
    if isinstance(container, dict):
        return container.get(name, default)
    value = getattr(container, name, None)
    return default if value is None else value


def _set(container, name, value):
    """`_get` と対。**入れた値がそのまま読み返せたときだけ** True（`313_` と同じ）。"""
    try:
        if isinstance(container, dict):
            container[name] = value
            return True
        setattr(container, name, value)
        current = getattr(container, name, None)
        return current == value and isinstance(current, type(value))
    except Exception:
        return False


def party_names(app):
    """同行者の名前の集合。読めない名前は入れない。プレイヤーは含まない。"""
    names = set()
    if app is None:
        return names
    for member_id in ui.party_member_ids(app):
        name = getattr(ui.character_of(app, member_id), "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def split_opponents(opponents, names):
    """`player_opponents` を `(残す, 外す)` に分ける。照合は前後の空白だけ均す。"""
    kept, removed = [], []
    for entry in opponents:
        if isinstance(entry, str) and entry.strip() in names:
            removed.append(entry)
        else:
            kept.append(entry)
    return kept, removed


def drop_step(result, steps, step):
    """`process` から1段を抜く。抜けたら True。

    等値ではなく同一性で選ぶ（pydantic のモデルは項目が同じだと等値になり、
    同じ内容の別の段まで巻き込みかねない）。
    """
    remaining = [s for s in steps if s is not step]
    if isinstance(steps, list):
        steps[:] = remaining
        return True
    try:
        return _set(result, "process", type(steps)(remaining))
    except Exception:
        return False


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    state = {"filtered": 0, "dropped": 0}

    def fix_result(site, result):
        steps = _get(result, "process")
        if not isinstance(steps, (list, tuple)):
            return
        battles = [s for s in steps if _get(s, "type") == "start_battle"]
        if not battles:
            return
        names = party_names(ui.find_app())
        if not names:
            # 同行者が居ない（か、名簿が読めない）。
            # 素通しの回も残す。残さないと「MOD が動いていない」のと見分けが付かない。
            write("{}: start_battle を素通し（同行者なし）".format(site))
            return
        for step in battles:
            opponents = _get(step, "player_opponents")
            if not isinstance(opponents, (list, tuple)):
                continue
            kept, removed = split_opponents(opponents, names)
            if not removed:
                write("{}: 敵={} に仲間は居ない。素通し".format(site, list(opponents)))
                continue
            if kept:
                if _set(step, "player_opponents", list(kept)):
                    state["filtered"] += 1
                    write("{}: 敵から仲間を外した {} -> {}".format(
                        site, list(opponents), kept))
                else:
                    write("{}: player_opponents を書き換えられなかった"
                          "（型が受け付けない）。素通し: {}".format(
                              site, list(opponents)))
            else:
                if drop_step(result, steps, step):
                    state["dropped"] += 1
                    write("{}: 敵が仲間だけ {} だったので戦闘を起こさない".format(
                        site, list(opponents)))
                else:
                    write("{}: start_battle を抜けなかった。素通し: {}".format(
                        site, list(opponents)))

    def make_wrapper(name):
        # `scripts.llm.llm_manager` は最初の LLM リクエストまで import されない
        # （TECH.md §3.4）。`required=False` で先に登録し、
        # 現れた時点でローダに当て直させる（`313_` と同じ形）。
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name),
                  required=False, safe=True)
        def facilitate(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                fix_result(name, result)
            except Exception:
                # 編集に失敗しても応答はそのまま通す。会話を止めない。
                ctx.log_exc("party opponent: could not fix {}".format(name))
            return result
        return facilitate

    for name in FACILITATORS:
        make_wrapper(name)

    # ------------------------------------------------------------ 自己検証
    # 実経路はマスターAI が戦闘を起こすまで通らないので、計算だけ先に確かめる。
    cases = (
        # (敵の一覧, 名簿, 残す, 外す)
        (["ゴブリン", "レオン"], {"レオン"}, ["ゴブリン"], ["レオン"]),
        (["レオン"], {"レオン"}, [], ["レオン"]),
        (["ゴブリン"], {"レオン"}, ["ゴブリン"], []),
        ([" レオン "], {"レオン"}, [], [" レオン "]),   # 空白は均して照合する
        ([], {"レオン"}, [], []),
        (["レオン"], set(), ["レオン"], []),
    )
    failures = [c for c in cases if split_opponents(c[0], c[1]) != (c[2], c[3])]
    sample = {"process": [{"type": "npc_say"}, {"type": "start_battle"}]}
    ok = (drop_step(sample, sample["process"], sample["process"][1])
          and sample["process"] == [{"type": "npc_say"}])
    if failures or not ok:
        ctx.log("VERIFY FAILED: split={} drop={}".format(failures, sample),
                level="ERROR")
    else:
        ctx.log("verified: split_opponents on {} cases / drop_step".format(
            len(cases)))

    ctx.log("party opponent fix installed; targets={} log={}".format(
        len(FACILITATORS), ctx.out_path(LOG_BASENAME)))
