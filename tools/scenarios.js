// The page's headless checks, against tools/fake_bridge.py: never a visible window, never real playback.
//
//   node tools/scenarios.js [name ...]       every scenario, or just the named ones; exit 1 on any failure
//
// Every request that isn't the fake bridge's origin is aborted, and any attempt at a YouTube or Google host fails
// the run. Screenshots go to LN_SHOTS (default <work>/shots). The work dir (fixtures, browser profiles, Chromium's
// temp files) defaults to ~/.cache/liner-notes-tests: it has to be on disk with a short path. Playwright runs
// Chromium with --disable-dev-shm-usage, which puts its shared memory in TMPDIR, and on a tmpfs /tmp near its
// quota the renderer's compositor dies with SIGTRAP on big layers (that was the "headless crash"). A long TMPDIR
// path breaks Chromium's singleton socket instead.
//
// CHROMIUM picks the browser (default /usr/lib/chromium/chromium, the binary itself: the /usr/bin/chromium launcher
// adds the flags in ~/.config/chromium-flags.conf, Wayland and extensions included). PYTHON picks the Python that
// runs the fake bridge (it imports liner_notes for the fixtures). LN_PORT moves the port (default 47411).
const fs = require('fs'), path = require('path'), os = require('os'), { spawn, execFileSync } = require('child_process');
const ROOT = path.resolve(__dirname, '..');
const WORK = process.env.LN_WORK || path.join(process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache'), 'liner-notes-tests');
fs.mkdirSync(WORK, { recursive: true });
process.env.TMPDIR = WORK;
const { chromium } = require('playwright-core');
const SHOTS = process.env.LN_SHOTS || path.join(WORK, 'shots');
const PORT = +(process.env.LN_PORT || 47411), B = `http://127.0.0.1:${PORT}`;
const EXE = process.env.CHROMIUM || '/usr/lib/chromium/chromium';
const PY = process.env.PYTHON || 'python3';
const FX = path.join(WORK, 'fx');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const FORBIDDEN = /(^|\.)(youtube\.com|youtube-nocookie\.com|ytimg\.com|googlevideo\.com|google\.com|googleapis\.com|gstatic\.com|youtu\.be)$/i;

/* ---------- the fake bridge ---------- */
let bridge = null, S = {}, epoch = 1;
function put(patch) { Object.assign(S, patch); const f = path.join(FX, 'state.json'); fs.writeFileSync(f + '.tmp', JSON.stringify(S)); fs.renameSync(f + '.tmp', f); }
async function startBridge() {
  bridge = spawn(PY, [path.join(ROOT, 'tools/fake_bridge.py'), 'serve', FX, '--port', String(PORT)], { stdio: ['ignore', 'ignore', process.env.LN_DEBUG ? fs.openSync(path.join(WORK, 'fake.err'), 'a') : 'ignore'], env: { ...process.env, PYTHONPATH: ROOT } });
  for (let k = 0; k < 50; k++) { try { const r = await fetch(B + '/now'); if (r.ok) return; } catch {} await sleep(100); }
  throw new Error('fake bridge did not start');
}
function stopBridge() { if (bridge) { bridge.kill('SIGKILL'); bridge = null; } }
const logLines = name => { try { return fs.readFileSync(path.join(FX, name), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)); } catch { return []; } };
const clearLogs = () => ['requests.log', 'posts.log'].forEach(n => fs.writeFileSync(path.join(FX, n), ''));

const M = () => JSON.parse(fs.readFileSync(path.join(FX, 'fixtures.json')));
const lyricsFor = (first = 8, every = 3, n = 60) => Array.from({ length: n }, (_, k) => ({ t: first + k * every, text: `made-up line number ${k + 1}` }));
function nowFor(s, extra = {}) {
  return { ok: true, state: 'playing', title: s.title, artist: s.artist, album: s.album || null, yt: s.yt, song: s.id || s.song || null, journey: s.journey === undefined ? M().slug : s.journey,
    playlist: s.playlist || 'Downtempo Drift', duration: 240, pos0: 0, t0: Date.now() / 1000, epoch: ++epoch, moving: true, ...extra };
}
const play = (s, extra) => put({ now: nowFor(s, extra) });

/* ---------- the browser ---------- */
let browser;
const failures = [], offsite = new Set(), forbidden = new Set();
let current = '';
function check(what, ok, detail = '') { if (!ok) { failures.push(`${current}: ${what}${detail ? ' (' + detail + ')' : ''}`); console.log(`    FAIL ${what}${detail ? ' (' + detail + ')' : ''}`); } else console.log(`    ok   ${what}`); return ok; }
const hiddenShim = () => {
  let hidden = false;
  Object.defineProperty(Document.prototype, 'hidden', { get: () => hidden, configurable: true });
  Object.defineProperty(Document.prototype, 'visibilityState', { get: () => hidden ? 'hidden' : 'visible', configurable: true });
  window.__setHidden = h => { hidden = h; document.dispatchEvent(new Event('visibilitychange')); };
};
async function guard(ctx) {
  await ctx.addInitScript(hiddenShim);
  // Only requests that leave the fake bridge are intercepted (and aborted). Routing the bridge's own requests
  // through Playwright as well stalled the browser's connections to it on a video 404 (the page stopped hearing
  // from the bridge), and slowed every scenario on CI.
  await ctx.route(url => !url.href.startsWith(B + '/'), r => {
    const u = r.request().url();
    if (u.startsWith('data:') || u.startsWith('blob:')) return r.continue();
    let host = ''; try { host = new URL(u).hostname; } catch {}
    if (FORBIDDEN.test(host)) forbidden.add(u); else offsite.add(u);
    return r.abort();
  });
}
async function newPage(opt = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: 'dark', ...opt });
  await guard(ctx);
  const page = await ctx.newPage();
  watch(page);
  return { ctx, page };
}
function watch(page) {
  if (process.env.LN_DEBUG) page.on('console', m => console.log('      console:', m.text().slice(0, 200)));
  if (process.env.LN_DEBUG) { page.on('framenavigated', f => console.log('      nav:', f.url())); page.on('requestfailed', r => console.log('      reqfail:', r.url(), r.failure() && r.failure().errorText)); }
  page.on('crash', () => check('renderer did not crash', false));
  const seen = new Set();  // each distinct error once, not once per timer tick
  page.on('pageerror', e => { if (!seen.has(e.message)) { seen.add(e.message); check('no page errors', false, e.message); } });
}
// Node-side wait on a condition (the fake's logs), polling every 50 ms; true if it came true in time.
async function waitFor(fn, ms) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await sleep(50); } return fn(); }
// Resolves to the milliseconds it took, or null after `ms`.
// Polls with page.evaluate, which runs under the page's CSP the way the page itself does. page.waitForFunction
// evaluates a string, and the bridge's hash-based CSP (no 'unsafe-eval') makes that throw at once, which turned
// every wait into an instant "no" and let checks that expect nothing to happen pass without waiting.
async function within(page, ms, fn, arg) {
  const t = Date.now(); let err = null;
  while (Date.now() - t < ms) {
    try { if (await page.evaluate(fn, arg)) return Date.now() - t; err = null; } catch (e) { err = e; }  // a navigation in between: try again
    await sleep(50);
  }
  if (err && process.env.LN_DEBUG) console.log('      within:', err.message.split('\n')[0]);
  return null;
}
const text = (page, sel) => page.$eval(sel, n => n.textContent).catch(() => null);
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name + '.png') });

