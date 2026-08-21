"use client";

/**
 * Plans, and the arithmetic that decides whether one is worth selling.
 *
 * A plan is a price against a bag of things: some call balance and some phone
 * numbers. The price alone says nothing — ₹2,999 for ₹2,500 of balance and a
 * ₹499 number is a deliberate zero-margin bundle, and the same ₹2,999 against
 * ₹3,500 of balance is a loss nobody typed on purpose.
 *
 * So every row shows the contents at list price beside the price, and their
 * difference. The server refuses the one case that loses money on contact — a
 * plan granting more balance than it collects — because balance is spendable at
 * our cost the moment it lands.
 *
 * Editing a plan does **not** re-price the customers already on it. Their bank
 * holds an instruction for a specific amount and their mandate records the plan
 * code it bought; the monthly grant follows that, not this screen.
 */

import { AlertTriangle, Loader2, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { formatPaise } from "@/lib/billing/format";

type Plan = {
    code: string;
    label: string;
    blurb: string;
    price_paise: number;
    balance_paise: number;
    included_numbers: number;
    extra_number_price_paise: number;
    numbers_value_paise: number;
    parts_paise: number;
    discount_paise: number;
    razorpay_plan_id: string | null;
    enabled: boolean;
    sort_order: number;
};

type Draft = {
    code: string;
    label: string;
    blurb: string;
    priceRupees: string;
    balanceRupees: string;
    includedNumbers: string;
    extraNumberRupees: string;
    razorpayPlanId: string;
    enabled: boolean;
    sortOrder: string;
};

function blankDraft(defaultExtraPaise: number): Draft {
    return {
        code: "",
        label: "",
        blurb: "",
        priceRupees: "",
        balanceRupees: "",
        includedNumbers: "1",
        extraNumberRupees: String(defaultExtraPaise / 100),
        razorpayPlanId: "",
        enabled: true,
        sortOrder: "0",
    };
}

function toDraft(plan: Plan): Draft {
    return {
        code: plan.code,
        label: plan.label,
        blurb: plan.blurb,
        priceRupees: String(plan.price_paise / 100),
        balanceRupees: String(plan.balance_paise / 100),
        includedNumbers: String(plan.included_numbers),
        extraNumberRupees: String(plan.extra_number_price_paise / 100),
        razorpayPlanId: plan.razorpay_plan_id ?? "",
        enabled: plan.enabled,
        sortOrder: String(plan.sort_order),
    };
}

const rupeesToPaise = (value: string): number =>
    Math.round((Number(value) || 0) * 100);

export default function PlansPage() {
    const { user, loading: authLoading } = useAuth();
    const [plans, setPlans] = useState<Plan[] | null>(null);
    const [defaultExtraPaise, setDefaultExtraPaise] = useState(49_900);
    const [draft, setDraft] = useState<Draft | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    const refresh = useCallback(async () => {
        const result = await client.get({ url: "/api/v1/admin/billing/plans" });
        if (result.error) {
            setError(detailFromError(result.error, "Could not load plans"));
            return;
        }
        const data = result.data as {
            plans: Plan[];
            number_rental_price_paise: number;
        };
        setPlans(data.plans ?? []);
        setDefaultExtraPaise(data.number_rental_price_paise ?? 49_900);
        setError(null);
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void refresh();
    }, [authLoading, user, refresh]);

    const save = useCallback(async () => {
        if (!draft) return;
        setSaving(true);
        setError(null);
        const result = await client.put({
            url: "/api/v1/admin/billing/plans",
            body: {
                code: draft.code.trim().toLowerCase(),
                label: draft.label,
                blurb: draft.blurb,
                price_paise: rupeesToPaise(draft.priceRupees),
                balance_paise: rupeesToPaise(draft.balanceRupees),
                included_numbers: Number(draft.includedNumbers) || 0,
                extra_number_price_paise: rupeesToPaise(draft.extraNumberRupees),
                razorpay_plan_id: draft.razorpayPlanId.trim() || null,
                enabled: draft.enabled,
                sort_order: Number(draft.sortOrder) || 0,
            },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not save the plan"));
            return;
        }
        setDraft(null);
        await refresh();
    }, [draft, refresh]);

    if (!plans) {
        return (
            <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        );
    }

    // Live, so the consequence of a number is visible while it is being typed
    // rather than after the save is refused.
    const draftPrice = draft ? rupeesToPaise(draft.priceRupees) : 0;
    const draftBalance = draft ? rupeesToPaise(draft.balanceRupees) : 0;
    const draftParts = draft
        ? draftBalance +
          (Number(draft.includedNumbers) || 0) *
              rupeesToPaise(draft.extraNumberRupees)
        : 0;
    const grantsMoreThanItTakes = draft !== null && draftBalance > draftPrice;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-medium">Plans</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        Every figure is net of GST. The bank is told the grossed-up
                        amount, computed per account against that customer&apos;s own
                        billing profile.
                    </p>
                </div>
                <Button
                    type="button"
                    onClick={() => setDraft(blankDraft(defaultExtraPaise))}
                >
                    <Plus className="mr-1.5 h-4 w-4" />
                    New plan
                </Button>
            </div>

            {error && (
                <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            <div className="overflow-x-auto rounded-xl border bg-card">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Plan</TableHead>
                            <TableHead className="text-right">Price</TableHead>
                            <TableHead className="text-right">Balance</TableHead>
                            <TableHead className="text-right">Numbers</TableHead>
                            <TableHead className="text-right">Contents</TableHead>
                            <TableHead className="text-right">Discount</TableHead>
                            <TableHead />
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {plans.map((plan) => (
                            <TableRow key={plan.code}>
                                <TableCell>
                                    <span className="font-medium">{plan.label}</span>
                                    <span className="ml-2 text-xs text-muted-foreground">
                                        {plan.code}
                                    </span>
                                    {!plan.enabled && (
                                        <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                                            not on sale
                                        </span>
                                    )}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {formatPaise(plan.price_paise)}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {formatPaise(plan.balance_paise)}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {plan.included_numbers}
                                    <span className="ml-1 text-xs text-muted-foreground">
                                        then {formatPaise(plan.extra_number_price_paise)}
                                    </span>
                                </TableCell>
                                <TableCell className="text-right tabular-nums text-muted-foreground">
                                    {formatPaise(plan.parts_paise)}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {plan.discount_paise === 0
                                        ? "—"
                                        : formatPaise(plan.discount_paise)}
                                </TableCell>
                                <TableCell className="text-right">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setDraft(toDraft(plan))}
                                    >
                                        Edit
                                    </Button>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>

            {draft && (
                <div className="rounded-xl border bg-card p-6">
                    <h3 className="font-medium">
                        {plans.some((p) => p.code === draft.code)
                            ? `Edit ${draft.code}`
                            : "New plan"}
                    </h3>

                    <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        <div>
                            <Label htmlFor="plan-code">Code</Label>
                            <Input
                                id="plan-code"
                                value={draft.code}
                                disabled={plans.some((p) => p.code === draft.code)}
                                onChange={(e) =>
                                    setDraft({ ...draft, code: e.target.value })
                                }
                                className="mt-1.5"
                            />
                            <p className="mt-1 text-xs text-muted-foreground">
                                Never renamed once someone is on it — a collection is
                                reconciled against it months later.
                            </p>
                        </div>
                        <div>
                            <Label htmlFor="plan-label">Name</Label>
                            <Input
                                id="plan-label"
                                value={draft.label}
                                onChange={(e) =>
                                    setDraft({ ...draft, label: e.target.value })
                                }
                                className="mt-1.5"
                            />
                        </div>
                        <div>
                            <Label htmlFor="plan-price">Price a month (₹)</Label>
                            <Input
                                id="plan-price"
                                type="number"
                                value={draft.priceRupees}
                                onChange={(e) =>
                                    setDraft({ ...draft, priceRupees: e.target.value })
                                }
                                className="mt-1.5"
                            />
                        </div>
                        <div>
                            <Label htmlFor="plan-balance">Balance granted (₹)</Label>
                            <Input
                                id="plan-balance"
                                type="number"
                                value={draft.balanceRupees}
                                onChange={(e) =>
                                    setDraft({
                                        ...draft,
                                        balanceRupees: e.target.value,
                                    })
                                }
                                className="mt-1.5"
                            />
                        </div>
                        <div>
                            <Label htmlFor="plan-numbers">Numbers included</Label>
                            <Input
                                id="plan-numbers"
                                type="number"
                                min={0}
                                value={draft.includedNumbers}
                                onChange={(e) =>
                                    setDraft({
                                        ...draft,
                                        includedNumbers: e.target.value,
                                    })
                                }
                                className="mt-1.5"
                            />
                        </div>
                        <div>
                            <Label htmlFor="plan-extra">Extra number (₹/month)</Label>
                            <Input
                                id="plan-extra"
                                type="number"
                                value={draft.extraNumberRupees}
                                onChange={(e) =>
                                    setDraft({
                                        ...draft,
                                        extraNumberRupees: e.target.value,
                                    })
                                }
                                className="mt-1.5"
                            />
                        </div>
                        <div className="sm:col-span-2">
                            <Label htmlFor="plan-blurb">One line for the customer</Label>
                            <Input
                                id="plan-blurb"
                                value={draft.blurb}
                                onChange={(e) =>
                                    setDraft({ ...draft, blurb: e.target.value })
                                }
                                className="mt-1.5"
                            />
                        </div>
                        <div>
                            <Label htmlFor="plan-rzp">Razorpay plan id</Label>
                            <Input
                                id="plan-rzp"
                                value={draft.razorpayPlanId}
                                placeholder="plan_..."
                                onChange={(e) =>
                                    setDraft({
                                        ...draft,
                                        razorpayPlanId: e.target.value,
                                    })
                                }
                                className="mt-1.5"
                            />
                            <p className="mt-1 text-xs text-muted-foreground">
                                Create it at Razorpay for the <em>grossed-up</em>
                                amount. Left blank, one is created on the fly and the
                                provider&apos;s own reporting fragments.
                            </p>
                        </div>
                    </div>

                    {/* The consequence of the numbers above, while they are being
                        typed. The server refuses the loss-making case; this is so
                        nobody has to submit to find that out. */}
                    <dl className="mt-4 max-w-sm space-y-1 border-t pt-3 text-sm">
                        <div className="flex justify-between">
                            <dt className="text-muted-foreground">Contents at list</dt>
                            <dd className="tabular-nums">{formatPaise(draftParts)}</dd>
                        </div>
                        <div className="flex justify-between">
                            <dt className="text-muted-foreground">Price</dt>
                            <dd className="tabular-nums">{formatPaise(draftPrice)}</dd>
                        </div>
                        <div className="flex justify-between font-medium">
                            <dt>Discount</dt>
                            <dd className="tabular-nums">
                                {formatPaise(draftParts - draftPrice)}
                            </dd>
                        </div>
                    </dl>

                    {grantsMoreThanItTakes && (
                        <p className="mt-3 flex items-start gap-1.5 text-sm text-red-600 dark:text-red-400">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            <span>
                                This grants more balance than it collects. Balance is
                                spent at our cost, so every cycle loses{" "}
                                {formatPaise(draftBalance - draftPrice)}.
                            </span>
                        </p>
                    )}

                    <div className="mt-4 flex gap-2">
                        <Button
                            type="button"
                            onClick={() => void save()}
                            disabled={saving || grantsMoreThanItTakes}
                        >
                            {saving ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Saving…
                                </>
                            ) : (
                                "Save plan"
                            )}
                        </Button>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setDraft(null)}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() =>
                                setDraft({ ...draft, enabled: !draft.enabled })
                            }
                        >
                            {draft.enabled ? "Withdraw from sale" : "Put on sale"}
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
