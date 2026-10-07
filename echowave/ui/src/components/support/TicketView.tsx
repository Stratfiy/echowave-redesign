"use client";

/**
 * Screen 28, the ticket: a status header, what was shared, the thread, the
 * state of anything support is doing for the person, and a reply box that
 * stays above the phone keyboard.
 *
 * A reply that fails keeps its words and its key, so trying again cannot
 * send it twice. A file that fails keeps the reply. Resolve and Reopen are
 * on the ticket itself; reopening keeps the whole history.
 */

import { ChevronDown, Loader2, Paperclip, RotateCcw, Send } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import {
    ACTION_STATE_LABEL,
    attachFile,
    loadTicket,
    newKey,
    reopenTicket,
    resolveTicket,
    sendReply,
    STATUS_LABEL,
    type TicketDetail,
} from "@/lib/support/help";
import { cn } from "@/lib/utils";

import { TicketThread } from "./TicketThread";

const FIELD = "w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base md:text-sm";

function StatusPill({ status }: { status: string }) {
    return (
        <span
            className={cn(
                "motion-m2 inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium",
                status === "resolved" ? "border-[#075A39]/30 text-[#075A39] dark:text-emerald-300" : "border-border",
                status === "waiting_on_customer" && "border-[#705500]/30 text-[#705500] dark:text-amber-300",
            )}
            data-testid="ticket-status"
        >
            {STATUS_LABEL[status] ?? status}
        </span>
    );
}

