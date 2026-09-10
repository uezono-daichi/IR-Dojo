"""答えが配信経路に載っていないことを検査する（SPEC 7.7.3）。

プレイ中の API レスポンスを全部走査し、ground_truth 由来のキーと、
選択を賭けでなくする構造化情報が含まれないことを確かめる。
"""

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from irdojo.api import app
from irdojo.loader import SCENARIO_DIR, load_scenario


@pytest.fixture
def raw_scenario():
    import yaml

    path = SCENARIO_DIR / "ransomware-initial-response-01.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))

# プレイ中のレスポンスに現れてはならないキー
FORBIDDEN_KEYS = {
    # 答えそのもの
    "ground_truth", "compromised", "persistence", "innocent",
    "patient_zero", "attack_narrative", "misleading", "refuted_by",
    # 選択を賭けでなくする構造化情報
    "points_to", "yields", "destroys", "investigates", "targets",
    # 何を防げるかは答えの側。ラベルが世界の言葉で何をするかを言えば足りる
    "prevents", "averted_text", "averted_label",
    # 採点の内部情報
    "business_impact_per_hour", "weights", "constraints",
    "violations_by_policy", "misled_follow_minutes",
}

# `depends_on` は意図的に開示している（SPEC 7.4）。
# 自組織の構成は担当者が知っていて当然のもので、伏せると
# 「構成を知らない人だけが上流を止めて痛い目を見る」形になる。
# 隠すのは侵害の有無であって、環境の形ではない。


@pytest.fixture
def client():
    return TestClient(app)


