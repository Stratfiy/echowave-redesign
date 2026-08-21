"use client";

/**
 * The partner queue.
 *
 * Granting a commission is the only thing on the staff side that costs money
 * on an ongoing basis, so it is a queue somebody reads rather than a settings
 * field. Each row carries what the applicant said they were and what volume
 * they expect, because those are the two inputs to the number being set.
 *
 * The basis picker is the part worth being careful about. A share of the
 * platform fee cannot go underwater — the commission is bounded by the margin.
 * A share of total spend can: 20% of total spend on an account running at 15%
 * margin loses money on every minute. The screen says so where the choice is
 * made, rather than leaving it to whoever remembers.
 */

import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    approveApplicationApiV1AdminPartnersApplicationIdApprovePost,
    generateStatementsApiV1AdminPartnersStatementsGeneratePost,
    getQueueApiV1AdminPartnersQueueGet,
    issueStatementApiV1AdminPartnersStatementsStatementIdIssuePost,
    listStatementsApiV1AdminPartnersStatementsGet,
    markStatementPaidApiV1AdminPartnersStatementsStatementIdMarkPaidPost,
    rejectApplicationApiV1AdminPartnersApplicationIdRejectPost,
} from "@/client/sdk.gen";
import { PanelMessage, useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
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
import { detailFromError } from "@/lib/apiError";
import { formatDateIST, formatPaise } from "@/lib/billing/format";

type Application = {
    id: number;
    organization_id: number;
    kind: string;
    expected_minutes_per_month: number | null;
    company_website: string | null;
    note: string | null;
    submitted_at: string | null;
};

/** One period's earnings for one partner, with its per-account breakdown. */
type Statement = {
    id: number;
    partner_organization_id: number;
    period_start: string;
    period_end: string;
    basis: string;
    amount_paise: number;
    status: string;
    lines: { account: string; amount_paise: number }[];
};

const BASIS_HELP: Record<string, string> = {
    platform_fee:
        "A share of what we keep. Bounded by the margin, so it cannot go underwater.",
    total_spend:
        "A share of everything they are charged, provider cost included. Can lose money — check it against the rate card.",
};

function Row({
    application,
    bases,
    onDone,
}: {
    application: Application;
    bases: string[];
    onDone: (id: number) => void;
}) {
    const [percent, setPercent] = useState("10");
    const [basis, setBasis] = useState(bases[0] ?? "platform_fee");
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);

    const approve = async () => {
        const bps = Math.round(Number(percent) * 100);
        if (!Number.isFinite(bps) || bps < 0 || bps > 10_000) {
            toast.error("Commission must be between 0% and 100%.");
            return;
        }
        setBusy(true);
        const response =
            await approveApplicationApiV1AdminPartnersApplicationIdApprovePost({
                path: { application_id: application.id },
                body: { commission_bps: bps, basis, note: note.trim() || null },
            });
        setBusy(false);
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not approve"));
            return;
        }
        toast.success(`Approved at ${percent}%`);
        onDone(application.id);
    };

    const reject = async () => {
        setBusy(true);
        const response =
            await rejectApplicationApiV1AdminPartnersApplicationIdRejectPost({
                path: { application_id: application.id },
                body: { note: note.trim() || null },
            });
        setBusy(false);
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not reject"));
            return;
        }
        toast.success("Rejected");
        onDone(application.id);
    };

    return (
        <Card>
            <CardContent className="space-y-4 pt-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="secondary" className="capitalize">
                                {application.kind}
                            </Badge>
                            <a
                                href={`/superadmin/billing/accounts/${application.organization_id}`}
                                className="text-sm font-medium hover:underline"
                            >
                                Account #{application.organization_id}
                            </a>
                            {application.submitted_at && (
                                <span className="text-xs text-muted-foreground">
                                    {formatDateIST(application.submitted_at)}
                                </span>
                            )}
                        </div>
                        <p className="mt-1 text-sm text-muted-foreground">
                            {application.expected_minutes_per_month != null
                                ? `Expects ${application.expected_minutes_per_month.toLocaleString()} minutes a month`
                                : "No volume given"}
                            {application.company_website
                                ? ` · ${application.company_website}`
                                : ""}
                        </p>
                        {application.note && (
                            <p className="mt-2 whitespace-pre-wrap text-sm">
                                {application.note}
                            </p>
                        )}
                    </div>
                </div>

                <div className="flex flex-wrap items-end gap-2 border-t border-border pt-4">
                    <div className="w-28 space-y-1.5">
                        <Label htmlFor={`pct-${application.id}`}>Commission %</Label>
                        <Input
                            id={`pct-${application.id}`}
                            type="number"
                            step="0.25"
                            min={0}
                            max={100}
                            value={percent}
                            onChange={(e) => setPercent(e.target.value)}
                        />
                    </div>
                    <div className="w-48 space-y-1.5">
                        <Label htmlFor={`basis-${application.id}`}>Of</Label>
                        <Select value={basis} onValueChange={setBasis}>
                            <SelectTrigger id={`basis-${application.id}`}>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {bases.map((b) => (
                                    <SelectItem key={b} value={b}>
                                        {b === "platform_fee"
                                            ? "Platform fee"
                                            : "Total spend"}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="min-w-[200px] flex-1 space-y-1.5">
                        <Label htmlFor={`note-${application.id}`}>
                            Note (shown to them)
                        </Label>
                        <Input
                            id={`note-${application.id}`}
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Quoted against 5k minutes a month."
                        />
                    </div>
                    <Button onClick={() => void approve()} disabled={busy}>
                        {busy ? (
                            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                        ) : (
                            <Check className="mr-1.5 h-4 w-4" />
                        )}
                        Approve
                    </Button>
                    <Button
                        variant="outline"
                        onClick={() => void reject()}
                        disabled={busy}
                    >
                        <X className="mr-1.5 h-4 w-4" />
                        Reject
                    </Button>
                </div>

                {/* Beside the control rather than in a doc nobody opens. */}
                <p className="text-xs text-muted-foreground">{BASIS_HELP[basis]}</p>
            </CardContent>
        </Card>
    );
}

/**
 * What we owe, and the two buttons that move it.
 *
 * Generation is a staff action rather than a schedule on purpose. A month is
 * only ready to bill once its rollups have settled, and a cron firing at
 * midnight on the 1st bills a month whose last day may still be being costed.
 * Regenerating a draft is free and is the ordinary way to pick up a late
 * arrival — which is why the button says "Generate" and not "Close the month".
 */
function Payouts() {
    const [statements, setStatements] = useState<Statement[]>([]);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    // Defaults to last calendar month, which is the period somebody opening
    // this screen almost always wants.
    const [start, setStart] = useState(() => {
        const now = new Date();
        return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1))
            .toISOString()
            .slice(0, 10);
    });
    const [end, setEnd] = useState(() => {
        const now = new Date();
        return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0))
            .toISOString()
            .slice(0, 10);
    });

    const load = async () => {
        const response = await listStatementsApiV1AdminPartnersStatementsGet();
        if (!response.error) {
            setStatements(
                (response.data as unknown as { statements?: Statement[] })
                    ?.statements ?? [],
            );
        }
        setLoading(false);
    };

    useEffect(() => {
        void load();

    }, []);

    const generate = async () => {
        setBusy(true);
        const response =
            await generateStatementsApiV1AdminPartnersStatementsGeneratePost({
                body: { period_start: start, period_end: end },
            });
        setBusy(false);
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not generate"));
            return;
        }
        const body = response.data as unknown as {
            generated: unknown[];
            refused: { reason: string }[];
        };
        // Refusals are named rather than swallowed: the usual one is a period
        // already issued, and somebody who thinks they just regenerated it
        // would otherwise pay the old number.
        if (body.refused?.length) {
            toast.warning(
                `${body.generated.length} generated, ${body.refused.length} refused — ${body.refused[0].reason}`,
            );
        } else {
            toast.success(`${body.generated.length} statements generated`);
        }
        await load();
    };

    const issue = async (statement: Statement) => {
        const response = await issueStatementApiV1AdminPartnersStatementsStatementIdIssuePost({
            path: { statement_id: statement.id },
        });
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not issue it"));
            return;
        }
        toast.success("Marked sent — the number is frozen against regeneration");
        await load();
    };

    const markPaid = async (statement: Statement) => {
        const reference = window.prompt(
            "Payment reference (UTR or bank reference). The transfer itself happens outside this system.",
        );
        if (reference === null) return;
        const response =
            await markStatementPaidApiV1AdminPartnersStatementsStatementIdMarkPaidPost({
                path: { statement_id: statement.id },
                body: { payment_reference: reference || null, note: null },
            });
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not mark it paid"));
            return;
        }
        toast.success("Marked paid");
        await load();
    };

    const owed = statements.reduce((sum, s) => sum + s.amount_paise, 0);

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-base">Partner payouts</CardTitle>
                <CardDescription>
                    Outstanding statements, oldest period first. {formatPaise(owed)} owed
                    across {statements.length} statement
                    {statements.length === 1 ? "" : "s"}.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex flex-wrap items-end gap-2">
                    <div className="space-y-1">
                        <Label htmlFor="period-start" className="text-xs">
                            Period start
                        </Label>
                        <Input
                            id="period-start"
                            type="date"
                            value={start}
                            onChange={(e) => setStart(e.target.value)}
                            className="w-40"
                        />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="period-end" className="text-xs">
                            Period end
                        </Label>
                        <Input
                            id="period-end"
                            type="date"
                            value={end}
                            onChange={(e) => setEnd(e.target.value)}
                            className="w-40"
                        />
                    </div>
                    <Button onClick={() => void generate()} disabled={busy}>
                        {busy ? (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : null}
                        Generate
                    </Button>
                </div>

                {loading ? (
                    <Skeleton className="h-24 w-full" />
                ) : statements.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                        Nothing outstanding. Generate a period to accrue it.
                    </p>
                ) : (
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Partner</TableHead>
                                <TableHead>Period</TableHead>
                                <TableHead>Accounts</TableHead>
                                <TableHead className="text-right">Owed</TableHead>
                                <TableHead className="text-right">Status</TableHead>
                                <TableHead />
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {statements.map((statement) => (
                                <TableRow key={statement.id}>
                                    <TableCell className="font-medium">
                                        #{statement.partner_organization_id}
                                    </TableCell>
                                    <TableCell className="text-sm text-muted-foreground">
                                        {statement.period_start} → {statement.period_end}
                                    </TableCell>
                                    <TableCell className="text-sm text-muted-foreground">
                                        {statement.lines.length}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(statement.amount_paise)}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {statement.status === "paid" ? (
                                            <Badge className="bg-green-600 hover:bg-green-600">Paid</Badge>
                                        ) : statement.status === "issued" ? (
                                            <Badge variant="secondary">Issued</Badge>
                                        ) : (
                                            <Badge variant="outline">Draft</Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex justify-end gap-1.5">
                                            {statement.status === "draft" && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => void issue(statement)}
                                                >
                                                    Issue
                                                </Button>
                                            )}
                                            {statement.status !== "paid" && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => void markPaid(statement)}
                                                >
                                                    Mark paid
                                                </Button>
                                            )}
                                        </div>
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


export default function PartnerQueuePage() {
    const authReady = useAuthReady();
    const [applications, setApplications] = useState<Application[]>([]);
    const [bases, setBases] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        void (async () => {
            const response = await getQueueApiV1AdminPartnersQueueGet({});
            if (cancelled) return;
            if (response.error) {
                setError(detailFromError(response.error, "Could not load the queue"));
            } else {
                const body = response.data as {
                    applications: Application[];
                    bases: string[];
                };
                setApplications(body.applications ?? []);
                setBases(body.bases ?? []);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    if (loading) {
        return (
            <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-40 w-full" />
                ))}
            </div>
        );
    }

    if (error) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={180}>
                {error}
            </PanelMessage>
        );
    }

    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <div>
                    <h1 className="text-lg font-semibold">Partner applications</h1>
                    <p className="text-sm text-muted-foreground">
                        Oldest first. Approving sets the commission, the account tier
                        and the referral code together.
                    </p>
                </div>
                {/* Inline rather than an early return: an empty queue must not
                    hide the payouts below it, which is the half of this screen
                    with money on it. */}
                {applications.length === 0 ? (
                    <PanelMessage height={120}>Nothing waiting.</PanelMessage>
                ) : (
                    applications.map((application) => (
                        <Row
                            key={application.id}
                            application={application}
                            bases={bases}
                            onDone={(id) =>
                                setApplications((prev) =>
                                    prev.filter((a) => a.id !== id),
                                )
                            }
                        />
                    ))
                )}
            </div>

            <Payouts />
        </div>
    );
}
