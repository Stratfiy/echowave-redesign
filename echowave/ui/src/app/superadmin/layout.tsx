import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { serverStaffRole } from "@/lib/auth/staff";

import { SuperadminGate } from "./SuperadminGate";

/**
 * The staff area, closed to everyone else.
 *
 * Two guards, one in front of the other:
 *
 *  - **Server (this component).** A definitive "not staff" answer is
 *    redirected before the shell is ever rendered — the address bar, a stale
 *    bookmark or a pasted link no longer paints the console for a customer.
 *    An *unknown* answer (no server-side token, a transient backend error)
 *    is not treated as a refusal: it falls through to the client gate, so a
 *    blip never bounces a real superadmin.
 *  - **Client (`SuperadminGate`).** The flicker-free refusal, and the
 *    support-vs-superadmin split: support renders only the KYC review queue.
 *
 * Neither is the security boundary — every route behind these screens is
 * enforced on the server with `get_superuser` or `get_staff`. This is about
 * not advertising that Decibyl's internal console is one URL away from any
 * customer, and not handing a support agent a page that only answers in 403s.
 */
export default async function SuperadminLayout({
    children,
}: {
    children: ReactNode;
}) {
    const role = await serverStaffRole();
    if (role === null) {
        redirect("/overview");
    }

    return <SuperadminGate>{children}</SuperadminGate>;
}
