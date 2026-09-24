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


@pytest.fixture
def raw_oauth():
    """2本目の生データ。**呼び名まわりの検査はこちらでしか歯が見えない。**

    1本目は端末を id（`ws-042`）で呼び、資料の中でもそう書く。
    2本目は SaaS で、同じものを資料が「受信箱」と呼ぶ。
    `aliases` の穴は、盤面が1本しか無い間は原理的に露出しなかった。
    """
    import yaml

    path = SCENARIO_DIR / "oauth-consent-abuse-01.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))

# プレイ中のレスポンスに現れてはならないキー
FORBIDDEN_KEYS = {
    # 答えそのもの
    "ground_truth", "compromised", "persistence", "innocent",
    "patient_zero", "attack_narrative", "misleading", "refuted_by",
    # 棄却の条件も答えの側。「2つ揃わないと棄却できない」と分かれば、
    # その誤導が誤導であることを言ったのと同じになる
    "refutation_mode",
    # 選択を賭けでなくする構造化情報。
    # `clears` は points_to の逆向きで、**印としてはより強い** —
    # 「この所見は白く見えるだけだ」と教えるのと同じになる（SPEC 3.4）
    "points_to", "clears", "yields", "destroys", "investigates", "targets",
    # 仕掛けの手が何を守るかは答えの側（SPEC 5.6.3）。
    # **渡すと「失われうる証拠の一覧」になる。**
    # `requires_actions` も同じで、まだ見えていない手の存在を教える
    "secures", "requires_actions",
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
    assert rep["truth"]["compromised"] == ["ws-042", "fs01", "ws-055"]
    assert rep["truth"]["misleading_evidence"] == ["ev_005", "ev_007", "ev_020"]


def test_scenarios_directory_is_not_served(client):
    for path in (
        "/static/../scenarios/ransomware-initial-response-01.yaml",
        "/scenarios/ransomware-initial-response-01.yaml",
        "/static/ransomware-initial-response-01.yaml",
    ):
        assert client.get(path).status_code in (403, 404, 405)


def test_the_screen_is_never_cached(client):
    """画面は一度もキャッシュさせない（SPEC 7.6.21）。

    **直したのに古いまま、が実際に起きた。** `FileResponse` も
    `StaticFiles` も `ETag` と `Last-Modified` を付けるので、
    再読み込みすれば新しいものを取る — **再読み込みしないかぎり取らない。**
    開いたままのタブは前の版を表示し続け、利用者には
    「直したと言っているのに無い」としか見えない。

    この道具は 127.0.0.1 でしか動かず、開発と学習が同じ画面で行われる。
    **転送量より、目の前の画面が本物であることのほうが大事である。**
    """
    for path in ("/", "/static/app.js", "/static/style.css", "/static/index.html"):
        head = client.get(path).headers.get("cache-control", "")
        assert "no-store" in head, f"{path} がキャッシュされうる: {head!r}"


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


def test_reading_never_states_the_conclusion(client, any_scenario):
    """読み方に結論を書かない。書くと推論を代行してしまう（SPEC 3.10）。

    語彙表は `irdojo.schema.CONCLUSION_WORDS` に一本化してローダへ昇格した。
    ここで見張っていた語だけを検査していたころは、同じ結論が
    `content` や `command` へ移って通り抜けていた。
    テストとローダで別々の表を持つと、片方に足した語がもう片方に無くなる。
    """
    from irdojo.schema import CONCLUSION_WORDS

    sc = any_scenario
    for word in ("侵害されている", "侵害された端末", "攻撃者の常套", "疑うべき",
                 "無関係である", "だからこの", "正常である"):
        assert word in CONCLUSION_WORDS, f"表から {word} が落ちている"
    for e in sc.evidence:
        for word in CONCLUSION_WORDS:
            assert word not in e.reading, f"{e.id}: reading が結論を述べている（{word}）"
    for a in sc.actions:
        for word in CONCLUSION_WORDS:
            assert word not in a.description, f"{a.id}: description が結論を述べている"


def test_misleading_readings_are_not_thinner(client, any_scenario):
    """誤導の注釈が、本物より薄くも厚くもないこと（SPEC 3.10）。

    薄いほうは最初から見ていた。厚みがそのまま誤導の目印になるからである。
    **厚いほうを見ていなかった。** 「注釈がやたら長いものを疑う」は
    assisted で実際に効く読みで、1本目は誤導の reading が本物の 1.43倍、
    長さ上位5件のうち2件が誤導だった（誤導は21件中3件）。
    片側の帯は、誤導を薄く書く作者だけを止めて、
    誤導に力を入れる作者を素通りさせる。後者のほうが起きやすい。
    """
    sc = any_scenario
    mis = [len(e.reading) for e in sc.evidence if e.misleading]
    real = [len(e.reading) for e in sc.evidence if not e.misleading]
    assert all(mis) and all(real)
    ratio = (sum(mis) / len(mis)) / (sum(real) / len(real))
    assert 0.7 <= ratio <= 1.3, (
        f"誤導の reading が本物の {ratio:.2f} 倍（帯は 0.7〜1.3）。"
        "薄くても厚くても、読む前に長さで振り分けられる"
    )


def test_possibilities_are_not_a_marker_for_misleading_evidence(any_scenario):
    """可能性の列挙の数と厚みが、誤導の目印になっていないこと（SPEC 3.10）。

    `reading` と同じ論法を、新しい欄にも最初から**両側**で当てる。
    誤導ほど丁寧に可能性を並べたくなるのは書き手の自然な衝動だが、
    それをやると「列挙が厚いものを疑え」が読みとして成立してしまう。

    **書かれていない盤面は対象外にする。** 欄は任意で、書くかどうかは
    盤面ごとの判断である（書かれているのに片側だけ薄い、が禁じたいこと）。
    「そもそも書いていない」のほうは tools/balance.py が別に見張る。
    """
    sc = any_scenario
    mis = [e for e in sc.evidence if e.misleading and e.possibilities]
    real = [e for e in sc.evidence if not e.misleading and e.possibilities]
    if not (mis and real):
        pytest.skip(f"{sc.meta.id}: 可能性の列挙がまだ無い")

    def shape(group):
        n = sum(len(e.possibilities) for e in group) / len(group)
        c = sum(sum(len(t) for t in e.possibilities) for e in group) / len(group)
        return n, c

    (mn, mc), (pn, pc) = shape(mis), shape(real)
    assert 0.7 <= mn / pn <= 1.3, f"列挙の数が偏っている（{mn / pn:.2f} 倍）"
    assert 0.7 <= mc / pc <= 1.3, f"列挙の長さが偏っている（{mc / pc:.2f} 倍）"


def test_possibilities_do_not_repeat_the_reading(any_scenario):
    """読み方と可能性で、同じ文を2度読ませないこと。

    どちらも assisted にだけ出る。同じことが2度並ぶと、カードが伸びた
    ぶんだけ結果が画面の外へ落ちるだけで、渡した情報は増えていない。

    **見るのは、そのまま重なっている文だけである。** 言い換えは拾えない —
    実際 v1.46 では 3件（ev_012 / ev_017 / ev_019）が言い換えで重複しており、
    見つけたのはこの検査ではなく**スクリーンショットを読んだとき**だった。
    ここは「一度見つけた重なりは二度と戻らない」ための止め具で、
    言い換えの側は 8.3 のチェックリストと目視が受け持つ
    （CONCLUSION_WORDS と同じ役割分担）。
    """
    sc = any_scenario
    for e in sc.evidence:
        for t in e.possibilities:
            for part in (x.strip() for x in t.split("。") if len(x.strip()) >= 12):
                assert part not in e.reading, (
                    f"{e.id}: 「{part}」が reading と列挙の両方にある"
                )


def test_possibilities_never_leak_what_the_board_holds_back(any_scenario):
    """列挙の漏洩検査を、盤面の側からも回す（判定は possibility_leaks）。

    ローダは最初の1件で止まるので、**読み込めた＝漏れが無い**ではある。
    それでも同梱シナリオに対して名前で回しておく — 検査を緩めたときに
    落ちるのは、ローダではなくこちらになる（緩めた本人はローダしか見ない）。
    """
    from irdojo.schema import possibility_leaks

    leaks = possibility_leaks(any_scenario)
    assert not leaks, f"{any_scenario.meta.id}: 列挙が渡している: {leaks[:3]}"


def test_possibilities_follow_the_assist_level(client):
    """考えられることもアシストで切り替わる。`reading` と同じ扱い（SPEC 3.10）。

    読み方を渡さない相手に可能性だけ渡しても、生ログとの対応が付かない。
    `hard` は生ログだけを読ませる設計なので、ここが出たら軸が死ぬ。
    """
    sc = load_scenario("ransomware-initial-response-01")
    act = next(a.id for a in sc.actions if not a.requires_evidence)
    for level, want in (("assisted", True), ("standard", False), ("hard", False)):
        res = client.post(
            "/api/session",
            json={"scenario_id": sc.meta.id, "assist_level": level},
        ).json()
        sid = res["session_id"]
        out = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": act},
        ).json()
        got = [e["possibilities"] for e in out["revealed_evidence"]]
        assert got, level
        for value in got:
            if want:
                assert value and len(value) >= 2, level
            else:
                # **空リストではなく None。** 「このレベルでは出さない」と
                # 「この証拠には書かれていない」を混ぜると、計器が分けられない
                assert value is None, level


