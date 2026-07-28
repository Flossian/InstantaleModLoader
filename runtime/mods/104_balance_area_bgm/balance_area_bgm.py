# -*- coding: utf-8 -*-
"""新しく生成されるエリアの BGM が偏らないようにする。

何が問題か
----------
BGM はエリアが作られたときに一度だけ決まり、そのままセーブに書き込まれる。

    areas["7"]["bgm"] = "Assets/sounds/musics/town/solemn/Ambient 7 Loop.mp3"

パスは musics/<size>/<mood>/<曲名> という3階層になっていて、
size はエリアの種類（town / village / city / dungeons / battle）、
mood は雰囲気（calm, eerie, solemn ...）を表す。

実際のセーブデータ（%LOCALAPPDATA%\\Darmabeko\\Instantale\\worlds の3ワールド、
107 エリア）を調べたところ、偏っていたのは曲の選択ではなく mood の選択だった。

    集落系     26 エリアで、9 種類ある mood のうち 5 種類しか使われていない
               calm / desolate / heroic / scary は一度も選ばれていない
               ワールド peld では、9 つの集落のうち 7 つが eerie
    ダンジョン 80 エリアで、eerie が 18 に対し desolate は 1
    全体       mood フォルダにある 97 曲のうち 50 曲が一度も使われていない

calm フォルダだけで 12 曲あるが、そのどれにも到達できない状態になっていた。

何をするか
----------
size はそのまま維持して、mood と曲だけを選び直す。
size はエリアの種類という事実であって、好みの問題ではないため。
選び方は「ワールド全体で使用回数がいちばん少ない曲を優先」。
これでその size が持っている全ての曲に散らばる。

mood 単位ではなく曲単位で均しているのは意図的。
「いつも同じ曲が流れる」という印象を決めるのは実際に耳にする曲の種類数なので、
6 曲を持つ mood が 1 曲しか持たない mood より多くのエリアを引き受けるのは、
むしろ自然な結果と考えている。もちろん、どの mood も使われないままにはならない。

最少優先に加えてもう1つルールがある。
そのエリアの今の曲が既に最少グループに入っているなら、変更せずそのままにする。
この結果、同じデータに対して何度実行しても結果が変わらない。
フックが何回発火しても壊れないので、どのフックを使うかを厳密に決めなくて済む。

なお最初の版では「最少の曲がその mood の中にあるなら、生成側が選んだ mood を
尊重する」というルールも入れていた。しかし実データで試すと、これが取り除きたい
偏りをそのまま復活させてしまった（peld の集落は 9 mood 中 2 種類のまま。
eerie にまだ未使用の曲が残っていたため）。今はこの優先を外し、同率最少の曲は
一様に選んでいる。結果として mood の使われ方は、各 mood が持つ曲数に比例する。

どこまで触るか
--------------
書き換えるのは、注入した後に新しく現れたエリアだけ。
mod が最初にワールドを見た時点で既にあったエリアは、集計には数えるが
書き換えはしない（数えるのは、新しいエリアが「使われすぎている曲」を
避けられるようにするため）。プレイヤーが既に知っている場所の音楽を
後から変えるのは、この mod の役目ではないと考えている。
既存ワールドをまとめて均したい場合は tools/rebalance_saved_bgm.py を使う。

もう1つ触らないものがある。musics/ の直下に置かれた曲
（それ以外.mp3 や JDSherbert のアンビエント）を指しているエリア。
これらは mood のセットではなく単発の曲なので、そこを指しているのは
偏りではなく意図的な指定と解釈している。

フックについて
--------------
以下の3つを対象にしている。いずれも out/recon/targets.txt に実在を確認済み。

    save_area_json:write_area_data_to_world_dict(world_dict, area_id)
    save_area_json:generate_quest_area(world_dict, quest_value, next_area_id)
    save_world_json:write_obfuscated_json_file(file_path, data)

確認できていないのは、bgm の値が実際にどこで決まっているのか。
ソースがコンパイル済みなので、これは調べようがない。
上2つは対象のエリア id を引数で受け取るのでピンポイントに均せる。
最後の1つはセーブがディスクに書かれる際に必ず通る、取りこぼし防止用。
どれが発火しても結果は同じになるようにしてあり、実際にどれだったかは
out/bgm.log を見れば分かる。
"""

