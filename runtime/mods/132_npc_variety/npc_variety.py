# -*- coding: utf-8 -*-
"""132_npc_variety: NPC を生成する頼み文に、表から引いた具体の特徴を足す。

ゲームが NPC を作る頼み文は2つ。
単体（`master_ai_npc_generater`。名前・概要1行・ランクだけ）と
町（`create_settlement_detail`。世界の概要1つで住民10人前後を1回で書かせる）。
どちらも人を見分ける材料（髪・瞳・肌・体格）を渡していないので、
モデルは役職の型で埋める（宿屋の主人＝皺と灰色のローブ、鍛冶師＝筋肉質でエプロン。
VERIFICATION_LOG.md §2.82）。

「没個性を避けよ」と言う（`111_` の既定ルールに入っている）だけでは動かず、
「髪色を必ず書け」と要素名を足しても値は同じになる（宿屋の主人の瞳は10回とも「鋭い灰色」）。
効いたのは**値を引いて渡す**ことで、指定は 94〜99% で本文に載り、
役職の型の語は 59% → 15%、「鋭い」は 17% → 3% に落ちた（同 §2.82）。

この MOD はその形を実装する。

  ・人物ごとに、外見9軸・性格6軸・来歴9軸の表から語を引く
  ・軸は順を混ぜ、性格と来歴は一部だけ渡す（全部渡すと書き出しが表の語に寄る）
  ・軸には「付く割合」を持たせられる。目立つ特徴（傷・眼帯・入れ墨）は 25% で、
    残りの人物には付けない（細部は LLM も画像生成も描き分けられず、毎回付けると全員が傷物になる）
  ・鍵=値ではなく句点で繋いだ文で渡し、「見出しや「=」は書かない」と言う
    （鍵=値で渡すと本文に「髪=…」が写る。probe で 14 / 105 人）
  ・町は必須施設の主5人・住民・冒険者を番号で名指しして人数ぶん渡す

仕掛ける場所はローダの `llm.wrap_outgoing`（`111_` と同じ）。
ローカルの3点もクラウドの別名も向こうが包み、こちらは
「この並びをどう書き換えるか」だけを渡す。
見分けは本文の印で行う（`manager_name` はここまで届かない）:

  単体  「【生成するNPC】」の下に `- 名前:` と `- 概要:` がある
        （衛兵・闘技場の敵の頼み文にも「【生成するNPC】」と `- look_description:` はあるが、
        名前と概要は無く「- 強さのランク」だけ。実機で衛兵に種が1回付いてから直した。
        VERIFICATION_LOG.md §2.82）
  町    「【NPCの生成要素】」と `settlement_name:` の両方

表は MOD フォルダの `seeds.default.json`。
同じフォルダに `seeds.json` を置けばそちらが優先される（`120_` の名簿と同じ）。
効くのは新しく生まれる NPC だけ。既に世界に居る NPC は変わらない。
"""

from __future__ import annotations

import io
import json
import os
import random
import threading
import time

from instantale_modloader.llm import wrap_outgoing

LOG_BASENAME = "npc_variety.log"

# ---------------------------------------------------------------- 設定
# mod.json の "settings" と同じ名前・同じ既定値（ローダが apply() の前に書き込む）。
SEED_CHANCE = 100            # 人物ごとに種を付ける割合（%）
LOOK_AXES = 9                # 外見の軸をいくつ渡すか（9で全部）
PERSONALITY_AXES = 3         # 性格の軸をいくつ渡すか（6で全部）
DESCRIPTION_AXES = 3         # 来歴の種をいくつ渡すか（9で全部）
TOWN_RESIDENTS = 4           # 町生成で種を付ける住民の人数
TOWN_ADVENTURERS = 3         # 町生成で種を付ける冒険者の人数
LOG_SEEDS = True             # [SEED] を残すか

# ---------------------------------------------------------------- 表
SEEDS_FILE_NAME = "seeds.json"
DEFAULT_SEEDS_FILE_NAME = "seeds.default.json"
SECTIONS = ("look", "personality", "description")
PHRASE_LIMIT = 60            # 表の1句の上限。壊れた1件はその1件だけ捨てる

