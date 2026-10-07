"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { EmptyState } from "@/components/shell";
import { ActionComposer } from "@/components/support/staff/ActionComposer";
import { SupportConsoleGate } from "@/components/support/staff/SupportConsoleGate";

/**
 * Screen 33: request a typed support action for one workspace and person,
 * usually from a case (`?ticket=&organization=&user=`).
 */
export default function NewSupportActionPage() {
    // useSearchParams needs a Suspense boundary to build statically.
    return (
        <Suspense>
            <NewSupportAction />
        </Suspense>
    );
}

function NewSupportAction() {
    const params = useSearchParams();
    const organization = Number(params?.get("organization"));
    const user = Number(params?.get("user"));
    const ticket = Number(params?.get("ticket"));
    return (
        <SupportConsoleGate flag="support_actions">
            <h1 className="mb-4 text-2xl font-semibold">Request a support action</h1>
            {organization > 0 ? (
                <ActionComposer organizationId={organization} targetUserId={user > 0 ? user : null} ticketId={ticket > 0 ? ticket : null} />
            ) : (
                <EmptyState title="Open this from a case or a customer." description="An action needs a workspace and a person to act for." />
            )}
        </SupportConsoleGate>
    );
}