def walk(node, path=""):
    """(パス, キー, 値) を再帰的に列挙する。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def assert_clean(payload, where):
    for path, key, _ in walk(payload):
        assert key not in FORBIDDEN_KEYS, f"{where}: {path}.{key} が漏れている"


def test_scenario_list_returns_display_info_only(client):
    body = client.get("/api/scenarios").json()
    assert_clean(body, "GET /api/scenarios")
    # 証拠もアクションも返さない
    text = json.dumps(body, ensure_ascii=False)
    assert "ev_00" not in text
    assert "act_" not in text


def test_play_responses_never_leak(client):
    sc = load_scenario("ransomware-initial-response-01")
    truth = sc.world.ground_truth
    narrative_head = truth.attack_narrative.strip().splitlines()[0]

    created = client.post(
        "/api/session", json={"scenario_id": sc.meta.id}
    ).json()
    assert_clean(created, "POST /api/session")
    sid = created["session_id"]

    seen = [created]
    for aid in ("act_collect_evtx_fs01", "act_netflow_overview", "act_dc_authlog"):
        res = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": aid},
        ).json()
        assert_clean(res, f"decide {aid}")
        seen.append(res)

    res = client.post(
        f"/api/session/{sid}/decide", json={"kind": "advance_phase"}
    ).json()
    assert_clean(res, "advance_phase")
    seen.append(res)

    blob = json.dumps(seen, ensure_ascii=False)
    # 真相の本文が混ざっていない
    assert narrative_head not in blob
    # 誤導証拠の解説が事前に出ていない
    for text in sc.debrief.misleading_explanations.values():
        assert text.strip().splitlines()[0] not in blob
    # 論点の implication（解消しないと何が起きるか）はプレイ中に出さない
    for q in sc.open_questions:
        assert q.implication.strip().splitlines()[0] not in blob


def test_report_discloses_truth_only_after_finish(client):
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]

    # 終了前は 409
    assert client.get(f"/api/session/{sid}/report").status_code == 409

    client.post(
        f"/api/session/{sid}/decide", json={"kind": "advance_phase"}
    )
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": ["ws-042"]},
    )
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})

    rep = client.get(f"/api/session/{sid}/report").json()
    # ここで初めて開示される
    assert rep["truth"]["compromised"] == ["ws-042", "fs01"]
    assert rep["truth"]["misleading_evidence"] == ["ev_005", "ev_007"]


def test_scenarios_directory_is_not_served(client):
    for path in (
        "/static/../scenarios/ransomware-initial-response-01.yaml",
        "/scenarios/ransomware-initial-response-01.yaml",
        "/static/ransomware-initial-response-01.yaml",
    ):
        assert client.get(path).status_code in (403, 404, 405)


def test_records_endpoint_requires_scenario_id(client):
    """シナリオ横断のエンドポイントを作らない（SPEC 3.11 / 7.5.3）。"""
    assert client.get("/api/records").status_code == 422


def test_record_delete_rejects_path_traversal(client):
    for bad in ("..%2F..%2Fetc%2Fpasswd", "../../SPEC.md", "not-json.txt"):
        assert client.delete(f"/api/records/{bad}").status_code in (400, 404)


def test_saved_record_has_no_ground_truth(tmp_path):
    from irdojo.engine import Decision, Engine
    from irdojo.report import json as report_json
    from irdojo import records as records_mod

    sc = load_scenario("ransomware-initial-response-01")
    e = Engine(sc, "business_continuity", None)
    e.decide(Decision(kind="declare_assessment", assessment=["ws-042"]))
    e.decide(Decision(kind="finish"))
    report_json.build(e.state, sc)

    files = list(records_mod.results_dir().glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert_clean(saved, "保存された記録")
    assert sc.world.ground_truth.attack_narrative.strip().splitlines()[0] not in files[0].read_text(encoding="utf-8")


def test_hard_level_hides_assists(client):
    sc = load_scenario("ransomware-initial-response-01")
    res = client.post(
        "/api/session",
        json={"scenario_id": sc.meta.id, "assist_level": "hard"},
    ).json()
    v = res["view"]
    assert v["open_questions"] is None
    assert v["unresolved_count"] is None
    assert v["damage_history"] is None

    sid = res["session_id"]
    out = client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "action", "action_id": "act_collect_evtx_fs01"},
    ).json()
    # hard では要約を出さず、生ログだけを渡す
    assert all(e["summary"] is None for e in out["view"]["obtained_evidence"])
    assert all(e["content"] for e in out["view"]["obtained_evidence"])
    assert all(e["summary"] is None for e in out["revealed_evidence"])


def test_standard_level_hides_resolution_but_gives_count(client):
    sc = load_scenario("ransomware-initial-response-01")
    res = client.post(
        "/api/session",
        json={"scenario_id": sc.meta.id, "assist_level": "standard"},
    ).json()
    v = res["view"]
    assert v["open_questions"] is not None
    assert all(q["resolved"] is None for q in v["open_questions"])
    # 件数は critical に限らず全未解消論点（SPEC 7.4）
    assert v["unresolved_count"] == len(sc.open_questions)


def test_reading_and_description_follow_assist_level(client):
    """読解の補助はアシストの段階で切り替わる（SPEC 3.10）。"""
    sc = load_scenario("ransomware-initial-response-01")
    expected = {
        "assisted": (True, True),   # description, reading
        "standard": (True, False),
        "hard": (False, False),
    }
    for level, (want_desc, want_reading) in expected.items():
        res = client.post(
            "/api/session",
            json={"scenario_id": sc.meta.id, "assist_level": level},
        ).json()
        sid = res["session_id"]
        has_desc = any(a["description"] for a in res["view"]["available_actions"])
        assert has_desc is want_desc, level

        out = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": "act_collect_evtx_fs01"},
        ).json()
        has_reading = any(e["reading"] for e in out["view"]["obtained_evidence"])
        assert has_reading is want_reading, level


def test_reading_never_states_the_conclusion(client):
    """読み方に結論を書かない。書くと推論を代行してしまう（SPEC 3.10）。

    語彙表は `irdojo.schema.CONCLUSION_WORDS` に一本化してローダへ昇格した。
    ここで見張っていた語だけを検査していたころは、同じ結論が
    `content` や `command` へ移って通り抜けていた。
    テストとローダで別々の表を持つと、片方に足した語がもう片方に無くなる。
    """
    from irdojo.schema import CONCLUSION_WORDS

    sc = load_scenario("ransomware-initial-response-01")
    for word in ("侵害されている", "侵害された端末", "攻撃者の常套", "疑うべき",
                 "無関係である", "だからこの", "正常である"):
        assert word in CONCLUSION_WORDS, f"表から {word} が落ちている"
    for e in sc.evidence:
        for word in CONCLUSION_WORDS:
            assert word not in e.reading, f"{e.id}: reading が結論を述べている（{word}）"
    for a in sc.actions:
        for word in CONCLUSION_WORDS:
            assert word not in a.description, f"{a.id}: description が結論を述べている"


def test_misleading_readings_are_not_thinner(client):
    """誤導の注釈だけ薄いと、厚みがそのまま誤導の目印になる（SPEC 3.10）。"""
    sc = load_scenario("ransomware-initial-response-01")
    mis = [len(e.reading) for e in sc.evidence if e.misleading]
    real = [len(e.reading) for e in sc.evidence if not e.misleading]
    assert all(mis) and all(real)
    assert sum(mis) / len(mis) >= 0.7 * (sum(real) / len(real))


# 資料が自分の守備範囲を書くときの見出し。
# 「非対象」だけを見ると、ネガティブ所見にしか付かない印になっていないかを
# 測れない（範囲を書く欄はすべて同じ役割を持つ）
SCOPE_MARKERS = ("非対象", "収集範囲", "検査範囲", "取得範囲",
                 "集計対象", "照会対象", "検証範囲", "スキャン範囲")


def test_scope_lines_are_not_a_marker_for_negative_findings(client):
    """検査範囲の欄が、ネガティブ所見の目印になっていないこと。

    0件を返す証拠に「どこを見て、どこを見ていないか」を書かせる規則
    （SPEC 5.4）は、その欄がネガティブ所見にだけ付くと逆効果になる。
    「非対象欄がある＝この端末は白」を読む側に教えることになり、
    misleading に印を付けるのと変わらない。
    どちらの側にも同じくらい付いていることを機械に見張らせる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    neg = [e for e in sc.evidence if not e.points_to]
    pos = [e for e in sc.evidence if e.points_to]
    assert neg and pos

    def rate(group):
        marked = [e for e in group if any(m in e.content for m in SCOPE_MARKERS)]
        return len(marked) / len(group)

    r_neg, r_pos = rate(neg), rate(pos)
    assert r_neg > 0 and r_pos > 0, (
        f"範囲欄が片側にしか無い（なし {r_neg:.2f} / あり {r_pos:.2f}）"
    )
    assert abs(r_neg - r_pos) <= 0.35, (
        f"範囲欄の付き方が偏っている（なし {r_neg:.2f} / あり {r_pos:.2f}）"
    )


