"""練習の盤面（SPEC 3.13）。

**チュートリアルは、この製品の原則と正面から衝突しうる唯一の機能である。**
「答えを漏らさない」を掲げた道具が、「まずこうしましょう」と手順を教える。

衝突させないための線は2本。

1. **書くのは手順と理由であって、答えではない。** 答えはふつうの演習と
   同じく講評で初めて出る（5.9）
2. **手を名指しする段には必ず「ただし」が付く。** それが唯一の正解では
   ないことを同じ画面で言わないと、製品の主張と食い違う
"""

from __future__ import annotations

import pytest

from irdojo.loader import list_scenarios, load_scenario
from irdojo.schema import CONCLUSION_WORDS


def tutorials():
    return [sc for sc in list_scenarios() if sc.meta.tutorial]


def test_there_is_a_tutorial_for_the_controls_and_one_for_the_thinking():
    """**2つ要る。** 操作と判断は別のことである。

    利用者の依頼：「どのように遊ぶのかの遊び方のチュートリアルと、
    インシデントレスポンスの教科書としてのチュートリアルのどちらも」。
    """
    ids = {sc.meta.id for sc in tutorials()}
    assert "tutorial-01-how-to-play" in ids, "遊び方の練習が無い"
    assert "tutorial-02-first-response" in ids, "教科書の練習が無い"


def test_the_tutorials_come_first_in_the_list():
    """初めての人が最初に見る場所に、最初にやるものを置く。

    ファイル名の並び順に任せると `tutorial-` は末尾に落ちる。
    """
    order = [sc.meta.tutorial for sc in list_scenarios()]
    assert order[0], "一覧の先頭が練習の盤面ではない"
    # 練習が途中に混ざらない（全部先頭に寄っている）
    assert order == sorted(order, reverse=True), f"練習が途中に混ざっている: {order}"


@pytest.mark.parametrize("sc", tutorials(), ids=lambda s: s.meta.id)
def test_a_step_that_names_a_move_says_it_is_not_the_only_one(sc):
    """**「まずこうしましょう」には「ただし」が要る**（SPEC 3.13）。

    手を1つ名指しする以上、それが唯一の正解ではないことを
    同じ画面で言わないと、この製品の主張（方針が変われば最善も変わる）と
    正面から衝突する。ローダも拒否するが、実物でも確かめる。
    """
    named = [s for s in sc.tutorial if s.expect_action]
    assert named, "手を名指しする段が1つも無い（案内になっていない）"
    for step in named:
        assert step.caveat.strip(), f"{step.id}: 「ただし」が無い"


@pytest.mark.parametrize("sc", tutorials(), ids=lambda s: s.meta.id)
def test_the_guidance_does_not_give_the_answer(sc):
    """案内に結論の言い回しが無いこと（原則1）。

    ローダの `_reject_conclusion_vocabulary` が案内も見ている。
    ここでは実物のシナリオで、もう一度確かめる。
    """
    for step in sc.tutorial:
        for word in CONCLUSION_WORDS:
            assert word not in step.body, f"{step.id}.body に「{word}」"
            assert word not in step.caveat, f"{step.id}.caveat に「{word}」"


@pytest.mark.parametrize("sc", tutorials(), ids=lambda s: s.meta.id)
def test_the_guidance_never_names_the_compromised_set(sc):
    """**真相の集合をそのまま書かない。**

    「この2つが侵害されています」と書けば、盤面を遊ぶ意味が消える。
    1つの資産に触れるのは構わない（ブリーフィングが既に名前を出している）。
    禁じるのは、**侵害資産の全部が同じ段に並ぶこと**である。
    """
    truth = set(sc.world.ground_truth.compromised)
    assert truth, "侵害資産が無い盤面"
    for step in sc.tutorial:
        text = step.body + step.caveat
        named = {a for a in truth if a in text}
        assert named != truth, (
            f"{step.id}: 侵害資産の集合がそのまま書かれている: {sorted(named)}"
        )


