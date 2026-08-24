# -*- coding: utf-8 -*-
"""人物欄に載せる値と、箱の置き場所。画面には触らない。

ここに在るのは「何を出すか」だけで、「どう描くか」は入口の
`ui_character_sheet.py` にある。
分けてあるのは、
この部分をゲーム抜きで確かめられるようにするため（`tools/tests/test_ui_character_sheet.py`）。

ゲーム側の作り（`212_probe_character_sheet` で採寸。窓 1920x1000）:

    hud.character_sheet_layout   FloatLayout 810x497  pos_hint {center_x:0.37, center_y:0.68}
      character_sheet_name              size_hint (0.5,  0.1 )  {x:0.02, center_y:0.94}
      character_sheet_basic_info        size_hint (0.7,  0.1 )  {x:0.03, center_y:0.86}
      character_sheet_background        size_hint (0.7,  0.4 )  {x:0.02, center_y:0.51}   出自
      character_sheet_health_condition  size_hint (0.7,  0.25)  {x:0.02, center_y:0.16}   健康状態
      character_sheet_attributes        size_hint (0.24, 0.45)  {x:0.74, y:0.515}         能力値
      character_sheet_empty             size_hint (0.24, 0.45)  {x:0.74, y:0.035}         常に空

- 開閉は「レイアウトごと `size` と `opacity` を切り替える」形。
  子は全て `size_hint` で置かれているので、親を広げれば付いてくるし、
  閉じれば一緒に消える
- 枠線は `canvas.after` の矩形（`add_border`）で、`pos` / `size` に追従する
- 立ち絵（`character_image_right`）はこのレイアウトの外。
  触らない

手配度は `player.area_history[エリアid]['lawfulness']`（`309_office_pardon` が実セーブで確認済み。負ほど重く、0 未満で犯罪者、
平常値は 10）。
"""

# ---------------------------------------------------------------- 箱の置き場所
# 値は**広げた後のレイアウトに対する割合**。
# ゲーム自身の置き方（`size_hint` と `pos_hint`）をそのまま使うので、
# 窓の大きさが変わっても比率のまま付いてくる。
# 左の列（出自・健康状態）／中央（手配度）／右の列（能力値・スキル・特性）。
# 縦は下から 0.03、上は 0.84 までを箱に使い、残りを名前と年齢行に空けてある。
# 3列の上端（出自・手配度・能力値）は 0.84 で揃えてある。
# 揃っていないと、開いたときに列の頭だけ段差になって見える。
# 名前の箱の上（0.975 から上）は枠線との余白。
# 詰めると名前が枠に貼り付いて見える。
# 横は、枠を左へ広げたぶん（PANEL_LEFT 0.05 → 0.02）を手配度の幅に充ててある。
# 他の箱は広げる前とほぼ同じ実寸になるように割合を縮めてある。
BOXES = {
    # ゲーム自身のウィジェット。
    # 属性名は実測で確定したもの。
    "character_sheet_name":             ((0.33, 0.07), {"x": 0.02, "center_y": 0.94}),
    # レベル行。二つ名を続けて書くので、幅は引き直しボタン（x 0.79）の手前まで。
    "character_sheet_basic_info":       ((0.75, 0.05), {"x": 0.03, "center_y": 0.875}),
    "character_sheet_background":       ((0.43, 0.54), {"x": 0.02, "y": 0.30}),
    "character_sheet_health_condition": ((0.43, 0.25), {"x": 0.02, "y": 0.03}),
    # 能力値は6行（筋力〜魅力）。
    # 高さ 0.36 だと窓の高さによっては最終行（魅力）が箱の下で欠けた。
    "character_sheet_attributes":       ((0.19, 0.40), {"x": 0.79, "y": 0.44}),
    # 空のまま置かれている箱を特性に使う（枠線が既に引かれている）。
    "character_sheet_empty":            ((0.19, 0.18), {"x": 0.79, "y": 0.03}),
    # この MOD が足す箱。
    # 二つ名は箱を持たない。レベル行（basic_info）の text に続けて書く
    # （別の箱に置いた2つの版は、名前と並べると浮き、レベル行と並べると
    # 折り返しが手配度の枠に食い込んだ ― 実機2・3周目。続けて書けば
    # 位置合わせそのものが要らない）。行が伸びるぶん、上の basic_info の
    # 箱を手配度の手前（0.78）まで広げてある。
    "wanted":                           ((0.30, 0.81), {"x": 0.47, "y": 0.03}),
    "skills":                           ((0.19, 0.21), {"x": 0.79, "y": 0.22}),
}

#: 足す箱（`BOXES` のうち、ゲームが持っていないもの）。
#: 特性は `character_sheet_empty` を使い回すので、新設するのはこの2つだけ。
ADDED = ("wanted", "skills")

#: 特性を入れる箱。
#: ゲームが作って一度も中身を入れていない Label（実測で毎回 `text=''`）。
#: ここを借りると枠線を引き直さずに済む。
TRAITS_BOX = "character_sheet_empty"

