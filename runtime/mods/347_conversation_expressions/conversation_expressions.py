# -*- coding: utf-8 -*-
r"""会話の相手の表情を、台詞に合わせて変える（DOC.md）。

会話の返事（`conversation_facilitator` / `_after_retrieval` / `_in_quest`）の返却型に表情の欄
`expression`（ローダの窓口の表情の一覧から1つ）を足し、【返答の要素】（`response_prompt_instruction`）に表情の説明を1行足す。
LLM が台詞を書いた後に選んだ表情の差分を、会話の立ち絵（`hud.character_image`）に出す。

    返事のプロンプトを組む直前   modnpc の prompt で説明を足し、相手の id をスレッドに控える
    送り口（send_request）    返却型を、表情の欄を足した子クラスに替えて送る（`342_` と同じ手口）
    返ってきた後             表情を控え、次のフレームで立ち絵を差分へ替える

- 差分は `343_` が描く。この MOD はローダの窓口 `expressions` に「表情を見せる」と宣言するだけで、
  `343_` はそれを見て、新しく描いた立ち絵に続けて差分を描く。差分の無い人物はゲームの立ち絵のまま
- 第一声（`conversation_starter`）は型の無い送り口を通るので表情を選べない。ふだんの顔（neutral）にする
- 台詞が空（自由行動の GM へ渡った）返事でも、LLM が返した表情をそのまま使う
- ゲームが立ち絵を出し直したとき（`update_character_image`）は、控えた表情を当て直す
- 控えは会話の始めと終わりに忘れる（前の会話の表情を、次の会話やパーティー会話の立ち絵に当て直さない）
- 足した欄はゲームが読まないので、会話のふるまいは変わらない

ローカル LLM は型の description を読まない（ゲームは型の説明をプロンプトに書かない）ので、
説明は【返答の要素】の地の文に置く（VERIFICATION.md §3.132）。
"""
import os
import sys
import threading

from instantale_modloader import expressions, frames, llm, modnpc, ui

LOG_BASENAME = "conversation_expressions.log"

#: 窓口 `expressions` と modnpc に名乗る名前。
OWNER = "347_conversation_expressions"

#: 返却型の送り口。「開始する」のたびに置き直されるので `llm.watch_aliases` で包む（TECH.md §3.4）。
SEND_TARGETS = ("scripts.llm.llm_manager:send_request",)

#: 表情を選ばせる返事。
SITES = ("conversation_facilitator", "conversation_facilitator_after_retrieval",
         "conversation_facilitator_in_quest")

#: 会話の第一声。ここでふだんの顔に戻す。
STARTERS = ("conversation_starter", "conversation_starter_in_quest")

#: ゲームが会話の相手の立ち絵を出す関数。出し直した後に控えた表情を当て直す。
IMAGE_TARGET = "__main__:InstantaleApp.update_character_image"

#: 会話の立ち絵の部品（HUD の属性。GAME.md §2.5）。
PORTRAIT_WIDGET = "character_image"

#: 表情の説明（型の description と【返答の要素】の1行）の頭。続く表情の一覧の説明はローダの窓口のもの。
#: 一覧は `343_` の追加の表情で変わるので、プロンプトを組むたびに `guide` / `guide_line` で組む。
GUIDE_HEAD = "この返答を言うときの話し手の表情。"
GUIDE_LINE_HEAD = "- 表情: あわせて、返答文を言うときのあなたの表情(expression)を出力する。"

#: 基本表情の一覧での説明（表情の一覧を登録した MOD が無いときと同じ）。
GUIDE = GUIDE_HEAD + expressions.GUIDE
GUIDE_LINE = GUIDE_LINE_HEAD + expressions.GUIDE


def guide():
    """型の description（今の表情の一覧から組む）。"""
    return GUIDE_HEAD + expressions.guide()


def guide_line():
    """【返答の要素】に足す1行（今の表情の一覧から組む）。"""
    return GUIDE_LINE_HEAD + expressions.guide()

#: いま見せている表情（`sys` の属性。注入し直しても続きが分かる）。
SHOWN_ATTR = "_mod347_shown_expression"

#: 返事のプロンプトを組んだスレッドの、相手の id（同じスレッドで送り口が呼ばれる）。
_LOCAL = threading.local()


def shown():
    got = getattr(sys, SHOWN_ATTR, None)
    if not isinstance(got, dict):
        got = {"npc": None, "expression": expressions.NEUTRAL}
        setattr(sys, SHOWN_ATTR, got)
    return got


