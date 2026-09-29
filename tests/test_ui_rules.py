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


@pytest.fixture(scope="module")
def playtest_mjs() -> str:
    """遊んで確かめる側の原稿。**検査そのものが痩せていないかを見る。**"""
    return (WEB.parent / "tools" / "playtest.mjs").read_text(encoding="utf-8")


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
    # 入口の流れの帯（7.6.20）だけは外す。ここはフェーズ名を**わざと**書いており、
    # `test_the_flow_strip_covers_exactly_the_phases_the_engine_has` が
    # 「エンジンの持つフェーズと集合として一致すること」を強制している。
    # **この規則より強い** — あちらはズレたら落ちるが、こちらは書かなければ通る。
    text = re.sub(r"<ol class=\"ir-flow\".*?</ol>", "", text, flags=re.S)
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


# ── 押した結果の結論（SPEC 7.6.16 / v1.45） ─────────────────


def test_the_scroll_after_pressing_aims_at_the_conclusion(app_js):
    """押したあとの視点は、**結論が読めるところ**まで送る。

    周7 で資産盤を足したとき、確認したのは「盤と結果の見出しが載る」
    までだった。その下にある結論（「分かったこと N 件」「何も出て
    こなかった。」）は 1440×800 でも 1440×1000 でも画面の外にあり、
    押した直後に読めるのは端末の数字だけだった。
    **空振りした手と当たった手が、同じ顔になっていた。**

    列の頭へ送るだけの実装には戻せない。結論の要素を見て決めること。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function scrollAfterAction"):]
    fn = fn[: fn.index("\n}")]
    assert ".outcome-msg" in fn, "押したあとの視点が結論を見ていない"
    assert "innerHeight" in fn, "画面の高さを見ていないので、狭い画面で落ちる"


def test_the_board_starts_folded_so_the_conclusion_fits(app_js):
    """盤の既定は畳む。開いた状態を既定に戻さない。

    開いたままだと図が 300px 強を占め、結論が下へ押し出される
    （実測 y=949 / 画面 800）。畳んだままでも数の一行（boardTally）が
    残るので、「14手を終えて、この資産には手 0」に気づく道は閉じない。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderBoard"):]
    fn = fn[: fn.index("\n}")]
    assert "b.box.open = !!S.boardOpen;" in fn, (
        "盤の既定が畳んだ状態になっていない（S.boardOpen !== false に戻すと"
        "初回が開いた状態になり、結論が画面外へ出る）"
    )


def test_the_folded_board_still_says_what_it_counted(app_js):
    """畳んだ盤も、数えたものは一行で言う。**ただし促さない**（SPEC 6.6）。

    畳むことで盤の値打ち（触れていない資産に気づく）まで捨てたら、
    結論を見せるために盤を殺したことになる。数は残す。
    残りの数（「あと3つ触っていない」）は書かない — 書いた瞬間に
    盤が「押せ」と言い始める。
    """
    body = strip_comments(app_js)
    assert body.count("function boardTally") == 1
    fn = body[body.index("function boardTally"):]
    fn = fn[: fn.index("\n}")]
    for word in ("手を当てた資産", "止めた", "取り除いた"):
        assert word in fn, f"畳んだ盤が {word} を言っていない"
    for urge in ("あと", "残り", "まだ", "触れていない"):
        assert urge not in fn, f"畳んだ盤が促している: {urge}"


def test_the_playtest_watches_the_conclusion_at_more_than_one_height(playtest_mjs):
    """遊んで確かめる側も、結論の可視性を**複数の画面の高さで**見ること。

    この不具合は高さで出方が変わった。1440×1000 では入り、1440×800 では
    落ちる回があった。1つの高さでしか見ない検査は、直したつもりの回帰を
    そのまま通す（dpr 1 でしか見なかった canvas と同じ失敗である）。
    """
    body = playtest_mjs
    assert "checkResultConclusionVisible" in body, "結論の可視性を見ていない"
    assert ".outcome-msg" in body, "結論の要素を指していない"
    heights = re.findall(r"HEIGHTS\s*=\s*\{([^}]*)\}", body)
    assert heights, "画面の高さを切り替える表が無い"
    assert len(re.findall(r":\s*(\d{3,4})", heights[0])) >= 2, (
        "画面の高さが1つしか無い（高さで出方が変わる不具合を素通りする）"
    )


# ── 事件の時計（SPEC 7.6.17） ─────────────────────────


def test_the_axis_is_built_only_from_what_the_learner_holds(app_js):
    """時間軸に並ぶのは、**学習者が取った証拠だけ**である。

    ここは真実に触れられる場所にしない。誤導かどうかで色・順序・
    大きさを変えれば、図がそのまま答えになる。組み立ての入口を
    `obtained_evidence` の1本に閉じておけば、そもそも触れない。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function timelineEntries"):]
    fn = fn[: fn.index("\n}")]
    assert "obtained_evidence" in fn, "時間軸が手元の証拠から作られていない"
    for forbidden in ("misleading", "points_to", "clears", "ground_truth"):
        assert forbidden not in fn, f"時間軸が {forbidden} を見ている"


def test_the_two_clocks_are_not_mixed_on_the_axis(app_js):
    """**画面には2種類の hh:mm が混ざる。** 何の時計かをその場で言う。

    被害グラフと帯の数字は経過時間（+03:20）、時間軸は事件の壁時計
    （02:34）である。印が無いと「02:34」は開始から2時間34分に読める
    （周4 が同じ取り違えを一度直している）。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderTimeline"):]
    fn = fn[: fn.index("\nfunction ")]
    assert "tl-note" in fn, "時間軸に、何の時刻かを言う行が無い"
    note = [t for t in literals(fn) if "経過時間" in t]
    assert note, "時間軸が、帯の経過時間と別ものだと言っていない"
    # 経過時間の表記（+）は elapsed() の仕事。時間軸の側で作らない
    assert "elapsed(" not in fn, "時間軸が経過時間の表記を混ぜている"


def test_the_axis_and_its_sample_come_from_the_same_function(app_js):
    """凡例の時間軸も、本物と同じ関数から出る（SPEC 7.6.7）。

    手で書いた見本は必ず腐る。ここは既にフェーズ帯で一度腐らせている。
    """
    body = strip_comments(app_js)
    assert body.count("function renderTimeline") == 1
    legend = body[body.index("function renderLegend"):body.index("function renderFlow")]
    assert "renderTimeline(" in legend, "凡例が本物の時間軸を描いていない"


