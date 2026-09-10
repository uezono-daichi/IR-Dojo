"""シナリオ YAML の読み込みと検証（SPEC 7.7.1）。

シナリオは信頼できない入力である。`yaml.safe_load` 以外を使わず、
Pydantic の検証を通過したものだけをエンジンに渡す。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import SCHEMA_VERSION, Scenario

# 走査対象はリポジトリ同梱の固定ディレクトリのみ
SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"

MAX_BYTES = 2 * 1024 * 1024  # 1シナリオの上限。読み込み時の DoS を避ける


class ScenarioError(Exception):
    """シナリオの読み込み・検証に失敗した。"""


def load_scenario_text(text: str, *, origin: str = "<string>") -> Scenario:
    """YAML 文字列からシナリオを組み立てる。

    `yaml.safe_load` 固定。`yaml.load` / `unsafe_load` は任意コード実行になる。
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{origin}: YAML として解釈できません: {exc}") from exc

    if not isinstance(data, dict):
        raise ScenarioError(f"{origin}: トップレベルがマッピングではありません")

    # complexity はローダが算出する。書かれていたら警告して捨てる（SPEC 3.12）
    meta = data.get("meta")
    if isinstance(meta, dict) and "complexity" in meta:
        meta.pop("complexity")
        _warn(f"{origin}: meta.complexity は無視されます（ローダが算出します）")

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        _warn(
            f"{origin}: schema_version が {version!r} です"
            f"（このエンジンは {SCHEMA_VERSION!r} 用）"
        )

    try:
        return Scenario.model_validate(data)
    except ValidationError as exc:
        raise ScenarioError(f"{origin}: 検証に失敗しました\n{_format(exc)}") from exc


def load_scenario_file(path: Path) -> Scenario:
    """ファイルから読む。パスは `scenarios/` 配下に限定する。"""
    resolved = _safe_path(path)
    if resolved.stat().st_size > MAX_BYTES:
        raise ScenarioError(f"{resolved.name}: シナリオが大きすぎます（上限 {MAX_BYTES} バイト）")
    text = resolved.read_text(encoding="utf-8")
    sc = load_scenario_text(text, origin=resolved.name)

    stem = resolved.stem
    if sc.meta.id != stem:
        _warn(f"{resolved.name}: meta.id ({sc.meta.id}) がファイル名と一致しません")
    return sc


def load_scenario(scenario_id: str) -> Scenario:
    """id からシナリオを読む。id はパス片として扱わない。"""
    if not _is_safe_id(scenario_id):
        raise ScenarioError(f"不正なシナリオ id: {scenario_id!r}")
    path = SCENARIO_DIR / f"{scenario_id}.yaml"
    if not path.exists():
        raise ScenarioError(f"シナリオが見つかりません: {scenario_id}")
    return load_scenario_file(path)


def list_scenarios() -> list[Scenario]:
    """`scenarios/` 配下を走査する。壊れたシナリオは飛ばして続行する。"""
    out: list[Scenario] = []
    if not SCENARIO_DIR.is_dir():
        return out
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        try:
            out.append(load_scenario_file(path))
        except ScenarioError as exc:
            _warn(f"読み込みを飛ばしました: {exc}")
    return out


# ─────────── パスの安全確認（SPEC 7.7.1） ───────────


def _is_safe_id(scenario_id: str) -> bool:
    if not scenario_id or len(scenario_id) > 128:
        return False
    if scenario_id in (".", ".."):
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    return set(scenario_id) <= allowed and "/" not in scenario_id


def _safe_path(path: Path) -> Path:
    """`..` とシンボリックリンクを拒否し、`scenarios/` 配下に閉じ込める。"""
    base = SCENARIO_DIR.resolve()
    candidate = Path(path)
    if candidate.is_symlink():
        raise ScenarioError(f"シンボリックリンクは読み込めません: {candidate}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise ScenarioError(f"scenarios/ の外を指しています: {candidate}")
    if not resolved.is_file():
        raise ScenarioError(f"ファイルではありません: {candidate}")
    return resolved


def _format(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def _warn(message: str) -> None:
    import warnings

    warnings.warn(message, stacklevel=3)
