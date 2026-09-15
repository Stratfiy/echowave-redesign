import { NextRequest, NextResponse } from "next/server";

import { getStackConfig } from "@/lib/auth/config";

import {
    IMPERSONATION_MAX_AGE,
    markerHeader,
    requestIsSecure,
    serializeSetCookie,
    sessionClearingHeaders,
} from "./session-cookies";

/**
 * Helper route that receives a Stack refresh token via query parameters, wipes
 * every Stack SDK session cookie the browser presented, stores the impersonated
 * session as a fresh cookie and finally redirects to the requested path.
 *
 * On HTTPS Chrome the Stack SDK writes its cookies into the CHIPS-partitioned
 * jar (`Secure; SameSite=None; Partitioned`), which coexists with the regular
 * jar under the same cookie name. A Set-Cookie without the `Partitioned`
 * attribute can neither delete nor overwrite the partitioned copy, and the
 * SDK's own cookie parsing is first-occurrence-wins — so a leftover partitioned
 * cookie from a previous session silently keeps winning over anything this
 * route sets in the regular jar, resurfacing the old user. Deletions below are
 * therefore emitted for BOTH jars (and for possible parent-domain scopes),
 * which is also why headers are appended manually: `response.cookies.set`
 * dedupes Set-Cookie by name.
 *
 * The fresh cookie is deliberately written only ONCE, with the `Partitioned`
 * attribute: CHIPS browsers store it in the partitioned jar and non-CHIPS
 * browsers ignore the attribute and store it in the regular jar — in both
 * cases exactly the jar the SDK's own writes will later overwrite. Writing
 * both jars instead would plant a copy the SDK never updates, recreating the
 * stale-session bug this route exists to fix.
 *
 * The session cookie lives ONE HOUR, the Stack session's own life, and a
 * marker cookie is set beside it so the shell can show that it is running as
 * somebody else and offer /impersonate/stop (KAN-82).
 *
 * Example usage (client side): a hidden form POSTing refresh_token,
 * redirect_path and who -- see lib/utils.ts.
 */

// POST, not GET (KAN-82): the refresh token arrives in the request body, never
// in the URL. A token in a query string lands in server access logs, the
// browser's address bar and history, and any Referer header a later navigation
// sends -- a session-stealing leak for the single most powerful credential in
// the product. A form POST navigates the browser (so the cookie is written
// first-party on this origin) while keeping the token out of every log.
export async function POST(request: NextRequest) {
    const form = await request.formData().catch(() => null);
    const refreshToken =
        typeof form?.get("refresh_token") === "string"
            ? (form.get("refresh_token") as string)
            : null;
    const redirectPath =
        (typeof form?.get("redirect_path") === "string"
            ? (form.get("redirect_path") as string)
            : null) ?? "/workflow/create";

    if (!refreshToken) {
        return new Response("Missing refresh_token", { status: 400 });
    }

    // The project id comes from the backend at runtime, so no inlined
    // NEXT_PUBLIC_* is needed.
    const stackConfig = await getStackConfig();
    if (!stackConfig) {
        return new Response("Stack auth is not configured", { status: 400 });
    }

    const fallbackRedirectUrl = new URL("/workflow/create", request.url);
    let redirectUrl = fallbackRedirectUrl.toString();
    try {
        const requestedRedirectUrl = new URL(redirectPath, request.url);
        if (requestedRedirectUrl.origin === request.nextUrl.origin) {
            redirectUrl = requestedRedirectUrl.toString();
        }
    } catch {
        // Malformed redirect_path (e.g. "https://") — keep the fallback.
    }

    // 303: the POST that carried the token becomes a plain GET at the
    // destination, so the app page loads normally with the fresh cookie.
    const response = NextResponse.redirect(redirectUrl, 303);

    const isSecure = requestIsSecure(request);
    const setCookieHeaders = sessionClearingHeaders(request);

    // Fresh impersonated session, written AFTER the deletions so it survives
    // them, in the name/shape Stack's nextjs-cookie token store reads. Single
    // write with Partitioned (see the header comment for why).
    const refreshCookieName = `${isSecure ? "__Host-" : ""}hexclave-refresh-${stackConfig.projectId}--default`;
    const refreshCookieValue = JSON.stringify({
        refresh_token: refreshToken,
        updated_at_millis: Date.now(),
    });
    setCookieHeaders.push(
        serializeSetCookie(refreshCookieName, refreshCookieValue, {
            maxAge: IMPERSONATION_MAX_AGE,
            secure: isSecure,
            partitioned: isSecure,
        }),
    );
    // And the marker the shell reads to show the banner and the way out
    // (/impersonate/stop). The "who" is a display hint only.
    const who =
        typeof form?.get("who") === "string" ? (form.get("who") as string) : "";
    setCookieHeaders.push(markerHeader(request, who.slice(0, 120) || "1"));

    for (const header of setCookieHeaders) {
        response.headers.append("set-cookie", header);
    }

    return response;
}