#: 開くたびに見た目を写し直す、ゲーム自身の箱。
#: 借りている特性の箱と、能力値の箱。
#: どちらも元は中央寄せ・左右 46 の余白で、
#: そのままだと文字が中央に寄って見え、書体によっては右端が欠ける。
RESTYLED = (TRAITS_BOX, "character_sheet_attributes")

#: 寸法を写し取る手本。
#: 足した箱の書体・色・余白はここから借りる（日本語の書体はゲームが読み込んだものでないと出ない）。
STYLE_SOURCE = "character_sheet_health_condition"

# ---------------------------------------------------------------- 二つ名
#: 評判 MOD（`317_reputation`）の控えの置き場。`state\reputation\<世界名>.json`。
#: 形は MOD をまたいだ取り決め（MODS.md `317_reputation` の「控えの形」）。
#: MOD どうしは import しない（TECH.md §3.2.3）ので、ファイルを読むことで繋がる。
#: ファイルが無ければ何も出さない ＝ 評判 MOD を切っていても人物欄は成り立つ。
REPUTATION_DIRNAME = "reputation"


def reputation_epithet(data):
    """評判 MOD の控え（読み込んだ JSON）から世界の二つ名を取り出す。

    形を決めつけない。読めなければ空文字（＝箱ごと空になる）。

        {"areas": {...}, "epithet": {"epithet": "灰の街の盾", ...}}
    """
    if not isinstance(data, dict):
        return ""
    record = data.get("epithet")
    value = record.get("epithet") if isinstance(record, dict) else None
    return value.strip() if isinstance(value, str) else ""


#: 二つ名の引き直しの頼み。ボタンが押されたらこの名前のファイルを書き、
#: 評判 MOD が次の照合で消して応える（呼び合わずにファイルで繋ぐ。TECH.md §3.2.3）。
#: 中身は読まれない（在ることが頼み）。
REROLL_SUFFIX = ".reroll.json"

#: 引き直しボタンの置き場所。二つ名の箱（x 0.43〜0.78）のすぐ右。
#: 大きさはボタン側の固定値（`ui.make_icon_button` の正方形）を使うので、位置だけ持つ。
#: 最初の版は箱の割合（幅0.05×高0.06）で置いていて、窓に合わせて横長に潰れていた（実機）。
REROLL_POS = {"x": 0.79, "center_y": 0.875}

#: 引き直しボタンの絵柄（0〜1 の座標。円弧＋矢尻）。
#: MOD 固有の絵柄はローダの `icon_strokes` に足さず自分のフォルダに置く決まり。
REROLL_STROKES = [
    [(0.35, 0.76), (0.50, 0.80), (0.65, 0.76), (0.76, 0.65), (0.80, 0.50),
     (0.76, 0.35), (0.65, 0.24), (0.50, 0.20), (0.35, 0.24), (0.24, 0.35),
     (0.20, 0.50)],
    [(0.20, 0.50), (0.10, 0.42)],
    [(0.20, 0.50), (0.32, 0.44)],
]


def epithet_text(epithet):
    """レベル行に続けて出す文字。二つ名が無ければ空文字。

    見出しを付けないのは、行の続きとして読ませたいから
    （「二つ名:」と書くと欄が1つ増えたように見える）。
    """
    epithet = epithet.strip() if isinstance(epithet, str) else ""
    return "「{}」".format(epithet) if epithet else ""


# ---------------------------------------------------------------- 見出し
# 見出しはゲーム自身の書き方（`出自:` / `健康状態:`）に合わせる。
# 空行は挟まない。
# 見出しの下に1行空けると、スキルのように行数の要る欄が1件で埋まる。
WANTED_HEADING = "手配度: (0未満で犯罪者)"
SKILLS_HEADING = "スキル:"
TRAITS_HEADING = "特性:"

#: 手配度の行が箱に入り切らなくなる目安。
#: これを超えたぶんは出さない。
MAX_WANTED_LINES = 24

#: エリアの規模。
#: この値のエリアは手配度に出さない。
#: セーブでは `dungeon`（単数）、BGM のフォルダは `dungeons`（複数）と揺れる。
DUNGEON_SIZES = ("dungeon", "dungeons")

#: BGM のパス `.../musics/<size>/<mood>/曲.mp3` から規模を読むときの目印。
MUSIC_MARKER = "musics"


def is_dungeon(size):
    """このエリアがダンジョンか。分からないものはダンジョン扱いにしない。

    規模を読めなかったエリアを伏せると、読めない理由（セーブの形が違う、
    BGM が空）がそのまま「記録が消えた」ように見える。
    出しておけば多すぎることはあっても、プレイヤーの記録が黙って落ちることはない。
    """
    return isinstance(size, str) and size.strip().lower() in DUNGEON_SIZES


def size_from_bgm(bgm):
    """BGM のパスから規模を読む。読めなければ `None`。

    `104_balance_area_bgm` と同じ読み方。
    実行中の `Area` は `size` を持っていないので（実測）、
    生の辞書が引けないときの控えとして使う。
    """
    if not isinstance(bgm, str) or not bgm.strip():
        return None
    parts = bgm.replace("\\", "/").split("/")
    if len(parts) < 4:
        return None
    if "/".join(parts[:-3]).lower().rsplit("/", 1)[-1] != MUSIC_MARKER:
        return None
    return parts[-3]


