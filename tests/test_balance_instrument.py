"""測定器そのものを見張る（tools/balance.py）。

設計の判定は測定器の出力でしか下せないので、測定器が盤面の一部を
見ていないと、判定は「通っている」まま嘘をつく。実際に2つ穴があった:

  - `advance_phase` を通さないので対応フェーズが一度も実行されず、
    containment_completeness は全プレイ 0.00 だった
  - 被疑判定に `ground_truth.compromised` をそのまま宣言していたので、
    事実認識層の重み 0.60 分が全プレイ同値だった

どちらも「判定が全部通る」状態と同居していた。ここで見張るのは
**測定器が動かしている軸の本数**であって、個々の点数ではない。
"""

import importlib.util
import pathlib
import sys

import pytest

from irdojo import scoring
from irdojo.loader import load_scenario

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_balance():
    spec = importlib.util.spec_from_file_location("balance", ROOT / "tools" / "balance.py")
    mod = importlib.util.module_from_spec(spec)
    # dataclass の型解決に sys.modules を引くので、先に登録しておく
    sys.modules["balance"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def balance():
    return _load_balance()


@pytest.fixture(scope="module")
def plays(balance):
    sc = load_scenario("ransomware-initial-response-01")
    return sc, {
        prof.key: balance._play(
            sc, prof.order(sc), [],
            stop=prof.stop_when_confident,
            patience=prof.patience_minutes,
            weighs_refutations=prof.weighs_refutations,
            contains_everything=prof.contains_everything,
            contains_root=prof.contains_root,
            names_everything=prof.names_everything,
        )
        for prof in balance.PROFILES
    }


def test_every_play_reaches_the_response_phase(plays):
    """どのプレイ像も封じ込めまで通す。

    対応フェーズを実行しないと `contained_at` が空のままになり、
    被害の減衰係数は全プレイ on_incorrect 固定、
    business_impact は全プレイ 0（＝正規化後の「良さ」が満点）になる。
    方針の重みのうち常に定数の部分が、方針によっては 0.50 に達していた。
    """
    sc, runs = plays
    for key, e in runs.items():
        assert e.state.assessment_snapshot is not None, key
        assert e.state.current_phase == sc.phases[-1].id, key
        assert e.state.contained_at, f"{key}: 何も止めていない"


def test_containment_is_not_the_same_for_everyone(plays):
    """止めたものが像によって違う。

    全プレイが同じ資産を止めるなら、containment_completeness も
    business_impact も定数で、方針適合層はその分だけ死んでいる。
    """
    sc, runs = plays
    shapes = {key: frozenset(e.state.contained_at) for key, e in runs.items()}
    assert len(set(shapes.values())) >= 3, shapes

    completeness = {
        key: scoring.consequences(e.state, sc).containment_completeness
        for key, e in runs.items()
    }
    assert min(completeness.values()) < 1.0, completeness
    assert max(completeness.values()) == 1.0, completeness


def test_assessment_is_built_from_what_the_play_holds(plays):
    """被疑判定は手元の証拠から組み立てる。ground_truth を読まない。

    読んでしまうと precision 1.0 / recall 1.0 / persistence_missed 0.0 が
    全プレイ同値になり、**このシナリオの教育の中心**（誤導に乗って
    無関係な端末を名指しする）が判定の数字に一度も現れない。
    """
    sc, runs = plays
    truth = set(sc.world.ground_truth.compromised)
    declared = {key: set(e.state.assessment) for key, e in runs.items()}

    assert declared["skilled"] == truth, declared["skilled"]
    assert declared["wanderer"] > truth, "誤導を追う像が無関係な資産を名指ししていない"
    assert declared["hasty"] < truth, "調べていない像が正解を当てている"
    assert len({frozenset(v) for v in declared.values()}) >= 3, declared


def test_the_misled_control_differs_only_in_the_declaration(plays):
    """誤導の対照は、調査の手が1つも違わないこと。

    ここが揃っていないと「誤導は損か」の差が、調査量の差と混ざる。
    違ってよいのは、宣言と、その宣言に従って止めた資産だけ。
    """
    sc, runs = plays
    from irdojo.schema import ActionType

    def investigated(e):
        return [
            a for a in e.state.executed_actions
            if sc.action_by_id[a].type != ActionType.CONTAIN
        ]

    bad, ok = runs["wanderer"], runs["wanderer_clear"]
    assert investigated(bad) == investigated(ok)
    assert set(bad.state.assessment) != set(ok.state.assessment)


# ─────────── 対応フェーズ（サイクル3で足した軸） ───────────


def test_some_play_stops_the_root_of_the_dependency_graph(plays):
    """依存の根を止める手を、誰かが押していること。

    像が「自分が名指しした資産に届く手」しか押さないと、盤面で最も
    高くつく資産（依存の根）は一度も止まらない。すると復旧地平も
    業務影響も、その資産については常に 0 で測られる。

    構成図は開始0分から画面に出ていて、根が dc01 であることは見えている。
    「根を落とせば全部止まる」は、それを見た人が普通に思いつく手である。
    """
    sc, runs = plays
    roots = {
        a.id for a in sc.world.assets
        if not a.depends_on and any(a.id in b.depends_on for b in sc.world.assets)
    }
    assert roots, "依存の根が無い（この検査が意味を持たない盤面）"
    stopped_root = [k for k, e in runs.items() if roots & set(e.state.contained_at)]
    assert stopped_root, f"どの像も依存の根 {roots} を止めていない"
    # 根を止めた像では、業務が資産の数だけ止まっていること。
    # ここが効いていないと、封じ込めの費用の上限が測れない
    for key in stopped_root:
        st = runs[key].state
        assert len(st.halted_at) == len(sc.world.assets), key


def test_no_play_presses_two_actions_at_the_same_asset(plays):
    """既定の像は、同じ資産に二重に手を打たない。

    fs01 を停止した**うえで**その fs01 への SMB 遮断の変更承認を
    待つ人はいない。押せる手を全部押す像は `exhaustive` が受け持つ。
    ここが緩むと、対応フェーズの取捨選択が「全部やる」に潰れる。
    """
    sc, runs = plays
    from irdojo.schema import ActionType

    for key, e in runs.items():
        if key == "exhaustive":
            continue        # 取捨選択をしない像。ここだけは二重に打つ
        seen: dict[str, str] = {}
        for aid in e.state.executed_actions:
            act = sc.action_by_id[aid]
            if act.type != ActionType.CONTAIN:
                continue
            for t in act.targets:
                assert t not in seen, f"{key}: {t} に {seen.get(t)} と {aid} の二重"
                seen[t] = aid


def _named(data, name):
    return next(c for c in data["checks"] if c["name"] == name)


def test_the_response_phase_is_not_measured_by_time(balance):
    """対応フェーズを時間比では測らない（SPEC 3.8）。

    フェーズ総コストの 2〜3倍検査は、対応フェーズには効かない —
    対応は定義上 L のあとに始まるので、折れ点までの予算は調査が
    使い切っている。同じ150分を二重に数えていて、何をしても1倍を切る。
    2倍に届かせるには封じ込め1手あたり60分が必要で、
    それは被害モデルが罰したいことと正反対の設計になる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    data = balance.report(sc)
    check = _named(data, "最終より前のフェーズは全部やれない")
    assert check["ok"]
    assert sc.phases[-1].label in check["detail"]
    # 最終フェーズの取捨選択は、不可逆性で測る3本が受け持つ
    for name in (
        "封じ込めに取捨選択がある",
        "封じ込めの最良手が方針ごとに違う",
        "何も止めないプレイは正しく止めたプレイに負ける",
    ):
        assert _named(data, name)["ok"], name


# ─────────── 弱い敵を選んでいないか（サイクル4で足した軸） ───────────


def test_the_blind_side_is_not_represented_by_its_weakest_shape(balance, plays):
    """「調べない像」を1つに代表させない。

    v1.35 の測定器は 0手・fs01 だけを名指しする像 1本を全ての他像と比べ、
    それが最下位であることをもって「調べる意味がある」と言っていた。
    **それは調べない側で最も弱い形である。** 同じ 0手でも
    「疑わしきは全部隔離」は再現率が満点になり、被害も止まる。
    弱い敵しか置いていない検査は、通ったまま何も保証しない。
    """
    sc, runs = plays
    blind = [p.key for p in balance.PROFILES if p.skips_investigation]
    assert len(blind) >= 2, f"調べない像が {blind} しかいない"

    scores = {
        key: {
            pol.id: scoring.score(runs[key].state, sc, pol).composite_score
            for pol in sc.policies
        }
        for key in blind
    }
    # 弱い敵を選んでいないこと。どこかの方針で hasty より強い像がいる
    assert any(
        scores["blanket"][pid] > scores["hasty"][pid] for pid in scores["hasty"]
    ), f"調べない像がどれも同じ強さ: {scores}"


def test_the_evidence_ladder_needs_the_support_metric(balance):
    """根拠の梯子は、裏付けを測る指標が無いと平らになる。

    梯子（同じ判定・根拠の厚みだけ違う3プレイ）は、今回のテーマの
    唯一の直接的な計器である。これが「通るように緩めた検査」でないことを、
    見張っている当の指標を外して落ちることで示す。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )
    data = copy.deepcopy(raw)
    fl = data["scoring"]["fact_layer"]
    fl["metrics"] = [m for m in fl["metrics"] if m["id"] != "assessment_support"]
    # 外した 0.20 は適合率と再現率へ。合計は 1.00 のまま
    fl["weights"] = {
        "assessment_precision": 0.30,
        "assessment_recall": 0.30,
        "unresolved_questions": 0.40,
    }
    sc = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    check = _named(balance.report(sc), "同じ判定でも、根拠の厚みで事実認識層が動く")
    assert not check["ok"], (
        "裏付けを測る指標が無くても梯子が立っている＝梯子が何も見ていない"
    )

    ladder = balance.evidence_ladder(sc)
    assert ladder[0][2] == ladder[1][2], f"平らになっていない: {ladder}"


