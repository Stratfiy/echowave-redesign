"use client";

/**
 * Asks every signed-in account for the agreements it still owes, once.
 *
 * Signup records Terms and Privacy. The Data Processing Agreement was only
 * asked for at the two points that cost money -- buying a number and starting
 * a campaign -- yet an account processes other people's personal data long
 * before either: an inbound call, a WhatsApp thread, an uploaded contact list.
 * This puts the same dialog in front of every account on its first signed-in
 * screen, so the record exists before the data does.
 *
 * A prompt, not a wall. "Not now" closes it for this tab and it returns on the
 * next visit; the server still refuses the money paths until it is accepted
 * (`require_accepted`). Locking existing accounts out of their own work would
 * be an outage dressed as a compliance improvement -- the same reasoning as the
 * verify-email banner.
 *
 * Never shown while a staffer is impersonating: an acceptance is the
 * customer's act, and one recorded by us on their behalf is worth nothing.
 */

import { useEffect, useState } from "react";

import { AgreementsDialog, useAgreements } from "@/components/AgreementsDialog";
import { isImpersonating } from "@/components/auth/ImpersonationBanner";
import { useAuth } from "@/lib/auth";

export const AGREEMENTS_GATE_REASON =
    "Your agents handle your customers' calls, messages and files. The Data Processing Agreement sets out how we process that data for you, so we need it accepted once for your workspace.";

export function AgreementsGate() {
    const { user, loading: authLoading } = useAuth();
    const [staff, setStaff] = useState(true);
    useEffect(() => {
        setStaff(isImpersonating());
    }, []);

    const enabled = Boolean(user) && !authLoading && !staff;
    const agreements = useAgreements(enabled);
    const [dismissed, setDismissed] = useState(false);

    const owes = !agreements.loading && agreements.outstanding.length > 0;
    const open = enabled && owes && !dismissed;

    return (
        <AgreementsDialog
            open={open}
            state={agreements}
            onOpenChange={(next) => {
                if (!next) setDismissed(true);
            }}
            onAccepted={() => setDismissed(true)}
            reason={AGREEMENTS_GATE_REASON}
        />
    );
}
