"""方針制約の判定（SPEC 5.3）。

制約違反は減点であって禁止ではない。実行は止めず、その場で通知もしない。
講評で初めて開示する（SPEC 3.7 / 原則2）。

制約の型は名前で登録する（SPEC 原則4 拡張点4）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .schema import Action, ActionType, Constraint, Policy, Scenario


@dataclass(frozen=True)
class ConstraintContext:
    """制約判定に必要な情報。エンジンの内部状態は渡さない。"""

    scenario: Scenario
    action: Action
    obtained_evidence: frozenset[str]
    executed_actions: tuple[str, ...]
    at_minute: int
    business_impact_after: float


Checker = Callable[[Constraint, ConstraintContext], bool]

_PRE: dict[str, Checker] = {}   # アクション実行前の状態で判定する
_POST: dict[str, Checker] = {}  # 実行後の状態で判定する

# 一度だけ記録すればよい制約（状態が閾値を跨いだ瞬間に1件）
_ONCE = {"max_business_impact"}


def register(name: str, *, post: bool = False) -> Callable[[Checker], Checker]:
    def wrap(fn: Checker) -> Checker:
        (_POST if post else _PRE)[name] = fn
        return fn

    return wrap


@register("forbid_containment_without_evidence")
def _forbid_containment_without_evidence(c: Constraint, ctx: ConstraintContext) -> bool:
    """指定資産を、それを示す証拠なしに封じ込めたら違反。

    「示す証拠」は取得済み証拠のうち points_to にその資産を含むもの。
    誤導証拠も数える。学習者には誤導かどうか分からないため、
    ここで区別すると「システムが正解を教えている」ことになる。
    """
    if ctx.action.type != ActionType.CONTAIN:
        return False
    guarded = set(c.assets) & set(ctx.action.targets)
    if not guarded:
        return False
    by_id = ctx.scenario.evidence_by_id
    supported = {
        a
        for eid in ctx.obtained_evidence
        for a in by_id[eid].points_to
        if eid in by_id
    }
    return bool(guarded - supported)


@register("require_before")
def _require_before(c: Constraint, ctx: ConstraintContext) -> bool:
    """指定タイプのアクションの前に、前提タグ付きアクションが必要。"""
    if c.action_type is None or ctx.action.type != c.action_type:
        return False
    if not c.prerequisite_tags:
        return False
    wanted = set(c.prerequisite_tags)
    by_id = ctx.scenario.action_by_id
    for aid in ctx.executed_actions:
        prior = by_id.get(aid)
        if prior is not None and wanted & set(prior.tags):
            return False
    return True


@register("require_capture_of_target")
def _require_capture_of_target(c: Constraint, ctx: ConstraintContext) -> bool:
    """封じ込める資産ごとに、**その資産の**揮発性証拠を先に取っていること。

    `require_before` は前提タグの付いた手を**盤面のどこか1つ**押していれば
    満たされる。ws-042 のメモリを取った学習者は、それだけで fs01 を
    無条件に落とせた — **事実上ほぼ無料の制約**で、証拠保全最優先は
    「どの手を選ぶか」に一度も効いていなかった（v1.40）。

    ここが効く相手は `evidence_preserved` ではない。取った後で壊しても
    証拠は手元に残るので（6.3）、重みをいくら上げても封じ込めの選択は
    動かない。動かせるのは制約だけである（5.10 の費用の型）。

    `prerequisite_tags` が空、または `action_type` が無い制約は
    永久に発火しない。ローダが拒否する（5.3）。
    """
    if c.action_type is None or ctx.action.type != c.action_type:
        return False
    if not c.prerequisite_tags:
        return False
    wanted = set(c.prerequisite_tags)
    by_id = ctx.scenario.action_by_id
    captured: set[str] = set()
    for aid in ctx.executed_actions:
        prior = by_id.get(aid)
        if prior is not None and wanted & set(prior.tags):
            captured |= set(prior.investigates)
    return bool(set(ctx.action.targets) - captured)


@register("forbid_action")
def _forbid_action(c: Constraint, ctx: ConstraintContext) -> bool:
    return ctx.action.id in set(c.action_ids)


@register("max_business_impact", post=True)
def _max_business_impact(c: Constraint, ctx: ConstraintContext) -> bool:
    if c.threshold is None:
        return False
    return ctx.business_impact_after > c.threshold


# 型ごとに「これが無いと永久に発火しない」フィールド（SPEC 5.3）。
# **黙って無視される制約は、書いていないのと同じである。** 方針の名乗りだけが
# 残り、盤面では何も起きない。ローダがここを見て拒否する。
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "forbid_containment_without_evidence": ("assets",),
    "require_before": ("action_type", "prerequisite_tags"),
    "require_capture_of_target": ("action_type", "prerequisite_tags"),
    "forbid_action": ("action_ids",),
    "max_business_impact": ("threshold",),
}


def required_fields(name: str) -> tuple[str, ...]:
    """その制約型が必ず持っていなければならないフィールド名。"""
    return REQUIRED_FIELDS.get(name, ())


def evaluate(
    policy: Policy,
    ctx: ConstraintContext,
    already_recorded: frozenset[str],
) -> list[Constraint]:
    """違反した制約を返す。

    `already_recorded` は記録済みの「一度きり」制約の型名。
    max_business_impact のような状態依存の制約が
    以降のアクション全部で再発火するのを防ぐ。
    """
    hits: list[Constraint] = []
    for c in policy.constraints:
        checker = _PRE.get(c.type) or _POST.get(c.type)
        if checker is None:
            # 未知の型は黙って無視する。エンジンが知らない拡張型を
            # 書いたシナリオでも、演習自体は成立させる。
            continue
        if c.type in _ONCE and c.type in already_recorded:
            continue
        if checker(c, ctx):
            hits.append(c)
    return hits


def known_types() -> set[str]:
    return set(_PRE) | set(_POST)
