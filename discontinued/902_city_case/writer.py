# -*- coding: utf-8 -*-
"""事件の描写を LLM に書かせる。ゲームのことも事件の論理も知らない。

ここが持つのは「どう頼んで、返ってきたものをどう検算するか」だけ。
誰が犯人か・どの手がかりが誰を消すかは一切知らないし、知る必要もない。

##### 何を任せて、何を任せないか

この MOD は「AI は描写しかしない」で成立してきた（VERIFICATION_LOG.md §2.31)。
`305_` が AI に判定させて4周外した記録もある。
だから境界をここに引く:

| | |
|---|---|
| コードが決める | 犯人・容疑者の特徴（性別/髪/体格）・どの事実が誰を消すか・手がかりの必要性と解けること・言い分の裏が取れるかどうか |
| LLM が書く | 事件のあらまし、容疑者4人の人となり（名前・職・来歴・人柄・立ち絵の語）、手がかりの言い回し |

論理を1つも渡していないので、LLM が何を返しても事件は壊れない。
最悪でも「文章が下手な事件」になるだけで、解けない事件や矛盾した事件にはならない。

##### 言い分（アリバイ）を書かせない理由

「その晩どこに居たか」は論理の一部（犯人だけ裏が取れない）。
文章として書かせると、
AI が犯人の人となりを無意識に怪しく書いてタダで割れる恐れがある。
人となりと言い分を同じ応答で書かせる限りこの危険は消えないので、
言い分は入口の表（`WHEREABOUTS` / `LONE_WHEREABOUTS`）から配る。

##### 手がかりの言い回しは検算してから使う

「髪が黒い」を書かせたのに「黒髪の大柄な男を見た」と返ってくると、**渡していない特徴まで漏れて**推理が壊れる。
だから2つとも確かめる:

  1. 頼んだ特徴の語が入っている
  2. 他の軸の語が入っていない

どちらか外れたら、その1本だけ定型文に戻す（`fallbacks`）。
全部捨てないのは、1本の失敗で他の出来まで巻き添えにしないため。

##### 届かなかったときは黙って元に戻る

LLM は遅いし、落ちるし、変なものを返す。
呼べなければ、返事が壊れていれば、検算に落ちれば、
いつでも下地（`CAST_POOL`）に戻る。
始まらないくらいなら、定型の事件でも始めたほうがよい。
"""

from instantale_modloader import llm

#: `send_request` に渡す名前。
#: ゲームのマネージャ名と被らせない。
#: この名前で `output_data/<世界>/<主人公>/<名前>/` にログが残るので、
#: 何を頼んで何が返ったかは後から確認できる。
MANAGER_NAME = "mod_city_case_writer"

#: LLM が返す文字列の上限。
#: 必ず切る。
#: 長文を返されると選択肢や一覧の表示が崩れる。
LIMITS = {"premise": 120, "name": 24, "job": 24, "profile": 120,
          "personality": 120, "look": 200, "fact": 60}

#: 名前に使えない文字（`110_fix_character_name_path` の対象）。
#: これが入ると**立ち絵だけが無言で作られなくなる**ので、入口で弾く。
BAD_NAME_CHARS = set('"<>:|?*/\\')


def available(module):
    """この版で LLM を呼べるか。呼べないなら黙って使わない。"""
    return (module is not None
            and callable(getattr(module, "send_request", None))
            and callable(getattr(module, "create_model", None)))


def build_structure(module, count):
    """構造化出力の型を組む。`Literal` は使わない。

    空の `Literal[]` は
    pydantic が拒否してゲームごと落ちる（`203_probe_create_model` が押さえた実際の落ち方）。
    選択肢を型で縛りたくなる場面だが、ここは全部ただの `str` にして、
    縛りは検算側で持つ。
    """
    import typing

    create_model = module.create_model
    person = create_model(
        "CityCasePerson",
        name=(str, ...), job=(str, ...), profile=(str, ...),
        personality=(str, ...), look=(str, ...))
    return create_model(
        "CityCaseMaterial",
        premise=(str, ...),
        people=(typing.List[person], ...),
        facts=(typing.List[str], ...)), count


