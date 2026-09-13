# -*- coding: utf-8 -*-
"""MOD が持つ NPC と、正規 NPC への被せ。

`npcs.make_npc`（GAME.md §2.23）が作るのは**セーブに残る本物**で、
MOD を外しても世界に残る。
こちらはその裏返しで、**ゲームが id で引く場所に現れない** NPC を MOD の側が丸ごと持つ。
残さないのは機械的な参照（`npcs` の項目・`party`・ボタンの引数・敵の辞書）で、
ほかの住人の記憶に残る名前は消さない（TECH.md §5.7「残る足跡」）。
同じ仕掛けを正規 NPC の上に被せれば、本物の素データを書き換えずに拡張できる。

##### 2つの使い方が1つの登録簿に乗る

| 対象 | id | 実体 | 保存のとき |
|---|---|---|---|
| MOD の NPC | `mod:<持ち主>:<鍵>` | ここで組む | 実体を控えに写し、名簿と素データの反復から隠す |
| 正規の NPC | ゲームの整数の id | ゲームのもの | 何もしない（層は頼み文とフックにだけ効く） |

どちらも「id に層を積む」という同じ形になる。
層（`register` の1回）は MOD 1本ぶんの登録で、同じ id に複数の MOD が積める。
持ち主（`owner`）が同じ層は積み重ならず差し替わるので、注入し直しても増えない。

##### なぜ id を文字列にするのか

`ids.claim` で採るとセーブの採番台帳（`index['npc']`）が進み、足跡が残る。
かといって適当な整数を振ると、ゲームが次の町を生成するときに同じ番号を踏む
（GAME.md §2.23 の事故）。
ゲームの採番は整数の連番なので、文字列にすれば名前空間が分かれて両方を避けられる。
`world.characters` は辞書なので、文字列の鍵でも載る。

##### 関所は1箇所

id で引かれる場所に残さないことを守っているのは、**保存の直前に引き上げる**ことだけ。
その関所を MOD ごとに持つと、MOD が増えるほど漏れる口が増える。
`install(ctx)` が包むのは1箇所で、どの MOD から呼ばれても1つしか立たない
（登録簿が空なら何もせずに素通しする）。

引き上げる先は4つあり、**名簿と素データだけでは足りない**:

| 器 | なぜ |
|---|---|
| `world.characters` | 保存が舐めて書く |
| `save_data_dict['npcs']` / `world_dict['npcs']` | 素データの写し |
| 選択肢（`buttons` / `buttons_backup` / …）と自由入力の `PhaseSpec` | `args` に id が載る（`SAVED_CHOICE_ATTRS`） |
| パーティ（`ui.party_stores`）と戦闘中の敵 | 名簿と同じく id の並び |

##### 何を差し替えられるか

| 登録するもの | 効く場所 |
|---|---|
| `fields` | MOD の NPC を組むときの初期値（控えの写しが在ればそちらが勝つ） |
| `on["conversation_start"]` / `["conversation_end"]` | 会話の開始・要約の後 |
| `prompt` | 会話の頼み文を組む直前（`llm_manager:conversation_*` の引数） |
| `on["detail"]` / `["detail_done"]` | 詳細生成（`ensure_npc_detail_generated`）の前と後 |
| `on["image"]` | 立ち絵の作り直し |
| `on["world"]` | セーブを読んだ直後。建て直すならここ |
| `on["save"]` | 保存の直前と直後 |

`fields` は**セーブの項目名**（`npcs.NEW_NPC_TEMPLATE` の33項目）で受ける。
`Character` 側で名前が違うものは `FIELD_TO_ATTR` が吸収する。

##### 真実は実体1つ

値の変換も戻しもしない。MOD の NPC は実体に直接書き、保存のたびにローダが実体を控えへ写す
（正規 NPC でゲームがやっている保存を、場所を変えてやる）。
正規 NPC の項目を書けばそれは本物の変更で、ゲームがセーブに書く。
「セーブに残さず画面上だけ変える」機構は持たない（往復の機構は同期の穴を作るので外した。2026-09-13）。
頼み文だけに効かせるなら `notes` / `prompt`。

##### 未確認

実機では何も確かめていない（測るのは `229_probe_mod_npc`）。
文字列 id の `Character` がセーブに漏れないか、会話が始まって終わるか、
詳細生成が素データを引かずに済むかは、この時点ではどれも見込みでしかない。
**パーティ加入はできない**（関所が断る）。
仲間は `save_data_dict['npcs']` に素データが在ることが前提で（実セーブで確認。2026-09-13）、
ここで組む NPC はそこに出ないので、加入したまま保存するとロードで組み立てられない。
仲間にしたい人物は `npcs.make_npc` で本物として作る。

戦闘はここでは何も引き受けていない。
素の住人は**会話の直前に `ensure_npc_detail_generated` で HP・スキル・立ち絵が埋まる**ので、
会話から挑んでも落ちない。実測で落ちたのは `make_npc` で作った詳細生成前の NPC
（`902_` の容疑者。VERIFICATION_LOG.md §2.40 / §2.42）で、素の住人が落ちた記録は無い。
ここで組んだ人物は既定でその詳細生成を本体へ通していない（`on["detail"]` が True を返した層が
無ければ素通し）ので、**話しかけてから挑むと素の住人では起きない落ち方をする**。
本体に埋めさせたとき素データを引いて落ちるか、素データへ書いて漏れるかは
`229_` の `TRY_DETAIL` が測る。それまでは会話で成り立つ人物に限る。

##### 控えはローダが持つ

`state\\modnpc\\<世界>.json` に、持ち主ごとに MOD の NPC の 存在・置き場所・実体の写し を控える。
世界を読み直すと、その持ち主の層が登録されているものだけ組み直して置く（`restore_world`）。
MOD を外せば何も戻らない。MOD 固有の続き（例えば出資の帳簿）は `state.WorldStore` で MOD が持つ。
"""

import copy
import inspect
import sys

from . import frames, log, log_exc, npcs, patch, state, ui

#: MOD の NPC の id の接頭辞。
#: ゲームの採番は整数の連番なので、ここが被ることはない。
PREFIX = "mod:"

#: 登録簿の置き場所。
#: `apply()` は1プロセスで何度も呼ばれる（TECH.md §3.5）ので、
#: モジュールのグローバルではなく `sys` に置いて注入をまたがせる。
REGISTRY_ATTR = "_instantale_modnpc_registry"

#: 関所を立てた世代の控え。同じ世代で二度立てない。
INSTALLED_ATTR = "_instantale_modnpc_installed"

#: 保存の間、MOD の NPC を名簿の**反復**から隠すか。
#: 最初は名簿から外していたが、外している窓（実機で約0.5秒。保存は別スレッド）の間は
#: ゲーム自身のコードもその id を引けず、`ConversationStartManager.__init__` と
#: `resolve_conversation` が `KeyError` で落ちた（2026-09-12）。
#: いまは `world.characters` を `_RosterView`（反復では隠し、id では引ける）に
#: 差し替えるので、窓は無い。保存が名簿を読まないと分かれば False にしてよい。
LIFT_ROSTER = True


class _RosterView(dict):
    """保存の間だけ `world.characters` に成り代わる名簿。

    **格納しているのは見せる分だけ**なので、反復・`len`・`dict(view)`・`json` の
    どれで舐めても MOD の NPC は出ない（dict の C レベルの複製は `items()` を
    呼ばず内部の格納を写すので、隠し方を Python 側の `items()` だけに頼れない）。
    隠した分は `hidden` に持ち、id で引く読み（`[]` / `get` / `in`）だけそこへ落ちる。
    保存が名簿を舐めて書く経路でも書かれず（実機 2026-09-12: 舐めている。
    来訪者を残すと `AttributeError` で保存が落ち、外すと通った）、
    その間にゲームが id で引いても `KeyError` にならない。
    書き込みは元の辞書にも通す（保存中に生まれた NPC を失わない）。
    """

    def __init__(self, source, hidden_ids):
        hidden = set(hidden_ids)
        dict.__init__(self, {k: v for k, v in source.items() if k not in hidden})
        self.source = source
        self.hidden = {k: source[k] for k in hidden if k in source}

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.hidden[key]

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        return self.hidden.get(key, default)

    def __contains__(self, key):
        return dict.__contains__(self, key) or key in self.hidden

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self.source[key] = value

    def __delitem__(self, key):
        if dict.__contains__(self, key):
            dict.__delitem__(self, key)
        self.hidden.pop(key, None)
        self.source.pop(key, None)

    def pop(self, key, *default):
        found = dict.__contains__(self, key) or key in self.hidden
        if not found:
            if default:
                return default[0]
            raise KeyError(key)
        value = self[key]
        del self[key]
        return value

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __repr__(self):
        return "RosterView({} visible, {} hidden)".format(
            dict.__len__(self), len(self.hidden))


#: セーブの項目名 → `Character` の属性名。
#: 2つだけ名前が違う（`npcs.CHARACTER_KWARGS` の注記）。
FIELD_TO_ATTR = {"ability_scores": "original_ability_scores",
                 "knowledge": "knowledges"}

#: 誰とも話していない NPC の `relationship`。
#: ひな型（`npcs.NEW_NPC_TEMPLATE`）は None だが、実セーブでは詳細生成前の個体を含む
#: 87/87 がこの形だった（2026-09-12）。`make_npc` の側はゲームが埋めるが、
#: ここは素データを通らないので自分で持つ。
DEFAULT_RELATIONSHIP = {"player": {"affinity": 0, "affinity_text": "警戒心がある",
                                   "relationship": ["初対面"],
                                   "conversation_count": 0}}

