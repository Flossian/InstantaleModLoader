# -*- coding: utf-8 -*-
"""機能追加: 戦闘で**実際に動いた HP** を数字で画面に出す。

このゲームの戦闘は地の文だけで進む。「深手を負わせた」「よろめいた」とは書かれるが、
**何点入ったのかはどこにも出ない**。この mod は1手ごとに HP の増減を測り、
地の文の後ろに数行足す:

    ゴブリンの斥候 に 23 のダメージ（残り HP 11/34）
    テストプレイヤー は 8 のダメージを受けた（残り HP 52/60）

味方が敵に与えたぶんも、敵が味方に与えたぶんも、同じ仕組みで出る。行の頭の記号は
`LINE_PREFIX` で選べる（既定は付けない。候補は環境依存文字を避けてある）。

**とどめの一撃は、場から消えた後に測る。** 倒した敵は報告より先に
`current_enemy_dict` から抜けるので、「今そこに居る者」だけを見ていると
**とどめのダメージだけが丸ごと落ちる**（GAME.md §2.10）。
台帳には HP だけでなく**持ち主そのもの**を控えてあり、鍵が場から消えた回に
その参照から最後の HP を読んで1行出してから台帳を落とす:

    泥濘の亡者 に 5 のダメージ（撃破）

## ダメージの式は読まない。HP の差を測る

`scripts.functions` に `get_instant_damage(attack, defense)` /
`get_base_damage_value(character_attack, weapon_attack)` があり、ここを包めば
「計算された1発の値」は取れる。**それはやらない。**

  * 1手の中で何回呼ばれるか（複数体攻撃・追撃・反撃）が分からない
  * 誰に当たった値なのかが引数から分からない（`attack` と `defense` は数値）
  * 状態異常の継続ダメージや、式を通らない固定ダメージが落ちる

代わりに**戦闘に居る全員の HP を、1手の前後で比べる**。誰が誰にいくつ入れたかを
ゲームの内部語彙で辿らずに済み、経路が増えても（毒・反射・自傷・回復）そのまま
数字が出る。TECH.md §6.2「語彙を推測するくらいなら、語彙を知らずに済む経路を探す」。

## 台帳方式: 入れ子でも二重に出ない

素朴に「前後を測って差を出す」と、外側と内側の両方を包んだときに同じ変化が2回出る。
そこで**最後に見た HP の台帳**を持ち、報告点では必ず

    台帳と今の HP を比べる → 出す → 台帳を今の値で更新する

をする。更新まで含めて1つの操作なので、内側が先に報告すれば外側には差が残らない。
報告点をいくつ足しても二重にならないので、後から測り漏れの場所を足せる。

報告点は3つ（`out/recon/targets.txt` より）:

    BattlePhaseManager.handle_battle_situation(character_key, character_side,
                                               battle_action)   1手ぶん
    BattlePhaseManager.reduce_status_turns_and_log(character)    毒などの継続分
    BattlePhaseManager.check_battle_end(self)                    取りこぼしの掃除

**`handle_battle_situation` の後**に出すのが要点。この中で `calculate_battle_effect`
→ `resolve_battle_effect`（HP を動かす）→ `process_battle_text`（地の文）が回ると
読めるので、その外側で出せば**必ず地の文の後ろ**に付く。内側の
`resolve_battle_effect` を包むと、地の文より先に数字が出る恐れがある。

`check_battle_end` は「戦闘が続くか」を見るためにゲームが節目で呼ぶ。ここでの報告は
台帳に差が無ければ**1行も出さない**ので、普段は何も起きない。想定外の経路で HP が
動いたときだけ、そのぶんが遅れて出る（黙って落とすよりよい）。

## 誰が「敵」で誰が「味方」か

  * 敵  … `app.current_enemy_dict`（GAME.md §2.10。戦闘の実体の有無もここで見る）
  * 味方 … `app.player` と、名簿から引いた同行者（`ui.party_member_ids`）

HP の持ち主が `Character` インスタンスか辞書かは**決めつけない**。どちらでも読める
形にしてある（`read_number`）。最大 HP の属性名は `Character.update_max_hp()` から
`max_hp` と読めるが、これも**当たれば使う**だけで、無ければ残量の括弧を省く
（属性名の推測でゲームを落とさない）。

## 戦闘が変わったら台帳を捨てる

敵の鍵（`current_enemy_dict` のキー）は戦闘をまたいで使い回されうる。前の戦闘の
台帳が残っていると、次の戦闘の1手目で「満タンまで回復した」が出る。そこで

  * `BattleStartManager.start_battle` の後に台帳を捨てて取り直す
  * `current_enemy_dict` の**入れ物そのものが差し替わった**ら捨てる
  * 鍵は同じでも**名前が違えば別人**として扱い、その回は報告しない

の3つで囲ってある。それでも取りこぼしたときに出るのは「1手ぶん数字が出ない」
だけで、ゲームの側には何も起きない。

## 表示しないもの

  * 変化が `MIN_AMOUNT` 未満のもの（既定 1。0 点の行は出さない）
  * `SHOW_INTEGRITY` が OFF なら身体の損耗（`physical_integrity`）。HP とは別の
    数字で、1手で動かない回が多いので既定 OFF

## ゲームの状態は一切書き換えない

読むのは HP だけ、書くのは画面のテキストだけ。セーブにも `world` にも触らない。
"""


