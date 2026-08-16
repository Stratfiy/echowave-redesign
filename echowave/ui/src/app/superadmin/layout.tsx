"use client";

import { ShieldAlert } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAccessRoles } from "@/hooks/useAccessRoles";

/**
 * The staff area, closed to everyone else.
 *
 * The sidebar and the top-bar search both hide these destinations from
 * customers, which covers every way the product itself offers them. It does
 * not cover the address bar, a stale bookmark, or a link pasted into a chat —
 * and before this the page rendered its full shell for anyone who arrived that
 * way, then filled it with failed requests. Nothing leaked: every route behind
 * it requires `get_superuser`, so the data was never at risk. What arrived was
 * the impression that Decibyl's internal console is one URL away from any
 * customer, which is its own kind of damage.
 *
 * The refusal is deliberately plain. It does not say what lives here, because
 * the person reading it is not meant to know.
 */
export default function SuperadminLayout({ children }: { children: ReactNode }) {
    const roles = useAccessRoles();

    // Until the server has answered, show nothing rather than a flash of the
    // console followed by a refusal — which would leak the shape of the page
    // to exactly the people this is for.
    if (!roles.loaded) {
        return null;
    }

    if (!roles.isStaff) {
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
