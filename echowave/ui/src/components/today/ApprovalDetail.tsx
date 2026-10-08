"use client";

/**
 * Screen 08, exact action approval: one card in full, bound to its version.
 *
 * The decision is the controls card's own: Approve is
 * `POST /timeline/actions/settle` with the version on screen, so a second tab
 * or a phone approving the same version arms it once, and an edited card's
 * old version is refused ("This changed since you looked at it"). Editing
 * goes through `revise`, which mints a new version and cancels any approval.
 * An accepted approval reads "Approved", then "Working", and "Done" only with
 * the evidence the server wrote; an unknown outcome offers Check delivery,
 * never Retry.
 */

import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    approvalPreviewApiV1TodayApprovalsEventIdGet,
    reviseActionApiV1TimelineActionsRevisePost,
    settleActionApiV1TimelineActionsSettlePost,
} from "@/client/sdk.gen";
import { ActionPreview, type ApprovalStatus, ErrorState, TaskStatus } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import type { TaskState } from "@/lib/shell/taskState";
import type { ApprovalPreview, ScreenState } from "@/lib/today/types";

const UNKNOWN_COPY = "We are checking whether this was delivered. Please do not send it again.";
const POLL_MS = 2000;

/** Screen states that are still moving on the server. */
const MOVING: ScreenState[] = ["approved", "executing"];

const LEDGER: Record<ScreenState, TaskState> = {
    pending: "awaiting_approval",
    approved: "scheduled",
    executing: "running",
    completed: "completed",
    failed: "failed",
    outcome_unknown: "outcome_unknown",
    cancelled: "cancelled",
};

function previewStatus(state: ScreenState, committing: boolean): ApprovalStatus {
    if (committing) return "committing";
    if (state === "pending") return "pending";
    if (state === "cancelled") return "cancelled";
    return "approved";
}

