# -*- coding: utf-8 -*-
"""修正: NPC の名前が重複する（「バルガス」と「ヴァルガス」が同居する）のを直す。

## 何が起きているか

NPC の名前は LLM が生成する。小さいモデル（Gemma 系）ほど**同じ語を何度も引く**ので、
1つの世界に近い名前が並ぶ。実際に多いのは次の3種で、完全一致は少数派:

| 形 | 例 |
|---|---|
| 表記ゆれ | バルガス / ヴァルガス（`ヴァ` と `バ`、`ティ` と `チ`、長音の有無） |
| 修飾語付き | 「隻眼の」バルガス / 隻眼のバルガス |
| 姓名の片方だけ一致 | バルガス / バルガス・ドレイク |

完全一致だけを弾いても体感は変わらない。**読んで同じ人に見えるものを重複として扱う。**

## どこを直すか

素データ（`npcs` の辞書）と `Character` の両方を、生まれる前に直す。

| 仕掛ける先 | 役目 |
|---|---|
| `__main__:World.generate_character` | **本命。**`character_value` の `name` を `orig` の前に直す。ここで直せば `Character` は最初から新しい名前で生まれる |
| `scripts.characters:Character.__init__` | 受け皿。`generate_character` を通らない経路があっても捕まえる |
| `__main__:InstantaleApp.load_game_new` / `start_game` | 名簿を控え直す（世界が変わったら突き合わせ相手も変わる） |

`Character.__init__` は名前の唯一の入口（`110_` が確かめた）だが、**そこだけでは足りない。**
新しい NPC は `save_data_dict['npcs'][id]` に**先に書かれてから**組み立てられる（GAME.md
§2.23）ので、`self.name` だけ直しても素データには古い名前が残り、次の保存で戻ってくる。
改名したときは id を鍵に持っている辞書（`save_data_dict` / `world_dict` の `npcs`）を
**全部書き換える**。既にある鍵への代入なので、セーブの項目の並びは動かない（§2.23）。

## 誰を対象にするか

**敵と魔物は対象外。**同じ戦闘に「ゴブリン」が3体並ぶのは重複ではない。`Character` は
敵にも使われる（`generate_character_from_enemy_data`）ので、受け皿の側では
**`npcs` の辞書に id がある個体だけ**を見る。敵はそこに載らない。

**プレイヤーは改名しない。**利用者が付けた名前を勝手に変えない。ただし突き合わせ相手
には入れる（プレイヤーと同名の NPC が出るのは同じ不具合）。

## 既にいる重複は既定で触らない（`FIX_EXISTING`）

`110_` は既存キャラクタを改名したが、あれは**壊れた名前のディレクトリが Windows 上に
存在し得ない**ことが根拠だった。こちらは違う。`worlds/<世界>/characters/<名前>/` は
**もう在って画像が入っている。**改名すると次に作る画像だけが新しい名前の側へ行く。

ロード中（`load_game_new` / `start_game` の最中）に見つけた重複は、既定では
**記録だけ**して名簿に載せる。載せておけば「後から作られる NPC がそれと衝突する」
判定には効く。設定で `FIX_EXISTING` を立てると、既にいる重複も改名する。

## 名づけを丸ごと引き取る（`ALWAYS_RENAME`。既定は切）

重複しているかに関わらず、**これから生まれる NPC の名前を全部こちらで付ける。**
LLM が書いた名前は捨てて名簿から引く。重複が消えるだけでなく、名前の傾向そのものが
名簿で決まる（`npc.json` を入れ替えれば世界の空気が変わる）。

**既定は切。**素のゲームが付けた名前を勝手に捨てるのは、直しではなく作り替えなので、
利用者が選んだときだけにする。

| | 既定（切） | `ALWAYS_RENAME` |
|---|---|---|
| これから生まれる NPC | 重複したときだけ付け直す | **全員**こちらで付ける |
| 既に世界に居る NPC | 触らない（`FIX_EXISTING` で重複だけ） | 同じ。触らない |
| プレイヤー・敵・魔物 | 触らない | 同じ。触らない |

**「既に居る人」を取り違えないことが要点。**`world.characters` と `npcs` は件数が
違う（GAME.md §2.23）ので、名簿を控えるときに**素データの `npcs` も全部控える**。
これを怠ると、後から組み立てられただけの古参が新顔に見え、**昔からの知り合いが
突然改名する**。同じ NPC を二度裁かない歯止めも要る（`resolve` の頭の番人）。
重複を見て動く既定の側では「衝突していない ＝ 何もしない」が歯止めになっていたが、
`ALWAYS_RENAME` では名前だけでは見分けられない。

> **LLM に名前を作らせるのをやめるわけではない。**止められるのは**結果**だけで、
> 生成そのものはゲームのプロンプトの中にある（そこは未調査）。推論の費用は変わらず、
> 出てきた名前を捨てている。

## 近さの測り方（`SIMILARITY` / `FOLD_VOICING`）

名前を**読みの骨だけに削った鍵**へ写して比べる。

    「隻眼の」バルガス  ->  ハルカス
    ヴァルガス          ->  ハルカス
    バルガス・ドレイク  ->  ハルカス + トレイク

1. NFKC で正規化し、括弧とその中身（`「」『』()【】…`）を落とす
2. 残った先頭の修飾語（`〜の`）を落とす。`の` は助詞なので、片仮名の `ノ` は落とさない
3. `・` `＝` 空白で姓名に割る
4. 平仮名を片仮名へ寄せ、拗音の綴りを潰す（`ヴァ`→`バ` `ティ`→`チ` `ファ`→`ハ`
   `ジェ`→`ゼ`）、長音 `ー`・促音 `ッ`・小書きを落とす
5. `FOLD_VOICING` なら濁点・半濁点も落とす（`ガ`→`カ`）。Gemma の揺れはここが多い
6. ラテン文字は小文字にして `v`→`b` `c`→`k` `ph`→`f` などに寄せる

比べ方は3つ。どれか1つでも当たれば重複とみなす。

| 判定 | 例 |
|---|---|
| 鍵が同じ | バルガス / ヴァルガス |
| 片方の姓名がもう片方に含まれる | バルガス / バルガス・ドレイク |
| 編集距離が近い | バルガス / バルガド |

編集距離の許容は `SIMILARITY` で決める。短い名前ほど1文字の差が効くので、鍵の長さで
段を付ける（`ジル` と `ジン` を同一視しない）。

| 値 | 許容 |
|---|---|
| `strict` | 0（鍵が完全に一致するときだけ） |
| `normal` | 鍵が4文字以上なら1 |
| `loose` | 3文字以上で1、6文字以上で2 |

## 付け直す名前は名簿から選ぶ

**名前を機械が組み立てると読み物として悪くなる**（音を替えると `ヴァルガス` →
`ヴァロガバ`、LLM に書かせると当たり外れが出る）。だから**用意した名簿から選ぶ。**

名簿は `npc.json`（同梱は `npc.default.json`）。

| 鍵 | 中身 |
|---|---|
| `male` / `female` | 名前（同梱はそれぞれ 600 件） |
| `epithets` | 二つ名（同梱 300 件）。`「死神」` `沈黙の` `死を運ぶ` の3つの形 |

**読むのはこの3つだけで、知らない鍵は在っても黙って無視する。**利用者が別の道具で
作った名簿を持ってくることがあるので、知らない鍵で撥ねない。撥ねてよいのは
**書き込む側**（`@ctx.patch` が名前を新設しないのはそれ）で、これは読む側のデータ。

### 男女はセーブの `category` で決める

`category` は年代と性別を1つで持っている（実データ: `young man` / `middle-aged woman` /
`teenage girl` / `old man` の8種）。**`woman` は `man` を含む**ので、女性を先に見る。
どちらとも読めなければ引く（名簿を分けている以上、どちらかに倒すしかない）。

### 二つ名は `EPITHET_CHANCE`（既定 10%）

既定を 30% から 10% に下げた（v5）。`ALWAYS_RENAME` で全員を名簿から命名する
遊び方だと、30% では10人に3人が異名持ちになり「特別な人の印」が薄まる。10% なら
NPC 20人の世界に1人くらいは居て、居れば目立つ。重複時のみ改名する既定の遊び方では
そもそも改名が稀なので、見せたければ設定で上げる。

名簿の二つ名は3つの形とも**そのまま名前の前に付く**（`「死神」ドルレイン` /
`沈黙のドルレイン` / `死を運ぶドルレイン`）。

**元の名前に付いていた二つ名は引き継がない。**新しい名前は元とは別物なので、
元の持ち主の二つ名を連れてくる理由が無い。

### 選び方は乱数（世界ごとに顔ぶれが変わる）

初版は `crc32(id)` で位置を決めていた。同じ世界を読み直しても同じ名前になる利点は
あったが、**世界をまたぐと同じ id が毎回同じ名前になる** ― 遊ぶたび同じ顔ぶれの
名前が並ぶ。名前を選ぶのに再現性は要らないので、乱数に替えた。

乱数は **MOD 専用の `random.Random`**（`RNG`）から引く。グローバルの `random` を
使うとゲーム自身の乱数列がずれる（GAME.md §2.11）。

名簿は**全部を混ぜてから前へ進む。**位置を1つ選んでそこから前へ進む形にすると、
名簿の前の方だけが繰り返し使われる。

> **乱数でも名前は落ち着く。**改名は `character.name` と素データの両方に書くので、
> 次にその NPC を見たときには衝突が無く、二度目の改名は起きない。名前が振れるのは
> 「改名したがセーブせずにやめた」ときだけ。

名簿が読めないとき・名簿が全部使われているときは、`110_` と同じ立場で
**記録だけ**して元の名前のまま通す。**名前を発明する経路は持たない。**
"""

