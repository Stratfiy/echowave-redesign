"use client";

/**
 * A huddle as the thread keeps it: who said what, compact. One row per
 * huddle (api/services/huddle/record.py), rewritten while it runs, so the
 * thread has the text of a conversation that happened out loud. The cards
 * it proposed are their own rows beside it.
 */

import { Headphones } from "lucide-react";
import { useState } from "react";

import type { TimelineEvent } from "@/client/types.gen";
import { Button } from "@/components/ui/button";

type Line = { who?: string; text?: string; interrupted?: boolean };

/** Lines shown before "Show all". */
export const HUDDLE_PREVIEW_LINES = 4;

export function huddleLines(event: Pick<TimelineEvent, "payload">): Line[] {
    const turns = (event.payload as { turns?: unknown } | null | undefined)?.turns;
    return Array.isArray(turns) ? (turns.filter((t) => t && typeof t === "object") as Line[]) : [];
}

export function HuddleEvent({
    event,
    agentName,
    when,
}: {
    event: TimelineEvent;
    agentName: string;
    when: string;
}) {
    const [open, setOpen] = useState(false);
    const lines = huddleLines(event);
    const payload = (event.payload ?? {}) as { cards?: unknown[] };
    const cards = Array.isArray(payload.cards) ? payload.cards.length : 0;
    const shown = open ? lines : lines.slice(-HUDDLE_PREVIEW_LINES);
    const hidden = lines.length - shown.length;
    return (
        <li className="flex gap-3" data-testid="huddle-event">
            <span
                aria-hidden
                className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-[var(--accent-brand)]"
            >
                <Headphones className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
                <p className="text-sm">
                    <span className="font-medium">Huddle</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                        <time dateTime={event.at}>{when}</time>
                    </span>
                    {cards > 0 && (
                        <span className="ml-2 text-xs text-muted-foreground">
                            {cards} change{cards === 1 ? "" : "s"} proposed
                        </span>
                    )}
                </p>
                {lines.length === 0 ? (
                    <p className="mt-0.5 text-sm text-muted-foreground">Nothing was said.</p>
                ) : (
                    <div className="mt-1 space-y-0.5 rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-sm">
                        {!open && hidden > 0 && (
                            <p className="text-xs text-muted-foreground">{hidden} earlier lines</p>
                        )}
                        {shown.map((line, index) => (
                            <p key={index} className="break-words">
                                <span className="font-medium">{line.who === "you" ? "You" : agentName}: </span>
                                {line.text}
                                {line.interrupted && <span className="text-muted-foreground"> (cut off)</span>}
                            </p>
                        ))}
                        {lines.length > HUDDLE_PREVIEW_LINES && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="-ml-2 h-7 px-2 text-xs"
                                aria-expanded={open}
                                onClick={() => setOpen((value) => !value)}
                            >
                                {open ? "Show less" : `Show all ${lines.length} lines`}
                            </Button>
                        )}
                    </div>
                )}
            </div>
        </li>
    );
}
