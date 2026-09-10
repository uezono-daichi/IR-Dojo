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


def play_spec_68(scenario, contain_first="act_shutdown_fs01",
                 second="act_isolate_ws042"):
    e = Engine(scenario, "business_continuity", None)
    for aid, _ in INVESTIGATION:
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "ws-107"]))
    e.decide(Decision(kind="action", action_id=contain_first))
    e.decide(Decision(kind="action", action_id=second))
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
    # 被害は二段。演習中に積み上がった分と、復旧地平 H の分（SPEC 5.8 / 6.3）
    assert c.accumulated_damage == pytest.approx(1383, abs=1)
    # このプレイは compromised を両方止めたが、**永続化は取り除いていない。**
    # 完全度 1.000 と引き換えに、地平の 480分は on_partial (0.6) で
    # 積分され続ける。「隔離しても、端末を戻せば攻撃者も戻ってくる」は
    # ここで初めて数字になる（v1.37 / 9.4 #5）
    assert c.projected_damage == pytest.approx(12973, abs=1)
    assert c.total_damage == pytest.approx(14356, abs=1)
    assert c.business_impact == pytest.approx(424.33, abs=0.01)
    assert c.evidence_preserved == pytest.approx(0.947, abs=0.001)
    assert c.containment_completeness == pytest.approx(1.000, abs=0.001)


def test_completeness_is_not_the_same_question_as_eradication(scenario):
    """完全度 1.000 は「もう戻ってこない」を意味しない（SPEC 5.8 / 6.3）。

    `containment_completeness` が測るのは「攻撃の経路を断てたか」で、
    5.8 の減衰はそれに加えて「居座られた状態を消せたか」を見る。
    2つを1つの集合で判定していた頃、後半の条項は恒真だった（9.4 #5）。

    同じ判定・同じ調査のまま、ws-042 の隔離を「元の状態に戻す」手に
    置き換えるだけで、地平の積分係数が 0.6 から 0.2 に変わる。
    """
    partial = scoring.score(play_spec_68(scenario).state, scenario).consequences
    full = scoring.score(
        play_spec_68(scenario, second="act_purge_persistence_ws042").state, scenario
    ).consequences

    # どちらも「侵害された2資産を直接止めた」ので完全度は同じ
    assert partial.containment_completeness == full.containment_completeness == 1.0
    # それでも被害は倍以上違う。違いは根絶したかどうかだけ
    assert partial.total_damage > full.total_damage * 2


def test_fact_metrics(scenario):
    r = scoring.score(play_spec_68(scenario).state, scenario)
    assert r.metrics["assessment_precision"] == pytest.approx(0.500, abs=0.001)
    assert r.metrics["assessment_recall"] == pytest.approx(0.500, abs=0.001)
    # ws-042 は ev_006 / ev_009 / ev_011 が指す。ws-107 を指すのは誤導の
    # ev_007 だけなので裏付けにならない
    assert r.metrics["assessment_support"] == pytest.approx(0.500, abs=0.001)
    assert r.metrics["unresolved_questions"] == pytest.approx(0.333, abs=0.001)
    assert r.fact_score == pytest.approx(0.567, abs=0.001)


def test_composite(scenario):
    r = scoring.score(play_spec_68(scenario).state, scenario)
    assert r.policy_score == pytest.approx(0.554, abs=0.001)
    assert r.composite_score == pytest.approx(0.563, abs=0.001)
    assert round(r.composite_score * 100) == 56


