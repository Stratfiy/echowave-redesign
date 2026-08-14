"use client";

/**
 * Token consumption, and the one ratio that predicts a bill.
 *
 * Token counts have always been stored — `call_cost_items.units` for the LLM
 * component is the raw count — and were only ever rendered as money. Money
 * conflates two different things: how busy the platform was, and how expensive
 * the agent design is. Rupees go up when you sell more, which tells you
 * nothing about whether a prompt change was a good idea.
 *
 * **Tokens per minute of conversation** separates them. It is flat when volume
 * grows and moves when the agent changes, which makes it the number to watch
 * after editing a prompt, swapping a model, or adding a tool. It is also
 * comparable across accounts and across months in a way spend is not.
 *
 * The effective price per 1k is derived from what was actually spent rather
 * than read back from the rate card, so a model priced one way and billed
 * another shows up here as the discrepancy it is.
 */

import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Line,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getTokensApiV1AdminBillingTokensGet } from "@/client/sdk.gen";
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
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
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

const GRANULARITIES = [
    { key: "day", label: "Daily" },
    { key: "week", label: "Weekly" },
    { key: "month", label: "Monthly" },
] as const;

type Granularity = (typeof GRANULARITIES)[number]["key"];

type TokenRow = {
    period: string;
    tokens: number;
    cost_paise: number;
    calls: number;
    minutes: number;
    tokens_per_minute: number | null;
};

type GrowthRow = {
    turn: number;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    cached_tokens: number;
    turns: number;
};

type ModelRow = {
    provider: string;
    model: string;
    tokens: number;
    cost_paise: number;
    calls: number;
    tokens_per_call: number | null;
    paise_per_1k_tokens: number | null;
};

function compactTokens(value: number | null | undefined): string {
    if (value === null || value === undefined) return "—";
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
    return String(value);
}

