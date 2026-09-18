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

import { AlertTriangle, Bot, CheckCircle2, CircleSlash, Clock, FileText, Loader2, MessageSquare, Phone, Wrench } from 'lucide-react';
import Link from 'next/link';
import React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    postMessageApiV1TimelineMessagePost,
    replyDraftTextApiV1TimelineDraftGet,
    threadChipsApiV1TimelineChipsGet,
    timelineApiV1TimelineGet,
    translateTextApiV1TranslatePost,
} from '@/client/sdk.gen';
import type { ThreadChip, TimelineEvent } from '@/client/types.gen';
import { BotAvatar } from '@/components/bot/BotAvatar';
import { BlockedCard } from '@/components/channel/BlockedCard';
import { tagTokens } from '@/components/channel/ChannelComposer';
import { emphasisTokens } from '@/components/channel/emphasis';
import { Button } from '@/components/ui/button';
import { ActionCard } from '@/components/workflow/ActionCard';
import { ConnectorCard } from '@/components/workflow/ConnectorCard';
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

/** The clock alone, for a row that follows one by the same author: the day
 *  is already on the divider above and the name on the row above. */
function clockOnly(at: string): string {
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/** The heading a day of the thread sits under. Today and yesterday by name,
 *  because that is how somebody scrolling back says it to themselves. */
export function dayLabel(at: string): string {
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return '';
    const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((midnight(new Date()) - midnight(date)) / 86_400_000);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    return date.toLocaleDateString(undefined, {
        day: 'numeric',
        month: 'short',
        ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' }),
    });
}

/** True when this row opens a new calendar day, so a date goes above it. */
export function opensDay(event: TimelineEvent, previous?: TimelineEvent): boolean {
    if (!previous) return true;
    const a = new Date(previous.at);
    const b = new Date(event.at);
    if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return false;
    return a.toDateString() !== b.toDateString();
}

/** Kinds that draw a card of their own. A card always carries its own
 *  attribution, so it never joins the run of messages above it. */
const CARDS = new Set([
    'call_ended',
    'edit_proposed',
    'activity',
    'action_proposed',
    'connector_offered',
    'needs_secret',
    'needs_decision',
]);

/** How long a pause can be and still read as one person still talking. */
const BURST_MS = 5 * 60 * 1000;

/** True when this row is the same author still talking, within a few minutes
 *  of the last one. Saying their name and the date over every line of a
 *  four-line answer is noise; the run reads as one turn without it. */
export function sameBurst(event: TimelineEvent, previous?: TimelineEvent): boolean {
    if (!previous) return false;
    if (event.blocked || previous.blocked) return false;
    if (CARDS.has(event.kind) || CARDS.has(previous.kind)) return false;
    if (event.actor !== previous.actor) return false;
    if ((event.workflow_id ?? null) !== (previous.workflow_id ?? null)) return false;
    if (opensDay(event, previous)) return false;
    const gap = new Date(event.at).getTime() - new Date(previous.at).getTime();
    return Number.isFinite(gap) && gap >= 0 && gap < BURST_MS;
}

/** A message with its `@bot` and `#channel` tags painted blue. */
/** One run of ordinary text, with the little markdown a model writes.
 *
 *  Emphasis is read inside a tag token's neighbours rather than across them,
 *  so a `@handle` keeps its colour and an asterisk beside one cannot swallow
 *  it. Every branch renders text into a React element, so a subject line
 *  relayed from somebody's inbox stays escaped however it is written. */
function Emphasised({ text }: { text: string }) {
    return (
        <>
            {emphasisTokens(text).map((token, index) => {
                if (token.emphasis === 'bold')
                    return (
                        <strong key={index} className="font-semibold">
                            {token.text}
                        </strong>
                    );
                if (token.emphasis === 'italic') return <em key={index}>{token.text}</em>;
                if (token.emphasis === 'code')
                    return (
                        <code
                            key={index}
                            className="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em]"
                        >
                            {token.text}
                        </code>
                    );
                return <span key={index}>{token.text}</span>;
            })}
        </>
    );
}

function Tagged({ text }: { text: string }) {
    return (
        <>
            {tagTokens(text).map((token, index) =>
                token.tag ? (
                    <span key={index} className="font-medium text-[var(--brand-blue)]">
                        {token.text}
                    </span>
                ) : (
                    <Emphasised key={index} text={token.text} />
                ),
            )}
        </>
    );
}

/** The words somebody typed, or a bot's whole reply. `summary` truncates at
 *  500; `payload.body` does not, and a message silently cut short is the
 *  product editing them. A bot's reply was cut mid-word on the home screen. */
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

type CheckResult = {
    result_id: number;
    workflow_id: number;
    bot_name?: string;
    handle?: string | null;
    brief?: string;
    status: string;
    passed: boolean;
    verdict?: string;
    evals_url?: string;
};

/** A Check it verdict Decibyl posted (KAN-140 P1), if this row carries one. */
function checkOf(event: TimelineEvent): CheckResult | null {
    const check = (event.payload as { check?: unknown } | null)?.check;
    if (!check || typeof check !== 'object') return null;
    const result = check as Partial<CheckResult>;
    if (typeof result.result_id !== 'number' || typeof result.status !== 'string') return null;
    return result as CheckResult;
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

/** How long a bot may show as thinking before the row stops spinning. A reply
 *  that has not landed in three minutes is not coming, and a spinner that
 *  never stops is a lie.
 *
 *  The row does not go away at that point, it changes what it says. Taking it
 *  down leaves the reader with a question they cannot answer: the bot was
 *  working, the line vanished, and nothing said whether it finished, failed
 *  or is still going. Silence is a state, and a state has to be rendered. */
const THINKING_FOR_MS = 3 * 60 * 1000;

export function ChannelStream({
    folderId,
    workflowId,
    assistant = false,
    assistantName = 'Decibyl',
    threadId = null,
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
    /** Which of Decibyl's conversations, with `assistant`. Null is the one
     *  the account has always had, so a caller that says nothing reads what
     *  it always read. */
    threadId?: string | null;
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
    // Fix it on a Result card: the verdict goes to Decibyl as a line, and
    // Decibyl proposes the edit. The card only knows it asked.
    const [fixing, setFixing] = useState<number | null>(null);
    const askToFix = async (event: TimelineEvent, check: CheckResult) => {
        const who = check.handle ? `@${check.handle}` : check.bot_name ?? 'the bot';
        const text = `Fix ${who} after the check: ${check.verdict || check.brief || 'it did not handle the caller'}`;
        setFixing(event.id);
        const response = await postMessageApiV1TimelineMessagePost({
            body: { assistant: true, thread_id: threadId, text },
        });
        setFixing(null);
        if (!response.error) void loadLatest();
    };
    // What to ask next. Read from the server rather than composed here: the
    // cards are built from this account's own life (home_openers), and a
    // suggestion the account cannot act on is worse than no suggestion.
    const [chips, setChips] = useState<ThreadChip[]>([]);
    const [sendingChip, setSendingChip] = useState<string | null>(null);
    const loadChips = useCallback(async () => {
        if (!assistant) return;
        const response = await threadChipsApiV1TimelineChipsGet();
        if (response.error) return; // A thread with no chips is still a thread.
        setChips(response.data?.chips ?? []);
    }, [assistant]);
    const sendChip = async (text: string) => {
        setSendingChip(text);
        // Cleared first: the chips answer the reply that is on screen, and
        // leaving them under the question they just asked reads as if
        // nothing happened.
        setChips([]);
        const response = await postMessageApiV1TimelineMessagePost({
            body: { assistant: true, thread_id: threadId, text },
        });
        setSendingChip(null);
        if (response.error) {
            void loadChips(); // Put them back; nothing was sent.
            return;
        }
        void loadLatest();
    };

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
        ? { assistant: true, thread_id: threadId ?? undefined }
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
        // Everything `target` is built from: a thread switch must re-read,
        // or the new chat shows the old one's rows until the next poll.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [folderId, workflowId, assistant, threadId]);

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
    }, [cursor, folderId, workflowId, assistant, threadId]);

    useEffect(() => {
        // The auth interceptor is registered only once auth has loaded, so
        // fetching earlier sends an unauthenticated request that fails
        // quietly — see ui/AGENTS.md.
        if (authLoading || !user || started.current) return;
        started.current = true;
        if (workflowId != null) seenBefore.current = markSeen(workflowId);
        void loadLatest().finally(() => setLoading(false));
        void loadChips();
    }, [authLoading, user, loadLatest, loadChips, workflowId]);


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
    const thinking = waitingFor
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
    // Past the clock the row stays but stops pretending: no spinner, and a
    // line that says the wait is over and the reply never came.
    const gaveUp =
        !!waitingFor &&
        Date.now() - new Date(waitingFor.since).getTime() >= THINKING_FOR_MS;

    const activityWho = (event: TimelineEvent) =>
        (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;

    // The reply as it forms. Polled only while somebody is thinking, faster
    // than the timeline, and shown in the thinking row in place of the
    // spinner's word. Decibyl's thread has no bot; a bot's chat has one.
    const [draft, setDraft] = useState('');
    const waiting = thinking.length > 0 && !gaveUp;
    // Refreshed when a reply lands, so the chips answer what was just said
    // rather than what was said when the screen opened.
    const wasWaiting = useRef(false);
    useEffect(() => {
        if (wasWaiting.current && !waiting) void loadChips();
        wasWaiting.current = waiting;
    }, [waiting, loadChips]);
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
            // Plain paper. The thread used to sit on a drifting violet wash;
            // a room you read all day is the one surface in the product that
            // must not have atmosphere of its own, and the messages have
            // colour enough in their faces.
            className="min-h-0 flex-1 overflow-y-auto bg-background px-4 py-4 sm:px-6"
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
                        const opener = group.events[0];
                        const before = inOrder[inOrder.indexOf(opener) - 1];
                        if (latest.kind === 'activity') {
                            // A run of readings: the latest, and how many. Muted,
                            // one line, opening to every step.
                            return (
                                <React.Fragment key={`group-${id}`}>
                                {dividers(opener, before)}
                                <li className="flex gap-3">
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
                                </React.Fragment>
                            );
                        }
                        const isCall = latest.kind === 'call_ended';
                        const who = (latest.workflow_id != null && botNames[latest.workflow_id]) || fallbackName;
                        const line = isCall
                            ? `${group.events.length} calls not answered`
                            : `${latest.summary} · ${group.events.length} steps`;
                        return (
                            <React.Fragment key={`group-${id}`}>
                            {dividers(opener, before)}
                            <li className="flex gap-3">
                                {isCall ? (
                                    <span
                                        aria-hidden
                                        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]"
                                    >
                                        <Phone className="h-4 w-4" />
                                    </span>
                                ) : (
                                    face(latest)
                                )}
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
                            </React.Fragment>
                        );
                    }
                    return group.events.map((event) => renderEvent(event, inOrder.indexOf(event)));
                })}
                {thinking.map((bot) => (
                    <li
                        key={`thinking-${bot}`}
                        className="flex gap-3"
                        aria-label={
                            gaveUp
                                ? `${botNames[bot] || fallbackName} has not replied`
                                : `${botNames[bot] || fallbackName} is thinking`
                        }
                    >
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
                            {gaveUp ? (
                                <p className="mt-0.5 flex items-center gap-1.5 text-sm text-muted-foreground">
                                    <CircleSlash className="h-3.5 w-3.5 shrink-0" aria-hidden />
                                    No reply yet. Ask again, or check History for what it did.
                                </p>
                            ) : draft ? (
                                <p className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed" data-testid="forming">
                                    {/* Emphasised here too, so the reply does
                                        not visibly re-set when it finalises. A
                                        half-streamed `**Wedn` has no closing
                                        marker and reads as the text it is
                                        until the rest arrives. */}
                                    <Emphasised text={draft} />
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
            {/* What to ask next, so the thread carries its own next steps.
                Hidden while a bot is thinking: offering a follow-up to an
                answer that has not arrived is asking somebody to interrupt.
                Hidden on a bot's own chat too -- these are the workspace's
                questions, and Decibyl is who answers them. */}
            {assistant && !waiting && chips.length > 0 && (
                <ul
                    className="mt-3 flex flex-wrap gap-2"
                    aria-label="Suggested next steps"
                    data-testid="thread-chips"
                >
                    {chips.map((chip) => (
                        <li key={`${chip.kind}-${chip.text}`}>
                            <button
                                type="button"
                                disabled={sendingChip !== null}
                                onClick={() => void sendChip(chip.text)}
                                className="rounded-full border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-[var(--accent-brand)] hover:text-foreground disabled:opacity-50"
                            >
                                {chip.text}
                            </button>
                        </li>
                    ))}
                </ul>
            )}
            <div ref={bottom} />
        </div>
    );

    /** The date above a new day, and the line above the first row somebody
     *  has not seen. Both belong to the row below them, so they are drawn
     *  wherever that row is drawn -- folded run or single message alike. */
    function dividers(event: TimelineEvent, previous?: TimelineEvent) {
        return (
            <>
                {opensDay(event, previous) && (
                    <li
                        key={`day-${event.id}`}
                        aria-label={dayLabel(event.at)}
                        className="flex items-center gap-2 pt-1"
                    >
                        <span className="h-px flex-1 bg-border" />
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                            {dayLabel(event.at)}
                        </span>
                        <span className="h-px flex-1 bg-border" />
                    </li>
                )}
                {seenBefore.current &&
                    event.at > seenBefore.current &&
                    (!previous || !(previous.at > seenBefore.current)) && (
                        <li key={`new-${event.id}`} aria-label="New" className="flex items-center gap-2">
                            <span className="h-px flex-1 bg-[var(--accent-brand)]/40" />
                            <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent-brand)]">
                                New
                            </span>
                            <span className="h-px flex-1 bg-[var(--accent-brand)]/40" />
                        </li>
                    )}
            </>
        );
    }

    /** A bot's own face in the gutter, the one the rail and the bots list
     *  draw. Every row used to wear the same brand square, so a thread with
     *  three bots in it looked like one voice. */
    function face(event: TimelineEvent) {
        const name = (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;
        return (
            <BotAvatar
                id={event.workflow_id ?? name}
                name={name}
                size="sm"
                className="mt-0.5 h-7 w-7 rounded-md"
            />
        );
    }

    function renderEvent(event: TimelineEvent, index: number) {
        {
                    // Oldest first, so the NEW line goes above the first row
                    // newer than the previous visit.
                    const previous = inOrder[index - 1];
                    const divider = dividers(event, previous);
                    const burst = sameBurst(event, previous);
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
                                {face(event)}
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
                                {face(event)}
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
                    if (event.kind === 'connector_offered') {
                        // Decibyl offered an app. The card is the whole
                        // answer: what it is, what it brings, and the
                        // button that starts the sign-in, in the thread
                        // that needed it.
                        const asker =
                            (event.workflow_id != null && botNames[event.workflow_id]) || fallbackName;
                        return (
                            <React.Fragment key={event.id}>
                            {divider}
                            <li className="flex gap-3">
                                {face(event)}
                                <div className="min-w-0 flex-1">
                                    <p className="mb-1 text-sm">
                                        <span className="font-medium">{asker}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            <time dateTime={event.at}>{when(event.at)}</time>
                                        </span>
                                    </p>
                                    <ConnectorCard event={event} />
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
                                {face(event)}
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
                    // A wall the server could name. Rendered before the
                    // generic row below, which would otherwise show the
                    // sentence and drop the ways past it on the floor.
                    if (event.blocked) {
                        const tone = TONE[event.kind];
                        return (
                            <React.Fragment key={event.id}>
                            {divider}
                            <li className="flex gap-3">
                                <span
                                    aria-hidden
                                    className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-amber-500/10"
                                >
                                    <AlertTriangle className="h-4 w-4 text-amber-600" />
                                </span>
                                <div className="min-w-0 flex-1">
                                    <p className="mb-1 text-xs text-muted-foreground">
                                        <time dateTime={event.at}>{when(event.at)}</time>
                                    </p>
                                    <BlockedCard
                                        wall={event.blocked}
                                        summary={event.summary}
                                        icon={tone?.icon}
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
                                {face(event)}
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
                        {/* A row that carries on from the one above keeps the
                            gutter and drops the heading: the same name and
                            the same date over every line of one answer is
                            noise. The clock stays, in the gutter, on hover. */}
                        <li className="group flex gap-3">
                            {burst ? (
                                <span className="w-7 shrink-0 text-right text-[10px] leading-7 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
                                    <time dateTime={event.at}>{clockOnly(event.at)}</time>
                                </span>
                            ) : !fromPerson && !tone ? (
                                face(event)
                            ) : (
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
                            )}
                            <div className="min-w-0 flex-1">
                                {!burst && (
                                    <p className="text-sm">
                                        <span className="font-medium">{author}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            <time dateTime={event.at}>{when(event.at)}</time>
                                            {event.is_deliverable ? ' · handed to you' : ''}
                                        </span>
                                    </p>
                                )}
                                {/* whitespace-pre-wrap: somebody who typed a
                                    list wrote the line breaks on purpose. */}
                                {messageBody(event) && (
                                    <p className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed">
                                        <Tagged text={messageBody(event)} />
                                    </p>
                                )}
                                {hasIndicScript(messageBody(event)) && (
                                    <div className="mt-1 text-xs">
                                        {translated[event.id] === undefined ? (
                                            <button
                                                type="button"
                                                className="text-muted-foreground underline-offset-2 hover:underline"
                                                onClick={() =>
                                                    void translateRow(event, messageBody(event))
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
                                {checkOf(event) && (
                                    <div
                                        className={cn(
                                            'mt-2 rounded-md border p-3 text-sm',
                                            checkOf(event)!.passed
                                                ? 'border-emerald-300/60 bg-emerald-50/60'
                                                : 'border-amber-300/60 bg-amber-50/60',
                                        )}
                                        aria-label="Check result"
                                    >
                                        <p className="flex items-center gap-1.5 font-medium">
                                            {checkOf(event)!.passed ? (
                                                <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />
                                            ) : (
                                                <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden />
                                            )}
                                            {checkOf(event)!.passed ? 'Handled' : checkOf(event)!.status === 'failed' ? 'Not handled' : 'Could not check'}
                                            <span className="ml-1 rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                                Test
                                            </span>
                                        </p>
                                        {checkOf(event)!.verdict && (
                                            <p className="mt-1 text-muted-foreground">{checkOf(event)!.verdict}</p>
                                        )}
                                        <div className="mt-2 flex flex-wrap items-center gap-2">
                                            {!checkOf(event)!.passed && checkOf(event)!.status !== 'error' && (
                                                <Button
                                                    size="sm"
                                                    disabled={fixing === event.id}
                                                    onClick={() => void askToFix(event, checkOf(event)!)}
                                                >
                                                    <Wrench className="mr-1 h-3.5 w-3.5" aria-hidden />
                                                    {fixing === event.id ? 'Asking…' : 'Fix it'}
                                                </Button>
                                            )}
                                            {checkOf(event)!.evals_url && (
                                                <Button size="sm" variant="outline" asChild>
                                                    <Link href={checkOf(event)!.evals_url!}>Transcript</Link>
                                                </Button>
                                            )}
                                        </div>
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
