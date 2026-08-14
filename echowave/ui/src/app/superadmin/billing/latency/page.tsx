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
import { LATENCY_TARGET_MS, seriesColor } from "@/components/charts/chartTheme";
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

/**
 * Median duration of each pipeline stage, in the order a turn flows through.
 *
 * The last bucket is **unattributed**, not playback. It is whatever is left of
 * the turn after the stages that report a mark, and it holds transport and
 * network transit, output queueing, and any gap the marks do not cover. Part
 * of it really is audio starting to play — but naming the whole remainder
 * after one of its parts sends anyone reading this chart to optimise audio
 * output when the time is usually somewhere else entirely.
 */
const STAGES = [
    { key: "endpointing_ms", label: "Endpointing", slot: 0 },
    { key: "stt_ms", label: "STT", slot: 1 },
    { key: "llm_ms", label: "LLM", slot: 2 },
    { key: "tts_ms", label: "TTS", slot: 3 },
    { key: "unattributed_ms", label: "Unattributed", slot: 4 },
] as const;

/**
 * The three numbers that all get called "latency".
 *
 * Keeping them apart is the point. TTFT is the model's thinking time and TTFB
 * the voice's; perceived is what the caller actually waits through and is
 * neither. A single "latency" figure cannot answer the only question worth
 * asking when it regresses — which of the two to go and fix.
 */
const MEASURES = [
    {
        key: "perceived",
        label: "Perceived",
        hint: "Caller stops speaking → audio leaves. What a complaint is about.",
    },
    {
        key: "ttft",
        label: "TTFT",
        hint: "Final transcript → first LLM token. The model's thinking time.",
    },
    {
        key: "ttfb",
        label: "TTFB",
        hint: "First token → first audio byte. The voice's.",
    },
] as const;

type MeasureKey = (typeof MEASURES)[number]["key"];

/** p99 earns its place: at twenty turns a call, a p95 breach lands roughly
 *  once per call, so p95 alone describes a call nobody enjoyed. */
const PERCENTILE_KEYS = ["p50_ms", "p90_ms", "p95_ms", "p99_ms"] as const;