# 資料が自分の守備範囲を書くときの見出し。
# 「非対象」だけを見ると、ネガティブ所見にしか付かない印になっていないかを
# 測れない（範囲を書く欄はすべて同じ役割を持つ）
SCOPE_MARKERS = ("非対象", "収集範囲", "検査範囲", "取得範囲",
                 "集計対象", "照会対象", "検証範囲", "スキャン範囲")


def test_scope_lines_are_not_a_marker_for_negative_findings(client, any_scenario):
    """検査範囲の欄が、ネガティブ所見の目印になっていないこと。

    0件を返す証拠に「どこを見て、どこを見ていないか」を書かせる規則
    （SPEC 5.4）は、その欄がネガティブ所見にだけ付くと逆効果になる。
    「非対象欄がある＝この端末は白」を読む側に教えることになり、
    misleading に印を付けるのと変わらない。
    どちらの側にも同じくらい付いていることを機械に見張らせる。
    """
    sc = any_scenario
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


def test_negative_findings_are_not_thinner_than_the_rest(client, any_scenario):
    """0件を返す証拠だけ薄いと、厚みがそのまま「何も無い」の合図になる。

    誤導の注釈と同じ論法（test_misleading_readings_are_not_thinner）。
    読む前に長さで振り分けられるなら、読ませていることにならない。
    """
    sc = any_scenario
    neg = [len(e.content) for e in sc.evidence if not e.points_to]
    pos = [len(e.content) for e in sc.evidence if e.points_to]
    assert neg and pos
    ratio = (sum(neg) / len(neg)) / (sum(pos) / len(pos))
    assert ratio >= 0.6, f"0件の証拠が薄すぎる（{ratio:.2f} 倍）"


def test_content_is_the_material_and_summary_is_the_reading(client, any_scenario):
    """content に要約を混ぜない。混ぜると hard という設計軸が死ぬ。

    `summary` は hard で伏せる。`content` は全レベルで出る。
    content の地の文が summary と同じ主張を述べていると、
    「要約を伏せて生ログを読ませる」ことができなくなる。

    ここで見るのは結論の言い回しだけで、**網羅検査ではない**
    （SPEC 5.4）。網羅は構造側の規則が担う。
    """
    from irdojo.schema import CONCLUSION_WORDS

    sc = any_scenario
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


def test_scenario_text_has_no_raw_markdown(client, any_scenario):
    """シナリオ由来の文字列は textContent で入れる（SPEC 7.7.2）。

    マークダウンは解釈されないので、`**強調**` はそのまま画面に出てしまう。
    """
    sc = any_scenario
    fields = []
    for e in sc.evidence:
        fields += [(e.id, e.summary), (e.id, e.content), (e.id, e.reading)]
    for a in sc.actions:
        fields += [(a.id, a.label), (a.id, a.description)]
    for q in sc.open_questions:
        fields += [(q.id, q.question), (q.id, q.implication)]
    for text in (sc.meta.briefing, sc.world.ground_truth.attack_narrative):
        fields.append(("meta", text))
    # **講評に出る文字列が抜けていた**（v1.42）。誤導の説明も key_lessons も
    # 同じ経路で画面に入るのに、この一覧に無かったので `**強調**` が
    # そのまま出た。拾ったのは playtest で、テストは通っていた
    for pol in sc.policies:
        fields.append((pol.id, pol.briefing))
    for eid, text in sc.debrief.misleading_explanations.items():
        fields.append((eid, text))
    for i, text in enumerate(sc.debrief.key_lessons):
        fields.append((f"key_lessons[{i}]", text))
    for i, hint in enumerate(sc.debrief.replay_suggestions):
        fields.append((f"replay_suggestions[{i}]", hint.hint))
    for ev in sc.timeline:
        fields += [(ev.id, ev.text), (ev.id, ev.averted_text)]
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


