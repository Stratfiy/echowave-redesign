"use client";

/**
 * The composer's Attach menu (screen 03): files, a voice note, meeting mode
 * and pasted notes. Meeting mode belongs to the meetings stream; until it
 * registers its entry point the item says it is not available yet rather
 * than opening nothing.
 */

import { FileUp, Mic, NotebookPen, Plus, Presentation } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export function AttachMenu({
    onFiles,
    onVoiceNote,
    onPasteNotes,
    onMeetingMode,
    meetingAvailable,
    filesAvailable = true,
    voiceBusy = false,
    disabled = false,
    className,
}: {
    onFiles: () => void;
    onVoiceNote: () => void;
    onPasteNotes: () => void;
    onMeetingMode: () => void;
    meetingAvailable: boolean;
    filesAvailable?: boolean;
    voiceBusy?: boolean;
    disabled?: boolean;
    className?: string;
}) {
    const item = "flex min-h-11 items-start gap-2 md:min-h-9";
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Attach"
                    title="Attach"
                    disabled={disabled}
                    className={cn("motion-m1 size-11 shrink-0 text-muted-foreground md:size-8", className)}
                >
                    <Plus className="h-4 w-4" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="top" className="w-64">
                <DropdownMenuItem className={item} disabled={!filesAvailable} onSelect={onFiles}>
                    <FileUp aria-hidden className="mt-0.5 h-4 w-4" />
                    <span className="flex flex-col">
                        <span>Files</span>
                        <span className="text-xs text-muted-foreground">PDF, Word, text, CSV</span>
                    </span>
                </DropdownMenuItem>
                <DropdownMenuItem className={item} disabled={voiceBusy} onSelect={onVoiceNote}>
                    <Mic aria-hidden className="mt-0.5 h-4 w-4" />
                    <span className="flex flex-col">
                        <span>Voice note</span>
                        <span className="text-xs text-muted-foreground">Record; it goes with your message</span>
                    </span>
                </DropdownMenuItem>
                <DropdownMenuItem className={item} disabled={!meetingAvailable} onSelect={onMeetingMode}>
                    <Presentation aria-hidden className="mt-0.5 h-4 w-4" />
                    <span className="flex flex-col">
                        <span>Meeting mode</span>
                        <span className="text-xs text-muted-foreground">
                            {meetingAvailable ? "Capture a meeting with consent" : "Not available yet"}
                        </span>
                    </span>
                </DropdownMenuItem>
                <DropdownMenuItem className={item} onSelect={onPasteNotes}>
                    <NotebookPen aria-hidden className="mt-0.5 h-4 w-4" />
                    <span className="flex flex-col">
                        <span>Paste notes</span>
                        <span className="text-xs text-muted-foreground">Text from anywhere, kept as one block</span>
                    </span>
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

export default AttachMenu;
