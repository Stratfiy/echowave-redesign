import { Link } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, Text, View } from 'react-native';

import { Button, Field, Notice, Screen, Title, Txt } from '@/components/ui';
import { LEGAL_LINKS, signUp } from '@/lib/auth/auth';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useFeature } from '@/lib/features';
import { useI18n } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

export default function SignUp() {
    const { t } = useI18n();
    const theme = useTheme();
    const { accept } = useAuth();
    const inviteOnly = useFeature('invite_only_signup');
    const [email, setEmail] = useState('');
    const [name, setName] = useState('');
    const [password, setPassword] = useState('');
    const [invite, setInvite] = useState('');
    const [agreed, setAgreed] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async () => {
        setBusy(true);
        setError(null);
        const result = await signUp({ email, password, name, inviteCode: invite, agreed });
        setBusy(false);
        if (result.kind === 'signed_up') await accept(result.auth);
        else setError(result.message);
    };

    return (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
            <Screen testID="screen-sign-up">
                <View style={{ height: 16 }} />
                <Title>{t('auth.signUp.title')}</Title>
                <Txt tone="ink2">{t('auth.signUp.hint')}</Txt>
                {inviteOnly ? (
                    <Field
                        label={t('auth.signUp.invite')}
                        hint={t('auth.signUp.inviteHint')}
                        autoCapitalize="characters"
                        value={invite}
                        onChangeText={setInvite}
                        testID="sign-up-invite"
                    />
                ) : null}
                <Field label={t('auth.email')} placeholder={t('auth.emailPlaceholder')} autoCapitalize="none" keyboardType="email-address" autoComplete="email" value={email} onChangeText={setEmail} testID="sign-up-email" />
                <Field label={t('auth.signUp.name')} autoComplete="name" value={name} onChangeText={setName} testID="sign-up-name" />
                <Field label={t('auth.password')} hint={t('auth.signUp.passwordHint')} secureTextEntry autoComplete="new-password" textContentType="newPassword" value={password} onChangeText={setPassword} testID="sign-up-password" />
                <Pressable
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: agreed }}
                    onPress={() => setAgreed(!agreed)}
                    style={{ flexDirection: 'row', gap: 10, alignItems: 'flex-start', minHeight: theme.control, paddingVertical: 6 }}
                    testID="sign-up-agree"
                >
                    <View
                        style={{
                            width: 22,
                            height: 22,
                            borderRadius: 4,
                            borderWidth: 2,
                            borderColor: theme.colors.ink,
                            backgroundColor: agreed ? theme.colors.ink : 'transparent',
                            marginTop: 2,
                        }}
                    />
                    <Txt style={{ flex: 1 }}>
                        {t('auth.signUp.agreePrefix')}{' '}
                        <Txt weight="600" style={{ textDecorationLine: 'underline' }}>
                            <Text accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(LEGAL_LINKS.terms)}>
                                {t('auth.signUp.terms')}
                            </Text>
                        </Txt>{' '}
                        {t('auth.signUp.and')}{' '}
                        <Txt weight="600" style={{ textDecorationLine: 'underline' }}>
                            <Text accessibilityRole="link" onPress={() => WebBrowser.openBrowserAsync(LEGAL_LINKS.privacy)}>
                                {t('auth.signUp.privacy')}
                            </Text>
                        </Txt>
                    </Txt>
                </Pressable>
                {error ? <Notice text={error} tone="bad" testID="sign-up-error" /> : null}
                <Button label={busy ? t('auth.signUp.submitting') : t('auth.signUp.submit')} onPress={submit} busy={busy} testID="sign-up-submit" />
                <View style={{ flexDirection: 'row', gap: 6, justifyContent: 'center', marginTop: 8 }}>
                    <Txt tone="ink2">{t('auth.signUp.haveAccount')}</Txt>
                    <Link href="/sign-in">
                        <Txt weight="600">{t('auth.signIn.submit')}</Txt>
                    </Link>
                </View>
            </Screen>
        </KeyboardAvoidingView>
    );
}
