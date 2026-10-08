/**
 * The screens the app opens as the web app in an authenticated in-app web
 * view, so nothing is missing on day one. Each is a candidate to replace
 * with a native screen, one at a time: when one is built natively, delete
 * its row here and route the path in src/lib/links.ts.
 *
 * Native today (not in this list): sign in / sign up, Chat (threads, a
 * thread, cards, connect chips, attachments, voice notes), Today, approval,
 * reminder, People, a person, live voice, share, Settings -> Account,
 * Language, Notifications, Privacy (export and erase), Simple mode,
 * biometric unlock, appearance.
 *
 * Grouped as in the web app's Settings (ui/src/app/settings) and profile
 * menu. Titles are the web app's own section names.
 */
export type WebScreen = { path: string; title: string; group: 'personal' | 'connections' | 'advanced' | 'workspace' | 'more' };

export const WEB_SCREENS: readonly WebScreen[] = [
    { path: '/settings/personalization', title: 'Personalization', group: 'personal' },
    { path: '/settings/voice', title: 'Voice', group: 'personal' },
    { path: '/settings/daily-brief', title: 'Daily brief', group: 'personal' },
    { path: '/settings/memory', title: 'Memory', group: 'personal' },
    { path: '/settings/saved', title: 'Saved items', group: 'personal' },
    { path: '/settings/connections', title: 'Connected apps', group: 'connections' },
    { path: '/settings/channels', title: 'Channels', group: 'connections' },
    { path: '/settings/identity', title: 'Decibyl identity', group: 'connections' },
    { path: '/settings/apps', title: 'Apps', group: 'connections' },
    { path: '/settings/phone-number', title: 'Phone number', group: 'connections' },
    { path: '/settings/models', title: 'Models', group: 'advanced' },
    { path: '/settings/skills', title: 'Skills', group: 'advanced' },
    { path: '/settings/knowledge', title: 'Knowledge', group: 'advanced' },
    { path: '/settings/developer', title: 'Developer', group: 'advanced' },
    { path: '/settings/advanced', title: 'Advanced', group: 'advanced' },
    { path: '/settings/general', title: 'Workspace', group: 'workspace' },
    { path: '/settings/team', title: 'Team', group: 'workspace' },
    { path: '/settings/company', title: 'Company', group: 'workspace' },
    { path: '/settings/compliance', title: 'Compliance', group: 'workspace' },
    { path: '/agents', title: 'Agents', group: 'more' },
    { path: '/activity', title: 'Activity', group: 'more' },
    { path: '/care', title: 'Care', group: 'more' },
    { path: '/learning', title: 'Learning', group: 'more' },
    { path: '/meetings', title: 'Meetings', group: 'more' },
    { path: '/follow-ups', title: 'Follow-ups', group: 'more' },
    { path: '/saved-reports', title: 'Saved reports', group: 'more' },
    { path: '/trackers', title: 'Trackers', group: 'more' },
    { path: '/help', title: 'Help', group: 'more' },
];

/** Only same-app paths: the web view never becomes a browser for anywhere. */
export function safeWebPath(path: string | undefined | null): string {
    const p = (path || '/').trim();
    if (!p.startsWith('/') || p.startsWith('//') || /^\/\\/.test(p)) return '/';
    return p;
}

/**
 * The page the web view loads first. It hands the app's session to the web
 * app the way the web's own sign-in does (POST /api/auth/session sets the
 * httpOnly cookie on the web app's origin), then goes to the screen. It is
 * loaded with the web app as its base URL, so the request is same-origin
 * and the token never appears in an address or a log.
 */
export function bootstrapHtml(token: string, user: unknown, path: string): string {
    const payload = JSON.stringify({ token, user }).replace(/</g, '\\u003c');
    const target = JSON.stringify(safeWebPath(path)).replace(/</g, '\\u003c');
    return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="font-family:system-ui;color:#5d5d5d;padding:24px">
<script>
fetch('/api/auth/session',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(${payload})})
  .then(function(r){ if(!r.ok) throw new Error(String(r.status)); location.replace(${target}); })
  .catch(function(){ document.body.textContent='This screen could not be opened.'; window.ReactNativeWebView&&window.ReactNativeWebView.postMessage('failed'); });
</script></body></html>`;
}
