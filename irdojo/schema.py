"""シナリオ DSL の Pydantic モデル（SPEC 7.1）。

エンジンはこのモジュールの型だけを見る。攻撃手法名や資産名といった
ドメイン知識は一切持たない（SPEC Part 4 原則1）。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Collection
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.4"

# 事件の時計の書式（SPEC 5.4 / 7.6.17）
_OCCURRED_AT = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


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
    RESTORE = "restore"
    # 入られた口を塞ぐ。**効くのは演習が終わったあと**なので、
    # 盤面の上では何も起きない。見えるのは講評の破線だけ（SPEC 5.12）
    HARDEN = "harden"


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
    # **この資産を、盤面の文がそう呼んでいる名前**（v1.45）。
    # 漏洩検査は id と label からしか名前を作れなかった。日本語の盤面では
    # 同じものが場所ごとに別の呼ばれ方をする — 「クラウドメール」は
    # 本文では「受信箱」であり、そこを著者が宣言する場所が無かった。
    # 結果として、同じ漏れ方が語によって通ったり弾かれたりしていた
    # （「差出人整理アシスタント」は弾かれ、「森の受信箱」は通った）。
    # ここに書いた語は `asset_names` に合流し、**すべての漏洩検査**と
    # 盤の「証」の数え方に同時に効く。
    aliases: list[str] = []
    criticality: Criticality
    depends_on: list[str] = []
    business_impact_per_hour: float = Field(default=0.0, ge=0.0)


class Spread(Strict):
    """演習中に侵害が1つ増える出来事（SPEC 5.2 / v1.48）。

    1本目・2本目の `ground_truth` は執筆時に固定で、`timeline` は圧力しか
    運ばなかった。**この形だけが、真実そのものを動かす。**

    配布の仕組みを攻撃者が握っている盤面では、学習者が調べている間も
    配られ続ける。時間が経つほど `compromised` が増え、
    `assessment_recall` と `containment_completeness` の**分母が動く**。

    `unless_contained` が要である。**学習者の不作為が原因で広がる形にする** —
    配り元を止めてあれば、その窓では何も配られない。
    防げない拡散を書いてはいけない（遅い学習者が自分の判断と無関係に
    不正解にされるのは、教材ではなく罰である）。ローダが、
    その窓までに配り元を止めきれない盤面を拒否する。

    `reveals` は**起きて初めて取れるようになる資料**である。これが無いと、
    窓の後に判定した学習者は「増えた1台」を名指しする手段を持たないまま
    再現率だけを落とす — それも防げない罰にあたる。逆に、起きる前から
    その資料が取れてしまうと、盤面はまだ起きていないことを配ることになる。
    """

    id: str
    to: str                          # 新たに侵害される資産
    at_minutes: int = Field(gt=0)    # 配られる窓（開始からの分）
    # ここに挙げた資産を**全部**止めてあれば、この窓では何も起きない。
    # 空にはできない（防ぎようのない拡散になる）
    unless_contained: list[str]
    # 起きて初めて取れるようになる資料。起きなければ永久に取れない
    reveals: list[str] = []


class GroundTruth(Strict):
    """学習者には最後まで見えない。採点にのみ使う（SPEC 3.2）。

    `compromised` は**開始時点**の侵害資産である。`spreads` を持つ盤面では、
    演習中にここへ資産が足される（SPEC 5.2）。採点が見る集合は
    `engine.compromised_now` / `engine.effective_truth` が組む。
    """

    patient_zero: str
    compromised: list[str]
    persistence: list[str] = []
    innocent: list[str] = []
    # 演習中に侵害が増える窓。省略可（1本目・2本目は 0件）
    spreads: list[Spread] = []
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
    # **誤導には向きが2つある**（SPEC 3.4）。`points_to` は「この資産を疑え」と
    # 読ませる側で、外すと適合率が落ちる。`clears` はその逆側 —
    # 「この資産については何も出てこなかった」と読ませる所見で、
    # 真に受けると**侵害された資産を名指しから落とす**（再現率が落ちる）。
    # 実務でより高くつくのは後者だが、v1.41 まで盤面にも計器にも存在しなかった。
    # ローダは誤導専用にし、`compromised` の資産しか書けないようにする —
    # 無実の資産を白と読ませる所見は、誤導ではなく正しい資料である。
    clears: list[str] = []
    misleading: bool = False
    plausibility: Literal["low", "medium", "high"] = "medium"
    volatile: bool = False
    summary: str
    content: str
    # ログの読み方。フィールドの意味、書式、一般的な相場観まで。
    # 「だからこの資産は侵害されている」という推論は書かない（それは答え）。
    reading: str = ""
    # **この所見は何を意味しうるか**（v1.46）。`reading` を読めても、
    # 初学者は「で、これは何なのか」のところで止まる。読み方だけを渡すのは、
    # 辞書を渡して文章を読めと言うのに近い。
    #
    # **`reading` を拡張せず別の欄にしてある。** 「読み方が漏らしているか」と
    # 「可能性の列挙が漏らしているか」は別の検査で見たい — 混ぜると、
    # どちらが漏らしたのか分からないまま1本の長さの帯で測ることになる。
    #
    # 守る線は4つ。①必ず2つ以上（1つならそれは結論）②順序・厚み・語気で
    # 本命を示唆しない ③そのシナリオの固有名を出さない（資産名・証拠ID・
    # アクション名・**棄却の材料にしか無い語**）④誤導と本物で数と長さが揃う。
    # ①③はローダが、②はローダ（語彙と厚み）が、④は tools/balance.py が見る。
    #
    # 1項目は「ありうる読み＋それを決着させるには何を見るか」で書く。
    # **決着はさせない。** 「毎日 02:15 のバックアップジョブかもしれません」は、
    # 台帳を引く手を 25分ぶん無料で配っているのと同じである。
    possibilities: list[str] = []
    # **事件の時計**（v1.47）。この資料が記録している出来事が、
    # 現実の壁時計でいつ起きたか。`YYYY-MM-DD HH:MM`。
    #
    # key_lessons の1行目は「時刻の近さは関係の証明にならない」と言うのに、
    # **学習者に時刻を並べる場所が無かった。** 時刻は生ログの中に
    # `2026-03-14T02:17:33Z` の形で埋まっていて、カードをまたいで頭の中で
    # 並べるしかない。並べる作業を要求しておいて、並べる場所を出していない。
    #
    # **content からの解析にしなかった。** 1つの資料には時刻が何個も出る
    # （収集範囲の期間、他ホストの最終受信、定義ファイルの更新時刻）。
    # どれがその資料の言う「出来事」なのかは作者にしか決められない。
    # 代わりにローダが、**content に出ていない時刻を書けないようにする** —
    # 欄は content の再掲であって、新しい開示ではない。
    #
    # **空にしてよい。** 台帳・期間の集計・聞き取りは、事件の出来事ではなく
    # 平常時の事実や期間をまとめたものであって、1点の時刻を持たない。
    # ただし**有無が誤導の印になってはいけない** — 誤導だけが時刻を持つ
    # （あるいは持たない）盤面は、欄そのものが答えを配る。ローダが
    # 2×2 の空きを拒否し、tools/balance.py が比を見る。
    occurred_at: str = ""
    refuted_by: list[str] = []
    # 棄却の条件。`open_questions.resolution_mode` と同じ形で読む。
    #   any … どれか1つ持っていれば棄却できる（既定）
    #   all … すべて揃って初めて棄却できる
    # `misleading: true` の証拠にだけ意味がある（ローダが他での指定を拒否する）。
    refutation_mode: Literal["any", "all"] = "any"

    @model_validator(mode="after")
    def check_occurred_at(self) -> "Evidence":
        """事件の時計の書式。`YYYY-MM-DD HH:MM` 固定にする。

        **日付まで書かせる。** 時刻だけにすると、盤面が日をまたいだ
        瞬間に嘘になる（2本目は同意が 5/17、騒ぎが 6/18 で、1か月
        離れている）。日付を持っていれば、画面の側が「同じ日なら
        時刻だけ・またぐなら日付も」と選べる。
        """
        t = self.occurred_at.strip()
        self.occurred_at = t
        if not t:
            return self
        if not _OCCURRED_AT.fullmatch(t):
            raise ValueError(
                f"{self.id}.occurred_at: 書式は 'YYYY-MM-DD HH:MM' です（{t!r}）"
            )
        try:
            datetime.strptime(t, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError(f"{self.id}.occurred_at: 実在しない日時です（{t!r}）") from exc
        return self

    @property
    def occurred(self) -> "datetime | None":
        """並べ替えのための日時。書かれていなければ None。"""
        if not self.occurred_at:
            return None
        return datetime.strptime(self.occurred_at, "%Y-%m-%d %H:%M")

    @model_validator(mode="after")
    def fold_possibilities(self) -> "Evidence":
        """列挙の各項目を1行に畳む。

        YAML 側は読める幅で折り返して書く（1項目で 60字を超える）。
        **畳むのを画面側の仕事にしない。** 折り返しが残ったままだと、
        ローダの漏洩検査が「行をまたいだ語」を取り逃がす — 資産名を
        改行で割って書けば、検査は素通りする。日本語なので空白は挟まない。
        """
        self.possibilities = [
            re.sub(r"\s*\n\s*", "", t).strip() for t in self.possibilities
        ]
        return self

    def is_refuted(self, held: Collection[str]) -> bool:
        """手元の証拠 `held` で、この誤導を棄却できるか。

        **この判定を2箇所で別々に書いてはいけない。** 複雑度の算出（3.12）は
        `refuted_by` を「2つ揃って初めて棄却できる」＝ AND と読み、
        `tools/balance.py` のプレイ像は「どれか1つで棄却できる」＝ OR と
        読んでいた（v1.38 まで）。同じフィールドを逆の意味で読む2箇所が
        あると、誤導の型を1つ増やした瞬間に、複雑度と実測が食い違う。
        条件は `refutation_mode` が言い、読むのはこの1本だけにする。
        """
        if not self.refuted_by:
            return False
        got = set(held)
        if self.refutation_mode == "all":
            return set(self.refuted_by) <= got
        return bool(set(self.refuted_by) & got)

    @property
    def needs_every_refutation(self) -> bool:
        """棄却に2つ以上の証拠を要する誤導か（SPEC 3.12 の「多段」）。

        `any` で候補が2つあるのは**多段ではなく、逃げ道が2本ある**という
        ことで、むしろ易しい。数え方を `len(refuted_by) >= 2` にしていた頃は
        易しい誤導ほど複雑度を押し上げていた。
        """
        return self.refutation_mode == "all" and len(self.refuted_by) >= 2


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
    # **この手を打ってからでないと押せない手**（SPEC 5.6.2）。
    # `requires_evidence` が「何が分かったら次を思いつくか」を表すのに対し、
    # こちらは「何を先に仕掛けたか」を表す。全部が実行済みで初めて解放される。
    # 取っていないイメージは読めない — 仕掛けと読解を割るための門であって、
    # 手がかりの連鎖ではない（証拠を1件も産まない手を前提にできる）。
    requires_actions: list[str] = []
    investigates: list[str] = []
    targets: list[str] = []
    # contain 専用。**その資産に残された永続化を取り除く**手であることを表す。
    # `targets` とは別に持つ。攻撃の経路を断つことと、居座られた状態を
    # 作り直して消すことは別の作業であり、費用も、失うものも、
    # 業務への当たり方も違う（SPEC 5.6 / 5.8）。
    # `targets` の部分集合でなければならない — 手を触れていない資産から
    # 永続化だけが消えることはない。ローダが拒否する
    eradicates: list[str] = []
    # **戻す**（SPEC 5.11）。止めてあった資産を業務に返す。
    # 業務影響はそこで止まるが、**取り除けていなければ攻撃者も戻ってくる。**
    # 「隔離しても、端末を戻せば攻撃者も戻ってくる」は `containment_factor` の
    # 説明に書いてありながら、**盤面で一度も問われていなかった**（9.4 #5 の続き）
    restores: list[str] = []
    # **入り口を塞ぐ**（SPEC 5.12）。止めて取り除いても、入られた口が
    # 開いたままなら同じことが起きる。その被害は演習の終わったあとに出る
    hardens: list[str] = []
    yields: list[str] = []
    destroys: list[str] = []
    # **時計にだけ効く保全**（SPEC 5.6.3）。この手を打ち終わった時刻から先、
    # ここに書かれた証拠は timeline の `destroys` では失われなくなる。
    # **自分の手の `destroys` では失われる** — 排他はそのまま残る。
    # 世界に奪われることと、自分で消すことは設計上の意味が違い（8.2）、
    # 前者は「先に仕掛けておけ」で防げるべきもの、後者は
    # 「順序そのものが判断」である（3.8）。両方に効かせると排他が消える。
    secures: list[str] = []
    # communicate 専用。以後この出来事は起きなくなる（既に起きた分には効かない）。
    # 学習者には渡さない。ラベルが世界の言葉で何をするかを言えば足りる
    prevents: list[str] = []
    repeatable: bool = False
    side_effects: SideEffects = SideEffects()

    @model_validator(mode="after")
    def contain_requires_targets(self) -> "Action":
        if self.type == ActionType.HARDEN and not self.hardens:
            raise ValueError(f"{self.id}: 塞ぐ手に hardens がありません")
        if self.hardens and self.type != ActionType.HARDEN:
            raise ValueError(
                f"{self.id}: hardens は type: harden の手にだけ書けます"
            )
        if self.type == ActionType.RESTORE and not self.restores:
            raise ValueError(f"{self.id}: 戻す手に restores がありません")
        if self.restores and self.type != ActionType.RESTORE:
            raise ValueError(
                f"{self.id}: restores は type: restore の手にだけ書けます"
            )
        if self.type == ActionType.CONTAIN and not self.targets:
            raise ValueError(f"{self.id}: contain アクションには targets が必要")
        # 封じ込めは手がかりで縛らない。証拠なしに止めることは「できる」べきで、
        # その当否は方針の制約で測る（SPEC 原則2 / 3.8）
        if self.type == ActionType.CONTAIN and self.requires_evidence:
            raise ValueError(
                f"{self.id}: contain に requires_evidence は使えない。"
                "封じ込めの当否は方針の制約で測る"
            )
        if self.eradicates:
            if self.type != ActionType.CONTAIN:
                raise ValueError(f"{self.id}: eradicates は contain 専用")
            stray = sorted(set(self.eradicates) - set(self.targets))
            if stray:
                raise ValueError(
                    f"{self.id}: eradicates が targets に含まれていません: {stray}。"
                    "手を触れていない資産から永続化だけが消えることはない"
                )
        if self.secures:
            # 保全は調査の一部である。**費用は調査フェーズの予算から出る** —
            # 仕掛けと読解を割る意味は、限られた時間の中で先に仕掛けるか
            # どうかを選ばせることにあり、他の型に付けると
            # 「連絡（出来事ごと止める）」との違いが消える（5.6.1 / 5.6.3）
            if self.type != ActionType.INVESTIGATE:
                raise ValueError(f"{self.id}: secures は investigate 専用")
            both = sorted(set(self.secures) & set(self.yields))
            if both:
                raise ValueError(
                    f"{self.id}: 自分が産む証拠を secures しています: {both}。"
                    "取得済みの証拠は初めから失われない（守る意味がない）"
                )
            clash = sorted(set(self.secures) & set(self.destroys))
            if clash:
                raise ValueError(
                    f"{self.id}: 同じ証拠を secures と destroys の両方に"
                    f"書いています: {clash}"
                )
        if self.id in self.requires_actions:
            raise ValueError(f"{self.id}: 自分自身を requires_actions に指定しています")
        if self.type == ActionType.COMMUNICATE:
            # 連絡は「何かを起きなくする」ためだけにある。
            # 何も防がない連絡は、時間を溶かすだけのボタンになる
            if not self.prevents:
                raise ValueError(
                    f"{self.id}: communicate には prevents が必要。"
                    "何も防がない連絡は時間を溶かすだけのボタンになる"
                )
            for field in ("yields", "destroys", "targets", "investigates", "secures"):
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
    """事実認識層の指標1本。

    `label` はその指標の名前（生値が何を測っているか）。
    `label_good` は**良い側から見た名前**で、講評はこちらを使う。

    講評は生値ではなく「良さ」（向きを揃えた 0〜100）を出す。
    「永続化の見落とし」という名前の列に 100 が並ぶと、
    読み手には「全部見落とした」としか読めない。向きを揃えるなら
    名前も揃える必要がある — 数字だけ裏返して名前を据え置くと、
    かえって読めなくなる。
    """

    id: str
    label: str
    label_good: str = ""
    direction: Literal["lower_is_better", "higher_is_better"]
    weight_multiplier: float = 1.0

    @model_validator(mode="after")
    def good_label_required_when_reversed(self) -> "MetricSpec":
        if self.direction == "lower_is_better" and not self.label_good.strip():
            raise ValueError(
                f"{self.id}: direction が lower_is_better の指標には "
                f"label_good（良い側から見た名前）が必要です"
            )
        return self

    @property
    def display_label(self) -> str:
        return self.label_good.strip() or self.label


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
    # 考えられること（この所見は何を意味しうるか）。`reading` と同じ扱いにする —
    # 読み方を渡さない相手に可能性だけ渡しても、生ログとの対応が付かない
    show_evidence_possibilities: bool = True
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
        show_evidence_possibilities=False,
    ),
    AssistLevel.HARD: AssistProfile(
        show_open_questions=False,
        show_question_resolution=False,
        show_assessment_warning=False,
        show_damage_graph=False,
        show_evidence_summary=False,
        show_action_description=False,
        show_evidence_reading=False,
        show_evidence_possibilities=False,
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

    **`clears` を持つ所見は数える。** 見た目は同じネガティブ所見だが、
    こちらは侵害された資産を名指しから落とさせる仕掛けであって、
    読む量ではなく推論の難しさを上げている（SPEC 3.4）。
    """
    resolving = {e for q in sc.open_questions for e in q.resolved_by}
    refuting = {r for e in sc.evidence for r in e.refuted_by}
    return [
        e
        for e in sc.evidence
        if e.points_to or e.clears or e.id in resolving or e.id in refuting
    ]


