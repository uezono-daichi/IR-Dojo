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
            contains_root=prof.contains_root,
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


# ─────────── 対応フェーズ（サイクル3で足した軸） ───────────


def test_some_play_stops_the_root_of_the_dependency_graph(plays):
    """依存の根を止める手を、誰かが押していること。

    像が「自分が名指しした資産に届く手」しか押さないと、盤面で最も
    高くつく資産（依存の根）は一度も止まらない。すると復旧地平も
    業務影響も、その資産については常に 0 で測られる。

    構成図は開始0分から画面に出ていて、根が dc01 であることは見えている。
    「根を落とせば全部止まる」は、それを見た人が普通に思いつく手である。
    """
    sc, runs = plays
    roots = {
        a.id for a in sc.world.assets
        if not a.depends_on and any(a.id in b.depends_on for b in sc.world.assets)
    }
    assert roots, "依存の根が無い（この検査が意味を持たない盤面）"
    stopped_root = [k for k, e in runs.items() if roots & set(e.state.contained_at)]
    assert stopped_root, f"どの像も依存の根 {roots} を止めていない"
    # 根を止めた像では、業務が資産の数だけ止まっていること。
    # ここが効いていないと、封じ込めの費用の上限が測れない
    for key in stopped_root:
        st = runs[key].state
        assert len(st.halted_at) == len(sc.world.assets), key


def test_no_play_presses_two_actions_at_the_same_asset(plays):
    """既定の像は、同じ資産に二重に手を打たない。

    fs01 を停止した**うえで**その fs01 への SMB 遮断の変更承認を
    待つ人はいない。押せる手を全部押す像は `exhaustive` が受け持つ。
    ここが緩むと、対応フェーズの取捨選択が「全部やる」に潰れる。
    """
    sc, runs = plays
    from irdojo.schema import ActionType

    for key, e in runs.items():
        if key == "exhaustive":
            continue        # 取捨選択をしない像。ここだけは二重に打つ
        seen: dict[str, str] = {}
        for aid in e.state.executed_actions:
            act = sc.action_by_id[aid]
            if act.type != ActionType.CONTAIN:
                continue
            for t in act.targets:
                assert t not in seen, f"{key}: {t} に {seen.get(t)} と {aid} の二重"
                seen[t] = aid


def _named(data, name):
    return next(c for c in data["checks"] if c["name"] == name)


def test_the_response_phase_is_not_measured_by_time(balance):
    """対応フェーズを時間比では測らない（SPEC 3.8）。

    フェーズ総コストの 2〜3倍検査は、対応フェーズには効かない —
    対応は定義上 L のあとに始まるので、折れ点までの予算は調査が
    使い切っている。同じ150分を二重に数えていて、何をしても1倍を切る。
    2倍に届かせるには封じ込め1手あたり60分が必要で、
    それは被害モデルが罰したいことと正反対の設計になる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    data = balance.report(sc)
    check = _named(data, "最終より前のフェーズは全部やれない")
    assert check["ok"]
    assert sc.phases[-1].label in check["detail"]
    # 最終フェーズの取捨選択は、不可逆性で測る3本が受け持つ
    for name in (
        "封じ込めに取捨選択がある",
        "封じ込めの最良手が方針ごとに違う",
        "何も止めないプレイは正しく止めたプレイに負ける",
    ):
        assert _named(data, name)["ok"], name


def test_the_replacement_checks_are_not_slack(balance, monkeypatch):
    """置き換えた検査が「通るように緩めた検査」になっていないこと。

    測定側を緩めて数字を動かしたのなら、盤面を元に戻しても通るはずである。
    2本について、それぞれが見張っている変更を外すと実際に落ちることを示す。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )

    # (1) fs01 の手を1つに戻すと、最良手は全方針で一致する
    data = copy.deepcopy(raw)
    data["actions"] = [a for a in data["actions"] if a["id"] != "act_block_smb_fs01"]
    for pol in data["policies"]:
        for c in pol.get("constraints", []):
            c["action_ids"] = [
                i for i in c.get("action_ids", []) if i != "act_block_smb_fs01"
            ]
    one_way = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    assert not _named(
        balance.report(one_way), "封じ込めの最良手が方針ごとに違う"
    )["ok"], "選択肢が1つでも「方針ごとに違う」が通ってしまう"

    # (2) 復旧地平を外すと、「何も止めない」が勝つ
    from irdojo import scoring

    monkeypatch.setattr(
        scoring.damage_mod, "project", lambda *a, **k: [], raising=True
    )
    sc = load_scenario("ransomware-initial-response-01")
    assert not _named(
        balance.report(sc), "何も止めないプレイは正しく止めたプレイに負ける"
    )["ok"], "地平が無くても「何も止めない」が負けている"
