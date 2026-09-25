# -*- coding: utf-8 -*-
r"""計測: 画像生成の出口とバックエンド。ゲームは変えない。

画像の強化（解像度・LoRA の付け替え・サンプラー上書き）を DLL の差し替えから
ローダの MOD へ移せるかの下調べ。
recon（`out\recon\targets.txt`）から読めるのは「そこに関数が在る」ところまでで、
次の3つが推測のまま残っていた。

    1. バックエンドは選ばれた一族だけが import されるのか
    2. 生成の出口は本当に1つか（txt2img も img2img も同じメソッドに来るのか、
       `highres_upscale` の段が `upscale()` という別の口を通っていないか）
    3. TAESD・チェックポイント・VAE はいつ決まるのか

答えは GAME.md §2.33（ゲームの側の結論）と VERIFICATION_LOG.md §2.86（測り方と件数）。
残っている書き込み側は VERIFICATION.md §3.65。

録り方は2段。
上の層（種類が分かる `image_generation.<一族>.*` の関数）でスレッドに印を立て、
下の出口（`txt2img_pipe` の型のメソッド）で印ごと引数を残す。
`111_llm_prompt_replace` がプロバイダを名指しせずに送信の口を捕まえているのと同じ形で、
こちらはモジュール名を**実行時に `sys.modules` から引く**。
名指ししないこと自体が測りたいこと（1）の答えになる。

    out\image_generation.log     読む用
    out\image_generation.jsonl   1呼び出し＝1行。後から数える用

見張りを1本立てて、設定を切り替えた後に現れたモジュールにも当て直す
（`ctx.superseded()` で降りる。TECH.md §3.6.1）。
プロンプトは頭 `PROMPT_HEAD` 字と長さ、それに `<lora:...>` タグだけを残す
（本文は世界とキャラの中身なので丸ごとは残さない）。
"""
import datetime
import inspect
import json
import os
import re
import sys
import threading
import time

from instantale_modloader import frames

#: `sys.meta_path` に置いた観測者の目印（入れ直しで積み上げないため）。
OBSERVER_MARK = "_probe_230_image_generation"

#: 上の層が立てる種類の印（スレッドごと）。**`apply()` の中に置いてはいけない。**
#: 当て直しが挟まると、上の層を包んだ apply と出口を包んだ apply が別世代になり、
#: クロージャごとに別の入れ物になって印が渡らない
#: （diffusers 系で `kind=None` が出た。VERIFICATION_LOG.md §2.86）。
MARKS = threading.local()

LOG_BASENAME = "image_generation.log"
RECORD_BASENAME = "image_generation.jsonl"

#: 棚卸しで拾うモジュール名。一族ごとに名前が変わるので広めに採る。
MODULE_HINTS = ("image_generation", "sdcpp", "stable_diffusion", "diffusers",
                "optimum", "openvino")

#: 自分を数えないための除外（ローダは MOD を `instantale_mod_<フォルダ名>` で登録する）。
MODULE_SKIP = ("instantale_mod", "instantale_modloader")

#: 「出口を持っている層」の目印。どちらかを持つモジュールを manager とみなす。
MANAGER_MARKS = ("txt2img_pipe", "load_sd_pipeline")

#: manager で包む関数（在るものだけ）。種類の印を立てる層。
MANAGER_FUNCS = ("generate_image_anime", "image_to_image_anime",
                 "generate_image_real_lcm")

#: 組み立ての関数。前後で `txt2img_pipe` が入れ替わるかを見る。
PIPELINE_FUNC = "load_sd_pipeline"

#: sdcpp 自身のメッセージ（`log_event(level, message)`）。
#: 何を読んだか・読めなかったかはここにしか出ない
#: （TAESD が読めないと黙って VAE へ落ちる。実機の色化け）。
LOGGER_SUFFIX = "._logger"
LOGGER_FUNC = "log_event"

