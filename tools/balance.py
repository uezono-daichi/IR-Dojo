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


PROFILES = [
    Profile("skilled", "巧い", "最短経路。答えを知っている者の上限", _skilled),
    Profile("exhaustive", "全部押す", "取捨選択をしない", _exhaustive,
            stop_when_confident=False, contains_everything=True),
    Profile("wanderer", "誤導を追う", "棄却の材料を持ったまま名指しする", _wanderer,
            patience_minutes=300, weighs_refutations=False),
    # wanderer と**調査量が1分も違わない**対照。違うのは宣言の作り方だけ。
    # これが無いと「誤導は損か」を調査量の差と切り分けられない
    Profile("wanderer_clear", "誤導を棄却する", "同じ調査量で、棄却を判定に反映した場合",
            _wanderer, patience_minutes=300),
    Profile("hasty", "調べずに止める", "ブリーフィングだけで名指しして封じ込める",
            _nothing),
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
    unresolved: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    averted: list[str] = field(default_factory=list)
    by_policy: dict[str, dict] = field(default_factory=dict)


def _play(
    sc: Scenario, order: list[str], notices: list[str],
    *, stop: bool, patience: int | None,
    weighs_refutations: bool, contains_everything: bool,
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
    assessment = _assess(sc, e.state, weighs_refutations=weighs_refutations)
    e.decide(Decision(kind="declare_assessment", assessment=assessment))

    # 宣言を要求するフェーズへはエンジンがそのまま進める（engine._declare）。
    # ここを呼ばずに終わっていたので、対応フェーズのアクションは
    # 一度も実行されず、containment_completeness は全プレイ 0.00、
    # 被害の減衰係数は全プレイ on_incorrect 固定だった
    for aid in _containment(sc, assessment, everything=contains_everything):
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


def _containment(sc: Scenario, assessed: list[str], *, everything: bool) -> list[str]:
    """封じ込めで実際に押す手。

    既定は「自分が名指しした資産に届く手を全部押す」。C2 の遮断も端末の隔離も
    どちらもやるのは実務として普通で、片方だけ選ぶ理由は学習者の側に無い。
    `everything` は取捨選択をしない像 — 調査で全部押した人は対応でも全部押す。
    """
    acts = [a for a in sc.actions if a.type == ActionType.CONTAIN]
    if everything:
        return [a.id for a in acts]
    judged = set(assessed)
    # targets が判定の外へはみ出す手は押さない。
    # 「dc01 を止めれば全部止まる」は、dc01 を疑っていない人の手ではない
    return [a.id for a in acts if a.targets and set(a.targets) <= judged]


def run_profile(sc: Scenario, prof: Profile, notices: list[str]) -> Run:
    e = _play(sc, prof.order(sc), notices,
              stop=prof.stop_when_confident, patience=prof.patience_minutes,
              weighs_refutations=prof.weighs_refutations,
              contains_everything=prof.contains_everything)
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


# ─────────── 判定 ───────────


@dataclass
class Check:
    ok: bool
    name: str
    detail: str


def checks(sc: Scenario, runs: dict[tuple[str, bool], Run]) -> list[Check]:
    out: list[Check] = []
    best, _ids, _exact = retrospective.minimal_path(sc)
    accel = sc.damage.acceleration

    # SPEC 8.2 手順7: 折れ点は巧いプレイの 1.1〜1.3 倍
    if accel is not None and best:
        ratio = accel.threshold_minutes / best
        out.append(Check(
            1.1 <= ratio <= 1.3,
            "折れ点の位置",
            f"最短 {best}分 / 折れ点 {accel.threshold_minutes}分 = {ratio:.2f}倍（狙い 1.1〜1.3）",
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

    # SPEC 8.3: 網羅と巧いプレイの差が10点以上
    pol = sc.meta.default_policy
    gap = (runs[("skilled", False)].by_policy[pol]["composite"]
           - runs[("exhaustive", False)].by_policy[pol]["composite"])
    out.append(Check(gap >= 10, "網羅は巧いプレイに負ける", f"差 {gap}点（狙い 10点以上、{pol}）"))

    # SPEC 6.6: 網羅すれば事実は掴める（そこは罰しない）
    ex_unres = [q for q in runs[("exhaustive", False)].unresolved
                if sc.question_by_id[q].critical]
    out.append(Check(
        not ex_unres,
        "網羅した学習者から critical 論点を奪っていない",
        f"未解消 critical: {ex_unres or 'なし'}",
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
    notices = [a.id for a in sc.actions if a.type == ActionType.COMMUNICATE]
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

    # 何もしないプレイが、調べたプレイに勝っていないか。
    # 被害は経過時間の積分なので、**動かなければ被害は増えない**。
    # 調べる意味は、この像がどの方針でも最下位になることでしか保証されない
    if ("hasty", False) in runs:
        h = runs[("hasty", False)]
        others = [(k, r) for k, r in runs.items() if k[0] != "hasty" and not k[1]]
        beaten = [
            f"{k[0]}/{p.id}"
            for k, r in others
            for p in sc.policies
            if h.by_policy[p.id]["composite"] >= r.by_policy[p.id]["composite"]
        ]
        out.append(Check(
            not beaten,
            "調べずに止めるプレイはどの方針でも最下位",
            f"{h.minutes}分・{h.by_policy[sc.meta.default_policy]['composite']}点"
            f"（{sc.meta.default_policy}）。勝ってしまった相手: {beaten or 'なし'}",
        ))

    # SPEC 3.8 / 8.2 手順7: フェーズ総コストは、そこで使える時間の2〜3倍。
    # 1倍を切るとそのフェーズは全部やれてしまい、取捨選択が発生しない。
    # 「使える時間」はシナリオが明示している唯一の予算＝折れ点までの時間。
    # 見るのは下限だけにする — 上振れ（メニューが厚い）は 8.1 の規模の目安が
    # 受け持っており、取捨選択という性質を壊すのは下振れの側だけである
    if accel is not None:
        budget = accel.threshold_minutes
        ratios = {}
        for ph in sc.phases:
            cost = sum(a.cost_minutes for a in sc.actions if a.phase == ph.id)
            ratios[ph.label] = (cost, cost / budget)
        out.append(Check(
            all(r >= 2.0 for _c, r in ratios.values()),
            "どのフェーズも全部はやれない",
            "".join(f"{lab} {c}分={r:.2f}倍 " for lab, (c, r) in ratios.items())
            + f"/ 使える時間 {budget}分（狙い 2〜3倍）",
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
              f"停止 {'・'.join(r['contained']) or '（なし）'}")

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
