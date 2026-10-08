"use client";

/**
 * One of the person's own identity cards (disconnect an app, send from their
 * Decibyl address, request a number), shown where they asked for it -- never
 * "go to Chat to approve" (LAUNCH-PLAN rule 5).
 *
 * The exact preview is the shell's ActionPreview; Approve sends the version
 * the person is looking at, so an edit made elsewhere is refused rather than
 * approved unseen. After Approve the card follows the controls lifecycle:
 * armed (with Undo inside the window), running, done, failed, or outcome
 * unknown -- which is never "failed" and never retried, and where a provider
 * cannot be asked, the person says whether it arrived.
 */

import { CheckCircle2, CircleAlert, HelpCircle, Loader2 } from "lucide-react";
import { useState } from "react";

import { sayWhetherItArrivedApiV1MeOutcomesEventIdPost, settleActionApiV1TimelineActionsSettlePost } from "@/client/sdk.gen";
import type { IdentityCard } from "@/client/types.gen";
import { ActionPreview, type ApprovalStatus } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useFeature } from "@/lib/features";

export const UNKNOWN_COPY = "We are checking whether this was delivered. Please do not send it again.";

const PREVIEW_STATES: Record<string, ApprovalStatus> = {
    proposed: "pending",
    armed: "approved",
    declined: "cancelled",
    cancelled: "cancelled",
};

function account(card: IdentityCard): string | undefined {
    const args = card.args as Record<string, string | undefined>;
    if (args.from_address) return `${args.from_address} (your Decibyl address)`;
    if (args.toolkit) return `${args.toolkit} (${args.scope === "workspace" ? "the workspace's" : "yours"})`;
    return undefined;
}

function content(card: IdentityCard): string | undefined {
    const args = card.args as Record<string, string | undefined>;
    if (args.subject || args.body) return [args.subject && `Subject: ${args.subject}`, args.body].filter(Boolean).join("\n\n");
    return undefined;
}

export function IdentityCardPanel({
    card,
    onChanged,
}: {
    card: IdentityCard;
    /** Called after any press, so the owner can refetch the card. */
    onChanged: () => void;
}) {
    const reconciliation = useFeature("identity_reconciliation");
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const settle = async (verb: "confirm" | "decline" | "undo") => {
        setBusy(verb);
        setError(null);
        const res = await settleActionApiV1TimelineActionsSettlePost({
            body: { event_id: card.event_id, verb, version: verb === "confirm" ? card.version ?? null : null },
        });
        setBusy(null);
        if (res.error) setError(detailFromError(res.error, "That did not go through. Try again."));
        onChanged();
    };

    const answer = async (arrived: boolean) => {
        setBusy(arrived ? "arrived" : "not_arrived");
        setError(null);
        const res = await sayWhetherItArrivedApiV1MeOutcomesEventIdPost({
            path: { event_id: card.event_id },
            body: { arrived },
        });
        setBusy(null);
        if (res.error) setError(detailFromError(res.error, "That was not saved. Try again."));
        onChanged();
    };

    const args = card.args as Record<string, string | undefined>;
    const previewStatus: ApprovalStatus = busy === "confirm" ? "committing" : PREVIEW_STATES[card.state] ?? "approved";
    const showPreview = card.state === "proposed" || card.state === "armed" || card.state === "declined";

    return (
        <div className="flex flex-col gap-2" data-testid="identity-card" data-state={card.state}>
            {showPreview && (
                <ActionPreview
                    preview={{
                        id: String(card.event_id),
                        version: card.revisions ?? 1,
                        action: card.label,
                        account: account(card),
                        recipient: args.to ?? args.address,
                        content: content(card),
                        consequence: card.effect ?? undefined,
                    }}
                    status={previewStatus}
                    onApprove={() => void settle("confirm")}
                    onCancel={card.state === "proposed" ? () => void settle("decline") : undefined}
                />
            )}
            {card.state === "armed" && (
                <div className="flex flex-wrap items-center gap-2 text-sm" role="status">
                    <span>Runs in a few seconds.</span>
                    <Button type="button" variant="outline" className="min-h-11 md:min-h-9" disabled={busy !== null} onClick={() => void settle("undo")}>
                        Undo
                    </Button>
                </div>
            )}
            {card.state === "running" && (
                <p role="status" className="flex items-center gap-1.5 text-sm">
                    <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" /> {card.label}: running.
                </p>
            )}
            {card.state === "done" && (
                <p role="status" className="flex items-start gap-1.5 text-sm text-[#075A39] dark:text-emerald-300">
                    <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                    <span className="min-w-0 [overflow-wrap:anywhere]">{card.done_note || `${card.label}: done.`}</span>
                </p>
            )}
            {card.state === "failed" && (
                <p role="status" className="flex items-start gap-1.5 text-sm text-[#772322] dark:text-red-300">
                    <CircleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                    <span className="min-w-0 [overflow-wrap:anywhere]">{card.error || "That did not work."}</span>
                </p>
            )}
            {card.state === "outcome_unknown" && (
                <div className="rounded-[var(--radius)] border border-border p-3 text-sm" role="status">
                    <p className="flex items-start gap-1.5 text-[#705500] dark:text-amber-300">
                        <HelpCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                            {card.label}: {UNKNOWN_COPY}
                        </span>
                    </p>
                    {reconciliation && card.needs_person && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                            <span>Did it arrive?</span>
                            <Button type="button" variant="outline" className="min-h-11 md:min-h-9" disabled={busy !== null} onClick={() => void answer(true)}>
                                It arrived
                            </Button>
                            <Button type="button" variant="outline" className="min-h-11 md:min-h-9" disabled={busy !== null} onClick={() => void answer(false)}>
                                It did not arrive
                            </Button>
                        </div>
                    )}
                </div>
            )}
            {error && (
                <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                    {error}
                </p>
            )}
        </div>
    );
}

export default IdentityCardPanel;
