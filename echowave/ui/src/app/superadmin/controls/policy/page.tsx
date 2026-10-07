"use client";

import { useState } from "react";

import { OpsCommand } from "@/components/staff/OpsCommand";
import { Field, Fields, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useStaffData } from "@/lib/staff/data";
import { count, money, words } from "@/lib/staff/format";

type Flag = {
    name: string;
    description: string;
    setting: string;
    global_enabled: boolean;
    global_source: string;
    environment_enabled: boolean;
    overrides: Array<{ organization_id: number; enabled: boolean; organization?: string | null }>;
};
type Policy = {
    flags: Flag[];
    billing: { free_mode: boolean; note: string };
    operational_budgets: {
        state: string;
        reason: string | null;
        per_person_daily: Array<{ kind: string; per_person_per_day: number; setting: string }>;
        live_grants: number;
        pilot_total: { state: string; limit_paise: number | null; setting: string };
        pilot_capacity: { state: string; people: number | null; setting: string };
        workspace_budget_incidents_open: number;
        cost_stop: string;
        applies: string;
    };
    model_policy: { routing: string; routing_setting: string; kinds: Array<{ kind: string; description: string; preset: string; tier: string | null }>; calls: string; fallback: string; applies: string };
    change: Record<string, string>;
};

/**
 * Flags, budgets and model policy (screen 43). Versioned rows with where
 * each value comes from. A change opens current and proposed side by side
 * with the affected scope, and goes through the ops stream's `flag.set`
 * (versioned, with rollback). Free billing and operational budgets are
 * separate groups: a zero price never means unlimited work.
 */
