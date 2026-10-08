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
 * So: one bar, the agent's name, and the way back. The canvas keeps its own,
 * because it carries save, publish, test and version history and none of these
 * do.
 *
 * That bar used to be dark, on the reasoning that darkness belonged to the
 * node editor. It does not any more: the rail is the app's chrome now, and a
 * second dark surface beside it made the agent screen read as two frames with
 * the content squeezed between them. Chrome is the rail; everything inside it
 * is content and takes the content surface, including the editor's own header.
 */

"use client";

import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

export function AgentHeader({
    workflowId,
    name,
    onBack,
    actions,
    backHref,
    face,
    status,
}: {
    workflowId: number;
    name: string;
    /** The agent's blob beside its name (the approved design's agent page). */
    face?: ReactNode;
    /** A few words on what it is doing, after the name. */
    status?: string | null;
    /**
     * Buttons for the right of the bar -- the chat's About, Test and Share.
     * They used to share a row with the tab strip, and on a 1280px screen
     * the two did not fit: the last tabs sat clipped behind the buttons with
     * no scrollbar to say so. The bar has the room; the strip needs the row.
     */
    actions?: ReactNode;
    /**
     * Interposed where leaving could discard an edit — the settings page
     * routes this through its unsaved-changes guard. Plain navigation
     * otherwise.
     */
    onBack?: (go: () => void) => void;
    /** Where the arrow goes; the agent's editor unless the page says otherwise. */
    backHref?: string;
}) {
    const router = useRouter();
    const go = () => router.push(backHref ?? `/workflow/${workflowId}`);

    return (
        // The design's 56px bar: the way back, the face, the name, a quiet status.
        <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2.5 border-b border-[var(--line,var(--border))] bg-background px-3 sm:px-5">
            <Button
                variant="ghost"
                size="icon"
                aria-label={backHref ? "Back" : "Back to agent"}
                onClick={() => (onBack ? onBack(go) : go())}
            >
                <ArrowLeft className="h-4 w-4" />
            </Button>
            {face}
            <h1 className="min-w-0 truncate text-base font-semibold">{name}</h1>
            {status && <span className="hidden shrink-0 text-[13px] text-[var(--ink-2,#5d5d5d)] sm:inline">{status}</span>}
            {actions && <div className="ml-auto flex shrink-0 gap-2">{actions}</div>}
        </header>
    );
}
