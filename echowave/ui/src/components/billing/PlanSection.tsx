"use client";

/**
 * Plans: what each one costs, what it includes, and how to start one.
 *
 * The backend for this has been complete for a while — mandate, idempotent
 * balance grant, rent settled inside the same collection, receipt voucher — and
 * nothing called it. The plan was the launch SKU and could not be bought.
 *
 * Two things this screen has to say plainly, because both are money:
 *
 * **What the bank will actually take.** The plan price is net of GST like every
 * other figure the ledger deals in, and the card is charged the grossed-up
 * amount. Showing ₹2,999 and debiting ₹3,538.82 is the kind of surprise that
 * ends in a chargeback, so both are on screen before the button.
 *
 * **How many numbers are included, and what the next one costs.** The
 * entitlement is a real limit now: the plan pays the rent for the numbers it
 * includes and the next one bills separately every month. A customer should
 * find that out here rather than on an invoice.
 */

import { AlertTriangle, ArrowUpRight, Check, Loader2, Phone } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Plan = {
    code: string;
    label: string;
    blurb: string;
    /** Net of GST, like the ledger. */
    price_paise: number;
    /** What the bank is told to take. Null when the billing profile cannot be
     *  taxed yet, in which case we say so rather than quoting a number that
     *  changes at the card form. */
    gross_paise: number | null;
    balance_paise: number;
    included_numbers: number;
    extra_number_paise: number;
    period: string;
    is_current: boolean;
};

type Numbers = {
    included: number;
    used: number;
    remaining: number;
    extra_paise: number;
};

type Mandate = {
    status: string;
    short_url: string | null;
} | null;

type PlanResponse = {
    plans: Plan[];
    current: Plan | null;
    numbers: Numbers;
    mandate: Mandate;
    configured: boolean;
    billing_profile_complete: boolean;
};

/** Mandate states in which the customer still has to go and authorise. */
const NEEDS_AUTHORISING = new Set(["created", "pending", "authenticated"]);

