/* 実際に遊んで、画面を撮って、壊れているところを機械的に拾う。
 *
 *   node tools/playtest.mjs [--dpr 2] [--out ディレクトリ] [--profile 名前]
 *
 * 数字で決まること（描画エラー、はみ出し、canvas の暴走、アクションが
 * 画面外へ追い出されていないか）はここで落とす。分かりやすさや面白さは
 * 落とせないので、撮ったものを人／エージェントが見る前提で置いていく。
 *
 * canvas を含む画面は dpr 2 でも必ず確認すること（SPEC 7.6.9）。
 * dpr 1 だけの検証は、Retina で必ず起きる不具合を素通りさせる。
 */

import { chromium } from 'playwright-core';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const args = Object.fromEntries(
  process.argv.slice(2).join(' ').split('--').filter(Boolean)
    .map(s => s.trim().split(/\s+/)).map(([k, ...v]) => [k, v.join(' ') || true])
);
const DPR = Number(args.dpr || 2);
const OUT = args.out || '.playtest';
const BASE = args.base || 'http://127.0.0.1:8000';
const PROFILE = args.profile || 'wanderer';
const SCENARIO = args.scenario || '(一覧の先頭)';
// アシストレベル。**hard は画面の作りが一番変わる** — グラフも論点も資産盤も
// 出ないので、他のレベルで整っていても hard だけ崩れることがある
const ASSIST = args.assist || '(既定)';

mkdirSync(OUT, { recursive: true });

const findings = [];
const shots = [];
const note = (severity, where, what) => findings.push({ severity, where, what });

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 }, deviceScaleFactor: DPR,
});
page.on('pageerror', e => note('error', 'js', e.message));
page.on('console', m => { if (m.type() === 'error') note('error', 'console', m.text()); });
page.on('requestfailed', r => note('error', 'network', `${r.url()} ${r.failure()?.errorText}`));

const TAG = args.assist ? `${args.assist}.dpr${DPR}` : `dpr${DPR}`;

async function shot(name) {
  const path = join(OUT, `${name}.${TAG}.png`);
  await page.screenshot({ path, fullPage: false });
  shots.push(path);
  return path;
}

/** 横スクロールが出ていないか。本文が横に流れる画面は必ず読みにくい */
async function checkNoHorizontalOverflow(where) {
  const over = await page.evaluate(() => {
    const d = document.documentElement;
    if (d.scrollWidth <= d.clientWidth + 1) return null;
    const wide = [...document.querySelectorAll('body *')]
      .filter(el => el.getBoundingClientRect().right > d.clientWidth + 1)
      .slice(0, 3)
      .map(el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '.' + el.className));
    return { scrollWidth: d.scrollWidth, clientWidth: d.clientWidth, wide };
  });
  if (over) note('error', where, `横にはみ出している: ${JSON.stringify(over)}`);
}

/** canvas の実寸が再描画のたびに膨らんでいないか（SPEC 7.6.9 の落とし穴） */
async function checkCanvasStable(where) {
  const sel = 'canvas';
  if (!(await page.$(sel))) return;
  const read = () => page.$$eval(sel, cs => cs.map(c => `${c.getAttribute('height')}x${c.width}`));
  const first = await read();
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => window.dispatchEvent(new Event('resize')));
    await page.waitForTimeout(120);
  }
  const after = await read();
  if (JSON.stringify(first) !== JSON.stringify(after)) {
    note('error', where, `canvas の実寸が再描画で変わる: ${first} → ${after}（dpr ${DPR}）`);
  }
}

/** 結果を読んでいる間、アクションが画面から消えていないか */
async function checkActionsStayVisible(where) {
  const box = await page.locator('#actions-list').boundingBox().catch(() => null);
  if (!box) { note('error', where, 'アクション一覧が見つからない'); return; }
  const vh = page.viewportSize().height;
  if (box.y > vh) {
    note('error', where, `アクションが画面外へ追い出されている（y=${Math.round(box.y)} > ${vh}）`);
  } else if (box.y + Math.min(box.height, 200) > vh) {
    note('warn', where, `アクションがほぼ画面外（y=${Math.round(box.y)}, 高さ${Math.round(box.height)}）`);
  }
}

/** 押した結果の**結論**が、押した直後に視野へ入っているか
 *
 *  見出しが載ることと、結果が読めることは別である。周7 で資産盤を足したとき、
 *  確認したのは「盤と結果の見出しが載る」までで、その下にある結論
 *  （分かったこと N 件 / 何も出てこなかった）は 1440×800 でも 1440×1000 でも
 *  画面の外だった。**空振りした手で画面に残るのは端末の数字だけ**になり、
 *  初見には成果が出たように読める。
 *
 *  視点の戻り先は押した瞬間に決まるので、**押す前に**画面の高さを変えて呼ぶ。
 *  追従している帯の下に潜っているものも読めていないので、そちらも見る。 */
