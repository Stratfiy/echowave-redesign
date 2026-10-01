"use client";

import { AlertTriangle, Flag, Loader2, Plus, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
    type GlobalChange,
    GlobalChangeDialog,
    TurnOnForAccountDialog,
} from "@/components/superadmin/FlagDialogs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/lib/auth";
import {
    clearForOrganization,
    type FlagOverride,
    type FlagRow,
    loadFlags,
    orgLabel,
    sourceLabel,
} from "@/lib/superadmin/flags";
import { cn } from "@/lib/utils";

/**
 * Feature flags (ADMIN-1). Every switched-off feature, what everyone gets,
 * and the accounts it is on (or held off) for. Replaces SSH, an `.env` edit
 * and a restart: a change here reaches every server within seconds, and
 * every change is written to the staff audit log.
 */
export default function FlagsPage() {
    const { user, loading: authLoading } = useAuth();
    const [flags, setFlags] = useState<FlagRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadedAt, setLoadedAt] = useState<Date | null>(null);
    const [filter, setFilter] = useState("");
    const [addingTo, setAddingTo] = useState<string | null>(null);
    const [globalChange, setGlobalChange] = useState<GlobalChange | null>(null);
    const [removing, setRemoving] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const outcome = await loadFlags();
        setLoading(false);
        if (!outcome.ok) {
            setError(outcome.error);
            return;
        }
        setFlags(outcome.value);
        setError(null);
        setLoadedAt(new Date());
    }, []);

    // A boolean, not the user object: a new object with the same person in it
    // must not reload the screen.
    const authReady = !authLoading && Boolean(user);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    const shown = useMemo(() => {
        const q = filter.trim().toLowerCase();
        if (!flags || !q) return flags ?? [];
        return flags.filter(
            (f) =>
                f.name.includes(q) ||
                f.description.toLowerCase().includes(q) ||
                f.overrides.some((o) => orgLabel(o).toLowerCase().includes(q)),
        );
    }, [flags, filter]);

    const remove = async (flag: string, o: FlagOverride) => {
        if (o.organization_id === null) return;
        const key = `${flag}:${o.organization_id}`;
        setRemoving(key);
        const outcome = await clearForOrganization(flag, o.organization_id);
        setRemoving(null);
        if (!outcome.ok) {
            toast.error(outcome.error);
            return;
        }
        toast.success(`Removed ${orgLabel(o)} from ${flag}`);
        await load();
    };

    return (
        <div className="container mx-auto max-w-6xl space-y-6 px-4 py-8">
            <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="space-y-1">
                    <h1 className="flex items-center gap-2 text-2xl font-semibold">
                        <Flag className="h-5 w-5" /> Feature flags
                    </h1>
                    <p className="max-w-2xl text-sm text-muted-foreground">
                        Turn a feature on for one account, or for everyone. A change reaches
                        every server within seconds and is written to the audit log.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {loadedAt && (
                        <span className="text-xs text-muted-foreground">
                            Updated {loadedAt.toLocaleTimeString()}
                        </span>
                    )}
                    <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                        <RefreshCw className={cn("mr-1 h-4 w-4", loading && "animate-spin")} />
                        Refresh
                    </Button>
                </div>
            </div>

            <Input
                placeholder="Filter by flag or account"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="max-w-sm"
                aria-label="Filter flags"
            />

            {error && (
                <Card>
                    <CardContent className="flex flex-wrap items-center gap-3 py-4 text-sm">
                        <AlertTriangle className="h-4 w-4 text-destructive" />
                        <span>{error}</span>
                        {flags && (
                            <span className="text-muted-foreground">
                                Showing what was loaded at {loadedAt?.toLocaleTimeString()}.
                            </span>
                        )}
                        <Button variant="outline" size="sm" onClick={() => void load()}>
                            Try again
                        </Button>
                    </CardContent>
                </Card>
            )}

            {flags === null && !error && (
                <div className="flex items-center gap-2 py-12 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading flags
                </div>
            )}

            {flags !== null && shown.length === 0 && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                    {flags.length === 0 ? "No flags are registered." : "No flag matches that filter."}
                </p>
            )}

            {shown.length > 0 && (
                <Card>
                    <CardContent className="p-0">
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead className="min-w-[220px]">Flag</TableHead>
                                        <TableHead className="min-w-[150px]">Everyone</TableHead>
                                        <TableHead className="min-w-[260px]">Accounts</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {shown.map((f) => (
                                        <FlagTableRow
                                            key={f.name}
                                            flag={f}
                                            removing={removing}
                                            onAdd={() => setAddingTo(f.name)}
                                            onRemove={(o) => void remove(f.name, o)}
                                            onGlobal={setGlobalChange}
                                        />
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                </Card>
            )}

            <TurnOnForAccountDialog
                flag={addingTo}
                open={addingTo !== null}
                onOpenChange={(open) => !open && setAddingTo(null)}
                onSaved={() => {
                    toast.success("Saved");
                    void load();
                }}
            />
            <GlobalChangeDialog
                change={globalChange}
                onOpenChange={(open) => !open && setGlobalChange(null)}
                onSaved={() => {
                    toast.success("Saved for everyone");
                    void load();
                }}
            />
        </div>
    );
}

function FlagTableRow({
    flag,
    removing,
    onAdd,
    onRemove,
    onGlobal,
}: {
    flag: FlagRow;
    removing: string | null;
    onAdd: () => void;
    onRemove: (o: FlagOverride) => void;
    onGlobal: (change: GlobalChange) => void;
}) {
    return (
        <TableRow data-testid={`flag-${flag.name}`}>
            <TableCell className="align-top">
                <div className="font-mono text-sm">{flag.name}</div>
                <div className="text-xs text-muted-foreground">{flag.description}</div>
            </TableCell>
            <TableCell className="align-top">
                <div className="flex items-center gap-2">
                    <Switch
                        checked={flag.global_enabled}
                        aria-label={`${flag.name} for everyone`}
                        onCheckedChange={(enabled) =>
                            onGlobal({ flag: flag.name, kind: "set", enabled })
                        }
                    />
                    <Badge variant={flag.global_enabled ? "success" : "outline"}>
                        {flag.global_enabled ? "On" : "Off"}
                    </Badge>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                    <span>{sourceLabel(flag.global_source)}</span>
                    {flag.global_source === "console" && (
                        <>
                            {" · "}
                            <button
                                type="button"
                                className="underline underline-offset-2 hover:text-foreground"
                                onClick={() => onGlobal({ flag: flag.name, kind: "clear" })}
                            >
                                use environment ({flag.environment_enabled ? "on" : "off"})
                            </button>
                        </>
                    )}
                </div>
            </TableCell>
            <TableCell className="align-top">
                <div className="flex flex-wrap items-center gap-1.5">
                    {flag.overrides.map((o) => {
                        const key = `${flag.name}:${o.organization_id}`;
                        return (
                            <span
                                key={key}
                                title={o.note ?? undefined}
                                className={cn(
                                    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                                    o.enabled
                                        ? "border-transparent bg-[var(--accent-brand-tint)] text-[var(--primary)]"
                                        : "border-dashed text-muted-foreground",
                                    o.expired && "line-through opacity-60",
                                )}
                            >
                                <Link
                                    href={`/superadmin/billing/accounts/${o.organization_id}`}
                                    className="hover:underline"
                                >
                                    {orgLabel(o)}
                                </Link>
                                {!o.enabled && <span>(held off)</span>}
                                {o.expired && <span>(expired)</span>}
                                <button
                                    type="button"
                                    aria-label={`Remove ${orgLabel(o)} from ${flag.name}`}
                                    disabled={removing === key}
                                    onClick={() => onRemove(o)}
                                    className="rounded-full p-0.5 hover:bg-black/10"
                                >
                                    {removing === key ? (
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                    ) : (
                                        <X className="h-3 w-3" />
                                    )}
                                </button>
                            </span>
                        );
                    })}
                    {flag.environment_organization_ids.map((id) => (
                        <span
                            key={`env-${id}`}
                            title="Set in FEATURE_ORG_OVERRIDES on the server; change it there"
                            className="inline-flex items-center rounded-full border border-dashed px-2 py-0.5 text-xs text-muted-foreground"
                        >
                            Organization {id} (environment)
                        </span>
                    ))}
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onAdd}>
                        <Plus className="mr-1 h-3 w-3" /> Turn on for an account
                    </Button>
                </div>
            </TableCell>
        </TableRow>
    );
}