# ---------------------------------------------------------------- 印
MARK_SINGLE = "【生成するNPC】"
MARK_SINGLE_NAME = "- 名前:"                    # 衛兵・闘技場の頼み文には無い（ランクだけ）
MARK_SINGLE_SUMMARY = "- 概要:"
#: 衛兵・闘技場の敵の頼み文にある語。見えたら触らない（名前の有無と二重に見る）。
NOT_SINGLE_WORDS = ("衛兵NPC", "闘技場で戦う")
MARK_TOWN = "【NPCの生成要素】"
MARK_TOWN_DATA = "settlement_name:"
HEAD_SINGLE = "【この人物について決まっていること】"
HEAD_TOWN = "【人物について決まっていること】"
FOOT_SINGLE = ("これらを description・personality・look_description の文章に溶かして書くこと。"
               "見出しや「=」の形は書かない。")
FOOT_TOWN = ("次の人物は、それぞれ決まっていることを description・personality・look_description "
             "の文章に溶かして書くこと。見出しや「=」の形は書かない。ここに無い人物は自由でよい。")

#: 町の必須施設。ゲームの頼み文が「必ずそれぞれ一つずつ存在する」と言う5種。
#: 種別で名指しできる（GAME.md §2.28）。
TOWN_OWNERS = (("inn", "宿屋"), ("guild", "ギルド"),
               ("administrative_office", "役所"), ("medical_facility", "医療施設"),
               ("general_store", "雑貨屋"))

RNG = random.Random()


# ================================================================== 表を読む

def seeds_path(mod_dir):
    """手元の表が在ればそれ、無ければ同梱。どちらも無ければ None。"""
    if not mod_dir:
        return None
    user = os.path.join(mod_dir, SEEDS_FILE_NAME)
    if os.path.isfile(user):
        return user
    bundled = os.path.join(mod_dir, DEFAULT_SEEDS_FILE_NAME)
    if os.path.isfile(bundled):
        return bundled
    return None


def usable(value):
    """表の1句として使える文字列か。"""
    if not isinstance(value, str):
        return False
    got = value.strip()
    return bool(got) and len(got) <= PHRASE_LIMIT and not any(
        ord(ch) < 0x20 or ch in "=\n" for ch in got)


def read_seeds(path):
    """表を読む。`{section: [(軸名, [句, ...], 付く割合), ...]}`。読めなければ全部空。

    軸は `["句", ...]` か `{"chance": 25, "rows": ["句", ...], "drops": {"句": ["軸名", ...]}}`。
    `chance` はその軸が人物ごとに付く割合（省くと 100）。
    `drops` は「この句を引いたら、後ろに来る軸のうちこれらは付けない」
    （「髪は短く刈っている」を引いたら「髪型」を省く、のように矛盾する組を避ける。
    節をまたいでもよい: 「まだ子ども」で来歴の「家族」を省く）。
    `not_for` は「この役の人物にはこれらの句を出さない」
    （`{"not_for": {"owner": ["まだ子どもと言ってよい年頃"]}}` で、施設の主に子どもを出さない）。
    句の並びの代わりに `true` を書くと、その役にはその軸ごと付けない
    （単体生成は概要に年齢が書かれているので、年齢の軸を渡すと食い違う）。
    役は `owner`（町の必須施設の主）/ `resident` / `adventurer` / `single`（単体生成）。
    知らない鍵は無視する。軸の名前は表示に使わない（並びの順と `drops` の宛先にだけ使う）。
    句の重複は1度だけ数える。`drops` は `tables["_drops"][section][(軸名, 句)]`、
    `not_for` は `tables["_not_for"][section][軸名][役]` に入る。
    """
    empty = {section: [] for section in SECTIONS}
    if not path:
        return empty
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    tables = {"_drops": {section: {} for section in SECTIONS},
              "_not_for": {section: {} for section in SECTIONS}}
    for section in SECTIONS:
        axes = []
        block = data.get(section)
        if isinstance(block, dict):
            for axis, rows in block.items():
                chance = 100
                drops = {}
                not_for = {}
                if isinstance(rows, dict):
                    chance = rows.get("chance", 100)
                    drops = rows.get("drops") if isinstance(rows.get("drops"), dict) else {}
                    not_for = rows.get("not_for") if isinstance(rows.get("not_for"), dict) else {}
                    rows = rows.get("rows")
                    if not isinstance(chance, (int, float)) or isinstance(chance, bool):
                        chance = 100
                    chance = max(0, min(100, int(chance)))
                if not isinstance(rows, list):
                    continue
                picked, seen = [], set()
                for row in rows:
                    got = row.strip() if isinstance(row, str) else row
                    if usable(got) and got not in seen:
                        seen.add(got)
                        picked.append(got)
                if picked:
                    axes.append((str(axis), picked, chance))
                    for row, targets in drops.items():
                        if row in picked and isinstance(targets, list):
                            tables["_drops"][section][(str(axis), row)] = [
                                str(t) for t in targets if isinstance(t, str)]
                    for role, banned in not_for.items():
                        if banned is True or banned == "*":
                            tables["_not_for"][section].setdefault(str(axis), {})[str(role)] = "*"
                        elif isinstance(banned, list):
                            got = {b for b in banned if isinstance(b, str) and b in picked}
                            if got:
                                tables["_not_for"][section].setdefault(str(axis), {})[str(role)] = got
        tables[section] = axes
    return tables


