"use client";

/**
 * What an agency sees of the accounts it manages.
 *
 * Deliberately a roll-up and nothing else. There is no way from here into a
 * client's agents, recordings or transcripts, and that is a decision rather
 * than a gap: those are conversations with the client's own customers, and an
 * agency relationship is not consent to listen to them.
 *
 * Every month row shows its own arithmetic — what the client was charged, the
 * rate, the amount — because an agency cannot see our costs, and a commission
 * figure they cannot check is one they will dispute.
 *
 * An ordinary account that lands here sees an explanation rather than an empty
 * table, since almost nobody is an agency and a blank screen reads as broken.
 */

import { Building2, Check, Wallet } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { getAgencyStatementApiV1AgencyGet } from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatPaise } from "@/lib/billing/format";

type Statement = {
    clients: Array<{
        organization_id: number;
        reference: string;
        commission_bps: number;
    }>;
    earned_paise: number;
    paid_paise: number;
    owed_paise: number;
    months: Array<{
        month: string;
        client_organization_id: number;
        charged_paise: number;
        commission_bps: number;
        amount_paise: number;
        paid: boolean;
    }>;
};

export default function AgencyPage() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [data, setData] = useState<Statement | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const result = await getAgencyStatementApiV1AgencyGet();
        if (result.error) {
            setError(detailFromError(result.error, "Could not load your clients"));
        } else {
            setData(result.data as unknown as Statement);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    if (loading) {
        return (
            <div className="mx-auto w-full max-w-[1000px] px-6 pt-8">
                <Skeleton className="h-64 w-full rounded-2xl" />
            </div>
        );
    }

    const isAgency = (data?.clients.length ?? 0) > 0 || (data?.months.length ?? 0) > 0;

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[1000px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Your clients
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        The accounts you manage, and what you have earned on them.
                    </p>
                </header>

                {error && (
                    <section className="glass-panel mb-5 px-5 py-4 text-sm text-destructive">
                        {error}
                    </section>
                )}

                {!isAgency ? (
                    // An explanation rather than an empty table. Almost nobody is
                    // an agency, and a blank screen reads as a fault.
                    <Card>
                        <CardContent className="py-10 text-center">
                            <Building2 className="mx-auto h-8 w-8 text-muted-foreground" />
                            <p className="mt-3 text-sm font-medium text-foreground">
                                This account does not manage any others
                            </p>
                            <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">
                                Agencies bring their own clients onto Decibyl and earn a
                                commission on what those clients spend. Talk to us if
                                that is you.
                            </p>
                        </CardContent>
                    </Card>
                ) : (
                    <>
                        <div className="mb-5 grid gap-3 sm:grid-cols-3">
                            <Card>
                                <CardContent className="pt-5">
                                    <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                        Owed to you
                                    </p>
                                    <p className="mt-1 text-2xl font-semibold tabular-nums tracking-[-0.03em]">
                                        {formatPaise(data?.owed_paise ?? 0)}
                                    </p>
                                </CardContent>
                            </Card>
                            <Card>
                                <CardContent className="pt-5">
                                    <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                        Earned all time
                                    </p>
                                    <p className="mt-1 text-2xl font-semibold tabular-nums tracking-[-0.03em]">
                                        {formatPaise(data?.earned_paise ?? 0)}
                                    </p>
                                </CardContent>
                            </Card>
                            <Card>
                                <CardContent className="pt-5">
                                    <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                        Clients
                                    </p>
                                    <p className="mt-1 text-2xl font-semibold tabular-nums tracking-[-0.03em]">
                                        {data?.clients.length ?? 0}
                                    </p>
                                </CardContent>
                            </Card>
                        </div>

                        <Card className="mb-5">
                            <CardHeader className="pb-3">
                                <CardTitle className="text-[0.9375rem]">
                                    Accounts you manage
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Account</TableHead>
                                                <TableHead className="text-right">
                                                    Your rate
                                                </TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {data?.clients.map((client) => (
                                                <TableRow key={client.organization_id}>
                                                    <TableCell className="whitespace-nowrap font-medium">
                                                        {client.reference}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {(client.commission_bps / 100).toFixed(
                                                            2,
                                                        )}
                                                        %
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader className="pb-3">
                                <CardTitle className="text-[0.9375rem]">
                                    Commission by month
                                </CardTitle>
                                <p className="mt-0.5 text-xs text-muted-foreground">
                                    Each row shows what the account was charged and the
                                    rate applied, so the amount can be checked rather
                                    than taken on trust.
                                </p>
                            </CardHeader>
                            <CardContent>
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Month</TableHead>
                                                <TableHead>Account</TableHead>
                                                <TableHead className="text-right">
                                                    They spent
                                                </TableHead>
                                                <TableHead className="text-right">
                                                    Rate
                                                </TableHead>
                                                <TableHead className="text-right">
                                                    You earned
                                                </TableHead>
                                                <TableHead />
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {data?.months.map((row) => (
                                                <TableRow
                                                    key={`${row.month}-${row.client_organization_id}`}
                                                >
                                                    <TableCell className="whitespace-nowrap font-medium tabular-nums">
                                                        {row.month.slice(0, 7)}
                                                    </TableCell>
                                                    <TableCell className="tabular-nums text-muted-foreground">
                                                        #{row.client_organization_id}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                                        {formatPaise(row.charged_paise)}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                                        {(row.commission_bps / 100).toFixed(2)}%
                                                    </TableCell>
                                                    <TableCell className="text-right font-medium tabular-nums">
                                                        {formatPaise(row.amount_paise)}
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        {row.paid ? (
                                                            <Badge variant="outline">
                                                                <Check className="mr-1 h-3 w-3" />
                                                                Paid
                                                            </Badge>
                                                        ) : (
                                                            <Badge variant="secondary">
                                                                <Wallet className="mr-1 h-3 w-3" />
                                                                Due
                                                            </Badge>
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            </CardContent>
                        </Card>
                    </>
                )}
            </div>
        </div>
    );
}