async function checkResultConclusionVisible(where) {
  const r = await page.evaluate(() => {
    const box = document.querySelector('#result-area .outcome');
    if (!box) return { miss: '結果の箱' };
    const pt = box.querySelector('.outcome-msg');
    if (!pt) return { miss: '結果の結論（.outcome-msg）' };
    const css = getComputedStyle(document.documentElement);
    const off = (parseInt(css.getPropertyValue('--header-h'), 10) || 0)
              + (parseInt(css.getPropertyValue('--strip-h'), 10) || 0);
    const b = pt.getBoundingClientRect();
    return {
      text: pt.textContent.replace(/\s+/g, ' ').trim().slice(0, 24),
      top: Math.round(b.top), bottom: Math.round(b.bottom),
      off: Math.round(off), vh: window.innerHeight,
    };
  });
  if (r.miss) { note('error', where, `${r.miss}が見つからない`); return; }
  if (r.bottom > r.vh || r.top < r.off) {
    note('error', where,
      `押した結果の結論「${r.text}」が視野に入っていない`
      + `（上端 ${r.top} / 下端 ${r.bottom} / 帯の下 ${r.off} / 画面 ${r.vh}）`);
  }
}

/** 生の markdown 記法が素通りしていないか（SPEC 7.6 / 過去の実バグ）
 *
 *  ログの抜粋は等幅の箱に入っていて、そこの `#` は markdown ではなく
 *  ログ自身のコメント記号である。散文の側だけを見る。 */