def test_action_groups_do_not_leak(client, any_scenario):
    """分類名で答えを漏らさない（SPEC 5.6）。

    「無関係な端末」のような名前は、misleading にマークを付けるのと変わらない。
    """
    sc = any_scenario
    NG = ("無関係", "本命", "誤導", "侵害され", "正解", "重要でない", "囮")
    for a in sc.actions:
        assert a.group, f"{a.id}: group が無い"
        for word in NG:
            assert word not in a.group, f"{a.id}: group が答えを漏らしている（{word}）"

    # 束はビューに載るが、採点には一切効かない
    res = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    groups = {a["group"] for a in res["view"]["available_actions"]}
    assert len(groups) >= 2


def test_group_says_no_more_than_the_label(client, any_scenario):
    """分類は「何を引くか」で決める。ラベルが言っていないことを分類で言わない。

    「メールゲートウェイのログを照合」を ws-042 の束に置くと、
    読む前からその結果が ws-042 の話だと分かってしまう。
    引く先は全社の仕組みであって、どの資産の話になるかは読むまで分からない。

    **照合は `names_asset` で行う。** かつては `asset.id in a.group` だけを
    見ていた。1本目は束の名前を「fs01（ファイルサーバ）」のように id 込みで
    書いていたので気づかなかったが、束を「クラウドメール」と label だけで
    書いた瞬間、この検査は何も見なくなる — 資産 id はラテン文字で、
    日本語の束名には決して現れない。ローダ側の漏洩検査（`names_asset`）が
    id・label・括弧内の呼び名の3つを見ているのに、こちらだけ id を見ていた。
    """
    from irdojo.schema import names_asset

    sc = any_scenario
    for asset in sc.world.assets:
        for a in sc.actions:
            said = names_asset(a.group, asset)
            if not said:
                continue
            assert names_asset(a.label, asset), (
                f"{a.id}: 分類「{a.group}」が {asset.id} と言っているのに、"
                f"ラベル「{a.label}」はそれを名指ししていない"
            )


def test_open_questions_do_not_hand_over_the_assessment(client, any_scenario):
    """未解消論点の文が、侵害された資産を名指ししていない。

    論点は assisted / standard では **開始0分・0アクション**で画面の左に出る。
    そこに答えの資産名があると、被疑判定はログを1行も読まずに転記で済む。
    実際「ws-042 から fs01 へどう到達したか」の1行は、compromised と
    patient_zero の両方をこの時点で渡していた。

    ブリーフィングが既に名指しした資産（fs01）は除く。一次情報として
    渡したものを問い文で繰り返しても、新しいことは何も渡していない。
    """
    from irdojo.schema import asset_names, briefing_assets

    sc = any_scenario
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


def test_actions_do_not_name_assets_they_do_not_touch(client, any_scenario):
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

    sc = any_scenario
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
        own = (set(act.targets) | set(act.investigates)
               | set(act.restores) | set(act.hardens))
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


