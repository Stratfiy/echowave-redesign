/**
 * Language: Decibyl's language for this person (member preferences, used
 * for answers, briefs and voice), and the app's own UI language -- Hindi or
 * English -- which follows it unless chosen here.
 */
import { Stack } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { Chip, Divider, Notice, Row, Screen, Section, Txt } from '@/components/ui';
import { useAppSettings } from '@/lib/appSettings';
import { useI18n } from '@/lib/i18n';
import { usePreferences } from '@/lib/preferences';

const NAMES: Record<string, string> = {
    'en-IN': 'English (India)',
    'hi-IN': 'हिन्दी · Hindi',
    'bn-IN': 'বাংলা · Bengali',
    'ta-IN': 'தமிழ் · Tamil',
    'te-IN': 'తెలుగు · Telugu',
    'kn-IN': 'ಕನ್ನಡ · Kannada',
    'ml-IN': 'മലയാളം · Malayalam',
    'mr-IN': 'मराठी · Marathi',
    'gu-IN': 'ગુજરાતી · Gujarati',
    'pa-IN': 'ਪੰਜਾਬੀ · Punjabi',
    'od-IN': 'ଓଡ଼ିଆ · Odia',
};

export default function Language() {
    const { t } = useI18n();
    const { prefs, save, available } = usePreferences();
    const { localeOverride, setLocaleOverride } = useAppSettings();
    const [error, setError] = useState<string | null>(null);
    const languages = prefs?.languages?.length ? prefs.languages : Object.keys(NAMES);

    const choose = async (tag: string) => {
        setError(null);
        try {
            await save({ language: tag });
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    };

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-language">
            <Stack.Screen options={{ title: t('settings.language') }} />
            <Section title={t('settings.appLanguage')}>
                <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                    <Chip label="English" selected={localeOverride === 'en'} onPress={() => setLocaleOverride('en')} testID="locale-en" />
                    <Chip label="हिन्दी" selected={localeOverride === 'hi'} onPress={() => setLocaleOverride('hi')} testID="locale-hi" />
                    <Chip label={t('settings.theme.system')} selected={localeOverride === null} onPress={() => setLocaleOverride(null)} />
                </View>
            </Section>
            <Section title={t('settings.language')}>
                <Txt size={13} tone="ink2">
                    {t('settings.languageHint')}
                </Txt>
                {available ? (
                    languages.map((tag, i) => (
                        <View key={tag}>
                            {i ? <Divider /> : null}
                            <Row title={NAMES[tag] ?? tag} onPress={() => choose(tag)} right={prefs?.language === tag ? <Txt weight="700">✓</Txt> : null} testID={`language-${tag}`} />
                        </View>
                    ))
                ) : (
                    <Notice text={t('settings.notSwitchedOn')} />
                )}
            </Section>
            {error ? <Notice text={error} tone="bad" /> : null}
        </Screen>
    );
}
