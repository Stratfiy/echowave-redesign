"use client";

/**
 * The multiple charged on managed model usage.
 *
 * One number sets the price of every managed call on every account, and this
 * form changes it with no deploy. That is the point, and it is also why saving
 * is two steps: the value is staged, a code goes to the operator inbox, and
 * nothing moves until the code comes back.
 *
 * The screen shows the *effect* rather than the setting — "1.4×" and what a
 * vendor's ₹1.00 becomes — because basis points are the storage format, not a
 * quantity anyone reasons in. A field that reads 14000 invites the mistake of
 * typing 140000.
 */

import { AlertTriangle, Check, Loader2, Mail, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    confirmManagedMarkupChangeApiV1AdminBillingRateCardMarkupPut,
    getManagedMarkupApiV1AdminBillingRateCardMarkupGet,
    requestManagedMarkupChangeApiV1AdminBillingRateCardMarkupRequestPost,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";

type Pending = {
    markup_bps: number;
    previous_markup_bps: number;
    expires_at: string;
    attempts_remaining: number;
};

type MarkupState = {
    markup_bps: number;
    min_bps: number;
    max_bps: number;
    notice_address: string;
    pending: Pending | null;
    history: Array<{
        markup_bps: number;
        effective_from: string;
        effective_to: string | null;
        note: string | null;
    }>;
};

/** Basis points to the multiple people actually say out loud. */
function asMultiple(bps: number): string {
    return `${(bps / 10000).toFixed(2).replace(/\.?0+$/, "")}×`;
}

export function ManagedMarkupCard() {
    const [state, setState] = useState<MarkupState | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    const [draft, setDraft] = useState("");
    const [note, setNote] = useState("");
    const [code, setCode] = useState("");
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        const result = await getManagedMarkupApiV1AdminBillingRateCardMarkupGet();
        if (result.error) {
            setError(detailFromError(result.error, "Could not load the markup"));
        } else {
            const next = result.data as unknown as MarkupState;
            setState(next);
            setDraft((next.markup_bps / 10000).toString());
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const requestChange = async () => {
        setBusy(true);
        setError(null);
        setNotice(null);
        const bps = Math.round(Number(draft) * 10000);
        const result =
            await requestManagedMarkupChangeApiV1AdminBillingRateCardMarkupRequestPost({
                body: { markup_bps: bps, note: note.trim() || null },
            });
        if (result.error) {
            setError(detailFromError(result.error, "Could not start the change"));
        } else {
            const data = result.data as unknown as {
                sent_to: string;
                email_sent: boolean;
                email_error: string | null;
            };
            // A failed send is reported rather than swallowed: the change is
            // still staged, and an operator who is not told will sit waiting
            // for an email that never arrives.
            setNotice(
                data.email_sent
                    ? `Code sent to ${data.sent_to}. Enter it below to apply the change.`
                    : `The change is staged, but the email could not be sent (${data.email_error}). Fix mail delivery and request it again.`,
            );
            await load();
        }
        setBusy(false);
    };

    const confirmChange = async () => {
        setBusy(true);
        setError(null);
        const result =
            await confirmManagedMarkupChangeApiV1AdminBillingRateCardMarkupPut({
                body: { code: code.trim() },
            });
        if (result.error) {
            setError(detailFromError(result.error, "Could not apply the change"));
        } else {
            const data = result.data as unknown as { markup_bps: number };
            setNotice(`Markup is now ${asMultiple(data.markup_bps)}.`);
            setCode("");
            await load();
        }
        setBusy(false);
    };

    if (loading) {
        return <Skeleton className="h-64 w-full rounded-2xl" />;
    }
    if (!state) {
        return (
            <section className="glass-panel px-5 py-4">
                <p className="text-sm text-destructive">{error}</p>
            </section>
        );
    }

    const pending = state.pending;
    const draftBps = Math.round(Number(draft) * 10000);
    const draftValid =
        Number.isFinite(draftBps) &&
        draftBps >= state.min_bps &&
        draftBps <= state.max_bps &&
        draftBps !== state.markup_bps;

    return (
        <section className="glass-panel px-5 pb-5 pt-4">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        Managed model markup
                    </h2>
                    <p className="mt-0.5 max-w-xl text-xs leading-relaxed tracking-[-0.01em] text-muted-foreground">
                        What we charge on model usage bought with our keys. Applies to
                        speech, language and voice — never to telephony, the platform
                        fee, or an account running on its own key.
                    </p>
                </div>
                <div className="text-right">
                    <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                        In force
                    </p>
                    <p className="text-[1.75rem] font-semibold leading-[1.1] tracking-[-0.035em] text-foreground">
                        {asMultiple(state.markup_bps)}
                    </p>
                </div>
            </div>

            {/* The effect, not the setting. Basis points are a storage format. */}
            <p className="mb-4 rounded-xl bg-foreground/[0.04] px-3 py-2 text-xs text-muted-foreground">
                A vendor line costing <span className="font-medium text-foreground">₹1.00</span> is
                charged at{" "}
                <span className="font-medium text-foreground">
                    ₹{(state.markup_bps / 10000).toFixed(2)}
                </span>
                .
            </p>

            {pending ? (
                <div className="rounded-xl border border-[color:var(--glass-amber-edge)] bg-amber-50/60 px-4 py-3 dark:bg-amber-950/20">
                    <p className="flex items-center gap-2 text-sm font-medium text-foreground">
                        <Mail className="h-4 w-4" />
                        Waiting for a code
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        {asMultiple(pending.previous_markup_bps)} →{" "}
                        <span className="font-medium text-foreground">
                            {asMultiple(pending.markup_bps)}
                        </span>{" "}
                        · sent to {state.notice_address} ·{" "}
                        {pending.attempts_remaining} attempt
                        {pending.attempts_remaining === 1 ? "" : "s"} left
                    </p>
                    <div className="mt-3 flex flex-wrap items-end gap-2">
                        <div className="space-y-1">
                            <Label htmlFor="markup-code" className="text-xs">
                                Confirmation code
                            </Label>
                            <Input
                                id="markup-code"
                                value={code}
                                onChange={(e) => setCode(e.currentTarget.value)}
                                placeholder="000000"
                                inputMode="numeric"
                                className="w-36 tracking-[0.3em]"
                            />
                        </div>
                        <Button
                            onClick={confirmChange}
                            disabled={busy || code.trim().length < 4}
                        >
                            {busy ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <ShieldCheck className="mr-2 h-4 w-4" />
                            )}
                            Apply change
                        </Button>
                    </div>
                </div>
            ) : (
                <div className="flex flex-wrap items-end gap-3">
                    <div className="space-y-1">
                        <Label htmlFor="markup-value" className="text-xs">
                            New multiple
                        </Label>
                        <Input
                            id="markup-value"
                            value={draft}
                            onChange={(e) => setDraft(e.currentTarget.value)}
                            inputMode="decimal"
                            className="w-28"
                        />
                    </div>
                    <div className="min-w-[200px] flex-1 space-y-1">
                        <Label htmlFor="markup-note" className="text-xs">
                            Why (optional)
                        </Label>
                        <Input
                            id="markup-note"
                            value={note}
                            onChange={(e) => setNote(e.currentTarget.value)}
                            placeholder="Recorded against the change"
                        />
                    </div>
                    <Button onClick={requestChange} disabled={busy || !draftValid}>
                        {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        Send me a code
                    </Button>
                </div>
            )}

            {!pending && !draftValid && draft !== "" && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Between {asMultiple(state.min_bps)} and {asMultiple(state.max_bps)},
                    and different from what is in force.
                </p>
            )}

            {notice && (
                <p className="mt-3 flex items-start gap-1.5 text-xs text-foreground">
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {notice}
                </p>
            )}
            {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

            {state.history.length > 0 && (
                <div className="mt-5 border-t border-border pt-3">
                    <p className="mb-2 text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                        Previously
                    </p>
                    <ul className="space-y-1 text-xs text-muted-foreground">
                        {state.history.map((row) => (
                            <li
                                key={`${row.effective_from}-${row.markup_bps}`}
                                className="flex flex-wrap items-center gap-2"
                            >
                                <Badge variant="outline" className="tabular-nums">
                                    {asMultiple(row.markup_bps)}
                                </Badge>
                                <span className="tabular-nums">
                                    from {row.effective_from.slice(0, 10)}
                                    {row.effective_to
                                        ? ` to ${row.effective_to.slice(0, 10)}`
                                        : " — in force"}
                                </span>
                                {row.note && <span>· {row.note}</span>}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </section>
    );
}