def test_dependency_chain_variant(scenario):
    """dc01 を止めると業務は全部止まるが、封じ込めたのは dc01 だけ。

    依存連鎖が答えているのは「どの資産で仕事ができなくなるか」であって
    「どの資産で攻撃が止まるか」ではない（SPEC 6.3）。dc01 の電源を
    落としても fs01 上の暗号化プロセスは動き続ける。
    """
    e = play_spec_68(scenario, contain_first="act_shutdown_dc01")
    # 封じ込めは直接止めた分だけ。fs01 は入らない
    assert e.state.contained_at == {"dc01": 180, "ws-042": 190}
    # 業務は依存で全部止まる。ws-042 は 190分の隔離より早い 180分に繰り上がる
    assert e.state.halted_at == {
        "dc01": 180, "fs01": 180, "ws-042": 180, "ws-107": 180, "ws-113": 180,
    }
    r = scoring.score(e.state, scenario)
    assert r.consequences.business_impact == pytest.approx(1127.00, abs=0.01)
    assert r.consequences.evidence_preserved == pytest.approx(1.000, abs=0.001)
    # fs01 が封じ込められていないので完全度は 0.5、減衰も on_partial 止まり
    assert r.consequences.containment_completeness == pytest.approx(0.500, abs=0.001)
    assert r.policy_score == pytest.approx(0.301, abs=0.001)
    assert round(r.composite_score * 100) == 49


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

    assert round(got["business_continuity"].composite_score * 100) == 56
    assert round(got["damage_minimization"].composite_score * 100) == 58
    assert round(got["evidence_preservation"].composite_score * 100) == 61

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
    """調査だけを入れ替えて、封じ込めは**正しく止めた形**で揃える。

    封じ込めに `act_rebuild_ws042` を使う（v1.37）。隔離だけでは
    `persistence` が残るので 5.8 は `on_partial` 止まりになり、
    **どちらのプレイも同じ 0.6 で積分される** — 比べたいのは調査量の差
    なのに、封じ込めの側で天井を揃えてしまうと帰結の差が潰れる。
    """
    e = Engine(scenario, policy or scenario.meta.default_policy, None)
    for aid in list(triage) + list(investigation):
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "fs01"]))
    for aid in ("act_rebuild_ws042", "act_shutdown_fs01"):
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
    # 止めたのは dc01 だけ。業務は依存で5資産すべてが止まる（SPEC 6.3）
    assert set(out.contained) == {"dc01"}
    assert set(out.halted) == {"dc01", "fs01", "ws-042", "ws-107", "ws-113"}
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
    e.decide(Decision(kind="action", action_id="act_disk_image_ws042"))         # 95→155分
    assert e.state.fired_events == []             # ここまでは何も起きない

    out = e.decide(Decision(kind="action", action_id="act_proxy_log"))          # 155→175分
    assert out.events == ["tl_accel"]             # 165分の分が拾われた


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
        e.decide(Decision(kind="action", action_id=aid))          # 0→155分
    assert "ev_010" not in e.state.lost_evidence

    out = e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))  # 155→185分
    assert out.events == ["tl_accel"]             # 165分の前触れが拾われる
    out = e.decide(Decision(kind="action", action_id="act_netflow_overview"))   # 185→205分
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

    次に、それを `minimal_path` の 1.1〜1.3倍に直したが、**まだずれていた。**
    `minimal_path` は判断に到達するまでで、封じ込めを含まない。
    被害モデルが罰したいのは判断の遅れではなく、**被害が止まるまで**の
    遅れである。折れ点 150分に対し、正しく止めた巧いプレイは 155分 —
    誰よりも巧く解いた人が、自分で折れ点を踏んでいた。
    """
    judged, _ids, _exact = retrospective.minimal_path(scenario)
    stopped, _stop_ids = retrospective.minimal_containment(scenario)
    assert judged is not None and stopped is not None
    best = judged + stopped
    bend = scenario.damage.acceleration.threshold_minutes
    assert 1.1 * best <= bend <= 1.3 * best, (
        f"折れ点 {bend}分 / L {best}分"
        f"（判断まで {judged}分＋最安の封じ込め {stopped}分）= {bend / best:.2f}倍"
    )
    # 巧いプレイが自分で折れ点を踏まないこと。ここが本体
    assert best <= bend


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


# ── 復旧地平（SPEC 5.8 / 6.3） ──────────────────────────────


def _consequences_after(scenario, contain):
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["fs01", "ws-042"]))
    for aid in contain:
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="finish"))
    return scoring.consequences(e.state, scenario)


def test_stopping_nothing_is_never_the_cheapest_way_out(scenario):
    """「何も止めない」が対応フェーズで最も安い手になっていないこと。

    被害は経過時間の積分なので、演習の終わりで積分を打ち切ると
    **動かなかったプレイの被害はほぼ 0 になる。** 15分で降りれば被害 89、
    封じ込めに30分使えば被害はその何倍にもなる — 盤面がそう答えていた。

    演習は終わってもインシデントは終わらない。復旧地平まで積分すると
    順序が入れ替わる。`containment_effect`（0.2 / 0.6 / 1.0）が
    実際に効くのはこの区間である。
    """
    stopped = _consequences_after(
        scenario, ["act_shutdown_fs01", "act_rebuild_ws042"]
    )
    nothing = _consequences_after(scenario, [])

    # 演習中だけを見れば、動かないほうが安い。ここは変えていない
    assert nothing.accumulated_damage < stopped.accumulated_damage
    # 地平まで見ると逆転する。しかも僅差ではない
    assert nothing.total_damage > stopped.total_damage * 2


def test_the_recovery_horizon_is_one_assumption_not_two(scenario):
    """復旧までの前提は1つしかない。被害と業務影響が同じ H を使う。

    独立したフィールドを2つ置くと、「被害は早く収まるが業務は長く止まる」
    のような、盤面のどこにも根拠のない組み合わせを作者が書けてしまう。
    H は「封じ込めた資産は復旧までこれだけ止まる」という前提そのもので、
    それは1つの世界に1つしかない。
    """
    short = scenario.model_copy(deep=True)
    short.scoring.consequence_layer.business_impact_horizon_minutes = 60

    long_h = _consequences_after(scenario, ["act_block_c2", "act_shutdown_fs01"])
    short_h = _consequences_after(short, ["act_block_c2", "act_shutdown_fs01"])

    assert short_h.projected_damage < long_h.projected_damage
    assert short_h.business_impact < long_h.business_impact


def test_the_projection_hangs_on_what_was_stopped(scenario):
    """地平の被害は、手を止めた時点の封じ込め状態が決める。

    ここが封じ込めに反応しないなら、対応フェーズの中核パラメータは
    演習終了までのわずかな残り時間にしか掛からず、盤面上ほぼ効かない。

    **3段ある**（SPEC 5.8 / v1.37）。「全部止めて根絶した」「止めたが
    根絶していない」「見当違いを止めた」は、地平の傾きが3つとも違う。
    根絶を入れる前は上2つが同じ 0.2 で、真ん中の段が存在しなかった。
    """
    correct = _consequences_after(
        scenario, ["act_shutdown_fs01", "act_rebuild_ws042"]
    )
    # 侵害資産は両方止めたが、永続化は残ったまま
    stopped_only = _consequences_after(
        scenario, ["act_block_c2", "act_shutdown_fs01"]
    )
    # 片方しか止めていない
    partial = _consequences_after(scenario, ["act_block_c2"])
    wrong = _consequences_after(scenario, ["act_isolate_ws107"])

    # 根絶まで行った側は **45分よけいに払っている**（地平の始点が遅く、
    # そのぶん被害率は高い）のに、それでも地平の積分は小さい
    assert correct.elapsed_minutes > stopped_only.elapsed_minutes
    assert correct.projected_damage < stopped_only.projected_damage

    # 経過が同じ 10分の2本で、係数だけを比べる。
    # 「片方だけ止めた」と「見当違いを止めた」は on_partial と on_incorrect
    assert partial.elapsed_minutes == wrong.elapsed_minutes
    assert partial.projected_damage < wrong.projected_damage


def test_stopping_something_already_stopped_is_not_a_blank(scenario):
    """既に止めてある資産への2手目は、空振りではない（SPEC 7.6.8）。

    同じ資産に手が2つあるので（境界で遮断する／電源を落とす）、
    これは事故ではなく普通に起きる。`contained` も `halted` も空になるため、
    数えないと緑の枠の中にオレンジの「何も出てこなかった」が並ぶ。
    """
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["fs01"]))

    first = e.decide(Decision(kind="action", action_id="act_block_smb_fs01"))
    assert first.contained == ["fs01"]
    assert first.already == []
    assert first.halted == []            # サーバは動き続ける
    assert not first.empty

    second = e.decide(Decision(kind="action", action_id="act_shutdown_fs01"))
    assert second.contained == []        # 攻撃は既に止めてある
    assert second.already == ["fs01"]
    assert second.halted == ["fs01"]     # だが今度は業務が止まる
    assert not second.empty


def test_evidence_band_covers_only_what_can_be_lost(scenario):
    """証拠保全の正規化の帯は、盤面で失いうる範囲と一致していること。

    帯が [0,1] だった頃、失いうる証拠は 3/19 しかなかったので、
    どのプレイも 0.842〜1.000 の中に居た。帯の 84% は誰も踏まない。
    その結果「証拠保全最優先」を選んで証拠を3つ落としても
    合成点は2点しか動かず、方針が名前にしている量が採点に現れなかった。

    壊れ方: destroys を1つ足して worst を直し忘れると、
    その分だけ帯が余り、証拠保全の重みが静かに薄まる。
    """
    losable = {e for a in scenario.actions for e in a.destroys}
    losable |= {e for t in scenario.timeline for e in t.destroys}
    floor = 1.0 - len(losable) / len(scenario.evidence)
    bounds = scenario.scoring.consequence_layer.normalization["evidence_preserved"]
    assert bounds.best == 1.0
    assert bounds.worst == pytest.approx(floor, abs=0.01)


def test_losing_everything_losable_reaches_the_bottom_of_the_band(scenario):
    """失いうるものを全部失ったプレイは、帯の底（0）に着く。

    上のテストが帯の端を見るのに対し、こちらは採点が実際に
    その端まで動くことを見る。0.9 台で止まるなら帯が広すぎる。
    """
    st = play_spec_68(scenario).state
    st.lost_evidence = sorted(
        {e for a in scenario.actions for e in a.destroys}
        | {e for t in scenario.timeline for e in t.destroys}
    )
    r = scoring.score(st, scenario)
    assert r.consequence_goodness["evidence_preserved"] == pytest.approx(0.0, abs=0.02)

    st.lost_evidence = []
    r = scoring.score(st, scenario)
    assert r.consequence_goodness["evidence_preserved"] == pytest.approx(1.0, abs=0.001)


def _play_until(scenario, minutes, skip):
    """指定の分を越えるまで、調査を安い順に押す。"""
    e = Engine(scenario, "damage_minimization", None)
    acts = sorted(
        (a for a in scenario.actions
         if a.type == ActionType.INVESTIGATE and a.id not in skip),
        key=lambda a: a.cost_minutes,
    )
    for a in acts:
        if e.state.elapsed_minutes >= minutes:
            break
        try:
            e.decide(Decision(kind="action", action_id=a.id))
        except InvalidDecision:
            continue
    return e


def test_a_wasted_move_is_tied_to_what_it_was_looking_for(scenario):
    """空振りした手と、取り損ねた証拠を、講評で結ぶ。

    学習者の記憶に残っているのは「30分払って何も出てこなかった手」であって、
    「1時間25分前に世界に奪われていた」ではない。この2つを結ばないと、
    講評は本人が体験した出来事を一度も説明しないまま終わる。

    壊れ方: 破壊より**前**に押した手を結んでしまうと、
    間に合った手を「遅かった」と責めることになる。
    """
    from irdojo.report import json as report_json

    e = _play_until(scenario, 240, skip={"act_backup_integrity"})
    gone_at = e.state.destroyed_at.get("ev_018")
    assert gone_at is not None, "ev_018 が世界に奪われる前に打ち切っている"
    pressed_at = e.state.elapsed_minutes
    e.decide(Decision(kind="action", action_id="act_backup_integrity"))
    e.decide(Decision(kind="declare_assessment", assessment=["fs01"]))
    e.decide(Decision(kind="finish"))

    rep = report_json.build(e.state, scenario, persist=False)
    lost = {l.id: l for l in rep.lost_evidence}
    assert "ev_018" in lost
    gather = [t for t in lost["ev_018"].too_late if t.kind == "gather"]
    assert len(gather) == 1
    assert gather[0].action_label == "バックアップの健全性を確認"
    assert gather[0].at_minute == pressed_at
    assert gather[0].late_by_minutes == pressed_at - gone_at
    assert gather[0].cost_minutes == 30


def test_a_move_made_in_time_is_not_called_late(scenario):
    """間に合った手は結ばない。取れた証拠はそもそも失われていない。"""
    from irdojo.report import json as report_json

    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="action", action_id="act_backup_integrity"))
    e.decide(Decision(kind="declare_assessment", assessment=["fs01"]))
    e.decide(Decision(kind="finish"))
    rep = report_json.build(e.state, scenario, persist=False)
    assert all(l.id != "ev_018" for l in rep.lost_evidence)


def test_the_notice_that_arrived_too_late_says_so(scenario):
    """遅れて出した周知が、なぜ効かなかったのかを講評で言う。

    プレイ中は「何も出てこなかった」としか出せない（原則5: 損失を
    プレイ中に告げない）。効かなかった理由を言える場所はここしかない。
    """
    from irdojo.report import json as report_json

    e = _play_until(scenario, 240, skip={"act_backup_integrity"})
    e.decide(Decision(kind="action", action_id="act_preservation_notice"))
    e.decide(Decision(kind="declare_assessment", assessment=["fs01"]))
    e.decide(Decision(kind="finish"))

    rep = report_json.build(e.state, scenario, persist=False)
    lost = {l.id: l for l in rep.lost_evidence}
    notices = [t for t in lost["ev_018"].too_late if t.kind == "notice"]
    assert len(notices) == 1
    assert notices[0].action_label == "全社に証拠保全の周知を出す"
    assert notices[0].late_by_minutes > 0


def test_the_policy_breakdown_adds_up_to_the_policy_score(scenario):
    """④ に出す内訳は、実際の採点式そのものであること。

    「方針違反はありませんでした」と「方針適合 39点」が同居していたとき、
    61点がどこへ消えたかは講評のどこにも書かれていなかった。
    説明のために別の式を書くと、説明と採点がいずれ食い違う。
    """
    from irdojo.report import json as report_json

    st = play_spec_68(scenario).state
    rep = report_json.build(st, scenario, persist=False)
    total = sum(t.contribution for t in rep.policy_terms)
    penalty = 1.0 - rep.score.constraint_penalty * rep.score.constraint_violations
    assert total * penalty == pytest.approx(rep.score.policy_score, abs=0.001)
    # 重みは方針のものがそのまま出ていること（別の重みで説明していない）
    weights = {t.key: t.weight for t in rep.policy_terms}
    assert weights == scenario.policy_by_id[rep.score.policy_id].weights


def test_chart_markers_are_numbered_in_time_order(scenario):
    """図と凡例は番号で結ぶ。番号が重複したら対応が取れなくなる。

    同時刻に2本立つことがある（依存の根と端末を同じ分に止めた場合）。
    位置の近さで対応を推測させると、その時点で読めなくなる。
    """
    from irdojo.report import json as report_json

    rep = report_json.build(play_spec_68(scenario).state, scenario, persist=False)
    idx = [m.index for m in rep.markers]
    assert idx == list(range(1, len(rep.markers) + 1))
    minutes = [m.minute for m in rep.markers]
    assert minutes == sorted(minutes)


def test_a_notice_that_arrives_too_late_is_not_reported_as_a_dead_end(scenario):
    """間に合わなかった周知を「何も出てこなかった」にしない（原則5 / SPEC 5.6.1）。

    連絡は「自分の速さへの賭け」で、押す時点では勝ち負けが分からない
    ——というのが 5.6.1 の主張である。ところが `prevents` の対象が全部
    起きてしまった後に押すと `prevented` が空になり、空振り判定が立って
    画面に「何も出てこなかった。」が出ていた。

    **それはその場で賭けの結果を告げることである。** 何を失ったかは
    言っていないが、「間に合わなかった」ことは言っている。
    速いプレイと遅いプレイで文言が変わってはいけない。

    周知そのものは出ている。世界の側で何が残っていたかは講評の仕事。
    """
    e = Engine(scenario, "damage_minimization", None)
    # 防げる出来事を全部通り過ぎるまで押す
    for a in scenario.actions:
        if a.type.value != "investigate":
            continue
        try:
            e.decide(Decision(kind="action", action_id=a.id))
        except InvalidDecision:
            continue
    preventable = {t for a in scenario.actions for t in a.prevents}
    assert preventable <= set(e.state.fired_events), "まだ防げる出来事が残っている"

    out = e.decide(Decision(kind="action", action_id=NOTICE))
    assert out.prevented == []      # もう防ぐものが残っていない
    assert not out.empty            # それでも空振りではない
    assert out.kind.value == "communicate"