def test_the_legend_sample_is_drawn_after_the_screen_is_shown(app_js):
    """見本の描画は、画面に入れてから。**条件を付けない。**

    `clientWidth` が 0 のまま段を割ると全部が1段に重なる（被害グラフと
    構成図で2度やっている）。さらに hard はグラフも盤も出ないので、
    「グラフか盤があれば描く」にすると hard だけ時間軸が描かれない。
    """
    body = strip_comments(app_js)
    legend = body[body.index("function renderLegend"):body.index("function renderFlow")]
    assert "if (cv || board) {" not in legend, (
        "見本の描画が、グラフか盤があるときだけになっている（hard で落ちる）"
    )
    assert "renderTimeline(tl, view, { sample: true });" in legend


def test_the_axis_says_how_many_it_could_not_place(app_js):
    """軸に並ばなかった資料の**件数は出す。ただし促さない**（SPEC 6.6）。

    台帳・期間の集計は出来事の時刻を持たないので軸には並ばない。
    そこを黙ると「軸に出ないものは無価値」と読める — 台帳は
    この盤面で最も安い棄却の手段である。件数だけ置いて、
    「取れ」とも「取るな」とも言わない。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderTimeline"):]
    fn = fn[: fn.index("\nfunction ")]
    rest = [t for t in literals(fn) if "時刻を持たない" in t]
    assert rest, "軸に並ばなかった資料の件数を言っていない"
    for urge in ("取りましょう", "押して", "べき", "無駄"):
        assert all(urge not in t for t in rest), f"軸が促している: {urge}"


def test_thinking_aloud_is_folded_by_default(app_js):
    """「考えられること」は既定で畳む（SPEC 5.4 / 7.6.8）。

    毎回読むものではなく「詰まったときに考えを広げる道具」である。
    開いたままだと、押した直後の視野から結果が落ちる — 実測で
    押した瞬間の画面から 674px 下、**一度も目に入っていなかった。**
    畳むのをやめるなら、その場所を別の何かから奪う必要がある。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function evidenceCard"):]
    fn = fn[: fn.index("\nfunction ")]
    assert "el('details', 'ev-reading ev-maybe')" in fn, (
        "考えられることが畳める形になっていない"
    )
    assert ".open = true" not in fn, "考えられることが既定で開いている"
    assert "caret(" in fn, "畳んだ印が、他の畳めるものと違う"


def test_the_playtest_watches_the_axis(playtest_mjs):
    """遊んで確かめる側も、時間軸を見張ること。

    見るのは「描かれているか」だけでは足りない。**取っていない証拠が
    出ていないか**と**時刻順か**まで見る — 前者は答えの漏洩、
    後者は図が図でなくなる壊れ方である。
    """
    body = playtest_mjs
    assert "checkTimeline" in body, "時間軸を見ていない"
    fn = body[body.index("async function checkTimeline"):]
    fn = fn[: fn.index("\nasync function ")]
    assert "時刻順" in fn, "並びが時刻順かを見ていない"
    assert "取っていない証拠" in fn, "取っていない証拠が出ていないかを見ていない"
    assert "重なっている" in fn, "札が重なっていないかを見ていない"


# ── 入口（SPEC 7.6.5） ─────────────────────────────────


def test_the_entrance_does_not_carry_the_numbers_it_shows(index_html, app_js):
    """入口の 3×3 は、原稿の側に1つも数字を持たないこと。

    **手で書き写した数字は、盤面を触った日から嘘になる。**
    凡例の手書きモックが3フェーズのまま腐ったのと同じ壊れ方をする
    （SPEC 7.6.7）。入口の点数は `/api/policy-swap` からしか来ない。
    """
    from irdojo import swap

    data = swap.load()
    assert data is not None, "生成物が読めない（--emit-swap を流し直す）"
    scores = {v for s in data.scenarios for r in s.rows for v in r.scores.values()}
    top = index_html[index_html.index('id="screen-top"'):]
    top = top[: top.index("</section>")]
    for n in sorted(scores):
        assert str(n) not in top, f"入口の原稿に点数 {n} が書いてある"
    body = strip_comments(app_js)
    fn = body[body.index("function renderPolicySwap"):]
    fn = fn[: fn.index("\nfunction ")]
    for n in sorted(scores):
        assert str(n) not in fn, f"描画側に点数 {n} が書いてある"