@pytest.mark.parametrize("sc", tutorials(), ids=lambda s: s.meta.id)
def test_the_guidance_walks_all_the_way_to_the_end(sc):
    """**最後まで連れて行く。**

    途中で案内が切れると、学習者は「次に何をすればいいか分からない画面」に
    置き去りにされる。それは、案内が無いより悪い。
    """
    assert sc.tutorial[-1].expect_kind == "finish", "最後が終了で終わっていない"
    known = {a.id for a in sc.actions}
    for step in sc.tutorial:
        if step.expect_action:
            assert step.expect_action in known, f"{step.id}: 盤面に無い手を指している"


@pytest.mark.parametrize("sc", tutorials(), ids=lambda s: s.meta.id)
def test_a_tutorial_board_is_still_a_real_board(sc):
    """**偽の盤面で操作だけ覚えても、本番で同じ画面に見えない。**

    練習の盤面もローダの検査を全部通っている（読み込めている時点で通っている）。
    そのうえで、誤導が最低1つあること — 誤導の無い盤面で練習すると、
    「調べれば書いてあるとおりに分かる」という誤った期待を持って本番に来る。
    """
    assert any(e.misleading for e in sc.evidence), "誤導が1つも無い"
    assert any(q.critical for q in sc.open_questions), "critical な論点が無い"
    assert sc.world.ground_truth.compromised, "侵害資産が無い"
    assert sc.world.ground_truth.innocent, "無実の資産が無い"


def test_the_tutorials_are_left_out_of_the_balance_measurement():
    """**練習の盤面は設計の均衡から外す**（SPEC 3.13）。

    外す理由は「通らないから」ではなく、**測っている対象が違う**ため。
    「網羅は損か」「折れ点を誰かが踏むか」は、案内どおりに歩く盤面では
    成立しない。黙って通すと、赤が出ないことを「均衡が取れている」と読む。
    """
    src = (
        (__import__("pathlib").Path(__file__).resolve().parent.parent
         / "tools" / "balance.py").read_text("utf-8")
    )
    assert "meta.tutorial" in src, "balance.py が練習の盤面を見分けていない"
    # 入口の 3×3 にも混ぜない
    from irdojo import swap

    data = swap.load()
    assert data is not None, "生成物が読めない（--emit-swap を流し直す）"
    tutorial_ids = {sc.meta.id for sc in tutorials()}
    baked = {s.scenario_id for s in data.scenarios}
    assert not (baked & tutorial_ids), f"入口の表に練習が混ざっている: {baked & tutorial_ids}"


def test_a_normal_scenario_has_no_guidance():
    """ふつうの演習に案内が付いていないこと。

    付いた瞬間に、その演習は答えを配る盤面になる。
    """
    for sc in list_scenarios():
        if sc.meta.tutorial:
            continue
        assert not sc.tutorial, f"{sc.meta.id} に案内が付いている"


def test_the_api_says_which_boards_are_practice(  ):
    """一覧が「これは練習だ」と言えること。

    言わないと、点が低く出たときに「自分は下手だ」と読まれる。
    案内どおりに歩く盤面は、そもそも競うためのものではない。
    """
    from fastapi.testclient import TestClient

    from irdojo.api import app

    rows = TestClient(app).get("/api/scenarios").json()
    assert rows and "tutorial" in rows[0], "一覧が練習かどうかを返していない"
    assert rows[0]["tutorial"] is True, "先頭が練習になっていない"


def test_the_guidance_does_not_copy_the_move_labels():
    """案内に手の**名前**を書き写さないこと。

    書き写すと、手の文言を直した日に案内だけが古い名前を指す
    （凡例のモックが3フェーズのまま腐ったのと同じ壊れ方 / SPEC 7.6.7）。
    画面は `expect_action` の id から名前を引いて出す。
    """
    web = (__import__("pathlib").Path(__file__).resolve().parent.parent
           / "web" / "app.js").read_text("utf-8")
    fn = web[web.index("function renderTutorial"):]
    fn = fn[: fn.index("\n/* ── 事件の時計")]
    assert "available_actions" in fn, "手の名前を盤面から引いていない"


