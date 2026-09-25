"use client";

/**
 * What things cost, in credits (D-1, the charge rule of 25 September 2026).
 *
 * Both pieces are shown only while the `charge_rule` flag is on: the rate
 * card as a table, read from `GET /billing/rate-card` so the screen prints
 * exactly the figures the ledger charges; and a line under the balance that
 * turns credits into things a person recognises -- replies and voice
 * minutes -- at the card's two headline figures.
 */

import { useEffect, useState } from "react";

import { getRateCardApiV1BillingRateCardGet } from "@/client/sdk.gen";
import type { RateCardResponse } from "@/client/types.gen";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { PAISE_PER_CREDIT } from "@/lib/billing/format";
import { useFeature } from "@/lib/features";

/** The card's headline figures, for the runway line. A reply is 1 credit and
 *  a standard voice minute 12 on every plan. */
export const CREDITS_PER_REPLY = 1;
export const CREDITS_PER_VOICE_MINUTE = 12;

const integers = new Intl.NumberFormat("en-IN");

export function RunwayLine({ balancePaise }: { balancePaise: number }) {
    const on = useFeature("charge_rule");
    if (!on) return null;
    const credits = Math.max(0, Math.trunc(balancePaise / PAISE_PER_CREDIT));
    const replies = Math.floor(credits / CREDITS_PER_REPLY);
    const minutes = Math.floor(credits / CREDITS_PER_VOICE_MINUTE);
    return (
        <p className="mt-1 text-sm text-muted-foreground">
            {`≈ ${integers.format(replies)} replies · ≈ ${integers.format(minutes)} voice minutes left`}
        </p>
    );
}

export function RateCardSection() {
    const on = useFeature("charge_rule");
    const [card, setCard] = useState<RateCardResponse | null>(null);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        if (!on) return;
        let cancelled = false;
        void (async () => {
            try {
                const response = await getRateCardApiV1BillingRateCardGet();
                if (cancelled) return;
                if (response.error || !response.data) {
                    setFailed(true);
                    return;
                }
                setCard(response.data);
            } catch {
                if (!cancelled) setFailed(true);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [on]);

    if (!on) return null;
    const rupees = card ? (card.paise_per_credit / 100).toFixed(2) : "0.50";

    return (
        <section className="rounded-xl border bg-card p-6" aria-labelledby="rate-card-heading">
            <h2 id="rate-card-heading" className="text-base font-semibold">
                What things cost
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
                1 credit = ₹{rupees}.
                {card?.standard_tokens_per_event
                    ? ` A reply includes ${integers.format(card.standard_tokens_per_event)} tokens on a standard model; a premium model adds its tokens at ${card.premium_model_multiplier}× their cost.`
                    : ""}
            </p>
            {failed && (
                <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
                    Could not load the rate card. Refresh to try again.
                </p>
            )}
            {card && (
                <Table className="mt-4">
                    <TableHeader>
                        <TableRow>
                            <TableHead>What</TableHead>
                            <TableHead className="text-right">Credits</TableHead>
                            <TableHead>Per</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {card.lines.map((line) => (
                            <TableRow key={line.key}>
                                <TableCell>
                                    <div className="text-sm">{line.label}</div>
                                    {line.notes && (
                                        <div className="text-xs text-muted-foreground">{line.notes}</div>
                                    )}
                                </TableCell>
                                <TableCell className="text-right tabular-nums">
                                    {line.credits === null || line.credits === undefined
                                        ? "Model tokens"
                                        : integers.format(line.credits)}
                                </TableCell>
                                <TableCell className="text-sm text-muted-foreground">
                                    {line.unit.replace(/^per /, "")}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            )}
            {card && (
                <p className="mt-3 text-xs text-muted-foreground">Rate card of {card.version}.</p>
            )}
        </section>
    );
}