from instantale_modloader import ui

LOG_BASENAME = "battle_damage.log"
LOG_TAG = "battle dmg"

# ---------------------------------------------------------------- 何を読むか
# HP の属性名。`Character.__init__` の `current_hp=None` から（実測の署名）。
HP_KEY = "current_hp"

# 最大 HP の属性名の候補。`Character.update_max_hp()` があるので `max_hp` が
# 本命だが、**当たらなければ残量の括弧を省くだけ**にして推測でこけないようにする。
MAX_HP_KEYS = ("max_hp", "current_max_hp", "max_hit_point")

# 身体の損耗（HP とは別の数字。`SHOW_INTEGRITY` が ON のときだけ見る）。
INTEGRITY_KEY = "physical_integrity"
MAX_INTEGRITY_KEY = "max_physical_integrity"

# 名前。台帳の鍵が同じでも、これが違えば別人として扱う。
NAME_KEY = "name"

# ---------------------------------------------------------------- 動作（設定）
# 味方が敵に与えたダメージを出す。
SHOW_DAMAGE_TO_ENEMIES = True

# 敵が自分・味方に与えたダメージを出す。
SHOW_DAMAGE_TO_ALLIES = True

# HP が増えたとき（回復）も出す。
SHOW_HEALING = True

# 残りの HP を括弧で添える。最大 HP が読めないときは現在値だけになる。
SHOW_REMAINING_HP = True

# 身体の損耗（`physical_integrity`）の増減も出す。HP とは別の数字なので既定 OFF。
SHOW_INTEGRITY = False

# これ未満の増減は出さない。0 にすると 0 点の行まで出る。
MIN_AMOUNT = 1

# ---------------------------------------------------------------- 文言
# 各行の頭に付ける記号。**既定は付けない。**
#
# 候補は**環境依存文字を入れない** ― cp932 に在り、かつ先頭バイトが NEC 特殊
# （0x87）・NEC 選定 IBM 拡張（0xED-0xEE）・IBM 拡張（0xFA-0xFC）でない字だけを
# 選んである（＝ JIS X 0208 の範囲）。最初の版で使っていた `▶`（U+25B6）は
# **cp932 に無く**、フォントや端末によっては出ない・化ける。実測:
#
#     '▶' U+25B6 → cp932 に無い（環境依存）      '●' U+25CF → JIS X 0208
#     '»'  U+00BB → cp932 に無い（環境依存）      '※' U+203B → JIS X 0208
#     '①' U+2460 → NEC 特殊（環境依存）          '・' U+30FB → JIS X 0208
#
# 候補を増やすときは、その字で上の判定を通してから足すこと。
LINE_PREFIX = "なし"

# `LINE_PREFIX` がこの値なら何も付けない。**空文字を選択肢にできない**ので名前で
# 表している ― GUI は空欄を「未指定」（`None`）として扱い、`allow_null` でない
# 設定では弾かれる（`tools/gui.py` の `_ok`）。一覧は読み取り専用なので、空文字を
# 選べたとしても一度選んだら戻せない。
NO_PREFIX = "なし"

# 記号と本文の間に挟むもの。**選択肢の側に空白を持たせない** ― JSON でも GUI の
# 一覧でも見えず、消えたことに気付けないため。
PREFIX_SEPARATOR = " "