def test_the_press_order_checks_watch_the_clock_and_the_notice(balance):
    """押し順の2本が、それぞれ別のものを見張っていること。

    「時計が奪うものは、盤面の手で取り戻せる」は連絡が**間に合うこと**を、
    「連絡には歯がある」は連絡が**何かを守っていること**を見ている。
    片方だけだと、奪う出来事を消すだけで両方通ってしまう。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )

    def with_reboot_at(minutes):
        data = copy.deepcopy(raw)
        for t in data["timeline"]:
            if t["id"] == "tl_field_reboot":
                t["at_minutes"] = minutes
        data["timeline"].sort(key=lambda t: t["at_minutes"])
        return load_scenario_text(yaml.safe_dump(data, allow_unicode=True))

    # 連絡（15分）を打ち終わる前に奪われると、盤面の手では取り戻せない
    early = balance.report(with_reboot_at(10))
    assert not _named(early, "時計が奪うものは、盤面の手で取り戻せる")["ok"]

    # 誰も届かない時刻に置くと、その連絡は何も守っていない
    late = balance.report(with_reboot_at(2000))
    assert not _named(late, "連絡には歯がある")["ok"]
    assert _named(late, "時計が奪うものは、盤面の手で取り戻せる")["ok"]


def test_press_orders_are_more_than_the_order_in_the_file(balance):
    """押し順は、YAML の記載順1本では足りない。

    記載順は作者の都合であって学習者の押し順ではない。
    実測すると、記載順が通っていたのは ev_009 の取得と現場再起動の
    15分の余裕のおかげで、`cost_minutes` を1つ触れば裏返る状態だった。
    """
    sc = load_scenario("ransomware-initial-response-01")
    orders = balance.press_orders(sc)
    assert len(orders) >= 5, orders
    # どの順序も「全部押す」であること（取捨選択の像ではない）
    every = {a.id for a in sc.actions if a.type.value == "investigate"}
    for name, ids in orders.items():
        assert set(ids) == every, name
    # 並びが実際に違うこと。同じ列が5本あっても何も測っていない
    assert len({tuple(ids) for ids in orders.values()}) >= 4, "押し順が実質1本"

    # 連絡なしでは、少なくとも1つの順序が critical 論点を落とす
    lost = {
        name: balance._lost_critical(sc, balance.run_order(sc, ids, []))
        for name, ids in orders.items()
    }
    assert any(v for v in lost.values()), lost


def test_the_replacement_checks_are_not_slack(balance, monkeypatch):
    """置き換えた検査が「通るように緩めた検査」になっていないこと。

    測定側を緩めて数字を動かしたのなら、盤面を元に戻しても通るはずである。
    2本について、それぞれが見張っている変更を外すと実際に落ちることを示す。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )

    # (1) 侵害資産ごとの手を1つずつに戻すと、最良手は全方針で一致する。
    #     **v1.37 で選択肢が増えた**（fs01 を止める2つ、ws-042 に手を当てる
    #     4つ）。1つ消しただけでは残りが方針を割り続けるので、
    #     「侵害資産1つにつき手が1つ」まで落として初めて検査が落ちる。
    #     残すのは act_shutdown_fs01 と act_rebuild_ws042 — これなら
    #     persistence を根絶する手も盤面に1つ残る（ローダの要求）
    alternatives = [
        "act_block_smb_fs01", "act_stop_share_fs01", "act_purge_persistence_ws042",
        "act_block_c2", "act_isolate_ws042",
    ]
    data = copy.deepcopy(raw)
    data["actions"] = [a for a in data["actions"] if a["id"] not in alternatives]
    for pol in data["policies"]:
        # 空になった forbid_action は落とす（空の制約はローダが
        # 「永久に発火しない」として別の理由で拒否する。v1.40）
        kept = []
        for c in pol.get("constraints", []):
            if "action_ids" in c:
                # 残った唯一の手を禁じる制約も落とす。侵害資産を止める手を
                # 全部禁じる方針はローダが拒否するので（v1.40）、
                # そのままでは読み込みで落ちて、見たい検査に辿り着かない
                c["action_ids"] = [
                    i for i in c["action_ids"]
                    if i not in alternatives and i != "act_shutdown_fs01"
                ]
                if not c["action_ids"]:
                    continue
            kept.append(c)
        pol["constraints"] = kept
    one_way = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    assert not _named(
        balance.report(one_way), "封じ込めの最良手が方針ごとに違う"
    )["ok"], "選択肢が1つでも「方針ごとに違う」が通ってしまう"

    # (2) 復旧地平を外すと、「何も止めない」が勝つ
    from irdojo import scoring

    monkeypatch.setattr(
        scoring.damage_mod, "project", lambda *a, **k: [], raising=True
    )
    sc = load_scenario("ransomware-initial-response-01")
    assert not _named(
        balance.report(sc), "何も止めないプレイは正しく止めたプレイに負ける"
    )["ok"], "地平が無くても「何も止めない」が負けている"


