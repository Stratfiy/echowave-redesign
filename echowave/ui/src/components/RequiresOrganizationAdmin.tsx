"use client";

import { ShieldAlert } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAccessRoles } from "@/hooks/useAccessRoles";

/**
 * A screen only an organization admin or owner can use.
 *
 * The sidebar already hides these destinations from members, which covers the
 * product's own doors. This covers the rest — a bookmark, a pasted link, a
 * page someone was on when their role changed — and turns what would be a
 * screen full of 403s into a sentence naming who to ask.
 *
 * **Presentation only.** Every route behind these screens is enforced with
 * `require_organization_role` on the server; removing this component would
 * make the product ruder, not less secure.
 *
 * Not for a screen that merely *contains* an admin control. Billing is the
 * case that matters: topping up is deliberately open to every member, because
 * a member who cannot pay when the balance runs out is a member who cannot
 * work. Wrap the control, not the door.
 */
export function RequiresOrganizationAdmin({
    children,
    /** What the member cannot do here, in words they would use. */
    what = "this page",
}: {
    children: ReactNode;
    what?: string;
}) {
    const roles = useAccessRoles();

    // Render nothing until the server has answered, rather than flashing the
    // screen and then withdrawing it.
    if (!roles.loaded) {
        return null;
    }

    if (!roles.isOrganizationAdmin) {
        return (
            <div className="flex min-h-[60vh] items-center justify-center p-6">
                <div className="max-w-md text-center">
                    <ShieldAlert className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
                    <h1 className="text-lg font-semibold">
                        You need admin access for {what}
                    </h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Your role in this organization is Member. An Owner or Admin can
                        change that, or make the change here for you.
                    </p>
                    <Button asChild className="mt-6">
                        <Link href="/overview">Back to your dashboard</Link>
                    </Button>
                </div>
            </div>
        );
    }

    return <>{children}</>;
}
