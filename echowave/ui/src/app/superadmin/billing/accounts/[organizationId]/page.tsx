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
    getCommissionHistoryApiV1AdminPartnersAccountsOrganizationIdCommissionGet,
    listPlatformManagedConfigurationsApiV1AdminTelephonyConfigurationsGet,
    setAccountPlatformRateApiV1AdminBillingAccountsOrganizationIdPlatformRatePut,
    setCommissionApiV1AdminPartnersAccountsOrganizationIdCommissionPut,
    setPlatformManagedApiV1AdminTelephonyConfigurationsConfigIdPlatformManagedPut,
} from "@/client/sdk.gen";
import { COST_COMPONENTS, seriesColor } from "@/components/charts/chartTheme";
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
} from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
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
import { detailFromError, detailFromResult } from "@/lib/apiError";
import {
    formatDateIST,
    formatDateTimeIST,
    formatMicrosUsd,
    formatMs,
    formatPaise,
    formatPaiseCompact,
    formatPercent,
    formatRateMpaise,
} from "@/lib/billing/format";
import { cn } from "@/lib/utils";

/** Ledger kinds, in words rather than in column values.
 *
 * `capitalize` alone renders `plan_expiry` as "Plan_expiry". The kinds that
 * take money need to read as sentences on a screen somebody opens when a
 * customer is asking why a debit is there.
 */
const LEDGER_KIND_LABELS: Record<string, string> = {
    plan: "Plan balance",
    plan_expiry: "Plan balance expired",
    topup: "Top-up",
    usage: "Usage",
    reservation: "Hold",
    rental: "Number rental",
    adjustment: "Adjustment",
    trial: "Trial credit",
};

