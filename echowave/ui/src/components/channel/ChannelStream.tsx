'use client';

/**
 * A channel, read as a conversation.
 *
 * The rows are `agent_events` — the same rows `BotThread` renders for one bot,
 * filtered to a channel instead. That is the whole reason messages were stored
 * as events rather than in a table of their own: a person asking for something
 * and a bot filing an outcome are two things that happened in the same place,
 * and interleaving two logs in a screen is not the same as having one thread.
 *
 * **Newest at the bottom, unlike the bot thread.** A bot's history is
 * something you scan — most recent first, like a log. A channel is something
 * you are *in*: you read down to the bottom and type. The API returns newest
 * first because that is what a cursor over `(at, id) DESC` gives, so this
 * reverses for display and keeps paging in the direction the cursor runs.
 *
 * It polls. A channel where a colleague's message appears only after a manual
 * refresh is a channel nobody trusts, and the bot's reply arrives from a
 * worker seconds after the message that asked for it — so "your own message
 * appears instantly and the answer never does" is the exact failure the
 * enqueue was designed to avoid, reintroduced at the last step. Polling rather
 * than a socket because there is no socket in this product yet and one message
 * every few seconds does not justify building one.
 */

import { AlertTriangle, Bot, CheckCircle2, CircleSlash, Clock, FileText, Phone } from 'lucide-react';
import Link from 'next/link';
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

/** How often to look for new rows. */
const POLL_MS = 5000;

const PAGE = 50;

/**
 * How each kind reads. The fallback is the default and this map only adds
 * emphasis to the kinds that carry weight: an unknown kind written by a newer
 * deploy must still render as itself rather than vanish.
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

/** The words somebody typed, whole. `summary` truncates at 500; `payload.body`
 *  does not, and a message silently cut short is the product editing them. */
function messageBody(event: TimelineEvent): string {
    const body = (event.payload as { body?: unknown } | null)?.body;
    return typeof body === 'string' && body ? body : event.summary;
}

type Attached = { document_uuid: string; filename: string; size_bytes?: number };

/** The files a message carried, if any. */
function attachmentsOf(event: TimelineEvent): Attached[] {
    const list = (event.payload as { attachments?: unknown } | null)?.attachments;
    if (!Array.isArray(list)) return [];
    return list.filter(
        (a): a is Attached =>
            !!a && typeof a === 'object' && typeof (a as Attached).filename === 'string',
    );
}

