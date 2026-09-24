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
    names_asset,
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
    # **その瞬間に侵害されていた資産**（SPEC 5.2 / v1.48）。
    # 拡散を持つ盤面では真実そのものが動くので、「判定した時点では
    # 正しかった」を言うには、手元の証拠だけでなく世界の側も
    # 凍らせておく必要がある。講評が「判定時点では N 台、
    # 終了時点では M 台」と言えるのはこの欄があるからである。
    # 拡散の無い盤面では compromised と同じ値が入る
    compromised: list[str] = []


class GameState(BaseModel):
    policy_id: str
    assist_level: AssistLevel
    current_phase: str
    elapsed_minutes: int = 0
    executed_actions: list[str] = []
    # アクションID → 押した時刻（分。実行開始の時点）。繰り返せる手は最初の1回。
    # 講評で「その手は何分遅かったのか」を言うために要る（SPEC 7.6.13）。
    # 学習者自身の履歴なので、これ自体は何も漏らさない
    executed_at: dict[str, int] = {}
    obtained_evidence: list[str] = []
    obtained_at: dict[str, int] = {}  # 証拠ID → 取得した時刻（分）
    destroyed_evidence: list[str] = []
    destroyed_at: dict[str, int] = {}   # 証拠ID → 失われた時刻（分）
    # 先に仕掛けて、時計から守ってある証拠（SPEC 5.6.3）。
    # timeline の destroys はこれを飛ばす。**自分の手の destroys は飛ばさない** —
    # 排他（メモリ vs ディスクイメージ）はそのまま残す
    secured_evidence: list[str] = []
    # 世界の側に奪われたもの。自分の手で壊したものとは講評での言い方が変わる
    destroyed_by_world: dict[str, str] = {}   # 証拠ID → 出来事ID
    # 破壊された証拠のうち、取得する前に失ったもの。
    # 取得済みのものは失われない（見たことは忘れない）。講評でのみ開示する。
    lost_evidence: list[str] = []
    resolved_questions: list[str] = []
    assessment: list[str] = []
    # **直近の宣言の時点で侵害されていた資産**（SPEC 6.2 / v1.48）。
    # 適合率と再現率はここを分母・照合先にする。被疑判定は対応フェーズ中も
    # 出し直せる（3.6）ので、出し直した時点の世界と突き合わせる —
    # 拡散に気づいて名指しを足した学習者が、そのせいで適合率を落とすのは
    # 「気づいたことへの罰」になる。拡散の無い盤面では compromised と同値
    assessment_compromised: list[str] = []
    assessment_snapshot: AssessmentSnapshot | None = None
    # 封じ込めた資産 → その時刻（分）。**直接 targets だけ。依存連鎖しない。**
    # 「どの資産で攻撃が止まるか」の答え。dc01 の電源を落としても
    # ws-042 の Run キーは残るし、fs01 上の暗号化プロセスは動き続ける。
    # containment_completeness と 5.8 の減衰判定はこちらを使う（SPEC 6.3）
    contained_at: dict[str, int] = {}
    # 永続化を取り除いた資産 → その時刻（分）。**依存連鎖しない。**
    # 根絶は個別の資産に手を当てる作業で、上流を作り直しても
    # 下流に残された Run キーは消えない。5.8 の減衰判定が
    # `persistence ⊆ eradicated` でこちらを見る（SPEC 5.8 / 6.3）
    eradicated_at: dict[str, int] = {}
    # 業務が止まった資産 → その時刻（分）。side_effects.business_impact が
    # true の手だけが入り、depends_on で不動点まで展開される。
    # 「どの資産で仕事ができなくなるか」の答え。business_impact だけが使う
    halted_at: dict[str, int] = {}
    # 戻した資産 → その時刻（SPEC 5.11）。業務影響はここで止まる
    restored_at: dict[str, int] = {}
    # 塞いだ入り口 → その時刻（SPEC 5.12）。
    # **盤面の上では何も起きない。** 効くのは演習が終わったあとで、
    # 見えるのは講評の破線（復旧地平まで伸ばした見込み）だけである
    hardened_at: dict[str, int] = {}
    # **戻した先で攻撃が再開した資産** → その時刻。
    # 取り除けていない資産を戻すと、そこで起きる。
    # これは学習者に**その場では告げない**（5.9）— 講評で初めて出る
    recompromised_at: dict[str, int] = {}
    phase_transitions: dict[str, int] = {}
    # 方針ごとに違反を記録する。同じ行動列を別方針で採点したとき、
    # その方針の制約で評価し直せるようにするため（SPEC 6.8）。
    # 学習者に見せるのは自分の方針の分だけ、しかも講評でのみ。
    violations_by_policy: dict[str, list[ConstraintViolation]] = {}
    accumulated_damage: float = 0.0
    accumulated_business_impact: float = 0.0
    damage_history: list[float] = []  # 1分刻みの累積被害。グラフ描画用
    # 誤導を追いかけた時間と、調べた時間の合計。**採点には入らない**
    # （v1.36 で misled_score を廃止した）。講評が平文で返すだけ（SPEC 6.6）。
    # ビューには出さない — プレイ中に「今のは外れだった」と告げるのと同じになる
    investigation_minutes: int = 0
    misled_follow_minutes: int = 0
    # 演習中に侵害された資産 → その時刻（分）。SPEC 5.2 の `spreads`。
    # **真実が動くのはここだけである。** compromised は開始時点の集合で、
    # 採点が見る集合は `compromised_now` がこの2つから組む
    spread_at: dict[str, int] = {}
    spreads_fired: list[str] = []      # 実際に配られた窓
    spreads_averted: list[str] = []    # 配り元が止まっていて何も起きなかった窓
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
    # 封じ込めで新たに止まった資産（直接 targets）。
    # 何をしたのかを告げないと、押しても無言になる。
    contained: list[str] = []
    # 永続化を取り除いた資産。止めることと作り直すことは別の作業なので、
    # 別の箱で返す。押した直後にその場で区別できないと、
    # 学習者は「隔離した」と「作り直した」を同じことだと受け取る
    eradicated: list[str] = []
    # 業務が止まった資産。依存で波及した分を含む。
    # 止めることと業務が止まることは別なので、別の箱で返す（SPEC 6.3）
    halted: list[str] = []
    cascaded: list[str] = []          # そのうち依存で巻き込まれた分
    # 業務に戻した資産（SPEC 5.11）。**戻したことだけを言う。**
    # 戻した先で攻撃が再開したかどうかは、ここでは返さない — 講評で初めて出る（5.9）
    restored: list[str] = []
    # 塞いだ入り口（SPEC 5.12）。**塞いだことだけを言う。**
    # それで見込みがどれだけ減ったかは、講評で初めて出る
    hardened: list[str] = []
    # 押す前から既に止まっていた targets。**空振りではない。**
    # 同じ資産に2つの手があるので、2手目はここに落ちる（例: 境界で遮断した
    # あとに電源を落とす）。数えないと「何も出てこなかった」が出る
    already: list[str] = []
    business_impact_delta: float = 0.0
    events: list[str] = []            # この間に向こうから入ってきたこと
    averted: list[str] = []           # そのうち、先手が効いて不発に終わった分
    # 連絡で「以後起きなくなる」ようにしたもの。押しても無言にしないため
    prevented: list[str] = []
    # どの型の手だったか。**連絡は空振りにならない**（下記）
    kind: ActionType = ActionType.INVESTIGATE
    # 保全の手だったか（`secures` を持つ手）。**件数ではなく真偽で持つ。**
    # 件数にすると 0 と 1 の差が「間に合ったか」の答えになり、
    # プレイ中に賭けの結果を告げることになる（原則5）
    preserves: bool = False

    @property
    def empty(self) -> bool:
        # 出来事は世界の側の話。押した結果が空振りだったかとは別に数える。
        # 45分かけて何も出ず、その間に電話が鳴った、は両方起きうる。
        #
        # **連絡は決して空振りにしない。** 周知は出したのだから、
        # 世界の側で起きたことは press した時点で確定している。
        # `prevented` が空になるのは「もう防ぐものが残っていない」ときだけで、
        # そこで「何も出てこなかった」と出すと、**間に合わなかったことを
        # プレイ中に告げる**ことになる（原則5）。速さへの賭けに負けたことは、
        # 講評まで開かない。文言も勝った回と揃える。
        if self.kind == ActionType.COMMUNICATE:
            return False
        # **保全の手も決して空振りにしない。** 仕掛けは証拠を1件も産まない
        # （産むのは後で読む手のほう）ので、ここを見落とすと
        # 20分をかけて「何も出てこなかった」と返ることになる。
        # しかも既に奪われていた回だけ文言が変わると、それが
        # 「間に合わなかった」の合図になる — 連絡と同じ理由で揃える
        if self.preserves:
            return False
        return (
            not self.revealed
            and not self.contained
            and not self.eradicated
            and not self.halted
            and not self.already
            and not self.prevented
            # 戻した回は空振りではない（SPEC 5.11）。
            # 業務が戻っているのに「何も出てこなかった」と返ると、
            # **押した結果が見えないまま盤面だけが変わる**
            and not self.restored
            # 塞いだ回も空振りではない。盤面は動かないが、
            # **先の見込みは動いている**（SPEC 5.12）
            and not self.hardened
        )


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

    misleading / refuted_by / refutation_mode / points_to を含めない。
    points_to は「この証拠がどの資産を示唆するか」という構造化された答えであり、
    summary より強いヒントになる。
    """

    id: str
    summary: str | None  # hard では None
    content: str
    reading: str | None  # ログの読み方。assisted のみ
    # 考えられること。assisted のみ。**空のリストと None を区別する** —
    # None は「このレベルでは出さない」、[] は「この証拠には書かれていない」。
    # 画面はどちらでも欄を出さないが、混ぜると
    # 「hard なのに欄が出ない」のか「欄が空なのか」を計器が分けられない
    possibilities: list[str] | None
    # **事件の時計**（SPEC 7.6.17）。この資料が記録している出来事の時刻。
    # `content` に書かれている時刻の再掲なので、**どのレベルでも出す** —
    # hard で伏せるのは要約であって、生ログの中身ではない。
    # 持たない資料（台帳・期間の集計・聞き取り）では空文字になる。
    occurred_at: str
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
    """資産。被疑判定の選択肢であり、資産盤（7.6.16）の材料でもある。

    `depends_on` は隠さない。自組織の構成は担当者が知っていて当然のもので、
    伏せると「構成を知らない人だけが上流を止めて痛い目を見る」という、
    判断ではなく知識を測る形になる（3.10）。
    `business_impact_per_hour` は採点の内部値なので渡さない。

    後半の5つは**注意の配分の写像**である（7.6.16）。真実は一切使わない —
    どれも「学習者が何をしたか」「学習者が手元に何を持っているか」の数え直しで、
    盤面の側の事実は1つも入っていない。
    """

    id: str
    label: str
    criticality: Criticality
    depends_on: list[str]

    # 自分が実行した手のうち、この資産に手を当てた数（targets ∪ investigates
    # ∪ eradicates）。**押した手だけを数える。** 押していない手の射程は
    # 決して漏らさないので、開始時点では全資産が 0 になる。
    # 自分の履歴を数え直した消化なので、hard では None（3.10）
    touched: int | None
    # 手元の証拠のうち、本文がこの資産を名指ししている数。
    # **`points_to` は使わない。** 使うのは `content` の文字列照合だけで、
    # 学習者が自分で Ctrl-F すれば得られるものしか出さない（7.4 / 7.6.16）。
    # hard では None
    mentions: int | None
    # 止めた・取り除いた・業務が止まった。**この3つは常に渡す** —
    # 押した瞬間に `ActionOutcome` が全レベルで告げている事実であり、
    # 盤に出しても新しいことは何も渡していない
    contained: bool
    eradicated: bool
    halted: bool


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


# ─────────── 動く真実（SPEC 5.2 / v1.48） ───────────


def compromised_now(scenario: Scenario, state: GameState) -> list[str]:
    """いま侵害されている資産。開始時点の集合に、演習中に増えた分を足す。

    **並びは資産一覧の順にする。** 講評が「N 台 → M 台」と並べる場所なので、
    増えた分が末尾に固まっていると、どれが増えたのかを順序から読めてしまう。
    """
    base = list(scenario.world.ground_truth.compromised)
    if not state.spread_at:
        return base
    both = set(base) | set(state.spread_at)
    return [a.id for a in scenario.world.assets if a.id in both]


def effective_truth(scenario: Scenario, state: GameState):
    """被害モデルへ渡す ground_truth。侵害の広さだけを今の値に差し替える。

    **`damage` の関数の形は変えない。** あちらは「真実を1つ受け取って
    係数を返す」だけの純関数で、そこに演習の状態を持ち込むと、
    盤面に拡散が無いときの挙動まで新しい経路を通ることになる。
    """
    gt = scenario.world.ground_truth
    if not state.spread_at:
        return gt
    return gt.model_copy(update={"compromised": compromised_now(scenario, state)})


def withheld_evidence(scenario: Scenario, state: GameState) -> set[str]:
    """まだ起きていないので、取りに行っても出てこない資料（SPEC 5.2）。

    拡散が起きて初めて実在する資料である。起きる前に渡すと、盤面は
    **まだ起きていないことを配る**ことになる。防がれた窓の分は永久に
    ここへ残る（起きなかったのだから、どこにも記録は無い）。
    """
    return {
        eid
        for sp in scenario.world.ground_truth.spreads
        if sp.id not in state.spreads_fired
        for eid in sp.reveals
    }


def _unlocked_by(action: Action, obtained: set[str], executed: set[str]) -> bool:
    """その手が今、一覧に出るか。

    門は2種類あり、**意味が違うので合成の仕方も違う**（SPEC 5.6.2）。

      requires_evidence … 手がかりを**1つでも**持っていれば開く。
                          「何が分かったら次を思いつくか」の連鎖
      requires_actions  … 前提の手を**全部**打ち終えていれば開く。
                          「何を先に仕掛けたか」。取っていないイメージは読めない

    どちらも空なら最初から選べる。
    """
    if action.requires_evidence and not (set(action.requires_evidence) & obtained):
        return False
    return set(action.requires_actions) <= executed


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
        # **塞ぐ手を1つも持たない盤面では、塞ぐことを求めない**（SPEC 5.12）。
        # 機構を足しても、既にある盤面の数字は1つも動かさない
        self.must_harden: set[str] = (
            {scenario.world.ground_truth.patient_zero}
            if any(a.hardens for a in scenario.actions) else set()
        )
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
        done = set(self.state.executed_actions)
        return [
            a
            for a in self.scenario.actions
            if a.phase in unlocked and _unlocked_by(a, got, done)
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
        if not _unlocked_by(
            action,
            set(self.state.obtained_evidence),
            set(self.state.executed_actions),
        ):
            raise InvalidDecision(f"まだ手がかりがありません: {action_id}")
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
                if eid in st.secured_evidence:
                    continue  # 先に仕掛けてある。現場が触っても、もう手元にある
                if eid in st.destroyed_evidence:
                    continue
                st.destroyed_evidence.append(eid)
                st.destroyed_at[eid] = ev.at_minutes
                st.destroyed_by_world[eid] = ev.id
                if eid not in st.obtained_evidence:
                    st.lost_evidence.append(eid)
        def _accrue(a: int, b: int) -> None:
            for inc in damage_mod.accrue(
                self.scenario.damage,
                effective_truth(self.scenario, st),
                st.contained_at.keys(),
                st.eradicated_at.keys(),
                a,
                b,
                st.hardened_at.keys(),
                self.must_harden,
            ):
                st.accumulated_damage += inc
                st.damage_history.append(st.accumulated_damage)

        # 拡散（SPEC 5.2）。**配る窓は、時計を進めた区間の中で通り過ぎる。**
        # 配り元が既に止まっていれば、その窓では何も起きない。
        # 止め終わったのが窓より後なら間に合っていない — 連絡（5.6.1）と
        # 同じで、効くのは打ち終わった時刻からである。
        #
        # **学習者には何も告げない**（原則5）。盤が動いたことは、あとで
        # 資料を引き直したときに初めて分かる。ここで知らせると、
        # 待っているだけで答えの一部が届くことになる（5.10 と同じ理由）。
        #
        # 被害の積分は窓で切る。区間の頭から終わりまでを新しい広さで
        # 数えると、まだ配られていない時間まで拡散の分を払わせることになる。
        cursor = start
        for sp in sorted(
            (
                sp
                for sp in self.scenario.world.ground_truth.spreads
                if start < sp.at_minutes <= end
                and sp.id not in st.spreads_fired
                and sp.id not in st.spreads_averted
            ),
            key=lambda sp: sp.at_minutes,
        ):
            _accrue(cursor, sp.at_minutes)
            cursor = sp.at_minutes
            if set(sp.unless_contained) <= set(st.contained_at):
                st.spreads_averted.append(sp.id)
                continue
            st.spreads_fired.append(sp.id)
            st.spread_at.setdefault(sp.to, sp.at_minutes)
        _accrue(cursor, end)
        st.elapsed_minutes = end

        # 解放前のアクション数。何が増えたかを学習者に返すため
        before_unlocked = {a.id for a in self.available_actions()}

        # 4. 証拠の開示
        revealed: list[str] = []
        withheld = withheld_evidence(self.scenario, st)
        for eid in action.yields:
            if eid in withheld:
                continue  # まだ起きていないので、そこには何も無い（SPEC 5.2）
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

        # 5.5 保全。**時計にだけ効く**（SPEC 5.6.3）。以後この証拠は
        #     timeline の destroys では失われない。効き始めるのは
        #     連絡と同じく**打ち終わった時刻**（end）から — 取りに行っている
        #     20分の間に現場が触ってしまうことはある。
        #     **自分の手の destroys には効かない**（上の 5 は secured を見ない）。
        #     排他は順序そのものが判断であり、そこまで守ると 3.8 が消える
        for eid in action.secures:
            if eid not in st.secured_evidence:
                st.secured_evidence.append(eid)

        # 6. 論点の解消状態を再評価
        st.resolved_questions = questions_mod.resolved_ids(
            self.scenario, st.obtained_evidence
        )

        # 誤導を追いかけた時間の集計（SPEC 6.6）。誤導証拠を取得した後に、
        # それが指す資産を調べた時間を数える。**採点はしない** —
        # 外れを引くのは調査の一部である。講評で本人に返すためだけに持つ。
        if action.type == ActionType.INVESTIGATE:
            st.investigation_minutes += action.cost_minutes
            if self._follows_misleading(action, pre_ctx.obtained_evidence):
                st.misled_follow_minutes += action.cost_minutes

        # 7. 封じ込め。**2つの別々の問いを、2つの集合で持つ**（SPEC 6.3）。
        #
        #    contained_at … 攻撃が止まった資産。直接 targets のみ。
        #    halted_at    … 業務が止まった資産。business_impact を持つ手だけが
        #                   入り、depends_on で波及する。
        #
        #    以前は1つの集合で両方を表していた。dc01 の電源を落とすと
        #    「fs01 と ws-042 も封じ込めた」ことになり、Run キーも
        #    暗号化プロセスも生きたまま封じ込め成立と数えられていた。
        contained: list[str] = []
        eradicated: list[str] = []
        halted: list[str] = []
        cascaded: list[str] = []
        already: list[str] = []
        if action.type == ActionType.CONTAIN:
            before_contained = set(st.contained_at)
            already = [t for t in action.targets if t in before_contained]
            for asset_id in action.targets:
                st.contained_at.setdefault(asset_id, end)
            contained = [a for a in st.contained_at if a not in before_contained]

            # 根絶。**依存連鎖しない。** 上流を作り直しても、下流の端末に
            # 残された永続化は消えない。5.8 の減衰はここを見る
            before_eradicated = set(st.eradicated_at)
            for asset_id in action.eradicates:
                st.eradicated_at.setdefault(asset_id, end)
            eradicated = [a for a in st.eradicated_at if a not in before_eradicated]

            before_halted = set(st.halted_at)
            if action.side_effects.business_impact:
                direct = dict(st.halted_at)
                for asset_id in action.targets:
                    direct.setdefault(asset_id, end)
                st.halted_at = damage_mod.expand_containment(
                    direct, self.scenario.asset_by_id
                )
            halted = [a for a in st.halted_at if a not in before_halted]
            # 直接指定していないのに業務が止まったもの＝依存で波及した分
            cascaded = [a for a in halted if a not in action.targets]

        # 7.2 戻す（SPEC 5.11）。**止めてあったものを業務に返す。**
        #
        #     業務影響はそこで止まる。だが**取り除けていなければ、
        #     攻撃者も一緒に戻ってくる** — 封じ込めが解けて、被害がまた積もり出す。
        #
        #     これは**その場では告げない**（5.9）。画面に出るのは
        #     「戻した」だけで、何が起きたかは講評で初めて分かる。
        #     告げてしまうと、取り除かずに戻す判断が一度も起きない。
        # 7.3 塞ぐ（SPEC 5.12）。**盤面の上では何も起きない。**
        #     止めるわけでも取り除くわけでもないので、押しても資産は動かない。
        #     効くのは演習が終わったあと — 見えるのは講評の破線だけである。
        #     押した結果が無言にならないよう、`hardened` は返す
        hardened: list[str] = []
        if action.type == ActionType.HARDEN:
            before_hardened = set(st.hardened_at)
            for asset_id in action.hardens:
                st.hardened_at.setdefault(asset_id, end)
            hardened = [a for a in st.hardened_at if a not in before_hardened]

        restored: list[str] = []
        if action.type == ActionType.RESTORE:
            truth = self.scenario.world.ground_truth
            for asset_id in action.restores:
                if asset_id not in st.halted_at:
                    continue          # 止まっていないものは戻せない
                st.restored_at.setdefault(asset_id, end)
                restored.append(asset_id)
            if restored:
                # 業務影響を解く。依存で波及していた分も引き直す
                keep = {
                    a: m for a, m in st.halted_at.items() if a not in st.restored_at
                }
                st.halted_at = damage_mod.expand_containment(
                    keep, self.scenario.asset_by_id
                )
                for asset_id in restored:
                    # **取り除けていない資産を戻すと、攻撃が再開する。**
                    # 居座られたままの状態を残して電源を入れ直すのと同じ
                    if (
                        asset_id in truth.persistence
                        and asset_id not in st.eradicated_at
                        and asset_id in st.contained_at
                    ):
                        st.contained_at.pop(asset_id, None)
                        st.recompromised_at.setdefault(asset_id, end)

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
        st.executed_at.setdefault(action.id, start)

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
            eradicated=eradicated,
            halted=halted,
            restored=restored,
            hardened=hardened,
            cascaded=cascaded,
            already=already,
            business_impact_delta=st.accumulated_business_impact - impact_before,
            events=events,
            averted=averted,
            prevented=prevented,
            kind=action.type,
            preserves=bool(action.secures),
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
        # 突き合わせる相手は、**その瞬間の世界**である（SPEC 6.2 / v1.48）。
        # 出し直すたびに更新する — 拡散に気づいて名指しを足した学習者が、
        # そのせいで適合率を落とすのは、気づいたことへの罰になる
        self.state.assessment_compromised = compromised_now(self.scenario, self.state)

        if self.state.assessment_snapshot is None:
            # 宣言時点のスナップショットが採点の基準点になる（SPEC 3.6）
            self.state.assessment_snapshot = AssessmentSnapshot(
                at_minute=self.state.elapsed_minutes,
                assessed_assets=list(cleaned),
                unresolved_critical=questions_mod.unresolved_critical(
                    self.scenario, self.state.obtained_evidence
                ),
                obtained_evidence=list(self.state.obtained_evidence),
                compromised=list(self.state.assessment_compromised),
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
        # 見るのは halted_at。止めた（攻撃を断った）ことと
        # 業務が止まったことは別の問いである（SPEC 6.3）
        for asset_id, entered in self.state.halted_at.items():
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
            possibilities=list(by_id[eid].possibilities)
            if profile.show_evidence_possibilities
            else None,
            occurred_at=by_id[eid].occurred_at,
            at_minute=state.obtained_at.get(eid, 0),
        )
        for eid in state.obtained_evidence
        if eid in by_id
    ]

    unlocked = scenario.phases_up_to(state.current_phase)
    phase_labels = {p.id: p.label for p in scenario.phases}
    got = set(state.obtained_evidence)
    done = set(state.executed_actions)
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
        if a.phase in unlocked and _unlocked_by(a, got, done)
    ]

    # ── 注意の配分（SPEC 7.6.16）。真実は使わない ──
    # 押した手だけを数える。押していない手の射程は数に現れないので、
    # 「まだ選べる手が何を見に行くか」は盤からは読めない
    touched: dict[str, int] = {}
    for aid in dict.fromkeys(state.executed_actions):
        act = scenario.action_by_id.get(aid)
        if act is None:
            continue
        for asset_id in set(act.targets) | set(act.investigates) | set(act.eradicates):
            touched[asset_id] = touched.get(asset_id, 0) + 1

    # 手元の証拠の本文が名指ししている数。`points_to` は見ない —
    # 学習者が自分で読めば分かることしか出さない（7.4）
    mentions: dict[str, int] = {}
    for asset in scenario.world.assets:
        mentions[asset.id] = sum(
            1
            for eid in state.obtained_evidence
            if eid in by_id and names_asset(by_id[eid].content, asset)
        )

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
                touched=touched.get(a.id, 0) if profile.show_topology else None,
                mentions=mentions.get(a.id, 0) if profile.show_topology else None,
                contained=(a.id in state.contained_at),
                eradicated=(a.id in state.eradicated_at),
                halted=(a.id in state.halted_at),
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
