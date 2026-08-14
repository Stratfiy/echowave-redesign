"use client";

/**
 * Payments and top-ups: what was collected, and what converted.
 *
 * These are two different questions and the screen keeps them apart, because
 * answering both off one timestamp is how a payments dashboard ends up
 * disagreeing with itself:
 *
 * * **Collected** is keyed on when a payment settled. The cash arrived when it
 *   arrived, and a day is credited only for money it actually received.
 * * **Conversion** is keyed on when the order was created — a cohort, the same
 *   rule the activation funnel uses. An order started on Tuesday and paid on
 *   Wednesday converted on Tuesday, which is what keeps the rate stable as the
 *   window widens rather than drifting above 100%.
 *
 * The ledger is reported beside both. Credit can reach an account without a
 * payment behind it — staff top-ups, trial grants, a manual correction — and
 * the gap between "collected" and "credited" is a real reconciliation signal
 * that only exists if both halves are shown.
 */

import { AlertTriangle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import {
    createRefundApiV1AdminBillingRefundsPost,
    getPaymentsApiV1AdminBillingPaymentsGet,
} from "@/client/sdk.gen";
import { seriesColor, STATUS } from "@/components/charts/chartTheme";
import {
    axisProps,
    ChartCard,
    gridStroke,
    LoadingBlock,
    OrderedLegend,
    PanelMessage,
    StatTile,
    useAuthReady,
    useChartMode,
} from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import {
    formatDateIST,
    formatDateTimeIST,
    formatNumber,
    formatPaise,
    formatPaiseCompact,
    formatPercent,
} from "@/lib/billing/format";

const GRANULARITIES = [
    { key: "day", label: "Daily" },
    { key: "week", label: "Weekly" },
    { key: "month", label: "Monthly" },
] as const;

type Granularity = (typeof GRANULARITIES)[number]["key"];

type SeriesRow = {
    period: string;
    payments: number;
    net_paise: number;
    gross_paise: number;
    accounts: number;
};

type AccountRow = {
    organization_id: number;
    name: string;
    payments: number;
    net_paise: number;
    last_paid_at: string | null;
};

type Report = {
    collected: {
        payments: number;
        net_paise: number;
        gross_paise: number;
        tax_paise: number;
        paying_accounts: number;
        average_topup_paise: number | null;
    };
    cohort: {
        started: number;
        paid: number;
        failed: number;
        abandoned: number;
        conversion: number | null;
    };
    series: SeriesRow[];
    top_accounts: AccountRow[];
    ledger: {
        entries: number;
        credited_paise: number;
        unpaid_credit_paise: number;
    };
};

export default function PaymentsPage() {
    const mode = useChartMode();
    const authReady = useAuthReady();

    const [granularity, setGranularity] = useState<Granularity>("day");
    const [data, setData] = useState<Report | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getPaymentsApiV1AdminBillingPaymentsGet({
                query: { granularity },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load payments"));
            } else {
                setData((result.data as unknown as Report) ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, granularity]);

    // Refunding lives on this screen because this is where somebody is when
    // they decide to. There is no per-payment row to hang a button on — the
    // page is a set of charts — so the id is typed, the way it is read off
    // Razorpay's own dashboard or a customer's email.
    const [refundPaymentId, setRefundPaymentId] = useState("");
    const [refundRupees, setRefundRupees] = useState("");
    const [refundReason, setRefundReason] = useState("");
    const [refunding, setRefunding] = useState(false);
    const [refundError, setRefundError] = useState<string | null>(null);
    const [refundDone, setRefundDone] = useState<string | null>(null);

    const submitRefund = async () => {
        if (!refundPaymentId.trim()) return;
        setRefunding(true);
        setRefundError(null);
        setRefundDone(null);
        const result = await createRefundApiV1AdminBillingRefundsPost({
            body: {
                payment_id: Number(refundPaymentId),
                // Blank means everything still refundable, which is the common
                // case and should not need a number typed into it.
                amount_paise: refundRupees.trim()
                    ? Math.round(Number(refundRupees) * 100)
                    : null,
                reason: refundReason.trim() || null,
            },
        });
        if (result.error) {
            setRefundError(detailFromError(result.error, "Could not refund that payment"));
        } else {
            const data = result.data as unknown as {
                amount_paise: number;
                reversed_credit_paise: number;
                refund_id: string | null;
            };
            setRefundDone(
                `Refunded ${formatPaise(data.amount_paise)} and reversed ${formatPaise(
                    data.reversed_credit_paise,
                )} of credit.${data.refund_id ? "" : " Razorpay is not configured, so no money moved — settle it separately."}`,
            );
            setRefundPaymentId("");
            setRefundRupees("");
            setRefundReason("");
        }
        setRefunding(false);
    };

    if (loading && !data) return <LoadingBlock label="Loading payments" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    const collected = data?.collected;
    const cohort = data?.cohort;
    const ledger = data?.ledger;
    const series = data?.series ?? [];
    const topAccounts = data?.top_accounts ?? [];

    // Tax is the difference between the two money figures, so it is drawn as
    // the remainder of a stack rather than as a third bar competing with them.
    const stacked = series.map((row) => ({
        ...row,
        tax_paise: Math.max(0, row.gross_paise - row.net_paise),
    }));

    // Credit granted that no settled payment paid for. Not an error on its own
    // — but a large unexplained figure is the first thing to chase.
    const unpaid = ledger?.unpaid_credit_paise ?? 0;

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-2">
                <Select
                    value={granularity}
                    onValueChange={(v) => setGranularity(v as Granularity)}
                >
                    <SelectTrigger className="w-[150px]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {GRANULARITIES.map((g) => (
                            <SelectItem key={g.key} value={g.key}>
                                {g.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <span className="text-xs text-muted-foreground">
                    Money is bucketed by when a payment settled; conversion by when the
                    order was created.
                </span>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                    label="Collected"
                    value={formatPaiseCompact(collected?.gross_paise)}
                    sub="What customers were charged, tax included"
                />
                <StatTile
                    label="Credit issued"
                    value={formatPaiseCompact(collected?.net_paise)}
                    sub="Net of GST — this is what reaches the ledger"
                />
                <StatTile
                    label="Top-ups"
                    value={formatNumber(collected?.payments)}
                    sub={
                        collected
                            ? `${formatNumber(collected.paying_accounts)} account${
                                  collected.paying_accounts === 1 ? "" : "s"
                              } · ${formatPaiseCompact(
                                  collected.average_topup_paise,
                              )} average`
                            : undefined
                    }
                />
                <StatTile
                    label="Checkout conversion"
                    value={formatPercent(cohort?.conversion)}
                    sub={
                        cohort
                            ? `${formatNumber(cohort.paid)} of ${formatNumber(
                                  cohort.started,
                              )} orders started`
                            : undefined
                    }
                />
            </div>

            <ChartCard
                title="Money collected"
                description="Credit issued and the tax on top of it, by when each payment settled"
                loading={loading}
                error={error}
                isEmpty={series.length === 0}
                emptyMessage="No settled payments in this window."
                height={300}
            >
                <ResponsiveContainer width="100%" height={300}>
                    <BarChart
                        data={stacked}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                    >
                        <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                        <XAxis
                            dataKey="period"
                            tickFormatter={formatDateIST}
                            {...axisProps(mode)}
                        />
                        <YAxis
                            width={68}
                            tickFormatter={(v: number) => formatPaiseCompact(v)}
                            {...axisProps(mode)}
                        />
                        <RTooltip
                            cursor={{ fill: "transparent" }}
                            content={({ active, payload, label }) => {
                                if (!active || !payload?.length) return null;
                                const row = payload[0].payload as SeriesRow & {
                                    tax_paise: number;
                                };
                                const rows: Array<[string, string]> = [
                                    ["Credit issued", formatPaise(row.net_paise)],
                                    ["Tax", formatPaise(row.tax_paise)],
                                    ["Charged", formatPaise(row.gross_paise)],
                                    ["Top-ups", formatNumber(row.payments)],
                                    ["Accounts", formatNumber(row.accounts)],
                                ];
                                return (
                                    <div className="glass-panel rounded-xl px-3 py-2 text-xs">
                                        <p className="mb-1.5 font-medium text-foreground">
                                            {formatDateIST(String(label))}
                                        </p>
                                        <div className="space-y-0.5">
                                            {rows.map(([name, value]) => (
                                                <div
                                                    key={name}
                                                    className="flex items-center justify-between gap-4"
                                                >
                                                    <span className="text-muted-foreground">
                                                        {name}
                                                    </span>
                                                    <span className="font-medium tabular-nums text-foreground">
                                                        {value}
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                );
                            }}
                        />
                        {/* Stacked, because the two parts sum to one real
                            quantity — what the customer was charged. Side by
                            side would invite reading them as rivals. */}
                        <Bar
                            dataKey="net_paise"
                            stackId="money"
                            name="Credit issued"
                            fill={seriesColor(0, mode)}
                        />
                        <Bar
                            dataKey="tax_paise"
                            stackId="money"
                            name="Tax"
                            fill={seriesColor(3, mode)}
                            radius={[3, 3, 0, 0]}
                        />
                    </BarChart>
                </ResponsiveContainer>
                <OrderedLegend
                    items={[
                        {
                            key: "net",
                            label: "Credit issued",
                            color: seriesColor(0, mode),
                        },
                        { key: "tax", label: "Tax", color: seriesColor(3, mode) },
                    ]}
                />
            </ChartCard>

            <div className="grid gap-6 lg:grid-cols-2">
                {/* Three outcomes, not two. An abandoned order is the only one
                    of them a change to the checkout page can move, so it is
                    never folded into failures. */}
                <ChartCard
                    title="What happened to the orders"
                    description="Of the orders started in this window — cohort, so a payment always lands with its order"
                    isEmpty={!cohort || cohort.started === 0}
                    emptyMessage="No orders started in this window."
                    height={200}
                >
                    <div className="space-y-3">
                        {cohort &&
                            (
                                [
                                    ["Paid", cohort.paid, STATUS.good],
                                    ["Abandoned", cohort.abandoned, STATUS.warning],
                                    ["Failed", cohort.failed, STATUS.critical],
                                ] as Array<[string, number, string]>
                            ).map(([label, count, color]) => (
                                <div key={label}>
                                    <div className="mb-1 flex items-center justify-between text-sm">
                                        <span className="flex items-center gap-2">
                                            <span
                                                aria-hidden
                                                className="inline-block h-2 w-2 rounded-[1px]"
                                                style={{ backgroundColor: color }}
                                            />
                                            {label}
                                        </span>
                                        <span className="tabular-nums text-muted-foreground">
                                            {formatNumber(count)}
                                            {cohort.started > 0 && (
                                                <span className="ml-2">
                                                    {formatPercent(
                                                        count / cohort.started,
                                                        0,
                                                    )}
                                                </span>
                                            )}
                                        </span>
                                    </div>
                                    <div className="h-2 w-full overflow-hidden rounded-full bg-foreground/[0.06]">
                                        <div
                                            className="h-full rounded-full"
                                            style={{
                                                width: `${
                                                    cohort.started
                                                        ? (count / cohort.started) * 100
                                                        : 0
                                                }%`,
                                                backgroundColor: color,
                                            }}
                                        />
                                    </div>
                                </div>
                            ))}
                    </div>
                </ChartCard>

                <ChartCard
                    title="Ledger reconciliation"
                    description="Credit granted against payments settled — the gap is credit nobody paid for"
                    isEmpty={!ledger}
                    height={200}
                >
                    <div className="space-y-4 text-sm">
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">
                                Top-up credit issued
                            </span>
                            <span className="font-medium tabular-nums">
                                {formatPaise(ledger?.credited_paise)}
                            </span>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">
                                Paid for by a settled payment
                            </span>
                            <span className="font-medium tabular-nums">
                                {formatPaise(collected?.net_paise)}
                            </span>
                        </div>
                        <div className="flex items-center justify-between border-t border-border pt-3">
                            <span className="text-muted-foreground">
                                Granted without a payment
                            </span>
                            <span
                                className={
                                    unpaid > 0
                                        ? "font-medium tabular-nums text-[color:var(--brand-amber)]"
                                        : "font-medium tabular-nums"
                                }
                            >
                                {formatPaise(unpaid)}
                            </span>
                        </div>
                        <p className="text-xs leading-relaxed text-muted-foreground">
                            Staff credit, promotional grants and manual corrections all
                            reach the ledger without a payment behind them. A figure here
                            is not an error — an unexplained one is where to start looking.
                        </p>
                    </div>
                </ChartCard>
            </div>

            <ChartCard
                title="Who paid"
                description="Accounts by what they topped up in this window"
                isEmpty={topAccounts.length === 0}
                height={200}
            >
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Account</TableHead>
                                <TableHead className="text-right">Top-ups</TableHead>
                                <TableHead className="text-right">Credit issued</TableHead>
                                <TableHead className="text-right">Last payment</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {topAccounts.map((row) => (
                                <TableRow key={row.organization_id}>
                                    <TableCell className="font-medium">
                                        {row.name}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatNumber(row.payments)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(row.net_paise)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatDateTimeIST(row.last_paid_at)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </ChartCard>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-[0.9375rem]">Refund a payment</CardTitle>
                    <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-muted-foreground">
                        Sends the money back through Razorpay and reverses the credit
                        it granted, in one act. Refused if that credit has already
                        been spent — refunding then would hand back the money and keep
                        the service.
                    </p>
                </CardHeader>
                <CardContent>
                    <div className="flex flex-wrap items-end gap-3">
                        <div className="w-32 space-y-1.5">
                            <Label htmlFor="refund-payment" className="text-xs">
                                Payment id
                            </Label>
                            <Input
                                id="refund-payment"
                                inputMode="numeric"
                                value={refundPaymentId}
                                onChange={(e) => setRefundPaymentId(e.target.value)}
                            />
                        </div>
                        <div className="w-36 space-y-1.5">
                            <Label htmlFor="refund-amount" className="text-xs">
                                Amount (₹)
                            </Label>
                            <Input
                                id="refund-amount"
                                inputMode="decimal"
                                placeholder="all of it"
                                value={refundRupees}
                                onChange={(e) => setRefundRupees(e.target.value)}
                            />
                        </div>
                        <div className="min-w-[200px] flex-1 space-y-1.5">
                            <Label htmlFor="refund-reason" className="text-xs">
                                Reason
                            </Label>
                            <Input
                                id="refund-reason"
                                placeholder="Duplicate charge, cancelled order"
                                value={refundReason}
                                onChange={(e) => setRefundReason(e.target.value)}
                            />
                        </div>
                        <Button
                            variant="outline"
                            disabled={refunding || !refundPaymentId.trim()}
                            onClick={() => void submitRefund()}
                        >
                            {refunding && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Refund
                        </Button>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                        Leave the amount blank to refund everything still refundable.
                        Partial refunds take back credit in proportion, so the tax
                        splits correctly on the credit note.
                    </p>
                    {refundError && (
                        <p className="mt-3 text-xs text-destructive">{refundError}</p>
                    )}
                    {refundDone && (
                        <p className="mt-3 text-xs text-foreground">{refundDone}</p>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
