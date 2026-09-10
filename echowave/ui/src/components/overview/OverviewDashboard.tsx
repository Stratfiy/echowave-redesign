"use client";

/**
 * The first screen after signing in, once there is something to show.
 *
 * Before an account has taken a call, this is a door: build an agent, hear
 * it, put it on a number. After that it is a dashboard — calls, answer rate,
 * spend and what is left, the last month at a glance — with the deeper
 * analytics and the daily export one click away. The Reports page's job
 * (yesterday's calls as a CSV) lives here now as an export button, so there
 * is one place to look rather than a summary screen and a separate report.
 *
 * Numbers come from the same two endpoints the Analytics tabs read, so the
 * tile here and the chart there never disagree. Money is in credits, the
 * in-product unit; rupees stay on invoices.
 */

import { format } from "date-fns";
import { ArrowRight, Download, Phone, Sparkles } from "lucide-react";
import Link from "next/link";
import posthog from "posthog-js";
import { useEffect, useState } from "react";
import {
    Area,
    AreaChart,
    Bar,
    CartesianGrid,
    ComposedChart,
    Legend,
    Line,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import {
    getCallAnalyticsApiV1OrganizationsUsageCallsGet,
    getDailyRunsDetailApiV1OrganizationsReportsDailyRunsGet,
    getSpendBreakdownApiV1OrganizationsUsageSpendGet,
} from "@/client/sdk.gen";
import { AgentBuilderPanel } from "@/components/agent-builder/AgentBuilderPanel";
import { COST_COMPONENTS, seriesColor } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    OrderedLegend,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PostHogEvent } from "@/constants/posthog-events";
import { detailFromResult } from "@/lib/apiError";
import { formatCredits, formatCreditsLabel, formatDateIST, formatNumber } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type DailyRow = { day: string; calls: number; billable_minutes: number; charged_paise: number };
type AgentRow = { workflow_id: number; name: string; calls: number; billable_seconds: number; charged_paise: number };
type Calls = {
    daily: DailyRow[];
    totals: {
        calls: number;
        answered: number;
        billable_seconds: number;
        charged_paise: number;
        average_seconds: number | null;
        answer_rate: number | null;
    };
    by_agent: AgentRow[];
};
type SpendRow = { day: string; stt: number; llm: number; tts: number; telephony: number; platform: number };
type Spend = {
    series: SpendRow[];
    balance_paise: number;
    spent_paise: number;
    burn: { daily_average_paise: number; days_remaining: number | null };
};

const WINDOWS = [7, 30, 90] as const;

