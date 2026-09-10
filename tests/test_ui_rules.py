"""画面が、自分の規則を守っているかを見張る（SPEC 7.6.15）。

ここで見ているのは見た目ではなく、**画面が自分で自分の規則を教えられる形になっているか**である。

- 押せるものには同じ記号が付いているか（片方だけ記号が消えていないか）
- 見本は本物から作られているか（手で書いたモックは必ず腐る）
- 数字が何であるかを、その数字の隣で言っているか

実際に描いた DOM どうしの突き合わせは `tools/playtest.mjs` が行う
（凡例と実画面のフェーズ帯・束の見出し・帯の見出しを比較する）。
こちらは**そもそも二重に書ける形になっていないか**を、原稿の側で見る。
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


def strip_comments(js: str) -> str:
    """コメントを落とす。規則の理由はコメントに書くので、検査の対象から外す。

    行の途中の `//` も落とす。文字列の中に `//` を含むのは
    名前空間 URL の1件だけで、その行が途中で切れても検査には響かない。
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(line.split("//")[0] for line in js.split("\n"))


def literals(js: str) -> list[str]:
    """JS の文字列リテラルを、行をまたがずに拾う。"""
    out: list[str] = []
    for line in strip_comments(js).split("\n"):
        out += re.findall(r"'((?:[^'\\\n]|\\.)*)'", line)
        out += re.findall(r'"((?:[^"\\\n]|\\.)*)"', line)
    return out


# ── 押せるものの記号 ────────────────────────────────────


def test_open_marks_come_from_one_helper(app_js):
    """開く記号は1箇所でしか作らない。

    畳んだ連絡はシェブロンを消してあり、hover の色変化しか手がかりが無かった。
    同じ画面の証拠カードには ▸ が付いていたので、画面の中で
    「押せるもの」の記号規則が食い違っていた。連絡は全プレイで3〜4件しかなく、
    そのすべてが「1手見逃したら二度と読めない」状態に置かれていた。

    場所ごとに記号を書けるようにしておくと、また片方だけ消える。
    """
    body = strip_comments(app_js)
    assert body.count("'▸'") == 1, "▸ を作る場所が1つではない"
    assert body.count("'▾'") == 1, "▾ を作る場所が1つではない"
    helper = re.search(r"function caret\(open\)[^\n]*\n?", body)
    assert helper and "'▾'" in helper.group(0) and "'▸'" in helper.group(0), (
        "記号を作っているのが caret() ではない"
    )


def test_everything_foldable_uses_the_shared_mark(app_js):
    """畳めるものは、証拠・アクションの束・連絡のどれも caret() を通る。

    「押せば開く」と分かるかどうかが、盤面が動いた唯一の証拠を
    読めるかどうかを決める。
    """
    body = strip_comments(app_js)
    for func in ("evidenceCard", "phaseGroupHead", "renderIncoming"):
        start = body.index("function " + func)
        chunk = body[start : start + 2200]
        assert "caret(" in chunk, f"{func} が共通の記号を使っていない"


# ── 見本は本物から作る ──────────────────────────────────


def test_legend_does_not_copy_scenario_content(app_js, index_html, scenario):
    """凡例がシナリオの中身を書き写していないこと（SPEC 7.6.7）。

    実際に腐った: 凡例には「初動トリアージ › 本格調査 › 封じ込め・対応」の
    3段が手で書かれていたが、実画面は2段になっていた。凡例で見たものが
    プレイ画面に無いので、初見の学習者は「自分は何かを飛ばしたのか」と思う。

    フェーズ名・アクション名・束の名前・資産名は、その時のビューから描く。
    画面の原稿にそれらが literal で現れたら、また同じことが起きる。
    """
    forbidden = set()
    for p in scenario.phases:
        forbidden.add(p.label)
    for a in scenario.actions:
        forbidden.add(a.label)
        if a.group:
            forbidden.add(a.group)
    for a in scenario.world.assets:
        forbidden.update({a.id, a.label})
    for q in scenario.open_questions:
        forbidden.add(q.label)
    for e in scenario.evidence:
        forbidden.add(e.summary)

    found = sorted(set(literals(app_js)) & forbidden)
    assert not found, f"画面の原稿にシナリオの中身が書き写されている: {found}"

    # HTML は静的なので、ここは部分一致で見る（資産 id は短く紛れやすい）
    text = re.sub(r"<!--.*?-->", "", index_html, flags=re.S)
    hits = sorted(w for w in forbidden if len(w) >= 4 and w in text)
    assert not hits, f"index.html にシナリオの中身が書かれている: {hits}"