#: 組んだ直後の `config`。`npcs.DEFAULT_CONFIG` と違って `level_of_detail` は **1**。
#: 2（詳細生成済みの値）で組むと、ゲームは「もう埋まっている」とみなして
#: 会話の直前の `ensure_npc_detail_generated` を呼ばない（実機 2026-09-12。
#: 一覧を組むときも `config` を読んでいる）。素の生成直後の住人は 1。
#: `fields["config"]` で上書きできる。
DEFAULT_CONFIG = dict(npcs.DEFAULT_CONFIG, level_of_detail=1)

#: 頼み文を組む直前に通る関数（`scripts.llm.llm_manager`）。
#: どれも `character_instance` を引数で受け取るので、相手を名前で拾える
#: （位置では拾わない。並びは版で動く。GAME.md §2.24）。
CONVERSATION_SITES = (
    "conversation_starter",
    "conversation_facilitator",
    "conversation_facilitator_after_retrieval",
    "conversation_starter_in_quest",
    "conversation_facilitator_in_quest",
    "conversation_resolver",
    "conversation_join_message",
    "conversation_join_message_family",
    "conversation_become_family_response",
    "conversation_recruitment_response",
    "conversation_recruitment_negotiator",
)

#: 相手を限定しない層の鍵。`register(owner, "*", notes=...)` で積む。
#: 誰と話していても呼ばれる（`311_` / `317_` / `403_` が全員に足しているもの）。
ANY = "*"

#: `notes` を足す頼み文。`311_` / `317_` / `321_` / `403_` が包んでいた5つで、
#: 要約（`conversation_resolver`）や加入・雇用の頼み文には足さない
#: （足した文が要約に写って記憶に残る）。層ごとに `sites=` で変えられる。
NOTES_SITES = frozenset((
    "conversation_starter",
    "conversation_facilitator",
    "conversation_facilitator_after_retrieval",
    "conversation_starter_in_quest",
    "conversation_facilitator_in_quest",
))

#: 関所を立てる先。
SAVE_TARGET = "__main__:InstantaleApp.save_game"
WORLD_TARGET = "__main__:World.__init__"
CONVERSATION_START_TARGET = "__main__:ConversationStartManager.__init__"
CONVERSATION_END_TARGET = "__main__:ConversationEndManager.resolve_conversation"
DETAIL_TARGET = "__main__:InstantaleApp.ensure_npc_detail_generated"
#: 会話の直前の詳細生成が実際に通る入口。`ConversationStartManager.generate_npc_detail_and_ready`
#: （別スレッド）がここを直に呼ぶ（実機 2026-09-12。`ensure_npc_detail_generated` は通らなかった）。
#: LLM の答えを `save_data_dict['npcs'][id]` へ書くので、素データの写しが無いと `KeyError`。
DETAIL_GEN_TARGET = "__main__:InstantaleApp.generate_npc_detail"
IMAGE_TARGET = "__main__:InstantaleApp.update_character_image"
#: 仲間に加える入口。MOD の NPC はここで断る（`_install` の `add_party_member`）。
PARTY_TARGET = "__main__:InstantaleApp.add_party_member"


# --------------------------------------------------------------------------
# 名乗る
# --------------------------------------------------------------------------
def make_id(owner, key):
    """`mod:<持ち主>:<鍵>`。同じ MOD の中で鍵が違えば別人になる。"""
    return "{}{}:{}".format(PREFIX, owner, key)


def is_mod_npc(npc_id):
    """MOD が持つ NPC の id か。"""
    return isinstance(npc_id, str) and npc_id.startswith(PREFIX)


def owner_of_id(npc_id):
    """`mod:` の id から持ち主の名乗りを取る。MOD の id でなければ None。"""
    if not is_mod_npc(npc_id):
        return None
    rest = npc_id[len(PREFIX):]
    return rest.split(":", 1)[0] if rest else None


def npc_id_of(app, character, world=None):
    """実体から id を引く。引けなければ空文字。

    `world.characters` の鍵から引く。`.id` は保険に下げる
    （`Character` が `id` を持つことは確かめてあるが、全件が鍵と一致するかは
    確かめていない。GAME.md §2.7）。
    `world=` を受けるのは、ロードの途中で `app.world` がまだ差し替わっていない
    場面があるため（`World.__init__` の中）。
    """
    if character is None:
        return ""
    characters = _roster(app, world)
    if isinstance(characters, dict):
        for key, value in characters.items():
            if value is character:
                return str(key)
    value = getattr(character, "id", None)
    return str(value) if value is not None else ""


# --------------------------------------------------------------------------
# 登録簿
# --------------------------------------------------------------------------
def registry():
    """`{npc_id: 記録}`。書き換えてよいのはこのモジュールだけ。"""
    reg = getattr(sys, REGISTRY_ATTR, None)
    if not isinstance(reg, dict):
        reg = {}
        setattr(sys, REGISTRY_ATTR, reg)
    return reg


def _record(npc_id):
    """その id の記録。無ければ作る。"""
    reg = registry()
    record = reg.get(npc_id)
    if not isinstance(record, dict):
        record = reg[npc_id] = {
            "id": npc_id,
            "layers": [],        # 積んだ順。同じ持ち主は1つだけ
            "character": None,   # MOD の NPC の実体
            "snapshot": None,    # 控えから読んだ実体の写し（次の spawn の材料）
            "placed": None,      # (area_id, facility_id, 主の元の値)
        }
    return record


def layers(npc_id):
    """その id に積まれている層。積んだ順。"""
    record = registry().get(npc_id)
    return list(record["layers"]) if isinstance(record, dict) else []


def layers_for(npc_id):
    """その id に効く層。id 指定の層の後に `ANY` の層。どちらも `priority` 順。

    `priority` が同じなら積んだ順。id 指定を先にするのは、
    その相手のために書いたものを、全員向けの定型より先に読ませるため。
    """
    specific = [(layer.get("priority", 0), 0, i, layer)
                for i, layer in enumerate(layers(str(npc_id)))]
    common = [(layer.get("priority", 0), 1, i, layer)
              for i, layer in enumerate(layers(ANY))] if str(npc_id) != ANY else []
    return [layer for _p, _k, _i, layer in sorted(specific + common,
                                                   key=lambda t: t[:3])]


def has_layers(npc_id):
    """その id に効く層が1つでもあるか（`ANY` を含む）。"""
    return bool(layers(str(npc_id))) or bool(layers(ANY))


def entries(owner=None):
    """登録されている id。`owner` を渡すとその MOD の分だけ。"""
    out = []
    for npc_id, record in registry().items():
        if owner is None or any(layer["owner"] == owner
                                for layer in record["layers"]):
            out.append(npc_id)
    return sorted(out)


def register(owner, npc_id=None, *, key=None, fields=None, prompt=None,
             notes=None, sites=None, priority=0, on=None, place=None,
             app=None, write=None):
    """層を1つ積む。id を返す。

    | 引数 | |
    |---|---|
    | `owner` | 登録する MOD の名乗り。フォルダ名を使う（ログに出る名前と揃う） |
    | `npc_id` | 正規 NPC に被せるならその id。MOD の NPC なら省いて `key` を渡す。`ANY`（`"*"`）なら誰にでも効く |
    | `key` | MOD の中で人物を見分ける鍵。id は `mod:<owner>:<key>` になる |
    | `fields` | MOD の NPC を組むときの初期値（セーブの項目名。`npcs.NEW_NPC_TEMPLATE` の33項目）。控えの写しが在ればそちらが勝つ |
    | `prompt` | 頼み文を組む直前に呼ぶ関数。`fn(info)`。引数一式（`info["args"]`）を直に触る口 |
    | `notes` | 相手の素性に足す文章を返す関数。`fn(info) -> str / None`。差し込みの手順は関所が持つ（`compose_notes`） |
    | `sites` | `notes` を足す頼み文の名前の集合。省くと `NOTES_SITES`（会話の5関数） |
    | `priority` | `notes` を繋ぐ順。小さいほど先。同じなら id 指定 → `ANY`、それぞれ積んだ順 |
    | `on` | 場面ごとのフック（`CONVERSATION_SITES` ではなく `fire` の site 名） |
    | `place` | `(エリア id, 施設 id)`。MOD の NPC を置く先 |
    | `app` | いまは使わない（互換のため残す） |

    `notes` は「何を書くか」だけを MOD に残すための口。
    `311_` / `317_` / `321_` / `403_` は「相手を複製して `profile` に足し、引数を
    差し替える」手順を4本とも自前で持っていて（2026-09-12 に確認）、
    外側の層から順に複製の複製ができ、繋ぐ順は `load_order.json` の並びでしか決まらなかった。
    ここに寄せると複製は1つ、順は宣言、ログは1行になる。

    同じ `(owner, npc_id)` の層は差し替える。
    注入し直すたびに `apply()` が走る（TECH.md §3.5）ので、
    ここで重ねると世代のぶんだけ層が積み上がる。
    """
    if npc_id is None:
        if key is None:
            raise ValueError("register needs either npc_id or key")
        npc_id = make_id(owner, key)
    npc_id = str(npc_id)
    layer = {"owner": owner, "fields": dict(fields or {}),
             "prompt": prompt, "notes": notes,
             "sites": frozenset(sites) if sites is not None else NOTES_SITES,
             "priority": int(priority), "on": dict(on or {}), "place": place}
    record = _record(npc_id)
    replaced = any(old["owner"] == owner for old in record["layers"])
    record["layers"] = [old for old in record["layers"]
                        if old["owner"] != owner]
    record["layers"].append(layer)
    if replaced and record.get("character") is not None:
        # 同じ持ち主が登録し直した＝その MOD の `apply()` が走り直した。
        # 登録簿は注入をまたいで生きるので、前の版で組んだ実体が残っている。
        # 次の `spawn` で今の `fields` と今の `build` から組み直す
        # （実機 2026-09-12。`build` を直しても古い実体が使い回されて同じ場所で落ちた）。
        record["character"] = None
    if write:
        write("modnpc: {} registered {} ({} layer(s))".format(
            owner, npc_id, len(record["layers"])))
    return npc_id


