/**
 * The signed-in session, kept in SecureStore.
 *
 * Local auth issues one JWT (30 days by default) with no refresh endpoint and
 * no server-side logout (api/routes/auth.py), so the session is the token
 * and the user it was issued for. Signing out deletes both; an expired token
 * (any 401) signs the person out with a line saying why.
 */
import type { AuthResponse, UserResponse } from '@/client/types.gen';
import type { KeyValue } from '@/lib/storage';

export const TOKEN_KEY = 'decibyl.session.token';
export const USER_KEY = 'decibyl.session.user';
export const BIOMETRIC_KEY = 'decibyl.session.biometric';

export type Session = { token: string; user: UserResponse };

export async function loadSession(store: KeyValue): Promise<Session | null> {
    const [token, raw] = await Promise.all([store.get(TOKEN_KEY), store.get(USER_KEY)]);
    if (!token || !raw) return null;
    try {
        const user = JSON.parse(raw) as UserResponse;
        if (!user || typeof user.id !== 'number') return null;
        return { token, user };
    } catch {
        return null;
    }
}

export async function saveSession(store: KeyValue, auth: Pick<AuthResponse, 'token' | 'user'>): Promise<Session> {
    await store.set(TOKEN_KEY, auth.token);
    await store.set(USER_KEY, JSON.stringify(auth.user));
    return { token: auth.token, user: auth.user };
}

export async function clearSession(store: KeyValue): Promise<void> {
    await Promise.all([store.remove(TOKEN_KEY), store.remove(USER_KEY)]);
}

export async function biometricEnabled(store: KeyValue): Promise<boolean> {
    return (await store.get(BIOMETRIC_KEY)) === 'on';
}

export async function setBiometricEnabled(store: KeyValue, on: boolean): Promise<void> {
    if (on) await store.set(BIOMETRIC_KEY, 'on');
    else await store.remove(BIOMETRIC_KEY);
}
