"use client";

import { AlertTriangle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { getCallApiV1AdminBillingCallsWorkflowRunIdGet } from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
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
import {
    formatDateTimeIST,
    formatDuration,
    formatMs,
    formatNumber,
    formatPaise,
    formatRateMpaise,
} from "@/lib/billing/format";

import { seriesColor } from "../../_components/chartTheme";
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
} from "../../_components/primitives";

/** The four pipeline stages that make up a turn's perceived latency. */
const STAGES = [
    { key: "endpointing_ms", label: "Endpointing", slot: 0 },
    { key: "stt_ms", label: "STT", slot: 1 },
    { key: "llm_ms", label: "LLM", slot: 2 },
    { key: "tts_ms", label: "TTS", slot: 3 },
] as const;

const UNIT_LABEL: Record<string, string> = {
    stt: "seconds",
    telephony: "seconds",
    llm: "tokens",
    tts: "characters",
    platform: "minutes",
};

export default function CallDetailPage() {
    const params = useParams<{ runId: string }>();
    const runId = Number(params.runId);
    const mode = useChartMode();

    const [call, setCall] = useState<Record<string, never> | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getCallApiV1AdminBillingCallsWorkflowRunIdGet({
                path: { workflow_run_id: runId },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load call"));
            } else {
                setCall((result.data as Record<string, never>) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, runId]);

    if (loading) return <LoadingBlock label="Loading call" />;
    if (error || !call) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error ?? "Call not found."}
            </PanelMessage>
        );
    }

    const items = (call.cost_items ?? []) as Array<Record<string, never>>;
    const turns = (call.turns ?? []) as Array<Record<string, never>>;

    const providerItems = items.filter((i) => String(i.component) !== "platform");
    const platformItem = items.find((i) => String(i.component) === "platform");
    const providerTotal = providerItems.reduce(
        (sum, i) => sum + Number(i.cost_paise ?? 0),
        0,
    );
    const total = Number(call.charged_paise ?? 0);

    const toolCalls = turns.filter((t) => t.tool_called);

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-3">
                <Link
                    href="/superadmin/billing/calls"
                    className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                >
                    <ArrowLeft className="h-4 w-4" />
                    Calls
                </Link>
                <h2 className="text-lg font-semibold">Call receipt</h2>
                <Badge variant="secondary">#{String(call.id)}</Badge>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
                <Card className="lg:col-span-1">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Call details</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm">
                        <Field label="Account" value={String(call.account ?? "—")} />
                        <Field label="Started" value={formatDateTimeIST(String(call.created_at ?? ""))} />
                        <Field label="Answered" value={formatDateTimeIST(String(call.answered_at ?? ""))} />
                        <Field label="Ended" value={formatDateTimeIST(String(call.ended_at ?? ""))} />
                        <Field
                            label="Billable"
                            value={formatDuration(Number(call.billable_seconds ?? 0))}
                        />
                        <Field label="Direction" value={String(call.direction ?? "—")} />
                        <Field label="Language" value={String(call.language ?? "—")} />
                        <Field label="Disposition" value={String(call.disposition ?? "—")} />
                        <Field label="From" value={String(call.caller_number ?? "—")} />
                        <Field label="To" value={String(call.called_number ?? "—")} />
                    </CardContent>
                </Card>

                <Card className="lg:col-span-2">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Itemised cost</CardTitle>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                            Provider costs are passed through at cost. The platform fee is the only
                            margin on this call.
                        </p>
                    </CardHeader>
                    <CardContent>
                        {items.length === 0 ? (
                            <PanelMessage height={140}>
                                This call has not been costed yet.
                            </PanelMessage>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Component</TableHead>
                                        <TableHead>Provider</TableHead>
                                        <TableHead className="text-right">Units</TableHead>
                                        <TableHead className="text-right">Unit rate</TableHead>
                                        <TableHead className="text-right">Cost</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {providerItems.map((item, index) => (
                                        <TableRow key={index}>
                                            <TableCell className="uppercase">
                                                {String(item.component)}
                                            </TableCell>
                                            <TableCell className="text-muted-foreground">
                                                {String(item.provider ?? "—")}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatNumber(Number(item.units))}{" "}
                                                <span className="text-xs text-muted-foreground">
                                                    {UNIT_LABEL[String(item.component)] ?? ""}
                                                </span>
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums text-muted-foreground">
                                                {formatRateMpaise(Number(item.unit_rate_mpaise))}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatPaise(Number(item.cost_paise))}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                    <TableRow className="border-t">
                                        <TableCell colSpan={4} className="text-muted-foreground">
                                            Provider cost subtotal
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(providerTotal)}
                                        </TableCell>
                                    </TableRow>
                                    {platformItem && (
                                        <TableRow>
                                            <TableCell className="font-medium">Platform fee</TableCell>
                                            <TableCell className="text-muted-foreground">—</TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatNumber(Number(platformItem.units))}{" "}
                                                <span className="text-xs text-muted-foreground">min</span>
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums text-muted-foreground">
                                                {formatRateMpaise(Number(platformItem.unit_rate_mpaise))}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatPaise(Number(platformItem.cost_paise))}
                                            </TableCell>
                                        </TableRow>
                                    )}
                                    <TableRow className="border-t-2">
                                        <TableCell colSpan={4} className="font-semibold">
                                            Total charged
                                        </TableCell>
                                        <TableCell className="text-right font-semibold tabular-nums">
                                            {formatPaise(total)}
                                        </TableCell>
                                    </TableRow>
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            </div>

            <ChartCard
                title="Latency breakdown per turn"
                description="Endpointing, STT, LLM and TTS stacked — a slow turn is diagnosable at a glance"
                isEmpty={turns.length === 0}
                emptyMessage="No per-turn latency was recorded for this call."
                height={280}
            >
                <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={turns} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis
                            dataKey="turn_index"
                            tickFormatter={(v: number) => `Turn ${v + 1}`}
                            {...axisProps(mode)}
                        />
                        <YAxis width={62} tickFormatter={(v: number) => formatMs(v)} {...axisProps(mode)} />
                        <RTooltip
                            cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                            content={
                                <ChartTooltip
                                    formatter={(v) => formatMs(v)}
                                    labelFormatter={(l) => `Turn ${Number(l) + 1}`}
                                />
                            }
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
                                stackId="latency"
                                fill={seriesColor(stage.slot, mode)}
                                maxBarSize={36}
                            />
                        ))}
                    </BarChart>
                </ResponsiveContainer>
            </ChartCard>

            {toolCalls.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Tool calls</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Turn</TableHead>
                                    <TableHead>Tool</TableHead>
                                    <TableHead className="text-right">Duration</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {toolCalls.map((turn, index) => (
                                    <TableRow key={index}>
                                        <TableCell>Turn {Number(turn.turn_index) + 1}</TableCell>
                                        <TableCell className="font-mono text-xs">
                                            {String(turn.tool_called)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatMs(Number(turn.tool_ms ?? 0))}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            )}

            {call.recording_url ? (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Recording</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <audio controls src={String(call.recording_url)} className="w-full">
                            Your browser does not support audio playback.
                        </audio>
                    </CardContent>
                </Card>
            ) : null}
        </div>
    );
}

function Field({ label, value }: { label: string; value: string }) {
    return (
        <div className="flex items-baseline justify-between gap-4">
            <span className="text-xs text-muted-foreground">{label}</span>
            <span className="text-right">{value}</span>
        </div>
    );
}
