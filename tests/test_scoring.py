"""SPEC 6.8 の計算例をそのままテストにする。

この1本が通れば、事実認識層・帰結指標・方針適合層・合成のすべてが
仕様どおりに動いていることになる。
"""

import pytest

from irdojo import retrospective, scoring
from irdojo.engine import Decision, Engine, InvalidDecision
from irdojo.schema import ActionType

# SPEC 6.8 のプレイ結果を再現する行動列
INVESTIGATION = [
    ("act_netflow_overview", 20),      # ev_007（誤導）を取得
    ("act_asset_inventory", 10),
    ("act_backup_config", 15),
    ("act_smb_session_fs01", 30),      # q_lateral_movement を解消
    ("act_memory_dump_ws042", 45),
    ("act_proxy_log", 20),
    ("act_dlp_review", 25),            # ev_007 取得後に ws-107 を調べる 25分
]


def play_spec_68(scenario, contain_first="act_shutdown_fs01"):
    e = Engine(scenario, "business_continuity", None)
    for aid, _ in INVESTIGATION:
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "ws-107"]))
    e.decide(Decision(kind="action", action_id=contain_first))
    e.decide(Decision(kind="action", action_id="act_isolate_ws042"))
    e.decide(Decision(kind="finish"))
    return e


def test_playthrough_state(scenario):
    e = play_spec_68(scenario)
    st = e.state
    assert st.investigation_minutes == 165      # 総調査時間
    assert st.elapsed_minutes == 190            # contain を含む最終値
    assert st.misled_follow_minutes == 25
    assert st.assessment_snapshot.unresolved_critical == ["q_initial_access"]
    assert st.destroyed_evidence == ["ev_010"]  # 隔離では揮発性証拠は失われない
    # 隔離では ev_009 が残るので、終了時点では永続化も解消されている
    assert st.resolved_questions == [
        "q_lateral_movement", "q_persistence", "q_exfiltration",
    ]
    assert st.contained_at == {"fs01": 180, "ws-042": 190}
    assert len(st.violations_by_policy["business_continuity"]) == 1


def test_consequences(scenario):
    c = scoring.score(play_spec_68(scenario).state, scenario).consequences
    assert c.elapsed_minutes == 190
    assert c.total_damage == pytest.approx(1850, abs=1)
    assert c.business_impact == pytest.approx(424.33, abs=0.01)
    assert c.evidence_preserved == pytest.approx(0.947, abs=0.001)
    assert c.containment_completeness == pytest.approx(1.000, abs=0.001)


def test_fact_metrics(scenario):
    r = scoring.score(play_spec_68(scenario).state, scenario)
    assert r.metrics["assessment_precision"] == pytest.approx(0.500, abs=0.001)
    assert r.metrics["assessment_recall"] == pytest.approx(0.500, abs=0.001)
    assert r.metrics["persistence_missed"] == pytest.approx(0.000, abs=0.001)
    assert r.metrics["misled_score"] == pytest.approx(0.361, abs=0.001)
    assert r.metrics["unresolved_questions"] == pytest.approx(0.333, abs=0.001)
    assert r.fact_score == pytest.approx(0.611, abs=0.001)


def test_composite(scenario):
    r = scoring.score(play_spec_68(scenario).state, scenario)
    assert r.policy_score == pytest.approx(0.714, abs=0.001)
    assert r.composite_score == pytest.approx(0.642, abs=0.001)
    assert round(r.composite_score * 100) == 64


def test_dependency_chain_variant(scenario):
    """dc01 を止めると、それに依存する全資産が同じ時刻で巻き込まれる。"""
    e = play_spec_68(scenario, contain_first="act_shutdown_dc01")
    # ws-042 は 190分に直接隔離したが、dc01 経由で 180分に繰り上がる
    assert e.state.contained_at == {
        "dc01": 180, "fs01": 180, "ws-042": 180, "ws-107": 180, "ws-113": 180,
    }
    r = scoring.score(e.state, scenario)
    assert r.consequences.business_impact == pytest.approx(1127.00, abs=0.01)
    assert r.consequences.evidence_preserved == pytest.approx(1.000, abs=0.001)
    assert r.policy_score == pytest.approx(0.573, abs=0.001)
    assert round(r.composite_score * 100) == 60


