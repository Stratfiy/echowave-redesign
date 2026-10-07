/**
 * A thread with Decibyl: the conversation, cards and connect chips in
 * place, the turn's status under the person's last line, follow-up chips to
 * keep going by tapping, the approval dock, and the composer.
 *
 * `/chat/new` (from a deep link or "Ask Decibyl about ...") becomes a fresh
 * thread id at once, with any `q` put in the box to check before sending.
 */
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useMemo, useRef } from 'react';
import { FlatList, KeyboardAvoidingView, Platform, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApprovalDock } from '@/components/ApprovalDock';
import { Composer } from '@/components/chat/Composer';
import { ThreadRow, TurnStatusLine } from '@/components/chat/Rows';
import { TalkButton } from '@/components/HeaderButtons';
import { Button, Chip, Empty, Failed, Loading } from '@/components/ui';
import { visibleRows, textOf, type Row } from '@/lib/chat/events';
import { newThreadId } from '@/lib/chat/ids';
import { latestTurnStatus } from '@/lib/chat/turnStatus';
import { useThread } from '@/lib/chat/useThread';
import { useFeature } from '@/lib/features';
import { useI18n } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

export default function ThreadScreen() {
    const params = useLocalSearchParams<{ id: string; q?: string }>();
    const router = useRouter();
    useEffect(() => {
        if (params.id === 'new') {
            const q = params.q ? `?q=${encodeURIComponent(params.q)}` : '';
            router.replace(`/chat/${newThreadId()}${q}`);
        }
    }, [params.id, params.q, router]);
    if (params.id === 'new') return <Loading />;
    return <Thread threadId={String(params.id)} initialText={params.q} />;
}

function Thread({ threadId, initialText }: { threadId: string; initialText?: string }) {
    const { t } = useI18n();
    const theme = useTheme();
    const feedbackOn = useFeature('reply_feedback');
    const thread = useThread(threadId);
    const list = useRef<FlatList<Row>>(null);

    const rows = useMemo(() => thread.rows ?? [], [thread.rows]);
    const shown = useMemo(() => visibleRows(rows), [rows]);
    const status = useMemo(() => latestTurnStatus(rows, thread.waiting), [rows, thread.waiting]);
    const lastHuman = [...rows].reverse().find((r) => r.actor === 'human');
    const title = rows.find((r) => r.actor === 'human') ? textOf(rows.find((r) => r.actor === 'human') as Row).slice(0, 40) : t('chat.newChat');

    useEffect(() => {
        if (shown.length) setTimeout(() => list.current?.scrollToEnd({ animated: true }), 50);
    }, [shown.length, thread.draft]);

    const footer = (
        <View style={{ gap: 10, paddingTop: 8 }}>
            <TurnStatusLine status={status} draft={thread.draft} />
            {!thread.waiting && thread.chips.length ? (
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }} testID="follow-up-chips">
                    {thread.chips.slice(0, 4).map((chip, i) => (
                        <Chip key={`${chip.kind}-${i}`} label={chip.text} onPress={() => void thread.send(chip.text)} testID={`chip-${i}`} />
                    ))}
                </View>
            ) : null}
        </View>
    );

    return (
        <SafeAreaView edges={['bottom', 'left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper }} testID="screen-thread">
            <Stack.Screen options={{ title, headerRight: () => <TalkButton threadId={threadId === 'main' ? null : threadId} /> }} />
            <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={90}>
                {thread.rows === null && !thread.error ? (
                    <Loading label={t('common.loading')} />
                ) : (
                    <FlatList
                        ref={list}
                        data={shown}
                        keyExtractor={(row) => String(row.id)}
                        contentContainerStyle={{ padding: 16, gap: 14, flexGrow: 1 }}
                        ListHeaderComponent={
                            <View style={{ gap: 8 }}>
                                {thread.error ? <Failed text={thread.error} onRetry={thread.load} retryLabel={t('common.retry')} /> : null}
                                {thread.hasOlder ? <Button label={t('chat.loadOlder')} kind="quiet" compact onPress={thread.loadOlder} /> : null}
                            </View>
                        }
                        ListEmptyComponent={<Empty text={t('chat.emptyThread')} />}
                        ListFooterComponent={footer}
                        renderItem={({ item }) => (
                            <ThreadRow
                                row={item}
                                showFeedback={feedbackOn}
                                onUpdated={(updated) => thread.replace(updated as Row)}
                                onRetry={lastHuman ? () => void thread.send(textOf(lastHuman)) : undefined}
                            />
                        )}
                    />
                )}
                <ApprovalDock threadId={threadId} onAnswered={thread.load} />
                <Composer
                    waiting={thread.waiting}
                    initialText={initialText}
                    onSend={thread.send}
                    onStop={async () => {
                        await thread.stop().catch(() => false);
                    }}
                />
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
