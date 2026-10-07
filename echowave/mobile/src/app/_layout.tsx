/**
 * The root: providers, the sign-in gate, and what happens on every app open
 * (push token refreshed, contacts re-synced, a tapped notification routed).
 */
import * as Notifications from 'expo-notifications';
import { Stack, useRouter } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect, type ReactNode } from 'react';
import { AppState, Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, useAuth } from '@/lib/auth/AuthProvider';
import { AppSettingsProvider, useAppSettings } from '@/lib/appSettings';
import { scheduleContactsSync, runContactsSync } from '@/lib/background/tasks';
import { hasConsent } from '@/lib/contacts/consent';
import { FeaturesProvider } from '@/lib/features';
import { I18nProvider, localeFor, useI18n } from '@/lib/i18n';
import { routeForPath } from '@/lib/links';
import { PreferencesProvider, usePreferences } from '@/lib/preferences';
import { registerForPush } from '@/lib/push';
import { installNotificationHandler, phonePushDeps } from '@/lib/pushDevice';
import { plain } from '@/lib/storage';
import { ThemeProvider, useTheme } from '@/lib/theme';

installNotificationHandler();

function ExpiredNotice({ children }: { children: ReactNode }) {
    const { t } = useI18n();
    return <AuthProvider expiredNotice={t('auth.expired')}>{children}</AuthProvider>;
}

/** The person's language from the server wins, then the app's own choice. */
function LanguageFollower() {
    const { prefs } = usePreferences();
    const { localeOverride } = useAppSettings();
    const { locale, setLocale } = useI18n();
    useEffect(() => {
        const wanted = localeOverride ?? (prefs?.language ? localeFor(prefs.language) : null);
        if (wanted && wanted !== locale) setLocale(wanted);
    }, [prefs?.language, localeOverride, locale, setLocale]);
    return null;
}

function OnOpen() {
    const router = useRouter();
    const { status } = useAuth();
    const signedIn = status === 'signed_in';

    // Every open: refresh this phone's push token (never prompts), and
    // re-sync contacts incrementally if the person agreed.
    useEffect(() => {
        if (!signedIn) return;
        const refresh = async () => {
            void registerForPush(phonePushDeps(), { ask: false });
            if (await hasConsent(plain)) {
                void runContactsSync();
                void scheduleContactsSync();
            }
        };
        void refresh();
        const sub = AppState.addEventListener('change', (next) => {
            if (next === 'active') void refresh();
        });
        return () => sub.remove();
    }, [signedIn]);

    // A tapped notification opens its screen (data.url is a web path).
    useEffect(() => {
        if (!signedIn || Platform.OS === 'web') return;
        const open = (url: unknown) => {
            if (typeof url === 'string' && url) router.push(routeForPath(url) as never);
        };
        const last = Notifications.getLastNotificationResponse();
        if (last) open(last.notification.request.content.data?.url);
        const sub = Notifications.addNotificationResponseReceivedListener((response) =>
            open(response.notification.request.content.data?.url),
        );
        return () => sub.remove();
    }, [signedIn, router]);
    return null;
}

function Gate() {
    const { status } = useAuth();
    const theme = useTheme();
    const signedIn = status === 'signed_in';
    return (
        <>
            <StatusBar style={theme.scheme === 'dark' ? 'light' : 'dark'} />
            <Stack
                screenOptions={{
                    headerStyle: { backgroundColor: theme.colors.paper },
                    headerTintColor: theme.colors.ink,
                    headerTitleStyle: { fontSize: 17 * theme.scale },
                    contentStyle: { backgroundColor: theme.colors.paper },
                    headerBackButtonDisplayMode: 'minimal',
                }}
            >
                <Stack.Protected guard={status === 'signed_out'}>
                    <Stack.Screen name="sign-in" options={{ headerShown: false }} />
                    <Stack.Screen name="sign-up" options={{ headerShown: false }} />
                </Stack.Protected>
                <Stack.Protected guard={status === 'locked'}>
                    <Stack.Screen name="unlock" options={{ headerShown: false }} />
                </Stack.Protected>
                <Stack.Protected guard={status === 'verify_email'}>
                    <Stack.Screen name="verify-email" options={{ headerShown: false }} />
                </Stack.Protected>
                <Stack.Protected guard={signedIn}>
                    <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
                    <Stack.Screen name="chat/[id]" options={{ title: '' }} />
                    <Stack.Screen name="approvals/[id]" options={{ title: '' }} />
                    <Stack.Screen name="reminders/[id]" options={{ title: '' }} />
                    <Stack.Screen name="person/[id]" options={{ title: '' }} />
                    <Stack.Screen name="voice" options={{ presentation: 'fullScreenModal', headerShown: false }} />
                    <Stack.Screen name="share" options={{ presentation: 'modal', title: '' }} />
                    <Stack.Screen name="settings/index" options={{ title: '' }} />
                    <Stack.Screen name="settings/account" options={{ title: '' }} />
                    <Stack.Screen name="settings/language" options={{ title: '' }} />
                    <Stack.Screen name="settings/notifications" options={{ title: '' }} />
                    <Stack.Screen name="settings/privacy" options={{ title: '' }} />
                    <Stack.Screen name="web" options={{ title: '' }} />
                </Stack.Protected>
            </Stack>
            <OnOpen />
            <LanguageFollower />
        </>
    );
}

function Themed({ children }: { children: ReactNode }) {
    const { theme } = useAppSettings();
    const { prefs } = usePreferences();
    return (
        <ThemeProvider preference={theme} simple={Boolean(prefs?.simple_mode)}>
            {children}
        </ThemeProvider>
    );
}

function SignedInProviders({ children }: { children: ReactNode }) {
    const { status } = useAuth();
    const signedIn = status === 'signed_in';
    return (
        <FeaturesProvider signedIn={signedIn}>
            <PreferencesProvider signedIn={signedIn}>
                <Themed>{children}</Themed>
            </PreferencesProvider>
        </FeaturesProvider>
    );
}

export default function RootLayout() {
    return (
        <SafeAreaProvider>
            <AppSettingsProvider>
                <I18nProvider>
                    <ExpiredNotice>
                        <SignedInProviders>
                            <Gate />
                        </SignedInProviders>
                    </ExpiredNotice>
                </I18nProvider>
            </AppSettingsProvider>
        </SafeAreaProvider>
    );
}
