"use client";

/**
 * Tokens by vendor and model, split the way vendors price them — input,
 * cached input, cache write, output — from both doors a model call comes
 * through: a run's receipt (calls, replies, routines) and a direct call
 * (Decibyl, the builder, triggers, document fields, reviews).
 *
 * The measurement the decision on charging credits for models waits on. It
 * charges nobody. A model with no rate on file is shown as unpriced, never
 * as free, and the report lists what it cannot see.
 */

import { AlertTriangle, Download } from "lucide-react";
import { useEffect, useState } from "react";

import { tokenUsageReportApiV1AdminBillingTokensByModelGet } from "@/client/sdk.gen";
import { LoadingBlock, PanelMessage, StatTile, useAuthReady } from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { detailFromResult } from "@/lib/apiError";
import { formatNumber, formatPaise } from "@/lib/billing/format";

type Line = {
    source: "run" | "direct";
    provider: string;
    model: string;
    calls: number;
    input_tokens: number;
    cached_tokens: number;
    cache_write_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cached_share: number;
    vendor_cost_paise: number;
    unpriced: string[];
};

type Spread = { count: number; median: number; p90: number };

type Work = {
    source: "run" | "direct";
    work: string;
    tokens_per_run?: Spread;
    vendor_paise_per_run?: Spread;
    tokens_per_call?: Spread;
    vendor_paise_per_call?: Spread;
};

type Report = {
    by_model: Line[];
    by_work: Work[];
    totals: { vendor_cost_paise: number; tokens: number; direct_calls: number; unattributed_calls: number };
    not_metered: string[];
};

function compact(value: number): string {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
    return String(value);
}

const WORK_LABEL: Record<string, string> = {
    voice: "Phone and browser calls",
    text: "Text replies",
    decibyl: "Decibyl assistant",
    builder: "Agent builder",
    trigger_compile: "Trigger setup",
    document_fields: "Document fields",
    acceptable_use: "Acceptable-use check",
    edit_proposal: "Edit proposals",
    graph_decisions: "Graph: decisions",
    graph_sunday_review: "Graph: Sunday review",
    unattributed: "Unattributed",
};

export function VendorSplit() {
    const authReady = useAuthReady();
    const [data, setData] = useState<Report | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            const result = await tokenUsageReportApiV1AdminBillingTokensByModelGet({});
            if (cancelled) return;
            if (result.error) setError(detailFromResult(result, "Failed to load the vendor split"));
            else setData((result.data as Report) ?? null);
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    async function downloadCsv() {
        const result = await tokenUsageReportApiV1AdminBillingTokensByModelGet({
            query: { format: "csv" },
            parseAs: "text",
        });
        if (result.error || typeof result.data !== "string") return;
        const url = URL.createObjectURL(new Blob([result.data], { type: "text/csv" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = "tokens-by-model.csv";
        a.click();
        URL.revokeObjectURL(url);
    }

    if (loading) return <LoadingBlock label="Loading the vendor split" />;
    if (error || !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={120}>
                {error ?? "No data"}
            </PanelMessage>
        );
    }

    const { totals } = data;
    return (
        <div className="space-y-6">
            <div className="grid gap-3 sm:grid-cols-3">
                <StatTile label="Vendor cost, all model calls" value={formatPaise(totals.vendor_cost_paise)} sub="What vendors charge us, before any markup" />
                <StatTile label="Tokens, both doors" value={compact(totals.tokens)} />
                <StatTile
                    label="Direct calls unattributed"
                    value={`${formatNumber(totals.unattributed_calls)} of ${formatNumber(totals.direct_calls)}`}
                    sub="Should read zero; anything else is a caller that has not said what it is"
                    tone={totals.unattributed_calls > 0 ? "warning" : undefined}
                />
            </div>

            <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                    <CardTitle>By vendor and model, split as they price it</CardTitle>
                    <Button variant="outline" size="sm" onClick={downloadCsv}>
                        <Download className="h-4 w-4" />
                        CSV
                    </Button>
                </CardHeader>
                <CardContent>
                    {data.by_model.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No model usage in this period.</p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Where</TableHead>
                                    <TableHead>Vendor</TableHead>
                                    <TableHead>Model</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                    <TableHead className="text-right">Input</TableHead>
                                    <TableHead className="text-right">Cached</TableHead>
                                    <TableHead className="text-right">Cache write</TableHead>
                                    <TableHead className="text-right">Output</TableHead>
                                    <TableHead className="text-right">Cached share</TableHead>
                                    <TableHead className="text-right">Vendor cost</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.by_model.map((row) => (
                                    <TableRow key={`${row.source}-${row.provider}-${row.model}`}>
                                        <TableCell>{row.source === "run" ? "Run" : "Direct"}</TableCell>
                                        <TableCell>{row.provider}</TableCell>
                                        <TableCell className="font-medium">{row.model}</TableCell>
                                        <TableCell className="text-right tabular-nums">{formatNumber(row.calls)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{compact(row.input_tokens)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{compact(row.cached_tokens)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{compact(row.cache_write_tokens)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{compact(row.output_tokens)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{`${Math.round(row.cached_share * 100)}%`}</TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {row.unpriced.length > 0 ? (
                                                <span className="text-amber-600" title={`No rate on file for ${row.unpriced.join(", ")}`}>
                                                    {formatPaise(row.vendor_cost_paise)} + unpriced
                                                </span>
                                            ) : (
                                                formatPaise(row.vendor_cost_paise)
                                            )}
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
                    <CardTitle>By kind of work</CardTitle>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Work</TableHead>
                                <TableHead className="text-right">Count</TableHead>
                                <TableHead className="text-right">Tokens, median</TableHead>
                                <TableHead className="text-right">Tokens, p90</TableHead>
                                <TableHead className="text-right">Vendor cost, median</TableHead>
                                <TableHead className="text-right">Vendor cost, p90</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.by_work.map((row) => {
                                const tokens = row.tokens_per_run ?? row.tokens_per_call;
                                const cost = row.vendor_paise_per_run ?? row.vendor_paise_per_call;
                                return (
                                    <TableRow key={`${row.source}-${row.work}`}>
                                        <TableCell className="font-medium">
                                            {WORK_LABEL[row.work] ?? row.work}
                                            <span className="ml-2 text-xs text-muted-foreground">{row.source === "run" ? "per run" : "per call"}</span>
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">{formatNumber(tokens?.count ?? 0)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{compact(tokens?.median ?? 0)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{compact(tokens?.p90 ?? 0)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{formatPaise(cost?.median ?? 0)}</TableCell>
                                        <TableCell className="text-right tabular-nums">{formatPaise(cost?.p90 ?? 0)}</TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                    {data.not_metered.length > 0 && (
                        <p className="mt-4 text-xs text-muted-foreground">
                            Not metered yet: {data.not_metered.join("; ")}.
                        </p>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
