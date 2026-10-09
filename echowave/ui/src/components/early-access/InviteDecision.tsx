"use client";

/**
 * The page an approver lands on from the Approve / Reject buttons in an
 * invite-request mail: /invite-requests/decide?token=…
 *
 * Opening it only reads (GET). Mail scanners and link previews fetch every
 * URL in a message, so nothing is decided until the approver presses the one
 * button here, which POSTs. No session is needed: the signed token is the
 * authority, and the server checks it is unexpired and that the request is
 * still pending. A second visit says who decided and when.
 */

import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

import {
    confirmInviteDecisionApiV1PublicInviteRequestsDecidePost,
    previewInviteDecisionApiV1PublicInviteRequestsDecideGet,
} from "@/client/sdk.gen";
import type { InviteDecisionPreview, InviteDecisionResult, InviteRequestView } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";

export const DECISION_COPY = {
    loading: "Opening the request…",
    missing: "This link is incomplete. Use the button in the email again.",
    failed: "That did not go through. Check your connection and try again.",
    approveTitle: "Approve this invite request?",
    rejectTitle: "Reject this invite request?",
    approveButton: "Approve and send the invite",
    rejectButton: "Reject",
    approveHint: "They get a code for this address and a short welcome email.",
    rejectHint: "Nothing is sent to them.",
    working: "Working…",
    approved: "Approved",
    approvedBody: (email: string) => `The invite code and a welcome email are on their way to ${email}.`,
    mailFailed: "The welcome email could not be sent. Send them this link yourself:",
    rejected: "Rejected",
    rejectedBody: "Nothing was sent to them.",
} as const;

function Field({ label, value }: { label: string; value: string | null | undefined }) {
    if (!value) return null;
    return (
        <div className="grid grid-cols-[7rem_1fr] gap-2 py-1">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 whitespace-pre-wrap break-words">{value}</dd>
        </div>
    );
}

function RequestCard({ request }: { request: InviteRequestView }) {
    return (
        <dl className="rounded-[var(--radius)] border border-border p-4 text-sm" data-testid="invite-request">
            <Field label="Name" value={request.name || "Not given"} />
            <Field label="Email" value={request.email} />
            <Field label="Note" value={request.note} />
            <Field label="What they do" value={request.occupation} />
        </dl>
    );
}

export function InviteDecision({ token }: { token: string | null }) {
    const [preview, setPreview] = useState<InviteDecisionPreview | null>(null);
    const [result, setResult] = useState<InviteDecisionResult | null>(null);
    const [error, setError] = useState<string | null>(token ? null : DECISION_COPY.missing);
    const [working, setWorking] = useState(false);

    useEffect(() => {
        if (!token) return;
        let cancelled = false;
        void (async () => {
            try {
                const response = await previewInviteDecisionApiV1PublicInviteRequestsDecideGet({ query: { token } });
                if (cancelled) return;
                if (response.error || !response.data) {
                    setError(detailFromResult(response, DECISION_COPY.failed));
                    return;
                }
                setPreview(response.data);
            } catch {
                if (!cancelled) setError(DECISION_COPY.failed);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [token]);

    const confirm = async () => {
        if (!token || working) return;
        setWorking(true);
        setError(null);
        try {
            const response = await confirmInviteDecisionApiV1PublicInviteRequestsDecidePost({ body: { token } });
            if (response.error || !response.data) {
                setError(detailFromResult(response, DECISION_COPY.failed));
                return;
            }
            setResult(response.data);
        } catch {
            setError(DECISION_COPY.failed);
        } finally {
            setWorking(false);
        }
    };

    if (error && !preview) {
        return (
            <p role="alert" className="text-base text-destructive" data-testid="invite-decision-error">
                {error}
            </p>
        );
    }
    if (!preview) {
        return (
            <p className="flex items-center gap-2 text-base text-muted-foreground" role="status">
                <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                {DECISION_COPY.loading}
            </p>
        );
    }

    const request = result?.request ?? preview.request;
    const settled = result ? result.outcome : request.state !== "pending" ? "already_decided" : null;

    if (settled) {
        const approved = settled === "approved";
        const rejected = settled === "rejected";
        const title = approved ? DECISION_COPY.approved : rejected ? DECISION_COPY.rejected : request.decided_message;
        return (
            <div className="flex flex-col gap-4" role="status" data-testid="invite-decision-result" data-outcome={settled}>
                <p className="flex items-center gap-2 text-base font-semibold">
                    {rejected || request.state === "rejected" ? (
                        <XCircle aria-hidden className="h-5 w-5 text-destructive" />
                    ) : (
                        <CheckCircle2 aria-hidden className="h-5 w-5 text-[#075A39]" />
                    )}
                    {title}
                </p>
                {approved && result?.mail_sent !== false && (
                    <p className="text-base text-muted-foreground">{DECISION_COPY.approvedBody(request.email)}</p>
                )}
                {approved && result?.mail_sent === false && result.signup_link && (
                    <p className="text-base">
                        {DECISION_COPY.mailFailed} <span className="break-all font-mono text-sm">{result.signup_link}</span>
                    </p>
                )}
                {rejected && <p className="text-base text-muted-foreground">{DECISION_COPY.rejectedBody}</p>}
                <RequestCard request={request} />
            </div>
        );
    }

    const approve = preview.action === "approve";
    return (
        <div className="flex flex-col gap-4" data-testid="invite-decision-confirm" data-action={preview.action}>
            <h2 className="text-lg font-semibold">{approve ? DECISION_COPY.approveTitle : DECISION_COPY.rejectTitle}</h2>
            <RequestCard request={request} />
            <p className="text-sm text-muted-foreground">{approve ? DECISION_COPY.approveHint : DECISION_COPY.rejectHint}</p>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            <Button
                type="button"
                className="min-h-11 w-full text-base"
                variant={approve ? "default" : "destructive"}
                disabled={working}
                onClick={confirm}
                data-testid="invite-decision-confirm-button"
            >
                {working && <Loader2 aria-hidden className="animate-spin" />}
                {working ? DECISION_COPY.working : approve ? DECISION_COPY.approveButton : DECISION_COPY.rejectButton}
            </Button>
        </div>
    );
}

export default InviteDecision;