def test_legend_and_screen_share_their_parts(app_js):
    """凡例と本体は、同じ関数から同じ部品を描く。

    見本を別に書けるようにしておくと、画面を直したときに凡例だけ古くなる。
    フェーズ帯・アクションカード・束の見出し・押した結果の見出しの4つが対象。
    """
    body = strip_comments(app_js)
    # 部品を組み立てる場所は、それぞれ1つだけ
    assert body.count("'phase-bar'") == 1, "フェーズ帯を組み立てる場所が2つある"
    assert body.count("'act-group-head'") == 1, "束の見出しを組み立てる場所が2つある"
    assert body.count("'outcome-act'") == 1, "結果の見出しを組み立てる場所が2つある"
    assert body.count("'未実行 '") == 1, "未実行の数え方が2箇所にある"

    legend = body[body.index("function renderLegend") : body.index("function renderFlow")]
    for part in ("phaseBar(view.phases)", "phaseGroupHead(", "actionCard(",
                 "outcomeHead(", "evidenceCard(", "cloneWithoutIds("):
        assert part in legend, f"凡例が本体の部品を使っていない: {part}"


def test_legend_is_reachable_during_play(app_js, index_html):
    """凡例はプレイ中も開ける（SPEC 7.6.15）。

    ●/○ の意味も、箱が琥珀色になる条件も、思い出したいのは開始前ではなく最中である。
    開始前にしか出ないなら、その説明は無いのと同じ。
    """
    assert 'id="btn-show-legend"' in index_html
    assert 'id="modal-legend"' in index_html
    body = strip_comments(app_js)
    assert "openModal('modal-legend')" in body
    # 同じ関数が、ブリーフィングとモーダルの両方を描く
    hosts = set()
    for line in body.split("\n"):
        if "renderLegend(" not in line or "function renderLegend" in line:
            continue
        hosts.update(re.findall(r"\$\('([a-z-]+)'\)", line))
    assert hosts == {"brief-mini", "brief-legend", "legend-mini", "legend-notes"}, (
        f"凡例の描き先が2つない: {hosts}"
    )


# ── 数字が何であるかを、その隣で言う ──────────────────────


def test_elapsed_time_is_marked_everywhere(app_js):
    """経過時間には印を付ける（SPEC 7.6.15）。

    画面には2種類の hh:mm が混ざる。ブリーフィングの「03:12」とログの
    「02:19:41」は壁時計、ヘッダの「08:50」は開始からの経過である。
    実プレイで「経過時間 08:50」を朝の8時50分と読み、講評の
    「あなたの経過時間: 610分」を見て初めて8時間50分だと分かった、が報告されている。

    印は1箇所で付ける。付け忘れた場所ができた時点で、印の意味が消えるため。

    **時刻と長さは別の整形関数に分ける。** 「+04:25 に押した」は時刻、
    「1時間35分遅く」は長さで、後者に「+」を付けると時刻に見える。
    分ける代わりに、時分へ直す場所はこの2つの外に作らせない。
    """
    body = strip_comments(app_js)
    formatters = ("function elapsed(", "function duration(")
    for name in formatters:
        assert name in body, f"{name} が無い"

    # 時分に直しているのは、この2つの整形関数の中だけであること
    starts = {body.index(n): n for n in formatters}
    at = -1
    found = []
    while True:
        at = body.find("Math.floor(minutes / 60)", at + 1)
        if at < 0:
            break
        owner = max((i for i in starts if i < at), default=None)
        assert owner is not None, "整形関数の外で時分に直している"
        found.append(starts[owner])
    assert sorted(found) == sorted(formatters), f"時分に直す場所が余分にある: {found}"

    fn = body[body.index("function elapsed(") :]
    fn = fn[: fn.index("\n}")]
    assert "'+'" in fn, "経過時間に印が付いていない"
    # 長さの側に印を付けない（付けると時刻と見分けが付かなくなる）
    dur = body[body.index("function duration(") :]
    dur = dur[: dur.index("\n}")]
    assert "'+'" not in dur, "長さに経過時間の印が付いている"
    # 呼ぶ側が印を剥がしていないこと
    assert "elapsed(" in body and "hhmm(" not in body