def complexity_raw(sc: "Scenario") -> float:
    """丸める前の値（SPEC 3.12）。**式が住んでいるのはここ1か所。**

    丸めた★だけでは、同梱シナリオが全部 3 に落ちたときに
    「式が壊れている」のか「盤面が似ている」のかが分からない。
    `tools/balance.py` が作者向けにこの値を出す。
    """
    misleading = [e for e in sc.evidence if e.misleading]
    crit = [q for q in sc.open_questions if q.critical]
    # 「棄却に2つ以上の証拠を要する誤導」は refutation_mode が決める。
    # 件数だけで数えると、逃げ道が2本ある易しい誤導を多段と数えてしまう
    multi_step = [e for e in misleading if e.needs_every_refutation]
    return (
        1.0 * len(misleading)
        + 0.5 * sum(PLAUSIBILITY_SCORE[e.plausibility] for e in misleading)
        + 0.8 * len(crit)
        + 0.3 * (len(bearing_evidence(sc)) / 5)
        + 1.0 * len(multi_step)
    )


def compute_complexity(sc: "Scenario") -> int:
    """シナリオ内容から 1〜5 を算出する。YAML には書かせない。"""
    return max(1, min(5, round(complexity_raw(sc) / 3.0)))


# ─────────── チュートリアル（SPEC 3.13） ───────────


class TutorialStep(Strict):
    """チュートリアルの1段（SPEC 3.13）。

    **書くのは手順と理由であって、答えではない。**
    「この端末が侵害されています」は書かない。書くのは
    「一次情報の裏を先に取ります。報告は観測ではないからです」である。
    答えは、ふつうのシナリオと同じく講評で初めて出る（5.9）。

    **`caveat` は省略できない段がある。** 学習者に「まずこうしましょう」と
    言う以上、**それが唯一の正解ではないこと**を同じ画面で言わないと、
    この製品の主張（方針が変われば最善も変わる）と正面から衝突する。
    手を1つ名指しする段（`expect_action` を持つ段）では必須にしてある。
    """

    id: str
    body: str
    # この段で押してほしい手。省略すると「読んで次へ」の段になる
    expect_action: str | None = None
    # 手ではなく、フェーズ移行や被疑判定を待つ段
    expect_kind: str | None = None
    # 「ただし、これが唯一の正解ではない」を言う一文
    caveat: str = ""
    # 画面のどこを見てほしいか（CSS セレクタ）。遊び方の案内で使う
    points_at: str | None = None

    @model_validator(mode="after")
    def _one_kind_of_wait(self) -> "TutorialStep":
        if self.expect_action and self.expect_kind:
            raise ValueError(
                f"{self.id}: expect_action と expect_kind は同時に書けない"
            )
        if self.expect_kind and self.expect_kind not in (
            "advance_phase", "declare_assessment", "finish"
        ):
            raise ValueError(f"{self.id}: 未知の expect_kind: {self.expect_kind}")
        if self.expect_action and not self.caveat.strip():
            raise ValueError(
                f"{self.id}: 手を名指しする段には caveat が要る"
                "（唯一の正解ではないことを同じ画面で言う / SPEC 3.13）"
            )
        return self


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
    # **練習用の盤面**（SPEC 3.13）。案内が付き、設計の均衡の測定からは外れる。
    # 外す理由は「通らないから」ではなく、**測っている対象が違う**ため —
    # 練習の盤面は「網羅が損か」「折れ点を誰かが踏むか」を成立させない
    tutorial: bool = False


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
    tutorial: list[TutorialStep] = []

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


# ─────────── 資産の名指し（漏洩検査の共通部品） ───────────


def asset_names(asset: "Asset") -> set[str]:
    """その資産を名指ししていると見なす文字列の集合。

    id だけでは足りない。資産一覧と構成図には label がそのまま出ているので、
    「営業部端末 (佐藤)」と書いても「ws-042」と書いても、読む側には
    同じ資産を指したことになる。括弧の中の呼び名も同じ。
    """
    names = {asset.id}
    for alias in re.findall(r"[（(]([^）)]+)[）)]", asset.label):
        alias = alias.strip()
        if alias:
            names.add(alias)
    bare = re.sub(r"[（(][^）)]*[）)]", "", asset.label).strip()
    if bare:
        names.add(bare)
    # 著者が宣言した呼び名。**id と label から機械的に作れる名前だけでは
    # 足りない。** 日本語の盤面は同じものを場所ごとに別の名前で呼ぶので、
    # 宣言する場所が無いと、同じ漏れ方が語によって通ったり弾かれたりする
    names.update(a.strip() for a in asset.aliases if a.strip())
    return names


