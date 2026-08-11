"use client";

/**
 * The shape of an account's calls.
 *
 * The runs table answers "which calls happened". None of the questions that
 * decide whether an agent is working can be read off it: what fraction
 * connect, how long they run, which agent carries the volume, and what hour
 * the phone actually rings.
 *
 * Answer rate leads because it is the one number that moves for reasons
 * outside the agent — a bad list, a blocked caller ID, a carrier problem —
 * and a team debugging prompts while the answer rate collapses is debugging
 * the wrong thing.
 */

import { AlertTriangle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    ComposedChart,
    Line,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getCallAnalyticsApiV1OrganizationsUsageCallsGet } from "@/client/sdk.gen";
import { seriesColor } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    LoadingBlock,
    PanelMessage,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import { formatDateIST, formatNumber, formatPaise } from "@/lib/billing/format";

type DailyRow = {
    day: string;
    calls: number;
    billable_minutes: number;
    charged_paise: number;
};

type Totals = {
    calls: number;
    answered: number;
    billable_seconds: number;
    charged_paise: number;
    average_seconds: number | null;
    answer_rate: number | null;
};

type DispositionRow = {
    disposition: string | null;
    calls: number;
    billable_seconds: number;
};

type DirectionRow = {
    direction: string;
    calls: number;
    answered: number;
    billable_seconds: number;
};

type AgentRow = {
    workflow_id: number;
    name: string;
    calls: number;
    billable_seconds: number;
    charged_paise: number;
};

type CallAnalytics = {
    daily: DailyRow[];
    totals: Totals;
    by_disposition: DispositionRow[];
    by_direction: DirectionRow[];
    by_duration: Array<{ bucket: string; calls: number }>;
    by_hour: Array<{ hour: number; calls: number }>;
    by_agent: AgentRow[];
};

