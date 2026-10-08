"use client";

/**
 * "Who owes me" (handoff 6, Follow-up; founder request). The commitments a
 * person approved tracking, owed to them, with totals and what is overdue.
 * A follow-up's line is its card's own state, so it can never say "sent"
 * while the card says otherwise. Settling or sharing names the revision it
 * read; a change made elsewhere since is shown, not overwritten.
 */

import { Loader2, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    addCommitmentApiV1HelpersCommitmentsPost,
    changeCommitmentApiV1HelpersCommitmentsCommitmentUuidPatch,
    whoOwesMeApiV1HelpersWhoOwesMeGet,
} from "@/client/sdk.gen";
import type { CommitmentOut, WhoOwesMeResponse } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { DELIVERY_LABEL } from "@/lib/helpers";

type Load = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; data: WhoOwesMeResponse };

const FIELD = "min-h-11 w-full rounded-md border border-border bg-background px-3 text-base md:text-sm";

function AddForm({ onAdded }: { onAdded: () => void }) {
    const [open, setOpen] = useState(false);
    const [who, setWho] = useState("");
    const [what, setWhat] = useState("");
    const [amount, setAmount] = useState("");
    const [due, setDue] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    if (!open) {
        return (
            <Button type="button" variant="outline" className="min-h-11" onClick={() => setOpen(true)}>
                <Plus aria-hidden className="h-4 w-4" /> Add one
            </Button>
        );
    }
    return (
        <form
            className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-2"
            onSubmit={async (event) => {
                event.preventDefault();
                setSaving(true);
                setError(null);
                const response = await addCommitmentApiV1HelpersCommitmentsPost({
                    body: {
                        direction: "owed_to_me",
                        counterparty: who,
                        description: what,
                        amount: amount || null,
                        due_on: due || null,
                    },
                });
                setSaving(false);
                if (response.error) {
                    setError(detailFromResult(response, "Not saved. Try again."));
                    return;
                }
                setWho("");
                setWhat("");
                setAmount("");
                setDue("");
                setOpen(false);
                onAdded();
            }}
        >
            <label className="text-sm">
                Who
                <input className={FIELD} value={who} onChange={(e) => setWho(e.target.value)} required />
            </label>
            <label className="text-sm">
                For what
                <input className={FIELD} value={what} onChange={(e) => setWhat(e.target.value)} required />
            </label>
            <label className="text-sm">
                Amount (₹)
                <input className={FIELD} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </label>
            <label className="text-sm">
                Due
                <input className={FIELD} type="date" value={due} onChange={(e) => setDue(e.target.value)} />
            </label>
            {error && (
                <p className="text-sm text-destructive sm:col-span-2" role="status">
                    {error}
                </p>
            )}
            <div className="flex gap-2 sm:col-span-2">
                <Button type="submit" className="min-h-11" disabled={saving}>
                    {saving && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Track it
                </Button>
                <Button type="button" variant="ghost" className="min-h-11" onClick={() => setOpen(false)}>
                    Cancel
                </Button>
            </div>
        </form>
    );
}

function Row({ item, onChanged }: { item: CommitmentOut; onChanged: (note?: string) => void }) {
    const [busy, setBusy] = useState(false);
    const change = async (body: { status?: "settled" | "cancelled"; visibility?: "private" | "workspace" }) => {
        setBusy(true);
        const response = await changeCommitmentApiV1HelpersCommitmentsCommitmentUuidPatch({
            path: { commitment_uuid: item.uuid },
            body: { revision: item.revision, ...body },
        });
        setBusy(false);
        onChanged(response.error ? detailFromResult(response, "That did not change. Try again.") : undefined);
    };
    return (
        <li className="space-y-1 py-3" data-testid="owed-row">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="min-w-0 break-words font-medium">
                    {item.counterparty}
                    {item.amount && <span className="ml-2">{item.amount}</span>}
                </p>
                <p className={item.overdue ? "text-sm font-medium text-[#8A1C1C] dark:text-red-300" : "text-sm text-muted-foreground"}>
                    {item.due_on ? `${item.overdue ? "Overdue since" : "Due"} ${item.due_on}` : "No due date"}
                </p>
            </div>
            <p className="break-words text-sm text-muted-foreground">{item.description}</p>
            {item.follow_up && (
                <p className="text-sm" data-testid="follow-up-state">
                    Follow-up: {DELIVERY_LABEL[item.follow_up.delivery] ?? item.follow_up.delivery}
                    {item.follow_up.error && <span className="text-muted-foreground"> ({item.follow_up.error})</span>}
                </p>
            )}
            <div className="flex flex-wrap gap-2">
                <Link
                    className="inline-flex min-h-11 items-center rounded-md border border-border px-3 text-sm hover:bg-accent"
                    href={`/overview?helper=follow_up&say=${encodeURIComponent(`Draft a follow-up to ${item.counterparty} about commitment ${item.id}: ${item.description}`)}`}
                >
                    Follow up
                </Link>
                {item.mine && (
                    <>
                        <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => void change({ status: "settled" })}>
                            Mark paid
                        </Button>
                        <Button
                            type="button"
                            variant="ghost"
                            className="min-h-11"
                            disabled={busy}
                            onClick={() => void change({ visibility: item.visibility === "workspace" ? "private" : "workspace" })}
                        >
                            {item.visibility === "workspace" ? "Make private" : "Share with workspace"}
                        </Button>
                    </>
                )}
                {!item.mine && <span className="self-center text-xs text-muted-foreground">Shared by a teammate</span>}
            </div>
        </li>
    );
}

