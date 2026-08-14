"use client";

/**
 * Staff putting an account under an agency, and settling what is owed.
 *
 * The rate is set here rather than by the agency, for the obvious reason: it is
 * what we pay, and a rate the earner can edit is not a rate.
 *
 * "Mark paid" records a fact, it does not move money — the copy says so,
 * because a button that appears to pay somebody and does not is worse than no
 * button. Payouts go through whatever the finance process is; this closes the
 * month against it.
 */

import { AlertTriangle, Check, Link2, Loader2 } from "lucide-react";
import { useState } from "react";

import {
    accrueApiV1AdminAgencyAccruePost,
    markPaidApiV1AdminAgencyPaidPost,
    setManagerApiV1AdminAgencyManagerPost,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export default function AdminAgencyPage() {
    const { user, loading: authLoading } = useAuth();

    const [clientId, setClientId] = useState("");
    const [agencyId, setAgencyId] = useState("");
    const [percent, setPercent] = useState("10");
    const [month, setMonth] = useState("");
    const [accrualId, setAccrualId] = useState("");
    const [note, setNote] = useState("");

    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState<string | null>(null);

    const run = async (
        key: string,
        call: () => Promise<{ error?: unknown }>,
        success: string,
        fallback: string,
    ) => {
        setBusy(key);
        setError(null);
        setDone(null);
        const result = await call();
        if (result.error) {
            setError(detailFromError(result.error, fallback));
        } else {
            setDone(success);
        }
        setBusy(null);
    };

    if (authLoading || !user) return null;

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[900px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Agencies
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        Put an account under an agency and set what that agency earns
                        on it. An agency sees a roll-up of its clients — never their
                        agents, recordings or transcripts.
                    </p>
                </header>

                <Card className="mb-5">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-[0.9375rem]">
                            Assign an account to an agency
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="w-32 space-y-1.5">
                                <Label htmlFor="client-id" className="text-xs">
                                    Client org id
                                </Label>
                                <Input
                                    id="client-id"
                                    inputMode="numeric"
                                    value={clientId}
                                    onChange={(e) => setClientId(e.target.value)}
                                />
                            </div>
                            <div className="w-32 space-y-1.5">
                                <Label htmlFor="agency-id" className="text-xs">
                                    Agency org id
                                </Label>
                                <Input
                                    id="agency-id"
                                    inputMode="numeric"
                                    value={agencyId}
                                    onChange={(e) => setAgencyId(e.target.value)}
                                    placeholder="blank = detach"
                                />
                            </div>
                            <div className="w-28 space-y-1.5">
                                <Label htmlFor="agency-rate" className="text-xs">
                                    Commission %
                                </Label>
                                <Input
                                    id="agency-rate"
                                    inputMode="decimal"
                                    value={percent}
                                    onChange={(e) => setPercent(e.target.value)}
                                />
                            </div>
                            <Button
                                disabled={busy !== null || !clientId.trim()}
                                onClick={() =>
                                    void run(
                                        "assign",
                                        () =>
                                            setManagerApiV1AdminAgencyManagerPost({
                                                body: {
                                                    client_organization_id: Number(clientId),
                                                    agency_organization_id: agencyId.trim()
                                                        ? Number(agencyId)
                                                        : null,
                                                    commission_bps: agencyId.trim()
                                                        ? Math.round(Number(percent) * 100)
                                                        : null,
                                                },
                                            }),
                                        agencyId.trim()
                                            ? "Assigned."
                                            : "Detached. Past commission is untouched.",
                                        "Could not assign that account",
                                    )
                                }
                            >
                                {busy === "assign" ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <Link2 className="mr-2 h-4 w-4" />
                                )}
                                Assign
                            </Button>
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">
                            Leave the agency blank to detach. Commission already earned
                            stays — the agency did the work.
                        </p>
                    </CardContent>
                </Card>

                <Card className="mb-5">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-[0.9375rem]">
                            Snapshot a month
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="w-44 space-y-1.5">
                                <Label htmlFor="accrue-month" className="text-xs">
                                    Any day in the month
                                </Label>
                                <Input
                                    id="accrue-month"
                                    type="date"
                                    value={month}
                                    onChange={(e) => setMonth(e.target.value)}
                                />
                            </div>
                            <Button
                                variant="outline"
                                disabled={busy !== null || !month}
                                onClick={() =>
                                    void run(
                                        "accrue",
                                        () =>
                                            accrueApiV1AdminAgencyAccruePost({
                                                body: { month },
                                            }),
                                        "Month snapshotted.",
                                        "Could not accrue that month",
                                    )
                                }
                            >
                                {busy === "accrue" && (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                )}
                                Accrue
                            </Button>
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">
                            Runs automatically on the 2nd of each month. Safe to repeat
                            — an unpaid month is replaced, never added to.
                        </p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-[0.9375rem]">
                            Mark a month settled
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="w-32 space-y-1.5">
                                <Label htmlFor="accrual-id" className="text-xs">
                                    Accrual id
                                </Label>
                                <Input
                                    id="accrual-id"
                                    inputMode="numeric"
                                    value={accrualId}
                                    onChange={(e) => setAccrualId(e.target.value)}
                                />
                            </div>
                            <div className="min-w-[200px] flex-1 space-y-1.5">
                                <Label htmlFor="paid-note" className="text-xs">
                                    Reference
                                </Label>
                                <Input
                                    id="paid-note"
                                    value={note}
                                    onChange={(e) => setNote(e.target.value)}
                                    placeholder="NEFT ref, invoice number"
                                />
                            </div>
                            <Button
                                variant="outline"
                                disabled={busy !== null || !accrualId.trim()}
                                onClick={() =>
                                    void run(
                                        "paid",
                                        () =>
                                            markPaidApiV1AdminAgencyPaidPost({
                                                body: {
                                                    accrual_id: Number(accrualId),
                                                    note: note.trim() || null,
                                                },
                                            }),
                                        "Recorded as settled.",
                                        "Could not mark that month paid",
                                    )
                                }
                            >
                                {busy === "paid" && (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                )}
                                Mark paid
                            </Button>
                        </div>
                        {/* Said plainly, because a button that appears to pay
                            somebody and does not is worse than no button. */}
                        <p className="mt-2 text-xs text-muted-foreground">
                            Records the fact. It does not move money — pay the agency
                            however you normally do, then close the month here.
                        </p>
                    </CardContent>
                </Card>

                {error && (
                    <p className="mt-4 flex items-start gap-1.5 text-sm text-destructive">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                        {error}
                    </p>
                )}
                {done && (
                    <p className="mt-4 flex items-center gap-1.5 text-sm text-foreground">
                        <Check className="h-4 w-4" />
                        {done}
                    </p>
                )}
            </div>
        </div>
    );
}
