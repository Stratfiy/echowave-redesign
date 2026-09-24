"use client";

/**
 * New chat, and the older ones, under the composer.
 *
 * Decibyl had exactly one conversation per account, for ever. Anything
 * you asked landed in the same scroll as everything you had ever asked,
 * and there was no way to start clean or to find last week's thread about
 * the Zoho bot without scrolling past this week's.
 *
 * A new chat is a fresh id minted here, not a row the server makes. It
 * exists the moment the first message is sent, so pressing New and
 * changing your mind leaves nothing behind -- and it does not appear in
 * this list until it has something in it, for the same reason.
 *
 * The original conversation is in the list like any other, as "the first
 * chat": it is the only one every account already has, and a list of
 * older chats that could not show it would hide everybody's history.
 */

import { MessageSquarePlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { threadsApiV1TimelineThreadsGet } from "@/client/sdk.gen";
import type { ThreadSummary } from "@/client/types.gen";

/** Roughly when, in the words a chat list uses. */
export function ago(iso: string, now = new Date()): string {
    const then = new Date(iso).getTime();
    const minutes = Math.max(0, Math.round((now.getTime() - then) / 60_000));
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** What a chat is called in the list. */
export function titleOf(thread: ThreadSummary): string {
    const title = (thread.title || "").trim();
    if (title) return title.length > 60 ? `${title.slice(0, 57)}…` : title;
    return thread.thread_id === null ? "The first chat" : "Untitled chat";
}

export function ThreadList({
    current,
    onPick,
    onNew,
    refreshKey = 0,
}: {
    /** The chat on screen. Null is the original one. */
    current: string | null;
    onPick: (threadId: string | null) => void;
    onNew: () => void;
    /** Bump to re-read: a chat only appears once its first line is sent. */
    refreshKey?: number;
}) {
    const [threads, setThreads] = useState<ThreadSummary[]>([]);

    const load = useCallback(async () => {
        const response = await threadsApiV1TimelineThreadsGet({ query: { limit: 20 } });
        // A list that could not be read is an empty list, not an error box:
        // the chat above still works, and this is navigation.
        if (response.error) return;
        setThreads(response.data?.threads ?? []);
    }, []);

    useEffect(() => {
        void load();
    }, [load, refreshKey]);

    // A brand-new chat is on screen but not yet in the list. Shown at the
    // top as itself so the person can see where they are.
    const fresh = current !== null && !threads.some((t) => t.thread_id === current);

    return (
        // One row that scrolls sideways, never a wrapping block: with twenty
        // chats a wrapped list grew taller than the conversation on a phone
        // and pushed the composer up into the middle of the screen.
        <div
            className="flex shrink-0 flex-nowrap items-center gap-2 overflow-x-auto px-4 pb-2 [scrollbar-width:none] sm:px-6 [&::-webkit-scrollbar]:hidden"
            data-testid="thread-list"
        >
            <button
                type="button"
                onClick={onNew}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-sm font-medium hover:bg-muted/40"
            >
                <MessageSquarePlus aria-hidden className="h-3.5 w-3.5" />
                New chat
            </button>
            {fresh && (
                <span
                    aria-current="true"
                    className="shrink-0 rounded-full bg-foreground px-3 py-1 text-sm text-background"
                >
                    New chat
                </span>
            )}
            {threads.map((thread) => {
                const active = thread.thread_id === current;
                return (
                    <button
                        key={thread.thread_id ?? "original"}
                        type="button"
                        aria-current={active ? "true" : undefined}
                        onClick={() => onPick(thread.thread_id)}
                        title={`${thread.messages} messages · ${ago(thread.last_at)}`}
                        className={
                            active
                                ? "max-w-[16rem] shrink-0 truncate rounded-full bg-foreground px-3 py-1 text-sm text-background"
                                : "max-w-[16rem] shrink-0 truncate rounded-full border border-border bg-card px-3 py-1 text-sm text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                        }
                    >
                        {titleOf(thread)}
                    </button>
                );
            })}
        </div>
    );
}