def test_command_transcripts_say_no_more_than_the_action(client, any_scenario):
    """実行の記録に「見つかったこと」を書かない（SPEC 5.6）。

    何をしたかは見せてよいが、結果は証拠の側の仕事である。
    """
    sc = any_scenario
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
    """失われた後にその手を押すと、記録は返るが中身は返らない。

    ev_010（fs01 の稼働中プロセス）は、現場の再起動でも、自分で行う
    セーフモード再起動でも消える。その後で「fs01 のメモリダンプ取得・解析」を
    押すと、証拠は出てこない。このとき端末の記録に PID やハンドル数が
    書かれていると、**取り損ねたものの中身を、失った後に見せている**
    ことになる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    res = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    sid = res["session_id"]

    def act(aid):
        return client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": aid},
        ).json()

    # ev_010 が取れなくなるまで盤面を進める。**押せる手だけを押す。**
    # 前提のある手（`requires_evidence` / `requires_actions`）を
    # 押したつもりで数えると、返ってくるのは 400 なのに時計だけが
    # 進んだことになり、前提が黙って崩れる。
    # ev_010 を奪うものは盤面に2つある（現場の再起動と、自分で行う
    # セーフモード再起動）。**どちらで失ったかはこの検査の主題ではない** —
    # 見たいのは、失った後に押した手が中身を渡さないことである
    for a in sc.actions:
        if a.type.value != "investigate" or a.id == "act_memory_dump_fs01":
            continue
        if a.requires_evidence or a.requires_actions:
            continue
        out = act(a.id)
    assert out["view"]["elapsed_minutes"] > 200, "時計が進みきっていない"

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
    「何も出てこなかった」が並ぶ。実際に3回それをやっている:
    連絡（証拠を産まない）、既に止めてある資産への2手目
    （同じ資産に手が2つあるので普通に起きる）、
    そして仕掛けの手（`secures`。産むのは後で読む手のほう。v1.43）。

    ここで見張るのは文言ではなく、**両方が同じ項目を数えていること**。
    名前の一覧だけでは足りない — 片側に項目を1つ足したときに、
    一覧を書き足し忘れれば黙って通る。**数も突き合わせる。**
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    app_js = (root / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function renderResult(")
    end = app_js.index("\nfunction ", start + 1)
    body = app_js[start:end]
    i = body.index("var empty =")
    statement = body[i:body.index(";", i)]

    screen_items = ("revealed", "stopped", "purged", "halted", "already",
                    "prevented", "notice", "preserve",
                    # 戻した回も空振りではない（SPEC 5.11）。
                    # 業務が戻っているのに「何も出てこなかった」と返ると、
                    # **押した結果が見えないまま盤面だけが変わる**
                    "restored",
                    # 塞いだ回も空振りではない。盤面は動かないが、
                    # **先の見込みは動いている**（SPEC 5.12）
                    "hardened")
    for name in screen_items:
        assert name in statement, f"画面側の空振り判定が {name} を数えていない"
    # 一覧に無いものが増えていないか。`!` の数＝否定した項目の数で見る
    assert statement.count("!") == len(screen_items), (
        f"画面側の空振り判定の項目数が一覧と合わない: {statement}"
    )

    # エンジン側（ActionOutcome.empty）と項目が揃っていること
    engine_py = (root / "irdojo" / "engine.py").read_text(encoding="utf-8")
    j = engine_py.index("    def empty(self)")
    prop = engine_py[j:engine_py.index("\n\n", j)]
    engine_items = ("revealed", "contained", "eradicated", "halted",
                    "already", "prevented", "restored", "hardened")
    for name in engine_items:
        assert f"self.{name}" in prop, f"エンジン側の空振り判定が {name} を数えていない"
    # 連絡と仕掛けは、どちらの側でも空振りにならない。片方だけ直すと、
    # 「周知が行き渡った」の下にオレンジの「何も出てこなかった」が並ぶ
    assert "COMMUNICATE" in prop, "エンジン側が連絡を除外していない"
    assert "self.preserves" in prop, "エンジン側が仕掛けを除外していない"
    # 両側の項目数が揃っていること（片側にだけ足した日に落ちる）
    assert len(engine_items) + 2 == len(screen_items), (
        "エンジン側と画面側で、空振り判定の項目数が食い違っている"
    )


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


def test_what_is_still_out_there_is_never_said_during_play(client):
    """「まだ取り除いていない」はプレイ中に言わない（原則5 / SPEC 5.8）。

    置いていかれたものが残っているかどうかは `ground_truth.persistence`
    にしか無い。押した結果が「元の状態に戻した」と言うのは自分の手の記録だが、
    「まだ残っている」と言えば、見ていないものの損失を先に告げることになる。

    実際に押して、封じ込めの応答に真相側の語彙が出ないことを見る。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": ["ws-042"]},
    )
    seen = []
    for aid in ("act_block_c2", "act_rebuild_ws042"):
        res = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": aid},
        ).json()
        assert_clean(res, f"decide {aid}")
        seen.append(res)

    # 止めた回は「元の状態に戻した」ものが空、戻した回は入る。
    # どちらも**自分が何をしたか**しか返さない
    assert seen[0]["contained"] == [{"id": "ws-042", "label": "営業部端末 (佐藤)"}]
    assert seen[0]["eradicated"] == []
    assert seen[1]["eradicated"] == [{"id": "ws-042", "label": "営業部端末 (佐藤)"}]

    # 押した結果の箱に渡る部分だけを見る。`view` の未解消論点には
    # 「永続化の有無」が最初から出ており、そちらは問いであって損失ではない
    outcome = json.dumps(
        [{k: v for k, v in r.items() if k != "view"} for r in seen],
        ensure_ascii=False,
    )
    for word in ("永続化", "persistence", "残って", "まだ"):
        assert word not in outcome, f"押した結果が「{word}」と言っている"


def test_the_debrief_says_what_was_left_behind(client):
    """取り除かないまま終わったことは、講評で初めて言う（SPEC 7.6.13）。

    復旧地平の破線は `on_partial` の傾きで伸びるが、**それは図の上でしか
    見えない。** 言葉にしないと「正しく止めたのに、なぜまだ伸びるのか」で
    終わる。誰に・どうすれば取り除けたかまで言う。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment",
              "assessment": ["ws-042", "fs01", "ws-055"]},
    )
    for aid in ("act_block_c2", "act_shutdown_fs01", "act_isolate_ws055"):
        client.post(
            f"/api/session/{sid}/decide", json={"kind": "action", "action_id": aid}
        )
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()

    c = rep["containment"]
    # 侵害資産は両方止めた。それでも「正しく片付けた」ではない
    assert c["missed"] == []
    assert rep["score"]["consequences"]["containment_completeness"] == 1.0
    assert c["verdict"] == "partial"
    assert c["still_persistent"]
    assert c["factor"] > c["best_factor"]
    # 何をすれば取り除けたかを名指しする
    assert c["eradicable_by"]


def test_the_debrief_explains_what_was_never_suspected(client):
    """名指ししなかった侵害資産について、なぜ落としたのかを講評で言う。

    **プレイ中は誰もこの話をしない**（原則5）。名指しに出てこなかった
    資産は、画面のどこにも現れないまま終わる。反実仮想は v1.41 まで
    `state.assessment` を回していたので、原理的にここを拾えず、
    講評が返していたのは「再現率 2/3」という分数だけだった。

    壊れ方: 向きを画面が判定すると、名指しした側と同じ文面が出る
    （「これを根拠に被疑と判定しました」）。判定していないのだから嘘になる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]

    def press(aid):
        client.post(
            f"/api/session/{sid}/decide", json={"kind": "action", "action_id": aid}
        )

    # 安い手で ws-055 を見て、何も出てこないまま名指しから落とす
    for aid in ("act_collect_evtx_fs01", "act_smb_session_fs01",
                "act_console_query_ws055"):
        press(aid)
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment", "assessment": ["ws-042", "fs01"]},
    )
    press("act_shutdown_fs01")
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()

    missed = [
        cf for cf in rep["retrospective"]["counterfactuals"]
        if cf["direction"] == "missed"
    ]
    assert len(missed) == 1, rep["retrospective"]["counterfactuals"]
    cf = missed[0]
    assert cf["asset_id"] == "ws-055"
    assert cf["evidence_id"] == "ev_020"
    assert cf["refuting_evidence"] == ["ev_021"]      # 取っていれば戻せた
    assert cf["obtainable_by"], "どうすれば取れたかを名指ししていない"
    assert cf["explanation"], "なぜ白く見えたのかの説明が無い"


