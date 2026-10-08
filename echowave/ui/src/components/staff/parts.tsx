"use client";

/**
 * Small pieces every staff screen uses: a panel that draws each load state
 * honestly, a state badge with a text label and icon (never colour alone),
 * a labelled scroll region for a true data table, and a page header.
 */

import { AlertTriangle, CheckCircle2, CircleHelp, Clock, MinusCircle, RefreshCw, Wrench, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Loaded } from "@/lib/staff/data";
import { ago, words } from "@/lib/staff/format";
import { cn } from "@/lib/utils";

const GOOD = new Set(["healthy", "ok", "passed", "succeeded", "refunded", "resolved", "active", "available", "connected", "paid", "comparable", "mature"]);
const BAD = new Set(["degraded", "failed", "regression", "verification_failed", "suspended", "denied", "payment_failed", "critical", "rejected", "expired"]);
const UNKNOWN = new Set(["unknown", "outcome_unknown", "unavailable", "stale", "partial", "insufficient_sample", "insufficient_window", "inconsistent_fixture", "missing_baseline", "undefined", "needs_baseline"]);
const SETUP = new Set(["needs_setup", "disabled_by_policy"]);

export function StateBadge({ state, label }: { state: string | null | undefined; label?: string }) {
    const s = state ?? "unknown";
    const tone = GOOD.has(s) ? "good" : BAD.has(s) ? "bad" : UNKNOWN.has(s) ? "unknown" : SETUP.has(s) ? "setup" : "pending";
    const Icon = { good: CheckCircle2, bad: XCircle, unknown: CircleHelp, setup: Wrench, pending: Clock }[tone];
    return (
        <span
            data-state={s}
            className={cn(
                "inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs",
                tone === "good" && "border-[#075A39]/30 text-[#075A39] dark:text-emerald-300",
                tone === "bad" && "border-[#772322]/30 text-[#772322] dark:text-red-300",
                tone === "unknown" && "border-[#705500]/30 text-[#705500] dark:text-amber-300",
                (tone === "setup" || tone === "pending") && "border-border text-muted-foreground",
            )}
        >
            <Icon aria-hidden className="h-3.5 w-3.5" />
            {label ?? words(s)}
        </span>
    );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
    return (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
                <h1 className="text-2xl font-semibold leading-8">{title}</h1>
                {description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}
            </div>
            {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
    );
}

/** A section whose body draws only once its data is known; every other
 *  state is said in words, and stale data stays visible under a warning. */
export function Panel<T>({
    title,
    query,
    children,
    className,
    skeletonHeight = 120,
    setupHint,
}: {
    title?: string;
    query: Loaded<T>;
    children: (data: T) => ReactNode;
    className?: string;
    skeletonHeight?: number;
    setupHint?: string;
}) {
    const { state, data, error, refreshedAt, refresh } = query;
    return (
        <section className={cn("min-w-0 rounded-lg border border-border p-4", className)} aria-label={title} aria-busy={state === "loading"}>
            {title && <h2 className="mb-3 text-base font-semibold">{title}</h2>}
            {state === "loading" && <Skeleton className="w-full" style={{ height: skeletonHeight }} />}
            {state === "needs_setup" && (
                <p className="flex items-start gap-2 text-sm text-muted-foreground" data-testid="needs-setup">
                    <Wrench aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>Needs setup. {setupHint ?? "This part of the console is not switched on or not built here yet."}</span>
                </p>
            )}
            {state === "denied" && (
                <p className="flex items-start gap-2 text-sm text-muted-foreground">
                    <MinusCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> Your role does not include this.
                </p>
            )}
            {state === "failed" && (
                <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-[#772322] dark:text-red-300">
                    <AlertTriangle aria-hidden className="h-4 w-4" /> Could not load: {error}. This is not the same as nothing to show.
                    <Button variant="outline" size="sm" className="min-h-11 md:min-h-8" onClick={() => void refresh()}>
                        <RefreshCw aria-hidden /> Try again
                    </Button>
                </div>
            )}
            {state === "stale" && (
                <p role="status" className="mb-2 flex items-center gap-2 text-xs text-[#705500] dark:text-amber-300">
                    <AlertTriangle aria-hidden className="h-3.5 w-3.5" /> Refresh failed ({error}). Showing values from {ago(refreshedAt)}.
                </p>
            )}
            {(state === "ok" || state === "stale") && data !== null && children(data)}
        </section>
    );
}

/** A true data table may scroll on its own; the page never does. Relative,
 *  so a visually hidden header label stays inside the scroll region instead
 *  of widening the page. */
export function TableRegion({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div role="region" aria-label={label} tabIndex={0} className="relative max-w-full overflow-x-auto">
            {children}
        </div>
    );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
    return (
        <>
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="min-w-0 break-words">{children}</dd>
        </>
    );
}

export function Fields({ children, className }: { children: ReactNode; className?: string }) {
    return <dl className={cn("grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-3 gap-y-1 text-sm", className)}>{children}</dl>;
}

export function Empty({ children }: { children: ReactNode }) {
    return <p className="text-sm text-muted-foreground">{children}</p>;
}
