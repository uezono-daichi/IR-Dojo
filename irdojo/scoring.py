"""三層採点（SPEC Part 6）。

事実認識層は方針に依存しない。方針適合層は方針の重みと制約で評価する。
帰結は採点せず、方針の重みを通してのみ評価に入る（SPEC 原則3）。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from .engine import GameState
from .schema import Policy, Scenario


def safe_ratio(num: float, den: float, default: float = 0.0) -> float:
    """分母ゼロの扱いは SPEC 6.7 に従う。"""
    return num / den if den else default


# ─────────── 帰結指標（SPEC 6.3） ───────────


class Consequences(BaseModel):
    elapsed_minutes: int
    total_damage: float
    business_impact: float
    evidence_preserved: float
    containment_completeness: float


def consequences(state: GameState, scenario: Scenario) -> Consequences:
    truth = scenario.world.ground_truth

    # 取り損ねた証拠の割合。分母はシナリオ内の全証拠数。
    # 取得数を分母にすると「調べないほど保全した」ことになる。
    #
    # 数えるのは「取得する前に失った」分だけ。取得済みのものを後で
    # 破壊しても、証拠としては手元に残る。証拠保全とは
    # **失われる前に取れたか**であって、破壊を避けたかではない。
    total_evidence = len(scenario.evidence)
    preserved = 1.0 - safe_ratio(len(state.lost_evidence), total_evidence)

    contained = set(state.contained_at)
    compromised = set(truth.compromised)
    completeness = safe_ratio(len(contained & compromised), len(compromised))

    horizon = scenario.scoring.consequence_layer.business_impact_horizon_minutes
    assets = scenario.asset_by_id
    impact = 0.0
    for asset_id, entered in state.contained_at.items():
        asset = assets.get(asset_id)
        if asset is None:
            continue
        minutes = max(0, (state.elapsed_minutes + horizon) - entered)
        impact += asset.business_impact_per_hour * minutes / 60.0

    return Consequences(
        elapsed_minutes=state.elapsed_minutes,
        total_damage=state.accumulated_damage,
        business_impact=impact,
        evidence_preserved=preserved,
        containment_completeness=completeness,
    )


# ─────────── 事実認識層の指標（SPEC 6.2） ───────────

MetricFn = Callable[[GameState, Scenario], float]
_METRICS: dict[str, MetricFn] = {}


def metric(name: str) -> Callable[[MetricFn], MetricFn]:
    """指標は関数として名前で登録する（SPEC 原則4 拡張点4）。"""

    def wrap(fn: MetricFn) -> MetricFn:
        _METRICS[name] = fn
        return fn

    return wrap


def _assessed(state: GameState) -> set[str]:
    return set(state.assessment)


@metric("assessment_precision")
def _precision(state: GameState, scenario: Scenario) -> float:
    a = _assessed(state)
    hit = a & set(scenario.world.ground_truth.compromised)
    # 判断を放棄した場合は 0。誤った判断より良くはない（SPEC 6.7）
    return safe_ratio(len(hit), len(a))


@metric("assessment_recall")
def _recall(state: GameState, scenario: Scenario) -> float:
    compromised = set(scenario.world.ground_truth.compromised)
    hit = _assessed(state) & compromised
    return safe_ratio(len(hit), len(compromised))


@metric("persistence_missed")
def _persistence_missed(state: GameState, scenario: Scenario) -> float:
    persistence = set(scenario.world.ground_truth.persistence)
    missed = persistence - _assessed(state)
    return safe_ratio(len(missed), len(persistence))


@metric("misled_score")
def _misled(state: GameState, scenario: Scenario) -> float:
    """行動ベースと配分ベースのハイブリッド（SPEC 6.2）。

    誤導証拠にアクセスすること自体は減点しない。測るのは
    「取得後にどれだけ追いかけたか」と「最終判断に混ぜたか」。
    """
    action_part = safe_ratio(state.misled_follow_minutes, state.investigation_minutes)

    by_id = scenario.evidence_by_id
    obtained = [by_id[e] for e in state.obtained_evidence if e in by_id]
    misled_only = 0
    for asset_id in _assessed(state):
        pointing = [e for e in obtained if asset_id in e.points_to]
        if pointing and all(e.misleading for e in pointing):
            misled_only += 1
    allocation_part = safe_ratio(misled_only, len(_assessed(state)))

    return 0.4 * action_part + 0.6 * allocation_part


@metric("unresolved_questions")
def _unresolved(state: GameState, scenario: Scenario) -> float:
    """宣言時点で測る。対応フェーズ中に解消しても遡って加点しない。"""
    critical_total = sum(1 for q in scenario.open_questions if q.critical)
    snap = state.assessment_snapshot
    unresolved = len(snap.unresolved_critical) if snap else critical_total
    return safe_ratio(unresolved, critical_total)


def known_metrics() -> set[str]:
    return set(_METRICS)


# ─────────── 合成（SPEC 6.4） ───────────


class ScoreReport(BaseModel):
    fact_score: float
    policy_score: float
    composite_score: float
    metrics: dict[str, float]        # 事実認識層の生値
    goodness: dict[str, float]       # 0〜1 に正規化した「良さ」
    consequences: Consequences
    consequence_goodness: dict[str, float]
    constraint_violations: int
    policy_id: str
    policy_label: str


def score(state: GameState, scenario: Scenario, policy: Policy | None = None) -> ScoreReport:
    pol = policy or scenario.policy_by_id[state.policy_id]
    sc = scenario.scoring

    # 事実認識層
    raw: dict[str, float] = {}
    good: dict[str, float] = {}
    for spec in sc.fact_layer.metrics:
        fn = _METRICS.get(spec.id)
        if fn is None:
            raise ValueError(f"未登録の指標: {spec.id}")
        value = fn(state, scenario)
        # weight_multiplier は指標値そのものに掛かる（SPEC 6.2 の persistence_missed）
        value = min(1.0, value * spec.weight_multiplier)
        raw[spec.id] = value
        good[spec.id] = value if spec.direction == "higher_is_better" else 1.0 - value

    fact_score = sum(sc.fact_layer.weights[k] * good[k] for k in sc.fact_layer.weights)

    # 帰結指標を正規化（採点しない。方針の重みを通してのみ入る）
    cons = consequences(state, scenario)
    cons_good: dict[str, float] = {}
    for key, bounds in sc.consequence_layer.normalization.items():
        actual = getattr(cons, key, None)
        if actual is None:
            continue
        span = bounds.best - bounds.worst
        cons_good[key] = _clamp((actual - bounds.worst) / span, 0.0, 1.0)

    # 方針適合層
    weighted = sum(w * cons_good.get(k, 0.0) for k, w in pol.weights.items())
    # 違反はその方針の制約で数える。別方針で採点し直すときも
    # その方針の制約が適用される（SPEC 6.8）。
    violations = len(state.violations_by_policy.get(pol.id, []))
    penalty = 1.0 - sc.policy_layer.constraint_penalty * violations
    policy_score = _clamp(weighted * penalty, 0.0, 1.0)

    # 総合
    if sc.composite.method == "multiplicative":
        composite = fact_score * policy_score
    else:
        fw = sc.composite.fact_weight
        composite = fw * fact_score + (1.0 - fw) * policy_score

    return ScoreReport(
        fact_score=fact_score,
        policy_score=policy_score,
        composite_score=composite,
        metrics=raw,
        goodness=good,
        consequences=cons,
        consequence_goodness=cons_good,
        constraint_violations=violations,
        policy_id=pol.id,
        policy_label=pol.label,
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