function formatDuration(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function formatHour(hour: number): string {
    const suffix = hour < 12 ? "am" : "pm";
    const twelve = hour % 12 === 0 ? 12 : hour % 12;
    return `${twelve}${suffix}`;
}

export default function CallAnalyticsPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();
    const searchParams = useSearchParams();
    const days = Number(searchParams.get("days")) || 30;

    const [data, setData] = useState<CallAnalytics | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getCallAnalyticsApiV1OrganizationsUsageCallsGet({
                query: { days },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load call analytics"));
            } else {
                setData((result.data as unknown as CallAnalytics) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, days]);

    if (loading && !data) return <LoadingBlock label="Loading call analytics" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const totals = data?.totals;
    const daily = data?.daily ?? [];
    const byDisposition = data?.by_disposition ?? [];
    const byDirection = data?.by_direction ?? [];
    const byDuration = data?.by_duration ?? [];
    const byHour = data?.by_hour ?? [];
    const byAgent = data?.by_agent ?? [];

    return (
        <div className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                    label="Calls"
                    value={formatNumber(totals?.calls ?? 0)}
                    sub={`Last ${days} days`}
                />
                <StatTile
                    label="Answer rate"
                    value={
                        totals?.answer_rate === null || totals?.answer_rate === undefined
                            ? "—"
                            : `${(totals.answer_rate * 100).toFixed(0)}%`
                    }
                    sub={`${formatNumber(totals?.answered ?? 0)} connected`}
                    // The number that moves for reasons outside the agent: a bad
                    // list, a blocked caller ID, a carrier problem.
                    tone={
                        totals?.answer_rate !== null &&
                        totals?.answer_rate !== undefined &&
                        totals.answer_rate < 0.3
                            ? "warning"
                            : undefined
                    }
                />
                <StatTile
                    label="Average length"
                    value={formatDuration(totals?.average_seconds)}
                    sub={`${formatNumber(
                        Math.round((totals?.billable_seconds ?? 0) / 60),
                    )} min total`}
                />
                <StatTile
                    label="Spend"
                    value={formatPaise(totals?.charged_paise ?? 0)}
                />
            </div>

            <ChartCard
                title="Calls per day"
                description="Volume and the minutes behind it"
                isEmpty={daily.every((row) => row.calls === 0)}
                height={280}
            >
                <ResponsiveContainer width="100%" height={280}>
                    <ComposedChart
                        data={daily}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                        <YAxis width={48} allowDecimals={false} {...axisProps(mode)} />
                        <RTooltip
                            content={
                                <ChartTooltip
                                    formatter={(v) => formatNumber(Number(v))}
                                    labelFormatter={formatDateIST}
                                />
                            }
                        />
                        <Bar
                            dataKey="calls"
                            name="Calls"
                            fill={seriesColor(0, mode)}
                            radius={[3, 3, 0, 0]}
                        />
                        <Line
                            type="monotone"
                            dataKey="billable_minutes"
                            name="Minutes"
                            stroke={seriesColor(1, mode)}
                            strokeWidth={2}
                            dot={false}
                        />
                    </ComposedChart>
                </ResponsiveContainer>
            </ChartCard>

            <div className="grid gap-6 lg:grid-cols-2">
                {/* Emitted in band order with the empty bands kept: a histogram
                    that drops them and sorts by count is not a distribution, it
                    rearranges itself as data arrives. */}
                <ChartCard
                    title="How long calls run"
                    description="Under 30s is usually a hang-up; past five minutes is where LLM spend compounds"
                    isEmpty={byDuration.every((row) => row.calls === 0)}
                    height={260}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart
                            data={byDuration}
                            margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="bucket" {...axisProps(mode)} />
                            <YAxis width={48} allowDecimals={false} {...axisProps(mode)} />
                            <RTooltip
                                content={
                                    <ChartTooltip formatter={(v) => formatNumber(Number(v))} />
                                }
                            />
                            <Bar
                                dataKey="calls"
                                name="Calls"
                                fill={seriesColor(2, mode)}
                                radius={[3, 3, 0, 0]}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>

                {/* Local hour, not UTC. IST is 5:30 off the hour, so a UTC
                    bucket would smear every peak across two of them. */}
                <ChartCard
                    title="When the phone rings"
                    description="By hour of the day, IST"
                    isEmpty={byHour.every((row) => row.calls === 0)}
                    height={260}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart
                            data={byHour}
                            margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis
                                dataKey="hour"
                                interval={2}
                                tickFormatter={(v: number) => formatHour(v)}
                                {...axisProps(mode)}
                            />
                            <YAxis width={48} allowDecimals={false} {...axisProps(mode)} />
                            <RTooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatNumber(Number(v))}
                                        labelFormatter={(l) => formatHour(Number(l))}
                                    />
                                }
                            />
                            <Bar
                                dataKey="calls"
                                name="Calls"
                                fill={seriesColor(3, mode)}
                                radius={[3, 3, 0, 0]}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
                <Card>
                    <CardHeader>
                        <CardTitle>Outcomes</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {byDisposition.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                No calls in this period.
                            </p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Disposition</TableHead>
                                        <TableHead className="text-right">Calls</TableHead>
                                        <TableHead className="text-right">Talk time</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {byDisposition.map((row) => (
                                        <TableRow key={row.disposition ?? "__none__"}>
                                            <TableCell className="font-medium">
                                                {/* Not folded into "unknown": a call that ended
                                                    without a disposition and one the pipeline
                                                    never finished writing are different
                                                    findings. */}
                                                {row.disposition ?? (
                                                    <span className="text-muted-foreground">
                                                        Not recorded
                                                    </span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatNumber(row.calls)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatDuration(row.billable_seconds)}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>By direction</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {byDirection.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                No calls in this period.
                            </p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Direction</TableHead>
                                        <TableHead className="text-right">Calls</TableHead>
                                        <TableHead className="text-right">Answered</TableHead>
                                        <TableHead className="text-right">Talk time</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {byDirection.map((row) => (
                                        <TableRow key={row.direction}>
                                            <TableCell className="font-medium capitalize">
                                                {row.direction}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatNumber(row.calls)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatNumber(row.answered)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatDuration(row.billable_seconds)}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Busiest agents</CardTitle>
                </CardHeader>
                <CardContent>
                    {byAgent.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            No calls in this period.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Agent</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                    <TableHead className="text-right">Talk time</TableHead>
                                    <TableHead className="text-right">Spend</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {byAgent.map((row) => (
                                    <TableRow key={row.workflow_id}>
                                        <TableCell className="font-medium">{row.name}</TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(row.calls)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatDuration(row.billable_seconds)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(row.charged_paise)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
