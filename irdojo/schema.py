"""シナリオ DSL の Pydantic モデル（SPEC 7.1）。

エンジンはこのモジュールの型だけを見る。攻撃手法名や資産名といった
ドメイン知識は一切持たない（SPEC Part 4 原則1）。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.4"


# ─────────── 列挙型 ───────────


class Criticality(str, Enum):
    """業務上の重要度＝**その資産が止まったときに業務が受ける影響の大きさ**。

    学習者に見せるのはこちらで、実際に採点へ効く
    `business_impact_per_hour` は渡さない（数字を出すと判断が算術になる）。
    したがって両者が矛盾していると、**表示が学習者に嘘をつく。**
    ローダが逆転を拒否する（_reject_criticality_inversion）。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


CRITICALITY_RANK = {
    Criticality.LOW: 0,
    Criticality.MEDIUM: 1,
    Criticality.HIGH: 2,
    Criticality.CRITICAL: 3,
}


class ActionType(str, Enum):
    INVESTIGATE = "investigate"
    CONTAIN = "contain"
    # 現場に指示を出して、世界の側のふるまいを変える。
    # 報告（適時性で測る類い）は依然として範囲外（SPEC 9.3）
    COMMUNICATE = "communicate"


class AssistLevel(str, Enum):
    ASSISTED = "assisted"
    STANDARD = "standard"
    HARD = "hard"


class Strict(BaseModel):
    """未知のキーを拒否する基底。

    シナリオは信頼できない入力である（SPEC 原則5）。タイポを黙って
    無視すると、作者が意図した挙動と実際の挙動がずれたまま公開される。
    """

    model_config = ConfigDict(extra="forbid")


# ─────────── world ───────────


class Asset(Strict):
    id: str
    label: str
    criticality: Criticality
    depends_on: list[str] = []
    business_impact_per_hour: float = Field(default=0.0, ge=0.0)


class GroundTruth(Strict):
    """学習者には最後まで見えない。採点にのみ使う（SPEC 3.2）。"""

    patient_zero: str
    compromised: list[str]
    persistence: list[str] = []
    innocent: list[str] = []
    attack_narrative: str = ""


class World(Strict):
    assets: list[Asset]
    ground_truth: GroundTruth


# ─────────── policies ───────────


class Constraint(Strict):
    type: str
    message: str = ""
    assets: list[str] = []
    action_type: ActionType | None = None
    action_ids: list[str] = []
    prerequisite_tags: list[str] = []
    threshold: float | None = None


class Policy(Strict):
    id: str
    label: str
    briefing: str
    weights: dict[str, float]
    constraints: list[Constraint] = []

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "Policy":
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"{self.id}: weights の合計が 1.0 でない ({total})")
        return self


# ─────────── evidence / questions ───────────


class Evidence(Strict):
    id: str
    points_to: list[str]
    misleading: bool = False
    plausibility: Literal["low", "medium", "high"] = "medium"
    volatile: bool = False
    summary: str
    content: str
    # ログの読み方。フィールドの意味、書式、一般的な相場観まで。
    # 「だからこの資産は侵害されている」という推論は書かない（それは答え）。
    reading: str = ""
    refuted_by: list[str] = []


class OpenQuestion(Strict):
    id: str
    label: str
    question: str
    resolved_by: list[str]
    resolution_mode: Literal["any", "all"] = "any"
    critical: bool
    implication: str


# ─────────── actions / phases ───────────


class SideEffects(Strict):
    business_impact: bool = False


