/* IR Dojo フロントエンド。素の JS、ビルド工程なし（SPEC 7.6.2）。

   シナリオ由来の文字列は必ず textContent で入れる。innerHTML は使わない
   （SPEC 7.7.2）。ログ断片の改行は CSS の white-space: pre-wrap で保つ。

   PlayerView のフィールドが null なら描かない。フロント側に
   アシストレベルの判定ロジックは置かない（SPEC 7.6.8）。 */

'use strict';

var S = {
  scenarios: [],
  scenario: null,
  policy: null,
  assist: null,
  sessionId: null,
  pendingFinish: false,
  view: null,
  report: null,
  newIds: {},
  openIds: {},
  closedPhases: {},
  lastOutcome: null,
  meta: null,         // /api/meta。入口の隅の帯に出す、この配布物の素性
  cards: [],          // シナリオ選択のカード。押して選べるようにするために持つ
  incoming: []        // 向こうから入ってきたこと。押した結果とは別に積む
};

// 段取りの説明。シナリオ固有の中身ではなく、フェーズの位置づけを述べる
var PHASE_NOTES = [
  'ブリーフィングに出てきたものから調べ始めます。' +
  '調べると次に調べられることが増えるので、どこを追ってどこを切り捨てるかが問われます。',
  '調査を打ち切って動きます。何をどこまで止めるかは、与えられた方針で決まります。'
];

var ASSIST_NOTES = [
  ['assisted', 'Assisted', '未解消の論点を常時表示'],
  ['standard', 'Standard', '論点は示すが解消状態は出さない'],
  ['hard', 'Hard', 'アシストなし。証拠の要約も出ない']
];

/* ── DOM ヘルパ ── */

function $(id) { return document.getElementById(id); }

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = String(text); }
  return n;
}

function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

/* シナリオの散文を、列幅に合わせて流す。

   YAML に書かれた改行をそのまま出すと、幅があるのに 25 文字で折り返る。
   空行は段落の区切りとして残し、段落の中の改行は畳む。
   ただし字下げのある行（ログや箇条書き）はそのまま保つ。 */

var CJK = /[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]/;

function foldParagraph(block) {
  var lines = block.split('\n');
  // 字下げがある＝整形済み。触らない
  for (var i = 0; i < lines.length; i++) {
    if (/^[ \t]+\S/.test(lines[i])) { return { pre: true, text: block.replace(/\s+$/, '') }; }
  }
  var out = '';
  lines.forEach(function (raw) {
    var line = raw.trim();
    if (!line) { return; }
    if (!out) { out = line; return; }
    // 日本語どうしの境目に空白を入れない
    var a = out.charAt(out.length - 1), b = line.charAt(0);
    out += (CJK.test(a) && CJK.test(b)) ? line : ' ' + line;
  });
  return { pre: false, text: out };
}

function prose(host, text, cls) {
  clear(host);
  if (!text) { return host; }
  String(text).trim().split(/\n[ \t]*\n/).forEach(function (block) {
    var p = foldParagraph(block);
    if (!p.text) { return; }
    var node = el('p', cls || null, p.text);
    if (p.pre) { node.style.whiteSpace = 'pre-wrap'; }
    host.appendChild(node);
  });
  return host;
}

/* 案内だけの描画。`**ここ**` を太字にする（SPEC 3.13）。

   **innerHTML は使わない**（SPEC 7.7.2）。`<strong>` を作って
   textContent を入れるだけなので、シナリオの文字列は一度も HTML として
   解釈されない。使うのはチュートリアルの案内に限る —
   ふつうの散文（ブリーフィング・方針・証拠）は素のままにしておく。
   強調できる場所が増えると、書き手が語気で本命を指せるようになる（5.4）。 */
function emphProse(host, text) {
  clear(host);
  if (!text) { return host; }
  String(text).trim().split(/\n[ \t]*\n/).forEach(function (block) {
    var folded = foldParagraph(block);
    if (!folded.text) { return; }
    var node = el('p');
    if (folded.pre) { node.style.whiteSpace = 'pre-wrap'; }
    folded.text.split('**').forEach(function (part, i) {
      if (!part) { return; }
      if (i % 2) { node.appendChild(el('strong', null, part)); }
      else { node.appendChild(document.createTextNode(part)); }
    });
    host.appendChild(node);
  });
  return host;
}

function show(screenId) {
  var all = document.querySelectorAll('.screen');
  for (var i = 0; i < all.length; i++) { all[i].classList.remove('active'); }
  $(screenId).classList.add('active');
  window.scrollTo(0, 0);
}

function openModal(id) { $(id).classList.add('open'); }
function closeModal(id) { $(id).classList.remove('open'); }

/* 経過時間の表記。頭に + を付ける（SPEC 7.6.15）。

   画面には2種類の hh:mm が混ざる。ログの中身の「02:19:41」は壁時計で、
   こちらは開始からの経過である。印が無いと「08:50」は朝の8時50分に読める。
   経過時間を出すところは全部この関数を通す。 */
function elapsed(minutes) {
  var h = Math.floor(minutes / 60), m = minutes % 60;
  return '+' + (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
}

/* 押して開けるものの記号。証拠・アクションの束・畳んだ連絡で同じ形を使う。
   ここでしか作らない — 場所ごとに書くと、片方だけ記号が消える（SPEC 7.6.15）。 */
function caret(open) { return el('span', 'caret', open ? '▾' : '▸'); }

function num(v) { return Math.round(v).toLocaleString('ja-JP'); }

/* ── API ── */

function api(path, options) {
  return fetch(path, options).then(function (res) {
    if (!res.ok) {
      return res.json().then(
        function (body) { throw fromStatus(res.status, body.detail); },
        function () { throw fromStatus(res.status, res.statusText); }
      );
    }
    return res.json();
  }, function () {
    // fetch 自体が失敗した = サーバに届いていない。
    // ローカル実行なので、まず起動しているかを疑う（SPEC 7.6.1）
    var e = new Error(
      'サーバに接続できません。\n\n' +
      'ターミナルで python -m irdojo が動いているか確認して、\n' +
      'このページをリロードしてください。'
    );
    e.offline = true;
    throw e;
  });
}

function fromStatus(status, detail) {
  if (status === 404 && /セッション/.test(detail || '')) {
    var e = new Error(
      'この演習セッションは失われました。\n\n' +
      'セッションはサーバのメモリ上にしかないため、\n' +
      'サーバを再起動すると消えます。最初からやり直してください。'
    );
    e.sessionLost = true;
    return e;
  }
  return new Error(detail || ('サーバがエラーを返しました (' + status + ')'));
}

function fail(err) {
  window.alert(err.message);
  if (err.sessionLost) {
    S.sessionId = null;
    S.report = null;
    show('screen-top');
  }
}

/* ── ① トップ ── */

/* 入口に置く 3×3（SPEC 7.6.5）。

   この製品の主張は「同じ盤面・同じ真相でも、方針が変われば別の対応が
   最善になる」の一点で、以前はそれを文章で2回言っていた。
   **数字はサーバから来る。画面は1つも持たない** — 手で書いた数字は、
   盤面を触った日から嘘になる。生成物が古ければサーバが `available: false`
   を返し、この箱ごと出ない（古い数字は出ない）。 */
function loadPolicySwap() {
  return api('/api/policy-swap').then(renderPolicySwap, function () {
    // 入口の飾りである。取れなくても演習は選べるので、警告は出さない
    renderPolicySwap(null);
  });
}

/* 「この升目は何列目か」を印として置く。**位置を決めるのは数字である。**

   CSS に nth-child で対角を書いてしまうと、盤面を触って最高点が
   対角から外れた日に、光だけが古い対角を歩き続ける。
   何列目かだけを渡し、いつ光るかは CSS が --swap-col から計算する。 */
function markSwapColumn(node, index) {
  node.setAttribute('data-swap', String(index));
  node.style.setProperty('--swap-col', index);
}

function renderPolicySwap(data) {
  var host = $('top-swap');
  if (!data || !data.available || !data.rows || !data.rows.length) {
    host.hidden = true;
    renderTopMeta();
    return;
  }
  var cols = data.policies;

  // 各列の最高点。**印の位置は数字が決める。**
  // 対角に印を焼き付けると、盤面が変わったとき表だけが正しい顔で嘘をつく
  var best = {};
  cols.forEach(function (c) {
    best[c.id] = Math.max.apply(null, data.rows.map(function (r) {
      return r.scores[c.id];
    }));
  });

  var table = el('table', 'swap-grid');
  var thead = el('thead');
  var hr = el('tr');
  hr.appendChild(el('th'));
  cols.forEach(function (c, i) {
    var th = el('th', null, c.label);
    th.setAttribute('scope', 'col');
    // 列の見出しも、その列が照らされる番に一緒に上がる（SPEC 7.6.18）
    markSwapColumn(th, i);
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  var tbody = el('tbody');
  data.rows.forEach(function (r) {
    var tr = el('tr');
    var th = el('th', null, r.label);
    th.setAttribute('scope', 'row');
    tr.appendChild(th);
    var topIn = [];
    cols.forEach(function (c, i) {
      var isBest = r.scores[c.id] === best[c.id];
      var td = el('td', isBest ? 'best' : null, r.scores[c.id]);
      if (isBest) { markSwapColumn(td, i); topIn.push(i); }
      tr.appendChild(td);
    });
    // 行の見出しが上がるのは、その行が**ちょうど1つの列で**最高のとき。
    // 2列で最高なら、どちらの番に上がるべきか数字が決めてくれない
    if (topIn.length === 1) { markSwapColumn(th, topIn[0]); }
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  /* 対角を歩く光を入れるかどうか（SPEC 7.6.18）。

     **3列を前提にした形である。** 光っている時間の長さは CSS の
     keyframes に 1/3 と書いてあり、% に変数は書けない。列が3でない表に
     そのまま掛けると、2つ同時に光ったり間が空いたりして
     「最善が1つずつ移る」という主張と食い違う。
     そのときは .walk を付けない — 各列の最高点が青いことは
     動きの外にあるので、止まっても表は何も失わない。 */
  table.style.setProperty('--swap-cols', cols.length);
  if (cols.length === 3) { table.classList.add('walk'); }

  var mount = $('top-swap-table');
  clear(mount);
  mount.appendChild(table);

  // 何の表かは、行数・列数から組み立てる。件数を手で書かない。
  // **1行に収める** — 図の脇の注記が2行に折り返すと、図の側にも
  // 読むものが増える。入口の文章は左に集めてある
  $('top-swap-note').textContent =
    '同じ盤面を' + data.rows.length + '通りに解き、' + cols.length +
    'つの方針すべてで採点し直した点数。色付きが各列の最高点。';
  host.hidden = false;
  // 表の出し入れがあったら帯も組み直す。**どちらが先に返るかに依存させない**
  renderTopMeta();
}

/* 隅の帯（SPEC 7.6.18）。

   計器らしさは、数字が本物であることから出る。**ここに手で書いた値を
   1つでも混ぜると、その日から入口が嘘をつく** — 凡例のモックが
   3フェーズのまま腐ったのと同じ壊れ方をする（SPEC 7.6.7）。

   だから出所はこの3つだけ:
     - 版と生成物の指紋 … サーバがファイルから読んだもの（/api/meta）
     - 演習の数・方針の数 … **下に並べている札と同じ配列から数える**
     - 接続先 … いま開いている URL そのもの
   取れなかったものは黙って落ちる。事実が1つも無ければ帯ごと出ない。 */
function loadTopMeta() {
  return api('/api/meta').then(function (m) {
    S.meta = m;
    renderTopMeta();
  }, function () {
    // 入口の飾りである。取れなくても演習は選べるので警告は出さない
    renderTopMeta();
  });
}

function renderTopMeta() {
  var host = $('top-meta');
  clear(host);
  var facts = [];
  if (S.meta && S.meta.spec_version) { facts.push('SPEC ' + S.meta.spec_version); }
  // 指紋を名乗ってよいのは、その指紋が作った表が**画面に出ているとき**だけ。
  // 生成物が古ければサーバが null を返すが、取りに行けなかっただけでも
  // 表は消える。消えた表の素性を帯が語らないように、ここでも見る
  if (S.meta && S.meta.build && !$('top-swap').hidden) {
    facts.push('BUILD ' + S.meta.build);
  }
  if (S.scenarios.length) {
    // **練習と本番は別に数える**（SPEC 7.6.27）。入口の札は練習を
    // 1枚にまとめているので、合計だけを出すと「7 と言っているのに札が6枚」
    // になる。数えている配列は1つのまま、出す数を分ける
    var real = S.scenarios.filter(function (sc) { return !sc.tutorial; }).length;
    var practice = S.scenarios.length - real;
    facts.push(real + ' SCENARIOS');
    if (practice) { facts.push(practice + ' TUTORIALS'); }
    var seen = {}, n = 0;
    S.scenarios.forEach(function (sc) {
      sc.policies.forEach(function (pol) {
        if (!seen[pol.id]) { seen[pol.id] = true; n += 1; }
      });
    });
    if (n) { facts.push(n + ' POLICIES'); }
  }
  if (window.location.host) { facts.push(window.location.host); }

  if (!facts.length) { host.hidden = true; return; }
  ['IR DOJO'].concat(facts).forEach(function (text, i) {
    if (i) { host.appendChild(el('span', 'sep', '·')); }
    host.appendChild(el('span', null, text));
  });
  host.hidden = false;
}

/* 入っている演習を入口にも並べる。

   以前はここを1回押さないと、何が遊べるのかが分からなかった。 */
function renderTopScenarios() {
  var host = $('top-scenarios');
  clear(host);

  /* **練習は1枚にまとめる**（SPEC 7.6.27）。
     7本を札のまま並べると、1280×720 では2行になり、広い画面では
     1行7列に割れて題がまた切れる。**入口の札は題だけを出す**という
     決め方（7.6.19）は、枚数が増えると横で同じ問題に当たる。
     押すと、選ぶ画面の練習の枠が開いた状態で開く。 */
  var tutorials = S.scenarios.filter(function (sc) { return sc.tutorial; });
  if (tutorials.length) { host.appendChild(tutorialGroupCard(tutorials.length)); }

  S.scenarios.filter(function (sc) { return !sc.tutorial; }).forEach(function (sc) {
    var card = el('div', 'card card-pick');
    card.setAttribute('role', 'button');
    card.setAttribute('data-scenario', sc.id);
    card.setAttribute('tabindex', '0');
    // **入口の札は題だけを出す。** ここでの札の役目は「何が入っているか」を
    // 一目で見せることであって、絞り込みではない。選ぶための道具は
    // 選ぶ画面に置く。
    // 5本目で、タグの行が 1280×720 の入口を 37px 溢れさせた。行を減らして
    // 収めたが、今度は横が 5列に割れて題が3文字で切れた — 脇に置いた文字が
    // 題と幅を取り合うため。**札が細くなるほど、先に消えるのは題ではない。**
    fillScenarioCard(card, sc, { compact: true });
    function go() { toSelect(sc); }
    card.addEventListener('click', go);
    card.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    });
    host.appendChild(card);
  });
}

/* 入口に置く「練習」の1枚（SPEC 7.6.27）。

   **これは演習の札ではない。** 中身はシナリオから来ないので
   `fillScenarioCard` は通さない。押すと、選ぶ画面の練習の枠が
   開いた状態で開く。 */
function tutorialGroupCard(count) {
  var card = el('div', 'card card-pick card-tutorial');
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  card.setAttribute('data-tutorial-group', '1');
  var head = el('div', 'card-head');
  var title = el('div', 'card-title', 'チュートリアル');
  title.insertBefore(el('span', 'card-badge', '練習'), title.firstChild);
  head.appendChild(title);
  head.appendChild(el('div', 'dim', count + '本'));
  card.appendChild(head);
  function open() {
    toSelect(null);
    var g = $('tutorial-group');
    if (g) { g.open = true; }
  }
  card.addEventListener('click', open);
  card.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
  return card;
}

/* 選択画面へ渡す。札から来たときは、そのシナリオを選んだ状態にする */
function toSelect(sc) {
  show('screen-select');
  if (!S.scenarios.length) { loadScenarios().catch(fail); return; }
  if (sc) { selectScenario(sc); }
}

/* ── ② シナリオ選択 ── */

function loadScenarios() {
  return api('/api/scenarios').then(function (list) {
    S.scenarios = list;
    renderScenarioList();
    renderTopScenarios();
    // 帯の「N SCENARIOS」は**この配列から数える**。札と食い違いようがない
    renderTopMeta();
  }, function (err) {
    // 警告を閉じたあと真っ白にしない。画面上にも理由と復帰手段を残す
    showListError(err);
    throw err;
  });
}

function showListError(err) {
  var host = $('scenario-list');
  clear(host);
  var box = el('div', 'card card-error');
  box.appendChild(el('div', 'card-title',
    err.offline ? 'サーバに接続できません' : '一覧を取得できませんでした'));
  box.appendChild(el('p', 'dim', err.offline
    ? 'ローカルで動かす作りなので、まずサーバが起動しているか確認してください。'
    : err.message));
  if (err.offline) {
    var pre = el('pre', null, 'python -m irdojo');
    pre.style.cssText = 'font-family:var(--mono);background:#14181e;padding:10px 12px;border-radius:4px';
    box.appendChild(pre);
  }
  var retry = el('button', null, '再読み込み');
  retry.addEventListener('click', function () { loadScenarios().catch(function () {}); });
  box.appendChild(retry);
  host.appendChild(box);
  // 入口にも同じことを出す。**片方だけに出すと、入口は黙って空になる**
  var top = $('top-scenarios');
  clear(top);
  top.appendChild(el('p', 'dim', err.offline
    ? 'サーバに接続できていないため、演習の一覧を出せません。'
    : '演習の一覧を取得できませんでした。'));
  // 一覧が無い状態で開始させない
  clear($('policy-list'));
  clear($('assist-list'));
  $('btn-start').disabled = true;
}

/* シナリオの札の中身。**入口と選択画面で同じ関数から作る。**

   別々に書くと片方だけが古い並びのまま残る — 凡例の手書きモックが
   3フェーズのまま腐ったのと同じ壊れ方をする（SPEC 7.6.7）。 */
function fillScenarioCard(card, sc, opts) {
  var compact = !!(opts && opts.compact);
  var head = el('div', 'card-head');
  var title = el('div', 'card-title', sc.title);
  if (sc.tutorial) {
    // **練習の盤面には印を付ける。**（SPEC 3.13）
    // 付けないと、点が低く出たときに「自分は下手だ」と読まれる。
    // 案内どおりに歩く盤面は、そもそも競うためのものではない
    title.insertBefore(el('span', 'card-badge', '練習'), title.firstChild);
  }
  head.appendChild(title);
  if (!compact) {
    head.appendChild(el('div', 'dim', '約' + sc.estimated_play_minutes + '分'));
  }
  card.appendChild(head);
  if (compact) { return card; }

  /* **選ぶときに効くのは「どれだけ難しいか」ではなく「どの綱引きか」。**
     ここには複雑度（★1〜5）を出していた。あれはローダが内容から算出する
     正しい数字だが、同梱5本すべてが 3 に落ちる（raw 8.86〜9.92）。
     執筆ガイド（SPEC 8.1）が誤導2〜3・critical論点2〜3 を薦めており、
     その範囲は複雑度の定義域の一点だからである。**壊れているのではなく、
     定数なので絞り込みに使えない。** 数字は設計の道具として残し
     （`balance.py` が出す）、画面からは外した。SPEC 3.12。

     代わりに出すのは、その演習が持つ3つの方針の名前。
     **この製品の主張そのものが、ここに出ている** — 「安全最優先／生産継続
     最優先／証拠保全最優先」と「立証可能性最優先／早期封じ込め最優先／
     就業関係への配慮最優先」は、違う綱引きを練習することを一目で言う。
     漏洩にもならない。**方針はこの札のすぐ下で自分で選ぶもの**であって、
     盤面の真相ではない。 */
  if (opts && opts.policies === false) { return card; }
  var pol = el('div', 'card-policies');
  sc.policies.forEach(function (p) {
    pol.appendChild(el('span', 'pchip', p.label));
  });
  card.appendChild(pol);
  if (!opts || opts.tags !== false) {
    card.appendChild(el('div', 'faint', sc.tags.join(' / ')));
  }
  return card;
}

function renderScenarioList() {
  var host = $('scenario-list');
  clear(host);
  S.cards = [];
  $('btn-start').disabled = false;
  if (!S.scenarios.length) {
    host.appendChild(el('p', 'empty', 'scenarios/ にシナリオがありません。'));
    $('btn-start').disabled = true;
    return;
  }
  // カードは押して選ぶ。**2本目が入るまで、ここは押せなかった** —
  // 一覧を描いたあと無条件に先頭を選んでいたので、1本のときは
  // 見た目どおりに動き、2本目を足した瞬間に「2本目だけ遊べない」になる
  //
  // **練習は別の枠へ**（SPEC 7.6.27）。札のまま混ぜると、この画面は
  // 縦 1593px になり、開始ボタンが一度も見えない
  var group = $('tutorial-group');
  var tutorHost = $('tutorial-list');
  clear(tutorHost);
  var tutorials = S.scenarios.filter(function (sc) { return sc.tutorial; });
  var real = S.scenarios.filter(function (sc) { return !sc.tutorial; });
  group.hidden = !tutorials.length;
  // **見出しの本数は配列から入れる。** 手で書くと、1本足した日から嘘になる
  $('tutorial-count').textContent = '（' + tutorials.length + '本）';
  $('scenario-count').textContent = '（' + real.length + '本）';
  $('scenario-group').hidden = !real.length;

  function addCard(sc, into, compact) {
    var card = el('div', 'card card-pick' + (compact ? ' card-slim' : ''));
    card.setAttribute('role', 'radio');
    card.setAttribute('data-scenario', sc.id);
    card.setAttribute('tabindex', '0');
    fillScenarioCard(card, sc, compact ? { tags: false, policies: false } : null);
    card.addEventListener('click', function () { selectScenario(sc); });
    card.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectScenario(sc); }
    });
    S.cards.push({ id: sc.id, node: card });
    into.appendChild(card);
  }

  S.scenarios.forEach(function (sc) {
    if (sc.tutorial) { addCard(sc, tutorHost, true); }
    else { addCard(sc, host, false); }
  });
  // **既定で選ばれるのは本番の先頭。** 練習は畳んである側なので、
  // 選ばれた札が見えない状態で開始ボタンだけが有効になるのを避ける
  selectScenario(real[0] || S.scenarios[0]);
}

