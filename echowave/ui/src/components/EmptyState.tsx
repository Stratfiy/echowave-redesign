/**
 * What a screen says when it has nothing to show.
 *
 * Most of ours said "No runs found" or "No campaigns found" — accurate, and
 * useless at the only moment it appears, which is a new account's first visit.
 * The screen a customer sees most often in week one is the one that tells them
 * least about what to do next.
 *
 * Two states, and telling them apart is the point. "You have not done this
 * yet" and "your filter matched nothing" are different facts about the world,
 * and a screen that shows the same sentence for both sends somebody looking
 * for a bug in their filter when they have simply never made a call — or, far
 * worse, tells an account with ten thousand calls that it has none.
 */

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
    icon: Icon,
    title,
    description,
    action,
}: {
    icon?: LucideIcon;
    /** What is true, in a few words. Not "No data". */
    title: string;
    /** Why it is worth doing, or what to change. One sentence. */
    description?: string;
    /** The next step, when there is one worth naming. */
    action?: ReactNode;
}) {
    return (
        <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
            {Icon && (
                <div className="mb-4 rounded-full bg-muted p-3">
                    <Icon className="h-5 w-5 text-muted-foreground" />
                </div>
            )}
            <p className="text-sm font-medium">{title}</p>
            {description && (
                <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
            )}
            {action && <div className="mt-4">{action}</div>}
        </div>
    );
}
