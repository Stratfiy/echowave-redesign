"use client";

/**
 * Simple mode on or off, wherever it is offered: Settings, the profile menu
 * and the Care screen. Saved with the person's own preferences, so it is the
 * same on every device; switching back is one press, from the same place.
 */

import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useSimpleMode } from "@/lib/care/simpleMode";
import { cn } from "@/lib/utils";

export function SimpleModeSwitch({ className, compact = false }: { className?: string; compact?: boolean }) {
    const simple = useSimpleMode();
    if (!simple.offered) return null;
    return (
        <div className={cn("flex flex-col gap-2", className)} data-testid="simple-mode-switch">
            {!compact && (
                <p className="text-sm text-muted-foreground">
                    Bigger writing, bigger buttons, fewer choices, one thing at a time. You can switch back here at any time.
                </p>
            )}
            <Button
                type="button"
                variant={simple.on ? "outline" : "default"}
                role="switch"
                aria-checked={simple.on}
                className="motion-m1 h-auto min-h-12 w-full whitespace-normal px-5 py-2 text-base sm:w-auto sm:self-start"
                disabled={simple.saving}
                onClick={() => void simple.setOn(!simple.on)}
            >
                {simple.saving && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                {simple.on ? "Switch back to the usual screen" : "Turn on Simple mode"}
            </Button>
            {simple.error && (
                <p role="alert" className="text-sm text-destructive">
                    {simple.error}
                </p>
            )}
        </div>
    );
}

export default SimpleModeSwitch;
