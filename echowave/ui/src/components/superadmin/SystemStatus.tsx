"use client";

import { AlertTriangle, CheckCircle2, CircleHelp, RefreshCw, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readSystemStatusApiV1AdminSystemGet } from "@/client/sdk.gen";
import { PanelMessage, useAuthReady } from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

type Probe = {
    ok: boolean;
    latency_ms: number | null;
    detail?: string | null;
};

type BalanceRow = {
    provider: string;
    status: string;
    kind: string | null;
    remaining: number | null;
    currency: string | null;
    needs_attention: boolean;
    detail: string | null;
};

export type SystemSnapshot = {
    status: "ok" | "degraded";
    build: { version: string; git_sha: string };
    probe_timeout_seconds: number;
    database: Probe;
    redis: Probe;
    queue: Probe & { length?: number };
    worker: Probe & { alive?: boolean | null; last_seen?: string | null; age_seconds?: number | null };
    provider_balances: Probe & { needs_attention?: number; providers?: BalanceRow[] };
};

/** How often the strip refreshes itself while open. */
export const SYSTEM_POLL_MS = 30_000;

function shortSha(sha: string): string {
    return sha === "unknown" ? sha : sha.slice(0, 7);
}

function age(seconds: number | null | undefined): string {
    if (seconds == null) return "never";
    if (seconds < 90) return `${Math.round(seconds)}s ago`;
    if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
    return `${Math.round(seconds / 3600)}h ago`;
}

/** One line per signal: what it is, whether it is fine, and the number. */
export function systemLines(snapshot: SystemSnapshot): Array<{
    key: string;
    label: string;
    state: "ok" | "bad" | "unknown";
    value: string;
    detail?: string | null;
}> {
    const latency = (probe: Probe) =>
        probe.latency_ms == null ? "" : `${Math.round(probe.latency_ms)} ms`;
    const worker = snapshot.worker;
    const balances = snapshot.provider_balances;
    return [
        {
            key: "database",
            label: "Database",
            state: snapshot.database.ok ? "ok" : "bad",
            value: snapshot.database.ok ? latency(snapshot.database) : "Down",
            detail: snapshot.database.detail,
        },
        {
            key: "redis",
            label: "Redis",
            state: snapshot.redis.ok ? "ok" : "bad",
            value: snapshot.redis.ok ? latency(snapshot.redis) : "Down",
            detail: snapshot.redis.detail,
        },
        {
            key: "worker",
            label: "Worker",
            // null alive is "no beat on record", not "dead" -- shown as unknown.
            state: worker.alive === true ? "ok" : worker.alive === false ? "bad" : "unknown",
            value:
                worker.alive === true
                    ? `Beat ${age(worker.age_seconds)}`
                    : worker.alive === false
                      ? `Stopped · last beat ${age(worker.age_seconds)}`
                      : "No heartbeat",
            detail: worker.detail,
        },
        {
            key: "queue",
            label: "Queue",
            state: snapshot.queue.ok ? "ok" : "bad",
            value: snapshot.queue.ok ? `${snapshot.queue.length ?? 0} waiting` : "Unknown",
            detail: snapshot.queue.detail,
        },
        {
            key: "balances",
            label: "Provider balances",
            state: !balances.ok && balances.needs_attention == null ? "unknown" : balances.ok ? "ok" : "bad",
            value:
                balances.needs_attention == null
                    ? "Could not check"
                    : balances.needs_attention === 0
                      ? "All funded"
                      : `${balances.needs_attention} need attention`,
            detail: balances.detail,
        },
    ];
}

function StateIcon({ state }: { state: "ok" | "bad" | "unknown" }) {
    if (state === "ok") return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" aria-label="OK" />;
    if (state === "bad") return <XCircle className="h-3.5 w-3.5 text-destructive" aria-label="Problem" />;
    return <CircleHelp className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" aria-label="Unknown" />;
}