export function TicketView({ ticketId, attachmentFailed = false }: { ticketId: number; attachmentFailed?: boolean }) {
    const [ticket, setTicket] = useState<TicketDetail | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [draft, setDraft] = useState("");
    const [sending, setSending] = useState(false);
    const [sendError, setSendError] = useState<string | null>(null);
    const [fileNote, setFileNote] = useState<string | null>(
        attachmentFailed ? "Your request was sent, but the file could not be stored. Try adding it again." : null,
    );
    const [uploading, setUploading] = useState(false);
    const [changing, setChanging] = useState(false);
    const [showShared, setShowShared] = useState(false);
    const replyKey = useRef(newKey());
    const fileInput = useRef<HTMLInputElement>(null);

    const load = useCallback(async () => {
        const outcome = await loadTicket(ticketId);
        if (outcome.ok) {
            setTicket(outcome.value);
            setError(null);
        } else {
            setError(outcome.error);
        }
    }, [ticketId]);

    useEffect(() => {
        void load();
    }, [load]);

    async function send() {
        const body = draft.trim();
        if (!body || sending) return;
        setSending(true);
        setSendError(null);
        const outcome = await sendReply(ticketId, body, replyKey.current);
        setSending(false);
        if (!outcome.ok) {
            // The words and the key are kept: Send again is the same message.
            setSendError(outcome.error);
            return;
        }
        setDraft("");
        replyKey.current = newKey();
        await load();
    }

    async function upload(file: File | null) {
        if (!file) return;
        setUploading(true);
        const outcome = await attachFile(ticketId, file);
        setUploading(false);
        if (fileInput.current) fileInput.current.value = "";
        if (!outcome.ok) {
            setFileNote(`${outcome.error} Your message is kept.`);
            return;
        }
        setFileNote(null);
        await load();
    }

    async function settle(action: "resolve" | "reopen") {
        setChanging(true);
        const outcome = action === "resolve" ? await resolveTicket(ticketId) : await reopenTicket(ticketId, draft.trim() || null);
        setChanging(false);
        if (!outcome.ok) {
            setSendError(outcome.error);
            return;
        }
        if (action === "reopen") setDraft("");
        setTicket(outcome.value);
    }

    if (error && !ticket) return <ErrorState title="This request could not load." description={error} onRetry={() => void load()} />;
    if (!ticket)
        return (
            <p role="status" className="py-10 text-center text-sm text-muted-foreground">
                Loading your request…
            </p>
        );

    const resolved = ticket.status === "resolved";
    return (
        <div className="flex min-h-[60vh] flex-col gap-4">
            <header className="flex flex-col gap-2 border-b border-border pb-3">
                <Link href="/help" className="min-h-11 text-sm text-muted-foreground underline-offset-2 hover:underline md:min-h-0">
                    ← All requests
                </Link>
                <div className="flex flex-wrap items-center gap-2">
                    <h1 className="min-w-0 flex-1 break-words text-xl font-semibold">{ticket.subject}</h1>
                    <StatusPill status={ticket.status} />
                </div>
                <p className="text-xs text-muted-foreground">
                    {ticket.category_label} · #{ticket.id}
                    {ticket.affected && (
                        <>
                            {" "}
                            · about {ticket.affected.kind} #{ticket.affected.id}
                        </>
                    )}
                </p>
                <button
                    type="button"
                    className="motion-m1 flex min-h-11 items-center gap-1 self-start text-sm underline-offset-2 hover:underline md:min-h-0"
                    aria-expanded={showShared}
                    onClick={() => setShowShared((v) => !v)}
                >
                    <ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform", showShared && "rotate-180")} />
                    What you shared with support
                </button>
                {showShared && (
                    <div className="motion-m3-enter rounded-[var(--radius)] border border-border p-3 text-sm" data-testid="shared-snapshot">
                        {(ticket.shared.sections ?? []).map((section) => (
                            <div key={section.key} className="mb-2 last:mb-0">
                                <p className="font-medium">{section.label}</p>
                                <dl className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3">
                                    {section.fields.map((field) => (
                                        <div key={field.label} className="contents">
                                            <dt className="text-muted-foreground">{field.label}</dt>
                                            <dd className="min-w-0 whitespace-pre-wrap break-words">{String(field.value ?? "—")}</dd>
                                        </div>
                                    ))}
                                </dl>
                            </div>
                        ))}
                        {(ticket.shared.left_out?.length ?? 0) > 0 && (
                            <p className="mt-2 text-xs text-muted-foreground">Not shared: {ticket.shared.left_out?.join(", ").replace(/_/g, " ")}.</p>
                        )}
                    </div>
                )}
            </header>

            {ticket.actions.length > 0 && (
                <section aria-label="What support is doing" className="rounded-[var(--radius)] border border-border p-3">
                    <h2 className="mb-1 text-sm font-semibold">What support is doing</h2>
                    <ul className="flex flex-col gap-1 text-sm">
                        {ticket.actions.map((action) => (
                            <li key={action.id} className="flex flex-wrap justify-between gap-2" data-testid="customer-action">
                                <span className="min-w-0 break-words">{action.summary}</span>
                                <span className="motion-m2 text-muted-foreground">{ACTION_STATE_LABEL[action.state] ?? action.state}</span>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <TicketThread messages={ticket.messages} viewer="customer" className="flex-1" />

            {ticket.attachments.length > 0 && (
                <ul aria-label="Files" className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    {ticket.attachments.map((a) => (
                        <li key={a.id} className="flex items-center gap-1 rounded-full border border-border px-2 py-1">
                            <Paperclip aria-hidden className="h-3 w-3" /> {a.file_name}
                        </li>
                    ))}
                </ul>
            )}

            {fileNote && (
                <p role="alert" className="text-sm text-[#705500] dark:text-amber-300" data-testid="attachment-failed">
                    {fileNote}
                </p>
            )}
            {sendError && (
                <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                    {sendError}
                </p>
            )}

            <div className="sticky bottom-0 -mx-4 flex flex-col gap-2 border-t border-border bg-background px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:mx-0 md:px-0">
                <label htmlFor="help-reply" className="sr-only">
                    {resolved ? "Why are you reopening it? (optional)" : "Reply to support"}
                </label>
                <textarea
                    id="help-reply"
                    rows={2}
                    className={FIELD}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder={resolved ? "Still a problem? Say what happened, then Reopen." : "Reply to support"}
                    disabled={sending || changing}
                />
                <input ref={fileInput} type="file" className="hidden" onChange={(event) => void upload(event.target.files?.[0] ?? null)} />
                <div className="flex flex-wrap items-center gap-2">
                    {resolved ? (
                        <Button type="button" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void settle("reopen")} disabled={changing}>
                            <RotateCcw aria-hidden className="h-4 w-4" /> Reopen
                        </Button>
                    ) : (
                        <>
                            <Button type="button" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void send()} disabled={!draft.trim() || sending}>
                                {sending ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <Send aria-hidden className="h-4 w-4" />}
                                {sending ? "Sending…" : sendError ? "Send again" : "Send"}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                className="motion-m1 min-h-11 md:min-h-9"
                                onClick={() => fileInput.current?.click()}
                                disabled={uploading}
                            >
                                {uploading ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <Paperclip aria-hidden className="h-4 w-4" />}
                                {uploading ? "Adding…" : "Add a file"}
                            </Button>
                            <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:ml-auto md:min-h-9" onClick={() => void settle("resolve")} disabled={changing}>
                                Mark resolved
                            </Button>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}

export default TicketView;
