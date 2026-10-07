"use client";

import { useState } from "react";

import { MetricDefinition } from "@/components/shell";
import { Empty, PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { Button } from "@/components/ui/button";
import { useStaffData } from "@/lib/staff/data";
import { count, percent, when, words } from "@/lib/staff/format";

type Report = {
    period: { start: string; end: string; days: number };
    definitions: Record<string, string>;
    funnel: Array<{ step: string; stage: string; count: number }>;
    weekly_useful_users: { value: number; window_days: number };
    seven_day_repeat: { value: number | null; numerator: number; denominator: number; not_yet_eligible: number; state: string };
    retention: Array<{ week_start: string; activated: number; returned_in_7_days: number | null; rate: number | null; state: string }>;
    task_success: {
        completed: number;
        failed: number;
        unknown: number;
        excluded: number;
        eligible: number;
        rate: number | null;
        breakdown: Array<{ type: string; completed: number; failed: number; unknown: number; excluded: number; eligible: number; rate: number | null }>;
        cancellation_policy: string;
    };
    usefulness: { answered: number; yes: number; not_quite: number; rate: number | null; exposure_upper_bound: number; response_rate: number | null };
};

const POSTHOG = process.env.NEXT_PUBLIC_POSTHOG_HOST;

/**
 * Product analytics (screen 36): one funnel, one retention view, one
 * breakdown. Acquisition, activity and verified outcomes are labelled apart;
 * every value carries its sample, and a cohort whose window has not passed
 * says so instead of showing a low number. No figure animates.
 */
export default function AnalyticsPage() {
    const { days } = useStaffConsole();
    const query = useStaffData<Report>("/api/v1/admin/staff/analytics", { days });
    const [drill, setDrill] = useState(false);
    const records = useStaffData<{ state: string; reason?: string; records: Array<{ person: string; outcomes: number; last_at: string; kinds: string[] }> }>(
        drill ? "/api/v1/admin/staff/analytics/records" : null,
        { days },
    );
    useReportFreshness(query.state, query.refreshedAt);

    return (
        <div className="space-y-4">
            <PageHeader
                title="Product analytics"
                description="From Decibyl's own records of tasks, approvals and feedback, which are authoritative for outcomes."
                actions={
                    POSTHOG ? (
                        <Button asChild variant="outline" className="min-h-11 md:min-h-9">
                            <a href={POSTHOG} target="_blank" rel="noreferrer">
                                Deeper analysis in PostHog
                            </a>
                        </Button>
                    ) : (
                        <StateBadge state="needs_setup" label="PostHog link needs setup" />
                    )
                }
            />
            <Panel title="Headline" query={query}>
                {(r) => (
                    <div className="grid gap-4 sm:grid-cols-3">
                        <MetricDefinition name="Weekly useful users" value={r.weekly_useful_users.value} definition={r.definitions.weekly_useful_users} period="Seven days ending now" source="agent_tasks, approval cards" />
                        <MetricDefinition
                            name="Seven-day repeat"
                            value={r.seven_day_repeat.value === null ? null : percent(r.seven_day_repeat.value)}
                            definition={r.definitions.seven_day_repeat}
                            period={`${r.seven_day_repeat.numerator} of ${r.seven_day_repeat.denominator} matured · ${r.seven_day_repeat.not_yet_eligible} not yet eligible`}
                            missingReason="No activated person's seven days have passed yet."
                        />
                        <MetricDefinition
                            name="Task success"
                            value={r.task_success.rate === null ? null : percent(r.task_success.rate)}
                            definition={r.definitions.task_success}
                            period={`n=${count(r.task_success.eligible)} · ${r.task_success.unknown} unknown · ${r.task_success.excluded} excluded`}
                            missingReason="Nothing finished in the period."
                        />
                    </div>
                )}
            </Panel>

            <Panel title="Funnel" query={query}>
                {(r) => {
                    const top = Math.max(1, ...r.funnel.map((f) => f.count));
                    return (
                        <figure>
                            <ol className="space-y-2" aria-label="Funnel steps">
                                {r.funnel.map((f) => (
                                    <li key={f.step} className="grid grid-cols-[minmax(8rem,12rem)_1fr_auto] items-center gap-2 text-sm">
                                        <span>
                                            {words(f.step)} <span className="text-xs text-muted-foreground">({f.stage})</span>
                                        </span>
                                        <span className="h-3 rounded bg-muted" aria-hidden>
                                            <span className="block h-3 rounded bg-foreground/70" style={{ width: `${(f.count / top) * 100}%` }} />
                                        </span>
                                        <span className="tabular-nums">{count(f.count)}</span>
                                    </li>
                                ))}
                            </ol>
                            <figcaption className="mt-2 text-xs text-muted-foreground">{r.definitions.activation}</figcaption>
                        </figure>
                    );
                }}
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Retention by activation week" query={query}>
                    {(r) => (
                        <TableRegion label="Retention by activation week">
                            <table className="w-full text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="py-1 font-normal">Week from</th>
                                        <th className="py-1 text-right font-normal">Activated</th>
                                        <th className="py-1 text-right font-normal">Back in 7 days</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {r.retention.map((c) => (
                                        <tr key={c.week_start} className="border-t border-border tabular-nums">
                                            <td className="py-1">{c.week_start}</td>
                                            <td className="py-1 text-right">{c.activated}</td>
                                            <td className="py-1 text-right">
                                                {c.state === "mature" ? `${c.returned_in_7_days} (${percent(c.rate)})` : <StateBadge state="insufficient_window" label="Window not passed" />}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    )}
                </Panel>
                <Panel title="Usefulness" query={query}>
                    {(r) => (
                        <div className="grid gap-4 sm:grid-cols-2">
                            <MetricDefinition
                                name="Yes rate"
                                value={r.usefulness.rate === null ? null : percent(r.usefulness.rate)}
                                definition={r.definitions.usefulness}
                                period={`${r.usefulness.yes} yes of ${r.usefulness.answered} answered`}
                                missingReason="Nobody answered in this period."
                            />
                            <MetricDefinition
                                name="Response rate"
                                value={r.usefulness.response_rate === null ? null : percent(r.usefulness.response_rate)}
                                definition="Answered prompts divided by Decibyl replies (an upper bound on prompts shown)."
                                period={`${count(r.usefulness.answered)} of ≤${count(r.usefulness.exposure_upper_bound)} shown`}
                                missingReason="No replies in this period."
                            />
                        </div>
                    )}
                </Panel>
            </div>

            <Panel title="Task success by type" query={query}>
                {(r) =>
                    r.task_success.breakdown.length === 0 ? (
                        <Empty>Nothing finished in this period.</Empty>
                    ) : (
                        <TableRegion label="Task success by type">
                            <table className="w-full min-w-[520px] text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="py-1 font-normal">Type</th>
                                        <th className="py-1 text-right font-normal">Completed</th>
                                        <th className="py-1 text-right font-normal">Failed</th>
                                        <th className="py-1 text-right font-normal">Unknown</th>
                                        <th className="py-1 text-right font-normal">Excluded</th>
                                        <th className="py-1 text-right font-normal">Rate (n)</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {r.task_success.breakdown.map((b) => (
                                        <tr key={b.type} className="border-t border-border tabular-nums">
                                            <td className="py-1">{words(b.type)}</td>
                                            <td className="py-1 text-right">{b.completed}</td>
                                            <td className="py-1 text-right">{b.failed}</td>
                                            <td className="py-1 text-right">{b.unknown}</td>
                                            <td className="py-1 text-right">{b.excluded}</td>
                                            <td className="py-1 text-right">
                                                {percent(b.rate)} ({b.eligible})
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            <p className="mt-2 text-xs text-muted-foreground">{r.task_success.cancellation_policy}</p>
                        </TableRegion>
                    )
                }
            </Panel>

            <section className="rounded-lg border border-border p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-base font-semibold">People behind the numbers</h2>
                    <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => setDrill((d) => !d)} aria-expanded={drill}>
                        {drill ? "Hide" : "Show pseudonymous records"}
                    </Button>
                </div>
                {drill && (
                    <Panel query={records} className="mt-3 border-0 p-0">
                        {(d) =>
                            d.state !== "ok" ? (
                                <p className="flex items-center gap-2 text-sm">
                                    <StateBadge state={d.state} /> {d.reason}
                                </p>
                            ) : d.records.length === 0 ? (
                                <Empty>No useful outcomes in this period.</Empty>
                            ) : (
                                <ul className="divide-y divide-border text-sm">
                                    {d.records.map((x) => (
                                        <li key={x.person} className="flex flex-wrap gap-2 py-1.5">
                                            <span className="font-mono text-xs">{x.person}</span>
                                            <span className="tabular-nums">{x.outcomes} outcomes</span>
                                            <span className="text-xs text-muted-foreground">{x.kinds.map(words).join(", ")}</span>
                                            <span className="ml-auto text-xs">{when(x.last_at)}</span>
                                        </li>
                                    ))}
                                </ul>
                            )
                        }
                    </Panel>
                )}
            </section>
        </div>
    );
}
