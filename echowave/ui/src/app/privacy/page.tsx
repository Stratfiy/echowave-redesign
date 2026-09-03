"use client";

/**
 * Data protection, for the customer to operate themselves.
 *
 * The controls behind this screen were built first and were reachable only by
 * API, which is a real gap rather than a cosmetic one: a right that requires
 * writing a curl command is a right most people never exercise, and "we support
 * erasure" stops being true in the way that matters.
 *
 * Two decisions worth knowing about:
 *
 * **Erasure asks for the number twice.** Not friction for its own sake — the
 * operation is irreversible by design, because a right to erasure satisfied by
 * something recoverable is not satisfied. A typo here deletes the wrong
 * person's calls and nothing brings them back.
 *
 * **The sub-processor list is read, never edited.** It is derived server-side
 * from the vendors that actually priced this account's calls. Nothing on this
 * page can add to it, which is the point: a list somebody can edit is a list
 * that goes stale.
 */

import {
    AlertTriangle,
    Download,
    Loader2,
    Search,
    ShieldCheck,
    Trash2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    breachReportApiV1PrivacyBreachReportGet,
    exportDataApiV1PrivacyExportGet,
    getAccessLogApiV1PrivacyAccessLogGet,
    getRetentionApiV1PrivacyRetentionGet,
    listErasureRequestsApiV1PrivacyErasureGet,
    listSubprocessorsApiV1PrivacySubprocessorsGet,
    privacyMetricsApiV1PrivacyMetricsGet,
    requestErasureApiV1PrivacyErasurePost,
    setRetentionApiV1PrivacyRetentionPut,
} from "@/client/sdk.gen";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError, detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDateTimeIST } from "@/lib/billing/format";

type Retention = {
    recording_retention_days: number;
    transcript_retention_days: number;
    is_platform_default: boolean;
    minimum_days: number;
};

type ErasureRecord = {
    id: number;
    status: string;
    runs_affected: number;
    objects_deleted: number;
    requested_at: string | null;
    completed_at: string | null;
};

type Subprocessor = {
    name: string;
    purpose: string;
    data: string;
    basis: string;
};

type GrievanceOfficer = {
    name: string | null;
    email: string | null;
    address: string | null;
};

type AccessEntry = {
    at: string | null;
    user_id: number | null;
    resource_type: string;
    resource_id: string | null;
    workflow_run_id: number | null;
    action: string;
    actor_kind: string | null;
    ip_address: string | null;
};

type PrivacyMetrics = {
    window_days: number;
    retention: {
        overdue: number;
        healthy: boolean;
        oldest_overdue_days: number | null;
    };
    access: {
        total: number;
        exports: number;
        unauthenticated: number;
        distinct_users: number;
    };
    erasure: {
        total: number;
        completed: number;
        open: number;
        past_deadline: number;
        deadline_days: number;
        median_hours: number | null;
        slowest_hours: number | null;
    };
};

type BreachReport = {
    window_start: string;
    window_end: string;
    total_accesses: number;
    by_resource_type: Record<string, number>;
    by_actor: Record<string, number>;
    distinct_calls_touched: number;
    distinct_objects_touched: number;
    affected_call_ids: number[];
    first_access: string | null;
    last_access: string | null;
};

const BASIS_LABEL: Record<string, string> = {
    configured: "Configured for your calls",
    observed: "Handled your calls",
    infrastructure: "Platform infrastructure",
};

function when(value: string | null): string {
    return value ? formatDateTimeIST(value) : "—";
}

