# -*- coding: utf-8 -*-
"""機能追加: 訓練で得た経験値を、**同行中の仲間にも**入れる。

宿屋で月日を訓練に充てるとプレイヤーは経験値を得てレベルが上がるが、隣で同じ
時間を過ごしている仲間は何も変わらない。この mod は**プレイヤーが訓練で経験値を
得た瞬間に、同じぶんを同行者にも入れてレベルアップまで通す**。

## 経験値の計算式は読まない。プレイヤーへの支給を横取りする

`Character` は経験値まわりを自分で持っている（実測。GAME.md §2.17）:

    Character.gain_exp(exp)     経験値を足す
    Character.check_levelup()   上がるかどうか
    Character.levelup()         上げる（HP・能力値の更新まで持っている）

訓練で何点入るのかは `scripts.functions.get_training_experience_point()` /
`get_days_elapsed_experience_point()` が決めているが、**こちらでその式を再現する
必要は無い。** `gain_exp` を包み、**プレイヤーに入った点数をそのまま仲間に渡す**。
式・宿の等級・月数・才能の扱いが将来変わっても、支給される点数を写すだけなので
ずれない（TECH.md §6.2「語彙を推測するくらいなら、語彙を知らずに済む経路を探す」）。

レベルアップも自分では計算しない。`gain_exp` が内部で上げてしまうビルドと、
呼び出し元が `check_levelup` → `levelup` を回すビルドの**どちらでも**辻褄が合うよう、
**レベルが動いていなければ `check_levelup` を聞いてから `levelup`** を呼ぶ形にして
ある。上がらなければ何も起きない。

## 「訓練の中か」は自分の包みで印を立てる（戦闘の経験値には触らない）

`gain_exp` は戦闘でも通る。**訓練の場面だけ**に効かせたいので、訓練マネージャの
`execute` を包み、その中だけ**スレッドごとの印**を立てる:

    VacationTrainManager                宿屋で月日を訓練に充てる（`quality` は宿の等級）
    TrainingStartManager                施設の主に年月と代金を払って教わる
    TrainingPhaseManager                その訓練の各段（技能の習得・強化）
    VacationRestManager                 宿屋での休養（**既定では対象外**）

**宿屋の訓練（`VacationTrainManager`）が本命**だが、施設での訓練も同じ「訓練」なので
既定で両方入れてある。どちらがどの画面なのかは設定で切れる。休養は「訓練した際に」
という依頼の趣旨から外れるので既定オフ。

印がスレッドごとなのは、訓練が別スレッドで走る（`execute` は `threading.run` の上）
一方で、判定するのは同じスレッドで同期的に呼ばれる `gain_exp` だから。別スレッドの
戦闘の支給と混ざらない。

### ここで `frames.MethodWatch` を使うと**必ず誤爆する**

`304_` のようにスタックを見る手も考えたが、この mod は**見張りたい `execute` を
自分で包む**ので成立しない。`MethodWatch` は最初に呼ばれた時点で
`getattr(cls, "execute").__code__` を表にする。そのときそこに載っているのは
**ローダのラッパのコードオブジェクト**で、これは `patch.py` の全パッチが共有している
（ラッパは関数として別物でも `__code__` は同一）。つまり「包まれた関数が1つでも
スタックに載っていれば訓練の中」になり、戦闘の経験値まで写してしまう。
`frames.MethodWatch` の docstring にも同じ注意を書いた。

## 出すのは「プレイヤーの獲得経験値」の後

訓練1回の並びはこうなっている（GAME.md §2.17）:

    add_text('テストプレイヤーはしばらくの期間を訓練に費やした...')
    player.gain_exp(686852)              ← こちらが割り込む位置
    add_text('156の経験値を得た。')        ← プレイヤーの獲得経験値（表示は156）

**`gain_exp` の中で出すと、プレイヤーの獲得経験値より先に仲間の話が出る。** そこで
文言はいったん溜め、**ゲームが次の行を出した後**に流す（`add_text` を包む）。
「経験値を得た」という文面では見分けない。表示の言い回しに寄りかからずに済み、
言語が変わっても順序は変わらないため。1行も出さずに訓練が終わるビルドのために、
セッションの終わりで必ず流す受け皿を置いてある。

**支給の点数と表示の数字は別物**（686852 を写して、画面には 156 と出る）。だから
こちらの文言に数値は入れない。`calculate_current_gained_exp_on_display()` の
換算を再現することになるため。

## ゲームが自分で仲間に配っていたら、その相手には渡さない

同じ訓練の中でゲーム自身が仲間の `gain_exp` を呼んだら、その id を控えて**以後
こちらからは渡さない**（二重取りになる）。将来ゲーム側が仲間にも配るようになったら、
この mod は黙って何もしなくなる。

## 見えなかったときに黙って終わらない

`gain_exp` を通らない支給（`experience_point` を直接書く経路）があると、写すものが
無いまま終わる。それを黙って見過ごさないよう、訓練の `execute` を包んで
**前後のレベルと経験値を必ず1行残す**。写せなかったのに数値が動いていたら `WARN` で
書くので、`out/party_train_exp.log` を見れば「効いていない」と「支給が無かった」が
区別できる。

## セーブはしない

仲間の経験値は `world.characters` の上なので、ゲームが次に保存した時点で一緒に
残る。訓練は月日が進む処理（＝ゲーム自身が保存する場面）なので、こちらから
`save_game()` を呼んで割り込むことはしない。
"""

