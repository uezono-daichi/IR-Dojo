"""演習中に真実が動く盤面（SPEC 5.2 の `ground_truth.spreads`）。

1本目・2本目の真実は執筆時に固定で、`timeline` は圧力しか運ばなかった。
3本目だけが**侵害資産の集合そのものを動かす**。動く以上、
「いつの盤面と突き合わせたのか」を決めないと採点が読めなくなる —
ここで見張るのはその線である。

  事実認識層（適合率・再現率）… **宣言した時点**の盤面
  封じ込めの完全度・被害モデル … **終了時点**の盤面

この2つが同じ集合を見ていると、早く正しく判断して止めそこねた学習者と、
遅くまで気づかなかった学習者が、同じ理由で同じだけ減点される。
別の失敗は別の層で咎めるのが原則3 である。
"""

import copy

import pytest
import yaml

from irdojo import scoring
from irdojo.engine import Decision, Engine, InvalidDecision, compromised_now
from irdojo.loader import SCENARIO_DIR, ScenarioError, load_scenario, load_scenario_text
from irdojo.report import json as report_json

SPREAD_ID = "software-distribution-spread-01"
SOURCE_STOP = "act_stop_dist_service"     # 配り元を止める最安の手（10分）
FIRST_WINDOW = 270                        # ws-sales へ配られる窓
SECOND_WINDOW = 380                       # ws-acct へ配られる窓


@pytest.fixture
def board():
    return load_scenario(SPREAD_ID)


@pytest.fixture
def raw_spread():
    path = SCENARIO_DIR / f"{SPREAD_ID}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _reload(data):
    return load_scenario_text(yaml.safe_dump(data, allow_unicode=True))


def _advance(engine, until, skip=()):
    """押せる調査の手を順に押して、時計を `until` まで進める。

    **時計は手を動かしたときにしか進まない**（SPEC 5.10）ので、
    待つだけでは窓は開かない。どの手を押したかはここでは問題ではない。
    """
    for act in engine.scenario.actions:
        if engine.state.elapsed_minutes >= until:
            return
        if act.type.value != "investigate" or act.id in skip:
            continue
        try:
            engine.decide(Decision(kind="action", action_id=act.id))
        except InvalidDecision:
            continue
    assert engine.state.elapsed_minutes >= until, (
        f"{until}分 まで進められなかった（{engine.state.elapsed_minutes}分）"
    )


def _declare(engine, assets):
    engine.decide(Decision(kind="declare_assessment", assessment=list(assets)))


# ─────────── 窓が開く条件 ───────────


def test_a_window_opens_only_because_nobody_closed_the_source(board):
    """配られるのは、配り元が動いたままだったからである。

    **学習者の不作為が原因でしか広がらない形にしてある**（SPEC 5.2）。
    ここが「時刻が来たら必ず起きる」に退化すると、遅い学習者は
    自分の判断と無関係に不正解にされる — それは教材ではなく罰である。
    """
    loose = Engine(board)
    _advance(loose, FIRST_WINDOW + 1)
    assert "ws-sales" in loose.state.spread_at
    assert loose.state.spreads_fired == ["sp_ring_sales"]

    closed = Engine(board)
    _declare(closed, ["dist01", "ws-eng01"])
    closed.decide(Decision(kind="action", action_id=SOURCE_STOP))
    _advance(closed, FIRST_WINDOW + 1)
    assert closed.state.spread_at == {}, "止めてあるのに配られた"
    assert closed.state.spreads_averted == ["sp_ring_sales"]


def test_stopping_the_source_after_the_window_is_too_late(board):
    """効くのは打ち終わった時刻から先だけである。

    連絡（SPEC 5.6.1）と同じ規則を、配り元の停止にもそのまま当てる。
    窓が開いた後に止めても、既に配られた分は戻らない。
    """
    e = Engine(board)
    _advance(e, FIRST_WINDOW + 1)
    _declare(e, ["dist01", "ws-eng01"])
    e.decide(Decision(kind="action", action_id=SOURCE_STOP))
    assert "ws-sales" in e.state.spread_at
    # 止めたので、2つ目の窓はもう開かない
    _advance(e, SECOND_WINDOW + 1)
    assert "ws-acct" not in e.state.spread_at


