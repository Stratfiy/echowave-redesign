/**
 * What a page in this app can reach: `window.decibylDesktop`.
 *
 * Context isolation is on and Node is off in every window, so this object
 * is the whole surface. Which half a page gets depends on where it was
 * loaded from, and main checks the sender's origin again on every call
 * (src/main/ipc.ts) -- this file choosing is a convenience, not the guard.
 */

import { contextBridge, ipcRenderer } from 'electron';

type Reply = { ok: true; value: unknown } | { ok: false; error: string };

async function call(channel: string, payload?: unknown): Promise<unknown> {
    const reply = (await ipcRenderer.invoke('decibyl', channel, payload)) as Reply;
    if (!reply.ok) throw new Error(reply.error);
    return reply.value;
}

const EVENTS = new Set([
    'files:incoming',
    'computer:event',
    'quick:prefill',
    'bar:update',
    'settings:changed',
]);

function on(event: string, callback: (data: unknown) => void): () => void {
    if (!EVENTS.has(event)) throw new Error(`Unknown event ${event}`);
    const listener = (_e: unknown, data: unknown) => callback(data);
    ipcRenderer.on(event, listener);
    return () => ipcRenderer.removeListener(event, listener);
}

const local = location.protocol === 'file:';

const web = {
    isDesktop: true,
    info: () => call('desktop:info'),
    notify: (n: { title: string; body?: string; url?: string }) => call('notify', n),
    setSession: (s: { apiBase: string; token: string; threadId?: string | null } | null) =>
        call('session:set', s),
    pickFiles: (o: { folders?: boolean } = {}) => call('files:pick', o),
    watchedFolder: {
        get: () => call('watch:get'),
        choose: () => call('watch:choose'),
        clear: () => call('watch:clear'),
    },
    computer: {
        start: (task: string, threadId?: string | null) =>
            call('computer:start', { task, threadId: threadId ?? null }),
        stop: () => call('computer:stop'),
        status: () => call('computer:status'),
        onEvent: (cb: (event: unknown) => void) => on('computer:event', cb),
    },
    onFiles: (cb: (files: unknown) => void) => on('files:incoming', cb),
};

const own = {
    isDesktop: true,
    settings: {
        get: () => call('settings:get'),
        update: (change: Record<string, unknown>) => call('settings:update', change),
        onChanged: (cb: (s: unknown) => void) => on('settings:changed', cb),
    },
    apiKey: {
        set: (key: string) => call('apikey:set', { key }),
        clear: () => call('apikey:clear'),
        present: () => call('apikey:present'),
    },
    apps: { running: () => call('apps:running') },
    quick: {
        submit: (text: string, mode: 'ask' | 'computer') => call('quick:submit', { text, mode }),
        close: () => call('quick:close'),
        onPrefill: (cb: (text: unknown) => void) => on('quick:prefill', cb),
    },
    bar: {
        stop: () => call('bar:stop'),
        onUpdate: (cb: (u: unknown) => void) => on('bar:update', cb),
    },
    computer: { status: () => call('computer:status') },
    watchedFolder: { choose: () => call('watch:choose'), clear: () => call('watch:clear') },
};

contextBridge.exposeInMainWorld('decibylDesktop', local ? own : web);