def wanted_facts(facts):
    """書かせてよい目撃情報だけ。`skip` が立っているものは外す。

    ##### 裏取り（アリバイ）は書かせない

    裏取りの文は特定の容疑者の名を挙げてその人物を除外する。
    ところが名前はこの同じ応答で作らせているので、頼む時点ではまだ無い。
    「誰の裏が取れたのか」を書きようがなく、
    返ってくるのは「あの晩は人前に居たのを覚えている」のような誰も指していない文になる。
    それを手がかりとして出すと、拾っても誰も消せない。

    名前が出来てから入口の側で組む（`ALIBI_FACT`）ほうが確実なので、
    ここでは頼まない。
    検算で弾くのではなく最初から頼まないのが要点で、
    頼んでから捨てると LLM の手間と待ち時間が丸ごと無駄になる。
    """
    return [fact for fact in facts if not fact.get("skip")]


def build_prompt(world, area, people, facts, count, incident=None,
                 place=None):
    """頼み文を組む。特徴は指定して渡す（人物像に合わせさせるため）。

    `people` は `[{"words": "黒髪の大柄な女",
                   "detail": [("髪", "黒髪の"), ("体格", "大柄な")]}]`。
    `facts` は `[{"word": "黒髪", "accept": [...], "avoid": [...]}]`。

    **どの特徴を指定するかは事件ごとに変わる**ので、軸の名前を決め打たない。

    `place` はその町の実データ（`world_notes` / `area_notes` / `facilities` /
    `residents`）。
    渡せるものだけ渡す。
    世界によっては説明が空のこともあるので、無い項目はその行ごと出さない。

    犯人が誰か・どの事実が誰を消すかは1文字も渡していない。
    """
    place = place or {}
    lines = [
        "あなたはテーブルトークRPGの進行役です。",
        "ある町で起きた事件について、登場人物と目撃情報の文章だけを作ってください。",
        "",
        "【世界】{}".format(world or "名も無き世界"),
    ]
    # 実在の町を渡す。
    # 架空の宿屋を書かれるより、
    # いま歩いている町の宿屋の名前が出るほうが「その町の事件」になる。
    # 渡せるものだけ渡す。
    if place.get("world_notes"):
        lines.append("　{}".format(place["world_notes"]))
    lines.append("【町】{}".format(area or "名も無き町"))
    if place.get("area_notes"):
        lines.append("　{}".format(place["area_notes"]))
    if place.get("facilities"):
        lines.append("【この町に実在する場所】")
        for name, kind, owner in place["facilities"]:
            lines.append("　・{}{}{}".format(
                name,
                "（{}）".format(kind) if kind else "",
                "　主: {}".format(owner) if owner else ""))
        lines.append("　**話に出す場所は、この中から選ぶこと。**新しい場所を"
                     "作らないでください。")
    if place.get("residents"):
        lines.append("【この町に既に居る人物】{}".format(
            "、".join(place["residents"])))
        lines.append("　**この人たちと同じ名前を付けないでください。**")
    lines += [
        "",
        "■ 事件のあらまし（premise）",
        "【何が起きているか】{}".format(
            (incident or {}).get("brief") or "町で盗みが続いている"),
        "これを1文（{}文字以内）で書いてください。".format(LIMITS["premise"]),
        "何が狙われたかを具体的に。**犯人が誰かは書かないこと。**",
        "",
        "■ 容疑者 {}人（people）".format(count),
        "この町に居そうな人物を{}人。**指定した見た目に必ず合わせること。**".format(count),
        "全員が「怪しい」わけではなく、ただその晩その辺りに居ただけの町の人です。",
        "**誰が犯人かは決めないでください。**特定の1人を怪しく書かないこと。",
    ]
    targets = [t for t in ((incident or {}).get("targets") or []) if t]
    if targets:
        lines.append("狙われたものの例（そのまま使わなくてよい）: {}".format(
            "、".join(targets)))
    lines.append("")
    # **どの特徴を指定するかは事件ごとに変わる**（`patterns/traits.json` から引いた軸）。
    # ここで軸の名前を決め打たない。
    for index, person in enumerate(people, 1):
        detail = "／".join("{}は{}".format(label, word)
                          for label, word in person.get("detail") or [])
        lines.append("  {}人目: {}{}".format(
            index, person.get("words", ""),
            "（{}）".format(detail) if detail else ""))
    lines += [
        "",
        "各人について次を書いてください:",
        "  name … 日本語の名前。{}文字以内。**記号を入れない**"
        "（\" < > : | ? * / \\ は使用禁止）".format(LIMITS["name"]),
        "  job … 生業を短く（{}文字以内）".format(LIMITS["job"]),
        "  profile … どういう人物か（{}文字以内）".format(LIMITS["profile"]),
        "  personality … 人柄と喋り方（{}文字以内）".format(LIMITS["personality"]),
        "  look … 立ち絵生成用の英語のタグ列（{}文字以内）。"
        "上で指定した見た目を必ず含めること".format(LIMITS["look"]),
        "",
        "■ 目撃情報 {}件（facts）".format(len(facts)),
        "町の人が語った目撃情報として、次の内容を1文ずつ（各{}文字以内）"
        "書いてください。".format(LIMITS["fact"]),
    ]
    for index, fact in enumerate(wanted_facts(facts), 1):
        lines.append("  {}件目: 「{}」ということだけを伝える文。".format(
            index, fact.get("word", "")))
        # 語をそのまま渡す。
        # 言い換えは認めるが、
        # 認める語を先に見せておくほうが通りやすい（検算と同じ表を見せている）。
        accept = [w for w in (fact.get("accept") or ()) if w]
        if accept:
            lines.append("        次のどれかの語を必ず入れること: {}".format(
                "／".join(accept)))
        avoid = fact.get("avoid", [])
        if avoid:
            lines.append("        **次の語には触れないこと: {}**".format(
                "／".join(avoid)))
    lines += [
        "",
        "指定した以外の特徴を足さないでください。"
        "目撃情報に犯人の名前や職業を書かないでください。",
    ]
    return "\n".join(lines)