def test_the_best_cell_is_decided_by_the_numbers(app_js):
    """各列の最高点の印は、**数字から決める。**

    対角に印を焼き付けると、盤面が動いて対角が最高でなくなった日に、
    表だけが正しい顔で嘘をつく。`tools/balance.py` の
    「同じ行動列が方針で違う評価になる」が落ちていることに、
    入口を見た人は気づけない。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderPolicySwap"):]
    fn = fn[: fn.index("\nfunction ")]
    assert "Math.max" in fn, "最高点を数字から出していない"
    assert "=== best[c.id]" in fn, "印の条件が数字の比較になっていない"


def test_the_entrance_keeps_its_prose_short(index_html):
    """入口の文章は増やさない。**足すなら減らす。**

    以前ここには5ブロックあり、そのうち2つは「正解は一つではない」を
    2回言っていた。表がその2つの仕事をしているので、文章は
    「何を測るか」だけに戻した。ここが再び伸びたら、
    表を置いた意味が消えている。

    **数えるのは段落の数ではなく字数である**（v1.52）。
    段落と箇条書きの数で見ていたときは、
    「知らない人には読めない」（v1.51）と「何をする道具か分からない」（v1.52）を
    直すのに `<li>` が増えるだけで落ちた。**落ちるべきなのは
    読む量が増えたときで、構造が増えたときではない。**

    **畳んだぶんは別に数える**（v1.54）。利用者が言ったのは
    「普通はざっくり概要が分かればいい」で、縛るべきは
    **開かずに目に入る量**である。畳み込みの中身も無制限ではない —
    無制限なら、畳めば何でも書けることになる。

    | | 既定で見える | 畳んである |
    |---|---|---|
    | v1.50（差別化点だけ） | 141字 | — |
    | v1.51（概要と流れの帯） | 206字 | — |
    | v1.52（遊び方3手） | 207字 | — |
    | v1.54（文章を左へ／手順を畳む） | **147字** | 65字 |
    """
    top = index_html[index_html.index('id="screen-top"') :]
    top = top[: top.index("</section>")]
    claim = top[top.index('<div class="top-claim">') : top.index('<section class="top-picks">')]
    claim = re.sub(r"<!--.*?-->", "", claim, flags=re.S)

    # 構造（流れの帯・遊び方の3手）は散文から外して数える。
    # **外した分は別に縛る** — 外しただけでは、箇条書きに文章を流し込める
    lists = re.findall(r'<ol class="(ir-flow|play-steps)".*?</ol>', claim, flags=re.S)
    assert sorted(lists) == ["ir-flow", "play-steps"], f"入口の一覧が増減している: {lists}"
    assert "<ul" not in claim, "入口に箇条書きが戻っている"

    def prose(html: str) -> str:
        html = re.sub(r'<ol class="(ir-flow|play-steps)".*?</ol>', "", html, flags=re.S)
        return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", html))

    folds = re.findall(r"<details.*?</details>", claim, flags=re.S)
    visible = claim
    for block in folds:
        visible = visible.replace(block, "")

    seen = prose(visible)
    assert len(seen) <= 170, f"開かずに見える散文が {len(seen)}字（上限 170）"
    hidden = sum(len(prose(block)) for block in folds)
    assert hidden <= 160, f"畳み込みの中の散文が {hidden}字（上限 160）"

    # 一覧のほうも、1項目が文章にならないこと
    for block in re.findall(r'<ol class="(?:ir-flow|play-steps)".*?</ol>', claim, flags=re.S):
        items = re.findall(r"<li[^>]*>(.*?)</li>", block, flags=re.S)
        assert len(items) <= 5, f"一覧の項目が増えている: {len(items)}"
        for item in items:
            text = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", item))
            assert len(text) <= 32, f"一覧の項目が文章になっている: {text}"

def test_the_entrance_and_the_list_share_one_card_builder(app_js):
    """入口の札と選択画面の札は、同じ関数から出ること。

    別々に書くと、片方だけが古い並びのまま残る。
    **実際に描いた DOM どうしの突き合わせは `playtest.mjs` が行う**
    （入口の題名と一覧の題名を比べる）。こちらは
    そもそも二重に書ける形になっていないかを原稿の側で見る。
    """
    body = strip_comments(app_js)
    assert body.count("function fillScenarioCard") == 1
    for fn in ("renderScenarioList", "renderTopScenarios"):
        part = body[body.index("function " + fn):]
        part = part[: part.index("\nfunction ")]
        # 呼び方は `(card, sc)` と `(card, sc, opts)` の2通りある。
        # **見ているのは引数ではなく、札を組む場所がここに無いこと。**
        # 入口は題だけを出すので第3引数で行を落とすが、落とす判断は
        # 組み立て側にあり、呼ぶ側は相変わらず何も書いていない。
        assert "fillScenarioCard(card, sc" in part, f"{fn} が札を自前で組んでいる"
        assert "card-title" not in part, f"{fn} が札の中身を自前で書いている"


def test_the_entrance_hides_the_table_rather_than_ageing_it(app_js):
    """生成物が無い・古いときは、表を**出さない**。

    古い数字を出すくらいなら、入口から図が1つ消えるほうがよい。
    消えたことは `playtest.mjs` が落として教える。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderPolicySwap"):]
    fn = fn[: fn.index("\nfunction ")]
    assert "host.hidden = true" in fn, "表が出せないときに箱ごと消していない"
    assert "!data.available" in fn, "サーバが出さないと言ったことを見ていない"


def test_the_playtest_watches_the_entrance(playtest_mjs):
    """遊んで確かめる側も、入口を見張ること。

    入口は「文章が5ブロック・図が0・下が真っ黒」の状態で何周も残った。
    **誰も入口を測っていなかったからである。**
    一番狭いところ（1280×720）で収まるかまで見る。
    """
    body = playtest_mjs
    assert "checkTopScreen" in body, "入口を見ていない"
    fn = body[body.index("async function checkTopScreen"):]
    fn = fn[: fn.index("\nasync function ")]
    assert "swap-grid" in fn, "主張の図が出ているかを見ていない"
    assert "top-scenarios" in fn, "入っている演習が並んでいるかを見ていない"
    assert "空いている" in fn, "下がどれだけ空いているかを見ていない"
    assert "1280" in body and "720" in body, "一番狭いところで見ていない"


# ─────────── 入口が「何の道具か」を言えているか（SPEC 7.6.20） ───────────


def test_the_entrance_says_what_incident_response_is(index_html):
    """**入口は、知らない人に向かって最初に一言で説明すること。**

    v1.50 までの入口には差別化点しか書いていなかった
    （「正しかったかではなく根拠が健全だったか」）。それは既に
    インシデント対応を知っている人にしか読めない文で、知らない人には
    **黒地に文字が並んでいるのと同じ**である。概要 → 扱う範囲 →
    その中で何を測るか、の順に並んでいることを見る。
    """
    top = index_html[index_html.index('id="screen-top"') :]
    top = top[: top.index("</section>")]
    assert "インシデントレスポンスとは" in top, "何の仕事かを言っていない"
    assert "ir-flow" in top, "扱う範囲を図で示していない"
    # 並び順。概要が主張より後に来ていたら、絞る順になっていない
    assert top.index("インシデントレスポンスとは") < top.index("lede-claim")
    assert top.index("ir-flow") < top.index("lede-claim")


