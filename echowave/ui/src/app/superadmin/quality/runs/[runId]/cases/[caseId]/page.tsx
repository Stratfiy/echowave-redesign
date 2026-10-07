"use client";

import { useParams } from "next/navigation";

import { PageHeader, Panel, StateBadge } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { useStaffData } from "@/lib/staff/data";
import { count } from "@/lib/staff/format";

type Result = {
    outcome: string;
    checks: Array<{ name: string; passed: boolean; detail?: string }>;
    output?: Record<string, unknown> | null;
    judge: Record<string, unknown>;
    latency_ms?: number | null;
    cost_paise?: number | null;
    error_code?: string | null;
};

type Comparison = {
    state: string;
    case: { case_key: string; version: number; input: Record<string, unknown>; expected: Record<string, unknown>; subgroup: Record<string, string>; source: string; source_ref: string | null };
    baseline_case_version: number | null;
    run: { id: number; baseline_run_id: number | null };
    candidate: Result;
    baseline: Result | null;
};

function Side({ title, result }: { title: string; result: Result | null }) {
    return (
        <section className="min-w-0 rounded-lg border border-border p-3" aria-label={title}>
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                {title} {result ? <StateBadge state={result.outcome} /> : <StateBadge state="missing_baseline" label="No result" />}
            </h2>
            {result && (
                <div className="space-y-3 text-sm">
                    <div>
                        <h3 className="text-xs font-medium text-muted-foreground">Evidence (output)</h3>
                        <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-xs">{JSON.stringify(result.output ?? null, null, 2)}</pre>
                    </div>
                    <div>
                        <h3 className="text-xs font-medium text-muted-foreground">Deterministic checks (decide the case)</h3>
                        {result.checks.length === 0 ? (
                            <p className="text-xs">None recorded: the outcome is unknown, not passed.</p>
                        ) : (
                            <ul className="space-y-1">
                                {result.checks.map((c) => (
                                    <li key={c.name} className="flex flex-wrap items-center gap-2 text-xs">
                                        <StateBadge state={c.passed ? "passed" : "failed"} /> {c.name}
                                        {c.detail && <span className="text-muted-foreground">{c.detail}</span>}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                    <div className="rounded border border-dashed border-border p-2">
                        <h3 className="text-xs font-medium text-muted-foreground">Model-judge commentary (never counted)</h3>
                        <pre className="whitespace-pre-wrap break-words text-xs">{JSON.stringify(result.judge, null, 2)}</pre>
                    </div>
                    <p className="text-xs text-muted-foreground">
                        Latency {count(result.latency_ms ?? null)} ms · cost {result.cost_paise === null || result.cost_paise === undefined ? "unknown" : `${result.cost_paise} paise`}
                    </p>
                </div>
            )}
        </section>
    );
}

/** Case comparison (screen 35): the shared input above, baseline and
 *  candidate side by side (stacked on a phone), evidence, deterministic
 *  checks and judge commentary kept apart. A missing result stays unknown. */
export default function CaseComparisonPage() {
    const params = useParams<{ runId: string; caseId: string }>();
    const query = useStaffData<Comparison>(`/api/v1/admin/staff/quality/runs/${params?.runId}/cases/${params?.caseId}`);
    useReportFreshness(query.state, query.refreshedAt);
    return (
        <div className="space-y-4">
            <PageHeader title="Case comparison" />
            <Panel query={query}>
                {(d) => (
                    <div className="space-y-4">
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="font-mono">
                                {d.case.case_key} v{d.case.version}
                            </span>
                            <StateBadge state={d.state} />
                            {d.baseline_case_version !== null && d.baseline_case_version !== d.case.version && (
                                <span className="text-xs">Baseline ran version {d.baseline_case_version}: not the same fixture.</span>
                            )}
                            <span className="text-xs text-muted-foreground">
                                Source: {d.case.source}
                                {d.case.source_ref ? ` (${d.case.source_ref})` : ""}
                            </span>
                        </div>
                        <section aria-label="Shared input" className="rounded-lg border border-border p-3">
                            <h2 className="mb-1 text-sm font-semibold">Shared input (sanitized)</h2>
                            <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({ input: d.case.input, expected: d.case.expected, subgroup: d.case.subgroup }, null, 2)}</pre>
                        </section>
                        <div className="grid gap-4 md:grid-cols-2">
                            <Side title={d.run.baseline_run_id ? `Baseline (run #${d.run.baseline_run_id})` : "Baseline"} result={d.baseline} />
                            <Side title={`Candidate (run #${d.run.id})`} result={d.candidate} />
                        </div>
                    </div>
                )}
            </Panel>
        </div>
    );
}
