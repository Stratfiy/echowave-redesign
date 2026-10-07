import { client } from '@/lib/api';
import { isMfaRequired, signIn, signUp, signUpProblems, SIGNUP_AGREEMENTS } from '@/lib/auth/auth';
import { biometricEnabled, clearSession, loadSession, saveSession, setBiometricEnabled, TOKEN_KEY } from '@/lib/auth/session';
import { memoryStore } from '@/lib/storage';

const USER = { id: 7, email: 'asha@example.com', name: 'Asha', organization_id: 3, provider_id: 'p', mfa_enabled: false };

function server(handler: (body: Record<string, unknown>, url: string) => { status: number; body: unknown; headers?: Record<string, string> }) {
    const requests: { url: string; body: Record<string, unknown> }[] = [];
    client.setConfig({
        baseUrl: 'https://api.test',
        fetch: (async (input: Request) => {
            const body = JSON.parse(await input.text());
            requests.push({ url: input.url, body });
            const answer = handler(body, input.url);
            return new Response(JSON.stringify(answer.body), {
                status: answer.status,
                headers: { 'Content-Type': 'application/json', ...(answer.headers ?? {}) },
            });
        }) as unknown as typeof fetch,
    });
    return requests;
}

describe('sign in', () => {
    test('a good password signs in', async () => {
        const requests = server(() => ({ status: 200, body: { token: 'jwt', user: USER } }));
        const result = await signIn({ email: ' asha@example.com ', password: 'secret123' });
        expect(result).toEqual({ kind: 'signed_in', auth: { token: 'jwt', user: USER } });
        expect(requests[0].url).toBe('https://api.test/api/v1/auth/login');
        expect(requests[0].body).toEqual({ email: 'asha@example.com', password: 'secret123', mfa_code: null });
    });

    test('an authenticator on the account is a next step, then the code is sent', async () => {
        const requests = server((body) =>
            body.mfa_code
                ? { status: 200, body: { token: 'jwt', user: USER } }
                : { status: 401, body: { detail: 'mfa_required' }, headers: { 'X-MFA-Required': 'totp' } },
        );
        expect(await signIn({ email: 'a@b.co', password: 'secret123' })).toEqual({ kind: 'mfa_required' });
        const second = await signIn({ email: 'a@b.co', password: 'secret123', mfaCode: '123456' });
        expect(second.kind).toBe('signed_in');
        expect(requests[1].body.mfa_code).toBe('123456');
    });

    test('a wrong password says so in words, not a status code', async () => {
        server(() => ({ status: 401, body: { detail: 'Invalid email or password' } }));
        const result = await signIn({ email: 'a@b.co', password: 'nope-nope' });
        expect(result).toMatchObject({ kind: 'error', status: 401 });
        expect((result as { message: string }).message).toMatch(/do not match/);
        expect(isMfaRequired(new Error('x'))).toBe(false);
    });
});

describe('sign up', () => {
    test('checks before sending: email, 8 characters, 72 bytes, the agreement', () => {
        expect(signUpProblems({ email: 'nope', password: 'short', agreed: false })).toEqual([
            'Enter a valid email address.',
            'Choose a password of at least 8 characters.',
            'Tick the box to agree before creating your account.',
        ]);
        // 25 Devanagari letters are 75 bytes: over bcrypt's limit.
        expect(signUpProblems({ email: 'a@b.co', password: 'क'.repeat(25), agreed: true })).toEqual([
            'That password is too long. Use a shorter one.',
        ]);
    });

    test('sends the agreements and the invite code; nothing sent when a check fails', async () => {
        const requests = server(() => ({ status: 200, body: { token: 'jwt', user: USER, email_verification_required: true } }));
        const refused = await signUp({ email: 'a@b.co', password: 'secret123', agreed: false });
        expect(refused.kind).toBe('error');
        expect(requests).toHaveLength(0);
        const ok = await signUp({ email: 'a@b.co', password: 'secret123', inviteCode: 'ABCD-2345', agreed: true });
        expect(ok.kind).toBe('signed_up');
        expect(requests[0].body).toMatchObject({ invite_code: 'ABCD-2345', accepted_agreements: [...SIGNUP_AGREEMENTS] });
    });

    test('an invite refusal comes back in the server\'s words', async () => {
        server(() => ({ status: 403, body: { detail: 'That invite code has already been used.' } }));
        const result = await signUp({ email: 'a@b.co', password: 'secret123', agreed: true });
        expect(result).toEqual({ kind: 'error', status: 403, message: 'That invite code has already been used.' });
    });
});

describe('the session in SecureStore', () => {
    test('saved, read back, cleared', async () => {
        const store = memoryStore();
        expect(await loadSession(store)).toBeNull();
        await saveSession(store, { token: 'jwt', user: USER });
        expect(await loadSession(store)).toEqual({ token: 'jwt', user: USER });
        await clearSession(store);
        expect(await loadSession(store)).toBeNull();
        expect(store.dump()).toEqual({});
    });

    test('a corrupted user is no session, not a crash', async () => {
        const store = memoryStore({ [TOKEN_KEY]: 'jwt', 'decibyl.session.user': '{oops' });
        expect(await loadSession(store)).toBeNull();
    });

    test('biometric unlock is off until turned on', async () => {
        const store = memoryStore();
        expect(await biometricEnabled(store)).toBe(false);
        await setBiometricEnabled(store, true);
        expect(await biometricEnabled(store)).toBe(true);
        await setBiometricEnabled(store, false);
        expect(await biometricEnabled(store)).toBe(false);
    });
});
