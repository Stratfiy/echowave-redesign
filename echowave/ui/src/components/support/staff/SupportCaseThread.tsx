"use client";

/**
 * Screen 32, the case: who and what, the controls (assignee, severity,
 * status, incident), the customer thread, staff internal notes on their own
 * surface, and one composer with two explicitly labelled modes -- Reply to
 * the customer, or an Internal note only staff see.
 *
 * Unsent text is kept per mode and survives a failed send; the key stays
 * with the draft, so "Send again" cannot send twice. Two staff changing the
 * case at once: the second is told what it is now, not silently overwritten.
 */

import { ArrowLeft, Loader2, Lock, PanelRight, Send } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { TicketThread } from "@/components/support/TicketThread";
import { Button } from "@/components/ui/button";
import { newKey } from "@/lib/support/help";
import {
    CASE_STATUS_LABEL,
    CASE_STATUSES,
    noteOnCase,
    replyToCase,
    SEVERITIES,
    type StaffMember,
    type SupportCase,
    updateCase,
} from "@/lib/support/staff";
import { cn } from "@/lib/utils";

const SELECT = "min-h-11 rounded-[var(--radius-control)] border border-input bg-background px-2 text-base md:min-h-9 md:text-sm";
type Mode = "reply" | "note";

export function SupportCaseThread({
    supportCase,
    staff,
    me,
    onChanged,
    onOpenContext,
    className,
}: {
    supportCase: SupportCase;
    staff: StaffMember[];
    me: number | null;
    onChanged: () => Promise<void> | void;
    /** Opens the customer context where it is not beside the thread. */
    onOpenContext?: () => void;
    className?: string;
}) {
    const [mode, setMode] = useState<Mode>("reply");
    const [drafts, setDrafts] = useState<Record<Mode, string>>({ reply: "", note: "" });
    const keys = useRef<Record<Mode, string>>({ reply: newKey(), note: newKey() });
    const [thenStatus, setThenStatus] = useState<string>("waiting_on_customer");
    const [sending, setSending] = useState(false);
    const [sendError, setSendError] = useState<string | null>(null);
    const [changeError, setChangeError] = useState<string | null>(null);
    const [changing, setChanging] = useState(false);
    const [incident, setIncident] = useState(supportCase.linked_incident ?? "");

    async function change(changes: Parameters<typeof updateCase>[2]) {
        setChanging(true);
        setChangeError(null);
        const outcome = await updateCase(supportCase.id, supportCase.version, changes);
        setChanging(false);
        if (!outcome.ok) {
            const current = (outcome.detail as { current?: { assignee_user_id?: number | null; status?: string } } | undefined)?.current;
            setChangeError(
                outcome.status === 409 && current
                    ? `Someone changed this case first. It is now ${CASE_STATUS_LABEL[current.status ?? ""] ?? current.status}, ${
                          current.assignee_user_id ? `assigned to ${name(current.assignee_user_id)}` : "unassigned"
                      }.`
                    : outcome.error,
            );
        }
        await onChanged();
    }

    function name(id: number): string {
        return staff.find((s) => s.id === id)?.email ?? `Staff #${id}`;
    }

    async function send() {
        const body = drafts[mode].trim();
        if (!body || sending) return;
        setSending(true);
        setSendError(null);
        const outcome =
            mode === "reply"
                ? await replyToCase(supportCase.id, body, keys.current.reply, thenStatus === "leave" ? null : thenStatus)
                : await noteOnCase(supportCase.id, body, keys.current.note);
        setSending(false);
        if (!outcome.ok) {
            setSendError(outcome.error);
            return;
        }
        setDrafts((d) => ({ ...d, [mode]: "" }));
        keys.current[mode] = newKey();
        await onChanged();
    }

    return (
        <section aria-label="Case" className={cn("flex min-h-0 flex-col", className)}>
            <header className="flex flex-col gap-2 border-b border-border p-3">
                <div className="flex items-start gap-2">
                    <Link
                        href="/superadmin/support"
                        className="motion-m1 flex h-11 w-11 shrink-0 items-center justify-center rounded-md hover:bg-muted lg:hidden"
                        aria-label="Back to the queue"
                    >
                        <ArrowLeft aria-hidden className="h-4 w-4" />
                    </Link>
                    <div className="min-w-0 flex-1">
                        <h1 className="break-words text-lg font-semibold">{supportCase.subject}</h1>
                        <p className="text-xs text-muted-foreground">
                            #{supportCase.id} · {supportCase.category_label} · {supportCase.requester.email} · {supportCase.workspace.name}
                        </p>
                    </div>
                    {onOpenContext && (
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 xl:hidden" onClick={onOpenContext}>
                            <PanelRight aria-hidden className="h-4 w-4" /> Context
                        </Button>
                    )}
                </div>
                <div className="flex flex-wrap items-center gap-2" aria-busy={changing}>
                    <label className="sr-only" htmlFor="case-assignee">
                        Assignee
                    </label>
                    <select
                        id="case-assignee"
                        className={SELECT}
                        value={supportCase.assignee_user_id ?? ""}
                        disabled={changing}
                        onChange={(e) => void change({ assignee_user_id: e.target.value ? Number(e.target.value) : null })}
                    >
                        <option value="">Unassigned</option>
                        {staff.map((s) => (
                            <option key={s.id} value={s.id}>
                                {s.id === me ? `Me (${s.email})` : s.email}
                            </option>
                        ))}
                    </select>
                    <label className="sr-only" htmlFor="case-severity">
                        Severity
                    </label>
                    <select id="case-severity" className={SELECT} value={supportCase.severity} disabled={changing} onChange={(e) => void change({ severity: e.target.value })}>
                        {SEVERITIES.map((s) => (
                            <option key={s} value={s}>
                                {s}
                            </option>
                        ))}
                    </select>
                    <label className="sr-only" htmlFor="case-status">
                        Status
                    </label>
                    <select id="case-status" className={SELECT} value={supportCase.status} disabled={changing} onChange={(e) => void change({ status: e.target.value })}>
                        {CASE_STATUSES.map((s) => (
                            <option key={s} value={s}>
                                {CASE_STATUS_LABEL[s]}
                            </option>
                        ))}
                    </select>
                    <form
                        className="flex items-center gap-1"
                        onSubmit={(e) => {
                            e.preventDefault();
                            void change({ linked_incident: incident || null });
                        }}
                    >
                        <label className="sr-only" htmlFor="case-incident">
                            Linked incident
                        </label>
                        <input
                            id="case-incident"
                            className={cn(SELECT, "w-28")}
                            placeholder="Incident"
                            value={incident}
                            onChange={(e) => setIncident(e.target.value)}
                        />
                        <Button type="submit" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={changing || incident === (supportCase.linked_incident ?? "")}>
                            Link
                        </Button>
                    </form>
                </div>
                {changeError && (
                    <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                        {changeError}
                    </p>
                )}
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <TicketThread messages={supportCase.messages} viewer="staff" />
                {supportCase.notes.length > 0 && (
                    <section aria-label="Internal notes" className="mt-4 rounded-[var(--radius)] border border-dashed border-border bg-muted/30 p-3">
                        <h2 className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            <Lock aria-hidden className="h-3.5 w-3.5" /> Internal notes · only staff see these
                        </h2>
                        <ol className="flex flex-col gap-2">
                            {supportCase.notes.map((note) => (
                                <li key={note.id} className="text-sm" data-testid="internal-note">
                                    <p className="text-xs text-muted-foreground">{note.author}</p>
                                    <p className="whitespace-pre-wrap break-words">{note.body}</p>
                                </li>
                            ))}
                        </ol>
                    </section>
                )}
            </div>

            <div className="sticky bottom-0 border-t border-border bg-background p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                <div role="tablist" aria-label="Write" className="mb-2 flex gap-1">
                    {(
                        [
                            ["reply", "Reply to customer"],
                            ["note", "Internal note"],
                        ] as const
                    ).map(([value, label]) => (
                        <button
                            key={value}
                            type="button"
                            role="tab"
                            aria-selected={mode === value}
                            className={cn(
                                "motion-m1 min-h-11 rounded-md px-3 text-sm md:min-h-8",
                                mode === value ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted/50",
                            )}
                            onClick={() => setMode(value)}
                        >
                            {label}
                            {drafts[value] && mode !== value ? " •" : ""}
                        </button>
                    ))}
                </div>
                <label htmlFor="case-composer" className="sr-only">
                    {mode === "reply" ? "Reply to customer" : "Internal note"}
                </label>
                <textarea
                    id="case-composer"
                    rows={3}
                    value={drafts[mode]}
                    onChange={(e) => setDrafts((d) => ({ ...d, [mode]: e.target.value }))}
                    placeholder={mode === "reply" ? "The customer reads this." : "Only staff see this. Never sent to the customer."}
                    className={cn(
                        "w-full rounded-[var(--radius-control)] border px-3 py-2 text-base md:text-sm",
                        mode === "note" ? "border-dashed border-border bg-muted/30" : "border-input bg-background",
                    )}
                    data-mode={mode}
                />
                {sendError && (
                    <p role="alert" className="mt-1 text-sm text-[#772322] dark:text-red-300">
                        {sendError}
                    </p>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                    {mode === "reply" && (
                        <>
                            <label htmlFor="case-then" className="text-xs text-muted-foreground">
                                Then
                            </label>
                            <select id="case-then" className={SELECT} value={thenStatus} onChange={(e) => setThenStatus(e.target.value)}>
                                <option value="waiting_on_customer">Wait on customer</option>
                                <option value="in_progress">Keep in progress</option>
                                <option value="resolved">Resolve</option>
                                <option value="leave">Leave status</option>
                            </select>
                        </>
                    )}
                    <Button type="button" className="motion-m1 ml-auto min-h-11 md:min-h-9" disabled={!drafts[mode].trim() || sending} onClick={() => void send()}>
                        {sending ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : mode === "reply" ? <Send aria-hidden className="h-4 w-4" /> : <Lock aria-hidden className="h-4 w-4" />}
                        {sending ? "Saving…" : mode === "reply" ? (sendError ? "Send again" : "Send reply") : sendError ? "Save again" : "Save note"}
                    </Button>
                </div>
            </div>
        </section>
    );
}

export default SupportCaseThread;
