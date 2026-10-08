/**
 * The staff area's first gate, run in middleware (phase 3).
 *
 * The superadmin layout used to be the only server-side refusal, by
 * throwing `redirect()` while the page streamed. Under the App Router that
 * intermittently crashed the client ("Rendered more hooks than during the
 * previous render" in Next's Router), so a workspace owner or member who
 * opened a staff URL could land on "Application error" instead of being sent
 * home. Here the answer is a plain 307 before any React renders.
 *
 * The same rule as `serverStaffRole`: only a definitive "not staff" (a 200
 * with no staff role) redirects; anything uncertain falls through to the
 * layout and the client gate, so a blip never bounces a real superadmin.
 * Not the security boundary either way: every staff API route is enforced
 * on the server.
 */

export const STAFF_PREFIX = "/superadmin";

export function isStaffPath(pathname: string): boolean {
    return pathname === STAFF_PREFIX || pathname.startsWith(`${STAFF_PREFIX}/`);
}

/** `true` only when the backend says, definitively, that this token is not staff. */
export async function definitelyNotStaff(
    backendUrl: string,
    token: string,
    fetcher: typeof fetch = fetch,
): Promise<boolean> {
    try {
        const res = await fetcher(`${backendUrl}/api/v1/user/auth/user`, {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
        });
        if (!res.ok) return false;
        const data = (await res.json()) as { staff_role?: string | null };
        return !(data?.staff_role === "support" || data?.staff_role === "superadmin");
    } catch {
        return false;
    }
}
