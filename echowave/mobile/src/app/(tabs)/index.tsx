/**
 * Chat: the person's threads with Decibyl, newest first, and a new chat.
 * In Simple mode (CARE.md) voice comes first: one large "Talk to Decibyl",
 * then typing, then the threads.
 */
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, Pressable, RefreshControl, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { threadsApiV1TimelineThreadsGet } from '@/client/sdk.gen';
import type { ThreadSummary } from '@/client/types.gen';
import { Button, Divider, Empty, Failed, Loading, Txt } from '@/components/ui';
import { call } from '@/lib/api';
import { newThreadId } from '@/lib/chat/ids';
import { formatTime, useI18n } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

export default function ChatList() {
    const { t, locale } = useI18n();
    const theme = useTheme();
    const router = useRouter();
    const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [refreshing, setRefreshing] = useState(false);

    const load = useCallback(async () => {
        try {
            const data = await call(threadsApiV1TimelineThreadsGet({ query: { limit: 50 } }));
            setThreads(data.threads);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : t('common.offline'));
        }
    }, [t]);

    useFocusEffect(
        useCallback(() => {
            void load();
        }, [load]),
    );

    const openNew = () => router.push(`/chat/${newThreadId()}`);

    const header = (
        <View style={{ padding: 16, gap: 12 }}>
            {theme.simple ? (
                <>
                    <Button label={t('simple.talk')} icon="🎙" onPress={() => router.push('/voice')} testID="simple-talk" style={{ minHeight: 88 }} />
                    <Button label={t('simple.type')} kind="secondary" onPress={openNew} testID="simple-type" />
                </>
            ) : (
                <Button label={t('chat.newChat')} onPress={openNew} testID="new-chat" icon="＋" />
            )}
            {error ? <Failed text={error} onRetry={load} retryLabel={t('common.retry')} /> : null}
        </View>
    );

    return (
        <SafeAreaView edges={['left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper }} testID="screen-chat-list">
            <FlatList
                data={threads ?? []}
                keyExtractor={(item) => item.thread_id ?? 'main'}
                ListHeaderComponent={header}
                ItemSeparatorComponent={Divider}
                refreshControl={
                    <RefreshControl
                        refreshing={refreshing}
                        onRefresh={async () => {
                            setRefreshing(true);
                            await load();
                            setRefreshing(false);
                        }}
                    />
                }
                ListEmptyComponent={threads === null ? <Loading label={t('common.loading')} /> : <Empty text={t('chat.threads.empty')} />}
                renderItem={({ item }) => (
                    <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={item.title || t('chat.threads.main')}
                        onPress={() => router.push(`/chat/${item.thread_id ?? 'main'}`)}
                        style={({ pressed }) => ({ paddingHorizontal: 16, paddingVertical: 12, flexDirection: 'row', gap: 12, alignItems: 'center', opacity: pressed ? 0.6 : 1, minHeight: theme.control })}
                        testID={`thread-${item.thread_id ?? 'main'}`}
                    >
                        <Ionicons name="chatbubble-outline" size={20} color={theme.colors.ink2} />
                        <View style={{ flex: 1 }}>
                            <Txt weight="600" numberOfLines={1}>
                                {item.title || t('chat.threads.main')}
                            </Txt>
                            <Txt size={13} tone="ink2">
                                {item.messages === 1 ? t('chat.threads.oneMessage') : t('chat.threads.messages', { count: item.messages })} · {formatTime(item.last_at, locale)}
                            </Txt>
                        </View>
                        <Txt tone="ink3">›</Txt>
                    </Pressable>
                )}
            />
        </SafeAreaView>
    );
}
