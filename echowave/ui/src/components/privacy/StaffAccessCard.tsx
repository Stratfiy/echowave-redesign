"use client";

import { ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Grant = { id: number; workflow_run_id: number | null; scope: string; expires_at: string; revoked_at: string | null };

const DAYS = [1, 7, 30] as const;

/**
 * Whether Decibyl support may read this call (phase 3). Off unless a
 * workspace owner or admin turns it on, for this call or every call, for a
 * few days; switched off again here at any time. Staff cannot turn it on.
 */
export function StaffAccessCard({ runId }: { runId: number }) {
    const { user, loading: authLoading } = useAuth();
    const [grants, setGrants] = useState<Grant[] | null>(null);
    const [days, setDays] = useState<number>(7);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const result = await client.get({ url: "/api/v1/organizations/staff-access" });
        if (result.error) {
            setError(detailFromResult(result, "Could not load support access."));
            return;
        }
        setGrants(((result.data as { grants?: Grant[] })?.grants ?? []) as Grant[]);
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void load();
    }, [authLoading, user, load]);

    const now = Date.now();
    const live = (grants ?? []).filter((g) => !g.revoked_at && new Date(g.expires_at).getTime() > now && (g.workflow_run_id === runId || g.workflow_run_id === null));

    const allow = async () => {
        setBusy(true);
        setError(null);
        const result = await client.post({ url: "/api/v1/organizations/staff-access", body: { workflow_run_id: runId, days } as never, headers: { "Content-Type": "application/json" } });
        if (result.error) setError(detailFromResult(result, "Could not allow access. Only an owner or admin can."));
        setBusy(false);
        void load();
    };
    const stop = async (id: number) => {
        setBusy(true);
        setError(null);
        const result = await client.delete({ url: `/api/v1/organizations/staff-access/${id}` });
        if (result.error) setError(detailFromResult(result, "Could not switch it off."));
        setBusy(false);
        void load();
    };

    return (
        <Card className="border-border" data-testid="staff-access-card">
            <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-lg">
                    <ShieldCheck aria-hidden className="h-5 w-5" /> Decibyl support access
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
                {grants === null && !error ? (
                    <p className="text-muted-foreground">Checking…</p>
                ) : live.length === 0 ? (
                    <>
                        <p className="text-muted-foreground">
                            Support cannot read or hear this call. Allow it if you have asked for help with it; each time they open it is recorded.
                        </p>
                        <div className="flex flex-wrap items-center gap-2">
                            <label className="flex items-center gap-2">
                                For
                                <select
                                    value={days}
                                    onChange={(e) => setDays(Number(e.target.value))}
                                    className="min-h-11 rounded-md border border-input bg-background px-2 text-base md:min-h-9 md:text-sm"
                                >
                                    {DAYS.map((d) => (
                                        <option key={d} value={d}>
                                            {d === 1 ? "1 day" : `${d} days`}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <Button className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void allow()}>
                                Allow support to read this call
                            </Button>
                        </div>
                    </>
                ) : (
                    <ul className="space-y-2">
                        {live.map((g) => (
                            <li key={g.id} className="flex flex-wrap items-center gap-2">
                                <span>
                                    Support may read {g.workflow_run_id === null ? "every call in this workspace" : "this call"} until {new Date(g.expires_at).toLocaleString()}.
                                </span>
                                <Button variant="outline" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void stop(g.id)}>
                                    Switch off
                                </Button>
                            </li>
                        ))}
                    </ul>
                )}
                {error && (
                    <p role="alert" className="text-[#772322] dark:text-red-300">
                        {error}
                    </p>
                )}
            </CardContent>
        </Card>
    );
}
