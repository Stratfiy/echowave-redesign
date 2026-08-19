"use client";

/**
 * The physical facts a price has to be set against.
 *
 * Every other screen in this section answers "what did we earn". This one
 * answers the question that comes first: what is actually true about the
 * calls, so a price can be set rather than estimated.
 *
 * It exists because three internal documents assumed 850, 900 and 2,300 TTS
 * characters per minute — a 2.7x spread nobody had ever measured — and every
 * margin figure on the screens beside this one rests on which is right. TTS is
 * quoted per 1,000 characters and is the largest line on an Indic call, so an
 * error there scales straight into the blended cost.
 *
 * Two decisions carry the screen:
 *
 * **Medians, not averages.** One call where the agent read a policy document
 * aloud moves a mean by a third and quietly re-prices the product. The p90 is
 * shown beside it because worst-case margin and capacity are set by the tail.
 *
 * **Rate-card gaps are drawn as a failure, not a table row.** A component with
 * no rate row does not error — it bills zero, marks the run uncosted, and
 * inflates margin by exactly what it failed to charge. That is how a model can
 * be resold below cost for a year with nothing going red anywhere.
 */

import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { client } from "@/client/client.gen";
import { seriesColor, STATUS } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    gridStroke,
    LoadingBlock,
    PanelMessage,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import { formatNumber, formatPaise } from "@/lib/billing/format";

type CharsRow = {
    provider: string;
    model: string;
    calls: number;
    avg_call_seconds: number;
    median_chars_per_minute: number;
    mean_chars_per_minute: number;
    p90_chars_per_minute: number;
};

type LanguageRow = {
    language: string;
    calls: number;
    median_chars_per_minute: number;
    p90_chars_per_minute: number;
};

type GapRow = {
    provider: string;
    model: string;
    component: string;
    cost_items: number;
    units: number;
    charged_paise: number;
};

type AccountRow = {
    month: string | null;
    organization_id: number;
    calls: number;
    minutes: number;
    charged_paise: number;
};

type Report = {
    tts_chars_per_minute: {
        headline: {
            measured_chars_per_minute: number;
            calls: number;
            assumptions: { assumed: number; error_pct: number | null }[];
        } | null;
        by_model: CharsRow[];
        by_language: LanguageRow[];
    };
    rate_card_gaps: GapRow[];
    peak_concurrency: { series: { day: string; peak: number }[]; peak: number };
    monthly_minutes_by_account: AccountRow[];
};

/** "sarvam · bulbul:v2", or just the provider where there is no model. */
function rowName(row: { provider: string; model: string }): string {
    if (!row.provider) return row.model || "—";
    return row.model ? `${row.provider} · ${row.model}` : row.provider;
}

