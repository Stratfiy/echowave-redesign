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

                const res = await fetch("/api/auth/session", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ token, user: user.data ?? null }),
                });
                if (!res.ok) throw new Error("session");

                window.location.href = next || "/after-sign-in";
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