class Action(Strict):
    id: str
    label: str
    phase: str
    type: ActionType
    tags: list[str] = []
    # 何をする作業なのか。手段の説明であって、何が見つかるかは書かない。
    description: str = ""
    # 実行の様子。実際に流すコマンドと、その手応え（件数・所要）を書く。
    # 実環境は作らない（SPEC 1.4）が、**何をしたのか**は見せる。
    # 見つかったことは書かない — それは証拠の側の仕事。
    # ラベルが言っていること以上を言わない（group と同じ規則）。
    command: str = ""
    # 一覧での並べ分け。作者が付ける表示用の見出しで、採点には一切効かない。
    # エンジンが investigates から推測しない（原則1：中身を知らない）。
    # 「無関係な端末」のような、答えを漏らす分類名を付けないこと（SPEC 5.6）。
    group: str = ""
    cost_minutes: int = Field(gt=0)
    # このアクションに辿り着くための手がかり。いずれか1つを持っていれば解放される。
    # 空なら最初から選べる。知らない資産は調べられない、という当たり前を表す。
    requires_evidence: list[str] = []
    investigates: list[str] = []
    targets: list[str] = []
    yields: list[str] = []
    destroys: list[str] = []
    # communicate 専用。以後この出来事は起きなくなる（既に起きた分には効かない）。
    # 学習者には渡さない。ラベルが世界の言葉で何をするかを言えば足りる
    prevents: list[str] = []
    repeatable: bool = False
    side_effects: SideEffects = SideEffects()

    @model_validator(mode="after")
    def contain_requires_targets(self) -> "Action":
        if self.type == ActionType.CONTAIN and not self.targets:
            raise ValueError(f"{self.id}: contain アクションには targets が必要")
        # 封じ込めは手がかりで縛らない。証拠なしに止めることは「できる」べきで、
        # その当否は方針の制約で測る（SPEC 原則2 / 3.8）
        if self.type == ActionType.CONTAIN and self.requires_evidence:
            raise ValueError(
                f"{self.id}: contain に requires_evidence は使えない。"
                "封じ込めの当否は方針の制約で測る"
            )
        if self.type == ActionType.COMMUNICATE:
            # 連絡は「何かを起きなくする」ためだけにある。
            # 何も防がない連絡は、時間を溶かすだけのボタンになる
            if not self.prevents:
                raise ValueError(
                    f"{self.id}: communicate には prevents が必要。"
                    "何も防がない連絡は時間を溶かすだけのボタンになる"
                )
            for field in ("yields", "destroys", "targets", "investigates"):
                if getattr(self, field):
                    raise ValueError(f"{self.id}: communicate に {field} は使えない")
        elif self.prevents:
            raise ValueError(f"{self.id}: prevents は communicate 専用")
        return self


class TimelineEvent(Strict):
    """時間が経つと向こうから入ってくること。

    インシデントは、こちらが調べている間も進む。
    押さない限り何も起きない演習は、時間の重みが数値でしか伝わらない。

    **証拠にはしない。** 論点も解消しないし、資産も指さない。
    向こうから来るものが答えを運ぶと、待つのが得になってしまう。
    運ぶのは圧力だけ。

    **ただし圧力は、痛みを伴わなければただの装飾である。**
    盤面を何も変えない出来事は、赤い枠と札を付けた飾りでしかなく、
    学習者は数手で読まなくなる。だから出来事は必ず**何かを奪う**か、
    **費用が変わったことを知らせる**かのどちらかでなければならない
    （`destroys` / `heralds`。ローダが両方とも無い出来事を拒否する）。

    奪う方向にしか働かないので、待つのが得になることはない。
    """

    id: str
    at_minutes: int = Field(gt=0)
    label: str                      # 「監視センターから追加連絡」
    text: str

    # 世界の側で失われるもの。アクションの destroys と意味は同じで、
    # **以後取得できなくなる**だけ。取得済みのものは消えない
    destroys: list[str] = []

    # 折れ点の前触れ。被害の増え方が変わることを、数字ではなく人の声で知らせる。
    # 折れ点そのものは伏せたままなので（7.6.9）、これが唯一の予告になる
    heralds: Literal["acceleration"] | None = None

    # 先手を打たれていた場合に、代わりに聞こえること。
    # 出来事そのものは起きる（人は動く）が、何も失われない。
    # 「何も起きなかった」にすると、打った手が効いたことが学習者に伝わらない
    averted_label: str = ""
    averted_text: str = ""


class Phase(Strict):
    id: str
    label: str
    advance_label: str | None = None
    requires_assessment: bool = False


# ─────────── damage ───────────


