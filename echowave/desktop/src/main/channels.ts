/**
 * Every IPC channel, which pages may use it, how its payload is checked,
 * and what it does -- with the Electron calls behind small interfaces so the
 * whole table can be tested without Electron (test/ipc.test.ts).
 */

import type { ComputerSessions } from './computer';
import type { Settings } from './config';
import type { PickResult } from './files';
import { BadPayload, type ChannelSpec, none, obj, str, webPath } from './ipc';
import type { Session } from '../computer-use/approvals-http';

export interface ChannelDeps {
    version: string;
    platform: string;
    environment: () => string;
    notify: (title: string, body: string, path: string) => void;
    setSession: (session: Session | null) => void;
    /** The Decibyl thread a task was started from: its cards go there. */
    setThread: (threadId: string | null) => void;
    pickFiles: (folders: boolean) => Promise<PickResult>;
    chooseWatchedFolder: () => Promise<string | null>;
    clearWatchedFolder: () => void;
    settings: { get: () => Settings; update: (change: Record<string, unknown>) => Settings };
    apiKey: { set: (key: string) => void; clear: () => void; present: () => boolean };
    runningApps: () => Promise<string[]>;
    computer: Pick<ComputerSessions, 'start' | 'stop' | 'status'>;
    askInMainWindow: (text: string) => void;
    closeQuick: () => void;
    /** Whether the web app may show desktop things at all (desktop_app). */
    canUseFiles: () => boolean;
}

const WEB = ['web'] as const;
const LOCAL = ['local'] as const;
const BOTH = ['web', 'local'] as const;

function spec<T>(s: ChannelSpec<T>): ChannelSpec<never> {
    return s as unknown as ChannelSpec<never>;
}

export function buildChannels(deps: ChannelDeps): Record<string, ChannelSpec<never>> {
    return {
        'desktop:info': spec({
            surfaces: BOTH,
            validate: none,
            handle: () => ({
                version: deps.version,
                platform: deps.platform,
                environment: deps.environment(),
            }),
        }),

        notify: spec({
            surfaces: WEB,
            validate: (p) => {
                const o = obj(p);
                return {
                    title: str(o.title, 'title', 120),
                    body: str(o.body, 'body', 500, { optional: true }),
                    url: webPath(o.url),
                };
            },
            handle: (n) => {
                deps.notify(n.title, n.body, n.url || '/');
            },
        }),

        'session:set': spec({
            surfaces: WEB,
            validate: (p): Session | null => {
                if (p === null) return null;
                const o = obj(p);
                const apiBase = str(o.apiBase, 'apiBase', 300);
                let url: URL;
                try {
                    url = new URL(apiBase);
                } catch {
                    throw new BadPayload('apiBase must be a URL.');
                }
                if (
                    url.protocol !== 'https:' &&
                    !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))
                ) {
                    throw new BadPayload('apiBase must be https.');
                }
                const threadId = str(o.threadId, 'threadId', 64, { optional: true });
                return {
                    apiBase: url.origin + url.pathname.replace(/\/$/, ''),
                    token: str(o.token, 'token', 8000),
                    threadId: threadId || null,
                };
            },
            handle: (s) => {
                deps.setSession(s);
            },
        }),

        'files:pick': spec({
            surfaces: WEB,
            validate: (p) => ({
                folders: p !== undefined && p !== null && obj(p).folders === true,
            }),
            handle: async ({ folders }) => {
                if (!deps.canUseFiles())
                    throw new Error('Files from this computer are not switched on yet.');
                return deps.pickFiles(folders);
            },
        }),

        'watch:get': spec({
            surfaces: WEB,
            validate: none,
            handle: () => ({ folder: deps.settings.get().watchedFolder }),
        }),
        'watch:choose': spec({
            surfaces: BOTH,
            validate: none,
            handle: async () => {
                if (!deps.canUseFiles())
                    throw new Error('Files from this computer are not switched on yet.');
                return { folder: await deps.chooseWatchedFolder() };
            },
        }),
        'watch:clear': spec({
            surfaces: BOTH,
            validate: none,
            handle: () => deps.clearWatchedFolder(),
        }),

        'computer:start': spec({
            surfaces: WEB,
            validate: (p) => {
                const o = obj(p);
                const threadId = str(o.threadId, 'threadId', 64, { optional: true });
                return { task: str(o.task, 'task', 2000), threadId: threadId || null };
            },
            handle: ({ task, threadId }) => {
                deps.setThread(threadId);
                return deps.computer.start(task);
            },
        }),
        // Stop is never refused: any page of this app can press it.
        'computer:stop': spec({
            surfaces: BOTH,
            validate: none,
            handle: () => ({ stopped: deps.computer.stop() }),
        }),
        'bar:stop': spec({
            surfaces: LOCAL,
            validate: none,
            handle: () => ({ stopped: deps.computer.stop() }),
        }),
        'computer:status': spec({
            surfaces: BOTH,
            validate: none,
            handle: () => deps.computer.status(),
        }),

        'settings:get': spec({
            surfaces: LOCAL,
            validate: none,
            handle: () => deps.settings.get(),
        }),
        'settings:update': spec({
            surfaces: LOCAL,
            validate: (p) => {
                const o = obj(p);
                // The watched folder only changes through the folder picker.
                const { watchedFolder: _ignored, ...rest } = o;
                return rest;
            },
            handle: (change) => deps.settings.update(change),
        }),
        'apikey:set': spec({
            surfaces: LOCAL,
            validate: (p) => {
                const key = str(obj(p).key, 'key', 400).trim();
                if (key.length < 20) throw new BadPayload('That does not look like a key.');
                return key;
            },
            handle: (key) => deps.apiKey.set(key),
        }),
        'apikey:clear': spec({
            surfaces: LOCAL,
            validate: none,
            handle: () => deps.apiKey.clear(),
        }),
        'apikey:present': spec({
            surfaces: LOCAL,
            validate: none,
            handle: () => deps.apiKey.present(),
        }),
        'apps:running': spec({ surfaces: LOCAL, validate: none, handle: () => deps.runningApps() }),

        'quick:submit': spec({
            surfaces: LOCAL,
            validate: (p) => {
                const o = obj(p);
                const mode = o.mode === 'computer' ? 'computer' : o.mode === 'ask' ? 'ask' : null;
                if (!mode) throw new BadPayload('mode must be ask or computer.');
                return { text: str(o.text, 'text', 2000).trim(), mode };
            },
            handle: async ({ text, mode }) => {
                if (!text) throw new Error('Say what you need.');
                if (mode === 'computer') return deps.computer.start(text);
                deps.askInMainWindow(text);
                return { asked: true };
            },
        }),
        'quick:close': spec({ surfaces: LOCAL, validate: none, handle: () => deps.closeQuick() }),
    };
}
