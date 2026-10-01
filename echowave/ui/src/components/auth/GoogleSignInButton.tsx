"use client";

/**
 * The Google half of the sign-in and sign-up screens.
 *
 * Renders nothing at all when the deployment has no Google credentials
 * configured — a button that always fails after the user has already left the
 * site is worse than no button, and an air-gapped install has no Google to
 * reach. The check is the same one the backend makes, so the two cannot
 * disagree: `/auth/google/start` answers 503 when it is not configured, and we
 * simply do not render on that.
 */

import type { ReactNode } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { GoogleMark, type GoogleSignIn, useGoogleSignIn } from "./useGoogleSignIn";

export function GoogleSignInButton({
    label = "Continue with Google",
    referralCode,
    inviteCode,
    notice,
    onStart,
    google: provided,
    divider = true,
    className,
}: {
    label?: string;
    /** The click-wrap line for this button: pressing it accepts the same
     *  documents the form's tick does. It renders here rather than beside the
     *  caller's form because it is only true when the button is. */
    notice?: ReactNode;
    /** A partner's code, from `?ref=` on the signup link, folded into the
     *  signed OAuth state so Google signups are attributed too. */
    referralCode?: string | null;
    /** The invite code (INVITE-1), carried in the signed state the same way
     *  so a new Google account can redeem it on the way back. */
    inviteCode?: string | null;
    /** Take over the click, e.g. to ask for an invite code before leaving. */
    onStart?: () => void;
    /** A shared `useGoogleSignIn()` so the caller and the button agree on
     *  availability; one is made here when absent. */
    google?: GoogleSignIn;
    /** The "or" rule under the button, for callers that put a form below. */
    divider?: boolean;
    className?: string;
}) {
    const own = useGoogleSignIn(!provided);
    const google = provided ?? own;

    if (google.available !== true) return null;

    const start = async () => {
        if (onStart) {
            onStart();
            return;
        }
        const error = await google.start({ referralCode, inviteCode });
        if (error) toast.error(error);
    };

    return (
        <div className={cn("space-y-4", className)}>
            <Button
                type="button"
                variant="outline"
                className="h-11 w-full gap-2"
                onClick={() => void start()}
                disabled={google.starting}
                data-testid="google-signin-button"
            >
                <GoogleMark />
                {google.starting ? "Redirecting…" : label}
            </Button>

            {notice}

            {divider && (
                <div className="flex items-center gap-3" aria-hidden="true">
                    <span className="h-px flex-1 bg-border" />
                    <span className="text-xs text-muted-foreground">or</span>
                    <span className="h-px flex-1 bg-border" />
                </div>
            )}
        </div>
    );
}