export function useSystemSnapshot(pollMs: number = SYSTEM_POLL_MS) {
    const authReady = useAuthReady();
    const [snapshot, setSnapshot] = useState<SystemSnapshot | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [checkedAt, setCheckedAt] = useState<Date | null>(null);

    const refresh = useCallback(async () => {
        setLoading(true);
        const result = await readSystemStatusApiV1AdminSystemGet();
        if (result.error) {
            setError(detailFromResult(result, "Could not read system status"));
        } else {
            setSnapshot(result.data as unknown as SystemSnapshot);
            setError(null);
            setCheckedAt(new Date());
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void refresh();
        const id = setInterval(() => void refresh(), pollMs);
        return () => clearInterval(id);
    }, [authReady, refresh, pollMs]);

    return { snapshot, error, loading, refresh, checkedAt };
}

/** The compact strip at the top of the console (ADMIN-2, A7). */
export function SystemStrip() {
    const { snapshot, error, loading } = useSystemSnapshot();
    if (loading && !snapshot) return <Skeleton className="h-9 w-full" aria-label="Checking the system" />;
    if (!snapshot) {
        return (
            <p role="alert" className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-muted-foreground">
                <AlertTriangle className="h-4 w-4" aria-hidden />
                {error ?? "Could not read system status"}
            </p>
        );
    }
    return (
        <Link
            href="/superadmin/system"
            aria-label="System status"
            className={cn(
                "flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border px-3 py-2 text-xs hover:bg-muted/50",
                snapshot.status === "ok" ? "border-border" : "border-destructive/40",
            )}
        >
            <span className="font-medium">
                v{snapshot.build.version} · {shortSha(snapshot.build.git_sha)}
            </span>
            {systemLines(snapshot).map((line) => (
                <span key={line.key} className="inline-flex items-center gap-1" title={line.detail ?? undefined}>
                    <StateIcon state={line.state} />
                    <span className="text-muted-foreground">{line.label}</span>
                    <span className="tabular-nums">{line.value}</span>
                </span>
            ))}
            {error && <span className="text-destructive">Stale: {error}</span>}
        </Link>
    );
}

/** The full page: every probe with its detail, and the provider balances. */
export function SystemStatusPanel() {
    const { snapshot, error, loading, refresh, checkedAt } = useSystemSnapshot();

    if (loading && !snapshot) {
        return (
            <div className="space-y-2" aria-label="Checking the system">
                {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                ))}
            </div>
        );
    }
    if (!snapshot) {
        return (
            <PanelMessage icon={<AlertTriangle className="h-5 w-5" />} height={180}>
                {error ?? "Could not read system status"}
            </PanelMessage>
        );
    }
    const providers = snapshot.provider_balances.providers ?? [];
    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className={cn("font-medium", snapshot.status === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-destructive")}>
                    {snapshot.status === "ok" ? "All core services answering" : "Something needs attention"}
                </span>
                <span className="text-muted-foreground">
                    Version {snapshot.build.version} · commit {shortSha(snapshot.build.git_sha)}
                </span>
                <span className="text-muted-foreground">
                    {checkedAt ? `Checked ${checkedAt.toLocaleTimeString("en-IN")}` : ""}
                    {error ? ` · last refresh failed: ${error}` : ""}
                </span>
                <Button size="sm" variant="outline" className="ml-auto" onClick={() => void refresh()} disabled={loading}>
                    <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} aria-hidden />
                    Check again
                </Button>
            </div>
            <Card>
                <CardContent className="divide-y divide-border p-0">
                    {systemLines(snapshot).map((line) => (
                        <div key={line.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-3 text-sm">
                            <span className="inline-flex w-40 items-center gap-1.5 font-medium">
                                <StateIcon state={line.state} />
                                {line.label}
                            </span>
                            <span className="tabular-nums">{line.value}</span>
                            {line.detail && <span className="min-w-0 flex-1 text-muted-foreground">{line.detail}</span>}
                        </div>
                    ))}
                </CardContent>
            </Card>
            {providers.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Provider balances</CardTitle>
                    </CardHeader>
                    <CardContent className="divide-y divide-border p-0">
                        {providers.map((row) => (
                            <div key={row.provider} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-sm">
                                <span className="inline-flex w-40 items-center gap-1.5 capitalize">
                                    <StateIcon state={row.needs_attention ? "bad" : row.status === "ok" ? "ok" : "unknown"} />
                                    {row.provider}
                                </span>
                                <span className="tabular-nums">
                                    {row.remaining == null ? row.status : `${row.remaining.toLocaleString("en-IN")} ${row.currency ?? ""}`}
                                </span>
                                {row.detail && <span className="min-w-0 flex-1 text-muted-foreground">{row.detail}</span>}
                            </div>
                        ))}
                    </CardContent>
                </Card>
            )}
            <p className="text-xs text-muted-foreground">
                Each check is cut off after {snapshot.probe_timeout_seconds} s, so one slow service cannot blank this page.
            </p>
        </div>
    );
}
