"use client";

import { ActionsList } from "@/components/support/staff/ActionsList";
import { SupportConsoleGate } from "@/components/support/staff/SupportConsoleGate";

/** Screen 33: support actions waiting on a second person, and recent ones. */
export default function SupportActionsPage() {
    return (
        <SupportConsoleGate flag="support_actions">
            <h1 className="mb-4 text-2xl font-semibold">Support actions</h1>
            <ActionsList />
        </SupportConsoleGate>
    );
}
