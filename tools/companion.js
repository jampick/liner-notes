// Companion mode against a running bridge: the stage must land on the song the player is playing,
// and the page must never POST (which would change the music).   node companion.js <bridge url> <slug> <out dir>
const { chromium } = require('playwright-core');
const [bridge, slug, out] = process.argv.slice(2);
const exe = process.env.CHROMIUM || '/usr/lib/chromium/chromium';
(async () => {
  const now = await (await fetch(bridge + '/now')).json();
  const browser = await chromium.launch({ executablePath: exe, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: 'dark' });
  const page = await ctx.newPage();
  const posts = [], errors = [];
  page.on('request', r => { if (r.method() === 'POST') posts.push(r.url()); });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`${bridge}/p/${slug}.html#companion`);
  await page.waitForTimeout(6000);
  await page.screenshot({ path: `${out}/companion-stage.png` });
  const shown = await page.evaluate(() => document.body.innerText);
  const onStage = now.ok && now.title && shown.includes(now.title.replace(/\s*\(.*$/, '').split(' - ').pop().trim());
  await browser.close();
  const bad = [];
  if (posts.length) bad.push('page POSTed: ' + posts.join(', '));
  if (!onStage) bad.push(`stage doesn't show the playing song (${now.title})`);
  if (errors.length) bad.push(...errors);
  console.log(JSON.stringify({ playing: now.title, song: now.song, source: now.source }), bad.length ? 'PROBLEMS: ' + bad.join(' | ') : 'ok');
  process.exit(bad.length ? 1 : 0);
})();