/* ---------- scenarios ---------- */
const scenarios = {};

scenarios['opening-and-full-page-keys'] = async () => {
  put({ now: { ok: false, reason: 'stopped' } });
  const { ctx, page } = await newPage({ viewport: { width: 1366, height: 768 } });
  await page.goto(B + '/p/downtempo-drift.html');
  await sleep(1500);
  const box = await page.evaluate(() => ({ y: scrollY, layerTop: document.querySelector('#opening').scrollTop, title: document.querySelector('#openTitle').getBoundingClientRect().top, start: document.querySelector('#start').getBoundingClientRect().bottom }));
  check('opening fits 1366x768: title on screen', box.title >= 0 && box.layerTop === 0, JSON.stringify(box));
  check('opening fits 1366x768: start button on screen', box.start <= 768, JSON.stringify(box));
  await shot(page, 'opening-1366x768');
  await page.click('#start');
  await sleep(400);
  const at = () => page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('s'));
  const s0 = await at();
  await page.keyboard.press('ArrowRight'); await sleep(900);
  const s1 = await at();
  check('full page: → walks to the next stop', s1 && s1 !== s0, `${s0} -> ${s1}`);
  await page.keyboard.press('l'); await sleep(500);
  await page.keyboard.press('h'); await sleep(500);
  check('full page: no h/l aliases', (await at()) === s1);
  const hint = await text(page, '#pageKeys');
  check('full page hint row lists the walk and no vim keys', /← → walk/.test(hint) && !/\bh\b|\bl\b/.test(hint), hint);
  await ctx.close();
};

scenarios['s1-stuck-opening'] = async () => {
  const m = M(), s = m.stops[2];
  put({ now: { ok: false, reason: 'stopped' }, lyrics: {}, history: [] });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await sleep(800);
  check('companion mode opens on the stage, not the opening', await page.isVisible('#stagev') && !(await page.isVisible('#opening')));
  check('nothing playing says so', /Nothing playing/.test(await text(page, '#svTitle')));
  await sleep(4200);
  play(s, { pos0: 30 });
  const ms = await within(page, 2000, t => document.querySelector('#svTitle').textContent === t, s.title);
  check('stage shows the song within 2 s of the first good answer', ms !== null, `${ms} ms`);
  check('#opening not visible', !(await page.isVisible('#opening')));
  await ctx.close();
};