function formatDuration(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function downloadCsv(filename: string, headers: string[], rows: string[][]) {
    const escape = (cell: string) => `"${cell.replace(/"/g, '""')}"`;
    const content = [headers.map(escape).join(","), ...rows.map((row) => row.map(escape).join(","))].join("\n");
    const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.style.visibility = "hidden";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

export function OverviewDashboard({ firstName }: { firstName?: string }) {
    const mode = useChartMode();
    const authReady = useAuthReady();
    const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
    const [calls, setCalls] = useState<Calls | null>(null);
    const [spend, setSpend] = useState<Spend | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [exporting, setExporting] = useState<"calls" | "spend" | null>(null);
    const [exportNote, setExportNote] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        void (async () => {
            setLoading(true);
            const [callsResult, spendResult] = await Promise.all([
                getCallAnalyticsApiV1OrganizationsUsageCallsGet({ query: { days } }),
                getSpendBreakdownApiV1OrganizationsUsageSpendGet({ query: { days } }),
            ]);
            if (cancelled) return;
            if (callsResult.error) {
                setError(detailFromResult(callsResult, "Could not load your calls."));
            } else {
                setCalls((callsResult.data as unknown as Calls) ?? null);
                setError(null);
            }
            if (!spendResult.error) setSpend((spendResult.data as unknown as Spend) ?? null);
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, days]);

    const changeWindow = (next: (typeof WINDOWS)[number]) => {
        setDays(next);
        posthog.capture(PostHogEvent.OVERVIEW_RANGE_CHANGED, { days: next });
    };

    // Yesterday's calls, one row each, the way the Reports page exported them.
    // "Yesterday" because today's calls are still arriving, and a report that
    // changes each time it is downloaded is not a report.
    const exportCalls = async () => {
        setExporting("calls");
        setExportNote(null);
        const date = format(new Date(Date.now() - 24 * 60 * 60 * 1000), "yyyy-MM-dd");
        const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";
        const response = await getDailyRunsDetailApiV1OrganizationsReportsDailyRunsGet({ query: { date, timezone } });
        setExporting(null);
        const rows = (response.data ?? []) as Array<{
            workflow_id: number;
            run_id: number;
            phone_number?: string | null;
            disposition?: string | null;
            duration_seconds: number;
            workflow_name?: string | null;
        }>;
        posthog.capture(PostHogEvent.OVERVIEW_EXPORT_CLICKED, { kind: "calls", rows: rows.length, date });
        if (response.error || rows.length === 0) {
            setExportNote(response.error ? detailFromResult(response, "Could not export.") : `No calls on ${date}.`);
            return;
        }
        downloadCsv(
            `calls_${date}.csv`,
            ["Date", "Agent", "Phone number", "Disposition", "Duration (seconds)", "Call URL"],
            rows.map((run) => [
                date,
                run.workflow_name ?? "",
                run.phone_number ?? "",
                run.disposition ?? "",
                String(run.duration_seconds),
                `${window.location.origin}/workflow/${run.workflow_id}/run/${run.run_id}`,
            ]),
        );
    };

    const exportSpend = () => {
        if (!spend) return;
        posthog.capture(PostHogEvent.OVERVIEW_EXPORT_CLICKED, { kind: "spend", days });
        downloadCsv(
            `spend_last_${days}_days.csv`,
            ["Day", ...COST_COMPONENTS.map((c) => `${c.label} (credits)`), "Total (credits)"],
            spend.series.map((row) => {
                const parts = COST_COMPONENTS.map((c) => Number(row[c.key as keyof SpendRow] ?? 0));
                return [
                    row.day,
                    ...parts.map((p) => formatCredits(p)),
                    formatCredits(parts.reduce((a, b) => a + b, 0)),
                ];
            }),
        );
    };

    const totals = calls?.totals;
    const daily = calls?.daily ?? [];
    const byAgent = [...(calls?.by_agent ?? [])].sort((a, b) => b.calls - a.calls).slice(0, 5);
    const noCallsYet = !loading && !error && (totals?.calls ?? 0) === 0 && (spend?.spent_paise ?? 0) === 0;
    const daysRemaining = spend?.burn?.days_remaining ?? null;
    const spendByComponent = COST_COMPONENTS.filter((c) =>
        (spend?.series ?? []).some((row) => Number(row[c.key as keyof SpendRow] ?? 0) > 0),
    );

    if (noCallsYet) {
        return (
            <div className="mx-auto max-w-4xl">
                <div className="mb-6 rounded-[var(--radius-large)] border border-border bg-card p-6">
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--accent-brand-soft)] px-2.5 py-1 text-xs font-medium text-[var(--accent-brand)]">
                        <Sparkles className="h-3.5 w-3.5" />
                        No calls yet
                    </span>
                    <h2 className="mt-3 text-xl font-semibold">
                        {firstName ? `${firstName}, your` : "Your"} numbers appear here after the first call.
                    </h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Pick a ready-made agent and hear it in about two minutes, or describe your business below.
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2">
                        <Button asChild>
                            <Link href="/start">
                                Build your first agent
                                <ArrowRight className="h-4 w-4" />
                            </Link>
                        </Button>
                        <Button asChild variant="outline">
                            <Link href="/numbers">
                                <Phone className="h-4 w-4" />
                                Get a phone number
                            </Link>
                        </Button>
                    </div>
                </div>
                <AgentBuilderPanel />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-1 rounded-full border border-border bg-card p-1" role="group" aria-label="Period">
                    {WINDOWS.map((option) => (
                        <button
                            key={option}
                            type="button"
                            aria-pressed={days === option}
                            onClick={() => changeWindow(option)}
                            className={cn(
                                "rounded-full px-3 py-1 text-sm transition-colors",
                                days === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
                            )}
                        >
                            {option}d
                        </button>
                    ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {exportNote && <span className="text-xs text-muted-foreground">{exportNote}</span>}
                    <Button variant="outline" size="sm" onClick={() => void exportCalls()} disabled={exporting !== null}>
                        <Download className="h-4 w-4" />
                        {exporting === "calls" ? "Preparing…" : "Yesterday's calls (CSV)"}
                    </Button>
                    <Button variant="outline" size="sm" onClick={exportSpend} disabled={!spend || exporting !== null}>
                        <Download className="h-4 w-4" />
                        Spend (CSV)
                    </Button>
                    <Button asChild size="sm">
                        <Link href="/start">
                            New agent
                            <ArrowRight className="h-4 w-4" />
                        </Link>
                    </Button>
                </div>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <StatTile label="Calls" value={formatNumber(totals?.calls ?? 0)} sub={`Last ${days} days`} />
                <StatTile
                    label="Answer rate"
                    value={totals?.answer_rate == null ? "—" : `${(totals.answer_rate * 100).toFixed(0)}%`}
                    sub={`${formatNumber(totals?.answered ?? 0)} connected`}
                    tone={totals?.answer_rate != null && totals.answer_rate < 0.3 ? "warning" : undefined}
                />
                <StatTile
                    label="Average length"
                    value={formatDuration(totals?.average_seconds)}
                    sub={`${formatNumber(Math.round((totals?.billable_seconds ?? 0) / 60))} min total`}
                />
                <StatTile label="Spent" value={formatCreditsLabel(spend?.spent_paise ?? totals?.charged_paise ?? 0)} sub={`Last ${days} days`} />
                <StatTile
                    label="Credits left"
                    value={formatCreditsLabel(spend?.balance_paise ?? 0)}
                    sub={daysRemaining === null ? "No spend to project from" : `About ${daysRemaining} days at this rate`}
                    tone={daysRemaining !== null && daysRemaining <= 7 ? "critical" : daysRemaining !== null && daysRemaining <= 21 ? "warning" : undefined}
                />
            </div>

            <div className="grid gap-6 lg:grid-cols-5">
                <ChartCard
                    title="Calls per day"
                    description="Volume and the minutes behind it"
                    loading={loading && !calls}
                    isEmpty={daily.every((row) => row.calls === 0)}
                    height={260}
                    className="lg:col-span-3"
                    action={
                        <Button asChild variant="ghost" size="sm">
                            <Link href={`/analytics?days=${days}`}>All analytics</Link>
                        </Button>
                    }
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <ComposedChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis width={40} allowDecimals={false} {...axisProps(mode)} />
                            <RTooltip content={<ChartTooltip formatter={(v) => formatNumber(Number(v))} labelFormatter={formatDateIST} />} />
                            <Bar dataKey="calls" name="Calls" fill={seriesColor(0, mode)} radius={[3, 3, 0, 0]} />
                            <Line type="monotone" dataKey="billable_minutes" name="Minutes" stroke={seriesColor(1, mode)} strokeWidth={2} dot={false} />
                        </ComposedChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Where the credits go"
                    description="Daily spend by component"
                    loading={loading && !spend}
                    isEmpty={spendByComponent.length === 0}
                    height={260}
                    className="lg:col-span-2"
                    action={
                        <Button asChild variant="ghost" size="sm">
                            <Link href={`/analytics/spend?days=${days}`}>Spend</Link>
                        </Button>
                    }
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <AreaChart data={spend?.series ?? []} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis width={44} tickFormatter={(v: number) => formatCredits(v)} {...axisProps(mode)} />
                            <RTooltip content={<ChartTooltip formatter={(v) => formatCreditsLabel(Number(v))} labelFormatter={formatDateIST} />} />
                            <Legend
                                content={
                                    <OrderedLegend
                                        items={spendByComponent.map((c) => ({ key: c.key, label: c.label, color: seriesColor(c.slot, mode) }))}
                                    />
                                }
                            />
                            {spendByComponent.map((c) => (
                                <Area
                                    key={c.key}
                                    type="monotone"
                                    dataKey={c.key}
                                    name={c.label}
                                    stackId="cost"
                                    stroke={seriesColor(c.slot, mode)}
                                    fill={seriesColor(c.slot, mode)}
                                    fillOpacity={0.35}
                                />
                            ))}
                        </AreaChart>
                    </ResponsiveContainer>
                </ChartCard>
            </div>

            <div className="grid gap-6 lg:grid-cols-5">
                <Card className="lg:col-span-3">
                    <CardHeader>
                        <CardTitle className="text-base">Busiest agents</CardTitle>
                        <CardDescription>Calls and credits in the last {days} days</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {byAgent.length === 0 ? (
                            <p className="text-sm text-muted-foreground">No agent has taken a call in this period.</p>
                        ) : (
                            <table className="w-full text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="pb-2 font-medium">Agent</th>
                                        <th className="pb-2 text-right font-medium">Calls</th>
                                        <th className="pb-2 text-right font-medium">Minutes</th>
                                        <th className="pb-2 text-right font-medium">Credits</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-border">
                                    {byAgent.map((row) => (
                                        <tr key={row.workflow_id}>
                                            <td className="py-2">
                                                <Link href={`/workflow/${row.workflow_id}`} className="font-medium hover:underline">
                                                    {row.name}
                                                </Link>
                                            </td>
                                            <td className="py-2 text-right tabular-nums">{formatNumber(row.calls)}</td>
                                            <td className="py-2 text-right tabular-nums">{formatNumber(Math.round(row.billable_seconds / 60))}</td>
                                            <td className="py-2 text-right tabular-nums">{formatCredits(row.charged_paise)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </CardContent>
                </Card>

                <Card className="lg:col-span-2">
                    <CardHeader>
                        <CardTitle className="text-base">Next steps</CardTitle>
                        <CardDescription>The two things that grow this page.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <Button asChild variant="outline" className="w-full justify-between">
                            <Link href="/start">
                                Build another agent
                                <ArrowRight className="h-4 w-4" />
                            </Link>
                        </Button>
                        <Button asChild variant="outline" className="w-full justify-between">
                            <Link href="/numbers">
                                Put an agent on a number
                                <ArrowRight className="h-4 w-4" />
                            </Link>
                        </Button>
                        <Button asChild variant="outline" className="w-full justify-between">
                            <Link href="/campaigns">
                                Run an outbound campaign
                                <ArrowRight className="h-4 w-4" />
                            </Link>
                        </Button>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}

export default OverviewDashboard;