export default function PolicyPage() {
    const { can } = useStaffConsole();
    const query = useStaffData<Policy>("/api/v1/admin/staff/policy");
    const [filter, setFilter] = useState("");
    const [editing, setEditing] = useState<Flag | null>(null);
    const [scope, setScope] = useState<string>("");
    useReportFreshness(query.state, query.refreshedAt);

    return (
        <div className="space-y-4">
            <PageHeader title="Flags, budgets and model policy" description="Read here; changed through approved, versioned commands." />
            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Billing" query={query}>
                    {(p) => (
                        <Fields>
                            <Field label="Free mode">{p.billing.free_mode ? "On: nothing is charged" : "Off: the rate card charges"}</Field>
                            <Field label="Note">{p.billing.note}</Field>
                        </Fields>
                    )}
                </Panel>
                <Panel title="Operational budgets" query={query}>
                    {(p) => (
                        <div className="space-y-2 text-sm">
                            <p className="flex items-center gap-2">
                                Daily limits <StateBadge state={p.operational_budgets.state} /> <span className="text-xs text-muted-foreground">{p.operational_budgets.reason}</span>
                            </p>
                            <ul className="divide-y divide-border">
                                {p.operational_budgets.per_person_daily.map((a) => (
                                    <li key={a.kind} className="flex justify-between py-1">
                                        <span>{words(a.kind)}</span>
                                        <span className="tabular-nums">
                                            {count(a.per_person_per_day)}/person/day <span className="font-mono text-xs text-muted-foreground">{a.setting}</span>
                                        </span>
                                    </li>
                                ))}
                            </ul>
                            <p>Live temporary grants: {count(p.operational_budgets.live_grants)}</p>
                            <p className="flex flex-wrap items-center gap-2">
                                Pilot total budget{" "}
                                {p.operational_budgets.pilot_total.state === "ok" ? money(p.operational_budgets.pilot_total.limit_paise) : <StateBadge state="needs_setup" label={`Needs setup (${p.operational_budgets.pilot_total.setting})`} />}
                            </p>
                            <p className="flex flex-wrap items-center gap-2">
                                Pilot capacity{" "}
                                {p.operational_budgets.pilot_capacity.state === "ok" ? `${count(p.operational_budgets.pilot_capacity.people)} people` : <StateBadge state="needs_setup" label={`Needs setup (${p.operational_budgets.pilot_capacity.setting})`} />}
                            </p>
                            <p>Workspace budget alerts open: {count(p.operational_budgets.workspace_budget_incidents_open)}</p>
                            <p className="text-xs text-muted-foreground">{p.operational_budgets.cost_stop}</p>
                            <p className="text-xs text-muted-foreground">{p.operational_budgets.applies}</p>
                        </div>
                    )}
                </Panel>
            </div>

            <Panel title="Model policy (Auto)" query={query}>
                {(p) => (
                    <div className="space-y-2 text-sm">
                        <p>
                            Routing: <strong>{p.model_policy.routing}</strong> <span className="font-mono text-xs text-muted-foreground">{p.model_policy.routing_setting}</span>
                        </p>
                        <ul className="divide-y divide-border">
                            {p.model_policy.kinds.map((k) => (
                                <li key={k.kind} className="grid gap-1 py-1.5 sm:grid-cols-[6rem_1fr_8rem]">
                                    <span className="font-medium">{words(k.kind)}</span>
                                    <span className="text-xs text-muted-foreground">{k.description}</span>
                                    <span>
                                        {words(k.preset)} <span className="text-xs text-muted-foreground">({k.tier ?? "unknown tier"})</span>
                                    </span>
                                </li>
                            ))}
                        </ul>
                        <p className="text-xs text-muted-foreground">
                            {p.model_policy.calls} {p.model_policy.fallback} Applies: {p.model_policy.applies}
                        </p>
                    </div>
                )}
            </Panel>

            <Panel title="Feature flags" query={query}>
                {(p) => {
                    const rows = p.flags.filter((f) => !filter || f.name.includes(filter.toLowerCase()) || f.description.toLowerCase().includes(filter.toLowerCase()));
                    return (
                        <div className="space-y-3">
                            <label className="flex max-w-sm flex-col gap-1 text-sm">
                                Find a flag
                                <Input value={filter} onChange={(e) => setFilter(e.target.value)} className="min-h-11 text-base md:min-h-9 md:text-sm" />
                            </label>
                            <TableRegion label="Feature flags">
                                <table className="w-full min-w-[560px] text-sm">
                                    <thead className="text-left text-xs text-muted-foreground">
                                        <tr>
                                            <th className="py-1 font-normal">Flag</th>
                                            <th className="py-1 font-normal">Everyone</th>
                                            <th className="py-1 font-normal">From</th>
                                            <th className="py-1 text-right font-normal">Workspaces</th>
                                            <th className="py-1 font-normal">
                                                <span className="sr-only">Change</span>
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rows.map((f) => (
                                            <tr key={f.name} className="border-t border-border">
                                                <td className="py-1">
                                                    <span className="font-mono text-xs">{f.name}</span>
                                                    <span className="block text-xs text-muted-foreground">{f.description}</span>
                                                </td>
                                                <td className="py-1">
                                                    <StateBadge state={f.global_enabled ? "active" : "disabled_by_policy"} label={f.global_enabled ? "On" : "Off"} />
                                                </td>
                                                <td className="py-1 text-xs">{words(f.global_source)}</td>
                                                <td className="py-1 text-right tabular-nums">{f.overrides.length}</td>
                                                <td className="py-1">
                                                    {can("policy.change") && (
                                                        <Button size="sm" variant="outline" className="min-h-11 md:min-h-8" onClick={() => { setEditing(f); setScope(""); }}>
                                                            Change…
                                                        </Button>
                                                    )}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </TableRegion>
                        </div>
                    );
                }}
            </Panel>

            {editing && (
                <section className="space-y-3 rounded-lg border border-border p-4" aria-label={`Change ${editing.name}`}>
                    <h2 className="text-base font-semibold">
                        Change <span className="font-mono">{editing.name}</span>
                    </h2>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <div className="rounded-md border border-border p-3 text-sm">
                            <p className="text-xs text-muted-foreground">Current</p>
                            <p>Everyone: {editing.global_enabled ? "On" : "Off"} ({words(editing.global_source)})</p>
                            <p>Workspace overrides: {editing.overrides.length}</p>
                        </div>
                        <div className="rounded-md border border-border p-3 text-sm">
                            <p className="text-xs text-muted-foreground">Proposed</p>
                            <p>
                                {scope ? `Workspace ${scope}` : "Everyone"}: {editing.global_enabled && !scope ? "Off" : "On"}
                            </p>
                            <label className="mt-2 flex flex-col gap-1">
                                Only for workspace id (empty for everyone)
                                <input inputMode="numeric" value={scope} onChange={(e) => setScope(e.target.value.replace(/\D/g, ""))} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm" />
                            </label>
                        </div>
                    </div>
                    <OpsCommand
                        key={`${editing.name}-${scope}`}
                        command="flag.set"
                        target={{ feature: editing.name, enabled: scope ? true : !editing.global_enabled, ...(scope ? { organization_id: Number(scope) } : {}) }}
                        targetLabel={`${editing.name} for ${scope ? `workspace ${scope}` : "everyone"}`}
                        effect="Versioned: flag.rollback restores the exact earlier value. Running calls keep their configuration until their next session."
                    />
                    <Button variant="ghost" className="min-h-11 md:min-h-8" onClick={() => setEditing(null)}>
                        Close
                    </Button>
                </section>
            )}
        </div>
    );
}
