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

async function shot(name) {
  const path = join(OUT, `${name}.dpr${DPR}.png`);
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
 *  次の一手で読めなくなる。 */
async function checkCaretsShared(where) {
  const bad = await page.evaluate(() => {
    const openers = [
      ['畳んだ連絡', '#incoming-list details.incoming-old > summary'],
      ['証拠カード', '#evidence-list .ev-head, #result-area .ev-head'],
      ['アクションの束', '#actions-list .act-group-head'],
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
  const seen = new Set();
  for (const g of bad) {
    if (g.marks.includes('なし')) {
      note('error', where, `${g.name} に開く記号が無い`);
    }
    g.marks.forEach(m => { if (m !== 'なし') seen.add(m); });
  }
  if (seen.size > 1) {
    note('error', where, `押せるものの記号が場所ごとに違う: ${[...seen].join(' ')}`);
  }
}

async function screen(name) {
  await checkNoHorizontalOverflow(name);
  await checkNoRawMarkdown(name);
  await shot(name);
}

// ── 遊ぶ ──────────────────────────────────────────

await page.goto(BASE, { waitUntil: 'networkidle' });
await screen('01-top');

await page.click('#btn-to-select');
await page.waitForSelector('#scenario-list .card:not(.card-error)');
await screen('02-select');

await page.click('#btn-start');
await page.waitForSelector('#screen-briefing.active');
await screen('03-briefing');

// 凡例は画面の下端にあり、上端だけを撮ると一度も写らない
await page.evaluate(() => document.getElementById('brief-mini')
  .scrollIntoView({ block: 'start' }));
await page.waitForTimeout(150);
await shot('03b-briefing-legend');
await page.evaluate(() => window.scrollTo(0, 0));

const legendBefore = await legendSample('#brief-mini');

await page.click('#btn-begin');
await page.waitForSelector('#screen-play.active');
await checkCanvasStable('play');
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

const log = [];
for (let i = 0; i < 40; i++) {
  const b = await page.$('#actions-list button.act:not([disabled])');
  if (!b) break;
  const label = (await b.textContent()).trim().split('\n')[0];
  await b.click();
  await page.waitForTimeout(140);
  const t = await page.textContent('#stat-time').catch(() => '');
  const inc = await page.$$eval('#incoming-list .incoming.fresh .incoming-label',
    e => e.map(x => x.textContent)).catch(() => []);
  log.push({ n: i + 1, at: t, action: label, incoming: inc });
  if (i === 2) { await checkActionsStayVisible('play-after-result'); await screen('05-play-result'); }
}
await checkCanvasStable('play-late');
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

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(200);
  await screen('10-debrief-bottom');
}

const summary = {
  dpr: DPR, profile: PROFILE, base: BASE,
  moves: log.length, log, shots,
  findings,
  passed: !findings.some(f => f.severity === 'error'),
};
writeFileSync(join(OUT, `playtest.dpr${DPR}.json`), JSON.stringify(summary, null, 2));

console.log(`■ プレイ ${log.length} 手 / dpr ${DPR}`);
for (const e of log) {
  if (e.incoming.length) console.log(`  ${e.at}  ★ ${e.incoming.join(' / ')}`);
}
console.log(`\n■ 機械的に拾えたもの`);
if (!findings.length) console.log('  なし');
for (const f of findings) console.log(`  [${f.severity}] ${f.where}: ${f.what}`);
console.log(`\n  スクリーンショット: ${OUT}/  （${shots.length}枚）`);
console.log(`  詳細: ${join(OUT, `playtest.dpr${DPR}.json`)}`);

await browser.close();
process.exit(summary.passed ? 0 : 1);
