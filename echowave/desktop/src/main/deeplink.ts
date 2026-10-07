/**
 * decibyl:// links.
 *
 * `decibyl://chat/123`        -> the web app at /chat/123
 * `decibyl://today`           -> /today
 * `decibyl://ask?q=...`       -> the quick ask window, prefilled
 *
 * Only the destinations below; anything else opens the app at its home
 * rather than a path a link elsewhere made up. A link can only navigate --
 * it never starts work on the computer, never approves anything.
 */

export type DeepLink = { kind: 'open'; path: string } | { kind: 'ask'; text: string };

export const PROTOCOL = 'decibyl';

const DESTINATIONS = new Set([
    'chat',
    'today',
    'settings',
    'agents',
    'thread',
    'tasks',
    'activity',
    'approvals',
]);
const SAFE_SEGMENT = /^[A-Za-z0-9_-]{1,80}$/;
const SAFE_QUERY_KEY = /^[A-Za-z0-9_]{1,40}$/;

export function parseDeepLink(raw: string): DeepLink | null {
    let url: URL;
    try {
        url = new URL(raw);
    } catch {
        return null;
    }
    if (url.protocol !== `${PROTOCOL}:`) return null;
    // decibyl://chat/1 parses with host "chat" and path "/1".
    const segments = [url.hostname, ...url.pathname.split('/')].filter(Boolean);
    const [first, ...rest] = segments;
    if (!first) return { kind: 'open', path: '/' };
    if (first === 'ask') {
        return { kind: 'ask', text: (url.searchParams.get('q') ?? '').slice(0, 2000) };
    }
    if (!DESTINATIONS.has(first) || !rest.every((s) => SAFE_SEGMENT.test(s))) {
        return { kind: 'open', path: '/' };
    }
    const query = new URLSearchParams();
    for (const [key, value] of url.searchParams) {
        if (SAFE_QUERY_KEY.test(key)) query.set(key, value.slice(0, 500));
    }
    const qs = query.toString();
    return { kind: 'open', path: `/${[first, ...rest].join('/')}${qs ? `?${qs}` : ''}` };
}

/** The first decibyl:// argument (Windows passes links on the command line). */
export function linkFromArgv(argv: string[]): string | null {
    return argv.find((a) => a.startsWith(`${PROTOCOL}://`)) ?? null;
}
