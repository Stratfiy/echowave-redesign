/**
 * The same bar above every tab of an agent that is not the canvas.
 *
 * There were three of them and they agreed about nothing. Tools drew a
 * hardcoded `#1a1a1a` bar — copied from the editor, where dark is the canvas's
 * own chrome — so on a light theme it was a black stripe sitting directly on
 * top of a light tab strip. Logs had no header at all. Settings had a
 * theme-aware one with a "Workflow Settings" eyebrow above the name, which
 * stopped being true the moment the tabs below it covered Tools and Logs too.
 *
 * So: one bar, the agent's name, and the way back. The canvas keeps its own —
 * it carries save, publish, test and version history, which none of these
 * have, and its darkness belongs to the node editor rather than to the agent.
 */

"use client";

import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";

export function AgentHeader({
    workflowId,
    name,
    onBack,
}: {
    workflowId: number;
    name: string;
    /**
     * Interposed where leaving could discard an edit — the settings page
     * routes this through its unsaved-changes guard. Plain navigation
     * otherwise.
     */
    onBack?: (go: () => void) => void;
}) {
    const router = useRouter();
    const go = () => router.push(`/workflow/${workflowId}`);

    return (
        <header className="sticky top-0 z-10 flex items-center gap-3 border-b bg-background/95 px-6 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <Button
                variant="ghost"
                size="icon"
                aria-label="Back to agent"
                onClick={() => (onBack ? onBack(go) : go())}
            >
                <ArrowLeft className="h-4 w-4" />
            </Button>
            <h1 className="min-w-0 truncate text-sm font-semibold">{name}</h1>
        </header>
    );
}
