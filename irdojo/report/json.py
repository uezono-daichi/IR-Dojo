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
from ..engine import GameState, compromised_now, effective_truth
from ..schema import Scenario

# ⑥ に出す履歴の件数。全件は records_total で数だけ伝える
RECENT_RECORDS = 20

# 帰結指標の呼び名。**エンジンの概念なのでアプリ側が持つ**（シナリオが
# 名前を付けられるようにすると、方針の重みが指しているものが
# シナリオごとに別名になり、方針を跨いだ比較が読めなくなる）
CONSEQUENCE_LABELS = {
    "elapsed_minutes": "経過時間",
    "total_damage": "被害額（累積＋復旧までの見込み）",
    "accumulated_damage": "累積被害",
    "projected_damage": "復旧までの見込み",
    "business_impact": "業務影響",
    "evidence_preserved": "証拠の保全",
    "containment_completeness": "封じ込めの完全度",
}


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


class SpreadStep(BaseModel):
    """演習中に配られた（あるいは配られずに済んだ）窓1つ（SPEC 5.2 / 7.6.13）。

    **プレイ中は一言も告げない。** 盤が動いたことを学習者は見ていないので、
    その場で言えば損失の予告になる（原則5）。ここが唯一の開示の場である。
    """

    asset: str
    label: str
    at_minute: int
    happened: bool                # 実際に配られたか
    source_labels: list[str]      # 止めてあれば防げた資産
    stopped_at: int | None        # その配り元を止め終わった時刻（間に合わなかった分も出す）


class SpreadReview(BaseModel):
    """真実が動いたことの開示（SPEC 5.2 / 7.6.13）。

    「あなたが判定した時点では N 台、終了時点では M 台」を言う。
    事実認識層は判定時点の N で採点し、封じ込めの完全度は終了時点の M で
    採点する — **数字が2つあることを言わないと、講評の中で
    再現率 100% と完全度 67% が同居する理由が読めない。**
    """

    at_decision: int
    at_finish: int
    decision_labels: list[str]
    finish_labels: list[str]
    decided_at_minute: int
    steps: list[SpreadStep]


class ContainmentReview(BaseModel):
    """封じ込めの答え合わせ（SPEC 5.8 / 7.6.13）。

    **プレイ中は「止めた」としか言わない。** 何が残ったままだったかは、
    学習者には見えていないので、そこで告げれば損失の予告になる（原則5）。
    ここが唯一の開示の場である。

    復旧地平の破線がどの傾きで伸びているかを決めているのは
    `factor` で、それは「止めたか」と「取り除いたか」の**両方**で決まる。
    完全度 100% でも `on_partial` に留まることがあり、
    その差は講評の図では傾きとしてしか見えない — 言葉にしないと、
    グラフを見た人は「正しく止めたのに、なぜまだ伸びるのか」と読む。
    """

    stopped: list[str]              # 攻撃の経路を断てた侵害資産
    missed: list[str]               # 止められなかった侵害資産
    eradicated: list[str]           # 残されたものを取り除けた資産
    still_persistent: list[str]     # 取り除けないまま終わった資産
    factor: float                   # 実際に地平へ掛かった減衰係数
    best_factor: float              # すべて片付けた場合の係数
    verdict: str                    # correct | partial | incorrect
    # 押していれば取り除けた手。**押した後に言う** ので答えにはならない
    eradicable_by: list[str]


class TooLate(BaseModel):
    """失われた後で押した、それを取りに行く手。

    学習者の記憶に残っているのは「30分払って何も出てこなかった手」であって、
    「1時間25分前に世界に奪われていた」ではない。この2つを結ばないと、
    講評は本人が体験した出来事を一度も説明しないまま終わる（SPEC 7.6.13）。
    """

    kind: str               # gather（取りに行く手）| notice（起きなくする手）
    action_label: str
    at_minute: int          # その手を押した時刻
    cost_minutes: int       # そこで払った時間
    late_by_minutes: int    # 失われてから押すまで


class LostEvidence(BaseModel):
    """取得する前に失われた証拠。講評で初めて開示する。"""

    id: str
    summary: str
    destroyed_by: str      # それを壊したアクション、または出来事のラベル
    at_minute: int         # 失われた時刻（分）
    by_world: bool         # 自分の手ではなく、世界の側に奪われたか
    obtainable_by: list[str]   # 先に実行していれば取れたアクション
    # 遅れて押した手。取りに行く手と、起きなくする手の両方を拾う。
    # 「押したのに何も起きなかった」の理由は、プレイ中には言えない
    # （原則5）ので、ここが唯一の説明の場になる
    too_late: list[TooLate] = []


