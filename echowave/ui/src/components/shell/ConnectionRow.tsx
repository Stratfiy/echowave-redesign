"use client";

/**
 * One connected app or channel, with its capability state (handoff
 * "Capability state"): available, needs setup, disabled by policy, or
 * unavailable -- each with its reason and next step. Visibility here never
 * stands in for the server's own permission check.
 */

import { Ban, CheckCircle2, CircleAlert, CircleSlash } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type CapabilityState = "available" | "needs_setup" | "disabled_by_policy" | "unavailable";

const STATE: Record<CapabilityState, { label: string; icon: typeof CheckCircle2; tone: string }> = {
    available: { label: "Connected", icon: CheckCircle2, tone: "text-[#075A39] dark:text-emerald-300" },
    needs_setup: { label: "Needs setup", icon: CircleAlert, tone: "text-[#705500] dark:text-amber-300" },
    disabled_by_policy: { label: "Turned off by your workspace", icon: Ban, tone: "text-muted-foreground" },
    unavailable: { label: "Unavailable", icon: CircleSlash, tone: "text-muted-foreground" },
};

export function ConnectionRow({
    name,
    icon,
    state,
    reason,
    detail,
    action,
    className,
}: {
    name: string;
    icon?: ReactNode;
    state: CapabilityState;
    /** Why, for anything but available: "Your Google sign-in expired". */
    reason?: string;
    /** Which account, or what it can do: "nithya@clinic.in · read and send". */
    detail?: string;
    /** The next step: Connect, Reconnect, Ask your admin. */
    action?: ReactNode;
    className?: string;
}) {
    const meta = STATE[state];
    const Icon = meta.icon;
    return (
        <div
            className={cn("flex min-h-11 flex-wrap items-center gap-3 border-b border-border py-3 last:border-b-0", className)}
            data-testid="connection-row"
            data-state={state}
        >
            {icon && <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center">{icon}</span>}
            <div className="min-w-0 flex-1">
                <p className="break-words text-sm font-medium">{name}</p>
                {detail && <p className="break-words text-xs text-muted-foreground">{detail}</p>}
                <p className={cn("mt-0.5 flex items-center gap-1 text-xs", meta.tone)}>
                    <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
                    <span>{meta.label}</span>
                    {reason && state !== "available" && <span className="text-muted-foreground">· {reason}</span>}
                </p>
            </div>
            {action && <div className="shrink-0">{action}</div>}
        </div>
    );
}

export default ConnectionRow;