def test_same_play_scores_differently_per_policy(scenario):
    """設計の中核。同じ行動列でも、方針を差し替えると評価が変わる。"""
    st = play_spec_68(scenario).state
    got = {
        pid: scoring.score(st, scenario, scenario.policy_by_id[pid])
        for pid in ("business_continuity", "damage_minimization", "evidence_preservation")
    }
    assert got["business_continuity"].constraint_violations == 1
    assert got["damage_minimization"].constraint_violations == 0
    assert got["evidence_preservation"].constraint_violations == 0

    assert round(got["business_continuity"].composite_score * 100) == 64
    assert round(got["damage_minimization"].composite_score * 100) == 70
    assert round(got["evidence_preservation"].composite_score * 100) == 70

    # 事実認識層は方針に依存しない
    facts = {r.fact_score for r in got.values()}
    assert len(facts) == 1


def test_minimal_path(scenario):
    minutes, ids, exact = retrospective.minimal_path(scenario)
    assert exact is True
    assert minutes == 130
    assert sorted(ids) == [
        "act_collect_evtx_fs01", "act_mail_gateway",
        "act_memory_dump_ws042", "act_smb_session_fs01",
    ]


def test_counterfactual_reports_held_refutation(scenario):
    """棄却材料を持っていたのに使わなかった場合を、未取得と区別する。"""
    retro = retrospective.build(play_spec_68(scenario).state, scenario)
    cfs = retro.counterfactuals
    assert len(cfs) == 1
    assert cfs[0].asset_id == "ws-107"
    assert cfs[0].evidence_id == "ev_007"
    assert cfs[0].refuting_evidence == []       # ev_003 は取得済み
    assert cfs[0].refuting_obtained == ["ev_003"]


def test_assist_level_does_not_change_scoring(scenario):
    """アシストレベルは表示の設定であって、採点の設定ではない（SPEC 3.10）。"""
    from irdojo.schema import AssistLevel

    scores = []
    for level in AssistLevel:
        e = Engine(scenario, "business_continuity", level)
        for aid, _ in INVESTIGATION:
            e.decide(Decision(kind="action", action_id=aid))
        e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "ws-107"]))
        e.decide(Decision(kind="action", action_id="act_shutdown_fs01"))
        e.decide(Decision(kind="action", action_id="act_isolate_ws042"))
        e.decide(Decision(kind="finish"))
        scores.append(scoring.score(e.state, scenario).composite_score)
    assert len(set(scores)) == 1


# ── 全部押しへの耐性（SPEC 3.8） ───────────────────────────────


def _play(scenario, triage, investigation, policy=None):
    e = Engine(scenario, policy or scenario.meta.default_policy, None)
    for aid in list(triage) + list(investigation):
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "fs01"]))
    for aid in ("act_isolate_ws042", "act_shutdown_fs01"):
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="finish"))
    return e


SKILLED_T = ["act_collect_evtx_fs01"]
SKILLED_I = ["act_mail_gateway", "act_smb_session_fs01", "act_memory_dump_ws042"]


def _all_investigate(scenario, phase="investigation"):
    return [
        a.id for a in scenario.actions
        if a.phase == phase and a.type is ActionType.INVESTIGATE
    ]


