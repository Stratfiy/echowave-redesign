"use client";

import { useParams } from "next/navigation";

import { ActionDetail } from "@/components/support/staff/ActionDetail";
import { SupportConsoleGate } from "@/components/support/staff/SupportConsoleGate";

/** Screen 33: one support action, its approval, run and record. */
export default function SupportActionPage() {
    const { actionId } = useParams<{ actionId: string }>();
    return (
        <SupportConsoleGate flag="support_actions">
            <ActionDetail actionId={Number(actionId)} />
        </SupportConsoleGate>
    );
}
