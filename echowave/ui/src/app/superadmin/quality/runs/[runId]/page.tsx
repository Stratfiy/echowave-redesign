"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { Empty, Field, Fields, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness } from "@/components/staff/StaffShell";
import { useStaffData } from "@/lib/staff/data";
import { count, when } from "@/lib/staff/format";

type RunDetail = {
    run: {
        id: number;
        dataset: string;
        dataset_version: string;
        config: Record<string, unknown>;
        config_version: string;
        runner: string;
        baseline_run_id: number | null;
        state: string;
        totals: Record<string, number | null> | null;
        created_at: string;
        finished_at: string | null;
    };
    subgroups: Array<{ group: string; passed: number; failed: number; unknown: number }>;
    failed_cases: Array<{ case_id: number; case_key: string; version: number; outcome: string; subgroup: Record<string, string>; failed_checks: string[]; error_code: string | null }>;
};

/** One run: its gate and why, coverage by subgroup beside the overall
 *  result, and every case that did not pass with the exact checks that
 *  failed. */
export default function RunPage() {
    const params = useParams<{ runId: string }>();
    const runId = Number(params?.runId);
    const query = useStaffData<RunDetail>(Number.isFinite(runId) ? `/api/v1/admin/staff/quality/runs/${runId}` : null);
    useReportFreshness(query.state, query.refreshedAt);
    return (
        <div className="space-y-4">
            <PageHeader title={`Evaluation run #${runId}`} description="Re-running makes a new run; this one's evidence is kept as it is." />
            <Panel title="Gate" query={query}>
                {(d) => (
                    <Fields>
                        <Field label="Result">
                            <StateBadge state={d.run.state} />
                        </Field>
                        <Field label="Dataset">
                            {d.run.dataset} · set {d.run.dataset_version}
                        </Field>
                        <Field label="Configuration">
                            <span className="font-mono text-xs">
                                {d.run.config_version} {JSON.stringify(d.run.config)}
                            </span>
                        </Field>
                        <Field label="Baseline">{d.run.baseline_run_id ? <Link className="inline-flex min-h-11 items-center underline md:min-h-0" href={`/superadmin/quality/runs/${d.run.baseline_run_id}`}>Run #{d.run.baseline_run_id}</Link> : "None (failures cannot be judged as regressions)"}</Field>
                        <Field label="Sample">{count(d.run.totals?.sample ?? null)}</Field>
                        <Field label="Passed / failed / unknown">
                            {d.run.totals ? `${d.run.totals.passed} / ${d.run.totals.failed} / ${d.run.totals.unknown}` : "—"}
                        </Field>
                        <Field label="Regressions">{count(d.run.totals?.regressions ?? null)}</Field>
                        <Field label="Blocking (permission, approval)">{count(d.run.totals?.blocking_failures ?? null)}</Field>
                        <Field label="Finished">{when(d.run.finished_at)}</Field>
                    </Fields>
                )}
            </Panel>
            <Panel title="Subgroup coverage" query={query}>
                {(d) =>
                    d.subgroups.length === 0 ? (
                        <Empty>No results recorded.</Empty>
                    ) : (
                        <TableRegion label="Subgroup coverage">
                            <table className="w-full text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="py-1 font-normal">Group</th>
                                        <th className="py-1 text-right font-normal">Passed</th>
                                        <th className="py-1 text-right font-normal">Failed</th>
                                        <th className="py-1 text-right font-normal">Unknown</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {d.subgroups.map((g) => (
                                        <tr key={g.group} className="border-t border-border tabular-nums">
                                            <td className="py-1">{g.group}</td>
                                            <td className="py-1 text-right">{g.passed}</td>
                                            <td className="py-1 text-right">{g.failed}</td>
                                            <td className="py-1 text-right">{g.unknown}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )
                }
            </Panel>
            <Panel title="Cases that did not pass" query={query}>
                {(d) =>
                    d.failed_cases.length === 0 ? (
                        <Empty>Every case passed.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm" data-testid="failed-cases">
                            {d.failed_cases.map((c) => (
                                <li key={c.case_id} className="flex flex-wrap items-center gap-2 py-2">
                                    <Link href={`/superadmin/quality/runs/${runId}/cases/${c.case_id}`} className="min-h-11 font-mono text-xs underline underline-offset-2 md:min-h-0">
                                        {c.case_key} v{c.version}
                                    </Link>
                                    <StateBadge state={c.outcome === "unknown" ? "unknown" : "failed"} label={c.outcome} />
                                    <span className="text-xs">{c.failed_checks.length ? `Failed: ${c.failed_checks.join(", ")}` : (c.error_code ?? "No result")}</span>
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>
        </div>
    );
}
