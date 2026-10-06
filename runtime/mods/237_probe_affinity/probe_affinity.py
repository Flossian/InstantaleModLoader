# -*- coding: utf-8 -*-
r"""計測: NPC の好感度（`relationship.player.affinity`）がいつ・どれだけ動くか。ゲームは変えない。

##### 何を決めるための計測か

会話で好感度が上下する MOD を作るかどうかの下調べ。
`213_probe_npc_memory` の記録では、会話を閉じた192回のうち好感度が動いたのは1回（0 → 4）だけだった
（素のゲームの会話は好感度をほぼ動かさない）。
作るなら、会話で動かす幅の物差しと、書いた値が感情の文に出る時機が要る。

| 問い | 見るところ |
|---|---|
| 依頼のクリアで同行者の好感度がいくつ上がるか（会話の幅の物差し） | `QuestEndManager.execute` の前後の差 |
| 依頼の放棄で動くか | `QuestRetireManager.execute` の前後の差 |
| 日数の送りで動くか（213 の 0 → 4 の手掛かり） | `InstantaleApp.elapse_days` の前後の差 |
| 会話の終わりで動くか（213 の裏取り） | `ConversationEndManager.resolve_conversation` の前後の差 |
| 感情の文（`affinity_text`）はいつ組み直されるか | `document_emotion_scores_new` の呼び出し元と引数 |

依頼の終わりは要約などを裏で続けるので、終わった直後と `LATE_SECONDS` 秒後の2回比べる。
2回目は直後から動いた分だけを書く。
2回目は時間で区切るだけなので、窓が重なる（依頼の終わりの直後に会話を閉じた）と、
同じ変化が両方の `late` に出る。どちらの窓のものかは時刻と `change` の行で読み分ける。

録るもの（1件＝1行）

    change    窓の前後で好感度が動いた。窓の名前、動いた人（id・名前・同行中か）、前後の値と差、
              同行者の一覧、窓の秒数。動いた人が居なければ `still`（窓の名前・見た人数・同行者。
              窓ごとに最初の1回だけ。以後は動かなかった回は書かない）
    late      窓が閉じた `LATE_SECONDS` 秒後に、直後から動いた分
    emotion   感情の文を組み直した。引数（好感度・魅力）、戻り、呼び出し元
              （呼び出し元ごとに最初の `CALLER_SAMPLES` 回だけ。以後は書かない）

    out\affinity.log     読む用
    out\affinity.jsonl   後から数える用

読み取りだけ。値も乱数も state\ もセーブも動かさない。引数は受け取ったまま `orig` へ渡す。
"""
import datetime
import sys
import threading
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "affinity.log"
RECORD_BASENAME = "affinity.jsonl"
INSTALLED_MARK = "_instantale_probe_affinity_installed"

#: 前後を比べる窓。`(対象, 名前, 遅れて比べ直すか)`。
WINDOWS = (
    ("__main__:QuestEndManager.execute", "quest_end", True),
    ("__main__:QuestRetireManager.execute", "quest_retire", True),
    ("__main__:InstantaleApp.elapse_days", "elapse_days", False),
    ("__main__:ConversationEndManager.resolve_conversation", "conversation_end", True),
)

#: 窓が閉じてから比べ直すまでの秒数。
LATE_SECONDS = 15.0

#: 呼び出し元の連鎖の段数と、呼び出し元ごとに組む回数。
CALLER_DEPTH = 6
CALLER_SAMPLES = 5