def test_consequences_are_labelled_where_they_are_shown(index_html):
    """帯の3つの数字が何であるかを、帯の中で言う（SPEC 6.1 / 7.6.15）。

    累積被害はプレイ中に20倍以上に増え、押すたびに動く。画面で最も大きく
    最も動く数字であり、何も言わなければ学習者はそれを最適化対象だと読む。
    「採点しません」はトップのモーダルにしか書かれていなかった。
    """
    strip = index_html[index_html.index('class="strip-stats"') :]
    strip = strip[: strip.index("</div>\n    </div>")]
    assert "帰結" in strip and "採点しません" in strip, "帯が帰結の位置づけを言っていない"
    # 理由まで帯に書くと説明文で埋まる。押せば開く形になっていること
    assert 'id="btn-what-play"' in strip


def test_only_actions_rewrite_the_result_box(app_js):
    """**アクション以外の決定は、直前の結果を書き換えないこと**（v1.40）。

    フェーズ移行も被疑判定の宣言も `decide()` を通る。素で上書きすると
    `revealed` が 0 に戻り、直前に押した調査の箱が、宣言した瞬間に
    「何も出てこなかった。」に化ける — **画面が過去を書き換えて嘘をつく。**
    証拠は手元にあるのに、見つけた事実だけが消えて見えた。
    対応フェーズの初画面を実際に撮って初めて見つかった。

    ここで見るのは、上書きが `kind === 'action'` で守られているかどうか。
    """
    body = strip_comments(app_js)
    i = body.index("S.lastOutcome.revealed =")
    guard = body.rfind("if (", 0, i)
    line = body[guard:i]
    assert "payload" in line and "'action'" in line, (
        "結果の箱の更新が、アクション以外の決定からも通っている: " + line.strip()
    )


# ── シナリオが2本以上あるときの選択 ──────────────────────


def test_the_scenario_list_can_actually_be_chosen(app_js):
    """一覧に並べたものは押して選べること。

    **1本しか無い間、この欠落は見えない。** 一覧を描いたあと
    無条件に先頭を選んでいたので、カードに押し手が付いていなくても
    画面は正しく動いていた。2本目を足した瞬間、
    「並んではいるが2本目は始められない」という形になる。

    見るのは「押せる形になっているか」までで、選んだ結果は
    `tools/playtest.mjs` が実画面で確かめる。
    """
    src = strip_comments(app_js)
    body = src[src.index("function renderScenarioList"):src.index("function selectScenario")]
    assert "addEventListener('click'" in body, "カードに押し手が付いていない"
    assert "selectScenario" in body, "押しても選択が切り替わらない"
    # キーボードでも選べること（div に role を付けた以上、Enter で動く必要がある）
    assert "keydown" in body and "'Enter'" in body


def test_switching_scenarios_does_not_carry_the_previous_policy(app_js):
    """シナリオを選び直したら、方針は選び直したシナリオのものに戻ること。

    方針 id はシナリオごとに定義される。持ち越すと、そのシナリオに
    存在しない id をサーバへ送ることになる。同梱の2本がたまたま
    同じ id を使っていると、この間違いは動いてしまう。
    """
    src = strip_comments(app_js)
    body = src[src.index("function selectScenario"):]
    body = body[:body.index("\n}\n") + 3]
    assert "S.scenario.id !== sc.id" in body, "選び直しを検出していない"
    assert "S.policy = sc.default_policy" in body, "方針が持ち越される"

def test_the_screen_does_not_decide_which_way_the_misdirection_went(app_js):
    """誤導の向きは、画面が判定しない（SPEC 3.4 / 6.6）。

    誤導には2つの向きがある — 疑わせて名指しさせる側と、白と読ませて
    名指しから落とさせる側で、講評の文面が正反対になる。
    向きを持っているのは `direction` だけで、画面が
    「名指しに入っていたか」から推測すると、同じ誤導の説明が
    2つの文面に割れる（`refuted` で同じ失敗を一度している）。

    見るのは2つ。分岐が `direction` を読んでいることと、
    `assessment` を引き合いに出して自前で判定していないこと。
    """
    body = strip_comments(app_js)
    start = body.index("function blockCounterfactuals")
    end = body.index("function blockPolicy")
    block = body[start:end]
    assert "cf.direction" in block, "向きの分岐が direction を読んでいない"
    assert "assessment" not in block, "画面が名指しから向きを判定している"



