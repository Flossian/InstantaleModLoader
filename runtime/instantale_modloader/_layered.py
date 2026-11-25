# -*- coding: utf-8 -*-
"""id に層を積む登録簿の共通の形（ローダの内部用。MOD は使わない）。

`modfacility` と `modnpc` は同じ仕組みを持つ。id ごとの記録に MOD 1本ぶんの登録（層）を積み、
周回ごとの控え（`state\\<フォルダ>\\<世界>.json`）に1件ずつ読み書きし、場面のフックを層の順に呼び、
保存の関所が今の世代で立っているかを確かめる。
その一式が両方に写されていて、違うのはモジュール名・id と実体の名前・ログの文言だけだった。
控えの繋ぎ方（`state.SysWorldStore`）を寄せたのと同じ形の続き。

各モジュールは自分の名前で1つ作り、公開名は別名か薄い包みで残す
（`registry = _reg.registry` のように）。公開 I/F は変えない。

##### 呼ぶ側の関数は引数で受ける

検査はモジュールの属性（`modnpc._persist` / `gate_is_live` / `installed` / `install` など）を差し替える。
ここで `self.persist` を直に呼ぶと差し替えが届かないので、
モジュールの関数を通して呼ぶ所（`despawn` / `_drop_persisted` / `fire` / `layers_for`）は
呼ぶ側がその時点の名前を引いて渡す。

##### 記録・層・控えの形

    記録  {"id", "layers": [層, ...], ...}   層の外の項目はモジュールが `new_record` で決める
    層    {"owner", "priority", "on": {場面: fn}, ...}
    控え  {持ち主: {id: {"snapshot": {...}, "spawned": bool, "place": [...]}}}
"""
import contextlib
import copy
import sys

from . import log_exc, patch