def test_the_flow_strip_covers_exactly_the_phases_the_engine_has():
    """**流れの帯で色が付くのは、エンジンが実際に持つフェーズだけ。**

    帯は手で書いた図なので、放っておけば腐る（凡例のモックが3フェーズの
    まま腐ったのと同じ壊れ方 / 7.6.7）。同梱シナリオのフェーズ名を全部集め、
    それが `data-covered` の札に出ていること、かつ
    **色の付いた札がフェーズより多くない**ことを見る。
    扱っていない工程まで色が付けば、入口がこの道具の守備範囲について嘘をつく。
    """
    from irdojo.loader import list_scenarios

    html = (WEB / "index.html").read_text(encoding="utf-8")
    strip = html[html.index('class="ir-flow"') :]
    strip = strip[: strip.index("</ol>")]
    covered = re.findall(r"<li data-covered=\"1\">([^<]+)</li>", strip)
    labels = {p.label for sc in list_scenarios() for p in sc.phases}
    assert labels, "同梱シナリオにフェーズが無い"
    assert set(covered) == labels, (
        f"帯の色 {covered} とエンジンのフェーズ {sorted(labels)} が食い違う"
    )
    # 色の付いていない札に盤面の言葉を隠さない。この帯は
    # `test_legend_does_not_copy_scenario_content` の走査から外してあるので、
    # 中身を見張るのはここだけである
    plain = re.findall(r"<li>([^<]+)</li>", strip)
    assert not (set(plain) & labels), f"色の付いていない札にフェーズ名がある: {plain}"
    forbidden = {
        w
        for sc in list_scenarios()
        for w in [a.label for a in sc.actions]
        + [a.label for a in sc.world.assets]
        + [q.label for q in sc.open_questions]
    }
    assert not (set(plain) & forbidden), f"帯に盤面の言葉が混ざっている: {plain}"


def test_the_entrance_does_not_repeat_the_panel(index_html):
    """**足したぶん、減らす。**

    入口に出していた箇条書き2つは「何を測るのか」の中に一字一句同じものが
    あった。概要を足すときに、重なっているほうを落とした。
    片方に戻すだけなら構わないが、**両方に出ている状態に戻さない。**
    """
    top = index_html[index_html.index('id="screen-top"') : index_html.index('id="screen-select"')]
    panel = index_html[index_html.index('id="modal-what"') :]
    for line in (
        "もっともらしい偽の手がかりに引っ張られなかったか",
        "説明できていないことを残したまま決断しなかったか",
    ):
        assert line in panel, "「何を測るのか」から消えている"
        assert line not in top, "入口と「何を測るのか」が同じ文を二度出している"