def names_asset(text: str, asset: "Asset") -> str:
    """その文が資産を名指ししていれば、名指しに使われた文字列を返す。

    **大文字小文字は区別しない。** 比較を区別していたので、
    `smbutil check --from WS-042` のようにコマンド行が大文字で書いた
    資産名が漏洩検査を素通りしていた。読む側にとって
    `WS-042` と `ws-042` は同じ資産である。
    """
    low = (text or "").casefold()
    for name in sorted(asset_names(asset)):
        if name.casefold() in low:
            return name
    return ""


def spread_targets(sc: "Scenario") -> set[str]:
    """演習中に侵害されうる資産（SPEC 5.2）。

    **開始時点では侵害されていないが、答えの一部ではある。** だから
    漏洩検査では `compromised` と同じ扱いにし（名指しを禁じる）、
    囮の数え方（`_reject_eradication_that_maps_persistence`）では
    無実の側に数えない — 数えると「取り除く手が無実にも届いている」の
    要件を、いずれ侵害される資産で満たせてしまう。
    """
    return {sp.to for sp in sc.world.ground_truth.spreads}


def answer_assets(sc: "Scenario") -> set[str]:
    """名指しを禁じる資産の集合（漏洩検査の共通部品）。

    `compromised ∪ {patient_zero} ∪ persistence ∪ 拡散先`。
    3箇所（論点・アクション・出来事）で同じ集合を使う。
    **3箇所に同じ式を書かない** — 拡散先を足した日に、2箇所だけ直る。
    """
    gt = sc.world.ground_truth
    return set(gt.compromised) | {gt.patient_zero} | set(gt.persistence) | spread_targets(sc)


def briefing_assets(sc: "Scenario") -> set[str]:
    """meta.briefing が既に名指ししている資産の id。

    ブリーフィングは一次情報である。「fs01 で大量のファイル名変更が
    検知された」と最初に渡した以上、その後の文がその資産に触れても
    新しいことは何も渡していない。漏洩検査はここを除外する。
    """
    text = sc.meta.briefing
    return {a.id for a in sc.world.assets if any(n in text for n in asset_names(a))}


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
        _validate_clears(sc, e, asset_ids)
        # refutation_mode は「誤導をどう棄却するか」の条件なので、
        # 誤導でない証拠に書いてあっても何も意味しない。黙って無視すると
        # 作者は効いているつもりで書き続ける
        if "refutation_mode" in e.model_fields_set and not e.misleading:
            raise ValueError(
                f"{e.id}: refutation_mode は misleading: true の証拠にのみ意味があります"
            )
        # 候補が1つしかない all は any と同じ挙動になる。書けてしまうと
        # 複雑度の「多段」の数え方（3.12）だけが作者の意図とずれる
        if e.refutation_mode == "all" and len(e.refuted_by) < 2:
            raise ValueError(
                f"{e.id}: refutation_mode: all には refuted_by が2件以上必要です"
                "（1件なら any と同じ）"
            )

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
        for e in act.yields + act.destroys + act.secures:
            if e not in ev_ids:
                raise ValueError(f"{act.id}: 未定義の証拠 {e}")
        for aid in act.requires_actions:
            if aid not in act_ids:
                raise ValueError(f"{act.id}: 未定義のアクションを前提にしています: {aid}")
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

    _validate_spreads(sc)
    _reject_toothless_constraints(sc)
    _reject_toothless_secures(sc)
    _reject_prerequisite_action_cycle(sc)
    _reject_unsatisfiable_capture_requirement(sc)
    _reject_policies_that_forbid_every_stop(sc)
    _reject_unreachable_actions(sc)
    _reject_assets_no_action_can_touch(sc)
    _reject_criticality_inversion(sc)
    _reject_ambiguous_asset_names(sc)
    _reject_uncontainable_compromise(sc)
    _reject_uneradicable_persistence(sc)
    _reject_eradication_that_maps_persistence(sc)
    _reject_stops_that_erase_without_stopping(sc)
    _reject_toothless_timeline(sc)
    _reject_timeline_that_names_assets(sc)
    _reject_timeline_that_hands_over_what_it_takes(sc)
    _reject_questions_that_name_the_truth(sc)
    _reject_actions_that_name_other_assets(sc)
    _reject_commands_that_hand_over_losable_evidence(sc)
    _reject_notes_that_mark_the_trap(sc)
    _reject_conclusion_vocabulary(sc)
    _reject_possibilities_that_settle(sc)
    _reject_possibilities_that_leak(sc)
    _reject_occurrences_not_in_the_log(sc)
    _reject_occurrence_times_that_mark_the_trap(sc)
    _reject_broken_tutorial(sc)
    _reject_broken_restore(sc)
    _reject_broken_hardening(sc)

    # 被害モデルの params
    _validate_damage_params(sc.damage)


def required_hardening(sc: "Scenario") -> set[str]:
    """塞がなければ見込みが緩まない入り口（SPEC 5.12）。

    **塞ぐ手を1つも持たない盤面では空集合を返す。**
    機構を足しても、既にある盤面の数字は1つも動かさない。
    """
    if not any(a.hardens for a in sc.actions):
        return set()
    return {sc.world.ground_truth.patient_zero}


def _reject_broken_hardening(sc: "Scenario") -> None:
    """塞ぐ手の検査（SPEC 5.12）。

    **塞ぐ手を置くなら、塞ぐべき口にも届くこと。** 入られた口
    （`patient_zero`）に届く手が1つも無いと、塞ぎようがないのに
    「塞いでいない」で減衰が下がる — **守りようのない要求**になる。

    **無実の資産にも塞ぐ手を置く。** 入り口にしか塞ぐ手が無いと、
    手が並んだ時点で「入られたのはここだ」を配ることになる
    （`_reject_eradication_that_maps_persistence` と同じ形の漏洩）。
    """
    hardens = {a.id: set(a.hardens) for a in sc.actions if a.hardens}
    if not hardens:
        return

    known = {a.id for a in sc.world.assets}
    for aid, targets in hardens.items():
        missing = sorted(targets - known)
        if missing:
            raise ValueError(f"{aid}: 塞ぐ先が盤面に無い: {missing}")

    reach = set().union(*hardens.values())
    entry = sc.world.ground_truth.patient_zero
    if entry not in reach:
        raise ValueError(
            f"塞ぐ手があるのに、入られた口に届く手がありません（{entry}）。"
            "塞ぎようがないのに減衰だけが下がります"
        )
    secret = set(sc.world.ground_truth.compromised) | {entry}
    if not reach - secret:
        raise ValueError(
            f"塞ぐ手の当たり先が侵害資産にしか届きません: {sorted(reach)}。"
            "無実の資産にも塞ぐ手を置いて、囮を作ってください"
        )


def _reject_broken_restore(sc: "Scenario") -> None:
    """戻す手の検査（SPEC 5.11）。

    **戻せるのは、止められる資産だけである。** どの手でも止まらない資産を
    戻す手は、押しても何も起きない — 盤面を変えない手はノイズである（5.10）。

    **無実の資産にも戻す手を置く。** 侵害資産にしか戻す手が無いと、
    手が並んだ時点で「危ないのはこれだ」を配ることになる
    （`_reject_eradication_that_maps_persistence` と同じ形の漏洩）。
    """
    restores = {a.id: set(a.restores) for a in sc.actions if a.restores}
    if not restores:
        return

    known = {a.id for a in sc.world.assets}
    stoppable = {t for a in sc.actions if a.side_effects.business_impact for t in a.targets}
    for aid, targets in restores.items():
        missing = sorted(targets - known)
        if missing:
            raise ValueError(f"{aid}: 戻す先が盤面に無い: {missing}")
        idle = sorted(targets - stoppable)
        if idle:
            raise ValueError(
                f"{aid}: どの手でも止まらない資産を戻そうとしています: {idle}。"
                "押しても何も起きない手は盤面を変えません"
            )

    reach = set().union(*restores.values())
    secret = set(sc.world.ground_truth.compromised) | {sc.world.ground_truth.patient_zero}
    if not reach - secret:
        raise ValueError(
            f"戻す手の当たり先が侵害資産にしか届きません: {sorted(reach)}。"
            "無実の資産にも戻す手を置いて、囮を作ってください"
        )


def _reject_broken_tutorial(sc: "Scenario") -> None:
    """案内が盤面と食い違っていないこと（SPEC 3.13）。

    案内は手で書く文章なので、**放っておけば盤面より先に腐る。**
    アクションの id を1つ書き換えた日に、案内だけが古い手を指し続ける
    （凡例のモックが3フェーズのまま腐ったのと同じ壊れ方 / 7.6.7）。
    """
    if sc.meta.tutorial and not sc.tutorial:
        raise ValueError("tutorial: true だが案内が1段も無い")
    if sc.tutorial and not sc.meta.tutorial:
        raise ValueError("案内があるのに meta.tutorial が false")
    if not sc.tutorial:
        return

    _reject_duplicates("チュートリアルの段", [s.id for s in sc.tutorial])
    known = {a.id for a in sc.actions}
    for step in sc.tutorial:
        if step.expect_action and step.expect_action not in known:
            raise ValueError(
                f"{step.id}: 案内が指している手が盤面に無い: {step.expect_action}"
            )
    # **最後は必ず終わりまで連れて行く。** 途中で案内が切れると、
    # 学習者は「次に何をすればいいか分からない画面」に置き去りにされる
    last = sc.tutorial[-1]
    if last.expect_kind != "finish":
        raise ValueError(
            "案内の最後の段は expect_kind: finish であること"
            "（終わりまで連れて行かないと、途中で置き去りになる）"
        )


