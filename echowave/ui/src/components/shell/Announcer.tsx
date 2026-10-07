"use client";

/**
 * One polite live region, for state changes a screen reader should hear:
 * "Decibyl is replying", "Reply finished", "Stopped". Never every token of
 * a streaming reply (handoff section 26): the caller announces a change of
 * state, and the region says it once.
 */

import { useEffect, useState } from "react";

export function Announcer({ message, politeness = "polite" }: { message: string | null; politeness?: "polite" | "assertive" }) {
    // Cleared and re-set on each change so the same sentence twice (two
    // replies in a row) is still announced.
    const [said, setSaid] = useState("");
    useEffect(() => {
        if (!message) return;
        setSaid("");
        const timer = setTimeout(() => setSaid(message), 50);
        return () => clearTimeout(timer);
    }, [message]);
    return (
        <div role="status" aria-live={politeness} aria-atomic="true" className="sr-only" data-testid="announcer">
            {said}
        </div>
    );
}

export default Announcer;