def unregister(owner, npc_id=None, app=None, write=None):
    """層を外す。外した id を返す。

    `npc_id` を省くとその MOD の層を全部外す。
    層が1つも残らなくなった id は、`app` を渡してあれば素の状態へ戻してから
    記録ごと捨てる（MOD の NPC は世界から降り、被せは素の値に戻る）。
    """
    dropped = []
    for target in ([str(npc_id)] if npc_id is not None else entries(owner)):
        record = registry().get(target)
        if not isinstance(record, dict):
            continue
        record["layers"] = [layer for layer in record["layers"]
                            if layer["owner"] != owner]
        dropped.append(target)
        if not record["layers"] and app is not None:
            try:
                despawn(app, target, write=write)
            except Exception:
                log_exc("modnpc: cannot take {} off the world".format(target))
        if app is not None:
            _drop_persisted(app, owner, target)   # `despawn` の後（先に消すと spawned=False が書き戻る）
        if record["layers"]:
            continue
        registry().pop(target, None)
    if write and dropped:
        write("modnpc: {} unregistered {}".format(owner, ", ".join(dropped)))
    return dropped


def purge(app=None, write=None):
    """登録簿を空にする。MOD の NPC は世界からも降ろす。

    ローダの `unload` はパッチを剥がすだけで、世界に書いた値は戻さない
    （TECH.md §3.10）。
    片付けたい MOD はこれを自分で呼ぶ。
    """
    for npc_id in list(registry()):
        if app is not None:
            try:
                despawn(app, npc_id, write=write)
            except Exception:
                log_exc("modnpc: cannot take {} off the world".format(npc_id))
    registry().clear()
    if write:
        write("modnpc: the registry is empty")


# --------------------------------------------------------------------------
# 組む
# --------------------------------------------------------------------------
def _character_class():
    """`scripts.characters.Character`。引けなければ None。"""
    module = sys.modules.get("scripts.characters")
    return getattr(module, "Character", None) if module is not None else None


def fields_of(npc_id):
    """積まれた層の `fields` を1つに重ねる。後から積んだ層が勝つ。"""
    merged = {}
    for layer in layers(npc_id):
        merged.update(layer["fields"])
    return merged


def plain_data(fields, npc_id=None):
    """組む人物の素データ（`npcs.NEW_NPC_TEMPLATE` の33項目、セーブと同じ形と並び）。

    `build` の材料であり、`spawn` が `save_data_dict['npcs']` / `world_dict['npcs']` に
    **同じ辞書**として置く（`install_plain`）。ゲームの詳細生成はそこへ書く。
    保存の間は反復から隠す（`hide`）ので、ディスクには出ない。
    """
    data = copy.deepcopy(npcs.NEW_NPC_TEMPLATE)
    data["relationship"] = copy.deepcopy(DEFAULT_RELATIONSHIP)
    data.update({key: value for key, value in dict(fields or {}).items()
                 if key != "config"})
    if npc_id is not None:
        data["id"] = str(npc_id)
    data["config"] = dict(DEFAULT_CONFIG,
                          **dict((fields or {}).get("config") or {}))
    return data


def build(fields, npc_id=None, write=None, data=None):
    """`Character` を直に組む。組めなければ None。`data=` に `plain_data` の戻りを渡せる。

    `World.generate_character` は通さない。
    あちらは `save_data_dict['npcs']` を id で引く側なので、
    素データを書いていない id は `KeyError` になる（GAME.md §2.23）。
    ここは素データを一切書かないことが目的なので、引かれる前に自分で組む。

    ひな型は `npcs.NEW_NPC_TEMPLATE` をそのまま使う。
    セーブに出さないので並び順は要らないが、控えに落とした中身を
    セーブと見比べられる形にしておく（`914_` の保管庫と同じ考え）。
    深い複製にするのは、入れ子（`ability_scores` / `memory` / `image_src`）が
    作った NPC 全員で同じ辞書になるのを避けるため。
    """
    cls = _character_class()
    if cls is None:
        if write:
            write("WARN modnpc: scripts.characters.Character is not available")
        return None
    if data is None:
        data = plain_data(fields, npc_id)
    # 実体にも id を持たせる（`plain_data` が入れる）。
    # 頼み文の関数は `character_instance` しか渡してこないので、
    # 名簿を走査できない場面（`app` が引けないとき）はここが唯一の手掛かりになる。
    # ひな型の項目のうち `Character.__init__` が受けるものは**全部**渡す。
    # `npcs.CHARACTER_KWARGS` の15個だけだと `life_log` / `current_log` /
    # `memory` / `knowledges` が既定の None のままになり、会話の第一声を組む
    # `context_manager.get_life_log_text` が `'NoneType' object is not iterable`
    # で落ちる（実機 2026-09-12）。素データ経由なら `[]` / `{}` が渡る項目。
    kwargs = {}
    accepts = _accepted_kwargs(cls)
    for field, value in data.items():
        name = FIELD_TO_ATTR.get(field, field)
        if accepts is None or name in accepts:
            kwargs[name] = value
    # `original_ability_scores` は添字で読まれるので、6つの鍵が要る。
    # 値は None でよい（実機で窓が開かなかった原因。GAME.md §2.23）。
    scores = data.get("ability_scores")
    kwargs["original_ability_scores"] = (
        scores if isinstance(scores, dict) and scores
        else dict(npcs.NEW_NPC_TEMPLATE["ability_scores"]))
    try:
        return cls(**kwargs)
    except Exception as exc:
        if write:
            write("WARN modnpc: Character(**{}) failed: {}: {}".format(
                sorted(kwargs), type(exc).__name__, exc))
        return None


def _accepted_kwargs(cls):
    """`cls.__init__` が名前で受ける引数。`**kwargs` を持つか読めなければ None（全部渡す）。"""
    try:
        params = inspect.signature(cls).parameters.values()
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return None
    return {p.name for p in params}


# --------------------------------------------------------------------------
# 載る
# --------------------------------------------------------------------------
def _roster(app, world=None):
    """実行時の名簿（`world.characters`）。読めなければ None。

    保存の間は `_RosterView` が成り代わっている。dict の派生なのでそのまま返す
    （書き込みは元へ通る）。"""
    target = world if world is not None else getattr(app, "world", None)
    characters = getattr(target, "characters", None)
    return characters if isinstance(characters, dict) else None


def spawn(app, npc_id, *, world=None, write=None):
    """MOD の NPC を組んで名簿に載せる。実体を返す。

    既に居れば組み直さない（ロードのたびに呼んでよい）。
    層の `place` があれば施設にも置く。
    """
    npc_id = str(npc_id)
    if not is_mod_npc(npc_id):
        if write:
            write("WARN modnpc: {} is not a mod npc; spawn refused".format(npc_id))
        return None
    if not gate_is_live():
        # 関所の無い世界に載せると、次の保存が名簿を舐めて落ちる（`gate_is_live`）。
        log("modnpc: the save gate is not up in this generation; {} not spawned"
            .format(npc_id), level="WARN")
        if write:
            write("WARN modnpc: the save gate is not up; {} not spawned".format(npc_id))
        return None
    record = _record(npc_id)
    character = record.get("character")
    if character is None:
        # 材料は 宣言時の初期値（fields）の上に控えの写し（snapshot）。
        # 写しはゲームの保存と同じ時点の実体なので、記憶も HP も死もそこから戻る。
        fields = dict(fields_of(npc_id))
        snapshot = record.get("snapshot")
        if isinstance(snapshot, dict):
            fields.update(copy.deepcopy(snapshot))
        data = plain_data(fields, npc_id)
        character = build(fields, npc_id=npc_id, write=write, data=data)
        if character is None:
            return None
        record["data"] = data
        if str(getattr(character, "id", "")) != npc_id:
            try:
                character.id = npc_id
            except Exception:
                log_exc("modnpc: cannot put the id on {}".format(npc_id))
        record["character"] = character
    characters = _roster(app, world)
    if characters is not None:
        characters[npc_id] = character
    elif write:
        write("WARN modnpc: the world has no roster; {} is not listed".format(npc_id))
    install_plain(app, npc_id, write=write)
    for layer in layers(npc_id):
        if layer.get("place"):
            area_id, facility_id = layer["place"]
            place(app, npc_id, area_id, facility_id, write=write)
            break
    if write:
        write("modnpc: {} {!r} is in the world".format(
            npc_id, frames.short(getattr(character, "name", None), 20)))
    _persist(app, owner_of_id(npc_id), npc_id, spawned=True)
    return character


def despawn(app, npc_id, *, world=None, write=None):
    """世界から降ろす。実体は記録に残したまま名簿と施設から外す。

    **名簿から外すのは MOD の NPC だけ。**
    正規 NPC に対してはこちらが施設へ足した分を外すだけで、本物を世界から消すことはしない
    （ここを分けないと、層を外す呼びがゲームの住人を1人消す）。
    """
    npc_id = str(npc_id)
    record = registry().get(npc_id)
    if not isinstance(record, dict):
        return False
    unplace(app, npc_id, write=write)
    if not is_mod_npc(npc_id):
        return False
    remove_plain(app, npc_id)
    _persist(app, owner_of_id(npc_id), npc_id, spawned=False)
    characters = _roster(app, world)
    if characters is not None and npc_id in characters:
        characters.pop(npc_id, None)
        if write:
            write("modnpc: {} is off the world".format(npc_id))
        return True
    return False


