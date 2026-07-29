"use client";

import { AlertCircle, Inbox, Loader2, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { CHROME, type Mode } from "./chartTheme";

/**
 * Which palette the charts should use. Recharts needs concrete colour values,
 * not CSS variables, so the mode has to be resolved in JS. Tracks both the OS
 * setting and the app's `data-theme` toggle, with the toggle winning.
 */
export function useChartMode(): Mode {
    const [mode, setMode] = useState<Mode>("light");

    useEffect(() => {
        const resolve = (): Mode => {
            const stamped = document.documentElement.getAttribute("data-theme");
            if (stamped === "dark") return "dark";
            if (stamped === "light") return "light";
            return window.matchMedia("(prefers-color-scheme: dark)").matches
                ? "dark"
                : "light";
        };
        setMode(resolve());

        const media = window.matchMedia("(prefers-color-scheme: dark)");
        const onMedia = () => setMode(resolve());
        media.addEventListener("change", onMedia);

        const observer = new MutationObserver(() => setMode(resolve()));
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ["data-theme", "class"],
        });

        return () => {
            media.removeEventListener("change", onMedia);
            observer.disconnect();
        };
    }, []);

    return mode;
}

/**
 * A headline figure. Deliberately not a chart: a single number's job is to be
 * read, not compared.
 */
export function StatTile({
    label,
    value,
    sub,
    change,
    changeGoodWhenUp = true,
    tone,
}: {
    label: string;
    value: string;
    sub?: string;
    change?: number | null;
    changeGoodWhenUp?: boolean;
    tone?: "critical" | "warning";
}) {
    const hasChange = change !== null && change !== undefined && Number.isFinite(change);
    const up = hasChange && change! > 0;
    const flat = hasChange && Math.abs(change!) < 0.0005;
    const good = up === changeGoodWhenUp;

    return (
        <Card className={cn(tone === "critical" && "border-destructive/40")}>
            <CardContent className="pt-5">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    {label}
                </p>
                <p className="mt-1.5 text-2xl font-semibold tracking-tight">{value}</p>
                <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                    {hasChange && (
                        <span
                            className={cn(
                                "inline-flex items-center gap-0.5 font-medium",
                                flat
                                    ? "text-muted-foreground"
                                    : good
                                      ? "text-emerald-600 dark:text-emerald-400"
                                      : "text-destructive",
                            )}
                        >
                            {flat ? (
                                <Minus className="h-3 w-3" />
                            ) : up ? (
                                <TrendingUp className="h-3 w-3" />
                            ) : (
                                <TrendingDown className="h-3 w-3" />
                            )}
                            {(Math.abs(change!) * 100).toFixed(1)}%
                        </span>
                    )}
                    {sub && <span>{sub}</span>}
                </div>
            </CardContent>
        </Card>
    );
}

/**
 * Wraps a chart with its title and handles the three states a panel can be in.
 * Empty is a first-class state, not an afterthought — with almost no data yet,
 * the dashboard has to look calm and correct rather than broken.
 */
export function ChartCard({
    title,
    description,
    isEmpty,
    emptyMessage = "No data in this period yet.",
    loading,
    error,
    action,
    children,
    className,
    height = 260,
}: {
    title: string;
    description?: string;
    isEmpty?: boolean;
    emptyMessage?: string;
    loading?: boolean;
    error?: string | null;
    action?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
    height?: number;
}) {
    return (
        <Card className={className}>
            <CardHeader className="flex flex-row items-start justify-between gap-4 pb-2">
                <div>
                    <CardTitle className="text-sm font-medium">{title}</CardTitle>
                    {description && (
                        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
                    )}
                </div>
                {action}
            </CardHeader>
            <CardContent>
                {loading ? (
                    <Skeleton style={{ height }} className="w-full" />
                ) : error ? (
                    <PanelMessage icon={<AlertCircle className="h-5 w-5" />} height={height}>
                        {error}
                    </PanelMessage>
                ) : isEmpty ? (
                    <PanelMessage icon={<Inbox className="h-5 w-5" />} height={height}>
                        {emptyMessage}
                    </PanelMessage>
                ) : (
                    children
                )}
            </CardContent>
        </Card>
    );
}

export function PanelMessage({
    icon,
    height = 260,
    children,
}: {
    icon?: React.ReactNode;
    height?: number;
    children: React.ReactNode;
}) {
    return (
        <div
            className="flex flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground"
            style={{ minHeight: height }}
        >
            {icon}
            <p className="max-w-xs">{children}</p>
        </div>
    );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
    return (
        <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {label}…
        </div>
    );
}

/** Shared recharts tooltip so every chart reads the same and carries values. */
export function ChartTooltip({
    active,
    payload,
    label,
    formatter,
    labelFormatter,
}: {
    active?: boolean;
    payload?: Array<{ name?: string; value?: number; color?: string; dataKey?: string }>;
    label?: string;
    formatter: (value: number, key: string) => string;
    labelFormatter?: (label: string) => string;
}) {
    if (!active || !payload?.length) return null;
    return (
        <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
            <p className="mb-1 font-medium text-popover-foreground">
                {labelFormatter && label ? labelFormatter(label) : label}
            </p>
            <div className="space-y-0.5">
                {payload.map((entry, index) => (
                    <div key={index} className="flex items-center justify-between gap-4">
                        <span className="flex items-center gap-1.5 text-muted-foreground">
                            <span
                                aria-hidden
                                className="inline-block h-2 w-2 rounded-[2px]"
                                style={{ background: entry.color }}
                            />
                            {entry.name}
                        </span>
                        <span className="font-medium tabular-nums text-popover-foreground">
                            {formatter(entry.value ?? 0, entry.dataKey ?? "")}
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

export function axisProps(mode: Mode) {
    return {
        stroke: CHROME[mode].axis,
        tick: { fill: CHROME[mode].muted, fontSize: 11 },
        tickLine: false,
        axisLine: false,
    };
}

export function gridStroke(mode: Mode) {
    return CHROME[mode].grid;
}

/**
 * Whether it is safe to make an authenticated request yet.
 *
 * The auth interceptor that attaches the Bearer token is only registered once
 * auth has finished loading. Fetching before that sends an unauthenticated
 * request, which on these staff-gated routes fails silently as a 403 — so
 * every fetch on this dashboard waits for this to be true.
 */
export function useAuthReady(): boolean {
    const { user, loading } = useAuth();
    return !loading && Boolean(user);
}

/**
 * A legend rendered in the order we declare, not the order recharts picks.
 *
 * recharts sorts a stacked-series legend alphabetically, which scrambles the
 * one thing a pipeline chart is about — the order a turn actually flows
 * through the stages. Pass this to `<Legend content={...} />`.
 */
export function OrderedLegend({
    items,
}: {
    items: ReadonlyArray<{ key: string; label: string; color: string }>;
}) {
    return (
        <ul className="flex flex-wrap justify-center gap-x-3 gap-y-1 pt-1 text-[11px]">
            {items.map((item) => (
                <li key={item.key} className="flex items-center gap-1.5">
                    <span
                        aria-hidden
                        className="inline-block h-2 w-2 shrink-0 rounded-[1px]"
                        style={{ backgroundColor: item.color }}
                    />
                    {item.label}
                </li>
            ))}
        </ul>
    );
}
