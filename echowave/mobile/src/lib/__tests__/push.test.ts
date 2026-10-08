/**
 * Push registration: the order of the steps, asking only when the person
 * chose to, and sign-out stopping this phone's notices.
 */
import { ApiError } from '@/lib/api/errors';
import { PUSH_TOKEN_KEY, registerForPush, unregisterPush, type PushDeps } from '@/lib/push';
import { memoryStore } from '@/lib/storage';

function deps(overrides: Partial<PushDeps> = {}, permission = 'undetermined', afterAsk = 'granted') {
    const steps: string[] = [];
    let status = permission;
    const d: PushDeps & { steps: string[] } = {
        steps,
        platform: 'android',
        isDevice: true,
        deviceName: 'Pixel 8',
        appVersion: '0.1.0',
        projectId: 'proj-1',
        androidImportanceHigh: 4,
        notifications: {
            setNotificationChannelAsync: async (id) => void steps.push(`channel:${id}`),
            getPermissionsAsync: async () => {
                steps.push('check');
                return { status };
            },
            requestPermissionsAsync: async () => {
                steps.push('ask');
                status = afterAsk;
                return { status };
            },
            getExpoPushTokenAsync: async ({ projectId }) => {
                steps.push(`token:${projectId}`);
                return { data: 'ExponentPushToken[abcdefghijkl]' };
            },
        },
        register: jest.fn(async (body) => {
            steps.push(`register:${body.platform}`);
            return {};
        }),
        unregister: jest.fn(async () => ({ removed: true })),
        store: memoryStore(),
        ...overrides,
    };
    return d;
}

test('Android: channel, check, ask, token, then the server -- in that order', async () => {
    const d = deps();
    const result = await registerForPush(d, { ask: true });
    expect(result).toEqual({ status: 'registered', token: 'ExponentPushToken[abcdefghijkl]' });
    expect(d.steps).toEqual(['channel:default', 'check', 'ask', 'token:proj-1', 'register:android']);
    expect(d.register).toHaveBeenCalledWith({
        token: 'ExponentPushToken[abcdefghijkl]',
        platform: 'android',
        device_label: 'Pixel 8',
        app_version: '0.1.0',
    });
    expect(await d.store.get(PUSH_TOKEN_KEY)).toBe('ExponentPushToken[abcdefghijkl]');
});

test('on app open (ask: false) the person is never prompted', async () => {
    const d = deps();
    expect(await registerForPush(d, { ask: false })).toEqual({ status: 'not_asked' });
    expect(d.steps).not.toContain('ask');
    expect(d.register).not.toHaveBeenCalled();
});

test('already allowed: registered again on open, without a prompt', async () => {
    const d = deps({ platform: 'ios' }, 'granted');
    expect((await registerForPush(d, { ask: false })).status).toBe('registered');
    expect(d.steps).toEqual(['check', 'token:proj-1', 'register:ios']);
});

test('the person said no: nothing is registered', async () => {
    const d = deps({}, 'undetermined', 'denied');
    expect(await registerForPush(d, { ask: true })).toEqual({ status: 'denied' });
    expect(d.register).not.toHaveBeenCalled();
});

test('web, a simulator, or no EAS project: unsupported, said why', async () => {
    expect(await registerForPush(deps({ platform: 'web' }), { ask: true })).toEqual({ status: 'unsupported', reason: 'web' });
    expect(await registerForPush(deps({ isDevice: false }), { ask: true })).toEqual({ status: 'unsupported', reason: 'simulator' });
    expect(await registerForPush(deps({ projectId: undefined }), { ask: true })).toEqual({ status: 'unsupported', reason: 'no_project' });
});

test('the flag off for the workspace (404) is "off", not a failure', async () => {
    const d = deps({ register: async () => Promise.reject(new ApiError(404, 'Not Found', 'Not Found')) }, 'granted');
    expect(await registerForPush(d, { ask: false })).toEqual({ status: 'off' });
    expect(await d.store.get(PUSH_TOKEN_KEY)).toBeNull();
});

test('sign-out removes the token on the server, then forgets it; offline still signs out', async () => {
    const d = deps({}, 'granted');
    await registerForPush(d, { ask: false });
    expect(await unregisterPush(d)).toBe(true);
    expect(d.unregister).toHaveBeenCalledWith({ token: 'ExponentPushToken[abcdefghijkl]' });
    expect(await d.store.get(PUSH_TOKEN_KEY)).toBeNull();
    expect(await unregisterPush(d)).toBe(false); // nothing left to remove

    const offline = deps({ unregister: async () => Promise.reject(new Error('offline')) }, 'granted');
    await registerForPush(offline, { ask: false });
    expect(await unregisterPush(offline)).toBe(true);
    expect(await offline.store.get(PUSH_TOKEN_KEY)).toBeNull();
});