export function ApprovalDetail({
    eventId,
    backHref,
    onChanged,
}: {
    eventId: number;
    /** Where Back goes (the Today list, keeping its scroll). */
    backHref?: string;
    /** Told after any decision, so a list can refresh its counts. */
    onChanged?: () => void;
}) {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const [preview, setPreview] = useState<ApprovalPreview | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [committing, setCommitting] = useState(false);
    const [changedNotice, setChangedNotice] = useState(false);
    const [editing, setEditing] = useState<Record<string, string> | null>(null);
    const poll = useRef<ReturnType<typeof setTimeout> | null>(null);

    const load = useCallback(async () => {
        const result = await approvalPreviewApiV1TodayApprovalsEventIdGet({ path: { event_id: eventId } });
        if (result.error) {
            setLoadError(detailFromError(result.error, "This approval could not load."));
            return null;
        }
        setLoadError(null);
        const data = result.data as unknown as ApprovalPreview;
        setPreview(data);
        return data;
    }, [eventId]);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
        return () => {
            if (poll.current) clearTimeout(poll.current);
        };
    }, [authLoading, signedIn, load]);

    // While the server is still moving it (undo window, then the run), read
    // it again until it settles. Nothing is claimed before the server says.
    useEffect(() => {
        if (!preview || !MOVING.includes(preview.screen_state)) return;
        poll.current = setTimeout(() => void load(), POLL_MS);
        return () => {
            if (poll.current) clearTimeout(poll.current);
        };
    }, [preview, load]);

    async function settle(verb: "confirm" | "decline" | "undo") {
        if (!preview) return;
        setCommitting(true);
        setActionError(null);
        const result = await settleActionApiV1TimelineActionsSettlePost({
            body: {
                event_id: preview.id,
                verb,
                ...(verb === "confirm" && preview.version ? { version: preview.version } : {}),
            },
        });
        setCommitting(false);
        if (result.error) {
            const message = detailFromError(result.error, "That did not go through.");
            const fresh = await load();
            if (fresh && fresh.version !== preview.version) setChangedNotice(true);
            setActionError(message);
            return;
        }
        setChangedNotice(false);
        await load();
        onChanged?.();
    }

    async function saveEdit() {
        if (!preview || !editing) return;
        setCommitting(true);
        const result = await reviseActionApiV1TimelineActionsRevisePost({
            body: { event_id: preview.id, arguments: editing },
        });
        setCommitting(false);
        if (result.error) {
            setActionError(detailFromError(result.error, "Your change was not saved. Try again."));
            return;
        }
        setEditing(null);
        setChangedNotice(true);
        await load();
        onChanged?.();
    }

    const back = backHref ? (
        <Link
            href={backHref}
            className="motion-m1 inline-flex min-h-11 items-center gap-1 rounded-md text-sm text-muted-foreground hover:text-foreground md:min-h-9"
        >
            <ArrowLeft aria-hidden className="h-4 w-4" />
            Back to Today
        </Link>
    ) : null;

    if (loadError && !preview) {
        return (
            <div className="flex flex-col gap-3">
                {back}
                <ErrorState title="This approval could not load." description={loadError} onRetry={() => void load()} />
            </div>
        );
    }
    if (!preview) {
        return (
            <div className="flex flex-col gap-3" aria-busy="true">
                {back}
                <Skeleton className="h-6 w-2/3" />
                <Skeleton className="h-40 w-full" />
            </div>
        );
    }

    const state = preview.screen_state;
    const settledDown = !MOVING.includes(state) && state !== "pending";
    return (
        <article className="flex flex-col gap-4" aria-labelledby={`approval-${preview.id}-title`} data-testid="approval-detail">
            {back}
            <header className="flex flex-col gap-1">
                <h2 id={`approval-${preview.id}-title`} className="text-lg font-semibold leading-snug">
                    {preview.verb}
                </h2>
                <p className="text-sm">{preview.sentence}</p>
                {preview.why && <p className="text-sm text-muted-foreground">Why: {preview.why}</p>}
            </header>

            {changedNotice && state === "pending" && (
                <p role="status" className="rounded-md border border-[#705500]/40 bg-[#705500]/5 px-3 py-2 text-sm text-[#705500] dark:text-amber-300">
                    This changed since you saw it. This is the new version; review it before approving.
                </p>
            )}

            {!settledDown && state !== "pending" && (
                <div role="status" aria-live="polite" className="motion-m2 flex flex-wrap items-center gap-2">
                    <TaskStatus state={LEDGER[state]} />
                    {preview.state === "released" && (
                        <span className="text-sm text-muted-foreground">Handed to your computer. It takes this step once.</span>
                    )}
                    {state === "approved" && (
                        <>
                            <span className="text-sm text-muted-foreground">Runs in a few seconds. You can still undo.</span>
                            <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void settle("undo")} disabled={committing}>
                                Undo
                            </Button>
                        </>
                    )}
                </div>
            )}

            {settledDown && (
                <div role="status" aria-live="polite" className="motion-m2 flex flex-col gap-2 rounded-md border border-border p-3">
                    <TaskStatus
                        state={LEDGER[state]}
                        evidence={
                            state === "completed"
                                ? preview.done_note
                                : state === "outcome_unknown"
                                  ? UNKNOWN_COPY
                                  : state === "failed"
                                    ? preview.error
                                    : null
                        }
                    />
                    {state === "outcome_unknown" && (
                        <Button variant="outline" className="motion-m1 min-h-11 self-start md:min-h-9" onClick={() => void load()}>
                            <RefreshCw aria-hidden className="h-4 w-4" />
                            Check delivery
                        </Button>
                    )}
                </div>
            )}

            {editing ? (
                <form
                    className="flex flex-col gap-3 rounded-[var(--radius)] border border-border p-4"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void saveEdit();
                    }}
                    aria-label="Edit before approving"
                >
                    <p className="text-sm text-muted-foreground">
                        Saving makes a new version. Any approval of the old one stops counting.
                    </p>
                    {Object.entries(editing).map(([key, value]) => (
                        <label key={key} className="flex flex-col gap-1 text-sm">
                            <span className="font-medium">{key}</span>
                            <Textarea
                                value={value}
                                className="text-base md:text-sm"
                                onChange={(e) => setEditing({ ...editing, [key]: e.target.value })}
                            />
                        </label>
                    ))}
                    <div className="flex flex-wrap gap-2">
                        <Button type="submit" className="motion-m1 min-h-11 md:min-h-9" disabled={committing}>
                            Save as a new version
                        </Button>
                        <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setEditing(null)}>
                            Keep it as it was
                        </Button>
                    </div>
                </form>
            ) : (
                <ActionPreview
                    preview={{
                        id: String(preview.id),
                        version: preview.version ?? "—",
                        action: preview.verb,
                        account: preview.account ?? undefined,
                        recipient: preview.recipient ?? undefined,
                        amount: preview.amount ?? undefined,
                        content: preview.content ?? undefined,
                        attachments: preview.attachments,
                        timing: preview.timing ?? undefined,
                        expiresAt: preview.expires_at,
                        consequence: preview.consequence,
                    }}
                    status={previewStatus(state, committing)}
                    approveLabel={preview.recipient ? "Approve and send" : "Approve"}
                    stickyDecision
                    decisionNote={preview.detail || preview.verb}
                    readOnly={preview.can_answer ? undefined : preview.answer_refusal ?? "Someone else answers this one."}
                    onApprove={() => void settle("confirm")}
                    onCancel={() => void settle("decline")}
                    onEdit={
                        preview.editable && preview.arguments
                            ? () =>
                                  setEditing(
                                      Object.fromEntries(
                                          Object.entries(preview.arguments ?? {}).map(([k, v]) => [
                                              k,
                                              typeof v === "string" ? v : JSON.stringify(v),
                                          ]),
                                      ),
                                  )
                            : undefined
                    }
                />
            )}

            {actionError && (
                <p role="alert" className="text-sm text-destructive">
                    {actionError}
                </p>
            )}
            {preview.revisions.length > 0 && (
                <p className="text-xs text-muted-foreground">
                    Edited {preview.revisions.length} time{preview.revisions.length === 1 ? "" : "s"}; only the version above can be approved.
                </p>
            )}
            {!preview.bound_to_version && state === "pending" && (
                <p className="text-xs text-muted-foreground">This card was made before versions; approving it approves exactly what is shown.</p>
            )}
        </article>
    );
}

export default ApprovalDetail;
