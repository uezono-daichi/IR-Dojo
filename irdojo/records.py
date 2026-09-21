"""プレイ記録の保存と読み出し（SPEC 7.5）。

同一シナリオ内でのみ比較する。シナリオをまたいだ比較はしない
（誤導の巧妙さも論点の数も違い、比較可能性を担保できない）。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from .schema import AssistLevel

RECORD_SCHEMA_VERSION = "1.0"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.json$")


def home() -> Path:
    return Path(os.environ.get("IRDOJO_HOME") or (Path.home() / ".irdojo"))


def results_dir() -> Path:
    return home() / "results"


class Move(BaseModel):
    """1手ぶんの足あと（SPEC 7.5.4）。

    **盤面の時刻と、実際にかかった秒の両方を残す。** 前者は採点に効く
    （どれだけ「演習内の時間」を使ったか）。後者は採点に一切効かないが、
    **どこで手が止まったかはここにしか出ない。**

    テストプレイで知りたいのは点数ではなく「どこで詰まったか」で、
    それは結果だけを保存していた頃の記録には残っていなかった。

    **`ground_truth` は入らない。** 入るのは学習者が自分で押したものと、
    その時点で自分が見ていた盤面の時刻だけである（7.7.5）。
    """

    n: int                      # 何手目か（1 から）
    kind: str                   # action | advance_phase | declare_assessment | finish
    action_id: str | None = None   # kind == "action" のときだけ
    phase: str                  # その手を押した時点のフェーズ
    at_minute: int              # 押す前の盤面時刻
    cost_minutes: int           # その手で進んだ盤面時刻
    think_seconds: float        # 前の手からの**実時間**。採点には使わない


class Record(BaseModel):
    schema_version: str = RECORD_SCHEMA_VERSION
    scenario_id: str
    scenario_title: str
    scenario_version: str  # シナリオ更新後の記録と区別するため
    policy_id: str
    policy_label: str
    assist_level: AssistLevel
    played_at: datetime
    is_first_play: bool  # このシナリオの初回プレイか（SPEC 3.11）

    fact_score: float
    policy_score: float
    composite_score: float
    metrics: dict[str, float]

    elapsed_minutes: int
    total_damage: float
    business_impact: float
    evidence_preserved: float
    containment_completeness: float

    assessment: list[str]
    unresolved_at_decision: list[str]
    constraint_violations: int

    # ── ここから下は採点に一切効かない。**どこで詰まったかを見るためだけ**にある
    # （SPEC 7.5.4）。古い記録には無いので、既定値を持たせて読めるようにしておく
    moves: list[Move] = []
    # ブリーフィングを開いてから「対応を開始」を押すまでの実秒。
    # **読ませすぎていないかは、ここでしか分からない**
    #
    # **完走したかは記録に書かない。** 記録は `finish` を押した回にしか
    # 作られないので、書けば必ず True になる。常に True の欄は
    # 「全員が完走した」と読まれる — 途中でやめた回は**記録そのものが無い**
    # ことを、集計の側（`tools/playtest_report.py`）が言う
    briefing_seconds: float | None = None

    # ground_truth は含めない（SPEC 7.7.5）


def filename_for(rec: Record) -> str:
    stamp = rec.played_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    parts = [rec.scenario_id, rec.policy_id, rec.assist_level.value, stamp]
    return "__".join(_slug(p) for p in parts) + ".json"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "-", text)


def has_any(scenario_id: str) -> bool:
    return bool(load_all(scenario_id))


def save(rec: Record) -> Path:
    d = results_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = _free_path(d, filename_for(rec))
    payload = rec.model_dump(mode="json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _free_path(d: Path, name: str) -> Path:
    """名前が衝突したら連番を足す。

    ファイル名の時刻は秒までしか持たない。同じ秒に終わった2つのプレイを
    同じ名前で書くと、**先の記録が黙って消える。** 記録は上書きしていい
    ものではないので、名前のほうを譲る。
    """
    path = d / name
    if not path.exists():
        return path
    stem = name[: -len(".json")]
    for i in range(2, 1000):
        alt = d / f"{stem}-{i}.json"
        if not alt.exists():
            return alt
    raise RuntimeError(f"記録の保存先を確保できない: {name}")


def load_all(scenario_id: str) -> list[tuple[str, Record]]:
    """指定シナリオの記録のみを返す。シナリオ横断では返さない（SPEC 3.11）。"""
    d = results_dir()
    if not d.is_dir():
        return []
    out: list[tuple[str, Record]] = []
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rec = Record.model_validate(data)
        except Exception:
            continue  # 壊れた記録は黙って飛ばす
        if rec.scenario_id == scenario_id:
            out.append((path.name, rec))
    out.sort(key=lambda pair: pair[1].played_at)
    return out


def resolve_record(filename: str) -> Path:
    """削除用。パストラバーサルを防ぐ（SPEC 7.7.5）。"""
    if not _SAFE_NAME.match(filename):
        raise ValueError("不正なファイル名")
    base = results_dir().resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base):
        raise ValueError("不正なパス")
    return target


def delete(filename: str) -> bool:
    path = resolve_record(filename)
    if not path.is_file():
        return False
    path.unlink()
    return True