def test_the_ranking_check_catches_a_duplicated_policy(balance):
    """**方針の複製は、1位だけを見ていると見つからない**（v1.40 / 9.1 差分#1）。

    v1.39 の証拠保全最優先を盤面に戻す。当時の制約は
    `require_before`（前提タグの付いた手を盤面のどこかで1つ押していれば満たす）
    1本で、ws-042 のメモリを取れば fs01 を無条件に落とせた。
    実測すると、業務継続最優先と**上位6組まで一字一句同じ順**に並ぶ —
    「方針を採点の入力にする」という主張が、盤面では2本しか
    実装されていない状態である。

    このとき「封じ込めの最良手が方針ごとに違う」（2通り以上あればよい）は
    **通ってしまう。** 被害最小化最優先が別の手を選ぶからで、
    その1本だけで3本分の証明になっていた。順位ごと比べないと落ちない。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )
    data = copy.deepcopy(raw)
    for pol in data["policies"]:
        if pol["id"] == "evidence_preservation":
            pol["constraints"] = [{
                "type": "require_before",
                "action_type": "contain",
                "prerequisite_tags": ["volatile_capture"],
                "message": "封じ込め前に揮発性証拠の取得が必要です",
            }]
    old = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    report = balance.report(old)

    assert not _named(report, "方針ごとに、封じ込めの順位が違う")["ok"], (
        "v1.39 の複製された方針でも順位の検査が通ってしまう"
    )
    # 弱い方の検査は通る。これが「1本で3本分を証明していた」形
    assert _named(report, "封じ込めの最良手が方針ごとに違う")["ok"]


def test_the_bend_is_placed_for_the_costliest_policy(balance):
    """折れ点は最も高くつく方針の L で置く（SPEC 8.2 手順7 / v1.40）。

    L に方針が入っていなかった頃、証拠保全最優先だけが
    「最良に解いても自分で折れ点を踏む」位置に置かれていた。
    折れ点を踏むかどうかが、渡された方針で決まってはいけない。
    """
    from irdojo import retrospective

    sc = load_scenario("ransomware-initial-response-01")
    lengths = {
        pol.id: retrospective.minimal_response(sc, pol)[0] for pol in sc.policies
    }
    bend = sc.damage.acceleration.threshold_minutes
    # 方針で L が変わること（変わらないなら、この検査は何も見ていない）
    assert len(set(lengths.values())) > 1, lengths
    for pid, length in lengths.items():
        assert length <= bend, f"{pid}: L {length}分 > 折れ点 {bend}分"
    check = _named(balance.report(sc), "折れ点の位置")
    assert check["ok"] and str(max(lengths.values())) in check["detail"]


# ─────────── 根絶（SPEC 5.8 / 9.4 #5） ───────────


def test_the_persistence_check_fails_when_the_board_loses_eradication(balance):
    """根絶の手を盤面から抜くと、persistence の検査が落ちること。

    v1.36 まで `ground_truth.persistence` は完全な死にフィールドだった。
    条項はコードにあり、ローダも通り、テストも全部緑だったのに、
    128通り全数で結果を変える組は0個 — **「持っているが効いていない」は、
    測定器に入って初めて見える。** ここが落ちない検査は、
    もう一度同じ穴を見逃す。

    盤面から根絶を抜くには `persistence` も空にする必要がある
    （空でないとローダが拒否する）。それが正しい依存関係で、
    「取り除く手が無いなら、そもそも永続化を書いてはいけない」。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )
    data = copy.deepcopy(raw)
    for a in data["actions"]:
        a.pop("eradicates", None)
    data["world"]["ground_truth"]["persistence"] = []

    toothless = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    report = balance.report(toothless)
    assert not _named(report, "persistence 条項が結果を変える封じ込めの組がある")["ok"]
    assert not _named(report, "根絶しない封じ込めは被害を止めきらない")["ok"]


