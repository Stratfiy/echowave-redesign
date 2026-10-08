import { useState } from 'react';

import { Button, Field, Notice, Screen, Title, Txt } from '@/components/ui';
import { verifyEmail } from '@/lib/auth/auth';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useI18n } from '@/lib/i18n';

export default function VerifyEmail() {
    const { t } = useI18n();
    const { user, verified, signOut } = useAuth();
    const [code, setCode] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const submit = async () => {
        setBusy(true);
        setError(null);
        try {
            await verifyEmail(code);
            verified();
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };
    return (
        <Screen testID="screen-verify-email">
            <Title>{t('auth.verify.title')}</Title>
            <Txt tone="ink2">{t('auth.verify.hint', { email: user?.email ?? '' })}</Txt>
            <Field label={t('auth.mfa.title')} keyboardType="number-pad" autoComplete="one-time-code" value={code} onChangeText={setCode} />
            {error ? <Notice text={error} tone="bad" /> : null}
            <Button label={t('auth.verify.submit')} onPress={submit} busy={busy} disabled={!code} />
            <Button label={t('settings.signOut')} kind="quiet" onPress={() => signOut()} />
        </Screen>
    );
}
