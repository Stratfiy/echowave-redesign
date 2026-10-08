/**
 * A pending approval docked above the composer until it is answered (the
 * founder's approval design, flag `approval_dock`): one sentence, one line
 * of exact detail, "Do it" and "Don't". Answers go through the same
 * confirm-once settler as the card in the thread, so the dock and the card
 * pressed together are one request.
 */
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { View } from 'react-native';

import { pendingApprovalsApiV1TodayApprovalsGet } from '@/client/sdk.gen';
import { Button, Card, Notice, Txt } from '@/components/ui';
import { call } from '@/lib/api';
import { settler } from '@/lib/approvals/settler';
import { useFeature } from '@/lib/features';
import { useI18n } from '@/lib/i18n';
import type { ApprovalQueue } from '@/lib/today/types';

export const DOCK_POLL_MS = 5_000;

export function ApprovalDock({ threadId, onAnswered }: { threadId?: string | null; onAnswered?: () => void }) {
    const on = useFeature('approval_dock');
    const { t } = useI18n();
    const router = useRouter();
    const [queue, setQueue] = useState<ApprovalQueue | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setQueue((await call(pendingApprovalsApiV1TodayApprovalsGet())) as unknown as ApprovalQueue);
        } catch {
            setQueue(null);
        }
    }, []);

    useEffect(() => {
        if (!on) return;
        void Promise.resolve().then(load);
        const timer = setInterval(() => void load(), DOCK_POLL_MS);
        return () => clearInterval(timer);
    }, [on, load]);

    // In a thread, its own cards are already on screen in place; the dock
    // holds what waits elsewhere, so nothing is shown twice.
    const items = (queue?.items ?? []).filter((i) => !threadId || (i.thread_id ?? 'main') !== threadId);
    if (!on || !items.length) return null;
    const item = items[0];
    const answer = async (verb: 'confirm' | 'decline') => {
        setBusy(true);
        setError(null);
        try {
            await settler.answer(item.id, verb, item.version);
            onAnswered?.();
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };
    return (
        <Card testID="approval-dock" style={{ marginHorizontal: 8, marginBottom: 6 }}>
            <Txt weight="700">{item.sentence || `${t('card.wants')} ${item.label}`}</Txt>
            {item.detail ? <Txt size={14}>{item.detail}</Txt> : null}
            <View style={{ flexDirection: 'row', gap: 8 }}>
                <Button label={t('card.confirm')} onPress={() => answer('confirm')} busy={busy} compact style={{ flex: 1 }} testID="dock-confirm" />
                <Button label={t('card.decline')} kind="secondary" onPress={() => answer('decline')} disabled={busy} compact style={{ flex: 1 }} />
                <Button label={t('card.open')} kind="quiet" compact onPress={() => router.push(`/approvals/${item.id}`)} />
            </View>
            {items.length > 1 ? (
                <Txt size={12} tone="ink3">
                    +{items.length - 1}
                </Txt>
            ) : null}
            {error ? <Notice text={error} tone="bad" /> : null}
        </Card>
    );
}
