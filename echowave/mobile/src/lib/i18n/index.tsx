/**
 * Hindi and English, India first.
 *
 * The language comes from, in order: the person's own preference on the
 * server (member preferences `language`, e.g. "hi-IN"), the choice made in
 * the app, then the phone's language. Anything that is not Hindi is English
 * for the UI; Decibyl's answers follow the preference on the server whatever
 * the UI shows.
 */
import { getLocales } from 'expo-localization';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { en, type MessageKey } from './en';
import { hi } from './hi';

export type Locale = 'en' | 'hi';

export const dictionaries: Record<Locale, Record<MessageKey, string>> = { en, hi };

export function localeFor(tag: string | null | undefined): Locale {
    return (tag || '').toLowerCase().startsWith('hi') ? 'hi' : 'en';
}

export function deviceLocale(): Locale {
    try {
        return localeFor(getLocales()[0]?.languageTag);
    } catch {
        return 'en';
    }
}

export function translate(locale: Locale, key: MessageKey, vars?: Record<string, string | number>): string {
    const template = dictionaries[locale][key] ?? en[key] ?? key;
    if (!vars) return template;
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
        name in vars ? String(vars[name]) : match,
    );
}

/** Indian digit grouping (12,34,567) in either language. */
export function formatNumber(value: number, locale: Locale): string {
    return new Intl.NumberFormat(locale === 'hi' ? 'hi-IN' : 'en-IN').format(value);
}

export function formatTime(iso: string | null | undefined, locale: Locale): string {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const sameDay = new Date().toDateString() === date.toDateString();
    return new Intl.DateTimeFormat(locale === 'hi' ? 'hi-IN' : 'en-IN', sameDay
        ? { hour: 'numeric', minute: '2-digit' }
        : { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }).format(date);
}

type I18n = {
    locale: Locale;
    setLocale: (locale: Locale) => void;
    t: (key: MessageKey, vars?: Record<string, string | number>) => string;
};

const I18nContext = createContext<I18n>({
    locale: 'en',
    setLocale: () => undefined,
    t: (key, vars) => translate('en', key, vars),
});

export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Locale }) {
    const [locale, setLocale] = useState<Locale>(initial ?? deviceLocale());
    const t = useCallback((key: MessageKey, vars?: Record<string, string | number>) => translate(locale, key, vars), [locale]);
    const value = useMemo(() => ({ locale, setLocale, t }), [locale, t]);
    return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
    return useContext(I18nContext);
}

export type { MessageKey };