async function checkNoRawMarkdown(where) {
  const raw = await page.evaluate(() => {
    const mono = 'pre, code, .term, .termbody, [class*="content"], [class*="log"]';
    for (const el of document.querySelectorAll('p, li, h1, h2, h3, summary, td, .dim, .narrative')) {
      if (el.closest(mono)) continue;
      const hit = el.innerText.match(/\*\*[^*\n]{2,40}\*\*|(^|\n)#{1,3} \S/);
      if (hit) return { text: hit[0].trim(), where: el.className || el.tagName };
    }
    return null;
  });
  if (raw) note('error', where, `生の markdown が出ている: ${JSON.stringify(raw)}`);
}

/** 凡例が実画面と食い違っていないか（SPEC 7.6.7）
 *
 *  手で書いたモックは必ず腐る。実際に「初動トリアージ › 本格調査 › 封じ込め・対応」
 *  の3段が、2フェーズになった実画面と食い違ったまま残っていた。
 *  ブリーフィングで読んだ凡例と、その直後のプレイ画面を突き合わせる。 */
async function legendSample(root) {
  return page.evaluate((sel) => {
    const q = (s) => [...document.querySelectorAll(sel + ' ' + s)]
      .map(e => e.textContent.replace(/\s+/g, ' ').trim());
    return { phases: q('.phase-bar .p'), groups: q('.act-group-head'),
             statKeys: q('.strip-stats .k') };
  }, root);
}

/** 押して開けるものの記号が、画面の中で食い違っていないか（SPEC 7.6.15）
 *
 *  畳んだ連絡はシェブロンを消しており、hover の色変化しか手がかりが無かった。
 *  同じ画面の証拠カードには ▸ が付いていた。世界が動いている唯一の証拠が、
 *  次の一手で読めなくなる。
 *
 *  **見るのは語彙であって、状態ではない**（v1.48 で直した）。
 *  ここは長らく「画面上のすべての記号が同じ1文字であること」を要求して
 *  いたが、▸ と ▾ は**開いているかどうか**を言う記号なので、
 *  閉じた束と開いた束が同時に見えている画面では必ず両方出る。
 *  同梱2本でこれが通っていたのは、play-late の時点でたまたま
 *  **全部が実行済みで全部畳まれていた**からにすぎない。
 *  3本目は引き直せる手（`repeatable`）を持つので、その束だけが最後まで
 *  開いたままになり、盤面は何も壊れていないのに落ちた。
 *
 *  SPEC 7.6.15 が要求しているのは「記号は `caret(open)` 1箇所でしか
 *  作らない」— つまり**語彙が共有されていること**である。
 *  状態が揃っていることではない。 */
async function checkCaretsShared(where) {
  const bad = await page.evaluate(() => {
    const openers = [
      ['畳んだ連絡', '#incoming-list details.incoming-old > summary'],
      ['証拠カード', '#evidence-list .ev-head, #result-area .ev-head'],
      ['アクションの束', '#actions-list .act-group-head'],
      ['考えられること', '#result-area .ev-maybe > summary, #evidence-list .ev-maybe > summary'],
    ];
    const out = [];
    for (const [name, sel] of openers) {
      const nodes = [...document.querySelectorAll(sel)];
      if (!nodes.length) continue;
      const marks = new Set(nodes.map(
        n => (n.querySelector('.caret') || {}).textContent || 'なし'));
      out.push({ name, marks: [...marks] });
    }
    return out;
  });
  const VOCAB = ['▸', '▾'];     // caret() が作る2文字。ここ以外から出てはいけない
  const seen = new Set();
  for (const g of bad) {
    if (g.marks.includes('なし')) {
      note('error', where, `${g.name} に開く記号が無い`);
    }
    g.marks.forEach(m => { if (m !== 'なし') seen.add(m); });
  }
  const stray = [...seen].filter(m => !VOCAB.includes(m));
  if (stray.length) {
    note('error', where,
      `caret() が作らない記号が混ざっている: ${stray.join(' ')}`);
  }
}

/** 事件の時計（SPEC 7.6.17）
 *
 *  見るのは4つ。**どれも真実を使わない** — 時間軸は学習者が取った証拠の
 *  並べ直しであって、盤面の答えには触れない。
 *
 *  ①取った証拠のうち時刻を持つものが、実際に描かれているか
 *  ②並びが時刻順か（DOM の順序で見る。目が追う順序がこれである）
 *  ③**取っていない証拠が出ていないか**（出たらその場で答えを配っている）
 *  ④札が入れ物からはみ出していないか・同じ段で重なっていないか
 */
async function checkTimeline(where) {
  const t = await page.evaluate(() => {
    const tl = document.querySelector('#play-timeline');
    if (!tl) return { miss: '事件の時計' };
    const band = tl.querySelector('.tl-band');
    const items = [...tl.querySelectorAll('.tl-item')].map(e => ({
      ev: e.getAttribute('data-ev'),
      at: e.querySelector('b').textContent.trim(),
      left: Math.round(parseFloat(e.style.left) || 0),
      top: Math.round(parseFloat(e.style.top) || 0),
      w: Math.round(e.getBoundingClientRect().width),
    }));
    // 画面のどこかに出ている証拠カード＝学習者が取ったもの
    const held = [...document.querySelectorAll('#result-area .ev[data-ev], #evidence-list .ev[data-ev]')]
      .map(e => e.getAttribute('data-ev'));
    return {
      items, held, bandW: band ? Math.round(band.clientWidth) : 0,
      empty: !!tl.querySelector('.tl-empty'),
      note: (tl.querySelector('.tl-note') || {}).textContent || '',
    };
  });
  if (t.miss) { note('error', where, `${t.miss}が無い`); return; }
  if (!t.items.length) {
    if (!t.empty) note('error', where, '事件の時計が空でも、空だと言っていない');
    return;
  }
  const times = t.items.map(i => i.at);
  const sorted = [...times].sort();
  if (JSON.stringify(times) !== JSON.stringify(sorted)) {
    note('error', where, `事件の時計が時刻順に並んでいない: ${times.join(' ')}`);
  }
  const held = new Set(t.held);
  const ghost = t.items.filter(i => !held.has(i.ev)).map(i => i.ev);
  if (ghost.length) {
    note('error', where, `取っていない証拠が事件の時計に出ている: ${ghost.join(',')}`);
  }
  // 2つの時計を混ぜない。何の時刻かをその場で言っているか
  if (!t.note.includes('経過時間')) {
    note('error', where, '事件の時計が、帯の経過時間と別ものだと言っていない');
  }
  for (const i of t.items) {
    if (i.left < 0 || i.left + i.w > t.bandW + 1) {
      note('error', where,
        `事件の時計の札が枠からはみ出している（${i.at} left=${i.left} w=${i.w} / 枠 ${t.bandW}）`);
    }
  }
  const lanes = {};
  for (const i of t.items) {
    (lanes[i.top] = lanes[i.top] || []).push(i);
  }
  for (const [top, row] of Object.entries(lanes)) {
    row.sort((a, b) => a.left - b.left);
    for (let k = 1; k < row.length; k++) {
      if (row[k].left < row[k - 1].left + row[k - 1].w) {
        note('error', where,
          `事件の時計の札が同じ段で重なっている（${top}px: ${row[k - 1].at} と ${row[k].at}）`);
      }
    }
  }
}

/** 入口の検査（SPEC 7.6.5）
 *
 *  入口は「文章が5ブロック・図が0・下 250px が真っ黒」だった。
 *  見るのは4つ。**どれも入口が自分で持っていなければならないもの**である。
 *
 *   ①主張の図（方針の入れ替え表）が実際に描かれているか。
 *     ここが空になるのは生成物が古いときなので、**消えたら落とす** —
 *     `python tools/balance.py --emit-swap` を流し直せという合図
 *   ②各列に最高点の印が付いているか（印は数字から決まる。焼き付けない）
 *   ③入っている演習が、1回も押す前に見えているか
 *   ④画面に収まっているか・下がどれだけ空いているか
 */
async function checkTopScreen(where) {
  const m = await page.evaluate(() => {
    const top = document.getElementById('screen-top');
    const field = top.querySelector('.top-field');
    const btn = document.getElementById('btn-to-select');
    const r = btn.getBoundingClientRect();
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    let bottom = 0;
    for (const el of top.querySelectorAll('*')) {
      const b = el.getBoundingClientRect();
      if (b.width > 0 && b.height > 0) bottom = Math.max(bottom, b.bottom);
    }
    const head = [...top.querySelectorAll('table.swap-grid thead th')].slice(1);
    const cols = head.map((_, i) => [...top.querySelectorAll('table.swap-grid tbody tr')]
      .map(tr => tr.children[i + 1]).filter(td => td && td.classList.contains('best')).length);
    return {
      rows: top.querySelectorAll('table.swap-grid tbody tr').length,
      cols, heads: head.length,
      cards: [...top.querySelectorAll('#top-scenarios .card-title')]
        .map(e => e.textContent.trim()),
      // 練習をまとめた札が名乗っている本数（SPEC 7.6.27）。
      // 題とは別の欄にあるので、別に読む
      tutorCount: (() => {
        const c = top.querySelector('#top-scenarios [data-tutorial-group] .dim');
        return c ? c.textContent.trim() : null;
      })(),
      prose: top.querySelectorAll('.lede p').length,
      // 隅の帯（SPEC 7.6.18）。**中身は実データからしか来ない**ので、
      // ここでは「札の枚数と食い違っていないか」だけを見る
      meta: [...top.querySelectorAll('#top-meta span:not(.sep)')]
        .map(e => e.textContent.trim()),
      // 背後の格子が中身を覆っていないか。覆うと**ボタンが押せなくなる**
      fieldOnTop: !!field && hit !== btn && !btn.contains(hit),
      bottom: Math.round(bottom), vh: window.innerHeight,
      scrollH: document.documentElement.scrollHeight,
      clientH: document.documentElement.clientHeight,
    };
  });
  if (m.rows < 2 || m.heads < 2) {
    note('error', where,
      `入口に方針の入れ替え表が出ていない（${m.rows}行 × ${m.heads}列）。`
      + 'python tools/balance.py --emit-swap を流し直す');
  } else if (m.cols.some(n => n < 1)) {
    note('error', where, `最高点の印が付いていない列がある: ${JSON.stringify(m.cols)}`);
  }
  if (!m.cards.length) {
    note('error', where, '入口に演習が1つも並んでいない');
  }
  if (m.fieldOnTop) {
    note('error', where, '背後の格子が中身の前に出ている（ボタンが押せない）');
  }
  // 帯が「N SCENARIOS」と言うなら、その N は下に並んでいる**本番の**札の枚数。
  // **別の経路で数えた数を並べると、片方が落ちた日に矛盾が表に出る**
  //
  // 練習は札1枚にまとめてあるので、帯も別に数える（SPEC 7.6.27）。
  // 「M TUTORIALS」は、その札が名乗っている本数と合っていること
  const said = m.meta.find(t => / SCENARIOS$/.test(t));
  const realCards = m.cards.filter((x) => !x.includes('チュートリアル'));
  const tutorCard = m.cards.find((x) => x.includes('チュートリアル'));
  const saidTutor = m.meta.find(t => / TUTORIALS$/.test(t));
  if (tutorCard || saidTutor) {
    const onCard = (m.tutorCount || '').match(/(\d+)\s*本/);
    const inStrip = (saidTutor || '').match(/(\d+)/);
    if (!tutorCard || !saidTutor) {
      note('error', where,
        `練習の札と帯が片方しか無い: 札 ${tutorCard ? 'あり' : 'なし'} / 帯 ${saidTutor || 'なし'}`);
    } else if (!onCard || !inStrip || onCard[1] !== inStrip[1]) {
      note('error', where,
        `練習の本数が食い違っている: 札「${m.tutorCount}」/ 帯「${saidTutor}」`);
    }
  }
  if (m.meta.length && !said) {
    note('error', where, `隅の帯に演習の数が無い: ${JSON.stringify(m.meta)}`);
  } else if (said && parseInt(said, 10) !== realCards.length) {
    note('error', where,
      `隅の帯と札の枚数が食い違っている: 「${said}」だが本番の札は ${realCards.length} 枚`);
  }
  if (m.scrollH > m.clientH + 1) {
    note('error', where,
      `入口が画面に収まっていない（${m.scrollH} > ${m.clientH}）`);
  }
  const blank = (m.vh - m.bottom) / m.vh;
  if (blank > 0.25) {
    note('warn', where,
      `入口の下が ${Math.round(blank * 100)}% 空いている（中身は ${m.bottom}px まで）`);
  }
  return m;
}

/** 対角を歩く光が、本当に歩いているか（SPEC 7.6.18）
 *
 *  これは装飾ではなく**主張を運ぶ動き**である。各列の最高点が
 *  A → B → C と移ることがこの道具の主張で、そこを順に照らしている。
 *  止まっていたら主張が伝わらないし、全部同時に光っていたら
 *  「最善が1つずつ入れ替わる」と食い違う。
 *
 *  見るのは「どの升目が一番強く光っているか」が、1周のあいだに
 *  **列の数だけ違う場所を通るか**。CSS の値は見ない（値を見ると、
 *  明るさを調整しただけで落ちる検査になる）。
 */
async function checkTheLightWalks(where) {
  const walking = await page.$('#screen-top table.swap-grid.walk');
  if (!walking) return;              // 3列でない表では光らせない。正しい状態
  const cols = await page.$$eval('#screen-top table.swap-grid thead th',
    ths => ths.length - 1);
  const seen = new Set();
  const step = 420, span = 8000;     // 1周は 3列 × 2.2秒 = 6.6秒
  for (let t = 0; t < span; t += step) {
    const lit = await page.evaluate(() => {
      let best = null, top = -1;
      for (const td of document.querySelectorAll('#screen-top table.swap-grid td.best')) {
        const m = getComputedStyle(td).backgroundColor.match(/[\d.]+\)$/);
        const a = m ? parseFloat(m[0]) : 1;
        if (a > top) { top = a; best = td.getAttribute('data-swap'); }
      }
      return best;
    });
    if (lit !== null) seen.add(lit);
    await page.waitForTimeout(step);
  }
  if (seen.size < cols) {
    note('error', where,
      `対角を歩く光が全部の列を通らない（${cols}列のうち ${seen.size}箇所: `
      + `${[...seen].join(',')}）`);
  }
}

