"use client";

/**
 * An infrastructure or platform command from the ops stream
 * (`/api/v1/admin/ops/commands`: pauses, retries, flag changes, drains). The
 * console never re-implements them: it asks the ops service, shows its typed
 * contract in the shell's CommandPreview, and follows the command's real
 * state. Until the ops console is on this build, it says "needs setup".
 */

import { AlertTriangle, Clock, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CommandPreview, type CommandState } from "@/components/shell";
import { staffGet, staffPost, useStaffData } from "@/lib/staff/data";
import { newIdempotencyKey, words } from "@/lib/staff/format";

import { useStaffConsole } from "./StaffShell";

type OpsView = { id: number; command: string; state: string; result: Record<string, unknown> | null; reason_code: string | null };
const FINAL = new Set(["succeeded", "failed", "rejected", "expired", "needs_setup", "outcome_unknown"]);

export function OpsCommand({ command, target, targetLabel, effect }: { command: string; target: Record<string, unknown>; targetLabel: string; effect?: string }) {
    const { me } = useStaffConsole();
    const catalogue = useStaffData<{ commands: Array<{ name: string; requires_approval: boolean; summary: string }> }>("/api/v1/admin/ops/commands/catalogue");
    const key = useMemo(() => newIdempotencyKey(command), [command]);
    const [view, setView] = useState<OpsView | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!view || FINAL.has(view.state) || view.state === "awaiting_approval") return;
        const timer = window.setTimeout(async () => {
            const r = await staffGet<OpsView>(`/api/v1/admin/ops/commands/${view.id}`);
            if (r.ok) setView(r.data);
        }, 2000);
        return () => window.clearTimeout(timer);
    }, [view]);

    if (catalogue.state === "loading") return null;
    if (catalogue.state !== "ok" && catalogue.state !== "stale") {
        return (
            <p className="flex items-center gap-2 text-xs text-muted-foreground" data-testid="ops-needs-setup">
                <Wrench aria-hidden className="h-3.5 w-3.5" /> {command}: needs setup (the ops console is not on this build).
            </p>
        );
    }
    const spec = catalogue.data?.commands.find((c) => c.name === command);
    if (!spec) return <p className="text-xs text-muted-foreground">{command} is not offered by the ops console here.</p>;

    const state: CommandState = busy ? "submitting" : error ? "failed" : !view ? "ready" : view.state === "succeeded" ? "succeeded" : FINAL.has(view.state) ? "failed" : "accepted";
    return (
        <div className="space-y-2">
            <CommandPreview
                command={{
                    command,
                    role: me.roles.join(", "),
                    environment: me.environment,
                    target: targetLabel,
                    idempotencyKey: key,
                    effect: effect ?? (spec.requires_approval ? `${spec.summary} Needs a second person.` : spec.summary),
                }}
                state={state}
                result={error ?? (view && FINAL.has(view.state) ? `${words(view.state)}${view.reason_code ? `: ${view.reason_code}` : ""}` : undefined)}
                onConfirm={async (reason) => {
                    setBusy(true);
                    setError(null);
                    const r = await staffPost<OpsView>("/api/v1/admin/ops/commands", { command, target, reason, idempotency_key: key, environment: me.environment });
                    setBusy(false);
                    if (r.ok) setView(r.data);
                    else setError(r.error);
                }}
            />
            {view && !FINAL.has(view.state) && (
                <p role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Clock aria-hidden className="h-3.5 w-3.5" /> Ops command #{view.id} is {words(view.state).toLowerCase()}.
                </p>
            )}
            {view?.state === "outcome_unknown" && (
                <p role="alert" className="flex items-center gap-1.5 text-xs text-[#705500]">
                    <AlertTriangle aria-hidden className="h-3.5 w-3.5" /> We are checking whether this happened. Do not run it again.
                </p>
            )}
        </div>
    );
}
