import type { Metadata } from "next";

import { FirstAgentJourney } from "@/components/first-agent/FirstAgentJourney";
import { StartGate } from "@/components/helpers/StartGate";

export const metadata: Metadata = {
    title: "Your first agent — Decibyl",
};

/**
 * Where a new account lands: pick a template, name it, hear it.
 *
 * Reached from after-sign-in when the account has no agent, and safe to
 * reopen later — a second visit starts a second agent. With the describe-it
 * builder on, StartGate sends everyone to Chat's builder instead.
 */
export default function StartPage() {
    return (
        <StartGate>
            <FirstAgentJourney />
        </StartGate>
    );
}
