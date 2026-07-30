"use client";

/**
 * Prepaid credit — the customer's side of it.
 *
 * Decibyl is prepaid, so this screen answers two questions and no others: how
 * much credit is left, and how to add more. Everything about what that credit
 * was spent on lives under Agent Runs, which is where someone goes when the
 * number surprises them.
 *
 * The important behaviour here is what happens *after* Razorpay's checkout
 * closes. The browser saying "paid" is not evidence of payment — only the
 * signature-verified webhook credits an account. So the success handler does
 * not add anything to the displayed balance; it re-reads it from the server,
 * and says plainly that a moment's delay is normal. Optimistically showing new
 * credit would be showing money that may never arrive.
 */

import {
    AlertTriangle,
    ArrowUpRight,
    CheckCircle2,
    Clock,
    Loader2,
    Wallet,
    XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createTopupApiV1BillingTopupPost,
    getBalanceApiV1BillingBalanceGet,
    listPaymentsApiV1BillingPaymentsGet,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDateTimeIST, formatPaise } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

const RAZORPAY_CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

/** Rupee amounts offered as one click. Chosen to bracket a month of ordinary
 *  usage rather than to anchor high. */
const QUICK_AMOUNTS_RUPEES = [500, 2000, 5000, 20000];

/** How long to keep re-reading the balance after checkout closes. The webhook
 *  usually lands in a second or two; this covers a slow one without spinning
 *  forever if it never comes. */
const POLL_ATTEMPTS = 10;
const POLL_INTERVAL_MS = 2000;

type Balance = {
    balance_paise: number;
    topups_enabled: boolean;
    min_topup_paise: number;
    max_topup_paise: number;
};

type Payment = {
    id: number;
    order_id: string;
    payment_id: string | null;
    amount_paise: number;
    status: string;
    created_at: string | null;
    paid_at: string | null;
};

type RazorpayOptions = {
    key: string;
    amount: number;
    currency: string;
    order_id: string;
    name: string;
    description: string;
    handler: () => void;
    modal: { ondismiss: () => void };
    theme: { color: string };
};

declare global {
    interface Window {
        Razorpay?: new (options: RazorpayOptions) => { open: () => void };
    }
}

/** Load Razorpay's checkout script once, on demand.
 *
 *  Not in the app shell: it is a third-party script on every page load for a
 *  screen most people visit once a month. */
function loadCheckout(): Promise<void> {
    if (typeof window === "undefined") return Promise.reject(new Error("no window"));
    if (window.Razorpay) return Promise.resolve();

    return new Promise((resolve, reject) => {
        const existing = document.querySelector<HTMLScriptElement>(
            `script[src="${RAZORPAY_CHECKOUT_SRC}"]`,
        );
        if (existing) {
            existing.addEventListener("load", () => resolve());
            existing.addEventListener("error", () =>
                reject(new Error("Could not load the payment window.")),
            );
            return;
        }
        const script = document.createElement("script");
        script.src = RAZORPAY_CHECKOUT_SRC;
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () =>
            reject(new Error("Could not load the payment window."));
        document.body.appendChild(script);
    });
}

function StatusBadge({ status }: { status: string }) {
    const shape =
        status === "paid"
            ? {
                  icon: CheckCircle2,
                  label: "Paid",
                  className: "text-emerald-600 dark:text-emerald-400",
              }
            : status === "failed"
              ? {
                    icon: XCircle,
                    label: "Failed",
                    className: "text-red-600 dark:text-red-400",
                }
              : {
                    icon: Clock,
                    label: "Pending",
                    className: "text-muted-foreground",
                };
    const Icon = shape.icon;
    return (
        <span className={cn("inline-flex items-center gap-1.5 text-sm", shape.className)}>
            <Icon className="h-3.5 w-3.5" />
            {shape.label}
        </span>
    );
}

