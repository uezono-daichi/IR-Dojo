"""事後参照値と反実仮想（SPEC 6.6）。

最良解は提示しない。最短経路は disclaimer 付きの参照値に留め、
主役は誤導の棄却経路と未解消論点の含意 — つまり推論の穴の指摘に置く。
"""

from __future__ import annotations

from pydantic import BaseModel

from .engine import GameState
from .questions import is_resolved
from .schema import Action, ActionType, Scenario

# 全探索の上限。これを超えたら貪欲法に落として近似であることを明示する。
EXHAUSTIVE_LIMIT = 22


class MinimalPath(BaseModel):
    minutes: int | None
    action_ids: list[str]
    exact: bool           # False なら貪欲法による上界
    label: str
    disclaimer: str


class Counterfactual(BaseModel):
    asset_id: str
    asset_label: str
    evidence_id: str
    evidence_summary: str
    explanation: str
    refuting_evidence: list[str]      # 取得していれば棄却できた証拠（未取得）
    refuting_summaries: list[str]
    obtainable_by: list[str]          # その証拠を得られたアクションのラベル
    refuting_obtained: list[str]      # 取得済みだったのに反映されなかった証拠
    refuting_obtained_summaries: list[str]
    # any = どれか1つで棄却できた / all = すべて揃って初めて棄却できた。
    # **講評の言い回しが変わる。** all の誤導で片方だけ持っていた人に
    # 「棄却の材料は手元にありました」と言うのは事実に反する
    refutation_mode: str = "any"
    # 宣言の時点の手元で、この誤導を棄却できたか。
    # **画面に判定を書かせない。** 「未取得が残っているか」で画面が判断すると、
    # any の誤導で候補が2つあるとき（1つ持っていれば足りる）に
    # 「これだけでは足りませんでした」と出る。条件を知っているのは
    # Evidence.is_refuted だけなので、結論はこちらで出して渡す
    refuted: bool = False


class UnresolvedItem(BaseModel):
    id: str
    label: str
    question: str
    critical: bool
    resolved_at_decision: bool
    implication: str
    resolved_later: bool


class Retro(BaseModel):
    minimal_path: MinimalPath | None
    counterfactuals: list[Counterfactual]
    unresolved_review: list[UnresolvedItem]
    # 誤導証拠が指す先を追いかけた時間。**採点には入らない。**
    # v1.36 まで misled_score の行動成分としてこれを割っていたが、
    # 分母（総調査時間）が学習者の裁量で伸びる比だったので、
    # よく調べた人ほど同じ寄り道が薄まった。外れを引くのは調査の
    # 必然であって罰する対象ではない（SPEC 6.6 / 3.8）。
    # ただし「どれだけ費やしたか」は本人に返す価値があるので、
    # 採点から降ろして講評の平文に回した
    misled_follow_minutes: int = 0
    investigation_minutes: int = 0


# ─────────── minimal_path（SPEC 6.6） ───────────


def _requirements_met(scenario: Scenario, evidence: set[str]) -> bool:
    """その証拠集合で、侵害資産の特定と critical 論点の解消が成立するか。"""
    by_id = scenario.evidence_by_id
    for asset_id in scenario.world.ground_truth.compromised:
        ok = any(
            asset_id in by_id[e].points_to and not by_id[e].misleading
            for e in evidence
            if e in by_id
        )
        if not ok:
            return False
    for q in scenario.open_questions:
        if q.critical and not is_resolved(q, evidence):
            return False
    return True


def _candidates(scenario: Scenario) -> list[Action]:
    """必要になりうる証拠を1つでも産むアクションだけを候補にする。

    フェーズ制約は無視してよい。参照値であり、理論的な下限として提示する。
    """
    by_id = scenario.evidence_by_id
    needed: set[str] = set()
    for asset_id in scenario.world.ground_truth.compromised:
        needed |= {
            e.id for e in scenario.evidence
            if asset_id in e.points_to and not e.misleading
        }
    for q in scenario.open_questions:
        if q.critical:
            needed |= set(q.resolved_by)
    needed &= set(by_id)
    return [a for a in scenario.actions if set(a.yields) & needed]