def test_the_board_grows_one_window_at_a_time(board):
    """時間が経つほど侵害資産が増える。これがこの盤面の中心である。"""
    e = Engine(board)
    assert compromised_now(board, e.state) == ["dist01", "ws-eng01"]
    _advance(e, FIRST_WINDOW + 1)
    assert len(compromised_now(board, e.state)) == 3
    _advance(e, SECOND_WINDOW + 1)
    assert len(compromised_now(board, e.state)) == 4


# ─────────── 採点がどの盤面と突き合わせるか ───────────


def test_the_assessment_is_judged_against_the_board_it_was_made_on(board):
    """「判定した時点では正しかった」が表現できること（SPEC 6.2）。

    窓が開く前に正しく名指しした学習者は、そのあと盤面が動いても
    **事実認識では満点のまま**である。間違えたのは事実の把握ではなく、
    止める場所と速さで、それは帰結の層（完全度・被害）が測る。

    ここを終了時点の集合で採点すると、早く正しく判断した学習者が
    「知りようのなかったもの」を名指ししなかったことで減点される。
    """
    e = Engine(board)
    _advance(e, 120)
    assert e.state.elapsed_minutes < FIRST_WINDOW
    _declare(e, ["dist01", "ws-eng01"])
    snap = e.state.assessment_snapshot
    assert snap.compromised == ["dist01", "ws-eng01"]

    # 配り元を止めないまま時計を進める。盤面は2度広がる
    _advance(e, SECOND_WINDOW + 1)
    e.decide(Decision(kind="finish"))

    report = scoring.score(e.state, board)
    assert report.metrics["assessment_precision"] == 1.0
    assert report.metrics["assessment_recall"] == 1.0, "判定時点の分母で測っていない"
    # 帰結の側は終了時点の広さで測る。止めた資産は1つも無い
    assert report.consequences.containment_completeness == 0.0


def test_the_two_denominators_move_apart(board):
    """再現率の分母と完全度の分母は、別々の時点の盤面である。

    同じ集合を使い回すと、この2つは必ず一致してしまい、
    「正しく判断したが止めきれなかった」と「気づかなかった」が
    区別できなくなる。
    """
    e = Engine(board)
    _advance(e, 120)
    _declare(e, ["dist01", "ws-eng01"])
    e.decide(Decision(kind="action", action_id="act_isolate_eng01"))
    _advance(e, SECOND_WINDOW + 1)
    e.decide(Decision(kind="finish"))

    parts = scoring.score(e.state, board).metric_parts
    assert parts["assessment_recall"][0].den == 2      # 宣言した時点の広さ
    cons = scoring.consequences(e.state, board)
    # 終了時点は4台。止められたのは1台だけ
    assert cons.containment_completeness == pytest.approx(0.25)


def test_naming_what_has_since_been_hit_is_not_punished(board):
    """盤面が動いたことに気づいて名指しを足した学習者を、罰しない。

    被疑判定は対応フェーズ中も出し直せる（SPEC 3.6）。出し直した名指しを
    **最初の宣言の時点**の盤面と突き合わせると、あとから配られた資産を
    足した人が適合率を落とす — 気づいたことへの罰になる。
    突き合わせる相手は、出し直した時点の盤面である。
    """
    e = Engine(board)
    _advance(e, 120)
    _declare(e, ["dist01", "ws-eng01"])
    _advance(e, SECOND_WINDOW + 1)
    _declare(e, ["dist01", "ws-eng01", "ws-sales", "ws-acct"])
    e.decide(Decision(kind="finish"))

    report = scoring.score(e.state, board)
    assert report.metrics["assessment_precision"] == 1.0
    assert report.metrics["assessment_recall"] == 1.0


# ─────────── まだ起きていないことは配らない ───────────


