/**
 * Paths a visitor may open with no account.
 *
 * One list, read by the edge middleware and by the local auth wrapper. They
 * used to disagree: the middleware let /talk through and the wrapper, on
 * its first 401, sent the visitor to /auth/login anyway — so the share link
 * a prospect was texted asked them to sign in.
 */
export const PUBLIC_PATHS = [
  "/auth/login",
  "/auth/signup",
  "/auth/google",
  "/auth/forgot",
  // The public share page: a prospect talks to an agent, no account.
  "/talk",
  // The embed widget's own script. It is loaded by the share page and by
  // every customer site that pastes the snippet, so it is never behind a
  // session. Guarded, the redirect to /auth/login answered with HTML under
  // `nosniff`, the browser refused to run it, and the script's `onload`
  // never fired — the share page sat on "Preparing…" forever.
  "/embed",
] as const;

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}
