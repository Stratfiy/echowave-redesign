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
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { pendingApprovalsApiV1TodayApprovalsGet, settleActionApiV1TimelineActionsSettlePost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import type { ApprovalItem, ApprovalQueue } from "@/lib/today/types";

const POLL_MS = 5000;

export function ApprovalDock({ refreshKey = 0, onSettled }: { refreshKey?: number; onSettled?: () => void }) {
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
    return (
        <section
            aria-label="Waiting for your approval"
            className="motion-m6-enter mx-auto mb-2 w-full rounded-[var(--radius)] border border-border bg-card p-3 shadow-sm"
            data-testid="approval-dock"
        >
            {note && (
                <div role="status" aria-live="polite" className={`mb-2 flex flex-wrap items-center gap-2 text-sm ${note.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}>
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
            )}
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
