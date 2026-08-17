"use client";

/**
 * Where Google sign-in lands after the backend has verified it.
 *
 * The backend cannot set the session cookie itself: it is httpOnly and set by
 * a Next route on this origin, and the browser arrives here from Google rather
 * than from our own fetch. So the callback hands the token over in the URL and
 * this page does the same two steps the password form does — exchange it for a
 * cookie, then go to /after-sign-in.
 *
 * The token is stripped from the address bar immediately. It is a live session
 * credential, and leaving it in history, in a shared screenshot, or in the
 * Referer of the next request is the sort of leak nobody notices until it is
 * being used.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { getAuthUserApiV1UserAuthUserGet } from "@/client/sdk.gen";

/**
 * Where we are willing to send the browser after a sign-in.
 *
 * `next` travels from a query parameter on `/auth/google/start`, through the
 * signed OAuth state, to here — and signing it proves *we* issued it, not that
 * it is safe. Anyone can call `/auth/google/start?next=https://evil.example`
 * and get back a genuine Google authorization URL; the victim signs in for
 * real and is handed straight to the attacker's page, at the exact moment they
 * are most likely to trust what they are looking at and re-enter a credential.
 *
 * So: same-origin paths only. A leading `//` or `/\` is rejected because the
 * browser reads both as protocol-relative — `//evil.example` is a different
 * host, not a path on this one.
 */
function safeNext(next: string | null): string {
    const fallback = "/after-sign-in";
    if (!next) return fallback;
    if (!next.startsWith("/")) return fallback;
    if (next.startsWith("//") || next.startsWith("/\\")) return fallback;
    return next;
}

function Handoff() {
    const params = useSearchParams();
    const router = useRouter();
    const [error, setError] = useState<string | null>(null);
    const ran = useRef(false);

    useEffect(() => {
        if (ran.current) return;
        ran.current = true;

        const token = params.get("token");
        const next = params.get("next");

        if (!token) {
            setError("That sign-in link was incomplete. Please try again.");
            return;
        }

        // Out of the address bar before anything else awaits.
        window.history.replaceState({}, "", "/auth/google");

        (async () => {
            try {
                const user = await getAuthUserApiV1UserAuthUserGet({
                    headers: { Authorization: `Bearer ${token}` },
                });

                // Checked explicitly. The generated client resolves to
                // `{data, error}` and does NOT throw on a 4xx/5xx, so the
                // previous `user.data ?? null` turned a failed identity lookup
                // into a *successful* sign-in carrying `user: null`. The
                // session cookie was then written with the literal string
                // "null", `getOSSUser` threw on every subsequent read, and the
                // app rendered as signed-out-but-not-redirected — permanently,
                // with no error ever shown and no way back except clearing
                // cookies by hand.
                if (user.error || !user.data) throw new Error("identity");

                const res = await fetch("/api/auth/session", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ token, user: user.data }),
                });
                if (!res.ok) throw new Error("session");

                window.location.href = safeNext(next);
            } catch {
                setError("We could not finish signing you in. Please try again.");
            }
        })();
    }, [params, router]);

    if (error) {
        return (
            <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
                <p className="text-sm text-muted-foreground">{error}</p>
                <a href="/auth/login" className="text-sm font-medium underline">
                    Back to sign in
                </a>
            </div>
        );
    }

    return (
        <div className="flex min-h-screen items-center justify-center p-6">
            <p className="text-sm text-muted-foreground">Signing you in…</p>
        </div>
    );
}

export default function GoogleCallbackPage() {
    return (
        <Suspense
            fallback={
                <div className="flex min-h-screen items-center justify-center p-6">
                    <p className="text-sm text-muted-foreground">Signing you in…</p>
                </div>
            }
        >
            <Handoff />
        </Suspense>
    );
}
