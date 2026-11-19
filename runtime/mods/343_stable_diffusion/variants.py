# -*- coding: utf-8 -*-
r"""会話の立ち絵の表情差分を描くための部品。DOC.md「表情差分」。

差分は、描き終えた立ち絵（`generated_image.png`）の顔の周りを、表情のタグを足して弱い強さで
描き直して貼った絵。顔だけ描き直す（`faces.py`）と同じ切り出し・貼り戻しを使う。
描くのは表情を見せる MOD（`347_` など）がローダの窓口 `expressions` に宣言しているときだけ。

貼った絵は、ゲームの立ち絵と同じ工程（縮小 → 2倍 → 減色）を通して `expressions\<表情>.png` に置く。
縮小と減色はゲームの関数を、ゲームが立ち絵に渡したのと同じ引数で呼ぶ（`131_` が入なら高画質のまま通る）。
2倍の段はゲームのコンパイル済みのコードの中にあって呼べないので、最近傍の2倍をここでする（GAME.md §2.30）。
背景の透明度は、ゲームが抜いた元の絵（`no_bg_image.png`）のものを使う（顔の周りの描き直しで輪郭はほぼ動かない）。

描く順番は `Turn` で決める。ゲームの絵も差分も、1枚描くあいだは同じ順番を持つ。
差分は1枚ごとに順番を放し、ゲームが待っていれば先に譲る。

一括生成では、立ち絵が既にある人物にも差分だけ描く（ゲームの仕事が済んだ後）。
立ち絵を描かないので、ゲームが立ち絵を描いたときの呼び方（部品の引数と工程の引数）を
`state\stable_diffusion\expression_template.json` に控えておき、文は人物のフォルダの `prompts.json` から取る。

表情は基本表情（笑う・怒る・悲しむ・驚く。タグは設定）と、追加の表情（`state\stable_diffusion\expressions.json`。
名前・意味・タグを道具画面で編む）。タグのある表情を、ローダの窓口に一覧として登録する（`entry_rows`）。
タグを変えた表情は、印のタグと比べて次の一括生成で描き直す（ローダの窓口の `expressions.plan`）。
"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import time

try:
    from . import faces, img2img, rules
except ImportError:          # 道具画面（tool.py）は隣のファイルを直に import する
    import faces
    import img2img
    import rules

#: 設定の名前（mod.json）。`live.py` が生成のたびに読み直す。
STRENGTH = "EXPRESSION_STRENGTH"
STRENGTH_SD15 = "EXPRESSION_STRENGTH_SD15"
BY_MODEL = "EXPRESSION_STRENGTH_BY_MODEL"
TAGS = (("smile", "EXPRESSION_SMILE"), ("angry", "EXPRESSION_ANGRY"),
        ("sad", "EXPRESSION_SAD"), ("surprised", "EXPRESSION_SURPRISED"))
NAMES = (STRENGTH_SD15, STRENGTH, BY_MODEL) + tuple(name for _expression, name in TAGS)

#: ふだんの顔（差分を描かない。ローダの窓口の `expressions.NEUTRAL` と同じ）。
NEUTRAL = "neutral"

#: 基本表情の名前（ふだんの顔と `TAGS` の表情。ローダの窓口の `expressions.NAMES` と同じ並び）。
BUILTIN = (NEUTRAL,) + tuple(expression for expression, _name in TAGS)

#: 追加の表情の置き場（`state\stable_diffusion\` の下。道具画面で編む）。
ADDED_NAME = "expressions.json"

#: 置き場が無いときの追加の表情（見本を兼ねる。道具画面で消して保存すれば、空の一覧が置き場に残って戻らない）。
SAMPLE_ADDED = ({"id": "x1", "name": "shy", "meaning": "照れる・恥ずかしがる", "tags": "blush, embarrassed"},)

#: 追加の表情の名前に使える形（ローダの窓口の `expressions.NAME_PATTERN` と同じ）。
ADDED_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: 1表情を描くのにかかる時間の目安（秒。SDXL の一括生成で差分4枚が1人約27秒。VERIFICATION.md §3.132）。
SECONDS_PER_EXPRESSION = 7

#: SDXL・その他のモデルの強さの既定（mod.json と同じ値。決めた経緯は VERIFICATION.md §3.132）。
DEFAULT_STRENGTH = 0.35

#: SD1.5 のモデルの強さの既定（mod.json と同じ値。0.45 以下では表情がほとんど付かなかった。§3.132）。
DEFAULT_SD15 = 0.5

#: モデル名で分ける強さの既定（空。SD1.5・SDXL の欄より先に見る）。
DEFAULT_BY_MODEL = ""

#: 強さがどの欄で決まったかの言い方（道具画面とログ）。
LABEL_SD15 = "SD1.5 のモデル"
LABEL_OTHER = "SDXL・その他のモデル"
LABEL_BY_NAME = "モデル名で分ける"

#: 表情のタグの既定（mod.json と同じ値）。
DEFAULT_TAGS = {"smile": "light smile, closed mouth",
                "angry": "annoyed, slight frown",
                "sad": "sad, worried",
                "surprised": "surprised, :o"}

#: 描く順番の置き場（`sys` の属性。注入し直しても、描いている途中の古い仕事と同じ順番を使う）。
TURN_ATTR = "_mod343_draw_turn"

#: 一括生成の印・工程の引数の控え・差分だけ描く関数の置き場（`sys` の属性）。
#: 一括生成のモジュールは「開始／再開」の後に読み込まれ、ローダが MOD を当て直すので、
#: モジュールの変数に持つと走っている実行の印が 0 に戻る（GAME.md の一括生成。VERIFICATION.md §3.132）。
SHARED_ATTR = "_mod343_variants"

#: ゲームが立ち絵を描いたときの呼び方の控え（`state\stable_diffusion\` の下）。
TEMPLATE_NAME = "expression_template.json"

#: 控えに残さない部品の引数（人物ごとに変わるもの・絵）。
TEMPLATE_SKIP = ("self", "prompt", "negative_prompt", "init_image", "strength", "mask_image",
                 "control_image")

#: 人物のフォルダの、ゲームが立ち絵を描いたときの文（GAME.md の立ち絵の置き場）。
PROMPTS_NAME = "prompts.json"


def shared():
    """当て直しをまたぐ置き場。`{"depth": 一括生成の数, "steps": 工程の引数, "backfill": 差分だけ描く関数}`。"""
    got = getattr(sys, SHARED_ATTR, None)
    if not isinstance(got, dict):
        got = {}
        setattr(sys, SHARED_ATTR, got)
    got.setdefault("depth", 0)
    got.setdefault("steps", {})
    got.setdefault("backfill", None)
    return got


def _scalar(value):
    return value is None or isinstance(value, (bool, int, float, str))


def template_params(arguments):
    """部品に渡った引数から、控えに残すもの（数・文字列・真偽だけ。人物ごとに変わるものと絵は除く）。"""
    return dict((name, value) for name, value in (arguments or {}).items()
                if name not in TEMPLATE_SKIP and _scalar(value))


def step_call(args, kwargs):
    """工程の引数を控えの形 `[位置引数, 名前つき引数]` にする。数・文字列以外が混じれば None。"""
    args, kwargs = list(args or ()), dict(kwargs or {})
    if not all(_scalar(v) for v in args) or not all(_scalar(v) for v in kwargs.values()):
        return None
    return [args, kwargs]


def steps_from(template, names):
    """控えから工程の引数 `{関数名: (位置引数, 名前つき引数)}`。どれかが無ければ None。"""
    steps = (template or {}).get("steps")
    if not isinstance(steps, dict):
        return None
    got = {}
    for name in names:
        row = steps.get(name)
        if not (isinstance(row, list) and len(row) == 2 and isinstance(row[0], list)
                and isinstance(row[1], dict)):
            return None
        got[name] = (tuple(row[0]), dict(row[1]))
    return got


def load_template(path):
    """呼び方の控え。無い・壊れているなら None。"""
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def save_template(path, data):
    """呼び方の控えを書く（ローダの `write_json`。ゲームの中でだけ呼ぶので、道具画面のためにここで import する）。書けたか。"""
    from instantale_modloader import write_json
    return write_json(path, data, sort_keys=True)


def read_prompts(folder):
    """人物のフォルダの `prompts.json` の `(文, 否定の文)`。無い・読めないなら None。"""
    got = load_template(os.path.join(folder, PROMPTS_NAME))
    if not got or not isinstance(got.get("positive_prompt"), str) or not got["positive_prompt"].strip():
        return None
    negative = got.get("negative_prompt")
    return got["positive_prompt"], negative if isinstance(negative, str) else ""


def world_npc_names(world_data, save_data=None):
    """世界の骨格（とセーブ）の人物の名前。骨格の順、セーブにだけ居る人物は後ろに足す。重複は除く。"""
    names = []
    for data in (world_data, save_data):
        npcs = (data or {}).get("npcs") if isinstance(data, dict) else None
        rows = npcs.values() if isinstance(npcs, dict) else npcs if isinstance(npcs, list) else ()
        for row in rows:
            name = row.get("name") if isinstance(row, dict) else None
            if isinstance(name, str) and name.strip() and name not in names:
                names.append(name)
    return names


def needs_variants(folder, expressions, wanted=None):
    """差分に手を入れる人物か（立ち絵の元の絵があり、その印に合う差分の控えが無いか、控えが描き揃っていないか、
    描き足す・タグの変わった・名前の変わった・消す表情がある）。

    `expressions` はローダの窓口（道具画面からこのファイルを読むときに要らない import を避けて渡す）。
    `wanted` は描くはずの表情（`wanted_rows`）。省けば印の合い方と揃い方だけ見る。
    """
    if wanted is not None:
        return expressions.needs(folder, wanted)
    stamp = expressions.stamp(folder)
    return stamp is not None and (expressions.sealed(folder) != stamp or not expressions.complete(folder))


# ---- 追加の表情 --------------------------------------------------------------

def added_path(state_dir):
    r"""追加の表情の置き場（`state\stable_diffusion\expressions.json`）。"""
    return os.path.join(state_dir, rules.STATE_DIRNAME, ADDED_NAME)


def clean_text(value):
    """1行の文字列にする（改行と続いた空白を1つの空白に）。"""
    return " ".join(str(value or "").split())


def check_added(rows):
    """追加の表情の行の誤り（行の番号は1から）。無ければ空。

    名前は英小文字で始まる英小文字・数字・`_`（32字まで）で、基本表情と、ほかの行と重ならない。
    タグのある行は意味が要る（LLM に渡す説明になる）。
    """
    errors, seen = [], set()
    for index, row in enumerate(rows, 1):
        name = clean_text(row.get("name"))
        if not ADDED_PATTERN.match(name):
            errors.append("{} 行目: 名前は英小文字で始まる英小文字・数字・_ にする（32字まで）: {!r}".format(index, name))
        elif name in BUILTIN:
            errors.append("{} 行目: 名前 {} は基本表情と重なる".format(index, name))
        elif name in seen:
            errors.append("{} 行目: 名前 {} はほかの行と重なる".format(index, name))
        seen.add(name)
        if clean_text(row.get("tags")) and not clean_text(row.get("meaning")):
            errors.append("{} 行目: 意味が空（LLM に渡す説明になる）".format(index))
    return errors


def new_id(taken):
    """追加の表情の id（`taken` に無いもの）。名前を変えても同じ表情と分かるように、行に1つ持たせる。"""
    number = 1
    while "x{}".format(number) in taken:
        number += 1
    return "x{}".format(number)


def normalize_added(rows):
    """追加の表情の行を、置き場に書く形 `[{"id", "name", "meaning", "tags"}]` にする。id の無い行には付ける。"""
    out, taken = [], set(clean_text(row.get("id")) for row in rows if isinstance(row, dict))
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = clean_text(row.get("id"))
        if not ident or any(ident == got["id"] for got in out):
            ident = new_id(taken | set(got["id"] for got in out))
            taken.add(ident)
        out.append({"id": ident, "name": clean_text(row.get("name")), "meaning": clean_text(row.get("meaning")),
                    "tags": clean_text(row.get("tags"))})
    return out


def load_added(path):
    """追加の表情の行（`normalize_added` の形）。無いなら `SAMPLE_ADDED`、壊れているなら空。"""
    if not os.path.exists(path):
        return normalize_added([dict(row) for row in SAMPLE_ADDED])
    got = load_template(path)
    rows = got.get("expressions") if isinstance(got, dict) else None
    return normalize_added(rows) if isinstance(rows, list) else []


def save_added(path, rows):
    """追加の表情を書く（隣に書いてから差し替える）。書けたか。"""
    body = json.dumps({"expressions": normalize_added(rows)}, ensure_ascii=False, indent=1)
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
        with io.open(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body + "\n")
        os.replace(temp, path)
        return True
    except OSError:
        return False


def wanted_rows(conf):
    """描くはずの表情（ローダの窓口の `expressions.plan` に渡す形）。タグのある表情だけ、基本表情が先。"""
    return [dict({"name": name, "tags": tags}, **({"id": conf["ids"][name]} if conf["ids"].get(name) else {}))
            for name, tags in conf["tags"].items()]


def entry_rows(conf):
    """ローダの窓口に登録する表情の一覧（`expressions.define`）。基本表情の意味は窓口のものを使う。"""
    return [dict({"name": name}, **({"meaning": conf["meanings"][name]} if conf["meanings"].get(name) else {}))
            for name in conf["tags"]]


#: 一括生成の画面に出す、何を描いているかの言い方。
PROGRESS_VARIANTS = "表情差分"
PROGRESS_FACE = "顔の描き直し"


def progress_state(completed, remaining, failed, name, what=PROGRESS_VARIANTS):
    """一括生成の画面へ渡す件数（`WorldBulkScreen.update_progress` が読む形。ゲームの仕事と同じ鍵）。"""
    return {"status": "running", "completed": completed, "remaining": remaining, "failed": failed,
            "kind": "portrait", "label": "{}（{}）".format(name, what)}


def strength_of(value, default=DEFAULT_STRENGTH):
    """強さを 0 より大きく 1 以下に収める。読めなければ `default`（`img2img.strength_of`）。"""
    return img2img.strength_of(value, default)


def model_rows(text):
    """モデルごとの強さの欄を `[(条件, 強さ), ...]` と読めなかった区切りの並びにする。

    書き方は `条件=強さ` を `;` か改行で区切る（`sd15=0.5; pony=0.4`）。
    条件は規則の `model` 列と同じ（`rules.model_matches`）。強さが読めない区切りは捨てて返す。
    """
    got, bad = [], []
    for part in str(text or "").replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        condition, sep, value = part.rpartition("=")
        condition = condition.strip()
        try:
            strength = float(value)
        except ValueError:
            strength = None
        if not sep or not condition or strength is None or strength != strength or not 0 < strength <= 1:
            bad.append(part)
            continue
        got.append((condition, strength))
    return got, bad


def derive(values, added=None):
    """設定の値と追加の表情を描く形に組む。空のタグの表情は描かない（LLM にも選ばせない）。

    `{"strength": SDXL・その他の強さ, "sd15": SD1.5 の強さ, "by_model": [(条件, 強さ)], "bad": [読めなかった区切り],
    "tags": {表情: タグ}（基本表情が先、追加の表情は行の順）, "ids": {追加の表情: id}, "meanings": {追加の表情: 意味},
    "skipped": [使えない追加の表情の名前]}`。
    名前が使えない・重なる追加の表情は飛ばす（道具画面は保存の前に断る。手で書き換えた置き場のため）。
    """
    tags, ids, meanings, skipped = {}, {}, {}, []
    for expression, name in TAGS:
        text = values.get(name)
        text = str(DEFAULT_TAGS[expression] if text is None else text).strip()
        if text:
            tags[expression] = text
    seen = set(BUILTIN)
    for row in added or ():
        name, text = row.get("name"), clean_text(row.get("tags"))
        if not isinstance(name, str) or not ADDED_PATTERN.match(name) or name in seen:
            skipped.append(str(name))
            continue
        seen.add(name)
        if text:
            tags[name] = text
            ids[name] = row.get("id") or ""
            meanings[name] = clean_text(row.get("meaning"))
    by_model = values.get(BY_MODEL)
    rows, bad = model_rows(DEFAULT_BY_MODEL if by_model is None else by_model)
    return {"strength": strength_of(values.get(STRENGTH)),
            "sd15": strength_of(values.get(STRENGTH_SD15), DEFAULT_SD15),
            "by_model": rows, "bad": bad, "tags": tags, "ids": ids, "meanings": meanings, "skipped": skipped}


def strength_for(conf, model):
    """描くモデル（`(系統, チェックポイントのパス)`）で使う強さと、どの欄で決まったかの言い方。

    「モデル名で分ける」の行を前から見て最初に当たった行。無ければ SD1.5 の系統なら SD1.5 の欄、
    ほか（SDXL・読めない系統）は SDXL・その他の欄。
    """
    for condition, strength in conf.get("by_model") or ():
        if rules.model_matches(condition, model):
            return strength, "{}: {}={}".format(LABEL_BY_NAME, condition, strength)
    family = model[0] if isinstance(model, tuple) and model else None
    if str(family or "").lower() == "sd15":
        return conf.get("sd15", DEFAULT_SD15), LABEL_SD15
    return conf["strength"], LABEL_OTHER


def expression_prompt(prompt, tags):
    """顔の寄りのプロンプト（`faces.face_prompt`）の、顔の寄りのタグの直後に表情のタグを入れる。"""
    got = faces.face_prompt(prompt)
    if not isinstance(got, str) or not tags:
        return got
    head = faces.FACE_TAGS
    at = got.find(head)
    if at < 0:
        return "{}, {}".format(tags, got)
    at += len(head)
    return "{}, {}{}".format(got[:at], tags, got[at:])


# ---- 描く順番 --------------------------------------------------------------

class Turn(object):
    """描く順番。ゲームの絵は差分より先に描く。

    同じスレッドが持っている間に入り直しても待たない（出口の中から出口が呼ばれても止まらない）。
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._owner = None
        self._depth = 0
        self._game_waiting = 0

    def _take(self, game):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return
            if game:
                self._game_waiting += 1
            try:
                while self._owner is not None or (not game and self._game_waiting):
                    self._cond.wait()
            finally:
                if game:
                    self._game_waiting -= 1
            self._owner, self._depth = me, 1

    def _give(self):
        with self._cond:
            self._depth -= 1
            if self._depth <= 0:
                self._owner, self._depth = None, 0
                self._cond.notify_all()

    def game(self):
        """ゲームの絵を描くあいだ持つ。"""
        return _Holding(self, True)

    def job(self):
        """差分を1枚描くあいだ持つ。ゲームが待っていれば、ゲームが描き終えるまで待つ。"""
        return _Holding(self, False)

    def busy(self):
        with self._cond:
            return self._owner is not None or bool(self._game_waiting)


