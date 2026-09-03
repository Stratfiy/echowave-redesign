"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    Line,
    LineChart,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getOverviewApiV1AdminBillingOverviewGet } from "@/client/sdk.gen";
import { COST_COMPONENTS, LATENCY_TARGET_MS, seriesColor } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { detailFromResult } from "@/lib/apiError";
import {
    changeRatio,
    formatDateIST,
    formatMs,
    formatNumber,
    formatPaise,
    formatPaiseCompact,
    formatPercent,
} from "@/lib/billing/format";

type Overview = Awaited<
    ReturnType<typeof getOverviewApiV1AdminBillingOverviewGet>
>["data"];

export default function BillingOverviewPage() {
    const mode = useChartMode();
    const [data, setData] = useState<Overview | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getOverviewApiV1AdminBillingOverviewGet();
            if (cancelled) return;
            if (result.error) {
                setError(detailFromResult(result, "Failed to load overview"));
            } else {
                setData(result.data ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    const current = data?.current as Record<string, number | null> | undefined;
    const previous = data?.previous as Record<string, number | null> | undefined;

    const minutesSeries = (data?.minutes_per_day ?? []) as Array<
        Record<string, number | string | null>
    >;
    const costSeries = (data?.cost_composition ?? []) as Array<Record<string, number | string>>;
    const topAccounts = (data?.top_accounts ?? []) as Array<Record<string, number | string>>;
    const latencySeries = (data?.latency ?? []) as Array<Record<string, number | string>>;

    const hasMinutes = minutesSeries.some((d) => Number(d.billable_minutes) > 0);
    const hasCost = costSeries.some((d) =>
        COST_COMPONENTS.some((c) => Number(d[c.key]) > 0),
    );
    const hasLatency = latencySeries.some(
        (d) => d.p50_ms !== null && d.p50_ms !== undefined,
    );
    // minutes_per_day is really "daily_series" — it carries revenue and call
    // success too, reused here rather than fetching the same rollup twice.
    const hasRevenueTrend = minutesSeries.some((d) => Number(d.charged_paise) > 0);
    const hasSuccessRate = minutesSeries.some(
        (d) => d.success_rate !== null && d.success_rate !== undefined,
    );

    return (
        <div className="space-y-6">
            <section
                className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
                aria-label="Headline figures"
            >
                <StatTile
                    label="Revenue"
                    value={formatPaise(current?.revenue_paise ?? 0)}
                    change={changeRatio(current?.revenue_paise, previous?.revenue_paise)}
                    sub="vs previous period"
                />
                <StatTile
                    label="Gross margin"
                    value={formatPaise(current?.margin_paise ?? 0)}
                    sub={formatPercent(current?.margin_pct)}
                    change={changeRatio(current?.margin_paise, previous?.margin_paise)}
                />
                <StatTile
                    label="Billable minutes"
                    value={formatNumber(current?.billable_minutes ?? 0)}
                    change={changeRatio(
                        current?.billable_minutes,
                        previous?.billable_minutes,
                    )}
                />
                <StatTile
                    label="Active accounts"
                    value={formatNumber(current?.active_accounts ?? 0)}
                    change={changeRatio(
                        current?.active_accounts,
                        previous?.active_accounts,
                    )}
                />
                <StatTile
                    label="Calls today"
                    value={formatNumber((data?.calls_today as number) ?? 0)}
                    sub="IST day"
                />
                <StatTile
                    label="Concurrent calls"
                    value={formatNumber((data?.concurrent_calls as number) ?? 0)}
                    sub="in flight now"
                />
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
                <ChartCard
                    title="Minutes per day"
                    description="Billable minutes, last 30 days"
                    loading={loading}
                    error={error}
                    isEmpty={!hasMinutes}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={minutesSeries} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis width={44} {...axisProps(mode)} />
                            <Tooltip
                                cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                                content={
                                    <ChartTooltip
                                        formatter={(v) => `${formatNumber(v)} min`}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Bar
                                dataKey="billable_minutes"
                                name="Billable minutes"
                                fill={seriesColor(0, mode)}
                                radius={[4, 4, 0, 0]}
                                maxBarSize={18}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Revenue trend"
                    description="What accounts were charged per day, last 30 days"
                    loading={loading}
                    error={error}
                    isEmpty={!hasRevenueTrend}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <AreaChart data={minutesSeries} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis
                                width={62}
                                tickFormatter={(v: number) => formatPaiseCompact(v)}
                                {...axisProps(mode)}
                            />
                            <Tooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatPaise(v)}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Area
                                type="monotone"
                                dataKey="charged_paise"
                                name="Revenue"
                                stroke={seriesColor(0, mode)}
                                strokeWidth={2}
                                fill={seriesColor(0, mode)}
                                fillOpacity={0.15}
                            />
                        </AreaChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Call success rate"
                    description="Answered calls ÷ total calls, per day. A day with no calls has no rate — not 0%."
                    loading={loading}
                    error={error}
                    isEmpty={!hasSuccessRate}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={minutesSeries} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis
                                width={48}
                                domain={[0, 1]}
                                tickFormatter={(v: number) => formatPercent(v, 0)}
                                {...axisProps(mode)}
                            />
                            <Tooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatPercent(v as number)}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Line
                                type="monotone"
                                dataKey="success_rate"
                                name="Success rate"
                                stroke={seriesColor(0, mode)}
                                strokeWidth={2}
                                dot={false}
                                connectNulls={false}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Cost composition"
                    description="Provider cost passed through at cost; the platform fee is our revenue"
                    loading={loading}
                    error={error}
                    isEmpty={!hasCost}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <AreaChart data={costSeries} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis
                                width={62}
                                tickFormatter={(v: number) => formatPaiseCompact(v)}
                                {...axisProps(mode)}
                            />
                            <Tooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatPaise(v)}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Legend
                                iconType="square"
                                iconSize={8}
                                wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
                            />
                            {COST_COMPONENTS.map((component) => (
                                <Area
                                    key={component.key}
                                    type="monotone"
                                    dataKey={component.key}
                                    name={component.label}
                                    stackId="cost"
                                    stroke={seriesColor(component.slot, mode)}
                                    // 2px surface gap between stacked segments so
                                    // adjacent fills stay separable.
                                    strokeWidth={2}
                                    fill={seriesColor(component.slot, mode)}
                                    fillOpacity={0.85}
                                />
                            ))}
                        </AreaChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Top accounts by revenue"
                    description="Top 10 this period"
                    loading={loading}
                    error={error}
                    isEmpty={topAccounts.length === 0}
                    height={300}
                >
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart
                            data={topAccounts}
                            layout="vertical"
                            margin={{ top: 4, right: 16, bottom: 0, left: 8 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} horizontal={false} />
                            <XAxis
                                type="number"
                                tickFormatter={(v: number) => formatPaiseCompact(v)}
                                {...axisProps(mode)}
                            />
                            <YAxis
                                type="category"
                                dataKey="name"
                                width={150}
                                {...axisProps(mode)}
                            />
                            <Tooltip
                                cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                                content={<ChartTooltip formatter={(v) => formatPaise(v)} />}
                            />
                            <Bar
                                dataKey="revenue_paise"
                                name="Revenue"
                                radius={[0, 4, 4, 0]}
                                maxBarSize={18}
                            >
                                {topAccounts.map((entry, index) => (
                                    <Cell key={index} fill={seriesColor(0, mode)} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Perceived latency"
                    description={`p50 and p95 per day · target ${LATENCY_TARGET_MS}ms`}
                    loading={loading}
                    error={error}
                    isEmpty={!hasLatency}
                    action={
                        <Link
                            href="/superadmin/billing/latency"
                            className="text-xs font-medium text-primary hover:underline"
                        >
                            Details
                        </Link>
                    }
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={latencySeries} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis
                                width={62}
                                tickFormatter={(v: number) => formatMs(v)}
                                {...axisProps(mode)}
                            />
                            <Tooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatMs(v)}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Legend
                                iconType="plainline"
                                iconSize={12}
                                wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
                            />
                            <ReferenceLine
                                y={LATENCY_TARGET_MS}
                                stroke={axisProps(mode).stroke}
                                strokeDasharray="4 4"
                                // No inline label: outside the plot it gets
                                // clipped, inside it lands on the p50 line. The
                                // card's subtitle already names the target.
                            />
                            <Line
                                type="monotone"
                                dataKey="p50_ms"
                                name="p50"
                                stroke={seriesColor(0, mode)}
                                strokeWidth={2}
                                dot={false}
                            />
                            <Line
                                type="monotone"
                                dataKey="p95_ms"
                                name="p95"
                                stroke={seriesColor(1, mode)}
                                strokeWidth={2}
                                dot={false}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </ChartCard>
            </div>
        </div>
    );
}