def test_the_thorough_play_and_the_half_play_differ_only_in_containment(balance):
    """「止めるだけ」と「根絶まで行く」は、調査が1手も違わないこと。

    調査量の差が混ざると、被害の差が「根絶したから」なのか
    「早く降りたから」なのか切り分けられない。誤導を追う／棄却する の
    対で使ったのと同じ作り方であり、その作り方を守っているかを見る。
    """
    sc = load_scenario("ransomware-initial-response-01")
    profiles = {p.key: p for p in balance.PROFILES}
    full = balance.run_profile(sc, profiles["skilled"], [])
    half = balance.run_profile(sc, profiles["skilled_halfway"], [])

    assert full.decided_at == half.decided_at
    assert full.assessment == half.assessment
    # 違うのは封じ込めの側だけ
    assert full.eradicated and not half.eradicated
    assert half.damage > full.damage


def test_the_cheapest_cover_reaches_for_eradication_when_asked(balance):
    """最安被覆が、頼まれれば根絶する手を選ぶこと（L の校正に効く）。

    費用だけで選ぶと必ず安いほう（止めるだけ）へ倒れる。それで測った
    L は「盤面で最良の対応にかかる時間」を過小に見積もり、
    折れ点が**最良の対応をした人の手前**に置かれる。
    """
    sc = load_scenario("ransomware-initial-response-01")
    wanted = {"ws-042", "fs01"}
    by_id = sc.action_by_id

    plain = balance.cheapest_cover(sc, wanted)
    purging = balance.cheapest_cover(sc, wanted, wanted)

    assert not any(by_id[a].eradicates for a in plain)
    assert any("ws-042" in by_id[a].eradicates for a in purging)
    # 根絶まで行くほうが高い。安いほうが選ばれていないことの裏取り
    assert (sum(by_id[a].cost_minutes for a in purging)
            > sum(by_id[a].cost_minutes for a in plain))


