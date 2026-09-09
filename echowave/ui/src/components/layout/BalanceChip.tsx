"use client";

/**
 * What the account has left to spend, on every screen.
 *
 * Prepaid is only honest if the balance is visible. Until this existed the
 * number lived on the Billing page alone, so the first time most people saw it
 * was after a call was refused — at which point the question is not "how much
 * is left" but "why did that fail". Both platforms we are measured against
 * keep it pinned: Bolna in the header, Vapi above the nav, each with a way to
 * add more next to it.
 *
 * Credits rather than rupees. One credit is one rupee — see formatCredits for
 * why the peg is 1:1 and public — and the unit lets a domestic and an export
 * account read the same number.
 */

import { Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { getBalanceApiV1BillingBalanceGet } from "@/client/sdk.gen";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCredits, formatPaise } from "@/lib/billing/format";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

/** How often the chip re-reads the balance while the tab is open. */
const REFRESH_MS = 60_000;

export function BalanceChip() {
    const { user, loading: authLoading } = useAuth();
    const [paise, setPaise] = useState<number | null>(null);
    const [blocked, setBlocked] = useState(false);
    const [lowBalance, setLowBalance] = useState(false);
    const [failed, setFailed] = useState(false);
    const hasFetched = useRef(false);

    const load = useCallback(async () => {
        const response = await getBalanceApiV1BillingBalanceGet();
        if (response.error) {
            // No banner and no zero. A balance we could not read is not a
            // balance of nothing, and showing "0" here would tell someone
            // their account is empty when it may be full.
            setFailed(true);
            return;
        }
        const data = response.data as
            | {
                  balance_paise?: number;
                  min_balance_paise?: number;
                  calling_blocked?: boolean;
              }
            | undefined;
        if (typeof data?.balance_paise === "number") {
            setPaise(data.balance_paise);
            setFailed(false);
            // Whether calling is possible is the server's answer, not ours —
            // it is derived once so the banner, the button and the runtime
            // that refuses a call all agree. "Running low" is a warning this
            // chip owns, and is a different question.
            setBlocked(data.calling_blocked === true);
            const floor = data.min_balance_paise;
            setLowBalance(typeof floor === "number" && data.balance_paise <= floor * 5);
        }
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
        const timer = setInterval(() => void load(), REFRESH_MS);
        return () => clearInterval(timer);
    }, [authLoading, user, load]);

    // Nothing to say yet, and a skeleton in the header is more distracting
    // than an empty space for the second it takes to arrive.
    if (authLoading || !user || (paise === null && !failed)) return null;

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <Link
                    href="/billing"
                    aria-label={
                        failed
                            ? "Balance unavailable — open billing"
                            : `${formatCredits(paise)} credits remaining — add credits`
                    }
                    className={cn(
                        "flex h-8 items-center gap-1.5 rounded-full border border-border px-2.5 text-sm tabular-nums transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        (blocked || lowBalance) &&
                            "border-destructive/40 text-destructive",
                    )}
                >
                    {failed ? (
                        <span className="text-muted-foreground">Balance —</span>
                    ) : (
                        <>
                            <span className="font-medium">{formatCredits(paise)}</span>
                            <span className="text-muted-foreground">credits</span>
                        </>
                    )}
                    <Plus className="h-3.5 w-3.5 text-muted-foreground" />
                </Link>
            </TooltipTrigger>
            <TooltipContent>
                {failed
                    ? "Could not read your balance. Open billing to check."
                    : blocked
                      ? `Too low to place calls — ${formatPaise(paise)}. Add credits to start calling again.`
                      : lowBalance
                        ? `Running low — ${formatPaise(paise)}. Calls stop when this reaches the minimum.`
                        : `${formatPaise(paise)} of call credit. One credit is ₹1.`}
            </TooltipContent>
        </Tooltip>
    );
}

export default BalanceChip;
