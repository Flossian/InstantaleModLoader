# -*- coding: utf-8 -*-
"""どの MOD がどこへパッチを当てたかの台帳。

`patch.py` は「当てる」ことだけを担い、こちらが「誰が何処に当てたか」を持つ。
分けてあるのは責務の都合だけではなく、
依存の向きを一方通行にするため（patch.py → patch_registry.py の片道。
逆向きの import は無い）。

台帳が要るのは、このローダが**同じ対象に複数の MOD を意図的に重ねる**から。
ファイル名順の適用はそれ自体が設計で、
例えば BGM 系（`104_` / `106_` / `207_`）やアイテム詳細（`109_` /
`208_`）は同じ関数を別々の MOD が触る。
重なること自体は正常だが、**意図しない重なり**が起きたとき、
ログを目で追う以外の手段が無かった。

記録するのは4種類:

    applied     実際に当たった（wrap / patch）
    deferred    対象のモジュールが未 import なので見送った（後で当て直す）
    skipped     待つのをやめた保留（その経路をこの実行では通らないと分かった）
    unresolved  モジュールは在るが対象が見つからない / None だった

`unresolved` はゲーム更新で関数が消えた場合に出る。
`required=True` なら例外になって apply が止まるが、
その場合も**止まる前にここへ記録してある**ので、boot の最後にまとめて報告できる。

台帳は boot ごとに作り直す（`reset`）。
前回の boot の帰属を残すと、遅延再適用のたびに一覧が二重三重になるため。
"""

from __future__ import annotations

# 今 apply() を実行中の MOD。
# boot() が MOD ごとに出し入れする。
# patch.py は「誰が呼んだか」を知らないので、ここを見て帰属を決める。
_current: str | None = None

# 記録の本体。
# (種類, 対象, MOD, 補足) の並び。
# 適用順がそのまま並び順になる。
_entries: list[tuple[str, str, str, str]] = []

APPLIED = "applied"
DEFERRED = "deferred"
SKIPPED = "skipped"
UNRESOLVED = "unresolved"


def reset() -> None:
    """boot の開始時に台帳を空にする。"""
    global _current
    _current = None
    _entries.clear()


def begin_mod(name: str) -> None:
    """この MOD の apply() に入る。以降の記録はこの名前に帰属する。"""
    global _current
    _current = name


def end_mod() -> None:
    """apply() を抜ける。

    必ず finally から呼ぶこと。
    apply が例外で抜けたときに前の MOD の名前が残っていると、
    次に記録されたパッチが**無関係な MOD のもの**として台帳に載る。
    """
    global _current
    _current = None


def record(kind: str, target: str, *, detail: str = "") -> None:
    """1件記録する。呼び出し元は patch.py。

    `_current` が None なのは、MOD の apply() の外から当てられた場合（遅延再適用の途中や、
    ローダ自身が当てた場合）。
    捨てずに "<loader>" として残す。
    台帳の目的は「取りこぼしを無くすこと」なので、帰属不明でも載せる。
    """
    _entries.append((kind, target, _current or "<loader>", detail))


def settle_deferred(modules, detail: str = "") -> int:
    """このモジュール宛ての保留を「待たない」（`skipped`）へ移す。戻り値は移した件数。

    `deferred` は「まだ来ていないだけ」＝いずれ当たる見込みの印なので、
    見込みが無くなったら降ろす。
    見込みの有無はローダ側の判断（クラウド実行と分かった・見張りが時間切れになった）なので、
    判定そのものはここには置かない。

    **消さずに残す**のが要点。
    消すと台帳の合計が合わなくなり、その
    MOD のフックがどこへ行ったのかを追えなくなる。
    種類だけ替えて理由を `detail` に書き足せば、
    `status.json` からも「待っているのか・降ろしたのか」が読める。

    呼ぶのは見張りのスレッド（`__init__._deferred_loop`）。
    触るのは要素の差し替えだけで、並びの長さは変えない。
    """
    wanted = set(modules)
    moved = 0
    for index, (kind, target, mod, module) in enumerate(_entries):
        if kind != DEFERRED or module not in wanted:
            continue
        # 元の detail（モジュール名）は捨てない。
        # 理由で上書きすると、どのモジュールを待っていたのかが台帳から読めなくなる。
        _entries[index] = (SKIPPED, target, mod,
                           "{} ({})".format(module, detail) if detail else module)
        moved += 1
    return moved


