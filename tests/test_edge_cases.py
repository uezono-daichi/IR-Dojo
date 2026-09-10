"""SPEC 6.7 の端値と、ローダが拒否すべき構造。"""

import copy

import pytest
import yaml

from irdojo import scoring
from irdojo.damage import expand_containment
from irdojo.engine import AssessmentRequired, Decision, Engine, InvalidDecision
from irdojo.loader import SCENARIO_DIR, ScenarioError, load_scenario_text
from irdojo.schema import compute_complexity


@pytest.fixture
def raw():
    path = SCENARIO_DIR / "ransomware-initial-response-01.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build(data):
    return load_scenario_text(yaml.safe_dump(data, allow_unicode=True))


# ─────────── 端値（SPEC 6.7） ───────────


def test_empty_assessment_is_the_worst_on_every_fact_metric(scenario):
    """判断放棄は、事実認識層のどの指標でも救われない。

    v1.35 まで misled_score は空判定を「誤導に引っかかっていない」と読んで
    0（＝良い側）にしていた。判断を放棄した人が、誤導を1つ混ぜた人より
    誤導耐性で上に立つ形になっていた。SPEC 6.7 の本則は
    「判断を放棄することは、誤った判断より良くはない」であって、
    そこに特例を置く理由は無い。
    """
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id="act_netflow_overview"))
    e.decide(Decision(kind="declare_assessment", assessment=[]))
    e.decide(Decision(kind="finish"))

    r = scoring.score(e.state, scenario)
    assert r.metrics["assessment_precision"] == 0.0
    assert r.metrics["assessment_recall"] == 0.0
    # higher_is_better なので safe_ratio の既定 0.0 がそのまま最悪になる
    assert r.metrics["assessment_support"] == 0.0
    assert r.goodness["assessment_support"] == 0.0


def test_zero_investigation_time_leaves_nothing_to_stand_on(scenario):
    """1分も調べずに名指しした資産は、当たっていても裏付けを持たない。

    fs01 はブリーフィングが名指ししている資産なので、当てること自体は
    ただである（precision も recall も動く）。動かないのは裏付けの側で、
    「言われたから書いた」と「見たから書いた」がここで初めて分かれる。
    """
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=["fs01"]))
    e.decide(Decision(kind="finish"))
    r = scoring.score(e.state, scenario)
    assert e.state.investigation_minutes == 0
    assert r.metrics["assessment_precision"] == 1.0   # fs01 は実際に侵害されている
    assert r.metrics["assessment_support"] == 0.0     # 手元には何も無い
    assert r.metrics["unresolved_questions"] == 1.0   # 何も解消していない


def test_support_counts_what_was_in_hand_when_the_call_was_made(scenario):
    """裏付けは**宣言した瞬間**の手元で数える。あとから取り直しても遡らない。

    被疑判定は対応フェーズ中も再宣言できる（SPEC 3.6）。基準点を
    「最後の状態」に置くと、証拠を1つも持たずに名指しした人が
    封じ込めの片手間に裏付けを拾って満点になれてしまう。
    unresolved_questions と同じく、判断を下した瞬間だけを見る。
    """
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    r = scoring.score(e.state, scenario)
    assert r.metrics["assessment_support"] == 0.0

    # 対応フェーズでは調査アクションを押せないので、スナップショットを
    # 直接進めて「あとから手に入れた」状態だけを作る
    snap = e.state.assessment_snapshot
    assert snap is not None and snap.obtained_evidence == []
    ws042_evidence = [
        ev.id for ev in scenario.evidence
        if "ws-042" in ev.points_to and not ev.misleading
    ]
    e.state.obtained_evidence = list(ws042_evidence)
    assert scoring.score(e.state, scenario).metrics["assessment_support"] == 0.0

    # 宣言時点で持っていれば、同じ証拠がそのまま裏付けになる
    snap.obtained_evidence = list(ws042_evidence)
    assert scoring.score(e.state, scenario).metrics["assessment_support"] == 1.0


