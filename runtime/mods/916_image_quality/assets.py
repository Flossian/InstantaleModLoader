# -*- coding: utf-8 -*-
r"""モデルの置き場と、TAESD デコーダのダウンロード。

ゲームのフォルダを触らずに SDXL へ移るには、チェックポイント・TAESD・VAE・LoRA を
**ゲームの外**に置く必要がある（ゲーム内の選択は `runtime\models\sd15\checkpoints\`
の中しか見ないため。DOC.md §1）。その置き場をここで決める。

    state\models\sd15\checkpoints\  taesd\  vae\  lora\
    state\models\sdxl\checkpoints\  taesd\  vae\  lora\

**系統を先に切る。**
壊れ方（プロセスごと落ちる）は系統の食い違いで起きるので、
1つのフォルダの中身が常に揃った組になる形にしてある。
中の名前はゲームの `runtime\models\sd15\...` と同じにして、置き場所の説明を減らす。

`state\` の下に置くのは、利用者が用意する大きなファイルの前例（`state\musics\battle\`）に合わせたもの。
MOD のフォルダ名では切らない（正式化で番号が変わると行方不明になる）。
"""
import io
import os

#: `state\` の下の根。
ROOT = "models"

#: 系統と、その下のフォルダ。ゲームの `runtime\models\sd15\` と同じ並び。
FAMILIES = ("sd15", "sdxl")
KINDS = ("checkpoints", "taesd", "vae", "lora")

#: モデルの拡張子（探すときに使う）。
SUFFIXES = (".safetensors", ".ckpt", ".gguf", ".pt")

#: ダウンロードできるもの。TAESD は小さい（約 9.8MB）ので画面から取れる。
#: URL は元 MOD の SDXL 手順書が案内しているものと同じ。
DOWNLOADS = {
    ("sdxl", "taesd"): {
        "label": "TAESDXL（SDXL 用のデコーダ）",
        "url": "https://huggingface.co/madebyollin/taesdxl/resolve/main/"
               "diffusion_pytorch_model.safetensors",
        "name": "diffusion_pytorch_model.safetensors",
        "license": "MIT（madebyollin/taesdxl）",
        "about": "SDXL のチェックポイントを使うときに要る。系統が違うと絵が崩れる",
    },
    ("sd15", "taesd"): {
        "label": "TAESD（SD1.5 用のデコーダ）",
        "url": "https://huggingface.co/madebyollin/taesd/resolve/main/"
               "diffusion_pytorch_model.safetensors",
        "name": "diffusion_pytorch_model.safetensors",
        "license": "MIT（madebyollin/taesd）",
        "about": "ゲームに同梱されているものと同じ。ゲームのフォルダを読ませたくないときだけ",
    },
}

#: ダウンロードしたファイルがこれより小さければ失敗とみなす（HTML が返ることがある）。
MIN_BYTES = 1024 * 1024


def root_dir(state_dir):
    return os.path.join(state_dir, ROOT)


def dir_of(state_dir, family, kind):
    """`state\\models\\<系統>\\<種類>\\` の絶対パス。"""
    return os.path.join(root_dir(state_dir), family, kind)


def ensure_dirs(state_dir):
    """置き場を作る。作れたフォルダの一覧を返す（既に在るものは含めない）。"""
    made = []
    for family in FAMILIES:
        for kind in KINDS:
            path = dir_of(state_dir, family, kind)
            if not os.path.isdir(path):
                try:
                    os.makedirs(path)
                    made.append(path)
                except Exception:
                    pass
    return made


def files_in(state_dir, family, kind):
    """その置き場に在るモデルのファイル名。"""
    path = dir_of(state_dir, family, kind)
    try:
        names = sorted(os.listdir(path))
    except Exception:
        return []
    return [name for name in names
            if os.path.splitext(name)[1].lower() in SUFFIXES]


def newest(state_dir, family, kind):
    """その置き場で一番新しいファイルの絶対パス。無ければ空。"""
    path = dir_of(state_dir, family, kind)
    found = [(os.path.getmtime(os.path.join(path, name)), name)
             for name in files_in(state_dir, family, kind)]
    if not found:
        return ""
    found.sort(reverse=True)
    return os.path.join(path, found[0][1])


def describe(state_dir):
    """置き場に何が在るかを行の並びで。画面にそのまま出す。"""
    lines = []
    for family in FAMILIES:
        parts = []
        for kind in KINDS:
            names = files_in(state_dir, family, kind)
            parts.append("{} {}".format(kind, len(names) if names else "なし"))
        lines.append("{}: {}".format(family, " / ".join(parts)))
    return lines


def download(url, dest, timeout=120, opener=None):
    """1つダウンロードして `dest` へ置く。`(できたか, 伝える文)` を返す。

    書き途中のファイルを残さないよう、`.part` に書いてから置き換える。
    小さすぎる応答（HTML のエラー頁など）は失敗として扱う。
    `opener` は検査用の差し替え口（既定は `urllib.request.urlopen`）。
    """
    if opener is None:
        from urllib.request import Request, urlopen

        def opener(target, seconds):
            return urlopen(Request(target, headers={"User-Agent": "InstantaleModLoader"}),
                           timeout=seconds)

    part = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with opener(url, timeout) as response:
            with io.open(part, "wb") as fh:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
    except Exception as exc:
        _drop(part)
        return False, "ダウンロードできませんでした: {}".format(exc)

    size = os.path.getsize(part) if os.path.isfile(part) else 0
    if size < MIN_BYTES:
        _drop(part)
        return False, ("応答が小さすぎます（{} バイト）。"
                       "URL をブラウザで開いて確かめてください".format(size))
    try:
        os.replace(part, dest)
    except Exception as exc:
        _drop(part)
        return False, "置き換えられませんでした: {}".format(exc)
    return True, "{} に置きました（{:.1f}MB）".format(dest, size / float(1024 * 1024))


def _drop(path):
    try:
        os.remove(path)
    except Exception:
        pass


def manual_steps(entry, dest_dir):
    """ダウンロードが使えないときの案内。URL と置き場と名前を出す。"""
    return "\n".join([
        "{}（{}）".format(entry["label"], entry["license"]),
        "  1. {} をブラウザで開いて保存してください".format(entry["url"]),
        "  2. {} へ置いてください".format(dest_dir),
        "  3. 名前は {} のままで構いません".format(entry["name"]),
    ])
