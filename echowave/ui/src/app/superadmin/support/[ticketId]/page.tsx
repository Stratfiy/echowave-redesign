"use client";

import { useParams } from "next/navigation";

import { SupportConsoleGate } from "@/components/support/staff/SupportConsoleGate";
import { SupportInbox } from "@/components/support/staff/SupportInbox";

/** Screen 32: one support case, with its own deep link. */
export default function SupportCasePage() {
    const { ticketId } = useParams<{ ticketId: string }>();
    return (
        <SupportConsoleGate flag="support_inbox" wide>
            <SupportInbox selectedId={Number(ticketId)} />
        </SupportConsoleGate>
    );
}
