"""座学の教材（SPEC 3.14）。

**演習は「判断」だけを鍛える道具で、判断の前提になる知識はどこにも無かった。**
最初のテスターの指摘：「全くの初心者（インシデントレスポンス初めて、
そもそもセキュリティ初めて）の人にはまだわかりにくい」。

ここは盤面ではない。**同梱シナリオの中身は1文字も書かない** —
書けば、読んだ人だけが答えを知っている状態になる。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from irdojo.loader import list_scenarios

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


@pytest.fixture(scope="module")
def learn_js() -> str:
    return (WEB / "learn.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


def chapters(learn_js: str) -> list[str]:
    return re.findall(r"title: '([^']+)'", learn_js)


def test_the_curriculum_covers_the_ground_a_beginner_needs(learn_js):
    """**この道具だけで、はじめの一歩が踏めること。**

    並びは「何が起きるのか」から「どう判断するのか」へ。
    章が1つ欠けると、その先が読めなくなる順に並べてある。
    """
    ids = re.findall(r"id: '([\w-]+)'", learn_js)
    # **「1〜100まで」の中身は SPEC 3.14 の表で定義してある。**
    # 終わりの無い目標は、終わったかどうかを誰も言えない。
    # ここで言う 1〜100 は「何も知らない人が、一人で現場の初動に立てるまで」。
    need = [
        "what",        # インシデントとは何か
        "prepare",     # 備える — 記録が無ければ何も分からない
        "kinds",       # どんな形で起きるか
        "damage",      # 起きると、何が失われるか
        "flow",        # 対応の流れ
        "reading",     # 記録を読む — 実例
        "investigate", # 調べるときの原則
        "contain",     # 止めるときの判断
        "recover",     # 取り除く・戻す
        "report",      # 伝える
        "policy",      # 方針で、最善が入れ替わる
        "words",       # 言葉
    ]
    assert ids == need, f"章の並びが変わっている: {ids}"


def test_every_chapter_says_what_it_is_before_it_is_opened(learn_js):
    """章は畳んで出す（7.6.28）ので、**開かなくても何の章かが分かること。**

    題だけでは「起きると、何が失われるか」が何の話か分からない。
    一行の要約を見出しに並べる。
    """
    blocks = re.split(r"\n  \{", learn_js)[1:]
    for block in blocks:
        title = re.search(r"title: '([^']+)'", block)
        assert title, "題の無い章がある"
        # 用語集だけは要約が要らない（題がそのまま中身）
        if title.group(1) == "言葉":
            continue
        assert "lead:" in block, f"{title.group(1)}: 一行の要約が無い"


def test_the_curriculum_does_not_give_away_the_bundled_boards(learn_js):
    """**座学に盤面の中身を書かない。**

    書けば、読んだ人だけが答えを知っている状態になる。
    例えるときは架空の一般例を使う。
    """
    forbidden: set[str] = set()
    for sc in list_scenarios():
        gt = sc.world.ground_truth
        forbidden |= set(gt.compromised) | set(gt.persistence) | {gt.patient_zero}
        forbidden |= {a.id for a in sc.world.assets}
        forbidden |= {a.label for a in sc.world.assets}
        forbidden |= {e.id for e in sc.evidence}
        forbidden |= {a.label for a in sc.actions}
        forbidden.add(sc.meta.title)
    hits = sorted(w for w in forbidden if w and len(w) >= 4 and w in learn_js)
    assert not hits, f"座学に盤面の中身が書かれている: {hits}"


def test_the_screen_holds_no_teaching_text(index_html):
    """教材の文は `learn.js` にあり、画面には無いこと。

    画面に書くと、教材を直すのに画面を触ることになる。
    **中身と入れ物を分けておく。**
    """
    screen = index_html[index_html.index('id="screen-learn"') :]
    screen = screen[: screen.index("</section>")]
    assert 'id="learn-body"' in screen, "教材の入れ物が無い"
    for ch in ("インシデントとは何か", "対応の流れ", "方針で、最善が入れ替わる"):
        assert ch not in screen, f"画面に教材の文が書かれている: {ch}"


def test_the_curriculum_is_reachable_where_you_choose_what_to_do(index_html):
    """**「何から始めますか」の1つ目として置く**（SPEC 7.6.31）。

    利用者の指摘：「演習を選ぶ／はじめて学ぶ／何を測るのか、この配置が難しい。
    そもそも同列？ **演習を選んだ先で初めて学ぶ人のコースあったらいいのでは？**」

    そのとおりで、3つは同じ高さのものではなかった — 1つ目は次の画面へ行く操作、
    2つ目は**コース**、3つ目はこの道具の説明である。
    コースは次の画面の中へ移し、読む → 練習 → 本番の順に並べた。
    """
    sel = index_html[index_html.index('id="screen-select"') :]
    sel = sel[: sel.index("</section>")]
    assert 'id="learn-group"' in sel, "座学のコースが選ぶ画面に無い"
    assert sel.index('id="learn-group"') < sel.index('id="tutorial-group"')

    top = index_html[index_html.index('id="screen-top"') : index_html.index('id="screen-select"')]
    assert 'id="btn-to-learn"' not in top, "入口に粒度の違うボタンが残っている"


def test_reading_leads_to_the_practice_boards(index_html):
    """読み終えた人が次に行くのは**練習**であって、いきなり本番ではない。"""
    screen = index_html[index_html.index('id="screen-learn"') :]
    screen = screen[: screen.index("</section>")]
    assert "練習" in screen, "読み終えた先が練習になっていない"

    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "$('btn-learn-play').addEventListener('click', toTutorials);" in app


def test_the_words_chapter_covers_what_the_screens_say(learn_js):
    """用語集が、画面に出る言葉を拾っていること。

    **画面で初めて見る言葉が、どこにも説明されていない**のが
    いちばん困る。演習の画面に出る語を名指しで確かめる。
    """
    for word in ("侵害されている", "被疑判定", "封じ込め", "根絶", "誤導",
                 "未解消の論点", "方針", "資産", "依存"):
        assert word in learn_js, f"用語集に「{word}」が無い"


def test_the_reading_chapter_shows_a_real_looking_log(learn_js):
    """**概念だけでは手が止まる**（SPEC 3.14）。

    「平常時と比べる」と言われても、記録を前にすると動けません。
    実際の形をした断片を1つ読ませて、読み方の型を4つに割る。
    """
    block = learn_js[learn_js.index("id: 'reading'") :]
    block = block[: block.index("\n  },")]
    assert "log:" in block, "記録の断片が無い"
    assert "steps:" in block, "読み方の手順が無い"
    assert block.count("['") >= 4, "手順が4つに満たない"
    # 架空の宛先だけを使う（RFC 5737 / RFC 2606）
    assert "example.test" in block or "example.com" in block
    assert "203.0.113." in block or "198.51.100." in block


def test_chapters_point_at_something_to_try(learn_js):
    """**読むだけで終わらせない。**

    章の終わりに「読んだら試す」を置く。演習と結びつかない座学は、
    読んだ気になって終わる。
    """
    ids = re.findall(r"id: '([\w-]+)'", learn_js)
    linked = re.findall(r"practice: '([\w-]+)'", learn_js)
    assert len(linked) >= 5, f"試す先を持つ章が少なすぎる: {len(linked)} / {len(ids)}"
    known = {sc.meta.id for sc in list_scenarios()}
    unknown = [p for p in linked if p not in known]
    assert not unknown, f"存在しない演習を指している: {unknown}"


def test_the_curriculum_matches_the_spec_table():
    """**表に無いものを足さない**（SPEC 3.14）。

    「1〜100」は終わりの無い目標なので、中身を表に書き出してある。
    章を足したら表も直す — 直さないと「まだ終わっていない」が永久に続く。
    """
    spec = (ROOT / "SPEC.md").read_text("utf-8")
    table = spec[spec.index("「1〜100まで」の中身") :]
    table = table[: table.index("**この12で「1〜100」とする。**")]
    learn = (WEB / "learn.js").read_text("utf-8")
    for title in re.findall(r"title: '([^']+)'", learn):
        assert title in table, f"SPEC の表に「{title}」が無い"
