"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { HelpGate } from "@/components/support/HelpGate";
import { HelpRequestForm } from "@/components/support/HelpRequestForm";

/**
 * Screen 28: ask support. Opened from a failed task (`?task=`) or reply
 * (`?reply=`), the request is about that one thing.
 */
export default function NewHelpRequestPage() {
    // useSearchParams needs a Suspense boundary to build statically.
    return (
        <Suspense>
            <NewHelpRequest />
        </Suspense>
    );
}

function NewHelpRequest() {
    const params = useSearchParams();
    const task = Number(params?.get("task"));
    const reply = Number(params?.get("reply"));
    const affectedKind = task > 0 ? "task" : reply > 0 ? "reply" : null;
    const affectedId = task > 0 ? task : reply > 0 ? reply : null;
    return (
        <HelpGate>
            <Link href="/help" className="mb-2 inline-flex min-h-11 items-center text-sm text-muted-foreground underline-offset-2 hover:underline md:min-h-0">
                ← Help
            </Link>
            <h1 className="mb-4 text-2xl font-semibold">Ask support</h1>
            <HelpRequestForm affectedKind={affectedKind} affectedId={affectedId} />
        </HelpGate>
    );
}
