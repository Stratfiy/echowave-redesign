"use client";

/**
 * What this bot has been doing, and what it has cost.
 *
 * The account had a usage screen and a run could show its own cost, but a
 * bot had no screen answering "is this one earning its keep" — which is the
 * question somebody asks the week before they decide whether to keep paying
 * for it. Every figure here is the account's existing call analytics narrowed
 * to one bot, plus two the account-wide answer cannot give: this bot's runs
 * per day, and the tokens its brains consumed.
 *
 * Runs, not "executions". A call and a chat turn are both runs everywhere
 * else in the product, and a fourth word for the same row would only be a
 * fourth word.
 */

import { useCallback, useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getCallAnalyticsApiV1OrganizationsUsageCallsGet } from "@/client/sdk.gen";
import { SERIES_DARK, SERIES_LIGHT } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    gridStroke,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { formatPaise } from "@/lib/billing/format";
import { PRICES_SHOWN } from "@/lib/pricing";
import { cn } from "@/lib/utils";

type DailyRun = {
    day: string;
    runs: number;
    answered: number;
    billable_seconds: number;
    charged_paise: number;
};

type Tokens = {
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    by_model: { model: string; total_tokens: number; prompt_tokens: number; completion_tokens: number }[];
};

type Analytics = {
    totals: {
        calls: number;
        answered: number;
        billable_seconds: number;
        charged_paise: number;
        average_seconds: number | null;
        answer_rate: number | null;
    };
    daily_runs?: DailyRun[];
    tokens?: Tokens;
};

/** The windows worth offering: this week, this month, this quarter. */
const RANGES = [
    { days: 7, label: "7 days" },
    { days: 30, label: "30 days" },
    { days: 90, label: "90 days" },
] as const;

const count = (value: number) => value.toLocaleString();

function minutes(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    return `${Math.round(seconds / 60).toLocaleString()} min`;
}

/** A day as "5 Sep", which is how the bars are read left to right. */
function dayLabel(iso: string): string {
    const date = new Date(`${iso}T00:00:00`);
    return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function BotAnalytics({ workflowId }: { workflowId: number }) {
    const ready = useAuthReady();
    const mode = useChartMode();
    const [days, setDays] = useState<number>(30);
    const [data, setData] = useState<Analytics | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        const response = await getCallAnalyticsApiV1OrganizationsUsageCallsGet({
            query: { days, workflow_id: workflowId },
        });
        setLoading(false);
        if (response.error || !response.data) {
            setError("These numbers could not be read just now.");
            return;
        }
        setData(response.data as unknown as Analytics);
    }, [days, workflowId]);

    useEffect(() => {
        if (!ready) return;
        void load();
    }, [ready, load]);

    const totals = data?.totals;
    const daily = data?.daily_runs ?? [];
    const tokens = data?.tokens;
    const series = mode === "dark" ? SERIES_DARK : SERIES_LIGHT;

    return (
        <div className="space-y-4 px-6 py-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-semibold">What it has been doing</h2>
                    <p className="text-sm text-muted-foreground">
                        This agent only. Nothing here is the account&apos;s total.
                    </p>
                </div>
                <div className="flex gap-2" role="group" aria-label="Period">
                    {RANGES.map((range) => (
                        <button
                            key={range.days}
                            type="button"
                            aria-pressed={days === range.days}
                            onClick={() => setDays(range.days)}
                            className={cn(
                                "rounded-full border px-3 py-1 text-sm transition-colors",
                                days === range.days
                                    ? "border-foreground bg-foreground text-background"
                                    : "border-border bg-card hover:bg-muted/40",
                            )}
                        >
                            {range.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile label="Runs" value={count(totals?.calls ?? 0)} />
                <StatTile
                    label="Answered"
                    value={
                        totals?.answer_rate === null || totals?.answer_rate === undefined
                            ? "—"
                            : `${Math.round(totals.answer_rate * 100)}%`
                    }
                    sub={totals ? `${count(totals.answered)} of ${count(totals.calls)}` : undefined}
                />
                <StatTile
                    label="Time on calls"
                    value={minutes(totals?.billable_seconds ?? 0)}
                    sub={
                        totals?.average_seconds
                            ? `${totals.average_seconds}s on average`
                            : undefined
                    }
                />
                {/* No pricing is shown to users (lib/pricing.ts). */}
                {PRICES_SHOWN && <StatTile label="Spent" value={formatPaise(totals?.charged_paise ?? 0)} />}
            </div>

            <ChartCard
                title="Runs a day"
                description="Every day in the window, including the quiet ones."
                loading={loading}
                error={error}
                isEmpty={daily.every((row) => row.runs === 0)}
                emptyMessage="This agent has not run in this window."
            >
                <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={daily.map((row) => ({ ...row, label: dayLabel(row.day) }))}>
                        <CartesianGrid vertical={false} stroke={gridStroke(mode)} />
                        <XAxis dataKey="label" {...axisProps(mode)} />
                        <YAxis allowDecimals={false} {...axisProps(mode)} />
                        <Tooltip
                            cursor={{ fill: gridStroke(mode) }}
                            contentStyle={{ fontSize: 12 }}
                        />
                        <Bar dataKey="runs" name="Runs" fill={series[0]} radius={[4, 4, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            <ChartCard
                title="Tokens its brains used"
                description="What the models read and wrote for this agent, by model."
                loading={loading}
                error={error}
                isEmpty={!tokens || tokens.total_tokens === 0}
                emptyMessage="No model usage recorded in this window."
                height={160}
            >
                <div className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-3">
                        <StatTile label="Total tokens" value={count(tokens?.total_tokens ?? 0)} />
                        <StatTile label="Read" value={count(tokens?.prompt_tokens ?? 0)} />
                        <StatTile label="Written" value={count(tokens?.completion_tokens ?? 0)} />
                    </div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left text-xs uppercase tracking-wider text-muted-foreground">
                                <th className="py-1 font-medium">Model</th>
                                <th className="py-1 text-right font-medium">Read</th>
                                <th className="py-1 text-right font-medium">Written</th>
                                <th className="py-1 text-right font-medium">Total</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(tokens?.by_model ?? []).map((row) => (
                                <tr key={row.model} className="border-t border-border/60">
                                    <td className="py-1.5">{row.model}</td>
                                    <td className="py-1.5 text-right tabular-nums">
                                        {count(row.prompt_tokens)}
                                    </td>
                                    <td className="py-1.5 text-right tabular-nums">
                                        {count(row.completion_tokens)}
                                    </td>
                                    <td className="py-1.5 text-right font-medium tabular-nums">
                                        {count(row.total_tokens)}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </ChartCard>
        </div>
    );
}
