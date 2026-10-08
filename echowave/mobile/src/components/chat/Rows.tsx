/**
 * One line of a thread. Messages are bubbles; cards and connect chips are
 * their own components; anything else the server writes (a call ended, a
 * deliverable, a decision, a kind added later) is shown as its summary --
 * never dropped (api/AGENTS.md, "Silent Absence").
 */
import { useState } from 'react';
import { View } from 'react-native';

import { giveFeedbackApiV1FeedbackPost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Chip, Txt } from '@/components/ui';
import { call } from '@/lib/api';
import { attachmentsOf, isDecibylReply, isMine, payloadOf, textOf, type Row } from '@/lib/chat/events';
import type { TurnStatus } from '@/lib/chat/turnStatus';
import { formatTime, useI18n, type MessageKey } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

import { ActionCard } from './ActionCard';
import { ConnectChip } from './ConnectChip';

function Feedback({ row }: { row: Row }) {
    const { t } = useI18n();
    const [given, setGiven] = useState<string | null>(null);
    const give = async (verdict: 'yes' | 'not_quite') => {
        setGiven(verdict);
        try {
            await call(giveFeedbackApiV1FeedbackPost({ body: { subject_kind: 'reply', subject_id: row.id, verdict, reasons: [] } }));
        } catch {
            setGiven(null);
        }
    };
    if (given) {
        return (
            <Txt size={12} tone="ink3">
                {t('chat.reply.thanks')}
            </Txt>
        );
    }
    return (
        <View style={{ flexDirection: 'row', gap: 6 }}>
            <Chip label={`👍 ${t('chat.reply.useful')}`} onPress={() => give('yes')} testID={`feedback-yes-${row.id}`} />
            <Chip label={`👎 ${t('chat.reply.notQuite')}`} onPress={() => give('not_quite')} />
        </View>
    );
}

export function MessageRow({
    row,
    showFeedback,
    onRetry,
}: {
    row: Row;
    showFeedback: boolean;
    onRetry?: () => void;
}) {
    const { t, locale } = useI18n();
    const theme = useTheme();
    const mine = isMine(row);
    const p = payloadOf(row);
    const files = attachmentsOf(row);
    return (
        <View style={{ alignItems: mine ? 'flex-end' : 'flex-start', gap: 4 }} testID={`row-${row.id}`}>
            <View
                style={{
                    maxWidth: '88%',
                    backgroundColor: mine ? theme.colors.bubbleMine : 'transparent',
                    borderRadius: theme.radius.lg,
                    paddingHorizontal: mine ? 14 : 2,
                    paddingVertical: mine ? 10 : 2,
                    gap: 4,
                }}
            >
                <Txt selectable>{textOf(row)}</Txt>
                {files.length ? (
                    <Txt size={13} tone="ink2">
                        {t('chat.attachments', { names: files.map((f) => f.filename).join(', ') })}
                    </Txt>
                ) : null}
            </View>
            <Txt size={11} tone="ink3">
                {mine ? t('chat.you') : String(p.from ?? '')} · {formatTime(row.at, locale)}
            </Txt>
            {p.failed || p.stopped ? (
                <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                    <Txt size={13} tone={p.failed ? 'bad' : 'haldi'}>
                        {p.failed ? t('chat.reply.failed') : t('chat.reply.stopped')}
                    </Txt>
                    {onRetry ? <Chip label={t('chat.reply.retry')} onPress={onRetry} /> : null}
                </View>
            ) : null}
            {showFeedback && isDecibylReply(row) && !p.failed ? <Feedback row={row} /> : null}
        </View>
    );
}

export function OtherRow({ row }: { row: Row }) {
    const { locale } = useI18n();
    return (
        <View style={{ paddingVertical: 2 }} testID={`row-${row.id}`}>
            <Txt size={13} tone="ink2">
                {row.summary || row.kind.replace(/_/g, ' ')} · {formatTime(row.at, locale)}
            </Txt>
        </View>
    );
}

export function ThreadRow({
    row,
    showFeedback,
    onUpdated,
    onRetry,
}: {
    row: Row;
    showFeedback: boolean;
    onUpdated: (row: TimelineEvent) => void;
    onRetry?: () => void;
}) {
    if (row.kind === 'action_proposed') return <ActionCard row={row} onUpdated={onUpdated} />;
    if (row.kind === 'connector_offered' || row.kind === 'reach_connect_offered') return <ConnectChip row={row} />;
    if (row.kind === 'message') return <MessageRow row={row} showFeedback={showFeedback} onRetry={onRetry} />;
    return <OtherRow row={row} />;
}

export function TurnStatusLine({ status, draft }: { status: TurnStatus | null; draft: string }) {
    const { t } = useI18n();
    const theme = useTheme();
    if (!status) return null;
    const label = t(`task.${status.state}` as MessageKey);
    const active = status.state === 'running' || status.state === 'queued';
    return (
        <View style={{ gap: 4 }} testID="turn-status" accessibilityLiveRegion="polite">
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View
                    style={{
                        width: 8,
                        height: 8,
                        borderRadius: 4,
                        backgroundColor:
                            status.state === 'completed' ? theme.colors.live : status.state === 'failed' ? theme.colors.bad : active ? theme.colors.haldi : theme.colors.ink3,
                    }}
                />
                <Txt size={13} weight="600" tone="ink2">
                    {active ? t('chat.thinking') : label}
                </Txt>
                {status.stage && active ? (
                    <Txt size={13} tone="ink3" numberOfLines={1} style={{ flex: 1 }}>
                        · {status.stage}
                    </Txt>
                ) : null}
            </View>
            {active && draft ? (
                <Txt tone="ink2" testID="reply-draft">
                    {draft}
                </Txt>
            ) : null}
        </View>
    );
}