function selectScenario(sc) {
  var changed = !S.scenario || S.scenario.id !== sc.id;
  S.scenario = sc;
  // **閉じていても、何を選んでいるかは見える。**（SPEC 7.6.28）
  // 枠を畳んだまま始められるので、選択が見出しに出ていないと
  // 「何が始まるのか分からないまま開始ボタンだけが有効」になる
  var picked = $('scenario-picked');
  if (picked) { picked.textContent = sc ? '　選択中: ' + sc.title : ''; }
  // 方針とアシストはシナリオごとのもの。持ち越すと、前のシナリオにしか
  // 無い方針 id を送ることになる（サーバは知らない id を弾く）
  if (changed) { S.policy = sc.default_policy; S.assist = sc.default_assist_level; }
  S.policy = S.policy || sc.default_policy;
  S.assist = S.assist || sc.default_assist_level;
  S.cards.forEach(function (c) {
    var on = (c.id === sc.id);
    c.node.classList.toggle('card-on', on);
    c.node.setAttribute('aria-checked', on ? 'true' : 'false');
  });

  var pl = $('policy-list');
  clear(pl);
  sc.policies.forEach(function (p) {
    var lab = el('label', 'opt');
    var input = el('input');
    input.type = 'radio';
    input.name = 'policy';
    input.value = p.id;
    input.checked = (p.id === S.policy);
    input.addEventListener('change', function () { S.policy = p.id; });
    lab.appendChild(input);
    lab.appendChild(el('span', null, p.label));
    pl.appendChild(lab);
  });

  var al = $('assist-list');
  clear(al);
  ASSIST_NOTES.forEach(function (row) {
    var lab = el('label', 'opt');
    var input = el('input');
    input.type = 'radio';
    input.name = 'assist';
    input.value = row[0];
    input.checked = (row[0] === S.assist);
    input.addEventListener('change', function () { S.assist = row[0]; });
    lab.appendChild(input);
    lab.appendChild(el('span', null, row[1]));
    lab.appendChild(el('span', 'note', row[2]));
    al.appendChild(lab);
  });
}

/* ── ③ ブリーフィング / セッション開始 ── */

function startSession() {
  if (!S.scenario) { return; }
  api('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      scenario_id: S.scenario.id,
      policy_id: S.policy,
      assist_level: S.assist
    })
  }).then(function (data) {
    S.sessionId = data.session_id;
    S.view = data.view;
    // 案内（SPEC 3.13）。練習の盤面にしか入っていない
    S.tutorial = data.tutorial || [];
    S.tutorAt = 0;
    S.newIds = {};
    S.openIds = {};
    S.incoming = [];
    $('brief-title').textContent = data.title;
    prose($('brief-body'), data.briefing);
    $('brief-policy-name').textContent = '【今回の対応方針】' + data.policy_label;
    prose($('brief-policy-body'), data.policy_briefing);
    renderFlow(data.phases || []);
    // **見本は開いてから描く。** 畳んだ `<details>` の中は幅が 0 なので、
    // ここで描くとグラフも盤も潰れる（SPEC 7.6.9）。組むのは `toggle` のとき
    S.legendView = data.view;
    S.legendDrawn = false;
    $('brief-legend-fold').open = false;
    $('brief-flow-fold').open = false;
    $('brief-topo-fold').open = false;
    // 考え方の枠組みを出すかはアシストレベルが決める（3.10）
    var primer = $('brief-primer');
    primer.hidden = !data.view.show_primer;
    // **畳みは全部、既定で閉じている**（SPEC 7.6.28）。
    // アシストが決めるのは「出すかどうか」であって「開いているかどうか」ではない
    primer.open = false;
    // 構成図はアシスト。依存関係そのものは資産一覧に残る（3.10）
    var topo = data.view.show_topology;
    $('brief-topo').hidden = !topo;
    $('brief-topo-fold').hidden = !topo;
    $('brief-topo-head').hidden = !topo;
    show('screen-briefing');
    // 描画は画面を表示してから。非表示のままだと clientWidth が 0 になる
    if (topo) {
      renderTopology($('brief-topo'), data.view.assets);
      topologyExample($('brief-topo-example'), data.view.assets);
    }
  }).catch(fail);
}

/* ── 環境の構成図 ──
   資産の依存関係から段を決めて描く。エンジンと同じく、シナリオの中身は知らない。
   知っているのは「どれがどれに依存しているか」だけ。 */

var SVGNS = 'http://www.w3.org/2000/svg';

function svg(tag, attrs) {
  var n = document.createElementNS(SVGNS, tag);
  for (var k in attrs) { if (attrs.hasOwnProperty(k)) { n.setAttribute(k, attrs[k]); } }
  return n;
}

var CRIT_CLASS = { critical: 'crit', high: 'high', medium: 'med', low: '' };
var ACT_TAG = {
  investigate: '実行した調査',
  contain: '実行した対応',
  communicate: '出した連絡'
};

// 重要度＝止まったときに業務が受ける影響の大きさ。生の英語を画面に出さない
var CRIT_LABEL = { critical: '最重要', high: '重要', medium: '中', low: '小' };
function critLabel(c) { return CRIT_LABEL[c] || c; }

/* 依存の例文。いちばん多くを巻き込む資産を、シナリオから選んで名指しする。
   「dc01 を止めれば4台も止まる」を画面に手で書くと、別のシナリオで嘘になる */
function topologyExample(host, assets) {
  if (!host) { return; }
  host.textContent = '';
  if (!assets || !assets.length) { return; }
  var byId = {};
  assets.forEach(function (a) { byId[a.id] = a; });
  // 依存は木とは限らない。1つの資産が複数の上流にぶら下がる（DAG）と、
  // 経路の数だけ数えてしまう。7つしかない盤面で「10台」と言っていた
  function fallout(id, hit) {
    assets.forEach(function (a) {
      if (hit[a.id] || a.id === id) { return; }
      if ((a.depends_on || []).indexOf(id) < 0) { return; }
      hit[a.id] = true;
      fallout(a.id, hit);
    });
    return hit;
  }
  var best = null, bestN = 0;
  assets.forEach(function (a) {
    var n = Object.keys(fallout(a.id, {})).length;
    if (n > bestN) { best = a; bestN = n; }
  });
  if (!best) { return; }
  // 数え方の単位は「つ」。資産が端末とは限らない（原則1：エンジンは中身を知らない）
  host.textContent = best.id + ' を止めれば、それに依存する'
    + bestN + 'つも一緒に止まります。';
}

/* 箱の幅に収まるところまでラベルを畳む。11px の .name 用。

   全角はおよそ 11px、半角はおよそ 5.5px として数える。厳密な計測は
   getComputedTextLength でしかできないが、そのためには一度 DOM へ入れて
   から測り直すことになる（描画前に幅が 0 の状態で測ると必ず外す）。
   ここは「隣の箱を潰さない」ことが目的なので、見積もりで足りる。 */
