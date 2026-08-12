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
    Download,
    Loader2,
    Wallet,
    XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createTopupApiV1BillingTopupPost,
    getBalanceApiV1BillingBalanceGet,
    getBillingProfileApiV1BillingProfileGet,
    getTaxDocumentPdfApiV1BillingDocumentsDocumentIdPdfGet,
    listPaymentsApiV1BillingPaymentsGet,
    listTaxDocumentsApiV1BillingDocumentsGet,
    saveBillingProfileApiV1BillingProfilePut,
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
    gst_rate_basis_points: number;
    is_export: boolean;
    billing_profile_complete: boolean;
};

type Payment = {
    id: number;
    order_id: string;
    payment_id: string | null;
    /** Credit bought, net of GST. */
    amount_paise: number;
    /** What the card was charged. */
    gross_paise: number;
    tax_paise: number;
    status: string;
    created_at: string | null;
    paid_at: string | null;
};

type BillingProfileFields = {
    legal_name: string | null;
    gstin: string | null;
    address_line1: string | null;
    address_line2: string | null;
    city: string | null;
    state_code: string | null;
    postal_code: string | null;
    country_code: string;
    billing_email: string | null;
};

type TaxDocument = {
    id: number;
    kind: string;
    number: string;
    issued_at: string | null;
    period_start: string | null;
    period_end: string | null;
    taxable_paise: number;
    cgst_paise: number;
    sgst_paise: number;
    igst_paise: number;
    total_paise: number;
    supply_type: string;
};