def test_paper_that_does_not_exist_yet_cannot_be_taken(board):
    """窓が開く前にその資料を引いても、そこには何も無い（SPEC 5.2）。

    静的な資料にしてしまうと、盤面は**まだ起きていないこと**を
    先に配ることになる。逆に窓が開いた後は、引き直せば手に入る —
    そうでないと、遅く判断した学習者は増えた資産を名指しする手段を
    持たないまま再現率だけを落とす。
    """
    e = Engine(board)
    e.decide(Decision(kind="action", action_id="act_eng_edr_detail"))
    e.decide(Decision(kind="action", action_id="act_deploy_ledger"))
    assert e.state.elapsed_minutes < FIRST_WINDOW
    out = e.decide(Decision(kind="action", action_id="act_endpoint_sweep"))
    assert "ev_ring_sales" not in out.revealed
    assert "ev_sweep_state" in out.revealed, "回った時点の姿は返ること"

    _advance(e, FIRST_WINDOW + 1)
    again = e.decide(Decision(kind="action", action_id="act_endpoint_sweep"))
    assert "ev_ring_sales" in again.revealed, "配られた後は引き直せば出ること"
    assert "ev_ring_acct" not in again.revealed, "まだ開いていない窓の分まで出ている"


def test_a_window_that_never_opened_leaves_no_paper(board):
    """防いだ窓の資料は、最後まで盤面に現れない。

    起きなかったことの記録はどこにも無い。ここを「防いでも資料は出る」に
    すると、防いだ学習者が**起きていない侵害の証拠**を手にする。
    """
    e = Engine(board)
    e.decide(Decision(kind="action", action_id="act_eng_edr_detail"))
    e.decide(Decision(kind="action", action_id="act_deploy_ledger"))
    _declare(e, ["dist01", "ws-eng01"])
    e.decide(Decision(kind="action", action_id=SOURCE_STOP))
    _advance(e, SECOND_WINDOW + 1)
    out = e.decide(Decision(kind="action", action_id="act_endpoint_sweep"))
    assert "ev_ring_sales" not in out.revealed
    assert "ev_ring_acct" not in out.revealed
    assert set(e.state.obtained_evidence).isdisjoint({"ev_ring_sales", "ev_ring_acct"})


# ─────────── 講評 ───────────


def test_the_debrief_counts_the_board_twice(board):
    """講評が「判定した時点では N 台、終了時点では M 台」と言えること。

    数字が2つあることを言わないと、再現率 100% と完全度 25% が
    同じ画面に並んでいる理由がどこにも書かれていないことになる。
    """
    e = Engine(board)
    _advance(e, 120)
    _declare(e, ["dist01", "ws-eng01"])
    _advance(e, SECOND_WINDOW + 1)
    e.decide(Decision(kind="finish"))

    rep = report_json.build(e.state, board, persist=False)
    assert rep.spread is not None
    assert rep.spread.at_decision == 2
    assert rep.spread.at_finish == 4
    assert [s.happened for s in rep.spread.steps] == [True, True]
    assert all(s.stopped_at is None for s in rep.spread.steps)
    # 止めた・止めそこねたの表も、終了時点の広さで並ぶ
    assert len(rep.containment.stopped) + len(rep.containment.missed) == 4


def test_the_two_tables_in_the_debrief_agree(board):
    """講評の2つの表が、同じ数を言っていること。

    「実際に侵害されていた資産」がすぐ下の「演習を終えた時点」と違う数を
    出していたら、読み手にはどちらが本当か決められない。**どちらも本当**
    なので、片方だけを「実際に」と呼ぶことができない — 真相の表が出すのは
    このプレイの帰結（終了時点）で、そこへ至った経緯は次の節が言う。
    """
    e = Engine(board)
    _advance(e, 120)
    _declare(e, ["dist01", "ws-eng01"])
    _advance(e, SECOND_WINDOW + 1)
    e.decide(Decision(kind="finish"))

    rep = report_json.build(e.state, board, persist=False)
    assert len(rep.truth.compromised) == rep.spread.at_finish
    assert set(rep.truth.compromised) == set(compromised_now(board, e.state))
    # 止めた・止めそこねたの表も同じ数を並べる
    assert len(rep.containment.stopped) + len(rep.containment.missed) \
        == rep.spread.at_finish


def test_a_board_that_does_not_move_says_nothing_about_moving(board):
    """真実が動かない盤面には、この節を出さない。

    「判定時点 3台／終了時点 3台」は、読み手には何の話か分からない。
    欄を出すこと自体が「動きうる」という情報になるので、
    動かない盤面では None にする。
    """
    still = load_scenario("ransomware-initial-response-01")
    e = Engine(still)
    e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))
    _declare(e, ["fs01"])
    e.decide(Decision(kind="finish"))
    rep = report_json.build(e.state, still, persist=False)
    assert rep.spread is None


