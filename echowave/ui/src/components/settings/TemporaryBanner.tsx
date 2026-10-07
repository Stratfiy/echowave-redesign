"use client";

/**
 * The line above a temporary conversation in Chat: what it keeps and for
 * how long, from the server -- shown only once the server confirms this
 * conversation is temporary and the person's own.
 */

import { TimerReset } from "lucide-react";
import { useEffect, useState } from "react";

import { temporaryConversationApiV1MeTemporaryConversationsThreadIdGet } from "@/client/sdk.gen";

export function TemporaryBanner({ threadId }: { threadId: string }) {
    const [line, setLine] = useState<string | null>(null);
    useEffect(() => {
        let cancelled = false;
        setLine(null);
        void (async () => {
            const result = await temporaryConversationApiV1MeTemporaryConversationsThreadIdGet({ path: { thread_id: threadId } });
            if (cancelled || result.error || !result.data) return;
            setLine(result.data.retention);
        })();
        return () => {
            cancelled = true;
        };
    }, [threadId]);
    if (!line) return null;
    return (
        <p className="flex shrink-0 items-start gap-2 border-b border-border/70 bg-muted/40 px-4 py-2 text-xs text-muted-foreground sm:px-6" role="note" data-testid="temporary-banner">
            <TimerReset aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
                <span className="font-medium text-foreground">Temporary conversation.</span> {line}
            </span>
        </p>
    );
}

export default TemporaryBanner;