import datetime
import os
import random
import sys

NAME = "Area BGM balance"
NAME_JA = "エリアBGMの均し"
VERSION = "1"
DESCRIPTION = "Evens out BGM for newly generated areas so unused tracks get played too"
DESCRIPTION_JA = "新しく生成されるエリアの BGM の偏りを均し、使われていなかった曲も出るようにする"
AUTHOR = "R01/Flossian"

MUSIC_MARKER = "assets/sounds/musics"
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".opus", ".m4a")

# エリアの size とフォルダ名は、ダンジョンだけ単数形と複数形で食い違っている。
# 実際のセーブ5件で確認した値: dungeon x120, town x22, village x13, city x10。
SIZE_ALIAS = {"dungeon": "dungeons"}

# bgm が空で、書式をコピーする元が無いエリアのための既定値。
# セーブに入っている値は全てこの形をしている。
DEFAULT_PREFIX = "Assets/sounds/musics"
DEFAULT_SEP = "/"

# 実データでは 6 エリアが bgm "" を持っていた（Astergrave の 2/3/5/6/8、dos の 3）。
# つまり無音の町や都市。size さえ分かれば曲を選べるので、これらにも曲を入れている。
# 無音のままにしたい場合は False にする。
FILL_MISSING = True

_rng = random.Random()          # ゲーム側の乱数列に影響しないよう専用に持つ
_pool_cache = {}                # musics のパス -> {size: [(mood, 曲名), ...]}
_seen = {}                      # ワールド -> 最初に見たときに存在したエリア id


# --------------------------------------------------------------------------
# パスの解析と組み立て
# --------------------------------------------------------------------------
def parse_bgm(src):
    """bgm のパスを (prefix, size, mood, 曲名, 区切り文字) に分解する。

    prefix は ".../musics" までの前半部分。ここをそのまま残して使い回すので、
    書き換えた値がゲーム自身の書く値と同じ見た目になる。

    mood フォルダ内の曲でないものには None を返す。具体的には、
    空文字列、musics/ 直下のファイル、音声以外の拡張子。
    """
    if not isinstance(src, str) or not src.strip():
        return None

    # 元のパスがどちらの区切り文字を使っていたか覚えておき、書き戻すときに合わせる。
    sep = "\\" if src.rfind("\\") > src.rfind("/") else "/"
    parts = src.replace("\\", "/").split("/")
    if len(parts) < 4:
        return None           # musics/size/mood/曲名 の4要素に足りない

    track, mood, size = parts[-1], parts[-2], parts[-3]
    if not track.lower().endswith(AUDIO_EXT):
        return None

    # 後ろ3階層を除いた部分が musics で終わっているか確認する。
    # ここが合わなければ、そもそも BGM プールの中のパスではない。
    head = "/".join(parts[:-3]).lower()
    if not head.endswith(MUSIC_MARKER):
        return None

    prefix = sep.join(parts[:-3])
    return prefix, size, mood, track, sep


def build_bgm(prefix, size, mood, track, sep):
    return sep.join([prefix, size, mood, track])


def size_folder(area, pool):
    """このエリアがどのフォルダから曲を引くかを決める。基準は area["size"]。

    今入っている bgm のパスから逆算する方法も考えられるが、それだと
    誤ったフォルダに入っているエリアが永久に直らない。たとえば size が
    dungeon なのにパスが town/ を指しているエリアは、以後もずっと town/ の
    中で選び直されてしまう。
    パスを見るのは、使える size を持たないエリアに対する保険としてだけ。
    """
    size = area.get("size") if isinstance(area, dict) else None
    if isinstance(size, str) and size:
        folder = SIZE_ALIAS.get(size, size)
        if folder in pool:
            return folder

    parsed = parse_bgm((area or {}).get("bgm"))
    if parsed is not None and parsed[1] in pool:
        return parsed[1]
    return None


