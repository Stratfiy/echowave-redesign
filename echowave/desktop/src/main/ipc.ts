/**
 * The bridge between the pages and the main process.
 *
 * Two kinds of page talk to main, and each may only use its own channels:
 *
 * - **web**: the Decibyl web app, loaded from the configured URL. It may
 *   notify, pick files, set its session, start and stop "work on my
 *   computer". Its origin must be exactly the configured web origin; a page
 *   the web app navigated to elsewhere (a link, an OAuth screen) gets
 *   nothing.
 * - **local**: the app's own windows (quick ask, the working bar, settings),
 *   loaded from files inside the app. They may read and change settings and
 *   press Stop.
 *
 * Every payload is checked by the channel's own validator before a handler
 * sees it. An unknown channel, a wrong origin or a bad payload is an error
 * the caller can read -- never a silent no-op, never a crash in main.
 */

export type Surface = 'web' | 'local';

export interface ChannelSpec<T = unknown> {
    surfaces: readonly Surface[];
    /** Throws on a bad payload; returns the clean value. */
    validate: (payload: unknown) => T;
    handle: (
        payload: T,
        ctx: { surface: Surface; senderUrl: string },
    ) => unknown | Promise<unknown>;
}

export type Reply = { ok: true; value: unknown } | { ok: false; error: string };

export class BadPayload extends Error {}

export interface BridgeOptions {
    /** The configured web origin, e.g. https://app.decibyl.ai. Read per call: it can change in settings. */
    webOrigin: () => string;
    /** File URLs under this directory are the app's own pages. */
    localRoot: string;
    channels: Record<string, ChannelSpec<never>>;
}

export function surfaceOf(senderUrl: string, webOrigin: string, localRoot: string): Surface | null {
    let url: URL;
    try {
        url = new URL(senderUrl);
    } catch {
        return null;
    }
    if (url.protocol === 'file:') {
        const root = localRoot.replace(/\\/g, '/').replace(/\/$/, '');
        const file = decodeURIComponent(url.pathname).replace(/\\/g, '/');
        // Windows file URLs are /C:/...; roots are C:/...
        const normal = file.replace(/^\/([A-Za-z]:)/, '$1');
        return normal.startsWith(`${root}/`) && !normal.includes('/../') ? 'local' : null;
    }
    try {
        return url.origin === new URL(webOrigin).origin ? 'web' : null;
    } catch {
        return null;
    }
}

export function createBridge(opts: BridgeOptions) {
    return {
        channels: Object.keys(opts.channels),
        async handle(channel: string, senderUrl: string, payload: unknown): Promise<Reply> {
            const spec = Object.prototype.hasOwnProperty.call(opts.channels, channel)
                ? opts.channels[channel]
                : undefined;
            if (!spec) return { ok: false, error: `Unknown channel ${channel}.` };
            const surface = surfaceOf(senderUrl, opts.webOrigin(), opts.localRoot);
            if (!surface || !spec.surfaces.includes(surface)) {
                return { ok: false, error: 'This page may not use that.' };
            }
            let clean: never;
            try {
                clean = spec.validate(payload) as never;
            } catch (err) {
                return {
                    ok: false,
                    error: err instanceof BadPayload ? err.message : 'Bad request.',
                };
            }
            try {
                return { ok: true, value: await spec.handle(clean, { surface, senderUrl }) };
            } catch (err) {
                return { ok: false, error: (err as Error)?.message || 'Something went wrong.' };
            }
        },
    };
}

// --- small validators ---------------------------------------------------------

export function obj(payload: unknown): Record<string, unknown> {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload))
        throw new BadPayload('Expected an object.');
    return payload as Record<string, unknown>;
}

export function str(value: unknown, field: string, max: number, { optional = false } = {}): string {
    if (value === undefined || value === null) {
        if (optional) return '';
        throw new BadPayload(`${field} is required.`);
    }
    if (typeof value !== 'string') throw new BadPayload(`${field} must be text.`);
    if (value.length > max) throw new BadPayload(`${field} is too long.`);
    return value;
}

export function none(payload: unknown): undefined {
    if (
        payload !== undefined &&
        payload !== null &&
        !(typeof payload === 'object' && Object.keys(payload as object).length === 0)
    ) {
        throw new BadPayload('This takes nothing.');
    }
    return undefined;
}

/** A path inside the web app, never an absolute URL to somewhere else. */
export function webPath(value: unknown, field = 'url'): string {
    const path = str(value, field, 2000, { optional: true });
    if (!path) return '';
    if (!path.startsWith('/') || path.startsWith('//') || /[\s\\]/.test(path))
        throw new BadPayload(`${field} must be a path in Decibyl.`);
    return path;
}
