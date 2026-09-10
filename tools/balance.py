"""シナリオの均衡を実測する（SPEC 8.2 手順7 / 8.3 のチェックリスト）。

設計の良し悪しは、読んで決めるものではなく走らせて決めるものである。
「網羅は損になっているか」「折れ点は誰かに踏まれるか」「この手は
一度でも押す価値があるか」は、いずれも数字にできる。

    python tools/balance.py [シナリオID] [--json]

数字にできないもの（分かりやすさ、面白さ）は tools/playtest.mjs の側で見る。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from irdojo import loader, retrospective, scoring
from irdojo.engine import Decision, Engine, InvalidDecision
from irdojo.schema import ActionType, Scenario

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


PROFILES = [
    Profile("skilled", "巧い", "最短経路。答えを知っている者の上限", _skilled),
    Profile("exhaustive", "全部押す", "取捨選択をしない", _exhaustive,
            stop_when_confident=False),
    Profile("wanderer", "誤導を追う", "揮発性を後回しにする初学者", _wanderer,
            patience_minutes=300),
]


# ─────────── 実行 ───────────


@dataclass
class Run:
    profile: str
    with_notice: bool
    minutes: int = 0
    damage: float = 0.0
    lost: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    averted: list[str] = field(default_factory=list)
    by_policy: dict[str, dict] = field(default_factory=dict)


def _play(
    sc: Scenario, order: list[str], notices: list[str],
    *, stop: bool, patience: int | None,
) -> Engine:
    """order を実行可能になった順に消化する。詰まったら諦める。"""
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

    truth = sc.world.ground_truth
    e.decide(Decision(kind="declare_assessment", assessment=list(truth.compromised)))
    e.decide(Decision(kind="finish"))
    return e


def run_profile(sc: Scenario, prof: Profile, notices: list[str]) -> Run:
    e = _play(sc, prof.order(sc), notices,
              stop=prof.stop_when_confident, patience=prof.patience_minutes)
    st = e.state
    r = Run(
        profile=prof.key,
        with_notice=bool(notices),
        minutes=st.elapsed_minutes,
        lost=list(st.lost_evidence),
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
        skilled = runs[("skilled", False)]
        out.append(Check(
            skilled.minutes <= accel.threshold_minutes,
            "巧いプレイは折れ点を踏まない",
            f"{skilled.minutes}分 vs 折れ点 {accel.threshold_minutes}分",
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

    # SPEC 5.10: 巧いプレイは奪われない
    if sc.timeline:
        out.append(Check(
            not runs[("skilled", False)].lost,
            "巧いプレイは世界に奪われない",
            f"失った: {runs[('skilled', False)].lost or 'なし'}",
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
