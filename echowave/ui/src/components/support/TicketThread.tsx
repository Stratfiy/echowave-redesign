"use client";

/**
 * A support ticket's conversation, oldest first: the customer, support, and
 * the system's lines after an action. Who said each line is in words, not
 * colour alone. Staff internal notes are never passed to this component on
 * the customer's side -- the customer API does not return them.
 */

import { Info } from "lucide-react";

import { cn } from "@/lib/utils";

export type ThreadMessage = {
    id: number;
    author_kind: string;
    body: string;
    created_at?: string | null;
};

function when(at?: string | null): string {
    if (!at) return "";
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function TicketThread({
    messages,
    viewer,
    className,
}: {
    messages: readonly ThreadMessage[];
    /** Whose screen this is: names "You" on the right side. */
    viewer: "customer" | "staff";
    className?: string;
}) {
    const label = (kind: string) => {
        if (kind === "system") return "Decibyl";
        if (kind === viewer) return "You";
        return kind === "customer" ? "Customer" : "Decibyl support";
    };
    return (
        <ol className={cn("flex flex-col gap-3", className)} aria-label="Conversation">
            {messages.map((message) =>
                message.author_kind === "system" ? (
                    <li key={message.id} className="motion-m6-enter flex items-start gap-2 px-1 text-sm text-muted-foreground" data-testid="thread-system">
                        <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                        <p className="min-w-0 break-words">
                            {message.body} <span className="text-xs">· {when(message.created_at)}</span>
                        </p>
                    </li>
                ) : (
                    <li
                        key={message.id}
                        className={cn(
                            "motion-m6-enter max-w-[min(36rem,92%)] rounded-[var(--radius)] border px-3 py-2",
                            message.author_kind === viewer ? "self-end border-border bg-muted/40" : "self-start border-border bg-background",
                        )}
                        data-testid={`thread-${message.author_kind}`}
                    >
                        <p className="text-xs font-medium text-muted-foreground">
                            {label(message.author_kind)} · <time dateTime={message.created_at ?? undefined}>{when(message.created_at)}</time>
                        </p>
                        <p className="mt-0.5 whitespace-pre-wrap break-words text-sm">{message.body}</p>
                    </li>
                ),
            )}
        </ol>
    );
}

export default TicketThread;
