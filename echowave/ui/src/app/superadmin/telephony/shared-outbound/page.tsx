"use client";

/**
 * Decibyl's own caller IDs, lent to every account for outbound calls.
 *
 * This is the pool a trial account dials from when it has no carrier account
 * of its own — the same job Twilio's own shared verification number does. Small
 * by design and shared platform-wide, so the count here is the operational
 * limit that bites: a third simultaneous trial call waits for one of these to
 * free up.
 *
 * Adding a number to the pool needs its numeric id, not its address — there is
 * no search here on purpose. This is a rare, deliberate commercial decision
 * (which of Decibyl's own numbers we hand to every account), not a self-serve
 * flow, and the id is one lookup away in the phone-numbers table for whoever
 * is making that call.
 */

import { AlertTriangle, Loader2, PhoneOff, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    listSharedOutboundApiV1AdminTelephonySharedOutboundGet,
    setSharedOutboundApiV1AdminTelephonyPhoneNumbersPhoneNumberIdSharedOutboundPost,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type SharedOutboundNumber = {
    id: number;
    address: string;
    telephony_configuration_id: number;
    organization_id: number;
    is_active: boolean;
};

export default function SharedOutboundPage() {
    const { user, loading: authLoading } = useAuth();

    const [numbers, setNumbers] = useState<SharedOutboundNumber[]>([]);
    const [concurrentTrialCalls, setConcurrentTrialCalls] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<number | null>(null);

    const [addId, setAddId] = useState("");
    const [adding, setAdding] = useState(false);
    const [addError, setAddError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await listSharedOutboundApiV1AdminTelephonySharedOutboundGet({});
        if (result.error) {
            setError(detailFromResult(result, "Failed to load shared outbound numbers"));
        } else {
            const data = result.data as {
                numbers?: SharedOutboundNumber[];
                concurrent_trial_calls?: number;
            };
            setNumbers(data.numbers ?? []);
            setConcurrentTrialCalls(data.concurrent_trial_calls ?? 0);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (authLoading || !user) return;
        void load();
    }, [authLoading, user, load]);

    const remove = async (id: number) => {
        setBusyId(id);
        setError(null);
        const result = await setSharedOutboundApiV1AdminTelephonyPhoneNumbersPhoneNumberIdSharedOutboundPost({
            path: { phone_number_id: id },
            body: { shared: false },
        });
        if (result.error) {
            setError(detailFromResult(result, "Failed to remove from the shared pool"));
        } else {
            await load();
        }
        setBusyId(null);
    };

    const add = async () => {
        const id = Number(addId.trim());
        if (!Number.isFinite(id) || id <= 0) {
            setAddError("Enter the phone number's numeric id.");
            return;
        }
        setAdding(true);
        setAddError(null);
        const result = await setSharedOutboundApiV1AdminTelephonyPhoneNumbersPhoneNumberIdSharedOutboundPost({
            path: { phone_number_id: id },
            body: { shared: true },
        });
        if (result.error) {
            setAddError(detailFromResult(result, "Failed to add to the shared pool"));
        } else {
            setAddId("");
            await load();
        }
        setAdding(false);
    };

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[900px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Shared outbound numbers
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        Decibyl&apos;s own caller IDs, lent to every account that has no
                        carrier account of its own. This is the pool a trial call dials
                        from — outbound only, never inbound-routable.
                    </p>
                </header>

                {loading ? (
                    <Skeleton className="h-64 w-full rounded-2xl" />
                ) : (
                    <>
                        {error && (
                            <section className="glass-panel mb-5 flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                                <AlertTriangle className="h-4 w-4 shrink-0" />
                                {error}
                            </section>
                        )}

                        <section className="glass-panel mb-5 px-6 pb-6 pt-5">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                                        Pool
                                    </h2>
                                    <p className="mt-0.5 text-xs text-muted-foreground">
                                        {numbers.length} number{numbers.length === 1 ? "" : "s"} —{" "}
                                        {concurrentTrialCalls} simultaneous trial call
                                        {concurrentTrialCalls === 1 ? "" : "s"} the platform can place
                                        right now.
                                    </p>
                                </div>
                                <Badge variant="outline">{concurrentTrialCalls} concurrent</Badge>
                            </div>

                            {numbers.length === 0 ? (
                                <p className="mt-4 text-sm text-muted-foreground">
                                    No shared outbound numbers. Every trial call currently has
                                    nothing to dial from.
                                </p>
                            ) : (
                                <div className="mt-4 overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Address</TableHead>
                                                <TableHead>Organization</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead className="text-right" />
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {numbers.map((n) => (
                                                <TableRow key={n.id}>
                                                    <TableCell className="font-mono">{n.address}</TableCell>
                                                    <TableCell className="text-muted-foreground">
                                                        #{n.organization_id}
                                                    </TableCell>
                                                    <TableCell>
                                                        {n.is_active ? (
                                                            <Badge variant="secondary">Active</Badge>
                                                        ) : (
                                                            <Badge variant="outline">Inactive</Badge>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            disabled={busyId === n.id}
                                                            onClick={() => remove(n.id)}
                                                            aria-label={`Remove ${n.address} from the shared pool`}
                                                        >
                                                            {busyId === n.id ? (
                                                                <Loader2 className="h-4 w-4 animate-spin" />
                                                            ) : (
                                                                <PhoneOff className="h-4 w-4 text-destructive" />
                                                            )}
                                                        </Button>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}
                        </section>

                        <section className="glass-panel px-6 pb-6 pt-5">
                            <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                                Add a number
                            </h2>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                                Refuses a number that answers inbound calls — a shared number
                                that also routed inbound could not say which account an
                                incoming call belonged to.
                            </p>
                            <div className="mt-4 flex flex-wrap items-end gap-3">
                                <div className="space-y-1.5">
                                    <Label htmlFor="shared-outbound-id">Phone number id</Label>
                                    <Input
                                        id="shared-outbound-id"
                                        value={addId}
                                        onChange={(e) => setAddId(e.target.value)}
                                        placeholder="e.g. 1042"
                                        className="w-40"
                                    />
                                </div>
                                <Button onClick={add} disabled={adding || !addId.trim()}>
                                    {adding ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <Plus className="mr-2 h-4 w-4" />
                                    )}
                                    Add to pool
                                </Button>
                            </div>
                            {addError && (
                                <p className="mt-2 text-sm text-destructive">{addError}</p>
                            )}
                        </section>
                    </>
                )}
            </div>
        </div>
    );
}