class Marker(BaseModel):
    minute: int
    label: str
    short: str = ""        # グラフ上に描く短い名前
    kind: str = "act"      # act（学習者がしたこと）| world（世界の側で起きたこと）
    # 図と凡例を結ぶ通し番号。色を6色に増やす代わりに番号で結ぶ。
    # 同じ分に2本立つことがあり（dc01 と ws-113 が同時刻）、
    # 位置の近さで対応を推測させると必ず読めなくなる（SPEC 7.6.9）
    index: int = 0


class PolicyTerm(BaseModel):
    """方針適合の内訳1行。「違反なしなのに 39点」の理由はここにしか無い。"""

    key: str
    label: str
    weight: float
    goodness: float        # 0〜1。正規化の帯で測った良さ
    display: str           # 実測値を読める形にしたもの
    # 正規化の帯を言葉にしたもの。これが無いと「30,207 → 0」が
    # 故障にしか見えない（帯の外に出たことが分からない）
    band: str
    contribution: float    # weight × goodness


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


# 層を「取り切った」と見なす線。0〜100 に直したあとの値で見る。
# **文面には出さない。** 学習者に渡すのは層の名前であって目標値ではない（3.11）。
LAYER_CEILING = 95


class NextTarget(BaseModel):
    """2周目以降に狙う層を1行で言う（SPEC 3.11 / 7.6.13）。

    同一シナリオを再プレイすると、学習者は答えを覚えている。3.11 は
    「事実認識スコアは信用できない」とだけ言って止まっていた。
    **では2周目に何を目指すのかが定義されていなかった。**

    信用できる層はある。方針適合は「名乗った方針が優先すると言っているものを、
    実際に選んだ手が優先できていたか」を見るので、**盤面の答えを覚えていても
    上がらない。** 3.11 を否定せず、的を信用できる層のほうへ移す。

    **数字を主語にしない。** 「84 → 90 を狙う」と書くと、そこから先は
    スコア最大化ゲームになる（1.3 が測らないと言っているもの）。
    言うのは層の名前と、その層が何を見ているかまで。
    **どの手を選べとは言わない** — それは答えである（原則2）。

    機械的に守る形は「**学習者に見せる文に算用数字を1文字も書かない**」である
    （`test_the_line_never_names_a_number_to_beat`）。だから周回の数え方も
    「二周目」と漢数字で書く。緩めた瞬間に、次に書く人は「あと6点」を足したくなる。
    """

    layer: str          # "policy"（方針適合の層） / "condition"（条件を変える）
    headline: str
    note: str


def _display(key: str, value: float, state: GameState) -> str:
    """実測値を、単位の分かる形にする。

    0.947 と 30,207 を同じ体裁で並べない。割合は割合として、
    金額は金額として出す。

    証拠の保全だけは割合で出さない。19件中 18件で「95%」と書くと、
    正規化の帯（失いうるのは3件だけ）と桁が合わず、
    「95% なのに 67点」という読めない並びになる。件数で言う。
    """
    if key == "evidence_preserved":
        n = len(state.lost_evidence)
        return "取り損ねなし" if n == 0 else f"{n}件を取り損ねた"
    if key == "containment_completeness":
        return f"{value * 100:.0f}%"
    if key == "elapsed_minutes":
        return f"{int(value)}分"
    return f"{value:,.0f}"


def _band(key: str, bounds, scenario: Scenario) -> str:
    """正規化の帯を、そのまま日本語にする。

    「良さ」は帯で測った値なので、帯を伏せると数字が読めない。
    30,207 が 0 になるのは帯の外（worst 20,000）に出たからであって、
    採点が壊れているからではない。
    """
    if key == "evidence_preserved":
        total = len(scenario.evidence)
        worst_lost = round((1.0 - bounds.worst) * total)
        return f"取り損ねなし で 100点、{worst_lost}件 で 0点"
    if key == "containment_completeness":
        return (
            f"{bounds.best * 100:.0f}% で 100点、{bounds.worst * 100:.0f}% で 0点"
        )
    return f"{bounds.best:,.0f} で 100点、{bounds.worst:,.0f} で 0点"


