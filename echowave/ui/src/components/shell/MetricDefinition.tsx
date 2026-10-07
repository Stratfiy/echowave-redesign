"use client";

/**
 * A number with its definition (design "Metrics events and release proof").
 *
 * The value shows as-is the moment it is known -- no count-up, no sweep --
 * and a missing value is an em dash with the reason, never a zero. "How
 * this is counted" opens the definition, the period and the source, so a
 * figure on a staff screen can be checked rather than believed.
 */

import { Info } from "lucide-react";
import { useId, useState } from "react";

import { cn } from "@/lib/utils";

export function MetricDefinition({
    name,
    value,
    unit,
    definition,
    period,
    source,
    missingReason,
    className,
}: {
    name: string;
    /** Null when not known: shown as an em dash, never as 0. */
    value: number | string | null;
    unit?: string;
    definition: string;
    /** "Last 7 days", "Today (IST)". */
    period?: string;
    /** Where the number comes from: "workflow_runs, completed only". */
    source?: string;
    /** Why there is no value: "Calendar not connected". */
    missingReason?: string;
    className?: string;
}) {
    const [open, setOpen] = useState(false);
    const detailId = useId();
    const known = value !== null && value !== undefined;
    return (
        <div className={cn("flex min-w-0 flex-col gap-1", className)} data-testid="metric">
            <p className="text-xs text-muted-foreground">{name}</p>
            <p className="text-2xl font-semibold tabular-nums" aria-label={known ? `${name}: ${value}${unit ? ` ${unit}` : ""}` : `${name}: not available`}>
                {known ? value : "—"}
                {known && unit && <span className="ml-1 text-sm font-normal text-muted-foreground">{unit}</span>}
            </p>
            {!known && missingReason && <p className="text-xs text-muted-foreground">{missingReason}</p>}
            {period && <p className="text-xs text-muted-foreground">{period}</p>}
            <button
                type="button"
                aria-expanded={open}
                aria-controls={detailId}
                onClick={() => setOpen((was) => !was)}
                className="motion-m1 inline-flex min-h-11 items-center gap-1 self-start text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline md:min-h-6"
            >
                <Info aria-hidden className="h-3.5 w-3.5" />
                How this is counted
            </button>
            <div id={detailId} hidden={!open} className="motion-m8 rounded-md border border-border bg-muted/30 p-2 text-xs">
                <p>{definition}</p>
                {source && <p className="mt-1 font-mono text-[11px] text-muted-foreground">{source}</p>}
            </div>
        </div>
    );
}

export default MetricDefinition;