scenarios['s1-off-journey-then-on'] = async () => {
  const m = M(), o = m.offsong, s = m.stops[3];
  put({ now: nowFor({ ...o, id: o.song, journey: null }, { pos0: 20 }), lyrics: { [o.yt]: lyricsFor(4) } });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  const ms = await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, o.title);
  check('an off-journey song shows its title', ms !== null);
  check('and its artist', (await text(page, '#svBy')) === o.artist);
  check('lyrics show for it', await within(page, 4000, () => getComputedStyle(document.querySelector('.sv-lyr')).display !== 'none') !== null);
  check('a cover from the bridge cache', await within(page, 3000, () => /\/m\/[^/]+\/covers\//.test(document.querySelector('#svCover').src)) !== null);
  const note = await text(page, '#svNote');
  check('the notes pane says this playlist has no notes yet', /Soul Rap Covers has no liner notes yet/.test(note), note);
  const blank = await page.evaluate(() => [...document.querySelectorAll('#stagev .pane')].filter(p => getComputedStyle(p).display !== 'none' && !p.innerText.trim()).map(p => p.className));
  check('no blank panes', !blank.length, blank.join(', '));
  await shot(page, 'stage-off-journey-1920x1080');
  await sleep(6000);
  play(s, { pos0: 10 });
  const ms2 = await within(page, 2000, t => document.querySelector('#svTitle').textContent === t, s.title);
  check('an on-journey song after 10 s off it enters the stage within 2 s', ms2 !== null, `${ms2} ms`);
  await ctx.close();
};

scenarios['s3-song-change'] = async () => {
  const m = M(), a = m.video_stops[0], b = m.video_stops[1];
  put({ lyrics: { [a.yt]: lyricsFor(), [b.yt]: lyricsFor() }, history: [] });
  play(a, { pos0: 196 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, a.title);
  if (m.videos) check('video plays for a song with one on disk', await within(page, 6000, () => document.querySelector('#stagev').classList.contains('cinema')) !== null);
  const t0 = Date.now();
  play(b, { pos0: 197, moving: false });  // cliamp holds the old position while the new song buffers
  const ms = await within(page, 1000, t => document.querySelector('#svTitle').textContent === t, b.title);
  check('title changes within 1000 ms', ms !== null, `${Date.now() - t0} ms`);
  let cinema = false;
  for (let k = 0; k < 12; k++) { cinema = cinema || await page.evaluate(() => document.querySelector('#stagev').classList.contains('cinema')); await sleep(100); }
  check('no picture from the new video until the new song moves', !cinema);
  if (m.videos) check('video source switched', /\/v\/[\w-]+\.mp4$/.test(await page.$eval('.sv-vid video', v => v.src)) && (await page.$eval('.sv-vid video', v => v.src)).includes(b.yt));
  play(b, { pos0: 0.2 });
  if (m.videos) check('video comes up once the song moves', await within(page, 5000, () => document.querySelector('#stagev').classList.contains('cinema')) !== null);
  const jumps = () => page.$eval('#svEra', n => (n.textContent.match(/years? (before|after)|the same year as/g) || []).length);
  check('era line carries exactly one jump phrase', (await jumps()) === 1, await text(page, '#svEra'));
  for (let k = 0; k < 4; k++) { await page.keyboard.press('k'); await sleep(150); }
  check('after K four times, still exactly one', (await jumps()) === 1, await text(page, '#svEra'));
  await page.keyboard.press('k');
  check('K keeps the song', /♥ Kept/.test(await text(page, '#svStamp')));
  await ctx.close();
};

scenarios['s5-pause-seek-keys'] = async () => {
  const m = M(), s = m.video_stops[2];
  put({ lyrics: { [s.yt]: lyricsFor() } });
  play(s, { pos0: 40 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  if (m.videos) await within(page, 6000, () => document.querySelector('#stagev').classList.contains('cinema'));
  await sleep(1500);
  const pos = (await page.evaluate(() => 0)) || 0;
  put({ now: { ...S.now, state: 'paused', pos0: S.now.pos0 + (Date.now() / 1000 - S.now.t0), t0: Date.now() / 1000, epoch: ++epoch } });
  const ms = await within(page, 600, () => document.querySelector('#stagev').classList.contains('paused') && document.querySelector('#svPos').textContent.startsWith('⏸'));
  check('pause shows within 600 ms', ms !== null, `${ms} ms`);
  if (m.videos) { await sleep(200); check('the video holds while paused', await page.$eval('.sv-vid video', v => v.paused)); }
  put({ now: { ...S.now, state: 'playing', t0: Date.now() / 1000, epoch: ++epoch } });
  await sleep(1200);
  if (m.videos) {
    const p0 = S.now.pos0 + (Date.now() / 1000 - S.now.t0) + 60;
    put({ now: { ...S.now, pos0: p0, t0: Date.now() / 1000, epoch: ++epoch } });
    const t = Date.now(); let gap = 99;
    while (Date.now() - t < 1500) { const r = await page.evaluate(() => [document.querySelector('.sv-vid video').currentTime, Date.now()]); gap = Math.abs(r[0] - (S.now.pos0 + (r[1] / 1000 - S.now.t0))); if (gap < 0.5) break; await sleep(100); }
    check('a 60 s seek re-syncs the video within 1500 ms', gap < 0.5, `off by ${gap.toFixed(2)} s`);
  }
  void pos;
  clearLogs();
  await page.keyboard.press('ArrowRight'); await sleep(700);
  await page.keyboard.press('Space'); await sleep(700);
  await page.keyboard.press('ArrowLeft'); await sleep(700);
  const posts = logLines('posts.log');
  const ops = posts.map(p => p.body.op).join(',');
  check('→ ␣ ← send next, toggle, prev once each', ops === 'next,toggle,prev', ops);
  check('every POST carries the token', posts.length && posts.every(p => p.token_ok), JSON.stringify(posts.map(p => p.token_ok)));
  await ctx.close();
};

scenarios['s6-no-video'] = async () => {
  const m = M(), s = m.artist_stop;
  put({ lyrics: {} });
  play(s, { pos0: 30 });
  const { ctx, page } = await newPage();
  const before = forbidden.size;
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  await sleep(4000);
  const f0 = await page.evaluate(() => LinerNotes.hero().frames || 0);
  await sleep(6000);
  check('no YouTube or Google requests', forbidden.size === before);
  const lbl = await text(page, '#svVisLabel');
  check('the big pane shows the artist photo', /^•— photo/.test(lbl), lbl);
  const stats = await page.evaluate(() => LinerNotes.hero());
  console.log('      hero', JSON.stringify(stats));
  check('depth parallax is up (WebGL2 in this browser)', stats.gl && stats.on, JSON.stringify(stats));
  const fps = (stats.frames - f0) / 6;
  check('depth parallax draws 10 to 15 frames a second', fps >= 10 && fps <= 15.5, fps.toFixed(1));
  await shot(page, 'stage-photo-1920x1080');
  await ctx.close();
};

scenarios['s7-no-lyrics-then-late'] = async () => {
  const m = M(), s = m.still_stops[0];
  put({ lyrics: {} });
  play(s, { pos0: 0 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  await sleep(2500);
  check('no lyric pane without lyrics', await page.$eval('.sv-lyr', n => getComputedStyle(n).display === 'none'));
  const g = await page.evaluate(() => { const r = q => document.querySelector(q).getBoundingClientRect(); return { now: r('.sv-now').bottom, notesTop: r('.sv-notes').top, notesBottom: r('.sv-notes').bottom, tonight: r('.sv-tonight').top }; });
  check('notes fill rows 2 and 3', Math.abs(g.notesTop - g.now - 10) < 3 && Math.abs(g.tonight - g.notesBottom - 10) < 3, JSON.stringify(g));
  await page.evaluate(() => { window.__samples = []; setInterval(() => window.__samples.push([LinerNotes.stage().beat, document.querySelector('#stagev').classList.contains('lyrics')]), 50); });
  await sleep(5000);
  put({ lyrics: { [s.yt]: lyricsFor(5, 3) } });  // the page asks again at 17 s
  const ms = await within(page, 60000, () => document.querySelector('#stagev').classList.contains('lyrics'));
  check('late lyrics show up', ms !== null);
  await sleep(200);
  const at = await page.evaluate(() => { const S = window.__samples, k = S.findIndex(x => x[1]); return k > 0 ? [S[k - 1][0], S[k][0]] : null; });
  check('they come in on a note boundary', at && at[0] !== at[1], JSON.stringify(at));
  await ctx.close();
};

scenarios['s9-bridge-death'] = async () => {
  const m = M(), s = m.stops[8];
  put({ lyrics: {} });
  play(s, { pos0: 50 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  await page.evaluate(() => { window.__same = 'yes'; });
  stopBridge();
  const ms = await within(page, 3000, () => !document.querySelector('#svLive').textContent.includes('●') && /bridge/i.test(document.querySelector('#svTitle').textContent));
  check('bridge down: grey dot and the stage names the bridge within 3 s', ms !== null, `${ms} ms`);
  await shot(page, 'stage-bridge-down-1920x1080');
  await startBridge();
  const ms2 = await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  check('recovers within 3 s of the bridge coming back', ms2 !== null, `${ms2} ms`);
  check('without a reload', (await page.evaluate(() => window.__same)) === 'yes');
  await ctx.close();
};

scenarios['s10-keeps-and-timing-survive'] = async () => {
  const m = M(), s = m.stops[10];
  put({ lyrics: { [s.yt]: lyricsFor() } });
  play(s, { pos0: 30 });
  const dir = path.join(WORK, 'profile'); fs.rmSync(dir, { recursive: true, force: true });
  const open = async () => { const ctx = await chromium.launchPersistentContext(dir, { executablePath: EXE, headless: true, viewport: { width: 1920, height: 1080 } }); await guard(ctx); const page = ctx.pages()[0] || await ctx.newPage(); watch(page); await page.goto(B + '/p/downtempo-drift.html#companion'); await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title); return { ctx, page }; };
  let { ctx, page } = await open();
  await within(page, 4000, () => LinerNotes.stage().lines);
  await page.keyboard.press('k'); await sleep(200);
  await page.keyboard.press(']'); await sleep(200);
  await page.keyboard.press('}'); await sleep(200);
  await page.keyboard.press('l'); await sleep(400);
  const ls = await page.evaluate(yt => ({ off: localStorage.getItem('journey-lyric-offset-' + yt), lead: localStorage.getItem('journey-lyric-lead'), lyr: localStorage.getItem('journey-lyrics'), keeps: localStorage.getItem('liner-keeps-downtempo-drift') }), s.yt);
  check('[ ] saves under the pilot key', ls.off === '0.5', JSON.stringify(ls));
  check('{ } saves under the pilot key', ls.lead === '0.25', JSON.stringify(ls));
  check('L is not remembered', ls.lyr === null);
  await ctx.close();
  ({ ctx, page } = await open());
  check('the keep survives a closed window', /♥ Kept/.test(await text(page, '#svStamp')));
  check('lyrics are back on after L last time', await within(page, 4000, () => document.querySelector('#stagev').classList.contains('lyrics')) !== null);
  await ctx.close();
};

scenarios['s11-phone'] = async () => {
  const m = M(), s = m.video_stops[0];
  put({ lyrics: { [s.yt]: lyricsFor() } });
  play(s, { pos0: 30 });
  const { ctx, page } = await newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  await sleep(1500);
  const r = await page.evaluate(() => { const p = document.querySelector('#svPos').getBoundingClientRect(), v = document.querySelector('.sv-vis').getBoundingClientRect(), b = document.querySelector('.sv-bar').getBoundingClientRect(); return { pos: [p.left, p.right, p.top, p.width], ratio: v.width / v.height, bar: b.width, sw: document.documentElement.scrollWidth }; });
  check('position visible at 390 px', r.pos[3] > 0 && r.pos[0] >= 0 && r.pos[1] <= 390 && r.pos[2] < 844, JSON.stringify(r));
  check('progress bar visible at 390 px', r.bar > 100, JSON.stringify(r));
  check('video pane keeps 16:9', Math.abs(r.ratio - 16 / 9) < 0.03, r.ratio.toFixed(3));
  check('no sideways scroll', r.sw <= 390, String(r.sw));
  await ctx.close();
};

scenarios['s4-switch-journeys'] = async () => {
  const m = M(), s = m.stops[12], o = m.offsong;
  put({ lyrics: {} });
  play(s, { pos0: 20 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  play({ ...m.stops[13], journey: 'drift-b' }, { pos0: 5 });
  const ms = await within(page, 3000, () => location.pathname === '/p/drift-b.html' && /companion/.test(location.hash));
  check('another journey\'s song moves the window to that journey within 3 s', ms !== null, page.url());
  check('in the same window', ctx.pages().length === 1);
  await within(page, 3000, t => document.querySelector('#svTitle') && document.querySelector('#svTitle').textContent === t, m.stops[13].title);
  play({ ...o, id: o.song, journey: null }, { pos0: 5 });
  await sleep(2500);
  check('a song with no journey stays put, on the live stage', new URL(page.url()).pathname === '/p/drift-b.html' && (await text(page, '#svTitle')) === o.title, page.url());
  await ctx.close();
  const lp = await newPage();
  await lp.page.goto(B + '/p/live.html');
  check('/p/live.html shows the song', await within(lp.page, 3000, t => document.querySelector('#svTitle').textContent === t, o.title) !== null);
  check('/p/live.html has no full-page button', !(await lp.page.isVisible('#svExit')));
  await shot(lp.page, 'stage-live-1920x1080');
  play(s, { pos0: 5 });
  check('/p/live.html moves to the journey when one has the song', await within(lp.page, 4000, () => location.pathname === '/p/downtempo-drift.html') !== null, lp.page.url());
  await lp.ctx.close();
};

// The bridge keeps its token across restarts, so a refusal means the page predates a token change (a new
// state dir, a rotated token). The page should reload once for the new one, and not loop if refusals go on.
// Every step waits on something it can see (a POST in the fake's log, a navigation), never on a fixed sleep.
scenarios['token-refresh'] = async () => {
  const m = M(), s = m.stops[18];
  play(s, { pos0: 20 });
  const { ctx, page } = await newPage();
  const ready = async () => {
    await within(page, 15000, t => window.LinerNotes && LinerNotes.stage().sync && document.querySelector('#svTitle').textContent === t, s.title);
  };
  const press = async n => { await page.keyboard.press('ArrowRight'); return waitFor(() => logLines('posts.log').length >= n, 10000); };
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await ready();
  await page.evaluate(() => { window.__same = 'yes'; });
  stopBridge(); await startBridge();
  await ready();
  clearLogs();
  await press(1);
  check('the token survives a bridge restart', (logLines('posts.log')[0] || {}).token_ok === true, JSON.stringify(logLines('posts.log')));
  check('and nothing reloads', (await page.evaluate(() => window.__same)) === 'yes');
  put({ refuse_token: true });
  clearLogs();
  const nav = page.waitForEvent('framenavigated', { timeout: 15000 }).then(() => true, () => false);
  await press(1);
  check('a refused token reloads the page once', await nav);
  await page.waitForLoadState('load');
  await ready();
  await page.evaluate(() => { window.__again = 'yes'; });
  let navigated = false;
  const onNav = () => { navigated = true; };
  page.on('framenavigated', onNav);
  await press(2);
  await sleep(1000);  // a reload would have started by now; the page reloads in the same task as the 403
  page.off('framenavigated', onNav);
  check('a second refusal within a minute doesn\'t reload again', !navigated && (await page.evaluate(() => window.__again)) === 'yes');
  put({ refuse_token: false });
  await press(3);
  const posts = logLines('posts.log');
  check('POSTs go through once the bridge takes the token', posts.length === 3 && !posts[0].token_ok && !posts[1].token_ok && posts[2].token_ok, JSON.stringify(posts.map(p => p.token_ok)));
  await ctx.close();
};

scenarios['p15-intro-first'] = async () => {
  const m = M(), s = m.intro;
  const run = async first => {
    put({ lyrics: { [s.yt]: lyricsFor(first) }, history: [] });
    play(s, { pos0: 0 });
    const { ctx, page } = await newPage();
    await page.goto(B + '/p/downtempo-drift.html#companion');
    await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
    await within(page, 3000, () => LinerNotes.stage().lines);
    const b = (await page.evaluate(() => LinerNotes.stage().beats)).map(x => x.kind);
    await ctx.close();
    return b;
  };
  const early = await run(9), late = await run(120);
  check('first vocal early: the intro note goes first', early[0] === 'intro' && early.includes('essay'), early.slice(0, 4).join(','));
  check('first vocal late: the era essay keeps its place', late[0] === 'essay', late.slice(0, 4).join(','));
};

scenarios['stage-click-and-notes-key'] = async () => {
  const m = M(), s = m.stops[15];
  play(s, { pos0: 20 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  await page.mouse.click(600, 500); await sleep(500);
  check('a click on the stage stays on the stage', await page.isVisible('#stagev'));
  await page.click('#svExit'); await sleep(500);
  check('the corner button leads to the full page', !(await page.isVisible('#stagev')) && await page.isVisible('#app'));
  await page.keyboard.press('s'); await sleep(500);
  check('S goes back to the stage', await page.isVisible('#stagev'));
  await page.keyboard.press('n'); await sleep(800);
  const h = await page.evaluate(() => new URLSearchParams(location.hash.slice(1)).get('s'));
  check('N opens this song\'s full notes', !(await page.isVisible('#stagev')) && h === String(s.n), h);
  await ctx.close();
};

scenarios['perf-stage'] = async () => {
  const m = M(), s = m.video_stops[3];
  put({ lyrics: { [s.yt]: lyricsFor(2, 2.5) }, theme: THEMES.dark });
  play(s, { pos0: 5 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  await sleep(3000);
  const cdp = await ctx.newCDPSession(page); await cdp.send('Performance.enable');
  const metric = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(x => [x.name, x.value]));
  const a = await metric(); await sleep(60000); const b = await metric();
  const lps = (b.LayoutCount - a.LayoutCount) / 60, rps = (b.RecalcStyleCount - a.RecalcStyleCount) / 60;
  console.log(`      ${lps.toFixed(2)} layouts/s, ${rps.toFixed(1)} style recalcs/s, ${((b.ScriptDuration - a.ScriptDuration) / 60 * 100).toFixed(2)}% script`);
  check('under 5 layouts a second on the stage', lps < 5, lps.toFixed(2));
  clearLogs();
  await page.evaluate(() => window.__setHidden(true));
  await sleep(30000);
  const req = logLines('requests.log');
  const nowN = req.filter(r => r.p === '/now').length;
  check('under 20 /now a minute when hidden', nowN * 2 < 20, `${nowN} in 30 s`);
  check('nothing else asked for while hidden', req.filter(r => r.p !== '/now').length <= 1, req.filter(r => r.p !== '/now').map(r => r.p).join(','));
  check('the video stops while hidden', await page.$eval('.sv-vid video', v => v.paused));
  await ctx.close();
};

scenarios['churn-30-songs'] = async () => {
  const m = M();
  const songs = m.stops.slice(0, 12);
  put({ lyrics: Object.fromEntries(songs.map(s => [s.yt, lyricsFor(1, 2)])) });
  play(songs[0], { pos0: 10 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  const cdp = await ctx.newCDPSession(page); await cdp.send('Performance.enable');
  const metric = async () => { await cdp.send('HeapProfiler.collectGarbage'); await sleep(300); await cdp.send('HeapProfiler.collectGarbage'); return Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(x => [x.name, x.value])); };
  const change = async k => { const s = songs[k % songs.length]; play(s, { pos0: 10 + k }); await within(page, 2500, t => document.querySelector('#svTitle').textContent === t, s.title); await sleep(700); };
  for (let k = 1; k <= 3; k++) await change(k);
  const a = await metric();
  for (let k = 4; k < 34; k++) await change(k);
  const b = await metric();
  console.log(`      heap ${(a.JSHeapUsedSize / 1e6).toFixed(1)} -> ${(b.JSHeapUsedSize / 1e6).toFixed(1)} MB, nodes ${a.Nodes} -> ${b.Nodes}`);
  check('DOM nodes flat across 30 song changes', b.Nodes <= a.Nodes * 1.1 + 50, `${a.Nodes} -> ${b.Nodes}`);
  check('JS heap flat across 30 song changes', b.JSHeapUsedSize <= a.JSHeapUsedSize * 1.25 + 1e6, `${a.JSHeapUsedSize} -> ${b.JSHeapUsedSize}`);
  await ctx.close();
};

scenarios['bands-reconnect'] = async () => {
  const m = M(), s = m.stops[20];
  put({ bands_close_after: 5 });
  play(s, { pos0: 10 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  clearLogs();
  await page.keyboard.press('Escape');
  await sleep(9000);
  const n = logLines('requests.log').filter(r => r.p === '/bands').length;
  check('a dropped /bands stream comes back by itself', n >= 3, `${n} connections in 9 s`);
  clearLogs();
  await page.keyboard.press('s'); await sleep(4000);
  check('no /bands under the stage', logLines('requests.log').filter(r => r.p === '/bands').length === 0);
  put({ bands_close_after: 0 });
  await ctx.close();
};

scenarios['remix'] = async () => {
  const m = M(), [r1, r2] = m.remix;
  const song = (id, title) => ({ ok: true, state: 'playing', title, artist: 'Test Artist', yt: id, song: null, journey: null, duration: 180, pos0: 9, t0: Date.now() / 1000, epoch: ++epoch, moving: true });
  put({ now: song(r1, 'Party Pupils - First Song (Remix)'), theme: THEMES.dark });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/remix-test.html');
  check('a measured remix loads', await within(page, 1500, () => !document.body.classList.contains('idle-on') && document.querySelector('#title').textContent === 'First Song') !== null);
  const by = await page.$eval('#by', n => getComputedStyle(n, '::before').content);
  check('"♪ Party Pupils" has its space', by.includes(' ') || by.includes('♪ '), JSON.stringify(by));
  check('the loop count shows', await within(page, 6000, () => /×\d/.test(document.querySelector('#count').textContent)) !== null);
  put({ now: { ok: false, reason: 'stopped' } });
  check('ok:false goes idle', await within(page, 1500, () => document.body.classList.contains('idle-on')) !== null);
  put({ now: song(r1, 'Party Pupils - First Song (Remix)') });
  check('the same song coming back leaves idle (E5)', await within(page, 1500, () => !document.body.classList.contains('idle-on')) !== null);
  clearLogs(); await sleep(4000);
  const vis = logLines('requests.log').filter(r => r.p === '/now');
  const gaps = vis.slice(1).map((r, k) => r.t - vis[k].t);
  check('/now about four a second, one at a time', vis.length >= 10 && vis.length <= 18 && Math.min(...gaps) > 0.15, `${vis.length} in 4 s, min gap ${Math.min(...gaps).toFixed(3)}`);
  clearLogs(); await page.evaluate(() => window.__setHidden(true)); await sleep(10500);
  const hid = logLines('requests.log').filter(r => r.p === '/now').length;
  check('every 5 s when hidden (E12)', hid <= 3, `${hid} in 10.5 s`);
  await page.evaluate(() => window.__setHidden(false));
  clearLogs();
  await page.keyboard.press('ArrowRight'); await sleep(600);
  const posts = logLines('posts.log');
  check('→ sends next with the token', posts.length === 1 && posts[0].body.op === 'next' && posts[0].token_ok, JSON.stringify(posts));
  put({ now: song(r2, 'Somebody Else - Second Song (Remix)') });
  await within(page, 1500, () => document.querySelector('#title').textContent === 'Second Song');
  await sleep(500);
  await shot(page, 'remix-1920x1080');
  stopBridge();
  check('bridge down says so', await within(page, 4000, () => /bridge/i.test(document.querySelector('#idlemsg').textContent) && document.body.classList.contains('idle-on')) !== null);
  await startBridge();
  check('and recovers', await within(page, 3000, () => !document.body.classList.contains('idle-on')) !== null);
  await ctx.close();
  const rm = await newPage({ reducedMotion: 'reduce' });
  await rm.page.goto(B + '/p/remix-test.html');
  await sleep(1000);
  const anim = await rm.page.evaluate(() => [getComputedStyle(document.querySelector('.pic .photo')).animationName, getComputedStyle(document.querySelector('#note')).transitionDuration]);
  check('the remix page honours reduced motion', anim[0] === 'none', anim.join(' '));
  await rm.ctx.close();
};

scenarios['webgl-context-loss'] = async () => {
  const m = M(), s = m.artist_stop, c = m.culture_stop;
  play(s, { pos0: 30 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  check('parallax up before the loss', await within(page, 5000, () => LinerNotes.hero().on) !== null);
  await page.evaluate(() => { window.__lc = document.querySelector('#svGl').getContext('webgl2').getExtension('WEBGL_lose_context'); window.__lc.loseContext(); });
  await sleep(600);
  const a = await page.evaluate(() => LinerNotes.hero());
  play(c, { pos0: 30 });  // a new picture while the context is gone
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, c.title);
  await sleep(2000);
  const b = await page.evaluate(() => [LinerNotes.hero(), document.querySelector('#svGl').classList.contains('on'), document.querySelector('#stagev').classList.contains('glon')]);
  check('while lost: canvas off, no drawing', b[0].lost && !b[1] && !b[2] && b[0].frames === a.frames, JSON.stringify([a.frames, b]));
  check('the still shows meanwhile', await page.evaluate(() => [...document.querySelectorAll('.sv-still')].some(i => i.classList.contains('on'))));
  await page.evaluate(() => window.__lc.restoreContext());
  check('after restore the parallax comes back', await within(page, 5000, () => { const h = LinerNotes.hero(); return !h.lost && h.on && h.running; }) !== null, JSON.stringify(await page.evaluate(() => LinerNotes.hero())));
  const f0 = await page.evaluate(() => LinerNotes.hero().frames); await sleep(1500);
  check('and draws again', (await page.evaluate(() => LinerNotes.hero().frames)) > f0);
  await ctx.close();
};

scenarios['journey-ping-pong'] = async () => {
  const m = M(), s = m.stops[12];
  play(s, { pos0: 20 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
  put({ now: { ...S.now, journey: 'drift-b' } });  // one answer naming another journey, then back
  await sleep(450);
  put({ now: { ...S.now, journey: 'downtempo-drift' } });
  await sleep(2500);
  check('one stray answer doesn\'t move the window', new URL(page.url()).pathname === '/p/downtempo-drift.html', page.url());
  put({ now: { ...S.now, journey: 'drift-b' } });
  check('two answers that agree do', await within(page, 4000, () => location.pathname === '/p/drift-b.html') !== null, page.url());
  await within(page, 3000, t => document.querySelector('#svTitle') && document.querySelector('#svTitle').textContent === t, s.title);
  put({ now: { ...S.now, journey: 'downtempo-drift' } });
  await sleep(5000);
  check('and it doesn\'t bounce straight back to the page it left', new URL(page.url()).pathname === '/p/drift-b.html', page.url());
  await ctx.close();
};

// A video that fails (here the bridge answers 404 for a file it lists) is set aside; once the bridge serves it
// again, the next song change asks /v/ again and the video gets another try.
scenarios['video-retry'] = async () => {
  const m = M(), a = m.video_stops[0], b = m.video_stops[1];
  if (!m.videos) return check('fixtures have video (ffmpeg)', false);
  put({ video_broken: [a.yt], videos: null, lyrics: {} });
  play(a, { pos0: 20 });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await within(page, 10000, t => document.querySelector('#svTitle').textContent === t, a.title);
  check('a video that errors is set aside', await within(page, 15000, yt => LinerNotes.stage().videoFailed.includes(yt), a.yt) !== null);
  check('the stage shows a picture instead', !(await page.evaluate(() => document.querySelector('#stagev').classList.contains('cinema'))));
  put({ video_broken: [] });  // the bridge serves it again
  play(b, { pos0: 20 });
  await within(page, 10000, t => document.querySelector('#svTitle').textContent === t, b.title);
  play(a, { pos0: 20 });
  await within(page, 10000, t => document.querySelector('#svTitle').textContent === t, a.title);
  check('back on the song, it is no longer set aside', await within(page, 10000, yt => !LinerNotes.stage().videoFailed.includes(yt), a.yt) !== null,
    JSON.stringify(await page.evaluate(() => LinerNotes.stage())));
  // Software decoding on a CI runner can take a while to get the first frame up.
  check('and it plays', await within(page, 20000, () => document.querySelector('#stagev').classList.contains('cinema')) !== null, JSON.stringify(await page.evaluate(() => LinerNotes.stage())));
  await ctx.close();
};

scenarios['stage-copy-and-layout'] = async () => {
  const m = M(), r = m.rawsong, o = m.offsong;
  put({ now: { ok: false, reason: 'stopped' }, lyrics: {}, history: [], videos: null, video_broken: [] });
  const { ctx, page } = await newPage();
  await page.goto(B + '/p/downtempo-drift.html#companion');
  await sleep(1500);
  const stopped = await page.evaluate(() => document.querySelector('#stagev').innerText);
  check('"Nothing playing" is said once', (stopped.match(/Nothing playing/g) || []).length === 1, stopped.replace(/\n/g, ' | ').slice(0, 300));
  check('no L in the hint row with nothing playing', !/lyrics/.test(await text(page, '#svKeys')), await text(page, '#svKeys'));
  play({ ...r, id: r.song, journey: null }, { pos0: 10 });
  check('a raw upload title reads as title and artist', await within(page, 3000, () => document.querySelector('#svTitle').textContent === 'Big Poppa' && document.querySelector('#svBy').textContent === 'Biggie Smalls') !== null,
    `${await text(page, '#svTitle')} / ${await text(page, '#svBy')}`);
  check('L shows once a song plays', await within(page, 2000, () => /lyrics/.test(document.querySelector('#svKeys').textContent)) !== null);
  const note = await within(page, 3000, () => /isn’t on it/.test(document.querySelector('#svNote').textContent));
  check('a playlist with a journey: the copy says this song isn\'t on it', note !== null, await text(page, '#svNote'));
  check('no cover in the cache: no cover card', await page.$eval('#svCover', i => !i.getAttribute('src')));
  await shot(page, 'stage-raw-title-1920x1080');
  play({ ...o, id: o.song, journey: null }, { pos0: 10 });
  check('a playlist with no journey says so', await within(page, 3000, () => /has no liner notes yet/.test(document.querySelector('#svNote').textContent)) !== null, await text(page, '#svNote'));
  const c = m.culture_stop;
  play(c, { pos0: 10 });
  await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, c.title);
  await page.keyboard.press('k'); await sleep(300);
  const st = await page.evaluate(() => { const b = document.querySelector('#svStamp'), x = document.querySelector('#svExit').getBoundingClientRect(), r = b.getBoundingClientRect(); return { h: r.height, lh: parseFloat(getComputedStyle(b).fontSize) * 1.8, overlap: r.left < x.right && r.right > x.left && r.top < x.bottom && r.bottom > x.top }; });
  check('the stamp stays on one line', st.h <= st.lh, JSON.stringify(st));
  check('and clear of the full-page chip', !st.overlap, JSON.stringify(st));
  check('the lettered cover stays away on the stage', await page.$eval('#svCover', i => !i.getAttribute('src')));
  const era = await page.$eval('#svEra', n => { const d = n.querySelector('.nw'); return d ? d.getClientRects().length : -1; });
  check('"draft notes, AI-written" never breaks', era === 1 || era === -1, String(era));
  await page.keyboard.press('k');
  stopBridge();
  await within(page, 3000, () => /bridge/i.test(document.querySelector('#svTitle').textContent));
  const codes = await page.$$eval('#svNote code', cs => cs.map(c => [c.textContent, c.getClientRects().length, getComputedStyle(c).whiteSpace]));
  check('bridge down: both commands on one line each, in mono', codes.length === 2 && codes.every(x => x[1] === 1 && x[2] === 'nowrap'), JSON.stringify(codes));
  check('no stray word in the stamp', (await text(page, '#svStamp')) === '');
  await startBridge();
  await ctx.close();
};

scenarios['small-screens'] = async () => {
  const m = M(), s = m.culture_stop;
  put({ lyrics: { [s.yt]: lyricsFor(2, 3).map((l, k) => ({ ...l, text: k % 2 ? l.text : 'a much longer made-up lyric line that takes two lines on a small screen' })) }, theme: null });
  for (const [w, h] of [[1366, 768], [1024, 600]]) {
    play(s, { pos0: 20 });
    const { ctx, page } = await newPage({ viewport: { width: w, height: h } });
    await page.goto(B + '/p/downtempo-drift.html#companion');
    await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, s.title);
    await within(page, 5000, () => document.querySelector('#stagev').classList.contains('lyrics'));
    await sleep(2500);
    const r = await page.evaluate(() => {
      const box = q => document.querySelector(q).getBoundingClientRect();
      const lyr = box('.sv-lyr'), now = box('#svLyric .now'), nx = box('#svLyric .nx'), note = document.querySelector('#svNote');
      return { lyrics: now.bottom <= lyr.bottom - 2 && nx.bottom <= lyr.bottom - 2 && now.top >= lyr.top, notes: note.scrollHeight <= note.clientHeight + 2 || note.classList.contains('clip') };
    });
    check(`${w}x${h}: lyric lines whole`, r.lyrics, JSON.stringify(r));
    check(`${w}x${h}: notes whole or faded, never cut`, r.notes, JSON.stringify(r));
    await shot(page, `stage-default-${w}x${h}`);
    await ctx.close();
  }
};

const THEMES = {
  dark: { ok: true, name: 'test-dark', mode: 'dark', font: 'JetBrainsMono Nerd Font', colors: { background: '#1a1b26', foreground: '#a9b1d6', accent: '#7aa2f7', blue: '#7aa2f7', cyan: '#7dcfff', green: '#9ece6a', yellow: '#e0af68', red: '#f7768e', magenta: '#bb9af7', muted: '#565f89', lighter_background: '#24283b' } },
  // The owner's theme on WS2 (GET /theme from the running bridge, 2026-10-20).
  'astro-arc': { ok: true, name: 'astro-arc', mode: 'dark', font: 'JetBrainsMono Nerd Font', colors: { accent: '#5694b4', selection: '#424457', muted: '#6c6f81', background: '#2e251c', dark_background: '#271f18', darker_background: '#1e1812', lighter_background: '#3e3226', foreground: '#cead7b', dark_foreground: '#6c7086', light_foreground: '#bac2de', bright_foreground: '#cead7b', red: '#f38ba8', yellow: '#f9e2af', orange: '#f6b6ab', green: '#a6e3a1', cyan: '#94e2d5', blue: '#5694b4', magenta: '#f5c2e7', brown: '#9f8883' } },
  light: { ok: true, name: 'test-light', mode: 'light', font: 'JetBrainsMono Nerd Font', colors: { background: '#e1e2e7', foreground: '#3760bf', accent: '#2e7de9', blue: '#2e7de9', cyan: '#007197', green: '#587539', yellow: '#8c6c3e', red: '#f52a65', magenta: '#9854f1', muted: '#a8aecb', dark_background: '#d0d5e3' } },
};

scenarios['themes-contrast-shots'] = async () => {
  const m = M(), s = m.culture_stop, v = m.video_stops[1];
  for (const [name, theme] of [...Object.entries(THEMES), ['default', null]]) {
    put({ theme, lyrics: { [s.yt]: lyricsFor(2, 4), [v.yt]: lyricsFor(2, 4) } });
    for (const [w, h] of theme ? [[1920, 1080], [1366, 768], [1024, 600], [390, 844]] : [[1920, 1080]]) {
      const stop = w === 390 ? v : s;
      play(stop, { pos0: 30 });
      const { ctx, page } = await newPage({ viewport: { width: w, height: h }, colorScheme: name === 'light' ? 'light' : 'dark' });
      await page.goto(B + '/p/downtempo-drift.html#companion');
      await within(page, 3000, t => document.querySelector('#svTitle').textContent === t, stop.title);
      if (theme) await within(page, 6000, n => document.documentElement.dataset.omarchy === n, theme.name);
      await sleep(3500);
      await shot(page, `stage-${name}-${w}x${h}`);
      if (w === 1920) {
        const bad = await page.evaluate(() => {
          const lum = c => { const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
          const rgb = s => (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
          const bg = rgb(getComputedStyle(document.querySelector('#stagev')).backgroundColor);
          const out = [];
          const want = [['.pl', 18], ['#svStamp', 18], ['#svEra', 18], ['#svBy', 18], ['#svPos', 24], ['#svClock', 24], ['#svLive', 18], ['#svTrailNote', 18], ['#svKeys', 18], ['#svLyric .nx', 18], ['#svLyric .now', 18], ['#svNote p', 18], ['#svExit', 18], ['#svLyrState', 18]];
          for (const [sel, min] of want) for (const n of document.querySelectorAll(sel)) {
            if (!n.getClientRects().length || !n.textContent.trim()) continue;
            const cs = getComputedStyle(n), fill = cs.webkitTextFillColor && !/rgba?\(0, 0, 0, 0\)/.test(cs.webkitTextFillColor) ? cs.webkitTextFillColor : cs.color;
            const c = rgb(fill), [x, y] = [lum(c), lum(bg)].sort((p, q) => q - p), ratio = (x + 0.05) / (y + 0.05), size = parseFloat(cs.fontSize);
            if (ratio < 4.5 || size < min) out.push(`${sel} ${ratio.toFixed(2)}:1 ${size}px`);
          }
          return out;
        });
        check(`${name}: stage text at least 4.5:1 and 18 px (24 px for position and clock)`, !bad.length, bad.join('; '));
      }
      await ctx.close();
    }
  }
  put({ theme: null });
};

/* ---------- run ---------- */
(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  execFileSync(PY, [path.join(ROOT, 'tools/fake_bridge.py'), 'fixtures', FX], { env: { ...process.env, PYTHONPATH: ROOT }, stdio: 'inherit' });
  // Without ffmpeg there's no test video and every video check is skipped; under CI that's a failure, not a pass.
  if (!M().videos) { if (process.env.CI) failures.push('fixtures: no test video (ffmpeg missing)'); else console.log('NOTE: no ffmpeg, so the video checks are skipped'); }
  put({ now: { ok: false, reason: 'stopped' }, lyrics: {}, history: [], theme: null });
  clearLogs();
  await startBridge();
  browser = await chromium.launch({ executablePath: EXE, headless: true });
  const names = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(scenarios);
  // The suite's waits must wait, under the page's own CSP: a wait that gives up at once makes every
  // "nothing happens" check pass for free.
  {
    current = 'harness';
    const { ctx, page } = await newPage();
    await page.goto(B + '/p/downtempo-drift.html');
    const t = Date.now(), r = await within(page, 400, () => false), took = Date.now() - t;
    check('waits really wait under the page\'s CSP', r === null && took >= 380 && (await within(page, 2000, () => !!window.LinerNotes)) !== null, `${took} ms`);
    await ctx.close();
  }
  try {
    for (const n of names) {
      if (!scenarios[n]) { failures.push(`no scenario ${n}`); continue; }
      current = n; console.log(n);
      try { await scenarios[n](); } catch (e) { check('ran to the end', false, e.message.split('\n')[0]); }
    }
  } finally {
    await browser.close().catch(() => {});
    stopBridge();
  }
  if (forbidden.size) failures.push('requests to YouTube/Google: ' + [...forbidden].join(' '));
  if (offsite.size) console.log('aborted off-site requests:', [...offsite].join(' '));
  console.log(failures.length ? `\n${failures.length} failed:\n  ` + failures.join('\n  ') : '\nall passed');
  console.log('screenshots in', SHOTS);
  process.exit(failures.length ? 1 : 0);
})();
