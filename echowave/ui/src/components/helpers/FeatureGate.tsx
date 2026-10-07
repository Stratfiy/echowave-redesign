"use client";

import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/EmptyState";
import type { Feature } from "@/lib/features";
import { useFeatureState } from "@/lib/helpers";

/** A page of this stream's, shown only while its flag is on. Loading is
 *  said as loading, never as "not switched on". */
export function FeatureGate({ feature, children }: { feature: Feature; children: ReactNode }) {
    const state = useFeatureState(feature);
    if (state === "loading") {
        return (
            <p className="flex items-center gap-2 px-4 py-8 text-sm text-muted-foreground" role="status">
                <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…
            </p>
        );
    }
    if (state === "off") {
        return <EmptyState title="This is not switched on here." description="Ask Decibyl in Chat instead." />;
    }
    return <>{children}</>;
}
