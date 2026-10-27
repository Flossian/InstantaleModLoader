# -*- coding: utf-8 -*-
r"""モデルの学習の書き方に合わせたプロンプトの書き換えの雛形（版26）。

ゲームの絵の前置きは SD1.5 のアニメ系に向けて書かれている（GAME.md §2.35）。
SDXL へ移ると作り（寸法・サンプラー・LoRA）はプリセットで揃うが、
プロンプトの書き方はモデルが学習した説明文の形で決まり、同じ SDXL でも系統ごとに違う。

| 雛形 | 画質の印 | 向くモデル |
| --- | --- | --- |
| Illustrious / NoobAI 系 | `masterpiece, best quality, amazing quality, very aesthetic, absurdres` | Danbooru のタグで学習したもの（Illustrious・NoobAI・その混ぜ合わせ） |
| Animagine XL 4.0 | `masterpiece, high score, great score, absurdres`（本文の後ろ） | Animagine XL 4.0（SDXL 1.0 から学習し直した Danbooru 系。画質の印を最後に置く） |
| Pony 系 | `score_9, score_8_up, score_7_up, source_anime` | Pony Diffusion V6 とその派生 |
| 素の SDXL | 足さない | 普通の英文で学習したもの（絵画系など） |

雛形は規則の行の束で、道具画面の「モデル別」のタブから入れる。
入れた行には `model`（いまのチェックポイントのファイル名）と `profile`（雛形の名前）が付き、
同じ雛形を入れ直すと前の行を置き換える（行が増えない）。
規則の書式と当てる順は `rules.py`（除去 → 置換 → 付け替え → 追加）。
"""

#: 立ち絵の性別の判定（書き換える前の本文で見る。`rules.matches` の書式）。
#: ゲームの前置きの category は `young man` / `middle-aged man` / `young woman` のような英語。
MALE_WHEN = "man/boy/male/!woman/!girl/!female/!lady"
FEMALE_WHEN = "woman/girl/female/lady"

#: タグに無い言い方を、意味の合うタグへ置き換える。
#: `plate mail` は `plate`（皿）と読まれて足元に料理が描かれた（実機。VERIFICATION.md §3.65）。
WORDS = (("plate mail", "plate armor"), ("chain mail", "chainmail"))

#: どの雛形でも、ゲームの前置きから外す語。
#: `wallpaper` は壁紙用の絵を表すタグで背景を描き込ませる方向に働き、
#: 黒い背景を切り抜く立ち絵とは逆向き。`empty background` はタグに無い言い方。
PORTRAIT_DROP = "empty background, wallpaper"

#: Danbooru の画材のタグ `watercolor (medium)`。
WATERCOLOR_TAG = r"watercolor \(medium\)"

_NEG_COMMON ="lowres, bad anatomy, bad hands, extra digits, fewer digits, signature, watermark, username, blurry"