def test_the_debrief_does_not_call_a_named_asset_a_miss(client):
    """高い手まで押して名指しした人に「落とした」と言わないこと。

    講評の文面は向きで分岐するので、片側だけ直すと必ずずれる
    （test_the_debrief_says_when_nothing_was_left_behind と同じ論法）。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]
    for aid in ("act_collect_evtx_fs01", "act_smb_session_fs01",
                "act_console_query_ws055", "act_local_collect_ws055"):
        client.post(
            f"/api/session/{sid}/decide", json={"kind": "action", "action_id": aid}
        )
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment",
              "assessment": ["ws-042", "fs01", "ws-055"]},
    )
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()

    assert [
        cf for cf in rep["retrospective"]["counterfactuals"]
        if cf["direction"] == "missed"
    ] == []


def test_the_debrief_says_when_nothing_was_left_behind(client):
    """取り除けた場合に「取り除けなかった」と言わないこと。

    講評の文面は分岐で作るので、片方だけ直すと必ずずれる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]
    client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "declare_assessment",
              "assessment": ["ws-042", "fs01", "ws-055"]},
    )
    for aid in ("act_rebuild_ws042", "act_shutdown_fs01", "act_isolate_ws055"):
        client.post(
            f"/api/session/{sid}/decide", json={"kind": "action", "action_id": aid}
        )
    client.post(f"/api/session/{sid}/decide", json={"kind": "finish"})
    rep = client.get(f"/api/session/{sid}/report").json()

    c = rep["containment"]
    assert c["verdict"] == "correct"
    assert c["still_persistent"] == []
    assert c["eradicated"]
    assert c["factor"] == c["best_factor"]


# ─────────── 資産盤（SPEC 7.6.16） ───────────


def test_the_board_counts_mentions_from_the_text_not_from_points_to(any_scenario):
    """盤の「証」は、学習者が自分でやれる文字列探しと同じ数になる。

    `points_to` は「この証拠はどの資産を示唆するか」という**構造化された答え**で、
    要約より強いヒントになる（SPEC 7.4）。盤に出すために向こう側を覗くと、
    キー名を検査するだけの漏洩テストは素通りしたまま、答えの形だけが漏れる。

    見分けが付くのは、`points_to` に載っているのに本文が名前を出していない
    証拠である。そこを数えていたら、盤は本文以上のことを知っている。
    """
    from irdojo.engine import Engine
    from irdojo.schema import names_asset

    e = Engine(any_scenario)
    # 全部手に入れた状態を作る（盤の数が最大になるところで比べる）
    e.state.obtained_evidence = [ev.id for ev in any_scenario.evidence]
    board = {a.id: a.mentions for a in e.view().assets}

    by_id = any_scenario.asset_by_id
    text_only = {
        aid: sum(1 for ev in any_scenario.evidence if names_asset(ev.content, by_id[aid]))
        for aid in board
    }
    assert board == text_only, "盤の数が本文の照合と一致しない"



def test_at_least_one_bundled_board_can_tell_the_two_apart():
    """上の検査に歯があること。

    2つの数え方（本文の照合 ／ `points_to` の数）が同梱のどの資産でも
    同じ値なら、実装を取り違えても上の検査は通ってしまう。
    **どちらかの向きに食い違う資産が、少なくとも1つ要る。**

    v1.44 まで、この歯は「本文が名指ししないのに points_to に載っている
    証拠」1種類で測っていた。実例は2本目の mailbox で、指しているのに
    本文は「受信箱」と書いていた。v1.45 で `aliases` に「受信箱」を
    宣言したので、その食い違いは消えた — **それは盤が過少に数えていた
    という欠陥のほうが直ったのであって、検査の歯が要らなくなったのでは
    ない。** 食い違いは逆向き（本文は名指しするが points_to には無い）
    にも出るので、向きを決め打ちにせず、差があることだけを見る。
    """
    from irdojo.engine import Engine

    apart = []
    for sid in ("ransomware-initial-response-01", "oauth-consent-abuse-01"):
        sc = load_scenario(sid)
        e = Engine(sc)
        e.state.obtained_evidence = [ev.id for ev in sc.evidence]
        board = {a.id: a.mentions for a in e.view().assets}
        for aid, said in board.items():
            pointed = sum(1 for ev in sc.evidence if aid in ev.points_to)
            if said != pointed:
                apart.append((sid, aid, said, pointed))
    assert apart, (
        "本文の照合と points_to の数が同梱のどの資産でも同じで、"
        "盤が構造を数えていても誰も気づかない"
    )


def test_counting_mentions_does_not_find_the_answer(any_scenario):
    """「言及の多い資産を名指しする」は、正解の近道にならない。

    盤に数を出す以上、学習者はそれを強さの目安に使う。使えてしまうなら、
    盤はログを読まずに点が取れる装置になり、原則2 を破る。

    見るのは、全証拠を手に入れた最良の状態で、言及の多い順に
    侵害と同じ数だけ名指ししたときに真実と一致しないこと。
    **一致する盤面を出荷したら、この検査が落ちる。**

    同梱1本目では dc01（4件）と ws-113（5件）が無実のまま上位に来て、
    侵害されている ws-055 は3件で下にいる。数の多さは注意の量であって、
    疑いの強さではない。
    """
    from irdojo.engine import Engine

    e = Engine(any_scenario)
    e.state.obtained_evidence = [ev.id for ev in any_scenario.evidence]
    board = {a.id: a.mentions for a in e.view().assets}

    truth = set(any_scenario.world.ground_truth.compromised)
    ranked = sorted(board, key=lambda aid: (-board[aid], aid))
    guess = set(ranked[: len(truth)])
    assert guess != truth, f"言及数の上位がそのまま答えになっている: {sorted(guess)}"


def test_the_board_never_shows_the_reach_of_a_hand_not_yet_pressed(client):
    """まだ押していない手が何を見に行くかは、盤にも API にも出さない。

    `investigates` / `targets` を渡さないのは「押す前に配ると選択が賭けで
    なくなる」からである（SPEC 7.4）。盤はそれを**押したあとに**数え直すので、
    1手も押していない時点では、どの資産も 0 でなければならない。
    """
    sc = load_scenario("oauth-consent-abuse-01")
    created = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()
    assert_clean(created, "POST /api/session")
    assert [a["touched"] for a in created["view"]["assets"]] == [0] * len(sc.world.assets)
    assert [a["mentions"] for a in created["view"]["assets"]] == [0] * len(sc.world.assets)