import datetime
import threading

from instantale_modloader import frames, ui

LOG_BASENAME = "party_train_exp.log"
LOG_TAG = "train exp"

# ---------------------------------------------------------------- 何を捕まえるか
# 宿屋で月日を訓練に充てる経路。**これが本命**（`__init__(self, app, months,
# quality)` / `change_background_image_to_inn_room(quality)` から宿屋と判断）。
INN_TRAINING_MANAGERS = ("VacationTrainManager",)

# 施設の主に年月と代金を払って教わる経路（`__init__(self, app, training_years,
# training_price)`）と、その各段。
FACILITY_TRAINING_MANAGERS = ("TrainingStartManager", "TrainingPhaseManager")

# 宿屋での休養。訓練ではないので既定では対象にしない。
REST_MANAGERS = ("VacationRestManager",)

# 印を立てる入口。マネージャは `process_choice` から `execute` で起こされる
# （GAME.md §2.2）ので、ここを包めば訓練1回がまるごと挟まる。
SESSION_METHOD = "execute"

# ---------------------------------------------------------------- 動作（設定）
# 宿屋の訓練で仲間にも経験値を入れるか。
SHARE_INN_TRAINING = True

# 施設での訓練（`TrainingStartManager` / `TrainingPhaseManager`）でも入れるか。
SHARE_FACILITY_TRAINING = True

# 宿屋での休養でも入れるか。**訓練ではない**ので既定オフ。
SHARE_REST = False

# 仲間に渡す割合。1.0 でプレイヤーと同じ点数。0.5 なら半分。
# 0 より大きい支給が 0 点に丸まるときは 1 点にする（何も起きないより分かりやすい）。
SHARE_RATIO = 1.0

# 仲間がレベルアップしたら画面に出すか。
ANNOUNCE_LEVELUP = True

# 経験値が入ったことも画面に出すか（レベルが上がらなくても1行出る）。
# **既定 ON。** 高レベルでは1回の訓練ではレベルが上がらないので、これが無いと
# 画面上は何も起きていないように見える。
ANNOUNCE_GAIN = True

# ---------------------------------------------------------------- 文言
LEVELUP_TEXT = "{name}はレベル{level}になった。"
GAIN_TEXT = "{names}も同じ月日を鍛錬に充て、経験を積んだ。"
NAME_SEPARATOR = "、"

# ---------------------------------------------------------------- 上限
# 1回の支給で回すレベルアップの上限（`check_levelup` が下がらないビルドでの保険）。
MAX_LEVELUPS = 20