#: 出口の候補。pipe の型が**自分の MRO の中に**持っているものだけ包む
#: （`getattr(cls, "__call__")` はメタクラスの `type.__call__` を拾うので使わない）。
EXIT_METHODS = ("generate_image", "image_to_image", "img2img", "txt2img",
                "upscale", "generate_video", "__call__")

#: 生成の引数のうち録る名前（`generate_image` の並びは recon の targets.txt）。
PARAM_KEYS = ("width", "height", "cfg_scale", "sample_steps", "sample_method",
              "scheduler", "seed", "clip_skip", "strength", "batch_count",
              "upscale_factor", "eta", "guidance", "num_inference_steps",
              "guidance_scale")

#: 構築時に録るキーワード。TAESD がどこで決まるかの直接の答えになる。
INIT_KEYS = ("model_path", "taesd_path", "vae_path", "lora_model_dir",
             "embedding_dir", "diffusion_model_path", "clip_l_path",
             "wtype", "rng_type", "vae_tiling", "vae_decode_only",
             "diffusion_flash_attn", "n_threads", "keep_vae_on_cpu")

#: manager モジュールで録るグローバル。
GLOBAL_KEYS = ("model_path_anime", "taesd_path", "vae_path", "lora_dir",
               "lora_name", "use_taesd", "use_vae_tiling", "use_flash_attension",
               "current_lcm", "character_generation_quality",
               "monster_generation_quality", "background_generation_quality")

#: 呼び出し元のファイル名に出る種類。
KIND_HINTS = (("image_generation_creature", "creature"),
              ("image_generation_background", "background"))

#: プロンプトから残す頭の字数。
PROMPT_HEAD = 80

#: `<lora:名前:重み>` を数える。ゲーム自身が入れている分が Python の層に出るか。
LORA_TAG = re.compile(r"<lora:[^>]*>")

#: 見張りの間隔（秒）。設定画面での切り替えを跨いで拾うためだけのもの。
POLL_SECONDS = 5.0


class _LoaderProxy(object):
    """本物のローダに被せて、**モジュールの本体が走り終えた直後**に知らせる器。

    ローダの実体は他のモジュールと共有されていることがあるので、
    `exec_module` を直に差し替えず、その1件ぶんだけを包む。
    """

    def __init__(self, loader, name, after):
        self._loader = loader
        self._name = name
        self._after = after

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        self._after(self._name, module)

    def __getattr__(self, item):
        return getattr(self._loader, item)


