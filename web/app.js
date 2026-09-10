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

/* ── ② シナリオ選択 ── */

function loadScenarios() {
  return api('/api/scenarios').then(function (list) {
    S.scenarios = list;
    renderScenarioList();
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
  // 一覧が無い状態で開始させない
  clear($('policy-list'));
  clear($('assist-list'));
  $('btn-start').disabled = true;
}

function renderScenarioList() {
  var host = $('scenario-list');
  clear(host);
  $('btn-start').disabled = false;
  if (!S.scenarios.length) {
    host.appendChild(el('p', 'empty', 'scenarios/ にシナリオがありません。'));
    $('btn-start').disabled = true;
    return;
  }
  S.scenarios.forEach(function (sc) {
    var card = el('div', 'card');
    var head = el('div', 'card-head');
    head.appendChild(el('div', 'card-title', sc.title));
    head.appendChild(el('div', 'dim',
      '複雑度 ' + '★'.repeat(sc.complexity) + '☆'.repeat(5 - sc.complexity)));
    card.appendChild(head);
    card.appendChild(el('div', 'faint',
      sc.tags.join(' / ') + '　約' + sc.estimated_play_minutes + '分'));
    host.appendChild(card);
  });
  selectScenario(S.scenarios[0]);
}

function selectScenario(sc) {
  S.scenario = sc;
  S.policy = S.policy || sc.default_policy;
  S.assist = S.assist || sc.default_assist_level;

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
    S.newIds = {};
    S.openIds = {};
    S.incoming = [];
    $('brief-title').textContent = data.title;
    prose($('brief-body'), data.briefing);
    $('brief-policy-name').textContent = '【今回の対応方針】' + data.policy_label;
    prose($('brief-policy-body'), data.policy_briefing);
    renderFlow(data.phases || []);
    renderLegend(data.view, $('brief-mini'), $('brief-legend'));
    // 考え方の枠組みを出すかはアシストレベルが決める（3.10）
    var primer = $('brief-primer');
    primer.hidden = !data.view.show_primer;
    primer.open = !!data.view.show_primer;
    // 構成図はアシスト。依存関係そのものは資産一覧に残る（3.10）
    var topo = data.view.show_topology;
    $('brief-topo').hidden = !topo;
    $('brief-topo-note').hidden = !topo;
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
  function fallout(id, seen) {
    var out = [];
    assets.forEach(function (a) {
      if (seen.indexOf(a.id) >= 0) { return; }
      if ((a.depends_on || []).indexOf(id) < 0) { return; }
      out = out.concat([a.id], fallout(a.id, seen.concat([id, a.id])));
    });
    return out;
  }
  var best = null, bestN = 0;
  assets.forEach(function (a) {
    var n = fallout(a.id, [a.id]).length;
    if (n > bestN) { best = a; bestN = n; }
  });
  if (!best) { return; }
  host.textContent = best.id + ' を止めれば、それに依存する'
    + bestN + '台も一緒に止まります。';
}

function renderTopology(host, assets) {
  clear(host);
  if (!assets || !assets.length) { return; }

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
  var GAP = 14, BH = 50, VGAP = 46, PAD = 10;
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
    root.appendChild(svg('rect', {
      'class': 'node ' + (CRIT_CLASS[a.criticality] || ''),
      x: p.x, y: p.y, width: BW, height: BH, rx: 5
    }));
    var id = svg('text', { 'class': 'id', x: p.x + 11, y: p.y + 21 });
    id.textContent = a.id;
    root.appendChild(id);
    var tag = svg('text', { 'class': 'crit-tag', x: p.x + BW - 11, y: p.y + 21,
                            'text-anchor': 'end' });
    tag.textContent = critLabel(a.criticality);
    root.appendChild(tag);
    var nm = svg('text', { 'class': 'name', x: p.x + 11, y: p.y + 38 });
    nm.textContent = a.label;      // シナリオ由来。textContent で入れる
    root.appendChild(nm);
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

  // ── 本体
  var cols = el('div', 'mini-cols');
  var left = el('div', 'mini-left');

  left.appendChild(region(phaseBar(view.phases),
    'いま何段目かを示します。進むのはあなたが宣言したときだけで、' +
    '一度進むと戻れません。'));

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

  // 描画は組み立ててから。非表示のままだと clientWidth が 0 になる
  if (cv) {
    setTimeout(function () {
      var s = [], v = 0;
      for (var i = 0; i < 90; i++) { v += 1 + Math.pow(i / 30, 3); s.push(v); }
      DamageChart.draw(cv, s, {});
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
    if (S.lastOutcome) {
      S.lastOutcome.revealed = (res.revealed_evidence || []).length;
      S.lastOutcome.unlocked = res.unlocked_count || 0;
      S.lastOutcome.contained = res.contained || [];
      S.lastOutcome.eradicated = res.eradicated || [];
      S.lastOutcome.halted = res.halted || [];
      S.lastOutcome.already = res.already || [];
      S.lastOutcome.haltsBusiness = !!res.halts_business;
      S.lastOutcome.impact = res.business_impact_delta || 0;
      S.lastOutcome.prevented = res.prevented_count || 0;
      S.lastOutcome.command = res.command || S.lastOutcome.command;
      S.lastOutcome.running = false;
    }
    S.incoming.forEach(function (ev) { ev.fresh = false; });
    (res.events || []).forEach(function (ev) {
      ev.fresh = true;
      S.incoming.push(ev);
    });
    if (res.finished) { loadReport(); return res; }
    renderPlay();
    if (S.lastOutcome) { window.scrollTo(0, 0); }
    if (S.pendingFinish && S.view.current_assessment.length) {
      S.pendingFinish = false;
      openFinish();
    }
    return res;
  }).catch(fail);
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
  $('btn-show-topo').style.display = v.show_topology ? '' : 'none';
  show('screen-play');

  // ヘッダと帯の高さを実測して追従位置に反映する。帯の高さはアシストレベルで
  // 変わる（hard ではグラフも論点も無い）ので、決め打ちにすると
  // 右カラムが帯の下に潜るか、無駄な隙間が空く
  var root = document.documentElement;
  root.style.setProperty('--header-h', Math.round(
    document.querySelector('#screen-play .bar').getBoundingClientRect().height) + 'px');
  root.style.setProperty('--strip-h', Math.round(
    document.querySelector('.strip').getBoundingClientRect().height) + 'px');

  // 描画は画面を表示してから。非表示のままだと clientWidth が 0 になる
  var chart = $('damage-chart');
  var wrapEl = chart.parentNode;
  wrapEl.style.display = v.damage_history ? 'flex' : 'none';
  if (v.damage_history) { DamageChart.draw(chart, v.damage_history, {}); }
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
  var empty = !o.revealed && !stopped && !purged.length
              && !halted.length && !already.length && !o.prevented;
  var note = el('div', 'outcome'
    + (stopped || purged.length || halted.length || already.length || o.prevented
        ? ' outcome-contained'
                              : (empty ? ' outcome-empty' : '')));

  note.appendChild(outcomeHead(o.type, o.label,
    o.cost ? (o.running ? '実行中…' : o.cost + '分を使った') : null));

  // 何をしたのかを、道具立てごと見せる。実環境は作らないが、
  // 手を動かした感触までは渡す（SPEC 1.4 / 7.6.8）
  if (o.command) { note.appendChild(terminal(o)); }

  // 連絡は証拠を産まない。何をしたのかを言わないと、押しても無言になる
  if (o.prevented) {
    note.appendChild(el('div', 'outcome-msg notified',
      '周知が行き渡った。以後、現場判断で状態が変わることは無くなる。'));
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
            haltsBusiness: false, impact: 0, prevented: 0
          };
          // 走らせている間もその場で見せる。押した瞬間に何か起きる
          renderResult(S.view);
          window.scrollTo(0, 0);
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
      '誤導証拠だけを根拠にした名指しはありませんでした。'));
    return b;
  }
  list.forEach(function (cf) {
    var note = el('div', 'note-bad');
    note.appendChild(el('p', null,
      '⚠ ' + cf.evidence_id + '（' + cf.evidence_summary + '）を根拠に ' +
      cf.asset_id + ' を被疑と判定しました。'));
    if (cf.explanation) { prose(note.appendChild(el('div')), cf.explanation, 'dim'); }
    /* 棄却の条件は誤導ごとに違う（refutation_mode）。**足りたかどうかは
       画面が判定しない** — cf.refuted が答えを持っている。未取得が
       残っているかで画面が決めていると、候補が2つある any の誤導
       （1つ持てば足りる）に「これだけでは足りません」と出てしまう */
    var every = cf.refutation_mode === 'all';
    if (cf.refuted) {
      note.appendChild(el('p', null,
        '→ ' + cf.refuting_obtained.join(', ') +
        ' は取得済みでした。棄却の材料は手元にありました。'));
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
        '→ ' + cf.refuting_evidence.join(', ') + tail + '棄却できました。' +
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
  $('btn-to-select').addEventListener('click', function () {
    show('screen-select');
    if (!S.scenarios.length) { loadScenarios().catch(fail); }
  });
  $('btn-what').addEventListener('click', function () { openModal('modal-what'); });
  $('btn-back-top').addEventListener('click', function () { show('screen-top'); });
  $('btn-start').addEventListener('click', startSession);
  $('btn-begin').addEventListener('click', function () {
    S.pendingFinish = false;
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

  $('btn-show-topo').addEventListener('click', function () {
    openModal('modal-topo');
    // 表示してから描く。非表示のままだと幅が 0 になる
    renderTopology($('play-topo'), S.view.assets);
  });

  // 凡例はプレイ中も開ける。●/○ の意味を思い出したいのは、開始前ではなく最中である
  $('btn-show-legend').addEventListener('click', function () {
    openModal('modal-legend');
    // 表示してから描く。非表示のままだと canvas の幅が 0 になる
    renderLegend(S.view, $('legend-mini'), $('legend-notes'));
  });

  $('btn-what-play').addEventListener('click', function () { openModal('modal-what'); });

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
  });
}

document.addEventListener('DOMContentLoaded', init);
