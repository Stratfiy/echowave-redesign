import { chromium } from 'playwright';
const BASE = 'http://localhost:8081';
const steps = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH, args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
const scheme = process.env.SCHEME || 'light';
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: scheme, locale: 'en-IN', permissions: ['microphone'], storageState: 'state.json' });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });
globalThis.page = page;
globalThis.shot = async (name, wait = 1200) => { await page.waitForTimeout(wait); await page.screenshot({ path: `out/${name}.png` }); console.log('shot', name, page.url()); };
globalThis.tid = (id) => page.getByTestId(id);
globalThis.go = async (path) => { await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' }); };
for (const step of steps) {
  const mod = await import(`./steps/${step}.mjs`);
  await mod.default();
}
console.log(errors.filter((e) => !e.includes('favicon')).slice(0, 15).join('\n'));
await browser.close();
