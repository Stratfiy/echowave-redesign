/**
 * Settings: the common items native (account, language, notifications,
 * privacy, Simple mode, unlock, appearance); everything else opens the web
 * app's own screen in an authenticated web view (src/lib/webScreens.ts), so
 * nothing is missing on day one.
 */
import Constants from 'expo-constants';
import { Stack, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { View } from 'react-native';

import { Button, Chip, Divider, Notice, Row, Screen, Section, Toggle, Txt } from '@/components/ui';
import { useAppSettings } from '@/lib/appSettings';
import { useAuth } from '@/lib/auth/AuthProvider';
import { biometricAvailable } from '@/lib/auth/biometric';
import { biometricEnabled, setBiometricEnabled } from '@/lib/auth/session';
import { useFeature } from '@/lib/features';
import { useI18n } from '@/lib/i18n';
import { usePreferences } from '@/lib/preferences';
import { secure } from '@/lib/storage';
import type { ThemePreference } from '@/lib/theme';
import { WEB_SCREENS } from '@/lib/webScreens';

export default function Settings() {
    const { t } = useI18n();
    const router = useRouter();
    const { user, signOut } = useAuth();
    const { theme, setTheme } = useAppSettings();
    const { prefs, save, available } = usePreferences();
    const simpleOn = useFeature('care_simple_mode');
    const [bio, setBio] = useState(false);
    const [bioAvailable, setBioAvailable] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        void biometricEnabled(secure).then(setBio);
        void biometricAvailable().then(setBioAvailable);
    }, []);

    const toggleSimple = async (on: boolean) => {
        setError(null);
        try {
            await save({ simple_mode: on });
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    };

    const toggleBio = async (on: boolean) => {
        await setBiometricEnabled(secure, on);
        setBio(on);
    };

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-settings">
            <Stack.Screen options={{ title: t('settings.title') }} />
            {user ? (
                <View style={{ gap: 2 }}>
                    <Txt size={20} weight="700">
                        {user.name || user.email}
                    </Txt>
                    {user.name ? <Txt tone="ink2">{user.email}</Txt> : null}
                </View>
            ) : null}
            <Section title={t('settings.account')}>
                <Row title={t('settings.account')} onPress={() => router.push('/settings/account')} chevron testID="settings-account" />
                <Divider />
                <Row title={t('settings.language')} onPress={() => router.push('/settings/language')} chevron testID="settings-language" />
                <Divider />
                <Row title={t('settings.notifications')} onPress={() => router.push('/settings/notifications')} chevron testID="settings-notifications" />
                <Divider />
                <Row title={t('settings.privacy')} onPress={() => router.push('/settings/privacy')} chevron testID="settings-privacy" />
            </Section>
            <Section title={t('settings.simpleMode')}>
                {simpleOn && available ? (
                    <Toggle label={t('settings.simpleMode')} hint={t('settings.simpleModeHint')} value={Boolean(prefs?.simple_mode)} onChange={toggleSimple} testID="settings-simple" />
                ) : (
                    <Row title={t('settings.simpleMode')} subtitle={t('settings.notSwitchedOn')} />
                )}
            </Section>
            <Section title={t('settings.security')}>
                <Toggle
                    label={t('settings.biometric')}
                    hint={bioAvailable ? t('settings.biometricHint') : t('settings.biometricUnavailable')}
                    value={bio}
                    onChange={toggleBio}
                    disabled={!bioAvailable}
                    testID="settings-biometric"
                />
            </Section>
            <Section title={t('settings.theme')}>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                    {(['system', 'light', 'dark'] as ThemePreference[]).map((value) => (
                        <Chip key={value} label={t(`settings.theme.${value}`)} selected={theme === value} onPress={() => setTheme(value)} testID={`theme-${value}`} />
                    ))}
                </View>
            </Section>
            {error ? <Notice text={error} tone="bad" /> : null}
            <Section title={t('settings.more')} testID="settings-web-screens">
                <Txt size={13} tone="ink2">
                    {t('settings.moreHint')}
                </Txt>
                {WEB_SCREENS.map((screen, i) => (
                    <View key={screen.path}>
                        {i ? <Divider /> : null}
                        <Row
                            title={screen.title}
                            onPress={() => router.push({ pathname: '/web', params: { path: screen.path, title: screen.title } })}
                            chevron
                        />
                    </View>
                ))}
            </Section>
            <Button label={t('settings.signOut')} kind="secondary" onPress={() => signOut()} testID="settings-sign-out" />
            <Txt size={12} tone="ink3" style={{ textAlign: 'center' }}>
                {t('settings.version', { version: Constants.expoConfig?.version ?? '' })}
            </Txt>
        </Screen>
    );
}