/** 動きを止めた人から、表の主張が消えていないか（SPEC 7.6.18）
 *
 *  `prefers-reduced-motion: reduce` は「動きを減らす」であって
 *  「情報を減らす」ではない。**各列の最高点が青いことを動きの中に置くと、
 *  止めた人には 9 個の同じ数字が並んでいるようにしか見えない。**
 *  止まった状態を1枚撮って、人／エージェントが見る側にも残す。
 */
async function checkStillLifeHoldsUp() {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 }, deviceScaleFactor: DPR,
    reducedMotion: 'reduce',
  });
  const still = await ctx.newPage();
  try {
    await still.goto(BASE, { waitUntil: 'networkidle' });
    await still.waitForSelector('#top-swap:not([hidden])', { timeout: 5000 });
    await still.waitForTimeout(300);
    const m = await still.evaluate(() => {
      const tds = [...document.querySelectorAll('#screen-top table.swap-grid td.best')];
      return {
        best: tds.length,
        plain: tds.filter(td => {
          const cs = getComputedStyle(td);
          // 比べる相手は**同じ行の最高点でない升目**。自分と比べても何も分からない
          const other = td.parentElement.querySelector('td:not(.best)');
          return cs.backgroundColor === 'rgba(0, 0, 0, 0)'
            && (!other || cs.color === getComputedStyle(other).color);
        }).length,
        running: tds.filter(td => td.getAnimations().some(a => a.playState === 'running')).length,
      };
    });
    if (!m.best) {
      note('error', 'top(reduced-motion)', '止めた状態で最高点の印が1つも無い');
    } else if (m.plain) {
      note('error', 'top(reduced-motion)',
        `止めると最高点が分からなくなる升目が ${m.plain} 個ある`);
    }
    if (m.running) {
      note('error', 'top(reduced-motion)',
        `reduce を指定しても ${m.running} 個が動き続けている`);
    }
    const path = join(OUT, `01c-top-reduced-motion.${TAG}.png`);
    await still.screenshot({ path });
    shots.push(path);
  } finally {
    await ctx.close();
  }
}

