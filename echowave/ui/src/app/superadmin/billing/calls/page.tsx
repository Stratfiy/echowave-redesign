"use client";

import { AlertTriangle, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { listCallsApiV1AdminBillingCallsGet } from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { detailFromError } from "@/lib/apiError";
import {
    formatDateTimeIST,
    formatDuration,
    formatNumber,
    formatPaise,
} from "@/lib/billing/format";

import { PanelMessage, useAuthReady } from "../_components/primitives";

const LANGUAGES = ["en-IN", "hi-IN", "kn-IN", "ta-IN", "mr-IN"];

export default function CallsPage() {
    const [calls, setCalls] = useState<Array<Record<string, never>>>([]);
    const [total, setTotal] = useState(0);
    const [totalPages, setTotalPages] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [search, setSearch] = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");
    const [language, setLanguage] = useState("all");
    const [direction, setDirection] = useState("all");

    useEffect(() => {
        const handle = setTimeout(() => setDebouncedSearch(search), 300);
        return () => clearTimeout(handle);
    }, [search]);

    useEffect(() => {
        setPage(1);
    }, [debouncedSearch, language, direction]);

    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await listCallsApiV1AdminBillingCallsGet({
                query: {
                    page,
                    limit: 50,
                    ...(debouncedSearch ? { search: debouncedSearch } : {}),
                    ...(language !== "all" ? { language } : {}),
                    ...(direction !== "all"
                        ? { direction: direction as "inbound" | "outbound" }
                        : {}),
                },
            });
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load calls"));
            } else {
                setCalls((result.data?.calls ?? []) as Array<Record<string, never>>);
                setTotal(Number(result.data?.total ?? 0));
                setTotalPages(Number(result.data?.total_pages ?? 0));
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, page, debouncedSearch, language, direction]);

    return (
        <Card>
            <CardContent className="pt-5">
                <div className="mb-4 flex flex-wrap items-center gap-2">
                    <div className="relative">
                        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                        <Input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Search calls or numbers"
                            className="w-[240px] pl-8"
                            aria-label="Search calls"
                        />
                    </div>
                    <Select value={language} onValueChange={setLanguage}>
                        <SelectTrigger className="w-[150px]">
                            <SelectValue placeholder="All languages" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All languages</SelectItem>
                            {LANGUAGES.map((l) => (
                                <SelectItem key={l} value={l}>
                                    {l}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Select value={direction} onValueChange={setDirection}>
                        <SelectTrigger className="w-[150px]">
                            <SelectValue placeholder="All directions" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All directions</SelectItem>
                            <SelectItem value="inbound">Inbound</SelectItem>
                            <SelectItem value="outbound">Outbound</SelectItem>
                        </SelectContent>
                    </Select>
                    <span className="ml-auto text-xs text-muted-foreground">
                        {loading ? "" : `${formatNumber(total)} calls`}
                    </span>
                </div>

                {loading ? (
                    <div className="space-y-2">
                        {Array.from({ length: 8 }).map((_, i) => (
                            <Skeleton key={i} className="h-10 w-full" />
                        ))}
                    </div>
                ) : error ? (
                    <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={180}>
                        {error}
                    </PanelMessage>
                ) : calls.length === 0 ? (
                    <PanelMessage height={180}>
                        No calls match these filters in this period.
                    </PanelMessage>
                ) : (
                    <>
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>When</TableHead>
                                        <TableHead>Account</TableHead>
                                        <TableHead>Direction</TableHead>
                                        <TableHead>Language</TableHead>
                                        <TableHead>Disposition</TableHead>
                                        <TableHead className="text-right">Duration</TableHead>
                                        <TableHead className="text-right">Provider cost</TableHead>
                                        <TableHead className="text-right">Charged</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {calls.map((call) => (
                                        <TableRow key={String(call.id)}>
                                            <TableCell className="whitespace-nowrap">
                                                <Link
                                                    href={`/superadmin/billing/calls/${call.id}`}
                                                    className="hover:underline"
                                                >
                                                    {formatDateTimeIST(String(call.created_at ?? ""))}
                                                </Link>
                                            </TableCell>
                                            <TableCell>{String(call.account ?? "—")}</TableCell>
                                            <TableCell className="capitalize text-muted-foreground">
                                                {String(call.direction ?? "—")}
                                            </TableCell>
                                            <TableCell className="text-muted-foreground">
                                                {String(call.language ?? "—")}
                                            </TableCell>
                                            <TableCell>
                                                {call.disposition ? (
                                                    <Badge variant="secondary">
                                                        {String(call.disposition)}
                                                    </Badge>
                                                ) : (
                                                    <span className="text-muted-foreground">—</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatDuration(Number(call.billable_seconds ?? 0))}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums text-muted-foreground">
                                                {formatPaise(Number(call.provider_cost_paise ?? 0))}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums font-medium">
                                                {formatPaise(Number(call.charged_paise ?? 0))}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>

                        {totalPages > 1 && (
                            <div className="mt-4 flex items-center justify-between text-sm">
                                <span className="text-muted-foreground">
                                    Page {page} of {totalPages}
                                </span>
                                <div className="flex gap-2">
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={page <= 1}
                                        onClick={() => setPage((p) => p - 1)}
                                    >
                                        Previous
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        disabled={page >= totalPages}
                                        onClick={() => setPage((p) => p + 1)}
                                    >
                                        Next
                                    </Button>
                                </div>
                            </div>
                        )}
                    </>
                )}
            </CardContent>
        </Card>
    );
}