def test_negative_findings_are_not_thinner_than_the_rest(client):
    """0件を返す証拠だけ薄いと、厚みがそのまま「何も無い」の合図になる。

    誤導の注釈と同じ論法（test_misleading_readings_are_not_thinner）。
    読む前に長さで振り分けられるなら、読ませていることにならない。
    """
    sc = load_scenario("ransomware-initial-response-01")
    neg = [len(e.content) for e in sc.evidence if not e.points_to]
    pos = [len(e.content) for e in sc.evidence if e.points_to]
    assert neg and pos
    ratio = (sum(neg) / len(neg)) / (sum(pos) / len(pos))
    assert ratio >= 0.6, f"0件の証拠が薄すぎる（{ratio:.2f} 倍）"


def test_content_is_the_material_and_summary_is_the_reading(client):
    """content に要約を混ぜない。混ぜると hard という設計軸が死ぬ。

    `summary` は hard で伏せる。`content` は全レベルで出る。
    content の地の文が summary と同じ主張を述べていると、
    「要約を伏せて生ログを読ませる」ことができなくなる。

    ここで見るのは結論の言い回しだけで、**網羅検査ではない**
    （SPEC 5.4）。網羅は構造側の規則が担う。
    """
    from irdojo.schema import CONCLUSION_WORDS

    sc = load_scenario("ransomware-initial-response-01")
    for e in sc.evidence:
        for word in CONCLUSION_WORDS:
            assert word not in e.content, f"{e.id}: content が結論を述べている（{word}）"
    # summary は縛らない。一行で所見を言うのが summary の仕事である。
    # 実際に結論の言い回しを含む summary が残っていることを確かめる —
    # ここが空になったら、規則が summary まで飲み込んだということ
    stated = [e.id for e in sc.evidence
              if any(w in e.summary for w in CONCLUSION_WORDS)]
    assert stated, "結論を言う summary が1つも無い。アシストが消えている"


def test_phase_structure_is_disclosed_upfront(client):
    """演習の規則は先に開示する。答えではないので隠さない（SPEC 7.6.7）。"""
    sc = load_scenario("ransomware-initial-response-01")
    res = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()

    labels = [p["label"] for p in res["phases"]]
    assert labels == [p.label for p in sc.phases]
    # どのフェーズで被疑判定を求めるかを、押す前に知らせる
    assert any(p["requires_assessment"] for p in res["phases"])

    # プレイ画面でも現在地が分かる
    phases = res["view"]["phases"]
    assert [p["current"] for p in phases] == [True, False]
    assert all(p["done"] is False for p in phases)


def test_primer_follows_assist_level(client):
    """考え方の枠組みもアシストで切り替わる（SPEC 3.10）。"""
    sc = load_scenario("ransomware-initial-response-01")
    for level, want in (("assisted", True), ("standard", True), ("hard", False)):
        res = client.post(
            "/api/session",
            json={"scenario_id": sc.meta.id, "assist_level": level},
        ).json()
        assert res["view"]["show_primer"] is want, level


def test_scenario_text_has_no_raw_markdown(client):
    """シナリオ由来の文字列は textContent で入れる（SPEC 7.7.2）。

    マークダウンは解釈されないので、`**強調**` はそのまま画面に出てしまう。
    """
    sc = load_scenario("ransomware-initial-response-01")
    fields = []
    for e in sc.evidence:
        fields += [(e.id, e.summary), (e.id, e.content), (e.id, e.reading)]
    for a in sc.actions:
        fields += [(a.id, a.label), (a.id, a.description)]
    for q in sc.open_questions:
        fields += [(q.id, q.question), (q.id, q.implication)]
    for text in (sc.meta.briefing, sc.world.ground_truth.attack_narrative):
        fields.append(("meta", text))
    for owner, text in fields:
        assert "**" not in (text or ""), f"{owner}: 素のマークダウンが混ざっている"


