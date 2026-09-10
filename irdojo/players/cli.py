"""開発・テスト用の最小 CLI（SPEC 1.5 / 9.5）。

**人に見せる想定はしない。** この演習の価値（被害が膨らむ緊張感、
未解消論点を抱えた決断）は Web UI でしか伝わらない。
ここはエンジンを手早く叩くための足場である。
"""

from __future__ import annotations

import argparse
import sys

from ..engine import AssessmentRequired, Decision, Engine, InvalidDecision, PlayerView
from ..loader import ScenarioError, list_scenarios, load_scenario
from ..report import json as report_json
from ..schema import AssistLevel

RULE = "─" * 68


def render(view: PlayerView) -> None:
    print()
    print(f"━━━ {view.current_phase_label} " + "━" * 40)
    print(
        f"  経過 {view.elapsed_minutes // 60:02d}:{view.elapsed_minutes % 60:02d}"
        f"    累積被害 {view.accumulated_damage:,.0f}"
        f"    業務影響 {view.accumulated_business_impact:,.0f}"
    )

    if view.obtained_evidence:
        print("\n  【取得済みの証拠】")
        for ev in view.obtained_evidence:
            head = ev.summary or "(要約なし)"
            print(f"    [{ev.id}] {head}")

    if view.open_questions is not None:
        print("\n  【未解消の論点】")
        for q in view.open_questions:
            mark = "●" if q.resolved is not True else "○"
            state = "" if q.resolved is None else ("  解消済み" if q.resolved else "")
            print(f"    {mark} {q.label}  {q.question}{state}")

    print("\n  【実行可能なアクション】")
    for i, a in enumerate(view.available_actions, 1):
        if not a.selectable:
            print(f"    [--] {a.label}  （実行済み）")
        else:
            print(f"    [{i:2d}] {a.label}  {a.cost_minutes}分")

    if view.advance_label:
        print(f"\n  [A] {view.advance_label}", end="")
    print("    [E] 証拠の全文    [Q] 終了")


def show_evidence(view: PlayerView) -> None:
    for ev in view.obtained_evidence:
        print()
        print(RULE)
        print(f"[{ev.id}] {ev.summary or ''}")
        print(RULE)
        print(ev.content)


def ask_assessment(view: PlayerView) -> list[str]:
    print()
    print("  現時点で「侵害されている」と判断する資産を選んでください。")
    for i, a in enumerate(view.assets, 1):
        mark = "x" if a.id in view.current_assessment else " "
        print(f"    [{i}] [{mark}] {a.id}  {a.label}")
    if view.unresolved_count:
        print(f"\n  ⚠ 未解消の論点が {view.unresolved_count} 件あります")
    raw = input("  番号をカンマ区切りで（空欄で確定なし）> ").strip()
    if not raw:
        return []
    picked: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(view.assets):
            picked.append(view.assets[int(token) - 1].id)
    return picked


def play(engine: Engine) -> None:
    sc = engine.scenario
    print(RULE)
    print(f"  {sc.meta.title}")
    print(RULE)
    print(sc.meta.briefing)
    print(RULE)
    print(f"  【今回の対応方針】{engine.policy.label}\n")
    print(engine.policy.briefing)
    print(RULE)

    while not engine.state.finished:
        view = engine.view()
        render(view)
        try:
            raw = input("\n> ").strip().lower()
        except EOFError:
            raw = "q"

        try:
            if raw == "e":
                show_evidence(view)
            elif raw == "a":
                try:
                    engine.decide(Decision(kind="advance_phase"))
                except AssessmentRequired:
                    picked = ask_assessment(view)
                    engine.decide(
                        Decision(kind="declare_assessment", assessment=picked)
                    )
            elif raw == "q":
                try:
                    engine.decide(Decision(kind="finish"))
                except AssessmentRequired:
                    picked = ask_assessment(view)
                    engine.decide(
                        Decision(kind="declare_assessment", assessment=picked)
                    )
                    engine.decide(Decision(kind="finish"))
            elif raw.isdigit():
                idx = int(raw) - 1
                if not (0 <= idx < len(view.available_actions)):
                    print("  そのような選択肢はありません")
                    continue
                target = view.available_actions[idx]
                outcome = engine.decide(
                    Decision(kind="action", action_id=target.id)
                )
                print(f"\n  ── {target.label}（{target.cost_minutes}分経過） ──")
                if outcome.revealed:
                    for eid in outcome.revealed:
                        ev = sc.evidence_by_id[eid]
                        print()
                        print(f"  [{eid}] {ev.summary}")
                        print()
                        for line in ev.content.rstrip().splitlines():
                            print(f"      {line}")
                else:
                    print("  この調査からは何も出てこなかった。")
            else:
                print("  入力を認識できません")
        except (InvalidDecision, AssessmentRequired) as exc:
            print(f"  {exc}")

    show_report(engine)