def table_size(tables):
    """`(軸の数, 句の数)`。空の判定と記録用。"""
    axes = sum(len(tables.get(section) or []) for section in SECTIONS)
    phrases = sum(len(rows) for section in SECTIONS
                  for _axis, rows, _chance in (tables.get(section) or []))
    return axes, phrases


class SeedFile(object):
    """表のファイル。変わっていたら読み直す（ゲームを止めずに編める）。"""

    def __init__(self, mod_dir, report):
        self.mod_dir = mod_dir
        self.report = report
        self.path = None
        self.stamp = None
        self.tables = {section: [] for section in SECTIONS}
        self.lock = threading.Lock()

    def current(self):
        with self.lock:
            self._reload_if_changed()
            return self.tables

    def _reload_if_changed(self):
        path = seeds_path(self.mod_dir)
        try:
            stamp = os.stat(path).st_mtime if path else None
        except OSError:
            stamp = None
        if path == self.path and stamp == self.stamp:
            return
        self.path = path
        self.stamp = stamp
        self.tables = read_seeds(path)
        axes, phrases = table_size(self.tables)
        if not path:
            self.report("[TABLE] 表が無い（{} も {} も無い）。何も足さない".format(
                SEEDS_FILE_NAME, DEFAULT_SEEDS_FILE_NAME))
        elif not axes:
            self.report("[TABLE] {} が読めないか空。何も足さない".format(
                os.path.basename(path)))
        else:
            self.report("[TABLE] {} を読んだ: {}軸 {}句".format(
                os.path.basename(path), axes, phrases))


# ================================================================== 種を組む

def pick_axes(axes, count, rng):
    """軸の並びから `count` 本を順を混ぜて選ぶ。付く割合を持つ軸は先に抽選する。

    割合の抽選を先にするのは、外れた軸のぶん本数が減らないようにするため
    （`count` は「渡す本数の上限」で、割合で外れた軸は最初から無かったことになる）。
    """
    if count <= 0 or not axes:
        return []
    order = [axis for axis in axes
             if axis[2] >= 100 or (axis[2] > 0 and rng.randrange(100) < axis[2])]
    rng.shuffle(order)
    return cluster(order[:count], axes)


def cluster(picked, axes):
    """同じ物を指す軸（名前に「髪」を含む: 髪色・長さ・髪型）は隣り合わせにする。

    順を混ぜたまま散らばると「髪を片側に流している。三十路に見える。濃い茶色の髪。」のように
    読みにくい文になる。最初に出た位置へ表の順で寄せる。
    """
    kin = [axis for axis in picked if "髪" in axis[0]]
    if len(kin) < 2:
        return picked
    rank = {axis[0]: index for index, axis in enumerate(axes)}
    kin.sort(key=lambda axis: rank.get(axis[0], 0))
    first = min(picked.index(axis) for axis in kin)
    rest = [axis for axis in picked if axis not in kin]
    return rest[:first] + kin + rest[first:]


def compose_person(tables, rng, *, look_axes=None, personality_axes=None,
                   description_axes=None, role=None):
    """人物1人ぶんの決まりごとを、句点で繋いだ1本の文にする。空なら ""。

    鍵=値の形にしない（本文に写る）。軸の名前も載せない。
    外見 → 性格 → 来歴の順は固定で、それぞれの中の順だけ混ぜる
    （外見が先に来るほうが look_description に落ちやすい）。
    `drops` は**後ろに来る軸にだけ**効く（髪は表の順に寄せるので長さ → 髪型の順になる）。
    節はまたぐ（外見の「まだ子ども」で来歴の「家族」を省ける）。
    `role` を渡すと、表の `not_for` でその役に出さないとした句を引かない
    （句が全部消えた軸は付けない）。
    """
    counts = (("look", LOOK_AXES if look_axes is None else look_axes),
              ("personality", PERSONALITY_AXES if personality_axes is None else personality_axes),
              ("description", DESCRIPTION_AXES if description_axes is None else description_axes))
    phrases = []
    drops_of = tables.get("_drops") or {}
    not_for = tables.get("_not_for") or {}
    dropped = set()          # 人物1人の間ずっと効く（節をまたぐ。年齢 → 家族など）
    for section, count in counts:
        for axis, rows, _chance in pick_axes(tables.get(section) or [], count, rng):
            if axis in dropped:
                continue
            banned = ((not_for.get(section) or {}).get(axis) or {}).get(role) if role else None
            if banned == "*":
                continue
            if banned:
                rows = [r for r in rows if r not in banned]
                if not rows:
                    continue
            row = rng.choice(rows)
            phrases.append(row)
            dropped.update((drops_of.get(section) or {}).get((axis, row), ()))
    return "。".join(phrases) + ("。" if phrases else "")


