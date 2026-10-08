/**
 * An action card in the thread: exactly what Decibyl wants to do, and one
 * Confirm ("Do it") that runs it once.
 *
 * The exact text is the card's own (`label`, `preview`, `effect`, `why`)
 * as the server wrote it -- nothing is paraphrased. Confirm names the
 * version shown; the settler (src/lib/approvals/confirmOnce.ts) makes a
 * double tap one request, and the server's compare-and-swap makes two
 * devices one run. After Confirm there is a 10-second undo window, then the
 * honest after-states, including "outcome unknown".
 */
import { useEffect, useState } from 'react';
import { View } from 'react-native';

import { Button, Card, Notice, Txt } from '@/components/ui';
import type { TimelineEvent } from '@/client/types.gen';
import { settler } from '@/lib/approvals/settler';
import { payloadOf, type Row } from '@/lib/chat/events';
import { useI18n, type MessageKey } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

const STATE_LINE: Record<string, MessageKey> = {
    armed: 'card.state.armed',
    running: 'card.state.running',
    done: 'card.state.done',
    failed: 'card.state.failed',
    declined: 'card.state.declined',
    cancelled: 'card.state.cancelled',
    undone: 'card.state.undone',
    outcome_unknown: 'card.state.outcome_unknown',
};

export function ActionCard({ row, onUpdated }: { row: Row; onUpdated: (row: TimelineEvent) => void }) {
    const { t } = useI18n();
    const theme = useTheme();
    const p = payloadOf(row);
    const state = String(p.state ?? 'proposed');
    const version = typeof p.version === 'string' ? p.version : null;
    const [busy, setBusy] = useState<'confirm' | 'decline' | 'undo' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [now, setNow] = useState(() => Date.now());

    const firesAt = typeof p.fires_at === 'string' ? Date.parse(p.fires_at) : null;
    const secondsLeft = firesAt ? Math.max(0, Math.ceil((firesAt - now) / 1000)) : 0;

    useEffect(() => {
        if (state !== 'armed' || !firesAt) return;
        const timer = setInterval(() => setNow(Date.now()), 500);
        return () => clearInterval(timer);
    }, [state, firesAt]);

    const answer = async (verb: 'confirm' | 'decline' | 'undo') => {
        setBusy(verb);
        setError(null);
        try {
            onUpdated(await settler.answer(row.id, verb, version));
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    };

    const detail = [p.preview, p.effect].filter((x): x is string => typeof x === 'string' && x.length > 0);
    const done = p.done as { note?: string } | undefined;
    return (
        <Card testID={`card-${row.id}`} style={{ borderColor: state === 'proposed' ? theme.colors.ink : theme.colors.line, borderWidth: 1 }}>
            <Txt size={13} weight="600" tone="ink2">
                {t('card.wants')}
            </Txt>
            <Txt weight="700" selectable>
                {String(p.label ?? row.summary)}
            </Txt>
            {detail.map((line, i) => (
                <Txt key={i} size={14} tone="ink" selectable>
                    {line}
                </Txt>
            ))}
            {typeof p.why === 'string' && p.why ? (
                <Txt size={13} tone="ink2">
                    {t('card.why', { why: p.why })}
                </Txt>
            ) : null}
            {state === 'proposed' ? (
                <>
                    <Txt size={12} tone="ink3">
                        {t('card.once')}
                    </Txt>
                    <View style={{ flexDirection: 'row', gap: 8 }}>
                        <Button
                            label={busy === 'confirm' ? t('card.confirming') : t('card.confirm')}
                            onPress={() => answer('confirm')}
                            busy={busy === 'confirm'}
                            disabled={busy !== null}
                            testID={`card-${row.id}-confirm`}
                            style={{ flex: 1 }}
                        />
                        <Button
                            label={t('card.decline')}
                            kind="secondary"
                            onPress={() => answer('decline')}
                            disabled={busy !== null}
                            testID={`card-${row.id}-decline`}
                            style={{ flex: 1 }}
                        />
                    </View>
                </>
            ) : (
                <View style={{ gap: 6 }}>
                    <Txt size={14} weight="600" tone={state === 'done' ? 'live' : state === 'failed' ? 'bad' : 'ink'}>
                        {state === 'armed' && secondsLeft > 0 ? t('card.runsIn', { seconds: secondsLeft }) : t(STATE_LINE[state] ?? 'card.state.running')}
                    </Txt>
                    {done?.note ? <Txt size={13} tone="ink2">{done.note}</Txt> : null}
                    {typeof p.error === 'string' && p.error ? <Txt size={13} tone="bad">{p.error}</Txt> : null}
                    {(state === 'armed' && secondsLeft > 0) || (state === 'done' && p.reversible) ? (
                        <Button label={t('card.undo')} kind="secondary" compact onPress={() => answer('undo')} busy={busy === 'undo'} testID={`card-${row.id}-undo`} />
                    ) : null}
                </View>
            )}
            {error ? <Notice text={error} tone="bad" /> : null}
        </Card>
    );
}
