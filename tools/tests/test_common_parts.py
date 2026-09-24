# -*- coding: utf-8 -*-
"""共通部品（`state.WorldStore` / `jobs.Worker` / `llm` の読み取り / `ui.game_day`）。

写して回っていたものをローダへ寄せた4つ。
ここで見るのは**寄せた先が、寄せる前の9本ぶんの要求を全部満たすか**:

| 見るもの | なぜ |
|---|---|
| `WorldStore` が読めないファイルを黙って空へ倒さない | 倒すと次の書き込みが空に近い正本を無傷で作る（TECH.md §3.11.1） |
| `own=False` がフォルダを作らない | 相手を切っている人の `state/` に空のフォルダを置かない（§3.11） |
| `fresh=True` が相手の更新を拾う | `403_` / `404_` が `311_` の控えを読む経路 |
| `Worker` が溢れたら古い方から捨てる | 推論が返らない間に会話を続けても際限なく溜めない |
| `Worker` が同じ鍵を二度積まない | `317_` / `321_` の編纂の重複除け |
| `Worker` が1件の失敗で止まらない | 止まると遊んでいる側からは何も起きなくなる |
| `llm.parse_json` が囲みと前置きを越える | 5本が各自で剥がしていて、3通りに枝分かれしていた |
| `llm.truthy` が両方の倒し方を持つ | `changed` は True へ、`content_violation` は False へ |
| `ui.game_day` がロード中も読める | 受け皿を持っていたのは `312_` だけだった |
| `ui.pressed_entry` がページ送りの枠を None にする | 地図の値が `'next'` の枠を添字に落とすと、自前の一覧を出す MOD が「次」を横取りする（`325_` で実際に起きた） |
| `ctx.jsonl` が JSON にできない値でも行を落とさない | probe が拾うのはゲームの生の値で、何が来るか決まらない（8本の写しを寄せた先） |
| `ctx.logger(dedup=True)` が変わったときだけ書く | 会話の LLM は1ターンに何度も回る。6本の写しを寄せた先 |
| `dedup` が `cap` の枠を食わない | 逆だと `cap=10, dedup=True` が「1行書いて終わり」になる |
| `npcs.enroll` が心当たりを全部見て書く | セーブの形＝実行時の形ではない（GAME.md §2.7）。実行時だけに足すと次のセーブで消える |
| `frames.arg` / `replace_arg` が位置でもキーワードでも届く | 5本に写っていて、届かなかったときの振る舞いが `327_` と `910_` で違った |
| `sounds` が曲の置き場と戦闘曲を見分ける | `104_` / `106_` / `322_` / `324_` に同じ本体が写っていた |
| `ui.walk_widgets` が2通りの順を出し分ける | `330_` / `402_` は「最初の1つ」を採るので、実機で確かめた順（古い子から）を変えられない |
| 寸法・見分け・テンプレート・クエスト・世界観の小さな部品 | 2〜3本ずつ写されていた（HANDOFF の §2） |
| `state.SysWorldStore` が上書き鍵を優先する | `modnpc` / `modfacility` の建て直しの間の鍵 |
| `ui.set_gold` / `add_gold` が所持金の型を保つ | `314_` / `315_` / `332_` の写しが避けていた int への変換を、`add_gold` が行っていた |
| `ui.scheduler` / `window_watcher` が Clock の中の例外を握る | MOD は素の関数を渡している。漏れるとゲームごと落ちる |
| `Screen.busy_on` を重ねても戻す値と interval が増えない | 取り直すと自分の `False` を覚えて、選択肢が押せないまま残る |
| `llm.watch_aliases` が注入し直しで降りるときも1行残す | 黙って降りると、当たらなかった理由を追えない |

ゲームは要らない（偽の `ctx` を渡す）。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import instantale_modloader as ml                                   # noqa: E402
from instantale_modloader import frames, jobs, llm, npcs, sounds, ui  # noqa: E402
from instantale_modloader import state as state_mod                 # noqa: E402
from instantale_modloader.state import world_filename               # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    if ok:
        print("  ok   " + label)
    else:
        FAILURES.append(label)
        print("  FAIL " + label + ((" :: " + str(detail)) if detail else ""))


class FakeCtx(object):
    """`ctx` のうち `WorldStore` / `Worker` が使う分だけ。"""

    def __init__(self, root):
        self.state_dir = os.path.join(root, "state")
        self.errors = []
        self.reads = []

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def read_json(self, path, default=None):
        self.reads.append(path)
        if not os.path.exists(path):
            return default
        try:
            with io.open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            # 本物と同じ振る舞い ―「在るのに読めない」は記録してから倒す。
            self.errors.append("read_json failed: " + path)
            return default

    def write_json(self, path, data, *, indent=1):
        try:
            with io.open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=indent)
            return True
        except Exception:
            return False

    def log_exc(self, msg):
        self.errors.append(msg)


# ---------------------------------------------------------------- WorldStore

def test_world_store(root):
    print("state.WorldStore")
    ctx = FakeCtx(root)
    store = state_mod.WorldStore(ctx, "my_mod")

    check("初回は空", store.load("灰の街") == {})
    bucket = store.load("灰の街")
    bucket["n1"] = {"profile": "無口"}
    check("読んだ dict がそのまま控え（書き換えたら効く）",
          store.load("灰の街") is bucket)
    check("書けた", store.save("灰の街"))

    path = os.path.join(ctx.state_dir, "my_mod", world_filename("灰の街"))
    with io.open(path, encoding="utf-8") as fh:
        check("ファイルの中身", json.load(fh) == {"n1": {"profile": "無口"}})

    store.forget()
    check("読み直しても同じ", store.load("灰の街") == {"n1": {"profile": "無口"}})

    # 世界が違えばファイルも別。
    store.save("鉄の港", {"n2": {}})
    check("世界の一覧", store.worlds() == sorted(["灰の街", "鉄の港"]))

    # 壊れたファイルは「無い」と同じ扱いにしない。
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("{壊れた")
    store.forget()
    before = len(ctx.errors)
    check("壊れていれば空に倒すが、倒したことを残す",
          store.load("灰の街") == {} and len(ctx.errors) > before)

    # 並びを固定する口。
    ordered = state_mod.WorldStore(
        ctx, "ordered_mod",
        order=lambda b: {k: b[k] for k in sorted(b)})
    ordered.save("灰の街", {"b": 2, "a": 1})
    with io.open(os.path.join(ctx.state_dir, "ordered_mod",
                              world_filename("灰の街")), encoding="utf-8") as fh:
        check("order= が書く前に並べ直す", list(json.load(fh)) == ["a", "b"])

    # 古い形の移行。
    calls = []

    def normalize(bucket):
        calls.append(dict(bucket))
        if "old" in bucket:
            return {"new": bucket["old"]}, True
        return bucket, False

    migrating = state_mod.WorldStore(ctx, "migrate_mod", normalize=normalize)
    ctx.write_json(migrating.path("灰の街"), {"old": 1})
    check("normalize= が読んだ形を直す", migrating.load("灰の街") == {"new": 1})
    migrating.forget()
    check("直した形は書き戻してある（2回目は直す必要が無い）",
          migrating.load("灰の街") == {"new": 1} and calls[-1] == {"new": 1})


def test_foreign_store(root):
    print("state.WorldStore（他の MOD の控えを読む）")
    ctx = FakeCtx(root)
    foreign = state_mod.WorldStore(ctx, "someone_else", own=False)

    dir_path = os.path.join(ctx.state_dir, "someone_else")
    check("読んでもフォルダを作らない",
          foreign.load("灰の街") == {} and not os.path.exists(dir_path))

    raised = False
    try:
        foreign.save("灰の街", {"x": 1})
    except ValueError:
        raised = True
    check("読むだけの控えには書かせない", raised)

    # 相手が書いた後は読めるようになる。
    owner = state_mod.WorldStore(ctx, "someone_else")
    owner.save("灰の街", {"n1": {"profile": "初版"}})
    check("相手が書けば読める",
          foreign.load("灰の街", fresh=True) == {"n1": {"profile": "初版"}})

    # 相手が書き換えたら読み直す。**同じ秒に書いても**気付くこと
    # （更新時刻だけでなく大きさも見ている）。
    owner.save("灰の街", {"n1": {"profile": "第二版"}, "n2": {}})
    check("相手の書き換えを拾う（fresh=True）",
          foreign.load("灰の街", fresh=True)["n1"]["profile"] == "第二版")
    check("fresh を渡さなければ読み直さない",
          foreign.load("灰の街") is foreign.load("灰の街"))


# -------------------------------------------------------------------- Worker

def test_worker(root):
    print("jobs.Worker")
    ctx = FakeCtx(root)
    done = []
    gate = threading.Event()

    def run(job):
        gate.wait(5.0)
        done.append(job)

    worker = jobs.Worker(ctx, run, name="test_worker", max_pending=2, idle=1.0)
    check("積む前はスレッドが居ない", not worker.alive())

    worker.enqueue({"n": 1})     # これは走り出して gate で止まる
    worker.enqueue({"n": 2})
    worker.enqueue({"n": 3})
    worker.enqueue({"n": 4})     # 上限2を超えたので {"n": 2} が落ちる
    check("待ち行列は上限まで", worker.pending() == 2)
    gate.set()
    check("捌け終わる", worker.drain(5.0))
    check("溢れたぶんは古い方から捨てた（残るのは 1・3・4）",
          [job["n"] for job in done] == [1, 3, 4], done)

    # 同じ鍵は二度積まない。
    seen = []
    hold = threading.Event()

    def run2(job):
        hold.wait(5.0)
        seen.append(job)

    keyed = jobs.Worker(ctx, run2, name="test_keyed", idle=1.0,
                        key=lambda job: job["area"])
    keyed.enqueue({"area": "a"})          # 走り出して hold で止まる
    check("走り出した1件は待ち行列から出ている", keyed.pending() == 0)
    # 処理中の鍵も積まない。積む側は `run` が書く結果を見て積むかを決めるので、
    # 受けると同じ入力で LLM を二度呼ぶ（jobs.Worker.enqueue の注記）。
    check("処理中の鍵は積まない", not keyed.enqueue({"area": "a"}))
    check("処理中の鍵も waiting が答える", keyed.waiting("a"))
    keyed.enqueue({"area": "b"})
    check("違う鍵は積む", keyed.enqueue({"area": "c"}))
    check("待っている鍵は積まない", not keyed.enqueue({"area": "b"}))
    check("待っているか答えられる", keyed.waiting("b") and not keyed.waiting("z"))
    hold.set()
    check("捌け終わる", keyed.drain(5.0))
    check("こなしたのは3件", [job["area"] for job in seen] == ["a", "b", "c"], seen)
    check("片付いた鍵はもう待っていない", not keyed.waiting("b"))

    # 1件の失敗で止まらない。
    ran = []

    def boom(job):
        ran.append(job)
        if job == "bad":
            raise RuntimeError("落ちる仕事")

    ctx.errors[:] = []
    hardy = jobs.Worker(ctx, boom, name="test_hardy", label="hardy", idle=1.0)
    hardy.enqueue("bad")
    hardy.enqueue("good")
    check("捌け終わる", hardy.drain(5.0))
    check("失敗した次の仕事もこなす", ran == ["bad", "good"], ran)
    check("失敗は記録に残る", any("hardy" in e for e in ctx.errors), ctx.errors)

    # 仕事が無ければ畳み、次の仕事で起き直す。
    quick = jobs.Worker(ctx, lambda job: None, name="test_idle", idle=0.05)
    quick.enqueue(1)
    quick.drain(5.0)
    deadline = time.monotonic() + 5.0
    while quick.alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    check("仕事が無ければ自分で畳む", not quick.alive())
    quick.enqueue(2)
    check("次の仕事で起き直す", quick.alive() or quick.drain(5.0))

    # 世代の繋ぎ替え。
    later = []
    quick.rebind(ctx, run=lambda job: later.append(job))
    quick.enqueue(3)
    quick.drain(5.0)
    check("rebind で新しい世代の仕事に切り替わる", later == [3], later)


# ----------------------------------------------------------------------- llm

def test_llm_reading():
    print("llm.strip_fence / parse_json / truthy")
    payload = {"changed": True, "profile": "無口な傭兵"}
    body = json.dumps(payload, ensure_ascii=False)

    check("素の JSON", llm.parse_json(body) == payload)
    check("囲み付き", llm.parse_json("```json\n" + body + "\n```") == payload)
    check("札が大文字でも数字付きでも剥がす",
          llm.parse_json("```JSON5\n" + body + "\n```") == payload)
    check("札の無い囲み", llm.parse_json("```\n" + body + "\n```") == payload)
    check("前置きと後書きを越える",
          llm.parse_json("こちらです。\n" + body + "\n以上です。") == payload)
    check("辞書はそのまま", llm.parse_json(payload) == payload)
    check("読めなければ None",
          llm.parse_json("霧が晴れました。") is None
          and llm.parse_json("{壊れた json") is None
          and llm.parse_json(None) is None)
    check("配列は受けない（1つの辞書を返す約束）", llm.parse_json("[1, 2]") is None)

    check("囲みの中の素の文章は本文だけ残る",
          llm.strip_fence("```\nこんにちは\n世界\n```") == "こんにちは\n世界")
    check("囲みが無ければそのまま", llm.strip_fence("  ただの文  ") == "ただの文")
    check("文字列でなければ空", llm.strip_fence(None) == "")

    check("真偽値はそのまま", llm.truthy(True) and not llm.truthy(False))
    check("`false` と書く相手にも耐える",
          not llm.truthy("false") and not llm.truthy("なし")
          and not llm.truthy("変更なし"))
    check("`true` 系は両方の倒し方で True",
          llm.truthy("yes") and llm.truthy("yes", unknown=False))
    check("判らない語は unknown が決める",
          llm.truthy("よく分からない語")
          and not llm.truthy("よく分からない語", unknown=False))
    check("None と 0 は False", not llm.truthy(None) and not llm.truthy(0))


# ------------------------------------------------------------------ game_day

def test_game_day():
    print("ui.game_day")
    world = types.SimpleNamespace(days_elapsed=12)
    check("app から", ui.game_day(types.SimpleNamespace(world=world)) == 12)
    check("World そのものから（`World.__init__` を包む場面）",
          ui.game_day(world) == 12)
    check("float は int にする",
          ui.game_day(types.SimpleNamespace(world=types.SimpleNamespace(
              days_elapsed=7.0))) == 7)
    check("ロードの途中は world_dict から拾う",
          ui.game_day(types.SimpleNamespace(
              world=None, world_dict={"world_data": {"days_elapsed": 3}})) == 3)
    check("真偽値は日数ではない",
          ui.game_day(types.SimpleNamespace(world=types.SimpleNamespace(
              days_elapsed=True))) is None)
    check("読めなければ None", ui.game_day(object()) is None)


def test_pressed_entry():
    print("ui.pressed_entry")
    buttons = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    plain = types.SimpleNamespace(buttons=buttons)
    check("地図が無ければ添字そのまま", ui.pressed_entry(plain, 1) is buttons[1])
    mapped = types.SimpleNamespace(buttons=buttons, display_button_map=[2, 0])
    check("地図があれば地図で引き直す", ui.pressed_entry(mapped, 0) is buttons[2])
    # 1ページに収まらないとき、最後の枠は `次` で地図の値は 'next'（`206_` の記録。GAME.md §2.2）。
    paged = types.SimpleNamespace(buttons=buttons, display_button_map=[0, "next"])
    check("ページ送りの枠（整数でない）は None ＝ ボタンではない",
          ui.pressed_entry(paged, 1) is None)
    check("地図が bool でも None", ui.pressed_entry(
        types.SimpleNamespace(buttons=buttons, display_button_map=[True]), 0) is None)
    check("地図より外の添字は添字そのまま", ui.pressed_entry(mapped, 2) is buttons[2])
    check("範囲外は None", ui.pressed_entry(plain, 9) is None)


def test_records(root):
    """`ctx.jsonl` と `ctx.logger(dedup=True)`。8本と6本の写しを寄せた先。"""
    print("[記録]")
    out = os.path.join(root, "out")
    ctx = ml.ModContext(out, os.path.join(ROOT, "runtime"))

    record = ctx.jsonl("probe.jsonl")
    record({"n": 1})
    record({"obj": object(), "when": "x"})          # JSON にできない値を混ぜる
    rows = [json.loads(l) for l in io.open(os.path.join(out, "probe.jsonl"), encoding="utf-8")]
    check("jsonl: 1行1件で書ける", len(rows) == 2, rows)
    check("jsonl: JSON にできない値でも行を落とさない",
          isinstance(rows[1]["obj"], str) and rows[1]["when"] == "x", rows[1])

    note = ctx.logger("note.log", dedup=True, stamp=False)
    for message in ("A", "A", "A", "B", "B", "A"):
        note(message)
    lines = io.open(os.path.join(out, "note.log"), encoding="utf-8").read().split()
    check("dedup: 変わったときだけ書く", lines == ["A", "B", "A"], lines)

    both = ctx.logger("both.log", dedup=True, cap=2, stamp=False)
    for message in ["X"] * 5 + ["Y", "Z"]:
        both(message)
    lines = io.open(os.path.join(out, "both.log"), encoding="utf-8").read().split()
    # 書かなかった行が枠を食うと ["X"] だけになる。
    check("dedup は cap の枠を食わない", lines == ["X", "Y"], lines)

    plain = ctx.logger("plain.log", stamp=False)
    for message in ("A", "A"):
        plain(message)
    lines = io.open(os.path.join(out, "plain.log"), encoding="utf-8").read().split()
    check("dedup 無しは今までどおり全部書く", lines == ["A", "A"], lines)


def test_enroll():
    """`npcs.enroll`。実行時の Area とセーブ側の両方に載せる（2本の写しを寄せた先）。"""
    print("[冒険者名簿]")
    area = types.SimpleNamespace(adventurer_npcs=[])
    app = types.SimpleNamespace(
        world_dict={"areas": {"0": {"adventurer_npcs": []}}},
        save_data_dict={"world_data": {"areas": {"0": {"adventurer_npcs": []}}}})

    wrote = npcs.enroll(app, area, "0", 64)
    check("実行時とセーブの両方に書く",
          wrote == ["area", "world_dict", "save_data_dict"], wrote)
    check("Area に載った", area.adventurer_npcs == [64])
    check("world_dict に載った", app.world_dict["areas"]["0"]["adventurer_npcs"] == [64])
    check("save_data_dict の world_data の下にも載った",
          app.save_data_dict["world_data"]["areas"]["0"]["adventurer_npcs"] == [64])

    check("二度目は何もしない（重複しない）", npcs.enroll(app, area, "0", 64) == [])
    check("重複していない", area.adventurer_npcs == [64])

    # 同じ実体を2箇所から指しているとき、二重に足さない。
    shared = []
    area2 = types.SimpleNamespace(adventurer_npcs=shared)
    app2 = types.SimpleNamespace(world_dict={"areas": {"0": {"adventurer_npcs": shared}}},
                                 save_data_dict=None)
    npcs.enroll(app2, area2, "0", 7)
    check("同じ実体への二重書きを避ける", shared == [7], shared)

    bare = types.SimpleNamespace(adventurer_npcs=None)
    empty = types.SimpleNamespace(world_dict=None, save_data_dict=None)
    check("名簿が1つも無ければ空を返す（例外にしない）",
          npcs.enroll(empty, bare, "0", 1) == [])


def test_args():
    print("frames.arg / replace_arg")
    names = ("quest_data", "player", "log")
    check("キーワードを先に見る", frames.arg(("a", "b"), {"player": "k"}, "player", 1) == "k")
    check("位置で読む（添字）", frames.arg(("a", "b"), {}, "player", 1) == "b")
    check("位置で読む（引数名の並び）", frames.arg(("a", "b"), {}, "player", names) == "b")
    check("届いていなければ default",
          frames.arg(("a",), {}, "player", 1, default="d") == "d")
    check("並びに無い名前はキーワードだけを見る",
          frames.arg(("a", "b", "c"), {}, "choice_text", names) is None
          and frames.arg((), {"choice_text": "x"}, "choice_text", names) == "x")

    args, kwargs = ["a", "b"], {"x": 1}
    new_args, new_kwargs, done = frames.replace_arg(args, kwargs, "player", names, "P")
    check("位置の差し替え", done and new_args == ("a", "P") and new_kwargs == {"x": 1})
    check("渡した入れ物は書き換えない", args == ["a", "b"] and kwargs == {"x": 1})
    new_args, new_kwargs, done = frames.replace_arg(("a",), {"player": 1}, "player", 1, "P")
    check("キーワードの差し替え", done and new_kwargs == {"player": "P"} and new_args == ("a",))
    new_args, new_kwargs, done = frames.replace_arg(("a",), {}, "player", 1, "P")
    check("届いていなければ触らない（既定）", not done and new_kwargs == {} and new_args == ("a",))
    new_args, new_kwargs, done = frames.replace_arg(("a",), {}, "log", names, "L", insert=True)
    check("insert=True ならキーワードとして足す", done and new_kwargs == {"log": "L"})


def test_sounds(root):
    print("sounds")
    check("戦闘曲（区切り・大文字を問わない）",
          sounds.is_battle_track(r"Assets\Sounds\Musics\Battle\a.mp3")
          and sounds.is_battle_track("musics/battle/a.ogg"))
    check("戦闘曲でないもの",
          not sounds.is_battle_track("Assets/sounds/musics/town/a.mp3")
          and not sounds.is_battle_track(None) and not sounds.is_battle_track(""))
    check("重み", sounds.coerce_weight("40") == 40.0 and sounds.coerce_weight(-1) == 0.0
          and sounds.coerce_weight(float("nan")) == 0.0 and sounds.coerce_weight(True) == 100.0)

    class Playing(object):
        def get_num_channels(self):
            return 1

    class Broken(object):
        def get_num_channels(self):
            raise RuntimeError("gone")
    check("鳴っているか", sounds.audible(Playing()) and not sounds.audible(Broken())
          and not sounds.audible(None))

    game = os.path.join(root, "game")
    os.makedirs(os.path.join(game, *sounds.MUSIC_SUBDIR))
    here = os.getcwd()
    try:
        os.chdir(game)
        found = sounds.game_root()
        check("カレントから曲の置き場を探す",
              found is not None and os.path.samefile(found, game), found)
        check("無いサブフォルダなら None",
              sounds.game_root(("no", "such", "dir")) is None)
    finally:
        os.chdir(here)


class W(object):
    """ウィジェットの代わり。`children` は Kivy と同じく新しい順。"""

    def __init__(self, name, *children, **attrs):
        self.name = name
        self.children = list(children)
        self.__dict__.update(attrs)


def test_widgets():
    print("ui.walk_widgets ほか")
    #      root
    #     /        #    b      a      （children は新しい順: b が新しい）
    #    |      |
    #    b1     a1
    a1, b1 = W("a1"), W("b1")
    root = W("root", W("b", b1), W("a", a1))
    names = lambda seq: [w.name for w in seq]
    check("前順（children の並び）",
          names(ui.walk_widgets(root)) == ["root", "b", "b1", "a", "a1"],
          names(ui.walk_widgets(root)))
    check("古い子から（330_ / 402_ の順）",
          names(ui.walk_widgets(root, oldest_first=True)) == ["root", "a", "a1", "b", "b1"],
          names(ui.walk_widgets(root, oldest_first=True)))
    check("深さの上限（その深さまで出す）",
          names(ui.walk_widgets(root, max_depth=1)) == ["root", "b", "a"])
    seen = set()
    first = names(ui.walk_widgets(root.children[0], seen=seen))
    check("seen を共有すれば重なりを二度出さない",
          first == ["b", "b1"] and names(ui.walk_widgets(root, seen=seen)) == ["root", "a", "a1"])
    check("None は何も出さない", list(ui.walk_widgets(None)) == [])
    check("children_of は写しを返す", ui.children_of(root) is not root.children
          and ui.children_of(root) == root.children and ui.children_of(object()) == [])

    label = W("l", text="本文", texture_update=None, text_size=(0, 0))
    check("is_label", ui.is_label(label) and not ui.is_label(W("x", text="t")))
    check("is_label の needs", ui.is_label(W("x", text="t", line_height=1, texture_update=None),
                                           needs=("text", "line_height", "texture_update")))
    check("is_scroller", ui.is_scroller(W("s", scroll_y=1, do_scroll_y=True))
          and not ui.is_scroller(W("s", scroll_y=1)))

    box = W("box", pos=(10, 20), size=(100, 50))
    check("rect_of", ui.rect_of(box) == (10.0, 20.0, 100.0, 50.0) and ui.rect_of(W("n")) is None)
    check("same_rect（許容の内と外）",
          ui.same_rect((15, 25, 110, 55), (10, 20, 100, 50), 12.0, 0.03)
          and not ui.same_rect((40, 20, 100, 50), (10, 20, 100, 50), 12.0, 0.03))
    check("close_enough", ui.close_enough(10.4, 10) and not ui.close_enough(10.6, 10)
          and not ui.close_enough("x", 10))

    safe = {"text": "やめる", "spec": types.SimpleNamespace(cls_name=ui.SAFE_CLS, args=[])}
    marked = dict(safe, **{ui.MARK_PREFIX + "x": True})
    other = {"text": "話す", "spec": types.SimpleNamespace(cls_name="Other", args=[])}
    check("back_button_index は印の無い無害 spec",
          ui.Screen.back_button_index([other, marked, safe]) == 2
          and ui.Screen.back_button_index([other, marked]) is None)

    check("fill_template は知らない名前を残す",
          ui.fill_template("{a}と{typo}", a="砦") == "砦と{typo}"
          and ui.fill_template("{", a=1) == "{")

    check("current_quest_id（インスタンスでも dict でも）",
          ui.current_quest_id(types.SimpleNamespace(current_quest_data={"id": 7})) == "7"
          and ui.current_quest_id(types.SimpleNamespace(
              current_quest_data=types.SimpleNamespace(id="q"))) == "q"
          and ui.current_quest_id(types.SimpleNamespace(current_quest_data=None)) is None
          and ui.current_quest_id(None) is None)
    app = types.SimpleNamespace(save_data_dict={"world_data": {"overview": "  "}},
                                world_dict={"world_data": {"overview": " 世界 " + "あ" * 700}})
    text = ui.world_overview(app)
    check("world_overview は空を飛ばして次を読み、切り詰める",
          text.startswith("世界") and len(text) <= 601, len(text))
    check("world_overview が無ければ空", ui.world_overview(types.SimpleNamespace()) == "")


def test_sys_world_store(root):
    print("state.jsonable / SysWorldStore")
    check("jsonable", state_mod.jsonable({"a": [1, 2.0, None, "x", True]})
          and not state_mod.jsonable({"a": object()}) and not state_mod.jsonable({1: 2}))
    shared = state_mod.SysWorldStore("_instantale_test_sys_store", "sys_store",
                                     "_instantale_test_sys_store_key")
    try:
        check("bind 前は store が None で bucket は空", shared.store() is None
              and shared.bucket(types.SimpleNamespace()) == (None, None))
        first = shared.bind(FakeCtx(root))
        again = shared.bind(FakeCtx(root))
        check("bind し直しても同じ控え（世代をまたぐ）", first is again and shared.store() is first)
        setattr(sys, "_instantale_test_sys_store_key", "上書きの鍵")
        key, bucket = shared.bucket(types.SimpleNamespace())
        check("上書き鍵を優先する", key == "上書きの鍵" and isinstance(bucket, dict), key)
    finally:
        for name in ("_instantale_test_sys_store", "_instantale_test_sys_store_key"):
            if hasattr(sys, name):
                delattr(sys, name)


def test_gold():
    print("ui.set_gold / add_gold")
    def app_with(gold):
        return types.SimpleNamespace(player=types.SimpleNamespace(gold=gold))
    errors = []

    app = app_with(100)
    check("int の所持金へは丸めた int を書く",
          ui.set_gold(app, 49.6) == 50 and type(app.player.gold) is int, app.player.gold)
    app = app_with(100.5)
    check("float の所持金へは float を書く",
          ui.set_gold(app, 49) == 49.0 and type(app.player.gold) is float, app.player.gold)

    app = app_with(100.5)
    check("add_gold は float を保つ（素の値に足す）",
          ui.add_gold(app, -10) == 90.5 and type(app.player.gold) is float, app.player.gold)
    app = app_with(100)
    check("add_gold は int を保つ",
          ui.add_gold(app, 25) == 125 and type(app.player.gold) is int, app.player.gold)

    for bad in (None, True, "100"):
        app = app_with(bad)
        check("読めない所持金（{!r}）には書かない".format(bad),
              ui.set_gold(app, 5) is None and ui.add_gold(app, 5) is None
              and app.player.gold is bad, app.player.gold)
    check("player が無ければ None", ui.set_gold(types.SimpleNamespace(), 5) is None)

    class Locked(object):
        gold = 10

        def __setattr__(self, name, value):
            raise AttributeError(name)
    app = types.SimpleNamespace(player=Locked())
    check("書けなければ None で on_error に渡す",
          ui.set_gold(app, 5, on_error=errors.append) is None and len(errors) == 1, errors)
    check("足せない額は書かずに on_error",
          ui.add_gold(app_with(10), "x", on_error=errors.append) is None
          and len(errors) == 2, errors)


class FakeClock(object):
    """`kivy.clock.Clock` の代わり。載せた関数を溜めるだけで、呼ぶのは検査の側。"""

    def __init__(self):
        self.once = []
        self.intervals = []

    def schedule_once(self, callback, delay=0):
        self.once.append(callback)

    def schedule_interval(self, callback, poll):
        self.intervals.append(callback)


class FakeWindow(object):
    """`kivy.core.window.Window` の代わり。結んだ手を1本ずつ持つ。"""

    def __init__(self):
        self.handlers = []

    def bind(self, on_resize=None):
        self.handlers.append(on_resize)

    def unbind(self, on_resize=None):
        self.handlers.remove(on_resize)


def with_fake_kivy(body):
    """Kivy が在るゲームの中と同じ経路を通すため、偽の `kivy.clock` / `kivy.core.window` を差す。"""
    names = ("kivy", "kivy.clock", "kivy.core", "kivy.core.window")
    saved = {name: sys.modules.get(name) for name in names}
    clock, window = FakeClock(), FakeWindow()
    for name in names:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["kivy.clock"].Clock = clock
    sys.modules["kivy.core.window"].Window = window
    try:
        body(clock, window)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_clock_guard(root):
    print("ui.scheduler / window_watcher / Screen.busy_on（Clock の中の例外と重なり）")
    ctx = FakeCtx(root)

    def body(clock, window):
        def boom():
            raise RuntimeError("MOD の不具合")

        schedule = ui.scheduler(ctx, "guard test")
        ran = []
        schedule(boom)
        schedule(lambda: ran.append(1), delay=0.5)
        raised = None
        try:
            for callback in clock.once:
                callback(0)
        except Exception as exc:
            raised = exc
        check("scheduler: Clock のコールバックから例外が漏れない", raised is None, raised)
        check("scheduler: 投げたことはローダのログに残る",
              any("guard test" in e for e in ctx.errors), ctx.errors)
        check("scheduler: ほかの予約はそのまま走る", ran == [1], ran)

        def on_resize(_window, width, height):
            raise RuntimeError("窓の手の不具合")

        attr = ui.MOD_WIDGET_PREFIX + "guard_test_resize"
        watch = ui.window_watcher(ctx, on_resize, attr, "guard test")
        check("window_watcher: 結べた", watch() and len(window.handlers) == 1,
              window.handlers)
        del ctx.errors[:]
        raised = None
        try:
            window.handlers[0](window, 800, 600)
        except Exception as exc:
            raised = exc
        check("window_watcher: リサイズの手から例外が漏れない", raised is None, raised)
        check("window_watcher: 投げたことはログに残る",
              any("guard test" in e for e in ctx.errors), ctx.errors)
        # 注入し直した世代は、前の世代が残した包みを外してから結ぶ。
        again = ui.window_watcher(ctx, on_resize, attr, "guard test")
        check("window_watcher: 結び直しても手は1本",
              again() and len(window.handlers) == 1, window.handlers)

        screen = ui.Screen(ctx, lambda line: None, tag="guard test")
        app = types.SimpleNamespace(is_button_enabled=True,
                                    to_display_buttons=["a", "b"])
        screen.busy_on(app)
        screen.busy_on(app)          # 待機表示を出したまま、もう一度
        check("busy_on: 重ねても interval は1本", len(clock.intervals) == 1,
              len(clock.intervals))
        screen.busy_off(app, restore=False)
        check("busy_on: 重ねた後の busy_off で押せる状態へ戻る",
              app.is_button_enabled is True, app.is_button_enabled)
        # 解いた直後、前のコマが来る前に出し直す。
        screen.busy_on(app)
        check("busy_on: 出し直しでは interval を足す", len(clock.intervals) == 2,
              len(clock.intervals))
        check("busy_on: 前の世代のコマ送りは外れる",
              clock.intervals[0](0) is False and clock.intervals[1](0) is True)
        screen.busy_off(app, restore=False)
        check("busy_on: 出し直しの後も元の値へ戻る", app.is_button_enabled is True,
              app.is_button_enabled)

    with_fake_kivy(body)


def test_watch_aliases_superseded():
    print("llm.watch_aliases（注入し直されて降りるときも1行残す）")
    lines = []
    state = {"superseded": False}
    ctx = types.SimpleNamespace(
        resolve=lambda target: (None, None, None),     # 別名はまだ生えない
        superseded=lambda: state["superseded"],
        log=lambda msg, level="INFO": lines.append(msg),
        log_exc=lambda msg: lines.append("EXC " + msg))
    old_poll = llm.ALIAS_POLL_SECONDS
    llm.ALIAS_POLL_SECONDS = 0.02
    try:
        watched = llm.watch_aliases(ctx, ["llm_manager:send_request"],
                                    lambda target: None, label="alias test")
        check("生えていない対象は見張りに回る", watched == ["llm_manager:send_request"],
              watched)
        state["superseded"] = True
        deadline = time.monotonic() + 5.0
        while not lines and time.monotonic() < deadline:
            time.sleep(0.01)
        check("降りたことと対象が記録に残る",
              any("alias test" in line and "send_request" in line
                  and "superseded" in line for line in lines), lines)
    finally:
        llm.ALIAS_POLL_SECONDS = old_poll


def main():
    root = tempfile.mkdtemp(prefix="instantale_common_")
    try:
        test_world_store(root)
        test_foreign_store(os.path.join(root, "foreign"))
        test_worker(root)
        test_llm_reading()
        test_game_day()
        test_pressed_entry()
        test_records(root)
        test_enroll()
        test_args()
        test_sounds(root)
        test_widgets()
        test_sys_world_store(root)
        test_gold()
        test_clock_guard(root)
        test_watch_aliases_superseded()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if FAILURES:
        print("\n失敗: " + ", ".join(FAILURES))
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
