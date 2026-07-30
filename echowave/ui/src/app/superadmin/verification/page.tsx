"use client";

/**
 * Staff review of telephony KYC.
 *
 * The screen is built around one distinction that is easy to lose: our approval
 * and the carrier's approval are different things. Approving here *forwards* an
 * application to the licensee — it does not let anyone place a call. Only the
 * carrier's verdict does. Every label, button and badge below is worded to keep
 * that visible, because a reviewer who thinks they have enabled an account will
 * tell the customer so.
 *
 * Master-detail rather than a table: reviewing means reading documents against
 * stated business details, which needs a panel, not a row.
 */

import {
    AlertTriangle,
    ArrowRight,
    Building2,
    CheckCircle2,
    Clock,
    Download,
    FileText,
    Loader2,
    PhoneOff,
    ShieldCheck,
    XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    approveApiV1AdminKycOrganizationIdApprovePost,
    carrierVerdictApiV1AdminKycOrganizationIdCarrierVerdictPost,
    claimApiV1AdminKycOrganizationIdClaimPost,
    getDocumentApiV1AdminKycDocumentsDocumentIdGet,
    rejectApiV1AdminKycOrganizationIdRejectPost,
    reviewQueueApiV1AdminKycQueueGet,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type ReviewDocument = {
    id: number;
    kind: string;
    filename: string | null;
    content_type: string | null;
    size_bytes: number | null;
    uploaded_at: string | null;
};

type ReviewItem = {
    organization_id: number;
    organization_name: string | null;
    status: string;
    business_type: string | null;
    legal_name: string | null;
    gstin: string | null;
    submitted_at: string | null;
    forwarded_at: string | null;
    carrier: string | null;
    carrier_status: string | null;
    rejection_reason: string | null;
    carrier_rejection_reason: string | null;
    documents: ReviewDocument[];
};

/**
 * The queue's three working states, plus the two settled ones behind a filter.
 *
 * `waiting` is the default because it is the only group where a reviewer can
 * change anything — the rest are there to answer "what happened to X".
 */
const FILTERS = [
    { key: "waiting", label: "Needs review", statuses: "submitted,under_review" },
    { key: "carrier", label: "With carrier", statuses: "forwarded" },
    { key: "approved", label: "Approved", statuses: "carrier_approved" },
    { key: "rejected", label: "Rejected", statuses: "rejected,carrier_rejected" },
] as const;

type FilterKey = (typeof FILTERS)[number]["key"];

/** Status presentation. `tone` never carries meaning on its own — the label does. */
const STATUS_META: Record<
    string,
    { label: string; tone: string; icon: React.ReactNode }
> = {
    submitted: {
        label: "Awaiting review",
        tone: "bg-amber-500/12 text-amber-700 dark:text-amber-300",
        icon: <Clock className="h-3 w-3" />,
    },
    under_review: {
        label: "Being reviewed",
        tone: "bg-blue-500/12 text-blue-700 dark:text-blue-300",
        icon: <FileText className="h-3 w-3" />,
    },
    forwarded: {
        label: "With the operator",
        tone: "bg-violet-500/12 text-violet-700 dark:text-violet-300",
        icon: <ArrowRight className="h-3 w-3" />,
    },
    carrier_approved: {
        label: "Calling enabled",
        tone: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
        icon: <ShieldCheck className="h-3 w-3" />,
    },
    rejected: {
        label: "Sent back",
        tone: "bg-destructive/12 text-destructive",
        icon: <XCircle className="h-3 w-3" />,
    },
    carrier_rejected: {
        label: "Operator refused",
        tone: "bg-destructive/12 text-destructive",
        icon: <PhoneOff className="h-3 w-3" />,
    },
};

const DOCUMENT_LABELS: Record<string, string> = {
    certificate_of_incorporation: "Certificate of incorporation",
    gst_certificate: "GST certificate",
    authorised_signatory_id: "Authorised signatory ID",
    address_proof: "Address proof",
};

function StatusBadge({ status }: { status: string }) {
    const meta = STATUS_META[status];
    if (!meta) return <Badge variant="secondary">{status}</Badge>;
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
                meta.tone,
            )}
        >
            {meta.icon}
            {meta.label}
        </span>
    );
}

