"use client";

/**
 * A pending approval docked directly above the composer until it is
 * answered (founder's approvals reference, 7 Oct): one plain sentence --
 * "Decibyl wants to: Pay Acme Print's invoice: ₹4,800" -- one line of exact
 * detail, and Do it / Don't.
 *
 * Do it is the controls card's Confirm with the version this dock shows, so
 * the same approval from the thread's own card, Today or a phone runs once,
 * and a card edited since is refused rather than approved unseen. The undo
 * window and the after-states stay the card's own (actions.py).
 *
 * `variant="waiting"` is the same approval as the approved design's home
 * card (Home.dc.html), drawn under the composer on an empty Chat: the
 * agent's blob, "<who> is waiting for you", the request, then See everything
 * it will do (the exact approval screen) and Do it (the same confirm as above).
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { pendingApprovalsApiV1TodayApprovalsGet, settleActionApiV1TimelineActionsSettlePost } from "@/client/sdk.gen";
import { BlobFace } from "@/components/brand/BlobFace";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import type { ApprovalItem, ApprovalQueue } from "@/lib/today/types";

const POLL_MS = 5000;

/** "Accounts wants to: Send …" read as who is asking and what. A sentence
 *  in another shape is all request, asked by `fallback`. */
export function splitAsk(sentence: string, fallback: string): { who: string; request: string } {
    const at = sentence.indexOf(" wants to:");
    if (at <= 0) return { who: fallback, request: sentence };
    const request = sentence.slice(at + " wants to:".length).trim();
    return { who: sentence.slice(0, at), request: request ? request[0].toUpperCase() + request.slice(1) : sentence };
}