def test_the_play_image_reads_refutation_the_same_way_the_engine_does(balance):
    """棄却の条件を読む場所を1つにする（Evidence.is_refuted）。

    複雑度の算出（SPEC 3.12）は refuted_by を「2つ揃って初めて棄却できる」＝
    AND と読み、この像の `_assess` は「どれか1つで棄却できる」＝ OR と
    読んでいた（v1.38 まで）。現行シナリオは誤導の refuted_by がどちらも
    1件だったので露出していなかっただけで、all の誤導を1つ置いた瞬間に
    「複雑度は多段と数えているのに、実測は台帳1枚で棄却できている」
    という食い違いになる。

    見張り方は、**片方だけ持った像が名指しを取り下げないこと**。
    ここが取り下げるなら、像は棄却の条件を独自に読んでいる。
    """
    from irdojo.engine import Decision, Engine

    sc = load_scenario("ransomware-initial-response-01")

    def named(*action_ids):
        e = Engine(sc, sc.meta.default_policy, None)
        for aid in action_ids:
            e.decide(Decision(kind="action", action_id=aid))
        return set(balance._assess(sc, e.state, weighs_refutations=True))

    # ev_005 だけ → ws-113 を名指しする
    assert "ws-113" in named("act_dc_authlog")
    # ＋台帳（ev_008）。all の誤導なので、まだ取り下げられない
    assert "ws-113" in named("act_dc_authlog", "act_asset_inventory")
    # ＋端末のトリアージ（ev_013）で揃う
    assert "ws-113" not in named(
        "act_dc_authlog", "act_asset_inventory", "act_triage_ws113"
    )
    # any の誤導は1つで足りる（同じ像が両方の型を正しく読む）
    assert "ws-107" in named("act_netflow_overview")
    assert "ws-107" not in named("act_netflow_overview", "act_backup_config")


