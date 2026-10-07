"use client";

/**
 * Settings -> Privacy -> Privacy and security (screen 25; handoff 24).
 *
 * Three groups, kept apart: account security (two-step sign-in, easy to
 * find), retention as the workspace actually runs it (and who manages it),
 * and your own data rights -- export and deletion, each opening a scoped
 * preview of exactly what it covers. Deletion asks for an identity check,
 * then a card you confirm; afterwards every store is listed with what
 * happened to it and any legitimate exception. Your own data is kept apart
 * from the workspace's: closing the workspace is the owner's, under
 * Compliance.
 */

import { Download, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    downloadMyExportApiV1MePrivacyRequestsRequestIdDownloadGet,
    myDataRequestApiV1MePrivacyRequestsRequestIdGet,
    myPrivacyApiV1MePrivacyGet,
    privacyPreviewApiV1MePrivacyPreviewGet,
    requestMyDeletionApiV1MePrivacyDeletionPost,
    requestMyExportApiV1MePrivacyExportPost,
} from "@/client/sdk.gen";
import type { DataPreview, DataRequest, PrivacyOverview, SettingsCard } from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MfaSection } from "@/components/MfaSection";
import { ErrorState } from "@/components/shell/ErrorState";
import { SettingsSection } from "@/components/shell/SettingsSection";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import { SettingsCardPanel } from "../SettingsCardPanel";
import { INPUT } from "../SettingsForm";

const STATUS: Record<string, string> = {
    queued: "Queued",
    running: "Being prepared",
    ready: "Ready to download",
    expired: "Expired",
    failed: "Failed",
    awaiting_approval: "Waiting for your OK",
    pending: "Pending",
    complete: "Complete",
    partial_failure: "Partly done",
    cancelled: "Cancelled",
};

function StoreList({ preview }: { preview: DataPreview }) {
    return (
        <div className="flex flex-col gap-2 text-sm" data-testid={`privacy-preview-${preview.kind}`}>
            <ul className="divide-y divide-border rounded-md border border-border">
                {preview.stores.map((store) => (
                    <li key={String(store.store)} className="flex items-baseline justify-between gap-3 px-3 py-2">
                        <span className="min-w-0 break-words">{String(store.label)}</span>
                        <span className="shrink-0 text-muted-foreground tabular-nums">{String(store.count)}</span>
                    </li>
                ))}
            </ul>
            {preview.exceptions.length > 0 && (
                <>
                    <p className="font-medium">Not deleted here</p>
                    <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                        {preview.exceptions.map((e) => (
                            <li key={String(e.store)}>
                                {String(e.label)}: {String(e.exception)}
                            </li>
                        ))}
                    </ul>
                </>
            )}
            <ul className="space-y-1 text-muted-foreground">
                {preview.lines.map((line) => (
                    <li key={line}>{line}</li>
                ))}
            </ul>
        </div>
    );
}

