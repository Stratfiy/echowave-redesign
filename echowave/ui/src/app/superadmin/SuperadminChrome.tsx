"use client";

import type { ReactNode } from "react";

import { StaffShell } from "@/components/staff/StaffShell";
import { useAppConfig } from "@/context/AppConfigContext";
import { useFeature } from "@/lib/features";

import { SuperadminGate } from "./SuperadminGate";
import { SuperadminNav } from "./SuperadminNav";

/**
 * Which staff area to draw. With `staff_console` on, the eight-destination
 * console (launch stream `staff`): its shell asks the server which roles
 * and destinations this person has and refuses anything else. Off, exactly
 * what /superadmin was: the gate, the strip of links and the page.
 *
 * Waits for the flags so neither version flashes before the other.
 */
export function SuperadminChrome({ children }: { children: ReactNode }) {
    const { loading } = useAppConfig();
    const consoleOn = useFeature("staff_console");
    if (loading) return null;
    if (consoleOn) return <StaffShell>{children}</StaffShell>;
    return (
        <SuperadminGate>
            <SuperadminNav />
            {children}
        </SuperadminGate>
    );
}
