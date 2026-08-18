"use client";

/**
 * One way to ask "are you sure?".
 *
 * Three screens used the browser's own `window.confirm` and the rest used
 * `AlertDialog`, which meant deleting a recording looked like a different
 * product from deleting a telephony configuration. That is not only a styling
 * complaint: the native dialog cannot say which item it is about in the title,
 * cannot mark the destructive action as destructive, is unstyled in dark mode,
 * and is silently suppressed by some browsers when it fires outside a user
 * gesture — so the action either happens with no warning at all or does not
 * happen and nobody is told why.
 *
 * The hook keeps the call sites honest. `await confirm({...})` reads exactly
 * like the `if (!confirm(...)) return;` it replaces, so adopting it does not
 * turn one guard clause into thirty lines of JSX per screen.
 */

import { useCallback, useRef, useState } from "react";

import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";

export type ConfirmRequest = {
    title: string;
    /** What actually happens, in the same words the user would use. Say whether
     * it can be undone; that is the only thing they are weighing. */
    description?: string;
    /** The verb, not "OK". A button labelled with the action is the difference
     * between reading the dialog and dismissing it. */
    confirmLabel?: string;
    cancelLabel?: string;
    /** Paints the confirm button red. For anything that destroys data or costs
     * money — not for ordinary "save?" prompts, or the red stops meaning
     * anything. */
    destructive?: boolean;
};

export function useConfirm() {
    const [request, setRequest] = useState<ConfirmRequest | null>(null);
    const resolver = useRef<((ok: boolean) => void) | null>(null);

    const confirm = useCallback((next: ConfirmRequest) => {
        setRequest(next);
        return new Promise<boolean>((resolve) => {
            resolver.current = resolve;
        });
    }, []);

    const settle = useCallback((ok: boolean) => {
        setRequest(null);
        // Resolving rather than leaving the promise hanging matters: a caller
        // that awaits this sits in the middle of a handler, and a dismissed
        // dialog that never resolves leaves that handler alive forever.
        resolver.current?.(ok);
        resolver.current = null;
    }, []);

    const dialog = (
        <AlertDialog
            open={request !== null}
            onOpenChange={(open) => {
                if (!open) settle(false);
            }}
        >
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>{request?.title}</AlertDialogTitle>
                    {request?.description && (
                        <AlertDialogDescription>
                            {request.description}
                        </AlertDialogDescription>
                    )}
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel onClick={() => settle(false)}>
                        {request?.cancelLabel ?? "Cancel"}
                    </AlertDialogCancel>
                    <AlertDialogAction
                        onClick={() => settle(true)}
                        className={cn(
                            request?.destructive &&
                                "bg-destructive text-white hover:bg-destructive/90",
                        )}
                    >
                        {request?.confirmLabel ?? "Continue"}
                    </AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );

    return { confirm, dialog };
}
