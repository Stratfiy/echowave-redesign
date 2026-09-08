"use client";

/**
 * Switching on an automatic debit, and being honest about what it will do.
 *
 * Three things this screen must never do, each of which is a support ticket or
 * a chargeback:
 *
 * 1. **Show an inviting toggle with no payment method behind it.** The customer
 *    turns it on, believes their balance is safe, and finds out it was not by
 *    running out mid-campaign. So the toggle is disabled without an instrument
 *    and says why.
 * 2. **Read "on" while a run of declines has stopped it.** `paused_reason`
 *    comes back from the API precisely so this can say what happened, rather
 *    than showing a confident switch over a feature that gave up days ago.
 * 3. **Surprise anybody.** A scheduled debit is announced here as well as by
 *    email, with the date and a way to stop it, because the notice period is
 *    only meaningful if it is visible somewhere the customer already looks.
 *
 * Amounts are entered in rupees and held in paise, matching the rest of
 * billing: every amount a customer types is net of GST, and the card is charged
 * that plus tax exactly as a manual top-up is.
 */

import { AlertTriangle, CreditCard, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";

interface Instrument {
    method: string | null;
    hint: string | null;
    max_amount_paise: number | null;
}

interface Pending {
    amount_paise: number;
    notified_at: string | null;
    charge_after: string | null;
    status: string;
}

interface AutoTopup {
    enabled: boolean;
    amount_paise: number;
    trigger_days: number;
    trigger_paise: number;
    monthly_cap_paise: number;
    max_per_month: number;
    paused_reason: string | null;
    instrument: Instrument | null;
    pending: Pending | null;
    notice_hours: number;
    minimum_paise: number;
}

// Fetched directly rather than through the generated client, which predates
// these endpoints. Delete after `npm run generate-client`.
async function call<T>(
    path: string,
    token: string | null | undefined,
    init?: RequestInit,
): Promise<T | null> {
    const response = await fetch(`${resolveBrowserBackendUrl()}/api/v1${path}`, {
        ...init,
        headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
            ...(init?.headers ?? {}),
        },
    });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(
            typeof body?.detail === "string" ? body.detail : "Something went wrong.",
        );
    }
    return (await response.json()) as T;
}

function rupeesFromPaise(paise: number): string {
    return paise > 0 ? String(Math.round(paise / 100)) : "";
}

