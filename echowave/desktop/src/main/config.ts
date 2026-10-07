/**
 * Which Decibyl this app opens, and the person's settings for this computer.
 *
 * The web app is loaded, not bundled: production, staging or a local dev
 * server, chosen by `--env=`, `DECIBYL_ENV`, `DECIBYL_URL`, or the settings
 * window. Everything the person sets lives in one JSON file in the app's
 * user-data folder. The model key (for "work on my computer") does not: it is
 * encrypted with the OS keychain through `safeStorage` (see main.ts).
 */

import fs from 'node:fs';
import path from 'node:path';

import { type Limits, DEFAULT_LIMITS, clampLimits } from '../computer-use/limits';
import { type AppRules, appKey, canBeAllowed } from '../computer-use/policy';

export const ENVIRONMENTS = {
    production: 'https://app.decibyl.ai',
    staging: 'https://staging.decibyl.ai',
    local: 'http://localhost:3000',
} as const;

export type EnvironmentName = keyof typeof ENVIRONMENTS | 'custom';

export interface Settings {
    environment: EnvironmentName;
    customUrl: string | null;
    startAtLogin: boolean;
    shortcut: string;
    updateChannel: 'latest' | 'beta';
    watchedFolder: string | null;
    computerUse: {
        /** Display names, in the order the person added them. */
        allowedApps: string[];
        limits: Limits;
        model: string | null;
        /** A relay that forwards model calls and keeps nothing; empty = direct. */
        baseURL: string | null;
    };
}

export const DEFAULT_SHORTCUT = 'CommandOrControl+Shift+Space';

export const DEFAULT_SETTINGS: Settings = {
    environment: 'production',
    customUrl: null,
    // Opt-in: nothing starts with the computer unless the person says so.
    startAtLogin: false,
    shortcut: DEFAULT_SHORTCUT,
    updateChannel: 'latest',
    watchedFolder: null,
    computerUse: { allowedApps: [], limits: DEFAULT_LIMITS, model: null, baseURL: null },
};

/** http(s) only; a local URL only over http to localhost. */
export function isSafeWebUrl(value: string): boolean {
    try {
        const url = new URL(value);
        if (url.protocol === 'https:') return true;
        return url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname);
    } catch {
        return false;
    }
}

export function resolveWebUrl(
    settings: Pick<Settings, 'environment' | 'customUrl'>,
    argv: string[] = [],
    env: NodeJS.ProcessEnv = {},
): string {
    const flag = argv.find((a) => a.startsWith('--env='))?.slice('--env='.length);
    const fromEnv = env.DECIBYL_URL;
    if (fromEnv && isSafeWebUrl(fromEnv)) return fromEnv.replace(/\/$/, '');
    const name = (flag || env.DECIBYL_ENV || settings.environment) as EnvironmentName;
    if (name === 'custom') {
        return settings.customUrl && isSafeWebUrl(settings.customUrl)
            ? settings.customUrl.replace(/\/$/, '')
            : ENVIRONMENTS.production;
    }
    return ENVIRONMENTS[name as keyof typeof ENVIRONMENTS] ?? ENVIRONMENTS.production;
}

export function rulesOf(settings: Settings): AppRules {
    const rules: AppRules = {};
    for (const name of settings.computerUse.allowedApps) rules[appKey(name)] = { allowed: true };
    return rules;
}

/**
 * Settings from disk or from the settings window, made safe: unknown keys
 * dropped, every value checked, never-touch apps refused, limits clamped.
 */
export function sanitizeSettings(raw: unknown, base: Settings = DEFAULT_SETTINGS): Settings {
    const input = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const cu = (
        input.computerUse && typeof input.computerUse === 'object' ? input.computerUse : {}
    ) as Record<string, unknown>;
    const envs: EnvironmentName[] = ['production', 'staging', 'local', 'custom'];
    const apps = Array.isArray(cu.allowedApps) ? cu.allowedApps : base.computerUse.allowedApps;
    const seen = new Set<string>();
    const allowedApps: string[] = [];
    for (const app of apps) {
        const name = String(app ?? '')
            .trim()
            .slice(0, 80);
        if (!name || !canBeAllowed(name) || seen.has(appKey(name))) continue;
        seen.add(appKey(name));
        allowedApps.push(name);
    }
    const customUrl =
        typeof input.customUrl === 'string' && isSafeWebUrl(input.customUrl)
            ? input.customUrl
            : input.customUrl === null
              ? null
              : base.customUrl;
    const baseURL =
        typeof cu.baseURL === 'string' && cu.baseURL && isSafeWebUrl(cu.baseURL)
            ? cu.baseURL
            : cu.baseURL === null || cu.baseURL === ''
              ? null
              : base.computerUse.baseURL;
    return {
        environment: envs.includes(input.environment as EnvironmentName)
            ? (input.environment as EnvironmentName)
            : base.environment,
        customUrl,
        startAtLogin:
            typeof input.startAtLogin === 'boolean' ? input.startAtLogin : base.startAtLogin,
        shortcut:
            typeof input.shortcut === 'string' && /^[A-Za-z0-9+]{1,60}$/.test(input.shortcut)
                ? input.shortcut
                : base.shortcut,
        updateChannel:
            input.updateChannel === 'beta' || input.updateChannel === 'latest'
                ? input.updateChannel
                : base.updateChannel,
        watchedFolder:
            typeof input.watchedFolder === 'string'
                ? input.watchedFolder
                : input.watchedFolder === null
                  ? null
                  : base.watchedFolder,
        computerUse: {
            allowedApps,
            limits: clampLimits({
                ...base.computerUse.limits,
                ...((cu.limits as Partial<Limits>) ?? {}),
            }),
            model:
                typeof cu.model === 'string' && /^[a-z0-9.-]{3,64}$/.test(cu.model)
                    ? cu.model
                    : cu.model === null
                      ? null
                      : base.computerUse.model,
            baseURL,
        },
    };
}

export class SettingsStore {
    private current: Settings;

    constructor(private readonly file: string) {
        let raw: unknown = {};
        try {
            raw = JSON.parse(fs.readFileSync(file, 'utf8'));
        } catch {
            raw = {};
        }
        this.current = sanitizeSettings(raw);
    }

    get(): Settings {
        return structuredClone(this.current);
    }

    /** Merge a partial change; the store only ever holds sanitized values. */
    update(change: Record<string, unknown>): Settings {
        const merged = {
            ...this.current,
            ...change,
            computerUse: { ...this.current.computerUse, ...((change.computerUse as object) ?? {}) },
        };
        this.current = sanitizeSettings(merged, this.current);
        fs.mkdirSync(path.dirname(this.file), { recursive: true });
        fs.writeFileSync(this.file, JSON.stringify(this.current, null, 2));
        return this.get();
    }

    /** Only the folder picker in main may set this, never a renderer. */
    setWatchedFolder(folder: string | null): Settings {
        return this.update({ watchedFolder: folder });
    }
}
