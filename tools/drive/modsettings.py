# -*- coding: utf-8 -*-
r"""`settings/mod_settings.json` の MOD の欄を書き換える（ゲームの外で走る）。

    python tools/drive/modsettings.py 911_rival_adventurer show
    python tools/drive/modsettings.py 911_rival_adventurer set DUE_DAYS_MIN=1 DUE_DAYS_MAX=2
    python tools/drive/modsettings.py 911_rival_adventurer reset     この MOD の欄を外す（既定へ戻る）

設定画面で値を変えるのと同じ置き場所。効くのは次の注入から（`python tools/injector.py`）。
`setmod.py`（nav.sh の `m`）は動いている MOD の定数だけを替えるので、注入し直すと消える。
注入し直しをまたいで効かせたいときはこちらを使い、試し終えたら `reset` で戻す。
値は JSON として読めればその型（数・真偽）、読めなければ文字列。並びと字下げは設定画面の書き方（鍵の順・2字下げ）に合わせる。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(ROOT, "settings", "mod_settings.json")


def main(argv):
    if len(argv) < 2 or argv[1] not in ("show", "set", "reset"):
        print(__doc__)
        return 2
    key, action = argv[0], argv[1]
    data = {}
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    before = data.get(key)
    if action == "set":
        row = dict(before or {})
        for pair in argv[2:]:
            name, _, value = pair.partition("=")
            try:
                row[name] = json.loads(value)
            except ValueError:
                row[name] = value
        data[key] = dict(sorted(row.items()))
    elif action == "reset":
        data.pop(key, None)
    if action != "show":
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=2) + "\n")
    print("before {}".format(json.dumps(before, ensure_ascii=False)))
    print("after  {}".format(json.dumps(data.get(key), ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
