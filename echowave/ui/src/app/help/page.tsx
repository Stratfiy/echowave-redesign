"use client";

import { HelpGate } from "@/components/support/HelpGate";
import { HelpHome } from "@/components/support/HelpHome";

/** Screen 28: Help, the person's own support requests. */
export default function HelpPage() {
    return (
        <HelpGate>
            <HelpHome />
        </HelpGate>
    );
}
