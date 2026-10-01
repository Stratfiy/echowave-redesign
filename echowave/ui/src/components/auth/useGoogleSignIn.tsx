"use client";

/**
 * Google sign-in without the button: whether this deployment offers it, and
 * a `start` that carries the partner and invite codes through Google's page.
 *
 * Split out of `GoogleSignInButton` so the stepped sign-up can ask for the
 * invite code first and only then leave for Google — the code has to be in
 * the signed OAuth state, because that state is the only thing that survives
 * the round trip.
 */

import { useCallback, useEffect, useState } from "react";

import { useAppConfig } from "@/context/AppConfigContext";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";

export interface GoogleStartOptions {
    referralCode?: string | null;
    inviteCode?: string | null;
}

export interface GoogleSignIn {
    /** null while the probe is in flight; false where Google is not configured. */
    available: boolean | null;
    starting: boolean;
    /** Leaves for Google on success. Resolves to the error to show otherwise. */
    start: (options?: GoogleStartOptions) => Promise<string | null>;
}

/** `probe: false` skips the availability check, for a component handed
 *  another instance's answer. */
export function useGoogleSignIn(probe = true): GoogleSignIn {
    const [available, setAvailable] = useState<boolean | null>(null);
    const [starting, setStarting] = useState(false);
    const { config } = useAppConfig();

    // Resolved the same way every other API call in the app resolves it, so a
    // dev box with the API on another port still finds `/auth/google/start`.
    const apiBase = resolveBrowserBackendUrl(config?.backendApiEndpoint);

    useEffect(() => {
        if (!probe) return;
        let cancelled = false;
        (async () => {
            try {
                const res = await fetch(`${apiBase}/api/v1/auth/google/start`);
                if (!cancelled) setAvailable(res.ok);
            } catch {
                if (!cancelled) setAvailable(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [apiBase, probe]);

    const start = useCallback(
        async ({ referralCode, inviteCode }: GoogleStartOptions = {}) => {
            setStarting(true);
            try {
                const params = new URLSearchParams();
                if (referralCode) params.set("ref", referralCode);
                if (inviteCode) params.set("invite", inviteCode);
                const query = params.toString() ? `?${params.toString()}` : "";
                const res = await fetch(`${apiBase}/api/v1/auth/google/start${query}`);
                const body = await res.json();
                if (!res.ok || !body.authorization_url) {
                    setStarting(false);
                    return (body.detail as string | undefined) || "Could not start sign-in with Google";
                }
                // A full navigation, not a fetch: the consent screen is
                // Google's page and has to own the tab.
                window.location.href = body.authorization_url;
                return null;
            } catch {
                setStarting(false);
                return "Could not reach Google. Try again.";
            }
        },
        [apiBase],
    );

    return { available, starting, start };
}

export function GoogleMark() {
    // Google's four-colour mark. Inline rather than an <img> so it is not a
    // network request that a blocked CDN turns into an empty box.
    return (
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"/>
            <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/>
            <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"/>
            <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/>
        </svg>
    );
}
