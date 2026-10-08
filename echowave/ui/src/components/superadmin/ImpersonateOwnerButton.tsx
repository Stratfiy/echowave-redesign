"use client";

import { Loader2, UserCog } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useAssistedAccessDialog } from "@/components/superadmin/AssistedAccessDialog";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { impersonateAsSuperadmin } from "@/lib/utils";

import { COPY } from "./orgHealth";

/**
 * "Impersonate owner" on the account page (ADMIN-2, A4): the same flow as the
 * console's email box (`/superuser/impersonate`, audited and refused for
 * staff accounts), aimed at the account's owner by user id so nobody has to
 * copy an address across screens.
 */
export function ImpersonateOwnerButton({
    ownerUserId,
    ownerEmail,
}: {
    ownerUserId: number | null;
    ownerEmail: string | null;
}) {
    const { getAccessToken } = useAuth();
    const { ask, dialog } = useAssistedAccessDialog();
    const [busy, setBusy] = useState(false);
    const who = ownerEmail ?? "the owner";

    const start = async () => {
        if (ownerUserId === null) return;
        const choice = await ask(who);
        if (!choice) return;
        setBusy(true);
        try {
            const accessToken = await getAccessToken();
            if (!accessToken) throw new Error("Your session has expired. Sign in again.");
            await impersonateAsSuperadmin({
                accessToken,
                userId: ownerUserId,
                reason: choice.reason,
                mode: choice.mode,
                redirectPath: "/overview",
                openInNewTab: true,
            });
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Could not start impersonating");
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            <Button
                size="sm"
                variant="outline"
                disabled={busy || ownerUserId === null}
                title={ownerUserId === null ? COPY.impersonateNoOwner : COPY.impersonateExplainer}
                onClick={() => void start()}
            >
                {busy ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                    <UserCog className="h-4 w-4" aria-hidden />
                )}
                {COPY.impersonateOwner}
            </Button>
            {dialog}
        </>
    );
}