import io
import json
import os
import random
import re
import sys
import unicodedata

from instantale_modloader import frames, ui

LOG_BASENAME = "npc_name.log"

# **MOD 専用の乱数。**グローバルの `random` から引くとゲーム自身の乱数列がずれる
# （GAME.md §2.11）。検査は `RNG.seed(0)` で固定する。
RNG = random.Random()

# 近さの許容（docstring の表）。
SIMILARITY = "normal"
# 濁点・半濁点の違いを同じ音として扱う。Gemma はここが揺れる。
FOLD_VOICING = True
# 既に世界に居る重複も改名する。既定では記録だけ（画像のディレクトリが在るため）。
FIX_EXISTING = False
# 重複していなくても、これから生まれる NPC の名前は全部こちらで付ける。
# **既定は切**（素のゲームの名前を勝手に捨てないため）。
ALWAYS_RENAME = False
# 二つ名を付ける割合（%）。0 で付けない。
EPITHET_CHANCE = 10

# 名簿。利用者のものが在ればそちら、無ければ同梱を読む（TECH.md §3.1.1）。
NAMES_FILE_NAME = "npc.json"
DEFAULT_NAMES_FILE_NAME = "npc.default.json"
# 読む鍵。知らない鍵は在っても無視する。
NAME_KEYS = ("male", "female")
EPITHET_KEY = "epithets"

