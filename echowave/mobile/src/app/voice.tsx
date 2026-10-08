/**
 * Talk with Decibyl (screen 05): a live conversation, not dictation. A
 * large state label, captions, Mute and End. Readiness is read first, so
 * "needs setup" or "turned off by your workspace" is said before the
 * microphone is asked for. Approvals never happen by voice: a card waits in
 * Chat and the screen says so.
 */
import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { voiceReadinessApiV1VoiceReadinessGet } from '@/client/sdk.gen';
import type { VoiceReadiness } from '@/client/types.gen';
import { Button, Notice, Toggle, Txt } from '@/components/ui';
import { ApiError, call } from '@/lib/api';
import { useI18n, type MessageKey } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';
import { ACTIVE, FINAL } from '@/lib/voice/sessionState';
import { useLiveVoice } from '@/lib/voice/useLiveVoice';

export default function Voice() {
    const { thread } = useLocalSearchParams<{ thread?: string }>();
    const { t } = useI18n();
    const theme = useTheme();
    const router = useRouter();
    const voice = useLiveVoice();
    const [readiness, setReadiness] = useState<VoiceReadiness | null | 'off'>(null);
    const [captions, setCaptions] = useState(true);
    const { state } = voice;

    useEffect(() => {
        call(voiceReadinessApiV1VoiceReadinessGet())
            .then(setReadiness)
            .catch((e) => setReadiness(e instanceof ApiError && e.status === 404 ? 'off' : null));
    }, []);

    const live = readiness && readiness !== 'off' ? readiness.live_voice : null;
    const blocked = readiness === 'off' || (live && live.state !== 'available');
    const active = ACTIVE.has(state.phase);

    const close = async () => {
        if (active) await voice.end();
        router.back();
    };

    return (
        <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.paper2 }} testID="screen-voice">
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 8 }}>
                <Txt size={17} weight="700" style={{ paddingLeft: 8 }}>
                    {t('voice.title')}
                </Txt>
                <Pressable accessibilityRole="button" accessibilityLabel={t('common.close')} onPress={close} style={{ padding: 10 }} testID="voice-close">
                    <Ionicons name="close" size={26} color={theme.colors.ink} />
                </Pressable>
            </View>
            <ScrollView contentContainerStyle={{ padding: 20, gap: 16, flexGrow: 1 }}>
                <View style={{ alignItems: 'center', gap: 16, paddingVertical: 24 }}>
                    <View
                        style={{
                            width: 140,
                            height: 140,
                            borderRadius: 70,
                            alignItems: 'center',
                            justifyContent: 'center',
                            backgroundColor: state.phase === 'speaking' ? theme.colors.live : state.phase === 'listening' ? theme.colors.ink : theme.colors.line,
                        }}
                    >
                        <Ionicons name={state.muted ? 'mic-off' : 'mic'} size={56} color={state.phase === 'listening' || state.phase === 'speaking' ? theme.colors.paper : theme.colors.ink2} />
                    </View>
                    <Txt size={26} weight="700" testID="voice-state" style={{ textAlign: 'center' }}>
                        {t(`voice.state.${state.phase}` as MessageKey)}
                    </Txt>
                    {state.notice ? <Txt tone="ink2" style={{ textAlign: 'center' }}>{state.notice}</Txt> : null}
                </View>
                {blocked ? (
                    <Notice
                        text={readiness === 'off' ? t('voice.state.needs_setup') : [live?.reason, live?.next_step].filter(Boolean).join(' ') || t('voice.state.needs_setup')}
                        tone="haldi"
                        testID="voice-blocked"
                    />
                ) : null}
                {state.approvalWaiting ? <Notice text={t('voice.approvalsNote')} tone="haldi" /> : null}
                {captions && state.captions.length ? (
                    <View style={{ gap: 8 }} testID="voice-captions">
                        {state.captions.map((c) => (
                            <Txt key={c.id} tone={c.who === 'you' ? 'ink2' : 'ink'} weight={c.who === 'you' ? '400' : '500'}>
                                {c.who === 'you' ? `${t('chat.you')}: ` : 'Decibyl: '}
                                {c.text}
                            </Txt>
                        ))}
                    </View>
                ) : null}
                <View style={{ flex: 1 }} />
                <Toggle label={t('voice.captions')} value={captions} onChange={setCaptions} />
                {active ? (
                    <View style={{ flexDirection: 'row', gap: 12 }}>
                        <Button label={state.muted ? t('voice.unmute') : t('voice.mute')} kind="secondary" onPress={voice.toggleMute} style={{ flex: 1 }} testID="voice-mute" />
                        <Button label={t('voice.end')} kind="danger" onPress={() => voice.end()} style={{ flex: 1 }} testID="voice-end" />
                    </View>
                ) : (
                    <Button
                        label={t('voice.start')}
                        onPress={() => voice.start(thread ?? null)}
                        disabled={Boolean(blocked) || readiness === null}
                        style={{ minHeight: theme.simple ? 88 : 56 }}
                        testID="voice-start"
                    />
                )}
                {FINAL.has(state.phase) || state.phase === 'idle' ? (
                    <Txt size={13} tone="ink2" style={{ textAlign: 'center' }}>
                        {t('voice.notDictation')}
                    </Txt>
                ) : null}
            </ScrollView>
        </SafeAreaView>
    );
}
