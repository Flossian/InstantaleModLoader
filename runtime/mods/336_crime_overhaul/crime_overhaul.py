# -*- coding: utf-8 -*-
"""犯罪まわりの全面改修。遊び方は DOC.md、決めた仕様と確認手順は VERIFICATION.md §3.84。

素のゲームの犯罪は、自由行動のその場で LLM が出す金品しか見返りが無く、
額も出すかどうかも LLM 任せだった。手配はリスクだけを積む（衛兵・懲役、`309_` の罰金、`316_` の追手）。
見返りは機能ごとのファイルに分けてある。ここは設定と、ゲームの入口（本文）を1枚だけ包んで配る役。
選択肢と押下は、ローダの窓口 `choices` に預ける。

| ファイル | 機能 |
|---|---|
| `loot` | 盗みの稼ぎ（自由行動の盗みの額に床と天井を付ける） |
| `law` | 手配への反応（店が委縮して値を下げる・時効） |
| `theft` | 店で盗む（売買画面の右クリック）。盗んだ店には売れず、よその店では買い取り額が半分 |
| `fence` | 盗品買取商（裏の事務所で盗品を売る） |
| `office` | 裏の仕事（裏の事務所の違法な依頼）。種類の表は `underworld` |
| `prison` | 脱獄（服役中の毎年の画面）。決まりは `jailbreak` |
| `cellmate` | 同房の囚人（服役の始まりに新しく作り、牢の中で話し、決行に加勢し、出所後はギルドで雇える） |
| `court` | 裁判の改修（釈明の知らせ・弁護人・司法取引・判事の買収・情状）。決まりは `trial` |
| `encounter` | 衛兵に見つかった場面での、衛兵の買収 |
| `rescue` | 死刑の判決の後の、処刑場からの脱出（ダンジョン） |
| `common` | 共有の土台（ゲームの値の読み方・控え・画面の部品・振り分け） |
| `rules` | 数の決まり（ゲームに触らない） |
"""

import random

from instantale_modloader import choices, wanted