def policy_terms(
    report_score: scoring_mod.ScoreReport,
    state: GameState,
    scenario: Scenario,
) -> list[PolicyTerm]:
    """方針適合の内訳。重みの大きい順に並べる。

    「方針違反はありませんでした」と「方針適合 39点」が同居していたとき、
    61点がどこへ消えたのかは講評のどこにも書かれていなかった。
    方針適合は **帰結の加重和 ×（1 - 0.15×違反数）** であって、
    違反ゼロでも加重和が低ければ低い。何がどれだけ効いたかを出す。
    """
    cons = report_score.consequences
    norm = scenario.scoring.consequence_layer.normalization
    out: list[PolicyTerm] = []
    for key, weight in report_score.policy_weights.items():
        good = report_score.consequence_goodness.get(key, 0.0)
        raw = getattr(cons, key, None)
        bounds = norm.get(key)
        out.append(
            PolicyTerm(
                key=key,
                label=CONSEQUENCE_LABELS.get(key, key),
                weight=weight,
                goodness=good,
                display=_display(key, raw, state) if raw is not None else "",
                band=_band(key, bounds, scenario) if bounds is not None else "",
                contribution=weight * good,
            )
        )
    out.sort(key=lambda t: -t.weight)
    return out


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
    # 封じ込めの答え合わせ。reveal_ground_truth が false なら出さない
    containment: ContainmentReview | None
    # 真実が動いた盤面でだけ出す。動かない盤面では None（SPEC 5.2）
    spread: SpreadReview | None
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
    # 方針適合の内訳。合計が加重和で、そこに違反の減衰が掛かる
    policy_terms: list[PolicyTerm]
    # 全方針。⑥ が「まだ試していない条件」を名指しするのに要る。
    # replay_suggestions は著者が推す組だけなので、これの代わりにはならない
    policies: list[dict[str, str]]
    record_groups: list[RecordGroup]
    records: list[RecordRow]    # 直近のみ。全件は records_total
    records_total: int
    is_first_play: bool
    # 2周目以降に狙う層。初回は None（SPEC 3.11 / 7.6.13）
    next_target: NextTarget | None