def test_the_card_shows_the_policies_not_the_complexity(app_js):
    """**選ぶ画面の札に出すのは、難しさではなく綱引きの名前。**

    複雑度（★1〜5）はローダが内容から算出する正しい数字だが、
    同梱5本すべてが 3 に落ちる（raw 8.86〜9.92）。執筆ガイド（8.1）が
    薦める規模が、その定義域の一点だからである。**壊れているのではなく
    定数なので、絞り込みには使えない。** 画面からは外し、設計の道具として
    `balance.py` に残した（3.12）。

    代わりに出すのは方針3つの名前。**漏洩にならない** — 方針はこの札の
    すぐ下で学習者自身が選ぶもので、盤面の真相ではない。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function fillScenarioCard") :]
    fn = fn[: fn.index("\nfunction ")]
    assert "sc.policies" in fn, "札が方針を出していない"
    assert "複雑度" not in fn and "★" not in fn, "札がまだ複雑度を出している"
    assert "複雑度" not in body, "どこかに複雑度の表示が残っている"


def test_the_entrance_says_what_you_actually_do(index_html):
    """**「他と何が違うか」の前に、「何をする道具か」を言うこと。**

    v1.51 で「インシデントレスポンスとは何の仕事か」までは言えるように
    なったが、**この道具で何をするのか**はまだどこにも書いていなかった
    （利用者の指摘：「特徴だけじゃん。ベースの説明がないって話」）。
    領域の説明と差別化点の間に、操作の手順が抜けていた。

    書くのは**手順**であって、手の中身でも巧い順番でもない — それは
    盤面の話になる（7.7.3）。

    **手順は畳んである**（v1.54）。既定で見えるのは
    「何の仕事か／どこを扱うか／何を測るか」までで、
    手順は開いた人にだけ出る。
    """
    top = index_html[index_html.index('id="screen-top"') :]
    top = top[: top.index("</section>")]
    assert "play-steps" in top, "何をするのかが書かれていない"
    steps = top[top.index('class="play-steps"') :]
    steps = steps[: steps.index("</ol>")]
    labels = re.findall(r"<b>([^<]+)</b>", steps)
    assert len(labels) == 3, f"手順が3つでない: {labels}"
    # 概要（何の仕事か → どこを扱うか → 何を測るか）が先。手順はその後ろ
    assert top.index("ir-flow") < top.index("lede-claim") < top.index("play-steps")


def test_the_entrance_folds_the_details_and_opens_nothing_by_default(index_html):
    """**ざっくり分かればよい人は、開かずに進める。**

    利用者の指摘：「文章が基本多すぎるので畳み込みを使って、
    みたい人が見れるように。普通はざっくりと概要が分かればいい」。

    畳み込みは `<details>` にする — JS を1行も足さずに済み、
    キーボードでも開け、読み上げにも最初から乗る。
    **`open` を書かない。** 書いた瞬間に「畳んである」が嘘になる。
    """
    top = index_html[index_html.index('id="screen-top"') :]
    top = top[: top.index("</section>")]
    folds = re.findall(r"<details[^>]*>", top)
    assert folds, "入口に畳み込みが無い"
    for tag in folds:
        assert "open" not in tag, f"既定で開いている畳み込みがある: {tag}"
    # 畳むのは詳細だけ。概要そのものを畳んだら、開くまで何も分からない
    for must_show in ("インシデントレスポンスとは", "ir-flow", "lede-claim"):
        assert top.index(must_show) < top.index("<details"), (
            f"{must_show} が畳み込みの中に入っている"
        )
    # 手順は畳み込みの中にあること
    fold = top[top.index("<details") : top.index("</details>")]
    assert "play-steps" in fold, "手順が畳まれていない"


def test_the_entrance_keeps_prose_on_the_left_and_the_figure_on_the_right(index_html):
    """**左は文章、右は図。**

    v1.52 で主張を右の柱の頭に置いたが、図の側に段落があると
    読むものと見るものが混ざる（利用者の指摘：
    「文章が右側にも入っているの見栄え的にも気になる」）。
    右に残ってよいのは図と、その図の見出し・注記だけ。
    """
    claim = index_html[index_html.index('<div class="top-claim">') :]
    claim = claim[: claim.index('<section class="top-picks">')]
    claim = re.sub(r"<!--.*?-->", "", claim, flags=re.S)
    right = claim[claim.index("<figure") :]
    # 図の中身は h2（見出し）・表の器・figcaption（注記）だけ
    tags = re.findall(r"<(\w+)[^>]*>", right)
    assert set(tags) <= {"figure", "h2", "div", "figcaption"}, (
        f"図の側に図以外のものがある: {sorted(set(tags))}"
    )
    assert "<p" not in right, "図の側に段落が残っている"


# ─────────── ブリーフィングの畳み込み（SPEC 7.6.23） ───────────


def test_the_briefing_folds_its_manuals(index_html):
    """**道具の説明は畳む。盤面と方針は畳まない。**

    ブリーフィングは開始前に 3,961px あり、そのうち 1,801px が
    「この演習の進み方」と「画面の見方」だった（利用者の指摘：
    「文章が基本多すぎる」「この画面も畳み込み使いたい」）。
    どちらも**道具の説明**であって、事案でも方針でも盤面でもない。

    畳んではいけないものを、名指しで守る。
    """
    brief = index_html[index_html.index('id="screen-briefing"') :]
    brief = brief[: brief.index("</section>")]

    folded = re.findall(r'<details[^>]*id="(brief-[\w-]+)"[^>]*>', brief)
    assert "brief-flow-fold" in folded, "進み方が畳まれていない"
    assert "brief-legend-fold" in folded, "画面の見方が畳まれていない"

    # 畳んだものには必ず見出しが要る。**何が入っているか言わない畳みは、
    # 開かれないまま終わる**
    for block in re.findall(r"<details.*?</details>", brief, flags=re.S):
        assert "<summary>" in block, "見出しの無い畳み込みがある"

    # 事案・方針・構成図は畳まない。これらは読まずに始めてよいものではない
    first_fold = brief.index("<details")
    for must_show in ('id="brief-body"', "policy-box", 'id="brief-topo"'):
        assert brief.index(must_show) < first_fold, f"{must_show} が畳まれている"


def test_the_briefing_sample_is_drawn_after_it_is_opened(app_js):
    """**畳んだ中では描かない**（SPEC 7.6.9）。

    閉じた `<details>` の中は幅が 0 になる。そこで `renderLegend` を呼ぶと、
    グラフも盤も潰れたまま組まれ、開いても潰れたままである
    （dpr 1 だけの検証が Retina の不具合を素通りさせたのと同じ形で、
    「エラーが出ない」ので気づけない）。

    見本を組むのは `toggle` のとき。ブリーフィングを描くところで
    呼んでいないことを、原稿の側で見る。
    """
    body = strip_comments(app_js)
    # ブリーフィングを組む処理の中で、見本を直に描いていないこと
    start = body.index("$('brief-title').textContent")
    block = body[start : body.index("show('screen-briefing')", start)]
    assert "renderLegend" not in block, "畳んだままの見本を描いている"
    # toggle で組んでいること
    assert "brief-legend-fold" in body and "'toggle'" in body, "開いたときに組んでいない"
    toggle = body[body.index("$('brief-legend-fold').addEventListener") :]
    toggle = toggle[: toggle.index("});")]
    assert "renderLegend" in toggle, "開いたときに見本を組んでいない"


def test_the_playtest_opens_the_fold_before_reading_the_sample(playtest_mjs):
    """遊んで確かめる側も、畳みを開いてから読むこと。

    開かずに読むと「凡例から見本が読めない」で落ちる。
    **それは正しい落ち方ではない** — 畳んであることは不具合ではない。
    """
    assert "brief-legend-fold" in playtest_mjs, "畳みを開かずに見本を読んでいる"
    assert playtest_mjs.index("brief-legend-fold") < playtest_mjs.index(
        "legendSample('#brief-mini')"
    ), "見本を読んだ後に畳みを開いている"


def test_the_screen_calls_begin_with_the_session_it_has(app_js):
    """画面が `begin` を**存在する名前**で呼ぶこと（SPEC 7.5.4）。

    実際に踏んだ: `S.sid` と書いた。そんな欄は無いので URL は
    `/api/session/undefined/begin` になり、サーバは 404 を返していた。
    **Python の検査は全部通っていた** — あちらは API を直に叩くので、
    画面の配線を1行も通らない。拾ったのは `playtest.mjs` である。

    ここで見るのは、セッション id を指す名前が画面の中で1つであること。
    """
    body = strip_comments(app_js)
    used = set(re.findall(r"'/api/session/' \+ (S\.\w+)", body))
    assert used, "セッションの呼び出しが見つからない"
    assert len(used) == 1, f"セッション id の呼び名が割れている: {sorted(used)}"
    name = used.pop().split(".", 1)[1]
    assert re.search(rf"S\.{name}\s*=", body), f"S.{name} に代入している場所が無い"
    assert "/begin'" in body, "ブリーフィングを閉じた合図を送っていない"


def test_the_play_screen_fits_without_scrolling_to_the_buttons(app_js):
    """**画面の外に出る寸法を手で書かない**（SPEC 7.6.24）。

    右カラムの天井は `100vh - ヘッダ - 帯 - 130px` と書いてあった。
    この 130px は、その上にあるフェーズ帯・資産盤・事件の時計（実測 196px）を
    **1つも数えていなかった。** 結果、どの画面サイズでもぴったり 186px はみ出し、
    「対応フェーズに移る」と「対応を終了する」は一度も見えなかった。

    テスターに言われるまで気づいていない。**測っていたのは入口だけだった。**
    実際に見えるかどうかは `playtest.mjs` が測る。こちらは
    寸法の出どころが実測であることを原稿の側で見る。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".play-actions {"):]
    block = block[: block.index("}")]
    assert "--cols-top" in block, "右カラムの天井が実測から来ていない"
    assert "--cols-bottom" in block, "下の余白を数えていない"
    body = strip_comments(app_js)
    assert "--cols-top" in body and "getBoundingClientRect" in body, "実測していない"
    # 二重スクロールに戻さない。スクロールする箱は外側の1つだけ
    inner = css[css.index("#actions-list {"):]
    inner = inner[: inner.index("}")]
    assert "max-height" not in inner, "内側にも天井があり、スクロールが二重になる"


