'use client';

/**
 * What this bot has been doing, as a thread.
 *
 * The rows have been written since `services/workflow/agent_timeline.py`
 * shipped and read by nothing — a write-only table, which is the silent
 * absence `api/AGENTS.md` is about, sitting in the one place a customer goes
 * to ask "what did it do". Every screen said "no activity" and meant "nobody
 * built the screen".
 *
 * Deliberately a thread rather than a table. A table of kinds and timestamps
 * is a log; the product sells a teammate, and you read what a teammate did as
 * a sequence of sentences. The sentence is written at the moment it happens
 * and stored, so this renders it rather than assembling one — see that
 * module's docstring for why that matters in a dispute.
 */

import { AlertTriangle, CheckCircle2, CircleSlash, Clock, FileText } from 'lucide-react';
import React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { timelineApiV1TimelineGet } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { DecisionCard } from '@/components/workflow/DecisionCard';
import { detailFromResult } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { markSeen } from '@/lib/botSeen';
import { cn } from '@/lib/utils';

/**
 * How each kind reads. A blocklist would be wrong here for once: an unknown
 * kind must still render, so the fallback is the default and this map only
 * adds emphasis to the four that carry weight.
 */
const TONE: Record<string, { icon: typeof FileText; className: string }> = {
    outcome_filed: { icon: CheckCircle2, className: 'text-emerald-600' },
    deliverable: { icon: FileText, className: 'text-emerald-600' },
    could_not: { icon: CircleSlash, className: 'text-amber-600' },
    needs_attention: { icon: AlertTriangle, className: 'text-amber-600' },
};

function when(at: string): string {
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString(undefined, {
        day: 'numeric',
        month: 'short',
        hour: 'numeric',
        minute: '2-digit',
    });
}

export function BotThread({ workflowId }: { workflowId: number }) {
    const { user, loading: authLoading } = useAuth();
    const [events, setEvents] = useState<TimelineEvent[]>([]);
    const [cursor, setCursor] = useState<{ at: string; id: number } | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const started = useRef(false);
    // When this person last opened this bot here, taken once on arrival and
    // then the mark is moved to now. Rows newer than it sit above a NEW line.
    const seenBefore = useRef<string | null>(null);

    const load = useCallback(
        async (after: { at: string; id: number } | null) => {
            const response = await timelineApiV1TimelineGet({
                query: {
                    workflow_id: workflowId,
                    limit: 50,
                    // Both halves or neither: the rows are ordered by (at, id)
                    // and a cursor narrower than the sort drops rows off every
                    // later page. See api/db/agent_event_client.py.
                    ...(after ? { before_at: after.at, before_id: after.id } : {}),
                },
            });
            if (response.error) {
                setError(detailFromResult(response, 'Could not load this history'));
                return;
            }
            const page = response.data?.events ?? [];
            setEvents((existing) => [...existing, ...page]);
            const nextAt = response.data?.next_before_at ?? null;
            const nextId = response.data?.next_before_id ?? null;
            setCursor(nextAt && nextId ? { at: nextAt, id: nextId } : null);
        },
        [workflowId],
    );

    useEffect(() => {
        // The auth interceptor is only registered once auth has loaded, so
        // fetching earlier sends an unauthenticated request that fails
        // quietly — see ui/AGENTS.md.
        if (authLoading || !user || started.current) return;
        started.current = true;
        seenBefore.current = markSeen(workflowId);
        load(null).finally(() => setLoading(false));
    }, [authLoading, user, load, workflowId]);

    if (loading) {
        return <p className="px-1 py-8 text-sm text-muted-foreground">Loading…</p>;
    }

    if (error) {
        return (
            <p className="px-1 py-8 text-sm text-destructive" role="alert">
                {error}
            </p>
        );
    }

    if (events.length === 0) {
        return (
            <div className="px-1 py-10">
                <p className="text-sm font-medium">Nothing yet.</p>
                <p className="mt-1 text-sm text-muted-foreground">
                    When this bot answers a call, files an outcome or cannot do
                    something, it appears here.
                </p>
            </div>
        );
    }

    return (
        <div className="flex flex-col">
            <ol className="flex flex-col gap-4 py-2">
                {events.map((event, index) => {
                    // Newest first, so the NEW line goes under the last row
                    // that is newer than the previous visit -- between what
                    // has happened since and what was already read.
                    const isNew = !!seenBefore.current && event.at > seenBefore.current;
                    const next = events[index + 1];
                    const dividerAfter =
                        isNew && (!next || !(next.at > (seenBefore.current ?? '')));
                    const divider = dividerAfter ? (
                        <li key={`new-${event.id}`} aria-label="New" className="flex items-center gap-2">
                            <span className="h-px flex-1 bg-[var(--accent-brand)]/40" />
                            <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent-brand)]">
                                New
                            </span>
                            <span className="h-px flex-1 bg-[var(--accent-brand)]/40" />
                        </li>
                    ) : null;
                    if (event.kind === 'needs_decision') {
                        return (
                            <React.Fragment key={event.id}>
                                <li>
                                    <DecisionCard
                                        event={event}
                                        onDecided={(updated) =>
                                            setEvents((all) => all.map((e) => (e.id === updated.id ? updated : e)))
                                        }
                                    />
                                </li>
                                {divider}
                            </React.Fragment>
                        );
                    }
                    const tone = TONE[event.kind];
                    const Icon = tone?.icon ?? Clock;
                    return (
                        <React.Fragment key={event.id}>
                            <li className="flex gap-3">
                                <Icon
                                    aria-hidden
                                    className={cn(
                                        'mt-0.5 h-4 w-4 shrink-0',
                                        tone?.className ?? 'text-muted-foreground',
                                    )}
                                />
                                <div className="min-w-0 flex-1">
                                    <p className="text-sm leading-relaxed">{event.summary}</p>
                                    <p className="mt-0.5 text-xs text-muted-foreground">
                                        <time dateTime={event.at}>{when(event.at)}</time>
                                        {event.is_deliverable ? ' · handed to you' : ''}
                                    </p>
                                </div>
                            </li>
                            {divider}
                        </React.Fragment>
                    );
                })}
            </ol>

            {cursor && (
                <Button
                    variant="outline"
                    size="sm"
                    className="mt-4 self-start"
                    onClick={() => load(cursor)}
                >
                    Show earlier
                </Button>
            )}
        </div>
    );
}