def test_topology_is_disclosed_at_every_level(client):
    """図はアシストで切り替えるが、依存関係そのものは常に渡す（SPEC 7.6.7）。

    両方隠すと hard だけが「構成を知らずに上流を止めて事故る」演習になり、
    判断ではなく知識と運を測ることになる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    for level, want_diagram in (("assisted", True), ("standard", True), ("hard", False)):
        res = client.post(
            "/api/session",
            json={"scenario_id": sc.meta.id, "assist_level": level},
        ).json()
        view = res["view"]
        assert view["show_topology"] is want_diagram, level

        assets = view["assets"]
        assert len(assets) == len(sc.world.assets)
        by_id = {a["id"]: a for a in assets}
        # 依存関係はどのレベルでも渡す
        assert by_id["fs01"]["depends_on"] == ["dc01"], level
        # 採点の内部値は渡さない
        assert all("business_impact_per_hour" not in a for a in assets), level


def test_action_groups_do_not_leak(client):
    """分類名で答えを漏らさない（SPEC 5.6）。

    「無関係な端末」のような名前は、misleading にマークを付けるのと変わらない。
    """
    sc = load_scenario("ransomware-initial-response-01")
    NG = ("無関係", "本命", "誤導", "侵害され", "正解", "重要でない", "囮")
    for a in sc.actions:
        assert a.group, f"{a.id}: group が無い"
        for word in NG:
            assert word not in a.group, f"{a.id}: group が答えを漏らしている（{word}）"

    # 束はビューに載るが、採点には一切効かない
    res = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    groups = {a["group"] for a in res["view"]["available_actions"]}
    assert len(groups) >= 2


def test_group_says_no_more_than_the_label(client):
    """分類は「何を引くか」で決める。ラベルが言っていないことを分類で言わない。

    「メールゲートウェイのログを照合」を ws-042 の束に置くと、
    読む前からその結果が ws-042 の話だと分かってしまう。
    引く先は全社の仕組みであって、どの資産の話になるかは読むまで分からない。
    """
    import re

    sc = load_scenario("ransomware-initial-response-01")
    for asset in sc.world.assets:
        # 資産の label に括弧で入っている呼び名（「営業部端末 (佐藤)」→ 佐藤）も
        # 資産一覧と構成図に出ているので、ラベルが名指ししたものとみなす
        alias = re.findall(r"[（(]([^）)]+)[）)]", asset.label)
        for a in sc.actions:
            if asset.id not in a.group:
                continue
            named = asset.id in a.label or any(x in a.label for x in alias)
            assert named, (
                f"{a.id}: 分類が {asset.id} と言っているのに、"
                f"ラベル「{a.label}」はそれを名指ししていない"
            )


def test_open_questions_do_not_hand_over_the_assessment(client):
    """未解消論点の文が、侵害された資産を名指ししていない。

    論点は assisted / standard では **開始0分・0アクション**で画面の左に出る。
    そこに答えの資産名があると、被疑判定はログを1行も読まずに転記で済む。
    実際「ws-042 から fs01 へどう到達したか」の1行は、compromised と
    patient_zero の両方をこの時点で渡していた。

    ブリーフィングが既に名指しした資産（fs01）は除く。一次情報として
    渡したものを問い文で繰り返しても、新しいことは何も渡していない。
    """
    from irdojo.schema import asset_names, briefing_assets

    sc = load_scenario("ransomware-initial-response-01")
    gt = sc.world.ground_truth
    secret = set(gt.compromised) | {gt.patient_zero} | set(gt.persistence)
    secret -= briefing_assets(sc)
    assert secret, "検査対象が空になっている（ブリーフィングが答えを全部渡している）"

    created = client.post(
        "/api/session",
        json={"scenario_id": sc.meta.id, "assist_level": "assisted"},
    ).json()
    questions = created["view"]["open_questions"]
    assert questions, "assisted で論点が出ていない"

    for q in questions:
        text = q["label"] + " " + q["question"]
        for asset_id in secret:
            for name in asset_names(sc.asset_by_id[asset_id]):
                assert name not in text, (
                    f"{q['id']}: 開始直後の画面が「{name}」を名指ししている"
                )


def test_actions_do_not_name_assets_they_do_not_touch(client):
    """アクションの文が、そのアクションが触らない侵害資産を名指ししていない。

    アクション一覧は**開始0分・0アクション**で画面の右に全部並ぶ。
    `contain` は `requires_evidence` を持てないので（SPEC 5.6）、
    封じ込めの手は最初から全部読める。実際「ws-042 から fs01 への SMB を
    境界で遮断」という1行が、compromised の全量と横展開の答えを
    0手・0分で渡していた。被疑判定は対応フェーズ中も再宣言できるので、
    そのまま点になる。

    見るのは**学習者に配られる文**（label / group / description）である。
    `command` は押すまで返らないので、ここでは PlayerView 側を見る。

    通るのは実際の悪用経路そのもの — 何も調べずに宣言して対応フェーズへ入り、
    そこで初めて読める封じ込めの一覧から、判定を組み直す。
    """
    from irdojo.schema import briefing_assets, names_asset

    sc = load_scenario("ransomware-initial-response-01")
    gt = sc.world.ground_truth
    secret = set(gt.compromised) | {gt.patient_zero} | set(gt.persistence)
    secret -= briefing_assets(sc)
    assert secret, "検査対象が空になっている"

    sid = client.post(
        "/api/session",
        json={"scenario_id": sc.meta.id, "assist_level": "assisted"},
    ).json()["session_id"]
    res = client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": []},
    ).json()
    actions = res["view"]["available_actions"]
    assert any(sc.action_by_id[a["id"]].type.value == "contain" for a in actions), \
        "対応フェーズの封じ込めが一覧に出ていない（検査対象が空）"

    for a in actions:
        act = sc.action_by_id[a["id"]]
        own = set(act.targets) | set(act.investigates)
        text = " ".join(
            str(a.get(f) or "") for f in ("label", "group", "description")
        )
        for asset_id in secret - own:
            name = names_asset(text, sc.asset_by_id[asset_id])
            assert not name, (
                f"{a['id']}: 触らない資産「{name}」を開始直後の画面で名指ししている"
            )


def test_asset_names_are_matched_without_regard_to_case(scenario):
    """漏洩検査の照合は、大文字小文字を区別しない。

    コマンド行は慣習として `WS-042` や `FS01` と大文字で書く。
    区別する比較をしていたので、`smbutil check --from WS-042` は
    素通りしていた。読む側にとって `WS-042` と `ws-042` は同じ資産である。
    """
    from irdojo.schema import names_asset

    ws042 = scenario.asset_by_id["ws-042"]
    assert names_asset("$ smbutil check --from WS-042", ws042) == "ws-042"
    assert names_asset("$ smbutil check --from ws-042", ws042) == "ws-042"
    assert names_asset("$ smbutil check --to FS01", ws042) == ""


def test_the_loader_rejects_an_action_that_names_someone_elses_asset(raw_scenario):
    """新しい規則を間違って使ったシナリオを、ローダが拒否すること。

    規則が保証するのは「自分が触らない資産の名前は出ない」までである。
    封じ込めの手が自分の止める先を名乗るのは許す（そうでないと
    何をする手なのか分からない）。
    """
    import copy

    import yaml

    from irdojo.loader import ScenarioError, load_scenario_text

    data = copy.deepcopy(raw_scenario)
    for a in data["actions"]:
        if a["id"] == "act_block_smb_fs01":
            a["label"] = "ws-042 から fs01 への SMB を境界で遮断"
    with pytest.raises(ScenarioError) as exc:
        load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    assert "ws-042" in str(exc.value)

    # 大文字で書いても同じこと。ここが v1.35 まで素通りしていた
    data = copy.deepcopy(raw_scenario)
    for a in data["actions"]:
        if a["id"] == "act_block_smb_fs01":
            a["command"] = "$ smbutil check --from WS-042 --to FS01\n"
    with pytest.raises(ScenarioError) as exc:
        load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    assert "ws-042" in str(exc.value)


def test_a_fleet_wide_command_may_list_every_host(raw_scenario):
    """全端末を並べる出力は拒否しない。**全員の名指しは選り分けではない。**

    全端末に EDR のフルスキャンをかける手は、出力に全ホストを並べる。
    そこから読めるのは「どれも検知なし」であって、どれが侵害されたかではない。
    禁じたいのは選り分けであって、列挙ではない。
    """
    import copy

    import yaml

    from irdojo.loader import load_scenario_text

    data = copy.deepcopy(raw_scenario)
    hosts = [a["id"] for a in data["world"]["assets"]]
    for a in data["actions"]:
        if a["id"] == "act_edr_full_scan":
            a["command"] = "$ edrctl scan --all\n" + "".join(
                f"  {h.upper()} done 0 detections\n" for h in hosts
            )
    load_scenario_text(yaml.safe_dump(data, allow_unicode=True))   # 通ること

    # 1台だけ落とすと、それは選り分けである
    data = copy.deepcopy(raw_scenario)
    for a in data["actions"]:
        if a["id"] == "act_edr_full_scan":
            a["command"] = "$ edrctl scan --all\n  WS-042 done 1 detection\n"
    with pytest.raises(Exception):
        load_scenario_text(yaml.safe_dump(data, allow_unicode=True))


def test_declaring_without_investigating_cannot_reach_a_full_fact_score(scenario):
    """何も調べずに宣言したら、どう当てても事実認識層は満点にならない。

    当てずっぽうでも転記でも、資産の組み合わせは 5 台なら 32 通りしかない。
    そのどれかが満点になるなら、この演習は当てもの（あるいは
    画面のどこかからの転記）で解けることになる。未解消論点の重みが
    その穴を塞いでいる — 論点を1つも解消していない状態は、
    正しい判定であっても「根拠を示せていない」からである。
    """
    from itertools import combinations

    from irdojo import retrospective, scoring
    from irdojo.engine import Decision, Engine, InvalidDecision

    ids = [a.id for a in scenario.world.assets]
    best = 0.0
    for n in range(len(ids) + 1):
        for combo in combinations(ids, n):
            e = Engine(scenario, scenario.meta.default_policy, None)
            e.decide(Decision(kind="declare_assessment", assessment=list(combo)))
            e.decide(Decision(kind="finish"))
            r = scoring.score(e.state, scenario)
            assert e.state.elapsed_minutes == 0
            best = max(best, r.fact_score)

    assert best < 1.0, f"0手で事実認識層が満点になる（{best}）"

    # 調べたプレイには届かないこと。ここが逆転すると、調べる意味が消える
    minutes, path, _exact = retrospective.minimal_path(scenario)
    e = Engine(scenario, scenario.meta.default_policy, None)
    todo = list(path)
    while todo:
        for aid in list(todo):
            try:
                e.decide(Decision(kind="action", action_id=aid))
            except InvalidDecision:
                continue
            todo.remove(aid)
            break
        else:
            break
    truth = list(scenario.world.ground_truth.compromised)
    e.decide(Decision(kind="declare_assessment", assessment=truth))
    e.decide(Decision(kind="finish"))
    skilled = scoring.score(e.state, scenario).fact_score
    assert best < skilled, f"0手 {best} が最短経路 {skilled} に並んでいる（{minutes}分）"


def test_command_transcripts_say_no_more_than_the_action(client):
    """実行の記録に「見つかったこと」を書かない（SPEC 5.6）。

    何をしたかは見せてよいが、結果は証拠の側の仕事である。
    """
    sc = load_scenario("ransomware-initial-response-01")
    NG = ("侵害", "攻撃者", "マルウェア", "不審", "疑わ", "検体を発見", "痕跡を発見")
    for a in sc.actions:
        assert a.command, f"{a.id}: command が無い"
        for word in NG:
            assert word not in a.command, f"{a.id}: command が結果を述べている（{word}）"

    # 証拠の中身をそのまま貼っていないこと
    for a in sc.actions:
        for e in sc.evidence:
            body = [l.strip() for l in e.content.splitlines() if len(l.strip()) > 24]
            for line in body:
                assert line not in a.command, f"{a.id}: {e.id} の中身を先出ししている"


def test_command_is_returned_only_after_the_action_is_taken(client):
    """端末の記録は押した後にだけ返る。押す前の一覧には載せない。

    載せると、そこに書かれた件数が機構ではなく結論になる。
    「0 detections ×5ホスト」は 35分かけて買うはずの所見であり、
    それを押す前に読めるなら、その手は押さないのが最適になる。
    どのアシストレベルでも同じ（hard で伏せる話ではなく、構造の話）。
    """
    sc = load_scenario("ransomware-initial-response-01")
    for level in ("assisted", "standard", "hard"):
        res = client.post(
            "/api/session",
            json={"scenario_id": sc.meta.id, "assist_level": level},
        ).json()
        sid = res["session_id"]
        acts = res["view"]["available_actions"]
        assert acts, level
        for a in acts:
            assert "command" not in a, f"{level}: {a['id']} が押す前に command を配っている"

        # 押した後は返る。何をしたのかは見せる（SPEC 7.6.8）
        aid = acts[0]["id"]
        out = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": aid},
        ).json()
        assert out["command"].strip(), f"{level}: 押した後に command が返っていない"


def test_pressing_a_dead_end_returns_a_transcript_with_nothing_in_it(client):
    """世界に奪われた後にその手を押すと、記録は返るが中身は返らない。

    fs01 は 200分に現場判断で再起動され、ev_010（稼働中プロセス）は消える。
    その後で「fs01 のメモリダンプ取得・解析」を押すと、証拠は出てこない。
    このとき端末の記録に PID やハンドル数が書かれていると、
    **取り損ねたものの中身を、失った後に見せている**ことになる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    res = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = res["session_id"]

    def act(aid):
        return client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": aid},
        ).json()

    # 200分を越えるまで時計を進める（tl_fs01_restart が ev_010 を奪う）
    burned = 0
    for a in sc.actions:
        if a.type.value != "investigate" or a.id == "act_memory_dump_fs01":
            continue
        if a.requires_evidence:
            continue
        out = act(a.id)
        burned += a.cost_minutes
        if burned > 210:
            break
    assert burned > 200, f"時計が進みきっていない（{burned}分）"

    out = act("act_memory_dump_fs01")
    assert not out["revealed_evidence"], "ev_010 がまだ取れている。前提が崩れた"

    # 行ごと貼っていないかではなく、ev_010 にしか無い値が届いていないかを見る。
    # 実際に踏んだ形（`--pid 6644` / `12,847 handles`）は行の貼り付けではなく、
    # 中身から抜いた値の再掲だった
    from irdojo.schema import _id_tokens

    others = " ".join(e.content for e in sc.evidence if e.id != "ev_010")
    only = _id_tokens(sc.evidence_by_id["ev_010"].content) - _id_tokens(others)
    assert only, "ev_010 に固有の値が無いなら、この検査は意味がない"
    for token in sorted(only):
        assert token not in out["command"].replace(",", ""), (
            f"失った ev_010 にしか無い値 {token} が端末の記録で届いている"
        )


