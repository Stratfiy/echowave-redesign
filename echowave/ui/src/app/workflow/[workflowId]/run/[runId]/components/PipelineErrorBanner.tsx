import { AlertTriangleIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface PipelineError {
    detail?: unknown;
    frame_type?: unknown;
    at?: unknown;
    fatal?: unknown;
}

/**
 * Why this call failed, at the top of the screen someone opened to find out.
 *
 * The backend has recorded the cause of a fatal pipeline failure onto the run
 * for a while, but nothing served it, so the answer to "my call ended the
 * moment I picked up" lived only in the API log — readable by whoever had shell
 * on the box, for as long as the logs had not rotated.
 *
 * Deliberately the provider's own words rather than a friendly paraphrase.
 * These failures are nearly always a provider rejecting a setting we sent, and
 * the vendor's message is the string that matches their docs and their support
 * thread. A rewrite would read better and be worth less.
 */
export function PipelineErrorBanner({ error }: { error: PipelineError | null }) {
    const detail = typeof error?.detail === "string" ? error.detail : null;
    if (!detail) return null;

    const at = typeof error?.at === "string" ? error.at : null;
    // Only a fatal error ended the call. A non-fatal one is a provider
    // complaining about something it recovered from, and telling somebody their
    // completed call "ended on an error" would send them hunting for a failure
    // that did not happen.
    const fatal = error?.fatal !== false;

    return (
        <div
            role="alert"
            className={cn(
                "rounded-md border p-4",
                fatal
                    ? "border-destructive/40 bg-destructive/5"
                    : "border-amber-500/40 bg-amber-500/5",
            )}
        >
            <div className="flex items-start gap-3">
                <AlertTriangleIcon
                    className={cn(
                        "mt-0.5 h-4 w-4 shrink-0",
                        fatal ? "text-destructive" : "text-amber-600",
                    )}
                    aria-hidden="true"
                />
                <div className="min-w-0 space-y-1">
                    <p
                        className={cn(
                            "text-sm font-medium",
                            fatal ? "text-destructive" : "text-amber-700",
                        )}
                    >
                        {fatal
                            ? "This call ended on a pipeline error"
                            : "A service reported an error during this call"}
                    </p>
                    <p className="break-words font-mono text-xs text-muted-foreground">
                        {detail}
                    </p>
                    {at && (
                        <p className="text-xs text-muted-foreground">
                            {new Date(at).toLocaleString()}
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