export function WhoOwesMe() {
    const { user, loading: authLoading } = useAuth();
    const [load, setLoad] = useState<Load>({ state: "loading" });
    const [note, setNote] = useState<string | null>(null);
    const fetchOwed = useCallback(async () => {
        const response = await whoOwesMeApiV1HelpersWhoOwesMeGet();
        if (response.error || !response.data) {
            setLoad({ state: "failed", message: detailFromResult(response, "Could not load who owes you.") });
            return;
        }
        setLoad({ state: "ready", data: response.data });
    }, []);
    useEffect(() => {
        if (authLoading || !user) return;
        void fetchOwed();
    }, [authLoading, user, fetchOwed]);

    if (load.state === "loading") return <p className="px-4 py-8 text-sm text-muted-foreground" role="status">Loading…</p>;
    if (load.state === "failed") return <ErrorState title="Who owes you did not load" description={load.message} onRetry={() => void fetchOwed()} />;
    const { items, totals, overdue } = load.data;
    return (
        <div className="mx-auto w-full max-w-[760px] space-y-4 px-4 py-4 sm:px-6" data-testid="who-owes-me">
            {items.length > 0 && (
                <p className="text-base" role="status">
                    {totals.map((t) => t.amount).join(" + ") || "No amounts"} owed by {items.length}{" "}
                    {items.length === 1 ? "person" : "people"}
                    {overdue > 0 ? `, ${overdue} overdue` : ""}.
                </p>
            )}
            <AddForm onAdded={() => void fetchOwed()} />
            {note && (
                <p className="text-sm text-destructive" role="status">
                    {note}
                </p>
            )}
            {items.length === 0 ? (
                <EmptyState
                    title="Nobody owes you anything tracked here."
                    description="Tell Follow-up in Chat about a payment you are waiting for, or add one above."
                />
            ) : (
                <ul className="divide-y divide-border" aria-label="Who owes me">
                    {items.map((item) => (
                        <Row
                            key={item.uuid}
                            item={item}
                            onChanged={(message) => {
                                setNote(message ?? null);
                                void fetchOwed();
                            }}
                        />
                    ))}
                </ul>
            )}
        </div>
    );
}
