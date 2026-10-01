"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readAuditLogApiV1AdminAuditGet } from "@/client/sdk.gen";
import { PanelMessage, useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { detailFromResult } from "@/lib/apiError";
import { formatDateTimeIST } from "@/lib/billing/format";

export type AuditEntry = {
    key: string;
    source: "admin" | "billing";
    id: number;
    created_at: string | null;
    actor_user_id: number | null;
    actor_email: string | null;
    action: string;
    organization_id: number | null;
    organization_name: string | null;
    target_user_id: number | null;
    target_user_email: string | null;
    note: string | null;
    old_value: unknown;
    new_value: unknown;
};

type AuditPage = {
    entries: AuditEntry[];
    next_before: string | null;
    actions: string[];
};

export type AuditFilters = {
    organizationId?: number;
    actorUserId?: number;
    action?: string;
};

const ALL = "all";

/** "impersonation_started" -> "Impersonation started". */
export function actionLabel(action: string): string {
    const words = action.replaceAll("_", " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
}

function changeSummary(entry: AuditEntry): string | null {
    if (entry.old_value == null && entry.new_value == null) return null;
    const show = (value: unknown) =>
        value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
    return `${show(entry.old_value)} → ${show(entry.new_value)}`;
}

/**
 * Staff actions and billing changes as one stream, newest first (ADMIN-2,
 * A6). With `organizationId` it is the account page's section; without, the
 * console page with every filter. Pages by cursor, so rows written while
 * somebody reads are never repeated or skipped.
 */
export function AuditLog({
    organizationId,
    showFilters = false,
    pageSize = 50,
}: {
    organizationId?: number;
    showFilters?: boolean;
    pageSize?: number;
}) {
    const authReady = useAuthReady();
    const [entries, setEntries] = useState<AuditEntry[]>([]);
    const [actions, setActions] = useState<string[]>([]);
    const [next, setNext] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [action, setAction] = useState<string>(ALL);
    const [orgInput, setOrgInput] = useState("");
    const [actorInput, setActorInput] = useState("");
    // The id boxes are debounced so typing "1234" is one request, not four.
    const [typed, setTyped] = useState({ org: "", actor: "" });
    useEffect(() => {
        const timer = setTimeout(() => setTyped({ org: orgInput, actor: actorInput }), 300);
        return () => clearTimeout(timer);
    }, [orgInput, actorInput]);

    const asId = (raw: string) =>
        raw.trim() && Number.isFinite(Number(raw)) ? Number(raw) : undefined;
    const filters: AuditFilters = {
        organizationId: organizationId ?? asId(typed.org),
        actorUserId: asId(typed.actor),
        action: action === ALL ? undefined : action,
    };
    const filterKey = JSON.stringify(filters);

    const fetchPage = useCallback(
        async (before: string | null) => {
            const parsed = JSON.parse(filterKey) as AuditFilters;
            return readAuditLogApiV1AdminAuditGet({
                query: {
                    limit: pageSize,
                    ...(parsed.organizationId !== undefined ? { organization_id: parsed.organizationId } : {}),
                    ...(parsed.actorUserId !== undefined ? { actor_user_id: parsed.actorUserId } : {}),
                    ...(parsed.action ? { action: parsed.action } : {}),
                    ...(before ? { before } : {}),
                },
            });
        },
        [filterKey, pageSize],
    );

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await fetchPage(null);
            if (cancelled) return;
            if (result.error) {
                setError(detailFromResult(result, "Could not load the audit log"));
            } else {
                const page = result.data as unknown as AuditPage;
                setEntries(page.entries ?? []);
                setActions(page.actions ?? []);
                setNext(page.next_before ?? null);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, fetchPage]);

    const loadMore = async () => {
        if (!next) return;
        setLoadingMore(true);
        const result = await fetchPage(next);
        setLoadingMore(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not load more"));
            return;
        }
        const page = result.data as unknown as AuditPage;
        setEntries((prev) => [...prev, ...(page.entries ?? [])]);
        setNext(page.next_before ?? null);
    };

    return (
        <div className="space-y-3">
            {showFilters && (
                <div className="flex flex-wrap items-end gap-2">
                    <div className="space-y-1">
                        <Label className="text-xs text-muted-foreground">Action</Label>
                        <Select value={action} onValueChange={setAction}>
                            <SelectTrigger className="w-[220px]" aria-label="Filter by action">
                                <SelectValue placeholder="Every action" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value={ALL}>Every action</SelectItem>
                                {actions.map((name) => (
                                    <SelectItem key={name} value={name}>
                                        {actionLabel(name)}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    {organizationId === undefined && (
                        <div className="space-y-1">
                            <Label htmlFor="audit-org" className="text-xs text-muted-foreground">
                                Account id
                            </Label>
                            <Input
                                id="audit-org"
                                inputMode="numeric"
                                value={orgInput}
                                onChange={(e) => setOrgInput(e.target.value)}
                                placeholder="Any"
                                className="w-[120px]"
                            />
                        </div>
                    )}
                    <div className="space-y-1">
                        <Label htmlFor="audit-actor" className="text-xs text-muted-foreground">
                            Staff user id
                        </Label>
                        <Input
                            id="audit-actor"
                            inputMode="numeric"
                            value={actorInput}
                            onChange={(e) => setActorInput(e.target.value)}
                            placeholder="Anyone"
                            className="w-[120px]"
                        />
                    </div>
                </div>
            )}

            {loading ? (
                <div className="space-y-2" aria-label="Loading the audit log">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <Skeleton key={i} className="h-9 w-full" />
                    ))}
                </div>
            ) : error && entries.length === 0 ? (
                <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={140}>
                    {error}
                </PanelMessage>
            ) : entries.length === 0 ? (
                <PanelMessage height={140}>Nothing recorded for these filters yet.</PanelMessage>
            ) : (
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>When</TableHead>
                                <TableHead>Who</TableHead>
                                <TableHead>Action</TableHead>
                                {organizationId === undefined && <TableHead>Account</TableHead>}
                                <TableHead>Details</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {entries.map((entry) => (
                                <TableRow key={entry.key}>
                                    <TableCell className="whitespace-nowrap text-muted-foreground tabular-nums">
                                        {formatDateTimeIST(entry.created_at)}
                                    </TableCell>
                                    <TableCell className="whitespace-nowrap">
                                        {entry.actor_email ?? (entry.actor_user_id ? `User ${entry.actor_user_id}` : "System")}
                                    </TableCell>
                                    <TableCell>
                                        <span className="inline-flex items-center gap-1.5">
                                            {actionLabel(entry.action)}
                                            <Badge variant="outline" className="text-[10px]">
                                                {entry.source === "billing" ? "Billing" : "Staff"}
                                            </Badge>
                                        </span>
                                    </TableCell>
                                    {organizationId === undefined && (
                                        <TableCell>
                                            {entry.organization_id ? (
                                                <Link
                                                    href={`/superadmin/billing/accounts/${entry.organization_id}`}
                                                    className="hover:underline"
                                                >
                                                    {entry.organization_name ?? `Account ${entry.organization_id}`}
                                                </Link>
                                            ) : (
                                                <span className="text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                    )}
                                    <TableCell className="max-w-[420px] break-words text-sm text-muted-foreground">
                                        {[entry.target_user_email, entry.note, changeSummary(entry)]
                                            .filter(Boolean)
                                            .join(" · ") || "—"}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {error && entries.length > 0 && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            {next && !loading && (
                <Button variant="outline" size="sm" onClick={() => void loadMore()} disabled={loadingMore}>
                    {loadingMore && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                    Load older entries
                </Button>
            )}
        </div>
    );
}
