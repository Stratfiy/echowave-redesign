"use client";

/**
 * What the calls cost, split by what caused the cost.
 *
 * A single total answers "how much" and nothing else. The split is what tells
 * someone whether their bill is speech, language or carriage — and those have
 * completely different fixes: a cheaper voice, a shorter prompt, or fewer
 * unanswered dials.
 *
 * Balance and days-remaining sit alongside because the question that always
 * follows a spend chart on a prepaid account is "how long does my credit
 * last".
 */

import { AlertTriangle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
    Area,
    AreaChart,
    CartesianGrid,
    Legend,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getSpendBreakdownApiV1OrganizationsUsageSpendGet } from "@/client/sdk.gen";
import { COST_COMPONENTS, seriesColor } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    LoadingBlock,
    OrderedLegend,
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
import { detailFromResult } from "@/lib/apiError";
import { formatDateIST, formatPaise, formatPaiseCompact } from "@/lib/billing/format";

type CompositionRow = {
    day: string;
    stt: number;
    llm: number;
    tts: number;
    telephony: number;
    platform: number;
};

type Spend = {
    series: CompositionRow[];
    balance_paise: number;
    spent_paise: number;
    burn: {
        daily_average_paise: number;
        days_remaining: number | null;
    };
};

export default function CustomerSpendPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();
    const searchParams = useSearchParams();
    const days = Number(searchParams.get("days")) || 30;

    const [data, setData] = useState<Spend | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getSpendBreakdownApiV1OrganizationsUsageSpendGet({
                query: { days },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromResult(result, "Failed to load spend"));
            } else {
                setData((result.data as unknown as Spend) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, days]);

    if (loading && !data) return <LoadingBlock label="Loading spend" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const series = data?.series ?? [];
    const spent = data?.spent_paise ?? 0;
    const balance = data?.balance_paise ?? 0;
    const daysRemaining = data?.burn?.days_remaining ?? null;

    const byComponent = COST_COMPONENTS.map((component) => ({
        ...component,
        total: series.reduce(
            (sum, row) =>
                sum + Number(row[component.key as keyof CompositionRow] ?? 0),
            0,
        ),
    })).filter((component) => component.total > 0);

    return (
        <div className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                    label="Spent"
                    value={formatPaise(spent)}
                    sub={`Last ${days} days`}
                />
                <StatTile label="Balance" value={formatPaise(balance)} />
                <StatTile
                    label="Daily average"
                    value={formatPaise(data?.burn?.daily_average_paise ?? 0)}
                    // Averaged over the whole window, quiet days included: an
                    // account that calls on weekdays only still runs out on a
                    // Sunday, and a projection that skips the quiet days is
                    // optimistic in the direction that hurts.
                    sub="Across every day in the window, quiet ones included"
                />
                <StatTile
                    label="Credit lasts"
                    value={daysRemaining === null ? "—" : `${daysRemaining} days`}
                    sub={
                        daysRemaining === null
                            ? "No spend to project from"
                            : "At the current burn rate"
                    }
                    tone={
                        daysRemaining !== null && daysRemaining <= 7
                            ? "critical"
                            : daysRemaining !== null && daysRemaining <= 21
                              ? "warning"
                              : undefined
                    }
                />
            </div>

            {/* Stacked in receipt order — the four pass-through costs, then our
                platform fee last, so the stack reads cost-then-margin bottom to
                top. Colour follows the component and never its rank, so
                filtering never repaints the chart. */}
            <ChartCard
                title="Where the money goes"
                description="Daily spend split by component"
                isEmpty={byComponent.length === 0}
                height={300}
            >
                <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                        <YAxis
                            width={62}
                            tickFormatter={(v: number) => formatPaiseCompact(v)}
                            {...axisProps(mode)}
                        />
                        <RTooltip
                            content={
                                <ChartTooltip
                                    formatter={(v) => formatPaise(Number(v))}
                                    labelFormatter={formatDateIST}
                                />
                            }
                        />
                        <Legend
                            content={
                                <OrderedLegend
                                    items={COST_COMPONENTS.map((component) => ({
                                        key: component.key,
                                        label: component.label,
                                        color: seriesColor(component.slot, mode),
                                    }))}
                                />
                            }
                        />
                        {COST_COMPONENTS.map((component) => (
                            <Area
                                key={component.key}
                                type="monotone"
                                dataKey={component.key}
                                name={component.label}
                                stackId="cost"
                                stroke={seriesColor(component.slot, mode)}
                                fill={seriesColor(component.slot, mode)}
                                fillOpacity={0.35}
                            />
                        ))}
                    </AreaChart>
                </ResponsiveContainer>
            </ChartCard>

            <Card>
                <CardHeader>
                    <CardTitle>By component</CardTitle>
                </CardHeader>
                <CardContent>
                    {byComponent.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            No spend in this period.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Component</TableHead>
                                    <TableHead className="text-right">Spend</TableHead>
                                    <TableHead className="text-right">Share</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {byComponent.map((component) => (
                                    <TableRow key={component.key}>
                                        <TableCell className="font-medium">
                                            <span className="flex items-center gap-2">
                                                <span
                                                    aria-hidden
                                                    className="inline-block h-2 w-2 rounded-[2px]"
                                                    style={{
                                                        background: seriesColor(
                                                            component.slot,
                                                            mode,
                                                        ),
                                                    }}
                                                />
                                                {component.label}
                                            </span>
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(component.total)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {spent
                                                ? `${((component.total / spent) * 100).toFixed(
                                                      1,
                                                  )}%`
                                                : "—"}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                    <p className="mt-4 text-xs text-muted-foreground">
                        Components running on your own provider keys are billed by that
                        provider directly and never appear here.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
}
