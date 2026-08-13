"use client";

/**
 * What the agents consumed, in tokens rather than rupees.
 *
 * Money conflates two different things: how busy the account was, and how
 * expensive the agent design is. Rupees go up when you make more calls, which
 * says nothing about whether a prompt change was a good idea.
 *
 * **Tokens per minute of conversation** separates them — flat when volume
 * grows, moving when the agent changes — so it is the number to watch after
 * editing a prompt, swapping a model, or adding a tool.
 *
 * The per-model split is the half a customer previously could not get at all:
 * a total tells you spend went up, the split tells you which model did it and
 * whether a cheaper one would serve.
 */

import { AlertTriangle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getTokenUsageApiV1OrganizationsUsageTokensGet } from "@/client/sdk.gen";
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
import { formatDateIST, formatNumber } from "@/lib/billing/format";

type TokenRow = {
    period: string;
    tokens: number;
    calls: number;
    minutes: number;
    tokens_per_minute: number | null;
};

/**
 * No money here, deliberately.
 *
 * Spend per named model divides into a unit price, and this account's models
 * have public rate cards — so a spend column on this table publishes what we
 * charge over the vendor. The bill, split by component, is on the Spend tab.
 *
 * Nothing is lost for the reader: the question this table answers — which
 * model is burning the context, would a cheaper one serve — is a question
 * about tokens.
 */
type ModelRow = {
    provider: string;
    model: string;
    tokens: number;
    calls: number;
    tokens_per_call: number | null;
};

type GrowthRow = {
    turn: number;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    cached_tokens: number;
    turns: number;
};

type TokenUsage = {
    series: TokenRow[];
    by_model: ModelRow[];
    context_growth: {
        series?: GrowthRow[];
        first_turn_prompt_tokens?: number | null;
        last_turn_prompt_tokens?: number | null;
        growth_multiple?: number | null;
        deepest_turn?: number | null;
        cache_hit_rate?: number | null;
    };
};

function compactTokens(value: number | null | undefined): string {
    if (value === null || value === undefined) return "—";
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
    return String(value);
}