class LayeredRegistry(object):
    """id に層を積む登録簿と、その控え・場面の通知・関所の確かめ。

    | 引数 | |
    |---|---|
    | `tag` | ログの頭（`modfacility` / `modnpc`） |
    | `registry_attr` | 登録簿を置く `sys` の属性名。注入をまたいで残る（`apply()` は1プロセスで何度も呼ばれる。TECH.md §3.5） |
    | `installed_attr` | 関所を立てた世代の控えを置く `sys` の属性名 |
    | `stores` | 控えの繋ぎ方（`state.SysWorldStore`） |
    | `id_key` / `obj_key` | 場面のフックに渡す `info` の id と実体の鍵（`facility_id` / `facility`、`npc_id` / `character`）。MOD のフックとの約束 |
    | `gate_mark` | 保存の関所の包みに立てる印の属性名 |
    | `save_target` | 保存の関所が包む対象 |
    | `off_text` | 壊せなかったときのログの文言（`off the town` / `off the world`） |
    | `new_record` | `fn(id) -> dict`。記録の初めの形（層の外の項目はモジュールごとに違う） |
    """

    def __init__(self, tag, registry_attr, installed_attr, stores, *, id_key, obj_key,
                 gate_mark, save_target, off_text, new_record):
        self.tag = tag
        self.registry_attr = registry_attr
        self.installed_attr = installed_attr
        self.stores = stores
        self.id_key = id_key
        self.obj_key = obj_key
        self.gate_mark = gate_mark
        self.save_target = save_target
        self.off_text = off_text
        self.new_record = new_record
        #: 前に書いた定型の行の中身（種類 -> 中身）。同じ中身なら繰り返さない（`changed`）。
        #: 注入し直すとモジュールごと読み直されて空に戻り、新しいログの頭には必ず出る。
        #: 両方のモジュールが `snapshot` と `gate` の種類を使うので、辞書はインスタンスごとに持つ（1つにすると互いに消し合う）。
        self.last_written = {}

    # -- 定型の行 -------------------------------------------------------------
    def changed(self, kind, content) -> bool:
        """`kind` の行の中身が前に書いたものと違えば控えて True。同じなら False。"""
        if self.last_written.get(kind) == content:
            return False
        self.last_written[kind] = content
        return True

    # -- 登録簿 ---------------------------------------------------------------
    def registry(self) -> dict:
        """`{id: 記録}`。書き換えてよいのは持ち主のモジュールだけ。"""
        reg = getattr(sys, self.registry_attr, None)
        if not isinstance(reg, dict):
            reg = {}
            setattr(sys, self.registry_attr, reg)
        return reg

    def record(self, entity_id) -> dict:
        """その id の記録。無ければ `new_record` で作る。"""
        reg = self.registry()
        record = reg.get(entity_id)
        if not isinstance(record, dict):
            record = reg[entity_id] = self.new_record(entity_id)
        return record

    def layers(self, entity_id) -> list:
        """その id に積まれている層。積んだ順。"""
        record = self.registry().get(str(entity_id))
        return list(record["layers"]) if isinstance(record, dict) else []

    def entries(self, owner=None) -> list:
        """登録されている id。`owner` を渡すとその MOD の分だけ。"""
        out = []
        for entity_id, record in self.registry().items():
            if owner is None or any(layer["owner"] == owner
                                    for layer in record["layers"]):
                out.append(entity_id)
        return sorted(out)

    def layer_of(self, owner, entity_id):
        """その id に `owner` が積んだ層。無ければ None。"""
        for layer in self.layers(entity_id):
            if layer["owner"] == owner:
                return layer
        return None

    def unregister(self, owner, entity_id, app, write, despawn, drop_persisted) -> list:
        """層を外す。外した id を返す（`modfacility.unregister` / `modnpc.unregister` の中身）。

        `entity_id` が None ならその MOD の層を全部外す。
        層が1つも残らなくなった id は、`app` があれば `despawn` で世界から降ろしてから記録ごと捨てる。
        控えは `despawn` の後に消す（先に消すと `spawned=False` が書き戻る）。
        """
        dropped = []
        for target in ([str(entity_id)] if entity_id is not None else self.entries(owner)):
            record = self.registry().get(target)
            if not isinstance(record, dict):
                continue
            record["layers"] = [layer for layer in record["layers"]
                                if layer["owner"] != owner]
            dropped.append(target)
            if not record["layers"] and app is not None:
                try:
                    despawn(app, target, write=write)
                except Exception:
                    log_exc("{}: cannot take {} {}".format(self.tag, target, self.off_text))
            if app is not None:
                drop_persisted(app, owner, target)
            if record["layers"]:
                continue
            self.registry().pop(target, None)
        if write and dropped:
            write("{}: {} unregistered {}".format(self.tag, owner, ", ".join(dropped)))
        return dropped

    def purge(self, app, write, despawn) -> None:
        """登録簿を空にする。`app` があれば先に全部を `despawn` で世界から降ろす。"""
        for entity_id in list(self.registry()):
            if app is not None:
                try:
                    despawn(app, entity_id, write=write)
                except Exception:
                    log_exc("{}: cannot take {} {}".format(self.tag, entity_id, self.off_text))
        self.registry().clear()
        if write:
            write("{}: the registry is empty".format(self.tag))

    # -- 控え -----------------------------------------------------------------
    @contextlib.contextmanager
    def keyed(self, key):
        """`with keyed(key):` の間、控えをその周回の鍵で読み書きする。

        `World.__init__` を MOD が直に包み、orig の後で `spawn` / `place` を呼ぶと、
        ローダの関所（`world_init`）が立てた鍵はもう消えていて、控えは `app` から決めた鍵
        （前の周回のことがある）へ書かれる。鍵を引数の `save_data_dict` から決めて
        （`state.playthrough_key_of_dict`）ここで立てれば、読んでいる周回へ書ける。
        抜けるときは入る前の値へ戻す（入れ子にしても外側の鍵を消さない）。
        鍵が空なら何も立てない。`modnpc.keyed` と `modfacility.keyed` は対で使う。
        """
        attr = self.stores.override_attr
        missing = object()
        previous = getattr(sys, attr, missing)
        if isinstance(key, str) and key:
            setattr(sys, attr, key)
        try:
            yield key
        finally:
            if previous is missing:
                try:
                    delattr(sys, attr)
                except AttributeError:
                    pass
            else:
                setattr(sys, attr, previous)

    def persist(self, app, owner, entity_id, **changes) -> bool:
        """1件ぶんの控えを書く。`snapshot=`（写し）／`spawned=`／`place=`。書けたら True。

        `snapshot` は JSON に落ちる項目だけにしてから渡す（実行時のオブジェクトは落とさない）。
        """
        if not owner:
            return False
        key, bucket = self.stores.bucket(app)
        if key is None:
            return False
        self.put_entry(bucket, owner, entity_id, changes)
        return self.stores.store().save(key, bucket)

    @staticmethod
    def put_entry(bucket, owner, entity_id, changes) -> None:
        """控え `bucket` の1件ぶんを書き換える（書き出さない）。"""
        entry = bucket.setdefault(owner, {}).setdefault(
            str(entity_id), {"snapshot": None, "spawned": False, "place": None})
        if "snapshot" in changes:
            entry["snapshot"] = copy.deepcopy(changes["snapshot"])
        if "spawned" in changes:
            entry["spawned"] = bool(changes["spawned"])
        if "place" in changes:
            entry["place"] = list(changes["place"]) if changes["place"] else None

    def drop_persisted(self, app, owner, entity_id) -> bool:
        """その持ち主の1件ぶんの控えを消して書き出す。消して書けたら True。"""
        key, bucket = self.stores.bucket(app)
        if key is None:
            return False
        owned = bucket.get(owner)
        if not isinstance(owned, dict) or str(entity_id) not in owned:
            return False
        owned.pop(str(entity_id), None)
        if not owned:
            bucket.pop(owner, None)
        return self.stores.store().save(key, bucket)

    def persisted_entry(self, app, owner, entity_id):
        """その持ち主の1件ぶんの控え。無ければ None。"""
        key, bucket = self.stores.bucket(app)
        if key is None:
            return None
        owned = bucket.get(owner)
        entry = owned.get(str(entity_id)) if isinstance(owned, dict) else None
        return entry if isinstance(entry, dict) else None

    # -- 呼ぶ -----------------------------------------------------------------
    def fire(self, site, app, entity_id, layers, obj=None, *, args=None, world=None,
             write=None) -> list:
        """`layers` のフック `on[site]` を順に呼ぶ。`[(持ち主, 戻り値), ...]`。

        `layers` は呼ぶ側が決めた順（`layers_for`。`modnpc` は全員向けの層を足す）。
        フックの例外は飲む（1本の MOD の失敗で関所を止めない）。
        飲んだことはローダのログに残す（見えなくするためではない）。
        """
        results = []
        for layer in layers:
            fn = (layer.get("on") or {}).get(site)
            if not callable(fn):
                continue
            info = {"site": site, "app": app, self.id_key: str(entity_id),
                    self.obj_key: obj, "args": args, "world": world,
                    "owner": layer["owner"]}
            try:
                results.append((layer["owner"], fn(info)))
            except Exception:
                log_exc("{}: {} hook of {} on {} failed".format(
                    self.tag, site, layer["owner"], entity_id))
                if write:
                    write("WARN {}: {} hook of {} on {} failed".format(
                        self.tag, site, layer["owner"], entity_id))
        return results

    def fire_all(self, fire, site, app, *, args=None, world=None, write=None) -> list:
        """登録されている全部の id に同じ場面を通知する。`fire` はモジュールの `fire`（id ごとの層の順を持つ）。"""
        results = []
        for entity_id in list(self.registry()):
            results.extend(fire(site, app, entity_id, args=args, world=world,
                                write=write))
        return results

    # -- 関所 -----------------------------------------------------------------
    def installed(self):
        """関所の状態。`{"generation", "owner", "targets"}` か、立っていなければ None。"""
        done = getattr(sys, self.installed_attr, None)
        return done if isinstance(done, dict) else None

    def gate_is_live(self) -> bool:
        """保存の関所が今の世代で立っているか（`save_target` の包みを `gate_mark` と世代の印で探す）。"""
        try:
            _owner, _name, current = patch.resolve(self.save_target)
        except (LookupError, AttributeError):
            return False
        for _ in range(32):
            if current is None:
                return False
            if getattr(current, "__wrapper_of__", None) == self.save_target \
                    and getattr(current, self.gate_mark, False) \
                    and getattr(current, patch.GENERATION_MARK, None) == patch._generation:
                return True
            current = getattr(current, "__original__", None)
        return False