def test_the_play_screen_can_be_left_without_recording(index_html, app_js):
    """途中でやめられること（SPEC 7.6.25）。

    テスターの指摘：「演習を途中終了する」みたいなボタンがあって、
    ホームに戻れたりしてもいい（演習のログは残らなくて平気）。

    **「対応を終了する」の隣には置かない。** あちらは採点まで行く手で、
    こちらは何も残さずに帰る手である。取り違えると、やり直しのつもりで
    採点を確定させることになる。
    """
    bar = index_html[index_html.index('<header class="bar">'):]
    bar = bar[: bar.index("</header>")]
    assert 'id="btn-abort"' in bar, "やめる手が上の帯に無い"
    assert 'id="btn-finish"' not in bar, "採点まで行く手が帯に混ざっている"

    body = strip_comments(app_js)
    fn = body[body.index("$('btn-abort-confirm')"):]
    fn = fn[: fn.index("});")]
    assert "'DELETE'" in fn, "サーバ側のセッションを捨てていない"
    assert "screen-top" in fn, "最初の画面に戻っていない"

    modal = index_html[index_html.index('id="modal-abort"'):]
    modal = modal[: modal.index("</div>\n</div>")]
    assert "記録されません" in modal, "何も残らないことを言っていない"


def test_the_select_screen_groups_the_practice_boards(index_html, app_js):
    """**練習は1つの枠にまとめる**（SPEC 7.6.27）。

    利用者の言葉：「チュートリアルで一つの枠にして、その中に遊び方と、
    インシデント対応の基本で2項目」「シナリオ選ぶ時にスクロールして
    選びたくないので一画面に収めたい」。

    実測で、選ぶ画面は縦 1593px あり、**「開始する」がどの画面サイズでも
    一度も見えなかった。** プレイ画面（7.6.24）と同じ穴を2回踏んでいる。
    """
    sel = index_html[index_html.index('id="screen-select"') :]
    sel = sel[: sel.index("</section>")]
    # **枠は2つ、粒度は同じ。** 利用者の言葉：「チュートリアルの枠が
    # あるなら同じ粒度で別の枠を作ってそこにシナリオを入れればいいのでは？」
    # 練習だけを枠に入れて本番を裸で並べると、同じ種類のものが違う見え方をする
    # **枠は3つ、粒度は同じ**（v1.65 / 7.6.31）。
    # 読む → 案内つきで遊ぶ → 自分で遊ぶ、の順に並ぶ
    assert 'id="learn-group"' in sel, "座学の枠が無い"
    assert 'id="tutorial-group"' in sel, "練習をまとめる枠が無い"
    assert 'id="scenario-group"' in sel, "本番をまとめる枠が無い"
    groups = re.findall(r'<details class="([^"]*)" id="(\w+[\w-]*)"', sel)
    assert len(groups) == 3, f"枠が3つでない: {groups}"
    assert [g[1] for g in groups] == [
        "learn-group", "tutorial-group", "scenario-group"
    ], f"並びが「読む → 練習 → 本番」でない: {groups}"
    assert {cls for cls, _ in groups} == {"fold pick-group"}, (
        f"2つの枠が違う作りになっている: {groups}"
    )
    # **どちらも既定で閉じている**（v1.62 / 7.6.28）。
    # 片方だけ開いていると、同じ見た目のものが違うふるまいをする
    assert "open>" not in sel, "既定で開いている枠がある"
    # 枠は札より前にある。後ろに置くと、はじめての人が最後まで見ない
    assert sel.index('id="tutorial-group"') < sel.index('id="scenario-group"')
    # **本数は手で書かない**（7.6.18）。1本足した日から嘘になる
    assert "（2本）" not in sel and "（5本）" not in sel, "本数が手で書かれている"
    assert 'id="tutorial-count"' in sel and 'id="scenario-count"' in sel
    # 方針とアシストは横に並べる（縦に積むと 249px 使う）
    assert "pick-options" in sel, "方針とアシストが縦に積まれている"

    body = strip_comments(app_js)
    fn = body[body.index("function renderScenarioList") :]
    fn = fn[: fn.index("\nfunction ")]
    assert "sc.tutorial" in fn, "練習を振り分けていない"
    # 座学の章もここで並べる（行そのものは `learnChapterRow` が組む）
    assert "learnChapterRow" in fn, "座学の章を並べていない"
    assert "tutorial-list" in fn, "練習を枠の中へ入れていない"
    # 見出しの本数は配列から入れる
    assert "tutorial-count" in fn and "scenario-count" in fn, "本数を入れていない"
    # 既定で選ばれるのは本番の先頭。畳みの中の札が選ばれた状態で
    # 始まると、選択が見えないまま開始ボタンだけが有効になる
    assert "!sc.tutorial" in fn, "既定の選択が本番になっていない"