# 名前に使えない文字（`110_` の対象。入ると立ち絵が無言で作られなくなる）。
# 名簿に混ざっていたらその1件だけ捨てる。
BAD_NAME_CHARS = set('"<>:|?*/\\')
# 名前の上限。名簿が壊れていたときの用心。
NAME_LIMIT = 24

# `category` から男女を読む語。**`woman` は `man` を含むので女性を先に見る。**
FEMALE_WORDS = ("woman", "women", "girl", "female", "lady")
MALE_WORDS = ("man", "men", "boy", "male", "guy")

# ---------------------------------------------------------------- 鍵の作り方

# NFKC を通した後に残る括弧だけを並べる（`（）` は `()` に、`［］` は `[]` になる）。
BRACKETS = (("「", "」"), ("『", "』"), ("〈", "〉"), ("《", "》"), ("【", "】"),
            ("〔", "〕"), ("(", ")"), ("[", "]"), ("{", "}"),
            ("“", "”"), ("‘", "’"), ('"', '"'))

# 姓名の区切り。`ー` は長音なので入れない。
SEPARATORS = "・･=,、。/\\ \t　"

# 拗音の綴りゆれ。**長いものから**当てる（`ヴァ` を `ヴ` より先に見る）。
DIGRAPHS = (
    ("ヴァ", "バ"), ("ヴィ", "ビ"), ("ヴゥ", "ブ"), ("ヴェ", "ベ"), ("ヴォ", "ボ"),
    ("ヴュ", "ビュ"), ("ヴ", "ブ"),
    ("ティ", "チ"), ("テュ", "チュ"), ("ディ", "ジ"), ("デュ", "ジュ"),
    ("トゥ", "ツ"), ("ドゥ", "ズ"),
    ("ファ", "ハ"), ("フィ", "ヒ"), ("フェ", "ヘ"), ("フォ", "ホ"), ("フュ", "ヒュ"),
    ("シェ", "セ"), ("ジェ", "ゼ"), ("チェ", "セ"),
    ("ウィ", "イ"), ("ウェ", "エ"), ("ウォ", "オ"), ("イェ", "エ"),
    ("クァ", "カ"), ("クィ", "キ"), ("クェ", "ケ"), ("クォ", "コ"), ("グァ", "ガ"),
    ("ツァ", "ツ"), ("ツィ", "ツ"), ("ツェ", "ツ"), ("ツォ", "ツ"),
)

