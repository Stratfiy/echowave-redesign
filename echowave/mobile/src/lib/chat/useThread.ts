/**
 * One Decibyl thread, kept current.
 *
 * There is no stream for chat on the server: the web polls, and so does the
 * app, with the same cadence (ui/src/components/channel/ChannelStream.tsx):
 * the thread every 5 s, and the reply as it forms (GET /timeline/draft)
 * every 0.7 s while a reply is awaited. A reply has landed when a row not
 * from the person, and not an activity stage, appears after the send; the
 * wait gives up after 3 minutes and the turn status says what is known.
 *
 * Thread id "main" is the account's original thread (no `thread_id` on the
 * wire).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    postMessageApiV1TimelineMessagePost,
    replyDraftTextApiV1TimelineDraftGet,
    stopReplyApiV1ShellChatStopPost,
    threadChipsApiV1TimelineChipsGet,
    timelineApiV1TimelineGet,
} from '@/client/sdk.gen';
import type { ThreadChip } from '@/client/types.gen';
import { call } from '@/lib/api';
import { setVisibleThread } from '@/lib/pushDevice';

import { mergeRows, replyLandedSince, type Attachment, type Row } from './events';

export const POLL_MS = 5_000;
export const DRAFT_MS = 700;
export const GIVE_UP_MS = 180_000;
export const PAGE = 50;

export function wireThreadId(threadId: string): string | undefined {
    return threadId === 'main' ? undefined : threadId;
}

export function useThread(threadId: string) {
    const [rows, setRows] = useState<Row[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [waitingSince, setWaitingSince] = useState<string | null>(null);
    const [draft, setDraft] = useState('');
    const [chips, setChips] = useState<ThreadChip[]>([]);
    const [older, setOlder] = useState<{ at: string; id: number } | null>(null);
    const rowsRef = useRef<Row[]>([]);
    const wire = wireThreadId(threadId);

    const load = useCallback(async () => {
        try {
            const page = await call(
                timelineApiV1TimelineGet({ query: { assistant: true, thread_id: wire ?? null, limit: PAGE } }),
            );
            const merged = mergeRows(rowsRef.current, page.events as Row[]);
            rowsRef.current = merged;
            setRows(merged);
            if (rowsRef.current.length <= page.events.length && page.next_before_at && page.next_before_id) {
                setOlder({ at: page.next_before_at, id: page.next_before_id });
            }
            setError(null);
            return merged;
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
            return rowsRef.current;
        }
    }, [wire]);

    const loadOlder = useCallback(async () => {
        if (!older) return;
        const page = await call(
            timelineApiV1TimelineGet({
                query: { assistant: true, thread_id: wire ?? null, limit: PAGE, before_at: older.at, before_id: older.id },
            }),
        );
        const merged = mergeRows(rowsRef.current, page.events as Row[]);
        rowsRef.current = merged;
        setRows(merged);
        setOlder(page.next_before_at && page.next_before_id ? { at: page.next_before_at, id: page.next_before_id } : null);
    }, [older, wire]);

    const loadChips = useCallback(async () => {
        try {
            setChips((await call(threadChipsApiV1TimelineChipsGet())).chips);
        } catch {
            setChips([]);
        }
    }, []);

    // The thread, every 5 s while open; this thread's pushes stay quiet.
    useEffect(() => {
        rowsRef.current = [];
        void Promise.resolve().then(() => {
            setRows(null);
            void load();
            void loadChips();
        });
        setVisibleThread(wire ?? null);
        const timer = setInterval(() => void load(), POLL_MS);
        return () => {
            clearInterval(timer);
            setVisibleThread(null);
        };
    }, [load, loadChips, wire]);

    // While waiting: the forming reply, and the end of the wait.
    useEffect(() => {
        if (!waitingSince) return;
        const started = Date.now();
        const timer = setInterval(async () => {
            try {
                const d = await call(replyDraftTextApiV1TimelineDraftGet({ query: { thread_id: wire ?? null } }));
                setDraft(d.text || '');
            } catch {
                // The draft is a nicety; the thread poll still lands the reply.
            }
            const current = await load();
            if (replyLandedSince(current, waitingSince) || Date.now() - started > GIVE_UP_MS) {
                setWaitingSince(null);
                setDraft('');
                void loadChips();
            }
        }, DRAFT_MS);
        return () => clearInterval(timer);
    }, [waitingSince, wire, load, loadChips]);

    const send = useCallback(
        async (text: string, attachments: Attachment[] = []) => {
            const since = new Date(Date.now() - 1000).toISOString();
            setChips([]);
            await call(
                postMessageApiV1TimelineMessagePost({
                    body: { assistant: true, thread_id: wire ?? null, text, attachments },
                }),
            );
            setWaitingSince(since);
            await load();
        },
        [wire, load],
    );

    const stop = useCallback(async () => {
        const result = await call(stopReplyApiV1ShellChatStopPost({ body: { thread_id: wire ?? null } }));
        return Boolean(result.requested);
    }, [wire]);

    return {
        rows,
        error,
        waiting: waitingSince !== null,
        draft,
        chips,
        hasOlder: older !== null,
        load,
        loadOlder,
        send,
        stop,
        replace(row: Row) {
            const merged = mergeRows(rowsRef.current, [row]);
            rowsRef.current = merged;
            setRows(merged);
        },
    };
}
