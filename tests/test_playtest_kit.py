"""テストプレイの道具立て（SPEC 7.5.4 / 7.5.5 / 7.5.6）。

**知りたいのは点数ではなく「どこで詰まったか」である。**
結果だけを保存していた頃の記録には、それが1つも残っていなかった。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from irdojo.api import app

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture
def client():
    """記録の行き先は `conftest.isolated_home` が一時ディレクトリへ逃がす。"""
    return TestClient(app)


# ── 足あと（7.5.4） ──────────────────────────────


def _play(client, scenario_id: str = "ransomware-initial-response-01") -> dict:
    """1回ぶん通しで遊ぶ。**押せる手だけを押す。**"""
    s = client.post("/api/session", json={
        "scenario_id": scenario_id,
        "policy_id": "business_continuity",
        "assist_level": "assisted",
    }).json()
    sid = s["session_id"]
    client.post(f"/api/session/{sid}/begin")
    # 被疑判定を出す前に進もうとする。**断られる手**（盤面は動かない）
    client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
    for a in s["view"]["available_actions"][:2]:
        client.post(f"/api/session/{sid}/decide",
                    json={"kind": "action", "action_id": a["id"]})
    asset = s["view"]["assets"][0]["id"]
    client.post(f"/api/session/{sid}/decide",
                json={"kind": "declare_assessment", "assessment": [asset]})
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    client.get(f"/api/session/{sid}/report")
    return s


def test_the_record_keeps_the_moves(client):
    """押した手の順番が記録に残ること。

    エンジンは `executed_actions` を持っていたのに、記録には結果しか
    書いていなかった。**どこで詰まったかは順番にしか出ない。**
    """
    from irdojo import records

    _play(client)
    _name, rec = records.load_all("ransomware-initial-response-01")[-1]
    assert rec.moves, "足あとが残っていない"
    assert [m.n for m in rec.moves] == list(range(1, len(rec.moves) + 1))
    assert rec.moves[-1].kind == "finish"
    # 手を押した回は、押す前の盤面時刻と、その手で進んだ分が両方ある
    acted = [m for m in rec.moves if m.kind == "action"]
    assert acted and all(m.action_id for m in acted)
    assert any(m.cost_minutes > 0 for m in acted), "費やした盤面時間が残っていない"


def test_the_record_keeps_the_blocked_move(client):
    """**断られた手も残す。**

    「被疑判定を出す前に進もうとした」は盤面を1つも動かさないが、
    規則が伝わっていなかったという意味では通った手より強い合図である。
    """
    from irdojo import records

    _play(client)
    _name, rec = records.load_all("ransomware-initial-response-01")[-1]
    kinds = [m.kind for m in rec.moves]
    assert "advance_phase_blocked" in kinds, f"断られた手が残っていない: {kinds}"


def test_the_record_keeps_the_briefing_seconds(client):
    """ブリーフィングを読んでいた秒が残ること。

    **読ませすぎていないかは、ここでしか分からない。**
    `begin` を送らないと、最初の1手の「考えていた秒」に丸ごと混ざる。
    """
    from irdojo import records

    _play(client)
    _name, rec = records.load_all("ransomware-initial-response-01")[-1]
    assert rec.briefing_seconds is not None, "ブリーフィングの秒が残っていない"
    assert rec.briefing_seconds >= 0


def test_begin_moves_nothing_on_the_board(client):
    """`begin` は時刻を受けるだけ。**盤面は1つも動かない。**"""
    s = client.post("/api/session", json={
        "scenario_id": "ransomware-initial-response-01",
        "policy_id": "business_continuity",
        "assist_level": "assisted",
    }).json()
    sid = s["session_id"]
    before = client.post(f"/api/session/{sid}/decide",
                         json={"kind": "advance_phase"}).json()["view"]
    client.post(f"/api/session/{sid}/begin")
    after = client.post(f"/api/session/{sid}/decide",
                        json={"kind": "advance_phase"}).json()["view"]
    assert before["elapsed_minutes"] == after["elapsed_minutes"]
    assert before["current_phase"] == after["current_phase"]
    assert before["obtained_evidence"] == after["obtained_evidence"]


def test_begin_does_not_answer_for_an_unknown_session(client):
    assert client.post("/api/session/deadbeef/begin").status_code == 404


def test_the_moves_carry_no_ground_truth(client):
    """足あとに真相が混ざらないこと（SPEC 7.7.5）。

    記録はテスターからそのまま送り返してもらうので、ここが漏れていると
    **回収そのものが答え合わせになる。**

    見るのは**欄の顔ぶれ**である。中身を文字列で探すやり方は当てにならない —
    `act_collect_evtx_fs01` のように、**学習者が自分で押した手の id に
    資産の id が入っている**ことがあり、それは漏洩ではない（その手の名前は
    最初から画面に出ている）。欄が増えたときに初めて漏れうるので、欄を数える。
    """
    from irdojo.records import Move, Record

    allowed = {"n", "kind", "action_id", "phase", "at_minute",
               "cost_minutes", "think_seconds"}
    assert set(Move.model_fields) == allowed, (
        f"足あとに欄が増えている: {sorted(set(Move.model_fields) - allowed)}"
    )

    _play(client)
    from irdojo import records

    _name, rec = records.load_all("ransomware-initial-response-01")[-1]
    blob = json.dumps(rec.model_dump(mode="json"), ensure_ascii=False)
    # 真相を入れている「箱」の名前だけを見る。`persistence` や `patient_zero`
    # のような語は論点の id（`q_persistence`）にも出るので、ここでは数えない —
    # **それは学習者が画面で見ている自分の論点である。**
    # 欄が増えていないことは上の白名簿が見ている
    for banned in ("ground_truth", "attack_narrative"):
        assert banned not in blob, f"記録に {banned} が入っている"
    # 記録の欄そのものにも、真相の側の名前が無いこと
    for name in Record.model_fields:
        assert "truth" not in name and "misleading" not in name


# ── 配布物（7.5.6） ──────────────────────────────


def test_the_tester_zip_leaves_out_everything_that_tells_the_answer():
    """**配るのは遊ぶのに要るものだけ。**

    SPEC も CLAUDE.md もテストも道具も入れない。読めば答えに近づける。
    """
    import make_tester_zip as kit

    assert set(kit.INCLUDE_DIRS) == {"irdojo", "web", "scenarios"}
    for banned in ("SPEC.md", "CLAUDE.md", "tests/", "tools/", ".git/"):
        assert banned in kit.EXCLUDE_NOTE, f"{banned} を外す理由が書かれていない"
    assert "tools" not in kit.INCLUDE_DIRS, "真相を読む道具を配ろうとしている"


def test_the_tester_zip_can_actually_be_installed(tmp_path):
    """`pip install -e .` が**テスターの機械で**通る形になっていること。

    `pyproject.toml` は `readme = "README.md"` を指しているが、
    README.md は配らない。落とさないまま渡すと hatchling が
    `Readme file does not exist` で止まる — **こちらでは動くので気づけない。**
    実際に展開して入れてみるまで出なかった。
    """
    import make_tester_zip as kit

    staged = tmp_path / "pyproject.toml"
    staged.write_text((ROOT / "pyproject.toml").read_text("utf-8"), encoding="utf-8")
    kit._strip_readme(staged)
    text = staged.read_text("utf-8")
    assert "readme" not in text, "readme の行が残っている"
    assert "[project]" in text and "dependencies" in text, "落としすぎている"


def test_the_table_is_still_shown_in_a_build_without_the_generator(monkeypatch, tmp_path):
    """配布物でも入口の表が出ること（SPEC 7.6.5）。

    `tools/balance.py` は配布物に入らない（真相を読むプレイ像を持っている）。
    指紋の材料にそれを入れていたので、**テスターの機械でだけ**
    `/api/policy-swap` が 500 を返していた。

    **点数を決めるのはエンジンとシナリオで、そこは配った先でも
    最後まで突き合わせている。** 道具は手元にあるときだけ見る。
    """
    from irdojo import swap

    monkeypatch.setattr(swap, "GENERATOR", tmp_path / "balance.py")
    assert swap.generator_digest() == "", "無い道具の指紋を名乗っている"
    data = swap.load()
    assert data is not None, "配布物で表が消えている"
    assert data.scenarios, "表が空"


# ── 集計（7.5.5） ────────────────────────────────


def test_the_report_says_that_the_people_who_quit_are_not_in_it():
    """**完走率は記録からは出せない。**

    記録は `finish` を押した回にしか作られない。黙っていると
    「残っている記録がすべて」と読まれる。道具の側が先に言う。
    """
    src = (ROOT / "tools" / "playtest_report.py").read_text("utf-8")
    assert "途中でやめた回はここに出ない" in src


def test_the_report_resolves_names_at_read_time():
    """記録には id しか書かない。名前は読むときに引く。

    名前を記録に書き写すと、シナリオを直した日に記録だけが古い名前を持つ
    （凡例のモックが3フェーズのまま腐ったのと同じ壊れ方 / 7.6.7）。
    """
    from irdojo.records import Move

    assert "action_label" not in Move.model_fields, "記録に名前を書き写している"
    src = (ROOT / "tools" / "playtest_report.py").read_text("utf-8")
    assert "def labels_for" in src, "読むときに名前を引いていない"


# ── クローンして遊んでもらう（7.5.7） ──────────────


def _truth_tokens() -> set[str]:
    """同梱シナリオの真相の語。**これが書いてある場所は、遊ぶ前に開けない。**"""
    from irdojo.loader import list_scenarios

    out: set[str] = set()
    for sc in list_scenarios():
        gt = sc.world.ground_truth
        out |= set(gt.compromised) | set(gt.persistence) | {gt.patient_zero}
        out |= {e.id for e in sc.evidence if e.misleading}
    return {t for t in out if t and len(t) >= 4}


SCAN_SKIP = {".git", ".venv", "dist", "__pycache__", ".pytest_cache",
             "node_modules", ".irdojo", "results"}


def _paths_that_tell_the_answer() -> set[str]:
    """答えが出る最上位の場所を、実際に走査して求める。

    **手で並べない。** 並べると、新しく足した場所が黙って外れる。
    """
    tokens = _truth_tokens()
    found: set[str] = set()
    for entry in sorted(ROOT.iterdir()):
        if entry.name in SCAN_SKIP or entry.name.startswith(".playtest"):
            continue
        if entry.name.startswith(".") and entry.is_dir() and entry.name != ".claude":
            continue
        files = [entry] if entry.is_file() else [
            f for f in entry.rglob("*") if f.is_file()
        ]
        for f in files:
            if set(f.parts) & SCAN_SKIP:
                continue
            try:
                text = f.read_text("utf-8", errors="ignore")
            except OSError:
                continue
            if any(t in text for t in tokens):
                found.add(entry.name)
                break
    return found


def test_the_readme_names_every_place_that_tells_the_answer():
    """**遊ぶ人を招くなら、開いてはいけない場所を全部名指しする**（SPEC 7.5.7）。

    リポジトリに招待して遊んでもらう形では、真相は技術では閉じられない。
    閉じられないものは、はっきり頼むしかない。**頼む先が1つでも漏れたら、
    そこだけ静かに開かれる。**

    並べるのは走査の結果であって、手書きの一覧ではない。
    答えの出る場所を新しく足したら、この検査が落ちる。
    """
    readme = (ROOT / "README.md").read_text("utf-8")
    warning = readme[readme.index("遊ぶ前に読まないでください"):]
    warning = warning[: warning.index("## 1. 動かす")]

    for name in sorted(_paths_that_tell_the_answer()):
        assert name in warning, (
            f"README が {name} を名指ししていない（ここに答えが出る）"
        )
    # 履歴も。HEAD から消しても、過去のコミットには残る
    assert "git log" in warning, "履歴に答えが残っていることを言っていない"


def test_the_debug_output_is_not_tracked():
    """検証の出力を追跡しないこと。

    実際に踏んだ: `.playtest/` は無視していたが `.playtest-stuck/` と `.pt/`
    は追跡されたままで、**講評のテキストごと 143 ファイル**入っていた。
    名前を1つずつ足す形にしていたのが原因なので、`*` で受ける。
    """
    ignore = (ROOT / ".gitignore").read_text("utf-8")
    assert ".playtest*/" in ignore, "検証の出力先を名前ごとに足している"
    assert ".pt/" in ignore


def test_the_tester_zip_carries_both_licences():
    """**配る以上、許諾表示も一緒に配る**（SPEC 7.5.6）。

    MIT は「上記の著作権表示および本許諾表示を、**ソフトウェアの複製または
    重要な部分のすべてに記載する**こと」を条件にしている。配布物は複製そのもので、
    そこに `LICENSE` が入っていなければ**その条件を満たしていない。**

    実際、長いあいだ入っていなかった。`scenarios/LICENSE`（CC-BY）は
    `copytree` に付いてきたので**片方だけが入っていて**、
    「ライセンスは同梱されている」ように見えていた。
    **付いてきたものと、入れたものを、区別していなかった。**

    zip を組む側の一覧で見る（実際に固めなくても、入る物は決まっている）。
    """
    import make_tester_zip as kit

    assert "LICENSE" in kit.INCLUDE_FILES, (
        "本体の MIT が配布物に入らない。MIT はすべての複製に許諾表示を求める"
    )
    assert "scenarios" in kit.INCLUDE_DIRS, (
        "盤面の CC-BY が配布物に入らない"
    )
    assert (ROOT / "LICENSE").exists() and (ROOT / "scenarios" / "LICENSE").exists()
