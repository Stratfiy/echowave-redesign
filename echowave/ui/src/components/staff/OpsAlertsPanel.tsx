"use client";

/**
 * Operational alerts (services/ops_alerts) on the Operations screen: what is
 * open now, what resolved lately, when each watched tick last completed, and
 * the last seven daily cost summaries. Read-only: the alerts arrive by email;
 * this is where to look when one does.
 */

import { Empty, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useStaffData } from "@/lib/staff/data";
import { count, money, when } from "@/lib/staff/format";

export type OpsIncident = {
    key: string;
    title: string;
    detail: string;
    severity: string;
    opened_at: string;
    last_seen_at?: string | null;
    resolved_at?: string | null;
    notified?: boolean;
};

export type OpsAlerts = {
    observed_at: string;
    open: OpsIncident[];
    resolved: OpsIncident[];
    ticks: Array<{ name: string; label: string; last_completed: string | null }>;
};

type SummaryLine = { key: string; label: string; unit: string; units: number; cost_paise: number; unpriced_units: number; no_rate: boolean };

export type DailySummary = {
    day: string;
    total_paise: number;
    average_paise: number;
    delta_pct: number | null;
    lines: SummaryLine[];
    top_organizations: Array<{ organization_id: number; name: string; cost_paise: number }>;
    top_agents: Array<{ agent_id: number; name: string; organization: string | null; cost_paise: number }>;
    calls: { finished: number; answered: number; not_connected: number; carrier_failed: number; unknown: number; in_progress: number };
    active_organizations: number;
    mailed_at: string | null;
};

function usage(line: SummaryLine): string {
    if (line.unit === "seconds") return `${count(Math.round(line.units / 60))} min`;
    return `${count(Math.round(line.units))} ${line.unit}`;
}

function Incident({ incident }: { incident: OpsIncident }) {
    return (
        <li className="space-y-1 py-2" data-testid={`ops-alert-${incident.key}`}>
            <div className="flex flex-wrap items-center gap-2">
                <StateBadge state={incident.resolved_at ? "resolved" : incident.severity} />
                <span className="font-medium">{incident.title}</span>
                <span className="ml-auto text-xs text-muted-foreground">
                    {incident.resolved_at ? `resolved ${when(incident.resolved_at)}` : `since ${when(incident.opened_at)}`}
                </span>
            </div>
            {!incident.resolved_at && <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">{incident.detail}</p>}
        </li>
    );
}

export function OpsAlertsPanel() {
    const alerts = useStaffData<OpsAlerts>("/api/v1/admin/ops/alerts", undefined, 60_000);
    const summaries = useStaffData<{ summaries: DailySummary[] }>("/api/v1/admin/ops/alerts/daily-summaries");

    return (
        <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Open alerts" query={alerts} setupHint="Operational alerts (ops_alerts) are switched off.">
                {(d) =>
                    d.open.length === 0 ? (
                        <Empty>Nothing open. Read {when(d.observed_at)}.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm">
                            {d.open.map((i) => (
                                <Incident key={i.key} incident={i} />
                            ))}
                        </ul>
                    )
                }
            </Panel>
            <Panel title="Scheduled ticks" query={alerts}>
                {(d) => (
                    <ul className="space-y-1 text-sm">
                        {d.ticks.map((tick) => (
                            <li key={tick.name} className="flex items-center justify-between gap-2">
                                <span>{tick.label}</span>
                                <span className="text-xs text-muted-foreground">{tick.last_completed ? `last completed ${when(tick.last_completed)}` : "no completion on record"}</span>
                            </li>
                        ))}
                    </ul>
                )}
            </Panel>
            <Panel title="Recently resolved" query={alerts} className="lg:col-span-2">
                {(d) =>
                    d.resolved.length === 0 ? (
                        <Empty>Nothing resolved recently.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm">
                            {d.resolved.map((i) => (
                                <Incident key={`${i.key}-${i.resolved_at}`} incident={i} />
                            ))}
                        </ul>
                    )
                }
            </Panel>
            <Panel title="Daily cost summaries" query={summaries} className="lg:col-span-2">
                {(d) =>
                    d.summaries.length === 0 ? (
                        <Empty>No summaries yet.</Empty>
                    ) : (
                        <ul className="space-y-4 text-sm">
                            {d.summaries.map((s) => (
                                <li key={s.day} className="rounded-md border border-border p-3" data-testid={`ops-summary-${s.day}`}>
                                    <div className="flex flex-wrap items-baseline gap-2">
                                        <span className="font-medium">{s.day}</span>
                                        <span className="tabular-nums">{money(s.total_paise)}</span>
                                        <span className="text-xs text-muted-foreground">
                                            7-day average {money(s.average_paise)}
                                            {s.delta_pct !== null && ` (${s.delta_pct >= 0 ? "+" : ""}${s.delta_pct.toFixed(0)}%)`}
                                        </span>
                                        <span className="ml-auto text-xs text-muted-foreground">{s.mailed_at ? `mailed ${when(s.mailed_at)}` : "not mailed"}</span>
                                    </div>
                                    <TableRegion label={`Spend by component, ${s.day}`}>
                                        <table className="mt-2 w-full min-w-[420px] text-xs">
                                            <tbody>
                                                {s.lines.map((line) => (
                                                    <tr key={line.key} className="border-t border-border">
                                                        <td className="py-1">{line.label}</td>
                                                        <td className="py-1 text-right tabular-nums">{usage(line)}</td>
                                                        <td className="py-1 text-right tabular-nums">
                                                            {line.no_rate && line.cost_paise === 0 ? "no rate" : money(line.cost_paise)}
                                                            {line.no_rate && line.cost_paise > 0 && " + no rate"}
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </TableRegion>
                                    <p className="mt-2 text-xs text-muted-foreground">
                                        Top workspaces: {s.top_organizations.map((o) => `${o.name} ${money(o.cost_paise)}`).join(" · ") || "none"}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        Top agents: {s.top_agents.map((a) => `${a.name} ${money(a.cost_paise)}`).join(" · ") || "none"}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        Calls {count(s.calls.finished)} finished: {count(s.calls.answered)} answered, {count(s.calls.carrier_failed)} failed at the carrier, {count(s.calls.unknown)} no outcome · active workspaces{" "}
                                        {count(s.active_organizations)}
                                    </p>
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>
        </div>
    );
}
