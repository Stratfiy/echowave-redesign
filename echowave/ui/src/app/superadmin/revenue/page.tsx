"use client";

import Link from "next/link";

import { MetricDefinition } from "@/components/shell";
import { PageHeader, Panel, StateBadge, TableRegion } from "@/components/staff/parts";
import { useReportFreshness, useStaffConsole } from "@/components/staff/StaffShell";
import { useStaffData } from "@/lib/staff/data";
import { count, money } from "@/lib/staff/format";

type Revenue = {
    period: { start: string; end: string; days: number; timezone: string };
    currency: string;
    free_mode: boolean;
    revenue: {
        cash_collected_paise: number;
        cash_collected_net_of_tax_paise: number;
        tax_paise: number;
        payments: number;
        refunds_paise: number;
        refunds_pending: number;
        net_cash_paise: number;
        recurring_value_paise: number | null;
        recurring_value_reason: string;
        usage_charged_paise: number;
        usage_charged_note: string;
    };
    costs: { provider_cost_paise: number; estimated: boolean; uncosted_runs: number; infrastructure: number | null; infrastructure_reason: string };
    contribution: { direct_paise: number; definition: string };
    beta: {
        spend_paise: number;
        useful_users: number;
        cost_per_useful_user_paise: number | null;
        subsidy_paise: number;
        budget: { state: string; limit_paise: number | null; spent_to_date_paise: number; remaining_paise: number | null; warning: boolean; setting: string };
    };
    cost_per_success: { value_paise: number | null; successes: number; state: string; definition: string };
    trend: Array<{ day: string; charged_paise: number; provider_cost_paise: number }>;
    breakdown: Array<Record<string, number | string>>;
};

const COMPONENTS = ["stt", "llm", "tts", "telephony", "platform"] as const;

/**
 * Revenue, costs and beta economics (screen 37). In the free beta it leads
 * with spend, useful users and the budget left; revenue reads zero when the
 * ledger says zero, and there is no subscription chart because there are no
 * subscriptions. Estimates are labelled, and every total opens the ledger.
 */
