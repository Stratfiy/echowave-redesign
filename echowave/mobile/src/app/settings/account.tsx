/**
 * Account: the name people see and the time zone, on the person's own
 * profile (GET/PUT /me/settings/profile, flag `settings_shell`). The sign-in
 * email is shown, not edited -- there is no endpoint to change it, on the
 * web either. A stale save (409) shows the stored values and says so.
 */
import { useLoad } from '@/lib/useLoad';
import { Stack } from 'expo-router';
import { useCallback, useState } from 'react';

import { myProfileApiV1MeSettingsProfileGet, saveMyProfileApiV1MeSettingsProfilePut } from '@/client/sdk.gen';
import type { Profile } from '@/client/types.gen';
import { Button, Field, Loading, Notice, Row, Screen, Section } from '@/components/ui';
import { ApiError, call } from '@/lib/api';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useI18n } from '@/lib/i18n';

export default function Account() {
    const { t } = useI18n();
    const { user } = useAuth();
    const [profile, setProfile] = useState<Profile | null>(null);
    const [unavailable, setUnavailable] = useState(false);
    const [name, setName] = useState('');
    const [timezone, setTimezone] = useState('');
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState<{ text: string; tone: 'live' | 'bad' | 'haldi' } | null>(null);

    const apply = (p: Profile) => {
        setProfile(p);
        setName(p.preferred_name ?? '');
        setTimezone(p.timezone ?? '');
    };

    const load = useCallback(async () => {
        try {
            apply(await call(myProfileApiV1MeSettingsProfileGet()));
        } catch (e) {
            if (e instanceof ApiError && (e.status === 404 || e.status === 503)) setUnavailable(true);
            else setNotice({ text: e instanceof Error ? e.message : String(e), tone: 'bad' });
        }
    }, []);

    useLoad(load, [load]);

    const save = async () => {
        if (!profile) return;
        setBusy(true);
        setNotice(null);
        try {
            apply(
                await call(
                    saveMyProfileApiV1MeSettingsProfilePut({
                        body: { revision: profile.revision, preferred_name: name.trim() || null, timezone: timezone.trim() || null },
                    }),
                ),
            );
            setNotice({ text: t('common.saved'), tone: 'live' });
        } catch (e) {
            const stored = e instanceof ApiError && e.status === 409 ? (e.detail as { stored?: Profile })?.stored : undefined;
            if (stored) {
                apply(stored);
                setNotice({ text: t('common.changedElsewhere'), tone: 'haldi' });
            } else setNotice({ text: e instanceof Error ? e.message : String(e), tone: 'bad' });
        } finally {
            setBusy(false);
        }
    };

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-account">
            <Stack.Screen options={{ title: t('settings.account') }} />
            <Section>
                <Row title={t('settings.email')} subtitle={profile?.email ?? user?.email ?? ''} />
            </Section>
            {unavailable ? (
                <>
                    <Row title={t('settings.name')} subtitle={user?.name ?? ''} />
                    <Notice text={t('settings.notSwitchedOn')} />
                </>
            ) : !profile ? (
                <Loading />
            ) : (
                <>
                    <Field label={t('settings.name')} value={name} onChangeText={setName} autoComplete="name" testID="account-name" />
                    <Field label={t('settings.timezone')} value={timezone} onChangeText={setTimezone} autoCapitalize="none" placeholder="Asia/Kolkata" testID="account-timezone" />
                    <Button label={t('common.save')} onPress={save} busy={busy} testID="account-save" />
                </>
            )}
            {notice ? <Notice text={notice.text} tone={notice.tone} /> : null}
        </Screen>
    );
}