def test_a_board_without_spreads_is_scored_from_the_static_truth():
    """拡散を持たない盤面では、採点の入口が1つも増えていないこと。

    `spreads` を足した日に既存の盤面が1点でも動いたら、それは
    新しい仕組みが既定で効いてしまっているということである。
    """
    for sid in ("ransomware-initial-response-01", "oauth-consent-abuse-01"):
        sc = load_scenario(sid)
        e = Engine(sc)
        assert compromised_now(sc, e.state) == sc.world.ground_truth.compromised
        _declare(e, list(sc.world.ground_truth.compromised))
        assert e.state.assessment_compromised == sc.world.ground_truth.compromised
        assert e.state.assessment_snapshot.compromised == sc.world.ground_truth.compromised
        report = scoring.score(e.state, sc)
        assert report.metrics["assessment_recall"] == 1.0


# ─────────── ローダの歯 ───────────


def test_the_loader_rejects_a_spread_nobody_can_prevent(raw_spread):
    """防ぎようのない拡散は、教材ではなく罰である（SPEC 5.2）。"""
    data = copy.deepcopy(raw_spread)
    data["world"]["ground_truth"]["spreads"][0]["unless_contained"] = []
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "unless_contained" in str(exc.value)


def test_the_loader_rejects_a_window_that_opens_before_anyone_could_close_it(raw_spread):
    """盤面の物理法則で開く窓を拒否する。

    最も安い止め方の合計より前に窓が置かれていたら、それは
    学習者の不作為ではない。**ここが見るのは下限だけ**で、
    「巧いプレイなら間に合うか」は tools/balance.py が実測する。
    """
    data = copy.deepcopy(raw_spread)
    data["world"]["ground_truth"]["spreads"][0]["at_minutes"] = 5
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "止めきれません" in str(exc.value)


def test_the_loader_rejects_a_spread_with_no_way_to_learn_of_it(raw_spread):
    """起きたことを知る資料が無いと、再現率だけが落ちる。

    防げない拡散を禁じたのと同じ理由が、事実認識の側にも要る。
    """
    data = copy.deepcopy(raw_spread)
    data["world"]["ground_truth"]["spreads"][0]["reveals"] = []
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "reveals" in str(exc.value)


def test_the_loader_rejects_paper_that_only_the_careless_can_hold(raw_spread):
    """拡散を防いだ学習者だけが解けない論点を作らせない。

    窓が開かなければその資料は永久に手に入らない。そこに critical 論点を
    紐づけると、**うまくやった人だけが論点を落とす**。
    """
    data = copy.deepcopy(raw_spread)
    for q in data["open_questions"]:
        if q["id"] == "q_persistence":
            q["resolved_by"] = ["ev_eng_memory", "ev_ring_sales"]
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "論点の解消" in str(exc.value)


def test_the_loader_rejects_a_spread_target_nobody_can_stop(raw_spread):
    """配られた先を止める手が無いと、完全度は誰にも 1.0 に届かない。"""
    data = copy.deepcopy(raw_spread)
    data["actions"] = [a for a in data["actions"] if a["id"] != "act_isolate_sales"]
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "直接止める手が無い" in str(exc.value)


def test_a_spread_target_is_not_an_innocent_decoy(board):
    """拡散先を、取り除く手の囮として数えないこと（SPEC 5.6 / v1.45）。

    「いずれ侵害される資産」への手で囮の要件を満たせてしまうと、
    束を読んだ学習者に配られるのは答えのままである。
    """
    from irdojo.schema import spread_targets

    gt = board.world.ground_truth
    reach = {t for a in board.actions for t in a.eradicates}
    secret = set(gt.compromised) | {gt.patient_zero} | spread_targets(board)
    assert reach - secret, "取り除く手が、答えの資産にしか届いていない"
    assert not (reach & spread_targets(board)), (
        "拡散先に取り除く手を置くと、囮として数えられてしまう"
    )
