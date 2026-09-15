"use client";

import { ShieldAlert } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAccessRoles } from "@/hooks/useAccessRoles";

/**
 * The client half of the staff gate.
 *
 * Two tiers, not one. Superadmin renders the whole console. Support renders
 * only the KYC review queue — the one staff screen backed by `get_staff`;
 * everything else here is `get_superuser`, so a support-tier person who
 * reaches the billing, impersonation or provider-key screens would only
 * collect 403s. They are refused the shell instead of shown a console they
 * cannot use.
 *
 * The refusal is deliberately plain: it does not say what lives here, because
 * the person reading it is not meant to know. Nothing behind this was ever at
 * risk — every route is server-enforced — this is about not advertising the
 * console, and not handing a support agent a page that only answers in 403s.
 */

// The screens support staff are meant to use: KYC review and nothing else.
// A prefix match, so a detail route under the queue is allowed too.
const SUPPORT_ALLOWED_PREFIXES = ["/superadmin/verification"];

function isSupportAllowed(pathname: string): boolean {
    return SUPPORT_ALLOWED_PREFIXES.some(
        (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    );
}

export function SuperadminGate({ children }: { children: ReactNode }) {
    const roles = useAccessRoles();
    const pathname = usePathname();

    // Until the server has answered, show nothing rather than a flash of the
    // console followed by a refusal — which would leak the shape of the page
    // to exactly the people this is for.
    if (!roles.loaded) {
        return null;
    }

    const isSuperadmin = roles.staffRole === "superadmin";
    const allowed =
        isSuperadmin || (roles.isStaff && isSupportAllowed(pathname));

    if (!allowed) {
        return (
            <div className="flex min-h-[60vh] items-center justify-center p-6">
                <div className="max-w-md text-center">
                    <ShieldAlert className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
                    <h1 className="text-lg font-semibold">This page is not available</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Your account does not have access to this area. If you reached
                        it from a link, the link was not meant for you.
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