function sizeOf(bytes?: number): string {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type ChannelStreamHandle = { refresh: () => void };

export function ChannelStream({
    folderId,
    workflowId,
    botNames,
    onRegisterRefresh,
}: {
    /** A channel's thread, or -- with `workflowId` instead -- one bot's own
     *  chat. Exactly one of the two. */
    folderId?: number;
    workflowId?: number;
    /** Bot id → display name, so an event can be attributed to a teammate
     *  rather than to an id. Missing names degrade to "A bot", never to a
     *  blank line. */
    botNames: Record<number, string>;
    onRegisterRefresh?: (refresh: () => void) => void;
}) {
    const { user, loading: authLoading } = useAuth();
    const [events, setEvents] = useState<TimelineEvent[]>([]);
    const [cursor, setCursor] = useState<{ at: string; id: number } | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const started = useRef(false);
    const bottom = useRef<HTMLDivElement | null>(null);
    // On a bot's own chat: when this person last opened it here, taken once
    // on arrival, then the mark moves to now. Rows newer than it sit under a
    // NEW line. Channels have no mark yet; nothing is drawn for them.
    const seenBefore = useRef<string | null>(null);
    const target = workflowId != null ? { workflow_id: workflowId } : { folder_id: folderId };
    // Whether the reader is at the bottom. Scrolling them back down while they
    // are reading something further up is worse than a missed new message.
    const pinned = useRef(true);

    /** The newest page, replacing what is held. Used on load and on every
     *  poll: the channel is short-lived reading, so re-reading fifty rows is
     *  cheaper than reconciling a diff. */
    const loadLatest = useCallback(async () => {
        const response = await timelineApiV1TimelineGet({
            query: { ...target, limit: PAGE },
        });
        if (response.error) {
            setError(detailFromResult(response, 'Could not load this channel'));
            return;
        }
        setError(null);
        setEvents(response.data?.events ?? []);
        const at = response.data?.next_before_at ?? null;
        const id = response.data?.next_before_id ?? null;
        setCursor(at && id ? { at, id } : null);
    }, [folderId]);

    /** An older page, appended. Both cursor halves or neither — the rows are
     *  ordered by (at, id) and a cursor narrower than the sort drops rows off
     *  every later page. See api/db/agent_event_client.py. */
    const loadEarlier = useCallback(async () => {
        if (!cursor) return;
        const response = await timelineApiV1TimelineGet({
            query: {
                ...target,
                limit: PAGE,
                before_at: cursor.at,
                before_id: cursor.id,
            },
        });
        if (response.error) {
            setError(detailFromResult(response, 'Could not load earlier messages'));
            return;
        }
        setEvents((existing) => [...existing, ...(response.data?.events ?? [])]);
        const at = response.data?.next_before_at ?? null;
        const id = response.data?.next_before_id ?? null;
        setCursor(at && id ? { at, id } : null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [cursor, folderId, workflowId]);

    useEffect(() => {
        // The auth interceptor is registered only once auth has loaded, so
        // fetching earlier sends an unauthenticated request that fails
        // quietly — see ui/AGENTS.md.
        if (authLoading || !user || started.current) return;
        started.current = true;
        if (workflowId != null) seenBefore.current = markSeen(workflowId);
        void loadLatest().finally(() => setLoading(false));
    }, [authLoading, user, loadLatest, workflowId]);

    useEffect(() => {
        if (authLoading || !user) return;
        const timer = setInterval(() => void loadLatest(), POLL_MS);
        return () => clearInterval(timer);
    }, [authLoading, user, loadLatest]);

    useEffect(() => {
        onRegisterRefresh?.(() => void loadLatest());
    }, [onRegisterRefresh, loadLatest]);

    useEffect(() => {
        if (pinned.current) bottom.current?.scrollIntoView({ block: 'end' });
    }, [events]);

    if (loading) {
        return <p className="px-6 py-8 text-sm text-muted-foreground">Loading…</p>;
    }

    // Oldest first for reading. The API answers newest-first because that is
    // the direction the cursor runs; only the display is reversed.
    const inOrder = [...events].reverse();

    return (
        <div
            className="flex-1 overflow-y-auto px-6 py-4"
            onScroll={(scroll) => {
                const element = scroll.currentTarget;
                pinned.current =
                    element.scrollHeight - element.scrollTop - element.clientHeight < 80;
            }}
        >
            {error && (
                <p className="mb-3 text-sm text-destructive" role="alert">
                    {error}
                </p>
            )}

            {cursor && (
                <Button
                    variant="outline"
                    size="sm"
                    className="mb-4"
                    onClick={() => void loadEarlier()}
                >
                    Show earlier
                </Button>
            )}

            {inOrder.length === 0 && !error && (
                <div className="py-10">
                    <p className="text-sm font-medium">
                        {workflowId != null ? 'Nothing yet.' : 'This channel is quiet.'}
                    </p>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Say something, or address a bot by its handle to ask it
                        for something.
                    </p>
                </div>
            )}

            <ol className="flex flex-col gap-4">
                {inOrder.map((event, index) => {
                    // Oldest first, so the NEW line goes above the first row
                    // newer than the previous visit.
                    const previous = inOrder[index - 1];
                    const divider =
                        seenBefore.current &&
                        event.at > seenBefore.current &&
                        (!previous || !(previous.at > seenBefore.current)) ? (
                            <li key={`new-${event.id}`} aria-label="New" className="flex items-center gap-2">
                                <span className="h-px flex-1 bg-[var(--accent-brand)]/40" />
                                <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent-brand)]">
                                    New
                                </span>
                                <span className="h-px flex-1 bg-[var(--accent-brand)]/40" />
                            </li>
                        ) : null;
                    if (event.kind === 'call_ended') {
                        // A call is a row with a door: the length and how it
                        // ended here, the transcript and recording behind it.
                        const call = (event.payload ?? {}) as { run_id?: number; answered?: boolean };
                        const href =
                            event.workflow_id != null && (call.run_id ?? event.workflow_run_id) != null
                                ? `/workflow/${event.workflow_id}/run/${call.run_id ?? event.workflow_run_id}`
                                : null;
                        return (
                            <React.Fragment key={event.id}>
                                {divider}
                                <li className="flex gap-3">
                                    <span
                                        aria-hidden
                                        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]"
                                    >
                                        <Phone className="h-4 w-4" />
                                    </span>
                                    <div className="min-w-0 flex-1">
                                        <p className="text-sm">
                                            <span className="font-medium">
                                                {(event.workflow_id != null && botNames[event.workflow_id]) || 'A bot'}
                                            </span>
                                            <span className="ml-2 text-xs text-muted-foreground">
                                                <time dateTime={event.at}>{when(event.at)}</time>
                                            </span>
                                        </p>
                                        <p className="mt-0.5 text-sm">
                                            {href ? (
                                                <Link href={href} className="underline-offset-2 hover:underline">
                                                    {event.summary}
                                                </Link>
                                            ) : (
                                                event.summary
                                            )}
                                        </p>
                                    </div>
                                </li>
                            </React.Fragment>
                        );
                    }
                    if (event.kind === 'needs_decision') {
                        // The bot's question, as a card with something to
                        // press. Attributed like any other bot row.
                        const asker =
                            (event.workflow_id != null && botNames[event.workflow_id]) || 'A bot';
                        return (
                            <React.Fragment key={event.id}>
                            {divider}
                            <li className="flex gap-3">
                                <span
                                    aria-hidden
                                    className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]"
                                >
                                    <Bot className="h-4 w-4" />
                                </span>
                                <div className="min-w-0 flex-1">
                                    <p className="mb-1 text-sm">
                                        <span className="font-medium">{asker}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            <time dateTime={event.at}>{when(event.at)}</time>
                                        </span>
                                    </p>
                                    <DecisionCard
                                        event={event}
                                        onDecided={(updated) =>
                                            setEvents((all) =>
                                                all.map((e) => (e.id === updated.id ? updated : e)),
                                            )
                                        }
                                    />
                                </div>
                            </li>
                            </React.Fragment>
                        );
                    }
                    const fromPerson = event.actor === 'human';
                    const tone = TONE[event.kind];
                    const Icon = tone?.icon ?? (fromPerson ? Clock : Bot);
                    const author = fromPerson
                        ? 'You'
                        : (event.workflow_id != null && botNames[event.workflow_id]) ||
                          'A bot';
                    return (
                        <React.Fragment key={event.id}>
                        {divider}
                        <li className="flex gap-3">
                            <span
                                aria-hidden
                                className={cn(
                                    'mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md',
                                    fromPerson
                                        ? 'bg-accent text-foreground'
                                        : 'bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]',
                                )}
                            >
                                <Icon className={cn('h-4 w-4', tone?.className)} />
                            </span>
                            <div className="min-w-0 flex-1">
                                <p className="text-sm">
                                    <span className="font-medium">{author}</span>
                                    <span className="ml-2 text-xs text-muted-foreground">
                                        <time dateTime={event.at}>{when(event.at)}</time>
                                        {event.is_deliverable ? ' · handed to you' : ''}
                                    </span>
                                </p>
                                {/* whitespace-pre-wrap: somebody who typed a
                                    list wrote the line breaks on purpose. */}
                                {(fromPerson ? messageBody(event) : event.summary) && (
                                    <p className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed">
                                        {fromPerson ? messageBody(event) : event.summary}
                                    </p>
                                )}
                                {attachmentsOf(event).length > 0 && (
                                    <ul className="mt-1.5 flex flex-wrap gap-2" aria-label="Files">
                                        {attachmentsOf(event).map((file) => (
                                            <li
                                                key={file.document_uuid}
                                                className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm"
                                            >
                                                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                                                <span className="max-w-[16rem] truncate">{file.filename}</span>
                                                {sizeOf(file.size_bytes) && (
                                                    <span className="text-xs text-muted-foreground">
                                                        {sizeOf(file.size_bytes)}
                                                    </span>
                                                )}
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        </li>
                        </React.Fragment>
                    );
                })}
            </ol>
            <div ref={bottom} />
        </div>
    );
}

export default ChannelStream;
