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

import { AlertTriangle, Bot, CheckCircle2, CircleSlash, Clock, FileText, Loader2, MessageSquare, Phone } from 'lucide-react';
import Link from 'next/link';
import React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { replyDraftTextApiV1TimelineDraftGet, timelineApiV1TimelineGet, translateTextApiV1TranslatePost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { ActionCard } from '@/components/workflow/ActionCard';
import { DecisionCard } from '@/components/workflow/DecisionCard';
import { EditCard } from '@/components/workflow/EditCard';
import { SecretCard } from '@/components/workflow/SecretCard';
import { detailFromResult } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { markSeen } from '@/lib/botSeen';
import { hasIndicScript } from '@/lib/indic';
import { cn } from '@/lib/utils';

/** How often to look for new rows. */
const POLL_MS = 5000;
/** While a bot is thinking, how often the forming reply is read. */
const DRAFT_POLL_MS = 700;

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

/** A reading, as one muted line under the avatar column: who, what, when. */
function ActivityRow({
    event,
    who,
    children,
}: {
    event: TimelineEvent;
    who: string;
    children?: React.ReactNode;
}) {
    return (
        <>
            <span aria-hidden className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center">
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
            </span>
            <p className="min-w-0 flex-1 self-center text-xs text-muted-foreground" data-activity>
                <span className="font-medium">{who}</span>
                <span> · {event.summary}</span>
                {children}
                <span className="ml-2">
                    <time dateTime={event.at}>{when(event.at)}</time>
                </span>
            </p>
        </>
    );
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

type TestOffer = { workflow_id: number; bot_name?: string; how?: string; brief?: string; url: string };

/** A Hear it / Try it card Decibyl offered (KAN-140), if this row carries one. */
function testOf(event: TimelineEvent): TestOffer | null {
    const test = (event.payload as { test?: unknown } | null)?.test;
    if (!test || typeof test !== 'object') return null;
    const offer = test as Partial<TestOffer>;
    if (typeof offer.workflow_id !== 'number' || typeof offer.url !== 'string') return null;
    return offer as TestOffer;
}

function sizeOf(bytes?: number): string {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export type ChannelStreamHandle = { refresh: () => void };

/** Rows that read as one line when they run together. A clinic's evening of
 *  missed calls is one fact, not nine; a bot's five tool calls on one message
 *  are one "working" line that ends on the latest step. Anything a person
 *  said, or a bot decided, is never folded. */
type Group = { key: string; events: TimelineEvent[] };

export function groupRows(inOrder: TimelineEvent[]): Group[] {
    const groups: Group[] = [];
    for (const event of inOrder) {
        const foldable =
            (event.kind === 'call_ended' && (event.payload as { answered?: boolean } | null)?.answered === false) ||
            event.kind === 'agent_acted' ||
            event.kind === 'activity';
        const key = foldable ? `${event.kind}:${event.workflow_id}` : '';
        const last = groups[groups.length - 1];
        if (foldable && last && last.key === key) {
            last.events.push(event);
        } else {
            groups.push({ key, events: [event] });
        }
    }
    return groups;
}

/** How long a bot may show as thinking before the row is taken down. A reply
 *  that has not landed in three minutes is not coming, and a spinner that
 *  never stops is a lie. */
const THINKING_FOR_MS = 3 * 60 * 1000;

export function ChannelStream({
    folderId,
    workflowId,
    assistant = false,
    assistantName = 'Decibyl',
    botNames,
    onRegisterRefresh,
    onCountChange,
    waitingFor,
}: {
    /** A channel's thread, or -- with `workflowId` instead -- one bot's own
     *  chat. Exactly one of the two. */
    folderId?: number;
    workflowId?: number;
    /** Decibyl's own thread: no channel, no bot. Rows with neither id are
     *  the assistant's, and are named for it rather than for "A bot". */
    assistant?: boolean;
    assistantName?: string;
    /** Bot id → display name, so an event can be attributed to a teammate
     *  rather than to an id. Missing names degrade to "A bot", never to a
     *  blank line. */
    botNames: Record<number, string>;
    onRegisterRefresh?: (refresh: () => void) => void;
    /** How many rows are showing. Home uses it to step its greeting aside
     *  once a conversation is under way. */
    onCountChange?: (count: number) => void;
    /** Bots asked something at this time and not yet heard from. Each shows
     *  as a thinking row until a row of theirs newer than this arrives. */
    waitingFor?: { since: string; bots: number[] } | null;
}) {
    const { user, loading: authLoading } = useAuth();
    const [events, setEvents] = useState<TimelineEvent[]>([]);
    const [cursor, setCursor] = useState<{ at: string; id: number } | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const started = useRef(false);
    const scroller = useRef<HTMLDivElement | null>(null);
    const bottom = useRef<HTMLDivElement | null>(null);
    // The newest row last scrolled to. The list is replaced on every poll;
    // only a genuinely new row moves the reader, and only inside this box.
    const newestSeen = useRef<number | null>(null);
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    // Translations shown under a row, by event id. Asked for, never automatic:
    // a clinic's Tamil is not a problem to be corrected, and the translation
    // costs a request.
    const [translated, setTranslated] = useState<Record<number, string | null>>({});
    const translateRow = async (event: TimelineEvent, text: string) => {
        setTranslated((all) => ({ ...all, [event.id]: null }));
        const response = await translateTextApiV1TranslatePost({ body: { text } });
        setTranslated((all) => ({
            ...all,
            [event.id]: response.error
                ? detailFromResult(response, 'Could not translate that')
                : (response.data?.text ?? ''),
        }));
    };
    // On a bot's own chat: when this person last opened it here, taken once
    // on arrival, then the mark moves to now. Rows newer than it sit under a
    // NEW line. Channels have no mark yet; nothing is drawn for them.
    const seenBefore = useRef<string | null>(null);
    const target = assistant
        ? { assistant: true }
        : workflowId != null
          ? { workflow_id: workflowId }
          : { folder_id: folderId };
    const fallbackName = assistant ? assistantName : 'A bot';
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
        onCountChange?.(events.length);
    }, [onCountChange, events.length]);

    useEffect(() => {
        const newest = events[0]?.id ?? null;
        if (newest === newestSeen.current) return;
        newestSeen.current = newest;
        const element = scroller.current;
        if (pinned.current && element) element.scrollTop = element.scrollHeight;
    }, [events]);

    // Bots asked and not yet heard from. A row of theirs newer than the
    // question ends it; so does the clock, because a reply that has not
    // landed in three minutes is not coming.
    const thinking =
        waitingFor && Date.now() - new Date(waitingFor.since).getTime() < THINKING_FOR_MS
            ? waitingFor.bots.filter(
                  (bot) =>
                      !events.some(
                          (e) =>
                              (assistant ? e.workflow_id == null : e.workflow_id === bot) &&
                              e.actor !== 'human' &&
                              // A reading is work on the way to the reply,
                              // not the reply.
                              e.kind !== 'activity' &&
                              e.at > waitingFor.since,
                      ),
              )
            : [];

    const activityWho = (event: TimelineEvent) =>
        (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;

    // The reply as it forms. Polled only while somebody is thinking, faster
    // than the timeline, and shown in the thinking row in place of the
    // spinner's word. Decibyl's thread has no bot; a bot's chat has one.
    const [draft, setDraft] = useState('');
    const waiting = thinking.length > 0;
    useEffect(() => {
        if (!waiting || (!assistant && workflowId == null)) {
            setDraft('');
            return;
        }
        let cancelled = false;
        const read = async () => {
            const response = await replyDraftTextApiV1TimelineDraftGet({
                query: workflowId != null ? { workflow_id: workflowId } : undefined,
            });
            if (!cancelled && !response.error) setDraft(response.data?.text ?? '');
        };
        void read();
        const timer = setInterval(() => void read(), DRAFT_POLL_MS);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [waiting, assistant, workflowId]);

    if (loading) {
        return <p className="px-6 py-8 text-sm text-muted-foreground">Loading…</p>;
    }

    // Oldest first for reading. The API answers newest-first because that is
    // the direction the cursor runs; only the display is reversed.
    const inOrder = [...events].reverse();


    return (
        <div
            ref={scroller}
            className="brand-wash min-h-0 flex-1 overflow-y-auto px-6 py-4"
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
                {groupRows(inOrder).map((group) => {
                    if (group.events.length > 1 && !expanded[group.key + group.events[0].id]) {
                        // One line for the run: the latest of them, and how
                        // many. Opening it shows every row as it was.
                        const latest = group.events[group.events.length - 1];
                        const id = group.key + group.events[0].id;
                        if (latest.kind === 'activity') {
                            // A run of readings: the latest, and how many. Muted,
                            // one line, opening to every step.
                            return (
                                <li key={`group-${id}`} className="flex gap-3">
                                    <ActivityRow event={latest} who={activityWho(latest)}>
                                        <span> · {group.events.length} steps</span>
                                        <button
                                            type="button"
                                            className="ml-2 underline-offset-2 hover:underline"
                                            onClick={() => setExpanded((all) => ({ ...all, [id]: true }))}
                                        >
                                            Show all
                                        </button>
                                    </ActivityRow>
                                </li>
                            );
                        }
                        const isCall = latest.kind === 'call_ended';
                        const who = (latest.workflow_id != null && botNames[latest.workflow_id]) || fallbackName;
                        const line = isCall
                            ? `${group.events.length} calls not answered`
                            : `${latest.summary} · ${group.events.length} steps`;
                        return (
                            <li key={`group-${id}`} className="flex gap-3">
                                <span
                                    aria-hidden
                                    className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]"
                                >
                                    {isCall ? <Phone className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                                </span>
                                <div className="min-w-0 flex-1">
                                    <p className="text-sm">
                                        <span className="font-medium">{who}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            <time dateTime={latest.at}>{when(latest.at)}</time>
                                        </span>
                                    </p>
                                    <p className="mt-0.5 text-sm">
                                        {line}
                                        <button
                                            type="button"
                                            className="ml-2 text-xs text-muted-foreground underline-offset-2 hover:underline"
                                            onClick={() => setExpanded((all) => ({ ...all, [id]: true }))}
                                        >
                                            Show all
                                        </button>
                                    </p>
                                </div>
                            </li>
                        );
                    }
                    return group.events.map((event) => renderEvent(event, inOrder.indexOf(event)));
                })}
                {thinking.map((bot) => (
                    <li key={`thinking-${bot}`} className="flex gap-3" aria-label={`${botNames[bot] || fallbackName} is thinking`}>
                        <span
                            aria-hidden
                            className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]"
                        >
                            <Bot className="h-4 w-4" />
                        </span>
                        <div className="min-w-0 flex-1">
                            <p className="text-sm">
                                <span className="font-medium">{botNames[bot] || fallbackName}</span>
                            </p>
                            {draft ? (
                                <p className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed" data-testid="forming">
                                    {draft}
                                    <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-[var(--accent-brand)] align-middle" aria-hidden />
                                </p>
                            ) : (
                                <p className="mt-0.5 flex items-center gap-1.5 text-sm text-muted-foreground">
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                                    Thinking…
                                </p>
                            )}
                        </div>
                    </li>
                ))}
            </ol>
            <div ref={bottom} />
        </div>
    );

    function renderEvent(event: TimelineEvent, index: number) {
        {
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
                        const call = (event.payload ?? {}) as { run_id?: number; answered?: boolean; test?: boolean };
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
                                                {(event.workflow_id != null && botNames[event.workflow_id]) || fallbackName}
                                            </span>
                                            {call.test && (
                                                <span className="ml-2 rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                                    Test
                                                </span>
                                            )}
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
                    if (event.kind === 'edit_proposed') {
                        // The bot's proposed change to itself, as a diff with
                        // Publish and Discard. Same frame as the question card.
                        const proposer =
                            (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;
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
                                        <span className="font-medium">{proposer}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            <time dateTime={event.at}>{when(event.at)}</time>
                                        </span>
                                    </p>
                                    <EditCard
                                        event={event}
                                        onSettled={(updated) =>
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
                    if (event.kind === 'activity') {
                        // What the bot read or checked on the way: a muted
                        // one-liner, not a bubble.
                        return (
                            <React.Fragment key={event.id}>
                            {divider}
                            <li className="flex gap-3">
                                <ActivityRow event={event} who={activityWho(event)} />
                            </li>
                            </React.Fragment>
                        );
                    }
                    if (event.kind === 'action_proposed') {
                        // The bot proposes to act; the person confirms here,
                        // with time to take it back. Same frame as the
                        // question card. When the window closes the list
                        // refetches, so the card moves to done by itself.
                        const proposer =
                            (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;
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
                                        <span className="font-medium">{proposer}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            <time dateTime={event.at}>{when(event.at)}</time>
                                        </span>
                                    </p>
                                    <ActionCard
                                        event={event}
                                        onSettled={(updated) =>
                                            setEvents((all) =>
                                                all.map((e) => (e.id === updated.id ? updated : e)),
                                            )
                                        }
                                        onFired={() => void loadLatest()}
                                    />
                                </div>
                            </li>
                            </React.Fragment>
                        );
                    }
                    if (event.kind === 'needs_secret') {
                        // The bot needs a key. A form, not a question in the
                        // chat: what is typed goes to Credentials, and the
                        // card only ever shows that it was done.
                        const asker =
                            (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;
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
                                    <SecretCard
                                        event={event}
                                        onProvided={(updated) =>
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
                    if (event.kind === 'needs_decision') {
                        // The bot's question, as a card with something to
                        // press. Attributed like any other bot row.
                        const asker =
                            (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;
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
                          fallbackName;
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
                                {hasIndicScript(fromPerson ? messageBody(event) : event.summary) && (
                                    <div className="mt-1 text-xs">
                                        {translated[event.id] === undefined ? (
                                            <button
                                                type="button"
                                                className="text-muted-foreground underline-offset-2 hover:underline"
                                                onClick={() =>
                                                    void translateRow(event, fromPerson ? messageBody(event) : event.summary)
                                                }
                                            >
                                                Translate
                                            </button>
                                        ) : translated[event.id] === null ? (
                                            <span className="text-muted-foreground">Translating…</span>
                                        ) : (
                                            <p className="whitespace-pre-wrap border-l-2 border-border pl-2 text-muted-foreground" aria-label="Translation">
                                                {translated[event.id]}
                                            </p>
                                        )}
                                    </div>
                                )}
                                {testOf(event) && (
                                    <div className="mt-2 flex flex-wrap items-center gap-2" aria-label="Test">
                                        <Button size="sm" asChild>
                                            <Link href={testOf(event)!.url}>
                                                {testOf(event)!.how === 'try' ? (
                                                    <MessageSquare aria-hidden className="mr-1 h-3.5 w-3.5" />
                                                ) : (
                                                    <Phone aria-hidden className="mr-1 h-3.5 w-3.5" />
                                                )}
                                                {testOf(event)!.how === 'try' ? 'Try it' : 'Hear it'}
                                            </Link>
                                        </Button>
                                        <span className="rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                            Test
                                        </span>
                                        {testOf(event)!.bot_name && (
                                            <span className="text-xs text-muted-foreground">{testOf(event)!.bot_name}</span>
                                        )}
                                    </div>
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
                }
    }
}

export default ChannelStream;