def test_the_entrance_shows_the_practice_boards_as_one_card(app_js):
    """入口でも練習は1枚にまとめる（SPEC 7.6.27）。

    7本を札のまま並べると 1280×720 で2行になり、広い画面では
    1行7列に割れて題が切れる。**「入口の札は題だけ」（7.6.19）という
    決め方は、枚数が増えると横で同じ問題に当たる。**
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderTopScenarios") :]
    fn = fn[: fn.index("\nfunction ")]
    assert "tutorialGroupCard" in fn, "練習をまとめた札が無い"
    assert "!sc.tutorial" in fn, "本番の札に練習が混ざっている"
    # **これは演習の札ではない**ので、`fillScenarioCard` は通さない。
    # 通すと、シナリオから来ない中身を札の組み立てに混ぜることになる
    maker = body[body.index("function tutorialGroupCard") :]
    maker = maker[: maker.index("\nfunction ")]
    assert "data-tutorial-group" in maker
    # 押したら、選ぶ画面の枠が開いた状態で開く。
    # **開く処理は1か所にまとめてある**（座学の「練習へ」も同じ道を通る）
    assert "toTutorials" in maker, "押しても枠が開かない"
    opener = body[body.index("function toTutorials") :]
    opener = opener[: opener.index("\n}") + 2]
    assert "open = true" in opener


def test_the_strip_counts_practice_separately(app_js):
    """隅の帯は練習と本番を別に数える（SPEC 7.6.27 / 7.6.18）。

    入口の札は練習を1枚にまとめているので、合計だけを出すと
    「7 と言っているのに札が6枚」になる。**数えている配列は1つのまま、
    出す数を分ける** — 別経路で数え直さない。
    """
    body = strip_comments(app_js)
    fn = body[body.index("function renderTopMeta") :]
    fn = fn[: fn.index("\nfunction ")]
    assert "TUTORIALS" in fn, "帯が練習の数を出していない"
    assert "!sc.tutorial" in fn, "本番の数を練習と分けていない"
    assert "S.scenarios.filter" in fn, "同じ配列から数えていない"


def test_the_playtest_watches_the_select_screen(playtest_mjs):
    """遊んで確かめる側も、選ぶ画面の収まりを見張ること。

    **同じ穴を2回踏んだ。** 入口には検査があり、プレイ画面にも足したが、
    選ぶ画面には無かった。
    """
    assert "選ぶ画面が" in playtest_mjs, "はみ出しを見ていない"
    assert "btn-start" in playtest_mjs, "開始ボタンが見えるかを見ていない"
    # 練習は畳みの中にあるので、開いてから押すこと
    assert "tutorial-group" in playtest_mjs, "畳みを開かずに練習を選ぼうとしている"


def test_a_closed_frame_does_not_lay_out_its_contents(index_html):
    """**閉じた `<details>` の中に `display` を書かない**（SPEC 7.6.27）。

    実際に踏んだ: `#tutorial-list { display: grid }` と書いたので、
    閉じているのに中身が 71px ぶん場所を取っていた。
    `<details>` が閉じているときの非表示は作者の `display` に負ける。

    枠が閉じているあいだ、中の札は**見えないまま生きている**ことになる。
    `[open]` を付けて、開いているときだけ並べる。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    for host in ("#scenario-list", "#tutorial-list"):
        for sel, decls in rules_with(css, host):
            # 見るのは**その箱そのもの**を指す規則だけ。
            # 中の子孫に display を与えるのは、閉じ方とは関係がない
            targets = [s.strip() for s in sel.split(",")]
            if not any(s.endswith(host) for s in targets):
                continue
            if re.search(r"(^|[;{\s])display\s*:", decls):
                assert "[open]" in sel, (
                    f"{host} に display を与える規則が [open] の外にある: {sel}"
                )


def rules_with(css: str, needle: str):
    """`needle` を含むセレクタの規則を (セレクタ, 宣言) で返す。"""
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, decls = block.group(1).strip(), block.group(2)
        if needle in sel:
            yield sel, decls


def test_every_fold_starts_closed(index_html, app_js):
    """**畳みは全部、既定で閉じている**（SPEC 7.6.28）。

    利用者の指摘：「畳み込みできるところに置いて畳み込みしてる
    ところとしてないところが統一されてない。基本畳み込みされた状態で
    表示して、押されたら開くようにして」。

    実際そうなっていた — 選ぶ画面の「演習」と、ブリーフィングの
    「はじめての人へ」だけが開いた状態で出ていた。**同じ見た目のものが
    違うふるまいをすると、畳みそのものが読めなくなる。**
    """
    text = re.sub(r"<!--.*?-->", "", index_html, flags=re.S)
    opened = re.findall(r"<details[^>]*\bopen\b[^>]*>", text)
    assert not opened, f"既定で開いている畳みがある: {opened}"

    # JS で開いてよい場所は4つだけ。**名前で列挙する** — どれも理由が違う
    allowed = {
        # 入口の札、または座学の「読み終えた — 練習へ」を押した結果。
        # **押された結果としてしか呼ばれない**ので、1か所にまとめてある
        "toTutorials",
        # 選ぶ画面の章の行を押した結果。その章を開いた状態で座学を出す
        "toLearn",
        # 座学の「読んだら試す」を押した結果。その演習を選んだ状態で開く
        "toPractice",
        # 証拠の参照を押した結果。畳んだ先にある証拠へ連れて行く
        "focusEvidence",
        # **見本の中**（画面の見方）。見本は中身を見せるためのもので、
        # 畳んだ見本は用をなさない。ここだけは実画面の既定と違ってよい
        "renderLegend",
        # 前に開いていたかを覚えているだけ。既定は閉じている
        "renderBoard",
    }
    body = strip_comments(app_js)
    lines = body.split("\n")
    owners = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        for m in [re.match(r"function (\w+)", line)]
        if m
    ]
    for i, line in enumerate(lines):
        if not re.search(r"\.open = (true|!!)", line):
            continue
        before = [name for j, name in owners if j < i]
        owner = before[-1] if before else "(不明)"
        assert owner in allowed, (
            f"{owner} が押されていないのに畳みを開いている: {line.strip()}"
        )


def test_the_frame_says_what_is_selected_even_when_closed(index_html, app_js):
    """畳んだままでも、何が選ばれているかは見えること（SPEC 7.6.28）。

    枠を閉じたまま始められるので、選択が見出しに出ていないと
    **「何が始まるのか分からないまま開始ボタンだけが有効」**になる。
    """
    sel = index_html[index_html.index('id="screen-select"') :]
    sel = sel[: sel.index("</section>")]
    assert 'id="scenario-picked"' in sel, "選択中の演習を見出しに出していない"
    body = strip_comments(app_js)
    fn = body[body.index("function selectScenario") :]
    fn = fn[: fn.index("\nfunction ")]
    assert "scenario-picked" in fn, "選んだときに見出しを書き替えていない"