export default function BillingPage() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    const [balance, setBalance] = useState<Balance | null>(null);
    const [payments, setPayments] = useState<Payment[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [amountRupees, setAmountRupees] = useState("2000");
    const [starting, setStarting] = useState(false);
    const [awaitingCredit, setAwaitingCredit] = useState(false);

    const refresh = useCallback(async () => {
        const [balanceResponse, paymentsResponse] = await Promise.all([
            getBalanceApiV1BillingBalanceGet(),
            listPaymentsApiV1BillingPaymentsGet(),
        ]);

        if (balanceResponse.error) {
            setError(
                detailFromError(balanceResponse.error, "Could not load your balance"),
            );
            return null;
        }
        if (paymentsResponse.error) {
            setError(
                detailFromError(paymentsResponse.error, "Could not load your payments"),
            );
            return null;
        }

        const next = balanceResponse.data as unknown as Balance;
        setBalance(next);
        setPayments(
            ((paymentsResponse.data as unknown as { payments: Payment[] }).payments) ??
                [],
        );
        setError(null);
        return next;
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            await refresh();
            setLoading(false);
        })();
    }, [authLoading, user, refresh]);

    // Any timer still pending when the screen unmounts would keep firing
    // requests against a page nobody is looking at.
    useEffect(() => {
        return () => {
            if (pollTimer.current) clearTimeout(pollTimer.current);
        };
    }, []);

    /** Re-read the balance until it moves, or until we give up saying so.
     *
     *  The credit arrives by webhook, out of band from this browser, so there
     *  is nothing to await — only to watch for. */
    const waitForCredit = useCallback(
        (before: number, attempt = 0) => {
            pollTimer.current = setTimeout(() => {
                void (async () => {
                    const next = await refresh();
                    if (next && next.balance_paise > before) {
                        setAwaitingCredit(false);
                        setNotice("Payment received. Your credit is available now.");
                        return;
                    }
                    if (attempt + 1 >= POLL_ATTEMPTS) {
                        setAwaitingCredit(false);
                        setNotice(
                            "Your payment is being confirmed. Credit usually appears " +
                                "within a minute — refresh the page, or contact support " +
                                "if it does not.",
                        );
                        return;
                    }
                    waitForCredit(before, attempt + 1);
                })();
            }, POLL_INTERVAL_MS);
        },
        [refresh],
    );

    const startTopup = useCallback(async () => {
        setError(null);
        setNotice(null);

        const rupees = Number(amountRupees);
        if (!Number.isFinite(rupees) || rupees <= 0) {
            setError("Enter an amount in rupees.");
            return;
        }
        // Rupees in the box, paise on the wire. Rounded rather than truncated so
        // a pasted "499.995" does not quietly become ₹499.99.
        const amountPaise = Math.round(rupees * 100);

        setStarting(true);
        try {
            await loadCheckout();

            const response = await createTopupApiV1BillingTopupPost({
                body: { amount_paise: amountPaise },
            });
            if (response.error) {
                setError(detailFromError(response.error, "Could not start the payment"));
                return;
            }

            const order = response.data as unknown as {
                order_id: string;
                amount_paise: number;
                currency: string;
                key_id: string;
            };
            const balanceBefore = balance?.balance_paise ?? 0;

            const Checkout = window.Razorpay;
            if (!Checkout) {
                setError("The payment window did not load. Try again.");
                return;
            }

            new Checkout({
                key: order.key_id,
                amount: order.amount_paise,
                currency: order.currency,
                order_id: order.order_id,
                name: "Decibyl",
                description: "Prepaid credit",
                handler: () => {
                    // Deliberately does not credit anything locally. This
                    // callback is an unauthenticated client claiming success.
                    setAwaitingCredit(true);
                    setNotice("Payment submitted. Confirming with the bank…");
                    waitForCredit(balanceBefore);
                },
                modal: {
                    ondismiss: () => {
                        // Closing the window is not a failure worth an error —
                        // the order simply stays pending and expires.
                        void refresh();
                    },
                },
                theme: { color: "#6366f1" },
            }).open();
        } catch (loadError) {
            setError(
                loadError instanceof Error
                    ? loadError.message
                    : "Could not start the payment.",
            );
        } finally {
            setStarting(false);
        }
    }, [amountRupees, balance, refresh, waitForCredit]);

    if (loading) {
        return (
            <div className="mx-auto max-w-4xl space-y-6 p-6">
                <Skeleton className="h-32 w-full" />
                <Skeleton className="h-64 w-full" />
            </div>
        );
    }

    const topupsEnabled = balance?.topups_enabled ?? false;
    const lowBalance = (balance?.balance_paise ?? 0) <= 0;

    return (
        <div className="mx-auto max-w-4xl space-y-8 p-6">
            <div>
                <h1 className="text-2xl font-semibold">Billing</h1>
                <p className="mt-1 text-sm text-muted-foreground">
                    Decibyl is prepaid. Calls run while there is credit on the
                    account.
                </p>
            </div>

            {error && (
                <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            {notice && (
                <div className="flex items-start gap-2 rounded-lg border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-700 dark:border-indigo-900/50 dark:bg-indigo-950/30 dark:text-indigo-300">
                    {awaitingCredit ? (
                        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
                    ) : (
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                    )}
                    <span>{notice}</span>
                </div>
            )}

            <section className="rounded-xl border bg-card p-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Wallet className="h-4 w-4" />
                    Available credit
                </div>
                <div
                    className={cn(
                        "mt-2 text-4xl font-semibold tabular-nums",
                        lowBalance && "text-red-600 dark:text-red-400",
                    )}
                >
                    {formatPaise(balance?.balance_paise ?? 0)}
                </div>
                {lowBalance && (
                    <p className="mt-2 text-sm text-red-600 dark:text-red-400">
                        Calls will not start without credit. Add some to keep your
                        agents running.
                    </p>
                )}
            </section>

            <section className="rounded-xl border bg-card p-6">
                <h2 className="text-lg font-medium">Add credit</h2>

                {!topupsEnabled ? (
                    <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>
                            Online top-ups are unavailable right now. Email{" "}
                            <a
                                className="underline"
                                href="mailto:support@decibyl.com"
                            >
                                support@decibyl.com
                            </a>{" "}
                            and we will add credit directly.
                        </span>
                    </div>
                ) : (
                    <>
                        <div className="mt-4 flex flex-wrap gap-2">
                            {QUICK_AMOUNTS_RUPEES.map((rupees) => (
                                <Button
                                    key={rupees}
                                    type="button"
                                    variant={
                                        amountRupees === String(rupees)
                                            ? "default"
                                            : "outline"
                                    }
                                    onClick={() => setAmountRupees(String(rupees))}
                                >
                                    ₹{rupees.toLocaleString("en-IN")}
                                </Button>
                            ))}
                        </div>

                        <div className="mt-4 flex flex-wrap items-end gap-3">
                            <div className="w-48">
                                <Label htmlFor="topup-amount">Amount (₹)</Label>
                                <Input
                                    id="topup-amount"
                                    type="number"
                                    inputMode="decimal"
                                    min={(balance?.min_topup_paise ?? 0) / 100}
                                    max={(balance?.max_topup_paise ?? 0) / 100}
                                    value={amountRupees}
                                    onChange={(event) =>
                                        setAmountRupees(event.target.value)
                                    }
                                    className="mt-1.5"
                                />
                            </div>
                            <Button
                                type="button"
                                onClick={() => void startTopup()}
                                disabled={starting || awaitingCredit}
                            >
                                {starting ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        Opening…
                                    </>
                                ) : (
                                    <>
                                        Pay with Razorpay
                                        <ArrowUpRight className="ml-1.5 h-4 w-4" />
                                    </>
                                )}
                            </Button>
                        </div>

                        <p className="mt-3 text-xs text-muted-foreground">
                            Between {formatPaise(balance?.min_topup_paise ?? 0)} and{" "}
                            {formatPaise(balance?.max_topup_paise ?? 0)} per payment.
                            Credit appears once your bank confirms the payment, which
                            is usually within seconds. GST is charged as applicable.
                        </p>
                    </>
                )}
            </section>

            <section className="rounded-xl border bg-card p-6">
                <h2 className="text-lg font-medium">Payment history</h2>
                {payments.length === 0 ? (
                    <p className="mt-4 text-sm text-muted-foreground">
                        No payments yet.
                    </p>
                ) : (
                    <div className="mt-4 overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Date</TableHead>
                                    <TableHead>Amount</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Reference</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {payments.map((payment) => (
                                    <TableRow key={payment.id}>
                                        <TableCell className="whitespace-nowrap">
                                            {formatDateTimeIST(
                                                payment.paid_at ?? payment.created_at,
                                            )}
                                        </TableCell>
                                        <TableCell className="tabular-nums">
                                            {formatPaise(payment.amount_paise)}
                                        </TableCell>
                                        <TableCell>
                                            <StatusBadge status={payment.status} />
                                        </TableCell>
                                        <TableCell className="font-mono text-xs text-muted-foreground">
                                            {/* The payment id is what support and
                                                the bank both recognise; the order
                                                id only exists until it is paid. */}
                                            {payment.payment_id ?? payment.order_id}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                )}
            </section>
        </div>
    );
}