def _rows(quality, negative_quality, game_quality, quality_last=False):
    """Danbooru 系の雛形の行。画質の印だけが系統で違う。

    `quality_last` は画質の印を本文の後ろ（追加の最後）へ置く。Animagine XL 4.0 は
    「人数・キャラ・作品・レーティング・ほかのタグ、最後に画質」の順で学習している。
    """
    head = "" if quality_last else quality + ", "
    rows = {
        # 読み違えられる言い方を、意味の合うタグへ置き換える（版26。`rules.py`）。
        # タグとの照合（alias）は入れない。試した範囲で絵の良し悪しの差が見えなかった（VERIFICATION.md §3.65）。
        "words": [{"kind": "any", "target": "prompt", "from": a, "to": b} for a, b in WORDS],
        "remove": [
            {"kind": "portrait", "target": "prompt",
             "text": "full-body, {}, {}".format(game_quality, PORTRAIT_DROP)},
            # 敵の前置きの `creature` は Danbooru では小さな生き物のタグで、
            # 人の敵の横にも小動物が描かれた（実機。VERIFICATION.md §3.65）。
            {"kind": "enemy", "target": "prompt", "text": "{}, creature".format(game_quality)},
            # 敵の否定にはゲームが `anime` を入れている。アニメ調のモデルでは画風ごと遠ざける。
            {"kind": "enemy", "target": "negative", "text": "anime"},
            {"kind": "background", "target": "prompt",
             "text": "{}, indoor, outdoor".format(game_quality)},
        ],
        # 置換は上から順に重なる。後の行ほど前へ付く（画質が先頭の雛形は画質 → 1boy の順、
        # 画質が最後の雛形は 1boy → full body の順にする）。
        "replace": ([] if not quality_last else [
            {"kind": "portrait", "target": "prompt", "text": "full body, {prompt}"},
        ]) + [
            {"kind": "portrait", "target": "prompt", "when": MALE_WHEN, "text": "1boy, {prompt}"},
            {"kind": "portrait", "target": "prompt", "when": FEMALE_WHEN, "text": "1girl, {prompt}"},
        ] + ([] if quality_last else [
            {"kind": "portrait", "target": "prompt",
             "text": "{}, full body, {{prompt}}".format(quality)},
        ]) + [
            {"kind": "background", "target": "prompt",
             "text": "{}no humans, scenery, {{prompt}}".format(head)},
        ] + ([] if quality_last else [
            {"kind": "enemy", "target": "prompt", "text": "{}, {{prompt}}".format(quality)},
        ]),
        "add": [
            {"kind": "portrait", "target": "prompt", "text": "simple background"},
            # 括弧は強調の書式として読まれるので、タグの括弧は `\(` `\)` で書く。
            {"kind": "any", "target": "prompt", "when": "watercolor", "text": WATERCOLOR_TAG},
            {"kind": "background", "target": "prompt", "when": "indoor", "text": "indoors"},
            {"kind": "background", "target": "prompt", "when": "outdoor", "text": "outdoors"},
            {"kind": "portrait", "target": "negative",
             "text": "{}, {}, multiple views, monochrome, greyscale".format(negative_quality, _NEG_COMMON)
             if negative_quality else "{}, multiple views, monochrome, greyscale".format(_NEG_COMMON)},
            {"kind": "enemy", "target": "negative",
             "text": "{}, {}".format(negative_quality, _NEG_COMMON)
             if negative_quality else _NEG_COMMON},
            {"kind": "background", "target": "negative",
             "text": "{}solo, multiple girls, multiple boys, lowres, worst quality, low quality, "
                     "signature, watermark, text".format(
                         negative_quality + ", " if negative_quality else "")},
        ],
    }
    if quality_last:
        rows["add"].append({"kind": "any", "target": "prompt", "text": quality})
    return rows


#: ゲームの前置きにある画質の語（除去の対象。区画単位の完全一致で外す）。
GAME_QUALITY = "masterpiece, high quality, best quality"

def _animagine_rows():
    """Animagine XL 4.0 の雛形の行。

    ゲームは大きさ huge の敵の否定に `detailed background` を足す。Animagine はこれで
    huge の敵を色の面だけの絵に崩した（溶岩の竜・要塞の守護機・時空の番人の3体とも、種3つすべて）。
    前置きの語（`concept drawing` / `transparent background` / `huge, monster`）を外しても、
    サンプラーを替えても、寸法を 1024x704 に下げても直らず、否定のこの語を外すと直った。
    外すと背景が描かれるので、代わりに `simple background` を足す（9枚とも黒い背景で描けた。
    VERIFICATION.md §3.65）。
    """
    rows = _rows("masterpiece, high score, great score, absurdres",
                 "worst quality, low quality, low score, bad score, average score",
                 GAME_QUALITY, quality_last=True)
    rows["remove"].append({"kind": "enemy", "target": "negative", "text": "detailed background"})
    rows["add"].insert(0, {"kind": "enemy", "target": "prompt", "text": "simple background"})
    return rows


#: ゲームの否定の画質の語。`(nsfw, worst quality, low quality:1.4)` のように 1.4 倍で書かれている。
GAME_NEGATIVE_QUALITY = "nsfw, worst quality, low quality"