def expression_of(answer):
    """返事の表情。読めない・一覧に無いならふだんの顔（`expressions.name_of`）。"""
    got = llm.as_dict(answer)
    return expressions.name_of(got.get("expression") if isinstance(got, dict) else None)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="expressions:")
    screen = ui.Screen(ctx, write, tag="conversation expressions")
    # 広げた型は強く持つ（弱参照では、ほかに持ち手の無い子クラスがすぐ消えて毎回作り直す）。
    # 返却型はゲームのモジュールの定数で数が少ないので、増え続けない。表情の一覧が変われば説明ごと作り直す
    extended = {}
    seen = set()

    expressions.want(OWNER, ctx)

    def widen(structure, description=None):
        """返却型に表情の欄を足した子クラス。足せなければ None。

        `description` は欄の説明（省けば今の一覧から組む）。説明が前と違えば作り直す。

        欄は `str` で受け、一覧に照らすのは受け取った側（`expression_of`）。型で縛ると、一覧に無い値で
        返事ごとゲームの検証に落ちて台詞が出なくなる（`404_` も同じ）。
        """
        description = guide() if description is None else description
        got = extended.get(structure)
        if got is not None and got[0] == description:
            return got[1]
        pydantic_module = sys.modules.get("pydantic")
        fields = getattr(structure, "model_fields", None)
        if pydantic_module is None or not isinstance(fields, dict) or "expression" in fields:
            return None
        got = pydantic_module.create_model(
            structure.__name__, __base__=structure,
            expression=(str, pydantic_module.Field(..., description=description)))
        extended[structure] = (description, got)
        return got

    # ------------------------------------------------------------ 見せる

    def show(npc_id, expression):
        """会話の立ち絵を、その人物の表情の差分（ふだんの顔ならゲームの立ち絵）に替える。メインスレッドで呼ぶ。

        部品がほかの人物の絵を出しているときは触らない。
        """
        app = ui.find_app()
        hud = ui.find_hud(app) if app is not None else None
        widget = frames.attr(hud, PORTRAIT_WIDGET, None)
        base = expressions.portrait_path(ui.character_of(app, npc_id)) if app is not None else None
        if widget is None or not base:
            return
        shown_path = expressions.show(widget, os.path.dirname(base), expression, base)
        if shown_path:
            write("{} ({}): {}".format(expression, npc_id, os.path.basename(shown_path)))

    def remember(npc_id, expression):
        """表情を控え、次のフレームで見せる。"""
        state = shown()
        state["npc"], state["expression"] = str(npc_id), expression
        screen.schedule(lambda: show(str(npc_id), expression), 0)

    @ctx.wrap(IMAGE_TARGET, required=False, safe=True)
    def update_character_image(orig, self, character_id, *args, **kwargs):
        result = orig(self, character_id, *args, **kwargs)
        state = shown()
        if state["npc"] is not None and state["npc"] == str(character_id) \
                and state["expression"] != expressions.NEUTRAL:
            expression = state["expression"]
            screen.schedule(lambda: show(str(character_id), expression), 0)
        return result

    # ------------------------------------------------------------ 選ばせる

    def on_prompt(info):
        """プロンプトを組む直前（ローダの `modnpc` の `prompt`）。

        第一声ならふだんの顔に戻す。返事なら【返答の要素】に表情の説明を足し、相手の id を控える。
        `response_prompt_instruction` は引数の写しなので、ゲームの会話履歴には残らない。
        """
        site, args = info.get("site"), info.get("args")
        npc_id = str(info.get("npc_id") or "")
        if not npc_id or not isinstance(args, dict):
            return
        if site in STARTERS:
            remember(npc_id, expressions.NEUTRAL)
            return
        if site not in SITES:
            return
        text = args.get("response_prompt_instruction")
        if isinstance(text, str):
            args["response_prompt_instruction"] = text.rstrip() + "\n" + guide_line()
        _LOCAL.pending = (site, npc_id)

    def forget(_info=None):
        """会話の始め（`ConversationStartManager`）と終わり（要約の後）に、控えた表情を忘れる。

        忘れないと、会話の画面を組むときの `update_character_image` で前の会話の表情を当て直す。
        パーティー会話（`404_`）は返事の送り口を通らないので控えが変わらず、`404_` の付けた表情を上書きする。
        """
        state = shown()
        state["npc"], state["expression"] = None, expressions.NEUTRAL

    modnpc.install(ctx, write=write)
    modnpc.register(OWNER, modnpc.ANY, prompt=on_prompt,
                    on={"conversation_start": forget, "conversation_end": forget}, write=write)

    def install(target):
        @ctx.wrap(target, required=False, safe=True)
        def send(orig, *args, **kwargs):
            manager_name = frames.arg(args, kwargs, "manager_name", 0)
            if manager_name not in SITES:
                return orig(*args, **kwargs)
            # 控えは1回の送信で使い切る（プロンプトを組まなかった返事に、前の相手の id を使わない）
            pending = getattr(_LOCAL, "pending", None)
            _LOCAL.pending = None
            if not pending or pending[0] != manager_name:
                return orig(*args, **kwargs)
            npc_id = pending[1]
            if "structure" in kwargs:
                structure, where = kwargs["structure"], "kw"
            elif len(args) >= 3:
                structure, where = args[2], "pos"
            else:
                return orig(*args, **kwargs)
            widened = widen(structure) if isinstance(structure, type) else None
            if widened is None:
                write("WARN {}: the response type was not widened ({!r})".format(manager_name, structure))
                return orig(*args, **kwargs)
            if manager_name not in seen:
                seen.add(manager_name)
                write("{}: widened {} with expression".format(manager_name, structure.__name__))
            if where == "kw":
                kwargs = dict(kwargs, structure=widened)
            else:
                args = args[:2] + (widened,) + tuple(args[3:])
            answer = orig(*args, **kwargs)
            remember(npc_id, expression_of(answer))
            return answer

    llm.watch_aliases(ctx, list(SEND_TARGETS), install, label="conversation expressions")
    write("ready")