/** How long this has been waiting. The queue is a promise about turnaround, so
 *  age is shown on every row rather than hidden in a timestamp. */
function ageLabel(iso: string | null): string {
    if (!iso) return "—";
    const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
    if (hours < 1) return "just now";
    if (hours < 24) return `${Math.floor(hours)}h`;
    return `${Math.floor(hours / 24)}d`;
}

function formatTimestamp(iso: string | null): string {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
    });
}

function formatSize(bytes: number | null): string {
    if (!bytes) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function VerificationQueuePage() {
    const { user, loading: authLoading } = useAuth();
    const authReady = !authLoading && Boolean(user);

    const [filter, setFilter] = useState<FilterKey>("waiting");
    const [items, setItems] = useState<ReviewItem[]>([]);
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [rejectionReason, setRejectionReason] = useState("");
    const [acting, setActing] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [downloading, setDownloading] = useState<number | null>(null);

    const statuses = useMemo(
        () => FILTERS.find((f) => f.key === filter)!.statuses,
        [filter],
    );

    const load = useCallback(async () => {
        setLoading(true);
        const result = await reviewQueueApiV1AdminKycQueueGet({
            query: { status: statuses },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Failed to load the review queue"));
            setItems([]);
        } else {
            const next = ((result.data?.items ?? []) as ReviewItem[]) ?? [];
            setItems(next);
            setError(null);
            // Keep the current selection if it survived the refresh; otherwise
            // fall to the oldest, which is the one that should be worked next.
            setSelectedId((current) =>
                current !== null && next.some((i) => i.organization_id === current)
                    ? current
                    : (next[0]?.organization_id ?? null),
            );
        }
        setLoading(false);
    }, [statuses]);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    const selected = items.find((i) => i.organization_id === selectedId) ?? null;

    // A reason typed against one application must not follow the reviewer to
    // the next one.
    useEffect(() => {
        setRejectionReason("");
        setActionError(null);
    }, [selectedId]);

    const runAction = async (
        key: string,
        call: () => Promise<{ error?: unknown }>,
        fallback: string,
    ) => {
        setActing(key);
        setActionError(null);
        const result = await call();
        if (result.error) {
            setActionError(detailFromError(result.error, fallback));
        } else {
            await load();
        }
        setActing(null);
    };

    const downloadDocument = async (document_id: number, filename: string | null) => {
        setDownloading(document_id);
        setActionError(null);
        const result = await getDocumentApiV1AdminKycDocumentsDocumentIdGet({
            path: { document_id },
            parseAs: "blob",
        });
        if (result.error || !result.data) {
            setActionError(detailFromError(result.error, "Could not open the document"));
        } else {
            // The API streams these as attachments rather than handing out
            // storage URLs, so the download happens from an in-memory blob.
            const url = URL.createObjectURL(result.data as unknown as Blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = filename || `document-${document_id}`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        }
        setDownloading(null);
    };

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[1400px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Telephony verification
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        Indian regulation requires the licensed operator to verify each
                        customer in its own name. Approving here forwards the documents to
                        them — it does not enable calling.
                    </p>
                </header>

                <nav
                    className="glass-nav mb-6 inline-flex flex-wrap gap-1 p-1.5"
                    aria-label="Queue filters"
                >
                    {FILTERS.map((tab) => (
                        <button
                            key={tab.key}
                            type="button"
                            onClick={() => setFilter(tab.key)}
                            aria-current={filter === tab.key ? "true" : undefined}
                            className={cn(
                                "rounded-full px-4 py-1.5 text-sm font-medium tracking-[-0.014em] transition-all duration-200",
                                filter === tab.key
                                    ? "glass-nav-active"
                                    : "text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground",
                            )}
                        >
                            {tab.label}
                        </button>
                    ))}
                </nav>

                {error ? (
                    <section className="glass-panel flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                        <AlertTriangle className="h-4 w-4 shrink-0" />
                        {error}
                    </section>
                ) : (
                    <div className="grid gap-5 lg:grid-cols-[minmax(280px,340px)_1fr]">
                        <QueueList
                            items={items}
                            loading={loading}
                            selectedId={selectedId}
                            onSelect={setSelectedId}
                        />
                        <ApplicationDetail
                            application={selected}
                            loading={loading}
                            acting={acting}
                            actionError={actionError}
                            downloading={downloading}
                            rejectionReason={rejectionReason}
                            onRejectionReasonChange={setRejectionReason}
                            onDownload={downloadDocument}
                            onClaim={(id) =>
                                runAction(
                                    "claim",
                                    () =>
                                        claimApiV1AdminKycOrganizationIdClaimPost({
                                            path: { organization_id: id },
                                        }),
                                    "Could not claim this application",
                                )
                            }
                            onReject={(id, reason) =>
                                runAction(
                                    "reject",
                                    () =>
                                        rejectApiV1AdminKycOrganizationIdRejectPost({
                                            path: { organization_id: id },
                                            body: { reason },
                                        }),
                                    "Could not send this back",
                                )
                            }
                            onApprove={(id) =>
                                runAction(
                                    "approve",
                                    () =>
                                        approveApiV1AdminKycOrganizationIdApprovePost({
                                            path: { organization_id: id },
                                            body: {},
                                        }),
                                    "Could not forward to the operator",
                                )
                            }
                            onCarrierVerdict={(id, verdict, reason) =>
                                runAction(
                                    `verdict-${verdict}`,
                                    () =>
                                        carrierVerdictApiV1AdminKycOrganizationIdCarrierVerdictPost(
                                            {
                                                path: { organization_id: id },
                                                body: {
                                                    verdict,
                                                    ...(reason
                                                        ? { rejection_reason: reason }
                                                        : {}),
                                                },
                                            },
                                        ),
                                    "Could not record the operator's decision",
                                )
                            }
                        />
                    </div>
                )}
            </div>
        </div>
    );
}

function QueueList({
    items,
    loading,
    selectedId,
    onSelect,
}: {
    items: ReviewItem[];
    loading: boolean;
    selectedId: number | null;
    onSelect: (id: number) => void;
}) {
    if (loading) {
        return (
            <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-[74px] w-full rounded-2xl" />
                ))}
            </div>
        );
    }

    if (items.length === 0) {
        return (
            <section className="glass-panel flex flex-col items-center justify-center gap-2 px-5 py-14 text-center">
                <CheckCircle2 className="h-5 w-5 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">Nothing here right now.</p>
            </section>
        );
    }

    return (
        <div className="space-y-2">
            {items.map((item) => (
                <button
                    key={item.organization_id}
                    type="button"
                    onClick={() => onSelect(item.organization_id)}
                    aria-current={item.organization_id === selectedId ? "true" : undefined}
                    className={cn(
                        "glass-tile w-full px-4 py-3 text-left transition-all duration-200",
                        item.organization_id === selectedId
                            ? "ring-1 ring-[color:var(--glass-amber-edge)]"
                            : "hover:bg-foreground/[0.03]",
                    )}
                >
                    <div className="flex items-start justify-between gap-3">
                        <p className="truncate text-sm font-medium tracking-[-0.012em] text-foreground">
                            {item.legal_name || item.organization_name || "Unnamed account"}
                        </p>
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                            {ageLabel(item.submitted_at)}
                        </span>
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-2">
                        <StatusBadge status={item.status} />
                        <span className="text-xs text-muted-foreground">
                            {item.documents.length} doc
                            {item.documents.length === 1 ? "" : "s"}
                        </span>
                    </div>
                </button>
            ))}
        </div>
    );
}