class _Holding(object):
    def __init__(self, turn, game):
        self._turn, self._game = turn, game

    def __enter__(self):
        self._turn._take(self._game)
        return self

    def __exit__(self, *_exc):
        self._turn._give()
        return False


def turn():
    """プロセスに1つの描く順番。

    クラスでは照合しない。このファイルは注入のたびに読み直され `Turn` は別のクラスになるので、
    照合すると注入し直した世代が錠を作り直し、走り続ける古い世代の仕事と同時に描く。
    """
    got = getattr(sys, TURN_ATTR, None)
    if got is None:
        return sys.__dict__.setdefault(TURN_ATTR, Turn())
    if not (callable(getattr(got, "game", None)) and callable(getattr(got, "job", None))):
        got = Turn()
        setattr(sys, TURN_ATTR, got)
    return got


# ---- 工程 --------------------------------------------------------------------

def in_fresh_thread(fn, name="mod343-expression-step"):
    """`fn()` を新しいスレッドで走らせて待ち、戻り値を返す（例外はそのまま投げ直す）。

    `131_` は縮小と減色の控えと旗をスレッドに置き、`縮小 → 減色 → （顔の代わり）縮小` の順で読む。
    同じスレッドで2枚目を通すと、2回目の縮小を顔の代わりの回と取り違えるので、1枚ずつ新しいスレッドで通す。
    """
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:           # 呼んだ側で投げ直す
            box["error"] = exc
    worker = threading.Thread(target=run, name=name, daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def with_alpha(image, cutout):
    """`image` に、背景を抜いた絵 `cutout` の透明度を付けた RGBA。寸法が違えば透明度を合わせる。"""
    from PIL import Image

    got = image.convert("RGBA")
    alpha = cutout.convert("RGBA").getchannel("A")
    if alpha.size != got.size:
        alpha = alpha.resize(got.size, Image.LANCZOS)
    got.putalpha(alpha)
    return got


def game_steps(image, pixel, reduce, pixel_call, reduce_call):
    """ゲームの立ち絵と同じ工程（縮小 → 2倍 → 減色）を通した絵。

    `pixel_call` / `reduce_call` はゲームが立ち絵に渡した引数 `(位置引数, 名前つき引数)`（絵を除く）。
    """
    return game_step_images(image, pixel, reduce, pixel_call, reduce_call)[1]


def game_step_images(image, pixel, reduce, pixel_call, reduce_call):
    """`game_steps` の途中の絵も返す。`(縮めた絵, 立ち絵)`。

    ゲームは縮めた絵を `pixelated_image_original.png`、立ち絵を `reduced_color_image.png` に書く（GAME.md §2.30）。
    """
    from PIL import Image

    pargs, pkwargs = pixel_call
    rargs, rkwargs = reduce_call
    _big, small = pixel(image, *pargs, **pkwargs)
    doubled = small.resize((small.size[0] * 2, small.size[1] * 2), Image.NEAREST)
    return small, reduce(doubled, *rargs, **rkwargs)


#: 絵の差し替えを PermissionError でやり直す回数と間隔（秒）。見せる側（Kivy）が絵を開いている間は拒まれる
#: （ローダの `write_text` と同じ値）。
REPLACE_RETRIES = 5
REPLACE_RETRY_WAIT = 0.05


def save_replacing(image, path):
    """隣に書いてから差し替える（見せる側が書きかけを読まないため）。

    一時ファイルの名前は毎回変える（同じ絵へ2本が同時に書いても混ざらない）。
    """
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    os.close(handle)
    try:
        image.save(temp, format="PNG")
        for _ in range(REPLACE_RETRIES - 1):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                time.sleep(REPLACE_RETRY_WAIT)
        os.replace(temp, path)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