class Acceleration(Strict):
    threshold_minutes: int = Field(gt=0)
    multiplier: float = Field(default=1.0, gt=0.0)


class ContainmentEffect(Strict):
    on_correct_containment: float = Field(default=0.2, ge=0.0)
    on_partial: float = Field(default=0.6, ge=0.0)
    on_incorrect: float = Field(default=1.0, ge=0.0)


class DamageModel(Strict):
    model: Literal["linear", "step", "exponential"] = "exponential"
    params: dict[str, float | list[float]] = {}
    acceleration: Acceleration | None = None
    containment_effect: ContainmentEffect = ContainmentEffect()


# ─────────── scoring ───────────


class MetricSpec(Strict):
    id: str
    label: str
    direction: Literal["lower_is_better", "higher_is_better"]
    weight_multiplier: float = 1.0


class FactLayer(Strict):
    metrics: list[MetricSpec]
    weights: dict[str, float]

    @model_validator(mode="after")
    def weights_match_metrics(self) -> "FactLayer":
        ids = {m.id for m in self.metrics}
        unknown = set(self.weights) - ids
        if unknown:
            raise ValueError(f"fact_layer.weights に未定義の指標: {sorted(unknown)}")
        missing = ids - set(self.weights)
        if missing:
            raise ValueError(f"fact_layer.weights に重みがない指標: {sorted(missing)}")
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"fact_layer.weights の合計が 1.0 でない ({total})")
        return self


class PolicyLayer(Strict):
    constraint_penalty: float = Field(default=0.15, ge=0.0, le=1.0)


class NormBounds(Strict):
    worst: float
    best: float

    @model_validator(mode="after")
    def bounds_differ(self) -> "NormBounds":
        if self.worst == self.best:
            raise ValueError("normalization の worst と best が同値では正規化できない")
        return self


class ConsequenceLayer(Strict):
    display_only: list[str]
    normalization: dict[str, NormBounds]
    business_impact_horizon_minutes: int = Field(default=480, gt=0)


class Composite(Strict):
    method: Literal["weighted_sum", "multiplicative"] = "weighted_sum"
    fact_weight: float = Field(default=0.7, ge=0.0, le=1.0)


class Scoring(Strict):
    fact_layer: FactLayer
    policy_layer: PolicyLayer = PolicyLayer()
    consequence_layer: ConsequenceLayer
    composite: Composite = Composite()


# ─────────── retrospective / debrief ───────────


class RetroBlock(Strict):
    compute: bool = True
    label: str = ""
    disclaimer: str = ""


class Retrospective(Strict):
    minimal_path: RetroBlock = RetroBlock()
    counterfactuals: RetroBlock = RetroBlock()
    unresolved_review: RetroBlock = RetroBlock()


class ReplaySuggestion(Strict):
    policy: str
    hint: str


class Debrief(Strict):
    reveal_ground_truth: bool = True
    misleading_explanations: dict[str, str] = {}
    key_lessons: list[str] = []
    replay_suggestions: list[ReplaySuggestion] = []


# ─────────── assist level ───────────


class AssistProfile(Strict):
    """アシストの表示可否。採点には一切関与しない（SPEC 3.10）。"""

    show_open_questions: bool = True
    show_question_resolution: bool = True
    show_assessment_warning: bool = True
    show_damage_graph: bool = True
    show_evidence_summary: bool = True
    show_action_description: bool = True   # 何をする作業か
    show_evidence_reading: bool = True     # ログの読み方
    show_primer: bool = True               # 考え方の枠組み（はじめての人へ）
    show_topology: bool = True             # 環境の構成図
    #
    # 図を出さないことと、依存関係を隠すことは別である。
    # 図は消化済みの見せ方なのでアシストだが、依存関係そのものは環境の事実であり、
    # 隠すと「構成を知らない人だけが上流を止めて事故る」という知識の測定になる。
    # `hard` では図をやめ、資産一覧に依存先をテキストで併記する（7.6.11）。


