/**
 * Where `/agents` sends somebody who is already signed in.
 *
 * `/agents` is the public marketplace, built for a stranger deciding whether
 * to sign up, and it is drawn with no app rail. Inside the app the word
 * "Agents" means the account's own agents (`/workflow`, the sidebar row of
 * that name), and the in-app marketplace lives at `/marketplace`. So a signed
 * in person typing `/agents` landed on a page with no way back to their work,
 * under a name that the sidebar uses for something else.
 *
 * - bare `/agents` -> their own agents, which is what the word means in-app;
 * - `/agents?q=...` or `?job=...` -> a marketplace search somebody followed
 *   from the public site, so the in-app marketplace, inside the shell.
 *
 * Role pages (`/agents/<slug>`) are not redirected: they are the shareable
 * page for one role and stay readable by anybody.
 */

export const SIGNED_IN_AGENTS_HOME = "/workflow";
export const SIGNED_IN_MARKETPLACE = "/marketplace";

export function signedInAgentsDestination(params: { q?: string; job?: string }): string {
    const searching = Boolean(params.q?.trim() || params.job?.trim());
    return searching ? SIGNED_IN_MARKETPLACE : SIGNED_IN_AGENTS_HOME;
}