def _validate_spreads(sc: "Scenario") -> None:
    """演習中に侵害が増える窓を検査する（SPEC 5.2 / v1.48）。

    **この形は、盤面の真実そのものを動かす。** だから他のどの検査よりも
    「守りようのない要求」を作りやすい。禁じるのは7つ。

    1. 参照が壊れている（未定義の資産・証拠）
    2. 拡散先が最初から侵害されている／無実だと宣言されている／
       `patient_zero` である。どれも「増える」と両立しない
    3. 同じ資産が2つの窓で侵害される。1度侵害されたものは二度目が無い
    4. `unless_contained` が空。**防ぎようのない拡散は罰である**
    5. `unless_contained` に `compromised` 以外の資産がある。
       侵害されていない資産を止めることが配布を止める、という盤面は
       「止める理由が無い手を押させる」ことになる
    6. **その窓までに配り元を止めきれない。** 盤面にある最も安い止め方を
       並べても `at_minutes` に間に合わないなら、それは学習者の不作為では
       なく物理法則である（ここが見るのは下限だけで、「巧いプレイなら
       間に合う」は `tools/balance.py` が実測する）
    7. `reveals` が空／その資産を指していない／論点や棄却に使われている。

       空を禁じるのは、窓の後に判定した学習者が**増えた1台を名指しする
       手段を持たない**まま再現率だけを落とすからである。防げない拡散を
       禁じたのと同じ理由が、事実認識の側にもそのまま要る。

       論点と棄却に使えないのは逆向きの理由で、**拡散を防いだ学習者だけが
       解けない論点**が生まれるからである。うまくやった人が罰される。
    """
    gt = sc.world.ground_truth
    if not gt.spreads:
        return

    asset_ids = {a.id for a in sc.world.assets}
    ev_ids = {e.id for e in sc.evidence}
    by_ev = sc.evidence_by_id
    compromised = set(gt.compromised)
    innocent = set(gt.innocent)

    _reject_duplicates("拡散", [sp.id for sp in gt.spreads])
    seen: set[str] = set()

    # 資産ごとの最安の止め方。6 の下限に使う
    cheapest: dict[str, int] = {}
    for a in sc.actions:
        if a.type != ActionType.CONTAIN:
            continue
        for t in a.targets:
            if t not in cheapest or a.cost_minutes < cheapest[t]:
                cheapest[t] = a.cost_minutes

    resolving = {e for q in sc.open_questions for e in q.resolved_by}
    refuting = {r for e in sc.evidence for r in e.refuted_by}

    for sp in gt.spreads:
        if sp.to not in asset_ids:
            raise ValueError(f"{sp.id}: 未定義の資産 {sp.to}")
        if sp.to in compromised:
            raise ValueError(
                f"{sp.id}: 拡散先 {sp.to} は開始時点で既に compromised です"
            )
        if sp.to in innocent:
            raise ValueError(
                f"{sp.id}: 拡散先 {sp.to} が innocent に書かれています。"
                "演習中に侵害される資産は、無関係ではありません"
            )
        if sp.to == gt.patient_zero:
            raise ValueError(f"{sp.id}: 拡散先が patient_zero です")
        if sp.to in seen:
            raise ValueError(f"{sp.id}: 拡散先 {sp.to} が2度侵害されています")
        seen.add(sp.to)

        if not sp.unless_contained:
            raise ValueError(
                f"{sp.id}: unless_contained が空です。"
                "防ぎようのない拡散は、教材ではなく罰です"
            )
        stray = sorted(set(sp.unless_contained) - compromised)
        if stray:
            raise ValueError(
                f"{sp.id}: unless_contained に侵害されていない資産があります: {stray}。"
                "止める理由の無い手を押さないと防げない、という盤面になります"
            )
        floor = 0
        for src in set(sp.unless_contained):
            if src not in cheapest:
                raise ValueError(f"{sp.id}: {src} を止める手が盤面にありません")
            floor += cheapest[src]
        if sp.at_minutes <= floor:
            raise ValueError(
                f"{sp.id}: {sp.at_minutes}分 までに配り元を止めきれません"
                f"（最も安い止め方でも {floor}分 かかります）。"
                "学習者の不作為ではなく、盤面の物理法則で起きる拡散です"
            )

        if not sp.reveals:
            raise ValueError(
                f"{sp.id}: reveals が空です。起きたことを知る資料が無いと、"
                "窓の後に判定した学習者は増えた資産を名指しできないまま"
                "再現率だけを落とします"
            )
        for eid in sp.reveals:
            if eid not in ev_ids:
                raise ValueError(f"{sp.id}: 未定義の証拠 {eid}")
            ev = by_ev[eid]
            if ev.misleading:
                raise ValueError(
                    f"{sp.id}: reveals に誤導証拠 {eid} があります。"
                    "起きたことを知らせる資料は、誤導ではありません"
                )
            if sp.to not in ev.points_to:
                raise ValueError(
                    f"{sp.id}: reveals の {eid} が拡散先 {sp.to} を points_to に"
                    "持っていません（名指しの裏付けにならない資料です）"
                )
            if eid in resolving:
                raise ValueError(
                    f"{sp.id}: reveals の {eid} が論点の解消に使われています。"
                    "拡散を防いだ学習者だけが解けない論点になります"
                )
            if eid in refuting:
                raise ValueError(
                    f"{sp.id}: reveals の {eid} が誤導の棄却に使われています。"
                    "拡散を防いだ学習者だけが畳めない誤導になります"
                )


def _reject_assets_no_action_can_touch(sc: "Scenario") -> None:
    """どの手も触れない資産を拒否する（SPEC 7.6.16）。

    資産盤は各資産に「あなたが押した手のうち、この資産に手を当てた数」を出す。
    盤に並んでいるのに `targets` にも `investigates` にも `eradicates` にも
    一度も現れない資産があると、そこは**何をしても 0 のまま**になる。
    学習者は自分の怠慢だと読むが、実際には触りに行く手が盤面に存在しない。

    盤が無かった頃も同じことは起きていた — その資産は被疑判定の選択肢に
    並ぶのに、根拠を取りに行く手段が1つも無い。**名指しを求めておいて
    調べさせない**のは、判断ではなく勘を測っている。盤はそれを見えるように
    しただけで、規則そのものは前からあるべきだった。

    数えるのはフェーズも解放条件も無視した全アクションである。到達可能性は
    `_reject_unreachable_actions` が別に見ている。
    """
    touchable: set[str] = set()
    for a in sc.actions:
        touchable |= (set(a.targets) | set(a.investigates)
                      | set(a.eradicates) | set(a.restores) | set(a.hardens))
    orphans = sorted(a.id for a in sc.world.assets if a.id not in touchable)
    if orphans:
        raise ValueError(
            f"どのアクションも触れない資産があります: {orphans}。"
            "被疑判定の選択肢に並ぶ資産には、targets / investigates / eradicates の"
            "どれかで手を当てられる手が1つは要ります"
        )


def _validate_clears(sc: "Scenario", e: "Evidence", asset_ids: set[str]) -> None:
    """`clears`（再現率を下げる側の誤導）の使い方を検査する（SPEC 3.4）。

    この向きの誤導は、**書き間違えても盤面では何も起きない。** 指す先が
    無いので学習者の名指しは増えず、作者は「罠を置いた」つもりのまま
    誰も引っかからない盤面を出荷できる。points_to 側の誤導が
    `refuted_by` 必須で守られているのと同じ厚みの検査をここに置く。

    禁じるのは5つ。

    1. `clears` を誤導以外に書くこと。無実の資産について「何も無かった」と
       読ませる所見は、**誤導ではなく正しい資料**である（同梱シナリオの
       ev_013 がそれで、ws-113 は本当に無実）。誤導の印を付けると
       講評が「これに騙されました」と嘘をつく
    2. `points_to` にも `clears` にも何も書かれていない誤導。
       誰も誤導されない飾りで、複雑度だけが上がる
    3. 同じ資産を `points_to` と `clears` の両方に書くこと。
       1つの所見が同じ資産を「疑え」と「白だ」の両方に読ませることはない
    4. `clears` に `compromised` 以外の資産を書くこと。無実の資産を
       白と読ませたなら、その所見は正しい
    5. 棄却経路がその資産を名指しに戻さないこと。`refuted_by` のどれかが
       その資産を `points_to` に持っていなければ、棄却できても
       **名指しは戻らない** — 再現率は下がったままで、罠に出口が無い
    6. `content` がその資産を名指ししていないこと（v1.44）。白と読ませる
       誤導は、読み手が**その資産の話だと分かって初めて**成立する。本文が
       名前を出さないなら、誰も「この端末は白だ」と一般化しない。
       資産盤の「言及」も `content` の文字列照合で数えるので（7.6.16）、
       名指しの無い `clears` は盤にも読み手にも現れないまま罠だけが残る
    """
    for a in e.clears:
        if a not in asset_ids:
            raise ValueError(f"{e.id}: clears に未定義の資産 {a}")
    if e.clears and not e.misleading:
        raise ValueError(
            f"{e.id}: clears は misleading: true の証拠にのみ書けます。"
            "無実の資産について「何も無かった」と読ませる所見は誤導ではありません"
        )
    if e.misleading and not e.points_to and not e.clears:
        raise ValueError(
            f"{e.id}: 誤導証拠が points_to も clears も持っていません。"
            "疑わせる先も、白と読ませる先も無い誤導は、誰も誤導しません"
        )
    both = sorted(set(e.points_to) & set(e.clears))
    if both:
        raise ValueError(
            f"{e.id}: 同じ資産を points_to と clears の両方に書いています: {both}"
        )
    if not e.clears:
        return
    compromised = set(sc.world.ground_truth.compromised)
    stray = sorted(set(e.clears) - compromised)
    if stray:
        raise ValueError(
            f"{e.id}: clears に compromised 以外の資産があります: {stray}。"
            "無実の資産を白と読ませたなら、その所見は正しい"
        )
    by_id = {x.id: x for x in sc.evidence}
    for asset in e.clears:
        back = [
            r for r in e.refuted_by
            if r in by_id and not by_id[r].misleading and asset in by_id[r].points_to
        ]
        if not back:
            raise ValueError(
                f"{e.id}: clears に書いた {asset} を points_to に持つ非誤導証拠が"
                " refuted_by にありません。棄却できても名指しが戻らないので、"
                "再現率は下がったままになります"
            )
        if not names_asset(e.content, sc.asset_by_id[asset]):
            raise ValueError(
                f"{e.id}: clears に書いた {asset} を content が名指ししていません。"
                "白と読ませる誤導は、読み手がその資産の話だと分かって初めて成立します"
            )


def _reject_timeline_that_names_assets(sc: "Scenario") -> None:
    """出来事の文が、侵害された資産を名指ししていないか（SPEC 5.10）。

    アクションの4フィールドには規則があった（`_reject_actions_that_name_other_assets`）。
    論点にもあった。**出来事だけが無防備だった。** 実測すると、
    `timeline.text` に label をそのまま書いても、「森の受信箱に、請求を隠す
    規則が仕込まれているようです」と書いても、ローダは通した（v1.45）。

    出来事は向こうから来る。押さなくても、待っていれば必ず届く。
    そこに答えが乗っていたら、**待つのが最良の調査**になる。
    運ぶのは圧力だけ、という 5.10 の規則は、この検査が無い限り
    「証拠にしない」という形式的な意味しか持っていなかった。

    アクションと違い、出来事には `targets` が無い — 自分が触る資産という
    逃げ道が無いので、禁じるのは
    `compromised ∪ {patient_zero} ∪ persistence ∪ 拡散先` のすべてである
    （`answer_assets`）。ブリーフィングが既に名指しした資産だけが書ける
    （他の検査と同じ免除規則）。

    **innocent は禁じない。** 無関係な資産の名前が出来事に出るのは誤導で
    あって漏洩ではない。むしろ出来事に誤導を載せるのは正当な設計である。
    """
    secret = answer_assets(sc) - briefing_assets(sc)
    by_id = sc.asset_by_id

    for ev in sc.timeline:
        for field in ("label", "text", "averted_label", "averted_text"):
            text = getattr(ev, field) or ""
            for asset_id in sorted(secret):
                name = names_asset(text, by_id[asset_id])
                if name:
                    raise ValueError(
                        f"{ev.id}.{field}: 侵害された資産「{name}」を名指ししています。"
                        "出来事は押さなくても届くので、待つのが最良の調査に"
                        "なってしまいます（5.10 が運ばせるのは圧力だけです）"
                    )


