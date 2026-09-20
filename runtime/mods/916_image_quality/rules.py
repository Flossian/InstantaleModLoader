# -*- coding: utf-8 -*-
r"""プロンプトの書き換え規則。MOD 本体と道具画面で共有する。

元の `InstantaleStableDiffusionMod` の ini の次の節に当たる。

    [lora_map]                    LoRA の付け替え・無効化
    [lora_add] / [lora_add_if]    種類別の追加と、条件つきの追加
    [negative_add]                ネガティブ側の追加
    [prompt_remove] / [negative_remove]   タグの除去
    [prompt_replace] / [negative_replace] 丸ごと置き換え（`{prompt}` で元を埋める）
    [upscale] の skip_if           条件に当たった生成は寸法を触らない

**当てる順は元 MOD と同じ**（`proxy.c` の処理順）。

    除去 → 置換 → 付け替え → 追加

条件（`when`）は**書き換える前のゲーム本来のプロンプト**で判定する。
除去や置換で消した語が条件に効いてしまうと、規則を足した順で結果が変わるため。

規則は表なのでローダの設定には宣言できない（TECH.md §3.8.2）。
`state\image_quality\prompt_rules.json` に持ち、道具画面で編む。

    {
      "lora_map":     [{"enabled": true, "from": "LCM_LoRA_Weights_SD15", "to": "off"}],
      "remove":       [{"enabled": true, "kind": "portrait", "target": "prompt",
                        "text": "medieval, watercolor"}],
      "replace":      [{"enabled": true, "kind": "portrait", "target": "prompt",
                        "text": "1girl, {prompt}, masterpiece"}],
      "add":          [{"enabled": true, "kind": "portrait", "target": "prompt",
                        "when": "1boy/male/!girl", "text": "<lora:maleStyle:0.8>"}],
      "skip_upscale": [{"enabled": true, "kind": "any", "when": "pixel art/sprite"}]
    }

`kind` は `portrait` / `enemy` / `background` / `any`。
元 MOD は縦横比から `portrait` / `landscape` / `square` を推測していたが、
こちらは呼び手で分かる（GAME.md §2.33）。
"""
import io
import json
import os
import re

#: 規則の置き場（`state\` の下。`322_battle_bgm` の playlist と同じ持ち方）。
STATE_DIRNAME = "image_quality"
RULES_NAME = "prompt_rules.json"

#: 表の種類と、その行が持つ項目。道具画面もこれを見て欄を作る。
KINDS = ("any", "portrait", "enemy", "background")
TARGETS = ("prompt", "negative")
SECTIONS = {
    "lora_map": ("from", "to"),
    "remove": ("kind", "target", "text"),
    "replace": ("kind", "target", "text"),
    "add": ("kind", "target", "when", "text"),
    "skip_upscale": ("kind", "when"),
}

#: `<lora:名前:重み>` のタグ。名前だけ・重みつきの両方が来る。
LORA_TAG = re.compile(r"<lora:([^:>]+)(:[^>]*)?>")

#: 置き換えの中で元のプロンプトを埋める場所。
PLACEHOLDER = "{prompt}"


def empty():
    """空の規則。読めないときもこの形に倒す。"""
    return dict((name, []) for name in SECTIONS)


def rules_path(state_dir):
    return os.path.join(state_dir, STATE_DIRNAME, RULES_NAME)


