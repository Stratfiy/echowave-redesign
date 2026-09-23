"use client";

import { Position } from "@xyflow/react";
import { CalendarClock, Globe, Megaphone, Phone, Webhook, Zap } from "lucide-react";

import type { Start } from "@/lib/graphExtras";

import { BaseHandle } from "./BaseHandle";

const ICONS: Record<string, typeof Phone> = {
    phone: Phone,
    routine: CalendarClock,
    trigger: Webhook,
    web: Globe,
    campaign: Megaphone,
};

/**
 * The graph's first card (G-1): what starts this agent. Read from the rows
 * that actually start it -- a number, a routine, a trigger, a web link, a
 * campaign -- so the canvas stops opening on "Start Call" for an agent a
 * routine runs. Display only: not a node in the definition, never saved,
 * cannot be selected, dragged or deleted.
 */
export function StartsCard({ data }: { data: { starts?: Start[] } }) {
    const starts = data.starts ?? [];
    return (
        <div
            className="w-64 rounded-xl border border-dashed bg-card/80 p-3 text-sm shadow-sm"
            aria-label="What starts this agent"
        >
            <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <Zap className="h-3.5 w-3.5" aria-hidden="true" /> Starts when
            </p>
            {starts.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                    Nothing starts it yet. Try it in the tester, or give it a number, a routine or a trigger.
                </p>
            ) : (
                <ul className="space-y-1.5">
                    {starts.map((start, index) => {
                        const Icon = ICONS[start.kind] ?? Zap;
                        return (
                            <li key={`${start.kind}-${index}`} className="flex items-start gap-2">
                                <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                                <div className="min-w-0">
                                    <p className="truncate text-xs font-medium">
                                        {start.label}
                                        {!start.active && <span className="font-normal text-muted-foreground"> (off)</span>}
                                    </p>
                                    {start.detail && (
                                        <p className="truncate text-[11px] text-muted-foreground">{start.detail}</p>
                                    )}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
            <BaseHandle type="source" position={Position.Right} aria-label="Starts the first step" />
        </div>
    );
}
