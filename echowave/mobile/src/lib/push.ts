/**
 * Push to this phone: permission, the Expo push token, and telling the
 * server (POST /me/mobile-push/tokens, flag `mobile_push`).
 *
 * The token is registered again on every app open (the server updates the
 * same row, so `last_seen_at` says the install is alive) and removed on
 * sign-out before the session is forgotten, so a phone handed to someone
 * else stops getting the first person's notices.
 *
 * The phone is only asked for permission after the person chose "Push to
 * this phone" in Settings -- never on first launch.
 *
 * Everything outside is passed in (`PushDeps`), so the order of these steps
 * is tested without a phone (src/lib/__tests__/push.test.ts).
 */
import type { KeyValue } from '@/lib/storage';
import { ApiError } from '@/lib/api/errors';

export const PUSH_TOKEN_KEY = 'decibyl.push.token';
export const ANDROID_CHANNEL = 'default';

type PermissionStatus = 'granted' | 'denied' | 'undetermined';

export type PushDeps = {
    platform: string;
    isDevice: boolean;
    deviceName?: string | null;
    appVersion?: string | null;
    projectId?: string;
    notifications: {
        getPermissionsAsync(): Promise<{ status: PermissionStatus | string }>;
        requestPermissionsAsync(): Promise<{ status: PermissionStatus | string }>;
        getExpoPushTokenAsync(options: { projectId: string }): Promise<{ data: string }>;
        setNotificationChannelAsync(id: string, channel: { name: string; importance: number }): Promise<unknown>;
    };
    androidImportanceHigh: number;
    register(body: { token: string; platform: 'ios' | 'android'; device_label: string | null; app_version: string | null }): Promise<unknown>;
    unregister(body: { token: string }): Promise<unknown>;
    store: KeyValue;
};

export type PushResult =
    | { status: 'registered'; token: string }
    | { status: 'denied' }
    | { status: 'not_asked' }
    | { status: 'unsupported'; reason: 'web' | 'simulator' | 'no_project' }
    | { status: 'off' }
    | { status: 'failed'; message: string };

export async function registerForPush(deps: PushDeps, options: { ask: boolean }): Promise<PushResult> {
    if (deps.platform !== 'ios' && deps.platform !== 'android') return { status: 'unsupported', reason: 'web' };
    if (!deps.isDevice) return { status: 'unsupported', reason: 'simulator' };
    if (!deps.projectId) return { status: 'unsupported', reason: 'no_project' };
    // Android 13+ shows the permission prompt only once a channel exists, and
    // the token must come after the channel.
    if (deps.platform === 'android') {
        await deps.notifications.setNotificationChannelAsync(ANDROID_CHANNEL, {
            name: 'Decibyl',
            importance: deps.androidImportanceHigh,
        });
    }
    let { status } = await deps.notifications.getPermissionsAsync();
    if (status !== 'granted') {
        if (!options.ask) return { status: 'not_asked' };
        status = (await deps.notifications.requestPermissionsAsync()).status;
    }
    if (status !== 'granted') return { status: 'denied' };
    try {
        const token = (await deps.notifications.getExpoPushTokenAsync({ projectId: deps.projectId })).data;
        await deps.register({
            token,
            platform: deps.platform,
            device_label: deps.deviceName ?? null,
            app_version: deps.appVersion ?? null,
        });
        await deps.store.set(PUSH_TOKEN_KEY, token);
        return { status: 'registered', token };
    } catch (error) {
        // The flag is off for this workspace: the route is not there.
        if (error instanceof ApiError && error.status === 404) return { status: 'off' };
        return { status: 'failed', message: error instanceof Error ? error.message : 'Push could not be set up.' };
    }
}

/** Before signing out: stop this phone getting the person's notices. Never
 * blocks sign-out -- a phone offline still signs out; the server drops the
 * token itself when Expo reports the install gone. */
export async function unregisterPush(deps: Pick<PushDeps, 'unregister' | 'store'>): Promise<boolean> {
    const token = await deps.store.get(PUSH_TOKEN_KEY);
    if (!token) return false;
    try {
        await deps.unregister({ token });
    } catch {
        // See above.
    }
    await deps.store.remove(PUSH_TOKEN_KEY);
    return true;
}