# 小書き・長音・促音。綴りの飾りなので鍵からは落とす。
SMALL_KANA = {"ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ",
              "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ", "ヵ": "カ", "ヶ": "ケ",
              "ッ": "", "ー": "", "〜": "", "～": ""}

# ラテン文字の綴りゆれ。**長いものから**当てる。
LATIN_FOLD = (("ph", "f"), ("th", "t"), ("ck", "k"), ("qu", "k"), ("x", "ks"),
              ("c", "k"), ("v", "b"), ("w", "u"), ("y", "i"), ("j", "i"))

VOICED = "゙"      # 濁点（結合文字）
SEMI_VOICED = "゚"  # 半濁点（結合文字）


# ============================================================ 鍵（読みの骨）

def _strip_brackets(text):
    """括弧とその中身を落とす。閉じていない括弧文字は最後にまとめて落とす。"""
    for opener, closer in BRACKETS:
        while True:
            start = text.find(opener)
            if start < 0:
                break
            end = text.find(closer, start + len(opener))
            if end < 0:
                break
            text = text[:start] + text[end + len(closer):]
    for opener, closer in BRACKETS:
        text = text.replace(opener, "").replace(closer, "")
    return text


def _strip_epithet(text):
    """先頭の修飾語（`隻眼の…`）を落とす。

    `の` は助詞。片仮名の `ノ`（`ミノル`）は名前の一部なので見ない。
    落とした残りが2文字未満になるなら、修飾語ではなく名前そのもの。
    """
    index = text.rfind("の")
    if index <= 0:
        return text
    rest = text[index + 1:]
    return rest if len(rest) >= 2 else text


def _fold_voicing(text):
    """濁点・半濁点を落とす。合成済みの文字を一度分解して結合文字を捨てる。"""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = decomposed.replace(VOICED, "").replace(SEMI_VOICED, "")
    return unicodedata.normalize("NFC", stripped)


def to_katakana(text):
    """平仮名を片仮名へ（`ゐ` `ゑ` まで。`ゝ` などは対象外）。"""
    return "".join(chr(ord(ch) + 0x60) if 0x3041 <= ord(ch) <= 0x3096 else ch
                   for ch in text)


def _fold_kana(text):
    """平仮名を片仮名へ寄せ、綴りの飾りを落とす。"""
    text = to_katakana(text)
    for src, dst in DIGRAPHS:
        text = text.replace(src, dst)
    for src, dst in SMALL_KANA.items():
        text = text.replace(src, dst)
    return text


def _fold_latin(text):
    text = text.lower()
    for src, dst in LATIN_FOLD:
        text = text.replace(src, dst)
    # 重ねた字（`Bell` / `Bel`）は同じ読み。**ラテン文字だけ**。仮名で潰すと
    # `ナナシ` が `ナシ` になり、別人が同じ鍵に落ちる。
    out = []
    for ch in text:
        if out and out[-1] == ch and "a" <= ch <= "z":
            continue
        out.append(ch)
    return "".join(out)