function fitLabel(text, px) {
  var s = String(text || '');
  var w = 0, out = '';
  for (var i = 0; i < s.length; i++) {
    w += /[\x20-\x7e]/.test(s[i]) ? 5.5 : 11;
    if (w > px) { return out.replace(/[ (（]+$/, '') + '…'; }
    out += s[i];
  }
  return out;
}

/* 盤の記号の意味。**盤の中でも凡例でも、ここ1箇所から出す。**
   意味を言わないまま数字を出さない（SPEC 7.6.7 / 7.6.16） */
function boardKey() {
  return '線 = 依存（上が止まると下も実質止まる）　'
       + '手 = あなたが押した手のうち、この資産に手を当てた数　'
       + '証 = 手元の証拠のうち、この資産を名指ししている数　'
       + '止 = 止めた　除 = 取り除いた　破線の箱 = いま業務が止まっている';
}

/* 畳んだときに一行だけ残すもの（SPEC 7.6.16）。

   盤は既定で畳む。開いたままだと 300px 強を占め、押した結果の結論が
   画面の下へ落ちる（実測 y=949 / 画面 800）。**それでも盤が拾っていた
   ものは残す** — 盤の値打ちは「14手を終えて、この資産には手 0」に
   気づけることで、それは資産ごとに見なくても総数で分かる。

   **促さない。** 「あと3つ触っていない」と書けば盤が「押せ」と言い始める
   （SPEC 6.6）。書くのは押した側の数だけで、残りは引き算の結果として
   読み手の側にある。記号は使わない — 畳んでいるときは記号の意味
   （boardKey）が見えないので、ここだけは言葉で言う。 */
function boardTally(assets) {
  var list = assets || [];
  var touched = 0, stopped = 0, purged = 0;
  list.forEach(function (a) {
    if (a.touched) { touched++; }
    if (a.contained) { stopped++; }
    if (a.eradicated) { purged++; }
  });
  return '手を当てた資産 ' + touched + ' / ' + list.length
       + '　止めた ' + stopped + '　取り除いた ' + purged;
}

/* 盤ひとそろい（見出し・畳んだときの一行・記号の説明・図の入れ物）を
   組み立てる。プレイ画面と凡例の見本が同じ関数から出る。見本を別に書くと
   必ず腐る（フェーズ帯で一度腐らせている。SPEC 7.6.7）。

   記号の意味は**図と一緒に**畳む。畳んだ状態で「止 = 止めた」とだけ
   書いてあっても、その記号はどこにも出ていない。 */
function boardBox(assets) {
  var d = el('details', 'board');
  var sm = el('summary');
  sm.appendChild(el('span', 'board-title', '環境の構成と、手の当たり方'));
  sm.appendChild(el('span', 'board-tally', boardTally(assets)));
  d.appendChild(sm);
  var body = el('div', 'board-body');
  body.appendChild(el('div', 'board-key', boardKey()));
  var host = el('div', 'topo');
  body.appendChild(host);
  d.appendChild(body);
  return { box: d, host: host };
}

/* 構成図と資産盤。opts.marks を立てると、各資産に自分の手の当たり方を重ねる。

   **重ねるのは学習者自身の行動と、手元の証拠の数え直しだけである。**
   侵害の有無も、まだ押していない手が何を見に行くかも、ここには出ない
   （SPEC 7.6.16）。0 を赤くしないのも同じ理由 — 盤が「押せ」と促すと、
   網羅が正解だと教えることになる（SPEC 6.6）。 */
function renderTopology(host, assets, opts) {
  clear(host);
  if (!assets || !assets.length) { return; }
  var marks = !!(opts && opts.marks);

  // 依存の深さで段に分ける。循環はローダが弾いているので必ず収束する
  var byId = {};
  assets.forEach(function (a) { byId[a.id] = a; });
  var depth = {};
  function depthOf(id, seen) {
    if (depth[id] !== undefined) { return depth[id]; }
    var a = byId[id];
    var deps = (a && a.depends_on || []).filter(function (d) {
      return byId[d] && seen.indexOf(d) < 0;
    });
    var d = deps.length
      ? 1 + Math.max.apply(null, deps.map(function (x) { return depthOf(x, seen.concat([id])); }))
      : 0;
    depth[id] = d;
    return d;
  }
  assets.forEach(function (a) { depthOf(a.id, []); });

  var levels = [];
  assets.forEach(function (a) {
    var d = depth[a.id];
    (levels[d] = levels[d] || []).push(a);
  });

  var W = Math.max(host.clientWidth - 34, 320);
  // 印を重ねる盤は1行ぶん背が高い。段の間は詰める — 盤は常設なので、
  // ここで取った高さがそのまま押した結果の箱を画面外へ押し出す
  var GAP = 14, BH = marks ? 56 : 50, VGAP = marks ? 24 : 46, PAD = 10;
  var widest = Math.max.apply(null, levels.map(function (r) { return r.length; }));
  var BW = Math.min(168, Math.floor((W - (widest - 1) * GAP) / widest));
  var totalW = widest * BW + (widest - 1) * GAP;
  var totalH = levels.length * BH + (levels.length - 1) * VGAP + PAD * 2;

  var root = svg('svg', {
    width: totalW, height: totalH,
    viewBox: '0 0 ' + totalW + ' ' + totalH, role: 'img'
  });

  var pos = {};
  levels.forEach(function (row, d) {
    var rowW = row.length * BW + (row.length - 1) * GAP;
    var x0 = (totalW - rowW) / 2;
    row.forEach(function (a, i) {
      pos[a.id] = { x: x0 + i * (BW + GAP), y: PAD + d * (BH + VGAP) };
    });
  });

  // 線を先に描いて、箱の下に潜らせる
  assets.forEach(function (a) {
    (a.depends_on || []).forEach(function (d) {
      if (!pos[d]) { return; }
      var from = pos[a.id], to = pos[d];
      var x1 = from.x + BW / 2, y1 = from.y;
      var x2 = to.x + BW / 2, y2 = to.y + BH;
      var my = (y1 + y2) / 2;
      root.appendChild(svg('path', {
        'class': 'edge',
        d: 'M' + x1 + ' ' + y1 + ' L' + x1 + ' ' + my +
           ' L' + x2 + ' ' + my + ' L' + x2 + ' ' + y2
      }));
    });
  });

  assets.forEach(function (a) {
    var p = pos[a.id];
    var cls = 'node ' + (CRIT_CLASS[a.criticality] || '');
    // 業務が止まっている資産は薄くする。**印を1つ増やさずに状態を出す** —
    // 「止めた（封じ込め）」と「止まった（業務）」に同じ字を使うと、
    // 画面の中で「止」の意味が2つになる
    if (marks && a.halted) { cls += ' halted'; }
    root.appendChild(svg('rect', {
      'class': cls, x: p.x, y: p.y, width: BW, height: BH, rx: 5
    }));
    var id = svg('text', { 'class': 'id', x: p.x + 11, y: p.y + 21 });
    id.textContent = a.id;
    root.appendChild(id);
    var tag = svg('text', { 'class': 'crit-tag', x: p.x + BW - 11, y: p.y + 21,
                            'text-anchor': 'end' });
    tag.textContent = critLabel(a.criticality);
    root.appendChild(tag);
    var nm = svg('text', { 'class': 'name', x: p.x + 11, y: p.y + 38 });
    // SVG のテキストは箱からはみ出しても切れない。隣の箱の上に重なって
    // 両方読めなくなる（「テナント管理者アカウント (岸)」が隣を潰していた）。
    // ラベルの長さは作者の自由なので、描く側で畳む
    nm.textContent = fitLabel(a.label, BW - 22);
    root.appendChild(nm);
    if (!marks) { return; }

    // 自分の注意の写像。触れていない資産は 0 のまま薄く出る
    var n = a.touched || 0, m = a.mentions || 0;
    var cnt = svg('text', {
      'class': 'tally' + (n || m ? '' : ' none'), x: p.x + 11, y: p.y + 50
    });
    cnt.textContent = '手 ' + n + '  証 ' + m;
    root.appendChild(cnt);

    var done = [];
    if (a.contained) { done.push('止'); }
    if (a.eradicated) { done.push('除'); }
    if (done.length) {
      var dn = svg('text', { 'class': 'done-tag', x: p.x + BW - 11, y: p.y + 50,
                             'text-anchor': 'end' });
      dn.textContent = done.join(' ');
      root.appendChild(dn);
    }
  });

  host.appendChild(root);
}

/* 画面の見方。数字も記号も、意味を言わないまま出さない。
   出る予定のないもの（hard のグラフや論点）は載せない。

   **見本は本物から作る。**（SPEC 7.6.7）
   フェーズ帯・帯の数値・アクションの束は、いま渡されているビューから描く。
   手で書いたモックは必ず腐る — 3フェーズを2フェーズに変えたとき、
   凡例だけが「初動トリアージ › 本格調査 › 封じ込め・対応」のまま残った。

   ブリーフィングとプレイ中のモーダルの両方から呼ぶ。開始前にしか読めない
   凡例は、●/○ の意味を思い出したいときには存在しないのと同じ。 */

/* 実物の複製から見本を作る。id は落とす（同じ id が2つある DOM を作らない） */
function cloneWithoutIds(node) {
  var c = node.cloneNode(true);
  c.removeAttribute('id');
  var ids = c.querySelectorAll('[id]');
  for (var i = 0; i < ids.length; i++) { ids[i].removeAttribute('id'); }
  return c;
}

function renderLegend(view, mini, notes) {
  clear(mini); clear(notes);

  var n = 0;
  function region(node, text) {
    n += 1;
    var w = el('div', 'mini-region');
    w.appendChild(el('span', 'mini-num', String(n)));
    w.appendChild(node);
    notes.appendChild(el('li', null, text));
    return w;
  }

  // ── ヘッダ
  var bar = el('div', 'mini-bar');
  bar.appendChild(el('div', 't', view.scenario_title));
  bar.appendChild(el('div', 'p', '方針: ' + view.policy_label));
  mini.appendChild(bar);

  // ── 状況の帯。数値は実物の複製なので、見出しも並びも本物と食い違わない
  var strip = el('div', 'mini-strip');
  strip.appendChild(region(cloneWithoutIds($('play-strip-stats')),
    '経過時間・累積被害・業務影響は、この対応がもたらした帰結です。' +
    'どれも採点しません（見出しを押すと理由が開きます）。' +
    '経過時間は仮想の時計で、アクションを押した分だけ進みます。' +
    '頭の + は「開始からの経過」という印です。' +
    '累積被害は金額ではなく比べるための相対値で、' +
    '侵害された資産を止めると増え方が鈍り、' +
    'そのうえで元の状態に戻すと、いちばん鈍ります。' +
    '業務影響は止めた資産が業務に与える影響で、何も止めていない間は 0 です。'));

  var cv = null;
  if (view.damage_history) {
    var cw = el('div', 'mini-chart');
    cv = document.createElement('canvas');
    cv.setAttribute('height', '54');
    cw.appendChild(cv);
    strip.appendChild(region(cw,
      '累積被害の推移です。途中で傾きが変わることがありますが、' +
      'どこで変わるかは表示されません。'));
  }

  if (view.open_questions) {
    var qs = el('div', 'mini-q');
    qs.appendChild(el('h3', null, '未解消の論点'));
    [['●', 'まだ説明がついていない', 'この論点の問いが並びます'],
     ['○', '説明がついた', 'こちらは解消済みです']].forEach(function (q, i) {
      var r = el('div', 'q' + (i ? ' done' : ''));
      r.appendChild(el('span', 'mark', q[0]));
      var b = el('div');
      b.appendChild(el('div', 'label', q[1]));
      b.appendChild(el('div', 'text', q[2]));
      r.appendChild(b);
      qs.appendChild(r);
    });
    strip.appendChild(region(qs,
      'まだ説明がついていない論点（●）と、ついた論点（○）です。' +
      '未解消のまま対応に移ることもできますが、その状態は記録され、採点されます。'));
  }
  mini.appendChild(strip);

  // ── フェーズ帯と資産盤は画面いっぱいに敷いてある。凡例でも同じ並びにする
  var row = el('div', 'mini-row');
  row.appendChild(region(phaseBar(view.phases),
    'いま何段目かを示します。進むのはあなたが宣言したときだけで、' +
    '一度進むと戻れません。'));

  var board = null;
  if (view.show_topology) {
    board = boardBox(view.assets);
    board.box.open = true;
    row.appendChild(region(board.box,
      '環境の構成に、あなたの手の当たり方を重ねた盤です。' +
      '線は依存で、上のものが止まると下のものも実質止まります。' +
      '「重要」などの印は止めたときに業務が受ける影響の大きさで、' +
      '侵害の有無とは関係ありません。' +
      '「手」はあなたが押した手のうちその資産に手を当てた数、' +
      '「証」は手元の証拠のうちその資産を名指ししている数です。' +
      'どちらもあなたの行動と手元の資料を数えただけで、' +
      '数が多いことも 0 であることも、当たりでも外れでもありません。' +
      '止めた資産には「止」、取り除いた資産には「除」が付き、' +
      '業務が止まっている資産は箱が破線になります — '
      + '止めた資産が上流なら、下流もつられて破線になります。'
      + 'プレイ中は畳んであります。畳んだままでも、見出しの横に'
      + '「手を当てた資産 / 止めた / 取り除いた」の数だけは出ています。'));
  }
  // 事件の時計も画面いっぱいに敷いてある。見本は実画面と同じ関数から出す。
  // 開始前は空なので、**最初に見る姿**（まだ何も並んでいない）がそのまま出る
  var tl = el('section', 'tl');
  row.appendChild(region(tl,
    '取った証拠を、ログの中に書かれている時刻の順に並べた軸です。' +
    '点の位置は時刻の比例なので、同じ時刻に起きたことは同じ位置に重なります。' +
    '近いことは、同じ原因であることを意味しません。' +
    'ここに出るのはあなたが取った証拠だけで、取っていないものは並びません。' +
    '札を押すと、その証拠のカードが開きます。' +
    '台帳や、期間をまとめた集計のように出来事の時刻を持たない資料は、' +
    '軸には並ばず件数だけが下に出ます。' +
    '大きく間が空いているところは軸を切ってあり、切った跡に空いた長さを書きます。' +
    'なお、ここの時刻はログの中の壁時計で、帯の「経過時間」（+00:30）とは' +
    '別の時計です。'));
  mini.appendChild(row);

  // ── 本体
  var cols = el('div', 'mini-cols');
  var left = el('div', 'mini-left');

  var oc = el('div', 'outcome');
  oc.appendChild(outcomeHead('investigate', '押したアクション', '30分を使った'));
  oc.appendChild(el('div', 'outcome-msg found', '分かったこと 1 件'));
  left.appendChild(region(oc,
    '押した結果です。何が分かったか、何も出なかったか、証拠を壊したかを必ず告げます。' +
    '払った時間もここに出ます。' +
    '何も出なかったときは、この箱ごと琥珀色になり「何も出てこなかった。」と出ます。'));

  left.appendChild(region(evidenceCard({
    id: 'ev_001',
    summary: view.assist_level === 'hard' ? '' : '見つかったことの要約',
    at_minute: 30,
    content: '（押すと、取得した生のログがここに開きます）',
    sample: true
  }, false),
    '手に入れた証拠です。右端に ▸ が付いているものは押すと開きます — ' +
    '証拠も、畳まれた連絡も、アクションの束も同じ印です。' +
    '添えてある +00:30 は取得した時点の経過時間で、' +
    'ログの中に出てくる時刻（現実の壁時計）とは別のものです。' +
    '新しいものが上に積まれ、古いものは畳まれます。'));
  cols.appendChild(left);

  // ── アクション。見本は本物の束から作る
  var right = el('div', 'mini-right');
  var panel = el('div', 'mini-panel');
  panel.appendChild(el('h3', null, '実行可能なアクション'));
  var acts = view.available_actions || [];
  // まだ選べる手が残っている束を見本にする。全部やり尽くした束を出すと、
  // 灰色だけが並んで「選べるもの」の見本にならない
  var lead = acts.filter(function (a) { return a.selectable; })[0] || acts[0];
  var key = lead ? (lead.group || lead.phase_label) : '';
  var group = acts.filter(function (a) { return (a.group || a.phase_label) === key; });
  var remain = group.filter(function (a) { return a.selectable; }).length;
  panel.appendChild(phaseGroupHead(key, remain, group.length, false));
  group.slice(0, 2).forEach(function (a) { panel.appendChild(actionCard(a)); });
  right.appendChild(region(panel,
    '選べる調査と封じ込めです。右上の数字はかかる時間で、押すと仮想の時計が' +
    'その分だけ進みます（実際に待つわけではありません）。' +
    '一度実行したものは灰色になり、もう選べませんが、消えずに残ります。' +
    '見出しの「未実行」の数は、まだ選んでいない数 / いま選べる数です。' +
    '調べると選択肢が増えるので、右の数は途中で増えます。' +
    '見出しを押すと、その束を畳めます。'));
  cols.appendChild(right);
  mini.appendChild(cols);

  // 描画は組み立ててから。非表示のままだと clientWidth が 0 になる。
  // **条件を付けない** — hard はグラフも盤も出ないので、
  // 条件に入れると事件の時計だけが描かれないまま残る
  {
    setTimeout(function () {
      if (cv) {
        var s = [], v = 0;
        for (var i = 0; i < 90; i++) { v += 1 + Math.pow(i / 30, 3); s.push(v); }
        DamageChart.draw(cv, s, {});
      }
      // 見本も本物の盤と同じ関数・同じビューから描く。
      // 開始前なら「手 0 証 0」が並ぶ — それが実際に最初に見る盤である
      if (board) { renderTopology(board.host, view.assets, { marks: true }); }
      renderTimeline(tl, view, { sample: true });
    }, 0);
  }
}

function renderFlow(phases) {
  var host = $('brief-phases');
  clear(host);
  phases.forEach(function (p, i) {
    var li = el('li');
    li.appendChild(el('div', 'n', p.label));
    if (PHASE_NOTES[i]) { li.appendChild(el('div', 'd', PHASE_NOTES[i])); }
    host.appendChild(li);
  });

  // 被疑判定を求めるフェーズがあるなら、驚かせずに先に言っておく
  var needs = phases.filter(function (p) { return p.requires_assessment; })[0];
  var rule = $('brief-assessment-rule');
  clear(rule);
  if (needs) {
    rule.appendChild(el('strong', null,
      '「' + needs.label + '」へ移る前に、被疑判定の宣言を求めます。'));
    rule.appendChild(el('span', null,
      ' 何を封じ込めるかではなく、何が侵害されていると判断したかを答えてください。' +
      'この宣言の時点が、採点の基準になります。'));
    rule.hidden = false;
  } else {
    rule.hidden = true;
  }
}

/* ── ④ プレイ ── */

function decide(payload) {
  return api('/api/session/' + S.sessionId + '/decide', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(function (res) {
    S.view = res.view;
    if (res.needs_assessment) {
      openAssessment();
      return res;
    }
    // 結果の箱だけが開いている状態にする。過去の分は必ず畳む
    S.newIds = {};
    S.openIds = {};
    (res.revealed_evidence || []).forEach(function (ev) {
      S.newIds[ev.id] = true;
      S.openIds[ev.id] = true;
    });
    // **アクション以外の決定で、直前の結果を書き換えないこと。**
    // フェーズ移行と被疑判定の宣言もここを通るので、素で上書きすると
    // `revealed` が 0 に戻る — 直前に押した調査の箱が、宣言した瞬間に
    // 「何も出てこなかった。」に化ける。**画面が過去を書き換えて嘘をつく**
    // 形で、証拠は手元にあるのに、見つけた事実だけが消えて見えた（v1.40）
    if (S.lastOutcome && payload && payload.kind === 'action') {
      S.lastOutcome.revealed = (res.revealed_evidence || []).length;
      S.lastOutcome.unlocked = res.unlocked_count || 0;
      S.lastOutcome.contained = res.contained || [];
      S.lastOutcome.eradicated = res.eradicated || [];
      S.lastOutcome.halted = res.halted || [];
      S.lastOutcome.already = res.already || [];
      S.lastOutcome.haltsBusiness = !!res.halts_business;
      S.lastOutcome.impact = res.business_impact_delta || 0;
      S.lastOutcome.prevented = res.prevented_count || 0;
      S.lastOutcome.preserved = !!res.preserved;
      S.lastOutcome.command = res.command || S.lastOutcome.command;
      S.lastOutcome.running = false;
    }
    advanceTutorial(payload);
    S.incoming.forEach(function (ev) { ev.fresh = false; });
    (res.events || []).forEach(function (ev) {
      ev.fresh = true;
      S.incoming.push(ev);
    });
    if (res.finished) { loadReport(); return res; }
    renderPlay();
    if (S.lastOutcome) { scrollAfterAction(); }
    if (S.pendingFinish && S.view.current_assessment.length) {
      S.pendingFinish = false;
      openFinish();
    }
    return res;
  }).catch(fail);
}

/* 押したあとの視点。**押した結果の結論が読めるところまで必ず送る。**

   画面の一番上（帯の上）まで戻すと、追従している帯のぶんだけ空振りする。
   盤の頭に戻すだけにしていた頃は、盤を開いた人の画面から結論が落ちた
   （実測 y=949 / 画面 800）。残るのは端末の数字だけなので、空振りした手が
   成果を出したように読める — **当たりと外れが同じ顔になる**。

   だから規則は2段にする。まず盤の頭へ戻し、そこから結論
   （結果の箱の最初の一行）が読めないなら、結果の箱の頭まで送る。
   盤を畳んであれば前者で足り、盤・結果の順に同じ画面へ並ぶ。 */
function scrollAfterAction() {
  var main = document.querySelector('.play-main');
  if (!main) { window.scrollTo(0, 0); return; }
  var css = getComputedStyle(document.documentElement);
  var off = (parseInt(css.getPropertyValue('--header-h'), 10) || 66)
          + (parseInt(css.getPropertyValue('--strip-h'), 10) || 140) + 10;
  var top = function (e) {
    return window.pageYOffset + e.getBoundingClientRect().top - off;
  };
  var y = top(main);
  var box = document.querySelector('#result-area .outcome');
  var point = box && box.querySelector('.outcome-msg');
  if (point) {
    var vh = window.innerHeight;
    var bottom = window.pageYOffset + point.getBoundingClientRect().bottom + 8;
    if (bottom > y + vh) {
      // 箱の頭を上端に寄せてなお結論が入らないなら（端末の記録が長い回）、
      // 結論の側を画面の下端に合わせる。上より下を優先する
      y = top(box) + vh >= bottom ? top(box) : bottom - vh;
    }
  }
  window.scrollTo(0, Math.max(0, y));
}

function renderPlay() {
  var v = S.view;
  $('play-title').textContent = v.scenario_title;
  $('play-policy').textContent = '　方針: ' + v.policy_label;
  renderPhaseBar(v);
  $('stat-time').textContent = elapsed(v.elapsed_minutes);
  $('stat-damage').textContent = num(v.accumulated_damage);
  $('stat-impact').textContent = num(v.accumulated_business_impact);

  renderEvidence(v);
  renderQuestions(v);
  renderActions(v);

  var adv = $('btn-advance');
  if (v.advance_label) {
    adv.style.display = '';
    adv.textContent = v.advance_label;
  } else {
    adv.style.display = 'none';
  }
  $('btn-show-assessment').style.display = v.current_assessment.length ? '' : 'none';
  show('screen-play');

  // ヘッダと帯の高さを実測して追従位置に反映する。帯の高さはアシストレベルで
  // 変わる（hard ではグラフも論点も無い）ので、決め打ちにすると
  // 右カラムが帯の下に潜るか、無駄な隙間が空く
  var root = document.documentElement;
  root.style.setProperty('--header-h', Math.round(
    document.querySelector('#screen-play .bar').getBoundingClientRect().height) + 'px');
  root.style.setProperty('--strip-h', Math.round(
    document.querySelector('.strip').getBoundingClientRect().height) + 'px');

  /* **右カラムの天井も実測する。**（SPEC 7.6.24）
     アクション欄の高さは `100vh - ヘッダ - 帯 - 130px` と書いてあったが、
     この 130px はフェーズ帯・資産盤・事件の時計（実測 196px）を
     1つも数えていなかった。結果、右カラムの下端がどの画面でも
     **ぴったり 186px はみ出し**、「対応フェーズに移る」と「対応を終了する」は
     スクロールしないと一度も見えなかった。テスターに指摘されるまで
     気づいていない — measuring していたのは入口だけだった。

     手で足し直しても、この上に何か置いた日にまた狂う。
     **画面の上端から右カラムまでの距離をそのまま測る。** */

  // 描画は画面を表示してから。非表示のままだと clientWidth が 0 になる
  var chart = $('damage-chart');
  var wrapEl = chart.parentNode;
  wrapEl.style.display = v.damage_history ? 'flex' : 'none';
  if (v.damage_history) { DamageChart.draw(chart, v.damage_history, {}); }
  renderBoard(v);
  renderTimeline($('play-timeline'), v);
  renderTutorial();
  measureCols();
}

/* **右カラムの天井を実測する。**（SPEC 7.6.24）

   以前は CSS に `100vh - ヘッダ - 帯 - 130px` と書いてあった。
   この 130px は、上にあるフェーズ帯・資産盤・事件の時計（実測 196px）を
   1つも数えていなかったので、**どの画面サイズでもぴったり 186px はみ出し**、
   「対応フェーズに移る」と「対応を終了する」は一度も見えなかった。

   **盤面と時計を描き終えてから、同期で測る。** `requestAnimationFrame` に
   逃がすと、描き終える前に測る回が混じって数 px ずれる（実測で 7px の
   はみ出しが出たり出なかったりした）。ずれる測定は、測っていないのと同じ。 */
function measureCols() {
  var cols = document.querySelector('#screen-play .play-cols');
  if (!cols) { return; }
  var root = document.documentElement;
  var top = cols.getBoundingClientRect().top + (window.scrollY || 0);
  root.style.setProperty('--cols-top', Math.round(top) + 'px');
  // 下の余白も測る。手で書くと、余白を1回調整した日にまた溢れる
  var pad = parseInt(getComputedStyle(cols.parentNode).paddingBottom, 10) || 0;
  root.style.setProperty('--cols-bottom', pad + 'px');
}

/* ── チュートリアルの案内（SPEC 3.13 / 7.6.26） ──

   **書いてあるのは手順と理由で、答えではない。** 答えはふつうの演習と
   同じく講評で初めて出る（5.9）。段の文面も、押してほしい手も、
   全部シナリオから来る — ここには1文字も書かない。

   **手を名指しする段には必ず「ただし」が付く。** 学習者に「まずこうしましょう」と
   言う以上、それが唯一の正解ではないことを同じ画面で言わないと、
   この製品の主張（方針が変われば最善も変わる）と正面から衝突する。
   欠けている案内はローダが受け付けない（`_reject_broken_tutorial`）。 */

function currentStep() {
  if (!S.tutorial || !S.tutorial.length) { return null; }
  return S.tutorial[S.tutorAt] || null;
}

/* 押した手が、いま待っている段のものだったら次へ進む。
   **待っていないものを押しても止めない。** 案内は道しるべであって
   通せんぼではない（寄り道して戻ってきた人を、ここで詰まらせない）。 */
function advanceTutorial(payload) {
  var step = currentStep();
  if (!step || !payload) { return; }
  var hit = step.expect_action
    ? (payload.kind === 'action' && payload.action_id === step.expect_action)
    : (step.expect_kind ? payload.kind === step.expect_kind : false);
  if (hit) { S.tutorAt += 1; }
}

function clearPointer() {
  var marked = document.querySelectorAll('.tutor-point');
  for (var i = 0; i < marked.length; i++) {
    marked[i].classList.remove('tutor-point');
  }
}

function renderTutorial() {
  var host = $('tutor-panel');
  clear(host);
  clearPointer();
  var step = currentStep();
  if (!step) { host.hidden = true; return; }
  host.hidden = false;

  var head = el('div', 'tutor-head');
  head.appendChild(el('span', 'tutor-step',
    (S.tutorAt + 1) + ' / ' + S.tutorial.length));
  head.appendChild(el('span', null, '練習の案内'));
  host.appendChild(head);

  var body = el('div', 'tutor-body');
  emphProse(body, step.body);
  host.appendChild(body);

  if (step.caveat) {
    var cav = el('div', 'tutor-caveat');
    cav.appendChild(el('b', null, 'ただし '));
    var span = el('span');
    emphProse(span, step.caveat);
    cav.appendChild(span);
    host.appendChild(cav);
  }

  if (step.expect_action) {
    // **手の名前は盤面から引く。** 案内に書き写すと、手の文言を直した日に
    // 案内だけが古い名前を指す（SPEC 7.6.7 と同じ壊れ方）
    var label = step.expect_action;
    (S.view.available_actions || []).forEach(function (a) {
      if (a.id === step.expect_action) { label = a.label; }
    });
    var wait = el('div', 'tutor-wait');
    wait.appendChild(document.createTextNode('右から '));
    wait.appendChild(el('span', 'what', label));
    wait.appendChild(document.createTextNode(' を押してください。'));
    host.appendChild(wait);
  } else if (step.expect_kind) {
    var w2 = el('div', 'tutor-wait');
    w2.appendChild(document.createTextNode(
      step.expect_kind === 'advance_phase' ? '下の ' :
      step.expect_kind === 'declare_assessment' ? '下の ' : '下の '));
    w2.appendChild(el('span', 'what',
      step.expect_kind === 'advance_phase' ? '対応フェーズに移る' :
      step.expect_kind === 'declare_assessment' ? '被疑判定を宣言する' :
      '対応を終了する'));
    w2.appendChild(document.createTextNode(' を押してください。'));
    host.appendChild(w2);
  } else {
    // 読むだけの段。自分で進める
    var row = el('div', 'btn-row');
    var next = el('button', 'primary', '次へ');
    next.addEventListener('click', function () {
      S.tutorAt += 1;
      renderTutorial();
    });
    row.appendChild(next);
    host.appendChild(row);
  }

  if (step.points_at) {
    var target = document.querySelector(step.points_at);
    if (target) { target.classList.add('tutor-point'); }
  }
}

/* ── 事件の時計（SPEC 7.6.17） ──

   key_lessons の1行目は「時刻の近さは関係の証明にならない」と言うのに、
   **学習者に時刻を並べる場所が無かった。** 時刻は生ログの中に
   `2026-03-14T02:17:33Z` の形で埋まっていて、カードをまたいで頭の中で
   並べるしかない。並べる作業を要求しておいて、並べる場所を出していない。

   置くのは**学習者が取った証拠だけ**で、真実は一切使わない。誤導かどうかで
   色も順序も大きさも変えない（変えたら答えを配る）。取るほど埋まるので、
   調査の進み方そのものが図になる。

   **2つの時計を混ぜない。** 被害グラフは経過時間（+03:20）、こちらは
   事件の壁時計（02:34）である。軸を共有させず、見出しでそう言う。 */

// 時刻を持つ出来事の間が、この分数より空いていたら軸を切る。
// 切った跡には「何日空いたか」を書く — 切ったことを黙ると軸が嘘をつく
var TL_BREAK_MINUTES = 60;
// 切った跡に与える幅（分に換算した見かけの長さ）。
// 潰さずに実寸で描くと、1か月離れた2本目が右端の1点に潰れる
var TL_BREAK_SPAN = 12;
// 札の最大幅の候補。上から順に試し、段が TL_MAX_LANES に収まった幅を採る。
// 最後の候補では要約が落ちて時刻と id だけになる（hard と同じ姿）
var TL_LABEL_WIDTHS = [280, 210, 150, 104];
var TL_MAX_LANES = 6;
// 1段の高さ（px）。CSS の .tl-item の高さと揃える
var TL_LANE_H = 18;

/* 「2026-03-14 02:17」を分に直す。並べ替えと間隔の計算にだけ使う */
function tlMinutes(at) {
  var m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(at || '');
  if (!m) { return null; }
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 60000;
}

/* 空いた間を言葉にする。「32日」「2時間」。切った跡にだけ出す */
function tlGapLabel(minutes) {
  if (minutes >= 1440) { return Math.round(minutes / 1440) + '日'; }
  return Math.round(minutes / 60) + '時間';
}

/* 事件の時計の並び。取った証拠のうち、時刻を持つものだけを時刻順に */
function timelineEntries(v) {
  var got = (v && v.obtained_evidence) || [];
  var out = [];
  got.forEach(function (ev) {
    var t = tlMinutes(ev.occurred_at);
    if (t === null) { return; }
    out.push({ id: ev.id, at: t, clock: ev.occurred_at.slice(11), day: ev.occurred_at.slice(0, 10),
               summary: ev.summary || '' });
  });
  out.sort(function (a, b) { return a.at - b.at || (a.id < b.id ? -1 : 1); });
  return out;
}

/* 軸の目盛りを組み立てる。空きが TL_BREAK_MINUTES を超えたら切って、
   切った跡に「何日空いたか」を置く。返すのは各点の論理座標（0..1） */
function timelineScale(entries) {
  var breaks = [];
  var span = 0;
  var pos = [0];
  for (var i = 1; i < entries.length; i++) {
    var gap = entries[i].at - entries[i - 1].at;
    if (gap > TL_BREAK_MINUTES) {
      breaks.push({ at: span + TL_BREAK_SPAN / 2, label: tlGapLabel(gap) });
      span += TL_BREAK_SPAN;
    } else {
      span += gap;
    }
    pos.push(span);
  }
  var scale = span > 0 ? 1 / span : 0;
  return {
    frac: pos.map(function (x) { return span > 0 ? x * scale : 0.5; }),
    breaks: breaks.map(function (b) { return { frac: b.at * scale, label: b.label }; })
  };
}

/* 何段に積むか。札は左から順に、前の札と重ならない最初の段へ置く */
function timelineLanes(items, containerW, maxW) {
  var ends = [];
  var lanes = [];
  for (var i = 0; i < items.length; i++) {
    var w = Math.min(items[i].natural, maxW);
    // 右端にかかる札は、点の**手前**に引く。入れ物の右端へ寄せて畳むと、
    // 札とその点の対応が切れる
    var left = items[i].x + w <= containerW ? items[i].x
             : Math.max(0, Math.min(items[i].x - w, containerW - w));
    var lane = 0;
    while (ends[lane] !== undefined && ends[lane] > left - 6) { lane++; }
    ends[lane] = left + w;
    lanes.push({ lane: lane, left: left, width: w });
  }
  return lanes;
}

/* 事件の時計を描く。**画面に入れてから測る** — clientWidth が 0 のまま
   段を割ると、全部が1段に重なる（被害グラフと構成図で2度やっている） */
function renderTimeline(host, v, opts) {
  clear(host);
  var entries = timelineEntries(v);
  var untimed = ((v && v.obtained_evidence) || []).length - entries.length;

  var head = el('div', 'tl-head');
  head.appendChild(el('span', 'tl-title', '事件の時計'));
  head.appendChild(el('span', 'tl-note',
    'ログの中の時刻です。帯の「経過時間 +」とは別の時計です。'));
  host.appendChild(head);

  if (!entries.length) {
    host.appendChild(el('div', 'tl-empty',
      '取った証拠のうち、出来事の時刻を持つものがここに並びます。'));
    return;
  }

  var band = el('div', 'tl-band');
  var rule = el('div', 'tl-rule');
  band.appendChild(rule);
  var sc = timelineScale(entries);

  // 同じ日に収まっているなら時刻だけ、日をまたぐなら日付も出す
  var multiday = entries[0].day !== entries[entries.length - 1].day;
  var lanes = el('div', 'tl-lanes');

  entries.forEach(function (e, i) {
    var dot = el('span', 'tl-dot');
    dot.style.left = (sc.frac[i] * 100) + '%';
    band.appendChild(dot);

    var btn = el('button', 'tl-item');
    btn.appendChild(el('b', null, (multiday ? e.day.slice(5) + ' ' : '') + e.clock));
    btn.appendChild(el('span', 'tl-what', e.summary || e.id));
    btn.setAttribute('data-ev', e.id);
    if (!(opts && opts.sample)) {
      btn.addEventListener('click', function () { focusEvidence(e.id); });
    }
    lanes.appendChild(btn);
    e.node = btn;
  });

  sc.breaks.forEach(function (b) {
    var mark = el('span', 'tl-break', b.label);
    mark.style.left = (b.frac * 100) + '%';
    band.appendChild(mark);
  });

  band.appendChild(lanes);
  host.appendChild(band);

  if (untimed > 0) {
    host.appendChild(el('div', 'tl-rest',
      'ほかに、出来事の時刻を持たない資料が ' + untimed
      + ' 件あります（台帳や、期間をまとめた集計）。'));
  }

  var place = function () {
    var w = band.clientWidth;
    if (!w) { return false; }
    var items = entries.map(function (e, i) {
      e.node.style.maxWidth = 'none';
      return { natural: e.node.offsetWidth, x: Math.round(sc.frac[i] * w) };
    });
    var put = null, maxW = TL_LABEL_WIDTHS[TL_LABEL_WIDTHS.length - 1];
    for (var k = 0; k < TL_LABEL_WIDTHS.length; k++) {
      put = timelineLanes(items, w, TL_LABEL_WIDTHS[k]);
      maxW = TL_LABEL_WIDTHS[k];
      var used = Math.max.apply(null, put.map(function (p) { return p.lane; })) + 1;
      if (used <= TL_MAX_LANES) { break; }
    }
    var top = 0;
    entries.forEach(function (e, i) {
      e.node.style.maxWidth = maxW + 'px';
      e.node.style.left = put[i].left + 'px';
      e.node.style.top = (put[i].lane * TL_LANE_H) + 'px';
      top = Math.max(top, put[i].lane);
    });
    lanes.style.height = ((top + 1) * TL_LANE_H) + 'px';
    return true;
  };
  if (!place()) { setTimeout(place, 0); }
}

/* 時間軸の札から、その証拠のカードへ送る。時間軸は索引であって、
   読むものはカードの側にある。**何も新しいことは言わない** */
function focusEvidence(id) {
  S.openIds[id] = true;
  var past = $('past-evidence');
  var inResult = document.querySelector('#result-area [data-ev="' + id + '"]');
  if (!inResult && past) { past.open = true; }
  renderEvidence(S.view);
  var box = document.querySelector('[data-ev="' + id + '"].ev');
  if (!box) { return; }
  box.classList.add('open');
  // **カードの頭を、追従している帯の下に置く。** 真ん中に寄せると、
  // 開いたカードは長いので [ev_004] の見出しが帯の上へ抜ける —
  // どれを開いたのか分からないまま生ログだけが出る
  var css = getComputedStyle(document.documentElement);
  var off = (parseInt(css.getPropertyValue('--header-h'), 10) || 66)
          + (parseInt(css.getPropertyValue('--strip-h'), 10) || 140) + 10;
  window.scrollTo(0, Math.max(0,
    window.pageYOffset + box.getBoundingClientRect().top - off));
}

/* 資産盤。プレイ中ずっと出しておく（SPEC 7.6.16）。

   **hard では出さない。** 盤は自分の行動を1枚に消化して見せる装置で、
   消化された見せ方は hard が奪う側にある（SPEC 3.10）。依存関係そのものは
   被疑判定モーダルに文字で残るので、世界の事実は取り上げていない。
   畳んだ／開いたは <details> 自身が覚えるので、再描画で戻らない。

   **既定は畳む。** 開いたままだと図が 300px 強を占め、押した結果の結論
   （「分かったこと N 件」「何も出てこなかった。」）が画面の下へ落ちる。
   そうなると画面に残るのは端末の数字だけになり、当たりも外れも同じ顔に
   見える。畳んでも見出しの一行（boardTally）は出したままなので、
   「手を当てた資産 3 / 6」が読めなくなることはない。 */
function renderBoard(v) {
  var slot = $('play-board');
  clear(slot);
  slot.hidden = !v.show_topology;
  if (!v.show_topology) { return; }
  var b = boardBox(v.assets);
  b.box.open = !!S.boardOpen;
  b.box.addEventListener('toggle', function () { S.boardOpen = b.box.open; });
  slot.appendChild(b.box);
  // 画面に入れてから描く。非表示のままだと clientWidth が 0 になる
  renderTopology(b.host, v.assets, { marks: true });
}

/* いま何段目か。凡例と本体で同じ関数から描く（SPEC 7.6.7）。
   手で書いた見本は必ず腐る — 3フェーズを2フェーズに変えたとき、
   凡例だけが3段のまま残り、初見の学習者に「何か飛ばしたのか」と思わせた */
function phaseBar(phases) {
  var ol = el('ol', 'phase-bar');
  (phases || []).forEach(function (p) {
    var li = el('li');
    li.appendChild(el('span', 'p' + (p.current ? ' now' : (p.done ? ' done' : '')), p.label));
    ol.appendChild(li);
  });
  return ol;
}

function renderPhaseBar(v) {
  var host = $('play-phase');
  var next = phaseBar(v.phases);
  next.id = host.id;
  host.parentNode.replaceChild(next, host);
}

function renderEvidence(v) {
  renderRespondFrame(v);
  renderIncoming();
  renderResult(v);
  renderPast(v);
}

/* 対応フェーズの枠組み。何を訊かれているのかを、決める場所に置く。
   被疑判定と封じ込めを分けているのがこの設計の要なので、
   分けている理由をその場で言う（SPEC 2.6）。 */
/* 実行の記録。$ で始まる行を立たせて、打った側と返った側を分ける。
   CSS では行の中身で選べないので、行ごとに包む。 */
function termBody(text) {
  var pre = el('pre', 'term-body');
  text.replace(/\s+$/, '').split('\n').forEach(function (line, i) {
    if (i) { pre.appendChild(document.createTextNode('\n')); }
    var isCmd = /^\s*\$/.test(line);
    pre.appendChild(el('span', isCmd ? 'cmd' : 'out', line));
  });
  return pre;
}

function terminal(o) {
  var term = el('div', 'term' + (o.running ? ' running' : ''));
  var bar = el('div', 'term-bar');
  bar.appendChild(el('span', 'dots'));
  bar.appendChild(el('span', 'term-title', o.label));
  term.appendChild(bar);
  // 押した直後はまだ手元に無い。枠だけ先に出して、返ってきたら中身が入る
  if (o.command) { term.appendChild(termBody(o.command)); }
  if (o.running) {
    term.appendChild(el('div', 'term-run', '実行中…'));
  } else if (o.cost) {
    term.appendChild(el('div', 'term-done', '── 仮想時計を ' + o.cost + '分 進めた ──'));
  }
  return term;
}

function renderRespondFrame(v) {
  var host = $('respond-frame');
  var phase = v.phases.filter(function (p) { return p.current; })[0];
  if (!phase || !phase.requires_assessment) { host.hidden = true; return; }
  host.hidden = false;
  clear(host);

  host.appendChild(el('h3', null, '対応を決める'));

  var r1 = el('div', 'respond-row');
  r1.appendChild(el('div', 'k', 'あなたの判定'));
  var v1 = el('div', 'v');
  if (v.current_assessment.length) {
    v.current_assessment.forEach(function (id) { v1.appendChild(el('span', 'chip', id)); });
  } else {
    v1.appendChild(el('span', 'dim', '侵害されている資産はないと判断'));
  }
  var edit = el('button', null, '変更');
  edit.addEventListener('click', openAssessment);
  v1.appendChild(edit);
  r1.appendChild(v1);
  host.appendChild(r1);

  var r2 = el('div', 'respond-row');
  r2.appendChild(el('div', 'k', '方針'));
  r2.appendChild(el('div', 'v', v.policy_label));
  host.appendChild(r2);

  var note = el('div', 'respond-note');
  note.appendChild(el('div', null,
    '侵害されていると判断したものを、' +
    '必ず止めなければならないわけではありません。'));
  note.appendChild(el('div', null,
    '何をどこまで止めるかは、上の方針が決めます。' +
    '判定していない資産を止めることも、止めずに終えることもできます。'));
  host.appendChild(note);
}

/* 押した結果の見出し。凡例と本体で同じ関数から描く（SPEC 7.6.7） */
function outcomeHead(type, label, costText) {
  var h = el('div', 'outcome-act');
  h.appendChild(el('span', 'outcome-tag', ACT_TAG[type] || ACT_TAG.investigate));
  h.appendChild(el('span', 'outcome-name', label));
  if (costText) { h.appendChild(el('span', 'outcome-cost', costText)); }
  return h;
}

/* 押した結果。「押した → こうなった」を1つの箱にまとめ、全幅で出す */
/* 向こうから入ってきたこと（SPEC 5.10 / 7.6.8）。

   押した結果の箱には入れない。証拠の間に挟まると、
   読み手はそこを「結果の続き」として読み飛ばす。
   新着は開いたまま、前の分は一行に畳んで残す。消さないのは、
   圧力がどう積み上がったかを後から辿れるようにするため。 */
function renderIncoming() {
  var feed = $('incoming-feed');
  var host = $('incoming-list');
  clear(host);
  if (!S.incoming.length) { feed.hidden = true; return; }
  feed.hidden = false;
  $('incoming-count').textContent = '（' + S.incoming.length + '件）';

  // 新しい順。今さっき来たものが一番上
  var items = S.incoming.slice().reverse();
  items.forEach(function (ev) {
    var box = el('div', 'incoming'
      + (ev.fresh ? ' fresh' : '') + (ev.averted ? ' averted' : ''));
    var h = el('div', 'incoming-head');
    if (ev.averted) { h.appendChild(el('span', 'incoming-tag', '未然')); }
    else if (ev.fresh) { h.appendChild(el('span', 'incoming-tag', '新着')); }
    h.appendChild(el('span', 'incoming-label', ev.label));
    h.appendChild(el('span', 'incoming-at', elapsed(ev.at_minutes)));
    if (ev.fresh) {
      box.appendChild(h);
      prose(box.appendChild(el('div', 'incoming-body')), ev.text);
    } else {
      // 済んだものは一行。押せば読み返せる。
      // 記号は証拠カードと同じもの（SPEC 7.6.15）。hover の色だけを頼りにすると、
      // 盤面が変わったという唯一の証拠が、次の一手で読めなくなる
      var det = el('details', 'incoming-old');
      var sum = el('summary');
      var mark = caret(false);
      h.appendChild(mark);
      sum.appendChild(h);
      det.appendChild(sum);
      det.addEventListener('toggle', function () {
        mark.textContent = caret(det.open).textContent;
      });
      prose(det.appendChild(el('div', 'incoming-body')), ev.text);
      box.appendChild(det);
    }
    host.appendChild(box);
  });
}

function renderResult(v) {
  var host = $('result-area');
  clear(host);

  var o = S.lastOutcome;
  var fresh = v.obtained_evidence.filter(function (ev) { return S.newIds[ev.id]; });

  if (o && o.running) {
    // まだ結果が返っていない。走っているところだけを出す
    var box = el('div', 'outcome');
    box.appendChild(outcomeHead(o.type, o.label, '実行中…'));
    box.appendChild(terminal(o));
    host.appendChild(box);
    return;
  }

  if (!o) {
    if (!v.obtained_evidence.length) {
      host.appendChild(el('p', 'empty',
        'まだ何も分かっていない。下のアクションから選んで情報を集める。'));
    }
    return;
  }

  var stopped = (o.contained || []).length;
  var purged = o.eradicated || [];
  var halted = o.halted || [];
  var already = o.already || [];
  // 連絡は証拠を産まないが、空振りではない。世界のふるまいが変わっている。
  // 既に止めてある資産をもう一度止めた回も空振りではない（同じ資産に
  // 手が2つあるので普通に起きる）。元の状態に戻した回も同じで、
  // 攻撃の経路が既に断ってあれば contained は空になる。
  // エンジン側 ActionOutcome.empty と揃える（片方だけ直すと、
  // 緑の枠の中にオレンジの「何も出てこなかった」が並ぶ）
  // 連絡は決して空振りにしない。周知は出したのだから、世界の側の変化は
  // 押した時点で確定している。`prevented` が空になるのは「もう防ぐものが
  // 残っていない」ときだけで、そこで「何も出てこなかった」と出すのは
  // **間に合わなかったことをプレイ中に告げる**ことになる（原則5）
  var notice = (o.type === 'communicate');
  // 保全の手（仕掛け）も決して空振りにしない。産む証拠が無いのが
  // 仕掛けの定義なので、ここを見落とすと 20分かけて
  // 「何も出てこなかった」と返る。しかも既に奪われていた回だけ
  // 文言が変わると、それが「間に合わなかった」の合図になる。
  // エンジン側 ActionOutcome.empty と揃える（片方だけ直すと、
  // 緑の枠の中にオレンジの「何も出てこなかった」が並ぶ）
  var preserve = !!o.preserved;
  var empty = !notice && !preserve && !o.revealed && !stopped && !purged.length
              && !halted.length && !already.length && !o.prevented;
  var note = el('div', 'outcome'
    + (notice || preserve || stopped || purged.length || halted.length
       || already.length || o.prevented
        ? ' outcome-contained'
                              : (empty ? ' outcome-empty' : '')));

  note.appendChild(outcomeHead(o.type, o.label,
    o.cost ? (o.running ? '実行中…' : o.cost + '分を使った') : null));

  // 何をしたのかを、道具立てごと見せる。実環境は作らないが、
  // 手を動かした感触までは渡す（SPEC 1.4 / 7.6.8）
  if (o.command) { note.appendChild(terminal(o)); }

  // 連絡は証拠を産まない。何をしたのかを言わないと、押しても無言になる。
  // **文言は「間に合った回」と「間に合わなかった回」で揃える。**
  // 分けると、その場で賭けの結果を告げることになる（SPEC 5.6.1 / 原則5）
  if (notice) {
    note.appendChild(el('div', 'outcome-msg notified',
      '周知が行き渡った。以後、現場判断で状態が変わることは無くなる。'));
    note.appendChild(el('div', 'outcome-cascade',
      '既に起きてしまったことには戻らない。効くのはここから先だけ。'));
  }

  // 仕掛けの手。**何を守ったかは言わない**（それは答えの側）。
  // 言うのは機構だけで、文言は「間に合った回」と「間に合わなかった回」で
  // 揃える（連絡と同じ理由。SPEC 5.6.3 / 原則5）
  if (preserve) {
    note.appendChild(el('div', 'outcome-msg notified',
      '取得して保全した。以後、現場で端末が触られても、手元の中身は変わらない。'));
    note.appendChild(el('div', 'outcome-cascade',
      '既に起きてしまったことには戻らない。効くのはここから先だけ。'));
  }

  if (stopped) {
    note.appendChild(el('div', 'outcome-msg stopped',
      o.contained.length + ' 件を止めた。'));
    var row = el('div', 'outcome-cascade');
    o.contained.forEach(function (a) {
      row.appendChild(el('span', 'chip', a.id + '　' + a.label));
    });
    note.appendChild(row);
  }
  // 止めることと、元の状態に戻すことは別の作業なので、別の行で言う。
  // **何が残っていたかは言わない。** それは講評まで開かない側の話で、
  // ここで言えば「まだ残っている」の予告になる（原則5）
  if (purged.length) {
    note.appendChild(el('div', 'outcome-msg stopped',
      purged.length + ' 件を元の状態に戻した。'));
    var prow = el('div', 'outcome-cascade');
    purged.forEach(function (a) {
      prow.appendChild(el('span', 'chip', a.id + '　' + a.label));
    });
    note.appendChild(prow);
  }
  // 既に止めてある資産に2手目を打った回。無言にすると
  // 「何も出てこなかった」と同じ見た目になり、押した意味が消える
  if (!stopped && !purged.length && already.length) {
    note.appendChild(el('div', 'outcome-msg stopped',
      already.length + ' 件は既に止まっている。'));
    var arow = el('div', 'outcome-cascade');
    already.forEach(function (a) {
      arow.appendChild(el('span', 'chip', a.id + '　' + a.label));
    });
    note.appendChild(arow);
  }
  // 業務が止まったことは、止めたことと別に告げる。
  // 同じ「止める」でも、通信だけを断つ手は仕事を止めない（SPEC 6.3）
  if (stopped || purged.length || halted.length || already.length) {
    if (halted.length) {
      note.appendChild(el('div', 'outcome-msg sub',
        '業務が止まったのは ' + halted.length + ' 件。'));
      var hrow = el('div', 'outcome-cascade');
      halted.forEach(function (a) {
        hrow.appendChild(el('span', 'chip' + (a.cascaded ? ' cascaded' : ''),
          a.id + '　' + a.label));
      });
      note.appendChild(hrow);
      var casc = halted.filter(function (a) { return a.cascaded; });
      if (casc.length) {
        note.appendChild(el('div', 'outcome-cascade',
          '橙は依存によって一緒に止まったものです（' + casc.length + ' 件）。'));
      }
    } else if (o.haltsBusiness) {
      // 止める手ではあるが、その資産の業務は既に止まっていた
      note.appendChild(el('div', 'outcome-cascade',
        '業務は既に止まっている。'));
    } else {
      note.appendChild(el('div', 'outcome-cascade',
        '資産は動いたままなので、業務は止まっていない。'));
    }
    if (o.impact) {
      note.appendChild(el('div', 'outcome-msg sub',
        '業務影響が ' + Math.round(o.impact).toLocaleString('ja-JP') + ' 増えた。'));
    }
  }
  if (empty) {
    note.appendChild(el('div', 'outcome-msg gone', '何も出てこなかった。'));
  }
  if (fresh.length) {
    note.appendChild(el('div', 'outcome-msg found', '分かったこと ' + fresh.length + ' 件'));
    var box = el('div', 'outcome-body');
    fresh.forEach(function (ev) { box.appendChild(evidenceCard(ev, true, true)); });
    note.appendChild(box);
  }
  if (o.unlocked) {
    // 何が開いたかは言わない。増えたことだけ告げて、一覧を見に行かせる
    note.appendChild(el('div', 'outcome-msg sub',
      '新たに調べられることが ' + o.unlocked + ' 件増えた。'));
  }
  host.appendChild(note);
}

/* 読み返すための証拠。今回の結果とは分ける */
function renderPast(v) {
  var wrap = $('past-evidence');
  var host = $('evidence-list');
  clear(host);

  var past = v.obtained_evidence.filter(function (ev) { return !S.newIds[ev.id]; });
  wrap.hidden = !past.length;
  if (!past.length) { return; }

  $('past-summary').textContent = 'これまでに取得した証拠　' + past.length + ' 件';
  past.slice().reverse().forEach(function (ev) {
    host.appendChild(evidenceCard(ev, false));
  });
}

function evidenceCard(ev, isNew, forceOpen) {
  var box = el('div', 'ev' + (isNew ? ' new' : ''));
  // 事件の時計の札から辿れるようにする（索引 → 本体）。属性だけで、
  // 中身は何も変えない
  if (ev.id) { box.setAttribute('data-ev', ev.id); }
  // 結果の箱の中は開く。積み上がった側は畳んで一覧として読ませる。
  // 凡例の見本は本物の開閉状態を引き継がない（畳んだ姿が見本だから）
  var open = forceOpen || (!ev.sample && !!S.openIds[ev.id]);
  if (open) { box.classList.add('open'); }

  var head = el('div', 'ev-head');
  head.appendChild(el('span', 'ev-id', '[' + ev.id + ']'));
  head.appendChild(el('span', 'ev-sum', ev.summary || ''));
  head.appendChild(el('span', 'ev-at', elapsed(ev.at_minute)));
  var mark = caret(open);
  head.appendChild(mark);
  head.addEventListener('click', function () {
    var nowOpen = box.classList.toggle('open');
    // 凡例の見本は記憶しない。本物の ev_001 が勝手に開いてしまう
    if (!ev.sample) { S.openIds[ev.id] = nowOpen; }
    mark.textContent = caret(nowOpen).textContent;
  });
  box.appendChild(head);

  var body = el('div', 'ev-body');
  body.appendChild(el('pre', null, ev.content));  // textContent + pre-wrap
  if (ev.reading) {
    var r = el('div', 'ev-reading');
    r.appendChild(el('div', 'ev-reading-head', '読み方'));
    prose(r.appendChild(el('div', 'ev-reading-body')), ev.reading);
    body.appendChild(r);
  }
  // 考えられること。読み方の**後ろ**に置く。生ログ → 読み方 → 意味しうること
  // の順でないと、まだ読めていないものの意味を先に渡すことになる。
  // 箇条書きにするのは、散文にすると順番と接続詞が本命を作るため
  // **既定では畳む。** 毎回読むものではなく「詰まったときに考えを広げる
  // 道具」であり、開いたままだと押した直後の視野から結果が落ちる
  // （実測 674px 下。押した直後に一度も目に入っていなかった）
  if (ev.possibilities && ev.possibilities.length) {
    var m = el('details', 'ev-reading ev-maybe');
    var sm = el('summary');
    var mk = caret(false);
    sm.appendChild(mk);
    sm.appendChild(el('span', 'ev-reading-head',
      '考えられること　' + ev.possibilities.length + ' 通り'));
    m.appendChild(sm);
    m.addEventListener('toggle', function () {
      mk.textContent = caret(m.open).textContent;
    });
    var ul = el('ul', 'ev-maybe-list');
    ev.possibilities.forEach(function (t) {
      ul.appendChild(el('li', null, String(t).trim().replace(/\s*\n\s*/g, '')));
    });
    m.appendChild(ul);
    body.appendChild(m);
  }
  box.appendChild(body);
  return box;
}

function renderQuestions(v) {
  var panel = $('questions-panel');
  // open_questions が null のレベルではパネルごと出さない
  if (!v.open_questions) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  var host = $('questions-list');
  clear(host);
  v.open_questions.forEach(function (q) {
    var row = el('div', 'q' + (q.resolved === true ? ' done' : ''));
    row.appendChild(el('span', 'mark', q.resolved === true ? '○' : '●'));
    var body = el('div');
    body.appendChild(el('div', 'label', q.label));
    body.appendChild(el('div', 'text', q.question));
    row.appendChild(body);
    host.appendChild(row);
  });
}

/* アクションの見た目。凡例の見本と本物で同じものを使う */
function actionCard(a) {
  var btn = el('button', 'act' + (a.type === 'contain' ? ' contain' : ''));
  var head = el('span', 'act-head');
  head.appendChild(el('span', 'act-label', a.label));
  head.appendChild(el('span', 'cost', a.cost_minutes + '分'));
  btn.appendChild(head);
  if (a.description) {
    // 短い散文なので、YAML 上の改行は畳んで列幅に合わせて流す
    btn.appendChild(el('span', 'act-desc', a.description.trim().replace(/\n+/g, ' ')));
  }
  btn.disabled = !a.selectable;
  return btn;
}

function phaseGroupHead(label, remain, total, closed) {
  var head = el('button', 'act-group-head');
  head.appendChild(caret(!closed));
  head.appendChild(el('span', null, label));
  head.appendChild(el('span', 'count', '未実行 ' + remain + ' / ' + total));
  return head;
}

function renderActions(v) {
  var host = $('actions-list');
  clear(host);

  // 作者が付けた分類で束ねる。18個をただ並べると、探せない
  var order = [];
  var groups = {};
  v.available_actions.forEach(function (a) {
    var key = a.group || a.phase_label;
    if (!groups[key]) { groups[key] = []; order.push(key); }
    groups[key].push(a);
  });

  order.forEach(function (key) {
    var list = groups[key];
    // 未実行が残っている束は開く。やり尽くした束は畳んで場所を空ける
    var remain = list.filter(function (a) { return a.selectable; }).length;
    var closed = S.closedPhases[key];
    if (closed === undefined) { closed = (remain === 0); }

    var head = phaseGroupHead(key, remain, list.length, closed);
    head.addEventListener('click', function () {
      S.closedPhases[key] = !closed;
      renderActions(S.view);
    });
    host.appendChild(head);
    if (closed) { return; }

    list.forEach(function (a) {
      var btn = actionCard(a);
      if (a.selectable) {
        btn.addEventListener('click', function () {
          btn.disabled = true;
          // command は押す前には持っていない。端末の記録は
          // decide のレスポンスで届く（SPEC 7.6.8）。
          // 実行中の箱は見出しと所要だけで出す
          S.lastOutcome = {
            label: a.label, cost: a.cost_minutes, type: a.type,
            command: '', running: true,
            revealed: 0, unlocked: 0, contained: [], eradicated: [],
            halted: [], already: [],
            haltsBusiness: false, impact: 0, prevented: 0, preserved: false
          };
          // 走らせている間もその場で見せる。押した瞬間に何か起きる
          renderResult(S.view);
          scrollAfterAction();
          decide({ kind: 'action', action_id: a.id });
        });
      }
      host.appendChild(btn);
    });
  });
}

/* ── モーダル ── */

function openAdvance() {
  var v = S.view;
  if (v.next_phase_requires_assessment) { openAssessment(); return; }
  var next = null;
  for (var i = 0; i < v.phases.length; i++) {
    if (v.phases[i].current) { next = v.phases[i + 1]; break; }
  }
  $('advance-title').textContent = v.advance_label + '？';
  $('advance-detail').textContent = next
    ? '「' + v.current_phase_label + '」から「' + next.label + '」へ進みます。'
    : '';
  openModal('modal-advance');
}

function openAssessment() {
  var v = S.view;
  var host = $('assess-list');
  clear(host);
  var picked = {};
  v.current_assessment.forEach(function (id) { picked[id] = true; });

  v.assets.forEach(function (a) {
    var lab = el('label', 'pick');
    var input = el('input');
    input.type = 'checkbox';
    input.value = a.id;
    input.checked = !!picked[a.id];
    lab.appendChild(input);
    lab.appendChild(el('span', 'id', a.id));
    lab.appendChild(el('span', null, a.label));
    lab.appendChild(el('span', 'crit', '停止影響 ' + critLabel(a.criticality)));
    // 図を出さないレベルでは、依存先を文字で添える。
    // 依存関係まで隠すと、構成を知らない人だけが上流を止めて事故る
    if (!v.show_topology && a.depends_on.length) {
      lab.appendChild(el('span', 'dep', '← ' + a.depends_on.join(', ') + ' に依存'));
    }
    host.appendChild(lab);
  });

  $('assess-criterion').hidden = !v.show_primer;

  var warn = $('assess-warn');
  clear(warn);
  // 論点を列挙できるか、件数だけか、何も出さないかはビューが決める
  if (v.open_questions && v.unresolved_count) {
    var unresolved = v.open_questions.filter(function (q) { return q.resolved === false; });
    if (unresolved.length) {
      warn.appendChild(el('div', null, '⚠ 以下の論点が未解消のままです'));
      var ul = el('ul');
      unresolved.forEach(function (q) { ul.appendChild(el('li', null, q.label)); });
      warn.appendChild(ul);
    } else {
      warn.appendChild(el('div', null,
        '⚠ 未解消の論点が ' + v.unresolved_count + ' 件あります'));
    }
  } else if (v.unresolved_count) {
    warn.appendChild(el('div', null,
      '⚠ 未解消の論点が ' + v.unresolved_count + ' 件あります'));
  }
  openModal('modal-assessment');
}

function openFinish() {
  var v = S.view;
  var host = $('finish-summary');
  clear(host);
  function row(k, val) {
    host.appendChild(el('div', 'k', k));
    host.appendChild(el('div', 'v', val));
  }
  row('経過時間', elapsed(v.elapsed_minutes));
  row('累積被害', num(v.accumulated_damage));
  row('業務影響', num(v.accumulated_business_impact));
  row('被疑判定', v.current_assessment.length ? v.current_assessment.join(', ') : '（未宣言）');
  openModal('modal-finish');
}

/* ── ⑤ 講評 ── */

function loadReport() {
  api('/api/session/' + S.sessionId + '/report').then(function (rep) {
    S.report = rep;
    renderDebrief(rep);
    show('screen-debrief');
  }).catch(fail);
}

function renderDebrief(rep) {
  $('debrief-sub').textContent =
    rep.scenario_title + '　方針: ' + rep.score.policy_label + '　アシスト: ' + rep.assist_level;

  var host = $('debrief-body');
  clear(host);
  host.appendChild(blockScore(rep));
  host.appendChild(blockUnresolved(rep));
  var lost = blockLost(rep);
  if (lost) { host.appendChild(lost); }
  var cf = blockCounterfactuals(rep);
  if (cf) { host.appendChild(cf); }
  host.appendChild(blockPolicy(rep));
  host.appendChild(blockTruth(rep));
  var rec = blockRecords(rep);
  if (rec) { host.appendChild(rec); }
}

function blockScore(rep) {
  var b = el('div', 'block');
  var s = rep.score;
  b.appendChild(el('h2', null, '① スコア'));

  var t = el('div', 'total', String(Math.round(s.composite_score * 100)));
  t.appendChild(el('small', null, ' / 100'));
  b.appendChild(t);

  // 合成は「重みで幅を分けた1本の帯」で見せる（SPEC 7.6.13）。
  // 事実認識は帯の 70%、方針適合は 30% を占め、それぞれの中で自分の点だけ塗る。
  // **塗った長さの合計が総合点になる。** 同じ長さの独立した2本を並べていた頃は、
  // 総合がどこから出た数字なのか画面のどこにも書かれていなかった
  var fw = s.fact_weight;
  var weighted = (s.composite_method === 'weighted_sum');
  var segs = weighted
    ? [['事実認識', s.fact_score, fw], ['方針適合', s.policy_score, 1 - fw]]
    : [['事実認識', s.fact_score, 0.5], ['方針適合', s.policy_score, 0.5]];
  var comp = el('div', 'composite');
  segs.forEach(function (seg) {
    var wrap = el('div', 'cseg');
    wrap.style.width = (seg[2] * 100) + '%';
    var track = el('div', 'ctrack');
    var fill = el('div', 'cfill');
    fill.style.width = Math.max(0, Math.min(100, seg[1] * 100)) + '%';
    track.appendChild(fill);
    wrap.appendChild(track);
    var lab = el('div', 'clab');
    lab.appendChild(el('span', 'n', String(Math.round(seg[1] * 100))));
    lab.appendChild(el('span', 'k', ' ' + seg[0]));
    if (weighted) { lab.appendChild(el('span', 'w', '×' + seg[2].toFixed(2))); }
    wrap.appendChild(lab);
    comp.appendChild(wrap);
  });
  b.appendChild(comp);
  b.appendChild(el('p', 'faint', weighted
    ? '帯の幅が重み、塗った長さがその層の取り分です。合計が総合点になります。'
    : '総合は2つの層の積です。'));

  // 採点はこの2本だけである。プレイ中に最も大きく動いた数字がここに無い理由を、
  // 講評でも言っておく（SPEC 6.1 / 7.6.15）
  b.appendChild(el('p', 'faint',
    '帰結（経過時間・被害額・業務影響）は採点していません。' +
    '方針の重みを通してのみ評価に入ります。'));

  b.appendChild(factTable(rep));

  if (s.fact_score < 0.5) {
    var note = el('div', 'note-warn');
    note.appendChild(el('p', null,
      '事実認識が低い状態では、方針適合の高さは実務上の意味を持ちません。' +
      '無関係な資産を丁寧に隔離しても、被害は止まりません。'));
    b.appendChild(note);
  }
  return b;
}

/* 事実認識の内訳。**出すのは向きを揃えた「良さ」だけ。**

   以前は生値（0.000〜1.000）を6つ並べていた。適合率は高い方が良く、
   見落としは低い方が良いのに、体裁が同じなので区別する手がかりが無かった。
   「1.000」が1件なのか 100% なのかも読めなかった。

   代わりに、何を何で割った値かを分数のまま出す。説明文が要らなくなる。 */
function factTable(rep) {
  var s = rep.score;
  var wrap = el('div', 'sub');
  wrap.appendChild(el('h3', null,
    '事実認識 ' + Math.round(s.fact_score * 100) + ' の内訳'));
  wrap.appendChild(el('p', 'faint',
    'どれも 100 に近いほど良い向きに揃えてあります。'));

  var tbl = el('table', 'mtable');
  Object.keys(s.goodness).forEach(function (id) {
    var tr = el('tr');
    tr.appendChild(el('td', 'k', rep.metric_labels[id] || id));

    var parts = (s.metric_parts && s.metric_parts[id]) || [];
    var td = el('td', 'frac');
    parts.forEach(function (p) {
      td.appendChild(el('div', null,
        p.label + ' ' + Math.round(p.num) + ' / ' + p.den_label + ' ' +
        Math.round(p.den)));
    });
    tr.appendChild(td);

    tr.appendChild(el('td', 'num', String(Math.round(s.goodness[id] * 100))));
    tr.appendChild(el('td', 'w', '×' + (s.fact_weights[id] || 0).toFixed(2)));
    tbl.appendChild(tr);
  });
  wrap.appendChild(tbl);
  return wrap;
}

function blockUnresolved(rep) {
  var b = el('div', 'block');
  b.appendChild(el('h2', null, '② 判定時点の論点'));

  var all = rep.retrospective.unresolved_review;
  var scored = all.filter(function (u) { return u.critical; });
  var rest = all.filter(function (u) { return !u.critical; });

  // **論点は同格ではない。** 以前は4件を一列に並べ、1件だけに
  // 「（採点対象外）」と括弧を付けていた。なぜ1件だけ違うのかは
  // プレイ中も講評も一度も説明していなかった（SPEC 3.5）
  group(b, '採点対象の論点', scored,
        '未解消のまま対応すると、封じ込めそのものが成立しなくなる論点です。');
  group(b, '採点対象外の論点', rest,
        '解けていなくても封じ込めは成立します。' +
        '実務では別の判断（報告義務など）に要るため、論点としては残ります。');

  if (rest.length) {
    // なぜプレイ中に見せないのか。見せない判断そのものを開示する（SPEC 3.10）
    b.appendChild(el('p', 'faint',
      'プレイ中は、どれが採点対象かを出していません。' +
      'どの論点が重いかは「どこを調べなくてよいか」の目印になり、' +
      '盤面が自分の罠に印を付けることになるからです。'));
  }
  return b;
}

function group(host, title, items, note) {
  if (!items.length) { return; }
  host.appendChild(el('h3', null, title + '（' + items.length + '件）'));
  host.appendChild(el('p', 'faint', note));
  items.forEach(function (u) {
    var item = el('div', 'item' + (u.resolved_at_decision ? ' done' : ''));
    var h = el('div', 'h');
    h.appendChild(el('span', 'mark', u.resolved_at_decision ? '○' : '●'));
    h.appendChild(el('span', null, u.label + ' — ' + u.question));
    item.appendChild(h);
    if (!u.resolved_at_decision) {
      prose(item.appendChild(el('div', 'imp')), u.implication);
      if (u.resolved_later) {
        item.appendChild(el('div', 'imp faint',
          '※ 対応フェーズ中に解消していますが、判断を下した時点では未解消でした。'));
      }
    }
    host.appendChild(item);
  });
}

function blockLost(rep) {
  if (!rep.lost_evidence.length) { return null; }
  var b = el('div', 'block');
  b.appendChild(el('h2', null, '取り損ねた証拠'));
  b.appendChild(el('p', 'dim',
    '次の情報は、取得する前に失われました。プレイ中は何も表示していません — ' +
    '見ていないものが失われたことは、そもそも分からないからです。'));
  rep.lost_evidence.forEach(function (l) {
    var note = el('div', 'note-warn');
    note.appendChild(el('p', null, '[' + l.id + '] ' + l.summary));
    // 自分の手で壊したのか、こちらが動く前に世界の側が奪ったのかを区別する。
    // 同じ「失った」でも、次に活かせる教訓が違う
    note.appendChild(el('p', 'dim',
      l.by_world
        ? elapsed(l.at_minute) + ' の時点で「' + l.destroyed_by + '」により、'
          + 'こちらが取りに行く前に失われました。'
          + (l.obtainable_by.length
              ? 'それまでに「' + l.obtainable_by.join('」「') + '」を実行していれば取れました。'
              : '')
        : '「' + l.destroyed_by + '」で失われました。'
          + (l.obtainable_by.length
              ? '先に「' + l.obtainable_by.join('」「') + '」を実行していれば取れました。'
              : '')));
    // 空振りした手の側から書く。**本人の記憶にあるのはこちらである。**
    // 「30分払って何も出てこなかった」と「1時間25分前に奪われていた」が
    // 同じ出来事だという接続が、講評のどこにも書かれていなかった
    (l.too_late || []).forEach(function (t) {
      note.appendChild(el('p', 'late', t.kind === 'notice'
        ? elapsed(t.at_minute) + ' に出した「' + t.action_label +
          '」は、この連絡を止められる手でした。' + duration(t.late_by_minutes) +
          '遅く、' + t.cost_minutes + '分を払って何も変わりませんでした。'
        : elapsed(t.at_minute) + ' に押した「' + t.action_label +
          '」がこれを取りに行く手でした。' + duration(t.late_by_minutes) +
          '遅く、' + t.cost_minutes + '分を払って何も出てきませんでした。'));
    });
    b.appendChild(note);
  });
  return b;
}

/* 長さ（「1時間25分」）。時刻ではないので elapsed の「+」は付けない。
   分だけで言うと、遅れの大きさが体感と結び付かない */
function duration(minutes) {
  var h = Math.floor(minutes / 60), m = minutes % 60;
  if (!h) { return m + '分'; }
  return h + '時間' + (m ? m + '分' : '');
}

function blockCounterfactuals(rep) {
  var list = rep.retrospective.counterfactuals;
  var chased = rep.retrospective.misled_follow_minutes || 0;
  if (!list.length && !chased) { return null; }
  var b = el('div', 'block');
  b.appendChild(el('h2', null, '③ 誤導の棄却経路'));

  /* 追いかけた時間は**採点していない**。外れを引くのは調査の必然であって、
     罰する対象ではない（SPEC 6.6）。それでも、どこに時間を落としたかは
     本人にしか返せない情報なので、平文の1行として置く。
     v1.36 まではこれを総調査時間で割って減点していたが、
     分母が本人の裁量で伸びるので、よく調べた人ほど寄り道が薄まっていた */
  if (chased) {
    var total = rep.retrospective.investigation_minutes || 0;
    b.appendChild(el('p', 'faint',
      '誤導証拠が指す先を追いかけた時間は ' + duration(chased) + 'です' +
      (total ? '（調べた時間の合計は ' + duration(total) + '）' : '') +
      '。これは採点していません。外れを引くこと自体は調査の一部です。'));
  }
  if (!list.length) {
    b.appendChild(el('p', 'dim',
      '誤導証拠だけを根拠にした名指しも、誤導に隠れた取りこぼしもありませんでした。'));
    return b;
  }
  list.forEach(function (cf) {
    var note = el('div', 'note-bad');
    /* **向きは画面が決めない**（cf.direction が持っている）。
       誤導には2つの向きがある — 疑わせて名指しさせる側と、
       白と読ませて名指しから落とさせる側で、言うことが正反対になる。
       ここを「名指ししたかどうか」で画面が判定すると、
       同じ誤導の説明が2つの文面に割れる（SPEC 3.4 / 6.6） */
    if (cf.direction === 'missed') {
      note.appendChild(el('p', null,
        '⚠ ' + cf.asset_id + ' を名指ししませんでした。手元にあった ' +
        cf.evidence_id + '（' + cf.evidence_summary + '）が、' +
        'この資産については何も出てこなかったと読める所見でした。'));
    } else {
      note.appendChild(el('p', null,
        '⚠ ' + cf.evidence_id + '（' + cf.evidence_summary + '）を根拠に ' +
        cf.asset_id + ' を被疑と判定しました。'));
    }
    if (cf.explanation) { prose(note.appendChild(el('div')), cf.explanation, 'dim'); }
    /* 棄却の条件は誤導ごとに違う（refutation_mode）。**足りたかどうかは
       画面が判定しない** — cf.refuted が答えを持っている。未取得が
       残っているかで画面が決めていると、候補が2つある any の誤導
       （1つ持てば足りる）に「これだけでは足りません」と出てしまう */
    var every = cf.refutation_mode === 'all';
    /* 向きで動詞が変わる。名指しした側は「棄却」、名指ししなかった側は
       白いという読みを「覆す」ことになる */
    var undo = cf.direction === 'missed' ? '読みを覆せました' : '棄却できました';
    if (cf.refuted) {
      note.appendChild(el('p', null,
        '→ ' + cf.refuting_obtained.join(', ') + ' は取得済みでした。' +
        (cf.direction === 'missed'
          ? 'この資産を名指しに戻す材料は手元にありました。'
          : '棄却の材料は手元にありました。')));
    } else if (cf.refuting_evidence.length) {
      if (cf.refuting_obtained.length) {
        note.appendChild(el('p', null,
          '→ ' + cf.refuting_obtained.join(', ') +
          ' は取得済みでしたが、これだけでは棄却に足りませんでした。'));
      }
      /* 「すべて」と言ってよいのは、残りが2件以上あるときだけ。
         1件しか足りていない人に「すべて取得していれば」と言うと、
         何件足りないのかが読めなくなる */
      var tail = cf.refuting_evidence.length > 1 && every ? ' をすべて取得していれば'
        : cf.refuting_obtained.length ? ' も取得していれば'
        : ' を取得していれば';
      note.appendChild(el('p', null,
        '→ ' + cf.refuting_evidence.join(', ') + tail + undo + '。' +
        (cf.obtainable_by.length ? '（' + cf.obtainable_by.join(' / ') + '）' : '')));
    }
    b.appendChild(note);
  });
  return b;
}

function blockPolicy(rep) {
  var b = el('div', 'block');
  var sc = rep.score;
  b.appendChild(el('h2', null,
    '④ 方針への適合　' + Math.round(sc.policy_score * 100)));

  // **違反していないのに点が低い理由は、ここにしか無い。**
  // 方針適合は「帰結の加重和 ×（1 - 減衰×違反数）」であって、
  // 違反ゼロでも加重和が低ければ低い。以前は「方針違反はありませんでした」の
  // 一行だけで、残りの 61点がどこへ消えたのか画面のどこにも書いていなかった
  var sum = 0;
  rep.policy_terms.forEach(function (t) { sum += t.contribution; });

  b.appendChild(el('p', 'faint',
    '「' + sc.policy_label + '」が見ているものと、その重みです。' +
    '良さは、この盤面で到達しうる範囲を 0〜100 に直したものです。'));

  var tbl = el('table', 'mtable');
  rep.policy_terms.forEach(function (t) {
    var tr = el('tr');
    tr.appendChild(el('td', 'k', t.label));
    var v = el('td', 'frac');
    v.appendChild(el('div', null, t.display));
    if (t.band) { v.appendChild(el('div', 'band', t.band)); }
    tr.appendChild(v);
    tr.appendChild(el('td', 'num', String(Math.round(t.goodness * 100))));
    tr.appendChild(el('td', 'w', '×' + t.weight.toFixed(2)));
    tr.appendChild(el('td', 'num strong', (t.contribution * 100).toFixed(1)));
    tbl.appendChild(tr);
  });
  var last = el('tr', 'sumrow');
  last.appendChild(el('td', 'k', '合計'));
  last.appendChild(el('td', 'frac', ''));
  last.appendChild(el('td', 'num', ''));
  last.appendChild(el('td', 'w', ''));
  last.appendChild(el('td', 'num strong', (sum * 100).toFixed(1)));
  tbl.appendChild(last);
  b.appendChild(tbl);

  if (!rep.violations.length) {
    b.appendChild(el('p', 'dim', '方針違反はありませんでした。'));
  } else {
    var pen = Math.round(sc.constraint_penalty * 100);
    b.appendChild(el('p', null,
      '方針違反 ' + rep.violations.length + '件　→　合計 ' +
      (sum * 100).toFixed(1) + ' から ' + rep.violations.length + ' × ' + pen +
      '% を引いて ' + Math.round(sc.policy_score * 100)));
    rep.violations.forEach(function (v) {
      var note = el('div', 'note-warn');
      note.appendChild(el('p', null, '「' + v.message + '」'));
      note.appendChild(el('p', 'dim',
        '→ ' + v.action_label + '（' + v.at_minute + '分時点）'));
      b.appendChild(note);
    });
  }
  return b;
}

function blockTruth(rep) {
  var b = el('div', 'block');
  b.appendChild(el('h2', null, '⑤ 真相と参照値'));

  if (rep.damage_history && rep.damage_history.length > 1) {
    var canvas = el('canvas');
    canvas.setAttribute('height', '190');
    b.appendChild(canvas);

    // 被害は二段で出す。プレイ中に見えていたのは実線の分だけで、
    // 破線の分は**手を止めた時点の封じ込め状態**が決めている。
    // ここを1つの数字に丸めると、封じ込めの巧拙が数字から消える
    var cq = rep.score.consequences;
    var two = el('p', 'damage-split');
    two.appendChild(el('span', 'k', '累積被害 '));
    two.appendChild(el('span', 'v', num(cq.accumulated_damage)));
    two.appendChild(el('span', 'k', '　／　復旧までの見込み '));
    two.appendChild(el('span', 'v proj', num(cq.projected_damage)));
    two.appendChild(el('span', 'k', '　＝　合計 '));
    two.appendChild(el('span', 'v', num(cq.total_damage)));
    b.appendChild(two);
    b.appendChild(el('p', 'faint',
      '破線は、あなたが手を止めた時点の封じ込め状態がそのまま続いた場合の' +
      '伸びです（復旧まで ' + Math.round(rep.recovery_horizon_minutes / 60) +
      '時間と置いています）。演習が終わってもインシデントは終わりません。' +
      'プレイ中に出していたのは実線の部分だけです。'));

    // 凡例は番号で図と結ぶ。**色では結ばない。**
    // 6項目すべて同じオレンジのダッシュを並べていた頃は、どの線がどれかを
    // x 座標の近さで推測するしかなかった（同時刻に2本立つ場合は不可能）
    var legend = el('div', 'legend legend-num');
    rep.markers.forEach(function (m) {
      var world = m.kind === 'world';
      var item = el('span', world ? 'lg-world' : null,
                    ' ' + m.label + '（' + m.minute + '分）');
      var sw = el('span', 'sw-num', String(m.index));
      sw.style.color = world ? '#f7768e' : '#e0af68';
      sw.style.borderColor = world ? '#f7768e' : '#e0af68';
      item.insertBefore(sw, item.firstChild);
      legend.appendChild(item);
    });
    var dash = el('span', 'lg-world', ' 復旧までの見込み（唯一の破線）');
    var dsw = el('span', 'sw dashed');
    dash.insertBefore(dsw, dash.firstChild);
    legend.appendChild(dash);
    b.appendChild(legend);
    // 描画は DOM 挿入後（clientWidth が要る）
    setTimeout(function () {
      DamageChart.draw(canvas, rep.damage_history, {
        projection: rep.damage_projection,
        axis: true,
        // 図に描くのは番号だけ。名前は凡例に出す
        markers: rep.markers.map(function (m) {
          return { minute: m.minute, index: m.index, kind: m.kind };
        })
      });
    }, 0);
  }

  if (rep.truth) {
    prose(b.appendChild(el('div', 'narrative')), rep.truth.attack_narrative);
    var g = el('div', 'summary-grid');
    g.appendChild(el('div', 'k', '実際に侵害されていた資産'));
    g.appendChild(el('div', 'v', rep.truth.compromised.join(', ')));
    g.appendChild(el('div', 'k', '無関係だった資産'));
    g.appendChild(el('div', 'v', rep.truth.innocent.join(', ') || '（なし）'));
    g.appendChild(el('div', 'k', '誤導だった証拠'));
    g.appendChild(el('div', 'v', rep.truth.misleading_evidence.join(', ')));
    b.appendChild(g);
  }

  var sp = blockSpread(rep);
  if (sp) { b.appendChild(sp); }

  var cr = blockContainment(rep);
  if (cr) { b.appendChild(cr); }

  var mp = rep.retrospective.minimal_path;
  if (mp && mp.minutes !== null) {
    b.appendChild(el('p', null,
      mp.label + ': ' + mp.minutes + '分　/　あなたの経過時間: ' +
      rep.score.consequences.elapsed_minutes + '分'));
    if (mp.disclaimer) { prose(b.appendChild(el('div')), mp.disclaimer, 'faint'); }
  }

  if (rep.key_lessons.length) {
    b.appendChild(el('h3', null, 'この演習で扱ったこと'));
    var ul = el('ul', 'lessons');
    rep.key_lessons.forEach(function (t) { ul.appendChild(el('li', null, t)); });
    b.appendChild(ul);
  }
  return b;
}

// 真実が動いたことの開示（SPEC 5.2 / 7.6.13）。
// **プレイ中は一言も告げない。** 配られたことは学習者に見えていないので、
// その場で言えば損失の予告になる（原則5）。ここが唯一の開示の場である。
//
// 数字を2つ並べる理由: 事実認識層は判定時点の広さで、封じ込めの完全度は
// 終了時点の広さで採点する。言わないと、再現率 100% と完全度 67% が
// 同じ画面に並んでいる理由がどこにも書かれていないことになる。
function blockSpread(rep) {
  var s = rep.spread;
  if (!s || !s.steps.length) { return null; }
  var moved = s.at_finish > s.at_decision;

  var box = el('div', moved ? 'note-warn' : 'note');
  box.appendChild(el('h3', null, '調べている間に、盤面がどう動いたか'));

  var g = el('div', 'summary-grid');
  g.appendChild(el('div', 'k', 'あなたが判定した時点（' + s.decided_at_minute + '分）'));
  g.appendChild(el('div', 'v', s.at_decision + '台　' + s.decision_labels.join('、')));
  g.appendChild(el('div', 'k', '演習を終えた時点'));
  g.appendChild(el('div', 'v', s.at_finish + '台　' + s.finish_labels.join('、')));
  box.appendChild(g);

  s.steps.forEach(function (st) {
    var line = el('p', st.happened ? null : 'dim');
    if (st.happened) {
      line.appendChild(document.createTextNode(
        st.at_minute + '分、' + st.label + 'へ配られました。' +
        st.source_labels.join('、') + 'を止めてあれば、この配信は走っていません。' +
        (st.stopped_at === null
          ? '演習の終わりまで止まっていませんでした。'
          : '止め終わったのは ' + st.stopped_at + '分で、' +
            (st.stopped_at - st.at_minute) + '分 遅れていました。')));
    } else {
      line.appendChild(document.createTextNode(
        st.at_minute + '分の配信は走りませんでした。' +
        st.source_labels.join('、') + 'が既に止まっていたためです。'));
    }
    box.appendChild(line);
  });

  if (moved) {
    box.appendChild(el('p', 'faint',
      '被疑判定は、宣言した時点の盤面と突き合わせて採点しています。' +
      'そのときの名指しが正しかったかと、最後まで止めきれたかは別の問いです。'));
  }
  return box;
}

// 封じ込めの答え合わせ（SPEC 5.8 / 7.6.13）。
// **ここが唯一の開示の場である。** プレイ中は「止めた」としか言わない —
// 何が残ったままかは学習者に見えていないので、その場で告げれば
// 損失の予告になる（原則5）。
//
// 図の破線がどの傾きで伸びているかを決めているのは、止めたかどうかと
// **取り除いたかどうか**の両方である。完全度 100% でも傾きが緩まないことが
// あり、それを言葉にしないと「正しく止めたのに、なぜまだ伸びるのか」で終わる。
function blockContainment(rep) {
  var c = rep.containment;
  if (!c) { return null; }

  var box = el('div', c.verdict === 'correct' ? 'note' : 'note-warn');
  box.appendChild(el('h3', null, '止めたもの／取り除いたもの'));

  var g = el('div', 'summary-grid');
  g.appendChild(el('div', 'k', '攻撃の経路を断てた'));
  g.appendChild(el('div', 'v', c.stopped.join('、') || '（なし）'));
  if (c.missed.length) {
    g.appendChild(el('div', 'k', '止められなかった'));
    g.appendChild(el('div', 'v', c.missed.join('、')));
  }
  g.appendChild(el('div', 'k', '残されたものを取り除けた'));
  g.appendChild(el('div', 'v', c.eradicated.join('、') || '（なし）'));
  if (c.still_persistent.length) {
    g.appendChild(el('div', 'k', '取り除かないまま終わった'));
    g.appendChild(el('div', 'v', c.still_persistent.join('、')));
  }
  box.appendChild(g);

  if (c.still_persistent.length) {
    var times = c.best_factor ? (c.factor / c.best_factor) : 0;
    box.appendChild(el('p', null,
      c.still_persistent.join('、') + ' には、攻撃者が置いていったものが' +
      'そのまま残っています。通信を止めても、その端末を業務に戻せば' +
      '同じところから戻ってきます。'));
    box.appendChild(el('p', null,
      'そのため、図の破線（復旧までの見込み）は取り除けた場合の約 ' +
      times.toFixed(1) + ' 倍の傾きで伸び続けています。' +
      '演習を終えた時点の状態がそのまま続く、という前提で引いた線です。'));
    if (c.eradicable_by.length) {
      box.appendChild(el('p', 'dim',
        '盤面には取り除ける手がありました: ' +
        c.eradicable_by.map(function (x) { return '「' + x + '」'; }).join('、')));
    }
  } else if (c.verdict === 'correct') {
    box.appendChild(el('p', null,
      '経路を断ち、置いていかれたものも取り除けています。' +
      '図の破線は、この盤面で引ける最も緩やかな傾きです。'));
  }
  return box;
}

function blockRecords(rep) {
  var b = el('div', 'block');
  b.appendChild(el('h2', null, '⑥ このシナリオの記録'));

  if (rep.records_total <= 1) {
    b.appendChild(el('p', 'dim',
      '別の方針で解くと、同じ行動でも評価が変わります。' +
      'アシストを減らすと、事実認識の点が落ちるはずです。'));
    rep.replay_suggestions.forEach(function (s) {
      var note = el('div', 'note-warn');
      note.appendChild(el('p', null, '「' + s.policy_label + '」で再挑戦'));
      prose(note.appendChild(el('div')), s.hint, 'dim');
      b.appendChild(note);
    });
    return b;
  }

  // 条件（方針 × アシスト）ごとに1行。ここが比較したい軸そのもの
  var table = el('table', 'cond');
  var thead = el('tr');
  ['方針', 'アシスト', '事実認識', '方針適合', '総合', '回数', ''].forEach(function (h) {
    thead.appendChild(el('th', null, h));
  });
  table.appendChild(thead);

  rep.record_groups.forEach(function (g) {
    var tr = el('tr', g.is_current ? 'here' : null);
    tr.appendChild(el('td', null, g.policy_label));
    tr.appendChild(el('td', null, g.assist_level));
    tr.appendChild(el('td', 'num', g.fact_score));
    tr.appendChild(el('td', 'num', g.policy_score));
    tr.appendChild(el('td', 'num strong', g.composite_score));
    tr.appendChild(el('td', 'num faint', g.plays > 1 ? g.plays + '回' : ''));
    var tag = el('td', 'tag');
    if (g.has_first_play) { tag.appendChild(el('span', 'first', '★初回')); }
    if (g.is_current) { tag.appendChild(el('span', 'now', '今回の条件')); }
    if (g.stale) { tag.appendChild(el('span', 'faint', '別バージョンを含む')); }
    tr.appendChild(tag);
    table.appendChild(tr);
  });
  b.appendChild(table);

  b.appendChild(el('p', 'faint',
    '各行はその条件での最高スコアです。' +
    '※ ★ 以外は答えを知った状態でのプレイです。' +
    '事実認識スコアの上昇は学習効果とは限りません。'));

  /* 2周目以降に狙う層を1行で言う（SPEC 3.11 / 7.6.13）。
     **数字を主語にしない。** 文面はサーバが作る — 画面で組み立てられる形に
     しておくと、いつか「あと6点」と書きたくなる */
  if (rep.next_target) {
    var aim = el('div', 'aim');
    aim.appendChild(el('p', 'aim-head', rep.next_target.headline));
    aim.appendChild(el('p', 'aim-note', rep.next_target.note));
    b.appendChild(aim);
  }

  // まだ試していない条件を名指しする。空白のほうが誘いになる
  var tried = {};
  rep.record_groups.forEach(function (g) { tried[g.policy_id + '/' + g.assist_level] = true; });
  var todo = [];
  rep.policies.forEach(function (p) {
    ['assisted', 'standard', 'hard'].forEach(function (lv) {
      if (!tried[p.id + '/' + lv]) { todo.push(p.label + ' × ' + lv); }
    });
  });
  if (todo.length) {
    b.appendChild(el('p', 'dim', 'まだ試していない条件: ' + todo.slice(0, 4).join('、')
      + (todo.length > 4 ? ' ほか' : '')));
  }

  // 履歴そのものは畳んでおく。読み返すためのもので、締めではない
  if (rep.records.length) {
    var det = el('details', 'history');
    var sum = el('summary', null,
      '1回ずつの記録を見る（' + rep.records_total + '件'
      + (rep.records_total > rep.records.length
          ? '中 直近 ' + rep.records.length + '件' : '') + '）');
    det.appendChild(sum);
    var log = el('table', 'log');
    var lh = el('tr');
    ['日時', '方針', 'アシスト', '事実認識', '方針適合', '総合', ''].forEach(function (h) {
      lh.appendChild(el('th', null, h));
    });
    log.appendChild(lh);
    rep.records.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', 'faint', stamp(r.played_at)));
      tr.appendChild(el('td', null, r.policy_label));
      tr.appendChild(el('td', null, r.assist_level));
      tr.appendChild(el('td', 'num', r.fact_score));
      tr.appendChild(el('td', 'num', r.policy_score));
      tr.appendChild(el('td', 'num', r.composite_score));
      tr.appendChild(el('td', 'tag', r.is_first_play ? '★初回' : ''));
      log.appendChild(tr);
    });
    det.appendChild(log);
    b.appendChild(det);
  }
  return b;
}