def forget(write=None):
    """実体だけ捨てる（層は残す）。世界が変わったときに呼ぶ。

    別の世界を読めば施設もエリアも別物なので、前の世界で組んだ実体は
    どこにも繋がらない。
    層は MOD のものなので消さない ―
    建て直すかどうかは `on["world"]` を受けた MOD が決める。
    """
    for record in registry().values():
        record["character"] = None
        record["data"] = None
        record["snapshot"] = None
        record["placed"] = None
    if write:
        write("modnpc: the world changed; every instance was dropped")


# --------------------------------------------------------------------------
# 素データの写し
# --------------------------------------------------------------------------
#: 素データの辞書を持っている入れ物と、その中の鍵。
#: `app.world_dict['npcs']` と `app.save_data_dict['npcs']` は別の辞書（GAME.md §2.28）。
#: `World` も `save_data_dict` を握っている（`World.__init__` の引数）。
PLAIN_HOLDERS = (("save_data_dict", "npcs"), ("world_dict", "npcs"))


def _plain_containers(app):
    """`[(入れ物の辞書, 鍵), ...]`。値が辞書のものだけ。"""
    out = []
    holders = [getattr(app, name, None) for name, _key in PLAIN_HOLDERS]
    holders.append(getattr(getattr(app, "world", None), "save_data_dict", None))
    seen = set()
    for holder, (_name, key) in zip(holders, list(PLAIN_HOLDERS) + [("world", "npcs")]):
        if not isinstance(holder, dict) or id(holder) in seen:
            continue
        seen.add(id(holder))
        if isinstance(holder.get(key), dict):
            out.append((holder, key))
    return out


def _plain_source(store):
    return store.source if isinstance(store, _RosterView) else store


def install_plain(app, npc_id, write=None):
    """素データの写しを素データの辞書すべてに置く。置いた数を返す。

    ゲームの詳細生成（`generate_npc_detail`）は結果を `save_data_dict['npcs'][id]` へ書く
    （実機 2026-09-12: 無いと LLM の答えが返った直後に `KeyError` で会話のスレッドが死ぬ）。
    置くのは `plain_data` の**同じ辞書**なので、どこに書かれても1つに集まる。
    保存の間は `hide` が反復から隠す。
    """
    npc_id = str(npc_id)
    record = registry().get(npc_id)
    data = record.get("data") if isinstance(record, dict) else None
    if not isinstance(data, dict):
        return 0
    placed = 0
    for holder, key in _plain_containers(app):
        store = _plain_source(holder[key])
        if store.get(npc_id) is not data:
            store[npc_id] = data
        placed += 1
    if write and placed:
        write("modnpc: the plain data of {} is in {} store(s)".format(npc_id, placed))
    return placed


def remove_plain(app, npc_id):
    """素データの写しを全部外す。"""
    npc_id = str(npc_id)
    for holder, key in _plain_containers(app):
        _plain_source(holder[key]).pop(npc_id, None)


def _character_at(app, npc_id, world=None):
    """その id の実体。MOD の NPC は記録から、正規 NPC は名簿から。"""
    record = registry().get(npc_id)
    if isinstance(record, dict) and record.get("character") is not None:
        return record["character"]
    characters = _roster(app, world)
    if isinstance(characters, dict):
        return characters.get(str(npc_id))
    return None


# --------------------------------------------------------------------------
# 置く
# --------------------------------------------------------------------------
def place(app, npc_id, area_id, facility_id, *, owner=False, write=None):
    """施設の名簿に載せる。載ったら True。

    `move_npc_to_facility` は通さない。
    あちらは素データ側にも登録する引数（`register_facility`）を持っていて、
    何を書くのかを確かめていない。
    ここで触るのは実行時の `Facility` だけにする
    （`Facility.characters` は重複を持つことがあるので、入れる前に見る。
    GAME.md §2.7）。

    `owner=True` は施設の主にも据える。
    元の主は記録に控えて `unplace` で戻す（`914_` の滞在中の差し替えと同じ形）。
    """
    npc_id = str(npc_id)
    area = ui.world_areas(app).get(str(area_id))
    facility, node = None, None
    if area is not None:
        try:
            facility, node = ui.find_facility(area, str(facility_id))
        except Exception:
            facility, node = None, None
    if facility is None:
        if write:
            write("WARN modnpc: facility {}/{} not found; {} was not placed"
                  .format(area_id, facility_id, npc_id))
        return False
    roster = getattr(facility, "characters", None)
    if isinstance(roster, list) and npc_id not in roster:
        roster.append(npc_id)
    # 「会話する」の一覧は `world.characters` を舐めて各人物の `.location` を
    # 今の施設と突き合わせる（実機 2026-09-12。名簿に居ても `.location` が
    # 施設オブジェクトでなければ出ない）。実行時の NPC と同じ形で持たせる。
    # 保存には出ない（`_RosterView` が反復から隠す）ので、オブジェクトを持っても焼かれない。
    was_at = None
    record = _record(npc_id)
    character = record.get("character")
    if character is not None:
        was_at = {name: getattr(character, name, None)
                  for name in ("location", "current_node", "current_area")}
        for name, value in (("location", facility), ("current_node", node),
                            ("current_area", area)):
            try:
                setattr(character, name, value)
            except Exception:
                log_exc("modnpc: cannot set {}.{}".format(npc_id, name))
    owner_was = None
    if owner:
        owner_was = getattr(facility, "owner", None)
        try:
            facility.owner = npc_id
        except Exception as exc:
            if write:
                write("WARN modnpc: cannot set the owner of {}/{}: {}: {}"
                      .format(area_id, facility_id, type(exc).__name__, exc))
            owner_was = None
    record["placed"] = (str(area_id), str(facility_id), owner_was, bool(owner))
    record["was_at"] = was_at
    if is_mod_npc(npc_id):
        _persist(app, owner_of_id(npc_id), npc_id,
                 place=[str(area_id), str(facility_id), bool(owner)])
    if write:
        write("modnpc: {} is at {}/{}{}".format(
            npc_id, area_id, facility_id, " as the owner" if owner else ""))
    return True


def unplace(app, npc_id, write=None):
    """施設の名簿から外し、主を元へ戻す。"""
    npc_id = str(npc_id)
    record = registry().get(npc_id)
    if not isinstance(record, dict) or not record.get("placed"):
        return False
    area_id, facility_id, owner_was, was_owner = record["placed"]
    facility = facility_of(app, area_id, facility_id)
    if facility is None:
        record["placed"] = None
        return False
    roster = getattr(facility, "characters", None)
    if isinstance(roster, list):
        roster[:] = [key for key in roster if str(key) != npc_id]
    character = record.get("character")
    if character is not None and isinstance(record.get("was_at"), dict):
        for name, value in record["was_at"].items():
            try:
                setattr(character, name, value)
            except Exception:
                log_exc("modnpc: cannot restore {}.{}".format(npc_id, name))
        record["was_at"] = None
    if was_owner and str(getattr(facility, "owner", None)) == npc_id:
        try:
            facility.owner = owner_was
        except Exception:
            log_exc("modnpc: cannot restore the owner of {}/{}".format(
                area_id, facility_id))
    record["placed"] = None
    if is_mod_npc(npc_id):
        _persist(app, owner_of_id(npc_id), npc_id, place=None)
    if write:
        write("modnpc: {} left {}/{}".format(npc_id, area_id, facility_id))
    return True


def facility_of(app, area_id, facility_id):
    """エリア id と施設 id から実行時の `Facility`。引けなければ None。"""
    area = ui.world_areas(app).get(str(area_id))
    if area is None:
        return None
    try:
        facility, _node = ui.find_facility(area, str(facility_id))
    except Exception:
        return None
    return facility


# --------------------------------------------------------------------------
# 控え（state\modnpc\<世界>.json）
# --------------------------------------------------------------------------
#: 控えの置き場所（`sys` の属性。注入をまたぐ）とフォルダ名。
STORE_ATTR = "_instantale_modnpc_store"
STATE_DIRNAME = "modnpc"
#: 建て直しの間だけ立つ「いまの世界の鍵」。`World.__init__` の中では `app.world_dict` が
#: まだ前の世界を指していることがあるので、鍵を引数の `save_data_dict` から決めて持ち回る。
_KEY_OVERRIDE_ATTR = "_instantale_modnpc_key_override"


def bind_store(ctx, write=None):
    """控えを今の世代の `ctx` に繋ぐ（`install` が毎回呼ぶ）。"""
    found = getattr(sys, STORE_ATTR, None)
    if isinstance(found, state.WorldStore):
        return found.rebind(ctx, write)
    found = state.WorldStore(ctx, STATE_DIRNAME, write=write)
    setattr(sys, STORE_ATTR, found)
    return found


def store():
    """控え。`install` がまだなら None（控えずに動く）。"""
    found = getattr(sys, STORE_ATTR, None)
    return found if isinstance(found, state.WorldStore) else None


def _current_key(app):
    override = getattr(sys, _KEY_OVERRIDE_ATTR, None)
    if isinstance(override, str) and override:
        return override
    return state.world_key(app) if app is not None else state.UNKNOWN_WORLD