export function PlanSection({
    onSubscribed,
    billingProfileComplete,
}: {
    onSubscribed?: () => void;
    billingProfileComplete: boolean;
}) {
    const { user, loading: authLoading } = useAuth();
    // Both controls below call ADMIN routes — `POST /api/v1/billing/plan` and
    // `POST /api/v1/billing/mandate/cancel`. Without this a member could read
    // the prices, press "Start this plan", and collect a 403; and an account
    // on a plan showed every member a live "Cancel plan" button for a standing
    // bank instruction they cannot actually withdraw. Presentation only — the
    // server is the boundary either way.
    const { isOrganizationAdmin, loaded: rolesLoaded } = useAccessRoles();
    const [data, setData] = useState<PlanResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [starting, setStarting] = useState<string | null>(null);
    const [cancelling, setCancelling] = useState(false);
    const [confirmingCancel, setConfirmingCancel] = useState(false);

    const refresh = useCallback(async () => {
        const result = await client.get({ url: "/api/v1/billing/plan" });
        if (result.error) {
            setError(detailFromError(result.error, "Could not load plans"));
            return;
        }
        setData((result.data as PlanResponse) ?? null);
        setError(null);
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void refresh();
    }, [authLoading, user, refresh]);

    const subscribe = useCallback(
        async (code: string) => {
            setStarting(code);
            setError(null);
            const result = await client.post({
                url: "/api/v1/billing/plan",
                body: { plan_code: code },
            });
            setStarting(null);
            if (result.error) {
                setError(detailFromError(result.error, "Could not start the plan"));
                return;
            }
            const mandate = (result.data as { mandate: Mandate })?.mandate;
            // The provider hosts the authorisation page. Opened in a new tab
            // rather than navigated to, so closing it returns the customer to a
            // billing page that still knows what it was doing.
            if (mandate?.short_url) {
                window.open(mandate.short_url, "_blank", "noopener");
            }
            await refresh();
            onSubscribed?.();
        },
        [refresh, onSubscribed],
    );

    const cancel = useCallback(async () => {
        setCancelling(true);
        setError(null);
        const result = await client.post({ url: "/api/v1/billing/mandate/cancel" });
        setCancelling(false);
        setConfirmingCancel(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not cancel the plan"));
            return;
        }
        await refresh();
        onSubscribed?.();
    }, [refresh, onSubscribed]);

    if (!data || data.plans.length === 0) return null;

    const { numbers, mandate, current } = data;
    const awaitingAuthorisation =
        mandate !== null && NEEDS_AUTHORISING.has(mandate.status);

    return (
        <section className="rounded-xl border bg-card p-6">
            <h2 className="text-lg font-medium">Plan</h2>
            <p className="mt-1 text-sm text-muted-foreground">
                A phone number and a call balance on one monthly payment, collected
                by your bank.
            </p>

            {!data.configured && (
                <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        Plans are not available on this deployment yet. Add credit
                        directly instead.
                    </span>
                </div>
            )}

            {error && (
                <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>
            )}

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {data.plans.map((plan) => (
                    <div
                        key={plan.code}
                        className={cn(
                            "rounded-lg border p-4",
                            plan.is_current
                                ? "border-primary ring-1 ring-primary"
                                : "border-border",
                        )}
                    >
                        <div className="flex items-center gap-2">
                            <span className="font-medium">{plan.label}</span>
                            {plan.is_current && (
                                <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[0.7rem] font-medium text-primary">
                                    <Check className="h-3 w-3" />
                                    Current
                                </span>
                            )}
                        </div>
                        {plan.blurb && (
                            <p className="mt-1 text-sm text-muted-foreground">
                                {plan.blurb}
                            </p>
                        )}

                        <p className="mt-3">
                            <span className="text-2xl font-semibold tabular-nums">
                                {formatPaise(plan.price_paise)}
                            </span>
                            <span className="text-sm text-muted-foreground">
                                {" "}
                                a month
                            </span>
                        </p>
                        {/* What the bank is actually told to take. Shown next to
                            the headline rather than discovered at the card. */}
                        {plan.gross_paise !== null &&
                            plan.gross_paise !== plan.price_paise && (
                                <p className="text-xs text-muted-foreground">
                                    {formatPaise(plan.gross_paise)} charged, including
                                    GST
                                </p>
                            )}
                        {plan.gross_paise === null && (
                            <p className="text-xs text-amber-600 dark:text-amber-400">
                                Complete your billing details to see what will be
                                charged.
                            </p>
                        )}

                        <ul className="mt-3 space-y-1 text-sm">
                            <li className="flex items-start gap-2">
                                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                                <span>
                                    {formatPaise(plan.balance_paise)} of call balance
                                    each month
                                    {/* Stated at the point of purchase, which is
                                        the only place stating it counts. Balance
                                        that expires is what makes a plan worth
                                        more than a top-up of the same size, and a
                                        customer who finds out from a debit on
                                        their statement reads it as us taking
                                        money back. */}
                                    <span className="block text-xs text-muted-foreground">
                                        Fresh each cycle — unused plan balance does
                                        not carry over. Top-ups you buy never expire.
                                    </span>
                                </span>
                            </li>
                            <li className="flex items-start gap-2">
                                <Phone className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                <span>
                                    {plan.included_numbers === 1
                                        ? "1 phone number included"
                                        : `${plan.included_numbers} phone numbers included`}
                                    {plan.included_numbers > 0 && (
                                        <span className="text-muted-foreground">
                                            {" "}
                                            — extra numbers{" "}
                                            {formatPaise(plan.extra_number_paise)} a
                                            month each
                                        </span>
                                    )}
                                </span>
                            </li>
                        </ul>

                        {!plan.is_current && (
                            <Button
                                type="button"
                                className="mt-4 w-full"
                                disabled={
                                    !data.configured ||
                                    !rolesLoaded ||
                                    !isOrganizationAdmin ||
                                    starting !== null ||
                                    !billingProfileComplete ||
                                    // One plan at a time. Switching is a cancel
                                    // and a re-subscribe, because the bank holds
                                    // an instruction for a specific amount.
                                    current !== null
                                }
                                onClick={() => void subscribe(plan.code)}
                            >
                                {starting === plan.code ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Starting…
                                    </>
                                ) : (
                                    <>
                                        Start this plan
                                        <ArrowUpRight className="ml-1.5 h-4 w-4" />
                                    </>
                                )}
                            </Button>
                        )}
                    </div>
                ))}
            </div>

            {!billingProfileComplete && (
                <p className="mt-3 text-sm text-amber-600 dark:text-amber-400">
                    Add your billing details below before starting a plan — the tax
                    on the collection depends on them.
                </p>
            )}

            {/* Named rather than hidden, for the reason the numbers screen
                names it: a member can see the prices and what is included, so
                the request they take to an admin is a specific one. */}
            {rolesLoaded && !isOrganizationAdmin && (
                <p className="mt-3 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                    Starting or cancelling a plan authorises a monthly debit for
                    the whole account, so only an organization Admin or Owner can
                    do it. Ask one of them.
                </p>
            )}

            {current !== null && (
                <>
                    {awaitingAuthorisation && mandate?.short_url && (
                        <p className="mt-4 text-sm text-amber-600 dark:text-amber-400">
                            Your plan is waiting to be authorised at your bank.{" "}
                            <a
                                className="underline"
                                href={mandate.short_url}
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                Finish authorising
                            </a>
                            . Nothing is collected and no balance arrives until you do.
                        </p>
                    )}

                    {/* The entitlement, stated as a count. It is a real limit:
                        the plan pays the rent for the numbers it includes and
                        the next one bills every month on its own. */}
                    <p className="mt-4 text-sm text-muted-foreground">
                        Numbers: <span className="text-foreground">{numbers.used}</span>{" "}
                        of {numbers.included} included
                        {numbers.remaining > 0
                            ? ` — you can claim ${numbers.remaining} more at no extra cost.`
                            : ` — the next one is ${formatPaise(numbers.extra_paise)} a month.`}
                    </p>

                    {/* Starting a standing instruction the customer cannot stop
                        in the product is a support ticket at best. The endpoint
                        has always existed; nothing called it. */}
                    {confirmingCancel ? (
                        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-900/50 dark:bg-amber-950/30">
                            <p className="text-amber-800 dark:text-amber-300">
                                Cancelling stops the monthly collection and the
                                balance that comes with it. Your number is{" "}
                                <strong>not</strong> released — its rent falls back
                                to your credit balance, and an unpaid rental
                                suspends the number after seven days.
                            </p>
                            <div className="mt-3 flex gap-2">
                                <Button
                                    type="button"
                                    variant="destructive"
                                    size="sm"
                                    disabled={cancelling}
                                    onClick={() => void cancel()}
                                >
                                    {cancelling ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Cancelling…
                                        </>
                                    ) : (
                                        "Cancel the plan"
                                    )}
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setConfirmingCancel(false)}
                                >
                                    Keep it
                                </Button>
                            </div>
                        </div>
                    ) : (
                        rolesLoaded &&
                        isOrganizationAdmin && (
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="mt-3"
                                onClick={() => setConfirmingCancel(true)}
                            >
                                Cancel plan
                            </Button>
                        )
                    )}
                </>
            )}
        </section>
    );
}
