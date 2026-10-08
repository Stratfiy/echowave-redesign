import Constants from 'expo-constants';

/**
 * Where this build talks to. Set per build profile in eas.json
 * (EXPO_PUBLIC_API_URL / EXPO_PUBLIC_WEB_URL) and read through app.config.ts,
 * so a TestFlight build points at staging and a store build at production
 * without a code change. Local development falls back to localhost.
 */
type Extra = { apiUrl?: string; webUrl?: string; easProjectId?: string };

function extra(): Extra {
    return (Constants.expoConfig?.extra ?? {}) as Extra;
}

function trimmed(url: string): string {
    return url.replace(/\/+$/, '');
}

export function apiBaseUrl(): string {
    return trimmed(process.env.EXPO_PUBLIC_API_URL || extra().apiUrl || 'http://localhost:8000');
}

/** The web app, for the screens the app opens as authenticated web views. */
export function webAppUrl(): string {
    return trimmed(process.env.EXPO_PUBLIC_WEB_URL || extra().webUrl || 'http://localhost:3000');
}

/** The EAS project id: Expo push tokens are issued per project. */
export function easProjectId(): string | undefined {
    return (
        process.env.EXPO_PUBLIC_EAS_PROJECT_ID ||
        extra().easProjectId ||
        (Constants.expoConfig as { extra?: { eas?: { projectId?: string } } } | null)?.extra?.eas
            ?.projectId
    );
}

export const APP_SCHEME = 'decibyl';
