"use client";

/**
 * Screen 33, one action: its exact preview, the reason, the approval, the
 * run, and the immutable record of what happened -- with the controls the
 * viewer may use, and no others.
 *
 * A second person approves (the requester sees why they cannot). Run is
 * disabled while the same command is in flight, and "queued" reads as
 * accepted, not done; the screen follows the worker until the result is
 * in. "Outcome unknown" offers Reconcile, never Run again.
 */

import { CheckCircle2, Clock, HelpCircle, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { getAuthUserApiV1UserAuthUserGet } from "@/client/sdk.gen";
import { AuditTimeline, ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import {
    ACTION_STATE_LABEL,
    approveAction,
    loadAction,
    reconcileAction,
    rejectAction,
    runAction,
    type SupportAction,
    withdrawAction,
} from "@/lib/support/staff";
import { cn } from "@/lib/utils";

import { ActionPreviewBlock } from "./ActionPreviewBlock";

const IN_FLIGHT = new Set(["queued", "running"]);

function StateLine({ action }: { action: SupportAction }) {
    const icon =
        action.state === "succeeded" ? (
            <CheckCircle2 aria-hidden className="h-4 w-4" />
        ) : action.state === "failed" || action.state === "rejected" ? (
            <XCircle aria-hidden className="h-4 w-4" />
        ) : action.state === "outcome_unknown" ? (
            <HelpCircle aria-hidden className="h-4 w-4" />
        ) : IN_FLIGHT.has(action.state) ? (
            <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
        ) : (
            <Clock aria-hidden className="h-4 w-4" />
        );
    return (
        <p
            role="status"
            className={cn(
                "motion-m2 flex items-center gap-1.5 text-sm font-medium",
                action.state === "succeeded" && "text-[#075A39] dark:text-emerald-300",
                (action.state === "failed" || action.state === "rejected") && "text-[#772322] dark:text-red-300",
                (action.state === "outcome_unknown" || action.state === "requested") && "text-[#705500] dark:text-amber-300",
            )}
            data-testid="action-state"
        >
            {icon}
            {ACTION_STATE_LABEL[action.state] ?? action.state}
            {IN_FLIGHT.has(action.state) && <span className="font-normal text-muted-foreground"> · accepted, not finished</span>}
        </p>
    );
}

export function ActionDetail({ actionId }: { actionId: number }) {
    const [action, setAction] = useState<SupportAction | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [me, setMe] = useState<{ id: number; role: string | null } | null>(null);
    const [busy, setBusy] = useState(false);
    const [actError, setActError] = useState<string | null>(null);
    const [rejecting, setRejecting] = useState(false);
    const [note, setNote] = useState("");
    const poll = useRef<ReturnType<typeof setTimeout> | null>(null);

    const load = useCallback(async () => {
        const outcome = await loadAction(actionId);
        if (outcome.ok) {
            setAction(outcome.value);
            setError(null);
        } else setError(outcome.error);
        return outcome.ok ? outcome.value : null;
    }, [actionId]);

    useEffect(() => {
        void load();
        void getAuthUserApiV1UserAuthUserGet().then((r) => r.data && setMe({ id: r.data.id, role: r.data.staff_role }));
        return () => {
            if (poll.current) clearTimeout(poll.current);
        };
    }, [load]);

    // Follow the worker while the command is in flight, backing off.
    useEffect(() => {
        if (!action || !IN_FLIGHT.has(action.state)) return;
        let delay = 1500;
        const tick = async () => {
            const fresh = await load();
            if (fresh && IN_FLIGHT.has(fresh.state)) {
                delay = Math.min(delay * 1.5, 10000);
                poll.current = setTimeout(() => void tick(), delay);
            }
        };
        poll.current = setTimeout(() => void tick(), delay);
        return () => {
            if (poll.current) clearTimeout(poll.current);
        };
    }, [action?.state, load]); // eslint-disable-line react-hooks/exhaustive-deps

    async function act(run: () => Promise<{ ok: boolean; error?: string }>) {
        setBusy(true);
        setActError(null);
        const outcome = await run();
        setBusy(false);
        if (!outcome.ok) setActError(outcome.error ?? "That did not work.");
        await load();
    }

    if (error && !action) return <ErrorState title="This action could not load." description={error} onRetry={() => void load()} />;
    if (!action)
        return (
            <p role="status" className="text-sm text-muted-foreground">
                Loading the action…
            </p>
        );

    const mine = me?.id === action.requested_by_id;
    const mayApprove = !mine && action.state === "requested" && (action.approver_role !== "superadmin" || me?.role === "superadmin");
    const mayRun = action.state === "approved" && (mine || me?.id === action.approved_by_id);
    const recordLines = [
        `command      ${action.kind}`,
        `params       ${JSON.stringify(action.params)}`,
        `target       workspace #${action.organization_id}${action.target_user_id ? ` · person #${action.target_user_id}` : ""}${action.ticket_id ? ` · ticket #${action.ticket_id}` : ""}`,
        `environment  ${action.environment}`,
        `version      ${action.version}`,
        `approved     ${action.approved_version ?? "—"}`,
        `key          ${action.idempotency_key}`,
    ];

    return (
        <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2">
                <h1 className="min-w-0 flex-1 break-words text-xl font-semibold">
                    {action.title} <span className="text-muted-foreground">#{action.id}</span>
                </h1>
                <StateLine action={action} />
            </div>
            {action.ticket_id && (
                <Link href={`/superadmin/support/${action.ticket_id}`} className="min-h-11 text-sm underline-offset-2 hover:underline md:min-h-0">
                    Ticket #{action.ticket_id}
                </Link>
            )}

            <ActionPreviewBlock preview={{ ...action.preview, environment: action.environment }} />

            <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Reason</dt>
                <dd className="break-words">{action.reason}</dd>
                <dt className="text-muted-foreground">Requested by</dt>
                <dd>{action.requested_by}</dd>
                <dt className="text-muted-foreground">Approved by</dt>
                <dd>{action.approved_by ?? (action.state === "requested" ? `Waiting (${action.approver_role} tier)` : "—")}</dd>
                {action.decided_note && (
                    <>
                        <dt className="text-muted-foreground">Note</dt>
                        <dd className="break-words">{action.decided_note}</dd>
                    </>
                )}
                {action.expires_at && (action.state === "requested" || action.state === "approved") && (
                    <>
                        <dt className="text-muted-foreground">Expires</dt>
                        <dd>{new Date(action.expires_at).toLocaleString()}</dd>
                    </>
                )}
            </dl>

            {action.notice && (
                <p role="alert" className="rounded-[var(--radius)] border border-[#705500]/40 p-3 text-sm text-[#705500] dark:text-amber-300">
                    {action.notice}
                </p>
            )}
            {actError && (
                <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                    {actError}
                </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
                {mayApprove && (
                    <>
                        <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void act(() => approveAction(action.id, action.version))}>
                            Approve this version
                        </Button>
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => setRejecting((v) => !v)}>
                            Reject
                        </Button>
                    </>
                )}
                {mine && action.state === "requested" && (
                    <p className="text-sm text-muted-foreground">A second staff member approves this. You asked for it, so you cannot.</p>
                )}
                {!mine && action.state === "requested" && !mayApprove && (
                    <p className="text-sm text-muted-foreground">Approving this needs the {action.approver_role} role.</p>
                )}
                {mayRun && (
                    <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void act(() => runAction(action.id))}>
                        {busy && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                        Run
                    </Button>
                )}
                {IN_FLIGHT.has(action.state) && (
                    <Button type="button" className="min-h-11 md:min-h-9" disabled>
                        <Loader2 aria-hidden className="motion-continuous animate-spin" /> Running
                    </Button>
                )}
                {action.state === "outcome_unknown" && (
                    <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void act(() => reconcileAction(action.id))}>
                        Check what happened
                    </Button>
                )}
                {mine && (action.state === "requested" || action.state === "approved") && (
                    <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void act(() => withdrawAction(action.id))}>
                        Withdraw
                    </Button>
                )}
            </div>
            {rejecting && (
                <form
                    className="flex flex-col gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void act(() => rejectAction(action.id, note.trim()));
                    }}
                >
                    <label htmlFor="reject-note" className="text-sm font-medium">
                        Why not?
                    </label>
                    <textarea
                        id="reject-note"
                        rows={2}
                        className="w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base md:text-sm"
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                    />
                    <Button type="submit" variant="destructive" className="motion-m1 min-h-11 self-start md:min-h-9" disabled={busy || note.trim().length < 3}>
                        Reject request
                    </Button>
                </form>
            )}

            <section aria-label="Command record" className="rounded-[var(--radius)] border border-border">
                <h2 className="border-b border-border px-3 py-2 text-sm font-semibold">Command record</h2>
                <pre className="overflow-x-auto px-3 py-2 font-mono text-xs" aria-label="Command">
                    {recordLines.join("\n")}
                </pre>
                {action.result && (
                    <div className="border-t border-border px-3 py-2 text-sm" data-testid="action-result">
                        <p>{action.result.summary}</p>
                        {action.result.evidence && (
                            <p className="break-all font-mono text-xs text-muted-foreground">{JSON.stringify(action.result.evidence)}</p>
                        )}
                    </div>
                )}
            </section>

            <section aria-label="Audit">
                <h2 className="mb-2 text-sm font-semibold">Audit</h2>
                <AuditTimeline
                    entries={(action.history ?? []).map((h) => ({ id: h.id, at: h.at ?? "", actor: h.actor, action: h.action }))}
                    emptyTitle="Nothing recorded yet."
                />
            </section>
        </div>
    );
}

export default ActionDetail;
