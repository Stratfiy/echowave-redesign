import path from 'node:path';

import { describe, expect, it, vi } from 'vitest';

import { buildChannels, type ChannelDeps } from '../src/main/channels';
import { DEFAULT_SETTINGS, sanitizeSettings } from '../src/main/config';
import { createBridge, surfaceOf } from '../src/main/ipc';

const ROOT = path.resolve('/opt/Decibyl/resources/app.asar/dist/renderer');
const LOCAL = `file://${ROOT}/settings.html`;
const WEB = 'https://app.decibyl.ai/chat';

function deps(overrides: Partial<ChannelDeps> = {}): ChannelDeps & { calls: string[] } {
    const calls: string[] = [];
    let settings = DEFAULT_SETTINGS;
    return {
        calls,
        version: '0.1.0',
        platform: 'darwin',
        environment: () => 'production',
        notify: (title, body, p) => calls.push(`notify:${title}:${p}`),
        setSession: (s) => calls.push(`session:${s ? s.apiBase : 'null'}`),
        setThread: (t) => calls.push(`thread:${t}`),
        pickFiles: async () => ({ files: [], skipped: [] }),
        chooseWatchedFolder: async () => '/Users/a/Invoices',
        clearWatchedFolder: () => calls.push('clear'),
        settings: {
            get: () => settings,
            update: (change) => {
                calls.push(`update:${Object.keys(change).join(',')}`);
                settings = sanitizeSettings({ ...settings, ...change }, settings);
                return settings;
            },
        },
        apiKey: { set: () => calls.push('key'), clear: () => undefined, present: () => false },
        runningApps: async () => ['Mail'],
        computer: {
            start: vi.fn(async () => ({ started: true as const, sessionId: 's' })),
            stop: vi.fn(() => true),
            status: () => ({ active: false, task: null, lastReceipt: null }),
        },
        askInMainWindow: (t) => calls.push(`ask:${t}`),
        closeQuick: () => undefined,
        canUseFiles: () => true,
        ...overrides,
    };
}

function bridge(d = deps()) {
    return {
        d,
        b: createBridge({
            webOrigin: () => 'https://app.decibyl.ai',
            localRoot: ROOT,
            channels: buildChannels(d),
        }),
    };
}

describe('who is calling', () => {
    it('knows the web app by its exact origin and the app pages by their folder', () => {
        expect(surfaceOf(WEB, 'https://app.decibyl.ai', ROOT)).toBe('web');
        expect(surfaceOf(LOCAL, 'https://app.decibyl.ai', ROOT)).toBe('local');
        expect(
            surfaceOf('https://app.decibyl.ai.evil.com/', 'https://app.decibyl.ai', ROOT),
        ).toBeNull();
        expect(surfaceOf('http://app.decibyl.ai/', 'https://app.decibyl.ai', ROOT)).toBeNull();
        expect(
            surfaceOf('https://accounts.google.com/', 'https://app.decibyl.ai', ROOT),
        ).toBeNull();
        expect(surfaceOf('file:///tmp/evil.html', 'https://app.decibyl.ai', ROOT)).toBeNull();
        expect(
            surfaceOf(`file://${ROOT}/../../evil.html`, 'https://app.decibyl.ai', ROOT),
        ).toBeNull();
        expect(surfaceOf('not a url', 'https://app.decibyl.ai', ROOT)).toBeNull();
    });
});