def _reject_timeline_that_hands_over_what_it_takes(sc: "Scenario") -> None:
    """奪う出来事が、奪う証拠の中身をその場で渡していないか（SPEC 5.10 / 原則5）。

    失ったものは**講評で初めて**開示する。プレイ中に「何を失ったか」を
    告げるのは原則5の違反であり、しかも中身まで言えば、
    **買っていない証拠を無料で配ったことになる。**

    実際にそうなっていた（v1.45）。350分の出来事が ev_006 を奪いながら、
    その ev_006 の要約とほぼ同じことを本文で述べていた。さらに悪いことに、
    先手を打った側（`averted_text`）の方が情報が少なかった — 周知を
    出さなかった学習者だけが、証拠を失う代わりにヒントを受け取っていた。

    機械で見られるのは2つ。

      1. その証拠にしか無い数（4文字以上の数字列・16進列）が出ていないか。
         `_reject_commands_that_hand_over_losable_evidence` と同じ道具。
      2. 要約・本文からの**書き写し**が無いか（10文字以上の一致）。

    **これは網羅検査ではない。** 言い換えれば通る。網羅を担保するのは
    「出来事は手段だけを言う」という書き方の規則（5.10）で、これはその
    止め具である。1本目の水準 —「再起動をかけました」「日次のパージが
    走りました」— を写経すること。
    """
    by_id = sc.evidence_by_id

    for ev in sc.timeline:
        texts = [(f, getattr(ev, f) or "") for f in ("text", "averted_text", "label")]
        for eid in ev.destroys:
            target = by_id[eid]
            body = (target.content or "").replace(",", "")
            tokens = _id_tokens(body) | _id_tokens(target.summary or "")
            for field, text in texts:
                flat = text.replace(",", "")
                shared = sorted(t for t in tokens if t in flat)
                if shared:
                    raise ValueError(
                        f"{ev.id}.{field}: 奪う証拠 {eid} にしか無い値 {shared} が"
                        "出ています。買っていない証拠の中身を、失った瞬間に"
                        "渡しています（何を失ったかは講評で開きます）"
                    )
                for source in (target.summary or "", target.content or ""):
                    quoted = _longest_shared_run(text, source)
                    if len(quoted) >= 10:
                        raise ValueError(
                            f"{ev.id}.{field}: 奪う証拠 {eid} からの書き写しが"
                            f"あります（「{quoted}」）。出来事が言えるのは"
                            "手段までで、中身は講評まで開きません"
                        )


def _longest_shared_run(a: str, b: str) -> str:
    """2つの文に共通する、最も長い連続部分。

    書き写しを見つけるためだけの素朴な照合で、言い換えには効かない。
    出来事も証拠も数百文字なので、この計算量で足りる。
    """
    a = re.sub(r"\s+", "", a or "")
    b = re.sub(r"\s+", "", b or "")
    if not a or not b:
        return ""
    best = ""
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > len(best):
                    best = a[i - cur[j]:i]
        prev = cur
    return best


def _reject_questions_that_name_the_truth(sc: "Scenario") -> None:
    """未解消論点の文が、被疑判定の答えを名指ししていないか。

    論点は assisted / standard では**開始0分・0アクション**で画面の左に出る。
    そこに侵害された資産の名前を書くと、被疑判定はログを1行も読まずに
    転記で済む。同梱シナリオの「ws-042 から fs01 へどう到達したか」が
    まさにそれで、compromised と patient_zero の両方をこの1行で渡していた。

    見るのは label / question / implication の3つ。禁じるのは
    `compromised ∪ {patient_zero} ∪ persistence` に属する資産の名指しで、
    **ブリーフィングが既に名指ししている資産は除く** — 一次情報として
    渡したものを問い文で繰り返しても、新しいことは何も渡していない。

    innocent は禁じない。無関係な資産の名前が問い文に出ることは
    誤導であって漏洩ではなく、それを潰すのは演習の中身そのものである。

    この検査は ground_truth を見るので作者にしか回らない。PlayerView は
    通らない（そちらには ground_truth が無い）。
    """
    secret = answer_assets(sc) - briefing_assets(sc)
    by_id = sc.asset_by_id

    for q in sc.open_questions:
        for field in ("label", "question", "implication"):
            text = getattr(q, field) or ""
            for asset_id in sorted(secret):
                name = names_asset(text, by_id[asset_id])
                if name:
                    raise ValueError(
                        f"{q.id}.{field}: 侵害された資産「{name}」を名指ししています。"
                        "未解消論点は開始直後から画面に出るため、"
                        "被疑判定の答えを転記できてしまいます"
                        "（ブリーフィングで既に渡した資産だけが書けます）"
                    )


def _reject_actions_that_name_other_assets(sc: "Scenario") -> None:
    """アクションの文が、そのアクションが触らない侵害資産を名指ししていないか。

    アクション一覧は**開始0分・0アクション**で画面の右に全部並ぶ。
    `contain` は `requires_evidence` を持てない（SPEC 5.6）ので、
    封じ込めの手は最初から全部読める。そこに他の資産の名前を書くと、
    調査を1つも押さずに被疑判定の答えが読める。

    同梱シナリオの「ws-042 から fs01 への SMB を境界で遮断」がそれで、
    この1行が `compromised` の全量（ws-042 と fs01 の両方）と
    critical 論点の答え（横展開の経路）を、0手・0分で渡していた。
    被疑判定は対応フェーズ中も再宣言できる（SPEC 3.6）ので、
    そのまま点になる。

    名指ししてよいのは、そのアクション自身が触る資産
    （`targets ∪ investigates`）に限る。封じ込めの手は自分が何を止めるかを
    名乗る必要があるので、そこまでは許す。
    禁じるのは `compromised ∪ {patient_zero} ∪ persistence` のうち、
    **自分が触らない**資産の名指しで、ブリーフィングが既に名指しした資産は
    除く（_reject_questions_that_name_the_truth と同じ免除規則）。

    **盤面の資産を全部名指しした文は除く。** 全端末に EDR のフルスキャンを
    かける手は、出力に全ホストを並べる。全員を名指しすることは
    誰も選り分けていないことであって、漏洩ではない。
    禁じたいのは**選り分け**である。

    **これは「ヒントを消す」規則ではない。** 「発信元を絞って遮断する」と
    書けば依然としてヒントである。保証するのは
    「自分が触らない資産の名前は出ない」までで、そこから先は書き手の仕事。
    """
    secret = answer_assets(sc) - briefing_assets(sc)
    by_id = sc.asset_by_id

    for act in sc.actions:
        own = (set(act.targets) | set(act.investigates)
               | set(act.restores) | set(act.hardens))
        forbidden = sorted(secret - own)
        for field in ("label", "description", "group", "command"):
            text = getattr(act, field) or ""
            if all(names_asset(text, a) for a in sc.world.assets):
                continue      # 全員を名指し＝誰も選り分けていない
            for asset_id in forbidden:
                name = names_asset(text, by_id[asset_id])
                if name:
                    raise ValueError(
                        f"{act.id}.{field}: 自分が触らない侵害資産「{name}」を"
                        "名指ししています。アクション一覧は開始直後から全部"
                        "読めるため、調査を1つも押さずに被疑判定が組み立てられます"
                        "（名指しできるのは targets / investigates の資産と、"
                        "ブリーフィングで既に渡した資産だけです）"
                    )


# ─────────── command の検査（SPEC 5.6 / 7.6.8） ───────────

# 4文字以上の数字列・16進列。桁区切りのカンマは落として比べる
_ID_TOKEN = re.compile(
    r"(?<![0-9A-Za-z])(?:[0-9][0-9,]{3,}|[0-9A-Fa-f]{4,})(?![0-9A-Za-z])"
)


def _id_tokens(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _ID_TOKEN.finditer(text)}


def _reject_commands_that_hand_over_losable_evidence(sc: "Scenario") -> None:
    """端末の記録が、失われうる証拠の中身を名指ししていないか。

    `command` は押した後に返る（7.6.8）。ふつうは証拠と同時に届くので
    重複でしかない。**失われうる証拠だけは違う。** 世界に奪われた後、
    あるいは自分の手で壊した後にその手を押すと、証拠は出てこないのに
    端末の記録だけが残り、取り損ねたものの中身をそこで渡してしまう。

    実際にそうなっていた: `vol.py -f fs01.raw windows.handles --pid 6644` は
    ev_010（fs01 の稼働中プロセス、`volatile: true`）にしか無い PID で、
    fs01 が現場判断で再起動された後に押すと、「何も出てこなかった」の上の
    端末窓に 6644 と 12,847 handles が並んだ。

    見るのは4文字以上の数字列・16進列に限る。短い数や語は誤検出が多い。
    **失われえない証拠は対象にしない** — 端末の記録と証拠が必ず一緒に届く以上、
    そこに同じ数が出ていても先出しにも後出しにもならない
    （`Where-Object Id -eq 4104` のような、押す前から打てる定数まで
    拒否してしまう）。
    """
    losable = {e.id for e in sc.evidence if e.volatile}
    for ev in sc.timeline:
        losable |= set(ev.destroys)
    for act in sc.actions:
        losable |= set(act.destroys)

    by_id = {e.id: e.content.replace(",", "") for e in sc.evidence}
    for a in sc.actions:
        for token in sorted(_id_tokens(a.command)):
            named = [e for e in a.yields if e in losable and token in by_id[e]]
            if not named:
                continue
            # そのアクション以外の場所から同じ値に辿り着けるなら、
            # 端末の記録がその値を渡した最初の場所ではない
            elsewhere = [
                eid for eid, body in by_id.items()
                if eid not in a.yields and token in body
            ]
            if elsewhere or token in sc.meta.briefing.replace(",", ""):
                continue
            raise ValueError(
                f"{a.id}: command の {token!r} は {named} にしか無い値です。"
                f"{named} は失われうる証拠なので、奪われた後にこの手を押すと"
                "端末の記録だけが残り、取り損ねたものの中身をそこで渡します"
            )


# 括弧の中に節を書かせないための助詞。名札（名詞句）なら現れない
_CLAUSE_PARTICLE = re.compile(r"[をはがにへでもと]|から|より|まで")
_JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
_PARENS = re.compile(r"[（(]([^）)]*)[）)]")


def _reject_notes_that_mark_the_trap(sc: "Scenario") -> None:
    """`command` の括弧に、日本語の**節**を書いていないか。

    「`WS-042: powered off  (揮発性の情報はここで失われる)`」は、
    システムが自分の罠に印を付ける行為である（原則2 / 7.4）。
    失うものは動詞で言う — ラベルが「停止して」と言っている以上、
    括弧で補う必要は無い。

    印の害は、書いた手より**書かなかった手**に出る。同じ `destroys: [ev_010]`
    を持つ「fs01 を停止」には注記が無く、注記の有無がそのまま
    「調査の排他ペアはこの2つ」という目印になっていた。

    禁じるのは節だけで、名札は通す。`(egress only)` `(00:11:42)` は資料だし、
    「架電 03:24 携帯（登録番号）」の括弧も呼び分けであって主張ではない。
    区別は格助詞の有無で見る（名詞句には現れず、節には必ず現れる）。
    完全な判定ではない — 助詞を使わずに主張を書けば通る。
    """
    for a in sc.actions:
        for m in _PARENS.finditer(a.command):
            note = m.group(1).strip()
            if not note or not _JAPANESE.search(note):
                continue
            if _CLAUSE_PARTICLE.search(note):
                raise ValueError(
                    f"{a.id}: command の括弧に日本語の節を書いています"
                    f"（「{note}」）。括弧の注記はシステムが自分の罠に"
                    "印を付ける行為です。失うものはラベルの動詞で言い、"
                    "説明が要るなら description / reading に置いてください"
                )


