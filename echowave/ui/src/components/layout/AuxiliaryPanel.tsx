"use client";

/**
 * The panel on the right: what you consult while the thread stays put.
 *
 * Buzz calls this the auxiliary panel and gives it one set of rules, which
 * is the point. We had the idea twice — About beside a bot's thread, and the
 * tester beside its canvas — written out by hand in two places with different
 * widths, different breakpoints, and neither of them resizable. Two
 * definitions of one thing is how they drift.
 *
 * The rules, Buzz's:
 *
 *  - It opens at 380px, and never narrows past 300px or leaves the pane
 *    beside it less than that.
 *  - It is dragged by its left edge, and a double-click puts it back.
 *  - The width is remembered per person, because somebody who widens it once
 *    meant it.
 *  - Below twice the minimum there is no room for two panes, so it takes the
 *    screen instead of sitting in a strip beside a chat that has none. That
 *    is the phone case, and it is a layout rule rather than a device test.
 *
 * The panel owns its header, which is the other half of the consolidation:
 * About and the tester each drew their own title-and-close row, so a reader
 * moving between them met the same bar built twice. The shape is the one
 * ManyChat's AI playground uses and Refero catalogues -- the control that
 * puts the panel away sits beside the title rather than floating over the
 * content, and the tenant's own action sits opposite it.
 */

import { ChevronsRight } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const AUX_DEFAULT_WIDTH_PX = 380;
export const AUX_MIN_WIDTH_PX = 300;
/** Under this there is no room for two panes, so the panel becomes the page. */
export const AUX_SINGLE_PANE_BELOW_PX = AUX_MIN_WIDTH_PX * 2;

const WIDTH_STORAGE_KEY = "decibyl.auxiliaryPanel.width";

/** The widest it may be and still leave the main pane its minimum. */
export function auxMaxWidth(viewportWidth: number): number {
    return Math.max(AUX_DEFAULT_WIDTH_PX, viewportWidth - AUX_MIN_WIDTH_PX);
}

export function clampAuxWidth(width: number, viewportWidth: number): number {
    return Math.max(AUX_MIN_WIDTH_PX, Math.min(auxMaxWidth(viewportWidth), width));
}

function readStoredWidth(): number {
    try {
        const raw = window.localStorage.getItem(WIDTH_STORAGE_KEY);
        const parsed = raw ? Number.parseInt(raw, 10) : Number.NaN;
        return Number.isFinite(parsed) ? parsed : AUX_DEFAULT_WIDTH_PX;
    } catch {
        // Private windows and blocked site data land here. A width is a
        // preference, not state worth failing over.
        return AUX_DEFAULT_WIDTH_PX;
    }
}

export function AuxiliaryPanel({
    label,
    onClose,
    action,
    children,
    className,
}: {
    /** Names the region for a screen reader, and heads the panel. */
    label: string;
    onClose?: () => void;
    /** The tenant's own control, opposite the collapse chevron. */
    action?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}) {
    const [width, setWidth] = React.useState(AUX_DEFAULT_WIDTH_PX);
    const [viewport, setViewport] = React.useState<number | null>(null);
    const dragging = React.useRef<{ startX: number; startWidth: number } | null>(null);

    React.useEffect(() => {
        setWidth(readStoredWidth());
        const onResize = () => setViewport(window.innerWidth);
        onResize();
        window.addEventListener("resize", onResize);
        return () => window.removeEventListener("resize", onResize);
    }, []);

    // Null until the first measurement, so the server and the first client
    // paint agree: a panel that guesses wide and corrects is a flash.
    const singlePane = viewport !== null && viewport < AUX_SINGLE_PANE_BELOW_PX;
    const resolved = viewport === null ? AUX_DEFAULT_WIDTH_PX : clampAuxWidth(width, viewport);

    const store = (next: number) => {
        setWidth(next);
        try {
            window.localStorage.setItem(WIDTH_STORAGE_KEY, String(next));
        } catch {
            /* see readStoredWidth */
        }
    };

    React.useEffect(() => {
        if (!dragging.current) return;
        const move = (event: PointerEvent) => {
            const start = dragging.current;
            if (!start) return;
            // Dragged by the left edge, so moving left widens it.
            store(clampAuxWidth(start.startWidth + (start.startX - event.clientX), window.innerWidth));
        };
        const stop = () => {
            dragging.current = null;
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", stop);
        return () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", stop);
        };
    });

    return (
        <aside
            aria-label={label}
            data-testid="auxiliary-panel"
            className={cn(
                "relative flex flex-col bg-background",
                singlePane
                    ? "fixed inset-0 z-40"
                    : "h-full shrink-0 border-l border-border",
                className,
            )}
            style={singlePane ? undefined : { width: resolved }}
        >
            {!singlePane && (
                <button
                    type="button"
                    aria-label={`Resize ${label}`}
                    title="Drag to resize. Double-click to reset."
                    data-testid="auxiliary-panel-resize"
                    className="group absolute inset-y-0 -left-1.5 z-50 w-3 cursor-col-resize"
                    onDoubleClick={() => store(AUX_DEFAULT_WIDTH_PX)}
                    onPointerDown={(event) => {
                        dragging.current = { startX: event.clientX, startWidth: resolved };
                    }}
                >
                    <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-border" />
                </button>
            )}
            <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2.5">
                {onClose && (
                    <Button
                        variant="outline"
                        size="icon"
                        className="h-7 w-7 shrink-0"
                        aria-label={`Close ${label}`}
                        onClick={onClose}
                    >
                        <ChevronsRight className="h-4 w-4" />
                    </Button>
                )}
                <p className="min-w-0 flex-1 truncate text-sm font-semibold">{label}</p>
                {action}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </aside>
    );
}
