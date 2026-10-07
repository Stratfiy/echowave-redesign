"use client";

/**
 * "2 of 3 sources checked", opening to which ones and why one was not
 * (design suggestion: make source coverage visible). A failed or
 * unavailable source is listed, never dropped: a list of only what worked
 * hides what was missing.
 */

import { CheckCircle2, ChevronDown, CircleSlash, FileText, XCircle } from "lucide-react";
import { useId, useState } from "react";

import type { SourceRead } from "@/lib/shell/taskState";
import { cn } from "@/lib/utils";

export function coverageLine(sources: readonly SourceRead[]): string {
    if (sources.length === 0) return "No sources checked";
    const read = sources.filter((s) => s.status === "read").length;
    return `${read} of ${sources.length} sources checked`;
}

export function SourceCoverage({
    sources,
    refreshedAt,
    defaultOpen = false,
    className,
}: {
    sources: readonly SourceRead[];
    /** ISO time of the last successful refresh, shown when given. */
    refreshedAt?: string | null;
    defaultOpen?: boolean;
    className?: string;
}) {
    const [open, setOpen] = useState(defaultOpen);
    const listId = useId();
    const incomplete = sources.some((s) => s.status !== "read");
    return (
        <div className={cn("text-sm", className)} data-testid="source-coverage">
            <button
                type="button"
                aria-expanded={open}
                aria-controls={listId}
                onClick={() => setOpen((was) => !was)}
                className="motion-m1 inline-flex min-h-11 items-center gap-1.5 rounded-md px-1 text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 md:min-h-8"
            >
                {incomplete ? (
                    <CircleSlash aria-hidden className="h-4 w-4 text-[#705500] dark:text-amber-300" />
                ) : (
                    <CheckCircle2 aria-hidden className="h-4 w-4 text-[#075A39] dark:text-emerald-300" />
                )}
                <span>{coverageLine(sources)}</span>
                <ChevronDown aria-hidden className={cn("motion-m2 h-3.5 w-3.5", open && "rotate-180")} />
            </button>
            {refreshedAt && (
                <span className="ml-2 text-xs text-muted-foreground">
                    Last refreshed <time dateTime={refreshedAt}>{new Date(refreshedAt).toLocaleString()}</time>
                </span>
            )}
            <ul id={listId} hidden={!open} className="motion-m2-enter mt-1 flex flex-col gap-2 pl-1">
                {sources.map((source) => (
                    <li key={`${source.kind}-${source.label}`} className="flex items-start gap-2">
                        {source.status === "read" ? (
                            <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-[#075A39] dark:text-emerald-300" />
                        ) : source.status === "failed" ? (
                            <XCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-[#772322] dark:text-red-300" />
                        ) : (
                            <CircleSlash aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        )}
                        <div className="min-w-0">
                            <p className="break-words">
                                <span className="font-medium">{source.label}</span>
                                <span className="text-muted-foreground">
                                    {" "}
                                    · {source.status === "read" ? "checked" : source.status === "failed" ? "failed" : "not checked"}
                                    {source.detail ? ` · ${source.detail}` : ""}
                                </span>
                            </p>
                            {source.documents && source.documents.length > 0 && (
                                <ul className="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">
                                    {source.documents.map((doc) => (
                                        <li key={doc} className="flex items-center gap-1 break-all">
                                            <FileText aria-hidden className="h-3 w-3 shrink-0" />
                                            {doc}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </li>
                ))}
            </ul>
        </div>
    );
}

export default SourceCoverage;
