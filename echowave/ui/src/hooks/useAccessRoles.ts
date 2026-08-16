"use client";

import { useEffect, useState } from "react";

import { getAuthUserApiV1UserAuthUserGet } from "@/client/sdk.gen";
import { useAuth } from "@/lib/auth";

/**
 * Who the signed-in person is, for deciding what to *show* them.
 *
 * Two independent dimensions, and conflating them is the mistake this exists
 * to prevent:
 *
 * - `staffRole` is Decibyl staff — support or superadmin — and has nothing to
 *   do with any customer organization.
 * - `organizationRole` is standing inside the currently selected organization:
 *   member, admin or owner.
 *
 * **This is presentation only.** Every one of these surfaces is enforced on
 * the server — `require_organization_role` and `get_superuser` — and nothing
 * here is a security boundary. What it buys is the difference between a
 * member who never sees a control they cannot use and a member who clicks one
 * and collects a 403. The second teaches people that the product is broken.
 *
 * Both values are read from the server rather than inferred from anything the
 * browser already holds, because staffness and role are server facts. While
 * the request is in flight, and if it fails, everything reports *unprivileged*
 * — showing a staff link to a customer is worse than making a reviewer reload,
 * so the failure direction is deliberate.
 */
export type AccessRoles = {
    /** "support" | "superadmin", or null for a customer. */
    staffRole: string | null;
    /** "member" | "admin" | "owner", or null before it is known. */
    organizationRole: string | null;
    /** True for any Decibyl staff tier. */
    isStaff: boolean;
    /** Admin or owner — the tier that can touch keys, money and the DND list. */
    isOrganizationAdmin: boolean;
    /** Owner only — the tier that decides who is in the organization. */
    isOrganizationOwner: boolean;
    /** False until the server has answered, so callers can avoid flicker. */
    loaded: boolean;
};

const UNPRIVILEGED: AccessRoles = {
    staffRole: null,
    organizationRole: null,
    isStaff: false,
    isOrganizationAdmin: false,
    isOrganizationOwner: false,
    loaded: false,
};

export function useAccessRoles(): AccessRoles {
    const { user } = useAuth();
    const [roles, setRoles] = useState<AccessRoles>(UNPRIVILEGED);

    useEffect(() => {
        if (!user) {
            setRoles(UNPRIVILEGED);
            return;
        }
        let cancelled = false;
        void (async () => {
            const response = await getAuthUserApiV1UserAuthUserGet();
            if (cancelled) return;
            if (response.error || !response.data) {
                // Stay unprivileged rather than guessing. A reviewer reloads;
                // a customer never sees something they should not.
                setRoles({ ...UNPRIVILEGED, loaded: true });
                return;
            }
            const staffRole = response.data.staff_role ?? null;
            const organizationRole = response.data.organization_role ?? null;
            setRoles({
                staffRole,
                organizationRole,
                isStaff: Boolean(staffRole),
                isOrganizationAdmin:
                    organizationRole === "admin" || organizationRole === "owner",
                isOrganizationOwner: organizationRole === "owner",
                loaded: true,
            });
        })();
        return () => {
            cancelled = true;
        };
    }, [user]);

    return roles;
}