# ── 案内どおりにしか動かせない（SPEC 7.6.29） ──────────


def _app_js() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text("utf-8")


def test_only_the_move_the_guidance_names_can_be_pressed():
    """**ルートをあらかじめ制限する**（SPEC 7.6.29）。

    利用者の言葉：「説明文をタッチするときは説明文以外を触れないように
    （途中でやめるは触れてOK）、この操作しての時はその操作のみできるように。
    ルートをあらかじめ制限することが大事かも」。

    はじめての人は、案内を読む前に手が動く。押せてしまう手があると、
    案内と盤面がずれた状態から始まり、**案内が嘘に見える。**
    """
    body = _app_js()
    fn = body[body.index("function applyTutorialLock") :]
    fn = fn[: fn.index("\n/* ── 事件の時計")] if "\n/* ── 事件の時計" in fn else fn
    # 止め方は inert。disabled はボタンにしか効かず、畳みの見出しには効かない
    assert "setAttribute('inert'" in fn, "inert で止めていない"
    # 読むだけの段は、案内の「次へ」だけを通す
    assert "#tutor-panel button" in fn, "読むだけの段で次へを通していない"
    # 手を名指しする段は、その手だけ
    assert "data-action=" in fn, "名指しされた手を通していない"


def test_the_way_out_is_never_blocked():
    """**「演習をやめる」だけは常に通す**（SPEC 7.6.29）。

    出口を塞ぐのは制限ではなく監禁である。
    あわせて、**盤面を1つも動かさない参照**（画面の見方・方針を再表示・
    いまの被疑判定）も通す。ここを塞ぐと、迷った人に逃げ場が無くなる。
    """
    body = _app_js()
    fn = body[body.index("var allow = [") :]
    fn = fn[: fn.index("];")]
    for must in ("#btn-abort", "#btn-show-legend", "#btn-show-policy"):
        assert must in fn, f"{must} が常に通る側に入っていない"
    # 盤面を動かすものは、ここに入っていないこと
    for must_not in ("#btn-finish", "#btn-advance"):
        assert must_not not in fn, f"{must_not} が無条件に通っている"


def test_a_normal_board_is_not_locked():
    """ふつうの演習では何も止めない（SPEC 7.6.29）。

    止めるのは案内があるときだけ。案内の無い盤面で手が減ったら、
    それは演習ではなくなる。
    """
    body = _app_js()
    fn = body[body.index("function applyTutorialLock") :]
    fn = fn[: fn.index("\n}") + 2]
    assert "if (!live) { return; }" in fn, "案内が無いときに素通りしていない"
    assert "removeAttribute('inert')" in fn, "前の段の止めを解いていない"


def test_the_action_buttons_carry_their_id():
    """案内が手を名指しできること。

    `data-action` が無いと、案内は「右の◯◯を押して」としか言えず、
    **どれを通すかを画面が決められない。**
    """
    body = _app_js()
    fn = body[body.index("function actionCard") :]
    fn = fn[: fn.index("\n}") + 2]
    assert "data-action" in fn, "手に id を持たせていない"


def test_the_playtest_walks_the_guided_route():
    """遊んで確かめる側も、案内どおりに歩くこと。

    **遊び方が違うのだから、測り方も変える。** ふつうの盤面は自由に押す像で
    測るが、練習は通る手が1つしか無い。自由に押す像はそこで詰まる。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tools" / "playtest.mjs").read_text("utf-8")
    assert "walkTutorial" in src, "案内どおりに歩く道筋が無い"
    assert "案内どおりに歩いても講評へ辿り着かない" in src, "途中で止まっても黙っている"
