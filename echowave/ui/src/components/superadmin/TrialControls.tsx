"use client";

import { CalendarClock, CalendarPlus, CircleStop, Loader2, PauseCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { setTrialEndApiV1SuperuserOrganizationsOrganizationIdTrialPost } from "@/client/sdk.gen";
import { useConfirm } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { detailFromResult } from "@/lib/apiError";
import { formatDateTimeIST } from "@/lib/billing/format";

import { canPause, COPY, type TrialView } from "./orgHealth";

type Body = { extend_days?: number; ends_at?: string; note?: string };

/** The end of a chosen day in India, so "set to the 10th" means the whole of
 *  the 10th rather than its first minute. */
export function endOfDayIST(day: string): string {
    return `${day}T23:59:59+05:30`;
}

/**
 * Extend, set or end an account's trial, and the interim pause (ADMIN-2, A3
 * and A5). Every action goes through the existing
 * `POST /superuser/organizations/{id}/trial`, which writes the audit row; each
 * asks first, and the answer from the server -- the new end -- is shown.
 */
export function TrialControls({
    organizationId,
    trial,
    onChanged,
}: {
    organizationId: number;
    trial: TrialView;
    onChanged: () => void;
}) {
    const { confirm, dialog } = useConfirm();
    const [busy, setBusy] = useState<string | null>(null);
    const [day, setDay] = useState("");
    const [result, setResult] = useState<string | null>(null);
    const pausable = canPause(trial);

    const run = async (key: string, body: Body, ask: Parameters<typeof confirm>[0]) => {
        if (!(await confirm(ask))) return;
        setBusy(key);
        try {
            const response = await setTrialEndApiV1SuperuserOrganizationsOrganizationIdTrialPost({
                path: { organization_id: organizationId },
                body,
            });
            if (response.error) {
                toast.error(detailFromResult(response, "Could not change the trial"));
                return;
            }
            const next = (response.data as { trial?: TrialView } | undefined)?.trial;
            const message = next?.ends_at
                ? `Trial now ${next.active ? "ends" : "ended"} ${formatDateTimeIST(next.ends_at)}`
                : "Trial window saved. This account is not on the trial, so nothing changes for it yet.";
            setResult(message);
            toast.success(message);
            onChanged();
        } finally {
            setBusy(null);
        }
    };

    const spinner = (key: string, icon: React.ReactNode) =>
        busy === key ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon;

    return (
        <div className="flex w-full flex-col gap-2">
            <div className="flex flex-wrap items-end gap-2">
                <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null}
                    onClick={() =>
                        run(
                            "extend",
                            { extend_days: 7, note: "Extended 7 days from the account page" },
                            {
                                title: COPY.extendTrial,
                                description:
                                    "The trial will end 7 days from now. The customer gets fresh 3-day and 1-day notices for the new date.",
                                confirmLabel: "Extend by 7 days",
                            },
                        )
                    }
                >
                    {spinner("extend", <CalendarPlus className="h-4 w-4" aria-hidden />)}
                    {COPY.extendTrial}
                </Button>

                <div className="flex items-end gap-2">
                    <div className="space-y-1">
                        <Label htmlFor={`trial-end-${organizationId}`} className="text-xs text-muted-foreground">
                            New end date
                        </Label>
                        <Input
                            id={`trial-end-${organizationId}`}
                            type="date"
                            value={day}
                            onChange={(e) => setDay(e.target.value)}
                            className="h-8 w-[160px]"
                        />
                    </div>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={busy !== null || !day}
                        onClick={() =>
                            run(
                                "set",
                                { ends_at: endOfDayIST(day), note: `Set to ${day} from the account page` },
                                {
                                    title: COPY.setEndDate,
                                    description: `The trial will end at the close of ${day} (India time).`,
                                    confirmLabel: COPY.setEndDate,
                                },
                            )
                        }
                    >
                        {spinner("set", <CalendarClock className="h-4 w-4" aria-hidden />)}
                        {COPY.setEndDate}
                    </Button>
                </div>

                <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null || !pausable}
                    onClick={() =>
                        run(
                            "end",
                            { ends_at: new Date().toISOString(), note: "Ended from the account page" },
                            {
                                title: COPY.endTrialNow,
                                description:
                                    "The trial ends immediately. New calls, messages and routines stop until the account chooses a plan; nothing is deleted.",
                                confirmLabel: COPY.endTrialNow,
                                destructive: true,
                            },
                        )
                    }
                >
                    {spinner("end", <CircleStop className="h-4 w-4" aria-hidden />)}
                    {COPY.endTrialNow}
                </Button>

                <Tooltip>
                    <TooltipTrigger asChild>
                        {/* A span, so the explanation still shows while the
                            button is disabled (disabled buttons get no hover). */}
                        <span tabIndex={pausable ? -1 : 0}>
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={busy !== null || !pausable}
                                onClick={() =>
                                    run(
                                        "pause",
                                        { ends_at: new Date().toISOString(), note: "Paused from the account page" },
                                        {
                                            title: COPY.pauseAccount,
                                            description: COPY.pauseExplainer,
                                            confirmLabel: COPY.pauseAccount,
                                            destructive: true,
                                        },
                                    )
                                }
                            >
                                {spinner("pause", <PauseCircle className="h-4 w-4" aria-hidden />)}
                                {COPY.pauseAccount}
                            </Button>
                        </span>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                        {pausable ? COPY.pauseExplainer : COPY.pauseUnavailable}
                    </TooltipContent>
                </Tooltip>
            </div>
            {!pausable && (
                <p className="text-xs text-muted-foreground">{COPY.pauseUnavailable}</p>
            )}
            {result && (
                <p role="status" className="text-xs text-muted-foreground">
                    {result}
                </p>
            )}
            {dialog}
        </div>
    );
}