def test_the_price_of_stopping_an_innocent_asset_has_teeth(balance):
    """「無実の資産を止めることに値段がある」が、値段の無い盤面で落ちること。

    v1.38 の端末は一律 2〜4/h で、帯の worst が 1200 だった。無実の端末を
    8時間止めても業務継続最優先で 0.2点しか動かず、**誤導に乗って
    無関係な部署を止めたことが、業務継続を選んだ学習者にさえ
    返っていなかった。** その盤面をそのまま復元して、検査が落ちることを見る。

    落ちない検査は、書いていないのと同じである。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )
    data = copy.deepcopy(raw)
    for a in data["world"]["assets"]:
        if a["id"] == "ws-107":
            a["business_impact_per_hour"] = 2
        if a["id"] == "ws-113":
            a["business_impact_per_hour"] = 4
    data["scoring"]["consequence_layer"]["normalization"]["business_impact"]["worst"] = 1200

    cheap = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    report = balance.report(cheap)
    assert not _named(report, "無実の資産を止めることに値段がある")["ok"]


def test_the_price_of_an_innocent_stop_is_not_the_price_of_ten_more_minutes(balance):
    """値段の測定に、その手にかかった時間の分を混ぜない。

    素朴に「押した場合」と「押さなかった場合」を比べると、`cost_minutes`
    の分だけ時計が進む。実測では 195分 → 205分 で 200分の出来事
    （現場の再起動）を跨ぎ、証拠保全が 1.00 → 0.75 に落ちて 2.09点の差に
    なっていた。**それは時計に歯があることの測定**であって、
    無実の資産を止めた代価ではない。混ざったままだと、
    per_hour を 0 にしても検査は通ってしまう。

    見張り方は、**業務影響を持たない資産にしたら検査が落ちること**。
    時間の分が混ざっていれば、per_hour が 0 でも差は残る。
    """
    import copy

    import yaml

    from irdojo.loader import SCENARIO_DIR, load_scenario_text

    raw = yaml.safe_load(
        (SCENARIO_DIR / "ransomware-initial-response-01.yaml").read_text(
            encoding="utf-8"
        )
    )
    data = copy.deepcopy(raw)
    for a in data["world"]["assets"]:
        if a["id"] in ("ws-042", "ws-107"):
            a["business_impact_per_hour"] = 0

    free = load_scenario_text(yaml.safe_dump(data, allow_unicode=True))
    costs = balance.innocent_containment_cost(free)
    assert costs, "無実の資産を止める手が盤面から消えている"
    for _aid, deltas in costs.items():
        for pid, delta in deltas.items():
            assert abs(delta) < 0.01, f"{pid}: 停止の値段が 0 でないのに per_hour は 0"
