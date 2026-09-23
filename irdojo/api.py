"""FastAPI の薄いラッパー（SPEC 7.6.3）。

エンジンは既に PlayerView を返すので、そのまま JSON にして渡すだけ。
セッションはメモリ上に保持し、永続化しない。
"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import records as records_mod
from . import swap as swap_data
from .engine import AssessmentRequired, Decision, Engine, InvalidDecision
from .loader import ScenarioError, list_scenarios, load_scenario
from .report import json as report_json
from .schema import AssistLevel

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SPEC_FILE = Path(__file__).resolve().parent.parent / "SPEC.md"

app = FastAPI(title="IR Dojo", docs_url=None, redoc_url=None, openapi_url=None)

# セッションはメモリ上のみ（SPEC 7.6.3）
_SESSIONS: dict[str, Engine] = {}
MAX_SESSIONS = 32

# ── 実時間の足あと（SPEC 7.5.4）
#
# **エンジンは実時間を知らない。** 知る必要が無く、知ると `balance.py` の
# 何千回の模擬プレイまで時計を持つことになる。実時間を測れるのは、
# 人が本当に押している API の層だけである。
#
# ここに溜まるのは学習者が自分で押したものだけで、`ground_truth` は入らない。
# 採点にも講評にも使われず、**記録に書き残すためだけ**にある。
_MOVES: dict[str, list[records_mod.Move]] = {}
_OPENED: dict[str, float] = {}   # ブリーフィングを渡した時刻
_MARKS: dict[str, float] = {}    # 直前の手の時刻（初手はブリーフィングを閉じた時刻）
_BRIEFING: dict[str, float] = {}  # ブリーフィングを読んでいた秒数


def _forget(sid: str) -> None:
    """セッションと一緒に足あとも捨てる。**溜め続けない。**"""
    for store in (_MOVES, _OPENED, _MARKS, _BRIEFING):
        store.pop(sid, None)


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
    tutorial: bool
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
            tutorial=sc.meta.tutorial,
            estimated_play_minutes=sc.meta.estimated_play_minutes,
            tags=list(sc.meta.tags),
            policies=[PolicyBrief(id=p.id, label=p.label) for p in sc.policies],
            default_policy=sc.meta.default_policy,
            default_assist_level=sc.meta.default_assist_level,
        )
        for sc in list_scenarios()
    ]


def _spec_version() -> str | None:
    """SPEC.md の冒頭から版を読む。**読めなければ None。**

    入口の隅に出す計器風の帯（7.6.18）に載る。版を画面やコードに
    手で書くと、次に SPEC を上げた日から入口が古い版を名乗る —
    `policy_swap.json` の指紋と同じ話で、**腐るくらいなら出さない**。

    SPEC.md は配布物の一部ではあるが、無い状態で動かされることはある
    （パッケージだけ取り出した場合など）。そのときは黙って版を落とす。
    """
    try:
        with SPEC_FILE.open(encoding="utf-8") as fh:
            for _ in range(20):  # 版は冒頭にある。無ければ諦める
                line = fh.readline()
                if not line:
                    break
                m = re.fullmatch(r"\*\*仕様書 (v\d+\.\d+)\*\*", line.strip())
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


@app.get("/api/meta")
def get_meta() -> dict[str, Any]:
    """入口の帯に出す、この配布物そのものの素性（SPEC 7.6.18）。

    **ここに入れてよいのは「実際に取れるもの」だけ。** 演習の数と方針の
    数は画面が `/api/scenarios` から数える（並べた札と食い違いようが
    ない場所で数えるため、ここでは返さない）。返すのはファイルを読まないと
    分からない2つだけで、どちらも取れなければ `null` = 帯に出ない。

    `build` は入口の表を作った生成物の指紋である。生成物が古ければ
    `swap.load()` が None を返すので、**表が消えるときは指紋も消える** —
    帯が、画面に無い表の素性を名乗ることはない。
    """
    data = swap_data.load()
    return {
        "spec_version": _spec_version(),
        "build": data.fingerprint[:8] if data is not None else None,
    }


@app.get("/api/policy-swap")
def get_policy_swap() -> dict[str, Any]:
    """入口の 3×3（SPEC 7.6.5）。**どの盤面のものかは返さない。**

    入口はシナリオを選ぶ前の画面なので、表が特定の演習と結び付く必要が
    ない。配線に scenario_id を載せないのが一番簡単な閉じ方である。

    数字は `tools/balance.py --emit-swap` の生成物から読む。指紋が
    合わなければ `available: false` を返す — **古い数字を出すくらいなら
    表ごと出さない**（`swap.py` の docstring）。
    """
    data = swap_data.load()
    if data is None or not data.scenarios:
        return {"available": False}

    # 並べるのは一覧の先頭のもの1つ。規則を固定しないと、
    # 「一番よく見える盤面を選ぶ」余地が残る
    chosen = data.scenarios[0]
    labels = {p.id: p.label for sc in list_scenarios() if sc.meta.id == chosen.scenario_id
              for p in sc.policies}
    if not set(chosen.policy_ids) <= set(labels):
        # 方針 id が動いた = 表の列が何を指しているか言えない
        return {"available": False}

    return {
        "available": True,
        "policies": [{"id": pid, "label": labels[pid]} for pid in chosen.policy_ids],
        "rows": [
            {"label": r.label, "scores": {k: r.scores[k] for k in chosen.policy_ids}}
            for r in chosen.rows
        ],
    }


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
        _forget(next(iter(_SESSIONS)))
        _SESSIONS.pop(next(iter(_SESSIONS)))

    sid = uuid.uuid4().hex
    _SESSIONS[sid] = engine
    now = time.monotonic()
    _MOVES[sid] = []
    _OPENED[sid] = now
    _MARKS[sid] = now
    return {
        "session_id": sid,
        "title": sc.meta.title,
        "briefing": sc.meta.briefing,
        "policy_label": engine.policy.label,
        "policy_briefing": engine.policy.briefing,
        # 案内（SPEC 3.13）。**書いてあるのは手順と理由で、答えではない。**
        # 練習の盤面にしか付かない
        "tutorial": [
            {
                "id": s.id,
                "body": s.body,
                "expect_action": s.expect_action,
                "expect_kind": s.expect_kind,
                "caveat": s.caveat,
                "points_at": s.points_at,
            }
            for s in sc.tutorial
        ],
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


def _log_move(
    sid: str,
    engine: Engine,
    body: Decision,
    phase: str,
    minute: int,
    *,
    blocked: bool = False,
) -> None:
    """1手ぶんの足あとを残す（SPEC 7.5.4）。

    **失敗しても遊びを止めない。** 足あとは採点にも講評にも効かないので、
    ここで例外を上げて手が通らなくなるほうが害が大きい。
    """
    log = _MOVES.get(sid)
    if log is None:
        return
    now = time.monotonic()
    log.append(
        records_mod.Move(
            n=len(log) + 1,
            kind=(body.kind + "_blocked") if blocked else body.kind,
            action_id=body.action_id if body.kind == "action" else None,
            phase=phase,
            at_minute=minute,
            cost_minutes=engine.state.elapsed_minutes - minute,
            think_seconds=round(now - _MARKS.get(sid, now), 1),
        )
    )
    _MARKS[sid] = now


@app.post("/api/session/{sid}/begin")
def begin(sid: str) -> dict[str, bool]:
    """ブリーフィングを閉じた、という合図だけを受ける（SPEC 7.5.4）。

    **盤面は1つも動かない。** 受け取るのは時刻だけで、
    ここから先の「手が止まった秒」が、ブリーフィングを読んでいた時間と
    混ざらなくなる。**読ませすぎていないかは、この差でしか分からない。**
    """
    _get(sid)  # 知らないセッションには答えない
    now = time.monotonic()
    if sid in _OPENED and sid not in _BRIEFING:
        _BRIEFING[sid] = round(now - _OPENED[sid], 1)
    _MARKS[sid] = now
    return {"ok": True}


@app.post("/api/session/{sid}/decide")
def decide(sid: str, body: Decision) -> dict[str, Any]:
    engine = _get(sid)
    acted = (
        engine.scenario.action_by_id.get(body.action_id or "")
        if body.kind == "action"
        else None
    )
    # **押す前**の盤面を控える。押した後では、その手が何分進めたか言えない
    before_phase = engine.state.current_phase
    before_minute = engine.state.elapsed_minutes
    try:
        outcome = engine.decide(body)
    except AssessmentRequired:
        # **断られた手も足あとに残す。** 「被疑判定を出す前に進もうとした」は
        # 盤面を1つも動かさないが、規則が伝わっていなかったという意味では
        # 通った手より強い合図である（SPEC 7.5.4）
        _log_move(sid, engine, body, before_phase, before_minute, blocked=True)
        return {
            "needs_assessment": True,
            "view": engine.view(),
            "revealed_evidence": [],
            "unlocked_count": 0,
            "contained": [],
            "eradicated": [],
            "halted": [],
            "restored": [],
            "already": [],
            "halts_business": False,
            "preserved": False,
            "business_impact_delta": 0.0,
            "events": [],
            "finished": False,
        }
    except InvalidDecision as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _log_move(sid, engine, body, before_phase, before_minute)

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
                "possibilities": list(by_id[eid].possibilities)
                if profile.show_evidence_possibilities
                else None,
                # 事件の時計。content の再掲なのでレベルで伏せない（7.6.17）
                "occurred_at": by_id[eid].occurred_at,
                "at_minute": engine.state.obtained_at.get(eid, 0),
            }
            for eid in outcome.revealed
        ],
        # この発見で新たに調べられるようになったものの件数。
        # 何が開いたかは一覧を見れば分かるので、件数だけ返す
        "unlocked_count": len(outcome.unlocked),
        # 封じ込めで止まった資産（直接指定した分）。
        "contained": [
            {"id": a, "label": engine.scenario.asset_by_id[a].label}
            for a in outcome.contained
            if a in engine.scenario.asset_by_id
        ],
        # 永続化を取り除いた資産。止めたことと別の箱で返す（SPEC 5.8 / 7.6.8）。
        # 「通信を止めた」と「元の状態に戻した」を同じ言い方にすると、
        # 3つ目の束を押した意味が押した直後に伝わらない。
        # **何が残っていたかは言わない** — それは ground_truth の側であり、
        # プレイ中に言えば損失の予告になる（原則5）
        "eradicated": [
            {"id": a, "label": engine.scenario.asset_by_id[a].label}
            for a in outcome.eradicated
            if a in engine.scenario.asset_by_id
        ],
        # 業務が止まった資産。依存で波及した分も含めて告げる。
        # 構成は既に開示しているので、波及を伝えても漏洩にはならない（7.6.7）。
        # 止めることと業務が止まることは別なので、別の箱で返す（6.3）
        "halted": [
            {
                "id": a,
                "label": engine.scenario.asset_by_id[a].label,
                "cascaded": a in outcome.cascaded,
            }
            for a in outcome.halted
            if a in engine.scenario.asset_by_id
        ],
        # 業務に戻した資産（SPEC 5.11）。**戻したことだけを返す。**
        # 戻した先で攻撃が再開したかどうかは、ここでは返さない — 講評で初めて出る
        "restored": [
            {"id": a, "label": engine.scenario.asset_by_id[a].label}
            for a in outcome.restored
            if a in engine.scenario.asset_by_id
        ],
        # 押す前から止まっていた targets。空振りではないので別に返す
        "already": [
            {"id": a, "label": engine.scenario.asset_by_id[a].label}
            for a in outcome.already
            if a in engine.scenario.asset_by_id
        ],
        # その手が業務を止める種類のものか。description が既に言っている
        # 機構であって、答えではない（「端末は動いたまま、通信だけを止める」）
        "halts_business": bool(acted and acted.side_effects.business_impact),
        # 保全の手だったか（SPEC 5.6.3）。**件数も中身も返さない。**
        # 仕掛けは証拠を1件も産まないので、これが無いと画面は
        # 「何も出てこなかった」と出す。件数を返すと 0 と 1 の差が
        # 「間に合ったか」の答えになり、プレイ中に賭けの結果を告げる（原則5）
        "preserved": bool(acted and acted.secures),
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


@app.delete("/api/session/{sid}")
def abort_session(sid: str) -> dict[str, bool]:
    """途中でやめる（SPEC 7.6.25）。**記録は残らない。**

    記録は講評を組むときにしか作られない（`report_json.build`）ので、
    **何もしないことが「残さない」になる。** ここで捨てるのは
    サーバ側のセッションと足あとだけである。

    知らない id でも 200 を返す。やめたい人を、後片付けの都合で
    引き止めない。
    """
    _SESSIONS.pop(sid, None)
    _REPORTS.pop(sid, None)
    _forget(sid)
    return {"ok": True}


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
        _REPORTS[sid] = report_json.build(
            engine.state,
            engine.scenario,
            moves=_MOVES.get(sid),
            briefing_seconds=_BRIEFING.get(sid),
        )
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


# **画面はキャッシュさせない。** 直したのに古いまま、が実際に起きた。
# 127.0.0.1 でしか動かない道具なので、転送量より目の前の画面が本物である
# ことのほうが大事である（下の `_NoStore` の docstring に理由を書いた）。
NO_STORE = {"Cache-Control": "no-store, must-revalidate"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers=NO_STORE)


class _NoStore(StaticFiles):
    """静的ファイルも毎回取りに来させる。

    **画面を直したのに古いままだ、が実際に起きた。** `FileResponse` と
    `StaticFiles` は `ETag` と `Last-Modified` を付けるので、ブラウザは
    再読み込みすれば新しいものを取る。**再読み込みしないかぎり取らない。**
    開いたままのタブは、前の版を表示し続ける。

    この道具は 127.0.0.1 でしか動かず（7.6.1）、開発と学習が同じ画面で
    行われる。**転送量より、目の前の画面が本物であることのほうが大事である。**
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.update(NO_STORE)
        return response


if WEB_DIR.is_dir():
    app.mount("/static", _NoStore(directory=WEB_DIR), name="static")
