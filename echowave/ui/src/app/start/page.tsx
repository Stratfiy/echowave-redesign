import type { Metadata } from "next";

import { FirstAgentJourney } from "@/components/first-agent/FirstAgentJourney";

export const metadata: Metadata = {
    title: "Your first agent — Decibyl",
};

/**
 * Where a new account lands: pick a template, name it, hear it.
 *
 * Reached from after-sign-in when the account has no agent, and safe to
 * reopen later — a second visit starts a second agent.
 */
export default function StartPage() {
    return <FirstAgentJourney />;
}