def test_support_does_not_accept_misleading_evidence(scenario):
    """誤導証拠しか指していない資産は、裏付けを持たないものとして数える。

    misled_score を廃止しても誤導の教育価値は落とさない。
    ws-107 を名指しした人は適合率で1回、裏付けで もう1回落ちる。
    「何も持たずに当てた」と「誤導だけを持って外した」が同じ穴に落ちるのは
    意図どおりで、どちらも根拠を持たない判断である。
    """
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id="act_netflow_overview"))  # ev_007（誤導）
    e.decide(Decision(kind="declare_assessment", assessment=["ws-107"]))
    e.decide(Decision(kind="finish"))
    snap = e.state.assessment_snapshot
    pointing = [
        ev for ev in scenario.evidence
        if ev.id in snap.obtained_evidence and "ws-107" in ev.points_to
    ]
    assert pointing and all(ev.misleading for ev in pointing)
    assert scoring.score(e.state, scenario).metrics["assessment_support"] == 0.0


def test_no_containment(scenario):
    """封じ込めを一度もしなければ完全性 0、被害も減衰しない。"""
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    e.decide(Decision(kind="finish"))
    c = scoring.score(e.state, scenario).consequences
    assert c.containment_completeness == 0.0
    assert c.business_impact == 0.0




def test_cannot_finish_without_declaring_assessment(scenario):
    """被疑判定なしの終了は起こらない（SPEC 6.7 の「✕」）。"""
    e = Engine(scenario, "business_continuity", None)
    with pytest.raises(AssessmentRequired):
        e.decide(Decision(kind="finish"))


def test_cannot_advance_into_assessment_phase_without_declaring(scenario):
    """不可逆な移行は1つだけで、それは必ず被疑判定を伴う（SPEC 3.8）。"""
    e = Engine(scenario, "business_continuity", None)
    with pytest.raises(AssessmentRequired):
        e.decide(Decision(kind="advance_phase"))


def test_snapshot_is_frozen_at_declaration(scenario):
    """対応フェーズ中に論点を解消しても、遡っての加点はしない。"""
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id="act_collect_evtx_fs01"))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    before = scoring.score(e.state, scenario).metrics["unresolved_questions"]
    # 対応フェーズからでも前フェーズのアクションは実行できる（解放は累積）
    e.decide(Decision(kind="action", action_id="act_mail_gateway"))
    e.decide(Decision(kind="action", action_id="act_smb_session_fs01"))
    assert "q_initial_access" in e.state.resolved_questions
    e.decide(Decision(kind="finish"))
    assert scoring.score(e.state, scenario).metrics["unresolved_questions"] == before


def test_assessment_editable_but_snapshot_kept(scenario):
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    snap = e.state.assessment_snapshot.model_copy(deep=True)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "fs01"]))
    assert e.state.assessment == ["ws-042", "fs01"]
    assert e.state.assessment_snapshot == snap


# ─────────── ローダが拒否する構造 ───────────


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d["world"]["ground_truth"].__setitem__("compromised", []), "compromised が空"),
        (lambda d: d.__setitem__("policies", d["policies"][:1]), "最低2つ"),
        (lambda d: [q.__setitem__("critical", False) for q in d["open_questions"]], "critical"),
        (lambda d: [e.__setitem__("misleading", False) for e in d["evidence"]], "誤導証拠"),
        (lambda d: d["meta"].__setitem__("default_policy", "nope"), "default_policy"),
        (lambda d: d["evidence"][6].__setitem__("refuted_by", []), "棄却不能"),
        (lambda d: d["actions"][0].__setitem__("yields", ["ev_999"]), "未定義の証拠"),
        (lambda d: d["actions"][0].__setitem__("phase", "nope"), "未定義のフェーズ"),
        (lambda d: d["actions"][0].__setitem__("type", "communicate"), "communicate"),
        (lambda d: d["world"]["assets"][0].__setitem__("depends_on", ["fs01"]), "循環"),
        (lambda d: d["policies"][0]["weights"].__setitem__("total_damage", 0.9), "1.0 でない"),
        (lambda d: d["actions"][0].__setitem__("cost_minutes", 0), "greater than 0"),
    ],
)
def test_loader_rejects(raw, mutate, fragment):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert fragment in str(exc.value)


def test_loader_rejects_unknown_keys(raw):
    raw["meta"]["compromised_hint"] = "ws-042"
    with pytest.raises(ScenarioError) as exc:
        build(raw)
    assert "Extra inputs" in str(exc.value) or "extra" in str(exc.value).lower()


