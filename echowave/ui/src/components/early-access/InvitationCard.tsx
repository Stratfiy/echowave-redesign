"use client";

/**
 * What an invitation link says (screen 01), before anything is spent.
 *
 * Valid shows the bound address (masked) and the expiry, and Accept goes to
 * sign-up with the code, where the server checks it again -- this page
 * never grants access itself. Expired, withdrawn, used and unknown links
 * each say so and offer "Request a new invitation" in place, never the
 * application behind them.
 */

import { Clock, Loader2, MailCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { inviteStatusApiV1PublicEarlyAccessInvitesCodeGet } from "@/client/sdk.gen";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";

import { INVITATION_COPY as COPY } from "./copy";
import { WaitlistForm } from "./WaitlistForm";

type InviteState = "valid" | "expired" | "revoked" | "used" | "invalid";
type Status = { state: InviteState; email_hint?: string | null; expires_at?: string | null; code?: string | null };

function formatExpiry(iso: string): string {
    const date = new Date(iso);
    return date.toLocaleString(undefined, { day: "numeric", month: "long", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function InvitationCard({ code }: { code: string }) {
    const [status, setStatus] = useState<Status | null>(null);
    const [failed, setFailed] = useState(false);
    const [checking, setChecking] = useState(true);
    const [requesting, setRequesting] = useState(false);

    const check = useCallback(async () => {
        setChecking(true);
        setFailed(false);
        try {
            const response = await inviteStatusApiV1PublicEarlyAccessInvitesCodeGet({ path: { code } });
            if (response.error || !response.data) {
                setFailed(true);
                return;
            }
            setStatus(response.data as Status);
        } catch {
            setFailed(true);
        } finally {
            setChecking(false);
        }
    }, [code]);

    useEffect(() => {
        void check();
    }, [check]);

    if (checking && !status) {
        return (
            <p role="status" className="flex items-center gap-2 text-base text-muted-foreground">
                <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                {COPY.loading}
            </p>
        );
    }
    if (failed || !status) {
        return <ErrorState title={COPY.failed} onRetry={() => void check()} retrying={checking} />;
    }

    if (status.state === "valid") {
        const accept = `/auth/signup?invite=${encodeURIComponent(status.code ?? code)}`;
        return (
            <section className="motion-m2-enter rounded-[var(--radius)] border border-border p-5" data-testid="invitation" data-state="valid">
                <h2 className="flex items-center gap-2 text-lg font-semibold">
                    <MailCheck aria-hidden className="h-5 w-5" />
                    {COPY.valid.title}
                </h2>
                <p className="mt-2 text-base leading-[26px] text-muted-foreground">{COPY.valid.body}</p>
                <ul className="mt-3 flex flex-col gap-1 text-sm">
                    {status.email_hint && <li>{COPY.valid.boundTo(status.email_hint)}</li>}
                    {status.expires_at && (
                        <li className="flex items-center gap-1.5">
                            <Clock aria-hidden className="h-3.5 w-3.5" />
                            {COPY.valid.until(formatExpiry(status.expires_at))}
                        </li>
                    )}
                </ul>
                <Button asChild className="motion-m1 mt-5 min-h-11 w-full text-base">
                    <Link href={accept}>{COPY.valid.accept}</Link>
                </Button>
            </section>
        );
    }

    const said = COPY[status.state];
    return (
        <section className="motion-m2-enter" data-testid="invitation" data-state={status.state}>
            <div role="status" className="rounded-[var(--radius)] border border-border p-5">
                <h2 className="text-lg font-semibold">{said.title}</h2>
                <p className="mt-2 text-base leading-[26px] text-muted-foreground">{said.body}</p>
                {status.state === "used" && (
                    <Button asChild variant="outline" className="motion-m1 mt-4 min-h-11">
                        <Link href="/auth/login">Sign in</Link>
                    </Button>
                )}
            </div>
            {requesting ? (
                <div className="mt-6">
                    <WaitlistForm renewal />
                </div>
            ) : (
                <Button type="button" className="motion-m1 mt-4 min-h-11 w-full text-base" onClick={() => setRequesting(true)}>
                    {COPY.requestNew}
                </Button>
            )}
        </section>
    );
}

export default InvitationCard;