def canonical(name):
    """名前を「読みの骨」の並びにする。姓名は分けたまま返す。

    戻り値は文字列のタプル。比べられるものが何も残らなければ空のタプル。
    """
    if not isinstance(name, str) or not name.strip():
        return ()
    text = unicodedata.normalize("NFKC", name)
    text = _strip_brackets(text)
    text = _strip_epithet(text)
    # 通し番号（`バルガス2`）は名前の違いではない。
    text = re.sub(r"[0-9]+", "", text)

    segments = []
    for chunk in re.split("[" + re.escape(SEPARATORS) + "]", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        chunk = _fold_kana(chunk)
        if FOLD_VOICING:
            chunk = _fold_voicing(chunk)
        chunk = _fold_latin(chunk)
        # 記号と、寄せ切れなかった飾りを落とす。漢字はそのまま残す。
        chunk = "".join(ch for ch in chunk if ch.isalnum())
        if chunk:
            segments.append(chunk)
    return tuple(segments)


def distance(a, b, limit):
    """編集距離。`limit` を超えると分かった時点で打ち切る。"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, 1):
        current = [i]
        for j, ch_b in enumerate(b, 1):
            current.append(min(previous[j] + 1,
                               current[j - 1] + 1,
                               previous[j - 1] + (ch_a != ch_b)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def allowance(length):
    """この長さの鍵で、何文字までの違いを「同じ」とみなすか。"""
    if SIMILARITY == "strict":
        return 0
    if SIMILARITY == "loose":
        if length >= 6:
            return 2
        return 1 if length >= 3 else 0
    return 1 if length >= 4 else 0


def _near(a, b):
    if a == b:
        return True
    limit = allowance(min(len(a), len(b)))
    return limit > 0 and distance(a, b, limit) <= limit


def too_close(left, right):
    """2つの鍵が同じ人に見えるか。"""
    if not left or not right:
        return False
    if _near("".join(left), "".join(right)):
        return True
    # 片方が姓名、もう片方が名だけ（バルガス / バルガス・ドレイク）。
    if len(left) == 1 or len(right) == 1:
        return any(_near(one, other) for one in left for other in right)
    # どちらも姓名なら、全部が含まれているときだけ同一とみなす。
    # 姓か名の片方だけが同じ（アレン・スミス / アレン・ジョーンズ）は別人。
    return all(any(_near(one, other) for other in right) for one in left) \
        or all(any(_near(one, other) for other in left) for one in right)


# ================================================== 名簿から名前を選ぶ

def names_path(mod_dir, basename):
    """`mod_dir` は `apply()` の外では None（GAME.md §2.12）。呼ぶ側が控えること。"""
    return os.path.join(mod_dir, basename) if mod_dir else None


def pick_path(mod_dir):
    """利用者の名簿が在ればそれ、無ければ同梱を返す。どちらも無ければ None。"""
    user = names_path(mod_dir, NAMES_FILE_NAME)
    if user and os.path.isfile(user):
        return user
    bundled = names_path(mod_dir, DEFAULT_NAMES_FILE_NAME)
    if bundled and os.path.isfile(bundled):
        return bundled
    return None


def usable(value):
    """名簿の1件として使える文字列か。壊れた1件はその1件だけ捨てる。"""
    if not isinstance(value, str):
        return False
    got = value.strip()
    return bool(got) and len(got) <= NAME_LIMIT and not any(
        ch in BAD_NAME_CHARS or ord(ch) < 0x20 for ch in got)


def read_roster(path):
    """名簿を読む。`(名前の表, 二つ名)` を返す。読めなければ `({}, [])`。

    **知らない鍵は黙って無視する。**別の道具で作った名簿をそのまま置けるように
    するため。読む鍵が1つも無ければ空で返り、呼んだ側が警告を出す。
    """
    if not path:
        return {}, []
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}, []
    if not isinstance(data, dict):
        return {}, []
    names = {}
    for key in NAME_KEYS:
        rows = data.get(key)
        if isinstance(rows, list):
            # 名簿に同じ名前が2度出ていたら1度だけ数える。
            picked, seen = [], set()
            for row in rows:
                got = row.strip() if isinstance(row, str) else row
                if usable(got) and got not in seen:
                    seen.add(got)
                    picked.append(got)
            names[key] = picked
    epithets = [row.strip() for row in data.get(EPITHET_KEY, [])
                if usable(row)] if isinstance(data.get(EPITHET_KEY), list) else []
    return names, epithets


def gender_of(context):
    """`male` / `female` のどちら側の名簿を引くか。

    `category` は年代と性別を1つで持っている（`young man` / `teenage girl`）。
    **`woman` は `man` を含むので女性を先に見る。**読めなければ引く（名簿を男女で
    分けている以上、どちらかに倒すしかない）。
    """
    text = ""
    if isinstance(context, dict):
        for key in ("category", "gender", "sex"):
            value = context.get(key)
            if isinstance(value, str):
                text += " " + value.lower()
    if any(word in text for word in FEMALE_WORDS):
        return "female"
    if any(word in text for word in MALE_WORDS):
        return "male"
    return RNG.choice(NAME_KEYS)


def wants_epithet():
    return RNG.random() * 100 < EPITHET_CHANCE


def epithet_for(epithets):
    """二つ名。名簿の3つの形（`「死神」` / `沈黙の` / `死を運ぶ`）はどれも
    そのまま名前の前に付く。"""
    return RNG.choice(epithets) if epithets else ""


def candidates(pool):
    """名簿を引く順に並べて返す。

    **位置を頭から探さない。**名簿の前の方だけが使われて、遊ぶたび同じ名前が
    並ぶことになる。全部を混ぜてから前へ進むので、空いている名前が均等に出る。
    """
    order = list(pool)
    RNG.shuffle(order)
    return order


# ================================================================== 本体

def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    # 名簿は MOD のフォルダの中。**`ctx.mod_dir` は `apply()` の中で控える**
    # （外では None になる。GAME.md §2.12 で実際に踏んでいる）。
    roster_path = pick_path(ctx.mod_dir)
    names, epithets = read_roster(roster_path)
    # id -> (鍵, 表示上の名前)。世界を読み直すたびに作り直す。
    known = {}
    # ロード中は「既にいる人」を見ている。深さで数えるのは start_game の中から
    # load_game_new が呼ばれても壊れないようにするため。
    loading = {"depth": 0}
    # (id, 元の名前) ごとに1回だけ記録する。同じ NPC が2つの経路から来ても、
    # 直せない重複がロードのたびに現れても、行が増えないように。
    seen = set()

    write = ctx.logger(LOG_BASENAME, tag="[NPCNAME]")

    find_app = ui.find_app     # 走っている app の探し方はローダの語彙

    def npc_tables(app):
        """id を鍵に持つ素データの辞書を全部返す（GAME.md §2.23）。"""
        tables = []
        for holder_name in ("save_data_dict", "world_dict"):
            holder = frames.attr(app, holder_name, None)
            if isinstance(holder, dict):
                npcs = holder.get("npcs")
                if isinstance(npcs, dict):
                    tables.append(npcs)
        return tables

    def is_npc(app, cid):
        """素データに載っている id か。敵と魔物はここに載らない。"""
        if cid is None:
            return False
        return any(str(cid) in npcs for npcs in npc_tables(app))

    def raw_npc(app, cid):
        """その NPC の素データ（職業・人物像）。頼み文に載せる用。"""
        for npcs in npc_tables(app):
            entry = npcs.get(str(cid))
            if isinstance(entry, dict):
                return entry
        return None

    def rewrite_raw(app, cid, old, new):
        """id を鍵に持つ辞書の `name` を全部書き換える。

        既にある鍵への代入なので、セーブの項目の並びは動かない（GAME.md §2.23）。
        """
        touched = 0
        for npcs in npc_tables(app):
            entry = npcs.get(str(cid))
            if isinstance(entry, dict) and entry.get("name") == old:
                entry["name"] = new
                touched += 1
        return touched

    def clash_with(key, cid):
        """名簿の中で `key` と同じ人に見える相手。無ければ None。"""
        for other_id, (other_key, other_name) in known.items():
            if str(other_id) == str(cid):
                continue
            if too_close(key, other_key):
                return other_id, other_name
        return None

    def remember(cid, key, name):
        known[str(cid)] = (key, name)

    def rename_to(name, cid, context):
        """名簿から、まだ使われていない名前を選ぶ。選べなければ None。

        名簿は男女で分かれているので、片方が尽きたらもう片方も見る
        （尽きるほど NPC が居る世界で、名前が付かないほうを選ぶ理由が無い）。
        """
        gender = gender_of(context)
        pools = [gender] + [key for key in NAME_KEYS if key != gender]
        head = epithet_for(epithets) if wants_epithet() else ""
        for pool_name in pools:
            for candidate in candidates(names.get(pool_name) or []):
                fresh = head + candidate
                key = canonical(fresh)
                if not key or clash_with(key, cid) is not None:
                    continue
                return fresh
        return None

    def resolve(cid, name, where, app=None, context=None):
        """名前を1つ裁く。改名したときだけ新しい名前を返す。

        控えへの登録はここで必ず行う（改名しなかった重複も相手として残す）。
        """
        kept = known.get(str(cid))
        if kept is not None and kept[1] == name:
            # この NPC はもう裁いた。`generate_character` で付けた名前を
            # 受け皿の `Character.__init__` がもう一度付け直さないための番人で、
            # `ALWAYS_RENAME` のときは**これが唯一の歯止め**になる
            # （衝突していなくても改名する経路なので、名前で見分けられない）。
            return None
        key = canonical(name)
        if not key:
            return None

        found = clash_with(key, cid)
        mark = (str(cid), name)
        if loading["depth"] > 0:
            # 既に世界に居る人。重複だけを、しかも許しがあるときだけ直す。
            wanted = found is not None and FIX_EXISTING
            why = "already in the world"
        else:
            # これから生まれる人。`ALWAYS_RENAME` なら重複していなくても付け直す。
            wanted = ALWAYS_RENAME or found is not None
            why = "no free name in the roster"

        fresh = rename_to(name, cid, context) if wanted else None
        if fresh is None:
            # 直せなくても控えには載せる。3人目が来たときの相手になる。
            remember(cid, key, name)
            if found is not None and mark not in seen:
                seen.add(mark)
                write("{}: id={!r} {!r} clashes with id={!r} {!r} -- left as it is ({})"
                      .format(where, cid, name, found[0], found[1],
                              why if wanted or loading["depth"] > 0
                              else "no free name in the roster"))
            return None

        remember(cid, canonical(fresh), fresh)
        touched = rewrite_raw(app, cid, name, fresh) if app is not None else 0
        if mark not in seen:
            seen.add(mark)
            write("{}: id={!r} {!r} -> {!r}  ({}; {} raw table(s))".format(
                where, cid, name, fresh,
                "clashed with id={!r} {!r}".format(*found) if found is not None
                else "always renaming", touched))
        return fresh

    def seed(app, where):
        """名簿を控え直す。世界が変わったら突き合わせ相手も変わる。

        ここで見つかる重複は**既に世界に居る**ので、`FIX_EXISTING` が立って
        いない限り改名しない（`loading` を立てたまま裁く）。
        """
        known.clear()
        if app is None:
            return 0
        world = frames.attr(app, "world", None)
        characters = frames.attr(world, "characters", None) if world is not None else None
        # 名簿の入れ物は辞書とは限らない（GAME.md §2.8）。
        if isinstance(characters, dict):
            people = list(characters.values())
        elif isinstance(characters, (list, tuple)):
            people = list(characters)
        else:
            people = []
        player = frames.attr(app, "player", None)
        if player is not None:
            # プレイヤーは改名しないが、突き合わせ相手には入れる。
            name = frames.attr(player, "name", None)
            key = canonical(name) if isinstance(name, str) else ()
            if key:
                remember(frames.attr(player, "id", None) or "__player__", key, name)
        fixed = 0
        for character in people:
            name = frames.attr(character, "name", None)
            cid = frames.attr(character, "id", None)
            if not isinstance(name, str):
                continue
            fresh = resolve(cid, name, where, app, raw_npc(app, cid))
            if fresh is not None:
                try:
                    character.name = fresh
                    fixed += 1
                except Exception:
                    ctx.log_exc("npc name: could not rename {!r}".format(name))

        # まだ組み立てられていないが、セーブの素データには居る人も控える。
        # `world.characters` と `npcs` は**件数が違う**（GAME.md §2.23）ので、
        # ここを控えないと、後から組み立てられた古参を新顔と取り違える。
        # `ALWAYS_RENAME` のときはそれがそのまま「昔からの知り合いが突然改名する」
        # になるので、この控えがその歯止めになっている。
        for npcs in npc_tables(app):
            for cid, entry in npcs.items():
                if str(cid) in known or not isinstance(entry, dict):
                    continue
                raw = entry.get("name")
                key = canonical(raw) if isinstance(raw, str) else ()
                if key:
                    remember(cid, key, raw)
        return fixed

    # -------------------------------------------------- 本命: 素データを直す
    @ctx.wrap("__main__:World.generate_character")
    def generate_character(orig, self, character_id, *args, **kwargs):
        value = args[0] if args else kwargs.get("character_value")
        try:
            if isinstance(value, dict):
                name = value.get("name")
                if isinstance(name, str):
                    app = find_app()
                    # 素データそのものが人物像。頼み文にそのまま載せる。
                    fresh = resolve(character_id, name, "generate_character",
                                    app, value)
                    if fresh is not None:
                        # 渡された辞書と、素データの辞書の両方を直す。
                        # 同じ辞書のこともあるが、二重に書いても結果は変わらない。
                        value["name"] = fresh
        except Exception:
            # 名前のために NPC の生成を落とさない。
            ctx.log_exc("npc name: could not check {!r}".format(character_id))
        return orig(self, character_id, *args, **kwargs)

    # ------------------------------------------------------------ 受け皿
    @ctx.wrap("scripts.characters:Character.__init__")
    def character_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if not frames.attr(self, "is_player", False):
                name = frames.attr(self, "name", None)
                cid = frames.attr(self, "id", None)
                app = find_app()
                # 敵と魔物は素データの `npcs` に載らない。同じ名前が並んでよい。
                if isinstance(name, str) and is_npc(app, cid):
                    fresh = resolve(cid, name, "Character.__init__", app,
                                    raw_npc(app, cid))
                    if fresh is not None:
                        self.name = fresh
        except Exception:
            ctx.log_exc("npc name: sanitize failed; leaving the name as it is")
        return result

    # ------------------------------------------ 世界が変わったら控え直す
    def on_load(target, label):
        @ctx.wrap(target, required=False)
        def _load(orig, self, *args, **kwargs):
            # ロードの最中に組み立てられる NPC も、その後の控え直しも、
            # どちらも「既に世界に居る人」として裁く。
            loading["depth"] += 1
            try:
                result = orig(self, *args, **kwargs)
                try:
                    app = (self if frames.attr(self, "world", None) is not None
                           else find_app())
                    fixed = seed(app, label)
                    if fixed:
                        ctx.log("npc name: renamed {} duplicate(s) already in the world; "
                                "see out/{}".format(fixed, LOG_BASENAME))
                except Exception:
                    ctx.log_exc("npc name: seeding the roster failed")
                return result
            finally:
                loading["depth"] -= 1
        return _load

    on_load("__main__:InstantaleApp.load_game_new", "load_game_new")
    on_load("__main__:InstantaleApp.start_game", "start_game")

    # 注入した時点で世界が動いていれば、そこから名簿を作る。
    loading["depth"] += 1
    try:
        seed(find_app(), "injection")
    except Exception:
        ctx.log_exc("npc name: boot seeding failed")
    finally:
        loading["depth"] -= 1

    # 名簿が読めていないと改名が1件も起きない。**必ず名指しで報告する**
    # （黙って何もしない MOD になるのが一番分かりにくい）。
    if not any(names.get(key) for key in NAME_KEYS):
        ctx.log("npc name dedup: no roster; nothing will be renamed. expected {} or {} "
                "in the mod folder (tried {})".format(
                    NAMES_FILE_NAME, DEFAULT_NAMES_FILE_NAME, roster_path),
                level="WARN")
    else:
        ctx.log("npc name dedup: roster={} ({}); epithets={} at {}%; "
                "similarity={} fold_voicing={} fix_existing={}; log={}".format(
                    os.path.basename(roster_path),
                    " ".join("{}={}".format(key, len(names.get(key) or ""))
                             for key in NAME_KEYS),
                    len(epithets), EPITHET_CHANCE,
                    SIMILARITY, FOLD_VOICING, FIX_EXISTING, log_path))
