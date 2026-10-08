/**
 * Colours and sizes, from the web app's shell (ui/src/app/shell-v2.css), so
 * the app and the site read as one product. Light and dark follow the phone
 * unless the person picks one in Settings.
 *
 * Simple mode (CARE.md) raises text to the web's 19px root (from 16) and
 * every control to 56px, and the screens show one thing at a time.
 */
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useColorScheme } from 'react-native';

export type Scheme = 'light' | 'dark';
export type ThemePreference = 'system' | Scheme;

const palettes = {
    light: {
        paper: '#ffffff',
        paper2: '#f9f9f9',
        line: '#ececec',
        ink: '#0d0d0d',
        ink2: '#5d5d5d',
        ink3: '#8f8f8f',
        primary: '#0d0d0d',
        onPrimary: '#ffffff',
        haldi: '#d97706',
        live: '#1f9d55',
        bad: '#b42318',
        bubbleMine: '#f0f0f0',
        overlay: 'rgba(0,0,0,0.35)',
    },
    dark: {
        paper: '#212121',
        paper2: '#171717',
        line: '#303030',
        ink: '#ececec',
        ink2: '#b4b4b4',
        ink3: '#8f8f8f',
        primary: '#ececec',
        onPrimary: '#171717',
        haldi: '#f0a948',
        live: '#4ade80',
        bad: '#f97066',
        bubbleMine: '#303030',
        overlay: 'rgba(0,0,0,0.55)',
    },
} as const;

export type Colors = { [K in keyof (typeof palettes)['light']]: string };

export type Theme = {
    scheme: Scheme;
    colors: Colors;
    simple: boolean;
    /** Multiply font sizes by this. */
    scale: number;
    /** The smallest tap target. */
    control: number;
    radius: { sm: number; md: number; lg: number; pill: number };
    space: (n: number) => number;
};

export function makeTheme(scheme: Scheme, simple: boolean): Theme {
    return {
        scheme,
        colors: palettes[scheme],
        simple,
        scale: simple ? 19 / 16 : 1,
        control: simple ? 56 : 44,
        radius: { sm: 6, md: 10, lg: 14, pill: 999 },
        space: (n: number) => n * 4,
    };
}

const ThemeContext = createContext<Theme>(makeTheme('light', false));

export function ThemeProvider({
    preference = 'system',
    simple = false,
    children,
}: {
    preference?: ThemePreference;
    simple?: boolean;
    children: ReactNode;
}) {
    const system = useColorScheme();
    const scheme: Scheme = preference === 'system' ? (system === 'dark' ? 'dark' : 'light') : preference;
    const theme = useMemo(() => makeTheme(scheme, simple), [scheme, simple]);
    return <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
    return useContext(ThemeContext);
}
