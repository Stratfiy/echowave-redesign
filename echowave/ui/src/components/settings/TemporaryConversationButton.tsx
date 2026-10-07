"use client";

/**
 * Start a temporary conversation (screens 16, 18): a new chat that saves
 * nothing to memory and is deleted after a while. The retention is said
 * before it starts, plainly, and it does not promise that nothing is
 * processed -- Decibyl still reads what you write to answer it.
 */

import { Loader2, TimerReset } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { startTemporaryConversationApiV1MeTemporaryConversationsPost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";

export function TemporaryConversationButton({ retention }: { retention?: string }) {
    const router = useRouter();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    return (
        <div className="flex flex-col gap-2" data-testid="temporary-conversation">
            <p className="text-sm text-muted-foreground">
                {retention
                    ? `A temporary conversation: ${retention}`
                    : "A temporary conversation saves nothing to memory, and its messages are deleted after a set time. Decibyl still reads what you write to answer you, and anything you approve in it still happens."}
            </p>
            <div>
                <Button
                    type="button"
                    variant="outline"
                    className="motion-m1 min-h-11 md:min-h-9"
                    disabled={busy}
                    onClick={async () => {
                        setBusy(true);
                        setError(null);
                        try {
                            const result = await startTemporaryConversationApiV1MeTemporaryConversationsPost();
                            if (result.error || !result.data?.href) {
                                setError(detailFromError(result.error, "Could not start one. Try again."));
                                return;
                            }
                            router.push(result.data.href);
                        } catch {
                            setError("Could not reach Decibyl. Try again.");
                        } finally {
                            setBusy(false);
                        }
                    }}
                >
                    {busy ? <Loader2 aria-hidden className="motion-continuous animate-spin" /> : <TimerReset aria-hidden />}
                    Start a temporary conversation
                </Button>
            </div>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

export default TemporaryConversationButton;
