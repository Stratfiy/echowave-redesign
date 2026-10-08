"use client";

/**
 * /invite-requests/decide?token=… — where the Approve / Reject buttons in an
 * invite-request mail land. Opening it changes nothing; see InviteDecision.
 */

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { DoorShell } from "@/components/early-access/DoorShell";
import { InviteDecision } from "@/components/early-access/InviteDecision";

function Decide() {
    const token = useSearchParams().get("token");
    return <InviteDecision token={token} />;
}

export default function InviteRequestDecidePage() {
    return (
        <DoorShell title="Invite request">
            {/* useSearchParams needs a Suspense boundary at build time. */}
            <Suspense fallback={null}>
                <Decide />
            </Suspense>
        </DoorShell>
    );
}
