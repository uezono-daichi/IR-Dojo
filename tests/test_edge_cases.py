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
        # refutation_mode は誤導の棄却条件。誤導でない証拠に書いても効かない
        (lambda d: d["evidence"][7].__setitem__("refutation_mode", "any"),
         "misleading: true の証拠にのみ"),
        # 候補1件の all は any と同じ挙動になるのに、複雑度だけがずれる
        (lambda d: d["evidence"][6].__setitem__("refutation_mode", "all"),
         "2件以上"),
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


def _set_ev(data, eid, **kw):
    for e in data["evidence"]:
        if e["id"] == eid:
            e.update(kw)
            return e
    raise KeyError(eid)


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        # 白と読ませて正しいなら、それは誤導ではなく資料である
        (lambda d: _set_ev(d, "ev_013", clears=["ws-055"]),
         "misleading: true の証拠にのみ"),
        # 指す先も、白と読ませる先も無い誤導は、誰も誤導しない
        (lambda d: _set_ev(d, "ev_020", clears=[]),
         "誰も誤導しません"),
        # 1つの所見が同じ資産を「疑え」と「白だ」の両方に読ませることはない
        (lambda d: _set_ev(d, "ev_020", points_to=["ws-055"]),
         "両方に書いています"),
        # 無実の資産を白と読ませたなら、その所見は正しい
        (lambda d: _set_ev(d, "ev_020", clears=["ws-107"]),
         "compromised 以外"),
        # 棄却できても名指しが戻らないなら、再現率は下がったまま
        (lambda d: _set_ev(d, "ev_020", refuted_by=["ev_017"]),
         "名指しが戻らない"),
        (lambda d: _set_ev(d, "ev_020", clears=["nope"]),
         "clears に未定義の資産"),
    ],
)
def test_the_loader_rejects_a_misused_clears(raw, mutate, fragment):
    """再現率を下げる側の誤導は、**書き間違えても盤面では何も起きない**。

    points_to の誤導は「名指しが増えた」という形で作者に見える。
    clears の誤導は指す先が無いので、間違って書いても画面は静かなままで、
    作者は罠を置いたつもりのまま誰も引っかからない盤面を出荷できる。
    だから同じ厚みの検査をローダに置く（SPEC 3.4）。
    """
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert fragment in str(exc.value)


def test_the_board_actually_uses_both_directions_of_misdirection(scenario):
    """盤面に、誤導の向きが両方あること（SPEC 3.4 / v1.42）。

    v1.41 までの1本目は誤導が2つとも innocent を指しており、
    教えられる失敗は「無関係なものを疑う」の1種類しか無かった。
    実務でより高くつくのは逆側で、そちらは盤面に存在しなかった。
    """
    truth = set(scenario.world.ground_truth.compromised)
    mis = [e for e in scenario.evidence if e.misleading]
    over = [e.id for e in mis if set(e.points_to) - truth]
    under = [e.id for e in mis if e.clears]
    assert over, "疑わせる側の誤導が無い"
    assert under, "白と読ませる側の誤導が無い"


def test_the_costly_probe_looks_at_the_same_asset_as_the_cheap_one(scenario):
    """白と読ませる誤導は、**同じ資産を見に行く手**で覆せること。

    棄却の材料が別の資産の話だと、学習者は「もう一度この端末を見る」
    という筋道に辿り着けない。SPEC 3.4 の「棄却に必要な証拠が極端に
    高コスト＝理不尽」の、対象側の条件である。

    壊れ方: 安いほうが高いほうと同値段になると、罠が罠でなくなる
    （誰も安いほうで済ませない）。
    """
    for ev in scenario.evidence:
        if not ev.clears:
            continue
        cheap = [a for a in scenario.actions if ev.id in a.yields]
        assert cheap, ev.id
        for asset in ev.clears:
            back = [
                a for a in scenario.actions
                if set(a.yields) & set(ev.refuted_by) and asset in a.investigates
            ]
            assert back, f"{ev.id}: {asset} をもう一度見に行く手が無い"
            assert min(a.cost_minutes for a in back) > min(
                a.cost_minutes for a in cheap
            ), f"{ev.id}: 覆す手が安いほうと同値段以下。罠が成立しない"


