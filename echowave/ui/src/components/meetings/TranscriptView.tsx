"use client";

/**
 * The transcript, part by part, with every pause and gap where it happened.
 *
 * A gap is shown, never skipped (screen 11: "A source failure identifies the
 * missing interval"); a part that could not be transcribed says why and
 * offers to try again. In the saved record a part can be corrected in place
 * (screen 12), with the original kept beside it.
 */

import { AlertTriangle, Loader2, Pause, Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { MeetingBreak, TranscriptPart } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { clock, duration } from "@/lib/meetings/format";
import { cn } from "@/lib/utils";

type Row =
    | { kind: "part"; at: number; part: TranscriptPart }
    | { kind: "break"; at: number; item: MeetingBreak; index: number };

/** Parts and breaks in capture order; a break sorts before a part that
 *  starts at the same moment, since it happened before that part began. */
export function interleave(parts: TranscriptPart[], breaks: MeetingBreak[]): Row[] {
    const rows: Row[] = [
        ...parts.map((part) => ({ kind: "part" as const, at: part.start_ms, part })),
        ...breaks.map((item, index) => ({ kind: "break" as const, at: item.at_ms, item, index })),
    ];
    return rows.sort((a, b) => a.at - b.at || (a.kind === "break" ? -1 : 1) - (b.kind === "break" ? -1 : 1));
}

function BreakRow({ item }: { item: MeetingBreak }) {
    const gap = item.kind === "gap";
    return (
        <li
            className={cn(
                "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
                gap
                    ? "border-[#705500]/40 bg-amber-50 text-[#705500] dark:bg-amber-950/30 dark:text-amber-200"
                    : "border-border bg-muted/40 text-muted-foreground",
            )}
            data-testid={gap ? "transcript-gap" : "transcript-pause"}
        >
            {gap ? <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> : <Pause aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />}
            <span>
                <span className="font-mono text-xs">{clock(item.at_ms)}</span>{" "}
                {gap ? `Gap: ${item.reason_label}` : "Paused"}
                {item.duration_ms != null && item.duration_ms > 0 && ` (${duration(item.duration_ms)})`}
                {gap && ". Nothing was captured here."}
            </span>
        </li>
    );
}

export function TranscriptView({
    parts,
    breaks,
    highlightSeq,
    onCorrect,
    onRetry,
    live = false,
}: {
    parts: TranscriptPart[];
    breaks: MeetingBreak[];
    /** A part to show briefly, when a source excerpt was followed here. */
    highlightSeq?: number | null;
    onCorrect?: (seq: number, text: string) => Promise<string | null>;
    onRetry?: () => void;
    live?: boolean;
}) {
    const [editing, setEditing] = useState<number | null>(null);
    const [draft, setDraft] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const highlighted = useRef<HTMLLIElement | null>(null);

    useEffect(() => {
        // Once per jump, never a repeated scroll (screen 12).
        if (highlightSeq != null) highlighted.current?.scrollIntoView({ block: "center" });
    }, [highlightSeq]);

    const rows = interleave(parts, breaks);
    if (rows.length === 0) {
        return (
            <p className="text-sm text-muted-foreground">
                {live ? "Words appear here a few seconds after they are spoken." : "There is no transcript."}
            </p>
        );
    }

    const save = async (seq: number) => {
        if (!onCorrect) return;
        setSaving(true);
        setSaveError(null);
        const problem = await onCorrect(seq, draft);
        setSaving(false);
        if (problem) {
            setSaveError(problem);
            return;
        }
        setEditing(null);
    };

    return (
        <ol className="flex flex-col gap-2" aria-live={live ? "polite" : undefined} aria-label="Transcript">
            {rows.map((row) => {
                if (row.kind === "break") return <BreakRow key={`b-${row.index}`} item={row.item} />;
                const { part } = row;
                const lit = highlightSeq === part.seq;
                return (
                    <li
                        key={`p-${part.seq}`}
                        id={`part-${part.seq}`}
                        ref={lit ? highlighted : undefined}
                        className={cn(
                            "motion-m2 rounded-md px-3 py-2",
                            lit && "bg-[#245B9A]/10 ring-2 ring-[#245B9A]/40",
                        )}
                        data-testid="transcript-part"
                    >
                        <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                            <span className="font-mono">{clock(part.start_ms)}</span>
                            {part.corrected && <span>Corrected</span>}
                        </div>
                        {part.status === "done" && editing !== part.seq && (
                            <div className="flex items-start gap-2">
                                <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-base leading-[1.6]">
                                    {part.text || <span className="text-muted-foreground">(silence)</span>}
                                </p>
                                {onCorrect && (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="motion-m1 size-11 shrink-0 md:size-9"
                                        aria-label={`Correct the part at ${clock(part.start_ms)}`}
                                        onClick={() => {
                                            setEditing(part.seq);
                                            setDraft(part.text);
                                            setSaveError(null);
                                        }}
                                    >
                                        <Pencil aria-hidden />
                                    </Button>
                                )}
                            </div>
                        )}
                        {part.status === "done" && editing === part.seq && (
                            <div className="flex flex-col gap-2">
                                <Textarea
                                    value={draft}
                                    onChange={(event) => setDraft(event.target.value)}
                                    className="min-h-24 text-base"
                                    aria-label="Corrected words"
                                />
                                {part.original_text && (
                                    <p className="text-xs text-muted-foreground">Originally heard: {part.original_text}</p>
                                )}
                                {saveError && <p role="alert" className="text-sm text-[#772322] dark:text-red-300">{saveError}</p>}
                                <div className="flex flex-wrap gap-2">
                                    <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={saving} onClick={() => void save(part.seq)}>
                                        {saving ? "Saving…" : "Save correction"}
                                    </Button>
                                    <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" disabled={saving} onClick={() => setEditing(null)}>
                                        Cancel
                                    </Button>
                                </div>
                            </div>
                        )}
                        {(part.status === "pending" || part.status === "transcribing" || part.status === "waiting") && (
                            <p className="flex items-center gap-2 text-sm text-muted-foreground">
                                <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                                Transcribing…
                            </p>
                        )}
                        {part.status === "failed" && (
                            <div className="flex flex-wrap items-center gap-2 text-sm text-[#772322] dark:text-red-300" role="status">
                                <AlertTriangle aria-hidden className="h-4 w-4" />
                                <span>Not transcribed: {part.error || "this part could not be heard"}</span>
                                {onRetry && (
                                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={onRetry}>
                                        Try again
                                    </Button>
                                )}
                            </div>
                        )}
                    </li>
                );
            })}
        </ol>
    );
}

export default TranscriptView;