function ApplicationDetail({
    application,
    loading,
    acting,
    actionError,
    downloading,
    rejectionReason,
    onRejectionReasonChange,
    onDownload,
    onClaim,
    onReject,
    onApprove,
    onCarrierVerdict,
}: {
    application: ReviewItem | null;
    loading: boolean;
    acting: string | null;
    actionError: string | null;
    downloading: number | null;
    rejectionReason: string;
    onRejectionReasonChange: (value: string) => void;
    onDownload: (documentId: number, filename: string | null) => void;
    onClaim: (organizationId: number) => void;
    onReject: (organizationId: number, reason: string) => void;
    onApprove: (organizationId: number) => void;
    onCarrierVerdict: (
        organizationId: number,
        verdict: "approved" | "rejected",
        reason?: string,
    ) => void;
}) {
    if (loading) return <Skeleton className="h-[520px] w-full rounded-2xl" />;

    if (!application) {
        return (
            <section className="glass-panel flex flex-col items-center justify-center gap-2 px-5 py-20 text-center">
                <Building2 className="h-5 w-5 text-muted-foreground" />
                <p className="max-w-xs text-sm text-muted-foreground">
                    Select an application to review its documents.
                </p>
            </section>
        );
    }

    const isOurs =
        application.status === "submitted" || application.status === "under_review";
    const withCarrier = application.status === "forwarded";
    const busy = acting !== null;

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                    <h2 className="text-xl font-semibold tracking-[-0.025em] text-foreground">
                        {application.legal_name || "No legal name given"}
                    </h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                        {application.organization_name} · account #
                        {application.organization_id}
                    </p>
                </div>
                <StatusBadge status={application.status} />
            </div>

            <dl className="mt-5 grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Business type" value={application.business_type} capitalize />
                <Field label="GSTIN" value={application.gstin} mono />
                <Field label="Submitted" value={formatTimestamp(application.submitted_at)} />
                <Field
                    label="Forwarded"
                    value={
                        application.forwarded_at
                            ? formatTimestamp(application.forwarded_at)
                            : "Not yet"
                    }
                />
            </dl>

            {application.rejection_reason && (
                <Notice tone="warning" title="Sent back to the customer">
                    {application.rejection_reason}
                </Notice>
            )}
            {application.carrier_rejection_reason && (
                <Notice tone="critical" title="The operator refused this">
                    {application.carrier_rejection_reason}
                </Notice>
            )}

            <h3 className="mt-6 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                Documents
            </h3>
            {application.documents.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">
                    Nothing uploaded yet.
                </p>
            ) : (
                <ul className="mt-3 space-y-2">
                    {application.documents.map((document) => (
                        <li
                            key={document.id}
                            className="glass-tile flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                        >
                            <div className="min-w-0">
                                <p className="text-sm font-medium tracking-[-0.012em] text-foreground">
                                    {DOCUMENT_LABELS[document.kind] || document.kind}
                                </p>
                                <p className="truncate text-xs text-muted-foreground">
                                    {document.filename}
                                    {document.size_bytes
                                        ? ` · ${formatSize(document.size_bytes)}`
                                        : ""}
                                </p>
                            </div>
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={downloading === document.id}
                                onClick={() => onDownload(document.id, document.filename)}
                            >
                                {downloading === document.id ? (
                                    <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Download className="mr-2 h-3.5 w-3.5" />
                                )}
                                Open
                            </Button>
                        </li>
                    ))}
                </ul>
            )}

            {actionError && (
                <Notice tone="critical" title="That did not go through">
                    {actionError}
                </Notice>
            )}

            {isOurs && (
                <div className="mt-6 border-t border-foreground/[0.07] pt-5">
                    <h3 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        Decision
                    </h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                        Forwarding sends these documents to the operator. The account still
                        cannot place calls until the operator approves it.
                    </p>

                    <Textarea
                        value={rejectionReason}
                        onChange={(event) => onRejectionReasonChange(event.target.value)}
                        placeholder="If you are sending this back, say what needs fixing — the customer sees this."
                        rows={2}
                        className="mt-3"
                    />

                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                            disabled={busy}
                            onClick={() => onApprove(application.organization_id)}
                        >
                            {acting === "approve" ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <ArrowRight className="mr-2 h-4 w-4" />
                            )}
                            Approve &amp; forward to operator
                        </Button>
                        <Button
                            variant="outline"
                            disabled={busy || rejectionReason.trim().length === 0}
                            onClick={() =>
                                onReject(application.organization_id, rejectionReason.trim())
                            }
                        >
                            {acting === "reject" ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <XCircle className="mr-2 h-4 w-4" />
                            )}
                            Send back
                        </Button>
                        {application.status === "submitted" && (
                            <Button
                                variant="ghost"
                                disabled={busy}
                                onClick={() => onClaim(application.organization_id)}
                            >
                                {acting === "claim" ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : null}
                                I&apos;m looking at this
                            </Button>
                        )}
                    </div>
                </div>
            )}

            {withCarrier && (
                <div className="mt-6 border-t border-foreground/[0.07] pt-5">
                    <h3 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        Operator decision
                    </h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                        Sent to{" "}
                        <span className="font-medium text-foreground">
                            {application.carrier ?? "the operator"}
                        </span>
                        {application.carrier_status
                            ? ` · last status "${application.carrier_status}"`
                            : ""}
                        . Record their decision here once it arrives. Approving turns on
                        calling for this account.
                    </p>

                    <Textarea
                        value={rejectionReason}
                        onChange={(event) => onRejectionReasonChange(event.target.value)}
                        placeholder="If the operator refused, paste their reason here."
                        rows={2}
                        className="mt-3"
                    />

                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                            disabled={busy}
                            onClick={() =>
                                onCarrierVerdict(application.organization_id, "approved")
                            }
                        >
                            {acting === "verdict-approved" ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <ShieldCheck className="mr-2 h-4 w-4" />
                            )}
                            Operator approved — enable calling
                        </Button>
                        <Button
                            variant="outline"
                            disabled={busy || rejectionReason.trim().length === 0}
                            onClick={() =>
                                onCarrierVerdict(
                                    application.organization_id,
                                    "rejected",
                                    rejectionReason.trim(),
                                )
                            }
                        >
                            {acting === "verdict-rejected" ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <PhoneOff className="mr-2 h-4 w-4" />
                            )}
                            Operator refused
                        </Button>
                    </div>
                </div>
            )}
        </section>
    );
}

function Field({
    label,
    value,
    mono,
    capitalize,
}: {
    label: string;
    value: string | null;
    mono?: boolean;
    capitalize?: boolean;
}) {
    return (
        <div>
            <dt className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                {label}
            </dt>
            <dd
                className={cn(
                    "mt-1 text-sm tracking-[-0.012em] text-foreground",
                    mono && "font-mono text-xs",
                    capitalize && "capitalize",
                )}
            >
                {value || "—"}
            </dd>
        </div>
    );
}

function Notice({
    tone,
    title,
    children,
}: {
    tone: "warning" | "critical";
    title: string;
    children: React.ReactNode;
}) {
    return (
        <div
            className={cn(
                "mt-4 rounded-xl px-4 py-3 text-sm",
                tone === "critical"
                    ? "bg-destructive/10 text-destructive"
                    : "bg-amber-500/10 text-amber-700 dark:text-amber-300",
            )}
        >
            <p className="font-medium">{title}</p>
            <p className="mt-0.5 opacity-90">{children}</p>
        </div>
    );
}
