import type { NextRequest } from "next/server";

/**
 * What the two impersonation routes share: which cookies hold a Stack
 * session, how to write a Set-Cookie that the SDK's own parsing will honour,
 * and the marker that tells the app it is running as somebody else.
 *
 * Read `route.ts` for the CHIPS story -- the partitioned jar is why deletions
 * are emitted for both jars and every parent domain, and why the fresh cookie
 * is written exactly once.
 */

// Stack SDK cookies that hold session identity: hexclave/stack access cookies
// and every refresh-cookie variant (bare legacy, project-scoped legacy,
// --default, --custom-<domain>, __Host- prefixed). Deliberately excludes
// non-identity SDK cookies (is-https flags, in-flight OAuth state) so an
// impersonation redirect can't abort an unrelated concurrent sign-in.
export const SESSION_COOKIE_RE =
  /^(?:__Host-)?(?:stack|hexclave)-(?:access|refresh)(?:-|$)/;

// Set beside the borrowed session so the shell can say so (KAN-82). Readable
// from document.cookie on purpose: the banner is a client component and the
// cookie carries nothing secret -- only a hint of who is being impersonated.
export const IMPERSONATION_MARKER = "decibyl-impersonating";

// One hour, matching the Stack session itself (api/services/auth/stack_auth.py).
// A borrowed session that outlived the real one by a year was the finding.
export const IMPERSONATION_MAX_AGE = 60 * 60;

/**
 * Domains a cookie could have been scoped to from this host, e.g.
 * "app.decibyl.ai" -> ["app.decibyl.ai", "decibyl.ai"]. Returns [] for
 * localhost / IP hosts. Stops before the last label, which over-generates for
 * multi-label public suffixes (app.example.co.uk also yields co.uk) -- the
 * browser just rejects those deletions, so the cost is a wasted header.
 */
export function parentDomains(hostname: string): string[] {
  if (!hostname.includes(".") || /^[\d.]+$/.test(hostname)) {
    return [];
  }
  const parts = hostname.split(".");
  const domains: string[] = [];
  for (let i = 0; i + 2 <= parts.length; i++) {
    domains.push(parts.slice(i).join("."));
  }
  return domains;
}

export interface SetCookieAttrs {
  maxAge: number;
  secure?: boolean;
  domain?: string;
  partitioned?: boolean;
}

// No HttpOnly: the Stack SDK reads these cookies from document.cookie.
export function serializeSetCookie(
  name: string,
  value: string,
  attrs: SetCookieAttrs,
): string {
  const parts = [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    `Max-Age=${attrs.maxAge}`,
  ];
  if (attrs.domain) {
    parts.push(`Domain=${attrs.domain}`);
  }
  if (attrs.partitioned) {
    // CHIPS requires Secure and SameSite=None.
    parts.push("Secure", "SameSite=None", "Partitioned");
  } else {
    if (attrs.secure) {
      parts.push("Secure");
    }
    parts.push("SameSite=Lax");
  }
  return parts.join("; ");
}

export function requestIsSecure(request: NextRequest): boolean {
  const forwardedProto = request.headers
    .get("x-forwarded-proto")
    ?.split(",")[0]
    ?.trim()
    .toLowerCase();
  return request.nextUrl.protocol === "https:" || forwardedProto === "https";
}

/**
 * Set-Cookie headers that delete every session cookie the request presented,
 * in every scope a stale SDK cookie may live in: host-only plus each parent
 * domain, each in the regular jar and (on https) its partitioned twin. The
 * request's Cookie header is the complete list of names to clear: the SDK
 * only sets Lax or None+Partitioned cookies, both of which the browser
 * attaches to a top-level navigation.
 */
export function sessionClearingHeaders(request: NextRequest): string[] {
  const isSecure = requestIsSecure(request);
  const domains: (string | undefined)[] = [
    undefined,
    ...parentDomains(request.nextUrl.hostname),
  ];
  const jars = isSecure ? [false, true] : [false];
  const headers: string[] = [];
  for (const cookie of request.cookies.getAll()) {
    if (!SESSION_COOKIE_RE.test(cookie.name)) {
      continue;
    }
    const isHostPrefixed = cookie.name.startsWith("__Host-");
    for (const partitioned of jars) {
      for (const domain of domains) {
        if (isHostPrefixed && domain) {
          continue; // __Host- cookies never have a Domain attribute
        }
        headers.push(
          serializeSetCookie(cookie.name, "", {
            maxAge: 0,
            secure: isHostPrefixed || isSecure,
            domain,
            partitioned,
          }),
        );
      }
    }
  }
  return headers;
}

/** The marker, written or cleared. Regular jar only: the shell reads it. */
export function markerHeader(
  request: NextRequest,
  value: string | null,
): string {
  return serializeSetCookie(IMPERSONATION_MARKER, value ?? "", {
    maxAge: value === null ? 0 : IMPERSONATION_MAX_AGE,
    secure: requestIsSecure(request),
  });
}