def test_pre_press_view_prices_an_action_by_cost_only(client):
    """押す前に渡してよいのは、ラベル（世界の言葉）と所要（費用）まで。

    「この手は何件返すか」が押す前に分かると、選択が賭けでなくなる。
    ActionView の項目を固定して、次に何かを足すときに必ずここを通す。
    """
    sc = load_scenario("ransomware-initial-response-01")
    res = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    allowed = {
        "id", "label", "description", "type", "cost_minutes",
        "executed", "selectable", "phase", "phase_label", "group",
    }
    for a in res["view"]["available_actions"]:
        extra = set(a) - allowed
        assert not extra, f"{a['id']}: 押す前に渡している余分な項目 {sorted(extra)}"


def test_transcript_does_not_hand_over_evidence_that_was_lost(client):
    """奪われた後にその手を押しても、端末の記録が中身を渡さない。

    `command` は押した後に返るので、ふつうは証拠と同時に届く重複でしかない。
    失われうる証拠だけは違う — 証拠が出てこない回にも記録は返るので、
    そこに中身が書かれていると、取り損ねたものを失った後に見せることになる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    losable = {e.id for e in sc.evidence if e.volatile}
    for ev in sc.timeline:
        losable |= set(ev.destroys)
    for act in sc.actions:
        losable |= set(act.destroys)
    assert losable, "失われうる証拠が無いなら、この検査は意味がない"

    for a in sc.actions:
        for eid in a.yields:
            if eid not in losable:
                continue
            body = sc.evidence_by_id[eid].content
            for line in body.splitlines():
                token = line.strip()
                if len(token) < 12:
                    continue
                assert token not in a.command, (
                    f"{a.id}: 失われうる {eid} の中身が端末の記録に入っている"
                )


def test_bend_is_silent_during_play(client):
    """折れ点の位置はプレイ中どこにも漏れない。

    跳ねたと分かるのは曲線の形だけで、いつ跳ねるかは答えの一部である。
    `damage_history` は曲線そのものなので出すが、
    折れ点を名指しするキーはどこにも無い。
    """
    sc = load_scenario("ransomware-initial-response-01")
    created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = created["session_id"]
    last = created
    crossed = False
    for _ in range(40):
        avail = [
            a for a in last["view"]["available_actions"]
            if a["selectable"] and a["type"] == "investigate"
        ]
        if not avail:
            break
        last = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": avail[0]["id"]},
        ).json()
        if last["view"]["elapsed_minutes"] > sc.damage.acceleration.threshold_minutes:
            crossed = True
        for path, key, _ in walk(last):
            assert key not in (
                "acceleration", "threshold_minutes", "multiplier", "markers",
            ), f"{path}.{key} が漏れている"
    assert crossed, "折れ点まで進んでいない"


def test_bend_is_named_in_the_debrief(client):
    """講評は答えを開ける場所。踏んだなら、いつ跳ねたのかを名指しする。"""
    sc = load_scenario("ransomware-initial-response-01")
    bend = sc.damage.acceleration.threshold_minutes
    created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = created["session_id"]
    last = created
    for _ in range(40):
        avail = [
            a for a in last["view"]["available_actions"]
            if a["selectable"] and a["type"] == "investigate"
        ]
        if not avail:
            break
        last = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": avail[0]["id"]},
        ).json()
    client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": ["ws-042"]},
    )
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()
    world = [m for m in rep["markers"] if m["kind"] == "world"]
    assert len(world) == 1
    assert world[0]["minute"] == bend


def test_bend_is_not_named_if_never_reached(client):
    """踏まなかったプレイに折れ点を描いても、起きなかったことの図でしかない。"""
    sc = load_scenario("ransomware-initial-response-01")
    created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = created["session_id"]
    client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": ["ws-042"]},
    )
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()
    assert [m for m in rep["markers"] if m["kind"] == "world"] == []


def test_records_fold_into_one_row_per_condition(client, tmp_path, monkeypatch):
    """同じ条件で何度やっても ⑥ は1行。比較したい軸が縦に埋もれない。"""
    monkeypatch.setenv("IRDOJO_HOME", str(tmp_path))
    sc = load_scenario("ransomware-initial-response-01")

    def play(policy_id, assist):
        created = client.post("/api/session", json={
            "scenario_id": sc.meta.id,
            "policy_id": policy_id,
            "assist_level": assist,
        }).json()
        sid = created["session_id"]
        client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
        client.post(f"/api/session/{sid}/decide",
                    json={"kind": "declare_assessment", "assessment": ["ws-042"]})
        client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
        return client.get(f"/api/session/{sid}/report").json()

    for _ in range(6):
        play("business_continuity", "assisted")
    rep = play("evidence_preservation", "hard")

    assert rep["records_total"] == 7
    groups = {(g["policy_id"], g["assist_level"]): g for g in rep["record_groups"]}
    assert len(groups) == 2, "条件は2つしか使っていない"
    assert groups[("business_continuity", "assisted")]["plays"] == 6
    assert groups[("business_continuity", "assisted")]["has_first_play"] is True
    assert groups[("evidence_preservation", "hard")]["is_current"] is True

    # 代表はその条件の最高スコア
    best = max(r["composite_score"] for r in rep["records"]
               if r["policy_label"] == groups[("business_continuity", "assisted")]["policy_label"])
    assert groups[("business_continuity", "assisted")]["composite_score"] == best


def test_record_log_is_capped(client, tmp_path, monkeypatch):
    """履歴は直近だけ。★初回は groups が持っているので落ちても消えない。"""
    from irdojo.report.json import RECENT_RECORDS

    monkeypatch.setenv("IRDOJO_HOME", str(tmp_path))
    sc = load_scenario("ransomware-initial-response-01")
    rep = None
    for _ in range(RECENT_RECORDS + 5):
        created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
        sid = created["session_id"]
        client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
        client.post(f"/api/session/{sid}/decide",
                    json={"kind": "declare_assessment", "assessment": ["ws-042"]})
        client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
        rep = client.get(f"/api/session/{sid}/report").json()

    assert rep["records_total"] == RECENT_RECORDS + 5
    assert len(rep["records"]) == RECENT_RECORDS
    assert not any(r["is_first_play"] for r in rep["records"]), "初回はもう履歴から外れている"
    assert any(g["has_first_play"] for g in rep["record_groups"]), "★初回が消えている"


def test_untried_conditions_cover_every_policy(client, tmp_path, monkeypatch):
    """「まだ試していない条件」は全方針から引く。

    replay_suggestions は著者が推す組だけなので、既定方針のように
    推薦の無い方針が黙って抜け落ちる。
    """
    monkeypatch.setenv("IRDOJO_HOME", str(tmp_path))
    sc = load_scenario("ransomware-initial-response-01")
    created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = created["session_id"]
    client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
    client.post(f"/api/session/{sid}/decide",
                json={"kind": "declare_assessment", "assessment": ["ws-042"]})
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()

    assert {p["id"] for p in rep["policies"]} == {p.id for p in sc.policies}
    suggested = {s["policy"] for s in rep["replay_suggestions"]}
    assert suggested < {p.id for p in sc.policies}, "推薦が全方針を覆うなら、この差は現れない"


def test_incoming_is_rendered_outside_the_result_box():
    """着信は押した結果の箱に入れない（SPEC 7.6.8）。

    結果の中に混ぜると生ログと証拠に挟まれ、「結果の続き」として
    読み飛ばされる。画面の構造そのものを機械的に見張る。
    """
    app_js = (pathlib.Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    start = app_js.index("function renderResult(")
    end = app_js.index("\nfunction ", start + 1)
    body = app_js[start:end]
    assert "events" not in body, "renderResult が events を描いている"
    assert "incoming" not in body, "renderResult が着信の枠を描いている"

    assert "function renderIncoming(" in app_js
    index_html = (
        pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html"
    ).read_text(encoding="utf-8")
    # 着信の枠は結果より前（＝上）に置く
    assert index_html.index('id="incoming-feed"') < index_html.index('id="result-area"')


def test_world_losses_are_attributed_in_the_debrief(client):
    """世界の側に奪われたものは、誰に・いつ奪われたかを講評で言う。

    プレイ中は何も言わない（見ていないものが失われたことは分からない）。
    だからこそ講評で説明がつかないと、ただの理不尽になる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    world_ev = {d for e in sc.timeline for d in e.destroys}
    assert world_ev, "世界の側が奪うものが無い"

    created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = created["session_id"]
    last = created
    for _ in range(40):
        avail = [
            a for a in last["view"]["available_actions"]
            if a["selectable"] and a["type"] == "investigate"
        ]
        if not avail:
            break
        last = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": avail[0]["id"]},
        ).json()
    client.post(f"/api/session/{sid}/decide", json={"kind": "advance_phase"})
    client.post(f"/api/session/{sid}/decide",
                json={"kind": "declare_assessment", "assessment": ["ws-042"]})
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()

    taken = [l for l in rep["lost_evidence"] if l["by_world"]]
    assert taken, "全部押しても世界に奪われていない（歯が無い）"
    labels = {e.label for e in sc.timeline}
    for l in taken:
        assert l["destroyed_by"] in labels, "誰に奪われたか言えていない"
        assert l["at_minute"] > 0, "いつ奪われたか言えていない"
        assert l["obtainable_by"], "どうすれば取れたか言えていない"


