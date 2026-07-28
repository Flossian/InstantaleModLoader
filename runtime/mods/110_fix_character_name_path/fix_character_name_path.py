# -*- coding: utf-8 -*-
"""修正: 名前に Windows のパスに使えない文字が入ると画像が生成できないのを直す。

## 実測（2026-07-28、`out/live_crashes.log` ＝ `001_crash_recorder`。VERIFICATION.md §2.14）

```
OSError: [WinError 123] ファイル名、ディレクトリ名、またはボリューム ラベルの
構文が間違っています。:
'...\\worlds\\ドスケベフェスティバル\\characters\\魔導演習人形「プロト・レガリア"'
```

キャラクタ `id='101'` の名前 `魔導演習人形「プロト・レガリア"` が **`「` で開いて
ASCII の `"` で閉じている**（LLM が生成した名前への引用符の混入。原因はユーザー判断で
確定済み）。`"` は Windows のパス構成要素に使えないので `os.makedirs` が落ちる。

| 時刻 | スレッド | 経路 |
|---|---|---|
| 00:06:36 | Thread-111 (execute) | `start_battle` → `create_enemies_from_npc_id` → `generate_and_write_character_detail` → `generate_character_image` → `os.makedirs` |
| 00:11:20 | Thread-123 (generate_images) | `ConversationStartManager.generate_images` → 同上 |

**バックグラウンドスレッドなのでゲームは落ちない。** 画像が生成されないまま無言で
失敗し、その NPC に関わるたび再発する（2回とも同一人物）。

## どこを直すか ― 入口ひとつ

`worlds/<世界>/characters/` の**ディレクトリ名はキャラクタ名そのもの**（実データ:
`「銀鱗」のジーン` / `イリス・ステラ (Iris Stella)`）。名前からパスを組む箇所は
**5つある**（`generate_and_write_character_detail` / `generate_character_image` /
`generate_character_image_from_enemy` / `generate_enemy_image_from_character` /
`delete_world_character_images`）。**5箇所で個別に消毒すると、書き込みと削除で
消毒がずれたときに別の不整合（消し残し・迷子）を生む。**

名前の唯一の入口は `scripts.characters:Character.__init__` で、LLM生成・プリセット・
プレイヤー・セーブからのロードが全部ここを通る。**ここで名前そのものを正せば、
5箇所は今までどおり「名前をそのまま使う」だけで全部一致する。**

引数ではなく `self.name`（`__init__` を抜けた後）を正す。ゲームが名前をどの引数から
どう組み立てているかを知らなくても効くのと、`name=None` のような経路を巻き込まない
ため。

## 置き換え方 ― 消さずに全角へ写す

`< > : " / \\ | ? *` → `＜ ＞ ： ” ／ ＼ ｜ ？ ＊`（VERIFICATION.md §2.14 の方針）。
消すと表示上も名前が欠けるが、全角なら見た目はほぼ変わらず、パスにも使える。
Windows は**末尾の空白・ピリオドを黙って切る**ので、こちらで先に落としておく。

**観測していないものには触らない**（`107_` と同じ立場）:

* 予約デバイス名（`CON` / `NUL` / `COM1` …）はディレクトリ名にできないが、
  直すには名前を発明するしかない。一度も観測していないので**記録だけ**する。
* 消毒の結果が空になる名前（全部が不正文字）も**元のまま**にして記録だけ。

## 既存キャラクタの救済

`id='101'` は**もう世界の中に居る**。`world_data.json` は暗号化されていて外部からは
読めない（先頭が `2L\\x04\\x1b...`、zlib/gzip でもない）ので、**注入後にゲーム内から
直す**しかない。この mod は3か所で名簿を掃く:

| 掃く時点 | 目的 |
|---|---|
| 注入直後 | 起動済みのセッションに居る残骸（`id='101'`） |
| `load_game_new` / `start_game` の直後 | 不正な名前が焼かれたセーブ |
| `__init__` | 以後に作られる全員 |

**旧名のディレクトリは Windows 上に存在し得ない**（不正文字を含むディレクトリは
作成できない ＝ 落ちたのはまさにそれ）ので、改名しても画像の迷子は発生しない。
これが「既存キャラクタを触ってよい」根拠。

**残る懸念**: セーブ側 JSON には旧名が残り、次の保存で入れ替わる。その間、名前で
突き合わせる処理があると食い違う。実測では突き合わせは id（`'101'`）で行われて
いるように見えるが**未確証**なので、**旧名は必ずログに残す**（`out/character_name.log`）。

同じ理由で、名前と同じ生文字列を持つ他の属性が同じインスタンスにあれば
**記録だけ**する（勝手に書き換えない。どこが名前の写しなのかは未確認のため）。

**世界名は対象外。** `worlds/<世界>/` も同じ壊れ方をしうるが、世界名の入口は未調査
なので触らない（発生すれば `001_` が同じ形で捕まえる）。
"""