def test_loader_ignores_author_written_complexity(raw):
    raw["meta"]["complexity"] = 5
    with pytest.warns(UserWarning, match="complexity"):
        sc = build(raw)
    assert sc.complexity == 2  # 内容から算出した値が使われる


def test_loader_uses_safe_load_only():
    """任意コード実行になる YAML タグを拒否する（SPEC 7.7.1）。"""
    with pytest.raises(ScenarioError):
        load_scenario_text("!!python/object/apply:os.system ['echo pwned']")


def test_loader_rejects_path_traversal():
    from irdojo.loader import load_scenario

    for bad in ("../SPEC", "..%2Fetc%2Fpasswd", "a/b", "/etc/passwd", ""):
        with pytest.raises(ScenarioError):
            load_scenario(bad)


# ─────────── 個別の関数 ───────────


def test_expand_containment_takes_earliest_time():
    from irdojo.schema import Asset, Criticality

    assets = {
        a.id: a
        for a in [
            Asset(id="dc01", label="dc", criticality=Criticality.CRITICAL),
            Asset(id="fs01", label="fs", criticality=Criticality.HIGH, depends_on=["dc01"]),
            Asset(id="app", label="app", criticality=Criticality.LOW, depends_on=["fs01"]),
        ]
    }
    # 多段の連鎖。直接停止した app も、上流がより早ければその時刻に繰り上がる
    out = expand_containment({"dc01": 100, "app": 200}, assets)
    assert out == {"dc01": 100, "fs01": 100, "app": 100}


def test_complexity_matches_spec_table(raw):
    """SPEC 3.12 の想定表と一致するか。"""
    sc = build(raw)
    assert compute_complexity(sc) == 2

    data = copy.deepcopy(raw)
    for e in data["evidence"]:
        if e.get("misleading"):
            e["plausibility"] = "high"
    assert compute_complexity(build(data)) == 2

    # 誤導を増やすと上がる（作者が狙って設計できる）
    data = copy.deepcopy(raw)
    for e in data["evidence"]:
        if e["id"] == "ev_013":          # ネガティブ所見を誤導に仕立て直す
            e["misleading"] = True
            e["plausibility"] = "high"
            e["points_to"] = ["ws-113"]
            e["refuted_by"] = ["ev_008"]
    assert compute_complexity(build(data)) == 3


def test_repeated_action_rejected(scenario):
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id="act_asset_inventory"))
    with pytest.raises(InvalidDecision):
        e.decide(Decision(kind="action", action_id="act_asset_inventory"))


def test_action_from_later_phase_rejected(scenario):
    e = Engine(scenario, "business_continuity", None)
    with pytest.raises(InvalidDecision):
        e.decide(Decision(kind="action", action_id="act_mail_gateway"))