class _ImportObserver(object):
    """`sys.meta_path` の先頭に立って、import の瞬間を捕まえる。

    5秒ごとの見張りでは**パイプラインが組まれた後**にしか気付けない。
    import と構築が同じ関数の中で閉じているため（GAME.md §2.33）。
    ここは自分では読み込まず、他の finder に spec を作らせて
    その `loader` だけを包む（`find_spec` は名前を見るだけ）。
    """

    def __init__(self, hints, skips, after, on_seen):
        self.hints = hints
        self.skips = skips
        self._after = after
        self._on_seen = on_seen
        self._busy = threading.local()
        setattr(self, OBSERVER_MARK, True)

    def _matches(self, name):
        low = name.lower()
        if any(low.startswith(skip) for skip in self.skips):
            return False
        return any(hint in low for hint in self.hints)

    def find_spec(self, name, path=None, target=None):
        # ここで投げるとゲームの import ごと落ちるので、何があっても None に落とす
        # （None ＝ 「こちらは何も見つけていない」で、本来の finder がそのまま動く）。
        try:
            return self._find(name, path, target)
        except Exception:
            return None

    def _find(self, name, path, target):
        if frames.attr(self._busy, "on", False) or not self._matches(name):
            return None
        self._busy.on = True
        try:
            spec = None
            for finder in list(sys.meta_path):
                if finder is self:
                    continue
                find = frames.attr(finder, "find_spec", None)
                if find is None:
                    continue
                try:
                    spec = find(name, path, target)
                except Exception:
                    spec = None
                if spec is not None:
                    break
        finally:
            self._busy.on = False
        self._on_seen(name, spec)
        if spec is None or frames.attr(spec, "loader", None) is None:
            return spec
        try:
            spec.loader = _LoaderProxy(spec.loader, name, self._after)
        except Exception:
            pass
        return spec


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)

    #: 当てた対象と、棚卸しで見たモジュール。当て直しで二重に包まないため。
    seen = {"targets": set(), "modules": set()}

    #: 数える用。出口1本で漏れが無いかは `manager` と `exit` の差で分かる。
    counts = {"manager": 0, "exit": 0, "exit_unmarked": 0,
              "init": 0, "pipeline": 0}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------ 見るための道具

    def config_backend():
        """ゲームが選んでいるバックエンド名（GAME.md §2.12.1 の `config.json`）。"""
        base = os.environ.get("LOCALAPPDATA") or ""
        path = os.path.join(base, "Darmabeko", "Instantale", "config.json")
        data = ctx.read_json(path, {}) or {}
        setting = (((data.get("ai_setting") or {})
                    .get("local_model_setting") or {}).get("sd_backend") or {})
        return {"path": path, "name": setting.get("name"),
                "character": setting.get("character_generation_quality"),
                "monster": setting.get("monster_generation_quality"),
                "background": setting.get("background_generation_quality"),
                "advanced": setting.get("advanced_setting")}

    def related_modules():
        """名前に心当たりのあるモジュール（今 `sys.modules` に載っているもの）。"""
        found = []
        for name in list(sys.modules):
            low = name.lower()
            if any(low.startswith(skip) for skip in MODULE_SKIP):
                continue
            if any(hint in low for hint in MODULE_HINTS):
                found.append(name)
        return sorted(found)

    def managers():
        """出口を持っている層（`txt2img_pipe` か `load_sd_pipeline` を持つもの）。"""
        found = []
        for name in related_modules():
            mod = sys.modules.get(name)
            if mod is None:
                continue
            for mark in MANAGER_MARKS:
                if frames.attr(mod, mark, None) is not None:
                    found.append(name)
                    break
        return found

    def type_of(value):
        """値の型を「どのモジュールの何か」と、同じ物かを見るための id で。"""
        if value is None:
            return None
        cls = type(value)
        return {"module": frames.attr(cls, "__module__", None),
                "qualname": frames.attr(cls, "__qualname__", cls.__name__),
                "id": hex(id(value))}

    def owner_of(cls, name):
        """`name` を**自分の MRO の中に**持っているクラス。無ければ None。"""
        for base in frames.attr(cls, "__mro__", (cls,)):
            if base is object:
                continue
            if name in frames.attr(base, "__dict__", {}):
                return base
        return None

    def globals_of(mod):
        got = {}
        for key in GLOBAL_KEYS:
            value = frames.attr(mod, key, None)
            if value is not None:
                got[key] = frames.repr_value(value)
        return got

    def text_brief(value):
        """プロンプトは頭と長さと lora タグだけ（本文は残さない）。"""
        if not isinstance(value, str):
            return {"type": None if value is None else type(value).__name__}
        return {"len": len(value), "head": value[:PROMPT_HEAD],
                "lora": LORA_TAG.findall(value)}

    def image_brief(value):
        """`init_image` / `mask_image` は在るかと寸法だけ。"""
        if value is None:
            return None
        size = frames.attr(value, "size", None)
        return {"type": type(value).__name__,
                "size": "x".join(str(n) for n in size)
                        if isinstance(size, tuple) else frames.repr_value(size)}

    def kind_now():
        """上の層が立てた印。立っていなければ None。"""
        return frames.attr(MARKS, "kind", None)

    def kind_from_stack():
        """呼び出し元のファイル名から種類を決める（印が無いときの予備）。"""
        chain = frames.caller(depth=4)
        for hint, kind in KIND_HINTS:
            if hint in chain:
                return kind
        return None

    # ------------------------------------------------------------------ 棚卸し

    def exit_class(mod):
        """出口を持つクラス。**建つ前でも掴めるように**モジュール側から探す。

        `txt2img_pipe` の型だけを見ていた版3では、構築そのもの（`__init__`）を
        包めたのが建った後になっていた（構築の引数が1件も録れない。VERIFICATION_LOG.md §2.86）。
        manager は本体の先頭でクラスを import しているので、そちらから採る。
        """
        pipe = frames.attr(mod, "txt2img_pipe", None)
        if pipe is not None:
            return type(pipe)
        here = frames.attr(mod, "StableDiffusion", None)
        if isinstance(here, type) and owner_of(here, "generate_image") is not None:
            return here
        for value in list(vars(mod).values()) if mod is not None else []:
            if isinstance(value, type) and owner_of(value, "generate_image"):
                return value
        return None

    def exits_of(mod_or_pipe, from_module=False):
        """出口の名前。棚卸しの表示用。"""
        cls = exit_class(mod_or_pipe) if from_module else (
            None if mod_or_pipe is None else type(mod_or_pipe))
        if cls is None:
            return []
        return [name for name in EXIT_METHODS if owner_of(cls, name) is not None]

    def inventory(reason):
        """今どの一族が載っていて、出口が誰なのかを1回ぶん残す。"""
        backend = config_backend()
        modules = related_modules()
        seen["modules"] = set(modules)
        rows = []
        for name in managers():
            mod = sys.modules.get(name)
            pipe = frames.attr(mod, "txt2img_pipe", None)
            here = frames.attr(mod, "StableDiffusion", None)
            rows.append({
                "manager": name,
                "pipe": type_of(pipe),
                # manager が持つクラスが pipe の型そのものか
                # ＝ クラス属性を包めば別名を追わずに全部へ効くか。
                "class_is_pipe_type": bool(pipe is not None and here is type(pipe)),
                "class_module": frames.attr(here, "__module__", None),
                "exits": exits_of(mod, from_module=True),
                "globals": globals_of(mod),
                "funcs": [fn for fn in MANAGER_FUNCS
                          if frames.attr(mod, fn, None) is not None],
            })
        write("=" * 72)
        write("inventory ({}): sd_backend={!r} quality={}/{}/{}".format(
            reason, backend.get("name"), backend.get("character"),
            backend.get("monster"), backend.get("background")))
        write("    advanced={}".format(
            json.dumps(backend.get("advanced"), ensure_ascii=False)))
        write("    modules ({}): {}".format(len(modules), ", ".join(modules)))
        for row in rows:
            write("    manager {}".format(row["manager"]))
            write("        pipe={} class_is_pipe_type={} class_module={}".format(
                json.dumps(row["pipe"], ensure_ascii=False),
                row["class_is_pipe_type"], row["class_module"]))
            write("        exits={} funcs={}".format(
                ", ".join(row["exits"]) or "(none)", ", ".join(row["funcs"])))
            for key, value in sorted(row["globals"].items()):
                write("        {} = {}".format(key, value))
        record({"at": now(), "event": "inventory", "reason": reason,
                "backend": backend, "modules": modules, "managers": rows,
                "counts": dict(counts)})
        return rows

    # ------------------------------------------------------------------ 包む側

    def signature_of(func):
        try:
            return inspect.signature(func)
        except Exception:
            return None

    def params_of(sig, args, kwargs):
        """引数を名前で引けるようにする。読めないビルドでは位置のまま残す。"""
        if sig is not None:
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                return dict(bound.arguments), None
            except Exception:
                pass
        return dict(kwargs), [frames.repr_value(a) for a in args]

    def make_manager_hook(module_name, fn_name, sig):
        """種類の印を立てて元を呼ぶ。引数（寸法とプロンプト）も残す。"""
        def hook(orig, *args, **kwargs):
            kind = kind_from_stack() or "?"
            before = kind_now()
            MARKS.kind = "{}:{}".format(kind, fn_name)
            counts["manager"] += 1
            started = time.time()
            try:
                return orig(*args, **kwargs)
            finally:
                MARKS.kind = before
                named, positional = params_of(sig, args, kwargs)
                row = {"at": now(), "event": "manager", "module": module_name,
                       "func": fn_name, "kind": kind, "nested": before,
                       "seconds": round(time.time() - started, 2),
                       "thread": threading.current_thread().name,
                       "caller": frames.caller(depth=3),
                       "width": frames.repr_value(named.get("width")),
                       "height": frames.repr_value(named.get("height")),
                       "positive": text_brief(named.get("positive_prompt")),
                       "negative": text_brief(named.get("negative_prompt")),
                       "positional": positional}
                write("manager {}.{} kind={} {}x{} {:.2f}s lora={}".format(
                    module_name.rsplit(".", 1)[-1], fn_name, kind,
                    row["width"], row["height"], row["seconds"],
                    row["positive"].get("lora")))
                record(row)
        return hook

    def make_pipeline_hook(module_name, sig):
        """`load_sd_pipeline` の前後。pipe が入れ替わるかで組み直しが分かる。"""
        def hook(orig, *args, **kwargs):
            mod = sys.modules.get(module_name)
            before = type_of(frames.attr(mod, "txt2img_pipe", None))
            before_globals = globals_of(mod)
            started = time.time()
            result = orig(*args, **kwargs)
            counts["pipeline"] += 1
            mod = sys.modules.get(module_name)
            after = type_of(frames.attr(mod, "txt2img_pipe", None))
            changed = (before or {}).get("id") != (after or {}).get("id")
            after_globals = globals_of(mod)
            write("-" * 72)
            write("load_sd_pipeline #{} in {} ({:.1f}s) rebuilt={}".format(
                counts["pipeline"], module_name, time.time() - started, changed))
            write("    from {}".format(frames.caller(depth=4)))
            write("    pipe {} -> {}".format(
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False)))
            for key in sorted(set(before_globals) | set(after_globals)):
                old, new = before_globals.get(key), after_globals.get(key)
                if old != new:
                    write("    {}: {} -> {}".format(key, old, new))
            record({"at": now(), "event": "load_sd_pipeline",
                    "module": module_name, "call": counts["pipeline"],
                    "seconds": round(time.time() - started, 2),
                    "thread": threading.current_thread().name,
                    "caller": frames.caller(depth=4),
                    "pipe_before": before, "pipe_after": after,
                    "rebuilt": changed,
                    "globals_before": before_globals,
                    "globals_after": after_globals})
            # 組み直しで別のクラスになっていることがあるので包み直す。
            attach("after load_sd_pipeline")
            return result
        return hook

    def make_init_hook(label, sig):
        """出口のクラスの構築。TAESD とチェックポイントが実際に何で渡るか。"""
        def hook(orig, self, *args, **kwargs):
            started = time.time()
            result = orig(self, *args, **kwargs)
            counts["init"] += 1
            named, positional = params_of(sig, (self,) + tuple(args), kwargs)
            got = {}
            for key in INIT_KEYS:
                if key in named:
                    got[key] = frames.repr_value(named.get(key))
            write("-" * 72)
            write("{} built #{} ({:.1f}s) from {}".format(
                label, counts["init"], time.time() - started,
                frames.caller(depth=4)))
            for key, value in sorted(got.items()):
                write("    {} = {}".format(key, value))
            record({"at": now(), "event": "pipe_init", "target": label,
                    "call": counts["init"],
                    "seconds": round(time.time() - started, 2),
                    "thread": threading.current_thread().name,
                    "caller": frames.caller(depth=4),
                    "kwargs": got, "positional": positional})
            return result
        return hook

    def make_exit_hook(label, method, sig):
        """出口。印（種類）ごと引数一式を残す。ゲームへは素通し。"""
        def hook(orig, self, *args, **kwargs):
            counts["exit"] += 1
            kind = kind_now()
            if kind is None:
                counts["exit_unmarked"] += 1
            # **元を呼ぶ前にも1行残す。**
            # ネイティブ側で落ちるとプロセスごと消えて、
            # 返ってから書く記録は何も残らない（実機: SDXL で1回）。
            before = params_of(sig, (self,) + tuple(args), kwargs)[0]
            write("exit #{} {} kind={} {}x{} lora={} ...".format(
                counts["exit"], method, kind,
                frames.repr_value(before.get("width")),
                frames.repr_value(before.get("height")),
                text_brief(before.get("prompt")).get("lora")))
            started = time.time()
            result = orig(self, *args, **kwargs)
            named, positional = params_of(sig, (self,) + tuple(args), kwargs)
            params = {}
            for key in PARAM_KEYS:
                if key in named:
                    params[key] = frames.repr_value(named.get(key))
            row = {"at": now(), "event": "exit", "target": label,
                   "method": method, "call": counts["exit"], "kind": kind,
                   "stack_kind": kind_from_stack(),
                   "seconds": round(time.time() - started, 2),
                   "thread": threading.current_thread().name,
                   "caller": frames.caller(depth=4),
                   "params": params,
                   "positive": text_brief(named.get("prompt")),
                   "negative": text_brief(named.get("negative_prompt")),
                   "init_image": image_brief(named.get("init_image")),
                   "mask_image": image_brief(named.get("mask_image")),
                   "returned": frames.repr_value(result),
                   "positional": positional,
                   "counts": dict(counts)}
            write("exit #{} {} kind={} {}x{} {}/{}/{} {:.1f}s lora={}".format(
                counts["exit"], method, kind,
                params.get("width"), params.get("height"),
                params.get("sample_method"), params.get("sample_steps"),
                params.get("cfg_scale"), row["seconds"],
                row["positive"].get("lora")))
            if kind is None:
                write("    WARN no mark from the upper layer; from {}".format(
                    row["caller"]))
            record(row)
            return result
        return hook

    # ------------------------------------------------------- 当てる・当て直す

    def install(target, hook):
        """同じ対象へ二度当てない。当たったかどうかだけ残す。"""
        if target in seen["targets"]:
            return 0
        seen["targets"].add(target)
        ctx.wrap(target, required=False, safe=True)(hook)
        write("    wrapped {}".format(target))
        return 1

    def make_logger_hook(module_name):
        """sdcpp が出すメッセージをそのまま残す（ゲームは捨てている）。"""
        def hook(orig, *args, **kwargs):
            try:
                message = args[1] if len(args) > 1 else kwargs.get("message", "")
                write("sdcpp: {}".format(frames.short(message, 300)))
            except Exception:
                pass
            return orig(*args, **kwargs)
        return hook

    def attach(reason):
        """今 `sys.modules` に居るものへ当てる。バックエンドは名指ししない。"""
        added = 0
        for name in list(sys.modules):
            if not name.endswith(LOGGER_SUFFIX):
                continue
            mod = sys.modules.get(name)
            if frames.attr(mod, LOGGER_FUNC, None) is None:
                continue
            added += install("{}:{}".format(name, LOGGER_FUNC),
                             make_logger_hook(name))
        for name in managers():
            mod = sys.modules.get(name)
            for fn in MANAGER_FUNCS:
                func = frames.attr(mod, fn, None)
                if func is None:
                    continue
                added += install("{}:{}".format(name, fn),
                                 make_manager_hook(name, fn, signature_of(func)))
            builder = frames.attr(mod, PIPELINE_FUNC, None)
            if builder is not None:
                added += install(
                    "{}:{}".format(name, PIPELINE_FUNC),
                    make_pipeline_hook(name, signature_of(builder)))
            cls = exit_class(mod)
            if cls is None:
                continue
            for method in EXIT_METHODS + ("__init__",):
                base = owner_of(cls, method)
                if base is None:
                    continue
                func = base.__dict__.get(method)
                target = "{}:{}.{}".format(
                    frames.attr(base, "__module__", "?"),
                    frames.attr(base, "__qualname__", base.__name__), method)
                sig = signature_of(func)
                if method == "__init__":
                    added += install(target, make_init_hook(target, sig))
                else:
                    added += install(target, make_exit_hook(target, method, sig))
        if added:
            write("attach ({}): {} new target(s)".format(reason, added))
        return added

    # ------------------------------------------------------- import の瞬間を見る

    def on_import_seen(name, spec):
        """spec を作らせた直後（本体はまだ走っていない）。"""
        try:
            record({"at": now(), "event": "import_seen", "module": name,
                    "found": spec is not None,
                    "thread": threading.current_thread().name,
                    "caller": frames.caller(depth=4)})
        except Exception:
            pass

    def on_import_done(name, module):
        """モジュールの本体が走り終えた直後。**MOD が値を差し替えられる時点**。"""
        try:
            pipe = frames.attr(module, "txt2img_pipe", None)
            built = pipe is not None
            write("import done: {} (txt2img_pipe {}) from {}".format(
                name, "already built" if built else "not built yet",
                frames.caller(depth=4)))
            record({"at": now(), "event": "import_done", "module": name,
                    "pipe": type_of(pipe), "built_during_import": built,
                    "globals": globals_of(module),
                    "has_loader": frames.attr(module, PIPELINE_FUNC, None) is not None,
                    "thread": threading.current_thread().name,
                    "caller": frames.caller(depth=4)})
            if attach("import of {}".format(name)):
                ctx.refresh_status()
        except Exception:
            ctx.log_exc("image generation probe: import hook failed")

    def install_observer():
        """`sys.meta_path` の先頭に観測者を置く。入れ直しでは古い分を外す。"""
        kept = [f for f in sys.meta_path
                if frames.attr(f, OBSERVER_MARK, None) is None]
        dropped = len(sys.meta_path) - len(kept)
        observer = _ImportObserver(MODULE_HINTS, MODULE_SKIP,
                                   on_import_done, on_import_seen)
        sys.meta_path[:] = [observer] + kept
        write("import observer installed (dropped {} old one(s))".format(dropped))

    # ------------------------------------------------- 素の継ぎ目で間に合うか

    @ctx.wrap("__main__:AIManager.set_ai_models", required=False, safe=True)
    def set_ai_models(orig, self, *args, **kwargs):
        """パイプラインを建てる呼び出し元（GAME.md §2.33）。

        `__main__` は注入の時点で居るので、この地点は**観測者なしで包める**。
        入った瞬間に manager が既に import されているなら、
        MOD は `sys.meta_path` に触らずここでグローバルを差し替えられる。
        """
        found = managers()
        pipe = None
        if found:
            pipe = frames.attr(sys.modules.get(found[0]), "txt2img_pipe", None)
        write("-" * 72)
        write("AIManager.set_ai_models: managers={} pipe={}".format(
            ", ".join(found) or "(none)", json.dumps(type_of(pipe))))
        record({"at": now(), "event": "set_ai_models", "when": "before",
                "managers": found, "pipe": type_of(pipe),
                "thread": threading.current_thread().name,
                "caller": frames.caller(depth=3)})
        result = orig(self, *args, **kwargs)
        after = managers()
        record({"at": now(), "event": "set_ai_models", "when": "after",
                "managers": after,
                "pipe": type_of(frames.attr(sys.modules.get(after[0]),
                                            "txt2img_pipe", None))
                        if after else None})
        return result

    # --------------------------------------------------------------------- 本体

    install_observer()
    inventory("apply")
    attach("apply")

    def start_poll():
        """設定を切り替えた後に現れた一族にも当て直す（TECH.md §3.6.1）。"""
        try:
            from kivy.clock import Clock
        except Exception:
            ctx.log("image generation probe: no kivy Clock; polling disabled",
                    level="WARN")
            return

        def poll(_dt):
            if ctx.superseded():
                return False
            try:
                modules = set(related_modules())
                if modules != seen["modules"]:
                    fresh = sorted(modules - seen["modules"])
                    write("new module(s): {}".format(", ".join(fresh) or "(none)"))
                    inventory("new modules")
                    if attach("new modules"):
                        ctx.refresh_status()
            except Exception:
                ctx.log_exc("image generation probe: poll failed")
            return True

        Clock.schedule_interval(poll, POLL_SECONDS)

    ctx.on_ready(start_poll,
                 key="230_probe_image_generation:poll:{}".format(ctx.generation))