export default function CustomerTokensPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();
    const searchParams = useSearchParams();
    const days = Number(searchParams.get("days")) || 30;

    const [data, setData] = useState<TokenUsage | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getTokenUsageApiV1OrganizationsUsageTokensGet({
                query: { days, granularity: "day" },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load token usage"));
            } else {
                setData((result.data as unknown as TokenUsage) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, days]);

    if (loading && !data) return <LoadingBlock label="Loading token usage" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const series = data?.series ?? [];
    const byModel = data?.by_model ?? [];
    const growth = data?.context_growth ?? {};
    const growthSeries = growth.series ?? [];

    const totalTokens = series.reduce((sum, r) => sum + r.tokens, 0);
    const totalMinutes = series.reduce((sum, r) => sum + r.minutes, 0);
    // From the totals, not a mean of the per-day ratios: that would weight a
    // quiet Sunday the same as a busy Monday.
    const overallPerMinute = totalMinutes
        ? Math.round(totalTokens / totalMinutes)
        : null;

    return (
        <div className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile label="Tokens" value={compactTokens(totalTokens)} />
                <StatTile
                    label="Tokens per minute"
                    value={
                        overallPerMinute === null ? "—" : formatNumber(overallPerMinute)
                    }
                    sub="Moves when the agent changes, not when volume does"
                />
                <StatTile
                    label="Conversation"
                    value={`${formatNumber(Math.round(totalMinutes))} min`}
                />
            </div>

            <ChartCard
                title="Tokens over time"
                description="Attributed to the day the call happened, not the day it was costed"
                isEmpty={series.length === 0 || series.every((r) => r.tokens === 0)}
                height={280}
            >
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis dataKey="period" tickFormatter={formatDateIST} {...axisProps(mode)} />
                        <YAxis
                            width={62}
                            tickFormatter={(v: number) => compactTokens(v)}
                            {...axisProps(mode)}
                        />
                        <RTooltip
                            content={
                                <ChartTooltip
                                    formatter={(v) => compactTokens(Number(v))}
                                    labelFormatter={formatDateIST}
                                />
                            }
                        />
                        <Bar
                            dataKey="tokens"
                            name="Tokens"
                            fill={seriesColor(0, mode)}
                            radius={[3, 3, 0, 0]}
                        />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {/* A voice agent resends the whole conversation every turn, so the
                prompt grows with turn index and language-model spend grows with
                the *square* of call length. A per-call total cannot show this:
                ten short exchanges and three long ones sum identically. The
                fixes are structural — summarise older turns, trim the system
                prompt, enable prompt caching — and none of them appears as a
                line item on any invoice. */}
            <ChartCard
                title="Context growth per turn"
                description="Median prompt size as a conversation goes on — the cost that compounds"
                isEmpty={growthSeries.length === 0}
                emptyMessage="No per-turn token usage recorded yet. Calls placed from now on will populate this."
                height={280}
            >
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart
                        data={growthSeries}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis
                            dataKey="turn"
                            tickFormatter={(v: number) => `Turn ${v}`}
                            {...axisProps(mode)}
                        />
                        <YAxis
                            width={62}
                            tickFormatter={(v: number) => compactTokens(v)}
                            {...axisProps(mode)}
                        />
                        <RTooltip
                            content={
                                <ChartTooltip
                                    formatter={(v) => compactTokens(Number(v))}
                                    labelFormatter={(l) => `Turn ${l}`}
                                />
                            }
                        />
                        <Bar
                            dataKey="prompt_tokens"
                            name="Prompt (context)"
                            fill={seriesColor(0, mode)}
                            radius={[3, 3, 0, 0]}
                        />
                        <Bar
                            dataKey="completion_tokens"
                            name="Completion"
                            fill={seriesColor(2, mode)}
                            radius={[3, 3, 0, 0]}
                        />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {growthSeries.length > 0 && (
                <div className="grid gap-3 sm:grid-cols-3">
                    <StatTile
                        label="Context growth"
                        value={growth.growth_multiple ? `${growth.growth_multiple}×` : "—"}
                        sub={`Turn 1 sends ${compactTokens(
                            growth.first_turn_prompt_tokens,
                        )}; turn ${growth.deepest_turn ?? "—"} sends ${compactTokens(
                            growth.last_turn_prompt_tokens,
                        )}`}
                        tone={(growth.growth_multiple ?? 0) >= 4 ? "warning" : undefined}
                    />
                    <StatTile
                        label="Prompt cache hit rate"
                        value={
                            growth.cache_hit_rate === null ||
                            growth.cache_hit_rate === undefined
                                ? "Not reported"
                                : `${(growth.cache_hit_rate * 100).toFixed(0)}%`
                        }
                        sub="Caching cuts the cost of resent context by most of its value"
                        tone={
                            growth.cache_hit_rate !== null &&
                            growth.cache_hit_rate !== undefined &&
                            growth.cache_hit_rate < 0.2
                                ? "warning"
                                : undefined
                        }
                    />
                    <StatTile
                        label="Turns measured"
                        value={formatNumber(
                            growthSeries.reduce((sum, r) => sum + r.turns, 0),
                        )}
                        sub={`Across ${growthSeries.length} turn position${
                            growthSeries.length === 1 ? "" : "s"
                        }`}
                    />
                </div>
            )}

            <Card>
                <CardHeader>
                    <CardTitle>By model</CardTitle>
                </CardHeader>
                <CardContent>
                    {byModel.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            No language-model usage in this period. Agents running on your
                            own keys are billed by the provider directly and do not appear
                            here.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Provider</TableHead>
                                    <TableHead>Model</TableHead>
                                    <TableHead className="text-right">Tokens</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                    <TableHead className="text-right">Tokens/call</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {byModel.map((row) => (
                                    <TableRow key={`${row.provider}-${row.model}`}>
                                        <TableCell>{row.provider}</TableCell>
                                        <TableCell className="font-medium">{row.model}</TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {compactTokens(row.tokens)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(row.calls)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(row.tokens_per_call)}
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