def minimal_path(scenario: Scenario) -> tuple[int | None, list[str], bool]:
    """正しい被疑判定に到達する最小コストの経路を求める（集合被覆）。"""
    actions = _candidates(scenario)
    if not _requirements_met(scenario, {e for a in actions for e in a.yields}):
        # 全部やっても要件を満たせない。シナリオ側の欠陥だが算出は諦める。
        return None, [], True

    if len(actions) > EXHAUSTIVE_LIMIT:
        picked = _greedy(scenario, actions)
        return sum(a.cost_minutes for a in picked), [a.id for a in picked], False

    # コストの小さい順に並べ、暫定最良を上界とした深さ優先探索で枝を刈る
    actions.sort(key=lambda a: a.cost_minutes)
    best: dict[str, object] = {"cost": None, "ids": []}

    def dfs(i: int, chosen: list[Action], cost: int, evidence: set[str]) -> None:
        cur_best = best["cost"]
        if cur_best is not None and cost >= cur_best:  # type: ignore[operator]
            return
        if _requirements_met(scenario, evidence):
            best["cost"] = cost
            best["ids"] = [a.id for a in chosen]
            return
        if i >= len(actions):
            return
        for j in range(i, len(actions)):
            a = actions[j]
            dfs(j + 1, chosen + [a], cost + a.cost_minutes, evidence | set(a.yields))

    dfs(0, [], 0, set())
    if best["cost"] is None:
        return None, [], True
    return int(best["cost"]), list(best["ids"]), True  # type: ignore[arg-type]


def minimal_containment(scenario: Scenario) -> tuple[int | None, list[str]]:
    """`compromised` を覆い、`persistence` を根絶する最安の封じ込め集合（8.2 手順7）。

    **学習者には出さない内部値である。** 講評に出るのは 6.6 の
    `minimal_path`（判断に到達するまでの参照値）だけで、こちらは
    折れ点を置くための校正にしか使わない。混ぜると講評の文言が変わる。

    折れ点を `minimal_path` だけで校正すると、**封じ込めの時間が
    予算に入っていない**折れ点になる。被害モデルが罰したいのは
    判断の遅れではなく、被害が止まるまでの遅れである。

    「封じ込め手の合計」ではなく「compromised を覆う最安の集合」で定義する。
    合計にすると、手を1つ足すたびに折れ点が動く循環になる。

    **根絶まで含める。** 5.8 の `on_correct_containment` は
    `persistence ⊆ eradicated` も要求するので、止めるだけの集合は
    **盤面で最良の対応ではない**（被害は on_partial から下がらない）。
    L を止めるだけの集合で測ると、折れ点は「最良の対応をした人が
    自分で踏む」位置に置かれる。ここは 8.2 手順7 が一度直したのと
    同じ取り違えで、そのときは封じ込めそのものが抜けていた。
    """
    acts = [a for a in scenario.actions if a.type == ActionType.CONTAIN and a.targets]
    need = set(scenario.world.ground_truth.compromised)
    need_erad = set(scenario.world.ground_truth.persistence)
    if not need and not need_erad:
        return 0, []

    # 各資産について最も安い手を選ぶ貪欲でよい場面が多いが、
    # 1手が複数資産を覆う定義もありうるので集合被覆として解く。
    # 封じ込め手は 8.1 の上限で 5〜7 個なので全探索で足りる。
    best: tuple[int, list[str]] | None = None

    def dfs(
        i: int, chosen: list[Action], cost: int,
        covered: set[str], purged: set[str],
    ) -> None:
        nonlocal best
        if best is not None and cost >= best[0]:
            return
        if need <= covered and need_erad <= purged:
            best = (cost, [a.id for a in chosen])
            return
        if i >= len(acts):
            return
        for j in range(i, len(acts)):
            a = acts[j]
            dfs(j + 1, chosen + [a], cost + a.cost_minutes,
                covered | set(a.targets), purged | set(a.eradicates))

    dfs(0, [], 0, set(), set())
    if best is None:
        return None, []
    return best[0], best[1]