# ─────────── 結論語のラチェット（SPEC 5.4 / 5.6） ───────────

# **これは網羅検査ではない。** 語彙表は「一度見つけた漏れは二度と戻らない」
# ための止め具であって、次の作者が別の言い回しで書けば通る。
# 網羅を担保しているのは構造側の規則（何をどのフィールドに置けるか）で、
# これはその補助にすぎない。見つけた実例をここへ足していく。
CONCLUSION_WORDS = (
    "認められない", "認められなかった",
    "裏づけ", "裏付け",
    "に一致する",
    "行われていない",
    "仕掛けられていない",
    "攻撃の痕跡",
    "ここで失われる",
    "見つからなかった",
    "侵害されている", "侵害された端末",
    "攻撃者の常套",
    "疑うべき",
    "無関係である",
    "だからこの",
    "正常である",
)


def _reject_conclusion_vocabulary(sc: "Scenario") -> None:
    """結論の言い回しを、資料・手段・規則の側に書いていないか（原則1）。

    見るのは「アシストレベルに関係なく出るもの」と、既に規則があった
    `reading` / `description`。

    **`evidence.summary` は見ない。** 一行で所見を言うことが summary の
    仕事そのもので、それが `hard` で伏せられる中身である。ここまで縛ると
    アシストが何も残らない。逆に `content` は全レベルで出るので、
    そこに要約が混ざると hard という設計軸が死ぬ — 実際 19件中6件で
    content の地の文が summary と同じ主張を述べていた。
    """
    targets: list[tuple[str, str, str]] = []
    for e in sc.evidence:
        targets.append((e.id, "content", e.content))
        targets.append((e.id, "reading", e.reading))
        # 可能性の列挙も同じ線で縛る。「だからこの端末は正常である」は、
        # 可能性ではなく結論であり、書ける場所がここに増えてはいけない
        for i, t in enumerate(e.possibilities):
            targets.append((e.id, f"possibilities[{i}]", t))
    for a in sc.actions:
        targets.append((a.id, "command", a.command))
        targets.append((a.id, "label", a.label))
        targets.append((a.id, "group", a.group))
        targets.append((a.id, "description", a.description))
    for q in sc.open_questions:
        targets.append((q.id, "label", q.label))
        targets.append((q.id, "question", q.question))
        targets.append((q.id, "implication", q.implication))
    for t in sc.timeline:
        targets.append((t.id, "label", t.label))
        targets.append((t.id, "text", t.text))
        targets.append((t.id, "averted_label", t.averted_label))
        targets.append((t.id, "averted_text", t.averted_text))
    # **案内も同じ線で縛る**（SPEC 3.13）。チュートリアルは
    # 「まずこうしましょう」と手順を言う場所であって、
    # 「この端末は侵害されている」と結論を言う場所ではない。
    # 答えはふつうの演習と同じく講評で初めて出る（5.9）
    for s in sc.tutorial:
        targets.append((s.id, "body", s.body))
        targets.append((s.id, "caveat", s.caveat))

    for oid, field, text in targets:
        for word in CONCLUSION_WORDS:
            if word in (text or ""):
                raise ValueError(
                    f"{oid}.{field}: 結論の言い回し「{word}」が入っています。"
                    "資料・手段・規則は書けますが、結論は書けません（原則1）"
                )


# ─────────── 可能性の列挙の検査（SPEC 5.4 / 3.10 / v1.46） ───────────

# **本命を指す語気。** 列挙は順序でも厚みでも語気でも、どれが本当かを
# 言ってはいけない。厚みは下で数えられるが、語気は語彙でしか止まらない。
# CONCLUSION_WORDS と同じで、**これは網羅検査ではない** — 見つけた実例を
# 足していく止め具であって、別の言い回しで書けば通る。
LEANING_WORDS = (
    "可能性が高い", "可能性は低い", "可能性が低い",
    "最も", "もっとも",
    "おそらく", "たいてい", "ほとんど", "だいたい",
    "本命", "有力", "考えにくい", "まずない",
    "多くの場合", "ふつうは", "通常は", "一般的には",
    "実際には", "本当は", "とはいえ", "ただし",
    "典型", "常套", "まれに",
)
# 「まれ」だけにしていたら受身の「持ち込まれた」に当たった。**止め具は、
# 止めたい語気より広く書くと日本語の活用に当たる。** 助詞まで含めて書く。


# **1行に入る全角の字数**（SPEC 5.4 / v1.47）。
# 実測して決めた値である — 1440幅のプレイ画面で、列挙の `li` は内寸 613px、
# 文字は 13px。613 / 13 = 47.2 字が1行に入る。
#
# **文字数の帯と、目が測る行数はずれる。** 45字・48字・46字の3項目は
# 文字数では 1.07倍（帯は 2.0倍まで）で楽に通るのに、画面では
# 1行・2行・1行に折れて、**2行の項目だけが倍の高さで並ぶ。**
# 実測で 21枚中13枚がそうなっていて、うち3枚は「2行になっている
# 唯一の項目が真相側」だった。厚みで本命を示唆しない（②）という規則は、
# 文字数ではなく行数で書かれていなければならなかった。
POSSIBILITY_COLUMNS = 47


def display_width(text: str) -> float:
    """全角換算の幅。全角（W/F/A）を 1、半角を 0.5 と数える。

    文字数で数えると、半角の英数字が混じった項目を過大に見積もる。
    折り返しを決めているのは字数ではなく幅である。
    """
    return sum(
        1.0 if unicodedata.east_asian_width(c) in "WFA" else 0.5
        for c in text
    )


def wrapped_lines(text: str, columns: int = POSSIBILITY_COLUMNS) -> int:
    """`columns` 字幅の箱に流したときの行数。"""
    return max(1, math.ceil(display_width(text) / columns))


def _reject_possibilities_that_settle(sc: "Scenario") -> None:
    """可能性の列挙が、そこで決着していないか（SPEC 3.10）。

    見るのは3つ。

    ①**1つしか挙がっていない列挙を拒否する。** 1つならそれは可能性ではなく
      結論である。「これはバックアップジョブかもしれません」は、
      台帳を引く 25分の手を無料で配ったのと同じになる。

    ②**厚みで本命を示唆していないか。** 3行書いたものと半行のものを
      並べれば、読む側は長いほうを本命と読む。列挙の中で
      最長と最短の比を 2.0 までに抑える（帯の根拠は
      test_misleading_readings_are_not_thinner と同じ — 読む前に
      長さで振り分けられるなら、読ませていることにならない）。

    ③**語気で本命を示唆していないか。** 「おそらく運用の処理でしょう」は
      順序も厚みも揃えたまま答えを渡す。LEANING_WORDS の止め具で拾う。

    順序そのものは機械では見えない。ここで保証できるのは
    「厚みと語気では傾いていない」までで、そこから先は書き手の仕事である。
    """
    for e in sc.evidence:
        items = [t.strip() for t in e.possibilities]
        if not items:
            continue
        if any(not t for t in items):
            raise ValueError(f"{e.id}.possibilities: 空の項目は書けません")
        if len(items) < 2:
            raise ValueError(
                f"{e.id}.possibilities: 可能性が1つしかありません。"
                "1つならそれは可能性ではなく結論です（2つ以上挙げてください）"
            )
        if len(set(items)) != len(items):
            raise ValueError(
                f"{e.id}.possibilities: 同じ項目が2回書かれています。"
                "数だけ揃えても、読む側には1つしか挙がっていません"
            )
        lengths = [len(t) for t in items]
        if max(lengths) > 2.0 * min(lengths):
            raise ValueError(
                f"{e.id}.possibilities: 項目の厚みが偏っています"
                f"（{min(lengths)}字 〜 {max(lengths)}字）。"
                "長いほうが本命に読めます（最長は最短の 2.0倍まで）"
            )
        lines = [wrapped_lines(t) for t in items]
        if len(set(lines)) != 1:
            raise ValueError(
                f"{e.id}.possibilities: 折り返しの行数が揃っていません"
                f"（{'/'.join(str(n) for n in lines)} 行、"
                f"全角換算 {'/'.join(f'{display_width(t):g}' for t in items)} 字幅、"
                f"1行 {POSSIBILITY_COLUMNS} 字）。"
                "読む側が測るのは字数ではなく高さで、"
                "1つだけ2行になっている項目はそれだけで本命に見えます"
            )
        for text in items:
            for word in LEANING_WORDS:
                if word in text:
                    raise ValueError(
                        f"{e.id}.possibilities: 本命を示唆する語「{word}」が"
                        "入っています。どれがありうるかは書けますが、"
                        "どれが本当かは書けません（原則1）"
                    )


# 資料から「その資料にしか無い語」を切り出すための刻み。
#   ・英数字まじりの字面（`02:15` `nightly-cloud-sync` `CHG-2026-0271`）
#   ・カタカナの連なり4字以上（「バックアップ」「スケジュールタスク」）
#   ・漢字の連なり3字以上（「起動時刻」「実行履歴」「平均転送量」）
# 下限を置いているのは、「毎日」「記録」「時刻」のような、どの資料にも出る
# 2字の熟語まで拾うと、可能性の列挙が日本語で書けなくなるため。
_MATERIAL_TOKEN = re.compile(
    r"[0-9A-Za-z][0-9A-Za-z:._\-/]{3,}"
    r"|[ァ-ヶー]{4,}"
    r"|[一-龥]{3,}"
)


def material_tokens(text: str) -> set[str]:
    """その文にしか無いかを比べるための、資料の刻み。"""
    return {m.group(0) for m in _MATERIAL_TOKEN.finditer(text or "")}