def test_exhaustive_play_loses_to_skilled_play(scenario):
    """全部押しても、巧いプレイには負ける。

    順序さえ正しければ事実はすべて掴めるので、事実認識ではほとんど差がつかない。
    差がつくのは帰結（時間と、それに伴う被害）である。
    """
    every = _play(scenario, [], _all_investigate(scenario))
    skilled = _play(scenario, SKILLED_T, SKILLED_I)

    re_, rs = scoring.score(every.state, scenario), scoring.score(skilled.state, scenario)
    # 全部やれば事実は掴める。そこは罰しない
    assert every.state.assessment_snapshot.unresolved_critical == []
    assert re_.fact_score >= 0.95
    # 払った時間の差が方針適合に出る
    assert every.state.elapsed_minutes > skilled.state.elapsed_minutes * 3
    assert re_.policy_score < rs.policy_score
    # 差が意味のある大きさであること（SPEC 8.3 のチェックリスト）
    assert (rs.composite_score - re_.composite_score) * 100 >= 10


def _play_order(scenario, order):
    e = Engine(scenario, "business_continuity", None)
    # ws-042 の名前が出るまで、ws-042 は調べられない
    e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))
    outs = [e.decide(Decision(kind="action", action_id=a)) for a in order]
    return e, outs


def test_volatile_first_keeps_everything(scenario):
    """揮発性を先に取れば、後で端末を止めても手元の証拠は消えない。

    取得済みのメモリダンプは、端末を停止しても消えない。
    「見たことを忘れる」挙動にしてはいけない。
    """
    e, outs = _play_order(
        scenario, ["act_memory_dump_ws042", "act_disk_image_ws042"]
    )
    assert outs[0].revealed == ["ev_009"]
    assert outs[1].revealed == ["ev_014"]
    assert "ev_009" in e.state.obtained_evidence
    assert "q_persistence" in e.state.resolved_questions
    assert e.state.lost_evidence == []


def test_destroying_first_loses_it_for_good(scenario):
    """先に止めてしまうと、その情報は二度と取れない（順序の問題）。"""
    e, outs = _play_order(
        scenario, ["act_disk_image_ws042", "act_memory_dump_ws042"]
    )
    assert outs[0].revealed == ["ev_014"]
    assert outs[1].empty            # 45分かけて何も出てこない
    assert "ev_009" not in e.state.obtained_evidence
    assert "q_persistence" not in e.state.resolved_questions
    assert e.state.lost_evidence == ["ev_009"]


def test_every_action_returns_something(scenario):
    """取る必要のない情報は存在しない（SPEC 8.1）。

    実務でやるべき調査を「押しても何も出ない罠」にすると、
    探索そのものを避けることを教えてしまう（SPEC 6.6）。
    """
    for a in scenario.actions:
        if a.type is ActionType.INVESTIGATE:
            assert a.yields, f"{a.id}: 何も返さない調査アクションになっている"


def test_orthogonal_findings_exist(scenario):
    """情報は得られるが、いま決めたいことには効かない証拠を用意する。

    どの資産も指さず、どの論点も解消せず、どの誤導も棄却しない。
    時間だけが確実に減る。これが「全部やる」への圧力になる。
    """
    from irdojo.schema import bearing_evidence

    bearing = {e.id for e in bearing_evidence(scenario)}
    orthogonal = [e for e in scenario.evidence if e.id not in bearing]
    assert 3 <= len(orthogonal) <= 7
    # 中身は空にしない。ネガティブ所見も情報である
    for e in orthogonal:
        assert e.content.strip() and e.summary.strip() and e.reading.strip()


def test_complexity_ignores_orthogonal_findings(scenario):
    """ネガティブ所見は読む量を増やすが、推論を難しくはしない。"""
    from irdojo.schema import bearing_evidence, compute_complexity

    assert len(bearing_evidence(scenario)) < len(scenario.evidence)
    assert compute_complexity(scenario) == 2


def test_action_labels_carry_no_parenthetical_hints(scenario):
    """destroys の注記を label に書かない（SPEC 7.4）。

    「（揮発性証拠は失われます）」のような補足は、システムが自分の罠に
    印を付ける行為であり、原則2 に反する。動詞で言えるはずである。
    """
    NG = ("失われ", "消えま", "できなくなり", "停止します")
    for a in scenario.actions:
        for word in NG:
            assert word not in a.label, f"{a.id}: label に注記が書かれている"


