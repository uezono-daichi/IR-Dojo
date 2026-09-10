"""被害モデル（SPEC 5.8）。

被害率の関数は名前で登録する（SPEC 原則4 拡張点3）。
封じ込めの減衰判定は ground_truth のみに基づく。学習者の被疑判定は見ない。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping

from .schema import Asset, ContainmentEffect, DamageModel, GroundTruth

RateFn = Callable[[float, Mapping[str, float | list[float]]], float]

_MODELS: dict[str, RateFn] = {}


def register(name: str) -> Callable[[RateFn], RateFn]:
    def wrap(fn: RateFn) -> RateFn:
        _MODELS[name] = fn
        return fn

    return wrap


@register("exponential")
def _exponential(t: float, p: Mapping[str, float | list[float]]) -> float:
    base = float(p["base"])  # type: ignore[arg-type]
    rate = float(p["rate"])  # type: ignore[arg-type]
    return base * math.exp(rate * t / 60.0)


@register("linear")
def _linear(t: float, p: Mapping[str, float | list[float]]) -> float:
    base = float(p["base"])  # type: ignore[arg-type]
    rate = float(p["rate"])  # type: ignore[arg-type]
    return base * (1.0 + rate * t / 60.0)


@register("step")
def _step(t: float, p: Mapping[str, float | list[float]]) -> float:
    base = float(p["base"])  # type: ignore[arg-type]
    steps = list(p["steps"])  # type: ignore[arg-type]
    interval = float(p["interval"])  # type: ignore[arg-type]
    idx = min(int(t // interval), len(steps) - 1)
    return base * float(steps[idx])


def base_rate(model: DamageModel, minute: float) -> float:
    """acceleration 込みの被害率。封じ込めの効果はまだ乗じない。"""
    fn = _MODELS.get(model.model)
    if fn is None:
        raise ValueError(f"未登録の被害モデル: {model.model}")
    r = fn(minute, model.params)
    acc = model.acceleration
    if acc is not None and minute > acc.threshold_minutes:
        r *= acc.multiplier
    return r


def containment_factor(
    contained: Iterable[str],
    truth: GroundTruth,
    effect: ContainmentEffect,
) -> float:
    """封じ込めの減衰係数（SPEC 5.8）。

    判定は ground_truth のみに基づく。被害は物理的な現実であり、
    学習者の思い込みでは変わらない。
    """
    c = set(contained)
    compromised = set(truth.compromised)
    persistence = set(truth.persistence)

    if compromised <= c and persistence <= c:
        return effect.on_correct_containment
    if c & compromised:
        return effect.on_partial
    return effect.on_incorrect


def expand_containment(
    direct: Mapping[str, int],
    assets: Mapping[str, Asset],
) -> dict[str, int]:
    """依存連鎖を不動点まで展開し、各資産の entered_at を返す（SPEC 6.3）。

    連鎖で入った資産は、依存先が入った時刻のうち最も早いものを継承する。
    直接停止した資産でも、依存先がより早く止まっていればその時刻に更新する。
    循環参照はローダが拒否するため、この反復は必ず停止する。
    """
    result = dict(direct)
    changed = True
    while changed:
        changed = False
        for a in assets.values():
            upstream = [result[d] for d in a.depends_on if d in result]
            if not upstream:
                continue
            t = min(upstream)
            if a.id not in result or t < result[a.id]:
                result[a.id] = t
                changed = True
    return result


def project(
    model: DamageModel,
    truth: GroundTruth,
    contained: Iterable[str],
    end_minute: int,
    horizon: int,
) -> list[float]:
    """演習を終えた時点から復旧地平 H までの、1分刻みの被害量（SPEC 5.8）。

    **演習が終わってもインシデントは終わらない。** 手を止めた時点の
    封じ込め状態がそのまま続くとみなして、H 分だけ積分を延長する。

    これが無いと「15分で降りる」が最も安い選択肢になり、
    `containment_effect`（0.2 / 0.6 / 1.0）は演習終了までの
    わずかな残り時間にしか掛からない — 対応フェーズの中核パラメータが
    盤面上ほとんど効かなくなる。H は business_impact と共用する
    （「復旧まで」は同じ1つの前提であり、2つ置くと作者が別々に調整できてしまう）。
    """
    return accrue(model, truth, contained, end_minute, end_minute + horizon)


def accrue(
    model: DamageModel,
    truth: GroundTruth,
    contained: Iterable[str],
    start_minute: int,
    end_minute: int,
) -> list[float]:
    """[start, end) の各分の被害量を返す（1分刻みの離散和）。

    封じ込め状態は区間内で一定とみなす。エンジンはアクション単位で
    この関数を呼ぶため、区間内で状態が変わることはない。
    """
    factor = containment_factor(contained, truth, model.containment_effect)
    out: list[float] = []
    for m in range(start_minute, end_minute):
        # 分あたりに直すため 60 で割る（base は「/時間」で与えられる）
        out.append(base_rate(model, m) / 60.0 * factor)
    return out