# ── 資産盤（SPEC 7.6.16） ──────────────────────────────


def test_the_topology_is_drawn_in_exactly_one_place(app_js, index_html):
    """構成図の描き先は1つに畳んだ。**モーダルは廃した**（v1.44）。

    プレイ中の構成図はヘッダの「構成」から開くモーダルにあり、
    開かない限り存在しないのと同じだった。学習者は「120分使って
    dc01 に一度も触れていない」ことに最後まで気づけない。

    盤に統合したので、モーダルとボタンは残さない。残すと同じ図が
    2つの経路から描かれ、片方だけ古くなる。
    """
    body = strip_comments(app_js)
    assert "modal-topo" not in index_html and "btn-show-topo" not in index_html, (
        "構成のモーダルが残っている"
    )
    assert "modal-topo" not in body and "btn-show-topo" not in body
    # 盤の入れ物を組み立てる場所は1つ
    assert body.count("el('details', 'board')") == 1, "盤の枠を作る場所が1つではない"


def test_the_board_and_its_sample_come_from_the_same_function(app_js):
    """凡例の盤も、本物と同じ関数から出る（SPEC 7.6.7）。

    ここは一度腐らせている（フェーズ帯が3段のまま残った）。盤は記号が
    4つある — 手・証・止・除 — ので、見本を手で書くと必ずどれかが古くなる。
    """
    body = strip_comments(app_js)
    legend = body[body.index("function renderLegend"):body.index("function renderFlow")]
    assert "boardBox(" in legend, "凡例が盤の部品を使っていない"
    assert "renderTopology(" in legend, "凡例が本物の図を描いていない"
    play = body[body.index("function renderBoard"):]
    play = play[: play.index("\n}\n") + 3]
    assert "boardBox(" in play and "renderTopology(" in play


def test_the_meaning_of_every_mark_is_stated_in_one_place(app_js):
    """記号の意味は1箇所で言う（SPEC 7.6.7「画面の記号に意味を与える」）。

    盤の中の説明と凡例の説明が別々に書けると、片方だけ直したときに
    「止」の意味が画面の中で2つになる。意味を作る関数は1つに畳んでおく。
    """
    body = strip_comments(app_js)
    assert body.count("function boardKey") == 1
    key = body[body.index("function boardKey"):]
    key = key[: key.index("\n}")]
    for mark in ("線", "手", "証", "止", "除"):
        assert mark in key, f"記号 {mark} の意味が盤の中で言われていない"


def test_the_board_does_not_urge_the_learner_to_press_more(app_js):
    """盤は促さない。0 を警告色にしない（SPEC 6.6）。

    網羅は「高くつく」べきであって「正解」ではない。触れていない資産を
    赤や琥珀で立てると、盤が「全部押せ」と教えることになる。
    薄くするところまでが写像で、そこから先は指示である。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    line = [ln for ln in css.split("\n") if ".tally.none" in ln]
    assert line, "触れていない資産の見せ方が決まっていない"
    assert "--bad" not in line[0] and "--warn" not in line[0], (
        "触れていない資産が警告色になっている: " + line[0]
    )


# ── 次に狙う層（SPEC 3.11 / 7.6.13） ─────────────────────


def test_the_screen_does_not_compose_the_next_target_line(app_js):
    """次の的の文面は、画面が組み立てない（v1.44）。

    画面で作れる形にしておくと、いつか誰かが「方針適合 84 → 90」を足す。
    **数字を主語にした瞬間、次のプレイはスコア最大化ゲームになる**（1.3）。
    サーバ側には算用数字を1文字も書かせない検査があるので、
    組み立てをそちらに寄せておけば、規則が1箇所で効く。
    """
    body = strip_comments(app_js)
    i = body.index("if (rep.next_target)")
    block = body[i : body.index("\n  }", i)]
    assert "headline" in block and "note" in block, "サーバの文面を使っていない"
    for forbidden in ("fact_score", "policy_score", "composite_score", "+ '"):
        assert forbidden not in block, (
            f"画面が次の的の文面を組み立てている: {forbidden}"
        )
