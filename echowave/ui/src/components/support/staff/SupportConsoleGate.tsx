"use client";

/**
 * The support screens inside the staff console, shown only while their
 * switch is on; off, the page says it is not available, as before. Staff
 * access itself is the console's gate (SuperadminGate) and, authoritatively,
 * `get_staff` on every route behind these screens.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/shell";
import SpinLoader from "@/components/SpinLoader";
import { useAppConfig } from "@/context/AppConfigContext";
import { useAuth } from "@/lib/auth";
import { type Feature, useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

export function SupportConsoleGate({
    flag,
    children,
    wide = false,
}: {
    flag: Extract<Feature, "support_inbox" | "support_actions">;
    children: ReactNode;
    /** The inbox uses the whole width; forms keep a reading column. */
    wide?: boolean;
}) {
    const { user, loading } = useAuth();
    const { loading: configLoading } = useAppConfig();
    const on = useFeature(flag);
    if (loading || !user || configLoading) return <SpinLoader />;
    if (!on) return <EmptyState title="This page is not available." />;
    return (
        <div className={cn("w-full", !wide && "mx-auto max-w-3xl px-4 py-6 md:px-6")} data-testid="support-console">
            {!wide && (
                <nav aria-label="Support" className="mb-3 flex flex-wrap gap-3 text-sm">
                    <Link href="/superadmin/support" className="min-h-11 underline-offset-2 hover:underline md:min-h-0">
                        Inbox
                    </Link>
                    <Link href="/superadmin/support/actions" className="min-h-11 underline-offset-2 hover:underline md:min-h-0">
                        Actions
                    </Link>
                </nav>
            )}
            {children}
        </div>
    );
}

export default SupportConsoleGate;
