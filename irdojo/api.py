"""FastAPI の薄いラッパー（SPEC 7.6.3）。

エンジンは既に PlayerView を返すので、そのまま JSON にして渡すだけ。
セッションはメモリ上に保持し、永続化しない。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import records as records_mod
from .engine import AssessmentRequired, Decision, Engine, InvalidDecision
from .loader import ScenarioError, list_scenarios, load_scenario
from .report import json as report_json
from .schema import AssistLevel

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="IR Dojo", docs_url=None, redoc_url=None, openapi_url=None)

# セッションはメモリ上のみ（SPEC 7.6.3）
_SESSIONS: dict[str, Engine] = {}
MAX_SESSIONS = 32


# ─────────── リクエスト / レスポンス ───────────


class NewSession(BaseModel):
    scenario_id: str
    policy_id: str | None = None
    assist_level: AssistLevel | None = None


class PolicyBrief(BaseModel):
    id: str
    label: str


class ScenarioBrief(BaseModel):
    """一覧用。`meta` と `policies` の表示情報のみ（SPEC 7.7.3）。

    ground_truth も evidence も actions も返さない。
    """

    id: str
    title: str
    complexity: int
    estimated_play_minutes: int
    tags: list[str]
    policies: list[PolicyBrief]
    default_policy: str
    default_assist_level: AssistLevel


# ─────────── エンドポイント ───────────


@app.get("/api/scenarios", response_model=list[ScenarioBrief])
def get_scenarios() -> list[ScenarioBrief]:
    return [
        ScenarioBrief(
            id=sc.meta.id,
            title=sc.meta.title,
            complexity=sc.complexity,
            estimated_play_minutes=sc.meta.estimated_play_minutes,
            tags=list(sc.meta.tags),
            policies=[PolicyBrief(id=p.id, label=p.label) for p in sc.policies],
            default_policy=sc.meta.default_policy,
            default_assist_level=sc.meta.default_assist_level,
        )
        for sc in list_scenarios()
    ]


@app.post("/api/session")
def create_session(body: NewSession) -> dict[str, Any]:
    try:
        sc = load_scenario(body.scenario_id)
    except ScenarioError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        engine = Engine(sc, body.policy_id, body.assist_level)
    except InvalidDecision as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(_SESSIONS) >= MAX_SESSIONS:
        # 単独プレイ前提。古いものから落として青天井の滞留を防ぐ
        _SESSIONS.pop(next(iter(_SESSIONS)))

    sid = uuid.uuid4().hex
    _SESSIONS[sid] = engine
    return {
        "session_id": sid,
        "title": sc.meta.title,
        "briefing": sc.meta.briefing,
        "policy_label": engine.policy.label,
        "policy_briefing": engine.policy.briefing,
        # 演習の段取り。答えではなく規則なので、始める前に開示する
        "phases": [
            {
                "label": p.label,
                "advance_label": p.advance_label,
                "requires_assessment": p.requires_assessment,
            }
            for p in sc.phases
        ],
        "view": engine.view(),
    }


@app.post("/api/session/{sid}/decide")
def decide(sid: str, body: Decision) -> dict[str, Any]:
    engine = _get(sid)
    acted = (
        engine.scenario.action_by_id.get(body.action_id or "")
        if body.kind == "action"
        else None
    )
    try:
        outcome = engine.decide(body)
    except AssessmentRequired:
        return {
            "needs_assessment": True,
            "view": engine.view(),
            "revealed_evidence": [],
            "unlocked_count": 0,
            "contained": [],
            "business_impact_delta": 0.0,
            "events": [],
            "finished": False,
        }
    except InvalidDecision as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    by_id = engine.scenario.evidence_by_id
    profile = engine.profile
    return {
        "needs_assessment": False,
        "view": engine.view(),
        # そのアクションで新たに開示された証拠のみ。フロントが強調表示する
        "revealed_evidence": [
            {
                "id": eid,
                "summary": by_id[eid].summary if profile.show_evidence_summary else None,
                "content": by_id[eid].content,
                "reading": (by_id[eid].reading or None)
                if profile.show_evidence_reading
                else None,
                "at_minute": engine.state.obtained_at.get(eid, 0),
            }
            for eid in outcome.revealed
        ],
        # この発見で新たに調べられるようになったものの件数。
        # 何が開いたかは一覧を見れば分かるので、件数だけ返す
        "unlocked_count": len(outcome.unlocked),
        # 封じ込めで止まった資産。依存で波及した分も含めて告げる。
        # 構成は既に開示しているので、波及を伝えても漏洩にはならない（7.6.7）
        "contained": [
            {
                "id": a,
                "label": engine.scenario.asset_by_id[a].label,
                "cascaded": a in outcome.cascaded,
            }
            for a in outcome.contained
            if a in engine.scenario.asset_by_id
        ],
        "business_impact_delta": outcome.business_impact_delta,
        # 実行の様子。何をしたのかを見せる（SPEC 7.6.8）
        # 向こうから入ってきたこと。証拠ではないので、別枠で返す
        "events": [
            {
                "id": e.id,
                "at_minutes": e.at_minutes,
                # 先手が効いた回は、代わりに聞こえることを返す。
                # 無言にすると、打った手が効いたことが学習者に伝わらない
                "label": (e.averted_label or e.label) if e.id in outcome.averted else e.label,
                "text": e.averted_text if e.id in outcome.averted else e.text,
                "averted": e.id in outcome.averted,
            }
            for e in engine.scenario.timeline
            if e.id in outcome.events
        ],
        # 連絡で以後起きなくしたものの件数。中身は言わない（何を防いだかは答えの側）
        "prevented_count": len(outcome.prevented),
        "command": acted.command if acted else "",
        "cost_minutes": acted.cost_minutes if acted else 0,
        "finished": engine.state.finished,
    }


@app.get("/api/session/{sid}/report")
def get_report(sid: str) -> report_json.Report:
    engine = _get(sid)
    if not engine.state.finished:
        raise HTTPException(status_code=409, detail="まだ終了していません")
    return _report_cache(sid, engine)


_REPORTS: dict[str, report_json.Report] = {}


def _report_cache(sid: str, engine: Engine) -> report_json.Report:
    """記録の保存は1セッションにつき1回だけ行う。"""
    if sid not in _REPORTS:
        _REPORTS[sid] = report_json.build(engine.state, engine.scenario)
    return _REPORTS[sid]


@app.get("/api/records")
def get_records(scenario_id: str) -> list[dict[str, Any]]:
    """指定シナリオの記録のみ。シナリオ横断は返さない（SPEC 3.11 / 7.5.3）。"""
    return [
        {"filename": name, **rec.model_dump(mode="json")}
        for name, rec in records_mod.load_all(scenario_id)
    ]


@app.delete("/api/records/{filename}")
def delete_record(filename: str) -> dict[str, bool]:
    try:
        ok = records_mod.delete(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="記録が見つかりません")
    return {"deleted": True}


def _get(sid: str) -> Engine:
    engine = _SESSIONS.get(sid)
    if engine is None:
        raise HTTPException(status_code=404, detail="セッションが見つかりません")
    return engine


# ─────────── 静的配信 ───────────
#
# `web/` のみ。`scenarios/` は決してマウントしない（SPEC 7.7.3）。


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