export default function RevenuePage() {
    const { days } = useStaffConsole();
    const query = useStaffData<Revenue>("/api/v1/admin/staff/revenue", { days });
    useReportFreshness(query.state, query.refreshedAt);

    const beta = (r: Revenue) => (
        <Panel title={r.free_mode ? "Free beta" : "Beta economics"} query={query}>
            {() => (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="beta">
                    <MetricDefinition name="Spend" value={money(r.beta.spend_paise)} definition="Provider cost in the period." period={`${r.period.start} to ${r.period.end} (IST)`} source="daily_organization_rollups" />
                    <MetricDefinition name="Useful users" value={r.beta.useful_users} definition="Distinct people with a useful outcome in the period." />
                    <MetricDefinition
                        name="Cost per useful user"
                        value={r.beta.cost_per_useful_user_paise === null ? null : money(r.beta.cost_per_useful_user_paise)}
                        definition="Spend divided by useful users."
                        missingReason="Undefined: no useful users in the period."
                    />
                    <div className="min-w-0 space-y-1">
                        <MetricDefinition
                            name="Budget left"
                            value={r.beta.budget.remaining_paise === null ? null : money(r.beta.budget.remaining_paise)}
                            definition={`The beta's total budget less all provider spend to date (${money(r.beta.budget.spent_to_date_paise)}).`}
                            missingReason={`Needs setup: ${r.beta.budget.setting} (founder decision).`}
                        />
                        {r.beta.budget.warning && <StateBadge state="degraded" label="80% spent" />}
                    </div>
                </div>
            )}
        </Panel>
    );

    return (
        <div className="space-y-4">
            <PageHeader
                title="Revenue and costs"
                description="Cash, refunds, recurring value and usage charged are kept apart. Infrastructure is not allocated here."
                actions={
                    <Link href="/superadmin/revenue/ledger" className="inline-flex min-h-11 items-center rounded-md border border-border px-3 text-sm md:min-h-9">
                        Open the ledger
                    </Link>
                }
            />
            {query.data?.free_mode && beta(query.data)}
            <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Revenue" query={query}>
                    {(r) => (
                        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-2 text-sm" data-testid="revenue-group">
                            <dt>Cash collected (incl. GST)</dt>
                            <dd className="text-right tabular-nums">{money(r.revenue.cash_collected_paise)}</dd>
                            <dt className="text-muted-foreground">of which GST</dt>
                            <dd className="text-right tabular-nums text-muted-foreground">{money(r.revenue.tax_paise)}</dd>
                            <dt>Refunds</dt>
                            <dd className="text-right tabular-nums">
                                {money(r.revenue.refunds_paise)}
                                {r.revenue.refunds_pending > 0 && <span className="block text-xs text-[#705500]">{r.revenue.refunds_pending} pending</span>}
                            </dd>
                            <dt className="font-medium">Net cash</dt>
                            <dd className="text-right font-medium tabular-nums">{money(r.revenue.net_cash_paise)}</dd>
                            <dt>Recurring value</dt>
                            <dd className="text-right text-xs text-muted-foreground">{r.revenue.recurring_value_paise === null ? r.revenue.recurring_value_reason : money(r.revenue.recurring_value_paise)}</dd>
                            <dt>
                                Usage charged
                                <span className="block text-xs text-muted-foreground">{r.revenue.usage_charged_note}</span>
                            </dt>
                            <dd className="text-right tabular-nums">{money(r.revenue.usage_charged_paise)}</dd>
                        </dl>
                    )}
                </Panel>
                <Panel title="Direct costs" query={query}>
                    {(r) => (
                        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-2 text-sm" data-testid="cost-group">
                            <dt>
                                Provider cost{" "}
                                {r.costs.estimated && <StateBadge state="partial" label={`Estimated: ${r.costs.uncosted_runs} runs not costed yet`} />}
                            </dt>
                            <dd className="text-right tabular-nums">{money(r.costs.provider_cost_paise)}</dd>
                            <dt>Infrastructure</dt>
                            <dd className="text-right text-xs text-muted-foreground">{r.costs.infrastructure_reason}</dd>
                            <dt className="font-medium">
                                Direct contribution
                                <span className="block text-xs font-normal text-muted-foreground">{r.contribution.definition}</span>
                            </dt>
                            <dd className="text-right font-medium tabular-nums">{money(r.contribution.direct_paise)}</dd>
                            <dt>
                                Cost per success
                                <span className="block text-xs text-muted-foreground">{r.cost_per_success.definition}</span>
                            </dt>
                            <dd className="text-right tabular-nums">
                                {r.cost_per_success.value_paise === null ? <StateBadge state="undefined" label="Undefined (no successes)" /> : money(r.cost_per_success.value_paise)}
                                <span className="block text-xs text-muted-foreground">n={count(r.cost_per_success.successes)}</span>
                            </dd>
                        </dl>
                    )}
                </Panel>
            </div>
            {query.data && !query.data.free_mode && beta(query.data)}
            <Panel title="Daily trend" query={query}>
                {(r) => {
                    const top = Math.max(1, ...r.trend.map((t) => Math.max(t.charged_paise, t.provider_cost_paise)));
                    return (
                        <TableRegion label="Daily trend">
                            <table className="w-full min-w-[480px] text-sm">
                                <thead className="text-left text-xs text-muted-foreground">
                                    <tr>
                                        <th className="py-1 font-normal">Day</th>
                                        <th className="py-1 text-right font-normal">Charged</th>
                                        <th className="py-1 text-right font-normal">Provider cost</th>
                                        <th className="w-1/3 py-1 font-normal">
                                            <span className="sr-only">Bar</span>
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {r.trend.map((t) => (
                                        <tr key={t.day} className="border-t border-border tabular-nums">
                                            <td className="py-1">{t.day}</td>
                                            <td className="py-1 text-right">{money(t.charged_paise)}</td>
                                            <td className="py-1 text-right">{money(t.provider_cost_paise)}</td>
                                            <td className="py-1 pl-2" aria-hidden>
                                                <span className="block h-2 rounded bg-foreground/60" style={{ width: `${(t.provider_cost_paise / top) * 100}%` }} />
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </TableRegion>
                    );
                }}
            </Panel>
            <Panel title="Cost by category" query={query}>
                {(r) => {
                    const totals = COMPONENTS.map((c) => ({ c, v: r.breakdown.reduce((s, d) => s + Number(d[c] ?? 0), 0) }));
                    return (
                        <ul className="space-y-1 text-sm">
                            {totals.map(({ c, v }) => (
                                <li key={c} className="flex justify-between">
                                    <span className="uppercase">{c}</span>
                                    <span className="tabular-nums">{money(v)}</span>
                                </li>
                            ))}
                        </ul>
                    );
                }}
            </Panel>
        </div>
    );
}