def possibility_leaks(sc: "Scenario") -> list[tuple[str, str]]:
    """可能性の列挙が渡してしまっているものを、全部並べる（原則1 / SPEC 3.4）。

    **ローダと測定器で判定を2つ書かない。** ローダは最初の1件で止まり、
    `tools/balance.py` は全部を並べる — 同じことを別々に実装すると、
    片方だけが更新されたまま何年も通る（過去に2度やっている）。

    見るのは2種類。

    ①**この盤面の固有名**。資産の呼び名（`names_asset` — id・label・
      括弧内・`aliases`）、証拠の id、アクションの id とラベル。
      列挙は「読みの種類」を並べる欄であって、誰かを名指しする欄ではない。
      **ブリーフィングの免除は置かない** — 問い文（5.5）と違い、
      列挙は盤面のどの名前が無くても書ける。

    ②**まだ買っていない棄却材料の中身**。これが列挙を足すときの一番大きい
      危険である。「毎日 02:15 に起動するバックアップジョブかもしれません」と
      書けば、誤導は誤導でなくなる。台帳を引く手は 25分の値札が付いたまま、
      誰も押す理由が無くなる — 盤面から手が1本死ぬ。
      比べる相手は棄却材料の `summary` と `content`（買って初めて手に入る
      資料そのもの）だけにする。`reading` は比べない — あれは資料ではなく
      assisted の注釈で、「平常時の姿と比べる」のような一般論まで
      禁止語に変えてしまう。その証拠自身に既に出ている語も除く
      （もう渡してあるものを繰り返しても、新しいことは渡していない）。

    **どちらも完全な判定ではない。** 刻みに引っかからない言い換えをすれば
    通る。構造側の保証は `tools/balance.py` の「列挙だけで棄却するプレイが、
    材料を買うプレイに勝たない」が担う。
    """
    out: list[tuple[str, str]] = []
    ev_ids = sorted(e.id for e in sc.evidence)
    by_id = sc.evidence_by_id

    for e in sc.evidence:
        own = material_tokens(" ".join((e.summary, e.content, e.reading)))
        secrets: list[tuple[str, set[str]]] = []
        if e.misleading:
            for rid in e.refuted_by:
                other = by_id.get(rid)
                if other is not None:
                    secrets.append(
                        (rid, material_tokens(other.summary + " " + other.content) - own)
                    )

        for text in e.possibilities:
            low = text.casefold()
            for asset in sc.world.assets:
                name = names_asset(text, asset)
                if name:
                    out.append((e.id, f"資産の呼び名「{name}」"))
            for other_id in ev_ids:
                if other_id.casefold() in low:
                    out.append((e.id, f"証拠の id「{other_id}」"))
            for act in sc.actions:
                if act.id.casefold() in low or (act.label and act.label in text):
                    out.append((e.id, f"アクション「{act.id}」の名指し"))
            for rid, secret in secrets:
                hit = sorted((t for t in secret if t in text), key=len, reverse=True)
                if hit:
                    out.append((e.id, f"棄却材料 {rid} にしか無い語「{hit[0]}」"))
    return out


def _reject_possibilities_that_leak(sc: "Scenario") -> None:
    """列挙が固有名や棄却材料を渡していたら拒否する（判定は possibility_leaks）。"""
    leaks = possibility_leaks(sc)
    if leaks:
        eid, what = leaks[0]
        raise ValueError(
            f"{eid}.possibilities: {what}が入っています。"
            "列挙が書けるのは可能性の空間と、それを決める方向までです。"
            "どれなのかを決める資料は、手で買わせてください"
            f"（ほかに {len(leaks) - 1} 件）"
            if len(leaks) > 1 else
            f"{eid}.possibilities: {what}が入っています。"
            "列挙が書けるのは可能性の空間と、それを決める方向までです。"
            "どれなのかを決める資料は、手で買わせてください"
        )


# ─────────── 事件の時計の検査（SPEC 5.4 / 7.6.17 / v1.47） ───────────

_DATE_IN_LOG = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}")


def _reject_occurrences_not_in_the_log(sc: "Scenario") -> None:
    """時間軸に出す時刻が、その資料に本当に書かれているか（原則2）。

    `occurred_at` は **content の再掲**である。時間軸は学習者が既に
    持っている紙の上の時刻を並べ直す場所であって、新しい開示ではない。
    content に無い時刻を欄に書けてしまうと、作者は
    「ログには載っていないが本当はこの時刻だった」を配れる — それは
    手で買わせるべき資料であり、欄で配ってよいものではない。

    見るのは2つ。

    ①**時刻（HH:MM）が content に文字列として出ていること。**
      `2026-03-14T02:17:33Z` でも `02:41:07` でも部分一致で拾える。
    ②**日付が content の日付と食い違っていないこと。** 日付を1つも
      書いていない資料（メモリのプロセス一覧など）は①だけを見る —
      そこに日付を要求すると、資料として不自然な行を足すことになる。
    """
    for e in sc.evidence:
        if not e.occurred_at:
            continue
        day, clock = e.occurred_at.split(" ")
        if clock not in e.content:
            raise ValueError(
                f"{e.id}.occurred_at: 時刻 {clock} が content に出ていません。"
                "時間軸に出せるのは、その資料に書かれている時刻だけです"
            )
        days = {d.replace("/", "-") for d in _DATE_IN_LOG.findall(e.content)}
        if days and day not in days:
            raise ValueError(
                f"{e.id}.occurred_at: 日付 {day} が content の日付 {sorted(days)} に"
                "ありません。時間軸に出せるのは、その資料に書かれている日だけです"
            )


def occurrence_split(sc: "Scenario") -> dict[str, int]:
    """時刻を持つ／持たないを、誤導と本物で数えた 2×2。

    **ローダと測定器で数え方を2つ書かない**（possibility_leaks と同じ）。
    ローダは空きのある表を拒否し、`tools/balance.py` は比を見る。
    """
    out = {"mis_with": 0, "mis_without": 0, "real_with": 0, "real_without": 0}
    for e in sc.evidence:
        side = "mis" if e.misleading else "real"
        out[f"{side}_{'with' if e.occurred_at else 'without'}"] += 1
    return out


def _reject_occurrence_times_that_mark_the_trap(sc: "Scenario") -> None:
    """時刻の有無が、誤導の印になっていないか（原則2 / SPEC 3.4）。

    新しい欄を足すたびに、盤面には**新しい目印**が生まれる。
    周4 は収集範囲の欄をネガティブ所見にだけ付けて「白の印」を作り、
    v1.46 は列挙の厚みで同じことをやりかけた。**欄の有無は、
    それ自体が読める。**

    ここが禁じるのは 2×2 の空き — 誤導が全部時刻を持つ（本物には
    持たないものがいる）、あるいは誤導が1つも時刻を持たない
    （本物には持つものがいる）。どちらも「時間軸に載るか」を見るだけで
    誤導を振り分けられてしまう。

    誤導が1件しかない盤面では見ない（1件では有無の分布に意味が無い）。
    細かい偏りは `tools/balance.py` の比（0.7〜1.3）が見る。
    """
    n = occurrence_split(sc)
    if not (n["mis_with"] + n["real_with"]):
        return  # 事件の時計を使っていない盤面
    if n["mis_with"] + n["mis_without"] < 2:
        return
    if n["mis_with"] == 0 and n["real_with"]:
        raise ValueError(
            "occurred_at: 誤導だけが時刻を持っていません"
            f"（誤導 0/{n['mis_without']} 件・本物 {n['real_with']}件に時刻あり）。"
            "時間軸に載らないことが、そのまま誤導の印になります"
        )
    if n["mis_without"] == 0 and n["real_without"]:
        raise ValueError(
            "occurred_at: 誤導だけが全部時刻を持っています"
            f"（誤導 {n['mis_with']}/{n['mis_with']} 件・"
            f"時刻を持たない本物 {n['real_without']}件）。"
            "時間軸に載ることが、そのまま誤導の印になります"
        )


def _reject_ambiguous_asset_names(sc: "Scenario") -> None:
    """同じ呼び名が2つの資産を指していないか（SPEC 7.1 / 5.6）。

    漏洩検査は名前で照合する。同じ語が2つの資産の名前になっていると、
    どちらを名指ししたのかが決まらない — 片方に許された名指しが、
    もう片方の漏洩を素通りさせる。`aliases` は著者が自由に書けるので、
    ここを見ないと衝突が黙って入る。

    **空白だけの別名も拒否する。** 空文字は `casefold` の比較で
    どんな文にも一致し、全資産の漏洩検査を無効にする。
    """
    seen: dict[str, str] = {}
    for a in sc.world.assets:
        for alias in a.aliases:
            if not alias.strip():
                raise ValueError(f"{a.id}: 空の aliases は書けません")
        for name in sorted(asset_names(a)):
            key = name.casefold()
            if key in seen and seen[key] != a.id:
                raise ValueError(
                    f"呼び名「{name}」が {seen[key]} と {a.id} の両方を指しています。"
                    "漏洩検査はどちらを名指ししたのか決められません"
                )
            seen[key] = a.id


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


def _reject_toothless_constraints(sc: "Scenario") -> None:
    """発火しようのない方針制約を拒否する（SPEC 5.3）。

    `evaluate` は知らない型を黙って無視し、各判定も足りないフィールドを
    黙って False で返す。**書けるが何も起きない制約**が作れるということで、
    症状は「方針の名乗りだけが変わって、盤面では何も変わらない」になる。
    `require_before` に `action_type` を書き忘れた版は、実測すると
    どの方針でも同じ手が最良になり、その理由は読んでも分からない。

    型ごとの必須フィールドは `constraints.REQUIRED_FIELDS` が持つ。
    **判定と必須条件を2箇所に書かない** — 制約型を1つ足したときに
    片方だけ更新される（過去に同じ形の食い違いを2度出している）。
    """
    from .constraints import known_types, required_fields

    for p in sc.policies:
        for c in p.constraints:
            if c.type not in known_types():
                continue
            for field in required_fields(c.type):
                if not getattr(c, field, None):
                    raise ValueError(
                        f"{p.id}: 制約 {c.type} に {field} がありません。"
                        "この制約は永久に発火しません"
                    )


def _reject_policies_that_forbid_every_stop(sc: "Scenario") -> None:
    """侵害資産を止める手を、方針が**全部**禁じていないか（SPEC 5.3 / 3.8）。

    `forbid_action` は「同じ資産を止める複数の手のうち、この方針では
    こちらを取れ」と言うための道具である。ある資産の手を全部禁じると、
    その方針を選んだ学習者は**止めれば違反、止めなければ完全度が落ちる** —
    払えない要求になる。

    同梱シナリオでは fs01 に手が3つあり、証拠保全最優先が2つ、
    被害最小化最優先が2つを禁じている。**手を1つ消すと、どちらかの方針が
    黙って詰む。** 消したことと詰んだことは離れた場所で起きるので、
    読んでも気づけない。
    """
    stops: dict[str, set[str]] = {}
    for a in sc.actions:
        if a.type != ActionType.CONTAIN:
            continue
        for t in a.targets:
            stops.setdefault(t, set()).add(a.id)

    for p in sc.policies:
        banned = {
            aid
            for c in p.constraints
            if c.type == "forbid_action"
            for aid in c.action_ids
        }
        for asset in sc.world.ground_truth.compromised:
            available = stops.get(asset, set())
            if available and available <= banned:
                raise ValueError(
                    f"{p.id}: 侵害資産 {asset} を止める手を全部禁じています"
                    f"（{sorted(available)}）。止めれば違反、止めなければ"
                    "完全度が落ちる — この方針を選んだ学習者に逃げ道が無い"
                )


def _reject_toothless_secures(sc: "Scenario") -> None:
    """時計から何も守らない `secures` を拒否する（SPEC 5.6.3）。

    `secures` が効くのは **timeline の `destroys` に対してだけ**である。
    どの出来事もその証拠を奪わないなら、この手は 20分を払って
    何も変えないボタンになる。症状が出るのは書いた場所ではなく
    **均衡の側**で、「仕掛けを先に押す意味がある」と信じて書いた盤面が、
    実測すると押しても押さなくても同じ、という形で現れる。
    `_reject_toothless_timeline`（何も奪わない出来事）と同じ形の検査で、
    見ている向きが逆になっただけである。

    **自分の手の `destroys` は数えない。** そちらから守る力は
    そもそも持たせていない（排他を消してしまうため）。
    """
    taken_by_world = {e for ev in sc.timeline for e in ev.destroys}
    for a in sc.actions:
        idle = sorted(set(a.secures) - taken_by_world)
        if idle:
            raise ValueError(
                f"{a.id}: どの出来事も奪わない証拠を secures しています: {idle}。"
                "secures が効くのは timeline の destroys に対してだけなので、"
                "この手は時間を払って何も変えないボタンになります"
                "（自分の手の destroys からは、そもそも守れません）"
            )