from . import cellmate, common, court, encounter, fence, law, loot, office, prison, rescue, theft

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
LOOT_ENABLED = True           # 盗みの稼ぎを整える
LOOT_SHARE_PETTY = 5          # 規模1（小銭）の目安。依頼1件の報酬に対する %
LOOT_SHARE_MODEST = 20        # 規模2（まとまった額）
LOOT_SHARE_LARGE = 50         # 規模3（大金）
LOOT_SHARE_FORTUNE = 100      # 規模4（財産）
LOOT_FLOOR_PCT = 50           # LLM の額がこれ（目安の%）に満たなければ、ここまで足す
LOOT_CEILING_PCT = 200        # LLM の額がこれ（目安の%）を超えれば、ここまで削る
LOOT_NOTICE = True            # 足した・削ったことを本文に1行出す
INTIMIDATION_ENABLED = True   # 手配中の土地で店が委縮して値を下げる
INTIMIDATION_PER_POINT = 0.5  # 手配の重さ1あたりの率（%）
INTIMIDATION_CAP = 30         # 率の上限（%）
INTIMIDATION_SELL = True      # 売値も上げる
INTIMIDATION_NOTICE = True    # 店に入ったときに1行出す
STATUTE_ENABLED = True        # 離れている土地の手配が時とともに戻る
STATUTE_PERIOD_MONTHS = 3     # 戻る間隔（ゲームの月。1ヵ月30日）
STATUTE_STEP = 10             # 1回に戻る手配度
STATUTE_RESTORE_TO = 10       # ここまで戻る（素の平常値）
STATUTE_HOLD_HUNTED = True    # 全域手配の間は、合計がその線に届いたところで止める（追手は続く）
STATUTE_NOTICE = True         # 戻ったときに1行出す
THEFT_ENABLED = True          # 売買画面で店の品を右クリックすると「盗む」が出る
THEFT_BASE_PCT = 30           # 能力値 15 のときの確率（器用で抜き取る・判断で視線に気づく、の両方）
THEFT_PER_POINT = 3           # 能力値1点ごとに動く確率（%）
THEFT_MAX_PCT = 90            # 確率の上限（%）
THEFT_STREAK_STEP = 10        # 同じ店で同じ日に試した回数1回ごとに、抜き取る確率から引く（%）
THEFT_CELL_STEP = 5           # 品が1マスを超えたマス1つごとに引く（%）
THEFT_PRICE_STEP = 10         # 買価がその店の棚の中央値の2倍で引く（%）。4倍でその2倍、間は比例
THEFT_PRICE_CAP = 30          # 値段で引く分の上限（%）
THEFT_RARITY_STEP = 5         # レア度の段（common 0 〜 mythic 5）1つごとに引く（%）
STOLEN_SELL_PCT = 50          # 盗品を盗んだ店以外の店で売るときの額（正規の買い取り額に対する %）
FENCE_ENABLED = True          # 裏の事務所に「盗品を売る」（盗品買取商）が出る
FENCE_PCT = 70                # 盗品買取商の買い取り額（正規の買い取り額に対する %）
UNDERWORLD_ENABLED = True     # 裏の事務所で裏の仕事を受けられる
UNDERWORLD_REWARD_PCT = 100   # 種類ごとの報酬の倍率（暗殺10倍など）に掛ける調整（%）
UNDERWORLD_LOSS_PCT = 100     # 種類ごとの手配度の下がり幅に掛ける調整（%）
UNDERWORLD_UNLOCK_PCT = 100   # 種類ごとの解禁に要る手配の重さに掛ける調整（%）
UNDERWORLD_SEARCH_BELOW = 2   # 片付けていない裏の仕事がこの数より少ないと「裏の仕事を探す」が出る（素の掲示板と同じ）
JAILBREAK_ENABLED = True      # 服役中の毎年の画面で脱獄に備え、決行できる
JAILBREAK_PREP_MAX = 5        # 備えの上限
JAILBREAK_PREP_BASE_PCT = 50  # 能力値 15 のときに備えが実る確率（壁を削る＝器用・抜け道を探る＝判断）
JAILBREAK_PER_POINT = 3       # 能力値1点ごとに動く確率（%）
JAILBREAK_DETECT_PCT = 10     # 備えの年に企てが露見する確率（%）
JAILBREAK_DETECT_PER_PREP = 5 # 積んだ備え1つごとに露見の確率に足す（%）
JAILBREAK_BRIBE_PCT = 50      # 看守を手懐ける額。その土地の依頼1件の報酬に対する %
JAILBREAK_EASE_PCT = 15       # 備え1つごとに、決行の戦闘の相手を弱くする（%）
JAILBREAK_EXTEND_YEARS = 2    # 露見したとき・決行で倒れたときに延びる刑期（年）
JAILBREAK_LOSS = 20           # 脱獄したとき、その土地で下がる手配度
CELLMATE_ENABLED = True       # 懲役が始まると、確率で新しい囚人が同じ房に入る
CELLMATE_PCT = 50             # 同房の囚人が現れる確率（%）
CELLMATE_AFFINITY_PER_YEAR = 10  # 同じ房で1年過ごすたびに上がる好感度
CELLMATE_ASSIST_AFFINITY = 40 # この好感度以上なら脱獄の決行に加勢する
TRIAL_HINT = True             # 裁判の画面で、入力欄の言葉が釈明になると知らせる
TRIAL_CONTEXT = True          # 検察と判事の頼みに情状（手配の重さ・この土地での活躍・人生の記録）を足す
TRIAL_LAWYER_ENABLED = True   # 裁判で弁護人を雇える
TRIAL_LAWYER_PCT = 100        # 弁護人の額。その土地の依頼1件の報酬に対する %
TRIAL_LAWYER_CUT_PCT = 30     # 弁護人で求刑から減らす割合（%）。死刑は懲役に改まる
TRIAL_PLEA_ENABLED = True     # 裏の仕事を片付けたことがあれば、司法取引を持ちかけられる
TRIAL_PLEA_CUT_PCT = 50       # 司法取引で求刑から減らす割合（%）
TRIAL_PLEA_BAN_DAYS = 360     # 司法取引の後、牢を出てから裏の事務所が仕事を回さない日数
TRIAL_BRIBE_ENABLED = True    # 判事に袖の下を渡せる
TRIAL_BRIBE_PCT = 200         # 袖の下の額。その土地の依頼1件の報酬に対する %
TRIAL_BRIBE_BASE_PCT = 40     # 魅力 15 のときに判事が受け取る確率（%）
TRIAL_BRIBE_PER_POINT = 3     # 魅力1点ごとに動く確率（%）
TRIAL_BRIBE_CUT_PCT = 70      # 受け取られたとき、求刑から減らす割合（%）
TRIAL_BRIBE_PENALTY_YEARS = 5 # 突き返されたときに足される刑期（年）
TRIAL_DEATH_YEARS = 20        # 死刑の求刑を懲役に改めるときの基の年数
TRIAL_ACQUITTAL_ENABLED = True  # 釈明が罪を覆すと判事が得心したら無罪（その土地の手配度は平常へ）
TRIAL_ACQUITTAL_MIN_CHARS = 10  # 無罪の道を開く釈明の字数（入力欄で書いた釈明だけ。素のボタンの文言では開かない）
GUARD_BRIBE_ENABLED = True    # 衛兵に見つかった場面で、その場で金を握らせられる
GUARD_BRIBE_PCT = 100         # 握らせる額。その土地の依頼1件の報酬に対する %
GUARD_BRIBE_BASE_PCT = 50     # 魅力 15 のときに見逃してもらえる確率（%）
GUARD_BRIBE_PER_POINT = 3     # 魅力1点ごとに動く確率（%）
GUARD_BRIBE_WANTED_PCT = 0.5  # その土地の手配の重さ1あたりに下がる確率（%）
RESCUE_ENABLED = True         # 死刑の判決の後、好感度の高い人物の手引きで処刑場からの脱出（ダンジョン）に挑める
RESCUE_AFFINITY = 60          # 手引きしてくれる好感度（60 は「仲間だと感じている」）
RESCUE_DIFFICULTY_PLUS = 10   # 脱出の難易度。土地の難易度とパーティの平均レベルの高いほうに足す
RESCUE_LOSS = 30              # 脱出したとき、捕まった土地で下がる手配度

