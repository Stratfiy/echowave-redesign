/**
 * The one way a screen tells someone something failed.
 *
 * There were five. Three used raw `red-*` utilities with no dark variant, so
 * the message was unreadable in a dark theme — a failure notice that cannot
 * be read is worse than none, because the screen looks merely empty. The
 * other two differed only in radius and background opacity, which is the kind
 * of difference nobody chooses and everybody copies.
 *
 * Semantic tokens, so it resolves in both themes; `role="alert"` so it is
 * announced rather than silently painted.
 */

import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function ErrorBanner({
    children,
    className,
    /** Rendered to the right of the message — a retry, usually. */
    action,
}: {
    children: ReactNode;
    className?: string;
    action?: ReactNode;
}) {
    return (
        <div
            role="alert"
            className={cn(
                "flex items-start gap-2 rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive",
                className,
            )}
        >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1">{children}</span>
            {action}
        </div>
    );
}

export default ErrorBanner;