def affinity_of(character):
    relationship = getattr(character, "relationship", None)
    row = relationship.get("player") if isinstance(relationship, dict) else None
    value = row.get("affinity") if isinstance(row, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def snapshot(app):
    """`{id: 好感度}`。実行時の名簿（`world.characters`）の全員。プレイヤーは除く。"""
    characters = getattr(getattr(app, "world", None), "characters", None)
    found = {}
    if not isinstance(characters, dict):
        return found
    for npc_id, character in list(characters.items()):
        if str(npc_id) == "player":
            continue
        value = affinity_of(character)
        if value is not None:
            found[str(npc_id)] = value
    return found


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    started = time.monotonic()

    def event(kind, **fields):
        row = {"at": datetime.datetime.now().isoformat(timespec="milliseconds"),
               "t": round(time.monotonic() - started, 3), "kind": kind,
               "thread": threading.current_thread().name}
        row.update(fields)
        record(row)
        write("{:>9.3f} [{}] {} {}".format(
            row["t"], row["thread"], kind,
            " ".join("{}={}".format(k, v) for k, v in fields.items())))

    def diff(app, before, after, party):
        rows = []
        for npc_id in sorted(set(before) | set(after), key=ui.id_sort_key):
            old, new = before.get(npc_id), after.get(npc_id)
            if old == new:
                continue
            rows.append({"id": npc_id, "name": ui.character_name(app, npc_id),
                         "party": npc_id in party, "before": old, "after": new,
                         "delta": (new - old) if old is not None and new is not None else None})
        return rows

    def find_app(owner):
        app = getattr(owner, "app", None)
        if app is None and getattr(owner, "world", None) is not None:
            app = owner
        return app or ui.find_app()

    #: `still` を書いた窓。
    still_seen = set()

    def watch(name, late):
        def wrapper(orig, self, *args, **kwargs):
            app = None
            before, party, opened = {}, [], time.monotonic()
            try:
                app = find_app(self)
                before = snapshot(app)
                party = [str(member) for member in ui.party_member_ids(app)]
            except Exception:
                ctx.log_exc("affinity probe: snapshot before {} failed".format(name))
            try:
                return orig(self, *args, **kwargs)
            finally:
                try:
                    if app is not None and before:
                        after = snapshot(app)
                        rows = diff(app, before, after, set(party))
                        seconds = round(time.monotonic() - opened, 2)
                        if rows:
                            event("change", window=name, changed=rows, party=party,
                                  seconds=seconds)
                        elif name not in still_seen:
                            # 日数送りのたびに来るので、動かなかった回は窓ごとに最初の1回だけ書く
                            still_seen.add(name)
                            event("still", window=name, seen=len(after), party=party,
                                  seconds=seconds)
                        if late:
                            timer = threading.Timer(
                                LATE_SECONDS, lambda: late_check(app, name, after, set(party)))
                            timer.daemon = True
                            timer.start()
                except Exception:
                    ctx.log_exc("affinity probe: snapshot after {} failed".format(name))
        return wrapper

    def late_check(app, name, after, party):
        try:
            rows = diff(app, after, snapshot(app), party)
            if rows:
                event("late", window=name, changed=rows, wait=LATE_SECONDS)
        except Exception:
            ctx.log_exc("affinity probe: late check of {} failed".format(name))

    for target, name, late in WINDOWS:
        ctx.wrap(target, required=False, safe=True)(watch(name, late))

    # ------------------------------------------------------------ 感情の文
    #: 呼び出し元ごとの回数（呼び出し元を組むのは最初の `CALLER_SAMPLES` 回だけ）。
    seen_callers = {}

    @ctx.wrap("scripts.functions:document_emotion_scores_new", required=False, safe=True)
    def emotion(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            caller = frames.caller(CALLER_DEPTH)
            key = repr(caller)
            seen_callers[key] = seen_callers.get(key, 0) + 1
            if seen_callers[key] <= CALLER_SAMPLES:
                event("emotion", args=[frames.short(repr(a), 40) for a in args],
                      kwargs={k: frames.short(repr(v), 40) for k, v in kwargs.items()},
                      result=frames.short(repr(result), 80), caller=caller,
                      count=seen_callers[key])
        except Exception:
            ctx.log_exc("affinity probe: recording document_emotion_scores_new failed")
        return result

    if not getattr(sys, INSTALLED_MARK, False):
        setattr(sys, INSTALLED_MARK, True)
        write("installed: windows {} (late check {}s), emotion text callers".format(
            [name for _t, name, _l in WINDOWS], LATE_SECONDS))
