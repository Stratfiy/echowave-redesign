/** Choices kept on this phone only: appearance and the UI language override. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import type { Locale } from '@/lib/i18n';
import type { ThemePreference } from '@/lib/theme';
import { plain } from '@/lib/storage';

const THEME_KEY = 'decibyl.app.theme';
const LOCALE_KEY = 'decibyl.app.locale';

type AppSettings = {
    theme: ThemePreference;
    setTheme(theme: ThemePreference): void;
    localeOverride: Locale | null;
    setLocaleOverride(locale: Locale | null): void;
    ready: boolean;
};

const Ctx = createContext<AppSettings>({
    theme: 'system',
    setTheme: () => undefined,
    localeOverride: null,
    setLocaleOverride: () => undefined,
    ready: false,
});

export function AppSettingsProvider({ children }: { children: ReactNode }) {
    const [theme, setThemeState] = useState<ThemePreference>('system');
    const [localeOverride, setLocaleState] = useState<Locale | null>(null);
    const [ready, setReady] = useState(false);

    useEffect(() => {
        (async () => {
            const [t, l] = await Promise.all([plain.get(THEME_KEY), plain.get(LOCALE_KEY)]);
            if (t === 'light' || t === 'dark' || t === 'system') setThemeState(t);
            if (l === 'en' || l === 'hi') setLocaleState(l);
            setReady(true);
        })();
    }, []);

    const setTheme = useCallback((value: ThemePreference) => {
        setThemeState(value);
        void plain.set(THEME_KEY, value);
    }, []);
    const setLocaleOverride = useCallback((value: Locale | null) => {
        setLocaleState(value);
        if (value) void plain.set(LOCALE_KEY, value);
        else void plain.remove(LOCALE_KEY);
    }, []);

    const value = useMemo(
        () => ({ theme, setTheme, localeOverride, setLocaleOverride, ready }),
        [theme, setTheme, localeOverride, setLocaleOverride, ready],
    );
    return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppSettings(): AppSettings {
    return useContext(Ctx);
}
