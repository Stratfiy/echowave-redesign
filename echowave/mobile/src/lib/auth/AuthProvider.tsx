/**
 * The session for the whole app: loading, signed out, locked (biometric
 * unlock is on and the app was just opened), verifying the email, or signed
 * in. Screens read it with `useAuth()`; the root layout routes on `status`.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { AuthResponse, UserResponse } from '@/client/types.gen';
import { unregisterPush } from '@/lib/push';
import { phonePushDeps } from '@/lib/pushDevice';
import { secure } from '@/lib/storage';

import { biometricAvailable, confirmBiometric } from './biometric';
import { biometricEnabled, clearSession, loadSession, saveSession } from './session';
import { installAuth, setToken, setUnauthorizedListener } from './token';

export type AuthStatus = 'loading' | 'signed_out' | 'locked' | 'verify_email' | 'signed_in';

type AuthState = {
    status: AuthStatus;
    user: UserResponse | null;
    /** Why the person was signed out, shown once on the sign-in screen. */
    notice: string | null;
    accept(auth: AuthResponse): Promise<void>;
    verified(): void;
    unlock(prompt: string, fallback: string): Promise<boolean>;
    signOut(notice?: string | null): Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children, expiredNotice }: { children: ReactNode; expiredNotice: string }) {
    const [status, setStatus] = useState<AuthStatus>('loading');
    const [user, setUser] = useState<UserResponse | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const signingOut = useRef(false);

    const signOut = useCallback(async (why: string | null = null) => {
        if (signingOut.current) return;
        signingOut.current = true;
        try {
            // Stop this phone's notices first, while the token still works.
            await unregisterPush(phonePushDeps());
            await clearSession(secure);
        } finally {
            setToken(null);
            setUser(null);
            setNotice(why);
            setStatus('signed_out');
            signingOut.current = false;
        }
    }, []);

    useEffect(() => {
        installAuth();
        setUnauthorizedListener(() => {
            void signOut(expiredNotice);
        });
        let alive = true;
        (async () => {
            const session = await loadSession(secure);
            if (!alive) return;
            if (!session) {
                setStatus('signed_out');
                return;
            }
            setToken(session.token);
            setUser(session.user);
            const lock = (await biometricEnabled(secure)) && (await biometricAvailable());
            if (alive) setStatus(lock ? 'locked' : 'signed_in');
        })();
        return () => {
            alive = false;
            setUnauthorizedListener(null);
        };
    }, [signOut, expiredNotice]);

    const accept = useCallback(async (auth: AuthResponse) => {
        const session = await saveSession(secure, auth);
        setToken(session.token);
        setUser(session.user);
        setNotice(null);
        setStatus(auth.email_verification_required ? 'verify_email' : 'signed_in');
    }, []);

    const unlock = useCallback(async (prompt: string, fallback: string) => {
        const ok = await confirmBiometric(prompt, fallback);
        if (ok) setStatus('signed_in');
        return ok;
    }, []);

    const verified = useCallback(() => setStatus('signed_in'), []);

    const value = useMemo(
        () => ({ status, user, notice, accept, verified, unlock, signOut }),
        [status, user, notice, accept, verified, unlock, signOut],
    );
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
    const value = useContext(AuthContext);
    if (!value) throw new Error('useAuth outside AuthProvider');
    return value;
}