def build(
    state: GameState,
    scenario: Scenario,
    *,
    persist: bool = True,
    moves: list[records_mod.Move] | None = None,
    briefing_seconds: float | None = None,
) -> Report:
    """講評を組み、必要なら記録を保存する。

    `moves` / `briefing_seconds` は**実時間の足あと**で、
    採点にも講評にも一切使われない（記録に書き残すだけ）。
    エンジンは実時間を知らない — 知る必要が無く、知ると
    `balance.py` の何千回の模擬プレイまで時計を持つことになる。
    測るのは API の層である（SPEC 7.5.4）。
    """
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
        # **このプレイで実際に侵害されていた資産**（v1.48）。真実が動く盤面では
        # 開始時点の集合を出すと、すぐ下の「終えた時点 4台」と食い違って見える。
        # どちらも本当のことなので、片方だけを「実際に」と呼んではいけない —
        # 何台になったかはこのプレイの帰結であり、増えた理由は次の節が言う
        ended = compromised_now(scenario, state)
        truth = TruthReveal(
            attack_narrative=gt.attack_narrative,
            compromised=list(ended),
            compromised_labels=[_label(assets, a) for a in ended],
            innocent=list(gt.innocent),
            innocent_labels=[_label(assets, a) for a in gt.innocent],
            patient_zero=gt.patient_zero,
            persistence=list(gt.persistence),
            misleading_evidence=[e.id for e in scenario.evidence if e.misleading],
        )

    markers: list[Marker] = []
    snap = state.assessment_snapshot
    if snap is not None:
        markers.append(
            Marker(minute=snap.at_minute, label="被疑判定を宣言", short="判定")
        )

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
        # 止めたことと、残されたものを取り除いたことは別の作業なので、
        # 同じ時刻に両方やった回は1本にまとめ、別の時刻なら2本立てる。
        # 図に出るのは番号だけで、名前は凡例に置く（7.6.9）
        purged = state.eradicated_at.get(asset_id)
        markers.append(
            Marker(
                minute=minute,
                label=("封じ込め・原状復帰 " if purged == minute else "封じ込め ")
                + _label(assets, asset_id),
                short=asset_id,
            )
        )
        if purged is not None and purged != minute:
            markers.append(
                Marker(
                    minute=purged,
                    label=f"原状復帰 {_label(assets, asset_id)}",
                    short=asset_id,
                )
            )

    # 時刻順に並べて通し番号を振る。図に描くのは番号だけで、名前は凡例に置く。
    # 名前を図に直接置いていた頃は、同時刻の2本の上に6個のラベルが
    # 3段に折り重なり、どの線がどれかを x 座標の近さで推測するしかなかった
    markers.sort(key=lambda m: m.minute)
    for i, mk in enumerate(markers, start=1):
        mk.index = i

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
        state.eradicated_at.keys(),
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

    def _pick(cands: list, kind: str, gone_at: int) -> TooLate | None:
        """種類ごとに、最も早い1手だけを返す。

        同じ話を何度も言うと、どれが自分の手だったか分からなくなる。
        """
        if not cands:
            return None
        a = min(cands, key=lambda x: state.executed_at[x.id])
        at = state.executed_at[a.id]
        return TooLate(
            kind=kind,
            action_label=a.label,
            at_minute=at,
            cost_minutes=a.cost_minutes,
            late_by_minutes=at - gone_at,
        )

    def _too_late(eid: str, gone_at: int, world_id: str | None) -> list[TooLate]:
        """失われた**後**に押した手を拾う。

        本人の記憶にあるのは「時間を払って何も起きなかった手」の方である。
        取り損ねた証拠の側からしか書かないと、講評はその体験に
        一度も触れないまま終わる。

        2種類ある。**取りに行く手**（押しても証拠が出てこない）と、
        **起きなくする手**（周知。押しても世界が止まらない）。
        後者はプレイ中「何も出てこなかった」としか出ないので、
        なぜ効かなかったのかを言える場所はここしかない。
        """
        out: list[TooLate] = []
        gather = _pick(
            [
                a
                for a in scenario.actions
                if eid in a.yields and a.id in state.executed_at
                and state.executed_at[a.id] >= gone_at
            ],
            "gather",
            gone_at,
        )
        if gather is not None:
            out.append(gather)
        if world_id is not None:
            notice = _pick(
                [
                    a
                    for a in scenario.actions
                    if world_id in a.prevents and a.id in state.executed_at
                    and state.executed_at[a.id] >= gone_at
                ],
                "notice",
                gone_at,
            )
            if notice is not None:
                out.append(notice)
        return out

    def _lost(eid: str) -> LostEvidence:
        world_id = state.destroyed_by_world.get(eid)
        if world_id is not None:
            label = by_tl[world_id].label if world_id in by_tl else ""
        else:
            label = destroyer[eid].label if eid in destroyer else ""
        gone_at = state.destroyed_at.get(eid, 0)
        return LostEvidence(
            id=eid,
            summary=by_ev[eid].summary,
            destroyed_by=label,
            at_minute=gone_at,
            by_world=world_id is not None,
            obtainable_by=[a.label for a in scenario.actions if eid in a.yields],
            too_late=_too_late(eid, gone_at, world_id),
        )

    lost = [_lost(eid) for eid in state.lost_evidence if eid in by_ev]

    is_first = not records_mod.has_any(scenario.meta.id)

    if persist:
        rec = _to_record(
            state, scenario, report_score, is_first,
            moves=moves, briefing_seconds=briefing_seconds,
        )
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
        # 講評が出すのは「良さ」なので、名前も良い側のものを渡す（SPEC 6.2）
        metric_labels={
            m.id: m.display_label for m in scenario.scoring.fact_layer.metrics
        },
        violations=violations,
        retrospective=retro,
        truth=truth,
        containment=_containment_review(state, scenario) if truth else None,
        spread=_spread_review(state, scenario) if truth else None,
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
        policy_terms=policy_terms(report_score, state, scenario),
        policies=[{"id": p.id, "label": p.label} for p in scenario.policies],
        record_groups=groups,
        next_target=_next_target(groups, is_first),
        records=rows,
        records_total=len(all_records),
        is_first_play=is_first,
    )


def _spread_review(state: GameState, scenario: Scenario) -> SpreadReview | None:
    """真実が動いたことを開示する（SPEC 5.2）。

    **拡散の無い盤面では None を返す。** 動かない盤面に「判定時点 3台／
    終了時点 3台」と出しても、読み手には何の話か分からない。
    """
    gt = scenario.world.ground_truth
    if not gt.spreads:
        return None

    assets = scenario.asset_by_id
    snap = state.assessment_snapshot
    at_decision = list(snap.compromised) if snap else list(gt.compromised)
    at_finish = compromised_now(scenario, state)

    steps: list[SpreadStep] = []
    for sp in gt.spreads:
        if sp.id not in state.spreads_fired and sp.id not in state.spreads_averted:
            continue      # その窓が来る前に手を止めた。起きなかった話はしない
        stopped = [state.contained_at[a] for a in sp.unless_contained
                   if a in state.contained_at]
        steps.append(
            SpreadStep(
                asset=sp.to,
                label=_label(assets, sp.to),
                at_minute=sp.at_minutes,
                happened=(sp.id in state.spreads_fired),
                source_labels=[_label(assets, a) for a in sp.unless_contained],
                stopped_at=max(stopped) if len(stopped) == len(set(sp.unless_contained))
                else None,
            )
        )

    return SpreadReview(
        at_decision=len(at_decision),
        at_finish=len(at_finish),
        decision_labels=[_label(assets, a) for a in at_decision],
        finish_labels=[_label(assets, a) for a in at_finish],
        decided_at_minute=snap.at_minute if snap else 0,
        steps=steps,
    )


