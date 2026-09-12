"use client";

/**
 * Mounts its children the first time they come near the viewport.
 *
 * The home screen opens on a greeting, a composer and a short list of agents.
 * The charts underneath are the expensive half — four analytics endpoints and
 * a charting library — and nobody should pay for them before deciding to
 * scroll. Once mounted it stays mounted: unmounting on scroll-away would
 * refetch on the way back, which is worse than the render it saves.
 *
 * Falls back to mounting immediately where IntersectionObserver is missing.
 * A browser without it would otherwise see a permanently blank lower half,
 * which is the silent-absence failure this codebase keeps having.
 */

import { useEffect, useRef, useState } from "react";

export function LazySection({
    children,
    /** How far ahead of the viewport to start. Enough that the charts have
     *  usually arrived by the time the scroll reaches them. */
    rootMargin = "400px",
    placeholderHeight = 320,
}: {
    children: React.ReactNode;
    rootMargin?: string;
    placeholderHeight?: number;
}) {
    const ref = useRef<HTMLDivElement>(null);
    const [shown, setShown] = useState(false);

    useEffect(() => {
        if (shown) return;
        if (typeof IntersectionObserver === "undefined") {
            setShown(true);
            return;
        }
        const element = ref.current;
        if (!element) return;

        const observer = new IntersectionObserver(
            (entries) => {
                if (entries.some((entry) => entry.isIntersecting)) {
                    setShown(true);
                    observer.disconnect();
                }
            },
            { rootMargin },
        );
        observer.observe(element);
        return () => observer.disconnect();
    }, [shown, rootMargin]);

    return (
        <div ref={ref}>
            {shown ? children : <div aria-hidden style={{ height: placeholderHeight }} />}
        </div>
    );
}
