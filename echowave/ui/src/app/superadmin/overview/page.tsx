"use client";

import Link from "next/link";

import { MetricDefinition } from "@/components/shell";
import { ApprovalQueue } from "@/components/staff/ApprovalQueue";
import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { type Attention, orderAttention } from "@/lib/staff/attention";
import { useStaffData } from "@/lib/staff/data";
import { count, percent, when, words } from "@/lib/staff/format";

type Metric = { value: number | null; sample: number; definition: string };

type Overview = {
    observed_at: string;
    attention: Attention[];
    attention_state: string;
    attention_reason?: string;
    health: { state: string; signals: Array<{ name: string; state: string; detail?: string | null }>; reason?: string };
    metrics:
        | { state: "ok"; period: string; weekly_useful_users: Metric; task_success: Metric; usefulness: Metric }
        | { state: "unavailable"; reason: string };
    recent_failures?: Array<{ task_id: number; organization_id: number; state: string; kind: string; created_at: string | null }>;
    recent_commands?: Array<{ id: number; command: string; state: string; created_at: string }>;
    evidence_state: string;
};

/**
 * Founder overview (screen 29): what needs a person now, then three
 * numbers with their definitions, then the evidence behind them. Read-only;
 * every row links to the one place that resolves it.
 */
export default function OverviewPage() {
    const query = useStaffData<Overview>("/api/v1/admin/staff/overview", undefined, 60_000);
    const ops = useStaffData<{ status: string }>("/api/v1/admin/ops/health");
    useReportFreshness(query.state, query.refreshedAt);

    return (
        <div className="space-y-4">
            <PageHeader title="Overview" description="What needs attention first, then the numbers. Every figure opens its source." />
            <Panel title="Needs attention" query={query} skeletonHeight={200}>
                {(data) =>
                    data.attention_state !== "ok" ? (
                        <p role="alert" className="text-sm">
                            The attention queue could not be read ({data.attention_reason}). This is not the same as nothing to do.
                        </p>
                    ) : (
                        <ul className="divide-y divide-border" data-testid="attention">
                            {orderAttention(data.attention).map((item) => (
                                <li key={item.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2" data-key={item.key}>
                                    <StateBadge
                                        state={item.count === null ? item.state : item.count > 0 ? (item.severity === "critical" ? "critical" : "pending") : "ok"}
                                        label={item.count === null ? words(item.state) : item.count > 0 ? (item.severity === "critical" ? "Act now" : "Look") : "Clear"}
                                    />
                                    <span className="min-w-0 flex-1 text-sm">{item.title}</span>
                                    <span className="text-sm font-semibold tabular-nums" aria-label={`${item.title}: ${item.count ?? "not measured"}`}>
                                        {item.count === null ? "—" : count(item.count)}
                                    </span>
                                    {item.count === null ? (
                                        <span className="w-full text-xs text-muted-foreground sm:w-auto">{item.reason}</span>
                                    ) : (
                                        <Link href={item.href} className="motion-m1 inline-flex min-h-11 items-center text-sm underline underline-offset-2 md:min-h-8">
                                            Open
                                        </Link>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>

            <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
                <Panel title="Useful outcomes" query={query}>
                    {(data) =>
                        data.metrics.state !== "ok" ? (
                            <p role="alert" className="text-sm">Metrics could not be computed ({data.metrics.reason}).</p>
                        ) : (
                            <div className="grid gap-4 sm:grid-cols-3">
                                <MetricLink href="/superadmin/analytics">
                                    <MetricDefinition
                                        name="Weekly useful users"
                                        value={data.metrics.weekly_useful_users.value}
                                        definition={data.metrics.weekly_useful_users.definition}
                                        period={`${data.metrics.period} · ${count(data.metrics.weekly_useful_users.sample)} outcomes`}
                                        source="agent_tasks (completed), approval cards (ran)"
                                    />
                                </MetricLink>
                                <MetricLink href="/superadmin/analytics">
                                    <MetricDefinition
                                        name="Task success"
                                        value={data.metrics.task_success.value === null ? null : percent(data.metrics.task_success.value)}
                                        definition={data.metrics.task_success.definition}
                                        period={`${data.metrics.period} · n=${count(data.metrics.task_success.sample)}`}
                                        missingReason="No finished tasks in the period"
                                        source="agent_tasks, approval cards"
                                    />
                                </MetricLink>
                                <MetricLink href="/superadmin/analytics">
                                    <MetricDefinition
                                        name="Usefulness"
                                        value={data.metrics.usefulness.value === null ? null : percent(data.metrics.usefulness.value)}
                                        definition={data.metrics.usefulness.definition}
                                        period={`${data.metrics.period} · n=${count(data.metrics.usefulness.sample)} answers`}
                                        missingReason="Nobody has answered yet"
                                        source="output_feedback"
                                    />
                                </MetricLink>
                            </div>
                        )
                    }
                </Panel>
                <Panel title="Health" query={query}>
                    {(data) => (
                        <div className="space-y-2 text-sm">
                            <p className="flex items-center gap-2">
                                Platform <StateBadge state={data.health.state} />
                            </p>
                            <ul className="space-y-1">
                                {data.health.signals.map((s) => (
                                    <li key={s.name} className="flex items-center justify-between gap-2">
                                        <span>{words(s.name)}</span>
                                        <StateBadge state={s.state} />
                                    </li>
                                ))}
                            </ul>
                            <p className="flex flex-wrap items-center gap-2 pt-2 text-xs text-muted-foreground">
                                Ops monitoring{" "}
                                {ops.state === "ok" ? (
                                    <StateBadge state={ops.data?.status === "ok" ? "healthy" : ops.data?.status} />
                                ) : ops.state === "loading" ? (
                                    "checking…"
                                ) : (
                                    <StateBadge state="needs_setup" label="Needs setup (ops console)" />
                                )}
                            </p>
                        </div>
                    )}
                </Panel>
            </div>

            <ApprovalQueue />

            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Recent failures (72 h)" query={query}>
                    {(data) =>
                        data.evidence_state !== "ok" ? (
                            <p role="alert" className="text-sm">Could not read recent failures.</p>
                        ) : !data.recent_failures?.length ? (
                            <Empty>No failed or unknown tasks in the last 72 hours.</Empty>
                        ) : (
                            <TableRegion label="Recent failures">
                                <table className="w-full text-sm">
                                    <thead className="text-left text-xs text-muted-foreground">
                                        <tr>
                                            <th className="py-1 pr-2 font-normal">Task</th>
                                            <th className="py-1 pr-2 font-normal">State</th>
                                            <th className="py-1 font-normal">When</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.recent_failures.map((f) => (
                                            <tr key={f.task_id} className="border-t border-border">
                                                <td className="py-1.5 pr-2">
                                                    <Link className="inline-flex min-h-11 items-center underline underline-offset-2 md:min-h-0" href={`/superadmin/operations/trace/${f.task_id}`}>
                                                        #{f.task_id}
                                                    </Link>{" "}
                                                    <span className="text-xs text-muted-foreground">ws {f.organization_id}</span>
                                                </td>
                                                <td className="py-1.5 pr-2">
                                                    <StateBadge state={f.state} />
                                                </td>
                                                <td className="py-1.5 text-xs">{when(f.created_at)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </TableRegion>
                        )
                    }
                </Panel>
                <Panel title="Recent staff commands" query={query}>
                    {(data) =>
                        !data.recent_commands?.length ? (
                            <Empty>No staff commands yet.</Empty>
                        ) : (
                            <ul className="divide-y divide-border text-sm">
                                {data.recent_commands.map((c) => (
                                    <li key={c.id} className="flex flex-wrap items-center gap-2 py-1.5">
                                        <span className="font-mono text-xs">#{c.id}</span>
                                        <span className="min-w-0 flex-1">{c.command}</span>
                                        <StateBadge state={c.state} />
                                        <span className="text-xs text-muted-foreground">{when(c.created_at)}</span>
                                    </li>
                                ))}
                            </ul>
                        )
                    }
                </Panel>
            </div>
        </div>
    );
}

function MetricLink({ href, children }: { href: string; children: React.ReactNode }) {
    return (
        <div className="min-w-0 space-y-1">
            {children}
            <Link href={href} className="inline-flex min-h-11 items-center text-xs underline underline-offset-2 md:min-h-6">
                Open the source
            </Link>
        </div>
    );
}
