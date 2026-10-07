'use client';

/**
 * A bot proposes to do something; a person confirms here; there is time to
 * take it back.
 *
 * The card walks the states in services/workflow/actions.py. Proposed shows
 * Confirm and Not now. Confirmed shows a countdown and Undo -- the action
 * fires only when the window closes, so a mis-press costs nothing. Done
 * shows what happened and, for an action that can be reversed (a switch),
 * Put it back. A call placed cannot be un-rung, so a placed call offers
 * nothing: a button that pretended otherwise would be a lie.
 *
 * Every press writes into the proposal's own row, so the card everyone opens
 * later is the record: who confirmed, who took it back, what it did.
 */

import { Check, CircleHelp, CircleSlash, Loader2, MessageSquare, Phone, Undo2, Zap } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { settleActionApiV1TimelineActionsSettlePost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';

export type ActionState =
    | 'proposed'
    | 'armed'
    /** A step on the person's own computer, approved and waiting for the
     *  computer to take it once (services/workflow/desktop_steps.py). */
    | 'released'
    | 'running'
    | 'done'
    | 'failed'
    | 'undone'
    | 'cancelled'
    | 'declined'
    /** The job lost track of a send (task ledger): whether it went is not
     *  known, and it is never fired again on its own. */
    | 'outcome_unknown';

export type ActionPayload = {
    action?: string;
    label?: string;
    why?: string;
    effect?: string;
    /** The exact detail to check -- recipient, amount, item -- when the
     *  proposer wrote one (a step on the person's computer does). */
    preview?: string;
    reversible?: boolean;
    state?: ActionState;
    fires_at?: string;
    error?: string;
    done?: { at?: string; note?: string };
    /** The exact payload version Confirm approves (task ledger). Sent back
     *  with Confirm; an edited card has a new one. */
    version?: string;
    /** What a build produced (KAN-140): where Hear it and Try it go. */
    result?: { workflow_id?: number; handle?: string | null; open_url?: string | null };
};

export function actionOf(event: TimelineEvent): ActionPayload {
    return (event.payload ?? {}) as ActionPayload;
}

/** Whole seconds until the action fires; zero once it has. */
function secondsLeft(firesAt: string | undefined, now: number): number {
    if (!firesAt) return 0;
    return Math.max(0, Math.ceil((new Date(firesAt).getTime() - now) / 1000));
}

export function ActionCard({
    event,
    onSettled,
    onFired,
}: {
    event: TimelineEvent;
    /** The updated row, so the list holding this card can replace it. */
    onSettled?: (event: TimelineEvent) => void;
    /** The window closed while the card was on screen: the list should
     *  refetch, because the worker has written what happened. */
    onFired?: () => void;
}) {
    const action = actionOf(event);
    const state = action.state ?? 'proposed';
    const [saving, setSaving] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [now, setNow] = useState(() => Date.now());

    // The countdown, while armed. Ticks once a second and, when it reaches
    // zero, asks the list to refetch so the card moves to done on its own.
    useEffect(() => {
        if (state !== 'armed') return;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [state]);
    const left = state === 'armed' ? secondsLeft(action.fires_at, now) : 0;
    useEffect(() => {
        if (state !== 'armed' || left > 0) return;
        const timer = setTimeout(() => onFired?.(), 1500);
        return () => clearTimeout(timer);
    }, [state, left, onFired]);

    const settle = async (verb: 'confirm' | 'decline' | 'undo') => {
        setSaving(verb);
        setError(null);
        const result = await settleActionApiV1TimelineActionsSettlePost({
            // Confirm approves the version on screen and no other: a card
            // edited since is refused, not run on words nobody read.
            body: {
                event_id: event.id,
                verb,
                ...(verb === 'confirm' && action.version ? { version: action.version } : {}),
            },
        });
        setSaving(null);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not do that'));
            return;
        }
        if (result.data) onSettled?.(result.data);
    };

    const label = action.label ?? event.summary;
    const onComputer = action.action === 'desktop_step';

    return (
        <div
            className="rounded-lg border border-border bg-card p-4"
            role="group"
            aria-label={label}
        >
            <p className="flex items-start gap-2 text-sm font-medium">
                <Zap aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <span>{label}</span>
            </p>
            {action.why && <p className="mt-1 pl-6 text-sm text-muted-foreground">{action.why}</p>}
            {action.preview && state === 'proposed' && (
                <p className="mt-1 whitespace-pre-wrap break-words pl-6 text-sm" data-testid="action-preview">
                    {action.preview}
                </p>
            )}
            {/* What Confirm actually does. Derived from the tool, not written
                by the model, and shown while the buttons are still there --
                `reversible` used to be read only after the thing had run, to
                decide whether to offer "Put it back". */}
            {action.effect && state === 'proposed' && (
                <p className="mt-2 pl-6 text-sm font-medium text-foreground">{action.effect}</p>
            )}

            {state === 'proposed' && (
                <div className="mt-3 flex items-center gap-2 pl-6">
                    <Button size="sm" disabled={saving !== null} onClick={() => void settle('confirm')}>
                        {saving === 'confirm' ? 'Confirming…' : 'Confirm'}
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={saving !== null}
                        onClick={() => void settle('decline')}
                    >
                        Not now
                    </Button>
                </div>
            )}

            {state === 'running' && (
                <div className="mt-3 flex items-center gap-3 pl-6 text-sm">
                    <span className="flex items-center gap-1.5 text-muted-foreground" aria-live="polite">
                        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                        {/* A step on a computer that never reports is swept
                            to outcome_unknown (actions.sweep_stale_running). */}
                        {onComputer ? 'Your computer is doing this now…' : 'Doing this now…'}
                    </span>
                </div>
            )}

            {state === 'released' && (
                <p className="mt-3 flex items-center gap-1.5 pl-6 text-sm text-muted-foreground" aria-live="polite">
                    <Check aria-hidden className="h-4 w-4 text-emerald-600" />
                    Approved. Your computer will do this once.
                </p>
            )}

            {state === 'armed' && (
                <div className="mt-3 flex items-center gap-3 pl-6 text-sm">
                    <span className="flex items-center gap-1.5 text-muted-foreground" aria-live="polite">
                        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                        {left > 0 ? `Doing this in ${left}s` : 'Doing this now…'}
                    </span>
                    {left > 0 && (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={saving !== null}
                            onClick={() => void settle('undo')}
                        >
                            <Undo2 aria-hidden className="mr-1 h-3.5 w-3.5" />
                            Undo
                        </Button>
                    )}
                </div>
            )}

            {state === 'done' && (
                <div className="mt-3 flex flex-wrap items-center gap-3 pl-6 text-sm">
                    <span className="flex items-center gap-1.5">
                        <Check aria-hidden className="h-4 w-4 text-emerald-600" />
                        <span className="font-medium">{action.done?.note ?? 'Done'}</span>
                    </span>
                    {action.reversible ? (
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={saving !== null}
                            onClick={() => void settle('undo')}
                        >
                            <Undo2 aria-hidden className="mr-1 h-3.5 w-3.5" />
                            {saving === 'undo' ? 'Putting back…' : 'Put it back'}
                        </Button>
                    ) : action.result?.workflow_id ? (
                        // A built bot is not undone; it is heard. The two
                        // test verbs go straight into the tester, marked TEST.
                        <>
                            <Button size="sm" asChild>
                                <Link href={`/workflow/${action.result.workflow_id}?test=call`}>
                                    <Phone aria-hidden className="mr-1 h-3.5 w-3.5" />
                                    Hear it
                                </Link>
                            </Button>
                            <Button size="sm" variant="outline" asChild>
                                <Link href={`/workflow/${action.result.workflow_id}?test=text`}>
                                    <MessageSquare aria-hidden className="mr-1 h-3.5 w-3.5" />
                                    Try it
                                </Link>
                            </Button>
                            {action.result.handle && (
                                <span className="text-xs text-muted-foreground">@{action.result.handle}</span>
                            )}
                        </>
                    ) : (
                        <span className="text-xs text-muted-foreground">Cannot be undone</span>
                    )}
                </div>
            )}

            {state === 'failed' && (
                <p className="mt-3 flex items-center gap-1.5 pl-6 text-sm">
                    <CircleSlash aria-hidden className="h-4 w-4 text-destructive" />
                    <span className="font-medium">Could not</span>
                    {action.error && <span className="text-muted-foreground">· {action.error}</span>}
                </p>
            )}

            {state === 'outcome_unknown' && (
                <p className="mt-3 flex items-start gap-1.5 pl-6 text-sm" role="status">
                    <CircleHelp aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                    <span>
                        {action.error ??
                            'We are checking whether this was delivered. Please do not send it again.'}
                    </span>
                </p>
            )}

            {(state === 'undone' || state === 'cancelled' || state === 'declined') && (
                <p className="mt-3 flex items-center gap-1.5 pl-6 text-sm text-muted-foreground">
                    <Undo2 aria-hidden className="h-4 w-4" />
                    {state === 'undone' ? 'Put back' : state === 'cancelled' ? 'Undone before it ran' : 'Not done'}
                    {/* Why, when the card knows: a desktop step no computer
                        took in time (desktop_steps.sweep_unclaimed). */}
                    {state === 'cancelled' && action.error && <span>· {action.error}</span>}
                </p>
            )}

            {error && (
                <p role="alert" className="mt-2 pl-6 text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
