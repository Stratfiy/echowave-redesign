"use client";

/**
 * Of each signup-month cohort of accounts, who is still placing calls N
 * months later.
 *
 * Activation (superadmin/billing/activation) answers who arrived and how far
 * they got, ever — this answers the question activation cannot: who stayed.
 * "Retained" is the same bar activation uses for its own steps: at least one
 * call, anywhere in that calendar month, no minimum spend or duration.
 *
 * A cell for a month that has not happened yet — a cohort formed this month,
 * asked about month 3 — renders as a dash, not 0%. Those look identical as a
 * bare number and are not the same story: one is churn, the other is "ask
 * again later". Shading only applies to a cell with a real answer, so an
 * unelapsed month never reads as a bad one at a glance.
 */

import { useEffect, useState } from "react";

import { getRetentionApiV1AdminBillingRetentionGet } from "@/client/sdk.gen";
import { seriesColor } from "@/components/charts/chartTheme";
import { ChartCard, useAuthReady, useChartMode } from "@/components/charts/primitives";
import { detailFromError } from "@/lib/apiError";
import { formatNumber, formatPercent } from "@/lib/billing/format";

type Cohort = {
    cohort_month: string;
    size: number;
    retention: (number | null)[];
};

type Retention = {
    cohorts: Cohort[];
    offsets: number[];
};

/** e.g. "#2a78d6" → "42, 120, 214", for building an rgba() background whose
 *  own alpha carries the intensity — an element's `opacity` would fade its
 *  text along with its fill, which is the one thing a heatmap cannot do. */
function hexToRgb(hex: string): string {
    const value = hex.replace("#", "");
    const r = parseInt(value.slice(0, 2), 16);
    const g = parseInt(value.slice(2, 4), 16);
    const b = parseInt(value.slice(4, 6), 16);
    return `${r}, ${g}, ${b}`;
}

function monthLabel(iso: string): string {
    const [year, month] = iso.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString("en-IN", {
        month: "short",
        year: "numeric",
        timeZone: "UTC",
    });
}

export default function RetentionPage() {
    const mode = useChartMode();
    const [data, setData] = useState<Retention | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getRetentionApiV1AdminBillingRetentionGet({
                query: { cohort_months: 6, retention_months: 6 },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load retention"));
            } else {
                setData((result.data as unknown as Retention) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    const cohorts = data?.cohorts ?? [];
    const offsets = data?.offsets ?? [];
    const isEmpty = cohorts.every((c) => c.size === 0);

    const baseRgb = hexToRgb(seriesColor(0, mode));

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-lg font-medium">Who stays</h2>
                <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                    Each row is the accounts that signed up in that month. Each
                    column is a fraction still placing at least one call that
                    many months later. Newer cohorts have fewer columns
                    answered — a dash means the month has not happened yet, not
                    that the cohort churned.
                </p>
            </div>

            <ChartCard
                title="Retention by signup cohort"
                description="Month 0 is the signup month itself"
                loading={loading}
                error={error}
                isEmpty={isEmpty}
                emptyMessage="No cohorts with any accounts in this window yet."
                height={320}
            >
                <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-sm">
                        <thead>
                            <tr>
                                <th className="whitespace-nowrap px-2 py-1.5 text-left text-xs font-medium text-muted-foreground">
                                    Cohort
                                </th>
                                <th className="whitespace-nowrap px-2 py-1.5 text-right text-xs font-medium text-muted-foreground">
                                    Accounts
                                </th>
                                {offsets.map((offset) => (
                                    <th
                                        key={offset}
                                        className="whitespace-nowrap px-2 py-1.5 text-center text-xs font-medium text-muted-foreground"
                                    >
                                        Month {offset}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {cohorts.map((cohort) => (
                                <tr key={cohort.cohort_month} className="border-t">
                                    <td className="whitespace-nowrap px-2 py-1.5 font-medium">
                                        {monthLabel(cohort.cohort_month)}
                                    </td>
                                    <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                                        {formatNumber(cohort.size)}
                                    </td>
                                    {cohort.retention.map((value, index) => (
                                        <td
                                            key={index}
                                            className="px-2 py-1.5 text-center font-medium tabular-nums"
                                            style={
                                                value !== null
                                                    ? {
                                                          backgroundColor: `rgba(${baseRgb}, ${(0.12 + value * 0.68).toFixed(2)})`,
                                                      }
                                                    : undefined
                                            }
                                        >
                                            {value !== null ? (
                                                formatPercent(value, 0)
                                            ) : (
                                                <span className="text-muted-foreground/50">
                                                    —
                                                </span>
                                            )}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </ChartCard>
        </div>
    );
}