export function ApprovalDock({
    refreshKey = 0,
    onSettled,
    variant = "dock",
}: {
    refreshKey?: number;
    onSettled?: () => void;
    /** "waiting": the home's card under the composer (the approved design). */
    variant?: "dock" | "waiting";
}) {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const [queue, setQueue] = useState<ApprovalQueue | null>(null);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<{ kind: "ok" | "error"; text: string; undo?: number; label?: string } | null>(null);
    const timer = useRef<ReturnType<typeof setInterval> | null>(null);

    const load = useCallback(async () => {
        const result = await pendingApprovalsApiV1TodayApprovalsGet();
        // A failed read keeps what was shown rather than hiding a pending
        // approval behind a blank dock.
        if (!result.error && result.data) setQueue(result.data as unknown as ApprovalQueue);
    }, []);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
        timer.current = setInterval(() => void load(), POLL_MS);
        return () => {
            if (timer.current) clearInterval(timer.current);
        };
    }, [authLoading, signedIn, load, refreshKey]);

    async function settle(item: Pick<ApprovalItem, "id" | "version" | "label">, verb: "confirm" | "decline" | "undo") {
        setBusy(true);
        const result = await settleActionApiV1TimelineActionsSettlePost({
            body: {
                event_id: item.id,
                verb,
                ...(verb === "confirm" && item.version ? { version: item.version } : {}),
            },
        });
        setBusy(false);
        if (result.error) {
            setNote({ kind: "error", text: detailFromError(result.error, "That did not go through.") });
        } else if (verb === "confirm") {
            setNote({ kind: "ok", text: `Approved: ${item.label}. It runs in a few seconds.`, undo: item.id, label: item.label });
        } else if (verb === "undo") {
            setNote({ kind: "ok", text: "Undone. Nothing was done." });
        } else {
            setNote({ kind: "ok", text: `Not done: ${item.label}.` });
        }
        await load();
        onSettled?.();
    }

    const item = queue?.items[0];
    if (!item && !note) return null;
    const more = (queue?.count ?? 0) - 1;
    const noteRow = note && (
        <div role="status" aria-live="polite" className={`flex flex-wrap items-center gap-2 text-sm ${note.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}>
            <span className="min-w-0 break-words">{note.text}</span>
            {note.undo !== undefined && (
                <Button
                    variant="ghost"
                    className="motion-m1 min-h-11 md:min-h-8"
                    disabled={busy}
                    onClick={() => void settle({ id: note.undo!, version: null, label: note.label ?? "" }, "undo")}
                >
                    Undo
                </Button>
            )}
            <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-8" onClick={() => setNote(null)} aria-label="Dismiss this note">
                OK
            </Button>
        </div>
    );
    if (variant === "waiting") {
        const ask = item ? splitAsk(item.sentence, "Decibyl") : null;
        return (
            <section
                aria-label="Waiting for your approval"
                className="motion-m6-enter flex w-full flex-col gap-2 rounded-[20px] bg-[var(--paper-2,#f9f9f9)] px-[18px] py-4"
                data-testid="approval-waiting"
            >
                {noteRow}
                {item && ask && (
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-3.5">
                        <div className="flex min-w-0 flex-1 items-center gap-3.5">
                            {item.workflow_id !== null ? (
                                <BlobFace seed={item.workflow_id} size={36} />
                            ) : (
                                <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-foreground text-[15px] font-semibold text-background">
                                    d
                                </span>
                            )}
                            <div className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
                                <span className="text-[13px] text-[var(--ink-2,#5d5d5d)]">
                                    {ask.who} is waiting for you
                                    {more > 0 && (
                                        <>
                                            {" · "}
                                            <Link href="/tasks" className="underline-offset-2 hover:underline">
                                                {more} more waiting
                                            </Link>
                                        </>
                                    )}
                                </span>
                                <span className="break-words text-[15px] leading-snug" data-testid="approval-waiting-request">
                                    {ask.request}
                                </span>
                                {item.detail && <span className="break-words text-[13px] text-[var(--ink-3,#8f8f8f)]">{item.detail}</span>}
                            </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2 pl-[50px] sm:pl-0">
                            <Link
                                href={`/tasks/approvals/${item.id}`}
                                className="motion-m1 inline-flex h-11 items-center rounded-full border border-[rgba(0,0,0,0.1)] bg-background px-3.5 text-sm text-foreground hover:bg-muted sm:h-9 dark:border-border"
                            >
                                See everything it will do
                            </Link>
                            <button
                                type="button"
                                disabled={busy}
                                onClick={() => void settle(item, "confirm")}
                                className="motion-m1 inline-flex h-11 items-center rounded-full bg-foreground px-4 text-sm text-background hover:opacity-90 disabled:opacity-60 sm:h-9"
                            >
                                Do it
                            </button>
                        </div>
                    </div>
                )}
            </section>
        );
    }
    return (
        <section
            aria-label="Waiting for your approval"
            className="motion-m6-enter mx-auto mb-2 w-full rounded-[var(--radius)] border border-border bg-card p-3 shadow-sm"
            data-testid="approval-dock"
        >
            {noteRow && <div className="mb-2">{noteRow}</div>}
            {item && (
                <div className="flex flex-col gap-2">
                    <p className="break-words text-sm font-medium" data-testid="approval-dock-sentence">
                        {item.sentence}
                    </p>
                    {item.detail && <p className="break-words text-xs text-muted-foreground">{item.detail}</p>}
                    <div className="flex flex-wrap items-center gap-2">
                        <Button className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void settle(item, "confirm")}>
                            Do it
                        </Button>
                        <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void settle(item, "decline")}>
                            Don&apos;t
                        </Button>
                        <Link
                            href={`/tasks/approvals/${item.id}`}
                            className="motion-m1 inline-flex min-h-11 items-center px-2 text-sm text-muted-foreground underline-offset-2 hover:underline md:min-h-9"
                        >
                            See everything it will do
                        </Link>
                        {more > 0 && (
                            <Link href="/tasks" className="inline-flex min-h-11 items-center px-2 text-sm text-muted-foreground underline-offset-2 hover:underline md:min-h-9">
                                {more} more waiting
                            </Link>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
}

export default ApprovalDock;
