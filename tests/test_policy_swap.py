"""入口に置いた 3×3 が、**いま実際にそう採点される**ことを見張る（SPEC 7.6.5）。

守っている性質は1つ。**入口は古い数字を出さない。**

トップ画面は「正解は一つではありません」を文章で2回言っていた。それを
`tools/balance.py` の入れ替え表に置き換えたので、盤面や採点を触った日から
入口が嘘をつく道が新しく1本できた。閉じ方は2段ある。

  ① 焼き直しを忘れたら、入口から表が**消える**（指紋が合わない）。
     半端に古い数字が出ることはない
  ② 焼き直した数字が本当にその点数であることは、ここで毎回計算し直して
     突き合わせる。①だけだと「指紋は合っているが中身が手書き」を通す

②は同梱2本ぶんのエンジン実行を伴うので十数秒かかる。**それでも
毎回回す。** これを飛ばしてよい理由は「遅いから」しかなく、
それは腐った数字を入口に出す理由にならない。
"""

import importlib.util
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from irdojo import swap
from irdojo.api import app
from irdojo.loader import list_scenarios

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def balance():
    spec = importlib.util.spec_from_file_location("balance", ROOT / "tools" / "balance.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["balance"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def stored():
    data = swap.load()
    assert data is not None, (
        "入口の 3×3 が読めません。"
        "python tools/balance.py --emit-swap を流し直してください"
    )
    return data


# ── 生成物が本物であること ──────────────────────────────


def test_the_stored_matrix_is_what_the_engine_scores_today(stored, balance):
    """保存してある点数を、いまのエンジンで計算し直しても同じであること。

    **入口に出る数字の出どころは、この計算1つだけである。** 画面にも
    SPEC にも書き写さないので、ここが通る限り入口は嘘をつけない。
    落ちたときの直し方は `python tools/balance.py --emit-swap`。
    """
    saved = {s.scenario_id: s for s in stored.scenarios}
    for sc in list_scenarios():
        assert sc.meta.id in saved, f"{sc.meta.id} の表が生成物に無い"
        fresh = balance.policy_swap(sc)
        row = saved[sc.meta.id]
        assert [r.policy_id for r in row.rows] == row.policy_ids
        for r in row.rows:
            want = {k: int(v) for k, v in fresh[r.policy_id].items()}
            assert r.scores == want, f"{sc.meta.id} / {r.policy_id} がずれている"


def test_every_bundled_scenario_has_a_row(stored):
    """シナリオを足したら、生成物も足されていること。

    足したまま焼き直さないと、入口は**前からある盤面の表**を
    出し続ける。指紋はシナリオを含むので実際には表ごと消えるが、
    消えた理由がこれであることを名指しで言えるようにしておく。
    """
    assert {s.scenario_id for s in stored.scenarios} == {
        sc.meta.id for sc in list_scenarios()
    }


# ── 腐ったら「出さない」こと ───────────────────────────


def test_a_stale_file_is_withheld_rather_than_shown(tmp_path):
    """指紋が合わない生成物は読まない。**古い数字を出すより出さないほうがよい。**

    盤面を触って焼き直しを忘れた朝に、入口が前の週の点数を
    正しい顔で出していることが、この仕掛けで一番避けたい形である。
    """
    data = json.loads(swap.DATA_FILE.read_text(encoding="utf-8"))
    data["fingerprint"] = "0" * 64
    stale = tmp_path / "policy_swap.json"
    stale.write_text(json.dumps(data), encoding="utf-8")
    assert swap.load(stale) is None


def test_a_broken_file_does_not_produce_half_a_table(tmp_path):
    """形が壊れていたら、読める部分だけを信じない。

    半分だけ本当の表は、全部嘘の表より始末が悪い。
    """
    for bad in ("{}", "[]", "壊れています", '{"fingerprint": 1, "scenarios": []}',
                '{"fingerprint": "x", "scenarios": [{"scenario_id": "a"}]}'):
        path = tmp_path / "bad.json"
        path.write_text(bad, encoding="utf-8")
        assert swap.load(path) is None
    assert swap.load(tmp_path / "存在しない.json") is None


def test_the_fingerprint_covers_everything_that_moves_the_numbers():
    """指紋の対象は**既定で「入れる」側**であること。

    新しく足したエンジンのモジュールが黙って対象外になると、そこを
    触っても指紋が動かず、入口だけが前の点数のまま生き残る。
    外してよいのは配信・記録・表示の層だけで、それは名前で列挙する。
    """
    covered = {p.relative_to(swap.ROOT).as_posix() for p in swap.source_files()}
    engine = {
        p.relative_to(swap.ROOT).as_posix()
        for p in ROOT.joinpath("irdojo").rglob("*.py")
        if "__pycache__" not in p.as_posix()
    }
    missing = engine - covered - swap.EXCLUDED
    missing = {m for m in missing if not m.startswith(swap.EXCLUDED_DIRS)}
    assert not missing, f"指紋から外れているモジュールがある: {sorted(missing)}"
    # 全シナリオと、点数を作っている道具そのもの
    assert {p.name for p in ROOT.joinpath("scenarios").glob("*.yaml")} <= {
        pathlib.Path(c).name for c in covered
    }
    assert "tools/balance.py" in covered


def test_touching_a_scenario_moves_the_fingerprint(tmp_path, monkeypatch):
    """シナリオを1バイト触ったら、指紋が動くこと。

    動かないなら、入口は盤面が変わっても前の表を出し続ける。
    """
    before = swap.fingerprint()
    target = ROOT / "scenarios" / "ransomware-initial-response-01.yaml"
    original = target.read_bytes()
    try:
        target.write_bytes(original + "\n# 指紋の検査\n".encode("utf-8"))
        assert swap.fingerprint() != before
    finally:
        target.write_bytes(original)
    assert swap.fingerprint() == before


# ── 入口に出る形 ────────────────────────────────────


def test_the_endpoint_does_not_say_which_board_it_came_from():
    """入口はシナリオを選ぶ前の画面なので、**表が盤面と結び付かない。**

    盤面の名前を配線に載せないのが、一番簡単な閉じ方である。
    """
    body = TestClient(app).get("/api/policy-swap").json()
    assert body["available"] is True
    text = json.dumps(body, ensure_ascii=False)
    for sc in list_scenarios():
        assert sc.meta.id not in text
        assert sc.meta.title not in text
        for a in sc.world.assets:
            assert a.id not in text
            assert a.label not in text
        for e in sc.evidence:
            assert e.id not in text
        for a in sc.actions:
            assert a.id not in text


def test_the_rows_are_labelled_no_further_than_a_letter():
    """行の名前は A / B / C まで。**ラベル以上のことを言わない。**

    「業務を止めない案」と書いた瞬間に、入口が盤面の手の内容を語り始める。
    """
    body = TestClient(app).get("/api/policy-swap").json()
    labels = [r["label"] for r in body["rows"]]
    assert labels == [f"{c}の対応" for c in "ABC"[: len(labels)]]


def test_every_column_is_scored_by_every_row():
    """表は埋まっていること。欠けた升目は「その方針では測っていない」に読める。"""
    body = TestClient(app).get("/api/policy-swap").json()
    ids = [p["id"] for p in body["policies"]]
    assert len(ids) >= 2 and len(body["rows"]) >= 2
    for r in body["rows"]:
        assert list(r["scores"]) == ids
