"""未解消論点の解消判定（SPEC 5.5）。"""

from __future__ import annotations

from collections.abc import Iterable

from .schema import OpenQuestion, Scenario


def is_resolved(question: OpenQuestion, obtained: Iterable[str]) -> bool:
    got = set(obtained)
    needed = set(question.resolved_by)
    if question.resolution_mode == "all":
        return needed <= got
    return bool(needed & got)


def resolved_ids(scenario: Scenario, obtained: Iterable[str]) -> list[str]:
    got = set(obtained)
    return [q.id for q in scenario.open_questions if is_resolved(q, got)]


def unresolved_critical(scenario: Scenario, obtained: Iterable[str]) -> list[str]:
    got = set(obtained)
    return [
        q.id
        for q in scenario.open_questions
        if q.critical and not is_resolved(q, got)
    ]
