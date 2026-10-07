import { Link } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, View } from 'react-native';

import { Button, Field, Notice, Screen, Title, Txt } from '@/components/ui';
import { signIn } from '@/lib/auth/auth';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useI18n } from '@/lib/i18n';

export default function SignIn() {
    const { t } = useI18n();
    const { accept, notice } = useAuth();
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [mfa, setMfa] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async () => {
        setBusy(true);
        setError(null);
        const result = await signIn({ email, password, mfaCode: mfa ?? undefined });
        setBusy(false);
        if (result.kind === 'signed_in') await accept(result.auth);
        else if (result.kind === 'mfa_required') setMfa('');
        else setError(result.message);
    };

    return (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
            <Screen testID="screen-sign-in">
                <View style={{ height: 32 }} />
                <Txt size={15} weight="700" tone="ink2">
                    {t('app.name')}
                </Txt>
                <Title>{mfa === null ? t('auth.signIn.title') : t('auth.mfa.title')}</Title>
                <Txt tone="ink2">{mfa === null ? t('auth.signIn.hint') : t('auth.mfa.hint')}</Txt>
                {notice ? <Notice text={notice} tone="haldi" /> : null}
                {mfa === null ? (
                    <>
                        <Field
                            label={t('auth.email')}
                            placeholder={t('auth.emailPlaceholder')}
                            autoCapitalize="none"
                            autoComplete="email"
                            keyboardType="email-address"
                            textContentType="username"
                            value={email}
                            onChangeText={setEmail}
                            testID="sign-in-email"
                        />
                        <Field
                            label={t('auth.password')}
                            secureTextEntry
                            autoComplete="password"
                            textContentType="password"
                            value={password}
                            onChangeText={setPassword}
                            onSubmitEditing={submit}
                            testID="sign-in-password"
                        />
                    </>
                ) : (
                    <Field
                        label={t('auth.mfa.title')}
                        keyboardType="number-pad"
                        autoComplete="one-time-code"
                        textContentType="oneTimeCode"
                        value={mfa}
                        onChangeText={setMfa}
                        onSubmitEditing={submit}
                        testID="sign-in-mfa"
                    />
                )}
                {error ? <Notice text={error} tone="bad" testID="sign-in-error" /> : null}
                <Button
                    label={mfa === null ? (busy ? t('auth.signIn.submitting') : t('auth.signIn.submit')) : t('auth.mfa.submit')}
                    onPress={submit}
                    busy={busy}
                    disabled={!email || !password}
                    testID="sign-in-submit"
                />
                <View style={{ flexDirection: 'row', gap: 6, justifyContent: 'center', marginTop: 8 }}>
                    <Txt tone="ink2">{t('auth.signIn.noAccount')}</Txt>
                    <Link href="/sign-up" accessibilityRole="link">
                        <Txt weight="600">{t('auth.signIn.createAccount')}</Txt>
                    </Link>
                </View>
            </Screen>
        </KeyboardAvoidingView>
    );
}
