// Writes ./bootstrap.html: the web view's first page, for steps/webview.mjs.
// TOKEN=<a local token> npx tsx e2e/bootstrap.ts > bootstrap.html
import { bootstrapHtml } from '../src/lib/webScreens';
process.stdout.write(bootstrapHtml(process.env.TOKEN as string, { id: 1, email: 'asha@example.com' }, '/settings/memory'));
