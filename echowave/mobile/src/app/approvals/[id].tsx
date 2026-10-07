/**
 * Exact action approval (screen 08): every detail the server binds the
 * Confirm to -- what, to whom, from which account, how much, when, and what
 * happens -- and one "Do it" that runs once. Someone else's card (their
 * consent, order or computer) says so instead of offering buttons.
 */
import { useLoad } from '@/lib/useLoad';
import { Stack, useLocalSearchParams } from 'expo-router';
import { useCallback, useState } from 'react';
import { View } from 'react-native';

import { approvalPreviewApiV1TodayApprovalsEventIdGet } from '@/client/sdk.gen';
import { Button, Card, Divider, Failed, Loading, Notice, Row, Screen, Title, Txt } from '@/components/ui';
import { call } from '@/lib/api';
import { settler } from '@/lib/approvals/settler';
import { useI18n, type MessageKey } from '@/lib/i18n';
import type { ApprovalPreview } from '@/lib/today/types';

const SCREEN_STATE: Record<string, MessageKey> = {
    approved: 'card.state.armed',
    executing: 'card.state.running',
    completed: 'card.state.done',
    failed: 'card.state.failed',
    outcome_unknown: 'card.state.outcome_unknown',
    cancelled: 'card.state.cancelled',
};

export default function Approval() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const { t } = useI18n();
    const [view, setView] = useState<ApprovalPreview | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        try {
            setView((await call(approvalPreviewApiV1TodayApprovalsEventIdGet({ path: { event_id: Number(id) } }))) as unknown as ApprovalPreview);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    }, [id]);

    useLoad(load, [load]);

    const answer = async (verb: 'confirm' | 'decline' | 'undo') => {
        if (!view) return;
        setBusy(true);
        try {
            await settler.answer(view.id, verb, view.version);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
            await load();
        }
    };

    const fields: [MessageKey, string | null][] = view
        ? [
              ['approval.detail', view.detail],
              ['approval.account', view.account],
              ['approval.recipient', view.recipient],
              ['approval.amount', view.amount],
              ['approval.timing', view.timing],
              ['approval.consequence', view.consequence],
          ]
        : [];

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-approval">
            <Stack.Screen options={{ title: t('approval.title') }} />
            {error ? <Failed text={error} onRetry={load} retryLabel={t('common.retry')} /> : null}
            {!view && !error ? <Loading /> : null}
            {view ? (
                <>
                    <Title>{view.sentence}</Title>
                    <Card>
                        {fields
                            .filter(([, value]) => value)
                            .map(([label, value], i) => (
                                <View key={label}>
                                    {i ? <Divider /> : null}
                                    <Row title={t(label)} subtitle={value} />
                                </View>
                            ))}
                        {view.content ? (
                            <Txt size={14} selectable>
                                {view.content}
                            </Txt>
                        ) : null}
                        {view.attachments?.length ? <Txt size={13} tone="ink2">{t('chat.attachments', { names: view.attachments.join(', ') })}</Txt> : null}
                    </Card>
                    {view.why ? <Txt tone="ink2">{t('card.why', { why: view.why })}</Txt> : null}
                    <Txt size={13} tone="ink2">
                        {view.reversible ? t('approval.reversible') : t('approval.notReversible')} · {t('card.once')}
                    </Txt>
                    {view.screen_state === 'pending' ? (
                        view.can_answer ? (
                            <View style={{ flexDirection: 'row', gap: 8 }}>
                                <Button label={t('card.confirm')} onPress={() => answer('confirm')} busy={busy} style={{ flex: 1 }} testID="approval-confirm" />
                                <Button label={t('card.decline')} kind="secondary" onPress={() => answer('decline')} disabled={busy} style={{ flex: 1 }} />
                            </View>
                        ) : (
                            <Notice text={view.answer_refusal || t('approval.cannotAnswer')} />
                        )
                    ) : (
                        <Notice text={t(SCREEN_STATE[view.screen_state] ?? 'card.state.running')} tone={view.screen_state === 'failed' ? 'bad' : view.screen_state === 'completed' ? 'live' : 'ink2'} />
                    )}
                    {view.screen_state === 'approved' ? <Button label={t('card.undo')} kind="secondary" onPress={() => answer('undo')} /> : null}
                    {view.error ? <Notice text={view.error} tone="bad" /> : null}
                    {view.done_note ? <Txt tone="ink2">{view.done_note}</Txt> : null}
                </>
            ) : null}
        </Screen>
    );
}
