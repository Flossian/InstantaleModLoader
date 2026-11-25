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

手配度は `player.area_history[エリアid]['lawfulness']`（GAME.md §2.20。負ほど重く、0 未満で犯罪者、
平常値は 10）。
"""

from instantale_modloader import frames

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
    # （別の箱に置くと、名前と並べれば浮き、レベル行と並べれば
    # 折り返しが手配度の枠に食い込む。VERIFICATION_LOG.md §2.46。続けて書けば
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
#: 評判 MOD（`317_reputation`）の控えの置き場。`state\reputation\<世界×主人公>.json`（周回の鍵）。
#: 形は MOD をまたいだ取り決め（MODS.md `317_reputation` の「控えの形」）。
#: MOD どうしは import しない（TECH.md §3.2.3）ので、ファイルを読むことで繋がる。
#: ファイルが無ければ何も出さない ＝ 評判 MOD を切っていても人物欄は成り立つ。
REPUTATION_DIRNAME = "reputation"


def reputation_epithet(data):
    """評判 MOD の控え（読み込んだ JSON）から世界の二つ名を取り出す。

    形を決めつけない。読めなければ空文字（＝箱ごと空になる）。

        {"areas": {...}, "epithet": {"epithet": "灰の街の盾", ...},
         "fame": {"eligible": true, ...}}

    名声が線に届いていなければ（`fame.eligible` が false）、控えに名が残っていても出さない。
    `fame` が無いのは評判 MOD の版5までのファイルで、そのときは名をそのまま出す。
    """
    if not isinstance(data, dict):
        return ""
    fame = data.get("fame")
    if isinstance(fame, dict) and fame.get("eligible") is False:
        return ""
    record = data.get("epithet")
    value = record.get("epithet") if isinstance(record, dict) else None
    return value.strip() if isinstance(value, str) else ""


# ---------------------------------------------------------------- 特性の効果
#: 特質の効果 MOD（`346_player_trait_effects`）の控えの置き場。`state\trait_effects\<世界×主人公>.json`。
#: 形は MOD をまたいだ取り決め（`346_` の DOC.md）。二つ名と同じく、ファイルを読むことで繋がる。
TRAIT_EFFECTS_DIRNAME = "trait_effects"


def trait_effect_texts(data):
    """特質の効果 MOD の控えから `{特性の名前: [{"text", "condition"}, ...]}` を取り出す。読めなければ空。

    効果は人物欄の特性の箱にマウスを乗せたときの説明の窓に出す（`trait_details`）。
    `condition` は窓口の場面（`abilities.situations`）の名前で、いまの場面に無ければ灰色にする。
    効果が文字列だけなら条件は `always`、効果ごとの並びが無ければつないだ1文（`text`）を1件として読む。

        {"display": [{"name": "坑夫の勘", "text": "依頼中判断+2",
                      "effects": [{"text": "依頼中判断+2", "condition": "quest"}]}, ...], ...}
    """
    rows = data.get("display") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    found = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        lines = []
        for effect in row.get("effects") if isinstance(row.get("effects"), list) else []:
            if isinstance(effect, str) and effect.strip():
                lines.append({"text": effect.strip(), "condition": "always"})
            elif isinstance(effect, dict) and isinstance(effect.get("text"), str) and effect["text"].strip():
                condition = effect.get("condition")
                lines.append({"text": effect["text"].strip(),
                              "condition": condition if isinstance(condition, str) and condition else "always"})
        if not lines and isinstance(row.get("text"), str) and row["text"].strip():
            lines = [{"text": row["text"].strip(), "condition": "always"}]
        if lines:
            found[row["name"].strip()] = lines
    return found


# ---------------------------------------------------------------- 健康状態
# 素のゲームは怪我のある部位を `arms: hurt, ` と英語のまま並べ、何が下がったかはどこにも出さなかった。
# 欄には部位と段階を日本語で1行ずつ書き、下がった能力値はマウスを乗せたときの説明の窓に出す。
# 値の出どころと測り方は GAME.md §2.19.1。

#: 健康状態の箱（ゲーム自身のウィジェット）。
HEALTH_BOX = "character_sheet_health_condition"

HEALTH_HEADING = "健康状態:"

#: 怪我が無いときのゲーム自身の文。こちらが書いた行が残ったまま怪我が治ったときに戻す先。
HEALTH_FINE = "特に問題はない。"

#: 部位の名前。並びはゲームが能力値へ倍率を掛ける順（`body_parts` の並び）。
PART_NAMES = {"head": "頭", "body": "胴体", "arms": "腕", "legs": "脚", "organs": "内臓", "sanity": "精神"}

#: 段階の言葉は部位の性質で3組に分ける。
#: ゲームの `lost` は倍率がいちばん重いだけの段階で、欠損にも死亡にもならない。
#: 治っても不自然でない言葉にしてある。
PART_GROUPS = {"arms": "limb", "legs": "limb", "head": "vital", "body": "vital", "organs": "vital",
               "sanity": "mind"}
STAGE_NAMES = {
    "limb": {"hurt": "軽傷", "damaged": "重傷", "disabled": "麻痺", "lost": "使用不能"},
    "vital": {"hurt": "軽傷", "damaged": "重傷", "disabled": "重体", "lost": "危篤"},
    "mind": {"hurt": "軽い不調", "damaged": "重い不調", "disabled": "衰弱", "lost": "錯乱"},
}

#: 段階の並びと、その段階になる怪我の重さ（`injury`）。
STAGES = ("hurt", "damaged", "disabled", "lost")
STAGE_AT = {"hurt": 10, "damaged": 30, "disabled": 60, "lost": 100}

ABILITY_NAMES = {"strength": "筋力", "dexterity": "器用", "constitution": "耐久",
                 "intelligence": "知力", "wisdom": "判断", "charisma": "魅力"}

#: 部位と段階ごとに、ゲームが能力値へ掛ける倍率。
_TWO = ((0.95,), (0.95, 0.85), (0.85, 0.75), (0.75, 0.75))


def _effects(first, second):
    """1段階目は `second` だけ、2段階目からは `first` と `second` の両方が下がる部位の表。"""
    table = {"hurt": ((second, _TWO[0][0]),)}
    for stage, rates in zip(STAGES[1:], _TWO[1:]):
        table[stage] = ((first, rates[0]), (second, rates[1]))
    return table


PART_EFFECTS = {
    "arms": _effects("strength", "dexterity"),
    "legs": _effects("constitution", "dexterity"),
    "body": _effects("strength", "constitution"),
    "head": _effects("intelligence", "charisma"),
    "sanity": _effects("intelligence", "wisdom"),
    "organs": {"hurt": (("constitution", 0.95),), "damaged": (("constitution", 0.85),),
               "disabled": (("constitution", 0.75),), "lost": (("constitution", 0.75),)},
}

#: 段階ごとに、ゲームが体力（スタミナ）の上限へ掛ける倍率。部位によらない。
STAMINA_RATES = {"hurt": 0.97, "damaged": 0.95, "disabled": 0.9, "lost": 0.85}
STAMINA_NAME = "体力の上限"

#: 効果の行の条件。能力値に出ていない倍率は、説明の窓でいまの場面に無い効果と同じ色にする。
HEALTH_APPLIED = "always"
HEALTH_IDLE = "health_idle"


def _whole(value):
    """整数として読めた値。読めなければ None（`bool` は数にしない。ローダの `frames.is_number`）。"""
    if not frames.is_number(value):
        return None
    return int(value) if float(value) == int(value) else value


def _rate_text(rate):
    return "×{:g}".format(rate)


def injured_parts(body_parts):
    """怪我のある部位 `[(部位, 段階, 怪我の重さ)]`。並びは `body_parts` のまま。

    段階が `intact` の部位と、形の読めない部位は入れない。
    """
    if not isinstance(body_parts, dict):
        return []
    found = []
    for part, info in body_parts.items():
        stage = info.get("stage") if isinstance(info, dict) else getattr(info, "stage", None)
        if not isinstance(stage, str) or not stage.strip() or stage == "intact":
            continue
        injury = info.get("injury") if isinstance(info, dict) else getattr(info, "injury", None)
        found.append((str(part), stage, _whole(injury)))
    return found


def part_name(part):
    """部位の表示名。表に無い部位はゲームの値のまま。"""
    return PART_NAMES.get(part, part)


def stage_name(part, stage):
    """段階の表示名。表に無い部位・段階はゲームの値のまま。"""
    return STAGE_NAMES.get(PART_GROUPS.get(part), {}).get(stage, stage)


#: 健康状態の欄に、見出しの下で入る行数。3行目は欄の下で落ちる（VERIFICATION.md §3.131）。
HEALTH_LINES = 2
#: 1行に複数の部位を並べるときの区切り。
HEALTH_GAP = "　"


def health_text(body_parts):
    """健康状態の欄の文。怪我が無ければ None（ゲームの文に任せる）。

    部位は1行に1つ。欄に入る行数（`HEALTH_LINES`）を超えるときは、1行に並べる数を増やして収める。
    """
    parts = injured_parts(body_parts)
    if not parts:
        return None
    cells = ["{}: {}".format(part_name(part), stage_name(part, stage)) for part, stage, _injury in parts]
    per_line = -(-len(cells) // HEALTH_LINES)
    lines = [HEALTH_GAP.join(cells[start:start + per_line]) for start in range(0, len(cells), per_line)]
    return "\n".join([HEALTH_HEADING] + lines)


def health_fine_text():
    return HEALTH_HEADING + "\n" + HEALTH_FINE


def _injury_line(part, stage, injury):
    """`怪我の重さ 12（30 で重傷）`。次の段階が無ければ数だけ、数が読めなければ空。"""
    if injury is None:
        return ""
    line = "怪我の重さ {}".format(injury)
    if stage in STAGES and stage != STAGES[-1]:
        following = STAGES[STAGES.index(stage) + 1]
        line += "（{} で{}）".format(STAGE_AT[following], stage_name(part, following))
    return line


def health_details(body_parts, original, current):
    """健康状態の説明の行 `[{"name", "effects", "text"}]`（`detail_pieces` に渡す形）。

    `original` は元の能力値、`current` はゲームが組み直した後の能力値（補正を足す前）。
    ゲームと同じ順（`body_parts` の並び）で倍率を掛け、1つ掛けるごとに丸める。
    その結果が `current` と合えば `耐久 30 → 28（×0.95）` と書く。
    `current` が元の値のままなら倍率だけを書き、条件を `HEALTH_IDLE` にする
    （読み込み直した後は、次に怪我の判定が走るまで能力値が元の値に戻っている）。
    どちらでもなければ倍率だけを書く。
    """
    parts = injured_parts(body_parts)
    if not parts:
        return []
    original = original if isinstance(original, dict) else {}
    current = current if isinstance(current, dict) else {}
    values = dict(original)
    steps = {}
    for part, stage, _injury in parts:
        rows = []
        for ability, rate in PART_EFFECTS.get(part, {}).get(stage, ()):
            before = _whole(values.get(ability))
            after = round(before * rate) if before is not None else None
            if after is not None:
                values[ability] = after
            rows.append((ability, rate, before, after))
        steps[part] = rows
    touched = {ability for rows in steps.values() for ability, _rate, _before, _after in rows}
    known = bool(touched) and all(_whole(original.get(a)) is not None and _whole(current.get(a)) is not None
                                  for a in touched)
    applied = known and all(current[a] == values[a] for a in touched)
    idle = known and not applied and all(current[a] == original[a] for a in touched)

    found = []
    for part, stage, injury in parts:
        effects = []
        for ability, rate, before, after in steps[part]:
            name = ABILITY_NAMES.get(ability, ability)
            if applied:
                text = "{} {} → {}（{}）".format(name, before, after, _rate_text(rate))
            else:
                text = "{} {}".format(name, _rate_text(rate))
            effects.append({"text": text, "condition": HEALTH_IDLE if idle else HEALTH_APPLIED})
        if stage in STAMINA_RATES:
            effects.append({"text": "{} {}".format(STAMINA_NAME, _rate_text(STAMINA_RATES[stage])),
                            "condition": HEALTH_APPLIED})
        found.append({"name": "{}　{}".format(part_name(part), stage_name(part, stage)),
                      "effects": effects, "text": _injury_line(part, stage, injury)})
    return found


# ---------------------------------------------------------------- マウスオーバーの説明
# 特性とスキルの箱は狭く、名前が1行ぶんしか見えない（2件目は箱の下で落ちる）。
# 箱にマウスを乗せたら、全件の名前・効果・説明を並べた窓を出す。

#: 説明の窓の幅（窓の幅に対する割合）と、マウスからの離れ（px。`ui.upx` で拡縮）。
#: 狭いと長い特質の説明が縦に伸びる（0.26 では 1920px で18行。VERIFICATION_LOG.md §2.46）。
DETAIL_WIDTH = 0.36
DETAIL_GAP = 16.0

#: 説明の窓が画面の高さを超えるときに、文字へ掛ける倍率と、掛ける回数の上限。
DETAIL_SHRINK = 0.9
DETAIL_SHRINKS = 6

#: 文が箱に収まらないときだけ説明の窓を出す欄。出自はゲームの文、手配度は土地の数で長さが決まる。
#: 収まっているときに出すと、箱と同じ文を重ねて見せるだけになる。
DETAIL_WHEN_CLIPPED = ("background", "wanted")

#: 説明の窓の幅を中身に合わせて詰める欄（`DETAIL_WIDTH` は上限）。手配度の行は短く、そのままでは右が空く。
DETAIL_SNUG = ("wanted",)

#: 出自の箱（ゲーム自身のウィジェット）。
BACKGROUND_BOX = "character_sheet_background"


def _text_of(item, name):
    value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
    return value.strip() if isinstance(value, str) else ""


def _number(item, name):
    value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def trait_details(value, effect_texts=None):
    """特性の説明の行 `[{"name", "effects", "text"}]`。効果は特質の効果 MOD の控え（無ければ空）。

    `effects` は `[{"text", "condition"}]`（`trait_effect_texts` の形）。
    """
    effect_texts = effect_texts or {}
    items = value if isinstance(value, (list, tuple)) else (
        list(value.values()) if isinstance(value, dict) else [])
    found = []
    for item in items:
        name = _name_of(item) or (item.strip() if isinstance(item, str) else "")
        if not name:
            continue
        found.append({"name": name, "effects": list(effect_texts.get(name.strip()) or []),
                      "text": _text_of(item, "description")})
    return found


#: スキルの属性の英語の鍵（ゲームのデータに英語のまま入っていることがある。実機で `physical`）。
ELEMENT_NAMES = {"physical": "物理", "magical": "魔法", "magic": "魔法", "fire": "火", "water": "水",
                 "ice": "氷", "lightning": "雷", "thunder": "雷", "wind": "風", "earth": "土",
                 "dark": "闇", "darkness": "闇", "light": "光", "holy": "聖", "poison": "毒"}


def skill_details(value):
    """スキルの説明の行 `[{"name", "effects", "text"}]`。効果の欄は残りの使用回数と属性（いつでも有効）。"""
    found = []
    pairs = value.items() if isinstance(value, dict) else enumerate(value if isinstance(value, (list, tuple)) else [])
    for key, item in pairs:
        name = _name_of(item) or (str(key) if isinstance(key, str) else "")
        if not name:
            continue
        parts = []
        left, top = _number(item, "current_uses"), _number(item, "max_uses")
        if top is not None:
            parts.append("使用回数 {}/{}".format(left if left is not None else top, top))
        element = _text_of(item, "element")
        if element:
            parts.append("属性 " + ELEMENT_NAMES.get(element.lower(), element))
        effects = [{"text": "・".join(parts), "condition": "always", "bullet": False}] if parts else []
        found.append({"name": name, "effects": effects, "text": _text_of(item, "description")})
    return found


def background_details(text):
    """出自の説明の行。箱の文（ゲームが書いた `出自:\\n本文`）をそのまま全文で出す。

    1行目が `:` で終わればそれを見出しにし、本文と分ける（ゲームの言語の見出しのまま）。
    見出しが無ければ全文を本文にする。本文が無ければ空。
    """
    text = text.strip() if isinstance(text, str) else ""
    head, _sep, body = text.partition("\n")
    if body and head.rstrip().endswith((":", "：")):
        name, text = head.rstrip().rstrip(":：").rstrip(), body.strip()
    else:
        name = ""
    return [{"name": name, "effects": [], "text": text}] if text else []


def wanted_details(entries):
    """手配度の説明の行。`entries` は `wanted_entries` の全件（上限で切らない）。

    土地ごとに1行。0 未満（手配されている土地）は `alert` の色にする。
    """
    if not entries:
        return []
    effects = [{"text": "{}：{}".format(name, value), "condition": "always", "bullet": False,
                "tone": "alert" if value < 0 else "plain"} for name, value in entries]
    return [{"name": WANTED_HEADING, "effects": effects, "text": ""}]


def detail_pieces(entries, active=None):
    """説明の窓の文 `[(文, 色の鍵)]`。鍵は name / effect / inactive / text と、効果の `tone`。1件ごとに空行で区切る。

    効果は1行ずつ。`active`（いまの場面の名前の集まり）を渡すと、条件が当てはまらない効果を
    `inactive`（灰色）にする。渡さなければ全部 `effect`。
    効果に `tone` があれば、当てはまるときの色はその鍵にする（手配度の `plain` / `alert`）。
    名前が空の件は名前の行を作らず、次の行から書き始める。
    """
    pieces = []
    for index, entry in enumerate(entries):
        if index:
            pieces.append(("\n\n", None))
        lead = ""
        if entry.get("name"):
            pieces.append((entry["name"], "name"))
            lead = "\n"
        for effect in entry.get("effects") or ():
            on = active is None or effect.get("condition", "always") in active
            head = "" if effect.get("bullet") is False else "・"
            pieces.append((lead + head + effect["text"],
                           (effect.get("tone") or "effect") if on else "inactive"))
            lead = "\n"
        if entry.get("text"):
            pieces.append((lead + entry["text"], "text"))
    return pieces


def detail_pos(mouse, size, window, gap):
    """説明の窓の左下の位置。マウスの右下に出し、はみ出すなら左・上へ回す。"""
    x = mouse[0] + gap
    if x + size[0] > window[0]:
        x = mouse[0] - gap - size[0]
    y = mouse[1] - gap - size[1]
    if y < 0:
        y = mouse[1] + gap
    return max(0.0, min(x, window[0] - size[0])), max(0.0, min(y, window[1] - size[1]))


#: 二つ名の引き直しの頼み。ボタンが押されたらこの名前のファイルを書き、
#: 評判 MOD が次の照合で消して応える（呼び合わずにファイルで繋ぐ。TECH.md §3.2.3）。
#: 中身は読まれない（在ることが頼み）。
REROLL_SUFFIX = ".reroll.json"

#: 引き直しボタンの置き場所の右端。レベル行の箱（x 0.03〜0.78）のすぐ右。
#: ふだんは二つ名の文字の直後に置き（`reroll_pos`）、文字の幅を測れないときと、
#: 文字が箱の右端まで届くときだけここに置く。
#: いつもここに置くと、短い二つ名では文字から大きく離れて見える。
#: 大きさはボタン側の固定値（`ui.make_icon_button` の正方形）を使うので、位置だけ持つ。
#: 箱の割合で置くと、窓に合わせて横長に潰れる（VERIFICATION_LOG.md §2.46）。
REROLL_POS = {"x": 0.79, "center_y": 0.875}

#: 二つ名の文字の終わりから引き直しボタンまでの間（upx）。
REROLL_GAP = 8.0

#: 引き直しボタンの絵柄の名前（角を落としたサイコロの面と3つの目）。形はローダの `ui.icon_strokes` が持つ。
#: 円弧＋矢尻の形は、408 の立ち絵の描き直しボタンと並ぶとどちらが何を更新するのか見分けが付かない。
#: `317_` の評判の画面の再生成のボタンも同じ形を使うので、形はローダに置く
#: （2つ以上の MOD が同じ意味で使う絵柄はローダに置く。TECH.md の HUD の節）。
REROLL_ICON = "さいころ"


def reroll_pos(text_width, padding_left, layout_width, gap):
    """引き直しボタンの `pos_hint`。レベル行の文字（二つ名まで）の直後に置く。

    値はレイアウトに対する割合。レベル行の箱の左端（`BOXES` の x）に、
    箱の左の余白・文字の幅・間を足す。
    `REROLL_POS` より右へは出さない（能力値の列に食い込む）。
    測れない値が1つでもあれば `REROLL_POS`。
    """
    try:
        text_width = float(text_width)
        padding_left = float(padding_left)
        layout_width = float(layout_width)
        gap = float(gap)
    except (TypeError, ValueError):
        return dict(REROLL_POS)
    if text_width <= 0 or layout_width <= 0:
        return dict(REROLL_POS)
    left = BOXES["character_sheet_basic_info"][1]["x"]
    x = left + (padding_left + text_width + gap) / layout_width
    return {"x": min(x, REROLL_POS["x"]), "center_y": REROLL_POS["center_y"]}


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

    パスの形は GAME.md §2.11 のエリアBGM。
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


def area_sizes(areas, saved):
    """`({エリアid(str): 規模}, セーブの表から読めた数)`。セーブの表を先に、無ければ BGM から読む。

    `saved(area_id)` はセーブ側の土地の表の規模（入口がローダの `ui.area_size` を渡す。
    実行中の `Area` は `size` を持っていないので、セーブの辞書から引く。GAME.md §2.7）。
    """
    sizes, found = {}, 0
    for area_id, area in (areas or {}).items():
        size = saved(area_id)
        if isinstance(size, str) and size:
            found += 1
        else:
            size = size_from_bgm(getattr(area, "bgm", None))
        sizes[str(area_id)] = size
    return sizes, found


def wanted_entries(areas, sizes, lawfulness, max_lines=None):
    """手配度に出す `[(エリア名, 手配度)]`。

    `lawfulness` は `{エリアid(str): 手配度}`（入口が `ui.lawfulness_by_area` で引いて渡す）。
    出す条件は3つ。
    規模がダンジョンでないこと、記録が在ること（＝一度も関わっていないエリアは出さない）、
    手配度が数として読めること（読めない記録は `lawfulness` に入っていない）。
    並びはエリア id 順で、id が数として読めるならその順に均す。
    `max_lines=None`（既定）で全件。
    """
    rows = []
    for area_id, area in (areas or {}).items():
        key = str(area_id)
        if is_dungeon((sizes or {}).get(key)):
            continue
        value = (lawfulness or {}).get(key)
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
