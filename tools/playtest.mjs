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

await page.click('#btn-begin');
await page.waitForSelector('#screen-play.active');
await checkCanvasStable('play');
await screen('04-play-start');

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