# ── 結果のフィードバック（押した後に分かる） ───────────────────────


def test_loss_is_never_announced_during_play(scenario):
    """取り損ねたことはプレイ中に告げない。

    見ていないものが失われたと言うのは、
    「あなたが取り損ねた証拠がある」と教えることであり、中身が漏れる。
    結果は、後でその調査を実行したときの空振りで現れる。
    """
    e, outs = _play_order(scenario, ["act_disk_image_ws042"])
    assert not hasattr(outs[0], "lost")
    assert e.state.lost_evidence == ["ev_009"]   # 状態には残る（講評で開示）


def test_destroyed_evidence_cannot_be_reacquired(scenario):
    """先に壊してから調べに行っても、もう出てこない。"""
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))
    e.decide(Decision(kind="action", action_id="act_disk_image_ws042"))
    out = e.decide(Decision(kind="action", action_id="act_memory_dump_ws042"))
    assert out.empty
    assert "ev_009" not in e.state.obtained_evidence


def test_orthogonal_action_costs_time_but_not_score(scenario):
    """効かない情報でも、返るものは返る。減るのは時間だけ。"""
    e = Engine(scenario, "business_continuity", None)
    out = e.decide(Decision(kind="action", action_id="act_edr_full_scan"))
    assert out.revealed == ["ev_017"]      # 空振りではない
    assert e.state.elapsed_minutes == 35   # 時間は確実に減る

    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    e.decide(Decision(kind="finish"))
    r = scoring.score(e.state, scenario)
    # 被疑判定にも論点にも効かない
    assert r.metrics["unresolved_questions"] == 1.0


# ── 手がかりの連鎖（SPEC 5.6 requires_evidence） ──────────────────


def test_actions_unlock_from_findings(scenario):
    """知らない資産は調べられない。手がかりが出て初めて選択肢になる。"""
    e = Engine(scenario, "damage_minimization", None)
    at_start = {a.id for a in e.available_actions()}
    # ブリーフィングは fs01 しか言っていないので、ws-042 はまだ視界にない
    assert "act_memory_dump_ws042" not in at_start
    assert "act_disk_image_ws042" not in at_start

    out = e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))
    assert "ev_001" in out.revealed          # ここで ws-042 の名前が出る
    assert "act_memory_dump_ws042" in out.unlocked
    assert "act_memory_dump_ws042" in {a.id for a in e.available_actions()}


def test_misleading_evidence_also_opens_doors(scenario):
    """誤導も扉を開ける。開いたこと自体は「調べる価値がある」の証明にならない。"""
    e = Engine(scenario, "damage_minimization", None)
    assert "act_dlp_review" not in {a.id for a in e.available_actions()}
    out = e.decide(Decision(kind="action", action_id="act_netflow_overview"))
    assert "ev_007" in out.revealed          # 誤導証拠
    # 誤導が指す ws-107 を追う手段と、それを棄却する手段が同時に開く。
    # 扉が開いたことは「調べる価値がある」の証明にはならない
    assert "act_dlp_review" in out.unlocked
    assert "act_backup_config" in out.unlocked


def test_locked_actions_are_hidden_not_greyed(scenario):
    """未解放は一覧に出さない。灰色で見せると未知の資産の存在が漏れる。"""
    e = Engine(scenario, "damage_minimization", None)
    ids = {a.id for a in e.view().available_actions}
    # 手がかりが無いので、一覧に出てこない
    assert "act_interview_sato" not in ids
    assert "act_triage_ws113" not in ids
    with pytest.raises(InvalidDecision, match="手がかり"):
        e.decide(Decision(kind="action", action_id="act_interview_sato"))