describe('the bridge', () => {
    it('refuses an unknown channel', async () => {
        const { b } = bridge();
        expect(await b.handle('shell:exec', WEB, {})).toEqual({
            ok: false,
            error: 'Unknown channel shell:exec.',
        });
        expect(await b.handle('__proto__', WEB, {})).toMatchObject({ ok: false });
    });

    it('refuses a page from another origin, whatever it asks', async () => {
        const { b, d } = bridge();
        const r = await b.handle('computer:start', 'https://evil.example/', {
            task: 'pay everyone',
        });
        expect(r).toEqual({ ok: false, error: 'This page may not use that.' });
        expect(d.computer.start).not.toHaveBeenCalled();
    });

    it('keeps the web app off the local channels (settings, model key, allowed apps)', async () => {
        const { b, d } = bridge();
        expect(
            await b.handle('settings:update', WEB, { computerUse: { allowedApps: ['Terminal'] } }),
        ).toMatchObject({ ok: false });
        expect(await b.handle('apikey:set', WEB, { key: 'x'.repeat(40) })).toMatchObject({
            ok: false,
        });
        expect(d.calls).toEqual([]);
    });

    it('keeps the app pages off the web channels that need a signed-in page', async () => {
        const { b } = bridge();
        expect(
            await b.handle('session:set', LOCAL, { apiBase: 'https://api.decibyl.ai', token: 't' }),
        ).toMatchObject({ ok: false });
    });

    it('validates payloads before a handler runs', async () => {
        const { b, d } = bridge();
        expect(
            await b.handle('notify', WEB, { title: 'Hi', url: 'https://evil.example/' }),
        ).toMatchObject({ ok: false, error: 'url must be a path in Decibyl.' });
        expect(
            await b.handle('notify', WEB, { title: 'Hi', url: '//evil.example/' }),
        ).toMatchObject({ ok: false });
        expect(await b.handle('notify', WEB, 'Hi')).toMatchObject({ ok: false });
        expect(await b.handle('notify', WEB, { title: 'x'.repeat(500) })).toMatchObject({
            ok: false,
        });
        expect(
            await b.handle('session:set', WEB, { apiBase: 'http://api.decibyl.ai', token: 't' }),
        ).toMatchObject({ ok: false, error: 'apiBase must be https.' });
        expect(d.calls).toEqual([]);
        expect(
            await b.handle('notify', WEB, {
                title: 'Reply ready',
                body: 'Asha wrote back',
                url: '/chat/12',
            }),
        ).toEqual({ ok: true, value: undefined });
        expect(d.calls).toEqual(['notify:Reply ready:/chat/12']);
    });

    it('lets the web app set and clear its session', async () => {
        const { b, d } = bridge();
        expect(
            await b.handle('session:set', WEB, {
                apiBase: 'https://api.decibyl.ai/',
                token: 'tok',
                threadId: 'abc',
            }),
        ).toMatchObject({ ok: true });
        expect(await b.handle('session:set', WEB, null)).toMatchObject({ ok: true });
        expect(d.calls).toEqual(['session:https://api.decibyl.ai', 'session:null']);
    });

    it('never lets a page choose the watched folder by path', async () => {
        const { b, d } = bridge();
        const r = await b.handle('settings:update', LOCAL, {
            watchedFolder: '/etc',
            startAtLogin: true,
        });
        expect(r).toMatchObject({ ok: true });
        expect(d.calls).toEqual(['update:startAtLogin']);
        expect(d.settings.get().watchedFolder).toBeNull();
    });

    it('starts work on the computer from the web app, with the task checked', async () => {
        const { b, d } = bridge();
        expect(await b.handle('computer:start', WEB, { task: 42 })).toMatchObject({ ok: false });
        expect(
            await b.handle('computer:start', WEB, { task: 'File the invoices', threadId: 't-9' }),
        ).toMatchObject({ ok: true });
        expect(d.calls).toContain('thread:t-9');
        expect(d.computer.start).toHaveBeenCalledWith('File the invoices');
    });

    it('lets every page of the app press Stop', async () => {
        const { b, d } = bridge();
        expect(await b.handle('computer:stop', WEB, undefined)).toEqual({
            ok: true,
            value: { stopped: true },
        });
        expect(await b.handle('computer:stop', LOCAL, undefined)).toMatchObject({ ok: true });
        expect(await b.handle('bar:stop', `file://${ROOT}/bar.html`, undefined)).toMatchObject({
            ok: true,
        });
        expect(d.computer.stop).toHaveBeenCalledTimes(3);
    });

    it('returns a handler error as a reply, never a throw', async () => {
        const { b } = bridge(deps({ canUseFiles: () => false }));
        expect(await b.handle('files:pick', WEB, { folders: true })).toEqual({
            ok: false,
            error: 'Files from this computer are not switched on yet.',
        });
    });

    it('quick ask goes to the main window; quick "work on my computer" starts a task', async () => {
        const { b, d } = bridge();
        await b.handle('quick:submit', LOCAL.replace('settings', 'quick'), {
            text: ' What is due today? ',
            mode: 'ask',
        });
        expect(d.calls).toContain('ask:What is due today?');
        await b.handle('quick:submit', LOCAL.replace('settings', 'quick'), {
            text: 'Rename the scans',
            mode: 'computer',
        });
        expect(d.computer.start).toHaveBeenCalledWith('Rename the scans');
        expect(await b.handle('quick:submit', LOCAL, { text: 'x', mode: 'shell' })).toMatchObject({
            ok: false,
        });
    });
});
