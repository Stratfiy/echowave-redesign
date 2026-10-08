import { useEffect } from 'react';
import { View } from 'react-native';

import { Button, Screen, Title, Txt } from '@/components/ui';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useI18n } from '@/lib/i18n';

/** Biometric unlock: asked once on open; the person can always sign out. */
export default function Unlock() {
    const { t } = useI18n();
    const { unlock, signOut } = useAuth();
    useEffect(() => {
        void unlock(t('auth.unlock.prompt'), t('auth.unlock.usePassword'));
    }, [unlock, t]);
    return (
        <Screen testID="screen-unlock">
            <View style={{ height: 80 }} />
            <Title>{t('auth.unlock.title')}</Title>
            <Txt tone="ink2">{t('settings.biometricHint')}</Txt>
            <Button label={t('auth.unlock.button')} onPress={() => unlock(t('auth.unlock.prompt'), t('auth.unlock.usePassword'))} />
            <Button label={t('auth.unlock.signOut')} kind="quiet" onPress={() => signOut()} />
        </Screen>
    );
}