def test_containment_is_never_gated(scenario):
    """封じ込めは手がかりで縛らない。証拠なしに止められることが前提（原則2）。"""
    for a in scenario.actions:
        if a.type is ActionType.CONTAIN:
            assert a.requires_evidence == []
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=[]))
    # 何も調べていなくても基幹資産を止められる。当否は方針の制約が測る
    e.decide(Decision(kind="action", action_id="act_shutdown_fs01"))
    assert len(e.state.violations_by_policy["business_continuity"]) == 1


# ── 封じ込めの結果（SPEC 7.4） ────────────────────────────────


def test_containment_reports_what_it_stopped(scenario):
    """封じ込めは押しても無言にしない。依存で波及した分まで返す。"""
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    out = e.decide(Decision(kind="action", action_id="act_shutdown_dc01"))

    assert not out.empty                    # 「何も出てこなかった」にならない
    assert set(out.contained) == {"dc01", "fs01", "ws-042", "ws-107", "ws-113"}
    assert set(out.cascaded) == {"fs01", "ws-042", "ws-107", "ws-113"}
    assert out.business_impact_delta > 0
    assert out.revealed == []               # 証拠は産まない


def test_containment_does_not_reveal_whether_it_was_correct(scenario):
    """減衰の判定は ground_truth に基づくので、それが漏れる値は返さない。

    侵害資産を止めても、無関係な資産を止めても、返ってくる形は同じでなければ
    「当たり」が分かってしまう。
    """
    def stop(action_id):
        e = Engine(scenario, "damage_minimization", None)
        e.decide(Decision(kind="declare_assessment", assessment=[]))
        return e.decide(Decision(kind="action", action_id=action_id))

    hit = stop("act_isolate_ws042")    # 侵害されている
    miss = stop("act_isolate_ws107")   # 無関係

    assert set(hit.model_dump()) == set(miss.model_dump())
    for f in ("damage_rate", "containment_effect", "correct"):
        assert f not in hit.model_dump()
    assert len(hit.contained) == len(miss.contained) == 1
    assert bool(hit.cascaded) == bool(miss.cascaded)


# ── 時間経過で入ってくること ────────────────────────────────


def test_timeline_fires_while_you_work(scenario):
    """押さなくても世界は進む。長いアクションなら複数まとめて起きる。"""
    e = Engine(scenario, "damage_minimization", None)
    out = e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))  # 0→30分
    assert out.events == []                       # まだ何も来ない

    e.decide(Decision(kind="action", action_id="act_netflow_overview"))         # 30→50分
    e.decide(Decision(kind="action", action_id="act_memory_dump_ws042"))        # 50→95分
    assert e.state.fired_events == []             # ここまでは何も起きない

    out = e.decide(Decision(kind="action", action_id="act_disk_image_ws042"))   # 95→155分
    assert out.events == ["tl_accel"]             # 145分の分が拾われた


def test_timeline_takes_something_away(scenario):
    """出来事は必ず何かを奪うか、費用の変化を告げる（5.10）。

    盤面を何も変えない出来事は、赤い枠を付けた飾りでしかない。
    ローダが拒否するが、実際に効いているかはここで確かめる。
    """
    for ev in scenario.timeline:
        assert ev.destroys or ev.heralds, f"{ev.id}: 盤面を何も変えない"

    # 200分を跨いだのに ev_010 を持っていなければ、もう取れない
    e = Engine(scenario, "damage_minimization", None)
    for aid in ("act_edr_full_scan", "act_backup_integrity", "act_smb_session_fs01",
                "act_ad_group_audit", "act_dc_authlog", "act_asset_inventory"):
        e.decide(Decision(kind="action", action_id=aid))          # 0→165分
    assert "ev_010" not in e.state.lost_evidence

    out = e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))  # 165→195分
    assert out.events == []
    out = e.decide(Decision(kind="action", action_id="act_netflow_overview"))   # 195→215分
    assert out.events == ["tl_fs01_restart"]
    assert e.state.lost_evidence == ["ev_010"]

    # 取りに行っても、もう出てこない
    out = e.decide(Decision(kind="action", action_id="act_memory_dump_fs01"))
    assert out.empty