DAMAGE_TO_ENEMY_TEXT = "{name} に {amount} のダメージ"
DAMAGE_TO_ALLY_TEXT = "{name} は {amount} のダメージを受けた"
HEAL_TEXT = "{name} は {amount} 回復した"
INTEGRITY_DAMAGE_TEXT = "{name} の負傷が {amount} 深くなった"
INTEGRITY_HEAL_TEXT = "{name} の負傷が {amount} 癒えた"
REMAINING_TEXT = "（残り HP {hp}/{max}）"
REMAINING_TEXT_NO_MAX = "（残り HP {hp}）"

# とどめの一撃に添える。残量（0/804）を出しても読み手に何も足さないため。
DEFEATED_TEXT = "（撃破）"

# 1回の報告で出す行数の上限。全体攻撃で敵が並んでも画面を埋めないため。
MAX_LINES = 12

# ---------------------------------------------------------------- 台帳の鍵
ENEMY_SIDE = "enemy"
ALLY_SIDE = "ally"


def line_prefix():
    """各行の頭に付ける文字列。既定（`なし`）では空。

    設定は `apply()` の直前にモジュールの定数へ書き込まれるので、**呼ばれた時点の
    `LINE_PREFIX` を読む**（値を握り込まない）。
    """
    if not LINE_PREFIX or LINE_PREFIX == NO_PREFIX:
        return ""
    return LINE_PREFIX + PREFIX_SEPARATOR


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    # 台帳。{(side, key): {"name":…, "hp":…, "max":…, "integrity":…}}
    # `enemies` は `current_enemy_dict` の入れ物の id（差し替わったら捨てる）。
    state = {"ledger": {}, "enemies": None, "reported": 0, "no_max_said": False}

    write = ctx.logger(LOG_BASENAME, tag=LOG_TAG + ":")

    # ------------------------------------------------------------ 読み取り
    def read_value(holder, key):
        """属性でも辞書でも同じように読む。**持ち主の型を決めつけない。**"""
        if holder is None:
            return None
        if isinstance(holder, dict):
            return holder.get(key)
        return getattr(holder, key, None)

    def read_number(holder, key):
        """数値として読めるときだけ返す。`bool` は数値として扱わない。"""
        value = read_value(holder, key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    def read_max(holder, keys):
        for key in keys:
            value = read_number(holder, key)
            if value is not None:
                return value
        return None

    def name_of(holder, fallback):
        name = read_value(holder, NAME_KEY)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return str(fallback)

    # ------------------------------------------------------------ 戦闘の面々
    def enemy_dict(app):
        found = getattr(app, "current_enemy_dict", None)
        return found if isinstance(found, dict) else None

    def combatants(app):
        """`[(side, key, name, 持ち主)]`。読み取りだけで、順序は敵→味方。

        敵は `current_enemy_dict`（GAME.md §2.10）、味方は `app.player` と名簿の
        同行者。どちらも引けなければその側は空になる。片方しか読めなくても
        読めたほうは出す。
        """
        found = []
        enemies = enemy_dict(app)
        if enemies:
            for key, holder in list(enemies.items()):
                found.append((ENEMY_SIDE, str(key), name_of(holder, key), holder))

        player = getattr(app, "player", None)
        if player is not None:
            found.append((ALLY_SIDE, ui.PLAYER_ID, name_of(player, ui.PLAYER_ID), player))
        try:
            member_ids = ui.party_member_ids(app)
        except Exception:
            ctx.log_exc("battle dmg: cannot read the party")
            member_ids = []
        for member_id in member_ids:
            member = ui.character_of(app, member_id)
            if member is None:
                continue
            found.append((ALLY_SIDE, str(member_id), name_of(member, member_id), member))
        return found

    def snapshot(holder):
        return {
            "hp": read_number(holder, HP_KEY),
            "max": read_max(holder, MAX_HP_KEYS),
            "integrity": read_number(holder, INTEGRITY_KEY),
            "max_integrity": read_number(holder, MAX_INTEGRITY_KEY),
            # **持ち主そのものを控える。** 倒した敵は報告より先に
            # `current_enemy_dict` から抜けるので、これが無いと**とどめの
            # 一撃だけ**が丸ごと落ちる（GAME.md §2.10）。
            # 場から消えた後でも、この参照から最後の HP を読める。
            "holder": holder,
        }

    # ------------------------------------------------------------ 台帳
    def forget(why):
        if state["ledger"]:
            write("ledger cleared ({}; had {} combatant(s))".format(
                why, len(state["ledger"])))
        state["ledger"] = {}
        state["enemies"] = None

    def check_battle_identity(app):
        """`current_enemy_dict` が別の入れ物に差し替わっていたら台帳を捨てる。

        戦闘が変わったのに前の台帳が残っていると、次の1手目で「満タンまで
        回復した」が出る。鍵（敵のキー）は戦闘をまたいで使い回されうるため。
        """
        enemies = enemy_dict(app)
        marker = id(enemies) if enemies is not None else None
        if marker is None:
            return
        if state["enemies"] is not None and state["enemies"] != marker:
            forget("a different current_enemy_dict appeared")
        state["enemies"] = marker

    def seed(app):
        """台帳に居ない者を、**報告せずに**記録する。

        1手の前にこれを通しておかないと、初回の変化が「新顔なので報告しない」に
        飲まれて消える。何度呼んでも既に居る者には触らない。
        """
        if app is None:
            return
        check_battle_identity(app)
        ledger = state["ledger"]
        for side, key, name, holder in combatants(app):
            entry = ledger.get((side, key))
            if entry is None or entry.get("name") != name:
                record = snapshot(holder)
                record["name"] = name
                ledger[(side, key)] = record

    # ------------------------------------------------------------ 差を出す
    def wanted(side, delta):
        """その増減を出す設定になっているか。`delta` は減ったぶんが正。"""
        if delta > 0:
            return SHOW_DAMAGE_TO_ENEMIES if side == ENEMY_SIDE else SHOW_DAMAGE_TO_ALLIES
        return SHOW_HEALING

    def amount_text(value):
        """整数は整数のまま、端数があるときだけ小数1桁で出す。"""
        if isinstance(value, float) and abs(value - round(value)) >= 0.05:
            return "{:.1f}".format(value)
        return str(int(round(value)))

    def remaining_text(record, gone=False, side=ALLY_SIDE):
        """残量の括弧。**場から消えた相手には残量を出さない。**

        倒れて `current_enemy_dict` から抜けた敵に「（残り HP 0/804）」と付けても
        読み手には何も足さない。HP が尽きているなら「（撃破）」、尽きていない
        （逃げたなど）なら何も付けない。見えている事実だけを書く。
        """
        if gone:
            if side == ENEMY_SIDE and record["hp"] is not None and record["hp"] <= 0:
                return DEFEATED_TEXT
            return ""
        if not SHOW_REMAINING_HP or record["hp"] is None:
            return ""
        if record["max"] is None:
            if not state["no_max_said"]:
                state["no_max_said"] = True
                write("note: no max HP attribute found (tried {}); showing the "
                      "current value only".format(", ".join(MAX_HP_KEYS)))
            return REMAINING_TEXT_NO_MAX.format(hp=amount_text(record["hp"]))
        return REMAINING_TEXT.format(hp=amount_text(record["hp"]),
                                     max=amount_text(record["max"]))

    def hp_line(side, name, delta, record, gone=False):
        if delta > 0:
            template = (DAMAGE_TO_ENEMY_TEXT if side == ENEMY_SIDE
                        else DAMAGE_TO_ALLY_TEXT)
        else:
            template = HEAL_TEXT
        return (line_prefix()
                + template.format(name=name, amount=amount_text(abs(delta)))
                + remaining_text(record, gone, side))

    def hp_change(side, name, before, record, gone=False):
        """HP の増減1件ぶんの `(画面の行, 記録の行)`。出さないときは `(None, None)`。

        場に居る相手も消えた相手も同じ規則で扱う（下限も設定も同じ）。
        """
        if before["hp"] is None or record["hp"] is None:
            return None, None
        delta = before["hp"] - record["hp"]
        if delta == 0 or abs(delta) < MIN_AMOUNT or not wanted(side, delta):
            return None, None
        return (hp_line(side, name, delta, record, gone),
                "{} {} hp {} -> {}{}".format(side, name, before["hp"], record["hp"],
                                             " (left the field)" if gone else ""))

    def integrity_line(name, delta):
        template = INTEGRITY_DAMAGE_TEXT if delta > 0 else INTEGRITY_HEAL_TEXT
        return line_prefix() + template.format(name=name,
                                               amount=amount_text(abs(delta)))

    def diff_lines(app):
        """台帳と今を比べて出す行を作り、**台帳を今の値へ進める**。

        比べると同時に更新するのがこの mod の要（入れ子の報告点で二重に出さない）。
        """
        ledger = state["ledger"]
        lines, logged = [], []
        present = combatants(app)
        for side, key, name, holder in present:
            record = snapshot(holder)
            record["name"] = name
            before = ledger.get((side, key))
            ledger[(side, key)] = record
            if before is None or before.get("name") != name:
                continue                      # 新顔。この回は比べる相手が無い
            line, logline = hp_change(side, name, before, record)
            if line is not None:
                lines.append(line)
                logged.append(logline)
            if SHOW_INTEGRITY and before["integrity"] is not None \
                    and record["integrity"] is not None:
                delta = before["integrity"] - record["integrity"]
                if abs(delta) >= MIN_AMOUNT and delta != 0:
                    lines.append(integrity_line(name, delta))
                    logged.append("{} {} integrity {} -> {}".format(
                        side, name, before["integrity"], record["integrity"]))

        # 場から消えた者（倒れて `current_enemy_dict` から抜けた敵など）。
        # **控えてある持ち主から最後の変化を測ってから**落とす ― ここを黙って
        # 捨てると、とどめの一撃だけが出なくなる。
        alive = set((side, key) for side, key, _n, _h in present)
        for key in [k for k in ledger if k not in alive]:
            before = ledger.pop(key)
            holder = before.get("holder")
            if holder is None:
                continue
            side = key[0]
            record = snapshot(holder)
            line, logline = hp_change(side, before.get("name"), before, record,
                                      gone=True)
            if line is not None:
                lines.append(line)
                logged.append(logline)
        return lines, logged

    def report(app, why):
        """1回ぶんの報告。**差が無ければ画面には何も出さない。**"""
        if app is None:
            return
        check_battle_identity(app)
        lines, logged = diff_lines(app)
        if not lines:
            return
        dropped = 0
        if len(lines) > MAX_LINES:
            dropped = len(lines) - MAX_LINES
            lines = lines[:MAX_LINES]
        state["reported"] += 1
        write("{}: {}{}".format(why, "; ".join(logged),
                                " (+{} line(s) not shown)".format(dropped) if dropped else ""))
        try:
            app.add_text("\n".join(lines))
        except Exception:
            ctx.log_exc("battle dmg: add_text failed")

    # ================================================================ フック
    # 1手ぶん。**元の処理をすべて通してから**出すので、ゲームの地の文の後ろに付く
    # （この中で HP が動き、地の文も出る）。
    @ctx.wrap("__main__:BattlePhaseManager.handle_battle_situation",
              required=False, safe=True)
    def handle_battle_situation(orig, self, character_key=None, character_side=None,
                                battle_action=None, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        try:
            seed(app)
        except Exception:
            ctx.log_exc("battle dmg: cannot read the combatants before the action")
        result = orig(self, character_key, character_side, battle_action,
                      *args, **kwargs)
        report(app, "action by {!r} ({})".format(character_key, character_side))
        return result

    # 毒などの継続分。1人ずつ呼ばれるが、台帳が差を吸うので何度出しても重ならない。
    @ctx.wrap("__main__:BattlePhaseManager.reduce_status_turns_and_log",
              required=False, safe=True)
    def reduce_status_turns_and_log(orig, self, character=None, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        try:
            seed(app)
        except Exception:
            ctx.log_exc("battle dmg: cannot read the combatants before the status turn")
        result = orig(self, character, *args, **kwargs)
        report(app, "status effects")
        return result

    # 取りこぼしの掃除。差が無ければ何も出さないので、普段はただ通り抜ける。
    @ctx.wrap("__main__:BattlePhaseManager.check_battle_end",
              required=False, safe=True)
    def check_battle_end(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        result = orig(self, *args, **kwargs)
        report(app, "battle end check")
        return result

    # 戦闘が始まったら台帳を捨てて取り直す（前の戦闘の残骸で誤報を出さない）。
    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        app = getattr(self, "app", None) or ui.find_app()
        forget("a new battle started")
        try:
            seed(app)
            write("battle start: {} combatant(s) on the ledger".format(
                len(state["ledger"])))
        except Exception:
            ctx.log_exc("battle dmg: cannot read the combatants at the battle start")
        return result

    ctx.log("battle dmg: log -> {} (enemies={} allies={} heal={} remaining={} "
            "integrity={} min={})".format(
                log_path, SHOW_DAMAGE_TO_ENEMIES, SHOW_DAMAGE_TO_ALLIES,
                SHOW_HEALING, SHOW_REMAINING_HP, SHOW_INTEGRITY, MIN_AMOUNT))