def wants_seed(rng, chance=None):
    chance = SEED_CHANCE if chance is None else chance
    if chance <= 0:
        return False
    if chance >= 100:
        return True
    return rng.randrange(100) < chance


def town_roles(residents=None, adventurers=None):
    """町で名指しする人物の呼び方。`[(鍵, 呼び方), ...]`。"""
    residents = TOWN_RESIDENTS if residents is None else residents
    adventurers = TOWN_ADVENTURERS if adventurers is None else adventurers
    roles = [(kind, "{}({})の owner".format(label, kind)) for kind, label in TOWN_OWNERS]
    roles += [("resident{}".format(i), "residents の{}人目".format(i))
              for i in range(1, max(0, residents) + 1)]
    roles += [("adventurer{}".format(i), "adventurers の{}人目".format(i))
              for i in range(1, max(0, adventurers) + 1)]
    return roles


def role_class(key):
    """役の鍵 → 表の `not_for` で使う役の名（owner / resident / adventurer / single）。"""
    if key.startswith("resident"):
        return "resident"
    if key.startswith("adventurer"):
        return "adventurer"
    return "owner"


# ================================================================== 見分けと差し込み

def classify(texts):
    """1回の推論の本文の並びから、対象の種類と user 本文の位置を返す。無ければ None。

    `("single", index)` / `("town", index)`。
    既に種が付いている（同じ本文が2度通った）ときも None。
    """
    joined = "\n".join(texts)
    if HEAD_SINGLE in joined or HEAD_TOWN in joined:
        return None
    if not any(word in joined for word in NOT_SINGLE_WORDS):
        for index, text in enumerate(texts):
            start = text.find(MARK_SINGLE)
            if start < 0:
                continue
            block = text[start:]
            if MARK_SINGLE_NAME in block and MARK_SINGLE_SUMMARY in block:
                return "single", index
    if MARK_TOWN in joined:
        for index, text in enumerate(texts):
            if MARK_TOWN in text and MARK_TOWN_DATA in text:
                return "town", index
    return None


def inject(texts, tables, rng=None, *, chance=None, residents=None, adventurers=None,
           look_axes=None, personality_axes=None, description_axes=None):
    """本文の並びに種を足す。`(新しい並び or None, 付けた種の [(呼び方, 文)])`。

    変えないときは None（ローダの `rewrite` の約束）。
    """
    rng = RNG if rng is None else rng
    found = classify(texts)
    if found is None:
        return None, []
    kind, index = found
    if not any(tables.get(section) for section in SECTIONS):
        return None, []
    compose = lambda role: compose_person(       # noqa: E731
        tables, rng, look_axes=look_axes, personality_axes=personality_axes,
        description_axes=description_axes, role=role)
    seeds = []
    if kind == "single":
        if wants_seed(rng, chance):
            line = compose("single")
            if line:
                seeds.append(("この人物", line))
        if not seeds:
            return None, []
        block = "{}\n{}\n{}\n".format(HEAD_SINGLE, seeds[0][1], FOOT_SINGLE)
    else:
        for key, label in town_roles(residents, adventurers):
            if not wants_seed(rng, chance):
                continue
            line = compose(role_class(key))
            if line:
                seeds.append((label, line))
        if not seeds:
            return None, []
        block = "{}\n{}\n{}\n".format(
            HEAD_TOWN, FOOT_TOWN,
            "\n".join("- {}: {}".format(label, line) for label, line in seeds))
    new_texts = list(texts)
    new_texts[index] = texts[index].rstrip("\n") + "\n" + block
    return new_texts, seeds


# ================================================================== 本体