def test_loader_rejects_unknown_keys(raw):
    raw["meta"]["compromised_hint"] = "ws-042"
    with pytest.raises(ScenarioError) as exc:
        build(raw)
    assert "Extra inputs" in str(exc.value) or "extra" in str(exc.value).lower()


def test_loader_ignores_author_written_complexity(raw):
    raw["meta"]["complexity"] = 5
    with pytest.warns(UserWarning, match="complexity"):
        sc = build(raw)
    # 作者の書いた 5 ではなく、内容から算出した値が使われる
    assert sc.complexity == compute_complexity(sc)
    assert sc.complexity != 5


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
    assert compute_complexity(sc) == 3

    data = copy.deepcopy(raw)
    for e in data["evidence"]:
        if e.get("misleading"):
            e["plausibility"] = "high"
    assert compute_complexity(build(data)) == 3

    # 誤導を増やすと上がる（作者が狙って設計できる）。
    # v1.42 で盤面の誤導が 2 → 3 になったので、1つ足すだけで★が上がる
    data = copy.deepcopy(raw)
    for e in data["evidence"]:
        if e["id"] == "ev_017":               # ネガティブ所見を誤導に仕立て直す
            e["misleading"] = True
            e["plausibility"] = "high"
            e["points_to"] = ["ws-113"]
            e["refuted_by"] = ["ev_008"]
    assert compute_complexity(build(data)) == 4


def test_complexity_counts_multi_step_by_mode_not_by_count(raw):
    """「棄却に2つ以上の証拠を要する誤導」は refutation_mode が決める。

    `len(refuted_by) >= 2` で数えていた頃は、**逃げ道が2本ある易しい誤導**
    （any で候補が2つ）を多段と数えていた。棄却の材料が増えるほど
    複雑度が上がるのは向きが逆で、作者が狙って設計できない。

    **候補の件数を揃えて、条件だけを入れ替える。** 誤導の refuted_by を
    2件にしたうえで any と all を比べると、`len(refuted_by)` は
    どちらも 2 で同じなのに★が変わる。件数で数えていたら、
    この2つは区別できない（逃げ道が2本ある易しい誤導のほうまで
    多段と数えてしまう）。

    **入れ替えるのは2つ。** 同梱の盤面から多段が無くなった（v1.43 で
    ev_005 が「向きの取り違え」型になり、棄却がもう一方の端の1つで
    閉じるようになった）ので、1つ足すだけでは★の丸めをまたがない。
    測りたいのは丸めの位置ではなく、mode が数え方を変えることである。
    """
    assert compute_complexity(build(raw)) == 3

    def with_mode(mode):
        data = copy.deepcopy(raw)
        pairs = {"ev_007": ["ev_003", "ev_012"], "ev_020": ["ev_021", "ev_017"]}
        for e in data["evidence"]:
            if e["id"] in pairs:
                e["refuted_by"] = pairs[e["id"]]
                e["refutation_mode"] = mode
        return compute_complexity(build(data))

    # 逃げ道が2本ある（any）だけでは、盤面は難しくならない
    assert with_mode("any") == 3
    # 2つ揃って初めて棄却できる（all）なら、多段が増えて★が上がる
    assert with_mode("all") == 4

    # 候補を1つに減らしても同じ（any で1件と、any で2件は同じ難しさ）
    data = copy.deepcopy(raw)
    for e in data["evidence"]:
        if e["id"] == "ev_005":
            e["refutation_mode"] = "any"
            e["refuted_by"] = ["ev_008"]
    assert compute_complexity(build(data)) == compute_complexity(
        build(_ev_005_as_any(raw))
    )


def _ev_005_as_any(raw):
    """ev_005 を「材料は2つのまま any」にした盤面。

    上のブロックと合わせて、`refuted_by` の件数が同じでも
    `refutation_mode` が違えば数え方が変わることを両側から押さえる。
    """
    data = copy.deepcopy(raw)
    for e in data["evidence"]:
        if e["id"] == "ev_005":
            e["refutation_mode"] = "any"
    return data


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
    assert viol[0].constraint_type == "require_capture_of_target"
    # 同じ行動でも業務継続最優先では違反にならない
    assert e.state.violations_by_policy["business_continuity"] == []


