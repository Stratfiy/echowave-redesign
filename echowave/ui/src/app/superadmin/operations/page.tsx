"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { OpsCommand } from "@/components/staff/OpsCommand";
import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { count, when, words } from "@/lib/staff/format";

type Ops = {
    jobs: { window_hours: number; by_state: Record<string, number>; failing: Array<{ task_id: number; organization_id: number; state: string; kind: string; created_at: string | null; finished_at: string | null }> };
    delivery: { by_status: Record<string, number>; dead_letter: Array<{ delivery_id: number; organization_id: number; workflow_run_id: number | null; attempts: number; last_status_code: number | null; at: string | null }>; retry: string };
    infrastructure: { state: string; signals: Array<{ name: string; state: string; detail?: string | null }>; build?: Record<string, string> | null; reason?: string };
    calls: {
        observed_at: string;
        live: number;
        connecting: number;
        by_channel: Array<{ mode: string; live: number }>;
        longest_minutes: number | null;
        possibly_stuck: Array<{ workflow_run_id: number; organization_id: number; campaign_id: number | null; mode: string; direction: string; state: string; age_minutes: number | null; reason: string }>;
        thresholds: { long_call_minutes: number; not_connected_minutes: number };
    };
    providers: Array<{ provider: string; status: string; kind: string; remaining: number | null; currency: string | null; needs_attention: boolean; detail: string | null }>;
};

const TABS = ["jobs", "calls", "providers", "delivery", "infrastructure"] as const;
type Tab = (typeof TABS)[number];

