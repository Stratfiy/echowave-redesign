"use client";

import { useState } from "react";

import { OpsCommand } from "@/components/staff/OpsCommand";
import { Empty, Field, Fields, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { count, money, when, words } from "@/lib/staff/format";

type Laya = {
    shadow: {
        days: number;
        readable: boolean;
        total: number;
        agreement: number | null;
        abstention: number | null;
        breaker: {
            guardrails: boolean;
            rolled_back: boolean;
            open: boolean;
            open_for_seconds: number;
            consecutive_failures: number;
            times_opened: number;
            routing: string;
            hard_deadline_ms: number;
        };
    };
    latest_evaluation: { outcome?: string; summary?: string; created_at?: string } | null;
};

type Stop = { scope: string; organization_id: number | null; engaged_at: string; reason: string; engaged_by: string; expires_at: string | null };
type CostStop = {
    enabled: boolean;
    configured: boolean;
    platform_hourly_paise: number | null;
    organization_hourly_paise: number | null;
    platform: Stop | null;
    organizations: Stop[];
    readable: boolean;
};

type OpsCommandRow = { id: number; command: string; target: Record<string, unknown>; state: string; reason: string; created_at: string; requested_by: number | null };

type Pending =
    | { kind: "laya.rollback" }
    | { kind: "laya.restore" }
    | { kind: "cost_stop.engage" | "cost_stop.release"; scope: "platform" | "organization"; organizationId: string }
    | { kind: "flag.rollback"; commandId: number; label: string }
    | { kind: "provider.resume"; component: string; provider: string };

/**
 * Model routing, cost stop and rollbacks (screen 43, "model policy ...
 * Laya routing, rollback and cost stop"). Everything here is an ops-stream
 * command with a preview, a reason, an approval where the command needs
 * one, and a result; the console never switches anything directly.
 */
export default function RoutingPage() {
    const { can } = useStaffConsole();
    const laya = useStaffData<Laya>("/api/v1/admin/ops/laya", undefined, 30_000);
    const stop = useStaffData<CostStop>("/api/v1/admin/ops/cost-stop", undefined, 30_000);
    const history = useStaffData<{ commands: OpsCommandRow[] }>("/api/v1/admin/ops/commands", { limit: 100 });
    const [pending, setPending] = useState<Pending | null>(null);
    useReportFreshness(laya.state, laya.refreshedAt);
    const act = can("operations.act") || can("policy.change");

    const flagChanges = (history.data?.commands ?? []).filter((c) => c.command === "flag.set" && c.state === "succeeded");
    const providerPauses = (history.data?.commands ?? []).filter((c) => c.command === "provider.pause" && c.state === "succeeded");

    return (
        <div className="space-y-4">
            <PageHeader
                title="Routing, cost stop and rollbacks"
                description="Laya routing and its guardrails, the cost stop, and the way back from a flag change or a provider pause. Each change is a typed command."
            />
            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Laya routing" query={laya} setupHint="The ops console (ops_console) is not on here.">
                    {(l) => (
                        <div className="space-y-3 text-sm" data-testid="laya-panel">
                            <Fields>
                                <Field label="Routing">{words(l.shadow.breaker.routing)}</Field>
                                <Field label="Rolled back">
                                    <StateBadge state={l.shadow.breaker.rolled_back ? "paused" : "active"} label={l.shadow.breaker.rolled_back ? "Yes: rules only, Laya not asked" : "No"} />
                                </Field>
                                <Field label="Guardrails">{l.shadow.breaker.guardrails ? `On, ${l.shadow.breaker.hard_deadline_ms} ms deadline` : "Off"}</Field>
                                <Field label="Breaker">
                                    <StateBadge
                                        state={l.shadow.breaker.open ? "degraded" : "ok"}
                                        label={l.shadow.breaker.open ? `Open for ${Math.round(l.shadow.breaker.open_for_seconds)} s` : `Closed (${count(l.shadow.breaker.times_opened)} openings)`}
                                    />
                                </Field>
                                <Field label={`Shadow, ${l.shadow.days} days`}>
                                    {!l.shadow.readable
                                        ? "Unknown: the counters could not be read"
                                        : l.shadow.total === 0
                                          ? "No decisions compared yet"
                                          : `${count(l.shadow.total)} decisions; agreement ${l.shadow.agreement === null ? "unknown" : `${Math.round(l.shadow.agreement * 100)}%`}`}
                                </Field>
                                <Field label="Latest evaluation">
                                    {l.latest_evaluation ? `${words(l.latest_evaluation.outcome ?? "recorded")} ${when(l.latest_evaluation.created_at ?? null)}` : "None recorded"}
                                </Field>
                            </Fields>
                            {act && (
                                <div className="flex flex-wrap gap-2">
                                    <Button variant="destructive" className="min-h-11 md:min-h-9" onClick={() => setPending({ kind: "laya.rollback" })}>
                                        Roll back to rules
                                    </Button>
                                    <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setPending({ kind: "laya.restore" })}>
                                        Let Auto ask Laya again
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}
                </Panel>

                <Panel title="Cost stop" query={stop} setupHint="The ops console (ops_console) is not on here.">
                    {(c) => (
                        <div className="space-y-3 text-sm" data-testid="cost-stop-panel">
                            <Fields>
                                <Field label="Switch">{c.enabled ? "On (cost_stop)" : "Off: engaged stops are ignored"}</Field>
                                <Field label="Platform ceiling">{c.platform_hourly_paise === null ? "Not set: not monitored" : `${money(c.platform_hourly_paise)} per hour`}</Field>
                                <Field label="Workspace ceiling">{c.organization_hourly_paise === null ? "Not set: not monitored" : `${money(c.organization_hourly_paise)} per hour`}</Field>
                                <Field label="Platform">
                                    {c.platform ? <StateBadge state="paused" label={`Engaged ${when(c.platform.engaged_at)} (${c.platform.engaged_by === "auto" ? "automatic" : "by staff"})`} /> : <StateBadge state="ok" label="Not engaged" />}
                                </Field>
                                <Field label="Workspaces stopped">{c.readable ? count(c.organizations.length) : "Unknown: Redis could not be read"}</Field>
                            </Fields>
                            {c.organizations.length > 0 && (
                                <ul className="divide-y divide-border">
                                    {c.organizations.map((o) => (
                                        <li key={o.organization_id ?? 0} className="flex flex-wrap items-center gap-2 py-1.5">
                                            <span>Workspace {o.organization_id}</span>
                                            <span className="text-xs text-muted-foreground">{o.engaged_by === "auto" ? "automatic" : "by staff"} · {when(o.engaged_at)}</span>
                                            {act && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="ml-auto min-h-11 md:min-h-8"
                                                    onClick={() => setPending({ kind: "cost_stop.release", scope: "organization", organizationId: String(o.organization_id) })}
                                                >
                                                    Release…
                                                </Button>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            )}
                            {act && (
                                <div className="flex flex-wrap gap-2">
                                    <Button variant="destructive" className="min-h-11 md:min-h-9" onClick={() => setPending({ kind: "cost_stop.engage", scope: "platform", organizationId: "" })}>
                                        Stop new billable work…
                                    </Button>
                                    {c.platform && (
                                        <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setPending({ kind: "cost_stop.release", scope: "platform", organizationId: "" })}>
                                            Release the platform stop…
                                        </Button>
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                </Panel>
            </div>

            <Panel title="Flag changes you can roll back" query={history} setupHint="The ops console (ops_console) is not on here.">
                {() =>
                    flagChanges.length === 0 ? (
                        <Empty>No flag has been changed through a command yet.</Empty>
                    ) : (
                        <TableRegion label="Flag changes">
                            <table className="w-full min-w-[560px] text-sm">
                                <thead>
                                    <tr className="text-left text-xs text-muted-foreground">
                                        <th className="py-1 font-normal">Command</th>
                                        <th className="py-1 font-normal">Change</th>
                                        <th className="py-1 font-normal">Reason</th>
                                        <th className="py-1 font-normal">When</th>
                                        <th className="py-1 font-normal">
                                            <span className="sr-only">Action</span>
                                        </th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-border">
                                    {flagChanges.map((c) => {
                                        const label = `${String(c.target.feature)} ${c.target.enabled ? "on" : "off"} for ${c.target.organization_id ? `workspace ${c.target.organization_id}` : "everyone"}`;
                                        return (
                                            <tr key={c.id}>
                                                <td className="py-1 font-mono text-xs">#{c.id}</td>
                                                <td className="py-1">{label}</td>
                                                <td className="py-1 text-xs text-muted-foreground">{c.reason}</td>
                                                <td className="py-1 text-xs">{when(c.created_at)}</td>
                                                <td className="py-1 text-right">
                                                    {act && (
                                                        <Button size="sm" variant="outline" className="min-h-11 md:min-h-8" onClick={() => setPending({ kind: "flag.rollback", commandId: c.id, label })}>
                                                            Roll back…
                                                        </Button>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </TableRegion>
                    )
                }
            </Panel>

            <Panel title="Paused providers" query={history} setupHint="The ops console (ops_console) is not on here.">
                {() =>
                    providerPauses.length === 0 ? (
                        <Empty>No provider has been paused through a command.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm">
                            {providerPauses.map((c) => (
                                <li key={c.id} className="flex flex-wrap items-center gap-2 py-1.5">
                                    <span className="font-mono text-xs">#{c.id}</span>
                                    <span>
                                        {String(c.target.component)} / {String(c.target.provider)}
                                    </span>
                                    <span className="text-xs text-muted-foreground">{when(c.created_at)}</span>
                                    {act && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            className="ml-auto min-h-11 md:min-h-8"
                                            onClick={() => setPending({ kind: "provider.resume", component: String(c.target.component), provider: String(c.target.provider) })}
                                        >
                                            Resume…
                                        </Button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>

            {pending && (
                <section className="space-y-3 rounded-lg border border-border p-4" aria-label="Command">
                    {(pending.kind === "cost_stop.engage" || pending.kind === "cost_stop.release") && (
                        <div className="flex flex-wrap gap-3 text-sm">
                            <label className="flex flex-col gap-1">
                                Scope
                                <select
                                    value={pending.scope}
                                    onChange={(e) => setPending({ ...pending, scope: e.target.value as "platform" | "organization" })}
                                    className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                                >
                                    <option value="platform">The whole platform</option>
                                    <option value="organization">One workspace</option>
                                </select>
                            </label>
                            {pending.scope === "organization" && (
                                <label className="flex flex-col gap-1">
                                    Workspace id
                                    <input
                                        inputMode="numeric"
                                        value={pending.organizationId}
                                        onChange={(e) => setPending({ ...pending, organizationId: e.target.value.replace(/\D/g, "") })}
                                        className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                                    />
                                </label>
                            )}
                        </div>
                    )}
                    <OpsCommand key={JSON.stringify(pending)} {...commandFor(pending)} />
                    <Button variant="ghost" className="min-h-11 md:min-h-8" onClick={() => setPending(null)}>
                        Close
                    </Button>
                </section>
            )}
        </div>
    );
}

function commandFor(p: Pending): { command: string; target: Record<string, unknown>; targetLabel: string; effect: string } {
    switch (p.kind) {
        case "laya.rollback":
            return { command: p.kind, target: {}, targetLabel: "Auto routing, every worker", effect: "Rules only: Laya is not asked again until restored. Applies to the next message on every worker within seconds." };
        case "laya.restore":
            return { command: p.kind, target: {}, targetLabel: "Auto routing, every worker", effect: "Auto asks Laya again as LAYA_ROUTING says. Needs a second person's approval." };
        case "cost_stop.engage":
        case "cost_stop.release": {
            const org = p.scope === "organization" ? Number(p.organizationId) || null : null;
            return {
                command: p.kind,
                target: { scope: p.scope, ...(org ? { organization_id: org } : {}) },
                targetLabel: p.scope === "platform" ? "The whole platform" : `Workspace ${p.organizationId || "?"}`,
                effect:
                    p.kind === "cost_stop.engage"
                        ? "New billable runs are refused with cost_stopped. Running calls and tasks finish."
                        : "New billable work is allowed again. Needs a second person's approval.",
            };
        }
        case "flag.rollback":
            return { command: p.kind, target: { command_id: p.commandId }, targetLabel: `Undo #${p.commandId}: ${p.label}`, effect: "Restores the exact value before that change, not a guessed default." };
        case "provider.resume":
            return { command: p.kind, target: { component: p.component, provider: p.provider }, targetLabel: `${p.component} / ${p.provider}`, effect: "The provider is offered again." };
    }
}