def _bucket(app):
    """`(世界の鍵, 控え)`。控えが無いか世界が分からなければ `(None, None)`。"""
    found = store()
    if found is None or app is None:
        return None, None
    key = _current_key(app)
    if not key or key == state.UNKNOWN_WORLD:
        return None, None
    return key, found.load(key)


def _jsonable(value):
    """控えに入れてよい値か。JSON に落ちるものだけ（実行時のオブジェクトは控えない）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _jsonable(v) for k, v in value.items())
    return False


def _persist(app, owner, npc_id, **changes):
    """1人ぶんの控えを書く。`snapshot=`（実体の写し）／`spawned=`／`place=`。書けたら True。

    控えの形は `{持ち主: {id: {"snapshot": {...}, "spawned": bool, "place": [area, facility, 主か]}}}`。
    `snapshot` は JSON に落ちる項目だけ（実行時のオブジェクトは落とさない）。
    """
    if not owner:
        return False
    key, bucket = _bucket(app)
    if key is None:
        return False
    entry = bucket.setdefault(owner, {}).setdefault(
        str(npc_id), {"snapshot": None, "spawned": False, "place": None})
    if "snapshot" in changes:
        entry["snapshot"] = copy.deepcopy(changes["snapshot"])
    if "spawned" in changes:
        entry["spawned"] = bool(changes["spawned"])
    if "place" in changes:
        entry["place"] = list(changes["place"]) if changes["place"] else None
    return store().save(key, bucket)


def _drop_persisted(app, owner, npc_id):
    key, bucket = _bucket(app)
    if key is None:
        return False
    owned = bucket.get(owner)
    if not isinstance(owned, dict) or str(npc_id) not in owned:
        return False
    owned.pop(str(npc_id), None)
    if not owned:
        bucket.pop(owner, None)
    return store().save(key, bucket)


def _persisted_entry(app, owner, npc_id):
    key, bucket = _bucket(app)
    if key is None:
        return None
    owned = bucket.get(owner)
    entry = owned.get(str(npc_id)) if isinstance(owned, dict) else None
    return entry if isinstance(entry, dict) else None


def _layer_of(owner, npc_id):
    for layer in layers(str(npc_id)):
        if layer["owner"] == owner:
            return layer
    return None


#: 写しに入れない項目。実行時のオブジェクトで、置き場所は `place` の控えが持つ。
SNAPSHOT_SKIP = frozenset(("location", "current_area", "current_location"))


def snapshot_of(character):
    """実体の33項目のうち JSON に落ちるものを写す（セーブの項目名で）。"""
    out = {}
    for field in npcs.NEW_NPC_TEMPLATE:
        if field in SNAPSHOT_SKIP:
            continue
        attr = FIELD_TO_ATTR.get(field, field)
        if not hasattr(character, attr):
            continue
        value = getattr(character, attr)
        if _jsonable(value):
            out[field] = copy.deepcopy(value)
    return out


def snapshot_all(app, write=None):
    """世界に居る MOD の NPC 全員の実体を控えに写す。ゲームの保存と同じ時点で呼ぶ。

    正規 NPC はゲームがセーブに書く。MOD の NPC はそこに出さないので、
    同じ時点で同じ中身をこちらが控える（会話の記憶・HP・詳細生成の結果・`is_dead`）。
    """
    done = []
    for npc_id, record in list(registry().items()):
        if not is_mod_npc(npc_id) or record.get("character") is None:
            continue
        if not (record.get("placed") or npc_id in (_roster(app) or {})):
            continue
        snap = snapshot_of(record["character"])
        if _persist(app, owner_of_id(npc_id), npc_id, snapshot=snap):
            done.append(npc_id)
    if write and done:
        write("modnpc: snapshot of {} taken".format(", ".join(done)))
    return done


def restore_world(app, world=None, save_data_dict=None, write=None):
    """世界を読んだ直後、控えから建て直す。

    控えは持ち主ごとなので、**その持ち主の層が今の世代に登録されているものだけ**戻す
    （MOD を外していれば何も戻らない）。
    `spawned` なら写し（`snapshot`）を材料に組み直して置く。
    """
    found = store()
    if found is None:
        return []
    key = state.world_key_of_dict(save_data_dict, None) if save_data_dict is not None else None
    if not key:
        key = state.world_key(app)
    if not key or key == state.UNKNOWN_WORLD:
        return []
    setattr(sys, _KEY_OVERRIDE_ATTR, key)
    done = []
    try:
        bucket = found.load(key, fresh=True)
        for owner, owned in list((bucket or {}).items()):
            if not isinstance(owned, dict):
                continue
            for npc_id, entry in list(owned.items()):
                layer = _layer_of(owner, npc_id)
                if layer is None or not isinstance(entry, dict) or not is_mod_npc(npc_id):
                    continue
                record = _record(npc_id)
                snap = entry.get("snapshot")
                record["snapshot"] = copy.deepcopy(snap) if isinstance(snap, dict) else None
                if entry.get("spawned"):
                    spawn(app, npc_id, world=world, write=write)
                    spot = entry.get("place")
                    if isinstance(spot, (list, tuple)) and len(spot) >= 2:
                        place(app, npc_id, spot[0], spot[1],
                              owner=bool(spot[2]) if len(spot) > 2 else False,
                              write=write)
                done.append((owner, npc_id))
    finally:
        try:
            delattr(sys, _KEY_OVERRIDE_ATTR)
        except AttributeError:
            pass
    if write and done:
        write("modnpc: restored {} entr(y/ies) from the state of {!r}".format(
            len(done), key))
    return done


# --------------------------------------------------------------------------
# 取っ手
# --------------------------------------------------------------------------
#: `config` の中で個別に読み書きしたい鍵。
CONFIG_KEYS = ("is_dead", "is_player", "level_of_detail", "difficulty_level")


class Npc(object):
    """1人ぶんの取っ手。`modnpc.get(app, id)` で得る。

        npc = modnpc.get(app, "35")            # 正規 NPC
        npc = modnpc.get(app, "mod:x:clerk")   # MOD の NPC
        npc.is_dead                            # config['is_dead']
        npc.profile = npc.profile + "\n…"     # 実体へそのまま書く
        npc.physical_integrity                 # ローダが知らない属性も実体へそのまま素通し

    **隠さない。** セーブの33項目は同名のプロパティで実体を読み書きし
    （`ability_scores` ↔ `original_ability_scores`、`knowledge` ↔ `knowledges` の名前の違いだけ
    吸収する）、それ以外の名前も実体へそのまま通す。
    真実は実体1つで、ローダは変換も戻しもしない:

    - MOD の NPC: 書いた値はそのまま生き、保存のたびに控えへ写され（`snapshot_all`）、
      読み直しで戻る。写しの辞書（`data`）にも同じ値を入れる（ゲームがそこを読む）
    - 正規 NPC: 書いた値は**本物の変更**で、ゲームがセーブに書く。MOD を外しても残るので、
      戻すのは書いた MOD の責任（`npcs.make_npc` と同じ性質。頼み文だけに効かせたいなら `notes`）
    - HP は `current_hp` / `max_hp` / `original_max_hp` の3つ組（GAME.md §2.22）。1つだけ動かすと
      本体の不変条件を破る
    """

    __slots__ = ("app", "npc_id", "world")

    def __init__(self, app, npc_id, world=None):
        object.__setattr__(self, "app", app)
        object.__setattr__(self, "npc_id", str(npc_id))
        object.__setattr__(self, "world", world)

    # -- 生のもの -----------------------------------------------------------
    @property
    def is_mod(self):
        return is_mod_npc(self.npc_id)

    @property
    def record(self):
        """登録簿の記録。層が無ければ None。"""
        return registry().get(self.npc_id)

    @property
    def layers(self):
        return layers_for(self.npc_id)

    @property
    def character(self):
        """実体。世界に居なければ None（MOD の NPC は降ろした後も記録に残る）。"""
        return _character_at(self.app, self.npc_id, self.world)

    @property
    def exists(self):
        """実体があるか。"""
        return self.character is not None

    @property
    def in_world(self):
        """名簿（`world.characters`）に載っているか。ゲームから見えるかはこちら。"""
        characters = _roster(self.app, self.world)
        return isinstance(characters, dict) and self.npc_id in characters

    @property
    def data(self):
        """MOD の NPC の素データの写し（`spawn` が置いたもの）。正規 NPC では None（本物はここから触らない）。"""
        record = self.record
        if isinstance(record, dict) and isinstance(record.get("data"), dict):
            return record["data"]
        return None

    # -- 書く ----------------------------------------------------------------------
    def _write(self, attr, value, field=None):
        character = _character_at(self.app, self.npc_id, self.world)
        if character is None:
            raise AttributeError("{} is not in the world; cannot set {!r}".format(
                self.npc_id, attr))
        setattr(character, attr, value)
        data = self.data
        if field is not None and isinstance(data, dict) and field in data:
            data[field] = value                 # 写しも同じ値に（ゲームがそこを読む）

    def _config_get(self, key):
        character = self.character
        config = getattr(character, "config", None) if character is not None else None
        return config.get(key) if isinstance(config, dict) else None

    def _config_set(self, key, value):
        character = self.character
        config = getattr(character, "config", None) if character is not None else None
        if not isinstance(config, dict):
            raise AttributeError("{} has no config".format(self.npc_id))
        config[key] = value                     # 写しとは同じ辞書

    def set(self, field, value):
        """`npc.profile = …` と同じ（名前が変数に入っているとき用）。"""
        setattr(self, field, value)

    # -- 実体の属性 -------------------------------------------------------------
    def __getattr__(self, name):
        # プロパティと slot 以外はここへ来る。実体へ素通し。
        character = _character_at(self.app, self.npc_id, self.world)
        if character is None:
            raise AttributeError("{} is not in the world; cannot read {!r}".format(
                self.npc_id, name))
        return getattr(character, name)

    def __setattr__(self, name, value):
        if name in Npc.__slots__ or isinstance(getattr(type(self), name, None), property):
            object.__setattr__(self, name, value)
            return
        self._write(name, value)

    def __repr__(self):
        return "Npc({!r}, {})".format(self.npc_id, "in world" if self.exists else "absent")

    # -- 既存の関数の同名メソッド ---------------------------------------------
    def register(self, owner, **kwargs):
        return register(owner, self.npc_id, **kwargs)

    def unregister(self, owner, write=None):
        return unregister(owner, self.npc_id, app=self.app, write=write)

    def spawn(self, write=None):
        return spawn(self.app, self.npc_id, world=self.world, write=write)

    def despawn(self, write=None):
        return despawn(self.app, self.npc_id, world=self.world, write=write)

    def place(self, area_id, facility_id, owner=False, write=None):
        return place(self.app, self.npc_id, area_id, facility_id, owner=owner, write=write)

    def unplace(self, write=None):
        return unplace(self.app, self.npc_id, write=write)

    def snapshot(self):
        """いまの実体の写し（`snapshot_of`）。控えには書かない。"""
        character = self.character
        return snapshot_of(character) if character is not None else None


def _field_property(field):
    attr = FIELD_TO_ATTR.get(field, field)

    def getter(self):
        character = self.character
        if character is not None and hasattr(character, attr):
            return getattr(character, attr)
        data = self.data
        return data.get(field) if isinstance(data, dict) else None

    def setter(self, value):
        self._write(attr, value, field=field)

    getter.__doc__ = "セーブの項目 `{}`（実体では `{}`）。".format(field, attr)
    return property(getter, setter)


def _config_property(key):
    return property(lambda self: self._config_get(key),
                    lambda self, value: self._config_set(key, value))


for _field in npcs.NEW_NPC_TEMPLATE:
    setattr(Npc, _field, _field_property(_field))
for _key in CONFIG_KEYS:
    setattr(Npc, _key, _config_property(_key))
del _field, _key


def get(app, npc_id, world=None):
    """id から取っ手を返す。登録簿にも名簿にも居なければ None。"""
    npc_id = str(npc_id)
    handle = Npc(app, npc_id, world)
    if handle.record is not None or handle.character is not None:
        return handle
    return None


# --------------------------------------------------------------------------
# 呼ぶ
# --------------------------------------------------------------------------
def fire(site, app, npc_id, *, character=None, args=None, world=None,
         write=None):
    """その id に積まれた層のフックを順に呼ぶ。`[(持ち主, 戻り値), ...]`。

    フックの例外は飲む（1本の MOD の失敗で関所を止めない）。
    飲んだことはローダのログに残す ― `safe=True` と同じ考えで、
    見えなくするためではない。
    """
    results = []
    for layer in layers_for(npc_id):
        fn = (layer.get("on") or {}).get(site)
        if not callable(fn):
            continue
        info = {"site": site, "app": app, "npc_id": str(npc_id),
                "character": character, "args": args, "world": world,
                "owner": layer["owner"]}
        try:
            results.append((layer["owner"], fn(info)))
        except Exception:
            log_exc("modnpc: {} hook of {} on {} failed".format(
                site, layer["owner"], npc_id))
            if write:
                write("WARN modnpc: {} hook of {} on {} failed".format(
                    site, layer["owner"], npc_id))
    return results


def fire_all(site, app, *, args=None, world=None, write=None):
    """登録されている全員に同じ場面を通知する。`on["world"]` 用。"""
    results = []
    for npc_id in list(registry()):
        results.extend(fire(site, app, npc_id, args=args, world=world,
                            write=write))
    return results


# --------------------------------------------------------------------------
# 関所
# --------------------------------------------------------------------------
#: セーブに焼かれる、NPC の id を載せた器（`app` の属性。`game_variables` はここから組まれる）。
#: **名簿と素データを隠すだけでは足りない。**
#: 実セーブの `game_variables.buttons_backup` に
#: `{"spec": {"cls_name": "ConversationStartManager", "args": ["35"]}}` が在った（2026-09-13 に確認）。
#: 「会話する」の一覧を出したまま保存すると、そこへ `mod:` の id が焼かれ、
#: MOD を外した後にその選択肢を押すと `ConversationStartManager(app, "mod:…")` が `KeyError` で落ちる。
SAVED_CHOICE_ATTRS = ("buttons", "buttons_backup", "buttons_backup_for_shopping")
#: 選択肢と同じ形の `PhaseSpec` を1つだけ持つ器（自由入力が次に呼ぶもの）。
SAVED_SPEC_ATTRS = ("function_correspond_to_input", "input_backup",
                    "input_backup_for_shopping")
#: 戦闘中の敵（`{id: 情報}`）。MOD の NPC と戦っている最中の保存で焼かれうる。
SAVED_ENEMY_ATTRS = ("current_enemy_dict",)
#: パーティの id が並ぶ器。`ui.party_stores` は「いまのパーティ」だけを見るが、
#: セーブには `original_party`（クエスト前の編成）も焼かれる。
#:
#: **加入そのものは関所が断る**（`PARTY_TARGET`）。ここを掃除するのは保険で、
#: `add_party_member` を通らない経路で入っていたときに漏らさないため。
#: 掃除は「セーブに残さない」を守るが、仲間だったことは戻らない（断るほうが本筋）。
SAVED_PARTY_ATTRS = ("party", "original_party")


def _spec_mentions(value, mod_ids):
    """`PhaseSpec`（またはその辞書）の `args` が MOD の NPC を指しているか。

    実行時は `PhaseSpec` のオブジェクト、セーブから復元された直後は辞書のことがある。
    どちらでも読む（`ui.spec_data` はオブジェクトだけを見る）。
    """
    data = value if isinstance(value, dict) and "cls_name" in value else ui.spec_data(value)
    args = data.get("args") if isinstance(data, dict) else None
    if not isinstance(args, (list, tuple)):
        return False
    return any(str(arg) in mod_ids for arg in args)


def _party_lists(app):
    """パーティの id が並ぶリスト。`app` の属性と `game_variables` の同名の両方。"""
    seen, out = set(), []

    def add(label, value):
        if isinstance(value, list) and id(value) not in seen:
            seen.add(id(value))
            out.append((label, value))

    for attr in SAVED_PARTY_ATTRS:
        add("app." + attr, getattr(app, attr, None))
    variables = getattr(app, "game_variables", None)
    if isinstance(variables, dict):
        for attr in SAVED_PARTY_ATTRS:
            add("game_variables[{!r}]".format(attr), variables.get(attr))
    return out


def _entry_mentions(entry, mod_ids):
    """ボタン1つ（`{"text":…, "spec":…}`）が MOD の NPC を指しているか。"""
    if not isinstance(entry, dict):
        return False
    return _spec_mentions(entry.get("spec"), mod_ids)


def scrub_saved_refs(app, mod_ids, write=None):
    """保存に焼かれる器から MOD の NPC への参照を落とす。戻すための控えを返す。

    落とすのは保存の窓の間だけで、`unscrub_saved_refs` が元の器をそのまま戻す
    （中身を組み直さない ― ゲームが同じ器を握っているので、差し替えた側を戻す）。
    """
    mod_ids = set(str(npc_id) for npc_id in mod_ids)
    if not mod_ids:
        return []
    undo = []
    for attr in SAVED_CHOICE_ATTRS:
        value = getattr(app, attr, None)
        if not isinstance(value, list):
            continue
        kept = [entry for entry in value if not _entry_mentions(entry, mod_ids)]
        if len(kept) == len(value):
            continue
        try:
            setattr(app, attr, kept)
        except Exception:
            log_exc("modnpc: cannot scrub {}".format(attr))
            continue
        undo.append(("attr", attr, value))
        if write:
            write("modnpc: save: {} choice(s) pointing at a mod npc lifted from {}"
                  .format(len(value) - len(kept), attr))
    for attr in SAVED_SPEC_ATTRS:
        value = getattr(app, attr, None)
        if value is None or not _spec_mentions(value, mod_ids):
            continue
        try:
            setattr(app, attr, None)
        except Exception:
            log_exc("modnpc: cannot scrub {}".format(attr))
            continue
        undo.append(("attr", attr, value))
        if write:
            write("modnpc: save: {} pointed at a mod npc; lifted".format(attr))
    for label, store in _party_lists(app):
        kept = [member for member in store
                if str(ui.element_id(member)) not in mod_ids]
        if len(kept) == len(store):
            continue
        undo.append(("list", store, list(store)))
        store[:] = kept
        if write:
            write("modnpc: save: a mod npc lifted from {}".format(label))
    for attr in SAVED_ENEMY_ATTRS:
        value = getattr(app, attr, None)
        if not isinstance(value, dict):
            continue
        present = [key for key in value if str(key) in mod_ids]
        if not present:
            continue
        undo.append(("dict", value, dict(value)))
        for key in present:
            value.pop(key, None)
        if write:
            write("modnpc: save: {} mod npc(s) lifted from {}".format(
                len(present), attr))
    return undo


def unscrub_saved_refs(app, undo):
    """`scrub_saved_refs` が落としたものを戻す。"""
    for kind, target, old in reversed(undo or []):
        try:
            if kind == "attr":
                setattr(app, target, old)
            elif kind == "list":
                target[:] = old
            elif kind == "dict":
                target.clear()
                target.update(old)
        except Exception:
            log_exc("modnpc: cannot put {} back after the save".format(target))


def hide(app, *, world=None, write=None):
    """保存の直前。MOD の NPC を名簿と素データの反復から隠す。

    戻すための控えを返す（`restore` にそのまま渡す）。
    """
    characters = _roster(app, world)
    taken = []
    placed = []
    # 名簿と素データだけでは足りない。選択肢・自由入力・パーティ・敵にも id が載る。
    scrubbed = scrub_saved_refs(
        app, [npc_id for npc_id in registry() if is_mod_npc(npc_id)], write=write)
    for npc_id in list(registry()):
        if not is_mod_npc(npc_id):
            continue
        record = registry()[npc_id]
        if record.get("placed"):
            placed.append((npc_id, record["placed"]))
            unplace(app, npc_id)
        if LIFT_ROSTER and characters is not None and npc_id in characters:
            taken.append(npc_id)
    view = None
    if taken:
        # 名簿から外さない。反復だけ隠す辞書に差し替える（`_RosterView`）。
        target = world if world is not None else getattr(app, "world", None)
        source = characters.source if isinstance(characters, _RosterView) else characters
        view = _RosterView(source, taken)
        try:
            target.characters = view
        except Exception:
            log_exc("modnpc: cannot swap the roster for the save")
            view = None
    # 素データの写しも同じ形で隠す（保存は `save_data_dict['npcs']` をそのまま書く）。
    plain_views = []
    if LIFT_ROSTER:
        mod_ids = [npc_id for npc_id in registry() if is_mod_npc(npc_id)]
        for holder, key in _plain_containers(app):
            source = _plain_source(holder[key])
            present = [npc_id for npc_id in mod_ids if npc_id in source]
            if not present:
                continue
            plain_view = _RosterView(source, present)
            holder[key] = plain_view
            plain_views.append((holder, key, plain_view))
    if write and taken:
        write("modnpc: save: {} instance(s) hidden from the roster".format(len(taken)))
    return {"taken": taken, "placed": placed, "view": view,
            "plain_views": plain_views, "scrubbed": scrubbed, "world": world}


def restore(app, hidden, *, world=None, write=None):
    """保存の直後。引き上げたものを戻す。"""
    if not isinstance(hidden, dict):
        return
    view = hidden.get("view")
    if view is not None:
        target = world if world is not None else getattr(app, "world", None)
        if getattr(target, "characters", None) is view:
            try:
                target.characters = view.source
            except Exception:
                log_exc("modnpc: cannot put the roster back after the save")
    unscrub_saved_refs(app, hidden.get("scrubbed"))
    for holder, key, view in hidden.get("plain_views") or ():
        if holder.get(key) is view:
            holder[key] = view.source
    for npc_id, spot in hidden.get("placed") or ():
        area_id, facility_id, _owner_was, was_owner = spot
        place(app, npc_id, area_id, facility_id, owner=was_owner)


def installed():
    """関所の状態。`{"generation", "owner", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, INSTALLED_ATTR, None)
    return done if isinstance(done, dict) else None


def gate_is_live():
    """保存の関所が**今の世代で**立っているか。

    登録簿は `sys` に在って関所より長生きする。
    関所を立てた MOD の `apply()` が失敗した世代では、前の世代の実体が名簿に残ったまま
    関所だけが無く、次の保存が名簿を舐めて落ちる（実機 2026-09-12 18:58。
    `AttributeError: 'NoneType' object has no attribute 'id'`）。
    だから `spawn` はここが真でなければ載せない。
    """
    try:
        _owner, _name, current = patch.resolve(SAVE_TARGET)
    except (LookupError, AttributeError):
        return False
    for _ in range(32):
        if current is None:
            return False
        if getattr(current, "__wrapper_of__", None) == SAVE_TARGET and                 getattr(current, "__instantale_modnpc_gate__", False) and                 getattr(current, patch.GENERATION_MARK, None) == patch._generation:
            return True
        current = getattr(current, "__original__", None)
    return False


def install(ctx, write=None):
    """関所を立てる。包んだ対象の名前を返す。

    フレームワークを使う MOD が `apply()` の中で呼ぶ。
    何本の MOD が呼んでも、1つの世代につき関所は1つしか立たない
    （2つ立つと、外側と内側が同じものを二度引き上げて二度戻す）。

    立てた MOD が外されていても、`apply()` は注入のたびに全 MOD で走るので
    次の世代では別の MOD が立てる。
    """
    bind_store(ctx, write)
    generation = getattr(ctx, "generation", None)
    done = installed()
    if done is not None and done.get("generation") == generation:
        return done.get("targets", [])
    targets = _install(ctx, write)
    setattr(sys, INSTALLED_ATTR, {"generation": generation,
                                  "owner": getattr(ctx, "_mod", None),
                                  "targets": targets})
    log("modnpc: the gate was declared on {} target(s)".format(len(targets)))
    if write:
        write("modnpc: the gate was declared on {}".format(", ".join(targets)))
    return targets


def _install(ctx, write):
    """関所の中身。宣言した対象の名前を集めて返す。

    実際に当たったかはここでは分からない。
    対象のモジュールがまだ読み込まれていなければ保留になり、
    読み込まれた時点で当たる（TECH.md §3.4）。
    当たったかどうかは `ctx.patches()` か `out/status.json` が持つ。
    """
    targets = []

    def save_game(orig, self, *args, **kwargs):
        """保存の間だけ、MOD の持ち物を世界から外す。"""
        hidden = None
        try:
            fire_all("save", self, args={"phase": "hide"}, write=write)
            snapshot_all(self, write=write)     # ゲームの保存と同じ時点で実体を写す
            hidden = hide(self, write=write)
        except Exception:
            log_exc("modnpc: cannot lift the mod npcs before the save")
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                restore(self, hidden, write=write)
                fire_all("save", self, args={"phase": "restore"}, write=write)
            except Exception:
                log_exc("modnpc: cannot put the mod npcs back after the save")
    # ローダの `wrap` は差し込むラッパではなく元の関数を返すので、
    # 印は差し込んだ後に対象を引き直して付ける（`gate_is_live` が見る）。
    ctx.wrap(SAVE_TARGET, required=False)(save_game)
    try:
        _owner, _name, current = patch.resolve(SAVE_TARGET)
        if getattr(current, "__wrapper_of__", None) == SAVE_TARGET:
            current.__instantale_modnpc_gate__ = True
    except Exception:
        pass
    targets.append(SAVE_TARGET)

    @ctx.wrap(WORLD_TARGET, required=False, safe=True)
    def world_init(orig, self, save_data_dict=None, app=None, *args, **kwargs):
        """セーブを読んだ直後。前の世界の実体を捨て、建て直しの合図を出す。"""
        result = orig(self, save_data_dict, app, *args, **kwargs)
        # 控えの鍵は引数の `save_data_dict` から決めて持ち回る。
        # `app.world_dict` はこの時点でまだ前の世界を指していることがあり、
        # `on["world"]` の中で MOD が `spawn` すると前の世界の控えに書いてしまう。
        key = state.world_key_of_dict(save_data_dict, None)
        if key:
            setattr(sys, _KEY_OVERRIDE_ATTR, key)
        try:
            forget(write=write)
            fire_all("world", app, world=self,
                     args={"save_data_dict": save_data_dict}, write=write)
            restore_world(app, world=self, save_data_dict=save_data_dict, write=write)
        except Exception:
            log_exc("modnpc: cannot tell the mods that the world changed")
        finally:
            try:
                delattr(sys, _KEY_OVERRIDE_ATTR)
            except AttributeError:
                pass
        return result
    targets.append(WORLD_TARGET)

    @ctx.wrap(CONVERSATION_START_TARGET, required=False, safe=True)
    def conversation_start(orig, self, app=None, character_id=None,
                           *args, **kwargs):
        """会話が組まれる直前。相手が登録されていれば知らせる。"""
        try:
            if character_id is not None and has_layers(character_id):
                fire("conversation_start", app, character_id,
                     character=ui.character_of(app, character_id),
                     args={"manager": self}, write=write)
        except Exception:
            log_exc("modnpc: the conversation_start hook failed")
        return orig(self, app, character_id, *args, **kwargs)
    targets.append(CONVERSATION_START_TARGET)

    @ctx.wrap(CONVERSATION_END_TARGET, required=False, safe=True)
    def conversation_end(orig, self, character_id=None, *args, **kwargs):
        """要約が回った後。記憶を控えるならここ（GAME.md §2.25）。"""
        result = orig(self, character_id, *args, **kwargs)
        try:
            if character_id is not None and has_layers(character_id):
                app = getattr(self, "app", None) or ui.find_app()
                fire("conversation_end", app, character_id,
                     character=ui.character_of(app, character_id),
                     args={"manager": self}, write=write)
        except Exception:
            log_exc("modnpc: the conversation_end hook failed")
        return result
    targets.append(CONVERSATION_END_TARGET)

    def detailing(orig, self, character_instance, args, kwargs, site):
        """詳細生成の2つの入口に共通の中身。

        MOD の NPC は素データの写しを置いてから本体に埋めさせる（既定）。
        層が `on["detail"]` で **False** を返したときだけ止める。
        埋めた後は `on["detail_done"]` で知らせる（何が埋まったかを測る口）。
        """
        npc_id = npc_id_of(self, character_instance)
        if not npc_id or not has_layers(npc_id):
            return orig(self, character_instance, *args, **kwargs)
        answers = fire("detail", self, npc_id, character=character_instance,
                       args={"site": site}, write=write)
        if is_mod_npc(npc_id) and any(answer is False for _owner, answer in answers):
            if write:
                write("modnpc: detail generation skipped for {}".format(npc_id))
            return None
        if is_mod_npc(npc_id):
            install_plain(self, npc_id, write=write)
        result = orig(self, character_instance, *args, **kwargs)
        fire("detail_done", self, npc_id, character=character_instance,
             args={"result": result, "site": site}, write=write)
        return result

    @ctx.wrap(DETAIL_TARGET, required=False, safe=True)
    def detail(orig, self, character_instance=None, *args, **kwargs):
        return detailing(orig, self, character_instance, args, kwargs, "ensure")
    targets.append(DETAIL_TARGET)

    @ctx.wrap(DETAIL_GEN_TARGET, required=False, safe=True)
    def detail_gen(orig, self, character_instance=None, *args, **kwargs):
        return detailing(orig, self, character_instance, args, kwargs, "generate")
    targets.append(DETAIL_GEN_TARGET)

    @ctx.wrap(PARTY_TARGET, required=False, safe=True)
    def add_party_member(orig, self, character_id=None, *args, **kwargs):
        """**MOD の NPC は仲間にできない。** 断って記録を残す。

        仲間は `save_data_dict['npcs']` に素データが在ることが前提
        （実セーブで確認。2026-09-13: `game_variables.party` の id が `npcs` の鍵を指し、
        その人物は `areas/<id>/adventurer_npcs` にも載っている）。
        MOD の NPC の素データは保存の直前に隠すので、加入したまま保存すると
        **ロードのときに組み立てられない**。
        保存の窓で `party` から外す手もあるが、それは「仲間だったことが黙って消える」形になる。
        だから、ここで断る。

        仲間にしたい人物は `npcs.make_npc` で本物として作ること
        （セーブに残るので、片付けはその MOD の責任。GAME.md §2.23）。
        """
        if character_id is not None and is_mod_npc(character_id):
            log("modnpc: {} cannot join the party (mod npcs are not in the save's "
                "npcs; use npcs.make_npc for a companion)".format(character_id),
                level="WARN")
            if write:
                write("WARN modnpc: {} cannot join the party; refused".format(
                    character_id))
            fire("party_refused", self, character_id,
                 character=ui.character_of(self, character_id), write=write)
            return None
        return orig(self, character_id, *args, **kwargs)
    targets.append(PARTY_TARGET)

    @ctx.wrap(IMAGE_TARGET, required=False, safe=True)
    def image(orig, self, character_id=None, *args, **kwargs):
        """立ち絵の作り直し。層が False を返せば本体を通さない。

        名前がそのままファイルのパスになる（GAME.md §2.15）ので、
        MOD が持つ絵を使わせたい層はここで断る。
        """
        npc_id = str(character_id) if character_id is not None else ""
        if npc_id and has_layers(npc_id):
            answers = fire("image", self, npc_id,
                           character=ui.character_of(self, npc_id), write=write)
            if any(answer is False for _owner, answer in answers):
                return None
        return orig(self, character_id, *args, **kwargs)
    targets.append(IMAGE_TARGET)

    for name in CONVERSATION_SITES:
        target = "scripts.llm.llm_manager:{}".format(name)
        _wrap_prompt(ctx, target, name, write)
        targets.append(target)
    return targets


def _wrap_prompt(ctx, target, site, write):
    """頼み文の関数を1つ包む。

    引数は名前で受け渡す（位置で決め打たない。GAME.md §2.24）。
    `character_instance` から相手の id を引き、層の `prompt` に
    引数一式を渡す。
    層が `info["args"]` を書き換えたものがそのまま本体へ渡る。
    """

    @ctx.wrap(target, required=False, safe=True)
    def prompt_site(orig, *args, **kwargs):
        bound = _bind(target, args, kwargs)
        if bound is None:
            _warn_once(site, "the signature could not be read; passing through",
                       write)
            return orig(*args, **kwargs)
        character = bound.arguments.get("character_instance")
        if character is None:
            _warn_once(site, "no character_instance among {}; passing through"
                       .format(sorted(bound.arguments)), write)
            return orig(*args, **kwargs)
        app = ui.find_app()
        npc_id = npc_id_of(app, character)
        if not npc_id or not has_layers(npc_id):
            return orig(*args, **kwargs)
        info = {"site": site, "app": app, "npc_id": npc_id,
                "character": character, "args": bound.arguments}
        stack = layers_for(npc_id)
        for layer in stack:
            fn = layer.get("prompt")
            if not callable(fn):
                continue
            info["owner"] = layer["owner"]
            try:
                fn(info)
            except Exception:
                log_exc("modnpc: the prompt hook of {} on {} failed".format(
                    layer["owner"], site))
                if write:
                    write("WARN modnpc: the prompt hook of {} on {} failed"
                          .format(layer["owner"], site))
        apply_notes(info, stack, write=write)
        return orig(*bound.args, **bound.kwargs)

    return prompt_site


def compose_notes(info, stack):
    """層の `notes` を呼んで繋ぐ。`(本文, [持ち主, ...])`。何も無ければ `("", [])`。

    - その頼み文（`info["site"]`）を `sites` に持つ層だけ呼ぶ
    - 空・非文字列は飛ばす。**同じ文章が2本から来たら1つにする**
      （311 と 403 が同じ会話の事実を別々に書く形が GAME.md §2.25 にある）
    - 例外は飲んで記録する（1本の失敗で他の層を巻き込まない）
    """
    blocks, owners, seen_text = [], [], set()
    site = info.get("site")
    for layer in stack:
        fn = layer.get("notes")
        if not callable(fn) or site not in layer.get("sites", NOTES_SITES):
            continue
        info["owner"] = layer["owner"]
        try:
            text = fn(info)
        except Exception:
            log_exc("modnpc: the notes hook of {} on {} failed".format(
                layer["owner"], site))
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        if text in seen_text:
            continue
        seen_text.add(text)
        blocks.append(text)
        owners.append(layer["owner"])
    return "\n\n".join(blocks), owners


def apply_notes(info, stack, write=None):
    """`notes` の本文を相手の複製の `profile` に足し、引数を複製に差し替える。

    手順は `311_` の `with_profile` と同じ（浅い複製、`profile` の末尾、
    引数の `character_instance` を差し替え）。本物には触らない
    （足した文が要約や記憶に残らないように。実体には何も書かない）。
    足した字数と持ち主を返す。何も足さなければ `(0, [])`。
    """
    text, owners = compose_notes(info, stack)
    if not text:
        return 0, []
    character = info.get("character")
    try:
        clone = copy.copy(character)
    except Exception as exc:
        _warn_once(info.get("site"), "cannot copy {} ({}); notes dropped".format(
            type(character).__name__, type(exc).__name__), write)
        return 0, []
    base = getattr(character, "profile", "")
    if not isinstance(base, str):
        base = ""
    clone.profile = (base.rstrip() + "\n\n" + text) if base.strip() else text
    args = info.get("args")
    if isinstance(args, dict):
        args["character_instance"] = clone
    info["character"] = clone
    _note_once(info.get("site"), info.get("npc_id"), owners, len(text), write)
    return len(text), owners


#: 直前と同じ足し方（頼み文・相手・持ち主・字数）は書かない。会話は1ターンに何度も回る。
_NOTED_ATTR = "_instantale_modnpc_noted"


def _note_once(site, npc_id, owners, chars, write=None):
    key = (site, npc_id, tuple(owners), chars)
    if getattr(sys, _NOTED_ATTR, None) == key:
        return
    setattr(sys, _NOTED_ATTR, key)
    if write:
        write("modnpc: {}: +{} chars into the profile of {} from {}".format(
            site, chars, npc_id, ", ".join(owners)))


#: 頼み文の包みが素通りした理由。同じ理由は1度しか書かない
#: （会話は1ターンに何度も回る）。
_WARNED_ATTR = "_instantale_modnpc_warned"


def _warn_once(site, message, write=None):
    warned = getattr(sys, _WARNED_ATTR, None)
    if not isinstance(warned, set):
        warned = set()
        setattr(sys, _WARNED_ATTR, warned)
    key = (site, message)
    if key in warned:
        return
    warned.add(key)
    log("modnpc: {}: {}".format(site, message), level="WARN")
    if write:
        write("WARN modnpc: {}: {}".format(site, message))


def _bind(target, args, kwargs):
    """引数を名前つきで取り出す。署名が読めなければ None。

    署名は `orig` ではなく**素の関数**から取る。
    同じ対象を包む MOD が他に居ると `orig` は内側の MOD のラッパで、
    その署名は `(*args, **kwargs)` でしかない（実機 2026-09-12。
    `conversation_starter` に9本が載っていて、名前が1つも引けずに素通りした）。
    `safe=True` の包みでは `orig` 自体がローダの閉包で `__original__` すら持たない
    （同日。底が `(*a, **kw)` で止まった）ので、`orig` からはたどらない。
    対象名でいま差し込まれている実物を引き、`__original__` を底までたどる
    （ローダのラッパは必ずこれを持つ）。
    それでも読めなければ素通しする（位置で当て推量をしない）。
    """
    try:
        _owner, _name, current = patch.resolve(target)
        signature = inspect.signature(patch.original_of(current))
        bound = signature.bind(*args, **kwargs)
    except (LookupError, AttributeError, TypeError, ValueError):
        return None
    try:
        bound.apply_defaults()
    except Exception:
        pass
    return bound
