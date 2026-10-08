"use client";

import { useState } from "react";

import { MetricDefinition } from "@/components/shell";
import { PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { useStaffData } from "@/lib/staff/data";
import { count, words } from "@/lib/staff/format";

type Pct = { p50_ms: number | null; p90_ms: number | null; p95_ms: number | null; p99_ms: number | null; turns: number };
type Latency = {
    period: { start: string; end: string };
    headline: Record<string, Pct>;
    by_language: Array<Record<string, unknown>>;
    stage_medians: Record<string, unknown> | Array<Record<string, unknown>>;
    languages: string[];
    filters: Record<string, string | null>;
    measured_at: string;
    client_measure: { state: string; reason: string };
    definition: string;
};

/**
 * Voice latency (screen 40): p50 and p95 with their sample sizes, from the
 * pipeline's own clock. The handoff's target is measured at the client; that
 * is not instrumented yet and the page says so rather than relabelling a
 * server number. Filters that are not recorded are shown as such.
 */
export default function LatencyPage() {
    const { days } = useStaffConsole();
    const [language, setLanguage] = useState("");
    const query = useStaffData<Latency>("/api/v1/admin/staff/operations/latency", { days, language: language || undefined });
    useReportFreshness(query.state, query.refreshedAt);
    return (
        <div className="space-y-4">
            <PageHeader title="Voice latency" description="Speech end to first audio, per turn. Turns missing a mark are left out, never counted as zero." />
            <Panel query={query}>
                {(d) => (
                    <div className="space-y-4">
                        <div className="flex flex-wrap items-end gap-3 text-sm">
                            <label className="flex flex-col gap-1">
                                Language
                                <select value={language} onChange={(e) => setLanguage(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                    <option value="">All</option>
                                    {d.languages.map((l) => (
                                        <option key={l} value={l}>
                                            {l}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            {Object.entries(d.filters)
                                .filter(([k]) => k !== "language")
                                .map(([k, v]) => (
                                    <span key={k} className="flex items-center gap-1 text-xs">
                                        {words(k)} <StateBadge state={v === "not_recorded" ? "unavailable" : "ok"} label={v === "not_recorded" ? "Not recorded" : String(v)} />
                                    </span>
                                ))}
                        </div>
                        <p className="flex flex-wrap items-center gap-2 text-sm">
                            Client-measured response time <StateBadge state={d.client_measure.state} /> <span className="text-xs text-muted-foreground">{d.client_measure.reason}</span>
                        </p>
                        <div className="grid gap-4 sm:grid-cols-3">
                            {Object.entries(d.headline).map(([measure, p]) => (
                                <MetricDefinition
                                    key={measure}
                                    name={`${words(measure)} p50 / p95`}
                                    value={p.p50_ms === null ? null : `${p.p50_ms} / ${p.p95_ms ?? "—"}`}
                                    unit="ms"
                                    definition={measure === "perceived" ? d.definition : measure === "ttft" ? "Final transcript to the model's first token." : "Model's first token to the first audio byte."}
                                    period={`${d.period.start} to ${d.period.end} · n=${count(p.turns)} turns`}
                                    missingReason="No turns with both marks in this period."
                                    source={`call_turn_metrics (measured at the ${d.measured_at})`}
                                />
                            ))}
                        </div>
                        <TableRegion label="Latency by language">
                            <table className="w-full min-w-[420px] text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="py-1 font-normal">Language</th>
                                        {Object.keys(d.by_language[0] ?? {})
                                            .filter((k) => k !== "language")
                                            .map((k) => (
                                                <th key={k} className="py-1 text-right font-normal">
                                                    {words(k)}
                                                </th>
                                            ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {d.by_language.map((row, i) => (
                                        <tr key={i} className="border-t border-border tabular-nums">
                                            <td className="py-1">{String(row.language ?? "unlabelled")}</td>
                                            {Object.entries(row)
                                                .filter(([k]) => k !== "language")
                                                .map(([k, v]) => (
                                                    <td key={k} className="py-1 text-right">
                                                        {v === null || v === undefined ? "—" : String(v)}
                                                    </td>
                                                ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    </div>
                )}
            </Panel>
        </div>
    );
}