#: 盗み・脱獄の判定の乱数。グローバルの `random` から引くとゲーム自身の乱数列がずれる（TECH.md §6.1）。
#: 機能のファイルは `env.cfg._RNG` をその場で読む（確かめるときにここを差し替えられる）。
_RNG = random.Random()


class _Settings(object):
    """入口のモジュールの定数を、その場で読む窓。

    ローダは apply() の前に設定を定数へ書き込む。機能のファイルは値を控えず、使うたびにここから読む。
    """

    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name)


def apply(ctx):
    env = common.Env(ctx, _Settings())
    screen = env.screen
    for feature in (loot, law, theft, office, fence, prison, cellmate, court, rescue, encounter):
        feature.install(env)

    # ---- ゲームの入口は1枚だけ包み、機能へ配る ------------------------------------------
    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        """本文の1行を機能に通す（盗みで借りた購入の文を止める・裏の仕事の報酬の額を書き換える）。"""
        for handler in env.text_filters:
            context, drop = handler(context)
            if drop:
                return None
        return orig(self, context, *args, **kwargs)

    # ---- 選択肢と押下は、ローダの窓口 `choices`（TECH.md §3.3.14）に預ける ---------------------
    # 組み直しへの差し込み、押下の振り分け、セーブで落ちた印の付け直し、ロードの後の組み直し
    # （人物が揃ってから）は窓口が1か所で引き受ける。ここは機能の処理を読み込んだ順に渡すだけ。
    def refresh(app, buttons):
        if not isinstance(buttons, list) or not buttons:
            return
        for handler in env.refresh_handlers:
            handler(app, buttons)

    choices.provide(ctx, screen, refresh=refresh, presses=dict(env.press_handlers))

    ctx.log("crime incentive: installed (loot {} shares {} floor {}% ceiling {}%; "
            "intimidation {} {}%/pt cap {}% sell {}; statute {} +{} per {} month(s) up to {}, "
            "hold {} at the line from {})".format(
                "on" if LOOT_ENABLED else "off", loot.loot_shares(env.cfg), LOOT_FLOOR_PCT,
                LOOT_CEILING_PCT, "on" if INTIMIDATION_ENABLED else "off",
                INTIMIDATION_PER_POINT, INTIMIDATION_CAP,
                "on" if INTIMIDATION_SELL else "off", "on" if STATUTE_ENABLED else "off",
                STATUTE_STEP, STATUTE_PERIOD_MONTHS, STATUTE_RESTORE_TO,
                "on" if STATUTE_HOLD_HUNTED else "off", wanted.owners() or "nobody"))