export default function PrivacyPage() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    const [retention, setRetention] = useState<Retention | null>(null);
    const [recordingDays, setRecordingDays] = useState("");
    const [transcriptDays, setTranscriptDays] = useState("");
    const [savingRetention, setSavingRetention] = useState(false);

    const [erasureNumber, setErasureNumber] = useState("");
    const [confirmNumber, setConfirmNumber] = useState("");
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [erasing, setErasing] = useState(false);
    const [erasures, setErasures] = useState<ErasureRecord[]>([]);

    const [exportNumber, setExportNumber] = useState("");
    const [exporting, setExporting] = useState(false);

    const [subprocessors, setSubprocessors] = useState<Subprocessor[]>([]);
    const [officer, setOfficer] = useState<GrievanceOfficer | null>(null);
    const [access, setAccess] = useState<AccessEntry[]>([]);
    const [metrics, setMetrics] = useState<PrivacyMetrics | null>(null);

    const nowLocal = () => new Date().toISOString().slice(0, 16);
    const weekAgoLocal = () =>
        new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 16);

    const [breachSince, setBreachSince] = useState(weekAgoLocal());
    const [breachUntil, setBreachUntil] = useState(nowLocal());
    const [breachRunning, setBreachRunning] = useState(false);
    const [breachError, setBreachError] = useState<string | null>(null);
    const [breachReport, setBreachReport] = useState<BreachReport | null>(null);

    const refresh = useCallback(async () => {
        const [
            retentionResponse,
            erasureResponse,
            subprocessorResponse,
            accessResponse,
            metricsResponse,
        ] = await Promise.all([
            getRetentionApiV1PrivacyRetentionGet(),
            listErasureRequestsApiV1PrivacyErasureGet(),
            listSubprocessorsApiV1PrivacySubprocessorsGet(),
            getAccessLogApiV1PrivacyAccessLogGet(),
            privacyMetricsApiV1PrivacyMetricsGet(),
        ]);

        if (retentionResponse.error) {
            setError(
                detailFromError(
                    retentionResponse.error,
                    "Could not load your retention settings",
                ),
            );
            return;
        }

        const policy = retentionResponse.data as unknown as Retention;
        setRetention(policy);
        setRecordingDays(String(policy.recording_retention_days));
        setTranscriptDays(String(policy.transcript_retention_days));

        if (!erasureResponse.error) {
            setErasures(
                (erasureResponse.data as unknown as { requests: ErasureRecord[] })
                    .requests ?? [],
            );
        }
        if (!subprocessorResponse.error) {
            const payload = subprocessorResponse.data as unknown as {
                subprocessors: Subprocessor[];
                grievance_officer: GrievanceOfficer;
            };
            setSubprocessors(payload.subprocessors ?? []);
            setOfficer(payload.grievance_officer ?? null);
        }
        if (!accessResponse.error) {
            setAccess(
                (accessResponse.data as unknown as { entries: AccessEntry[] }).entries ??
                    [],
            );
        }
        if (!metricsResponse.error) {
            setMetrics(metricsResponse.data as unknown as PrivacyMetrics);
        }
        setError(null);
    }, []);

    const runBreachReport = useCallback(async () => {
        setBreachError(null);
        setBreachRunning(true);
        try {
            const since = new Date(breachSince).toISOString();
            const until = new Date(breachUntil).toISOString();
            const response = await breachReportApiV1PrivacyBreachReportGet({
                query: { since, until },
            });
            if (response.error) {
                setBreachError(
                    detailFromResult(response, "Could not build the breach report"),
                );
                setBreachReport(null);
                return;
            }
            setBreachReport(response.data as unknown as BreachReport);
        } finally {
            setBreachRunning(false);
        }
    }, [breachSince, breachUntil]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            await refresh();
            setLoading(false);
        })();
    }, [authLoading, user, refresh]);

    const saveRetention = useCallback(async () => {
        setError(null);
        setNotice(null);
        setSavingRetention(true);
        try {
            const response = await setRetentionApiV1PrivacyRetentionPut({
                body: {
                    recording_retention_days: Number(recordingDays),
                    transcript_retention_days: Number(transcriptDays),
                },
            });
            if (response.error) {
                setError(
                    detailFromError(
                        response.error,
                        "Could not save your retention settings",
                    ),
                );
                return;
            }
            setNotice(
                "Saved. Anything already past the new window is deleted by tonight's sweep.",
            );
            await refresh();
        } finally {
            setSavingRetention(false);
        }
    }, [recordingDays, transcriptDays, refresh]);

    const erase = useCallback(async () => {
        setConfirmOpen(false);
        setError(null);
        setNotice(null);
        setErasing(true);
        try {
            const response = await requestErasureApiV1PrivacyErasurePost({
                body: { phone_number: erasureNumber },
            });
            if (response.error) {
                setError(detailFromResult(response, "Could not erase that number"));
                return;
            }
            const result = response.data as unknown as {
                calls_erased: number;
                files_deleted: number;
            };
            setNotice(
                `Erased ${result.calls_erased} call${
                    result.calls_erased === 1 ? "" : "s"
                } and deleted ${result.files_deleted} file${
                    result.files_deleted === 1 ? "" : "s"
                }.`,
            );
            setErasureNumber("");
            setConfirmNumber("");
            await refresh();
        } finally {
            setErasing(false);
        }
    }, [erasureNumber, refresh]);

    /** Downloads the export rather than rendering it: it is a portability
     *  artefact meant to be handed on, and an account's whole history is not
     *  something to paint into a table. */
    const download = useCallback(async () => {
        setError(null);
        setNotice(null);
        setExporting(true);
        try {
            const response = await exportDataApiV1PrivacyExportGet(
                exportNumber ? { query: { phone_number: exportNumber } } : {},
            );
            if (response.error) {
                setError(detailFromResult(response, "Could not build the export"));
                return;
            }
            const blob = new Blob([JSON.stringify(response.data, null, 2)], {
                type: "application/json",
            });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = exportNumber
                ? `decibyl-export-${exportNumber.replace(/[^0-9]/g, "")}.json`
                : "decibyl-export.json";
            link.click();
            URL.revokeObjectURL(url);
            setNotice("Export downloaded.");
        } finally {
            setExporting(false);
        }
    }, [exportNumber]);

    if (loading) {
        return (
            <div className="flex justify-center py-12 px-4">
                <div className="w-full max-w-4xl space-y-6">
                    <Skeleton className="h-8 w-64" />
                    <Skeleton className="h-48 w-full" />
                    <Skeleton className="h-48 w-full" />
                </div>
            </div>
        );
    }

    return (
        <div className="flex justify-center py-12 px-4">
            <div className="w-full max-w-4xl space-y-6">
                <div>
                    <h1 className="text-2xl font-bold">Privacy &amp; data</h1>
                    <p className="text-muted-foreground">
                        The people you call have rights over their data. These are the
                        tools to honour them.
                    </p>
                </div>

                {error && (
                    <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                        <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                        <span>{error}</span>
                    </div>
                )}
                {notice && (
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
                        {notice}
                    </div>
                )}

                <Card>
                    <CardHeader>
                        <CardTitle>Are the controls actually working</CardTitle>
                        <CardDescription>
                            Every control on this page reports success by not raising,
                            which is the wrong kind of quiet — a retention sweep that
                            silently stopped running looks exactly like one with
                            nothing to do. The headline below is designed to be
                            zero; any other value is an incident, not a statistic.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {metrics ? (
                            <div className="grid gap-4 sm:grid-cols-3">
                                <div
                                    className={`rounded-lg border p-3 ${
                                        metrics.retention.healthy
                                            ? "border-emerald-500/40 bg-emerald-500/10"
                                            : "border-destructive/40 bg-destructive/10"
                                    }`}
                                >
                                    <p className="text-2xl font-semibold tabular-nums">
                                        {metrics.retention.overdue}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        Recordings past their retention window and
                                        still in storage — should always be zero.
                                        {metrics.retention.oldest_overdue_days
                                            ? ` Oldest is ${metrics.retention.oldest_overdue_days} days overdue.`
                                            : ""}
                                    </p>
                                </div>
                                <div className="rounded-lg border p-3">
                                    <p className="text-2xl font-semibold tabular-nums">
                                        {metrics.access.total}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        Accesses to recordings/transcripts in the
                                        last {metrics.window_days} days —{" "}
                                        {metrics.access.unauthenticated} via a public
                                        share link, {metrics.access.distinct_users}{" "}
                                        distinct people signed in.
                                    </p>
                                </div>
                                <div
                                    className={`rounded-lg border p-3 ${
                                        metrics.erasure.past_deadline > 0
                                            ? "border-destructive/40 bg-destructive/10"
                                            : ""
                                    }`}
                                >
                                    <p className="text-2xl font-semibold tabular-nums">
                                        {metrics.erasure.open}
                                    </p>
                                    <p className="text-xs text-muted-foreground">
                                        Open erasure requests
                                        {metrics.erasure.past_deadline > 0
                                            ? `, ${metrics.erasure.past_deadline} past the ${metrics.erasure.deadline_days}-day deadline`
                                            : ` — none past the ${metrics.erasure.deadline_days}-day deadline`}
                                        .
                                        {metrics.erasure.median_hours !== null
                                            ? ` Median turnaround ${metrics.erasure.median_hours}h.`
                                            : ""}
                                    </p>
                                </div>
                            </div>
                        ) : (
                            <Skeleton className="h-20 w-full" />
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>How long call data is kept</CardTitle>
                        <CardDescription>
                            Deletion is automatic — a nightly job removes anything past
                            its window, from storage as well as the database. Audio and
                            text age separately: a recording is a person&apos;s voice and
                            is rarely useful a month later, while a transcript is what
                            reporting actually reads.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="recording-days">
                                    Recordings (days)
                                </Label>
                                <Input
                                    id="recording-days"
                                    type="number"
                                    min={retention?.minimum_days ?? 1}
                                    value={recordingDays}
                                    onChange={(event) =>
                                        setRecordingDays(event.target.value)
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="transcript-days">
                                    Transcripts (days)
                                </Label>
                                <Input
                                    id="transcript-days"
                                    type="number"
                                    min={retention?.minimum_days ?? 1}
                                    value={transcriptDays}
                                    onChange={(event) =>
                                        setTranscriptDays(event.target.value)
                                    }
                                />
                            </div>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            {retention?.is_platform_default
                                ? "Currently on the platform defaults."
                                : "Set for this account."}{" "}
                            Minimum {retention?.minimum_days ?? 1} day — a window
                            mistyped as zero would delete calls as they finished.
                            Billing records are kept longer, as GST law requires; what
                            survives is a duration and an amount, which identifies
                            nobody.
                        </p>
                        <Button onClick={saveRetention} disabled={savingRetention}>
                            {savingRetention && (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            )}
                            Save
                        </Button>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Export someone&apos;s data</CardTitle>
                        <CardDescription>
                            Machine-readable JSON, for one phone number or for the whole
                            account. Data already erased is reported as erased rather
                            than omitted — &quot;we deleted this&quot; and &quot;we never
                            had this&quot; are different answers.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="export-number">
                                Phone number (leave blank for the whole account)
                            </Label>
                            <Input
                                id="export-number"
                                placeholder="+91 98765 43210"
                                value={exportNumber}
                                onChange={(event) => setExportNumber(event.target.value)}
                            />
                        </div>
                        <Button
                            variant="outline"
                            onClick={download}
                            disabled={exporting}
                        >
                            {exporting ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Download className="h-4 w-4" />
                            )}
                            Download export
                        </Button>
                    </CardContent>
                </Card>

                <Card className="border-destructive/40">
                    <CardHeader>
                        <CardTitle>Erase someone&apos;s data</CardTitle>
                        <CardDescription>
                            Deletes the recordings, transcripts and gathered context of
                            every call to or from a number.{" "}
                            <strong>This cannot be undone</strong> — a right to erasure
                            satisfied by something recoverable is not satisfied. The
                            duration and cost of each call remain, because GST records
                            must outlive the conversation they describe.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="erasure-number">Phone number</Label>
                                <Input
                                    id="erasure-number"
                                    placeholder="+91 98765 43210"
                                    value={erasureNumber}
                                    onChange={(event) =>
                                        setErasureNumber(event.target.value)
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="erasure-confirm">
                                    Type it again to confirm
                                </Label>
                                <Input
                                    id="erasure-confirm"
                                    placeholder="+91 98765 43210"
                                    value={confirmNumber}
                                    onChange={(event) =>
                                        setConfirmNumber(event.target.value)
                                    }
                                />
                            </div>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Formatting does not matter:{" "}
                            <code>+91 98765 43210</code>, <code>09876543210</code> and{" "}
                            <code>9876543210</code> all match the same person.
                        </p>
                        <Button
                            variant="destructive"
                            disabled={
                                erasing ||
                                erasureNumber.trim().length < 4 ||
                                erasureNumber.replace(/[^0-9]/g, "") !==
                                    confirmNumber.replace(/[^0-9]/g, "")
                            }
                            onClick={() => setConfirmOpen(true)}
                        >
                            {erasing ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Trash2 className="h-4 w-4" />
                            )}
                            Erase this number
                        </Button>

                        {erasures.length > 0 && (
                            <div className="pt-2">
                                <p className="text-sm font-medium mb-2">
                                    Erasure history
                                </p>
                                <p className="text-xs text-muted-foreground mb-2">
                                    Kept deliberately. The record of an erasure is not
                                    the erased data, and it is the only evidence the
                                    request was honoured. It holds a one-way hash of the
                                    number, never the number.
                                </p>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Requested</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead className="text-right">
                                                Calls
                                            </TableHead>
                                            <TableHead className="text-right">
                                                Files
                                            </TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {erasures.map((record) => (
                                            <TableRow key={record.id}>
                                                <TableCell>
                                                    {when(record.requested_at)}
                                                </TableCell>
                                                <TableCell>{record.status}</TableCell>
                                                <TableCell className="text-right">
                                                    {record.runs_affected}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    {record.objects_deleted}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Who else has your data</CardTitle>
                        <CardDescription>
                            Generated from the vendors that actually handled your calls,
                            not from a list maintained by hand. A vendor you never routed
                            to has processed nothing, and does not appear.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Company</TableHead>
                                    <TableHead>What they do</TableHead>
                                    <TableHead>What they receive</TableHead>
                                    <TableHead>Why they are listed</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {subprocessors.map((entry) => (
                                    <TableRow key={`${entry.name}-${entry.basis}`}>
                                        <TableCell className="font-medium">
                                            {entry.name}
                                        </TableCell>
                                        <TableCell>{entry.purpose}</TableCell>
                                        <TableCell>{entry.data}</TableCell>
                                        <TableCell className="text-muted-foreground">
                                            {BASIS_LABEL[entry.basis] ?? entry.basis}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>

                        {officer?.email && (
                            <div className="flex items-start gap-2 rounded-md border p-3 text-sm">
                                <ShieldCheck className="h-4 w-4 mt-0.5 shrink-0" />
                                <div>
                                    <p className="font-medium">
                                        Data protection grievances
                                    </p>
                                    <p className="text-muted-foreground">
                                        {officer.name ? `${officer.name} — ` : ""}
                                        <a
                                            className="underline"
                                            href={`mailto:${officer.email}`}
                                        >
                                            {officer.email}
                                        </a>
                                        {officer.address ? ` · ${officer.address}` : ""}
                                    </p>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Who accessed your recordings</CardTitle>
                        <CardDescription>
                            Recorded when a signed link is issued, because that is the
                            moment access becomes possible. Access through a public share
                            link shows no user, which is exactly the case worth being
                            able to see.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {access.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                Nothing has been accessed yet.
                            </p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>When</TableHead>
                                        <TableHead>What</TableHead>
                                        <TableHead>Call</TableHead>
                                        <TableHead>Who</TableHead>
                                        <TableHead>From</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {access.map((entry, index) => (
                                        <TableRow key={`${entry.at}-${index}`}>
                                            <TableCell>{when(entry.at)}</TableCell>
                                            <TableCell>{entry.resource_type}</TableCell>
                                            <TableCell>
                                                {entry.workflow_run_id ?? "—"}
                                            </TableCell>
                                            <TableCell>
                                                {entry.user_id
                                                    ? `User ${entry.user_id}`
                                                    : (entry.actor_kind ??
                                                      "Unauthenticated")}
                                            </TableCell>
                                            <TableCell>
                                                {entry.ip_address ?? "—"}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Breach report</CardTitle>
                        <CardDescription>
                            GDPR Art 33 gives 72 hours from becoming aware of a breach
                            to describe its scope. That question cannot be answered
                            retrospectively — either the access log was being written
                            or it was not — so this turns the log into an answer for
                            any window you name.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label htmlFor="breach-since">From</Label>
                                <Input
                                    id="breach-since"
                                    type="datetime-local"
                                    value={breachSince}
                                    onChange={(event) =>
                                        setBreachSince(event.target.value)
                                    }
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="breach-until">To</Label>
                                <Input
                                    id="breach-until"
                                    type="datetime-local"
                                    value={breachUntil}
                                    onChange={(event) =>
                                        setBreachUntil(event.target.value)
                                    }
                                />
                            </div>
                        </div>
                        <Button
                            variant="outline"
                            onClick={runBreachReport}
                            disabled={breachRunning}
                        >
                            {breachRunning ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Search className="h-4 w-4" />
                            )}
                            Run report
                        </Button>
                        {breachError && (
                            <p className="text-sm text-destructive">{breachError}</p>
                        )}
                        {breachReport && (
                            <div className="space-y-3 rounded-lg border p-3">
                                <div className="grid gap-4 sm:grid-cols-3">
                                    <div>
                                        <p className="text-xl font-semibold tabular-nums">
                                            {breachReport.total_accesses}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Total accesses in window
                                        </p>
                                    </div>
                                    <div>
                                        <p className="text-xl font-semibold tabular-nums">
                                            {breachReport.distinct_calls_touched}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Distinct calls touched
                                        </p>
                                    </div>
                                    <div>
                                        <p className="text-xl font-semibold tabular-nums">
                                            {breachReport.distinct_objects_touched}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            Distinct objects touched
                                        </p>
                                    </div>
                                </div>
                                {breachReport.total_accesses > 0 && (
                                    <>
                                        <div className="text-sm">
                                            <p className="font-medium">By resource type</p>
                                            <p className="text-muted-foreground">
                                                {Object.entries(
                                                    breachReport.by_resource_type,
                                                )
                                                    .map(([type, n]) => `${type}: ${n}`)
                                                    .join(", ") || "—"}
                                            </p>
                                        </div>
                                        <div className="text-sm">
                                            <p className="font-medium">By who accessed</p>
                                            <p className="text-muted-foreground">
                                                {Object.entries(breachReport.by_actor)
                                                    .map(([who, n]) => `${who}: ${n}`)
                                                    .join(", ") || "—"}
                                            </p>
                                        </div>
                                        {breachReport.affected_call_ids.length > 0 && (
                                            <div className="text-sm">
                                                <p className="font-medium">
                                                    Affected call IDs
                                                </p>
                                                <p className="break-words text-muted-foreground">
                                                    {breachReport.affected_call_ids.join(
                                                        ", ",
                                                    )}
                                                </p>
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>

            <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            Erase every call with {erasureNumber}?
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            The recordings, transcripts and gathered context of every
                            call to or from this number will be deleted from storage.
                            There is no undo and no backup to restore from — that is what
                            makes it an erasure.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={erase}>
                            Erase permanently
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