DEFAULT_PROFILES: dict[AssistLevel, AssistProfile] = {
    AssistLevel.ASSISTED: AssistProfile(),
    AssistLevel.STANDARD: AssistProfile(
        show_question_resolution=False,
        show_evidence_reading=False,
    ),
    AssistLevel.HARD: AssistProfile(
        show_open_questions=False,
        show_question_resolution=False,
        show_assessment_warning=False,
        show_damage_graph=False,
        show_evidence_summary=False,
        show_action_description=False,
        show_evidence_reading=False,
        show_primer=False,
        show_topology=False,
    ),
}


# ─────────── complexity（SPEC 3.12） ───────────

PLAUSIBILITY_SCORE = {"low": 0, "medium": 1, "high": 2}


def bearing_evidence(sc: "Scenario") -> list["Evidence"]:
    """事案の判断に効く証拠。

    どの資産も指さず、どの論点も解消せず、どの誤導も棄却しない証拠
    （「バックアップは健全」「特権グループに変更なし」といったネガティブ所見）は
    読む量は増やすが、推論を難しくはしない。複雑度の材料から外す。
    """
    resolving = {e for q in sc.open_questions for e in q.resolved_by}
    refuting = {r for e in sc.evidence for r in e.refuted_by}
    return [
        e
        for e in sc.evidence
        if e.points_to or e.id in resolving or e.id in refuting
    ]


def compute_complexity(sc: "Scenario") -> int:
    """シナリオ内容から 1〜5 を算出する。YAML には書かせない。"""
    misleading = [e for e in sc.evidence if e.misleading]
    crit = [q for q in sc.open_questions if q.critical]
    multi_step = [e for e in misleading if len(e.refuted_by) >= 2]
    raw = (
        1.0 * len(misleading)
        + 0.5 * sum(PLAUSIBILITY_SCORE[e.plausibility] for e in misleading)
        + 0.8 * len(crit)
        + 0.3 * (len(bearing_evidence(sc)) / 5)
        + 1.0 * len(multi_step)
    )
    return max(1, min(5, round(raw / 3.0)))


# ─────────── meta / root ───────────


class Meta(Strict):
    id: str
    title: str
    version: str = "1.0.0"
    author: str = ""
    license: str = "CC-BY-4.0"
    estimated_play_minutes: int = 10
    language: str = "ja"
    default_policy: str
    default_assist_level: AssistLevel = AssistLevel.ASSISTED
    briefing: str
    tags: list[str] = []


class Scenario(Strict):
    schema_version: str
    meta: Meta
    world: World
    policies: list[Policy]
    evidence: list[Evidence]
    open_questions: list[OpenQuestion]
    actions: list[Action]
    phases: list[Phase]
    timeline: list[TimelineEvent] = []
    damage: DamageModel
    scoring: Scoring
    assist_profiles: dict[AssistLevel, AssistProfile] = {}
    retrospective: Retrospective = Retrospective()
    debrief: Debrief = Debrief()

    def profile(self, level: AssistLevel) -> AssistProfile:
        """シナリオ側の上書きがあればそれを、なければ既定値を返す。"""
        return self.assist_profiles.get(level, DEFAULT_PROFILES[level])

    @property
    def complexity(self) -> int:
        return compute_complexity(self)

    # 検索用の索引。バリデーション後に一度だけ組む。
    @property
    def asset_by_id(self) -> dict[str, Asset]:
        return {a.id: a for a in self.world.assets}

    @property
    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}

    @property
    def action_by_id(self) -> dict[str, Action]:
        return {a.id: a for a in self.actions}

    @property
    def question_by_id(self) -> dict[str, OpenQuestion]:
        return {q.id: q for q in self.open_questions}

    @property
    def policy_by_id(self) -> dict[str, Policy]:
        return {p.id: p for p in self.policies}

    def phases_up_to(self, phase_id: str) -> set[str]:
        """解放は累積（SPEC 3.8）。現フェーズまでの全フェーズ id を返す。"""
        out: set[str] = set()
        for p in self.phases:
            out.add(p.id)
            if p.id == phase_id:
                break
        return out

    @model_validator(mode="after")
    def check_references(self) -> "Scenario":
        _validate_scenario(self)
        return self


