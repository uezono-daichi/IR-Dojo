"""演習の状態機械（SPEC 7.2 / 7.3 / 7.4）。

エンジンはシナリオの中身を知らない。アクションを実行すると時間が進み、
証拠が開示され、状態が変わる — その構造だけを扱う（SPEC 原則1）。
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from . import damage as damage_mod
from . import questions as questions_mod
from .constraints import ConstraintContext, evaluate
from .schema import (
    Action,
    ActionType,
    AssistLevel,
    AssistProfile,
    Criticality,
    Scenario,
)


# ─────────── 実行時状態（SPEC 7.2） ───────────


class ConstraintViolation(BaseModel):
    constraint_type: str
    action_id: str
    message: str
    at_minute: int


class AssessmentSnapshot(BaseModel):
    """被疑判定を宣言した瞬間の記録。採点の基準点。"""

    at_minute: int
    assessed_assets: list[str]
    unresolved_critical: list[str]
    obtained_evidence: list[str]


class GameState(BaseModel):
    policy_id: str
    assist_level: AssistLevel
    current_phase: str
    elapsed_minutes: int = 0
    executed_actions: list[str] = []
    obtained_evidence: list[str] = []
    obtained_at: dict[str, int] = {}  # 証拠ID → 取得した時刻（分）
    destroyed_evidence: list[str] = []
    destroyed_at: dict[str, int] = {}   # 証拠ID → 失われた時刻（分）
    # 世界の側に奪われたもの。自分の手で壊したものとは講評での言い方が変わる
    destroyed_by_world: dict[str, str] = {}   # 証拠ID → 出来事ID
    # 破壊された証拠のうち、取得する前に失ったもの。
    # 取得済みのものは失われない（見たことは忘れない）。講評でのみ開示する。
    lost_evidence: list[str] = []
    resolved_questions: list[str] = []
    assessment: list[str] = []
    assessment_snapshot: AssessmentSnapshot | None = None
    contained_at: dict[str, int] = {}  # 資産ID → 停止時刻（分）。依存連鎖込み（6.3）
    phase_transitions: dict[str, int] = {}
    # 方針ごとに違反を記録する。同じ行動列を別方針で採点したとき、
    # その方針の制約で評価し直せるようにするため（SPEC 6.8）。
    # 学習者に見せるのは自分の方針の分だけ、しかも講評でのみ。
    violations_by_policy: dict[str, list[ConstraintViolation]] = {}
    accumulated_damage: float = 0.0
    accumulated_business_impact: float = 0.0
    damage_history: list[float] = []  # 1分刻みの累積被害。グラフ描画用
    # misled_score の分母と分子（SPEC 6.2）。ビューには出さない。
    investigation_minutes: int = 0
    misled_follow_minutes: int = 0
    fired_events: list[str] = []      # 発生済みの時間経過イベント
    # 先手を打って起きなくしたもの。既に起きた分には効かない
    prevented_events: list[str] = []
    averted_events: list[str] = []    # 実際に不発に終わった分
    finished: bool = False


# ─────────── プレイヤーに見せるビュー（SPEC 7.4） ───────────


class ActionOutcome(BaseModel):
    """アクション1回の結果。何が分かり、何が新たに選べるようになったか。

    **失ったものは返さない。** 取得済みの証拠は破壊されないし、
    取り損ねたものは学習者がその存在を知らないので、
    「失われました」と言うこと自体がシナリオの中身を漏らす。
    結果は、後でその調査を実行したときの「何も出てこなかった」で現れる。
    """

    revealed: list[str] = []
    unlocked: list[str] = []  # この発見で新たに調べられるようになったもの
    # 封じ込めで新たに止まった資産。依存で波及した分を含む。
    # 何をしたのかを告げないと、押しても無言になる。
    contained: list[str] = []
    cascaded: list[str] = []          # そのうち依存で巻き込まれた分
    business_impact_delta: float = 0.0
    events: list[str] = []            # この間に向こうから入ってきたこと
    averted: list[str] = []           # そのうち、先手が効いて不発に終わった分
    # 連絡で「以後起きなくなる」ようにしたもの。押しても無言にしないため
    prevented: list[str] = []

    @property
    def empty(self) -> bool:
        # 出来事は世界の側の話。押した結果が空振りだったかとは別に数える。
        # 45分かけて何も出ず、その間に電話が鳴った、は両方起きうる。
        return not self.revealed and not self.contained and not self.prevented


class Decision(BaseModel):
    kind: Literal["action", "advance_phase", "declare_assessment", "finish"]
    action_id: str | None = None
    assessment: list[str] = []


class QuestionView(BaseModel):
    id: str
    label: str
    question: str
    resolved: bool | None  # show_question_resolution=False のとき None


class EvidenceView(BaseModel):
    """学習者に見せる証拠。

    misleading / refuted_by / points_to を含めない。
    points_to は「この証拠がどの資産を示唆するか」という構造化された答えであり、
    summary より強いヒントになる。
    """

    id: str
    summary: str | None  # hard では None
    content: str
    reading: str | None  # ログの読み方。assisted のみ
    at_minute: int       # 取得した時刻。並びに意味を持たせる


class ActionView(BaseModel):
    """学習者に見せるアクション。

    yields / destroys / investigates を含めない。渡すと
    「このアクションで証拠が何件出るか」が事前に見えて選択が賭けでなくなる。

    destroys の存在はアクション名の動詞が語る（SPEC 7.4）。
    「停止して」「再起動して」なら揮発性の情報が消えることは動詞から分かる。
    括弧書きの注記で補わない。注記はシステムが自分の罠に印を付ける行為であり、
    原則2 に反する。動詞で言えないなら、シナリオ側の定義が間違っている。

    **command を含めない。** 端末の記録は「何をしたか」であって
    「何を選ぶか」の材料ではない。押す前に配ると、そこに書かれた件数が
    機構ではなく結論になる（「0 detections」は 35分かけて買うはずの所見）。
    押した後に decide のレスポンスで返す（SPEC 7.6.8）。
    選ぶ材料はラベル（世界の言葉）と所要（費用）で足りる。
    """

    id: str
    label: str
    description: str | None  # 何をする作業か。hard では None
    type: ActionType
    cost_minutes: int
    executed: bool
    selectable: bool
    phase: str          # 表示のグループ分けに使う
    phase_label: str
    group: str          # 一覧での並べ分け。空ならフェーズ名でまとめる


class PhaseView(BaseModel):
    """演習の段取り。どこにいて、この先に何が控えているか。

    これは演習の規則であって、シナリオの答えではない。
    規則を隠すと、学習者は仕掛けに驚かされるだけで判断ができない。
    """

    id: str
    label: str
    done: bool           # 通過済み
    current: bool
    requires_assessment: bool


class AssetView(BaseModel):
    """資産。被疑判定の選択肢であり、環境の構成図の材料でもある。

    `depends_on` は隠さない。自組織の構成は担当者が知っていて当然のもので、
    伏せると「構成を知らない人だけが上流を止めて痛い目を見る」という、
    判断ではなく知識を測る形になる（3.10）。
    `business_impact_per_hour` は採点の内部値なので渡さない。
    """

    id: str
    label: str
    criticality: Criticality
    depends_on: list[str]


class PlayerView(BaseModel):
    """学習者に見せる情報のみ。ground_truth は一切含まない。"""

    scenario_title: str
    policy_label: str
    policy_briefing: str
    assist_level: AssistLevel
    show_primer: bool          # 考え方の枠組みを出すか（3.10）
    show_topology: bool        # 構成図を出すか。依存関係そのものは常に渡す
    elapsed_minutes: int
    accumulated_damage: float
    accumulated_business_impact: float
    current_phase: str
    current_phase_label: str
    phases: list[PhaseView]
    advance_label: str | None
    next_phase_requires_assessment: bool
    assets: list[AssetView]
    obtained_evidence: list[EvidenceView]
    open_questions: list[QuestionView] | None  # hard では None
    unresolved_count: int | None  # 未解消論点の件数。hard では None
    damage_history: list[float] | None  # hard では None
    available_actions: list[ActionView]
    current_assessment: list[str]
    finished: bool


class Player(Protocol):
    def choose(self, view: PlayerView) -> Decision: ...


def _unlocked_by(action: Action, obtained: set[str]) -> bool:
    """手がかりを1つでも持っていれば解放される。空なら最初から選べる。"""
    if not action.requires_evidence:
        return True
    return bool(set(action.requires_evidence) & obtained)


# ─────────── 例外 ───────────


class InvalidDecision(Exception):
    """規則上できない操作。"""


class AssessmentRequired(Exception):
    """被疑判定の宣言が要る。UI はモーダルを開く。"""


# ─────────── エンジン ───────────


class Engine:
    def __init__(
        self,
        scenario: Scenario,
        policy_id: str | None = None,
        assist_level: AssistLevel | None = None,
    ) -> None:
        self.scenario = scenario
        pid = policy_id or scenario.meta.default_policy
        if pid not in scenario.policy_by_id:
            raise InvalidDecision(f"未定義の方針: {pid}")
        level = assist_level or scenario.meta.default_assist_level
        self.state = GameState(
            policy_id=pid,
            assist_level=level,
            current_phase=scenario.phases[0].id,
        )
        self.state.phase_transitions[scenario.phases[0].id] = 0
        self.state.resolved_questions = questions_mod.resolved_ids(scenario, [])

    # ── 参照 ──

    @property
    def policy(self):
        return self.scenario.policy_by_id[self.state.policy_id]

    @property
    def profile(self) -> AssistProfile:
        return self.scenario.profile(self.state.assist_level)

    def _phase(self, phase_id: str):
        for p in self.scenario.phases:
            if p.id == phase_id:
                return p
        raise InvalidDecision(f"未定義のフェーズ: {phase_id}")

    def _next_phase(self):
        ids = [p.id for p in self.scenario.phases]
        i = ids.index(self.state.current_phase)
        if i + 1 >= len(ids):
            return None
        return self.scenario.phases[i + 1]

    def available_actions(self) -> list[Action]:
        unlocked = self.scenario.phases_up_to(self.state.current_phase)
        got = set(self.state.obtained_evidence)
        return [
            a
            for a in self.scenario.actions
            if a.phase in unlocked and _unlocked_by(a, got)
        ]

    # ── 遷移 ──

    def decide(self, decision: Decision) -> ActionOutcome:
        """決定を1つ適用し、何が分かって何を失ったかを返す。"""
        if self.state.finished:
            raise InvalidDecision("この演習は終了しています")

        if decision.kind == "action":
            if not decision.action_id:
                raise InvalidDecision("action_id が指定されていません")
            return self._execute(decision.action_id)

        if decision.kind == "advance_phase":
            self._advance()
            return ActionOutcome()

        if decision.kind == "declare_assessment":
            self._declare(decision.assessment)
            return ActionOutcome()

        if decision.kind == "finish":
            self._finish()
            return ActionOutcome()

        raise InvalidDecision(f"未知の決定: {decision.kind}")

    def _execute(self, action_id: str) -> ActionOutcome:
        action = self.scenario.action_by_id.get(action_id)
        if action is None:
            raise InvalidDecision(f"未定義のアクション: {action_id}")
        unlocked = self.scenario.phases_up_to(self.state.current_phase)
        if action.phase not in unlocked:
            raise InvalidDecision(f"このフェーズでは実行できません: {action_id}")
        if not _unlocked_by(action, set(st_evidence := self.state.obtained_evidence)):
            raise InvalidDecision(f"まだ手がかりがありません: {action_id}")
        del st_evidence
        if not action.repeatable and action_id in self.state.executed_actions:
            raise InvalidDecision(f"実行済みです: {action_id}")

        st = self.state

        # 1. 制約チェック（実行前の状態で。記録するだけで止めない・通知しない）
        pre_ctx = ConstraintContext(
            scenario=self.scenario,
            action=action,
            obtained_evidence=frozenset(st.obtained_evidence),
            executed_actions=tuple(st.executed_actions),
            at_minute=st.elapsed_minutes,
            business_impact_after=st.accumulated_business_impact,
        )
        self._record_violations(pre_ctx, only=None)

        # 2-3. 時間を進め、その区間の被害を積分する
        start = st.elapsed_minutes
        end = start + action.cost_minutes

        # 進めた区間に入る出来事を拾う。長いアクションなら複数まとめて起きる
        fired = [
            ev
            for ev in self.scenario.timeline
            if start < ev.at_minutes <= end and ev.id not in st.fired_events
        ]
        fired.sort(key=lambda ev: ev.at_minutes)
        events = [ev.id for ev in fired]
        st.fired_events.extend(events)

        # 先手を打ってあったものは、起きはするが何も奪わない。
        # 人は動くが、周知が届いていたので手が止まる
        averted = [ev.id for ev in fired if ev.id in st.prevented_events]
        st.averted_events.extend(averted)

        # 世界の側の破壊。意味はアクションの destroys と同じで、
        # **以後取得できなくなる**だけ。取得済みのものは消えない。
        # 押している最中に起きたことなので、この手の証拠開示より先に効く
        for ev in fired:
            if ev.id in st.prevented_events:
                continue
            for eid in ev.destroys:
                if eid in st.destroyed_evidence:
                    continue
                st.destroyed_evidence.append(eid)
                st.destroyed_at[eid] = ev.at_minutes
                st.destroyed_by_world[eid] = ev.id
                if eid not in st.obtained_evidence:
                    st.lost_evidence.append(eid)
        increments = damage_mod.accrue(
            self.scenario.damage,
            self.scenario.world.ground_truth,
            st.contained_at.keys(),
            start,
            end,
        )
        for inc in increments:
            st.accumulated_damage += inc
            st.damage_history.append(st.accumulated_damage)
        st.elapsed_minutes = end

        # 解放前のアクション数。何が増えたかを学習者に返すため
        before_unlocked = {a.id for a in self.available_actions()}

        # 4. 証拠の開示
        revealed: list[str] = []
        for eid in action.yields:
            if eid in st.destroyed_evidence:
                continue  # 既に失われたものは出てこない
            if eid not in st.obtained_evidence:
                st.obtained_evidence.append(eid)
                st.obtained_at[eid] = end
                revealed.append(eid)

        # 5. 証拠の破壊。**以後その証拠は取得できなくなる**。
        #    すでに手元にあるものは消えない。端末を停止しても、
        #    取得済みのメモリダンプが消えるわけではない。
        #    取り損ねたものだけが、この調査から永久に失われる。
        for eid in action.destroys:
            if eid in st.destroyed_evidence:
                continue
            st.destroyed_evidence.append(eid)
            st.destroyed_at[eid] = end
            if eid not in st.obtained_evidence:
                st.lost_evidence.append(eid)

        # 6. 論点の解消状態を再評価
        st.resolved_questions = questions_mod.resolved_ids(
            self.scenario, st.obtained_evidence
        )

        # misled_score の集計（SPEC 6.2）。誤導証拠を取得した後に、
        # それが指す資産を調べた時間を数える。
        if action.type == ActionType.INVESTIGATE:
            st.investigation_minutes += action.cost_minutes
            if self._follows_misleading(action, pre_ctx.obtained_evidence):
                st.misled_follow_minutes += action.cost_minutes

        # 7. 封じ込め。依存連鎖を展開して entered_at を確定する
        contained: list[str] = []
        cascaded: list[str] = []
        if action.type == ActionType.CONTAIN:
            before_contained = set(st.contained_at)
            direct = dict(st.contained_at)
            for asset_id in action.targets:
                direct.setdefault(asset_id, end)
            st.contained_at = damage_mod.expand_containment(
                direct, self.scenario.asset_by_id
            )
            contained = [a for a in st.contained_at if a not in before_contained]
            # 直接指定していないのに止まったもの＝依存で波及した分
            cascaded = [a for a in contained if a not in action.targets]

        # 7.5 連絡。以後この出来事は起きなくなる。
        #     効き始めるのは **打ち終わった時刻**（end）から。
        #     周知を書いている 20分の間に現場が動いてしまうことはある
        prevented: list[str] = []
        for tid in action.prevents:
            if tid in st.fired_events or tid in st.prevented_events:
                continue  # 起きてしまった後の周知は、その件には効かない
            st.prevented_events.append(tid)
            prevented.append(tid)

        # 8. 業務影響を再計算（復旧地平込み。SPEC 6.3）
        impact_before = st.accumulated_business_impact
        st.accumulated_business_impact = self.business_impact()

        # 実行済みに追加（制約の require_before は実行前の履歴で見るため最後）
        st.executed_actions.append(action.id)

        # post 判定の制約（max_business_impact）
        post_ctx = ConstraintContext(
            scenario=self.scenario,
            action=action,
            obtained_evidence=frozenset(st.obtained_evidence),
            executed_actions=tuple(st.executed_actions),
            at_minute=st.elapsed_minutes,
            business_impact_after=st.accumulated_business_impact,
        )
        self._record_violations(post_ctx, only={"max_business_impact"})

        after_unlocked = {a.id for a in self.available_actions()}
        return ActionOutcome(
            revealed=revealed,
            unlocked=sorted(after_unlocked - before_unlocked),
            contained=contained,
            cascaded=cascaded,
            business_impact_delta=st.accumulated_business_impact - impact_before,
            events=events,
            averted=averted,
            prevented=prevented,
        )

    def _record_violations(
        self, ctx: ConstraintContext, only: set[str] | None
    ) -> None:
        """全方針について違反を記録する。

        学習者の方針以外も評価しておくのは、同じ行動列を別方針で
        採点し直せるようにするため。プレイ中に見せるものは何もない。
        """
        st = self.state
        for policy in self.scenario.policies:
            recorded = st.violations_by_policy.setdefault(policy.id, [])
            once = frozenset(v.constraint_type for v in recorded)
            for c in evaluate(policy, ctx, once):
                if only is not None and c.type not in only:
                    continue
                recorded.append(
                    ConstraintViolation(
                        constraint_type=c.type,
                        action_id=ctx.action.id,
                        message=c.message,
                        at_minute=ctx.at_minute,
                    )
                )

    def _follows_misleading(self, action: Action, obtained_before: frozenset[str]) -> bool:
        by_id = self.scenario.evidence_by_id
        targets = set(action.investigates)
        if not targets:
            return False
        for eid in obtained_before:
            ev = by_id.get(eid)
            if ev is not None and ev.misleading and targets & set(ev.points_to):
                return True
        return False

    def _advance(self) -> None:
        nxt = self._next_phase()
        if nxt is None:
            raise InvalidDecision("これが最終フェーズです")
        if nxt.requires_assessment and self.state.assessment_snapshot is None:
            raise AssessmentRequired(nxt.id)
        self.state.current_phase = nxt.id
        self.state.phase_transitions[nxt.id] = self.state.elapsed_minutes

    def _declare(self, assessment: list[str]) -> None:
        asset_ids = set(self.scenario.asset_by_id)
        unknown = [a for a in assessment if a not in asset_ids]
        if unknown:
            raise InvalidDecision(f"未定義の資産: {unknown}")
        # 重複を落として順序は保つ
        seen: set[str] = set()
        cleaned = [a for a in assessment if not (a in seen or seen.add(a))]
        self.state.assessment = cleaned

        if self.state.assessment_snapshot is None:
            # 宣言時点のスナップショットが採点の基準点になる（SPEC 3.6）
            self.state.assessment_snapshot = AssessmentSnapshot(
                at_minute=self.state.elapsed_minutes,
                assessed_assets=list(cleaned),
                unresolved_critical=questions_mod.unresolved_critical(
                    self.scenario, self.state.obtained_evidence
                ),
                obtained_evidence=list(self.state.obtained_evidence),
            )
            # 宣言を要求したフェーズへそのまま進む
            nxt = self._next_phase()
            if nxt is not None and nxt.requires_assessment:
                self.state.current_phase = nxt.id
                self.state.phase_transitions[nxt.id] = self.state.elapsed_minutes

    def _finish(self) -> None:
        if self.state.assessment_snapshot is None:
            # 被疑判定がないと事実認識層が採点できない（SPEC 6.7）
            raise AssessmentRequired(self.state.current_phase)
        self.state.finished = True

    # ── 派生値 ──

    def business_impact(self, at_minute: int | None = None) -> float:
        """停止した資産の業務影響（復旧地平込み。SPEC 6.3）。

        封じ込めは終盤に実行されるため、演習の終了時刻までで数えると
        常にほぼ 0 になる。復旧までの時間 H を足して評価する。
        """
        now = self.state.elapsed_minutes if at_minute is None else at_minute
        horizon = self.scenario.scoring.consequence_layer.business_impact_horizon_minutes
        assets = self.scenario.asset_by_id
        total = 0.0
        for asset_id, entered in self.state.contained_at.items():
            asset = assets.get(asset_id)
            if asset is None:
                continue
            minutes = max(0, (now + horizon) - entered)
            total += asset.business_impact_per_hour * minutes / 60.0
        return total

    def violations(self, policy_id: str | None = None) -> list[ConstraintViolation]:
        pid = policy_id or self.state.policy_id
        return list(self.state.violations_by_policy.get(pid, []))

    def next_phase_requires_assessment(self) -> bool:
        nxt = self._next_phase()
        return bool(nxt and nxt.requires_assessment and self.state.assessment_snapshot is None)

    # ── ビュー（SPEC 7.4） ──

    def view(self) -> PlayerView:
        return build_view(self.state, self.scenario, self.state.assist_level, self.profile)


def build_view(
    state: GameState,
    scenario: Scenario,
    level: AssistLevel,
    profile: AssistProfile,
) -> PlayerView:
    """アシストレベルによる分岐はここだけに閉じる。

    エンジン本体、採点、講評生成はアシストレベルを一切見ない（SPEC 3.10）。
    """
    resolved_set = set(state.resolved_questions)

    questions = None
    if profile.show_open_questions:
        questions = [
            QuestionView(
                id=q.id,
                label=q.label,
                question=q.question,
                resolved=(q.id in resolved_set)
                if profile.show_question_resolution
                else None,
            )
            for q in scenario.open_questions
        ]

    unresolved_count = None
    if profile.show_assessment_warning:
        unresolved_count = sum(
            1 for q in scenario.open_questions if q.id not in resolved_set
        )

    by_id = scenario.evidence_by_id
    evidence = [
        EvidenceView(
            id=eid,
            summary=by_id[eid].summary if profile.show_evidence_summary else None,
            content=by_id[eid].content,
            reading=(by_id[eid].reading or None)
            if profile.show_evidence_reading
            else None,
            at_minute=state.obtained_at.get(eid, 0),
        )
        for eid in state.obtained_evidence
        if eid in by_id
    ]

    unlocked = scenario.phases_up_to(state.current_phase)
    phase_labels = {p.id: p.label for p in scenario.phases}
    got = set(state.obtained_evidence)
    # 手がかりの無いアクションは一覧に出さない。灰色で見せると
    # 「まだ知らない資産が存在する」ことを教えてしまう
    actions = [
        ActionView(
            id=a.id,
            label=a.label,
            description=(a.description or None)
            if profile.show_action_description
            else None,
            type=a.type,
            cost_minutes=a.cost_minutes,
            executed=(a.id in state.executed_actions),
            selectable=(a.repeatable or a.id not in state.executed_actions),
            phase=a.phase,
            phase_label=phase_labels.get(a.phase, a.phase),
            group=a.group or phase_labels.get(a.phase, a.phase),
        )
        for a in scenario.actions
        if a.phase in unlocked and _unlocked_by(a, got)
    ]

    phase_ids = [p.id for p in scenario.phases]
    idx = phase_ids.index(state.current_phase)
    current = scenario.phases[idx]
    nxt = scenario.phases[idx + 1] if idx + 1 < len(scenario.phases) else None

    policy = scenario.policy_by_id[state.policy_id]

    return PlayerView(
        scenario_title=scenario.meta.title,
        policy_label=policy.label,
        policy_briefing=policy.briefing,
        assist_level=level,
        show_primer=profile.show_primer,
        show_topology=profile.show_topology,
        elapsed_minutes=state.elapsed_minutes,
        accumulated_damage=state.accumulated_damage,
        accumulated_business_impact=state.accumulated_business_impact,
        current_phase=state.current_phase,
        current_phase_label=current.label,
        phases=[
            PhaseView(
                id=p.id,
                label=p.label,
                done=(i < idx),
                current=(i == idx),
                requires_assessment=p.requires_assessment,
            )
            for i, p in enumerate(scenario.phases)
        ],
        advance_label=current.advance_label if nxt is not None else None,
        next_phase_requires_assessment=bool(
            nxt is not None
            and nxt.requires_assessment
            and state.assessment_snapshot is None
        ),
        assets=[
            AssetView(
                id=a.id,
                label=a.label,
                criticality=a.criticality,
                depends_on=list(a.depends_on),
            )
            for a in scenario.world.assets
        ],
        obtained_evidence=evidence,
        open_questions=questions,
        unresolved_count=unresolved_count,
        damage_history=state.damage_history if profile.show_damage_graph else None,
        available_actions=actions,
        current_assessment=list(state.assessment),
        finished=state.finished,
    )