def test_timeline_never_carries_answers(scenario):
    """向こうから来るものが答えを運ぶと、待つのが得になる。"""
    ids = {e.id for e in scenario.evidence}
    assets = {a.id for a in scenario.world.assets}
    for ev in scenario.timeline:
        for eid in ids:
            assert eid not in ev.text, f"{ev.id}: 証拠を運んでいる"
        # 奪う相手も本文では名指ししない。「何を失ったか」は講評で初めて言う
        for eid in ev.destroys:
            assert eid not in ev.text, f"{ev.id}: 奪うものを本文で名指ししている"
        # ブリーフィングに出ている fs01 以外の資産を名指ししない
        for a in assets - {"fs01"}:
            assert a not in ev.text, f"{ev.id}: {a} を名指ししている"


def test_empty_counts_only_the_action(scenario):
    """空振りと「その間に電話が鳴った」は別のこと。"""
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))       #  0→ 30分
    e.decide(Decision(kind="action", action_id="act_disk_image_ws042"))        # 30→ 90分
    out = e.decide(Decision(kind="action", action_id="act_memory_dump_ws042")) # 90→135分
    assert out.empty                              # 先に壊したので空振り
    assert out.events == []
    out = e.decide(Decision(kind="action", action_id="act_smb_session_fs01"))  # 135→165分
    assert out.events == ["tl_accel"]             # だがその間に電話は鳴る


def test_bend_sits_just_past_a_good_play(scenario):
    """折れ点は「最大の見せ場」なのに、誰にも踏まれないことがある。

    かつて 8.2 手順7 は折れ点を全アクション合計コストの4〜5割に置けと
    書いていた。メニュー全体は巧いプレイの3〜5倍あるので、その4〜5割は
    必ず巧いプレイの先に落ちる。折れ点 270分に対し巧いプレイ 130分。
    被害モデルの主役が一度も画面に出ないまま終わっていた。
    """
    best, _ids, _exact = retrospective.minimal_path(scenario)
    bend = scenario.damage.acceleration.threshold_minutes
    assert best is not None
    assert 1.1 * best <= bend <= 1.3 * best, (
        f"折れ点 {bend}分 / 最短経路 {best}分 = {bend / best:.2f}倍"
    )


def test_exhaustive_play_crosses_the_bend_early(scenario):
    """全部押す側は中盤で折れ点を踏む。網羅は戦略ではない、を体で知る。"""
    e = Engine(scenario, "damage_minimization", None)
    bend = scenario.damage.acceleration.threshold_minutes
    crossed_at, total = None, 0
    while True:
        avail = [
            a for a in e.available_actions()
            if a.type == ActionType.INVESTIGATE and a.id not in e.state.executed_actions
        ]
        if not avail:
            break
        total += 1
        e.decide(Decision(kind="action", action_id=avail[0].id))
        if crossed_at is None and e.state.elapsed_minutes > bend:
            crossed_at = total
    assert crossed_at is not None, "全部押しても折れ点に届かない"
    assert crossed_at <= total / 2, f"{total}手中 {crossed_at}手目でようやく到達"


# ── 先手を打って、消えるのを止める ──────────────────────────


NOTICE = "act_preservation_notice"


