"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import {
    clearForOrganization,
    loadOrgFlags,
    type OrgFlagRow,
    setForOrganization,
} from "@/lib/superadmin/flags";

/**
 * The flags as one account sees them, on that account's page (ADMIN-1).
 * A toggle sets an override for this account only; "Reset" removes it so
 * the value everyone gets applies again.
 */
export function OrgFlagsPanel({ organizationId }: { organizationId: number }) {
    const [flags, setFlags] = useState<OrgFlagRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);

    const load = useCallback(async () => {
        const outcome = await loadOrgFlags(organizationId);
        if (!outcome.ok) {
            setError(outcome.error);
            return;
        }
        setFlags(outcome.value);
        setError(null);
    }, [organizationId]);

    useEffect(() => {
        void load();
    }, [load]);

    const toggle = async (flag: OrgFlagRow, enabled: boolean) => {
        setBusy(flag.name);
        const outcome = await setForOrganization(flag.name, organizationId, { enabled });
        setBusy(null);
        if (!outcome.ok) {
            toast.error(outcome.error);
            return;
        }
        await load();
    };

    const reset = async (flag: OrgFlagRow) => {
        setBusy(flag.name);
        const outcome = await clearForOrganization(flag.name, organizationId);
        setBusy(null);
        if (!outcome.ok) {
            toast.error(outcome.error);
            return;
        }
        await load();
    };

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Flags</CardTitle>
                <p className="mt-0.5 text-xs text-muted-foreground">
                    What is switched on for this account. A toggle here applies to this account
                    only; the value for everyone is set on{" "}
                    <Link href="/superadmin/flags" className="underline underline-offset-2">
                        Feature flags
                    </Link>
                    .
                </p>
            </CardHeader>
            <CardContent>
                {error && (
                    <div className="flex items-center gap-2 text-sm text-destructive">
                        <AlertTriangle className="h-4 w-4" /> {error}
                        <Button variant="outline" size="sm" onClick={() => void load()}>
                            Try again
                        </Button>
                    </div>
                )}
                {flags === null && !error && (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" /> Loading flags
                    </p>
                )}
                {flags !== null && flags.length === 0 && (
                    <p className="text-sm text-muted-foreground">No flags are registered.</p>
                )}
                {flags !== null && flags.length > 0 && (
                    <ul className="divide-y">
                        {flags.map((f) => (
                            <li
                                key={f.name}
                                className="flex flex-wrap items-center justify-between gap-2 py-2"
                                data-testid={`org-flag-${f.name}`}
                            >
                                <div className="min-w-0">
                                    <div className="font-mono text-sm">{f.name}</div>
                                    <div className="text-xs text-muted-foreground">{f.description}</div>
                                </div>
                                <div className="flex items-center gap-2">
                                    {f.override && !f.override.expired ? (
                                        <Badge variant="brand">this account</Badge>
                                    ) : f.environment_listed ? (
                                        <Badge variant="outline">environment</Badge>
                                    ) : (
                                        <Badge variant="outline">
                                            everyone: {f.global_enabled ? "on" : "off"}
                                        </Badge>
                                    )}
                                    {f.override && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            className="h-7 px-2 text-xs"
                                            disabled={busy === f.name}
                                            onClick={() => void reset(f)}
                                        >
                                            Reset
                                        </Button>
                                    )}
                                    <Switch
                                        checked={f.enabled}
                                        disabled={busy === f.name}
                                        aria-label={`${f.name} for this account`}
                                        onCheckedChange={(enabled) => void toggle(f, enabled)}
                                    />
                                </div>
                            </li>
                        ))}
                    </ul>
                )}
            </CardContent>
        </Card>
    );
}
