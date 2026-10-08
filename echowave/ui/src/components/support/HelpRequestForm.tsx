"use client";

/**
 * Screen 28, the request: what it is about, what happened, an optional
 * file, and -- before anything is sent -- exactly what support will see.
 *
 * Opened from a failed task or reply, the request is about that one thing
 * and offers its details by default; its words are shared only if switched
 * on. One submit is one request however often it is pressed (the key is
 * made once per draft). A file that cannot be stored does not lose the
 * request: the request is sent, and the file failure is said on the ticket.
 */

import { Loader2, Paperclip, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import {
    type AffectedKind,
    attachFile,
    createTicket,
    type HelpOptions,
    loadOptions,
    newKey,
    previewShare,
    type SharePreview,
} from "@/lib/support/help";

import { SharePreviewPanel } from "./SharePreviewPanel";

const FIELD = "w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base md:text-sm";

export function HelpRequestForm({
    affectedKind = null,
    affectedId = null,
}: {
    affectedKind?: AffectedKind | null;
    affectedId?: number | null;
}) {
    const router = useRouter();
    const [options, setOptions] = useState<HelpOptions | null>(null);
    const [optionsError, setOptionsError] = useState<string | null>(null);
    const [category, setCategory] = useState(affectedKind ? "something_failed" : "");
    const [description, setDescription] = useState("");
    const [share, setShare] = useState<string[] | null>(null);
    const [preview, setPreview] = useState<SharePreview | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    const [fileError, setFileError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const [sendError, setSendError] = useState<string | null>(null);
    const key = useRef(newKey());
    const fileInput = useRef<HTMLInputElement>(null);

    useEffect(() => {
        void loadOptions().then((outcome) => {
            if (outcome.ok) setOptions(outcome.value);
            else setOptionsError(outcome.error);
        });
    }, []);

    // Only the latest preview is shown: an earlier one answering late would
    // show a share other than the one that is sent.
    const latest = useRef(0);
    const refreshPreview = useCallback(
        async (next: string[] | null) => {
            const asked = ++latest.current;
            setPreviewing(true);
            const outcome = await previewShare({ affectedKind, affectedId, share: next });
            if (asked !== latest.current) return;
            setPreviewing(false);
            if (outcome.ok) {
                setPreview(outcome.value);
                setPreviewError(null);
            } else {
                setPreviewError(outcome.error);
            }
        },
        [affectedKind, affectedId],
    );

    useEffect(() => {
        void refreshPreview(null);
    }, [refreshPreview]);

    // The person's own choice once they have made one; the defaults before.
    const included = useMemo(
        () => share ?? (preview ? preview.sections.filter((s) => s.included && !s.required).map((s) => s.key) : []),
        [share, preview],
    );

    function toggle(sectionKey: string, on: boolean) {
        const next = on ? Array.from(new Set([...included, sectionKey])) : included.filter((k) => k !== sectionKey);
        setShare(next);
        void refreshPreview(next);
    }

    function pick(chosen: File | null) {
        setFileError(null);
        if (!chosen) return setFile(null);
        if (options && !options.attachment_types.includes(chosen.type)) {
            setFileError("Attach a screenshot, a PDF or a text file.");
            return;
        }
        if (options && chosen.size > options.max_attachment_bytes) {
            setFileError("Files up to 5 MB can be attached.");
            return;
        }
        setFile(chosen);
    }

    const canSend = Boolean(category && description.trim() && preview && !previewError && !sending && !previewing);

    async function submit() {
        if (!canSend) return;
        setSending(true);
        setSendError(null);
        const outcome = await createTicket({
            category,
            description: description.trim(),
            affectedKind,
            affectedId,
            share,
            key: key.current,
        });
        if (!outcome.ok) {
            setSending(false);
            setSendError(outcome.error);
            return;
        }
        const ticketId = outcome.value.ticket.id;
        let attachment = "";
        if (file) {
            const stored = await attachFile(ticketId, file);
            if (!stored.ok) attachment = "?attachment=failed";
        }
        router.push(`/help/${ticketId}${attachment}`);
    }

    if (optionsError) return <ErrorState title="Help could not load." description={optionsError} onRetry={() => location.reload()} />;

    return (
        <form
            className="flex flex-col gap-5"
            onSubmit={(event) => {
                event.preventDefault();
                void submit();
            }}
            aria-busy={sending}
        >
            {affectedKind && affectedId && (
                <p className="rounded-[var(--radius)] border border-border bg-muted/30 px-3 py-2 text-sm" data-testid="affected">
                    About {affectedKind === "task" ? "task" : "reply"} #{affectedId}
                </p>
            )}
            <div className="flex flex-col gap-1.5">
                <label htmlFor="help-category" className="text-sm font-medium">
                    What is it about?
                </label>
                <select
                    id="help-category"
                    className={`${FIELD} min-h-11`}
                    value={category}
                    onChange={(event) => setCategory(event.target.value)}
                    disabled={sending || !options}
                >
                    <option value="" disabled>
                        Choose one
                    </option>
                    {options?.categories.map((c) => (
                        <option key={c.key} value={c.key}>
                            {c.label}
                        </option>
                    ))}
                </select>
            </div>
            <div className="flex flex-col gap-1.5">
                <label htmlFor="help-description" className="text-sm font-medium">
                    What happened?
                </label>
                <textarea
                    id="help-description"
                    rows={5}
                    className={FIELD}
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    disabled={sending}
                    placeholder="What you tried, and what you expected instead."
                />
            </div>
            <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium">Attachment (optional)</span>
                <input
                    ref={fileInput}
                    type="file"
                    className="hidden"
                    accept={options?.attachment_types.join(",")}
                    onChange={(event) => pick(event.target.files?.[0] ?? null)}
                    data-testid="help-file"
                />
                <div className="flex flex-wrap items-center gap-2">
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => fileInput.current?.click()} disabled={sending}>
                        <Paperclip aria-hidden className="h-4 w-4" />
                        {file ? "Change file" : "Add a file"}
                    </Button>
                    {file && (
                        <span className="flex min-w-0 items-center gap-1 text-sm">
                            <span className="truncate">{file.name}</span>
                            <button
                                type="button"
                                aria-label={`Remove ${file.name}`}
                                className="motion-m1 flex h-11 w-11 items-center justify-center rounded-md hover:bg-muted md:h-8 md:w-8"
                                onClick={() => setFile(null)}
                            >
                                <X aria-hidden className="h-4 w-4" />
                            </button>
                        </span>
                    )}
                </div>
                {fileError && (
                    <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                        {fileError}
                    </p>
                )}
                <p className="text-xs text-muted-foreground">Screenshots, PDFs or text files, up to 5 MB. Files are shared with support.</p>
            </div>

            {previewError ? (
                <ErrorState title="Could not show what will be shared." description={previewError} onRetry={() => void refreshPreview(share)} />
            ) : preview ? (
                <SharePreviewPanel preview={preview} onToggle={toggle} disabled={sending || previewing} />
            ) : (
                <p className="text-sm text-muted-foreground" role="status">
                    Working out what will be shared…
                </p>
            )}

            {sendError && (
                <p role="alert" className="text-sm text-[#772322] dark:text-red-300">
                    {sendError}
                </p>
            )}
            {/* The action stays reachable above the phone keyboard. */}
            <div className="sticky bottom-0 -mx-4 border-t border-border bg-background px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:static md:mx-0 md:border-0 md:p-0">
                <Button type="submit" className="motion-m1 min-h-11 w-full md:w-auto" disabled={!canSend}>
                    {sending && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                    {sending ? "Sending…" : "Send to support"}
                </Button>
            </div>
        </form>
    );
}

export default HelpRequestForm;
