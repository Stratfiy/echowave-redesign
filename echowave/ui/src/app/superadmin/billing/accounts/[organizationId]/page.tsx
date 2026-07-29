"use client";

import { AlertTriangle, ArrowLeft, Check, Loader2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

import {
    adjustCreditApiV1AdminBillingAccountsOrganizationIdCreditPost,
    getAccountApiV1AdminBillingAccountsOrganizationIdGet,
    setPlatformRateApiV1AdminBillingAccountsOrganizationIdPlatformRatePut,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import {
    formatDateIST,
    formatDateTimeIST,
    formatMs,
    formatPaise,
    formatPaiseCompact,
    formatPercent,
    formatRateMpaise,
} from "@/lib/billing/format";
import { cn } from "@/lib/utils";

import { COST_COMPONENTS, seriesColor } from "../../_components/chartTheme";
import {
    axisProps,
    ChartCard,
    ChartTooltip,
    gridStroke,
    LoadingBlock,
    PanelMessage,
    StatTile,
    useAuthReady,
    useChartMode,
} from "../../_components/primitives";

export default function AccountDetailPage() {
    const params = useParams<{ organizationId: string }>();
    const organizationId = Number(params.organizationId);
    const mode = useChartMode();

    const [data, setData] = useState<Record<string, unknown> | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await getAccountApiV1AdminBillingAccountsOrganizationIdGet({
            path: { organization_id: organizationId },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Failed to load account"));
        } else {
            setData((result.data as Record<string, unknown>) ?? null);
            setError(null);
        }
        setLoading(false);
    }, [organizationId]);

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady || !Number.isFinite(organizationId)) return;
        void load();
    }, [authReady, organizationId, load]);

    const account = data?.account as Record<string, never> | undefined;
    const daily = (data?.daily ?? []) as Array<Record<string, number | string>>;
    const cost = (data?.cost_composition ?? []) as Array<Record<string, number | string>>;
    const byLanguage = (data?.latency_by_language ?? []) as Array<Record<string, never>>;
    const rateHistory = (data?.rate_history ?? []) as Array<Record<string, never>>;
    const ledger = (data?.credit_ledger ?? []) as Array<Record<string, never>>;

    // Summing ~30 rows; memoising it would only destabilise the dependency on
    // `daily`, which is rebuilt from `data` on every render anyway.
    const totals = daily.reduce<{
        revenue: number;
        cost: number;
        margin: number;
        minutes: number;
    }>(
        (acc, day) => ({
            revenue: acc.revenue + Number(day.charged_paise ?? 0),
            cost: acc.cost + Number(day.provider_cost_paise ?? 0),
            margin: acc.margin + Number(day.margin_paise ?? 0),
            minutes: acc.minutes + Number(day.billable_minutes ?? 0),
        }),
        { revenue: 0, cost: 0, margin: 0, minutes: 0 },
    );

    if (loading && !data) return <LoadingBlock label="Loading account" />;
    if (error && !data) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={200}>
                {error}
            </PanelMessage>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-3">
                <Link
                    href="/superadmin/billing/accounts"
                    className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                >
                    <ArrowLeft className="h-4 w-4" />
                    Accounts
                </Link>
                <h2 className="text-lg font-semibold">{String(account?.name ?? "")}</h2>
                {account?.account_type && (
                    <Badge variant="secondary" className="capitalize">
                        {String(account.account_type)}
                    </Badge>
                )}
            </div>

            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <StatTile label="Revenue" value={formatPaise(totals.revenue)} />
                <StatTile label="Provider cost" value={formatPaise(totals.cost)} />
                <StatTile
                    label="Gross margin"
                    value={formatPaise(totals.margin)}
                    sub={formatPercent(totals.revenue ? totals.margin / totals.revenue : null)}
                />
                <StatTile
                    label="Credit balance"
                    value={formatPaise(Number(account?.balance_paise ?? 0))}
                    tone={Number(account?.balance_paise ?? 0) < 0 ? "critical" : undefined}
                />
                <StatTile
                    label="Platform rate"
                    value={`${formatRateMpaise(Number(account?.platform_rate_mpaise ?? 0))}/min`}
                    sub={account?.platform_rate_is_override ? "Account override" : "Global default"}
                />
            </section>

            <div className="grid gap-4 lg:grid-cols-2">
                <ChartCard
                    title="Usage and revenue"
                    description="Revenue and provider cost per day — the gap is the margin"
                    isEmpty={!daily.some((d) => Number(d.charged_paise) > 0)}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            {/* One axis only: revenue in paise. Minutes live in the
                                companion chart rather than a second y-scale. */}
                            <YAxis
                                width={62}
                                tickFormatter={(v: number) => formatPaiseCompact(v)}
                                {...axisProps(mode)}
                            />
                            <RTooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatPaise(v)}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Legend iconType="plainline" iconSize={12} wrapperStyle={{ fontSize: 11 }} />
                            <Line
                                type="monotone"
                                dataKey="charged_paise"
                                name="Revenue"
                                stroke={seriesColor(0, mode)}
                                strokeWidth={2}
                                dot={false}
                            />
                            <Line
                                type="monotone"
                                dataKey="provider_cost_paise"
                                name="Provider cost"
                                stroke={seriesColor(1, mode)}
                                strokeWidth={2}
                                dot={false}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Cost breakdown"
                    description="Where this account's spend goes"
                    isEmpty={!cost.some((d) => COST_COMPONENTS.some((c) => Number(d[c.key]) > 0))}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <AreaChart data={cost} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="day" tickFormatter={formatDateIST} {...axisProps(mode)} />
                            <YAxis
                                width={62}
                                tickFormatter={(v: number) => formatPaiseCompact(v)}
                                {...axisProps(mode)}
                            />
                            <RTooltip
                                content={
                                    <ChartTooltip
                                        formatter={(v) => formatPaise(v)}
                                        labelFormatter={formatDateIST}
                                    />
                                }
                            />
                            <Legend iconType="square" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                            {COST_COMPONENTS.map((component) => (
                                <Area
                                    key={component.key}
                                    type="monotone"
                                    dataKey={component.key}
                                    name={component.label}
                                    stackId="cost"
                                    stroke={seriesColor(component.slot, mode)}
                                    strokeWidth={2}
                                    fill={seriesColor(component.slot, mode)}
                                    fillOpacity={0.85}
                                />
                            ))}
                        </AreaChart>
                    </ResponsiveContainer>
                </ChartCard>

                <ChartCard
                    title="Latency by language"
                    description="p50 and p95 per language — where a slow language shows itself"
                    isEmpty={byLanguage.length === 0}
                >
                    <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={byLanguage} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={gridStroke(mode)} vertical={false} />
                            <XAxis dataKey="language" {...axisProps(mode)} />
                            <YAxis width={62} tickFormatter={(v: number) => formatMs(v)} {...axisProps(mode)} />
                            <RTooltip
                                cursor={{ fill: gridStroke(mode), opacity: 0.4 }}
                                content={<ChartTooltip formatter={(v) => formatMs(v)} />}
                            />
                            <Legend iconType="square" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                            <Bar dataKey="p50_ms" name="p50" fill={seriesColor(0, mode)} radius={[4, 4, 0, 0]} maxBarSize={22} />
                            <Bar dataKey="p95_ms" name="p95" fill={seriesColor(1, mode)} radius={[4, 4, 0, 0]} maxBarSize={22} />
                        </BarChart>
                    </ResponsiveContainer>
                </ChartCard>

                <RateSettingsPanel
                    organizationId={organizationId}
                    currentRateMpaise={Number(account?.platform_rate_mpaise ?? 0)}
                    rateHistory={rateHistory}
                    onChanged={load}
                />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Credit ledger</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {ledger.length === 0 ? (
                            <PanelMessage height={140}>No credit movements yet.</PanelMessage>
                        ) : (
                            <div
                                className="max-h-[340px] overflow-auto"
                                // The mask fades the last row instead of slicing
                                // it, so a full ledger reads as "more below"
                                // rather than as a rendering fault.
                                style={{
                                    maskImage:
                                        "linear-gradient(to bottom, #000 calc(100% - 2rem), transparent)",
                                    WebkitMaskImage:
                                        "linear-gradient(to bottom, #000 calc(100% - 2rem), transparent)",
                                }}
                            >
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>When</TableHead>
                                            <TableHead>Kind</TableHead>
                                            <TableHead className="text-right">Change</TableHead>
                                            <TableHead className="text-right">Balance</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {ledger.map((entry) => (
                                            <TableRow key={String(entry.id)}>
                                                <TableCell className="whitespace-nowrap text-muted-foreground">
                                                    {formatDateTimeIST(String(entry.created_at ?? ""))}
                                                </TableCell>
                                                <TableCell className="capitalize">{String(entry.kind)}</TableCell>
                                                <TableCell
                                                    className={cn(
                                                        "text-right tabular-nums",
                                                        Number(entry.delta_paise) < 0
                                                            ? "text-muted-foreground"
                                                            : "text-emerald-600 dark:text-emerald-400",
                                                    )}
                                                >
                                                    {formatPaise(Number(entry.delta_paise))}
                                                </TableCell>
                                                <TableCell className="text-right tabular-nums">
                                                    {formatPaise(Number(entry.balance_after_paise))}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        )}
                    </CardContent>
                </Card>

                <CreditAdjustPanel organizationId={organizationId} onChanged={load} />
            </div>
        </div>
    );
}

function RateSettingsPanel({
    organizationId,
    currentRateMpaise,
    rateHistory,
    onChanged,
}: {
    organizationId: number;
    currentRateMpaise: number;
    rateHistory: Array<Record<string, never>>;
    onChanged: () => Promise<void>;
}) {
    const [rupees, setRupees] = useState((currentRateMpaise / 100_000).toFixed(2));
    const [effectiveFrom, setEffectiveFrom] = useState("");
    const [note, setNote] = useState("");
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [failure, setFailure] = useState<string | null>(null);

    const submit = async () => {
        setSaving(true);
        setMessage(null);
        setFailure(null);
        // ₹ per minute → millipaise: ×100 paise ×1000 millipaise.
        const mpaise = Math.round(Number(rupees) * 100 * 1000);
        const result = await setPlatformRateApiV1AdminBillingAccountsOrganizationIdPlatformRatePut({
            path: { organization_id: organizationId },
            body: {
                platform_rate_mpaise: mpaise,
                effective_from: effectiveFrom ? new Date(effectiveFrom).toISOString() : null,
                note: note || null,
            },
        });
        if (result.error) {
            setFailure(detailFromError(result.error, "Failed to set rate"));
        } else {
            setMessage("Rate updated");
            setNote("");
            await onChanged();
        }
        setSaving(false);
    };

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Rate settings</CardTitle>
                <p className="mt-0.5 text-xs text-muted-foreground">
                    Rates are effective-dated. Changing one closes the current row and opens a
                    new one, so historical invoices keep their original numbers.
                </p>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                        <Label htmlFor="rate" className="text-xs">
                            Platform rate (₹/min)
                        </Label>
                        <Input
                            id="rate"
                            type="number"
                            step="0.01"
                            min="0"
                            value={rupees}
                            onChange={(e) => setRupees(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label htmlFor="effective" className="text-xs">
                            Effective from
                        </Label>
                        <Input
                            id="effective"
                            type="datetime-local"
                            value={effectiveFrom}
                            onChange={(e) => setEffectiveFrom(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label htmlFor="note" className="text-xs">
                            Note
                        </Label>
                        <Input
                            id="note"
                            value={note}
                            placeholder="Why this changed"
                            onChange={(e) => setNote(e.target.value)}
                        />
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <Button size="sm" onClick={submit} disabled={saving}>
                        {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                        Set rate
                    </Button>
                    {message && (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                            <Check className="h-3.5 w-3.5" />
                            {message}
                        </span>
                    )}
                    {failure && <span className="text-xs text-destructive">{failure}</span>}
                </div>

                <div>
                    <p className="mb-1.5 text-xs font-medium text-muted-foreground">Rate history</p>
                    {rateHistory.length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                            No overrides — this account is on the global default.
                        </p>
                    ) : (
                        // The two nowrap timestamp columns push the note past the
                        // card edge on a narrow panel; scroll rather than clip it.
                        <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="text-right">Rate/min</TableHead>
                                    <TableHead>From</TableHead>
                                    <TableHead>To</TableHead>
                                    <TableHead className="min-w-[10rem]">Note</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {rateHistory.map((row) => (
                                    <TableRow key={String(row.id)}>
                                        <TableCell className="text-right tabular-nums">
                                            {formatRateMpaise(Number(row.platform_rate_mpaise))}
                                        </TableCell>
                                        {/* Dates, not timestamps: rate periods read
                                            as days, and the two full timestamps
                                            crowded the note off this panel. The
                                            exact instant stays on hover. */}
                                        <TableCell
                                            className="whitespace-nowrap text-muted-foreground"
                                            title={formatDateTimeIST(
                                                String(row.effective_from ?? ""),
                                            )}
                                        >
                                            {formatDateIST(String(row.effective_from ?? ""))}
                                        </TableCell>
                                        <TableCell
                                            className="whitespace-nowrap text-muted-foreground"
                                            title={
                                                row.effective_to
                                                    ? formatDateTimeIST(String(row.effective_to))
                                                    : undefined
                                            }
                                        >
                                            {row.effective_to ? (
                                                formatDateIST(String(row.effective_to))
                                            ) : (
                                                <Badge variant="outline">Current</Badge>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-muted-foreground">
                                            {String(row.note ?? "—")}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}

function CreditAdjustPanel({
    organizationId,
    onChanged,
}: {
    organizationId: number;
    onChanged: () => Promise<void>;
}) {
    const [amount, setAmount] = useState("");
    const [note, setNote] = useState("");
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [failure, setFailure] = useState<string | null>(null);

    const submit = async () => {
        setSaving(true);
        setMessage(null);
        setFailure(null);
        const result = await adjustCreditApiV1AdminBillingAccountsOrganizationIdCreditPost({
            path: { organization_id: organizationId },
            body: { delta_paise: Math.round(Number(amount) * 100), note },
        });
        if (result.error) {
            setFailure(detailFromError(result.error, "Failed to adjust credit"));
        } else {
            setMessage("Credit adjusted");
            setAmount("");
            setNote("");
            await onChanged();
        }
        setSaving(false);
    };

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Adjust credit</CardTitle>
                <p className="mt-0.5 text-xs text-muted-foreground">
                    Positive credits, negative debits. Every adjustment is audited and needs a note.
                </p>
            </CardHeader>
            <CardContent className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                        <Label htmlFor="amount" className="text-xs">
                            Amount (₹)
                        </Label>
                        <Input
                            id="amount"
                            type="number"
                            step="0.01"
                            value={amount}
                            placeholder="e.g. 5000 or -250"
                            onChange={(e) => setAmount(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label htmlFor="credit-note" className="text-xs">
                            Note (required)
                        </Label>
                        <Input
                            id="credit-note"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                        />
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <Button
                        size="sm"
                        variant="outline"
                        onClick={submit}
                        disabled={saving || !amount || !note.trim()}
                    >
                        {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                        Apply adjustment
                    </Button>
                    {message && (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                            <Check className="h-3.5 w-3.5" />
                            {message}
                        </span>
                    )}
                    {failure && <span className="text-xs text-destructive">{failure}</span>}
                </div>
            </CardContent>
        </Card>
    );
}