def _capture_then_stop(scenario, captures, stop):
    e = Engine(scenario, "evidence_preservation", None)
    for aid in captures:
        e.decide(Decision(kind="action", action_id=aid))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042", "fs01"]))
    e.decide(Decision(kind="action", action_id=stop))
    e.decide(Decision(kind="finish"))
    return [
        v.constraint_type
        for v in e.state.violations_by_policy["evidence_preservation"]
    ]


def test_capture_must_be_of_the_asset_being_stopped(scenario):
    """採取は**その資産の分**でなければ足りない（SPEC 5.3 / v1.40）。

    v1.39 までの `require_before` は、前提タグの付いた手を盤面のどこかで
    1つ押していれば満たされた。ws-042 のメモリを取った学習者は、
    それだけで fs01 を無条件に落とせた — **事実上ほぼ無料の制約**で、
    証拠保全最優先は「どの手を選ぶか」に一度も効いていなかった。
    その結果、封じ込めの組を総当たりで並べると業務継続最優先と
    上位6組まで完全に同順になり、3本目の方針が2本目の複製になっていた。

    ここが壊れると、症状は「証拠保全を選んでも盤面が変わらない」になる。
    無言で成立してしまうので、テストでしか見張れない。
    """
    # ws-042 の分を取っただけでは、fs01 を止める要件は満たさない
    # （メモリ取得の手がかりを開けるために、先に2手押す必要がある）
    # **要件を満たすのは仕掛けの手のほうである**（v1.43）。読む前でも
    # 「その資産から揮発性の情報を取った」ことは成立している
    assert _capture_then_stop(
        scenario,
        ["act_collect_evtx_fs01", "act_netflow_overview",
         "act_memory_capture_ws042"],
        "act_stop_share_fs01",
    ) == ["require_capture_of_target"]

    # fs01 の分を取れば、fs01 を止めるのは違反にならない
    assert _capture_then_stop(
        scenario, ["act_memory_dump_fs01"], "act_stop_share_fs01",
    ) == []


def test_evidence_preservation_leaves_one_way_to_stop_the_file_server(scenario):
    """証拠保全最優先で fs01 に取れる手は1つだけになる（SPEC 3.8）。

    業務に載せたままの遮断は原本の上書きを止めず、電源断は稼働中の状態を
    消す。残るのは「動かしたまま、業務から降ろす」だけ。
    **業務継続最優先の最良手も、被害最小化最優先の最良手も、
    この方針では違反になる** — 同じ資産に方針ごとの正解があるとは
    こういうことである。
    """
    got = {
        aid: _capture_then_stop(scenario, ["act_memory_dump_fs01"], aid)
        for aid in ("act_block_smb_fs01", "act_stop_share_fs01", "act_shutdown_fs01")
    }
    assert got["act_stop_share_fs01"] == []
    assert got["act_block_smb_fs01"] == ["forbid_action"]
    assert got["act_shutdown_fs01"] == ["forbid_action"]


def _policy(data, pid):
    return next(p for p in data["policies"] if p["id"] == pid)


