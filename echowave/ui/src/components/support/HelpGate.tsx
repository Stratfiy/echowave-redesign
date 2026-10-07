"use client";

/**
 * Help is shown only when `support_help` is on for the workspace and the
 * person is signed in. While either is not known yet, nothing flashes;
 * while off, the page says it is not available -- as it was before Help
 * existed -- rather than showing a form whose request would be refused.
 */

import type { ReactNode } from "react";

import { EmptyState } from "@/components/shell";
import SpinLoader from "@/components/SpinLoader";
import { useAuth } from "@/lib/auth";
import { useHelpAvailability } from "@/lib/support/help";

export function HelpGate({ children }: { children: ReactNode }) {
    const { user, loading } = useAuth();
    const availability = useHelpAvailability();
    if (loading || !user || availability === "loading") return <SpinLoader />;
    if (availability === "off") return <EmptyState title="This page is not available." />;
    return (
        <div className="mx-auto w-full max-w-3xl px-4 py-6 md:px-6" data-testid="help-page">
            {children}
        </div>
    );
}

export default HelpGate;