def show_report(engine: Engine) -> None:
    rep = report_json.build(engine.state, engine.scenario)
    s = rep.score
    print()
    print(RULE)
    print("  講評")
    print(RULE)
    print(f"  総合スコア  {round(s.composite_score * 100)} / 100")
    print(
        f"    事実認識 {round(s.fact_score * 100)}"
        f"     方針適合 {round(s.policy_score * 100)}"
    )
    print()
    for mid, value in s.metrics.items():
        print(f"    {rep.metric_labels.get(mid, mid):<28} {value:.3f}")

    print()
    print("  【判定時点で未解消だった論点】")
    for u in rep.retrospective.unresolved_review:
        if u.resolved_at_decision:
            print(f"    ○ {u.label} — 解消済み")
        else:
            tag = "" if u.critical else "（採点対象外）"
            print(f"    ● {u.label} — {u.question}{tag}")
            for line in u.implication.rstrip().splitlines():
                print(f"        {line}")

    if rep.retrospective.counterfactuals:
        print()
        print("  【誤導の棄却経路】")
        for cf in rep.retrospective.counterfactuals:
            print(f"    ⚠ {cf.evidence_id}（{cf.evidence_summary}）を根拠に")
            print(f"      {cf.asset_id} を被疑と判定しました。")
            for line in cf.explanation.rstrip().splitlines():
                print(f"      {line}")
            if cf.refuting_evidence:
                print(f"      → {', '.join(cf.refuting_evidence)} を取得していれば棄却できました。")
            if cf.refuting_obtained:
                print(
                    f"      → {', '.join(cf.refuting_obtained)} は取得済みでした。"
                    "  棄却の材料は手元にありました。"
                )

    if rep.violations:
        print()
        print(f"  【方針への適合】方針違反 {len(rep.violations)}件")
        for v in rep.violations:
            print(f"    「{v.message}」")
            print(f"      → {v.action_label}（{v.at_minute}分時点）")

    if rep.truth:
        print()
        print("  【真相】")
        for line in rep.truth.attack_narrative.rstrip().splitlines():
            print(f"    {line}")
        print(f"    実際に侵害されていた資産: {', '.join(rep.truth.compromised)}")
        print(f"    無関係だった資産:         {', '.join(rep.truth.innocent)}")

    mp = rep.retrospective.minimal_path
    if mp and mp.minutes is not None:
        print()
        print(f"  【参照値】{mp.label}: {mp.minutes}分"
              f"   /   あなたの経過時間: {s.consequences.elapsed_minutes}分")
        for line in mp.disclaimer.rstrip().splitlines():
            print(f"    {line}")

    if len(rep.records) > 1:
        print()
        print("  【このシナリオの記録】")
        for row in rep.records:
            star = " ★初回" if row.is_first_play else ""
            print(
                f"    {row.policy_label:<16} {row.assist_level:<9}"
                f" 事実 {row.fact_score:>3} 方針 {row.policy_score:>3}"
                f" 総合 {row.composite_score:>3}{star}"
            )
        print("    ※ ★ 以外は答えを知った状態でのプレイです。")
    print(RULE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="irdojo-cli", description="IR Dojo CLI（開発用）")
    parser.add_argument("scenario", nargs="?", help="シナリオ id")
    parser.add_argument("--policy", help="方針 id")
    parser.add_argument("--assist", choices=[a.value for a in AssistLevel])
    parser.add_argument("--list", action="store_true", help="シナリオ一覧")
    args = parser.parse_args(argv)

    if args.list or not args.scenario:
        for sc in list_scenarios():
            print(f"{sc.meta.id}  複雑度{sc.complexity}  {sc.meta.title}")
            for p in sc.policies:
                print(f"    方針: {p.id}  {p.label}")
        return 0

    try:
        sc = load_scenario(args.scenario)
    except ScenarioError as exc:
        print(exc, file=sys.stderr)
        return 1

    engine = Engine(
        sc,
        args.policy,
        AssistLevel(args.assist) if args.assist else None,
    )
    play(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