def test_evidence_preservation_constraint_fires(scenario):
    """揮発性証拠を取らずに封じ込めると、証拠保全最優先では違反になる。"""
    e = Engine(scenario, "evidence_preservation", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    e.decide(Decision(kind="action", action_id="act_isolate_ws042"))
    e.decide(Decision(kind="finish"))
    viol = e.state.violations_by_policy["evidence_preservation"]
    assert len(viol) == 1
    assert viol[0].constraint_type == "require_before"
    # 同じ行動でも業務継続最優先では違反にならない
    assert e.state.violations_by_policy["business_continuity"] == []


def test_loader_rejects_criticality_inversion(raw):
    """重要度と業務影響が逆転していたら拒否する（SPEC 3.1）。

    学習者には重要度しか見せないため、逆転していると表示が実態と食い違う。
    """
    data = copy.deepcopy(raw)
    for a in data["world"]["assets"]:
        if a["id"] == "ws-042":          # low なのに dc01 より高コストにする
            a["business_impact_per_hour"] = 999
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "逆転" in str(exc.value)


def test_criticality_is_display_only(scenario):
    """重要度は採点に一切効かない。効くのは business_impact_per_hour。"""
    from irdojo.schema import CRITICALITY_RANK

    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    e.decide(Decision(kind="action", action_id="act_shutdown_fs01"))
    e.decide(Decision(kind="finish"))
    before = scoring.score(e.state, scenario).composite_score

    # 重要度だけを一段上げても、順序が保たれる限りスコアは動かない
    for a in scenario.world.assets:
        if a.id == "ws-107":
            a.criticality = list(CRITICALITY_RANK)[1]
    assert scoring.score(e.state, scenario).composite_score == before


def test_same_second_plays_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """ファイル名の時刻は秒まで。同じ秒に終わった記録を消してはいけない。"""
    from datetime import datetime, timezone

    from irdojo import records as records_mod
    from irdojo.schema import AssistLevel

    monkeypatch.setenv("IRDOJO_HOME", str(tmp_path))
    at = datetime(2026, 3, 14, 2, 17, 0, tzinfo=timezone.utc)

    def rec(score):
        return records_mod.Record(
            scenario_id="s", scenario_title="s", scenario_version="1.0.0",
            policy_id="p", policy_label="p", assist_level=AssistLevel.ASSISTED,
            played_at=at, is_first_play=False,
            fact_score=score, policy_score=score, composite_score=score, metrics={},
            elapsed_minutes=1, total_damage=0.0, business_impact=0.0,
            evidence_preserved=1.0, containment_completeness=1.0,
            assessment=[], unresolved_at_decision=[], constraint_violations=0,
        )

    paths = [records_mod.save(rec(v)) for v in (0.1, 0.2, 0.3)]
    assert len({p.name for p in paths}) == 3
    got = records_mod.load_all("s")
    assert len(got) == 3
    assert sorted(r.composite_score for _n, r in got) == [0.1, 0.2, 0.3]


def test_loader_rejects_a_notice_that_prevents_nothing(raw):
    """何も防がない連絡は、時間を溶かすだけのボタンになる。"""
    data = copy.deepcopy(raw)
    data["actions"].append({
        "id": "act_chat", "label": "関係者に一報を入れる", "phase": "investigation",
        "type": "communicate", "cost_minutes": 10,
    })
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "prevents が必要" in str(exc.value)


def test_loader_rejects_a_notice_that_gathers_evidence(raw):
    """連絡で証拠が出てくるなら、それは調査である。"""
    data = copy.deepcopy(raw)
    data["actions"].append({
        "id": "act_chat", "label": "関係者に一報を入れる", "phase": "investigation",
        "type": "communicate", "cost_minutes": 10,
        "prevents": ["tl_field_reboot"], "yields": ["ev_001"],
    })
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "yields は使えない" in str(exc.value)


def test_loader_rejects_a_preventable_event_with_nothing_to_say(raw):
    """不発を無言で済ませると、打った手が効いたことが伝わらない。"""
    data = copy.deepcopy(raw)
    for ev in data["timeline"]:
        if ev["id"] == "tl_field_reboot":
            ev.pop("averted_text", None)
            ev.pop("averted_label", None)
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "averted_text が要る" in str(exc.value)


def _question(data, qid):
    return next(q for q in data["open_questions"] if q["id"] == qid)


def test_loader_rejects_a_question_that_names_a_compromised_asset(raw):
    """論点の問い文に答えの資産名を書いたら拒否する。

    これは同梱シナリオが実際に踏んだ形である。「ws-042 から fs01 へ
    どう到達したか」は、開始0分の画面で compromised と patient_zero を
    渡していた。論点は最初から出ているので、ここに書いたものは
    「調べれば分かること」ではなく「最初から配られたもの」になる。
    """
    for field, text in (
        ("question", "ws-042 から fs01 へどう到達したか"),
        ("label", "ws-042 の横展開"),
        ("implication", "ws-042 を止めないと同じ経路で他へ広がります。\n"),
    ):
        data = copy.deepcopy(raw)
        _question(data, "q_lateral_movement")[field] = text
        with pytest.raises(ScenarioError) as exc:
            build(data)
        assert "名指し" in str(exc.value), field
        assert "ws-042" in str(exc.value), field


def test_loader_rejects_the_label_of_a_compromised_asset_too(raw):
    """id を避けても、資産一覧に出ている呼び名で書けば同じことである。"""
    data = copy.deepcopy(raw)
    _question(data, "q_lateral_movement")["question"] = "営業部端末から fs01 へどう到達したか"
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "営業部端末" in str(exc.value)

    data = copy.deepcopy(raw)
    _question(data, "q_lateral_movement")["question"] = "佐藤の端末から何が起きたか"
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "佐藤" in str(exc.value)


def test_loader_allows_what_the_briefing_already_gave(raw):
    """ブリーフィングが渡した資産は、論点で名指ししてよい。

    「fs01 で大量のファイル名変更が検知された」は一次情報として最初に渡している。
    それを問い文が繰り返しても、新しいことは何も渡していない。
    ここまで禁じると、何を調べればいいか分からない問いしか書けなくなる。
    """
    data = copy.deepcopy(raw)
    _question(data, "q_lateral_movement")["question"] = "fs01 のファイル操作はどこから来たか"
    build(data)  # 例外が出ないこと


def test_loader_allows_naming_an_innocent_asset(raw):
    """無関係な資産の名前は禁じない。それは誤導であって漏洩ではない。"""
    data = copy.deepcopy(raw)
    _question(data, "q_exfiltration")["question"] = "ws-107 の外部通信は持ち出しか C2 か"
    build(data)  # 例外が出ないこと


def test_every_shipped_question_passes_the_rule(raw):
    """同梱シナリオの4論点のうち、規則に引っかかるのは1つも無い。

    規則を足した側が「例外を1つ抱えたまま」になっていないことの確認。
    """
    build(copy.deepcopy(raw))


def test_loader_rejects_prevents_on_a_non_communicate_action(raw):
    data = copy.deepcopy(raw)
    for a in data["actions"]:
        if a["id"] == "act_smb_session_fs01":
            a["prevents"] = ["tl_field_reboot"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "communicate 専用" in str(exc.value)


# ─────────── 端末の記録と、資料の中の結論（SPEC 5.4 / 5.6） ───────────


def _action(data, aid):
    return next(a for a in data["actions"] if a["id"] == aid)


def _evidence(data, eid):
    return next(e for e in data["evidence"] if e["id"] == eid)


def test_loader_rejects_a_transcript_that_names_losable_evidence(raw):
    """失われうる証拠にしか無い値を、端末の記録に書いたら拒否する。

    これは同梱シナリオが実際に踏んだ形である。
    `vol.py ... windows.handles --pid 6644` の 6644 は ev_010（fs01 の
    稼働中プロセス）にしか無い。fs01 が現場判断で再起動された後に
    この手を押すと、証拠は出てこないのに端末の記録だけが返り、
    取り損ねたものの中身をそこで渡してしまう。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_memory_dump_fs01")["command"] += (
        "\n$ vol.py -f fs01.raw windows.handles --pid 6644\n  12,847 handles\n"
    )
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "6644" in str(exc.value)


def test_loader_allows_a_constant_the_responder_can_type_before_pressing(raw):
    """押す前から打てる定数は通す。

    `Where-Object Id -eq 4104` の 4104 は公開されたイベントIDで、
    ev_001 の中身にしか出てこなくても「結果を先に見た」ことにはならない。
    ここまで拒否すると、コマンドに絞り込み条件が書けなくなる。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_dc_authlog")["command"] += (
        "\n$ Get-WinEvent -ComputerName DC01 | Where-Object Id -eq 4104\n  12 events\n"
    )
    build(data)  # 例外が出ないこと


def test_loader_rejects_a_parenthetical_note_in_a_transcript(raw):
    """括弧の注記は、システムが自分の罠に印を付ける行為である。

    害は書いた手よりも**書かなかった手**に出る。同じ destroys を持つ
    「fs01 を停止」には注記が無く、注記の有無がそのまま
    「調査の排他ペアはこの2つ」という目印になっていた。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_disk_image_ws042")["command"] = (
        "$ Stop-Computer -ComputerName WS-042 -Force\n"
        "  WS-042: powered off  (揮発性の情報はここで失われる)\n"
    )
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "括弧" in str(exc.value)


def test_loader_allows_a_parenthetical_label_in_a_transcript(raw):
    """禁じるのは節であって、名札ではない。

    「架電 03:24 携帯（登録番号）」の括弧は呼び分けであって主張ではない。
    `(egress only)` `(00:11:42)` も資料である。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_dc_authlog")["command"] += (
        "\n$ 架電 04:02  携帯（登録番号）  呼出 30秒\n"
    )
    build(data)  # 例外が出ないこと