export default function LatencyPage() {
    const mode = useChartMode();
    const [data, setData] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [language, setLanguage] = useState("all");
    const [measure, setMeasure] = useState<MeasureKey>("perceived");

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
    const percentiles = (data?.percentiles ?? {}) as Record<
        string,
        Array<Record<string, number | string>>
    >;
    const headline = (data?.headline ?? {}) as Record<
        string,
        Record<string, number | null>
    >;
    const tools = (data?.tools ?? []) as Array<Record<string, never>>;
    const byLanguage = (data?.by_language ?? []) as Array<Record<string, never>>;
    const stageMedians = (data?.stage_medians ?? {}) as Record<string, number>;
    // Endpointing split by language and transcriber. The global median above
    // averages a language paying no silence wait with one paying half a second
    // every turn, and describes neither.
    const endpointing = (data?.endpointing_by_language ?? []) as Array<{
        language: string;
        stt_model: string;
        waits_for_silence: boolean;
        p50_ms: number | null;
        p95_ms: number | null;
        turns: number;
        calls: number;
    }>;
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

    // Endpointing only became measurable once the pipeline started reporting a
    // turn-release mark, so a window spanning that change is part measured and
    // part not. Saying so is the difference between "endpointing is fast" and
    // "endpointing is mostly unmeasured here" — which look identical otherwise.
    const coverage = stageMedians.endpointing_coverage ?? null;
    const stageNote =
        coverage === null || coverage >= 0.99
            ? "Median duration of each pipeline stage"
            : `Median duration of each pipeline stage · endpointing measured on ${Math.round(
                  coverage * 100,
              )}% of turns`;

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

            {/* The two numbers every comparable platform headlines, which were
                only ever visible here folded into the stage bar — a median of
                stages averages away the tail and cannot separate the model
                from the voice. */}
            <div className="grid gap-3 sm:grid-cols-3">
                {MEASURES.map((m) => {
                    const stats = headline[m.key] ?? {};
                    return (
                        <StatTile
                            key={m.key}
                            label={`${m.label} p50`}
                            value={formatMs(stats.p50_ms ?? null)}
                            sub={`p95 ${formatMs(stats.p95_ms ?? null)} · p99 ${formatMs(
                                stats.p99_ms ?? null,
                            )} · ${m.hint}`}
                            tone={
                                m.key === "perceived" &&
                                (stats.p95_ms ?? 0) > LATENCY_TARGET_MS
                                    ? "warning"
                                    : undefined
                            }
                        />
                    );
                })}
            </div>

            <ChartCard
                title="Percentiles over time"
                description="p50, p90, p95 and p99 — computed over the whole window, not averaged from daily figures"
                isEmpty={(percentiles[measure] ?? []).length === 0}
                height={300}
                action={
                    <Select
                        value={measure}
                        onValueChange={(v) => setMeasure(v as MeasureKey)}
                    >
                        <SelectTrigger className="w-[150px]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {MEASURES.map((m) => (
                                <SelectItem key={m.key} value={m.key}>
                                    {m.label}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                }
            >
                <ResponsiveContainer width="100%" height={300}>
                    <LineChart
                        data={percentiles[measure] ?? []}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
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
                        {measure === "perceived" && (
                            <ReferenceLine
                                y={LATENCY_TARGET_MS}
                                stroke={axisProps(mode).stroke}
                                strokeDasharray="4 4"
                            />
                        )}
                        {PERCENTILE_KEYS.map((key, index) => (
                            <Line
                                key={key}
                                type="monotone"
                                dataKey={key}
                                name={key.replace("_ms", "")}
                                stroke={seriesColor(index, mode)}
                                strokeWidth={key === "p50_ms" ? 2 : 1.5}
                                dot={false}
                            />
                        ))}
                    </LineChart>
                </ResponsiveContainer>
            </ChartCard>

            {/* tool_called and tool_ms have been recorded on every turn since
                the latency observer landed and were never read. A tool taking
                two seconds makes a call feel broken while every provider
                metric stays green. */}
            <Card>
                <CardHeader>
                    <CardTitle>Tool calls</CardTitle>
                </CardHeader>
                <CardContent>
                    {tools.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            No tool was called in this period.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Tool</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                    <TableHead className="text-right">p50</TableHead>
                                    <TableHead className="text-right">p95</TableHead>
                                    <TableHead className="text-right">Worst</TableHead>
                                    <TableHead className="text-right">
                                        Never returned
                                    </TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {tools.map((t: Record<string, never>) => (
                                    <TableRow key={String(t.tool)}>
                                        <TableCell className="font-medium">
                                            {String(t.tool)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(t.calls)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatMs(t.p50_ms)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatMs(t.p95_ms)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatMs(t.worst_ms)}
                                        </TableCell>
                                        <TableCell
                                            className={cn(
                                                "text-right tabular-nums",
                                                Number(t.incomplete) > 0 &&
                                                    "text-destructive",
                                            )}
                                        >
                                            {formatNumber(t.incomplete)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

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
                    description={stageNote}
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
                    <CardTitle className="text-sm font-medium">
                        Endpointing by language
                    </CardTitle>
                    <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-muted-foreground">
                        How long the agent waits before it knows the caller has
                        finished. Flux signals the end of a turn, so it waits for
                        nothing; every other transcriber waits out the silence
                        timeout on every turn. Flux has no Tamil, Telugu, Kannada,
                        Marathi or Bengali — which is why those languages sit here.
                    </p>
                </CardHeader>
                <CardContent>
                    {endpointing.length === 0 ? (
                        <PanelMessage height={140}>
                            No measured turns in this period.
                        </PanelMessage>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Language</TableHead>
                                        <TableHead>Transcriber</TableHead>
                                        <TableHead className="text-right">p50</TableHead>
                                        <TableHead className="text-right">p95</TableHead>
                                        <TableHead className="text-right">Turns</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {endpointing.map((row) => (
                                        <TableRow
                                            key={`${row.language}-${row.stt_model}`}
                                        >
                                            <TableCell className="whitespace-nowrap font-medium">
                                                {row.language}
                                            </TableCell>
                                            <TableCell className="whitespace-nowrap">
                                                <span className="text-muted-foreground">
                                                    {row.stt_model}
                                                </span>
                                                {/* The distinction the table
                                                    exists to draw, said in
                                                    words rather than left to
                                                    be inferred from a model
                                                    name nobody memorises. */}
                                                <span className="ml-2 rounded-md bg-foreground/[0.06] px-1.5 py-0.5 text-[11px] text-muted-foreground">
                                                    {row.waits_for_silence
                                                        ? "waits for silence"
                                                        : "signals turn end"}
                                                </span>
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {row.p50_ms === null
                                                    ? "—"
                                                    : formatMs(row.p50_ms)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums text-muted-foreground">
                                                {row.p95_ms === null
                                                    ? "—"
                                                    : formatMs(row.p95_ms)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums text-muted-foreground">
                                                {formatNumber(row.turns)}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

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
