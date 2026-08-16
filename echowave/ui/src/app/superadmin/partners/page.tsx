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
    getQueueApiV1AdminPartnersQueueGet,
    rejectApplicationApiV1AdminPartnersApplicationIdRejectPost,
} from "@/client/sdk.gen";
import { PanelMessage, useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { detailFromError } from "@/lib/apiError";
import { formatDateIST } from "@/lib/billing/format";

type Application = {
    id: number;
    organization_id: number;
    kind: string;
    expected_minutes_per_month: number | null;
    company_website: string | null;
    note: string | null;
    submitted_at: string | null;
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

    if (applications.length === 0) {
        return <PanelMessage height={180}>Nothing waiting.</PanelMessage>;
    }

    return (
        <div className="space-y-4">
            <div>
                <h1 className="text-lg font-semibold">Partner applications</h1>
                <p className="text-sm text-muted-foreground">
                    Oldest first. Approving sets the commission and the account tier
                    together.
                </p>
            </div>
            {applications.map((application) => (
                <Row
                    key={application.id}
                    application={application}
                    bases={bases}
                    onDone={(id) =>
                        setApplications((prev) => prev.filter((a) => a.id !== id))
                    }
                />
            ))}
        </div>
    );
}
