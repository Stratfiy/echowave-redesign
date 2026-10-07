"use client";

import { Suspense } from "react";

import { CareHub, useCareParts } from "@/components/care/CareHub";
import { useSimpleMode } from "@/lib/care/simpleMode";

/**
 * /care (launch stream `care`). Nothing here while every care switch is off:
 * the page says it is not available rather than showing an empty hub.
 */
export default function CarePage() {
    const parts = useCareParts();
    const simple = useSimpleMode();
    if (parts.length === 0 && !simple.offered) {
        return (
            <div className="mx-auto max-w-xl px-4 py-10">
                <h1 className="text-2xl font-semibold">Care</h1>
                <p className="mt-2 text-muted-foreground">Care is not switched on for this workspace.</p>
            </div>
        );
    }
    return (
        <Suspense fallback={null}>
            <CareHub />
        </Suspense>
    );
}