def test_the_browser_scripts_parse():
    """**画面の JavaScript が構文として通ること。**

    テストは 560本あって、そのうち1本も `web/*.js` を読んでいなかった。
    Python 側の検査は文字列として grep するだけなので、
    **コメントの閉じ方を1つ間違えただけの版が全部通る。**
    実際 v1.72 で壊れた版が 562本すべてを通り抜け、
    `playtest.mjs` が画面を開いて初めて落ちた。

    これは「判定が甘かった」のではなく、**その言語を誰も読んでいなかった**。
    `node --check` は 0.1秒で答えるので、ブラウザを立てる前にここで落とす。
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い（playtest.mjs も動かない環境）")

    for path in sorted(WEB.glob("*.js")):
        done = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True
        )
        assert done.returncode == 0, f"{path.name} が構文として通らない:\n{done.stderr}"


def test_the_guidance_points_at_a_button_that_exists(any_scenario, index_html):
    """**案内が名指しするボタンが、画面に実在すること**（SPEC 7.6.7）。

    練習「遊び方を覚える」の名指しの段は、こう言っていた。

        下の **被疑判定を宣言する** を押してください。

    **そんなボタンは画面のどこにも無い。** 押すのは盤面の `advance_label`
    （この盤面では「対応フェーズに移る」）で、`applyTutorialLock()` は
    最初からそれを通していた。**通す先と、案内が指す先が、別々に
    書かれていた**ので、片方だけが現実と切れても誰も気づかなかった。

    利用者に「押せと言われたボタンが無い」と報告されて初めて出た
    （GitHub issue #2）。押す物の名前は、押す物から引くしかない。
    """
    steps = getattr(any_scenario, "tutorial", None) or []
    if not steps:
        pytest.skip("案内を持たない盤面")

    # 画面に実在するボタンの文言（静的なもの＋盤面が与えるもの）
    real = {m.strip() for m in re.findall(r"<button[^>]*>([^<]+)</button>", index_html)}
    real |= {p.advance_label for p in any_scenario.phases if p.advance_label}
    real |= {a.label for a in any_scenario.actions}

    named = re.compile(r"\*\*(.+?)\*\*\s*を押して")
    for step in steps:
        for hit in named.findall(step.body or ""):
            assert hit in real, (
                f"{any_scenario.id} の案内 `{step.id}` が "
                f"「{hit}」を押せと言うが、そのボタンは画面に無い。"
                f"\n実在するのは: {sorted(real)}"
            )


def test_the_guidance_does_not_copy_a_button_name(app_js):
    """**画面が、ボタンの名前を案内の側に書き写していないこと。**

    `expect_action` の段は「手の名前は盤面から引く」と自分で書いて
    そうしているのに、`expect_kind` の段だけが文字列のべた書きだった。
    **同じファイルの70行しか離れていない場所で、規律が片側にしか
    かかっていなかった。**

    盤面が `advance_label` を変えた日に案内だけが古い名前を指す。
    ここで落とす。
    """
    src = strip_comments(app_js)

    # 画面に無いボタン名がコードに残っていないこと
    assert "被疑判定を宣言する" not in src, (
        "`被疑判定を宣言する` というボタンは画面に無い。"
        "案内がそう言っていた版が issue #2 になった"
    )

    # 待ち文言は、ボタンそのものから引いていること
    assert "btn-advance" in src and "btn-finish" in src, (
        "案内の待ち文言が、押すボタンを参照していない"
    )


def test_the_guidance_does_not_block_what_it_invites(app_js):
    """**案内が「押してみてください」と言う段で、手が押せること。**

    `advanceTutorial()` は自分でこう書いている —

        **待っていないものを押しても止めない。**
        案内は道しるべであって通せんぼではない

    ところが `applyTutorialLock()` は、手を名指ししない段で
    アクションを**全部** `inert` にしていた。おかげで

    - 「余裕があれば、右の2つを両方押してみてください」（`t06_noise`）→ 押せない
    - 「止める手を打ってもいいですし、何も止めずに終えることもできます」
      （`t08_finish`）→ 打てない

    後者は**この製品の主張そのもの**（どちらが正しいかは方針が決める）で、
    練習がそれを試させないのは主張を自分で取り下げているのと同じだった
    （GitHub issue #3）。

    塞いでよいのは**後の段が名指ししている手だけ**である
    （先に押されるとその段で押す物が無くなり、案内が進まなくなる）。
    """
    src = strip_comments(app_js)
    lock = src[src.index("function applyTutorialLock"):]
    lock = lock[: lock.index("\n}")]

    assert "freeActions" in lock, (
        "手を名指ししない段で、アクションを通す道が無い"
    )
    # 「終わらせるだけ」の段と「読むだけ」の段の両方で通っていること
    assert lock.count("allow.concat(freeActions())") >= 2, (
        "通しているのが片方の段だけになっている"
    )
    # 後の段が名指しする手は、先に押させない（詰み防止）
    assert "expect_action" in lock and "later[" in lock, (
        "後の段が待っている手まで通すと、その段で押す物が無くなる"
    )


def test_the_guidance_can_be_read_again(app_js):
    """**読み終えた案内に、戻る手段があること**（SPEC 9.4 #13）。

    案内は指した手を押すと自動で次の段へ進むので、読み途中の文が消えていた。
    `S.tutorAt` を動かすのは `+= 1` の2箇所だけで、**減らす場所が無かった**
    （GitHub issue #4）。

    `applyTutorialLock()` は自分でこう書いている —

        制限するのは「ルート」であって、**読み返す手段ではない。**
        ここを塞ぐと、迷った人に逃げ場が無くなる — それは制限ではなく監禁である。

    **思想として守ると言っているものが、実装として無かった。**

    直し方は「段を戻す」ではなく「読み終えた段を畳んで残す」。戻すと、押した手の
    結果が既に画面を書き換えているので、本文（「左に結果が出ました」）と画面が
    食い違う段が生まれる。残すだけなら盤面に触らない。
    """
    src = strip_comments(app_js)
    fn = src[src.index("function renderTutorial"):]
    fn = fn[: fn.index("\n}")]

    assert "tutor-past" in fn, "読み終えた案内を残す場所が無い"
    assert "S.tutorAt" in fn and "tutorial[" in fn, (
        "過去の段を、案内そのもの（S.tutorial）から引いていない"
    )
    # 畳みの記号は共通のものを通す（SPEC 7.6.15）
    assert "caret(" in fn, "読み返しの畳みが共通の記号を使っていない"
    # **現在の段より後ろに置く。** 頭に置くと、開いた瞬間に現在の段が
    # 画面の外へ押し出される（実測で確かめた）
    assert fn.index("tutor-past") > fn.index("tutor-wait"), (
        "読み返しが現在の段より前にある。開くと、いま読むべき文が画面の外へ出る"
    )
