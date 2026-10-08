"use client";

import { useEffect, useState } from "react";

/** How much of the layout viewport the visual viewport must lose before it
 *  counts as a keyboard rather than a browser toolbar sliding in. */
const KEYBOARD_MIN_PX = 150;

function editable(element: Element | null): boolean {
    if (!element) return false;
    const tag = element.tagName;
    if (tag === "TEXTAREA") return true;
    if (tag === "INPUT") {
        const type = (element as HTMLInputElement).type;
        return !["button", "checkbox", "radio", "range", "submit", "reset", "file", "color"].includes(type);
    }
    return (element as HTMLElement).isContentEditable === true;
}

/**
 * Whether a phone's software keyboard is open.
 *
 * The visual viewport shrinks when the keyboard opens and the layout
 * viewport does not; that difference, while a text field has focus, is the
 * signal. Focus alone is not enough (a hardware keyboard on a tablet opens
 * nothing) and the resize alone is not either (a toolbar collapsing on
 * scroll changes it too). Only on a coarse pointer: a laptop never has one.
 */
export function useSoftKeyboardOpen(): boolean {
    const [open, setOpen] = useState(false);
    useEffect(() => {
        if (typeof window === "undefined") return;
        const coarse = window.matchMedia?.("(pointer: coarse)").matches ?? false;
        if (!coarse) return;
        const viewport = window.visualViewport;
        const check = () => {
            const focused = editable(document.activeElement);
            const lost = viewport ? window.innerHeight - viewport.height : 0;
            // Without a visual viewport (old browsers), focus on a text
            // field on a touch device is the best signal there is.
            setOpen(focused && (viewport ? lost > KEYBOARD_MIN_PX : true));
        };
        // On focusout the next element has not taken focus yet; look once
        // the event has settled.
        const later = () => setTimeout(check, 0);
        viewport?.addEventListener("resize", check);
        document.addEventListener("focusin", check);
        document.addEventListener("focusout", later);
        check();
        return () => {
            viewport?.removeEventListener("resize", check);
            document.removeEventListener("focusin", check);
            document.removeEventListener("focusout", later);
        };
    }, []);
    return open;
}