def _greedy(scenario: Scenario, actions: list[Action]) -> list[Action]:
    """大きいシナリオ用の近似。被覆できていない要件を最も安く減らす順に取る。"""
    picked: list[Action] = []
    evidence: set[str] = set()
    remaining = list(actions)
    while remaining and not _requirements_met(scenario, evidence):
        best_a = None
        best_gain = 0.0
        for a in remaining:
            gain = len(set(a.yields) - evidence) / a.cost_minutes
            if gain > best_gain:
                best_gain, best_a = gain, a
        if best_a is None:
            break
        picked.append(best_a)
        evidence |= set(best_a.yields)
        remaining.remove(best_a)
    return picked


# ─────────── counterfactuals / unresolved_review ───────────


def counterfactuals(state: GameState, scenario: Scenario) -> list[Counterfactual]:
    """誤導に依拠した被疑判定と、その棄却経路を洗い出す（SPEC 6.6）。"""
    by_id = scenario.evidence_by_id
    assets = scenario.asset_by_id
    obtained = [by_id[e] for e in state.obtained_evidence if e in by_id]
    explanations = scenario.debrief.misleading_explanations

    out: list[Counterfactual] = []
    for asset_id in state.assessment:
        pointing = [e for e in obtained if asset_id in e.points_to]
        if not pointing or not all(e.misleading for e in pointing):
            continue
        asset = assets.get(asset_id)
        for ev in pointing:
            missing = [r for r in ev.refuted_by if r not in state.obtained_evidence]
            held = [r for r in ev.refuted_by if r in state.obtained_evidence]
            out.append(
                Counterfactual(
                    asset_id=asset_id,
                    asset_label=asset.label if asset else asset_id,
                    evidence_id=ev.id,
                    evidence_summary=ev.summary,
                    explanation=explanations.get(ev.id, ""),
                    refuting_evidence=missing,
                    refuting_summaries=[
                        by_id[r].summary for r in missing if r in by_id
                    ],
                    obtainable_by=_actions_yielding(scenario, missing),
                    refuting_obtained=held,
                    refuting_obtained_summaries=[
                        by_id[r].summary for r in held if r in by_id
                    ],
                    refutation_mode=ev.refutation_mode,
                    refuted=ev.is_refuted(state.obtained_evidence),
                )
            )
    return out


def _actions_yielding(scenario: Scenario, evidence_ids: list[str]) -> list[str]:
    wanted = set(evidence_ids)
    if not wanted:
        return []
    return [a.label for a in scenario.actions if wanted & set(a.yields)]


def unresolved_review(state: GameState, scenario: Scenario) -> list[UnresolvedItem]:
    """判定時点の未解消論点をそのまま列挙し、implication を添える。"""
    snap = state.assessment_snapshot
    unresolved_then = set(snap.unresolved_critical) if snap else set()
    obtained_then = set(snap.obtained_evidence) if snap else set()
    resolved_now = set(state.resolved_questions)

    out: list[UnresolvedItem] = []
    for q in scenario.open_questions:
        was_resolved = is_resolved(q, obtained_then)
        out.append(
            UnresolvedItem(
                id=q.id,
                label=q.label,
                question=q.question,
                critical=q.critical,
                resolved_at_decision=was_resolved,
                implication=q.implication,
                resolved_later=(not was_resolved) and (q.id in resolved_now),
            )
        )
    # 未解消かつ critical を先頭に
    out.sort(key=lambda i: (i.resolved_at_decision, not i.critical))
    _ = unresolved_then
    return out


def build(state: GameState, scenario: Scenario) -> Retro:
    cfg = scenario.retrospective

    mp: MinimalPath | None = None
    if cfg.minimal_path.compute:
        minutes, ids, exact = minimal_path(scenario)
        mp = MinimalPath(
            minutes=minutes,
            action_ids=ids,
            exact=exact,
            label=cfg.minimal_path.label or "事後的に見た最短経路",
            disclaimer=cfg.minimal_path.disclaimer,
        )

    return Retro(
        minimal_path=mp,
        counterfactuals=counterfactuals(state, scenario)
        if cfg.counterfactuals.compute
        else [],
        unresolved_review=unresolved_review(state, scenario)
        if cfg.unresolved_review.compute
        else [],
        misled_follow_minutes=state.misled_follow_minutes,
        investigation_minutes=state.investigation_minutes,
    )