def _validate_scenario(sc: "Scenario") -> None:
    """構造要件と参照整合性（SPEC 7.1）。"""
    asset_ids = {a.id for a in sc.world.assets}
    ev_ids = {e.id for e in sc.evidence}
    act_ids = {a.id for a in sc.actions}
    act_tags = {t for a in sc.actions for t in a.tags}
    phase_ids = {p.id for p in sc.phases}
    pol_ids = {p.id for p in sc.policies}

    _reject_duplicates("資産", [a.id for a in sc.world.assets])
    _reject_duplicates("証拠", [e.id for e in sc.evidence])
    _reject_duplicates("アクション", [a.id for a in sc.actions])
    _reject_duplicates("論点", [q.id for q in sc.open_questions])
    _reject_duplicates("フェーズ", [p.id for p in sc.phases])
    _reject_duplicates("時間経過の出来事", [e.id for e in sc.timeline])
    _reject_duplicates("方針", [p.id for p in sc.policies])

    # 構造要件
    if not sc.phases:
        raise ValueError("phases が空です")
    if len(sc.policies) < 2:
        raise ValueError("policies は最低2つ必要（方針切り替えの体験のため）")
    if sc.meta.default_policy not in pol_ids:
        raise ValueError(f"未定義の default_policy: {sc.meta.default_policy}")
    if not any(e.misleading for e in sc.evidence):
        raise ValueError("誤導証拠が定義されていません")
    if not any(q.critical for q in sc.open_questions):
        raise ValueError("critical な未解消論点が定義されていません")
    if not any(p.requires_assessment for p in sc.phases):
        raise ValueError("被疑判定を求めるフェーズがありません")

    # ground_truth（SPEC 6.7 の端値: compromised が空はローダで拒否）
    gt = sc.world.ground_truth
    if not gt.compromised:
        raise ValueError("compromised が空です")
    for field in ("compromised", "persistence", "innocent"):
        for a in getattr(gt, field):
            if a not in asset_ids:
                raise ValueError(f"ground_truth.{field}: 未定義の資産 {a}")
    if gt.patient_zero not in asset_ids:
        raise ValueError(f"ground_truth.patient_zero: 未定義の資産 {gt.patient_zero}")
    overlap = set(gt.compromised) & set(gt.innocent)
    if overlap:
        raise ValueError(f"compromised と innocent が重複: {sorted(overlap)}")
    stray = set(gt.persistence) - set(gt.compromised)
    if stray:
        raise ValueError(f"persistence が compromised に含まれない: {sorted(stray)}")

    # 資産の依存関係
    for a in sc.world.assets:
        for d in a.depends_on:
            if d not in asset_ids:
                raise ValueError(f"{a.id}: 未定義の依存先 {d}")
            if d == a.id:
                raise ValueError(f"{a.id}: 自分自身に依存しています")
    _reject_dependency_cycle(sc.world.assets)

    # 証拠
    for e in sc.evidence:
        for a in e.points_to:
            if a not in asset_ids:
                raise ValueError(f"{e.id}: 未定義の資産 {a}")
        for r in e.refuted_by:
            if r not in ev_ids:
                raise ValueError(f"{e.id}: 未定義の証拠 {r}")
            if r == e.id:
                raise ValueError(f"{e.id}: 自分自身を refuted_by に指定しています")
        if e.misleading and not e.refuted_by:
            raise ValueError(f"{e.id}: 誤導証拠に refuted_by がありません（棄却不能）")

    # 論点
    for q in sc.open_questions:
        if not q.resolved_by:
            raise ValueError(f"{q.id}: resolved_by が空です（解消不能）")
        for r in q.resolved_by:
            if r not in ev_ids:
                raise ValueError(f"{q.id}: 未定義の証拠 {r}")

    # アクション
    tl_ids = {e.id for e in sc.timeline}
    for act in sc.actions:
        for tid in act.prevents:
            if tid not in tl_ids:
                raise ValueError(f"{act.id}: 未定義の出来事を prevents しています: {tid}")
        if act.phase not in phase_ids:
            raise ValueError(f"{act.id}: 未定義のフェーズ {act.phase}")
        for e in act.yields + act.destroys:
            if e not in ev_ids:
                raise ValueError(f"{act.id}: 未定義の証拠 {e}")
        for a in act.investigates + act.targets:
            if a not in asset_ids:
                raise ValueError(f"{act.id}: 未定義の資産 {a}")
        for e in act.requires_evidence:
            if e not in ev_ids:
                raise ValueError(f"{act.id}: 未定義の証拠 {e}")
            if e in act.yields:
                raise ValueError(
                    f"{act.id}: 自分が産む証拠 {e} を前提にしています（永久に解放されない）"
                )

    # どのアクションからも得られない証拠は死蔵。論点の解消が不可能になる
    produced = {e for a in sc.actions for e in a.yields}
    orphan = ev_ids - produced
    if orphan:
        raise ValueError(f"どのアクションからも得られない証拠: {sorted(orphan)}")

    # 方針
    for p in sc.policies:
        for c in p.constraints:
            for a in c.assets:
                if a not in asset_ids:
                    raise ValueError(f"{p.id}: 未定義の資産 {a}")
            for aid in c.action_ids:
                if aid not in act_ids:
                    raise ValueError(f"{p.id}: 未定義のアクション {aid}")
            for t in c.prerequisite_tags:
                if t not in act_tags:
                    raise ValueError(f"{p.id}: どのアクションにも無いタグ {t}")

    # 採点設定と方針の重みが同じ指標名を指しているか
    norm_keys = set(sc.scoring.consequence_layer.normalization)
    for p in sc.policies:
        unknown = set(p.weights) - norm_keys
        if unknown:
            raise ValueError(
                f"{p.id}: normalization に無い帰結指標に重みがあります: {sorted(unknown)}"
            )

    _reject_unreachable_actions(sc)
    _reject_criticality_inversion(sc)
    _reject_toothless_timeline(sc)

    # 被害モデルの params
    _validate_damage_params(sc.damage)


