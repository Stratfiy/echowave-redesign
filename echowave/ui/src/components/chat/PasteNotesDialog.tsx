"use client";

/**
 * Paste notes (screen 03): a block of text from anywhere -- meeting notes,
 * a forwarded message -- kept as one block that goes with the next
 * message, not poured into the composer where it would bury what the
 * person is asking. Escape closes without losing the box behind it, and
 * focus returns to the composer (the dialog primitive does both).
 */

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";

export function PasteNotesDialog({
    open,
    onOpenChange,
    onAdd,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onAdd: (notes: string) => void;
}) {
    const [text, setText] = useState("");
    const fieldId = useId();
    const add = () => {
        if (!text.trim()) return;
        onAdd(text.trim());
        setText("");
        onOpenChange(false);
    };
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="motion-m4-enter max-w-[560px]">
                <DialogHeader>
                    <DialogTitle>Paste notes</DialogTitle>
                    <DialogDescription>They go with your next message as one block. You can still type your question.</DialogDescription>
                </DialogHeader>
                <label htmlFor={fieldId} className="sr-only">
                    Notes
                </label>
                <textarea
                    id={fieldId}
                    autoFocus
                    rows={10}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    className="max-h-[50dvh] w-full rounded-[var(--radius-control)] border border-input bg-background px-3 py-2 text-base leading-[1.6] md:text-sm"
                />
                <DialogFooter>
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={!text.trim()} onClick={add}>
                        Add notes
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default PasteNotesDialog;