# 訓練の外での支給（＝戦闘など）を記録する本数の上限。**触らないが、経験値の
# 経路がどこを通っているかはこれで分かる。** 0 にすると記録しない。
TRACE_OTHER_GAINS = 20


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    # 訓練は別スレッドで走る（`execute` は `threading.run` の上）。印と控えは
    # **スレッドごと**に持つ ― プロセス全体で共有すると、訓練中に別スレッドで
    # 起きた戦闘の経験値まで「訓練の中」に見えてしまう。
    local = threading.local()

    state = {
        "mirrored": 0,       # 写した支給の本数（session の報告に使う）
        "traced": 0,         # 訓練の外での支給を記録した本数
        "no_party_said": 0,  # 仲間が居ないときのログ（1セッション1回に絞る）
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}: {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"),
                    LOG_TAG, text))
        except Exception:
            ctx.log_exc("train exp: write failed")

    # -------------------------------------------------- 訓練の中かどうかを見る
    def shared_managers():
        """設定で選ばれた「訓練」のマネージャ。ここだけが写す対象。"""
        names = []
        if SHARE_INN_TRAINING:
            names += list(INN_TRAINING_MANAGERS)
        if SHARE_FACILITY_TRAINING:
            names += list(FACILITY_TRAINING_MANAGERS)
        if SHARE_REST:
            names += list(REST_MANAGERS)
        return names

    def in_training():
        """写す対象の訓練の中なら `'VacationTrainManager.execute'`、外なら None。"""
        return getattr(local, "sharing", None)

    # ------------------------------------------------------------ 読み取り
    def level_of(character):
        value = getattr(character, "experience_level", None)
        return value if isinstance(value, (int, float)) else None

    def point_of(character):
        value = getattr(character, "experience_point", None)
        return value if isinstance(value, (int, float)) else None

    def is_player(app, character):
        """その Character がプレイヤーか。**同一性を先に見る。**"""
        player = getattr(app, "player", None)
        if player is not None and character is player:
            return True
        return bool(getattr(character, "is_player", False))

    def share_amount(exp):
        """仲間に渡す点数。整数の支給は整数のまま渡す。"""
        if SHARE_RATIO <= 0:
            return 0
        value = exp * SHARE_RATIO
        if isinstance(exp, int) and not isinstance(exp, bool):
            value = int(round(value))
            if value <= 0:
                # 端数で消えるくらいなら1点入れる（0 だと何も起きず、設定が
                # 効いていないのと区別が付かない）。
                value = 1
        return value

    def given_ids():
        """このスレッドで「ゲーム自身が配った」と分かっている仲間の id。"""
        found = getattr(local, "given", None)
        if found is None:
            found = set()
            local.given = found
        return found

    # ------------------------------------------------------------ 写す
    def mirror(app, exp, source):
        """プレイヤーに入った点数を、同行者それぞれに入れる。"""
        member_ids = ui.party_member_ids(app)
        if not member_ids:
            if state["no_party_said"] < 1:
                state["no_party_said"] += 1
                write("{}: player gained {} exp but nobody is travelling with them "
                      "({})".format(source, exp, ui.describe_stores(app)))
            return
        share = share_amount(exp)
        if share <= 0:
            write("{}: SHARE_RATIO={} turns {} exp into nothing; not sharing"
                  .format(source, SHARE_RATIO, exp))
            return

        local.mirroring = True
        try:
            leveled, gained = [], []
            for member_id in member_ids:
                if member_id in given_ids():
                    write("{}: the game already gave exp to {!r} itself; skipping"
                          .format(source, ui.character_name(app, member_id)))
                    continue
                member = ui.character_of(app, member_id)
                if member is None:
                    write("WARN {}: cannot find character {!r} in world.characters; "
                          "not sharing with them".format(source, member_id))
                    continue
                name = ui.character_name(app, member_id)
                before_level, before_point = level_of(member), point_of(member)
                try:
                    member.gain_exp(share)
                except Exception:
                    ctx.log_exc("train exp: gain_exp({!r}) failed for {!r}".format(
                        share, member_id))
                    continue
                levels = level_up(member, before_level)
                after_level, after_point = level_of(member), point_of(member)
                write("{}: {!r} +{} exp (lvl {} -> {}, point {} -> {})".format(
                    source, name, share, before_level, after_level,
                    before_point, after_point))
                gained.append(name)
                if levels or (before_level is not None and after_level is not None
                              and after_level > before_level):
                    leveled.append((name, after_level))
            state["mirrored"] += 1
            queue_messages(gained, leveled)
        finally:
            local.mirroring = False

    def level_up(member, before_level):
        """上がるぶんだけレベルを上げる。**上がらなければ何もしない。**

        `gain_exp` が内部で上げてしまうビルドでは `check_levelup` が False を返す
        ので、ここは素通りになる（二重に上げない）。
        """
        gained = []
        for _ in range(MAX_LEVELUPS):
            try:
                if not member.check_levelup():
                    break
            except Exception:
                ctx.log_exc("train exp: check_levelup failed")
                break
            try:
                member.levelup()
            except Exception:
                ctx.log_exc("train exp: levelup failed")
                break
            level = level_of(member)
            gained.append(level)
            if level is not None and before_level is not None and level <= before_level:
                # 上げたのに数値が動いていない＝この先も止まらない。打ち切る。
                write("WARN levelup did not raise experience_level ({}); stopping"
                      .format(level))
                break
            before_level = level
        return gained

    # ------------------------------------------------------------ 画面に出す
    # **その場では出さない。** 訓練1回の並びはこうなっている（GAME.md §2.17）:
    #
    #     add_text('…はしばらくの期間を訓練に費やした...')
    #     player.gain_exp(N)                     ← こちらが割り込む位置
    #     add_text('156の経験値を得た。')          ← プレイヤーの獲得経験値
    #
    # ここで出すと**プレイヤーの獲得経験値より先**に仲間の話が出てしまう。文言は
    # 溜めておき、**ゲームが次の行を出した後**に流す（`add_text` を包む）。
    # 「経験値を得た」という文面では見分けない ― 表示の言い回しに寄りかからずに
    # 済むし、言語が変わっても順序は変わらないため。
    def queue_messages(gained, leveled):
        """出す文言を溜める（この時点では画面に出さない）。"""
        messages = []
        if ANNOUNCE_GAIN and gained:
            messages.append(GAIN_TEXT.format(names=NAME_SEPARATOR.join(gained)))
        if ANNOUNCE_LEVELUP:
            for name, level in leveled:
                messages.append(LEVELUP_TEXT.format(name=name, level=level))
        if not messages:
            return
        pending = getattr(local, "pending", None)
        if pending is None:
            pending = []
            local.pending = pending
        pending.extend(messages)

    def flush(app, why):
        """溜めた文言を流す。**流している間は自分の add_text で再入しない。**"""
        messages = getattr(local, "pending", None)
        if not messages or app is None:
            return
        local.pending = []
        local.flushing = True
        try:
            for text in messages:
                app.add_text(text)
            write("said {} line(s) {}".format(len(messages), why))
        except Exception:
            ctx.log_exc("train exp: add_text failed")
        finally:
            local.flushing = False

    def trace_other_gain(app, character, exp):
        """訓練の外での支給を、上限つきで記録する（**何もしない**）。"""
        if TRACE_OTHER_GAINS <= 0 or state["traced"] >= TRACE_OTHER_GAINS:
            return
        state["traced"] += 1
        write("(not training) {} gained {} exp from {}".format(
            frames.describe_instance(character), exp, frames.caller(3)))

    # ================================================================ フック
    @ctx.wrap("scripts.characters:Character.gain_exp", required=False)
    def gain_exp(orig, self, exp=0, *args, **kwargs):
        """プレイヤーが訓練で経験値を得たら、同じぶんを同行者にも入れる。

        **元の処理を先に通す**（プレイヤーの支給はゲームの仕事のまま）。写すのは
        その後で、こちらが失敗してもプレイヤー側の結果は返る。
        """
        result = orig(self, exp, *args, **kwargs)
        try:
            if getattr(local, "mirroring", False):
                return result          # こちらが仲間に入れている最中
            if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= 0:
                return result
            app = ui.find_app()
            if app is None:
                return result
            source = in_training()
            if not is_player(app, self):
                if source is not None:
                    # ゲーム自身が仲間に配った。二重取りを避けるため控える。
                    member_id = ui.element_id(self)
                    given_ids().add(member_id)
                    write("{}: the game gave {} exp to {!r} itself".format(
                        source, exp, ui.character_name(app, member_id)))
                return result
            if source is None:
                trace_other_gain(app, self, exp)
                return result
            mirror(app, exp, source)
        except Exception:
            ctx.log_exc("train exp: sharing the training exp failed")
        return result

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context=None, *args, **kwargs):
        """ゲームが1行出したら、溜めてある仲間の話をその**後ろ**に流す。

        溜まっているときしか何もしないので、普段の描画には触らない。
        """
        result = orig(self, context, *args, **kwargs)
        try:
            if getattr(local, "pending", None) and not getattr(local, "flushing", False):
                flush(self, "after the game's own line")
        except Exception:
            ctx.log_exc("train exp: cannot say the companions' lines")
        return result

    # -------------------------------------------------- 訓練1回ぶんの記録
    # `execute` を包んで、前後のレベルと経験値を必ず1行残す。**写せなかったのに
    # 数値が動いていたら WARN** ― 「mod が効いていない」と「そもそも支給が
    # 無かった」を、ログだけで区別できるようにするため。
    def watch_session(cls_name):
        label = "{}.{}".format(cls_name, SESSION_METHOD)
        shared = cls_name in shared_managers()

        @ctx.wrap("__main__:{}.{}".format(cls_name, SESSION_METHOD), required=False)
        def execute(orig, self, *args, **kwargs):
            app = ui.find_app()
            player = getattr(app, "player", None) if app is not None else None
            before = (level_of(player), point_of(player)) if player is not None else None
            mirrored_before = state["mirrored"]
            # 入れ子（`TrainingStartManager` から `TrainingPhaseManager`）でも
            # 辻褄が合うよう、前の印は控えて必ず戻す。
            outer_sharing = getattr(local, "sharing", None)
            outer_given = getattr(local, "given", None)
            local.sharing = label if shared else outer_sharing
            local.given = set() if outer_given is None else outer_given
            state["no_party_said"] = 0
            try:
                return orig(self, *args, **kwargs)
            finally:
                local.sharing = outer_sharing
                local.given = outer_given
                try:
                    # ゲームがこの後1行も出さなかったときの受け皿。**訓練が
                    # 終わってもまだ溜まっているなら、ここで必ず流す**
                    # （出さずに終わると、次の訓練の途中で混ざる）。
                    flush(app, "at the end of the training")
                except Exception:
                    ctx.log_exc("train exp: cannot say the companions' lines")
                try:
                    report(app, label, shared, before, mirrored_before)
                except Exception:
                    ctx.log_exc("train exp: cannot report the training session")

    def report(app, label, shared, before, mirrored_before):
        player = getattr(app, "player", None) if app is not None else None
        after = (level_of(player), point_of(player)) if player is not None else None
        mirrored = state["mirrored"] - mirrored_before
        members = ui.party_member_ids(app) if app is not None else []
        write("{} done: player lvl/point {} -> {}; shared {} gain(s) with "
              "{} companion(s){}".format(label, before, after, mirrored,
                                         len(members),
                                         "" if shared else " (not a sharing target)"))
        if shared and mirrored == 0 and members and before is not None \
                and after is not None and before != after:
            # 支給はあったのに写せていない＝`gain_exp` を通らない経路がある。
            # 黙って終わらせず、次に何を見るべきかまで書く。
            write("WARN the player's exp changed but no Character.gain_exp call was "
                  "seen inside {}; nothing was shared. The game may be writing "
                  "experience_point directly — report this line with the numbers "
                  "above.".format(label))

    for name in (INN_TRAINING_MANAGERS + FACILITY_TRAINING_MANAGERS + REST_MANAGERS):
        watch_session(name)

    ctx.log("train exp: log -> {} (sharing: inn={} facility={} rest={} ratio={})"
            .format(log_path, SHARE_INN_TRAINING, SHARE_FACILITY_TRAINING,
                    SHARE_REST, SHARE_RATIO))
