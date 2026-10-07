"use client";

/**
 * One staff command from preview to result (design "Operational action";
 * screen 33's layout reused): the server's exact preview first, then the
 * shell's CommandPreview for the reason and Run, then the command's real
 * state, polled until it is final. Accepted is never shown as done: waiting
 * for a second person, queued and running each say so, and an unknown
 * outcome says to check before trying again.
 *
 * The idempotency key is made once per preview, so a double press, a retry
 * or a second tab sends the same request.
 */

import { AlertTriangle, Clock, Loader2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { CommandPreview, type CommandState } from "@/components/shell";
import { staffGet, staffPost } from "@/lib/staff/data";
import { newIdempotencyKey, words } from "@/lib/staff/format";

import { useStaffConsole } from "./StaffShell";

export type CommandView = {
    id: number;
    command: string;
    state: string;
    environment: string;
    preview: Record<string, unknown> | null;
    result: Record<string, unknown> | null;
    reason_code: string | null;
    approval_required: boolean;
    requested_by: number;
    approved_by: number | null;
};

type Preview = {
    eligible: boolean;
    refusal: string | null;
    preview: Record<string, unknown> | null;
    requires_approval: boolean;
    execution: string;
    summary: string;
    environment: string;
    roles: string[];
};

const FINAL = new Set(["succeeded", "failed", "rejected", "expired", "outcome_unknown"]);

function toShellState(view: CommandView | null, submitting: boolean, error: string | null): CommandState {
    if (submitting) return "submitting";
    if (error) return "failed";
    if (!view) return "ready";
    if (view.state === "succeeded") return "succeeded";
    if (view.state === "failed" || view.state === "rejected" || view.state === "expired") return "failed";
    return "accepted";
}

export function PreviewValue({ value }: { value: unknown }) {
    if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
    if (Array.isArray(value)) {
        return (
            <ul className="list-disc pl-4">
                {value.map((item, i) => (
                    <li key={i}>
                        <PreviewValue value={item} />
                    </li>
                ))}
            </ul>
        );
    }
    if (typeof value === "object") {
        return (
            <dl className="grid grid-cols-[auto_1fr] gap-x-2">
                {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
                    <div key={k} className="contents">
                        <dt className="text-muted-foreground">{words(k)}</dt>
                        <dd className="min-w-0 break-words">
                            <PreviewValue value={v} />
                        </dd>
                    </div>
                ))}
            </dl>
        );
    }
    return <span className="break-words">{String(value)}</span>;
}

export function CommandFlow({
    command,
    target,
    targetLabel,
    effect,
    renderPreview,
    onDone,
    onCancel,
}: {
    command: string;
    target: Record<string, unknown>;
    /** Who or what it acts on, in words, pinned above the reason. */
    targetLabel: string;
    effect?: string;
    renderPreview?: (preview: Record<string, unknown>) => ReactNode;
    onDone?: (view: CommandView) => void;
    onCancel?: () => void;
}) {
    const { me } = useStaffConsole();
    const key = useMemo(() => newIdempotencyKey(command), [command]);
    const [preview, setPreview] = useState<Preview | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [view, setView] = useState<CommandView | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const targetKey = JSON.stringify(target);
    const doneRef = useRef(false);

    useEffect(() => {
        let alive = true;
        void staffPost<Preview>("/api/v1/admin/staff/commands/preview", { command, target }).then((r) => {
            if (!alive) return;
            if (r.ok) setPreview(r.data);
            else setPreviewError(r.status === 404 ? "This command is not switched on here (needs setup)." : r.error);
        });
        return () => {
            alive = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [command, targetKey]);

    useEffect(() => {
        if (!view || FINAL.has(view.state)) {
            if (view && FINAL.has(view.state) && !doneRef.current) {
                doneRef.current = true;
                onDone?.(view);
            }
            return;
        }
        if (view.state === "awaiting_approval") return;
        const timer = window.setTimeout(async () => {
            const r = await staffGet<CommandView>(`/api/v1/admin/staff/commands/${view.id}`);
            if (r.ok) setView(r.data);
        }, 2000);
        return () => window.clearTimeout(timer);
    }, [view, onDone]);

    async function run(reason: string) {
        setSubmitting(true);
        setError(null);
        const r = await staffPost<CommandView>("/api/v1/admin/staff/commands", {
            command,
            target,
            reason,
            idempotency_key: key,
            environment: me.environment,
        });
        setSubmitting(false);
        if (r.ok) setView(r.data);
        else setError(r.error);
    }

    if (previewError) {
        return (
            <p role="alert" className="flex items-start gap-2 rounded-md border border-border p-3 text-sm">
                <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> {previewError}
            </p>
        );
    }
    if (!preview) {
        return (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" /> Preparing the exact preview…
            </p>
        );
    }
    if (!preview.eligible && !view) {
        return (
            <div role="alert" className="rounded-md border border-border p-3 text-sm" data-testid="command-refused">
                <p className="font-medium">{preview.summary}</p>
                <p className="mt-1">Not possible right now: {preview.refusal}</p>
                {onCancel && (
                    <button type="button" className="mt-2 min-h-11 text-sm underline md:min-h-8" onClick={onCancel}>
                        Close
                    </button>
                )}
            </div>
        );
    }
    const resultLine =
        view?.state === "failed" || view?.state === "rejected" || view?.state === "expired"
            ? `${words(view.state)}: ${String((view.result as Record<string, unknown> | null)?.message ?? view.reason_code ?? "no detail")}`
            : error ?? undefined;
    return (
        <div className="space-y-3" data-testid="command-flow" data-command={command}>
            <p className="text-sm">{preview.summary}</p>
            {preview.preview && Object.keys(preview.preview).length > 0 && (
                <div className="rounded-md border border-border bg-muted/30 p-3 text-sm" aria-label="Exact effect">
                    {renderPreview ? renderPreview(preview.preview) : <PreviewValue value={preview.preview} />}
                </div>
            )}
            <CommandPreview
                command={{
                    command,
                    role: preview.roles.join(", "),
                    environment: preview.environment,
                    target: targetLabel,
                    idempotencyKey: key,
                    effect:
                        effect ??
                        (preview.requires_approval
                            ? "Needs a second person's approval before it runs."
                            : preview.execution === "worker"
                              ? "Queued, then run once by the worker."
                              : "Runs as soon as you ask."),
                }}
                state={toShellState(view, submitting, error)}
                result={resultLine ?? (view?.state === "succeeded" ? "Done." : undefined)}
                onConfirm={(reason) => void run(reason)}
                onCancel={onCancel}
            />
            {view && !FINAL.has(view.state) && (
                <p role="status" className="flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="command-state" data-state={view.state}>
                    <Clock aria-hidden className="h-4 w-4" />
                    {view.state === "awaiting_approval"
                        ? `Command #${view.id} is waiting for a second person to approve it.`
                        : `Command #${view.id} is ${words(view.state).toLowerCase()}.`}
                </p>
            )}
            {view?.state === "outcome_unknown" && (
                <p role="alert" className="flex items-center gap-1.5 text-sm text-[#705500] dark:text-amber-300" data-testid="command-state" data-state={view.state}>
                    <AlertTriangle aria-hidden className="h-4 w-4" /> We are checking whether this happened. Do not run it again.
                </p>
            )}
        </div>
    );
}
