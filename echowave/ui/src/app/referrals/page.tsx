"use client";

/**
 * Refer a friend.
 *
 * The screen has one job the copy has to get right: a referrer looks here the
 * day after somebody signs up, sees nothing, and concludes it is broken. So it
 * says plainly that the reward is earned when the friend first *pays*, and
 * sits for a week after that before it can be spent.
 *
 * Three figures rather than a total, because they answer different questions —
 * what is mine, what is coming, and what did not survive. A single "earned"
 * number would either promise money that was clawed back, or hide money that
 * is on its way.
 */

import { AlertTriangle, Check, Copy, Gift, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    applyReferralCodeApiV1ReferralsApplyPost,
    getReferralsApiV1ReferralsGet,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";

type Award = {
    amount_paise: number;
    earned_at: string | null;
    available_at: string | null;
    status: "held" | "credited" | "cancelled";
};

type Summary = {
    code: string | null;
    share_bps: number;
    hold_days: number;
    signed_up: number;
    credited_paise: number;
    pending_paise: number;
    cancelled_paise: number;
    awards: Award[];
};

const STATUS_LABEL: Record<Award["status"], string> = {
    held: "Held",
    credited: "In your balance",
    cancelled: "Cancelled",
};

export default function ReferralsPage() {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [data, setData] = useState<Summary | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);

    const [code, setCode] = useState("");
    const [applying, setApplying] = useState(false);

    const load = useCallback(async () => {
        const result = await getReferralsApiV1ReferralsGet();
        if (result.error) {
            setError(detailFromError(result.error, "Could not load your referrals"));
        } else {
            setData(result.data as unknown as Summary);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const copy = async () => {
        if (!data?.code) return;
        await navigator.clipboard.writeText(data.code);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const apply = async () => {
        if (!code.trim()) return;
        setApplying(true);
        const result = await applyReferralCodeApiV1ReferralsApplyPost({
            body: { code: code.trim() },
        });
        if (result.error) {
            toast.error(detailFromError(result.error, "That code could not be applied"));
        } else {
            toast.success("Referral code applied.");
            setCode("");
            await load();
        }
        setApplying(false);
    };

    if (loading) {
        return (
            <div className="mx-auto w-full max-w-[900px] px-6 pt-8">
                <Skeleton className="h-64 w-full rounded-2xl" />
            </div>
        );
    }

    const share = data ? Math.round(data.share_bps / 100) : 20;

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[900px] px-6 pb-10 pt-8">
                <header className="mb-6">
                    <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                        Refer a friend
                    </h1>
                    <p className="mt-1.5 max-w-2xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                        Share your code. When someone who used it makes their first
                        payment, {share}% of it becomes credit on your account.
                    </p>
                </header>

                {error && (
                    <section className="glass-panel mb-5 flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                        <AlertTriangle className="h-4 w-4 shrink-0" />
                        {error}
                    </section>
                )}

                <Card className="mb-5">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-[0.9375rem]">Your code</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap items-center gap-3">
                            <code className="rounded-xl bg-foreground/[0.06] px-4 py-2.5 text-lg font-semibold tracking-[0.18em] text-foreground">
                                {data?.code ?? "—"}
                            </code>
                            <Button variant="outline" onClick={() => void copy()}>
                                {copied ? (
                                    <Check className="mr-2 h-4 w-4" />
                                ) : (
                                    <Copy className="mr-2 h-4 w-4" />
                                )}
                                {copied ? "Copied" : "Copy"}
                            </Button>
                        </div>
                        {/* The sentence that stops "it is broken". A referrer
                            checks the day after a friend signs up and sees
                            nothing, because signing up is not the trigger. */}
                        <p className="mt-3 max-w-xl text-xs leading-relaxed text-muted-foreground">
                            The reward is earned when your friend makes their first
                            payment — not when they sign up — and is held for{" "}
                            {data?.hold_days ?? 7} days before it can be spent, so a
                            refunded payment can be taken back before anyone has used
                            it.
                        </p>
                    </CardContent>
                </Card>

                <div className="mb-5 grid gap-3 sm:grid-cols-3">
                    <Card>
                        <CardContent className="pt-5">
                            <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                In your balance
                            </p>
                            <p className="mt-1 text-2xl font-semibold tabular-nums tracking-[-0.03em]">
                                {formatPaise(data?.credited_paise ?? 0)}
                            </p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-5">
                            <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                On its way
                            </p>
                            <p className="mt-1 text-2xl font-semibold tabular-nums tracking-[-0.03em]">
                                {formatPaise(data?.pending_paise ?? 0)}
                            </p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardContent className="pt-5">
                            <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                                Signed up
                            </p>
                            <p className="mt-1 text-2xl font-semibold tabular-nums tracking-[-0.03em]">
                                {data?.signed_up ?? 0}
                            </p>
                        </CardContent>
                    </Card>
                </div>

                {(data?.awards.length ?? 0) > 0 && (
                    <Card className="mb-5">
                        <CardHeader className="pb-3">
                            <CardTitle className="text-[0.9375rem]">Rewards</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <ul className="space-y-2 text-sm">
                                {data?.awards.map((award) => (
                                    <li
                                        key={`${award.earned_at}-${award.amount_paise}`}
                                        className="flex flex-wrap items-center gap-2"
                                    >
                                        <Gift className="h-4 w-4 shrink-0 text-muted-foreground" />
                                        <span className="font-medium tabular-nums">
                                            {formatPaise(award.amount_paise)}
                                        </span>
                                        <Badge
                                            variant={
                                                award.status === "credited"
                                                    ? "default"
                                                    : "outline"
                                            }
                                        >
                                            {STATUS_LABEL[award.status]}
                                        </Badge>
                                        {award.status === "held" && award.available_at && (
                                            <span className="text-xs text-muted-foreground">
                                                available {award.available_at.slice(0, 10)}
                                            </span>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        </CardContent>
                    </Card>
                )}

                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-[0.9375rem]">
                            Were you referred?
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="min-w-[200px] space-y-1.5">
                                <Label htmlFor="referral-code" className="text-xs">
                                    Their code
                                </Label>
                                <Input
                                    id="referral-code"
                                    value={code}
                                    onChange={(e) => setCode(e.target.value)}
                                    placeholder="ABCD2345"
                                    className="tracking-[0.18em] uppercase"
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") void apply();
                                    }}
                                />
                            </div>
                            <Button
                                variant="outline"
                                onClick={() => void apply()}
                                disabled={applying || !code.trim()}
                            >
                                {applying && (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                )}
                                Apply code
                            </Button>
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">
                            Can be set once, before your first payment.
                        </p>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
