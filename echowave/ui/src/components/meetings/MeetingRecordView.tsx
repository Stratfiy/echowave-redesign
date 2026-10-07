"use client";

/**
 * Screen 12: the meeting record and its actions.
 *
 * A document-like main column with Summary, Decisions and Transcript tabs,
 * and the suggested actions beside it at wide widths (320 px) or below the
 * summary on a phone. Processing, partial and failed are their own states,
 * each saying what happened and what can be done. Rename, export, delete
 * (with what happens to the tasks it made) and Return to chat sit in the
 * header, so nothing needs another screen.
 */

import { AlertTriangle, ArrowLeft, Download, Loader2, Pencil, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import {
    correctTranscriptApiV1MeetingsMeetingIdTranscriptSeqPut,
    deleteMeetingApiV1MeetingsMeetingIdDelete,
    deletionPreviewApiV1MeetingsMeetingIdDeletionGet,
    exportMeetingApiV1MeetingsMeetingIdExportGet,
    readAgainApiV1MeetingsMeetingIdReadPost,
    renameMeetingApiV1MeetingsMeetingIdPatch,
    retryMeetingApiV1MeetingsMeetingIdRetryPost,
    stopMeetingApiV1MeetingsMeetingIdStopPost,
} from "@/client/sdk.gen";
import type { DeletionPreview, MeetingRecord } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { detailFromResult } from "@/lib/apiError";
import { clock, duration, statusLine } from "@/lib/meetings/format";
import { cn } from "@/lib/utils";

import { MeetingActionRow } from "./MeetingActionRow";
import { TranscriptView } from "./TranscriptView";
import { useMeeting } from "./useMeeting";

const TONE = {
    ok: "text-[#075A39] border-[#075A39]/40 dark:text-emerald-300",
    warn: "text-[#705500] border-[#705500]/40 dark:text-amber-300",
    error: "text-[#772322] border-[#772322]/40 dark:text-red-300",
    busy: "text-foreground border-border",
};

export function processingLine(record: MeetingRecord): string {
    const parts = record.transcript.filter((p) => p.status !== "waiting");
    const done = parts.filter((p) => p.status === "done" || p.status === "failed").length;
    if (record.transcript.some((p) => p.status === "waiting")) return "Preparing the recording…";
    if (parts.length > 0 && done < parts.length) return `Transcribing: ${done} of ${parts.length} parts done`;
    return "Writing the summary…";
}

export function MeetingRecordView({ meetingId }: { meetingId: string }) {
    const router = useRouter();
    const { record, loading, error, notFound, stale, reload, accept } = useMeeting(meetingId);
    const [tab, setTab] = useState("summary");
    const [highlight, setHighlight] = useState<number | null>(null);
    const [renaming, setRenaming] = useState(false);
    const [titleDraft, setTitleDraft] = useState("");
    const [problem, setProblem] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [deleting, setDeleting] = useState<DeletionPreview | null>(null);
    const [cancelTasks, setCancelTasks] = useState(false);

    const starts = useMemo(() => {
        const map = new Map<number, number>();
        for (const part of record?.transcript ?? []) map.set(part.seq, part.start_ms);
        return map;
    }, [record?.transcript]);

    if (loading && !record) {
        return (
            <div className="mx-auto w-full max-w-[960px] px-4 py-6 md:px-6" aria-busy>
                <Skeleton className="mb-3 h-8 w-64" />
                <Skeleton className="mb-6 h-4 w-48" />
                <Skeleton className="mb-2 h-24 w-full" />
                <Skeleton className="h-24 w-full" />
            </div>
        );
    }
    if (notFound) {
        return (
            <div className="mx-auto w-full max-w-[960px] px-4 py-6 md:px-6">
                <EmptyState
                    title="This meeting is not here"
                    description="It may have been deleted, or it belongs to someone else."
                    action={
                        <Button asChild variant="outline" className="min-h-11">
                            <Link href="/meetings">Your meetings</Link>
                        </Button>
                    }
                />
            </div>
        );
    }
    if (error || !record) {
        return (
            <div className="mx-auto w-full max-w-[960px] px-4 py-6 md:px-6">
                <ErrorState title="Could not load this meeting" description={error ?? undefined} onRetry={() => void reload()} />
            </div>
        );
    }

    const run = async (name: string, call: () => Promise<{ data?: unknown; error?: unknown; response?: Response }>, fallback: string) => {
        setBusy(name);
        setProblem(null);
        const response = await call();
        setBusy(null);
        if (response.error) {
            setProblem(detailFromResult(response, fallback));
            return null;
        }
        if (response.data && typeof response.data === "object" && "revision" in (response.data as object)) {
            accept(response.data as MeetingRecord);
        }
        return response.data;
    };

    const path = { meeting_id: record.id };
    const status = statusLine(record.status);
    const back = record.origin_thread_id ? `/overview?thread=${encodeURIComponent(record.origin_thread_id)}` : "/overview";
    const corrected = record.transcript.some((p) => p.corrected);
    const failedParts = record.transcript.some((p) => p.status === "failed");

    const showSource = (seq: number) => {
        setTab("transcript");
        setHighlight(seq);
        window.setTimeout(() => setHighlight(null), 2500);
    };

    const exportIt = async () => {
        const data = await run("export", () => exportMeetingApiV1MeetingsMeetingIdExportGet({ path, parseAs: "text" }), "Could not export this meeting");
        if (typeof data !== "string") return;
        const url = URL.createObjectURL(new Blob([data], { type: "text/markdown" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = `${record.title.replace(/[^\p{L}\p{N} _-]/gu, "_").slice(0, 60) || "meeting"}.md`;
        link.click();
        URL.revokeObjectURL(url);
    };

    const openDelete = async () => {
        const data = await run("preview", () => deletionPreviewApiV1MeetingsMeetingIdDeletionGet({ path }), "Could not check what deleting would do");
        if (data) {
            setCancelTasks(false);
            setDeleting(data as DeletionPreview);
        }
    };

    const confirmDelete = async () => {
        const data = await run(
            "delete",
            () => deleteMeetingApiV1MeetingsMeetingIdDelete({ path, query: { cancel_tasks: cancelTasks } }),
            "The meeting was not deleted. Try again.",
        );
        if (data) router.push("/meetings");
    };

    const actionsList = (
        <section aria-labelledby="actions-heading" className="flex flex-col gap-3">
            <h2 id="actions-heading" className="text-base font-semibold">
                Suggested actions
            </h2>
            <p className="text-sm text-muted-foreground">
                Nothing happens until you approve each one. Approving adds a task; it never sends an email or an invitation.
            </p>
            {record.actions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                    {record.reading_status === "ready" || record.reading_status === "empty"
                        ? "No clear actions were found."
                        : "Actions appear here once the summary is written."}
                </p>
            ) : (
                <ul className="flex flex-col gap-3">
                    {record.actions.map((action) => (
                        <MeetingActionRow
                            key={action.id}
                            meetingId={record.id}
                            action={action}
                            startOf={(seq) => starts.get(seq) ?? null}
                            onRecord={accept}
                            onShowSource={showSource}
                        />
                    ))}
                </ul>
            )}
        </section>
    );

    return (
        <div className="mx-auto w-full max-w-[1280px] px-4 pb-16 pt-4 md:px-6">
            <div className="mb-3 flex flex-wrap items-center gap-2">
                <Button asChild variant="ghost" className="motion-m1 min-h-11 px-2 md:min-h-9">
                    <Link href={back}>
                        <ArrowLeft aria-hidden />
                        Return to chat
                    </Link>
                </Button>
                <Button asChild variant="ghost" className="motion-m1 min-h-11 px-2 md:min-h-9">
                    <Link href="/meetings">All meetings</Link>
                </Button>
            </div>

            <header className="flex flex-col gap-2">
                {renaming ? (
                    <form
                        className="flex flex-wrap items-end gap-2"
                        onSubmit={async (event) => {
                            event.preventDefault();
                            const ok = await run("rename", () => renameMeetingApiV1MeetingsMeetingIdPatch({ path, body: { title: titleDraft } }), "Your change was not saved. Try again.");
                            if (ok) setRenaming(false);
                        }}
                    >
                        <div className="flex min-w-0 flex-1 flex-col gap-1">
                            <Label htmlFor="rename">Name</Label>
                            <Input id="rename" className="h-11 text-base" value={titleDraft} maxLength={200} onChange={(e) => setTitleDraft(e.target.value)} />
                        </div>
                        <Button type="submit" className="motion-m1 min-h-11" disabled={!titleDraft.trim() || busy === "rename"}>
                            Save
                        </Button>
                        <Button type="button" variant="ghost" className="motion-m1 min-h-11" onClick={() => setRenaming(false)}>
                            Cancel
                        </Button>
                    </form>
                ) : (
                    <div className="flex items-start gap-2">
                        <h1 className="min-w-0 flex-1 break-words text-2xl font-semibold leading-8">{record.title}</h1>
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="motion-m1 size-11 shrink-0"
                            aria-label="Rename"
                            onClick={() => {
                                setTitleDraft(record.title);
                                setRenaming(true);
                            }}
                        >
                            <Pencil aria-hidden />
                        </Button>
                    </div>
                )}
                <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
                    <span className={cn("rounded-full border px-2 py-0.5 text-xs font-medium", TONE[status.tone])} data-testid="meeting-status">
                        {status.label}
                    </span>
                    <span>{record.source_label}</span>
                    <span aria-hidden>·</span>
                    <span>{record.created_at ? new Date(record.created_at).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }) : ""}</span>
                    {record.captured_ms > 0 && (
                        <>
                            <span aria-hidden>·</span>
                            <span>{duration(record.captured_ms)} captured</span>
                        </>
                    )}
                    {record.participants.length > 0 && (
                        <>
                            <span aria-hidden>·</span>
                            <span className="break-words">{record.participants.join(", ")}</span>
                        </>
                    )}
                </p>
                <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy === "export"} onClick={() => void exportIt()}>
                        <Download aria-hidden />
                        Export
                    </Button>
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy === "preview"} onClick={() => void openDelete()}>
                        <Trash2 aria-hidden />
                        Delete
                    </Button>
                </div>
                {stale && (
                    <p role="status" className="text-sm text-[#705500] dark:text-amber-300">
                        Could not refresh. Showing what was last loaded.
                    </p>
                )}
                {problem && (
                    <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                        {problem}
                    </p>
                )}
            </header>

            {(record.status === "recording" || record.status === "paused") && (
                <div className="mt-4 rounded-[8px] border border-[#705500]/40 p-3 text-sm" role="status">
                    <p>This meeting is still marked as recording. If the page that was recording was closed, stop it to save what was captured.</p>
                    <Button
                        type="button"
                        className="motion-m1 mt-2 min-h-11"
                        disabled={busy === "stop"}
                        onClick={() => void run("stop", () => stopMeetingApiV1MeetingsMeetingIdStopPost({ path, body: {} }), "Could not stop the meeting")}
                    >
                        Stop and save
                    </Button>
                </div>
            )}
            {record.status === "uploading" && (
                <p className="mt-4 text-sm text-muted-foreground" role="status">
                    No recording has arrived for this meeting. <Link className="underline" href="/meetings/new">Start again</Link>.
                </p>
            )}
            {record.status === "processing" && (
                <p className="motion-m2 mt-4 flex items-center gap-2 text-sm" role="status" aria-live="polite">
                    <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                    {processingLine(record)}
                </p>
            )}
            {record.status === "partial" && (
                <div className="mt-4 flex flex-wrap items-start gap-2 rounded-[8px] border border-[#705500]/40 bg-amber-50 p-3 text-sm text-[#705500] dark:bg-amber-950/30 dark:text-amber-200" role="status">
                    <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1">Partial: {record.status_reason} What was captured is below; gaps are marked in the transcript.</span>
                    {failedParts && (
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy === "retry"} onClick={() => void run("retry", () => retryMeetingApiV1MeetingsMeetingIdRetryPost({ path }), "Could not try again")}>
                            <RefreshCw aria-hidden />
                            Try again
                        </Button>
                    )}
                </div>
            )}
            {record.status === "failed" && (
                <ErrorState
                    className="mt-4 rounded-[8px] border border-border"
                    title="This meeting could not be processed"
                    description={record.status_reason ?? undefined}
                    onRetry={failedParts ? () => void run("retry", () => retryMeetingApiV1MeetingsMeetingIdRetryPost({ path }), "Could not try again") : undefined}
                    retrying={busy === "retry"}
                />
            )}

            <div className="mt-6 grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,1fr)_320px]">
                <div className="min-w-0 max-w-[760px]">
                    <Tabs value={tab} onValueChange={setTab}>
                        <TabsList className="h-auto w-full md:w-fit">
                            <TabsTrigger value="summary" className="h-11 px-3 md:h-8">Summary</TabsTrigger>
                            <TabsTrigger value="decisions" className="h-11 px-3 md:h-8">Decisions</TabsTrigger>
                            <TabsTrigger value="transcript" className="h-11 px-3 md:h-8">Transcript</TabsTrigger>
                        </TabsList>
                        <TabsContent value="summary" className="pt-3">
                            {record.summary.length > 0 && (
                                <ul className="flex list-disc flex-col gap-2 pl-5 text-base leading-[1.6]">
                                    {record.summary.map((line, index) => (
                                        <li key={index} className="break-words">{line}</li>
                                    ))}
                                </ul>
                            )}
                            {record.reading_note && (
                                <p className={cn("mt-3 text-sm", record.reading_status === "ready" ? "text-muted-foreground" : "text-[#705500] dark:text-amber-300")} role="status">
                                    {record.reading_note}
                                </p>
                            )}
                            {record.summary.length === 0 && !record.reading_note && (
                                <p className="text-sm text-muted-foreground">
                                    {record.status === "processing" || record.reading_status === "reading" ? "The summary is being written." : "No summary yet."}
                                </p>
                            )}
                            {(corrected || record.reading_status === "failed") && (record.status === "ready" || record.status === "partial") && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    className="motion-m1 mt-3 min-h-11 md:min-h-9"
                                    disabled={busy === "read"}
                                    onClick={() => void run("read", () => readAgainApiV1MeetingsMeetingIdReadPost({ path }), "Could not write the summary again")}
                                >
                                    {busy === "read" ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <RefreshCw aria-hidden />}
                                    Update summary from the transcript
                                </Button>
                            )}
                        </TabsContent>
                        <TabsContent value="decisions" className="pt-3">
                            {record.decisions.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No decisions were found.</p>
                            ) : (
                                <ul className="flex flex-col gap-3">
                                    {record.decisions.map((decision) => (
                                        <li key={decision.id} className="rounded-[8px] border border-border p-3">
                                            <p className="break-words font-medium">{decision.text}</p>
                                            {decision.excerpt && (
                                                <button
                                                    type="button"
                                                    className="motion-m1 mt-1 min-h-11 text-left text-sm text-muted-foreground underline-offset-4 hover:underline md:min-h-0"
                                                    onClick={() => decision.segment_seq != null && showSource(decision.segment_seq)}
                                                >
                                                    “{decision.excerpt}”
                                                    {decision.segment_seq != null && starts.has(decision.segment_seq) && ` · ${clock(starts.get(decision.segment_seq) ?? 0)}`}
                                                </button>
                                            )}
                                            {!decision.source_found && (
                                                <p className="mt-1 text-sm text-[#705500] dark:text-amber-300">These words were not found in the transcript.</p>
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </TabsContent>
                        <TabsContent value="transcript" className="pt-3">
                            <TranscriptView
                                parts={record.transcript}
                                breaks={record.breaks}
                                highlightSeq={highlight}
                                onCorrect={
                                    record.status === "ready" || record.status === "partial"
                                        ? async (seq, text) => {
                                              const response = await correctTranscriptApiV1MeetingsMeetingIdTranscriptSeqPut({ path: { meeting_id: record.id, seq }, body: { text } });
                                              if (response.error) return detailFromResult(response, "Your change was not saved. Try again.");
                                              accept(response.data as MeetingRecord);
                                              return null;
                                          }
                                        : undefined
                                }
                                onRetry={failedParts ? () => void run("retry", () => retryMeetingApiV1MeetingsMeetingIdRetryPost({ path }), "Could not try again") : undefined}
                            />
                        </TabsContent>
                    </Tabs>
                </div>
                {/* One list: below the tabs on a phone (under the summary),
                    beside them in a 320 px column at wide widths. */}
                <aside>{actionsList}</aside>
            </div>

            <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete this meeting?</DialogTitle>
                        <DialogDescription>The transcript, summary and suggestions are deleted for good.</DialogDescription>
                    </DialogHeader>
                    {deleting && (
                        <div className="flex flex-col gap-3 text-sm">
                            {deleting.linked_tasks.length > 0 ? (
                                <div>
                                    <p className="font-medium">Tasks it made</p>
                                    <ul className="mt-1 list-disc pl-5">
                                        {deleting.linked_tasks.map((task) => (
                                            <li key={task.task_id} className="break-words">
                                                {task.title} <span className="text-muted-foreground">({task.status})</span>
                                            </li>
                                        ))}
                                    </ul>
                                    <div className="mt-2 flex items-start gap-2">
                                        <Checkbox id="cancel-tasks" className="mt-0.5 size-5" checked={cancelTasks} onCheckedChange={(v) => setCancelTasks(v === true)} />
                                        <Label htmlFor="cancel-tasks" className="text-sm font-normal leading-5">
                                            Also cancel the tasks nobody has started. Otherwise they stay on the task board.
                                        </Label>
                                    </div>
                                </div>
                            ) : (
                                <p>It made no tasks.</p>
                            )}
                            {deleting.waiting_cards > 0 && <p>{deleting.cards_note}</p>}
                            <p className="text-muted-foreground">{deleting.memory}</p>
                        </div>
                    )}
                    <DialogFooter>
                        <Button type="button" variant="outline" className="min-h-11" onClick={() => setDeleting(null)}>
                            Keep it
                        </Button>
                        <Button type="button" variant="destructive" className="min-h-11" disabled={busy === "delete"} onClick={() => void confirmDelete()}>
                            {busy === "delete" ? "Deleting…" : "Delete meeting"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

export default MeetingRecordView;