export default function TokensPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();

    const [granularity, setGranularity] = useState<Granularity>("day");
    const [data, setData] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getTokensApiV1AdminBillingTokensGet({
                query: { granularity },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load token usage"));
            } else {
                setData((result.data as Record<string, unknown>) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, granularity]);

    if (loading && !data) return <LoadingBlock label="Loading token usage" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const series = (data?.series ?? []) as TokenRow[];
    const byModel = (data?.by_model ?? []) as ModelRow[];
    const growth = (data?.context_growth ?? {}) as {
        series?: GrowthRow[];
        first_turn_prompt_tokens?: number | null;
        last_turn_prompt_tokens?: number | null;
        growth_multiple?: number | null;
        deepest_turn?: number | null;
        cache_hit_rate?: number | null;
    };
    const growthSeries = growth.series ?? [];

    // The blend assumption, measured. Every LLM margin figure on every screen
    // rests on LLM_INPUT_SHARE, and until this the only way to check it was to
    // read the constant and believe it.
    const inputShare = (data?.input_share ?? {}) as {
        assumed?: number;
        observed?: number | null;
        drift?: number | null;
        cached_share?: number | null;
        turns?: number;
    };

    const totalTokens = series.reduce((sum, r) => sum + r.tokens, 0);
    const totalCost = series.reduce((sum, r) => sum + r.cost_paise, 0);
    const totalMinutes = series.reduce((sum, r) => sum + r.minutes, 0);
    // Computed from the totals, not averaged across periods: a mean of ratios
    // weights a quiet Sunday the same as a busy Monday.
    const overallPerMinute = totalMinutes
        ? Math.round(totalTokens / totalMinutes)
        : null;

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-2">
                <Select
                    value={granularity}
                    onValueChange={(v) => setGranularity(v as Granularity)}
                >
                    <SelectTrigger className="w-[150px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {GRANULARITIES.map((g) => (
                            <SelectItem key={g.key} value={g.key}>
                                {g.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground">
                    Tokens are attributed to the day the call happened, not the day
                    costing ran — so a recosted call does not move periods.
                </span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile label="Tokens" value={compactTokens(totalTokens)} />
                <StatTile
                    label="Tokens per minute"
                    value={
                        overallPerMinute === null
                            ? "—"
                            : formatNumber(overallPerMinute)
                    }
                    sub="The number that moves when the agent changes, not when volume does"
                />
                <StatTile label="LLM spend" value={formatPaise(totalCost)} />
                <StatTile
                    label="Conversation"
                    value={`${formatNumber(Math.round(totalMinutes))} min`}
                />
            </div>

            {/* Placed above the charts because it is the figure the charts
                are denominated in: if the blend is wrong, every cost and
                margin below it is wrong by the same proportion. */}
            {inputShare.observed !== null && inputShare.observed !== undefined && (
                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-[0.9375rem]">
                            Input share — assumed vs measured
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
                            <div>
                                <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                    Assumed
                                </p>
                                <p className="text-2xl font-semibold tabular-nums leading-tight tracking-[-0.03em]">
                                    {((inputShare.assumed ?? 0) * 100).toFixed(0)}%
                                </p>
                            </div>
                            <div>
                                <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                    Measured
                                </p>
                                <p className="text-2xl font-semibold tabular-nums leading-tight tracking-[-0.03em]">
                                    {(inputShare.observed * 100).toFixed(1)}%
                                </p>
                            </div>
                            {inputShare.cached_share !== null &&
                                inputShare.cached_share !== undefined && (
                                    <div>
                                        <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                            Of input, cached
                                        </p>
                                        <p className="text-2xl font-semibold tabular-nums leading-tight tracking-[-0.03em]">
                                            {(inputShare.cached_share * 100).toFixed(0)}%
                                        </p>
                                    </div>
                                )}
                            <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
                                {Math.abs(inputShare.drift ?? 0) < 0.05
                                    ? `The assumption holds, across ${formatNumber(inputShare.turns ?? 0)} turns. Nothing to change.`
                                    : (inputShare.drift ?? 0) > 0
                                      ? `We assume more input than there is. Output costs several times input, so every LLM cost here is understated and every margin overstated. Lower LLM_INPUT_SHARE to ${(inputShare.observed).toFixed(2)}.`
                                      : `We assume less input than there is, so LLM costs here are overstated and margins understated. Raise LLM_INPUT_SHARE to ${(inputShare.observed).toFixed(2)}.`}
                            </p>
                        </div>
                    </CardContent>
                </Card>
            )}

            <ChartCard
                title="Tokens over time"
                description="Consumption by period, with cost alongside"
                isEmpty={series.length === 0}
                height={300}
            >
                <ResponsiveContainer width="100%" height={300}>
                    <BarChart
                        data={series}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis
                            dataKey="period"
                            tickFormatter={formatDateIST}
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

            <ChartCard
                title="Tokens per minute"
                description="Flat when volume grows; moves when the agent design does"
                isEmpty={series.every((r) => r.tokens_per_minute === null)}
                height={260}
            >
                <ResponsiveContainer width="100%" height={260}>
                    <BarChart
                        data={series}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis
                            dataKey="period"
                            tickFormatter={formatDateIST}
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
                                    formatter={(v) => formatNumber(Number(v))}
                                    labelFormatter={formatDateIST}
                                />
                            }
                        />
                        <Line
                            type="monotone"
                            dataKey="tokens_per_minute"
                            name="Tokens/min"
                            stroke={seriesColor(1, mode)}
                            strokeWidth={2}
                            dot={false}
                        />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {/* The chart no competitor draws.
                A voice agent resends the whole conversation every turn, so the
                prompt grows with turn index and language-model spend grows with
                the *square* of call length — a call twice as long costs closer
                to four times as much, not twice. A call-wide total cannot show
                this: ten short exchanges and three long ones sum identically.
                The fixes are structural (summarise older turns, trim the system
                prompt, enable prompt caching) and none appears as a line item
                on any invoice. */}
            <ChartCard
                title="Context growth per turn"
                description="Median prompt size as a conversation goes on — the cost that compounds"
                isEmpty={growthSeries.length === 0}
                emptyMessage="No per-turn token usage recorded yet. Calls placed from now on will populate this."
                height={300}
            >
                <ResponsiveContainer width="100%" height={300}>
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
                        value={
                            growth.growth_multiple
                                ? `${growth.growth_multiple}×`
                                : "—"
                        }
                        sub={`Turn 1 sends ${compactTokens(
                            growth.first_turn_prompt_tokens,
                        )}; turn ${growth.deepest_turn ?? "—"} sends ${compactTokens(
                            growth.last_turn_prompt_tokens,
                        )}`}
                        tone={
                            (growth.growth_multiple ?? 0) >= 4 ? "warning" : undefined
                        }
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
                            No LLM usage in this period.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Provider</TableHead>
                                    <TableHead>Model</TableHead>
                                    <TableHead className="text-right">Tokens</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                    <TableHead className="text-right">
                                        Tokens/call
                                    </TableHead>
                                    <TableHead className="text-right">Spend</TableHead>
                                    <TableHead className="text-right">
                                        Effective /1k
                                    </TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {byModel.map((row) => (
                                    <TableRow key={`${row.provider}-${row.model}`}>
                                        <TableCell>{row.provider}</TableCell>
                                        <TableCell className="font-medium">
                                            {row.model}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {compactTokens(row.tokens)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(row.calls)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(row.tokens_per_call)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(row.cost_paise)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {row.paise_per_1k_tokens === null
                                                ? "—"
                                                : `${row.paise_per_1k_tokens.toFixed(2)}p`}
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
