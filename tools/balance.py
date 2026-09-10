"""シナリオの均衡を実測する（SPEC 8.2 手順7 / 8.3 のチェックリスト）。

設計の良し悪しは、読んで決めるものではなく走らせて決めるものである。
「網羅は損になっているか」「折れ点は誰かに踏まれるか」「この手は
一度でも押す価値があるか」は、いずれも数字にできる。

    python tools/balance.py [シナリオID] [--json]

数字にできないもの（分かりやすさ、面白さ）は tools/playtest.mjs の側で見る。

**測定は盤面の全部を通す**（SPEC 8.2「測定は、盤面の全部を通す」）。
調査を並べて終わりにすると封じ込めが一度も実行されず、
`ground_truth` をそのまま宣言すると事実認識層が全プレイ同値になる。
どちらも「判定が全部通っている」状態と同居する。

**プレイ像が使ってよいのは画面に出ているものだけ。**
`misleading` や `ground_truth` を見て動く像は、実在しない学習者である。
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from irdojo import loader, retrospective, scoring
from irdojo.engine import Decision, Engine, InvalidDecision
from irdojo.schema import ActionType, Scenario, briefing_assets

DEFAULT_SCENARIO = "ransomware-initial-response-01"


# ─────────── プレイ像 ───────────


@dataclass
class Profile:
    """一人の学習者の振る舞い。実測はすべてこれを通す。"""

    key: str
    label: str
    note: str
    order: Callable[[Scenario], list[str]]
    # critical 論点が片付いた時点で調査をやめるか。
    # 実在の学習者は「分かった」と思ったら動く。最後まで押し切るのは
    # 網羅プレイだけで、それを全プレイ像の既定にすると
    # 「遅いが取りこぼす」という一番ありふれた失敗が測れなくなる
    stop_when_confident: bool = True
    # 我慢の限界（仮想分）。実在の学習者は完全性ではなく予算で降りる。
    # 現場にも「そろそろ動け」という圧があり、被害モデル自体が
    # 長い調査を罰している。全アクションを押し切る像だけを見ていると、
    # 「遅いうえに取りこぼす」という一番ありふれた失敗が測れない。
    # 特定の出来事の時刻に合わせて決めないこと（それは測定ではなく作文）
    patience_minutes: int | None = None
    # 手元の証拠に付いた「別の説明」を、最終判断まで持ち帰るか。
    # 定時ジョブの台帳を引いた**あとで**なお ws-107 を名指しするかどうかは、
    # 調査量ではなく読みの問題である。このシナリオが教えたい失敗は
    # 「調べ足りない」ではなく「調べたのに結び付けない」なので、
    # ここを像の性質として分けないと、誤導は一度も判定に現れない
    weighs_refutations: bool = True
    # 封じ込めの選び方。False なら「自分が名指しした資産に届く手」だけを押す。
    # True は取捨選択をしない像（調査と同じ振る舞いを対応でも取る）
    contains_everything: bool = False
    # 名指しした資産に「作り直す」手があるなら、そちらを選ぶか。
    # 画面に出ているのはラベルと所要だけなので、これは
    # ground_truth を読む像ではない — 「通信だけを止める」束と
    # 「作り直す」束のどちらを取るかという、値札を見た上での判断である。
    # この印が無いと、最安被覆は必ず安いほう（止めるだけ）を選び、
    # 根絶の手は一度も押されない
    eradicates_named: bool = False
    # 判定の外でも、依存の根に届く手を押すか。
    # `depends_on` は構成図として学習者に開示されている（7.6.7）ので、
    # 「根を落とせば全部止まる」は画面を見た人が普通に思いつく手である。
    # この像が無いと、盤面で最も高くつく資産が一度も止まらない
    contains_root: bool = False
    # 盤面の資産を全部名指しするか（`_assess` を迂回する）。
    # 「疑わしきは全部隔離」は、深夜に人手が無いときの定番の降り方である。
    # 手元の証拠から組み立てる `_assess` ではこの形が作れない
    names_everything: bool = False
    # 調査を1手も押さない像か。「調べる意味はあるか」の判定は、
    # この印が付いた像と付いていない像の**全対戦**で見る。
    # 1本だけ押して降りる像（shallow）はここに入らない —
    # 30分の1手でも調べたことは調べたことであり、
    # その最も薄い像にすら勝てないことを要求する方が検査は強い
    skips_investigation: bool = False


def _skilled(sc: Scenario) -> list[str]:
    """事後的に見た最短経路。答えを知っている者の上限。"""
    _minutes, ids, _exact = retrospective.minimal_path(sc)
    return list(ids)


def _exhaustive(sc: Scenario) -> list[str]:
    """全部押す。取捨選択をしない。"""
    return [a.id for a in sc.actions if a.type == ActionType.INVESTIGATE]


def _wanderer(sc: Scenario) -> list[str]:
    """誤導を追い、揮発性を後回しにする。初学者の典型。

    「安いものから」「誤導が指す資産から」順に潰し、
    揮発性の証拠を出すアクションを最後に回す。
    """
    misled_assets = {a for e in sc.evidence if e.misleading for a in e.points_to}
    volatile = {e.id for e in sc.evidence if e.volatile}

    def rank(a) -> tuple:
        yields_volatile = bool(set(a.yields) & volatile)
        chases_misled = bool(set(a.investigates) & misled_assets)
        return (yields_volatile, not chases_misled, a.cost_minutes)

    acts = [a for a in sc.actions if a.type == ActionType.INVESTIGATE]
    return [a.id for a in sorted(acts, key=rank)]


def _nothing(sc: Scenario) -> list[str]:
    """調べない。ブリーフィングだけを根拠に動く。

    実在する。深夜に叩き起こされて「fs01 でファイル名が変わっている」と
    言われたら、まず fs01 を落とす — それは現場では珍しくない判断である。
    この像が調べた像に勝つなら、この演習は調査を訓練していない。
    """
    return []


def _shallow(sc: Scenario) -> list[str]:
    """ブリーフィングが名指しした資産に届く手を、1つだけ押して降りる。

    「fs01 のログを見たら ws-042 から来ていた。もう十分」は、
    現場で最もありふれた降り方である。`hasty` の 0手だけを置いていると、
    この形（1本で確信する）が一度も測られない。
    最初から押せる手のうち、その資産に届く最安のものを1つ。
    """
    named = set(briefing_assets(sc))
    acts = [
        a for a in sc.actions
        if a.type == ActionType.INVESTIGATE
        and set(a.investigates) & named
        and not a.requires_evidence
    ]
    if not acts:
        return []
    return [min(acts, key=lambda a: (a.cost_minutes, a.id)).id]


PROFILES = [
    Profile("skilled", "巧い", "最短経路のあと、作り直すところまで行く", _skilled,
            eradicates_named=True),
    # skilled と**調査量が1分も違わない**対照。違うのは封じ込めの束だけ。
    # これが無いと「根絶したか」の差を調査量の差と切り分けられない
    # （誤導を追う／棄却する の対で使ったのと同じ作り方）
    Profile("skilled_halfway", "止めるだけ",
            "同じ調査量で、通信を断つところまでで降りた場合", _skilled),
    Profile("exhaustive", "全部押す", "取捨選択をしない", _exhaustive,
            stop_when_confident=False, contains_everything=True),
    Profile("wanderer", "誤導を追う", "棄却の材料を持ったまま名指しする", _wanderer,
            patience_minutes=300, weighs_refutations=False),
    # wanderer と**調査量が1分も違わない**対照。違うのは宣言の作り方だけ。
    # これが無いと「誤導は損か」を調査量の差と切り分けられない
    Profile("wanderer_clear", "誤導を棄却する", "同じ調査量で、棄却を判定に反映した場合",
            _wanderer, patience_minutes=300),
    # 「調べずに動く」には形が2つある。0手・1資産だけを代表にしていた頃は、
    # **その中の最弱の1つ**を相手に「調べない像は最下位」と言っていた。
    # 「疑わしきは全部隔離」は同じ 0手でも遥かに強い（適合率は落ちるが
    # 再現率は満点になり、被害も止まる）
    Profile("hasty", "調べずに止める", "ブリーフィングだけで名指しして封じ込める",
            _nothing, skips_investigation=True),
    Profile("blanket", "疑わしきは全部", "調べずに盤面の資産を全部名指しして止める",
            _nothing, names_everything=True, skips_investigation=True),
    # 逆側の弱い敵。**調べた側の下限**を、網羅や誤導追いではなく
    # 「1本読んで確信した人」に置く。調べない像は、この 30分の1手にも
    # 勝ってはならない
    Profile("shallow", "1本で確信する", "手を1つだけ押して、もう十分だと判断する",
            _shallow),
    # 構成図の根が dc01 であることは開始時から画面に出ている。
    # 「根を落とせば全部止まる」は、それを見た人が普通に思いつく手であり、
    # **対応フェーズで最も高くつく選択肢**でもある。この像が無いと
    # 依存の根を止める手が一度も押されず、封じ込めの取捨選択が測れない
    Profile("decapitate", "根元を落とす", "依存の根を止めれば全部止まると考える",
            _skilled, contains_root=True),
]


# ─────────── 実行 ───────────


@dataclass
class Run:
    profile: str
    with_notice: bool
    minutes: int = 0
    damage: float = 0.0
    lost: list[str] = field(default_factory=list)
    # そのうち、世界の側に奪われた分。自分の手で壊した分と区別する
    # （封じ込めで自分が消したものを「奪われた」に数えると、
    #   止めた人ほど世界に責められることになる）
    lost_to_world: list[str] = field(default_factory=list)
    assessment: list[str] = field(default_factory=list)
    decided_at: int = 0          # 被疑判定を宣言した時刻。折れ点は判断の長さを罰する
    contained: list[str] = field(default_factory=list)
    # そのうち、永続化まで取り除いた分。止めただけの資産と区別しないと
    # 「何を止めたか」の表が、5.8 の減衰がどちらだったかを説明できない
    eradicated: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    averted: list[str] = field(default_factory=list)
    by_policy: dict[str, dict] = field(default_factory=dict)


def _play(
    sc: Scenario, order: list[str], notices: list[str],
    *, stop: bool, patience: int | None,
    weighs_refutations: bool, contains_everything: bool,
    contains_root: bool = False,
    eradicates_named: bool = False,
    names_everything: bool = False,
    containment: list[str] | None = None,
    assessment: list[str] | None = None,
) -> Engine:
    """order を実行可能になった順に消化し、宣言して封じ込めるまでを通す。"""
    e = Engine(sc, sc.meta.default_policy, None)
    for nid in notices:
        e.decide(Decision(kind="action", action_id=nid))

    critical = {q.id for q in sc.open_questions if q.critical}
    todo = list(order)
    while todo:
        if patience is not None and e.state.elapsed_minutes >= patience:
            break
        # 「分かった」と思ったら調査をやめる。
        # **奪われて解けなくなった論点を打ち切りに数えてはいけない。**
        # プレイ中に損失は告げないので（8.2 手順6）、学習者には
        # 「もう無理」が見えていない。見えないものを根拠に降りる像は、
        # 実在しないだけでなく「奪われた方が早く降りられて得」という
        # 偽の均衡を作る（実測で -7 点の逆転が出た）
        if stop and critical <= set(e.state.resolved_questions):
            break
        for aid in list(todo):
            try:
                e.decide(Decision(kind="action", action_id=aid))
            except InvalidDecision:
                continue
            todo.remove(aid)
            break
        else:
            break  # どれも実行できない＝手詰まり

    # 宣言は**手元の材料から組み立てる**。ground_truth は読まない。
    # 以前はここで truth.compromised をそのまま宣言していて、
    # 全プレイが precision 1.0 / recall 1.0 / persistence_missed 0.0 になり、
    # 事実認識層の重み 0.60 分が定数になっていた。
    # このシナリオの教育の中心（誤導に乗って ws-107 を名指しする）が
    # 判定の数字に一度も現れていなかった
    # `assessment` を外から渡せるようにしてある。根拠の梯子（checks の
    # evidence_ladder）は**判定を揃えて根拠だけ変える**対照なので、
    # 像を足しても作れない — 同じ宣言のまま調査量だけを入れ替える必要がある
    if assessment is None:
        if names_everything:
            # 「疑わしきは全部」。手元の証拠ではなく、盤面に見えている
            # 資産をそのまま名指しする。構成図は開始時から出ている（7.6.7）
            assessment = [a.id for a in sc.world.assets]
        else:
            assessment = _assess(sc, e.state, weighs_refutations=weighs_refutations)
    e.decide(Decision(kind="declare_assessment", assessment=assessment))

    # 宣言を要求するフェーズへはエンジンがそのまま進める（engine._declare）。
    # ここを呼ばずに終わっていたので、対応フェーズのアクションは
    # 一度も実行されず、containment_completeness は全プレイ 0.00、
    # 被害の減衰係数は全プレイ on_incorrect 固定だった
    # 封じ込めの組を外から差し替えられるようにしてある。
    # 対応フェーズの取捨選択は、像を1本足しても測れない — 同じ調査のあとに
    # 組だけを入れ替えて比べる必要がある（checks の containment_sweep）
    chosen = containment if containment is not None else _containment(
        sc, assessment, everything=contains_everything, root=contains_root,
        eradicate=eradicates_named,
    )
    for aid in chosen:
        try:
            e.decide(Decision(kind="action", action_id=aid))
        except InvalidDecision:
            continue

    e.decide(Decision(kind="finish"))
    return e


def _assess(sc: Scenario, state, *, weighs_refutations: bool) -> list[str]:
    """学習者が画面から組み立てる被疑判定。

    使ってよいのは画面に出ているものだけ:
      - ブリーフィングが最初に名指しした資産（一次情報として配られている）
      - 手元の証拠が指す資産

    `refuted_by` は学習者に渡らないが、**棄却の材料そのもの**（定時ジョブの
    台帳、パッチ作業の予定表）は証拠として手元にある。それを読んで
    名指しを取り下げるかどうかが、この像の分かれ目である。
    `misleading` は見ない — 見た瞬間に「答えを知っている像」になる。
    """
    got = set(state.obtained_evidence)
    by_id = sc.evidence_by_id
    named = set(briefing_assets(sc))
    for eid in state.obtained_evidence:
        ev = by_id.get(eid)
        if ev is None:
            continue
        if weighs_refutations and set(ev.refuted_by) & got:
            continue
        named |= set(ev.points_to)
    return [a.id for a in sc.world.assets if a.id in named]


def _dependency_roots(sc: Scenario) -> set[str]:
    """依存の根。他が依存していて、自分は何にも依存していない資産。

    `depends_on` は構成図として学習者に開示されている（3.10 / 7.6.7）ので、
    ここを読む像は答えを見ていない。画面を見た人が普通に思いつく手である。
    """
    depended = {d for a in sc.world.assets for d in a.depends_on}
    return {a.id for a in sc.world.assets if a.id in depended and not a.depends_on}


def cheapest_cover(
    sc: Scenario, wanted: set[str], eradicate: set[str] | None = None
) -> list[str]:
    """`wanted` の各資産を、最も安い手で1回ずつ覆う（同じ資産に二重に打たない）。

    画面に出ているのは**ラベルと所要**だけなので、方針を知らない像が
    使える基準は費用しかない。同点は一覧に出る順（作者が書いた順）で決める。

    `eradicate` に入れた資産は、**作り直す手があるならそちらで覆う。**
    費用だけで選ぶと必ず安いほう（止めるだけ）に倒れるので、
    根絶の手は盤面にあっても一度も押されない — その状態で測った L は
    「最良の対応にかかる時間」を過小に見積もる（9.4 #5）。
    根絶の手が無い資産は、これまでどおり最安の手で覆う。
    """
    want_erad = set(eradicate or ())
    acts = [a for a in sc.actions if a.type == ActionType.CONTAIN and a.targets]
    order = {a.id: i for i, a in enumerate(acts)}
    picked: list[str] = []
    covered: set[str] = set()
    for asset in [a.id for a in sc.world.assets if a.id in wanted]:
        if asset in covered:
            continue
        cands = [a for a in acts if asset in a.targets and set(a.targets) <= wanted]
        if asset in want_erad:
            purging = [a for a in cands if asset in a.eradicates]
            if purging:
                cands = purging
        if not cands:
            continue
        best = min(cands, key=lambda a: (a.cost_minutes, order[a.id]))
        picked.append(best.id)
        covered |= set(best.targets)
    return picked


def _containment(
    sc: Scenario, assessed: list[str], *, everything: bool, root: bool = False,
    eradicate: bool = False,
) -> list[str]:
    """封じ込めで実際に押す手。

    既定は「自分が名指しした資産を、**1資産につき1手**で止める」。
    同じ資産に二重に手を打つ像は実在しない — fs01 を停止した**うえで**
    その fs01 への SMB 遮断の変更承認を待つ人はいない。
    どちらを選ぶかは、方針を知らない像には費用でしか決められない。

    `everything` は取捨選択をしない像 — 調査で全部押した人は対応でも全部押す。
    `root` は「根元を落とせば全部止まる」と読む像 — 判定の外でも
    依存の根に届く手を押す。既定の像だけだと盤面で最も高くつく資産
    （依存の根）が一度も止まらず、封じ込めの費用が測れない。
    """
    if everything:
        return [a.id for a in sc.actions if a.type == ActionType.CONTAIN]
    # targets が判定の外へはみ出す手は押さない。
    # 「dc01 を止めれば全部止まる」は、dc01 を疑っていない人の手ではない
    # — ただし構成図を見て根を落としに行く人はいる（root=True）
    reachable = set(assessed) | (_dependency_roots(sc) if root else set())
    # `eradicate` は「作り直す束を選ぶ」像。名指しした資産のうち、
    # 作り直す手があるものはそちらで覆う（値札を見た上での判断であって、
    # ground_truth を読んだわけではない）
    return cheapest_cover(sc, reachable, reachable if eradicate else None)


def run_profile(sc: Scenario, prof: Profile, notices: list[str]) -> Run:
    e = _play(sc, prof.order(sc), notices,
              stop=prof.stop_when_confident, patience=prof.patience_minutes,
              weighs_refutations=prof.weighs_refutations,
              contains_everything=prof.contains_everything,
              contains_root=prof.contains_root,
              eradicates_named=prof.eradicates_named,
              names_everything=prof.names_everything)
    st = e.state
    r = Run(
        profile=prof.key,
        with_notice=bool(notices),
        minutes=st.elapsed_minutes,
        lost=list(st.lost_evidence),
        lost_to_world=[x for x in st.lost_evidence if x in st.destroyed_by_world],
        assessment=list(st.assessment),
        decided_at=st.assessment_snapshot.at_minute if st.assessment_snapshot else 0,
        contained=sorted(st.contained_at),
        eradicated=sorted(st.eradicated_at),
        unresolved=[q.id for q in sc.open_questions if q.id not in st.resolved_questions],
        fired=list(st.fired_events),
        averted=list(st.averted_events),
    )
    for p in sc.policies:
        s = scoring.score(st, sc, p)
        r.damage = s.consequences.total_damage
        r.by_policy[p.id] = {
            "composite": round(s.composite_score * 100),
            "fact": round(s.fact_score * 100),
            "policy": round(s.policy_score * 100),
            "violations": s.constraint_violations,
            "evidence_preserved": round(s.consequences.evidence_preserved, 3),
        }
    return r


# ─────────── 封じ込めの取捨選択（SPEC 3.8 / 8.3） ───────────


def containment_sweep(sc: Scenario) -> dict[tuple[str, ...], dict[str, int]]:
    """同じ調査のあとに、封じ込めの組だけを総当たりで入れ替えて採点する。

    **対応フェーズの排他は時間比では測れない。**
    フェーズの総コストが「そこで使える時間の2〜3倍」という検査（8.2 手順7）は
    調査フェーズのためのもので、対応フェーズには効かない — 対応は定義上
    L のあとに始まるので、折れ点までの予算は調査が使い切っている。
    同じ時間を二重に数えているだけで、封じ込め1手あたり60分にでもしない限り
    2倍には届かない。それは被害モデルが罰したいことと正反対の設計になる。

    SPEC 3.8 が答えを持っている — 「時間で罰することはできない — 排他で閉じる」。
    対応フェーズの排他は**不可逆性**（止めたものは復旧地平の間ずっと止まり、
    消した揮発性証拠は戻らない）なので、測るなら組ごとの帰結を比べるしかない。
    """
    order = _skilled(sc)
    acts = [a.id for a in sc.actions if a.type == ActionType.CONTAIN]
    out: dict[tuple[str, ...], dict[str, int]] = {}
    for r in range(len(acts) + 1):
        for combo in itertools.combinations(acts, r):
            e = _play(sc, order, [], stop=True, patience=None,
                      weighs_refutations=True, contains_everything=False,
                      containment=list(combo))
            out[combo] = {
                pol.id: round(scoring.score(e.state, sc, pol).composite_score * 100)
                for pol in sc.policies
            }
    return out


# ─────────── 網羅の押し順（SPEC 6.6 / 8.3） ───────────


def press_orders(sc: Scenario) -> dict[str, list[str]]:
    """「全部押す」の、実在しそうな押し順。

    **YAML の記載順1本で「網羅から critical 論点を奪っていない」を
    判定していた。** 記載順は作者の都合であって、学習者の押し順ではない。
    このシナリオでは記載順がたまたま 15分の余裕で通っており、
    `cost_minutes` を1つ触れば裏返る状態だった。
    画面を見た人が実際に取りうる並べ方を全部通す。
    """
    acts = [a for a in sc.actions if a.type == ActionType.INVESTIGATE]
    volatile = {e.id for e in sc.evidence if e.volatile}
    named = set(briefing_assets(sc))
    groups = {}
    for a in acts:
        groups.setdefault(a.group, len(groups))
    return {
        "画面の並び順": [a.id for a in acts],
        "安い順": [a.id for a in sorted(acts, key=lambda a: (a.cost_minutes, a.id))],
        "高い順": [a.id for a in sorted(acts, key=lambda a: (-a.cost_minutes, a.id))],
        "揮発性を先に": [
            a.id for a in sorted(acts, key=lambda a: not (set(a.yields) & volatile))
        ],
        "群ごと": [a.id for a in sorted(acts, key=lambda a: groups[a.group])],
        "ブリーフィングの資産から": [
            a.id for a in sorted(acts, key=lambda a: not (set(a.investigates) & named))
        ],
    }


def _lost_critical(sc: Scenario, run: Run) -> dict[str, str]:
    """未解消のまま終わった critical 論点を、原因で仕分ける。

    「解けなかった」には2つの原因があり、**設計上の意味がまるで違う**。

      時計 — 世界の側の出来事が、取りに行く前に証拠を消した。
             これは `communicate` の手で防げるべきものである（SPEC 5.6.1）
      排他 — 自分の手で、取りに行く前に証拠を消した。
             これは SPEC 3.8 が意図した「順序そのものが判断」である

    片方だけを見て「網羅を罰していない／いる」と言うと、必ず取り違える。
    """
    out: dict[str, str] = {}
    lost = set(run.lost)
    world = set(run.lost_to_world)
    for q in sc.open_questions:
        if not q.critical or q.id not in set(run.unresolved):
            continue
        missing = set(q.resolved_by) & lost
        if not missing:
            out[q.id] = "未取得"      # 単に押していない。奪われてはいない
        else:
            out[q.id] = "時計" if (missing & world) else "排他"
    return out


def run_order(sc: Scenario, order: list[str], notices: list[str]) -> Run:
    """指定の押し順で全部押し切って、封じ込めまで通す。"""
    e = _play(sc, order, notices, stop=False, patience=None,
              weighs_refutations=True, contains_everything=True)
    st = e.state
    return Run(
        profile="order", with_notice=bool(notices),
        minutes=st.elapsed_minutes,
        lost=list(st.lost_evidence),
        lost_to_world=[x for x in st.lost_evidence if x in st.destroyed_by_world],
        assessment=list(st.assessment),
        contained=sorted(st.contained_at),
        eradicated=sorted(st.eradicated_at),
        unresolved=[q.id for q in sc.open_questions
                    if q.id not in st.resolved_questions],
    )


# ─────────── 根拠の梯子（SPEC 6.2） ───────────


def evidence_ladder(sc: Scenario) -> list[tuple[str, int, float]]:
    """**判定を揃えて、根拠の厚みだけを変える**対照。

    これが今回の直接の計器である。適合率・再現率だけを見ていた頃は、
    ブリーフィングの資産名を書き写しただけのプレイと、
    証拠を積んで同じ結論に達したプレイが同点だった。
    事実認識層の重みが「compromised と書けたか」の1ビットに落ちていた。

    宣言する内容は**巧いプレイが自分の証拠から組み立てたもの**を使う。
    `ground_truth` は読まない（読んだ瞬間に、実在しない像になる）。
    """
    skilled_order = _skilled(sc)
    ref = _play(sc, skilled_order, [], stop=True, patience=None,
                weighs_refutations=True, contains_everything=False)
    verdict = list(ref.state.assessment)
    contain = cheapest_cover(sc, set(verdict))

    rungs = [("0手", []), ("1本だけ", _shallow(sc)), ("最短経路", skilled_order)]
    out: list[tuple[str, int, float]] = []
    for label, order in rungs:
        e = _play(sc, order, [], stop=False, patience=None,
                  weighs_refutations=True, contains_everything=False,
                  containment=contain, assessment=verdict)
        out.append((label, e.state.elapsed_minutes,
                    scoring.score(e.state, sc).fact_score * 100))
    return out


def _covers_compromised(sc: Scenario, combo: tuple[str, ...]) -> bool:
    by_id = sc.action_by_id
    stopped = {t for aid in combo for t in by_id[aid].targets}
    return set(sc.world.ground_truth.compromised) <= stopped


# ─────────── 根絶（SPEC 5.8 / 9.4 #5） ───────────


def persistence_matters(sc: Scenario) -> list[tuple[str, ...]]:
    """5.8 の persistence 条項が、実際に結果を変える封じ込めの組を全数で拾う。

    **恒真な条項は書いていないのと同じである。** ローダは
    `persistence ⊆ compromised` を要求しているので、根絶を
    `contained` で判定していた頃は `compromised ⊆ contained` が成り立てば
    後半も自動的に成り立った。128通り全数で結果を変える組は 0個で、
    `ground_truth.persistence` は完全な死にフィールドだった（9.4 #5）。

    ここが 0件に戻ったら、盤面から根絶の手が消えた（あるいは
    根絶できる資産が persistence を覆ってしまった）ということである。
    """
    from irdojo import damage as damage_mod

    truth = sc.world.ground_truth
    effect = sc.damage.containment_effect
    by_id = sc.action_by_id
    acts = [a.id for a in sc.actions if a.type == ActionType.CONTAIN]
    out: list[tuple[str, ...]] = []
    for r in range(len(acts) + 1):
        for combo in itertools.combinations(acts, r):
            stopped = {t for aid in combo for t in by_id[aid].targets}
            purged = {t for aid in combo for t in by_id[aid].eradicates}
            real = damage_mod.containment_factor(stopped, purged, truth, effect)
            # 条項が無かった場合＝「止めれば根絶したことになる」旧実装
            naive = damage_mod.containment_factor(stopped, stopped, truth, effect)
            if real != naive:
                out.append(combo)
    return out


def eradication_choice(sc: Scenario) -> dict[str, dict[str, int]]:
    """根絶の手だけを入れ替えて、方針ごとの評価を比べる。

    **押せば必ず得になる手は、判断ではなく作業である。** 根絶そのものは
    物理的に必要（それしか on_correct_containment に届かない）だが、
    **どの根絶手を取るか**は方針で入れ替わらなければならない。
    残りの封じ込めは揃えて、根絶の手だけを差し替える。
    """
    erad = [a for a in sc.actions if a.eradicates]
    if len(erad) < 2:
        return {}
    # 根絶しない資産を覆う分は、最安の手で揃える
    need = set(sc.world.ground_truth.compromised) - {
        t for a in erad for t in a.eradicates
    }
    base = tuple(cheapest_cover(sc, need))
    order = [a.id for a in sc.actions if a.type == ActionType.CONTAIN]
    sweep = containment_sweep(sc)
    out: dict[str, dict[str, int]] = {}
    for a in erad:
        combo = tuple(sorted(set(base) | {a.id}, key=order.index))
        if combo in sweep:
            out[a.id] = sweep[combo]
    return out


# ─────────── 判定 ───────────


@dataclass
class Check:
    ok: bool
    name: str
    detail: str


def checks(sc: Scenario, runs: dict[tuple[str, bool], Run]) -> list[Check]:
    out: list[Check] = []
    judged, _ids, _exact = retrospective.minimal_path(sc)
    stopped, _stop_ids = retrospective.minimal_containment(sc)
    # 折れ点が基準にする「巧いプレイの長さ」L は、**被害が止まるまで**の長さ。
    # minimal_path は判断に到達するまでで、封じ込めを含まない。
    # 被害モデルが罰したいのは判断の遅れではなく、止まるまでの遅れである
    best = (judged + stopped) if (judged is not None and stopped is not None) else judged
    accel = sc.damage.acceleration

    # SPEC 8.2 手順7: 折れ点は巧いプレイの 1.1〜1.3 倍
    if accel is not None and best:
        ratio = accel.threshold_minutes / best
        out.append(Check(
            1.1 <= ratio <= 1.3,
            "折れ点の位置",
            f"判断まで {judged}分＋最安の封じ込め {stopped}分 = L {best}分"
            f" / 折れ点 {accel.threshold_minutes}分 = {ratio:.2f}倍（狙い 1.1〜1.3）",
        ))
        sk = runs[("skilled", False)]
        out.append(Check(
            sk.minutes <= accel.threshold_minutes,
            "巧いプレイは折れ点を踏まない",
            f"{sk.minutes}分（判断まで {sk.decided_at}分＋封じ込め {sk.minutes - sk.decided_at}分）"
            f" vs 折れ点 {accel.threshold_minutes}分",
        ))
        ex = runs[("exhaustive", False)]
        out.append(Check(
            ex.minutes > accel.threshold_minutes,
            "全部押すと折れ点を踏む",
            f"{ex.minutes}分 vs 折れ点 {accel.threshold_minutes}分",
        ))

    # SPEC 8.2 手順7 / 8.3: 巧いプレイの累積被害が worst の2〜3割。
    # 1割を切ると被害の差が潰れ、5割を超えると何をしても赤くなる。
    # **8.3 に項目があるのに実装されていなかった。** 復旧地平を入れる前は
    # 1216/14000 = 8.7% で、8.3 自身が「潰れる」と名指しした側に居た
    bounds = sc.scoring.consequence_layer.normalization.get("total_damage")
    if bounds is not None and bounds.worst:
        sk = runs[("skilled", False)]
        share = sk.damage / bounds.worst
        out.append(Check(
            0.2 <= share <= 0.3,
            "巧いプレイの被害が正規化の帯に入る",
            f"{sk.damage:.0f} / worst {bounds.worst:.0f} = {share:.1%}（狙い 2〜3割）",
        ))

    # SPEC 8.3: 帰結指標の正規化の帯は、盤面で到達できる範囲と揃っていること。
    # evidence_preserved は帯が [0,1] で、実際に失いうるのは destroys に
    # 書かれた分だけだった（3/19）。どのプレイも 0.84〜1.00 の中に居るので、
    # 帯の 84% は誰も踏まない。**「証拠保全最優先」を選んで証拠を3つ落としても
    # 2点しか動かない**という状態になっていた。名前にしている量が
    # 採点に現れないなら、その方針は選べても意味を持たない
    ev_bounds = sc.scoring.consequence_layer.normalization.get("evidence_preserved")
    losable = {e for a in sc.actions for e in a.destroys}
    losable |= {e for t in sc.timeline for e in t.destroys}
    if ev_bounds is not None and losable and sc.evidence:
        floor = 1.0 - len(losable) / len(sc.evidence)
        out.append(Check(
            abs(ev_bounds.worst - floor) <= 0.01,
            "証拠保全の正規化の帯が、失いうる範囲と揃っている",
            f"失いうる証拠 {len(losable)}/{len(sc.evidence)}件"
            f" → 到達しうる最悪 {floor:.3f} / 帯の worst {ev_bounds.worst:.3f}",
        ))

    # SPEC 8.3: 網羅と巧いプレイの差が10点以上
    pol = sc.meta.default_policy
    gap = (runs[("skilled", False)].by_policy[pol]["composite"]
           - runs[("exhaustive", False)].by_policy[pol]["composite"])
    out.append(Check(gap >= 10, "網羅は巧いプレイに負ける", f"差 {gap}点（狙い 10点以上、{pol}）"))

    # SPEC 6.6: 網羅すれば事実は掴める（そこは罰しない）。
    # **押し順を1本しか見ていなかった。** YAML の記載順は作者の都合であって、
    # 学習者の押し順ではない。実測すると、記載順が通っていたのは
    # ev_009 の取得（255分）と現場再起動（270分）の 15分の余裕のおかげで、
    # `cost_minutes` を1つ触れば裏返る状態だった。
    #
    # 検査を2本に割る。**時計が奪う分と排他が奪う分は設計上の意味が違う** —
    # 前者は連絡の手で防げるべきもの、後者は SPEC 3.8 が意図した
    # 「順序そのものが判断」である。混ぜて1本で測ると、
    # どちらが原因で落ちたのか分からないまま「網羅を罰している」と読む
    notices = [a.id for a in sc.actions if a.type == ActionType.COMMUNICATE]
    orders = press_orders(sc)
    order_runs = {
        (name, notice): run_order(sc, ids, notices if notice else [])
        for name, ids in orders.items()
        for notice in (False, True)
    }

    taken = {
        name: _lost_critical(sc, r)
        for (name, notice), r in order_runs.items() if notice
    }
    # 排他を踏んだ順序は対象外。自分の手で消したものは、
    # 連絡で防ぐものではなく順序で避けるものである
    by_clock = {
        name: lost for name, lost in taken.items()
        if not any(v == "排他" for v in lost.values())
    }
    hit = {n: v for n, v in by_clock.items() if v}
    out.append(Check(
        not hit,
        "時計が奪うものは、盤面の手で取り戻せる",
        f"連絡ありで全部押す {len(by_clock)}通りの順序（排他を踏んだ "
        f"{len(taken) - len(by_clock)}通りを除く）。失った critical: "
        + (str(hit) if hit else "なし"),
    ))

    if notices:
        # 逆側。連絡を出さなければ、少なくとも1つの押し順が時計に奪われること。
        # ここが全通りで「奪われない」なら、その連絡は何も守っていない
        without = {
            name: _lost_critical(sc, r)
            for (name, notice), r in order_runs.items() if not notice
        }
        bitten = {
            n: v for n, v in without.items()
            if any(x == "時計" for x in v.values())
        }
        out.append(Check(
            bool(bitten),
            "連絡には歯がある",
            f"連絡なしで全部押すと時計に奪われる順序: {bitten or 'なし'}"
            f"（{len(without)}通り中 {len(bitten)}通り）",
        ))

    # 事実認識層は、**答えではなく根拠の厚み**を測っているか。
    # 判定を揃えて調査量だけを変える対照。ここが平らなら、
    # 事実認識層の重みは「compromised と書けたか」の1ビットに落ちている
    ladder = evidence_ladder(sc)
    facts = [f for _lab, _m, f in ladder]
    rising = all(b > a for a, b in zip(facts, facts[1:]))
    out.append(Check(
        rising and (facts[-1] - facts[0]) >= 30,
        "同じ判定でも、根拠の厚みで事実認識層が動く",
        " → ".join(f"{lab} {m}分 {f:.1f}" for lab, m, f in ladder)
        + f"（単調増加・両端の差 {facts[-1] - facts[0]:.1f}点、狙い 30点以上）",
    ))

    # SPEC 5.10: 出来事はすべて盤面を変える
    toothless = [e.id for e in sc.timeline if not e.destroys and not e.heralds]
    out.append(Check(not toothless, "盤面を変えない出来事が無い", f"{toothless or 'なし'}"))

    # SPEC 5.10: 巧いプレイは世界に奪われない。
    # 見るのは**世界の側に奪われた分**だけ。自分の手で消したもの
    # （fs01 を止めれば稼働中の痕跡は消える）は順序の判断であって、
    # 出来事の置き場所の話ではない。ここを混ぜると、封じ込めた人ほど
    # 「世界に奪われた」ことになり、8.2 手順7 が測りたいものが消える
    if sc.timeline:
        skilled = runs[("skilled", False)]
        out.append(Check(
            not skilled.lost_to_world,
            "巧いプレイは世界に奪われない",
            f"奪われた: {skilled.lost_to_world or 'なし'}"
            f"（自分の手で消した分: "
            f"{[x for x in skilled.lost if x not in skilled.lost_to_world] or 'なし'}）",
        ))

    # SPEC 5.6.1: 連絡は、遅いプレイで実際に得になる
    if notices:
        w0, w1 = runs[("wanderer", False)], runs[("wanderer", True)]
        gains = {p.id: w1.by_policy[p.id]["composite"] - w0.by_policy[p.id]["composite"]
                 for p in sc.policies}
        out.append(Check(
            max(gains.values()) >= 3,
            "連絡が遅いプレイで得になる",
            f"誤導を追うプレイでの増減: {gains}（どれか +3 以上）",
        ))
        s0, s1 = runs[("skilled", False)], runs[("skilled", True)]
        sg = {p.id: s1.by_policy[p.id]["composite"] - s0.by_policy[p.id]["composite"]
              for p in sc.policies}
        out.append(Check(
            max(sg.values()) <= 0,
            "連絡が速いプレイでは払い損",
            f"巧いプレイでの増減: {sg}（どれも 0 以下）",
        ))
        viol = any(
            any(c.action_ids and set(c.action_ids) & set(notices) for c in p.constraints)
            for p in sc.policies
        )
        out.append(Check(viol, "連絡が少なくとも1つの方針で違反になる",
                         "時間だけを費用にすると、どの方針でも割に合わない"))

    # 誤導は損か。**調査量を揃えた対照**と比べる。
    # 誤導を追う像と棄却する像は、300分まで1手も違わない。
    # 違うのは宣言と、その宣言に従って何を止めたかだけ。
    # ここが縮むなら「誤導に乗った」ことが採点に現れていない
    if ("wanderer_clear", False) in runs:
        bad, ok = runs[("wanderer", False)], runs[("wanderer_clear", False)]
        diffs = {p.id: ok.by_policy[p.id]["composite"] - bad.by_policy[p.id]["composite"]
                 for p in sc.policies}
        out.append(Check(
            min(diffs.values()) >= 5,
            "誤導に乗った判定は損になる",
            f"棄却できた場合との差: {diffs}"
            f"（調査は両方 {ok.decided_at}分まで同一。どの方針でも 5点以上）",
        ))
        out.append(Check(
            set(bad.assessment) > set(ok.assessment),
            "誤導は実際に無関係な資産を名指しさせる",
            f"誤導を追う: {bad.assessment} / 棄却できた: {ok.assessment}",
        ))

    # 調べないプレイが、調べたプレイに勝っていないか。
    # 被害は経過時間の積分なので、**動かなければ被害は増えない**。
    # 調べる意味は、この対戦が全敗になることでしか保証されない。
    #
    # **以前は「調べない像」を1本（0手・fs01 だけ名指し）しか置いておらず、
    # その1本を全ての他像と比べていた。** それは調べない側の最弱の形である。
    # 同じ 0手でも「疑わしきは全部隔離」は再現率が満点になり、被害も止まる。
    # 弱い敵を選んでいる限り、この検査は通ったまま何も保証しない
    blind = [p.key for p in PROFILES if p.skips_investigation]
    seeing = [p.key for p in PROFILES if not p.skips_investigation]
    if blind and seeing:
        beaten = [
            f"{b}≧{g}/{pol.id}"
            for b in blind
            for g in seeing
            for pol in sc.policies
            if runs[(b, False)].by_policy[pol.id]["composite"]
            >= runs[(g, False)].by_policy[pol.id]["composite"]
        ]
        worst_blind = max(
            (runs[(b, False)].by_policy[pol.id]["composite"], f"{b}/{pol.id}")
            for b in blind for pol in sc.policies
        )
        best_gap = min(
            (runs[(g, False)].by_policy[pol.id]["composite"], f"{g}/{pol.id}")
            for g in seeing for pol in sc.policies
        )
        out.append(Check(
            not beaten,
            "調べない像は、調べた像のどれにも勝たない",
            f"調べない側の最高 {worst_blind[1]} {worst_blind[0]}点"
            f" / 調べた側の最低 {best_gap[1]} {best_gap[0]}点"
            f"。勝ってしまった組: {beaten or 'なし'}",
        ))

    # SPEC 3.8 / 8.2 手順7: フェーズ総コストは、そこで使える時間の2〜3倍。
    # 1倍を切るとそのフェーズは全部やれてしまい、取捨選択が発生しない。
    # 「使える時間」はシナリオが明示している唯一の予算＝折れ点までの時間。
    # 見るのは下限だけにする — 上振れ（メニューが厚い）は 8.1 の規模の目安が
    # 受け持っており、取捨選択という性質を壊すのは下振れの側だけである。
    #
    # **最終フェーズは対象から外す。** 対応フェーズは定義上 L のあとに
    # 始まるので、折れ点までの予算は調査フェーズが使い切っている。
    # 同じ時間を二重に数えていて、何をしても1倍を切る。2倍に届かせるには
    # 封じ込め1手あたり60分が必要で、被害モデルが罰したいことと正反対になる。
    # 最終フェーズの取捨選択は、下の3本（不可逆性で測る）が受け持つ
    if accel is not None and len(sc.phases) > 1:
        budget = accel.threshold_minutes
        ratios = {}
        for ph in sc.phases[:-1]:
            cost = sum(a.cost_minutes for a in sc.actions if a.phase == ph.id)
            ratios[ph.label] = (cost, cost / budget)
        out.append(Check(
            all(r >= 2.0 for _c, r in ratios.values()),
            "最終より前のフェーズは全部やれない",
            "".join(f"{lab} {c}分={r:.2f}倍 " for lab, (c, r) in ratios.items())
            + f"/ 使える時間 {budget}分（狙い 2〜3倍）"
            + f"。最終フェーズ「{sc.phases[-1].label}」は時間比では測らない",
        ))

    # SPEC 3.8: 対応フェーズの排他は**不可逆性**で閉じる。
    # 止めたものは復旧地平の間ずっと止まり、消した揮発性証拠は戻らない。
    # 同じ調査のあとに封じ込めの組だけを入れ替えて、帰結の差を見る
    sweep = containment_sweep(sc)
    all_contain = tuple(a.id for a in sc.actions if a.type == ActionType.CONTAIN)
    covering = {c: v for c, v in sweep.items() if _covers_compromised(sc, c)}
    if covering and all_contain in sweep:
        best = {
            pol.id: max(covering.items(), key=lambda kv: kv[1][pol.id])
            for pol in sc.policies
        }
        gaps = {pid: b[1][pid] - sweep[all_contain][pid] for pid, b in best.items()}
        out.append(Check(
            max(gaps.values()) >= 5,
            "封じ込めに取捨選択がある",
            f"最良の組と「全部押す」の差: {gaps}（どれか 5点以上）",
        ))

        shapes = {pid: b[0] for pid, b in best.items()}
        out.append(Check(
            len(set(shapes.values())) >= 2,
            "封じ込めの最良手が方針ごとに違う",
            " / ".join(
                f"{pid}: {'＋'.join(ids) or '（何も止めない）'}"
                for pid, ids in shapes.items()
            ),
        ))

        nothing = sweep[()]
        losses = {pid: b[1][pid] - nothing[pid] for pid, b in best.items()}
        out.append(Check(
            min(losses.values()) >= 10,
            "何も止めないプレイは正しく止めたプレイに負ける",
            f"最良の組との差: {losses}（どの方針でも 10点以上）",
        ))

    # SPEC 5.8 / 9.4 #5: persistence 条項が恒真になっていないか。
    # 「隔離しても、端末を戻せば攻撃者も戻ってくる」は key_lessons が
    # 教えている中核だが、v1.36 までこの条項は 128通り全数で
    # 一度も結果を変えていなかった（根絶の手が盤面に無かったため）
    differing = persistence_matters(sc)
    contain_n = sum(1 for a in sc.actions if a.type == ActionType.CONTAIN)
    out.append(Check(
        bool(differing),
        "persistence 条項が結果を変える封じ込めの組がある",
        f"{len(differing)} / {2 ** contain_n}通り"
        + (f"（例: {'＋'.join(differing[0]) or '（何も止めない）'}）" if differing else
           "。persistence ⊆ compromised なら恒真になる — 根絶の手が盤面に無い印"),
    ))

    # 同じ調査・同じ判定で、根絶まで行ったかどうかだけを変えた対照。
    # ここが縮むなら on_partial は名前だけの係数で、
    # 「止めた」と「取り除いた」が盤面で同じことになっている
    if ("skilled_halfway", False) in runs:
        full, half = runs[("skilled", False)], runs[("skilled_halfway", False)]
        ratio = half.damage / full.damage if full.damage else 0.0
        out.append(Check(
            ratio >= 1.5,
            "根絶しない封じ込めは被害を止めきらない",
            f"止めるだけ {half.damage:.0f}（{half.minutes}分） vs "
            f"根絶まで {full.damage:.0f}（{full.minutes}分） = {ratio:.2f}倍"
            "（狙い 1.5倍以上。調査は両方同一）",
        ))

    # 根絶そのものは物理的に必要だが、**どの根絶手を取るか**は
    # 方針で入れ替わること。押せば必ず得になる手は判断ではなく作業である
    choice = eradication_choice(sc)
    if len(choice) >= 2:
        ids = list(choice)
        winners = {
            pol.id: max(ids, key=lambda a: choice[a][pol.id])
            for pol in sc.policies
        }
        out.append(Check(
            len(set(winners.values())) >= 2,
            "どの根絶手を取るかが方針で入れ替わる",
            " / ".join(
                f"{a.replace('act_', '')}: "
                + "・".join(f"{p.id[:4]} {choice[a][p.id]}" for p in sc.policies)
                for a in ids
            ),
        ))

    # 方針を差し替えると評価が変わる（原則3）
    ex = runs[("exhaustive", False)]
    spread = max(v["composite"] for v in ex.by_policy.values()) - \
             min(v["composite"] for v in ex.by_policy.values())
    out.append(Check(spread >= 5, "同じ行動列が方針で違う評価になる", f"開き {spread}点"))

    return out


# ─────────── 表示 ───────────


def report(sc: Scenario) -> dict:
    notices = [a.id for a in sc.actions if a.type == ActionType.COMMUNICATE]
    runs: dict[tuple[str, bool], Run] = {}
    for prof in PROFILES:
        runs[(prof.key, False)] = run_profile(sc, prof, [])
        if notices:
            runs[(prof.key, True)] = run_profile(sc, prof, notices)

    result = checks(sc, runs)
    return {
        "scenario": sc.meta.id,
        "runs": {f"{k[0]}{'+連絡' if k[1] else ''}": vars(v) for k, v in runs.items()},
        "checks": [vars(c) for c in result],
        "passed": all(c.ok for c in result),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", nargs="?", default=DEFAULT_SCENARIO)
    ap.add_argument("--json", action="store_true", help="機械可読で出す")
    args = ap.parse_args()

    sc = loader.load_scenario(args.scenario)
    data = report(sc)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data["passed"] else 1

    print(f"■ {sc.meta.title}（{sc.meta.id}）")
    accel = sc.damage.acceleration
    best, _ids, _ = retrospective.minimal_path(sc)
    print(f"  最短経路 {best}分 / 折れ点 {accel.threshold_minutes if accel else '—'}分"
          f" / 出来事 {len(sc.timeline)}件 / アクション {len(sc.actions)}個")

    print("\n■ プレイ像ごとの実測")
    pols = [p.id for p in sc.policies]
    head = f"  {'':<16}{'経過':>7}{'被害':>9}{'失った':>8}{'未解消':>8}  "
    print(head + "".join(f"{p:>22}" for p in pols))
    for prof in PROFILES:
        for notice in (False, True):
            key = (prof.key, notice)
            if key not in data["runs"] and notice:
                continue
            r = report_run(data, prof, notice)
            if r is None:
                continue
            name = prof.label + ("＋連絡" if notice else "")
            row = (f"  {name:<16}{r['minutes']:>6}分{r['damage']:>9.0f}"
                   f"{len(r['lost']):>7}件{len(r['unresolved']):>7}件  ")
            row += "".join(f"{r['by_policy'][p]['composite']:>16}"
                           f"{'(違反)' if r['by_policy'][p]['violations'] else '      '}"
                           for p in pols)
            print(row)

    # 何を名指しし、何を止めたか。ここが全プレイで同じだった時期があり、
    # 事実認識層の重み 0.60 分と封じ込めの効果が丸ごと定数になっていた
    print("\n■ 何を名指しし、何を止めたか（連絡なし）")
    for prof in PROFILES:
        r = report_run(data, prof, False)
        if r is None:
            continue
        print(f"  {prof.label:<16}判定 {'・'.join(r['assessment']) or '（なし）':<28}"
              f"停止 {'・'.join(r['contained']) or '（なし）':<24}"
              f"根絶 {'・'.join(r['eradicated']) or '（なし）'}")

    print("\n■ 設計の判定（SPEC 8.3）")
    for c in data["checks"]:
        print(f"  {'✓' if c['ok'] else '✗'} {c['name']:<34} {c['detail']}")

    bad = [c for c in data["checks"] if not c["ok"]]
    print(f"\n  {len(data['checks']) - len(bad)} / {len(data['checks'])} 通過")
    return 0 if not bad else 1


def report_run(data: dict, prof: Profile, notice: bool) -> dict | None:
    return data["runs"].get(f"{prof.key}{'+連絡' if notice else ''}")


if __name__ == "__main__":
    sys.exit(main())
