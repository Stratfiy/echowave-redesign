"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { HelpGate } from "@/components/support/HelpGate";
import { TicketView } from "@/components/support/TicketView";

/** Screen 28: one support request, its thread and its status. */
export default function HelpTicketPage() {
    return (
        <Suspense>
            <HelpTicket />
        </Suspense>
    );
}

function HelpTicket() {
    const { ticketId } = useParams<{ ticketId: string }>();
    const params = useSearchParams();
    return (
        <HelpGate>
            <TicketView ticketId={Number(ticketId)} attachmentFailed={params?.get("attachment") === "failed"} />
        </HelpGate>
    );
}