def _reject_prerequisite_action_cycle(sc: "Scenario") -> None:
    """`requires_actions` の循環を拒否する（SPEC 5.6.2）。

    輪になった前提はどれも永久に押せない。`_reject_unreachable_actions` は
    「手がかりを辿って到達できるか」を見るので輪も検出はするが、
    メッセージが「到達できないアクション」になり、
    原因が前提の輪であることが読み手に伝わらない。
    """
    by_id = {a.id: a for a in sc.actions}
    state: dict[str, int] = {}

    def walk(aid: str, path: list[str]) -> None:
        if state.get(aid) == 2:
            return
        if state.get(aid) == 1:
            cycle = path[path.index(aid):] + [aid]
            raise ValueError(f"requires_actions が循環しています: {' → '.join(cycle)}")
        state[aid] = 1
        for nxt in by_id[aid].requires_actions:
            walk(nxt, path + [aid])
        state[aid] = 2

    for a in sc.actions:
        walk(a.id, [])


def _reject_unsatisfiable_capture_requirement(sc: "Scenario") -> None:
    """`require_capture_of_target` を、守れない方針にしていないか（SPEC 5.3）。

    この制約は「止める資産ごとに、その資産の揮発性証拠を先に取れ」と言う。
    侵害資産に届く capture の手が盤面に無いと、**その方針を選んだ学習者は
    何をしても違反する** — compromised を覆えば違反、覆わなければ完全度が
    落ちる。選べるが守れない方針は、方針ではなく罠である。

    `_reject_uncontainable_compromise`（止める手が無い侵害資産）と
    同じ形の検査で、見ている軸が「止められるか」ではなく
    「方針どおりに止められるか」に変わっただけである。
    """
    for p in sc.policies:
        tags = {
            t
            for c in p.constraints
            if c.type == "require_capture_of_target"
            for t in c.prerequisite_tags
        }
        if not tags:
            continue
        covered = {
            asset
            for a in sc.actions
            if tags & set(a.tags)
            for asset in a.investigates
        }
        # **拡散先も数える**（v1.48）。窓を跨いだプレイはその資産も
        # 止めることになるので、採取の手が無いとその方針では必ず違反する
        need = list(sc.world.ground_truth.compromised) + sorted(spread_targets(sc))
        missing = [a for a in need if a not in covered]
        if missing:
            raise ValueError(
                f"{p.id}: 揮発性証拠を取る手が無い侵害資産があります: {sorted(set(missing))}。"
                f"タグ {sorted(tags)} を持ち、その資産を investigates する"
                "アクションが要る（この方針では止めれば必ず違反になる）"
            )


def _reject_uncontainable_compromise(sc: "Scenario") -> None:
    """侵害された資産すべてに、それを直接止める手があるか（SPEC 6.3）。

    `contained_at` は依存連鎖しない。「上流の dc01 を落としたので
    ぶら下がっている資産も封じ込めた」ことにはならない — 電源を落としても
    端末の永続化は残り、稼働中の暗号化プロセスは動き続けるからである。

    したがって直接止める手が1つも無い資産が compromised にあると、
    `containment_completeness` は誰にも 1.0 に届かず、
    5.8 の `on_correct_containment` は永久に使われない。
    そのシナリオでは「正しく止める」ことが定義上できない。
    """
    targeted = {
        t
        for a in sc.actions
        if a.type == ActionType.CONTAIN
        for t in a.targets
    }
    # **拡散先も数える**（v1.48）。窓を跨いだプレイでは、その資産が
    # `compromised` に入る＝完全度の分母に入る。止める手が無いと、
    # 遅れたプレイは何をしても完全度 1.0 に届かない
    need = list(sc.world.ground_truth.compromised) + sorted(spread_targets(sc))
    missing = [a for a in need if a not in targeted]
    if missing:
        raise ValueError(
            f"直接止める手が無い侵害資産があります: {sorted(set(missing))}。"
            "封じ込めは依存連鎖しないため（6.3）、上流を止めても代わりにならない"
        )


def _reject_uneradicable_persistence(sc: "Scenario") -> None:
    """永続化が残る資産すべてに、それを根絶する手があるか（SPEC 5.8 / 6.3）。

    5.8 の減衰は `compromised ⊆ contained` かつ `persistence ⊆ eradicated`
    のときにだけ `on_correct_containment` を返す。根絶する手が1つも無い資産が
    `persistence` にあると、**その条件は誰にも満たせない** —
    どれだけ正しく止めても被害は `on_partial` から下がらず、
    盤面で最良の対応が存在しないことになる。

    逆に、この検査が無いまま `persistence` を書くと何が起きるかは
    実測してある（9.4 #5）。`persistence ⊆ compromised` はローダが
    要求しているので、根絶を `contained` で判定していた頃の条項は
    **恒真**だった。128通り全数で、条項の有無が結果を変える組は 0個。
    `ground_truth.persistence` は完全な死にフィールドだった。
    """
    eradicable = {
        t
        for a in sc.actions
        if a.type == ActionType.CONTAIN
        for t in a.eradicates
    }
    missing = [a for a in sc.world.ground_truth.persistence if a not in eradicable]
    if missing:
        raise ValueError(
            f"根絶する手が無い永続化があります: {sorted(missing)}。"
            "eradicates を持つ contain アクションが無いと、"
            "5.8 の on_correct_containment には誰も到達できない"
        )


def _reject_eradication_that_maps_persistence(sc: "Scenario") -> None:
    """取り除く手の当たり先が、`ground_truth.persistence` の写しになっていないか。

    封じ込めの手は `requires_evidence` を持てない（SPEC 5.6）ので、
    **開始0分・0アクションで全部読める。** そこに「元の状態に戻す」という束が
    あり、その束の手だけが `eradicates` を持ち、当たり先が persistence と
    完全に一致していると、**束の名前がそのまま答えの一部になる。**

    実測してある（v1.45）。2本目の盤面では、被疑判定が応答フェーズ中も
    再宣言できること（3.6）と合わせて、0分・0手・証拠0件のまま
    空宣言 → 束を読む → 宣言し直すだけで適合率 1.00・再現率 1.00 が出た。
    256通りの総当たりで、0手時点の最高得点がこれだった。

    隔離の束には無実の資産の手が混ざっていた（ws-107 / app-scan /
    acct-admin）のに、**囮があるのは止める側だけだった。**

    だから要求は2つある。

      1. 取り除く手の当たり先が、侵害資産の中に収まっていないこと。
         少なくとも1つは無実の資産へ届いていること（＝囮）。
         等しくないだけでは足りない — compromised の部分集合に収まる限り、
         その束は「どれが侵害されているか」を配り続ける。
      2. どの束についても、その束が取り除く先が persistence と
         一致しないこと。囮を別の束に置いて逃げられるため。

    **盤面の資産が全部 compromised なら、この検査は何もしない。**
    選り分けるものが無いところに選り分けは無い
    （`_reject_actions_that_name_other_assets` の「全員を名指し」と同じ理屈）。
    """
    gt = sc.world.ground_truth
    # 拡散先は「まだ侵害されていない」が無実でもない。囮には数えない —
    # 数えると、いずれ侵害される資産への手で囮の要件を満たせてしまう
    secret = set(gt.compromised) | {gt.patient_zero} | spread_targets(sc)
    outsiders = {a.id for a in sc.world.assets} - secret
    if not outsiders:
        return

    purgers = [a for a in sc.actions if a.eradicates]
    reach = {t for a in purgers for t in a.eradicates}
    if reach and reach <= secret:
        raise ValueError(
            f"取り除く手の当たり先が侵害資産の中に収まっています: {sorted(reach)}。"
            "封じ込めの手は開始0分で全部読めるので、そのままでは"
            "「取り除くものが残っているのはどれか」を配ります。"
            f"無実の資産（{sorted(outsiders)}）にも取り除く手を置いて、"
            "囮を作ってください"
        )

    persistence = set(gt.persistence)
    by_group: dict[str, set[str]] = {}
    for a in purgers:
        by_group.setdefault(a.group, set()).update(a.eradicates)
    for group, reached in by_group.items():
        if persistence and reached == persistence:
            raise ValueError(
                f"束「{group}」が取り除く先が persistence と一致しています: "
                f"{sorted(reached)}。束の名前がそのまま答えの一部になります"
            )


def _reject_stops_that_erase_without_stopping(sc: "Scenario") -> None:
    """通り道を塞ぐだけの封じ込めが、稼働中の痕跡を消していないか（SPEC 6.3 / 5.6）。

    境界での遮断や論理的な切り離しは、資産に手を触れずに通り道だけを塞ぐ手で、
    そこから揮発性の情報が消える理由は無い。ここを許すと
    「業務影響ゼロで証拠だけ消える」手が書けてしまい、
    業務継続と証拠保全のどちらの方針からも一方的に安い抜け道になる。

    **除くのは `eradicates` を持つ手である**（v1.38）。取り除く手は定義上、
    その資産に残されたものを消しに行くのだから、消しに行った先の
    「現在の状態」が消えるのは当たり前で、業務が止まるかどうかとは関係ない。

    この条件はもともと `business_impact: false` だけで書かれていた。
    「業務が止まる」と「資産が動き続ける」が一致する世界
    （端末は電源を切ると業務も止まる）でしか正しくない近似で、
    2本目のシナリオ（SaaS）で破れた — 委任同意を利用者ごとに取り消す手は、
    生きている同意の一覧を確実に消すが、業務は1分も止まらない。
    そこで `business_impact: true` と書かせるのは世界について嘘をつくことで、
    `volatile: false` と書かせるのは証拠について嘘をつくことだった。

    抜け道が開かないのは、取り除く手が盤面で最も価値のある手だからである。
    「一方的に安い」ことはありえない。
    """
    volatile = {e.id for e in sc.evidence if e.volatile}
    for a in sc.actions:
        if a.type != ActionType.CONTAIN or a.side_effects.business_impact:
            continue
        if a.eradicates:
            continue
        bad = sorted(set(a.destroys) & volatile)
        if bad:
            raise ValueError(
                f"{a.id}: 通り道を塞ぐだけの封じ込めが揮発性の証拠を消しています: {bad}。"
                "資産に手を触れていないなら、稼働中の痕跡は消えません"
                "（取り除く手なら eradicates を書いてください）"
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

    門は2種類ある。`requires_evidence` は「どれか1つ持っていれば」で開き、
    `requires_actions` は「全部押し終えていれば」で開く（SPEC 5.6.2）。
    **両方を辿らないと、仕掛けの手を消したときに読解の手が死蔵になったことに
    気づけない。**
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
            if not set(a.requires_actions) <= reached:
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
