"use client";

/**
 * Save and Discard for a form with unsaved changes (handoff "Save state").
 *
 * dirty -> saving -> saved. A rejection keeps the draft and says so; a
 * revision conflict shows both versions' existence and never overwrites
 * silently. On a phone it sits above the safe area and the keyboard, so
 * Save is never hidden (design "Forms and sheets").
 */

import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type SaveState = "clean" | "dirty" | "saving" | "saved" | "rejected" | "conflict";

const LINE: Record<SaveState, string> = {
    clean: "",
    dirty: "You have unsaved changes.",
    saving: "Saving…",
    saved: "Saved.",
    rejected: "Your change was not saved. Try again.",
    conflict: "Someone else changed this while you were editing. Review both versions before saving.",
};

export function SaveBar({
    state,
    onSave,
    onDiscard,
    message,
    saveLabel = "Save",
    className,
}: {
    state: SaveState;
    onSave: () => void;
    onDiscard: () => void;
    /** The server's reason on a rejection, when it gave one. */
    message?: string | null;
    saveLabel?: string;
    className?: string;
}) {
    if (state === "clean") return null;
    const busy = state === "saving";
    const problem = state === "rejected" || state === "conflict";
    return (
        <div
            className={cn(
                "sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-background px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]",
                className,
            )}
            data-testid="save-bar"
            data-state={state}
        >
            <p role="status" aria-live="polite" className={cn("flex min-w-0 items-center gap-1.5 text-sm", problem ? "text-destructive" : "text-muted-foreground")}>
                {problem && <AlertTriangle aria-hidden className="h-4 w-4 shrink-0" />}
                {state === "saved" && <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0 text-[#075A39]" />}
                <span>{problem && message ? message : LINE[state]}</span>
            </p>
            {state !== "saved" && (
                <div className="flex shrink-0 items-center gap-2">
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={onDiscard} disabled={busy}>
                        Discard
                    </Button>
                    <Button type="button" className="motion-m1 min-h-11 md:min-h-9" onClick={onSave} disabled={busy}>
                        {busy && <Loader2 aria-hidden className="motion-continuous animate-spin" />}
                        {saveLabel}
                    </Button>
                </div>
            )}
        </div>
    );
}

export default SaveBar;
