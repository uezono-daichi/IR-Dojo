"""入口の装飾が、**入口の外と、数字の外に出ていない**ことを見張る（SPEC 7.6.18）。

入口に飾りを入れると、腐り方が3つ増える。

  ① 飾りが入口の外へ漏れる。CSS は場所を選ばないので、`.wrap-top` に
     書いたつもりの規則が講評の表に効く、ということが普通に起きる
  ② 動きが主張とずれる。対角を歩く光は「各列の最高点が A → B → C と
     移る」という主張そのものなので、**光る場所を CSS に焼き付けると、
     盤面を触って最高点が対角から外れた日に、光だけが古い対角を歩き続ける**
  ③ 帯の数字が腐る。版も演習の数も、手で書いた瞬間から次の変更を待つだけの嘘になる

見ているのは見た目ではなく、この3つが**構造として起こりえない形**になっているか。
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from irdojo.api import app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


@pytest.fixture(scope="module")
def style_css() -> str:
    return (WEB / "style.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def decor_css(style_css: str) -> str:
    """装飾ブロックだけを切り出す。見出しコメントの `/*` から次の節までが範囲。"""
    start = style_css.index("/* ── 入口の装飾")
    rest = style_css[start:]
    return rest[: rest.index("button.big {")]


def uncommented(css: str) -> str:
    """理由はコメントに書く。**コメントは検査の対象から外す** —
    「nth-child は使わない」と書いたコメント自体が引っかかる。"""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def selectors(css: str) -> list[str]:
    """規則の頭（セレクタ）を並べる。

    `@media` は囲いなので外し、中の規則だけを見る（囲いの中に置いた
    規則も「入口の外に出ていないか」を問われる側である）。
    `@keyframes` の中は宣言であってセレクタではないので落とす。
    """
    css = uncommented(css)
    css = re.sub(r"@keyframes[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{", "", css)
    out: list[str] = []
    for chunk in re.findall(r"([^{}]+)\{", css):
        sel = chunk.strip()
        if sel:
            out += [s.strip() for s in sel.split(",") if s.strip()]
    return out


def rules(css: str) -> list[tuple[str, str]]:
    """規則を (セレクタ, 宣言) で並べる。@media の囲いは外す。"""
    css = uncommented(css)
    css = re.sub(r"@keyframes[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{", "", css)
    return [(m[0].strip(), m[1]) for m in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def literals(js: str) -> list[str]:
    """JS の文字列リテラルを、行をまたがずに拾う（test_ui_rules と同じ読み方）。"""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = "\n".join(line.split("//")[0] for line in js.split("\n"))
    out: list[str] = []
    for line in js.split("\n"):
        out += re.findall(r"'((?:[^'\\\n]|\\.)*)'", line)
        out += re.findall(r'"((?:[^"\\\n]|\\.)*)"', line)
    return out


def meta_body(app_js: str) -> str:
    return app_js[app_js.index("function renderTopMeta"):
                  app_js.index("\n/* 入っている演習")]


def swap_body(app_js: str) -> str:
    return app_js[app_js.index("function markSwapColumn"):
                  app_js.index("/* 隅の帯（SPEC 7.6.18）")]


# ── ① 入口の外へ出ない ──────────────────────────────


def test_the_decoration_is_closed_inside_the_entry_screen(decor_css):
    """装飾の規則はすべて `#screen-top` の中で閉じる。

    演習中と講評は、盤面が変わったことだけを伝える画面である。
    そこに入口の飾りが1つでも効くと、**意味の無い緊迫**（原則3）を
    作った側が自分で持ち込むことになる。

    「いま `.top-meta` は入口にしか無いから大丈夫」では閉じない。
    同じ名前の札を別の画面で使った日に、黙って効き始める。
    """
    leaked = [s for s in selectors(decor_css) if not s.startswith("#screen-top")]
    assert leaked == [], f"入口の外に出ている規則がある: {leaked}"


def test_the_entry_screen_does_not_clip_its_own_content(style_css):
    """入口は自分の中身を切り抜かない。

    背後に格子を敷くとき `overflow: hidden` を付けたくなるが、入口は
    画面の高さいっぱいを使う作りで、**中身が画面より高くなることがある**
    （1280×720 では実測で 11px 溢れた）。切り抜くと、溢れた側は
    スクロールでも届かなくなる — 押すべきボタンが二度と出てこない。
    格子は `inset: 0` の中に収まるので、そもそも切る必要が無い。
    """
    seen = [(sel, decls) for sel, decls in rules(style_css) if "#screen-top" in sel]
    assert seen, "入口の規則が1つも見つからない"
    for sel, decls in seen:
        assert not re.search(r"overflow[^:]*:\s*(hidden|clip)", decls), \
            f"入口が中身を切り抜いている: {sel}"


def test_the_decoration_cannot_be_pressed(decor_css):
    """背後の格子は押せない。

    `inset: 0` で全面を覆う要素なので、`pointer-events` を落とし忘れると
    **入口のボタンが1つも押せなくなる**。見た目には何も起きないので、
    画面を見ただけでは気付けない壊れ方をする。
    """
    field = re.search(r"#screen-top \.top-field\s*\{([^}]*)\}", decor_css)
    assert field, "#screen-top .top-field の規則が見当たらない"
    assert "pointer-events: none" in field.group(1)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'class="top-field" aria-hidden="true"' in html


# ── ② 光る場所は数字が決める ────────────────────────


def test_the_light_does_not_know_where_the_diagonal_is(decor_css):
    """歩く光の位置を CSS に焼き付けない。

    いまの表はたまたま対角が最高点だが、それは盤面と採点の帰結であって
    表の性質ではない。`nth-child(2)` で2列目を光らせた瞬間、
    最高点が動いた日に**表は正しく、光だけが嘘をつく**状態ができる。
    位置は `--swap-col`（app.js が数字から決める）だけから来ること。
    """
    css = uncommented(decor_css)
    assert "nth-child" not in css
    assert "nth-of-type" not in css
    assert "var(--swap-col)" in css


def test_the_light_is_placed_by_the_same_code_that_found_the_best(app_js):
    """印を置くのは、最高点を見つけたのと同じ場所。

    `markSwapColumn` は1つしかなく、升目・行の見出し・列の見出しの
    3つともそこを通る。別々に書けるようにしておくと、片方だけが
    古い列番号を持ったまま残る。
    """
    body = swap_body(app_js)
    # 定義1つ + 升目・行の見出し・列の見出しの3回
    assert body.count("markSwapColumn(") == 4
    # 属性名を直に書くのは定義の中だけ。呼ぶ側は列番号しか渡さない
    assert body.count("data-swap") == 1


def test_stopping_the_light_loses_nothing(decor_css):
    """光を止めても、各列の最高点は分かったままであること。

    `prefers-reduced-motion: reduce` は「動きを減らす」であって
    「情報を減らす」ではない。**最高点が青いこと自体を動きの中に置くと、
    止めた人からは表の主張が丸ごと消える。**

    形で担保する: `animation` の宣言は `no-preference` の中だけにあり、
    `.best` の色と地は外にある。
    """
    guarded = re.search(
        r"@media \(prefers-reduced-motion: no-preference\) \{(?:[^{}]|\{[^{}]*\})*\}",
        decor_css, flags=re.S)
    assert guarded, "prefers-reduced-motion の囲いが無い"
    outside = decor_css.replace(guarded.group(0), "")
    assert "animation" not in outside, "動きの指定が囲いの外に出ている"

    best = re.search(r"#screen-top table\.swap-grid td\.best\s*\{([^}]*)\}", outside)
    assert best, "止まった状態の .best が囲いの外に無い"
    assert "var(--accent)" in best.group(1)


def test_the_light_turns_itself_off_when_the_table_is_not_three_wide(app_js):
    """列が3でない表では、歩く光を付けないこと。

    光っている時間の長さは keyframes に 1/3 と書いてある。CSS の % に
    変数は書けないので、4列の表に同じものを掛けると2つ同時に光る。
    **「最善が1つずつ移る」と食い違うくらいなら止める** —
    止まっても各列の最高点は青いままで、表は何も失わない。
    """
    body = swap_body(app_js)
    assert "if (cols.length === 3) { table.classList.add('walk'); }" in body
    # 動きの宣言は、.walk が付いた表にしか効かないこと
    css = (WEB / "style.css").read_text(encoding="utf-8")
    moving = [sel for sel, decls in rules(css) if "animation" in decls]
    assert moving, "動きの規則が1つも見つからない"
    for sel in moving:
        assert ".walk" in sel, f"walk 無しで動く規則がある: {sel}"


# ── ③ 帯の中身は実データ ────────────────────────────


def test_the_strip_writes_no_version_by_hand(app_js):
    """版を画面のコードに書かない。

    書いた日は正しい。**次に SPEC を上げた日から、入口が古い版を名乗る。**
    凡例のモックが3フェーズのまま腐ったのと同じ壊れ方で、しかも
    入口なので一番よく見られる場所に出る。
    """
    for text in literals(app_js):
        assert not re.search(r"\bv\d+\.\d+", text), f"版が手で書かれている: {text!r}"


def test_the_strip_counts_the_very_list_it_shows(app_js):
    """帯の「N SCENARIOS」は、下に並べた札と同じ配列から数えること。

    サーバに件数を数えさせると、札と帯が別々の経路で来る。片方が
    失敗したときに「2 SCENARIOS」と言いながら札が0枚、が成立する。
    """
    body = meta_body(app_js)
    # **練習と本番は別に数える**（SPEC 7.6.27）。入口の札は練習を1枚に
    # まとめているので、合計だけを出すと「7 と言っているのに札が6枚」になる。
    # 見ているのは**同じ配列から数えていること**で、合計であることではない。
    assert "S.scenarios.filter" in body, "帯が別の経路で数えている"
    assert "real + ' SCENARIOS'" in body
    assert "practice + ' TUTORIALS'" in body
    # 一覧を入れ替えたら帯も数え直す
    loader = app_js[app_js.index("function loadScenarios"):]
    loader = loader[: loader.index("function showListError")]
    assert "renderTopScenarios();" in loader and "renderTopMeta();" in loader


def test_the_strip_drops_what_it_could_not_get(app_js):
    """取れなかったものは黙って落ちる。事実が1つも無ければ帯ごと出ない。

    「BUILD ―」や「SPEC 不明」を出すのは、**分からないことを
    分かった顔で書く**のと同じである。`policy_swap.json` の指紋が
    合わないとき表ごと消すのと同じ閉じ方にする。
    """
    assert "if (!facts.length) { host.hidden = true; return; }" in meta_body(app_js)


def test_the_build_mark_is_tied_to_the_table_on_the_screen(app_js):
    """指紋を名乗るのは、その表が**画面に出ているとき**だけ。

    サーバ側は生成物が古ければ `null` を返すので、そこは閉じている。
    閉じていないのは**取りに行けなかったとき**で、`/api/policy-swap` が
    落ちると表は消えるが `/api/meta` は指紋を返し続ける。
    そのまま出すと、帯が**画面に無い表の素性**を名乗る。
    """
    body = meta_body(app_js)
    assert "!$('top-swap').hidden" in body
    # 表の出し入れがあったら帯を組み直す。どちらが先に返るかに依存させない
    swap = app_js[app_js.index("function renderPolicySwap"):
                  app_js.index("/* 隅の帯（SPEC 7.6.18）")]
    assert swap.count("renderTopMeta();") == 2  # 出したときと、消したとき


def test_the_endpoint_reports_the_version_the_spec_actually_says():
    """`/api/meta` が返す版は、SPEC.md に書いてある版そのものであること。"""
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    written = re.search(r"^\*\*仕様書 (v\d+\.\d+)\*\*$", spec, flags=re.M)
    assert written, "SPEC.md の冒頭に版が無い"
    assert TestClient(app).get("/api/meta").json()["spec_version"] == written.group(1)


def test_the_endpoint_says_nothing_when_it_cannot_read_the_version(monkeypatch):
    """SPEC.md が読めないときは版を返さない。**それらしい既定値を作らない。**

    パッケージだけ取り出して動かす形はありうる。そのとき
    「v0.0」や `__version__` で埋めると、入口が本当ではない版を名乗る。
    """
    from irdojo import api  # noqa: PLC0415

    monkeypatch.setattr(api, "SPEC_FILE", ROOT / "SPEC-does-not-exist.md")
    assert TestClient(app).get("/api/meta").json()["spec_version"] is None


def test_the_build_mark_disappears_with_the_table_it_describes(monkeypatch):
    """指紋は、その指紋が作った表が出ているときだけ出す。

    生成物が古ければ入口の表は消える（`swap.load()` が None）。
    そのとき帯だけが `BUILD ...` と名乗ると、**画面に無い表の素性**を
    言っていることになる。消えるときは一緒に消える。
    """
    from irdojo import api  # noqa: PLC0415

    monkeypatch.setattr(api.swap_data, "load", lambda: None)
    body = TestClient(app).get("/api/meta").json()
    assert body["build"] is None
    assert TestClient(app).get("/api/policy-swap").json()["available"] is False


def test_the_strip_is_not_a_place_for_numbers_nobody_can_check(app_js):
    """帯に載せてよいのは、その場で取れるものだけ。

    「338 CHECKS」のようなテストの本数は、動いているサーバからは
    取れない。取れないものを書くと、増やすたびに手で直す約束が1つ増え、
    その約束は必ず破られる。
    """
    for text in re.findall(r"'((?:[^'\\\n]|\\.)*)'", uncommented(meta_body(app_js))):
        assert not re.search(r"\d", text), f"帯に手書きの数字がある: {text!r}"


def test_the_strip_never_names_a_board():
    """帯は盤面の名前を1つも出さない（原則1 / SPEC 7.7.3）。

    入口はシナリオを選ぶ前の画面である。`/api/policy-swap` が
    `scenario_id` を載せないのと同じ理由で、帯にも載らないこと。
    「いま入っているのは ○○ です」を足したくなる場所なので、先に閉じる。
    """
    from irdojo.loader import list_scenarios  # noqa: PLC0415

    text = json.dumps(TestClient(app).get("/api/meta").json(), ensure_ascii=False)
    for sc in list_scenarios():
        assert sc.meta.id not in text
        assert sc.meta.title not in text
        for a in sc.world.assets:
            assert a.id not in text