# ── 呼び名の宣言（aliases / SPEC 7.1 / v1.45） ──────────────────


def _reload(data):
    """辞書を YAML に戻してローダへ通す。拒否は例外で出る。"""
    import yaml

    from irdojo.loader import load_scenario_text

    return load_scenario_text(yaml.safe_dump(data, allow_unicode=True))


def test_a_declared_alias_stops_a_leak_that_the_label_alone_lets_through(raw_oauth):
    """宣言した呼び名は、label と同じ重さで漏洩検査に効く。

    漏洩検査は id と label からしか名前を作れなかった。日本語の盤面は
    同じものを場所ごとに別の名前で呼ぶので、**同じ漏れ方が語によって
    通ったり弾かれたりしていた** — 連携アプリを「差出人整理アシスタント」と
    書けば弾かれ、同じ文が受信箱を「森の受信箱の規則」と書いても通った。

    見るのは2つで、順序に意味がある。

      1. 宣言があると、その呼び名で書いた漏れが拒否されること
      2. 宣言を外すと、**同じ文が通ってしまう**こと

    2 が無いと、この検査は「もともと弾かれていたもの」を見ているだけに
    なり、`aliases` が1文字も効いていなくても通る。
    """
    import copy

    from irdojo.loader import ScenarioError

    LEAK = "\n森の受信箱に残された規則も、この照会で一緒に確認する。\n"

    def with_leak(data):
        data = copy.deepcopy(data)
        for a in data["actions"]:
            # mailbox を targets にも investigates にも持たない調査の手
            if a["id"] == "act_signin_review":
                a["description"] += LEAK
        return data

    with pytest.raises(ScenarioError) as exc:
        _reload(with_leak(raw_oauth))
    assert "受信箱" in str(exc.value)

    stripped = copy.deepcopy(raw_oauth)
    for a in stripped["world"]["assets"]:
        a.pop("aliases", None)
    _reload(stripped)                      # 宣言を外すだけなら盤面は通る
    _reload(with_leak(stripped))           # **同じ漏れが素通りする**


def test_every_asset_a_piece_of_evidence_points_at_is_named_in_it(any_scenario):
    """証拠が指している資産は、その証拠の文のどこかでそう呼ばれていること。

    `points_to` は採点の構造で、資料の文は学習者が読むものである。
    **両者がずれていたら、学習者は読んでも辿り着けない。** ずれる原因は
    たいてい「資料はその資産を別の名前で呼んでいるのに、宣言が無い」で、
    そのとき盤の言及数は過少に数え、漏洩検査は空振りする。

    ここは書き方の規則であって、`points_to` の正しさの検査ではない。
    呼び名が足りないなら `aliases` に足す。それが嫌なら文を直す。
    """
    from irdojo.schema import names_asset

    by_id = any_scenario.asset_by_id
    silent = [
        (ev.id, aid)
        for ev in any_scenario.evidence
        for aid in ev.points_to
        if not (names_asset(ev.content, by_id[aid])
                or names_asset(ev.summary, by_id[aid])
                or names_asset(ev.reading, by_id[aid]))
    ]
    assert not silent, (
        f"指している資産を一度も名前で呼んでいない証拠: {silent}"
        "（資料の呼び名を aliases に宣言するか、文を直す）"
    )


def test_that_naming_rule_is_what_the_aliases_are_paying_for(raw_oauth):
    """上の検査が、2本目では `aliases` 無しに成り立たないこと。

    「証拠は指した資産をそう呼ぶ」は、1本目では宣言ゼロで成り立つ —
    端末を `ws-042` と呼び、資料でもそう書くからである。だから
    1本目だけを見ていた頃は、この性質が**盤面の性質なのか
    呼び名の宣言のおかげなのかが区別できなかった。**

    2本目で分かれる。宣言を外すと 6件が黙る。
    つまり `aliases` は飾りではなく、上の検査を成立させている側にある。
    """
    import copy

    from irdojo.schema import names_asset

    stripped = copy.deepcopy(raw_oauth)
    for a in stripped["world"]["assets"]:
        a.pop("aliases", None)
    sc = _reload(stripped)
    by_id = sc.asset_by_id
    silent = [
        (ev.id, aid)
        for ev in sc.evidence
        for aid in ev.points_to
        if not (names_asset(ev.content, by_id[aid])
                or names_asset(ev.summary, by_id[aid])
                or names_asset(ev.reading, by_id[aid]))
    ]
    assert len(silent) >= 5, (
        f"宣言を外しても黙る証拠がほとんど無い（{silent}）。"
        "aliases が仕事をしていないか、資料が id で呼ぶ書き方に戻っている"
    )


def test_the_loader_rejects_a_name_that_points_at_two_assets(raw_oauth):
    """同じ呼び名を2つの資産に付けたら拒否すること。

    漏洩検査は名前で照合するので、衝突した名前は「どちらを名指ししたか」を
    決められない。片方に許された名指しが、もう片方の漏洩を素通りさせる。
    `aliases` は著者が自由に書ける欄なので、衝突は黙って入る。

    空白だけの別名も拒否する。空文字はどんな文にも一致するので、
    **1件入れるだけで全資産の漏洩検査が無効になる。**
    """
    import copy

    from irdojo.loader import ScenarioError

    data = copy.deepcopy(raw_oauth)
    for a in data["world"]["assets"]:
        if a["id"] == "app-board":
            a["aliases"] = a.get("aliases", []) + ["受信箱"]
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "受信箱" in str(exc.value)

    data = copy.deepcopy(raw_oauth)
    for a in data["world"]["assets"]:
        if a["id"] == "app-board":
            a["aliases"] = ["   "]
    with pytest.raises(ScenarioError):
        _reload(data)


# ── 出来事が運ぶもの（SPEC 5.10 / v1.45） ──────────────────────


def test_the_loader_rejects_a_timeline_that_names_a_compromised_asset(raw_oauth):
    """出来事の文が侵害資産を名指ししたら拒否すること。

    アクションの4フィールドには規則があり、論点にもあった。
    **出来事だけが無防備だった。** 出来事は押さなくても届く —
    そこに名前が乗っていたら、待つのが最良の調査になる。

    実際に1件あった。2本目の 300分の連絡が「メールボックスの容量が…」で
    始まっており、`aliases` に「メールボックス」を宣言した途端に
    この検査が拾った。**呼び名の宣言が漏洩を1件掘り出した**形である。
    """
    import copy

    from irdojo.loader import ScenarioError

    data = copy.deepcopy(raw_oauth)
    for ev in data["timeline"]:
        if ev["id"] == "tl_deleted_items":
            ev["text"] = "「メールボックスの容量が上限に近づいていたので、\n" \
                         "  自動整理が動きました。」\n"
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "tl_deleted_items" in str(exc.value)


