"""講評データの生成（SPEC 7.6.13）。

フロントが描画する。HTML はここで作らない。
ここで初めて ground_truth を開示する — プレイ中の経路には決して載せない。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from .. import damage as damage_mod
from .. import records as records_mod
from .. import retrospective as retro_mod
from .. import scoring as scoring_mod
from ..engine import GameState
from ..schema import Scenario

# ⑥ に出す履歴の件数。全件は records_total で数だけ伝える
RECENT_RECORDS = 20


class ViolationDetail(BaseModel):
    constraint_type: str
    action_id: str
    action_label: str
    message: str
    at_minute: int


class TruthReveal(BaseModel):
    attack_narrative: str
    compromised: list[str]
    compromised_labels: list[str]
    innocent: list[str]
    innocent_labels: list[str]
    patient_zero: str
    persistence: list[str]
    misleading_evidence: list[str]  # 事後に色分けするため


class LostEvidence(BaseModel):
    """取得する前に失われた証拠。講評で初めて開示する。"""

    id: str
    summary: str
    destroyed_by: str      # それを壊したアクション、または出来事のラベル
    at_minute: int         # 失われた時刻（分）
    by_world: bool         # 自分の手ではなく、世界の側に奪われたか
    obtainable_by: list[str]   # 先に実行していれば取れたアクション


class Marker(BaseModel):
    minute: int
    label: str
    short: str = ""        # グラフ上に描く短い名前
    kind: str = "act"      # act（学習者がしたこと）| world（世界の側で起きたこと）


class RecordRow(BaseModel):
    filename: str
    policy_label: str
    assist_level: str
    fact_score: int
    policy_score: int
    composite_score: int
    is_first_play: bool
    scenario_version: str
    played_at: datetime


class RecordGroup(BaseModel):
    """同じ条件（方針 × アシスト）で何度やったかを1行にまとめたもの。

    ⑥ の役割は「条件を変えると評価が変わる」を1枚で見せることであって、
    プレイの履歴を並べることではない（SPEC 7.6.13）。同じ条件を10回やった
    記録をそのまま10行出すと、比較したい軸が縦に埋もれて読めなくなる。
    """

    policy_id: str
    policy_label: str
    assist_level: str
    plays: int
    # その条件での最高（総合）のプレイ。条件が到達しうる水準を代表させる
    fact_score: int
    policy_score: int
    composite_score: int
    best_at: datetime
    has_first_play: bool
    is_current: bool           # 今回と同じ条件か
    stale: bool                # シナリオが更新される前の記録を含むか


class Report(BaseModel):
    scenario_id: str
    scenario_title: str
    scenario_version: str
    assist_level: str
    score: scoring_mod.ScoreReport
    metric_labels: dict[str, str]
    violations: list[ViolationDetail]
    retrospective: retro_mod.Retro
    truth: TruthReveal | None
    key_lessons: list[str]
    replay_suggestions: list[dict[str, str]]
    damage_history: list[float]
    # 手を止めた時点の封じ込め状態のまま伸ばした先（累積で続く）。
    # **講評でしか出さない。** プレイ中に「この先こう伸びる」と言うのは
    # 損失を先に告げることであり、原則5 に反する（7.6.9）
    damage_projection: list[float]
    recovery_horizon_minutes: int
    markers: list[Marker]
    lost_evidence: list[LostEvidence]
    # 全方針。⑥ が「まだ試していない条件」を名指しするのに要る。
    # replay_suggestions は著者が推す組だけなので、これの代わりにはならない
    policies: list[dict[str, str]]
    record_groups: list[RecordGroup]
    records: list[RecordRow]    # 直近のみ。全件は records_total
    records_total: int
    is_first_play: bool


def build(
    state: GameState,
    scenario: Scenario,
    *,
    persist: bool = True,
) -> Report:
    report_score = scoring_mod.score(state, scenario)
    retro = retro_mod.build(state, scenario)

    by_action = scenario.action_by_id
    violations = [
        ViolationDetail(
            constraint_type=v.constraint_type,
            action_id=v.action_id,
            action_label=by_action[v.action_id].label
            if v.action_id in by_action
            else v.action_id,
            message=v.message,
            at_minute=v.at_minute,
        )
        for v in state.violations_by_policy.get(report_score.policy_id, [])
    ]

    assets = scenario.asset_by_id
    truth = None
    if scenario.debrief.reveal_ground_truth:
        gt = scenario.world.ground_truth
        truth = TruthReveal(
            attack_narrative=gt.attack_narrative,
            compromised=list(gt.compromised),
            compromised_labels=[_label(assets, a) for a in gt.compromised],
            innocent=list(gt.innocent),
            innocent_labels=[_label(assets, a) for a in gt.innocent],
            patient_zero=gt.patient_zero,
            persistence=list(gt.persistence),
            misleading_evidence=[e.id for e in scenario.evidence if e.misleading],
        )

    markers: list[Marker] = []
    snap = state.assessment_snapshot
    if snap is not None:
        markers.append(Marker(minute=snap.at_minute, label="判定", short="判定"))

    # 折れ点。プレイ中は最後まで黙っている（曲線が跳ねたようにしか見えないのが正しい）。
    # 講評は答えを開ける場所なので、ここで初めて位置を名指しする。
    # 踏まなかったプレイには出さない — 起きなかったことを図に描いても意味がない
    accel = scenario.damage.acceleration
    if accel is not None and state.elapsed_minutes > accel.threshold_minutes:
        markers.append(
            Marker(
                minute=accel.threshold_minutes,
                label=f"被害の増え方が変わった時点（以降 {accel.multiplier} 倍）",
                short="加速",
                kind="world",
            )
        )
    for asset_id, minute in sorted(state.contained_at.items(), key=lambda kv: kv[1]):
        markers.append(
            Marker(
                minute=minute,
                label=f"封じ込め {_label(assets, asset_id)}",
                short=asset_id,
            )
        )

    # 復旧地平の延長線（SPEC 5.8）。累積のまま先へ伸ばす。
    # 「あなたが手を止めた時点で、この先はこう伸びる」は講評でしか言えない。
    # 何を止めたか（止めなかったか）が、ここで初めて傾きとして見える
    horizon = scenario.scoring.consequence_layer.business_impact_horizon_minutes
    running = state.accumulated_damage
    projection: list[float] = []
    for inc in damage_mod.project(
        scenario.damage,
        scenario.world.ground_truth,
        state.contained_at.keys(),
        state.elapsed_minutes,
        horizon,
    ):
        running += inc
        projection.append(running)

    # 取り損ねた証拠。プレイ中は黙っていたものを、ここで初めて言う
    by_ev = scenario.evidence_by_id
    destroyer = {}
    for aid in state.executed_actions:
        act = by_action.get(aid)
        if act is None:
            continue
        for eid in act.destroys:
            destroyer.setdefault(eid, act)
    by_tl = {e.id: e for e in scenario.timeline}

    def _lost(eid: str) -> LostEvidence:
        world_id = state.destroyed_by_world.get(eid)
        if world_id is not None:
            label = by_tl[world_id].label if world_id in by_tl else ""
        else:
            label = destroyer[eid].label if eid in destroyer else ""
        return LostEvidence(
            id=eid,
            summary=by_ev[eid].summary,
            destroyed_by=label,
            at_minute=state.destroyed_at.get(eid, 0),
            by_world=world_id is not None,
            obtainable_by=[a.label for a in scenario.actions if eid in a.yields],
        )

    lost = [_lost(eid) for eid in state.lost_evidence if eid in by_ev]

    is_first = not records_mod.has_any(scenario.meta.id)

    if persist:
        rec = _to_record(state, scenario, report_score, is_first)
        records_mod.save(rec)

    all_records = records_mod.load_all(scenario.meta.id)
    groups = _group_records(all_records, scenario, state)

    # 履歴は新しい順に、直近だけ。★初回は groups 側が持っているので落ちない
    rows = [
        RecordRow(
            filename=name,
            policy_label=r.policy_label,
            assist_level=r.assist_level.value,
            fact_score=round(r.fact_score * 100),
            policy_score=round(r.policy_score * 100),
            composite_score=round(r.composite_score * 100),
            is_first_play=r.is_first_play,
            scenario_version=r.scenario_version,
            played_at=r.played_at,
        )
        for name, r in reversed(all_records)
    ][:RECENT_RECORDS]

    return Report(
        scenario_id=scenario.meta.id,
        scenario_title=scenario.meta.title,
        scenario_version=scenario.meta.version,
        assist_level=state.assist_level.value,
        score=report_score,
        metric_labels={m.id: m.label for m in scenario.scoring.fact_layer.metrics},
        violations=violations,
        retrospective=retro,
        truth=truth,
        key_lessons=list(scenario.debrief.key_lessons),
        replay_suggestions=[
            {
                "policy": s.policy,
                "policy_label": _policy_label(scenario, s.policy),
                "hint": s.hint,
            }
            for s in scenario.debrief.replay_suggestions
        ],
        damage_history=list(state.damage_history),
        damage_projection=projection,
        recovery_horizon_minutes=horizon,
        markers=markers,
        lost_evidence=lost,
        policies=[{"id": p.id, "label": p.label} for p in scenario.policies],
        record_groups=groups,
        records=rows,
        records_total=len(all_records),
        is_first_play=is_first,
    )


def _group_records(
    all_records: list[tuple[str, records_mod.Record]],
    scenario: Scenario,
    state: GameState,
) -> list[RecordGroup]:
    """(方針, アシスト) ごとに1行へ畳む。代表は総合が最も高いプレイ。"""
    buckets: dict[tuple[str, str], list[records_mod.Record]] = {}
    for _name, r in all_records:
        buckets.setdefault((r.policy_id, r.assist_level.value), []).append(r)

    out: list[RecordGroup] = []
    for (policy_id, assist), recs in buckets.items():
        best = max(recs, key=lambda r: (r.composite_score, r.played_at))
        out.append(
            RecordGroup(
                policy_id=policy_id,
                policy_label=best.policy_label,
                assist_level=assist,
                plays=len(recs),
                fact_score=round(best.fact_score * 100),
                policy_score=round(best.policy_score * 100),
                composite_score=round(best.composite_score * 100),
                best_at=best.played_at,
                has_first_play=any(r.is_first_play for r in recs),
                is_current=(
                    policy_id == state.policy_id
                    and assist == state.assist_level.value
                ),
                stale=any(r.scenario_version != scenario.meta.version for r in recs),
            )
        )
    # 総合の高い順。条件の比較が目的なので、時系列ではなく到達水準で並べる
    out.sort(key=lambda g: (-g.composite_score, g.policy_label, g.assist_level))
    return out


def _to_record(
    state: GameState,
    scenario: Scenario,
    report_score: scoring_mod.ScoreReport,
    is_first: bool,
) -> records_mod.Record:
    cons = report_score.consequences
    snap = state.assessment_snapshot
    return records_mod.Record(
        scenario_id=scenario.meta.id,
        scenario_title=scenario.meta.title,
        scenario_version=scenario.meta.version,
        policy_id=report_score.policy_id,
        policy_label=report_score.policy_label,
        assist_level=state.assist_level,
        played_at=datetime.now(timezone.utc),
        is_first_play=is_first,
        fact_score=report_score.fact_score,
        policy_score=report_score.policy_score,
        composite_score=report_score.composite_score,
        metrics=report_score.metrics,
        elapsed_minutes=cons.elapsed_minutes,
        total_damage=cons.total_damage,
        business_impact=cons.business_impact,
        evidence_preserved=cons.evidence_preserved,
        containment_completeness=cons.containment_completeness,
        assessment=list(state.assessment),
        unresolved_at_decision=list(snap.unresolved_critical) if snap else [],
        constraint_violations=report_score.constraint_violations,
    )


def _label(assets: dict, asset_id: str) -> str:
    a = assets.get(asset_id)
    return f"{asset_id}（{a.label}）" if a else asset_id


def _policy_label(scenario: Scenario, policy_id: str) -> str:
    p = scenario.policy_by_id.get(policy_id)
    return p.label if p else policy_id
