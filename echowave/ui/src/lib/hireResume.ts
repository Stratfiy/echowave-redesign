/**
 * Carrying a hire across signup.
 *
 * A visitor on the public marketplace clicks Hire on a role, and has no
 * account. Signup, the code screen and the landing that follows all have
 * their own idea of where to go next, and none of them knew the visitor
 * came to hire something — so they arrived on an empty workspace and had
 * to find the role again.
 *
 * The public page leaves a short-lived cookie naming the role; the landing
 * after sign-in reads it and goes to that role's hire flow. A cookie rather
 * than a query parameter because the landing is a server page reached through
 * three redirects, and rather than storage because the server has to read it.
 *
 * Only a hire path is ever honoured. Anything else in the cookie is ignored,
 * so it can never become a way to send somebody off-site after they sign in.
 */

export const HIRE_COOKIE = "decibyl_hire";

/** Half an hour: long enough to sign up and read a code from email. */
const MAX_AGE_SECONDS = 30 * 60;

const TEMPLATE_ID = /^[A-Za-z0-9_-]{1,64}$/;

/** The hire path for a template, or null if the id is not one we would make. */
export function hirePath(templateId: string | null | undefined): string | null {
    if (!templateId || !TEMPLATE_ID.test(templateId)) return null;
    return `/start?template=${templateId}`;
}

/** Remember which role a visitor meant to hire. Browser only. */
export function rememberHire(templateId: string): void {
    if (!hirePath(templateId) || typeof document === "undefined") return;
    document.cookie = `${HIRE_COOKIE}=${templateId}; Max-Age=${MAX_AGE_SECONDS}; Path=/; SameSite=Lax`;
}

/** Where to resume, from the cookie's value, or null. */
export function resumePath(cookieValue: string | null | undefined): string | null {
    return hirePath(cookieValue ?? null);
}