function stamp(iso) {
  var d = new Date(iso);
  if (isNaN(d)) { return ''; }
  function p(n) { return (n < 10 ? '0' : '') + n; }
  return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

/* ── 配線 ── */

function init() {
  $('btn-to-select').addEventListener('click', function () { toSelect(null); });
  $('btn-what').addEventListener('click', function () { openModal('modal-what'); });
  $('btn-back-top').addEventListener('click', function () { show('screen-top'); });
  $('btn-start').addEventListener('click', startSession);
  $('btn-begin').addEventListener('click', function () {
    S.pendingFinish = false;
    /* ブリーフィングを閉じた合図だけを送る（SPEC 7.5.4）。
       **盤面は1つも動かない。** これを送らないと、最初の1手の
       「考えていた秒」にブリーフィングを読んでいた時間が丸ごと混ざる。
       **失敗しても遊びは止めない** — 足あとは採点に効かないので、
       ここで転んで開始できなくなるほうが害が大きい */
    api('/api/session/' + S.sessionId + '/begin', { method: 'POST' })
      .catch(function () {});
    renderPlay();
  });

  $('btn-advance').addEventListener('click', openAdvance);
  $('btn-advance-confirm').addEventListener('click', function () {
    closeModal('modal-advance');
    decide({ kind: 'advance_phase' });
  });

  $('btn-show-assessment').addEventListener('click', openAssessment);
  $('btn-assess-confirm').addEventListener('click', function () {
    var picked = [];
    var boxes = $('assess-list').querySelectorAll('input[type=checkbox]');
    /* 空の判定も許す。判断の放棄も1つの判断であり、採点できる（SPEC 6.7） */
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].checked) { picked.push(boxes[i].value); }
    }
    closeModal('modal-assessment');
    decide({ kind: 'declare_assessment', assessment: picked });
  });

  $('btn-finish').addEventListener('click', function () {
    if (S.view.next_phase_requires_assessment || !S.view.current_assessment.length) {
      // 被疑判定がないと事実認識層が採点できない。先に宣言してもらう
      S.pendingFinish = true;
      openAssessment();
      return;
    }
    openFinish();
  });
  $('btn-finish-confirm').addEventListener('click', function () {
    closeModal('modal-finish');
    decide({ kind: 'finish' });
  });

  // 凡例はプレイ中も開ける。●/○ の意味を思い出したいのは、開始前ではなく最中である
  $('btn-show-legend').addEventListener('click', function () {
    openModal('modal-legend');
    // 表示してから描く。非表示のままだと canvas の幅が 0 になる
    renderLegend(S.view, $('legend-mini'), $('legend-notes'));
  });

  $('btn-what-play').addEventListener('click', function () { openModal('modal-what'); });

  /* 画面の見方の見本は、**畳みを開いたときに初めて組む。**
     閉じた `<details>` の中は幅が 0 で、そこでグラフや盤を描くと
     潰れたまま残る（SPEC 7.6.9 — dpr 1 だけの検証で素通りした不具合と同じ形）。
     一度組んだら組み直さない。組み直す必要があるのは演習が変わったときで、
     そのときは `S.legendDrawn` が false に戻る */
  $('brief-legend-fold').addEventListener('toggle', function () {
    if (!this.open || S.legendDrawn || !S.legendView) { return; }
    S.legendDrawn = true;
    renderLegend(S.legendView, $('brief-mini'), $('brief-legend'));
  });

  $('btn-abort').addEventListener('click', function () { openModal('modal-abort'); });
  $('btn-abort-confirm').addEventListener('click', function () {
    /* 途中でやめる（SPEC 7.6.25）。**記録は残らない** —
       記録は `finish` を押した回にしか作られないので、
       何もしないことが「残さない」になる。
       サーバ側のセッションだけ捨てる。**失敗しても帰る** —
       やめたい人を、後片付けの失敗で引き止めない。 */
    var sid = S.sessionId;
    closeModal('modal-abort');
    S.sessionId = null;
    show('screen-top');
    if (sid) {
      api('/api/session/' + sid, { method: 'DELETE' }).catch(function () {});
    }
  });

  $('btn-show-policy').addEventListener('click', function () {
    $('policy-modal-name').textContent = '【対応方針】' + S.view.policy_label;
    prose($('policy-modal-body'), S.view.policy_briefing);
    openModal('modal-policy');
  });

  $('btn-replay').addEventListener('click', function () {
    S.sessionId = null;
    S.report = null;
    show('screen-select');
    loadScenarios().catch(fail);
  });

  var closers = document.querySelectorAll('[data-close]');
  for (var i = 0; i < closers.length; i++) {
    (function (btn) {
      btn.addEventListener('click', function () {
        S.pendingFinish = false;
        closeModal(btn.getAttribute('data-close'));
      });
    })(closers[i]);
  }

  window.addEventListener('resize', function () {
    if (S.view && S.view.damage_history) {
      DamageChart.draw($('damage-chart'), S.view.damage_history, {});
    }
    // 段の割り方は幅で決まる。幅が変わったら割り直す
    if (S.view) { renderTimeline($('play-timeline'), S.view); }
  });
}

document.addEventListener('DOMContentLoaded', function () {
  init();
  // 入口が「何が遊べるか」と「何を主張しているか」を自分で言えるように、
  // どちらも最初に取りに行く。どちらも失敗しても画面は開いたままにする
  loadScenarios().catch(function () {});
  loadPolicySwap();
  loadTopMeta();
});