def test_a_timeline_may_still_name_what_the_briefing_already_named(raw_scenario):
    """ブリーフィングが既に渡した資産は、出来事も名乗ってよい。

    1本目の 295分は「fs01 が応答しなくなっていたので、一度再起動を
    かけました」と言う。fs01 はブリーフィングの一次情報なので、
    ここで隠すのは**学習者が既に持っているものを隠す**ことであり、
    文が何の話か分からなくなるだけである。

    この検査が無いと、上の規則を「出来事に資産名を書くな」と
    強くしすぎたときに、誰も気づかない。
    """
    from irdojo.schema import briefing_assets, names_asset

    sc = _reload(raw_scenario)
    assert "fs01" in briefing_assets(sc)
    named = [ev.id for ev in sc.timeline if names_asset(ev.text, sc.asset_by_id["fs01"])]
    assert named, "免除されているはずの資産を名乗る出来事が1件も無い"


def test_the_loader_rejects_a_timeline_that_quotes_what_it_destroys(raw_oauth):
    """奪う出来事が、奪う証拠の中身をその場で渡していたら拒否すること。

    失ったものは講評で初めて開く（原則5）。中身まで言えば、
    **買っていない証拠を、失った瞬間に無料で配ったことになる。**

    v1.44 で実際にそうなっていた。350分の連絡が ev_006 を奪いながら、
    その要約の要点（請求のメールが移されている）を本文で述べていた。
    しかも先手を打った側（averted_text）の方が情報が少なく、
    **周知を出さなかった学習者だけがヒントを受け取っていた。**
    """
    import copy

    from irdojo.loader import ScenarioError

    quote = "請求と口座を含む受信を既読にして別フォルダへ移す規則"
    data = copy.deepcopy(raw_oauth)
    for ev in data["timeline"]:
        if ev["id"] == "tl_helpdesk_rule":
            ev["text"] = f"「営業部から申告があり、担当者が{quote}を外したそうです。」\n"
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "tl_helpdesk_rule" in str(exc.value)

    # 先手を打った側にだけ書いても同じこと。**周知を出した人には
    # 見えて、出さなかった人には見えない中身**というのも配布である
    data = copy.deepcopy(raw_oauth)
    for ev in data["timeline"]:
        if ev["id"] == "tl_helpdesk_rule":
            ev["averted_text"] = f"「{quote}は、そのまま預かっています。」\n"
    with pytest.raises(ScenarioError):
        _reload(data)


def test_both_sides_of_an_event_say_the_same_amount(any_scenario):
    """起きた側と防げた側で、書いてある量が逆転していないこと。

    周知は「先に手を打った」ことへの報酬である。ところが v1.44 の
    2本目では、**周知を出さなかった側の文のほうが長くヒントが多かった** —

        text         「…見覚えのないものを片付けた」   ← 何を外したかが読める
        averted_text 「…そのままにしておいた」

    払った人が情報で損をするなら、周知は「証拠を守るが手がかりを失う手」に
    なる。これは盤面が意図した取引ではない。

    **この検査が見るのは量だけである。** 上の v1.44 の2文は長さが
    ほぼ同じで、ここでは落ちない — 実測してある。落ちるのは
    「片側を書き忘れた」水準で、情報量の逆転そのものはローダの2規則
    （資産を名指ししない／奪う証拠を引き写さない）と、5.10 の書き方が
    受け持つ。実際 v1.44 の tl_helpdesk_rule はローダが拒否するように
    なったが、tl_user_unlink の「見覚えのないものを片付けた」は
    **いまも機械では拾えない。** 拾えないものを拾えるふりをしないために、
    ここに書いておく。

    0.6 は同梱2本の実測（最小 0.82）から取った緩い下限で、
    書き足りない側があることの目印であって、文体の規則ではない。
    """
    import re

    def size(t):
        return len(re.sub(r"\s+", "", t or ""))

    for ev in any_scenario.timeline:
        if not ev.averted_text:
            continue
        a, b = size(ev.text), size(ev.averted_text)
        assert min(a, b) >= 0.6 * max(a, b), (
            f"{ev.id}: 起きた側 {a}文字 / 防げた側 {b}文字。"
            "片側だけが書き足りていない（周知を出した人が情報で損をする）"
        )


# ── 取り除く手の束（SPEC 5.6 / v1.45） ────────────────────────


@pytest.mark.parametrize(
    "fixture_name,decoy",
    [("raw_scenario", "act_purge_persistence_ws107"),
     ("raw_oauth", "act_reconsent_app_scan")],
)
def test_the_eradication_bundle_must_not_map_the_persistence(
    request, fixture_name, decoy
):
    """取り除く手の当たり先が、侵害資産の中に収まっていたら拒否すること。

    封じ込めの手は `requires_evidence` を持てない（SPEC 5.6）ので、
    **開始0分・0アクションで全部読める。** そこに「元の状態に戻す」という束が
    あり、その束の手だけが `eradicates` を持ち、当たり先が侵害資産だけなら、
    **束の名前がそのまま答えの一部になる。**

    2本目の総当たりで実際に出た。被疑判定は応答フェーズ中も
    再宣言できる（3.6）ので、0分・0手・証拠0件のまま
    空宣言 → 束を読む → 宣言し直すだけで適合率 1.00・再現率 1.00 に届いた。

    隔離の束には無実の資産の手が混ざっていたのに、**囮があるのは
    止める側だけだった。** 囮を1つ外すだけで束は答えに戻るので、
    外して落ちることをここで確かめる。
    """
    import copy

    from irdojo.loader import ScenarioError

    data = copy.deepcopy(request.getfixturevalue(fixture_name))
    data["actions"] = [a for a in data["actions"] if a["id"] != decoy]
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "取り除く" in str(exc.value)