def raw_areas(holders):
    """セーブの生のエリア表 `{id: {"size": ..., ...}}` を探す。

    実行中の `Area` オブジェクトは `size` を持っていない（実測）。
    セーブを読み込んだ辞書のほうには在るので、
    `app.world_dict` などから中身で見分ける。
    属性名は決めつけない（`world_dict` /
    `save_data_dict` のどちらが生きているかはビルド次第）。
    """
    for holder in holders:
        try:
            own = dict(vars(holder))
        except Exception:
            continue
        for value in own.values():
            if not isinstance(value, dict):
                continue
            found = _areas_in(value)
            if found is not None:
                return found
    return None


def _areas_in(value, depth=0):
    """辞書の中から「エリアの表らしいもの」を1つ選ぶ。

    `{id: {...}}` の形で、中身が `size` と
    `name` の両方を持っているものだけをエリア表とみなす。
    1段だけ潜るのは、セーブが `{"areas": {...}}` の形だから。
    """
    if not value:
        return None
    sample = next(iter(value.values()))
    if isinstance(sample, dict) and "size" in sample and "name" in sample:
        return value
    if depth >= 1:
        return None
    for nested in value.values():
        if isinstance(nested, dict):
            found = _areas_in(nested, depth + 1)
            if found is not None:
                return found
    return None


def area_sizes(areas, raw):
    """`{エリアid: 規模}`。生の表が在ればそちらを、無ければ BGM から読む。

    id は保存されるときに文字列になるので、両方を文字列に均して突き合わせる。
    """
    sizes = {}
    for area_id, area in (areas or {}).items():
        key = str(area_id)
        size = None
        if isinstance(raw, dict):
            entry = raw.get(key, raw.get(area_id))
            if isinstance(entry, dict):
                size = entry.get("size")
        if not isinstance(size, str) or not size:
            size = size_from_bgm(getattr(area, "bgm", None))
        sizes[key] = size
    return sizes


def wanted_entries(areas, sizes, history, max_lines=MAX_WANTED_LINES):
    """手配度に出す `[(エリア名, 手配度)]`。

    出す条件は3つ。
    規模がダンジョンでないこと、記録が在ること（＝一度も関わっていないエリアは出さない）、
    手配度が数として読めること。
    並びはエリア id 順で、id が数として読めるならその順に均す。
    """
    rows = []
    for area_id, area in (areas or {}).items():
        key = str(area_id)
        if is_dungeon((sizes or {}).get(key)):
            continue
        entry = _history_entry(history, key)
        value = _lawfulness(entry)
        if value is None:
            continue
        name = getattr(area, "name", None)
        if not isinstance(name, str) or not name.strip():
            name = key
        rows.append((_sort_key(key), name, value))
    rows.sort(key=lambda row: row[0])
    return [(name, value) for _key, name, value in rows[:max_lines]]


def _sort_key(area_id):
    try:
        return (0, int(area_id), "")
    except (TypeError, ValueError):
        return (1, 0, str(area_id))


def _history_entry(history, area_id):
    if not isinstance(history, dict):
        return None
    if area_id in history:
        return history[area_id]
    for key, value in history.items():
        if str(key) == area_id:
            return value
    return None


def _lawfulness(entry):
    """記録の手配度。`bool` を弾くのは `isinstance(True, int)` が真だから。"""
    if isinstance(entry, dict):
        value = entry.get("lawfulness")
    else:
        value = getattr(entry, "lawfulness", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def names_of(value, limit=12):
    """スキル / 特性の名前を並べる。入れ物の形を決めつけない。

    実測ではスキルが `{名前: {...}}`、特性が `[{'name': ..., ...}]` だったが、
    どちらも文字列の配列やオブジェクトでありうる前提で読む。
    """
    items = []
    if isinstance(value, dict):
        # 鍵が名前。
        # 中身に `name` が在ればそちらを優先する。
        for key, item in value.items():
            items.append(_name_of(item) or (str(key) if key is not None else ""))
    elif isinstance(value, (list, tuple)):
        for item in value:
            items.append(_name_of(item) or (item if isinstance(item, str) else ""))
    return [text for text in items if isinstance(text, str) and text.strip()][:limit]


def _name_of(item):
    if isinstance(item, dict):
        name = item.get("name")
    else:
        name = getattr(item, "name", None)
    return name if isinstance(name, str) and name.strip() else None


# ---------------------------------------------------------------- 文面
# ゲーム自身の「出自:」「健康状態:」と同じ形。
# 見出しの次の行から中身が始まり、行頭は下げない。
# 空行も字下げも入れない。
# 空行を挟むとスキル欄が1件で埋まり、字下げると箱の余白と二重にずれて見える。
def wanted_text(entries):
    if not entries:
        return ""
    return "\n".join([WANTED_HEADING]
                     + ["{}：{}".format(name, value) for name, value in entries])


def list_text(heading, names):
    if not names:
        return ""
    return "\n".join([heading] + list(names))
