"use client";

import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    Line,
    LineChart,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getLatencyApiV1AdminBillingLatencyGet } from "@/client/sdk.gen";
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
import { formatDateIST, formatDateTimeIST, formatMs, formatNumber } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

import { LATENCY_TARGET_MS, seriesColor } from "../_components/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    LoadingBlock,
    OrderedLegend,
    PanelMessage,
    useAuthReady,
    useChartMode,
} from "../_components/primitives";

/** Median duration of each pipeline stage, in the order a turn flows through. */
const STAGES = [
    { key: "endpointing_ms", label: "Endpointing", slot: 0 },
    { key: "stt_ms", label: "STT", slot: 1 },
    { key: "llm_ms", label: "LLM", slot: 2 },
    { key: "tts_ms", label: "TTS", slot: 3 },
    { key: "playback_ms", label: "Playback", slot: 4 },
] as const;

export default function LatencyPage() {
    const mode = useChartMode();
    const [data, setData] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [language, setLanguage] = useState("all");

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getLatencyApiV1AdminBillingLatencyGet({
                query: language !== "all" ? { language } : {},
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load latency"));
            } else {
                setData((result.data as Record<string, unknown>) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, language]);

    if (loading && !data) return <LoadingBlock label="Loading latency" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const series = (data?.series ?? []) as Array<Record<string, never>>;
    const byLanguage = (data?.by_language ?? []) as Array<Record<string, never>>;
    const stageMedians = (data?.stage_medians ?? {}) as Record<string, number>;
    const slowest = (data?.slowest_turns ?? []) as Array<Record<string, never>>;
    const languages = (data?.languages ?? []) as string[];

    const stageData = [
        {
            name: "Median turn",
            ...STAGES.reduce(
                (acc, stage) => ({ ...acc, [stage.key]: stageMedians[stage.key] ?? 0 }),
                {},
            ),
        },
    ];
    const hasStages = STAGES.some((s) => (stageMedians[s.key] ?? 0) > 0);

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-2">
                <Select value={language} onValueChange={setLanguage}>
                    <SelectTrigger className="w-[180px]">
                        <SelectValue placeholder="All languages" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All languages</SelectItem>
                        {languages.map((l) => (
                            <SelectItem key={l} value={l}>
                                {l}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground">
                    Perceived latency is the gap between the caller finishing and audio
                    leaving — target {LATENCY_TARGET_MS}ms.
                </span>
            </div>

            <ChartCard
                title="p50 and p95 over time"
                description="Percentiles computed over raw turns, never averaged from buckets"
                isEmpty={series.length === 0}
                height={300}
            >
                <ResponsiveContainer width="100%" height={300}>
                    <LineChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                        <YAxis width={62} tickFormatter={(v: number) => formatMs(v)} {...axisProps(mode)} />
                        <RTooltip
                            content={
                                <ChartTooltip
                                    formatter={(v) => formatMs(v)}
                                    labelFormatter={formatDateIST}
                                />
                            }
                        />
                        <Legend iconType="plainline" iconSize={12} wrapperStyle={{ fontSize: 11 }} />
                        <ReferenceLine
                            y={LATENCY_TARGET_MS}
                            stroke={axisProps(mode).stroke}
                            strokeDasharray="4 4"
                            // No inline label: outside the plot it gets clipped,
                            // inside it lands on the p50 line. The caption above
                            // the chart already names the target.
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

            <div className="grid gap-4 lg:grid-cols-2">
                <ChartCard
                    title="Where the time goes"
                    description="Median duration of each pipeline stage"
                    isEmpty={!hasStages}
                >
                    <ResponsiveContainer width="100%" height={220}>
                        <BarChart
                            data={stageData}
                            layout="vertical"
                            margin={{ top: 8, right: 8, bottom: 0, left: 8 }}
                        >
                            <CartesianGrid stroke={gridStroke(mode)} horizontal={false} />
                            <XAxis
                                type="number"
                                tickFormatter={(v: number) => formatMs(v)}
                                {...axisProps(mode)}
                            />
                            <YAxis type="category" dataKey="name" width={90} {...axisProps(mode)} />
                            <RTooltip
                                cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                                content={<ChartTooltip formatter={(v) => formatMs(v)} />}
                            />
                            <Legend
                                content={
                                    <OrderedLegend
                                        items={STAGES.map((s) => ({
                                            key: s.key,
                                            label: s.label,
                                            color: seriesColor(s.slot, mode),
                                        }))}
                                    />
                                }
                            />
                            {STAGES.map((stage) => (
                                <Bar
                                    key={stage.key}
                                    dataKey={stage.key}
                                    name={stage.label}
                                    stackId="stages"
                                    fill={seriesColor(stage.slot, mode)}
                                    maxBarSize={48}
                                />
                            ))}
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Latency by language"
                    description="p50 per language"
                    isEmpty={byLanguage.length === 0}
                >
                    <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={byLanguage} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="language" {...axisProps(mode)} />
                            <YAxis width={62} tickFormatter={(v: number) => formatMs(v)} {...axisProps(mode)} />
                            <RTooltip
                                cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                                content={<ChartTooltip formatter={(v) => formatMs(v)} />}
                            />
                            <ReferenceLine
                                y={LATENCY_TARGET_MS}
                                stroke={axisProps(mode).stroke}
                                strokeDasharray="4 4"
                            />
                            <Bar dataKey="p50_ms" name="p50" radius={[4, 4, 0, 0]} maxBarSize={40}>
                                {byLanguage.map((row, index) => (
                                    <Cell
                                        key={index}
                                        fill={seriesColor(0, mode)}
                                        fillOpacity={
                                            Number(row.p50_ms) > LATENCY_TARGET_MS ? 1 : 0.55
                                        }
                                    />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>
            </div>

            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium">Slowest 20 turns</CardTitle>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        Follow a link to the call to listen to what happened.
                    </p>
                </CardHeader>
                <CardContent>
                    {slowest.length === 0 ? (
                        <PanelMessage height={140}>No turns recorded in this period.</PanelMessage>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>When</TableHead>
                                        <TableHead>Account</TableHead>
                                        <TableHead>Language</TableHead>
                                        <TableHead className="text-right">Turn</TableHead>
                                        <TableHead className="text-right">Latency</TableHead>
                                        <TableHead />
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {slowest.map((turn, index) => (
                                        <TableRow key={index}>
                                            <TableCell className="whitespace-nowrap text-muted-foreground">
                                                {formatDateTimeIST(String(turn.created_at ?? ""))}
                                            </TableCell>
                                            <TableCell>{String(turn.account ?? "—")}</TableCell>
                                            <TableCell className="text-muted-foreground">
                                                {String(turn.language ?? "—")}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatNumber(Number(turn.turn_index) + 1)}
                                            </TableCell>
                                            <TableCell
                                                className={cn(
                                                    "text-right font-medium tabular-nums",
                                                    Number(turn.latency_ms) > LATENCY_TARGET_MS &&
                                                        "text-destructive",
                                                )}
                                            >
                                                {formatMs(Number(turn.latency_ms))}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <Link
                                                    href={`/superadmin/billing/calls/${turn.workflow_run_id}`}
                                                    className="text-xs text-primary hover:underline"
                                                >
                                                    View call
                                                </Link>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