def as_messages(message):
    """頼み文を `send_request` が受け取れる形にする。リストで渡す。

    実機で踏んだ。
    素の文字列を渡すとゲームの中でこうなる:

        TypeError: can only concatenate list (not "str") to list
          request_llm_inference_llama_cpp_completion.py:187
            in send_request_on_id

    ゲーム側は `自前のリスト + messages` をしているので、**リストでなければならない**（引数名も `messages` で、
    そもそも複数形だった）。

    タチが悪いのは死に方のほうで、
    この例外は**ゲームが内部で立てた別スレッド**（`Thread-86 (send_request_on_id)`）で起きる。
    呼んだ側には戻らず、例外も飛んでこない。
    `send_request` が永久に返らないという形で現れる。
    見つけられたのは `001_crash_recorder` がスレッドの例外を拾っていたから。
    """
    if isinstance(message, (list, tuple)):
        return list(message)
    return [{"role": "user", "content": message}]


def ask(ctx, message, structure, timeout=None, write=None):
    """LLM を呼ぶ。落ちても呼び出し側には None しか返さない。

    呼び方（プロバイダを名指ししない・`timeout` を必ず渡す・返却を辞書に均す）はローダに集約してある（`llm.ask`。
    `311_` / `313_` と共有。TECH.md §5.3）。
    ここが持つのは何を聞くか（`build_prompt`）と、
    返事をどう検算するか（`read_people` / `read_facts`）だけ。

    `timeout` を渡しても返ってこない場合は面倒を見ない。
    呼び出し側はその備え（`city_case.py` の `BUSY_GRACE`）を別に持つこと。
    """
    payload = llm.ask(ctx, MANAGER_NAME, as_messages(message),
                      timeout=timeout, structure=structure,
                      label="city case", write=write)
    if payload is None and write:
        write("    writer: no readable payload came back")
    return payload


