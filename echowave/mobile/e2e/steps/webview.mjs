import { readFileSync } from 'node:fs';
export default async () => {
  // 1. The native web view's first page, on the web app's origin, as the
  //    WebView loads it (html + baseUrl): it hands over the session, then goes.
  const html = readFileSync('./bootstrap.html', 'utf8');
  await page.route('http://localhost:3000/__webview_bootstrap', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html }));
  await page.goto('http://localhost:3000/__webview_bootstrap', { waitUntil: 'commit', timeout: 120000 });
  await page.waitForURL('**/settings/memory', { timeout: 240000 });
  await page.waitForTimeout(8000);
  await shot('36-webview-handoff-memory', 500);
  // 2. The app's web view screen (iframe in the web build), same session.
  await go('/web?path=%2Fsettings%2Fmemory&title=Memory');
  await page.waitForTimeout(9000);
  await shot('37-webview-screen', 500);
};
