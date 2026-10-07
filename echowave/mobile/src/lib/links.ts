/**
 * Every way into the app, mapped to one native route.
 *
 * Three sources speak in paths:
 *
 * - `decibyl://` links, the same scheme the desktop app registers
 *   (desktop/src/main/deeplink.ts): chat/<id>, thread/<id>, approvals/<id>,
 *   reminders/<id>, today, tasks/..., ask?q=...
 * - universal links / App Links on the web app's own addresses
 *   (https://app.decibyl.ai/overview?thread=..., /tasks/approvals/<id>),
 *   so a link shared from the web opens the app where it is installed;
 * - push notifications, whose `data.url` is the web path the server already
 *   uses for web push (api/services/identity/mobile_push.py).
 *
 * A path with no native screen is not dropped: it opens the same page of the
 * web app in the authenticated web view, so nothing a link points at is ever
 * missing from the app.
 */

const SAFE_ID = /^[A-Za-z0-9_-]{1,80}$/;

export const WEB_HOSTS = ['app.decibyl.ai', 'staging.decibyl.ai'];

type Parsed = { segments: string[]; query: Record<string, string> };

function parsePath(pathWithQuery: string): Parsed {
    const [rawPath, rawQuery = ''] = pathWithQuery.split('?', 2);
    const segments = rawPath
        .split('#')[0]
        .split('/')
        .filter(Boolean)
        .map((s) => {
            try {
                return decodeURIComponent(s);
            } catch {
                return s;
            }
        });
    const query: Record<string, string> = {};
    for (const pair of rawQuery.split('#')[0].split('&')) {
        if (!pair) continue;
        const [k, v = ''] = pair.split('=');
        try {
            query[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, ' '));
        } catch {
            query[k] = v;
        }
    }
    return { segments, query };
}

function webView(path: string): string {
    return `/web?path=${encodeURIComponent(path.startsWith('/') ? path : `/${path}`)}`;
}

/** The native route for a path from any of the three sources. */
export function routeForPath(pathWithQuery: string): string {
    const { segments, query } = parsePath(pathWithQuery || '/');
    const [first, second, third] = segments;
    const id = (value: string | undefined) => (value && SAFE_ID.test(value) ? value : null);

    if (!first) return '/';
    switch (first) {
        case 'overview':
        case 'home':
            if (query.thread && id(query.thread)) return `/chat/${query.thread}`;
            return '/';
        case 'chat':
        case 'thread':
            if (id(second)) return `/chat/${second}`;
            return '/';
        case 'ask': {
            const q = (query.q || '').slice(0, 2000);
            return q ? `/chat/new?q=${encodeURIComponent(q)}` : '/chat/new';
        }
        case 'today':
            return '/today';
        case 'approvals':
            return id(second) ? `/approvals/${second}` : '/today';
        case 'reminders':
            if (second === 'new') return '/reminders/new';
            return id(second) ? `/reminders/${second}` : '/today';
        case 'tasks':
            if (!second) return '/today';
            if (second === 'approvals' && id(third)) return `/approvals/${third}`;
            if (second === 'reminders' && third === 'new') return '/reminders/new';
            if (second === 'reminders' && id(third)) return `/reminders/${third}`;
            return webView(pathWithQuery);
        case 'people':
            return id(second) ? `/person/${second}` : '/people';
        case 'share': {
            const q = pathWithQuery.includes('?') ? pathWithQuery.slice(pathWithQuery.indexOf('?')) : '';
            return `/share${q}`;
        }
        case 'voice':
        case 'talk':
            return '/voice';
        case 'settings':
            if (!second) return '/settings';
            if (['account', 'notifications', 'privacy', 'language'].includes(second)) return `/settings/${second}`;
            return webView(pathWithQuery);
        default:
            return webView(pathWithQuery);
    }
}

/**
 * A full URL (`decibyl://chat/1`, `https://app.decibyl.ai/overview?thread=1`,
 * `exp+decibyl://...` in development) to a native route, or null when the URL
 * is not one of ours (left to the router's own handling).
 */
export function routeForUrl(url: string | null | undefined): string | null {
    if (!url) return null;
    const match = /^([a-z][a-z0-9+.-]*):\/\/([^/?#]*)(.*)$/i.exec(url.trim());
    if (!match) return url.startsWith('/') ? routeForPath(url) : null;
    const [, scheme, host, rest] = match;
    const lower = scheme.toLowerCase();
    if (lower === 'https' || lower === 'http') {
        if (!WEB_HOSTS.includes(host.toLowerCase())) return null;
        return routeForPath(rest || '/');
    }
    if (lower === 'decibyl' || lower.endsWith('+decibyl')) {
        // decibyl://chat/1 -> host "chat", rest "/1".
        return routeForPath(`/${host}${rest}`);
    }
    return null;
}

/** The web app path for a native route, for the web view and "open on web". */
export function webPathForThread(threadId: string | null): string {
    return threadId && threadId !== 'main' ? `/overview?thread=${encodeURIComponent(threadId)}` : '/overview';
}