def as_dict(raw, write=None):
    """返ってきたものを辞書にする。形を決めつけない（`llm.as_dict`）。"""
    got = llm.as_dict(raw)
    if got is None and write:
        write("    writer: cannot read a {}".format(type(raw).__name__))
    return got


def _text(value, limit):
    """文字列として受け取れる形に整える。受け取れなければ None。"""
    if not isinstance(value, str):
        return None
    got = " ".join(value.split())
    if not got:
        return None
    return got[:limit]


def read_people(payload, count, write=None):
    """人物の並びを取り出して検算する。1人でも欠けたら全部捨てる。

    人数が足りない事件は組めないし、
    一部だけ下地で埋めると「この人だけ文体が違う」という分かりやすい綻びになる。
    """
    if not isinstance(payload, dict):
        return None
    rows = payload.get("people")
    if not isinstance(rows, list) or len(rows) < count:
        if write:
            write("    writer: wanted {} people, got {}".format(
                count, len(rows) if isinstance(rows, list) else "none"))
        return None
    out, seen = [], set()
    for row in rows[:count]:
        row = as_dict(row) or {}
        person = {}
        for key in ("name", "job", "profile", "personality", "look"):
            got = _text(row.get(key), LIMITS[key])
            if got is None:
                if write:
                    write("    writer: a person is missing {!r}".format(key))
                return None
            person[key] = got
        if set(person["name"]) & BAD_NAME_CHARS:
            # `110_` の対象。
            # 立ち絵だけが無言で作られなくなるので通さない。
            if write:
                write("    writer: name {!r} has characters that break the "
                      "portrait path".format(person["name"]))
            return None
        if person["name"] in seen:
            if write:
                write("    writer: duplicate name {!r}".format(person["name"]))
            return None
        seen.add(person["name"])
        out.append(person)
    return out


def read_premise(payload):
    if not isinstance(payload, dict):
        return None
    return _text(payload.get("premise"), LIMITS["premise"])


def read_facts(payload, facts, write=None):
    """目撃情報の言い回しを取り出す。1本ずつ検算する。

    戻り値は `facts` と同じ長さの並びで、**検算に落ちた要素は None**。
    呼び出し側はそこだけ定型文に戻せばよい。
    1本の失敗で他まで捨てない。

    確かめるのは2つ:

      1. 頼んだ特徴の語が入っている（入っていなければ別のことを言っている）
      2. 他の軸の語が入っていない（渡していない特徴まで漏らすと、
         その1文で犯人が決まってしまい推理が消える）
    """
    rows = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [None] * len(facts)
    out, asked = [], -1
    for fact in facts:
        if fact.get("skip"):
            # 頼んでいないので、返事の並びも1つ進めない。
            # 定型のまま。
            out.append(None)
            continue
        asked += 1
        got = _text(rows[asked], LIMITS["fact"]) if asked < len(rows) else None
        if got is None:
            out.append(None)
            continue
        # 言い換えを認める。
        # 「大柄な」を頼んで『大きな影』と返るのは正しい仕事なので、
        # 語の完全一致で捨てない（実機で3本とも落ちた）。
        # 認める語は入口が `accept` で渡す。
        accept = [w for w in (fact.get("accept") or ()) if w]
        if not accept and fact.get("word"):
            accept = [fact["word"]]
        if accept and not any(word in got for word in accept):
            if write:
                write("    writer: fact {} says none of {}: {!r}"
                      .format(asked + 1, accept, got))
            out.append(None)
            continue
        leaked = [other for other in fact.get("avoid", []) if other in got]
        if leaked:
            if write:
                write("    writer: fact {} leaks {}: {!r}".format(
                    asked + 1, leaked, got))
            out.append(None)
            continue
        out.append(got)
    return out