export function AutoTopupSection({ onChanged }: { onChanged?: () => void }) {
    const { user, loading: authLoading, getAccessToken } = useAuth();
    const { isOrganizationAdmin: isAdmin } = useAccessRoles();

    const [data, setData] = useState<AutoTopup | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [amount, setAmount] = useState("");
    const [days, setDays] = useState("5");
    const [perMonth, setPerMonth] = useState("4");
    const hasFetched = useRef(false);

    const apply = useCallback((next: AutoTopup) => {
        setData(next);
        setAmount(rupeesFromPaise(next.amount_paise));
        setDays(String(next.trigger_days));
        setPerMonth(String(next.max_per_month));
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const token = await getAccessToken();
            const next = await call<AutoTopup>("/billing/auto-topup", token);
            if (next) apply(next);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not load this.");
        } finally {
            setLoading(false);
        }
    }, [getAccessToken, apply]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const save = useCallback(
        async (enabled: boolean) => {
            if (!data) return;
            setSaving(true);
            setError(null);
            try {
                const token = await getAccessToken();
                const next = await call<AutoTopup>("/billing/auto-topup", token, {
                    method: "PUT",
                    body: JSON.stringify({
                        enabled,
                        amount_paise: Math.round(Number(amount || 0) * 100),
                        trigger_days: Number(days || 5),
                        trigger_paise: data.trigger_paise,
                        monthly_cap_paise: data.monthly_cap_paise,
                        max_per_month: Number(perMonth || 4),
                    }),
                });
                if (next) apply(next);
                onChanged?.();
            } catch (e) {
                setError(e instanceof Error ? e.message : "Could not save this.");
            } finally {
                setSaving(false);
            }
        },
        [data, amount, days, perMonth, getAccessToken, apply, onChanged],
    );

    const cancelPending = useCallback(async () => {
        setSaving(true);
        try {
            const token = await getAccessToken();
            await call("/billing/auto-topup/cancel-pending", token, { method: "POST" });
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not stop it.");
        } finally {
            setSaving(false);
        }
    }, [getAccessToken, load]);

    if (loading) {
        return (
            <section className="rounded-xl border bg-card p-6">
                <Skeleton className="h-6 w-48" />
                <Skeleton className="mt-4 h-20 w-full" />
            </section>
        );
    }

    if (!data) return null;

    const hasInstrument = data.instrument !== null;
    const noticeDays = Math.round(data.notice_hours / 24);

    return (
        <section className="rounded-xl border bg-card p-6">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="text-lg font-medium">Automatic top-up</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Keep credit topped up so calls never stop for want of
                        balance. We tell you {noticeDays === 1 ? "a day" : `${noticeDays} days`}{" "}
                        before every charge, and you can stop it.
                    </p>
                </div>
                {data.enabled && !data.paused_reason && (
                    <span className="shrink-0 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                        On
                    </span>
                )}
            </div>

            {/* Why nothing is happening, when nothing is happening. A confident
                toggle above a feature that gave up days ago is the worst state
                this screen can be in. */}
            {data.paused_reason && (
                <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        {data.paused_reason} Check your payment method, then save
                        below to start again.
                    </span>
                </div>
            )}

            {data.pending && (
                <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900 dark:border-blue-900/50 dark:bg-blue-950/30 dark:text-blue-200">
                    <p>
                        A top-up of{" "}
                        <strong>{formatPaise(data.pending.amount_paise)}</strong> is
                        scheduled
                        {data.pending.charge_after
                            ? ` for ${new Date(data.pending.charge_after).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}`
                            : ""}
                        .
                    </p>
                    {isAdmin && data.pending.status === "scheduled" && (
                        <Button
                            variant="outline"
                            size="sm"
                            className="mt-3"
                            disabled={saving}
                            onClick={() => void cancelPending()}
                        >
                            Don&apos;t charge me
                        </Button>
                    )}
                </div>
            )}

            {!hasInstrument ? (
                // The state to be honest about. An enabled setting with nothing
                // behind it is a promise the product cannot keep, and the
                // customer would only discover it by running out of credit.
                <div className="mt-4 flex items-start gap-2 rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
                    <CreditCard className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        No saved payment method yet. Add credit once and choose to
                        save the method for automatic top-ups — then this can be
                        switched on.
                    </span>
                </div>
            ) : (
                <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
                    <CreditCard className="h-4 w-4 shrink-0" />
                    {data.instrument?.method ?? "Saved method"}
                    {data.instrument?.hint ? ` ending ${data.instrument.hint}` : ""}
                    {data.instrument?.max_amount_paise
                        ? ` — authorised up to ${formatPaise(data.instrument.max_amount_paise)} per charge`
                        : ""}
                </p>
            )}

            <div className="mt-5 grid gap-4 sm:grid-cols-3">
                <div className="space-y-1.5">
                    <Label htmlFor="at-amount">Top up by (₹)</Label>
                    <Input
                        id="at-amount"
                        inputMode="numeric"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        disabled={!isAdmin || !hasInstrument}
                        placeholder={String(Math.round(data.minimum_paise / 100))}
                    />
                </div>
                <div className="space-y-1.5">
                    <Label htmlFor="at-days">When credit falls below (days)</Label>
                    <Input
                        id="at-days"
                        inputMode="numeric"
                        value={days}
                        onChange={(e) => setDays(e.target.value)}
                        disabled={!isAdmin || !hasInstrument}
                    />
                </div>
                <div className="space-y-1.5">
                    <Label htmlFor="at-max">At most, per month</Label>
                    <Input
                        id="at-max"
                        inputMode="numeric"
                        value={perMonth}
                        onChange={(e) => setPerMonth(e.target.value)}
                        disabled={!isAdmin || !hasInstrument}
                    />
                </div>
            </div>

            <p className="mt-2 text-xs text-muted-foreground">
                Judged on your recent spending, so it scales with how much you
                actually call. GST is added at checkout, as it is on any top-up.
            </p>

            {error && (
                <p className="mt-3 text-sm text-destructive" role="alert">
                    {error}
                </p>
            )}

            {isAdmin && (
                <div className="mt-5 flex flex-wrap gap-2">
                    <Button
                        disabled={saving || !hasInstrument}
                        onClick={() => void save(true)}
                    >
                        {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {data.enabled && !data.paused_reason
                            ? "Save changes"
                            : "Switch on"}
                    </Button>
                    {data.enabled && (
                        <Button
                            variant="outline"
                            disabled={saving}
                            onClick={() => void save(false)}
                        >
                            Switch off
                        </Button>
                    )}
                </div>
            )}
        </section>
    );
}