def _reject_criticality_inversion(sc: "Scenario") -> None:
    """重要度の順序が business_impact_per_hour の順序と逆転していないか。

    学習者には重要度しか見せない。逆転していると、
    「重要度が高いほうを止めたのに、業務影響は軽かった」という
    表示と実態の食い違いが起き、判断の材料として使えなくなる。
    """
    for a in sc.world.assets:
        for b in sc.world.assets:
            if a.id == b.id:
                continue
            higher = CRITICALITY_RANK[a.criticality] > CRITICALITY_RANK[b.criticality]
            cheaper = a.business_impact_per_hour < b.business_impact_per_hour
            if higher and cheaper:
                raise ValueError(
                    f"重要度と業務影響が逆転しています: "
                    f"{a.id}({a.criticality.value}, {a.business_impact_per_hour}/h) と "
                    f"{b.id}({b.criticality.value}, {b.business_impact_per_hour}/h)。"
                    "学習者には重要度しか見せないため、表示が実態と食い違う"
                )


def _reject_toothless_timeline(sc: "Scenario") -> None:
    """盤面を何も変えない出来事を拒否する。

    圧力だけを運ぶ、という規則（5.10）は「答えを運ばせない」ためのもので、
    「何もさせない」ためのものではなかった。区別を怠ると、赤い枠と札の付いた
    ただの飾りが画面の一等地に居座り、学習者は数手で読まなくなる。

    出来事が持ちうる歯は2種類だけ:
      destroys — 何かを奪う（以後取得できなくなる）
      heralds  — 費用が変わったことを知らせる
    どちらも学習者に**不利にしか**働かないので、待つのが得になることはない。
    """
    ev_ids = {e.id for e in sc.evidence}
    accel = sc.damage.acceleration

    for ev in sc.timeline:
        unknown = [d for d in ev.destroys if d not in ev_ids]
        if unknown:
            raise ValueError(f"{ev.id}: 存在しない証拠を destroys しています: {unknown}")

        if not ev.destroys and ev.heralds is None:
            raise ValueError(
                f"{ev.id}: 盤面を何も変えない出来事です。"
                "destroys で何かを奪うか、heralds で費用の変化を知らせてください"
            )

        if ev.heralds == "acceleration":
            if accel is None:
                raise ValueError(
                    f"{ev.id}: heralds: acceleration ですが damage.acceleration がありません"
                )
            # 前触れは折れ点の手前。過ぎてから言うのは予告ではなく事後報告で、
            # 遠すぎると何の前触れだったか結び付かない
            lo = accel.threshold_minutes - 40
            if not (lo <= ev.at_minutes < accel.threshold_minutes):
                raise ValueError(
                    f"{ev.id}: 折れ点の前触れは {lo}〜{accel.threshold_minutes - 1}分 に置いてください"
                    f"（現在 {ev.at_minutes}分、折れ点 {accel.threshold_minutes}分）"
                )

    preventable = {tid for a in sc.actions for tid in a.prevents}
    for ev in sc.timeline:
        if ev.id in preventable and not ev.averted_text:
            raise ValueError(
                f"{ev.id}: 防がれうる出来事には averted_text が要る。"
                "無言だと、打った手が効いたことが学習者に伝わらない"
            )
        if ev.averted_text and ev.id not in preventable:
            raise ValueError(f"{ev.id}: averted_text がありますが、防ぐ手段がありません")

    times = [e.at_minutes for e in sc.timeline]
    if times != sorted(times):
        raise ValueError("timeline は時刻の昇順で書いてください（読む順序が起きる順序になります）")

    heralds = [e.id for e in sc.timeline if e.heralds == "acceleration"]
    if len(heralds) > 1:
        raise ValueError(f"折れ点の前触れが複数あります: {heralds}")