import datetime
import sys

from instantale_modloader import frames

NAME = "Character name sanitize"
NAME_JA = "キャラクタ名の正規化"
VERSION = "1"
DESCRIPTION = "Replaces path-unsafe characters in character names so portraits generate"
DESCRIPTION_JA = "パスに使えない文字を含むキャラクタ名を全角に置き換え、画像生成の失敗を防ぐ"
AUTHOR = "R01/Flossian"

LOG_BASENAME = "character_name.log"

# パス構成要素に使えない文字 → 見た目の近い全角。消さずに写す。
REPLACEMENTS = {
    "<": "＜", ">": "＞", ":": "：", '"': "”",
    "/": "／", "\\": "＼", "|": "｜", "?": "？", "*": "＊",
}

# Windows が末尾で黙って切るもの。切られると保存先と読み出し先がずれる。
TRAILING = " ."

# ディレクトリ名にできない予約名。**直さない**（名前を発明することになるため）。
# 一度も観測していない。出たらログに残るので、そのとき設計を決める。
RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM{}".format(n) for n in range(1, 10)]
    + ["LPT{}".format(n) for n in range(1, 10)]
)

# 既存の名簿を掃く時点。
SWEEP_ON_BOOT = True
SWEEP_ON_LOAD = True


def sanitize(name):
    """パス構成要素にできる名前を返す。直せない/直す必要が無ければ None。

    戻り値は `(新しい名前, 理由)`。理由はログ用で、`reserved` / `empty` のときは
    新しい名前が None ＝ **触らない**。
    """
    if not isinstance(name, str) or not name:
        return None, None

    cleaned = "".join(REPLACEMENTS.get(ch, ch) for ch in name)
    # 制御文字はパスに置けず、そもそも表示もされない。写す先が無いので落とす。
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 0x20 and ch != "\x7f")
    cleaned = cleaned.rstrip(TRAILING)

    if not cleaned:
        # 名前が丸ごと不正文字。埋める文字を発明しない。
        return None, "empty"
    # 予約名は「不正文字が1つも無いのに作れない」別種なので、**何も変わって
    # いなくても先に見る**（`CON` はこのままでもディレクトリにできない）。
    # 判定は拡張子の手前まで、大文字小文字を問わない ― Windows の規則。
    if cleaned.split(".")[0].upper() in RESERVED:
        return None, "reserved"
    if cleaned == name:
        return None, None
    return cleaned, "sanitized"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    seen = set()          # (id, 生の名前) ごとに1回だけ記録する

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] [NAMEFIX] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            # 記録のせいでゲームを落とさない。
            ctx.log_exc("character name: write failed")

    def echoes(character, raw):
        """名前と同じ生文字列を持つ他の属性。**記録だけ**する（書き換えない）。"""
        found = []
        try:
            for key, value in vars(character).items():
                if key != "name" and value is not None and value == raw:
                    found.append(key)
        except Exception:
            return []
        return found

    def fix(character, where):
        """1人分。直したら True。ゲームが既に正しければ何もしない。"""
        raw = frames.attr(character, "name", None)
        if not isinstance(raw, str):
            return False
        new, reason = sanitize(raw)
        key = (frames.attr(character, "id", None), raw)
        if reason is None:
            return False
        if new is None:
            # 直し方を決めていない種類。記録だけして、ゲームのまま通す。
            if key not in seen:
                seen.add(key)
                write("{}: id={!r} name={!r} is {} -- not touching "
                      "(never observed; no safe replacement)".format(
                          where, key[0], raw, reason))
            return False
        try:
            character.name = new
        except Exception:
            ctx.log_exc("character name: could not rename {!r}".format(raw))
            return False
        if key not in seen:
            seen.add(key)
            extra = echoes(character, raw)
            write("{}: id={!r} {!r} -> {!r}{}".format(
                where, key[0], raw, new,
                "  (same string also in: {})".format(", ".join(sorted(extra)))
                if extra else ""))
        return True

    # ------------------------------------------------------------ 入口を正す
    @ctx.wrap("scripts.characters:Character.__init__")
    def character_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            fix(self, "Character.__init__")
        except Exception:
            # 名前のためにキャラクタ生成を落とさない。画像が出ないだけで遊べる。
            ctx.log_exc("character name: sanitize failed; leaving the name as it is")
        return result

    # ------------------------------------------------------ 既にいる人の救済
    def find_app():
        main = sys.modules.get("__main__")
        cls = getattr(main, "InstantaleApp", None)
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and (cls is None or isinstance(app, cls)):
                return app
        except Exception:
            pass
        if main is not None and isinstance(cls, type):
            for value in vars(main).values():
                if isinstance(value, cls):
                    return value
        return None

    def sweep(app, where):
        """`app.world.characters` と `app.player` を掃く。掃いた人数を返す。

        名簿は `{id: Character}`（TECH.md §7.7）。**保存はしない** ―
        ゲームが次に保存するときに一緒に入る。ここで保存を起こすと、
        こちらが選んだ時点でセーブを書き換えることになる。
        """
        if app is None:
            return 0
        fixed = 0
        world = frames.attr(app, "world", None)
        characters = frames.attr(world, "characters", None) if world is not None else None
        if isinstance(characters, dict):
            for character in list(characters.values()):
                fixed += 1 if fix(character, where) else 0
        elif isinstance(characters, (list, tuple)):
            # 名簿の入れ物は辞書とは限らない（TECH.md §7.8）。
            for character in list(characters):
                fixed += 1 if fix(character, where) else 0
        player = frames.attr(app, "player", None)
        if player is not None:
            fixed += 1 if fix(player, where) else 0
        return fixed

    if SWEEP_ON_LOAD:
        def on_load(target, label):
            @ctx.wrap(target, required=False)
            def _load(orig, self, *args, **kwargs):
                result = orig(self, *args, **kwargs)
                try:
                    sweep(self if frames.attr(self, "world", None) is not None
                          else find_app(), label)
                except Exception:
                    ctx.log_exc("character name: sweep failed")
                return result
            return _load

        on_load("__main__:InstantaleApp.load_game_new", "load_game_new")
        on_load("__main__:InstantaleApp.start_game", "start_game")

    if SWEEP_ON_BOOT:
        try:
            count = sweep(find_app(), "injection")
            if count:
                ctx.log("character name fix: renamed {} character(s) already in the "
                        "world; see out/{}".format(count, LOG_BASENAME))
        except Exception:
            ctx.log_exc("character name: boot sweep failed")

    ctx.log("character name fix: {} -> {} in Character.__init__{}; log={}".format(
        "".join(sorted(REPLACEMENTS)), "".join(REPLACEMENTS[k] for k in sorted(REPLACEMENTS)),
        " and on load" if SWEEP_ON_LOAD else "", log_path))