def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    # 表は MOD のフォルダの中。
    # `ctx.mod_dir` は apply() の外では None になるので、ここで控える。
    mod_dir = ctx.mod_dir
    write = ctx.logger(LOG_BASENAME)
    seeds = SeedFile(mod_dir, write)
    count = {"single": 0, "town": 0}

    def run(texts, site):
        """ローダから、1回の推論で出ていく本文の並びとして呼ばれる。変えなければ None。"""
        tables = seeds.current()
        started = time.monotonic()
        new_texts, made = inject(texts, tables)
        if new_texts is None:
            return None
        kind = "single" if len(made) == 1 and made[0][0] == "この人物" else "town"
        count[kind] += 1
        if LOG_SEEDS:
            write("[SEED] {} {} | {}人 ({:.1f}ms)".format(
                site, kind, len(made), (time.monotonic() - started) * 1000))
            for label, line in made:
                write("    {}: {}".format(label, line))
        return new_texts

    hooks = wrap_outgoing(
        ctx, run, label="npc variety",
        on_arm=lambda target: write("[ARM] 遅れて仕掛けた: {}".format(target)))

    # 注入した時点で、作ったデータで正しさを確かめておく。
    # 実経路はゲームが NPC を作るまで通らない（`111_` と同じ方針）。
    _verify(ctx)

    armed = hooks.armed()
    tables = seeds.current()
    axes, phrases = table_size(tables)
    ctx.log("npc variety: armed on {} | {} | log {}".format(
        ", ".join(armed) if armed else "nothing (targets missing)",
        "{}軸 {}句 from {}".format(axes, phrases, os.path.basename(seeds.path))
        if axes else "no table (nothing will be added)",
        log_path))


# --------------------------------------------------------------------------
# 自己検証
# --------------------------------------------------------------------------
_SAMPLE_TABLES = {
    "look": [("髪", ["赤茶の髪を短く刈っている"], 100), ("瞳", ["鳶色の瞳"], 100),
             ("特徴", ["片目に眼帯"], 0), ("年齢", ["まだ子ども"], 100)],
    "personality": [("口数", ["無口"], 100)],
    "description": [("出自", ["川向こうの漁村の生まれ"], 100)],
    "_not_for": {"look": {"年齢": {"single": {"まだ子ども"}, "owner": {"まだ子ども"}}},
                 "personality": {}, "description": {}},
}
_SAMPLE_SINGLE = ["型: - look_description: 外見", "指示",
                  "【生成するNPC】\n- 名前: 宿屋の主人\n- 概要: 宿を営む\n- 強さのランク: 8\n"]
_SAMPLE_GUARD = ["型: - look_description: 外見", "衛兵NPCをデザインしろ",
                 "【生成するNPC】\n- 強さのランク: 8\n"]
_SAMPLE_TOWN = ["型", "指示", "【NPCの生成要素】\n- 名前\n【生成対象エリアのデータ】\n"
                             "- settlement_name: 風の村\n"]


def _verify(ctx):
    rng = random.Random(1)
    got, made = inject(_SAMPLE_SINGLE, _SAMPLE_TABLES, rng, chance=100)
    if got is None or len(made) != 1 or HEAD_SINGLE not in got[2] \
            or "赤茶の髪を短く刈っている" not in got[2] or "眼帯" in got[2] \
            or "まだ子ども" in got[2] or got[0] != _SAMPLE_SINGLE[0]:
        ctx.log("VERIFY FAILED: single injection {!r}".format(got), level="ERROR")
        return
    again, _made = inject(got, _SAMPLE_TABLES, rng, chance=100)
    if again is not None:
        ctx.log("VERIFY FAILED: seeded text was seeded again", level="ERROR")
        return
    if inject(_SAMPLE_GUARD, _SAMPLE_TABLES, rng, chance=100)[0] is not None:
        ctx.log("VERIFY FAILED: guard prompt was touched", level="ERROR")
        return
    got, made = inject(_SAMPLE_TOWN, _SAMPLE_TABLES, rng, chance=100,
                       residents=2, adventurers=1)
    if got is None or len(made) != len(TOWN_OWNERS) + 3 or HEAD_TOWN not in got[2] \
            or "宿屋(inn)の owner:" not in got[2] \
            or any("まだ子ども" in line for label, line in made if "owner" in label) \
            or not all("まだ子ども" in line for label, line in made if "owner" not in label):
        ctx.log("VERIFY FAILED: town injection {!r}".format(got), level="ERROR")
        return
    if inject(_SAMPLE_TOWN, _SAMPLE_TABLES, rng, chance=0)[0] is not None:
        ctx.log("VERIFY FAILED: chance 0 still injected", level="ERROR")
        return
    ctx.log("npc variety: self-check passed")
