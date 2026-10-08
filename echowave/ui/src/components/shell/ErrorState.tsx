"use client";

/**
 * A region that failed to load, with the step that failed and the safe next
 * action. Never an empty state in disguise: "Could not load this
 * conversation" with Retry is the honest screen when history fails, where
 * "Say hello" over a blank thread is a lie (screen 03).
 */

import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ErrorState({
    title,
    description,
    onRetry,
    retrying = false,
    retryLabel = "Try again",
    action,
    className,
}: {
    /** The failed step, in a few words: "Could not load this conversation". */
    title: string;
    /** What is safe to do, or what is kept: "Your draft is kept." */
    description?: ReactNode;
    onRetry?: () => void;
    retrying?: boolean;
    retryLabel?: string;
    /** A second way forward, when there is one. */
    action?: ReactNode;
    className?: string;
}) {
    return (
        <div
            role="alert"
            className={cn("flex flex-col items-center justify-center px-6 py-10 text-center", className)}
            data-testid="error-state"
        >
            <div className="mb-3 rounded-full bg-destructive/10 p-3">
                <AlertTriangle aria-hidden className="h-5 w-5 text-destructive" />
            </div>
            <p className="text-sm font-medium text-foreground">{title}</p>
            {description && <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>}
            {(onRetry || action) && (
                <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                    {onRetry && (
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={onRetry} disabled={retrying}>
                            {retrying ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <RefreshCw aria-hidden />}
                            {retrying ? "Trying again…" : retryLabel}
                        </Button>
                    )}
                    {action}
                </div>
            )}
        </div>
    );
}

export default ErrorState;