def test_the_two_places_that_decide_nothing_happened_agree():
    """「空振り」の判定は、エンジンと画面の二重にある（SPEC 7.6.8）。

    片方だけ直すと、緑の完了メッセージの下にオレンジの
    「何も出てこなかった」が並ぶ。実際に2回それをやっている:
    連絡（証拠を産まない）と、既に止めてある資産への2手目
    （同じ資産に手が2つあるので普通に起きる）。

    ここで見張るのは文言ではなく、**両方が同じ項目を数えていること**。
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function renderResult(")
    end = app_js.index("\nfunction ", start + 1)
    body = app_js[start:end]
    i = body.index("var empty =")
    statement = body[i:body.index(";", i)]

    for name in ("revealed", "stopped", "halted", "already", "prevented"):
        assert name in statement, f"画面側の空振り判定が {name} を数えていない"

    # エンジン側（ActionOutcome.empty）と項目が揃っていること
    engine_py = (root / "irdojo" / "engine.py").read_text(encoding="utf-8")
    j = engine_py.index("    def empty(self)")
    prop = engine_py[j:engine_py.index("\n\n", j)]
    for name in ("revealed", "contained", "halted", "already", "prevented"):
        assert f"self.{name}" in prop, f"エンジン側の空振り判定が {name} を数えていない"


def test_the_projection_is_never_shown_during_play(client):
    """「この先こう伸びる」は講評でしか言わない（原則5 / SPEC 7.6.9）。

    復旧地平の分は、手を止めた時点の封じ込め状態が決める。プレイ中に
    出すと「まだ止めていないもの」の代償を先に告げることになり、
    盤面を見ずに数字だけを追う遊びになる。ここは損失の開示と同じ扱いで、
    講評で初めて開ける。

    プレイ中の `accumulated_damage` と講評の `total_damage` は
    別の数字なので、講評は二段で出す必要がある — 片方だけ直すと
    「グラフの終点」と「講評の被害額」が食い違ったまま残る。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()[
        "session_id"
    ]

    def act(aid):
        return client.post(
            f"/api/session/{sid}/decide", json={"kind": "action", "action_id": aid}
        ).json()

    act("act_collect_evtx_fs01")
    body = act("act_smb_session_fs01")
    text = json.dumps(body, ensure_ascii=False)
    assert "projected" not in text, "プレイ中に復旧地平の被害を出している"
    assert "projection" not in text
    view = body["view"]
    assert "accumulated_damage" in view
    assert "total_damage" not in view

    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": ["fs01", "ws-042"]},
    )
    act("act_shutdown_fs01")
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})

    rep = client.get(f"/api/session/{sid}/report").json()
    cons = rep["score"]["consequences"]
    assert cons["projected_damage"] > 0, "講評でも地平を出していない"
    assert cons["total_damage"] == pytest.approx(
        cons["accumulated_damage"] + cons["projected_damage"], abs=0.01
    )
    # グラフの破線。実線の終端から続き、講評にしか無い
    assert rep["damage_projection"], "延長線が講評に無い"
    assert rep["damage_projection"][0] >= rep["damage_history"][-1]
    assert len(rep["damage_projection"]) == rep["recovery_horizon_minutes"]


def test_the_debrief_shows_the_damage_in_two_tiers():
    """講評の被害は二段で出す（SPEC 7.6.9）。

    プレイ中に見えていたのは積分の本体だけで、破線の分は
    手を止めた時点の封じ込め状態が決めている。1つの数字に丸めると、
    封じ込めの巧拙が数字から消え、講評だけ跳ねたように見える。
    """
    app_js = (pathlib.Path(__file__).resolve().parents[1] / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    start = app_js.index("function blockTruth(")
    end = app_js.index("\nfunction ", start + 1)
    body = app_js[start:end]
    assert "accumulated_damage" in body, "累積被害を出していない"
    assert "projected_damage" in body, "復旧までの見込みを出していない"
    assert "total_damage" in body, "合計を出していない"
    assert "projection: rep.damage_projection" in body, "グラフに延長線を渡していない"

    chart_js = (
        pathlib.Path(__file__).resolve().parents[1] / "web" / "chart.js"
    ).read_text(encoding="utf-8")
    assert "setLineDash" in chart_js
    # 破線の下は塗らない。積み上がった被害と見分けが付かなくなる
    assert "ctx.lineTo(px(series.length - 1), padT + h);" in chart_js