async function screen(name) {
  await checkNoHorizontalOverflow(name);
  await checkNoRawMarkdown(name);
  await shot(name);
}

// ── 遊ぶ ──────────────────────────────────────────

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForTimeout(250);
const top = await checkTopScreen('top(1440×1000)');
await screen('01-top');
await checkTheLightWalks('top(1440×1000)');
await checkStillLifeHoldsUp();

// **入口は一番狭いところで見る。** 1440 で通ったものが 1280×720 で
// 縦に溢れると、押すべきボタンが折り返しの下に落ちる
await page.setViewportSize({ width: 1280, height: 720 });
await page.waitForTimeout(200);
await checkTopScreen('top(1280×720)');
await shot('01b-top-1280x720');
await page.setViewportSize({ width: 1440, height: 1000 });
await page.waitForTimeout(200);

await page.click('#btn-to-select');
await page.waitForSelector('#scenario-list .card:not(.card-error)');
// 入口の札と選択画面の札は同じ関数から出ている。**別々に書いたら腐る**
//
// **練習は両方で1つにまとめてある**（SPEC 7.6.27）。入口は「チュートリアル」
// の1枚、選択画面は畳みの中の2枚。突き合わせるのは**本番の札どうし**で、
// 練習の側は「入口に1枚あり、畳みの中に2枚ある」で見る
const listed = await page.$$eval('#scenario-list .card-title',
  e => e.map(x => x.textContent.trim()));