def _reject_unreachable_actions(sc: "Scenario") -> None:
    """手がかりの連鎖を辿って、全アクションに到達できるかを確かめる。

    到達できないアクションは死蔵であり、そこでしか得られない証拠があると
    論点が解消不能になる。destroys は考えない（最善手を仮定した到達性を見る）。
    """
    reached: set[str] = set()
    evidence: set[str] = set()
    changed = True
    while changed:
        changed = False
        for a in sc.actions:
            if a.id in reached:
                continue
            if a.requires_evidence and not (set(a.requires_evidence) & evidence):
                continue
            reached.add(a.id)
            evidence |= set(a.yields)
            changed = True

    stranded = [a.id for a in sc.actions if a.id not in reached]
    if stranded:
        raise ValueError(
            f"手がかりを辿っても到達できないアクション: {sorted(stranded)}"
        )


def _reject_duplicates(what: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise ValueError(f"{what}の id が重複しています: {i}")
        seen.add(i)


def _reject_dependency_cycle(assets: list[Asset]) -> None:
    """depends_on の循環を拒否する（SPEC 6.3 の不動点展開が止まらなくなる）。"""
    deps = {a.id: list(a.depends_on) for a in assets}
    state: dict[str, int] = {}  # 0=未訪問 1=探索中 2=完了

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = " → ".join(path[path.index(node) :] + [node])
            raise ValueError(f"資産の依存関係が循環しています: {cycle}")
        state[node] = 1
        for d in deps.get(node, []):
            visit(d, path + [node])
        state[node] = 2

    for a in assets:
        visit(a.id, [])


def _validate_damage_params(d: DamageModel) -> None:
    p = d.params
    if d.model in ("exponential", "linear"):
        for key in ("base", "rate"):
            if key not in p:
                raise ValueError(f"damage.params に {key} がありません（model={d.model}）")
            if not isinstance(p[key], (int, float)):
                raise ValueError(f"damage.params.{key} は数値である必要があります")
    elif d.model == "step":
        steps = p.get("steps")
        interval = p.get("interval")
        if not isinstance(steps, list) or not steps:
            raise ValueError("damage.params.steps は空でないリストである必要があります")
        if not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError("damage.params.interval は正の数値である必要があります")
        if "base" not in p or not isinstance(p["base"], (int, float)):
            raise ValueError("damage.params.base がありません（model=step）")