const EMPTY_PROFILE: BillingProfileFields = {
    legal_name: "",
    gstin: "",
    address_line1: "",
    address_line2: "",
    city: "",
    state_code: "",
    postal_code: "",
    country_code: "IN",
    billing_email: "",
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
    const [profile, setProfile] = useState<BillingProfileFields>(EMPTY_PROFILE);
    const [profileComplete, setProfileComplete] = useState(true);
    const [savingProfile, setSavingProfile] = useState(false);
    const [documents, setDocuments] = useState<TaxDocument[]>([]);
    const [downloadingDocumentId, setDownloadingDocumentId] = useState<number | null>(
        null,
    );

    const refresh = useCallback(async () => {
        const [balanceResponse, paymentsResponse, profileResponse, documentsResponse] =
            await Promise.all([
                getBalanceApiV1BillingBalanceGet(),
                listPaymentsApiV1BillingPaymentsGet(),
                getBillingProfileApiV1BillingProfileGet(),
                listTaxDocumentsApiV1BillingDocumentsGet(),
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

        if (!profileResponse.error) {
            const loaded = profileResponse.data as unknown as {
                profile: BillingProfileFields;
                is_complete: boolean;
            };
            // Nulls become empty strings: a controlled input handed null flips
            // to uncontrolled and React drops the value on the next render.
            setProfile({
                ...EMPTY_PROFILE,
                ...Object.fromEntries(
                    Object.entries(loaded.profile).map(([k, v]) => [k, v ?? ""]),
                ),
                country_code: loaded.profile.country_code || "IN",
            });
            setProfileComplete(loaded.is_complete);
        }
        if (!documentsResponse.error) {
            setDocuments(
                (documentsResponse.data as unknown as { documents: TaxDocument[] })
                    .documents ?? [],
            );
        }

        setError(null);
        return next;
    }, []);

    const saveProfile = useCallback(async () => {
        setError(null);
        setNotice(null);
        setSavingProfile(true);
        try {
            const response = await saveBillingProfileApiV1BillingProfilePut({
                body: {
                    legal_name: profile.legal_name || null,
                    gstin: profile.gstin || null,
                    address_line1: profile.address_line1 || null,
                    address_line2: profile.address_line2 || null,
                    city: profile.city || null,
                    state_code: profile.state_code || null,
                    postal_code: profile.postal_code || null,
                    country_code: profile.country_code || "IN",
                    billing_email: profile.billing_email || null,
                },
            });
            if (response.error) {
                setError(
                    detailFromError(response.error, "Could not save your billing details"),
                );
                return;
            }
            setNotice("Billing details saved.");
            await refresh();
        } finally {
            setSavingProfile(false);
        }
    }, [profile, refresh]);

    const downloadDocument = useCallback(async (doc: TaxDocument) => {
        setError(null);
        setDownloadingDocumentId(doc.id);
        try {
            const response = await getTaxDocumentPdfApiV1BillingDocumentsDocumentIdPdfGet({
                path: { document_id: doc.id },
                parseAs: "blob",
            });
            if (response.error || !response.data) {
                setError(detailFromError(response.error, "Could not download the PDF"));
                return;
            }
            const blob = response.data as Blob;
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `${doc.number.replace(/\//g, "-")}.pdf`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
        } finally {
            setDownloadingDocumentId(null);
        }
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
                gross_paise: number;
                tax_paise: number;
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
                // Gross, not the credit amount. Passing amount_paise here would
                // show the customer a figure 18% below what the order was
                // created for, and Razorpay would reject the mismatch.
                amount: order.gross_paise,
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
    const balancePaise = balance?.balance_paise ?? 0;
    const outOfCredit = balancePaise <= 0;
    // Warn before calls stop, not only after. Someone whose campaign dies
    // mid-run finds out by the campaign dying, which is the worst way to learn
    // it. One minimum top-up's worth of runway is enough notice to act on.
    const runningLow =
        !outOfCredit && balancePaise < (balance?.min_topup_paise ?? 0);

    // What the card will actually be charged, shown before the customer clicks
    // pay. Discovering the tax at the card form reads as a surprise fee.
    const gstRate = (balance?.gst_rate_basis_points ?? 0) / 100;
    const enteredPaise = Math.round((Number(amountRupees) || 0) * 100);
    const taxPaise = Math.round((enteredPaise * (balance?.gst_rate_basis_points ?? 0)) / 10_000);
    const payablePaise = enteredPaise + taxPaise;

    const setProfileField = (field: keyof BillingProfileFields, value: string) =>
        setProfile((current) => ({ ...current, [field]: value }));

    // A GSTIN's first two digits *are* the state code, and the server rejects a
    // profile where the two disagree — correctly, since one of them would be
    // wrong and the choice decides CGST+SGST versus IGST.
    //
    // Left to the operator those two fields are an invitation to contradict
    // yourself: the state field is placeheld "29" while the helper text says
    // "the first two digits of your GSTIN", so a GSTIN starting 33 beside a
    // typed-in 29 is the obvious mistake to make. It then fails with a
    // validation message about state codes on a form where both fields look
    // filled in correctly.
    //
    // So the GSTIN fills the state in, the way the server already does when the
    // state is left blank. Typing a GSTIN is the more specific act, and the
    // field stays editable for anyone who genuinely needs to differ.
    const setGstin = (value: string) =>
        setProfile((current) => {
            const derived = value.length >= 2 && /^\d{2}$/.test(value.slice(0, 2))
                ? value.slice(0, 2)
                : null;
            return {
                ...current,
                gstin: value,
                state_code: derived ?? current.state_code,
            };
        });

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
                        outOfCredit && "text-red-600 dark:text-red-400",
                        runningLow && "text-amber-600 dark:text-amber-400",
                    )}
                >
                    {formatPaise(balancePaise)}
                </div>
                {outOfCredit && (
                    <p className="mt-2 text-sm text-red-600 dark:text-red-400">
                        Calls will not start without credit. Add some to keep your
                        agents running.
                    </p>
                )}
                {runningLow && (
                    <p className="mt-2 text-sm text-amber-600 dark:text-amber-400">
                        Running low. Calls stop as soon as this reaches zero.
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
                                href="mailto:support@decibyl.ai"
                            >
                                support@decibyl.ai
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

                        {/* The whole point of showing this: the customer sees
                            what the card will be charged before they click,
                            rather than discovering the tax at the card form. */}
                        {enteredPaise > 0 && (
                            <dl className="mt-4 max-w-xs space-y-1.5 border-t pt-3 text-sm">
                                <div className="flex justify-between">
                                    <dt className="text-muted-foreground">Credit</dt>
                                    <dd className="tabular-nums">
                                        {formatPaise(enteredPaise)}
                                    </dd>
                                </div>
                                <div className="flex justify-between">
                                    <dt className="text-muted-foreground">
                                        {balance?.is_export
                                            ? "GST (zero-rated export)"
                                            : `GST @ ${gstRate}%`}
                                    </dt>
                                    <dd className="tabular-nums">
                                        {formatPaise(taxPaise)}
                                    </dd>
                                </div>
                                <div className="flex justify-between border-t pt-1.5 font-medium">
                                    <dt>You pay</dt>
                                    <dd className="tabular-nums">
                                        {formatPaise(payablePaise)}
                                    </dd>
                                </div>
                            </dl>
                        )}

                        <p className="mt-3 text-xs text-muted-foreground">
                            Between {formatPaise(balance?.min_topup_paise ?? 0)} and{" "}
                            {formatPaise(balance?.max_topup_paise ?? 0)} of credit per
                            payment. Credit appears once your bank confirms the
                            payment, which is usually within seconds.
                        </p>

                        {!profileComplete && (
                            <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                                Add your billing details below so we can issue a
                                proper tax invoice for this payment.
                            </p>
                        )}
                    </>
                )}
            </section>

            <section className="rounded-xl border bg-card p-6">
                <h2 className="text-lg font-medium">Billing details</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Who your invoices are made out to. Your state decides whether
                    GST is charged as CGST + SGST or as IGST, so it has to match
                    your GSTIN.
                </p>

                <div className="mt-5 grid gap-4 sm:grid-cols-2">
                    <div className="sm:col-span-2">
                        <Label htmlFor="legal-name">Registered business name</Label>
                        <Input
                            id="legal-name"
                            value={profile.legal_name ?? ""}
                            onChange={(e) =>
                                setProfileField("legal_name", e.target.value)
                            }
                            placeholder="Acme Technologies Private Limited"
                            className="mt-1.5"
                        />
                    </div>

                    <div>
                        <Label htmlFor="gstin">GSTIN</Label>
                        <Input
                            id="gstin"
                            value={profile.gstin ?? ""}
                            onChange={(e) => setGstin(e.target.value.toUpperCase())}
                            placeholder="29ABCDE1234F1Z5"
                            maxLength={15}
                            className="mt-1.5 font-mono"
                        />
                        <p className="mt-1 text-xs text-muted-foreground">
                            Leave blank if you are not registered.
                        </p>
                    </div>

                    <div>
                        <Label htmlFor="billing-email">Billing email</Label>
                        <Input
                            id="billing-email"
                            type="email"
                            value={profile.billing_email ?? ""}
                            onChange={(e) =>
                                setProfileField("billing_email", e.target.value)
                            }
                            placeholder="accounts@acme.com"
                            className="mt-1.5"
                        />
                    </div>

                    <div className="sm:col-span-2">
                        <Label htmlFor="address1">Address</Label>
                        <Input
                            id="address1"
                            value={profile.address_line1 ?? ""}
                            onChange={(e) =>
                                setProfileField("address_line1", e.target.value)
                            }
                            placeholder="Street address"
                            className="mt-1.5"
                        />
                        <Input
                            aria-label="Address line 2"
                            value={profile.address_line2 ?? ""}
                            onChange={(e) =>
                                setProfileField("address_line2", e.target.value)
                            }
                            placeholder="Building, floor (optional)"
                            className="mt-2"
                        />
                    </div>

                    <div>
                        <Label htmlFor="city">City</Label>
                        <Input
                            id="city"
                            value={profile.city ?? ""}
                            onChange={(e) => setProfileField("city", e.target.value)}
                            className="mt-1.5"
                        />
                    </div>

                    <div>
                        <Label htmlFor="postal">PIN / postal code</Label>
                        <Input
                            id="postal"
                            value={profile.postal_code ?? ""}
                            onChange={(e) =>
                                setProfileField("postal_code", e.target.value)
                            }
                            className="mt-1.5"
                        />
                    </div>

                    <div>
                        <Label htmlFor="state-code">GST state code</Label>
                        <Input
                            id="state-code"
                            value={profile.state_code ?? ""}
                            onChange={(e) =>
                                setProfileField("state_code", e.target.value)
                            }
                            placeholder="29"
                            maxLength={2}
                            className="mt-1.5 font-mono"
                        />
                        <p className="mt-1 text-xs text-muted-foreground">
                            The first two digits of your GSTIN.
                        </p>
                    </div>

                    <div>
                        <Label htmlFor="country">Country</Label>
                        <Input
                            id="country"
                            value={profile.country_code ?? "IN"}
                            onChange={(e) =>
                                setProfileField(
                                    "country_code",
                                    e.target.value.toUpperCase(),
                                )
                            }
                            placeholder="IN"
                            maxLength={2}
                            className="mt-1.5 font-mono"
                        />
                        <p className="mt-1 text-xs text-muted-foreground">
                            Outside India is a zero-rated export.
                        </p>
                    </div>
                </div>

                <Button
                    type="button"
                    onClick={() => void saveProfile()}
                    disabled={savingProfile}
                    className="mt-5"
                >
                    {savingProfile ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Saving…
                        </>
                    ) : (
                        "Save billing details"
                    )}
                </Button>
            </section>

            <section className="rounded-xl border bg-card p-6">
                <h2 className="text-lg font-medium">Tax documents</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    A receipt voucher for each payment, and a tax invoice each
                    month for what you actually used.
                </p>
                {documents.length === 0 ? (
                    <p className="mt-4 text-sm text-muted-foreground">
                        Nothing issued yet.
                    </p>
                ) : (
                    <div className="mt-4 overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Number</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Date</TableHead>
                                    <TableHead className="text-right">Taxable</TableHead>
                                    <TableHead className="text-right">GST</TableHead>
                                    <TableHead className="text-right">Total</TableHead>
                                    <TableHead className="text-right"></TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {documents.map((doc) => {
                                    const gst =
                                        doc.cgst_paise +
                                        doc.sgst_paise +
                                        doc.igst_paise;
                                    return (
                                        <TableRow key={doc.id}>
                                            <TableCell className="whitespace-nowrap font-mono text-xs">
                                                {doc.number}
                                            </TableCell>
                                            <TableCell className="whitespace-nowrap text-sm">
                                                {doc.kind === "tax_invoice"
                                                    ? "Tax invoice"
                                                    : "Receipt voucher"}
                                            </TableCell>
                                            <TableCell className="whitespace-nowrap">
                                                {formatDateTimeIST(doc.issued_at)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatPaise(doc.taxable_paise)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {doc.supply_type === "export"
                                                    ? "Zero-rated"
                                                    : formatPaise(gst)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatPaise(doc.total_paise)}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8"
                                                    disabled={downloadingDocumentId === doc.id}
                                                    onClick={() => void downloadDocument(doc)}
                                                    aria-label={`Download ${doc.number}`}
                                                >
                                                    {downloadingDocumentId === doc.id ? (
                                                        <Loader2 className="h-4 w-4 animate-spin" />
                                                    ) : (
                                                        <Download className="h-4 w-4" />
                                                    )}
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </div>
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
                                    <TableHead className="text-right">Credit</TableHead>
                                    <TableHead className="text-right">Charged</TableHead>
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
                                        {/* Both, because someone reconciling a
                                            card statement wants the gross while
                                            someone reconciling their balance
                                            wants the net. */}
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(payment.amount_paise)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(
                                                payment.gross_paise ??
                                                    payment.amount_paise,
                                            )}
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
