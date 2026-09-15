import "server-only";

import { getServerBackendUrl } from "@/lib/apiClient";
import logger from "@/lib/logger";

import { getServerAccessToken } from "./server";

/**
 * The signed-in person's staff tier, read on the server.
 *
 *  - `"support"` / `"superadmin"` — a definitive staff answer.
 *  - `null` — a definitive answer that they are **not** staff.
 *  - `undefined` — could not tell (no token yet, backend unreachable, a
 *    non-200). The caller must treat this as "don't act": a transient failure
 *    must never bounce a real superadmin, so uncertainty falls through to the
 *    client gate rather than to a redirect.
 *
 * This is defence in depth. Every route behind the superadmin screens is
 * enforced on the server (`get_superuser` / `get_staff`); this only decides
 * whether to render the shell at all, one step before the client gate.
 */
export type ServerStaffRole = "support" | "superadmin" | null | undefined;

export async function serverStaffRole(): Promise<ServerStaffRole> {
  let token: string | null = null;
  try {
    token = await getServerAccessToken();
  } catch (error) {
    logger.error("serverStaffRole: could not read access token", error);
    return undefined;
  }
  if (!token) {
    // No token server-side is normal (the client holds it in some modes), so
    // this is "unknown", not "not staff" — never a redirect.
    return undefined;
  }

  try {
    const res = await fetch(`${getServerBackendUrl()}/api/v1/user/auth/user`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!res.ok) {
      return undefined;
    }
    const data = (await res.json()) as { staff_role?: string | null };
    const role = data?.staff_role;
    if (role === "support" || role === "superadmin") {
      return role;
    }
    // A 200 that carries no staff role is a definitive "not staff".
    return null;
  } catch (error) {
    logger.error("serverStaffRole: staff check failed", error);
    return undefined;
  }
}
