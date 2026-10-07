import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

import { CEILINGS } from '../src/computer-use/limits';
import {
    DEFAULT_SETTINGS,
    ENVIRONMENTS,
    SettingsStore,
    resolveWebUrl,
    rulesOf,
    sanitizeSettings,
} from '../src/main/config';
import { linkFromArgv, parseDeepLink } from '../src/main/deeplink';
import { MAX_FILES, isIgnorable, readPicked, readWatched, watchFolder } from '../src/main/files';

const tmp: string[] = [];
function dir(): string {
    const d = fs.mkdtempSync(path.join(os.tmpdir(), 'decibyl-'));
    tmp.push(d);
    return d;
}
afterEach(() => {
    for (const d of tmp.splice(0)) fs.rmSync(d, { recursive: true, force: true });
});

describe('which Decibyl opens', () => {
    it('is production unless told otherwise', () => {
        expect(resolveWebUrl(DEFAULT_SETTINGS)).toBe(ENVIRONMENTS.production);
        expect(resolveWebUrl(DEFAULT_SETTINGS, ['--env=staging'])).toBe(ENVIRONMENTS.staging);
        expect(resolveWebUrl(DEFAULT_SETTINGS, [], { DECIBYL_ENV: 'local' })).toBe(
            'http://localhost:3000',
        );
        expect(
            resolveWebUrl(DEFAULT_SETTINGS, [], { DECIBYL_URL: 'https://pr-12.decibyl.ai/' }),
        ).toBe('https://pr-12.decibyl.ai');
        expect(resolveWebUrl({ environment: 'custom', customUrl: 'http://evil.example' })).toBe(
            ENVIRONMENTS.production,
        );
        expect(resolveWebUrl(DEFAULT_SETTINGS, [], { DECIBYL_URL: 'http://evil.example' })).toBe(
            ENVIRONMENTS.production,
        );
    });
});

describe('settings', () => {
    it('start at login is off until the person turns it on', () => {
        expect(DEFAULT_SETTINGS.startAtLogin).toBe(false);
        expect(DEFAULT_SETTINGS.watchedFolder).toBeNull();
        expect(DEFAULT_SETTINGS.computerUse.allowedApps).toEqual([]);
    });

    it('refuses never-touch apps, duplicates and silly limits', () => {
        const s = sanitizeSettings({
            computerUse: {
                allowedApps: ['Mail', 'mail', '1Password', 'System Settings', '', 'Notes'],
                limits: { maxSteps: 1e9, maxSeconds: -1, maxCostUsd: 'x' },
            },
            shortcut: 'rm -rf /',
            environment: 'mars',
        });
        expect(s.computerUse.allowedApps).toEqual(['Mail', 'Notes']);
        expect(s.computerUse.limits.maxSteps).toBe(CEILINGS.maxSteps);
        expect(s.computerUse.limits.maxSeconds).toBe(
            DEFAULT_SETTINGS.computerUse.limits.maxSeconds,
        );
        expect(s.shortcut).toBe(DEFAULT_SETTINGS.shortcut);
        expect(s.environment).toBe('production');
        expect(rulesOf(s)).toEqual({ mail: { allowed: true }, notes: { allowed: true } });
    });

    it('persists what it kept', () => {
        const file = path.join(dir(), 'settings.json');
        new SettingsStore(file).update({
            startAtLogin: true,
            computerUse: { allowedApps: ['Excel'] },
        });
        const again = new SettingsStore(file).get();
        expect(again.startAtLogin).toBe(true);
        expect(again.computerUse.allowedApps).toEqual(['Excel']);
    });
});

describe('decibyl:// links', () => {
    it('opens known destinations only, and never acts', () => {
        expect(parseDeepLink('decibyl://chat/123?thread=ab')).toEqual({
            kind: 'open',
            path: '/chat/123?thread=ab',
        });
        expect(parseDeepLink('decibyl://today')).toEqual({ kind: 'open', path: '/today' });
        expect(parseDeepLink('decibyl://ask?q=what%20is%20due')).toEqual({
            kind: 'ask',
            text: 'what is due',
        });
        expect(parseDeepLink('decibyl://evil/..%2F..%2Fsettings')).toEqual({
            kind: 'open',
            path: '/',
        });
        expect(parseDeepLink('decibyl://chat/..%2Fx')).toEqual({ kind: 'open', path: '/' });
        expect(parseDeepLink('https://app.decibyl.ai/chat')).toBeNull();
        expect(parseDeepLink('decibyl://chat?<script>=1')).toEqual({ kind: 'open', path: '/chat' });
        expect(linkFromArgv(['Decibyl.exe', '--hidden', 'decibyl://today'])).toBe(
            'decibyl://today',
        );
    });
});

describe('files from this computer', () => {
    it('reads picked files and a folder to a fixed depth, and says what it left out', () => {
        const root = dir();
        const folder = path.join(root, 'Invoices');
        fs.mkdirSync(path.join(folder, 'a', 'b', 'c'), { recursive: true });
        fs.writeFileSync(path.join(folder, 'inv-1.pdf'), 'pdf');
        fs.writeFileSync(path.join(folder, '.DS_Store'), 'x');
        fs.writeFileSync(path.join(folder, 'a', 'b', 'deep.txt'), 'x');
        fs.writeFileSync(path.join(folder, 'a', 'b', 'c', 'too-deep.txt'), 'x');
        const result = readPicked([folder]);
        expect(result.files.map((f) => f.relativePath)).toEqual([
            'Invoices/a/b/deep.txt',
            'Invoices/inv-1.pdf',
        ]);
        expect(result.files[1]).toMatchObject({
            type: 'application/pdf',
            size: 3,
            data: Buffer.from('pdf').toString('base64'),
        });
        expect(result.skipped).toEqual(['Invoices/a/b/c/: deeper than 3 folders']);
    });

    it('stops at the file count and says so', () => {
        const folder = dir();
        for (let i = 0; i < MAX_FILES + 2; i++)
            fs.writeFileSync(path.join(folder, `f${String(i).padStart(2, '0')}.txt`), 'x');
        const result = readPicked([folder]);
        expect(result.files).toHaveLength(MAX_FILES);
        expect(result.skipped).toHaveLength(2);
    });

    it('ignores partial downloads and OS litter', () => {
        expect(isIgnorable('report.pdf.crdownload')).toBe(true);
        expect(isIgnorable('~$budget.xlsx')).toBe(true);
        expect(isIgnorable('invoice.pdf')).toBe(false);
    });

    it('watches only for new files, once each, after they finish writing', async () => {
        const folder = dir();
        fs.writeFileSync(path.join(folder, 'old.pdf'), 'old');
        const seen: string[] = [];
        const stop = watchFolder(folder, (f) => seen.push(f.name), { settleMs: 30 });
        fs.writeFileSync(path.join(folder, 'new.pdf'), 'new');
        fs.writeFileSync(path.join(folder, 'part.pdf.crdownload'), 'x');
        fs.appendFileSync(path.join(folder, 'old.pdf'), 'more');
        await new Promise((r) => setTimeout(r, 300));
        stop();
        expect(seen).toEqual(['new.pdf']);
    });

    it('reads a watched file only from inside the folder', () => {
        const folder = dir();
        fs.writeFileSync(path.join(folder, 'inv.pdf'), 'x');
        expect(readWatched(folder, 'inv.pdf').name).toBe('inv.pdf');
        expect(() => readWatched(folder, '../etc/passwd')).toThrow();
    });
});