def test_loader_rejects_a_conclusion_in_the_raw_material(raw):
    """content は全レベルで出る。そこに要約が混ざると hard が死ぬ。

    `summary` を伏せて生ログを読ませるのが hard の主題なのに、
    生ログの末尾が summary と同じ主張を述べていた（19件中6件）。
    """
    data = copy.deepcopy(raw)
    _evidence(data, "ev_013")["content"] += "\n注: この端末に攻撃の痕跡は認められない。\n"
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "ev_013.content" in str(exc.value)


def test_loader_does_not_police_the_summary(raw):
    """summary は結論を言ってよい。それが summary の仕事である。

    一行で所見を言うことが summary の役割で、それこそが hard で
    伏せられる中身である。ここまで縛るとアシストが何も残らない。
    """
    data = copy.deepcopy(raw)
    _evidence(data, "ev_013")["summary"] = "ws-113 に攻撃の痕跡は認められない"
    build(data)  # 例外が出ないこと


# ─────────── 封じ込めと業務停止の分離（SPEC 6.3） ───────────


def test_loader_rejects_a_compromise_that_cannot_be_stopped(raw):
    """侵害資産を直接止める手が無いシナリオを拒否する。

    封じ込めは依存連鎖しない。上流の dc01 を落としても fs01 の
    暗号化プロセスは止まらないので、fs01 を名指しで止める手が
    どこにも無いと `containment_completeness` は誰にも 1.0 に届かず、
    5.8 の `on_correct_containment` が永久に使われない。
    そのシナリオでは「正しく止める」ことが定義上できない。
    """
    data = copy.deepcopy(raw)
    gone = ("act_shutdown_fs01", "act_block_smb_fs01")
    data["actions"] = [a for a in data["actions"] if a["id"] not in gone]
    for pol in data["policies"]:
        for c in pol.get("constraints", []):
            c["action_ids"] = [i for i in c.get("action_ids", []) if i not in gone]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "直接止める手が無い侵害資産" in str(exc.value)
    assert "fs01" in str(exc.value)