def path_style(areas):
    """このワールドが実際に使っている (prefix, 区切り文字) を借りてくる。"""
    # 1件でも解析できるパスがあれば、その書式に合わせる。無ければ既定値。
    for aid in area_order(areas):
        parsed = parse_bgm((areas.get(aid) or {}).get("bgm"))
        if parsed is not None:
            return parsed[0], parsed[4]
    return DEFAULT_PREFIX, DEFAULT_SEP


def current_choice(area, folder):
    """このエリアが今持っている (mood, 曲名)。使えない場合は (None, None)。

    (None, None) は「尊重すべき現在の選択が無い」という意味。
    bgm が空のときと、入っていても size が示すフォルダの外を指しているときが該当する。
    """
    parsed = parse_bgm((area or {}).get("bgm"))
    if parsed is None or parsed[1] != folder:
        return (None, None)
    return (parsed[2], parsed[3])


def is_special(area):
    """mood フォルダの外を指している bgm かどうか。該当するものは一切触らない。

    musics/ の直下に置かれた曲（それ以外.mp3 や JDSherbert のアンビエント）は
    mood のセットではなく単発の曲。エリアがそこを指しているなら、それは
    均すべき偏りではなく意図的な指定と見なす。
    """
    raw = (area or {}).get("bgm")
    return bool(raw) and isinstance(raw, str) and raw.strip() != "" \
        and parse_bgm(raw) is None


# --------------------------------------------------------------------------
# ディスク上にある曲の一覧（プール）
# --------------------------------------------------------------------------
def music_root():
    """Assets/sounds/musics の場所を探す。

    リコンの結果ではゲームプロセスのカレントディレクトリがゲーム本体の
    フォルダになっていたので、まずそこを見る。
    """
    seen = []
    # カレントディレクトリ → 実行ファイルのある場所 → sys.prefix の順で試す。
    for get in (os.getcwd,
                lambda: os.path.dirname(os.path.abspath(sys.executable)),
                lambda: sys.prefix):
        try:
            base = get()
        except Exception:
            continue
        if not base or base in seen:
            continue
        seen.append(base)
        candidate = os.path.join(base, "Assets", "sounds", "musics")
        if os.path.isdir(candidate):
            return candidate
    return None


def scan_pool(root):
    """{size フォルダ: [(mood, 曲名), ...]} を作る。中身はソート済み。"""
    # ディスクを walk するのは1回で足りるのでキャッシュしておく。
    if root in _pool_cache:
        return _pool_cache[root]

    pool = {}
    try:
        sizes = sorted(os.listdir(root))
    except OSError:
        return {}

    for size in sizes:
        size_dir = os.path.join(root, size)
        if not os.path.isdir(size_dir):
            continue
        tracks = []
        try:
            moods = sorted(os.listdir(size_dir))
        except OSError:
            continue
        for mood in moods:
            mood_dir = os.path.join(size_dir, mood)
            if not os.path.isdir(mood_dir):
                # musics/battle/*.mp3 のように、mood フォルダを挟まず
                # 直接曲が置かれている場所がある。ここは対象外。
                continue
            try:
                names = sorted(os.listdir(mood_dir))
            except OSError:
                continue
            for name in names:
                if name.lower().endswith(AUDIO_EXT) and os.path.isfile(
                        os.path.join(mood_dir, name)):
                    tracks.append((mood, name))
        # 曲が1つも無い size は選びようがないので、プールに入れない。
        if tracks:
            pool[size] = tracks

    _pool_cache[root] = pool
    return pool


