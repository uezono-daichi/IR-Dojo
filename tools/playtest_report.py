#!/usr/bin/env python3
"""テストプレイの記録をまとめて読む（SPEC 7.5.5）。

**点数を並べる道具ではない。** 知りたいのは「どこで詰まったか」で、
それは平均点には出ない。

使い方:

    python tools/playtest_report.py                 # ~/.irdojo/results を読む
    python tools/playtest_report.py 回収したフォルダ    # 複数人ぶんをまとめて
    python tools/playtest_report.py --json          # 機械で読む

**この道具が答えられないことが1つある。** 記録は `finish` を押した回にしか
作られないので、**途中でやめた人はここに1件も出ない。**
完走率は記録からは出せない。配った人数は手で数えて、下の「回数」と比べること。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from irdojo.loader import load_scenario  # noqa: E402
from irdojo.records import Record  # noqa: E402

# 手が止まったとみなす秒数。**同席して数えた「10秒」に合わせてある** —
# 画面の前で10秒黙っていたら、それは読み直しているか、迷っている
STUCK_SECONDS = 10.0


def read(paths: list[Path]) -> list[tuple[Path, Record]]:
    out: list[tuple[Path, Record]] = []
    for base in paths:
        files = sorted(base.glob("**/*.json")) if base.is_dir() else [base]
        for path in files:
            try:
                out.append((path, Record.model_validate_json(path.read_text("utf-8"))))
            except Exception:
                continue  # 壊れた記録は黙って飛ばす（records.load_all と同じ扱い）
    out.sort(key=lambda pair: pair[1].played_at)
    return out


def labels_for(scenario_id: str) -> dict[str, str]:
    """アクション id → 表示名。**記録には id しか書いていない。**

    名前を記録に書き写すと、シナリオを直した日に記録だけが古い名前を持つ。
    引くのは読むときでよい。
    """
    try:
        sc = load_scenario(scenario_id)
    except Exception:
        return {}
    return {a.id: a.label for a in sc.actions}


def summarize(recs: list[tuple[Path, Record]]) -> dict:
    by_scenario: dict[str, list[Record]] = defaultdict(list)
    for _path, rec in recs:
        by_scenario[rec.scenario_id].append(rec)

    out = {"total_plays": len(recs), "scenarios": []}
    for sid, group in sorted(by_scenario.items()):
        names = labels_for(sid)
        moves = [m for r in group for m in r.moves]
        stuck: Counter[str] = Counter()
        stuck_secs: dict[str, list[float]] = defaultdict(list)
        for rec in group:
            for m in rec.moves:
                if m.think_seconds >= STUCK_SECONDS:
                    key = m.action_id or m.kind
                    stuck[key] += 1
                    stuck_secs[key].append(m.think_seconds)

        briefs = [r.briefing_seconds for r in group if r.briefing_seconds is not None]
        acted = [r for r in group if r.moves]
        blocked = sum(
            1 for m in moves if m.kind.endswith("_blocked")
        )
        # 「全部押す」に逃げたか。**手の数はシナリオごとに違う**ので割合で見る
        total_actions = len(names) or 1
        swept = [
            r for r in group
            if len({m.action_id for m in r.moves if m.action_id}) / total_actions >= 0.8
        ]
        out["scenarios"].append({
            "scenario_id": sid,
            "title": group[0].scenario_title,
            "plays": len(group),
            "players_policies": sorted({r.policy_id for r in group}),
            "replayed_with_other_policy": len({r.policy_id for r in group}) > 1,
            "briefing_seconds": {
                "median": round(statistics.median(briefs), 1) if briefs else None,
                "max": round(max(briefs), 1) if briefs else None,
                "missing": len(group) - len(briefs),
            },
            "moves_median": round(statistics.median([len(r.moves) for r in acted]), 1)
            if acted else None,
            "elapsed_minutes_median": round(
                statistics.median([r.elapsed_minutes for r in group]), 1),
            # **画面と同じ 0〜100 で出す。** 記録は 0〜1 で持っているが、
            # 道具ごとに桁が違うと、人が突き合わせるときに必ず間違える
            "composite_median": round(
                statistics.median([r.composite_score for r in group]) * 100, 1),
            "violations_total": sum(r.constraint_violations for r in group),
            "blocked_moves": blocked,
            "swept_everything": len(swept),
            "stuck": [
                {
                    "at": key,
                    "label": names.get(key, key),
                    "times": n,
                    "median_seconds": round(statistics.median(stuck_secs[key]), 1),
                }
                for key, n in stuck.most_common(8)
            ],
            "no_moves_recorded": len(group) - len(acted),
        })
    return out


def render(data: dict) -> None:
    print(f"■ 記録 {data['total_plays']} 件")
    print("  **途中でやめた回はここに出ない。** 記録は最後まで行った回にだけ作られる。")
    print("  配った人数と比べて、足りない分が離脱である。\n")
    for s in data["scenarios"]:
        print(f"■ {s['title']}（{s['scenario_id']}） — {s['plays']} 回")
        b = s["briefing_seconds"]
        if b["median"] is not None:
            print(f"  ブリーフィング   中央 {b['median']}秒 / 最長 {b['max']}秒"
                  + (f"（{b['missing']}件は未計測）" if b["missing"] else ""))
        if s["moves_median"] is None:
            print(f"  手数             —（{s['no_moves_recorded']}件は足あとを持たない"
                  "古い記録。SPEC 7.5.4 より前のもの）")
        else:
            print(f"  手数             中央 {s['moves_median']}")
        print(f"  盤面の経過       中央 {s['elapsed_minutes_median']}分")
        print(f"  総合点           中央 {s['composite_median']}")
        print(f"  方針違反         のべ {s['violations_total']}件")
        if s["blocked_moves"]:
            print(f"  断られた手       {s['blocked_moves']}件"
                  "  ← 規則が伝わっていない合図")
        if s["swept_everything"]:
            print(f"  ほぼ全部押した   {s['swept_everything']} / {s['plays']} 人"
                  "  ← 網羅が損になっていない合図")
        if s["replayed_with_other_policy"]:
            print(f"  方針を変えて再挑戦 あり（{', '.join(s['players_policies'])}）")
        if s["stuck"]:
            print(f"\n  手が {STUCK_SECONDS:.0f} 秒以上止まった場所")
            for row in s["stuck"]:
                print(f"    {row['times']}回  中央 {row['median_seconds']:>5.1f}秒  "
                      f"{row['label']}")
        print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="テストプレイの記録をまとめて読む")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="記録のフォルダかファイル（既定: ~/.irdojo/results）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from irdojo.records import results_dir

    paths = args.paths or [results_dir()]
    recs = read(paths)
    if not recs:
        print(f"記録が見つかりません: {', '.join(str(p) for p in paths)}", file=sys.stderr)
        return 1
    data = summarize(recs)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        render(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