const topReal = top.cards.filter((x) => !x.includes('チュートリアル'));
const topTutorial = top.cards.filter((x) => x.includes('チュートリアル'));
const inFold = await page.$$eval('#tutorial-list .card-title',
  e => e.map(x => x.textContent.trim()));
if (topTutorial.length !== (inFold.length ? 1 : 0)) {
  note('error', 'top',
    `練習の札が入口に ${topTutorial.length} 枚、畳みの中に ${inFold.length} 本`);
}
if (JSON.stringify(listed) !== JSON.stringify(topReal)) {
  note('error', 'top', `入口の本番の一覧が選択画面と食い違っている: `
    + `${JSON.stringify(top.cards)} ≠ ${JSON.stringify(listed)}`);
}
// どのシナリオを遊ぶか。指定が無ければ一覧の先頭（既定の選択）
if (args.scenario) {
  // **練習は畳みの中にある**（SPEC 7.6.27）。開かずに押そうとすると
  // 「カードが無い」で落ちるが、それは正しい落ち方ではない
  const inFoldSel = `#tutorial-list [data-scenario="${args.scenario}"]`;
  if (await page.$(inFoldSel)) {
    await page.click('#tutorial-group > summary');
    await page.waitForTimeout(250);
  }
  const sel = (await page.$(inFoldSel)) ? inFoldSel
    : `#scenario-list [data-scenario="${args.scenario}"]`;
  if (!(await page.$(sel))) {
    note('error', 'select', `シナリオ ${args.scenario} のカードが無い`);
  } else {
    await page.click(sel);
    await page.waitForTimeout(120);
    const on = await page.$eval(sel, e => e.classList.contains('card-on'));
    if (!on) note('error', 'select', `カードを押しても選択が切り替わらない（${args.scenario}）`);
  }
}
if (args.assist) {
  const sel = `#assist-list input[value="${args.assist}"]`;
  if (!(await page.$(sel))) {
    note('error', 'select', `アシスト ${args.assist} が選べない`);
  } else {
    await page.check(sel);
    await page.waitForTimeout(80);
  }
}
await screen('02-select');

// **選ぶ画面が画面に収まっているか。**（SPEC 7.6.27）
// プレイ画面と同じ穴を2回踏んだ — 札を7枚積むと縦 1593px になり、
// 「開始する」がどの画面サイズでも一度も見えなかった。
// **測っていたのは入口とプレイ画面だけだった。**
{
  const fit = await page.evaluate(() => {
    const s = document.querySelector('#btn-start').getBoundingClientRect();
    return {
      over: document.documentElement.scrollHeight - innerHeight,
      startVisible: s.top >= 0 && s.bottom <= innerHeight,
      startBottom: Math.round(s.bottom),
    };
  });
  if (fit.over > 0) {
    note('error', 'select', `選ぶ画面が ${fit.over}px はみ出している`);
  }
  if (!fit.startVisible) {
    note('error', 'select',
      `「開始する」がスクロールしないと見えない（下端 ${fit.startBottom}px）`);
  }
}

await page.click('#btn-start');
await page.waitForSelector('#screen-briefing.active');
await screen('03-briefing');

// 画面の見方は畳んである（SPEC 7.6.23）。**開くまで中身は組まれない** —
// 閉じた `<details>` の中は幅が 0 なので、そこで描くと潰れる。
// ここで開けるのは、凡例と実画面の突き合わせがこの先にあるため
await page.click('#brief-legend-fold > summary');
await page.waitForTimeout(250);
// 凡例は画面の下端にあり、上端だけを撮ると一度も写らない
await page.evaluate(() => document.getElementById('brief-mini')
  .scrollIntoView({ block: 'start' }));
await page.waitForTimeout(150);
await shot('03b-briefing-legend');
await page.evaluate(() => window.scrollTo(0, 0));

const legendBefore = await legendSample('#brief-mini');

await page.click('#btn-begin');

