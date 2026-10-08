"use client";

/**
 * A staff operation before it runs (handoff "Operational action"): the typed
 * command, the role it runs as, the environment, the target, the reason and
 * the idempotency key. A reason is required. Accepted means queued, and the
 * screen says so -- "Accepted, not finished" -- until the result arrives.
 */

import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type OperationalCommand = {
    command: string;
    role: string;
    environment: string;
    target: string;
    idempotencyKey: string;
    /** What it will change, in a sentence. */
    effect?: string;
};

export type CommandState = "ready" | "submitting" | "accepted" | "succeeded" | "failed";

export function CommandPreview({
    command,
    state,
    result,
    onConfirm,
    onCancel,
    className,
}: {
    command: OperationalCommand;
    state: CommandState;
    /** The server's result line, for succeeded or failed. */
    result?: string;
    onConfirm: (reason: string) => void;
    onCancel?: () => void;
    className?: string;
}) {
    const [reason, setReason] = useState("");
    const reasonId = useId();
    const production = command.environment.toLowerCase() === "production";
    const busy = state === "submitting";
    const done = state === "accepted" || state === "succeeded";
    return (
        <section
            aria-label={`Run ${command.command}`}
            className={cn("rounded-[var(--radius)] border p-4", production ? "border-[#772322]/40" : "border-border", className)}
            data-testid="command-preview"
            data-state={state}
        >
            <pre className="mb-3 overflow-x-auto rounded-md bg-muted/50 px-3 py-2 font-mono text-xs" aria-label="Command">
                {command.command}
            </pre>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Runs as</dt>
                <dd>{command.role}</dd>
                <dt className="text-muted-foreground">Environment</dt>
                <dd className={cn(production && "font-semibold text-[#772322] dark:text-red-300")}>{command.environment}</dd>
                <dt className="text-muted-foreground">Target</dt>
                <dd className="break-words">{command.target}</dd>
                <dt className="text-muted-foreground">Key</dt>
                <dd className="break-all font-mono text-xs">{command.idempotencyKey}</dd>
                {command.effect && (
                    <>
                        <dt className="text-muted-foreground">Effect</dt>
                        <dd>{command.effect}</dd>
                    </>
                )}
            </dl>
            {!done && (
                <div className="mt-3">
                    <label htmlFor={reasonId} className="text-sm font-medium">
                        Reason (recorded in the audit)
                    </label>
                    <textarea
                        id={reasonId}
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        rows={2}
                        disabled={busy}
                        className="mt-1 w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base md:text-sm"
                    />
                </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
                {state === "accepted" && (
                    <p role="status" className="flex items-center gap-1.5 text-sm text-muted-foreground">
                        <Clock aria-hidden className="h-4 w-4" /> Accepted, not finished. The result will show here.
                    </p>
                )}
                {state === "succeeded" && (
                    <p role="status" className="flex items-center gap-1.5 text-sm text-[#075A39] dark:text-emerald-300">
                        <CheckCircle2 aria-hidden className="h-4 w-4" /> {result ?? "Succeeded."}
                    </p>
                )}
                {state === "failed" && (
                    <p role="alert" className="flex items-center gap-1.5 text-sm text-[#772322] dark:text-red-300">
                        <XCircle aria-hidden className="h-4 w-4" /> {result ?? "Failed."}
                    </p>
                )}
                {!done && (
                    <>
                        <Button
                            type="button"
                            className="motion-m1 min-h-11 md:min-h-9"
                            variant={production ? "destructive" : "default"}
                            disabled={busy || !reason.trim()}
                            onClick={() => onConfirm(reason.trim())}
                        >
                            {busy && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                            {busy ? "Submitting…" : state === "failed" ? "Run again" : "Run"}
                        </Button>
                        {onCancel && (
                            <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={onCancel}>
                                Cancel
                            </Button>
                        )}
                    </>
                )}
            </div>
        </section>
    );
}

export default CommandPreview;
