"use client";

/**
 * How much is left in the accounts Decibyl pays from.
 *
 * The provider-keys grid answers "is this key good?". This answers the
 * question that grid cannot: a key with an empty account behind it is a
 * perfectly valid key, passes every check on that screen, and fails on the
 * next real call. See api/services/configuration/provider_balance.py.
 *
 * Two things the layout is doing deliberately. Accounts that need a top-up
 * sort to the top, because a screen where the one emergency is alphabetised
 * between two healthy rows is a screen nobody reads in time. And a vendor with
 * no balance API renders as a plain sentence saying so, never as a blank or a
 * zero — a blank next to OpenAI would read as "no credit" and send someone
 * chasing an outage that is not happening.
 */

import { AlertTriangle, Loader2, RefreshCw, Wallet } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { readProviderBalancesApiV1AdminProviderKeysBalancesGet } from "@/client/sdk.gen";
import { providerLabel } from "@/components/providerCards";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";

export type Balance = {
    provider: string;
    status: "ok" | "low" | "empty" | "unsupported" | "unreachable" | "unconfigured";
    kind: "money" | "quota" | null;
    amount: number | null;
    currency: string | null;
    used: number | null;
    limit: number | null;
    remaining: number | null;
    renews_at: string | null;
    detail: string | null;
};

/** Attention first, then everything else alphabetically. */
export const ORDER: Record<Balance["status"], number> = {
    empty: 0,
    low: 1,
    ok: 2,
    unreachable: 3,
    unconfigured: 4,
    unsupported: 5,
};

export function sortBalances(balances: Balance[]): Balance[] {
    return [...balances].sort(
        (a, b) => ORDER[a.status] - ORDER[b.status] || a.provider.localeCompare(b.provider),
    );
}

const PILL: Record<Balance["status"], string> = {
    empty: "bg-destructive/10 text-destructive ring-destructive/30",
    low: "bg-amber-500/10 text-amber-700 ring-amber-500/30 dark:text-amber-400",
    ok: "bg-green-500/10 text-green-700 ring-green-500/30 dark:text-green-400",
    unreachable: "bg-foreground/[0.06] text-muted-foreground ring-foreground/10",
    unconfigured: "bg-foreground/[0.06] text-muted-foreground ring-foreground/10",
    unsupported: "bg-foreground/[0.06] text-muted-foreground ring-foreground/10",
};

export const PILL_TEXT: Record<Balance["status"], string> = {
    empty: "Empty",
    low: "Running low",
    ok: "Healthy",
    unreachable: "Could not read",
    unconfigured: "Not connected",
    unsupported: "No balance API",
};

/** The figure itself, in the unit the vendor denominates it in. */
export function figure(balance: Balance): string | null {
    if (balance.remaining === null) return null;
    if (balance.kind === "quota") {
        const left = balance.remaining.toLocaleString(undefined, {
            maximumFractionDigits: 0,
        });
        const ceiling = (balance.limit ?? 0).toLocaleString(undefined, {
            maximumFractionDigits: 0,
        });
        return `${left} of ${ceiling} characters`;
    }
    const amount = balance.remaining.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    // No currency means the vendor did not tell us one — Plivo does not — and
    // stamping a symbol on it would be a guess a reader would take literally.
    return balance.currency ? `${amount} ${balance.currency.toUpperCase()}` : amount;
}

function renewal(balance: Balance): string | null {
    if (!balance.renews_at) return null;
    const when = new Date(balance.renews_at);
    if (Number.isNaN(when.getTime())) return null;
    return `renews ${when.toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
    })}`;
}

export function ProviderBalances() {
    const [balances, setBalances] = useState<Balance[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await readProviderBalancesApiV1AdminProviderKeysBalancesGet({});
        if (result.error) {
            setError(detailFromResult(result, "Could not read provider balances"));
        } else {
            setBalances(((result.data as unknown as { balances: Balance[] })?.balances) ?? []);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const rows = sortBalances(balances ?? []);
    const attention = rows.filter((row) => row.status === "low" || row.status === "empty");

    return (
        <section className="glass-panel mb-5 px-5 py-4">
            <div className="mb-3 flex items-center gap-2">
                <Wallet className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                    Account balances
                </h2>
                {attention.length > 0 && (
                    <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive ring-1 ring-destructive/30">
                        {attention.length} need{attention.length === 1 ? "s" : ""} a top-up
                    </span>
                )}
                <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto h-7 gap-1.5 px-2 text-xs"
                    onClick={() => void load()}
                    disabled={loading}
                >
                    {loading ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                    )}
                    Refresh
                </Button>
            </div>

            <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
                What is left in the accounts these keys draw on. A key can be valid and its
                account empty — that is what running out of credit looks like, and every
                other check on this page passes straight through it.
            </p>

            {error && (
                <p className="flex items-center gap-2 text-sm text-destructive">
                    <AlertTriangle className="h-4 w-4" />
                    {error}
                </p>
            )}

            {!error && rows.length === 0 && !loading && (
                <p className="text-sm text-muted-foreground">Nothing to report yet.</p>
            )}

            {rows.length > 0 && (
                <ul className="divide-y divide-foreground/[0.06]">
                    {rows.map((balance) => {
                        const value = figure(balance);
                        const renews = renewal(balance);
                        return (
                            <li
                                key={balance.provider}
                                className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5"
                            >
                                <span className="text-sm font-medium text-foreground">
                                    {providerLabel(balance.provider)}
                                </span>
                                <span
                                    className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${PILL[balance.status]}`}
                                >
                                    {PILL_TEXT[balance.status]}
                                </span>
                                {value && (
                                    <span className="text-sm tabular-nums text-foreground">
                                        {value}
                                    </span>
                                )}
                                {renews && (
                                    <span className="text-xs text-muted-foreground">{renews}</span>
                                )}
                                {balance.detail && (
                                    <span className="w-full text-xs text-muted-foreground sm:ml-auto sm:w-auto sm:text-right">
                                        {balance.detail}
                                    </span>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
        </section>
    );
}