// **プレイ画面が画面に収まっているか。**（SPEC 7.6.24）
// 右カラムの天井を手で書いていたので、どの画面サイズでも 186px はみ出し、
// 下のボタン2つが一度も見えなかった。**測っていたのは入口だけだった。**
{
  const fit = await page.evaluate(() => {
    const vis = (sel) => {
      const e = document.querySelector(sel);
      if (!e || e.offsetParent === null) return null;
      const r = e.getBoundingClientRect();
      return { bottom: Math.round(r.bottom), ok: r.top >= 0 && r.bottom <= innerHeight };
    };
    const scrollers = [...document.querySelectorAll('#screen-play *')].filter(
      (e) => e.scrollHeight > e.clientHeight + 2
        && /auto|scroll/.test(getComputedStyle(e).overflowY)).length;
    return {
      over: document.documentElement.scrollHeight - innerHeight,
      finish: vis('#btn-finish'), advance: vis('#btn-advance'), scrollers,
    };
  });
  if (fit.over > 0) {
    note('error', 'play', `プレイ画面が ${fit.over}px はみ出している`);
  }
  for (const [name, v] of [['対応を終了する', fit.finish], ['対応フェーズに移る', fit.advance]]) {
    if (v && !v.ok) {
      note('error', 'play', `${name} がスクロールしないと見えない（下端 ${v.bottom}px）`);
    }
  }
  if (fit.scrollers > 1) {
    note('error', 'play', `スクロールする箱が ${fit.scrollers} 個ある（1つに絞る）`);
  }
}
await page.waitForSelector('#screen-play.active');
await checkCanvasStable('play');
await checkTimeline('play-start');
await screen('04-play-start');

// 凡例で見たものが、そのままここにあるか
const playNow = await legendSample('#screen-play');
if (!legendBefore.phases.length || !legendBefore.groups.length
    || !legendBefore.statKeys.length) {
  note('error', 'legend', `凡例から見本が読めない: ${JSON.stringify(legendBefore)}`);
}
for (const [k, label] of [['phases', 'フェーズ帯'], ['statKeys', '帯の数値の見出し']]) {
  const a = JSON.stringify(legendBefore[k]), b = JSON.stringify(playNow[k]);
  if (a !== b) {
    note('error', 'legend', `凡例が実画面と食い違っている（${label}）: ${a} ≠ ${b}`);
  }
}
// 束の見本はどれを選んでもよいが、実画面に無い束を見せてはいけない
if (legendBefore.groups.length !== 1
    || !playNow.groups.includes(legendBefore.groups[0])) {
  note('error', 'legend', `凡例のアクションの束が実画面に無い: `
    + `${JSON.stringify(legendBefore.groups)} ⊄ ${JSON.stringify(playNow.groups)}`);
}

// 押した結果が読めるかは画面の高さで変わる。**狭い側から先に見る** —
// 1440×1000 で通っていたものが 1440×800 で落ちた（周7 の退行）
const HEIGHTS = { 2: 800, 3: 1000 };

const log = [];
for (let i = 0; i < 40; i++) {
  // 視点の戻り先は押した瞬間に決まるので、高さを変えるのは押す前
  if (HEIGHTS[i]) {
    await page.setViewportSize({ width: 1440, height: HEIGHTS[i] });
    await page.waitForTimeout(150);
  }
  const b = await page.$('#actions-list button.act:not([disabled])');
  if (!b) break;
  const label = (await b.textContent()).trim().split('\n')[0];
  await b.click();
  await page.waitForTimeout(140);
  const t = await page.textContent('#stat-time').catch(() => '');
  const inc = await page.$$eval('#incoming-list .incoming.fresh .incoming-label',
    e => e.map(x => x.textContent)).catch(() => []);
  log.push({ n: i + 1, at: t, action: label, incoming: inc });
  if (HEIGHTS[i]) {
    await checkTimeline(`play-after-result(1440×${HEIGHTS[i]})`);
    await checkResultConclusionVisible(`play-after-result(1440×${HEIGHTS[i]})`);
    await checkActionsStayVisible(`play-after-result(1440×${HEIGHTS[i]})`);
    await shot(`05-play-result-h${HEIGHTS[i]}`);
    await checkNoHorizontalOverflow(`play-after-result(1440×${HEIGHTS[i]})`);
  }
}
await page.setViewportSize({ width: 1440, height: 1000 });
await page.waitForTimeout(150);
await checkCanvasStable('play-late');
await checkTimeline('play-late');
await screen('06-play-late');
await checkCaretsShared('play-late');

// 凡例はプレイ中も開ける（SPEC 7.6.15）。開いた状態も撮る
await page.click('#btn-show-legend');
await page.waitForTimeout(200);
await screen('06b-legend');
const legendInPlay = await legendSample('#legend-mini');
if (JSON.stringify(legendInPlay.phases) !== JSON.stringify(playNow.phases)) {
  note('error', 'legend', `プレイ中の凡例が実画面と食い違っている: `
    + `${JSON.stringify(legendInPlay.phases)}`);
}
await page.click('[data-close="modal-legend"]');
await page.waitForTimeout(120);

