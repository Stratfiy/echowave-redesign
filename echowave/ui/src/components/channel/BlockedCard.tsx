"use client";

/**
 * A bot stopped, and this is what to do about it.
 *
 * These rows used to be an amber icon and a sentence. "Could not finish its
 * run" is a symptom; the reader is left to work out whether that means the
 * account is out of credit, a connector is down, or the thing that arrived
 * was missing a field — three problems with three different answers, and
 * one of them takes a minute to fix.
 *
 * The wall and the ways past it are computed by the server from what the
 * writer already recorded (`services/workflow/blocked.py`), so this
 * component renders them and invents nothing. That matters: a choice this
 * screen made up would be a door that is not there, which costs the reader
 * the walk.
 *
 * Lettered because the bot's own question card is lettered. A person who
 * has answered one of those should recognise this as the same gesture.
 */

import Link from "next/link";
import type { ComponentType } from "react";

import type { BlockedWall } from "@/client/types.gen";

/** The server's shape, not a copy of it. A local mirror of a response type
 *  is a second definition that drifts, and this one would drift silently:
 *  a way forward the server added would simply not render. */
export type Wall = BlockedWall;

export function BlockedCard({
    wall,
    summary,
    icon: Icon,
}: {
    wall: Wall;
    /** What the bot said. Kept above the wall: it is the bot's own words. */
    summary: string;
    icon?: ComponentType<{ className?: string }>;
}) {
    return (
        <div
            className="rounded-lg border border-amber-500/40 bg-amber-50/50 p-3 dark:bg-amber-500/5"
            data-testid="blocked-card"
            data-reason={wall.reason}
        >
            <p className="flex items-start gap-2 text-sm">
                {Icon ? (
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                ) : null}
                <span>{summary}</span>
            </p>

            {/* The wall, in the reader's words rather than the system's. */}
            <p className="mt-1.5 text-sm text-muted-foreground">{wall.says}</p>

            {wall.ways && wall.ways.length > 0 ? (
                <ul className="mt-3 space-y-1.5">
                    {wall.ways.map((way) => (
                        <li key={way.letter}>
                            <Link
                                href={way.href}
                                className="flex items-center gap-2.5 rounded-md border border-border bg-background px-3 py-2 text-sm transition-colors hover:bg-muted/60"
                            >
                                <span
                                    aria-hidden
                                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-border font-mono text-[11px] font-medium text-muted-foreground"
                                >
                                    {way.letter}
                                </span>
                                <span className="font-medium">{way.label}</span>
                            </Link>
                        </li>
                    ))}
                </ul>
            ) : null}
        </div>
    );
}