def test_loader_rejects_a_block_that_erases_without_stopping(raw):
    """業務を止めない封じ込めは、稼働中の痕跡を消せない。

    `side_effects.business_impact: false` は「資産は動き続ける」という宣言で、
    動いている資産から揮発性の情報が消える理由は無い。ここを許すと
    「業務影響ゼロで証拠だけ消える」手が書け、業務継続からも証拠保全からも
    一方的に安い抜け道になる。消したいなら止めること。
    """
    data = copy.deepcopy(raw)
    for a in data["actions"]:
        if a["id"] == "act_block_smb_fs01":
            a["destroys"] = ["ev_010"]      # fs01 の稼働中プロセス（volatile）
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "揮発性の証拠を消しています" in str(exc.value)


def test_stopping_the_root_does_not_contain_what_hangs_off_it(scenario):
    """依存の根を落としても、その先の攻撃は止まらない。

    `depends_on` が答えているのは「どの資産で仕事ができなくなるか」であって
    「どの資産で攻撃が止まるか」ではない。ここを1つの集合で表していた頃は、
    dc01 の電源を落とすだけで fs01 も ws-042 も封じ込めたことになり、
    生きている Run キーと動き続ける暗号化プロセスが
    `on_correct_containment` を引いていた。
    """
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["dc01"]))
    e.decide(Decision(kind="action", action_id="act_shutdown_dc01"))
    e.decide(Decision(kind="finish"))

    assert set(e.state.contained_at) == {"dc01"}
    assert set(e.state.halted_at) == {"dc01", "fs01", "ws-042", "ws-107", "ws-113"}

    c = scoring.consequences(e.state, scenario)
    assert c.containment_completeness == 0.0    # 侵害資産は1つも止まっていない
    assert c.business_impact > 0                # 業務は全部止まっている


