"use client";

/**
 * "Save as report" under one of Decibyl's replies (`research_reports`).
 * Saves the reply as it was shown, with every link in it as a source; the
 * saved report stays attached to this conversation and opens at its own
 * address. A failure says so; it never reads as saved.
 */

import { FileText, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { saveReplyAsReportApiV1HelpersReportsFromReplyPost } from "@/client/sdk.gen";
import { detailFromResult } from "@/lib/apiError";

const LINK =
    "motion-m1 inline-flex min-h-11 items-center gap-1 rounded-md px-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-60 md:min-h-6";

export function SaveReportButton({ eventId }: { eventId: number }) {
    const [state, setState] = useState<{ kind: "idle" | "saving" } | { kind: "saved"; uuid: string } | { kind: "failed"; message: string }>({
        kind: "idle",
    });
    if (state.kind === "saved") {
        return (
            <Link href={`/saved-reports/${state.uuid}`} className={LINK} data-testid="saved-report-link">
                <FileText aria-hidden className="h-3.5 w-3.5" />
                Saved · Open report
            </Link>
        );
    }
    return (
        <>
            <button
                type="button"
                className={LINK}
                disabled={state.kind === "saving"}
                data-testid="save-report"
                onClick={async () => {
                    setState({ kind: "saving" });
                    const response = await saveReplyAsReportApiV1HelpersReportsFromReplyPost({ body: { event_id: eventId } });
                    if (response.error || !response.data) {
                        setState({ kind: "failed", message: detailFromResult(response, "Not saved. Try again.") });
                        return;
                    }
                    setState({ kind: "saved", uuid: response.data.uuid });
                }}
            >
                {state.kind === "saving" ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <FileText aria-hidden className="h-3.5 w-3.5" />}
                {state.kind === "saving" ? "Saving…" : "Save as report"}
            </button>
            {state.kind === "failed" && (
                <span className="text-xs text-destructive" role="status">
                    {state.message}
                </span>
            )}
        </>
    );
}