def _pony_rows():
    """Pony 系の雛形の行。

    ゲームの否定の 1.4 倍の画質の語が、Prefect Pony XL v6 の絵を崩した
    （ゲームの否定だけでは一面のノイズ、雛形の否定と合わせると輪郭の太い原色の絵）。
    同じ語を重み無しで置くと、作者の作例と同じ絵柄で描けた（VERIFICATION.md §3.65）。
    除去は重みの括弧の中まで届き、空になった括弧ごと落ちるので、外してから重み無しで足す。
    """
    rows = _rows("score_9, score_8_up, score_7_up, source_anime",
                 "score_6, score_5, score_4, source_pony, source_furry", GAME_QUALITY)
    rows["remove"].append({"kind": "any", "target": "negative", "text": GAME_NEGATIVE_QUALITY})
    rows["add"].append({"kind": "any", "target": "negative", "text": GAME_NEGATIVE_QUALITY})
    return rows


PROFILES = (
    {
        "key": "illustrious",
        "label": "Illustrious / NoobAI 系",
        "about": "Danbooru のタグで学習したモデル向け。画質の印を Illustrious の形に替え、"
                 "立ち絵に 1boy / 1girl、背景に no humans, scenery を足し、wallpaper を外す。"
                 "立ち絵の否定に monochrome を足し、敵の creature と否定の anime を外す。"
                 "ゲームの語の plate mail を plate armor に置き換える（皿と読まれたため）",
        "rows": _rows("masterpiece, best quality, amazing quality, very aesthetic, absurdres",
                      "", GAME_QUALITY),
    },
    {
        "key": "animagine",
        "label": "Animagine XL 4.0",
        "about": "Animagine XL 4.0 向け。画質の印を本文の後ろに masterpiece, high score, great score, absurdres の形で置き、"
                 "否定に low score, bad score, average score を足す。"
                 "大きい敵の否定の detailed background を外し、代わりに simple background を足す（絵が崩れるため）。"
                 "ほかは Illustrious / NoobAI 系と同じ",
        "rows": _animagine_rows(),
    },
    {
        "key": "pony",
        "label": "Pony 系",
        "about": "Pony Diffusion V6 の派生向け。画質の印を score_9, score_8_up, score_7_up, source_anime に替え、"
                 "否定に score_6, score_5, score_4 を足す。"
                 "ゲームの否定の 1.4 倍の nsfw, worst quality, low quality を重み無しにする（絵が崩れるため）。"
                 "ほかは Illustrious / NoobAI 系と同じ",
        "rows": _pony_rows(),
    },
    {
        "key": "plain",
        "label": "素の SDXL",
        "about": "普通の英文で学習したモデル向け。立ち絵の wallpaper と empty background を外し、"
                 "否定に崩れの語を足すだけ",
        "rows": {
            "remove": [{"kind": "portrait", "target": "prompt", "text": PORTRAIT_DROP}],
            "add": [
                {"kind": "portrait", "target": "prompt", "text": "plain black background"},
                {"kind": "any", "target": "negative", "text": "lowres, blurry, watermark, text"},
            ],
        },
    },
)

#: 雛形の行が入る節。
SECTIONS = ("words", "tags", "remove", "replace", "add", "skip_upscale")


def by_key(key):
    for profile in PROFILES:
        if profile["key"] == key:
            return profile
    raise KeyError(key)


def model_condition(checkpoint_path):
    """雛形の行に書く `model`。チェックポイントを指していればファイル名、無ければ `sdxl`。

    Illustrious と Pony は系統（SDXL）では分けられないので、ファイル名で分ける。
    """
    import os
    name = os.path.splitext(os.path.basename(str(checkpoint_path or "").strip()))[0]
    return name or "sdxl"


def rows_of(key, model):
    """雛形の行を `(節, 行)` の並びで。行には `enabled` / `model` / `profile` が付く。"""
    profile = by_key(key)
    out = []
    for section in SECTIONS:
        for row in profile["rows"].get(section) or []:
            got = dict(row)
            got.update({"enabled": True, "model": model, "profile": key})
            out.append((section, got))
    return out


def put(rules, key, model):
    """規則へ雛形を入れる。同じ雛形と同じ `model` の行は置き換える（入れ直しても増えない）。

    別のモデルに入れた同じ雛形の行は残す（モデルを行き来しても書き直さずに済むように）。
    `rules` は書き換えた写しを返す。
    """
    got = dict((name, list(rows or [])) for name, rows in rules.items())
    for section in SECTIONS:
        got[section] = [row for row in got.get(section) or []
                        if not (row.get("profile") == key
                                and str(row.get("model") or "") == model)]
    for section, row in rows_of(key, model):
        got.setdefault(section, []).append(row)
    return got
