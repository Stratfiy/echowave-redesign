"use client";

import { AlertTriangle, ArrowUpDown, Clock, Search, TrendingDown, Users, Wallet } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { listAccountsApiV1AdminBillingAccountsGet } from "@/client/sdk.gen";
import { PanelMessage, useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { detailFromError } from "@/lib/apiError";
import {
    formatDateIST,
    formatMicrosUsd,
    formatNumber,
    formatPaise,
    formatPercent,
    formatRateMpaise,
} from "@/lib/billing/format";
import { cn } from "@/lib/utils";

/** Flag thresholds. Each renders with an icon and a label, never colour alone. */
const LOW_BALANCE_PAISE = 100_000; // ₹1,000
const LOW_MARGIN_RATIO = 0.35;
const STALE_DAYS = 14;

type Account = {
    organization_id: number;
    name: string;
    /** Whoever answers for the account: Owner, else Admin, else earliest member. */
    owner_email: string | null;
    owner_role: string | null;
    member_count: number;
    account_type: string | null;
    status: string | null;
    billable_minutes: number;
    revenue_paise: number;
    provider_cost_paise: number;
    margin_paise: number;
    margin_pct: number | null;
    balance_paise: number;
    platform_rate_mpaise: number;
    platform_rate_micros_usd: number | null;
    pulse_seconds: number;
    platform_rate_source: string;
    platform_rate_is_override: boolean;
    last_active_day: string | null;
};

type SortKey =
    | "name"
    | "owner_email"
    | "billable_minutes"
    | "revenue_paise"
    | "provider_cost_paise"
    | "margin_pct"
    | "balance_paise";

function daysSince(iso: string | null): number | null {
    if (!iso) return null;
    const then = new Date(`${iso}T00:00:00Z`).getTime();
    return Math.floor((Date.now() - then) / 86_400_000);
}

/**
 * A price quoted in dollars is shown in dollars.
 *
 * Rendering its rupee equivalent as though it were the price would be wrong in
 * a way that matters: the rupee figure moves with the exchange rate, so it is a
 * conversion of the price rather than the price itself. The converted amount
 * goes on the tooltip.
 */
function formatPlatformRate(account: Account): string {
    return account.platform_rate_micros_usd != null
        ? formatMicrosUsd(account.platform_rate_micros_usd)
        : formatRateMpaise(account.platform_rate_mpaise);
}

const RATE_SOURCE_LABELS: Record<string, string> = {
    account_override: "Account override",
    volume_tier: "Volume tier",
    global_default: "Global default",
};

function AccountFlags({ account }: { account: Account }) {
    const stale = daysSince(account.last_active_day);
    const flags: Array<{ icon: React.ReactNode; label: string; tone: string }> = [];

    if (account.balance_paise < LOW_BALANCE_PAISE) {
        flags.push({
            icon: <Wallet className="h-3 w-3" />,
            label: account.balance_paise < 0 ? "Negative balance" : "Low credit",
            tone: account.balance_paise < 0 ? "text-destructive" : "text-amber-600 dark:text-amber-400",
        });
    }
    if (account.margin_pct !== null && account.margin_pct < LOW_MARGIN_RATIO) {
        flags.push({
            icon: <TrendingDown className="h-3 w-3" />,
            label: "Low margin",
            tone: "text-amber-600 dark:text-amber-400",
        });
    }
    if (stale === null || stale >= STALE_DAYS) {
        flags.push({
            icon: <Clock className="h-3 w-3" />,
            label: stale === null ? "No activity" : `Idle ${stale}d`,
            tone: "text-muted-foreground",
        });
    }

    if (flags.length === 0) return null;

    return (
        <span className="flex flex-wrap items-center gap-2">
            {flags.map((flag) => (
                <span
                    key={flag.label}
                    className={cn("inline-flex items-center gap-1 text-xs", flag.tone)}
                >
                    {flag.icon}
                    {flag.label}
                </span>
            ))}
        </span>
    );
}

export default function AccountsPage() {
    const [accounts, setAccounts] = useState<Account[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [typeFilter, setTypeFilter] = useState<string>("all");
    const [statusFilter, setStatusFilter] = useState<string>("all");
    // `search` is what the box shows; `query` is what has been sent. Debounced
    // apart so typing an address does not fire a request per keystroke.
    const [search, setSearch] = useState("");
    const [query, setQuery] = useState("");
    const [sortKey, setSortKey] = useState<SortKey>("revenue_paise");
    const [sortAsc, setSortAsc] = useState(false);

    const authReady = useAuthReady();

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search), 250);
        return () => clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await listAccountsApiV1AdminBillingAccountsGet({
                query: {
                    ...(typeFilter !== "all" ? { account_type: typeFilter } : {}),
                    ...(statusFilter !== "all" ? { status: statusFilter } : {}),
                    // Filtered server-side: support pastes an address out of a
                    // ticket, and that has to find the account once there are
                    // more of them than fit on one screen.
                    ...(query.trim() ? { q: query.trim() } : {}),
                },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load accounts"));
            } else {
                setAccounts(((result.data?.accounts ?? []) as Account[]) ?? []);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, typeFilter, statusFilter, query]);

    const sorted = useMemo(() => {
        const rows = [...accounts];
        rows.sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (av === null || av === undefined) return 1;
            if (bv === null || bv === undefined) return -1;
            if (typeof av === "string" && typeof bv === "string") {
                return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
            }
            return sortAsc ? Number(av) - Number(bv) : Number(bv) - Number(av);
        });
        return rows;
    }, [accounts, sortKey, sortAsc]);

    const toggleSort = (key: SortKey) => {
        if (key === sortKey) setSortAsc((v) => !v);
        else {
            setSortKey(key);
            setSortAsc(false);
        }
    };

    const SortableHead = ({
        label,
        sortBy,
        numeric,
    }: {
        label: string;
        sortBy: SortKey;
        numeric?: boolean;
    }) => (
        <TableHead className={numeric ? "text-right" : undefined}>
            <button
                type="button"
                onClick={() => toggleSort(sortBy)}
                className={cn(
                    "inline-flex items-center gap-1 hover:text-foreground",
                    sortKey === sortBy ? "text-foreground" : "text-muted-foreground",
                )}
                aria-label={`Sort by ${label}`}
            >
                {label}
                <ArrowUpDown className="h-3 w-3" />
            </button>
        </TableHead>
    );

    return (
        <Card>
            <CardContent className="pt-5">
                <div className="mb-4 flex flex-wrap items-center gap-2">
                    <div className="relative w-full sm:w-[260px]">
                        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        <Input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Search email or account name"
                            aria-label="Search accounts by email or name"
                            className="pl-8"
                        />
                    </div>
                    <Select value={typeFilter} onValueChange={setTypeFilter}>
                        <SelectTrigger className="w-[160px]">
                            <SelectValue placeholder="All types" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All types</SelectItem>
                            <SelectItem value="clinic">Clinic</SelectItem>
                            <SelectItem value="agency">Agency</SelectItem>
                            <SelectItem value="enterprise">Enterprise</SelectItem>
                        </SelectContent>
                    </Select>
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-[160px]">
                            <SelectValue placeholder="All statuses" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All statuses</SelectItem>
                            <SelectItem value="active">Active</SelectItem>
                            <SelectItem value="suspended">Suspended</SelectItem>
                        </SelectContent>
                    </Select>
                    <span className="ml-auto text-xs text-muted-foreground">
                        {loading ? "" : `${sorted.length} account${sorted.length === 1 ? "" : "s"}`}
                    </span>
                </div>

                {loading ? (
                    <div className="space-y-2">
                        {Array.from({ length: 6 }).map((_, i) => (
                            <Skeleton key={i} className="h-11 w-full" />
                        ))}
                    </div>
                ) : error ? (
                    <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={180}>
                        {error}
                    </PanelMessage>
                ) : sorted.length === 0 ? (
                    <PanelMessage height={180}>
                        No accounts match these filters yet.
                    </PanelMessage>
                ) : (
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <SortableHead label="Account" sortBy="name" />
                                    <SortableHead label="Contact" sortBy="owner_email" />
                                    <TableHead>Type</TableHead>
                                    <SortableHead label="Minutes" sortBy="billable_minutes" numeric />
                                    <SortableHead label="Revenue" sortBy="revenue_paise" numeric />
                                    <SortableHead
                                        label="Provider cost"
                                        sortBy="provider_cost_paise"
                                        numeric
                                    />
                                    <SortableHead label="Margin" sortBy="margin_pct" numeric />
                                    <SortableHead label="Credit" sortBy="balance_paise" numeric />
                                    <TableHead className="text-right">Rate/min</TableHead>
                                    <TableHead>Last active</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {sorted.map((account) => (
                                    <TableRow key={account.organization_id}>
                                        <TableCell>
                                            <Link
                                                href={`/superadmin/billing/accounts/${account.organization_id}`}
                                                className="font-medium hover:underline"
                                            >
                                                {account.name}
                                            </Link>
                                            <div className="mt-0.5">
                                                <AccountFlags account={account} />
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            {account.owner_email ? (
                                                <>
                                                    {/* A mailto rather than plain text: the
                                                        reason staff want the address on this
                                                        screen is to write to the customer. */}
                                                    <a
                                                        href={`mailto:${account.owner_email}`}
                                                        className="hover:underline"
                                                    >
                                                        {account.owner_email}
                                                    </a>
                                                    {account.member_count > 1 && (
                                                        <Tooltip>
                                                            <TooltipTrigger asChild>
                                                                <span className="ml-2 inline-flex items-center gap-1 align-middle text-xs text-muted-foreground">
                                                                    <Users className="h-3 w-3" />
                                                                    {account.member_count}
                                                                </span>
                                                            </TooltipTrigger>
                                                            <TooltipContent>
                                                                <p>
                                                                    {account.member_count} people on this
                                                                    account
                                                                </p>
                                                            </TooltipContent>
                                                        </Tooltip>
                                                    )}
                                                </>
                                            ) : (
                                                // An organization with no membership row at all.
                                                // Rare and worth seeing rather than blanking:
                                                // it means nobody can sign in to this account.
                                                <span className="text-muted-foreground">
                                                    No members
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            {account.account_type ? (
                                                <Badge variant="secondary" className="capitalize">
                                                    {account.account_type}
                                                </Badge>
                                            ) : (
                                                <span className="text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatNumber(account.billable_minutes)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(account.revenue_paise)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums text-muted-foreground">
                                            {formatPaise(account.provider_cost_paise)}
                                        </TableCell>
                                        <TableCell
                                            className={cn(
                                                "text-right tabular-nums",
                                                account.margin_pct !== null &&
                                                    account.margin_pct < LOW_MARGIN_RATIO &&
                                                    "font-medium text-amber-600 dark:text-amber-400",
                                            )}
                                        >
                                            {formatPercent(account.margin_pct)}
                                        </TableCell>
                                        <TableCell
                                            className={cn(
                                                "text-right tabular-nums",
                                                account.balance_paise < 0 && "font-medium text-destructive",
                                            )}
                                        >
                                            {formatPaise(account.balance_paise)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <span
                                                        className={cn(
                                                            account.platform_rate_is_override &&
                                                                "underline decoration-dotted underline-offset-2",
                                                        )}
                                                    >
                                                        {formatPlatformRate(account)}
                                                    </span>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    {RATE_SOURCE_LABELS[
                                                        account.platform_rate_source
                                                    ] ?? account.platform_rate_source}
                                                    {account.platform_rate_micros_usd != null &&
                                                        ` · ${formatRateMpaise(account.platform_rate_mpaise)} at today's rate`}
                                                    {` · ${account.pulse_seconds}s pulse`}
                                                </TooltipContent>
                                            </Tooltip>
                                        </TableCell>
                                        <TableCell className="text-muted-foreground">
                                            {account.last_active_day
                                                ? formatDateIST(account.last_active_day)
                                                : "Never"}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
