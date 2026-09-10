"""測定器そのものを見張る（tools/balance.py）。

設計の判定は測定器の出力でしか下せないので、測定器が盤面の一部を
見ていないと、判定は「通っている」まま嘘をつく。実際に2つ穴があった:

  - `advance_phase` を通さないので対応フェーズが一度も実行されず、
    containment_completeness は全プレイ 0.00 だった
  - 被疑判定に `ground_truth.compromised` をそのまま宣言していたので、
    事実認識層の重み 0.60 分が全プレイ同値だった

どちらも「判定が全部通る」状態と同居していた。ここで見張るのは
**測定器が動かしている軸の本数**であって、個々の点数ではない。
"""

import importlib.util
import pathlib
import sys

import pytest

from irdojo import scoring
from irdojo.loader import load_scenario

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_balance():
    spec = importlib.util.spec_from_file_location("balance", ROOT / "tools" / "balance.py")
    mod = importlib.util.module_from_spec(spec)
    # dataclass の型解決に sys.modules を引くので、先に登録しておく
    sys.modules["balance"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def balance():
    return _load_balance()


@pytest.fixture(scope="module")
def plays(balance):
    sc = load_scenario("ransomware-initial-response-01")
    return sc, {
        prof.key: balance._play(
            sc, prof.order(sc), [],
            stop=prof.stop_when_confident,
            patience=prof.patience_minutes,
            weighs_refutations=prof.weighs_refutations,
            contains_everything=prof.contains_everything,
        )
        for prof in balance.PROFILES
    }


def test_every_play_reaches_the_response_phase(plays):
    """どのプレイ像も封じ込めまで通す。

    対応フェーズを実行しないと `contained_at` が空のままになり、
    被害の減衰係数は全プレイ on_incorrect 固定、
    business_impact は全プレイ 0（＝正規化後の「良さ」が満点）になる。
    方針の重みのうち常に定数の部分が、方針によっては 0.50 に達していた。
    """
    sc, runs = plays
    for key, e in runs.items():
        assert e.state.assessment_snapshot is not None, key
        assert e.state.current_phase == sc.phases[-1].id, key
        assert e.state.contained_at, f"{key}: 何も止めていない"


def test_containment_is_not_the_same_for_everyone(plays):
    """止めたものが像によって違う。

    全プレイが同じ資産を止めるなら、containment_completeness も
    business_impact も定数で、方針適合層はその分だけ死んでいる。
    """
    sc, runs = plays
    shapes = {key: frozenset(e.state.contained_at) for key, e in runs.items()}
    assert len(set(shapes.values())) >= 3, shapes

    completeness = {
        key: scoring.consequences(e.state, sc).containment_completeness
        for key, e in runs.items()
    }
    assert min(completeness.values()) < 1.0, completeness
    assert max(completeness.values()) == 1.0, completeness


def test_assessment_is_built_from_what_the_play_holds(plays):
    """被疑判定は手元の証拠から組み立てる。ground_truth を読まない。

    読んでしまうと precision 1.0 / recall 1.0 / persistence_missed 0.0 が
    全プレイ同値になり、**このシナリオの教育の中心**（誤導に乗って
    無関係な端末を名指しする）が判定の数字に一度も現れない。
    """
    sc, runs = plays
    truth = set(sc.world.ground_truth.compromised)
    declared = {key: set(e.state.assessment) for key, e in runs.items()}

    assert declared["skilled"] == truth, declared["skilled"]
    assert declared["wanderer"] > truth, "誤導を追う像が無関係な資産を名指ししていない"
    assert declared["hasty"] < truth, "調べていない像が正解を当てている"
    assert len({frozenset(v) for v in declared.values()}) >= 3, declared


def test_the_misled_control_differs_only_in_the_declaration(plays):
    """誤導の対照は、調査の手が1つも違わないこと。

    ここが揃っていないと「誤導は損か」の差が、調査量の差と混ざる。
    違ってよいのは、宣言と、その宣言に従って止めた資産だけ。
    """
    sc, runs = plays
    from irdojo.schema import ActionType

    def investigated(e):
        return [
            a for a in e.state.executed_actions
            if sc.action_by_id[a].type != ActionType.CONTAIN
        ]

    bad, ok = runs["wanderer"], runs["wanderer_clear"]
    assert investigated(bad) == investigated(ok)
    assert set(bad.state.assessment) != set(ok.state.assessment)