function RequestRow({ request, onRefresh }: { request: DataRequest; onRefresh: () => void }) {
    const waiting = request.status === "queued" || request.status === "running";
    useEffect(() => {
        if (!waiting) return;
        const timer = setTimeout(onRefresh, 2000);
        return () => clearTimeout(timer);
    }, [waiting, onRefresh]);
    const [failed, setFailed] = useState<string | null>(null);
    const download = async () => {
        setFailed(null);
        // The person's own data, fetched with their session, saved as a file.
        const response = await downloadMyExportApiV1MePrivacyRequestsRequestIdDownloadGet({
            path: { request_id: request.id },
            parseAs: "blob",
        });
        if (response.error || !response.data) {
            setFailed(detailFromError(response.error, "Could not download it."));
            onRefresh();
            return;
        }
        const url = URL.createObjectURL(response.data as Blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "my-decibyl-data.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    };
    return (
        <li className="flex flex-col gap-1 py-2 text-sm" data-testid="privacy-request" data-status={request.status}>
            <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                    {request.kind === "export" ? "Export" : "Deletion"} · {new Date(request.created_at).toLocaleDateString()}
                </span>
                <span className="flex items-center gap-1 text-muted-foreground">
                    {waiting && <Loader2 aria-hidden className="motion-continuous h-3.5 w-3.5 animate-spin" />}
                    {STATUS[request.status] ?? request.status}
                </span>
            </div>
            {request.kind === "export" && request.status === "ready" && (
                <div className="flex flex-wrap items-center gap-2">
                    <Button type="button" variant="outline" size="sm" className="min-h-11 md:min-h-8" onClick={() => void download()}>
                        <Download aria-hidden /> Download
                    </Button>
                    {request.expires_at && <span className="text-xs text-muted-foreground">Until {new Date(request.expires_at).toLocaleDateString()}</span>}
                </div>
            )}
            {request.kind === "deletion" && request.stores.length > 0 && request.status !== "awaiting_approval" && (
                <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                    {request.stores.map((store) => (
                        <li key={String(store.store)}>
                            {String(store.label)}: {String(store.status)}
                            {store.count !== null && store.count !== undefined ? ` (${String(store.count)})` : ""}
                            {store.exception ? ` -- ${String(store.exception)}` : ""}
                        </li>
                    ))}
                </ul>
            )}
            {request.error && <p className="text-xs text-destructive">{request.error}</p>}
            {failed && (
                <p role="alert" className="text-xs text-destructive">
                    {failed}
                </p>
            )}
        </li>
    );
}

export function PrivacySettings() {
    const { user, loading: authLoading } = useAuth();
    const [overview, setOverview] = useState<PrivacyOverview | null>(null);
    const [failed, setFailed] = useState(false);
    const [exportPreview, setExportPreview] = useState<DataPreview | null>(null);
    const [deletionPreview, setDeletionPreview] = useState<DataPreview | null>(null);
    const [phrase, setPhrase] = useState("");
    const [code, setCode] = useState("");
    const [card, setCard] = useState<SettingsCard | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const started = useRef(false);

    const load = useCallback(async () => {
        setFailed(false);
        const result = await myPrivacyApiV1MePrivacyGet();
        if (result.error || !result.data) {
            setFailed(true);
            return;
        }
        setOverview(result.data);
    }, []);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
    }, [authLoading, user, load]);

    const refreshRequest = useCallback(
        async (id: number) => {
            const result = await myDataRequestApiV1MePrivacyRequestsRequestIdGet({ path: { request_id: id } });
            if (result.data) void load();
        },
        [load],
    );

    const previewOf = async (kind: "export" | "deletion") => {
        setError(null);
        const result = await privacyPreviewApiV1MePrivacyPreviewGet({ query: { kind } });
        if (result.error || !result.data) {
            setError(detailFromError(result.error, "Could not prepare the preview."));
            return;
        }
        if (kind === "export") setExportPreview(result.data);
        else setDeletionPreview(result.data);
    };

    if (failed) {
        return (
            <>
                <PageHeader title="Privacy and security" />
                <PageBody className="max-w-[640px]">
                    <ErrorState title="Could not load privacy and security" onRetry={() => void load()} />
                </PageBody>
            </>
        );
    }

    const mfa = overview?.security.mfa_enabled === true;

    return (
        <>
            <PageHeader title="Privacy and security" description="How you sign in, how long things are kept, and your own data." />
            <PageBody className="max-w-[640px]">
                {!overview ? (
                    <Skeleton className="h-64 w-full" />
                ) : (
                    <div className="flex flex-col gap-6">
                        <SettingsSection id="security" title="Two-step sign-in" description="A code from an authenticator app at sign-in, on top of your password." scope="Just you">
                            <MfaSection />
                        </SettingsSection>

                        <SettingsSection
                            id="retention"
                            title="How long things are kept"
                            description="What this workspace actually runs, read from the same policy that deletes it."
                            scope={String(overview.retention.workspace_name)}
                        >
                            <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-2 text-sm">
                                <dt>Call recordings</dt>
                                <dd className="text-right tabular-nums">{String(overview.retention.recording_days)} days</dd>
                                <dt>Transcripts and call history</dt>
                                <dd className="text-right tabular-nums">{String(overview.retention.transcript_days)} days</dd>
                                <dt>Temporary conversations</dt>
                                <dd className="text-right tabular-nums">{String(overview.retention.temporary_conversation_hours)} hours</dd>
                                <dt>Your export, once ready</dt>
                                <dd className="text-right tabular-nums">{String(overview.retention.export_days)} days</dd>
                            </dl>
                            <p className="mt-3 text-xs text-muted-foreground">
                                {overview.retention.is_platform_default ? "Decibyl's defaults. " : "Set by this workspace. "}
                                {overview.retention.managed_by_workspace ? (
                                    "Managed by your workspace: its admins change it under Compliance."
                                ) : (
                                    <>
                                        You can change it under{" "}
                                        <Link className="underline underline-offset-2" href="/settings/compliance">
                                            Compliance
                                        </Link>
                                        .
                                    </>
                                )}
                            </p>
                        </SettingsSection>

                        <SettingsSection id="export" title="Download your data" description="Your own settings, memory, saved items and feedback, as one file." scope="Just you">
                            {!exportPreview ? (
                                <Button type="button" variant="outline" className="min-h-11 md:min-h-9" onClick={() => void previewOf("export")}>
                                    See what is included
                                </Button>
                            ) : (
                                <div className="flex flex-col gap-3">
                                    <StoreList preview={exportPreview} />
                                    <Button
                                        type="button"
                                        className="min-h-11 self-start md:min-h-9"
                                        disabled={busy}
                                        onClick={async () => {
                                            setBusy(true);
                                            const result = await requestMyExportApiV1MePrivacyExportPost();
                                            setBusy(false);
                                            if (result.error) setError(detailFromError(result.error, "Could not start the export."));
                                            setExportPreview(null);
                                            void load();
                                        }}
                                    >
                                        Prepare my export
                                    </Button>
                                </div>
                            )}
                        </SettingsSection>

                        <SettingsSection id="delete" title="Delete your data" description="Your own data only. Nothing of your workspaces is deleted." scope="Just you">
                            {!overview.deletion_available ? (
                                <p className="text-sm text-muted-foreground" data-testid="deletion-unavailable">
                                    Unavailable here yet: deleting your data asks for your OK in your personal space, which is not open on this
                                    workspace. Help can do it for you meanwhile.
                                </p>
                            ) : card ? (
                                <SettingsCardPanel card={card} onSettled={() => void load()} />
                            ) : !deletionPreview ? (
                                <Button type="button" variant="outline" className="min-h-11 text-destructive md:min-h-9" onClick={() => void previewOf("deletion")}>
                                    See what would be deleted
                                </Button>
                            ) : (
                                <div className="flex flex-col gap-3">
                                    <StoreList preview={deletionPreview} />
                                    {mfa ? (
                                        <label className="text-sm">
                                            <span className="mb-1 block font-medium">Code from your authenticator app</span>
                                            <input className={INPUT} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value)} />
                                        </label>
                                    ) : (
                                        <label className="text-sm">
                                            <span className="mb-1 block font-medium">Type “{overview.delete_phrase}” to continue</span>
                                            <input className={INPUT} value={phrase} autoComplete="off" onChange={(event) => setPhrase(event.target.value)} />
                                        </label>
                                    )}
                                    <div className="flex flex-wrap gap-2">
                                        <Button
                                            type="button"
                                            variant="destructive"
                                            className="min-h-11 md:min-h-9"
                                            disabled={busy || (mfa ? !code.trim() : phrase.trim().toLowerCase() !== overview.delete_phrase)}
                                            onClick={async () => {
                                                setBusy(true);
                                                setError(null);
                                                const result = await requestMyDeletionApiV1MePrivacyDeletionPost({
                                                    body: mfa ? { code } : { phrase },
                                                });
                                                setBusy(false);
                                                if (result.error || !result.data) {
                                                    setError(detailFromError(result.error, "Could not ask for deletion."));
                                                    return;
                                                }
                                                if (result.data.card) setCard(result.data.card);
                                                setDeletionPreview(null);
                                                void load();
                                            }}
                                        >
                                            Ask to delete my data
                                        </Button>
                                        <Button type="button" variant="ghost" className="min-h-11 md:min-h-9" onClick={() => setDeletionPreview(null)}>
                                            Not now
                                        </Button>
                                    </div>
                                </div>
                            )}
                            {overview.workspace_owner && (
                                <p className="mt-3 text-xs text-muted-foreground">
                                    Deleting the whole workspace is separate, and the owner&apos;s: it is under{" "}
                                    <Link className="underline underline-offset-2" href="/settings/compliance">
                                        Compliance
                                    </Link>
                                    .
                                </p>
                            )}
                        </SettingsSection>

                        {error && (
                            <p role="alert" className="text-sm text-destructive">
                                {error}
                            </p>
                        )}

                        {overview.requests.length > 0 && (
                            <SettingsSection id="requests" title="Your requests" scope="Just you">
                                <ul className="divide-y divide-border">
                                    {overview.requests.map((request) => (
                                        <RequestRow key={request.id} request={request} onRefresh={() => void refreshRequest(request.id)} />
                                    ))}
                                </ul>
                            </SettingsSection>
                        )}
                    </div>
                )}
            </PageBody>
        </>
    );
}

export default PrivacySettings;
