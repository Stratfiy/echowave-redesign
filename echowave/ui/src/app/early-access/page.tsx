"use client";

/**
 * Screen 01: the public waitlist. Behind `early_access`; while it is off
 * the page says early access is not open rather than showing a form whose
 * request would be refused.
 */

import Link from "next/link";

import { EARLY_ACCESS_COPY, INVITATION_COPY } from "@/components/early-access/copy";
import { DoorShell } from "@/components/early-access/DoorShell";
import { WaitlistForm } from "@/components/early-access/WaitlistForm";
import { useAppConfig } from "@/context/AppConfigContext";
import { useFeature } from "@/lib/features";

export default function EarlyAccessPage() {
    const { loading } = useAppConfig();
    const open = useFeature("early_access");
    return (
        <DoorShell title={EARLY_ACCESS_COPY.title} lead={open ? EARLY_ACCESS_COPY.lead : undefined}>
            {loading ? null : open ? (
                <WaitlistForm />
            ) : (
                <p className="text-base text-muted-foreground">
                    {INVITATION_COPY.notOpen}{" "}
                    <Link href="/auth/login" className="underline underline-offset-2">
                        Sign in
                    </Link>
                </p>
            )}
        </DoorShell>
    );
}