def test_keeping_the_decoy_but_moving_it_to_another_bundle_does_not_help(raw_oauth):
    """囮を別の束へ逃がしても拒否すること。

    要求が「どこかに無実の資産への根絶手がある」だけだと、著者は囮を
    別の束に置いて逃げられる。学習者が読むのは束の見出しなので、
    「元の状態に戻す」を開いたときに侵害資産しか並んでいなければ、
    囮がよそにあっても答えは配られている。
    """
    import copy

    from irdojo.loader import ScenarioError

    data = copy.deepcopy(raw_oauth)
    for a in data["actions"]:
        if a["id"] == "act_reconsent_app_scan":
            a["group"] = "連携だけを止める"
    with pytest.raises(ScenarioError) as exc:
        _reload(data)
    assert "元の状態に戻す" in str(exc.value)


def test_the_eradication_bundle_reaches_an_innocent_asset(any_scenario):
    """同梱の盤面が、実際にその形になっていること。

    上の2本はローダの歯を見ている。こちらは**出荷物がその歯に
    掛かっていない**ことを見る。規則を足しても、同梱が規則の外に
    いたら教材は変わらない。
    """
    gt = any_scenario.world.ground_truth
    secret = set(gt.compromised) | {gt.patient_zero}
    reach = {t for a in any_scenario.actions for t in a.eradicates}
    assert reach - secret, (
        f"取り除く手が侵害資産にしか届かない: {sorted(reach)}。"
        "束を開いただけで persistence が読める"
    )


def test_no_bundle_readable_at_zero_minutes_spells_out_the_truth(any_scenario):
    """0分・0手で読めるものだけを足しても、真実そのものにならないこと。

    学習者が開始直後に読めるのは、アクション一覧（束の見出しと当たり先）と
    ブリーフィングである。封じ込めの手は `requires_evidence` を持てないので、
    **応答フェーズの束は最初から全部見えている。**

    実際の抜け道はこれだった。2本目の「元の状態に戻す」が
    {mailbox, app-relay} を、ブリーフィングが {acct-mori} を配っており、
    足すと侵害資産の集合とぴたり一致した。被疑判定は応答フェーズ中も
    再宣言できる（3.6）ので、空宣言で進み、束を読み、宣言し直すだけで
    適合率 1.00・再現率 1.00 が取れた。**ログを1行も読まずにである。**

    論点の重みが総合点を抑えるので満点にはならないが、
    事実認識の層を丸ごと素通りされたら演習は演習でなくなる。
    """
    from irdojo.schema import briefing_assets

    truth = set(any_scenario.world.ground_truth.compromised)
    given = briefing_assets(any_scenario)

    by_group: dict[str, dict[str, set]] = {}
    for a in any_scenario.actions:
        slot = by_group.setdefault(a.group, {"targets": set(), "eradicates": set()})
        slot["targets"].update(a.targets)
        slot["eradicates"].update(a.eradicates)

    for group, slot in by_group.items():
        for field, reach in slot.items():
            if not reach:
                continue
            for extra, tag in ((set(), ""), (given, "＋ブリーフィング")):
                assert (reach | extra) != truth, (
                    f"束「{group}」の {field}{tag} が侵害資産と一致する: "
                    f"{sorted(reach | extra)}。押さずに読めるものだけで答えが出る"
                )


def test_the_axis_only_carries_times_that_are_already_on_the_page(client):
    """**事件の時計は、content の再掲でしかない**（SPEC 7.6.17）。

    時間軸は新しい開示ではなく、学習者が既に持っている紙の上の時刻を
    並べ直す場所である。だから返す `occurred_at` は、同じ応答で返して
    いる `content` の中に必ず見つかる。見つからない時刻が返るなら、
    それは 25分の値札を付けて手で買わせるはずだった資料である。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]
    seen = 0
    for aid in ("act_collect_evtx_fs01", "act_netflow_overview",
                "act_dc_authlog", "act_mail_gateway"):
        res = client.post(
            f"/api/session/{sid}/decide",
            json={"kind": "action", "action_id": aid},
        ).json()
        for ev in res["view"]["obtained_evidence"]:
            if not ev.get("occurred_at"):
                continue
            seen += 1
            assert ev["occurred_at"].split(" ")[1] in ev["content"], (
                f"{ev['id']}: 本文に無い時刻が時間軸へ渡っている"
            )
    assert seen, "この手順では一度も時刻が返っていない（検査が空回りしている）"


def test_the_axis_is_silent_about_evidence_never_taken(client):
    """取っていない証拠の時刻は、どこにも出ない。

    時間軸は「調査の進み方そのものが図になる」ことに価値がある。
    取っていないものが並んだ瞬間、図は盤面の構造を配る装置になる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post("/api/session", json={"scenario_id": sc.meta.id}).json()["session_id"]
    res = client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "action", "action_id": "act_collect_evtx_fs01"},
    ).json()
    got = {ev["id"] for ev in res["view"]["obtained_evidence"]}
    assert got, "前提が崩れている（1手押しても証拠が出ていない）"
    missing = [e.id for e in sc.evidence if e.occurred_at and e.id not in got]
    assert missing, "前提が崩れている（1手で全部の時刻が出ている）"
    text = json.dumps(res, ensure_ascii=False)
    for eid in missing:
        assert eid not in text, f"取っていない {eid} が応答に出ている"


def test_hard_still_gets_the_times(client):
    """`hard` でも時刻は渡す。**伏せるのは要約であって、生ログではない。**

    hard は消化された見せ方（要約・読み方・列挙・図）を奪うレベルで、
    `content` は全レベルで出る（SPEC 5.4）。時刻はその content の中に
    既に書いてある。ここで伏せると、hard だけが「並べる場所」を
    持たないまま「並べろ」と言われることになる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    sid = client.post(
        "/api/session",
        json={"scenario_id": sc.meta.id, "assist_level": "hard"},
    ).json()["session_id"]
    res = client.post(
        f"/api/session/{sid}/decide",
        json={"kind": "action", "action_id": "act_collect_evtx_fs01"},
    ).json()
    evs = res["view"]["obtained_evidence"]
    assert evs, "前提が崩れている"
    assert all(e["summary"] is None for e in evs), "hard で要約が出ている"
    assert any(e["occurred_at"] for e in evs), "hard で時刻まで消えている"
