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