export default function PricingInputsPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();

    const [data, setData] = useState<Report | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        // Not in the generated SDK yet — the route is newer than the last
        // client generation. The generated client resolves rather than throws
        // on a 4xx/5xx, so the error is checked explicitly below.
        const result = await client.get({
            url: "/api/v1/admin/billing/pricing-inputs",
        });
        if (result.error) {
            setError(detailFromError(result.error, "Failed to load pricing inputs"));
        } else {
            setData((result.data as Report | undefined) ?? null);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    if (loading && !data) return <LoadingBlock label="Loading pricing inputs" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const chars = data?.tts_chars_per_minute;
    const headline = chars?.headline ?? null;
    const byModel = chars?.by_model ?? [];
    const byLanguage = chars?.by_language ?? [];
    const gaps = data?.rate_card_gaps ?? [];
    const concurrency = data?.peak_concurrency;
    const accounts = data?.monthly_minutes_by_account ?? [];

    // Latest month only. The bundle question is "what does an account use in a
    // month", and stacking six months of the same account answers a different
    // one.
    const latestMonth = accounts.length ? accounts[0].month : null;
    const thisMonth = accounts
        .filter((row) => row.month === latestMonth)
        .sort((a, b) => b.minutes - a.minutes);

    const medianAccountMinutes = thisMonth.length
        ? thisMonth[Math.floor(thisMonth.length / 2)].minutes
        : 0;

    return (
        <div className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatTile
                    label="TTS chars / minute"
                    value={
                        headline ? formatNumber(headline.measured_chars_per_minute) : "—"
                    }
                    sub={
                        headline
                            ? `measured across ${formatNumber(headline.calls)} calls`
                            : "not enough calls yet"
                    }
                />
                <StatTile
                    label="Components with no rate row"
                    value={formatNumber(gaps.length)}
                    sub={gaps.length ? "billing zero — see below" : "every component priced"}
                    tone={gaps.length ? "critical" : undefined}
                />
                <StatTile
                    label="Peak concurrency reached"
                    value={formatNumber(concurrency?.peak ?? 0)}
                    sub="the number a concurrency tier should be priced against"
                />
                <StatTile
                    label="Median account, this month"
                    value={`${formatNumber(medianAccountMinutes)} min`}
                    sub="what a bundle's balance has to be sized against"
                />
            </div>

            {headline && (
                <ChartCard
                    title="How wrong the assumptions were"
                    description="Each internal document's assumed characters per minute, against what the calls actually did. Positive means the assumption was high, so the cost model built on it over-states what a minute costs us."
                    height={200}
                    isEmpty={!headline.assumptions.length}
                >
                    <ResponsiveContainer width="100%" height={200}>
                        <BarChart
                            data={headline.assumptions.map((a) => ({
                                name: `${formatNumber(a.assumed)} assumed`,
                                error: a.error_pct ?? 0,
                            }))}
                            layout="vertical"
                            margin={{ left: 8, right: 24, top: 8, bottom: 8 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} horizontal={false} />
                            <XAxis type="number" unit="%" {...axisProps(mode)} />
                            <YAxis
                                type="category"
                                dataKey="name"
                                width={120}
                                {...axisProps(mode)}
                            />
                            <ReferenceLine x={0} stroke={gridStroke(mode)} />
                            <RTooltip
                                formatter={(value: number) => [`${value.toFixed(1)}%`, "Error"]}
                            />
                            <Bar dataKey="error" radius={[0, 4, 4, 0]}>
                                {headline.assumptions.map((a, i) => (
                                    <Cell
                                        key={i}
                                        fill={
                                            Math.abs(a.error_pct ?? 0) > 25
                                                ? STATUS.critical
                                                : seriesColor(0, mode)
                                        }
                                    />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>
            )}

            <ChartCard
                title="Characters per minute, by model"
                description="Median and p90. The median is what the rate card should be set from; the p90 is what a bad month looks like."
                isEmpty={!byModel.length}
                emptyMessage="No model has enough costed calls yet to take a percentile from."
                height={280}
            >
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart
                        data={byModel.map((row) => ({
                            name: rowName(row),
                            median: row.median_chars_per_minute,
                            p90: row.p90_chars_per_minute,
                        }))}
                        margin={{ left: 8, right: 8, top: 8, bottom: 8 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis dataKey="name" {...axisProps(mode)} />
                        <YAxis {...axisProps(mode)} />
                        <RTooltip />
                        <Bar dataKey="median" name="Median" fill={seriesColor(0, mode)} radius={[4, 4, 0, 0]} />
                        <Bar dataKey="p90" name="p90" fill={seriesColor(1, mode)} radius={[4, 4, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            <ChartCard
                title="Rate-card gaps"
                description="Provider and model combinations that appeared on a call with no live rate row. Each one bills zero and reports healthy."
                isEmpty={!gaps.length}
                emptyMessage="Every component that billed had a rate row. Nothing is silently free."
                height={200}
            >
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Provider / model</TableHead>
                            <TableHead>Component</TableHead>
                            <TableHead className="text-right">Cost items</TableHead>
                            <TableHead className="text-right">Units</TableHead>
                            <TableHead className="text-right">Charged</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {gaps.map((row, i) => (
                            <TableRow key={i}>
                                <TableCell className="font-medium">{rowName(row)}</TableCell>
                                <TableCell>{row.component}</TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {formatNumber(row.cost_items)}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {formatNumber(row.units)}
                                </TableCell>
                                <TableCell className="text-right tabular-nums text-destructive">
                                    {formatPaise(row.charged_paise)}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </ChartCard>

            <div className="grid gap-6 xl:grid-cols-2">
                <ChartCard
                    title="Peak concurrency by day"
                    description="What anyone has actually needed at once. The limiter's ceiling says what we permit, which is a different number."
                    isEmpty={!concurrency?.series.length}
                    height={240}
                >
                    <ResponsiveContainer width="100%" height={240}>
                        <AreaChart
                            data={concurrency?.series ?? []}
                            margin={{ left: 8, right: 8, top: 8, bottom: 8 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" {...axisProps(mode)} />
                            <YAxis allowDecimals={false} {...axisProps(mode)} />
                            <RTooltip />
                            <Area
                                type="monotone"
                                dataKey="peak"
                                name="Peak concurrent calls"
                                stroke={seriesColor(2, mode)}
                                fill={seriesColor(2, mode)}
                                fillOpacity={0.2}
                            />
                        </AreaChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Characters per minute, by language"
                    description="Devanagari and Telugu encode a syllable in fewer characters than the Latin transliteration of the same sentence, so a per-1k-character rate is not language-neutral even when the vendor's price is."
                    isEmpty={!byLanguage.length}
                    height={240}
                >
                    <ResponsiveContainer width="100%" height={240}>
                        <BarChart
                            data={byLanguage.map((row) => ({
                                name: row.language || "unset",
                                median: row.median_chars_per_minute,
                            }))}
                            margin={{ left: 8, right: 8, top: 8, bottom: 8 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="name" {...axisProps(mode)} />
                            <YAxis {...axisProps(mode)} />
                            <RTooltip />
                            <Bar
                                dataKey="median"
                                name="Median chars/min"
                                fill={seriesColor(3, mode)}
                                radius={[4, 4, 0, 0]}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>
            </div>

            <ChartCard
                title="Minutes per account, this month"
                description="What a bundle's included balance has to be sized against. A grant a typical account uses a fifth of reads as a waste; one they exhaust in a fortnight produces an overage conversation nobody enjoys."
                isEmpty={!thisMonth.length}
                height={240}
            >
                <ResponsiveContainer width="100%" height={240}>
                    <BarChart
                        data={thisMonth.slice(0, 25).map((row) => ({
                            name: `#${row.organization_id}`,
                            minutes: row.minutes,
                        }))}
                        margin={{ left: 8, right: 8, top: 8, bottom: 8 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis dataKey="name" {...axisProps(mode)} />
                        <YAxis {...axisProps(mode)} />
                        <RTooltip />
                        <Bar
                            dataKey="minutes"
                            name="Connected minutes"
                            fill={seriesColor(4, mode)}
                            radius={[4, 4, 0, 0]}
                        />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>
        </div>
    );
}