def test_notice_prevents_only_what_has_not_happened(scenario):
    """周知が効くのは打ち終わった時刻から先だけ。

    書いている 15分の間に現場が動いてしまうことはあるし、
    既に起きたことは戻らない。
    """
    e = Engine(scenario, "damage_minimization", None)
    for aid in ("act_edr_full_scan", "act_backup_integrity", "act_smb_session_fs01",
                "act_ad_group_audit", "act_dc_authlog", "act_asset_inventory",
                "act_collect_evtx_fs01", "act_netflow_overview"):
        e.decide(Decision(kind="action", action_id=aid))
    assert e.state.elapsed_minutes > 200          # tl_fs01_restart は起きてしまった
    assert "ev_010" in e.state.lost_evidence

    out = e.decide(Decision(kind="action", action_id=NOTICE))   # 205→220分
    assert "tl_fs01_restart" not in out.prevented  # 起きた後の周知は効かない
    assert out.prevented == ["tl_field_reboot"]    # まだ起きていない分だけ
    assert not out.empty                           # 押しても無言にはならない

    # 210分の分は「周知を書いている 15分の間」に起きてしまった。
    # 効き始めるのは打ち終わってからなので、これは止められない
    assert out.events == ["tl_backup_roll"]
    assert out.averted == []
    # 取得済みなので失いはしない（見たことは忘れない）。以後取れなくなるだけ
    assert "ev_018" in e.state.destroyed_evidence
    assert "ev_018" not in e.state.lost_evidence


def test_averted_events_still_happen_but_take_nothing(scenario):
    """不発でも「何も起きなかった」にはしない。

    無言だと、打った手が効いたことが学習者に伝わらない。
    人は動くが、周知が届いていたので手が止まる。
    """
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="action", action_id=NOTICE))
    for aid in ("act_edr_full_scan", "act_backup_integrity", "act_smb_session_fs01",
                "act_ad_group_audit", "act_dc_authlog", "act_asset_inventory",
                "act_collect_evtx_fs01", "act_netflow_overview"):
        e.decide(Decision(kind="action", action_id=aid))

    assert "tl_fs01_restart" in e.state.fired_events   # 出来事そのものは起きる
    assert "tl_fs01_restart" in e.state.averted_events
    assert e.state.lost_evidence == []                # だが何も持っていかない

    by_id = {ev.id: ev for ev in scenario.timeline}
    for tid in e.state.averted_events:
        assert by_id[tid].averted_text, f"{tid}: 不発のときに聞こえることが無い"


def test_notice_is_a_bet_on_your_own_pace(scenario):
    """速ければ払い損、遅ければ助かる。どちらになるかは事前に分からない。"""
    def play(order, notice):
        e = Engine(scenario, "damage_minimization", None)
        if notice:
            e.decide(Decision(kind="action", action_id=NOTICE))
        for aid in order:
            try:
                e.decide(Decision(kind="action", action_id=aid))
            except InvalidDecision:
                pass
        e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "fs01"]))
        e.decide(Decision(kind="finish"))
        return e

    _mins, fast, _exact = retrospective.minimal_path(scenario)
    # 誤導を追って、揮発性を後回しにするプレイ
    slow = [
        "act_netflow_overview", "act_dc_authlog", "act_asset_inventory",
        "act_edr_full_scan", "act_backup_integrity", "act_ad_group_audit",
        "act_smb_session_fs01", "act_collect_evtx_fs01", "act_dlp_review",
        "act_proxy_log", "act_memory_dump_ws042",
    ]
    pol = scenario.policy_by_id["evidence_preservation"]

    def pts(order, notice):
        return scoring.score(play(order, notice).state, scenario, pol).composite_score * 100

    # 速いプレイは世界に追いつかれない。保険はただの出費
    assert play(fast, False).state.lost_evidence == []
    assert pts(fast, True) <= pts(fast, False)

    # 遅いプレイは奪われる。保険が効く
    assert play(slow, False).state.lost_evidence
    assert pts(slow, True) - pts(slow, False) >= 3


def test_notice_is_right_or_wrong_depending_on_the_policy(scenario):
    """全社に「触るな」と流すことは、業務を止めることでもある。"""
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id=NOTICE))
    assert e.state.violations_by_policy["business_continuity"]
    assert not e.state.violations_by_policy["evidence_preservation"]
    assert not e.state.violations_by_policy["damage_minimization"]
