"use client";

/**
 * The detail drawer for Today (screen 07): 440 px beside the list on a
 * desktop, full screen with Back on a phone. Not a modal -- the list stays in
 * reading order beside it -- so focus is not trapped; Escape closes it and
 * focus returns to the row that opened it. The list keeps its scroll because
 * it never unmounts.
 */

import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";

export function TodayDrawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
    const ref = useRef<HTMLElement>(null);
    const opener = useRef<Element | null>(null);

    useEffect(() => {
        opener.current = document.activeElement;
        ref.current?.focus();
        function onKey(event: KeyboardEvent) {
            if (event.key === "Escape") onClose();
        }
        document.addEventListener("keydown", onKey);
        const back = opener.current;
        return () => {
            document.removeEventListener("keydown", onKey);
            if (back instanceof HTMLElement) back.focus();
        };
    }, [onClose]);

    return (
        <aside
            ref={ref}
            tabIndex={-1}
            aria-label={title}
            data-testid="today-drawer"
            className="motion-m3-enter fixed inset-0 z-40 overflow-y-auto bg-background px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 outline-none md:inset-y-0 md:left-auto md:right-0 md:w-[440px] md:border-l md:border-border md:px-6 md:shadow-lg"
        >
            <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-sm text-muted-foreground">{title}</p>
                <Button variant="ghost" className="motion-m1 min-h-11 min-w-11 md:min-h-9 md:min-w-9" onClick={onClose} aria-label="Close and go back to Today">
                    <X aria-hidden className="h-4 w-4" />
                </Button>
            </div>
            {children}
        </aside>
    );
}

export default TodayDrawer;
