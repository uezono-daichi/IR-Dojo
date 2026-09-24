"""三層採点（SPEC Part 6）。

事実認識層は方針に依存しない。方針適合層は方針の重みと制約で評価する。
帰結は採点せず、方針の重みを通してのみ評価に入る（SPEC 原則3）。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from . import damage as damage_mod
from .engine import GameState, compromised_now, effective_truth
from .schema import Policy, Scenario, required_hardening


def safe_ratio(num: float, den: float, default: float = 0.0) -> float:
    """分母ゼロの扱いは SPEC 6.7 に従う。"""
    return num / den if den else default


# ─────────── 帰結指標（SPEC 6.3） ───────────


class Consequences(BaseModel):
    elapsed_minutes: int
    # 演習中に実際に積み上がった分。プレイ画面が出しているのはこの数字
    accumulated_damage: float
    # 手を止めた時点の封じ込め状態のまま、復旧地平 H まで伸ばした分
    projected_damage: float
    total_damage: float          # 上の2つの和。方針の重みが見るのはこれ
    business_impact: float
    evidence_preserved: float
    containment_completeness: float


def _truth_at_decision(state: GameState, scenario: Scenario) -> set[str]:
    """被疑判定を突き合わせる相手（SPEC 6.2 / 5.2）。

    **宣言した時点で侵害されていた資産**である。拡散を持つ盤面では
    演習中に真実が動くので、終了時点の集合で採点すると
    「判定した時点では正しかった」が表現できない — 早く正しく判断して、
    そのあと配り元を止めそこねた学習者は、事実認識を間違えたのではなく
    **止めそこねた**のであり、それは帰結の層（完全度・被害）が測る。

    拡散の無い盤面では `compromised` と同値になる。宣言していない状態で
    採点されたときも同じ（「まだ判断していない」を読み替えない）。
    """
    return set(state.assessment_compromised or scenario.world.ground_truth.compromised)


def consequences(state: GameState, scenario: Scenario) -> Consequences:
    # 帰結の層が見るのは**終了時点の世界**である。判定の時点ではない —
    # 止まったかどうかは事実の問題であって、思い込みでは変わらない（6.3）
    truth = effective_truth(scenario, state)

    # 取り損ねた証拠の割合。分母はシナリオ内の全証拠数。
    # 取得数を分母にすると「調べないほど保全した」ことになる。
    #
    # 数えるのは「取得する前に失った」分だけ。取得済みのものを後で
    # 破壊しても、証拠としては手元に残る。証拠保全とは
    # **失われる前に取れたか**であって、破壊を避けたかではない。
    total_evidence = len(scenario.evidence)
    preserved = 1.0 - safe_ratio(len(state.lost_evidence), total_evidence)

    # 完全度が見るのは contained_at（直接止めた資産）だけ。
    # 依存連鎖を混ぜると「dc01 を落としたので ws-042 も封じ込めた」ことになり、
    # 生きている Run キーを封じ込め成立と数えることになる（SPEC 6.3）
    contained = set(state.contained_at)
    # **こちらも分母が動く**（SPEC 5.2）。演習中に配られた先は、
    # 止めるべきものとして終了時点の分母に入る
    compromised = set(compromised_now(scenario, state))
    completeness = safe_ratio(len(contained & compromised), len(compromised))

    # 業務影響が見るのは halted_at。止めたことと仕事が止まることは別の問い
    horizon = scenario.scoring.consequence_layer.business_impact_horizon_minutes
    assets = scenario.asset_by_id
    impact = 0.0
    for asset_id, entered in state.halted_at.items():
        asset = assets.get(asset_id)
        if asset is None:
            continue
        minutes = max(0, (state.elapsed_minutes + horizon) - entered)
        impact += asset.business_impact_per_hour * minutes / 60.0

    # 復旧地平（SPEC 5.8 / 6.3）。演習が終わってもインシデントは終わらない。
    # 手を止めた時点の封じ込め状態のまま H 分だけ積分を延長する。
    # business_impact と同じ H を使う — 「復旧まで」という前提は1つしかない。
    projected = sum(
        damage_mod.project(
            scenario.damage,
            truth,
            state.contained_at.keys(),
            state.eradicated_at.keys(),
            state.elapsed_minutes,
            horizon,
            # 入り口を塞いだか（SPEC 5.12）。**再発の被害は演習の外に出る** —
            # 効くのはここ、復旧地平まで伸ばした見込みの傾きである
            state.hardened_at.keys(),
            required_hardening(scenario),
        )
    )

    return Consequences(
        elapsed_minutes=state.elapsed_minutes,
        accumulated_damage=state.accumulated_damage,
        projected_damage=projected,
        total_damage=state.accumulated_damage + projected,
        business_impact=impact,
        evidence_preserved=preserved,
        containment_completeness=completeness,
    )


# ─────────── 事実認識層の指標（SPEC 6.2） ───────────

MetricFn = Callable[[GameState, Scenario], float]
_METRICS: dict[str, MetricFn] = {}


class MetricPart(BaseModel):
    """指標が「何を何で割った値か」。講評はこれを分数のまま出す。

    0.039 という小数を出しても、読み手は何を数えた値か分からない。
    「誤導を追った 25分 / 調べた 165分」なら説明文が要らない
    （SPEC 7.6.13）。分子の名前・分母の名前をそのまま持つ。
    """

    label: str        # 分子が何か（「当たり」「見落とし」）
    num: float
    den_label: str    # 分母が何か（「名指しした資産」「永続化」）
    den: float


PartsFn = Callable[[GameState, Scenario], list[MetricPart]]
_PARTS: dict[str, PartsFn] = {}


def metric(name: str) -> Callable[[MetricFn], MetricFn]:
    """指標は関数として名前で登録する（SPEC 原則4 拡張点4）。"""

    def wrap(fn: MetricFn) -> MetricFn:
        _METRICS[name] = fn
        return fn

    return wrap


def breakdown(name: str) -> Callable[[PartsFn], PartsFn]:
    """その指標の内訳を返す関数を、同じ名前で登録する。

    指標本体と同じ拡張点に乗せる。内訳が無い指標は何も出さない
    （分数で書けない指標を無理に分数にしない）。
    """

    def wrap(fn: PartsFn) -> PartsFn:
        _PARTS[name] = fn
        return fn

    return wrap


def _assessed(state: GameState) -> set[str]:
    return set(state.assessment)


@metric("assessment_precision")
def _precision(state: GameState, scenario: Scenario) -> float:
    a = _assessed(state)
    hit = a & _truth_at_decision(state, scenario)
    # 判断を放棄した場合は 0。誤った判断より良くはない（SPEC 6.7）
    return safe_ratio(len(hit), len(a))


@breakdown("assessment_precision")
def _precision_parts(state: GameState, scenario: Scenario) -> list[MetricPart]:
    a = _assessed(state)
    hit = a & _truth_at_decision(state, scenario)
    return [MetricPart(label="当たり", num=len(hit), den_label="名指し", den=len(a))]


@metric("assessment_recall")
def _recall(state: GameState, scenario: Scenario) -> float:
    # **分母が動く。** 拡散を持つ盤面では、遅く判定するほど
    # 「名指しすべきもの」が増える（SPEC 5.2 / 6.2）
    compromised = _truth_at_decision(state, scenario)
    hit = _assessed(state) & compromised
    return safe_ratio(len(hit), len(compromised))


@breakdown("assessment_recall")
def _recall_parts(state: GameState, scenario: Scenario) -> list[MetricPart]:
    compromised = _truth_at_decision(state, scenario)
    hit = _assessed(state) & compromised
    return [
        MetricPart(label="名指しできた", num=len(hit),
                   den_label="実際の侵害", den=len(compromised))
    ]


@metric("assessment_support")
def _support(state: GameState, scenario: Scenario) -> float:
    """名指しのうち、宣言時点の手元に非誤導の裏付けがあった割合（SPEC 6.2）。

    適合率・再現率は「答えと合っていたか」しか見ない。当てずっぽうで
    当てた判定と、証拠を積んで辿り着いた判定が同じ点になる。
    ここが測るのは**根拠の厚み**であって、当たり外れではない。

    数えるのは**宣言した瞬間に手元にあった**証拠だけ。あとから
    取り直しても、その判断の根拠にはなっていない（unresolved_questions と
    同じ基準点）。
    """
    a = _assessed(state)
    return safe_ratio(len(_supported(state, scenario)), len(a))


def _supported(state: GameState, scenario: Scenario) -> set[str]:
    """名指しのうち、宣言時点の手元に「その資産を指す非誤導証拠」があるもの。"""
    by_id = scenario.evidence_by_id
    snap = state.assessment_snapshot
    # 宣言していない状態で採点されたら、手元は空とみなす。
    # 「まだ判断していない」を「今の手元で判断した」に読み替えない
    held = snap.obtained_evidence if snap else []
    obtained = [by_id[e] for e in held if e in by_id]
    return {
        asset_id
        for asset_id in _assessed(state)
        if any(asset_id in e.points_to and not e.misleading for e in obtained)
    }


@breakdown("assessment_support")
def _support_parts(state: GameState, scenario: Scenario) -> list[MetricPart]:
    return [
        MetricPart(label="手元の証拠が指していた", num=len(_supported(state, scenario)),
                   den_label="名指し", den=len(_assessed(state)))
    ]


@metric("unresolved_questions")
def _unresolved(state: GameState, scenario: Scenario) -> float:
    """宣言時点で測る。対応フェーズ中に解消しても遡って加点しない。"""
    critical_total = sum(1 for q in scenario.open_questions if q.critical)
    snap = state.assessment_snapshot
    unresolved = len(snap.unresolved_critical) if snap else critical_total
    return safe_ratio(unresolved, critical_total)


@breakdown("unresolved_questions")
def _unresolved_parts(state: GameState, scenario: Scenario) -> list[MetricPart]:
    critical_total = sum(1 for q in scenario.open_questions if q.critical)
    snap = state.assessment_snapshot
    unresolved = len(snap.unresolved_critical) if snap else critical_total
    return [
        MetricPart(label="未解消", num=unresolved,
                   den_label="採点対象の論点", den=critical_total)
    ]


def known_metrics() -> set[str]:
    return set(_METRICS)


# ─────────── 合成（SPEC 6.4） ───────────


class ScoreReport(BaseModel):
    fact_score: float
    policy_score: float
    composite_score: float
    metrics: dict[str, float]        # 事実認識層の生値
    goodness: dict[str, float]       # 0〜1 に正規化した「良さ」。向きが揃っている
    # 各指標が何を何で割った値か。講評はこれを分数のまま出す（SPEC 7.6.13）
    metric_parts: dict[str, list[MetricPart]]
    fact_weights: dict[str, float]   # 事実認識層の内訳の重み
    consequences: Consequences
    consequence_goodness: dict[str, float]
    # この方針が帰結の何をどれだけ見ているか。違反ゼロでも点が低い理由は
    # ここにしか無い。出さないと「違反なしで 39点」の説明がどこにも無くなる
    policy_weights: dict[str, float]
    constraint_penalty: float        # 違反1件あたりの減衰率
    constraint_violations: int
    policy_id: str
    policy_label: str
    fact_weight: float               # 合成での事実認識層の重み
    composite_method: str


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
        # weight_multiplier は指標値そのものに掛かる（SPEC 6.2）。
        # 同梱シナリオでは現在どの指標も使っていない（既定 1.0）
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

    parts: dict[str, list[MetricPart]] = {}
    for spec in sc.fact_layer.metrics:
        pf = _PARTS.get(spec.id)
        if pf is not None:
            parts[spec.id] = pf(state, scenario)

    return ScoreReport(
        fact_score=fact_score,
        policy_score=policy_score,
        composite_score=composite,
        metrics=raw,
        goodness=good,
        metric_parts=parts,
        fact_weights=dict(sc.fact_layer.weights),
        consequences=cons,
        consequence_goodness=cons_good,
        policy_weights=dict(pol.weights),
        constraint_penalty=sc.policy_layer.constraint_penalty,
        constraint_violations=violations,
        policy_id=pol.id,
        policy_label=pol.label,
        fact_weight=sc.composite.fact_weight,
        composite_method=sc.composite.method,
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
