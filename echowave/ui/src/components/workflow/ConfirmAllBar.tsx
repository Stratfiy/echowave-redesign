'use client';

/**
 * "Confirm all" for a run of send cards on the thread (outreach: one card
 * per lead). One press, still one approval per card: each card is confirmed
 * against the version it showed (POST /timeline/actions/confirm-all,
 * services/workflow/actions.py settle_many), and a card that changed since,
 * or was settled elsewhere, is refused on its own line while the rest go.
 * Every card keeps its own undo window afterwards.
 *
 * Shown only for two or more waiting cards that reach people and carry a
 * preview -- the exact recipient and text the person has just read above.
 */

import { CheckCheck } from 'lucide-react';
import { useState } from 'react';

import { confirmAllActionsApiV1TimelineActionsConfirmAllPost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { actionOf } from '@/components/workflow/ActionCard';
import { detailFromError } from '@/lib/apiError';

/** The waiting send cards Confirm all may name, in thread order. */
export function waitingSends(events: TimelineEvent[]): TimelineEvent[] {
    return events.filter((event) => {
        if (event.kind !== 'action_proposed') return false;
        const action = actionOf(event) as ReturnType<typeof actionOf> & { reaches_people?: boolean };
        return (
            (action.state ?? 'proposed') === 'proposed' &&
            action.action === 'run_tool' &&
            action.reaches_people === true &&
            Boolean(action.version) &&
            Boolean(action.preview)
        );
    });
}

export function ConfirmAllBar({
    events,
    onDone,
}: {
    events: TimelineEvent[];
    /** Called after the press, so the thread refetches every card's state. */
    onDone?: () => void;
}) {
    const waiting = waitingSends(events);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [refused, setRefused] = useState<string[]>([]);

    if (waiting.length < 2 && refused.length === 0 && !error) return null;

    const confirmAll = async () => {
        setSaving(true);
        setError(null);
        setRefused([]);
        const result = await confirmAllActionsApiV1TimelineActionsConfirmAllPost({
            body: {
                items: waiting.map((event) => ({
                    event_id: event.id,
                    version: actionOf(event).version ?? null,
                })),
            },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not confirm these'));
            return;
        }
        const notDone = (result.data?.results ?? []).filter((r) => !r.ok);
        setRefused(
            notDone.map((r) => {
                const card = waiting.find((e) => e.id === r.event_id);
                const to = (actionOf(card ?? ({} as TimelineEvent)).preview ?? '').split('\n')[0];
                return `${to || 'One card'}: ${r.reason ?? 'not confirmed'}`;
            }),
        );
        onDone?.();
    };

    return (
        <div
            className="mt-3 rounded-lg border border-border bg-card p-3"
            role="group"
            aria-label="Confirm all waiting emails"
        >
            {waiting.length >= 2 && (
                <div className="flex flex-wrap items-center gap-3">
                    <p className="flex-1 text-sm">
                        {waiting.length} emails are waiting, each shown above exactly as it will be sent.
                    </p>
                    <Button size="sm" disabled={saving} onClick={() => void confirmAll()}>
                        <CheckCheck aria-hidden className="mr-1 h-3.5 w-3.5" />
                        {saving ? 'Confirming…' : `Confirm all ${waiting.length}`}
                    </Button>
                </div>
            )}
            {refused.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground" aria-label="Not confirmed">
                    {refused.map((line) => (
                        <li key={line}>{line}</li>
                    ))}
                </ul>
            )}
            {error && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
