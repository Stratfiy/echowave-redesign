/**
 * The person's own preferences (GET/PUT /me/preferences, flag
 * `member_preferences`): language and Simple mode are what the app needs.
 *
 * Saves name the revision they read; a stale one is a 409 carrying the
 * stored row, and the save is retried once on that row (the same rule as
 * ui/src/lib/care/simpleMode.tsx). They follow the person to every device.
 */
import { useLoad } from '@/lib/useLoad';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { myPreferencesApiV1MePreferencesGet, saveMyPreferencesApiV1MePreferencesPut } from '@/client/sdk.gen';
import type { MemberPreferences, MemberPreferencesWrite } from '@/client/types.gen';
import { ApiError, call } from '@/lib/api';

type Prefs = {
    prefs: MemberPreferences | null;
    /** False when member preferences are not switched on (a 404). */
    available: boolean;
    save(change: Omit<MemberPreferencesWrite, 'revision'>): Promise<MemberPreferences>;
    reload(): Promise<void>;
};

const PrefsContext = createContext<Prefs>({
    prefs: null,
    available: false,
    save: async () => {
        throw new Error('Preferences are not loaded.');
    },
    reload: async () => undefined,
});

function storedFrom(error: unknown): MemberPreferences | null {
    if (!(error instanceof ApiError) || error.status !== 409) return null;
    const stored = (error.detail as { stored?: MemberPreferences } | null)?.stored;
    return stored && typeof stored.revision === 'number' ? stored : null;
}

export async function saveWithRetry(
    current: MemberPreferences,
    change: Omit<MemberPreferencesWrite, 'revision'>,
    put: (body: MemberPreferencesWrite) => Promise<MemberPreferences>,
): Promise<MemberPreferences> {
    try {
        return await put({ ...change, revision: current.revision });
    } catch (error) {
        const stored = storedFrom(error);
        if (!stored) throw error;
        return put({ ...change, revision: stored.revision });
    }
}

export function PreferencesProvider({ children, signedIn }: { children: ReactNode; signedIn: boolean }) {
    const [prefs, setPrefs] = useState<MemberPreferences | null>(null);
    const [available, setAvailable] = useState(false);

    const reload = useCallback(async () => {
        if (!signedIn) {
            setPrefs(null);
            return;
        }
        try {
            setPrefs(await call(myPreferencesApiV1MePreferencesGet()));
            setAvailable(true);
        } catch (error) {
            if (error instanceof ApiError && error.status === 404) setAvailable(false);
        }
    }, [signedIn]);

    useLoad(reload, [reload]);

    const save = useCallback(
        async (change: Omit<MemberPreferencesWrite, 'revision'>) => {
            const base = prefs ?? (await call(myPreferencesApiV1MePreferencesGet()));
            const saved = await saveWithRetry(base, change, (body) =>
                call(saveMyPreferencesApiV1MePreferencesPut({ body })),
            );
            setPrefs(saved);
            return saved;
        },
        [prefs],
    );

    const value = useMemo(() => ({ prefs, available, save, reload }), [prefs, available, save, reload]);
    return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePreferences(): Prefs {
    return useContext(PrefsContext);
}