def test_a_block_that_keeps_the_asset_running_costs_no_business_impact(scenario):
    """同じ資産を止める2つの手が、違う代償を持つこと。

    fs01 を落とせば安いが業務が止まり、稼働中の痕跡も消える。
    境界で遮断すれば高いがサーバは動き続ける。どちらも封じ込めとしては
    同じだけ効く — 差が出るのは帰結の側であり、その重み付けは方針が持つ。
    ここが同じになると、対応フェーズに判断が無くなる。
    """
    def stop(action_id):
        e = Engine(scenario, "damage_minimization", None)
        e.decide(Decision(kind="declare_assessment", assessment=["fs01"]))
        e.decide(Decision(kind="action", action_id=action_id))
        e.decide(Decision(kind="finish"))
        return e

    blocked = stop("act_block_smb_fs01")
    powered_off = stop("act_shutdown_fs01")

    # 封じ込めとしては同じだけ効く
    assert set(blocked.state.contained_at) == set(powered_off.state.contained_at)

    cb = scoring.consequences(blocked.state, scenario)
    cp = scoring.consequences(powered_off.state, scenario)
    assert cb.containment_completeness == cp.containment_completeness

    # 代償は違う
    assert cb.business_impact == 0.0
    assert cp.business_impact > 0.0
    assert blocked.state.lost_evidence == []
    assert powered_off.state.lost_evidence == ["ev_010"]
    # 遮断のほうが時間がかかる（夜間の変更管理と実機確認）
    assert blocked.state.elapsed_minutes > powered_off.state.elapsed_minutes


def test_neither_way_of_stopping_fs01_wins_under_every_policy(scenario):
    """fs01 の2つの手は、方針をまたいで一律には順位が付かない。

    ここが一致してしまうと、選択肢が2つあっても判断は1つしかない
    （常に同じほうを押せばよい）。原則4「正解を一つに定めない」は、
    盤面のこの性質でしか担保できない。
    """
    def stop(action_id):
        e = Engine(scenario, "damage_minimization", None)
        e.decide(Decision(kind="declare_assessment", assessment=["fs01", "ws-042"]))
        e.decide(Decision(kind="action", action_id="act_block_c2"))
        e.decide(Decision(kind="action", action_id=action_id))
        e.decide(Decision(kind="finish"))
        return e.state

    blocked = stop("act_block_smb_fs01")
    powered_off = stop("act_shutdown_fs01")

    winners = set()
    for pol in scenario.policies:
        b = scoring.score(blocked, scenario, pol).composite_score
        o = scoring.score(powered_off, scenario, pol).composite_score
        assert b != o, f"{pol.id}: どちらを選んでも同じ評価になっている"
        winners.add("blocked" if b > o else "powered_off")
    assert winners == {"blocked", "powered_off"}, (
        f"どの方針でも同じ手が勝っている: {winners}"
    )


def test_loader_rejects_a_reversed_metric_without_a_good_side_name(raw):
    """悪さを測る指標には、良い側から見た名前を必ず付けさせる。

    講評は生値ではなく「良さ」を出す（向きが混ざった小数を並べても
    読めないため）。名前だけ悪い側のままにすると
    「未解消論点を抱えたままの判断 100」という列ができ、
    裏返す前より読めなくなる。
    """
    data = copy.deepcopy(raw)
    for m in data["scoring"]["fact_layer"]["metrics"]:
        if m["id"] == "unresolved_questions":
            m.pop("label_good")
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "label_good" in str(exc.value)


def test_the_good_side_name_is_what_the_debrief_shows(scenario):
    """講評に渡る名前は、良い側から見た名前であること。"""
    labels = {
        m.id: m.display_label for m in scenario.scoring.fact_layer.metrics
    }
    assert labels["unresolved_questions"] == "論点を解消してからの判断"
    # 向きが元から良い側の指標は、そのままの名前で通る
    assert labels["assessment_recall"] == "被疑判定の再現率"


# ─────────── 根絶（SPEC 5.8 / 9.4 #5） ───────────


def _eradicator(data):
    """盤面から永続化を取り除ける手を1つ取り出す。"""
    return next(a for a in data["actions"] if a.get("eradicates"))


