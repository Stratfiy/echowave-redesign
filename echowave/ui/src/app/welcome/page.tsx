"use client";

/**
 * Screen 02, after a verified sign-in: language, timezone, first task.
 * Reached from the landing (`/shell/landing`) while `first_task_onboarding`
 * is on; with it off the page hands straight over to Chat.
 */

import { DoorShell } from "@/components/early-access/DoorShell";
import { FirstTaskOnboarding } from "@/components/onboarding/FirstTaskOnboarding";

export default function WelcomePage() {
    return (
        <DoorShell width="wide" title="Welcome to Decibyl" lead="Two quick choices, then tell Decibyl what you need.">
            <FirstTaskOnboarding />
        </DoorShell>
    );
}