def load(state_dir):
    """規則を読む。無い・壊れているなら空（＝何もしない）。"""
    try:
        with io.open(rules_path(state_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return empty()
    got = empty()
    if not isinstance(data, dict):
        return got
    for name in SECTIONS:
        rows = data.get(name)
        if isinstance(rows, list):
            got[name] = [row for row in rows if isinstance(row, dict)]
    return got


def save(state_dir, data):
    """規則を書く。書けたかを返す（呼び側が画面に出す）。"""
    path = rules_path(state_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
        return True
    except Exception:
        return False


#: 「種類で絞らない」を表す番人。`kind=None` は**種類が分からない**の意味で、
#: そのときは `any` の行だけが当たる（分からない回に立ち絵専用の置換を当てないため）。
ALL_KINDS = object()


def active(rules, name, kind=ALL_KINDS, target=None):
    """効いている行だけ。`kind` と `target` で絞る（`any` の行は全部に当たる）。

    `kind` が `None`（入口の印が無く種類が分からない回）なら `any` の行だけ。
    """
    for row in rules.get(name) or []:
        if not row.get("enabled", True):
            continue
        if kind is not ALL_KINDS:
            row_kind = str(row.get("kind") or "any")
            if row_kind != "any" and row_kind != kind:
                continue
        if target is not None and str(row.get("target") or "prompt") != target:
            continue
        yield row


def matches(condition, text):
    """条件が本文に当たるか。書式は元 MOD の `[lora_add_if]` と同じ。

    `/` 区切りでどれか1つ含まれれば成立。
    先頭が `!` の語は「含まれない」ことが条件で、**すべて**満たす必要がある。
    判定は単語境界つき・大文字小文字を問わない
    （`male` は `female` に、`man` は `woman` に当たらない）。
    条件が空なら常に成立。
    """
    words = [w.strip() for w in str(condition or "").split("/") if w.strip()]
    if not words:
        return True
    body = text or ""
    wanted, unwanted = [], []
    for word in words:
        (unwanted if word.startswith("!") else wanted).append(word.lstrip("!").strip())
    for word in unwanted:
        if word and _contains(body, word):
            return False
    if not wanted:
        return True
    return any(_contains(body, word) for word in wanted)


def _contains(body, word):
    """単語境界つきの部分一致（大文字小文字を問わない）。"""
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", body,
                     re.IGNORECASE) is not None


def split_tags(text):
    """カンマ区切りの1区画ずつ。前後の空白は落とす。"""
    return [part.strip() for part in str(text or "").split(",")]


#: 区画の飾り。先頭の括弧、本体、末尾の `:重み` と閉じ括弧。
#: ゲームのネガティブは `(nsfw, worst quality, low quality:1.4)` のように
#: 重みの括弧でまとめて来るので、区画をそのまま比べると `(nsfw` になって当たらない
#: （実機 2026-09-20: `nsfw` の除去が1件も効かなかった）。
_DRESSED = re.compile(r"^([\(\[\{]*)(.*?)((?::[\d.]+)?[\)\]\}]*)$")


def bare(tag):
    """区画の飾りを外した `(先頭の括弧, 本体, 末尾)`。本体で比べる。"""
    found = _DRESSED.match(tag.strip())
    return found.group(1), found.group(2).strip(), found.group(3)


def remove_tags(body, spec):
    """指定したタグを取り除く。1区画単位・大文字小文字を問わない完全一致。

    比べるのは飾りを外した本体（`(nsfw` も `nsfw:1.2)` も `nsfw`）。
    消すときは区画ごと落とし、括弧の対応が崩れないように
    先頭の括弧は次の区画へ、末尾の `:重み)` は前の区画へ移す。

        (nsfw, worst quality, low quality:1.4)  から nsfw   → (worst quality, low quality:1.4)
        (nsfw, worst quality, low quality:1.4)  から low quality → (nsfw, worst quality:1.4)
        (nsfw:1.4)                              から nsfw   → 消える
    """
    drop = set(bare(tag)[1].lower() for tag in split_tags(spec) if tag)
    drop.discard("")
    if not drop:
        return body
    kept = []
    carry = ""                              # 落とした区画の先頭の括弧
    for tag in split_tags(body):
        if not tag:
            continue
        opening, core, closing = bare(tag)
        if core.lower() not in drop:
            kept.append(carry + tag)
            carry = ""
            continue
        if opening and closing:
            continue                        # 括弧が閉じている1区画。丸ごと落とす
        if opening:
            carry += opening
        elif closing:
            if carry:
                carry = ""                  # 開きも閉じも落とした区画。括弧ごと消える
            elif kept:
                kept[-1] += closing
    return ", ".join(kept)


def remap_lora(body, pairs):
    """`<lora:名前:重み>` の名前を付け替える。`to` が `off` なら取り除く。

    `to` に `名前:重み` と書けば重みも差し替わる（元 MOD と同じ書式）。
    """
    if not pairs:
        return body

    def swap(found):
        name = found.group(1).strip()
        weight = found.group(2) or ""
        for want, to in pairs:
            if name.lower() != str(want).strip().lower():
                continue
            to = str(to or "").strip()
            if to.lower() == "off":
                return ""
            return "<lora:{}>".format(to) if ":" in to else "<lora:{}{}>".format(to, weight)
        return found.group(0)

    return LORA_TAG.sub(swap, body)


def lora_names(body):
    """本文に在る `<lora:名前...>` の名前（小文字）。"""
    return set(found.group(1).strip().lower()
               for found in LORA_TAG.finditer(body or ""))


def strip_lora(body, only=None):
    """`<lora:...>` のタグを外す。`only` を渡せばその名前（小文字）のものだけ。

    ゲームが入れたタグだけを外したいときは、書き換える前の本文の `lora_names` を
    `only` に渡す。規則で足したタグ（別の名前）はそのまま残る。
    """
    if only is not None and not only:
        return body

    def swap(found):
        if only is None or found.group(1).strip().lower() in only:
            return ""
        return found.group(0)

    return tidy(LORA_TAG.sub(swap, body or ""))


def tidy(body):
    """空の区画を落としてカンマを整える。

    付け替えで `<lora:...>` を外すと `a, , b` のような穴が残る。
    ゲームのプロンプトはカンマ区切りなので、区画で数えて繋ぎ直す。
    """
    return ", ".join(tag for tag in split_tags(body) if tag)


def join(parts):
    """カンマで繋ぎ直す。空の区画と重なったカンマを落とす。"""
    return tidy(", ".join(part.strip() for part in parts if str(part).strip()))


def rewrite(rules, kind, target, body, original):
    """1本ぶんの書き換え。変えないなら `None`。

    `body` はいまの本文、`original` は**ゲーム本来の**本文（条件の判定に使う）。
    """
    now = body if isinstance(body, str) else ""
    was = now
    #: 当たった規則が1つでもあるか。**1つも無ければ本文に触らない。**
    #: 当たっていないのに `tidy()` を通すと、ゲームの `a,b` が `a, b` に均されて
    #: 「規則が空でも毎回プロンプトを書き換える」ことになる
    #: （実機 2026-09-20: 規則0行で 227字 -> 231字。ゲームの本文はカンマの後に空白が無い）。
    touched = False

    for row in active(rules, "remove", kind, target):
        now = remove_tags(now, row.get("text"))
        touched = True

    for row in active(rules, "replace", kind, target):
        text = str(row.get("text") or "")
        if text:
            now = text.replace(PLACEHOLDER, now)
            touched = True

    if target == "prompt":
        pairs = [(row.get("from"), row.get("to"))
                 for row in active(rules, "lora_map")]
        if pairs:
            now = remap_lora(now, pairs)
            touched = True

    additions = [str(row.get("text") or "")
                 for row in active(rules, "add", kind, target)
                 if matches(row.get("when"), original)]
    if additions:
        now = join([now] + additions)
    elif touched:
        now = tidy(now)          # 付け替えで空いた区画を塞ぐ
    elif not touched:
        return None

    return None if now == was else now


def skip_upscale(rules, kind, original):
    """この生成で寸法を触らないか（元 MOD の `skip_if`）。"""
    for row in active(rules, "skip_upscale", kind):
        if matches(row.get("when"), original or ""):
            return True
    return False
