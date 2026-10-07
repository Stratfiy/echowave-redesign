"use client";

/**
 * The exact action, before it happens (handoff "Approval state").
 *
 * What will be done, from which account, to whom, with what content and
 * attachments, when, and what follows from it -- bound to one preview id and
 * version. Approve is disabled while the approval is being committed, so a
 * double tap is one approval; an expired or changed preview cannot be
 * approved and asks for a fresh review. Editing hands back to the caller,
 * which must mint a new version: an edit never carries an old approval.
 */

import { CheckCircle2, Clock, Loader2, Paperclip, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type ActionPreviewData = {
    id: string;
    /** The card's payload version: a number, or the controls card's hash. */
    version: number | string;
    /** "Send email", "Book appointment". */
    action: string;
    /** The account it acts as: "nithya@clinic.in (Gmail)". */
    account?: string;
    recipient?: string;
    /** The exact amount, as it will be paid: "₹4,800". */
    amount?: string;
    /** The exact words or payload, as it will go. */
    content?: string;
    attachments?: string[];
    /** When it will run: "Now" or "Tomorrow 9:00 AM IST". */
    timing?: string;
    /** ISO time after which the approval no longer holds. */
    expiresAt?: string | null;
    /** What happens once it runs: "Ravi will receive this on WhatsApp". */
    consequence?: string;
};

export type ApprovalStatus = "pending" | "committing" | "approved" | "expired" | "changed" | "cancelled";

const ROW = "grid grid-cols-[minmax(5.5rem,auto)_1fr] gap-x-3 gap-y-1 text-sm";

function Row({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className={ROW}>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words">{children}</dd>
        </div>
    );
}

export function ActionPreview({
    preview,
    status,
    onApprove,
    onEdit,
    onCancel,
    now = Date.now(),
    className,
    approveLabel = "Approve",
    stickyDecision = false,
    decisionNote,
}: {
    preview: ActionPreviewData;
    status: ApprovalStatus;
    onApprove: (id: string, version: number | string) => void;
    onEdit?: () => void;
    onCancel?: () => void;
    /** For tests and for a screen that ticks. */
    now?: number;
    className?: string;
    /** The primary button's words: "Approve and send". */
    approveLabel?: string;
    /** Keep the decision in a footer that stays on screen (screen 08 on a
     *  phone), with ``decisionNote`` -- the final recipient and action --
     *  beside the button. */
    stickyDecision?: boolean;
    decisionNote?: ReactNode;
}) {
    const expired =
        status === "expired" || (!!preview.expiresAt && new Date(preview.expiresAt).getTime() <= now);
    const settled = status === "approved" || status === "cancelled";
    const blocked = expired || status === "changed";
    const committing = status === "committing";
    return (
        <section
            aria-label={`Review: ${preview.action}`}
            className={cn("rounded-[var(--radius)] border border-border bg-card p-4", className)}
            data-testid="action-preview"
            data-status={expired ? "expired" : status}
        >
            <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">{preview.action}</h3>
                <span className="font-mono text-[11px] text-muted-foreground" title="Preview version">
                    v{preview.version}
                </span>
            </header>
            <dl className="flex flex-col gap-1.5">
                {preview.account && <Row label="From">{preview.account}</Row>}
                {preview.recipient && <Row label="To">{preview.recipient}</Row>}
                {preview.amount && <Row label="Amount">{preview.amount}</Row>}
                {preview.timing && <Row label="When">{preview.timing}</Row>}
                {preview.content && (
                    <Row label="Content">
                        <span className="block whitespace-pre-wrap rounded-md border border-border bg-muted/40 px-2 py-1.5">
                            {preview.content}
                        </span>
                    </Row>
                )}
                {preview.attachments && preview.attachments.length > 0 && (
                    <Row label="Attached">
                        <ul className="flex flex-wrap gap-1.5">
                            {preview.attachments.map((name) => (
                                <li key={name} className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-xs">
                                    <Paperclip aria-hidden className="h-3 w-3" />
                                    {name}
                                </li>
                            ))}
                        </ul>
                    </Row>
                )}
                {preview.consequence && <Row label="Then">{preview.consequence}</Row>}
                {preview.expiresAt && !expired && (
                    <Row label="Valid until">
                        <time dateTime={preview.expiresAt}>
                            {new Date(preview.expiresAt).toLocaleString(undefined, {
                                day: "numeric",
                                month: "short",
                                hour: "numeric",
                                minute: "2-digit",
                            })}
                        </time>
                    </Row>
                )}
            </dl>
            <div
                className={cn(
                    "mt-4 flex flex-wrap items-center gap-2",
                    stickyDecision &&
                        "sticky bottom-0 -mx-4 -mb-4 border-t border-border bg-card px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]",
                )}
                role="group"
                aria-label="Decision"
            >
                {decisionNote && !settled && <p className="w-full min-w-0 break-words text-sm text-muted-foreground">{decisionNote}</p>}
                {status === "approved" && (
                    <p role="status" className="flex items-center gap-1.5 text-sm text-[#075A39] dark:text-emerald-300">
                        <CheckCircle2 aria-hidden className="h-4 w-4" />
                        Approved. It runs once.
                    </p>
                )}
                {status === "cancelled" && (
                    <p role="status" className="text-sm text-muted-foreground">
                        Cancelled. Nothing was done.
                    </p>
                )}
                {blocked && !settled && (
                    <p role="status" className="flex items-center gap-1.5 text-sm text-[#705500] dark:text-amber-300">
                        {expired ? <Clock aria-hidden className="h-4 w-4" /> : <ShieldAlert aria-hidden className="h-4 w-4" />}
                        {expired
                            ? "This approval expired. Review it again to continue."
                            : "This changed since you saw it. Review the new version."}
                    </p>
                )}
                {!settled && (
                    <>
                        <Button
                            type="button"
                            className="motion-m1 min-h-11 md:min-h-9"
                            disabled={committing || blocked}
                            aria-disabled={committing || blocked}
                            onClick={() => onApprove(preview.id, preview.version)}
                        >
                            {committing && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                            {committing ? "Approving…" : approveLabel}
                        </Button>
                        {onEdit && (
                            <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={committing} onClick={onEdit}>
                                Edit
                            </Button>
                        )}
                        {onCancel && (
                            <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={committing} onClick={onCancel}>
                                Cancel
                            </Button>
                        )}
                    </>
                )}
            </div>
        </section>
    );
}

export default ActionPreview;