function OperationsInner() {
    const router = useRouter();
    const params = useSearchParams();
    const tab = (TABS as readonly string[]).includes(params?.get("tab") ?? "") ? (params?.get("tab") as Tab) : "jobs";
    const { can, me } = useStaffConsole();
    const query = useStaffData<Ops>("/api/v1/admin/staff/operations", undefined, 60_000);
    const opsHealth = useStaffData<{ status: string; signals: Array<{ name: string; status: string; detail: string; metrics?: Record<string, unknown> }> }>("/api/v1/admin/ops/health");
    const owner = me.roles.includes("owner");
    const [act, setAct] = useState<{ command: string; target: Record<string, unknown>; label: string } | null>(null);
    useReportFreshness(query.state, query.refreshedAt);

    return (
        <div className="space-y-4">
            <PageHeader title="Operations and delivery" description="Actionable failures first. Monitoring that did not answer is shown as unknown, never as healthy." />
            <div role="tablist" aria-label="Operations sections" className="flex flex-wrap gap-1 border-b border-border">
                {TABS.map((t) => (
                    <button
                        key={t}
                        role="tab"
                        type="button"
                        aria-selected={tab === t}
                        onClick={() => router.replace(`/superadmin/operations?tab=${t}`)}
                        className={`motion-m1 min-h-11 border-b-2 px-3 text-sm md:min-h-9 ${tab === t ? "border-foreground font-medium" : "border-transparent text-muted-foreground"}`}
                    >
                        {words(t)}
                    </button>
                ))}
            </div>

            {tab === "jobs" && (
                <Panel title="Jobs that need a person" query={query}>
                    {(d) => (
                        <div className="space-y-3">
                            <p className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                {Object.entries(d.jobs.by_state).map(([s, n]) => (
                                    <span key={s}>
                                        {words(s)}: {count(n)}
                                    </span>
                                ))}
                                {Object.keys(d.jobs.by_state).length === 0 && <span>No ledger tasks in the last {d.jobs.window_hours} h.</span>}
                            </p>
                            {d.jobs.failing.length === 0 ? (
                                <Empty>Nothing failed, unknown or waiting on input in the last {d.jobs.window_hours} hours.</Empty>
                            ) : (
                                <ul className="divide-y divide-border text-sm">
                                    {d.jobs.failing.map((j) => (
                                        <li key={j.task_id} className="flex flex-wrap items-center gap-2 py-2">
                                            <Link className="min-h-11 font-mono text-xs underline underline-offset-2 md:min-h-0" href={`/superadmin/operations/trace/${j.task_id}`}>
                                                Task #{j.task_id}
                                            </Link>
                                            <StateBadge state={j.state} />
                                            <span className="text-xs text-muted-foreground">workspace {j.organization_id} · {words(j.kind)}</span>
                                            <span className="ml-auto text-xs">{when(j.finished_at ?? j.created_at)}</span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}
                </Panel>
            )}

            {tab === "providers" && (
                <Panel title="Providers" query={query}>
                    {(d) =>
                        d.providers.length === 0 ? (
                            <Empty>No provider balances are reported here.</Empty>
                        ) : (
                            <ul className="divide-y divide-border text-sm">
                                {d.providers.map((p) => (
                                    <li key={`${p.provider}-${p.kind}`} className="flex flex-wrap items-center gap-2 py-2">
                                        <span className="font-medium">{p.provider}</span>
                                        <StateBadge state={p.needs_attention ? "degraded" : p.status === "ok" ? "healthy" : p.status} label={p.needs_attention ? "Needs attention" : words(p.status)} />
                                        <span className="text-xs text-muted-foreground">{p.detail}</span>
                                        {can("operations.act") && (
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="ml-auto min-h-11 md:min-h-8"
                                                onClick={() => setAct({ command: "provider.pause", target: { component: "llm", provider: p.provider.toLowerCase() }, label: `${p.provider} (llm)` })}
                                            >
                                                Pause…
                                            </Button>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )
                    }
                </Panel>
            )}

            {tab === "delivery" && (
                <Panel title="Webhook delivery" query={query}>
                    {(d) => (
                        <div className="space-y-3">
                            <p className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                {Object.entries(d.delivery.by_status).map(([s, n]) => (
                                    <span key={s}>
                                        {words(s)}: {count(n)}
                                    </span>
                                ))}
                            </p>
                            {d.delivery.dead_letter.length === 0 ? (
                                <Empty>No deliveries were given up on in this window.</Empty>
                            ) : (
                                <TableRegion label="Dead-lettered deliveries">
                                    <table className="w-full min-w-[520px] text-sm">
                                        <thead className="text-left text-xs text-muted-foreground">
                                            <tr>
                                                <th className="py-1 font-normal">Delivery</th>
                                                <th className="py-1 font-normal">Workspace</th>
                                                <th className="py-1 text-right font-normal">Attempts</th>
                                                <th className="py-1 text-right font-normal">Last status</th>
                                                <th className="py-1 font-normal">When</th>
                                                <th className="py-1 font-normal">
                                                    <span className="sr-only">Action</span>
                                                </th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {d.delivery.dead_letter.map((x) => (
                                                <tr key={x.delivery_id} className="border-t border-border tabular-nums">
                                                    <td className="py-1">#{x.delivery_id}</td>
                                                    <td className="py-1">{x.organization_id}</td>
                                                    <td className="py-1 text-right">{x.attempts}</td>
                                                    <td className="py-1 text-right">{x.last_status_code ?? "none"}</td>
                                                    <td className="py-1 text-xs">{when(x.at)}</td>
                                                    <td className="py-1">
                                                        {can("operations.act") && (
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                className="min-h-11 md:min-h-8"
                                                                onClick={() => setAct({ command: "delivery.retry", target: { organization_id: x.organization_id, delivery_id: x.delivery_id }, label: `delivery #${x.delivery_id} (workspace ${x.organization_id})` })}
                                                            >
                                                                Retry…
                                                            </Button>
                                                        )}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </TableRegion>
                            )}
                        </div>
                    )}
                </Panel>
            )}

            {tab === "infrastructure" && (
                <div className="grid gap-4 lg:grid-cols-2">
                    <Panel title="Platform probes" query={query}>
                        {(d) => (
                            <div className="space-y-2 text-sm">
                                <p className="flex items-center gap-2">
                                    Overall <StateBadge state={d.infrastructure.state} />
                                </p>
                                <ul className="space-y-1">
                                    {d.infrastructure.signals.map((s) => (
                                        <li key={s.name} className="flex items-center justify-between gap-2">
                                            <span>{words(s.name)}</span>
                                            <StateBadge state={s.state} />
                                        </li>
                                    ))}
                                </ul>
                                {d.infrastructure.build && <p className="font-mono text-xs text-muted-foreground">{Object.values(d.infrastructure.build).filter(Boolean).join(" · ")}</p>}
                            </div>
                        )}
                    </Panel>
                    <Panel title="Ops health (workers, queues, backups, drills)" query={opsHealth} setupHint="The ops stream's health service (/admin/ops/health) is not on this build yet.">
                        {(h) => (
                            <ul className="space-y-1 text-sm">
                                <li className="flex items-center justify-between">
                                    Overall <StateBadge state={h.status === "ok" ? "healthy" : h.status} />
                                </li>
                                {h.signals.map((s) => (
                                    <li key={s.name} className="border-b border-border py-1 last:border-0" data-testid={`ops-signal-${s.name}`}>
                                        <div className="flex items-center justify-between gap-2">
                                            <span>{words(s.name)}</span>
                                            <StateBadge state={s.status === "ok" ? "healthy" : s.status} />
                                        </div>
                                        {s.detail && <p className="text-xs text-muted-foreground">{s.detail}</p>}
                                        {s.name === "analytics_outbox" && s.metrics && (
                                            <p className="text-xs tabular-nums">
                                                Waiting {count(Number(s.metrics.pending ?? 0))} · gave up {count(Number(s.metrics.stuck ?? 0))} · oldest{" "}
                                                {s.metrics.oldest_age_seconds === null || s.metrics.oldest_age_seconds === undefined
                                                    ? "none waiting"
                                                    : `${Math.round(Number(s.metrics.oldest_age_seconds) / 60)} min`}
                                            </p>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Panel>
                    {can("operations.act") && (
                        <section className="rounded-lg border border-border p-4 lg:col-span-2" aria-label="Runbook commands">
                            <h2 className="mb-2 text-base font-semibold">Approved runbook commands</h2>
                            <p className="mb-3 text-sm text-muted-foreground">Drain and restart run through the ops console&apos;s approved runbooks; nothing here is a terminal.</p>
                            <div className="flex flex-wrap gap-2">
                                <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setAct({ command: "workers.drain", target: { group: "worker" }, label: "worker group" })}>
                                    Drain workers…
                                </Button>
                                <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setAct({ command: "queue.inspect", target: {}, label: "queue" })}>
                                    Inspect the queue…
                                </Button>
                            </div>
                        </section>
                    )}
                </div>
            )}

            {tab === "calls" && (
                <Panel title="Active calls" query={query}>
                    {(d) => (
                        <div className="space-y-3 text-sm" data-testid="active-calls">
                            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                                {[
                                    ["Live now", count(d.calls.live)],
                                    ["Connecting", count(d.calls.connecting)],
                                    ["Longest", d.calls.longest_minutes === null ? "None live" : `${d.calls.longest_minutes} min`],
                                    ["Possibly stuck", count(d.calls.possibly_stuck.length)],
                                ].map(([label, value]) => (
                                    <div key={label} className="rounded-md border border-border p-3">
                                        <p className="text-xs text-muted-foreground">{label}</p>
                                        <p className="text-lg font-semibold tabular-nums">{value}</p>
                                    </div>
                                ))}
                            </div>
                            {d.calls.by_channel.length > 0 && (
                                <p className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                                    {d.calls.by_channel.map((c) => (
                                        <span key={c.mode}>
                                            {c.mode}: {count(c.live)}
                                        </span>
                                    ))}
                                </p>
                            )}
                            {d.calls.possibly_stuck.length === 0 ? (
                                <Empty>
                                    No call has run past {d.calls.thresholds.long_call_minutes} minutes or failed to connect within {d.calls.thresholds.not_connected_minutes}. Read{" "}
                                    {when(d.calls.observed_at)}.
                                </Empty>
                            ) : (
                                <TableRegion label="Possibly stuck calls">
                                    <table className="w-full min-w-[560px] text-sm">
                                        <thead>
                                            <tr className="text-left text-xs text-muted-foreground">
                                                <th className="py-1 font-normal">Run</th>
                                                <th className="py-1 font-normal">Workspace</th>
                                                <th className="py-1 font-normal">Channel</th>
                                                <th className="py-1 font-normal">Why</th>
                                                <th className="py-1 text-right font-normal">Age</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-border">
                                            {d.calls.possibly_stuck.map((c) => (
                                                <tr key={c.workflow_run_id}>
                                                    <td className="py-1 font-mono text-xs">
                                                        {owner ? (
                                                            <Link className="underline underline-offset-2" href={`/superadmin/billing/calls/${c.workflow_run_id}`}>
                                                                #{c.workflow_run_id}
                                                            </Link>
                                                        ) : (
                                                            `#${c.workflow_run_id}`
                                                        )}
                                                    </td>
                                                    <td className="py-1">{c.organization_id}</td>
                                                    <td className="py-1">
                                                        {c.mode} · {words(c.direction)}
                                                    </td>
                                                    <td className="py-1">
                                                        <StateBadge state="degraded" label={words(c.reason)} />
                                                    </td>
                                                    <td className="py-1 text-right tabular-nums">{c.age_minutes} min</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </TableRegion>
                            )}
                        </div>
                    )}
                </Panel>
            )}

            {act && (
                <section className="rounded-lg border border-border p-4" aria-label="Command">
                    <div className="mb-2 flex items-center justify-between">
                        <h2 className="text-base font-semibold">{act.command}</h2>
                        <Button variant="ghost" className="min-h-11 md:min-h-8" onClick={() => setAct(null)}>
                            Close
                        </Button>
                    </div>
                    <OpsCommand key={`${act.command}-${act.label}`} command={act.command} target={act.target} targetLabel={act.label} />
                </section>
            )}
        </div>
    );
}

/**
 * Operations and delivery (screen 39): Jobs, Providers, Delivery and
 * Infrastructure under one environment header. Pause, retry and drain are
 * the ops stream's typed commands, previewed here and run there.
 */
export default function OperationsPage() {
    return (
        <Suspense fallback={null}>
            <OperationsInner />
        </Suspense>
    );
}