function ledgerKindLabel(kind: string): string {
    return LEDGER_KIND_LABELS[kind] ?? kind;
}

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
            setError(detailFromResult(result, "Failed to load account"));
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
    // Owner first, then Admin, then earliest member — the API returns them in
    // that order, so whoever is listed first is the person to escalate to.
    const members = (account?.members ?? []) as unknown as {
        email: string | null;
        role: string;
    }[];
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

            {/* Who to write to. Support arrives on this page from a flag on the
                accounts table — low balance, idle, a failed payment — and the
                next question is always who to contact. Before this the page
                showed the money and named nobody. */}
            {members.length > 0 && (
                <section className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                    {members.map((member) => (
                        <span key={member.email} className="inline-flex items-center gap-1.5">
                            <a
                                href={`mailto:${member.email}`}
                                className="text-muted-foreground hover:text-foreground hover:underline"
                            >
                                {member.email}
                            </a>
                            <Badge variant="outline" className="capitalize">
                                {member.role}
                            </Badge>
                        </span>
                    ))}
                </section>
            )}

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
                    value={
                        account?.platform_rate_micros_usd != null
                            ? `${formatMicrosUsd(Number(account.platform_rate_micros_usd))}/min`
                            : `${formatRateMpaise(Number(account?.platform_rate_mpaise ?? 0))}/min`
                    }
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
                                                <TableCell className="capitalize">{ledgerKindLabel(String(entry.kind))}</TableCell>
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

            <TelephonyPanel organizationId={organizationId} />
            <CommissionPanel organizationId={organizationId} />
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
    const [currency, setCurrency] = useState<"usd" | "inr">("inr");
    const [rupees, setRupees] = useState((currentRateMpaise / 100_000).toFixed(2));
    const [pulse, setPulse] = useState("");
    const [effectiveFrom, setEffectiveFrom] = useState("");
    const [note, setNote] = useState("");
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [failure, setFailure] = useState<string | null>(null);

    const submit = async () => {
        setSaving(true);
        setMessage(null);
        setFailure(null);
        const value = Number(rupees);
        // Exactly one currency reaches the API. A dollar price follows the
        // exchange rate; a rupee one deliberately does not, which is the whole
        // reason both are offered.
        const price =
            currency === "usd"
                ? { platform_rate_micros_usd: Math.round(value * 1_000_000) }
                : // ₹ per minute → millipaise: ×100 paise ×1000 millipaise.
                  { platform_rate_mpaise: Math.round(value * 100 * 1000) };
        const result = await setAccountPlatformRateApiV1AdminBillingAccountsOrganizationIdPlatformRatePut({
            path: { organization_id: organizationId },
            body: {
                ...price,
                pulse_seconds: pulse ? Number(pulse) : null,
                effective_from: effectiveFrom ? new Date(effectiveFrom).toISOString() : null,
                note: note || null,
            },
        });
        if (result.error) {
            setFailure(detailFromResult(result, "Failed to set rate"));
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
                <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
                    <div>
                        <Label htmlFor="rate-currency" className="text-xs">
                            Quoted in
                        </Label>
                        <Select
                            value={currency}
                            onValueChange={(v) => setCurrency(v as "usd" | "inr")}
                        >
                            <SelectTrigger id="rate-currency">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="inr">Rupees</SelectItem>
                                <SelectItem value="usd">US dollars</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label htmlFor="rate" className="text-xs">
                            Platform rate ({currency === "usd" ? "$" : "₹"}/min)
                        </Label>
                        <Input
                            id="rate"
                            type="number"
                            step={currency === "usd" ? "0.001" : "0.01"}
                            min="0"
                            value={rupees}
                            onChange={(e) => setRupees(e.target.value)}
                        />
                    </div>
                    <div>
                        <Label htmlFor="rate-pulse" className="text-xs">
                            Pulse (s)
                        </Label>
                        <Input
                            id="rate-pulse"
                            type="number"
                            min="1"
                            max="60"
                            value={pulse}
                            placeholder="default"
                            onChange={(e) => setPulse(e.target.value)}
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
                                            {/* A dollar-quoted row has no fixed
                                                rupee value; showing one would
                                                invent a number nobody agreed. */}
                                            {row.platform_rate_micros_usd != null
                                                ? formatMicrosUsd(
                                                      Number(row.platform_rate_micros_usd),
                                                  )
                                                : formatRateMpaise(
                                                      Number(row.platform_rate_mpaise),
                                                  )}
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
            setFailure(detailFromResult(result, "Failed to adjust credit"));
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

type TelephonyConfigSummary = {
    id: number;
    organization_id: number;
    name: string;
    provider: string;
    is_platform_managed: boolean;
    is_default_outbound: boolean;
    created_at: string | null;
};

/**
 * Whether this account's numbers sit under Decibyl's carrier account — ours
 * to bill carriage on — or its own, where we bill none at all.
 *
 * The route this writes to (`PUT .../platform-managed`) already refuses a
 * carrier outside `carrier_rates.MANAGED_CARRIER_ALLOWLIST` (a rollout
 * decision — only Plivo, for now) or whose rate is still a stand-in; this
 * panel surfaces whatever it says rather than second-guessing it, so an
 * operator sees the real reason a toggle was refused instead of one this
 * screen invented.
 */
function TelephonyPanel({ organizationId }: { organizationId: number }) {
    const [configs, setConfigs] = useState<TelephonyConfigSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<number | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await listPlatformManagedConfigurationsApiV1AdminTelephonyConfigurationsGet({
            query: { organization_id: organizationId },
        });
        if (result.error) {
            setError(detailFromResult(result, "Failed to load telephony configurations"));
        } else {
            const data = result.data as { configurations?: TelephonyConfigSummary[] };
            setConfigs(data.configurations ?? []);
            setError(null);
        }
        setLoading(false);
    }, [organizationId]);

    useEffect(() => {
        void load();
    }, [load]);

    const toggle = async (config: TelephonyConfigSummary) => {
        setBusyId(config.id);
        setError(null);
        const result = await setPlatformManagedApiV1AdminTelephonyConfigurationsConfigIdPlatformManagedPut({
            path: { config_id: config.id },
            body: { managed: !config.is_platform_managed },
        });
        if (result.error) {
            setError(
                detailFromError(
                    result.error,
                    config.is_platform_managed
                        ? "Failed to remove from platform-managed"
                        : "Failed to mark platform-managed",
                ),
            );
        } else {
            await load();
        }
        setBusyId(null);
    };

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Telephony</CardTitle>
                <p className="mt-0.5 text-xs text-muted-foreground">
                    Platform-managed means this account&apos;s calls run on Decibyl&apos;s
                    carrier account and we bill the minutes. Its own carrier means we bill
                    no carriage at all.
                </p>
            </CardHeader>
            <CardContent className="space-y-3">
                {error && <p className="text-sm text-destructive">{error}</p>}
                {loading ? (
                    <PanelMessage height={80}>Loading…</PanelMessage>
                ) : configs.length === 0 ? (
                    <PanelMessage height={80}>
                        No telephony configuration on this account yet.
                    </PanelMessage>
                ) : (
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Carrier</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Action</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {configs.map((c) => (
                                <TableRow key={c.id}>
                                    <TableCell>{c.name}</TableCell>
                                    <TableCell>
                                        <Badge variant="outline" className="capitalize">
                                            {c.provider}
                                        </Badge>
                                    </TableCell>
                                    <TableCell>
                                        {c.is_platform_managed ? (
                                            <Badge className="bg-green-600 hover:bg-green-600">
                                                Platform-managed
                                            </Badge>
                                        ) : (
                                            <Badge variant="secondary">
                                                Customer&apos;s own carrier
                                            </Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={busyId === c.id}
                                            onClick={() => toggle(c)}
                                        >
                                            {busyId === c.id && (
                                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                            )}
                                            {c.is_platform_managed
                                                ? "Remove from managed"
                                                : "Mark platform-managed"}
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                )}
            </CardContent>
        </Card>
    );
}

type CommissionRecord = {
    id: number;
    commission_bps: number;
    basis: string;
    application_id: number | null;
    effective_from: string;
    effective_to: string | null;
    note: string | null;
};

/**
 * What this account is paid as a partner, and the history behind it.
 *
 * Harmless to render on an account that has never been a partner — the
 * history is simply empty — so this is not gated on the account actually
 * having an approved application. Setting a rate here is "renegotiate,
 * without a new application"; it closes the open rate and starts a new one
 * rather than editing it, so an already-issued statement still reproduces
 * its own number.
 */
function CommissionPanel({ organizationId }: { organizationId: number }) {
    const [history, setHistory] = useState<CommissionRecord[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [editing, setEditing] = useState(false);
    const [bps, setBps] = useState("");
    const [basis, setBasis] = useState<"platform_fee" | "total_spend">("platform_fee");
    const [note, setNote] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await getCommissionHistoryApiV1AdminPartnersAccountsOrganizationIdCommissionGet({
            path: { organization_id: organizationId },
        });
        if (result.error) {
            setError(detailFromResult(result, "Failed to load commission history"));
        } else {
            const data = result.data as { commissions?: CommissionRecord[] };
            setHistory(data.commissions ?? []);
            setError(null);
        }
        setLoading(false);
    }, [organizationId]);

    useEffect(() => {
        void load();
    }, [load]);

    const current = history.find((c) => c.effective_to === null) ?? null;

    const startEditing = () => {
        setBps(current ? String(current.commission_bps) : "");
        setBasis((current?.basis as "platform_fee" | "total_spend") ?? "platform_fee");
        setNote("");
        setSaveError(null);
        setEditing(true);
    };

    const save = async () => {
        const value = Number(bps);
        if (!Number.isFinite(value) || value < 0 || value > 10_000) {
            setSaveError("Basis points must be between 0 and 10,000 (100%).");
            return;
        }
        setSaving(true);
        setSaveError(null);
        const result = await setCommissionApiV1AdminPartnersAccountsOrganizationIdCommissionPut({
            path: { organization_id: organizationId },
            body: { commission_bps: value, basis, note: note.trim() || null },
        });
        if (result.error) {
            setSaveError(detailFromResult(result, "Failed to set commission"));
        } else {
            setEditing(false);
            await load();
        }
        setSaving(false);
    };

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Partner commission</CardTitle>
                <p className="mt-0.5 text-xs text-muted-foreground">
                    What this account is paid for referrals. Renegotiating closes the
                    current rate and starts a new one, so an already-issued statement
                    keeps its own number.
                </p>
            </CardHeader>
            <CardContent className="space-y-4">
                {error && <p className="text-sm text-destructive">{error}</p>}
                {loading ? (
                    <PanelMessage height={80}>Loading…</PanelMessage>
                ) : (
                    <>
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            {current ? (
                                <div className="text-sm">
                                    <span className="font-medium tabular-nums">
                                        {(current.commission_bps / 100).toFixed(2)}%
                                    </span>{" "}
                                    <span className="text-muted-foreground">
                                        of{" "}
                                        {current.basis === "total_spend"
                                            ? "everything the account is charged"
                                            : "what we keep"}
                                        , since {formatDateIST(current.effective_from)}
                                    </span>
                                </div>
                            ) : (
                                <p className="text-sm text-muted-foreground">
                                    Not on a commission rate. This account is not currently
                                    paid for referrals.
                                </p>
                            )}
                            {!editing && (
                                <Button variant="outline" size="sm" onClick={startEditing}>
                                    {current ? "Renegotiate" : "Set a rate"}
                                </Button>
                            )}
                        </div>

                        {editing && (
                            <div className="space-y-3 rounded-lg border p-3">
                                <div className="grid gap-3 sm:grid-cols-3">
                                    <div className="space-y-1">
                                        <Label htmlFor="commission-bps" className="text-xs">
                                            Basis points
                                        </Label>
                                        <Input
                                            id="commission-bps"
                                            type="number"
                                            min={0}
                                            max={10_000}
                                            value={bps}
                                            onChange={(e) => setBps(e.target.value)}
                                            placeholder="e.g. 1250 for 12.5%"
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <Label className="text-xs">Basis</Label>
                                        <Select
                                            value={basis}
                                            onValueChange={(v) => setBasis(v as typeof basis)}
                                        >
                                            <SelectTrigger>
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="platform_fee">
                                                    Platform fee (what we keep)
                                                </SelectItem>
                                                <SelectItem value="total_spend">
                                                    Total spend (everything charged)
                                                </SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor="commission-note" className="text-xs">
                                            Note (shown to the partner)
                                        </Label>
                                        <Input
                                            id="commission-note"
                                            value={note}
                                            onChange={(e) => setNote(e.target.value)}
                                            placeholder="optional"
                                        />
                                    </div>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Button size="sm" onClick={() => void save()} disabled={saving}>
                                        {saving && (
                                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                        )}
                                        Save
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        onClick={() => setEditing(false)}
                                        disabled={saving}
                                    >
                                        Cancel
                                    </Button>
                                    {saveError && (
                                        <span className="text-xs text-destructive">{saveError}</span>
                                    )}
                                </div>
                            </div>
                        )}

                        {history.length > 1 && (
                            <div className="overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Rate</TableHead>
                                            <TableHead>Basis</TableHead>
                                            <TableHead>Effective</TableHead>
                                            <TableHead>Note</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {history.map((c) => (
                                            <TableRow key={c.id}>
                                                <TableCell className="tabular-nums">
                                                    {(c.commission_bps / 100).toFixed(2)}%
                                                </TableCell>
                                                <TableCell className="text-muted-foreground">
                                                    {c.basis}
                                                </TableCell>
                                                <TableCell className="text-muted-foreground">
                                                    {formatDateIST(c.effective_from)}
                                                    {c.effective_to
                                                        ? ` → ${formatDateIST(c.effective_to)}`
                                                        : " → present"}
                                                </TableCell>
                                                <TableCell className="text-muted-foreground">
                                                    {c.note ?? "-"}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        )}
                    </>
                )}
            </CardContent>
        </Card>
    );
}