# --------------------------------------------------------------------------
# 選び方のルール
# --------------------------------------------------------------------------
def area_order(areas):
    """エリア id を数値順に並べる。

    辞書順のままだと "10" が "2" より前に来てしまい、同じデータでも
    処理の順番が直感と合わなくなる。数値にできない id は後ろにまとめる。
    """
    def key(aid):
        try:
            return (0, int(aid), "")
        except (TypeError, ValueError):
            return (1, 0, str(aid))
    return sorted(areas, key=key)


def count_usage(areas, pool, skip_id=None):
    """曲ごとの使用回数を数える。{フォルダ: {(mood, 曲名): 回数}}。"""
    counts = {}
    for aid in areas:
        # skip_id はこれから選び直すエリア。自分自身を数えてしまうと、
        # 今の曲の回数が 1 多く見えて、最少グループから外れてしまう。
        if skip_id is not None and aid == skip_id:
            continue
        area = areas[aid]
        folder = size_folder(area, pool)
        if folder is None:
            continue
        mood, track = current_choice(area, folder)
        if mood is None:
            continue
        bucket = counts.setdefault(folder, {})
        bucket[(mood, track)] = bucket.get((mood, track), 0) + 1
    return counts


def choose(tracks, counts, current_mood, current_track):
    """使用回数が最少の曲を選ぶ。今の曲は、既に最少グループにいる場合だけ残る。

    生成側が選んだ mood を優先することはしない。
    その理由はファイル冒頭の説明のとおりで、優先すると取り除きたい偏りが戻る。
    """
    lowest = min(counts.get(t, 0) for t in tracks)
    candidates = [t for t in tracks if counts.get(t, 0) == lowest]

    # 既に十分ばらけているなら触らない。何度実行しても結果が変わらないのはこの分岐のおかげ。
    if (current_mood, current_track) in candidates:
        return (current_mood, current_track)
    return _rng.choice(candidates)


def balance_area(areas, aid, pool):
    """1つのエリアを、他の全エリアとの兼ね合いで選び直す。

    戻り値は変更があれば (変更前, 変更後)、無ければ None。
    """
    area = areas.get(aid)
    if not isinstance(area, dict) or is_special(area):
        return None

    folder = size_folder(area, pool)
    tracks = pool.get(folder) if folder else None
    if not tracks:
        return None           # フォルダが決まらない、または候補が無い

    mood, track = current_choice(area, folder)
    # bgm が空のエリアは、FILL_MISSING が有効なときだけ埋める。
    if mood is None and not FILL_MISSING:
        return None

    prefix, sep = path_style(areas)
    counts = count_usage(areas, pool, skip_id=aid).get(folder, {})
    mood2, track2 = choose(tracks, counts, mood, track)

    src = area.get("bgm")
    new_src = build_bgm(prefix, folder, mood2, track2, sep)
    if new_src == src:
        return None           # 結果が同じなら変更として扱わない

    area["bgm"] = new_src
    return src, new_src


def balance_world(areas, pool, only=None):
    """ワールド内のエリアを id 順に選び直す。

    only に id の集合を渡すと、書き換え対象をそこだけに限定できる。
    同じデータに何度実行しても結果は変わらない（自分の順番が来た時点で
    既に最少なら、そのまま維持されるため）。
    """
    prefix, sep = path_style(areas)
    counts = {}
    changes = []

    for aid in area_order(areas):
        area = areas.get(aid)
        if not isinstance(area, dict) or is_special(area):
            continue
        folder = size_folder(area, pool)
        tracks = pool.get(folder) if folder else None
        if not tracks:
            continue

        mood, track = current_choice(area, folder)
        bucket = counts.setdefault(folder, {})

        # only の対象外のエリアは書き換えないが、使用回数には数える。
        # こうすることで、新しいエリアが「既に使われすぎている曲」を避けられる。
        if only is not None and aid not in only:
            if mood is not None:
                bucket[(mood, track)] = bucket.get((mood, track), 0) + 1
            continue
        if mood is None and not FILL_MISSING:
            continue

        mood2, track2 = choose(tracks, bucket, mood, track)
        # 決めた結果をその場で集計に足す。
        # これが無いと、同じ走査の中で後続のエリアが同じ曲に集中してしまう。
        bucket[(mood2, track2)] = bucket.get((mood2, track2), 0) + 1
        new_src = build_bgm(prefix, folder, mood2, track2, sep)
        if new_src != area.get("bgm"):
            changes.append((aid, area.get("bgm"), new_src))
            area["bgm"] = new_src

    return changes