def test_loader_rejects_a_constraint_that_can_never_fire(raw):
    """発火しようのない制約を拒否する（SPEC 5.3 / v1.40）。

    `evaluate` は足りないフィールドを黙って False で返す。つまり
    **書けるが何も起きない制約**が作れるということで、症状は
    「方針の名乗りだけが変わって、盤面では何も変わらない」になる。
    無言で成立するので、読んでも見つからない。
    """
    data = copy.deepcopy(raw)
    for c in _policy(data, "evidence_preservation")["constraints"]:
        if c["type"] == "require_capture_of_target":
            del c["prerequisite_tags"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "永久に発火しません" in str(exc.value)

    data = copy.deepcopy(raw)
    for c in _policy(data, "evidence_preservation")["constraints"]:
        if c["type"] == "require_capture_of_target":
            del c["action_type"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "永久に発火しません" in str(exc.value)


def test_loader_rejects_a_capture_requirement_nobody_can_meet(raw):
    """守れない方針を拒否する（SPEC 5.3 / v1.40）。

    「止める資産ごとに、その資産の揮発性証拠を先に取れ」と言いながら、
    その資産に届く採取の手が盤面に無いと、**その方針を選んだ学習者は
    何をしても違反する** — compromised を覆えば違反、覆わなければ
    完全度が落ちる。選べるが守れない方針は、方針ではなく罠である。
    """
    data = copy.deepcopy(raw)
    for a in data["actions"]:
        if a["id"] == "act_memory_dump_fs01":
            a["tags"] = []            # fs01 に届く採取の手が盤面から消える
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "揮発性証拠を取る手が無い侵害資産" in str(exc.value)
    assert "fs01" in str(exc.value)


def test_loader_rejects_a_policy_that_forbids_every_stop(raw):
    """止める手を全部禁じる方針を拒否する（SPEC 5.3 / 3.8 / v1.40）。

    `forbid_action` は「複数ある手のうち、この方針ではこちらを取れ」と
    言うための道具である。ある侵害資産の手を全部禁じると、その方針を
    選んだ学習者は**止めれば違反、止めなければ完全度が落ちる。**

    fs01 には手が3つあり、2つの方針がそれぞれ2つを禁じている。
    **手を1つ消すと、どちらかの方針が黙って詰む** — 消した場所と
    詰んだ場所が離れているので、読んでも気づけない。
    """
    data = copy.deepcopy(raw)
    data["actions"] = [
        a for a in data["actions"] if a["id"] != "act_stop_share_fs01"
    ]
    for pol in data["policies"]:
        for c in pol.get("constraints", []):
            if "action_ids" in c:
                c["action_ids"] = [
                    i for i in c["action_ids"] if i != "act_stop_share_fs01"
                ]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "止める手を全部禁じています" in str(exc.value)


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
    gone = ("act_shutdown_fs01", "act_block_smb_fs01", "act_stop_share_fs01")
    data["actions"] = [a for a in data["actions"] if a["id"] not in gone]
    for pol in data["policies"]:
        # 中身が空になった forbid_action は残さない。空の制約は
        # 「永久に発火しない制約」としてローダが別の理由で拒否するので、
        # この検査が見たいものに辿り着かない（v1.40）
        kept = []
        for c in pol.get("constraints", []):
            if "action_ids" in c:
                c["action_ids"] = [i for i in c["action_ids"] if i not in gone]
                if not c["action_ids"]:
                    continue
            kept.append(c)
        pol["constraints"] = kept
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
    assert set(e.state.halted_at) == {
        "dc01", "fs01", "ws-042", "ws-055", "ws-107", "ws-113",
    }

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
    act = next(a for a in data["actions"] if a["id"] == "act_memory_analyze_ws042")
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


def _action(data, aid):
    return next(a for a in data["actions"] if a["id"] == aid)


def test_loader_rejects_a_block_that_erases_what_it_never_touched(raw):
    """通り道を塞ぐだけの手から、稼働中の痕跡は消えない（SPEC 5.6）。

    境界での遮断は資産に手を触れない。そこに `destroys` を付けると
    「業務影響ゼロで証拠だけ消える」手になり、業務継続からも証拠保全からも
    一方的に安い抜け道になる。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_block_smb_fs01")["destroys"] = ["ev_010"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "通り道を塞ぐだけ" in str(exc.value)


def test_an_eradicating_hand_may_erase_the_state_it_removes(raw):
    """取り除く手は、業務を止めなくても現在の状態を消してよい（v1.38）。

    この条件はもともと `business_impact: false` だけで書かれていた。
    「業務が止まる」と「資産が動き続ける」が一致する世界
    ——端末は電源を切ると業務も止まる——でしか正しくない近似で、
    SaaS の盤面で破れた。委任同意を利用者ごとに取り消す手は、
    生きている同意の一覧を確実に消すが、業務は1分も止まらない。

    そこで `business_impact: true` と書かせるのは世界について嘘をつくこと、
    `volatile: false` と書かせるのは証拠について嘘をつくことだった。
    抜け道が開かないのは、取り除く手が盤面で最も価値のある手だからである。
    """
    data = copy.deepcopy(raw)
    act = _action(data, "act_purge_persistence_ws042")
    assert act["side_effects"]["business_impact"] is False
    assert act["eradicates"] == ["ws-042"]
    act["destroys"] = ["ev_010"]        # volatile な稼働中の痕跡
    sc = build(data)                    # 通ること
    assert "ev_010" in sc.action_by_id["act_purge_persistence_ws042"].destroys


# ─────────── 仕掛けと読解（SPEC 5.6.2 / 5.6.3） ───────────


def test_loader_rejects_securing_what_the_clock_never_takes(raw):
    """時計から何も守らない `secures` を拒否する。

    `secures` が効くのは timeline の `destroys` に対してだけである
    （自分の手の `destroys` から守る力は、そもそも持たせていない —
    そこまで守ると 3.8 の排他が盤面から消える）。
    どの出来事もその証拠を奪わないなら、その手は時間を払って
    盤面を1ミリも変えないボタンになる。

    **症状が出るのは書いた場所ではない。** 作者は「先に仕掛ける意味がある」
    つもりで盤面を出荷し、実測して初めて「押しても押さなくても同じ」と
    分かる。`_reject_toothless_timeline`（何も奪わない出来事）と同じ形の
    検査で、見ている向きが逆になっただけである。
    """
    data = copy.deepcopy(raw)
    # ev_011 はどの出来事にも奪われない（プロキシのログは消えない）
    _action(data, "act_memory_capture_ws042")["secures"] = ["ev_011"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "ev_011" in str(exc.value)


def test_loader_rejects_securing_what_you_produce_yourself(raw):
    """自分が産む証拠を守っても意味がない。

    取得済みの証拠は初めから失われない（`destroys` は「以後取得できなく
    なる」だけ。5.6）。同じ手が産んで同じ手が守るなら、守る側は飾りである。
    """
    data = copy.deepcopy(raw)
    act = _action(data, "act_memory_analyze_ws042")
    act["secures"] = ["ev_009"]         # この手が yields しているもの
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "ev_009" in str(exc.value)


def test_loader_rejects_a_ring_of_prerequisites(raw):
    """`requires_actions` が輪になっていたら拒否する。

    輪の中の手はどれも永久に押せない。到達性の検査（死蔵アクション）でも
    引っかかるが、そちらのメッセージは「到達できないアクション」で、
    原因が前提の輪であることが読み手に伝わらない。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_memory_capture_ws042")["requires_actions"] = [
        "act_memory_analyze_ws042"
    ]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "循環" in str(exc.value)


def test_loader_rejects_a_prerequisite_that_does_not_exist(raw):
    """綴りを間違えた前提は、黙って「最初から押せる手」にならないこと。

    `requires_actions` は**全部**押し終えて初めて開く門なので、
    未定義の id を無視すると門そのものが消える（空集合は常に満たされる）。
    ここを黙って通すと、仕掛けを押さずに読める盤面が出荷される。
    """
    data = copy.deepcopy(raw)
    _action(data, "act_memory_analyze_ws042")["requires_actions"] = ["act_typo"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "act_typo" in str(exc.value)


def test_loader_rejects_a_deadlock_that_crosses_the_two_gates(raw):
    """2種類の門をまたいだ手詰まりを、到達性の検査が拾うこと。

    門は2つある — 手がかり（`requires_evidence`、どれか1つで開く）と、
    前提の手（`requires_actions`、全部押して初めて開く）。
    **輪の検査だけでは足りない。** `requires_actions` の中だけを見ても、
    「読む手 → 前提の手 → その手を開く証拠 → 読む手」のように
    2つの門をまたいだ輪は見つからない。

    到達性の検査が `requires_evidence` の連鎖しか辿っていなかった頃は、
    前提の手を持つものが**最初から押せる手**として数えられ、
    こういう盤面が黙って出荷された。
    """
    data = copy.deepcopy(raw)
    data["actions"].append({
        "id": "act_dead",
        "label": "ws-042 の解析結果を突き合わせる",
        "group": "ws-042（営業部端末）",
        "phase": "investigation",
        "type": "investigate",
        "cost_minutes": 10,
        "requires_evidence": ["ev_009"],     # 読む手だけが産む証拠
        "investigates": ["ws-042"],
        "yields": [],
    })
    _action(data, "act_memory_analyze_ws042")["requires_actions"] = ["act_dead"]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "到達できない" in str(exc.value)
    assert "act_memory_analyze_ws042" in str(exc.value)


# ─────────── 資産盤（SPEC 7.6.16） ───────────


def test_the_loader_rejects_an_asset_no_action_can_touch(raw):
    """どの手も触れない資産を拒否する。

    盤は資産ごとに「あなたが押した手のうち、この資産に手を当てた数」を出す。
    触りに行く手が盤面に1つも無い資産があると、そこは**何をしても 0 のまま**で、
    学習者は自分が飛ばしたのだと読む。実際には飛ばしようがない。

    盤が無かった頃も同じ穴は開いていた — 被疑判定の選択肢には並ぶのに、
    根拠を取りに行く手段が無い。名指しを求めておいて調べさせないのは、
    判断ではなく勘を測っている。
    """
    data = copy.deepcopy(raw)
    data["world"]["assets"].append({
        "id": "ws-900",
        "label": "総務部端末 (未使用)",
        "criticality": "low",
        "business_impact_per_hour": 100,
        "depends_on": ["dc01"],
    })
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "どのアクションも触れない資産" in str(exc.value)
    assert "ws-900" in str(exc.value)


def test_the_loader_rejects_a_clears_that_never_names_its_asset(raw):
    """白と読ませる誤導は、その資産を本文で名指ししていること。

    読み手が「この端末の話だ」と分かって初めて「この端末は白だ」に化ける。
    本文が名前を出さないなら、誰もその一般化をしない — 罠は置かれているのに、
    誰も踏まない。**盤の「言及」も本文の文字列照合で数える**ので、
    名指しの無い `clears` は盤にも読み手にも現れない。
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_020")
    ev["content"] = ev["content"].replace("ws-055", "当該端末").replace(
        "WS-055", "当該端末"
    )
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "content が名指ししていません" in str(exc.value)


# ─────────── 可能性の列挙（SPEC 5.4 / v1.46） ───────────


def test_the_loader_rejects_a_single_possibility(raw):
    """可能性が1つしかない列挙は、可能性ではなく結論である。

    「これは定時の処理かもしれません」と1行だけ書けば、読む側は
    そう読む。**数が2に満たない時点で、列挙は本命を1つ立てている。**
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_007")
    ev["possibilities"] = [ev["possibilities"][0]]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "可能性が1つしかありません" in str(exc.value)


def test_the_loader_rejects_an_enumeration_that_leans_by_thickness(raw):
    """1項目だけ厚い列挙は、厚みで本命を指している。

    順序は機械には見えないが、厚みは数えられる。3行のものと半行のものを
    並べれば、読む側は長いほうを本命と読む — `reading` の厚みが誤導の
    目印になるのと同じ理屈（test_misleading_readings_are_not_thinner）。
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_005")
    ev["possibilities"] = [ev["possibilities"][0] * 3] + ev["possibilities"][1:]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "厚みが偏っています" in str(exc.value)


def test_the_loader_rejects_an_enumeration_that_leans_by_wording(raw):
    """語気で本命を指す列挙を拒否する。

    数も厚みも揃えたまま、「おそらく」の4文字で答えは渡せる。
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_007")
    ev["possibilities"][0] = "おそらく" + ev["possibilities"][0]
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "本命を示唆する語" in str(exc.value)


def test_the_loader_rejects_an_enumeration_that_names_an_asset(raw):
    """列挙は読みの種類を並べる欄で、誰かを名指しする欄ではない。

    **ブリーフィングの免除は置いていない。** 問い文（5.5）と違って、
    列挙は盤面のどの名前が無くても書ける。
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_005")
    # 厚みの検査に先に当たらないよう、元の項目の中の語だけを差し替える
    ev["possibilities"][0] = ev["possibilities"][0].replace(
        "その端末の前", "ws-113 の前"
    )
    with pytest.raises(ScenarioError) as exc:
        build(data)
    assert "資産の呼び名" in str(exc.value)


def test_the_loader_rejects_an_enumeration_that_pays_for_the_ledger(raw):
    """**列挙が棄却材料の中身を先に渡すと、その材料を買う手が死ぬ。**

    これが可能性の列挙を足すときの一番大きい危険である。
    「毎日 02:15 に起動するバックアップジョブかもしれません」と書いた瞬間、
    誤導は誤導でなくなる。台帳を引く手は 25分の値札が付いたまま、
    誰も押す理由が無くなる — 盤面から手が1本、値札だけ残して消える。

    刻みは資料の側にしか無い語（英数字まじりの字面・カタカナ4字以上・
    漢字3字以上）で、**その証拠に既に出ている語は除く**。
    完全な判定ではない（言い換えれば通る）ので、構造側の保証は
    `tools/balance.py` の「列挙だけで畳むプレイが、材料を買うプレイに
    勝たない」が受け持つ。
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_007")
    ev["possibilities"][0] = (
        "毎日 02:15 に起動するバックアップジョブの可能性。その端末の登録で分かれる"
    )
    with pytest.raises(ScenarioError) as exc:
        build(data)
    msg = str(exc.value)
    assert "棄却材料 ev_003 にしか無い語" in msg


def test_an_enumeration_may_point_at_the_direction_that_settles_it(raw):
    """**方向は書ける。決着だけが書けない。**

    上の検査が「確かめる先を書くな」になっていたら、列挙は
    「いろいろありえます」で終わる役立たずの欄になる。
    ここは逆側 — どこを見れば分かれるかまでは書けることを見張る。
    """
    data = copy.deepcopy(raw)
    ev = _set_ev(data, "ev_007")
    ev["possibilities"][0] = (
        "予定された自動の処理が、決まった時刻に外へ出した。"
        "その端末に何が仕込まれていたかを見れば分かれる"
    )
    build(data)   # 例外が出ないこと


def test_the_board_counts_only_the_hands_the_learner_pressed(scenario):
    """盤の数は、押した手の写像でしかない。

    **押していない手の射程は盤に出ない。** 出せば「この手は dc01 を見に行く」と
    押す前に教えることになり、選択が賭けでなくなる（SPEC 7.4）。
    開始時点でどの資産も 0 であること、1手押したらその手が触れた資産だけが
    増えることを見る。
    """
    e = Engine(scenario, "business_continuity", None)
    before = {a.id: a.touched for a in e.view().assets}
    assert set(before.values()) == {0}, "押す前から数がある"

    act = scenario.action_by_id["act_dc_authlog"]
    hit = set(act.targets) | set(act.investigates) | set(act.eradicates)
    assert hit, "前提が崩れている（この手はどの資産にも触れない）"

    e.decide(Decision(kind="action", action_id=act.id))
    after = {a.id: a.touched for a in e.view().assets}
    grew = {aid for aid in after if after[aid] > before[aid]}
    assert grew == hit, f"増えた資産が手の射程と違う: {sorted(grew)} ≠ {sorted(hit)}"
    assert all(after[aid] == 1 for aid in grew), "1手で2つ以上数えている"


def test_the_board_is_withheld_where_digested_views_are(scenario):
    """hard には盤の数を渡さない。世界の事実は取り上げない。

    盤は**自分の行動を1枚に消化して見せる装置**である。消化された見せ方は
    hard が奪う側にあり、構成図・被害グラフ・論点一覧と同じ扱いになる（3.10）。

    取り上げないのは世界の側の事実 — `depends_on` は hard でも渡す。
    両方消すと、hard だけが構成を知らずに上流を止めて事故る演習になる。
    """
    from irdojo.schema import AssistLevel

    e = Engine(scenario, "business_continuity", AssistLevel.HARD)
    e.decide(Decision(kind="action", action_id="act_dc_authlog"))
    for a in e.view().assets:
        assert a.touched is None and a.mentions is None, f"{a.id} に数が出ている"
    assert any(a.depends_on for a in e.view().assets), "依存関係まで消えている"


def test_the_board_only_repeats_what_the_outcome_already_said(scenario):
    """止めた・取り除いた・止まったは、押した瞬間に告げてある事実。

    盤はそれを並べ直すだけで、新しいことを1つも渡さない。だから
    hard でも隠さない。ここが食い違うと、盤が結果の箱より先に
    何かを知っていることになる。
    """
    e = Engine(scenario, "business_continuity", None)
    e.decide(Decision(kind="action", action_id="act_netflow_overview"))
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    out = e.decide(Decision(kind="action", action_id="act_shutdown_fs01"))

    board = {a.id: a for a in e.view().assets}
    for aid in out.contained:
        assert board[aid].contained, f"{aid} を止めたのに盤が言わない"
    for aid in out.halted:
        assert board[aid].halted, f"{aid} が止まったのに盤が言わない"
    # 告げていないものを盤が勝手に足していないこと
    said = set(out.contained)
    assert {a.id for a in board.values() if a.contained} == said
