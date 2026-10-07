"use client";

/**
 * Screen 01, invitation redemption: /invite/ABCD-2345. Reads the code's
 * state without spending it; Accept continues to sign-up with the code.
 */

import Link from "next/link";
import { use } from "react";

import { INVITATION_COPY } from "@/components/early-access/copy";
import { DoorShell } from "@/components/early-access/DoorShell";
import { InvitationCard } from "@/components/early-access/InvitationCard";
import { useAppConfig } from "@/context/AppConfigContext";
import { useFeature } from "@/lib/features";

export default function InvitePage({ params }: { params: Promise<{ code: string }> }) {
    const { code } = use(params);
    const { loading } = useAppConfig();
    const open = useFeature("early_access");
    return (
        <DoorShell title="Your invitation">
            {loading ? null : open ? (
                <InvitationCard code={decodeURIComponent(code)} />
            ) : (
                // Off: the existing door still takes the code.
                <p className="text-base text-muted-foreground">
                    <Link href={`/auth/signup?invite=${encodeURIComponent(code)}`} className="underline underline-offset-2">
                        Continue to sign up
                    </Link>{" "}
                    · {INVITATION_COPY.notOpen}
                </p>
            )}
        </DoorShell>
    );
}
