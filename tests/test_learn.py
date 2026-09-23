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
    need = ["what", "kinds", "damage", "flow", "investigate", "contain", "policy", "words"]
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
