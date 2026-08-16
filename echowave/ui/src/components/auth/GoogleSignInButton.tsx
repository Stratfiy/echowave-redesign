"use client";

/**
 * The Google half of the login and signup pages.
 *
 * Renders nothing at all when the deployment has no Google credentials
 * configured — a button that always fails after the user has already left the
 * site is worse than no button, and an air-gapped install has no Google to
 * reach. The check is the same one the backend makes, so the two cannot
 * disagree: `/auth/google/start` answers 503 when it is not configured, and we
 * simply do not render on that.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useAppConfig } from "@/context/AppConfigContext";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";

function GoogleMark() {
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

export function GoogleSignInButton({
    label = "Continue with Google",
    referralCode,
}: {
    label?: string;
    /** A partner's code, from `?ref=` on the signup link.
     *
     *  Passed to `/auth/google/start`, which folds it into the signed OAuth
     *  state. That is the only thing that survives the round trip through
     *  Google's own page, so without it a partner's link would attribute every
     *  password signup and no Google one. */
    referralCode?: string | null;
}) {
    const [available, setAvailable] = useState<boolean | null>(null);
    const [starting, setStarting] = useState(false);
    const { config } = useAppConfig();

    // Resolved the same way every other API call in the app resolves it.
    //
    // This used to read NEXT_PUBLIC_API_BASE_URL, a name defined nowhere in the
    // repo, so it was always the empty string and every request went to the
    // app's own origin. In production that happens to work — nginx on the app
    // host proxies /api/v1 to the API — which is exactly why it survived: it
    // fails only where the two are served separately, such as `npm run dev` on
    // :3000 against an API on :8000. There the probe 404s, res.ok is false, and
    // the button silently does not render at all.
    const apiBase = resolveBrowserBackendUrl(config?.backendApiEndpoint);

    useEffect(() => {
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
    }, [apiBase]);

    if (available !== true) return null;

    const start = async () => {
        setStarting(true);
        try {
            const query = referralCode
                ? `?ref=${encodeURIComponent(referralCode)}`
                : "";
            const res = await fetch(
                `${apiBase}/api/v1/auth/google/start${query}`,
            );
            const body = await res.json();
            if (!res.ok || !body.authorization_url) {
                toast.error(body.detail || "Could not start sign-in with Google");
                setStarting(false);
                return;
            }
            // A full navigation, not a fetch: the consent screen is Google's
            // page and has to own the tab.
            window.location.href = body.authorization_url;
        } catch {
            toast.error("Could not reach Google. Try again.");
            setStarting(false);
        }
    };

    return (
        <div className="space-y-4">
            <Button
                type="button"
                variant="outline"
                className="w-full gap-2"
                onClick={start}
                disabled={starting}
                data-testid="google-signin-button"
            >
                <GoogleMark />
                {starting ? "Redirecting…" : label}
            </Button>

            <div className="flex items-center gap-3">
                <span className="h-px flex-1 bg-border" />
                <span className="text-xs text-muted-foreground">or</span>
                <span className="h-px flex-1 bg-border" />
            </div>
        </div>
    );
}