await page.click('#btn-advance').catch(() => {});
await page.waitForTimeout(350);
await screen('07-assessment');
const boxes = await page.$$('#assess-list input[type=checkbox]');
if (boxes.length) await boxes[0].check();
await page.click('#btn-assess-confirm').catch(() => {});
await page.waitForTimeout(400);
await screen('08-response');

for (let i = 0; i < 10; i++) {
  const b = await page.$('#actions-list button.act:not([disabled])');
  if (!b) break;
  await b.click(); await page.waitForTimeout(140);
}
// 止めた・取り除いたが盤に出ている状態。**ここでしか撮れない** —
// 講評へ進むと盤は消え、答え合わせの側の表示に変わる（SPEC 7.6.16）
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(150);
await screen('08b-contained');
await page.click('#btn-finish').catch(() => {});
await page.waitForTimeout(300);
await page.click('#btn-finish-confirm').catch(() => {});
await page.waitForTimeout(1200);

if (!(await page.$('#screen-debrief.active'))) {
  note('error', 'debrief', '講評画面が出ない');
} else {
  await checkCanvasStable('debrief');
  await page.evaluate(() => window.scrollTo(0, 0));
  await screen('09-debrief');

  // 被害のグラフは画面の中ほどにある。上端と下端だけを撮ると一度も写らない。
  // ここは講評の主役の1つ（実線＝積み上がった分、破線＝復旧までの見込み）
  const damage = await page.evaluate(() => {
    const el = document.querySelector('.damage-split');
    if (!el) return null;
    el.scrollIntoView({ block: 'center' });
    const cv = el.parentElement.querySelector('canvas');
    return {
      text: el.innerText.replace(/\s+/g, ' ').trim(),
      canvas: cv ? `${cv.getAttribute('height')}x${cv.width}` : null,
    };
  });
  await page.waitForTimeout(200);
  if (!damage) {
    note('error', 'debrief', '被害の二段表示（累積／復旧までの見込み）が無い');
  } else {
    if (!damage.canvas) note('error', 'debrief', '被害グラフが無い');
    await shot('09b-debrief-damage');
  }

  // ②③④ は上端にも下端にも入らない。**講評の中身はここにある** —
  // 論点の重み付け、取り損ねた証拠、方針適合の内訳。撮らないと誰も見ない
  for (const [name, needle] of [
    ['09c-debrief-questions', '判定時点の論点'],
    ['09d-debrief-lost', '取り損ねた証拠'],
    ['09e-debrief-policy', '方針への適合'],
  ]) {
    const found = await page.evaluate((text) => {
      for (const h of document.querySelectorAll('#debrief-body h2')) {
        if (h.textContent.includes(text)) {
          h.scrollIntoView({ block: 'start' });
          return true;
        }
      }
      return false;
    }, needle);
    if (!found) { note('warn', 'debrief', `${needle} の節が無い`); continue; }
    await page.waitForTimeout(180);
    await shot(name);
  }

  // 封じ込めの答え合わせ（SPEC 5.8 / 7.6.13）。**ここが唯一の開示の場**で、
  // 復旧地平の破線がなぜその傾きなのかを言葉にしている唯一の箇所でもある
  const cont = await page.evaluate(() => {
    for (const h of document.querySelectorAll('#debrief-body h3')) {
      if (h.textContent.includes('止めたもの')) {
        h.scrollIntoView({ block: 'start' });
        return h.parentElement.innerText.replace(/\s+/g, ' ').trim();
      }
    }
    return null;
  });
  if (!cont) {
    note('error', 'debrief', '封じ込めの答え合わせ（止めたもの／取り除いたもの）が無い');
  } else {
    await page.waitForTimeout(180);
    await shot('09f-debrief-containment');
  }

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(200);
  await screen('10-debrief-bottom');
}

const summary = {
  dpr: DPR, profile: PROFILE, assist: ASSIST, scenario: SCENARIO, base: BASE,
  moves: log.length, log, shots,
  findings,
  passed: !findings.some(f => f.severity === 'error'),
};
writeFileSync(join(OUT, `playtest.${TAG}.json`), JSON.stringify(summary, null, 2));

console.log(`■ プレイ ${log.length} 手 / dpr ${DPR} / アシスト ${ASSIST} / シナリオ ${SCENARIO}`);
for (const e of log) {
  if (e.incoming.length) console.log(`  ${e.at}  ★ ${e.incoming.join(' / ')}`);
}
console.log(`\n■ 機械的に拾えたもの`);
if (!findings.length) console.log('  なし');
for (const f of findings) console.log(`  [${f.severity}] ${f.where}: ${f.what}`);
console.log(`\n  スクリーンショット: ${OUT}/  （${shots.length}枚）`);
console.log(`  詳細: ${join(OUT, `playtest.${TAG}.json`)}`);

await browser.close();
process.exit(summary.passed ? 0 : 1);
