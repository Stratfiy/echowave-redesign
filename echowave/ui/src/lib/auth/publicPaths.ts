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
  // The trust page: a security review happens before somebody signs up, so
  // the page answering it cannot be behind the signup.
  "/trust",
  // The embed widget's own script. It is loaded by the share page and by
  // every customer site that pastes the snippet, so it is never behind a
  // session. Guarded, the redirect to /auth/login answered with HTML under
  // `nosniff`, the browser refused to run it, and the script's `onload`
  // never fired — the share page sat on "Preparing…" forever.
  "/embed",
  // The public marketplace: a stranger browses what can be hired before
  // deciding to sign up. Hiring itself stays behind the account.
  "/agents",
  // Early access (screen 01): the waitlist and an invitation's own page are
  // read before an account exists. Both say nothing while `early_access` is
  // off, and the invitation page never grants access by itself.
  "/early-access",
  "/invite",
] as const;

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}
