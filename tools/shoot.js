// Headless walk-through of a built page: screenshots in dark, light and phone width, console errors,
// and the things a page must carry (AI disclosure, iTunes attribution next to previews, draft badges).
// Never opens a visible window.   node shoot.js <built html> <out dir>
const { chromium } = require('playwright-core');
const file = require('path').resolve(process.argv[2]), out = process.argv[3];
const exe = process.env.CHROMIUM || '/usr/bin/chromium';
(async () => {
  const browser = await chromium.launch({ executablePath: exe, headless: true });
  let failed = 0;
  for (const [scheme, w, h] of [['dark', 1440, 1000], ['light', 1440, 1000], ['dark', 400, 860]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme });
    const page = await ctx.newPage();
    // A static check: keep the page away from any bridge running on this machine (and never change its music).
    await page.route(/127\.0\.0\.1:47391|localhost:47391/, r => r.abort());
    const errors = [];
    page.on('pageerror', e => errors.push('pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/127\.0\.0\.1:47391|ERR_CONNECTION_REFUSED|ERR_FAILED|fonts\.g/.test(m.text())) errors.push('console: ' + m.text()); });
    await page.goto('file://' + file);
    await page.waitForTimeout(4000);
    const tag = `${scheme}-${w}`;
    const head = await page.evaluate(() => ({
      generator: document.querySelector('meta[name=generator]')?.content,
      ai: document.querySelector('meta[name=ai-disclosure]')?.content,
      ld: !!document.querySelector('script[type="application/ld+json"]'),
    }));
    if (w > 500) await page.screenshot({ path: `${out}/${tag}-1-opening.png` });
    await page.click('#start');
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${out}/${tag}-2-stop1.png`, fullPage: w < 500 });
    const stop = await page.evaluate(() => ({
      itunes: [...document.querySelectorAll('.act')].some(e => /courtesy of iTunes/.test(e.textContent)),
      apple: [...document.querySelectorAll('a.act')].some(e => /Apple Music/.test(e.textContent)),
      draft: [...document.querySelectorAll('.badge')].some(e => /Draft notes/.test(e.textContent)),
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
    }));
    if (w > 500 && scheme === 'dark') {
      for (let k = 0; k < 5; k++) await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(1200);
      await page.screenshot({ path: `${out}/${tag}-3-era.png` });
      await page.keyboard.press('m');
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${out}/${tag}-4-map.png` });
    }
    const bad = [];
    if (!head.generator || !head.ai || !head.ld) bad.push('missing AI disclosure in <head>');
    if (stop.apple && !stop.itunes) bad.push('preview without "provided courtesy of iTunes"');
    if (stop.overflow) bad.push('horizontal scroll');
    if (errors.length) bad.push(...errors);
    failed += bad.length;
    console.log(tag, JSON.stringify({ ...stop, draftBadge: stop.draft }), bad.length ? 'PROBLEMS: ' + bad.join(' | ') : 'ok');
    await ctx.close();
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})();
