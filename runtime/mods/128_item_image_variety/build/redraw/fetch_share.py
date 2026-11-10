# -*- coding: utf-8 -*-
r"""ChatGPT の共有リンクから原寸の画像を取り出し、`sheets\<手本の名前>.png` に書く。

    python runtime/mods/128_item_image_variety/build/redraw/fetch_share.py <手本の名前> <https://chatgpt.com/s/...>

ページの `estuary/public_content` の URL のうち、`sediment` を含まないもの（中身を base64 で読むと
`file_...` だけのもの）が原寸の PNG。`sediment` のほうは 1200x630 の JPEG のプレビューや縮小。
"""
import base64
import io
import os
import re
import sys
import urllib.request

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(os.path.dirname(HERE))   # 128_item_image_variety
ROOT = os.path.normpath(os.path.join(MOD, os.pardir, os.pardir, os.pardir))
# 取り込みの作業場（手本・描き直しの一枚絵・切り出し・頼んだ記録）。大きいので git の外の local\ に置く
WORK = os.path.join(ROOT, "local", "item_image_redraw")
# 描き直しの絵の置き場（配布物）
OUT = os.path.join(MOD, "images", "redraw")
UA = {"User-Agent": "Mozilla/5.0"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as res:
        return res.read()


def original_url(page):
    for enc in dict.fromkeys(re.findall(r"estuary/public_content/enc/([A-Za-z0-9_=-]+)", page)):
        try:
            inner = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode("utf-8", "replace")
        except ValueError:
            continue
        if "sediment" not in inner:
            return "https://chatgpt.com/backend-api/estuary/public_content/enc/" + enc
    return None


def main():
    name, link = sys.argv[1], sys.argv[2]
    url = original_url(get(link).decode("utf-8", "replace"))
    if url is None:
        sys.exit("原寸の画像の URL が見つからない")
    im = Image.open(io.BytesIO(get(url)))
    os.makedirs(os.path.join(WORK, "sheets"), exist_ok=True)
    path = os.path.join(WORK, "sheets", name + ".png")
    im.convert("RGB").save(path)
    print("{} {}x{} -> {}".format(im.format, im.width, im.height, path))


if __name__ == "__main__":
    main()