def _containment_review(
    state: GameState, scenario: Scenario
) -> ContainmentReview:
    """止めた／取り除いた の答え合わせを組む（SPEC 5.8）。

    `ground_truth` を読むので、**講評の経路にしか置けない。**
    プレイ中のどのレスポンスにもこの形は現れない。
    """
    gt = effective_truth(scenario, state)
    effect = scenario.damage.containment_effect
    assets = scenario.asset_by_id

    contained = set(state.contained_at)
    purged = set(state.eradicated_at)
    # **終了時点の広さで答え合わせする**（SPEC 5.2）。演習中に配られた先は、
    # 止めるべきものとしてここに並ぶ。判定時点の広さで見ると、
    # 「全部止めた」と書いてあるのに被害が緩まない講評になる
    compromised = list(gt.compromised)
    persistence = list(gt.persistence)

    factor = damage_mod.containment_factor(contained, purged, gt, effect)
    if factor == effect.on_correct_containment:
        verdict = "correct"
    elif factor == effect.on_partial:
        verdict = "partial"
    else:
        verdict = "incorrect"

    remaining = [a for a in persistence if a not in purged]
    # 押していれば取り除けた手。押し終わったあとなので答えにはならない
    could = [
        act.label
        for act in scenario.actions
        if set(act.eradicates) & set(remaining)
    ]

    return ContainmentReview(
        stopped=[_label(assets, a) for a in compromised if a in contained],
        missed=[_label(assets, a) for a in compromised if a not in contained],
        eradicated=[_label(assets, a) for a in persistence if a in purged],
        still_persistent=[_label(assets, a) for a in remaining],
        factor=factor,
        best_factor=effect.on_correct_containment,
        verdict=verdict,
        eradicable_by=could,
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


def _next_target(
    groups: list[RecordGroup], is_first: bool
) -> NextTarget | None:
    """次に狙う層を1行で返す（SPEC 3.11 / 7.6.13）。

    初回は返さない。**初回の的は講評そのもの**で、まだ何も知らない人に
    「次はこの層」と言っても意味を成さない。

    2周目以降は、その条件でこれまでに届いた方針適合を見る。まだ余地があるなら
    そこを名指しし、取り切っているなら条件のほうを変えてもらう。
    事実認識は的にしない — 答えを覚えているぶんだけ上がるので、
    伸びたかどうかを確かめる的にならない（3.11）。
    """
    if is_first:
        return None
    here = [g for g in groups if g.is_current]
    if not here:
        return None
    g = here[0]

    if g.policy_score < LAYER_CEILING:
        lead = "事実認識はこの条件で取り切っています。" if g.fact_score >= LAYER_CEILING else ""
        return NextTarget(
            layer="policy",
            headline=lead + "次に狙うのは方針適合の層です。",
            note=(
                "方針適合が低いのは、方針の名乗りと違う手を選んでいるからです。"
                "この層が見るのは、名乗った方針が「これを優先する」と言っているものを、"
                "実際の手がそのとおり優先できていたか、それだけです。"
                "盤面の答えを覚えていても上がりません — "
                "だから二周目以降に確かめられるのは、こちらの層です。"
            ),
        )

    return NextTarget(
        layer="condition",
        headline="この条件では、方針適合の層まで取り切っています。"
        "次は方針を変えて、同じ盤面をもう一度解いてください。",
        note=(
            "同じ行動列でも、名乗る方針が変われば評価は変わります。"
            "見る番なのは「どの手が正しいか」ではなく"
            "「どの方針の下で正しくなるか」です。"
            "事実認識の側は、二周目以降は答えを覚えているぶんだけ上がるので、"
            "伸びたかどうかを確かめる的にはなりません。"
        ),
    )


def _to_record(
    state: GameState,
    scenario: Scenario,
    report_score: scoring_mod.ScoreReport,
    is_first: bool,
    *,
    moves: list[records_mod.Move] | None = None,
    briefing_seconds: float | None = None,
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
        moves=list(moves or []),
        briefing_seconds=briefing_seconds,
    )


def _label(assets: dict, asset_id: str) -> str:
    a = assets.get(asset_id)
    return f"{asset_id}（{a.label}）" if a else asset_id


def _policy_label(scenario: Scenario, policy_id: str) -> str:
    p = scenario.policy_by_id.get(policy_id)
    return p.label if p else policy_id
