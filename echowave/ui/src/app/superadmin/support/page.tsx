"use client";

import { SupportConsoleGate } from "@/components/support/staff/SupportConsoleGate";
import { SupportInbox } from "@/components/support/staff/SupportInbox";

/** Screen 32: the support inbox. */
export default function SupportInboxPage() {
    return (
        <SupportConsoleGate flag="support_inbox" wide>
            <SupportInbox selectedId={null} />
        </SupportConsoleGate>
    );
}