def test_loader_rejects_eradication_by_something_that_is_not_containment(raw):
    """`eradicates` は封じ込めの手にしか書けない。

    調べる手や連絡の手が資産を作り直すことはない。ここを開けておくと、
    「メモリを取ったので永続化も消えた」のような、盤面のどこにも
    根拠のない書き方が通る。
    """
    data = copy.deepcopy(raw)
    act = next(a for a in data["actions"] if a["id"] == "act_memory_dump_ws042")
    act["eradicates"] = ["ws-042"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "contain 専用" in str(exc.value)


def test_loader_rejects_eradicating_an_asset_you_do_not_touch(raw):
    """手を触れていない資産から、永続化だけが消えることはない。

    `eradicates ⊆ targets` を外すと、「dc01 を止めたので ws-042 の
    仕掛けも消えた」が書けてしまう。6.3 が `contained_at` について
    退けたのと同じ形の誤りが、根絶の側で復活する。
    """
    data = copy.deepcopy(raw)
    _eradicator(data)["eradicates"] = ["ws-107"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "eradicates が targets に含まれていません" in str(exc.value)


def test_loader_rejects_persistence_that_nothing_can_remove(raw):
    """根絶する手が盤面に無い `persistence` は、到達不能な条件になる。

    5.8 は `persistence ⊆ eradicated` を要求する。取り除く手が
    どこにも無ければ、どれだけ正しく止めても `on_correct_containment` には
    誰も届かない — そのシナリオには「最良の対応」が存在しない。
    """
    data = copy.deepcopy(raw)
    for a in data["actions"]:
        a.pop("eradicates", None)
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "根絶する手が無い永続化" in str(exc.value)


def test_the_persistence_clause_is_not_vacuous(scenario):
    """5.8 の後半の条項が、実際に結果を変える組を持っていること。

    ローダは `persistence ⊆ compromised` を要求している。だから
    根絶を `contained` で判定していた頃は、前半が成り立てば後半も
    自動的に成り立った — **条項は恒真で、128通り全数で結果を変える組は
    0個だった**（9.4 #5）。恒真な条項は書いていないのと同じで、
    「隔離しても、端末を戻せば攻撃者も戻ってくる」は盤面で一度も
    問われていなかった。

    ここが 0件に戻ったら、盤面から根絶の手が消えたということである。
    """
    import itertools

    from irdojo import damage as damage_mod
    from irdojo.schema import ActionType

    gt = scenario.world.ground_truth
    effect = scenario.damage.containment_effect
    by_id = scenario.action_by_id
    acts = [a.id for a in scenario.actions if a.type == ActionType.CONTAIN]

    differing = 0
    for r in range(len(acts) + 1):
        for combo in itertools.combinations(acts, r):
            stopped = {t for aid in combo for t in by_id[aid].targets}
            purged = {t for aid in combo for t in by_id[aid].eradicates}
            real = damage_mod.containment_factor(stopped, purged, gt, effect)
            naive = damage_mod.containment_factor(stopped, stopped, gt, effect)
            if real != naive:
                differing += 1
    assert differing > 0


def test_eradication_does_not_travel_down_the_dependency_chain(scenario):
    """根絶は連鎖しない。上流を作り直しても、下流の仕掛けは残る。

    業務の停止（`halted_at`）だけが `depends_on` で波及する。
    攻撃が止まる場所も（6.3）、置いていかれたものが消える場所も、
    手を当てた資産の上だけである。ここを連鎖させると、
    **依存の根を1つ作り直すだけで盤面が片付く** — 最も乱暴な手が
    最も安くなる。
    """
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    out = e.decide(Decision(kind="action", action_id="act_rebuild_ws042"))

    assert out.eradicated == ["ws-042"]
    assert set(e.state.eradicated_at) == {"ws-042"}
    # 業務は依存で波及するが、根絶はしない
    assert "dc01" not in e.state.eradicated_at


def test_stopping_is_not_eradicating(scenario):
    """通信を止める手は、置いていかれたものには触らない（SPEC 5.8）。

    同じ資産を対象にしていても、`targets` と `eradicates` は別の問いに
    答えている。ここが一致してしまうと、5.8 の後半の条項は恒真に戻る。
    """
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    out = e.decide(Decision(kind="action", action_id="act_block_c2"))

    assert out.contained == ["ws-042"]
    assert out.eradicated == []
    assert e.state.eradicated_at == {}
    assert not out.empty


def test_restoring_something_already_stopped_is_not_a_blank(scenario):
    """既に止めた資産を作り直した回は、空振りではない（SPEC 7.6.8）。

    `contained` は空になる（もう止めてある）。`already` は数えるが、
    その回に実際に起きたのは根絶である。`empty` が `eradicated` を
    数えないと、緑の枠の中にオレンジの「何も出てこなかった」が並ぶ。
    判定はエンジンと画面の両方にあるので、必ず同時に直すこと。
    """
    e = Engine(scenario, "damage_minimization", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    e.decide(Decision(kind="action", action_id="act_block_c2"))
    out = e.decide(Decision(kind="action", action_id="act_rebuild_ws042"))

    assert out.contained == []
    assert out.eradicated == ["ws-042"]
    assert not out.empty