# --------------------------------------------------------------------------
# フックの設置
# --------------------------------------------------------------------------
def apply(ctx):
    root = music_root()
    if root is None:
        ctx.log("bgm: Assets/sounds/musics not found; skipping", level="WARN")
        return

    pool = scan_pool(root)
    if not pool:
        ctx.log("bgm: no mood folders under {}; skipping".format(root), level="WARN")
        return

    log_path = ctx.out_path("bgm.log")

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("bgm: write failed")

    def world_key(container):
        """ワールドを見分けるためのキー。名前が取れなければオブジェクト id で代用する。"""
        try:
            name = (container.get("world_data") or {}).get("name")
            if isinstance(name, str) and name:
                return name
        except Exception:
            pass
        return "id:{}".format(id(container))

    def areas_of(container):
        """渡されたデータから areas を取り出す。想定した形でなければ None。"""
        if not isinstance(container, dict):
            return None
        areas = container.get("areas")
        return areas if isinstance(areas, dict) else None

    def short(src):
        # 無音のエリアに曲を入れた場合、変更前は "" か None になる。
        if not isinstance(src, str) or not src:
            return "(none)"
        return "/".join(src.replace("\\", "/").split("/")[-2:])

    def note(hook, changes):
        for aid, old, new in changes:
            write("[BALANCE] {} area {}: {} -> {}".format(
                hook, aid, short(old), short(new)))

    def handle_named_area(hook, world_dict, area_id):
        """対象のエリア id が分かるフック用。そのエリアだけを選び直す。"""
        areas = areas_of(world_dict)
        if areas is None:
            return
        # id は int で来ることも文字列で来ることもあるので、両方試す。
        key = area_id if area_id in areas else str(area_id)
        if key not in areas:
            return
        # このエリアは今この瞬間に存在している。後から「既存エリア」として
        # 除外されないよう、ここで記録しておく。
        _seen.setdefault(world_key(world_dict), set()).add(key)
        result = balance_area(areas, key, pool)
        if result:
            note(hook, [(key, result[0], result[1])])

    def handle_world(hook, container):
        """エリア id が分からないフック用。前回以降に増えた分だけを選び直す。"""
        areas = areas_of(container)
        if areas is None:
            return
        key = world_key(container)
        known = _seen.get(key)
        if known is None:
            # このワールドを見るのが初めて。今あるエリアは全て対象外として記録し、
            # 次回以降に増えた分だけを新しいエリアとして扱う。
            _seen[key] = set(areas)
            write("[BALANCE] {} first sight of {!r}: {} existing area(s) "
                  "grandfathered".format(hook, key, len(areas)))
            return
        fresh = set(areas) - known
        _seen[key] = set(areas) | known
        if not fresh:
            return
        changes = balance_world(areas, pool, only=fresh)
        note(hook, changes)

    installed = []

    # 呼び出し側はコンパイル済みなので、引数が位置で渡されるのかキーワードで
    # 渡されるのかを知る方法が無い。そこで各ラッパは *args/**kwargs をそのまま
    # 元の関数へ渡し、自分に必要な値だけを「名前か位置」で取り出す。
    # 見つからなければ何もせず、呼び出し自体は通常どおり通す。
    def arg_at(args, kwargs, index, name):
        if name in kwargs:
            return kwargs[name]
        if len(args) > index:
            return args[index]
        return None

    @ctx.wrap("save_area_json:write_area_data_to_world_dict", required=False)
    def write_area_data_to_world_dict(orig, *args, **kwargs):
        # 先に元の処理を実行する。bgm が書き込まれた後でないと選び直せない。
        result = orig(*args, **kwargs)
        try:
            world_dict = arg_at(args, kwargs, 0, "world_dict")
            area_id = arg_at(args, kwargs, 1, "area_id")
            if world_dict is not None and area_id is not None:
                handle_named_area("write_area_data_to_world_dict", world_dict, area_id)
        except Exception:
            # 選び直しに失敗してもゲーム側には影響させない。
            # BGM が偏るのは、ゲームを落としてまで防ぐような問題ではない。
            ctx.log_exc("bgm: balance failed in write_area_data_to_world_dict")
        return result

    @ctx.wrap("save_area_json:generate_quest_area", required=False)
    def generate_quest_area(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        try:
            world_dict = arg_at(args, kwargs, 0, "world_dict")
            # この関数だけはエリア id が3番目の引数（next_area_id）にある。
            area_id = arg_at(args, kwargs, 2, "next_area_id")
            if world_dict is not None and area_id is not None:
                handle_named_area("generate_quest_area", world_dict, area_id)
        except Exception:
            ctx.log_exc("bgm: balance failed in generate_quest_area")
        return result

    @ctx.wrap("save_world_json:write_obfuscated_json_file", required=False)
    def write_obfuscated_json_file(orig, *args, **kwargs):
        # こちらは書き込みの前に選び直す。ディスクに出る内容そのものを直したいため。
        try:
            data = arg_at(args, kwargs, 1, "data")
            if data is not None:
                handle_world("write_obfuscated_json_file", data)
        except Exception:
            ctx.log_exc("bgm: balance failed in write_obfuscated_json_file")
        return orig(*args, **kwargs)

    # どのフックが実際に設置できたかを記録しておく。
    # 3つのうちどれが呼ばれるかは分からないので、生きている数を把握しておきたい。
    for target in ("save_area_json:write_area_data_to_world_dict",
                   "save_area_json:generate_quest_area",
                   "save_world_json:write_obfuscated_json_file"):
        try:
            ctx.resolve(target)
            installed.append(target.split(":")[1])
        except Exception:
            ctx.log("bgm: target missing: {}".format(target), level="WARN")

    ctx.log("bgm: hooks live on {}".format(", ".join(installed) or "nothing"))
    _report_pool(ctx, write, root, pool)
    _self_test(ctx, pool)


def _report_pool(ctx, write, root, pool):
    """見つかった曲の内訳をログに出す。size ごとの mood 数と曲数。"""
    write("[BALANCE] pool under {}".format(root))
    for size in sorted(pool):
        moods = {}
        for mood, _track in pool[size]:
            moods[mood] = moods.get(mood, 0) + 1
        write("    {:<10} {} track(s) over {} mood(s): {}".format(
            size, len(pool[size]), len(moods),
            ", ".join("{}={}".format(m, moods[m]) for m in sorted(moods))))
    ctx.log("bgm: pool = {}".format(
        ", ".join("{} {}".format(s, len(pool[s])) for s in sorted(pool))))


def _self_test(ctx, pool):
    """注入した時点で動作を確認する。

    実際の処理はエリアが生成されたときにしか動かないので、
    ここで作ったデータを使って一通り検証しておく。
    """
    checks = []

    # --- パスの解析 ---
    real = "Assets/sounds/musics/town/solemn/Ambient 7 Loop.mp3"
    checks.append(("parse", parse_bgm(real) ==
                   ("Assets/sounds/musics", "town", "solemn", "Ambient 7 Loop.mp3", "/")))
    checks.append(("parse backslash",
                   parse_bgm(r"Assets\sounds\musics\dungeons\eerie\Hunted.mp3") ==
                   (r"Assets\sounds\musics", "dungeons", "eerie", "Hunted.mp3", "\\")))
    checks.append(("top-level musics ignored",
                   parse_bgm("Assets/sounds/musics/other.mp3") is None))
    checks.append(("empty ignored", parse_bgm("") is None))
    checks.append(("None ignored", parse_bgm(None) is None))
    checks.append(("round trip", build_bgm(*parse_bgm(real)[:4], sep="/") == real))

    # --- 選び方のルール ---
    # 3曲に対して6エリアなら、2/2/2 に分かれるはず。
    fake = {"town": [("a", "1.mp3"), ("a", "2.mp3"), ("b", "3.mp3")]}
    areas = dict((str(i), {"bgm": "Assets/sounds/musics/town/a/1.mp3"}) for i in range(6))
    balance_world(areas, fake)
    spread = {}
    for area in areas.values():
        spread[area["bgm"]] = spread.get(area["bgm"], 0) + 1
    checks.append(("even spread", sorted(spread.values()) == [2, 2, 2]))

    # 同じデータへの2回目は何も変えないこと。
    again = balance_world(areas, fake)
    checks.append(("idempotent", again == []))

    # フォルダを決めるのは size であってパスではないこと。
    # エリアが持つ値 dungeon が、フォルダ名 dungeons に対応する必要がある。
    if "dungeons" in pool and "town" in pool:
        misfiled = {"0": {"size": "dungeon",
                          "bgm": "Assets/sounds/musics/town/calm/Ambient 1.mp3"}}
        balance_world(misfiled, pool)
        moved = parse_bgm(misfiled["0"]["bgm"])
        checks.append(("size=dungeon -> dungeons folder",
                       moved is not None and moved[1] == "dungeons"))

        for value, want in (("town", "town"), ("village", "village"),
                            ("city", "city"), ("dungeon", "dungeons")):
            if want in pool:
                checks.append(("size=%s -> %s" % (value, want),
                               size_folder({"size": value}, pool) == want))

    # bgm が空でも、size が分かっていれば曲が入ること。
    silent = {"0": {"size": "city", "bgm": ""}, "1": {"size": "town"}}
    balance_world(silent, pool)
    checks.append(("empty bgm filled from size",
                   (parse_bgm(silent["0"].get("bgm")) or [None, None])[1] == "city"))
    checks.append(("absent bgm filled from size",
                   (parse_bgm(silent["1"].get("bgm")) or [None, None])[1] == "town"))

    # size もパスも無い場合は判断材料が無いので、何もしないこと。
    nothing = {"0": {"bgm": ""}, "1": {}}
    balance_world(nothing, pool)
    checks.append(("no size and no path -> untouched",
                   nothing["0"]["bgm"] == "" and "bgm" not in nothing["1"]))

    # mood フォルダの外を指す曲には触らないこと。
    special = {"0": {"size": "town", "bgm": "Assets/sounds/musics/それ以外.mp3"}}
    balance_world(special, pool)
    checks.append(("top-level one-off untouched",
                   special["0"]["bgm"] == "Assets/sounds/musics/それ以外.mp3"))

    # only を指定したとき、それ以外のエリアが書き換わらないこと。
    mixed = dict((str(i), {"bgm": "Assets/sounds/musics/town/a/1.mp3"}) for i in range(4))
    before = mixed["0"]["bgm"]
    balance_world(mixed, fake, only={"3"})
    checks.append(("only= confines writes",
                   mixed["0"]["bgm"] == before and mixed["1"]["bgm"] == before))

    failed = [name for name, ok in checks if not ok]
    if failed:
        ctx.log("bgm: VERIFY FAILED: {}".format(", ".join(failed)), level="ERROR")
    else:
        ctx.log("bgm: verified {} check(s)".format(len(checks)))