# --------------------------------------------------------------------------
# 参照
# --------------------------------------------------------------------------
def entries(kind: str | None = None) -> list[tuple[str, str, str, str]]:
    """記録の一覧。kind を渡すとその種類だけ。"""
    if kind is None:
        return list(_entries)
    return [e for e in _entries if e[0] == kind]


def by_target() -> dict[str, list[str]]:
    """対象 → その対象に当てた MOD の一覧（適用順、重複は畳む）。

    レビューで言う「Character.update ← FixInventory / UIEnhancer / DebugTool」の形。
    """
    table: dict[str, list[str]] = {}
    for kind, target, mod, _detail in _entries:
        if kind != APPLIED:
            continue
        mods = table.setdefault(target, [])
        if mod not in mods:      # 同じ MOD が同じ対象を2回触っても1回として数える
            mods.append(mod)
    return table


def by_mod() -> dict[str, list[str]]:
    """MOD -> その MOD が当てた対象の一覧（適用順、重複は畳む）。

    `by_target` の逆引き。
    GUI で MOD を1つ選んだときに「この MOD は何処を触っているか」を出すのはこちら。
    台帳を持つ意味の半分はこの向きにある。
    """
    table: dict[str, list[str]] = {}
    for kind, target, mod, _detail in _entries:
        if kind != APPLIED:
            continue
        targets = table.setdefault(mod, [])
        if target not in targets:
            targets.append(target)
    return table


def conflicts() -> dict[str, list[str]]:
    """2つ以上の MOD が触っている対象だけ。

    「壊れている」ではなく「重なっている」の一覧。
    重ねるのは正常な使い方なので警告ではなく報告として出す（判断するのは
    MOD 作者）。
    """
    return {t: mods for t, mods in by_target().items() if len(mods) > 1}


def unresolved() -> list[tuple[str, str, str, str]]:
    """解決できなかった対象。ゲーム更新で関数が消えたときにここへ出る。"""
    return entries(UNRESOLVED)


def summary() -> dict:
    """台帳の中身をまとめて返す。GUI や外からの問い合わせ用。

    `format_report` が「人が読む行」なのに対し、こちらは**データのまま**返す。
    表示の都合（並び順・言い回し・折り返し）は受け取った側で決められるように、
    ここでは整形しない。
    """
    return {
        "counts": {
            "applied": len(entries(APPLIED)),
            "targets": len(by_target()),
            "mods": len(by_mod()),
            "conflicts": len(conflicts()),
            "deferred": len(entries(DEFERRED)),
            "skipped": len(entries(SKIPPED)),
            "unresolved": len(unresolved()),
        },
        "by_target": by_target(),
        "by_mod": by_mod(),
        "conflicts": conflicts(),
        "deferred": entries(DEFERRED),
        "skipped": entries(SKIPPED),
        "unresolved": unresolved(),
    }


# --------------------------------------------------------------------------
# 報告
# --------------------------------------------------------------------------
def format_report() -> list[str]:
    """boot の最後にログへ流す行の並び。空の節は出さない。"""
    lines: list[str] = []

    applied = entries(APPLIED)
    lines.append("patches: {} applied on {} target(s) by {} mod(s)".format(
        len(applied), len(by_target()),
        len({mod for _k, _t, mod, _d in applied})))

    overlap = conflicts()
    if overlap:
        lines.append("overlapping targets ({}):".format(len(overlap)))
        for target in sorted(overlap):
            lines.append("  {} <- {}".format(target, ", ".join(overlap[target])))

    deferred = entries(DEFERRED)
    if deferred:
        # 対象ごとではなくモジュールごとにまとめる。
        # 見張りが待つ単位がモジュールなので。
        lines.append("deferred ({}): waiting for the module to be imported".format(
            len(deferred)))
        for _k, target, mod, detail in deferred:
            lines.append("  {} ({}) <- {}".format(target, detail or "?", mod))

    skipped = entries(SKIPPED)
    if skipped:
        # 待たないと決めたもの。
        # ゲーム更新を疑う話ではないので大文字にしない（ログを目で追ったときに
        # UNRESOLVED と同じ重さに読ませない）。
        lines.append("skipped ({}): the module is not used in this run".format(
            len(skipped)))
        for _k, target, mod, detail in skipped:
            lines.append("  {} ({}) <- {}".format(target, detail or "?", mod))

    missing = unresolved()
    if missing:
        # ここが空でないなら、ゲーム側が変わった可能性を最初に疑う。
        lines.append("UNRESOLVED ({}): target not found in the running build".format(
            len(missing)))
        for _k, target, mod, detail in missing:
            lines.append("  {} <- {} ({})".format(target, mod, detail or "?"))

    return lines
