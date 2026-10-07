"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CommandFlow, PreviewValue } from "@/components/staff/CommandFlow";
import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { count, when } from "@/lib/staff/format";

type Datasets = { datasets: Array<{ dataset: string; cases: number; drafts: number }>; min_sample: number; runners: string[] };
type Run = {
    id: number;
    dataset: string;
    dataset_version: string;
    config: Record<string, unknown>;
    config_version: string;
    runner: string;
    baseline_run_id: number | null;
    state: string;
    totals: null | {
        passed: number;
        failed: number;
        unknown: number;
        sample: number;
        regressions: number;
        blocking_failures: number;
        latency_p50_ms: number | null;
        latency_p95_ms: number | null;
        cost_paise: number | null;
    };
    created_at: string;
};
type Case = { id: number; case_key: string; version: number; review_state: string; input: Record<string, unknown>; expected: Record<string, unknown>; subgroup: Record<string, string>; created_by: number; source: string };

/**
 * Quality and evaluation runs (screen 34): pick a dataset and a baseline,
 * compare runs on sample, correctness, latency and cost, and see where the
 * gate blocked. A run uses the dataset's approved cases at that moment and
 * says which set and configuration it used.
 */
export default function QualityPage() {
    const { can, me } = useStaffConsole();
    const datasets = useStaffData<Datasets>("/api/v1/admin/staff/quality/datasets");
    const [dataset, setDataset] = useState<string>("");
    const [baseline, setBaseline] = useState<string>("");
    const [panel, setPanel] = useState<null | "run" | "case">(null);
    const [draft, setDraft] = useState({ case_key: "", text: "", kind: "quick", language: "en", group: "routing" });
    const [review, setReview] = useState<Case | null>(null);
    useEffect(() => {
        if (!dataset && datasets.data?.datasets[0]) setDataset(datasets.data.datasets[0].dataset);
    }, [dataset, datasets.data]);
    const runs = useStaffData<{ runs: Run[] }>(dataset ? "/api/v1/admin/staff/quality/runs" : null, { dataset });
    const cases = useStaffData<{ cases: Case[] }>(dataset ? `/api/v1/admin/staff/quality/datasets/${encodeURIComponent(dataset)}/cases` : null);
    useReportFreshness(runs.state === "loading" ? datasets.state : runs.state, runs.refreshedAt ?? datasets.refreshedAt);
    const manage = can("quality.manage");
    const laya = useStaffData<{ shadow: Record<string, unknown>; latest_evaluation: Record<string, unknown> | null }>("/api/v1/admin/ops/laya");

    return (
        <div className="space-y-4">
            <PageHeader
                title="Quality and evaluations"
                description="Runs against a fixed, approved case set. Only deterministic checks decide a case; a model judge's view is shown beside, never counted."
                actions={
                    manage && dataset ? (
                        <>
                            <Button className="min-h-11 md:min-h-9" onClick={() => setPanel("run")}>
                                Run this dataset
                            </Button>
                            <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setPanel("case")}>
                                New case version
                            </Button>
                        </>
                    ) : null
                }
            />
            <Panel query={datasets} setupHint="Turn on staff_evaluations to keep evaluation datasets here.">
                {(d) =>
                    d.datasets.length === 0 ? (
                        <div className="space-y-2 text-sm">
                            <Empty>No datasets yet. A dataset starts with its first case.</Empty>
                            {manage && (
                                <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => { setDataset("routing"); setPanel("case"); }}>
                                    Write the first case
                                </Button>
                            )}
                        </div>
                    ) : (
                        <div className="flex flex-wrap items-end gap-3 text-sm">
                            <label className="flex flex-col gap-1">
                                Dataset
                                <select value={dataset} onChange={(e) => setDataset(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                    {d.datasets.map((x) => (
                                        <option key={x.dataset} value={x.dataset}>
                                            {x.dataset} ({x.cases} cases{x.drafts ? `, ${x.drafts} drafts` : ""})
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label className="flex flex-col gap-1">
                                Baseline for the next run
                                <select value={baseline} onChange={(e) => setBaseline(e.target.value)} className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm">
                                    <option value="">None</option>
                                    {runs.data?.runs.map((r) => (
                                        <option key={r.id} value={r.id}>
                                            Run #{r.id} ({r.state})
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <span className="text-xs text-muted-foreground">A run needs at least {d.min_sample} cases to count.</span>
                        </div>
                    )
                }
            </Panel>

            {panel && (
                <section className="rounded-lg border border-border p-4" aria-label={panel === "run" ? "Start a run" : "New case version"}>
                    {panel === "case" && (
                        <div className="mb-3 grid gap-2 sm:grid-cols-2">
                            {(
                                [
                                    ["case_key", "Case key"],
                                    ["text", "Input text (sanitized when saved)"],
                                    ["kind", "Expected kind (quick, steps, deep)"],
                                    ["language", "Language"],
                                    ["group", "Subgroup kind (routing, approval, permission…)"],
                                ] as const
                            ).map(([k, label]) => (
                                <label key={k} className="flex flex-col gap-1 text-sm">
                                    {label}
                                    <input
                                        value={draft[k]}
                                        onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                                        className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                                    />
                                </label>
                            ))}
                        </div>
                    )}
                    <CommandFlow
                        key={panel === "case" ? JSON.stringify(draft) : `run-${baseline}`}
                        command={panel === "run" ? "eval.run" : "eval.case.save"}
                        target={
                            panel === "run"
                                ? { dataset, runner: "routing_rules", ...(baseline ? { baseline_run_id: Number(baseline) } : {}) }
                                : {
                                      dataset: dataset || "routing",
                                      case_key: draft.case_key || "case",
                                      input: { text: draft.text },
                                      expected: { kind: draft.kind },
                                      subgroup: { language: draft.language, kind: draft.group },
                                  }
                        }
                        targetLabel={`dataset ${dataset || "routing"}`}
                        onDone={() => {
                            void runs.refresh();
                            void cases.refresh();
                            void datasets.refresh();
                        }}
                        onCancel={() => setPanel(null)}
                    />
                </section>
            )}

            <Panel title="Runs" query={runs}>
                {(r) =>
                    r.runs.length === 0 ? (
                        <Empty>No runs of this dataset yet.</Empty>
                    ) : (
                        <TableRegion label="Evaluation runs">
                            <table className="w-full min-w-[640px] text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="sticky left-0 bg-background py-1 pr-2 font-normal">Run</th>
                                        <th className="py-1 pr-2 font-normal">Gate</th>
                                        <th className="py-1 pr-2 font-normal">Configuration</th>
                                        <th className="py-1 pr-2 text-right font-normal">Sample</th>
                                        <th className="py-1 pr-2 text-right font-normal">Passed / failed / unknown</th>
                                        <th className="py-1 pr-2 text-right font-normal">p50 / p95 ms</th>
                                        <th className="py-1 text-right font-normal">Cost</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {r.runs.map((run) => (
                                        <tr key={run.id} className="border-t border-border">
                                            <td className="sticky left-0 bg-background py-1.5 pr-2">
                                                <Link href={`/superadmin/quality/runs/${run.id}`} className="inline-flex min-h-11 items-center underline underline-offset-2 md:min-h-0">
                                                    #{run.id}
                                                </Link>
                                                <div className="text-xs text-muted-foreground">{when(run.created_at)}</div>
                                            </td>
                                            <td className="py-1.5 pr-2">
                                                <StateBadge state={run.state} />
                                            </td>
                                            <td className="py-1.5 pr-2 font-mono text-xs">
                                                {run.runner} · cfg {run.config_version} · set {run.dataset_version}
                                                {run.baseline_run_id ? ` · vs #${run.baseline_run_id}` : ""}
                                            </td>
                                            <td className="py-1.5 pr-2 text-right tabular-nums">{count(run.totals?.sample)}</td>
                                            <td className="py-1.5 pr-2 text-right tabular-nums">
                                                {run.totals ? `${run.totals.passed} / ${run.totals.failed} / ${run.totals.unknown}` : "—"}
                                            </td>
                                            <td className="py-1.5 pr-2 text-right tabular-nums">
                                                {count(run.totals?.latency_p50_ms)} / {count(run.totals?.latency_p95_ms)}
                                            </td>
                                            <td className="py-1.5 text-right tabular-nums">{run.totals?.cost_paise === null || run.totals?.cost_paise === undefined ? "Unknown" : `${run.totals.cost_paise} paise`}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )
                }
            </Panel>

            <Panel title="Cases" query={cases}>
                {(c) =>
                    c.cases.length === 0 ? (
                        <Empty>No cases yet.</Empty>
                    ) : (
                        <ul className="divide-y divide-border text-sm">
                            {c.cases.map((x) => (
                                <li key={x.id} className="flex flex-wrap items-center gap-2 py-1.5">
                                    <span className="font-mono text-xs">
                                        {x.case_key} v{x.version}
                                    </span>
                                    <StateBadge state={x.review_state === "approved" ? "ok" : x.review_state} label={x.review_state} />
                                    <span className="text-xs text-muted-foreground">
                                        {Object.entries(x.subgroup)
                                            .map(([k, v]) => `${k}: ${v}`)
                                            .join(" · ")}
                                    </span>
                                    {manage && x.review_state === "draft" && x.created_by !== me.user_id && (
                                        <Button size="sm" variant="outline" className="ml-auto min-h-11 md:min-h-8" onClick={() => setReview(x)}>
                                            Review
                                        </Button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )
                }
            </Panel>
            <Panel title="Laya routing (shadow agreement and latest offline evaluation)" query={laya} setupHint="Laya evaluation is the ops stream's service (/admin/ops/laya), not on this build yet.">
                {(l) => (
                    <div className="grid gap-3 text-xs sm:grid-cols-2">
                        <div>
                            <h3 className="mb-1 font-medium">Shadow agreement (counts only)</h3>
                            <PreviewValue value={l.shadow} />
                        </div>
                        <div>
                            <h3 className="mb-1 font-medium">Latest offline evaluation</h3>
                            {l.latest_evaluation ? <PreviewValue value={l.latest_evaluation} /> : <StateBadge state="unknown" label="None recorded" />}
                        </div>
                    </div>
                )}
            </Panel>
            {review && (
                <section className="rounded-lg border border-border p-4" aria-label="Review a case">
                    <pre className="mb-3 max-w-full overflow-x-auto rounded bg-muted/40 p-2 text-xs">{JSON.stringify({ input: review.input, expected: review.expected }, null, 2)}</pre>
                    <CommandFlow
                        command="eval.case.review"
                        target={{ case_id: review.id, decision: "approve" }}
                        targetLabel={`${review.case_key} v${review.version}`}
                        onDone={() => void cases.refresh()}
                        onCancel={() => setReview(null)}
                    />
                </section>
            )}
        </div>
    );
}
